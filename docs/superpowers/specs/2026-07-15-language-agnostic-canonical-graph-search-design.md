# Language-Agnostic Canonical Graph Search Design

## Purpose

Repair the Hades code-graph pipeline so routes, test symbols, inherited framework entrypoints, and ordinary code symbols are searchable through the backend Graph Explorer. The fix must work across supported languages and frameworks, preserve project/version isolation, repair compatible stored artifacts, and expose honest coverage when bounded extraction omits data.

This specification covers the graph subsystem only. Wiki verification and human-oriented wiki generation are separate follow-up specifications that depend on the corrected graph contract.

## Confirmed Failure

The Carnovali artifact contains complete route inventory records in top-level `routes[]`, including URI, method, route name, handler, source path, and line. Canonicalization currently treats route endpoints mentioned by relationships as missing symbols and synthesizes placeholder route nodes such as `route:contact_flock_roles_worker_home`. Those placeholders do not retain the route inventory properties, so the backend projection has route counts but no searchable URI.

The search index then compounds the problem:

- it indexes route placeholders without URI/path;
- it searches escaped literal values rather than normalized aliases;
- route names generally match only when the caller includes the internal `route:` prefix;
- URI segments separated by `/` or `-` are not reliable search terms.

Test files have a separate failure. The PHP symbol pass intentionally skips test paths, while the test map records only bounded file metadata and relationships. As a result, a real class such as `AdminControllerBulkDeleteBehaviorTest` is not a first-class searchable node. The default 1,000-file AST budget can also starve test directories in larger repositories.

Symfony inherited routes expose a third gap. The extractor combines class-level and method-level annotations only when both occur on the same concrete class. It does not materialize an effective route formed from a concrete controller prefix and an annotated action inherited from an ancestor.

## Selected Approach

Implement a two-sided contract repair:

1. The local Hades producer promotes route and test inventories into first-class canonical declarations before generic canonicalization.
2. Language adapters resolve framework-specific semantics before promotion.
3. The backend defensively hydrates route inventories from old artifacts before normalization.
4. A versioned search index stores safe, normalized aliases and supports segment/prefix discovery.
5. Coverage and truncation remain visible instead of being represented as an empty search result.

A backend-only patch was rejected because it would preserve a malformed producer contract. A frontend-only fuzzy search was rejected because it cannot recover properties absent from Neo4j. An agent-only fix was rejected because it would not repair compatible stored artifacts or protect the backend from older producers.

## Architecture

```text
language/framework adapters
  PHP: Symfony + Laravel
  Python: FastAPI + Django
  TypeScript/JavaScript: Next.js + Express
  SQL and future adapters
            |
            v
uniform inventories: routes[], symbols[], tests, relationships[]
            |
            v
shared graph promotion and polyglot merge
            |
            v
hades.graph_artifact.v1 canonical nodes + relationships
            |
            v
backend compatibility hydration + canonical normalization
            |
            v
Neo4j projection + canonical_node_search_v2
            |
            v
bounded Graph Explorer search/details/traversal
```

The shared stages must not contain Symfony-specific parsing. Framework semantics remain inside their adapter; canonical promotion, identity, capacity selection, search aliases, and projection remain language-independent.

## Uniform Route Contract

Every detected route or public entrypoint must be representable as:

```json
{
  "framework": "symfony",
  "method": "GET",
  "uri": "/generale/soggetti-attivi/",
  "name": "contact_flock_roles_worker",
  "handler": "WorkerController@index",
  "controller": "App\\Controller\\ContactFlock\\Roles\\WorkerController",
  "path": "src/Controller/ContactFlock/Roles/WorkerController.php",
  "line": 206,
  "defined_handler": "AdminController@index",
  "inherited": true
}
```

Only fields supported by evidence are emitted. `method` may contain a stable joined set such as `GET|HEAD`; an unspecified method becomes `ANY`. `uri` is normalized to one leading slash, no duplicate separators, and no trailing slash except the root route or when the framework contract distinguishes it. The original source-relative path remains evidence, never an absolute local path.

The route's canonical node must contain:

- `kind: route`;
- stable route name when available, otherwise `METHOD URI`;
- `method`, `uri`, `handler`, `controller`, and `framework` properties;
- bounded source-relative evidence (`path`, `line`);
- `inherited` and `defined_handler` when an adapter resolved inheritance.

Route identity uses the stable route name when it is unambiguous within the artifact. Otherwise it uses framework, method set, normalized URI, and effective handler. Route records and already-declared route nodes are merged by identity; neither input order nor duplicated inventories may create duplicate nodes.

## Test Symbol Contract

Each indexed test file becomes a first-class node:

```json
{
  "kind": "test",
  "name": "AdminControllerBulkDeleteBehaviorTest",
  "framework": "phpunit",
  "path": "tests/Controller/AdminFlock/AdminControllerBulkDeleteBehaviorTest.php",
  "cases": ["testBulkDelete..."],
  "target_candidates": ["AdminControllerBulkDeleteBehavior"]
}
```

