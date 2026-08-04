---
name: devboard-agent-implementation
description: "Implement a new LLM-backed server-side agent or AI feature in the DevBoard/Hades Laravel backend. Covers Agent, Service, Controller, Route, Seeder, and Test layers with deterministic fallback."
version: 1.0.0
author: Hades Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [devboard, hades, laravel, agent, implementation, llm, intake]
    related_skills: [hades-coordination, hades-bug-diagnosis, requesting-code-review]
---

# DevBoard Agent Implementation

Use this skill when implementing a new LLM-backed server-side agent or AI
feature in the DevBoard/Hades Laravel backend. The pattern is the same whether
you are adding an intake normalizer, a task clarifier, a backlog triager, or a
wiki query agent.

## Architecture Pattern (6 layers)

Every new agent feature touches these layers in order:

### 1. Agent (LLM contract)

`backend/app/Assistants/Agents/<Name>Agent.php`

- Implements `Laravel\Ai\Contracts\Agent` + `HasStructuredOutput` (add `HasTools`
  only when the agent needs read-only DB tools).
- Uses `Promptable` trait.
- `instructions()`: return a `<<<'INSTRUCTIONS'` heredoc describing the
  agent's role, constraints, and output format.
- `schema(JsonSchema $schema): array`: structured output schema with typed
  fields (`->string()`, `->array()`, `->number()`, `->enum([...])`).
- Keep the agent stateless — all context comes from the prompt, not from
  injected services.

### 2. Service (LLM orchestration + deterministic fallback)

`backend/app/Services/Hades/<Name>Service.php`

- Constructor injects the deterministic fallback dependency (e.g.
  `HadesKanbanTaskIntakeService`).
- `normalize()` / main method:
  1. Look up the agent profile from `ai_agent_profiles` by `agent_key`.
  2. Look up the model profile + provider (same join pattern as
     `TaskClarifierService::modelProfileForAgent`).
  3. If `IntakeNormalizerAgent::isFaked()` or provider is enabled with a key:
     a. **Redact prompt content first** — pass raw text through
        `$this->evidencePolicy->redactTextMaterial($rawText)` before building
        the LLM prompt. Use the redacted `['text']` in the prompt; keep the
        original `$rawText` for deterministic fallback, field normalization,
        and keyword checks. The `HadesEvidencePolicy` replaces Bearer tokens,
        `api_key`/`secret`/`password` values, `sk-live`/`sk-test` key patterns,
        and PEM private keys with `[redacted]` markers.
     b. Configure the Laravel AI provider (`configureLaravelAiProvider`), call
        `Agent::make()->prompt(...)` with the redacted prompt, and normalize
        the structured response.
  4. If the provider is missing/disabled or the call throws, fall back to the
     deterministic path immediately (using original, unredacted raw text —
     the fallback never sends data to an external LLM).
- Copy `modelProfileForAgent()`, `modelProfileCanCallProvider()`, and
  `configureLaravelAiProvider()` verbatim from `TaskClarifierService` — they
  are identical across all agents.
- `normalizeStructuredResult()`: validate LLM output field-by-field with
  fallback to deterministic values. Sanitize types, clamp lengths, and
  round floats.

### 3. Controller (thin HTTP layer)

`backend/app/Http/Controllers/Dashboard/Api/<Name>Controller.php`

- Uses `ChecksDashboardRoles` trait.
- `__invoke(Request $request, Service $service, string $project): JsonResponse`
- Role guard: Admin || PM || (Developer when applicable).
- Validate input (`$request->validate([...])`).
- Call service, return JSON with `project_id` and the result under a
  descriptive key (e.g. `normalization`, `suggestion`).
- Do NOT persist anything in the controller. The endpoint is preview-only
  unless the feature explicitly requires persistence (task clarify does).

### 4. Route

`backend/routes/web.php`

- Add import at top.
- Add route under the `auth` middleware group, inside the
  `prefix('/api/dashboard')` block.
- Pattern: `Route::post('/projects/{project}/<feature>/<action>', Controller::class);`

### 5. Seeder

`backend/database/seeders/DevBoardSeeder.php`

- Add an entry to `defaultAgentProfiles()`:
  - `agent_key`: snake_case identifier matching what the Service queries.
  - `agent_type`: `specialist` (or `supervisor` for routing agents).
  - `parent_agent_key`: `socrate_supervisor` for specialists.
  - `allowed_tools`: `[]` when no tools are needed; otherwise a list of
    tool keys from `AiAgentToolRegistry`.
  - `output_schema`: JSON Schema object matching the Agent's structured
    output.

### 6. Tests

`backend/tests/Feature/Dashboard/<Name>Test.php`

