# Hades Graph Lifecycle v2 Backend Import, Projection, and API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Validate and store chunked graph v2 artifacts, publish exact versioned Neo4j lifecycle projections atomically, and expose one closed bounded dashboard/plugin query protocol.

**Architecture:** PostgreSQL owns imports, validation leases, projection heads, versions, and contexts. Neo4j stores isolated immutable projection namespaces. Two streaming validation passes precede a four-attempt projection cycle; a head CAS publishes only the current desired generation. Dashboard requests resolve a signed current context and query only its project/binding/projection namespace.

**Tech Stack:** Laravel/PHP 8.3+, Pest/PHPUnit, PostgreSQL JSONB/ULIDs/advisory locks, Neo4j 5.26.27 Community, Laravel queues/scheduler, JSON Schema, JCS/SHA-256.

## Global Constraints

- Inherit `2026-07-16-graph-lifecycle-v2-master.md`.
- Work in `/home/ubuntu/dev-sandbox` on the exact active task branch recorded by `2026-07-21-graph-lifecycle-v2-execution-checkpoint.md`. The open Task 5 repair keeps its checkpointed historical branch; after that, every task or mandatory slice starts from then-current clean, pulled `main` on `codex/graph-v2-p2-task-N[-slice]`, only after the accepted predecessor was reviewed, integrated, tested, and pushed. Do not reuse an obsolete catch-all branch.
- START GATE: Plan 1 is committed; root contract lock and schema manifest are final.
- Vendor schema bytes exactly. Backend validators may be stricter only where the specification assigns backend authorization/storage checks; never looser.
- V2 readers resolve `canonical_graph_projection_heads.active_projection_id`; they never inspect legacy `active_graph_version`.
- Every Neo4j read/write/delete has project ID, `workspace_binding`, binding ID, and projection version.
- A candidate namespace remains invisible until PostgreSQL head CAS succeeds.
- Plan 2 emits only the semantic `CanonicalGraphV2ProjectionActivated` event after a successful publication CAS; it never imports, resolves, or calls a Plan 3 verification service.
- Artifact reachability is extended only through the fixed internal `GraphArtifactReachabilityProvider` tag. It is not a public plugin hook, callback registry, or model-tool surface.
- `GraphArtifactReferenceLock` is the outermost transaction for every writer that can create a graph-import reference. The total lock order is import advisory keys sorted by `(project_id,workspace_binding_id,import_id)`, import rows `FOR UPDATE` in the same order, then domain rows; callers must hold no domain row lock before entering it.
- No controller contains validation, projection, overlay, or traversal business logic.
- After every focused GREEN, run the fixed existing-code regression set `backend/tests/Feature/CanonicalGraphProjectionTest.php`, `backend/tests/Feature/CanonicalGraphRepositoryTest.php`, `backend/tests/Feature/Plugin/GraphQueryApiTest.php`, and `backend/tests/Feature/Hades/HadesM1ApiTest.php`, followed by `git diff --check`. A task is not review-ready until both focused and regression commands pass or a parent-identical unrelated failure is recorded with a reproducer.

---

## File Structure

Design section 9.5 remains the normative product file map. The approved cross-plan seam adds only the event and internal reachability files named explicitly in Tasks 6 and 11 below. This plan groups the complete execution set into independently reviewable units:

- Migrations/models: imports, chunks, validation indexes, projection heads, projection attempts/contexts, and graph maintenance.
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
    $root = base_path('resources/contracts/hades/graph-v2');
    $manifest = json_decode(file_get_contents("{$root}/manifest.json"), true, flags: JSON_THROW_ON_ERROR);
    expect(array_column($manifest['files'], 'path'))->toBe(array_values(array_unique(array_column($manifest['files'], 'path'))));
    foreach ($manifest['files'] as $file) {
        expect(hash_file('sha256', "{$root}/{$file['path']}"))->toBe($file['sha256']);
    }
});
```

Add a table-driven PHP JCS/ID/digest test that reads every `golden/canonicalization.json` vector and asserts its exact canonical UTF-8 hex and SHA-256, providing the PHP half of G13.

- [ ] **Step 2: Run RED**

Run: `docker compose exec -T app php artisan test tests/Unit/Graph/GraphV2ContractManifestTest.php`

Expected: FAIL because the vendored v2 contract does not exist.

- [ ] **Step 3: Copy exact root bytes and add CI drift check**

Copy from the pinned Plan 1 commit, not from an uncommitted checkout. Sort manifest files lexically and compare each byte digest plus `contract-lock.json` semantics in CI.

- [ ] **Step 4: Run GREEN and commit**

Run: `docker compose exec -T app php artisan test tests/Unit/Graph/GraphV2ContractManifestTest.php`

```bash
git add -- backend/resources/contracts/hades/graph-v2/artifact.schema.json backend/resources/contracts/hades/graph-v2/bundle.schema.json backend/resources/contracts/hades/graph-v2/chunk.schema.json backend/resources/contracts/hades/graph-v2/dashboard-query.schema.json backend/resources/contracts/hades/graph-v2/dashboard-response.schema.json backend/resources/contracts/hades/graph-v2/verification-work.schema.json backend/resources/contracts/hades/graph-v2/verification-result.schema.json backend/resources/contracts/hades/graph-v2/graph-overlay.schema.json backend/resources/contracts/hades/graph-v2/golden/canonicalization.json backend/resources/contracts/hades/graph-v2/golden/dashboard-protocol.json backend/resources/contracts/hades/graph-v2/golden/verification-results.json backend/resources/contracts/hades/graph-v2/manifest.json backend/resources/contracts/hades/graph-v2/contract-lock.json backend/tests/Unit/Graph/GraphV2ContractManifestTest.php
git diff --cached --name-only
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

Run: `docker compose exec -T app php artisan test tests/Feature/Hades/GraphV2MigrationTest.php`


- [ ] **Step 3: Implement migrations/models**

Use existing project/workspace FK types and deletion policies. Use explicit casts for JSON/immutable datetimes. Do not add repository IDs or polymorphic fallback identities. Add DB-level checks/partial indexes with raw PostgreSQL SQL where Laravel schema builder cannot express them.

- [ ] **Step 4: Run GREEN, rollback/forward migration check, commit**

Run: `docker compose exec -T app php artisan migrate --pretend`

Run: `docker compose exec -T app php artisan test tests/Feature/Hades/GraphV2MigrationTest.php`

```bash
git add backend/database/migrations/2026_07_16_000100_create_hades_graph_imports_table.php backend/database/migrations/2026_07_16_000200_create_hades_graph_import_chunks_table.php backend/database/migrations/2026_07_16_000250_create_hades_graph_import_validation_tables.php backend/database/migrations/2026_07_16_000300_extend_canonical_graph_projections_for_v2.php backend/database/migrations/2026_07_16_000350_create_canonical_graph_projection_heads.php backend/app/Models/HadesGraphImport.php backend/app/Models/HadesGraphImportChunk.php backend/app/Models/CanonicalGraphProjectionHead.php backend/tests/Feature/Hades/GraphV2MigrationTest.php
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
final class GraphV2ImportService
{
    public function create(Project $project, HadesWorkspaceBinding $binding, HadesAgent $agent, array $manifest): HadesGraphImport;
    public function putChunk(HadesGraphImport $import, int $index, UploadedFile $body, array $headers): HadesGraphImportChunk;
    public function complete(HadesGraphImport $import, string $artifactGraphVersion): HadesGraphImport;
    public function show(HadesGraphImport $import): HadesGraphImport;
}
```

- [ ] **Step 1: Add RED feature matrix**

Test create 201/new and 200/idempotent, wrong project/binding/device/capability, v1 schema, attempt conflict, descriptor mismatch, server-owned path, duplicate same bytes, duplicate different bytes 409, missing chunk, manifest digest mismatch, and complete after all chunks.

- [ ] **Step 2: Run RED**

Run: `docker compose exec -T app php artisan test tests/Feature/Hades/GraphImportControllerTest.php`

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

Run: `docker compose exec -T app php artisan test tests/Feature/Hades/GraphImportControllerTest.php`

