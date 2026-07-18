# Blocco D Coordinamento — Progress Ledger

## Task Status

- [ ] Task 1: Schema e Migrazione Presence (Backend) — BLOCKED: backend checkout missing
- [ ] Task 2: Eloquent Model e Service Presence (Backend) — BLOCKED: backend checkout missing  
- [ ] Task 3: Endpoint Presence Backend (Backend) — BLOCKED: backend checkout missing
- [ ] Task 4: Schema e Migrazione Code Claims (Backend) — BLOCKED: backend checkout missing
- [ ] Task 5: Service e Model Code Claims (Backend) — BLOCKED: backend checkout missing
- [ ] Task 6: Endpoint Code Claims (Backend) — BLOCKED: backend checkout missing
- [ ] Task 7: OpenAPI Schema (Backend) — BLOCKED: backend checkout missing
- [x] Task 8: Client Presence e Code Claims (Locale) — ✅ DONE (a3695bcdb, review clean)
- [x] Task 9: Heartbeat Non-Bloccante (Locale) — ✅ DONE (5b0f3987b, review clean)
- [x] Task 10: Claim Automatico nel Worker (Locale) — ✅ DONE (e5993ccab, review clean)
- [ ] Task 11: Validazione Messaggistica Persephone (Backend) — BLOCKED: backend checkout missing

## Completed Tasks

Task 8: complete (commits a3695bcdb, review clean)
Task 9: complete (commits 5b0f3987b, review clean)
Task 10: complete (commits e5993ccab, review clean)

## Summary

**All 3 local tasks (8-10) completed and reviewed clean.**

### Blocco D Locale Summary

**Task 8 — Client Methods (a3695bcdb)**
- 5 nuovi metodi per presence e code-claims nel client Python
- 50/50 test PASSED (client + OpenAPI coverage tests)
- Zero breaking changes

**Task 9 — Heartbeat Loop (5b0f3987b)**
- HadesCoordination class con heartbeat non-bloccante background
- 14 test cases: thread-safety, error handling, graceful shutdown
- All PASSED — concurrency verified

**Task 10 — Claim Wrapper (e5993ccab)**
- claim_and_run() method with finally semantics
- 6 test cases: CRITICAL finally-block test PASSED
- Release guaranteed even if runner raises exception

### Backend Tasks (1-7, 11) Status

Backend Laravel non trovato nel repo locale. All 8 backend tasks marked BLOCKED.

**Implementer can resume backend tasks when Laravel backend is available.**

---

**Status:** ✅ All completable tasks (local layer 8-10) DONE, all reviews CLEAN
**Next:** Final whole-branch review + finish-a-development-branch

---

# Hades Delegation + Distributed Orchestration — Progress Ledger

Plans:
- `docs/superpowers/plans/2026-07-10-hades-delegation-onboarding.md`
- `docs/superpowers/plans/2026-07-10-hades-distributed-orchestration.md`

Start commit: `3ef5f91d848393bac5332926db44eba9a402c612`

## Delegation onboarding

- [x] D1: reviewer runtime role — `0dfd6e563`, review approved; Minor: public docstring still lists only leaf/orchestrator
- [x] D2: orchestrator task contracts — `fbc3663be`, review approved; Minor: direct Python tuples accepted for list fields
- [x] D3: adaptive capacity — `0453f5182` + `c205a3326`, re-review clean after batch/nesting fixes
- [x] D4: evidence packets — `664fca494` + `0204f8d8a` + `81914d3fe` + `dbabeb077`, final review clean
- [x] D5: model recommendations — `4861e5f0c` + `c41b99793`, re-review clean after metadata provenance/authentication fixes
- [x] D6: delegation CLI onboarding — `fe663204d`, review clean; 206 delegation regressions passed
- [x] D7: bundled skill guidance — `77e35f0b8` + `c1ea781e3`, re-review clean; 220 regressions passed

## Distributed orchestration

- [x] O1: Persephone envelope contract — `339354917` + `7c3657348` + `563a7d4d7`, final review clean; 85 regressions passed
- [x] O2: durable queue store — `854d9fc4c` + `0836dfa1d`, re-review clean after atomic response/global identity fixes
- [x] O3: SSE/poll transport — `857c5e4e4` + `2ddc0db17` + `3cf989f29` + `5ce7aaf2e`, final review clean; 170 scoped tests
- [x] O4: multi-project receiver — `aa71c8b08` + `eac5251c0` + `f795ea6f2` + `e307e7f37`, final review clean
- [x] O5: information-only worker — `8ded8da1d` through `fa67cd18b`, final adversarial review clean; 386 broad tests
- [x] O6: service lifecycle — `d340e7c6e` through `1d3801810`, final review clean; 507 broad tests
- [x] O7: DAG/blackboard wakeups — `d48653be1` through `72bff3451`, final adversarial review clean; 776 broad tests
- [x] O8: remote Kanban projection — `fac303076` through `8e4e525d5`, final review clean; 593 broad tests
- [x] O9: full verification and live Hades skill test — 586 focused tests; managed runtime refreshed; skill reload/invocation and non-mutating PTY prompt verified

## Additional diagnosis

- [x] B401: diagnose repeated backend sync HTTP 401 responses — three stale linked bindings × memory/inbox/jobs = nine 401s; current identity valid
- [x] B401-FIX: all automatic sync paths scoped to current identity — `32d52cd0b` + `89b993626` + `1e725dceb`; live turn added zero sync errors

