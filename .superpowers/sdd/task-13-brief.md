# Task 13 execution brief — Next.js lifecycle adapter

## Closed assignment

- Brief path: `.superpowers/sdd/task-13-brief.md`.
- Implementer: `gpt-5.6-terra` with `high` reasoning; no delegation and no subagents.
- Required branch/base: begin only from shared base commit `06ed97dd50298a29cb209fecc3baa7ee995de062` on `codex/graph-lifecycle-v2-agent`.
- At execution time, start through `superpowers:using-git-worktrees`: create this Next.js worktree and the separate Express worktree from that same shared base. Do not create either worktree while preparing this handoff.
- Frozen Next.js bundle SHA-256: `c0c6d651bbce5f096a61cb72fa66c2080c39d1b4e6c18342295d7e5974cf3af2`.
- Frozen bundle paths (read-only): `tests/fixtures/hades/adapter_acceptance/nextjs/corpus.json`, `tests/fixtures/hades/adapter_acceptance/nextjs/matrix.json`, and `tests/fixtures/hades/adapter_acceptance/nextjs/lock.json`.
- The two and only writable implementation source/test files are `hermes_cli/hades_index/lifecycle/frameworks/nextjs.py` and `tests/hermes_cli/test_hades_lifecycle_nextjs.py`. Every other production, test, fixture, lock, registry, integration, plan, spec, and controller path is read-only except the role-specific controller artifacts named in **Artifact routing and completion definition** below.
- Required source commit subject: `feat(hades): extract Next.js request lifecycles`.

Before editing, validate the frozen bundle and stop if its output is not the stated digest:

```bash
.venv/bin/python scripts/hades_adapter_acceptance.py validate \
  --corpus tests/fixtures/hades/adapter_acceptance/nextjs/corpus.json \
  --matrix tests/fixtures/hades/adapter_acceptance/nextjs/matrix.json \
  --lock tests/fixtures/hades/adapter_acceptance/nextjs/lock.json
```

## Canonical Task 13 requirements

First add RED golden fixtures covering App Router `GET`/`POST`; Pages API method switch; route groups; dynamic and catch-all patterns; detected-version precedence; middleware matcher, redirect, response, and next; static rewrite; unresolved computed config; and explicit exclusion of server/client render graphs as HTTP entrypoints. Run the RED selection before production implementation:

```bash
scripts/run_tests.sh tests/hermes_cli/test_hades_lifecycle_nextjs.py -q
```

Then implement filesystem and middleware semantics: one App Router export is one HTTP method; Pages API is unrestricted unless exhaustive dispatch proves its arms; normalized public patterns and source files are recorded; and only static matcher/rewrite/redirect configuration is evaluated. GREEN requires the focused command, Ruff format/check, Python compilation, and scoped diff check:

```bash
scripts/run_tests.sh tests/hermes_cli/test_hades_lifecycle_nextjs.py -q
.venv/bin/ruff format --check hermes_cli/hades_index/lifecycle/frameworks/nextjs.py tests/hermes_cli/test_hades_lifecycle_nextjs.py
.venv/bin/ruff check hermes_cli/hades_index/lifecycle/frameworks/nextjs.py tests/hermes_cli/test_hades_lifecycle_nextjs.py
.venv/bin/python -m py_compile hermes_cli/hades_index/lifecycle/frameworks/nextjs.py
git diff --check -- hermes_cli/hades_index/lifecycle/frameworks/nextjs.py tests/hermes_cli/test_hades_lifecycle_nextjs.py
```

Focused implementation may run concurrently with Express. Its final aggregate framework gate waits for Express integration: after Express is integrated, the orchestrator rebases/transplants Next.js onto that integrated base and runs the aggregate suite once. Do not edit shared integration surfaces to make that happen:

```bash
scripts/run_tests.sh tests/hermes_cli/test_hades_lifecycle_framework_adapter.py tests/hermes_cli/test_hades_lifecycle_symfony.py tests/hermes_cli/test_hades_lifecycle_laravel.py tests/hermes_cli/test_hades_lifecycle_django.py tests/hermes_cli/test_hades_lifecycle_fastapi.py tests/hermes_cli/test_hades_lifecycle_express.py tests/hermes_cli/test_hades_lifecycle_nextjs.py -q
```

Self-review every frozen ID and negative envelope below before requesting review. `exact` is allowed only for demonstrated material facts; unsupported or computed forms must explicitly become `partial` or `unresolved`, never silently disappear or fabricate topology.

