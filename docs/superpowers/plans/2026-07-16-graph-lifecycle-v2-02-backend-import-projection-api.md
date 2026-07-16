# Hades Graph Lifecycle v2 Backend Import, Projection, and API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Validate and store chunked graph v2 artifacts, publish exact versioned Neo4j lifecycle projections atomically, and expose one closed bounded dashboard/plugin query protocol.

**Architecture:** PostgreSQL owns imports, validation leases, projection heads, versions, and contexts. Neo4j stores isolated immutable projection namespaces. Two streaming validation passes precede a four-attempt projection cycle; a head CAS publishes only the current desired generation. Dashboard requests resolve a signed current context and query only its project/binding/projection namespace.

**Tech Stack:** Laravel/PHP 8.3+, Pest/PHPUnit, PostgreSQL JSONB/ULIDs/advisory locks, Neo4j 5.26.27 Community, Laravel queues/scheduler, JSON Schema, JCS/SHA-256.

## Global Constraints

- Inherit `2026-07-16-graph-lifecycle-v2-master.md`.
- Work in `/home/ubuntu/dev-sandbox` on `codex/graph-lifecycle-v2-backend`.
- START GATE: Plan 1 is committed; root contract lock and schema manifest are final.
- Vendor schema bytes exactly. Backend validators may be stricter only where the specification assigns backend authorization/storage checks; never looser.
- V2 readers resolve `canonical_graph_projection_heads.active_projection_id`; they never inspect legacy `active_graph_version`.
- Every Neo4j read/write/delete has project ID, `workspace_binding`, binding ID, and projection version.
- A candidate namespace remains invisible until PostgreSQL head CAS succeeds.
- No controller contains validation, projection, overlay, or traversal business logic.

---

## File Structure

The complete create/modify list is normative in design section 9.5. This plan groups it into independently reviewable units:

- Migrations/models: imports, chunks, validation indexes, projection heads, maintenance, work-item extensions, verification tables, retirement audit.
- Import boundary: `GraphImportController`, manifest/chunk validators, import service, artifact reader, normalizer, validation-run jobs.
- Projection: v2 mapper, Neo4j schema/projector, projection service/job/head reconciliation.
- Query: repository, signed context, snapshot search, explorer service/controller/plugin controller.
- Tests: feature, integration, adversarial transport, concurrency, and tenant isolation.

---

### Task 1: Vendor and Lock the Root Contracts

**Files:**
- Create: `backend/resources/contracts/hades/graph-v2/*.schema.json`
- Create: `backend/resources/contracts/hades/graph-v2/golden/*.json`
- Create: `backend/resources/contracts/hades/graph-v2/manifest.json`
- Create: `backend/resources/contracts/hades/graph-v2/contract-lock.json`
- Test: `backend/tests/Unit/Graph/GraphV2ContractManifestTest.php`

**Interfaces:**

```php
final class GraphV2ContractManifest
{
    public function assertVendoredFilesMatchManifest(): void;
    public function digest(string $filename): string;
}
```

- [ ] **Step 1: Add the RED byte-compatibility test**

```php
it('vendors the exact locked graph v2 contract bytes', function () {
    $root = base_path('backend/resources/contracts/hades/graph-v2');
    $manifest = json_decode(file_get_contents("{$root}/manifest.json"), true, flags: JSON_THROW_ON_ERROR);
    expect(array_column($manifest['files'], 'path'))->toBe(array_values(array_unique(array_column($manifest['files'], 'path'))));
    foreach ($manifest['files'] as $file) {
        expect(hash_file('sha256', "{$root}/{$file['path']}"))->toBe($file['sha256']);
    }
});
```

Add a table-driven PHP JCS/ID/digest test that reads every `golden/canonicalization.json` vector and asserts its exact canonical UTF-8 hex and SHA-256, providing the PHP half of G13.

- [ ] **Step 2: Run RED**

Run: `docker compose exec -T app php artisan test backend/tests/Unit/Graph/GraphV2ContractManifestTest.php`

Expected: FAIL because the vendored v2 contract does not exist.

- [ ] **Step 3: Copy exact root bytes and add CI drift check**

