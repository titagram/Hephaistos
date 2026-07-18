# Hades Graph Lifecycle v2 Contract and Agent Indexer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Hades Agent produce one deterministic, language-neutral, privacy-safe v2 graph artifact with bounded lifecycle flows and resumable chunk transport.

**Architecture:** Closed JSON Schemas and golden vectors define the wire contract. Frozen extraction IR separates language/framework parsing from canonical graph construction. One builder assigns stable IDs, one traversal computes bounded flow membership, one pruner removes whole semantic units under configured budgets, and one bundler emits referentially complete chunks.

**Tech Stack:** Python 3.11+, pytest, dataclasses/enums, `jsonschema`, RFC 8785 JCS, SHA-256, tree-sitter 0.26.0, exact official JavaScript/TypeScript/PHP/Python grammar wheels, gzip.

## Global Constraints

- Inherit every global constraint from `2026-07-16-graph-lifecycle-v2-master.md`.
- Work only in `/Users/gabriele/Dev/Hephaistos` on `codex/graph-lifecycle-v2-agent`.
- Read design sections 6–8 and 12 before Task 1; treat their scalar rules and closed unions as normative.
- `hermes_cli/hades_graph_contract.py` remains an import facade but exports v2 only.
- All public artifact objects include every declared property; nullable fields use explicit `null`/`None`.
- Every same-ID/different-value collision is fatal.
- No hard entity-count truncation may survive outside `GraphBudgetPruner`.
- Adapter failures are typed coverage events; they are never swallowed or treated as successful full extraction.

---

## File Structure

### Contract package

- `contracts/hades/graph-v2/*.schema.json`: eight closed JSON Schemas.
- `contracts/hades/graph-v2/golden/*.json`: canonicalization, dashboard protocol, and verification vectors.
- `contracts/hades/graph-v2/manifest.json`: sorted filename/digest manifest.
- `contracts/hades/graph-v2/contract-lock.json`: commit and manifest lock consumed by later plans.
- `hermes_cli/hades_graph_v2/model.py`: frozen public artifact dataclasses/enums.
- `hermes_cli/hades_graph_v2/schema.py`: constants, schema loading, serialization/deserialization.
- `hermes_cli/hades_graph_v2/identity.py`: NFC/JCS, IDs, digests, fingerprints.
- `hermes_cli/hades_graph_v2/canonicalize.py`: stable sort/dedupe/redaction/collision detection.
- `hermes_cli/hades_graph_v2/coverage.py`: completeness/count-knowledge ledgers.
- `hermes_cli/hades_graph_v2/validation.py`: privacy, size, reference, flow, and artifact invariants.
- `hermes_cli/hades_graph_v2/pruning.py`: atomic-unit budget selection only.
- `hermes_cli/hades_graph_v2/bundle.py`: deterministic chunks, spool, resume readback.
- `hermes_cli/hades_graph_config.py`: immutable `hades.graph_index` config reader.
- `hermes_cli/hades_graph_contract.py`: v2-only compatibility import facade.

### Extraction and lifecycle

- `hermes_cli/hades_index/lifecycle/model.py`: frozen language-neutral adapter IR.
- `hermes_cli/hades_index/lifecycle/control_flow.py`: canonical CFG conversion.
- `hermes_cli/hades_index/lifecycle/interprocedural.py`: exact/candidate/unresolved call resolution.
- `hermes_cli/hades_index/lifecycle/entrypoints.py`: entrypoint normalization.
- `hermes_cli/hades_index/lifecycle/traversal.py`: finite flow membership/stage assignment.
- `hermes_cli/hades_index/lifecycle/builder.py`: sole IR-to-artifact construction authority.
- `hermes_cli/hades_index/lifecycle/frameworks/*.py`: Laravel, Symfony, Django, FastAPI, Express, Next.js semantics.
- Existing `hades_index` modules: parsing, aggregation, inventory, and adapter entrypoints.

### Tests

- `tests/hermes_cli/test_hades_graph_contract.py`: schemas, IDs, references, privacy, provenance.
- `tests/hermes_cli/test_hades_graph_v2_golden.py`: exact golden bytes/digests.
- `tests/hermes_cli/test_hades_lifecycle_ir.py`: every IR variant and invalid combination.
- `tests/hermes_cli/test_hades_lifecycle_control_flow.py`: branches/loops/exceptions/async.
- `tests/hermes_cli/test_hades_lifecycle_framework_adapter.py` and `test_hades_lifecycle_{symfony,laravel,django,fastapi,express,nextjs}.py`: adapter boundary plus framework pipelines/entrypoints.
- `tests/hermes_cli/test_hades_lifecycle_traversal.py`: frontier rule, stages, linked async.
- `tests/hermes_cli/test_hades_graph_budget_pruner.py`: atomic budget behavior.
- `tests/hermes_cli/test_hades_graph_bundle.py`: chunks, manifest, gzip, resume.
- `tests/hermes_cli/test_hades_backend_indexer_golden.py`: language/polyglot fixtures.

---

### Task 1: Create Closed Root Schemas and Golden Fixtures

**Files:**
- Create: `contracts/hades/graph-v2/artifact.schema.json`
- Create: `contracts/hades/graph-v2/bundle.schema.json`
- Create: `contracts/hades/graph-v2/chunk.schema.json`
- Create: `contracts/hades/graph-v2/dashboard-query.schema.json`
- Create: `contracts/hades/graph-v2/dashboard-response.schema.json`
- Create: `contracts/hades/graph-v2/verification-work.schema.json`
- Create: `contracts/hades/graph-v2/verification-result.schema.json`
- Create: `contracts/hades/graph-v2/graph-overlay.schema.json`
- Create: `contracts/hades/graph-v2/golden/canonicalization.json`
- Create: `contracts/hades/graph-v2/golden/dashboard-protocol.json`
- Create: `contracts/hades/graph-v2/golden/verification-results.json`
- Create: `contracts/hades/graph-v2/manifest.json`
- Test: `tests/hermes_cli/test_hades_graph_v2_golden.py`

**Interfaces:**
- Produces eight JSON Schema 2020-12 documents with recursive `additionalProperties: false`.
- Produces `manifest.json` as `{schema, files:[{path,sha256}]}` sorted by `path`.
- Produces complete preimages plus exact canonical UTF-8 hex and lower-case SHA-256 outputs.

- [ ] **Step 1: Add the failing schema inventory test**

```python
SCHEMAS = (
    "artifact.schema.json", "bundle.schema.json", "chunk.schema.json",
    "dashboard-query.schema.json", "dashboard-response.schema.json",
    "verification-work.schema.json", "verification-result.schema.json",
    "graph-overlay.schema.json",
)

def test_graph_v2_contract_inventory_is_closed_and_manifested():
    root = Path("contracts/hades/graph-v2")
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["schema"] == "hades.graph_v2_contract_manifest.v1"
    assert [row["path"] for row in manifest["files"]] == sorted(SCHEMAS)
    for name in SCHEMAS:
        document = json.loads((root / name).read_text())
        Draft202012Validator.check_schema(document)
        assert _all_object_schemas_are_closed(document)
        digest = hashlib.sha256((root / name).read_bytes()).hexdigest()
        assert next(row["sha256"] for row in manifest["files"] if row["path"] == name) == digest
```

