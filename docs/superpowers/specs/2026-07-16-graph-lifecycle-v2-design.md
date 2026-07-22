# Hades Code Graph and Lifecycle Contract v2

**Status:** draft for user review; implementation not started

**Audience:** Hades Agent, Hades Laravel backend, React frontend implementers

**Normative language:** MUST, MUST NOT, SHOULD, and MAY are requirements in the RFC 2119 sense.

## 1. Purpose

Replace the current canonical graph v1 with a clean v2 contract that can answer two different user questions honestly:

1. **What is in this codebase and how is it connected?** — the canonical code graph, the structural map.
2. **What can happen from this entrypoint until termination?** — an entrypoint lifecycle projection, the traversal map over the canonical graph.

The first default frontend experience is **Request lifecycle** for HTTP applications and **Execution flow** for applications with non-HTTP entrypoints. A secondary **Analyze an element** mode remains available for symbol-, route-, file-, and natural-language-led exploration.

This release also adds a project-scoped verification queue. Unresolved graph assertions and Wiki pages marked `needs_verification` become explicit work for a local Hades Agent. Normal backend sync reports bounded queue counts but never claims or executes work.

The implementation is a clean cut:

- v2 artifacts use a new top-level schema and new IDs;
- the v2 backend never adapts, merges, or falls back to v1 graph data;
- a pinned Symfony Demo is imported in an isolated acceptance environment before production promotion; Carnovali is an optional later monitored scale fixture;
- v1 graph data is removed only after backup and successful v2 acceptance;
- unrelated project data, users, memory, Wiki pages, and Kanban data are never reset.

## 2. Current defects being removed

The current implementation cannot be incrementally reinterpreted as lifecycle v2 because these are structural defects:

- `hades.graph_artifact.v1` contains flat symbols and relationships, not an intra- and inter-procedural control-flow graph;
- `resolve_call_graph()` is a bounded breadth-first search with a default depth of eight, not a request lifecycle model;
- limits of 5,000 nodes, 10,000 edges, 500 routes/tests, and other bounded collections can silently turn missing data into apparent absence;
- `ready`, `verified`, and `complete` are not consistently orthogonal;
- the browser can show `0` for callers, dependencies, or impact when the corresponding capability is incomplete;
- the current `Find path` control exposes an internal graph operation without a clear user goal;
- the generic plugin worker treats any non-empty prose response as successful work;
- plugin work items and status reports assume all work is Kanban work;
- Wiki verification is rigorous, but it is not fed by a unified verification queue;
- the one-request artifact upload cannot safely remove existing size caps;
- Graphify output can be mistaken for verified canonical structure unless provenance is enforced.

## 3. Frozen product decisions

These decisions are not delegated to the implementing model.

### 3.1 Default user experience

- If the selected scope has at least one `http_route` entrypoint, `/graph` opens in **Request lifecycle** mode.
- If it has executable entrypoints but no HTTP route, it opens in **Execution flow** mode.
- If it has no executable entrypoint but has public/exported APIs, those APIs are the entrypoints and the mode is **Execution flow**.
- If entrypoint discovery is complete and the scope truly has no entrypoints, it opens in **Analyze an element** mode and explains that the codebase is a library or data-only scope.
- If entrypoint discovery is partial, the UI says entrypoints are incomplete; it MUST NOT claim that none exist.

### 3.2 Graph versus lifecycle

The canonical graph is a directed multigraph. It contains code declarations, executable units, effects, boundaries, and typed edges. It is not a tree.

An entrypoint lifecycle is a version-bound traversal projection over that graph. It includes every structurally reachable branch once, including alternative, exception, loop, and linked asynchronous flows. It does not enumerate the Cartesian set of complete runtime paths.

For example, both branches below are confirmed structural facts when parsed successfully:

```php
if ($user->isAdmin()) {
    $adminService->load();
} else {
    $customerService->load();
}
```

The runtime choice is unknown until execution, but the existence of both alternatives is not uncertain. An uncertainty is created only when the code target or behavior cannot be resolved, for example reflection, a dynamic service locator, generated code, or an unavailable parser.

### 3.3 Source of path truth

- Neo4j and the canonical graph contract own topology and lifecycle traversal.
- PostgreSQL owns artifact metadata, publication state, verification work, and the active projection pointer.
- The vector database MAY locate and rank entrypoints or symbols from natural language. It MUST NOT invent or define a path.
- Runtime traces are reserved as a future `observed_runtime` evidence overlay. Trace collection and ingestion are not part of this release.

### 3.4 Publication policy

Publication uses immutable, atomic **partial publication**:

- `ready + full` and `ready + partial` are both valid;
- a partial artifact is usable and visibly partial;
- a failed candidate never replaces the active projection;
- no reader mixes two artifact or verification versions.

## 4. Non-goals

This implementation does not include:

- symbolic execution, SMT solving, model checking, or proof that all runtime states terminate;
- execution of user applications to discover routes or behavior;
- enumeration of every possible complete path;
- runtime tracing or production telemetry;
- parsing vendor, generated, build, or dependency directories configured as out of scope;
- remote code changes by another Hades Agent;
- a new core model tool;
- a separate transport queue for verification;
- migration of v1 graph records into v2;
- a general-purpose UI for viewing every graph node at once.

## 5. Domain vocabulary

| Term | Exact meaning |
|---|---|
| Artifact graph version | SHA-256 digest of the immutable logical v2 graph artifact. |
| Verification set hash | SHA-256 digest of the ordered, active graph-verification overlay records. |
| Projection version | SHA-256 of `artifact_graph_version + ":" + verification_set_hash`. |
| Projection state | Operational state: `queued`, `projecting`, `ready`, `failed`, or `stale`. |
| Completeness | Epistemic coverage: `full` or `partial`, globally and by capability. |
| Evidence origin | Why a fact is believed: code, agent verification, runtime observation, inference, or unresolved. |
| Flow semantics | How control may move: always, conditional, alternative, exception, async, or loop; uncertainty is separate evidence. |
| Entrypoint | A root an external actor, runtime, scheduler, queue, event system, or library consumer can invoke. |
| Lifecycle flow | The complete bounded structural traversal from one entrypoint, without full-path enumeration. |
| Backbone | The compact initial lifecycle representation: ordered stages plus mandatory or collapsed structural segments. It is a UI projection, not a claim that only one path exists. |
| Verification work item | A leased operational queue item whose structured resolution is `verified`, `contradicted`, or `deferred`. |

## 6. Canonical artifact contract

### 6.1 Version identifiers

The only accepted v2 identifiers are:

```text
top-level schema:       hades.code_graph.v2
graph contract version: hades.graph_artifact.v2
bundle schema:          hades.graph_bundle.v2
chunk schema:           hades.graph_chunk.v2
node ID prefix:         hades:node:v2:
edge ID prefix:         hades:edge:v2:
flow ID prefix:         hades:flow:v2:
flow-step ID prefix:    hades:flow-step:v2:
branch ID prefix:       hades:branch:v2:
call-site ID prefix:    hades:call-site:v2:
exception-scope prefix: hades:exception-scope:v2:
uncertainty ID prefix:  hades:uncertainty:v2:
```

`hades.php_graph.v2` MUST NOT be introduced. Language and framework are data dimensions inside one language-neutral top-level schema.

#### 6.1.1 Normative machine-readable contracts

The prose in this document explains intent. The following files are the executable source of truth and MUST be created before producer, backend, or frontend implementation begins:

```text
contracts/hades/graph-v2/artifact.schema.json
contracts/hades/graph-v2/bundle.schema.json
contracts/hades/graph-v2/chunk.schema.json
contracts/hades/graph-v2/dashboard-query.schema.json
contracts/hades/graph-v2/dashboard-response.schema.json
contracts/hades/graph-v2/verification-work.schema.json
contracts/hades/graph-v2/verification-result.schema.json
contracts/hades/graph-v2/graph-overlay.schema.json
contracts/hades/graph-v2/golden/canonicalization.json
contracts/hades/graph-v2/golden/dashboard-protocol.json
contracts/hades/graph-v2/golden/verification-results.json
```

All object schemas recursively use `additionalProperties: false`, explicitly list required and nullable fields, define string/array/object size limits, and use discriminator-based `oneOf` branches where this specification defines a union. The schemas reject floats, unknown enum members, unsafe integers, empty IDs, absolute paths, path traversal, and raw source fields. `canonicalization.json` contains at least one Unicode-NFC, path-normalization, safe-integer, ID, artifact-digest, verification-dedupe, result-digest, and projection-version vector with the exact canonical UTF-8 bytes and expected lower-case SHA-256.

The backend vendors an exact copy under `backend/resources/contracts/hades/graph-v2/` and records every schema digest in `backend/resources/contracts/hades/graph-v2/manifest.json`. The agent package owns the root copy. CI fails if the backend copy, OpenAPI component schema, Python validator, PHP validator, TypeScript types, or golden outputs differ. Implementers MUST generate or manually define types from these contracts; they MUST NOT maintain a looser private DTO. A prose example never overrides a schema constraint.

#### 6.1.2 Common scalar and nullability rules

These rules apply recursively unless a narrower section overrides them:

| Scalar | Exact rule |
|---|---|
| digest | lower-case `^[0-9a-f]{64}$` |
| prefixed public ID | declared ASCII prefix plus exactly 64 lower-case hex characters |
| ULID | upper-case canonical 26-character ULID |
| safe source path | NFC POSIX relative path, 1–4,096 UTF-8 bytes, no empty/`.`/`..` segment, NUL, control character, backslash, drive prefix, or leading slash |
| AST/config structural path | NFC printable string, 1–1,024 bytes, slash-delimited normalized field/ordinal tokens, no source line/byte offset |
| display label/name | NFC, collapsed whitespace, no control character/HTML, 1–1,024 bytes unless narrower |
| enum/extractor/rule ID | lower ASCII identifier matching its schema pattern, at most 128 bytes |
| line | safe integer >=1; end >= start |
| ordinal/count/byte size | safe integer >=0 |
| timestamp | RFC 3339 UTC whole seconds with `Z` |

All record objects include every schema property; nullable values are explicit `null`, while optional request filters may be omitted only where the query schema says optional. Empty string never means unknown. Arrays are dense, contain no null unless explicitly declared, and use the ordering stated in this document. Contract strings reject isolated surrogates and normalize to NFC before validation/hashing. Producer edge/registration occurrence unions may use only `ast` or `config`; server-generated effective edge occurrences may use `derived` as defined in section 6.7. Evidence locators separately follow the file/AST/config/derived context rules in section 6.7.

### 6.2 Logical envelope

The unchunked logical artifact has exactly these required top-level fields:

```json
{
  "schema": "hades.code_graph.v2",
  "generated_at": "2026-07-16T12:00:00Z",
  "project": {
    "project_id": "01KXJD0SV73EBGWKNE2EK3M4KD",
    "workspace_binding_id": "01KXJD1BDMQ2TFABMVJV6EFE8Q"
  },
  "source": {
    "head_commit": null,
    "tree_sha256": "64-hex",
    "dirty": false,
    "branch": null
  },
  "graph_contract": {
    "version": "hades.graph_artifact.v2",
    "artifact_graph_version": "64-hex",
    "projection_state": "queued",
    "completeness": {},
    "coverage": {}
  },
  "frameworks": [],
  "languages": [],
  "entrypoints": [],
  "nodes": [],
  "structures": [],
  "edges": [],
  "flows": [],
  "flow_steps": [],
  "uncertainties": []
}
```

`projection_state` in a producer artifact is always `queued`; the backend owns later transitions. Arrays are logically complete even when physically split into bundle chunks.

Graph v2 source scope is exactly the declared `workspace_binding_id`. The API retains the `{type,id}` shape, but `type` MUST equal `workspace_binding` in this release. Repository metadata may be linked to the binding for display; it is never used as a fallback graph scope.

`languages` is a key-sorted array of records with exactly `name`, `extractor`, `extractor_version`, `detected_file_count`, and `analyzed_file_count`. Names for this release are `php`, `python`, `javascript`, `typescript`, and `sql`; future names match `^[a-z][a-z0-9_]{0,31}$`. `frameworks` is sorted by language/name and each record has exactly `language`, `name`, nullable `version`, `detector`, `configuration_paths` (maximum 20 safe source-relative paths), and `knowledge=verified|unresolved`. Strings are NFC and at most 128 bytes except paths. Duplicate language or language/framework pairs are rejected.

### 6.3 Source identity

`generated_at` is RFC 3339 UTC with whole-second precision and a `Z` suffix. `head_commit` is null or 40 lowercase hexadecimal characters; `branch` is null or a Git ref display name of at most 255 UTF-8 bytes.

`source.tree_sha256` is computed from the deterministic, lexicographically sorted inventory of all in-scope files. Normalize each source-relative POSIX path to Unicode NFC without case folding. For each regular file, create bytes `path_utf8 || 0x00 || lowercase_hex_sha256(file_exact_bytes) || 0x0A`; SHA-256 the concatenation of all record bytes.

Rules:

- absolute paths MUST NOT enter the artifact;
- if two distinct raw filesystem entries normalize to the same NFC source-relative POSIX path, fail before extraction with `source_path_normalization_collision`; log/report only the safe normalized path, never silently deduplicate or expose raw secret-like names. Golden inventory fixtures include an NFC/NFD collision;
- hashing is a streaming precondition outside parse/file-size/time budgets; even oversized or binary in-scope files are hashed;
- an unreadable file during identity hashing fails the import with `source_snapshot_unreadable`; it cannot produce a partial artifact with a false identity;
- recompute the inventory digest after extraction; a mismatch fails with `source_changed_during_index`;
- follow a symbolic link only when its fully resolved target is a regular file inside the workspace root; hash target bytes under the symlink's source-relative path. For an escaping/broken/cyclic source symlink, hash `SYMLINK_INVALID || 0x00 || exact_link_target_bytes` as that path's file digest without exposing the target text, count the applicable inventory capability partial, and do not follow it;
- a checked-out Git submodule is inventoried recursively; an unavailable in-scope submodule hashes its gitlink commit but makes applicable extraction capabilities partial with `submodule_unavailable`;
- Git HEAD alone is never sufficient because the worktree may be dirty;
- `dirty` is true when tracked changes, staged changes, or in-scope untracked files alter the inventory relative to HEAD;
- `branch` and `head_commit` are nullable for non-Git workspaces;
- identical in-scope content produces identical `tree_sha256` regardless of absolute checkout path.

Default excluded directory names are `.git`, `.hg`, `.svn`, `vendor`, `node_modules`, `.venv`, `venv`, `dist`, `build`, `out`, `target`, `coverage`, `.next`, `.nuxt`, `var/cache`, `storage/framework/cache`, and generated dependency caches. Default excluded secret files are `.env`, `.env.*` except `.env.example`, `*.pem`, `*.key`, `*.p12`, `*.pfx`, and files matched by the existing Hades secret policy. These compiled baselines live in `hades_graph_config.py`; `hades.graph_index.excluded_paths` contains only user additions. Repository rules may add exclusions but cannot remove secret exclusions. Excluded paths appear only as aggregate counts, never public names. A configured exclusion is outside scope and does not reduce completeness.

### 6.4 Deterministic identity

Every ID is the prefix plus lower-case SHA-256 of an RFC 8785 JSON Canonicalization Scheme identity object. Before JCS, normalize every string to Unicode NFC and every path to source-relative POSIX form. Contract JSON permits only strings, booleans, null, integers in the interoperable IEEE-754 safe range, arrays, and string-keyed objects; floats and non-finite numbers are rejected. Python and PHP share one golden byte fixture for every digest/ID type.

`NodeIdentity` is a discriminator-based closed union. The node ID is always `hades:node:v2:` plus SHA-256 of JCS of the complete selected identity object; display fields outside `identity` never change the ID.

Named source declarations use:

```json
{
  "variant": "source_declaration",
  "workspace_binding_id": "01KXJD1BDMQ2TFABMVJV6EFE8Q",
  "language": "php",
  "kind": "method",
  "namespace": "App\\Controller",
  "qualified_name": "App\\Controller\\WorkerController::index",
  "path": "src/Controller/WorkerController.php"
}
```

The other variants are exact:

```text
file:
  {variant:"file", workspace_binding_id, language, kind:"file", path}

source_occurrence:
  {variant:"source_occurrence", workspace_binding_id, language, kind,
   owner_node_id, structural_path, ordinal, semantic_role}

anonymous_callable:
  {variant:"anonymous_callable", workspace_binding_id, language,
   kind:"function", owner_node_id, structural_path, ordinal}

entrypoint:
  {variant:"entrypoint", workspace_binding_id, language, kind:"entrypoint",
   path, entrypoint_identity}

semantic_resource:
  {variant:"semantic_resource", workspace_binding_id, language, kind,
   framework, namespace, qualified_name, public_resource_name, protocol,
   operation}
```

`source_declaration` is required for named `module|namespace|class|interface|trait|enum|function|method|middleware|guard|authorization|validator|binding|controller|service|domain|model|repository|event|listener|job|queue|test` entities. `anonymous_callable` is required for closures/lambdas/anonymous callbacks; its owner is an existing declaration/entrypoint/anonymous-callable node, structural path is line-independent, and ordinal distinguishes same-parent anonymous siblings. Its display `name` is `<anonymous>` and `qualified_name` is `<owner qualified name>::<anonymous:` plus the first 12 hex characters of its computed node ID plus `>`; namespace is copied from the owner. `file` is required for inventory file nodes. `source_occurrence` is required for `basic_block|branch|merge|loop|response|redirect|abort|exception|exit|async_boundary|unknown_boundary` and for any occurrence-bound framework/external boundary; its owner is an existing declaration, entrypoint, or anonymous-callable node, structural path is line-independent, ordinal distinguishes equal-role siblings, and `semantic_role` is a normalized rule ID matching `^[a-z][a-z0-9_]{0,63}$` (it is identity data, not a closed product enum). `semantic_resource` is required for intentionally shared `table|query|cache|storage|integration|external_boundary|framework_boundary` resources that are not occurrence-bound; nullable fields are explicit and at least one of `qualified_name` or `public_resource_name` is non-null. A named source entity cannot be emitted as a semantic resource merely to merge distinct declarations. Same-named declarations in different languages, namespaces, paths, or scopes remain distinct; two blocks or terminals in one callable remain distinct. Golden fixtures include a closure with nested blocks/terminal references to its anonymous owner.

`entrypoint_identity` is exactly:

```text
{entrypoint_kind, framework, method_semantics, methods, public_path,
 public_name, trigger, match_constraints, registration_occurrence}
```

Its closed children are defined in section 6.6. `golden/canonicalization.json` contains two same-kind control occurrences in one callable, two identical same-file route registrations, and a line-insertion mutation proving that unrelated line changes preserve IDs.

Edge identity fields are:

```json
{
  "source_id": "hades:node:v2:...",
  "target_id": "hades:node:v2:...",
  "relation": "invokes",
  "flow": "conditional",
  "condition_hash": "64-hex-or-null",
  "branch_group_id": "stable-id-or-null",
  "call_site_id": "stable-id-or-null",
  "exception_scope_id": "stable-id-or-null",
  "occurrence": {
    "kind": "ast",
    "owner_node_id": "hades:node:v2:...",
    "ast_path": "body/3/consequence/1/call/0",
    "ordinal": 1
  }
}
```

Input permutation MUST NOT alter IDs. Identical duplicate records are deduplicated. Distinct source occurrences, conditions, and branches remain distinct edges. Multiple extractors supporting the same semantic occurrence merge into the evidence envelope defined below and do not duplicate topology.

`artifact_graph_version` is SHA-256 over the canonical semantic payload consisting of project/binding, source identity, frameworks/languages, entrypoints, nodes, structural identity records, edges, flows, flow steps, uncertainties, completeness, and coverage. The digest excludes `generated_at`, producer `projection_state`, the digest field itself, bundle/chunk boundaries, and compression metadata. The same semantic artifact therefore has the same version even when regenerated or chunked differently.

The following hash preimages are normative. In every formula, `JCS(x)` means the exact RFC 8785 UTF-8 bytes after the normalization rules above, and `SHA256` returns lower-case hexadecimal:

```text
condition.hash = SHA256(JCS({
  "normalized_full": <complete secret-redacted predicate before the 256-byte display truncation>
}))

AST source_fingerprint = SHA256(JCS({
  "file_sha256": <lower-case file digest>,
  "occurrence_kind": "ast",
  "path": <safe relative source path>,
  "structural_path": <normalized AST path>
}))

config source_fingerprint = SHA256(JCS({
  "file_sha256": <lower-case file digest>,
  "occurrence_kind": "config",
  "path": <safe relative source path>,
  "structural_pointer": <normalized configuration pointer>
}))

file source_fingerprint = SHA256(JCS({
  "file_sha256": <lower-case file digest>,
  "occurrence_kind": "file",
  "path": <safe relative source path>
}))

flow ID digest = SHA256(JCS({
  "entrypoint_id": <external entrypoint ID>,
  "root_node_id": <root node ID>,
  "kind": <request_lifecycle|execution_flow|async_flow>
}))

flow-step ID digest = SHA256(JCS({
  "flow_id": <flow ID>,
  "edge_id": <edge ID>,
  "stage_from": <stage>,
  "stage_to": <stage>,
  "async_context": <synchronous|linked_async>
}))

uncertainty fingerprint = SHA256(JCS({
  "domain": "graph",
  "project_id": <project ULID>,
  "workspace_binding_id": <binding ULID>,
  "subject": <the complete closed subject object>,
  "resolution_kind": <resolution kind>,
  "reason_code": <reason code>,
  "question": <normalized question>
}))

evidence_digest = SHA256(JCS(<result.evidence after the exact server sort/deduplication rules in section 11.3>))
verification_set_hash = SHA256(JCS(<the canonical sorted active-overlay array from section 9.2>))
```

The artifact semantic preimage is exactly an object with these keys and no others: `schema`, `project`, `source`, `graph_contract_version`, `frameworks`, `languages`, `entrypoints`, `nodes`, `structures`, `edges`, `flows`, `flow_steps`, `uncertainties`, `completeness`, and `coverage`. `graph_contract_version` is the scalar `hades.graph_artifact.v2`; it is not the producer `graph_contract` object. Arrays are sorted as their record sections require. This explicit object excludes `generated_at`, `projection_state`, `artifact_graph_version`, manifest/chunk descriptors, compression fields, and backend state. `artifact_graph_version=SHA256(JCS(semantic_preimage))`.

`projection_version` does not use ambiguous string interpolation: it is SHA-256 of the ASCII bytes `lowercase_artifact_digest`, one literal colon byte `0x3a`, then `lowercase_verification_set_hash`. Every formula and its complete preimage appears in `golden/canonicalization.json`; implementations call shared helpers rather than rebuilding preimages at call sites.

### 6.5 Node contract

Each node contains:

```json
{
  "id": "hades:node:v2:...",
  "identity": {
    "variant": "source_declaration",
    "workspace_binding_id": "01KXJD1BDMQ2TFABMVJV6EFE8Q",
    "language": "php",
    "kind": "method",
    "namespace": "App\\Controller",
    "qualified_name": "App\\Controller\\WorkerController::index",
    "path": "src/Controller/WorkerController.php"
  },
  "kind": "method",
  "language": "php",
  "framework": "symfony",
  "name": "index",
  "qualified_name": "App\\Controller\\WorkerController::index",
  "namespace": "App\\Controller",
  "uncertainty_id": null,
  "location": {
    "path": "src/Controller/WorkerController.php",
    "start_line": 206,
    "end_line": 240
  },
  "properties": {},
  "evidence": {
    "primary": {
      "origin": "verified_from_code",
      "extractor": "php.symfony.v2",
      "source_locator": {
        "kind": "ast",
        "path": "src/Controller/WorkerController.php",
        "structural_path": "declaration/class/WorkerController/method/index"
      },
      "source_fingerprint": "64-hex",
      "inference_rule": null
    },
    "supporting": [],
    "supporting_omitted_count": 0
  }
}
```

Every node has exactly the fields shown. `identity` validates against the section 6.4 union, its variant is legal for the node kind, its duplicated language/kind/path/name dimensions equal the node display fields, and recomputing it must equal `id`. `language` and `framework` are null or normalized names. Language is non-null for source declarations/blocks; a file uses its single detected supported language or null when binary/unsupported/unclassified; a language-neutral semantic resource or terminal/framework boundary may also use null. `name` is always a safe 1–256 byte display label. `qualified_name` is present but nullable and, when non-null, is 1–1,024 bytes; `namespace` is null or at most 512 bytes. Display-field rules are exact: source declarations require their identity qualified name/namespace; anonymous callables use the derived qualified name in section 6.4; file nodes use safe path as qualified name and basename as name; an entrypoint uses `public_name` as qualified name when non-null and otherwise its public `trigger.value`, while name is its label; source occurrences use null qualified name and a semantic-role label; semantic resources copy nullable qualified name/namespace from identity and derive name from qualified-name tail or required `public_resource_name`.

Node-level uncertainty is deliberately narrow. A non-null `uncertainty_id` is legal only on a `kind=unknown_boundary` node whose primary evidence is `unresolved`; every other node kind has `uncertainty_id=null` and rejects unresolved primary evidence. Conversely, an unresolved `unknown_boundary` has exactly one non-null uncertainty ID. That boundary is assertion-exclusive: inside the immutable artifact its only incoming executable/topology edge is the exact semantic subject edge, or the unique unresolved invocation for a call-site subject, carrying that same uncertainty. Candidate/hint edges target compatible real nodes, never the boundary. The boundary is never an entrypoint or flow root, an entrypoint handler, a structure owner or continuation, the owner of another node identity, the source/target of any unrelated edge, or the unknown boundary for another uncertainty. Import validates this complete reference closure. These invariants make later suppression deterministic: a verified or contradicted overlay for the uncertainty always suppresses this boundary node, without an orphan/reference heuristic.

`location` is always null for `kind=file`. It is required for every `source_declaration`, `source_occurrence`, `anonymous_callable`, and `entrypoint` identity, with exactly safe path, positive `start_line`, and `end_line >= start_line`. A `semantic_resource` may have null location because it is intentionally shared rather than occurrence-bound; it still has at least one AST/config evidence locator pointing to the source/configuration occurrence that justified it. No other null-location producer node is legal. Empty/unknown values use null, never placeholder text such as `unknown`.

Allowed `kind` values are:

```text
entrypoint, file, module, namespace, class, interface, trait, enum,
function, method, basic_block, branch, merge, loop,
middleware, guard, authorization, validator, binding,
controller, service, domain, model, repository,
table, query, cache, storage, integration, external_boundary,
response, redirect, abort, exception, exit,
event, listener, job, queue, async_boundary, framework_boundary,
test, unknown_boundary
```

`properties` has at most 32 lower-snake-case keys, nesting depth three, total canonical size 8 KiB, strings at most 1 KiB, and arrays at most 64 scalar values. Additional properties outside this table are rejected:

| Node family | Allowed property keys |
|---|---|
| file | `file_sha256`, `byte_size`, `analysis_status`, `omission_reason`, `is_test`, `is_generated` |
| module/namespace | `package`, `module_system`, `is_test`, `is_generated` |
| class/interface/trait/enum | `visibility`, `abstract`, `final`, `modifiers` |
| function/method/controller/service/domain/repository | `visibility`, `static`, `async`, `parameter_count`, `return_type`, `modifiers` |
| basic_block/branch/merge/loop | `control_kind`, `ordinal` |
| middleware/guard/authorization/validator/binding/framework_boundary | `framework_role`, `pipeline_order`, `boundary_name` |
| model/table/query/cache/storage | `operation`, `public_resource_name`, `query_kind` |
| integration/external_boundary | `protocol`, `operation`, `destination_kind` |
| response/redirect/abort/exception/exit | `status_code`, `exception_type`, `terminal_kind` |
| event/listener/job/queue/async_boundary | `channel_kind`, `public_name`, `schedule` |
| test | `test_framework`, `case_count` |
| entrypoint/unknown_boundary | `reason_code` |

Unknown values are null or the key is omitted; empty strings are rejected. `properties` MUST NOT contain raw source, secrets, environment values, absolute paths, control characters, SQL bodies, URL credentials/query strings, or arbitrary framework payloads.

Every represented in-scope inventory file has exactly one `kind=file` node. Its `location` is null and its primary inventory evidence uses exactly `source_locator={kind:"file",path:<identity path>}` plus the file fingerprint preimage in section 6.4; this is valid even for an empty, binary, unsupported, failed, or oversized file. Its digest is the lower-case SHA-256 used by the source tree; byte size is a non-negative safe integer; `analysis_status` is `analyzed|unsupported|failed|too_large|budget_omitted`; `omission_reason` is null only for analyzed/unsupported and otherwise one completeness reason code. Bundle-budget selection follows the atomic-unit algorithm in section 8.2; it never relies on kind-prefix truncation. Excluded/secret paths have no file node and appear only in aggregate coverage. Golden fixtures cover empty, binary, and unsupported inventory files.

Every non-null source path in an included source-declaration identity/location, anonymous/source-occurrence location, entrypoint registration/location, edge location/config occurrence, uncertainty source ref, or evidence locator must resolve to exactly one included file node with the same path. The validator recomputes every AST/config evidence fingerprint from that file node digest plus its locator. A synthesized null-location record still has at least one path-bearing evidence locator. No included fact may reference an excluded, pruned, or unrepresented path; the budget unit closure includes the matching file node.

### 6.6 Entrypoint contract

Entrypoints are records that reference `kind=entrypoint` nodes and use these `entrypoint_kind` values:

```text
http_route, process_main, cli_command, scheduled_job,
queue_consumer, event_listener, rpc_method, public_api
```

An HTTP entrypoint record is:

```json
{
  "id": "hades:node:v2:...",
  "entrypoint_kind": "http_route",
  "label": "GET /generale/soggetti-attivi/",
  "framework": "symfony",
  "method_semantics": "explicit",
  "methods": ["GET"],
  "public_path": "/generale/soggetti-attivi/",
  "public_name": "contact_flock_roles_worker",
  "handler_node_id": "hades:node:v2:...",
  "uncertainty_id": null,
  "trigger": {"kind": "http", "value": "GET /generale/soggetti-attivi/"},
  "match_constraints": {
    "host": null,
    "schemes": [],
    "condition_hash": null
  },
  "registration_occurrence": {
    "kind": "config",
    "path": "config/routes.yaml",
    "structural_pointer": "routes/contact_flock_roles_worker",
    "ordinal": 0
  },
  "evidence": {
    "primary": {
      "origin": "verified_from_code",
      "extractor": "php.symfony.v2",
      "source_locator": {
        "kind": "config",
        "path": "config/routes.yaml",
        "structural_pointer": "routes/contact_flock_roles_worker"
      },
      "source_fingerprint": "64-hex",
      "inference_rule": null
    },
    "supporting": [],
    "supporting_omitted_count": 0
  }
}
```

Every entrypoint record contains exactly these common fields: `id`, `entrypoint_kind`, `label`, nullable `framework`, `method_semantics`, `methods` array, nullable `public_path`, nullable `public_name`, nullable `handler_node_id`, nullable `uncertainty_id`, `trigger`, `match_constraints`, `registration_occurrence`, and `evidence`. It references exactly one node whose `identity.variant=entrypoint`; that node's `entrypoint_identity` is assembled from these record fields exactly as section 6.4 specifies and must reproduce the same `id`. The registration source is unambiguous: `node.identity.path == entrypoint.registration_occurrence.path == node.location.path`; it is the route/job/listener registration path, never the handler declaration path. HTTP permits null public name and uses `trigger={"kind":"http","value":"METHOD /path"}`; search still indexes path/trigger. Every non-HTTP kind requires non-null public name. Non-HTTP values are:

| Entrypoint kind | `public_name` | `trigger.kind` / `trigger.value` |
|---|---|---|
| process_main | qualified module/binary main name | `process` / executable or module name |
| cli_command | public command name | `cli` / command name |
| scheduled_job | qualified job name | `schedule` / normalized cron or scheduler expression |
| queue_consumer | qualified consumer name | `queue` / public queue/topic name |
| event_listener | qualified listener name | `event` / public event name |
| rpc_method | qualified service and method | `rpc` / public RPC method |
| public_api | exported qualified symbol | `library` / exported symbol |

For non-HTTP records, `method_semantics="not_applicable"`, `methods=[]`, `public_path=null`, and `match_constraints={"host":null,"schemes":[],"condition_hash":null}`. `trigger` has exactly `kind` and nullable `value`, each at most 256 UTF-8 bytes and secret-redacted. `handler_node_id` is required unless target resolution failed, in which case it is null and `uncertainty_id` is required.

`registration_occurrence` is a line-independent closed union:

```text
ast:    {kind:"ast", path, structural_path, ordinal}
config: {kind:"config", path, structural_pointer, ordinal}
```

Path is safe and source-relative; structural path/pointer uses the normalized rules in section 6.1.2; ordinal is the same-kind registration ordinal at that structural parent. It distinguishes duplicate registrations in the same file and preserves framework ordering without using source lines. `match_constraints` has exactly `host`, `schemes`, and `condition_hash`: host is null or a normalized public host/pattern of at most 253 bytes, schemes is the unique lexical subset of `http|https`, and condition hash is null or the section 6.4 digest of the full redacted route condition. Host, scheme, and condition participate in identity even when they are hidden from the default UI.

HTTP `method_semantics` is `explicit` or `unrestricted`. Explicit routes contain one or more uppercase, unique, lexically sorted methods; a framework's implicit `HEAD` handling for a declared `GET` is not added unless `HEAD` is independently declared in source/configuration. Join multiple methods with `|` in lexical order, so label and trigger are `GET|POST /path`. Unrestricted routes, including Express `all()`, use `methods=[]`, label and trigger value `ALL /path`, and are not rewritten to an invented `ANY` method. HTTP paths remove query/fragment, use one leading slash, collapse duplicate separators, and remove a trailing slash except root unless the detected framework configuration distinguishes trailing-slash routes. Route parameters remain in framework-normalized placeholder form. Non-HTTP entrypoints use stable public trigger/registration identity. Handler is a resolvable relation, not identity: an `entrypoint_handler` overlay may replace the effective handler without changing the entrypoint/node/flow ID.

Framework golden fixtures include Symfony/Laravel host/domain and route-condition differences, two otherwise identical same-file registrations, one job registered with two schedules, and one consumer registered against two queues/topics. Every case produces distinct entrypoint IDs and preserves source registration order.

For libraries, only exported executable functions, methods, and constructors are `public_api` entrypoints. An exported class produces one entrypoint per exported/public callable method or constructor, not one flow for the class; interfaces, constants, types, and non-callable exports remain searchable structural nodes. If entrypoint discovery is `full` and no executable public API exists, zero entrypoints is a valid verified result. If discovery is `partial`, entrypoint count is unknown even if the returned list is empty. Golden fixtures include an unnamed Express route and a library exporting both callable and non-callable declarations.

### 6.6.1 Structural identity records

Call sites, branch groups, and exception scopes are first-class identity records so every referenced prefixed ID can be validated independently of an edge:

```json
{
  "id": "hades:branch:v2:...",
  "kind": "branch_group",
  "owner_node_id": "hades:node:v2:...",
  "structural_path": "body/3/if",
  "ordinal": 0,
  "subtype": "if",
  "continuation_node_id": "hades:node:v2:...",
  "parent_structure_id": null,
  "evidence": {
    "primary": {
      "origin": "verified_from_code",
      "extractor": "php.cfg.v2",
      "source_locator": {
        "kind": "ast",
        "path": "src/Controller/WorkerController.php",
        "structural_path": "declaration/class/WorkerController/method/index/body/3/if"
      },
      "source_fingerprint": "64-hex",
      "inference_rule": null
    },
    "supporting": [],
    "supporting_omitted_count": 0
  }
}
```

`kind` is `call_site`, `branch_group`, or `exception_scope` and requires the matching call-site, branch, or exception-scope ID prefix. Subtypes are: `call` for call site; `if|switch|match|ternary|loop|exception_dispatch|dynamic_dispatch|framework_short_circuit` for branch group; and `try_catch|try_finally|try_catch_finally|framework_exception_handler` for exception scope. The ID is the matching prefix plus SHA-256 JCS of exactly `{kind,owner_node_id,structural_path,ordinal,subtype}`.

Owner is an existing containing named callable, anonymous-callable, module, or entrypoint node. `continuation_node_id` is required for call sites, nullable for a branch with no common merge, and nullable for exception scope with no statically known post-scope continuation. `parent_structure_id` is null or an existing enclosing branch/exception scope; for a call site it may reference only the nearest exception scope. Every non-null structure ID on an edge or uncertainty must resolve to exactly one structure record. Structures are sorted by ID, chunked separately, and are not rendered as graph nodes.

