# Canonical Graph Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unify Hades and legacy Graphify graph artifacts behind one canonical contract, persist and rebuild Neo4j projection state, and expose one source-aware graph read path without redesigning the frontend yet.

**Architecture:** Hades native indexers and the legacy analyzer retain their extraction logic but both attach `hades.graph_artifact.v1` metadata. Laravel normalizes either payload, selects graphs by explicit source scope, persists projection lifecycle, and projects verified versions into Neo4j. Existing plugin, Hades, and dashboard graph reads delegate to the shared foundation while preserving their public contracts.

**Tech Stack:** Python 3.11+, pytest, Laravel/PHP 8.3, PostgreSQL 16, Pest, Laravel queues, Laudis Neo4j client, Neo4j 5.26, Docker Compose.

## Global Constraints

- Use isolated worktrees at execution time; never implement on a dirty checkout.
- `/Users/gabriele/Dev/Hephaistos` and `/home/ubuntu/dev-sandbox` are separate Git repositories and require separate commits.
- Preserve `hades.php_graph.v1` and `hades.code_graph.v1`; canonical metadata is additive.
- Canonical artifacts are authoritative; Neo4j is a rebuildable projection.
- Every extraction fallback has a bounded machine-readable reason.
- Selection always includes project plus exact `workspace_binding` or `repository` scope.
- Keep the previous ready projection current until its replacement verifies.
- Do not add a model tool or implement the graph UI, wiki, Platon, tenancy, or backup here.
- Preserve current plugin and Hades response envelopes; additions must be backward compatible.
- Every task follows RED -> GREEN -> focused regression -> commit.
- Never log tokens, passwords, cookies, raw exception messages, or absolute workspace paths.

## File Map

### Hades repository

- Create `hermes_cli/hades_graph_contract.py`.
- Modify `hermes_cli/hades_index/__init__.py`.
- Create `tests/hermes_cli/test_hades_graph_contract.py`.
- Modify `tests/hermes_cli/test_hades_backend_jobs.py`.
- Modify `docs/hades/backend.md`.

### DevBoard repository

- Modify `analyzer/src/devboard_analyzer/code_graph.py` and `analyzer/tests/test_code_graph.py`.
- Create `backend/app/Services/Graph/CanonicalGraphNormalizer.php`.
- Create `backend/app/Services/Graph/CanonicalGraphRepository.php`.
- Create `backend/app/Services/Graph/CanonicalGraphProjectionService.php`.
- Create `backend/app/Services/Graph/Neo4jCanonicalGraphProjector.php`.
- Create `backend/app/Services/Graph/CanonicalGraphQueryService.php`.
- Create `backend/app/Jobs/ProjectCanonicalGraphToNeo4j.php`.
- Create `backend/database/migrations/2026_07_11_000003_create_canonical_graph_projections_table.php`.
- Modify Hades artifact/traversal controllers, Genesis import, Neo4j rebuild, graph query compatibility, dashboard reader, console command, tests, OpenAPI fixture, and logbook as named in the tasks below.

---

### Task 1: Canonical Metadata On Native Hades Graphs

**Files:**
- Create: `hermes_cli/hades_graph_contract.py`
- Modify: `hermes_cli/hades_index/__init__.py`
- Test: `tests/hermes_cli/test_hades_graph_contract.py`
- Test: `tests/hermes_cli/test_hades_backend_jobs.py`

**Interfaces:**
- Produces `finalize_graph_artifact(graph, *, payload, candidates, omitted) -> dict`.
- Produces `graph_contract.version == "hades.graph_artifact.v1"`.

- [ ] **Step 1: Write the failing contract tests**

```python
from pathlib import Path

from hermes_cli.hades_graph_contract import finalize_graph_artifact


def test_finalize_graph_artifact_records_source_and_quality(tmp_path: Path):
    graph = {
        "schema": "hades.php_graph.v1",
        "language": "php",
        "symbols": [{"id": "class:A", "name": "A", "kind": "class", "path": "a.php"}],
        "edges": [{"id": "calls:1", "kind": "calls", "source": "class:A", "target": "class:B"}],
        "truncated": False,
    }
    result = finalize_graph_artifact(
        graph,
        payload={"head_commit": "abc123", "branch": "main"},
        candidates=[tmp_path / "a.php"],
        omitted=[],
    )
    assert result["graph_contract"] == {
        "version": "hades.graph_artifact.v1",
        "extractor": {"name": "hades-native-php", "version": "1", "mode": "native", "quality": "full", "fallback_reason": None},
        "coverage": {"languages": ["php"], "files_total": 1, "files_analyzed": 1, "files_failed": 0},
        "source": {"branch": "main", "head_commit": "abc123"},
    }
    assert result["head_commit"] == "abc123"


def test_finalize_graph_artifact_exposes_inventory_fallback(tmp_path: Path):
    result = finalize_graph_artifact(
        {"schema": "hades.code_graph.v1", "language": "typescript", "symbols": [], "edges": [], "truncated": True},
        payload={"workspace_head_commit": "def456"},
        candidates=[tmp_path / "app.ts"],
        omitted=[{"path": "large.ts", "reason": "max_file_bytes"}],
    )
    assert result["graph_contract"]["extractor"]["quality"] == "inventory_only"
    assert result["graph_contract"]["extractor"]["fallback_reason"] == "no_relationships_extracted"
    assert result["graph_contract"]["coverage"]["files_failed"] == 1
```

