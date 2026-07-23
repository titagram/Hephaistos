# Hades Graph Lifecycle v2 Master Implementation Plan

> **Current execution status:** read
> `docs/superpowers/plans/2026-07-21-graph-lifecycle-v2-execution-checkpoint.md`
> before selecting a task. This master remains normative for behavior and task
> order; the checkpoint is authoritative for SHAs, completed evidence,
> blockers, and session ownership.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current inferred/partial graph pipeline with the clean v2 canonical graph, bounded lifecycle exploration, verification queue, human-readable Wiki workflow, React Graph Explorer, and verified disaster-recovery path defined by the approved specification.

**Architecture:** The root JSON Schemas and golden vectors are the source of truth. Hades Agent produces immutable v2 artifacts and resumable chunks; the Laravel backend validates, projects, versions, queries, and overlays them; a verification queue reduces graph/Wiki uncertainty without mutating base artifacts; React consumes one closed v2 dashboard protocol; Restic plus scoped export/restore protect the cutover. There is no v1 adapter, fallback, mixed-version query, or dual-protocol graph route.

**Tech Stack:** Python 3.11+, pytest, JSON Schema 2020-12, RFC 8785 JCS, tree-sitter 0.26.0, filelock 3.24.3, Laravel/PHP 8.3+, Pest/PHPUnit, PostgreSQL, Neo4j 5.26.27 Community, React/TypeScript/Vitest, `@xyflow/react` 12.11.2, Docker Compose, Restic, systemd.

## Global Constraints

- Normative design: `docs/superpowers/specs/2026-07-16-graph-lifecycle-v2-design.md`; a plan may narrow execution order but may not weaken that contract.
- Accepted graph schema is exactly `hades.code_graph.v2`; accepted graph contract is exactly `hades.graph_artifact.v2`.
- No v1 compatibility adapter, fallback, merge, current-cache selection, or dual-protocol dashboard graph endpoint may remain after cutover.
- The graph source scope is exactly `{type: "workspace_binding", id: <binding ULID>}`; repository identity is display metadata only.
- IDs, digests, handles, cursors, overlays, and version preimages use the exact JCS/SHA-256 rules and golden vectors in the approved specification.
- No absolute path, raw source, secret-like literal, traversal component, control character, arbitrary DTO property, unsafe integer, or float may cross the artifact/API boundary.
- Evidence, flow semantics, and completeness remain orthogonal. Unknown is never rendered or returned as zero.
- Uncertain edges are traversal frontiers. Neither producer nor backend traverses through an unresolved target.
- Producer flow membership and lifecycle stages are authoritative. The backend validates/maps them one-to-one and does not reconstruct them.
- The browser keeps at most 200 canonical lifecycle nodes and receives at most 120 in the initial backbone.
- `@xyflow/react` is pinned to exactly `12.11.2`; do not add ELK, D3, Cytoscape, or another graph renderer.
- `jsonschema==4.26.0`, `tree-sitter==0.26.0`, the exact official JavaScript, TypeScript/TSX, PHP, and Python grammar wheels, and `filelock==3.24.3` are mandatory base dependencies, never extras or lazy installs. Grammar loading never downloads at runtime. A failed detected-language canary escapes the legacy graph builder and blocks publication; only a failure confined to one ordinary source file becomes partial coverage.
- Ordinary `hades backend sync` reads and caches verification counts only; it never claims work, invokes a model, or injects conversation messages.
- Verification work is processed one item at a time, with project, binding, capability, lease, target-version, and structured-result checks.
- Plan 2 publishes the semantic `CanonicalGraphV2ProjectionActivated` event after a successful projection-head CAS and owns the fixed internal graph-artifact reachability aggregator. Plan 2 never imports verification/Wiki services; Plan 3 consumes the event and registers concrete verification/Wiki reachability providers.
- `GraphArtifactReferenceLock` is the outer transaction for every import-reference writer and cleanup: sorted import advisory locks, sorted import rows, then scope/domain locks. Plan 3 domain services consume its guard; they never reacquire it. `WikiRevisionService` publishes `WikiCurrentRevisionActivated` after its current-pointer CAS through the real `AppServiceProvider` event registration.
- Verification audit cleanup is whole-chain, fixed at 90 days, externally reachability-aware, and fail-closed; provider composition alone is not a substitute for deleting eligible audit chains.
- No new core model tool is added. Agent capability is CLI + skills + the service-gated Hades backend plugin surface. The separately distributed Codex plugin delegates index, sync, lifecycle queries, and verification to the installed Hades CLI and never embeds a second parser or backend client.
- Inertia must not be reintroduced. `frontend/src/pages/GraphPage.tsx` stays a thin composition root.
- Traefik remains separate from the application Compose stack and is not reconfigured by these plans.
- Never run `migrate:fresh`, drop PostgreSQL, clear Neo4j globally, or delete users/projects/memory/Wiki/Kanban data. The only automated schema-reset exception is the repository's guarded `composer test:postgres` runner after it proves the exact disposable database name is `devboard_acceptance`; it is never pointed at the application database.
- Before any potentially destructive graph operation, create and verify the required DR backup and scoped v1 export.
- If an unexpected restore occurs, rerun the user seeder and prove admin login before completion.
- Do not write this platform work to `LOGBOOK_CARNOVALI`.
- Preserve unrelated local or server changes. If a listed file differs materially from the approved specification, stop that task and report the exact mismatch.
- Every task uses red/green TDD, a focused test command, a broader regression command, `git diff --check`, and one scoped commit.