Adapters may emit test-case child nodes when a stable case name is available, but file/class-level test nodes are mandatory. Existing `TEST_COVERS_SYMBOL`, `TEST_COVERS_ROUTE`, and `TEST_IMPORTS` relationships target these explicit nodes rather than relying on synthetic endpoint placeholders.

Test-node promotion is shared across languages. It consumes the existing test-map schema produced for PHPUnit, pytest, Vitest/Jest, and future frameworks. Language-specific parsing may enrich the name and cases but must not change the canonical shape.

## Framework Resolution

### Symfony

Build a bounded class metadata index containing fully qualified class name, parent class, class route annotations/attributes, and annotated public methods. For every concrete controller with a class route:

1. Traverse its ancestor chain with cycle detection and a fixed maximum depth.
2. Collect annotated actions from the concrete class and ancestors.
3. Combine the concrete class route with each action route using the existing URL/name normalizers.
4. Record the concrete controller as the effective owner and the ancestor method as `defined_handler`.
5. Emit a route-handler relationship to the defining method symbol and preserve effective-controller metadata on the relationship.
6. Deduplicate by normalized route identity.

This materializes `/generale/soggetti-attivi/` from `WorkerController` plus the inherited `AdminController@index` action without inventing an unsupported concrete method declaration.

### Laravel

Continue extracting route files, resource routes, controller handlers, middleware, and bindings. Feed the same uniform route records into shared promotion. Existing named routes must be searchable with or without the internal `route:` prefix and by URI segments.

### Python

FastAPI and Django route records already use top-level `routes[]`; promote them through the same shared path. Test nodes come from the shared test map. Python-specific decorators and URL inclusion semantics remain adapter responsibilities.

### TypeScript and JavaScript

Next.js and Express route records already use top-level `routes[]`; promote them through the same shared path. Route filenames, handler symbols, and HTTP method exports remain adapter responsibilities. Vitest/Jest test files become shared test nodes.

### SQL and Future Languages

Languages with no HTTP routes emit an empty route inventory. They still use shared symbol/test promotion and canonical search. A future adapter gains route search by producing the uniform route record; no backend or frontend special case is allowed.

## Polyglot Workspaces

Replace the current exclusive `if PHP / elif TypeScript / elif SQL / else Python` dispatch with bounded multi-adapter execution. Every detected supported language adapter runs on its own candidate subset. A shared merger combines:

- routes;
- symbols/nodes;
- edges/relationships;
- database metadata;
- test maps;
- log/runtime signals;
- per-language coverage and omissions.

The merger must be permutation-invariant and deduplicate only by canonical semantic identity. It must not merge same-named symbols from different namespaces or languages. Artifact `language` becomes `polyglot` when multiple adapters contribute, while `graph_contract.coverage.languages` lists each contributing language.

## Bounded Extraction

Raise the default AST file ceiling from 1,000 to 10,000, matching the existing project-tree ceiling, but add an explicit total-byte budget so the larger file count cannot produce unbounded reads. All ceilings remain payload-configurable and are reported in coverage.

Capacity selection must reserve first-class declarations in this order:

1. route and public-entrypoint nodes;
2. test nodes with coverage/import relationships;
3. application declarations referenced by relationships;
4. other application declarations;
5. synthesized external placeholders.

This priority guarantees that route/test discovery does not lose to placeholders. It does not claim complete application coverage when the global node budget is exhausted. Coverage reports must distinguish:

- files discovered;
- files analyzed;
- files omitted by file or byte budget;
- declared nodes omitted by node budget;
- synthesized placeholders omitted;
- routes detected/promoted/omitted;
- tests detected/promoted/omitted.

The backend and UI must call this `partial coverage`, not `files failed`, when omission is caused by a configured budget.

## Backend Compatibility Hydration

Before `CanonicalGraphNormalizer` processes a Hades artifact, the backend must merge top-level `routes[]` into whichever declaration collection the artifact uses (`nodes` or `symbols`). This applies whether or not `graph_contract` is present.

The compatibility hydrator:

- validates route records with the same safe URI rules already used by projection;
- maps them to first-class route declarations;
- merges properties into matching placeholder/declared route nodes;
- records trusted route provenance outside producer-controlled public properties;
- never treats a filesystem path as a public route;
- leaves original artifact JSON immutable for audit;
- supports rebuilding stored pre-fix artifacts.

New producer artifacts will already contain route nodes, so hydration becomes an idempotent validation/merge step rather than a second source of truth.

Stored artifacts can recover route URIs and names through a canonical rebuild. Framework semantics absent from the stored artifact, such as an unmaterialized inherited Symfony route or excluded test file, require a fresh local import.

## Search Index Version 2

Create a new full-text index name, `canonical_node_search_v2`, rather than relying on `CREATE ... IF NOT EXISTS` to mutate the old index definition.

Each projected node receives only safe, derived public search fields:

- `public_search_name`;
- `public_search_label`;
- `public_search_path` for trusted routes only;
- `public_search_terms`, a bounded alias string.

`public_search_terms` includes applicable aliases:

- original public name and label;
- route name with and without `route:`;
- normalized route URI;
- individual URI segments;
- identifiers split on `_`, `-`, `.`, `:`, `/`, `@`, and namespace separators;
- class and method portions of handlers;
- test class/file stem and test-case names.

