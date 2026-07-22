# Hades Graph and Wiki Verification Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn graph uncertainty and Wiki `needs_verification` revisions into project-scoped leased work that Hades Agent resolves one item at a time with structured, auditable, version-safe results.

**Architecture:** PostgreSQL verification requests own target/version/dedupe state; generic work items supply the existing lease transport; domain completion applies graph overlays or immutable Wiki revisions in the same transaction as terminal work completion. Local sync caches counts only. Explicit CLI commands and skills claim one current item, gather read-only evidence, validate the result schema, heartbeat, and complete atomically.

**Tech Stack:** Laravel/PHP/Pest, PostgreSQL JSONB/ULIDs/row locks, Python 3.11+/pytest/SQLite, Hades CLI and skills, JCS/SHA-256, existing Wiki revision model.

## Global Constraints

- Inherit `2026-07-16-graph-lifecycle-v2-master.md`.
- Backend execution uses `/home/ubuntu/dev-sandbox`; Agent execution uses `/Users/gabriele/Dev/Hephaistos`. Each task or mandatory slice starts on a fresh checkpoint-recorded `codex/graph-v2-p3-<backend|agent>-<task-or-slice>` branch from then-current clean, pulled `main`, after the accepted predecessor was reviewed, integrated, tested, and pushed. Never reuse the historical Plan 1, Task 5, or catch-all verification branches. Backend and Agent commits remain separate.
- START GATE: Plan 2 contracts, graph import, projection head, post-CAS activation event, base reachability aggregator, signed context, and query DTOs are green.
- Commit backend and agent changes separately even when one task describes an end-to-end interface.
- Generic Kanban/task workers exclude verification twice: server filter and local discriminator check.
- Graph base artifacts are immutable. Verified/contradicted graph facts create append-only version-bound overlays.
- Wiki verification never edits claim text. It creates a new immutable revision and recomputes fingerprints with the new revision ULID.
- `deferred` is completed and quiet for that target/source version.
- Free prose cannot complete a verification item.
- Automated claim/complete always requires agent/device capability plus project and binding checks; an admin user token does not erase those automated checks.
- Graph reconciliation starts from Plan 2's `CanonicalGraphV2ProjectionActivated` event. Its listener always rereads the current projection head and treats event fields as routing metadata, never as current-state authority.
- Verification/Wiki artifact references extend Plan 2 retention only through concrete providers registered under `GraphArtifactReachability::PROVIDER_TAG`; providers are read-only and no public/plugin registry is introduced.
- Before creating any new reference to a graph import, a Plan 3 writer with a nonempty import set enters Plan 2's `GraphArtifactReferenceLock` as its outermost transaction. A valid Wiki `source_unavailable` path has no graph-import reference and instead opens one ordinary transaction; it must not construct an empty `GraphArtifactReferenceGuard`. The total order for a nonempty set is always all sorted import advisory locks, all matching import rows `FOR UPDATE`, `VerificationScopeLock`, then projection head, work items, verification requests, overlays, Wiki page, and Wiki revision rows; the empty-set path begins at `VerificationScopeLock` and follows the same domain order. `VerificationScopeLock` executes `SELECT pg_advisory_xact_lock(hashtextextended(?, 0))` with canonical key `hades:verification:v2:{project_id}:{workspace_binding_id}` and requires an open transaction. Chain rows lock in ascending `(attempt_generation,id)` order; deletion later runs in descending generation. A writer that already holds any domain lock must not enter the reference lock. Domain services receive the invocation's `GraphArtifactReferenceGuard` and call `assertHolds()` before inserting a reference. After domain locks are acquired, recompute the complete import set; a difference aborts/retries from outside rather than taking an incremental lock.
- After every focused backend GREEN, run the fixed regression set `backend/tests/Feature/Plugin/AgentWorkItemContractTest.php`, `backend/tests/Feature/Hades/HadesWikiWorkflowTest.php`, `backend/tests/Feature/WikiRevisionTest.php`, and `backend/tests/Integration/Graph/CanonicalGraphV2ProjectionTest.php`. After every focused Agent GREEN, run `tests/hermes_cli/test_hades_plugin_work_items_client.py`, `tests/hermes_cli/test_hades_plugin_worker.py`, `tests/hermes_cli/test_hades_backend_client.py`, and `tests/hermes_cli/test_hades_backend_cmd.py`. A task is not review-ready until its applicable regression set, `git diff --check`, and exact task-file diff pass.

---

## File Structure

### Backend

- Migrations/models: work-item extension, verification requests, overlays, immutable Wiki claim ledgers, and normalized Wiki-to-graph references.
- Services: reconciliation, completion, overlay derivation, Wiki application, whole-chain audit retention, and concrete read-only graph-artifact reachability providers.
- List/claim/heartbeat/complete/retry API: existing `AgentWorkItemController` plus domain services.
- Listeners/jobs: the Plan 2 projection-activated event and Wiki revision CAS enqueue coalesced reconciliation.

### Hades Agent

- `hades_verification_contract.py`: schema validation/canonical digest.
- `hades_verification_tasks.py`: list/show/status/cache.
- `hades_verification_worker.py`: one-item claim/heartbeat/specialist/complete.
- Existing client/DB/status/commands/runtime: typed verification DTOs and non-regressing cache.
- Skills: orchestrator, graph specialist, updated Wiki specialist/push.

---

### Task 1: Add Verification Storage and State-Versioned Work Items

**Backend files:**
- Create: migration `2026_07_16_000400_extend_agent_work_items_for_verification.php`
- Create: migration `2026_07_16_000500_create_verification_requests_table.php`
- Create: migration `2026_07_16_000525_extend_wiki_revisions_for_verification.php`
- Create: migration `2026_07_16_000550_create_wiki_verification_claims_table.php`
- Create: migration `2026_07_16_000575_create_wiki_graph_artifact_references_table.php`
- Create: migration `2026_07_16_000600_create_graph_verification_overlays_table.php`
- Create: migration `2026_07_16_000625_create_projection_verification_overlay_links_table.php`
- Create: `backend/app/Models/VerificationRequest.php`
- Create: `backend/app/Models/GraphVerificationOverlay.php`
- Create: `backend/app/Models/WikiVerificationClaim.php`
- Create: `backend/app/Models/WikiGraphArtifactReference.php`
- Create: `backend/app/Models/AgentWorkItem.php`
- Create: `backend/app/Services/Hades/AgentWorkItemStateService.php`
- Create: `backend/app/Services/Hades/VerificationExecutionEpochService.php`
- Modify: `backend/app/Http/Controllers/Plugin/AgentWorkItemController.php`
- Modify: `backend/app/Http/Controllers/Dashboard/Api/DashboardAgentChatController.php`
- Modify: `backend/app/Http/Controllers/Dashboard/Api/DashboardAgentWorkController.php`
- Modify: `backend/app/Services/Hades/HadesKanbanTaskIntakeService.php`
- Modify: `backend/app/Services/ServerAgentWorkService.php`
- Test: `backend/tests/Feature/Hades/VerificationMigrationTest.php`
- Test: `backend/tests/Feature/Hades/AgentWorkItemStateServiceTest.php`
- Test: `backend/tests/Feature/Postgres/VerificationConcurrencyPostgresTest.php`

**Mandatory execution slices (never combine in one agent session):**

1. **1A — schema/models/portable upgrade (max 150 min):** seven forward migrations, models, exact constraints/indexes/FKs, same-version overlay provenance, and only `VerificationMigrationTest.php`. Commit `feat(verification): add versioned verification storage` only after schema review.
2. **1B — shared work-item transition service (max 120 min):** `AgentWorkItemStateService`, migrate the five named legacy writers, priority/status/version/attempt behavior, and only `AgentWorkItemStateServiceTest.php` plus the five named writers' existing focused tests. Commit `refactor(work): centralize versioned work transitions`.
3. **1C — real PostgreSQL concurrency acceptance (max 120 min):** tests-only two-physical-connection acceptance for partial/NULLS-NOT-DISTINCT constraints and claim/retry/reference races in `VerificationConcurrencyPostgresTest.php`. If it exposes a production defect, stop and add a separately reviewed bounded **1B-hardening** amendment naming the exact production path; 1C never edits production code under a tests-only commit. Commit `test(verification): prove postgres concurrency contracts`.

Each slice starts from clean pulled `main` after the accepted previous slice was independently reviewed, integrated, tested, and pushed. It stops at its time guard with a checkpoint rather than expanding scope. Task 2 is blocked until 1A–1C and their main integrations are green.

**Interfaces:**
- Columns, indexes, statuses, FKs, retry links, and retention restrictions are exactly section 9.1. `verification_requests.source_graph_import_id`, `graph_verification_overlays.graph_import_id`, and `wiki_graph_artifact_references.graph_import_id` are explicit `RESTRICT/NO ACTION` references to `hades_graph_imports`; reachability never parses JSON to discover ownership. Explicit referencing-side indexes are mandatory on `agent_work_items.retry_of_work_item_id`, `verification_requests.retry_of_request_id`, `wiki_revisions.workspace_binding_id`, `wiki_revisions.verification_source_revision_id`, each of `wiki_graph_artifact_references.verification_request_id|project_id|workspace_binding_id`, a standalone index beginning with `wiki_graph_artifact_references.graph_import_id`, `(project_id,workspace_binding_id,graph_import_id)`, and the overlay-ID side of both membership tables; PostgreSQL does not create them for FKs.
- `wiki_revisions` gains nullable `workspace_binding_id`, `content_sha256`, `verification_source_revision_id`, `result_digest`, and `generator_metadata`. `wiki_verification_claims` stores the immutable ordinal/byte range/text/fingerprint plus nullable translated verdict/evidence indexes/reason. `wiki_graph_artifact_references` stores exact `source_snapshot|evidence` references, nullable verification-request/evidence-index detail, and is the only table queried by the Wiki reachability provider.
- Every committed status/claim/lease/result/terminal mutation increments `state_version` once. Verification rows also have `execution_attempts` default 0. Each successful claim increments it; at 10 attempts the next lease-expiry/reclaim or claim guard atomically terminalizes that generation as `failed` with code `verification_execution_attempts_exhausted` instead of requeueing. Human retry creates a new generation/work item at 0 and is a separate policy.
- Migration `000400` also adds the exact specialist-fence fields and nullable verification execution-epoch generation from design section 9.1, their all-null/all-required checks and partial unique fence-ID index, plus the singleton `hades_verification_execution_epochs` row. The fence generation is monotonic and independent of `execution_attempts`; existing/general rows backfill null fence/epoch state and generation 0. Every verification execution/fence CAS uses `VerificationExecutionEpochService` and the row's copied current generation.
- Verification-request priority stays `low|normal|high|critical`; the existing transport stores `critical` as work-item priority `urgent`. API filters translate `critical↔urgent` only for `work_kind=verification`; payload/request DTOs never expose `urgent`, and generic work keeps its existing `low|normal|high|urgent` contract.
- Migration `000625` creates `canonical_graph_desired_verification_overlays` and `canonical_graph_projection_verification_overlays`. The first stores durable exact membership before a candidate exists; the second stores immutable membership for each candidate/effective projection. Each row carries `project_id`, `workspace_binding_id`, `base_graph_import_id`, `overlay_source_graph_import_id`, and overlay ID. Add parent unique `(project_id,source_scope_id,id)` on v2 heads/projections. Desired FK `(project_id,workspace_binding_id,projection_head_id)` references heads `(project_id,source_scope_id,id)`; projection FK `(project_id,workspace_binding_id,canonical_graph_projection_id)` references projections `(project_id,source_scope_id,id)`. Existing v2 checks require `source_scope_type='workspace_binding'`, so the child binding equals parent `source_scope_id` without adding a duplicate parent column. Both import columns have separate scoped composite FKs to parent unique `(project_id,workspace_binding_id,id)` on imports; overlay FK `(project_id,workspace_binding_id,overlay_source_graph_import_id,graph_verification_overlay_id)` references overlay `(project_id,workspace_binding_id,graph_import_id,id)`. Head/projection FKs cascade; overlay/import FKs restrict. Primary keys are `(projection_head_id,graph_verification_overlay_id)` and `(canonical_graph_projection_id,graph_verification_overlay_id)`; both have indexes beginning with overlay ID and with each import FK. Cross-project/binding is impossible. Base import may differ from overlay source import only when their immutable `artifact_graph_version` values are exactly equal; the service checks this under locks, allowing evidence reuse across byte-identical reimports while retaining both provenance roots. Service validation recomputes the sorted overlay set hash and requires exact equality with head/projection `verification_set_hash`. Verification-chain retention checks both tables, not artifact-level reachability.

```php
final class AgentWorkItemStateService
{
    /**
     * @param list<string> $expectedStatuses
     * @param array<string, mixed> $changes
     */
    public function transition(
        string $workItemId,
        int $expectedStateVersion,
        array $expectedStatuses,
        array $changes,
        string $auditEvent,
    ): AgentWorkItem;
}
```

- [ ] **Step 1: Add RED migration/state tests**

```php
it('increments state version exactly once for a committed transition', function () {
    $item = createVerificationWorkItem(['status' => 'queued', 'state_version' => 1]);
    $service = app(AgentWorkItemStateService::class);
    $claimed = $service->transition($item->id, 1, ['queued'], [
        'status' => 'claimed',
        'claimed_by_device_id' => registeredVerificationDevice()->id,
        'lease_expires_at' => now()->addMinute(),
    ], 'verification.claimed');
    expect($claimed->state_version)->toBe(2);
    expect($claimed->fresh()->state_version)->toBe(2);
});
```

Also test status union, partial dedupe uniqueness, request generation uniqueness, overlay append-only uniqueness plus desired-head/projection membership links, cross-tenant/import rejection and set-hash agreement, claim ordinal/fingerprint uniqueness, graph-reference scope/discriminator constraints, all graph-import `RESTRICT/NO ACTION` FKs, every named referencing-side index, cleanup order, and generic historical statuses unchanged. Test attempts 1–10; an idempotent repeated claim on the same live lease changes neither attempts nor version; a heartbeat that actually extends the lease increments `state_version` exactly once but never `execution_attempts`; an identical no-op heartbeat changes neither. Test terminalization instead of an 11th lease, human retry reset, and exact request-critical/work-urgent mapping plus deterministic ordering/filter behavior. Apply the new Wiki migration to the existing June schema; never edit `2026_06_16_000000_create_devboard_core_tables.php`.