---

## Repository Map

| Alias | Absolute path | Branch rule at execution time | Ownership |
|---|---|---|---|
| `AGENT_REPO` | `/Users/gabriele/Dev/Hephaistos` | checkpoint-authoritative existing branch for an open repair; otherwise fresh `codex/graph-v2-<plan>-<slice>` from then-current clean, pulled `main` after the accepted predecessor was integrated | contracts, producer, uploader, CLI, skills, agent docs |
| `BACKEND_REPO` | `/home/ubuntu/dev-sandbox` | checkpoint-authoritative existing branch for an open repair; otherwise fresh `codex/graph-v2-<plan>-<slice>` from then-current clean, pulled `main` after the accepted predecessor was integrated | Laravel backend, React frontend, PostgreSQL/Neo4j projection, operations |
| `CODEX_PLUGIN_REPO` | `/Users/gabriele/plugins/hades-backend` | standalone `codex/graph-v2-verification`; never Agent core or installed cache | five delegating Codex skills and plugin contract tests |
| `SYMFONY_DEMO_FIXTURE` | isolated pinned Symfony Demo checkout chosen by Plan 6 | read-only during import | small representative live acceptance fixture |
| `CARNOVALI_WORKSPACE` | `/Users/gabriele/Dev/sinervis/carnovali` | read-only; optional post-acceptance scale gate | large real-world extraction/performance fixture |

Backend-relative paths beginning with `backend/`, `frontend/`, `ops/`, or backend `docs/` are resolved from `BACKEND_REPO`. Agent-relative paths are resolved from `AGENT_REPO`. Never revive the old catch-all branch names merely because they appear in historical prose: the checkpoint records the exact accepted SHA and branch for every current slice.

## Plan Set and Hard Dependencies

Execute these files in the listed order. A later plan may start only when its `START GATE` is satisfied.