```bash
git add backend/app/Http/Controllers/Hades/GraphImportController.php backend/app/Services/Graph/V2/GraphV2ManifestValidator.php backend/app/Services/Graph/V2/GraphV2ChunkValidator.php backend/app/Services/Graph/V2/GraphV2ImportService.php backend/routes/api.php backend/tests/Feature/Hades/GraphImportControllerTest.php
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

Assert exactly two artifact passes, batches of 1,000, heartbeat CAS by token, one try per encrypted validation job, exactly four separately tokenized transient runs, delays 10/30/90 seconds before runs 2/3/4, deterministic failure terminal immediately, transient run 4 terminal `graph_validation_infrastructure_failed`, reclaimed old worker cannot commit, and successful CAS alone requests projection after commit. Task 6 will replace this temporary callback-only request with a durable same-transaction desired-head write before cleanup exists.

- [ ] **Step 2: Run RED**

Run: `docker compose exec -T app php artisan test tests/Integration/Graph/GraphV2ImportValidationTest.php`

- [ ] **Step 3: Implement pass responsibilities**

Pass 1 inserts file paths and record keys while checking descriptor bytes/digests, local shape/order/privacy. Pass 2 inserts typed references, resolves the closed matrix, verifies evidence locators/fingerprints, recomputes semantic digest/counts/coverage/flow membership, and validates the manifest. Neither pass builds a complete PHP array. `GraphV2ValidationRunService` alone locks the validating import, requires no live lease, increments attempts, creates a random 256-bit raw token, stores only its lower-case SHA-256, sets start/heartbeat `now()` and the Task 4 baseline lease `now()+5 minutes`, commits, then dispatches after commit. The validator heartbeats before every chunk, after every 1,000 records, and before 30 seconds monotonic elapsed; every heartbeat/final write CAS-matches import ID, import generation, status, validation attempt, and token hash. Runs 1–3 schedule only the acquisition wrapper after exactly 10/30/90 seconds; an uncaught crash relies on lease-expiry reconciliation under the same four-run ceiling. **Historical-boundary note:** Task 6A replaces this baseline lease/runtime with the additive `000375` execution-generation fields, renewable 120-second fencing, and the dedicated `1900 > 1800 > 1740` queue invariant. Do not carry the five-minute authority into the release implementation or edit Task 4's already-accepted migrations to retrofit it.

- [ ] **Step 4: Run GREEN and commit**

Run: `docker compose exec -T app php artisan test tests/Integration/Graph/GraphV2ImportValidationTest.php`

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

Run: `docker compose exec -T app php artisan test tests/Integration/Graph/CanonicalGraphV2ProjectionTest.php`

- [ ] **Step 3: Implement schema and mapper**

Compute namespace key as SHA-256 JCS of `{project_id,source_scope_type,source_scope_id,projection_version,public_id}`. Project `CanonicalLifecycleFlow`/`CanonicalLifecycleStep` and relationships exactly as section 9.4. The mapper validates and copies producer membership; it never traverses or assigns stages.

- [ ] **Step 4: Run GREEN and commit**

Run: `docker compose exec -T app php artisan hades:graph-v2:init-schema --verify`

Run: `docker compose exec -T app php artisan test tests/Integration/Graph/CanonicalGraphV2ProjectionTest.php`

```bash
git add backend/app/Services/Graph/V2/Neo4jGraphV2Schema.php backend/app/Services/Graph/V2/GraphV2LifecycleProjectionMapper.php backend/app/Console/Commands/InitializeCanonicalGraphV2Schema.php backend/app/Services/Graph/Neo4jCanonicalGraphProjector.php backend/tests/Integration/Graph/CanonicalGraphV2ProjectionTest.php
git commit -m "feat(graph): project isolated lifecycle v2 namespaces"
```

### Task 6: Add Desired-Generation Projection CAS and Retry Cycle

**Files:**
- Create: `backend/database/migrations/2026_07_16_000375_add_graph_v2_coordination_state.php`
- Create: `backend/app/Jobs/ProjectCanonicalGraphV2.php`
- Create: `backend/app/Events/CanonicalGraphV2ProjectionActivated.php`
- Create: `backend/app/Services/Graph/CanonicalGraphDesiredProjectionService.php`
- Create: `backend/app/Services/Graph/Retention/GraphArtifactReferenceLock.php`
- Create: `backend/app/Services/Graph/Retention/GraphArtifactReferenceGuard.php`
- Create: `backend/app/Console/Commands/ReconcileCanonicalGraphV2.php`
- Create: `backend/app/Console/Commands/CleanupCanonicalGraphV2.php`
- Modify: `backend/app/Services/Graph/V2/GraphV2ValidationRunService.php`
- Modify: `backend/app/Services/Graph/V2/GraphV2ArtifactReader.php`
- Modify: `backend/app/Services/Graph/V2/Neo4jGraphV2Schema.php`
- Modify: `backend/app/Services/Graph/V2/GraphV2LifecycleProjectionMapper.php`
- Modify: `backend/app/Services/Graph/V2/GraphV2ImportService.php`
- Modify: `backend/app/Services/Graph/Neo4jCanonicalGraphProjector.php`
- Modify: `backend/app/Jobs/ValidateGraphV2Import.php`
- Modify: `backend/app/Services/Graph/CanonicalGraphProjectionService.php`
- Modify: `backend/app/Models/HadesGraphImport.php`
- Modify: `backend/config/queue.php`
- Modify: `docker-compose.devboard.yaml`
- Modify: `docker-compose.devboard.prod.yaml`
- Modify: `backend/routes/console.php`
- Test: `backend/tests/Integration/Graph/CanonicalGraphV2ProjectionRetryTest.php`
- Test: `backend/tests/Feature/Postgres/GraphV2ConcurrencyPostgresTest.php`
- Test: `backend/tests/Unit/Graph/GraphV2QueueRuntimeContractTest.php`
- Test: `backend/tests/Feature/Graph/GraphV2CoordinationMigrationTest.php`
- Test: `backend/tests/Integration/Graph/GraphV2ValidationExecutionTest.php`
- Test: `backend/tests/Feature/Postgres/GraphArtifactReferenceLockPostgresTest.php`

**Mandatory execution slices (never combine in one agent session):**

1. **6A — PostgreSQL/queue/validation/desired intent (max 150 min):** additive `000375`, exact upgrade/backfill, durable uncertainty index, dedicated queue runtime, validation execution/delivery fencing, and atomic `desired_graph_import_id` write. Its only new test owners are `GraphV2CoordinationMigrationTest.php`, `GraphV2ValidationExecutionTest.php`, and `GraphV2QueueRuntimeContractTest.php`; these contain no reference-lock, Neo4j-incarnation, or publication expectation. Stop after the exact 6A commands below, review, and commit `feat(graph): persist fenced desired projection work`.
2. **6B — reference-lock security boundary (max 90 min):** implement `GraphArtifactReferenceLock`/unforgeable guard plus two-connection races. Its only new test owner is `GraphArtifactReferenceLockPostgresTest.php`; it uses a minimal test callback and contains no projector/reconciler expectation. Stop after the exact 6B command, security review, and commit `feat(graph): guard graph artifact references`.
3. **6C — projection incarnation/CAS (max 150 min):** Neo4j definition upgrade, fresh-incarnation projection attempts, projection delivery claim/fenced heartbeats, two short DB phases, activation event/reconcile, and exact-incarnation cleanup compatibility. It owns `CanonicalGraphV2ProjectionRetryTest.php` and the cross-component `GraphV2ConcurrencyPostgresTest.php`, which may now rely on integrated 6A+6B. Stop after the exact 6C commands, review, and commit `feat(graph): publish fenced projection incarnations`.

Each slice starts from clean pulled `main` after the accepted previous slice was reviewed, integrated, tested, and pushed. It has a clean slice-only diff and may fix only findings caused by that slice. At the time limit it must stop with a checkpoint (SHA, tests, remaining blocker), never drift into the next slice. Task 7 is blocked until all three slices, reviews, and main integrations are green.

**Closed slice commands and allowlists:**

```bash
# 6A RED/GREEN; guarded upgrade/fresh-schema coverage is inside GraphV2CoordinationMigrationTest.
docker compose exec -T app php artisan test tests/Feature/Graph/GraphV2CoordinationMigrationTest.php tests/Integration/Graph/GraphV2ValidationExecutionTest.php tests/Unit/Graph/GraphV2QueueRuntimeContractTest.php

# 6B RED/GREEN; run only on guarded devboard_acceptance PostgreSQL.
docker compose exec -T app composer test:postgres -- tests/Feature/Postgres/GraphArtifactReferenceLockPostgresTest.php

# 6C RED/GREEN after integrated 6A+6B.
docker compose exec -T app php artisan test tests/Integration/Graph/CanonicalGraphV2ProjectionRetryTest.php
docker compose exec -T app composer test:postgres -- tests/Feature/Postgres/GraphV2ConcurrencyPostgresTest.php
```

If a slice discovers that a required production edit or test lies outside its listed files, it stops and requests a one-line plan amendment before editing. It never borrows a later slice's production path merely to make a shared suite green.

**Interfaces:**

```php
final class ProjectCanonicalGraphV2 implements ShouldQueue, ShouldBeUniqueUntilProcessing
{
    public int $tries = 1;
    public int $timeout = 1740;
    public bool $failOnTimeout = true;
    public int $uniqueFor = 300;
    public function uniqueId(): string; // graph-projection:{project_id}:{workspace_binding_id}:{desired_generation}
}

final class ValidateGraphV2Import implements ShouldQueue, ShouldBeEncrypted, ShouldBeUniqueUntilProcessing
{
    public int $tries = 1;
    public int $timeout = 1740;
    public bool $failOnTimeout = true;
    public int $uniqueFor = 300;
    public function uniqueId(): string; // graph-import:{import_id}:{attempt_generation}:validation:{validation_attempts}; never raw token
}

final readonly class CanonicalGraphV2ProjectionActivated
{
    public function __construct(
        public string $projectId,
        public string $workspaceBindingId,
        public string $projectionHeadId,
        public string $activeProjectionId,
        public string $artifactGraphVersion,
        public string $projectionVersion,
        public int $desiredGeneration,
    ) {}
}

final readonly class DesiredGraphProjection
{
    public function __construct(
        public string $headId,
        public string $graphImportId,
        public int $desiredGeneration,
        public string $artifactGraphVersion,
        public string $verificationSetHash,
        public string $projectionVersion,
        public bool $changed,
    ) {}
}