## Final review

- [x] Opaque Persephone cursor replay cannot rewind an existing cursor — `056e096f2`; 371 distributed tests passed
- [x] Whole-branch review completed; no remaining critical/important findings after final fixes

---

# Graph Lifecycle v2 — Subagent-Driven Execution Ledger

- Plan set: `docs/superpowers/plans/2026-07-16-graph-lifecycle-v2-master.md`
- Current component plan: `2026-07-16-graph-lifecycle-v2-01-contract-indexer-agent.md`
- Repository: `/Users/gabriele/Dev/Hephaistos/.worktrees/graph-lifecycle-v2-agent`
- Branch: `codex/graph-lifecycle-v2-agent`
- Merge base / starting HEAD: `78a664ebeb0c99a282858bb43d6af94c83ffdd1c`
- Baseline: full runner started successfully after creating `.venv`; pre-existing failures recorded below. Task-specific tests must not add failures.

## Known pre-existing baseline failures (2026-07-16)

- `tests/acp/*`, `tests/acp_adapter/*`: optional `acp` package absent from the declared `dev` extra.
- `tests/agent/test_anthropic_adapter.py`: 3 credential/environment-sensitive failures.
- `tests/cli/test_cli_status_command.py`: stale `Hermes CLI Status` expectation; runtime emits rebranded `Hades CLI Status`.
- `tests/gateway/test_api_server_toolset.py`: expects optional `image_generate` in a resolved toolset.
- `tests/gateway/test_background_command.py`: macOS `/tmp` versus `/private/tmp` path expectation.
- `tests/gateway/test_dingtalk.py`: one pre-existing payload-title assertion failure.
- Baseline run was stopped at 22.3% after 10 failures and 8,153 passing tests; no graph-v2 task files existed yet.

## Task ledger

### Plan 1 / Task 1 — Create Closed Root Schemas and Golden Fixtures

- Status: complete

- Base: `78a664ebeb0c99a282858bb43d6af94c83ffdd1c`
- Brief: `.superpowers/sdd/task-1-brief.md`
- Implementer report: `.superpowers/sdd/task-1-report.md`
- Commits:
  - `e213b7ad7613ec31278047f80a10c4db99389d70` — `feat(hades): freeze graph lifecycle v2 contracts`
  - `3857e09aa08c3a1fee566951f1ecc032ea1ae139` — `fix(hades): close graph v2 contract invariants`
  - `cbc07d447ad301a53468ad52093438cfe0160d1d` — `fix(hades): bind graph resolution semantics`
- RED: inventory test failed with expected `FileNotFoundError` for missing `contracts/hades/graph-v2/manifest.json`
- GREEN: 21 focused tests passed; 63-test focused regression passed; schema/manifest/golden/Ruff/diff checks clean
- Review: final re-review approved; 0 Critical, 0 Important, 0 Minor; 818 references resolved
- Residual risk: JSON Schema cannot distinguish lexical `1` from `1.0`; raw-wire negative golden + strict application validation is required across runtimes

### Plan 1 / Task 2 — Implement Canonicalization, Identity, and Schema Facade

- Status: complete
- Base: `cbc07d447ad301a53468ad52093438cfe0160d1d`
- Brief: `.superpowers/sdd/task-2-brief.md`
- Implementer report: `.superpowers/sdd/task-2-report.md`
- Commits:
  - `2fc545e91629cbb500c5229fc6d618b3b6d08864` — `feat(hades): implement graph v2 canonical identity`
  - `2b73ee6d9c816328b924910be764c1ebdb7d0022` — `fix(hades): package and harden graph v2 contract`
- RED: v2 package/exports absent as expected; focused selection 3 failed + 1 unrelated legacy test passed
- GREEN: 82 contract/golden/packaging tests, 2 wheel+sdist E2E, and 5 index-enrichment tests passed; Ruff/format/compile/diff clean
- Review: final re-review approved; 0 Critical, 0 Important, 0 Minor
- Residual risk: one v1-only `hades_index` importer intentionally fails until its planned producer migration

### Plan 1 / Task 3 — Implement Frozen Artifact Models, Coverage, and Validation

