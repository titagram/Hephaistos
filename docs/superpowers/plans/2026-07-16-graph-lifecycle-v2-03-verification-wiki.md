# Hades Graph and Wiki Verification Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn graph uncertainty and Wiki `needs_verification` revisions into project-scoped leased work that Hades Agent resolves one item at a time with structured, auditable, version-safe results.

**Architecture:** PostgreSQL verification requests own target/version/dedupe state; generic work items supply the existing lease transport; domain completion applies graph overlays or immutable Wiki revisions in the same transaction as terminal work completion. Local sync caches counts only. Explicit CLI commands and skills claim one current item, gather read-only evidence, validate the result schema, heartbeat, and complete atomically.

**Tech Stack:** Laravel/PHP/Pest, PostgreSQL JSONB/ULIDs/row locks, Python 3.11+/pytest/SQLite, Hades CLI and skills, JCS/SHA-256, existing Wiki revision model.

## Global Constraints

- Inherit `2026-07-16-graph-lifecycle-v2-master.md`.
- Backend edits use `/home/ubuntu/dev-sandbox` branch `codex/graph-lifecycle-v2-backend`; agent edits use `/Users/gabriele/Dev/Hephaistos` branch `codex/graph-lifecycle-v2-agent`.
- START GATE: Plan 2 contracts, graph import, projection head, signed context, and query DTOs are green.
- Commit backend and agent changes separately even when one task describes an end-to-end interface.
- Generic Kanban/task workers exclude verification twice: server filter and local discriminator check.
- Graph base artifacts are immutable. Verified/contradicted graph facts create append-only version-bound overlays.
- Wiki verification never edits claim text. It creates a new immutable revision and recomputes fingerprints with the new revision ULID.
- `deferred` is completed and quiet for that target/source version.
- Free prose cannot complete a verification item.
- Automated claim/complete always requires agent/device capability plus project and binding checks; an admin user token does not erase those automated checks.

---

## File Structure

### Backend

- Migrations/models: work-item extension, verification requests, overlays.
- Services: reconciliation, completion, overlay derivation, Wiki application.
- List/claim/heartbeat/complete/retry API: existing `AgentWorkItemController` plus domain services.
- Listeners/jobs: graph publication and Wiki revision CAS enqueue coalesced reconciliation.

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
- Create: migration `2026_07_16_000600_create_graph_verification_overlays_table.php`
- Create: `backend/app/Models/VerificationRequest.php`
- Create: `backend/app/Models/GraphVerificationOverlay.php`
- Modify: `backend/app/Models/AgentWorkItem.php`
- Test: `backend/tests/Feature/Hades/VerificationMigrationTest.php`

**Interfaces:**
- Columns, indexes, statuses, FKs, retry links, and retention restrictions are exactly section 9.1.
- Every committed status/claim/lease/result/terminal mutation increments `state_version` once.

- [ ] **Step 1: Add RED migration/state tests**

```php
it('increments state version exactly once for a committed transition', function () {
    $item = AgentWorkItem::factory()->verification()->queued()->create(['state_version' => 1]);
    $service = app(AgentWorkItemLeaseService::class);
    $claimed = $service->claim($item, registeredVerificationAgent());
    expect($claimed->state_version)->toBe(2);
    expect($claimed->fresh()->state_version)->toBe(2);
});
```

Also test status union, partial dedupe uniqueness, request generation uniqueness, overlay append-only uniqueness, RESTRICT cleanup order, and generic historical statuses unchanged.

- [ ] **Step 2: Run RED**

Run: `docker compose exec -T app php artisan test backend/tests/Feature/Hades/VerificationMigrationTest.php`

- [ ] **Step 3: Implement migrations/models and transition helper**

All state changes go through one locked mutation helper that compares expected state/version, applies fields, increments once, and records the audit event in the same transaction.

- [ ] **Step 4: Run GREEN and commit backend**