- [ ] **Step 2: Run RED**

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_graph_v2_golden.py::test_graph_v2_contract_inventory_is_closed_and_manifested -q`

Expected: FAIL because the v2 contract directory and manifest do not exist.

- [ ] **Step 3: Create the schemas from the approved field tables**

Use this exact root pattern for every schema and define every nested object under `$defs`; do not use an open `metadata` escape hatch:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://home-sweet-home.cloud/contracts/hades/graph-v2/artifact.schema.json",
  "title": "Hades Code Graph Artifact v2",
  "type": "object",
  "additionalProperties": false,
  "required": ["schema", "generated_at", "project", "source", "graph_contract", "frameworks", "languages", "entrypoints", "nodes", "structures", "edges", "flows", "flow_steps", "uncertainties"],
  "properties": {
    "schema": {"const": "hades.code_graph.v2"},
    "generated_at": {"$ref": "#/$defs/utcTimestamp"},
    "project": {"$ref": "#/$defs/project"},
    "source": {"$ref": "#/$defs/source"},
    "graph_contract": {"$ref": "#/$defs/graphContract"},
    "frameworks": {"type": "array", "items": {"$ref": "#/$defs/framework"}},
    "languages": {"type": "array", "items": {"$ref": "#/$defs/language"}},
    "entrypoints": {"type": "array", "items": {"$ref": "#/$defs/entrypoint"}},
    "nodes": {"type": "array", "items": {"$ref": "#/$defs/node"}},
    "structures": {"type": "array", "items": {"$ref": "#/$defs/structure"}},
    "edges": {"type": "array", "items": {"$ref": "#/$defs/edge"}},
    "flows": {"type": "array", "items": {"$ref": "#/$defs/flow"}},
    "flow_steps": {"type": "array", "items": {"$ref": "#/$defs/flowStep"}},
    "uncertainties": {"type": "array", "items": {"$ref": "#/$defs/uncertainty"}}
  },
  "$defs": {
    "digest": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
    "ulid": {"type": "string", "pattern": "^[0-9A-HJKMNP-TV-Z]{26}$"},
    "utcTimestamp": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"},
    "safeInteger": {"type": "integer", "minimum": 0, "maximum": 9007199254740991}
  }
}
```

Complete `$defs` with the exact closed unions and limits in design sections 6.1–6.11. The other seven roots use their exact discriminator and DTO tables from sections 8, 10, and 11.

- [ ] **Step 4: Add adversarial and golden tests**

```python
@pytest.mark.parametrize("mutation", [
    lambda a: {**a, "schema": "hades.code_graph.v1"},
    lambda a: {**a, "unknown": True},
    lambda a: _replace_path(a, "/absolute/private.php"),
    lambda a: _replace_first_integer(a, 1.5),
])
def test_artifact_schema_rejects_non_v2_open_or_unsafe_payloads(valid_artifact, mutation):
    with pytest.raises(ValidationError):
        validate_contract("artifact.schema.json", mutation(valid_artifact))

def test_canonicalization_vectors_have_exact_bytes_and_digests():
    vectors = json.loads(Path("contracts/hades/graph-v2/golden/canonicalization.json").read_text())
    for vector in vectors["vectors"]:
        assert canonical_json_bytes(vector["input"]).hex() == vector["canonical_utf8_hex"]
        assert hashlib.sha256(bytes.fromhex(vector["canonical_utf8_hex"])).hexdigest() == vector["sha256"]
```

- [ ] **Step 5: Run GREEN and schema lint**

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_graph_v2_golden.py -q`

Expected: PASS; manifest paths are sorted and every digest matches exact file bytes.

- [ ] **Step 6: Commit**

```bash
git add contracts/hades/graph-v2 tests/hermes_cli/test_hades_graph_v2_golden.py
git commit -m "feat(hades): freeze graph lifecycle v2 contracts"
```

### Task 2: Implement Canonicalization, Identity, and Schema Facade

**Files:**
- Create: `hermes_cli/hades_graph_v2/__init__.py`
- Create: `hermes_cli/hades_graph_v2/schema.py`
- Create: `hermes_cli/hades_graph_v2/identity.py`
- Create: `hermes_cli/hades_graph_v2/canonicalize.py`
- Modify: `hermes_cli/hades_graph_contract.py`
- Test: `tests/hermes_cli/test_hades_graph_contract.py`
- Test: `tests/hermes_cli/test_hades_graph_v2_golden.py`

**Interfaces:**

```python
def canonical_json_bytes(value: JsonValue) -> bytes: ...
def sha256_jcs(value: JsonValue) -> str: ...
def node_id(identity: NodeIdentity) -> str: ...
def edge_id(identity: EdgeIdentity) -> str: ...
def flow_id(entrypoint_id: str, root_node_id: str, kind: FlowKind) -> str: ...
def flow_step_id(flow_id: str, edge_id: str, stage_from: Stage, stage_to: Stage, async_context: AsyncContext) -> str: ...
def artifact_graph_version(artifact: GraphArtifactV2) -> str: ...
def projection_version(artifact_digest: str, verification_set_hash: str) -> str: ...
def validate_schema(document_name: str, payload: JsonValue) -> None: ...
```

- [ ] **Step 1: Add failing stability/collision/privacy tests**

```python
def test_named_node_id_survives_unrelated_line_insertion(named_node):
    moved = replace(named_node, location=replace(named_node.location, start_line=900, end_line=905))
    assert node_id(named_node.identity) == node_id(moved.identity)

def test_same_id_different_value_is_fatal():
    with pytest.raises(GraphIdentityCollision, match="same public ID has different canonical values"):
        canonicalize_records([_node("same", label="A"), _node("same", label="B")])

def test_projection_version_hashes_exact_ascii_preimage():
    assert projection_version("a" * 64, "b" * 64) == hashlib.sha256(("a" * 64 + ":" + "b" * 64).encode("ascii")).hexdigest()
```

- [ ] **Step 2: Run RED**

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_graph_contract.py -k 'line_insertion or collision or projection_version' -q`

Expected: FAIL because v2 helpers do not exist and the old facade still exposes v1 behavior.

- [ ] **Step 3: Implement exact normalization and digest helpers**

```python
NODE_PREFIX = "hades:node:v2:"
EDGE_PREFIX = "hades:edge:v2:"
FLOW_PREFIX = "hades:flow:v2:"
FLOW_STEP_PREFIX = "hades:flow-step:v2:"

def projection_version(artifact_digest: str, verification_set_hash: str) -> str:
    _require_digest(artifact_digest)
    _require_digest(verification_set_hash)
    return hashlib.sha256(f"{artifact_digest}:{verification_set_hash}".encode("ascii")).hexdigest()

def prefixed_id(prefix: str, identity: JsonValue) -> str:
    return prefix + sha256_jcs(normalize_contract_value(identity))
```