final class CanonicalGraphDesiredProjectionService
{
    public function recordWithinCurrentTransaction(
        HadesGraphImport $lockedImport,
        string $verificationSetHash,
    ): DesiredGraphProjection;

    public function lockHeadWithinCurrentTransaction(
        string $projectId,
        string $workspaceBindingId,
    ): CanonicalGraphProjectionHead;
}

final class GraphArtifactReferenceLock
{
    /**
     * @template T
     * @param list<string> $importIds
     * @param Closure(GraphArtifactReferenceGuard, Collection<int, HadesGraphImport>): T $criticalSection
     * @return T
     */
    public function within(
        string $projectId,
        string $workspaceBindingId,
        array $importIds,
        Closure $criticalSection,
    ): mixed;
}

final class GraphArtifactReferenceGuard
{
    private function __construct(
        private GraphArtifactReferenceLock $issuer,
        private string $invocationId,
    ) {}

    /** @internal Requires a one-use 256-bit nonce held only by GraphArtifactReferenceLock. */
    public static function issue(
        GraphArtifactReferenceLock $issuer,
        string $oneUseNonce,
    ): self;

    public function assertHolds(string $graphImportId): void;
}
```

- [ ] **Step 1: Add only the current slice's RED tests**

Assert active projection stays readable through candidate build. The application service owns exactly four projection attempts per desired generation, each in a fresh projection row/physical incarnation, with delays 10/30/90 seconds before attempts 2/3/4; the queue job itself has one delivery try. Failures 1–3 do not set head failure, attempt 4 sets the exact failed generation/version, stale generation is success no-op, newer generation cannot be overwritten, exact previous projection can swap without rewrite, invalid previous refuses rebuild/delete, and reconcile does not redispatch a blocked generation. Assert `CanonicalGraphV2ProjectionActivated` is dispatched exactly once after the successful CAS transaction commits and zero times for failed attempts, stale/no-op generations, already-active projections, or rolled-back publication. Kill the process after validation's durable desired-head commit but before dispatch and prove scheduled reconciliation dispatches that generation.

Prove delivery fencing for both long-running jobs. Acquisition resets the matching `*_execution_delivery_claimed_at` to null. Two simultaneous deliveries of the same validation payload/token/generation race the null-to-timestamp job-entry CAS: exactly one may read chunks or heartbeat and the loser exits as a successful no-op without consuming a domain attempt. After a 120-second heartbeat lease expires, reconciliation creates a new owner, increments `validation_execution_generation`, resets the claim field, and every heartbeat/final CAS by the old token fails. Projection attempts use the same one-shot delivery claim, `execution_generation`, random 256-bit raw owner token/stored SHA-256, heartbeat, and lease fields; a duplicate same-token projection delivery cannot touch PostgreSQL or Neo4j, each acquired retry creates a fresh candidate incarnation, and an expired owner may be replaced only by reconciliation after the lease. Pause old Neo4j attempt A, expire/steal it, publish attempt B, then let A resume: queries and publication still see only B. Add a permanent per-incarnation `CanonicalProjectionFence` and prove (a) a batch holding its fence makes cleanup wait and cleanup then removes the committed batch, (b) cleanup winning first makes the stale batch write zero, (c) cleanup can create a `deleting` fence before a never-started old writer, (d) after A's PostgreSQL projection row is deleted its old token still cannot revive the retired fence or create canonical records, and (e) same-version incarnation B is unaffected. The old worker cannot finalize, clear, delete B, or create an orphan A.

Instrument two imports and prove `GraphArtifactReferenceLock` rejects entry from an existing transaction and an empty ID list, rejects a missing/cross-scope/tombstoned import, deduplicates/sorts IDs, acquires every advisory lock before any import row, acquires every import row before head/attempt rows, returns the callback collection in normalized ID order, and rolls back the reference write with the callback. Prove the guard constructor is inaccessible, an invalid/reused issuance nonce is rejected, the only issuance call site is `GraphArtifactReferenceLock`, and `assertHolds()` checks an active issuer registry entry bound to the exact DB connection, transaction level, project/binding, import IDs, and `artifact_cleanup_state=active`. Leak a real guard from success and rollback callbacks and prove it is revoked in `finally` and unusable after callback return, commit, rollback, or inside another transaction. Race concurrent cleanup and require the post-`FOR UPDATE` count/scope/state recheck to abort. Cover candidate/attempt creation, final publication CAS, scheduled reconciliation, and `--retry-failed` separately.

The PostgreSQL acceptance test uses two physical connections to prove advisory/row-lock order, blocking, guard transaction binding, cleanup races, and generation CAS. SQLite may test pure state decisions but is not evidence for those invariants.

Start from a database that has the already-applied `000100`–`000350` migrations and prove the new additive `000375` migration adds, without rewriting historical migration bytes: (a) `canonical_graph_projection_heads.desired_graph_import_id`, nullable but all-or-none with the existing desired artifact/hash/version tuple, indexed, with a v2 check `source_scope_type='workspace_binding'`, and protected by scoped composite FK `(project_id,source_scope_id,desired_graph_import_id)` → `hades_graph_imports(project_id,workspace_binding_id,id)` with `RESTRICT`; add the required parent unique key `(project_id,workspace_binding_id,id)` first; (b) validation and projection-attempt execution generation/token-hash/heartbeat/lease fields plus nullable timezone-aware `*_execution_delivery_claimed_at`; (c) `hades_graph_imports.artifact_cleanup_state=active|tombstoned|objects_deleted`, nullable operation ULID/start/object-deleted timestamps; (d) `canonical_graph_projections.namespace_cleanup_state=active|tombstoned|neo4j_deleted`, nullable operation ULID/start/Neo4j-deleted timestamps; and (e) durable `hades_graph_import_uncertainty_index` rows keyed by `(graph_import_id,public_id)` with scoped import FK, `record_ordinal`, `chunk_index`, and indexes beginning `(graph_import_id,public_id)` and `(graph_import_id,record_ordinal)`. Because retries require distinct incarnation rows for one logical version, `000375` drops the already-applied `canonical_graph_projections_v2_scope_version_unique` and replaces it with a non-unique scoped lookup index; projection-row ULID remains the incarnation identity and head CAS remains the sole active uniqueness authority. Backfill each existing nonempty desired tuple by the unique same-scope import whose artifact version matches; abort the migration with a diagnostic on zero or multiple matches. Test two same-version imports, two same-version candidate incarnations, all-or-none desired values, non-workspace head rejection, cross-scope rejection, parent-delete restriction, delivery-claim null/reset/CAS behavior, and durable uncertainty rows after validation staging cleanup. Add forward-only state/nullable-column checks and model casts. Neither cleanup machine can return to `active`. This migration must be green both as an upgrade and in a fresh guarded PostgreSQL build.

Refactor Task 4's existing validation-success path here: while its validating import row is still locked, the successful CAS calls `CanonicalGraphDesiredProjectionService::recordWithinCurrentTransaction($import, hashJcs([]))` before the transaction commits. This is the sole import-owner finalization exception to the later generic reference-writer lock: validation already owns the exact `validating` import row, and cleanup selects only terminal imports. The service requires an open transaction, revalidates project/binding/import and active cleanup state, locks or conflict-safely creates the exact head, and changes `desired_generation` only when the complete tuple `(desired_graph_import_id,artifact_graph_version,verification_set_hash,projection_version)` differs. Rollback leaves neither `validated` nor the head intent. Kill after commit but before the dispatch callback and prove the minute reconciler recovers it. No Task 1–5 migration is edited.

Before validation staging rows can be removed, the successful finalization transaction copies only verified `uncertainties` locators into `hades_graph_import_uncertainty_index` and checks its count against the manifest. The durable index lives until its import is finally deleted; it is not a temporary validation table. Add `GraphV2ArtifactReader::recordsByPublicIds(HadesGraphImport $import, string $kind, array $publicIds): iterable`, restricted to `kind=uncertainties`, which resolves this index, streams chunk records, and verifies public ID/ordinal/chunk equality. Plan 3 reconciliation must work after all disposable validation key/reference rows have been deleted.

Create a dedicated database queue connection and queue, both named `graph-v2`, and a dedicated `graph-v2-worker` in development and production Compose. Its runtime invariant is fixed and mechanically tested from `config/queue.php` and both Compose files: connection `retry_after=1900s` > worker `--timeout=1800s` > job `$timeout=1740s`; Task 6 replaces Task 4's initial five-minute validation run lease with one renewable 120-second execution-owner lease, heartbeat at most every 30s, so a live owner renews long before expiry. Both jobs dispatch explicitly to connection/queue `graph-v2`, use `$tries=1`, `$failOnTimeout=true`, and bounded `ShouldBeUniqueUntilProcessing` keys with `$uniqueFor=300`; uniqueness only coalesces queued messages and releases when processing begins. The literal formatters are frozen and table-tested: validation `graph-import:{import_id}:{attempt_generation}:validation:{validation_attempts}` (vector `graph-import:01ARZ3NDEKTSV4RRFFQ69G5FAV:7:validation:3`) and projection `graph-projection:{project_id}:{workspace_binding_id}:{desired_generation}` (vector `graph-projection:01ARZ3NDEKTSV4RRFFQ69G5FAV:01BX5ZZKBKACTAV9WEVGEMMVRZ:42`). Four domain attempts are rows/state transitions, never broker retries. A duplicate broker delivery cannot consume a domain attempt unless it successfully acquires a new expired lease. Maintenance deferral consumes neither a broker nor a domain attempt. Tests freeze these values, simulate crash/redelivery, wait past uniqueness expiry, and prove reconciliation eventually redispatches without concurrent namespace writers.

- [ ] **Step 2: Run the exact RED command for 6A, 6B, or 6C from the closed slice block above**

Do not run a later slice's new suite as the current slice's RED/GREEN gate. Existing broad regressions may be run read-only after the focused GREEN, but failures caused solely by not-yet-implemented later slices are not repaired early and do not authorize scope expansion.

- [ ] **Step 3: Implement transactional publication**

`GraphArtifactReferenceLock::within()` must reject an already-open transaction and an empty ID list, normalize unique import IDs, read every tuple constrained by the explicit project and binding arguments, require an exact one-to-one ID match, sort by `(project_id,workspace_binding_id,id)`, and open the outer transaction. For each tuple it executes `SELECT pg_advisory_xact_lock(hashtextextended(?, 0))` with the canonical key `hades:graph-import:v2:{project_id}:{workspace_binding_id}:{import_id}`, then locks all import rows `FOR UPDATE` in the same order. Recheck exact row count, scope, and `artifact_cleanup_state=active` after those row locks, then pass the models to the callback in normalized input-ID order.

Guard issuance is runtime-enforced, not an `@internal` convention. The lock generates a random 256-bit nonce, stores a one-use private issuance record bound to invocation ID, DB connection identity, open transaction level, project/binding, normalized IDs, and active cleanup state, and calls the guard's private-constructor factory. Issuance consumes the nonce. `assertHolds()` calls back into the issuer registry and fails when the invocation is absent/revoked, the transaction/connection changed, the ID/scope differs, or cleanup is no longer active. The lock revokes/removes the registry entry in `finally` before commit/rollback can return, even if the callback leaks the guard or throws. Missing, deleted, scope-mismatched, or tombstoned imports abort before domain locks or writes.

Queue after commit with a two-second delay and the exact generation-specific `graph-projection:{project_id}:{workspace_binding_id}:{desired_generation}` key. The Task 6 refactor persists the desired head tuple in the same transaction as validation success; the after-commit dispatch is only a latency optimization. `hades:graph-v2:reconcile` scans durable heads every minute and dispatches only when desired differs from active, no live attempt execution lease exists, and the same generation is not blocked, so a lost callback cannot lose projection work. `ShouldBeUniqueUntilProcessing` may coalesce queued duplicates but is never the runtime mutex.

Publication has exactly two short PostgreSQL reference-lock transactions with Neo4j work strictly between them. Before transaction A or any Neo4j read, `ProjectCanonicalGraphV2` must win the attempt row's null-to-timestamp `projection_execution_delivery_claimed_at` CAS on the full attempt/generation/token tuple; a zero-row duplicate exits successfully without side effects. Transaction A enters `GraphArtifactReferenceLock`, locks the head then relevant attempt rows, revalidates the desired generation/import and winning delivery claim, and creates/claims a fresh candidate projection row whose ULID is the immutable `projection_incarnation_id`; then it commits. The winning delivery performs the full isolated build/validation while `DB::transactionLevel()===0`, heartbeating through fenced short transactions. Transaction B reacquires `GraphArtifactReferenceLock`, locks the head then attempt/candidate rows in the same order, CAS-matches desired generation plus execution generation/token and non-null delivery claim, revalidates desired import/version and `namespace_cleanup_state=active`, and performs the final ready/active/previous CAS. No database advisory or row lock spans the Neo4j call. Reconciliation and `--retry-failed` likewise pre-read IDs, enter the reference lock, and only then lock head/attempt rows; they never start from a head lock.

Task 6 extends Task 5's physical namespace: every v2 Neo4j node, flow, step, and relationship carries `projection_incarnation_id=canonical_graph_projections.id`; every uniqueness key, mapper write, validation, read, and exact delete includes it in addition to project/scope/binding/version. A retry never clears/reuses an older incarnation. Publication stores the winning projection row; contexts resolve it and queries filter it. Final validation rejects any required record missing from the candidate incarnation, while records from another incarnation are invisible. Artifact object keys likewise include immutable `graph_import_id` before filename/digest; no import retry may overwrite another import's key.

Every incarnation also owns the design section 9.4 `CanonicalProjectionFence`. `Neo4jGraphV2Schema` adds/verifies its named unique constraint and scoped index. Projector setup can initialize `building` only for the exact execution generation/token. Every mutation batch is at most 1,000 records, uses a server-side 60-second Neo4j transaction timeout, first takes the exact fence write lock by changing `lock_nonce`, then rereads `state=building` and the exact token hash before any canonical write. Query-shape tests reject a projector mutation without that prefix. Cleanup changes the same permanent fence to `deleting`, deletes/proves zero exact-incarnation records, then leaves it `retired` forever. These immutable identities and the negative fence make a late Neo4j transaction unable to recreate an orphan even after its PostgreSQL owner row is gone.

`Neo4jGraphV2Schema` now compares each named v2 constraint/index definition, not only its name. Its explicit forward upgrade recognizes the exact Task 5 definitions, drops/recreates only those named v2 objects with `projection_incarnation_id`, adds the projection-fence definitions, and leaves every legacy/unrelated Neo4j object untouched. Tests bootstrap the accepted Task 5 schema, run the upgrade twice, verify exact definitions, and prove existing Task 5 data is not considered readable until rebuilt into an incarnation behind its fence. This upgrade is implemented but not run live in Plan 2 Task 6; isolated acceptance runs it under the maintenance contract introduced by Plan 2 Task 10, and Plan 6 Task 10 repeats that token-bound global `cutover` procedure before the first production reimport.

On success update the prior active/previous rows' grace timestamps, set the candidate ready, rotate active/previous, capture the exact successful CAS result, and register one `DB::afterCommit()` callback that dispatches `CanonicalGraphV2ProjectionActivated` from those captured values. Do not reread a possibly newer head to construct the event, and do not dispatch it from an attempt/failure handler, stale/no-op path, already-active early return, or before commit. Plan 2 must not import or resolve `VerificationQueueService`; the Plan 3 listener owns reconciliation and always rereads current state.

- [ ] **Step 4: Run GREEN and commit**

Run the exact focused GREEN command for the current slice from the closed slice block, then its repository's pre-existing regression/lint command.

Commit only the current mandatory slice:

```bash
# 6A
git add -- backend/database/migrations/2026_07_16_000375_add_graph_v2_coordination_state.php backend/app/Jobs/ValidateGraphV2Import.php backend/app/Services/Graph/CanonicalGraphDesiredProjectionService.php backend/app/Services/Graph/V2/GraphV2ValidationRunService.php backend/app/Services/Graph/V2/GraphV2ArtifactReader.php backend/app/Services/Graph/V2/GraphV2ImportService.php backend/app/Models/HadesGraphImport.php backend/config/queue.php docker-compose.devboard.yaml docker-compose.devboard.prod.yaml backend/routes/console.php backend/tests/Feature/Graph/GraphV2CoordinationMigrationTest.php backend/tests/Integration/Graph/GraphV2ValidationExecutionTest.php backend/tests/Unit/Graph/GraphV2QueueRuntimeContractTest.php
git commit -m "feat(graph): persist fenced desired projection work"