| Order | Plan | Deliverable | START GATE | EXIT GATE |
|---:|---|---|---|---|
| 1 | `2026-07-16-graph-lifecycle-v2-01-contract-indexer-agent.md` | Root schemas, golden vectors, typed v2 artifact package, polyglot lifecycle producer, bundle/chunk spool | Clean agent feature branch | G01–G12/G14 plus Python side of G13 green |
| 2 | `2026-07-16-graph-lifecycle-v2-02-backend-import-projection-api.md` | Backend vendored contracts, resumable validation, atomic projection, closed dashboard API | Plan 1 contract commit pinned by SHA | L01–L15 green in backend |
| 3 | `2026-07-16-graph-lifecycle-v2-03-verification-wiki.md` | Graph/Wiki verification queue, overlays, CLI/worker/skills, empty-project Wiki bootstrap, standalone Codex plugin | Global Checkpoint C / execution checkpoint C2 and L01–L15 green, including activation event, reference lock, and base reachability seam | V01–V19 plus verification-retention gate green end-to-end |
| 4 | `2026-07-16-graph-lifecycle-v2-04-react-graph-explorer.md` | Explainable lifecycle-first Graph Explorer and element analysis | Plan 2 dashboard response golden fixture frozen and Plan 3 verification badge/status DTOs available | U01–U12 green; production frontend build green |
| 5 | `2026-07-16-graph-lifecycle-v2-05-backup-cutover.md` | DR scripts/timers, bound maintenance authority, central storage guard/inventory, scoped v1 export/retire/restore, resumable cutover driver | C1/C2/C3 and Checkpoint E green; Plans 1–4 exact accepted slices integrated, tested, reviewed with zero Critical/Important, and pushed; maintenance service/copy exists | Checkpoint F tooling: central storage boundary, bound-authority crash/resume, disposable backup/restore/export/retirement/restore, and release-driver gates green with production untouched |
| 6 | `2026-07-16-graph-lifecycle-v2-06-live-acceptance.md` | isolated Symfony Demo import, browser/API/queue/plugin acceptance, production-promotion/deployed-SHA verification | C1/C2/C3 plus Checkpoints E/F green; Plans 1–5 completed on exact pushed SHAs without unresolved Critical/Important | All G/L/V/U and isolated live gates green; digest-bound approval before production promotion and separate digest-bound approval before v1 retirement; Carnovali scale run is optional follow-up |

Naming is fixed: execution checkpoint **C1** is the Agent producer gate, execution checkpoint **C2** is global **Checkpoint C** / Plan 2 backend gate, and execution checkpoint **C3** is global **Checkpoint D** / Plan 3 verification gate. C2 closes only the Plan 2 side of the cross-plan seam: post-CAS event emission, outer reference lock/guard, fixed provider aggregator, and projection/head/attempt/context roots. C3 closes the listeners, normalized verification/Wiki providers, whole-chain audit retention, and cleanup races. C2 must not invent Plan 3 tables; C3 must not modify the projection core to call verification directly.

## Cross-Repository Contract Lock

The implementation worker creates this record after Plan 1 Task 2 and updates it only when a contract change is intentionally approved:

```json
{
  "schema": "hades.graph_v2_contract_lock.v1",
  "schema_source_commit": "40-lowercase-git-sha",
  "manifest_sha256": "64-lowercase-hex",
  "schema_digests": {
    "artifact.schema.json": "64-lowercase-hex",
    "bundle.schema.json": "64-lowercase-hex",
    "chunk.schema.json": "64-lowercase-hex",
    "dashboard-query.schema.json": "64-lowercase-hex",
    "dashboard-response.schema.json": "64-lowercase-hex",
    "graph-overlay.schema.json": "64-lowercase-hex",
    "verification-result.schema.json": "64-lowercase-hex",
    "verification-work.schema.json": "64-lowercase-hex"
  }
}
```

`schema_source_commit` is the already-existing commit that last changed any schema, golden vector, or manifest byte. The lock is created in the following commit, so it never attempts to contain its own Git SHA. Store the agent copy at `contracts/hades/graph-v2/contract-lock.json` and the vendored backend copy at `backend/resources/contracts/hades/graph-v2/contract-lock.json`. The backend CI compares JSON semantics and exact schema bytes. Do not hand-edit only one copy.

## Executor Protocol

Use this protocol for every task in every component plan:

