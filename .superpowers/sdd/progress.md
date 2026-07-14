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
