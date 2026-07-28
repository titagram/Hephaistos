# Evolution Control Center Local Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish one non-mutating, cross-profile, local-only organism source of truth before the dashboard can read or mutate evolution state.

**Architecture:** The rich `OrganismRevisionStore` becomes the only Gnothi store and defaults to `<default Hermes root>/organism/gnothi_seauton`. Identity probing is separated from identity creation. Existing profile-local Gnothi stores are detected but never merged. Remote backend sync and the Hades backend memory provider lose the `organism` graph scope entirely.

**Tech Stack:** Python 3, pathlib, immutable JSON artifacts, SQLite lifecycle services, pytest.

## Global Constraints

- Execute after `2026-07-28-evolution-control-center-index.md`.
- Tests must use temporary default roots and profile overrides; never touch the developer's real `~/.hermes`.
- Absence checks must assert the organism directory was not created.
- Do not import the obsolete lightweight `GlobalGnothiStore` schema into the rich graph store.
- Do not change project-scoped remote graph search/traversal behavior.
- Do not change lifecycle state names or authorization policies in this plan.

---

### Task 1: Add a strictly non-mutating organism identity probe

**Files:**

- Modify: `hermes_cli/evolution/organism_identity.py`
- Modify: `tests/hermes_cli/evolution/test_organism_identity.py`

- [ ] **Step 1: Write failing probe tests**

Add tests that cover all externally relevant states:

```python
def test_probe_missing_identity_does_not_create_root(tmp_path):
    root = tmp_path / "organism"
    assert probe_organism_identity(root) is None
    assert not root.exists()


def test_probe_returns_existing_identity_without_writing(tmp_path):
    root = tmp_path / "organism"
    expected = create_organism_identity(root)
    before = sorted(path.relative_to(root) for path in root.rglob("*"))
    assert probe_organism_identity(root) == expected
    assert sorted(path.relative_to(root) for path in root.rglob("*")) == before


def test_probe_rejects_symlink_and_corrupt_identity(tmp_path):
    root = tmp_path / "organism"
    root.mkdir()
    (root / "identity.json").write_text("{}", encoding="utf-8")
    with pytest.raises(OrganismIdentityError, match="organism_identity_corrupted"):
        probe_organism_identity(root)
```

- [ ] **Step 2: Run the focused test and confirm it fails**

Run:

```bash
./scripts/run_tests.sh tests/hermes_cli/evolution/test_organism_identity.py -q
```

Expected: failure because `probe_organism_identity` does not exist.

- [ ] **Step 3: Implement the read-only probe**

Add a function which uses `resolve_organism_root()` and `_identity_path_stat()` but never calls `ensure_organism_directories()`:

```python
def probe_organism_identity(
    organism_root: Path | None = None,
) -> OrganismIdentity | None:
    from .organism_home import resolve_organism_root

    root = resolve_organism_root(organism_root)
    st = _identity_path_stat(root)
    if st is None:
        return None
    if not stat_module.S_ISREG(st.st_mode):
        raise OrganismIdentityError("organism_identity_unsafe")
    try:
        data = json.loads((root / "identity.json").read_text(encoding="utf-8"))
        identity = OrganismIdentity(
            schema_version=int(data.get("schema_version", 0)),
            organism_id=str(data.get("organism_id", "")),
            created_at=str(data.get("created_at", "")),
            lineage_root_digest=str(data.get("lineage_root_digest", "")),
        )
        validate_organism_identity(identity)
        return identity
    except OrganismIdentityError:
        raise
    except Exception:
        raise OrganismIdentityError("organism_identity_corrupted") from None
```

Refactor `load_organism_identity()` to retain its current create-directory behavior for existing mutation paths while sharing a private `_decode_identity()` helper with the probe.

- [ ] **Step 4: Run the focused tests**

Run:

```bash
./scripts/run_tests.sh tests/hermes_cli/evolution/test_organism_identity.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add hermes_cli/evolution/organism_identity.py tests/hermes_cli/evolution/test_organism_identity.py
git commit -m "feat(evolution): add non-mutating organism identity probe"
```

---

### Task 2: Make the rich Gnothi revision store global and cross-profile

**Files:**

- Modify: `hermes_cli/gnothi/store.py`
- Modify: `tests/hermes_cli/test_gnothi_store.py`
- Modify: `tests/hermes_cli/test_hades_gnothi_cmd.py`
- Modify: `tests/hermes_cli/test_gnothi_e2e.py`

- [ ] **Step 1: Write the cross-profile behavior test**