- [ ] **Step 2: Run RED**

```bash
source .venv/bin/activate
pytest -q tests/hermes_cli/test_hades_graph_contract.py
```

Expected: module import failure.

- [ ] **Step 3: Implement the finalizer**

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

GRAPH_CONTRACT_VERSION = "hades.graph_artifact.v1"


def finalize_graph_artifact(
    graph: dict[str, Any], *, payload: dict[str, Any], candidates: list[Path], omitted: list[dict[str, Any]]
) -> dict[str, Any]:
    language = str(graph.get("language") or "unknown").strip().lower() or "unknown"
    edges = graph.get("edges") if isinstance(graph.get("edges"), list) else graph.get("relationships", [])
    if not edges:
        quality, reason = "inventory_only", "no_relationships_extracted"
    elif bool(graph.get("truncated")) or omitted:
        quality, reason = "partial", "bounded_or_omitted_input"
    else:
        quality, reason = "full", None
    head = str(payload.get("head_commit") or payload.get("workspace_head_commit") or "").strip()
    branch = str(payload.get("branch") or payload.get("current_branch") or "").strip()
    graph["head_commit"] = head or None
    graph["workspace_head_commit"] = head or None
    graph["graph_contract"] = {
        "version": GRAPH_CONTRACT_VERSION,
        "extractor": {"name": f"hades-native-{language}", "version": "1", "mode": "native", "quality": quality, "fallback_reason": reason},
        "coverage": {"languages": [language], "files_total": len(candidates) + len(omitted), "files_analyzed": len(candidates), "files_failed": len(omitted)},
        "source": {"branch": branch or None, "head_commit": head or None},
    }
    return graph
```

At the end of `build_graph_for_workspace()` call this function after source-slice candidates are attached and remove the old bare `return graph`.

- [ ] **Step 4: Add job-level assertions**

In the existing PHP job fixture assert contract version, `hades-native-php`, and the expected head commit.

- [ ] **Step 5: Run GREEN and regressions**

```bash
pytest -q tests/hermes_cli/test_hades_graph_contract.py tests/hermes_cli/test_hades_backend_jobs.py tests/hermes_cli/test_hades_backend_sync_runner.py
ruff check hermes_cli/hades_graph_contract.py hermes_cli/hades_index/__init__.py tests/hermes_cli/test_hades_graph_contract.py
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add hermes_cli/hades_graph_contract.py hermes_cli/hades_index/__init__.py tests/hermes_cli/test_hades_graph_contract.py tests/hermes_cli/test_hades_backend_jobs.py
git commit -m "feat(hades): emit canonical graph metadata"
```

---

### Task 2: Observable Graphify Fallback

**Files:**
- Modify: `analyzer/src/devboard_analyzer/code_graph.py`
- Test: `analyzer/tests/test_code_graph.py`

**Interfaces:**
- Graphify success uses extractor `graphify`.
- Fallback reason is `graphify_unavailable` or `graphify_failed:<ExceptionClass>`.
- Raw exception messages are never serialized.

- [ ] **Step 1: Add failing tests**

```python
def test_graphify_unavailable_is_explicit(monkeypatch, tmp_path):
    source = tmp_path / "app.py"
    source.write_text("def run():\n    return 1\n", encoding="utf-8")
    monkeypatch.setattr("devboard_analyzer.code_graph._load_graphify_extract", lambda: (None, "graphify_unavailable"))
    graph = build_code_graph(tmp_path, [source])
    assert graph["graph_contract"]["extractor"]["mode"] == "fallback"
    assert graph["graph_contract"]["extractor"]["fallback_reason"] == "graphify_unavailable"


