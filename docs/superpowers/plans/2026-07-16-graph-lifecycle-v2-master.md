# Hades Graph Lifecycle v2 Master Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current inferred/partial graph pipeline with the clean v2 canonical graph, bounded lifecycle exploration, verification queue, human-readable Wiki workflow, React Graph Explorer, and verified disaster-recovery path defined by the approved specification.

**Architecture:** The root JSON Schemas and golden vectors are the source of truth. Hades Agent produces immutable v2 artifacts and resumable chunks; the Laravel backend validates, projects, versions, queries, and overlays them; a verification queue reduces graph/Wiki uncertainty without mutating base artifacts; React consumes one closed v2 dashboard protocol; Restic plus scoped export/restore protect the cutover. There is no v1 adapter, fallback, mixed-version query, or dual-protocol graph route.

**Tech Stack:** Python 3.11+, pytest, JSON Schema 2020-12, RFC 8785 JCS, tree-sitter 0.26.0, Laravel/PHP 8.3+, Pest/PHPUnit, PostgreSQL, Neo4j 5.26.27 Community, React/TypeScript/Vitest, `@xyflow/react` 12.11.2, Docker Compose, Restic, systemd.

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
- `tree-sitter` is pinned to `0.26.0`; `tree-sitter-language-pack` is pinned to `1.12.5`.
- Ordinary `hades backend sync` reads and caches verification counts only; it never claims work, invokes a model, or injects conversation messages.
- Verification work is processed one item at a time, with project, binding, capability, lease, target-version, and structured-result checks.
- No new core model tool is added. Agent capability is CLI + skills + the service-gated Hades backend plugin surface.
- Inertia must not be reintroduced. `frontend/src/pages/GraphPage.tsx` stays a thin composition root.
- Traefik remains separate from the application Compose stack and is not reconfigured by these plans.
- Never run `migrate:fresh`, drop PostgreSQL, clear Neo4j globally, or delete users/projects/memory/Wiki/Kanban data.
- Before any potentially destructive graph operation, create and verify the required DR backup and scoped v1 export.
- If an unexpected restore occurs, rerun the user seeder and prove admin login before completion.
- Do not write this platform work to `LOGBOOK_CARNOVALI`.
- Preserve unrelated local or server changes. If a listed file differs materially from the approved specification, stop that task and report the exact mismatch.
- Every task uses red/green TDD, a focused test command, a broader regression command, `git diff --check`, and one scoped commit.

---

## Repository Map

| Alias | Absolute path | Branch to create at execution time | Ownership |
|---|---|---|---|
| `AGENT_REPO` | `/Users/gabriele/Dev/Hephaistos` | `codex/graph-lifecycle-v2-agent` | contracts, producer, uploader, CLI, skills, agent docs |
| `BACKEND_REPO` | `/home/ubuntu/dev-sandbox` | `codex/graph-lifecycle-v2-backend` | Laravel backend, React frontend, PostgreSQL/Neo4j projection, operations |
| `CARNOVALI_WORKSPACE` | `/Users/gabriele/Dev/sinervis/carnovali` | read-only during import | live v2 extraction fixture |

Backend-relative paths beginning with `backend/`, `frontend/`, `ops/`, or backend `docs/` are resolved from `BACKEND_REPO`. Agent-relative paths are resolved from `AGENT_REPO`.

## Plan Set and Hard Dependencies

Execute these files in the listed order. A later plan may start only when its `START GATE` is satisfied.

| Order | Plan | Deliverable | START GATE | EXIT GATE |
|---:|---|---|---|---|
| 1 | `2026-07-16-graph-lifecycle-v2-01-contract-indexer-agent.md` | Root schemas, golden vectors, typed v2 artifact package, polyglot lifecycle producer, bundle/chunk spool | Clean agent feature branch | G01–G12/G14 plus Python side of G13 green |
| 2 | `2026-07-16-graph-lifecycle-v2-02-backend-import-projection-api.md` | Backend vendored contracts, resumable validation, atomic projection, closed dashboard API | Plan 1 contract commit pinned by SHA | L01–L15 green in backend |
| 3 | `2026-07-16-graph-lifecycle-v2-03-verification-wiki.md` | Graph/Wiki verification queue, overlays, CLI/worker/skills, empty-project Wiki bootstrap | Plans 1–2 DTOs and storage migrations green | V01–V18 green end-to-end |
| 4 | `2026-07-16-graph-lifecycle-v2-04-react-graph-explorer.md` | Explainable lifecycle-first Graph Explorer and element analysis | Backend dashboard response golden fixture frozen | U01–U12 green; production frontend build green |
| 5 | `2026-07-16-graph-lifecycle-v2-05-backup-cutover.md` | DR scripts/timers, maintenance, scoped v1 export/retire/restore, maintenance deployment | Backend/agent feature branches pass their suites | Restore rehearsal green; cutover prerequisites recorded |
| 6 | `2026-07-16-graph-lifecycle-v2-06-live-acceptance.md` | Carnovali import, deployed browser/API/queue acceptance, review, merge/push | Plans 1–5 completed without unresolved P0/P1 findings | All G/L/V/U and 12 live gates green; user acceptance before v1 retirement |

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
8. Commit only task files with the prescribed message.
9. Have a fresh reviewer check spec compliance first and code quality second. Fix findings before the next task.
10. Update the execution ledger below with commit, tests, and residual risk.