```bash
docker compose exec -T app php artisan test backend/tests/Feature/Hades/VerificationMigrationTest.php
git add backend/database/migrations/2026_07_16_000400_extend_agent_work_items_for_verification.php backend/database/migrations/2026_07_16_000500_create_verification_requests_table.php backend/database/migrations/2026_07_16_000600_create_graph_verification_overlays_table.php backend/app/Models/VerificationRequest.php backend/app/Models/GraphVerificationOverlay.php backend/app/Models/AgentWorkItem.php backend/tests/Feature/Hades/VerificationMigrationTest.php
git commit -m "feat(verification): add versioned verification storage"
```

### Task 2: Reconcile Current Graph and Wiki Uncertainty into Work

**Backend files:**
- Create: `backend/app/Services/Hades/VerificationQueueService.php`
- Create: `backend/app/Jobs/ReconcileVerificationQueue.php`
- Create: `backend/app/Listeners/QueueGraphVerificationRequests.php`
- Create: `backend/app/Listeners/QueueWikiVerificationRequest.php`
- Create: `backend/app/Console/Commands/ReconcileVerificationQueue.php`
- Modify: `backend/app/Providers/EventServiceProvider.php`
- Modify: `backend/routes/console.php`
- Test: `backend/tests/Integration/Hades/VerificationQueueReconciliationTest.php`

**Interfaces:**

```php
final class VerificationQueueService
{
    public function reconcile(VerificationScope $scope): VerificationSummary;
    public function summary(Project $project, ?WorkspaceBinding $binding): VerificationSummary;
}
```

- [ ] **Step 1: Add RED reconciliation tests**

Test one item per exact graph assertion/artifact and Wiki page/revision/fingerprint; same target idempotency; new version stales old live item and creates generation 1; exact A→B→A reactivation creates one linked next generation; deferred does not requeue; no ledger creates diagnostic only; 81–512 Wiki claims creates one `page_requires_split` deferred diagnostic; project/binding/domain filters; concurrent listener/command coalesces under unique key.

- [ ] **Step 2: Run RED**

Run: `docker compose exec -T app php artisan test backend/tests/Integration/Hades/VerificationQueueReconciliationTest.php`

- [ ] **Step 3: Implement locked chain reconciliation**

Compute dedupe/fingerprints using the root contract. Lock the exact request chain. Create work payload only from server-owned artifact/ledger records. Dispatch listeners only after projection-head or Wiki current-revision CAS commit. Schedule reconciliation every five minutes with `withoutOverlapping()`/one server.

- [ ] **Step 4: Run GREEN and commit backend**

```bash
docker compose exec -T app php artisan test backend/tests/Integration/Hades/VerificationQueueReconciliationTest.php
git add backend/app/Services/Hades/VerificationQueueService.php backend/app/Jobs/ReconcileVerificationQueue.php backend/app/Listeners/QueueGraphVerificationRequests.php backend/app/Listeners/QueueWikiVerificationRequest.php backend/app/Console/Commands/ReconcileVerificationQueue.php backend/app/Providers/EventServiceProvider.php backend/routes/console.php backend/tests/Integration/Hades/VerificationQueueReconciliationTest.php
git commit -m "feat(verification): reconcile graph and wiki uncertainty"
```

### Task 3: Extend Work APIs with Verification Filters and Lease Rules

**Backend files:**
- Modify: `backend/app/Http/Controllers/Plugin/AgentWorkItemController.php`
- Modify: work-item request/resources/services
- Test: `backend/tests/Feature/Hades/VerificationWorkItemApiTest.php`

**Interfaces:**
- List/claim accepts `work_kind`, project/repository, binding, priority, status, cursor, limit.
- DTO includes `state_version` and `remote_updated_at`.
- Claim/heartbeat validates work kind, agent/device, project membership, binding, capability, and current target.
- Heartbeat every 30s; before evidence operation when remaining lease <15s.

- [ ] **Step 1: Add RED authorization/lease tests**

Cover cross-project, cross-binding, wrong device, missing `verify_project_graph`/`verify_project_wiki`, wrong kind, stale target, state-version CAS, lease expiry event and reclaimed queued state, heartbeat rejection, idempotent claim, and generic worker request unable to receive verification items.