The SQLite suite is only the portable contract. `VerificationConcurrencyPostgresTest` must use real `NULLS NOT DISTINCT`, partial indexes, FKs, advisory/row locks, and two independent connections for claim/retry/reference/retention races. `AgentWorkItemStateServiceTest` owns transition inventory/version/attempt behavior and has no PostgreSQL-only expectation.

- [ ] **Step 2: Run only the current slice's RED command**

```bash
# 1A
docker compose exec -T app php artisan test tests/Feature/Hades/VerificationMigrationTest.php
# 1B, only after integrated 1A
docker compose exec -T app php artisan test tests/Feature/Hades/AgentWorkItemStateServiceTest.php
# 1C, only after integrated 1B and only on guarded disposable devboard_acceptance
docker compose exec -T app composer test:postgres -- tests/Feature/Postgres/VerificationConcurrencyPostgresTest.php
```

- [ ] **Step 3: Implement migrations/models and transition helper**

All five existing writers listed above plus the new verification services use `AgentWorkItemStateService`. It locks one row, compares expected status/version, applies only the caller-supplied validated fields, increments once, and records the audit event in the same transaction. Existing create paths explicitly set `state_version=1`. Do not leave a raw `agent_work_items` status/claim/lease/result/terminal update in a sibling path; the RED test inventories these exact writer files.

- [ ] **Step 4: Run GREEN and commit backend**

```bash
# 1A
docker compose exec -T app php artisan test tests/Feature/Hades/VerificationMigrationTest.php
git add -- backend/database/migrations/2026_07_16_000400_extend_agent_work_items_for_verification.php backend/database/migrations/2026_07_16_000500_create_verification_requests_table.php backend/database/migrations/2026_07_16_000525_extend_wiki_revisions_for_verification.php backend/database/migrations/2026_07_16_000550_create_wiki_verification_claims_table.php backend/database/migrations/2026_07_16_000575_create_wiki_graph_artifact_references_table.php backend/database/migrations/2026_07_16_000600_create_graph_verification_overlays_table.php backend/database/migrations/2026_07_16_000625_create_projection_verification_overlay_links_table.php backend/app/Models/VerificationRequest.php backend/app/Models/GraphVerificationOverlay.php backend/app/Models/WikiVerificationClaim.php backend/app/Models/WikiGraphArtifactReference.php backend/app/Models/AgentWorkItem.php backend/tests/Feature/Hades/VerificationMigrationTest.php
git commit -m "feat(verification): add versioned verification storage"

# 1B, in the next session after 1A review
docker compose exec -T app php artisan test tests/Feature/Hades/AgentWorkItemStateServiceTest.php
git add -- backend/app/Services/Hades/AgentWorkItemStateService.php backend/app/Services/Hades/VerificationExecutionEpochService.php backend/app/Http/Controllers/Plugin/AgentWorkItemController.php backend/app/Http/Controllers/Dashboard/Api/DashboardAgentChatController.php backend/app/Http/Controllers/Dashboard/Api/DashboardAgentWorkController.php backend/app/Services/Hades/HadesKanbanTaskIntakeService.php backend/app/Services/ServerAgentWorkService.php backend/tests/Feature/Hades/AgentWorkItemStateServiceTest.php
git commit -m "refactor(work): centralize versioned work transitions"

# 1C, in the next session after 1B review
docker compose exec -T app composer test:postgres -- tests/Feature/Postgres/VerificationConcurrencyPostgresTest.php
git add -- backend/tests/Feature/Postgres/VerificationConcurrencyPostgresTest.php
git commit -m "test(verification): prove postgres concurrency contracts"
```

### Task 2: Reconcile Current Graph and Wiki Uncertainty into Work

**Backend files:**
- Create: `backend/app/Services/Hades/VerificationQueueService.php`
- Create: `backend/app/Services/Hades/VerificationScope.php`
- Create: `backend/app/Services/Hades/VerificationScopeLock.php`
- Create: `backend/app/Services/Hades/VerificationSummary.php`
- Create: `backend/app/Events/WikiCurrentRevisionActivated.php`
- Create: `backend/app/Jobs/ReconcileVerificationQueue.php`
- Create: `backend/app/Listeners/QueueGraphVerificationRequests.php`
- Create: `backend/app/Listeners/QueueWikiVerificationRequest.php`
- Create: `backend/app/Services/Graph/Retention/VerificationGraphArtifactReachabilityProvider.php`
- Create: `backend/app/Console/Commands/ReconcileVerificationQueue.php`
- Create: `backend/app/Http/Controllers/Hades/VerificationSummaryController.php`
- Modify: `backend/app/Services/Graph/V2/GraphV2ArtifactReader.php`
- Modify: `backend/app/Services/WikiRevisionService.php`
- Modify: `backend/app/Providers/AppServiceProvider.php`
- Modify: `backend/routes/api.php`
- Modify: `backend/routes/console.php`
- Test: `backend/tests/Integration/Hades/VerificationQueueReconciliationTest.php`

**Interfaces:**

```php
final class VerificationQueueService
{
    public function reconcile(VerificationScope $scope): VerificationSummary;
    public function summary(Project $project, ?HadesWorkspaceBinding $binding): VerificationSummary;
}

final readonly class VerificationScope
{
    public function __construct(
        public Project $project,
        public HadesWorkspaceBinding $workspaceBinding,
        public string $domain,
    ) {}
}

final class VerificationScopeLock
{
    public function acquire(string $projectId, string $workspaceBindingId): void;
}

final readonly class VerificationSummary
{
    public function __construct(
        public int $queued,
        public int $highPriority,
        public int $graphQueued,
        public int $wikiQueued,
    ) {}

    /** @return array{verification_queued:int,verification_high_priority:int,verification_by_domain:array{graph:int,wiki:int}} */
    public function toArray(): array;
}

final class QueueGraphVerificationRequests
{
    public function handle(CanonicalGraphV2ProjectionActivated $event): void;
}

final readonly class WikiCurrentRevisionActivated
{
    public function __construct(
        public string $projectId,
        public string $wikiPageId,
        public string $publishedRevisionId,
    ) {}
}

final class QueueWikiVerificationRequest
{
    public function handle(WikiCurrentRevisionActivated $event): void;
}

final class ReconcileVerificationQueue implements ShouldQueue, ShouldBeUniqueUntilProcessing
{
    public int $tries = 3;
    public int $timeout = 60;
    public int $uniqueFor = 240;

    public function __construct(
        public string $projectId,
        public string $workspaceBindingId,
        public string $domain,
        public string $phase = 'upsert', // upsert|stale
        public ?string $targetType = null,
        public ?string $targetId = null,
    ) {}

    public function backoff(): array { return [10, 30]; }
    public function uniqueId(): string;
}

final class VerificationGraphArtifactReachabilityProvider implements GraphArtifactReachabilityProvider
{
    public function retains(HadesGraphImport $import): bool;
}
```

- [ ] **Step 1: Add RED reconciliation tests**

Test one item per exact graph assertion/artifact and one item per Wiki page/revision/source fingerprint (never per claim); same target idempotency; new version stales old live item and creates generation 1; exact A→B→A reactivation creates one linked next generation; deferred does not requeue; no ledger creates diagnostic only; 81–512 Wiki claims creates one `page_requires_split` audit diagnostic and no work item; project/binding/domain filters; concurrent listener/command coalesces under unique key. A current `needs_verification` revision queues once. A current `verified_from_code|conflict_with_code` revision queues again only when its normalized certification source import differs from the locked current source; matching source is quiet. If active and desired imports differ, do not start a new verification against either and let projection activation retry. Derived `verification_freshness` is exactly `current|stale|unavailable` and a stale certification remains visible to the user. Instrument `VerificationScopeLock` and prove it rejects use outside a transaction and executes the exact canonical advisory key before head/domain-row locks.

For every available-source path assert import advisory/import rows, scope lock, projection head `FOR UPDATE`, then work/request/page/revision rows in the global order. For source-unavailable Wiki assert scope lock then the same projection head lock before chain/page locks, with no import guard. Race publication of import B against reconciliation for A: either A commits while holding the head first or it observes B and aborts/retries without creating stale work. Dispatch current, duplicate, and superseded `CanonicalGraphV2ProjectionActivated` events and prove only an event whose locked/reread head is still current queues graph reconciliation; every current activation also dispatches Wiki-freshness reconciliation. Dispatch current, duplicate, superseded, `verified_from_code`, and `conflict_with_code` Wiki events and prove the listener rereads the actual current revision/head: current `needs_verification` queues, matching-source certification is quiet, and a graph change that occurs before delayed listener execution queues the new source rather than trusting the event snapshot. Prove projection and Wiki events in either order converge; active!=desired queues no Wiki work, both-null yields unavailable freshness, and the five-minute sweep recovers either lost event. Prove `WikiRevisionService` emits the Wiki event once after a successful current-revision CAS commit and never for a failed CAS or rollback.

Test the job's scalar-only serialization, exact unique ID `verification-reconcile:{project}:{binding}:{domain}`, 240-second `ShouldBeUniqueUntilProcessing` lock, three tries/backoff `[10,30]`, hard timeout 60 seconds (strictly below the default database queue's 90-second `retry_after`), duplicate coalescing across listener/sweep/phase/target wake-ups, and worker crash followed by unique-lock expiry and the five-minute sweep redispatch. `phase`, `targetType`, and `targetId` remain scalar payload hints but never enter identity. One execution commits at most 250 upserts or 250 stale transitions, dispatches the next same-scope phase under the same key only after commit, and never holds scope/head locks while loading a whole artifact. Because `ShouldBeUniqueUntilProcessing` releases before `handle`, the continuation can be queued; if dispatch is lost or coalesced, the sweep derives the missing phase again from durable state. A >1,000-uncertainty fixture and a kill between pages prove bounded memory/transaction duration, exact-once rows, and resumability. Delete disposable validation staging rows first and prove all uncertainty requests are still recovered from Plan 2's durable index. A command with no binding enumerates authorized `HadesWorkspaceBinding` IDs ordered by `(project_id,id)` and dispatches one isolated concrete-binding/domain job each; it never creates a null lock key or one transaction spanning bindings.

Test `GET /api/hades/v1/graph/verification-summary` with exact project/binding authorization and read-only behavior. Optional `projection_version`, when supplied, must equal the current active version for graph counts but never suppress current Wiki counts. A binding with no ready graph may still return source-unavailable Wiki counts. The response has only the four bounded summary fields. `verification_high_priority` counts live queued verification requests whose request priority is `high|critical` (transport `high|urgent`); it excludes low/normal, terminal rows, and non-verification urgent work. Prove verification request/result and overlay rows retain their graph import through `GraphArtifactReachability`, while another project, binding, or import does not. Race cleanup against request creation and require the full lock order before the reference insert. Resolve the Laravel tag and assert each concrete provider appears exactly once.

- [ ] **Step 2: Run RED**

Run: `docker compose exec -T app php artisan test tests/Integration/Hades/VerificationQueueReconciliationTest.php`

- [ ] **Step 3: Implement locked chain reconciliation**

Compute dedupe/fingerprints using the root contract and create work payloads only from server-owned artifact/ledger rows. `VerificationScope` is always one concrete project/binding and `domain=graph|wiki`; the command expands optional all-project/all-binding/all-domain filters outside transactions into deterministic scalar jobs. Jobs never serialize Eloquent models and each handle performs one fixed page of at most 250 changes.

Graph upsert paging is exact and index-driven: pre-read the current head/import; select at most 250 Plan 2 durable `hades_graph_import_uncertainty_index` rows ordered by public ID for which no exact current-generation request exists. Fetch only those full records through `GraphV2ArtifactReader::recordsByPublicIds($import,'uncertainties',$ids)`; it reads indexed chunk locations in ascending chunk/index order, streams at most one 1,000-record batch in memory, and cross-checks every `(import,kind,public_id,record_ordinal,chunk_index)` against the durable index. Metadata-only rows are never used to invent question/evidence payloads. Then enter the lock transaction below and discard the page if the head/import changed. Wiki upsert pages select at most 250 current revisions ordered by ID, including stale verified/conflicted certifications as defined above. Stale phase selects at most 250 live mismatched request/work chains per transaction. When a phase page is full, dispatch the same phase after commit; when empty, advance `upsert→stale→done`. A lost continuation is recovered because the next sweep selects the next still-missing/still-live rows rather than relying on an in-memory cursor.

For an available source snapshot, first read only the candidate import ID and target identity without locks, enter `GraphArtifactReferenceLock`, acquire `VerificationScopeLock`, call Plan 2 `CanonicalGraphDesiredProjectionService::lockHeadWithinCurrentTransaction()`, then lock work items, verification requests, overlays if applicable, and finally Wiki page/revision. For an unavailable Wiki source, open an ordinary transaction, acquire scope then head, and follow the same domain order without constructing a guard. Use the pre-read identity to find/lock the chain; only after chain locks, lock/reread Wiki page/revision. Recompute head/source/target under those locks. If the import set or target differs, roll back/no-op and let the sweep retry; never add an incremental import lock.

Diagnostics use existing `AuditLogger`, not an unspecified table. Under the same locked page/revision (or concrete scope) transaction, check `(action,target_type,target_id)` and append at most one of `verification.wiki_workspace_binding_missing`, `verification.wiki_no_verifiable_claims`, or `verification.wiki_page_requires_split`, with target type `wiki_revision`. Payload is exactly `{project_id,wiki_page_id,wiki_revision_id,workspace_binding_id,claim_count,source_snapshot_state}`. The page/revision lock serializes check+record; no diagnostic creates a completed/deferred work row.