Never combine tasks across repositories in one Git commit. Backend and agent histories remain independently bisectable.

## Execution Ledger

Copy this row once per completed task into the implementation task report:

```text
Plan/Task:
Repository:
Commit:
RED command and observed failure:
GREEN command and result:
Regression command and result:
Spec gates covered:
Reviewer verdict:
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
- the live project/binding differs from `01KXJD0SV73EBGWKNE2EK3M4KD` / `01KXJD1BDMQ2TFABMVJV6EFE8Q`.

Do not treat a test failure as permission to weaken the assertion, use a v1 fallback, return a false zero, disable authorization, or skip the backup gate.

## Global Review Checkpoints

- [ ] **Checkpoint A — Contract freeze:** After Plan 1 Tasks 1–3, compare root schemas, Python types, golden bytes, and contract-lock digests.
- [ ] **Checkpoint B — Producer gate:** After Plan 1, run G01–G12/G14, Python G13 vectors, plus the >5,000-node/>10,000-edge/>500-entrypoint benchmark; PHP/TypeScript G13 closes in Plans 2/4.
- [ ] **Checkpoint C — Backend gate:** After Plan 2, run L01–L15 with failure injection, concurrency, and tenant-isolation suites.
- [ ] **Checkpoint D — Verification gate:** After Plan 3, run V01–V18 including out-of-order cache, stale CAS, graph overlays, Wiki fingerprint recompute, and one-item worker behavior.
- [ ] **Checkpoint E — UX gate:** After Plan 4, run U01–U12, typecheck, build, accessibility checks, and deterministic layout fixtures.
- [ ] **Checkpoint F — DR gate:** After Plan 5, retain the successful Restic snapshot ID and restore-rehearsal report before cutover.
- [ ] **Checkpoint G — Live gate:** After Plan 6, obtain explicit user acceptance before executing the v1 retirement `--confirm` command.

## Specification Traceability

| Design section | Implemented by |
|---|---|
| 6.1–6.4 schemas, scalars, source/identity/digests | Plan 1 Tasks 1–4; Plan 2 Task 1 |
| 6.5–6.11 nodes, entrypoints, structures, edges, evidence, completeness, uncertainty | Plan 1 Tasks 2–3, 5–8; Plan 3 Task 5 |
| 7 lifecycle model, stages, traversal, framework matrix, Graphify role | Plan 1 Tasks 5–8 |
| 8 bundle/chunk transport and resumable API | Plan 1 Tasks 9–11; Plan 2 Tasks 3–4 |
| 9.1 storage migrations/retention | Plan 2 Tasks 2, 11; Plan 3 Task 1; Plan 5 Tasks 5–6 |
| 9.2–9.5 projection versions, context, Neo4j, services | Plan 2 Tasks 4–9 |
| 10 dashboard graph API, handles, cursors, DTOs/errors | Plan 2 Tasks 7–9; Plan 4 Task 1 |
| 11.1–11.6 queue, results, leases, verdicts, CLI | Plan 3 Tasks 1–10 |
| 11.7–11.8 skills and empty-project Wiki bootstrap | Plan 3 Task 11 |
| 12 Hades Agent file map/dependencies/docs | Plan 1 Tasks 1–18; Plan 3 Tasks 8–13; Plan 6 Task 9 |
| 13 React Graph Explorer UX | Plan 4 Tasks 1–10 |
| 14.1 deployment order | Plan 6 Tasks 1–9 |
| 14.2 DR/hourly differential backup | Plan 5 Tasks 1–4, 7–8 |
| 14.3–14.4 scoped retirement/restore/rollback | Plan 5 Tasks 4–6; Plan 6 Tasks 7–8 |
| 15 G/L/V/U/live acceptance | Plans 1–4 final tasks; Plan 6 Tasks 3–6 |
| 16 documentation/operator handoff | Plan 3 Tasks 11–13; Plan 5 Task 7; Plan 6 Task 9 |
| 17 definition of done | Plan 6 Exit Gate |

## Completion Rule

The program is complete only when every component plan is committed on its feature branch, all automated gates are green, the live Carnovali gate passes on desktop and mobile, backups and scoped rollback are verified, unrelated data counts are unchanged, final reviews contain no unresolved P0/P1 finding, and the user explicitly accepts the deployment. Only then merge backend and agent branches to `main` and push.