- [ ] **Step 2: Run RED**

Run: `docker compose exec -T app php artisan test backend/tests/Feature/Hades/VerificationWorkItemApiTest.php`

- [ ] **Step 3: Implement filtered DTO/API behavior**

Do not add a persisted `expired` status. Reclaim clears owner/lease, increments version, records `lease_expired`, and keeps attempt generation. Terminal rows never regress.

- [ ] **Step 4: Run GREEN and commit backend**

```bash
docker compose exec -T app php artisan test backend/tests/Feature/Hades/VerificationWorkItemApiTest.php
git add backend/app/Http/Controllers/Plugin/AgentWorkItemController.php backend/app/Http backend/app/Services/Hades backend/tests/Feature/Hades/VerificationWorkItemApiTest.php
git commit -m "feat(verification): expose scoped leased verification work"
```

### Task 4: Validate Structured Results and Complete Atomically

**Backend files:**
- Create: `backend/app/Services/Hades/VerificationCompletionService.php`
- Test: `backend/tests/Feature/Hades/VerificationCompletionTest.php`

**Interfaces:**

```php
final class VerificationCompletionService
{
    public function complete(AgentWorkItem $item, HadesAgent $agent, array $submittedResult): VerificationCompletion;
}
```

- [ ] **Step 1: Add RED result/CAS/idempotency tests**

Test schema discriminator, `verified|contradicted|deferred`, no unknown/free-form field, 1MiB result ceiling, evidence sort/dedupe, result digest before JSONB, claimed/running first completion only, completed same digest returns 200 after lease expiry, different digest 409, queued/failed/canceled/stale rejection, stale artifact/revision writes no domain effect, one transaction stores semantically equal JSONB/digest in both rows, and rollback leaves both live.

- [ ] **Step 2: Run RED**

Run: `docker compose exec -T app php artisan test backend/tests/Feature/Hades/VerificationCompletionTest.php`

- [ ] **Step 3: Implement canonical completion transaction**

Canonicalize result, derive digest, lock item/request, handle completed replay before lease check, recheck authorization/current target, delegate domain effect, write both results/digests/resolution/audit, increment work state version, and commit once.

- [ ] **Step 4: Run GREEN and commit backend**

```bash
docker compose exec -T app php artisan test backend/tests/Feature/Hades/VerificationCompletionTest.php
git add backend/app/Services/Hades/VerificationCompletionService.php backend/tests/Feature/Hades/VerificationCompletionTest.php
git commit -m "feat(verification): complete structured results atomically"
```

### Task 5: Derive Immutable Graph Verification Overlays

**Backend files:**
- Create: `backend/app/Services/Hades/GraphVerificationOverlayService.php`
- Modify: `backend/app/Services/Graph/V2/GraphV2LifecycleProjectionMapper.php`
- Test: `backend/tests/Integration/Hades/GraphVerificationOverlayTest.php`

**Interfaces:**

```php
final class GraphVerificationOverlayService
{
    public function derive(VerificationRequest $request, array $canonicalResult): ?GraphVerificationOverlay;
    public function verificationSetHash(HadesGraphImport $import): string;
}
```

- [ ] **Step 1: Add RED operation/adversarial tests**

Test `resolve_candidate_set`, `resolve_call_targets`, `resolve_edge_targets`, `reject_unresolved_subject`; server-derived node/edge/structure IDs only; exact subject ownership; entrypoint effective handler view; complete-candidate structure behavior; assertion-exclusive unknown boundary suppression; no arbitrary injected record; deferred no overlay; overlay changes projection version; contradiction disappears only in new effective projection; audit/base immutable; frontier step preserved.

- [ ] **Step 2: Run RED**

Run: `docker compose exec -T app php artisan test backend/tests/Integration/Hades/GraphVerificationOverlayTest.php`

- [ ] **Step 3: Implement exact overlay derivation**

Stored JSON has exactly the fields in section 11.5. Recompute closures and effective IDs from retained immutable artifact bytes/index; do not trust worker-supplied IDs beyond the allowed resolution selections. Sort arrays and overlay set before JCS hash. Dispatch one coalesced projection after commit.