Use a real RFC 8785 implementation or a locally tested equivalent; reject floats, unsafe integers, isolated surrogates, non-string object keys, non-NFC output, and non-UTC timestamps before hashing.

- [ ] **Step 4: Make the old facade v2-only**

`hermes_cli/hades_graph_contract.py` imports and re-exports only v2 names. Delete v1 schema constants, collection aliases, route/test promotion, legacy adapter selection, and hard caps. A v1 artifact passed to any facade validator raises `GraphContractError(code="graph_v1_not_supported")`.

- [ ] **Step 5: Run GREEN and permutation property tests**

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_graph_contract.py tests/hermes_cli/test_hades_graph_v2_golden.py -q`

Expected: PASS including NFC, path-normalization, line-insertion, permutation, and duplicate-record vectors.

- [ ] **Step 6: Write the contract lock and commit**

Generate `contracts/hades/graph-v2/contract-lock.json` from the current manifest. Set `schema_source_commit` to the Task 1 commit, which is the already-existing commit that last changed schema/golden/manifest bytes. Do not amend this commit to insert its own SHA; that would create a self-reference with no stable value.

```bash
git add hermes_cli/hades_graph_v2 hermes_cli/hades_graph_contract.py contracts/hades/graph-v2/contract-lock.json tests/hermes_cli/test_hades_graph_contract.py tests/hermes_cli/test_hades_graph_v2_golden.py
git commit -m "feat(hades): implement graph v2 canonical identity"
```

### Task 3: Implement Frozen Artifact Models, Coverage, and Validation

**Files:**
- Create: `hermes_cli/hades_graph_v2/model.py`
- Create: `hermes_cli/hades_graph_v2/coverage.py`
- Create: `hermes_cli/hades_graph_v2/validation.py`
- Test: `tests/hermes_cli/test_hades_graph_contract.py`

**Interfaces:**

```python
class Knowledge(str, Enum):
    ABSENCE_VERIFIED = "absence_verified"
    LOWER_BOUND = "lower_bound"
    UNKNOWN = "unknown"

@dataclass(frozen=True, slots=True)
class CountKnowledge:
    represented: int
    exact_value: int | None
    knowledge: Knowledge

@dataclass(frozen=True, slots=True)
class GraphArtifactV2:
    schema: Literal["hades.code_graph.v2"]
    generated_at: str
    project: ProjectIdentity
    source: SourceIdentity
    graph_contract: GraphContractMetadata
    frameworks: tuple[FrameworkRecord, ...]
    languages: tuple[LanguageRecord, ...]
    entrypoints: tuple[Entrypoint, ...]
    nodes: tuple[Node, ...]
    structures: tuple[Structure, ...]
    edges: tuple[Edge, ...]
    flows: tuple[Flow, ...]
    flow_steps: tuple[FlowStep, ...]
    uncertainties: tuple[Uncertainty, ...]
```

- [ ] **Step 1: Add failing zero/unknown, provenance, and reference tests**

```python
def test_partial_family_never_returns_verified_zero():
    count = count_knowledge(represented=0, omitted=2, capability_status="partial")
    assert count == CountKnowledge(represented=0, exact_value=None, knowledge=Knowledge.UNKNOWN)

def test_base_artifact_rejects_agent_verified_evidence(valid_artifact):
    payload = replace_first_evidence_origin(valid_artifact, "agent_verified")
    with pytest.raises(GraphValidationError, match="base artifact evidence origin"):
        validate_artifact(payload)

def test_unresolved_target_requires_exact_uncertainty_subject(valid_artifact):
    broken = remove_subject_uncertainty(valid_artifact)
    with pytest.raises(GraphValidationError, match="uncertainty ownership"):
        validate_artifact(broken)
```

- [ ] **Step 2: Run RED**

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_graph_contract.py -k 'partial_family or agent_verified or uncertainty_subject' -q`

- [ ] **Step 3: Implement closed dataclasses/enums**

Define every field and closed union exactly as sections 6.5–6.11. Use `@dataclass(frozen=True, slots=True)`, tuples for public arrays, enums for closed values, and discriminator subclasses for identity/occurrence/subject unions. No model has `dict[str, Any]` as an extension payload.

- [ ] **Step 4: Implement coverage and validation passes**

Validation order is fixed:

```python
def validate_artifact(artifact: GraphArtifactV2) -> None:
    validate_scalar_and_privacy_rules(artifact)
    validate_sorted_unique_records(artifact)
    index = build_record_index(artifact)
    validate_identity_recomputation(artifact, index)
    validate_references(artifact, index)
    validate_uncertainty_ownership(artifact, index)
    validate_flow_membership(artifact, index)
    validate_coverage_and_counts(artifact)
    validate_artifact_digest(artifact)
```

The record index is local to this artifact. Do not resolve by schema, repository, active cache, or another artifact.

- [ ] **Step 5: Run GREEN**

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_graph_contract.py -q`

Expected: PASS; an intentionally dangling locator fails with a deterministic safe error code.

- [ ] **Step 6: Commit**

```bash
git add hermes_cli/hades_graph_v2/model.py hermes_cli/hades_graph_v2/coverage.py hermes_cli/hades_graph_v2/validation.py tests/hermes_cli/test_hades_graph_contract.py
git commit -m "feat(hades): validate graph v2 artifact invariants"
```

### Task 4: Add Typed Graph Configuration and Source Snapshot Identity

**Files:**
- Create: `hermes_cli/hades_graph_config.py`
- Modify: `hermes_cli/config.py`
- Modify: `cli-config.yaml`
- Modify: `hermes_cli/hades_index/inventory.py`
- Test: `tests/hermes_cli/test_hades_graph_config.py`
- Test: `tests/hermes_cli/test_hades_backend_jobs.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class HadesGraphIndexConfig:
    excluded_paths: tuple[str, ...]
    max_file_bytes: int
    max_total_source_bytes: int
    max_wall_seconds: int
    max_bundle_uncompressed_bytes: int
    max_chunk_uncompressed_bytes: int
    spool_ttl_seconds: int
    graphify_candidates: bool

def load_hades_graph_index_config(config: Mapping[str, object]) -> HadesGraphIndexConfig: ...
def build_source_identity(root: Path, config: HadesGraphIndexConfig) -> SourceIdentity: ...
```

- [ ] **Step 1: Add failing config/source tests**

Test compulsory secret exclusions, unknown-key error at explicit index boundary, `graphify_candidates` default false and strict boolean validation, NFC/NFD path collision, in-workspace symlink, escaping symlink, unavailable submodule, dirty worktree, non-Git workspace, and before/after extraction digest mismatch.

- [ ] **Step 2: Run RED**

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_graph_config.py tests/hermes_cli/test_hades_backend_jobs.py -k 'source_identity or graph_index_config' -q`

- [ ] **Step 3: Implement config and streaming inventory hash**

