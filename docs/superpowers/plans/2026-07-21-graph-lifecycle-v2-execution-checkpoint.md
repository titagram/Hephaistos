# Graph Lifecycle v2 Execution Checkpoint — 2026-07-21

> **For agentic workers:** this is the only authoritative **execution-status**
> document for Graph Lifecycle v2. Read it completely before selecting a task.
> The approved specification and component plans remain authoritative for
> behavior and implementation details; historical ledgers are evidence only.

**Checkpoint status:** active

**Snapshot date:** 2026-07-21, Europe/Rome

**Scope of this checkpoint:** status reconciliation and execution handoff only.
No product code, runtime service, database, graph, container, migration, or
deployment was changed while producing it.

## 1. Authority Order

When two documents disagree, use this order:

1. Normative behavior:
   `docs/superpowers/specs/2026-07-16-graph-lifecycle-v2-design.md`.
2. Normative implementation sequence:
   `docs/superpowers/plans/2026-07-16-graph-lifecycle-v2-master.md` and its six
   component plans.
3. Current execution status, repository SHAs, blockers, and next-session
   ownership: this checkpoint.
4. Historical evidence only: `.superpowers/sdd/progress.md` and
   `docs/backend-agent-coordination.md`.

Checkboxes in the component plans are instructions, not proof of execution.
Do not infer completion from old v1 Wiki, graph, backup, or frontend features.

## 2. Milestone Vocabulary

The component plans never defined the conversational term `P0`. From this
checkpoint onward it has exactly this meaning:

| Milestone | Component plans | Exit condition |
|---|---|---|
| **P0 — Graph foundation** | Plan 1 producer + Plan 2 backend | Producer checkpoint C1 and backend checkpoint C2 are verified on exact SHAs; no open Critical/Important review finding |
| **P1 — Uncertainty reduction** | Plan 3 verification + Wiki + standalone Codex plugin | V01–V19 plus 90-day whole-chain retention verified end-to-end |
| **P2 — Human exploration** | Plan 4 React Graph Explorer | U01–U12, build, responsive and accessibility gates verified |
| **P3 — Recoverability** | Plan 5 backup + cutover tooling | Restic snapshot, restore rehearsal, scoped export/restore and runbook verified |
| **P4 — Isolated acceptance and promotion** | Plan 6 | isolated Symfony Demo/plugin/browser gates, explicit production-promotion decision, separately authorized scoped-v1 retirement decision, deployed-SHA verification |

`P0/P1 finding` in older review text means severity, not one of these
milestones. New reports must use `Critical`, `Important`, or `Minor` for review
severity.

## 3. Verified Repository Snapshot

### 3.1 Hades Agent on the Mac

| Field | Value |
|---|---|
| Repository | `/Users/gabriele/Dev/Hephaistos` |
| Branch | `codex/graph-v2-lifecycle-validation-performance` |
| Tested C1 implementation HEAD | `74c27506639195bb4ddff595821b312f4f1fdd6f` |
| C1 evidence commit | `f41b72525684eaf9a98a16dae31baac4af00e3eb` |
| `main` | `9f226f29269f270b0e4bd2e377d5dee6177a7902` |
| `origin/main` | `9f226f29269f270b0e4bd2e377d5dee6177a7902` |
| Branch-specific implementation | indexed `AdapterResult.validate()` plus deterministic direct operation-count regressions |

The old branch `codex/graph-lifecycle-v2-agent` stops at
`001a5b8f91c19fd9f5ba1cafbb6cf14a732764ea` and is 81 commits behind the
current implementation lineage. Do not resume or merge it; use the performance
branch named above.

Plan 1 Tasks 1–18 have implementation commits on `main`. The strict evidence
status is more nuanced:

- Tasks 1–11 and Task 15 have persisted implementation, test, and review
  evidence.
- Tasks 12–14 and 16–17 are implemented, but their task-level final reports or
  complete executor-ledger evidence are incomplete.
- `.codex-artifacts/graph-v2/agent-gates.json` records fresh G01–G14 passing
  at tested SHA `74c27506639195bb4ddff595821b312f4f1fdd6f`. G07 took
  `219.704411s`.