`QueueGraphVerificationRequests` consumes `CanonicalGraphV2ProjectionActivated`, rereads the current head/projection, and returns without graph dispatch when the head ID, active projection ID, desired generation, or projection version differs from the event. A current activation dispatches both graph reconciliation and the same Wiki-freshness reconciliation used by the periodic sweep. `QueueWikiVerificationRequest` consumes `WikiCurrentRevisionActivated` but treats the event only as a wake-up: it rereads the actual page/current revision and projection head. Current `needs_verification` dispatches; `verified_from_code|conflict_with_code` dispatches only when its certification source differs from locked active=desired; matching certification is quiet. If the event revision was superseded, the listener evaluates that superseding current revision rather than returning early, so a graph change before listener execution cannot suppress required work. Active!=desired dispatches no Wiki work and both-null records unavailable freshness. Matching duplicate deliveries coalesce to the same unique reconciliation key. `WikiRevisionService` captures the page/revision selected by its successful current-revision CAS and registers one `DB::afterCommit()` callback for `WikiCurrentRevisionActivated`; a failed CAS or rollback emits nothing.

Register `VerificationGraphArtifactReachabilityProvider` under the fixed Plan 2 provider tag; it retains the explicit `source_graph_import_id` on verification requests and `graph_import_id` on overlays. Schedule reconciliation every five minutes with `withoutOverlapping()`/one server.

`VerificationSummaryController` validates query keys exactly `project_id`, `workspace_binding_id`, optional `projection_version`; resolves the authenticated Hades agent and linked binding; requires at least one verification capability; returns graph counts only when `verify_project_graph` is effective and Wiki counts only when `verify_project_wiki` is effective, using zero for a domain not granted. It never lists/claims work or changes queue state.

The event and Task 2 provider registrations are exact:

```php
// AppServiceProvider::boot(); Laravel 13 has no EventServiceProvider here.
Event::listen(CanonicalGraphV2ProjectionActivated::class, QueueGraphVerificationRequests::class);
Event::listen(WikiCurrentRevisionActivated::class, QueueWikiVerificationRequest::class);

// AppServiceProvider::register(); REPLACE the sole Plan 2 tag call with this block.
$this->app->tag(
    [
        ProjectionGraphArtifactReachabilityProvider::class,
        VerificationGraphArtifactReachabilityProvider::class,
    ],
    GraphArtifactReachability::PROVIDER_TAG,
);
$this->app->when(GraphArtifactReachability::class)
    ->needs('$providers')
    ->giveTagged(GraphArtifactReachability::PROVIDER_TAG);
```

- [ ] **Step 4: Run GREEN and commit backend**

```bash
docker compose exec -T app php artisan test tests/Integration/Hades/VerificationQueueReconciliationTest.php
git add backend/app/Services/Hades/VerificationQueueService.php backend/app/Services/Hades/VerificationScope.php backend/app/Services/Hades/VerificationScopeLock.php backend/app/Services/Hades/VerificationSummary.php backend/app/Events/WikiCurrentRevisionActivated.php backend/app/Jobs/ReconcileVerificationQueue.php backend/app/Listeners/QueueGraphVerificationRequests.php backend/app/Listeners/QueueWikiVerificationRequest.php backend/app/Services/Graph/V2/GraphV2ArtifactReader.php backend/app/Services/Graph/Retention/VerificationGraphArtifactReachabilityProvider.php backend/app/Console/Commands/ReconcileVerificationQueue.php backend/app/Http/Controllers/Hades/VerificationSummaryController.php backend/app/Services/WikiRevisionService.php backend/app/Providers/AppServiceProvider.php backend/routes/api.php backend/routes/console.php backend/tests/Integration/Hades/VerificationQueueReconciliationTest.php
git commit -m "feat(verification): reconcile graph and wiki uncertainty"
```

### Task 3: Extend Work APIs with Verification Filters and Lease Rules

**Backend files:**
- Modify: `backend/app/Http/Controllers/Plugin/AgentWorkItemController.php`
- Modify: `backend/app/Services/Hades/HadesCapabilityPolicy.php`
- Modify: `backend/routes/api.php`
- Modify: `backend/tests/Feature/Hades/HadesM1ApiTest.php`
- Test: `backend/tests/Feature/Hades/VerificationWorkItemApiTest.php`

**Interfaces:**
- Add `verify_project_graph` to `HadesCapabilityPolicy::SUPPORTED_M1_CAPABILITIES`, but not to `LEGACY_NULL_GRANT_CAPABILITIES`. Registration must explicitly declare it. Preserve `verify_project_wiki`.
- List/claim accepts `work_kind`, project/repository, binding, request priority, status, cursor, limit. For verification only, request `critical` maps to stored work priority `urgent`; generic urgent work remains unchanged.
- DTO includes request priority (`critical`, never `urgent` for verification), `execution_attempts`, `state_version`, and `remote_updated_at`.
- Read-only show is `GET /api/plugin/v1/agent-work-items/{item}`. It returns the same closed item DTO, only after token/device/project/binding/work-kind/capability authorization; foreign or non-visible items are 404. It never claims or extends a lease.
- Claim/heartbeat validates work kind, agent/device, project membership, binding, capability, and current target.
- Heartbeat every 30s; before evidence operation when remaining lease <15s.
- Add authenticated endpoints `POST .../{item}/specialist-fence/start`, `POST .../{item}/specialist-fence/quarantine`, and `POST .../{item}/specialist-fence/clear`. Start requires the current claim/lease owner plus `containment_kind=linux_cgroup_v2|windows_job|darwin_container` and a 64-hex opaque containment-ID hash; it increments fence generation, creates a server ULID, returns one random raw fence token once, and stores only its hash. Quarantine and proof-backed clear require exact item/fence/generation/raw token/device; human clear additionally requires a separate unbound Admin token with non-default `verification.quarantine.clear` and an audit reason/evidence descriptor. Hades device credentials never receive that scope.

- [ ] **Step 1: Add RED authorization/lease tests**

Cover supported/normalized capability ordering, no legacy implicit graph grant, cross-project, cross-binding, wrong device, missing `verify_project_graph`/`verify_project_wiki`, wrong kind, stale target, state-version CAS, lease expiry event and reclaimed queued state, heartbeat rejection, idempotent claim, and generic worker request unable to receive verification items. Cover read-only show success plus foreign/capability/terminal visibility without lease mutation. Cover deterministic `critical→urgent` storage and `urgent→critical` verification DTO/filter mapping without changing general work. Claim attempts 1–10 increment once; expiry/reclaim at 10 atomically fails with `verification_execution_attempts_exhausted`; no 11th lease is issued; a later human-retry generation begins at 0. Test start-before-child contract fields, one-time token/hash, wrong owner/kind/hash/generation, quarantine idempotency, proof-backed clear, Admin-only manual clear, and cross-host claim/reclaim/expiry rejection while fence state is `active|quarantined`; those rejected attempts change neither attempt count nor lease. A parent crash after fence start remains globally blocked.

- [ ] **Step 2: Run RED**

Run: `docker compose exec -T app php artisan test tests/Feature/Hades/VerificationWorkItemApiTest.php`

- [ ] **Step 3: Implement filtered DTO/API behavior**

Extend the existing controller in place; this backend has no separate work-item request/resource/service layer. Do not create parallel DTO controllers or stage an HTTP/service directory. Do not add a persisted `expired` status. Before claim, authorize the domain capability from the server-owned verification request, not a client payload. A successful verification claim increments `execution_attempts` in the same state transition that assigns owner/lease. Reclaim below 10 clears owner/lease, increments version, records `lease_expired`, and keeps attempt generation only when no specialist fence exists. `active|quarantined` globally blocks claim/reclaim/terminalization until exact proof-backed clear. At 10 an unfenced item releases the lease and transitions once to failed with stable code; it never requeues. Fence start/quarantine/clear use `AgentWorkItemStateService`, exact CAS, and audit events; raw fence token is returned once and never logged/stored. Terminal rows never regress.

- [ ] **Step 4: Run GREEN and commit backend**

```bash
docker compose exec -T app php artisan test tests/Feature/Hades/VerificationWorkItemApiTest.php
git add backend/app/Http/Controllers/Plugin/AgentWorkItemController.php backend/app/Services/Hades/HadesCapabilityPolicy.php backend/routes/api.php backend/tests/Feature/Hades/HadesM1ApiTest.php backend/tests/Feature/Hades/VerificationWorkItemApiTest.php
git commit -m "feat(verification): expose scoped leased verification work"
```

### Task 4: Derive Immutable Graph Verification Overlays

**Backend files:**
- Create: `backend/app/Services/Hades/GraphVerificationOverlayService.php`
- Create: `backend/app/Services/Hades/GraphVerificationOverlayDraft.php`
- Modify: `backend/app/Services/Graph/V2/GraphV2LifecycleProjectionMapper.php`
- Modify: `backend/app/Services/Graph/CanonicalGraphProjectionService.php`
- Test: `backend/tests/Integration/Hades/GraphVerificationOverlayTest.php`

**Interfaces:**

```php
final readonly class GraphVerificationOverlayDraft
{
    /** @param list<array<string,mixed>> $derivedRecords */
    public function __construct(
        public string $verificationRequestId,
        public string $sourceGraphImportId,
        public string $artifactGraphVersion,
        public string $manifestSemanticSha256,
        public string $subjectFingerprint,
        public string $resultDigest,
        public string $resolution,
        public array $derivedRecords,
        public string $payloadSha256,
    ) {}
}

final class GraphVerificationOverlayService
{
    public function prepareDraft(
        VerificationRequest $request,
        array $canonicalResult,
    ): ?GraphVerificationOverlayDraft;
    public function commitDraftWithinCurrentTransaction(
        VerificationRequest $lockedRequest,
        GraphVerificationOverlayDraft $draft,
        GraphArtifactReferenceGuard $guard,
    ): ?GraphVerificationOverlay;
    public function verificationSetHash(HadesGraphImport $import): string;
}
```

- [ ] **Step 1: Add RED operation/adversarial tests**

Test `resolve_candidate_set`, `resolve_call_targets`, `resolve_edge_targets`, `reject_unresolved_subject`; server-derived node/edge/structure IDs only; exact subject ownership; entrypoint effective handler view; complete-candidate structure behavior; assertion-exclusive unknown boundary suppression; no arbitrary injected record; deferred no overlay; overlay changes projection version; contradiction disappears only in new effective projection; audit/base immutable; frontier step preserved. Create a real overlay row through one outer `GraphArtifactReferenceLock` invocation and prove `VerificationGraphArtifactReachabilityProvider` retains its source import. A missing/wrong/revoked guard must fail before insert. Race overlay creation against cleanup and prove cleanup's locked recheck aborts without deleting artifact bytes or projection state. Inject a deliberately slow artifact reader and prove all artifact I/O and closure computation finishes before any maintenance/import/scope/head/work/request DB or advisory lock is acquired.

In one completion transaction, lock the projection head before work/request rows, insert the overlay, compute the exact ordered effective overlay set, call Plan 2 `CanonicalGraphDesiredProjectionService::recordWithinCurrentTransaction()`, and replace the head's `canonical_graph_desired_verification_overlays` links before commit. Assert overlay, desired generation, and desired links roll back together. Kill after commit before job dispatch and prove Plan 2's minute reconciler sees desired≠active and dispatches. When building a candidate, persist one scoped `canonical_graph_projection_verification_overlays` row per exact overlay; cross-project/binding/import pairs are rejected and both stored sets hash to their owner row's `verification_set_hash`.

- [ ] **Step 2: Run RED**

Run: `docker compose exec -T app php artisan test tests/Integration/Hades/GraphVerificationOverlayTest.php`

- [ ] **Step 3: Implement exact overlay derivation**

Stored JSON has exactly the fields in section 11.5. `prepareDraft()` runs before any database/advisory lock: it recomputes closures and effective IDs from the exact retained immutable artifact bytes/index, rejects worker-supplied IDs outside allowed resolution selections, sorts arrays, and returns a closed immutable draft containing source import ID, artifact graph version, manifest/object digests, request ID/target fingerprint, canonical result digest, and derived records. It performs no database write. `commitDraftWithinCurrentTransaction()` performs no artifact/object/Neo4j I/O: it asserts the guard, locked request/source/target/result and every draft fingerprint still match, then performs only bounded DB-local inserts and set-hash checks; mismatch aborts and a caller may rebuild outside locks. This service never calls `GraphArtifactReferenceLock::within()` itself. The test helper must enter the same outer lock explicitly.

Task 6 prepares the draft first, then locks the head before work/request rows and, after `commitDraftWithinCurrentTransaction()` inserts the immutable overlay, calls `CanonicalGraphDesiredProjectionService` inside the same transaction with the recomputed set hash. Only queue dispatch is after commit; it never opens a second desired-state transaction. `CanonicalGraphProjectionService` incorporates the immutable overlay set into `verification_set_hash`/`projection_version` and writes the exact normalized projection-overlay membership rows when it creates the candidate. The provider remains read-only; do not make it create or repair references.

- [ ] **Step 4: Run GREEN and commit backend**

```bash
docker compose exec -T app php artisan test tests/Integration/Hades/GraphVerificationOverlayTest.php
git add backend/app/Services/Hades/GraphVerificationOverlayService.php backend/app/Services/Hades/GraphVerificationOverlayDraft.php backend/app/Services/Graph/V2/GraphV2LifecycleProjectionMapper.php backend/app/Services/Graph/CanonicalGraphProjectionService.php backend/tests/Integration/Hades/GraphVerificationOverlayTest.php
git commit -m "feat(verification): apply immutable graph evidence overlays"
```

### Task 5: Make Wiki Claim Ledgers and Results Revision-Safe