Use these exact defaults and ranges: `max_file_bytes=8388608` (1 KiB–1 GiB), `max_total_source_bytes=2147483648` (1 MiB–16 TiB), `max_wall_seconds=3600` (30–86,400), `max_chunk_uncompressed_bytes=8388608` (64 KiB–8 MiB), `max_bundle_uncompressed_bytes=536870912` (8 MiB–4 GiB), `spool_ttl_seconds=86400` (3,600–604,800), `graphify_candidates=false`, and `excluded_paths=[]`; chunk must not exceed bundle. Use the compiled exclusion baseline from section 6.3 plus user additions. For each sorted in-scope record hash `path_utf8 + b"\0" + file_sha256_ascii + b"\n"`; hash invalid symlink markers without exposing target text. Recompute after extraction and raise `source_changed_during_index` on mismatch.

- [ ] **Step 4: Run GREEN and commit**

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_graph_config.py tests/hermes_cli/test_hades_backend_jobs.py -k 'source_identity or graph_index_config' -q`

```bash
git add hermes_cli/hades_graph_config.py hermes_cli/config.py cli-config.yaml hermes_cli/hades_index/inventory.py tests/hermes_cli/test_hades_graph_config.py tests/hermes_cli/test_hades_backend_jobs.py
git commit -m "feat(hades): add graph v2 source identity and config"
```

### Task 5: Create and Validate the Language-Neutral Extraction IR

**Files:**
- Create: `hermes_cli/hades_index/lifecycle/__init__.py`
- Create: `hermes_cli/hades_index/lifecycle/model.py`
- Modify: `hermes_cli/hades_index/base.py`
- Test: `tests/hermes_cli/test_hades_lifecycle_ir.py`

**Interfaces:**
- `index(context: ExtractionContext, files: Sequence[Path]) -> AdapterResult` is the only adapter entrypoint.
- Every IR dataclass and union is the exact one in design section 12.2.
- Every local key is `SHA256(JCS({language,path,record_family,locator_kind,structural_path_or_pointer,ordinal}))`.

- [ ] **Step 1: Add failing construction tests for every IR variant**

```python
@pytest.mark.parametrize("successor", [
    AlwaysSuccessor(target_block_key="b2", order=0),
    BranchSuccessor(target_block_key="b2", branch_arm_key="arm", order=0),
    ExceptionSuccessor(target_block_key="catch", exception_scope_key="scope", caught_type_name="RuntimeError", order=0),
    LoopSuccessor(target_block_key="loop", loop_role="back", order=0),
    AsyncSuccessor(target_local_key="job", dispatch_kind="job", order=0),
    ReturnSuccessor(terminal_local_key="return", order=0),
])
def test_successor_union_round_trips(successor):
    assert successor_from_json(successor_to_json(successor)) == successor
```

Add rejection tests for invalid discriminators, nullable fields outside the table, unresolved references, unsorted parameters/modifiers/successors, handler/unresolved XOR violation, and an edge reference without `StructureIR`.

- [ ] **Step 2: Run RED**

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_lifecycle_ir.py -q`

- [ ] **Step 3: Implement frozen IR and `AdapterResult.validate()`**

Use the dataclass fields and enums verbatim from design section 12.2. `AdapterResult.validate()` builds typed local-key indexes, checks every reference in one result, validates evidence locators, and returns `None`; it never mutates or canonicalizes records.

- [ ] **Step 4: Run GREEN and commit**

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_lifecycle_ir.py -q`

```bash
git add hermes_cli/hades_index/lifecycle hermes_cli/hades_index/base.py tests/hermes_cli/test_hades_lifecycle_ir.py
git commit -m "feat(hades): define lifecycle extraction IR"
```

### Task 6: Parse Control Flow and Resolve Interprocedural Targets

**Files:**
- Create: `hermes_cli/hades_index/lifecycle/control_flow.py`
- Create: `hermes_cli/hades_index/lifecycle/interprocedural.py`
- Create: `hermes_cli/hades_index/graphify_candidates.py`
- Modify: `hermes_cli/hades_index/tree_sitter_adapter.py`
- Modify: `hermes_cli/hades_index/resolution.py`
- Test: `tests/hermes_cli/test_hades_lifecycle_control_flow.py`

**Interfaces:**

```python
def build_control_flow(result: AdapterResult) -> ControlFlowResult: ...
def resolve_call_sites(results: Sequence[AdapterResult]) -> ResolutionResult: ...
def attach_graphify_hints(result: AdapterResult, candidates: Mapping[str, Sequence[str]], *, enabled: bool) -> AdapterResult: ...

@dataclass(frozen=True, slots=True)
class ParseResult:
    status: Literal["parsed", "failed"]
    syntax: SyntaxIR | None
    failure: ParseFailure | None
```

- [ ] **Step 1: Add golden RED fixtures**

Create PHP/Python/TypeScript fixtures for `if/else`, switch/match, early return, try/catch/finally, loop/back edge, recursion, await/promise, exact call, multi-candidate dynamic call, and unresolved reflection/eval. Assert finite nodes/edges and explicit merge/terminal/boundary records. Add Graphify tests proving default disabled, maximum 20 existing node targets, native unresolved subject required, `candidate_set_knowledge=incomplete`, `graphify.` evidence prefix, no full completeness, and no direct promotion/certification.

- [ ] **Step 2: Run RED**

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_lifecycle_control_flow.py -q`

- [ ] **Step 3: Implement parse and resolution rules**

Remove the depth-eight BFS and `route_reaches_table` lifecycle shortcut. Missing grammar/analyzer returns `ParseResult(status="failed", ...)` and a partial coverage event. Resolution returns exactly one of exact target, exhaustive candidate edges, or unresolved frontier; it does not guess one candidate. `attach_graphify_hints` runs only after native resolution and only when enabled; it attaches inferred hint edges to an already-existing uncertainty and cannot create a subject.

- [ ] **Step 4: Run GREEN and commit**

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_lifecycle_control_flow.py -q`

```bash
git add hermes_cli/hades_index/lifecycle/control_flow.py hermes_cli/hades_index/lifecycle/interprocedural.py hermes_cli/hades_index/graphify_candidates.py hermes_cli/hades_index/tree_sitter_adapter.py hermes_cli/hades_index/resolution.py tests/hermes_cli/test_hades_lifecycle_control_flow.py
git commit -m "feat(hades): extract bounded control flow and call targets"
```

### Task 7: Freeze the Framework Adapter Interface and Generic Entrypoints

**Files:**
- Create: `hermes_cli/hades_index/lifecycle/entrypoints.py`
- Create: `hermes_cli/hades_index/lifecycle/frameworks/__init__.py`
- Modify: `hermes_cli/hades_index/php.py`
- Modify: `hermes_cli/hades_index/python.py`
- Modify: `hermes_cli/hades_index/typescript.py`
- Modify: `hermes_cli/hades_index/sql.py`
- Test: `tests/hermes_cli/test_hades_lifecycle_framework_adapter.py`

**Interfaces:**

```python
class FrameworkAdapter(Protocol):
    def detect(self, context: ExtractionContext) -> FrameworkDetection: ...
    def entrypoints(self, context: ExtractionContext, syntax: Sequence[SyntaxIR]) -> tuple[EntrypointCandidate, ...]: ...
    def pipeline(self, context: ExtractionContext, candidate: EntrypointCandidate) -> tuple[FrameworkPipelineSegment, ...]: ...
