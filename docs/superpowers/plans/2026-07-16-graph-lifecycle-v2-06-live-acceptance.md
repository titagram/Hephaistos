# Hades Graph Lifecycle v2 Live Acceptance and Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove the complete v2 release first in an isolated, resumable Symfony Demo environment; let the user test it manually; then, and only with explicit approval plus fresh DR evidence, promote the exact reviewed commits to production, decide scoped v1 retirement, and integrate Git histories.

**Architecture:** A checked-in acceptance harness creates a uniquely named Docker Compose project on the backend server with loopback-only ports, disposable PostgreSQL/Neo4j/Redis/artifact volumes, and no Traefik labels. A temporary Mac `HERMES_HOME` links one disposable Hades device to one disposable backend project/binding. A pinned Symfony Demo checkout supplies the real MVC request-lifecycle fixture; a tiny existing contract fixture may supply a deterministic uncertainty only if Symfony Demo has none. The standalone Codex plugin is tested in a fresh Codex task against that same disposable project. Production remains untouched until every isolated gate and review is green.

**Tech Stack:** Docker Compose, Laravel queues/scheduler, PostgreSQL, Neo4j, Hades CLI, React/Vitest/browser tooling, Git worktrees, the standalone Codex plugin, Restic/DR evidence.

## Global Constraints

- Inherit the master plan and current execution checkpoint. START GATE: C1, C2, C3, Checkpoint E, and Checkpoint F are green; Plans 1–5 are integrated/tested/pushed on exact recorded `main` SHAs with zero unresolved Critical/Important finding.
- Server repository is `/home/ubuntu/dev-sandbox`; never switch its production worktree merely to run acceptance. Task 1 develops the checked-in harness on `codex/graph-v2-p6-acceptance-harness` in `/home/ubuntu/graph-v2-acceptance/<RUN_ID>/harness-repo`. After that exact commit is reviewed, integrated, tested, and pushed to backend `main`, remove the clean harness worktree and create `/home/ubuntu/graph-v2-acceptance/<RUN_ID>/repo` detached at the recorded integration SHA. Tasks 2–9 use only that detached exact candidate.
- `RUN_ID` is exactly UTC `YYYYMMDDTHHMMSSZ-<8-random-lowercase-hex>`. Derive `RUN_SLUG` by ASCII-lowercasing `RUN_ID` and removing every non-alphanumeric character; the Compose project is exactly `hades_gv2_acc_<RUN_SLUG>`. Validate both forms before using them. Every container, network, volume, temporary path, project, binding, user, token, and report carries the canonical `RUN_ID`; Docker resource names use `RUN_SLUG` where their grammar requires it.
- The isolated PostgreSQL database is exactly `devboard_acceptance` inside its own disposable volume. No acceptance command may name the production database, production Docker volumes, production project/binding ULIDs, `home-sweet-home.cloud`, or the normal Mac Hades profile.
- Explicitly reject project `01KX8G47N6HK2AC4NSVS912ECJ`, Carnovali project/binding IDs, `/Users/gabriele/Dev/sinervis/carnovali`, and every existing backend project/binding from the run manifest. Carnovali is an optional later scale test, not this acceptance fixture.
- The acceptance frontend binds only to `127.0.0.1:<allocated-port>` and is reached through an SSH tunnel. Traefik stays separate and receives no label/router/config change.
- Probe occupied Docker/host ports before `up`; allocate from a configured acceptance-only range and persist the chosen mapping before container creation. A collision aborts; never stop/reconfigure another container to free a port.
- Every disposable Docker resource has label `com.hades.acceptance.run=<RUN_ID>`. Cleanup enumerates by that exact label and refuses an unlabeled or mismatched target. No `docker compose down -v`, prune, raw volume delete, or database reset may run without the acceptance Compose project and label guard.
- Mac Agent state lives only at `/private/tmp/hades-graph-v2-acceptance/<RUN_ID>/hermes-home`; the Symfony Demo checkout is pinned/read-only and outside the user's projects. Build/install the exact Agent candidate into a run-specific virtual environment; do not run `hades update` or alter the normal installation/profile.
- All credentials are random, run-scoped, excluded from Git/reports, and revoked before teardown. Automated verification credentials are device-bound/capability-scoped; any human retry token is separate and never passed to the worker.
- Before any production mutation, require a successful Restic snapshot less than 90 minutes old, a passed restore rehearsal, scoped v1 export where applicable, exact digests, baseline counts, and admin-login proof. If any restore/reset occurs, rerun the real user seeder and re-prove admin login before completion.
- Each task is time-boxed to 90 minutes unless it explicitly says 150. At 30 minutes without a new artifact, or after three repeats of one failure without a new hypothesis, stop with a resumable checkpoint. Do not compensate by weakening a gate.
- Implementation workers never integrate or push. For every component/evidence commit, a genuinely fresh coordinator Codex task receives the base SHA, candidate SHA, exact allowed-path list, required commands, and SHA-256 handoff digest. It verifies `BASE_SHA` is an ancestor of both clean pulled `main` and `CANDIDATE_SHA`, verifies the complete `BASE_SHA..CANDIDATE_SHA` path set, then integrates only with `git merge --no-ff --no-edit CANDIDATE_SHA`. Cherry-pick, squash, rebase, conflict resolution, and reimplementation are forbidden; any conflict stops. It proves the candidate remains an ancestor of the merge commit, tests that integration commit, and pushes. Record coordinator task ID/model/handoff digest/integration SHA in the run ledger. `git add .`, `git add -A`, and directory-root staging are forbidden.

---

### Task 0: Freeze Release Candidates and Acceptance Inputs (max 90 min)

**Create outside Git while credentials/resources are live:** Task 0 creates only provisional acceptance state at `/home/ubuntu/graph-v2-acceptance/<RUN_ID>/evidence/acceptance-run-manifest.json` plus an immutable `acceptance-inputs.json`; it reserves an empty mode-0700 `/home/ubuntu/graph-v2-release-control/<RUN_ID>/` directory but does **not** create a release manifest, command plan, or journal. Those final release-control inputs cannot be sealed until the Task 9 evidence integration SHA exists. All outside-Git files are mode 0600. Task 9 copies only sanitized acceptance evidence into Git and removes disposable acceptance state; final release-control is created at Task 10A and survives through rollback retention.