- [ ] **Step 4: Run GREEN and commit backend**

```bash
docker compose exec -T app php artisan test backend/tests/Integration/Hades/GraphVerificationOverlayTest.php
git add backend/app/Services/Hades/GraphVerificationOverlayService.php backend/app/Services/Graph/V2/GraphV2LifecycleProjectionMapper.php backend/tests/Integration/Hades/GraphVerificationOverlayTest.php
git commit -m "feat(verification): apply immutable graph evidence overlays"
```

### Task 6: Make Wiki Claim Ledgers and Results Revision-Safe

**Backend files:**
- Modify: Wiki revision migration/model if ledger storage is missing
- Modify: `backend/app/Services/Hades/WikiVerificationService.php`
- Modify: `backend/app/Services/Hades/WikiVerificationEvidencePolicy.php`
- Modify: Wiki push/show request/resources
- Test: `backend/tests/Feature/Hades/WikiVerificationQueueTest.php`

**Interfaces:**
- Generated push requires `claim_locators:[{byte_start,byte_end,ordinal}]`.
- Backend derives `normalized_text` and `fingerprint=SHA256(JCS({page_id,revision_id,ordinal,byte_start,byte_end,normalized_text}))` after allocating revision ID.
- Result ledger size is 1–80 and must exactly match the claimed ledger.

- [ ] **Step 1: Add RED multibyte/immutability tests**

Test UTF-8 byte offsets, sorted/non-overlap locators, client fingerprint/text rejection, content mismatch, zero claims omitted, >80 generated claims split/omit, 81–512 human claims deferred diagnostic, full-content gate, stale revision CAS, exact supported/contradicted classification set, verified-all-supported, contradicted-at-least-one, unclassifiable-deferred, and no content edits.

- [ ] **Step 2: Add RED new-revision fingerprint tests**

```php
it('recomputes every claim fingerprint against the new verification revision id', function () {
    $completion = completeWikiVerification($this->supportedResult());
    foreach ($completion->revision->claims as $claim) {
        expect($claim->fingerprint)->toBe(hashJcs([
            'page_id' => $completion->revision->page_id,
            'revision_id' => $completion->revision->id,
            'ordinal' => $claim->ordinal,
            'byte_start' => $claim->byte_start,
            'byte_end' => $claim->byte_end,
            'normalized_text' => $claim->normalized_text,
        ]));
    }
});
```

- [ ] **Step 3: Run RED**

Run: `docker compose exec -T app php artisan test backend/tests/Feature/Hades/WikiVerificationQueueTest.php`

- [ ] **Step 4: Implement immutable Wiki application**

Lock page/current revision, allocate new ULID, copy exact Markdown/digest/locators/text, recompute all fingerprints, translate classifications by `(ordinal,byte_start,byte_end,normalized_text)`, store metadata/evidence, and CAS current revision in the surrounding verification completion transaction.

- [ ] **Step 5: Run GREEN and commit backend**

```bash
docker compose exec -T app php artisan test backend/tests/Feature/Hades/WikiVerificationQueueTest.php
git add backend/app/Services/Hades/WikiVerificationService.php backend/app/Services/Hades/WikiVerificationEvidencePolicy.php backend/app/Models backend/app/Http backend/tests/Feature/Hades/WikiVerificationQueueTest.php
git commit -m "feat(wiki): verify immutable claim ledgers through queue"
```

### Task 7: Add Human Admin Retry without Duplicating Live Work

**Backend files:**
- Modify: `backend/app/Http/Controllers/Plugin/AgentWorkItemController.php`
- Modify: `backend/routes/api.php`
- Modify: `backend/app/Services/Hades/VerificationQueueService.php`
- Test: `backend/tests/Feature/Hades/VerificationRetryTest.php`

**Interfaces:**
- Endpoint: `POST /api/hades/v1/agent-work-items/{item}/retry-verification`.
- Requires authenticated human project admin and 1–500-byte reason.
- Eligible selected item: failed/canceled or completed+deferred; target/source still current.