**Backend files:**
- Modify: `backend/app/Services/WikiRevisionService.php`
- Create: `backend/app/Services/WikiRevisionDraft.php`
- Modify: `backend/app/Services/Hades/WikiVerificationService.php`
- Modify: `backend/app/Services/Hades/WikiVerificationEvidencePolicy.php`
- Modify: `backend/app/Services/WikiRefreshResultService.php`
- Create: `backend/app/Services/Graph/Retention/WikiGraphArtifactReachabilityProvider.php`
- Modify: `backend/app/Providers/AppServiceProvider.php`
- Modify: `backend/app/Http/Controllers/Hades/WikiPageController.php`
- Modify: `backend/app/Http/Controllers/Hades/CapabilitiesController.php`
- Modify: `backend/routes/api.php`
- Modify: `backend/app/Models/WikiVerificationClaim.php`
- Modify: `backend/app/Models/WikiGraphArtifactReference.php`
- Test: `backend/tests/Feature/Hades/WikiVerificationQueueTest.php`
- Modify: `backend/tests/Feature/Hades/HadesWikiWorkflowTest.php`
- Modify: `backend/tests/Feature/Hades/HadesWikiOpenApiContractTest.php`

**Interfaces:**
- Generated push requires `claim_locators:[{byte_start,byte_end,ordinal}]`.
- Backend derives `content_sha256=SHA256(NFC Markdown bytes)`, `normalized_text`, and `fingerprint=SHA256(JCS({page_id,revision_id,content_sha256,normalized_text,byte_start,byte_end,ordinal}))` after allocating the revision ID.
- Result ledger size is 1–80 and must exactly match the claimed ledger.
- `WikiRevisionService::writeDraftWithinCurrentTransaction()` accepts an optional `GraphArtifactReferenceGuard`; if the draft carries an available source snapshot or any `graph_ref`, the guard is mandatory and must contain every referenced import.

```php
final readonly class WikiRevisionDraft
{
    /** @param list<array{byte_start:int,byte_end:int,ordinal:int,normalized_text:string}> $claims */
    /** @param list<array<string,mixed>> $graphReferences */
    public function __construct(
        public string $projectId,
        public string $workspaceBindingId,
        public string $wikiPageId,
        public ?string $expectedCurrentRevisionId,
        public string $markdownNfc,
        public string $contentSha256,
        public string $sourceStatus,
        public ?string $sourceGraphImportId,
        public ?string $sourceArtifactGraphVersion,
        public ?string $sourceManifestSemanticSha256,
        public array $claims,
        public array $graphReferences,
        public array $generatorMetadata,
        public string $draftSha256,
    ) {}
}

final class WikiRevisionService
{
    public function prepareDraft(array $payload): WikiRevisionDraft;

    /** @return array{wiki_page_id:string,wiki_revision_id:string,source_status:string,created:bool} */
    public function writeDraftWithinCurrentTransaction(
        WikiRevisionDraft $draft,
        ?int $authorUserId = null,
        ?string $authorDeviceId = null,
        ?array $auditActor = null,
        ?GraphArtifactReferenceGuard $graphGuard = null,
    ): array;
}

final class WikiVerificationService
{
    public function prepareDraft(
        VerificationRequest $request,
        array $canonicalResult,
    ): WikiRevisionDraft;

    public function commitDraftWithinCurrentTransaction(
        VerificationRequest $lockedRequest,
        WikiRevisionDraft $draft,
        GraphArtifactReferenceGuard $guard,
    ): string;
}

final class WikiGraphArtifactReachabilityProvider implements GraphArtifactReachabilityProvider
{
    public function retains(HadesGraphImport $import): bool;
}
```

- [ ] **Step 1: Add RED multibyte/immutability tests**

Test UTF-8 byte offsets, sorted/non-overlap locators, client fingerprint/text rejection, content mismatch, zero claims omitted, >80 generated claims split/omit, 81–512 human claims audit diagnostic, full-content gate, stale revision CAS, exact supported/contradicted classification set, verified-all-supported, contradicted-at-least-one, unclassifiable-deferred, and no content edits. Assert normalized tables and the show DTO are semantically identical. Prove a Wiki source-snapshot/evidence reference retains exactly its graph import through `WikiGraphArtifactReachabilityProvider`; unrelated page/project/binding/import rows do not. A missing/wrong/revoked guard fails before insert. Race ledger/evidence creation against cleanup and assert maintenance lease, import lock, scope, projection head, then page/revision. A slow evidence/artifact resolver must finish before any such lock is acquired. A `verified_from_code|conflict_with_code` revision event is quiet only while its certification source still equals the locked active=desired import; a later source queues one page-level re-verification request.

Exercise `WikiRefreshResultService` with a real job and prove it propagates `job.workspace_binding_id` into every Hades-generated `WikiRevisionService` payload; new revisions never fall into `wiki_workspace_binding_missing`. Prove the old direct mutation `POST /api/hades/v1/wiki/pages/{page}/verify`, controller method, capability-route advertisement, and OpenAPI operation are removed: verification happens only through leased work completion. Keep capability name `verify_project_wiki` for list/claim/complete authorization.

- [ ] **Step 2: Add RED new-revision fingerprint tests**

```php
it('recomputes every claim fingerprint against the new verification revision id', function () {
    $completion = completeWikiVerification($this->supportedResult());
    foreach ($completion->revision->claims as $claim) {
        expect($claim->fingerprint)->toBe(hashJcs([
            'page_id' => $completion->revision->page_id,
            'revision_id' => $completion->revision->id,
            'content_sha256' => $completion->revision->content_sha256,
            'normalized_text' => $claim->normalized_text,
            'byte_start' => $claim->byte_start,
            'byte_end' => $claim->byte_end,
            'ordinal' => $claim->ordinal,
        ]));
    }
});
```

- [ ] **Step 3: Run RED**

Run: `docker compose exec -T app php artisan test tests/Feature/Hades/WikiVerificationQueueTest.php`

- [ ] **Step 4: Implement immutable Wiki application**

For a Hades Wiki push, `WikiPageController` validates locators and calls `WikiRevisionService::prepareDraft()` before any DB/advisory lock. The draft resolves/hashes immutable graph evidence and contains exact source import/version/object digests, Markdown/content digest, locators, normalized claim text, target page/current-revision expectation, and reference rows; no external I/O remains. It then either enters `GraphArtifactReferenceLock` once or uses a domain-only transaction when the source snapshot is unavailable. Inside, acquire Plan 3 scope lock, lock the exact projection head, then lock page/current revision. Recompute the source and draft fingerprints under the locks. Available-source writes require locked active import and desired import to be the same draft source; A-active/B-desired returns retryable `graph_projection_updating` and writes nothing. Source-unavailable is valid only when both active and desired imports are null. `writeDraftWithinCurrentTransaction()` performs only bounded DB checks/inserts, allocates the ULID, writes exact Markdown/digest/claim/reference rows, CASes the pointer, and emits the Task 2 event after the outer commit. Mismatch discards the draft and retries from outside.

For a verification result, `WikiVerificationService` prepares the closed revision/evidence draft before Task 6 enters any lock; Task 6 then owns maintenance/import/scope/head and passes the same draft and guard. The service never reacquires the import lock or performs external evidence I/O. It invokes `WikiRevisionService::writeDraftWithinCurrentTransaction()` with `expected_current_revision_id` and the same guard. `WikiRevisionService` alone locks page/current revision, revalidates request/source/content/result/draft digests, allocates the new ULID, copies exact Markdown/digest/locators/text, recomputes every fingerprint including `content_sha256`, translates classifications by `(ordinal,byte_start,byte_end,normalized_text)`, writes evidence/reference rows, CASes the pointer, and registers `WikiCurrentRevisionActivated` after the outermost commit. Available-source completion requires request source = locked active import = locked desired import; source-unavailable completion requires both head sources null. Rollback/failed CAS emits nothing. Register `WikiGraphArtifactReachabilityProvider`; it is read-only and queries only indexed `wiki_graph_artifact_references`.

Delete the legacy `WikiVerificationService::verify()` adapter and direct route instead of maintaining two mutation protocols. Update current workflow/OpenAPI tests to assert route absence and queue completion as the sole path. `WikiRefreshResultService` includes normalized `workspace_binding_id` in its write payload; do not hide it only in audit metadata.

After Task 5, **replace** the sole Task 2 tag call with this exact fixed provider set in `AppServiceProvider::register()`; do not append a second tag call, and preserve the existing `giveTagged()` binding:

```php
$this->app->tag(
    [
        ProjectionGraphArtifactReachabilityProvider::class,
        VerificationGraphArtifactReachabilityProvider::class,
        WikiGraphArtifactReachabilityProvider::class,
    ],
    GraphArtifactReachability::PROVIDER_TAG,
);
$this->app->when(GraphArtifactReachability::class)
    ->needs('$providers')
    ->giveTagged(GraphArtifactReachability::PROVIDER_TAG);
```

- [ ] **Step 5: Run GREEN and commit backend**

```bash
docker compose exec -T app php artisan test tests/Feature/Hades/WikiVerificationQueueTest.php
git add backend/app/Services/WikiRevisionService.php backend/app/Services/WikiRevisionDraft.php backend/app/Services/Hades/WikiVerificationService.php backend/app/Services/Hades/WikiVerificationEvidencePolicy.php backend/app/Services/WikiRefreshResultService.php backend/app/Services/Graph/Retention/WikiGraphArtifactReachabilityProvider.php backend/app/Providers/AppServiceProvider.php backend/app/Http/Controllers/Hades/WikiPageController.php backend/app/Http/Controllers/Hades/CapabilitiesController.php backend/routes/api.php backend/app/Models/WikiVerificationClaim.php backend/app/Models/WikiGraphArtifactReference.php backend/tests/Feature/Hades/WikiVerificationQueueTest.php backend/tests/Feature/Hades/HadesWikiWorkflowTest.php backend/tests/Feature/Hades/HadesWikiOpenApiContractTest.php
git commit -m "feat(wiki): verify immutable claim ledgers through queue"
```

### Task 6: Validate Structured Results and Complete Atomically

**Backend files:**
- Create: `backend/app/Services/Hades/VerificationCompletion.php`
- Create: `backend/app/Services/Hades/VerificationCompletionService.php`
- Create: `backend/app/Services/Hades/HadesAuthenticatedPrincipal.php`
- Modify: `backend/app/Http/Controllers/Plugin/AgentWorkItemController.php`
- Test: `backend/tests/Feature/Hades/VerificationCompletionTest.php`

**Interfaces:**

```php
final readonly class HadesAuthenticatedPrincipal
{
    private function __construct(
        public string $apiTokenId,
        public string $hadesAgentId,
        public string $deviceId,
        public string $projectId,
        public string $workspaceBindingId,
        /** @var list<string> */
        public array $capabilities,
    ) {}

    public static function fromRequestAttributes(Request $request): self;
}

final readonly class VerificationCompletion
{
    /** @param array<string, mixed> $canonicalResult */
    public function __construct(
        public string $workItemId,
        public string $verificationRequestId,
        public int $stateVersion,
        public array $canonicalResult,
        public string $resultDigest,
        public ?string $graphOverlayId,
        public ?string $wikiRevisionId,
        public bool $idempotentReplay,
    ) {}
}

final class VerificationCompletionService
{
    public function complete(
        AgentWorkItem $item,
        HadesAuthenticatedPrincipal $principal,
        array $submittedResult,
    ): VerificationCompletion;
}
```

The factory accepts only middleware-populated authenticated rows, requires a non-null active device and linked binding, normalizes capabilities through `HadesCapabilityPolicy`, and rejects missing/revoked/disabled state. No public constructor/factory accepts request JSON. Completion uses the stored IDs to reload token, agent, device, project membership, binding, and capability under its locked path; the DTO is routing context, not a substitute for the authoritative reread.

- [ ] **Step 1: Add RED result/CAS/idempotency/lock tests**

Test schema discriminator, `verified|contradicted|deferred`, no unknown/free-form field, 1MiB result ceiling, evidence sort/dedupe, result digest before JSONB, claimed/running first completion only, completed same digest returns the stored DTO after lease expiry, different digest 409, queued/failed/canceled/stale rejection, stale artifact/revision writes no domain effect, and one transaction stores semantically equal JSONB/digest in both rows. Controller constructs `HadesAuthenticatedPrincipal` only from authenticated plugin-token/device/agent/binding rows; client principal fields are rejected. Same-digest replay still rejects cross-agent, cross-device, cross-project, cross-binding, revoked token, or missing capability. Instrument the nonempty-import path and prove exactly one `GraphArtifactReferenceLock::within()` call, then scope lock, projection head `FOR UPDATE`, work item, request, overlay, Wiki page/revision; the exact locked import set/source head is recomputed before mutation, and Graph/Wiki appliers consume the same guard without nesting. Separately prove that a Wiki `source_unavailable` deferred completion opens one ordinary transaction, takes scope then projection-head lock, never constructs/calls the reference lock or a guard, and creates no Wiki/overlay/reference domain object. Race same-digest replay against retention and prove either replay returns the retained stored DTO under locks or the item is not found; it never bypasses authorization or dereferences deleted rows. Race a publisher changing the head and require completion either precede it under the head lock or abort as `verification_source_changed`. In both paths any mismatch/rollback leaves head desired state, work, request, overlay, Wiki, and references unchanged.

- [ ] **Step 2: Run RED**

Run: `docker compose exec -T app php artisan test tests/Feature/Hades/VerificationCompletionTest.php`

- [ ] **Step 3: Implement the single outer completion transaction**

Canonicalize the submitted result and derive its digest before opening a transaction. Authenticate the full server-derived principal first, then read only item/request and candidate source import IDs. Build the graph overlay or Wiki evidence draft completely outside locks from immutable artifact bytes. For a nonempty import set, enter `GraphArtifactReferenceLock` once. A valid Wiki `source_unavailable` request has an empty set and can complete only as `deferred`; for that one case open an ordinary transaction without constructing a guard. In either path acquire `VerificationScopeLock`, lock the exact projection head through `CanonicalGraphDesiredProjectionService::lockHeadWithinCurrentTransaction()`, then lock/reload item and request followed by overlay/Wiki rows. Recheck token/agent/device/project/binding/capability, target currency, result digest, and every draft/import/source fingerprint. A completed same-digest row may return its stored DTO only after this same authorization and locked reread; it skips domain writes but not locks/checks. A different digest conflicts. For first completion, available-source graph and Wiki work require locked active import and desired import both equal the request/draft source. A-active/B-desired atomically stales A work and never reverses B. Source-unavailable is valid only when both head imports are null. If the set or source differs, roll back with retryable `verification_source_changed` or commit only that stale transition as applicable; never acquire an incremental import lock.