- [ ] Record exact backend/frontend, Agent, contract-lock, plugin, and Plan 5 DR commits/digests. Fetch/pull each clean `main` and prove it already contains every accepted task/slice commit; if any reviewed commit is still unintegrated, stop and use the normal coordinator integration boundary before acceptance rather than assembling an ad-hoc release branch. Record backend `main` only as immutable `pre_harness_backend_sha`; it is never replaced. The run manifest's separate all-null `backend_rc` record is filled atomically once, and only once, after Task 1's reviewed harness commit is integrated. No feature edit is allowed during this freeze.
- [ ] Freeze a Symfony Demo Git URL and 40-character commit SHA. Before accepting it, verify from source that `/en/blog/` resolves to `App\\Controller\\BlogController::index`; if not, stop and update the fixture expectation rather than silently selecting another route. Record license, commit, tree digest, expected primary route/symbol, and one protected/branched route when present.
- [ ] Capture a read-only inventory of production Compose projects, containers, networks, volumes, exposed ports, database name, project/binding IDs, and current Git SHAs. Store only identifiers/digests, never secrets. These values form a deny-list for the harness.
- [ ] Resolve and record absolute Compose-file paths, then run `docker compose -f ABS_BASE -f ABS_PROD config --services`. Bind exactly one discovered service to each required role `app`, `postgres`, `neo4j`, `redis`, `frontend`, and `graph_worker`; reject missing, duplicate, or guessed role mappings. Store the raw service list, closed role map, Compose config SHA-256, and file SHA-256 values in the manifest.
- [ ] Record immutable `pre_harness_backend_sha`, the other three candidate SHAs, Compose roles, and commands needed for isolated acceptance; require all five `backend_rc` fields to be null. Run the already-integrated Plan 5 helper exactly as `ops/acceptance/graph-v2-acceptance-state --inputs=ABS --manifest=ABS validate-bootstrap`, retain its canonical result, and seal both byte digests. Do not invent or pre-bind the harness integration SHA or final release-control bytes. A missing helper/schema or failed validation stops with `missing_acceptance_bootstrap_contract`.
- [ ] Refuse to proceed if a candidate worktree is dirty, a required commit is missing, a contract digest differs, or the newest verified DR status is already degraded.

### Task 1: Build and Prove the Isolated Acceptance Harness (max 150 min)

**Backend files:**
- Create `ops/acceptance/graph-v2-acceptance`.
- Create `ops/acceptance/docker-compose.graph-v2-acceptance.yaml`.
- Create `ops/acceptance/tests/test_graph_v2_acceptance_harness.py`.

The four acceptance schemas are immutable, already-reviewed inputs created and integrated by Plan 5 Task 7. Task 1 tests their real behavior but must not modify them. `acceptance-inputs` requires exactly: `schema="hades.graph_v2_acceptance_inputs.v1"`, `run_id`, `created_at`, `pre_harness_backend_sha`, `pre_harness_backend_artifact_sha256`, `candidate_shas:{frontend,agent,plugin}`, `candidate_artifact_sha256:{frontend,agent,plugin}`, `contract_lock_sha256`, `fixture:{repository_url,commit_sha,tree_sha256,license,primary_route,primary_symbol,branched_route|null}`, `compose:{absolute_files:[string],file_sha256:[{path,sha256}],config_sha256,services:[string],role_map:{app,postgres,neo4j,redis,frontend,graph_worker}}`, and `production_denylist_sha256`. Arrays are nonempty, duplicate-free, lexically sorted unless their order is explicitly command order; ULIDs/digests/SHAs/timestamps use closed patterns.

`acceptance-run-manifest` requires exactly: `schema="hades.graph_v2_acceptance_run_manifest.v1"`, all immutable input fields above, `state`, `updated_at`, `inactivity_deadline`, `backend_rc:{harness_candidate_sha|null,integration_sha|null,artifact_sha256|null,coordinator_handoff_sha256|null,integrated_at|null}`, `h1:{prepared_document_sha256|null,approval_digest|null,approved_at|null,operator|null}`, `operator_decision:{preview_sha256|null,decision:"pending|accepted|declined",decided_at|null,operator|null,decision_digest|null}`, `resources:{compose_project,containers,networks,volumes,loopback_ports}`, `identities:{company_id|null,user_id,project_id,workspace_binding_id,agent_id,device_id}`, `checkpoints:[{task,phase,status,started_at,finished_at|null,evidence_sha256|null}]`, `commands:[{argv:[string],exit_code,started_at,finished_at,stdout_sha256,stderr_sha256}]`, `evidence:[{kind,path_basename,sha256,created_at}]`, and `cleanup:{credentials_revoked_at|null,resources_removed_at|null,verified:boolean}`. Resource arrays contain closed `{id,name,label_sha256}` or `{service,host,port}` records and never credentials. The five `backend_rc` fields transition together from all-null to all-present only after the coordinator push; partial or repeated assignment is rejected. The decision transitions exactly once from pending/all-null to accepted-or-declined/all-present while holding the per-run lock. `prepare` additionally writes immutable mode-0600 `prepared-acceptance.json`, validated as a closed projection of run ID, immutable-input/Compose digests, the complete `backend_rc` record, selected ports/resource names, effective candidate SHAs, and preparation timestamp; it contains no field derived from its own digest. H1 binds this separate file, while the mutable run manifest records its digest after hashing.