- Task 18 evidence is closed for that tested SHA by
  `.codex-artifacts/graph-v2/c1-evidence.json`, including the exact targeted
  and broad commands, timestamp, contract digest, fixture tree, and clean
  final spec/code-quality verdicts. The broad suite has one explicitly
  recorded unrelated parent-identical Persephone/OpenAPI failure; it is not
  claimed passing and was not repaired in C1.

No historical report was fabricated. Future refreshes must continue to use
fresh tested-SHA evidence rather than reconstructing missing old reports.

### 3.2 Backend on the server

| Field | Value |
|---|---|
| Workspace | `/home/ubuntu/dev-sandbox` |
| Backend | `/home/ubuntu/dev-sandbox/backend` |
| Branch | `codex/graph-lifecycle-v2-backend-task-5` |
| HEAD | `1735eacd01f03cbe559a7aee9631404e4b05643a` |
| Branch base | `148da33c897d38c76b3778c923f743498f2daa7a` (`main` and `origin/main` at Task 5 start) |
| Worktree | not clean: only `.superpowers/sdd/progress.md` and `ai-sandbox/logbooks/LOGBOOK_PROJECT.md` are modified evidence files; preserve them |
| Live runtime | not re-audited after Task 5; no live operation was authorized or reported by the Task 5 session |

Plan 2 implementation state across `main` and the active Task 5 branch:

| Task | Commit | Snapshot evidence | Status |
|---:|---|---|---|
| 1 — vendored contracts | `4e8009de` | manifest, lock, schema, and golden bytes match the Agent copy | implemented; current audit did not rerun tests |
| 2 — storage migrations | `31262c22` | five graph-v2 migrations `000100`–`000350` are `Ran`, batch 3 | implemented; L-gates open |
| 3 — resumable import | `eaa0ea58` | create/show/chunk/complete routes registered | implemented; L-gates open |
| 4 — two-pass leased validation | `148da33c` | four-run constants, leases, retry policy, encrypted single-try job, and reconcile schedule present | implemented; L-gates open |
| 5 — Neo4j schema and projection | `1735eacd` | 20 focused tests / 222 assertions and 182 regression tests / 1,111 assertions passed; Pint, PHP lint, and PHPStan passed | implemented but **not verified**: quality review has 1 Important and 1 Minor |
| 6–12 | — | not started by the bounded Task 5 session | not started |

No current live-availability conclusion is drawn from the Task 5 code/test
session; operational checks and L01–L15 remain separate later gates.

### 3.3 Backup evidence

The newest verified readable pre-Graph-v2 PostgreSQL dump is:

```text
/home/ubuntu/backups/devboard/devboard-before-graph-v2-20260720T220721Z.dump
size: 6,966,998 bytes
sha256: 04ca6796146744424b20e1f18aed7c74572a396dc796f862ce11338245e25f3f
```

No readable sidecar checksum was found. Restore has not been rehearsed, so this
is backup evidence but not P3 disaster-recovery acceptance. No live graph
initialization, migration, projection, purge, or restore may rely on it without
the separate backup/restore gate required by Plan 5.

## 4. Current Program Status

| Plan | Implementation status | Exit-gate status |
|---|---|---|
| Plan 1 — producer | all 18 tasks implemented | **C1 verified** at `74c27506639195bb4ddff595821b312f4f1fdd6f`; current evidence pack persisted |
| Plan 2 — backend | Tasks 1–4 implemented; Task 5 implemented but quality-blocked; Tasks 6–12 not started | C2/L01–L15 open; Task 5 has 1 Important and 1 Minor |
| Plan 3 — verification/Wiki/plugin | v2 plan not started | C3/V01–V19 open |
| Plan 4 — React Explorer | v2 plan not started | C4/U01–U12 open |
| Plan 5 — DR/cutover | v2 plan not started | C5 open |
| Plan 6 — live acceptance | not started | C6 and user acceptance open |

Therefore P0 is not closeable yet. Its remaining deliverables are the bounded
Task 5 repair, backend Tasks 6–12, and C2.

