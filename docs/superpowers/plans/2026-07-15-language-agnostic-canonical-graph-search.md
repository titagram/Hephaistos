# Language-Agnostic Canonical Graph Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make routes, inherited framework entrypoints, tests, and ordinary symbols reliably searchable in Hades canonical graphs across PHP, Python, TypeScript/JavaScript, SQL, and polyglot workspaces.

**Architecture:** Language adapters continue extracting framework semantics, then shared inventory promotion converts `routes[]` and test maps into first-class declarations before canonicalization. A polyglot aggregator merges adapter outputs deterministically. The backend hydrates compatible older route inventories, projects safe search aliases into a versioned Neo4j full-text index, and exposes honest coverage to the search-first frontend.

**Tech Stack:** Python 3.11+, pytest, Laravel/PHP 8.3+, Pest, PostgreSQL, Neo4j 5.26, React/TypeScript/Vitest, Docker Compose.

## Global Constraints

- Preserve `hades.graph_artifact.v1`; additive fields must remain backward compatible.
- Never expose raw source, secrets, local absolute paths, raw canonical external IDs, or cross-project graph data.
- Keep graph reads scoped by project ID, source-scope type, source-scope ID, and active graph version.
- Keep extraction bounded by configurable file, byte, node, edge, test, and issue budgets.
- Run every behavior through a red-green TDD cycle before production code.
- Do not reset or delete PostgreSQL or Neo4j data; candidate projection publication remains atomic.
- Keep framework semantics in adapters and shared identity/search behavior language-independent.
- Do not implement wiki verification or wiki generation in this plan.

---

## File Structure

### Hades Agent repository

- Create `hermes_cli/hades_index/inventory.py`: promote uniform route/test inventories into explicit graph declarations and merge duplicates.
- Create `hermes_cli/hades_index/aggregate.py`: merge multiple per-language graph artifacts deterministically.
- Modify `hermes_cli/hades_index/__init__.py`: run every detected adapter and call the shared aggregator.
- Modify `hermes_cli/hades_graph_contract.py`: invoke shared promotion before canonicalization and prioritize route/test declarations over placeholders.
- Modify `hermes_cli/hades_backend_jobs.py`: use 10,000-file default plus bounded aggregate byte budget; report budget omissions distinctly.
- Modify `hermes_cli/hades_index/php.py`: build the bounded Symfony class/action inheritance index.
- Modify `tests/hermes_cli/test_hades_graph_contract.py`: generic route/test promotion and capacity tests.
- Modify `tests/hermes_cli/test_hades_backend_jobs.py`: Symfony inheritance, cross-language, polyglot, budget, and test-node integration tests.

### Backend repository `/home/ubuntu/dev-sandbox`

- Modify `backend/app/Services/Graph/CanonicalGraphRepository.php`: hydrate top-level routes for both legacy and contracted artifacts.
- Modify `backend/app/Services/Graph/Neo4jCanonicalGraphProjector.php`: create search-v2 index and project safe alias terms.
- Create `backend/app/Services/Graph/DashboardGraphSearchTerms.php`: shared normalization for stored aliases and incoming queries.
- Modify `backend/app/Services/Graph/DashboardGraphExplorerService.php`: query search-v2 with exact/prefix token ranking.
- Create `backend/database/migrations/2026_07_15_000000_add_canonical_graph_coverage.php`: persist bounded coverage metadata on projections.
- Modify `backend/app/Services/Graph/CanonicalGraphProjectionService.php`: store graph-contract coverage when queuing candidates.
- Modify `backend/tests/Feature/Hades/CanonicalHadesGraphProjectionTest.php`: contracted-artifact route hydration and projection tests.
- Modify `backend/tests/Unit/Services/Graph/DashboardGraphExplorerServiceTest.php`: search query normalization/isolation tests.
- Modify `backend/tests/Feature/Graph/CanonicalGraphExplorerNeo4jAcceptanceTest.php`: live URI/test search acceptance.
- Modify `frontend/src/components/devboard/GraphExplorer.tsx`: display partial coverage and distinguish not-found from not-indexed.
- Modify `frontend/src/components/devboard/GraphExplorer.test.tsx`: coverage messaging tests.
- Modify `frontend/src/types/devboard.ts`: additive coverage response types.

