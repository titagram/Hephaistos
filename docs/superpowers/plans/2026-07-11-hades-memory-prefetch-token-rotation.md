# Hades Stable Memory Recall and Token Rotation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure automatic Hades recall reaches compact wiki evidence and prevent stale long-lived processes from quarantining bindings after credential rotation.

**Architecture:** Correct backend candidate selection so excluded raw chunks never consume the memory candidate window, then add a bounded wiki fallback in the Hermes provider for compatibility and query-language misses. Protect auth quarantine with a one-way fingerprint comparison between the credential used by the sync and the credential currently persisted on disk.

**Tech Stack:** Laravel/PHP/Pest on the backend; Python 3, pytest, SQLite, and the existing Hades memory-provider and sync modules in Hermes.

## Global Constraints

- Do not inject the complete source or wiki corpus into a turn.
- Do not treat raw chunks as compact authoritative memory.
- Preserve prompt caching: memory context stays ephemeral in the current user turn.
- Do not add a model tool or change existing tool schemas.
- Never log, persist, or expose raw backend credentials.
- A genuine `401` for the currently persisted credential must remain fail-closed.
- Backend tests must bypass the production config cache with `APP_CONFIG_CACHE=/tmp/hades-test-config.php`; never run `RefreshDatabase` against the live cached PostgreSQL configuration.

---

### Task 1: Exclude raw memory chunks before backend candidate limiting

**Files:**
- Modify (remote backend): `/home/ubuntu/dev-sandbox/backend/app/Http/Controllers/Hades/MemorySearchController.php`
- Test (remote backend): `/home/ubuntu/dev-sandbox/backend/tests/Feature/Hades/HadesM3SharedMemoryTest.php`

**Interfaces:**
- Consumes: `MemorySearchController::memoryResults(..., bool $includeRawChunks, ...)`
- Produces: a query-level raw exclusion helper applied before `orderBy` and `limit`; the JSON response contract remains unchanged.

- [ ] **Step 1: Write the failing backend test**

Add a Pest case beside the existing raw-chunk search tests. Insert more than the controller's current coarse candidate window of matching raw entries, then insert an older compact matching entry. Search with `domain=project_memory`, `limit=8`, and `include_raw_chunks=false`.

```php
it('does not let excluded raw chunks consume compact memory candidate slots', function () {
    $agent = hadesM3RegisteredAgent();
    $binding = hadesM3WorkspaceBinding($agent);
    $now = now();

    DB::table('project_memory_entries')->insert(collect(range(1, 120))->map(fn (int $index) => [
        'id' => (string) Str::ulid(),
        'project_id' => $agent['project_id'],
        'repository_id' => null,
        'task_id' => null,
        'run_id' => null,
        'author_user_id' => null,
        'agent_key' => 'raw-import',
        'source' => 'hades_agent',
        'kind' => 'proposal',
        'completeness' => 'complete',
        'summary' => "Carnovali payroll raw chunk {$index}",
        'payload' => json_encode(['schema' => 'hades.backend_wiki.file_chunk.v1'], JSON_THROW_ON_ERROR),
        'occurred_at' => $now->copy()->addSeconds($index),
        'created_at' => $now,
        'updated_at' => $now,
    ])->all());

    DB::table('project_memory_entries')->insert([
        'id' => (string) Str::ulid(),
        'project_id' => $agent['project_id'],
        'repository_id' => null,
        'task_id' => null,
        'run_id' => null,
        'author_user_id' => null,
        'agent_key' => 'memory-steward',
        'source' => 'hades_agent',
        'kind' => 'project_note',
        'completeness' => 'complete',
        'summary' => 'Carnovali payroll uses the verified monthly workflow.',
        'payload' => json_encode(['schema' => 'devboard.memory_note.v1'], JSON_THROW_ON_ERROR),
        'occurred_at' => $now->subMinute(),
        'created_at' => $now,
        'updated_at' => $now,
    ]);

    $this->getJson('/api/hades/v1/memory/search?'.http_build_query([
        'project_id' => $agent['project_id'],
        'workspace_binding_id' => $binding['workspace_binding_id'],
        'query' => 'Carnovali payroll',
        'domain' => 'project_memory',
        'limit' => 8,
        'include_raw_chunks' => false,
    ]), hadesM3Headers($agent['agent_token']))
        ->assertOk()
        ->assertJsonPath('count', 1)
        ->assertJsonPath('items.0.kind', 'project_note');
});
```

- [ ] **Step 2: Run the focused test and verify RED safely**