def test_graphify_failure_does_not_leak_message(monkeypatch, tmp_path):
    source = tmp_path / "app.py"
    source.write_text("def run():\n    return 1\n", encoding="utf-8")
    def broken(*args, **kwargs):
        raise RuntimeError("private path")
    monkeypatch.setattr("devboard_analyzer.code_graph._load_graphify_extract", lambda: (broken, None))
    graph = build_code_graph(tmp_path, [source])
    assert graph["graph_contract"]["extractor"]["fallback_reason"] == "graphify_failed:RuntimeError"
    assert "private path" not in str(graph)
```

Extend the existing success test with contract version, extractor name, and null fallback reason.

- [ ] **Step 2: Run RED**

```bash
cd /home/ubuntu/dev-sandbox/analyzer
pytest -q tests/test_code_graph.py
```

Expected: missing loader and contract.

- [ ] **Step 3: Implement the loader and contract**

Add `_load_graphify_extract()` returning `(extract, None)` or `(None, "graphify_unavailable")`. Change `_build_graphify_graph()` to return `(graph, None)` on success or `(None, "graphify_failed:" + type(exc).__name__)` on failure. Add `_attach_graph_contract(graph, files, fallback_reason)` with the same contract keys as Task 1. Use `quality=full` only when relationships exist and there is no fallback; use `partial` when fallback still yields relationships; otherwise use `inventory_only`.

- [ ] **Step 4: Run GREEN and full analyzer regression**

```bash
pytest -q
ruff check src/devboard_analyzer/code_graph.py tests/test_code_graph.py
```

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/dev-sandbox
git add analyzer/src/devboard_analyzer/code_graph.py analyzer/tests/test_code_graph.py
git commit -m "feat(analyzer): report graph extraction quality"
```

---

### Task 3: Laravel Canonical Normalizer

**Files:**
- Create: `backend/app/Services/Graph/CanonicalGraphNormalizer.php`
- Test: `backend/tests/Unit/CanonicalGraphNormalizerTest.php`

**Interfaces:**
- Produces `normalize(array $payload, array $identity): array`.
- Return keys are `contract`, `identity`, `nodes`, `relationships`, `stats`.

- [ ] **Step 1: Write failing tests**

Create fixtures for legacy `nodes/relationships` and Hades `symbols/edges`. Assert Hades edge keys `kind/source/target` normalize to `type/source_id/target_id`; assert Graphify labels and properties survive; assert missing IDs and unsupported contract versions throw `InvalidArgumentException`.

- [ ] **Step 2: Run RED**

```bash
cd /home/ubuntu/dev-sandbox
docker compose -f docker-compose.devboard.yaml exec -T app sh -lc 'APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Unit/CanonicalGraphNormalizerTest.php'
```

- [ ] **Step 3: Implement exact normalization rules**

```php
public function normalize(array $payload, array $identity): array
{
    $contract = $payload['graph_contract'] ?? null;
    if (! is_array($contract) || ($contract['version'] ?? null) !== 'hades.graph_artifact.v1') {
        throw new InvalidArgumentException('Canonical graph contract is missing or unsupported.');
    }
    $rawNodes = is_array($payload['nodes'] ?? null) ? $payload['nodes'] : ($payload['symbols'] ?? []);
    $rawEdges = is_array($payload['relationships'] ?? null) ? $payload['relationships'] : ($payload['edges'] ?? []);
    $nodes = array_map(fn (array $node): array => $this->node($node), array_values(array_filter($rawNodes, 'is_array')));
    $relationships = array_map(fn (array $edge): array => $this->edge($edge), array_values(array_filter($rawEdges, 'is_array')));
    return ['contract' => $contract, 'identity' => $identity, 'nodes' => $nodes, 'relationships' => $relationships, 'stats' => ['nodes' => count($nodes), 'relationships' => count($relationships)]];
}
```

Implement private `node()` and `edge()` methods with these rules: node ID is `id` then `symbol_id`; properties start from `properties` and absorb `name`, `kind`, `path`, `line_start`, `line_end`, `confidence`; default labels are `Symbol` plus title-cased kind. Edge endpoints accept `source_id/source/from` and `target_id/target/to`; type accepts `type/kind`, uppercases it, and replaces non `[A-Z0-9_]` characters with `_`. Blank node IDs or endpoints throw bounded exceptions.

- [ ] **Step 4: Run GREEN and Pint**

```bash
docker compose -f docker-compose.devboard.yaml exec -T app sh -lc 'APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Unit/CanonicalGraphNormalizerTest.php'
docker compose -f docker-compose.devboard.yaml exec -T app vendor/bin/pint app/Services/Graph/CanonicalGraphNormalizer.php tests/Unit/CanonicalGraphNormalizerTest.php
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/Services/Graph/CanonicalGraphNormalizer.php backend/tests/Unit/CanonicalGraphNormalizerTest.php
git commit -m "feat(graph): normalize canonical graph artifacts"
```