- [ ] **Step 1: Add RED chain/race tests**

Test agent/device token rejection, non-admin, stale selected, selected-not-latest, existing live conflict, latest eligible creates generation+1 with both retry links, A→B→A coalescence, race with reconciliation creates exactly one successor, terminal predecessor unchanged, audit reason/selected ancestor.

- [ ] **Step 2: Run RED**

Run: `docker compose exec -T app php artisan test backend/tests/Feature/Hades/VerificationRetryTest.php`

- [ ] **Step 3: Implement locked retry algorithm, run GREEN, commit backend**

```bash
docker compose exec -T app php artisan test backend/tests/Feature/Hades/VerificationRetryTest.php
git add backend/app/Http/Controllers/Plugin/AgentWorkItemController.php backend/routes/api.php backend/app/Services/Hades/VerificationQueueService.php backend/tests/Feature/Hades/VerificationRetryTest.php
git commit -m "feat(verification): add admin-controlled retry generations"
```

### Task 8: Implement Agent Verification Contract, Cache, and Client

**Agent files:**
- Create: `hermes_cli/hades_verification_contract.py`
- Create: `hermes_cli/hades_verification_tasks.py`
- Modify: `hermes_cli/hades_plugin_work_items_client.py`
- Modify: `hermes_cli/hades_backend_db.py`
- Test: `tests/hermes_cli/test_hades_verification_contract.py`
- Test: `tests/hermes_cli/test_hades_verification_tasks.py`

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

- [ ] **Step 1: Add RED contract/cache ordering tests**

Test root schemas/goldens, lower remote version ignored, equal semantic match accepted, equal conflict logged/ignored, greater valid transition, live→queued reclaim only with cleared claim/lease, terminal never regresses, out-of-order list around claim/heartbeat/completion, new retry row accepted, and cached remote fields round-trip.

- [ ] **Step 2: Run RED**

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_verification_contract.py tests/hermes_cli/test_hades_verification_tasks.py -q`

- [ ] **Step 3: Implement typed contract/client/cache**

Add SQLite fields `workspace_binding_id,subject_type,subject_id,subject_version,deduplication_key,priority,lease_expires_at,result_json,remote_state_version,remote_updated_at`. Apply the transition table before mutation; never overwrite terminal cache with an older/equal conflict.

- [ ] **Step 4: Run GREEN and commit agent**

```bash
.venv/bin/python -m pytest tests/hermes_cli/test_hades_verification_contract.py tests/hermes_cli/test_hades_verification_tasks.py -q
git add hermes_cli/hades_verification_contract.py hermes_cli/hades_verification_tasks.py hermes_cli/hades_plugin_work_items_client.py hermes_cli/hades_backend_db.py tests/hermes_cli/test_hades_verification_contract.py tests/hermes_cli/test_hades_verification_tasks.py
git commit -m "feat(hades): cache and inspect verification work safely"
```

### Task 9: Implement One-Item Verification Worker and CLI Commands

**Agent files:**
- Create: `hermes_cli/hades_verification_worker.py`
- Modify: `hermes_cli/hades_plugin_worker.py`
- Modify: `hermes_cli/hades_plugin_tasks.py`
- Modify: `hermes_cli/hades_backend_cmd.py`
- Modify: `hermes_cli/hades_backend_runtime.py`
- Modify: `hermes_cli/commands.py`
- Test: `tests/hermes_cli/test_hades_verification_worker.py`
- Test: `tests/hermes_cli/test_hades_backend_cmd.py`

**Interfaces:**

```python
class VerificationWorker:
    def work_once(self, filters: VerificationFilters) -> VerificationWorkOutcome: ...
    def work_all(self, filters: VerificationFilters) -> VerificationBatchOutcome: ...
