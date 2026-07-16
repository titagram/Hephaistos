# Hades Graph v2 Backup, Rollback, and Cutover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Protect all Hades truth stores, prove restoration, provide scoped graph-v1 rollback, and execute the v2 cutover without endangering unrelated project data.

**Architecture:** Hourly Restic snapshots capture one graph-consistent PostgreSQL/Neo4j/artifact-storage set while graph-only maintenance freezes graph heads. Weekly disposable restore rehearsals prove all stores and queue reconstruction. A separate scoped v1 export plus resumable retirement/restore state machine supports feature rollback without whole-system restore.

**Tech Stack:** Bash/Python operator scripts, Restic, systemd, PostgreSQL `pg_dump`/`pg_restore`, Neo4j Admin 5.26.27 Community, Laravel Artisan, Docker Compose, SHA-256/JCS.

## Global Constraints

- Inherit `2026-07-16-graph-lifecycle-v2-master.md`.
- Work in `/home/ubuntu/dev-sandbox` on `codex/graph-lifecycle-v2-backend`.
- START GATE: Plans 1–4 pass locally; graph maintenance service exists and frontend maintenance copy is implemented.
- Backup secrets live only in a root-readable mode-0600 systemd credential/environment file; never Git/Laravel/Hades config.
- A failed maintenance, Neo4j stop/dump/start/health, or token-close step fails safe and leaves graph maintenance active.
- Redis/queue payloads are not truth; restore rehearsal starts disposable Redis empty and proves reconciliation.
- Whole-system restore is disaster recovery only with global freeze and explicit human approval.
- Scoped v1 retirement `--confirm` is forbidden until the user explicitly accepts live v2.

---

## File Structure

- `ops/backup/hades-backup`: locked consistency-set capture + Restic snapshot/retention/status.
- `ops/backup/hades-restore-rehearsal`: disposable weekly restore and integrity report.
- `ops/backup/hades-backup.example.yaml`: non-secret paths, images, retention, health, safety configuration.
- `ops/systemd/hades-backup-hourly.service|timer`: hourly persistent randomized execution.
- `ops/systemd/hades-backup-rehearsal.service|timer`: weekly rehearsal.
- `docs/operations/disaster-recovery.md`: installation, status, alerting, restore, manual recovery.
- Backend export/retire/restore services/commands/models/migrations: scoped feature rollback.

---

### Task 1: Implement Graph-Consistent Hourly Backup Capture

**Files:**
- Create: `ops/backup/hades-backup`
- Create: `ops/backup/hades-backup.example.yaml`
- Test: `ops/backup/tests/test_hades_backup.py`

**Interfaces:**
- Lock: `/run/lock/hades-backup.lock`.
- Staging: mode-0700 timestamped directory.
- Status: atomically written `/var/lib/hades-backup/status.json`.
- Restic tags: `hades`, `hourly`, timestamp.

- [ ] **Step 1: Add RED command-sequence/fail-safe tests**

Use command fakes to assert exact sequence: maintenance on → PostgreSQL dump/list → Neo4j stop → system+neo4j dump → Neo4j start/health → maintenance off with token/health → artifact snapshot/inventory → JCS manifest/tree digest → Restic snapshot → verify snapshot → Restic check → retention → status. Inject failure at each edge and assert no later unsafe step, nonzero exit, status safe failure code, staging retained when diagnostic value exists, and maintenance never falsely closes.

- [ ] **Step 2: Run RED**

Run: `python3 -m pytest ops/backup/tests/test_hades_backup.py -q`

- [ ] **Step 3: Implement configuration and preflight**

Example config has exact non-secret keys:

```yaml
schema: hades.backup_config.v1
backend_root: /home/ubuntu/dev-sandbox
compose_file: /home/ubuntu/dev-sandbox/docker-compose.yml
staging_root: /var/lib/hades-backup/staging
status_file: /var/lib/hades-backup/status.json
artifact_storage_path: /home/ubuntu/dev-sandbox/storage/app/artifacts
neo4j_image: neo4j:5.26.27-community
neo4j_service: neo4j
neo4j_health_query: RETURN 1
retention: {hourly: 72, daily: 35, monthly: 12}
maintenance_ttl_seconds: 3600
max_success_age_seconds: 5400
```

The script reads `NEO4J_USERNAME` and `NEO4J_PASSWORD` only from the root-readable systemd credential/environment file and constructs the health command as an argument array at runtime. The tracked YAML and generated manifest store no credential or credential placeholder.

