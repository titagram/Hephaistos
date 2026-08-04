---
name: hermes-memory-providers
description: Configure, enable, and verify external memory provider plugins in Hermes (supermemory, honcho, mem0, hindsight, ...) — provider switching, per-provider config files, API keys, connection probes, and E2E store/search/forget verification against SaaS or self-hosted backends.
---

# Hermes Memory Providers (supermemory, honcho, mem0, ...)

## When to use
- User asks to configure/enable/switch the Hermes memory provider (`memory.provider`), e.g. "abilita supermemory con provider locale".
- Setting up or troubleshooting any `plugins/memory/<name>/` provider plugin.
- Verifying a memory backend (self-hosted or SaaS) is reachable and actually working end-to-end.

## Key facts
- Providers are plugins under `$HERMES_HOME/hermes-agent/plugins/memory/<name>/` (each has `plugin.yaml`; the agent install doubles as the source repo — run Python with `sys.path.insert(0, <repo_root>)` and the venv python `$HERMES_HOME/hermes-agent/venv/bin/python`).
- Enable via `memory.provider: <name>` in `$HERMES_HOME/config.yaml` — set it with `hermes config set memory.provider <name>`.
- Per-provider config file lives at `$HERMES_HOME/<provider>.json`; secrets go in `$HERMES_HOME/.env` (mode 0600) as `PROVIDER_API_KEY=...`.
- The provider is loaded at session start → config changes take effect in the NEXT session, not the current one (say so to the user).

## Steps (supermemory example)
1. `hermes memory status` → shows Configured/Effective provider, installed plugins and availability. This is the source of truth (see pitfall: the setup help text lies).
2. `hermes config set memory.provider supermemory`.
3. Write `$HERMES_HOME/supermemory.json` (mode 0600, JSON): `base_url`, `container_tag` (supports `{identity}` template), `auto_recall`, `auto_capture`, `max_recall_results`, `profile_frequency`, `capture_mode`, `search_mode`, `entity_context`, `api_timeout`. `base_url` must be a bare HTTPS origin — no path, query, credentials or fragment (plugin validates and raises `ValueError` otherwise). Example: `{"base_url": "https://persephone.cc", "container_tag": "hermes"}`.
4. Add `SUPERMEMORY_API_KEY=<key>` to `$HERMES_HOME/.env`, then `chmod 600`.
5. Verify connectivity: run `scripts/verify_supermemory.py` from the skill (`--hermes-home` defaults to `~/.hermes`), or the inline probe recipe in `references/supermemory-selfhosted.md`. Expect `✓ Connected · container: hermes · N profile facts · auto_recall on · auto_capture on`.
6. E2E (optional, `--e2e` flag): store → poll ~30-40s for async ingestion → search → forget with the MEMORY ENTRY id → delete the document. See Pitfalls for id types.

## Pitfalls
- **Namespace-package trap**: `import supermemory` can succeed as an EMPTY namespace package (`__file__` is None) when `PYTHONPATH`/cwd contains a `supermemory/` directory without `__init__.py` (e.g. a monorepo checkout) — this masks a missing SDK. Always check `supermemory.__file__` (or the plugin's own probe) instead of trusting `import` success.
- **Hermes venv has no pip** (`python -m pip` → No module named pip). Install with uv: `uv pip install --python $HERMES_HOME/hermes-agent/venv/bin/python supermemory`. The supermemory plugin attempts a lazy install via `tools.lazy_deps.ensure("memory.supermemory")`, but install it explicitly to be sure.
- **`hermes memory setup --help` provider list is hardcoded and stale** (doesn't list supermemory even when installed). Use `hermes memory status` / discovery, not the help text.
- **Async ingestion**: a stored document becomes a searchable memory entry only ~30-40s later (AI summarization rewrites/cleans content). Searching immediately returns hits with empty `memory` fields.
- **ID types**: `add_memory`/`documents.add` returns a DOCUMENT id; `forget` needs the MEMORY ENTRY id from search/list results. Passing the document id → 404 "Memory not found".
- **SDK signature drift**: `documents.delete(id=...)` in SDK 3.56.0 (older code may show `document_id=`). Check `inspect.signature()` before guessing kwargs.
- **Hybrid search noise**: results mix real memory entries with document/chunk hits whose `memory` field is empty — filter empty `memory` when formatting recall context.
- **Self-hosted behind traefik**: `/` and `/v4/reference` (UI/docs) require BasicAuth; API `/v3`, `/v4`, `/files` use `Authorization: Bearer <key>`; `/v3/openapi` returns 200 unauthenticated → good liveness probe for the whole stack.

## Support files
- `references/supermemory-selfhosted.md` — self-hosted server deployment shape, API surface, inline probe recipe, observed behaviors.
- `scripts/verify_supermemory.py` — re-runnable connection probe + optional E2E store/search/forget cycle.
