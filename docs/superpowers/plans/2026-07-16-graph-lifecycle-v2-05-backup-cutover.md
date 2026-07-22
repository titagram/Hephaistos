# Hades Graph v2 Backup, Rollback, and Cutover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Protect all Hades truth stores, prove restoration, provide scoped graph-v1 rollback, and execute the v2 cutover without endangering unrelated project data.

**Architecture:** Hourly Restic snapshots capture one graph-consistent PostgreSQL/Neo4j/artifact-storage set while graph-only maintenance freezes graph heads. Weekly disposable restore rehearsals prove all stores and queue reconstruction. A separate scoped v1 export plus resumable retirement/restore state machine supports feature rollback without whole-system restore.

**Tech Stack:** Bash/Python operator scripts, Restic, systemd, PostgreSQL `pg_dump`/`pg_restore`, Neo4j Admin 5.26.27 Community, Laravel Artisan, Docker Compose, SHA-256/JCS.

## Global Constraints

- Inherit `2026-07-16-graph-lifecycle-v2-master.md`.
- Work in `/home/ubuntu/dev-sandbox`; each task starts on a fresh checkpoint-recorded `codex/graph-v2-p5-ops-task-N` branch from then-current clean, pulled `main`, after the accepted predecessor was reviewed, integrated, tested, and pushed. Do not revive the historical catch-all branch.
- START GATE: C1, C2, C3, and Checkpoint E are green; Plans 1–4 are integrated, tested, reviewed with zero unresolved Critical/Important, and pushed on the exact recorded `main` SHAs. The graph maintenance service, central artifact-storage guard/inventory seam, and frontend maintenance copy all exist before any live operation.
- Backup secrets live only in a root-readable mode-0600 systemd credential/environment file; never Git/Laravel/Hades config.
- A failed maintenance, lease drain, PostgreSQL verification, Neo4j stop/dump/start/health, artifact inventory/verification, common-manifest seal, or token-close step fails safe and leaves graph maintenance active.
- Global backup maintenance fences every `ArtifactStorageService` delete/replace/cleanup path until the common manifest is sealed. Immutable uploads may write bytes before their owning DB transaction, but missing bytes referenced by the PostgreSQL snapshot fail the run.
- Redis/queue payloads are not truth; restore rehearsal starts disposable Redis empty and proves reconciliation.
- Whole-system restore is disaster recovery only with global freeze and explicit human approval.
- Scoped v1 retirement `--confirm` is forbidden until the user explicitly accepts live v2.
- Export, retirement, and restore use only a typed, project/reason/operation/generation-bound `MaintenanceAuthority`; an ordinary maintenance token/off command is never a crash-recovery mechanism and cannot close a bound window.
- Every task worker commits only its allowed files. A fresh coordinator task verifies ancestry, allowlisted names, diff, tests, and handoff digest before integrating/pushing that exact commit. `git add .`, `git add -A`, and directory-root staging are forbidden.

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
- Create: `backend/app/Services/Graph/ArtifactStorageMaintenanceGuard.php`
- Create: `backend/app/Services/Graph/GraphArtifactInventoryService.php`
- Modify: `backend/app/Services/ArtifactStorageService.php`
- Test: `backend/tests/Feature/Graph/ArtifactStorageMaintenanceGuardTest.php`
- Test: `backend/tests/Architecture/ArtifactStorageMutationBoundaryTest.php`
- Create: `ops/backup/hades-backup`
- Create: `ops/backup/hades-backup.example.yaml`
- Test: `ops/backup/tests/test_hades_backup.py`

**Interfaces:**
- Lock: `/run/lock/hades-backup.lock`.
- Staging: mode-0700 timestamped directory.
- Status: atomically written `/var/lib/hades-backup/status.json`.
- Restic tags: `hades`, `hourly`, timestamp.
- Inventory record: exactly `{relative_key,bytes,etag|null,sha256}`, streamed in relative-key order.

- [ ] **Step 1: Add RED central storage-boundary tests**

Exercise every public `ArtifactStorageService` delete, replace, move-overwrite, and cleanup path. A global backup window must throw before any destructive call; immutable create is legal only when absent or byte-identical. Task 1 deliberately implements no bound-authority bypass—the type does not exist until Task 4. Stream inventory with traversal/duplicate/missing/mismatched-key fixtures, require every PostgreSQL-reachable graph object, and permit unrelated extras. The architecture test scans production PHP and rejects raw destructive filesystem/object-store calls outside `ArtifactStorageService` and the explicitly named but not-yet-enabled scoped restore adapter.

- [ ] **Step 2: Add RED command-sequence/fail-safe tests**

Use command fakes to assert exact sequence: allocate random `consistency_set_id` → maintenance on with that ID in safe window metadata → drain graph read/mutation leases → PostgreSQL dump/list → Neo4j stop → system+neo4j dump → Neo4j start/health → artifact snapshot/inventory → verify every PostgreSQL-reachable graph object → JCS manifest/tree digest seal → maintenance off with token/health → Restic snapshot → verify snapshot → Restic check → retention → status. The common manifest must bind consistency-set ID, captured maintenance-window ID/reason/start time/token hash (never raw token), and every store digest so a restored target can identify the transient captured window. Assert the off call is impossible before the sealed-manifest marker. Inject failure at each edge and assert no later unsafe step, nonzero exit, status safe failure code, staging retained when diagnostically useful, and maintenance never falsely closes.

- [ ] **Step 3: Run RED**

```bash
docker compose exec -T app php artisan test tests/Feature/Graph/ArtifactStorageMaintenanceGuardTest.php tests/Architecture/ArtifactStorageMutationBoundaryTest.php
python3 -m pytest ops/backup/tests/test_hades_backup.py -q
```