Use `HERMES_HOME` to switch the active profile while keeping the same default root:

```python
def test_default_store_is_global_across_profile_switches(
    tmp_path, monkeypatch
):
    default_root = tmp_path / ".hermes"
    profile_root = default_root / "profiles" / "reviewer"
    monkeypatch.setattr(
        hermes_constants, "get_default_hermes_root", lambda: default_root
    )
    monkeypatch.setenv("HERMES_HOME", str(profile_root))

    store = OrganismRevisionStore()
    assert store.root == default_root / "organism" / "gnothi_seauton"
```

Add an integration test that publishes through the default profile, switches to a named profile, and reads the same current revision and pointer digest.

- [ ] **Step 2: Run the focused tests and confirm the old profile-local path fails**

Run:

```bash
./scripts/run_tests.sh \
  tests/hermes_cli/test_gnothi_store.py \
  tests/hermes_cli/test_hades_gnothi_cmd.py \
  tests/hermes_cli/test_gnothi_e2e.py -q
```

Expected: new assertions fail because the store uses `get_hermes_home()`.

- [ ] **Step 3: Change only the default root resolver**

Replace the profile-aware import and constructor default:

```python
import hermes_constants


class OrganismRevisionStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (
            root
            if root is not None
            else hermes_constants.get_organism_home() / "gnothi_seauton"
        )
```

Keep explicit `root=` behavior unchanged so tests and isolated callers remain deterministic.

- [ ] **Step 4: Update test fixtures to patch the default root, not only `HERMES_HOME`**

Where a test intentionally exercises the default constructor, patch `hermes_constants.get_default_hermes_root()` or set the repository-supported default-root override. Do not make production code profile-aware again to satisfy tests.

- [ ] **Step 5: Run the focused tests**

Run:

```bash
./scripts/run_tests.sh \
  tests/hermes_cli/test_gnothi_store.py \
  tests/hermes_cli/test_hades_gnothi_cmd.py \
  tests/hermes_cli/test_gnothi_e2e.py -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add \
  hermes_cli/gnothi/store.py \
  tests/hermes_cli/test_gnothi_store.py \
  tests/hermes_cli/test_hades_gnothi_cmd.py \
  tests/hermes_cli/test_gnothi_e2e.py
git commit -m "fix(gnothi): use one global organism revision store"
```

---

### Task 3: Detect legacy profile-local Gnothi state without merging it

**Files:**

- Modify: `hermes_cli/gnothi/store.py`
- Modify: `hermes_cli/gnothi/query.py`
- Modify: `hermes_cli/hades_gnothi_cmd.py`
- Modify: `tests/hermes_cli/test_gnothi_store.py`
- Modify: `tests/hermes_cli/test_hades_gnothi_cmd.py`

- [ ] **Step 1: Write failing legacy-state tests**

Cover these invariants:

```python
def test_legacy_profile_store_is_reported_but_not_imported(
    tmp_path, monkeypatch
):
    # Write a valid legacy pointer and revision under
    # <active profile>/gnothi_seauton.
    # Leave the global store absent.
    result = OrganismQuery(OrganismRevisionStore()).status()
    assert result == {
        "status": "missing",
        "actions": ["rebuild"],
        "diagnostics": ["legacy_profile_state_detected"],
    }
    assert not global_root.exists()


def test_rebuild_publishes_fresh_global_revision_without_copying_legacy(
    tmp_path, monkeypatch
):
    # Stub collectors with deterministic fresh output.
    # Assert the new global revision digest differs from the legacy artifact
    # and no legacy revision filename appears in the global revision directory.
```

- [ ] **Step 2: Add a read-only legacy detector**

Implement:

```python
def legacy_profile_store_present() -> bool:
    legacy = hermes_constants.get_hermes_home() / "gnothi_seauton"
    canonical = hermes_constants.get_organism_home() / "gnothi_seauton"
    if legacy.absolute() == canonical.absolute():
        return False
    return (legacy / "current.json").is_file()
```

The function is diagnostic only. It must not parse, copy, rename, delete, or publish legacy content.

- [ ] **Step 3: Surface the diagnostic in missing status**

Change `OrganismQuery.status()` so absent global state returns:

```python
result = {"status": "missing", "actions": ["rebuild"]}
if legacy_profile_store_present():
    result["diagnostics"] = ["legacy_profile_state_detected"]
return result
```

Keep `rebuild` as a fresh collector run. Do not add an `import` or `merge` action.

