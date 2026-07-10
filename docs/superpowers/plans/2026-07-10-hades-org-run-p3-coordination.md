# Hades OrgRun P3-P4 Coordination and Delegation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add typed, filtered coordination and deterministic integration gates to OrgRun, then optimize short-lived delegate trees with local role routing and a shared budget.

**Architecture:** Encode coordination as structured comments/events on the existing OrgRun blackboard, project inboxes as read-time projections, and compose all dispatch gates through one generic admission pipeline. Keep ephemeral delegation process-local and independent from durable Kanban state.

**Tech Stack:** Python, dataclasses, JSON, SQLite, existing Kanban tools, Git worktrees, existing `delegate_tool`, pytest.

## Global Constraints

- P1 is required for all P3 tasks; P2 is required only for remote E2E.
- No `org_*` tables, chat mesh, periodic LLM standup or new core tool.
- Extend existing `kanban_comment`; do not add `org_comm` model tool.
- Decisions affecting public contracts must block impacted dispatch.
- Integration is deterministic and evidence-driven; no automatic push/PR.
- P4 must not change the model within an existing conversation.

---

### Task P3-T01: Define the typed organization-message contract

**Files:**
- Create: `hermes_cli/hades_org_messages.py`.
- Create: `tests/hermes_cli/test_hades_org_messages.py`.

**Interfaces:**
- Produces `OrgMessage`, `parse_org_message`, `render_org_message`.
- Schema: `hades.org-message.v1`.

- [ ] **Step 1: Write validation and round-trip tests**

Test every allowed type, reject unknown type/severity, absolute scope path,
missing summary, more than 20 related tasks, more than 20 evidence refs, summary
over 500 characters and body over 4000 characters.

- [ ] **Step 2: Implement exact shape**

```python
ORG_MESSAGE_SCHEMA = "hades.org-message.v1"
ORG_MESSAGE_PREFIX = "[org:message] "
ORG_MESSAGE_TYPES = frozenset({
    "fyi", "handoff", "blocker", "decision_proposal",
    "decision_resolution", "interface_change", "review_request",
    "integration_notice",
})
ORG_MESSAGE_SEVERITIES = frozenset({"info", "action_required", "blocking"})


@dataclass(frozen=True)
class OrgMessage:
    schema: str
    message_id: str
    org_run_id: str
    sender_task_id: str
    sender_role: str
    message_type: str
    severity: str
    summary: str
    body: str
    scope: tuple[str, ...]
    related_tasks: tuple[str, ...]
    required_action: str | None
    evidence_refs: tuple[str, ...]
    resolves_message_id: str | None
```

`render_org_message` returns prefix plus compact, sorted JSON. Parser accepts
only prefixed content and validates repository-relative scope.

- [ ] **Step 3: Verify and commit**

```bash
scripts/run_tests.sh tests/hermes_cli/test_hades_org_messages.py -q
git add hermes_cli/hades_org_messages.py tests/hermes_cli/test_hades_org_messages.py
git commit -m "feat(hades): define typed OrgRun messages"
```

---

### Task P3-T02: Extend `kanban_comment` without adding a tool

**Files:**
- Modify: `tools/kanban_tools.py` handler and existing schema.
- Modify: `tests/tools/test_kanban_tools.py`.
- Modify: `tests/tools/test_kanban_redaction.py`.

**Interfaces:**
- Existing `kanban_comment` gains optional object `org_message`.
- Plain `body` behavior remains unchanged.

- [ ] **Step 1: Add tests**

Assert a valid `org_message` becomes one prefixed structured comment, author
still comes from `HERMES_PROFILE`, forged sender role/task identity is replaced
with current runtime task/profile, and secret content is redacted. Invalid
objects return `tool_error` without writing a comment.

- [ ] **Step 2: Extend schema**

Add optional `org_message` with fields matching P3-T01 except server-derived
`schema`, `message_id`, `sender_task_id`, `sender_role`. Keep `body` optional
only when `org_message` is present; require at least one of them.

- [ ] **Step 3: Implement handler branch**

Generate `message_id` with `secrets.token_hex(12)`. Derive sender task from
`HERMES_KANBAN_TASK` and role from `HERMES_PROFILE`. Call `render_org_message`,
then existing `kb.add_comment`. Do not expose author override.

- [ ] **Step 4: Verify and commit**

```bash
scripts/run_tests.sh tests/tools/test_kanban_tools.py tests/tools/test_kanban_redaction.py -q
git add tools/kanban_tools.py tests/tools/test_kanban_tools.py tests/tools/test_kanban_redaction.py
git commit -m "feat(kanban): support typed OrgRun comments"
```

---

### Task P3-T03: Build filtered inbox and decision projection