---

### Task 1: Shared Route and Test Inventory Promotion

**Files:**
- Create: `hermes_cli/hades_index/inventory.py`
- Modify: `hermes_cli/hades_graph_contract.py`
- Test: `tests/hermes_cli/test_hades_graph_contract.py`

**Interfaces:**
- Produces `promote_graph_inventories(graph: dict[str, Any]) -> dict[str, int]`.
- Mutates only the working artifact's declaration collection; preserves `routes[]`, `tests`, `symbols`, and `edges` evidence.
- Returns counters `routes_detected`, `routes_promoted`, `routes_merged`, `tests_detected`, `tests_promoted`, and `tests_merged`.

- [ ] **Step 1: Write failing cross-language route promotion tests**

Add table-driven fixtures whose schemas are PHP, Python, and TypeScript but whose route record shape is uniform:

```python
@pytest.mark.parametrize("schema,language", [
    ("hades.php_graph.v1", "php"),
    ("hades.code_graph.v1", "python"),
    ("hades.code_graph.v1", "typescript"),
])
def test_finalize_promotes_uniform_route_inventory_to_first_class_nodes(schema, language):
    result = _finalize({
        "schema": schema,
        "language": language,
        "routes": [{
            "framework": "fixture",
            "method": "GET",
            "uri": "/orders/{id}",
            "name": "orders.show",
            "handler": "OrderController@show",
            "path": "src/OrderController.php",
            "line": 12,
        }],
        "symbols": [{"kind": "method", "name": "OrderController@show"}],
        "edges": [{"kind": "route_handler", "from": "route:orders.show", "to": "OrderController@show"}],
    }, max_symbols=20)
    route = next(node for node in result["nodes"] if node.get("kind") == "route")
    assert route["name"] == "orders.show"
    assert route["uri"] == "/orders/{id}"
    assert route["method"] == "GET"
    assert route["handler"] == "OrderController@show"
    assert result["canonicalization"]["route_inventory"]["routes_promoted"] == 1
```

- [ ] **Step 2: Run the route test and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/hermes_cli/test_hades_graph_contract.py -k promotes_uniform_route_inventory -q
```

Expected: FAIL because canonicalization still synthesizes a placeholder route without `uri`.

- [ ] **Step 3: Write failing test-node and idempotency tests**

```python
def test_finalize_promotes_test_map_files_to_searchable_test_nodes():
    result = _finalize({
        "schema": "hades.php_graph.v1",
        "language": "php",
        "symbols": [],
        "edges": [],
        "tests": {"schema": "hades.test_map.v1", "files": [{
            "path": "tests/AdminControllerBulkDeleteBehaviorTest.php",
            "framework": "phpunit",
            "cases": ["testBulkDeleteSkipsForbiddenRows"],
            "target_candidates": ["AdminControllerBulkDeleteBehavior"],
        }]},
    }, max_symbols=20)
    test_node = next(node for node in result["nodes"] if node.get("kind") == "test")
    assert test_node["name"] == "AdminControllerBulkDeleteBehaviorTest"
    assert test_node["framework"] == "phpunit"


def test_inventory_promotion_merges_an_existing_route_node_idempotently():
    graph = {
        "routes": [{"method": "GET", "uri": "/orders", "name": "orders.index"}],
        "symbols": [{"kind": "route", "name": "orders.index", "method": "GET"}],
        "edges": [],
    }
    result = _finalize(graph, max_symbols=20)
    routes = [node for node in result["nodes"] if node.get("kind") == "route"]
    assert len(routes) == 1
    assert routes[0]["uri"] == "/orders"
```

- [ ] **Step 4: Run the new tests and verify RED**

Run the two exact tests and confirm they fail because no test node exists and route declarations do not merge inventory properties.

- [ ] **Step 5: Implement shared promotion**

Create `inventory.py` with these public functions and semantics:

```python
def promote_graph_inventories(graph: dict[str, Any]) -> dict[str, int]:
    declarations_key = "nodes" if isinstance(graph.get("nodes"), list) else "symbols"
    declarations = [dict(item) for item in graph.get(declarations_key, []) if isinstance(item, dict)]
    declarations = _promote_routes(declarations, graph.get("routes") or [], counters)
    declarations = _promote_tests(declarations, (graph.get("tests") or {}).get("files") or [], counters)
    graph[declarations_key] = declarations
    return counters