---

### Task 4: Source-Scoped Graph Repository

**Files:**
- Create: `backend/app/Services/Graph/CanonicalGraphRepository.php`
- Test: `backend/tests/Feature/CanonicalGraphRepositoryTest.php`

**Interfaces:**
- Produces `latestForScope(string $projectId, string $scopeType, string $scopeId): ?array`.
- Produces `findByIdentity(string $projectId, string $scopeType, string $scopeId, string $artifactType, string $artifactId): ?array` for queued jobs.
- Produces `listScopes(string $projectId): array`.
- Allowed scopes are `workspace_binding` and `repository`.

- [ ] **Step 1: Write failing repository tests**

Create minimum database fixtures and assert:

```php
$graph = app(CanonicalGraphRepository::class)->latestForScope($projectId, 'workspace_binding', $bindingId);
expect($graph['identity']['artifact_type'])->toBe('hades_agent_artifact')
    ->and($graph['identity']['source_scope_id'])->toBe($bindingId);

$legacy = app(CanonicalGraphRepository::class)->latestForScope($projectId, 'repository', $repositoryId);
expect($legacy['identity']['artifact_type'])->toBe('legacy_artifact');

expect(app(CanonicalGraphRepository::class)->latestForScope((string) Str::ulid(), 'workspace_binding', $bindingId))->toBeNull();
```

Also assert an `unlinked` Hades binding is ignored and an unknown scope type throws `InvalidArgumentException`.

- [ ] **Step 2: Run RED**

```bash
docker compose -f docker-compose.devboard.yaml exec -T app sh -lc 'APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/CanonicalGraphRepositoryTest.php'
```

- [ ] **Step 3: Implement exact selection**

For `workspace_binding`, require a linked binding matching project and ID; select the newest `hades_agent_artifacts` row with schema `hades.php_graph.v1` or `hades.code_graph.v1`. For `repository`, require the repository to match the project; select the newest snapshot with `graph_snapshot_artifact_id`, read the artifact through `Storage::disk('local')`, and normalize it. Never fall back between scope types. `findByIdentity()` repeats all project/scope checks but selects the exact artifact instead of the latest one.

Existing stored artifacts may predate `graph_contract`. Before normalization,
adapt only trusted rows already selected through the project/scope checks by
adding `mode=legacy_adapter`, `quality=partial`, and
`fallback_reason=missing_contract_metadata`. Derive language and counts from
the payload and use extractor `hades-legacy-<language>` for Hades rows or
`legacy-analyzer` for snapshot rows. Do not rewrite stored payloads. Newly
produced artifacts must carry the real contract from Tasks 1 or 2.

Build identity as:

```php
[
    'project_id' => $projectId,
    'source_scope_type' => $scopeType,
    'source_scope_id' => $scopeId,
    'artifact_type' => $artifactType,
    'artifact_id' => (string) $artifact->id,
    'checksum' => (string) ($artifact->sha256 ?: hash('sha256', $json)),
    'created_at' => (string) $artifact->created_at,
]
```

`listScopes()` returns separate scope records and never chooses a project-wide graph when several sources exist. Do not include `display_path`.

- [ ] **Step 4: Run GREEN and privacy regressions**

```bash
docker compose -f docker-compose.devboard.yaml exec -T app sh -lc 'APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/CanonicalGraphRepositoryTest.php tests/Feature/Hades/HadesM3SharedMemoryTest.php tests/Feature/ProjectLifecycleDashboardApiTest.php'
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/Services/Graph/CanonicalGraphRepository.php backend/tests/Feature/CanonicalGraphRepositoryTest.php
git commit -m "feat(graph): resolve source-scoped graph artifacts"
```

---

### Task 5: Persist Projection Lifecycle

**Files:**
- Create: `backend/database/migrations/2026_07_11_000003_create_canonical_graph_projections_table.php`
- Create: `backend/app/Services/Graph/CanonicalGraphProjectionService.php`
- Test: `backend/tests/Feature/CanonicalGraphProjectionTest.php`

**Interfaces:**
- Statuses: `queued`, `projecting`, `ready`, `failed`, `stale`.
- Methods: `queue(array $graph): object`, `markProjecting(string $id): void`, `markReady(string $id, int $nodes, int $relationships): void`, `markFailed(string $id, string $code): void`, `readyForScope(string $projectId, string $scopeType, string $scopeId): ?object`.

- [ ] **Step 1: Write failing lifecycle tests**