**Files:**
- Modify: `hermes_cli/hades_org_messages.py`.
- Modify: `tests/hermes_cli/test_hades_org_messages.py`.

**Interfaces:**
- Produces `list_org_messages`, `org_inbox`, `open_decisions`.

- [ ] **Step 1: Add projection tests**

Create messages on one OrgRun anchor and assert a worker sees only:

- messages directly related to its task;
- messages whose scope intersects its declared read/write scope;
- blockers from dependency tasks;
- decision resolutions for a proposal it could see.

Assert a marshal with `include_squad=True` sees its squad messages. Assert a
resolution closes exactly its `resolves_message_id`; unrelated proposals stay
open. No read marks or new DB rows are created.

- [ ] **Step 2: Implement pure queries**

Expose exactly:

- `list_org_messages(conn, anchor_id: str) -> tuple[OrgMessage, ...]`;
- `org_inbox(conn, anchor_id: str, *, task_id: str, scope: tuple[str, ...], dependency_ids: tuple[str, ...] = ()) -> tuple[OrgMessage, ...]`;
- `open_decisions(messages: tuple[OrgMessage, ...]) -> tuple[OrgMessage, ...]`.

Sort by comment creation time then `message_id`. Malformed prefixed comments
are skipped and logged; they never crash a worker.

- [ ] **Step 3: Verify and commit**

```bash
scripts/run_tests.sh tests/hermes_cli/test_hades_org_messages.py -q
git add hermes_cli/hades_org_messages.py tests/hermes_cli/test_hades_org_messages.py
git commit -m "feat(hades): project OrgRun inboxes and decisions"
```

---

### Task P3-T04: Compose dispatch admission policies

**Files:**
- Create: `hermes_cli/kanban_admission.py`.
- Create: `tests/hermes_cli/test_kanban_admission.py`.
- Modify: `gateway/kanban_watchers.py`.

**Interfaces:**
- Produces `compose_dispatch_admissions` and `OrgDecisionAdmission`.

- [ ] **Step 1: Test precedence**

Precedence is `supersede > defer > allow`. All policies run until supersede;
exceptions become defer. Reasons are joined deterministically with `; ` and
deduplicated.

- [ ] **Step 2: Implement composer**

```python
def compose_dispatch_admissions(*policies):
    active = tuple(policy for policy in policies if policy is not None)
    if not active:
        return None

    def admit(task):
        decisions = [policy(task) for policy in active]
        action = "allow"
        if any(item.action == "supersede" for item in decisions):
            action = "supersede"
        elif any(item.action == "defer" for item in decisions):
            action = "defer"
        reasons = sorted({item.reason for item in decisions if item.reason})
        return DispatchAdmission(action, "; ".join(reasons))

    return admit
```

- [ ] **Step 3: Add decision gate**

`OrgDecisionAdmission` looks up the task topology and open decisions. Return
defer when a blocking `decision_proposal` or `interface_change` intersects the
task scope and has no resolution. Unmapped/local non-OrgRun tasks return allow.

- [ ] **Step 4: Compose with backend admission**

Gateway passes one callback composed from optional Hades mirror admission and
optional decision admission. Do not add a second dispatcher parameter.

- [ ] **Step 5: Verify and commit**

```bash
scripts/run_tests.sh tests/hermes_cli/test_kanban_admission.py tests/gateway/test_hades_kanban_sync_watcher.py -q
git add hermes_cli/kanban_admission.py gateway/kanban_watchers.py tests/hermes_cli/test_kanban_admission.py
git commit -m "feat(kanban): compose OrgRun dispatch gates"
```

---

### Task P3-T05: Derive standup and integration plan

**Files:**
- Create: `hermes_cli/kanban_integration.py`.
- Create: `tests/hermes_cli/test_kanban_integration.py`.

**Interfaces:**
- Produces `derive_org_standup`, `build_integration_plan`.

- [ ] **Step 1: Test standup as a pure snapshot**

Given done/running/blocked/todo cards and open decisions, assert exact counts,
IDs, conflicts and risks. Call twice and assert equal results plus unchanged
DB row counts. No LLM or network mock appears in this test.

- [ ] **Step 2: Test deterministic patch ordering**

Completion metadata must provide `commit_sha` or `patch_ref`, `base_commit`,
`changed_files`, and tests. Reject missing evidence, mismatched base commit,
duplicate commit SHA and changed file outside declared scope. Order by DAG
topology, then priority descending, then remote task ID.

- [ ] **Step 3: Implement exact result types**

```python
@dataclass(frozen=True)
class IntegrationItem:
    remote_task_id: str
    completion_task_id: str
    commit_sha: str | None
    patch_ref: str | None
    changed_files: tuple[str, ...]


@dataclass(frozen=True)
class IntegrationPlan:
    org_run_id: str
    base_commit: str
    items: tuple[IntegrationItem, ...]
    final_test_commands: tuple[str, ...]
```