```

Use route identity `name` when present, otherwise `METHOD normalized-uri + handler`. Merge non-empty route properties into an existing route declaration. Build test names from an explicit class/name when present, otherwise from the source file stem. Keep paths source-relative and bounded; do not copy raw test bodies.

Call `promote_graph_inventories()` at the beginning of `finalize_graph_artifact()` and store its counters under `canonicalization.route_inventory` and `canonicalization.test_inventory` after `_canonicalize_graph()` returns.

- [ ] **Step 6: Prioritize explicit route/test nodes over placeholders**

Change `_canonicalize_graph()` candidate sorting so explicit nodes rank by:

```python
kind_priority = {"route": 0, "http_endpoint": 0, "endpoint": 0, "test": 1}
(
    0 if not candidate["synthetic"] else 1,
    kind_priority.get(candidate["kind"], 10),
    0 if reference_counts[candidate["id"]] else 1,
    -reference_counts[candidate["id"]],
    candidate["id"],
)
```

Store normalized candidate kind during candidate construction rather than re-parsing it inside the sorter.

- [ ] **Step 7: Run targeted and full contract tests GREEN**

```bash
.venv/bin/python -m pytest tests/hermes_cli/test_hades_graph_contract.py -q
```

Expected: all tests pass with no warnings.

- [ ] **Step 8: Commit Task 1**

```bash
git add hermes_cli/hades_index/inventory.py hermes_cli/hades_graph_contract.py tests/hermes_cli/test_hades_graph_contract.py
git commit -m "fix(hades): promote graph route and test inventories"
```

### Task 2: Polyglot Aggregation and Honest Extraction Budgets

**Files:**
- Create: `hermes_cli/hades_index/aggregate.py`
- Modify: `hermes_cli/hades_index/__init__.py`
- Modify: `hermes_cli/hades_backend_jobs.py`
- Test: `tests/hermes_cli/test_hades_backend_jobs.py`

**Interfaces:**
- Produces `merge_graph_artifacts(artifacts: list[dict[str, Any]], *, root: str) -> dict[str, Any]`.
- Produces per-language `graph_contract.coverage.languages` through `finalize_graph_artifact()`.
- `_iter_workspace_files(..., max_total_bytes: int | None)` reports `file_budget_exceeded` and `byte_budget_exceeded` separately.

- [ ] **Step 1: Write a failing PHP+TypeScript workspace test**

Create a fixture with a Symfony route and a Next.js route. Assert one final artifact contains both routes, both language declarations, and `language == "polyglot"`.

- [ ] **Step 2: Verify RED**

Run the exact polyglot test. Expected: FAIL because current `if has_php` prevents TypeScript indexing.

- [ ] **Step 3: Write a failing 1,001+file budget test**

Use small files and explicit payload budgets. Assert the default AST ceiling admits more than 1,000 files, while a small `max_total_bytes` produces `byte_budget_exceeded` coverage rather than `files_failed`.

- [ ] **Step 4: Implement deterministic aggregation**

Run each detected adapter against its language-specific candidates. `aggregate.py` must:

```python
def merge_graph_artifacts(artifacts: list[dict[str, Any]], *, root: str) -> dict[str, Any]:
    return {
        "schema": "hades.code_graph.v1",
        "language": "polyglot" if len(languages) > 1 else languages[0],
        "framework": "polyglot" if len(frameworks) > 1 else frameworks[0],
        "root": root,
        "routes": stable_unique(route_records),
        "symbols": stable_unique(symbol_records),
        "edges": stable_unique(edge_records),
        "database": merge_database_maps(artifacts),
        "tests": merge_test_maps(artifacts),
        "logs": merge_log_maps(artifacts),
        "analysis": merge_analysis(artifacts),
        "truncated": any(a.get("truncated") for a in artifacts),
        "omitted": stable_unique_omissions(artifacts),
        "raw_source_included": False,
    }