Copy from the pinned Plan 1 commit, not from an uncommitted checkout. Sort manifest files lexically and compare each byte digest plus `contract-lock.json` semantics in CI.

- [ ] **Step 4: Run GREEN and commit**

Run: `docker compose exec -T app php artisan test backend/tests/Unit/Graph/GraphV2ContractManifestTest.php`

```bash
git add backend/resources/contracts/hades/graph-v2 backend/tests/Unit/Graph/GraphV2ContractManifestTest.php
git commit -m "feat(graph): vendor locked graph v2 contracts"
```

### Task 2: Add Import, Validation, and Projection Storage Migrations

**Files:**
- Create: `backend/database/migrations/2026_07_16_000100_create_hades_graph_imports_table.php`
- Create: `backend/database/migrations/2026_07_16_000200_create_hades_graph_import_chunks_table.php`
- Create: `backend/database/migrations/2026_07_16_000250_create_hades_graph_import_validation_tables.php`
- Create: `backend/database/migrations/2026_07_16_000300_extend_canonical_graph_projections_for_v2.php`
- Create: `backend/database/migrations/2026_07_16_000350_create_canonical_graph_projection_heads.php`
- Create: `backend/app/Models/HadesGraphImport.php`
- Create: `backend/app/Models/HadesGraphImportChunk.php`
- Create: `backend/app/Models/CanonicalGraphProjectionHead.php`
- Modify: existing canonical projection/attempt models
- Test: `backend/tests/Feature/Hades/GraphV2MigrationTest.php`

**Interfaces:**
- Table/column/index/FK names and statuses are exactly those in section 9.1.
- Import status: `staging|validating|validated|failed|stale`.
- Projection head unique key: `(project_id,source_scope_type,source_scope_id)`.

- [ ] **Step 1: Add RED schema assertions**

```php
it('creates graph v2 storage with scoped uniqueness', function () {
    expect(Schema::hasColumns('hades_graph_imports', [
        'project_id', 'workspace_binding_id', 'attempt_generation', 'schema',
        'artifact_graph_version', 'manifest_semantic_sha256', 'status',
        'validation_attempts', 'validation_run_token_hash', 'validation_lease_expires_at',
    ]))->toBeTrue();
    expect(Schema::hasColumns('canonical_graph_projection_heads', [
        'desired_generation', 'desired_artifact_graph_version',
        'desired_verification_set_hash', 'desired_projection_version',
        'active_projection_id', 'previous_projection_id', 'failed_generation',
    ]))->toBeTrue();
});
```

Add DB tests for live-import partial uniqueness, cross-import reference rejection, exact cross-kind ID exception, immutable validated import, and head uniqueness.

- [ ] **Step 2: Run RED**

Run: `docker compose exec -T app php artisan test backend/tests/Feature/Hades/GraphV2MigrationTest.php`

- [ ] **Step 3: Implement migrations/models**

Use existing project/workspace FK types and deletion policies. Use explicit casts for JSON/immutable datetimes. Do not add repository IDs or polymorphic fallback identities. Add DB-level checks/partial indexes with raw PostgreSQL SQL where Laravel schema builder cannot express them.

- [ ] **Step 4: Run GREEN, rollback/forward migration check, commit**

Run: `docker compose exec -T app php artisan migrate --pretend`

Run: `docker compose exec -T app php artisan test backend/tests/Feature/Hades/GraphV2MigrationTest.php`

```bash
git add backend/database/migrations backend/app/Models/HadesGraphImport.php backend/app/Models/HadesGraphImportChunk.php backend/app/Models/CanonicalGraphProjectionHead.php backend/tests/Feature/Hades/GraphV2MigrationTest.php
git commit -m "feat(graph): add v2 import and projection storage"
```

### Task 3: Implement Authenticated Resumable Import Endpoints

**Files:**
- Create: `backend/app/Http/Controllers/Hades/GraphImportController.php`
- Create: `backend/app/Services/Graph/V2/GraphV2ManifestValidator.php`
- Create: `backend/app/Services/Graph/V2/GraphV2ChunkValidator.php`
- Create: `backend/app/Services/Graph/V2/GraphV2ImportService.php`
- Modify: `backend/routes/api.php`
- Test: `backend/tests/Feature/Hades/GraphImportControllerTest.php`