## Frozen Next.js acceptance matrix

- `NEXTJS-APP-ROUTE-001` — exact static App Router `GET`/`POST` exports: source file, normalized public path, one method per export, and resolved handler export. `NEXTJS-APP-ROUTE-001-N1` is partial for an unresolved re-export with `route_handler_export_target_unresolved`.
  - Exact: `tests/hermes_cli/test_hades_lifecycle_nextjs.py::test_app_route_static_get_and_post_exports_are_method_entrypoints`
  - Negative: `tests/hermes_cli/test_hades_lifecycle_nextjs.py::test_app_route_reexport_with_unresolved_target_is_partial`
- `NEXTJS-PAGES-API-001` — exact Pages API default handler with literal method arms when exhaustive, otherwise an unrestricted method. `NEXTJS-PAGES-API-001-N1` is partial for computed/delegated dispatch with `pages_api_method_dispatch_unresolved`.
  - Exact: `tests/hermes_cli/test_hades_lifecycle_nextjs.py::test_pages_api_exhaustive_method_switch_and_unrestricted_fallback`
  - Negative: `tests/hermes_cli/test_hades_lifecycle_nextjs.py::test_pages_api_computed_method_dispatch_is_partial`
- `NEXTJS-FILESYSTEM-ROUTE-001` — exact route groups excluded from public path plus dynamic, catch-all, optional catch-all parameters, normalized pattern, and source file. `NEXTJS-FILESYSTEM-ROUTE-001-N1` is unresolved for malformed/unsupported bracket syntax with `route_pattern_unresolved`.
  - Exact: `tests/hermes_cli/test_hades_lifecycle_nextjs.py::test_route_groups_dynamic_and_catch_all_segments_normalize_public_paths`
  - Negative: `tests/hermes_cli/test_hades_lifecycle_nextjs.py::test_unsupported_filesystem_segment_syntax_is_unresolved`
- `NEXTJS-VERSION-001` — exact detected framework record precedence, package metadata fallback, and selected-version provenance. `NEXTJS-VERSION-001-N1` is unresolved for no detection and a nonliteral package version with `framework_version_unresolved`.
  - Exact: `tests/hermes_cli/test_hades_lifecycle_nextjs.py::test_detected_nextjs_version_precedes_package_metadata`
  - Negative: `tests/hermes_cli/test_hades_lifecycle_nextjs.py::test_nonliteral_package_version_without_detection_is_unresolved`
- `NEXTJS-MIDDLEWARE-MATCHER-001` — exact static middleware matcher supports literal strings, ordered literal arrays, and literal object `source`. `NEXTJS-MIDDLEWARE-MATCHER-001-N1` is partial for computed matcher values with `middleware_matcher_unresolved`.
  - Exact: `tests/hermes_cli/test_hades_lifecycle_nextjs.py::test_static_middleware_matcher_is_exact`
  - Negative: `tests/hermes_cli/test_hades_lifecycle_nextjs.py::test_computed_middleware_matcher_is_partial`
- `NEXTJS-MIDDLEWARE-OUTCOME-001` — exact middleware redirect, response, and next outcomes: literal redirect target, response terminal, and `NextResponse.next` continuation. `NEXTJS-MIDDLEWARE-OUTCOME-001-N1` is partial for delegated/unproven outcomes with `middleware_outcome_unresolved`.
  - Exact: `tests/hermes_cli/test_hades_lifecycle_nextjs.py::test_middleware_redirect_response_and_next_outcomes`
  - Negative: `tests/hermes_cli/test_hades_lifecycle_nextjs.py::test_unproven_middleware_outcome_is_partial`
- `NEXTJS-REWRITE-001` — exact static `next.config` rewrite entries: literal source/destination, registration order, and array/named phase. `NEXTJS-REWRITE-001-N1` is partial for computed source/destination with `framework_config_unresolved`.
  - Exact: `tests/hermes_cli/test_hades_lifecycle_nextjs.py::test_static_next_config_rewrites_are_exact`
  - Negative: `tests/hermes_cli/test_hades_lifecycle_nextjs.py::test_computed_rewrite_entry_is_partial`