Base structure evidence may use only `verified_from_code` or `inferred`; a structure with primary or supporting origin `unresolved`, `agent_verified`, or `observed_runtime` is rejected. Uncertainty belongs to the node or edge assertion that uses a structure, never to the structure identity record itself. Server-derived overlay structures use the exact `agent_verified` envelope defined in section 11.5 and exist only in the effective projection/audit overlay.

### 6.7 Edge contract

Each edge contains:

```json
{
  "id": "hades:edge:v2:...",
  "source_id": "hades:node:v2:...",
  "target_id": "hades:node:v2:...",
  "relation": "invokes",
  "flow": "conditional",
  "condition": {
    "kind": "predicate",
    "normalized": "user.isAdmin()",
    "hash": "64-hex",
    "polarity": "true"
  },
  "branch_group_id": "hades:branch:v2:...",
  "call_site_id": "hades:call-site:v2:...",
  "exception_scope_id": null,
  "order": 3,
  "uncertainty_id": null,
  "occurrence": {
    "kind": "ast",
    "owner_node_id": "hades:node:v2:...",
    "ast_path": "body/3/consequence/1/call/0",
    "ordinal": 1
  },
  "evidence": {
    "primary": {
      "origin": "verified_from_code",
      "extractor": "php.cfg.v2",
      "source_locator": {
        "kind": "ast",
        "path": "src/Service/Example.php",
        "structural_path": "declaration/class/Example/method/run/body/3/consequence/1/call/0"
      },
      "source_fingerprint": "64-hex",
      "inference_rule": null
    },
    "supporting": [],
    "supporting_omitted_count": 0
  },
  "location": {
    "path": "src/Service/Example.php",
    "line": 42,
    "ordinal": 1
  }
}
```

Every edge has exactly the fields shown. `occurrence` is the following closed union and is part of edge identity:

```text
ast:     {kind:"ast", owner_node_id, ast_path, ordinal}
config:  {kind:"config", owner_node_id, path, structural_pointer, ordinal}
derived: {kind:"derived", owner_node_id, base_edge_id, derivation_kind,
          target_ordinal, exit_ordinal}
```

The owner is the containing callable for executable AST edges and the containing module/entrypoint/configuration node for structural/framework edges. AST path and configuration pointer are normalized, line-independent structural paths. `derived` is forbidden in producer chunks and is created only by the verification overlay service; `derivation_kind` is `verified_target|verified_invocation|verified_return`, target ordinal follows sorted target node IDs, and exit ordinal is null for target/invocation or follows sorted normal-exit node IDs for return. `location` is separate display evidence and is null only for a server-derived verification-overlay edge or a framework edge whose ordered configuration evidence has no single source span; otherwise it has exactly safe path, positive line, and non-negative display ordinal. Source and target are distinct existing node IDs except in exactly two cases: an explicit CFG loop/back edge with `flow=loop`, or a self-recursive `relation=invokes` edge whose non-null call-site occurrence owner equals the callable used as both source and target. Each recursive call site remains a distinct edge. Relation/flow-specific nullable fields follow the rules below; no omitted field changes meaning.

Allowed non-null `flow` values are:

```text
always, conditional, alternative, exception, async, loop
```

Allowed lifecycle-traversable `relation` values are:

```text
enters, routes_to, passes_through, binds, validates, authorizes,
invokes, returns_to, branches_to, merges_at, throws_to,
reads, writes, queries, calls_external, emits, dispatches,
handles, schedules, responds_with, redirects_to, aborts_with, exits_at
```

Allowed non-traversable structural `relation` values are exactly:

```text
declares, contains, imports, inherits, implements,
references, tests, documents, maps_to
```

Their `flow`, `condition`, `branch_group_id`, `call_site_id`, `exception_scope_id`, and `order` fields are null. They remain searchable graph edges but are not lifecycle-traversable unless an adapter also emits a separate executable edge.

Derived v1 summaries such as `route_reaches_table` MUST NOT be emitted as lifecycle edges. The v2 adapter re-emits source facts directly; the backend never converts v1 relationships.

When non-null, `condition.kind` is the constant `predicate`. `condition.normalized` is limited to 256 UTF-8 bytes, strips literals that may contain secrets, and preserves only operator/identifier structure. `condition.hash` uses the exact preimage in section 6.4 before truncation. `condition.polarity` is one of `true`, `false`, `case`, `default`, `loop_body`, `loop_exit`, `exception`, or `finally`. Certainty is never encoded in `flow`; unresolved certainty uses evidence plus `uncertainty_id` while retaining its real control-flow value. The condition matrix is exact: `conditional` requires a condition with `true|false|loop_body|loop_exit`; `alternative` requires `case|default`, except any base or effective edge referencing a `branch_group` whose subtype is `dynamic_dispatch` may carry null or a copied outer condition of any polarity; `exception` requires polarity `exception`; `always` is null except an explicit finally edge may use `finally`; `async` and loop back-edges require null. `order` is a non-negative integer only when framework or AST order is known; otherwise it is null.

Each evidence item has exactly `origin`, `extractor`, `source_locator`, `source_fingerprint`, and nullable `inference_rule`. `source_locator` is the closed union `file={kind:"file",path}`, `ast={kind:"ast",path,structural_path}`, `config={kind:"config",path,structural_pointer}`, or server-only `derived={kind:"derived",base_edge_id}`. `file` is legal only in primary/supporting evidence of the matching `kind=file` inventory node, with the identical identity path/digest; every other producer node/structure/edge/entrypoint fact requires an AST or config locator. Server effective evidence requires `derived`. File/AST/config fingerprints use the exact preimages in section 6.4 and are recomputed against the matching file node. Derived evidence copies the referenced immutable base edge's primary fingerprint; validation follows that edge's locator transitively until it reaches an AST/config locator, rejects cycles, and proves the same retained file digest. `extractor` matches `^[a-z][a-z0-9_.-]{0,127}$`. `inference_rule` is required only for `origin=inferred`. Merge identical semantic facts by sorting evidence with origin rank `agent_verified`, `verified_from_code`, `observed_runtime`, `inferred`, `unresolved`, then extractor, fingerprint, and JCS locator. The first becomes `primary`; retain at most seven unique remaining items in `supporting` and count the rest. Record-context schemas and a negative golden reject file-only evidence on a declaration or edge.

An edge carries a non-null `uncertainty_id` only under this exact matrix. For `candidate_set_knowledge=incomplete|not_applicable`, the semantic subject edge—or the unique unresolved `invokes` edge for a `call_target` call-site subject—carries the ID but is not a candidate/hint; every other edge carrying that ID is an incomplete hint and appears in `candidate_edge_ids`. For `candidate_set_knowledge=complete`, every and only candidate edge carries the ID; an edge-subject uncertainty includes its subject in that array, while a call-site subject has only its listed candidate invocations. An edge with primary `unresolved` must be that exact subject; an `inferred` edge carrying the ID must be a listed incomplete hint or complete candidate. No edge may carry an uncertainty for another assertion. Golden validation fixtures cover `not_applicable`, `incomplete`, and `complete` for both subject discriminators.

Every `call_site_id`, `branch_group_id`, and `exception_scope_id` resolves to the matching structure record in section 6.6.1. Every `invokes` edge has a call site; every normal `returns_to` edge targets that structure's continuation node and carries the same ID. `throws_to` carries the lexically selected exception-scope ID when handled, or targets an unhandled exception terminal when null. Structural non-traversable edges set all three fields null. The backend rejects a return with no matching invocation/continuation, an edge whose owner conflicts with the structure owner, and a handled throw with no matching exception scope. Normalized structural paths remove comments/trivia and use child-field names/same-kind sibling ordinals rather than source lines or byte offsets; unrelated line/comment insertion cannot change IDs, while semantic sibling reordering may change affected records.

### 6.8 Evidence is independent of flow and completeness

Allowed `evidence.primary.origin` and supporting evidence origin values are:

```text
verified_from_code, agent_verified, observed_runtime, inferred, unresolved
```

`observed_runtime` is reserved by the schema but is not produced in this release. A conditional edge parsed from an AST is `flow=conditional` and primary origin `verified_from_code`. A graph may contain verified facts while global completeness is partial.

The immutable producer artifact accepts only `verified_from_code`, `inferred`, and `unresolved`. `agent_verified` can appear only in a server-created verification overlay/effective projection, and `observed_runtime` can appear only after a future runtime-evidence contract exists. `GraphV2Normalizer` rejects either origin in base chunks, so a producer cannot bypass verification or runtime ingestion.

`inferred` facts MUST contain an exact inference rule identifier. `unresolved` facts MUST reference an uncertainty record. Neither may be shown as verified.

### 6.9 Completeness contract

Completeness has four dimensions: global, per language, per capability, and per entrypoint flow. The artifact-level envelope stores global/per-language values; each flow record stores its own capability envelope. Per-flow completeness is never duplicated into the unchunked bundle manifest.

The exact envelope is:

```json
{
  "status": "full",
  "capabilities": {
    "inventory": {"status": "full", "reasons": []},
    "entrypoint_discovery": {"status": "full", "reasons": []},
    "symbol_resolution": {"status": "full", "reasons": []},
    "call_graph": {"status": "full", "reasons": []},
    "control_flow": {"status": "full", "reasons": []},
    "framework_lifecycle": {"status": "full", "reasons": []},
    "exceptions": {"status": "full", "reasons": []},
    "async": {"status": "full", "reasons": []},
    "data_access": {"status": "full", "reasons": []}
  },
  "languages": [
    {
      "language": "php",
      "status": "full",
      "capabilities": {
        "inventory": {"status": "full", "reasons": []},
        "entrypoint_discovery": {"status": "full", "reasons": []},
        "symbol_resolution": {"status": "full", "reasons": []},
        "call_graph": {"status": "full", "reasons": []},
        "control_flow": {"status": "full", "reasons": []},
        "framework_lifecycle": {"status": "full", "reasons": []},
        "exceptions": {"status": "full", "reasons": []},
        "async": {"status": "full", "reasons": []},
        "data_access": {"status": "full", "reasons": []}
      }
    }
  ]
}
```

Every `capabilities` object contains all nine keys in the fixed order above. Language and flow capability values use the same `{status,reasons}` shape. The artifact-level `languages` array is sorted by language; flow records are sorted by flow ID in the `flows` chunk. `framework_lifecycle` is `not_applicable` for a generic entrypoint with no detected framework; it is never silently full. SQL uses `not_applicable` for entrypoint discovery, call graph, control flow, framework lifecycle, exceptions, and async. An unsupported source language has full/partial inventory as measured and `unsupported` for executable capabilities. `data_access` applies to all languages; verified absence is full.

Required capabilities are:

```text
inventory, entrypoint_discovery, symbol_resolution, call_graph,
control_flow, framework_lifecycle, exceptions, async, data_access
```

Each capability value is one of:

```text
full, partial, unsupported, not_applicable
```

The top-level `completeness.status` is:

- `full` only when every applicable capability for every detected in-scope language and every entrypoint flow is `full` or `not_applicable`;
- `partial` otherwise.

Every `partial` or `unsupported` value has one or more counted reasons:

```json
{
  "status": "partial",
  "reasons": [
    {
      "code": "parser_unavailable",
      "count": 12,
      "language": "typescript",
      "paths_sample": ["src/example.ts"]
    }
  ]
}
```

Reason arrays are sorted by code, language (null first), then first sampled path. `paths_sample` is the first ten affected NFC source-relative paths in lexical order, never an arbitrary sample.

`graph_contract.coverage` is exactly:

```json
{
  "scope": {
    "included_roots": ["."],
    "excluded_config_sha256": "64-hex",
    "excluded_path_count": 12000
  },
  "files": {
    "discovered": 3117,
    "hashed": 3117,
    "parser_candidates": 3056,
    "analyzed": 3056,
    "unsupported": 0,
    "failed": 0,
    "too_large": 0,
    "budget_omitted": 0
  },
  "entrypoints": {
    "detected": 1633,
    "analyzed": 1633,
    "partial": 0,
    "by_kind": {"http_route": 1633}
  },
  "records": {
    "nodes": 0,
    "structures": 0,
    "edges": 0,
    "flows": 0,
    "flow_steps": 0,
    "uncertainties": 0,
    "omitted_by_bundle_budget": 0
  }
}
```

All counts are non-negative integers. `by_kind` contains every entrypoint kind with a detected nonzero value and is key-sorted. Coverage describes represented/processed units; completeness determines whether those units are the full in-scope population.

`paths_sample` contains at most ten source-relative paths. Required reason codes are:

```text
parser_unavailable, parser_failed, unsupported_language,
file_read_failed, file_too_large, record_too_large, resource_budget_reached,
entrypoint_unresolved, call_target_unresolved, dynamic_dispatch,
reflection_or_generated_code, framework_config_unresolved,
exception_target_unresolved, async_target_unresolved,
external_boundary_unresolved, symlink_unavailable,
submodule_unavailable, graphify_candidate, invalid_source_fact,
verified_target_not_materialized
```

A known typed external boundary is complete for static topology and does not by itself create a partial reason. Only an unresolved boundary does.

Configured exclusions such as `vendor`, `node_modules`, generated output, build output, and explicitly ignored directories are out of scope and do not by themselves make the graph partial. Any omitted in-scope file, parser failure, unresolved required target, or resource budget makes the affected capability and graph partial.

### 6.10 Zero versus unknown

Every API count that could be incomplete uses this shape:

```json
{
  "represented": 3,
  "value": null,
  "knowledge": "unknown",
  "reason": "dynamic_dispatch"
}
```

A numeric zero is legal only with:

```json
{
  "represented": 0,
  "value": 0,
  "knowledge": "absence_verified",
  "reason": null
}
```

For a complete nonzero count, `represented=value` and `knowledge=exact`. `represented` is always the exact number of records currently represented and may be nonzero while `value` is null. Allowed knowledge values are `exact`, `absence_verified`, and `unknown`. `reason` is null for exact/absence; when multiple completeness reasons apply, it is the lexically smallest applicable normalized reason code. This rule applies to entrypoints, stages, callers, callees, dependencies, impact, branches, exceptions, async flows, data effects, uncertainties, and terminal outcomes. The frontend renders unknown with represented records as `N+` and unknown with none as `?`; it MUST NOT convert `null` into zero. Golden fixtures include two simultaneous partial reasons and assert the same selected reason in Python, PHP, and TypeScript.

### 6.11 Uncertainty contract

Each exact unresolved assertion creates one uncertainty:

```json
{
  "id": "hades:uncertainty:v2:...",
  "domain": "graph",
  "subject": {
    "call_site_id": "hades:call-site:v2:..."
  },
  "resolution_kind": "call_target",
  "reason_code": "dynamic_dispatch",
  "question": "Which implementation can this call site invoke in this source snapshot?",
  "evidence_requirements": [
    "inspect_service_container_configuration",
    "inspect_all_assignments_to_receiver_type"
  ],
  "source_refs": [
    {"path": "src/Service/Example.php", "line": 42}
  ],
  "candidate_target_node_ids": [],
  "candidate_edge_ids": [],
  "candidate_set_knowledge": "not_applicable",
  "priority": "high",
  "impact": "May change the request lifecycle after authorization",
  "fingerprint": "64-hex"
}
```

`resolution_kind` is the semantic fact being resolved: `call_target`, `entrypoint_handler`, `async_target`, `exception_target`, `framework_target`, or `external_target`. There is deliberately no `candidate_set` resolution kind. `call_target` requires a `call_site_id` subject; each of the other five kinds requires the exact unresolved `edge_id` subject. The semantic kind always controls relation/flow compatibility, target kinds, cardinality, and side effects, whether zero, one, or many candidates were extracted.

`candidate_set_knowledge` is the independent closed enum `complete|incomplete|not_applicable`:

- `complete` requires non-empty `candidate_edge_ids` and `candidate_target_node_ids`, and is legal only when a native extractor proves an exhaustive closed-world set of at most 20 possibilities. For an edge subject, `candidate_edge_ids` equals exactly every artifact edge carrying this uncertainty, the subject is deterministically the lexicographically smallest candidate edge ID after canonical IDs are assigned, and all candidates satisfy the semantic resolution row. For a call-site subject, it equals exactly every candidate `invokes` edge carrying this uncertainty; each shares the subject call site, has a distinct target, and owns an unambiguous complete companion group of every `returns_to` edge for that call-site/target continuation. Companion returns do not carry the uncertainty and never appear in worker selection arrays. No other edge may carry the uncertainty.
- `incomplete` requires at least one non-empty candidate array and means those values are locator hints only. Every candidate edge carries this uncertainty and has primary origin `inferred`; every candidate edge target appears in `candidate_target_node_ids`; target-only hints are legal, edge-only hints are not. A truncated or heuristic set can never be promoted wholesale.
- `not_applicable` requires both candidate arrays to be empty.

Placeholder ownership is exact. `incomplete|not_applicable` requires exactly one assertion-exclusive `unknown_boundary` node carrying this uncertainty; the semantic subject edge, or the unique unresolved invocation for a call-site subject, targets that boundary. The subject validates the semantic row's source, relation, flow, and required structure, but the placeholder is exempt from allowed resolved-target kinds and cardinality. Every incomplete hint target must have an allowed resolved-target kind. `complete` has no unknown-boundary placeholder for this uncertainty: all listed candidates, including an edge-kind subject, validate the full semantic row. The matrix cardinality applies to the complete universe and to a verified promoted/result target set, not to the number of incomplete hints. `reject_unresolved_subject` revalidates the subject half plus exclusive boundary ownership. Import rejects every other combination.

For `complete`, `candidate_target_node_ids` equals the sorted unique target IDs of `candidate_edge_ids` and the full immutable candidate universe satisfies the semantic row's target compatibility and cardinality. Every call-site candidate has primary origin `inferred`. For an edge subject, exactly the lexically selected subject candidate may have primary origin `unresolved`; every other candidate is `inferred`. All candidate IDs exist in the same immutable artifact, candidates belonging to another assertion are rejected, and import, claim, and completion recompute the whole closure and deterministic subject choice. `question` and `impact` are Unicode NFC with collapsed whitespace and maximum lengths of 500 and 1,000 UTF-8 bytes. `evidence_requirements` contains 1–16 unique lower-snake-case identifiers of at most 80 bytes. `source_refs` contains at most 20 source-relative path/line records. Each candidate array contains at most 20 unique IDs. Priority is `low`, `normal`, `high`, or `critical`.

All edges in one `complete` candidate set express the same semantic assertion: they share source, relation, occurrence owner/structural path/ordinal, call-site ID, exception scope, outer condition, outer branch parent, and order; have unique compatible targets; and differ only by `target_id` plus the deterministic evidence-primary distinction above. Base AST/config occurrences have no target ordinal; ordering is the sorted candidate ID arrays, while `target_ordinal` exists only on server-derived overlay occurrences. A one-target set copies the real outer flow allowed by its semantic row (`always|conditional|alternative`, or unchanged `async|exception`), condition, and outer branch group; it creates no `dynamic_dispatch` group. A multi-target synchronous call/entrypoint/framework/external set uses `flow=alternative` with null or copied outer condition as section 6.7 permits. A multi-target async set retains `flow=async` with null condition; a multi-target exception set retains `flow=exception` with its exact exception condition/scope. Every multi-target set references exactly one `dynamic_dispatch` branch group whose parent preserves outer branch/exception precedence; that group never relaxes the async/exception condition matrix. It is assertion-exclusive: it is referenced only by this uncertainty's candidate edges and their frontier FlowSteps, never by unrelated topology; import proves the closure. For a call-site set candidates additionally use `relation=invokes`. Golden fixtures cover one versus many candidates, a genuine one-target switch arm, async/exception conditions, input permutation, and base dynamic-dispatch validation.

The uncertainty fingerprint uses the exact object in section 6.4, including `resolution_kind`. It deliberately excludes `artifact_graph_version` so artifact hashing is not circular. The uncertainty ID is its prefix plus that fingerprint. One uncertainty maps to one graph verification work item; the work-item deduplication key separately hashes domain, project, binding, target artifact version, and uncertainty fingerprint.

#### 6.11.1 Resolution compatibility matrix

Import, queue claim, and completion enforce this closed matrix; “existing target” never means arbitrary node:

| Resolution kind | Subject relation / flow | Allowed target node kinds | Cardinality | Required structure |
|---|---|---|---:|---|
| `call_target` | relation exactly `invokes`; non-complete has one exact subject invocation, complete has 1–20 candidate invocations sharing the call site; `always|conditional|alternative` | `function|method|controller|service|domain|repository|middleware|guard|authorization|validator|binding|listener|job` | 1–20 | subject `call_site` with owner and continuation |
| `entrypoint_handler` | `routes_to`; `always|conditional|alternative` | `function|method|controller|service|listener|job` | exactly 1 | source must be the asserted entrypoint/pipeline and its entrypoint record must reference the uncertainty |
| `async_target` | `emits|dispatches|schedules`; `async` | `event|listener|job|queue|async_boundary|function|method|service` | 1–20 | source occurrence; child flow optional until materialized |
| `exception_target` | `throws_to|handles`; `exception` | `exception|listener|framework_boundary|function|method|service` | 1–20 | non-null matching exception scope |
| `framework_target` | `passes_through|binds|validates|authorizes|routes_to|handles`; `always|conditional|alternative` | `middleware|guard|authorization|validator|binding|controller|framework_boundary|function|method|service` | 1–20 | source occurrence and detected framework |
| `external_target` | `calls_external|reads|writes|queries`; `always|conditional|alternative|async` | `integration|external_boundary|model|repository|table|query|cache|storage|queue` | 1–20 | source occurrence |

An anonymous callable is represented by node kind `function` and is therefore allowed wherever `function` appears. Non-HTTP scheduled/consumer/listener entrypoints may resolve to the explicit `job|listener` semantic handler; queues remain trigger/resource nodes, not executable handlers. A call located inside an exception or loop retains that stage/backbone context but its invocation flow is still `always|conditional|alternative`; async dispatch is modeled by the async row and never receives an inline return. A complete candidate set still uses its uncertainty's semantic row (`invokes` therefore uses `call_target`). The full immutable universe always satisfies that row; for a `verified` result the promoted subset also satisfies its target/cardinality constraints, while a `contradicted` result is the explicit zero-promoted exception and suppresses the otherwise valid universe. `reject_unresolved_subject` validates only the subject half of the same row. Golden fixtures try every legal row, including non-HTTP handlers and unresolved ORM/model data targets, and reject wrong relation, flow, target kind, cardinality, project, structure, and handler multiplicity.

## 7. Lifecycle model

### 7.1 Flow records

Every entrypoint has one synchronous lifecycle flow record:

```json
{
  "id": "hades:flow:v2:...",
  "entrypoint_id": "hades:node:v2:...",
  "root_node_id": "hades:node:v2:...",
  "kind": "request_lifecycle",
  "represented_step_count": 42,
  "terminal_count": {"represented": 3, "value": null, "knowledge": "unknown", "reason": "exception_target_unresolved"},
  "linked_async_flow_count": {"represented": 1, "value": 1, "knowledge": "exact", "reason": null},
  "stage_counts": {},
  "completeness": {},
  "uncertainty_count": {"represented": 2, "value": null, "knowledge": "unknown", "reason": "dynamic_dispatch"}
}
```

`kind` is `request_lifecycle` for an HTTP root, `execution_flow` for a non-HTTP root, and `async_flow` for a linked asynchronous root. The flow ID hashes canonical JSON containing external `entrypoint_id`, `root_node_id`, and kind. In a synchronous flow both IDs reference the entrypoint; a linked flow retains the external entrypoint ID and uses the event/job/queue/async target as `root_node_id`. Async execution is never drawn as if it occurred before the synchronous HTTP response.

`completeness` on every flow has exactly `status` plus the same fixed nine-key `capabilities` object defined in section 6.9. It is the sole per-flow completeness record. `stage_counts` has every applicable stage key and a `CountKnowledge` value; keys are emitted in the stage order from section 7.2.

Flow membership is stored as one bounded record per reachable edge:

```json
{
  "id": "hades:flow-step:v2:...",
  "flow_id": "hades:flow:v2:...",
  "edge_id": "hades:edge:v2:...",
  "stage_from": "handler",
  "stage_to": "domain",
  "min_depth": 6,
  "branch_group_id": "hades:branch:v2:...",
  "async_context": "synchronous",
  "async_child_flow_id": null,
  "async_cycle": false,
  "backbone_role": "branch",
  "order_key": "05:000006:source:target:edge"
}
```

The flow-step ID hashes the exact object in section 6.4. There is exactly one step per that tuple. This permits the same shared canonical edge to play two lifecycle roles without enumerating caller paths; the finite upper bound is the number of edges times the fixed stage count. `async_context` is `synchronous` or `linked_async`; an async dispatch step in a parent flow sets `async_child_flow_id`. Required boolean `async_cycle` is true if and only if that child flow ID is already in the current traversal's ancestor stack; a global dedup hit from a sibling branch reuses the child with `async_cycle=false`. True requires a non-null child flow and `backbone_role=async`. `backbone_role` is `mandatory`, `branch`, `exception`, `loop`, or `async`. Loop/back-edge state appears once per stage context. Golden fixtures distinguish sibling duplicate dispatch from self-reschedule. The producer computes and serializes all flow and flow-step records before artifact hashing. The backend validates and projects them one-to-one; it never recomputes traversal, stage assignment, or backbone roles. The browser therefore never performs unbounded traversal.

Backbone roles are deterministic. On the synchronous subgraph, compute node dominators to a fixed point: the entry dominates only itself initially; every other reachable node starts with all reachable synchronous nodes; repeatedly set each non-entry dominator set to itself plus the intersection of predecessor sets. A node is mandatory when it dominates every reachable synchronous terminal. Edge mandatory status uses edge dominance, not merely mandatory endpoints: for calculation only, split each eligible edge with one synthetic node, rerun the same dominator test, and mark the edge mandatory only when its synthetic node dominates every reachable synchronous terminal. Synthetic calculation nodes are never serialized. A step is `mandatory` only when its edge is mandatory and `flow=always`. An exception edge is `exception`; an explicit CFG loop/back edge or an invocation whose source and target callable belong to the same recursive SCC is `loop`; an async edge is `async`; all remaining steps are `branch`. Recursive invocation edges retain their real `flow=always|conditional|alternative`; recursion is a flow-step role, not a certainty/control-flow rewrite. If no terminal is provable, compute mandatory nodes/edges only through the first unresolved boundary and mark the flow partial.

### 7.2 Lifecycle stages

The stage value belongs to the flow membership, because the same code node can play different roles for different entrypoints. Allowed ordered stages are:

```text
entry, routing, middleware, security, input,
handler, domain, data, integration, async, response, error
```

For HTTP entrypoints the minimum expected chain is:

```text
ingress/router
→ ordered global, group, and route middleware
→ authentication, authorization, binding, and validation
→ controller/handler
→ domain services, data access, and integrations
→ response, redirect, abort, or exception
```

Middleware short-circuits and exception paths are first-class branches. Framework/vendor internals are represented as bounded `framework_boundary` nodes. They are `verified_from_code` only when framework, version, and applicable configuration were detected successfully; otherwise the affected flow is partial.

### 7.3 Traversal construction algorithm

Before entrypoint traversal, build context-correct interprocedural summaries. Every call site has one continuation block and one `call_site_id`. For each `(callable_id, input_stage)`, a summary contains reachable edge-stage states, normal exits, exception exits, terminals, effects, async dispatches, and uncertainties. Build the callable call graph, compute strongly connected components, and process its condensation DAG in reverse topological order. For an acyclic call, union the callee summary and resume only through the `returns_to` edge with that invocation's `call_site_id`. For a recursive SCC, initialize every member from intraprocedural edges, repeatedly apply member summaries until no set grows, retain each internal invocation's real `flow=always|conditional|alternative`, mark its serialized flow step `backbone_role=loop`, and still resume only the matching continuation. Exception propagation selects only the nearest matching `exception_scope_id`; unmatched exceptions go to the caller's matched scope or the unhandled terminal. This monotone finite fixed point replaces call-stack/path enumeration and prevents a shared callee from returning into another caller. Golden fixtures cover both self-recursion and mutual recursion.

The producer then implements this exact deterministic algorithm:

1. Inventory and hash all in-scope files in source-relative lexical order.
2. Run every detected supported language adapter on its own candidate files; never use an exclusive `if/elif` language dispatch.
3. Emit declarations, entrypoints, executable relations, effects, and framework pipeline facts.
4. Construct an intra-procedural CFG for every analyzed executable declaration. Emit explicit branch, merge, loop/back-edge, early return, throw, catch, and finally structure.
5. Resolve call sites. Exact targets are verified edges. A proven exhaustive finite target set is emitted as inferred complete candidates plus one semantic uncertainty per call site. A non-exhaustive set becomes inferred topology-only hints plus an unresolved subject edge to its assertion-exclusive `unknown_boundary`; no hints uses the same subject boundary with `candidate_set_knowledge=not_applicable`.
6. Create matched call-site continuation and lexical exception-scope edges, compute SCCs, and calculate callable summaries to the fixed point above.
7. Connect framework entrypoints through ordered middleware, security/input stages, effective handler, and terminal behavior.
8. For each entrypoint, instantiate its root summary and traverse lifecycle-eligible summary states with uniqueness on `(edge_id, stage_from, stage_to, async_context)`. Continue through all verified branches and record each loop state once. Any edge with non-null `uncertainty_id` is a hard frontier: serialize the exact subject or complete-candidate frontier step, mark the flow partial, and do not enter its target summary. Incomplete hint edges and every companion `returns_to` edge for an uncertain invocation are topology/audit-only and MUST NOT have a `FlowStep`; only the non-complete subject→unknown-boundary edge is shown. Stop also at terminals and explicit external/framework boundaries.
9. When a verified `async` edge is found, include the dispatch edge in the parent flow and materialize/link the deterministic child flow ID; do not continue the child inside the synchronous flow. When the async edge carries uncertainty, include only the parent frontier step with `backbone_role=async` and do not create a child flow. Its child-flow ID may reference only a flow already materialized independently by another verified dispatch for the same external entrypoint/root; otherwise it is null and `async_cycle=false`.
10. Deduplicate async flow creation by `(external_entrypoint_id, async_root_node_id)`. A self-reschedule or async cycle links to the already existing child flow and marks the dispatch step as a cycle; it never recursively creates flows.
11. Record flow-step membership, backbone roles, stage counts, terminal/async/uncertainty counts, and per-flow completeness.
12. Canonicalize, validate referential integrity, sort, calculate digests, and write the chunked bundle.

There is no arbitrary traversal depth or entity-count limit. The producer reads these profile-aware `config.yaml` defaults:

```yaml
hades:
  graph_index:
    max_file_bytes: 8388608
    max_total_source_bytes: 2147483648
    max_wall_seconds: 3600
    max_chunk_uncompressed_bytes: 8388608
    max_bundle_uncompressed_bytes: 536870912
    spool_ttl_seconds: 86400
    graphify_candidates: false
    excluded_paths: []
```

`excluded_paths` contains only user additions; the compiled defaults and compulsory secret exclusions in section 6.3 are always unioned in. Integer validation ranges are: file 1 KiB–1 GiB, total source 1 MiB–16 TiB, wall 30–86,400 seconds, chunk 64 KiB–8 MiB, bundle 8 MiB–4 GiB, and spool TTL 3,600–604,800 seconds. Chunk cannot exceed bundle. Boolean keys accept booleans only. No corresponding `HADES_*` environment variable is added. An operator may lower or raise values within these ranges, but every omitted unit is counted as `resource_budget_reached` or `file_too_large` and the affected capability/flow becomes partial. The backend-advertised artifact limit is authoritative when lower than the local bundle limit.

Call-target resolution precedence is fixed and stops at the first level that produces a unique target:

1. same-file lexical declaration and explicit fully qualified/static target;
2. resolved import/use/namespace/module export;
3. receiver type from declared type, constructor/property assignment, or language AST inference;
4. framework container/DI configuration and route-handler binding;
5. closed-world subtype/implementation candidates inside the in-scope inventory.

A unique target at levels 1–4 is `verified_from_code`. A proven exhaustive level-5 result is a `complete` semantic uncertainty: one candidate copies real outer control without a dynamic group; multiple synchronous candidates use the section 6.11 dynamic alternatives; async/exception candidates retain their exact flow semantics. Every candidate is `inferred`. A non-exhaustive level-5 result is only an `incomplete` hint set behind the unresolved boundary. Reflection, computed module/class names, runtime container mutation, monkey patching, or `eval` creates an unresolved boundary. PHP resolution reads Composer PSR-4 plus Symfony/Laravel container/config facts; Python reads package/import structure plus Django/FastAPI registration; JavaScript/TypeScript reads `package.json`, `tsconfig` path aliases, ESM/CommonJS imports, and framework registrations. No network package resolution is performed.

### 7.4 Supported extraction matrix for this release

| Language | Required framework/entrypoint support | Required CFG constructs |
|---|---|---|
| PHP | Symfony, Laravel, process/CLI entrypoints | branch/merge, switch, early return, try/catch/finally, throw, loops, method/function calls, event/job dispatch |
| Python | Django, FastAPI, `__main__`, CLI/public API | branch/merge, match, early return, try/except/finally, raise, loops, function/method calls, task/event dispatch |
| JavaScript/TypeScript | Express, Next.js, process/CLI/public API | branch/merge, switch, early return, try/catch/finally, throw, loops, function/method calls, Promise/event/job dispatch |
| SQL | data objects and dependencies only | `control_flow=not_applicable`; no executable entrypoint is invented |

Other languages use the adapter interface but report `unsupported` for capabilities they cannot prove. They MUST NOT emit a guessed full lifecycle. Compiled applications are represented through `process_main`, `cli_command`, `event_listener`, or `public_api` once their language adapter exists; the contract requires no HTTP assumption.

Framework adapters MUST follow this exact discovery and ordering table. A source expression that cannot be evaluated statically creates an uncertainty and makes only the affected capability/flow partial; it is never replaced by a guessed order.

| Adapter | Required discovery inputs | Required ordering and lifecycle semantics | Mandatory golden fixtures |
|---|---|---|---|
| Symfony | `composer.json`/lock framework version; `config/routes.{yaml,yml,xml,php}`; imported route resources; PHP route attributes and supported legacy annotations; `config/packages/framework*`, `security*`; `services.{yaml,yml,xml,php}`; kernel/event subscribers/listeners; controller/service container bindings | Apply route import prefix/name/method/host/condition in declared resource order and route priority. Build request/kernel listeners by numeric priority descending, then stable service/source order. Represent router, firewall/access-control/voters, argument/value resolution, controller, response listeners, and exception listeners. A listener returning a response, access denial, redirect, thrown exception, or exception listener response is a distinct terminal/exception arm. | route YAML + attribute collision; imported prefix/method; firewall allow/deny; listener priority; controller argument resolver; early response; handled/unhandled exception |
| Laravel | framework version from Composer; `routes/*.php`; route groups/prefixes/names/domains/methods; `bootstrap/app.php`; legacy `app/Http/Kernel.php`; route service providers; middleware aliases/groups/priority; controller middleware; route/model bindings; FormRequest/validation; policies/gates; exception handler; events/listeners/jobs/queues; console kernel/scheduler | Preserve route declaration order after group expansion. Effective middleware order is global, group expansion, route/controller middleware, then configured middleware priority where Laravel applies it; deduplicate exactly as framework registration does. Represent binding, authentication/authorization, validation, handler, terminating middleware, response, redirect/abort, and exception renderer. Job/event dispatch creates linked async flow, never an inline continuation. | nested route groups; resource route; middleware alias/group/priority; binding miss; FormRequest pass/fail; policy allow/deny; redirect/abort; exception render; queued job/event |
| Django | installed version; settings modules; `ROOT_URLCONF`; recursive `urlpatterns`, `include`, `path`, `re_path`; converters; function views; class-based `as_view`; decorators; `MIDDLEWARE`; authentication/permission decorators or recognized DRF metadata; exception handlers; management commands; ASGI/WSGI application declarations | Resolve URL patterns in list order with accumulated prefixes/namespaces. Request middleware executes configured top-down; response unwinds bottom-up only for middleware entered; a middleware response short-circuits later request layers and still unwinds entered layers. Resolve class-based dispatch to method arms by HTTP method. Represent converter/binding, decorators/auth, view, response, and exception middleware/handler arms. | nested include/namespace; path vs re_path ordering; CBV GET/POST; decorator denial; middleware short-circuit/unwind; sync/async view; handled exception; management command |
| FastAPI | installed FastAPI/Starlette version; `FastAPI`/`APIRouter` construction; verb and `api_route` decorators; `include_router` prefix/tags/dependencies/default response; app/router/route dependencies; dependency call graph and yield cleanup; middleware; exception handlers; lifespan/events | Expand router prefixes and methods in registration order. Execute app/router/route/decorator dependencies in framework order with cache identity; represent validation/422, dependency exception, handler, response-model serialization, background task/async dispatch, and exception handlers. Middleware order follows the detected Starlette version; if version/order cannot be proven, emit a framework boundary plus uncertainty rather than guessing. | nested routers; dependency levels; dependency cache; yield cleanup; validation failure; middleware; exception handler; sync/async endpoint; background task |
| Express | `package.json` and lock version; ESM/CommonJS imports; `express()`/`Router()`; `use`, verb methods, `all`, `route().verb`; router mounts/prefixes/parameters; error middleware arity; response terminal calls | Preserve registration order exactly. Expand mounts without inventing a method; `.all()` is unrestricted. Normal middleware proceeds only through a proven `next()` continuation; response/redirect/end/send/throw is terminal. `next('route')`, `next(err)`, error middleware, promise rejection, and async handlers are separate arms. A computed path/router/middleware target is unresolved. | nested router mount; same path multiple verbs; `.all`; ordered middleware; `next`, `next('route')`, `next(err)`; response short-circuit; async rejection; error handler |
| Next.js | installed Next version; `app/**/route.{js,jsx,ts,tsx}` exported HTTP handlers; `pages/api/**/*`; `middleware.*` and static matcher config; route groups/dynamic/catch-all segments; supported rewrites/redirects only when statically evaluable; imported handler calls | Use documented file-system precedence for the detected Next version and retain normalized public pattern plus source file. One App Router export is one explicit HTTP method; Pages API handler is unrestricted unless a statically exhaustive method dispatch proves explicit arms. Middleware matcher/redirect/response/next branches precede the route handler. Server/client component render graphs are not HTTP lifecycle entrypoints in this release. | app GET/POST route; pages API method switch; dynamic/catch-all path; middleware matcher/redirect; route group; statically known rewrite; unresolved computed config |