```php
$first = $service->queue(canonicalProjectionGraph('artifact-1', str_repeat('a', 64)));
$service->markProjecting($first->id);
$service->markReady($first->id, 10, 5);
$second = $service->queue(canonicalProjectionGraph('artifact-2', str_repeat('b', 64)));
$service->markProjecting($second->id);
expect($service->readyForScope('project-1', 'workspace_binding', 'binding-1')->id)->toBe($first->id);
$service->markReady($second->id, 12, 7);
expect($service->readyForScope('project-1', 'workspace_binding', 'binding-1')->id)->toBe($second->id)
    ->and(DB::table('canonical_graph_projections')->where('id', $first->id)->value('status'))->toBe('stale');
```

Add a failure-code test proving raw exception text is rejected.

- [ ] **Step 2: Run RED**

Expected: missing table and service.

- [ ] **Step 3: Create the migration**

```php
$table->ulid('id')->primary();
$table->foreignUlid('project_id')->constrained()->cascadeOnDelete();
$table->string('source_scope_type', 32);
$table->string('source_scope_id', 191);
$table->string('artifact_type', 32);
$table->string('artifact_id', 191);
$table->string('graph_version', 64)->unique();
$table->string('checksum', 64);
$table->string('head_commit', 80)->nullable();
$table->string('quality', 32);
$table->string('status', 32);
$table->unsignedBigInteger('node_count')->nullable();
$table->unsignedBigInteger('relationship_count')->nullable();
$table->string('error_code', 100)->nullable();
$table->timestamp('projected_at')->nullable();
$table->timestamps();
$table->unique(['artifact_type', 'artifact_id']);
$table->index(['project_id', 'source_scope_type', 'source_scope_id', 'status'], 'canonical_graph_scope_status_idx');
```

- [ ] **Step 4: Implement transaction-safe transitions**

Compute `graph_version` as SHA-256 of `artifact_type|artifact_id|checksum`. `queue()` is idempotent on artifact identity. `markReady()` locks the candidate, marks the previous ready row in the same scope stale, and then marks the candidate ready in one DB transaction. `markFailed()` stores only `[a-z0-9_]{1,100}`, falling back to `projection_failed`.

- [ ] **Step 5: Run GREEN**

```bash
docker compose -f docker-compose.devboard.yaml exec -T app sh -lc 'APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/CanonicalGraphProjectionTest.php'
```

- [ ] **Step 6: Commit**

```bash
git add backend/database/migrations/2026_07_11_000003_create_canonical_graph_projections_table.php backend/app/Services/Graph/CanonicalGraphProjectionService.php backend/tests/Feature/CanonicalGraphProjectionTest.php
git commit -m "feat(graph): persist projection lifecycle"
```

---

### Task 6: Common Neo4j Projector And Queue Job

**Files:**
- Create: `backend/app/Services/Graph/Neo4jCanonicalGraphProjector.php`
- Create: `backend/app/Jobs/ProjectCanonicalGraphToNeo4j.php`
- Create: `backend/tests/Feature/Hades/CanonicalHadesGraphProjectionTest.php`
- Modify: `backend/app/Http/Controllers/Hades/ArtifactController.php`
- Modify: `backend/app/Services/GenesisGraphImportService.php`
- Modify: `backend/tests/Feature/GenesisGraphImportTest.php`

**Interfaces:**
- `project(array $graph, object $projection, Neo4jClient $client): array{nodes:int, relationships:int}`.
- Job constructor accepts only `projectionId`.

- [ ] **Step 1: Write failing Hades projection tests**

Use `Bus::fake()`. Upload a canonical Hades graph, assert a queued projection row and exactly one `ProjectCanonicalGraphToNeo4j` dispatch. Repeat the same upload and assert deduplication dispatches no second job. Run the job with `FakeNeo4jClient` and assert node/edge command parameters include project, source scope, and graph version.

- [ ] **Step 2: Run RED**

Expected: missing job/projector and no projection row.

- [ ] **Step 3: Implement the projector**

The projector creates indexes on `(graph_version, external_id)`, creates a candidate `CanonicalGraphVersion` with `current=false`, writes nodes in batches of 500, writes edges grouped by sanitized type, verifies counts, and only then switches current versions in one final Cypher command. Use `graph_version` as isolation key; retain legacy `snapshot_id` only as an optional compatibility property.

Required final switch:

```cypher
MATCH (candidate:CanonicalGraphVersion {graph_version: $graph_version})
MATCH (other:CanonicalGraphVersion {project_id: $project_id, source_scope_type: $source_scope_type, source_scope_id: $source_scope_id})
SET other.current = false, candidate.current = true, candidate.status = 'ready', candidate.projected_at = datetime()
```

- [ ] **Step 4: Implement the job**

