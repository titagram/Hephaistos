# Autopoiesis Project C — Workshop, Research Gate, and Blueprint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user start or accept an evolution, explicitly authorize bounded research, collaboratively define “what Hades will become,” persist a digest-stable Blueprint, and request a separately bound build approval.

**Architecture:** `/autopoiesis` is a normal user turn built from a byte-stable prompt contract. Deterministic CLI commands own draft/revision persistence and approval requests. Approval issuance is performed by the host's existing user-consent surface, not by model prose. A session-bound research gate permits only `web_search` and `web_extract` within a valid research lease. The Workshop can prepare a build request but has no builder or pointer authority.

**Tech Stack:** Project A grants/ledger, Project B suggestions, existing slash-command registry, existing CLI/gateway/TUI approval callbacks, existing web tools, canonical JSON, pytest.

## Global Constraints

- Read design sections “Autopoiesis Workshop”, “Research authorization”,
  “Build authorization”, “Operator and Conversational Interfaces”, and
  “Privacy and Security”.
- Research, build, and promotion remain three separate requests and grants.
- User discussion and model brainstorming are not authority. Only a committed
  grant row can authorize a gated operation.
- Editing any material Blueprint field creates a new revision and invalidates
  an earlier build request/grant.
- A Workshop may read `gnothi_seauton` evidence, suggestions, and bounded
  research records. It cannot patch core, acquire executable artifacts, create
  a candidate environment, or change pointers.
- `/autopoiesis` must not rebuild the system prompt or tool schema.

---

## File and Module Map

| Path | Responsibility |
|---|---|
| `hermes_cli/evolution/approval_surface.py` | Host-owned presentation and issuance of digest-bound grants |
| `hermes_cli/evolution/research_gate.py` | Session-bound web authorization and source recording |
| `hermes_cli/evolution/blueprint.py` | Blueprint schema, canonical revisions, digest, deviation classification |
| `hermes_cli/evolution/workshop.py` | Attempts, session bindings, suggestion acceptance, revision workflow |
| `agent/autopoiesis_prompt.py` | Stable conversational operating contract |
| `hermes_cli/commands.py` | `/autopoiesis` registry entry |
| `hermes_cli/cli_commands_mixin.py`, `cli.py` | Classic CLI dispatch |
| `gateway/run.py` | Messaging dispatch and approval presentation |
| `tui_gateway/server.py` | TUI/desktop slash dispatch |
| `hermes_cli/evolution/command.py` | Deterministic workshop/approval commands |
| `hermes_cli/subcommands/evolution.py` | Operator parser additions |

## Task 1: Host-owned consent and research authority contract

**Model:** `gpt-5.6-sol`, reasoning `high`

**Objective:** Ensure only an explicit host-mediated decision or direct
operator confirmation can issue a research grant, and define the exact lease
checked by web operations.

**Non-goals:** No Blueprint or web call.

**Files:**
- Create: `hermes_cli/evolution/approval_surface.py`
- Create: `hermes_cli/evolution/research_gate.py`
- Modify: `hermes_cli/evolution/ledger.py`
- Modify: `tools/approval.py`
- Create: `tests/hermes_cli/evolution/test_approval_surface.py`
- Create: `tests/hermes_cli/evolution/test_research_authority.py`
- Modify: `tests/tools/test_approval.py`

**Context pack:** Project A authorization API; `request_elicitation_consent`;
terminal dangerous-pattern policy; design authorization rules.

**Interfaces:**

```python
def request_user_grant(request_id: str, *, surface: str,
                       session_id: str | None = None) -> GrantDecision: ...

def confirm_operator_grant(request_id: str, *, confirmation_digest: str,
                           stdin_is_tty: bool) -> GrantDecision: ...

@dataclass(frozen=True)
class ResearchLease:
    grant_id: str
    attempt_id: str
    session_id: str
    permitted_domains: tuple[str, ...]
    source_classes: tuple[str, ...]
    started_at: str
    expires_at: str

def begin_research_lease(ledger: EvolutionLedger, *, grant_id: str,
                         session_id: str) -> ResearchLease: ...
```

- [ ] **Step 1: Write failing consent provenance tests**