- [ ] **Step 4: Implement exact capture sequence**

Use argument arrays/no shell interpolation for paths. Store the one-time maintenance token only in memory or mode-0600 staging file excluded from manifest/Restic input. PostgreSQL uses custom format and `pg_restore -l`. Neo4j dumps both `system` and `neo4j`. Artifact inventory records relative key/path, bytes, optional ETag, SHA-256. Common manifest binds image versions, commits, timestamps, and digests.

- [ ] **Step 5: Run GREEN and commit**

```bash
python3 -m pytest ops/backup/tests/test_hades_backup.py -q
git add ops/backup/hades-backup ops/backup/hades-backup.example.yaml ops/backup/tests/test_hades_backup.py
git commit -m "feat(ops): capture graph-consistent restic backups"
```

### Task 2: Add Hourly systemd Units and Retention Safety

**Files:**
- Create: `ops/systemd/hades-backup-hourly.service`
- Create: `ops/systemd/hades-backup-hourly.timer`
- Test: `ops/backup/tests/test_systemd_units.py`

**Interfaces:**
- Timer: `OnCalendar=hourly`, `Persistent=true`, `RandomizedDelaySec=300`.
- Service cannot overlap and uses root-readable credentials/config.

- [ ] **Step 1: Add RED unit tests**

Parse units and assert schedule, persistence, random delay, `flock`/script lock, credentials/environment file path, hardening, nonzero propagation, and no secret literal.

- [ ] **Step 2: Run RED**

Run: `python3 -m pytest ops/backup/tests/test_systemd_units.py -q`

- [ ] **Step 3: Implement units**

```ini
[Timer]
OnCalendar=hourly
Persistent=true
RandomizedDelaySec=300
Unit=hades-backup-hourly.service

[Install]
WantedBy=timers.target
```

Service executes `/home/ubuntu/dev-sandbox/ops/backup/hades-backup --config /etc/hades-backup/config.yaml`. Retention runs only after new snapshot presence and `restic check` metadata pass. A failed run never prunes the last success.

- [ ] **Step 4: Run GREEN and commit**

```bash
python3 -m pytest ops/backup/tests/test_systemd_units.py -q
git add ops/systemd/hades-backup-hourly.service ops/systemd/hades-backup-hourly.timer ops/backup/tests/test_systemd_units.py
git commit -m "feat(ops): schedule protected hourly backups"
```

### Task 3: Implement Weekly Disposable Restore Rehearsal

**Files:**
- Create: `ops/backup/hades-restore-rehearsal`
- Create: `ops/systemd/hades-backup-rehearsal.service`
- Create: `ops/systemd/hades-backup-rehearsal.timer`
- Test: `ops/backup/tests/test_hades_restore_rehearsal.py`

**Interfaces:**
- Select newest successful consistency set.
- Disposable resources require explicit `hades_restore_rehearsal=<run-id>` label/tag.
- Production host/database/volume identifiers are rejected.

- [ ] **Step 1: Add RED restore/safety tests**

Test Restic restore, manifest/tree digest, disposable PostgreSQL restore, both Neo4j loads with recorded image, artifact restore and retained-object resolution, counts/digests, empty Redis reconciliation/no duplicate completed work, tagged cleanup only, production target rejection, and retained report.

- [ ] **Step 2: Run RED**

Run: `python3 -m pytest ops/backup/tests/test_hades_restore_rehearsal.py -q`

- [ ] **Step 3: Implement rehearsal and units**

The weekly timer uses a fixed low-traffic calendar and `Persistent=true`. Rehearsal report includes snapshot ID, manifest digest, per-store restore targets/counts/digests, artifact resolution proof, queue-reconcile result, cleanup result, started/completed timestamps, and pass/fail code.

- [ ] **Step 4: Run GREEN and commit**

```bash
python3 -m pytest ops/backup/tests/test_hades_restore_rehearsal.py -q
git add ops/backup/hades-restore-rehearsal ops/systemd/hades-backup-rehearsal.service ops/systemd/hades-backup-rehearsal.timer ops/backup/tests/test_hades_restore_rehearsal.py
git commit -m "feat(ops): rehearse complete Hades restores weekly"
```

### Task 4: Implement Scoped Graph-v1 Export and Smoke Restore

**Files:**
- Create: `backend/app/Services/Graph/V2/GraphV1ExportService.php`
- Create: `backend/app/Console/Commands/ExportCanonicalGraphV1.php`
- Test: `backend/tests/Feature/Graph/GraphV1ExportCommandTest.php`