## 5. P0 Blocker Closure: Producer Validation Scalability

A real Carnovali sync ran for roughly twenty minutes and reached approximately
7 GB before the Mac restarted. This runtime observation was not represented by
the earlier synthetic green gates and opened a P0 blocker. The deterministic
C1 repair and evidence below close the producer validation blocker without
claiming that Carnovali itself has been rerun.

The investigation confirmed these repeated full scans in the pre-repair
`hermes_cli/hades_index/lifecycle/model.py::AdapterResult.validate()`:

- call sites scan all structures around line 2197: `O(C * S)`;
- successor validation repeatedly scans branch arms;
- `RETURNS_TO` validation scans call sites/scopes and all edges, reaching an
  `O(E^2)` case;
- unresolved facts scan all edges: `O(U * E)`.

Commit `74c27506639195bb4ddff595821b312f4f1fdd6f` added deterministic direct
operation-count regressions and replaced the repeated scans with indexed
lookups while preserving validation order, error codes, and error messages.
The closure therefore rests on behavioral/complexity evidence, not only on
wall-clock measurements.

After synthetic C1 closure and before any future Carnovali attempt:

- do not run a full Carnovali sync;
- do not recreate the removed Carnovali Docker stack merely for this work;
- use deterministic synthetic fixtures, then Symfony Demo as the bounded real
  fixture;
- monitor peak RSS and elapsed time explicitly before attempting Carnovali.

### 5.1 C1 closure evidence — 2026-07-21

```text
plan/task: Plan 1 C1 validation scalability and Task 18 evidence closure
repository and branch: /Users/gabriele/Dev/Hephaistos; codex/graph-v2-lifecycle-validation-performance
tested commit SHA: 74c27506639195bb4ddff595821b312f4f1fdd6f
contract-lock manifest digest: cfb469a60fd0c59a13ca254df5e60871c83ffa207f21e3d307ced6ade6233644
RED: 4,686 structure iterations at 64 call sites; remaining unresolved path reproduced 6,182 operations for 32 facts × 32-edge bucket
GREEN: 59 IR, 105 control-flow/framework/traversal, 90 Symfony/Laravel/Django, 150 FastAPI/Express/Next.js; all exit 0
regression: G01-G14 exit 0; broad Agent command 1,795 passed, 1 deselected, 1 unrelated parent-identical Persephone/OpenAPI failure
gate IDs covered: G01-G14, with G13 limited to the normative Python side
review verdict: spec Approved and code quality Approved; 0 Critical / 0 Important / 0 Minor after exactly two bounded repair cycles
residual risk: no Carnovali run and no live peak-RSS measurement; the unrelated Persephone/OpenAPI test remains outside C1
next unblocked task: bounded backend Plan 2 Task 5 quality repair; P0 remains open through backend Tasks 5-12 and C2
```

The complete machine-readable record is
`.codex-artifacts/graph-v2/c1-evidence.json`. C1 closure does not authorize a
Carnovali run: use a bounded real fixture and explicit RSS/elapsed monitoring
before any future production-scale attempt.

### 5.2 Backend Task 5 checkpoint — 2026-07-21

```text
plan/task: Plan 2 Task 5 Neo4j schema and Graph Lifecycle v2 projection
repository and branch: /home/ubuntu/dev-sandbox; codex/graph-lifecycle-v2-backend-task-5
implementation commit SHA: 1735eacd01f03cbe559a7aee9631404e4b05643a
base SHA: 148da33c897d38c76b3778c923f743498f2daa7a
GREEN: 20 focused tests / 222 assertions
regression: 182 tests / 1,111 assertions; Pint, PHP lint, and PHPStan pass
review verdict: specification Approved; code quality has 0 Critical / 1 Important / 1 Minor
Important: each artifact batch queries staged keys by an unindexed record_ordinal range and ORDER BY, which may repeatedly scan/sort the same kind and approach O(N^2/B)
bounded repair: look up the current artifact batch by its public IDs through the existing composite primary key, map rows by public_id, then validate ID, expected ordinal, and chunk index in original artifact order
Minor residual: schema verification checks expected Neo4j object names but not their complete definitions; allowed by the explicit Task 5 contract, deferred as pre-production hardening
worktree note: preserve the modified .superpowers/sdd/progress.md and ai-sandbox/logbooks/LOGBOOK_PROJECT.md evidence files; they were not part of implementation commit 1735eacd
live safety: no migration, schema initialization, projection, purge, restart, deploy, restore, or destructive Docker operation was run
next unblocked task: Task 5 Important repair only; Task 6 remains blocked
```