Run from `/home/ubuntu/dev-sandbox/backend`:

```bash
APP_CONFIG_CACHE=/tmp/hades-test-config.php \
APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= \
php artisan test tests/Feature/Hades/HadesM3SharedMemoryTest.php \
  --filter='does not let excluded raw chunks consume compact memory candidate slots'
```

Expected: FAIL because the compact entry is absent after the SQL query limits the newer raw rows.

- [ ] **Step 3: Implement query-level raw exclusion**

Add focused helpers that mirror `RAW_CHUNK_MARKERS`: one builds the grouped raw predicate used to preserve the existing `raw_chunks_omitted` count, and one applies the inverse predicate before ordering/limiting.

```php
private function excludeRawChunksFromQuery(mixed $builder): void
{
    $builder->where(function ($query): void {
        foreach (self::RAW_CHUNK_MARKERS as $marker) {
            $like = '%'.$marker.'%';
            $query
                ->where('summary', 'not like', $like)
                ->where('payload', 'not like', $like);
        }
    });
}
```

Build the filtered query first. Before excluding rows, clone it and count raw matches using grouped `LIKE` predicates over `summary` and `payload`; assign that count to the existing `raw_chunks_omitted` response field. Then apply the exclusion before `orderByDesc()`:

```php
if (! $includeRawChunks) {
    $rawChunksOmitted = (clone $builder)
        ->where(fn ($query) => $this->applyRawChunkMatch($query))
        ->count();
}

->when(! $includeRawChunks, function ($builder): void {
    $this->excludeRawChunksFromQuery($builder);
})
```

Keep the existing in-PHP `isRawChunk()` guard as defense in depth. Extend the new regression test with `->assertJsonPath('raw_chunks_omitted', 120)` so query-level filtering does not silently weaken the API contract.

- [ ] **Step 4: Run focused and neighboring tests**

```bash
APP_CONFIG_CACHE=/tmp/hades-test-config.php \
APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= \
php artisan test tests/Feature/Hades/HadesM3SharedMemoryTest.php \
  --filter='raw chunks|excluded raw chunks|wiki'
```

Expected: PASS with no failures. Verify the production counts remain unchanged immediately afterward:

```bash
docker exec devboard-postgres-1 psql -U devboard -d devboard -Atc \
  "select count(*) from projects; select count(*) from project_memory_entries; select count(*) from wiki_pages;"
```

- [ ] **Step 5: Commit the backend change**

```bash
git add app/Http/Controllers/Hades/MemorySearchController.php tests/Feature/Hades/HadesM3SharedMemoryTest.php
git commit -m "fix(hades): exclude raw chunks before memory ranking"
```

---

### Task 2: Add bounded wiki fallback to automatic provider prefetch

**Files:**
- Modify: `plugins/memory/hades_backend/__init__.py:673-718`
- Test: `tests/agent/test_hades_backend_memory_provider.py`

**Interfaces:**
- Consumes: `HadesBackendMemoryProvider._backend_memory_search(...)` and `_format_backend_prefetch(response)`
- Produces: `_has_usable_prefetch_items(response: dict[str, Any]) -> bool`; `prefetch()` performs at most one wiki fallback.

- [ ] **Step 1: Write failing provider tests**

Add two tests using the existing linked-provider fixtures.

```python
def test_prefetch_falls_back_to_wiki_when_broad_search_only_omits_raw_chunks(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)
    calls = []

    def search(**kwargs):
        calls.append(kwargs)
        if kwargs["domain"] == "all":
            return {"items": [], "raw_chunks_omitted": 200}, None
        return {
            "items": [{"domain": "wiki", "summary": "Entity index", "payload_excerpt": "Workers and departments"}],
            "raw_chunks_omitted": 0,
            "version": "wiki-v1",
        }, None

    monkeypatch.setattr(provider, "_backend_memory_search", search)

    context = provider.prefetch("Quali sono le entità principali?")

    assert [call["domain"] for call in calls] == ["all", "wiki"]
    assert "Entity index" in context


def test_prefetch_does_not_query_wiki_when_broad_search_is_usable(monkeypatch, tmp_path):
    provider = _create_linked_provider(monkeypatch, tmp_path)
    calls = []

    def search(**kwargs):
        calls.append(kwargs)
        return {"items": [{"domain": "project_memory", "summary": "Compact fact"}], "raw_chunks_omitted": 0}, None

    monkeypatch.setattr(provider, "_backend_memory_search", search)

    assert "Compact fact" in provider.prefetch("fact")
    assert [call["domain"] for call in calls] == ["all"]
```