# 6B, in the next session after 6A review
git add -- backend/app/Services/Graph/Retention/GraphArtifactReferenceLock.php backend/app/Services/Graph/Retention/GraphArtifactReferenceGuard.php backend/tests/Feature/Postgres/GraphArtifactReferenceLockPostgresTest.php
git commit -m "feat(graph): guard graph artifact references"

# 6C, in the next session after 6B review
git add -- backend/app/Jobs/ProjectCanonicalGraphV2.php backend/app/Events/CanonicalGraphV2ProjectionActivated.php backend/app/Console/Commands/ReconcileCanonicalGraphV2.php backend/app/Console/Commands/CleanupCanonicalGraphV2.php backend/app/Services/Graph/V2/Neo4jGraphV2Schema.php backend/app/Services/Graph/V2/GraphV2LifecycleProjectionMapper.php backend/app/Services/Graph/Neo4jCanonicalGraphProjector.php backend/app/Services/Graph/CanonicalGraphProjectionService.php backend/tests/Integration/Graph/CanonicalGraphV2ProjectionRetryTest.php backend/tests/Feature/Postgres/GraphV2ConcurrencyPostgresTest.php
git commit -m "feat(graph): publish fenced projection incarnations"
```

### Task 7: Implement Signed Contexts, Handles, Cursors, and Search Snapshots

**Files:**
- Create: `backend/app/Services/Graph/DashboardGraphContext.php`
- Create: `backend/app/Services/Graph/GraphCallerPrincipal.php`
- Create: `backend/app/Services/Graph/GraphProjectionIdentity.php`
- Create: `backend/app/Services/Graph/VerifiedGraphContext.php`
- Create: `backend/app/Services/Graph/GraphCursorPayload.php`
- Create: `backend/app/Services/Graph/DashboardGraphSearchSnapshot.php`
- Test: `backend/tests/Unit/Graph/DashboardGraphContextTest.php`
- Test: `backend/tests/Integration/Graph/DashboardGraphSearchSnapshotTest.php`

**Interfaces:**

```php
final class DashboardGraphContext
{
    public function issue(GraphCallerPrincipal $principal, Project $project, GraphProjectionIdentity $projection): string;
    public function verify(string $token, GraphCallerPrincipal $principal, Project $project): VerifiedGraphContext;
    public function issueHandle(VerifiedGraphContext $context, string $type, string $publicId): string;
    public function verifyHandle(string $handle, VerifiedGraphContext $context, string $expectedType): string;
    public function issueCursor(VerifiedGraphContext $context, GraphCursorPayload $payload): string;
    public function verifyCursor(string $cursor, VerifiedGraphContext $context, string $expectedQueryType, string $expectedFiltersSha256): GraphCursorPayload;
}