```

- [ ] **Step 1: Add RED worker tests**

Test `--once` at most one, `--all` cursor pages but one claim at a time, target recheck before claim, heartbeat 30s/<15s rule, lost lease abort/no completion, specialist returns only schema result, result validation before complete, stale CAS does not submit second failure completion, generic worker excludes verification even if backend ignores filter, Wiki full read snapshot matching, and source read-only behavior.

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

- [ ] **Step 4: Run GREEN and commit agent**

```bash
.venv/bin/python -m pytest tests/hermes_cli/test_hades_verification_worker.py tests/hermes_cli/test_hades_backend_cmd.py -q
git add hermes_cli/hades_verification_worker.py hermes_cli/hades_plugin_worker.py hermes_cli/hades_plugin_tasks.py hermes_cli/hades_backend_cmd.py hermes_cli/hades_backend_runtime.py hermes_cli/commands.py tests/hermes_cli/test_hades_verification_worker.py tests/hermes_cli/test_hades_backend_cmd.py
git commit -m "feat(hades): process verification work one item at a time"
```

### Task 10: Keep Sync Read-Only and Separate Status/Quality Domains

**Agent files:**
- Modify: `hermes_cli/hades_backend_sync.py`
- Modify: `hermes_cli/hades_backend_status.py`
- Modify: `hermes_cli/hades_quality_report.py`
- Test: `tests/hermes_cli/test_hades_backend_sync_runner.py`
- Test: `tests/hermes_cli/test_hades_backend_status.py`
- Test: `tests/hermes_cli/test_hades_quality_report.py`
- Test: `tests/agent/test_hades_backend_conversation_sync.py`

- [ ] **Step 1: Add RED non-action tests**

Assert sync requests only `{verification_queued,verification_high_priority,verification_by_domain}`, caches/displays next command, never lists payload, claims, runs model, changes system prompt, or adds synthetic message; summary failure is warning/fail-open. Status has separate `task_work` and `verification_work`; pending verification is actionable not degraded. Quality excludes verification from Kanban shared-memory metrics.

- [ ] **Step 2: Run RED**

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_backend_sync_runner.py tests/hermes_cli/test_hades_backend_status.py tests/hermes_cli/test_hades_quality_report.py tests/agent/test_hades_backend_conversation_sync.py -k verification -q`

- [ ] **Step 3: Implement, run GREEN, commit agent**

```bash
.venv/bin/python -m pytest tests/hermes_cli/test_hades_backend_sync_runner.py tests/hermes_cli/test_hades_backend_status.py tests/hermes_cli/test_hades_quality_report.py tests/agent/test_hades_backend_conversation_sync.py -k verification -q
git add hermes_cli/hades_backend_sync.py hermes_cli/hades_backend_status.py hermes_cli/hades_quality_report.py tests/hermes_cli/test_hades_backend_sync_runner.py tests/hermes_cli/test_hades_backend_status.py tests/hermes_cli/test_hades_quality_report.py tests/agent/test_hades_backend_conversation_sync.py
git commit -m "feat(hades): surface verification without sync side effects"
```

### Task 11: Add Verification Orchestrator and Specialist Skills

**Agent files:**
- Create: `skills/autonomous-ai-agents/hades-verify/SKILL.md`
- Create: `skills/autonomous-ai-agents/hades-verify/agents/openai.yaml`
- Create: `skills/autonomous-ai-agents/hades-graph-verify/SKILL.md`
- Create: `skills/autonomous-ai-agents/hades-graph-verify/agents/openai.yaml`
- Modify: installed/source `hades-wiki-verify` skill
- Test: `tests/hermes_cli/test_hades_wiki_skills.py`

**Interfaces:**
- `hades-verify`: list → choose one → delegate by domain → validate → complete.
- `hades-graph-verify`: read-only source/backend evidence → structured result.
- `hades-wiki-verify`: routes to queue worker; no direct mutation in worker path.

- [ ] **Step 1: Read `skill-creator`/`writing-skills` before editing skills**

The executor must invoke the available skill-authoring instructions and read each selected `SKILL.md` completely. This step is mandatory because these files define agent behavior.

- [ ] **Step 2: Add RED skill routing and safety tests**

Test orchestrator routes exact domain, specialists cannot write source, same run cannot certify without independent reread, Wiki worker full snapshot gate, no legacy direct verify, exact result-only output, and unavailable/ambiguous evidence produces `deferred`.