**Interfaces:**

```php
interface GraphV2ImportService
{
    public function create(Project $project, WorkspaceBinding $binding, HadesAgent $agent, array $manifest): GraphImportResult;
    public function putChunk(HadesGraphImport $import, int $index, UploadedFile $body, array $headers): GraphChunkResult;
    public function complete(HadesGraphImport $import, string $artifactGraphVersion): GraphImportResult;
    public function show(HadesGraphImport $import): GraphImportResult;
}
```

- [ ] **Step 1: Add RED feature matrix**

Test create 201/new and 200/idempotent, wrong project/binding/device/capability, v1 schema, attempt conflict, descriptor mismatch, server-owned path, duplicate same bytes, duplicate different bytes 409, missing chunk, manifest digest mismatch, and complete after all chunks.

- [ ] **Step 2: Run RED**

Run: `docker compose exec -T app php artisan test backend/tests/Feature/Hades/GraphImportControllerTest.php`

- [ ] **Step 3: Implement controller/service boundary**

Routes are exactly:

```php
Route::post('graph-imports', [GraphImportController::class, 'store']);
Route::put('graph-imports/{graphImport}/chunks/{index}', [GraphImportController::class, 'putChunk']);
Route::post('graph-imports/{graphImport}/complete', [GraphImportController::class, 'complete']);
Route::get('graph-imports/{graphImport}', [GraphImportController::class, 'show']);
```

Controller authorizes and delegates. `GraphV2ChunkValidator` reads one gzip member as a stream, enforces advertised compressed/uncompressed limits before allocation, rejects trailing bytes, and compares both digests/count/schema/kind/order. `complete` accepts exactly `{artifact_graph_version:"64-hex"}`; it never accepts `manifest_semantic_sha256` from the client. The server recomputes/stores the semantic manifest digest independently.

- [ ] **Step 4: Run GREEN and commit**

Run: `docker compose exec -T app php artisan test backend/tests/Feature/Hades/GraphImportControllerTest.php`

```bash
git add backend/app/Http/Controllers/Hades/GraphImportController.php backend/app/Services/Graph/V2 backend/routes/api.php backend/tests/Feature/Hades/GraphImportControllerTest.php
git commit -m "feat(graph): accept resumable graph v2 imports"
```

### Task 4: Implement Two-Pass Streaming Validation with Four Leased Runs

**Files:**
- Create: `backend/app/Services/Graph/V2/GraphV2ArtifactReader.php`
- Create: `backend/app/Services/Graph/V2/GraphV2Normalizer.php`
- Create: `backend/app/Services/Graph/V2/GraphV2ValidationRunService.php`
- Create: `backend/app/Jobs/AcquireGraphV2ValidationRun.php`
- Create: `backend/app/Jobs/ValidateGraphV2Import.php`
- Test: `backend/tests/Integration/Graph/GraphV2ImportValidationTest.php`

**Interfaces:**

```php
final class GraphV2ValidationRunService
{
    public function acquireAndDispatch(HadesGraphImport $import): bool;
}

final class ValidateGraphV2Import implements ShouldQueue, ShouldBeEncrypted
{
    public int $tries = 1;
    public function __construct(
        public string $importId,
        public int $attemptGeneration,
        public int $validationAttempt,
        public string $runToken,
    ) {}
}
```

- [ ] **Step 1: Add RED lease/streaming/failure tests**

Assert exactly two artifact passes, batches of 1,000, heartbeat CAS by token, one try per encrypted validation job, exactly four separately tokenized transient runs, delays 10/30/90 seconds before runs 2/3/4, deterministic failure terminal immediately, transient run 4 terminal `graph_validation_infrastructure_failed`, reclaimed old worker cannot commit, and successful CAS alone requests projection after commit.

- [ ] **Step 2: Run RED**

Run: `docker compose exec -T app php artisan test backend/tests/Integration/Graph/GraphV2ImportValidationTest.php`

- [ ] **Step 3: Implement pass responsibilities**