Load the projection row through `findByIdentity()`, verify artifact ID/checksum, mark projecting, call the projector, then mark ready. Never use `latestForScope()` inside a queued job because a newer artifact may arrive while the job waits. On error map to `neo4j_unavailable`, `artifact_missing`, `artifact_changed`, or `neo4j_query_failed`, mark failed, and rethrow for queue retry.

- [ ] **Step 5: Enqueue only newly inserted Hades graphs**

Inject repository/projection services into `ArtifactController`. After commit and only for graph schemas, resolve the exact binding graph, queue projection, and dispatch the job with `afterCommit()`. Do not dispatch from the deduplicated early return.

- [ ] **Step 6: Delegate legacy full snapshots**

In `GenesisGraphImportService`, normalize loaded legacy payloads and use the common projector for full snapshots. Keep affected-subgraph delta preparation intact, then project the resulting version through the common projector. Preserve artifact status and `graph.imported` run events.

- [ ] **Step 7: Run GREEN and regressions**

```bash
docker compose -f docker-compose.devboard.yaml exec -T app sh -lc 'APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades/CanonicalHadesGraphProjectionTest.php tests/Feature/GenesisGraphImportTest.php tests/Feature/GraphImportJobRetryTest.php'
```

- [ ] **Step 8: Commit**

```bash
git add backend/app/Services/Graph/Neo4jCanonicalGraphProjector.php backend/app/Jobs/ProjectCanonicalGraphToNeo4j.php backend/app/Http/Controllers/Hades/ArtifactController.php backend/app/Services/GenesisGraphImportService.php backend/tests/Feature/Hades/CanonicalHadesGraphProjectionTest.php backend/tests/Feature/GenesisGraphImportTest.php
git commit -m "feat(graph): project canonical artifacts into Neo4j"
```

---

### Task 7: Shared Plugin And Hades Query Service

**Files:**
- Create: `backend/app/Services/Graph/CanonicalGraphQueryService.php`
- Modify: `backend/app/Services/Graph/GraphQueryService.php`
- Modify: `backend/app/Http/Controllers/Hades/GraphTraversalController.php`
- Modify: `backend/tests/Feature/Plugin/GraphQueryApiTest.php`
- Modify: `backend/tests/Feature/Hades/HadesM3SharedMemoryTest.php`

**Interfaces:**
- `query(projectId, scopeType, scopeId, type, params): array`.
- Types: `callers`, `callees`, `path`, `traverse`.
- Return: `found`, `reason`, `graph_version`, `quality`, `results`, `edges`.

- [ ] **Step 1: Write failing shared-query tests**

Create a Hades-only ready projection without legacy snapshots. Assert plugin callers/callees/path and Hades traverse resolve the same graph version. Assert project mismatch returns before any Neo4j call. Add a project with two graph scopes and assert an omitted scope returns `scope_required` rather than selecting one.

- [ ] **Step 2: Run RED**

Expected: existing service returns `graph_snapshot_not_found`.

- [ ] **Step 3: Implement canonical query resolution**

Resolve ready projection server-side from authorized project/scope. Hades obtains its exact workspace-binding scope from the authenticated binding. The plugin endpoint accepts optional `repository_id` or `workspace_binding_id`, verifies it belongs to the authenticated project, and defaults only when exactly one graph scope exists. Multiple scopes without an explicit authorized scope return `scope_required`. If no ready projection exists return `graph_projection_not_ready` with empty results. Match nodes by `graph_version`. Keep plugin limit 50/path depth 10 and Hades traverse limit 50/depth 3. Never accept graph version from the client.

- [ ] **Step 4: Preserve compatibility**

Keep `GraphQueryService` injectable as a facade. Keep Hades request validation and response keys, adding graph version and artifact ID inside provenance. Remove duplicate direct artifact BFS only after shared-service parity tests pass.

- [ ] **Step 5: Run GREEN and auth regressions**

```bash
docker compose -f docker-compose.devboard.yaml exec -T app sh -lc 'APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Plugin/GraphQueryApiTest.php tests/Feature/Hades/HadesM3SharedMemoryTest.php tests/Feature/Hades/HadesHardeningTest.php'
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/Services/Graph/CanonicalGraphQueryService.php backend/app/Services/Graph/GraphQueryService.php backend/app/Http/Controllers/Hades/GraphTraversalController.php backend/tests/Feature/Plugin/GraphQueryApiTest.php backend/tests/Feature/Hades/HadesM3SharedMemoryTest.php
git commit -m "refactor(graph): share source-scoped graph queries"
```

---

### Task 8: Canonical Dashboard Graph Reads