Model text, Blueprint text, a request ID alone, silence, timeout, and a
confirmation digest for another request must not issue a grant. A host callback
returning `accept` issues exactly one grant; deny/cancel append denial evidence.

- [ ] **Step 2: Write failing terminal-guard tests**

The existing dangerous-command detector must flag common direct and module
invocations of `evolution approve-research|approve-build|approve-promotion`.
This ensures an agent using the terminal cannot run the noninteractive operator
form without the existing user approval rail.

- [ ] **Step 3: Write failing lease tests**

The first authorized web operation consumes the research grant and starts one
lease bound to session/attempt. Reuse in another session, after expiry, after
denial, or with a broader domain/source class fails closed.

- [ ] **Step 4: Run red tests**

Run: `scripts/run_tests.sh tests/hermes_cli/evolution/test_approval_surface.py tests/hermes_cli/evolution/test_research_authority.py tests/tools/test_approval.py -q`

- [ ] **Step 5: Implement host approval presentation**

Use `request_elicitation_consent` with `allow_permanent=False` semantics and a
message that displays request kind, exact subject digest, closed scope, expiry,
and non-authorized actions. Do not accept free-form parsing as the decision.

- [ ] **Step 6: Implement direct operator confirmation**

TTY mode displays the same payload and requires an exact affirmative response.
Non-TTY mode requires `--confirm-digest` equal to the request digest. Both call
the same atomic `issue_grant`; neither performs the later operation.

- [ ] **Step 7: Implement research lease persistence**

Ledger migration 3 adds `workshop_sessions` and `research_leases`. Bind a
session to at most one active attempt and a grant to at most one lease.

- [ ] **Step 8: Run tests and commit**

Commit: `evolution-c: require host-owned research consent`

**Escalate if:** a child/candidate process can mint a grant, permanent approval
can cover evolution, or a lease can broaden its request scope.

## Task 2: Canonical Blueprint revisions

**Model:** `gpt-5.6-sol`, reasoning `high`

**Objective:** Define a complete, deterministic statement of what Hades will
become and make all build-relevant edits digest-invalidating.

**Non-goals:** No conversational UX, research, or build.

**Files:**
- Create: `hermes_cli/evolution/blueprint.py`
- Modify: `hermes_cli/evolution/ledger.py`
- Create: `tests/hermes_cli/evolution/test_blueprint.py`

**Context pack:** Project A canonical serializer; complete design Blueprint
field list, component adapter contract, canary requirements.

**Required top-level fields:**

```text
schema_version, attempt_id, revision_number, source, capability_gap,
desired_outcome, alternatives, selected_approach, components,
sources, dependencies, credential_references, service_prerequisites,
affected_capabilities, affected_contracts, affected_organism_areas,
build_strategy, isolation_policy, fixtures, real_pilot_task, canary_policy,
allowed_side_effects, resource_ceilings, expected_organism_diff,
rollback_triggers, risks, unknowns, operational_cost
```

**Interfaces:**

```python
def validate_blueprint(value: Mapping[str, object]) -> EvolutionBlueprint: ...
def blueprint_digest(value: Mapping[str, object]) -> str: ...
def save_blueprint_revision(ledger: EvolutionLedger, *,
                            value: Mapping[str, object]) -> StoredBlueprint: ...
def material_change(previous: EvolutionBlueprint,
                    current: EvolutionBlueprint) -> bool: ...
```

- [ ] **Step 1: Write failing completeness and closed-enum tests**

Reject missing fields, empty outcome/pilot/rollback triggers, unsupported
component class, arbitrary credential value, unlisted side effect, absolute
path/URI, source without author/license/provenance, dependency without family
and constraint, and canary without mandatory checks.

- [ ] **Step 2: Write failing digest tests**

Whitespace/key order do not change digest. Every material field above does.
`revision_number`, presentation-only notes, and creation timestamp are stored
but excluded from material identity only if they cannot affect build/canary.

- [ ] **Step 3: Run red tests**

Run: `scripts/run_tests.sh tests/hermes_cli/evolution/test_blueprint.py -q`

- [ ] **Step 4: Implement immutable typed validation**

Normalize only representation (trim identifiers, sort set-like values); never
invent a missing source, license, dependency, side effect, or acceptance test.

