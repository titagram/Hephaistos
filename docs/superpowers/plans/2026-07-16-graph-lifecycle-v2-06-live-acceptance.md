# Hades Graph Lifecycle v2 Live Acceptance and Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy the clean v2 pipeline behind graph-only maintenance, import Carnovali through Hades Agent, verify lifecycle/Wiki/UX behavior end-to-end, obtain user acceptance, and then safely retire v1 and merge both repositories.

**Architecture:** The frontend first enters an explicit no-request graph maintenance mode. Backend v2 and Hades Agent deploy while v1 remains rollback-retained. Carnovali provides a real Symfony lifecycle and symbol/search fixture. Automated gates, direct data invariants, desktop/mobile browser checks, and one graph plus one Wiki verification completion precede user acceptance and the receipt-bound retirement command.

**Tech Stack:** Docker Compose, Laravel queues/scheduler, PostgreSQL, Neo4j, Hades CLI, React production build, browser test tooling, Git.

## Global Constraints

- Inherit `2026-07-16-graph-lifecycle-v2-master.md`.
- START GATE: Plans 1–5 exit gates are satisfied; DR readiness file is green.
- Live project: `01KXJD0SV73EBGWKNE2EK3M4KD`.
- Live workspace binding: `01KXJD1BDMQ2TFABMVJV6EFE8Q`.
- Live workspace: `/Users/gabriele/Dev/sinervis/carnovali/`.
- Required route: `/generale/soggetti-attivi/`.
- Required symbol: `AdminControllerBulkDeleteBehavior`.
- Do not modify Carnovali source, `/Users/gabriele/Dev/sinervis/LOGBOOK.md`, or any Carnovali logbook.
- Do not retire v1 until the user explicitly confirms acceptance after seeing the live results.
- Git merge/push occurs only after acceptance, review, and retirement/rollback decision.

---

### Task 1: Deploy the Graph-Only Maintenance Frontend

**Backend/frontend files:**
- No feature edit: deploy the Plan 4 `GraphMaintenanceScreen` branch with the maintenance build flag.
- Create artifact: `.codex-artifacts/graph-v2/deploy-baseline.json`

**Interfaces:**
- Build flag: `VITE_GRAPH_V2_MAINTENANCE=true`.
- `/graph` renders exact `graphCopy.maintenance` and makes no graph API request.
- Every non-graph route remains active.

- [ ] **Step 1: Capture deployment baseline**

Record backend/frontend image digests, Git commits, running containers, PostgreSQL/Neo4j health, queue/scheduler health, active v1 graph counts/pointer, unrelated data counts, DR snapshot ID, scoped export digest, and admin login smoke.

- [ ] **Step 2: Build/test maintenance bundle**

```bash
cd /home/ubuntu/dev-sandbox/frontend
VITE_GRAPH_V2_MAINTENANCE=true npm run build
npm test -- --run src/pages/__tests__/GraphPage.test.tsx -t maintenance
```

Expected: PASS and no graph request in test.

- [ ] **Step 3: Deploy frontend only and smoke**

```bash
cd /home/ubuntu/dev-sandbox
docker compose config --quiet
docker compose build frontend
docker compose up -d --no-deps frontend
docker compose ps frontend
```

Expected: the `frontend` service is healthy/running. If `docker compose config --services` does not contain the exact service name `frontend`, stop and update the plan from the checked-in Compose file before deploying. Verify `/graph` maintenance on desktop/mobile, login/project/Wiki/memory/Kanban remain usable, browser network contains no graph query, and Traefik routing remains unchanged.

- [ ] **Step 4: Commit baseline artifact**

```bash
git add .codex-artifacts/graph-v2/deploy-baseline.json
git commit -m "chore(graph): record v2 cutover baseline"
```

### Task 2: Deploy Backend v2 and Start Projection Infrastructure

**Backend files:**
- No new behavior; deploy Plan 2/3/5 commits.
- Create artifact: `.codex-artifacts/graph-v2/backend-deploy.json`

- [ ] **Step 1: Verify backup freshness and enable global graph maintenance**

