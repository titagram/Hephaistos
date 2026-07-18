# Task 12 execution brief — Express lifecycle adapter

## Closed assignment

- Implementer: `gpt-5.6-terra` with `high` reasoning; no delegation and no subagents.
- Required branch/base: begin only from shared base commit `06ed97dd50298a29cb209fecc3baa7ee995de062` on `codex/graph-lifecycle-v2-agent`.
- At execution time, start through `superpowers:using-git-worktrees`: create this Express worktree and the separate Next.js worktree from that same shared base. Do not create either worktree while preparing this handoff.
- Frozen Express bundle SHA-256: `27b490ad91f90c45d226a451eb6e71e0b3ed525290250805878bbb53ef01c7ac`.
- Frozen bundle paths (read-only): `tests/fixtures/hades/adapter_acceptance/express/corpus.json`, `tests/fixtures/hades/adapter_acceptance/express/matrix.json`, and `tests/fixtures/hades/adapter_acceptance/express/lock.json`.
- The two and only writable implementation source/test files are `hermes_cli/hades_index/lifecycle/frameworks/express.py` and `tests/hermes_cli/test_hades_lifecycle_express.py`. Every other production, test, fixture, lock, registry, integration, plan, spec, and controller path is read-only.
- Required source commit subject: `feat(hades): extract Express request lifecycles`.

Before editing, validate the frozen bundle and stop if its output is not the stated digest:

```bash
.venv/bin/python scripts/hades_adapter_acceptance.py validate \
  --corpus tests/fixtures/hades/adapter_acceptance/express/corpus.json \
  --matrix tests/fixtures/hades/adapter_acceptance/express/matrix.json \
  --lock tests/fixtures/hades/adapter_acceptance/express/lock.json
```

## Canonical Task 12 requirements

First add RED golden fixtures covering nested router mount; same path with multiple verbs; `.all`; ordered `use` and route handlers; proven `next()`, `next('route')`, and `next(err)`; `send`, `json`, `end`, and `redirect` terminal outcomes; thrown errors; returned async rejection; four-parameter error-middleware arity; and a computed target reported as unresolved. Run the RED selection before production implementation:

```bash
scripts/run_tests.sh tests/hermes_cli/test_hades_lifecycle_express.py -q
```

Then implement registration-order continuation semantics. Continue only through statically proven `next()`, preserve mount prefixes and parameters, preserve unrestricted `.all`, and never invent an HTTP method or continuation. GREEN requires the same focused command to pass, followed by Ruff format/check, Python compilation, and a scoped diff check:

```bash
scripts/run_tests.sh tests/hermes_cli/test_hades_lifecycle_express.py -q
.venv/bin/ruff format --check hermes_cli/hades_index/lifecycle/frameworks/express.py tests/hermes_cli/test_hades_lifecycle_express.py
.venv/bin/ruff check hermes_cli/hades_index/lifecycle/frameworks/express.py tests/hermes_cli/test_hades_lifecycle_express.py
.venv/bin/python -m py_compile hermes_cli/hades_index/lifecycle/frameworks/express.py
git diff --check -- hermes_cli/hades_index/lifecycle/frameworks/express.py tests/hermes_cli/test_hades_lifecycle_express.py
```

Self-review every frozen ID and negative envelope below before requesting review. `exact` is allowed only for demonstrated material facts; unsupported or computed forms must explicitly become `partial` or `unresolved`, never silently disappear or fabricate topology.

## Frozen Express acceptance matrix

- `EXPRESS-ROUTER-001` — exact nested literal router mount: parent mount prefix, nested router identity, composed normalized path, mounted handler identity. `EXPRESS-ROUTER-001-N1` is partial for computed mount prefix/target with `mount_prefix_unresolved` and `router_target_unresolved`.
  - Exact: `tests/hermes_cli/test_hades_lifecycle_express.py::test_nested_router_mount_composes_literal_path_prefix_exactly`
  - Negative: `tests/hermes_cli/test_hades_lifecycle_express.py::test_computed_router_mount_is_partial_with_explicit_mount_uncertainty`
- `EXPRESS-METHOD-001` — exact same literal path with distinct explicit verbs, per-method handler identities, and registration order. `EXPRESS-METHOD-001-N1` is unresolved for a computed verb with `route_method_unresolved` and no invented method.
  - Exact: `tests/hermes_cli/test_hades_lifecycle_express.py::test_same_path_explicit_verbs_remain_distinct_and_ordered`
  - Negative: `tests/hermes_cli/test_hades_lifecycle_express.py::test_computed_verb_registration_is_unresolved_without_an_invented_method`