Call exactly one closed domain branch: after exact source/draft currency checks, graph calls Task 4 `commitDraftWithinCurrentTransaction(...,$guard)` and records the recomputed desired overlay projection on the already-locked head in this same transaction; Wiki commits Task 5's prepared draft with the same guard; deferred creates no domain object or desired projection change. These branches perform bounded DB-local work only. A required A-active/B-desired race proves A cannot overwrite B or create an A overlay. Write the canonical result/digest/resolution/audit and increment work state version in that same transaction. Register only the projection job dispatch after commit; a lost callback is recovered from the durable desired head. The existing controller's `complete()` method constructs the principal from middleware attributes, delegates verification items to this service, and leaves general Kanban completion unchanged; no second route or controller is created.

- [ ] **Step 4: Run GREEN and commit backend**

```bash
docker compose exec -T app php artisan test tests/Feature/Hades/VerificationCompletionTest.php
git add backend/app/Services/Hades/VerificationCompletion.php backend/app/Services/Hades/VerificationCompletionService.php backend/app/Services/Hades/HadesAuthenticatedPrincipal.php backend/app/Http/Controllers/Plugin/AgentWorkItemController.php backend/tests/Feature/Hades/VerificationCompletionTest.php
git commit -m "feat(verification): complete structured results atomically"
```

### Task 7: Add Human Admin Retry without Duplicating Live Work

**Backend files:**
- Modify: `backend/app/Http/Controllers/Plugin/AgentWorkItemController.php`
- Modify: `backend/routes/api.php`
- Modify: `backend/app/Services/Hades/VerificationQueueService.php`
- Test: `backend/tests/Feature/Hades/VerificationRetryTest.php`

**Interfaces:**
- Endpoint: `POST /api/plugin/v1/agent-work-items/{item}/retry-verification`.
- Requires scope `verification.retry`, an API token owned by a current project Admin with `device_id=null`, explicit human invocation, and a 1–500-byte reason. Hades auto-issued device/plugin credentials never receive this scope.
- Eligible selected item: failed/canceled or completed+deferred; target/source still current.

- [ ] **Step 1: Add RED chain/race tests**

Test agent/device token rejection, missing `verification.retry`, non-admin token owner, wrong project, stale selected, selected-not-latest, existing live conflict, latest eligible creates generation+1 with both retry links, A→B→A coalescence, race with reconciliation creates exactly one successor, terminal predecessor unchanged, audit reason/selected ancestor. Instrument two independent PostgreSQL connections and prove the total order is outer import/reference lock when the pre-read source set is nonempty, then verification scope advisory lock, exact projection head `FOR UPDATE`, complete request/work chain in ascending `(attempt_generation,id)`, and only then overlay/Wiki/domain rows. Race head publication, retry vs reactivation, and retention/cleanup; require either one exact successor or `409 verification_source_changed`, never deadlock or duplicate generation. For a source-unavailable Wiki target prove one ordinary transaction starts at scope then head, constructs no reference guard, and remains safe against a simultaneous source activation. Any changed selected/latest/current/import set aborts with no successor and a later attempt restarts outside all locks.

- [ ] **Step 2: Run RED**

Run: `docker compose exec -T app php artisan test tests/Feature/Hades/VerificationRetryTest.php`

- [ ] **Step 3: Implement locked retry algorithm, run GREEN, commit backend**

Authenticate through the existing plugin-token middleware with the new non-default scope, then additionally load `plugin_auth.token.user`, require Admin membership for the selected project, and reject any device-bound token. Do not add `verification.retry` to `HadesPluginCredentialIssuer`. Before mutable locks, pre-read selected/latest IDs, target/source identity, and the complete candidate import set. If nonempty, enter `GraphArtifactReferenceLock` once; otherwise open the source-unavailable transaction without a guard. In either path take scope, exact projection head, full request/work chain ascending, then later domain rows. Reread selected/latest/current/head/import set under those locks; on any difference roll back with `verification_source_changed` and restart only from outside. Reconciliation uses the identical order, so retry/reactivation creates exactly one linked successor and the loser returns the existing live generation. Task 10's CLI handler loads a separately configured human-admin secret only for an explicit interactive retry; workers and skills never receive it.

```bash
docker compose exec -T app php artisan test tests/Feature/Hades/VerificationRetryTest.php
git add backend/app/Http/Controllers/Plugin/AgentWorkItemController.php backend/routes/api.php backend/app/Services/Hades/VerificationQueueService.php backend/tests/Feature/Hades/VerificationRetryTest.php
git commit -m "feat(verification): add admin-controlled retry generations"
```

### Task 8: Clean Expired Verification Audit Chains Safely

**Backend files:**
- Create: `backend/app/Services/Hades/VerificationRetentionService.php`
- Create: `backend/app/Console/Commands/CleanupVerificationHistory.php`
- Modify: `backend/routes/console.php`
- Test: `backend/tests/Integration/Hades/VerificationRetentionTest.php`

**Interfaces:**

```php
final class VerificationRetentionService
{
    public const RETENTION_DAYS = 90;

    /** @return array{chains_examined:int,chains_deleted:int,work_items_deleted:int,requests_deleted:int,overlays_deleted:int,artifacts_rechecked:int,chains_skipped:int} */
    public function cleanup(
        ?string $projectId = null,
        ?string $workspaceBindingId = null,
        int $limit = 100,
    ): array;
}
```

The returned array has only the seven named integer keys; do not introduce an additional public extension interface. Command signature is exactly `hades:verification:cleanup {--project=} {--binding=} {--limit=100}`. Retention is fixed at 90 days and is not a CLI/config override.

- [ ] **Step 1: Add RED whole-chain/retention/race tests**

Cover a self-contained terminal chain older than 90 days; live and younger chains; a multi-generation chain selected through both `retry_of_work_item_id` and `retry_of_request_id`; desired-head overlay membership, retained projection overlay membership, Wiki/direct audit FK roots, and internal request/overlay references that must not self-retain the candidate; exact deletion order overlays, requests descending generation, work items descending generation; concurrent new reference abort; and a new reference arriving after chain commit but before artifact cleanup preserving the import/blob. An overlay-bearing chain is retained while any normalized desired/projection membership or direct external FK points to its rows/overlay. A no-overlay chain older than 90 days is deleted even when its source import remains active/previous/context-retained. A desired overlay with no candidate for more than 90 days remains retained; after the desired set moves and no projection/Wiki/audit root remains, it becomes eligible. Prove generic work-item cleanup never selects a row referenced by `verification_requests`.

- [ ] **Step 2: Run RED**

Run: `docker compose exec -T app php artisan test tests/Integration/Hades/VerificationRetentionTest.php`

- [ ] **Step 3: Implement bounded external-reachability cleanup**

Select at most `limit` deterministic root candidates ordered by oldest terminal timestamp then ID. Reconstruct each entire connected component through both retry links; skip it if any row is live or its latest `resolved_at|superseded_at|updated_at` is newer than 90 days. Outside a transaction pre-read every source import ID. Enter `GraphArtifactReferenceLock` when that set is nonempty; for a source-unavailable Wiki-only chain use an ordinary transaction. In either path take `VerificationScopeLock`, then lock the exact projection head `FOR UPDATE`, then lock/reload the whole chain in ascending `(attempt_generation,id)`. Abort if head/source, membership, terminal state, timestamps, or import set changed.

Inside that transaction inspect only references to the candidate chain/overlay rows: normalized desired-head membership, normalized retained-projection membership, Wiki references, and explicit audit/domain FKs. Any such external root rolls back/skips the chain. Do not call `GraphArtifactReachability` as a chain-retention veto: artifact roots and audit-chain roots are intentionally different, and a base graph may outlive a no-overlay chain. When no external chain root exists, delete candidate overlays, requests from highest generation to lowest, then work items from highest generation to lowest. After commit, call `GraphV2ArtifactCleanupService` for imports that may have become unreachable; only that service invokes `GraphArtifactReachability` under its own outer lock and repeats the final artifact check, so active/previous projection or a reference created between phases preserves the blob without resurrecting the deleted audit chain. Provider exceptions fail closed only in this independent artifact-cleanup phase. Schedule the command hourly with `withoutOverlapping()` and `onOneServer()`.

- [ ] **Step 4: Run GREEN and commit backend**

```bash
docker compose exec -T app php artisan test tests/Integration/Hades/VerificationRetentionTest.php
git add backend/app/Services/Hades/VerificationRetentionService.php backend/app/Console/Commands/CleanupVerificationHistory.php backend/routes/console.php backend/tests/Integration/Hades/VerificationRetentionTest.php
git commit -m "feat(verification): retain and clean whole audit chains"
```

### Task 9: Implement Agent Verification Contract, Cache, and Client

**Agent files:**
- Create: `hermes_cli/hades_verification_contract.py`
- Create: `hermes_cli/hades_verification_tasks.py`
- Modify: `hermes_cli/hades_plugin_work_items_client.py`
- Modify: `hermes_cli/hades_backend_db.py`
- Modify: `hermes_cli/hades_backend_client.py`
- Modify: `hermes_cli/hades_backend_cmd.py`
- Modify: `plugins/memory/hades_backend/__init__.py`
- Test: `tests/hermes_cli/test_hades_verification_contract.py`
- Test: `tests/hermes_cli/test_hades_verification_tasks.py`
- Modify: `tests/hermes_cli/test_hades_backend_client.py`
- Modify: `tests/hermes_cli/test_hades_backend_cmd.py`
- Modify: `tests/agent/test_hades_backend_memory_provider.py`
- Modify: `tests/agent/test_hades_backend_memory_provider_sync.py`

**Interfaces:**

```python
def validate_verification_work(payload: Mapping[str, object]) -> VerificationWork: ...
def validate_verification_result(payload: Mapping[str, object]) -> VerificationResult: ...
def canonical_result_digest(result: VerificationResult) -> str: ...

class HadesVerificationTasks:
    def list(self, filters: VerificationFilters) -> VerificationPage: ...
    def show(self, work_item_id: str) -> VerificationWork: ...
    def status(self) -> VerificationStatus: ...
```

`HadesPluginWorkItemsClient` exposes these exact methods and wire calls:

```python
def list_verification_work(self, **filters: object) -> dict[str, object]: ...
# GET /api/plugin/v1/agent-work-items?work_kind=verification&...
def show_verification_work(self, work_item_id: str) -> dict[str, object]: ...
# GET /api/plugin/v1/agent-work-items/{id}
def claim_verification_work(self, work_item_id: str, *, local_workspace_id: str, state_version: int) -> dict[str, object]: ...
# POST /api/plugin/v1/agent-work-items/{id}/claim
def heartbeat_verification_work(self, work_item_id: str, *, lease_token: str, state_version: int) -> dict[str, object]: ...
# POST /api/plugin/v1/agent-work-items/{id}/heartbeat
def complete_verification_work(self, work_item_id: str, *, lease_token: str, state_version: int, result: dict[str, object]) -> dict[str, object]: ...
# POST /api/plugin/v1/agent-work-items/{id}/complete
def retry_verification_work(self, work_item_id: str, *, reason: str) -> dict[str, object]: ...
# POST /api/plugin/v1/agent-work-items/{id}/retry-verification; human-admin client only
```

Task 9 also freezes one high-level, read-only graph command for humans and the standalone plugin:

```text
hades backend graph explore --route ROUTE_PATH --json
```

It requires an already configured project/binding, calls only v2 scopes/bootstrap/search/entrypoints/lifecycle queries, resolves the exact route match server-side, and returns one closed JSON object `{schema:"hades.graph_explore.v2",project_id,workspace_binding_id,route,entrypoint,lifecycle,completeness}`. It never accepts client projection/incarnation IDs, source paths, or a repository fallback. Missing/ambiguous route and partial capability use the stable v2 error/completeness contract. Tests freeze `hades backend graph explore --route /en/blog/ --json` as literal argv and prove no v1/direct HTTP/local traversal path runs.

List returns exactly `{protocol_version:"v1",items:[...],next_cursor:string|null}`; show/claim/heartbeat return `{protocol_version:"v1",item:{...}}`; complete returns `{protocol_version:"v1",item:{...},verification_completion:{...}}`; retry returns `{protocol_version:"v1",item:{...},verification_request:{...}}`. `work_kind=verification` is accepted only by list and expands server-side to the two exact stored kinds `hades.verification.graph.v1|hades.verification.wiki.v1`; it is never stored or accepted by show/claim/heartbeat/complete/retry. Cursor signing binds the normalized two-kind set, and tests reject cursor reuse under an exact-kind or general filter. `HadesBackendClient.graph_verification_summary()` remains the Hades-token read path and returns the four-field summary envelope from Task 2. Structured completion has its own method/body and never reuses `complete_agent_work_item(chat_message,memory_entry)`.

Mandatory execution split: **9A** implements verification schema/client/cache/capability in at most 120 minutes; **9B** performs the v1→v2 graph-read cutover in at most 120 minutes. Each ends in its own commit and review; Task 10 starts only after both are green.

- [ ] **Step 1: Add RED contract/cache ordering tests**

Test root schemas/goldens and every exact method/path/envelope above; lower remote version ignored, equal semantic match accepted, equal conflict logged/ignored, greater valid transition, live→queued reclaim only with cleared claim/lease, terminal never regresses, out-of-order list around claim/heartbeat/completion, new retry row accepted, and cached remote fields round-trip. Wire status is `canceled`; the HTTP adapter maps it once to existing local cache spelling `cancelled` and maps filters back, without renaming unrelated backend-job status constants. Test round-trip and terminal non-regression. `_detect_default_capabilities()` now explicitly declares `verify_project_graph` alongside `verify_project_wiki`; neither is an auto-job capability or implicit backend grant.

