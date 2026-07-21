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
| **P1 — Uncertainty reduction** | Plan 3 verification + Wiki | V01–V18 verified end-to-end |
| **P2 — Human exploration** | Plan 4 React Graph Explorer | U01–U12, build, responsive and accessibility gates verified |
| **P3 — Recoverability** | Plan 5 backup + cutover tooling | Restic snapshot, restore rehearsal, scoped export/restore and runbook verified |
| **P4 — Live acceptance** | Plan 6 | deployed live gates, explicit user acceptance, scoped v1 retirement decision, final integration |

`P0/P1 finding` in older review text means severity, not one of these
milestones. New reports must use `Critical`, `Important`, or `Minor` for review
severity.

## 3. Verified Repository Snapshot

### 3.1 Hades Agent on the Mac

| Field | Value |
|---|---|
| Repository | `/Users/gabriele/Dev/Hephaistos` |
| Branch | `codex/graph-v2-lifecycle-validation-performance` |
| HEAD | `9f226f29269f270b0e4bd2e377d5dee6177a7902` |
| `main` | same SHA |
| `origin/main` | same SHA |
| Worktree | clean, including untracked files |
| Branch-specific implementation | none; the branch currently points exactly at `main` |

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
- `.codex-artifacts/graph-v2/agent-gates.json` records G01–G14 as passing at
  the current code lineage. G07 took `228.969093s`.
- Task 18 is not formally closed: the complete targeted and broad regression
  commands, tested SHA, timestamp, final reviewer verdict, and required
  `tests/fixtures/hades/graph_v2/` tree are not all persisted together.

Do not fabricate historical reports. Close this debt with one fresh C1
evidence pack and current-HEAD review.

### 3.2 Backend on the server

| Field | Value |
|---|---|
| Workspace | `/home/ubuntu/dev-sandbox` |
| Backend | `/home/ubuntu/dev-sandbox/backend` |
| Branch | `main` |
| HEAD | `148da33c897d38c76b3778c923f743498f2daa7a` |
| `origin/main` | same SHA, verified with `git ls-remote` |
| Worktree | clean |
| Runtime | app, frontend, PostgreSQL, Neo4j, scheduler, and worker healthy |
| Graph-v2 imports | zero rows at checkpoint time |
| Queue | zero pending, delayed, or reserved jobs at checkpoint time |

Plan 2 implementation present on `main`:

| Task | Commit | Snapshot evidence | Status |
|---:|---|---|---|
| 1 — vendored contracts | `4e8009de` | manifest, lock, schema, and golden bytes match the Agent copy | implemented; current audit did not rerun tests |
| 2 — storage migrations | `31262c22` | five graph-v2 migrations `000100`–`000350` are `Ran`, batch 3 | implemented; L-gates open |
| 3 — resumable import | `eaa0ea58` | create/show/chunk/complete routes registered | implemented; L-gates open |
| 4 — two-pass leased validation | `148da33c` | four-run constants, leases, retry policy, encrypted single-try job, and reconcile schedule present | implemented; L-gates open |
| 5–12 | — | normative sentinel classes/tests/artifact absent | not started |

The live services being healthy proves operational availability, not L01–L15.

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
| Plan 1 — producer | all 18 tasks implemented | **reopened / implemented-unverified** because of production-scale performance and incomplete C1 evidence |
| Plan 2 — backend | Tasks 1–4 implemented; Tasks 5–12 not started | C2/L01–L15 open |
| Plan 3 — verification/Wiki | v2 plan not started | C3/V01–V18 open |
| Plan 4 — React Explorer | v2 plan not started | C4/U01–U12 open |
| Plan 5 — DR/cutover | v2 plan not started | C5 open |
| Plan 6 — live acceptance | not started | C6 and user acceptance open |

Therefore P0 is not closeable yet. Its remaining deliverables are one Agent C1
closure workstream plus backend Tasks 5–12 and C2.

## 5. P0 Blocker: Producer Validation Scalability

A real Carnovali sync ran for roughly twenty minutes and reached approximately
7 GB before the Mac restarted. This runtime observation is not represented by
the synthetic green gates and must be treated as a P0 blocker.

Confirmed repeated full scans exist inside
`hermes_cli/hades_index/lifecycle/model.py::AdapterResult.validate()`:

- call sites scan all structures around line 2197: `O(C * S)`;
- successor validation repeatedly scans branch arms;
- `RETURNS_TO` validation scans call sites/scopes and all edges, reaching an
  `O(E^2)` case;
- unresolved facts scan all edges: `O(U * E)`.

The exact repair is not pre-authorized by this observation. The repair session
must first add deterministic operation-count/lookup regressions, then replace
repeated scans with indexes while preserving validation order, error codes, and
error messages. Wall-clock-only tests are insufficient.

Until C1 is green:

- do not run a full Carnovali sync;
- do not recreate the removed Carnovali Docker stack merely for this work;
- use deterministic synthetic fixtures, then Symfony Demo as the bounded real
  fixture;
- monitor peak RSS and elapsed time explicitly before attempting Carnovali.

## 6. Plan Defects Found During Reconciliation

This cleanup corrected three documentation-only contradictions:

- review severity is now `Critical/Important/Minor`, so milestone `P0` is no
  longer confused with a `P0` finding;
- the master Plan 4 start gate now includes the Plan 3 verification
  badge/status DTO dependency already required by the component plan;
- Plan 6 now agrees with the master and Plan 1 Task 16: Tree-sitter and grammar
  wheels are base dependencies and sync performs no lazy/runtime install.