- [ ] **Step 2: Run both tests and verify RED**

```bash
pytest -q tests/agent/test_hades_backend_memory_provider.py \
  -k 'prefetch_falls_back_to_wiki or prefetch_does_not_query_wiki'
```

Expected: the fallback test FAILS because `prefetch()` returns immediately after the empty broad backend response.

- [ ] **Step 3: Implement minimal fallback**

```python
def _has_usable_prefetch_items(response: dict[str, Any]) -> bool:
    return any(
        isinstance(item, dict) and not bool(item.get("raw_chunk"))
        for item in (response.get("items") or [])
    )
```

Update `prefetch()` so it formats and returns the broad response only when usable. Otherwise perform one live wiki search:

```python
if backend_result is not None and _has_usable_prefetch_items(backend_result):
    return _format_backend_prefetch(backend_result)

wiki_result, _wiki_error = self._backend_memory_search(
    query=query,
    domain="wiki",
    filters={},
    limit=AUTO_PREFETCH_LIMIT,
    include_raw_chunks=False,
)
if wiki_result is not None and _has_usable_prefetch_items(wiki_result):
    return _format_backend_prefetch(wiki_result)
```

After an empty/failed wiki fallback, continue to the existing local snapshot path rather than returning the empty broad response.

- [ ] **Step 4: Run provider regression tests**

```bash
pytest -q tests/agent/test_hades_backend_memory_provider.py -k 'prefetch or raw_chunk'
```

Expected: PASS.

- [ ] **Step 5: Commit the provider change**

```bash
git add plugins/memory/hades_backend/__init__.py tests/agent/test_hades_backend_memory_provider.py
git commit -m "fix(hades): fall back to wiki during automatic recall"
```

---

### Task 3: Make auth quarantine credential-generation aware

**Files:**
- Modify: `hermes_cli/hades_backend_sync.py:35-450`
- Test: `tests/hermes_cli/test_hades_backend_sync_runner.py:2488-2650`

**Interfaces:**
- Consumes: `BackendAgent.token_env_key`, `runtime.agent_token(agent)`, and `hermes_cli.config.load_env()`
- Produces: `_credential_fingerprint(secret: str) -> str | None` and `_persisted_credential_fingerprint(agent: db.BackendAgent) -> str | None`.

- [ ] **Step 1: Write the stale-token failing test**

Extend the existing auth-quarantine fixture with a client that always returns `401`. Make the runtime credential used at sync start be `old-token`, while the persisted `.env` lookup at final classification returns `new-token`.

```python
def test_sync_does_not_quarantine_binding_after_persisted_token_rotation(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import hades_backend_db as hdb
    from hermes_cli import hades_backend_runtime as runtime
    from hermes_cli import hades_backend_sync
    from hermes_cli.hades_backend_client import HadesBackendError

    workspace = tmp_path / "repo"
    workspace.mkdir()
    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_auth",
            project_id="project_auth",
            base_url="https://backend.invalid",
            label="auth",
            token_env_key="TOKEN_AUTH",
            capabilities={"sync_git_tree": False, "populate_backend_ast": False},
        )
        hdb.upsert_workspace_binding(
            conn,
            project_id="project_auth",
            agent_id="agent_auth",
            local_project_id="local_auth",
            workspace_fingerprint="wf_auth",
            display_path="~/repo",
            repo_root=str(workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id="binding_auth",
        )

    class UnauthorizedClient:
        def capabilities(self):
            raise HadesBackendError("unauthorized", status_code=401)

        memory_snapshot = capabilities
        list_inbox = capabilities
        pull_jobs = capabilities

    monkeypatch.setattr(runtime, "agent_token", lambda selected: "old-token")
    monkeypatch.setattr(
        hades_backend_sync,
        "_persisted_credential_fingerprint",
        lambda selected: hades_backend_sync._credential_fingerprint("new-token"),
    )

    result = hades_backend_sync.run_backend_sync(client_factory=UnauthorizedClient, quiet=True)

    with hdb.connect_closing() as check:
        stored = hdb.get_binding_for_backend_id(check, "binding_auth")
        health = hdb.get_route_auth_health(check, project_id="project_auth", agent_id="agent_auth")
        last_error = hdb.get_sync_state(check, "last_sync_error")

    assert result.summary["stale_auth_routes"] == 1
    assert result.summary["auth_failed_routes"] == 0
    assert stored.status == "linked"
    assert health is None
    assert last_error is None
```

Retain the existing unchanged-token three-cycle test as the fail-closed contract.