- [ ] **Step 4: Implement central guard/inventory and configuration preflight**

`ArtifactStorageMaintenanceGuard` is invoked *inside* every destructive public storage method before its first mutation; callers cannot opt out. In Task 1 it reads ordinary/global maintenance context and rejects destruction during global backup. `GraphArtifactInventoryService` validates safe relative keys, streams in deterministic order without loading all bytes/rows into memory, hashes exact bytes, and cross-checks the complete PostgreSQL-reachable key set. The backup script invokes this same backend inventory adapter; it does not implement a second filesystem walker with different semantics. Task 4 alone extends this guard with the typed exact-operation authorization path and its tests.

Example config has exact non-secret keys:

```yaml
schema: hades.backup_config.v1
backend_root: /home/ubuntu/dev-sandbox
compose_files:
  - /home/ubuntu/dev-sandbox/docker-compose.devboard.yaml
  - /home/ubuntu/dev-sandbox/docker-compose.devboard.prod.yaml
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

- [ ] **Step 5: Implement exact capture sequence**

Use argument arrays/no shell interpolation for paths. Before mutation, resolve the configured Compose files with `docker compose ... config --services`; stop on missing app/PostgreSQL/Neo4j services rather than guessing names. Store the one-time maintenance token only in memory or a mode-0600 staging file excluded from manifest/Restic input. PostgreSQL uses custom format and `pg_restore -l`. Neo4j dumps both `system` and `neo4j`. While maintenance remains active, call the central inventory path to record relative key, bytes, optional ETag, and SHA-256 and verify all graph-reachable object keys from the captured PostgreSQL state. The common manifest binds image versions, commits, timestamps, every per-store digest, and the sealed tree digest. Only then close maintenance. Unreferenced extra immutable-upload bytes are allowed; a referenced missing/mismatched byte fails closed.

- [ ] **Step 6: Run GREEN and commit exact allowlist**

```bash
docker compose exec -T app php artisan test tests/Feature/Graph/ArtifactStorageMaintenanceGuardTest.php tests/Architecture/ArtifactStorageMutationBoundaryTest.php
python3 -m pytest ops/backup/tests/test_hades_backup.py -q
git add -- backend/app/Services/Graph/ArtifactStorageMaintenanceGuard.php backend/app/Services/Graph/GraphArtifactInventoryService.php backend/app/Services/ArtifactStorageService.php backend/tests/Feature/Graph/ArtifactStorageMaintenanceGuardTest.php backend/tests/Architecture/ArtifactStorageMutationBoundaryTest.php ops/backup/hades-backup ops/backup/hades-backup.example.yaml ops/backup/tests/test_hades_backup.py
git diff --cached --name-only
git commit -m "feat(ops): capture graph-consistent restic backups"
```

The staged-name output must equal those eight paths exactly.

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
- Create: `backend/app/Services/Graph/RestoredBackupMaintenanceRecoveryService.php`
- Create: `backend/app/Console/Commands/RecoverRestoredGraphBackupWindow.php`
- Test: `backend/tests/Feature/Graph/RestoredBackupMaintenanceRecoveryTest.php`
- Create: `ops/backup/hades-restore-rehearsal`
- Create: `ops/systemd/hades-backup-rehearsal.service`
- Create: `ops/systemd/hades-backup-rehearsal.timer`
- Test: `ops/backup/tests/test_hades_restore_rehearsal.py`

**Interfaces:**
- Select newest successful consistency set.
- Disposable resources require explicit `hades_restore_rehearsal=<run-id>` label/tag.
- Production host/database/volume identifiers are rejected.
- Recovery command: `php artisan hades:graph-v2:recover-restored-backup-window --manifest=ABS --manifest-sha256=HEX --restore-target=ABS_MODE_0600_DESCRIPTOR`.

- [ ] **Step 1: Add RED restore/safety tests**

Test Restic restore, manifest/tree digest, disposable PostgreSQL restore, both Neo4j loads with recorded image, artifact restore and retained-object resolution, counts/digests, empty Redis reconciliation/no duplicate completed work, tagged cleanup only, production target rejection, and retained report. The restored PostgreSQL snapshot contains the captured active global `reason=backup` row and a fixture verification item with an active specialist fence/current execution epoch. Before reconciliation or worker start, run the recovery command and prove it alone can close that row and rotate copied verification ownership without lost raw tokens: exclusive global session lock; regular non-symlink owner-only mode-0600 restore-target descriptor; explicit disposable/approved-DR target identity; exact manifest bytes/digest, consistency-set ID, window ID/reason/start/token hash; all three restored-store digests; Neo4j health; atomic verification-epoch increment plus ascending copied-item reset/audit; then one window-closing audited CAS. The copied item is queued or exhausted according to its retained attempt count, its evidence/result remains unchanged, and stale source-epoch heartbeat/fence-clear/completion all fail before domain effect. Wrong/live-production target, another window, missing digest, unhealthy store, already-rotated-to-another-epoch, or already-changed row fails closed. No generic tokenless maintenance-off or live-production epoch-rotation path exists.

- [ ] **Step 2: Run RED**

```bash
docker compose exec -T app php artisan test tests/Feature/Graph/RestoredBackupMaintenanceRecoveryTest.php
python3 -m pytest ops/backup/tests/test_hades_restore_rehearsal.py -q
```

- [ ] **Step 3: Implement rehearsal and units**

The weekly timer uses a fixed low-traffic calendar and `Persistent=true`. `RestoredBackupMaintenanceRecoveryService` has no HTTP binding and accepts no ordinary maintenance token; the command is its sole operator adapter and consumes the closed restore-target descriptor plus sealed manifest. Rehearsal recovers the captured transient window and invokes Plan 3 `VerificationExecutionEpochService::rotateRestoredTarget()` before starting workers/reconciliation. The epoch/item reset and maintenance close occur under the same exclusive global recovery authority, are separately forward-marked for crash resume, and are idempotent only for the exact consistency set. Its report includes snapshot ID, manifest digest, recovered window/audit IDs, old/new verification epoch, reset/queued/exhausted item counts, stale-authority rejection proof, per-store restore targets/counts/digests, artifact resolution proof, queue-reconcile result, cleanup result, started/completed timestamps, and pass/fail code.

- [ ] **Step 4: Run GREEN and commit**

```bash
docker compose exec -T app php artisan test tests/Feature/Graph/RestoredBackupMaintenanceRecoveryTest.php
python3 -m pytest ops/backup/tests/test_hades_restore_rehearsal.py -q
git add -- backend/app/Services/Graph/RestoredBackupMaintenanceRecoveryService.php backend/app/Console/Commands/RecoverRestoredGraphBackupWindow.php backend/tests/Feature/Graph/RestoredBackupMaintenanceRecoveryTest.php ops/backup/hades-restore-rehearsal ops/systemd/hades-backup-rehearsal.service ops/systemd/hades-backup-rehearsal.timer ops/backup/tests/test_hades_restore_rehearsal.py
git diff --cached --name-only
git diff --cached --check
git commit -m "feat(ops): rehearse complete Hades restores weekly"
```

The staged-name output must equal those seven paths exactly.

### Task 4: Implement Scoped Graph-v1 Export and Smoke Restore

**Files:**
- Create: `backend/database/migrations/2026_07_16_000675_add_bound_maintenance_authority_and_graph_v1_exports.php`
- Create: `backend/app/Models/GraphV1Export.php`
- Create: `backend/app/Services/Graph/MaintenanceAuthority.php`
- Modify: `backend/app/Services/Graph/GraphMaintenanceService.php`
- Modify: `backend/app/Services/Graph/ArtifactStorageMaintenanceGuard.php`
- Modify: `backend/app/Services/ArtifactStorageService.php`
- Create: `backend/app/Services/Graph/V2/GraphV1ExportService.php`
- Create: `backend/app/Console/Commands/ExportCanonicalGraphV1.php`
- Test: `backend/tests/Feature/Graph/BoundMaintenanceAuthorityTest.php`
- Modify: `backend/tests/Feature/Graph/ArtifactStorageMaintenanceGuardTest.php`
- Test: `backend/tests/Feature/Graph/GraphV1ExportCommandTest.php`

**Interfaces:**

```text
php artisan hades:graph-v2:export-v1 --project=PROJECT_ULID --output=/backups/PROJECT/v1-graph
```

- [ ] **Step 1: Add RED bound-authority tests**

Migration adds monotonic window `scope_generation`, all-or-none owner kind/operation ID, authority generation/hash/heartbeat, and the durable `graph_v1_exports` state machine. Test this exact internal interface:

```php
beginBoundProjectOperation($projectId, 'retirement', $kind, $operationId, $ttl, $initialize): MaintenanceAuthority
resumeBoundProjectOperation($projectId, 'retirement', $kind, $operationId, $expectedScopeGeneration, $validateOwner): MaintenanceAuthority
transferBoundProjectOperation($authority, $nextKind, $nextOperationId, $transition): MaintenanceAuthority
withinAuthorizedRead($authority, $callback): mixed
withinAuthorizedMutation($authority, $callback): mixed
completeBoundProjectOperation($authority, $requireNeo4jHealthy, $finalize): void
```

Kinds are only `v1_export|v1_retirement|v1_restore`. `MaintenanceAuthority` carries project/window/reason/kind/operation/scope generation/authority generation/expiry and a process-only random 256-bit raw token; DB stores only its SHA-256. Test wrong reason/project/kind/operation/generation/hash/expired owner, stale-token fencing, TTL resume only for the exact nonterminal operation/digests, generation increment/token rotation, transfer, ordinary-off refusal, and verified terminal close. The separate audited abandon command closes only a read-only `v1_export` after full pre-state equality and `external_mutation_started_at=null`; it rejects retirement/restore unconditionally. Prove schema/HTTP/general graph/other-project bypass is impossible.

Extend the Task 1 storage guard only here: a valid bound retirement/restore authority may mutate only its exact manifest-selected project graph keys and exact forward stage; export stays read-only. Wrong project/kind/operation/generation/token/key is rejected before storage mutation. Re-run every Task 1 guard and architecture test.

Using two connections, assert begin/resume/transfer/complete lock shared global session → exclusive project session → transaction window → operation → pointer/domain and perform no external I/O in the transaction. Authorized work locks shared global → shared project → exclusive operation → short authority verification → existing reference lock → external I/O → short fenced stage CAS. Same-authority nesting reuses context; widening/cross-project/kind switching is rejected.

- [ ] **Step 2: Add RED scope/snapshot/manifest tests**

Test exact states `prepared→postgres_captured→neo4j_captured→artifacts_captured→sealed→verified`. Begin bound project maintenance and drain graph leases before capture. PostgreSQL capture uses one `REPEATABLE READ` snapshot and streams selected project/scope v1 projection/attempt/artifact/search/pointer rows. Under the same authority capture matching Neo4j namespace and referenced blobs through `GraphArtifactInventoryService`/guarded storage. Manifest binds export ID, window/scope/authority generations, snapshot ID, JCS JSONL table/family/IDs/counts/scope/version/index/constraint/file digests, whole export digest, and pre-v2 image/commit/Agent artifact. Reject unreachable/cross-project record, changed digest on resume, and stale owner. Crash before each state CAS; pre-seal failures leave maintenance active and resume the same export. Disposable PostgreSQL/Neo4j/artifact restore compares every count/digest before `verified`; only then `completeBoundProjectOperation` closes the export window.

- [ ] **Step 3: Run RED**

Run: `docker compose exec -T app php artisan test tests/Feature/Graph/BoundMaintenanceAuthorityTest.php tests/Feature/Graph/ArtifactStorageMaintenanceGuardTest.php tests/Architecture/ArtifactStorageMutationBoundaryTest.php tests/Feature/Graph/GraphV1ExportCommandTest.php`

- [ ] **Step 4: Implement authority and resumable export**

Every authority heartbeat/stage/final CAS matches window ID, scope generation, authority generation, token hash, owner kind, and operation ID. Resume validates the exact nonterminal export and immutable selection/output/deployment digests before rotation. The command is only an argument adapter and never serializes the raw token. Export external calls run inside authorized read while session locks remain held; each stage is committed in a separate short fenced transaction. A sealed export is immutable; only successful disposable restore makes it `verified`, and its terminal finalize closes the bound export window.

- [ ] **Step 5: Run GREEN and commit exact allowlist**

```bash
docker compose exec -T app php artisan test tests/Feature/Graph/BoundMaintenanceAuthorityTest.php tests/Feature/Graph/ArtifactStorageMaintenanceGuardTest.php tests/Architecture/ArtifactStorageMutationBoundaryTest.php tests/Feature/Graph/GraphV1ExportCommandTest.php
git add -- backend/database/migrations/2026_07_16_000675_add_bound_maintenance_authority_and_graph_v1_exports.php backend/app/Models/GraphV1Export.php backend/app/Services/Graph/MaintenanceAuthority.php backend/app/Services/Graph/GraphMaintenanceService.php backend/app/Services/Graph/ArtifactStorageMaintenanceGuard.php backend/app/Services/ArtifactStorageService.php backend/app/Services/Graph/V2/GraphV1ExportService.php backend/app/Console/Commands/ExportCanonicalGraphV1.php backend/tests/Feature/Graph/BoundMaintenanceAuthorityTest.php backend/tests/Feature/Graph/ArtifactStorageMaintenanceGuardTest.php backend/tests/Feature/Graph/GraphV1ExportCommandTest.php
git diff --cached --name-only
git commit -m "feat(graph): export scoped v1 rollback sets"
```

The staged-name output must equal those eleven paths exactly. `ArtifactStorageMutationBoundaryTest.php` is rerun but was already committed by Task 1 and is not restaged unless its RED test exposes a required architecture-list update; if that happens, amend this allowlist through review before editing.

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

Test closed JCS receipt, random nonce/time excluded from selection recompute, exact whole-file/selection digest, required dry-run, project mismatch, changed selection, missing ready v2 per v1 scope, invalid/unrehearsed or non-`verified` export, selected v2/non-graph record rejection, unrelated counts. Migration/model persist sealed export ID plus bound window/scope/authority generations and operation ID. `--confirm` must begin a new exact `v1_retirement` bound operation only after revalidating the sealed export and current receipt selection, or resume that same retirement after TTL; an ordinary token/off path is rejected.

- [ ] **Step 2: Add RED crash-resume tests**

Inject crash in `prepared`, after the durable `external_mutation_started` marker, after each Neo4j batch but before its completed-progress CAS, `neo4j_deleted`, and `postgres_deleted`. Assert every batch records fenced started/completed ordinal plus pre/post-state digest, state only advances after verification, rerun safely repeats/reconciles an incomplete idempotent batch without repeating a verified step, scope/authority generation rotates after TTL, and the stale process cannot heartbeat/delete/advance/transfer/close. Maintenance remains bound after error/token loss; abandon-open is rejected for retirement; only resume or transfer to restore/approved DR is allowed. Audit error is recorded, v2/unrelated are unchanged, and blob bytes remain. Race exporter/retirer/restore and assert the total session/advisory/domain lock order has no deadlock and only one owner.

- [ ] **Step 3: Run RED**

Run: `docker compose exec -T app php artisan test tests/Feature/Graph/GraphV1RetirementCommandTest.php`

- [ ] **Step 4: Implement exact state machine**

States and transitions are exactly `prepared → neo4j_deleted → postgres_deleted → completed`; `restored` is set only by the Task 6 restore service. All external work runs through `withinAuthorizedMutation()` and every mutation marker/progress/state CAS includes window/scope/authority/token/kind/operation fencing. `prepared` marks each selected v1 Neo4j batch before deletion, records completion digest after it, and proves zero; `neo4j_deleted` applies the same discipline to selected PostgreSQL v1 metadata/search rows and proves zero; `postgres_deleted` rechecks manifest/namespaces/unrelated counts. Only the terminal transaction plus `completeBoundProjectOperation(requireNeo4jHealthy=true)` may close maintenance. A failure leaves the last proven state and bound window active; recovery uses `resumeBoundProjectOperation`, never a recovered raw token or abandon-open.

- [ ] **Step 5: Run GREEN and commit**

```bash
docker compose exec -T app php artisan test tests/Feature/Graph/GraphV1RetirementCommandTest.php
git add -- backend/database/migrations/2026_07_16_000700_create_graph_v1_retirements_table.php backend/app/Models/GraphV1Retirement.php backend/app/Services/Graph/V2/GraphV1RetirementService.php backend/app/Console/Commands/RetireCanonicalGraphV1.php backend/tests/Feature/Graph/GraphV1RetirementCommandTest.php
git diff --cached --name-only
git commit -m "feat(graph): retire scoped v1 data resumably"
```

The staged-name output must equal those five paths exactly.

### Task 6: Implement Scoped v1 Restore and Rollback Verification

**Files:**
- Create: migration `2026_07_16_000725_create_graph_v1_restores_table.php`
- Create: `backend/app/Models/GraphV1Restore.php`
- Create: `backend/app/Services/Graph/V2/GraphV1RestoreService.php`
- Create: `backend/app/Console/Commands/RestoreCanonicalGraphV1.php`
- Test: `backend/tests/Feature/Graph/GraphV1RestoreCommandTest.php`

**Interfaces:**

```text
php artisan hades:graph-v2:restore-v1 --project=PROJECT_ULID --scoped-backup-manifest=PATH --scoped-backup-sha256=HEX
```

- [ ] **Step 1: Add RED restore/collision tests**

Test exact manifest/project/export/selection digest, scoped rows/blobs/search/Neo4j only, collision with unrelated/v2 refusal, and absent-or-byte-identical idempotency. The durable restore states are exactly `prepared→artifacts_restored→postgres_restored→neo4j_restored→pointer_restored→smoke_verified→completed`. Start by transferring a nonterminal retirement authority or beginning a new bound `v1_restore` operation for a completed retirement. Before every external phase/batch persist its fenced started marker/ordinal/pre-state digest and afterward its completed/post-state digest. Crash before/after every marker, mutation, and stage CAS; resume only after TTL with rotated authority and prove the stale owner cannot write/close. Abandon-open is always rejected. Require exact pointer restoration, count/digest comparison, Neo4j health, authenticated v1 API/UI smoke, unrelated-data invariants, retirement `restored`, and maintenance active through `smoke_verified`.

- [ ] **Step 2: Run RED**

Run: `docker compose exec -T app php artisan test tests/Feature/Graph/GraphV1RestoreCommandTest.php`

- [ ] **Step 3: Implement exact forward restore**

Persist window/scope/authority generations, owner operation ID, retirement/export IDs, and immutable digests in `graph_v1_restores`. Restore guarded artifacts first, then PostgreSQL, Neo4j, pointer, and smoke; each external phase uses `withinAuthorizedMutation()` and a separate fenced CAS. Never overwrite different bytes/rows/namespaces. Preserve the v2 operator artifact/worktree until restore reaches `completed`; do not deploy the restore command away first. Only the final transaction marks the retirement `restored`, and only `completeBoundProjectOperation(requireNeo4jHealthy=true)` reopens graph access.

- [ ] **Step 4: Run GREEN and commit exact allowlist**

```bash
docker compose exec -T app php artisan test tests/Feature/Graph/GraphV1RestoreCommandTest.php
git add -- backend/database/migrations/2026_07_16_000725_create_graph_v1_restores_table.php backend/app/Models/GraphV1Restore.php backend/app/Services/Graph/V2/GraphV1RestoreService.php backend/app/Console/Commands/RestoreCanonicalGraphV1.php backend/tests/Feature/Graph/GraphV1RestoreCommandTest.php
git diff --cached --name-only
git commit -m "feat(graph): restore scoped v1 rollback sets"
```

The staged-name output must equal those five paths exactly.

### Task 7: Document Installation, Monitoring, Recovery, and Traefik Boundary

**Files:**
- Create: `ops/release/graph-v2-cutover`
- Create: `ops/release/schemas/graph-v2-release-manifest.schema.json`
- Create: `ops/release/schemas/graph-v2-production-command-plan.schema.json`
- Create: `ops/acceptance/schemas/acceptance-inputs.schema.json`
- Create: `ops/acceptance/schemas/acceptance-run-manifest.schema.json`
- Create: `ops/acceptance/schemas/isolated-acceptance-preview.schema.json`
- Create: `ops/acceptance/schemas/isolated-acceptance-pack.schema.json`
- Create: `ops/acceptance/graph-v2-acceptance-state`
- Create: `ops/release/tests/test_graph_v2_cutover.py`
- Create: `docs/operations/disaster-recovery.md`
- Modify: backend/frontend operator README sections
- Modify: existing project Traefik guidance only to link, not reconfigure
- Test: `ops/backup/tests/test_documented_commands.py`

**Release-driver interface:**

```text
ops/release/graph-v2-cutover \
  --release-manifest=ABSOLUTE_PATH \
  --command-plan=ABSOLUTE_PATH \
  [--approval-file=ABSOLUTE_PATH] \
  preflight|record-promotion-approval|refresh-dr|deploy|smoke|rollback|retirement-dry-run|record-retirement-approval|retirement-confirm|record-dr-approval|status|resume