final readonly class GraphProjectionIdentity
{
    public function __construct(
        public string $projectionId,
        public string $projectId,
        public string $workspaceBindingId,
        public string $artifactGraphVersion,
        public string $verificationSetHash,
        public string $projectionVersion,
        public CarbonImmutable $generatedAt,
    ) {}
}

final readonly class GraphCallerPrincipal
{
    public function __construct(
        public string $type, // dashboard_session|hades_device
        public string $subjectId, // authenticated user ID or Hades agent ULID
        public string $credentialBinding, // lower-case HMAC-SHA256
    ) {}
}

final readonly class VerifiedGraphContext
{
    public function __construct(
        public string $principalType,
        public string $principalId,
        public string $credentialBinding,
        public string $projectId,
        public string $workspaceBindingId,
        public string $projectionId,
        public string $verificationSetHash,
        public string $projectionVersion,
        public CarbonImmutable $issuedAt,
        public CarbonImmutable $expiresAt,
    ) {}
}

final readonly class GraphCursorPayload
{
    /** @param list<string|int> $lastSortTuple */
    public function __construct(
        public string $queryType,
        public string $filtersSha256,
        public array $lastSortTuple,
        public ?string $searchSnapshotId,
    ) {}
}

final class DashboardGraphSearchSnapshot
{
    /** @param list<array{public_id:string,vector_rank:int,score:float}> $candidates */
    public function capture(
        VerifiedGraphContext $context,
        string $filtersSha256,
        array $candidates,
    ): string;

    /** @return list<array{public_id:string,vector_rank:int,score:float}> */
    public function load(
        string $snapshotId,
        VerifiedGraphContext $context,
        string $filtersSha256,
    ): array;