**Files:**
- Modify: `backend/app/Dashboard/DashboardApiReader.php`
- Modify: `backend/tests/Feature/Dashboard/DashboardApiContractTest.php`

**Interfaces:**
- Preserve `graph(?string $projectId, ?string $snapshotId, ?string $runId): array`.
- Add `source_scope`, `graph_version`, `quality`, and `projection_status`.

- [ ] **Step 1: Add failing dashboard tests**

For a project with one Hades binding/artifact and no legacy repository, assert nonzero nodes, workspace-binding scope, and full quality. For a project with two graph scopes, assert `projection_status=scope_required` and no arbitrary selection.

```php
$this->actingAs($admin)->getJson("/api/dashboard/projects/{$projectId}/graph")
    ->assertOk()
    ->assertJsonPath('stats.nodes', 1)
    ->assertJsonPath('source_scope.type', 'workspace_binding')
    ->assertJsonPath('quality', 'full');
```

- [ ] **Step 2: Run RED**

Expected: current reader returns an empty graph.

- [ ] **Step 3: Implement canonical selection**

Preserve explicit `snapshot_id`/`run_id` legacy selection. Otherwise call `listScopes(projectId)`: zero scopes returns `unavailable`; one returns its bounded normalized preview; multiple returns `scope_required` plus scope type, ID, quality, head commit, and created time. Never expose display paths.

- [ ] **Step 4: Run GREEN**

```bash
docker compose -f docker-compose.devboard.yaml exec -T app sh -lc 'APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Dashboard/DashboardApiContractTest.php tests/Feature/Dashboard/ProjectKickstartDashboardApiTest.php'
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/Dashboard/DashboardApiReader.php backend/tests/Feature/Dashboard/DashboardApiContractTest.php
git commit -m "fix(dashboard): read canonical project graphs"
```

---

### Task 9: Reconcile And Rebuild Canonical Projections

**Files:**
- Modify: `backend/app/Services/Neo4jRebuildService.php`
- Modify: `backend/routes/console.php`
- Create: `backend/tests/Feature/CanonicalGraphRebuildCommandTest.php`

**Interfaces:**
- Keep command `devboard:neo4j-rebuild`.
- Add `--project=`, `--scope-type=`, `--scope-id=`, and `--dry-run`.

- [ ] **Step 1: Write failing command tests**

Test Hades-only discovery, legacy discovery, dry-run no mutation, exact project filtering, and retry of a failed projection. Assert summary:

```json
{"scanned":1,"queued":1,"ready":0,"failed":0,"skipped":0,"dry_run":true}
```

- [ ] **Step 2: Run RED**

Expected: Hades graph is absent and dry-run is unknown.

- [ ] **Step 3: Implement repository-backed reconciliation**

Use `listScopes`, resolve each exact graph, compare artifact/checksum with projection state, skip ready matches, and queue missing/failed matches. Dry-run performs no insert, dispatch, or Neo4j call. Invalid scope combinations exit nonzero with a bounded message.

- [ ] **Step 4: Run GREEN**

```bash
docker compose -f docker-compose.devboard.yaml exec -T app sh -lc 'APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/CanonicalGraphRebuildCommandTest.php tests/Feature/GenesisGraphImportTest.php tests/Feature/GraphImportJobRetryTest.php'
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/Services/Neo4jRebuildService.php backend/routes/console.php backend/tests/Feature/CanonicalGraphRebuildCommandTest.php
git commit -m "feat(graph): reconcile canonical Neo4j projections"
```

---

### Task 10: Documentation And End-To-End Verification

**Files:**
- Modify: `docs/hades/backend.md`
- Modify: Hades OpenAPI fixture found with `rg --files backend | rg 'openapi.*hades|hades.*openapi'`
- Modify: `docs/backend-agent-coordination.md`
- Modify: `/home/ubuntu/dev-sandbox/ai-sandbox/logbooks/LOGBOOK_PROJECT.md`

**Interfaces:**
- Document contract fields, projection states, additive responses, and rebuild operations.

- [ ] **Step 1: Document the contract**

Document contract version, extractor name/version/mode/quality/fallback reason, coverage language/file counts, source branch/head commit, and that old schema names remain valid.

- [ ] **Step 2: Update API and operational docs**

Document additive graph response fields, `graph_projection_not_ready`, exact-scope resolution, and rebuild options. State that clients cannot select graph versions directly.

- [ ] **Step 3: Run full local Hades verification**