Each fixture asserts entrypoint identity, source order, all short-circuit/error/async arms, stage assignment, evidence origin, and exact completeness reasons. Adapters MUST NOT shell out to framework applications or execute repository code. Version-specific behavior is selected from detected locked versions; an unknown version uses only version-independent facts and marks version-dependent lifecycle portions partial.

The implementation adds these exact mandatory base dependencies in `pyproject.toml`:

```toml
"jsonschema==4.26.0",
"tree-sitter==0.26.0",
"tree-sitter-javascript==0.25.0",
"tree-sitter-typescript==0.23.2",
"tree-sitter-php==0.24.1",
"tree-sitter-python==0.25.0",
```

They are not registered for lazy installation. `jsonschema` is a direct dependency because the mandatory graph-v2 contract imports it. Every pin is locked in `uv.lock`; the official precompiled grammar wheels supply PHP, Python, JavaScript, TypeScript, and TSX without a runtime download or mutable grammar cache. Installation is tested in a clean virtual environment. TypeScript validation includes both TypeScript and TSX canaries while preserving `typescript` as the public language. A missing or incompatible required parser/grammar escapes the legacy graph builder, blocks publication, and is never converted into degraded enrichment; only a failure confined to an ordinary source file after successful canaries is partial.

### 7.5 Graphify

Graphify remains installed only as an optional diagnostic and hint extractor behind `hades.graph_index.graphify_candidates: false` in `config.yaml`. When enabled, it may attach at most 20 existing candidate target node IDs only to a native v2 unresolved call site or unresolved edge already emitted by a language/framework adapter. The adapter then creates the corresponding deterministic inferred hint edges, places their IDs in that existing uncertainty's `candidate_edge_ids`, sets/retains `candidate_set_knowledge=incomplete`, and records supporting evidence whose extractor begins `graphify.`. Graphify can never set `complete`, even if its returned list appears exhaustive. If no native unresolved subject exists, it emits only a local diagnostic and no actionable uncertainty. Graphify cannot create a subject, directly certify a canonical edge, authorize `full` completeness, or provide a v1 fallback. Only a completed verification overlay can promote a Graphify-supported target into the effective lifecycle.

### 7.6 Existing extractor fact conversion

This table tells adapters how to re-emit already detected source facts. It is implementation guidance inside the producer, not a backend v1 conversion layer.

| Existing source fact | Required v2 emission | Stage |
|---|---|---|
| route to effective handler | `entrypoint -[routes_to/always]-> handler or first middleware` | routing |
| global/group/route middleware | ordered `passes_through` edges, plus response/exception short-circuit arms | middleware |
| route/model binding | `binds` | input |
| form/request/schema validation | `validates`, with failure terminal | input |
| auth guard/policy/voter | `authorizes`, with denied terminal | security |
| function/method/static/member call | `invokes`; exact or unresolved evidence as applicable | inherited from flow context |
| return to caller | `returns_to` | inherited from flow context |
| if/switch/match/ternary arm | `branches_to` with branch group and redacted condition | inherited from flow context |
| branch convergence | `merges_at` | inherited from flow context |
| throw/catch/finally | `throws_to` and normal `always` finally edges | error |
| loop body/back/exit | `branches_to` plus `flow=loop` back edge | inherited from flow context |
| ORM/model/table read | `reads` or `queries` from the real callsite | data |
| ORM/model/table mutation | `writes` from the real callsite | data |
| HTTP/mail/storage/cache/external SDK call | `calls_external`, `reads`, or `writes` to a typed boundary | integration/data |
| event emit or job/queue dispatch | `emits` or `dispatches` with `flow=async`, plus linked child flow | async |
| response/redirect/abort/exit | `responds_with`, `redirects_to`, `aborts_with`, or `exits_at` | response/error |
| `route_reaches_table` and similar summaries | omit from lifecycle; recompute as a query summary from real edges | none |

An effect is linked to its actual callsite/callable. Adapters MUST NOT connect every effect directly to the route merely because a bounded resolver found it downstream.

## 8. Chunked graph transport

### 8.1 Bundle layout

The producer writes one small manifest and ordered deterministic-gzip chunks. `graph_contract.completeness` in the manifest contains only global, capability, and per-language completeness; per-flow completeness is carried only by each flow record. The UTF-8 JCS manifest MUST remain at or below 4 MiB. The manifest contains:

```json
{
  "schema": "hades.graph_bundle.v2",
  "artifact_schema": "hades.code_graph.v2",
  "artifact_graph_version": "64-hex",
  "generated_at": "2026-07-16T12:00:00Z",
  "source": {},
  "project": {},
  "graph_contract": {},
  "frameworks": [],
  "languages": [],
  "counts": {
    "frameworks": 0,
    "languages": 0,
    "entrypoints": 0,
    "nodes": 0,
    "structures": 0,
    "edges": 0,
    "flows": 0,
    "flow_steps": 0,
    "uncertainties": 0
  },
  "chunks": [
    {
      "index": 0,
      "kind": "nodes",
      "record_count": 1000,
      "sha256": "64-hex",
      "uncompressed_bytes": 123456,
      "compression": "gzip",
      "compressed_sha256": "64-hex",
      "compressed_bytes": 23456
    }
  ]
}
```

Chunk kinds are exactly:

```text
entrypoints, nodes, structures, edges, flows, flow_steps, uncertainties
```

The kind order above is normative. Chunk indexes are contiguous from zero, descriptors are ordered by index, all chunks for one kind are contiguous, and a later kind can never precede an earlier kind. A kind with zero records has no chunk. Every emitted chunk has 1 or more records; a bundle has 0–512 chunks.

The uncompressed chunk is exactly a JCS UTF-8 object with fields `schema`, `index`, `kind`, and `records`; the values match its descriptor. Records are strictly increasing by public `id` across all chunks of the same kind. No record crosses a chunk boundary. `sha256` and `uncompressed_bytes` cover the exact uncompressed JCS bytes. The wire body is one RFC 1952 gzip member produced with compression level 6, `mtime=0`, no filename/comment/extra field, and OS byte 255. `compressed_sha256` and `compressed_bytes` cover those exact wire bytes. Concatenated gzip members, trailing bytes, and an HTTP `Content-Encoding` header are rejected.

### 8.2 Size rules

- Reuse `config('devboard.artifacts.max_chunk_bytes')`, `config('devboard.artifacts.max_chunks')`, `config('devboard.artifacts.max_artifact_bytes')`, and `backend/app/Services/ArtifactStorageService.php`; do not add a new user-facing environment variable.
- A chunk is at most `min(configured_max_chunk_bytes, 8 MiB)` uncompressed.
- A compressed body is at most the descriptor's `compressed_bytes`, at most 8 MiB, and at most the backend-advertised body limit.
- The logical bundle is at most the existing configured total artifact limit, currently 512 MiB.
- Entity-count caps are removed. Size/CPU limits are safety budgets, not silent sampling rules.
- A record larger than one chunk is not uploaded or removed alone. `GraphBudgetPruner` rejects every atomic unit whose dependency closure contains it, records `record_too_large`, and makes every affected capability/flow partial; unrelated units may still publish. Oversized required envelope metadata is the hard failure `graph_record_too_large`. Final validation still requires zero dangling references, and a manifest never describes a deliberately missing chunk record.
- If total capacity is reached, the producer emits a valid partial manifest with exact omission counts. It MUST NOT label the artifact full.
- Decompression is streaming and aborts before output exceeds the descriptor, the 8 MiB chunk ceiling, the manifest total, or a 100:1 uncompressed-to-compressed ratio. CRC/trailer mismatch, an early EOF, a second member, or any trailing byte fails the import.
- The client spools at `<profile-aware HERMES_HOME>/cache/hades/graph-imports/{project_id}/{workspace_binding_id}/{artifact_graph_version}/`. Directories are mode `0700`; manifest, chunks, resume metadata, and `.lock` are mode `0600`; writes use temp-file plus atomic rename. One exclusive lock guards build/upload/delete. A successfully published or explicitly canceled spool is deleted. An incomplete unlocked spool older than 24 hours is deleted on the next graph-sync cleanup; a locked or currently uploading spool is never age-deleted.

`GraphBudgetPruner` is the only component allowed to omit a valid record for total bundle capacity. It operates after every canonical record ID has been assigned and the complete graph has passed referential validation, but before completeness/coverage finalization, `artifact_graph_version`, chunking, and manifest digests. Pruning never recalculates surviving record IDs; it recalculates only ledgers, artifact digest, chunks, and manifest. It uses these atomic units:

1. One **entrypoint-flow unit** per external entrypoint, containing its entrypoint record/node, root flow, recursively linked async flows, every flow step, referenced edges/structures/nodes, occurrence/anonymous owners, uncertainties and complete candidate closures, and file nodes needed by their locations/evidence.
2. For topology not selected through an entrypoint unit, one **structural-component unit** per weakly connected component under node/edge/structure/uncertainty/reference links, including referenced file nodes. These units never contain an entrypoint record, a `kind=entrypoint` node, a flow, or a flow step; any topology record requiring an excluded entrypoint node is also excluded. An entrypoint node is included if and only if its paired entrypoint record belongs to an accepted entrypoint unit. Non-entrypoint topology shared with another accepted unit may still appear once.
3. One **inventory unit** for each remaining file node.

Entrypoint units sort by entrypoint-kind ordinal, normalized public label, then ID. Structural components sort by their lexically smallest `(safe_path_or_empty,public_id)` member; inventory units sort by path then ID. Starting from required envelope metadata, process every unit in that order. A unit containing any record over the chunk ceiling is rejected whole with `record_too_large` before capacity fitting. For every other unit, union records by public ID with the already accepted set, recompute the candidate coverage/completeness/stage-count ledgers including all rejections seen so far, run the deterministic chunker, and compute the exact candidate manifest plus all uncompressed chunk bytes including wrappers. Accept the whole unit only when the exact total stays within `min(local_bundle_limit,backend_advertised_artifact_limit)` and the chunk-count limit; otherwise reject the whole unit and continue to smaller later units. If even the empty valid artifact envelope with its final omission ledgers exceeds the limit, fail `graph_bundle_budget_too_small` instead of uploading invalid data.

After selection, remove every unreferenced record, recalculate all stage/count/coverage/completeness ledgers, mark affected capabilities/flows partial with `resource_budget_reached`, count each unique excluded record once in `records.omitted_by_bundle_budget`, and only then compute source-independent record ordering, artifact version, chunks, and manifest. A rejected flow unit increments detected-but-not-analyzed/partial entrypoint coverage and emits no half-flow, entrypoint, step, or dangling async link. Final validation requires zero missing references and count/digest agreement. Golden fixtures include a single flow larger than the budget, shared topology across accepted/rejected flows, and a small later unit that still fits; output is permutation-invariant and referentially valid.

### 8.3 Backend import API

The existing Hades API major version remains v1; the artifact contract is v2. Add these authenticated, project-scoped endpoints:

```text
POST /api/hades/v1/graph-imports
PUT  /api/hades/v1/graph-imports/{graphImport}/chunks/{index}
POST /api/hades/v1/graph-imports/{graphImport}/complete
GET  /api/hades/v1/graph-imports/{graphImport}
```

The wire protocol is exact:

| Operation | Request | Success | Idempotency |
|---|---|---|---|
| create | `Content-Type: application/json`; body is the complete bundle manifest | `201` for a new attempt or `200` for an existing live/validated artifact; body `{import_id,attempt_generation,validation_status,publication_status,missing_chunk_indexes,expires_at}` | behavior follows the attempt rules below |
| chunk | `Content-Type: application/vnd.hades.graph-chunk+gzip`; raw body is the single gzip member; path index must exist in manifest | `201` first acceptance or `200` identical replay; body `{index,status:"accepted"}` | identical compressed and uncompressed digests is idempotent; any mismatch returns `409 chunk_digest_conflict` |
| complete | `Content-Type: application/json`; body exactly `{artifact_graph_version:"64-hex"}` | `202` while validation/projection is pending, `200` when already published; body `{import_id,validation_status,publication_status,projection_version|null}` | repeat never creates a second projection generation for the same desired artifact/overlay set |
| get | no body | `200`; body `{import_id,validation_status,publication_status,received_chunks,expected_chunks,missing_chunk_indexes,failure|null,projection_version|null,expires_at}` | read-only |

`manifest_semantic_sha256` is SHA-256 JCS of the manifest after removing only `generated_at`; all schema, source/project/contract/framework/language/count/chunk descriptor fields remain. This permits regeneration of the same semantic artifact at a later time without conflicting merely on its informational timestamp. The first accepted manifest/timestamp remains stored for audit; a replay is never allowed to alter descriptors.

Create-attempt selection locks all rows for project/binding/artifact and applies exactly: if the matching row is `tombstoned|objects_deleted`, return retryable `409 graph_import_cleanup_in_progress` with empty `details`; otherwise a `validated` row returns 200 regardless of chunking because artifact semantic identity has already been proved; a live `staging|validating` row with equal manifest semantic digest returns 200, while a different live manifest returns `409 graph_import_manifest_conflict`; when only `failed|stale` rows exist, create generation `max+1` and accept any valid chunk layout whose recomputed logical artifact digest equals the declared artifact version. After cleanup removes the terminal row, the same request may create a fresh attempt. Thus the same semantic graph may be rechunked on a later import attempt without changing its artifact version. Projection retry/rebuild always reuses a retained validated import and never creates another import attempt.

Only `staging` imports accept chunks. The create endpoint sets `expires_at` to 24 hours after creation; accepting a chunk extends it to 24 hours after that acceptance. `expires_at` applies only to `staging`; `complete` verifies manifest identity and receipt of every declared chunk, then CAS-transitions to `validating`, clears expiry, and dispatches validation—nothing more. An expired staging import becomes `stale` and rejects writes. Authentication/authorization failures are `401/403`; wrong project/binding or unknown import is `404`; schema/body/index errors are `422` with one stable code from `graph_manifest_invalid`, `graph_chunk_invalid`, `graph_chunk_too_large`, `graph_import_not_staging`, or `graph_import_incomplete`; digest conflicts are `409`; a matching import whose cleanup state is not active returns retryable `409 graph_import_cleanup_in_progress` with empty `details`.

Import validation and projection publication are separate state machines. Import `validation_status` is exactly `staging|validating|validated|failed|stale`. Projection `publication_status` is derived from the projection head/attempt and is exactly `not_requested|queued|projecting|ready|failed|stale`. `complete` transitions are: `staging` with all chunks → atomically `validating`, clear expiry, and 202; `staging` missing chunks → 422; `validating` → 202 without duplicate validation; `validated` → 200 only when the exact projection is already ready, otherwise 202 after idempotently requesting it; `failed` → 409 `graph_import_failed`; `stale` → 410 `graph_import_stale`. Validation success makes the immutable import `validated` and requests projection after commit. Validation failure stores code/details and makes the import `failed`. Projection success/failure never mutates import validation status. No transition returns a terminal import to staging.

Validation is asynchronous because a bundle may be 512 MiB. There are exactly four acquired **domain runs**: run 1, then new runs after 10, 30, and 90 seconds. Schema/digest/reference/privacy failures are deterministic and fail immediately. Initial dispatch and reconciliation use the same lease-acquisition transaction: lock the import row, require `status=validating`, require no unexpired execution lease, increment `validation_attempts` and `validation_execution_generation`, generate a random 256-bit run token, store only its lower-case SHA-256 in the execution token column, set start/heartbeat to now, execution expiry to `now()+120 seconds`, and `validation_execution_delivery_claimed_at=null`, commit, then dispatch `ValidateGraphV2Import(import_id,attempt_generation,validation_attempts,validation_execution_generation,raw_run_token)` through `DB::afterCommit()`.

The validator implements `ShouldBeEncrypted` and `ShouldBeUniqueUntilProcessing`, has `$tries=1`, `$timeout=1740`, `$failOnTimeout=true`, `$uniqueFor=300`, and exact unique ID `graph-import:{import_id}:{attempt_generation}:validation:{validation_attempts}`; the raw token exists only in its encrypted queue payload and executing memory and is never stored or logged. It runs on the dedicated `graph-v2` database queue whose `retry_after=1900s`, behind a worker timeout of 1800s. Queue uniqueness only coalesces waiting messages and is never the runtime mutex.

At job entry, before reading chunks or heartbeating, the delivery performs a one-shot CAS from `validation_execution_delivery_claimed_at=null` to `now()` matching import ID, import generation, `status=validating`, domain attempt, execution generation, and token hash. Exactly one delivery can win; a duplicate carrying the same token/generation exits as a successful no-op and cannot read artifacts, heartbeat, or consume another domain attempt. Lease reclamation creates a new execution generation and resets the claim field to null. Every heartbeat hashes the supplied token and CAS-updates only when the full tuple matches and the delivery claim is non-null; it renews the lease to 120 seconds. The job heartbeats before every chunk, after every 1,000-record batch, and in all cases before 30 seconds of monotonic elapsed time. A zero-row CAS aborts immediately as `LostValidationLease` without a terminal write. Success or deterministic failure uses the same full CAS tuple, then clears only its own execution token/lease while retaining audit timestamps and the delivery-claim audit timestamp.

A caught transient failure CAS-records the safe failure detail and clears its current token/lease while keeping `status=validating`. Runs 1–3 schedule `AcquireGraphV2ValidationRun(import_id,attempt_generation)` after exactly 10/30/90 seconds; that wrapper calls the same acquisition service. A transient failure in run 4 writes terminal `failed/graph_validation_infrastructure_failed` only through the fenced CAS. An uncaught crash schedules nothing; lease expiry plus the minute reconciler may acquire the next domain run. Duplicate broker delivery cannot consume a domain attempt unless it wins an expired lease, and maintenance deferral consumes neither. An old worker racing a reclaimed worker cannot heartbeat, publish, fail, clear, or otherwise mutate the import. Projection cannot observe or dispatch from `validating`.

The validation job MUST use two bounded streaming passes over retained chunk blobs; it MUST NOT load and decode the entire logical graph as one JSON object. Pass one streams chunks in manifest order, verifies compressed/uncompressed descriptors, schema, record shape, source identity, counts, chunk/kind order, and strictly increasing IDs, then batch-inserts every record key and file-node path/digest into staging tables. Pass two re-streams the same retained bytes, batch-inserts every typed reference, and validates each file/AST/config evidence locator/fingerprint against the complete file-path index; a server-derived locator is forbidden in producer chunks. Unique constraints detect cross-chunk collisions; set-based anti-joins after pass two detect missing or cross-import references. Batches are at most 1,000 rows and neither pass keeps graph-size-proportional application memory. Only after both passes and every set-based invariant succeed does the job CAS-commit `validated` and request projection. Validation key/reference rows may be deleted after successful validation; the validated import, manifest, chunk metadata, and chunk blobs follow the reachability retention rule in section 9.1 and are not staging garbage.

## 9. Backend storage and atomic projection

The backend repository is `/home/ubuntu/dev-sandbox`. All paths in this section are relative to that repository.

### 9.1 Database migrations

Create these migrations in the listed order. Use ULIDs and the existing project/workspace foreign-key types and deletion policies; do not invent parallel identity columns.

#### `backend/database/migrations/2026_07_16_000100_create_hades_graph_imports_table.php`

Create `hades_graph_imports` with:

```text
id                         ULID primary key
project_id                 existing project FK, indexed
workspace_binding_id       existing workspace binding FK, indexed
hades_agent_id             nullable FK to hades_agents
attempt_generation         unsigned integer, required, starts at 1
schema                     string, required; only hades.code_graph.v2
artifact_graph_version     char(64), required
manifest_semantic_sha256   char(64), required
source_identity            jsonb, required
manifest                   jsonb, required
status                     string, required
completeness_status        string, required
expected_chunks            unsigned integer, required
received_chunks            unsigned integer, default 0
expected_uncompressed_bytes unsigned bigint, required
received_uncompressed_bytes unsigned bigint, default 0
expected_compressed_bytes   unsigned bigint, required
received_compressed_bytes   unsigned bigint, default 0
failure_code               nullable string
failure_details            nullable jsonb
completed_at               nullable timestamp with timezone
validated_at               nullable timestamp with timezone
validation_started_at      nullable timestamp with timezone
validation_heartbeat_at    nullable timestamp with timezone
validation_attempts        unsigned integer, default 0
validation_run_token_hash  nullable char(64)
validation_lease_expires_at nullable timestamp with timezone
expires_at                 nullable timestamp with timezone
created_at / updated_at
```

Allowed status values are `staging`, `validating`, `validated`, `failed`, `stale`. Add unique `(project_id, workspace_binding_id, artifact_graph_version, attempt_generation)` and a partial unique index allowing only one row for the same project/binding/artifact where status is `staging|validating`. Projection states do not appear in this table.

#### `backend/database/migrations/2026_07_16_000200_create_hades_graph_import_chunks_table.php`

Create `hades_graph_import_chunks` with:

```text
id                  ULID primary key
graph_import_id     FK to hades_graph_imports, cascade delete
chunk_index         unsigned integer
kind                string
sha256              char(64)
record_count        unsigned integer
uncompressed_bytes  unsigned integer
compression         string; only gzip
compressed_sha256   char(64)
compressed_bytes    unsigned integer
storage_disk        string
storage_path        string
received_at         timestamp with timezone
created_at / updated_at
```

Add unique `(graph_import_id, chunk_index)`. Storage paths are generated server-side and never accepted from the client.

#### `backend/database/migrations/2026_07_16_000250_create_hades_graph_import_validation_tables.php`

Create `hades_graph_import_record_keys` with `graph_import_id`, `record_kind`, `public_id`, `chunk_index`, and `record_ordinal`; use composite primary key `(graph_import_id, record_kind, public_id)` and index `(graph_import_id, public_id)`. Record kinds are the seven chunk kinds. The only same public ID allowed in two kinds is an `entrypoints` ID paired with exactly one `nodes` record whose `kind=entrypoint`; the validator requires the pair and rejects every other cross-kind collision.

Create `hades_graph_import_file_paths` with `graph_import_id`, safe `path`, `file_node_public_id`, and `file_sha256`; primary key `(graph_import_id,path)`, unique `(graph_import_id,file_node_public_id)`, and import FK cascade. The normalizer inserts every `kind=file` node, then validates every path-bearing identity/location/reference/evidence against this index and digest before projection.

Create `hades_graph_import_references` with a bigint primary key, `graph_import_id`, `owner_record_kind`, `owner_public_id`, `reference_kind`, `target_record_kind`, and `target_public_id`; index `(graph_import_id, target_record_kind, target_public_id)`. The closed reference matrix is: entrypoint→node for entrypoint/handler and →uncertainty when unresolved; structure→node for owner/continuation and →structure for parent; edge→node for source/target/occurrence owner, →structure for call-site/branch/exception scope, and →uncertainty when present; flow→entrypoint/node for entrypoint/root; flow_step→flow/edge, nullable structure branch group, and nullable child flow; a `call_target` uncertainty subject→the exact `call_site` structure; each of the five edge-target uncertainty subjects→the exact edge; uncertainty→candidate nodes/edges; node→node for `source_occurrence.identity.owner_node_id` or `anonymous_callable.identity.owner_node_id`, and →uncertainty only for the assertion-exclusive `unknown_boundary` case in section 6.5. An uncertainty can never use a node, branch group, or exception scope as its subject, and the validator cross-checks resolution kind against subject discriminator. Node owners must be allowed declaration/entrypoint/anonymous-callable nodes in the same import. Composite logical joins use graph import, record kind, and public ID. Both import FKs cascade delete. The normalizer inserts in batches of 1,000 inside the validation transaction. The validator never resolves a reference against another import or active projection.

#### Validated artifact and chunk retention

A `validated` import has `expires_at=null` and is immutable. Its manifest, chunk rows, and exact stored compressed blobs are retained while reachable from any projection row/head, active or previous namespace, graph overlay, verification request/result, graph context within TTL, or normalized Wiki claim/evidence reference. `graph_ref` resolution always reads these retained immutable bytes or their validated indexed representation; it never depends on temporary validation rows.

All graph-import reference writers and cleanup share `GraphArtifactReferenceLock`. Outside any transaction a caller identifies the complete import-ID set, then `within(project_id,workspace_binding_id,import_ids,callback)` opens the outer transaction. It acquires every PostgreSQL advisory lock in total `(project_id,workspace_binding_id,import_id)` order using key `hades:graph-import:v2:{project_id}:{workspace_binding_id}:{import_id}`, then every matching import row `FOR UPDATE` in that order, rechecks exact count/scope, and only then exposes a per-invocation `GraphArtifactReferenceGuard`. Domain locks follow; a writer holding a domain lock may not enter this service. A domain service asserts the guard contains every import before inserting a reference. If a locked reread discovers a different import set, the caller rolls back and retries from outside; it never takes an incremental lock.

`GraphArtifactReachability` composes one fixed internal Laravel tag of read-only providers. Plan 2 registers projection/head/attempt/context reachability; Plan 3 adds verification request/overlay and Wiki reference providers. It throws when no provider is configured, short-circuits on `true`, returns `false` only after at least one provider ran and all returned false, and never catches a provider exception. Namespace reachability and artifact reachability are separate decisions: retaining immutable source bytes does not retain every old physical projection incarnation.

Cleanup uses forward-only crash-resumable state machines inside a graph mutation lease. Namespace phase A enters `GraphArtifactReferenceLock`, rechecks head/attempt/context roots, and CASes only the exact incarnation `active→tombstoned` with an operation ULID; after commit it retires that incarnation's permanent Neo4j projection fence, deletes only `(project,scope,projection_version,projection_incarnation_id)` canonical records, proves zero remain, and keeps the tiny retired fence forever. It then resumes PostgreSQL `tombstoned→neo4j_deleted` and deletes the projection row only after FKs/heads/attempts permit. Artifact phase A begins only after no projection incarnation remains, enters the same reference lock, reruns every artifact provider, and CASes import `active→tombstoned`; after commit it deletes only the stored verified object list, resumes `tombstoned→objects_deleted`, then deletes chunk/validation/import ownership rows last. Resume accepts only the stored operation ID and forward state and never issues a reference guard. A crash or external failure leaves the durable stage for idempotent retry. Once tombstoned, replay, desired-state, publication, and new reference writers reject the row. A retired Neo4j fence is a negative ownership record, not graph data; backup retains it and no cleanup TTL may remove it.

Disposable validation key/reference rows may be removed after the durable uncertainty index is written. `failed|stale` imports become eligible after 24 hours only when unreachable. Terminal projection attempts are audit-retained for exactly 24 hours and are roots only while queued/projecting with an unexpired execution lease; a namespace cannot be tombstoned until its terminal attempts are past retention and removed. A validated import is removable only when no provider retains it and all namespace/context grace has closed. A referenced blob is never deleted due to age or pressure. Raw prefix deletion is forbidden, and every `ArtifactStorageService` deletion path participates in graph maintenance/backup fencing.

#### `backend/database/migrations/2026_07_16_000300_extend_canonical_graph_projections_for_v2.php`

Extend `canonical_graph_projections` with:

```text
graph_import_id             nullable FK, indexed
graph_contract_version      nullable string
artifact_graph_version      nullable char(64), indexed
verification_set_hash       nullable char(64)
projection_version          nullable char(64), indexed
source_identity             nullable jsonb
completeness                nullable jsonb
base_node_count             nullable unsigned bigint
base_relationship_count     nullable unsigned bigint
base_flow_count             nullable unsigned bigint
effective_node_count        nullable unsigned bigint
effective_relationship_count nullable unsigned bigint
effective_flow_count        nullable unsigned bigint
```

Migration `000300` originally adds unique `(project_id, source_scope_type, source_scope_id, projection_version)`. Existing v1 rows remain readable only by rollback code until the cutover and are never selected by a v2 query. Because this migration may already be applied, its bytes are immutable; the forward-only `000375` migration below replaces that v2 uniqueness rule with a non-unique lookup index so every retry can own a fresh physical incarnation.

#### `backend/database/migrations/2026_07_16_000350_create_canonical_graph_projection_heads.php`

Create `canonical_graph_projection_heads` with:

```text
id                              ULID primary key
project_id                      project FK
source_scope_type               string; v2 value workspace_binding
source_scope_id                 string; Hades binding ULID
desired_generation              unsigned bigint, default 0
desired_artifact_graph_version  nullable char(64)
desired_verification_set_hash   nullable char(64)
desired_projection_version      nullable char(64)
active_projection_id            nullable FK to canonical_graph_projections
previous_projection_id          nullable FK to canonical_graph_projections
failed_generation               nullable unsigned bigint
failed_projection_version       nullable char(64)
failed_at                       nullable timestamp with timezone
created_at / updated_at
```

Add unique `(project_id, source_scope_type, source_scope_id)`. Extend `canonical_graph_projection_attempts` with required nullable `desired_generation` and `candidate_projection_version` fields for v2 attempts. V2 readers use this head table; they ignore the legacy `active_graph_version` column.

#### `backend/database/migrations/2026_07_16_000375_add_graph_v2_coordination_state.php`

This is the only migration allowed to amend the already-applied `000100`–`000350` contract. Never edit or replace those historical migration files. The migration is additive/forward-only except for replacing the obsolete v2 scope/version unique index by its non-unique equivalent.

Add `canonical_graph_projection_heads.desired_graph_import_id`, nullable but all-or-none with `desired_artifact_graph_version`, `desired_verification_set_hash`, and `desired_projection_version`. Add the parent unique key `(project_id,workspace_binding_id,id)` to `hades_graph_imports`, then the scoped composite FK `(project_id,source_scope_id,desired_graph_import_id)` to that key with `RESTRICT`, plus an index and a v2 check requiring `source_scope_type='workspace_binding'`. Backfill every existing nonempty desired tuple from the unique same-scope import with the matching artifact version; abort with a diagnostic rather than guess when there are zero or multiple candidates.

Add independent execution-fencing state to graph imports and projection attempts: unsigned `*_execution_generation`, nullable 64-character `*_execution_run_token_hash`, nullable timezone-aware `*_execution_started_at`, `*_execution_heartbeat_at`, `*_execution_lease_expires_at`, and `*_execution_delivery_claimed_at`. Acquisition or reclamation sets the delivery claim null; job entry must win a null-to-timestamp CAS on the complete owner tuple before any read or side effect. A lease owner is identified by `(row ID, logical attempt generation, execution generation, token hash)`; an older owner or duplicate delivery cannot heartbeat, finalize, clear, publish, or clean a replacement owner's work.

Add `hades_graph_imports.artifact_cleanup_state=active|tombstoned|objects_deleted` plus nullable cleanup operation ULID/start/object-deleted timestamps. Add `canonical_graph_projections.namespace_cleanup_state=active|tombstoned|neo4j_deleted` plus nullable cleanup operation ULID/start/Neo4j-deleted timestamps. Both machines are forward-only and can never return to `active`.

Create durable `hades_graph_import_uncertainty_index` rows keyed by `(graph_import_id,public_id)` with the scoped import FK, `record_ordinal`, `chunk_index`, and indexes beginning `(graph_import_id,public_id)` and `(graph_import_id,record_ordinal)`. A successful validation copies only schema-verified uncertainty locators here before temporary validation rows are removed. `GraphV2ArtifactReader::recordsByPublicIds()` resolves these locators by primary public ID, verifies the stored ordinal/chunk against streamed bytes, and never pages the source table by unindexed ordinal ranges.

Drop only the already-applied named `canonical_graph_projections_v2_scope_version_unique` index and create a non-unique scoped lookup on the same logical columns. `canonical_graph_projections.id` becomes the immutable `projection_incarnation_id`; head CAS, not a uniqueness constraint on logical version, is the sole authority over which incarnation is readable. Upgrade tests must start from applied `000100`–`000350`, and a guarded fresh PostgreSQL build must reach the identical schema.

#### `backend/database/migrations/2026_07_16_000400_extend_agent_work_items_for_verification.php`

Extend `agent_work_items` with:

```text
work_kind             string, not null, default 'general'
workspace_binding_id  nullable workspace binding FK, indexed
deduplication_key     nullable char(64)
result                nullable jsonb
result_digest         nullable char(64)
state_version         unsigned bigint, not null, default 1
execution_attempts    unsigned integer, not null, default 0
retry_of_work_item_id nullable self FK, ON DELETE RESTRICT/NO ACTION
verification_execution_epoch_generation nullable unsigned bigint
specialist_fence_id   nullable ULID
specialist_fence_generation unsigned integer, not null, default 0
specialist_fence_state nullable string: active or quarantined
specialist_fence_token_hash nullable char(64)
specialist_containment_kind nullable string: linux_cgroup_v2, windows_job, or darwin_container
specialist_containment_id_hash nullable char(64)
specialist_fence_started_at nullable timestamp with timezone
specialist_fence_quarantined_at nullable timestamp with timezone
```

The same migration creates singleton `hades_verification_execution_epochs` row `name='verification'` with `generation` (unsigned bigint), random `epoch_id` (ULID), and timestamps. Every verification claim copies the current generation to `verification_execution_epoch_generation`; claim, heartbeat, specialist-fence start/quarantine/clear, completion, lease release, and terminal CAS match it. Generic work stores null. Epoch generation is part of the authenticated execution authority but never a user-supplied filter.

Preserve the existing work-item statuses and add `stale`. The stored union is:

```text
draft, queued, claimed, running, completed,
completed_with_incomplete_memory, failed, canceled, stale
```

Verification items use only `queued`, `claimed`, `running`, `completed`, `failed`, `canceled`, and `stale`; they never use `draft` or `completed_with_incomplete_memory`. Lease expiry remains an event handled by the existing lease-release/reclaim policy rather than a new terminal status. Add a partial unique index on `(project_id, deduplication_key)` where `deduplication_key` is non-null and a partial unique index on `specialist_fence_id` where non-null. Fence fields are all-null except generation, or all required for `active|quarantined`; only verification work may carry them. Update status constants to include `stale` without changing historical terminal values.

Every create starts at `state_version=1`; every committed status, claim owner, lease, result, or terminal-reason mutation atomically increments it exactly once. Read/list/claim/heartbeat/complete DTOs include `state_version` and RFC3339 `remote_updated_at`. A heartbeat that actually extends the lease increments `state_version` once but never `execution_attempts`; a semantic no-op changes neither. Earlier product shorthand called lease timeout `expired`; this contract deliberately does not persist an `expired` status because the existing queue reclaims the same live item. The audit event is `lease_expired`, after which the item returns to `queued` with claim fields cleared, state version incremented, and attempt generation unchanged. Each successful new claim increments `execution_attempts`; after attempt 10, the next claim/reclaim boundary atomically terminalizes that generation as `failed/verification_execution_attempts_exhausted` instead of creating an eleventh execution. Human retry creates a new request/work-item generation with zero executions. `stale` is reserved for a target/source version that can no longer be completed.