`isolated-acceptance-preview` has the exact evidence fields defined in Plan 5, `cleanup_pending:true`, and no manifest digest or operator decision. `isolated-acceptance-pack` requires exactly: `schema="hades.graph_v2_isolated_acceptance_pack.v1"`, `run_id`, `acceptance_manifest_sha256`, `candidate_shas:{backend,frontend,agent,plugin}`, `candidate_artifact_sha256:{backend,frontend,agent,plugin}`, `contract_lock_sha256`, `operator_decision:{preview_sha256,decision:"accepted|declined",decided_at,operator,decision_digest}`, `plugin:{version,source_sha256,fresh_task_id}`, `fixture`, `commands`, `gate_reports:{agent,backend,verification,frontend,dr}`, `truth_store_digests:{manifest,postgres,neo4j,artifacts,wiki,verification}`, `browser_matrix:[{width,theme,reduced_motion,keyboard_only,result,evidence_sha256}]`, `reviews:[{kind,reviewed_sha,critical,important,minor,status}]`, `known_minor_risks:[string]`, `cleanup:{credentials_revoked_at,resources_removed_at,verified:true}`, and `generated_at`. Every digest-bearing nested record uses a 64-lower-hex pattern; every command is an argv array, never a shell string. The final pack requires completed cleanup and a terminal operator decision; its backend SHA/artifact must equal `backend_rc`, its other candidates/artifacts must equal immutable inputs, and `acceptance_manifest_sha256` must hash the post-cleanup manifest bytes. Schema tests reject missing/extra/nondeterministically ordered fields and scan serialized examples for secrets, cookies, absolute private source paths, and evidence excerpts.

**Interface:**

```text
ops/acceptance/graph-v2-acceptance \
  --run-id=RUN_ID \
  --repo-worktree=ABSOLUTE_REPO_PATH \
  --manifest=ABSOLUTE_MANIFEST_PATH \
  [--approval-file=ABSOLUTE_PATH] \
  [--decision-file=ABSOLUTE_PATH] \
  prepare|record-h1-approval|record-decision|up|status|stop|down|extend
```

`--approval-file` is required only by `record-h1-approval` and forbidden for every other verb. It must be a regular non-symlink mode-0600 file owned by the effective operator containing exactly `APPROVE H1 <RUN_ID> <PREPARED_DOCUMENT_SHA256>` plus one final LF. The command rehashes immutable `prepared-acceptance.json`, verifies the mutable manifest still references that digest and all preparation inputs match, validates the exact line, and atomically stores only approval digest/timestamp/operator/prepared-document digest in the mode-0600 run journal. `--decision-file` is required only by `record-decision`; it has the same ownership/mode/symlink/LF rules and contains exactly `ACCEPT ISOLATED CANDIDATE <RUN_ID> <PREVIEW_SHA256>` or `DECLINE ISOLATED CANDIDATE <RUN_ID> <PREVIEW_SHA256>`. The command rehashes the schema-valid immutable preview and atomically records only its digest, accepted/declined, timestamp, operator, and decision-line digest; the caller then removes the file. Replay, changed preview, or a second decision fails without mutation. `up` requires the current prepared-document and recorded approval digests plus unchanged preparation inputs; direct `up`, stale approval, or modified prepared inputs fails before Docker access. A later `prepare` replaces the prepared document and invalidates the previous H1. `--abandon-after-failure` is the sole other flag, is valid only with `down`, and requires an interactive exact-run confirmation; ordinary cleanup never uses it.

Every mutating verb in both this harness and the Plan 5 state helper takes the same stable per-run exclusive `.state.lock` before reading the manifest and holds it through durable journal update and its external mutation boundary. In particular, `up` holds the lock continuously from prepared-document/H1 revalidation through Docker resource creation and the terminal `running|failed_safe` journal commit; concurrent `prepare|record-h1-approval|extend|stop|down|record-backend-rc` block and then revalidate. Read-only `status` may take a shared lock. Lock acquisition has a bounded timeout and reports the owner/pid/verb without bypassing it; no command replaces or unlinks the lock file before final successful teardown.

Before creating a worktree or Docker resource, run this literal preflight in a fresh shell after exporting `RUN_ID`, `RUN_SLUG`, `BASE_COMPOSE`, `PROD_COMPOSE`, and `PRE_HARNESS_BACKEND_SHA` from the sealed Task 0 manifest:

```bash
set -euo pipefail
umask 077
: "${RUN_ID:?missing RUN_ID}"
: "${RUN_SLUG:?missing RUN_SLUG}"
: "${BASE_COMPOSE:?missing BASE_COMPOSE}"
: "${PROD_COMPOSE:?missing PROD_COMPOSE}"
: "${PRE_HARNESS_BACKEND_SHA:?missing PRE_HARNESS_BACKEND_SHA}"
python3 - "$RUN_ID" "$RUN_SLUG" <<'PY'
from datetime import datetime
import re
import sys

run_id, run_slug = sys.argv[1:]
match = re.fullmatch(r"(\d{8}T\d{6}Z)-([0-9a-f]{8})", run_id)
if match is None:
    raise SystemExit(64)
try:
    datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ")
except ValueError:
    raise SystemExit(64)
expected_slug = re.sub(r"[^a-z0-9]", "", run_id.lower())
if run_slug != expected_slug or not re.fullmatch(r"[a-z0-9]+", run_slug):
    raise SystemExit(64)
PY
test -f "$BASE_COMPOSE"
test -f "$PROD_COMPOSE"
git -C /home/ubuntu/dev-sandbox diff --quiet
git -C /home/ubuntu/dev-sandbox diff --cached --quiet
test -z "$(git -C /home/ubuntu/dev-sandbox ls-files --others --exclude-standard)"
test "$(git -C /home/ubuntu/dev-sandbox rev-parse HEAD)" = "$PRE_HARNESS_BACKEND_SHA"
test -z "$(docker ps -aq --filter label=com.hades.acceptance.run="$RUN_ID")"
test -z "$(docker network ls -q --filter label=com.hades.acceptance.run="$RUN_ID")"
test -z "$(docker volume ls -q --filter label=com.hades.acceptance.run="$RUN_ID")"
install -d -m 0700 "/home/ubuntu/graph-v2-acceptance/$RUN_ID/evidence"
docker compose -f "$BASE_COMPOSE" -f "$PROD_COMPOSE" config --services
sha256sum "$BASE_COMPOSE" "$PROD_COMPOSE" "/home/ubuntu/graph-v2-acceptance/$RUN_ID/evidence/acceptance-run-manifest.json" "/home/ubuntu/graph-v2-acceptance/$RUN_ID/evidence/acceptance-inputs.json"
```