```

`--approval-file` is required only by the three `record-*` phases and forbidden otherwise. It must be a regular, non-symlink, mode-0600 file owned by the effective operator and contain exactly one UTF-8 approval line plus one final LF; after successful recording the caller securely removes that input file. The journal stores only approval kind, SHA-256 of the exact line bytes without LF, timestamp, effective OS operator identity, and the bound digests—never the raw line.

Inputs are validated against the two checked-in JSON Schema 2020-12 documents, whose root and every nested object set `additionalProperties:false`, then serialized as exact RFC-8785/JCS plus one final LF:

- `hades.graph_v2_release_manifest.v1` contains exactly `schema`, `run_id`, `created_at`, `candidate_shas` (`backend`,`frontend`,`agent`,`plugin`), `candidate_artifact_sha256`, `contract_lock_sha256`, `accepted_task_commits`, `coordinator_integrations`, `compose` (`absolute_files`,`file_sha256`,`config_sha256`,`services`,`role_map`), `executable_allowlist`, `fixture`, nullable `uncertainty_fixture`, `acceptance_manifest_sha256`, `acceptance_pack_sha256`, `production_baseline_sha256`, and `plan5_tooling_gate_sha256`.
- `hades.graph_v2_production_command_plan.v1` contains exactly `schema`, `run_id`, `release_manifest_sha256`, and `commands`. `commands` has exactly `preflight`, `refresh_dr`, `maintenance_frontend`, `migrate`, `neo4j_schema`, `workers_scheduler`, `agent`, `final_frontend`, `smoke`, `retirement_dry_run`, `retirement_confirm`, `status`, and `rollback`; each value is a non-empty array of non-empty argv arrays with no shell metacharacter interpretation, secret literal, relative executable, or unvalidated placeholder.

Closed nested shapes are exact. `candidate_shas` is four 40-lower-hex strings; `candidate_artifact_sha256` is four 64-lower-hex strings. `accepted_task_commits` is a lexically sorted, nonempty, duplicate-free array of `{repository:"agent|backend|plugin",plan:1..6,task:string,slice:string|null,candidate_sha:40-hex,integration_sha:40-hex}`. `coordinator_integrations` is a chronologically sorted array of `{repository,task_id,model,handoff_sha256,candidate_sha,merge_sha,pushed_at}` with closed enum/patterns. `compose` contains nonempty absolute normalized files, lexically sorted `{path,sha256}` digests, one config digest, sorted unique services, and role map exactly `{app,postgres,neo4j,redis,frontend,graph_worker}` whose values must appear once in services. `executable_allowlist` is a lexically path-sorted, nonempty, duplicate-free array of closed `{path,sha256}` objects; each path is absolute, normalized, regular, non-symlink, root/operator-owned, and non-group/world-writable, and its exact bytes are rehashed before every phase. `fixture` is exactly `{repository_url,commit_sha,tree_sha256,license,primary_route,primary_symbol,branched_route}`. `uncertainty_fixture`, when non-null, is exactly `{agent_subject_sha,path,source_tree_sha256,index_argv,manifest_sha256,expected_uncertainty_id,expected_reason_code}`. Every remaining `*_sha256` is 64 lower hex; `run_id` and UTC timestamp use the Plan 6 closed patterns.

Each command-plan phase value is an ordered nonempty array of argv arrays. Every argv array begins with an exact `executable_allowlist.path`; the driver rehashes that executable against its allowlisted digest before launch. Following strings are closed literal arguments, `${manifest.<closed.path>}` placeholders, or one of these phase-gated journal placeholders: `rollback` may use `${journal.refresh_dr.consistency_set_path}`, `${journal.refresh_dr.consistency_set_sha256}`, `${journal.refresh_dr.scoped_export_path}`, and `${journal.refresh_dr.scoped_export_sha256}`; `retirement_confirm|rollback` may use `${journal.retirement.receipt_path}`, `${journal.retirement.receipt_sha256}`, and `${journal.retirement.selection_sha256}`; `rollback` may additionally use `${journal.preflight.pre_v2_backend_artifact}`, `${journal.preflight.pre_v2_frontend_artifact}`, `${journal.preflight.pre_v2_agent_artifact}`, and their three `*_sha256` siblings. No other journal namespace/path is legal. A journal placeholder resolves only after its producing phase is terminal-success, while holding the release-control lock, and only if the referenced regular non-symlink file/digest still matches the journal; otherwise the phase stops before subprocess creation. Empty strings, environment assignments, redirections, control operators, command substitution, relative paths, secrets, duplicate phase arrays, cross-phase/unknown placeholders, and argv construction outside this resolver are invalid. JSON-Schema tests plus semantic tests freeze ordering, phase-to-placeholder mapping, role/path resolution, JCS bytes, executable digests, and `release_manifest_sha256` equality.

Both live in `/home/ubuntu/graph-v2-release-control/<RUN_ID>/` (directory mode 0700, files 0600), include no credential, and are bound by their exact byte SHA-256 in the driver journal. The release manifest is a separate sanitized derivative of the acceptance manifest, not an alias or mutable copy of it.

Task 7 also owns the four JSON Schema 2020-12 acceptance contracts consumed before any Plan 6 implementation work can begin. Their roots and every nested object set `additionalProperties:false`:

- `hades.graph_v2_acceptance_inputs.v1` is immutable and contains exactly `schema`, `run_id`, `created_at`, `pre_harness_backend_sha`, `pre_harness_backend_artifact_sha256`, `candidate_shas:{frontend,agent,plugin}`, `candidate_artifact_sha256:{frontend,agent,plugin}`, `contract_lock_sha256`, `fixture`, `compose`, and `production_denylist_sha256`.
- `hades.graph_v2_acceptance_run_manifest.v1` contains all immutable input fields plus `state`, `updated_at`, `inactivity_deadline`, a one-time `backend_rc:{harness_candidate_sha,integration_sha,artifact_sha256,coordinator_handoff_sha256,integrated_at}` record whose five nullable fields transition atomically from all-null to all-present after the Task 1 coordinator integration, `h1`, `operator_decision:{preview_sha256|null,decision:"pending|accepted|declined",decided_at|null,operator|null,decision_digest|null}`, `resources`, `identities`, `checkpoints`, `commands`, `evidence`, and `cleanup`. A second or partial `backend_rc` assignment is invalid; the decision transitions once from pending/all-null to accepted-or-declined/all-present.
- `hades.graph_v2_isolated_acceptance_preview.v1` contains the effective candidate SHAs/artifact digests, contract/plugin/fixture/commands/gates/truth-store/browser/review evidence, known risks, exact cleanup plan, `cleanup_pending:true`, and generation time. It has no manifest digest, operator decision, credential literal, or claim that teardown completed.
- `hades.graph_v2_isolated_acceptance_pack.v1` contains the final effective `candidate_shas:{backend,frontend,agent,plugin}` and `candidate_artifact_sha256:{backend,frontend,agent,plugin}`, where the backend values equal the completed run manifest's `backend_rc.integration_sha` and `backend_rc.artifact_sha256`, plus `operator_decision` and the final artifact/contract/gate/truth-store/browser/review/cleanup evidence defined by Plan 6. It may never substitute `pre_harness_backend_sha` or its artifact digest for the effective backend release candidate.

The schema fixtures and semantic tests freeze the null-to-complete `backend_rc` transition, SHA/digest/ULID/timestamp patterns, closed field sets, deterministic ordering, JCS bytes, secret scanning, and the equality relationships above. Plan 6 Task 0 only instantiates and validates these already-integrated contracts; Plan 6 Task 1 must not create or mutate their definitions.

The read-mostly state helper is the only writer of the one-time `backend_rc` transition:

```text
ops/acceptance/graph-v2-acceptance-state \
  --inputs=ABSOLUTE_PATH \
  --manifest=ABSOLUTE_PATH \
  [--pack=ABSOLUTE_PATH] \
  [--preview=ABSOLUTE_PATH] \
  [--repo-worktree=ABSOLUTE_PATH] \
  [--harness-candidate-sha=SHA] \
  [--integration-sha=SHA] \
  [--artifact-sha256=DIGEST] \
  [--handoff-sha256=DIGEST] \
  validate-bootstrap|record-backend-rc|validate-complete|validate-preview|validate-pack