```

- [ ] **Step 1: Add RED protocol/generic-entrypoint tests**

Test adapter registration ordering, duplicate framework rejection, process `main`, CLI command, scheduler, consumer/listener, public API, SQL `control_flow=not_applicable`, all detected adapters run, and unknown-language-to-Python fallback absent.

- [ ] **Step 2: Run RED**

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_lifecycle_framework_adapter.py -q`

- [ ] **Step 3: Implement interface and language delegation**

Language modules emit syntax/semantic IR only and call registered framework adapters. `php.py` receives no new lifecycle block. Generic entrypoints use the same normalized identity path as framework routes.

- [ ] **Step 4: Run GREEN and commit**

```bash
.venv/bin/python -m pytest tests/hermes_cli/test_hades_lifecycle_framework_adapter.py -q
git add hermes_cli/hades_index/lifecycle/entrypoints.py hermes_cli/hades_index/lifecycle/frameworks/__init__.py hermes_cli/hades_index/php.py hermes_cli/hades_index/python.py hermes_cli/hades_index/typescript.py hermes_cli/hades_index/sql.py tests/hermes_cli/test_hades_lifecycle_framework_adapter.py
git commit -m "feat(hades): define framework lifecycle adapter boundary"
```

### Task 8: Implement the Symfony Lifecycle Adapter

**Files:**
- Create: `hermes_cli/hades_index/lifecycle/frameworks/symfony.py`
- Test: `tests/hermes_cli/test_hades_lifecycle_symfony.py`

**Interfaces:**
- Detect Composer/lock version; YAML/XML/PHP routes/imports and attributes/legacy annotations; framework/security/services config; kernel listeners/subscribers; controller/service bindings.
- Order route priority/resource order and listener priority descending then stable service/source order.

- [ ] **Step 1: Add RED Symfony golden fixtures**

Cover YAML+attribute collision, imported prefix/method/name/host/condition, inherited controller action, firewall allow/deny, voter, argument resolver, listener priority, early listener response, controller response, handled and unhandled exception, unresolved computed service/route.

- [ ] **Step 2: Run RED**

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_lifecycle_symfony.py -q`

- [ ] **Step 3: Implement exact Symfony pipeline**

Emit router → request/kernel listeners → firewall/access-control/voters → argument/value resolution → controller → response listeners, plus short-circuit/redirect/exception arms. Unknown framework/version-dependent order creates a boundary/uncertainty, never a guess.

- [ ] **Step 4: Run GREEN and commit**

```bash
.venv/bin/python -m pytest tests/hermes_cli/test_hades_lifecycle_symfony.py -q
git add hermes_cli/hades_index/lifecycle/frameworks/symfony.py tests/hermes_cli/test_hades_lifecycle_symfony.py
git commit -m "feat(hades): extract Symfony request lifecycles"
```

### Task 9: Implement the Laravel Lifecycle Adapter

**Files:**
- Create: `hermes_cli/hades_index/lifecycle/frameworks/laravel.py`
- Test: `tests/hermes_cli/test_hades_lifecycle_laravel.py`

**Interfaces:**
- Detect Composer version, route groups/resources, bootstrap/kernel/providers, middleware aliases/groups/priority, controller middleware, binding/FormRequest/policy/gate/handler/events/jobs/queues/scheduler.

- [ ] **Step 1: Add RED Laravel golden fixtures**

Cover nested groups, resource route, prefix/name/domain/method expansion, middleware alias/group/priority/dedup, binding miss, FormRequest pass/fail, policy allow/deny, redirect/abort, exception renderer, terminating middleware, queued job/event child flow, console/scheduler entrypoint.

- [ ] **Step 2: Run RED**

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_lifecycle_laravel.py -q`

- [ ] **Step 3: Implement exact Laravel pipeline**

Preserve declaration order after group expansion. Effective middleware is global, expanded group, route/controller, then configured priority where applicable. Emit binding → security → validation → handler → terminal/error; dispatch creates linked async flow only.

- [ ] **Step 4: Run GREEN and commit**

```bash
.venv/bin/python -m pytest tests/hermes_cli/test_hades_lifecycle_laravel.py -q
git add hermes_cli/hades_index/lifecycle/frameworks/laravel.py tests/hermes_cli/test_hades_lifecycle_laravel.py
git commit -m "feat(hades): extract Laravel request lifecycles"
```

### Task 10: Implement the Django Lifecycle Adapter

**Files:**
- Create: `hermes_cli/hades_index/lifecycle/frameworks/django.py`
- Test: `tests/hermes_cli/test_hades_lifecycle_django.py`

- [ ] **Step 1: Add RED Django golden fixtures**

Cover nested include/namespace, path versus re_path ordering, accumulated prefixes/converters, function view, CBV GET/POST dispatch, decorator denial, middleware top-down request/bottom-up entered response unwind, middleware short-circuit, sync/async view, handled exception, management command, ASGI/WSGI declaration.

- [ ] **Step 2: Run RED**

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_lifecycle_django.py -q`

- [ ] **Step 3: Implement exact Django order and arms**

Resolve `ROOT_URLCONF` recursively in list order. A short-circuit skips later request middleware but unwinds only entered response middleware. Unknown dynamic URL/settings facts create uncertainty.

- [ ] **Step 4: Run GREEN and commit**

```bash
.venv/bin/python -m pytest tests/hermes_cli/test_hades_lifecycle_django.py -q
git add hermes_cli/hades_index/lifecycle/frameworks/django.py tests/hermes_cli/test_hades_lifecycle_django.py
git commit -m "feat(hades): extract Django request lifecycles"
```

### Task 11: Implement the FastAPI Lifecycle Adapter

**Files:**
- Create: `hermes_cli/hades_index/lifecycle/frameworks/fastapi.py`
- Test: `tests/hermes_cli/test_hades_lifecycle_fastapi.py`

- [ ] **Step 1: Add RED FastAPI golden fixtures**

Cover nested routers/prefixes/methods, app/router/route/decorator dependencies, dependency cache identity, yield cleanup, validation 422, dependency exception, middleware, exception handler, sync/async endpoint, response-model serialization, lifespan/events, background task async child.

- [ ] **Step 2: Run RED**

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_lifecycle_fastapi.py -q`

- [ ] **Step 3: Implement exact FastAPI/Starlette semantics**

Use detected version for middleware order. Version/order not proven becomes a boundary/partial flow. Preserve registration/dependency order and distinguish validation, handler, serialization, cleanup, exception, and background arms.

- [ ] **Step 4: Run GREEN and commit**

```bash
.venv/bin/python -m pytest tests/hermes_cli/test_hades_lifecycle_fastapi.py -q
git add hermes_cli/hades_index/lifecycle/frameworks/fastapi.py tests/hermes_cli/test_hades_lifecycle_fastapi.py
git commit -m "feat(hades): extract FastAPI request lifecycles"
```