    public function forget(string $snapshotId): void;
}
```

- [ ] **Step 1: Add RED token/snapshot tests**

Test canonical base64url payload, domain-separated HMAC/HKDF with `APP_KEY`, 30-minute TTL, principal/project/binding/projection/verification-set authorization, inactive/expired version 409, invalid signature/type/filter 422, context-bound handle/cursor, one captured vector rank per snapshot, maximum 200 candidates, pagination without new similarity query, TTL cleanup, cache miss, and cross-session, cross-dashboard-user, cross-plugin-token, cross-Hades-agent, cross-device, cross-project/projection/filter rejection. `verifyCursor()` rejects wrong query/filter/context/expiry before exact tuple decoding; tuples reject floats and validate per-query arity/types. Golden preimages prove dashboard/Hades domain separation. Assert no snapshot table/model/FK is created and a projection leaving active/previous remains namespace-retained for the full 40-minute grace, exceeding every snapshot TTL.

- [ ] **Step 2: Run RED**

Run: `docker compose exec -T app php artisan test tests/Unit/Graph/DashboardGraphContextTest.php tests/Integration/Graph/DashboardGraphSearchSnapshotTest.php`

- [ ] **Step 3: Implement closed token payloads and snapshot storage**

Decode Laravel `APP_KEY` exactly once: strict-base64 decode a `base64:` suffix, otherwise use raw UTF-8 bytes; reject fewer than 32 bytes. The graph-context payload is exactly `{v:2,principal_type,principal_id,credential_binding,project_id,scope_type:"workspace_binding",scope_id,projection_id,verification_set_hash,projection_version,issued_at,expires_at}`. Dashboard binding is lower-case HMAC-SHA256(root key, `hades.graph-principal.dashboard.v2\0 || JCS({user_id,session_id})`) from authenticated user/server session. Hades-device binding is HMAC-SHA256(root key, `hades.graph-principal.device.v2\0 || JCS({api_token_id,hades_agent_id,device_id})`) from authenticated server rows; device-bound access is required. Raw session IDs/tokens and client hashes are never accepted.

Context signatures are HMAC-SHA256(root key, `hades.graph-context.v2\0 || JCS(payload)`). Handle/cursor payload fields remain exactly section 10.1; principal fields are not duplicated. Derive their signing key with HKDF-SHA256(root key, 32 bytes, salt=`SHA256(raw graph-context token)`, info=`hades.graph-context-bound-token.v2`). Thus they verify only with the exact issuing context/principal. `verifyHandle()` and `verifyCursor()` are the sole decoders; controllers/repositories never duplicate signature logic.

Search snapshots are deliberately ephemeral existing-Laravel-cache values, not database rows and not graph-import references. Generate a cryptographically random 256-bit base64url ID and store under `hades:graph-v2:search-snapshot:{sha256(id)}` for `min(context.expires_at, now()+30 minutes)`. The closed cache object is `{schema:"hades.graph_search_snapshot.v2",principal_type,principal_id,credential_binding,project_id,workspace_binding_id,projection_id,verification_set_hash,projection_version,filters_sha256,candidates:[{public_id,vector_rank,score}],expires_at}` with at most 200 candidates in strict rank order. `load()` validates every binding field and expiry before returning candidates; mismatch/miss deletes nothing and throws `graph_search_snapshot_expired`. Vector score stays internal to the snapshot and never enters a cursor/order token. Cache values are not enumerated for retention: the 40-minute namespace grace is the sole safety invariant.

- [ ] **Step 4: Run GREEN and commit**

```bash
docker compose exec -T app php artisan test tests/Unit/Graph/DashboardGraphContextTest.php tests/Integration/Graph/DashboardGraphSearchSnapshotTest.php
git add backend/app/Services/Graph/DashboardGraphContext.php backend/app/Services/Graph/GraphCallerPrincipal.php backend/app/Services/Graph/GraphProjectionIdentity.php backend/app/Services/Graph/VerifiedGraphContext.php backend/app/Services/Graph/GraphCursorPayload.php backend/app/Services/Graph/DashboardGraphSearchSnapshot.php backend/tests/Unit/Graph/DashboardGraphContextTest.php backend/tests/Integration/Graph/DashboardGraphSearchSnapshotTest.php
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
    /**
     * @param array<string,mixed> $request validated by dashboard-query.schema.json
     * @return array<string,mixed> validated by dashboard-response.schema.json
     */
    public function query(GraphCallerPrincipal $principal, Project $project, array $request): array;
}
```

Supported discriminators are exactly `scopes|overview|entrypoints|lifecycle|lifecycle_expand|search|detail|neighborhood|impact|path`.
Do not invent PHP classes named `DashboardGraphRequest`, `DashboardGraphResponse`, or `CountKnowledge`. The live graph stack is array-oriented: both closed JSON schemas are the runtime boundary, and `CountKnowledge` is the exact closed JSON object defined in the root contract/schema.

- [ ] **Step 1: Add RED protocol matrix**

Test every query type, exact closed request/response DTO, scopes with null context/projection, overview context adoption, bounded limits/cursors, entrypoint labels/search, lifecycle backbone/stage counts/branches/terminals/async summaries, expand selector, search match basis, detail, neighborhood, impact, path success/no-path/unknown, false-zero prevention, stale context 409, not-ready error, invalid handle/cursor 422, and tenant isolation.

- [ ] **Step 2: Run RED**

Run: `docker compose exec -T app php artisan test tests/Feature/Dashboard/GraphExplorerV2ControllerTest.php`

- [ ] **Step 3: Implement exact active-v2 queries**

Each controller derives `GraphCallerPrincipal` only from authenticated server state: dashboard user+session or Hades token+agent+device. Client payloads never supply principal fields. Repository first resolves the head active projection for project/binding, then every Neo4j query supplies all namespace fields including the winning projection incarnation. Use stable sort tuples from section 10.1. Return `CountKnowledge`; never coalesce unknown to zero. Initial lifecycle includes at most 120 canonical nodes.

- [ ] **Step 4: Run GREEN and commit**

Run: `docker compose exec -T app php artisan test tests/Feature/Dashboard/GraphExplorerV2ControllerTest.php`

```bash
git add backend/app/Services/Graph/CanonicalGraphRepository.php backend/app/Services/Graph/CanonicalGraphQueryService.php backend/app/Services/Graph/DashboardGraphExplorerService.php backend/app/Http/Controllers/Dashboard/Api/DashboardGraphExplorerController.php backend/routes/web.php backend/tests/Feature/Dashboard/GraphExplorerV2ControllerTest.php
git commit -m "feat(graph): expose dashboard lifecycle protocol v2"
```

### Task 9: Align the Service-Gated Plugin Graph Query Surface

**Files:**
- Modify: `backend/app/Http/Controllers/Plugin/GraphQueryController.php`
- Create: `backend/app/Http/Controllers/Hades/GraphQueryController.php`
- Delete: `backend/app/Http/Controllers/Hades/GraphTraversalController.php`
- Modify: `backend/app/Http/Controllers/Hades/CapabilitiesController.php`
- Modify: `backend/app/Http/Controllers/Hades/HealthController.php`
- Modify: `backend/config/devboard.php`
- Modify: `backend/routes/api.php`
- Test: `backend/tests/Feature/Hades/PluginGraphQueryV2Test.php`
- Modify: `backend/tests/Feature/Hades/HadesNoCodebaseDiagnosisTest.php`
- Modify: `backend/tests/Feature/Hades/HadesM3SharedMemoryTest.php`

**Interfaces:**
- Plugin and Hades-device graph query return the same v2 handles, context, completeness, and bounded semantics as dashboard service.
- Vector ranking returns handles/candidates only; topology always calls `DashboardGraphExplorerService`/current projection.
- The sole Hades-device endpoint is `POST /api/hades/v1/graph/query` with exactly the closed `dashboard-query.schema.json` body. Project and workspace-binding IDs are derived from authenticated Hades token/agent/device/binding server rows and are not body/query fields; client-supplied scope or principal keys fail closed because `additionalProperties:false`. `GET /api/hades/v1/graph/traverse` is removed, returns 404, and is absent from capability/health/OpenAPI advertisements. There is no v1 response adapter or fallback.
- Hades-device graph read requires the existing `project_inspection` capability plus linked project/binding/device checks. Do not invent a second read capability. This preserves the established read-only capability contract; verification mutation still separately requires `verify_project_graph`.

- [ ] **Step 1: Add RED plugin tests**

Test plugin and Hades capability gates, server-derived principal, project/binding/device isolation, old traverse route absence, exact request/response schema parity between dashboard/plugin/Hades, cross-principal context rejection, vector candidate resolution through exact active context, no local/repository fallback, route/symbol search, and unknown counts. The authenticated device principal must never be replaced with the owning admin user.

- [ ] **Step 2: Run RED**

Run: `docker compose exec -T app php artisan test tests/Feature/Hades/PluginGraphQueryV2Test.php tests/Feature/Hades/HadesNoCodebaseDiagnosisTest.php tests/Feature/Hades/HadesM3SharedMemoryTest.php`

- [ ] **Step 3: Reuse dashboard services, run GREEN, commit**

```bash
docker compose exec -T app php artisan test tests/Feature/Hades/PluginGraphQueryV2Test.php tests/Feature/Hades/HadesNoCodebaseDiagnosisTest.php tests/Feature/Hades/HadesM3SharedMemoryTest.php
git add backend/app/Http/Controllers/Plugin/GraphQueryController.php backend/app/Http/Controllers/Hades/GraphQueryController.php backend/app/Http/Controllers/Hades/GraphTraversalController.php backend/app/Http/Controllers/Hades/CapabilitiesController.php backend/app/Http/Controllers/Hades/HealthController.php backend/config/devboard.php backend/routes/api.php backend/tests/Feature/Hades/PluginGraphQueryV2Test.php backend/tests/Feature/Hades/HadesNoCodebaseDiagnosisTest.php backend/tests/Feature/Hades/HadesM3SharedMemoryTest.php
git commit -m "feat(graph): align plugin queries with graph v2"
```

### Task 10: Add Graph-Only Maintenance Gating

**Files:**
- Create: migration `2026_07_16_000650_create_graph_maintenance_windows_table.php`
- Create: `backend/app/Models/GraphMaintenanceWindow.php`
- Create: `backend/app/Services/Graph/GraphMaintenanceService.php`
- Create: `backend/app/Services/Graph/MaintenanceToken.php`
- Create: `backend/app/Console/Commands/SetCanonicalGraphMaintenance.php`
- Modify: `backend/app/Http/Controllers/Hades/GraphImportController.php`
- Modify: `backend/app/Http/Controllers/Hades/GraphQueryController.php`
- Modify: `backend/app/Http/Controllers/Dashboard/Api/DashboardGraphExplorerController.php`
- Modify: `backend/app/Http/Controllers/Plugin/GraphQueryController.php`
- Modify: `backend/app/Services/Graph/CanonicalGraphRepository.php`
- Modify: `backend/app/Services/Graph/Retention/GraphArtifactReferenceLock.php`
- Modify: `backend/app/Services/Graph/V2/GraphV2ValidationRunService.php`
- Modify: `backend/app/Services/Graph/CanonicalGraphDesiredProjectionService.php`
- Modify: `backend/app/Services/Graph/CanonicalGraphProjectionService.php`
- Modify: `backend/app/Jobs/ValidateGraphV2Import.php`
- Modify: `backend/app/Jobs/ProjectCanonicalGraphV2.php`
- Modify: `backend/app/Console/Commands/ReconcileCanonicalGraphV2.php`
- Modify: `backend/app/Console/Commands/CleanupCanonicalGraphV2.php`
- Modify: `backend/app/Console/Commands/InitializeCanonicalGraphV2Schema.php`
- Modify: `backend/routes/api.php`
- Modify: `backend/routes/web.php`
- Test: `backend/tests/Feature/Graph/GraphMaintenanceTest.php`
- Modify: `backend/tests/Feature/Postgres/GraphV2ConcurrencyPostgresTest.php`

**Interfaces:**

```php
public function begin(?string $projectId, string $reason, int $ttl): MaintenanceToken;
public function end(?string $projectId, string $rawToken, bool $requireNeo4jHealthy): void;
public function assertGraphAvailable(?string $projectId): void;
public function withinReadLease(?string $projectId, Closure $callback): mixed;
public function withinMutationLease(?string $projectId, Closure $callback): mixed;
public function withinCutoverSchemaLease(string $rawToken, Closure $callback): mixed;
```

- [ ] **Step 1: Add RED scope/token/fail-safe tests**

Test one global/one per-project active window, one-time raw token, stored hash only, bounded expiry, wrong token, expired window remains fail-safe, Neo4j health required on off, graph import/query/UI blocked, and non-graph backend/project routes unaffected. With two PostgreSQL connections, hold a mutation lease across a fake long Neo4j operation and prove maintenance activation waits; hold a read lease through a Neo4j query and prove backup activation drains it before stopping Neo4j. After activation, import/chunk/validation finalization, desired-head writes, projection/reconcile/retry, namespace/artifact cleanup, graph-reference writers, and graph reads are rejected or safely deferred before external access. A nested same-project lease is reference-counted/reentrant; a different or broader scope is rejected. Prove project maintenance does not block another project, while global maintenance does. `init-schema` succeeds only with the raw token of an active global window whose reason is exactly `cutover`; it rejects backup/project/expired/wrong tokens. The artifact inventory/snapshot remains inside the active frozen window.

- [ ] **Step 2: Run RED**

Run: `docker compose exec -T app php artisan test tests/Feature/Graph/GraphMaintenanceTest.php`

- [ ] **Step 3: Implement service/command/controller guards, run GREEN, commit**

Use canonical session-lock keys `hades:graph-maintenance:v2:global` and `hades:graph-maintenance:v2:project:{project_id}` on dedicated PostgreSQL connections. A normal project read/mutation lease acquires shared global then shared project with `pg_advisory_lock_shared`, checks global and project rows, holds both through the complete callback including Neo4j/object-store I/O, and unlocks in reverse order in `finally`. Global `begin/end` acquires exclusive global; project `begin/end` acquires shared global then exclusive project. `begin()` waits for earlier holders, writes/commits the active row while locks remain held, then unlocks; later holders acquire and reject from the row. `withinReadLease()` and `withinMutationLease()` share this fencing algorithm but remain distinct methods for call-site auditing. Same-scope nesting reuses the in-process lease; cross-project or scope-widening nesting throws.

`withinCutoverSchemaLease()` acquires exclusive global, verifies the exact active global row, raw token hash, non-expiry, and `reason=cutover`, and holds the lock across `Neo4jGraphV2Schema::ensure/verify`; it is the only maintenance bypass. `hades:graph-v2:init-schema` requires `--maintenance-token` for mutation and may use the ordinary read-only `--verify` outside maintenance. A `backup` window token can never mutate schema.

Inject the service into the four named graph controllers for availability. Every dashboard/plugin/Hades graph query and repository entry point uses `withinReadLease()` around context resolution plus the whole Neo4j read. Wrap all mutation entry points present by Task 10—import create/chunk/complete, validation acquisition/execution/final CAS, desired-head writes, projection jobs/reconcile/operator retry, and cleanup—with the mutation lease. `GraphArtifactReferenceLock::within()` acquires/reuses the project lease before its PostgreSQL transaction, so every later Plan 3 reference writer is covered automatically. Projection and cleanup jobs keep one lease around all short DB phases and intervening external calls. Maintenance returns a retryable/deferred outcome for background jobs rather than consuming a projection/validation attempt. Use route edits only to preserve graph route grouping/error mapping; do not gate shared project middleware or non-graph endpoints.

```bash
docker compose exec -T app php artisan test tests/Feature/Graph/GraphMaintenanceTest.php
git add backend/database/migrations/2026_07_16_000650_create_graph_maintenance_windows_table.php backend/app/Models/GraphMaintenanceWindow.php backend/app/Services/Graph/GraphMaintenanceService.php backend/app/Services/Graph/MaintenanceToken.php backend/app/Console/Commands/SetCanonicalGraphMaintenance.php backend/app/Console/Commands/InitializeCanonicalGraphV2Schema.php backend/app/Http/Controllers/Hades/GraphImportController.php backend/app/Http/Controllers/Hades/GraphQueryController.php backend/app/Http/Controllers/Dashboard/Api/DashboardGraphExplorerController.php backend/app/Http/Controllers/Plugin/GraphQueryController.php backend/app/Services/Graph/CanonicalGraphRepository.php backend/app/Services/Graph/Retention/GraphArtifactReferenceLock.php backend/app/Services/Graph/V2/GraphV2ValidationRunService.php backend/app/Services/Graph/CanonicalGraphDesiredProjectionService.php backend/app/Services/Graph/CanonicalGraphProjectionService.php backend/app/Jobs/ValidateGraphV2Import.php backend/app/Jobs/ProjectCanonicalGraphV2.php backend/app/Console/Commands/ReconcileCanonicalGraphV2.php backend/app/Console/Commands/CleanupCanonicalGraphV2.php backend/routes/api.php backend/routes/web.php backend/tests/Feature/Graph/GraphMaintenanceTest.php backend/tests/Feature/Postgres/GraphV2ConcurrencyPostgresTest.php
git commit -m "feat(graph): gate graph operations during maintenance"
```

### Task 11: Wire Scheduling, Retention, and Artifact Reachability

**Files:**
- Create: `backend/app/Contracts/Graph/GraphArtifactReachabilityProvider.php`
- Create: `backend/app/Services/Graph/Retention/GraphArtifactReachability.php`
- Create: `backend/app/Services/Graph/Retention/ProjectionGraphArtifactReachabilityProvider.php`
- Create: `backend/app/Services/Graph/Retention/ProjectionNamespaceReachability.php`
- Create: `backend/app/Services/Graph/Retention/GraphArtifactCleanupLock.php`
- Create: `backend/app/Services/Graph/Retention/GraphV2NamespaceCleanupService.php`
- Create: `backend/app/Services/Graph/Retention/GraphV2ArtifactCleanupService.php`
- Modify: `backend/app/Services/ArtifactStorageService.php`
- Modify: `backend/app/Console/Commands/CleanupCanonicalGraphV2.php`
- Modify: `backend/app/Providers/AppServiceProvider.php`
- Modify: `backend/routes/console.php`
- Test: `backend/tests/Integration/Graph/GraphV2RetentionTest.php`
- Test: `backend/tests/Feature/Postgres/GraphV2ConcurrencyPostgresTest.php`

**Interfaces:**

```php
interface GraphArtifactReachabilityProvider
{
    public function retains(HadesGraphImport $import): bool;
}