Any nonzero command stops Task 1. Save stdout/digests outside Git; never “repair” preflight by deleting or stopping an unlabelled resource.

- [ ] RED tests must prove unique Compose project/name/labels, loopback-only dynamic ports, disposable named volumes for PostgreSQL/Neo4j/Redis/artifacts, no Traefik labels/network, production deny-list rejection, occupied-port abort, exact database guard, idempotent `prepare|record-h1-approval|up|status|stop|down|extend`, direct/stale/wrong-digest/wrong-mode/symlink/cross-run `up` rejection before Docker access, and label-scoped cleanup only. A deterministic two-process barrier test races `up` against `prepare`: exactly one linearized order is observed, `prepare` can never invalidate H1 between `up` validation and Docker mutation, and no unapproved resource is created. Test lock timeout/owner diagnostics and the shared lock protocol with `record-backend-rc`. `down` revokes credentials first and refuses when evidence has not been sealed unless `--abandon-after-failure` is explicitly confirmed.
- [ ] Run literal RED: `python3 -m pytest ops/acceptance/tests/test_graph_v2_acceptance_harness.py -q`; require failure only for missing harness behavior, never import/environment errors.
- [ ] Except for the two guarded flags defined above, the harness accepts only `--run-id`, `--repo-worktree`, `--manifest`, and the closed verb union. It generates a mode-0600 runtime env/journal outside Git, renders Compose config, records selected ports/resources, and runs `docker compose config` before mutation. It never interpolates shell fragments from the manifest.
- [ ] From clean pulled backend `main` at `pre_harness_backend_sha`, create branch `codex/graph-v2-p6-acceptance-harness` in `/home/ubuntu/graph-v2-acceptance/<RUN_ID>/harness-repo`. Run the harness there with both checked-in base/production Compose files plus the acceptance override; if actual service names differ from the plan, stop before `up` and amend the reviewed harness/tests—never guess at runtime.
- [ ] Split preparation from mutation. First run only `prepare`, seal immutable `prepared-acceptance.json`, and compute its SHA-256. Stop at human gate H1 and require the exact reply `APPROVE H1 <RUN_ID> <PREPARED_DOCUMENT_SHA256>`. Put only that line plus LF in a temporary owner-only mode-0600 non-symlink file, invoke `record-h1-approval --approval-file=ABS`, then delete the input file. Only a new shell invocation whose `up` revalidates the journal/prepared-document/manifest references may access Docker. A missing/mismatched approval leaves zero Docker resources and checkpoints `h1_pending`.
- [ ] Run literal GREEN and regression: `python3 -m pytest ops/acceptance/tests/test_graph_v2_acceptance_harness.py -q`, then `python3 -m pytest ops/acceptance/tests/test_graph_v2_acceptance_harness.py ops/release/tests/test_graph_v2_cutover.py ops/backup/tests/test_documented_commands.py -q`, then `python3 -m py_compile ops/acceptance/graph-v2-acceptance`. All must pass before runtime smoke, review, or staging; these exact commands go in the coordinator handoff.
- [ ] After valid H1, start one label-scoped harness smoke stack, then prove every container/volume/network belongs only to the acceptance Compose project, all published ports are loopback-only and previously free, production resources/counts are unchanged, and Traefik config has no new router. Save `docker compose config`, `ps`, health, labels, ports, and volume IDs in the run manifest, then stop/remove that smoke stack only through the label-guarded harness and prove no labeled resource remains.
- [ ] After focused GREEN and independent safety review, stage/verify/commit only the harness allowlist:

```bash
git add -- ops/acceptance/graph-v2-acceptance ops/acceptance/docker-compose.graph-v2-acceptance.yaml ops/acceptance/tests/test_graph_v2_acceptance_harness.py
git diff --cached --name-only
git diff --cached --check
git commit -m "test(graph): add isolated graph v2 acceptance harness"
```

The name output must equal those three paths. Seal a handoff containing base/candidate SHA, allowed names, exact test command/result, safety-review verdict, and SHA-256. A genuinely fresh coordinator task independently verifies it, integrates that exact commit into current backend `main`, reruns the focused harness smoke/config tests on the integration commit, and pushes. From that clean integration worktree, invoke the already-reviewed Plan 5 helper with `record-backend-rc` and all four literal digest/SHA options; it alone performs the all-null compare-and-set to the complete five-field `backend_rc` record. Then run the helper's read-only `validate-complete` verb; do not hand-edit JSON. The effective `backend_rc_sha` is `backend_rc.integration_sha`. Verify it contains the harness commit, remove the clean `harness-repo`, and run `git worktree add --detach /home/ubuntu/graph-v2-acceptance/<RUN_ID>/repo <backend_rc_sha>`.

From a fresh shell, execute these commands separately and literally (the manifest path is the same sealed run manifest used by Task 0):

```bash
/home/ubuntu/graph-v2-acceptance/$RUN_ID/repo/ops/acceptance/graph-v2-acceptance --run-id="$RUN_ID" --repo-worktree="/home/ubuntu/graph-v2-acceptance/$RUN_ID/repo" --manifest="/home/ubuntu/graph-v2-acceptance/$RUN_ID/evidence/acceptance-run-manifest.json" prepare
sha256sum "/home/ubuntu/graph-v2-acceptance/$RUN_ID/evidence/prepared-acceptance.json"
```

Record the resulting `DETACHED_PREPARED_DOCUMENT_SHA256` and obtain a **new** exact H1 line `APPROVE H1 <RUN_ID> <DETACHED_PREPARED_DOCUMENT_SHA256>`; the earlier harness-development H1 cannot satisfy it. Put only that line plus LF in an owner-only mode-0600 non-symlink temporary file `H1_APPROVAL_FILE`, run the following command, and delete the input file after success:

```bash
/home/ubuntu/graph-v2-acceptance/$RUN_ID/repo/ops/acceptance/graph-v2-acceptance --run-id="$RUN_ID" --repo-worktree="/home/ubuntu/graph-v2-acceptance/$RUN_ID/repo" --manifest="/home/ubuntu/graph-v2-acceptance/$RUN_ID/evidence/acceptance-run-manifest.json" --approval-file="$H1_APPROVAL_FILE" record-h1-approval
```

In another fresh shell, revalidate that digest and run:

```bash
/home/ubuntu/graph-v2-acceptance/$RUN_ID/repo/ops/acceptance/graph-v2-acceptance --run-id="$RUN_ID" --repo-worktree="/home/ubuntu/graph-v2-acceptance/$RUN_ID/repo" --manifest="/home/ubuntu/graph-v2-acceptance/$RUN_ID/evidence/acceptance-run-manifest.json" up
/home/ubuntu/graph-v2-acceptance/$RUN_ID/repo/ops/acceptance/graph-v2-acceptance --run-id="$RUN_ID" --repo-worktree="/home/ubuntu/graph-v2-acceptance/$RUN_ID/repo" --manifest="/home/ubuntu/graph-v2-acceptance/$RUN_ID/evidence/acceptance-run-manifest.json" status
```

Require all Task 1 invariants before Task 2. No production deploy occurs.

### Task 2: Initialize the Disposable Backend and Seed Users (max 90 min)

- [ ] Run normal forward `php artisan migrate --force` inside the acceptance `app` service against isolated `devboard_acceptance`; never run `migrate:fresh`. Resolve the repository's real user seeder class from source, run it, and prove the seeded admin can authenticate to the isolated frontend/API.
- [ ] Enable an isolated global `reason=cutover` maintenance window, retain its raw token only in the run secret file, run `hades:graph-v2:init-schema --maintenance-token=...`, verify exact definitions, then close the window only after Neo4j health. A backup token or production Neo4j endpoint must be rejected.
- [ ] Verify the dedicated `graph-v2-worker` uses queue `graph-v2`, `retry_after=1900`, worker timeout 1800, and job timeout 1740; verify scheduler/reconciler processes are healthy. Route/OpenAPI inspection must show v2 `POST graph/query`, no Hades `GET graph/traverse`, verification endpoints, and no direct Wiki verify mutation.
- [ ] Record migration list, schema definitions, seed command/result, admin smoke, routes, workers, scheduler, and unchanged production inventory. On failure, keep the isolated stack for evidence; do not touch production.

### Task 3: Create Run-Scoped Project, Binding, Agent, and Tokens (max 90 min)

- [ ] Use only an existing supported UI/API/Artisan bootstrap path; raw SQL is forbidden. Create one run-scoped company/user if multi-tenant support exists, one project `Graph v2 acceptance <RUN_ID>`, one workspace binding, one Hades agent/device, and capability grants `project_inspection`, Wiki population/read, `verify_project_graph`, and `verify_project_wiki` as required by the implemented policy.
- [ ] Persist generated ULIDs in the run manifest and assert none exists in the production deny-list. Create the separate admin/retry credential only when the retry smoke is selected; never grant its scope to the device token.
- [ ] Configure only the temporary run `HERMES_HOME` to the SSH-tunnelled acceptance URL/project/binding. Run status/profile/capability read-backs and prove the Agent sees exactly that project. If no supported bootstrap path exists, stop with a missing-interface finding; do not insert rows manually.

### Task 4: Import Pinned Symfony Demo Through Hades Agent (max 150 min)

- [ ] Build the exact Agent candidate wheel/source artifact, verify SHA/commit, install it in the run venv, and run parser canaries for PHP plus every detected supported language. Missing Tree-sitter/grammar is terminal and no upload begins.
- [ ] Clone/checkout Symfony Demo at the frozen SHA into the run workspace, make it read-only after dependency metadata is present, verify tree digest and the expected `/en/blog/` route/controller, and never modify its source/logbooks.
- [ ] Start `hades backend sync` from the fixture root with explicit elapsed-time and peak-RSS monitoring. Hard limit: 45 minutes and 4 GiB RSS for this bounded fixture; crossing either stops the process and records a performance blocker rather than continuing indefinitely.
- [ ] Once at least one chunk is acknowledged, terminate only the acceptance Agent process once, rerun the same command, and prove it resumes the same semantic import/missing chunks rather than starting conflicting work. Do not kill backend workers or production processes.
- [ ] Poll read-only import status until `validated` and publication `ready`. Require two bounded validation passes, one winning fenced projection incarnation, exact manifest/PostgreSQL/Neo4j counts/digests, working search index, no v1/local fallback, and no late-attempt mutation. Record timings/RSS/counts/IDs/digests without raw source or secrets.

### Task 5: Prove Lifecycle, Wiki, and Verification Workflows (max 150 min)