```

Deduplicate using stable JSON, never name alone. Preserve namespace/language differences.

- [ ] **Step 5: Implement bounded candidate collection**

Set `_execute_populate_backend_ast()` defaults to `max_files=10_000` and `max_total_bytes=134_217_728`. Track cumulative `path.stat().st_size`; stop before a file that exceeds the remaining aggregate budget and append a bounded omission reason. Pass configured limits into coverage metadata.

- [ ] **Step 6: Run cross-language, existing indexer, and budget tests GREEN**

```bash
.venv/bin/python -m pytest tests/hermes_cli/test_hades_backend_jobs.py -k "populate_backend_ast or polyglot or budget" -q
```

- [ ] **Step 7: Commit Task 2**

```bash
git add hermes_cli/hades_index/aggregate.py hermes_cli/hades_index/__init__.py hermes_cli/hades_backend_jobs.py tests/hermes_cli/test_hades_backend_jobs.py
git commit -m "feat(hades): aggregate bounded polyglot code graphs"
```

### Task 3: Symfony Inherited Route Resolution

**Files:**
- Modify: `hermes_cli/hades_index/php.py`
- Test: `tests/hermes_cli/test_hades_backend_jobs.py`

**Interfaces:**
- Produces `_php_symfony_class_index(...) -> dict[str, SymfonyClassInfo]` as an internal bounded metadata map.
- Adds `defined_handler` and `inherited` to uniform route records.
- Emits route-handler edges to the defining method symbol with `effective_controller` metadata.

- [ ] **Step 1: Write the Carnovali-shaped failing fixture**

Create abstract `AdminController` with `@Route("/", name="") public function index()`, intermediate `RoleController`, and concrete `WorkerController` with class annotation `@Route("generale/soggetti-attivi", name="contact_flock_roles_worker")`. Assert:

```python
assert {
    "uri": "/generale/soggetti-attivi/",
    "name": "contact_flock_roles_worker",
    "handler": "WorkerController@index",
    "defined_handler": "AdminController@index",
    "inherited": True,
} <= inherited_route
assert ("route_handler", "route:contact_flock_roles_worker", "AdminController@index") in edges
```

- [ ] **Step 2: Verify RED**

Run the exact test. Expected: no inherited route exists.

- [ ] **Step 3: Add cycle/depth failing fixture**

Construct metadata with a parent cycle and assert direct routes survive, extraction terminates, and analysis reports `symfony_inheritance: partial` with a bounded reason.

- [ ] **Step 4: Implement a two-pass bounded class/action index**

First pass reads eligible PHP files and records FQCN, parent FQCN, class routes, annotated methods, method symbol, source-relative path, and line. Second pass walks ancestors up to 32 levels with a visited set, combines concrete class route with inherited action routes, and emits deduplicated uniform records.

Do not create a fake `WorkerController@index` method node. Route properties hold the effective handler; the edge targets the real `AdminController@index` declaration.

- [ ] **Step 5: Run Symfony and PHP regression tests GREEN**

```bash
.venv/bin/python -m pytest tests/hermes_cli/test_hades_backend_jobs.py -k "symfony or php_graph" -q
```

- [ ] **Step 6: Commit Task 3**

```bash
git add hermes_cli/hades_index/php.py tests/hermes_cli/test_hades_backend_jobs.py
git commit -m "feat(hades): resolve inherited Symfony routes"
```

### Task 4: Backend Contracted-Artifact Route Hydration

**Files:**
- Modify: `/home/ubuntu/dev-sandbox/backend/app/Services/Graph/CanonicalGraphRepository.php`
- Test: `/home/ubuntu/dev-sandbox/backend/tests/Feature/Hades/CanonicalHadesGraphProjectionTest.php`

**Interfaces:**
- `prepareHadesPayload()` always calls an idempotent `hydrateRouteInventory()` before normalization.
- Hydration returns declarations plus server-private trusted-route provenance.

- [ ] **Step 1: Write a failing contracted-artifact test**

Upload `hades.php_graph.v1` with `graph_contract`, complete `routes[]`, a route-handler relationship, and no explicit route node. Assert normalized graph contains exactly one route node whose properties include trusted `/generale/soggetti-attivi/{id}/home`, method, handler, and name.

- [ ] **Step 2: Verify RED in the app container**

```bash
docker compose -f docker-compose.devboard.yaml exec -T app php artisan test \
  backend/tests/Feature/Hades/CanonicalHadesGraphProjectionTest.php \
  --filter="hydrates contracted route inventories"