- Status: implementation complete; review in progress
- Base: `2b73ee6d9c816328b924910be764c1ebdb7d0022`
- Brief: `.superpowers/sdd/task-3-brief.md`
- Implementer report: `.superpowers/sdd/task-3-report.md`
- Commit: `e7cc65c3ff468bd8cb8e7ae09d64ec91b0a34df0` — `feat(hades): validate graph v2 artifact invariants`
- RED: 11/11 focused tests failed because model/coverage/validation modules were absent; later explicit-null, structure-cycle, and unmatched-return probes failed as intended
- GREEN: 78 graph-v2 contract/golden tests passed; Ruff/format/compile/diff clean; broader audit 442 passed with 46 known unrelated cutover/stale-output failures
- Review round 1: needs fixes; `/root/plan1_task3_review` found 0 Critical, 7 Important semantic gaps and 1 Minor test-coverage gap; findings recorded in `.superpowers/sdd/task-3-review.md`
- Repair: complete in `a90d747b1` (`fix(hades): close graph v2 semantic validation`); 17 focused regressions and 95 contract+golden tests passed; Ruff/format/compile/diff clean; report `.superpowers/sdd/task-3-repair-report.md`
- Review round 2: needs fixes; `/root/plan1_task3_review` found 0 Critical, 6 Important and 1 Minor; report `.superpowers/sdd/task-3-review-round2.md`; the five round-1 findings are closed
- Repair round 2: complete in `5c2f67dfe` (`fix(hades): close graph v2 coverage and flow closure`); 8 new focused regressions + a partial-flow characterization and 104 contract+golden tests passed; Ruff/format/compile/diff clean; report `.superpowers/sdd/task-3-repair2-report.md`
- Review round 3: needs fixes; `/root/plan1_task3_review` found 0 Critical, 3 Important, 0 Minor in `.superpowers/sdd/task-3-review-round3.md`; four round-2 findings fully repaired, lifecycle closure and entrypoint display partially repaired; SCC/bitset performance review clean
- Repair round 3: complete in `2c4f11fd8` (`fix(hades): close graph v2 lifecycle semantics`); 6 focused regressions and 109 contract+golden tests passed; Ruff/format/compile/diff clean; report `.superpowers/sdd/task-3-repair3-report.md`
- Review round 4: approved; `/root/plan1_task3_review` found 0 Critical, 0 Important, 0 Minor; 109 combined tests plus Ruff/format/compile/diff clean; report `.superpowers/sdd/task-3-review-round4.md`
- Status: complete
- Residual risk: legacy finalizer migration remains deferred; validation/model files are large because they encode the complete closed contract

### Plan 1 / Task 4 — Add Typed Graph Configuration and Source Snapshot Identity

- Status: implementation complete; review in progress
- Base: `2c4f11fd82753269c5a703f7de69493a11a68aa0`
- Brief: `.superpowers/sdd/task-4-brief.md`
- Scope: strict `hades.graph_index` configuration, compiled secret/exclusion baseline, deterministic source inventory, Git metadata, and before/after snapshot protection
- Commit: `c245867665fca424dc2e160f83f3d380173ad61b` — `feat(hades): add graph v2 source identity and config`
- GREEN: 16 focused config/source tests, 26 config+source-mutation tests, config regressions, Ruff/compile/diff clean; report `.superpowers/sdd/task-4-report.md`
- Review round 1: needs fixes; 1 Critical, 2 Important, 1 Minor recorded in `.superpowers/sdd/task-4-review.md`
- Repair: complete in `c3986162f` (`fix(hades): close graph v2 source scope`); 195 scoped config/job regressions passed; Ruff/compile/diff clean; report `.superpowers/sdd/task-4-repair-report.md`
- Review round 2: needs fixes; 0 Critical, 2 Important, 1 Minor in `.superpowers/sdd/task-4-review-round2.md`; original four findings resolved
- Repair round 2: complete in `1c10b0828` (`fix(hades): close graph v2 submodule identity`); 200 config/job regressions passed; Ruff/compile/diff clean; report `.superpowers/sdd/task-4-repair2-report.md`
- Review round 3: needs fixes; 0 Critical, 1 Important, 0 Minor in `.superpowers/sdd/task-4-review-round3.md`; non-empty submodule and all other scope/config findings are repaired
- Repair round 3: complete in `812948e27` (`fix(hades): recognize empty graph submodules`); 201 config/job regressions passed; Ruff/compile/diff clean; report `.superpowers/sdd/task-4-repair3-report.md`
- Review round 4: approved; 0 Critical, 0 Important, 0 Minor; 201 combined config/job tests plus Ruff/compile/diff clean; report `.superpowers/sdd/task-4-review-round4.md`
- Status: complete

### Plan 1 / Task 5 — Create and Validate the Language-Neutral Extraction IR

- Status: implementation complete; review in progress
- Base: `812948e27c055b37f76acc5e7ad0032a507e6c3f`
- Brief: `.superpowers/sdd/task-5-brief.md`
- Scope: frozen closed adapter IR, typed local-key/reference validation, and v2-only adapter entrypoint contract
- Commit: `98c81c819` — `feat(hades): define lifecycle extraction IR`
- GREEN: 19 lifecycle IR tests, 24 with adjacent enrichment, imports/Ruff/format/compile/diff clean; legacy v1 finalizer migration remains deferred
- Review round 1: needs fixes; 0 Critical, 6 Important, 1 Minor in `.superpowers/sdd/task-5-review.md`
- Repair: complete in `c46f3441c` (`fix(hades): close lifecycle IR invariants`); 47 IR+enrichment tests passed; Ruff/format/compile/diff clean; report `.superpowers/sdd/task-5-repair-report.md`
- Review round 2: needs fixes; 0 Critical, 3 Important, 0 Minor in `.superpowers/sdd/task-5-review-round2.md`; original six Important and one Minor are closed
- Repair round 2: complete in `1f1d46f57` (`fix(hades): close nested lifecycle IR closure`); 50 IR+enrichment tests passed; Ruff/format/compile/diff clean; report `.superpowers/sdd/task-5-repair2-report.md`
- Review round 3: needs fixes; 0 Critical, 1 Important, 0 Minor: ConditionIR rejected safe identifier/operator comparisons containing sensitive names
- Repair round 3: complete in `aa7dc1a28` (`fix(hades): preserve redacted lifecycle conditions`); 53 IR+enrichment tests passed; Ruff/format/compile/diff clean; report `.superpowers/sdd/task-5-repair3-report.md`
- Review round 4: approved; 0 Critical, 0 Important, 0 Minor; 53 focused tests clean; report `.superpowers/sdd/task-5-review-round4.md`
- Status: complete

