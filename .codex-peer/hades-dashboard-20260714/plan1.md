# Frontend Resilience + Graph Explorer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent render exceptions from producing a black Hades dashboard, and replace the circular graph preview at `/projects/:projectId/graph` with a bounded, project-scoped explorer backed by a browser-safe dashboard contract. A selected preview node must be queryable through a reusable opaque backend handle; internal Neo4j identifiers, plugin tokens, and full graph payloads must never reach the browser.

**Architecture:** Plan 1 is implemented first. Plan 2 consumes its public graph/source metadata conventions, and Plan 3 consumes its graph status and graph evidence conventions. The existing `POST /api/plugin/v1/projects/{project}/graph/query` remains plugin-token protected and unchanged for plugin callers. A new `DashboardGraphExplorerController` handles dashboard sessions and delegates to `DashboardGraphExplorerService`, which delegates canonical scope/projection work to `GraphQueryService`/`CanonicalGraphQueryService`. Each canonical node is materialized with a deterministic non-reversible `public_handle`: `gh1_` plus a base64url HMAC of project, scope, graph version, and canonical external id. The browser never decodes it; the backend resolves it by querying the current project/scope/version for the indexed handle. Search, neighborhood, path, and typed impact are all bounded and cursorable. The frontend fetches only dashboard URLs, constructs memoized indexes once per response, and renders only a selected subgraph.

**Tech Stack:** Laravel 13, PHP 8.3, Pest, PostgreSQL, Neo4j, `CanonicalGraphQueryService`, React 19, TypeScript 5.4, CRA/CRACO, Radix/Tailwind, Jest with the CRA jsdom environment.

## Global Constraints

- Implement this plan before `2026-07-14-knowledge-human-machine-views.md`, then implement that plan before `2026-07-14-project-clarity-contextual-triage.md`.
- Work in the existing clean checkout on branch `feature/canonical-graph-foundation-20260712` at baseline `82f9f469`; do not create a nested worktree.
- The project backend is the source of truth. Every graph read must verify the route project, selected `repository` or `workspace_binding`, and authenticated dashboard reader role. Never accept a scope from another project.
- Do not modify `project/`, migrations, database contents, containers, or git history while this plan is being reviewed. The task commands below are for a future execution worker; no commit command in this document is to be executed during planning.
- Do not expose plugin credentials or `POST /api/plugin/v1/...` to the browser. The browser uses Laravel dashboard cookies/XSRF through `frontend/src/api/httpApi.ts`.
- Do not expose Neo4j `external_id`, internal numeric ids, raw paths, raw edge endpoints, artifact ids, or private provenance. A public node `handle` is the only query identifier; a stable presentation alias may remain as `id` for compatibility but is never accepted by the query endpoint.
- Preserve the existing data-minimized `DashboardApiReader::graph()` preview and its source fallback behavior. The explorer may add `handle` to preview nodes, but it must not make the existing preview less restrictive.
- The live graph vocabulary is exact: call family `CALLS`, `CALLS_METHOD`, `STATIC_CALL`; dependency family `USES_DEPENDENCY`, `INSTANTIATES`, `EXTENDS`, `USES_FORM_REQUEST`, `THROWS_EXCEPTION`, `API_RESOURCE_REF`; route family `ROUTE_HANDLER`; test family `TEST_COVERS_SYMBOL`, `TEST_IMPORTS`, `TEST_COVERS_ROUTE`; table family `QUERY_TABLE`, `ELOQUENT_QUERY`. Do not materialize duplicate legacy relationship types. Tests use these exact names.
- The canonical node-kind map is exact: `method`, `class`, `method_reference`, `external_class`, `table`, `route`, `trait`, `external_symbol`, `interface`, and `file`; anything else is `unknown`. Human labels come only from sanitized source properties. Remove any fallback label shaped like `<kind> <hades-public-*>`; never use `handle` or `id` as a human label. Unresolved nodes remain available to Machine/detail responses, while the default canvas excludes every non-renderable node and every edge incident to an excluded endpoint.
- Search must use a real Neo4j FULLTEXT index/query. The projector materializes only sanitized `public_search_name`, `public_search_label`, and `public_search_path` properties and creates `canonical_node_search` over `graph_version` plus those `public_search_*` properties; raw `external_id` and raw `path` remain separate and are not indexed. Search calls `db.index.fulltext.queryNodes` with `graph_version:"<escaped>" AND (public_search_name:<escaped> OR public_search_label:<escaped> OR public_search_path:<escaped>)`, where Lucene input is escaped, then applies the defensive `WHERE node.graph_version = $graphVersion` before ordering/limiting. There is no `CONTAINS` scan disguised by a limit. If FULLTEXT is unavailable during a controlled rebuild, only exact/prefix lookup against indexed `graph_version`/`public_search_*` fields is permitted, with a bounded `search_unavailable` reason.
- The current `CanonicalGraphVersion`/projection stores `handle_key_version: "gh1"` and `handle_key_fingerprint: hash_hmac("sha256", "hades.graph.handle.v1", APP_KEY)`. Dashboard preview and every explorer query require `status=ready`, non-null `active_graph_version`, and a fingerprint equal to the current application key fingerprint; mismatch returns `graph_projection_rebuild_required` and makes preview nodes non-clickable.
- APP_KEY rotation creates a new candidate projection with the new fingerprint and rematerialized handles, verifies it, and publishes its `active_graph_version` atomically. The old projection remains non-current; during the mismatch window no old preview handle is emitted and an old handle resolves as `404 node_not_found`.
- Current-projection response statistics define `unknown_kind_count` as the count of unknown-kind nodes, `missing_label_count` as the count of nodes without a human label, and `excluded_node_count` as the cardinality of the union of those two node sets; therefore `excluded_node_count >= max(unknown_kind_count, missing_label_count)`. The contract test rejects any `unknown` node whose label is `hades-public-*`.
- All response envelopes state `returned`, `limit`, `has_more`, `truncated`, `quality`, and `source`. They also expose the published `active_graph_version` when a projection is ready. `limit` is at most 50 for detail/relationship operations and at most 100 for overview/search; depth is at most 3, with impact fixed at two hops.
- UI copy remains Hades-branded, with accessible labels, keyboard-operable controls, explicit loading/empty/error states, and no full-graph SVG render. Existing source fallback regression coverage in `frontend/src/components/devboard/Badges.test.tsx` stays green.
- Every production behavior starts with a failing test. Each task ends with a focused test command, `git diff --check -- <exact files>`, and a commit command; do not add a push command.