```

Expected: route node exists only as a placeholder and lacks URI.

- [ ] **Step 3: Write idempotency and unsafe-path tests**

Assert a new artifact with an explicit matching route node remains one node and gains missing safe properties. Assert `src/Controller.php`, `../secret`, `file:///etc/passwd`, and an untrusted absolute path cannot become `public_search_path`.

- [ ] **Step 4: Implement hydration at the contract boundary**

Move route adaptation out of the legacy-only branch. Always call it before `CanonicalGraphNormalizer::normalize()`. Merge route records into `nodes` or `symbols` by `route:<name>`/method+URI identity, preserve the original payload separately, and stamp trusted provenance only in the server-private map.

- [ ] **Step 5: Run graph repository/projection tests GREEN**

```bash
docker compose -f docker-compose.devboard.yaml exec -T app php artisan test \
  backend/tests/Feature/Hades/CanonicalHadesGraphProjectionTest.php \
  backend/tests/Feature/CanonicalGraphProjectionTest.php
```

- [ ] **Step 6: Commit Task 4 in the backend repository**

```bash
git add backend/app/Services/Graph/CanonicalGraphRepository.php backend/tests/Feature/Hades/CanonicalHadesGraphProjectionTest.php
git commit -m "fix(graph): hydrate contracted route inventories"
```

### Task 5: Safe Search Terms and Versioned Neo4j Index

**Files:**
- Create: `/home/ubuntu/dev-sandbox/backend/app/Services/Graph/DashboardGraphSearchTerms.php`
- Modify: `/home/ubuntu/dev-sandbox/backend/app/Services/Graph/Neo4jCanonicalGraphProjector.php`
- Modify: `/home/ubuntu/dev-sandbox/backend/app/Services/Graph/DashboardGraphExplorerService.php`
- Test: `/home/ubuntu/dev-sandbox/backend/tests/Unit/Services/Graph/DashboardGraphExplorerServiceTest.php`
- Test: `/home/ubuntu/dev-sandbox/backend/tests/Feature/Hades/CanonicalHadesGraphProjectionTest.php`

**Interfaces:**
- `DashboardGraphSearchTerms::forNode(array $properties, string $kind, bool $trustedRoute): array` returns exact fields and bounded `public_search_terms`.
- `DashboardGraphSearchTerms::forQuery(string $query): array{normalized:string,tokens:list<string>,lucene:string}`.
- Explorer reads `canonical_node_search_v2` only.

- [ ] **Step 1: Write failing normalization unit tests**

Assert equivalent tokens for `soggetti-attivi`, `/generale/soggetti-attivi/`, `contact_flock_roles_worker_home`, and `route:contact_flock_roles_worker_home`. Assert Lucene operators and path traversal input are escaped/rejected rather than interpreted.

- [ ] **Step 2: Write failing projector tests**

Assert the projector creates:

```cypher
CREATE FULLTEXT INDEX canonical_node_search_v2 IF NOT EXISTS
FOR (n:CanonicalGraphNode)
ON EACH [n.graph_version, n.public_search_name, n.public_search_label, n.public_search_path, n.public_search_terms]
```

Assert route/test nodes receive aliases; non-route filesystem paths never enter public fields.

- [ ] **Step 3: Verify RED**

Run the two targeted Pest files; expect missing class/index/field assertions.

- [ ] **Step 4: Implement `DashboardGraphSearchTerms`**

Normalize Unicode validity, lowercase, separator boundaries (`_-. :/@\\`), repeated whitespace, and trusted route slashes. Bound every field and total alias text. Build Lucene from escaped alphanumeric tokens as `token*` joined with `AND`; retain exact normalized URI/name separately for ranking.

- [ ] **Step 5: Project search-v2 fields**

Replace projector-local search derivation with the shared service. Create v2 index before writes. Keep v1 untouched for rollback.

- [ ] **Step 6: Query and rank search-v2**

Use `canonical_node_search_v2`, retain mandatory graph-version/project/scope predicates, and add exact normalized name/path boosts before full-text score. Keep cursor ordering deterministic as `(score DESC, public_handle ASC)`.

- [ ] **Step 7: Run unit/projection/explorer tests GREEN**