Require last successful snapshot <90 minutes, successful rehearsal, scoped export present, and all digests. Run maintenance on with reason `cutover`/bounded TTL and retain raw token securely outside Git.

- [ ] **Step 2: Deploy backend and migrate safely**

```bash
cd /home/ubuntu/dev-sandbox
docker compose config --quiet
docker compose build
docker compose up -d --remove-orphans
docker compose exec -T app php artisan migrate --force
docker compose exec -T app php artisan hades:graph-v2:init-schema --verify
docker compose exec -T app php artisan optimize:clear
docker compose exec -T app php artisan route:list --path=graph
docker compose exec -T app php artisan schedule:list
docker compose ps
```

Expected: all application services healthy; migrations succeed without destructive reset; Neo4j v2 schema verifies; graph routes are v2/maintenance-gated. If the checked-in Compose file does not contain `app` and `frontend`, stop before deployment and revise commands from `docker compose config --services`. Never substitute `migrate:fresh`.

- [ ] **Step 3: Prove no v1 current reader**

Check deployed routes/services and authenticated graph API: v1 protocol returns controlled invalid-contract response; no v1 fallback result; graph remains maintenance-blocked; non-graph features work. Verify database migration created only planned graph/verification tables/columns.

- [ ] **Step 4: Record deploy and compare unrelated counts**

`backend-deploy.json` includes image/commit, migration batch, Neo4j constraints/indexes, worker/scheduler state, count comparison, and `healthy:true` only after all pass.

### Task 3: Install Hades Agent v2 and Import Carnovali

**Agent files:**
- No new behavior; install Plan 1/3 commits.
- Create artifact: `.codex-artifacts/graph-v2/carnovali-import.json`

- [ ] **Step 1: Build/install agent from exact commit**

Record wheel/source artifact SHA-256 and commit. Install into the active local Hades environment. Run `hades --version`, `hades backend status --json`, and `hades backend profiles --json`; confirm project/binding association.

- [ ] **Step 2: Run explicit v2 index/import from workspace root**

```bash
cd /Users/gabriele/Dev/sinervis/carnovali
hades backend sync
```

For this release, `hades backend sync` is the explicit graph-index command boundary. Tree-sitter and every pinned grammar wheel are mandatory base dependencies installed before the command starts; sync never installs a lazy `hades-indexer` group or downloads a grammar at runtime. A missing or failed required-language canary blocks graph publication with the typed failure required by Plan 1 Task 16.

- [ ] **Step 3: Monitor resumable import/validation/projection**

Poll import status without restarting work. Confirm chunks/counts/digests, two validation passes, one ready projection/head, artifact and verification hashes, Neo4j namespace counts, search index availability, and no v1/current-cache fallback.

- [ ] **Step 4: Assert real extraction facts**

Require route discovery by exact URI, segment/name query; symbol discovery for `AdminControllerBulkDeleteBehavior`; ordered Symfony lifecycle stages; one expandable branch with alternatives; terminal outcome; honest completeness/omission reasons; unresolved dynamic target creates verification item when present.

- [ ] **Step 5: Store safe import report**

Record IDs/digests/counts/timings/completeness/reason counts only; no raw source or absolute private path in backend artifact. The local workspace path may appear only in the operator report stored outside uploaded graph data.

### Task 4: Exercise Graph and Wiki Verification End-to-End

**Backend/Agent files:**
- No new behavior; validate Plan 3.
- Create artifact: `.codex-artifacts/graph-v2/live-verification.json`

- [ ] **Step 1: Prove sync is notification-only**

Run `hades backend sync` with verification work queued. Record counts and next command. Inspect logs/cache/conversation: no claim, model call, payload listing, system-prompt change, or synthetic message.

- [ ] **Step 2: Complete one graph item**

Run `hades backend verification work --once --domain graph --json`. Verify one item only, heartbeats, structured result, immutable base artifact digest, overlay/audit for verified/contradicted or quiet deferred, and a new desired projection only when overlay exists.

- [ ] **Step 3: Complete one Wiki item**