```

The helper and the later acceptance harness share one stable per-run lock at `/home/ubuntu/graph-v2-acceptance/<RUN_ID>/.state.lock`, opened with no-follow/create-exclusive-safe semantics, mode 0600, correct owner, and an exclusive OS advisory lock before reading mutable state. They hold it through validation, temporary-file fsync, atomic replace, directory fsync, and postcondition reread. The lock file is never replaced while the run exists. `validate-bootstrap` is read-only and may use a shared lock; it requires no preview/pack/repository/RC arguments, validates both bootstrap documents, requires all `backend_rc` fields null, and verifies immutable-field equality plus JCS/digests. `record-backend-rc` requires `--repo-worktree` and all four RC arguments, takes the exclusive lock, revalidates the inputs and all-null manifest, requires that repository to be clean at the integration SHA, and proves the harness candidate is its ancestor before atomically rewriting the mode-0600 manifest with all five RC fields present and no other semantic change. It refuses symlinks, wrong owner/mode, partial/repeated/cross-run updates, dirty/wrong-HEAD repositories, ancestry failure, or digest mismatch. `validate-complete` is read-only, rejects preview/pack/repository/RC arguments, and requires the same immutable-field equality plus a fully populated, internally consistent `backend_rc`. `validate-preview` is read-only, requires only `--preview`, and proves its effective candidate/artifact/contract fields and evidence prerequisites equal the completed manifest while cleanup/decision remain pending. `validate-pack` is read-only, requires only `--pack`, and proves the pack's effective backend/artifact, operator decision, cleanup state, final manifest digest, and immutable fields equal the completed post-cleanup manifest. The helper accepts no shell fragments and emits a canonical machine-readable result.

- [ ] **Step 1: Add RED release-driver tests**

Validate all six closed schemas, JCS/byte digests, every required key/argv constraint, executable allowlist/digests, phase-gated journal placeholder resolution, acceptance `backend_rc` all-null-to-all-present compare-and-set semantics, preview/final effective-backend/artifact equality, and mismatched run/manifest rejection. Run a real two-process race proving exactly one different `record-backend-rc` contender succeeds and the loser observes the winner without overwriting it. Execution uses `shell=False` and never evaluates strings. Test atomic mode-0600 journal writes, exact release-manifest/command-plan digests, forward-only phases, resume from every crash point, refusal to skip a phase, and all `record-*` file ownership/mode/symlink/LF/raw-string/digest rules. Promotion approval must equal `APPROVE PRODUCTION PROMOTION <RUN_ID> <PREPARED_RELEASE_SHA256>`; retirement approval must equal `APPROVE V1 RETIREMENT <RUN_ID> <RECEIPT_SHA256> <SELECTION_SHA256> <SCOPED_EXPORT_SHA256>`; DR approval must equal `APPROVE DISASTER RECOVERY <RUN_ID> <CONSISTENCY_SET_SHA256>`. A wrong/replayed/cross-run approval leaves the journal unchanged. `deploy`, `retirement-confirm`, and any documented DR restore adapter refuse unless their exact recorded approval and current bound digests match. Before completed retirement, `rollback` requires recorded P1, retained/healthy v1 data and pre-v2 artifacts; it performs pointer/image rollback without invoking scoped restore. After completed retirement, it additionally requires recorded P2 plus the sealed scoped export and uses the forward restore. Both reject after rollback retention closes. Any drift, stale backup, unhealthy Neo4j, failed seeder/admin check, or unknown service/argument aborts without advancing the journal.

- [ ] **Step 2: Add RED documentation command tests**

Extract command blocks and assert referenced scripts/units/Artisan commands/options exist. Assert docs include 90-minute alert threshold, credential mode 0600, manual stuck-maintenance recovery, bound-authority generations/token rotation/owner inspection, the distinct audited abandonment procedure, last-success status, retention, weekly report, DR vs feature rollback, forward restore stages, preserved v2 restore operator artifact, seeder/admin-login rule, and Traefik-separate statement.

- [ ] **Step 3: Run RED**

Run: `python3 -m pytest ops/release/tests/test_graph_v2_cutover.py ops/backup/tests/test_documented_commands.py -q`

- [ ] **Step 4: Implement driver and operator runbook, run GREEN, commit**

The driver owns the exact production sequence and journal; Plan 6 invokes phases rather than hand-copying shell. `preflight` is read-only and writes `prepared-release.json`, including exact pre-v2 artifact paths/digests used only by typed journal placeholders. Each `record-*` validates its exact mode-0600 single-line input and atomically records only the approval digest/bindings. `refresh-dr` creates/verifies fresh DR and scoped export evidence and seals their typed journal values. `deploy` requires the exact recorded promotion approval and applies only `maintenance_frontend` first, then migrations, Neo4j schema, workers/scheduler, Agent, and final frontend. `smoke` is read-only apart from bounded health bookkeeping. `rollback` selects and durably freezes one mode before mutation: pre-retirement mode advances `rollback_prepared→maintenance_frontend→pre_v2_images→v1_pointer_verified→smoke_verified→rolled_back` and never invokes scoped restore because retained v1 data/pointer are authoritative; post-retirement mode advances `rollback_prepared→maintenance_frontend→v1_restored→pre_v2_images→smoke_verified→rolled_back` and invokes the reviewed forward scoped restore for the completed retirement. Both use only typed, digest-revalidated journal placeholders and recorded pre-v2 argv, are crash-resumable/digest-fenced, never perform whole-system DR, and never down-migrate the schema. `retirement-dry-run` creates/seals the receipt and typed journal values. `retirement-confirm` requires the independently recorded matching retirement approval. A whole DR restore remains a separate documented adapter and requires the recorded matching DR approval. `status|resume` never infer or skip state. The driver preserves `/home/ubuntu/graph-v2-release-control/<RUN_ID>` plus the v2 restore-capable operator worktree/artifact until the rollback window closes.

```bash
python3 -m pytest ops/release/tests/test_graph_v2_cutover.py ops/backup/tests/test_documented_commands.py -q
git add -- ops/release/graph-v2-cutover ops/release/schemas/graph-v2-release-manifest.schema.json ops/release/schemas/graph-v2-production-command-plan.schema.json ops/acceptance/schemas/acceptance-inputs.schema.json ops/acceptance/schemas/acceptance-run-manifest.schema.json ops/acceptance/schemas/isolated-acceptance-preview.schema.json ops/acceptance/schemas/isolated-acceptance-pack.schema.json ops/acceptance/graph-v2-acceptance-state ops/release/tests/test_graph_v2_cutover.py docs/operations/disaster-recovery.md ops/backup/tests/test_documented_commands.py README.md frontend/README.md
git diff --cached --name-only
git commit -m "feat(ops): add resumable graph cutover runbook"
```

The staged-name output must equal those thirteen Task 7 paths and contain nothing else. Traefik guidance is updated only through the explicitly listed README link targets; if implementation discovers a different required existing guidance file, stop and amend/review the task allowlist before staging it—never stage a whole directory.

### Task 8: Prove DR Tooling Hermetically Before Live Approval

**Files:**
- Create artifact report: `.codex-artifacts/graph-v2/dr-tooling-readiness.json`

- [ ] **Step 1: Freeze the hermetic fixture**

Use only disposable, test-labeled PostgreSQL/Neo4j/Redis/artifact paths and a temporary local Restic repository. The fixture contains users/project/memory/Wiki/Kanban plus v1/v2 graph rows, referenced/unreferenced blobs, queues, and deterministic pre-v2 artifacts. Reject every production host/database/volume/project/config path. Do not write `/etc`, call `systemctl`, toggle production maintenance, install/enable a timer, capture production, or use the real project ULID.

- [ ] **Step 2: Run one complete disposable backup and restore rehearsal**

Run the real scripts against the disposable fixture. Require maintenance/drain ordering, PostgreSQL custom dump/list, both Neo4j dumps, central artifact inventory, common manifest, local Restic snapshot/check/retention, restore into newly disposable targets, empty-Redis reconciliation, exact counts/digests, restored-window recovery CAS, and label-scoped cleanup. Inject and record at least one pre-seal failure proving maintenance remains fail-safe.

- [ ] **Step 3: Run scoped export/retirement/restore only on the fixture**

Create a verified v1 export, execute retirement, inject one crash/resume with rotated authority, then execute the forward restore and smoke. Prove stale-owner fencing, external-mutation progress, central storage guard, exact pointer restoration, and unrelated fixture data unchanged. This is not authorization to touch live v1.

- [ ] **Step 4: Seal the non-live tooling report**

`dr-tooling-readiness.json` has schema `hades.graph_v2_dr_tooling_readiness.v1` and exactly `schema`, `tested_backend_sha`, `fixture_sha256`, `commands`, `test_results`, `snapshot_id`, `backup_manifest_sha256`, `restore_report_sha256`, `scoped_export_sha256`, `retirement_restore_report_sha256`, `failure_injection`, `production_touched:false`, `ready`, and `generated_at`. It contains no credential/path to a private source or production identifier. `ready=true` requires every disposable gate.

- [ ] **Step 5: Review and commit only the tooling report**

```bash
git add -- .codex-artifacts/graph-v2/dr-tooling-readiness.json
git diff --cached --name-only
git diff --cached --check
git commit -m "chore(ops): record hermetic graph v2 recovery tooling"
```

The staged-name output must be exactly that one file. A fresh coordinator integrates/pushes it only after digest/schema/secret/production-touch review.

## Plan 5 Exit Gate

- One complete disposable backup/restore rehearsal passed with a local Restic snapshot and retained report; no live system was touched.
- One disposable scoped v1 export/retirement/forward-restore passed with crash/resume evidence.
- Central guarded artifact mutation and deterministic inventory tests pass; no raw destructive storage call bypass exists.
- Bound maintenance crash/resume, stale-owner fencing, lock-order, retirement, and forward-restore tests pass with no ordinary-token escape hatch.
- Disposable `status.json` and safe failure behavior were exercised.
- No secret is in Git or manifest.
- `dr-tooling-readiness.json` proves `production_touched=false`; live snapshot/rehearsal/export/admin/baseline readiness is intentionally deferred to Plan 6 Task 10B after P1.