- [ ] **Step 5: Implement atomic revision storage**

Save canonical JSON and digest in one transaction, append a revision event,
and mark older unconsumed build requests/grants superseded. Preserve all prior
revisions.

- [ ] **Step 6: Run tests and commit**

Commit: `evolution-c: define immutable evolution blueprints`

**Escalate if:** a build-affecting edit keeps the same digest, a secret can be
serialized, or validation relies on a prose interpretation.

## Task 3: Workshop service and draft/revision lifecycle

**Model:** `gpt-5.6-terra`, reasoning `medium`

**Objective:** Start attempts from suggestions or user goals, bind a session,
create research requests, and save complete Blueprint revisions.

**Non-goals:** No web dispatch, approval issuance, or builder call.

**Files:**
- Create: `hermes_cli/evolution/workshop.py`
- Modify: `hermes_cli/evolution/suggestions.py`
- Create: `tests/hermes_cli/evolution/test_workshop.py`

**Context pack:** Project B suggestion API; Tasks 1–2; lifecycle transitions
`draft -> research_authorized -> blueprint_ready`.

**Interfaces:**

```python
def start_workshop(*, session_id: str, suggestion_id: str | None,
                   user_goal: str | None) -> WorkshopSession: ...
def request_research(*, attempt_id: str, source_classes: tuple[str, ...],
                     domains: tuple[str, ...], ttl_seconds: int) -> AuthorizationRequest: ...
def save_blueprint(*, session_id: str,
                   value: Mapping[str, object]) -> StoredBlueprint: ...
def workshop_status(*, session_id: str) -> WorkshopStatus: ...
```

- [ ] **Step 1: Write failing source tests**

Exactly one of suggestion ID or normalized user goal is required. Suggestion
acceptance is transactional; failed/expired retries create a new attempt but
do not delete the suggestion.

- [ ] **Step 2: Write failing session-binding tests**

Another session cannot write the draft. Resume by the same session is
idempotent. Explicit handoff is not part of MVP and must fail closed.

- [ ] **Step 3: Run red tests**

Run: `scripts/run_tests.sh tests/hermes_cli/evolution/test_workshop.py -q`

- [ ] **Step 4: Implement deterministic lifecycle methods**

Store normalized user goals as bounded summaries and digests; the full
conversation stays in normal session storage, not the evolution ledger.

- [ ] **Step 5: Implement research and Blueprint transitions**

Research request creation leaves the attempt `draft`; host grant issue moves
it to `research_authorized`; saving a complete revision moves it to
`blueprint_ready`.

- [ ] **Step 6: Run tests and commit**

Commit: `evolution-c: add collaborative workshop lifecycle`

**Escalate if:** session chat content is copied into the ledger or starting a
workshop implicitly grants research.

## Task 4: `/autopoiesis` across CLI, gateway, TUI, and desktop

**Model:** `gpt-5.6-terra`, reasoning `medium`

**Objective:** Provide one byte-stable conversational entry point and
host-dispatched approval subcommands on every supported chat surface.

**Non-goals:** No new model tool or system-prompt block.

**Files:**
- Create: `agent/autopoiesis_prompt.py`
- Modify: `hermes_cli/commands.py`
- Modify: `hermes_cli/cli_commands_mixin.py`
- Modify: `cli.py`
- Modify: `gateway/run.py`
- Modify: `tui_gateway/server.py`
- Modify: `apps/desktop/src/lib/desktop-slash-commands.ts`
- Create: `tests/agent/test_autopoiesis_prompt.py`
- Create: `tests/hermes_cli/evolution/test_autopoiesis_slash.py`
- Modify: `apps/desktop/src/lib/desktop-slash-commands.test.ts`

**Context pack:** Existing Gnothi prompt/dispatch pattern; Tasks 1–3 public
services; desktop extension-command rules from `AGENTS.md`.

**Prompt contract:** It tells the model to inspect the bound attempt and
`gnothi_seauton`, distinguish evidence/inference, brainstorm with the user,
request research rather than use the web without a grant, persist a complete
Blueprint via deterministic CLI, display “what Hades will become,” and stop
before build until the user approves the exact digest.