---

## Task 1: Add the global Hades render-error boundary

**Files:**

- Create `frontend/src/components/devboard/AppErrorBoundary.tsx`.
- Create `frontend/src/components/devboard/AppErrorBoundary.test.tsx`.
- Modify `frontend/src/index.js` around `ReactDOM.createRoot`, `React.StrictMode`, and `QueryClientProvider`.

**Consumed/produced interfaces:**

    export interface AppErrorBoundaryProps { children: React.ReactNode }
    interface AppErrorBoundaryState { error: Error | null; resetKey: number }

The component produces a Hades-branded fallback with `role="alert"`, a safe human-readable error id derived from `resetKey` rather than the exception message, a `Try again` button that clears `error` and increments `resetKey`, and a `Reload dashboard` button that calls `window.location.reload()`. It must not render an exception stack or untrusted exception text.

- [ ] Add the jsdom regression test with a child that throws on the first render, assert the alert and Hades branding are visible, click `Try again`, and assert the healthy child is visible after the boundary reset.
- [ ] Run the RED command and record the expected failure: `cd frontend && CI=true corepack yarn test --watchAll=false --runInBand src/components/devboard/AppErrorBoundary.test.tsx`; expected failure is that the new test file/component cannot be resolved.
- [ ] Implement `AppErrorBoundary` with `static getDerivedStateFromError`, `componentDidCatch` logging only a bounded `console.error("Hades dashboard render error", { componentStack })`, and a reset button that remounts children with `key={resetKey}`.
- [ ] Wrap `<QueryClientProvider client={queryClient}><App /></QueryClientProvider>` with `<AppErrorBoundary>` inside `React.StrictMode` in `frontend/src/index.js`, keeping the existing `AuthProvider`/router ownership inside `App` and avoiding duplicate providers.
- [ ] Run the GREEN command: `cd frontend && CI=true corepack yarn test --watchAll=false --runInBand src/components/devboard/AppErrorBoundary.test.tsx`; expected result is one passing jsdom regression test with no uncaught render exception.
- [ ] Run the focused suite: `cd frontend && CI=true corepack yarn test --watchAll=false --runInBand src/components/devboard/AppErrorBoundary.test.tsx src/components/devboard/Badges.test.tsx`; expected result is both boundary tests and both existing source metadata tests passing.
- [ ] Check the exact diff with `git diff --check -- frontend/src/index.js frontend/src/components/devboard/AppErrorBoundary.tsx frontend/src/components/devboard/AppErrorBoundary.test.tsx`.
- [ ] Prepare the task commit command, without executing it during planning: `git add frontend/src/index.js frontend/src/components/devboard/AppErrorBoundary.tsx frontend/src/components/devboard/AppErrorBoundary.test.tsx && git commit -m "feat(frontend): add branded render error boundary"`.

## Task 2: Correct canonical semantic edges and materialize the non-reversible public node handle

**Files:**

- Create `backend/app/Services/Graph/DashboardGraphPublicHandle.php`.
- Create `backend/app/Services/Graph/DashboardGraphExplorerService.php`.
- Create `backend/app/Services/Graph/DashboardGraphExplorerCursor.php`.
- Modify `backend/app/Services/Graph/CanonicalGraphQueryService.php`.
- Modify `backend/app/Services/Graph/Neo4jCanonicalGraphProjector.php`.
- Modify `backend/app/Dashboard/DashboardApiReader.php` canonical preview mapping.
- Create `backend/tests/Unit/Services/Graph/DashboardGraphPublicHandleTest.php`.
- Create `backend/tests/Unit/Services/Graph/DashboardGraphExplorerCursorTest.php`.
- Modify `backend/tests/Feature/Dashboard/DashboardApiContractTest.php`.
- Modify `backend/tests/Feature/Graph/CanonicalGraphTraverseNeo4jReadOnlyTest.php` with deterministic fake-client coverage, while retaining both existing environment-gated read-only tests.
- Modify `backend/tests/Feature/Assistants/AiAgentReadToolsTest.php` only for the stale canonical-ready baseline fixture and graph version/scope assertions.