### Finite Adapter Acceptance Gate

See the approved [finite adapter acceptance gates design](../specs/2026-07-17-finite-adapter-acceptance-gates-design.md).

Every remaining framework adapter must have a validated corpus.json, matrix.json, and lock.json before production implementation. The task brief records the lock digest. One complete independent review, one repair, and one scoped re-review are permitted. Correctly declared out-of-matrix partial/unresolved behavior is backlog, not a blocker.

### Task 12: Implement the Express Lifecycle Adapter

**Files:**
- Create: `hermes_cli/hades_index/lifecycle/frameworks/express.py`
- Test: `tests/hermes_cli/test_hades_lifecycle_express.py`

- [ ] **Step 1: Add RED Express golden fixtures**

Cover nested router mount, same path multiple verbs, `.all`, ordered `use`/handlers, proven `next()`, `next('route')`, `next(err)`, response/send/end/redirect terminal, thrown error, async rejection, error middleware arity, computed target unresolved.

- [ ] **Step 2: Run RED**

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_lifecycle_express.py -q`

- [ ] **Step 3: Implement registration-order continuation semantics**

Continue only through statically proven `next()`. Preserve mount prefixes/parameters and unrestricted `.all`; do not invent a method or continuation.

- [ ] **Step 4: Run GREEN and commit**

```bash
.venv/bin/python -m pytest tests/hermes_cli/test_hades_lifecycle_express.py -q
git add hermes_cli/hades_index/lifecycle/frameworks/express.py tests/hermes_cli/test_hades_lifecycle_express.py
git commit -m "feat(hades): extract Express request lifecycles"
```

### Task 13: Implement the Next.js Lifecycle Adapter

**Files:**
- Create: `hermes_cli/hades_index/lifecycle/frameworks/nextjs.py`
- Test: `tests/hermes_cli/test_hades_lifecycle_nextjs.py`

- [ ] **Step 1: Add RED Next.js golden fixtures**

Cover App Router GET/POST, Pages API method switch, route groups, dynamic/catch-all pattern, detected-version precedence, middleware matcher/redirect/response/next, static rewrite, unresolved computed config, and explicit exclusion of server/client render graphs as HTTP entrypoints.

- [ ] **Step 2: Run RED**

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_lifecycle_nextjs.py -q`

- [ ] **Step 3: Implement file-system and middleware semantics**

One App Router export is one HTTP method. Pages API is unrestricted unless exhaustive dispatch proves arms. Store normalized public patterns and source files; evaluate only static matcher/rewrite/redirect config.

- [ ] **Step 4: Run GREEN and run the aggregate framework suite**

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_lifecycle_framework_adapter.py tests/hermes_cli/test_hades_lifecycle_symfony.py tests/hermes_cli/test_hades_lifecycle_laravel.py tests/hermes_cli/test_hades_lifecycle_django.py tests/hermes_cli/test_hades_lifecycle_fastapi.py tests/hermes_cli/test_hades_lifecycle_express.py tests/hermes_cli/test_hades_lifecycle_nextjs.py -q`

- [ ] **Step 5: Commit**

```bash
git add hermes_cli/hades_index/lifecycle/frameworks/nextjs.py tests/hermes_cli/test_hades_lifecycle_nextjs.py
git commit -m "feat(hades): extract Next.js request lifecycles"
```

### Task 14: Build Canonical Artifact Objects and Finite Lifecycle Traversal

**Files:**
- Create: `hermes_cli/hades_index/lifecycle/builder.py`
- Create: `hermes_cli/hades_index/lifecycle/traversal.py`
- Modify: `hermes_cli/hades_index/aggregate.py`
- Modify: `hermes_cli/hades_index/__init__.py`
- Test: `tests/hermes_cli/test_hades_lifecycle_traversal.py`
- Test: `tests/hermes_cli/test_hades_backend_indexer_golden.py`

**Interfaces:**

```python
class GraphBuilder:
    def build(self, context: ExtractionContext, results: Sequence[AdapterResult]) -> GraphArtifactV2: ...

def build_callable_summaries(graph: CanonicalTopology) -> Mapping[tuple[str, Stage], CallableSummary]: ...
def build_lifecycle_flows(graph: CanonicalTopology, entrypoints: Sequence[Entrypoint]) -> tuple[tuple[Flow, ...], tuple[FlowStep, ...]]: ...
```

- [ ] **Step 1: Add RED traversal invariants**

Assert one flow per entrypoint, one finite step per unique `(edge,stage_from,stage_to,async_context)`, shortest `min_depth`, explicit branch alternatives, no downstream traversal through uncertain target, matched `returns_to` by `call_site_id`, nearest lexical `exception_scope_id`, linked async child flow excluded after synchronous dispatch, self-recursion and mutual-recursion fixed points, finite loop representation, terminal outcome knowledge, and permutation-invariant output.

- [ ] **Step 2: Run RED**

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_lifecycle_traversal.py -q`

- [ ] **Step 3: Implement builder order and traversal**

Builder order is fixed: validate adapter results; canonicalize nodes; canonicalize structures; canonicalize every `EdgeFactIR`; build uncertainty subjects/candidates; normalize entrypoints; calculate callable summaries; traverse flows; calculate coverage; canonicalize all arrays; validate artifact; calculate artifact digest; replace only the digest field; validate again.

Build the callable call graph, compute strongly connected components, process the condensation DAG in reverse topological order, and iterate each recursive component monotonically until no summary set grows. Summary key is `(callable_id,input_stage)` and contains reachable edge-stage states, normal/exception exits, terminals, effects, async dispatches, and uncertainties. Resume a callee only through the invocation's matching `call_site_id`; propagate exceptions to the nearest matching scope. Traversal queue state is `(node_id, stage, async_context)`. Stop at terminal nodes, unresolved target boundaries, and previously visited state. A dispatch creates/links a child async flow but the parent continues only through the explicit synchronous continuation edge.

- [ ] **Step 4: Run GREEN and commit**

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_lifecycle_traversal.py tests/hermes_cli/test_hades_backend_indexer_golden.py -q`

```bash
git add hermes_cli/hades_index/lifecycle/builder.py hermes_cli/hades_index/lifecycle/traversal.py hermes_cli/hades_index/aggregate.py hermes_cli/hades_index/__init__.py tests/hermes_cli/test_hades_lifecycle_traversal.py tests/hermes_cli/test_hades_backend_indexer_golden.py
git commit -m "feat(hades): build canonical lifecycle flows"
```

### Task 15: Add Atomic Budget Pruning and Deterministic Chunk Bundles

**Files:**
- Create: `hermes_cli/hades_graph_v2/pruning.py`
- Create: `hermes_cli/hades_graph_v2/bundle.py`
- Test: `tests/hermes_cli/test_hades_graph_budget_pruner.py`
- Test: `tests/hermes_cli/test_hades_graph_bundle.py`

**Interfaces:**

```python
class GraphBudgetPruner:
    def select(self, artifact: GraphArtifactV2, limits: BundleLimits) -> GraphArtifactV2: ...

