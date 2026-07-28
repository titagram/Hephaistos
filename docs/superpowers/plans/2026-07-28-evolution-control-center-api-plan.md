# Evolution Control Center Service and API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose bounded, consistent, authenticated dashboard contracts over the real local Gnothi and Autopoiesis services.

**Architecture:** A new framework-independent `EvolutionDashboardService` owns reads, optimistic concurrency, sanitization, and calls into existing domain services. A small persistent local job registry runs rebuilds, scans, and diffs. The bundled plugin's FastAPI router validates HTTP shapes and delegates; it never constructs CLI strings or remote backend clients.

**Tech Stack:** Python 3, dataclasses/TypedDict, FastAPI/Pydantic, SQLite, threaded local jobs, pytest/TestClient.

## Global Constraints

- Complete the foundation plan first.
- `EvolutionDashboardService` defaults to `hermes_constants.get_organism_home()` and accepts an explicit root only for tests.
- Read methods use `probe_organism_identity()` and direct safe path probes. They must not instantiate constructors that call `ensure_organism_directories()` when the root is absent.
- Every returned collection has a fixed maximum and a truthful `truncated` flag.
- Every mutation validates organism identity and an expected digest under the lifecycle lock.
- Redact unexpected exception text to stable public reason codes.
- No route imports or constructs a Hades backend client.
- Do not add a core model tool.

---

### Task 1: Define and test the non-mutating snapshot contract

**Files:**

- Create: `hermes_cli/evolution/dashboard_service.py`
- Create: `tests/hermes_cli/evolution/test_dashboard_service.py`

- [ ] **Step 1: Write the absent-root snapshot test**

```python
def test_snapshot_missing_is_bounded_and_non_mutating(tmp_path):
    root = tmp_path / "organism"
    result = EvolutionDashboardService(root).snapshot()

    assert result["state"] == "missing"
    assert result["organism"] is None
    assert result["gnothi"]["state"] == "missing"
    assert result["telos"]["state"] == "missing"
    assert result["observer"]["state"] == "not_ready"
    assert len(result["snapshot_digest"]) == 64
    assert not root.exists()
```

Add tests for corrupt identity, missing Gnothi pointer, partial coverage, absent ledger, and coherent initialized state.

- [ ] **Step 2: Define stable public result types**

Use `TypedDict` interfaces for the service boundary:

```python
class PublicOrganism(TypedDict):
    id_prefix: str
    lineage_prefix: str


class EvolutionSnapshot(TypedDict):
    schema_version: int
    state: Literal["missing", "ready", "partial", "stale", "blocked", "corrupt"]
    observed_at: str
    snapshot_digest: str
    organism: PublicOrganism | None
    gnothi: dict[str, Any]
    telos: dict[str, Any]
    observer: dict[str, Any]
    generations: dict[str, Any]
    pipeline: dict[str, Any]
    diagnostics: list[str]
```

The snapshot body before `observed_at` is canonicalized with sorted compact JSON. Hash that body to produce `snapshot_digest`; do not include `observed_at` in the digest.

- [ ] **Step 3: Implement read-only path probes**

Add private helpers that:

- call `probe_organism_identity(root)`;
- instantiate `OrganismRevisionStore(root / "gnothi_seauton")` only after `root.is_dir()`;
- read Telos pointer/revisions directly before constructing `TelosStore`;
- call `evolution_state_kind(root / "evolution")` and `_status`-equivalent read logic without initialization;
- query SQLite only if `evolution.db` is a safe regular file.

Return full organism UUID nowhere in the snapshot; use `organism_id[:8]` and `lineage_root_digest[:12]`.

- [ ] **Step 4: Implement one coherent snapshot read**

When lifecycle DB reads are needed, wrap them with `read_evolution_snapshot()`. Read the Gnothi pointer once and derive coverage/counts from that artifact. State precedence is:

```python
STATE_PRIORITY = {
    "corrupt": 5,
    "blocked": 4,
    "partial": 3,
    "stale": 2,
    "missing": 1,
    "ready": 0,
}
```

Never turn an exception into healthy/empty data. Map known failures to stable diagnostics such as `identity_corrupt`, `gnothi_pointer_invalid`, `lifecycle_unavailable`, and `event_chain_invalid`.

- [ ] **Step 5: Run tests**