For 9B, replace `HadesBackendClient`'s v1 `GET graph/traverse` call with `POST graph/query` carrying exactly the closed v2 query body. Keep a public Python helper name only if needed for source compatibility, but its input/output contract is v2 and it has no v1 wire fallback. Update `plugins/memory/hades_backend` to use server v2 handles/context/completeness only; delete local/repository topology fallback and never synthesize callers/callees from cached memory. Test old route is never called, client-supplied scope is absent, cross-device contexts fail, and no fallback executes on 404/409.

- [ ] **Step 2: Run RED**

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_verification_contract.py tests/hermes_cli/test_hades_verification_tasks.py -q`

- [ ] **Step 3: Implement typed contract/client/cache**

Upgrade `plugin_work_items` through the existing `add_column_if_missing()` pattern inside one idempotent migration: `workspace_binding_id TEXT NULL`, `subject_type TEXT NULL`, `subject_id TEXT NULL`, `subject_version TEXT NULL`, `deduplication_key TEXT NULL`, `priority TEXT NOT NULL DEFAULT 'normal'`, `execution_attempts INTEGER NOT NULL DEFAULT 0`, `lease_expires_at TEXT NULL`, `result_json TEXT NULL`, `remote_state_version INTEGER NOT NULL DEFAULT 0`, and `remote_updated_at TEXT NULL`. The two temporal values are canonical UTC RFC3339 strings at the HTTP/SQLite boundary; parsing/comparison always converts them to aware instants and never uses lexical comparison or integer epochs. Add index `idx_plugin_work_items_verification` on `(project_id,workspace_binding_id,kind,status,priority,remote_updated_at)`. Existing rows survive with defaults/nulls and may remain non-verification; validated verification rows require binding/subject/dedupe/remote version. Open a database created with the pre-change schema, insert cached rows, run initialization twice, and prove rows/values/indexes survive. Apply the transition table before mutation; never overwrite terminal cache with an older/equal conflict.

- [ ] **Step 4: Run GREEN and commit agent**

```bash
.venv/bin/python -m pytest tests/hermes_cli/test_hades_verification_contract.py tests/hermes_cli/test_hades_verification_tasks.py -q
git add hermes_cli/hades_verification_contract.py hermes_cli/hades_verification_tasks.py hermes_cli/hades_plugin_work_items_client.py hermes_cli/hades_backend_db.py hermes_cli/hades_backend_cmd.py tests/hermes_cli/test_hades_verification_contract.py tests/hermes_cli/test_hades_verification_tasks.py tests/hermes_cli/test_hades_backend_cmd.py
git commit -m "feat(hades): cache and inspect verification work safely"
.venv/bin/python -m pytest tests/hermes_cli/test_hades_backend_client.py tests/agent/test_hades_backend_memory_provider.py tests/agent/test_hades_backend_memory_provider_sync.py -q
git add hermes_cli/hades_backend_client.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_client.py tests/agent/test_hades_backend_memory_provider.py tests/agent/test_hades_backend_memory_provider_sync.py
git commit -m "feat(hades): use graph v2 for backend reads"
```

### Task 10: Implement One-Item Verification Worker and CLI Commands

**Agent files:**
- Create: `hermes_cli/hades_verification_worker.py`
- Create: `hermes_cli/hades_verification_specialist.py`
- Create: `hermes_cli/hades_verification_lock.py`
- Create: `hermes_cli/hades_verification_containment.py`
- Modify: `hermes_cli/hades_plugin_worker.py`
- Modify: `hermes_cli/hades_plugin_tasks.py`
- Modify: `hermes_cli/hades_backend_cmd.py`
- Modify: `hermes_cli/hades_backend_client.py`
- Modify: `hermes_cli/hades_wiki_actions.py`
- Modify: `hermes_cli/hades_backend_runtime.py`
- Modify: `hermes_cli/commands.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Test: `tests/hermes_cli/test_hades_verification_worker.py`
- Test: `tests/hermes_cli/test_hades_backend_cmd.py`
- Modify: `tests/hermes_cli/test_hades_backend_client.py`
- Modify: `tests/hermes_cli/test_hades_backend_exit_status.py`

**Interfaces:**

```python
class VerificationWorker:
    def work_once(self, filters: VerificationFilters) -> VerificationWorkOutcome: ...
    def work_all(self, filters: VerificationFilters) -> VerificationBatchOutcome: ...
```

```python
class CancellationSignal(Protocol):
    def is_set(self) -> bool: ...

class VerificationSpecialistRunner(Protocol):
    def run(self, work: VerificationWork, *, cancel: CancellationSignal) -> VerificationResult: ...
```

- [ ] **Step 1: Add RED worker tests**

Test `--once` at most one, `--all` cursor pages but one claim at a time, target recheck before claim, heartbeat 30s/<15s rule, and result validation before complete. Use a fake monotonic clock to prove the hard boundary is alive at 899 seconds and expires at 900, the progress boundary is alive at 299 and expires at 300, heartbeat/log/stdout/tool-start cannot keep a hung specialist alive, only the five closed progress events with a strictly increasing sequence reset the progress timer, and no expired work-item ID is reclaimed by the same invocation. Attempt 10 still remains server-authoritative and terminalizes without an 11th lease.

A specialist uses mandatory `VerificationContainment`: delegated Linux cgroup v2, kill-on-close Windows Job Object, or a Darwin run-scoped Docker container with its own PID namespace, read-only source, no host PID/privileged/socket/nested container access, and exact run label. Unsupported/unavailable containment exits `specialist_containment_unavailable` before claim. The local launcher is blocked; tests prove it cannot construct `AIAgent` or spawn a grandchild until the parent attaches the OS domain and the backend acknowledges `specialist-fence/start`. Exercise direct-child crash with a live descendant, resistant descendant, attempted group escape, normal zero exit, exception, deadline, lease loss, parent crash, and `KeyboardInterrupt`. Whole-domain stop/kill/prove-empty is authoritative; `psutil==7.2.2` identities are diagnostic only and add no dependency.

Prove the server fence globally blocks another profile/host reclaim after lease expiry and does not increment attempts. Valid zero-exit teardown clears only after containment proof. Abnormal exit leaves/marks server quarantine before future heartbeats cease; if the parent dies, the already-active fence remains. When proof fails, the mode-0600 local marker stores only work/fence IDs, containment kind/hash, reason, and diagnostic identities; it never substitutes for the server fence. Next invocation checks both and cannot delete them on PID disappearance. Test proof-backed device clear and explicit separate-Admin clear; wrong/replayed token/generation/device/domain evidence fails. Specialist returns only schema result; stale CAS submits no second failure completion; generic worker excludes verification even if backend ignores filter; Wiki full read snapshot matches; source remains read-only. Inject deterministic fake containment/client/runner for unit tests and controlled real domains for platform-marked integration tests. Launch daemon and manual `work --all` concurrently with the same profile/project/binding and prove the local lock allows exactly one list/claim/model call while the other exits `already_running`; different profiles/bindings do not collide. For `retry`, prove a missing separate admin token, non-TTY call, declined confirmation, or worker-token fallback sends no request; explicit confirmation sends once. Prove removed direct Wiki verify is absent.

- [ ] **Step 2: Run RED**

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_verification_worker.py tests/hermes_cli/test_hades_backend_cmd.py -q`

- [ ] **Step 3: Implement exact command tree**

Commands are exactly:

```text
hades backend verification list
hades backend verification show WORK_ITEM_ID
hades backend verification status
hades backend verification work --once
hades backend verification work --all
hades backend verification retry WORK_ITEM_ID --reason "HUMAN_REASON"
```

Common filters are domain/status/priority/limit/json. No core model tool is registered.

Before listing or claiming, acquire a `filelock.FileLock` with `timeout=0` at `$HERMES_HOME/run/hades-verification-{sha256(profile+project_id+workspace_binding_id)}.lock`; add exact direct dependency `filelock==3.24.3` to `pyproject.toml` and `uv.lock`. Hold it through the complete `--once` or `--all` run, including heartbeat and completion, and release in `finally`. The lock prevents same-profile local token burn only; backend lease/CAS remains authoritative across devices/machines.

`SkillVerificationSpecialistRunner` creates one fresh contained child per item; local providers use Python `multiprocessing` **spawn**, while Darwin's provider launches the same closed worker entrypoint inside its run-scoped container. The child creates the fresh `AIAgent` and session ID, so no model client/thread/transcript is inherited or reused. It resolves only `/hades-graph-verify` or `/hades-wiki-verify` through `agent.skill_commands.get_skill_commands()`, injects that selected skill as the user task, supplies the server work payload/evidence contract, and accepts only the closed result JSON over a bounded one-result IPC envelope. It uses current configured verification model/credentials and a 40-iteration budget. Secrets enter only the exact isolated child environment/secret mount and never logs, image layers, command argv, or IPC result.

The parent owns heartbeat and lifecycle with a monotonic 900-second hard deadline from `Process.start()` and 300-second meaningful-progress deadline. Only child-ready, skill-resolved, completed-model-turn, completed-tool-call, or final-envelope events carrying a strictly increasing sequence reset the progress deadline; heartbeat/log/stdout/stream/tool-start do not, and the hard deadline never resets. On first lease/CAS loss or deadline the parent stops future heartbeats, records `specialist_hard_deadline|specialist_progress_deadline` when applicable, sets the shared event, and never completes/fails/retries or reclaims that work ID in this invocation.

Before claim, choose/probe a supported provider. After claim, create a blocked launcher/domain, call the Task 3 fence-start endpoint, retain the raw fence token only in parent memory, and release the child only after both acknowledgements. The parent owns heartbeat and deadlines. Every exit performs cancel/public `AIAgent.interrupt()` plus 10 seconds, whole-domain terminate plus 5, whole-domain kill plus 5, joins/closes IPC, and asks the provider to prove empty/destroyed. A valid zero-exit result clears the server fence with proof before completion. Exception/cancellation/deadline/lease loss/`KeyboardInterrupt` quarantines or leaves active the server fence and sends no completion/failure/retry; it may clear only after an explicit later proof. If proof fails, write the diagnostic mode-0600 local marker and exit `unsafe_specialist_process_tree`. No host can reclaim until server clear; a later invocation restarts from immutable work, never a half transcript. Task 12 installs/validates real skills; until then tests use injected fakes and Task 10 is not live-deployable.

Remove the direct `hades backend wiki verify` subcommand, `HadesBackendClient.verify_wiki_page()`, and the mutation branch in `hades_wiki_actions.py`. Queue work completion is the only Wiki verification mutation. Keep read/show Wiki commands. Update exact client/CLI/exit-status tests so no command, help text, skill, or fallback references the deleted endpoint.

`retry` is outside the worker path. It requires an interactive TTY, prints the selected item and reason, asks for yes/no confirmation, and loads only the secret named by `backend.human_admin_token_env_key`. That config value is the environment-variable **name**; the token remains in the secret store. It never falls back to the normal device-bound plugin token, and neither `work --all` nor any verification skill invokes retry. If the separate credential is absent, print the dashboard/manual-token instruction and exit without mutation.

- [ ] **Step 4: Run GREEN and commit agent**

```bash
.venv/bin/python -m pytest tests/hermes_cli/test_hades_verification_worker.py tests/hermes_cli/test_hades_backend_cmd.py -q
git add hermes_cli/hades_verification_worker.py hermes_cli/hades_verification_specialist.py hermes_cli/hades_verification_lock.py hermes_cli/hades_verification_containment.py hermes_cli/hades_plugin_worker.py hermes_cli/hades_plugin_tasks.py hermes_cli/hades_backend_cmd.py hermes_cli/hades_backend_client.py hermes_cli/hades_wiki_actions.py hermes_cli/hades_backend_runtime.py hermes_cli/commands.py pyproject.toml uv.lock tests/hermes_cli/test_hades_verification_worker.py tests/hermes_cli/test_hades_backend_cmd.py tests/hermes_cli/test_hades_backend_client.py tests/hermes_cli/test_hades_backend_exit_status.py
git commit -m "feat(hades): process verification work one item at a time"
```

### Task 11: Keep Sync Read-Only and Separate Status/Quality Domains

**Agent files:**
- Modify: `hermes_cli/hades_backend_sync.py`
- Modify: `hermes_cli/hades_backend_status.py`
- Modify: `hermes_cli/hades_quality_report.py`
- Test: `tests/hermes_cli/test_hades_backend_sync_runner.py`
- Test: `tests/hermes_cli/test_hades_backend_status.py`
- Test: `tests/hermes_cli/test_hades_quality_report.py`
- Test: `tests/agent/test_hades_backend_conversation_sync.py`

- [ ] **Step 1: Add RED non-action tests**

Assert every backend sync with a linked project/binding requests the verification summary even when no graph projection/version exists. It caches/displays only `{verification_queued,verification_high_priority,verification_by_domain}` and the next command; an optional projection version validates graph counts but never suppresses Wiki counts. A no-graph fixture with source-unavailable Wiki work must surface `wiki>0`. Sync never lists payload, claims, runs model, changes system prompt, or adds synthetic message; summary failure is warning/fail-open. Status has separate `task_work` and `verification_work`; pending verification is actionable not degraded. Quality excludes verification from Kanban shared-memory metrics.

- [ ] **Step 2: Run RED**

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_backend_sync_runner.py tests/hermes_cli/test_hades_backend_status.py tests/hermes_cli/test_hades_quality_report.py tests/agent/test_hades_backend_conversation_sync.py -k verification -q`

- [ ] **Step 3: Implement, run GREEN, commit agent**

```bash
.venv/bin/python -m pytest tests/hermes_cli/test_hades_backend_sync_runner.py tests/hermes_cli/test_hades_backend_status.py tests/hermes_cli/test_hades_quality_report.py tests/agent/test_hades_backend_conversation_sync.py -k verification -q
git add hermes_cli/hades_backend_sync.py hermes_cli/hades_backend_status.py hermes_cli/hades_quality_report.py tests/hermes_cli/test_hades_backend_sync_runner.py tests/hermes_cli/test_hades_backend_status.py tests/hermes_cli/test_hades_quality_report.py tests/agent/test_hades_backend_conversation_sync.py
git commit -m "feat(hades): surface verification without sync side effects"
```

### Task 12: Add Verification Orchestrator and Specialist Skills