Pass 1 inserts file paths and record keys while checking descriptor bytes/digests, local shape/order/privacy. Pass 2 inserts typed references, resolves the closed matrix, verifies evidence locators/fingerprints, recomputes semantic digest/counts/coverage/flow membership, and validates the manifest. Neither pass builds a complete PHP array. `GraphV2ValidationRunService` alone locks the validating import, requires no live lease, increments attempts, creates a random 256-bit raw token, stores only its lower-case SHA-256, sets start/heartbeat `now()` and lease `now()+5 minutes`, commits, then dispatches after commit. The validator heartbeats before every chunk, after every 1,000 records, and before 30 seconds monotonic elapsed; every heartbeat/final write CAS-matches import ID, import generation, status, validation attempt, and token hash. Runs 1–3 schedule only the acquisition wrapper after exactly 10/30/90 seconds; an uncaught crash relies on lease-expiry reconciliation under the same four-run ceiling.

- [ ] **Step 4: Run GREEN and commit**

Run: `docker compose exec -T app php artisan test backend/tests/Integration/Graph/GraphV2ImportValidationTest.php`

```bash
git add backend/app/Services/Graph/V2/GraphV2ArtifactReader.php backend/app/Services/Graph/V2/GraphV2Normalizer.php backend/app/Services/Graph/V2/GraphV2ValidationRunService.php backend/app/Jobs/AcquireGraphV2ValidationRun.php backend/app/Jobs/ValidateGraphV2Import.php backend/tests/Integration/Graph/GraphV2ImportValidationTest.php
git commit -m "feat(graph): validate graph v2 imports with leased passes"
```

### Task 5: Initialize and Project Isolated Neo4j v2 Namespaces

**Files:**
- Create: `backend/app/Services/Graph/V2/Neo4jGraphV2Schema.php`
- Create: `backend/app/Services/Graph/V2/GraphV2LifecycleProjectionMapper.php`
- Create: `backend/app/Console/Commands/InitializeCanonicalGraphV2Schema.php`
- Modify: `backend/app/Services/Graph/Neo4jCanonicalGraphProjector.php`
- Test: `backend/tests/Integration/Graph/CanonicalGraphV2ProjectionTest.php`

**Interfaces:**

```php
final class GraphV2LifecycleProjectionMapper
{
    /** @return iterable<LifecycleProjectionRecord> */
    public function map(HadesGraphImport $import): iterable;
}

final class Neo4jGraphV2Schema
{
    public function ensure(): void;
    public function verify(): void;
}
```

- [ ] **Step 1: Add RED namespace/schema/one-to-one tests**

Test named uniqueness constraints on `namespace_key`, range indexes for all four namespace dimensions, mapper rejection of producer step mismatch, one flow-step per five-field identity, exact stage/layout copy, candidate namespace isolation, and no cross-version delete/reuse.

- [ ] **Step 2: Run RED**

Run: `docker compose exec -T app php artisan test backend/tests/Integration/Graph/CanonicalGraphV2ProjectionTest.php`

- [ ] **Step 3: Implement schema and mapper**

Compute namespace key as SHA-256 JCS of `{project_id,source_scope_type,source_scope_id,projection_version,public_id}`. Project `CanonicalLifecycleFlow`/`CanonicalLifecycleStep` and relationships exactly as section 9.4. The mapper validates and copies producer membership; it never traverses or assigns stages.

- [ ] **Step 4: Run GREEN and commit**

Run: `docker compose exec -T app php artisan hades:graph-v2:init-schema --verify`

Run: `docker compose exec -T app php artisan test backend/tests/Integration/Graph/CanonicalGraphV2ProjectionTest.php`

```bash
git add backend/app/Services/Graph/V2/Neo4jGraphV2Schema.php backend/app/Services/Graph/V2/GraphV2LifecycleProjectionMapper.php backend/app/Console/Commands/InitializeCanonicalGraphV2Schema.php backend/app/Services/Graph/Neo4jCanonicalGraphProjector.php backend/tests/Integration/Graph/CanonicalGraphV2ProjectionTest.php
git commit -m "feat(graph): project isolated lifecycle v2 namespaces"
```

### Task 6: Add Desired-Generation Projection CAS and Retry Cycle