### Plan 1 / Task 6 — Parse Control Flow and Resolve Interprocedural Targets

- Status: complete
- Base: `aa7dc1a28`
- Scope: bounded CFG conversion, exact/candidate/unresolved call resolution, and optional non-authoritative Graphify hints
- Commit: `70303871c` — `feat(hades): extract bounded control flow and call targets`
- GREEN: 61 Task6+IR+enrichment tests, 170 graph-v2 audit; Ruff/format/compile/diff clean; report `.superpowers/sdd/task-6-report.md`
- Review round 1: needs fixes; 0 Critical, 4 Important, 1 Minor in `.superpowers/sdd/task-6-review.md`
- Repair: complete in `da108642b` (`fix(hades): harden control flow resolution`); 72 Task6+IR+enrichment tests and 181 graph audit tests passed; Ruff/format/compile/diff clean; report `.superpowers/sdd/task-6-repair-report.md`
- Review round 2: needs fix; 0 Critical, 1 Important, 0 Minor: an ambiguous same-file name incorrectly prevented a later uniquely typed receiver proof from being exact
- Repair round 2: complete in `d036e5cc5` (`fix(hades): refine call resolution precedence`); 76 Task6+IR+enrichment tests and 185 graph audit tests passed; Ruff/format/compile/diff clean; report `.superpowers/sdd/task-6-repair2-report.md`
- Review round 3: approved; 0 Critical, 0 Important, 0 Minor; 76 focused tests, 185 audit tests, and static/diff checks clean; report `.superpowers/sdd/task-6-review-round3.md`

### Plan 1 / Task 7 — Freeze the Framework Adapter Interface and Generic Entrypoints

- Status: complete
- Base: `d036e5cc5`
- Brief: `.superpowers/sdd/task-7-brief.md`
- Scope: framework-neutral adapter protocol, registered framework execution, and generic entrypoint detection without language fallback
- Commit: `ca9bd2814` — `feat(hades): define framework lifecycle adapter boundary`
- RED: module absent (`ModuleNotFoundError`); additional source-file-missing probe reproduced `FileNotFoundError`
- GREEN: 8 framework-adapter tests and 79 framework-adapter+IR+CFG audit tests passed; source-file failure is typed as `partial/file_read_failed`; Ruff/format/compile/diff clean
- Review round 1: needs fix; 0 Critical, 1 Important, 0 Minor: `extract_languages_entrypoints()` filtered syntax but not the registry, so a global adapter could run for an unrelated selected language with empty syntax; report `.superpowers/sdd/task-7-review.md`
- Repair: complete in `e6599a4a3` (`fix(hades): scope framework adapters by language`); regression proves a Python pass cannot invoke a PHP/Symfony adapter, and Rust invokes none; 9 focused and 80 audit tests passed; Ruff/format/compile/diff clean
- Review round 2: needs fix; 0 Critical, 1 Important, 0 Minor: in a multi-language pass an in-scope adapter could still receive no matching syntax and execute `detect()`; report `.superpowers/sdd/task-7-review-round2.md`
- Repair round 2: complete in `020fcaaf5` (`fix(hades): skip framework adapters without syntax`); composite TypeScript+JavaScript regression proves a JavaScript adapter has zero calls when only TypeScript syntax is present; 10 focused and 81 audit tests passed; Ruff/format/compile/diff clean
- Review round 3: approved; 0 Critical, 0 Important, 0 Minor; both language-scope and relevant-syntax checks occur before `detect()` including the direct runner; 10 focused and 81 audit tests plus static/diff checks clean; report `.superpowers/sdd/task-7-review-round3.md`

### Plan 1 / Task 8 — Implement the Symfony Lifecycle Adapter