- [ ] Query through Hades v2 and dashboard v2 for `/en/blog/` and `App\\Controller\\BlogController::index`. Require exact/segment/symbol discovery, ordered Symfony request stages, controller/response terminal, branches/unknowns represented honestly, usable partial reasons, and callers/dependencies/impact as values or explicit unknown—not false zero.
- [ ] Run Wiki bootstrap from the Agent against the same project. Require human Markdown pages, machine ledgers outside Markdown, 1–80 claims per generated page, one request per revision, stable slug/CAS rerun, no overwrite of developer-provided content, and visible `verification_freshness`.
- [ ] Run `hades backend sync` with queued work and prove it reports only bounded counts/next command with no claim, model call, full payload, prompt mutation, or synthetic message.
- [ ] If Symfony Demo contains a valid current graph uncertainty, process exactly one with `verification work --once --domain graph`. If it contains none, load Plan 3's committed `.codex-artifacts/graph-v2/verification-live-smoke-handoff.json`, verify its Agent/backend/plugin/contract/fixture source-tree/manifest digests and exact expected uncertainty ID/reason code, run only its sealed index argv against the reviewed `tests/fixtures/hades/graph_v2/verification_uncertainty_project/`, and import the resulting bundle into a **second disposable project/binding** created by this run. Any mismatch stops; never substitute a hand-authored bundle or inject an artificial row into the Symfony project. Verify heartbeat/cancellation, structured result, immutable base digest, overlay provenance, and new projection only for verified/contradicted.
- [ ] Process exactly one Wiki item from the Symfony project. Require full immutable read snapshot, exact ledger, atomic completion/new fingerprints for verified/contradicted or quiet deferred, and absence of any direct Wiki mutation call. Then use a disposable item to race/supersede target completion and prove no domain effect/cache regression.
- [ ] Before draining, select one same-project verification item that remains readable after terminalization, prove `show` authorization, and seal its ULID as `EXPECTED_READABLE_WORK_ITEM_ID` in the outside-Git run manifest. Run `--all` on the remaining bounded queue only after `--once` passes. Require one-at-a-time claims, local same-scope lock, 900-second hard/300-second meaningful-progress deadlines, mandatory OS-containment/backend-fence teardown or quarantine proof, no same-run reclaim, ten-attempt server ceiling, resumable pagination, and no retry loop. Seal a safe verification report containing IDs/digests/verdict/state versions but no evidence excerpts.

### Task 6: Install and Test the Standalone Codex Plugin (max 90 min)

- [ ] Test `/Users/gabriele/plugins/hades-backend` at the exact candidate SHA; never edit installed cache. Install the strictly newer candidate through the personal marketplace and verify version/source digest.
- [ ] The implementation worker verifies install/version/static contracts, writes the expected plugin SHA/version and temporary acceptance profile/project identifiers to the outside-Git run manifest, then stops at `fresh_task_required`. It cannot validate its own immutable skill catalog.
- [ ] The coordinator/user opens one genuinely new Codex task and gives it this exact prompt, with placeholders filled from the sealed manifest: `Read-only Hades acceptance plugin smoke. Do not edit/install/configure or mutate state. Require hades-backend plugin EXPECTED_VERSION and EXPECTED_SHA256; require exactly hades-backend, hades-wiki-push, hades-wiki-verify, hades-graph-explore, hades-verify in this task catalog; run command -v hades. Set HERMES_HOME to exactly TEMP_HERMES_HOME and require its configured project equals EXPECTED_PROJECT_ID. Run exactly: hades backend status --json; hades backend verification status --json; hades backend verification list --limit 1 --json; hades backend verification show EXPECTED_READABLE_WORK_ITEM_ID --json; hades backend graph explore --route /en/blog/ --json. Never run retry, work, claim, heartbeat, completion, Wiki push, sync, configuration, or any mutation. Return JSON only with schema=hades.codex_plugin_acceptance_smoke.v1, task_id, plugin_version, plugin_source_sha256, skill_names, hades_executable, hermes_home, project_id, commands, sanitized_results, direct_http_or_v1_detected, passed, failure_reason.`
- [ ] Wait at most 30 minutes. Validate the closed result against expected SHA/version/project and store it at `/home/ubuntu/graph-v2-acceptance/<RUN_ID>/evidence/codex-plugin-acceptance-smoke.json`. Prove the plugin made no direct HTTP/private endpoint/v1 traversal/direct Wiki verify call and delegated to the installed CLI. A timeout, absent real task ID, cache-only inspection, wrong project, or incomplete JSON checkpoints `fresh_task_gate_pending` and blocks Task 7; it never becomes a silent pass.

### Task 7: Run Isolated Graph Explorer Browser Acceptance (max 150 min)

- [ ] Run frontend typecheck/tests/build for the exact candidate with `VITE_GRAPH_V2_MAINTENANCE=false`, then expose only the acceptance frontend's loopback port through an SSH tunnel. Do not configure Traefik or `home-sweet-home.cloud`.
- [ ] At desktop width, select the disposable project/scope, find `/en/blog/`, inspect lifecycle backbone/stage counts/branch/terminal/async summary, toggle stages, expand/load more, select drawer, reset, analyze the controller symbol, inspect value-or-unknown callers/dependencies/impact, and exercise advanced connection compare states. Technical details start collapsed; no inert Find Path control exists.
- [ ] At 390px and by keyboard/reduced-motion, verify rail/canvas/bottom sheet, focus return, text-equivalent graph, no overflow/black screen, AA distinctions, and deterministic layout. Rapidly change project/scope/entrypoint; old requests abort and stale context reloads once then reports a visible error.
- [ ] Require zero uncaught console exception and no unexpected 401/404/405/500. Store screenshot/network/console artifact digests with tokens/cookies redacted. Give the user the SSH-tunnel command and temporary URL for manual testing. The acceptance harness records a 12-hour **inactivity deadline**, atomically extending it on every recorded Task 7/8/9 action and while an explicit manual gate is pending. Expiry only marks `operator_attention_required` and makes later mutation commands refuse until an explicit `extend`; it never stops containers, revokes evidence, or triggers cleanup. Cleanup remains the explicit label-guarded Task 9 command only.

### Task 8: Run Final Gates, Reviews, and Truth-Store Comparison (max 150 min)

- [ ] Run G01–G14 on exact Agent SHA, L01–L15 on exact backend SHA, V01–V19 including plugin contract, U01–U12 on exact frontend SHA, Plan 5 DR gates, and prescribed platform regressions in deterministic shards. Never replace a timed-out shard with a smaller assertion set.
- [ ] Compare manifest, PostgreSQL, Neo4j, overlay memberships, Wiki ledgers, and active incarnation counts/digests. Verify acceptance users/projects/memory/Wiki/Kanban are internally consistent and production baseline/resources/counts are byte-for-byte or count-for-count unchanged.
- [ ] Run fresh spec, code-quality, security/concurrency/data-safety reviews. Explicitly search for dead v1 readers, unscoped query/delete, incarnation omission, false-zero coalesce, arbitrary overlay input, direct Wiki verify, prompt-cache mutation, inert UI, backup secret, premature maintenance release, Inertia import, and plugin HTTP/parser duplication. Zero Critical/Important is mandatory.
- [ ] Seal `/home/ubuntu/graph-v2-acceptance/<RUN_ID>/evidence/isolated-acceptance-pack.preview.json` with exact effective SHAs/artifact digests, commands/results, resource IDs, route/symbol lifecycle proof, queue/plugin/browser evidence, review verdicts, known Minor risks, and the intended cleanup/revocation procedure. This sanitized immutable user-decision preview carries `schema=hades.graph_v2_isolated_acceptance_preview.v1`, `cleanup_pending=true`, and no `acceptance_manifest_sha256`. Run the state helper with `--preview=ABS validate-preview`, then hash it for the Task 9 decision. While live credentials/resources exist, it remains outside Git and excludes secrets, cookies, evidence excerpts, and absolute private source paths.

