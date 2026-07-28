# Final whole-branch fix report

Date: 2026-07-28
Branch: `codex/agentic-kanban-orgrun`
Reviewed base: `3ea7d97e0407c1e1774c557454c69d7aace5e08a`
Functional fix commit: `d13b3bc5d`

## Outcome

The Critical managed-plan immutability failure and all Important findings in
`whole-branch-fix-brief.md` are fixed. The wave stayed within the local
Agentic-Kanban/OrgRun surfaces; Evolution, Gnothi, remote synchronization, and
backend-sync files were not changed.

## Finding-by-finding evidence

### 1. Managed-plan immutability and live trust

- Added a central `ManagedPlanMutationError` guard in `kanban_db.py`.
- Generic assignment/reassignment, direct and indirect DAG linking, triage
  specification/decomposition, archive, and deletion now reject managed cards.
- The only authorization escape is connection-scoped and requires the active
  atomic amendment transaction.
- Dashboard contract edits and generic managed mutations return HTTP 409.
- Stored local plan provenance now validates exact live assignee, title, body,
  skills, idempotency identity, and current-plan parent links.
- Both ready and review dispatch paths validate the entire live topology and
  route from the persisted logical role before spawning.
- Exact materialization replay and final-report projection use the same live
  validation, so terminal statuses alone cannot manufacture trusted evidence.

Regression coverage includes direct mutations, `create_task(parents=...)`,
dependency-block linking, live field drift, live parent drift, review-column
dispatch, persisted-role routing, exact replay, dashboard mutation paths, and
final-report drift rejection.

### 2. Installed profiles versus delegation routes

- CLI validation now calls `hermes_cli.profiles.profile_exists` independently
  from delegation-route resolution.
- Amendment validation requires both callbacks instead of treating a route as
  proof that a profile is installed.
- Regressions cover missing profile with present routes and missing amendment
  route with present profiles.

### 3. Immutable Git and validation provenance

- `base_commit` must be a lowercase full 40- or 64-hex commit OID.
- Git verifies `<oid>^{commit}` and the resolved canonical OID must equal the
  supplied value; abbreviations are rejected.
- Plan hash, dependency projection, scope conflicts, profile projection, and
  routed roles are recomputed and compared before the materialization write
  transaction opens.
- A forged `PlanValidation` regression proves no write transaction is entered.

### 4. Amendment provenance for retained nodes

- Effective node provenance includes the node contract and dependency-node set.
- Every retained active node changed by an amendment receives the new plan
  version and a contract hash recomputed from the immutable current plan.
- Unchanged retained nodes preserve their historical version, and prior plan
  versions remain immutable.
- Regressions cover integration rewiring and other retained-node DAG changes.

### 5. Completion board ownership

- `complete_task` accepts an explicit board and derives a managed task's board
  from OrgRun ownership before consulting global UI state.
- CLI and dashboard single/bulk completion paths carry the selected board.
- Report projection and lifecycle hooks use that resolved board.
- The dashboard regression completes and finalizes an OrgRun on explicit board
  `other` while the current board remains `default`, then verifies the report
  remains owned by `other`.

### 6. Durable OrgRun state

- Fresh materialization persists `materialized`, regardless of activation
  intent.
- Managed claim, completion, block, unblock, and schedule transitions refresh
  the owning OrgRun state inside their transaction.
- Legacy adoption starts from `materialized` and immediately derives its state
  from the adopted card rows.
- CLI `show` and `list` refresh from durable rows rather than returning a stale
  cached state.
- Recurrence-routed triage at the human-intervention threshold derives
  `blocked`, while ordinary pre-activation triage remains `materialized`.

### 7. Logbook run filtering and cancellation

- `list_reports(run_id=...)` selects the run-level report and task reports
  through `kanban_org_nodes` ownership.
- Actual `set_org_run_state(..., "cancelled")` atomically projects one
  canonical, bounded, redacted, versioned `org_run_cancelled` report.
- Repeated cancellation is idempotent and returns the original evidence.
- Merely blocked runs never receive a cancellation report.

## TDD record

The first focused RED run exercised the new regressions before production
changes. It produced the expected failures for abbreviated Git provenance,
forged validation, generic managed mutations, ready/review dispatch drift,
amendment provenance, profile/route separation, wrong-board completion, stale
state reads, run filtering, cancellation projection, and final-report trust.
The cancellation test initially failed at collection because the projector did
not yet exist. Focused compatibility failures found during GREEN were retained
as regressions and fixed at their owning boundaries.

## Verification

Baseline before this wave:

```text
Task 10 targeted gate: 723 passed, 1 skipped, 8 warnings
```

Final focused affected files:

```text
222 passed, 8 warnings in 23.20s
```

Final Task 10 targeted acceptance gate:

```text
747 passed, 1 skipped, 8 warnings in 47.67s
```

The gate covered:

```text
tests/hermes_cli/test_implementation_plan.py
tests/hermes_cli/test_org_run_store.py
tests/hermes_cli/test_agentic_org_run.py
tests/hermes_cli/test_org_run_amendments.py
tests/hermes_cli/test_kanban_reports.py
tests/hermes_cli/test_hades_org_cli.py
tests/hermes_cli/test_kanban_cli.py
tests/hermes_cli/test_kanban_core_functionality.py
tests/hermes_cli/test_kanban_db.py
tests/hermes_cli/test_kanban_db_init.py
tests/hermes_cli/test_kanban_decompose.py
tests/gateway/test_kanban_auto_decompose_live.py
tests/plugins/test_kanban_dashboard_plugin.py
tests/skills/test_hierarchical_development_skill.py
tests/e2e/test_agentic_kanban_org_run.py
```

Static checks:

```text
ruff check <14 touched Python files>: All checks passed
python -m py_compile <14 touched Python files>: passed
git diff --check: passed
```

The eight warnings are unchanged third-party `pkg_resources`, `lark_oapi`,
datetime, and websockets deprecations exercised by the dashboard suite. They
are unrelated to this wave.

## Residual risk

No known in-scope correctness issue remains. The central guard intentionally
does not freeze ordinary workflow status/result/evidence updates; it protects
the plan-owned contract, role, topology, archive, and identity surfaces while
allowing the Kanban lifecycle to execute.