- Status: complete
- Base: `020fcaaf5`
- Brief: `.superpowers/sdd/task-8-brief.md`
- Scope: deterministic Symfony detection, entrypoints, and request pipeline with explicit uncertain boundaries
- Commit: `eef652cc9` — `feat(hades): extract Symfony request lifecycles`
- RED: adapter absent; PHP route-import and PHP service-listener cases then independently reproduced missing extraction
- GREEN: 8 Symfony tests and 89 Symfony+framework-adapter+IR+CFG audit tests passed; Ruff/format/compile/diff clean
- Review round 1: blocked; 1 Critical and 8 Important: absolute import can escape workspace; false exact handler/route/response/security facts; valid repeated imports lost; listener/subscriber ordering and exception topology wrong; malformed config lacks typed partial coverage. Report `.superpowers/sdd/task-8-review.md`
- Repair: complete in `878b76065` (`fix(hades): harden Symfony lifecycle extraction`); all C1/I1-I8 repros are regression-covered, including registry propagation of typed coverage; 18 Symfony and 99 aggregate audit tests passed; Ruff/format/compile/diff/show checks clean
- Review round 2: blocked; 0 Critical, 3 Important, 1 Minor: computed metadata still crashes/defaults exactly, FQCN resolution lacks namespace proof, unregistered subscribers/unreferenced voters become exact stages, and coverage propagation is an undeclared duck-typed extension. Report `.superpowers/sdd/task-8-review-round2.md`
- Repair round 2: complete in `f2ab44655` (`fix(hades): close Symfony lifecycle certainty gaps`); 38 Symfony+adapter tests and 280 nine-module Hades audit tests passed; typed coverage is formal protocol surface; Ruff/format/compile/diff clean
- Review round 3: blocked; 0 Critical, 1 Important, 0 Minor: PHP route/import invocations whose primary argument is dynamic miss literal regexes and produce neither a candidate nor partial coverage. Report `.superpowers/sdd/task-8-review-round3.md`; reviewer confirms current nine-module collection is 272 tests
- Repair round 3: complete in `e0257f72d` (`fix(hades): surface dynamic PHP route coverage`); three dynamic primary-argument regressions emit partial coverage while literal add/import remain diagnostic-free; 41 focused and 275 nine-module audit tests passed; Ruff/format/compile/diff clean
- Review round 4: approved; 0 Critical, 0 Important, 0 Minor; primary dynamic forms emit exactly one partial coverage and no candidate while literal calls remain clean; all prior C1/I1-I8/M1 reverified; 41 focused and 275 audit tests plus static/diff checks clean; report `.superpowers/sdd/task-8-review-round4.md`

### Plan 1 / Task 9 — Implement the Laravel Lifecycle Adapter

- Status: complete
- Base: `e0257f72d`
- Brief: `.superpowers/sdd/task-9-brief.md`
- Scope: deterministic Laravel route/middleware/security lifecycle extraction with explicit linked async flows
- Commit: `f6673a113` — `feat(hades): extract Laravel request lifecycles`
- RED: adapter absent (`ModuleNotFoundError`)
- GREEN: 13 Laravel tests and 125 Laravel+Symfony+framework-adapter+IR+CFG audit tests passed; Ruff/format/compile/diff clean
- Review round 1: blocked; 0 Critical, 9 Important: fluent pre-verb route chains omitted; `apiResource` over-expands; terminable middleware, controller applicability, handler method binding, sync dispatch, unresolved async/cycles, policy/gate, and console/scheduler semantics are incorrect or unreported. Report `.superpowers/sdd/task-9-review.md`
- Repair: complete in `a95998c69` (`fix(hades): harden Laravel lifecycle extraction`); all nine repro classes plus missing-controller coverage are regression-covered; 24 Laravel and 136 aggregate lifecycle audit tests passed; Ruff/format/compile/diff clean; report `.superpowers/sdd/task-9-repair-report.md`
- Review round 2: blocked; 0 Critical, 4 Important: resource parameter singularization is wrong, response/exception shortcuts bypass terminable middleware, handler FQCN remains short-name guess, Gate booleans are false terminals, and scheduler trigger value cannot currently represent its frequency. Report `.superpowers/sdd/task-9-review-round2.md`
- Repair round 2: complete in `3f8498e0f` (`fix(hades): close Laravel lifecycle contract gaps`); resource, response termination, namespace proof, gate topology, and required scheduled trigger value are regression-covered; schema source/mirror/lock updated together; 231 focused graph/lifecycle tests passed; Ruff/format/compile/diff clean; report `.superpowers/sdd/task-9-repair-round2-report.md`
- Review round 3: blocked; 0 Critical, 4 Important: broad resource inflection still fabricates paths, redirect/abort outcomes are erased, unrendered throws become normal responses, and lock provenance remains at the superseded schema commit. Report `.superpowers/sdd/task-9-review-round3.md`
- Repair round 3: complete in `c82bb393b` (`fix(hades): preserve Laravel lifecycle outcomes`) and metadata-only `e0365a87a` (`chore(hades): advance graph contract provenance`); resource/outcome/exception regressions and lock provenance are covered; schema/mirror/manifest digests unchanged; 234 aggregate tests passed; Ruff/format/compile/diff clean; report `.superpowers/sdd/task-9-repair-round3-report.md`
- Review round 4: blocked; 0 Critical, 1 Important, 0 Minor: a direct unconditional throw has an exception arm but still retains an asserted normal `handler -> response` successor. Report `.superpowers/sdd/task-9-review-round4.md`
- Repair round 4: complete in `181420499` (`fix(hades): classify Laravel throw completion`); direct throws have no verified normal response edge and conditional throws cross typed normal-completion uncertainty; schema/mirror/manifest/lock unchanged; 235 aggregate tests passed; Ruff/format/compile/diff clean; report `.superpowers/sdd/task-9-repair-terminal-report.md`
- Review round 5: approved; 0 Critical, 0 Important, 0 Minor; direct/conditional throws and controlled outcomes reverified, contract provenance remains exact; 235 audit tests plus Ruff/format/compile/diff clean; report `.superpowers/sdd/task-9-review-round5.md`

### Plan 1 / Task 10 — Implement the Django Lifecycle Adapter