final class GraphArtifactReachability
{
    public const PROVIDER_TAG = 'hades.graph_v2.artifact_reachability';

    /** @param iterable<GraphArtifactReachabilityProvider> $providers */
    public function __construct(iterable $providers) {}

    public function isReachable(HadesGraphImport $import): bool;
}

final class ProjectionGraphArtifactReachabilityProvider implements GraphArtifactReachabilityProvider
{
    public function retains(HadesGraphImport $import): bool;
}

final class GraphArtifactCleanupLock
{
    /**
     * Cleanup-only resume path. It issues no GraphArtifactReferenceGuard.
     * @param list<string> $expectedImportStates
     */
    public function resumeImport(
        string $projectId,
        string $workspaceBindingId,
        string $importId,
        string $operationId,
        array $expectedImportStates,
        Closure $criticalSection,
    ): mixed;

    /** @param list<string> $expectedNamespaceStates */
    public function resumeNamespace(
        string $projectId,
        string $workspaceBindingId,
        string $importId,
        string $projectionId,
        string $operationId,
        array $expectedNamespaceStates,
        Closure $criticalSection,
    ): mixed;
}
```

- Failed/stale imports are eligible after 24h only when unreachable.
- C2 artifact reachability covers only Plan 2 roots: projection rows/heads, desired/active/previous imports, projection attempts, and the fixed context grace.
- Plan 3 registers providers for verification requests/results/overlays and Wiki ledgers; those roots close at C3, not C2.
- Namespace and artifact decisions are separate. `ProjectionNamespaceReachability` retains exactly active/previous, a live execution-leased candidate/attempt, or a within-grace projection incarnation. When a pointer rotation causes a projection to cease being active/previous, update that projection row's `updated_at` in the CAS transaction; this timestamp starts the 40-minute grace (30-minute context/cache TTL plus ten minutes). Search snapshots are opaque TTL cache entries, not normalized references, and are protected by this grace. Artifact reachability may retain immutable blobs/import rows without retaining every old overlay projection incarnation.
- The provider tag is an internal fixed Laravel container tag. Only concrete application providers are registered; there is no runtime discovery or user/plugin registration API.
- `GraphArtifactReachability::isReachable()` throws `graph_artifact_reachability_unconfigured` when zero providers resolve, returns `true` as soon as one configured provider returns `true`, returns `false` only after at least one provider ran and every provider returned `false`, and never catches provider exceptions. Any provider/query failure propagates and no cleanup state advances.

- [ ] **Step 1: Add RED reachability/race tests**

Cover every Plan 2 artifact root and namespace root independently, zero/composed providers, a provider returning reachable, and a provider throwing. Assert each registered provider is resolved/called exactly once, `true` short-circuits, `false` requires at least one provider and all false, and zero providers/exception leave live rows and external data untouched. Prove an import retained only by Wiki-like blob ownership would not retain stale namespaces using a Plan 2 test double, without creating fake Plan 3 tables. Cover exact object ownership, no raw prefix delete, referenced blob retention, scheduler cadence, `withoutOverlapping()` and one-server locks.

Lock a matching equal-semantic import in `tombstoned` and `objects_deleted` states and prove create returns exactly HTTP 409 `{error:{code:"graph_import_cleanup_in_progress",message:<nonempty>,retryable:true,details:{}}}` without changing rows or objects; after the cleanup row is removed, the identical request creates a fresh attempt. Freeze that code/envelope in controller/OpenAPI contract tests and prove an active validated equal import still returns 200.

Test both forward-only durable state machines at every crash point: before tombstone commit leaves live metadata/external data; after tombstone commit resumes idempotently; after external delete but before stage update repeats safely; final PostgreSQL deletion happens only after the external stage. A reference racing before tombstone wins and aborts cleanup; after tombstone every guard/reference writer rejects. A namespace publication racing tombstone likewise either wins first or is rejected. Pause a stale cleaner for incarnation A, publish a same-version incarnation B, resume A, and prove only A is deleted. Exercise fence races in both orders: a projector batch holding A's fence makes cleanup wait and is subsequently removed; cleanup winning first changes/creates A's fence as `deleting`, so a stale batch writes zero. Retire A, delete A's PostgreSQL projection row, then resume an old A token and prove it cannot revive the fence or create any record while B remains the only readable incarnation. Query the invariant that every non-fence v2 record has one matching non-retired fence and every retired fence has zero matching records. The PostgreSQL acceptance suite uses independent connections for its races; Neo4j integration tests use controlled transactions and the 60-second server timeout.

Projection attempts are roots only while queued/projecting with an unexpired execution lease. Terminal `succeeded|failed|stale` attempt rows are audit-retained for exactly 24 hours, then deleted in a bounded batch under import reference lock → head → attempts in ascending `(attempt_generation,id)` order, provided no head/candidate points at them; deletion never changes a projection state. Namespace cleanup cannot tombstone a projection until its terminal attempts are past retention and removed. Test a failed fourth attempt retained for 24 hours, deletion after the boundary, then exact namespace and artifact eligibility.

- [ ] **Step 2: Run RED**

Run: `docker compose exec -T app php artisan test tests/Integration/Graph/GraphV2RetentionTest.php`

Run only on guarded `devboard_acceptance`: `docker compose exec -T app composer test:postgres -- tests/Feature/Postgres/GraphV2ConcurrencyPostgresTest.php`

- [ ] **Step 3: Implement base reachability and locked cleanup**

Register only `ProjectionGraphArtifactReachabilityProvider` under `GraphArtifactReachability::PROVIDER_TAG` in Plan 2 and inject the tagged iterable. Container tests require that provider exactly once; runtime still throws on an empty iterable. Providers are read-only.

Namespace cleanup and artifact cleanup are distinct bounded loops inside one graph maintenance mutation lease:

1. Namespace phase A preselects one stale projection, enters `GraphArtifactReferenceLock`, locks head then projection/attempt rows, reruns `ProjectionNamespaceReachability`, and CASes `namespace_cleanup_state active→tombstoned` with a new operation ULID; commit before external work. Publication/query paths reject a non-active namespace state.
2. The resumable worker first `MERGE`s/locks the exact incarnation's `CanonicalProjectionFence`, creating it directly as `deleting` only when absent, and requires the stored cleanup operation ID. It transitions `building→deleting`, deletes only the exact `(project,scope,binding,projection_version,projection_incarnation_id)` canonical records in fence-guarded bounded transactions, proves zero remain, and transitions `deleting→retired`. The retired fence is permanent and excluded from application counts. Only then does `GraphArtifactCleanupLock::resumeNamespace()` CAS PostgreSQL `tombstoned→neo4j_deleted` and finally delete the projection row when no FK/head/attempt remains. The cleanup-only lock acquires the same maintenance lease, import advisory lock/import row, then head/projection rows; it requires the exact operation ID and expected forward state, never accepts `active`, and never issues a reference guard. A crash resumes the stored stage; it never untombstones or deletes a retired fence.
3. Artifact phase A selects an import only after no projection row/namespace remains, enters `GraphArtifactReferenceLock`, reruns every artifact provider, and CASes `artifact_cleanup_state active→tombstoned` with an operation ULID; commit before object deletion. `GraphArtifactReferenceGuard::assertHolds()`, import replay, desired-state writers, and all reference writers reject non-active cleanup state.
4. The worker deletes only the exact stored verified object list through `ArtifactStorageService`, idempotently accepts absence only for proven owned paths/digests, then uses `GraphArtifactCleanupLock::resumeImport()` to CAS `tombstoned→objects_deleted` and in a later resume transaction delete chunks/validation rows/import last. The exact operation ID/state CAS makes duplicate workers harmless. Provider failure before tombstone rolls back; no provider is called after tombstone because new references are impossible. External failure leaves the durable stage for retry, never live metadata that can accept a reference to missing bytes.

The create-import endpoint returns `409 graph_import_cleanup_in_progress` while an equal semantic import is tombstoned; after final row removal a fresh attempt may be uploaded. No raw prefix delete is allowed.

`AppServiceProvider::register()` must contain the exact Plan 2 provider set:

```php
$this->app->tag(
    [ProjectionGraphArtifactReachabilityProvider::class],
    GraphArtifactReachability::PROVIDER_TAG,
);
$this->app->when(GraphArtifactReachability::class)
    ->needs('$providers')
    ->giveTagged(GraphArtifactReachability::PROVIDER_TAG);