- [ ] **Step 3: Run RED**

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_wiki_skills.py -q`

- [ ] **Step 4: Implement skills and run GREEN**

Each skill frontmatter description stays within the product limit; instructions explicitly say only the parent/orchestrator commands its leaf, source work is read-only, exact result JSON is required, and an unavailable/ambiguous target returns `deferred` rather than a guess.

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_wiki_skills.py -q`

- [ ] **Step 5: Commit agent**

```bash
git add skills/autonomous-ai-agents/hades-verify skills/autonomous-ai-agents/hades-graph-verify skills tests/hermes_cli/test_hades_wiki_skills.py
git commit -m "feat(hades): verify graph and wiki evidence through skills"
```

### Task 12: Bootstrap Human-Readable Wiki Pages from Current Evidence

**Agent files:**
- Modify: installed/source `hades-wiki-push` skill
- Modify: Wiki push implementation/client
- Test: `tests/hermes_cli/test_hades_wiki_bootstrap.py`

**Interfaces:**
- Empty-project page set is fixed: Overview, Architecture, Entrypoints and lifecycles, Domain and data, External integrations, Operations.
- Current graph v2 is read first; current project memory/Wiki supplies terminology only; vector search locates but never certifies evidence.
- Visible body is human Markdown; fingerprints/handles/snapshot/ledger/generator metadata remain revision JSON.

- [ ] **Step 1: Add RED bootstrap tests**

Test current v2 graph first, human title/purpose/prose/tables/lists/source-relative links, machine metadata absent from Markdown, stable slug/revision CAS, rerun no duplicate title, developer-provided page not overwritten, no evidence no page, zero claims omitted, generated page <=80 claims, exact byte locators sent, and every generated claim queues verification.