```bash
docker compose -f docker-compose.devboard.yaml exec -T app php artisan test \
  backend/tests/Unit/Services/Graph/DashboardGraphExplorerServiceTest.php \
  backend/tests/Feature/Hades/CanonicalHadesGraphProjectionTest.php \
  backend/tests/Feature/Dashboard/DashboardGraphExplorerApiTest.php
```

- [ ] **Step 8: Commit Task 5**

```bash
git add backend/app/Services/Graph/DashboardGraphSearchTerms.php \
  backend/app/Services/Graph/Neo4jCanonicalGraphProjector.php \
  backend/app/Services/Graph/DashboardGraphExplorerService.php \
  backend/tests/Unit/Services/Graph/DashboardGraphExplorerServiceTest.php \
  backend/tests/Feature/Hades/CanonicalHadesGraphProjectionTest.php
git commit -m "feat(graph): add safe canonical search aliases"
```

### Task 6: Persist and Display Coverage

**Files:**
- Create: `/home/ubuntu/dev-sandbox/backend/database/migrations/2026_07_15_000000_add_canonical_graph_coverage.php`
- Modify: `/home/ubuntu/dev-sandbox/backend/app/Services/Graph/CanonicalGraphProjectionService.php`
- Modify: `/home/ubuntu/dev-sandbox/backend/app/Services/Graph/DashboardGraphExplorerService.php`
- Modify: `/home/ubuntu/dev-sandbox/frontend/src/types/devboard.ts`
- Modify: `/home/ubuntu/dev-sandbox/frontend/src/components/devboard/GraphExplorer.tsx`
- Modify: `/home/ubuntu/dev-sandbox/frontend/src/components/devboard/GraphExplorer.test.tsx`

**Interfaces:**
- Projection envelope adds `coverage` as a nullable additive object.
- Frontend treats missing coverage as legacy/unknown and remains backward compatible.

- [ ] **Step 1: Write failing backend coverage test**

Queue a graph with partial contract coverage and assert the projection stores JSON containing languages, files discovered/analyzed/budget-omitted, routes promoted/omitted, tests promoted/omitted, and node-capacity omissions.

- [ ] **Step 2: Write failing frontend messaging tests**

Assert partial coverage renders “Indexed subset” with concrete counts and an empty search says “No match in the indexed subset”. Full coverage keeps “No matching symbols”.

- [ ] **Step 3: Verify RED in backend and frontend containers**

Run the targeted Pest and Vitest files and confirm missing coverage behavior.

- [ ] **Step 4: Add non-destructive coverage migration and persistence**

Add nullable `jsonb('coverage')` on PostgreSQL (`json` fallback where needed by tests). Store only bounded aggregate metadata, never raw artifact content. Down migration removes only the new nullable column.

- [ ] **Step 5: Add additive API and frontend rendering**

Include decoded coverage in `projectionEnvelope()`. Add optional TypeScript fields and a compact amber coverage banner. Do not trigger a graph query on initial page load.

- [ ] **Step 6: Run backend/frontend tests and builds GREEN**

```bash
docker compose -f docker-compose.devboard.yaml exec -T app php artisan test backend/tests/Feature/Dashboard/DashboardGraphExplorerApiTest.php
cd frontend && npm test -- --run src/components/devboard/GraphExplorer.test.tsx && npm run build
```

- [ ] **Step 7: Commit Task 6**

Commit backend and frontend coverage changes with message `feat(graph): expose canonical graph coverage`.

### Task 7: Local Agent Regression Suite and Packaging

**Files:**
- Modify only files already touched by Tasks 1-3 if regressions require corrections.
- Test: `tests/hermes_cli/test_hades_graph_contract.py`
- Test: `tests/hermes_cli/test_hades_backend_jobs.py`
- Test: `tests/hermes_cli/test_hades_backend_indexer_golden.py`

- [ ] **Step 1: Run focused graph suites**

```bash
.venv/bin/python -m pytest \
  tests/hermes_cli/test_hades_graph_contract.py \
  tests/hermes_cli/test_hades_backend_jobs.py \
  tests/hermes_cli/test_hades_backend_indexer_golden.py -q
```

- [ ] **Step 2: Run CLI/backend sync regressions**

```bash
.venv/bin/python -m pytest \
  tests/hermes_cli/test_hades_backend_cmd.py \
  tests/hermes_cli/test_hades_backend_sync_runner.py -q
```