class GraphBundleWriter:
    def write(self, artifact: GraphArtifactV2, spool: Path, limits: BundleLimits) -> BundleManifest: ...
    def resume_state(self, spool: Path) -> BundleResumeState: ...
```

- [ ] **Step 1: Add RED boundary/adversarial tests**

Test a single flow over budget, shared topology between accepted/rejected flows, a smaller later unit that still fits, oversized single record, exact ceiling, >5,000 nodes, >10,000 edges, >500 routes, chunk permutation invariance, no dangling reference, single-member deterministic gzip, trailing bytes, mismatched compressed/uncompressed digest, and resume after uploaded chunk 2.

- [ ] **Step 2: Run RED**

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_graph_budget_pruner.py tests/hermes_cli/test_hades_graph_bundle.py -q`

- [ ] **Step 3: Implement unit selection and chunking**

Select complete entrypoint flow units first, then structural components, then inventory units in the exact order from section 8.2. Recompute ledgers and exact serialized candidate bundle after every unit. Reject the whole unit if it does not fit. After selection remove unreferenced records, record `resource_budget_reached`, recompute version, then emit chunk kinds in descriptor order. Use gzip `mtime=0` and no optional filename/comment/header fields.

- [ ] **Step 4: Run GREEN and commit**

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_graph_budget_pruner.py tests/hermes_cli/test_hades_graph_bundle.py -q`

```bash
git add hermes_cli/hades_graph_v2/pruning.py hermes_cli/hades_graph_v2/bundle.py tests/hermes_cli/test_hades_graph_budget_pruner.py tests/hermes_cli/test_hades_graph_bundle.py
git commit -m "feat(hades): bundle graph v2 without silent truncation"
```

### Task 16: Install and Validate the Required Parser at the Explicit Index Boundary

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `hermes_cli/hades_index/tree_sitter_adapter.py`
- Modify: `hermes_cli/hades_index/resolution.py`
- Modify: `tests/test_project_metadata.py`
- Modify: `tests/hermes_cli/test_hades_index_enrichment.py`
- Modify: `tests/hermes_cli/test_hades_lifecycle_control_flow.py`

**Interfaces:**
- Mandatory base dependencies are exactly `tree-sitter==0.26.0` plus `tree-sitter-javascript==0.25.0`, `tree-sitter-typescript==0.23.2`, `tree-sitter-php==0.24.1`, and `tree-sitter-python==0.25.0`.
- No `hades-indexer` extra or `tools.lazy_deps` group exists.
- `TreeSitterAdapter.require_languages(languages)` performs a real in-memory parse canary for every detected supported language and raises `RequiredParserUnavailable` before graph construction on any failure.
- Once canaries pass, an individual source-file parse failure remains a typed partial coverage event.

- [ ] **Step 1: Add RED metadata and parser-boundary tests**

```python
def test_tree_sitter_is_an_exact_mandatory_dependency(project_metadata):
    expected = {
        "tree-sitter==0.26.0",
        "tree-sitter-javascript==0.25.0",
        "tree-sitter-typescript==0.23.2",
        "tree-sitter-php==0.24.1",
        "tree-sitter-python==0.25.0",
    }
    assert expected <= set(project_metadata.dependencies)
    assert "hades-indexer" not in project_metadata.optional_dependencies

def test_missing_required_parser_blocks_graph_index(tmp_path):
    adapter = TreeSitterAdapter(parser_loader=lambda _language: None)
    with pytest.raises(RequiredParserUnavailable, match="typescript"):
        adapter.require_languages(("typescript",))

def test_one_bad_file_after_canary_is_partial(tmp_path):
    adapter = TreeSitterAdapter(parser_loader=lambda _language: ParserWithSelectiveFailure())
    adapter.require_languages(("typescript",))
    result = adapter.parse_bytes(b"invalid", path="src/bad.ts", language="typescript")
    assert result.failure.code == "parser_failed"
    assert result.coverage_event.outcome is CoverageOutcome.PARTIAL
```

- [ ] **Step 2: Run RED**

Run: `.venv/bin/python -m pytest tests/test_project_metadata.py tests/hermes_cli/test_hades_index_enrichment.py tests/hermes_cli/test_hades_lifecycle_control_flow.py -q`

- [ ] **Step 3: Add pins, one package loader, canaries, fail-fast boundary, refresh lock, run GREEN, commit**

`_load_parser()` imports only `tree_sitter` and the exact language-specific grammar module; remove compatibility probing of `tree_sitter_language_pack` and `tree_sitter_languages` so a runtime download cache or stale binding cannot silently win. `require_languages()` uses fixed safe snippets for JavaScript, TypeScript, PHP, and Python and includes only language names in its exception. `enrich_graph_for_workspace()` computes detected supported languages first and calls the canary before it emits or merges graph facts. It does not provide a `tree_sitter=false` bypass.

Run: `uv lock`

Run: `.venv/bin/python -m pytest tests/test_project_metadata.py tests/hermes_cli/test_hades_index_enrichment.py tests/hermes_cli/test_hades_lifecycle_control_flow.py -q`

```bash
git add pyproject.toml uv.lock hermes_cli/hades_index/tree_sitter_adapter.py hermes_cli/hades_index/resolution.py tests/test_project_metadata.py tests/hermes_cli/test_hades_index_enrichment.py tests/hermes_cli/test_hades_lifecycle_control_flow.py
git commit -m "build(hades): pin lifecycle parser dependencies"
```

### Task 17: Integrate v2 Jobs, Upload Client, Sync, Cache, and Benchmark

**Files:**
- Modify: `hermes_cli/hades_backend_jobs.py`
- Modify: `hermes_cli/hades_backend_sync.py`
- Modify: `hermes_cli/hades_backend_client.py`
- Modify: `hermes_cli/hades_backend_benchmark.py`
- Modify: `hermes_cli/hades_source_slice_policy.py`
- Modify: `hermes_cli/gnothi/collectors/source.py`
- Modify: `plugins/memory/hades_backend/__init__.py`
- Test: `tests/hermes_cli/test_hades_backend_jobs.py`
- Test: `tests/hermes_cli/test_hades_backend_sync_runner.py`
- Test: `tests/hermes_cli/test_hades_backend_client.py`
- Test: `tests/agent/test_hades_backend_memory_provider_sync.py`

**Interfaces:**

```python
class HadesBackendClient:
    def create_graph_import(self, manifest: dict[str, object]) -> GraphImportState: ...
    def upload_graph_chunk(self, import_id: str, index: int, body: BinaryIO, headers: ChunkHeaders) -> GraphChunkState: ...
    def complete_graph_import(self, import_id: str, artifact_graph_version: str) -> GraphImportState: ...
    def graph_import(self, import_id: str) -> GraphImportState: ...