- [ ] **Step 2: Run RED**

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_wiki_bootstrap.py -q`

- [ ] **Step 3: Implement bootstrap through existing push path**

Do not create a second generator. Build final NFC Markdown first, identify factual byte spans, split or omit before the 80-claim ceiling, send only locators, and let the backend allocate revision/fingerprints. Pages without evidence or factual claims are absent rather than speculative.

- [ ] **Step 4: Run GREEN and commit agent**

```bash
.venv/bin/python -m pytest tests/hermes_cli/test_hades_wiki_bootstrap.py -q
git add skills hermes_cli tests/hermes_cli/test_hades_wiki_bootstrap.py
git commit -m "feat(hades): bootstrap human-readable project wiki pages"
```

### Task 13: Synchronize OpenAPI and Hades Operator Documentation

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
- Documentation states v2-only graph, workspace-binding scope, source identity, completeness/evidence/lifecycle, verification queue, vector-as-candidate role, chunk resume, projection reconciliation, backup, scoped retirement, and rollback.

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

Also extract every documented `hades backend verification`, graph-v2 Artisan, backup, restore, and retirement command; compare command names/options with registries/route definitions.

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

### Task 14: Close V01–V18 End-to-End

**Backend/Agent files:**
- Modify: verification tests only for missing gate coverage
- Create backend report: `.codex-artifacts/graph-v2/verification-backend-gates.json`
- Create agent report: `.codex-artifacts/graph-v2/verification-agent-gates.json`

- [ ] **Step 1: Map every verification gate to an exact test node**

```json
{
  "V01": "tests/hermes_cli/test_hades_verification_contract.py::test_work_and_result_match_openapi_and_root_schema",
  "V02": "backend/tests/Integration/Hades/VerificationQueueReconciliationTest.php::same_target_dedupes_new_version_stales",
  "V03": "backend/tests/Feature/Hades/VerificationWorkItemApiTest.php::claim_and_complete_enforce_project_binding_device_capability_kind",
  "V04": "tests/hermes_cli/test_hades_verification_tasks.py::test_out_of_order_lease_transitions_never_regress_cache",
  "V05": "tests/hermes_cli/test_hades_verification_worker.py::test_free_prose_cannot_complete",
  "V06": "backend/tests/Feature/Hades/VerificationCompletionTest.php::stale_target_applies_nothing",
  "V07": "backend/tests/Integration/Hades/VerificationQueueReconciliationTest.php::deferred_is_terminal_and_quiet",
  "V08": "backend/tests/Feature/Hades/WikiVerificationQueueTest.php::wiki_completion_allocates_revision_and_recomputes_full_ledger",
  "V09": "backend/tests/Integration/Hades/GraphVerificationOverlayTest.php::graph_verdict_creates_immutable_versioned_overlay",
  "V10": "tests/agent/test_hades_backend_conversation_sync.py::test_sync_reports_counts_without_claim_model_or_prompt_mutation",
  "V11": "tests/hermes_cli/test_hades_verification_worker.py::test_once_handles_one_and_all_pages_one_at_a_time",
  "V12": "tests/hermes_cli/test_hades_verification_worker.py::test_generic_worker_rejects_verification_payload_even_when_server_filter_fails",
  "V13": "tests/hermes_cli/test_hades_verification_tasks.py::test_cache_version_conflict_and_terminal_regression_rules",
  "V14": "tests/hermes_cli/test_hades_backend_status.py::test_status_and_quality_separate_work_domains",
  "V15": "backend/tests/Feature/Hades/VerificationRetryTest.php::admin_retry_creates_one_linked_generation",
  "V16": "backend/tests/Feature/Hades/WikiVerificationQueueTest.php::wiki_adversarial_result_preserves_markdown_and_rekeys_fingerprints",
  "V17": "backend/tests/Integration/Hades/GraphVerificationOverlayTest.php::server_derives_all_overlay_records_and_frontier_steps",
  "V18": "tests/hermes_cli/test_hades_wiki_bootstrap.py::test_empty_project_bootstrap_is_human_markdown_evidence_bounded_and_idempotent"
}
```

- [ ] **Step 2: Run backend verification suite**

```bash
docker compose exec -T app php artisan test \
  backend/tests/Feature/Hades/VerificationMigrationTest.php \
  backend/tests/Integration/Hades/VerificationQueueReconciliationTest.php \
  backend/tests/Feature/Hades/VerificationWorkItemApiTest.php \
  backend/tests/Feature/Hades/VerificationCompletionTest.php \
  backend/tests/Integration/Hades/GraphVerificationOverlayTest.php \
  backend/tests/Feature/Hades/WikiVerificationQueueTest.php \
  backend/tests/Feature/Hades/VerificationRetryTest.php
```

- [ ] **Step 3: Run agent verification suite**

```bash
.venv/bin/python -m pytest \
  tests/hermes_cli/test_hades_verification_contract.py \
  tests/hermes_cli/test_hades_verification_tasks.py \
  tests/hermes_cli/test_hades_verification_worker.py \
  tests/hermes_cli/test_hades_backend_sync_runner.py \
  tests/hermes_cli/test_hades_backend_status.py \
  tests/hermes_cli/test_hades_quality_report.py \
  tests/hermes_cli/test_hades_wiki_skills.py \
  tests/hermes_cli/test_hades_wiki_bootstrap.py -q
```

- [ ] **Step 4: Run real local-backend smoke**

With a disposable project/binding, queue one graph and one Wiki item. Run `sync` and prove counts only. Run `verification work --once --domain graph`, then Wiki, and inspect immutable overlay/new revision plus terminal audit.

- [ ] **Step 5: Review, record, commit reports separately**

Backend commit message: `test(verification): close backend verification gates`.

Agent commit message: `test(hades): close verification worker gates`.

## Plan 3 Exit Gate

- V01–V18 are green.
- A generic Kanban worker cannot claim verification even with a misbehaving backend filter.
- A stale/lost-lease worker applies no domain effect.
- Graph base artifact remains byte-identical after verified and contradicted results.
- New Wiki revision fingerprints validate against the new revision ID.
- Sync has no model/prompt/message side effect.
- Fresh reviews of both repositories contain no unresolved P0/P1 finding.
