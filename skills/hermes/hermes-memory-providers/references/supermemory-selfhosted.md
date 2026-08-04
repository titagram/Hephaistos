# Supermemory self-hosted — session notes (2026-08)

## Deployment shape
- Self-hosted supermemory **server binary v0.0.6** (Hono-based "native Supermemory API") behind Traefik on a VPS.
- Traefik routing: UI/docs (`/`, `/v4/reference`) behind BasicAuth (`www-authenticate: Basic realm="traefik"`); API paths `/v3`, `/v4`, `/files` use Bearer auth; `/v3/openapi` returns 200 unauthenticated → liveness probe.
- Client API key: Bearer key stored in the deploy's `.env.runtime` as `SUPERMEMORY_API_KEY`; API-key metadata can scope container tags (`sm_permission`, `containerTags`).
- Reference deployment: `supermemory/deploy/codex/` (bootstrap.sh, smoke.sh, compose.yaml, `.env.runtime`), `SUPERMEMORY_HOSTNAME` holds the public hostname.

## API surface (server v0.0.6)
- v3: `/documents` (add/list/delete/bulk/processing), `/search`, `/settings`, `/analytics`, `/auth`, `/projects`, `/container-tags`, `/openapi`.
- v4: `/memories` (POST `/` create, DELETE `/` forget, `/forget-matching`, `/list`), `/profile` (POST `/`), `/search` (POST `/`), `/conversations`, `/reference`, `/openapi`.
- Sanity probes: `POST /v4/memories/list {"containerTags":["hermes"],"limit":5}` → `{"memoryEntries":[...],"pagination":{...}}`.

## Hermes plugin wiring
- Plugin: `$HERMES_HOME/hermes-agent/plugins/memory/supermemory/` — `plugin.yaml` (`pip_dependencies: [supermemory]`), `register(ctx)` → `ctx.register_memory_provider(SupermemoryMemoryProvider())`.
- Config: `$HERMES_HOME/supermemory.json`; `base_url` normalized to canonical HTTPS origin (IDNA-lowercased host, optional port; rejects paths/creds/query/fragment, IPv6 requires brackets).
- Client (`_SupermemoryClient`): `add_memory` → SDK `documents.add`; `search_memories(q, container_tag, limit, search_mode)` → `search.memories` (hybrid default); `get_profile` → `profile`; `forget_memory` → `memories.forget`.
- Lazy SDK install: `_SupermemoryClient.__init__` calls `tools.lazy_deps.ensure("memory.supermemory", prompt=False)`.

## Inline probe recipe
```bash
cd $HERMES_HOME/hermes-agent
venv/bin/python -c "
import sys; sys.path.insert(0, '.')
from plugins.memory.supermemory import _probe_supermemory_connection, _format_connection_summary
key = [l.split('=',1)[1].strip() for l in open('$HERMES_HOME/.env') if l.startswith('SUPERMEMORY_API_KEY=')][0]
print(_format_connection_summary(_probe_supermemory_connection(key, '$HERMES_HOME')))"
```
→ `✓ Connected · container: hermes · 0 profile facts · auto_recall on · auto_capture on`

## Observed behaviors
- `add_memory` → document created immediately (returns document id); memory entry appears ~30-40 s later after async AI ingestion (content gets rewritten/cleaned, e.g. "Test E2E: ..." → "The Hermes supermemory plugin is reachable on persephone.cc.").
- Search before ingestion completes: hit exists but `memory` field empty (document/chunk hit, sim ~0.89).
- `forget_memory` with document id → 404 "Memory not found"; must use the memory entry id (from search/list).
- SDK 3.56.0: `documents.delete(id=...)` (NOT `document_id=`); `memories.forget(container_tag=..., id=...)`.
- `hermes memory status` → `Configured: supermemory / Effective: supermemory / Plugin: installed ✓ / Status: available ✓`; summary line comes from `_format_connection_summary`.
- `hermes memory setup --help` provider list is hardcoded/stale (no supermemory) — ignore it.
- Venv has no pip → `uv pip install --python $HERMES_HOME/hermes-agent/venv/bin/python supermemory`.
- Namespace-package trap: with cwd/PYTHONPATH containing a `supermemory/` dir without `__init__.py`, `import supermemory` succeeds with `__file__ is None` while the SDK is actually missing.