### Task 9: User Decision, Credential Revocation, and Isolated Teardown (max 90 min)

- [ ] Present the temporary UI and concise schema-valid acceptance preview with its SHA-256. Ask whether the user accepts the isolated candidate as eligible for production preflight. Put the user's exact `ACCEPT ISOLATED CANDIDATE <RUN_ID> <PREVIEW_SHA256>` or `DECLINE ISOLATED CANDIDATE <RUN_ID> <PREVIEW_SHA256>` line plus LF in an owner-only mode-0600 non-symlink temporary decision file, invoke the harness with `--decision-file=ABS record-decision`, then remove it. The per-run lock makes the decision a one-time transition; a changed preview requires a new run, not a journal edit. This does not mutate production, does not replace Task 10's digest-bound P1 approval, and does **not** authorize v1 retirement.
- [ ] Regardless of promotion decision, revoke run-scoped admin/device/API tokens first and verify they return 401. Seal evidence. Run the real user seeder one final time in the disposable database and re-prove the disposable admin login before teardown; this satisfies the development-data safety rule even if a prior reset/restore occurred.
- [ ] Use only `ops/acceptance/graph-v2-acceptance --run-id=<RUN_ID> --repo-worktree=/home/ubuntu/graph-v2-acceptance/<RUN_ID>/repo --manifest=/home/ubuntu/graph-v2-acceptance/<RUN_ID>/evidence/acceptance-run-manifest.json down`; enumerate exact labels before/after and prove production resources unchanged. Remove the run Git worktree/temp Mac `HERMES_HOME` only after logs/reports are sealed. On failed cleanup, report exact labeled leftovers and stop—never prune globally.
- [ ] After revocation and successful label-scoped teardown, update `cleanup` under the per-run lock, fsync the post-cleanup manifest, and hash its final bytes. Build `isolated-acceptance-pack.json` only now from that immutable manifest plus the accepted/declined preview, including effective candidate artifact digests, terminal decision, and completed cleanup. Run `ops/acceptance/graph-v2-acceptance-state --inputs=ABS --manifest=ABS --pack=ABS validate-pack`; any mismatch stops before Git. Then create fresh branch `codex/graph-v2-p6-acceptance-evidence` from clean pulled backend `main` in a separate worktree. Copy only the validated sanitized final manifest and final pack to `.codex-artifacts/graph-v2/acceptance-run-manifest.json` and `.codex-artifacts/graph-v2/isolated-acceptance-pack.json`; run schema/digest/secret/path scans and an independent evidence review. Stage/verify/commit with exactly:

```bash
git add -- .codex-artifacts/graph-v2/acceptance-run-manifest.json .codex-artifacts/graph-v2/isolated-acceptance-pack.json
git diff --cached --name-only
git diff --cached --check
git commit -m "chore(graph): record isolated graph v2 acceptance"
```

The staged-name output must contain exactly those two files. Give the candidate/base SHAs, allowlist, tests, and handoff digest to a fresh coordinator task; only it integrates, smokes, and pushes that exact evidence commit. Preserve the outside-Git originals until the pushed files' SHA-256 values match; then remove only `/home/ubuntu/graph-v2-acceptance/<RUN_ID>` and the temporary Mac profile. Do not remove the reserved `/home/ubuntu/graph-v2-release-control/<RUN_ID>` directory. It is still empty at this point; Task 10A alone creates the final release manifest, command plan, and journal, which then survive through rollback retention.

### Task 10: Promote Exact Accepted Commits and Decide v1 Retirement (max 150 min plus user pauses)

#### Task 10A: Read-only production preflight and P1 (max 90 min plus user pause)

- [ ] The harness and sanitized evidence commits, like every accepted component slice, must already be integrated/tested/pushed incrementally. Fetch each current `main`, prove it contains every accepted task SHA and no unreviewed graph diff, and rebuild candidate artifacts without adding a late product/evidence merge. Verify the standalone plugin's separately versioned history; never place it in Agent core.
- [ ] Now—and not in Task 0—derive the final `hades.graph_v2_release_manifest.v1` from the immutable acceptance inputs, final Task 9 acceptance-pack/evidence integration SHA, exact current-main SHAs, coordinator integrations, rebuilt artifact digests, and the exact absolute-path/SHA-256 allowlist of every executable that any command-plan argv may launch. Require the acceptance decision to be `accepted`; a declined candidate cannot reach P1. Derive `hades.graph_v2_production_command_plan.v1` from that exact release-manifest digest using only literals, closed manifest placeholders, and the Plan 5 phase-gated journal-placeholder union; validate both against the checked-in Plan 5 schemas, canonicalize as UTF-8 JCS with one final LF, and atomically create mode-0600 `release-manifest.json`, `production-command-plan.json`, and the initial journal in the reserved release-control directory. A mismatch with any accepted evidence stops; these bytes are immutable after journal creation.
- [ ] Run only `ops/release/graph-v2-cutover --release-manifest=/home/ubuntu/graph-v2-release-control/<RUN_ID>/release-manifest.json --command-plan=/home/ubuntu/graph-v2-release-control/<RUN_ID>/production-command-plan.json preflight`. It must revalidate closed role mappings from `docker compose config --services`, exact SHAs/images/contracts/plugin, production deny-list/baseline/admin login, driver journal, available disk, and current DR status. It performs no migration, maintenance, export, deploy, restart, retirement, or restore.
- [ ] Seal `prepared-release.json`, compute `PREPARED_RELEASE_SHA256`, and show the user exact SHAs/images, planned argv arrays, baseline, and risks. Require the exact P1 response `APPROVE PRODUCTION PROMOTION <RUN_ID> <PREPARED_RELEASE_SHA256>`. Put only that exact line plus LF into a temporary owner-only non-symlink mode-0600 file and run the driver's `--approval-file=ABS record-promotion-approval`; then delete the input file. The driver validates/binds it and journals only digest/timestamp/operator. Missing/mismatched approval checkpoints `p1_pending` and stops with zero production mutation; nobody edits the journal manually.