**Interfaces:**

```text
php artisan hades:graph-v2:export-v1 --project=PROJECT_ULID --output=/backups/PROJECT/v1-graph
```

- [ ] **Step 1: Add RED scope/manifest tests**

Test export only selected project/scope v1 projection/attempt/artifact/search rows and Neo4j namespace; referenced blob bytes through `ArtifactStorageService`; JCS JSONL; table/family/ID/count/scope/version/index/constraint/file digests; whole export digest; pre-v2 image/commit/agent artifact; reject unreachable/cross-project record; disposable PostgreSQL schema+Neo4j import compares all counts/digests.

- [ ] **Step 2: Run RED**

Run: `docker compose exec -T app php artisan test backend/tests/Feature/Graph/GraphV1ExportCommandTest.php`

- [ ] **Step 3: Implement read-only export/service, run GREEN, commit**

```bash
docker compose exec -T app php artisan test backend/tests/Feature/Graph/GraphV1ExportCommandTest.php
git add backend/app/Services/Graph/V2/GraphV1ExportService.php backend/app/Console/Commands/ExportCanonicalGraphV1.php backend/tests/Feature/Graph/GraphV1ExportCommandTest.php
git commit -m "feat(graph): export scoped v1 rollback sets"
```

### Task 5: Implement Receipt-Bound Resumable v1 Retirement

**Files:**
- Create: migration `2026_07_16_000700_create_graph_v1_retirements_table.php`
- Create: `backend/app/Models/GraphV1Retirement.php`
- Create: `backend/app/Services/Graph/V2/GraphV1RetirementService.php`
- Create: `backend/app/Console/Commands/RetireCanonicalGraphV1.php`
- Test: `backend/tests/Feature/Graph/GraphV1RetirementCommandTest.php`

**Interfaces:**

```text
php artisan hades:graph-v2:retire-v1 --project=PROJECT_ULID --dry-run
php artisan hades:graph-v2:retire-v1 --project=PROJECT_ULID --confirm --receipt=ABSOLUTE_PATH --receipt-sha256=HEX --scoped-backup-manifest=ABSOLUTE_PATH --scoped-backup-sha256=HEX
```

- [ ] **Step 1: Add RED receipt/precondition tests**

Test closed JCS receipt, random nonce/time excluded from selection recompute, exact whole-file/selection digest, required dry-run, project mismatch, changed selection, missing ready v2 per v1 scope, invalid/unrehearsed backup, selected v2/non-graph record rejection, advisory lock, unrelated counts.

- [ ] **Step 2: Add RED crash-resume tests**

Inject crash in `prepared`, after Neo4j batch, `neo4j_deleted`, and `postgres_deleted`. Assert state only advances after verification, rerun resumes without repeating verified step, maintenance remains active after error, audit error recorded, v2/unrelated unchanged, blob bytes retained.

- [ ] **Step 3: Run RED**

Run: `docker compose exec -T app php artisan test backend/tests/Feature/Graph/GraphV1RetirementCommandTest.php`

- [ ] **Step 4: Implement exact state machine**

States and transitions are exactly `prepared → neo4j_deleted → postgres_deleted → completed`; `restored` is set only by restore service. Disable project maintenance only on `completed` or verified `restored`.

- [ ] **Step 5: Run GREEN and commit**

```bash
docker compose exec -T app php artisan test backend/tests/Feature/Graph/GraphV1RetirementCommandTest.php
git add backend/database/migrations/2026_07_16_000700_create_graph_v1_retirements_table.php backend/app/Models/GraphV1Retirement.php backend/app/Services/Graph/V2/GraphV1RetirementService.php backend/app/Console/Commands/RetireCanonicalGraphV1.php backend/tests/Feature/Graph/GraphV1RetirementCommandTest.php
git commit -m "feat(graph): retire scoped v1 data resumably"
```

### Task 6: Implement Scoped v1 Restore and Rollback Verification

**Files:**
- Create: `backend/app/Services/Graph/V2/GraphV1RestoreService.php`
- Create: `backend/app/Console/Commands/RestoreCanonicalGraphV1.php`
- Test: `backend/tests/Feature/Graph/GraphV1RestoreCommandTest.php`

**Interfaces:**

```text
php artisan hades:graph-v2:restore-v1 --project=PROJECT_ULID --scoped-backup-manifest=PATH --scoped-backup-sha256=HEX
```