## 6. Plan Defects Found During Reconciliation

This cleanup corrected three documentation-only contradictions:

- review severity is now `Critical/Important/Minor`, so milestone `P0` is no
  longer confused with a `P0` finding;
- the master Plan 4 start gate now includes the Plan 3 verification
  badge/status DTO dependency already required by the component plan;
- Plan 6 now agrees with the master and Plan 1 Task 16: Tree-sitter and grammar
  wheels are base dependencies and sync performs no lazy/runtime install.

The full plan review identified the following forward-contract defects; the
2026-07-21 amendment resolves them without changing product code:

1. Plan 2 Task 6 queues verification reconciliation although the concrete
   verification implementation is introduced only in Plan 3.
2. Plan 2 Task 11 requires reachability through overlays, requests, and Wiki
   ledgers that Plan 3 has not created yet.

The original branch/merge instructions also no longer describe reality: Agent
Plan 1 and backend Plan 2 Tasks 1–4 are already on `main`. Do not rewrite or
revert published history; continue from current `main` on fresh feature
branches and record the deviation.

The Agent C1 session is closed. The initial backend Task 5 session is also
closed, but Task 5 itself remains quality-blocked by the Important recorded in
Section 5.2. The only authorized implementation session is its bounded repair.
**Do not start backend Task 6 until the Task 5 Important is closed.**

Approved amendment — Alternative A:

1. Plan 2 Task 6 emits one generic
   `CanonicalGraphV2ProjectionActivated` domain event after a successful CAS
   commit; it never imports or resolves the future verification service.
2. Plan 2 Task 11 defines a read-only
   `GraphArtifactReachabilityProvider` seam plus an aggregator and the concrete
   Plan 2 provider for projection/head, attempts, and contexts.
3. Plan 3 supplies the event listener and concrete reachability providers for
   verification requests/results/overlays and Wiki ledgers. The graph listener
   rereads the current projection head, the Wiki listener rereads the current
   revision, and cleanup rechecks reachability under its locks.

4. `GraphArtifactReferenceLock` is the single outer transaction for every
   reference writer and cleanup: sorted import advisory locks, sorted import
   rows, the Plan 3 scope advisory lock, then domain rows. Domain services
   consume the same per-invocation guard and never reacquire the lock.
5. Wiki verification uses exact new normalized revision/claim/import-reference
   storage and `WikiCurrentRevisionActivated`; Laravel listener registration is
   in the real `AppServiceProvider`, not a nonexistent `EventServiceProvider`.
6. Plan 3 Task 8 owns the missing 90-day whole-retry-chain cleanup and the
   external-reachability/concurrent-reference tests. Agent-side implementation
   is Tasks 9–14, standalone plugin is Task 15, and V01–V19 closure is Task 16.
7. Global Checkpoint C is execution checkpoint C2 and proves event emission plus
   only the Plan 2 reachability roots. Global Checkpoint D is execution
   checkpoint C3 and proves the Plan 3 listeners/providers, audit retention,
   and cleanup races.
8. Already-applied backend migrations `000100`–`000350` remain immutable. New
   coordination state, desired-import provenance, cleanup state, execution
   fencing, durable uncertainty locators, and retry-incarnation indexing are a
   forward-only `000375` upgrade tested from both the applied and fresh schema.
9. Validation/projection jobs have one broker try on dedicated `graph-v2`
   runtime (`1900 > 1800 > 1740`); four domain attempts are explicit rows with
   renewable 120-second fencing. Every projection retry has a fresh physical
   incarnation so late workers/cleaners cannot affect a same-version winner.