**Files:**
- Create: `backend/app/Jobs/ProjectCanonicalGraphV2.php`
- Create: `backend/app/Console/Commands/ReconcileCanonicalGraphV2.php`
- Create: `backend/app/Console/Commands/CleanupCanonicalGraphV2.php`
- Modify: `backend/app/Services/Graph/CanonicalGraphProjectionService.php`
- Modify: `backend/routes/console.php`
- Test: `backend/tests/Integration/Graph/CanonicalGraphV2ProjectionRetryTest.php`

**Interfaces:**

```php
final class ProjectCanonicalGraphV2 implements ShouldQueue, ShouldBeUnique
{
    public int $tries = 4;
    public function backoff(): array { return [10, 30, 90]; }
}
```

- [ ] **Step 1: Add RED concurrency/failure tests**

Assert active projection stays readable through candidate build, failure attempts 1–3 do not set head failure, attempt 4 sets exact failed generation/version, stale generation is success no-op, newer generation cannot be overwritten, exact previous projection can swap without rewrite, invalid previous refuses rebuild/delete, partial candidate namespace is cleared before retry, reconcile does not redispatch blocked generation, and cleanup waits context TTL + ten minutes.

- [ ] **Step 2: Run RED**

Run: `docker compose exec -T app php artisan test backend/tests/Integration/Graph/CanonicalGraphV2ProjectionRetryTest.php`

- [ ] **Step 3: Implement transactional publication**

Queue after commit with two-second delay and unique project/scope key. Lock head/attempts before writes. Publish with desired generation + projection version CAS in one PostgreSQL transaction. On success set candidate ready, rotate active/previous, then queue verification reconciliation after commit.

- [ ] **Step 4: Run GREEN and commit**

Run: `docker compose exec -T app php artisan test backend/tests/Integration/Graph/CanonicalGraphV2ProjectionRetryTest.php`

```bash
git add backend/app/Jobs/ProjectCanonicalGraphV2.php backend/app/Console/Commands/ReconcileCanonicalGraphV2.php backend/app/Console/Commands/CleanupCanonicalGraphV2.php backend/app/Services/Graph/CanonicalGraphProjectionService.php backend/routes/console.php backend/tests/Integration/Graph/CanonicalGraphV2ProjectionRetryTest.php
git commit -m "feat(graph): publish graph v2 with generation CAS"
```

### Task 7: Implement Signed Contexts, Handles, Cursors, and Search Snapshots

**Files:**
- Create: `backend/app/Services/Graph/DashboardGraphContext.php`
- Create: `backend/app/Services/Graph/DashboardGraphSearchSnapshot.php`
- Test: `backend/tests/Unit/Graph/DashboardGraphContextTest.php`
- Test: `backend/tests/Integration/Graph/DashboardGraphSearchSnapshotTest.php`

**Interfaces:**

```php
final class DashboardGraphContext
{
    public function issue(Project $project, CanonicalGraphProjection $projection): string;
    public function verify(string $token, User $user, Project $project): VerifiedGraphContext;
    public function issueHandle(VerifiedGraphContext $context, string $type, string $publicId): string;
    public function verifyHandle(string $handle, VerifiedGraphContext $context, string $expectedType): string;
    public function issueCursor(VerifiedGraphContext $context, CursorPayload $payload): string;
}
```

- [ ] **Step 1: Add RED token/snapshot tests**

Test canonical base64url payload, HMAC-SHA256 with `APP_KEY`, 30-minute TTL, user/project/binding authorization, inactive/expired version 409, invalid signature/type/filter 422, context-bound handle/cursor, one captured vector rank per snapshot, pagination without new similarity query, TTL cleanup, and cross-session/project/projection rejection.

- [ ] **Step 2: Run RED**

Run: `docker compose exec -T app php artisan test backend/tests/Unit/Graph/DashboardGraphContextTest.php backend/tests/Integration/Graph/DashboardGraphSearchSnapshotTest.php`

- [ ] **Step 3: Implement closed token payloads and snapshot storage**

Token payload keys are exactly those in section 10.1. Do not expose raw projection choice to the browser. Snapshot IDs are opaque, session-bound, and stored with captured ordered candidate IDs/scores until context expiry.

- [ ] **Step 4: Run GREEN and commit**