Ensure a current `needs_verification` page/revision with <=80 claims. Run `hades backend verification work --once --domain wiki --json`. Verify full immutable read snapshot, exact ledger, new revision/fingerprints for verified/contradicted or quiet deferred, and no direct page mutation before atomic completion.

- [ ] **Step 4: Test stale safety in a disposable item**

Claim a disposable verification item, supersede its target before completion, and assert completion applies neither overlay nor Wiki revision and terminal/cache state cannot regress.

- [ ] **Step 5: Record safe verification report**

Include work/request IDs, target versions, result digests/verdicts, overlay/projection or Wiki revision IDs, state versions, and audit checks; exclude evidence content that could reveal source/secrets.

### Task 5: Deploy Graph Explorer v2 and Run Browser Acceptance

**Frontend files:**
- No new behavior; deploy Plan 4 with `VITE_GRAPH_V2_MAINTENANCE=false`.
- Create artifact: `.codex-artifacts/graph-v2/browser-acceptance.json`

- [ ] **Step 1: Build production frontend and run automated U gates**

```bash
cd /home/ubuntu/dev-sandbox/frontend
VITE_GRAPH_V2_MAINTENANCE=false npm run typecheck
VITE_GRAPH_V2_MAINTENANCE=false npm run build
npm test -- --run src/components/devboard/graph/__tests__ src/pages/__tests__/GraphPage.test.tsx src/pages/__tests__/GraphPageProjectTransition.test.tsx
```

- [ ] **Step 2: Deploy and release graph maintenance**

```bash
cd /home/ubuntu/dev-sandbox
docker compose build frontend
docker compose up -d --no-deps frontend
docker compose ps frontend
```

Then close the cutover maintenance token only after backend/Neo4j health succeeds. Confirm `/graph` loads v2 and no v1 request is made.

- [ ] **Step 3: Desktop browser gate**

At >=1440px: choose Carnovali project/scope; find `/generale/soggetti-attivi/`; inspect lifecycle backbone/stage counts/branch/terminal/async; hide/show stage; expand/load more; select node/drawer; reset; analyze `AdminControllerBulkDeleteBehavior`; inspect callers/dependencies/impact as value or honest unknown; advanced compare path states. Technical details start collapsed.

- [ ] **Step 4: Mobile/accessibility gate**

At 390px: chip rail, full-width canvas, bottom-sheet drawer, no overflow/black screen. Keyboard-only at desktop: picker, mode, stages, nodes/tree, drawer focus return, technical disclosure. Enable reduced motion. Verify AA contrast and text distinctions.

- [ ] **Step 5: Error/race gate**

Exercise project/scope/entrypoint rapid changes, abort old requests, stale context one reload then visible error, bad handle clean reselection, partial lifecycle usable warning, projection not-ready/retry. Browser console has zero uncaught exception and network has no 404/401/405/500.

- [ ] **Step 6: Record screenshots/network/console summary**

Store artifact paths/digests plus pass/fail matrix, not secrets/session tokens.

### Task 6: Run All Automated Gates and Independent Reviews

**Files:**
- Create backend artifact: `.codex-artifacts/graph-v2/final-backend-gates.json`
- Create agent artifact: `.codex-artifacts/graph-v2/final-agent-gates.json`

- [ ] **Step 1: Run G gates on exact agent release commit**

Use Plan 1 Task 18 command plus verification suites from Plan 3. Expected all G01–G14 and agent-side V gates PASS.

- [ ] **Step 2: Run L/V gates on exact backend release commit**

Use Plan 2 Task 12 and Plan 3 Task 14 backend commands. Expected L01–L15 and V01–V18 PASS.

- [ ] **Step 3: Run U gates on exact frontend image commit**

Use Plan 4 Task 10 commands. Expected U01–U12 PASS.

- [ ] **Step 4: Run platform regressions**

Agent: Hades backend sync/client/jobs/status/provider/skills suites. Backend: complete graph/verification/Wiki/auth tests, Pint, migrations, route/schedule list. Frontend: test/typecheck/lint/build.

- [ ] **Step 5: Request two-stage reviews**