- [ ] **Step 1: Write failing prompt stability tests**

The fixed prefix is byte-identical for a goal and suggestion invocation; only
the final request block differs. It explicitly denies self-approval, direct
memory edits, core patches, downloads before build approval, and pointer writes.

- [ ] **Step 2: Write failing surface-routing tests**

`/autopoiesis [suggestion-id|goal]` becomes one normal user turn.
`/autopoiesis approve-research <request-id>`,
`approve-build`, and `approve-promotion` are handled by the host and never
submitted as model text.

- [ ] **Step 3: Run red tests**

Run Python tests plus the focused desktop Vitest.

- [ ] **Step 4: Register command metadata**

Add:

```python
CommandDef(
    "autopoiesis",
    "Design and govern a peripheral evolution of Hades",
    "Tools & Skills",
    args_hint="[suggestion-id|goal|approve-* <request-id>]",
)
```

Keep extension commands discoverable in desktop curation.

- [ ] **Step 5: Implement each dispatcher**

Classic CLI queues the stable prompt. Gateway and TUI use the same builder.
Approval subcommands call `request_user_grant` in a worker-safe manner and
return a bounded result without falling through to the model.

- [ ] **Step 6: Prove cache/alternation invariants**

Assert prior messages are unchanged, system prompt object is unchanged, tool
definition list is unchanged, and exactly one new user turn is submitted for
the conversational form.

- [ ] **Step 7: Run tests and commit**

Commit: `evolution-c: add governed autopoiesis workshop command`

**Escalate if:** a surface needs a synthetic role, desktop hides the command,
or an approval subcommand reaches the model.

## Task 5: Enforce and record bounded web research

**Model:** `gpt-5.6-terra`, reasoning `medium`

**Objective:** Permit only approved read-only web research in a bound Workshop
and record sanitized provenance/digests.

**Non-goals:** No executable download, package lookup acquisition, browser
automation, authentication, or candidate dependency resolution.

**Files:**
- Modify: `hermes_cli/evolution/research_gate.py`
- Modify: `model_tools.py`
- Create: `tests/hermes_cli/evolution/test_research_gate.py`
- Modify: `tests/tools/test_approval_plugin_hooks.py`

**Context pack:** Task 1 authority contract; current `handle_function_call`
pre/post-hook order; web tool schemas.

**Interfaces:**

```python
def authorize_web_call(*, session_id: str, tool_name: str,
                       arguments: Mapping[str, object]) -> ResearchDecision: ...
def record_web_result(*, decision: ResearchDecision, result: str) -> None: ...
```

- [ ] **Step 1: Write failing gate tests**

Normal sessions remain unaffected. Bound Workshop sessions block `web_search`
and `web_extract` without a lease, outside domain/source scope, after expiry,
or after attempt closure. Browser and arbitrary network tools are never added
to the lease.

- [ ] **Step 2: Write failing provenance tests**

Record source URL after redirect, retrieval timestamp, author/license when
reported, and content digest; store no page body, query body, local context, or
response excerpt in the ledger.

- [ ] **Step 3: Run red tests**

Run: `scripts/run_tests.sh tests/hermes_cli/evolution/test_research_gate.py -q`

- [ ] **Step 4: Add a narrow dispatch seam**

Before plugin hooks, call the evolution gate only when `session_id` has an
active Workshop binding and the tool name is `web_search` or `web_extract`.
On block, emit the normal post-tool observation with status `blocked`. On
success, record provenance after result canonicalization.

- [ ] **Step 5: Run regression tests**

Run focused research tests plus web tools, plugin approval hooks, and tool
search bridge tests.

- [ ] **Step 6: Commit**

Commit: `evolution-c: enforce scoped workshop research`

**Escalate if:** the gate changes ordinary web behavior, records response
bodies, or allows browser/MCP/network calls through a research grant.

## Task 6: Material deviations and build-approval requests

**Model:** `gpt-5.6-terra`, reasoning `medium`

**Objective:** Turn a complete Blueprint into an exact build request and
classify later discoveries as permitted resolution or material deviation.

**Non-goals:** No build execution or acquisition.