**Agent files:**
- Create: `skills/autonomous-ai-agents/hades-verify/SKILL.md`
- Create: `skills/autonomous-ai-agents/hades-verify/agents/openai.yaml`
- Create: `skills/autonomous-ai-agents/hades-graph-verify/SKILL.md`
- Create: `skills/autonomous-ai-agents/hades-graph-verify/agents/openai.yaml`
- Modify: `skills/autonomous-ai-agents/hades-wiki-verify/SKILL.md`
- Modify: `skills/autonomous-ai-agents/hades-wiki-verify/agents/openai.yaml`
- Test: `tests/hermes_cli/test_hades_wiki_skills.py`

**Interfaces:**
- `hades-verify`: list → choose one → delegate by domain → validate → complete.
- `hades-graph-verify`: read-only source/backend evidence → structured result.
- `hades-wiki-verify`: routes to queue worker; no direct mutation in worker path.

- [ ] **Step 1: Read `skill-creator`/`writing-skills` before editing skills**

The executor must invoke the available skill-authoring instructions and read each selected `SKILL.md` completely. This step is mandatory because these files define agent behavior.

- [ ] **Step 2: Add RED skill routing and safety tests**

Test orchestrator routes exact domain, specialists cannot write source, same run cannot certify without independent reread, Wiki worker full snapshot gate, no legacy direct verify command/endpoint, exact result-only output, and unavailable/ambiguous evidence produces `deferred`. Instantiate Task 10's real `SkillVerificationSpecialistRunner` with a controlled model transport and prove each domain resolves the installed skill, uses a fresh session, returns the closed result, and responds to cancellation without completion.

- [ ] **Step 3: Run RED**

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_wiki_skills.py -q`

- [ ] **Step 4: Implement skills and run GREEN**

Each skill frontmatter `description` is nonempty and at most 60 Unicode code points; the test parses YAML and counts Python characters, so bytes/words do not substitute for the limit. Instructions explicitly say only the parent/orchestrator commands its leaf, source work is read-only, exact result JSON is required, and an unavailable/ambiguous target returns `deferred` rather than a guess. `hades-wiki-verify` invokes only `hades backend verification work --once/--all`; it contains no `wiki verify` command or direct mutation path.

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_wiki_skills.py -q`

- [ ] **Step 5: Commit agent**

```bash
git add skills/autonomous-ai-agents/hades-verify/SKILL.md skills/autonomous-ai-agents/hades-verify/agents/openai.yaml skills/autonomous-ai-agents/hades-graph-verify/SKILL.md skills/autonomous-ai-agents/hades-graph-verify/agents/openai.yaml skills/autonomous-ai-agents/hades-wiki-verify/SKILL.md skills/autonomous-ai-agents/hades-wiki-verify/agents/openai.yaml tests/hermes_cli/test_hades_wiki_skills.py
git commit -m "feat(hades): verify graph and wiki evidence through skills"
```

### Task 13: Bootstrap Human-Readable Wiki Pages from Current Evidence

**Agent files:**
- Modify: `skills/autonomous-ai-agents/hades-wiki-push/SKILL.md`
- Modify: `skills/autonomous-ai-agents/hades-wiki-push/agents/openai.yaml`
- Modify: `hermes_cli/hades_wiki_actions.py`
- Modify: `hermes_cli/hades_backend_client.py`
- Test: `tests/hermes_cli/test_hades_wiki_bootstrap.py`

**Interfaces:**
- Empty-project page set is fixed: Overview, Architecture, Entrypoints and lifecycles, Domain and data, External integrations, Operations.
- Current graph v2 is read first; current project memory/Wiki supplies terminology only; vector search locates but never certifies evidence.
- Visible body is human Markdown. Claim ledgers, fingerprints, graph references, and certification source are normalized backend rows; signed handles/search snapshots remain ephemeral graph protocol values and are never copied into a Wiki revision. `generator_metadata` contains only bounded generator/source descriptors (`generator_id`, `generator_version`, `source_kind`, `source_projection_id`, `generated_at`) and no claim/evidence ledger.

- [ ] **Step 1: Add RED bootstrap tests**

Test current v2 graph first through `POST graph/query`, human title/purpose/prose/tables/lists/source-relative links, machine metadata absent from Markdown, stable slug/revision CAS, rerun no duplicate title, developer-provided page not overwritten, no evidence no page, zero claims omitted, generated page <=80 claims, exact byte locators sent, normalized ledger rows, and exactly one page/revision verification request that covers the complete ledger (never one work item per claim).

- [ ] **Step 2: Run RED**

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_wiki_bootstrap.py -q`

- [ ] **Step 3: Implement bootstrap through existing push path**

Do not create a second generator. Build final NFC Markdown first, identify factual byte spans, split or omit before the 80-claim ceiling, send only locators, and let the backend allocate revision/fingerprints. Pages without evidence or factual claims are absent rather than speculative.

- [ ] **Step 4: Run GREEN and commit agent**

```bash
.venv/bin/python -m pytest tests/hermes_cli/test_hades_wiki_bootstrap.py -q
git add skills/autonomous-ai-agents/hades-wiki-push/SKILL.md skills/autonomous-ai-agents/hades-wiki-push/agents/openai.yaml hermes_cli/hades_wiki_actions.py hermes_cli/hades_backend_client.py tests/hermes_cli/test_hades_wiki_bootstrap.py
git commit -m "feat(hades): bootstrap human-readable project wiki pages"
```

### Task 14: Synchronize OpenAPI and Hades Operator Documentation

**Agent files:**
- Modify: `docs/hades/openapi-hades-v1.json`
- Modify: `docs/hades/backend.md`
- Modify: `docs/hades/operations.md`
- Modify: `docs/backend-agent-coordination.md`
- Test: `tests/hermes_cli/test_hades_openapi_graph_v2.py`
- Test: `tests/hermes_cli/test_hades_documented_commands.py`

**Interfaces:**
- `PluginAgentWorkItem.payload` is a discriminator `oneOf` for existing Kanban payload and `VerificationWorkPayload`.
- OpenAPI includes graph imports/chunks, verification filters/result/completion/retry, v2 dashboard/plugin graph DTO references, and capability errors.
- Documentation states v2-only graph, workspace-binding scope, source identity, completeness/evidence/lifecycle, verification queue, vector-as-candidate role, chunk resume, and projection reconciliation. It may link to the approved DR/retirement plans but must not claim their future commands are implemented.

- [ ] **Step 1: Add RED OpenAPI/command/reference tests**

```python
def test_openapi_contains_graph_v2_and_no_current_v1_graph_schema():
    spec = json.loads(Path("docs/hades/openapi-hades-v1.json").read_text())
    assert "/api/hades/v1/graph-imports" in spec["paths"]
    assert "VerificationResult" in spec["components"]["schemas"]
    encoded = json.dumps(spec, sort_keys=True)
    assert "hades.code_graph.v2" in encoded
    assert "hades.php_graph.v1" not in encoded
    assert "hades.code_graph.v1" not in encoded
```

Extract every already-implemented `hades backend verification` and graph-v2 Artisan command and compare names/options with current registries/route definitions. Backup, restore, and retirement command-parity assertions belong to Plan 5 after those commands exist; this task asserts only that links label them as planned, not available.

- [ ] **Step 2: Run RED**

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_openapi_graph_v2.py tests/hermes_cli/test_hades_documented_commands.py -q`

- [ ] **Step 3: Update OpenAPI from root contracts**

Embed/reference closed schema components without loosening required/nullability/additional-properties rules. Remove active v1/legacy-adapter/fallback claims. Preserve unrelated Hades API operations and API major version v1.

- [ ] **Step 4: Write operational documentation**

Document exact commands, states, failure codes, recovery order, project/binding isolation, read-only sync notification, admin-versus-agent capability distinction, and the rule that no `LOGBOOK_CARNOVALI` entry is written.

- [ ] **Step 5: Run GREEN and commit agent**

```bash
.venv/bin/python -m pytest tests/hermes_cli/test_hades_openapi_graph_v2.py tests/hermes_cli/test_hades_documented_commands.py -q
git add docs/hades/openapi-hades-v1.json docs/hades/backend.md docs/hades/operations.md docs/backend-agent-coordination.md tests/hermes_cli/test_hades_openapi_graph_v2.py tests/hermes_cli/test_hades_documented_commands.py
git commit -m "docs(hades): document graph v2 and verification operations"
```

### Task 15: Update and Smoke-Test the Standalone Codex Plugin

**Standalone plugin source (never edit the installed cache):**
- Modify: `/Users/gabriele/plugins/hades-backend/.codex-plugin/plugin.json`
- Modify: `/Users/gabriele/plugins/hades-backend/skills/hades-backend/SKILL.md`
- Modify: `/Users/gabriele/plugins/hades-backend/skills/hades-wiki-push/SKILL.md`
- Modify: `/Users/gabriele/plugins/hades-backend/skills/hades-wiki-verify/SKILL.md`
- Create: `/Users/gabriele/plugins/hades-backend/skills/hades-graph-explore/SKILL.md`
- Create: `/Users/gabriele/plugins/hades-backend/skills/hades-graph-explore/agents/openai.yaml`
- Create: `/Users/gabriele/plugins/hades-backend/skills/hades-verify/SKILL.md`
- Create: `/Users/gabriele/plugins/hades-backend/skills/hades-verify/agents/openai.yaml`
- Create: `/Users/gabriele/plugins/hades-backend/tests/test_plugin_contract.py`

- [ ] **Step 1: Add RED standalone contract tests**

Parse the manifest and every skill completely. Assert unique names, nonempty descriptions of at most 60 Unicode code points, no direct HTTP/private endpoint implementation, no v1 `graph/traverse`, no direct `wiki verify`, no token literals, and only supported Hades CLI commands. Graph exploration delegates to the installed CLI's v2 query path; verification delegates to `hades backend verification list|show|status|work`, never reimplements schemas, retries, claims, or parsers. State-changing commands still require user intent; read-only status/list/show/query may auto-run inside the same backend project.

- [ ] **Step 2: Implement as an independent plugin repository**

Use `/Users/gabriele/plugins/hades-backend` as the editable source. Never edit `/Users/gabriele/.codex/plugins/cache/...`. If the source directory is not yet Git-controlled, initialize a standalone repository there, create branch `codex/graph-v2-verification`, and commit only plugin files; do not move it into the Agent core tree. Bump the manifest version to a strictly newer `0.2.0+codex.YYYYMMDDHHMMSS` value and keep the plugin dependent on the separately installed `hades` executable. No MCP server/core tool is added.

- [ ] **Step 3: Run tests and install the exact candidate**

The implementation worker runs the standalone test plus `codex plugin list`, updates the personal marketplace source, and executes `codex plugin add hades-backend@personal`. It confirms the installed version equals the new manifest, then stops and writes `.codex-artifacts/graph-v2/codex-plugin-install-ready.json` with plugin commit SHA, source tree digest, manifest version, install command/result, and `fresh_task_required:true`. It cannot validate its own immutable task catalog. If no Git remote exists, commit locally and report `push:not_configured`; never invent a remote.

- [ ] **Step 4: Coordinator/user fresh-task catalog gate (max 30 min)**

Only the coordinator/user opens a genuinely new Codex task after installation and supplies the expected plugin SHA/version. Use this exact prompt:

```text
Read-only Hades plugin smoke. Do not edit files, install/update anything, or mutate backend state. Confirm the installed hades-backend plugin version and source digest equal EXPECTED_VERSION and EXPECTED_SHA256. Enumerate the current task's skill catalog and require exactly these five plugin skills: hades-backend, hades-wiki-push, hades-wiki-verify, hades-graph-explore, hades-verify. Run `command -v hades`. If the already-configured Hades profile is linked to EXPECTED_PROJECT_ID, run exactly `hades backend status --json`, `hades backend verification status --json`, `hades backend verification list --limit 1 --json`, and `hades backend graph explore --route /en/blog/ --json`; otherwise do not configure/bootstrap anything and report backend_read_smoke=deferred_to_plan_6. Return one JSON object only with schema=hades.codex_plugin_fresh_task_smoke.v1, task_id, plugin_version, plugin_source_sha256, skill_names, hades_executable, backend_read_smoke, backend_project_id_or_null, commands, passed, and failure_or_deferred_reason.
```

The coordinator records the real new task ID and validates that closed result into `.codex-artifacts/graph-v2/codex-plugin-smoke.json`; `passed` requires version/digest, executable, and all five catalog entries even when only the backend read is legitimately deferred. Inspecting cache files or this old task never satisfies the gate. At 30 minutes without a closed result, checkpoint `fresh_task_gate_pending` and stop; do not loop or mark V19 green.

### Task 16: Close V01–V19 End-to-End

**Backend/Agent files:**
- Modify only if a mapped node is missing: `backend/tests/Feature/Hades/VerificationMigrationTest.php`, `backend/tests/Integration/Hades/VerificationQueueReconciliationTest.php`, `backend/tests/Feature/Hades/VerificationWorkItemApiTest.php`, `backend/tests/Feature/Hades/VerificationCompletionTest.php`, `backend/tests/Integration/Hades/GraphVerificationOverlayTest.php`, `backend/tests/Feature/Hades/WikiVerificationQueueTest.php`, `backend/tests/Feature/Hades/VerificationRetryTest.php`, `backend/tests/Integration/Hades/VerificationRetentionTest.php`, `tests/hermes_cli/test_hades_verification_contract.py`, `tests/hermes_cli/test_hades_verification_tasks.py`, `tests/hermes_cli/test_hades_verification_worker.py`, `tests/hermes_cli/test_hades_backend_cmd.py`, `tests/agent/test_hades_backend_conversation_sync.py`, `tests/hermes_cli/test_hades_backend_status.py`, `tests/hermes_cli/test_hades_wiki_bootstrap.py`
- Create backend report: `.codex-artifacts/graph-v2/verification-backend-gates.json`
- Create agent report: `.codex-artifacts/graph-v2/verification-agent-gates.json`