```bash
cd /Users/gabriele/Dev/Hephaistos
source .venv/bin/activate
pytest -q tests/hermes_cli/test_hades_graph_contract.py tests/hermes_cli/test_hades_backend_jobs.py tests/hermes_cli/test_hades_backend_sync_runner.py tests/hermes_cli/test_hades_backend_indexer_golden.py tests/hermes_cli/test_hades_source_slice_policy.py
ruff check hermes_cli/hades_graph_contract.py hermes_cli/hades_index tests/hermes_cli/test_hades_graph_contract.py
git diff --check
```

Expected: zero failures and lint errors.

- [ ] **Step 4: Run full backend graph/Hades verification**

```bash
cd /home/ubuntu/dev-sandbox
docker compose -f docker-compose.devboard.yaml exec -T app sh -lc 'APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Unit/CanonicalGraphNormalizerTest.php tests/Feature/CanonicalGraphRepositoryTest.php tests/Feature/CanonicalGraphProjectionTest.php tests/Feature/Hades/CanonicalHadesGraphProjectionTest.php tests/Feature/CanonicalGraphRebuildCommandTest.php tests/Feature/GenesisGraphImportTest.php tests/Feature/GraphImportJobRetryTest.php tests/Feature/Plugin/GraphQueryApiTest.php tests/Feature/Hades tests/Feature/Dashboard/DashboardApiContractTest.php'
```

Expected: zero failures. Existing PostgreSQL-only skips may remain; no new canonical graph test may be skipped.

- [ ] **Step 5: Run formatting and migration checks**

```bash
docker compose -f docker-compose.devboard.yaml exec -T app vendor/bin/pint --test app/Services/Graph app/Jobs/ProjectCanonicalGraphToNeo4j.php app/Http/Controllers/Hades/ArtifactController.php app/Http/Controllers/Hades/GraphTraversalController.php app/Dashboard/DashboardApiReader.php tests/Unit tests/Feature
docker compose -f docker-compose.devboard.yaml exec -T app php artisan migrate:status
git diff --check
```

- [ ] **Step 6: Apply development migration and reconcile**

Only after tests pass:

```bash
docker compose -f docker-compose.devboard.yaml exec -T app php artisan migrate --force
docker compose -f docker-compose.devboard.yaml exec -T app php artisan devboard:neo4j-rebuild --project=01KX8G47N6HK2AC4NSVS912ECJ --dry-run
docker compose -f docker-compose.devboard.yaml exec -T app php artisan devboard:neo4j-rebuild --project=01KX8G47N6HK2AC4NSVS912ECJ
```

Expected: dry-run discovers the Hades graph; real run creates a ready projection with nonzero counts.

- [ ] **Step 7: Perform live read-only smokes**

```bash
cd /Users/gabriele/Dev/Hephaistos
hades backend sync
hades backend status --json
```

Verify without exposing tokens: no 401, expected binding selected, extractor/quality/head commit present, dashboard graph stats nonzero, plugin and Hades queries use the same graph version, and Neo4j rebuild reproduces counts.

- [ ] **Step 8: Record and commit evidence separately**

Hades repository:

```bash
git add docs/hades/backend.md docs/backend-agent-coordination.md
git commit -m "docs(hades): document canonical graph pipeline"
```

DevBoard repository:

```bash
cd /home/ubuntu/dev-sandbox
git add backend/docs ai-sandbox/logbooks/LOGBOOK_PROJECT.md
git commit -m "docs(graph): record canonical projection rollout"
```

If OpenAPI is outside `backend/docs`, add its exact path. Never use `git add -A`.

## Final Acceptance Checklist

- [ ] Native Hades graphs contain `hades.graph_artifact.v1` metadata.
- [ ] Graphify success, absence, and runtime failure are distinguishable.
- [ ] No Graphify exception text or local absolute path leaks.
- [ ] Laravel normalizes Hades and legacy payloads.
- [ ] Selection always uses project plus exact source scope.
- [ ] Hades-only projects work without legacy snapshots.
- [ ] Projection lifecycle survives restarts with bounded failure codes.
- [ ] Previous ready projection remains current until replacement verifies.
- [ ] Neo4j data is isolated by canonical graph version.
- [ ] Legacy Genesis events and behavior remain intact.
- [ ] Plugin and Hades queries share canonical resolution.
- [ ] Dashboard reads no longer require legacy graph rows.
- [ ] Dry-run rebuild makes no changes.
- [ ] Rebuild reconstructs Neo4j from canonical artifacts.
- [ ] Local tests, analyzer tests, backend tests, Ruff, Pint, migration status, and diff checks pass.
- [ ] Project `01KX8G47N6HK2AC4NSVS912ECJ` has a ready projection with nonzero verified counts.
- [ ] No UI redesign, tenancy, backup, wiki, Platon, or new model tool entered this tranche.