**Consumed/produced interfaces:**

   final class DashboardGraphPublicHandle
   {
       public function forNode(string $projectId, string $scopeType, string $scopeId, string $graphVersion, string $externalId): string;
        public function isWellFormed(string $handle): bool;
       public function keyVersion(): string;
       public function keyFingerprint(): string;
   }

    final class DashboardGraphExplorerCursor
    {
        public function encode(string $projectId, ?string $sourceScopeType, ?string $sourceScopeId, string $activeGraphVersion, string $queryType, string $query, string $sortKey): string;
        public function decode(string $cursor): array;
    }

    final class DashboardGraphExplorerService
    {
        public function scopes(string $projectId, int $limit = 50, ?string $cursor = null): array;
        public function overview(string $projectId, string $scopeType, string $scopeId): array;
        public function search(string $projectId, string $scopeType, string $scopeId, string $query, int $limit = 50, ?string $cursor = null): array;
        public function detail(string $projectId, string $scopeType, string $scopeId, string $handle, int $limit = 50): array;
        public function neighborhood(string $projectId, string $scopeType, string $scopeId, string $handle, string $direction, int $maxDepth, int $limit, array $families = []): array;
        public function path(string $projectId, string $scopeType, string $scopeId, string $fromHandle, string $toHandle, int $maxDepth, int $limit): array;
        public function impact(string $projectId, string $scopeType, string $scopeId, string $handle, int $limit = 50): array;
    }

The handle is `gh1_<base64url(hmac_sha256(canonical_json, APP_KEY))>`, where `canonical_json` is `json_encode` with exactly the ordered keys `v`, `project_id`, `scope_type`, `scope_id`, `graph_version`, and `external_id`; `graph_version` is the published `active_graph_version`, not merely the origin `graph_version`. The output contains only the `gh1_` prefix and hash, is non-reversible, and exposes no payload. `Neo4jCanonicalGraphProjector` materializes it as `public_handle` on every `CanonicalGraphNode` and indexes the stored properties `project_id`, `source_scope_type`, `source_scope_id`, `graph_version`, and `public_handle`. Runtime resolution performs one direct indexed lookup against the current published `active_graph_version` and matching `source_scope_type`/`source_scope_id`; it does not decode, enumerate candidate nodes, or scan.

`DashboardGraphExplorerCursor` encodes a canonical JSON payload containing `project_id`, nullable `source_scope_type`/`source_scope_id`, published `active_graph_version`, `query_type`, normalized `query`, and `sort_key`, then signs it with HMAC using the current APP_KEY. Search order is `score DESC, handle ASC`; scope order is `source_scope_type ASC, source_scope_id ASC`. Every list query fetches `limit + 1`, and the next cursor is issued only from the deterministic last sort key. Decode verifies the signature and the service compares every payload field with the current project, selected source scope, active graph version, query type, and query before use.

The canonical query service must use one family predicate for adjacency/path/traverse with the exact live types above, always passing the published `active_graph_version` to Neo4j even when it differs from the origin `graph_version`. `Neo4jCanonicalGraphProjector` must materialize `public_handle`, create a lookup index over the stored `project_id`, `source_scope_type`, `source_scope_id`, `graph_version`, and `public_handle` properties, and create the `canonical_node_search` FULLTEXT index over `graph_version`, `public_search_name`, `public_search_label`, and `public_search_path` only. Search must call `db.index.fulltext.queryNodes` with `graph_version:"<escaped>" AND (public_search_name:<escaped> OR public_search_label:<escaped> OR public_search_path:<escaped>)`, then apply the defensive `WHERE node.graph_version = $activeGraphVersion`; raw `external_id` and raw local path must not be searchable.