10. Graph contexts/handles/cursors are bound to a server-derived dashboard or
    Hades-device principal and physical projection ID. The Hades graph surface
    is v2 `POST graph/query` under existing read capability; old `GET
    graph/traverse` and local topology fallbacks are deleted.
11. Graph/Wiki completion prepares immutable evidence drafts before locks and
    performs only bounded DB work under reference→scope→head→domain locks.
    Same-digest replay repeats authorization and locking. Wiki certification
    freshness and identical-artifact overlay reuse retain explicit source/base
    provenance.
12. Verification has a ten-execution ceiling per generation, exact
    critical↔urgent boundary mapping, local same-profile/scope `filelock`, and
    a fresh bounded `AIAgent` specialist per work item. Direct Wiki verify is
    removed; queue completion is the only verification mutation.
13. The Codex integration is a separately versioned five-skill plugin that
    delegates to the installed CLI. The recurring backup keeps global graph
    maintenance active through PostgreSQL, Neo4j, artifact inventory, and
    manifest sealing. Live acceptance uses an isolated Symfony Demo fixture;
    Carnovali is a later optional scale gate.
14. Every Neo4j projection incarnation owns a permanent
    `CanonicalProjectionFence`. Projector batches lock it before bounded
    writes; cleanup changes `building→deleting`, deletes/proves zero, and
    leaves `retired` forever. A late owner cannot recreate an orphan even
    after its PostgreSQL projection row is deleted.
15. A verification specialist has a parent-monotonic 900-second hard deadline
    and 300-second meaningful-progress deadline. Heartbeats/log noise do not
    reset progress. Before child release it must enter a mandatory OS
    containment domain (Linux cgroup v2, Windows Job Object, or Darwin
    run-scoped Docker PID namespace) and acquire the backend specialist fence.
    Every exit tears down and proves that whole domain empty. A failed proof
    leaves the server fence `active|quarantined`, globally blocking claims and
    reclaim across hosts; the mode-0600 local marker is diagnostic only. The
    same invocation never reclaims expired work.
16. Human retry and reconciliation use one total order: optional outer
    reference lock, verification scope, exact projection head, ascending full
    request/work chain, then domain rows. Any pre-read/current-source change
    returns `verification_source_changed` and restarts only outside locks.
    Wiki revision/projection events are wake-ups that reread both current
    heads, so either delivery order converges and stale certification is not
    suppressed.
17. Plan 5 introduces a typed project/reason/owner/generation-bound
    `MaintenanceAuthority`, central guarded artifact mutations/inventory, and
    durable export, retirement, and restore state machines. Crash recovery
    rotates authority and fences old owners; ordinary maintenance-off cannot
    close a bound operation. Restore is forward-only and retains a v2 operator
    artifact until verified completion. Its exit gate is hermetic tooling only;
    production backup/rehearsal/export installation and execution move to Plan
    6 Task 10B after digest-bound P1.
18. Implementation workers never integrate/push. A genuinely fresh
    coordinator verifies a SHA-256 handoff, base ancestry, exact path
    allowlist, diff, tests, and reviews, then uses only
    `git merge --no-ff --no-edit CANDIDATE_SHA`; cherry-pick/squash/rebase and
    conflict resolution are forbidden. It proves the candidate is an ancestor
    of the tested merge commit before pushing. Plan 6 uses a closed
    argv-array release driver, H1 for disposable stack creation, digest-bound
    P1 for production promotion, distinct receipt/selection/export-bound P2
    for retirement, and a third consistency-set-bound approval for whole DR.

This is deliberately not a generic hook or plugin surface: the event and
provider have named consumers in Plans 2 and 3. Alternative B — modifying the
projection core and cleanup directly from Plan 3 — was rejected because it
would couple Graph publication to verification and Wiki internals.

## 7. Safe Parallel Session Layout

Only one implementation writer is safe now: the bounded backend Task 5 repair.
This local session remains the read-only coordination/control plane except for
checkpoint and plan-document maintenance. C1 needs no further code repair.

### Bounded review protocol

The unbounded review/repair loop used during early adapter work is forbidden
for these sessions. For each task:

1. one implementer produces the RED/GREEN task commit;
2. one fresh reviewer checks specification compliance against the exact task
   contract and diff;
3. one fresh reviewer checks code quality, regressions, safety, and test
   adequacy;
4. at most two repair/re-review cycles are allowed in the same session;
5. if a Critical/Important finding remains after cycle two, stop and record a
   bounded blocker with reproducer and owner instead of beginning an unlimited
   sibling-case hunt.

Reviewers must not expand framework semantics beyond the frozen acceptance
matrices or reopen already accepted unrelated components without a concrete
current-HEAD reproducer. This bounds execution while preserving the rule that
no Critical/Important finding may cross a milestone gate.

### Temporal and retry guardrails

Every implementation session has a hard wall-clock budget of **150 minutes**.
Reaching the limit means checkpoint and handoff, not permission to weaken a
test or expand scope.

| Elapsed time | Required outcome |
|---:|---|
| 0–15 min | Read required documents, verify repository/branch/status/SHA, identify the exact test and production files; no implementation yet |
| 15–45 min | Produce a deterministic reproducer or the prescribed RED test and record the observed failure |
| 45–105 min | Implement the smallest scoped change and obtain the targeted GREEN result |
| 105–135 min | Run prescribed regressions and the two bounded reviews |
| 135–150 min | Apply only bounded repairs, record evidence, commit if the task is coherent, and write the handoff |

Additional stop rules:

- Send a progress update at every RED, GREEN, review, and commit boundary, and
  at least once every 20 minutes while work is active.
- Never run the same failing command more than three times. Every retry after
  the first must follow a stated new hypothesis or a code/environment change.
- If 30 minutes pass without a new artifact — reproducer, test result, diff,
  profile, review finding, or commit — stop and checkpoint the blocker.
- Poll a command or subagent after 10 minutes without useful output. Interrupt
  it at 20 minutes unless it is producing measurable progress.
- A focused test command has a 15-minute hard limit. Split it if it exceeds
  that limit.
- A prescribed gate/regression batch has a 45-minute hard limit. Split it into
  deterministic shards and record every shard; do not replace it with a
  smaller assertion set.
- Do not start an unrelated repair to make a broad suite green. Prove whether
  it reproduces on the task parent and record it separately.
- At the session limit, terminate background processes and subagents. Leave a
  clean commit only when the scoped deliverable is coherent and its targeted
  tests pass; otherwise preserve the working tree and record exact `git
  status`, diff scope, last command, failure, and next action without claiming
  completion.

### Current wave — Task 5 bounded quality repair

- Repository: `/home/ubuntu/dev-sandbox`.
- Existing branch: `codex/graph-lifecycle-v2-backend-task-5`.
- Preserve the two known modified evidence documents; do not clean, reset, or
  overwrite them.
- Repair only the unindexed staged-record lookup described in Section 5.2.
- Test against isolated/test storage and namespaces. Do not run the live schema
  initializer, migration, projection, purge, restart, deploy, or restore.
- Stop after focused/regression checks and fresh reviews. Do not continue into
  Task 6 even if Task 5 becomes verified.
- The Task 5 worker does not merge/push. After its zero-Critical/Important
  report, it seals the exact handoff. A genuinely fresh coordinator task
  verifies the SHA/digest/ancestry/allowlist/tests, integrates it into current
  backend `main`, reruns the affected smoke, pushes, updates this checkpoint,
  and only then creates the Task 6A branch.

A simultaneous session may be read-only reviewer only. It must not edit either
repository, database, Neo4j, deployment, or this checkpoint.

### Later waves — sequential after the Task 5 quality gate

1. Backend Task 6A, then 6B, then 6C: three separate sessions/commits/reviews,
   with coordinator merge-to-main/test/push after each accepted slice.