- `EXPRESS-ALL-001` — exact `.all` registration: normalized path, unrestricted method constraint, handler identity, and no invented single method. `EXPRESS-ALL-001-N1` is partial for a computed path with `route_path_unresolved`.
  - Exact: `tests/hermes_cli/test_hades_lifecycle_express.py::test_all_registration_remains_method_unrestricted`
  - Negative: `tests/hermes_cli/test_hades_lifecycle_express.py::test_all_with_computed_path_is_partial_without_an_invented_path`
- `EXPRESS-ORDER-001` — exact registration-ordered `use` and route handler chain. `EXPRESS-ORDER-001-N1` is partial for a computed middleware/handler collection with `middleware_order_unresolved` and `handler_target_unresolved`.
  - Exact: `tests/hermes_cli/test_hades_lifecycle_express.py::test_use_and_route_handlers_preserve_registration_order`
  - Negative: `tests/hermes_cli/test_hades_lifecycle_express.py::test_computed_handler_collection_is_partial_with_explicit_order_uncertainty`
- `EXPRESS-NEXT-001` — exact proven continuation: `next()` continues the ordered stack, `next('route')` skips remaining route handlers and resumes at the next matching route, `next(err)` enters ordered error flow, and ordinary handlers are skipped during error flow. `EXPRESS-NEXT-001-N1` is partial for a computed argument with `continuation_kind_unresolved` and `error_flow_unresolved`.
  - Exact: `tests/hermes_cli/test_hades_lifecycle_express.py::test_proven_next_forms_select_the_correct_continuation`
  - Negative: `tests/hermes_cli/test_hades_lifecycle_express.py::test_computed_next_argument_is_partial_without_an_invented_continuation`
- `EXPRESS-TERMINAL-001` — exact terminal outcomes for `send`, `json`, `end`, and `redirect`, with no fallthrough without proven `next`. `EXPRESS-TERMINAL-001-N1` is partial for a computed response terminal method with `response_outcome_unresolved`.
  - Exact: `tests/hermes_cli/test_hades_lifecycle_express.py::test_send_json_end_and_redirect_are_terminal_outcomes`
  - Negative: `tests/hermes_cli/test_hades_lifecycle_express.py::test_computed_response_terminal_is_partial_without_an_invented_outcome`
- `EXPRESS-ERROR-001` — exact direct throws and returned rejected handler promises enter error flow with error identity when proven and ordinary handlers skipped. `EXPRESS-ERROR-001-N1` is partial for a detached, non-returned rejection with `async_error_flow_unresolved`.
  - Exact: `tests/hermes_cli/test_hades_lifecycle_express.py::test_direct_throw_and_returned_async_rejection_enter_error_flow`
  - Negative: `tests/hermes_cli/test_hades_lifecycle_express.py::test_detached_async_rejection_is_partial_with_explicit_error_flow_uncertainty`
- `EXPRESS-ERROR-MIDDLEWARE-001` — exact error-flow selection by declared four-parameter arity, excluding ordinary three-parameter middleware. `EXPRESS-ERROR-MIDDLEWARE-001-N1` is partial for a computed error middleware target with `error_middleware_arity_unresolved` and `handler_target_unresolved`.
  - Exact: `tests/hermes_cli/test_hades_lifecycle_express.py::test_four_parameter_error_middleware_is_selected_by_arity`
  - Negative: `tests/hermes_cli/test_hades_lifecycle_express.py::test_computed_error_middleware_target_is_partial_with_explicit_arity_uncertainty`
- `EXPRESS-COMPUTED-001` — unresolved computed route registration target or method: no invented route/method and explicit uncertainty coverage for registration target, route method/path, and handler target.
  - Test: `tests/hermes_cli/test_hades_lifecycle_express.py::test_computed_registration_target_is_unresolved_without_an_invented_route`

Commit only the two owned files:

```bash
git add hermes_cli/hades_index/lifecycle/frameworks/express.py tests/hermes_cli/test_hades_lifecycle_express.py
git diff --cached --check
git commit -m "feat(hades): extract Express request lifecycles"
```

## Independent finite review and stop conditions

Require exactly one complete independent `gpt-5.6-sol/xhigh` review. The reviewer must not edit production code, delegate, or expand the frozen matrix; it must report every blocking finding in one batch with a stable finding ID, violated matrix ID/invariant, reproducer, actual result, required result, and rationale. The original implementer receives at most one repair assignment for that complete batch. The same reviewer then performs one scoped re-review of only those finding IDs and directly caused regressions within the frozen matrix; no exploratory second review follows. If that repair does not close the batch, stop for design escalation.

Stop without editing any shared path if the base/branch changes, the locked digest changes or fails validation, a matrix/corpus/lock change is requested, either owned file is no longer exclusive, or any registry/generated/integration/shared-file edit is required. Report that condition to the orchestrator; do not widen scope or create a substitute matrix.