```bash
./scripts/run_tests.sh tests/hermes_cli/evolution/test_dashboard_service.py -q
```

- [ ] **Step 6: Commit**

```bash
git add hermes_cli/evolution/dashboard_service.py tests/hermes_cli/evolution/test_dashboard_service.py
git commit -m "feat(evolution): add bounded dashboard snapshot service"
```

---

### Task 2: Add bounded graph, revision, and semantic diff reads

**Files:**

- Modify: `hermes_cli/evolution/dashboard_service.py`
- Modify: `tests/hermes_cli/evolution/test_dashboard_service.py`
- Modify: `hermes_cli/gnothi/query.py`
- Modify: `tests/hermes_cli/test_gnothi_query.py`

- [ ] **Step 1: Add graph contract tests**

Test:

- `depth` outside `0..4` is rejected;
- `limit` outside `1..200` is rejected;
- no-root graph returns the first bounded stable-ID-sorted nodes;
- rooted graph includes the root, both dependency directions, and only `provides`, `requires`, `depends_on`;
- filters apply to node kinds without leaving dangling edges;
- the response reports `total_nodes`, `total_edges`, and `truncated`;
- search matches stable ID and case-insensitive label;
- evidence refs are capped at 20 per node and sanitized.

Expected shape:

```python
{
    "schema_version": 1,
    "revision_id": "rev-20260728-001",
    "revision_digest": "a" * 64,
    "nodes": [{"id": "capability:alpha", "kind": "capability"}],
    "edges": [],
    "blockers": [],
    "total_nodes": 312,
    "total_edges": 478,
    "truncated": True,
}
```

- [ ] **Step 2: Extract a reusable bounded subgraph query**

Extend `OrganismQuery` with:

```python
def subgraph(
    self,
    *,
    root_id: str | None,
    depth: int,
    limit: int,
    kinds: frozenset[str],
    search: str,
) -> dict[str, Any]:
```

Use a deterministic breadth-first traversal and stable ID sort. Count the full matching candidate set before slicing. Never mutate the artifact.

- [ ] **Step 3: Add service methods**

```python
def graph(
    self,
    *,
    root_id: str | None = None,
    depth: int = 2,
    limit: int = 200,
    kinds: frozenset[str] | None = None,
    search: str = "",
    expected_revision: str | None = None,
) -> dict[str, Any]
def revisions(limit: int = 50) -> dict[str, Any]
def revision_diff(left: str, right: str) -> dict[str, Any]
```

If `expected_revision` does not equal the current pointer revision, raise `EvolutionDashboardConflict("gnothi_revision_changed")`.

- [ ] **Step 4: Run graph tests**

```bash
./scripts/run_tests.sh \
  tests/hermes_cli/test_gnothi_query.py \
  tests/hermes_cli/evolution/test_dashboard_service.py -q
```

- [ ] **Step 5: Commit**

```bash
git add \
  hermes_cli/gnothi/query.py \
  hermes_cli/evolution/dashboard_service.py \
  tests/hermes_cli/test_gnothi_query.py \
  tests/hermes_cli/evolution/test_dashboard_service.py
git commit -m "feat(evolution): expose bounded organism graph reads"
```

---

### Task 3: Add Telos, Observer, pipeline, and audit reads

**Files:**

- Modify: `hermes_cli/evolution/dashboard_service.py`
- Modify: `tests/hermes_cli/evolution/test_dashboard_service.py`

- [ ] **Step 1: Write relation-based read tests**

Avoid frozen counts. Assert:

- active Telos digest equals the digest of the returned active revision;
- every returned historical Telos revision belongs to the probed organism;
- suggestion counts sum to the number of bounded suggestion rows;
- every blueprint row resolves through `BlueprintRepository`;
- an attempt's audit rows are monotonically increasing by event sequence;
- unsupported runtime stages are returned with `available: False` and no action;
- corrupt event chains return `state: "corrupt"` and no mutable actions.

- [ ] **Step 2: Add service reads**

Implement:

```python
def telos(self, *, history_limit: int = 50) -> dict[str, Any]
def pipeline(
    self, *, attempt_id: str | None = None, limit: int = 50
) -> dict[str, Any]
def audit(self, *, after: int = 0, limit: int = 100) -> dict[str, Any]
```