```bash
docker compose exec -T app php artisan test backend/tests/Unit/Graph/DashboardGraphContextTest.php backend/tests/Integration/Graph/DashboardGraphSearchSnapshotTest.php
git add backend/app/Services/Graph/DashboardGraphContext.php backend/app/Services/Graph/DashboardGraphSearchSnapshot.php backend/tests/Unit/Graph/DashboardGraphContextTest.php backend/tests/Integration/Graph/DashboardGraphSearchSnapshotTest.php
git commit -m "feat(graph): bind dashboard exploration to signed context"
```

### Task 8: Implement the Closed Dashboard Graph Query Protocol

**Files:**
- Modify: `backend/app/Services/Graph/CanonicalGraphRepository.php`
- Modify: `backend/app/Services/Graph/CanonicalGraphQueryService.php`
- Modify: `backend/app/Services/Graph/DashboardGraphExplorerService.php`
- Modify: `backend/app/Http/Controllers/Dashboard/Api/DashboardGraphExplorerController.php`
- Modify: `backend/routes/web.php`
- Test: `backend/tests/Feature/Dashboard/GraphExplorerV2ControllerTest.php`

**Interfaces:**

```php
final class DashboardGraphExplorerService
{
    public function query(User $user, Project $project, DashboardGraphRequest $request): DashboardGraphResponse;
}
```

Supported discriminators are exactly `scopes|overview|entrypoints|lifecycle|lifecycle_expand|search|detail|neighborhood|impact|path`.

- [ ] **Step 1: Add RED protocol matrix**

Test every query type, exact closed request/response DTO, scopes with null context/projection, overview context adoption, bounded limits/cursors, entrypoint labels/search, lifecycle backbone/stage counts/branches/terminals/async summaries, expand selector, search match basis, detail, neighborhood, impact, path success/no-path/unknown, false-zero prevention, stale context 409, not-ready error, invalid handle/cursor 422, and tenant isolation.

- [ ] **Step 2: Run RED**

Run: `docker compose exec -T app php artisan test backend/tests/Feature/Dashboard/GraphExplorerV2ControllerTest.php`

- [ ] **Step 3: Implement exact active-v2 queries**

Repository first resolves the head active projection for project/binding, then every Neo4j query supplies all namespace fields. Use stable sort tuples from section 10.1. Return `CountKnowledge`; never coalesce unknown to zero. Initial lifecycle includes at most 120 canonical nodes.

- [ ] **Step 4: Run GREEN and commit**

Run: `docker compose exec -T app php artisan test backend/tests/Feature/Dashboard/GraphExplorerV2ControllerTest.php`

```bash
git add backend/app/Services/Graph/CanonicalGraphRepository.php backend/app/Services/Graph/CanonicalGraphQueryService.php backend/app/Services/Graph/DashboardGraphExplorerService.php backend/app/Http/Controllers/Dashboard/Api/DashboardGraphExplorerController.php backend/routes/web.php backend/tests/Feature/Dashboard/GraphExplorerV2ControllerTest.php
git commit -m "feat(graph): expose dashboard lifecycle protocol v2"
```

### Task 9: Align the Service-Gated Plugin Graph Query Surface

**Files:**
- Modify: `backend/app/Http/Controllers/Plugin/GraphQueryController.php`
- Modify: `backend/config/devboard.php`
- Test: `backend/tests/Feature/Hades/PluginGraphQueryV2Test.php`

**Interfaces:**
- Plugin graph search/traverse returns the same v2 handles, context, completeness, and bounded semantics as dashboard service.
- Vector ranking returns handles/candidates only; topology always calls `DashboardGraphExplorerService`/current projection.

- [ ] **Step 1: Add RED plugin tests**

Test capability gate, project/binding/device isolation, v1 request rejection, vector candidate resolution through exact active context, no local/repository fallback, route/symbol search, and unknown counts.

- [ ] **Step 2: Run RED**

Run: `docker compose exec -T app php artisan test backend/tests/Feature/Hades/PluginGraphQueryV2Test.php`

- [ ] **Step 3: Reuse dashboard services, run GREEN, commit**