- `NEXTJS-REDIRECT-001` — exact static redirect entries: literal source/destination/permanent flag, derived 307/308 status, and registration order. `NEXTJS-REDIRECT-001-N1` is partial for computed source, destination, or permanence with `framework_config_unresolved`.
  - Exact: `tests/hermes_cli/test_hades_lifecycle_nextjs.py::test_static_next_config_redirects_are_exact`
  - Negative: `tests/hermes_cli/test_hades_lifecycle_nextjs.py::test_computed_redirect_entry_is_partial`
- `NEXTJS-COMPUTED-MATCHER-001` — partial computed middleware matcher configuration: detect the middleware file, invent no matcher, and emit explicit partial coverage for computed matcher values.
  - Test: `tests/hermes_cli/test_hades_lifecycle_nextjs.py::test_computed_middleware_config_reports_explicit_uncertainty`
- `NEXTJS-COMPUTED-CONFIG-001` — unresolved computed rewrite/redirect config: detect the boundary, invent no rewrite/redirect, and emit explicit unresolved coverage for computed source/destination and conditional entries.
  - Test: `tests/hermes_cli/test_hades_lifecycle_nextjs.py::test_computed_rewrite_or_redirect_config_is_unresolved`
- `NEXTJS-RENDER-EXCLUSION-001` — exact exclusion of page, layout, and component render graphs as HTTP entrypoints while HTTP boundary files remain eligible. `NEXTJS-RENDER-EXCLUSION-001-N1` is partial for an obscured nonstandard render-file role with `http_entrypoint_file_role_unresolved`.
  - Exact: `tests/hermes_cli/test_hades_lifecycle_nextjs.py::test_render_graph_files_are_not_http_entrypoints`
  - Negative: `tests/hermes_cli/test_hades_lifecycle_nextjs.py::test_ambiguous_nonstandard_render_file_role_is_partial`

Commit only the two owned files:

```bash
git add hermes_cli/hades_index/lifecycle/frameworks/nextjs.py tests/hermes_cli/test_hades_lifecycle_nextjs.py
git diff --cached --check
git commit -m "feat(hades): extract Next.js request lifecycles"
```

## Artifact routing and completion definition

- Implementer report: `.superpowers/sdd/task-13-report.md`.
- Reviewer dispatch brief: `.superpowers/sdd/task-13-review-brief.md`; independent review report: `.superpowers/sdd/task-13-review.md`.
- Conditional repair brief: `.superpowers/sdd/task-13-repair-brief.md`; conditional repair report: `.superpowers/sdd/task-13-repair-report.md`; scoped re-review report: `.superpowers/sdd/task-13-rereview.md`.
- During implementation, only the two owned source/test files and `.superpowers/sdd/task-13-report.md` are writable; the report is excluded from the two-file source commit. During review, repair, and scoped re-review, only that role's explicitly named controller artifact is writable in addition to any role-authorized source repair files: the reviewer writes `.superpowers/sdd/task-13-review.md`, the repair role writes `.superpowers/sdd/task-13-repair-report.md`, and the scoped re-reviewer writes `.superpowers/sdd/task-13-rereview.md`. The dispatch brief is the reviewer's read-only input. All implementer, review, repair, and re-review reports must quote the locked Next.js bundle digest `c0c6d651bbce5f096a61cb72fa66c2080c39d1b4e6c18342295d7e5974cf3af2`.
- Completion requires: exact validation of the locked bundle digest; an observed matrix RED result before production implementation; focused GREEN; Ruff format/check, compilation, and scoped diff evidence; self-review of every frozen matrix ID and test node; the exact two-file source commit with the stated subject; the implementer report; one complete independent verdict; and then either clean approval or one repaired scoped approval. Any other terminal result is design escalation.

## Independent finite review and stop conditions

Require exactly one complete independent `gpt-5.6-sol/xhigh` review. The reviewer must not edit production code, delegate, or expand the frozen matrix; it must report every blocking finding in one batch with a stable finding ID, violated matrix ID/invariant, reproducer, actual result, required result, and rationale. The original implementer receives at most one repair assignment for that complete batch. The same reviewer then performs one scoped re-review of only those finding IDs and directly caused regressions within the frozen matrix; no exploratory second review follows. If that repair does not close the batch, stop for design escalation.

Stop without editing any shared path if the base/branch changes, the locked digest changes or fails validation, a matrix/corpus/lock change is requested, either owned file is no longer exclusive, or any registry/generated/integration/shared-file edit is required. Report that condition to the orchestrator; do not widen scope or create a substitute matrix.