- [ ] Write handle/query tests for deterministic canonical JSON output, `isWellFormed` acceptance/rejection, tampered-hash rejection through the direct current-scope lookup, malformed `gh1_` rejection, cross-project/scope/version non-resolution, key-version/fingerprint mismatch, and absence of any decode or payload API.
- [ ] Run the RED command: `cd backend && php artisan test --filter=DashboardGraphPublicHandleTest`; expected failure is that the new handle class/test does not exist.
- [ ] Run the cursor RED command: `cd backend && php artisan test --filter=DashboardGraphExplorerCursorTest`; expected failure is that the signed cursor class/test does not exist; the test cases cover signature tampering and cross-project, source-scope, active-version, query-type, and query mismatches.
- [ ] Implement the handle value object with a fixed-order array of `v`, `project_id`, `scope_type`, `scope_id`, `graph_version`, and `external_id`, `json_encode(..., JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES)`, `hash_hmac('sha256', $canonicalJson, (string) config('app.key'), true)`, base64url encoding, and only `gh1_<hash>` output; implement `isWellFormed`, `keyVersion`, and `keyFingerprint`, reject malformed input with the single bounded `invalid_handle` error, and never expose canonical ids or a payload.
- [ ] Implement `DashboardGraphExplorerCursor` as canonical JSON plus an HMAC signature containing project, nullable source scope, active graph version, query type/query, and sort key; reject tampering and any project/scope/version/query mismatch as `invalid_cursor` without revealing cursor payload.
- [ ] Apply deterministic `score DESC, handle ASC` search ordering and `source_scope_type ASC, source_scope_id ASC` scope ordering, fetch `limit + 1` in every cursorable list, and derive `next_cursor` from the final retained sort key.
- [ ] Add rotation tests in `DashboardApiContractTest` and `CanonicalGraphProjectionTest`: rotate APP_KEY, require a new fingerprint, publish a newly projected active graph atomically, assert mismatch returns `graph_projection_rebuild_required` with non-clickable/no preview handles, and assert an old preview handle resolves as `404 node_not_found` rather than being reused.
- [ ] Add a fake Neo4j fixture using only the exact live edge types `CALLS`, `CALLS_METHOD`, `STATIC_CALL`, `USES_DEPENDENCY`, `INSTANTIATES`, `EXTENDS`, `USES_FORM_REQUEST`, `THROWS_EXCEPTION`, `API_RESOURCE_REF`, `ROUTE_HANDLER`, `TEST_COVERS_SYMBOL`, `TEST_IMPORTS`, `TEST_COVERS_ROUTE`, `QUERY_TABLE`, and `ELOQUENT_QUERY`; assert callers/callees and impact classify them without materializing `ROUTES_TO`, `TESTS`, or `READS_TABLE` duplicates.
- [ ] Assert projector writes and every resolver/query fixture use the real Neo4j property names `source_scope_type` and `source_scope_id`, never `scope_type` or `scope_id` on `CanonicalGraphNode`.
- [ ] Add the projector/FULLTEXT RED assertion before implementation: `CanonicalGraphProjectionTest` must expect `public_search_name`, `public_search_label`, `public_search_path`, graph-version Lucene filtering, and no raw `external_id`/local `path` indexing; run `cd backend && php artisan test --filter='CanonicalGraphProjectionTest'`; expected failure is missing sanitized properties/index statements.
- [ ] Run the RED command for the semantic behavior before implementation is complete: `cd backend && php artisan test --filter='CanonicalGraphTraverseNeo4jReadOnlyTest|CanonicalGraphProjectionTest'`; expected failure is an empty caller/callee result or a missing index assertion for the live edge vocabulary.
- [ ] Modify `CanonicalGraphQueryService` so `callers`, `callees`, `path`, and `traverse` use the exact family predicates, preserve `max_depth`/`limit` bounds, resolve a ready projection’s non-null `active_graph_version`, pass that value to every Neo4j query even when origin `graph_version` differs, and return normalized `edge_type`, `family`, source/target handles, and source/target kinds internally for the explorer adapter; test the active-versus-origin case.
- [ ] Add `DashboardGraphExplorerService` methods that call `CanonicalGraphQueryService` for canonical reads, call `GraphQueryService` only through its existing bounded adapter where the caller contract requires it, resolve a requested handle with `MATCH (node:CanonicalGraphNode {project_id: $projectId, source_scope_type: $scopeType, source_scope_id: $scopeId, graph_version: $graphVersion, public_handle: $handle}) LIMIT 1`, and map impact records to `{handle, kind, label, distance, family, edge_types, why}` without candidate scans or internal ids.
- [ ] Implement impact as bounded two-hop reverse traversal over all semantic families, grouping explanations by `family`, exact live `edge_types`, and node kind (`method`, `class`, `method_reference`, `external_class`, `table`, `route`, `trait`, `external_symbol`, `interface`, `file`) so each route, test, table, or symbol reports why it is affected rather than returning a flat inbound list.
- [ ] Add `DashboardApiContractTest` before implementation: fixture the current ready projection and dashboard preview, run `cd backend && php artisan test tests/Feature/Dashboard/DashboardApiContractTest.php`, and observe failure because preview nodes do not yet emit coherent `gh1_` handles.
- [ ] Add the current-projection contract fixture with exactly 200 nodes, including 70 `unknown` kinds and 145 legacy-looking `hades-public-*` values; assert the public response has `unknown_kind_count`, `missing_label_count`, and `excluded_node_count`, `excluded_node_count >= max(unknown_kind_count, missing_label_count)`, rejects `unknown` plus `hades-public-*` as a human label, keeps unresolved nodes only in Machine/detail data, excludes them from the default canvas, and returns only edges whose endpoints remain in the canvas node set.
- [ ] Update `DashboardApiReader` current-projection preview mapping so every node emits a handle from the same logical project/scope/active-graph-version/external-id inputs while reading stored `source_scope_type`/`source_scope_id`; remove any fallback label shaped like `<kind> <hades-public-*>`, use only a sanitized source label or `null`, and never use `id` or `handle` as the human label.
- [ ] Add the stale baseline assertion in `AiAgentReadToolsTest`: fixture a canonical projection with `status=ready`, exact `graph_version`, and project/scope fields, then assert the read-tool context uses those fields rather than a legacy `snapshot_id`; run `cd backend && php artisan test tests/Feature/Assistants/AiAgentReadToolsTest.php` and observe the old fixture/assertion failure before implementation.
- [ ] Update the baseline fixture/expectation to use canonical `graph_version` and scope while preserving the existing read-tool behavior; legacy snapshot identifiers are not accepted as readiness fields.
- [ ] Run the GREEN command: `cd backend && php artisan test --filter='DashboardGraphPublicHandleTest|DashboardGraphExplorerCursorTest|DashboardApiContractTest|AiAgentReadToolsTest|CanonicalGraphTraverseNeo4jReadOnlyTest|CanonicalGraphProjectionTest'`; expected result is all deterministic tests passing, with environment-gated Neo4j tests skipped unless their required variables are present.
- [ ] Check the exact diff with `git diff --check -- backend/app/Services/Graph/DashboardGraphPublicHandle.php backend/app/Services/Graph/DashboardGraphExplorerCursor.php backend/app/Services/Graph/DashboardGraphExplorerService.php backend/app/Services/Graph/CanonicalGraphQueryService.php backend/app/Services/Graph/Neo4jCanonicalGraphProjector.php backend/app/Dashboard/DashboardApiReader.php backend/tests/Unit/Services/Graph/DashboardGraphPublicHandleTest.php backend/tests/Unit/Services/Graph/DashboardGraphExplorerCursorTest.php backend/tests/Feature/Dashboard/DashboardApiContractTest.php backend/tests/Feature/Assistants/AiAgentReadToolsTest.php backend/tests/Feature/CanonicalGraphProjectionTest.php backend/tests/Feature/Graph/CanonicalGraphTraverseNeo4jReadOnlyTest.php`.
- [ ] Prepare the task commit command, without executing it during planning: `git add backend/app/Services/Graph/DashboardGraphPublicHandle.php backend/app/Services/Graph/DashboardGraphExplorerCursor.php backend/app/Services/Graph/DashboardGraphExplorerService.php backend/app/Services/Graph/CanonicalGraphQueryService.php backend/app/Services/Graph/Neo4jCanonicalGraphProjector.php backend/app/Dashboard/DashboardApiReader.php backend/tests/Unit/Services/Graph/DashboardGraphPublicHandleTest.php backend/tests/Unit/Services/Graph/DashboardGraphExplorerCursorTest.php backend/tests/Feature/Dashboard/DashboardApiContractTest.php backend/tests/Feature/Assistants/AiAgentReadToolsTest.php backend/tests/Feature/CanonicalGraphProjectionTest.php backend/tests/Feature/Graph/CanonicalGraphTraverseNeo4jReadOnlyTest.php && git commit -m "feat(graph): expose semantic canonical relationships safely"`.

