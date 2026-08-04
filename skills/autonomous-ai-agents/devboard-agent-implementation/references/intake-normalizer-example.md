# Worked Example: Intake Normalizer (2026-07-08)

Concrete implementation of the `devboard-agent-implementation` pattern.
This session added a natural-language intake normalize endpoint to the
DevBoard dashboard that classifies raw free-text as bug/task/feature/question.

## Files Created

### Agent: `IntakeNormalizerAgent`

Path: `backend/app/Assistants/Agents/IntakeNormalizerAgent.php`

- Implements `Agent, HasStructuredOutput` (no `HasTools`).
- Schema: `task_type` (enum bug|task|feature|question), `suggested_title`,
  `suggested_description`, `clarifying_questions`, `confidence`.
- Instructions constrain the agent to classify, extract, and ask only
  critical missing-fact questions.

### Service: `IntakeNormalizerService`

Path: `backend/app/Services/Hades/IntakeNormalizerService.php`

- Constructor injects `HadesKanbanTaskIntakeService` for deterministic
  fallback.
- `normalize(string $rawText): array`:
  - Queries `ai_agent_profiles` for `intake_normalizer`.
  - Checks `IntakeNormalizerAgent::isFaked()` or provider readiness.
  - Falls back to `HadesKanbanTaskIntakeService::normalizeFreeText()` if
    no provider.
  - On LLM success, normalizes the structured result with field-by-field
    validation against the fallback.
  - Computes `requires_root_cause` independently from raw text keywords
    (avoids double-calling the fallback).
- Returns: `task_type`, `suggested_title`, `suggested_description`,
  `clarifying_questions`, `requires_root_cause`, `confidence`,
  `execution_mode`.

### Controller: `IntakeNormalizerController`

Path: `backend/app/Http/Controllers/Dashboard/Api/IntakeNormalizerController.php`

- `__invoke(Request, IntakeNormalizerService, string $project): JsonResponse`
- Role guard: Admin, PM, Developer.
- Validates `raw_text`: required, string, min:5, max:5000.
- Returns `{ project_id, normalization: {...} }`. No persistence.

### Tests: `IntakeNormalizerTest`

Path: `backend/tests/Feature/Dashboard/IntakeNormalizerTest.php`

10 tests:
1. Bug-like text with error/version → `task_type: bug`, `execution_mode: deterministic_fallback`
2. Feature-like text with 'Add' → `task_type: feature`
3. Vague 'Fix it' → low confidence, clarifying questions present
4. 'diagnose' + 'root cause' keywords → `requires_root_cause: true`
5. Question mark in text → `task_type: question`
6. Min length validation (2 chars → 422)
7. Max length validation (5001 chars → 422)
8. Sysadmin role → 403, PM role → 200
9. Unauthenticated → 401
10. SDK fake with `IntakeNormalizerAgent::fake([[...]])` → structured output + `execution_mode: laravel_ai_sdk_fake`

## Hardening Slice (2026-07-08, second pass)

### HadesEvidencePolicy injection

**Service change**: `IntakeNormalizerService` now injects `HadesEvidencePolicy`
and redacts raw text before sending it to the LLM:

```php
// Before LLM prompt — redact secrets
$redacted = $this->evidencePolicy->redactTextMaterial($rawText);
$prompt = $this->promptForRawText($redacted['text'], $projectId);

// Original $rawText stays untouched for:
// - $this->kanbanIntake->normalizeFreeText($rawText, ...)  (fallback)
// - normalizeStructuredResult(..., $rawText, ...)           (field fallback)
// - mb_strtolower($rawText)                                 (keyword checks)
```

### New test: prompt redaction verification

Added test `'redacts secrets from the LLM prompt using HadesEvidencePolicy when agent is faked'`:

- Sends raw text containing an `sk-live-test-...` key, a JWT Bearer token, and a
  colon-separated `secret: xyz-token-...`.
- Fakes `IntakeNormalizerAgent` and asserts via `assertPrompted()`:
  - `$prompt->contains('raw-key')` → `toBeFalse()` (secrets removed)
  - `$prompt->contains('[redacted]')` → `toBeTrue()` (redaction markers present)
- Uses `expect($prompt->contains(...))->toBeTrue()/toBeFalse()` because
  `AgentPrompt` cannot be cast to `(string)`.

### Learnings

- `AgentPrompt` does not implement `__toString()`. Use `$prompt->contains()`
  with `expect()` for content assertions, not `(string)` cast.
- The Service must call `normalizeFreeText()` (deterministic fallback) with
  the **original** unredacted text since it never reaches an external provider.
  Only the LLM path receives redacted input.

## Files Modified

### `HadesKanbanTaskIntakeService`

Added public method `normalizeFreeText(string $rawText): array`:

- Extends the keyword matching from private `normalizeTask()` to work on
  raw text instead of a task DB row.
- Classification chain: bug keywords → analysis keywords → feature
  keywords → question keywords → default 'task'.
- Generates clarifying questions based on text length, missing project
  reference, missing reproduction steps (for bugs), missing version info.
- Returns `execution_mode: 'deterministic_fallback'`.
- Does NOT persist anything — this is the pure deterministic fallback.

### `routes/web.php`

Added at line 96:

```php
Route::post('/projects/{project}/intake/normalize', IntakeNormalizerController::class);
```

Under the `auth` middleware, inside `prefix('/api/dashboard')`.

### `DevBoardSeeder.php`

Added `intake_normalizer` agent profile entry:

```php
[
    'agent_key' => 'intake_normalizer',
    'display_name' => 'Intake Normalizer',
    'agent_type' => 'specialist',
    'parent_agent_key' => 'socrate_supervisor',
    'allowed_tools' => [],
    'trigger_events' => ['manual_intake'],
    'output_schema' => [ ... ],
]
```

## Bug Fixed During Implementation

**Issue**: The LLM path in `IntakeNormalizerService::normalize()` returned
all normalization fields except `requires_root_cause`, but the fallback
path (`normalizeFreeText()`) included it. This meant the JSON response
shape changed depending on whether the provider was available.

**Fix**: Compute `requires_root_cause` independently in the `normalize()`
method from `rawText` keywords (`str_contains($haystack, 'root cause')`
etc.) rather than from the `normalizeStructuredResult()` output. This
avoids calling the deterministic fallback twice — once inside
`normalizeStructuredResult()` for field normalization and once more for
a single field.

## Design Decisions

- **No persistence in normalize endpoint**: The intake normalize endpoint
  is a pure preview. It does NOT create tasks, bug reports, or work items.
  The user said "non duplicare il flusso di creazione task/bug." The
  existing `HadesKanbanTaskIntakeService::queueLocalAgentWorkForTask()` is
  the creation path and was left untouched.
- **No new agent tools**: The user said "non aggiungere nuovi tool agent."
  The `IntakeNormalizerAgent` has no tools — it works purely from the
  prompt text.
- **No Artisan commands**: User explicitly excluded them from scope.
- **Developer role allowed**: Unlike Task Clarifier (PM/Admin only), intake
  normalization is available to Developers too, since they often write raw
  bug reports.