- [ ] **Step 3: Verify install/package discovery**

Run the repository's package/build checks used for the installed `hades` entrypoint. Confirm a clean temporary `HERMES_HOME` can execute `hades backend status --json` without import errors.

- [ ] **Step 4: Commit any test-only corrections**

Use message `test(hades): cover language-agnostic graph extraction` only if Task 7 required additional test fixtures.

### Task 8: Backend Regression Suite, Migration, and Candidate Rebuild

- [ ] **Step 1: Run the full graph/backend slices**

```bash
docker compose -f docker-compose.devboard.yaml exec -T app php artisan test \
  backend/tests/Feature/Hades/CanonicalHadesGraphProjectionTest.php \
  backend/tests/Feature/CanonicalGraphProjectionTest.php \
  backend/tests/Feature/CanonicalGraphRebuildCommandTest.php \
  backend/tests/Feature/Dashboard/DashboardGraphExplorerApiTest.php \
  backend/tests/Unit/Services/Graph/DashboardGraphExplorerServiceTest.php
```

- [ ] **Step 2: Back up migration state and apply the additive migration**

Record current migration status and create the configured database backup bundle. Apply migrations inside the app container. Verify PostgreSQL and Neo4j containers remain healthy.

- [ ] **Step 3: Rebuild Carnovali from the stored artifact**

Run the canonical rebuild command for project `01KXJD0SV73EBGWKNE2EK3M4KD`, scope `workspace_binding`, binding `01KXJD1BDMQ2TFABMVJV6EFE8Q`. Verify the candidate recovers route names/URIs from the existing 146 route records before publication.

- [ ] **Step 4: Verify compatibility search**

Directly call `DashboardGraphExplorerService::search()` for `soggetti-attivi`, the full URI of a stored child route, and route name with/without `route:`. Do not expect the absent inherited root/test class until the fresh import.

### Task 9: Fresh Carnovali Import and Live Acceptance

- [ ] **Step 1: Install/run the updated local Hades Agent**

From `/Users/gabriele/Dev/sinervis/carnovali`, run:

```bash
hades backend status --json
hades backend sync
```

Ensure the running CLI resolves to the updated checkout/package before generating the artifact.

- [ ] **Step 2: Generate and upload a fresh graph artifact**

Run the normal Hades `populate_backend_ast`/awareness flow with the linked binding. Confirm the artifact reports PHP plus any other detected languages, the 10,000-file/byte budgets, explicit route/test nodes, and inherited Symfony routes.

- [ ] **Step 3: Project the fresh candidate atomically**

Wait for candidate projection completion. Verify node, relationship, and adjacency counts before switching current. Retain the previous current version on any failed verification.

- [ ] **Step 4: Execute live acceptance queries**

Verify:

```text
soggetti-attivi
/generale/soggetti-attivi/
contact_flock_roles_worker_home
route:contact_flock_roles_worker_home
AdminControllerBulkDeleteBehaviorTest
WorkerController
```

The first four must resolve route nodes; the test name must resolve a test node; `WorkerController` must retain class/method results.

- [ ] **Step 5: Browser acceptance**

Open the project Graph Explorer, search the same values, select route/test results, and confirm details and bounded neighborhood render without black screens, 404s, raw IDs, or local paths.

- [ ] **Step 6: Run health and sync verification**

Verify Docker services, Traefik routing, backend logs, Hades sync, and absence of new 401/404/500 bursts.

### Task 10: Review, Integration, and Handoff

- [ ] **Step 1: Review both repository diffs against the approved specification**

Check contract compatibility, cache/tool footprint, security isolation, bounded behavior, migration safety, and absence of unrelated edits.

- [ ] **Step 2: Run fresh completion verification**

Repeat all focused local and backend test commands plus frontend build and live acceptance. Record exact pass/fail counts.

- [ ] **Step 3: Merge only after verification**

Merge the isolated local-agent branch into `main`, then the backend branch into backend `main`. Do not force-push or rewrite unrelated history.

- [ ] **Step 4: Report exact outcome and remaining limitations**

Report commits, migrations, live graph version, query results, coverage, tests, deployment state, and any explicitly deferred items. Transition next to the separate wiki verification/generation specification.