- Use `RefreshDatabase` + `$this->withoutVite()` + seed `DevBoardSeeder`.
- Test cases (minimum set):
  - Happy path: correct classification/normalization with deterministic
    fallback.
  - Vague input: clarifying questions returned, low confidence.
  - Edge case keywords: verify specific keyword triggers (e.g.
    `'root cause'` → bug, `'?'` → question).
  - Input validation: min/max length, required fields.
  - Role guard: forbidden for Sysadmin, ok for PM/Admin.
  - Unauthenticated: 401.
  - SDK fake: `Agent::fake([[...]])->preventStrayPrompts()` then assert
    structured output and `Agent::assertPrompted(...)`.
  - Fallback when no provider: don't configure provider, assert
    `execution_mode === 'deterministic_fallback'`.
- Helper functions at file bottom: `*UserWithRole()`, `*ConfigureProvider()`,
  `*CreateTask()` — same pattern as `TaskClarifierDashboardTest`.

## Scope Execution

When the user asks you to "analyze" or "design" before implementing:

1. Deliver the analysis with concrete, implementable slice boundaries
   (e.g. "First slice: endpoint + service + fallback. Next slice: hardening +
   redaction.").
2. Ask which slice to start — do not implement everything from the analysis
   unless told to.
3. Each slice must be independently verifiable (testable) and narrow enough
   to complete in one session.
4. The user's slice constraints are hard boundaries:
   - "non duplicare il flusso di creazione task/bug" → preview-only endpoints
   - "non aggiungere comandi Artisan" → route-only, no CLI entrypoints
   - "non aggiungere nuovi tool agent" → Agent without `HasTools`
5. **Tests must pass before you say "done"**. The user's last instruction on
   a slice is the final verification gate.

## Deterministic Fallback Pattern

Every agent MUST have a keyword-based or rule-based fallback. The
`IntakeNormalizerAgent` uses keyword detection in
`HadesKanbanTaskIntakeService::normalizeFreeText()`. The
`TaskClarifierAgent` uses `structuredSuggestion()`.

Rules for the fallback:
- Same return shape as the LLM path (no missing keys).
- `execution_mode` must be `'deterministic_fallback'`.
- Confidence should be lower than the LLM would produce (0.40–0.70 range).
- Clarifying questions should be practical and grounded in what the text
  is missing, not generic.

## Pitfalls

- **Inconsistent return shape**: The LLM path and fallback path must return
  identical keys. If the fallback returns `requires_root_cause` but the LLM
  path doesn't, the JSON response changes shape depending on provider state.
  Compute missing fields independently after the LLM call.
- **Double fallback calls**: Don't call the deterministic fallback inside
  `normalizeStructuredResult()` AND again after for a single field. Extract
  the field directly from the raw text or compute it from the LLM result.
- **Seeded agent not found**: The Service queries `ai_agent_profiles` by
  `agent_key`. If the seeder entry is missing, the Service must fall back
  gracefully — not crash.
- **Prompt security bypass**: Raw user text sent to an LLM without redaction
  can leak tokens, API keys, and credentials. Always call
  `HadesEvidencePolicy::redactTextMaterial($rawText)` before building the
  prompt. Only the redacted text goes to the LLM; the original stays for
  deterministic fallback and local keyword checks.
- **AgentPrompt cast fails**: `$prompt` in `assertPrompted(fn ($prompt) => ...)`
  is `Laravel\Ai\Prompts\AgentPrompt` and cannot be cast to `(string)`. Use
  `$prompt->contains(string)` for positive/negative assertions:
  `expect($prompt->contains('[redacted]'))->toBeTrue()` and
  `expect($prompt->contains('raw-secret'))->toBeFalse()`.
- **Wrong model in test assertion**: When using `Agent::fake()`, the
  `assertPrompted($prompt->model)` value may differ from the seeded model.
  Check the existing tests for the correct value (usually `'gpt-5.4'` for
  the openai driver default).

## Running Tests

### PHP syntax check (when host lacks PHP)

Use a lightweight PHP CLI container:

```bash
docker run --rm -v "$PWD/backend:/app" php:8.2-cli \
  php -l /app/app/Services/Hades/<Name>Service.php
```

Check all touched files in parallel before running the full test suite.

### Feature tests

```bash
docker exec devboard-app-1 sh -lc \
  'APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= \
   php artisan test tests/Feature/Dashboard/<Name>Test.php --display-warnings'
```

### Verification gate

**Do NOT claim completion until tests pass.** The user explicitly requires
a passing test suite as the verification signal. A failing test means the
implementation is not done, regardless of how correct the code appears.
Run the focused test file first, then check the full suite doesn't regress:

```bash
docker exec devboard-app-1 sh -lc \
  'APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= \
   php artisan test --display-warnings'
```

## Related Skills

- `hades-coordination`: CLI-based natural-language bug intake and
  coordination with the shared backend.
- `hades-bug-diagnosis`: Evidence-based diagnosis workflow that can consume
  intake-normalized bug reports.
- `requesting-code-review`: Pre-commit security scan and quality gates to
  run before merging agent implementation branches.