Two forward dependencies still need a reviewed architectural amendment. They
are plan defects, not permission for an implementation agent to improvise:

1. Plan 2 Task 6 queues verification reconciliation although the concrete
   verification implementation is introduced only in Plan 3.
2. Plan 2 Task 11 requires reachability through overlays, requests, and Wiki
   ledgers that Plan 3 has not created yet.

The original branch/merge instructions also no longer describe reality: Agent
Plan 1 and backend Plan 2 Tasks 1–4 are already on `main`. Do not rewrite or
revert published history; continue from current `main` on fresh feature
branches and record the deviation.

Immediate sessions may execute the Agent C1 repair and backend Task 5 because
neither depends on the open forward dependencies. **Do not start backend Task
6 until a reviewed plan amendment resolves them.**

Recommended amendment, pending explicit design approval: emit a generic
projection-activated domain event in Plan 2 and define a concrete reachability
provider interface with current Plan 2 consumers; Plan 3 supplies the
verification/Wiki listener and provider.

## 7. Safe Parallel Session Layout

Only two implementation writers are safe now because they own different
repositories and have no shared mutable runtime dependency.

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

### Wave 1 — may run in parallel

#### Session A: Agent C1 performance and evidence closure

- Repository: `/Users/gabriele/Dev/Hephaistos`.
- Branch: existing `codex/graph-v2-lifecycle-validation-performance`.
- Owns only Agent files/tests/evidence.
- Diagnose and repair the validation complexity with TDD.
- Run focused lifecycle suites before the complete G01–G14 and broad Agent
  regression.
- Produce one current-HEAD C1 evidence pack and fresh spec/code review. Do not
  recreate fictional per-task history.
- Do not run Carnovali until bounded and Symfony Demo gates are safe.

#### Session B: Backend Plan 2 Task 5 only

- Repository: `/home/ubuntu/dev-sandbox`.
- Create `codex/graph-lifecycle-v2-backend-task-5` from
  `148da33c897d38c76b3778c923f743498f2daa7a`.
- Execute exactly Plan 2 Task 5, with RED/GREEN, focused regression, review, and
  one scoped commit.
- Test against isolated/test namespaces. Do not run the live schema initializer,
  migration, projection, purge, restart, or deploy.
- Stop after Task 5 and report commit, commands, results, review findings, and
  residual risk. Do not continue into Task 6 because of the amendment gate.

A third simultaneous session may be read-only reviewer only. It must not edit
either repository, database, Neo4j, deployment, or this checkpoint.

### Later waves — sequential after the amendment gate

1. Backend Task 6.
2. Backend Tasks 7–8, task-by-task with a review boundary between them.
3. Backend Tasks 9–11, still one commit and review per task.
4. Backend Task 12, C2/L01–L15, fresh overall review, and checkpoint update.
5. Start P1 only after P0 C1 and C2 are both verified.

This is approximately five dependency waves. Opening more backend writer
sessions does not shorten the chain; it creates branch, database, and Neo4j
contention.

## 8. Exact Prompts for New Sessions

### Prompt A — local Agent repair

```text
Work only in /Users/gabriele/Dev/Hephaistos on the existing branch
codex/graph-v2-lifecycle-validation-performance.

Read completely, in this order:
1. docs/superpowers/plans/2026-07-21-graph-lifecycle-v2-execution-checkpoint.md
2. docs/superpowers/plans/2026-07-16-graph-lifecycle-v2-master.md
3. Plan 1 Tasks 17, 18 and the Plan 1 exit gate.

Use systematic debugging and TDD before implementation, then work
subagent-driven with a fresh spec reviewer and code-quality reviewer. Scope is
only C1: reproduce the AdapterResult.validate scalability class with a
deterministic operation-count/lookup regression; identify every sibling
repeated full scan; implement indexed validation without changing error order,
codes or messages; run focused suites; run G01-G14 and the broad Agent
regression; create a current-HEAD evidence pack and close the missing Task 18
evidence honestly. Do not fabricate old reports. Do not run Carnovali, start
Docker, modify backend code, or push/merge until tests and reviews are clean.
Keep me updated at each RED, GREEN, review and final-gate boundary.
```

### Prompt B — remote backend Task 5

```text
Work only in /home/ubuntu/dev-sandbox. Read these handoff files completely:
1. /home/ubuntu/graph-lifecycle-v2-handoff/2026-07-21-graph-lifecycle-v2-execution-checkpoint.md
2. /home/ubuntu/graph-lifecycle-v2-handoff/2026-07-16-graph-lifecycle-v2-master.md
3. /home/ubuntu/graph-lifecycle-v2-handoff/2026-07-16-graph-lifecycle-v2-02-backend-import-projection-api.md, especially Task 5.

Verify main and origin/main are exactly
148da33c897d38c76b3778c923f743498f2daa7a and the worktree is clean. Create a
fresh branch named codex/graph-lifecycle-v2-backend-task-5. Execute only Plan 2 Task 5 using TDD and
subagent-driven development, with separate spec and code-quality reviews.
Use isolated/test Neo4j namespaces. Do not run live migrations, the live schema
initializer, projection, purge, restart, deploy, restore or destructive Docker
commands. Stop after one scoped Task 5 commit. Report exact RED/GREEN/regression
commands and results, review findings, commit SHA and residual risks. Do not
start Task 6: the checkpoint records a required plan-amendment gate.
```

Recommended coordinator model for both sessions: GPT-5.6-sol. Delegated
bounded implementation/review work may use GPT-5.6-terra, but the root session
must retain contract and final-review ownership.

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

The next safe action is Wave 1: Session A and Session B in parallel, and no
other implementation writer.