A restored PostgreSQL snapshot may contain claims and `active|quarantined` specialist fences whose raw tokens/process domains belong to the source deployment. Before any restored worker or reconciliation starts, the restore-target-only recovery command from section 14.2 holds the exclusive global lock, verifies the sealed consistency set and all stores, atomically increments the singleton verification epoch, and processes every nonterminal verification row from the copied older epoch in ascending ID order. It clears copied claim/lease/fence token/containment fields, records `verification.restore_epoch_rotated`, increments `state_version`, and returns work below ten attempts to `queued`; exhausted work becomes `failed/verification_execution_attempts_exhausted`. Evidence/results/attempt counts remain immutable. All old-epoch heartbeat, fence-clear, and completion requests then fail CAS even if the source host reaches the restored endpoint. The recovery audit binds old/new epoch, consistency set, item counts, and manifest digest. It never rotates a live production database and is idempotent for the exact restored target/new epoch.

Verification-request priority is `low|normal|high|critical`. The existing work-item transport represents `critical` as `urgent`; API filtering and ordering translate `critical↔urgent` only for verification work. Verification payloads and request DTOs never expose `urgent`, and generic work retains its existing priority contract. Add explicit indexes on every new referencing FK, including `agent_work_items.retry_of_work_item_id`.

#### `backend/database/migrations/2026_07_16_000500_create_verification_requests_table.php`

Create `verification_requests` with:

```text
id                       ULID primary key
agent_work_item_id       unique FK to agent_work_items, ON DELETE RESTRICT/NO ACTION
project_id               project FK, indexed
workspace_binding_id     workspace binding FK, indexed
domain                   string: graph or wiki
target_type              string
target_id                string
target_version           string
assertion_fingerprint    char(64)
source_graph_import_id   nullable graph import FK, indexed, ON DELETE RESTRICT/NO ACTION
source_artifact_graph_version nullable char(64)
attempt_generation       unsigned integer, default 1
retry_of_request_id      nullable self FK, ON DELETE RESTRICT/NO ACTION
reason_code              string
question                 text
evidence_requirements    jsonb
priority                 string: low, normal, high, critical
impact                   text
resolution               nullable string: verified, contradicted, deferred
result                    nullable jsonb
result_digest             nullable char(64)
resolved_at              nullable timestamp with timezone
superseded_at            nullable timestamp with timezone
created_at / updated_at
```

Add unique `(project_id, workspace_binding_id, domain, target_type, target_id, target_version, assertion_fingerprint, attempt_generation)`. Generation starts at one and increments only inside the locked retry/reactivation transaction: either explicit human retry or exact stale A→B→A reactivation from section 11.2. Both link to the immediately previous request; no other reconciliation path increments it. A concurrency test races human retry with auto-reactivation and proves that row locking plus the unique key creates at most one next generation.

Graph requests and Wiki requests with an available source snapshot require `source_graph_import_id` and the exact matching artifact version. Only a Wiki request whose source snapshot is explicitly unavailable may store both fields null. The FK is the authoritative reachability join; cleanup never parses result JSON to infer source ownership.

#### `backend/database/migrations/2026_07_16_000525_extend_wiki_revisions_for_verification.php`

Extend the existing `wiki_revisions` table; do not edit the June core migration. Add nullable `workspace_binding_id` FK (legacy rows may be null), nullable indexed `content_sha256` char(64), nullable self-FK `verification_source_revision_id` with RESTRICT/NO ACTION, nullable `result_digest` char(64), and nullable JSONB `generator_metadata`. Every new Hades-generated revision requires binding plus content digest. A verification-created revision also requires source revision and result digest. Generator metadata stores bounded generator/source-snapshot descriptors only; claims and graph ownership use normalized tables below.

#### `backend/database/migrations/2026_07_16_000550_create_wiki_verification_claims_table.php`

Create `wiki_verification_claims` with ULID `id`, `wiki_revision_id` FK `ON DELETE CASCADE`, `ordinal`, `byte_start`, `byte_end`, `normalized_text`, `fingerprint` char(64), nullable `verdict=supported|contradicted`, nullable JSONB `evidence_indexes`, nullable `reason`, and `created_at`. Add unique `(wiki_revision_id,ordinal)` and `(wiki_revision_id,fingerprint)`, require nonnegative half-open ranges and contiguous ordinals at service validation, and cascade rows only when their owning Wiki revision is deliberately deleted. This table is the immutable claim ledger returned by show/read APIs.

#### `backend/database/migrations/2026_07_16_000575_create_wiki_graph_artifact_references_table.php`

Create `wiki_graph_artifact_references` with ULID `id`, `wiki_revision_id` FK `ON DELETE CASCADE`, nullable `verification_request_id` FK with RESTRICT/NO ACTION, required project/binding FKs with project-owned cascade, required graph-import FK with RESTRICT/NO ACTION, `reference_kind=source_snapshot|evidence`, `artifact_graph_version`, nullable `evidence_index`, nullable `record_kind=node|edge`, nullable `public_id`, nullable `source_fingerprint`, and `created_at`. A `source_snapshot` row has all evidence-record fields null; an `evidence` row requires evidence index, record kind, public ID, and fingerprint. Add one PostgreSQL `UNIQUE NULLS NOT DISTINCT` index over `(wiki_revision_id,verification_request_id,graph_import_id,reference_kind,evidence_index,record_kind,public_id,source_fingerprint)`; the SQLite test migration uses an equivalent `COALESCE` expression index. Add standalone indexes beginning with `graph_import_id`, `verification_request_id`, `project_id`, and `workspace_binding_id`, plus `(project_id,workspace_binding_id,graph_import_id)`; PostgreSQL does not add them automatically for FKs. This is the sole Wiki query source for graph-artifact reachability.

#### `backend/database/migrations/2026_07_16_000600_create_graph_verification_overlays_table.php`

Create `graph_verification_overlays` with:

```text
id                       ULID primary key
verification_request_id  unique FK to verification_requests, ON DELETE RESTRICT/NO ACTION
graph_import_id          graph import FK, indexed, ON DELETE RESTRICT/NO ACTION
project_id               project FK, indexed
workspace_binding_id     workspace binding FK, indexed
artifact_graph_version   char(64), indexed
assertion_fingerprint    char(64)
verdict                  string: verified or contradicted
operation                string: resolve_candidate_set, resolve_call_targets, resolve_edge_targets, or reject_unresolved_subject
overlay                  jsonb
evidence                 jsonb
result_digest            char(64)
created_at
```

Add unique `(project_id, workspace_binding_id, artifact_graph_version, assertion_fingerprint)` and a scoped parent key `(project_id,workspace_binding_id,graph_import_id,id)` for membership FKs. Overlay rows are append-only: no update endpoint exists. An overlay remains eligible across an identical reimport only when the immutable `artifact_graph_version` is exactly equal; membership records preserve both the new base import and the original overlay-source import, so this reuse cannot erase provenance. `deferred` creates no overlay.

#### `backend/database/migrations/2026_07_16_000625_create_projection_verification_overlay_links_table.php`

Create `canonical_graph_desired_verification_overlays` and `canonical_graph_projection_verification_overlays`. The first is the durable exact overlay set desired by a head before a candidate exists; the second is the immutable exact set used by a particular projection incarnation. Every membership row stores `project_id`, `workspace_binding_id`, `base_graph_import_id`, `overlay_source_graph_import_id`, and `graph_verification_overlay_id`.

Add parent unique keys `(project_id,source_scope_id,id)` to v2 projection heads and projection rows. Desired membership FK `(project_id,workspace_binding_id,projection_head_id)` references head `(project_id,source_scope_id,id)`; projection membership FK `(project_id,workspace_binding_id,canonical_graph_projection_id)` references projection `(project_id,source_scope_id,id)`. Existing v2 checks require `source_scope_type='workspace_binding'`, so child binding is the parent scope ID without adding a duplicate parent column. Both import IDs use separate scoped composite FKs to imports `(project_id,workspace_binding_id,id)` with `RESTRICT`; overlay FK `(project_id,workspace_binding_id,overlay_source_graph_import_id,graph_verification_overlay_id)` references overlay `(project_id,workspace_binding_id,graph_import_id,id)`. Head/projection ownership cascades; overlay/import ownership restricts. Primary keys are `(projection_head_id,graph_verification_overlay_id)` and `(canonical_graph_projection_id,graph_verification_overlay_id)`. Add indexes beginning with overlay ID and with each import FK. Cross-project/binding links are impossible.

`base_graph_import_id` may differ from `overlay_source_graph_import_id` only when both imports have the same immutable `artifact_graph_version`. Under the ordered reference/scope/head locks, the service recomputes the canonical sorted overlay set and requires its hash to equal the owning head/projection `verification_set_hash`. Verification-chain retention reads these membership tables directly and is intentionally separate from artifact-level reachability.

Verification audit retention is independent of generic queue and graph-artifact cleanup. Generic `agent_work_items` cleanup always excludes a work item referenced by `verification_requests`, and no live verification chain is ever collected. A terminal retry chain is retained for at least 90 days after the latest terminal generation. After that boundary, a chain with no overlay is eligible even when its source artifact remains active; immutable artifact reachability is not itself an audit-chain root. An overlay-bearing chain remains retained only while a desired-head membership, projection-incarnation membership, Wiki/direct audit FK, or another explicit verification-chain FK references it.

`VerificationRetentionService` and `hades:verification:cleanup` select the whole connected component through both retry links and prove **external chain reachability** while logically excluding references internal to that candidate. For a nonempty source-import set they recheck under `GraphArtifactReferenceLock`, scope lock, head, then chain rows; a source-unavailable Wiki-only component starts an ordinary transaction at that scope lock and never constructs an empty guard. Before any delete, the locked transaction computes the complete candidate work/request/overlay ID sets and queries desired/projection memberships, Wiki references, direct audit/domain FKs, and every other explicit chain root with candidate-internal retry/request/overlay links excluded by ID. Any query failure or external root aborts/skips with no mutation. Only a root-free candidate is deleted in FK-safe order: overlays, requests from highest to lowest generation, then work items from highest to lowest generation. Generic `GraphArtifactReachability` is never used as a veto in this chain decision. After commit, artifact cleanup independently reacquires its own lock and may re-evaluate newly eligible bytes. RESTRICT/NO ACTION FKs remain a final safety net, not the reachability algorithm. Cleanup never mutates a retained overlay, result, digest, membership, or retry link, so a retained projection's `verification_set_hash` cannot change underneath it.

#### `backend/database/migrations/2026_07_16_000650_create_graph_maintenance_windows_table.php`

Create `graph_maintenance_windows` with `id` ULID, nullable indexed `project_id` (null means global), `reason=backup|cutover|retirement|operator`, `token_hash` char(64), `active` boolean, `started_at`, `expires_at`, nullable `ended_at`, safe JSONB `metadata`, and timestamps. `GraphMaintenanceService` serializes begin/end under a scope advisory lock; it allows at most one active global window and one active window per project. Begin returns a one-time random token, stores only its hash, and requires a bounded expiry. End requires the token and health preconditions; expired windows remain visible to operators and fail safe until explicitly cleared after health verification.

#### `backend/database/migrations/2026_07_16_000675_add_bound_maintenance_authority_and_graph_v1_exports.php`

Extend `graph_maintenance_windows` with monotonic `scope_generation`, nullable `owner_kind=v1_export|v1_retirement|v1_restore`, nullable `owner_operation_id`, `authority_generation`, nullable `authority_token_hash`, nullable `authority_heartbeat_at`, nullable `external_mutation_started_at`, nullable `external_mutation_kind`, and nullable `external_mutation_progress` closed JSON; owner fields are all-null or all-non-null. Create `graph_v1_exports` with project/window/scope-generation/authority-generation ownership, immutable output/selection/pre-v2 deployment descriptors, per-stage digests/counts, error audit fields, and exact forward state `prepared|postgres_captured|neo4j_captured|artifacts_captured|sealed|verified`. Every nonterminal export is uniquely bound to one active `reason=retirement` project window and operation ID; export never sets an external-mutation marker.

`GraphMaintenanceService` exposes only this typed internal operation surface; no controller, generic graph mutation, or unrelated reason can manufacture an authority:

```php
beginBoundProjectOperation($projectId, $reason, $kind, $operationId, $ttl, $initialize): MaintenanceAuthority
resumeBoundProjectOperation($projectId, $reason, $kind, $operationId, $expectedScopeGeneration, $validateOwner): MaintenanceAuthority
transferBoundProjectOperation(MaintenanceAuthority $authority, $nextKind, $nextOperationId, $transition): MaintenanceAuthority
withinAuthorizedRead(MaintenanceAuthority $authority, callable $callback): mixed
withinAuthorizedMutation(MaintenanceAuthority $authority, callable $callback): mixed
completeBoundProjectOperation(MaintenanceAuthority $authority, bool $requireNeo4jHealthy, callable $finalize): void
```

`MaintenanceAuthority` contains window ID, project ID, `reason=retirement`, closed owner kind/operation ID, scope generation, authority generation, expiry, and a process-only random 256-bit raw token; PostgreSQL stores only its lower-case SHA-256. It authorizes only the exact operation's project-scoped graph reads/mutations and never schema mutation, general HTTP bypass, another project, or a different reason. Resume is legal only after owner TTL expiry, validates the exact nonterminal operation plus immutable selection/manifest digests, increments authority generation, rotates the raw token/hash, renews TTL, and fences every earlier owner. Every stage/heartbeat/final CAS matches window ID, scope generation, authority generation, token hash, owner kind, and operation ID. Before the first external mutation and before every bounded batch, a fenced transaction sets/advances the closed mutation kind, batch ordinal, selection digest, and pre-state digest; completion records the post-state digest. An ordinary maintenance-off call refuses a bound window; only `completeBoundProjectOperation` may close it after the operation's final verified state and health callback.

The audited manual-abandon command may close only a `v1_export` owner, because export is read-only, and only after it re-proves every PostgreSQL/Neo4j/artifact pre-state count/digest and `external_mutation_started_at IS NULL` under the normal locks. A `v1_retirement|v1_restore` owner never has an abandon-open path, even before its first batch: it remains fenced and may only resume, transfer to a verified scoped restore, or enter separately approved whole-system DR. A marker with incomplete batch progress is positive evidence that external state may be partial, not a reason to reopen.

The total session-lock order is normative. Begin/resume/transfer/complete take shared global session advisory lock, exclusive project session advisory lock, then a short transaction locking window, operation row, and only then pointer/domain rows; no external I/O occurs inside that transaction. Authorized work takes shared global, shared project, exclusive operation advisory lock, verifies authority in a short transaction, then enters the existing reference lock and performs external I/O while the session locks remain held, followed by a short fenced stage CAS. Nested ordinary graph leases reuse the same authority; scope widening, cross-project nesting, or switching owner kind without `transferBoundProjectOperation` is rejected.

#### `backend/database/migrations/2026_07_16_000700_create_graph_v1_retirements_table.php`

Create `graph_v1_retirements` with:

```text
id                              ULID primary key
project_id                      project FK, indexed
selection_sha256                char(64)
selection                       jsonb
receipt_sha256                  char(64)
scoped_backup_manifest_path     text
scoped_backup_manifest_sha256   char(64)
pre_v2_deployment               jsonb
state                           string
last_error                      nullable text
failed_at                       nullable timestamp with timezone
completed_at                    nullable timestamp with timezone
restored_at                     nullable timestamp with timezone
created_at / updated_at
```

Allowed state values are `prepared|neo4j_deleted|postgres_deleted|completed|restored`. Add unique `(project_id, selection_sha256)`. `pre_v2_deployment` is the closed object `{backend_image_digest,frontend_image_digest,backend_git_commit,frontend_git_commit,agent_git_commit,agent_artifact_path,agent_artifact_sha256}`. The table is the resumable audit/state machine for section 14.3; command stdout is never its source of truth.

The retirement row also stores the bound maintenance window ID, scope generation, authority generation, current owner operation ID, sealed export ID, external-mutation start time/kind, last started/completed batch ordinal, and pre/post-state digests. Its forward stages match those authority fields on every CAS. A nonterminal row can be resumed only through the `v1_retirement` authority owner and can transfer to `v1_restore` only through the explicit restore transition; it can never be abandoned open.

#### `backend/database/migrations/2026_07_16_000725_create_graph_v1_restores_table.php`

Create `graph_v1_restores` with project, retirement/export/window ownership, scope and authority generations, immutable manifest/selection/deployment digests, stage digests/counts, external-mutation start/kind and batch-progress/pre/post-state digests, error audit fields, and exact forward state `prepared|artifacts_restored|postgres_restored|neo4j_restored|pointer_restored|smoke_verified|completed`. Add unique `(project_id,retirement_id,scoped_backup_manifest_sha256)`. Every mutation marker and stage CAS matches the active `v1_restore` authority. A restore may start by transferring a nonterminal retirement authority or by beginning a new bound restore window after a completed retirement; it never overwrites existing different bytes/rows/namespaces and never has an abandon-open path. The retirement becomes `restored` and the window closes only after `smoke_verified→completed` verifies exact counts/digests, active pointer, Neo4j health, authenticated v1 smoke, and unrelated-data invariants.

### 9.2 Projection versioning

The backend calculates:

```text
artifact_graph_version = canonical semantic digest declared by the validated manifest
verification_set_hash  = SHA-256(canonical ordered active verification overlays)
projection_version      = SHA-256(artifact_graph_version + ":" + verification_set_hash)
```

For an empty overlay set, `verification_set_hash` is SHA-256 of the RFC 8785 canonical JSON bytes for `[]`. Otherwise sort overlays by `assertion_fingerprint` then overlay ID and hash JCS of an array whose members have exactly `artifact_graph_version`, `assertion_fingerprint`, `verdict`, `overlay`, and `evidence`. The projection preimage is the exact byte concatenation defined in section 6.4.

When a new artifact or overlay set is desired, the writer first enters `GraphArtifactReferenceLock` for the complete base/overlay-source import set, then takes the verification scope lock where applicable and the exact projection-head lock. It stores `desired_graph_import_id`, all three desired-version fields, and the exact desired overlay memberships in one transaction. It increments `desired_generation` and clears the three failure fields only when that complete tuple/set differs. Validation success is the sole special case: while its exact validating import row is already locked and cleanup cannot select it, it records the empty-overlay desired tuple in the same transaction that makes the import validated. Dispatch after commit is a latency optimization; the minute reconciler recovers a committed intent after a crash.

Every Neo4j v2 node, relationship, lifecycle flow, and lifecycle step has `project_id`, `source_scope_type`, `source_scope_id`, `projection_version`, and immutable `projection_incarnation_id=canonical_graph_projections.id`. Every namespace key, relationship identity, query, validation, and delete filters all five. Logical projection versions may recur after A→B→A or a retry; physical incarnations never do. The browser can submit neither an arbitrary projection version nor an incarnation ID.

PostgreSQL and Neo4j cannot share a transaction. Publication is therefore exactly:

1. Persist and validate all staged chunks.
2. In short PostgreSQL transaction A, create and claim a fresh `projecting` projection row whose ULID is the immutable incarnation, without changing the active pointer; commit before external work.
3. Under a fenced renewable execution lease, prepare the isolated Neo4j namespace keyed by logical version plus incarnation and create/validate its permanent projection fence, then project that fresh candidate from zero in fence-locked batches while no PostgreSQL transaction/advisory/row lock is held.
4. Validate base counts/digests/endpoints/lifecycle membership against the manifest, then validate effective counts against `base counts + canonical overlay delta`; verify search index availability.
5. In short PostgreSQL transaction B, reacquire the complete reference lock, then head/attempt/candidate locks; CAS the desired import/generation/version, execution generation/token, cleanup state, and exact overlay memberships; mark the candidate `ready`, move the old active projection to `previous_projection_id`, and update `active_projection_id`. If any CAS fails, mark only that candidate stale and do not move the head.
6. Readers resolve the active pointer first, then query only that exact Neo4j namespace.
7. On any failure, mark the candidate failed and leave the old active pointer and namespace untouched.

Retain the active and immediately previous ready v2 incarnations. A stale incarnation may be tombstoned only after the graph-context TTL plus ten minutes has elapsed, no head/candidate/read context retains it, and every terminal projection attempt that refers to it has passed its 24-hour audit retention and been removed.

Before each domain attempt, transaction A rechecks the desired head and live attempts under the reference-lock order. If the exact desired projection incarnation is already active, it short-circuits without writing. If the exact logical version is the retained previous incarnation and that row/namespace is still `ready` with matching base import, overlay memberships, counts, and digests, transaction B may swap the exact active/previous IDs without a Neo4j rewrite; any mismatch returns `graph_previous_projection_invalid` and never deletes/rebuilds the retained version. Otherwise every retry creates a new candidate row/incarnation and leaves older partial incarnations isolated for the cleanup state machine. No retry clears or reuses another incarnation, and no cleanup query may omit project, source scope, logical version, and incarnation.

Create `backend/app/Jobs/ProjectCanonicalGraphV2.php` as a one-delivery job: `$tries=1`, `$timeout=1740`, `$failOnTimeout=true`, `ShouldBeUniqueUntilProcessing`, `$uniqueFor=300`, and exact unique ID `graph-projection:{project_id}:{workspace_binding_id}:{desired_generation}`. `ValidateGraphV2Import` has the same broker/runtime settings and an encrypted payload, with exact unique ID `graph-import:{import_id}:{attempt_generation}:validation:{validation_attempts}` that never contains the raw token. Both dispatch explicitly to the dedicated `graph-v2` database connection/queue. Development and production Compose run a dedicated `graph-v2-worker`; the frozen runtime inequality is queue `retry_after=1900s` > worker `--timeout=1800s` > job timeout `1740s`.

Projection has exactly four **domain attempts**, each represented by its own attempt/candidate row and fresh incarnation, with delays of 10/30/90 seconds before attempts 2/3/4. They are never broker retries. Acquisition creates a renewable 120-second execution-owner lease with a random 256-bit raw token (only its lower-case SHA-256 is stored) and resets `projection_execution_delivery_claimed_at=null`. At job entry the delivery must win the matching null-to-timestamp claim CAS before reading PostgreSQL/Neo4j or heartbeating; a duplicate carrying the same token/generation exits as a successful no-op. The winner heartbeats at most every 30 seconds in short fenced transactions. Only reconciliation after an expired lease may acquire a new execution generation; maintenance deferral consumes neither a broker delivery nor a domain attempt. An expired owner loses every later heartbeat/final CAS, including after its external Neo4j call returns.

Only failure of domain attempt 4 retains the old active projection and transactionally sets the three head failure fields to that exact desired generation/version. A stale desired-generation CAS is a successful no-op. `hades:graph-v2:reconcile` runs every minute and dispatches only when desired differs from active, no unexpired execution lease exists, and `failed_generation != desired_generation`; it never retries a blocked generation forever. `--retry-failed` is an explicit mutation that enters the reference lock before the head, increments desired generation while keeping the exact desired import/version/memberships, clears failure fields, and begins one new four-attempt cycle. Cleanup runs hourly through separate incarnation and artifact state machines. Acceptance tests assert exactly four domain executions, one broker try each, and null head failure through attempts 1–3.

### 9.3 Graph context

Multi-request dashboard and Hades-device exploration uses an opaque `graph_context`. Add `DashboardGraphContext`, `GraphCallerPrincipal`, `GraphProjectionIdentity`, `VerifiedGraphContext`, and `GraphCursorPayload` services/DTOs.

Decode Laravel `APP_KEY` exactly once: strict-base64 decode a `base64:` suffix, otherwise use its raw UTF-8 bytes, and reject a result shorter than 32 bytes. The context payload is exactly `{v:2,principal_type,principal_id,credential_binding,project_id,scope_type:"workspace_binding",scope_id,projection_id,verification_set_hash,projection_version,issued_at,expires_at}`. `projection_id` is the physical incarnation ID. TTL is 30 minutes.

The server derives principals only from authenticated state. Dashboard credential binding is lower-case HMAC-SHA256 of the root key over domain `hades.graph-principal.dashboard.v2\0` plus JCS `{user_id,session_id}`. Hades credential binding uses domain `hades.graph-principal.device.v2\0` plus JCS `{api_token_id,hades_agent_id,device_id}` and requires a device-bound token. Raw session IDs, raw tokens, client-provided subject IDs, and client hashes never enter a request body. Sign contexts with HMAC-SHA256 of the root key over `hades.graph-context.v2\0 || JCS(payload)`.

Handles and cursors are bound to the exact raw context token: derive a 32-byte signing key using HKDF-SHA256 with root key, salt `SHA256(raw graph-context token)`, and info `hades.graph-context-bound-token.v2`. This prevents replay across another dashboard session, user, API token, Hades agent/device, project, binding, projection incarnation, or verification set. No controller or repository duplicates token decoding/signature logic.

Every detail, expansion, impact, or advanced-path request requires the context returned by the initial `overview`, `entrypoints`, `search`, or `lifecycle` response. The service verifies session authorization and all embedded scope fields. An expired, no-longer-current, or no-longer-retained context returns:

```json
{
  "protocol_version": "hades.dashboard_graph.v2",
  "error": {
    "code": "graph_context_stale",
    "message": "The graph changed. Reload this exploration.",
    "retryable": true,
    "details": {}
  }
}
```

with HTTP 409. The frontend discards all graph state and reloads; it never merges old and new responses.

### 9.4 Neo4j lifecycle projection

Keep canonical code nodes and executable relationships as the structural graph. Every projected record and relationship includes `project_id`, `source_scope_type`, `source_scope_id`, `projection_version`, and `projection_incarnation_id`. Additionally project:

```text
(:CanonicalProjectionFence {
  fence_key, project_id, source_scope_type, source_scope_id,
  projection_version, projection_incarnation_id,
  execution_generation, execution_token_hash,
  state, cleanup_operation_id, lock_nonce
})

(:CanonicalLifecycleFlow {
  namespace_key, public_id, entrypoint_public_id, kind, projection_version, projection_incarnation_id,
  completeness_status, stage_counts_json,
  terminal_represented_count, terminal_value, terminal_knowledge, terminal_reason,
  uncertainty_represented_count, uncertainty_value, uncertainty_knowledge, uncertainty_reason
})

(:CanonicalLifecycleFlow)-[:HAS_STEP]->(:CanonicalLifecycleStep {
  namespace_key, public_id, canonical_edge_public_id, stage_from, stage_to,
  min_depth, branch_group_id, async_context, async_child_flow_id, async_cycle, backbone_role,
  order_key, projection_version, projection_incarnation_id
})

(:CanonicalLifecycleStep)-[:FROM]->(:CanonicalNode)
(:CanonicalLifecycleStep)-[:TO]->(:CanonicalNode)
(:CanonicalLifecycleFlow)-[:SPAWNS_ASYNC]->(:CanonicalLifecycleFlow)
```

The two count families round-trip the exact `CountKnowledge` contract: `represented`, `value`, `knowledge`, and `reason`. Neo4j property absence represents JSON null only for `value`/`reason`; mapper and query code must reconstruct explicit nulls and validate the knowledge-dependent rules before returning the DTO. Golden mapper/query tests cover exact nonzero, verified zero, and unknown with its lexically selected reason, and reject the obsolete `*_exact_value`/`lower_bound` aliases.

Each physical incarnation owns exactly one permanent `CanonicalProjectionFence`. Its unique `fence_key` is SHA-256 of JCS `{schema:"hades.neo4j_projection_fence.v2",project_id,source_scope_type,source_scope_id,projection_version,projection_incarnation_id}`. It stores the exact scope/incarnation, current execution generation, only the lower-case SHA-256 execution-token hash, `state=building|deleting|retired`, nullable cleanup operation ID, and a random per-transaction `lock_nonce`; state is forward-only. Add a named uniqueness constraint on `fence_key` and a scoped range index ending `(projection_incarnation_id,state)`, and verify their complete definitions during the Task 5→6 schema upgrade.

Projector setup `MERGE`s the fence. `ON CREATE` may initialize `building` only for the current fenced owner; `ON MATCH` accepts only the identical building generation/token and can never revive `deleting|retired`. Every Neo4j mutation transaction handles at most 1,000 records and has a server-side 60-second transaction timeout. Before any canonical write it matches the exact fence, writes a fresh `lock_nonce` to acquire the fence write lock, then rereads and requires `state=building` plus the exact execution generation/token; zero/mismatch raises `LostProjectionNamespaceFence` and writes no canonical record. No projector query may mutate v2 data without this locked-fence prefix.

After PostgreSQL tombstones an incarnation, cleanup `MERGE`s/locks the same fence (creating it directly as `deleting` when an old attempt never created one), requires the exact cleanup operation, transitions `building→deleting`, deletes only exact-incarnation canonical records in bounded guarded transactions, proves zero remain, then transitions `deleting→retired`. The retired fence is never deleted or reused and is excluded from normal graph queries/counts. Consequently a batch paused before fence creation, during a transaction, after lease loss, or even after PostgreSQL ownership deletion cannot recreate a retired namespace. An invariant scan requires every non-fence v2 record to have one matching non-retired fence and every retired fence to have zero matching canonical records.

A `CanonicalLifecycleStep` exists once per `(flow_id, canonical_edge_id, stage_from, stage_to, async_context)`. An explicit CFG loop/back edge has `flow=loop`; a recursive invocation retains `flow=always|conditional|alternative` and has `backbone_role=loop`. Both are one step per finite stage context and neither expands paths. `min_depth` is the shortest edge distance from the flow entrypoint for that state and is used only for deterministic layout. `order_key` is a stable tuple serialized as text: stage ordinal, min depth, source public ID, target public ID, edge public ID.

The current `Neo4jCanonicalGraphProjector.php` performs this projection in batches and always includes both version and incarnation. `namespace_key` is SHA-256 JCS of project, scope type/ID, logical projection version, physical incarnation ID, record type, and public ID. It MUST NOT delete or reuse records from another incarnation while projecting a candidate.

`Neo4jGraphV2Schema` compares the complete definition—not only the name—of every named v2 uniqueness constraint/range index. Its forward upgrade recognizes only the exact Task 5 definitions, replaces only those named v2 objects to incorporate incarnation identity, and leaves every legacy/unrelated Neo4j object untouched. Only `hades:graph-v2:init-schema --maintenance-token=...` under an active global `reason=cutover` window may mutate schema; `--verify` is read-only. A `backup` token can never authorize schema changes.

### 9.5 Backend services and exact file changes

Create:

```text
backend/app/Http/Controllers/Hades/GraphImportController.php
backend/app/Http/Controllers/Hades/GraphQueryController.php
backend/app/Services/Graph/CanonicalGraphDesiredProjectionService.php
backend/app/Services/Graph/V2/GraphV2ManifestValidator.php
backend/app/Services/Graph/V2/GraphV2ChunkValidator.php
backend/app/Services/Graph/V2/GraphV2ImportService.php
backend/app/Services/Graph/V2/GraphV2ArtifactReader.php
backend/app/Services/Graph/V2/GraphV2Normalizer.php
backend/app/Services/Graph/V2/GraphV2ValidationRunService.php
backend/app/Services/Graph/V2/GraphV2LifecycleProjectionMapper.php
backend/app/Services/Graph/DashboardGraphContext.php
backend/app/Services/Graph/GraphCallerPrincipal.php
backend/app/Services/Graph/GraphProjectionIdentity.php
backend/app/Services/Graph/VerifiedGraphContext.php
backend/app/Services/Graph/GraphCursorPayload.php
backend/app/Services/Graph/DashboardGraphSearchSnapshot.php
backend/app/Contracts/Graph/GraphArtifactReachabilityProvider.php
backend/app/Services/Graph/Retention/GraphArtifactReferenceLock.php
backend/app/Services/Graph/Retention/GraphArtifactReferenceGuard.php
backend/app/Services/Graph/Retention/GraphArtifactReachability.php
backend/app/Services/Graph/Retention/ProjectionGraphArtifactReachabilityProvider.php
backend/app/Services/Graph/Retention/ProjectionNamespaceReachability.php
backend/app/Services/Graph/Retention/GraphArtifactCleanupLock.php
backend/app/Services/Graph/Retention/GraphV2NamespaceCleanupService.php
backend/app/Services/Graph/Retention/VerificationGraphArtifactReachabilityProvider.php
backend/app/Services/Graph/Retention/WikiGraphArtifactReachabilityProvider.php
backend/app/Services/Graph/Retention/GraphV2ArtifactCleanupService.php
backend/app/Services/Hades/VerificationQueueService.php
backend/app/Services/Hades/VerificationScope.php
backend/app/Services/Hades/VerificationScopeLock.php
backend/app/Services/Hades/VerificationSummary.php
backend/app/Services/Hades/VerificationCompletion.php
backend/app/Services/Hades/VerificationCompletionService.php
backend/app/Services/Hades/GraphVerificationOverlayService.php
backend/app/Services/Hades/GraphVerificationOverlayDraft.php
backend/app/Services/Hades/HadesAuthenticatedPrincipal.php
backend/app/Services/Hades/VerificationRetentionService.php
backend/app/Services/Hades/AgentWorkItemStateService.php
backend/app/Services/WikiRevisionDraft.php
backend/app/Services/Graph/GraphMaintenanceService.php
backend/app/Services/Graph/MaintenanceToken.php
backend/app/Services/Graph/MaintenanceAuthority.php
backend/app/Services/Graph/ArtifactStorageMaintenanceGuard.php
backend/app/Services/Graph/GraphArtifactInventoryService.php
backend/app/Services/Graph/RestoredBackupMaintenanceRecoveryService.php
backend/app/Services/Graph/V2/GraphV1ExportService.php
backend/app/Services/Graph/V2/GraphV1RetirementService.php
backend/app/Services/Graph/V2/GraphV1RestoreService.php
backend/app/Jobs/ProjectCanonicalGraphV2.php
backend/app/Jobs/AcquireGraphV2ValidationRun.php
backend/app/Jobs/ValidateGraphV2Import.php
backend/app/Jobs/ReconcileVerificationQueue.php
backend/app/Events/CanonicalGraphV2ProjectionActivated.php
backend/app/Events/WikiCurrentRevisionActivated.php
backend/app/Listeners/QueueGraphVerificationRequests.php
backend/app/Listeners/QueueWikiVerificationRequest.php
backend/app/Console/Commands/ReconcileCanonicalGraphV2.php
backend/app/Console/Commands/CleanupCanonicalGraphV2.php
backend/app/Console/Commands/InitializeCanonicalGraphV2Schema.php
backend/app/Console/Commands/ReconcileVerificationQueue.php
backend/app/Console/Commands/CleanupVerificationHistory.php
backend/app/Console/Commands/ExportCanonicalGraphV1.php
backend/app/Console/Commands/RetireCanonicalGraphV1.php
backend/app/Console/Commands/RestoreCanonicalGraphV1.php
backend/app/Console/Commands/RecoverRestoredGraphBackupWindow.php
backend/app/Console/Commands/SetCanonicalGraphMaintenance.php
backend/app/Services/Graph/V2/Neo4jGraphV2Schema.php
backend/app/Models/HadesGraphImport.php
backend/app/Models/HadesGraphImportChunk.php
backend/app/Models/VerificationRequest.php
backend/app/Models/GraphVerificationOverlay.php
backend/app/Models/WikiVerificationClaim.php
backend/app/Models/WikiGraphArtifactReference.php
backend/app/Models/AgentWorkItem.php
backend/app/Models/CanonicalGraphProjectionHead.php
backend/app/Models/GraphV1Export.php
backend/app/Models/GraphV1Retirement.php
backend/app/Models/GraphV1Restore.php
backend/app/Models/GraphMaintenanceWindow.php
backend/resources/contracts/hades/graph-v2/manifest.json
backend/resources/contracts/hades/graph-v2/*.schema.json
backend/resources/contracts/hades/graph-v2/golden/*.json
backend/tests/Feature/Hades/GraphImportControllerTest.php
backend/tests/Feature/Dashboard/GraphExplorerV2ControllerTest.php
backend/tests/Feature/Hades/VerificationCompletionTest.php
backend/tests/Integration/Hades/VerificationRetentionTest.php
backend/tests/Feature/Graph/GraphV1RetirementCommandTest.php
backend/tests/Feature/Graph/GraphV1RestoreCommandTest.php
backend/tests/Integration/Graph/CanonicalGraphV2ProjectionTest.php
backend/tests/Integration/Graph/CanonicalGraphV2ProjectionRetryTest.php
```