The module plans integration only. It never runs Git commands itself.

- [ ] **Step 4: Verify and commit**

```bash
scripts/run_tests.sh tests/hermes_cli/test_kanban_integration.py -q
git add hermes_cli/kanban_integration.py tests/hermes_cli/test_kanban_integration.py
git commit -m "feat(kanban): derive OrgRun standup and integration plan"
```

---

### Task P3-T05B: Enforce cross-device code-claim conflicts

**Files:**
- Modify: `hermes_cli/hades_coordination.py` at `claim_and_run`.
- Modify: `tests/hermes_cli/test_hades_code_claim_local.py`.

**Interfaces:**
- Existing `claim_and_run` gains keyword-only
  `require_conflict_free: bool = False`.

- [ ] **Step 1: Freeze legacy soft-claim behavior**

Add a test proving the runner still executes when conflicts are returned and
`require_conflict_free` is omitted. This protects existing callers.

- [ ] **Step 2: Test strict OrgRun behavior**

With `require_conflict_free=True` and a non-empty conflict list, assert runner
is not called, claim is released in `finally`, and
`HadesCodeClaimConflict` contains the redacted conflict count and refs but no
other agent transcript or secret.

- [ ] **Step 3: Implement the strict branch**

Define `HadesCodeClaimConflict(RuntimeError)`. After claim creation and before
runner invocation, raise it when strict mode sees conflicts. Do not change the
existing default or release logic. OrgRun integration workers must call strict
mode; read/verify scopes may keep the default.

- [ ] **Step 4: Verify and commit**

```bash
scripts/run_tests.sh tests/hermes_cli/test_hades_code_claim_local.py -q
git add hermes_cli/hades_coordination.py tests/hermes_cli/test_hades_code_claim_local.py
git commit -m "feat(hades): enforce strict code claims for OrgRun writers"
```

---

### Task P3-T06: Prove interface-change coordination E2E

**Files:**
- Create: `tests/hermes_cli/test_hades_org_coordination_e2e.py`.

**Interfaces:**
- Consumes all P3 interfaces.
- Produces release proof only.

- [ ] **Step 1: Build the scenario**

Materialize tasks A and B. A owns `contracts.py`; B owns `consumer.py` and
depends semantically on the contract. Post a blocking `interface_change` from
A scoped to both files. Assert B admission defers. Post a resolution. Assert B
admission allows. Complete both with evidence, build integration plan, then
close/reopen DB and assert identical standup and decision state.

- [ ] **Step 2: Run full P3 gate and commit**

```bash
scripts/run_tests.sh tests/hermes_cli/test_hades_org_messages.py tests/tools/test_kanban_tools.py tests/hermes_cli/test_kanban_admission.py tests/hermes_cli/test_kanban_integration.py tests/hermes_cli/test_hades_org_coordination_e2e.py -q
git add tests/hermes_cli/test_hades_org_coordination_e2e.py
git commit -m "test(hades): prove OrgRun interface coordination"
```

---

### Task P4-T01: Parse allow-listed delegation role routes

**Files:**
- Create: `tools/delegation_routing.py`.
- Create: `tests/tools/test_delegation_routing.py`.
- Modify: `website/docs/user-guide/configuration.md` and
  `website/docs/user-guide/features/delegation.md`.

**Interfaces:**
- Produces `DelegationProfile`, `DelegationRouting`, `load_delegation_routing`,
  `resolve_role_profile`.

- [ ] **Step 1: Test defaults and rejection**

No `profiles/role_routes` returns legacy mode. Reject routes to undefined
profiles, arbitrary roles, empty model/provider, non-positive limits and
user-facing secrets in config. Allowed roles are `orchestrator`, `leaf`,
`reviewer`.

- [ ] **Step 2: Implement exact public shapes**

```python
@dataclass(frozen=True)
class DelegationProfile:
    provider: str
    model: str
    reasoning_effort: str | None
    max_iterations: int
    child_timeout_seconds: int


@dataclass(frozen=True)
class DelegationRouting:
    profiles: dict[str, DelegationProfile]
    role_routes: dict[str, str]
```

The resolver accepts only a logical role and returns a configured profile. It
never accepts provider/model from tool arguments.

- [ ] **Step 3: Verify and commit**

```bash
scripts/run_tests.sh tests/tools/test_delegation_routing.py -q
git add tools/delegation_routing.py tests/tools/test_delegation_routing.py website/docs/user-guide/configuration.md website/docs/user-guide/features/delegation.md
git commit -m "feat(delegation): route child runtime by logical role"
```

---

### Task P4-T02: Add shared atomic tree budget