#### Task 10B: Refresh DR, deploy, and smoke (max 150 min)

- [ ] In a new shell, revalidate recorded P1 plus both manifest digests, then run the full driver invocation ending in `refresh-dr`. This phase installs the reviewed `/etc/hades-backup/config.yaml`, owner/root modes and systemd credentials/units/timers for the first time; executes/verifies one live global-maintenance consistency set, restore rehearsal, and scoped v1 export; and requires snapshot age <90 minutes, exact baseline counts, admin login, Compose role map, and image/source SHAs. It uses the restore-target-only maintenance recovery CAS after loading the captured PostgreSQL dump. A failed/drifted check leaves the driver's resumable safe state and stops.
- [ ] Run the full driver invocation ending in `deploy`; it uses only reviewed argv arrays and exact current-main artifacts. Deploy the no-graph-request maintenance frontend **first**, then activate/verify graph-only cutover maintenance, apply forward migrations, token-bound Neo4j schema upgrade, dedicated workers/scheduler, Agent v2, and finally the v2 Graph Explorer frontend. Traefik is unchanged. The driver journal prevents a skipped/repeated unsafe phase and retains the release-control directory plus v2 restore-capable operator worktree/artifact.
- [ ] Run driver phase `smoke`: use read-only production v2 routes/UI, compare unrelated counts, prove deployed commits/images equal the release manifest, and release cutover maintenance only after health. If any restore/reset unexpectedly occurred, stop, obtain the separate disaster-recovery authority below, restore the verified consistency set, rerun the production user seeder, and verify admin login before any further action.
- [ ] If deploy or smoke fails before completed v1 retirement, invoke only the driver's pre-retirement `rollback` mode. It revalidates recorded P1/current manifest, retained v1 pointer/data, and pre-v2 artifact digests; under maintenance it redeploys exact pre-v2 images, verifies/repoints the retained v1 pointer when needed, runs authenticated v1/admin/unrelated-data smoke, and never calls scoped restore or rewrites graph data. If rollback is requested after a completed retirement, the same phase freezes post-retirement mode and requires recorded P2 plus the verified scoped export before executing the reviewed forward v1 restore followed by pre-v2 images/smoke. Both modes resume their own journal, close maintenance only at `rolled_back`, perform no whole-system DR, and need no new approval beyond explicit operator invocation because P1/P2 already authorized their respective safety fallback. A failed rollback remains fenced and escalates to the separately approved DR adapter; no freehand restore is allowed.

#### Task 10C: Independent v1-retirement decision (max 90 min plus user pause)

- [ ] Run only driver phase `retirement-dry-run`. It refreshes current selection/export/health evidence without deletion and seals the exact receipt, `selection_sha256`, receipt SHA-256, and verified export digest. Present those values and the resumable deletion stages to the user.
- [ ] Ask the distinct P2 question. If declined, run no confirm, retain rollback data, and record `retirement_declined`. If approved, require the exact response `APPROVE V1 RETIREMENT <RUN_ID> <RECEIPT_SHA256> <SELECTION_SHA256> <SCOPED_EXPORT_SHA256>`. Put only that exact line plus LF in a temporary owner-only non-symlink mode-0600 file, run the driver's `--approval-file=ABS record-retirement-approval`, then delete it. The earlier P1 or isolated-candidate approval cannot satisfy P2 and nobody edits the journal manually.
- [ ] In a new shell, revalidate P2 and run driver phase `retirement-confirm`; no freehand Artisan command is allowed. Verify bound-authority crash/resume semantics, selected v1 zero, v2/unrelated counts unchanged, and maintenance closes only after terminal health. Record final backend/Agent/plugin SHAs, deployed image digests, backup/rehearsal/export IDs, approvals, retirement/restore state, tests/reviews, known limitations, and operator docs.

A whole PostgreSQL/Neo4j/artifact restore is outside P1/P2 and requires the third exact line `APPROVE DISASTER RECOVERY <RUN_ID> <CONSISTENCY_SET_SHA256>` in the same temporary-file protocol through `record-dr-approval`. The separately documented DR adapter checks that recorded digest before mutation. Without it, the driver may report/recommend but cannot execute restore. Keep release-control until the rollback-retention decision is terminal; then archive its sanitized digest ledger and remove secrets/control files through the documented command. No Carnovali run is required for release completion; schedule it later only as an explicit elapsed/RSS-monitored scale test.

## Plan 6 Exit Gate

- G01–G14, L01–L15, V01–V19, U01–U12, and DR gates pass on the exact promoted commits.
- Isolated Symfony Demo import resumes correctly and proves route/controller lifecycle, honest uncertainty, Wiki, verification, plugin, desktop/mobile/accessibility, and zero unexpected browser/API errors.
- The isolated harness never touches production/Traefik and removes only run-labeled resources after credential revocation and evidence seal.
- Production was changed only after explicit promotion approval and fresh DR gates; deployed SHAs match pushed main.
- User retirement decision is separately recorded and respected; no `retire-v1 --confirm` ran without it.
- Unrelated data/resources and admin access are unchanged. If any restore/reset occurred, the appropriate user seeder and admin login were reverified.
- Carnovali remains an optional later monitored scale gate and no platform work is written to its logbook.