- [ ] **Step 2: Run stale and unchanged credential tests and verify RED**

```bash
pytest -q tests/hermes_cli/test_hades_backend_sync_runner.py \
  -k 'persisted_token_rotation or auth_quarantine_counts_one_401'
```

Expected: the rotation test FAILS because the old process increments route auth health and leaves `last_sync_error`.

- [ ] **Step 3: Implement one-way credential fingerprints**

```python
def _credential_fingerprint(secret: str) -> str | None:
    value = str(secret or "")
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if value else None


def _persisted_credential_fingerprint(agent: db.BackendAgent) -> str | None:
    from hermes_cli.config import load_env

    return _credential_fingerprint(load_env().get(agent.token_env_key, ""))
```

When each agent client is first created, capture the fingerprint returned by `runtime.agent_token(sync_agent)` in `used_credential_fingerprints[agent_id]`. Extend each route observation with `unauthorized_errors: int` and increment it only for `HadesBackendError(status_code=401)`.

Immediately before `record_route_auth_cycle()`, compare the captured fingerprint with `_persisted_credential_fingerprint(route_agent)`. When both exist and differ:

```python
stale_credential = bool(
    observation["unauthorized"]
    and used_fingerprint
    and persisted_fingerprint
    and used_fingerprint != persisted_fingerprint
)
if stale_credential:
    stale_auth_routes += 1
    sync_errors = max(0, sync_errors - observation["unauthorized_errors"])
    continue
```

Add `stale_auth_routes` to the sync summary. Because stale-only errors are removed from `sync_errors`, the existing success cleanup clears `last_sync_error` and background backoff state. Missing fingerprints retain the existing fail-closed path.

- [ ] **Step 4: Run auth and sync regression suites**

```bash
pytest -q tests/hermes_cli/test_hades_backend_sync_runner.py \
  -k 'auth_quarantine or persisted_token_rotation or non_401'
pytest -q tests/hermes_cli/test_hades_backend_db.py -k 'route_auth_health'
```

Expected: PASS; unchanged credentials still quarantine on the third unauthorized cycle.

- [ ] **Step 5: Commit the auth guard**

```bash
git add hermes_cli/hades_backend_sync.py tests/hermes_cli/test_hades_backend_sync_runner.py
git commit -m "fix(hades): ignore stale credential auth failures"
```

---

### Task 4: Install and verify the complete behavior against Carnovali

**Files:**
- No new source files.
- Verify local checkout: `/Users/gabriele/Dev/Hephaistos`
- Verify linked project: `/Users/gabriele/Dev/sinervis/carnovali`
- Verify remote backend: `/home/ubuntu/dev-sandbox/backend`

**Interfaces:**
- Consumes: backend search fix, provider wiki fallback, credential-generation guard.
- Produces: installed Hades runtime with verified live recall and healthy sync.

- [ ] **Step 1: Run combined local tests**

```bash
pytest -q \
  tests/agent/test_hades_backend_memory_provider.py \
  tests/hermes_cli/test_hades_backend_sync_runner.py \
  tests/hermes_cli/test_hades_backend_db.py
```

Expected: PASS with no new warnings.

- [ ] **Step 2: Install the updated Hades checkout**

Use the repository's existing installer/update path, then verify the installed files report the new `main` revision. Do not run backend bootstrap and do not rotate credentials during installation.

- [ ] **Step 3: Verify live sync from Carnovali**

```bash
cd /Users/gabriele/Dev/sinervis/carnovali
hades backend sync
hades backend status --json
```

Expected: `degraded=false`, `last_error=null`, one linked binding, and 8,701 cached project-memory items.

- [ ] **Step 4: Verify automatic wiki prefetch**

Run a provider-level probe with the natural-language query:

```text
Quali sono le entità principali descritte nella wiki ENTITY_INDEX?
```

Expected: non-empty `Shared Hades project memory` context containing a wiki result such as `Entity index`, not the message that only raw chunks were omitted.

- [ ] **Step 5: Verify stale-process isolation**

In an isolated temporary `HERMES_HOME`, create the tested old-token/new-token condition and run one sync cycle. Expected: `stale_auth_routes=1`, the binding remains `linked`, and `last_sync_error` is absent. Do not rotate or invalidate the real Carnovali credentials for this verification.

- [ ] **Step 6: Final status and change review**

```bash
git status --short
git log --oneline -5
```

Confirm only the planned commits exist. Record backend and local commit IDs, exact test counts, live prefetch evidence, and final Hades status in the handoff.