## Task 3: Expose the dashboard graph explorer controller with scope and validation contracts

**Files:**

- Create `backend/app/Http/Controllers/Dashboard/Api/DashboardGraphExplorerController.php`.
- Modify `backend/routes/web.php` at the exact authenticated dashboard route group; leave the plugin route in `backend/routes/api.php` unchanged.
- Create `backend/tests/Feature/Dashboard/DashboardGraphExplorerApiTest.php`.

**Consumed/produced interfaces:**

The route is `POST /api/dashboard/projects/{project}/graph/query` and uses `ChecksDashboardRoles::abortUnlessDashboardReader`. The request body is:

    {
      "type": "scopes|overview|search|detail|neighborhood|path|impact",
      "scope_type": "repository|workspace_binding",
      "scope_id": "project-owned opaque scope id",
      "query": "bounded search text",
      "node_handle": "gh1...",
      "from_handle": "gh1...",
      "to_handle": "gh1...",
      "direction": "in|out|any",
      "families": ["call", "dependency", "route", "test", "table"],
      "max_depth": 2,
      "limit": 50,
      "cursor": null
    }

`scope_type`/`scope_id` are required for every type except `scopes`; `search` requires a 1–160 character `query`; `detail`, `neighborhood`, and `impact` require `node_handle`; `path` requires both handles; `max_depth` is 1–3, with impact rejecting values other than 2; `limit` is 1–100 for `scopes`/`search` and 1–50 otherwise; cursors are signed opaque strings max 512 characters. `graph_version`, `external_id`, `symbol_id`, `from_symbol_id`, `to_symbol_id`, and plugin headers are prohibited.

The JSON envelope is:

    [
        'protocol_version' => 'v1', 'project_id' => $projectId, 'query_type' => $type,
        'found' => true, 'reason' => null, 'scope' => ['type' => $scopeType, 'id' => $scopeId],
        'projection' => ['status' => 'ready', 'quality' => 'verified', 'generated_at' => $iso, 'active_graph_version' => $activeGraphVersion, 'node_count' => 5000, 'relationship_count' => 12000, 'unknown_kind_count' => 70, 'missing_label_count' => 145, 'excluded_node_count' => 145],
        'items' => [], 'edges' => [], 'returned' => 0, 'limit' => $limit,
        'next_cursor' => null, 'has_more' => false, 'truncated' => false,
        'source' => ['type' => 'canonical_graph', 'status' => 'verified_from_code', 'origin' => 'canonical projection'],
    ]

Projection unavailable returns HTTP 200 with `found:false`, `reason:"graph_projection_not_ready"`, and the safe projection envelope. Missing scope returns HTTP 422 `scope_required` only when a project has multiple selectable scopes and the body omitted scope. Invalid handle returns HTTP 404 `node_not_found` or HTTP 422 `invalid_handle` without the canonical id. A project from another scope always returns 403/404 according to existing dashboard authorization behavior and never leaks whether the foreign handle exists.