**Files:**
- Modify: `hermes_cli/evolution/blueprint.py`
- Modify: `hermes_cli/evolution/workshop.py`
- Modify: `hermes_cli/evolution/command.py`
- Modify: `hermes_cli/subcommands/evolution.py`
- Create: `tests/hermes_cli/evolution/test_build_request.py`
- Create: `tests/hermes_cli/evolution/test_deviations.py`

**Context pack:** Blueprint contract; design Builder prohibitions and build
authorization scope.

**Interfaces:**

```python
DeviationKind = Literal["none", "resolved_within_constraint", "material"]

def classify_deviation(blueprint: EvolutionBlueprint, *,
                       observed: Mapping[str, object]) -> DeviationDecision: ...
def request_build_approval(ledger: EvolutionLedger, *,
                           blueprint_digest: str) -> AuthorizationRequest: ...
```

- [ ] **Step 1: Write the deviation truth table**

An exact dependency version and digest within an approved family/constraint is
`resolved_within_constraint`. New component, family, source, domain, license,
credential, side effect, endpoint, privilege, environment variable, or
resource ceiling is `material`.

- [ ] **Step 2: Write failing request tests**

The request subject is exactly the Blueprint digest and its scope is derived
only from canonical fields. A newer revision supersedes the request. A
research grant cannot satisfy it.

- [ ] **Step 3: Run red tests**

Run both new test files.

- [ ] **Step 4: Implement pure deviation classification**

Return stable reason codes and bounded safe facts. Do not compare arbitrary
prose or ask a model.

- [ ] **Step 5: Add deterministic operator commands**

Add `workshop`, `show blueprint`, `request-build`, and `approve-build`.
Mutating preview-capable commands implement `--dry-run`. Approval follows Task
1's host/operator path.

- [ ] **Step 6: Run tests and commit**

Commit: `evolution-c: bind build requests to blueprint revisions`

**Escalate if:** any discovery can silently widen scope or a new revision keeps
an old build grant valid.

## Task 7: Real-path Workshop and consent verification

**Model:** `gpt-5.6-terra`, reasoning `high`

**Objective:** Prove suggestion acceptance, explicit research, Blueprint
revision, and build-request binding across real local interfaces.

**Non-goals:** Do not issue a build grant that Project D consumes and do not
construct a candidate.

**Files:**
- Create: `tests/integration/test_autopoiesis_workshop.py`
- Create: `docs/autopoiesis/workshop-v1.md`

No production files are planned in this verification task. A reproduced defect
must become a focused failing regression and a separate repair task before the
gate is rerun.

**Context pack:** Project B real suggestion fixture; all Project C public
interfaces; acceptance criteria 2–5 and 20.

- [ ] **Step 1: Start from a real suggestion**

Use the Project B integration event stream, invoke the real slash dispatch,
and assert one attempt bound to the session with no research grant.

- [ ] **Step 2: Prove denial then approval**

Deny one research request and assert no web call can run. Create a fresh
request, approve through a fake host callback that records the explicit user
decision, then execute real fixture-backed `web_search`/`web_extract`.

- [ ] **Step 3: Save and revise a Blueprint**

Save a complete fixture, request build approval, change one material field,
and prove the request is invalidated. Request again and approve the exact new
digest without consuming the grant.

- [ ] **Step 4: Verify forbidden persistence**

Inspect ledger and Blueprint files for research page bodies, prompt text,
fixture secrets, local absolute paths, and raw web output.

- [ ] **Step 5: Run full Project C tests**

```bash
scripts/run_tests.sh tests/hermes_cli/evolution -q
scripts/run_tests.sh tests/integration/test_autopoiesis_foundation.py tests/integration/test_autopoiesis_observer.py tests/integration/test_autopoiesis_workshop.py -q
scripts/run_tests.sh tests/agent/test_autopoiesis_prompt.py tests/agent/test_gnothi_prompt.py -q
```

- [ ] **Step 6: Document exact user journey and commit**

Commit: `evolution-c: verify blueprint-bound workshop`

The handoff gives Project D one unconsumed build grant fixture, its exact
Blueprint digest, and sanitized source manifests.

**Escalate if:** the model can self-authorize, research runs before approval,
or a material revision does not invalidate build authority.