```

- [ ] **Step 4: Run GREEN and commit**

```bash
docker compose exec -T app php artisan test tests/Integration/Graph/GraphV2RetentionTest.php
git add backend/app/Contracts/Graph/GraphArtifactReachabilityProvider.php backend/app/Services/Graph/Retention/GraphArtifactReachability.php backend/app/Services/Graph/Retention/ProjectionGraphArtifactReachabilityProvider.php backend/app/Services/Graph/Retention/ProjectionNamespaceReachability.php backend/app/Services/Graph/Retention/GraphArtifactCleanupLock.php backend/app/Services/Graph/Retention/GraphV2NamespaceCleanupService.php backend/app/Services/Graph/Retention/GraphV2ArtifactCleanupService.php backend/app/Services/ArtifactStorageService.php backend/app/Console/Commands/CleanupCanonicalGraphV2.php backend/app/Providers/AppServiceProvider.php backend/routes/console.php backend/tests/Integration/Graph/GraphV2RetentionTest.php backend/tests/Feature/Postgres/GraphV2ConcurrencyPostgresTest.php
git commit -m "feat(graph): retain reachable graph v2 artifacts"
```

### Task 12: Close Backend Acceptance Gates L01–L15

**Files:**
- Modify only if a mapped node is missing: `backend/tests/Unit/Graph/GraphV2ContractManifestTest.php`, `backend/tests/Unit/Graph/GraphV2QueueRuntimeContractTest.php`, `backend/tests/Feature/Hades/GraphV2MigrationTest.php`, `backend/tests/Feature/Graph/GraphV2CoordinationMigrationTest.php`, `backend/tests/Feature/Hades/GraphImportControllerTest.php`, `backend/tests/Feature/Dashboard/GraphExplorerV2ControllerTest.php`, `backend/tests/Feature/Hades/PluginGraphQueryV2Test.php`, `backend/tests/Feature/Graph/GraphMaintenanceTest.php`, `backend/tests/Integration/Graph/GraphV2ImportValidationTest.php`, `backend/tests/Integration/Graph/GraphV2ValidationExecutionTest.php`, `backend/tests/Integration/Graph/CanonicalGraphV2ProjectionTest.php`, `backend/tests/Integration/Graph/CanonicalGraphV2ProjectionRetryTest.php`, `backend/tests/Integration/Graph/DashboardGraphSearchSnapshotTest.php`, `backend/tests/Integration/Graph/GraphV2RetentionTest.php`, `backend/tests/Feature/Postgres/GraphArtifactReferenceLockPostgresTest.php`, `backend/tests/Feature/Postgres/GraphV2ConcurrencyPostgresTest.php`
- Create: `.codex-artifacts/graph-v2/backend-gates.json`

- [ ] **Step 1: Map every backend gate to one exact test node**

```json
{
  "L01": [
    "backend/tests/Feature/Hades/GraphImportControllerTest.php::import_is_scoped_resumable_digest_checked_and_idempotent",
    "backend/tests/Feature/Hades/GraphImportControllerTest.php::equal_import_in_cleanup_returns_retryable_stable_conflict"
  ],
  "L02": "backend/tests/Integration/Graph/CanonicalGraphV2ProjectionTest.php::projection_binds_exact_project_scope_artifact_overlay_and_entrypoint",
  "L03": "backend/tests/Feature/Dashboard/GraphExplorerV2ControllerTest.php::v2_query_has_no_repository_workspace_or_version_fallback",
  "L04": "backend/tests/Integration/Graph/CanonicalGraphV2ProjectionTest.php::candidate_is_invisible_until_ready_partial_head_cas",
  "L05": "backend/tests/Integration/Graph/CanonicalGraphV2ProjectionRetryTest.php::failure_preserves_old_active_projection",
  "L06": "backend/tests/Feature/Dashboard/GraphExplorerV2ControllerTest.php::zero_requires_absence_verified",
  "L07": [
    "backend/tests/Integration/Graph/GraphV2ValidationExecutionTest.php::duplicate_same_token_delivery_has_one_claim_winner",
    "backend/tests/Integration/Graph/CanonicalGraphV2ProjectionRetryTest.php::duplicate_projectors_are_idempotent_and_do_not_steal_lease"
  ],
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
  tests/Unit/Graph/GraphV2ContractManifestTest.php \
  tests/Unit/Graph/GraphV2QueueRuntimeContractTest.php \
  tests/Feature/Hades/GraphV2MigrationTest.php \
  tests/Feature/Graph/GraphV2CoordinationMigrationTest.php \
  tests/Feature/Hades/GraphImportControllerTest.php \
  tests/Feature/Dashboard/GraphExplorerV2ControllerTest.php \
  tests/Feature/Hades/PluginGraphQueryV2Test.php \
  tests/Feature/Graph/GraphMaintenanceTest.php \
  tests/Integration/Graph/GraphV2ImportValidationTest.php \
  tests/Integration/Graph/GraphV2ValidationExecutionTest.php \
  tests/Integration/Graph/CanonicalGraphV2ProjectionTest.php \
  tests/Integration/Graph/CanonicalGraphV2ProjectionRetryTest.php \
  tests/Integration/Graph/DashboardGraphSearchSnapshotTest.php \
  tests/Integration/Graph/GraphV2RetentionTest.php

docker compose exec -T app composer test:postgres -- \
  tests/Feature/Postgres/GraphArtifactReferenceLockPostgresTest.php \
  tests/Feature/Postgres/GraphV2ConcurrencyPostgresTest.php
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
git add -- .codex-artifacts/graph-v2/backend-gates.json backend/tests/Unit/Graph/GraphV2ContractManifestTest.php backend/tests/Unit/Graph/GraphV2QueueRuntimeContractTest.php backend/tests/Feature/Hades/GraphV2MigrationTest.php backend/tests/Feature/Graph/GraphV2CoordinationMigrationTest.php backend/tests/Feature/Hades/GraphImportControllerTest.php backend/tests/Feature/Dashboard/GraphExplorerV2ControllerTest.php backend/tests/Feature/Hades/PluginGraphQueryV2Test.php backend/tests/Feature/Graph/GraphMaintenanceTest.php backend/tests/Integration/Graph/GraphV2ImportValidationTest.php backend/tests/Integration/Graph/GraphV2ValidationExecutionTest.php backend/tests/Integration/Graph/CanonicalGraphV2ProjectionTest.php backend/tests/Integration/Graph/CanonicalGraphV2ProjectionRetryTest.php backend/tests/Integration/Graph/DashboardGraphSearchSnapshotTest.php backend/tests/Integration/Graph/GraphV2RetentionTest.php backend/tests/Feature/Postgres/GraphArtifactReferenceLockPostgresTest.php backend/tests/Feature/Postgres/GraphV2ConcurrencyPostgresTest.php
git commit -m "test(graph): close backend lifecycle v2 gates"
```

## Plan 2 Exit Gate

- L01–L15 are green.
- Backend vendored contract bytes equal Plan 1.
- Four total validation runs and four total projection attempts are proven independently.
- A successful publication emits exactly one post-commit `CanonicalGraphV2ProjectionActivated`; failed, stale/no-op, already-active, and rolled-back paths emit none.
- The fixed Plan 2 reachability provider set covers projection/head/attempt/context roots, and the locked final recheck defeats a concurrent new reference.
- `scopes` is the only graph query without context/projection; all other successful responses carry the exact active context.
- No active v2 repository/query path falls back to repository/workspace/v1 data.
- Old active projection survives every injected candidate failure.
- Fresh spec-compliance review contains no unresolved Critical/Important finding.