- [ ] **Step 1: Add RED restore/collision tests**

Test exact manifest/project/digest, scoped rows/blobs/search/Neo4j only, collision with unrelated/v2 refusal, exact active pointer restoration, count/digest compare, maintenance active until smoke verification, retirement `restored`, idempotent rerun, and unrelated data counts.

- [ ] **Step 2: Run RED**

Run: `docker compose exec -T app php artisan test backend/tests/Feature/Graph/GraphV1RestoreCommandTest.php`

- [ ] **Step 3: Implement, run GREEN, commit**

```bash
docker compose exec -T app php artisan test backend/tests/Feature/Graph/GraphV1RestoreCommandTest.php
git add backend/app/Services/Graph/V2/GraphV1RestoreService.php backend/app/Console/Commands/RestoreCanonicalGraphV1.php backend/tests/Feature/Graph/GraphV1RestoreCommandTest.php
git commit -m "feat(graph): restore scoped v1 rollback sets"
```

### Task 7: Document Installation, Monitoring, Recovery, and Traefik Boundary

**Files:**
- Create: `docs/operations/disaster-recovery.md`
- Modify: backend/frontend operator README sections
- Modify: existing project Traefik guidance only to link, not reconfigure
- Test: `ops/backup/tests/test_documented_commands.py`

- [ ] **Step 1: Add RED documentation command tests**

Extract command blocks and assert referenced scripts/units/Artisan commands/options exist. Assert docs include 90-minute alert threshold, credential mode 0600, manual stuck-maintenance recovery, last-success status, retention, weekly report, DR vs feature rollback, seeder/admin-login rule, and Traefik-separate statement.

- [ ] **Step 2: Run RED**

Run: `python3 -m pytest ops/backup/tests/test_documented_commands.py -q`

- [ ] **Step 3: Write operator runbook, run GREEN, commit**

```bash
python3 -m pytest ops/backup/tests/test_documented_commands.py -q
git add docs/operations/disaster-recovery.md ops/backup/tests/test_documented_commands.py README.md frontend/README.md docs
git commit -m "docs(ops): document Hades backup and graph rollback"
```

### Task 8: Install and Prove One Real Backup/Rehearsal Before Cutover

**Files:**
- Create outside Git: `/etc/hades-backup/config.yaml`
- Create outside Git: root-readable credential/environment file mode 0600
- Create artifact report: `.codex-artifacts/graph-v2/dr-readiness.json`

- [ ] **Step 1: Preflight non-destructively**

Record current backend/frontend/agent commits and image digests, PostgreSQL/Neo4j versions, artifact disk path, user/project/memory/Wiki/Kanban counts, and admin login smoke. Verify `restic snapshots`, available space, Docker health, and maintenance command on/off against a short operator window.

- [ ] **Step 2: Install config/units and execute backup**

Copy example config, set mode/root ownership, load credentials, install units, `systemctl daemon-reload`, enable timers, start one backup service manually. Capture snapshot ID and `/var/lib/hades-backup/status.json`.

- [ ] **Step 3: Execute real disposable restore rehearsal**

Run the rehearsal service manually. Verify PostgreSQL, both Neo4j DBs, artifact resolution, empty-Redis reconciliation, digest/count equality, and cleanup. Retain report.

- [ ] **Step 4: Create scoped v1 export and smoke restore**

For project `01KXJD0SV73EBGWKNE2EK3M4KD`, create the scoped export and restore it into disposable PostgreSQL/Neo4j targets. Record manifest and whole-export digests; do not retire anything.

- [ ] **Step 5: Verify data unchanged and record readiness**

Compare all unrelated counts and admin login. `dr-readiness.json` includes snapshot ID, report paths/digests, scoped export manifest/digest, pre-v2 commits/images, timestamp, and `ready:true` only when all checks pass.

- [ ] **Step 6: Commit only the non-secret readiness record**

```bash
git add .codex-artifacts/graph-v2/dr-readiness.json
git commit -m "chore(ops): record verified graph v2 recovery readiness"
```

## Plan 5 Exit Gate

- One real hourly-style snapshot exists and Restic sees it.
- One complete disposable restore rehearsal passed and its report is retained.
- One scoped v1 export passed disposable smoke restore.
- `status.json` is fresh and safe failure behavior was exercised.
- No secret is in Git or manifest.
- Production unrelated counts and admin login are unchanged.
- v1 retirement has not been confirmed.