The query normalizer applies the same separator and case folding. A query such as `soggetti-attivi` becomes the required token set `soggetti AND attivi`; `/generale/soggetti-attivi/` additionally receives an exact normalized-URI boost. Prefix matching is allowed only on escaped alphanumeric tokens and remains scoped by project, source scope, and active graph version.

Ranking order is:

1. exact normalized name or route URI;
2. exact alias;
3. all-token prefix match;
4. lower-scoring label/handler matches.

Search remains bounded to 100 public results per page and retains opaque handles. Raw external IDs, local absolute paths, and untrusted producer strings must never enter public search fields.

## Graph Explorer Behavior

The explorer remains search-first; it does not render all nodes on page load. After a result is selected it loads bounded details, adjacency, impact, or paths.

The overview must expose:

- active graph version and scope;
- node/edge/route/test counts;
- extractor languages/frameworks;
- coverage quality;
- concrete truncation/omission counts.

An empty result under partial coverage must say that the symbol was not found in the indexed subset. It must not imply that the symbol does not exist in the repository.

## Rebuild and Rollout

1. Deploy backend compatibility hydration and search-v2 support with tests.
2. Deploy the local agent's shared promotion, polyglot aggregation, test nodes, and Symfony inheritance resolution.
3. Rebuild the current Carnovali canonical graph from the stored artifact to verify route-property recovery.
4. Run a fresh Carnovali Hades import to add inherited routes and previously excluded test nodes.
5. Project the fresh candidate, verify counts and search acceptance, then atomically mark it current.
6. Retain the previous current projection until the new projection passes node, relationship, adjacency, and search verification.
7. Drop the obsolete v1 full-text index only after all reads use v2 and rollback is no longer required.

No database reset or data deletion is required.

## Error Handling

- A failed language adapter produces a degraded per-language coverage record; successful adapters remain usable.
- An invalid route record is omitted with a bounded reason and cannot authorize route publication.
- An inheritance cycle or depth overflow marks framework enrichment partial and preserves directly declared routes.
- A search-v2 index unavailable during rollout returns an explicit projection/index-rebuild reason; it must not silently return zero hits.
- A failed candidate projection never replaces the current version.
- A fresh import that is less complete than the current artifact requires explicit operator review before publication.

## Security and Isolation

- Every query remains constrained by project ID, source-scope type, source-scope ID, and active graph version.
- Opaque public handles remain keyed and versioned.
- Route paths enter public search only after trusted-provenance validation.
- Local absolute paths are reduced to safe source-relative evidence and never indexed publicly.
- Search token construction escapes Lucene operators; users cannot inject arbitrary Lucene clauses.
- Compatibility hydration does not mutate or reinterpret unrelated project artifacts.

## Test Strategy

Follow red-green TDD for every behavior.

### Local agent tests

- Generic promotion converts route inventories from PHP, Python, and TypeScript fixtures into equivalent first-class route nodes.
- Generic promotion converts PHPUnit, pytest, and Vitest/Jest fixtures into searchable test nodes.
- Promotion is idempotent when an adapter already emitted a route node.
- Polyglot aggregation runs all detected adapters and preserves same-named symbols from different languages/namespaces.
- File and byte budgets report bounded omissions accurately.
- Capacity ranking retains route/test declarations before synthesized placeholders.
- Symfony inheritance produces the concrete `/generale/soggetti-attivi/` route linked to its defining ancestor method.
- Symfony cycle/depth protection degrades safely.
- Laravel, FastAPI/Django, Next.js/Express, and route-less SQL fixtures keep their expected behavior.

### Backend tests

- A contracted old artifact containing only top-level route records hydrates complete route nodes.
- Hydration is idempotent for new artifacts that already contain route nodes.
- Route provenance and filesystem-path rejection remain enforced.
- Search-v2 finds a route by exact URI, URI segment, normalized name, and name without `route:`.
- Search-v2 finds a test class by full name and meaningful tokens.
- Queries cannot cross project, scope, or graph-version boundaries.
- Partial coverage is returned to the dashboard.
- Existing class/method search, details, traversal, impact, and path tests remain green.

### Live acceptance on Carnovali

- `soggetti-attivi` returns the WorkerController route family.
- `/generale/soggetti-attivi/` returns the inherited index route.
- `contact_flock_roles_worker_home` and `route:contact_flock_roles_worker_home` resolve to the same route node.
- `AdminControllerBulkDeleteBehaviorTest` returns a test node.
- `WorkerController` continues returning its class and methods.
- Selecting a route shows its URI, effective handler, defining handler when inherited, and bounded neighborhood.
- The overview reports actual coverage and no longer presents budget omissions as unexplained absence.

## Non-Goals

- Rendering the entire graph at once.
- Executing application routes to discover them dynamically.
- Treating the graph as remote Git truth.
- Indexing raw source code or local absolute paths in Neo4j public search fields.
- Replacing framework adapters with one universal parser.
- Implementing wiki verification or wiki generation in this graph change.