- [ ] Add feature tests for Admin/PM/Developer reader access, unauthenticated access, one-scope defaulting, multi-scope `scopes` response, cross-project scope denial, and the plugin URL remaining token-protected.
- [ ] Run the RED command: `cd backend && php artisan test --filter=DashboardGraphExplorerApiTest`; expected failure is that the controller route and test file do not exist.
- [ ] Add the controller with `Request::validate` rules matching the body contract, project ownership checks, `DashboardGraphExplorerService` dispatch by the seven allowed types, and explicit JSON responses for projection unavailable and bounded validation failures.
- [ ] Register exactly `Route::post('/projects/{project}/graph/query', [DashboardGraphExplorerController::class, 'query'])` in `backend/routes/web.php` inside the existing `/api/dashboard` prefix and authenticated dashboard middleware/reader group; do not alter `Route::post('/projects/{project}/graph/query', GraphQueryController::class)` in `backend/routes/api.php` under `plugin/v1`.
- [ ] Add tests using a project-owned repository and workspace binding plus a second project, asserting `POST /api/dashboard/projects/{project}/graph/query` never accepts a preview `id` as `node_handle`, never accepts an internal id field, and never returns `external_id`, `artifact_id`, `projection_id`, or plugin credential fields.
- [ ] Add tests for the exact live edge vocabulary (`CALLS`, `CALLS_METHOD`, `STATIC_CALL`, `USES_DEPENDENCY`, `INSTANTIATES`, `EXTENDS`, `USES_FORM_REQUEST`, `THROWS_EXCEPTION`, `API_RESOURCE_REF`, `ROUTE_HANDLER`, `TEST_COVERS_SYMBOL`, `TEST_IMPORTS`, `TEST_COVERS_ROUTE`, `QUERY_TABLE`, `ELOQUENT_QUERY`) and assert response edge families plus impact `why` values are present.
- [ ] Add tests for `limit=0`, `limit=101`, `max_depth=4`, missing query/handles, invalid family, oversized cursor, unavailable projection, current ready projection, and returned count/truncation envelope.
- [ ] Run the GREEN command: `cd backend && php artisan test --filter=DashboardGraphExplorerApiTest`; expected result is all authorization, validation, projection, handle, semantic edge, and envelope tests passing.
- [ ] Run the focused backend suite by exact file paths, keeping the plugin test separate from the filter: `cd backend && php artisan test tests/Feature/Dashboard/DashboardGraphExplorerApiTest.php tests/Feature/Plugin/GraphQueryApiTest.php tests/Feature/CanonicalGraphProjectionTest.php tests/Feature/Graph/CanonicalGraphTraverseNeo4jReadOnlyTest.php`.
- [ ] Check the exact diff with `git diff --check -- backend/app/Http/Controllers/Dashboard/Api/DashboardGraphExplorerController.php backend/routes/web.php backend/tests/Feature/Dashboard/DashboardGraphExplorerApiTest.php`.
- [ ] Prepare the task commit command, without executing it during planning: `git add backend/app/Http/Controllers/Dashboard/Api/DashboardGraphExplorerController.php backend/routes/web.php backend/tests/Feature/Dashboard/DashboardGraphExplorerApiTest.php && git commit -m "feat(dashboard): add scoped graph explorer query API"`.

## Task 4: Add the typed frontend dashboard graph API contract and mock

**Files:**

- Modify `frontend/src/types/devboard.ts` in the Graph section.
- Modify `frontend/src/api/devboardApi.ts`.
- Modify `frontend/src/api/httpApi.ts`.
- Modify `frontend/src/api/mockApi.ts` and `frontend/src/api/mockData.ts`.
- Modify `frontend/src/api/httpApi.test.ts` and `frontend/src/api/mockApi.test.ts`.

**Consumed/produced interfaces:**

    export type DashboardGraphQueryType = "scopes" | "overview" | "search" | "detail" | "neighborhood" | "path" | "impact";
    export type DashboardGraphScopeType = "repository" | "workspace_binding";
    export type DashboardGraphDirection = "in" | "out" | "any";
    export type DashboardGraphFamily = "call" | "dependency" | "route" | "test" | "table";
    export interface DashboardGraphQueryRequest { type: DashboardGraphQueryType; scope_type?: DashboardGraphScopeType; scope_id?: string; query?: string; node_handle?: string; from_handle?: string; to_handle?: string; direction?: DashboardGraphDirection; families?: DashboardGraphFamily[]; max_depth?: 1 | 2 | 3; limit?: number; cursor?: string | null; }
    export type DashboardGraphNodeKind = "method" | "class" | "method_reference" | "external_class" | "table" | "route" | "trait" | "external_symbol" | "interface" | "file" | "unknown";
    export interface DashboardGraphNode { handle: string; id: string; label: string | null; kind: DashboardGraphNodeKind; repository: string; degree: number; risk: RiskLevel; source: SourceMeta; semantic_family?: DashboardGraphFamily | "other"; why?: string; distance?: number; edge_types?: string[]; }
    export interface DashboardGraphEdge { id: string; from_handle: string; to_handle: string; edge_type: string; family: DashboardGraphFamily | "other"; why?: string; }
    export interface DashboardGraphResponse { protocol_version: "v1"; project_id: string; query_type: DashboardGraphQueryType; found: boolean; reason: string | null; scope: GraphSourceScope | null; projection: { status: string; quality: string | null; generated_at: string | null; active_graph_version: string | null; node_count: number; relationship_count: number; unknown_kind_count: number; missing_label_count: number; excluded_node_count: number }; items: DashboardGraphNode[]; edges: DashboardGraphEdge[]; returned: number; limit: number; next_cursor: string | null; has_more: boolean; truncated: boolean; source: SourceMeta; }

Extend `DevboardApi` with `queryProjectGraph(projectId: string, request: DashboardGraphQueryRequest): Promise<DashboardGraphResponse>`. `httpApi` must POST to `/api/dashboard/projects/${projectId}/graph/query`, JSON encode exactly the request, and never construct a plugin URL. `mockApi` returns the same envelope and handles a preview node by its `handle`, not by `GraphNode.id`.