- [ ] **Step 4: Run focused tests**

Run:

```bash
./scripts/run_tests.sh \
  tests/hermes_cli/test_gnothi_store.py \
  tests/hermes_cli/test_hades_gnothi_cmd.py -q
```

Expected: all pass and global absence remains non-mutating.

- [ ] **Step 5: Commit**

```bash
git add \
  hermes_cli/gnothi/store.py \
  hermes_cli/gnothi/query.py \
  hermes_cli/hades_gnothi_cmd.py \
  tests/hermes_cli/test_gnothi_store.py \
  tests/hermes_cli/test_hades_gnothi_cmd.py
git commit -m "fix(gnothi): detect legacy profile state without merging"
```

---

### Task 4: Remove the conflicting lightweight global Gnothi store

**Files:**

- Delete: `hermes_cli/evolution/gnothi_store.py`
- Delete: `tests/hermes_cli/evolution/test_gnothi_store.py`
- Modify only if imports exist after the deletion: the importing production/test files returned by `rg`

- [ ] **Step 1: Prove the lightweight store has no production consumer**

Run:

```bash
rg -n "GlobalGnothiStore|evolution\\.gnothi_store" \
  --glob '*.py' \
  --glob '!hermes_cli/evolution/gnothi_store.py' \
  --glob '!tests/hermes_cli/evolution/test_gnothi_store.py'
```

Expected: no output. If production output appears, stop and migrate that consumer to `OrganismRevisionStore` with a focused test before deleting the file.

- [ ] **Step 2: Delete the incompatible schema and its isolated tests**

Remove both files. The rich `hades.organism_graph.v1` artifact and `hades.gnothi_pointer.v1` pointer are now the sole Gnothi contracts.

- [ ] **Step 3: Run all Gnothi and evolution tests**

Run:

```bash
./scripts/run_tests.sh \
  tests/hermes_cli/test_gnothi_store.py \
  tests/hermes_cli/test_gnothi_builder.py \
  tests/hermes_cli/test_gnothi_query.py \
  tests/hermes_cli/test_gnothi_e2e.py \
  tests/hermes_cli/test_hades_gnothi_cmd.py \
  tests/hermes_cli/evolution -q
```

Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add -u hermes_cli/evolution/gnothi_store.py tests/hermes_cli/evolution/test_gnothi_store.py
git commit -m "refactor(evolution): remove conflicting gnothi store"
```

---

### Task 5: Stop uploading organism artifacts during backend sync

**Files:**

- Modify: `hermes_cli/hades_backend_sync.py`
- Modify: `tests/hermes_cli/test_hades_backend_sync_runner.py`

- [ ] **Step 1: Replace upload-positive tests with a negative contract**

Write a test whose advertised backend capabilities include both the old schema and old `graph_scopes: ["project", "organism"]`, then assert:

```python
assert not any(
    call["payload"].get("artifact", {}).get("schema")
    == "hades.organism_graph.v1"
    for call in fake_client.artifact_calls
)
```

Also assert ordinary project artifacts continue to sync.

- [ ] **Step 2: Run the focused test and confirm it fails**

Run:

```bash
./scripts/run_tests.sh tests/hermes_cli/test_hades_backend_sync_runner.py -q
```

Expected: the new negative assertion fails on `_sync_current_organism_artifact()`.

- [ ] **Step 3: Remove the remote organism sync path**

Delete:

- `ORGANISM_GRAPH_SCHEMA`;
- the `_supports_organism_graph()` branch in `run_backend_sync`;
- `_supports_organism_graph()`;
- `_sync_current_organism_artifact()`;
- `_organism_artifact_matches_binding()`;
- organism-schema capability advertisement derived only for this upload path.

Do not alter project baseline or source-slice upload paths.

- [ ] **Step 4: Run backend sync tests**

Run:

```bash
./scripts/run_tests.sh tests/hermes_cli/test_hades_backend_sync_runner.py -q
```

Expected: all pass, including project artifact upload tests.

- [ ] **Step 5: Commit**

```bash
git add hermes_cli/hades_backend_sync.py tests/hermes_cli/test_hades_backend_sync_runner.py
git commit -m "fix(backend): keep organism artifacts local"
```

---

### Task 6: Remove remote `organism` graph scope from the Hades backend memory provider

**Files:**

- Modify: `plugins/memory/hades_backend/__init__.py`
- Modify: `tests/agent/test_hades_backend_memory_provider.py`

- [ ] **Step 1: Change the schema contract tests first**

Assert both graph tools expose only project scope:

```python
assert graph_search["function"]["parameters"]["properties"]["scope"]["enum"] == [
    "project"
]
assert graph_traverse["function"]["parameters"]["properties"]["scope"]["enum"] == [
    "project"
]
```

Add direct-call tests asserting `scope="organism"` returns an unsupported-scope error and never calls the remote client or `OrganismRevisionStore`.

- [ ] **Step 2: Run focused provider tests and confirm failure**

Run:

```bash
./scripts/run_tests.sh tests/agent/test_hades_backend_memory_provider.py -q
```

- [ ] **Step 3: Collapse graph scope to project-only**

Set:

```python
GRAPH_SCOPES = ("project",)
```

Remove:

- `OrganismRevisionStore` imports;
- `_local_organism_graph_sources()`;
- organism branches in `_graph_sources_for_scope()`;
- organism scope annotations in result payloads;
- organism-specific backend fallback and identity branches.

Retain local cached project graph fallback unchanged.

- [ ] **Step 4: Run provider tests**

Run:

```bash
./scripts/run_tests.sh tests/agent/test_hades_backend_memory_provider.py -q
```

Expected: project graph behavior passes and organism scope is rejected locally before I/O.

- [ ] **Step 5: Commit**

```bash
git add plugins/memory/hades_backend/__init__.py tests/agent/test_hades_backend_memory_provider.py
git commit -m "fix(memory): remove remote organism graph scope"
```

---

### Task 7: Amend operator guidance without changing lifecycle governance

**Files:**

- Modify: `skills/autopoiesis/SKILL.md`
- Modify: `tests/hermes_cli/evolution/test_state_machine.py`
- Create: `tests/hermes_cli/evolution/test_research_policy_copy.py`

- [ ] **Step 1: Add a wording guard**

The test must assert the skill says all three facts:

```python
assert "public read-only research is always allowed" in normalized
assert "research_authorized" in normalized
assert "not a network permission" in normalized
```

It must also reject phrases that say web access is enabled by a research grant.

- [ ] **Step 2: Update the Autopoiesis skill**

State:

- public search and public documentation reads are always available;
- private data must never enter queries or uploads;
- authenticated browsing, forms, downloads, installs, and execution are outside this permission;
- `research_authorized` governs attempt progression only;
- build, activation, promotion, rollback, and retirement remain separately governed.

Do not rewrite the parent historical design documents. The approved 2026-07-28 design is their explicit amendment.

- [ ] **Step 3: Preserve state-machine behavior**

Keep the existing `draft -> research_authorized -> blueprint_ready` transition tests. Add a comment-level assertion or test name making clear the authorization kind is lifecycle governance, not network enablement.

- [ ] **Step 4: Run policy and state-machine tests**

Run:

```bash
./scripts/run_tests.sh \
  tests/hermes_cli/evolution/test_research_policy_copy.py \
  tests/hermes_cli/evolution/test_state_machine.py -q
```

- [ ] **Step 5: Commit**

```bash
git add \
  skills/autopoiesis/SKILL.md \
  tests/hermes_cli/evolution/test_research_policy_copy.py \
  tests/hermes_cli/evolution/test_state_machine.py
git commit -m "docs(autopoiesis): separate research access from lifecycle grants"
```

---

### Task 8: Run the foundation regression gate

- [ ] **Step 1: Run the full focused suite**

```bash
./scripts/run_tests.sh \
  tests/hermes_cli/test_gnothi_store.py \
  tests/hermes_cli/test_gnothi_builder.py \
  tests/hermes_cli/test_gnothi_query.py \
  tests/hermes_cli/test_gnothi_e2e.py \
  tests/hermes_cli/test_hades_gnothi_cmd.py \
  tests/hermes_cli/evolution \
  tests/hermes_cli/test_hades_backend_sync_runner.py \
  tests/agent/test_hades_backend_memory_provider.py -q
```

- [ ] **Step 2: Verify no remote organism path remains**

```bash
rg -n \
  "_sync_current_organism_artifact|_local_organism_graph_sources|scope == [\"']organism[\"']|graph_scopes.*organism" \
  hermes_cli plugins/memory/hades_backend tests
```

Expected: no production matches. Historical approved specs and explicit negative tests may still contain the word `organism`.

- [ ] **Step 3: Verify formatting and repository state**

```bash
git diff --check
git status --short
```

Expected: only intended foundation changes and the pre-existing untracked `README_MEMORY_COMMANDS.ms`.