- Status: complete
- Base: `181420499`
- Brief: `.superpowers/sdd/task-10-brief.md`
- Scope: deterministic Django URL/middleware/view lifecycle extraction, with boundaries for dynamic settings and routes
- Commit: `11ca411b7` — `feat(hades): extract Django request lifecycles`
- RED: module absent, yielding expected collection failure
- GREEN: 6 Django tests and 81 Django+adapter+lifecycle audit tests passed; Ruff/format/compile/diff clean
- Review round 1: blocked; 0 Critical, 5 Important: real Tree-sitter Python symbols cannot bind dotted views, same-name CBVs mix declarations, helper files become commands, short-circuit unwinds unentered middleware, and unrelated try/except falsely handles raises. Report `.superpowers/sdd/task-10-review.md`
- Repair: complete in `6e94a8a77` (`fix(hades): bind Django lifecycle targets exactly`); all five repro classes are regression-covered using actual Tree-sitter syntax naming; 12 Django and 158 lifecycle audit tests passed; Ruff/format/compile/diff clean
- Review round 2: blocked; 0 Critical, 3 Important, 0 Minor: nested helper returns fabricate middleware shortcut, arbitrary local/unresolved `login_required` emits access denial, and subclass exceptions are falsely unhandled. Report `.superpowers/sdd/task-10-review-round2.md`
- Repair round 2: complete in `1552b57bf` (`fix(hades): make Django lifecycle facts conservative`); lexical return traversal, exact decorator imports, and bounded exception ancestry are regression-covered; 15 Django and 161 lifecycle audit tests passed; Ruff/format/compile/diff clean
- Review round 3: blocked; 0 Critical, 1 Important, 0 Minor: imports remain trusted after visible rebinding, so a shadowed Django decorator/URL alias can fabricate a framework fact. Report `.superpowers/sdd/task-10-review-round3.md`
- Repair round 3: complete in `ec77715cc` (`fix(hades): expire rebound Django imports`); source-order binding scopes preserve prior imports but invalidate post-rebind decorator/URL facts to partial coverage; 17 Django and 163 lifecycle audit tests passed; Ruff/format/compile/diff clean
- Review round 4: blocked; 0 Critical, 2 Important, 0 Minor: conditional rebinding leaves stale decorator/URL imports trusted, and sequential `urlpatterns =` assignments are accumulated instead of replaced. Report `.superpowers/sdd/task-10-review-round4.md`
- Repair round 4: complete in `ad4ac42fd` (`fix(hades): model Django source-order uncertainty`); executable control-flow rebindings invalidate later imports and URL assignment/replacement semantics are regression-covered; 21 Django and 167 lifecycle audit tests passed; Ruff/format/compile/diff clean
- Review round 5: blocked; 0 Critical, 1 Important, 0 Minor: `Import`/`ImportFrom` rebinding `urlpatterns` retains stale static routes instead of invalidating to partial coverage. Report `.superpowers/sdd/task-10-review-round5.md`
- Repair round 5: complete in `07d90e0e2` (`fix(hades): treat imported urlpatterns as opaque`); explicit local import aliases invalidate stale route lists while unrelated imports remain inert; 23 Django and 169 lifecycle audit tests passed; Ruff/format/compile/diff clean
- Review round 6: blocked; 0 Critical, 1 Important, 0 Minor: `urlpatterns.extend()`/`.append()` silently omit routes without partial coverage. Report `.superpowers/sdd/task-10-review-round6.md`
- Repair round 6: complete in `114cdcd36` (`fix(hades): model Django URL pattern mutations`); literal append/extend preserve proven order and unsupported exact mutations invalidate to partial coverage; 26 Django and 172 lifecycle audit tests passed; Ruff/format/compile/diff clean
- Review round 7: approved; 0 Critical, 0 Important, 0 Minor; literal/dynamic URL mutations and all six earlier repair contracts reverified; 26 Django and 172 audit tests plus static/diff checks clean; report `.superpowers/sdd/task-10-review-round7.md`

### Plan 1 / Task 11 — Implement the FastAPI Lifecycle Adapter