```

- [ ] **Step 1: Add RED upload/resume/cache tests**

Test create-200/201 idempotency, resume missing chunks only, chunk conflict, complete-to-validating, poll until validated/ready, no mutation after digest, v1 rejection, exact active artifact cache identity, verification-summary-only sync, and provider vector-candidate-to-v2-topology resolution.

- [ ] **Step 2: Run RED**

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_backend_jobs.py tests/hermes_cli/test_hades_backend_sync_runner.py tests/hermes_cli/test_hades_backend_client.py tests/agent/test_hades_backend_memory_provider_sync.py -k 'graph_v2 or graph_import or verification_summary' -q`

- [ ] **Step 3: Implement v2-only pipeline**

Jobs call `GraphBuilder`, `GraphBudgetPruner`, and `GraphBundleWriter` before network I/O. Sync persists spool/resume metadata, uploads missing chunks, calls complete, and marks current only after backend readiness. Remove v1 graph schemas from `GRAPH_ARTIFACT_SCHEMAS`. Select local cache by project + binding + source identity + artifact version, never schema alone. Source-slice priority is entrypoint root, middleware/security/input, branch/unresolved, domain/data/integration, tests.

- [ ] **Step 4: Extend benchmark and run GREEN**

Benchmark fixture produces at least 5,501 nodes, 10,501 edges, and 501 routes and asserts either complete chunked delivery or explicit counted omissions.

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_backend_jobs.py tests/hermes_cli/test_hades_backend_sync_runner.py tests/hermes_cli/test_hades_backend_client.py tests/agent/test_hades_backend_memory_provider_sync.py -q`

- [ ] **Step 5: Commit**

```bash
git add hermes_cli/hades_backend_jobs.py hermes_cli/hades_backend_sync.py hermes_cli/hades_backend_client.py hermes_cli/hades_backend_benchmark.py hermes_cli/hades_source_slice_policy.py hermes_cli/gnothi/collectors/source.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/hermes_cli/test_hades_backend_sync_runner.py tests/hermes_cli/test_hades_backend_client.py tests/agent/test_hades_backend_memory_provider_sync.py
git commit -m "feat(hades): upload and sync graph lifecycle v2"
```

### Task 18: Close Producer Gates and the Python Side of G13

**Files:**
- Modify: `tests/hermes_cli/test_hades_graph_contract.py`
- Modify: `tests/hermes_cli/test_hades_graph_v2_golden.py`
- Modify: `tests/hermes_cli/test_hades_lifecycle_control_flow.py`
- Modify: all seven framework adapter test files created in Tasks 7–13
- Modify: `tests/hermes_cli/test_hades_lifecycle_traversal.py`
- Modify: `tests/hermes_cli/test_hades_graph_budget_pruner.py`
- Modify: `tests/hermes_cli/test_hades_graph_bundle.py`
- Modify: `tests/hermes_cli/test_hades_backend_indexer_golden.py`
- Create: `tests/fixtures/hades/graph_v2/` golden fixture tree

**Interfaces:**
- Produces a machine-readable gate report at `.codex-artifacts/graph-v2/agent-gates.json` with `{gate,command,passed,duration_seconds}` records.

- [ ] **Step 1: Map every gate to an exact test node**

```json
{
  "G01": "tests/hermes_cli/test_hades_graph_contract.py::test_v2_only",
  "G02": "tests/hermes_cli/test_hades_graph_v2_golden.py::test_identity_vectors",
  "G03": "tests/hermes_cli/test_hades_graph_contract.py::test_reference_resolution",
  "G04": "tests/hermes_cli/test_hades_graph_contract.py::test_privacy_rejection",
  "G05": "tests/hermes_cli/test_hades_graph_contract.py::test_evidence_flow_completeness_orthogonal",
  "G06": "tests/hermes_cli/test_hades_graph_contract.py::test_omission_ledgers",
  "G07": "tests/hermes_cli/test_hades_graph_bundle.py::test_large_bundle",
  "G08": "tests/hermes_cli/test_hades_lifecycle_control_flow.py::test_cfg_matrix",
  "G09": "tests/hermes_cli/test_hades_lifecycle_framework_adapter.py::test_all_required_framework_golden_suites_are_registered",
  "G10": "tests/hermes_cli/test_hades_lifecycle_traversal.py::test_async_terminal_semantics",
  "G11": "tests/hermes_cli/test_hades_backend_indexer_golden.py::test_polyglot",
  "G12": "tests/hermes_cli/test_hades_lifecycle_control_flow.py::test_missing_parser_partial",
  "G13": "tests/hermes_cli/test_hades_graph_v2_golden.py::test_python_vectors_match_locked_contract",
  "G14": "tests/hermes_cli/test_hades_graph_contract.py::test_base_provenance_and_candidate_ownership"
}
```

- [ ] **Step 2: Run targeted gate suite**

Run:

```bash
.venv/bin/python -m pytest \
  tests/hermes_cli/test_hades_graph_contract.py \
  tests/hermes_cli/test_hades_graph_v2_golden.py \
  tests/hermes_cli/test_hades_lifecycle_ir.py \
  tests/hermes_cli/test_hades_lifecycle_control_flow.py \
  tests/hermes_cli/test_hades_lifecycle_framework_adapter.py \
  tests/hermes_cli/test_hades_lifecycle_symfony.py \
  tests/hermes_cli/test_hades_lifecycle_laravel.py \
  tests/hermes_cli/test_hades_lifecycle_django.py \
  tests/hermes_cli/test_hades_lifecycle_fastapi.py \
  tests/hermes_cli/test_hades_lifecycle_express.py \
  tests/hermes_cli/test_hades_lifecycle_nextjs.py \
  tests/hermes_cli/test_hades_lifecycle_traversal.py \
  tests/hermes_cli/test_hades_graph_budget_pruner.py \
  tests/hermes_cli/test_hades_graph_bundle.py \
  tests/hermes_cli/test_hades_backend_indexer_golden.py -q
```

Expected: PASS with zero xfail/skip for G01–G12, G14, and the Python/golden portion of G13. PHP and TypeScript byte compatibility are intentionally closed by Plans 2 and 4 after those runtimes exist.

- [ ] **Step 3: Run agent regression suite**

Run: `.venv/bin/python -m pytest tests/hermes_cli/test_hades_*.py tests/agent/test_hades_backend_*.py -q`

Expected: PASS. If unrelated pre-existing failures exist, record exact node IDs and prove they reproduce on the branch parent before proceeding.

- [ ] **Step 4: Review and commit fixture/report changes**

```bash
git diff --check
git add tests/fixtures/hades/graph_v2 tests/hermes_cli .codex-artifacts/graph-v2/agent-gates.json
git commit -m "test(hades): close graph v2 producer gates"
```

## Plan 1 Exit Gate

- G01–G12 and G14 are green; Python emits every G13 golden byte/digest exactly.
- `git grep -nE 'hades\.(php_graph|code_graph)\.v1|graph_artifact\.v1' -- hermes_cli plugins/memory/hades_backend` returns no active v2-pipeline reader or fallback.
- Contract manifest, contract lock, and golden vectors are committed.
- The large benchmark has no silent cap.
- A clean v2 bundle validates after round-trip spool readback.
- Fresh spec-compliance review contains no unresolved P0/P1 finding.