Review each completed task/spec first, then overall code quality/security/concurrency/data safety. Run an explicit search for dead v1 reader, silent cap, unscoped query/delete, false-zero coalesce, arbitrary overlay input, prompt-cache mutation, inert UI control, secret in backup config, and Inertia import. Resolve every Critical/Important finding before proceeding.

- [ ] **Step 6: Compare truth stores and unrelated data**

Manifest/PostgreSQL/Neo4j active counts and projection versions agree. Users/projects/memory/Wiki/Kanban unrelated counts equal baseline. Admin login passes. Backup status remains fresh.

### Task 7: Present Acceptance Evidence and Await Explicit User Decision

**Files:**
- Create: `.codex-artifacts/graph-v2/user-acceptance-pack.json`

- [ ] **Step 1: Prepare concise acceptance pack**

Include release commits/images, G/L/V/U totals, Carnovali route/symbol results, graph/Wiki verification results, browser matrix, DR snapshot/rehearsal/scoped export digests, data-count comparison, review findings/resolutions, and rollback command references.

- [ ] **Step 2: Show the user the live behavior**

Explain in plain language: entrypoint lifecycle, branch map, uncertainty/verification, Wiki verification, optional technical detail, and exact remaining known limitations.

- [ ] **Step 3: Stop before retirement**

Ask explicitly whether the user accepts v2 and authorizes scoped v1 retirement. No `retire-v1 --confirm`, merge, or push occurs without that response.

### Task 8: Retire v1 After Acceptance, or Preserve Rollback if Declined

**Files:**
- Update: acceptance/deployment artifacts with decision and receipt digests.

- [ ] **Step 1A: If accepted, refresh safety evidence**

Require fresh backup <90 minutes, successful rehearsal still current, scoped export/digests, unchanged selection, and current ready v2 per v1 scope. Run retirement dry-run and inspect closed receipt/counts.

- [ ] **Step 2A: Confirm scoped retirement**

Run exact `retire-v1 --confirm` with receipt and scoped manifest paths/digests. Monitor resumable state through completed. Verify selected v1 zero, v2/unrelated counts unchanged, maintenance released only after completed, app/browser/API smoke green.

- [ ] **Step 3A: Record completion**

Record receipt/selection/manifest digests, retirement row/state, deleted scoped counts, unchanged unrelated counts, and post-retirement smoke.

- [ ] **Step 1B: If declined, do not mutate v1**

Retain active v2 plus previous/v1 rollback data and both backup classes. Record the user's decision and no retirement command.

### Task 9: Merge Backend and Agent Branches to Main and Push

**Files:**
- No new feature edits; Git integration only.

- [ ] **Step 1: Rebase/merge with final verification**

Fetch remotes. Inspect `main` changes since branch creation. Resolve conflicts by preserving v2 contract and unrelated work; rerun affected suites. Do not force-push shared main.

- [ ] **Step 2: Merge backend branch**

Merge `codex/graph-lifecycle-v2-backend` into backend `main`, run backend/frontend smoke/build/gates on merge commit, then push.

- [ ] **Step 3: Merge agent branch**

Merge `codex/graph-lifecycle-v2-agent` into agent `main`, run agent G/V/sync/skills suites on merge commit, then push.

- [ ] **Step 4: Verify deployed commits match main**

Record remote main SHAs and deployed image/source commits. If merges changed release SHA, rebuild/redeploy and repeat smoke; do not claim completion with mismatch.

- [ ] **Step 5: Final completion report**

Report user-visible improvements, exact gates/results, backup/rollback state, v1 retirement decision, final SHAs, deployed health, known non-blocking limitations, and where operator documentation lives.

## Plan 6 Exit Gate

- All G01–G14, L01–L15, V01–V18, U01–U12 pass on final merge commits.
- All 12 live Carnovali conditions from design section 15.5 pass.
- `/graph` has no black screen, dead action, mixed version, 401/404/405/500, or uncaught console error.
- Graph and Wiki verification complete atomically end-to-end.
- DR and scoped rollback remain proven.
- Unrelated data and admin access are unchanged.
- User decision on v1 retirement is recorded and respected.
- Backend and agent `main` are pushed and deployed commits match them.