```bash
docker compose exec -T app php artisan test backend/tests/Feature/Hades/PluginGraphQueryV2Test.php
git add backend/app/Http/Controllers/Plugin/GraphQueryController.php backend/config/devboard.php backend/tests/Feature/Hades/PluginGraphQueryV2Test.php
git commit -m "feat(graph): align plugin queries with graph v2"
```

### Task 10: Add Graph-Only Maintenance Gating

**Files:**
- Create: migration `2026_07_16_000650_create_graph_maintenance_windows_table.php`
- Create: `backend/app/Models/GraphMaintenanceWindow.php`
- Create: `backend/app/Services/Graph/GraphMaintenanceService.php`
- Create: `backend/app/Console/Commands/SetCanonicalGraphMaintenance.php`
- Modify: graph controllers/routes only
- Test: `backend/tests/Feature/Graph/GraphMaintenanceTest.php`

**Interfaces:**

```php
public function begin(?string $projectId, string $reason, int $ttl): MaintenanceToken;
public function end(?string $projectId, string $rawToken, bool $requireNeo4jHealthy): void;
public function assertGraphAvailable(?string $projectId): void;
```

- [ ] **Step 1: Add RED scope/token/fail-safe tests**

Test one global/one per-project active window, one-time raw token, stored hash only, bounded expiry, wrong token, expired window remains fail-safe, Neo4j health required on off, graph import/query/UI blocked, and non-graph backend/project routes unaffected.

- [ ] **Step 2: Run RED**

Run: `docker compose exec -T app php artisan test backend/tests/Feature/Graph/GraphMaintenanceTest.php`

- [ ] **Step 3: Implement service/command/middleware, run GREEN, commit**

```bash
docker compose exec -T app php artisan test backend/tests/Feature/Graph/GraphMaintenanceTest.php
git add backend/database/migrations/2026_07_16_000650_create_graph_maintenance_windows_table.php backend/app/Models/GraphMaintenanceWindow.php backend/app/Services/Graph/GraphMaintenanceService.php backend/app/Console/Commands/SetCanonicalGraphMaintenance.php backend/tests/Feature/Graph/GraphMaintenanceTest.php
git commit -m "feat(graph): gate graph operations during maintenance"
```

### Task 11: Wire Scheduling, Retention, and Artifact Reachability

**Files:**
- Modify: `backend/app/Services/ArtifactStorageService.php`
- Modify: `backend/routes/console.php`
- Modify: graph cleanup/repository services
- Test: `backend/tests/Integration/Graph/GraphV2RetentionTest.php`

**Interfaces:**
- Failed/stale imports eligible after 24h only when unreachable.
- Validated import/chunks retained while reachable by projections/heads/overlays/requests/contexts/Wiki ledger.
- Active + previous v2 namespaces retained; stale cleanup waits 40 minutes and rechecks under lock.

- [ ] **Step 1: Add RED reachability/race tests**

Cover every reachability source, concurrent new reference abort, exact object ownership before deletion, no raw prefix delete, referenced blob retention, scheduler minute/hour cadence, `withoutOverlapping()` and one-server locks.

- [ ] **Step 2: Run RED**

Run: `docker compose exec -T app php artisan test backend/tests/Integration/Graph/GraphV2RetentionTest.php`

- [ ] **Step 3: Implement reachability cleanup, run GREEN, commit**

```bash
docker compose exec -T app php artisan test backend/tests/Integration/Graph/GraphV2RetentionTest.php
git add backend/app/Services/ArtifactStorageService.php backend/routes/console.php backend/app/Services/Graph backend/tests/Integration/Graph/GraphV2RetentionTest.php
git commit -m "feat(graph): retain reachable graph v2 artifacts"
```

### Task 12: Close Backend Acceptance Gates L01–L15

**Files:**
- Modify: backend graph v2 tests only for missing coverage
- Create: `.codex-artifacts/graph-v2/backend-gates.json`

- [ ] **Step 1: Map every backend gate to one exact test node**