**Mandatory execution split:** **16A-implementation** closes missing backend/PostgreSQL test nodes in at most 150 minutes, commits only test changes, and passes the normal fresh coordinator merge/test/push boundary. **16B-implementation** does the same for Agent/plugin/fixture nodes and may run concurrently only after Tasks 1–15 are integrated. Reports are not created in either implementation commit. After 16A implementation is integrated, **16A-evidence** checks out that immutable backend `subject_main_sha` in a clean detached test worktree, runs/maps gates, then creates an evidence-only branch from that subject and commits only the backend report. After both implementation integrations exist and 16A's exact backend subject SHA is known, **16B-evidence** does the analogous Agent run and commits only the Agent report plus live-smoke handoff; the handoff references immutable subject SHAs, never its own evidence commit. **16C** is a read-only coordinator pass of at most 90 minutes: verify both evidence commits/reports against their subject SHAs/contract digests, union per-node ownership, run only missing cross-runtime golden comparisons, obtain final C3 review, and update the checkpoint. A missing/failed node returns to a new owning implementation branch; evidence sessions and 16C never patch product code.

- [ ] **Step 1: Map every verification gate to one or more exact test/evidence nodes**

```json
{
  "V01": "tests/hermes_cli/test_hades_verification_contract.py::test_work_and_result_match_openapi_and_root_schema",
  "V02": "backend/tests/Integration/Hades/VerificationQueueReconciliationTest.php::same_target_dedupes_new_version_stales",
  "V03": "backend/tests/Feature/Hades/VerificationWorkItemApiTest.php::claim_and_complete_enforce_project_binding_device_capability_kind",
  "V04": [
    "backend/tests/Feature/Hades/VerificationWorkItemApiTest.php::lease_heartbeat_expiry_reclaim_and_ten_execution_ceiling",
    "backend/tests/Feature/Hades/VerificationCompletionTest.php::authorized_identical_completion_is_idempotent_and_conflicting_digest_is_rejected",
    "tests/hermes_cli/test_hades_verification_tasks.py::test_out_of_order_lease_transitions_never_regress_cache"
  ],
  "V05": "tests/hermes_cli/test_hades_verification_worker.py::test_free_prose_cannot_complete",
  "V06": [
    "backend/tests/Feature/Hades/VerificationCompletionTest.php::stale_target_applies_nothing",
    "tests/hermes_cli/test_hades_verification_worker.py::test_stale_completion_cas_sends_no_second_failure_completion"
  ],
  "V07": "backend/tests/Integration/Hades/VerificationQueueReconciliationTest.php::deferred_is_terminal_and_quiet",
  "V08": "backend/tests/Feature/Hades/WikiVerificationQueueTest.php::wiki_completion_allocates_revision_and_recomputes_full_ledger",
  "V09": "backend/tests/Integration/Hades/GraphVerificationOverlayTest.php::graph_verdict_creates_immutable_versioned_overlay",
  "V10": "tests/agent/test_hades_backend_conversation_sync.py::test_sync_reports_counts_without_claim_model_or_prompt_mutation",
  "V11": [
    "tests/hermes_cli/test_hades_verification_worker.py::test_once_handles_one_and_all_pages_one_at_a_time",
    "tests/hermes_cli/test_hades_verification_worker.py::test_deadlines_require_meaningful_progress_and_forbid_same_run_reclaim",
    "tests/hermes_cli/test_hades_verification_worker.py::test_containment_and_server_fence_block_zombie_and_cross_host_reclaim"
  ],
  "V12": "tests/hermes_cli/test_hades_verification_worker.py::test_generic_worker_rejects_verification_payload_even_when_server_filter_fails",
  "V13": "tests/hermes_cli/test_hades_verification_tasks.py::test_cache_version_conflict_and_terminal_regression_rules",
  "V14": "tests/hermes_cli/test_hades_backend_status.py::test_status_and_quality_separate_work_domains",
  "V15": [
    "backend/tests/Feature/Hades/VerificationRetryTest.php::admin_retry_requires_current_target_and_preserves_terminal_predecessor",
    "backend/tests/Feature/Postgres/VerificationConcurrencyPostgresTest.php::concurrent_human_retry_creates_exactly_one_live_successor",
    "backend/tests/Feature/Postgres/VerificationConcurrencyPostgresTest.php::retry_reconciliation_cleanup_share_total_lock_order",
    "tests/hermes_cli/test_hades_backend_cmd.py::test_retry_requires_tty_confirmation_and_separate_human_admin_token"
  ],
  "V16": "backend/tests/Feature/Hades/WikiVerificationQueueTest.php::wiki_adversarial_result_preserves_markdown_and_rekeys_fingerprints",
  "V17": "backend/tests/Integration/Hades/GraphVerificationOverlayTest.php::server_derives_all_overlay_records_and_frontier_steps",
  "V18": "tests/hermes_cli/test_hades_wiki_bootstrap.py::test_empty_project_bootstrap_is_human_markdown_evidence_bounded_and_idempotent",
  "V19": [
    "/Users/gabriele/plugins/hades-backend/tests/test_plugin_contract.py::test_plugin_delegates_only_to_graph_v2_and_verification_cli",
    "/Users/gabriele/plugins/hades-backend/tests/test_plugin_contract.py::test_plugin_exposes_exactly_five_unique_skills",
    "/Users/gabriele/plugins/hades-backend/tests/test_plugin_contract.py::test_plugin_contains_no_http_v1_or_direct_wiki_implementation",
    ".codex-artifacts/graph-v2/codex-plugin-smoke.json#fresh_task_catalog_matches_plugin_sha_version_and_five_skills"
  ]
}
```

The gate report schema accepts a nonempty array for every gate (a legacy single string is normalized to a one-element array) and requires every named node/evidence assertion to exist and pass on the recorded SHA. A compound gate is red when any member is missing, skipped, stale, or failed; no session may collapse V04, V06, V15, or V19 back to one representative test.

Both reports are closed JSON objects with `additionalProperties:false`. The backend report has schema `hades.graph_v2_verification_backend_gates.v1`; the Agent report has schema `hades.graph_v2_verification_agent_gates.v1`. Each requires exactly `schema`, `subject_main_sha` (40 lower-case hex), `contract_lock_path`, `contract_lock_sha256`, `generated_at`, `commands`, `gates`, and `review`. Backend `contract_lock_path` is exactly `backend/resources/contracts/hades/graph-v2/contract-lock.json`; Agent is exactly `contracts/hades/graph-v2/contract-lock.json`. Commands are a nonempty ordered array of `{argv:[nonempty strings],exit_code,started_at,finished_at,stdout_sha256,stderr_sha256}` executed in a clean worktree whose HEAD remains `subject_main_sha`.

`gates` has exactly V01–V19 and each value is the canonical ordered nonempty array of **node records**, not one whole-gate status: `{node,owner:"backend|agent|plugin",status:"passed|not_owned",evidence_sha256}`. The backend report may pass only `backend` nodes. The Agent report may pass `agent|plugin` nodes because 16B verifies the separately versioned plugin SHA recorded in the same report; all other canonical nodes are present as `not_owned` with null digest. Thus compound V04/V06/V15/V19 split cleanly. `review` is exactly `{spec_status:"approved",quality_status:"approved",critical:0,important:0,reviewed_sha:subject_main_sha}`. The evidence-only commit is allowed to descend from the immutable subject; it does not change or claim to equal `subject_main_sha`. 16C requires both evidence commits descend from their declared subjects, requires identical canonical node arrays/order, and unions them so each node is `passed` by exactly one report and `not_owned` by the other.

- [ ] **Step 2: Run backend verification suite**

```bash
docker compose exec -T app php artisan test \
  tests/Feature/Hades/VerificationMigrationTest.php \
  tests/Integration/Hades/VerificationQueueReconciliationTest.php \
  tests/Feature/Hades/VerificationWorkItemApiTest.php \
  tests/Feature/Hades/VerificationCompletionTest.php \
  tests/Integration/Hades/GraphVerificationOverlayTest.php \
  tests/Feature/Hades/WikiVerificationQueueTest.php \
  tests/Feature/Hades/VerificationRetryTest.php \
  tests/Integration/Hades/VerificationRetentionTest.php

docker compose exec -T app composer test:postgres -- \
  tests/Feature/Postgres/VerificationConcurrencyPostgresTest.php
```

- [ ] **Step 3: Run agent verification suite**

```bash
.venv/bin/python -m pytest \
  tests/hermes_cli/test_hades_verification_contract.py \
  tests/hermes_cli/test_hades_verification_tasks.py \
  tests/hermes_cli/test_hades_verification_worker.py \
  tests/hermes_cli/test_hades_backend_cmd.py \
  tests/hermes_cli/test_hades_backend_sync_runner.py \
  tests/hermes_cli/test_hades_backend_status.py \
  tests/hermes_cli/test_hades_quality_report.py \
  tests/hermes_cli/test_hades_wiki_skills.py \
  tests/hermes_cli/test_hades_wiki_bootstrap.py \
  tests/hermes_cli/test_hades_backend_client.py \
  tests/agent/test_hades_backend_memory_provider.py \
  tests/agent/test_hades_backend_memory_provider_sync.py -q

cd /Users/gabriele/plugins/hades-backend
python3 -m pytest tests/test_plugin_contract.py -q
```

- [ ] **Step 4: Produce the Plan 6 smoke handoff; do not touch live data here**

Plan 3 closes hermetic contracts and real PostgreSQL concurrency only. After the backend 16A implementation/report subject and Agent/plugin implementation subjects are immutable, 16B-evidence records them in `.codex-artifacts/graph-v2/verification-live-smoke-handoff.json`. This handoff is a closed `hades.graph_v2_verification_live_smoke_handoff.v1` object containing `backend_subject_main_sha`, `agent_subject_main_sha`, separately versioned `plugin_sha`, contract-lock digest, fixture source path, fixture source-tree SHA-256, exact Agent index command argv, expected artifact schema/version, expected uncertainty public ID, expected reason code, and the SHA-256 of the generated deterministic bundle manifest. It never contains or predicts its own evidence commit SHA. The fixture is the reviewed tiny source tree `tests/fixtures/hades/graph_v2/verification_uncertainty_project/`; 16B-implementation creates it only if absent, commits/integrates it before evidence generation, and its test must generate a valid bundle containing exactly the recorded uncertainty. Plan 6 refuses any path/digest/ID mismatch and never substitutes synthetic database rows. The remote disposable project/binding, temporary Mac `HERMES_HOME`, token lifecycle, graph+Wiki queue execution, and teardown belong exclusively to Plan 6 after its isolated acceptance harness exists. Never improvise this smoke against project `01KX8G47N6HK2AC4NSVS912ECJ`, Carnovali, the production PostgreSQL database, or a user's normal Hades profile.

- [ ] **Step 5: Integrate implementation tests first; then record and commit evidence separately**

Backend 16A-implementation stages only this explicit test allowlist (any changed path outside it stops for plan amendment), commits/reviews, and is integrated before the report run:

```bash
git add -- backend/tests/Feature/Hades/VerificationMigrationTest.php backend/tests/Integration/Hades/VerificationQueueReconciliationTest.php backend/tests/Feature/Hades/VerificationWorkItemApiTest.php backend/tests/Feature/Hades/VerificationCompletionTest.php backend/tests/Integration/Hades/GraphVerificationOverlayTest.php backend/tests/Feature/Hades/WikiVerificationQueueTest.php backend/tests/Feature/Hades/VerificationRetryTest.php backend/tests/Integration/Hades/VerificationRetentionTest.php backend/tests/Feature/Postgres/VerificationConcurrencyPostgresTest.php
git diff --cached --name-only
git commit -m "test(verification): complete backend gate coverage"
```

Agent 16B-implementation stages only this explicit test/fixture allowlist; the standalone plugin remains separately versioned and is never staged here:

```bash
git add -- tests/hermes_cli/test_hades_verification_contract.py tests/hermes_cli/test_hades_verification_tasks.py tests/hermes_cli/test_hades_verification_worker.py tests/hermes_cli/test_hades_backend_cmd.py tests/hermes_cli/test_hades_backend_sync_runner.py tests/hermes_cli/test_hades_backend_status.py tests/hermes_cli/test_hades_quality_report.py tests/hermes_cli/test_hades_wiki_skills.py tests/hermes_cli/test_hades_wiki_bootstrap.py tests/hermes_cli/test_hades_backend_client.py tests/agent/test_hades_backend_memory_provider.py tests/agent/test_hades_backend_memory_provider_sync.py tests/fixtures/hades/graph_v2/verification_uncertainty_project/app.py
git diff --cached --name-only
git commit -m "test(hades): complete verification gate coverage"
```

After those commits are integrated, 16A-evidence stages exactly `.codex-artifacts/graph-v2/verification-backend-gates.json` and commits `test(verification): record backend verification gates`; 16B-evidence stages exactly `.codex-artifacts/graph-v2/verification-agent-gates.json` and `.codex-artifacts/graph-v2/verification-live-smoke-handoff.json`, then commits `test(hades): record verification gate evidence`. Each runs `git diff --cached --name-only` and requires the exact evidence allowlist. 16C verifies both committed report bytes from their respective subject SHAs, recomputes contract/fixture/handoff digests, and records no Git change. If a report, test node, fixture, or digest is stale, it returns the defect to a new implementation/evidence cycle; it does not patch either report in place.

## Plan 3 Exit Gate

- V01–V19 are green.
- A delayed/superseded projection event reconciles only the reread current head, and duplicate delivery coalesces.
- Verification request/result/overlay and Wiki ledger/evidence providers retain exactly their imports; each concurrent reference-vs-cleanup test aborts deletion safely.
- Verification audit cleanup preserves live/younger/externally reachable chains, removes only complete expired self-contained chains in the specified order, and safely rechecks newly eligible artifacts.
- A generic Kanban worker cannot claim verification even with a misbehaving backend filter.
- A stale/lost-lease worker applies no domain effect.
- Graph base artifact remains byte-identical after verified and contradicted results.
- New Wiki revision fingerprints validate against the new revision ID.
- Sync has no model/prompt/message side effect.
- Fresh reviews of both repositories contain no unresolved Critical/Important finding.