Modify:

```text
backend/routes/api.php
backend/routes/web.php
backend/routes/console.php
backend/app/Providers/AppServiceProvider.php
backend/app/Services/Graph/CanonicalGraphNormalizer.php
backend/app/Services/Graph/CanonicalGraphProjectionService.php
backend/app/Services/Graph/CanonicalGraphQueryService.php
backend/app/Services/Graph/CanonicalGraphRepository.php
backend/app/Services/Graph/DashboardGraphExplorerService.php
backend/app/Services/Graph/Neo4jCanonicalGraphProjector.php
backend/app/Http/Controllers/Dashboard/Api/DashboardGraphExplorerController.php
backend/app/Http/Controllers/Plugin/GraphQueryController.php
backend/app/Http/Controllers/Plugin/AgentWorkItemController.php
backend/app/Http/Controllers/Hades/WikiPageController.php
backend/app/Http/Controllers/Dashboard/Api/DashboardAgentChatController.php
backend/app/Http/Controllers/Dashboard/Api/DashboardAgentWorkController.php
backend/app/Services/WikiRevisionService.php
backend/app/Services/Hades/WikiVerificationService.php
backend/app/Services/Hades/WikiVerificationEvidencePolicy.php
backend/app/Services/Hades/HadesKanbanTaskIntakeService.php
backend/app/Services/ServerAgentWorkService.php
backend/app/Services/ArtifactStorageService.php
backend/config/devboard.php
```

Exact responsibilities:

- `GraphImportController` is a thin authorization/validation layer for the four import endpoints.
- `GraphV2ManifestValidator` rejects all non-v2 schemas and validates manifest counts, source identity, allowed enums, size, and project/binding.
- `GraphV2ChunkValidator` streams decompression, verifies byte count/digest/schema/kind/order, and rejects duplicate IDs inside a chunk.
- `GraphV2ArtifactReader` streams logical records in manifest order without building a full object in memory. Point lookup uses the durable public-ID uncertainty index, validates ordinal/chunk locators, and never performs repeated unindexed ordinal-range scans.
- `GraphV2Normalizer` validates cross-chunk uniqueness, referential integrity, evidence, coverage, flow membership, privacy, and deterministic ordering. It has no v1 branch.
- `complete` commits `staging→validating`; its after-commit callback invokes `GraphV2ValidationRunService` from section 8.3. That service—not `ValidateGraphV2Import`—locks the row, increments `validation_attempts`, issues/stores the token hash and lease, resets the delivery claim to null, and dispatches the encrypted single-try job with key `graph-import:{id}:{attempt_generation}:validation:{validation_attempts}`. `AcquireGraphV2ValidationRun` is only the delayed/reconciliation wrapper around that same service. At entry `ValidateGraphV2Import` must win the one-shot null-to-timestamp delivery-claim CAS; only that winner streams the two passes, heartbeats with its supplied token at the exact cadence in section 8.3, and CAS-writes `validated` plus `validated_at` or the applicable deterministic/transient outcome. It never acquires its own lease or increments attempts, and a duplicate same-token delivery is a successful no-op. Only a successful terminal CAS and its after-commit path request projection.
- `GraphV2LifecycleProjectionMapper` validates every producer-authored flow/flow-step against its base edge, structure, stage, count, and completeness records, then maps it one-to-one into Neo4j properties. It does not traverse the graph, infer membership, assign stages, or recompute backbone roles.
- `Neo4jGraphV2Schema` idempotently creates single-property uniqueness constraints on the server-generated incarnation-aware `namespace_key` for `CanonicalNode`, `CanonicalLifecycleFlow`, and `CanonicalLifecycleStep`, plus range indexes beginning with `(project_id,source_scope_type,source_scope_id,projection_version,projection_incarnation_id)` for all three labels. It verifies complete named definitions and supports only the explicit Task 5→Task 6 forward upgrade described in section 9.4; application validation remains authoritative for relationships.
- `CanonicalGraphProjectionService` retains its existing candidate/lease/active-pointer orchestration and calls v2 services for v2 imports. Every reference write starts in `GraphArtifactReferenceLock`. A successful head CAS captures its exact values and emits one `CanonicalGraphV2ProjectionActivated` only after commit; failed, stale/no-op, already-active, and rolled-back paths emit none.
- `CanonicalGraphRepository` resolves only the exact active v2 projection for v2 API calls; no repository/workspace fallback.
- `DashboardGraphExplorerService` implements the protocol in section 10.
- `DashboardGraphSearchSnapshot` owns the bounded session/project/projection-bound vector candidate cache; cursors never access it without validating the enclosing graph context.
- Dashboard, plugin, and Hades `GraphQueryController` adapters all call the same principal-aware v2 service. Vector search may rank graph handles, but topology comes only from the exact current projection. No controller accepts client-supplied principal/scope fields.
- `GraphMaintenanceService` gates only graph import/query/UI routes and graph mutations. A project read/mutation lease acquires shared global then shared project PostgreSQL session advisory locks on dedicated connections and holds them through the complete callback, including Neo4j/object-store I/O. Global begin/end acquires exclusive global; project begin/end acquires shared global then exclusive project. Begin writes the active row while locks remain held; later readers/writers acquire and reject. Same-scope nesting is reentrant; scope widening or cross-project nesting fails. `hades:graph-v2:maintenance --global|--project=... --on --reason=... --ttl=SECONDS` returns a one-time token; `--off --token=... --require-neo4j-healthy` closes an unbound window. Export/retirement/restore instead use only the section 9.1 typed, generation-fenced `MaintenanceAuthority`; raw authority tokens never enter receipts, manifests, logs, jobs, or the database. Non-graph project/backend features remain available.
- Every graph read holds `withinReadLease()` across context resolution and the complete Neo4j read. Import upload/finalization, validation acquisition/execution/CAS, desired-head writes, projection/reconcile/operator retry, graph-reference writers, schema mutation, namespace cleanup, artifact cleanup, and every `ArtifactStorageService` deletion path hold `withinMutationLease()` before external access. Maintenance deferral consumes no validation/projection attempt. Only `withinCutoverSchemaLease()` may bypass availability, and only for an unexpired raw token matching an active global `reason=cutover` row.
- `ArtifactStorageMaintenanceGuard` is called inside `ArtifactStorageService` before every public delete, replace, move-overwrite, and cleanup mutation, not merely by selected callers. Global backup rejects every destructive storage mutation; a bound project retirement/restore authority permits only manifest-selected graph keys for its exact project and stage. Immutable creation is allowed only when the destination is absent or byte-identical. A static architecture test rejects raw destructive filesystem/object-store calls outside `ArtifactStorageService` and the scoped restore adapter. `GraphArtifactInventoryService` streams deterministic records `{relative_key,bytes,etag|null,sha256}`, rejects traversal/duplicate keys, verifies every PostgreSQL-reachable graph key, permits unrelated extras, and fails on a missing or mismatched reachable object. Backup, scoped export, retirement, and restore all call this one inventory path.
- `GraphArtifactReferenceLock`, its invocation guard, the fail-closed reachability aggregator, and the three fixed concrete providers implement the retention contract above. `AppServiceProvider::register()` contains one tag call listing each currently implemented provider exactly once plus one `giveTagged()` binding; this is an internal closed set, not plugin discovery.
- `AgentWorkItemStateService` is the single locked state/version mutation path used by existing dashboard/plugin/server writers and new verification code; every create begins at one and every committed claim/lease/result/status/terminal change increments once.
- `VerificationScopeLock` requires an open transaction and acquires `pg_advisory_xact_lock(hashtextextended(key,0))` for canonical key `hades:verification:v2:{project_id}:{workspace_binding_id}`. `VerificationQueueService` creates/deduplicates/stales work and returns bounded `VerificationSummary` counts. It reads candidate import IDs, enters the outer reference lock, takes the scope lock, then locks/revalidates domain rows.
- `WikiRevisionService` performs the current-revision CAS and emits `WikiCurrentRevisionActivated` after commit. `QueueGraphVerificationRequests` and `QueueWikiVerificationRequest` are registered through `Event::listen()` in `AppServiceProvider::boot()`. Events are wake-ups rather than trusted snapshots: listeners reread the actual projection head and current Wiki revision. A graph activation dispatches Wiki freshness as well as graph reconciliation; a Wiki event delayed across a graph change evaluates the new source. Current `needs_verification` queues, matching-source certification is quiet, stale certification queues only when active=desired, active!=desired queues nothing, and both-null is unavailable. Matching duplicates coalesce, and the periodic sweep runs the same rules for lost-event liveness.
- `ReconcileVerificationQueue` takes optional project/binding/domain filters and one unique queue key per scope/domain. `routes/console.php` schedules graph projection reconciliation every minute, verification reconciliation every five minutes, graph cleanup hourly, and verification-history cleanup hourly with `withoutOverlapping()` and one-server locking.
- `VerificationCompletionService` validates/canonicalizes result bytes and builds all potentially slow graph/Wiki drafts before acquiring any graph-import, scope, head, or domain lock. It then uses one outer reference lock and the fixed scope/head/work/request/domain row order to revalidate source currency, apply the already-built draft, and complete the item in one bounded PostgreSQL transaction. `HadesAuthenticatedPrincipal` is constructible only from authenticated middleware attributes; even identical-result replay repeats agent/device/project/binding/capability authorization and the same lock path. `VerificationCompletion` is the closed response DTO.
- `GraphVerificationOverlayService::prepareDraft()` performs artifact reads, locator/evidence checks, and deterministic derivation outside locks; `commitDraftWithinCurrentTransaction()` performs bounded DB-only revalidation/inserts with the supplied guard. It never mutates an artifact or reacquires the outer lock. A committed overlay writes exact desired/projection membership provenance and requests a coalesced projection only after completion commits.
- `WikiRevisionService` and `WikiVerificationService` have the same prepare/commit split through immutable `WikiRevisionDraft`. Markdown/evidence/digest/ledger construction and source reads happen before locks; the commit path is DB-only, consumes the guard when source is available, preserves full content/current-revision CAS, and writes immutable normalized claims/evidence for `verified|contradicted`. `deferred` creates no Wiki revision. The legacy direct Hades Wiki verify mutation route/client/CLI action is removed; queue completion is the only automated verification write.
- `VerificationRetentionService` applies the 90-day whole-retry-chain algorithm above; `CleanupVerificationHistory` is its bounded operator/scheduler adapter.
- Export/retire/restore commands are thin argument/confirmation adapters. `GraphV1ExportService`, `GraphV1RetirementService`, and `GraphV1RestoreService` implement sections 14.2–14.4 only through typed bound maintenance authority, existing graph models, guarded `ArtifactStorageService`, the persistent export/retirement/restore state machines, and a project/scope-filtered Neo4j repository. They do not issue unscoped deletes/restores, close ordinary maintenance directly, or trust a recovered raw token.

## 10. Dashboard Graph API v2

Keep the existing authenticated endpoint:

```text
POST /projects/{project}/graph/query
```

Expose the identical closed query/result protocol through the existing service-gated machine surfaces:

```text
POST /api/plugin/v1/graph/query
POST /api/hades/v1/graph/query
```

The Hades route requires the existing project-scoped `project_inspection` capability; graph verification work separately requires `verify_project_graph`. The authenticated token/agent/device/workspace-binding records determine the caller principal, project, and scope. Those values are forbidden in the body because the query schema has `additionalProperties:false`. Remove `GET /api/hades/v1/graph/traverse`, its controller, capability/health/OpenAPI advertisement, Agent caller, memory-plugin fallback, and every v1 response adapter. A missing v2 route is an explicit compatibility/deployment error, never permission to synthesize topology from memory.

Every request uses:

```json
{
  "protocol_version": "hades.dashboard_graph.v2",
  "query": {
    "type": "entrypoints"
  }
}
```

Every success response uses:

```json
{
  "protocol_version": "hades.dashboard_graph.v2",
  "graph_context": "opaque-signed-token-or-null",
  "projection": {
    "state": "ready",
    "completeness": "partial",
    "generated_at": "ISO-8601",
    "source_label": "Project workspace"
  },
  "data": {}
}
```

For `scopes`, `graph_context` and `projection` are both null. Every other successful query requires a ready projection and returns both non-null; absence of a ready projection is an error envelope, never a success with empty data.

The supported query types are:

```text
scopes, overview, entrypoints, lifecycle, lifecycle_expand,
search, detail, neighborhood, impact, path
```

`path` remains supported only as an advanced technical operation and is absent from the primary lifecycle UI.

### 10.1 Handles, cursors, and closed DTOs

Entity handles and cursors are opaque base64url JCS payloads signed by the exact-context HKDF key from section 9.3; raw public IDs are returned only inside `technical_summary` when the caller explicitly sets `include_technical=true`. A handle contains exactly `{v:2,type,public_id,project_id,scope_type:"workspace_binding",scope_id,projection_id,projection_version,expires_at}`. A cursor contains exactly `{v:2,query_type,project_id,scope_type:"workspace_binding",scope_id,projection_id,projection_version,filters_sha256,last_sort_tuple,search_snapshot_id,expires_at}`. Principal fields are not duplicated because the derived key already binds the exact principal/context. Both expire no later than their graph context. Invalid signature/type/filter/tuple shape returns `422 graph_handle_invalid` or `graph_cursor_invalid`; a valid token for an inactive incarnation returns `409 graph_context_stale`.

Pagination order is fixed and never score-unstable:

- entrypoints: entrypoint-kind ordinal, normalized public label, public ID;
- exact/lexical search: match-basis ordinal, normalized label, public ID;
- vector candidates: vector rank captured once by the initial response, normalized label, public ID; later pages use that captured ranked candidate set and context, not a new vector query;
- lifecycle expansion: `order_key`, edge public ID, target public ID;
- neighborhood/impact: shortest distance, normalized label, public ID.

All response DTOs are closed by `dashboard-response.schema.json`. The primary DTOs have exactly these fields:

```text
ScopeDto:
  scope, label, workspace_name, ready,
  active_projection_generated_at, completeness, entrypoint_discovery

ProjectionDto:
  state, completeness, generated_at, source_label

StageCountDto:
  stage, count

CapabilityCompletenessDto:
  status, plain_summary, reason_count

CompletenessDto:
  status, capabilities

CoverageDto:
  files, entrypoints, records, omitted_reason_counts

OverviewCountsDto:
  nodes, relationships, entrypoints, flows, uncertainties

EntrypointDto:
  handle, entrypoint_kind, label, method_semantics, methods,
  public_path, public_name, effective_handler_handle, handler_label,
  framework, evidence_badge, verification_state, flow_completeness

LifecycleNodeDto:
  handle, kind, label, qualified_name, role_label, stage, layout,
  source_location, evidence_badge, verification_state,
  uncertainty_count, expandable

LifecycleEdgeDto:
  handle, source_handle, target_handle, relation, flow,
  label, condition_label, branch_group_handle,
  backbone_role, evidence_badge

CollapsedGroupDto:
  handle, group_kind, label, source_handle, target_handle,
  arm_count, represented_node_count, total_node_count,
  expanded, next_cursor

AsyncFlowSummaryDto:
  flow_handle, trigger_label, root_label, stage_counts,
  completeness, cycle, expandable

TerminalOutcomeDto:
  node_handle, kind, label, status_code, exception_type,
  count, evidence_badge

SearchResultDto:
  handle, label, kind, source_location, match_basis,
  match_explanation, selectable, unresolved_reason

RelationshipDto:
  edge_handle, node_handle, direction, relation, label, flow,
  count, evidence_badge, verification_state

EffectDto:
  node_handle, kind, operation, label, count, evidence_badge

IntegrationDto:
  node_handle, protocol, operation, label, count, evidence_badge

EntrypointMembershipDto:
  entrypoint_handle, label, entrypoint_kind, stage, flow_completeness

UncertaintyDto:
  handle, reason_label, question, impact, priority,
  verification_state, evidence_requirements

DetailCountsDto:
  incoming, outgoing, callers, callees, effects, integrations,
  entrypoint_memberships, uncertainties
```

Nullable fields are explicit in the schema. `ScopeDto.scope` is exactly `{type:"workspace_binding",id:<authorized ULID>}`; readiness false requires null projection time/completeness. `ProjectionDto.state` is always `ready`. `CompletenessDto.capabilities` has exactly the nine capability keys from section 6.9, each mapped to `CapabilityCompletenessDto`; `CoverageDto.files|entrypoints|records` are closed unsigned-count maps matching section 6.9 and `omitted_reason_counts` is a key-sorted array of `{code,count}`. `OverviewCountsDto`, `DetailCountsDto`, and every other count use `CountKnowledge`. `StageCountDto.stage` uses the ordered lifecycle enum. `RelationshipDto.direction` is `incoming|outgoing`; effect kind and integration protocol use the closed node/property enums. `evidence_badge` is `verified_from_code|agent_verified|observed_runtime|inferred|unresolved`; `verification_state` is `verified|contradicted|needs_verification|not_applicable`. `EntrypointDto.effective_handler_handle`/`handler_label` come from the effective entrypoint view in section 11.5 and are both null when no effective handler exists. `source_location` is null or exactly `{path,start_line,end_line}`. Labels are plain text, never Markdown/HTML. `technical_summary` is null by default and, when requested, contains only projection/version/count/coverage identifiers defined in the response schema.

For a current graph entity, `SearchResultDto.handle` is non-null, `selectable=true`, and `unresolved_reason=null`. For a vector-only candidate, handle is null, selectable is false, and unresolved reason is `not_in_current_projection`; the UI may show it as a locator hint but cannot issue topology queries for it. `match_explanation` is a bounded plain-language string selected from server templates for its `match_basis`, not model-generated HTML.

`layout` is server-authored, non-topological UI metadata with exactly `{stage_ordinal,min_depth,order_key,branch_lane,lane_ordinal}`; ordinals/depths are non-negative integers and `branch_lane` is `backbone|alternative|exception|loop|async`. `group_kind` is `branch|mandatory_chain|stage_overflow|loop|exception|verification_materialization_gap`. A collapsed group is a truthful display summary, not a canonical graph node, and its count still follows `CountKnowledge`. `target_handle` is nullable only for an open-ended `verification_materialization_gap`; that group is never expandable and its label is the fixed copy `Target verified; downstream lifecycle is not materialized by this artifact`.

The allowed request fields are closed per query type:

| Query | Required fields after `type` | Optional fields |
|---|---|---|
| scopes | none | none |
| overview | `scope` | `graph_context` |
| entrypoints | `scope` | `graph_context`, `search`, `entrypoint_kinds`, `cursor`, `limit` |
| lifecycle | `scope`, `entrypoint_handle` | `include_async`, `graph_context`, `include_technical` |
| lifecycle_expand | `scope`, `graph_context`, `entrypoint_handle`, exactly one selector | `cursor`, `limit` |
| search | `scope`, `query` | `graph_context`, `kinds`, `cursor`, `limit` |
| detail | `scope`, `graph_context`, `node_handle` | `entrypoint_handle`, `include_technical` |
| neighborhood | `scope`, `graph_context`, `node_handle` | `depth`, `cursor`, `limit` |
| impact | `scope`, `graph_context`, `node_handle` | `cursor`, `limit` |
| path | `scope`, `graph_context`, `from_handle`, `to_handle`, `relation_scope` | `entrypoint_handle` only for lifecycle scope |

Unknown request fields are `422 graph_query_invalid`. Search query is NFC/collapsed whitespace, 1–256 bytes; kinds are unique known enums, maximum 20; every limit defaults to 50 and is 1–100; depth defaults to 1 and is 1–3. The backend response serializer validates against the same schema in non-production tests and contract fixtures.

For `overview|entrypoints|search|lifecycle`, omitted/null `graph_context` is legal only as a bootstrap and mints one current context. When a valid context is supplied, the response returns that exact token byte-for-byte rather than minting a different expiry/token. Every other query requires it. The frontend bootstraps sequentially through overview as section 13.2.1 specifies, so only one context-free request is in flight per project/scope generation.

Response `data` keys are also fixed: `scopes={items:ScopeDto[]}`; `overview={counts:OverviewCountsDto,completeness:CompletenessDto,coverage:CoverageDto,recommended_mode}`; `entrypoints={items,counts_by_kind,discovery_knowledge,next_cursor,recommended_mode}`; `lifecycle` uses the keys in section 10.3; `lifecycle_expand={nodes,edges,collapsed_groups,next_cursor}`; `search={items,next_cursor}`; `detail={node,relationships,effects,integrations,entrypoint_memberships,uncertainties,counts}`; `neighborhood={nodes,edges,count,next_cursor}`; `impact={entrypoints,nodes,count,next_cursor}`; `path={state,nodes,edges,reason}`. `counts_by_kind` is a closed object with all eight entrypoint-kind keys, each a `CountKnowledge`. No query returns a bare array.

### 10.2 `entrypoints`

Request query fields:

```json
{
  "type": "entrypoints",
  "scope": {"type": "workspace_binding", "id": "..."},
  "graph_context": "opaque",
  "search": "optional normalized query",
  "entrypoint_kinds": ["http_route"],
  "cursor": null,
  "limit": 50
}
```

`limit` is 1–100. Response `data` contains `items`, `counts_by_kind`, `discovery_knowledge`, `next_cursor`, and `recommended_mode`. `recommended_mode` is exactly `request_lifecycle`, `execution_flow`, or `analyze_element`. Items contain opaque handle, `entrypoint_kind`, label, method/path/name when public, handler label, framework, evidence badge, and flow completeness.

### 10.3 `lifecycle`

Request query fields:

```json
{
  "type": "lifecycle",
  "scope": {"type": "workspace_binding", "id": "..."},
  "entrypoint_handle": "opaque",
  "include_async": true,
  "graph_context": null
}
```

When `graph_context` is null or omitted, the backend resolves the exact current projection and returns a new context. When non-null, it must validate as current.

Response `data` contains exactly:

```text
entrypoint
mode_label
stage_counts
backbone.nodes
backbone.edges
collapsed_groups
async_flow_summaries
terminal_outcomes
completeness_by_capability
uncertainty_counts
technical_summary
```

The initial backbone response contains at most 120 actual nodes. It includes mandatory `always` segments, but replaces any consecutive same-stage mandatory run needed to respect that limit with a `mandatory_chain` summary; it also emits one collapsed summary per branch group, loop, exception fan-out, or stage overflow. Compression preserves the first and last canonical node around each summary. It never pretends a collapsed group is absent.

### 10.4 `lifecycle_expand`

Request requires `graph_context`, `scope`, and `entrypoint_handle`, plus exactly one selector:

```text
stage
collapsed_group_handle
```

The selector names are `stage` and `collapsed_group_handle`; raw branch/group IDs are not accepted. Node click uses `detail` and never expands topology implicitly. It also accepts `cursor` and `limit` of 1–100. The response contains bounded nodes/edges and `next_cursor`. Cursor payload is signed and includes the same projection version; a mismatched cursor returns HTTP 409.

### 10.5 `search` and Analyze an element

Search accepts `query`, optional `kinds`, scope, cursor, and limit. Exact normalized public identifiers are ranked first; vector similarity may add candidates. Every result includes `match_basis`:

```text
exact_identifier, exact_route, lexical_alias, vector_candidate
```

Search normalization is deterministic. Preserve an NFC trimmed query for case-sensitive exact matching; also derive a Unicode-casefolded lexical form that maps `\\`, `/`, `.`, `:`, `::`, `_`, and `-` to token boundaries and collapses whitespace. Ranking tiers are: exact normalized route path; exact qualified/public name; exact simple symbol/route name; token-prefix alias; token-containing alias; captured vector candidates. Within a tier sort by normalized label then public ID. Route lookup indexes both the full normalized path and its non-parameter path segments, so `/generale/soggetti-attivi/` and `soggetti-attivi` locate the same route candidates. Symbols index qualified and simple names, so `AdminControllerBulkDeleteBehavior` is an exact identifier even when the qualified name contains a namespace. Fuzzy lexical aliases never outrank any exact tier.

Selecting a result obtains `detail`, bounded `neighborhood`, `impact`, and applicable entrypoint memberships. Callers/callees/impact use the zero-versus-unknown contract. A vector candidate with no matching current graph node is displayed as an unresolved candidate, never as topology.

`detail.entrypoint_handle` is optional. When present, it must belong to the same context and all relationships/counts/effects/integrations are restricted to that selected lifecycle flow; `entrypoint_memberships` still lists all known memberships. When absent, detail is structural across the active scope. The lifecycle drawer always supplies its selected entrypoint handle; Analyze an element omits it until the user opens a specific membership. The response never labels structural/global relationships as “within this lifecycle.”

On the first search request, the service captures at most 200 vector candidate public IDs/ranks into the existing Laravel cache for 30 minutes under a cryptographically random `search_snapshot_id`, bound inside the value to session user, project, scope, projection, and filters digest. The signed next cursor contains that ID; every later page loads the same ordered list. Cache miss/binding mismatch returns `409 graph_search_snapshot_expired`, and the frontend restarts that search with a visible note. Exact/lexical rows are read from the immutable projection and merged ahead of the captured vector ranks with public-ID deduplication. No page reruns vector similarity.

`impact` traverses incoming executable/lifecycle membership with node-state uniqueness and paginates affected entrypoints/nodes. `neighborhood` is exactly one hop unless its request explicitly sets `depth` from 1 to 3. If the relevant capability is partial, both return available records plus `knowledge=unknown`; they do not return verified absence.

Advanced `path` requires `graph_context`, `from_handle`, `to_handle`, and `relation_scope=structural|lifecycle`; lifecycle scope also requires `entrypoint_handle`. It performs an exhaustive bidirectional search over the selected current projection with node-state uniqueness. Backend config keys `devboard.graph_query.max_visited_nodes=100000` and `devboard.graph_query.max_wall_milliseconds=2000` are fixed defaults and have no new environment variables. Response `state` is `found`, `not_found`, or `unknown`. `not_found` is legal only when the relevant capability is full and the search exhausts the graph; a safety-budget hit or partial capability returns `unknown` with a reason.

### 10.6 API errors

Every graph protocol error uses exactly:

```json
{
  "protocol_version": "hades.dashboard_graph.v2",
  "error": {
    "code": "graph_context_stale",
    "message": "The graph changed. Reload this exploration.",
    "retryable": true,
    "details": {}
  }
}
```

`details` is a closed code-specific object and is `{}` when no safe structured detail exists. It never contains exception text, SQL/Cypher, internal paths, raw IDs outside the caller's authorized scope, or stack traces. The stable mapping is:

| Code | HTTP | Retryable |
|---|---:|---|
| `graph_not_ready` | 409 | true |
| `graph_context_stale` | 409 | true |
| `graph_scope_mismatch` | 403 | false |
| `graph_entrypoint_not_found` | 404 | false |
| `graph_query_invalid` | 422 | false |
| `graph_projection_partial` | 409 | false |
| `graph_projection_failed` | 503 | true |
| `graph_handle_invalid` | 422 | false |
| `graph_cursor_invalid` | 422 | false |
| `graph_search_snapshot_expired` | 409 | true |
| `graph_maintenance` | 503 | true |

For `graph_maintenance`, details is exactly `{reason:"backup|cutover|retirement|operator",retry_after_seconds:<non-negative integer>}`; the frontend renders the maintenance copy and retries only on explicit user action or after that interval.

Normal authentication failure before graph-protocol authorization remains the application's standard HTTP 401/403 envelope. The graph codes are exactly:

```text
graph_not_ready, graph_context_stale, graph_scope_mismatch,
graph_entrypoint_not_found, graph_query_invalid,
graph_projection_partial, graph_projection_failed,
graph_handle_invalid, graph_cursor_invalid,
graph_search_snapshot_expired, graph_maintenance
```

`graph_projection_partial` is not returned merely because a usable projection is partial. Partial data returns HTTP 200 with completeness metadata. It is used only when the requested operation requires a capability that is wholly unavailable.

## 11. Verification queue

### 11.1 Reuse and isolation

Reuse the existing project-scoped plugin agent work queue. Do not create another transport. Add domain metadata and typed executors so verification work cannot be mistaken for Kanban/chat work.

Server-side and client-side filters are both mandatory:

- the backend claim endpoint filters `work_kind`;
- the generic plugin worker always excludes `hades.verification.graph.v1` and `hades.verification.wiki.v1` even if an older backend ignores the filter;
- the verification worker allows only those two kinds;
- status and quality reports aggregate Kanban and verification separately.

`work_kind=verification` is a list-only aggregate filter alias that expands server-side to the exact stored kinds `hades.verification.graph.v1|hades.verification.wiki.v1`. It is never stored in `agent_work_items`, never appears in a payload, and is rejected on show, claim, heartbeat, complete, retry, or generic worker dispatch. Cursor identity includes the normalized two-kind filter set, so a cursor minted for `verification` cannot be replayed with one exact kind or with `general`. Contract tests freeze alias expansion, authorization, pagination, cursor mismatch, and the prohibition on mutating endpoints.

### 11.2 Work payload

The payload schema is `hades.verification_work.v1`:

```json
{
  "schema": "hades.verification_work.v1",
  "kind": "hades.verification.graph.v1",
  "domain": "graph",
  "project_id": "...",
  "workspace_binding_id": "...",
  "target": {
    "type": "graph_assertion",
    "id": "hades:uncertainty:v2:...",
    "version": "artifact-graph-version"
  },
  "assertion": {
    "fingerprint": "64-hex",
    "subject": {"call_site_id": "hades:call-site:v2:..."},
    "resolution_kind": "call_target",
    "candidate_set_knowledge": "not_applicable",
    "candidate_target_node_ids": [],
    "candidate_edge_ids": []
  },
  "source_snapshot": {
    "state": "available",
    "artifact_graph_version": "64-hex",
    "tree_sha256": "64-hex"
  },
  "question": "Which implementation can this call site invoke in this source snapshot?",
  "reason_code": "dynamic_dispatch",
  "evidence_requirements": ["inspect_service_container_configuration"],
  "source_refs": [],
  "priority": "high",
  "impact": "...",
  "attempt_generation": 1,
  "retry_of_work_item_id": null,
  "deduplication_key": "64-hex"
}
```

`source_snapshot` is a discriminated union: available has exactly `{state:"available",artifact_graph_version:"64-hex",tree_sha256:"64-hex"}`; unavailable has exactly `{state:"unavailable",artifact_graph_version:null,tree_sha256:null}`. Graph work always uses available. Wiki items use `kind=hades.verification.wiki.v1`, target type `wiki_page`, target ID equal to page ID, and target version equal to the exact current revision ID. The Wiki revision's normalized `workspace_binding_id` column selects the current active v2 artifact, or unavailable when none exists. There is one Wiki item per page/revision/source snapshot, not one per claim. A verifiable revision has 1–80 normalized claims; zero claims records `wiki_no_verifiable_claims`, and 81–512 records `wiki_page_requires_split`, with no work item. With unavailable source it can complete only as deferred `source_unavailable`. A legacy/current revision lacking an authorized originating binding creates no item and records reconciliation diagnostic `wiki_workspace_binding_missing`; the service never guesses among project bindings. These diagnostics use the existing audit logger and are coalesced under the locked page/revision; no diagnostic invents a completed/deferred queue row.

A Wiki work payload is exactly:

```json
{
  "schema": "hades.verification_work.v1",
  "kind": "hades.verification.wiki.v1",
  "domain": "wiki",
  "project_id": "...",
  "workspace_binding_id": "...",
  "target": {
    "type": "wiki_page",
    "id": "wiki-page-ulid",
    "version": "wiki-revision-ulid"
  },
  "assertion": null,
  "source_snapshot": {
    "state": "available",
    "artifact_graph_version": "64-hex",
    "tree_sha256": "64-hex"
  },
  "question": "Verify every factual claim in this Wiki revision against the current project source.",
  "reason_code": "wiki_revision_needs_verification",
  "evidence_requirements": ["verify_all_claims_against_current_source"],
  "source_refs": [],
  "priority": "normal",
  "impact": "Unverified project documentation may mislead humans and agents.",
  "attempt_generation": 1,
  "retry_of_work_item_id": null,
  "deduplication_key": "64-hex"
}
```

For graph work, `question`, `reason_code`, `evidence_requirements`, `source_refs`, `priority`, and `impact` are copied byte-for-byte from the immutable uncertainty after schema normalization. Its non-null `assertion` contains exactly the six fields shown and is also copied from that uncertainty; fingerprint must equal target ID suffix, stored `assertion_fingerprint`, and the value used in the deduplication key. Subject, resolution kind, candidate knowledge, and candidate arrays are revalidated against the retained artifact during claim and completion. The worker therefore needs no hidden graph fetch to select a legal result operation. For Wiki work, `assertion` is null and the question, reason code, evidence requirement, priority, impact, and empty source refs are the exact server templates above; the worker cannot rewrite them. In every work payload `kind` equals stored `work_kind`, evidence requirements are non-empty, and `attempt_generation` equals both the request generation and the generation included in the deduplication key.

Deduplication keys are lower-case SHA-256 over these exact JCS objects:

```jsonl
{"kind":"hades.verification.graph.v1","project_id":"...","workspace_binding_id":"...","target_id":"hades:uncertainty:v2:...","target_version":"artifact-graph-version","assertion_fingerprint":"64-hex","attempt_generation":1}
{"kind":"hades.verification.wiki.v1","project_id":"...","workspace_binding_id":"...","target_id":"wiki-page-ulid","target_version":"wiki-revision-ulid","source_state":"available","artifact_graph_version":"64-hex","source_tree_sha256":"64-hex","attempt_generation":1}
{"kind":"hades.verification.wiki.v1","project_id":"...","workspace_binding_id":"...","target_id":"wiki-page-ulid","target_version":"wiki-revision-ulid","source_state":"unavailable","artifact_graph_version":null,"source_tree_sha256":null,"attempt_generation":1}
```

All pre-existing work-item rows receive `work_kind=general`; no payload inference or historical rewrite is attempted. New verification rows are created only through `VerificationQueueService` and always carry a non-null deduplication key.

For graph requests, `assertion_fingerprint` is the uncertainty fingerprint. For Wiki requests, it is SHA-256 JCS of project, binding, page ID, revision ID, source state, nullable artifact graph version, and nullable source tree SHA-256. These values populate `verification_requests` and are not model-generated.

Queue creation is event driven:

- after a graph projection becomes active, `VerificationQueueService` upserts one item for every current artifact uncertainty, reconciles Wiki certification freshness against the new source snapshot, and stales only live items whose exact target/source is no longer current;
- after a Wiki revision with `source_status=needs_verification` becomes current, the service upserts one page/revision item and stales queued/claimed/running items for older revisions;
- a current `verified_from_code|conflict_with_code` revision is quiet only while its normalized certification source import equals the locked current source import; a different current source makes certification `stale`, visible to the user, and queues one page/revision/source item. `developer_provided` remains human-owned and does not auto-queue;
- when active and desired graph imports differ, reconciliation starts no new Wiki verification against either snapshot; the next projection-activation event/sweep retries after publication. Derived certification freshness is exactly `current|stale|unavailable`;
- periodic reconciliation repeats both upserts idempotently so a transient event failure cannot lose work.

`ReconcileVerificationQueue` serializes only scalar project/binding/domain/phase/target values, implements `ShouldBeUniqueUntilProcessing`, has three broker tries with backoff `[10,30]`, hard timeout 60 seconds, and `uniqueFor=240`. Its exact unique ID is `verification-reconcile:{project_id}:{workspace_binding_id}:{domain}`; phase and target hints never enter identity, so listener, sweep, and continuation wake-ups for one scope/domain coalesce. Each execution commits at most 250 upserts or stale transitions and dispatches its same-key continuation only after commit; the lock has already released on processing start, and a five-minute sweep recovers a lost/coalesced continuation from durable state. Graph pages select durable uncertainty-index public IDs in indexed order, then call `GraphV2ArtifactReader::recordsByPublicIds()` for at most that page and verify every stored chunk/ordinal locator. It never depends on disposable validation tables or holds locks while loading a complete artifact. A binding-less operator command expands authorized concrete bindings outside transactions; it never creates a null lock key or one transaction spanning bindings.

Reconciliation first opens a transaction, locks the complete request chain for the exact project/binding/domain/target/version/source/assertion fingerprint, reports `verification_chain_live_conflict` without mutation if more than one live `queued|claimed|running` row exists, and otherwise evaluates **only** the row with maximum `attempt_generation`. Historical generations never block or revive the latest generation. The latest-row state table is normative:

| Latest state for exact target/version/source/assertion | Reconciliation action |
|---|---|
| no request exists | insert generation 1 in `queued` |
| `queued|claimed|running` | leave it byte-for-byte unchanged |
| `completed|failed|canceled` | leave terminal history unchanged; never auto-revive |
| `stale`, and this exact target/version/source is current again after an A→B→A change | insert `latest.attempt_generation+1` in `queued`, link `retry_of_request_id`/`retry_of_work_item_id` to that latest stale predecessor, and use the new generation in the deduplication key |

When a different target version or source snapshot becomes current, insert its generation 1 if absent and mark only older live `queued|claimed|running` requests stale; never mutate an old terminal row. Periodic reconciliation cannot revive deferred/completed, failed, or canceled work. Human retry in section 11.6 is the only same-current-target retry for those terminal states. The A→B→A rule above is automatic reactivation, not a human retry, because the latest A item was stale and never allowed to finish while A was inactive. A required golden sequence is: A generation 1 becomes deferred/completed; a human retry creates A generation 2; B makes generation 2 stale; returning to A creates generation 3 despite the older completed generation 1. A concurrency test races human retry against reactivation under the same chain lock and unique key and proves that at most one successor generation commits.

### 11.3 Structured result

Completion requires the discriminated `hades.verification_result.v1` union. Common fields are:

```json
{
  "schema": "hades.verification_result.v1",
  "domain": "graph",
  "verdict": "verified",
  "target": {
    "type": "graph_assertion",
    "id": "...",
    "version": "..."
  },
  "reason": "Bounded explanation",
  "evidence": [
    {
      "kind": "source_ref",
      "path": "src/Container/services.yaml",
      "line_start": 10,
      "line_end": 20,
      "file_sha256": "64-hex",
      "source_tree_sha256": "64-hex"
    }
  ],
  "graph": {
    "operation": "resolve_call_targets",
    "uncertainty_id": "hades:uncertainty:v2:...",
    "promoted_edge_ids": [],
    "suppressed_edge_ids": [],
    "call_site_id": "hades:call-site:v2:...",
    "subject_edge_id": null,
    "target_node_ids": ["hades:node:v2:..."]
  },
  "wiki": null,
  "deferred": null
}
```

Allowed `verdict` values are `verified`, `contradicted`, and `deferred`. `domain`, target type, and target version must equal the claimed work item. Exactly one domain payload is non-null for a non-deferred result; a deferred result has both domain payloads null and a non-null `deferred` payload. Non-empty prose without a valid result is a worker failure and does not complete the item.

Evidence is a 0–80 item union. `verified` and `contradicted` require at least one item; `deferred` may use an empty array when evidence is unavailable:

- `source_ref` has exactly `kind`, safe source-relative `path`, positive `line_start`, `line_end >= line_start`, exact lower-case `file_sha256`, and exact `source_tree_sha256` equal to the claimed source snapshot;
- `graph_ref` has exactly `kind`, `record_kind=node|edge`, `public_id`, `artifact_graph_version`, and `source_fingerprint`; the backend resolves it only inside the claimed immutable base artifact and accepts it as proof only when its primary evidence is `verified_from_code`. An overlay cannot recursively certify another assertion.

Before either digest is computed, the backend validates every evidence record, rejects JCS-identical duplicates, and canonicalizes the array with this total sort order: a `source_ref` key is `[0,path,line_start,line_end,file_sha256,source_tree_sha256]`; a `graph_ref` key is `[1,record_kind,public_id,artifact_graph_version,source_fingerprint]`. Comparison is numeric for the first discriminator and line fields and Unicode code-point lexical for strings. Client array order has no meaning. The canonicalized array is written back into the immutable result before `result_digest` is computed. Independently, `evidence_digest = SHA256(JCS(result.evidence))` over that exact canonical array; it never includes server-authored effective Edge/Structure `EvidenceEnvelope` records.

A source slice is re-read by the worker immediately before completion. For graph work, the backend requires a matching file node in the claimed artifact with the same safe path and `file_sha256`, and requires `source_tree_sha256` to equal the artifact source identity. For Wiki work, the backend requires the current workspace binding's active v2 artifact to provide the same match; if no current file node exists, source evidence cannot be certified and the result must be deferred. Project memory, vector similarity, prose, or an inferred graph edge may help locate evidence but cannot alone support a verified verdict.

The graph payload is one of these exact operations:

Every graph payload has exactly `operation`, `uncertainty_id`, sorted `promoted_edge_ids`, sorted `suppressed_edge_ids`, nullable `call_site_id`, nullable `subject_edge_id`, and sorted `target_node_ids`. Its valid combinations are:

| Verdict | Operation | Required fields | Backend effect |
|---|---|---|---|
| verified | `resolve_candidate_set` | `candidate_set_knowledge=complete`; null call site and subject edge; empty target nodes; promoted is non-empty; promoted and suppressed are disjoint and their union equals the uncertainty's complete `candidate_edge_ids`; promoted targets and their count satisfy the uncertainty's semantic compatibility row | promoted candidate groups become `agent_verified`; suppressed groups leave the effective projection; no candidate remains unresolved; `entrypoint_handler` sets the effective handler to its exactly one promoted target |
| contradicted | `resolve_candidate_set` | `candidate_set_knowledge=complete`; null call site and subject edge; empty target nodes; promoted is empty; suppressed equals the uncertainty's complete non-empty `candidate_edge_ids` | all candidate groups leave the effective projection but remain in immutable audit data |
| verified | `resolve_call_targets` | `resolution_kind=call_target`; `candidate_set_knowledge=incomplete|not_applicable`; uncertainty's exact call site; null subject edge; 1–20 existing callable target nodes; result promoted/suppressed arrays empty | suppress the call site's unresolved invocation/hint closure and deterministically create effective invocation/return edges |
| verified | `resolve_edge_targets` | one of the five edge-target semantic kinds; `candidate_set_knowledge=incomplete|not_applicable`; null call site; subject edge equals the uncertainty subject; exactly one existing target for `entrypoint_handler`, otherwise 1–20 existing target nodes; result promoted/suppressed arrays empty | suppress the unresolved edge/hint closure and deterministically create effective target edges; `entrypoint_handler` also updates the effective handler |
| contradicted | `reject_unresolved_subject` | `candidate_set_knowledge=incomplete|not_applicable`; for `call_target`, exact call site and null subject edge; for an edge-target kind, null call site and exact subject edge; result target/promoted/suppressed arrays empty | suppress the unresolved invocation or edge and its hint closure without replacement; keep the contradicted assertion in audit |

No graph operation can create a node or accept an edge/structure body. `resolve_candidate_set` is legal for any of the six semantic resolution kinds only when `candidate_set_knowledge=complete`; compatibility, promoted cardinality, and effects still come from that semantic kind. `resolve_call_targets` is legal only for `resolution_kind=call_target` with a non-complete candidate set. `resolve_edge_targets` is legal only for `entrypoint_handler|async_target|exception_target|framework_target|external_target` with a non-complete candidate set. `reject_unresolved_subject` is legal only for those same six semantic kinds, only with verdict `contradicted`, and only with a non-complete candidate set. A complete candidate set must use `resolve_candidate_set`. A mismatch returns `verification_result_not_applicable`; the worker must return `deferred` when evidence cannot satisfy the applicable operation.

For an edge-subject complete candidate set, the result promoted/suppressed IDs are the exact effective edge selection. For a call-site complete candidate set, those IDs select candidate invocation groups: the server expands every selected invocation to its complete validated `returns_to` companion group and promotes or suppresses the group atomically. A companion cannot be selected independently or split from its invocation. The stored overlay's promoted/suppressed arrays contain the expanded closure (invocation plus companions), while the immutable result and `result_digest` retain the worker's invocation-only selection. Missing, shared, or ambiguous companions reject completion. Golden fixtures exercise all six semantic kinds, promote one candidate and suppress another, assert the semantic cardinality rules, assert an effective handler for `entrypoint_handler`, and assert that no orphan return survives.

For every resolution with `candidate_set_knowledge=incomplete|not_applicable`, the server suppression closure includes the exact unresolved subject edge (or unique unresolved invocation for a call), every incomplete locator-hint edge in `candidate_edge_ids`, and every deterministic `returns_to` companion of any included call-site invocation. All hints must represent the same assertion and satisfy the compatibility/equivalence rules; missing, shared, or ambiguous companions reject completion. Thus a hint not selected as the verified target cannot remain effective with a resolved uncertainty. `reject_unresolved_subject` applies this same full closure and creates no replacement.

For `resolve_call_targets`, the backend finds the unique unresolved invocation for the call site and its structure record. For `resolve_edge_targets`, it uses exactly the supplied unresolved subject edge. Effective edges use normal v2 edge identity with a `derived` occurrence: source and relation are copied from the base unresolved subject edge; target comes from sorted `target_node_ids`; `base_edge_id` is that subject edge; `derivation_kind` is `verified_invocation` for a call and `verified_target` otherwise; `target_ordinal` is the target's zero-based position; `exit_ordinal=null`. Call-site, exception-scope, order, source owner, and condition are copied. One target also copies `flow` and `branch_group_id`. Multiple targets use a derived dynamic branch group: a base `async` or `exception` flow retains that value, and every other base flow becomes `alternative`. Derived-edge location is null. Resolution kind must satisfy the exact compatibility row; no implicit target conversion exists. Golden fixtures include a locator hint different from the verified target and assert that it is suppressed.

For every call target, the server also creates one `returns_to` edge from every statically known normal-exit node of that target to the call-site continuation. Its occurrence has `owner_node_id` equal to the call-site structure owner (the caller), the same suppressed invocation as `base_edge_id`, `derivation_kind=verified_return`, the target ordinal, and that exit's zero-based index in the lexicographically sorted unique normal-exit node IDs as `exit_ordinal`; the callee is already identified by the edge source plus target/exit ordinals. Duplicate or non-normal exits reject the operation. Relation is `returns_to`, call site is preserved, flow is `always`, order/condition/branch are null, and location is null. If source, continuation, target, or normal exits cannot be proved from the immutable artifact, the operation is not applicable. Golden identity fixtures assert caller ownership and permute IR exit order without changing derived return IDs.

When an operation has multiple targets, the server creates exactly one effective branch-group structure. For a call, owner and continuation come from the call-site structure and `structural_path=<call-site structural_path>/verified_targets`. For an edge target, owner comes from the base occurrence, continuation is the applicable base/call-site continuation or null, and `structural_path=<ast_path-or-config-pointer>/verified_targets`. In both cases parent precedence is exactly: base edge `branch_group_id` when non-null; otherwise base edge `exception_scope_id`; otherwise call-site `parent_structure_id` when a call; otherwise null. Use ordinal 0, subtype `dynamic_dispatch`, and compute the normal structure ID from exactly `{kind:"branch_group",owner_node_id,structural_path,ordinal:0,subtype:"dynamic_dispatch"}`. Every derived alternative/async/exception edge in the multi-target set references that ID. One target creates no structure. Golden fixtures cover a multi-target call nested in both an if branch and try scope and assert this precedence.

The server-authored primary for every promoted immutable edge, promoted assertion-exclusive base structure, and server-derived effective edge/structure is an exact EvidenceItem: `{origin:"agent_verified",extractor:"hades.verification.v1",source_locator:{kind:"derived",base_edge_id:<immutable base edge ID>},source_fingerprint:<that base edge primary source_fingerprint>,inference_rule:null}`. For a promoted candidate or companion, the base edge is that immutable edge itself. For a promoted complete-set base structure, it is the uncertainty's edge subject, or the lexicographically smallest candidate invocation for a call-site subject. For a derived invocation/target/return edge and its new effective dynamic-dispatch structure, it is the immutable unresolved subject/invocation from which the record was derived. The old base primary plus supporting evidence are sorted and retained as supporting evidence under the normal seven-item/omitted-count rule. The validator resolves every derived locator transitively, rejects a mismatched copied fingerprint, and golden fixtures cover a promoted candidate, promoted base structure, and derived structure. These server envelopes do not enter `evidence_digest`, which remains the digest of canonical `result.evidence`. `reject_unresolved_subject` creates no effective record.

Projection applies flow-step overlays without traversal. Every lifecycle edge carrying a non-null uncertainty is already a frontier in the immutable artifact: a complete candidate edge may have normal frontier steps; for an incomplete/not-applicable uncertainty only the exact subject→unknown-boundary edge may have steps. Incomplete hint edges and every uncertain invocation's companion `returns_to` edges are topology/audit-only and import rejects any `FlowStep` that references them. Traversal stops at every frontier target and incorporates no downstream callee/target summary.

Application is operation-specific and deterministic:

- `resolve_candidate_set` removes steps whose edges are in the suppressed candidate/companion closure. It retains every promoted candidate's existing flow steps byte-for-byte; the edge's effective view keeps the same ID but has `uncertainty_id=null` and promoted evidence. It never manufactures a promoted step from a suppressed candidate step.
- `resolve_call_targets|resolve_edge_targets` removes every subject/hint/companion edge in the suppression closure. For each base flow step of the **exact subject edge only**, and for each newly derived target/invocation edge, it emits exactly one substituted step. Hints and companions have no replacements.
- `reject_unresolved_subject` removes the subject frontier and emits no replacement.

Each substituted step copies exactly `flow_id`, `stage_from`, `stage_to`, `min_depth`, and `async_context` from that exact subject step; references the effective edge's branch group; sets `backbone_role=async` for an async effective edge, `exception` for an exception edge, `branch` when its effective branch group is non-null, and otherwise copies the subject role; and recomputes both the section 6.4 flow-step ID and `order_key` from the effective edge. It copies `async_child_flow_id` and `async_cycle` only when the effective edge preserves the exact immutable async root/child-flow link represented by that subject step; otherwise they become null/false. Expansion occurs independently for every subject `(flow_id,stage_from,stage_to,async_context)` context. The base contract already permits only one step per identity tuple; output deduplication is legal only for a byte-identical full FlowStep and a same-ID/different-`min_depth` result is `graph_overlay_flow_step_collision`. No operation creates a flow step for a derived return edge or traverses a newly verified target. Golden tests cover two subject contexts, incomplete hints with different source depths, and prove no duplicate/min-depth collision.

Instead the lifecycle response emits a non-canonical `verification_materialization_gap` collapsed group from each verified target to the known continuation when one exists, or to an open boundary for async/external targets. Its count is unknown with reason `verified_target_not_materialized`; an async child flow is linked only if that exact child flow already existed in the immutable artifact and the rule above preserved it. Thus topology records the verified target while this artifact's lifecycle remains visibly partial. A plain re-index of unchanged source is not promised to close the gap: it disappears only when source/configuration or a future extractor contract can prove and serialize the target summary. Consuming verification overlays inside the producer is explicitly outside v2. Golden fixtures include two alternative targets with downstream A/B steps, promote only A, and assert that neither A nor B downstream steps survive—only A's frontier plus the materialization gap remains. No disconnected return step or false inline async flow is serialized.

The Wiki payload is exactly:

```json
{
  "page_id": "wiki-page-ulid",
  "expected_revision_id": "wiki-revision-ulid",
  "content_markdown": "# Full, untruncated page\n",
  "content_sha256": "64-hex",
  "content_truncated": false,
  "claims": [
    {
      "claim_fingerprint": "64-hex",
      "verdict": "supported",
      "evidence_indexes": [0],
      "reason": "The route declaration and handler support this claim."
    }
  ]
}
```

Markdown is UTF-8 NFC, at most 512 KiB, and its digest covers exact UTF-8 bytes. `wiki_revisions.content_sha256` stores the digest and the normalized `wiki_verification_claims` table stores the closed `WikiClaimDto` ledger returned by APIs; each record has exactly:

```json
{
  "claim_fingerprint": "64-hex",
  "normalized_text": "The application exposes an admin route.",
  "locator": {
    "byte_start": 120,
    "byte_end": 169,
    "ordinal": 3
  }
}
```

Offsets are a half-open slice of the exact NFC Markdown UTF-8 bytes, land on code-point boundaries, and contain the factual claim. `ordinal` is the zero-based factual-claim order by byte start and must be contiguous. `normalized_text` equals NFC plus collapsed whitespace of the decoded slice; it is 1–2,000 bytes. The fingerprint is SHA-256 JCS of exactly `{page_id,revision_id,content_sha256,normalized_text,byte_start,byte_end,ordinal}`. Ledgers sort by ordinal then fingerprint, contain unique non-overlapping locators/fingerprints, and are stored with the revision before it becomes current. Backend push/show, bootstrap, worker, result validation, and golden fixtures use this same formula; a worker can never supply a new ledger.

The Wiki create/update wire request for `source_status=needs_verification` gains required `claim_locators`, an array of 1–512 records exactly `{byte_start,byte_end,ordinal}` with the same bounds/order rules and no fingerprint/text supplied by the client. The 512 wire/backend ceiling is retained for compatibility with human input, but `hades-wiki-push` and bootstrap MUST split or omit generated content before writing so each generated page has at most 80 factual claims—the verification result ceiling. A generated page with zero claims is omitted. A legacy/human `needs_verification` revision with 81–512 claims queues only the deterministic deferred diagnostic `page_requires_split`; it is never partially verified. `hades-wiki-push` identifies factual claim spans in the final full Markdown and sends those locators; the backend performs no hidden LLM claim extraction. Inside the revision transaction, the backend allocates the revision ULID, normalizes and hashes content, validates every slice, derives normalized text/fingerprint, stores revision plus ledger, and only then performs the current-revision CAS. A locator/content mismatch rejects the whole write. Human/developer-provided content follows its existing status path and is not forced through generated-claim verification.

The read/show response returns the stored `WikiClaimDto[]`, never reconstructed claims. Legacy `needs_verification` revisions without a ledger create no queue work and emit `wiki_no_verifiable_claims`; an explicit new edit/push must create a fresh revision with locators. Contract tests round-trip multibyte Markdown offsets from push to show to verification result and reject stale content, client fingerprints, overlaps, missing ordinals, and post-allocation mutation.

For both Wiki `verified` and `contradicted`, `content_markdown` and `content_sha256` must match the claimed current revision byte-for-byte; verification never edits or removes a claim. There are 1–80 result claims, their fingerprint set must exactly equal the backend ledger, and each result has verdict `supported|contradicted`, a non-empty unique in-range evidence-index list, and a 1–2,000 byte reason. A Wiki `verified` result requires every claim supported. A Wiki `contradicted` result requires at least one contradicted claim; every remaining claim is still classified and evidenced as supported or contradicted. If any claim cannot be classified with admissible evidence, the entire work result is `deferred` with no Wiki payload. Any content correction is a separate Wiki edit creating a new `needs_verification` revision. More than 80 factual claims returns deferred `page_requires_split`; it is not partially verified. A generated page with zero factual claims is omitted; a current `needs_verification` revision with an empty ledger creates no item and records `wiki_no_verifiable_claims` for human correction.

The deferred payload has exactly `blocker_code`, `missing_evidence_requirements`, and `retry_hint`. Codes are `insufficient_evidence`, `source_unavailable`, `ambiguous_scope`, `tool_failure`, `page_requires_split`, and `target_unreachable`; the missing list is a unique 0–16 item lower-snake-case array and `retry_hint` is null or at most 1,000 bytes. `insufficient_evidence`, `source_unavailable`, `ambiguous_scope`, and `target_unreachable` require at least one missing requirement; `tool_failure` and `page_requires_split` may use an empty list. A whole result is at most 1 MiB JCS bytes; common `reason` is 1–2,000 bytes. No free-form `resolved_value` or unknown property is accepted.

Operational state and verdict remain separate:

```text
queued → claimed → running → completed | failed | canceled | stale
completed.result.verdict = verified | contradicted | deferred
lease expiry → existing release/reclaim policy, with an expiry event
```

### 11.4 Lifecycle, leases, and CAS

- Claim verifies agent/device registration, project membership, workspace binding, requested work kind, and target currency.
- The existing server lease duration remains authoritative. The verification worker heartbeats every 30 seconds and also before starting completion; if the server reports less than 15 seconds remaining, it heartbeats before running another evidence operation. A lost/rejected heartbeat aborts the specialist and never submits a result.
- A local specialist is additionally bounded by two parent-owned monotonic deadlines measured from `Process.start()`: a 900-second hard deadline and a 300-second meaningful-progress deadline. Meaningful progress is only a child-ready envelope, successful skill resolution, completed model turn, completed tool call, or final result envelope, each carrying a strictly increasing sequence number. Heartbeats, log/stdout activity, partial streaming, and tool-start notifications do not reset either deadline. The hard deadline never resets; meaningful progress resets only the 300-second deadline. Expiry stops future server heartbeats, emits the local reason `specialist_hard_deadline` or `specialist_progress_deadline`, invokes the process-tree teardown below, and sends no completion/failure/retry mutation. The same CLI invocation must not reclaim that work-item ID; later invocations rely on the authoritative lease and ten-attempt policy.
- A specialist cannot execute until it is inside a mandatory OS containment provider and the server has CAS-created its global specialist fence. Linux uses a delegated cgroup-v2 subtree; Windows uses a kill-on-close Job Object; Darwin uses a run-scoped container with its own PID namespace. A launcher child starts blocked, the parent attaches it to the domain, calls the authenticated fence-start endpoint with a hash of the opaque containment ID, receives a one-time 256-bit fence token (hash stored), and only then releases the child to construct `AIAgent`. If no supported provider is available, `work` returns `specialist_containment_unavailable` before claim/model execution. Descendants cannot escape the domain; terminal tools may not use host PID namespace, privileged mode, nested container sockets, daemonization, or detach.
- The parent still tracks `(pid,create_time)` identities and progress, but teardown is authoritative at the containment-domain boundary. On every normal exit, cancellation, exception, deadline, or `KeyboardInterrupt`, it sets cancellation/calls public `interrupt()` and waits 10 seconds, terminates the whole domain, waits 5 seconds, kills the whole domain, waits 5 seconds, joins/closes IPC, and asks the provider to prove the domain empty/destroyed. A zero-exit valid result may clear the server fence with its one-time token only after that proof. Any abnormal exit first CAS-marks the already-active server fence `quarantined`; even if that notification fails, the existing active fence remains globally blocking. It may clear only after a later explicit containment proof using the same fence token, or an authenticated human-admin clearance that records host/device/domain evidence. Claim/reclaim/lease-expiry handling on every host rejects `active|quarantined`; the attempts counter does not advance while fenced.
- If domain emptiness cannot be proved, the parent leaves the server fence active/quarantined and atomically writes a mode-0600 `$HERMES_HOME/run/hades-verification-<scope-hash>.quarantine.json` containing only work-item/fence IDs, local reason, containment kind/ID hash, and unresolved identities. The next local invocation checks both server and local quarantine before list/claim and cannot delete either merely because a PID disappeared. PID reuse is never evidence. A crashed parent cannot strand an unfenced specialist because server fencing precedes launcher release; a remote profile/host cannot reclaim while that fence exists.
- Completion canonicalizes the submitted result, computes `result_digest=SHA-256(JCS(result))`, validates source/evidence, and builds the complete immutable graph/Wiki draft outside every reference/scope/head/domain lock. It then pre-reads the draft's complete import-ID set. For an available source, the transaction enters `GraphArtifactReferenceLock`, scope lock, projection-head lock, then work/request/domain rows. It requires `active_graph_import_id == desired_graph_import_id == draft.base_graph_import_id`; overlay-source imports may differ only by an exactly equal artifact version and remain separately referenced. A valid Wiki `source_unavailable` completion has an empty set, may be only deferred, requires both active and desired import IDs null under the head lock, and opens an ordinary transaction without a guard. Any source/import/head difference aborts with `verification_source_changed`; it never adds an incremental lock. Commit methods perform DB-only work and consume the same guard. No source/artifact/object-store/model I/O occurs while locks are held.
- If the item is already `completed`, an identical stored digest returns HTTP 200 with the previous canonical result without another domain write, but only after reconstructing the server-authenticated principal, repeating token/agent/device/project/binding/capability authorization, and traversing the same locked current-chain path. A different digest returns `409 verification_result_conflict`. This authorized idempotent replay works after the original lease expired; it is never an unauthenticated fast path.
- Only `claimed|running` may perform a first completion. For those states the service rechecks lease, project, binding, artifact/revision, and source snapshot. `queued` returns `409 verification_not_claimed`; `failed|canceled|stale` returns `409 verification_item_terminal` and applies nothing.
- A superseded graph artifact or Wiki revision makes queued/claimed/running work stale. In the same transaction, the backend clears the claim/lease fields, writes the stale event, and prevents later completion. Stale completion is rejected without applying evidence.
- On first success, the service computes JCS bytes/digest before JSONB encoding; `agent_work_items.result` and `verification_requests.result` receive semantically equal JSONB values, and both `result_digest` columns receive that same JCS digest in the same transaction. PostgreSQL JSONB is not claimed to preserve byte order/whitespace. Domain effects, resolution/resolved time, queue completion state, and audit event commit in that transaction. A transaction test rejects one-sided, JSONB-unequal, or digest-divergent storage.
- `deferred` is terminal and quiet. It is not automatically requeued for the same target/source version. A new target/source version starts at attempt generation 1. Explicit human retry follows the locked latest-chain algorithm in section 11.6, creates at most `latest+1`, sets both retry links, and therefore has a different deduplication key; it never regresses an old terminal item.
- The local cache cannot regress a terminal item to queued when it receives an older remote list response.

### 11.5 Applying verdicts

For graph work:

- `verified` creates an immutable `agent_verified` promotion or call-target overlay for the exact artifact assertion;
- `contradicted` creates an immutable suppression overlay; the candidate remains in audit but is omitted from the next effective projection;
- `deferred` stores the reason and evidence attempt but leaves the assertion unresolved;
- verified or contradicted overlays change `verification_set_hash` and trigger one coalesced projection rebuild from the same immutable artifact;
- the artifact JSON is never modified in place.

In the effective graph, a verified or contradicted overlay removes its base uncertainty from the unresolved set and queue-visible unresolved counts; it remains queryable in audit with `verification_state=verified|contradicted`, result/evidence, and immutable base assertion. A deferred result creates no overlay, leaves `verification_state=needs_verification`, and remains counted as unresolved even though its work attempt is terminal. All affected `CountKnowledge` values are recalculated from effective records without upgrading the immutable completeness capability.

Overlay eligibility is semantic-artifact scoped, not import-row scoped. A byte-identical reimport with the same `artifact_graph_version` may reuse an overlay only after server revalidation of the exact assertion/public IDs; desired/projection membership then stores the new `base_graph_import_id` and the original `overlay_source_graph_import_id`. A different artifact version never inherits an overlay implicitly. This preserves useful verification without confusing evidence provenance or artifact retention.

The server, not the client, builds the stored overlay with schema `hades.graph_overlay.v1` and exactly these fields: `schema`, `artifact_graph_version`, `uncertainty_id`, `assertion_fingerprint`, `operation`, nullable `subject_edge_id`, sorted `promoted_edge_ids`, sorted `suppressed_edge_ids`, sorted `suppressed_node_ids`, sorted `suppressed_structure_ids`, sorted `target_node_ids`, sorted `effective_edge_ids`, sorted `effective_structure_ids`, nullable `effective_entrypoint_handler_node_id`, `evidence_digest`, and `result_digest`. `subject_edge_id` equals the immutable uncertainty subject for every edge-target semantic kind and is null for `call_target`, regardless of operation. For verified `resolve_candidate_set`, `target_node_ids` is derived from the worker-selected promoted invocation/candidate edges **before** companion expansion and `effective_edge_ids` is the promoted expanded closure; both are empty for its contradicted form. For `resolve_call_targets|resolve_edge_targets`, target nodes equal the validated result targets and effective edges are every newly derived target/invocation/return edge. For rejection all target/effective arrays are empty. `effective_entrypoint_handler_node_id` is non-null exactly for a verified semantic `entrypoint_handler` resolution—including `resolve_candidate_set`—and equals its single effective target. Every promoted or derived effective edge/structure view has `uncertainty_id=null`; the immutable base audit record is unchanged. Base referenced IDs must exist in the claimed immutable artifact; effective edge/structure IDs and all array closures are rederived and verified by the server using the rules above.

Structure arrays are not inferred by generic orphan detection. A contradicted complete multi-candidate set places its assertion-exclusive base `dynamic_dispatch` group in `suppressed_structure_ids`; verified selection leaves `suppressed_structure_ids=[]` and places that same base group in `effective_structure_ids` with the agent-verified evidence view, even when only one candidate remains promoted. A verified non-complete multi-target resolution places its newly derived dynamic group in `effective_structure_ids`. One-target non-complete resolution and rejection leave both arrays empty. Projection removes only explicitly suppressed groups and otherwise follows these effective IDs. The overlay object—including `suppressed_structure_ids`—participates in `verification_set_hash`, count deltas, JSON Schema/golden vectors, and projection retry identity.

`suppressed_node_ids` is fully determined by the import invariant: it is `[]` when the assertion has no unknown-boundary node (`candidate_set_knowledge=complete`), and otherwise is exactly `[<that assertion-exclusive unknown_boundary ID>]`. Completion rechecks that every immutable reference to the boundary belongs to this uncertainty's subject/hint suppression closure; any extra effective reference rejects completion as artifact corruption. There is no conditional orphan heuristic. Suppression removes the boundary only from the effective projection/search/count delta, never from immutable artifact/audit bytes. The effective entrypoint query view is separate from immutable producer bytes: for an `entrypoint_handler` verification it exposes `effective_handler_node_id=<overlay target>` and `verification_state=verified`; for contradiction it exposes a null effective handler and `verification_state=contradicted`; before resolution it exposes the base/null handler and `needs_verification`. Queue/count logic uses that state rather than the raw immutable entrypoint `uncertainty_id`. The JSON stored in `graph_verification_overlays.overlay` is this exact object; its `evidence` column is canonical `result.evidence`, so `evidence_digest=SHA256(JCS(result.evidence))`. Overlays never contain arbitrary node, edge, structure, source, label, or property objects.

For Wiki work, `deferred` leaves the page `needs_verification` and records only the resolution attempt. A `verified|contradicted` completion performs this exact immutable-revision transaction:

1. Under the completion transaction's existing import guard, lock the page/current revision and require it still equals the claimed source revision; allocate a new revision ULID.
2. Copy the exact NFC Markdown, content digest, each locator, and each `normalized_text`. Recompute **every** claim fingerprint with the section 11.3 formula using the new revision ID; copied old fingerprints are forbidden.
3. Translate each result classification/evidence mapping from the claimed ledger to the new ledger by the exact key `(ordinal,byte_start,byte_end,normalized_text)`, requiring a total one-to-one match. Store the original result and old fingerprints unchanged only on the verification-request/work-item audit records.
4. Write the new revision as `verified_from_code` for a fully supported result or `conflict_with_code` when at least one claim is contradicted. Store `verification_source_revision_id`, result digest, recomputed normalized claim rows, translated evidence mapping, and normalized graph references; no duplicate ledger copy becomes a second source of truth.
5. CAS the page's current revision from the claimed ID to the new ID in the same verification-completion transaction. `WikiRevisionService` captures the successful page/revision pair and emits `WikiCurrentRevisionActivated` only after commit. Its listener always rereads the page's actual current revision and the locked graph projection head: `needs_verification` queues the current source snapshot; `verified_from_code|conflict_with_code` stays quiet only when its normalized certification source equals that current snapshot; a differing current source marks certification stale and queues exactly that new source. A stale event for a superseded revision is therefore harmless, but an event delayed across a graph change cannot suppress required re-verification.

`CanonicalGraphV2ProjectionActivated` also dispatches Wiki-freshness reconciliation after commit. It locks and rereads the actual projection head plus current Wiki revision, and marks/queues a certified current revision only when `active_graph_import_id == desired_graph_import_id` and that import differs from the stored certification source. While active and desired differ it queues nothing; when both are null freshness is `unavailable`. The periodic sweep executes the same two reread algorithms, so event loss and either delivery order converge to the same state without a certification loop.

Full content, `content_truncated=false`, revision CAS, and claim-to-evidence coverage remain mandatory. Golden tests prove every new fingerprint validates against the new revision ID and that a stale CAS writes neither revision, ledger, evidence, nor completed work state.

Applying a verdict and completing the work item happen inside one backend transaction through `VerificationCompletionService`; clients do not call two independent completion endpoints.

### 11.6 Sync and CLI behavior

Normal `hades backend sync` fetches only this bounded read-only summary:

```json
{
  "verification_queued": 7,
  "verification_high_priority": 2,
  "verification_by_domain": {"graph": 5, "wiki": 2}
}
```

Sync caches and displays the counts plus the next command. It does not list full payloads, claim work, run a model, inject a synthetic user message, or alter the conversation system prompt. Failure to fetch the summary is fail-open for ordinary sync and is reported as a backend warning.

`verification_high_priority` counts only live queued verification requests whose request priority is `high|critical` (transport `high|urgent`). It excludes low/normal, non-queued/terminal work, and generic urgent Kanban work. Sync requests the summary for every linked project/binding even when no graph projection exists; optional projection version validates graph counts but never suppresses source-unavailable Wiki counts.

Add these exact CLI commands:

```text
hades backend verification list
hades backend verification show WORK_ITEM_ID
hades backend verification status
hades backend verification work --once
hades backend verification work --all
hades backend verification retry WORK_ITEM_ID --reason "HUMAN_REASON"
```

Common options are `--domain all|graph|wiki`, `--status`, `--priority`, `--limit`, and `--json`. `--once` processes at most one item. `--all` pages until `next_cursor=null`, but still claims and completes one item at a time and rechecks target currency before every claim. `retry` is an explicit mutation requiring a 1–500 byte reason, a selected work item with status `failed|canceled` or `completed` plus result verdict `deferred`, and a still-current target/source snapshot. It calls `POST /api/plugin/v1/agent-work-items/{item}/retry-verification` with a separate unbound API token carrying `verification.retry`; the token owner must be a current project Admin. Hades auto-issued device credentials never receive this scope. The CLI requires an interactive confirmation and loads only `backend.human_admin_token_env_key`; it never falls back to the worker token, and workers/skills never call retry.

The endpoint rejects device-bound tokens and performs authorization plus a read-only pre-read of selected/latest IDs, current target/source identity, and the complete candidate import set before acquiring any mutable domain lock. For a nonempty set it then enters `GraphArtifactReferenceLock`; a still-current source-unavailable Wiki target opens an ordinary transaction and constructs no guard. Both retry and reconciliation use this single total order: verification scope advisory lock; exact projection-head row; complete work/request chain in ascending `(attempt_generation,id)`; then later overlay/Wiki/domain rows. The available-source path takes the outer reference lock before that order; the source-unavailable path starts directly at the scope lock. Neither path acquires an import/reference lock after a scope/head/domain lock.

Under those locks, the service rereads the selected row, latest chain row, current target/source, head, and complete import set. Any difference from the pre-read aborts the whole transaction with `409 verification_source_changed`; the caller may restart only from outside all locks, never by incrementally widening or retrying under a domain lock. If latest is live `queued|claimed|running`, return `409 verification_retry_existing_live`. If latest is the selected eligible terminal row, create generation `latest+1` linked to latest. If latest is `stale`, the selected row is an eligible terminal ancestor, and this exact A target/source is current again, human retry coalesces with A→B→A reactivation: create generation `latest+1` linked to the latest stale row and record the admin reason plus selected ancestor ID in the audit event. In every other selected-non-latest case return `409 verification_retry_not_latest`; a stale selected item is never itself retry-eligible. Creation writes both retry FKs and the new deduplication generation atomically. Reconciliation takes the identical lock order and unique key, so a race creates exactly one successor; the loser rereads and returns the existing-live conflict rather than another generation.

Add project-scoped capability `verify_project_graph`. Preserve `verify_project_wiki`. Admin user authentication may authorize management, but agent/device capability and project/binding checks still apply to automated claim/complete calls.

### 11.7 Skills

Create:

```text
skills/autonomous-ai-agents/hades-verify/SKILL.md
skills/autonomous-ai-agents/hades-verify/agents/openai.yaml
skills/autonomous-ai-agents/hades-graph-verify/SKILL.md
skills/autonomous-ai-agents/hades-graph-verify/agents/openai.yaml
```

Update the existing `hades-wiki-verify` and `hades-wiki-push` skills.

`hades-verify` is the orchestrator. It lists eligible work, selects one, delegates to the domain specialist, validates the structured result, and completes it. `hades-graph-verify` is read-only against source and backend. Neither skill may modify source code as part of verification. The same agent run that created an assertion must not certify it without independently re-reading the required evidence.