2. Backend Tasks 7–8, task-by-task with a review boundary between them.
3. Backend Tasks 9–11, still one commit and review per task.
4. Backend Task 12, C2/L01–L15, fresh overall review, and checkpoint update.
5. Start P1 only after P0 C1 and C2 are both verified and integrated. Plan 3
   begins with 1A, 1B, and 1C as separate sessions/commits/reviews/main
   integrations before Task 2. It closes with 16A-backend and 16B-Agent/plugin
   implementation commits integrated first, then evidence-only report commits
   bound to immutable `subject_main_sha` values; 16B evidence waits for the
   exact 16A backend subject SHA. Read-only 16C unions per-node ownership and
   closes C3. Implementation work may overlap only after Tasks 1–15 are
   integrated; evidence sealing follows the stated dependency order.

This is a dependency chain, not permission to create simultaneous writers.
Parallel sessions are useful only for read-only review, test observation, or
work in a different repository after its start gate; they must not share a
branch, database, Neo4j namespace, Compose project, or checkpoint writer.

## 8. Exact Prompt for the Next Session

### Prompt C — remote backend Task 5 bounded repair

```text
Before reading any handoff document, verify the copy using the manifest digest
supplied OUT OF BAND by the coordinator (never read the expected value from
the remote directory):
cd /home/ubuntu/graph-lifecycle-v2-handoff
printf '%s  %s\n' 'EXPECTED_HANDOFF_MANIFEST_SHA256' SHA256SUMS | sha256sum -c -
sha256sum -c SHA256SUMS
If either command fails, stop without editing. The coordinator must replace
EXPECTED_HANDOFF_MANIFEST_SHA256 with the exact 64-hex value in this prompt.

Work only in /home/ubuntu/dev-sandbox on the existing branch
codex/graph-lifecycle-v2-backend-task-5. Read these handoff files completely:
1. /home/ubuntu/graph-lifecycle-v2-handoff/2026-07-21-graph-lifecycle-v2-execution-checkpoint.md
2. /home/ubuntu/graph-lifecycle-v2-handoff/2026-07-16-graph-lifecycle-v2-master.md
3. /home/ubuntu/graph-lifecycle-v2-handoff/2026-07-16-graph-lifecycle-v2-02-backend-import-projection-api.md, especially Task 5.

Verify HEAD is exactly 1735eacd01f03cbe559a7aee9631404e4b05643a before
editing. Verify git status contains only the two known modified evidence files:
.superpowers/sdd/progress.md and ai-sandbox/logbooks/LOGBOOK_PROJECT.md. Preserve
their contents; do not clean, reset, restore, or overwrite unrelated changes.

Scope is only the remaining Task 5 Important. Do not start Task 6. Use
receiving-code-review, TDD, and subagent-driven development with bounded fresh
specification and code-quality reviews.

First add RED regressions for the query shape and for duplicate IDs across
batches, swapped batches, omitted records, and incorrect chunk indices. Then
replace the record_ordinal range/ORDER BY staged-window query with a lookup of
the current artifact batch's public IDs, scoped by graph_import_id and
record_kind, using the existing composite primary key. Chunk the ID list
internally only if needed for bind limits. Map returned rows by public_id, then
iterate in original artifact order and require exactly one row with the exact
public_id, expected record_ordinal, and expected chunk_index. Preserve the final
per-kind consumed-versus-staged count.

Do not sort by public_id, add a migration/index, change the artifact order, or
touch live data. A deterministic query-shape regression that proves public_id
lookup and excludes ordinal range/order is required; EXPLAIN is optional only
against an isolated representative PostgreSQL database.

The implementation allowlist is exactly:
- backend/app/Services/Graph/V2/GraphV2LifecycleProjectionMapper.php
- backend/tests/Integration/Graph/CanonicalGraphV2ProjectionTest.php

Run these exact gates, respecting the checkpoint's 15/45-minute limits:
1. RED then GREEN: `docker compose exec -T app php artisan test tests/Integration/Graph/CanonicalGraphV2ProjectionTest.php`
2. Graph regression: `docker compose exec -T app php artisan test tests/Integration/Graph`
3. Pint: `docker compose exec -T app vendor/bin/pint --test app/Services/Graph/V2/GraphV2LifecycleProjectionMapper.php tests/Integration/Graph/CanonicalGraphV2ProjectionTest.php`
4. PHP lint: `docker compose exec -T app php -l app/Services/Graph/V2/GraphV2LifecycleProjectionMapper.php` and the same command for `tests/Integration/Graph/CanonicalGraphV2ProjectionTest.php`
5. PHPStan: `docker compose exec -T app vendor/bin/phpstan analyse app/Services/Graph/V2/GraphV2LifecycleProjectionMapper.php --no-progress`

Use one spec reviewer and one quality reviewer, with at most two repair cycles.
Task 5 may be declared verified only with zero Critical/Important; the existing
schema-definition verification Minor may remain explicitly recorded. Before
the implementation commit require:
`git diff --name-only 1735eacd01f03cbe559a7aee9631404e4b05643a --` equals exactly the two implementation paths **plus** the two known pre-dirty evidence paths. Separately require
`git diff --name-only 1735eacd01f03cbe559a7aee9631404e4b05643a -- backend/app/Services/Graph/V2/GraphV2LifecycleProjectionMapper.php backend/tests/Integration/Graph/CanonicalGraphV2ProjectionTest.php`
equals exactly the implementation allowlist, then run:
`git add -- backend/app/Services/Graph/V2/GraphV2LifecycleProjectionMapper.php backend/tests/Integration/Graph/CanonicalGraphV2ProjectionTest.php`
and require `git diff --cached --name-only` equals those two paths. Commit with
`fix(graph): index lifecycle records by public id` and record `REPAIR_SHA`.

Only after that commit, update the two pre-existing evidence documents without
discarding their old content. Stage exactly:
`git add -- .superpowers/sdd/progress.md ai-sandbox/logbooks/LOGBOOK_PROJECT.md`
and require the cached-name output equals those two paths. Commit with
`docs(graph): record task 5 quality closure` and record `EVIDENCE_SHA`. Require
`git rev-parse REPAIR_SHA^` equals the original Task 5 SHA above and
`git rev-parse EVIDENCE_SHA^` equals `REPAIR_SHA`. Compute/report
`TASK5_BASE_SHA=$(git rev-parse 1735eacd01f03cbe559a7aee9631404e4b05643a^)`;
the coordinator candidate range is exactly `TASK5_BASE_SHA..EVIDENCE_SHA`, so
it preserves the original Task 5 commit, repair commit, and evidence commit.

Do not run live migrations, the live schema initializer, projection, purge,
restart, deploy, restore, or destructive Docker commands. Do not push or merge.
Report exact RED/GREEN/regression commands and results, both review verdicts,
commit SHAs, git status, and residual risks.
The checkpoint temporal/retry guardrails are mandatory. Stop and hand off at
150 minutes even if the Important is not yet closed.
```