```json
{
  "L01": "backend/tests/Feature/Hades/GraphImportControllerTest.php::import_is_scoped_resumable_digest_checked_and_idempotent",
  "L02": "backend/tests/Integration/Graph/CanonicalGraphV2ProjectionTest.php::projection_binds_exact_project_scope_artifact_overlay_and_entrypoint",
  "L03": "backend/tests/Feature/Dashboard/GraphExplorerV2ControllerTest.php::v2_query_has_no_repository_workspace_or_version_fallback",
  "L04": "backend/tests/Integration/Graph/CanonicalGraphV2ProjectionTest.php::candidate_is_invisible_until_ready_partial_head_cas",
  "L05": "backend/tests/Integration/Graph/CanonicalGraphV2ProjectionRetryTest.php::failure_preserves_old_active_projection",
  "L06": "backend/tests/Feature/Dashboard/GraphExplorerV2ControllerTest.php::zero_requires_absence_verified",
  "L07": "backend/tests/Integration/Graph/CanonicalGraphV2ProjectionRetryTest.php::duplicate_projectors_are_idempotent_and_do_not_steal_lease",
  "L08": "backend/tests/Unit/Graph/DashboardGraphContextTest.php::new_generation_invalidates_old_context_without_mixing",
  "L09": "backend/tests/Integration/Graph/CanonicalGraphV2ProjectionTest.php::mapper_preserves_unique_producer_flow_membership_and_frontiers",
  "L10": "backend/tests/Feature/Dashboard/GraphExplorerV2ControllerTest.php::all_v2_query_types_match_closed_bounded_protocol",
  "L11": "backend/tests/Feature/Hades/GraphImportControllerTest.php::transport_rejects_descriptor_order_trailing_bytes_bombs_and_replay_mismatch",
  "L12": "backend/tests/Integration/Graph/CanonicalGraphV2ProjectionRetryTest.php::fourth_failure_blocks_generation_until_explicit_retry",
  "L13": "backend/tests/Integration/Graph/DashboardGraphSearchSnapshotTest.php::vector_pages_reuse_one_context_bound_snapshot",
  "L14": "backend/tests/Integration/Graph/CanonicalGraphV2ProjectionRetryTest.php::stale_generation_cannot_publish",
  "L15": "backend/tests/Integration/Graph/GraphV2ImportValidationTest.php::validation_uses_two_passes_four_tokens_and_heartbeat_cas"
}
```

- [ ] **Step 2: Run the complete graph v2 suite**

```bash
docker compose exec -T app php artisan test \
  backend/tests/Feature/Hades/GraphImportControllerTest.php \
  backend/tests/Feature/Dashboard/GraphExplorerV2ControllerTest.php \
  backend/tests/Feature/Hades/PluginGraphQueryV2Test.php \
  backend/tests/Integration/Graph/GraphV2ImportValidationTest.php \
  backend/tests/Integration/Graph/CanonicalGraphV2ProjectionTest.php \
  backend/tests/Integration/Graph/CanonicalGraphV2ProjectionRetryTest.php \
  backend/tests/Integration/Graph/DashboardGraphSearchSnapshotTest.php \
  backend/tests/Integration/Graph/GraphV2RetentionTest.php
```

Expected: PASS with zero xfail/skip for L01–L15.

- [ ] **Step 3: Run static/style/route checks**

```bash
docker compose exec -T app vendor/bin/pint --test
docker compose exec -T app php artisan route:list --path=graph
docker compose exec -T app php artisan schedule:list
```

Expected: Pint PASS; only v2 graph query/import paths are active; reconciliation/cleanup schedules match the specification.

- [ ] **Step 4: Run tenant and failure-injection regression**

Run: `docker compose exec -T app php artisan test --group=graph-tenant-isolation --group=graph-failure-injection`

Expected: PASS; old active projection remains readable for every injected failure.

- [ ] **Step 5: Record gates, review, commit**

```bash
git diff --check
git add .codex-artifacts/graph-v2/backend-gates.json backend/tests
git commit -m "test(graph): close backend lifecycle v2 gates"
```

## Plan 2 Exit Gate

- L01–L15 are green.
- Backend vendored contract bytes equal Plan 1.
- Four total validation runs and four total projection attempts are proven independently.
- `scopes` is the only graph query without context/projection; all other successful responses carry the exact active context.
- No active v2 repository/query path falls back to repository/workspace/v1 data.
- Old active projection survives every injected candidate failure.
- Fresh spec-compliance review contains no unresolved P0/P1 finding.