Use existing public contracts:

- `TelosStore.get_active_digest()` and `get_revision()`;
- `SuggestionRepository.list_suggestions()`;
- `BlueprintRepository.list()` / `get()`;
- `EvolutionLedger.history()`;
- `_observer_status()` semantics or `ObserverService` only after safe-root preflight.

Sanitize each summary with
`redact_sensitive_text(summary, force=True)`. Return at most 50 suggestions,
50 blueprints, 50 Telos revisions, and 100 audit events.

- [ ] **Step 3: Define the pipeline stage table**

Use a table rather than condition ladders:

```python
PIPELINE_STAGES = (
    ("suggestion", True),
    ("research", True),
    ("blueprint", True),
    ("build", False),
    ("canary", False),
    ("promotion", False),
    ("stable", False),
)
```

Availability for later stages may become true only when a real local runtime service exists. Do not expose action names for unavailable stages.

- [ ] **Step 4: Run tests**

```bash
./scripts/run_tests.sh tests/hermes_cli/evolution/test_dashboard_service.py -q
```

- [ ] **Step 5: Commit**

```bash
git add hermes_cli/evolution/dashboard_service.py tests/hermes_cli/evolution/test_dashboard_service.py
git commit -m "feat(evolution): add dashboard governance reads"
```

---

### Task 4: Implement persistent bounded local jobs

**Files:**

- Create: `hermes_cli/evolution/dashboard_jobs.py`
- Create: `tests/hermes_cli/evolution/test_dashboard_jobs.py`

- [ ] **Step 1: Write job-store tests**

Cover:

- reads against an absent jobs directory do not create it;
- `submit()` creates a UUID job and a 0600 JSON record;
- only one rebuild and one observer scan can run at a time;
- progress is clamped to `0..100`;
- error detail is redacted and capped;
- a record left `running` by another process nonce reads as `unknown` with reason `process_interrupted`;
- result payloads are capped and use known result kinds;
- cancellation changes only queued jobs in v1.

- [ ] **Step 2: Define the persisted record**

```python
@dataclass(frozen=True)
class EvolutionJob:
    job_id: str
    kind: Literal["organism_rebuild", "observer_scan", "revision_diff"]
    state: Literal[
        "queued", "running", "completed", "failed", "cancelled", "unknown"
    ]
    progress: int
    created_at: str
    started_at: str | None
    finished_at: str | None
    process_nonce: str
    result: dict[str, Any] | None
    error_code: str | None
```

Store records under `<organism>/evolution/dashboard-jobs/<job_id>.json` with atomic replace and 0600 permissions. The directory is created only by `submit()`.

- [ ] **Step 3: Add a bounded executor**

`EvolutionJobManager` uses one `ThreadPoolExecutor(max_workers=2)`. Register fixed Python callables by kind; never accept a callable name or command from HTTP.

For `organism_rebuild`, call:

```python
build_organism_revision(
    workspace,
    store=global_store,
    force=force,
    collector_names=collector_names,
)
```

Validate the workspace is the repository root selected by the server, not a
browser-supplied arbitrary path.

For `observer_scan`, call `ObserverService(root).scan_and_update_suggestions(max_events=1000)`.

For `revision_diff`, call `OrganismQuery.diff(left, right)`.

- [ ] **Step 4: Run tests**

```bash
./scripts/run_tests.sh tests/hermes_cli/evolution/test_dashboard_jobs.py -q
```

- [ ] **Step 5: Commit**

```bash
git add hermes_cli/evolution/dashboard_jobs.py tests/hermes_cli/evolution/test_dashboard_jobs.py
git commit -m "feat(evolution): add bounded local dashboard jobs"
```

---

### Task 5: Add digest-bound mutation services

**Files:**

- Modify: `hermes_cli/evolution/dashboard_service.py`
- Create: `hermes_cli/evolution/dashboard_confirmations.py`
- Modify: `tests/hermes_cli/evolution/test_dashboard_service.py`
- Create: `tests/hermes_cli/evolution/test_dashboard_confirmations.py`

- [ ] **Step 1: Write stale-digest and atomicity tests**

Every test records filesystem/DB state before a stale request and proves it is byte-for-byte unchanged afterward.

Cover:

- initialize only when identity is absent;
- pause/resume expected snapshot mismatch;
- rebuild submission expected snapshot mismatch;
- Telos draft expected active digest mismatch;
- blueprint expected suggestion digest/state mismatch;
- Telos activation/rollback expected current digest mismatch;
- confirmation phrase mismatch;
- confirmation expiry and one-time consumption;
- successful blueprint creation remains idempotent.

- [ ] **Step 2: Add common mutation validation**

```python
def _validate_mutation(
    self,
    *,
    organism_id: str,
    expected_snapshot_digest: str,
) -> OrganismIdentity:
    identity = load_organism_identity(self.root)
    if identity.organism_id != organism_id:
        raise EvolutionDashboardConflict("organism_changed")
    if self.snapshot()["snapshot_digest"] != expected_snapshot_digest:
        raise EvolutionDashboardConflict("snapshot_changed")
    return identity
```

Call it inside `lifecycle_lock(home=self.root, timeout_seconds=10)`.

- [ ] **Step 3: Add supported mutation methods**

```python
def initialize(self) -> dict[str, Any]
def mutation_context(self) -> dict[str, Any]
def set_observer_enabled(
    self,
    *,
    organism_id: str,
    expected_snapshot_digest: str,
    enabled: bool,
) -> dict[str, Any]
def submit_rebuild(
    self,
    *,
    organism_id: str,
    expected_snapshot_digest: str,
    force: bool,
    collectors: list[str],
) -> EvolutionJob
def submit_observer_scan(
    self,
    *,
    organism_id: str,
    expected_snapshot_digest: str,
) -> EvolutionJob
def save_telos_draft(
    self,
    *,
    organism_id: str,
    expected_snapshot_digest: str,
    document: dict[str, Any],
) -> dict[str, Any]
def create_blueprint(
    self,
    *,
    organism_id: str,
    expected_snapshot_digest: str,
    suggestion_id: str,
) -> dict[str, Any]
```

`initialize()` calls `ensure_global_lifecycle_initialized()` and returns the newly created identity/snapshot. It must fail closed if a valid identity already exists or if corrupt identity material exists.

`mutation_context()` is an authenticated, read-only response containing the
full organism UUID and the current snapshot digest. It exists because mutation
requests must carry the full identity while ordinary snapshot/display payloads
must not. It returns no lineage digest, paths, secrets, or private artifact
content.

Draft validation uses `telos_revision_from_dict()` and `validate_telos_revision()`. Blueprint creation delegates to `propose_suggestion()`.

- [ ] **Step 4: Add host-owned dashboard Telos confirmations**

`DashboardConfirmationStore` retains the secret `HostApprovalContext` server-side in a locked in-memory map. A process restart invalidates pending confirmations safely.

Preparation:

```python
prepared = prepare_telos_pending_request(
    digest=target_digest,
    action=action,
    surface="dashboard",
    actor_ref="authenticated-local-operator",
    session_ref=secrets.token_urlsafe(32),
    ttl_seconds=300,
    organism_root=self.root,
)
```

Return only:

```python
{
    "confirmation_id": request_id,
    "display_nonce": prompt_fields["display_nonce"],
    "organism_id": identity.organism_id,
    "current_digest": current_digest,
    "target_digest": target_digest,
    "action": action,
    "expires_at": prompt_fields["expires_at"],
    "required_phrase": (
        f"{action.upper()} {identity.organism_id[:8]} "
        f"{target_digest[:12]} {prompt_fields['display_nonce']}"
    ),
}
```

Confirmation revalidates organism ID, current digest, target digest, phrase, and expiry, then calls `perform_telos_transition()`. Remove the server-side context whether the final decision succeeds or fails. A stale digest returns conflict without pointer mutation.

- [ ] **Step 5: Run mutation tests**

```bash
./scripts/run_tests.sh \
  tests/hermes_cli/evolution/test_dashboard_service.py \
  tests/hermes_cli/evolution/test_dashboard_confirmations.py -q
```

- [ ] **Step 6: Commit**

```bash
git add \
  hermes_cli/evolution/dashboard_service.py \
  hermes_cli/evolution/dashboard_confirmations.py \
  tests/hermes_cli/evolution/test_dashboard_service.py \
  tests/hermes_cli/evolution/test_dashboard_confirmations.py
git commit -m "feat(evolution): add governed dashboard mutations"
```