- [ ] Add the request/response types and `DevboardApi.queryProjectGraph` signature; keep `getGraph` for overview/source fallback compatibility.
- [ ] Run the RED command: `cd frontend && CI=true corepack yarn test --watchAll=false --runInBand src/api/httpApi.test.ts src/api/mockApi.test.ts`; expected failure is the missing `queryProjectGraph` contract and missing URL/body assertions.
- [ ] Implement `httpApi.queryProjectGraph` using a typed POST request to `/api/dashboard/projects/${projectId}/graph/query` with the exact request body and add an API test asserting the exact dashboard URL, JSON body, and absence of `/api/plugin/v1`.
- [ ] Add mock scopes, overview, search, detail, neighborhood, path, and impact responses in `mockData.ts` with at least two scopes, one selected node, exact live types `CALLS_METHOD`, `USES_DEPENDENCY`, `ROUTE_HANDLER`, `TEST_COVERS_SYMBOL`, and `QUERY_TABLE`, plus a truncated impact record.
- [ ] Implement `mockApi.queryProjectGraph` with the same bounded limits and `reason` values as the controller, including a response where `next_cursor` is non-null and `truncated` is true.
- [ ] Run the GREEN command: `cd frontend && CI=true corepack yarn test --watchAll=false --runInBand src/api/httpApi.test.ts src/api/mockApi.test.ts`; expected result is the new URL/body test and mock contract tests passing.
- [ ] Check the exact diff with `git diff --check -- frontend/src/types/devboard.ts frontend/src/api/devboardApi.ts frontend/src/api/httpApi.ts frontend/src/api/mockApi.ts frontend/src/api/mockData.ts frontend/src/api/httpApi.test.ts frontend/src/api/mockApi.test.ts`.
- [ ] Prepare the task commit command, without executing it during planning: `git add frontend/src/types/devboard.ts frontend/src/api/devboardApi.ts frontend/src/api/httpApi.ts frontend/src/api/mockApi.ts frontend/src/api/mockData.ts frontend/src/api/httpApi.test.ts frontend/src/api/mockApi.test.ts && git commit -m "feat(frontend): add dashboard graph query contract"`.

## Task 5: Build the memoized graph explorer model and page

**Files:**

- Create `frontend/src/pages/graphExplorerModel.ts`.
- Create `frontend/src/pages/graphExplorerModel.test.ts`.
- Create `frontend/src/components/devboard/GraphExplorer.tsx`.
- Modify `frontend/src/pages/GraphPage.tsx`.

**Consumed/produced interfaces:**

    export interface GraphViewModel { nodesByHandle: ReadonlyMap<string, DashboardGraphNode>; edgesByHandle: ReadonlyMap<string, readonly DashboardGraphEdge[]>; incomingByHandle: ReadonlyMap<string, readonly DashboardGraphNode[]>; outgoingByHandle: ReadonlyMap<string, readonly DashboardGraphNode[]>; selectedEdges: readonly DashboardGraphEdge[]; visibleNodes: readonly DashboardGraphNode[]; }
    export function buildGraphViewModel(response: DashboardGraphResponse, selectedHandle: string | null): GraphViewModel;

`buildGraphViewModel` makes one node map and one edge adjacency map per response. It never calls `edges.filter(...)` inside React render and never derives relationships from `GraphNode.id`. `GraphExplorer` consumes `GraphViewModel`, `DashboardGraphResponse`, and a callback `(request: DashboardGraphQueryRequest) => Promise<void>`.

The page keeps both `/projects/:projectId/graph` and `/graph`, defaults to the existing GET overview/stats, uses `scopes` when the backend returns `scope_required` or multiple scopes, and then POSTs bounded `search`, `detail`, `neighborhood`, `path`, and `impact` requests. The global `/graph` route first renders a read-only project selector and overview; it must not POST any graph query until a real `projectId` is selected, and no request URL may contain `/projects/undefined`. Search is debounced at 250 ms, limited to 50 results, and writes `symbol` and `scope_type`/`scope_id` query parameters with `useSearchParams`. A selected preview node carries its opaque `handle` in state and the URL, never its presentation alias as a query id.

- [ ] Add model tests for empty response, one selected handle, inbound/outbound adjacency, unknown handle, duplicate edges, and a 5,000-node/12,000-edge synthetic response proving the model creates maps without render-time N×E filtering.
- [ ] Run the RED command: `cd frontend && CI=true corepack yarn test --watchAll=false --runInBand src/pages/graphExplorerModel.test.ts`; expected failure is the missing model module and exported function.
- [ ] Implement `buildGraphViewModel` with `Map` construction, `nodesByHandle`, `incomingByHandle`, `outgoingByHandle`, and selected-edge extraction; use `Object.freeze` only for returned arrays and do not mutate API responses.
- [ ] Add `GraphExplorer` with an accessible search `<label>`, result buttons, scope `<select>`, retry button, empty states, projection quality/source metadata, truncation text, and a compact relationship SVG whose node set is only the selected node plus returned neighborhood/path/impact items; filter the default canvas to renderable nodes and retain only edges whose `from_handle` and `to_handle` are both present.
- [ ] Replace the full circular `GraphCanvas` usage in `GraphPage.tsx` with `GraphExplorer`; retain the same route, GET overview/stats source fallback, and explicit `graph_projection_not_ready` rendering.
- [ ] Add the Symbol, Callers, Dependencies/Callees, and Impact panels. The frontend loads Callers and Callees in parallel with `Promise.all` by sending two `type:"neighborhood"` requests with explicit `direction:"in"` and `direction:"out"`, each with `families:["call","dependency"]`; it never sends nonexistent `callers`/`callees` query types. Impact sends `max_depth: 2`, and every result shows `why`, edge family/type, distance, `returned`, `truncated`, and source/projection quality.
- [ ] Add keyboard activation, `aria-live` loading/error status, focusable results, an empty search state, an unavailable projection state, and an error state that preserves the selected handle and offers retry.
- [ ] Run the GREEN command: `cd frontend && CI=true corepack yarn test --watchAll=false --runInBand src/pages/graphExplorerModel.test.ts`; expected result is the model suite passing.
- [ ] Run the focused frontend suite: `cd frontend && CI=true corepack yarn test --watchAll=false --runInBand src/api/httpApi.test.ts src/api/mockApi.test.ts src/pages/graphExplorerModel.test.ts src/components/devboard/Badges.test.tsx`.
- [ ] Check the exact diff with `git diff --check -- frontend/src/pages/graphExplorerModel.ts frontend/src/pages/graphExplorerModel.test.ts frontend/src/components/devboard/GraphExplorer.tsx frontend/src/pages/GraphPage.tsx`.
- [ ] Prepare the task commit command, without executing it during planning: `git add frontend/src/pages/graphExplorerModel.ts frontend/src/pages/graphExplorerModel.test.ts frontend/src/components/devboard/GraphExplorer.tsx frontend/src/pages/GraphPage.tsx && git commit -m "feat(frontend): replace graph preview with bounded explorer"`.