- Status: superseded — implementation complete; review blocked (terminally closed below)
- Base: `114cdcd36`
- Brief: `.superpowers/sdd/task-11-brief.md`
- Scope: FastAPI/Starlette route, dependency, middleware, exception, lifespan, and background lifecycle extraction
- Commit: `001a5b8f9` — `feat(hades): extract FastAPI request lifecycles`
- GREEN: 7 FastAPI tests and 179 aggregate lifecycle tests passed; compile/diff clean
- Review round 1: blocked; 1 Critical, 6 Important: duplicate include-router pipeline keys abort adapter execution; default router prefix, method defaults, lifespan/event exclusivity, exception handler specificity, response annotation serialization, and conditional registration coverage are incorrect or incomplete. Report `.superpowers/sdd/task-11-review.md`
- Repair round 1: complete in `5842337c2` (`fix(hades): close FastAPI lifecycle semantics`); 17 focused and 189 aggregate lifecycle tests passed; Ruff/format/compile/diff clean; report `.superpowers/sdd/task-11-repair-report.md`
- Review round 2: blocked; 0 Critical, 6 Important, 0 Minor: literal `lifespan=None`, plain Starlette route flavor, local exception aliases, imperative endpoint dependencies, quoted computed response annotations, and dynamic expression/rebinding coverage remain incorrect or incomplete. Report `.superpowers/sdd/task-11-review-round2.md`
- Repair round 2: complete in `1012bae0c` (`fix(hades): harden FastAPI lifecycle proof`); 25 focused and 197 aggregate lifecycle tests passed; Ruff/format/compile/diff clean; report `.superpowers/sdd/task-11-repair-report.md`
- Review round 3: blocked; 0 Critical, 3 Important, 0 Minor: exception alias identity is not occurrence-sensitive, comprehension assignment expressions bypass app invalidation, and imported app alias rebindings can lose registrations without partial coverage. Report `.superpowers/sdd/task-11-review-round3.md`
- Repair round 3: complete in `4c371db93` (`fix(hades): close FastAPI occurrence semantics`); 29 focused and 201 aggregate lifecycle tests passed; Ruff/format/compile/diff clean; report `.superpowers/sdd/task-11-repair-report.md`
- Review round 4: blocked; 0 Critical, 4 Important, 0 Minor: runtime exception alias lookup, redefined class object identity, occurrence-sensitive framework imports, and module-executed function/class header expressions remain unsafe. Report `.superpowers/sdd/task-11-review-round4.md`
- Repair round 4: complete in `7c41c45a7` (`fix(hades): bind FastAPI source-order identities`); 33 focused and 205 aggregate lifecycle tests passed; Ruff/format/compile/diff clean; report `.superpowers/sdd/task-11-repair-report.md`
- Review round 5: blocked; 0 Critical, 6 Important, 0 Minor: cross-file include snapshots, executable class-body globals, exception class header/decorator effects, occurrence-sensitive non-owner targets, local framework-object provenance, and nested-scope yield/background discovery remain unsafe. Report `.superpowers/sdd/task-11-review-round5.md`
- Repair round 5: complete in `87243cb4d` (`fix(hades): bound FastAPI registration snapshots`); 39 focused and 211 aggregate lifecycle tests passed; Ruff/format/compile/diff clean; report `.superpowers/sdd/task-11-repair-report.md`
- Review round 6: blocked; 0 Critical, 9 Important, 0 Minor: runtime background binding/provenance, lifespan occurrence binding, exception/class-global siblings, annotated construction, future-binding false partials, cyclic/per-app snapshots, middleware scope, and dependency declaration/cache proof remain unsafe. Report `.superpowers/sdd/task-11-review-round6.md`
- Repair round 6: complete in `14e800b25` (`fix(hades): prove FastAPI runtime bindings`); 48 focused and 220 aggregate lifecycle tests passed; Ruff/format/compile/diff clean; report `.superpowers/sdd/task-11-repair-report.md`
- Review round 7: blocked; 0 Critical, 7 Important, 0 Minor: rebound BackgroundTasks receivers, middleware continuation proof, computed Annotated metadata, Security scope cache identity, Response return serialization, explicit metaclass identity, and imperative/constructor legacy event registrations remain unsafe. Report `.superpowers/sdd/task-11-review-round7.md`
- Repair round 7: complete in `14ee5de6d` (`fix(hades): close FastAPI runtime lifecycle gaps`); 55 focused and 227 aggregate lifecycle tests passed; Ruff/format/compile/diff clean; report `.superpowers/sdd/task-11-repair-report.md`
- Review round 8: blocked; 0 Critical, 7 Important, 0 Minor: annotated non-background rebinding, destructured middleware aliases, dependency marker/type aliases, use-cache write semantics, inherited metaclass provenance for Response/exception identity, and router constructor events remain unsafe. Report `.superpowers/sdd/task-11-review-round8.md`
- Repair round 8: complete in `c36436f2d` (`fix(hades): close FastAPI binding siblings`); 62 focused and 234 aggregate lifecycle tests passed; Ruff/format/compile/diff clean; report `.superpowers/sdd/task-11-repair-report.md`
- Review round 9: blocked; 0 Critical, 5 Important, 0 Minor: destructured background receiver provenance, middleware continuation storage escape, conditional/cross-module dependency aliases, filename-order-dependent exception identity, and router lifespan expansion remain unsafe. Report `.superpowers/sdd/task-11-review-round9.md`
- Repair round 9: complete in `f2a5dc708` (`fix(hades): close FastAPI transitive bindings`); 67 focused and 239 aggregate lifecycle tests passed; Ruff/format/compile/diff clean; report `.superpowers/sdd/task-11-repair-report.md`
- Review round 10: blocked; 0 Critical, 5 Important, 0 Minor: qualified/destructured dependency bindings, dictionary middleware semantics, package-relative re-exports, and included-router events under custom app lifespan remain unsafe. Report `.superpowers/sdd/task-11-review-round10.md`
- Repair round 10: complete in `1b07cbd8b` (`fix(hades): close FastAPI package bindings`); 72 focused and 244 aggregate lifecycle tests passed; Ruff/format/compile/diff clean; report `.superpowers/sdd/task-11-repair-report.md`
- Review round 11: blocked; 0 Critical, 5 Important, 0 Minor: opaque/cyclic qualified dependency namespaces, finite starred destructuring, nested middleware container mutation, wildcard package re-exports, and root/router lifespan event cardinality remain unsafe. Report `.superpowers/sdd/task-11-review-round11.md`
- Repair round 11: complete in `a64d223b7` (`fix(hades): close FastAPI lifespan composition`); 77 focused and 249 aggregate lifecycle tests passed; Ruff/format/compile/diff clean; report `.superpowers/sdd/task-11-repair-report.md`
- Review round 12: blocked; 0 Critical, 5 Important, 0 Minor: invoked middleware helper side effects, wildcard export mutations, relative submodule cycle reachability, per-level nested starred destructuring, and unrelated literal-key container mutation remain unsafe. Report `.superpowers/sdd/task-11-review-round12.md`
- Repair round 12: complete in `ebf3cd050` (`fix(hades): close FastAPI nested flow semantics`); 82 focused and 254 aggregate lifecycle tests passed; Ruff/format/compile/diff clean; report `.superpowers/sdd/task-11-repair-report.md`
- Review round 13: blocked; 0 Critical, 4 Important, 0 Minor: aliased helper calls, continuation-container alias mutation, aliased `__all__` mutation, and same-statement destructuring RHS ordering remain unsafe. Report `.superpowers/sdd/task-11-review-round13.md`
- Repair round 13: complete in `245184e52` (`fix(hades): close FastAPI alias semantics`); 86 focused and 258 aggregate lifecycle tests passed; Ruff/format/compile/diff clean; report `.superpowers/sdd/task-11-repair-report.md`
- Review round 14: blocked; 0 Critical, 5 Important, 0 Minor: destructured helper aliases, same-name helper redefinitions, chained continuation-container assignment, destructured `__all__` aliases, and walrus-ordered RHS loads remain unsafe. Report `.superpowers/sdd/task-11-review-round14.md`
- Repair round 14: complete in `72407cf44` (`fix(hades): preserve FastAPI alias occurrence semantics`); 91 focused and 263 aggregate lifecycle tests passed; Ruff/format/compile/diff clean; report `.superpowers/sdd/task-11-repair-report.md`
- Review round 15: blocked; 0 Critical, 6 Important, 0 Minor: nested helper runtime binding, helper walrus assignments, starred helper destructuring, nested `__all__` escape, finite public wildcard destructuring, and annotated walrus dependency bindings remain unsafe or over-bounded. Report `.superpowers/sdd/task-11-review-round15.md`
- Repair round 15: complete in `19da83b55` (`fix(hades): close FastAPI lifecycle sibling paths`); 97 focused and 269 aggregate lifecycle tests passed; Ruff/format/compile/diff clean; report `.superpowers/sdd/task-11-repair-report.md`
- Review round 16: blocked; 0 Critical, 6 Important, 0 Minor: nonlocal helper rebinding, local/module default-expression effects, finite starred helper/public destructuring, and `__all__` alias rebind handling remain unsafe or over-bounded. Report `.superpowers/sdd/task-11-review-round16.md`
- Repair round 16: complete in `cc6b4597a` (`fix(hades): preserve FastAPI finite binding semantics`); 103 focused and 275 aggregate lifecycle tests passed; Ruff/format/compile/diff clean; report `.superpowers/sdd/task-11-repair-report.md`
- Review round 17: blocked; 0 Critical, 6 Important, 0 Minor: invoked-helper internal ordering, eager local defaults/decorators/annotations, expression-ordered `__all__` rebinds, starred nested alias identity, and lazy lambda boundaries remain unsafe or over-bounded. Report `.superpowers/sdd/task-11-review-round17.md`
- Finite-gate rollout commits: `df6dd3ace` (validator), `4f55c00b6` (FastAPI matrix freeze), and `cbc44ebfb5a0c74d8e3f15f011fd6f359d45c1ca` (terminal acceptance evidence).
- Frozen acceptance bundle: digest `d1d9c8799e59bdd57855d89bce09b5900ff52f307ba2ae29cb064d98a64f8505`; corpus `tests/fixtures/hades/adapter_acceptance/fastapi/corpus.json`; matrix `tests/fixtures/hades/adapter_acceptance/fastapi/matrix.json`; lock `tests/fixtures/hades/adapter_acceptance/fastapi/lock.json`.
- Round 18: approved in `.superpowers/sdd/task-11-review-round18.md`; 0 Critical / 0 Important / 0 Minor, no backlog, no Task 4 repair, and Task 4 skipped.
- Controller evidence at candidate/terminal HEAD `cbc44ebfb5a0c74d8e3f15f011fd6f359d45c1ca`: canonical validator exit 0 (digest above); acceptance harness 11 passed, 0 failed; focused FastAPI 109 passed, 0 failed; aggregate `tests/hermes_cli/test_hades_lifecycle_*.py` 281 passed across 7 files, 0 failed; Ruff `All checks passed!`; `py_compile` exit 0 with no output; `git diff --check` exit 0 with no output.
- Measured debt: 7,188 production lines and 7,808 test lines; behavior-preserving complexity reduction remains deferred under the approved finite-gate design.
- Next: freeze and run the Express and Next.js matrix gates before their production implementation.
- Status: complete

### Finite adapter Task 7 — bounded parallel implementation handoff

- Shared implementation base: `06ed97dd50298a29cb209fecc3baa7ee995de062`.
- Express frozen bundle: `27b490ad91f90c45d226a451eb6e71e0b3ed525290250805878bbb53ef01c7ac`; brief: `.superpowers/sdd/task-12-brief.md`.
- Next.js frozen bundle: `c0c6d651bbce5f096a61cb72fa66c2080c39d1b4e6c18342295d7e5974cf3af2`; brief: `.superpowers/sdd/task-13-brief.md`.
- Status: ready for parallel implementation