---

### Task 6: Create the bundled plugin manifest and authenticated FastAPI adapter

**Files:**

- Create: `plugins/evolution/dashboard/manifest.json`
- Create: `plugins/evolution/dashboard/plugin_api.py`
- Create: `tests/plugins/test_evolution_dashboard_plugin.py`
- Modify: `tests/plugins/test_plugin_dashboard_auth_contract.py` only if the new bundle changes a fixture assumption

- [ ] **Step 1: Write plugin discovery and API tests**

Load the router the same way as `tests/plugins/test_kanban_dashboard_plugin.py`, mount it at `/api/plugins/evolution`, and isolate the global root.

Test:

- manifest discovery and `/evolution` tab;
- GET snapshot does not create an absent root;
- GET mutation context returns the full identity only for initialized state;
- GET graph bounds and input validation;
- GET Telos/pipeline/audit bounds;
- POST initialize;
- POST rebuild and scan return `202`;
- GET job polling;
- POST observer pause/resume;
- POST Telos draft;
- POST confirmation prepare/confirm;
- POST blueprint;
- `409` for stale digests;
- stable sanitized `400/404/409/422/500` bodies;
- no remote backend module is imported or client constructed.

- [ ] **Step 2: Add the exact manifest**

```json
{
  "name": "evolution",
  "label": "Evolution",
  "description": "Local Gnothi Seauton and Autopoiesis control center",
  "icon": "Activity",
  "version": "1.0.0",
  "tab": {
    "path": "/evolution",
    "position": "after:plugins"
  },
  "entry": "dist/index.js",
  "css": "dist/style.css",
  "api": "plugin_api.py"
}
```

- [ ] **Step 3: Add explicit Pydantic request models**

Define models for:

- `InitializeRequest`;
- `MutationContext` (`organism_id`, `expected_snapshot_digest`);
- `RebuildRequest` (`force`, bounded collector enum);
- `ObserverToggleRequest`;
- `TelosDraftRequest`;
- `TelosPrepareRequest`;
- `TelosConfirmRequest`;
- `BlueprintRequest`.

Use `extra="forbid"` and length/pattern constraints. Never accept filesystem paths, URLs, arbitrary command names, actor IDs, or session IDs.

- [ ] **Step 4: Add route table**

```text
GET  /snapshot
GET  /mutation-context
GET  /graph
GET  /revisions
GET  /diff
GET  /telos
GET  /pipeline
GET  /audit
GET  /jobs/{job_id}
POST /initialize
POST /jobs/organism-rebuild
POST /jobs/observer-scan
POST /observer
POST /telos/drafts
POST /telos/transitions/prepare
POST /telos/transitions/confirm
POST /suggestions/{suggestion_id}/blueprint
```

Return `202` only for submitted jobs. Map `EvolutionDashboardConflict` to `409` with `{"code": exc.code}`. Unexpected errors return `{"code": "evolution_unavailable"}` without exception text.

- [ ] **Step 5: Run plugin API and auth tests**

```bash
./scripts/run_tests.sh \
  tests/plugins/test_evolution_dashboard_plugin.py \
  tests/plugins/test_plugin_dashboard_auth_contract.py -q
```

- [ ] **Step 6: Commit**

```bash
git add \
  plugins/evolution/dashboard/manifest.json \
  plugins/evolution/dashboard/plugin_api.py \
  tests/plugins/test_evolution_dashboard_plugin.py
git commit -m "feat(evolution): expose authenticated dashboard API"
```

---

### Task 7: Run the API regression gate

- [ ] **Step 1: Run all affected tests**

```bash
./scripts/run_tests.sh \
  tests/hermes_cli/test_gnothi_query.py \
  tests/hermes_cli/evolution \
  tests/plugins/test_evolution_dashboard_plugin.py \
  tests/plugins/test_plugin_dashboard_auth_contract.py -q
```

- [ ] **Step 2: Verify route isolation**

```bash
rg -n "hades_backend|BackendClient|subprocess|shell=True|os\\.system" \
  plugins/evolution hermes_cli/evolution/dashboard_service.py \
  hermes_cli/evolution/dashboard_jobs.py
```

Expected: no remote backend imports and no command-execution path.

- [ ] **Step 3: Verify formatting**

```bash
git diff --check
git status --short
```