## Task 6: Add browser-level explorer/page coverage and preserve existing source fallback behavior

**Files:**

- Create `frontend/src/components/devboard/GraphExplorer.test.tsx`.
- Create `frontend/src/pages/GraphPage.test.tsx`.
- Retain and, if needed, minimally update `frontend/src/components/devboard/Badges.test.tsx`.

**Consumed/produced interfaces:** Tests inject a typed `DevboardApi` mock with `queryProjectGraph` and existing `getGraph` methods. They assert visible behavior rather than private component state: scope selection, search result selection, parallel relationship requests, impact explanation, URL query preservation, empty/unavailable/error states, and no full graph rendering.

- [ ] Add a jsdom component test that renders two canonical scopes, selects the second scope, searches `InvoiceService`, and verifies the selected symbol, callers, dependencies, and source quality metadata are visible.
- [ ] Add a global `/graph` jsdom test with no project selected; assert a read-only project selector is visible, no `queryProjectGraph` POST occurs, and no fetch URL contains `/projects/undefined` before and after selector interaction.
- [ ] Run the RED command: `cd frontend && CI=true corepack yarn test --watchAll=false --runInBand src/components/devboard/GraphExplorer.test.tsx src/pages/GraphPage.test.tsx`; expected failure is that the new tests cannot import the explorer/page behavior.
- [ ] Add assertions that the mock API receives `node_handle`, not `id`, sends two neighborhood requests with `direction:"in"`/`"out"` and explicit `families:["call","dependency"]`, sends impact `max_depth: 2`, and never receives an internal or plugin credential field.
- [ ] Add assertions for `has_more`, `next_cursor`, `truncated`, and `projection.status`, plus visible `No matching symbols`, `Graph projection unavailable`, and `Unable to load graph details` messages with retry buttons.
- [ ] Keep the existing `Badges.test.tsx` tests green, including `canonical_graph` and unknown future source rendering; no source fallback may regress while graph types are extended.
- [ ] Run the GREEN command: `cd frontend && CI=true corepack yarn test --watchAll=false --runInBand src/components/devboard/GraphExplorer.test.tsx src/pages/GraphPage.test.tsx src/components/devboard/Badges.test.tsx`; expected result is all component/page/source tests passing.
- [ ] Check the exact diff with `git diff --check -- frontend/src/components/devboard/GraphExplorer.test.tsx frontend/src/pages/GraphPage.test.tsx frontend/src/components/devboard/Badges.test.tsx`.
- [ ] Prepare the task commit command, without executing it during planning: `git add frontend/src/components/devboard/GraphExplorer.test.tsx frontend/src/pages/GraphPage.test.tsx frontend/src/components/devboard/Badges.test.tsx && git commit -m "test(frontend): cover graph explorer states"`.

## Task 7: Run full verification, deploy the passing frontend/backend changes, and verify the public deep link

**Files/configuration inspected only:** `frontend/package.json`, `frontend/yarn.lock`, `backend/routes/api.php`, `backend/routes/web.php`, `docker-compose.devboard.yaml`, `docker-compose.devboard.traefik.yaml`, `docs/runbooks/traefik-integration.md`, and server `.env` `COMPOSE_FILE` selection.

- [ ] Run the complete backend suite: `cd backend && php artisan test`; expected result is zero failures and no migration command.
- [ ] Run the complete frontend suite: `cd frontend && CI=true corepack yarn test --watchAll=false --runInBand`; expected result is zero failures, including `Badges.test.tsx`.
- [ ] Build only after both suites pass: `cd frontend && corepack yarn install --frozen-lockfile && CI=true corepack yarn build`; expected result is a successful CRA production build.
- [ ] Check the final implementation diff before any deployment with `git diff --check` and `git status --short`; the implementation worker must confirm only intended graph/boundary files are modified and no migration, generated database data, or Traefik service definition changed.
- [ ] Deploy from `/home/ubuntu/dev-sandbox` with the server-selected Compose files using the canonical command `docker compose up -d --build --wait`; do not add, recreate, or remove a `traefik` service and do not run a database reset.
- [ ] Reload/restart the backend application only if the changed PHP image is not picked up by the Compose health check; do not restart worker/scheduler/database/Neo4j solely for a frontend change.
- [ ] Read the deployed frontend entry HTML and record the referenced JavaScript bundle hash, then verify the graph deep link loads the same shell without a 404: `curl -fsS "$DEVBOARD_PUBLIC_BASE_URL/projects/<project-id>/graph" | rg -o 'static/js/[^" ]+\\.js'` and `curl -fsS -o /dev/null -w '%{http_code}\\n' "$DEVBOARD_PUBLIC_BASE_URL/api/dashboard/projects/<project-id>/graph"` using the public base URL from the server `.env`.
- [ ] Verify an authenticated browser session can select a scope and a preview node, while an unauthenticated request to `/api/dashboard/projects/<project-id>/graph/query` returns the existing dashboard auth response and no response contains `/api/plugin/v1`, `external_id`, or a plugin token.
- [ ] Prepare the final task commit command, without executing it during planning: `git add frontend backend && git commit -m "feat: ship resilient dashboard graph explorer"`.