1. Read the task, its `Interfaces`, and only the referenced design sections before editing.
2. Confirm the current repository, branch, and `git status --short`; do not absorb unrelated changes into the task commit.
3. Write the named failing test exactly where specified.
4. Run the exact RED command and record the expected failure class. If it passes unexpectedly, inspect current code and stop rather than inventing a second behavior.
5. Implement only the interfaces and invariants listed in that task.
6. Run the targeted GREEN command, then the task regression command.
7. Run `git diff --check` and inspect `git diff --stat` plus `git diff -- <task files>`.
8. Stage only explicit file paths with `git add -- PATH...`, inspect `git diff --cached --name-only` against the task allowlist, and run `git diff --cached --check` before committing with the prescribed message. `git add .`, `git add -A`, wildcard/directory-root staging, and unrelated paths are forbidden. Tasks explicitly split into mandatory slices are separate sessions, reviews, and commits; never aggregate them back into a mega-task.
9. Have a fresh reviewer check spec compliance first and code quality second. Fix findings before the next task.
10. After zero Critical/Important, seal a SHA-256 handoff containing repository, parent/base SHA, candidate SHA, exact allowlist, diff/check results, test commands/results, review verdicts, and residual risk. A genuinely fresh coordinator Codex task—never the implementation worker—verifies that `BASE_SHA` is an ancestor of both current pulled `main` and `CANDIDATE_SHA`, and that every path in `BASE_SHA..CANDIDATE_SHA` is allowlisted. Integration is only `git merge --no-ff --no-edit CANDIDATE_SHA` from clean current `main`; cherry-pick, squash, rebase, conflict resolution, and hand reimplementation are forbidden. A merge conflict stops and returns to the implementation owner. The coordinator verifies `CANDIDATE_SHA` is an ancestor of the merge commit, reruns affected smoke on that integration commit, and pushes. Record coordinator task ID/model/handoff digest/integration SHA, then create the next branch from pulled `main`.
11. Update the execution ledger below with task commit, main integration SHA, tests, elapsed time, and residual risk. Respect the checkpoint's time-box; on expiry stop with evidence and a resumable checkpoint instead of broadening scope or silently continuing.

Never combine tasks across repositories in one Git commit. Backend and agent histories remain independently bisectable.

## Execution Ledger

Copy this row once per completed task into the implementation task report:

```text
Plan/Task:
Repository:
Commit:
Integrated main commit / push:
RED command and observed failure:
GREEN command and result:
Regression command and result:
Spec gates covered:
Reviewer verdict:
Elapsed time / guardrail:
Residual risk:
```

## Stop Conditions

Stop the current task and report evidence when any of these occurs:

- a required existing model/service/route differs enough that the exact interface cannot be added without changing the approved design;
- a migration would alter or delete non-graph data;
- a backend query cannot prove project + binding + projection scoping;
- a schema/golden digest differs across runtimes;
- validation needs to load the complete artifact into memory instead of using the specified bounded passes;
- a frontend action would require an API not present in the frozen protocol;
- a verification operation cannot be committed atomically with work completion;
- a backup or restore rehearsal fails, Neo4j cannot be restarted healthy, or graph maintenance cannot be safely released;
- a live acceptance command is about to use a production project/binding, the Carnovali project IDs, a user's normal Hades profile, or any non-disposable database/volume instead of the Plan 6 isolated identifiers.

Do not treat a test failure as permission to weaken the assertion, use a v1 fallback, return a false zero, disable authorization, or skip the backup gate.

## Global Review Checkpoints