Recommended coordinator model: GPT-5.6-sol. Delegated bounded
implementation/review work may use GPT-5.6-terra, but the root session must
retain contract and final-review ownership.

## 9. Checkpoint Update Protocol

After every task, the coordinating session must update this file with:

```text
plan/task
repository and branch
tested commit SHA
contract-lock manifest digest
RED command and observed failure
GREEN and regression commands with exit status
gate IDs covered
review verdict and open findings by Critical/Important/Minor
residual risk
next unblocked task
```

Never mark a task `verified` from code presence, a green mock, a healthy
container, or an old report on another SHA. Never have two writers update the
same repository or checkpoint concurrently.

## 10. Manual-Test Expectations

- End of P0: technical backend import/projection/query API can be accepted;
  this is not yet the final human Graph Explorer.
- End of P1 + P2: manual browser testing of request lifecycles, entrypoint
  exploration, element analysis, verification badges, and human-readable Wiki
  becomes meaningful.
- End of P3 + P4: disaster recovery, deployment, mobile/desktop live acceptance,
  and v1 retirement decision can be accepted.

C1 is closed. The plan-amendment gate remains open until the normative design,
Plans 2–3, Plan 5 backup sequencing, Plan 6 isolated acceptance, master, and
this checkpoint pass fresh specification, executor, and concurrency reviews
with zero Critical/Important findings. The next safe implementation
action remains the bounded backend Task 5 Important repair in Prompt C. Do not
start backend Task 6 before both the Task 5 quality gate and this amendment gate
are closed.