For Wiki work, after claim and before specialist execution, `hades_verification_worker.py` calls the existing authenticated Wiki show/read API through `hades_backend_client.py` with page ID, exact target revision ID, and `full=true`. The typed response used by the worker has exactly `page_id`, `revision_id`, `workspace_binding_id`, `source_status`, full `content_markdown`, `content_sha256`, `content_truncated`, and the exact sorted claim ledger. The worker requires page/revision/binding to match the claim, `content_truncated=false`, content digest equality, and the current source snapshot from the work item; mismatch/later revision aborts specialist output and lets reconciliation/stale CAS handle it.

The Wiki specialist receives that immutable read snapshot, re-reads source evidence, and returns only `hades.verification_result.v1`. It MUST NOT edit the page or update source status. The sole write is the verification complete endpoint, which applies Wiki revision/evidence and work-item completion atomically as section 11.5 requires. Direct invocation of `hades-wiki-verify` routes the user to `hades backend verification work --domain wiki`. The legacy direct Wiki verify backend route, client method, CLI subcommand, action branch, skill fallback, and tests are removed completely; there is no retained human or automated bypass. Tests change the current revision after show but before completion and prove that no Wiki revision, ledger, evidence, or completed work state is written.

No new core model tool is added. The feature is CLI + skill + already service-gated Hades backend plugin surface.

### 11.8 Wiki bootstrap from an empty project

The existing `hades-wiki-push` path remains the creator; no second Wiki generator is introduced. When a project has no current Wiki pages, `hades-wiki-push` runs in bootstrap mode and proposes this fixed initial page set only when evidence exists: `Overview`, `Architecture`, `Entrypoints and lifecycles`, `Domain and data`, `External integrations`, and `Operations`. It reads the current v2 graph first, then current project memory/Wiki context for terminology; vector retrieval may locate evidence but cannot certify a claim.

Every created page stores full Markdown intended for a human: title, short purpose, prose sections, tables/lists where useful, and source-relative links. Machine data is not dumped into visible Markdown: claim fingerprints/locators live in `wiki_verification_claims`, graph/source references in `wiki_graph_artifact_references`, binding/digest on `wiki_revisions`, and evidence in normalized reference rows. `generator_metadata` contains only bounded generator name/version, source snapshot descriptors, and page-template identifier—never claim ledgers, source excerpts, graph blobs, prompts, or model reasoning. A generated factual page has 1–80 claims and `needs_verification`; exactly one request covers the complete revision ledger, never one request per claim. Pages with no evidence are omitted rather than filled with speculation. Re-running bootstrap updates by revision CAS and stable page slug, never creates duplicate titles, and never overwrites `developer_provided` content without an explicit human-approved edit path.

## 12. Hades Agent implementation map

All paths in this section are relative to this repository (`/Users/gabriele/Dev/Hephaistos`).

### 12.1 Contract package

Keep `hermes_cli/hades_graph_contract.py` as the stable internal import facade, but remove every v1 constant, alias, fallback, legacy collection name, and hard entity-count clamp. It re-exports the v2 implementation from:

```text
hermes_cli/hades_graph_v2/__init__.py
hermes_cli/hades_graph_v2/model.py
hermes_cli/hades_graph_v2/schema.py
hermes_cli/hades_graph_v2/identity.py
hermes_cli/hades_graph_v2/canonicalize.py
hermes_cli/hades_graph_v2/coverage.py
hermes_cli/hades_graph_v2/validation.py
hermes_cli/hades_graph_v2/pruning.py
hermes_cli/hades_graph_v2/bundle.py
hermes_cli/hades_graph_config.py
contracts/hades/graph-v2/artifact.schema.json
contracts/hades/graph-v2/bundle.schema.json
contracts/hades/graph-v2/chunk.schema.json
contracts/hades/graph-v2/dashboard-query.schema.json
contracts/hades/graph-v2/dashboard-response.schema.json
contracts/hades/graph-v2/verification-work.schema.json
contracts/hades/graph-v2/verification-result.schema.json
contracts/hades/graph-v2/graph-overlay.schema.json
contracts/hades/graph-v2/golden/canonicalization.json
contracts/hades/graph-v2/golden/dashboard-protocol.json
contracts/hades/graph-v2/golden/verification-results.json
tests/hermes_cli/test_hades_graph_budget_pruner.py
```

Responsibilities are fixed:

- `model.py`: frozen dataclasses/enums for nodes, structures, edges, entrypoints, flows, flow steps, uncertainties, source identity, evidence, and coverage.
- `schema.py`: schema/version constants and JSON serialization/deserialization. It accepts only v2.
- `identity.py`: canonical JSON and all ID/digest functions.
- `canonicalize.py`: semantic dedupe, stable ordering, redaction, and collision detection. A same-ID/different-value collision is a fatal validation error.
- `coverage.py`: capability/per-language/per-flow ledgers, reason counting, zero-versus-unknown helpers, and global completeness calculation.
- `validation.py`: enum, privacy, size, referential integrity, flow membership, and artifact invariant validation.
- `pruning.py`: the exact atomic-unit `GraphBudgetPruner` from section 8.2; no other module removes a valid record for capacity.
- `bundle.py`: deterministic chunking, local spool manifest, resume metadata, and streaming readback.
- `hades_graph_config.py`: the only typed reader for `hades.graph_index`; it consumes the normal merged `config.yaml`, applies compulsory secret exclusions, validates ranges/types, and exposes an immutable config object.

Modify `hermes_cli/config.py` `DEFAULT_CONFIG`, `cli-config.yaml`, and config-validation tests to add the exact YAML keys in section 7.3. Unknown `hades.graph_index` keys are reported as configuration errors at the explicit index command boundary. No environment bridge or runtime mutation is added.

Node identity MUST remain stable when unrelated lines are inserted above a declaration. Therefore:

- named declaration IDs use language, kind, namespace/qualified name, and source-relative path, but not line numbers;
- entrypoint IDs use framework, entrypoint kind, public trigger/match constraints, and registration occurrence; effective handler is deliberately excluded;
- basic-block/branch/merge/loop IDs use containing callable ID plus a normalized AST structural path and branch ordinal;
- anonymous callable IDs use containing declaration ID plus normalized AST structural path;
- locations are evidence fields, not identity fields;
- edge occurrence identity uses containing owner node ID plus normalized AST/config structural path and ordinal, not raw line number.

### 12.2 Indexer architecture

Create:

```text
hermes_cli/hades_index/lifecycle/__init__.py
hermes_cli/hades_index/lifecycle/model.py
hermes_cli/hades_index/lifecycle/builder.py
hermes_cli/hades_index/lifecycle/control_flow.py
hermes_cli/hades_index/lifecycle/interprocedural.py
hermes_cli/hades_index/lifecycle/entrypoints.py
hermes_cli/hades_index/lifecycle/traversal.py
hermes_cli/hades_index/lifecycle/frameworks/__init__.py
hermes_cli/hades_index/lifecycle/frameworks/laravel.py
hermes_cli/hades_index/lifecycle/frameworks/symfony.py
hermes_cli/hades_index/lifecycle/frameworks/django.py
hermes_cli/hades_index/lifecycle/frameworks/fastapi.py
hermes_cli/hades_index/lifecycle/frameworks/express.py
hermes_cli/hades_index/lifecycle/frameworks/nextjs.py
```

`lifecycle/model.py` contains the language-neutral extraction IR: executable declaration, basic block, branch arm, call site, explicit edge fact, terminal, effect, framework pipeline segment, and unresolved fact. `builder.py` is the only indexer component allowed to create artifact objects. `control_flow.py` converts adapter IR into canonical CFG nodes/edges. `interprocedural.py` resolves calls and boundaries without BFS depth limits. `entrypoints.py` normalizes entrypoint identity. `traversal.py` implements section 7.3 and emits one flow per entrypoint plus linked async flows.

The IR is frozen, contains no arbitrary payload dictionaries, and uses these dataclasses:

| Dataclass | Required fields |
|---|---|
| `ExtractionContext` | workspace root, project ID, binding ID, source identity, typed graph config, detected languages/frameworks, Composer/Python/package/tsconfig metadata, and read-only file accessor |
| `SourceLocationIR` | safe relative path, positive start/end line, file SHA-256 |
| `FileLocatorIR` | `kind=file`, safe path, file SHA-256 |
| `AstLocatorIR` | `kind=ast`, source location, normalized structural path, same-kind ordinal |
| `ConfigLocatorIR` | `kind=config`, source location, normalized structural pointer, same-kind ordinal |
| `ExecutableDeclaration` | local key, language, declaration kind, identity kind `named|anonymous`, nullable owner declaration local key, name/qualified name/namespace, modifiers, parameters, nullable return type, AST locator, entry block key, sorted normal-exit block keys, sorted exception-exit block keys |
| `BasicBlock` | local key, declaration key, control kind, ordinal, AST locator, ordered successor descriptors |
| `BranchArm` | branch local key, source block key, target block key, polarity, redacted condition text/hash, arm ordinal |
| `StructureIR` | local key, `call_site|branch_group|exception_scope`, owner declaration local key, normalized structural path, ordinal, closed subtype, nullable continuation block local key, nullable parent structure local key, evidence |
| `CallSite` | local key, `caller_declaration_key`, `source_block_key`, AST locator, target-expression kind, nullable lexical/FQ target, nullable receiver type, argument count, continuation block key, nullable exception-scope key |
| `EdgeFactIR` | local key, source node local key, discriminated target, closed relation/flow, nullable typed condition, nullable branch/call-site/exception-scope local keys, nullable order, AST/config occurrence locator, evidence |
| `ExceptionScope` | local key, declaration key, AST locator, ordered caught type names, catch block keys, nullable finally block key, nullable parent scope key |
| `Terminal` | local key, source block key, kind, nullable public status/exception type, AST locator |
| `Effect` | local key, discriminated source, kind, operation, nullable safe public resource name/protocol, AST/config locator |
| `FrameworkPipelineSegment` | local key, framework role, pipeline order, discriminated target, success successor, ordered short-circuit successors, evidence |
| `EntrypointCandidate` | kind, framework, method semantics/methods, public path/name, trigger, match constraints, AST/config registration locator, nullable handler local key, nullable unresolved-fact local key, ordered framework segments, evidence |
| `UnresolvedFact` | local key, discriminated subject local reference, semantic resolution kind, candidate-set knowledge, reason code, normalized question, evidence requirements, AST/config source locators, separate candidate target local keys and candidate edge local keys, priority, impact |
| `CoverageEvent` | language, capability, outcome, reason code, nullable path, represented/omitted count |
| `AdapterResult` | sorted tuples of every IR record family including `StructureIR` and `EdgeFactIR`, coverage events, typed diagnostics; no canonical IDs |

`lifecycle/model.py` freezes the following supporting unions/enums; adapters cannot pass dictionaries:

```text
Successor =
  AlwaysSuccessor(kind="always", target_block_key, order)
  BranchSuccessor(kind="branch", target_block_key, branch_arm_key, order)
  ExceptionSuccessor(kind="exception", target_block_key,
                     exception_scope_key, caught_type_name|null, order)
  LoopSuccessor(kind="loop", target_block_key,
                loop_role="body|back|exit", order)
  AsyncSuccessor(kind="async", target_local_key,
                 dispatch_kind="event|job|queue|task|promise|callback", order)
  ReturnSuccessor(kind="return", terminal_local_key, order)

TargetExpressionKind =
  direct_function|direct_static_method|direct_instance_method|constructor|
  callable_value|dynamic_member|reflection|eval|import_symbol|framework_service

ControlKind =
  entry|straight_line|branch|merge|loop_header|loop_body|catch|finally|
  return|throw|async_dispatch

TerminalKind = response|redirect|abort|exception|exit

EffectKind =
  data_read|data_write|external_call|cache_read|cache_write|
  storage_read|storage_write|event_emit|job_dispatch|queue_dispatch

Modifier =
  public|protected|private|static|abstract|final|async|generator|
  readonly|sealed|virtual|override

EvidenceLocatorIR = FileLocatorIR | AstLocatorIR | ConfigLocatorIR

EdgeTargetIR =
  LocalNodeTarget(kind="local_node", local_key) |
  BoundaryTarget(kind="boundary", descriptor)

EffectSourceIR =
  BlockEffectSource(kind="block", local_key) |
  CallSiteEffectSource(kind="call_site", local_key)

FrameworkTargetIR =
  FrameworkLocalTarget(kind="local_node", local_key) |
  FrameworkBoundaryTarget(kind="boundary", descriptor)
```

`ParameterIR` has exactly `position`, nullable public `name`, nullable normalized `type_name`, `variadic`, `by_reference`, and `has_default`; default values/raw annotations are never stored. Parameter tuples sort by position. Modifier tuples are unique in the enum order above. `IREvidence` has exactly artifact-legal base `origin`, extractor, one `EvidenceLocatorIR`, and nullable inference rule; builder derives the section 6.4 fingerprint. File locators are accepted only for inventory-file facts. `FrameworkBoundaryDescriptor` has exactly framework, role, nullable safe public name, one AST/config locator, and `IREvidence`. `ConditionIR` has exactly redacted normalized/full-hash/polarity fields from section 6.7. All successor orders are non-negative and successors are sorted by order then kind/target; nullable fields shown above are the only nullable ones.

`CallSite` always stores both containing declaration and source block; neither slash-combined nor inferred fields exist. `Effect.source`, `EdgeFactIR.target`, and `FrameworkPipelineSegment.target` validate only against the discriminated unions above. `EntrypointCandidate` enforces XOR: exactly one of `handler_local_key` and `unresolved_fact_local_key` is non-null. The unresolved branch must point to an `entrypoint_handler` fact whose edge subject is an emitted `EdgeFactIR`. `tests/hermes_cli/test_hades_lifecycle_ir.py` instantiates every variant, includes YAML/XML/PHP configuration locators, and rejects every invalid null/enum/discriminator/reference combination; it is the adapter-author contract.

Every local key is SHA-256 JCS of exactly `{language,path,record_family,locator_kind,structural_path_or_pointer,ordinal}`; `FileLocatorIR` uses an empty structural value and ordinal zero. Cross-record references must resolve inside the same `AdapterResult` or a declared boundary descriptor. Successor descriptors are the closed union above. `UnresolvedFact.subject` is exactly `{kind:"call_site",local_key}` or `{kind:"edge",local_key}`; every edge key and candidate edge key must resolve to an emitted `EdgeFactIR`, and its resolution/candidate fields follow section 6.11. `GraphBuilder` first canonicalizes nodes/structures and every `EdgeFactIR`, assigns canonical target/edge IDs, and only then builds uncertainty objects, chooses the deterministic complete-set subject, and reproduces exhaustive candidates, fingerprint, and uncertainty ID. It never hashes ambiguous local-key lists into the public contract. Diagnostics are `info|warning|error` plus one stable code and safe `SourceLocationIR`; exceptions and raw source are logged locally but never enter the artifact. `AdapterResult` validation runs before aggregation, then `GraphBuilder` maps local keys to canonical node, structure, edge, flow, and flow-step identities exactly once. Every emitted call-site/branch/exception reference must originate from one `StructureIR`; a reference without its structure record is a fatal adapter error.

Modify:

```text
hermes_cli/hades_index/base.py
hermes_cli/hades_index/__init__.py
hermes_cli/hades_index/aggregate.py
hermes_cli/hades_index/inventory.py
hermes_cli/hades_index/resolution.py
hermes_cli/hades_index/tree_sitter_adapter.py
hermes_cli/hades_index/php.py
hermes_cli/hades_index/python.py
hermes_cli/hades_index/typescript.py
hermes_cli/hades_index/sql.py
```

Required changes:

- The adapter interface becomes `index(context: ExtractionContext, files: Sequence[Path]) -> AdapterResult`.
- `hades_index/__init__.py` runs all detected adapters, removes the unknown-language-to-Python fallback, and records typed failures instead of swallowing them.
- `aggregate.py` merges only v2 IR/artifact objects and rejects semantic collisions.
- `inventory.py` emits entrypoint candidates and test nodes; it no longer promotes v1 `routes[]` or `tests[]` collections during finalization.
- `resolution.py` removes `route_reaches_table` as lifecycle truth and removes the depth-eight BFS. It resolves calls into exact, candidate, or unresolved results.
- `tree_sitter_adapter.py` returns a typed `ParseResult` with either syntax IR or a typed failure. It extracts branches, merges, loops, return/throw, try/catch/finally, await/async, and call sites. Missing parser is never represented as `None` success.
- `php.py`, `python.py`, and `typescript.py` keep language parsing but delegate graph construction and framework lifecycle semantics to the new modules.
- `sql.py` emits data topology and `not_applicable` executable capabilities only.

The PHP file is already a god-file. New lifecycle logic MUST be extracted into the listed modules rather than adding another large block to `php.py`.

### 12.3 Dependencies and installation

Modify:

```text
pyproject.toml
uv.lock
tests/test_project_metadata.py
```

Add exactly `jsonschema==4.26.0`, `tree-sitter==0.26.0`, `tree-sitter-javascript==0.25.0`, `tree-sitter-typescript==0.23.2`, `tree-sitter-php==0.24.1`, `tree-sitter-python==0.25.0`, and `filelock==3.24.3` to the mandatory project dependencies. They are not optional extras and are never lazy-installed. The four grammar wheels total less than 1 MB on supported macOS and Linux platforms and avoid role-specific installation states; a PM-only agent pays only that disk cost because parser loading and the canary occur exclusively at the explicit graph-index boundary. No grammar may be downloaded at graph-index runtime. The TypeScript wheel's `language_tsx` factory is mandatory and canary-tested alongside `language_typescript`.

Before indexing any supported source language, the graph producer runs a real in-memory parse canary for every detected supported language. A missing package, missing grammar, incompatible parser, or failed canary is an installation failure and blocks graph publication; it is never reported as a partial graph. After the canary passes, a parse failure confined to one ordinary source file produces a typed per-file partial coverage event and indexing continues. Tests assert the exact dependency pins, absence of a `hades-indexer` extra/lazy group, all supported canaries, global fail-fast behavior, and per-file partial behavior.

The separately distributed Codex `hades-backend` plugin does not bundle Tree-sitter or reimplement indexing. It verifies the installed Hades CLI, invokes its graph index/sync commands, polls backend projection state, exposes lifecycle/verification workflows when the v2 API is deployed, and reports parser installation failures verbatim. This keeps one producer implementation and one parser version surface.

### 12.4 Jobs, sync, cache, clients, and provider

Modify:

```text
hermes_cli/hades_backend_jobs.py
hermes_cli/hades_backend_sync.py
hermes_cli/hades_backend_client.py
hermes_cli/hades_backend_benchmark.py
hermes_cli/hades_source_slice_policy.py
hermes_cli/gnothi/collectors/source.py
plugins/memory/hades_backend/__init__.py
```

Required changes:

- Jobs build and validate a v2 bundle, then stage it for resumable sync. No code mutates the logical artifact after digest finalization.
- The existing 10,000-file, 512-KiB-per-file, 5,000-node, 10,000-edge, and 500-item silent completeness assumptions are removed. Safety budgets remain configuration values and create counted partial coverage.
- Sync accepts and uploads only `hades.code_graph.v2` for code graphs. It resumes chunks, marks cache current only after backend `ready`, and fetches only the verification summary.
- Source-slice priority becomes entrypoint root, middleware/security/input, branch/unresolved points, domain/data/integration, then tests.
- Gnothi never marks a partial graph fully current or falls back to v1 structures.
- The benchmark fixture includes more than 5,000 nodes, 10,000 edges, and 500 routes and asserts either full chunked delivery or explicit partial reasons.
- `HadesBackendClient` replaces v1 `GET graph/traverse` with `POST graph/query` carrying exactly the closed v2 query body; its typed response requires protocol/context/completeness fields. Client-supplied project/scope/principal fields, 404 fallback, and v1 response adaptation are forbidden.
- The memory plugin removes graph v1 schemas and every local/repository topology fallback. It may retain upload/cache bookkeeping for the producer, but callers/dependencies/lifecycle/impact/path come only from the backend's active v2 projection. Vector retrieval can return candidate graph handles and must resolve topology through the exact v2 query.

### 12.5 Verification client and worker

Create:

```text
hermes_cli/hades_verification_contract.py
hermes_cli/hades_verification_tasks.py
hermes_cli/hades_verification_worker.py
```

Modify:

```text
hermes_cli/hades_plugin_work_items_client.py
hermes_cli/hades_plugin_worker.py
hermes_cli/hades_plugin_tasks.py
hermes_cli/hades_backend_db.py
hermes_cli/hades_backend_status.py
hermes_cli/hades_quality_report.py
hermes_cli/hades_backend_cmd.py
hermes_cli/hades_backend_runtime.py
```

Exact behavior:

- `hades_verification_contract.py` validates the work/result JSON schemas and verdicts.
- `hades_verification_tasks.py` implements list/show/cache/status with verification-only filters.
- `hades_verification_worker.py` implements claim, heartbeat, specialist execution, result validation, and complete for one item at a time.
- Work-item client list/claim methods accept kind, project/repository, binding, priority, status, cursor, and limit. Completion accepts typed `result`.
- Generic plugin task and worker paths exclude verification work twice: request filter and local payload check.
- The local SQLite work-item cache uses idempotent `add_column_if_missing` for `workspace_binding_id TEXT`, `subject_type TEXT`, `subject_id TEXT`, `subject_version TEXT`, `deduplication_key TEXT`, `priority TEXT`, `execution_attempts INTEGER NOT NULL DEFAULT 0`, `lease_expires_at TEXT`, `result_json TEXT`, `remote_state_version INTEGER NOT NULL DEFAULT 0`, and `remote_updated_at TEXT`, with indexes supporting project/binding/kind/state/version lookups. Opening an old database twice is harmless. A response with a lower version is ignored; an equal version must be JSON-semantically equal for all cached remote fields or is logged as `verification_cache_version_conflict` and ignored. A greater version may apply only a server-valid transition. `queued→claimed→running` and live→terminal are normal. `claimed|running→queued` is accepted only at a greater version with remote claim/lease fields cleared (lease-expired reclaim); any `completed|failed|canceled|stale` row can never regress. Wire `canceled` maps to existing local `cancelled` only at this storage boundary. Human retry/reactivation uses a new row.
- Backend status prints separate `task_work` and `verification_work` sections. Pending verification is actionable, not backend degradation; transport/auth failures are degradation.
- Quality reports never apply Kanban shared-memory metrics to verification items.
- Command dispatch implements exactly the commands in section 11.6 and adds no core model tool.
- `_detect_default_capabilities()` explicitly advertises `verify_project_graph` when the installed Agent supports this workflow, while preserving `verify_project_wiki`; the backend still requires explicit registration/grant and never treats a legacy null capability set as a graph-verification grant.
- Add mandatory direct dependency `filelock==3.24.3`. Before list/claim, acquire nonblocking `$HERMES_HOME/run/hades-verification-{sha256(profile+project_id+workspace_binding_id)}.lock` and hold it through the entire `--once|--all` run, heartbeat, and completion, releasing in `finally`. A same-profile/scope concurrent process exits `already_running`; the backend lease/CAS remains authoritative across machines.
- `SkillVerificationSpecialistRunner` uses a fresh child per item; that child creates a fresh `AIAgent` and session ID, resolves exactly the graph or Wiki specialist via `agent.skill_commands.get_skill_commands()`, injects the selected skill plus immutable server work/evidence contract as the user task, and may return only one closed result JSON envelope. It uses configured verification credentials/model and a 40-iteration budget. Heartbeats remain in the parent. The parent enforces the section 11.4 monotonic 900-second hard deadline, 300-second meaningful-progress deadline, strict progress sequence, and no-same-invocation reclaim rule. Before releasing the blocked launcher, it establishes the mandatory Linux cgroup-v2, Windows Job Object, or Darwin run-scoped-container provider and the server-side specialist fence. Teardown kills/proves that whole domain; the server fence blocks every host until explicit proof-backed clear and a local marker is diagnostic only. Unsupported containment fails before claim. Lease/CAS loss or either local deadline sends no completion/failure mutation; a later invocation may rely on the server lease/ten-attempt policy only after both global and local quarantine safety pass. Interactive conversation and cross-item transcript/process state are never reused.
- Remove `hades backend wiki verify`, `HadesBackendClient.verify_wiki_page()`, and the direct mutation action. Keep read/show operations. Human retry remains a distinct interactive admin-token operation and is never invoked by a worker/skill.

### 12.6 OpenAPI and Hades documentation

Modify:

```text
docs/hades/openapi-hades-v1.json
docs/hades/backend.md
docs/hades/operations.md
docs/backend-agent-coordination.md
```

The OpenAPI `PluginAgentWorkItem.payload` becomes a discriminator-based `oneOf` containing the existing Kanban payload and `VerificationWorkPayload`. Add `VerificationResult`, graph import endpoints, chunk contracts, verification filters, and completion result. Remove statements that v1 graph schemas remain valid for the v2 pipeline or that a legacy adapter can feed the current graph.

### 12.7 Standalone Codex plugin

The Codex integration remains a standalone repository at `/Users/gabriele/plugins/hades-backend`, never a directory in Hades Agent and never an edit under `/Users/gabriele/.codex/plugins/cache`. It owns exactly five user-facing skills after this release: the existing backend, Wiki push, and Wiki verify skills plus `hades-graph-explore` and `hades-verify`. Every skill delegates to the separately installed `hades` executable; none implements HTTP, tokens, graph schemas/parsers, claim/retry state, or Wiki mutation itself.

Plugin contract tests parse the manifest and every skill fully, require unique names and nonempty descriptions of at most 60 Unicode code points, reject v1 `graph/traverse`, direct `wiki verify`, private endpoint logic, token literals, and unsupported commands, and permit automatic execution only for same-project read-only status/list/show/query. Publish/install by bumping to a strictly newer `0.2.0+codex.YYYYMMDDHHMMSS` version through the personal marketplace. Because a running Codex task has an immutable skill catalog, installation acceptance occurs in a fresh task and records plugin SHA, source digest, installed version, task ID, and smoke output. If no plugin Git remote exists, commit locally and report that push is unconfigured; never invent a remote.

## 13. React frontend implementation

The frontend lives in the backend repository under `frontend/`. Inertia is not reintroduced. `GraphPage.tsx` remains a thin route/page composition component.

### 13.1 File split

Replace the current monolithic explorer with:

```text
frontend/src/components/devboard/graph/GraphExplorer.tsx
frontend/src/components/devboard/graph/GraphScopePicker.tsx
frontend/src/components/devboard/graph/GraphModeToggle.tsx
frontend/src/components/devboard/graph/EntrypointPicker.tsx
frontend/src/components/devboard/graph/LifecycleStageRail.tsx
frontend/src/components/devboard/graph/LifecycleCanvas.tsx
frontend/src/components/devboard/graph/LifecycleAccessibleTree.tsx
frontend/src/components/devboard/graph/LifecycleNodeDrawer.tsx
frontend/src/components/devboard/graph/AsyncFlowsPanel.tsx
frontend/src/components/devboard/graph/ElementAnalysisPanel.tsx
frontend/src/components/devboard/graph/TechnicalGraphDetails.tsx
frontend/src/components/devboard/graph/graphExplorerReducer.ts
frontend/src/components/devboard/graph/lifecycleLayout.ts
frontend/src/components/devboard/graph/useGraphBootstrap.ts
frontend/src/components/devboard/graph/useEntrypoints.ts
frontend/src/components/devboard/graph/useLifecycle.ts
frontend/src/components/devboard/graph/useElementAnalysis.ts
frontend/src/components/devboard/graph/graphCopy.ts
frontend/src/components/devboard/graph/__tests__/graphExplorerReducer.test.ts
frontend/src/components/devboard/graph/__tests__/lifecycleLayout.test.ts
frontend/src/components/devboard/graph/__tests__/GraphExplorer.test.tsx
frontend/src/components/devboard/graph/__tests__/GraphScopePicker.test.tsx
frontend/src/components/devboard/graph/__tests__/LifecycleCanvas.test.tsx
frontend/src/components/devboard/graph/__tests__/LifecycleAccessibleTree.test.tsx
frontend/src/components/devboard/graph/__tests__/LifecycleNodeDrawer.test.tsx
frontend/src/components/devboard/graph/__tests__/ElementAnalysisPanel.test.tsx
frontend/src/components/devboard/graph/__tests__/useGraphBootstrap.test.tsx
frontend/src/components/devboard/graph/__tests__/useLifecycle.test.tsx
frontend/src/pages/__tests__/GraphPage.test.tsx
frontend/src/pages/__tests__/GraphPageProjectTransition.test.tsx
```

Modify:

```text
frontend/src/pages/GraphPage.tsx
frontend/src/types/devboard.ts
frontend/src/lib/apiClient.ts
frontend/src/lib/mockApi.ts
frontend/package.json
frontend/package-lock.json
```

Remove the old `frontend/src/components/devboard/GraphExplorer.tsx` and `frontend/src/pages/graphExplorerModel.ts` after all imports point to the new feature folder; no compatibility re-export remains.

Add exactly `@xyflow/react@12.11.2` to frontend dependencies and lock it. Do not add ELK, D3, Cytoscape, or a second graph renderer. `LifecycleCanvas` is a controlled, read-only React Flow surface: nodes cannot be dragged, connected, or deleted; selection, pan, wheel/pinch zoom, fit-view, keyboard focus, and the built-in controls are enabled. `LifecycleCanvas.tsx` imports `@xyflow/react/dist/style.css` exactly once; no page/global file imports it again. Min/max zoom are `0.2` and `1.8`; fit-view padding is `0.15`.

### 13.2 Initial layout

The visible default layout, top to bottom, is:

1. Title and one-sentence plain-language purpose.
2. Two-option mode toggle: Follow an entry point and Analyze an element.
3. Entrypoint picker with search and kind filters.
4. Selected entrypoint header with method/path or kind/name, handler, evidence badge, and honest completeness badge.
5. A two-column lifecycle work area: a 240-pixel stage/filter sidebar and the canvas; below 768 pixels the sidebar becomes a horizontal chip row above the canvas.
6. Lifecycle canvas showing the compact backbone and collapsed group summaries.
7. Linked asynchronous flows panel, collapsed by default when empty.
8. Technical details disclosure, collapsed by default.

Raw node/edge/module/route counters are removed from the primary visual hierarchy. They live inside Technical details. Internal scope IDs, projection versions, coverage reason codes, and provenance strings are hidden by default and translated to plain-language summaries.

Core English copy is fixed in `graphCopy.ts` (future localization may replace it, components may not):

| Key | Copy |
|---|---|
| title | `Explore how this application works` |
| HTTP purpose | `Choose an endpoint to see what can happen from request to response.` |
| non-HTTP purpose | `Choose an entry point to follow its execution from start to finish.` |
| lifecycle mode | `Follow an entry point` |
| analysis mode | `Analyze an element` |
| stage sidebar | `Show lifecycle stages` |
| partial badge | `Some paths may be missing` |
| unknown count | `Unknown — this part of the graph is incomplete` |
| verified empty | `No matching relationships were found in the complete indexed scope.` |
| stale reload | `The graph changed while you were exploring it. We reloaded the latest version.` |
| collapsed | `More nodes are available. Expand this section to inspect them.` |
| maintenance | `The graph explorer is being upgraded. Other project features remain available.` |

Every primary action has one exact effect: `Change` reopens the entrypoint picker; a stage checkbox hides/shows that stage; a collapsed summary expands it; a node opens the drawer; an async summary loads/selects its child flow; `Load more` uses the supplied cursor; `Reset view` performs the reducer behavior in section 13.4; `Retry` repeats only the failed read request; `Technical details` toggles the disclosure; `Compare connection to…` opens a second-element picker and calls `path` only after a valid selection. Disabled actions display their reason. There is no button whose handler is empty or logs only.

#### 13.2.1 Scope bootstrap and default mode

`useGraphBootstrap.ts` executes this exact state machine after project selection:

1. Fetch `scopes` with no graph context.
2. With zero authorized scopes, show the graph-not-ready/empty state and issue no graph query.
3. With one scope, select it automatically.
4. With multiple scopes, read `sessionStorage["hades.graph.scope." + projectId]`; reuse it only if it is still in the current authorized response. Otherwise show `GraphScopePicker` and issue no scope-bound query until the user chooses.
5. Store an explicit/automatic valid choice under that project key. Fetch `overview` first without context and adopt its returned context, then fetch `entrypoints` with that exact context; do not race two bootstrap requests that both mint a context. Scope never appears in the URL; the URL contains only the session-bound entrypoint handle.
6. Map `recommended_mode=request_lifecycle|execution_flow` to UI mode `follow_entrypoint`; map `analyze_element` to `analyze_element`. Project or scope change resets to this recommendation. A user mode toggle persists only while project and scope remain unchanged.
7. In follow mode use HTTP purpose copy when the selected entrypoint is `http_route`, or before selection when recommendation is `request_lifecycle`; otherwise use non-HTTP purpose copy. If verified-full discovery returns no entrypoints, switch to Analyze an element. If discovery is partial and currently empty, retain the recommended mode but show the partial empty explanation and allow manual Analyze.

The scope picker labels use only `ScopeDto.label`/`workspace_name`; the ULID appears only inside Technical details. A scope saved for another project, removed binding, or unauthorized binding is never submitted.

### 13.3 Entrypoint interaction

- Before selection, show a searchable list of dynamically loaded entrypoints; never send lifecycle queries with an empty handle.
- HTTP list labels are `METHOD /path` with route name and handler as secondary text.
- Non-HTTP list labels include entrypoint kind and public name.
- After selection, collapse the picker to a compact selected-entrypoint bar with a Change action.
- Search is debounced by 250 ms and cancels obsolete requests.
- Preserve selected entrypoint in the URL query string using its opaque handle only for the current browser session. A stale handle triggers clean reselection.

### 13.4 Stage rail and canvas

- Stage checkboxes are populated from `stage_counts`; stages absent with `absence_verified` are omitted, while unknown stages display `?`.
- Every represented backbone stage starts checked. Unchecking a stage hides its canonical nodes/edges and leaves one truthful collapsed-stage connector with its `CountKnowledge`; rechecking shows cached data or calls `lifecycle_expand` once if that stage has never been expanded.
- The canvas initially renders only the at-most-120-node returned backbone plus collapsed summaries; it does not fetch or draw the whole flow.
- Expanding a stage replaces only that stage's expansion page. Expanding a collapsed group calls `lifecycle_expand` with its opaque handle. `Load more` appends only a response whose cursor/context/request generation still match.
- Selecting a node opens the drawer and may request node expansion; it never clears the surrounding lifecycle.
- Branch arms have distinct labels and text descriptions; color is not the only differentiator.
- Loop/back edges use a loop marker and are never recursively duplicated.
- Async dispatch ends visually at an async boundary in the main flow and links to the separate Async flows panel.
- Response, redirect, abort, exception, and exit are visually distinct terminal outcomes.
- The browser holds at most 200 canonical `LifecycleNodeDto` records in the canvas state; collapsed summary cards do not count as canonical nodes. Before a new expansion, it collapses least-recently-used expansions until `base backbone + retained expansions + requested limit <= 200`. It never evicts the base backbone, the selected node's owning expansion, or the expansion currently being requested. The requested API limit is `min(100, remaining capacity)`. If no capacity remains, the action explains which selected expansion must be closed; it does not silently drop data. `Load more` follows the same rule.

The collapsed-stage connector is frontend-only and has exactly this TypeScript shape:

```ts
interface HiddenStageSummary {
  kind: 'hidden_stage'
  stage: LifecycleStage
  count: CountKnowledge
  sourceHandles: string[]
  targetHandles: string[]
}
```

It is derived from the selected `StageCountDto` plus base lifecycle edges crossing into/out of that hidden stage; handle arrays are unique and lexically sorted. It is not a `CollapsedGroupDto`, has no backend handle, and cannot be sent to `lifecycle_expand`. Clicking it simply rechecks the stage. Its connector label uses the stage count contract (`N`, `N+`, or `?`) and never implies that hidden nodes are absent.

Layout is deterministic and does not delegate creative choices to the implementer:

- desktop and mobile both use left-to-right stage bands, so a resize never changes lifecycle semantics; mobile relies on pan/zoom and the accessible list;
- each stage band is 420 pixels wide and every new node starts at `x = stage_ordinal * 420`;
- node cards are 240 by 72 pixels. Define `laneSeed` as `backbone=0`, `alternative=1`, `exception=2`, `loop=3`, `async=4`; every new node starts at `y = min_depth * 128 + (laneSeed + lane_ordinal) * 88`;
- process visible nodes in `layout.order_key` order. Keep positions already cached by handle. For a new node, if its 16-pixel-padded box overlaps an occupied box in the same stage, move it down in 88-pixel increments until free. Therefore expanding one branch never moves an existing node;
- stage backgrounds and labels stay fixed; collapsing removes only that expansion's cached positions; `Reset view` collapses all non-base expansions, clears non-base positions, and calls fit-view;
- React Flow `smoothstep` edges are solid for mandatory flow, labelled for alternatives, dashed for exception, dotted for async, and rendered with a loop marker for loop/back edges. Unresolved edges end at a warning boundary. Every style has a text/icon distinction, not color alone;
- the viewport is at least 640 pixels high on desktop and 520 pixels on mobile. Zoom controls and Reset are always visible; the minimap appears only at widths at least 1024 pixels.

The API supplies `stage_ordinal`, `min_depth`, `order_key`, `branch_lane`, and non-negative `lane_ordinal` in each node's `layout`. These values affect presentation only. `lifecycleLayout.ts` is a pure function with golden position fixtures; it must not inspect labels or randomize coordinates.

### 13.4.1 State ownership and request cancellation

`GraphExplorer` owns one `useReducer` state from `graphExplorerReducer.ts`. Its exact state families are `projectId`, `scopes`, `scope`, `requestGeneration`, `graphContext`, `projectionKey`, `mode`, `entrypoint`, `entrypointFilters`, `baseLifecycle`, `expansions`, `visibleStages`, `selectedNode`, `drawer`, `viewport`, `requests`, and `staleReloadAttempted`. There is no single global `error`. `requests` is keyed by operation plus stable selector (`scopes`, `overview:<scope>`, `entrypoints:<filters-hash>`, `lifecycle:<entrypoint>`, `expand:<selector>`, `detail:<node>`, `search:<query-hash>`, and so on). Every value has exactly `{status:'idle'|'loading'|'success'|'error',generation,requestedContext,error}`; `requestedContext` is nullable and `error` is the typed API error or null. Expansion entries carry selector, nodes/edges/groups, next cursor, last-used monotonic counter, and the request generation that produced them.

Project or scope change increments `requestGeneration`, aborts every in-flight graph request, clears context/entrypoint/lifecycle/expansions/selection/URL handle, resets `staleReloadAttempted=false`, and then runs the bootstrap sequence. Entrypoint change increments generation, aborts lifecycle/detail/expansion requests, preserves only project/scope/mode, writes the new `entrypoint` query parameter, and fetches one lifecycle. Mode change aborts requests owned by the old mode and clears only old-mode selection/results.

Every response action carries captured generation, captured `requestedContext`, and returned context. The reducer applies these exact guards:

- a bootstrap request sent without context may adopt the returned non-null context only when its generation still matches **and** current `graphContext` is still null;
- a contextual response is accepted only when `requestedContext === current graphContext === returned graphContext` and generation matches;
- if an otherwise current response returns a different context while one is active, do not merge any payload. When `staleReloadAttempted=false`, set it true, abort dependent requests, clear context-dependent state, and launch exactly one clean bootstrap reload with the stale-copy notice;
- if a second mismatch/409 occurs while `staleReloadAttempted=true`, keep the typed error visible and stop automatic reload. A user Retry starts a new generation and resets the flag.

Late/aborted responses never change request status for a newer generation. A successful clean bootstrap resets `staleReloadAttempted=false` only after its context and projection key are stored.

`useEntrypoints`, `useLifecycle`, and `useElementAnalysis` are fetch-only hooks. They accept explicit DTO inputs plus `AbortSignal`, return typed promises, and own no durable UI state. Every effect cleanup aborts its controller. Search debounce is 250 ms after NFC/collapsed-whitespace normalization; an empty search cancels rather than queries. The URL stores only `entrypoint=<opaque handle>`; on reload it is validated against the new entrypoint response/context, and invalid/stale handles are removed with `history.replaceState` before clean reselection.

### 13.5 Node drawer

The drawer displays:

```text
plain-language role in this lifecycle
kind and qualified name
source-relative file and line
incoming/outgoing relationships within the selected flow
branch condition summary when applicable
data effects and external integrations
evidence origin and verification state
uncertainties with reason and verification status
```

Technical IDs and raw JSON are available only in a nested Advanced disclosure.

### 13.6 Analyze an element

The mode starts with one search box whose examples adapt to the project: route, class/function, file, or plain-language intent. Results show why they matched. Selecting a result shows its bounded neighborhood, callers, dependencies/callees, impact, and entrypoint lifecycles that include it.

When a family is incomplete, the card says `Unknown — this part of the graph is incomplete`; it does not show zero. `Find path` is removed from the normal view. An advanced `Compare connection to…` action inside Technical details may call the existing `path` query and must show no-path versus unknown distinctly.

### 13.7 Loading, empty, partial, and error states

- Loading uses stable skeletons; do not flash a zero graph.
- No entrypoints with verified full discovery explains library/data-only scope and switches to Analyze an element.
- Partial discovery explains that more entrypoints may exist and links to technical omission reasons.
- A usable partial lifecycle remains interactive with a persistent warning badge.
- HTTP 409 `graph_context_stale` clears all dependent state and performs one full reload with a visible explanation.
- A projection failure offers Retry and returns to the last known ready projection when the API exposes one.
- Every button has an implemented action and a test. No inert `Find path` or placeholder controls remain.

### 13.8 Accessibility and responsive behavior

- Every graph has an equivalent keyboard-navigable ordered list/tree representation.
- Nodes are buttons with accessible names; edges/branches have text labels.
- Focus moves into the node drawer and returns to the selected node on close.
- Stage toggles, branch expansion, and mode toggle are fully keyboard operable.
- At widths below 768 px, the stage rail becomes a horizontal scroll/chip row, the canvas is full width, and the node drawer becomes a bottom sheet.
- Respect reduced-motion settings and WCAG AA contrast.
- `graphCopy.ts` centralizes all user-facing copy. Do not scatter new technical English strings through components.

## 14. Clean-cut rollout and data safety

### 14.1 Deployment order

Use this exact order:

1. Integrate each independently reviewed task/slice into its current `main`, smoke and push before branching the dependent slice; assemble the release manifest from those exact integrated SHAs without rewriting task history.
2. In a new server Git worktree, create a uniquely labeled Docker Compose acceptance project with disposable PostgreSQL/Neo4j/Redis/artifact volumes, loopback-only free ports, and no Traefik route; production remains untouched.
3. Run forward migrations, the real user seeder, token-bound Neo4j schema initialization, and health checks in that isolated stack. Create only run-scoped project/binding/agent/device credentials and a temporary Mac `HERMES_HOME`.
4. Import a pinned read-only Symfony Demo, interrupt/resume one chunked upload, and validate artifact, projection incarnation, entrypoint lifecycle, search, partial reasons, Wiki/verification work, Agent CLI, Codex plugin, and desktop/mobile UI.
5. Run every automated gate and fresh review, compare all isolated truth stores, prove production inventory unchanged, and let the user test the loopback frontend through an SSH tunnel.
6. Revoke all acceptance credentials and remove only run-labeled disposable resources after evidence is sealed. Ask separately whether the reviewed candidate may be promoted to production.
7. Only after production-promotion approval, prove each current `main` contains the exact reviewed release-manifest SHAs, capture/verify the DR snapshot and scoped v1 export, and deploy a maintenance frontend whose `/graph` makes no graph request.
8. While production graph maintenance is active, deploy forward migrations, v2-only ingestion/projection/verification/API, token-bound Neo4j schema upgrade, dedicated workers, Hades Agent v2, and finally the React Graph Explorer. There is no dual-protocol route or v1/local fallback.
9. Run production read-only smoke and unrelated-count/admin-login comparisons; keep scoped v1 export and previous ready data throughout the rollback window.
10. Ask a second explicit question for v1 retirement. Only an affirmative answer authorizes the receipt-bound scoped `retire-v1 --confirm`; declining preserves rollback data.
11. Carnovali may be scheduled later as an explicit elapsed/RSS-monitored scale gate. It is not a release blocker and is never modified or logged by this platform work.

The executable release contract is Plan 5's checked-in closed schemas for `hades.graph_v2_release_manifest.v1` plus `hades.graph_v2_production_command_plan.v1`, sealed only after isolated evidence integration, stored outside Git under `/home/ubuntu/graph-v2-release-control/<RUN_ID>/`, and bound by byte SHA-256 in the forward-only driver journal. The release manifest includes an exact absolute-path/SHA-256 executable allowlist. The command plan contains only validated argv arrays and the exact phases `preflight`, `refresh_dr`, `maintenance_frontend`, `migrate`, `neo4j_schema`, `workers_scheduler`, `agent`, `final_frontend`, `smoke`, `retirement_dry_run`, `retirement_confirm`, `status`, and `rollback`; argv may use only closed manifest placeholders or Plan 5's phase-gated, digest-revalidated journal placeholders produced by successful preflight/DR/retirement phases, and the driver uses no shell evaluation or ad-hoc argument construction. Its executable `rollback` phase freezes one mode: before completed retirement it restores exact pre-v2 artifacts and the retained v1 pointer without graph-data restore; after completed retirement and recorded P2 it consumes the sealed scoped v1 export through the forward restore, then restores pre-v2 artifacts. Both modes are resumable under graph maintenance and never perform whole-system DR or down-migrate the schema. Promotion, retirement, and whole-system DR each require their own exact mode-0600, owner-only, non-symlink, single-line approval file through `record-promotion-approval`, `record-retirement-approval`, or `record-dr-approval`. The journal retains only approval digest/timestamp/operator/bindings, never the raw approval. No plan or operator edits that journal manually.

### 14.2 Required backups

Before any graph retirement or migration that can delete data, create two different backup classes:

- **Disaster recovery only:** create a PostgreSQL custom-format backup and verify `pg_restore -l`; stop the pinned Neo4j 5.26.27 Community service during the graph maintenance window, dump `system` and `neo4j` with `neo4j-admin database dump`, restart it, inspect both archives with `neo4j-admin database load ... --info`, and smoke-restore into a disposable volume using the same image. Snapshot the exact configured filesystem/object-storage disk used by `ArtifactStorageService`; write a key/path, byte-size, ETag-if-available, and SHA-256 inventory and verify a sample plus all graph-reachable objects after restore into a disposable disk/prefix. One common DR manifest binds PostgreSQL, both Neo4j dumps, artifact-storage inventory/snapshot, image versions, timestamps, and every digest. These whole-system backups are never used as an ordinary graph rollback because they could erase unrelated writes made after the dump.
- **Feature rollback:** run `php artisan hades:graph-v2:export-v1 --project=PROJECT_ULID --output=/backups/PROJECT/v1-graph`. The command begins or resumes a project `reason=retirement`, owner-kind `v1_export` bound maintenance operation and drains ordinary graph leases before its first capture; it never exports changing v1 state. The forward export state is `prepared→postgres_captured→neo4j_captured→artifacts_captured→sealed→verified`. PostgreSQL capture uses one `REPEATABLE READ` snapshot and streams only project/scope v1 projection rows/attempts, graph-search rows, artifact metadata, active-pointer mappings, and exact IDs. While the same generation-fenced authority remains held, it captures matching Neo4j namespaces and guarded artifact bytes through the common inventory service. It writes newline-delimited JCS records plus a manifest containing export/window/scope/authority generations, snapshot identity, table/model family, IDs, counts, source scopes, projection versions, constraints/index metadata, file digests, and one whole-export digest. It also records exact pre-v2 backend/frontend image digests and Git commits plus the Hades Agent commit and installable artifact digest/path. It refuses anything unreachable or cross-project. A pre-seal failure leaves maintenance active and resumes the same export; after disposable restore marks it `verified`, `completeBoundProjectOperation` closes that export window. Later retirement starts its own bound operation and revalidates that sealed snapshot against the human-approved receipt/current selection.
- Record timestamp, server, database/image versions, Git commits, file sizes, row/node/relationship counts, and SHA-256 digests for both classes. Validate the scoped export by importing it into disposable PostgreSQL schemas and a disposable Neo4j database/volume and comparing every manifest count/digest. Validate the DR class by restoring all three truth stores—PostgreSQL, Neo4j, and artifact storage—into disposable targets and resolving a retained graph artifact through `ArtifactStorageService`.
- Retain both classes until the user explicitly accepts v2 and the v1 retirement window closes.

Redis and queue payloads are explicitly excluded from backup truth: they are transient delivery/cache state reconstructed from PostgreSQL projection heads, verification requests, and reconciliation jobs after restore. The rehearsal flushes the disposable Redis instance and proves that reconciliation restores required work without duplicating completed items.

#### 14.2.1 Recurring hourly differential backup

The cutover backup is not the recurring backup system. Add these backend-repository/operator files:

```text
ops/backup/hades-backup
ops/backup/hades-restore-rehearsal
ops/backup/hades-backup.example.yaml
ops/systemd/hades-backup-hourly.service
ops/systemd/hades-backup-hourly.timer
ops/systemd/hades-backup-rehearsal.service
ops/systemd/hades-backup-rehearsal.timer
docs/operations/disaster-recovery.md
```

Use an encrypted, content-addressed Restic repository so every hourly snapshot transfers/stores only changed blocks even when a logical dump file is regenerated. Secrets (`RESTIC_PASSWORD_FILE` and remote repository credentials) live only in a root-readable systemd credential/environment file with mode `0600`; schedules, retention, paths, service names, and safety behavior live in `ops/backup/hades-backup.example.yaml`, copied to `/etc/hades-backup/config.yaml`. No credential enters Git or Laravel/Hades normal config.

`hades-backup` acquires `/run/lock/hades-backup.lock`, creates a mode-0700 timestamped staging directory, and captures one consistency set in this exact order: enable global graph maintenance with `reason=backup`, retaining the one-time raw token only in memory or a mode-0600 staging file excluded from the manifest; drain all read/mutation leases; create the PostgreSQL custom-format dump and verify `pg_restore -l`; cleanly stop pinned Neo4j Community; dump `system` and `neo4j`; restart Neo4j and pass health; while maintenance is **still active**, snapshot/inventory the complete configured `ArtifactStorageService` disk/prefix, verify every graph-reachable object against the PostgreSQL snapshot, and write/seal the common JCS manifest plus SHA-256 tree; only after that seal call maintenance off with the raw token and required Neo4j health. Restic then snapshots the sealed complete set with tags `hades`, `hourly`, and timestamp and verifies snapshot presence before deleting staging.

Because the PostgreSQL dump necessarily contains that active backup window while the raw token is intentionally absent, every common manifest binds a random consistency-set ID plus the captured window ID, `reason=backup`, start time, token hash, and captured verification-execution epoch. It may also contain claims/fences whose source process domain is not part of backup truth. After loading a consistency set and before starting any worker/reconciliation, `hades:graph-v2:recover-restored-backup-window` performs the only tokenless recovery permitted anywhere. Under the exclusive global session advisory lock it requires a regular non-symlink owner-only mode-0600 restore-target descriptor that identifies either a disposable rehearsal target or a separately DR-approved target; exact manifest bytes/digest and consistency/window fields; verified PostgreSQL, both Neo4j, and artifact-storage digests; and Neo4j health. It first rotates the singleton verification execution epoch and clears/requeues or exhausts copied older-epoch nonterminal ownership exactly as section 9.1 specifies, then CAS-closes only that captured active backup row and writes bound audit records. Stale source-epoch heartbeat/fence/completion calls fail. A production target without the independently recorded consistency-set-bound DR approval, a different/current window or epoch, digest mismatch, unhealthy store, or replay fails closed. No raw maintenance/fence token is backed up and no generic tokenless maintenance-off/epoch-rotation operation exists.

The active global window is enforced centrally by `ArtifactStorageMaintenanceGuard` inside every destructive `ArtifactStorageService` entry point—delete, replace, move-overwrite, and cleanup—including callers outside graph cleanup. Immutable creation is allowed only at an absent key or when bytes are identical. `GraphArtifactInventoryService` streams deterministic `{relative_key,bytes,etag|null,sha256}` rows, rejects traversal/duplicates, and verifies every graph-reachable object from the PostgreSQL snapshot; extra unreachable bytes are harmless while missing/mismatched reachable bytes fail. A static architecture test rejects direct destructive disk/object-store calls outside the guarded service/scoped restore adapter. Graph maintenance remains active through all three truth-store captures; unrelated non-graph backend features remain available. Any maintenance, lease drain, PostgreSQL verification, Neo4j stop/dump/restart/health, artifact inventory, manifest seal, or off-token failure exits nonzero and leaves graph maintenance fail-safe for operator recovery; it never labels an inconsistent set successful.

The timer uses `OnCalendar=hourly`, `Persistent=true`, `RandomizedDelaySec=300`, and no overlapping run. Retention is exactly 72 hourly, 35 daily, 12 monthly snapshots, applied only after the new snapshot and `restic check` metadata pass; the last known successful snapshot is never pruned by a failed run. The job writes `/var/lib/hades-backup/status.json` atomically with last attempt/success, snapshot ID, per-store digests, duration, and safe failure code; backend operations/status reads it read-only and alerts on more than 90 minutes since success.

`hades-restore-rehearsal` runs weekly, selects the newest successful consistency set, restores Restic data into disposable paths, restores PostgreSQL into a disposable database/container, loads both Neo4j dumps into a disposable volume using the recorded image, restores artifact storage into a disposable disk/prefix, resolves a retained graph artifact, and compares all manifest counts/digests. It must prove queue reconstruction with empty Redis, then destroy only explicitly tagged disposable resources. The weekly timer never points at production databases/volumes. A release is not DR-ready until one rehearsal has passed and its report is retained.

The implementation MUST NOT run `migrate:fresh`, drop the whole PostgreSQL database, clear all Neo4j data, or delete users/projects/memory/Wiki/Kanban data. If an unexpected destructive recovery or database restore occurs, restore the backup, rerun the user seeder, verify login/admin access, and only then declare completion.

### 14.3 Scoped retirement command

Create backend Artisan command:

```text
php artisan hades:graph-v2:retire-v1 --project=PROJECT_ULID --dry-run
php artisan hades:graph-v2:retire-v1 --project=PROJECT_ULID --confirm \
  --receipt=/absolute/server/path/retirement-receipt.json \
  --receipt-sha256=RECEIPT_SHA256 \
  --scoped-backup-manifest=/absolute/server/path/v1-graph/manifest.json \
  --scoped-backup-sha256=MANIFEST_SHA256
```

`--dry-run` is mandatory before `--confirm`. It writes this closed JCS receipt shape:

```json
{
  "schema": "hades.graph_v1_retirement_receipt.v1",
  "metadata": {
    "generated_at": "RFC-3339 UTC",
    "nonce": "cryptographically-random-base64url"
  },
  "selection": {
    "project_id": "PROJECT_ULID",
    "postgres_primary_keys": {},
    "artifact_blob_digests": [],
    "neo4j_namespace_keys": [],
    "source_scopes": [],
    "row_count": 0,
    "node_count": 0,
    "relationship_count": 0,
    "active_v2_projection_versions": []
  },
  "selection_sha256": "64-hex"
}
```

`selection_sha256=SHA-256(JCS(selection))`; the displayed `receipt_sha256` is SHA-256 of the exact whole receipt bytes. `--confirm` first verifies the supplied whole-file digest, parses/validates the receipt, then recomputes only `selection` from current state and compares both its JCS bytes and `selection_sha256`. It never tries to recreate time or nonce. It refuses unless:

- a ready v2 active projection exists for every v1 source scope selected;
- a smoke-tested scoped v1 export manifest and both exact digests are provided through command options;
- the project ULID matches exactly;
- no selected v2 projection or non-graph record would be deleted.

`GraphV1RetirementService` receives the verified sealed export, revalidates the approved receipt/current selection, and starts or resumes its own `v1_retirement` bound project operation before any delete. Its state machine is exact:

1. `prepared`: persist validated receipt/selection, sealed export/backup references, pre-v2 deployment metadata, window/scope/authority generations, and operation ID. Under `withinAuthorizedMutation`, fenced-mark each selected v1 Neo4j batch started with ordinal/selection/pre-state digest before deletion, record its completion/post-state digest afterward, verify zero selected records and unchanged unrelated counts, then fenced-CAS `neo4j_deleted`.
2. `neo4j_deleted`: under the same bound authority, fenced-mark the selected PostgreSQL mutation before one transaction deletes only v1 projection/artifact metadata/search rows, preserving blob bytes for retention/backup cleanup; record post-state digest, verify selected rows are zero and unrelated/v2 counts unchanged, then fenced-CAS `postgres_deleted`.
3. `postgres_deleted`: rerun manifest/namespace/count checks, record audit completion, set `completed`/`completed_at`, then `completeBoundProjectOperation(...requireNeo4jHealthy=true...)` closes maintenance atomically with the verified terminal state.
4. A process error records safe `last_error`/`failed_at` without moving past the last completed state. After TTL, rerunning `--confirm` calls `resumeBoundProjectOperation` for the exact nonterminal retirement, increments authority generation, rotates the token, and resumes idempotently. A stale owner cannot heartbeat, delete, advance, transfer, or close.

The service never deletes v2 or unrelated data. The project graph route stays in bound maintenance while state is `prepared|neo4j_deleted|postgres_deleted`, including after a crash or token loss. An ordinary off command cannot release it. It can close only after verified `completed`, or transfer to a restore that reaches verified `completed` and then marks the retirement `restored`. Unreferenced retained blob bytes are eligible for normal reachability cleanup only after the rollback window closes.

### 14.4 Rollback

Before v1 retirement, rollback first activates the graph-only maintenance screen, then redeploys the previous backend/frontend images and repoints to the retained v1 active graph; the graph route is reopened only after its v1 smoke tests pass. A failed v2 candidate needs no data rollback because the active pointer never changes.

After retirement, preserve the v2 operator artifact/worktree that contains the restore command; do not deploy it away before restoration completes. The exact rollback order is mandatory:

1. Begin a new project `reason=retirement`, owner-kind `v1_restore` bound operation for a completed retirement, or transfer the still-bound nonterminal retirement to `v1_restore`; validate the exact sealed export/manifest/selection and persist `prepared` in `graph_v1_restores`.
2. Under `withinAuthorizedMutation`, restore only absent or byte-identical selected artifact objects through the scoped guarded adapter; verify digest/count and fenced-CAS `artifacts_restored`.
3. Restore only absent or byte-identical scoped PostgreSQL records/search rows; refuse any different collision or v2/unrelated selection and fenced-CAS `postgres_restored`.
4. Restore only the exact scoped Neo4j namespace, verify counts/digests/unrelated invariants, and fenced-CAS `neo4j_restored`.
5. Restore and verify the exact v1 active pointer/source-scope mapping captured by the export, then fenced-CAS `pointer_restored`.
6. Redeploy the exact pre-v2 backend/frontend image digests and verify commits; install the recorded pre-v2 Hades Agent artifact only if required. Run authenticated v1 API, graph query, desktop/mobile `/graph`, Neo4j health, and unrelated project/user/memory/Wiki/Kanban checks; fenced-CAS `smoke_verified`.
7. Set restore `completed`, mark retirement `restored`, and call `completeBoundProjectOperation(...requireNeo4jHealthy=true...)`; only then reopen `/graph`. A crash resumes the exact forward stage with a rotated authority and never overwrites different bytes.

Whole PostgreSQL/Neo4j/artifact-storage restore is reserved for declared disaster recovery with a global write freeze and explicit human approval; it is never the normal feature rollback.

## 15. Acceptance tests

Implementation follows red-green TDD. Each row is a release gate, not an optional example.

### 15.1 Agent contract and extraction

| ID | Required assertion | Primary local test target |
|---|---|---|
| G01 | Only the top-level v2 schema and nested v2 contract are accepted; v1 cannot be adapted, merged, uploaded, queried as current, or selected from cache. | `tests/hermes_cli/test_hades_graph_contract.py`, backend sync/client/provider tests |
| G02 | Named node IDs survive unrelated line insertion and all IDs are permutation invariant; distinct semantic occurrences remain distinct. | `test_hades_graph_contract.py` |
| G03 | Every edge/flow reference resolves; a semantically unresolved target creates the exact uncertainty/boundary, while a malformed or dangling locator rejects the artifact. | `test_hades_graph_contract.py` |
| G04 | No absolute path, raw source, secret-like literal, traversal component, or control character survives validation. | graph contract security tests |
| G05 | Evidence, flow semantics, and completeness are orthogonal; verified conditional branches in a partial graph remain verified branches. | new evidence/completeness tests |
| G06 | Every in-scope omission is counted and makes the exact capability/flow partial; configured exclusions do not. | graph contract/index enrichment tests |
| G07 | More than 5,000 nodes, 10,000 edges, and 500 routes/tests has no silent truncation; chunk reassembly is digest/count identical or explicitly partial. | backend jobs/bundle/benchmark tests |
| G08 | `if/else`, switch/match, early return, try/catch/finally, loops/back edges, recursion, and async are represented without path enumeration. | language fixtures and golden tests |
| G09 | Laravel/Symfony, Django/FastAPI, Express/Next, process main, CLI, scheduler, consumer/listener, and public API entrypoints are found as specified. | adapter golden tests |
| G10 | Async child flows are linked but excluded from the synchronous request path after dispatch; terminal outcomes are complete or explicitly unknown. | lifecycle traversal tests |
| G11 | Polyglot workspaces run all adapters, preserve language identity, and aggregate coverage without collisions. | aggregate/polyglot tests |
| G12 | Missing required grammar/analyzer cannot produce full completeness. | tree-sitter/enrichment tests |
| G13 | Root/backend JSON Schemas, OpenAPI, Python/PHP validators, TypeScript DTOs, and all JCS/ID/digest golden vectors are byte-compatible. | contract manifest and cross-runtime golden tests |
| G14 | Base artifacts reject `agent_verified`/`observed_runtime`; candidate edges are owned by exactly one uncertainty. | artifact provenance/candidate tests |

### 15.2 Backend projection and API

| ID | Required assertion | Backend test target |
|---|---|---|
| L01 | Import create/chunk/complete is authenticated, project/binding scoped, resumable, digest-checked, and idempotent. | Graph import feature tests |
| L02 | A lifecycle projection is bound to exact project, scope, artifact, verification set, and entrypoint. | projection integration tests |
| L03 | No repository/workspace fallback and no artifact-version merge can occur. | repository/query isolation tests |
| L04 | Staging/projecting data is invisible; `ready+partial` publishes atomically. | projection concurrency tests |
| L05 | Injected validation/Neo4j failure leaves the old active version fully readable. | failure-injection tests |
| L06 | Zero is returned only with `absence_verified`; partial families return null/unknown. | API contract tests |
| L07 | Duplicate deliveries are idempotent; only an expired fenced lease can be reclaimed, every domain retry gets a fresh incarnation, and a late owner cannot publish, delete the winner, or create/recreate records after its own incarnation is retired. | projection lease/Neo4j-fence concurrency tests |
| L08 | New artifact or overlay creates a new projection version and invalidates old context/cursors without mixing responses. | graph-context tests |
| L09 | Producer flow membership includes every unique verified-reachable or uncertainty-frontier `(edge, stage_from, stage_to, async_context)` state once, never traverses an uncertain target, and backend projection applies the exact overlay substitution rules. | lifecycle traversal and projection-mapper tests |
| L10 | Entrypoint/lifecycle/expand/search/detail/impact/path protocol v2 responses match the schema and remain bounded. | dashboard graph controller/service tests |
| L11 | Gzip wire bytes, single-member/trailing-byte/bomb limits, descriptor order, staging uniqueness/references, and semantic-manifest replay behave exactly as section 8. | import transport/adversarial tests |
| L12 | Four single-delivery domain attempts use fresh rows/incarnations and 10/30/90 delays; a generation blocked only after attempt four is not reconciled again until a new generation or explicit retry. | projection reconcile/queue-runtime tests |
| L13 | Vector search pages reuse one session/project/projection-bound snapshot and fail cleanly after its TTL; no later page reruns similarity. | dashboard search snapshot tests |
| L14 | Stale desired-generation jobs cannot publish after a newer artifact/overlay wins the head CAS. | projection concurrency tests |
| L15 | Graph validation uses two bounded streaming passes, four separately tokenized single-try runs, exact heartbeat CAS, and a reclaimed old worker can never commit. | import validation lease/failure-injection tests |

### 15.3 Verification

| ID | Required assertion | Primary test target |
|---|---|---|
| V01 | Graph and Wiki work payloads contain exact project/binding/target version/question/evidence/dedupe fields and validate against OpenAPI. | local contract + backend request tests |
| V02 | Identical uncertainty/page revision creates one work item; a new target version creates a new item and stales the old. | dedupe/stale tests |
| V03 | Cross-project, cross-binding, wrong device, missing capability, or wrong kind claim/complete is rejected. | authorization tests |
| V04 | Lease, heartbeat, expiry, state-versioned reclaim, authorized idempotent completion, and the ten-execution ceiling preserve queue behavior; out-of-order DTOs cannot regress cache state. | worker/backend lease tests |
| V05 | Free prose cannot complete verification; only a valid structured verdict can. | verification worker tests |
| V06 | Stale artifact/revision CAS applies no evidence and cannot later issue a second fail completion. | concurrency/worker tests |
| V07 | Deferred is completed and quiet; it does not requeue until new evidence/version/manual action. | queue/status tests |
| V08 | Wiki verification keeps the full-content gate, exact source-revision CAS and ledger, then allocates a new immutable revision and recomputes its complete claim ledger. | Wiki service/skill tests |
| V09 | Graph verification creates immutable overlays; contradicted facts disappear only from a new effective projection and remain auditable. | overlay/projection tests |
| V10 | Sync fetches/caches/displays counts only; it never claims, invokes a model, or injects conversation messages. | sync/conversation tests |
| V11 | `work --once` handles at most one item; `--all` paginates one-at-a-time with target recheck; heartbeat cannot hide a hung specialist; hard/progress deadlines, mandatory OS containment, and the server fence prevent zombie work, cross-host reclaim, and same-run reclaim. | command/worker containment tests |
| V12 | Generic Kanban worker cannot claim verification even against a backend that ignores requested filters. | generic worker tests |
| V13 | Local cache compares remote state versions, accepts only the versioned lease-reclaim transition, rejects equal-version conflicts, and never regresses terminal state. | backend DB/cache tests |
| V14 | Status/quality separate work domains and capabilities remain project scoped. | status/quality/client tests |
| V15 | Human retry increments attempt generation, preserves the terminal predecessor, requires current source/target and admin authorization, and cannot duplicate a live attempt. | retry endpoint/CLI/dedupe tests |
| V16 | Wiki verified/contradicted results preserve Markdown/locators/text, reject missing claims, and recompute every fingerprint against the newly allocated revision ID while retaining the old result in audit. | Wiki result adversarial tests |
| V17 | Graph results cannot inject arbitrary records; node/edge/structure suppression, evidence locators, frontier FlowSteps, entrypoint effective view and materialization gaps are server-derived exactly from the claimed artifact. | graph overlay schema/service tests |
| V18 | Empty-project Wiki bootstrap creates only evidence-backed human Markdown pages with at most 80 claims each, stores machine metadata separately, is slug/CAS-idempotent, and queues generated claims for verification. | Wiki push job/skill integration tests |
| V19 | The standalone Codex plugin delegates only to the installed Hades v2 graph and verification CLI, exposes all five skills in a fresh task, and contains no private HTTP/v1/direct-Wiki implementation. | `/Users/gabriele/plugins/hades-backend/tests/test_plugin_contract.py` |

### 15.4 Frontend

| ID | Required assertion | Frontend test target |
|---|---|---|
| U01 | The default mode follows entrypoint availability and explains its purpose in plain language. | `GraphPage.test.tsx`, `GraphExplorer.test.tsx` |
| U02 | No lifecycle request is sent before project, scope, and entrypoint are valid. | `GraphPageProjectTransition.test.tsx` |
| U03 | Selecting `/generale/soggetti-attivi/` loads a backbone, stage counts, branches, terminal outcomes, and async summary. | Graph Explorer integration test |
| U04 | Stage, branch, and node expansion send the exact current graph context and reject mixed stale state. | hook/component tests |
| U05 | Unknown is never rendered as zero and partial warnings remain visible without disabling usable data. | model/component tests |
| U06 | Find path is absent from the primary view; advanced compare connection has success, no-path, unknown, and error states. | component tests |
| U07 | Technical IDs/details are collapsed by default; raw metric cards are not primary content. | snapshot/semantic query tests |
| U08 | At most 200 nodes render simultaneously while pagination/expansion preserves discoverability and states that content is collapsed. | canvas performance test |
| U09 | Every visible control works; no inert button or placeholder action exists. | interaction coverage test |
| U10 | Keyboard navigation, focus return, text-equivalent graph, reduced motion, contrast, and mobile bottom sheet pass. | accessibility/responsive tests |
| U11 | Project/scope/entrypoint/mode changes abort old requests; generation/context guards ignore late responses and stale URL handles are removed. | reducer/hook race tests |
| U12 | Layout golden fixtures are deterministic; branch expansion preserves existing coordinates and LRU collapse never exceeds 200 canonical nodes. | `lifecycleLayout.test.ts`, reducer/canvas tests |

### 15.5 Isolated live acceptance gate

The final acceptance fixture is a pinned, read-only Symfony Demo in a run-labeled disposable Compose project. The frozen fixture preflight requires route `/en/blog/` and symbol `App\\Controller\\BlogController::index`; changing the pinned commit requires updating and reviewing those expectations before execution. Generated project/binding/device identifiers and the temporary Mac `HERMES_HOME` are recorded in the run manifest and must not equal any existing/production identifier. Carnovali is excluded from this gate.

Acceptance requires:

1. Fresh v2 import and chunk upload completes without v1 fallback.
2. `/en/blog/` is discoverable by URI, meaningful segment, route name, and natural-language query candidate followed by graph resolution.
3. Selecting the route shows ordered Symfony lifecycle stages through handler and at least one terminal response/error outcome.
4. At least one conditional branch can be expanded and both alternatives are represented.
5. Callers/dependencies/impact are either supported values or honest unknowns; no false zero.
6. `App\\Controller\\BlogController::index` is searchable and shows the entrypoint flows that include it when evidence supports membership.
7. Any real unresolved dynamic target creates a visible graph verification item; if Symfony Demo has none, an already-reviewed deterministic fixture proves this in a second disposable project rather than injecting a row into Symfony Demo.
8. A Wiki page still marked `needs_verification` creates or exposes exactly one page/revision item.
9. `hades backend sync` reports verification counts without processing them.
10. One graph and one Wiki verification item can be completed end-to-end with valid structured results.
11. Hades Agent and all five standalone Codex plugin skills use the same disposable backend project; the plugin delegates only to the v2 CLI.
12. Browser desktop and mobile views have no black screen, unexpected 401/404/405/500, dead action, mixed version, or uncaught console error.
13. PostgreSQL/Neo4j/artifact active incarnation and counts/digests agree with the manifest; production resources and unrelated data are unchanged.
14. Acceptance credentials are revoked before label-scoped teardown, the disposable user seeder/admin login are reverified, and Traefik was never modified.

## 16. Documentation and operator handoff

The implementation is not complete until these documents describe the deployed behavior:

- `docs/hades/backend.md`: graph v2 contract, source identity, completeness/evidence, lifecycle, verification queue, vector role, no-v1 rule.
- `docs/hades/operations.md`: import status, chunk resume, projection reconciliation, verification worker, backup, scoped retirement, and rollback.
- `docs/hades/openapi-hades-v1.json`: exact wire contracts.
- `docs/backend-agent-coordination.md`: project/binding isolation and read-only sync notification.
- frontend operator/readme section: build/deploy command, Graph Explorer smoke test, and Traefik separation remains unchanged.

No entry is written to `LOGBOOK_CARNOVALI` because this is Hades platform work, not a Carnovali feature change.

## 17. Definition of done

The work is complete only when all of the following are true:

- every G, L, V, and U acceptance row is automated and green;
- the isolated Symfony Demo gate passes, its run-scoped resources are safely revoked/removed, and production inventory remains unchanged;
- v2 artifacts and APIs contain no v1 compatibility path;
- graph and Wiki verification work end-to-end;
- partial data cannot appear as verified absence;
- frontend technical details are optional and hidden by default;
- backups are verified before scoped v1 retirement;
- no unrelated database data was lost; if a restore occurred, the user seeder and admin login were reverified;
- code review finds no dead v1 reader, silent cap, unscoped query, dead UI action, or prompt-cache-breaking sync behavior;
- backend and local-agent branches are merged to main and pushed only after the user-approved implementation plan is executed and final verification passes.