- [ ] **Checkpoint A — Contract freeze:** After Plan 1 Tasks 1–3, compare root schemas, Python types, golden bytes, and contract-lock digests.
- [ ] **Checkpoint B — Producer gate:** After Plan 1, run G01–G12/G14, Python G13 vectors, plus the >5,000-node/>10,000-edge/>500-entrypoint benchmark; PHP/TypeScript G13 closes in Plans 2/4.
- [ ] **Checkpoint C — Backend gate:** After Plan 2, run L01–L15 with failure injection, concurrency, tenant isolation, one post-CAS projection-activated event, and the composed Plan 2 reachability roots.
- [ ] **Checkpoint D — Verification gate:** After Plan 3, run V01–V19 including out-of-order cache, stale CAS, graph overlays, Wiki fingerprint recompute, one-item worker behavior, hard/progress deadlines, mandatory OS containment plus backend specialist-fence teardown/quarantine across hosts, delayed graph/Wiki event-order handling, human-retry total lock order, verification/Wiki retention races, and the standalone Codex plugin contract/fresh-task smoke.
- [ ] **Checkpoint E — UX gate:** After Plan 4, run U01–U12, typecheck, build, accessibility checks, and deterministic layout fixtures.
- [ ] **Checkpoint F — DR tooling gate:** After Plan 5, prove the central artifact mutation guard/inventory, bound maintenance authority crash/resume/stale-owner fencing, scoped export/retirement/forward restore, resumable release driver, and complete disposable Restic backup/restore rehearsal with `production_touched=false`. Live DR readiness occurs only in Plan 6 Task 10B after digest-bound P1 and before deployment.
- [ ] **Checkpoint G — Acceptance/promotion gate:** After Plan 6 isolated acceptance, obtain the exact run/prepared-release-digest P1 approval before production mutation and a separate run/receipt/selection/export-digest P2 approval before v1 retirement. Whole-system restore requires its own consistency-set-digest approval.

## Specification Traceability

| Design section | Implemented by |
|---|---|
| 6.1–6.4 schemas, scalars, source/identity/digests | Plan 1 Tasks 1–4; Plan 2 Task 1 |
| 6.5–6.11 nodes, entrypoints, structures, edges, evidence, completeness, uncertainty | Plan 1 Tasks 2–3 and 5–15; Plan 3 Task 4 |
| 7 lifecycle model, stages, traversal, framework matrix, Graphify role | Plan 1 Tasks 5–14 |
| 8 bundle/chunk transport and resumable API | Plan 1 Tasks 15 and 17; Plan 2 Tasks 3–4 |
| 9.1 storage migrations/retention | Plan 2 Tasks 2, 6, 11; Plan 3 Tasks 1–8; Plan 5 Tasks 1 and 4–6 |
| 9.2–9.5 projection versions, context, Neo4j, services/maintenance/retention | Plan 2 Tasks 4–11; Plan 3 Tasks 2–8 |
| 10 dashboard graph API, handles, cursors, DTOs/errors | Plan 2 Tasks 7–9; Plan 4 Task 1 |
| 11.1–11.6 queue, results, leases, verdicts, CLI | Plan 3 Tasks 1–11 |
| 11.7–11.8 skills and empty-project Wiki bootstrap | Plan 3 Tasks 12–13 |
| 12 Hades Agent file map/dependencies/docs/plugin | Plan 1 Tasks 1–18; Plan 3 Tasks 9–15; Plan 6 Tasks 4–6 |
| 13 React Graph Explorer UX | Plan 4 Tasks 1–10 |
| 14.1 deployment order | Plan 6 Tasks 0–10 |
| 14.2 DR/hourly differential backup | Plan 5 Tasks 1–4, 7–8 |
| 14.3–14.4 scoped retirement/restore/rollback | Plan 5 Tasks 4–6; Plan 6 Task 10 |
| 15 G/L/V/U/live acceptance | Plans 1–4 final tasks; Plan 6 Tasks 2–8 |
| 16 documentation/operator handoff | Plan 3 Tasks 12–14; Plan 5 Task 7; Plan 6 Tasks 8–10 |
| 17 definition of done | Plan 6 Exit Gate |

## Completion Rule

The program is complete only when every accepted component slice is integrated into and pushed from the appropriate `main`, all automated gates are green, the isolated Symfony Demo acceptance passes on desktop and mobile (with Carnovali retained as an optional later scale gate), backups and scoped rollback are verified, unrelated data counts are unchanged, final reviews contain no unresolved Critical/Important finding, and the user explicitly accepts production promotion/retirement decisions. Plan 6 verifies that deployed SHAs match those incrementally integrated histories; it does not accumulate one late mega-merge.