**Files:**
- Create: `tools/delegation_budget.py`.
- Create: `tests/tools/test_delegation_budget.py`.

**Interfaces:**
- Produces `DelegationTreeBudget`, `BudgetReservation`, `BudgetExhausted`.

- [ ] **Step 1: Test sequential and concurrent reservations**

Cover node exhaustion, iteration exhaustion, failure/replan counters, rollback
when child creation fails, commit when child starts and 20-thread race proving
the configured node limit is never exceeded.

- [ ] **Step 2: Implement thread-safe API**

Expose exactly these methods:

- `DelegationTreeBudget.reserve_child(*, iterations: int) -> BudgetReservation`;
- `DelegationTreeBudget.record_failure() -> None`;
- `DelegationTreeBudget.reserve_replan() -> None`;
- `DelegationTreeBudget.snapshot() -> dict[str, int]`;
- `BudgetReservation.commit() -> None`;
- `BudgetReservation.rollback() -> None`.

Use `threading.Lock`. Reservation decrements atomically; rollback restores
exactly once; double commit/rollback raises `RuntimeError`.

- [ ] **Step 3: Verify and commit**

```bash
scripts/run_tests.sh tests/tools/test_delegation_budget.py -q
git add tools/delegation_budget.py tests/tools/test_delegation_budget.py
git commit -m "feat(delegation): share atomic budget across child tree"
```

---

### Task P4-T03: Wire routing and budget into `delegate_task`

**Files:**
- Modify: `tools/delegate_tool.py` at config resolution and child construction.
- Modify: `tests/tools/test_delegate.py`.

**Interfaces:**
- Consumes P4-T01 and P4-T02.
- Preserves existing tool schema roles and all legacy defaults.

- [ ] **Step 1: Add integration tests**

Assert orchestrator resolves marshal profile, leaf resolves worker profile,
reviewer is internal-only and absent from public role enum, undefined routing
uses legacy parent credentials, sibling race respects shared node budget,
failed child construction rolls reservation back, and child receives the same
budget object identity as its parent.

- [ ] **Step 2: Wire without adding tool arguments**

Resolve routing after role normalization. Construct child credentials and
limits from the local profile. Store budget on root agent as
`_delegation_tree_budget`; children inherit the same reference. Reserve before
child construction, commit after successful start, rollback on every exception.

- [ ] **Step 3: Run delegation regressions**

```bash
scripts/run_tests.sh tests/tools/test_delegation_routing.py tests/tools/test_delegation_budget.py tests/tools/test_delegate.py tests/run_agent/test_agent_guardrails.py -q
```

Expected: exit code `0`; public tool schema still exposes only `leaf` and
`orchestrator`.

- [ ] **Step 4: Commit**

```bash
git add tools/delegate_tool.py tests/tools/test_delegate.py
git commit -m "feat(delegation): apply role routing and tree budget"
```

---

### Task P4-T04: Add the hierarchical-development skill and E2E gate

**Files:**
- Create: `skills/software-development/hierarchical-development/SKILL.md`.
- Create: `tests/skills/test_hierarchical_development_skill.py`.

**Interfaces:**
- Produces bundled skill; no Python API.

- [ ] **Step 1: Write structural skill test**

Assert the skill requires: fast-vs-durable classification, ExecutionPlan or
portfolio input, bounded scope, reviewer independence, evidence contract,
escalation on drift, no direct model selection and no raw backend upload.

- [ ] **Step 2: Write the skill**

The skill must instruct planner -> marshal -> leaf execution and explicitly
route durable/restart-safe work to Kanban OrgRun. It must not modify the
official installed subagent skill and must not instruct changing system prompts
mid-conversation.

- [ ] **Step 3: Run full P4 gate and commit**

```bash
scripts/run_tests.sh tests/skills/test_hierarchical_development_skill.py tests/tools/test_delegation_routing.py tests/tools/test_delegation_budget.py tests/tools/test_delegate.py -q
git add skills/software-development/hierarchical-development/SKILL.md tests/skills/test_hierarchical_development_skill.py
git commit -m "feat(skills): add bounded hierarchical development workflow"
```

## P3-P4 Completion Checklist

- [ ] Typed messages reuse `kanban_comment`; no new tool exists.
- [ ] Inbox is a query projection; no inbox table exists.
- [ ] Standup performs no writes and no LLM call.
- [ ] Blocking interface change defers impacted task until resolution.
- [ ] Integration planner rejects missing/out-of-scope evidence.
- [ ] Public delegation schema still offers only leaf/orchestrator.
- [ ] Role routing is local and allow-listed.
- [ ] Shared budget passes concurrent reservation test.
- [ ] All P3/P4 gates pass and `git status --short` is empty.
