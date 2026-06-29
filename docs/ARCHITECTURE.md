# Architecture

Ogni affermazione sotto deriva da file osservati nel checkout locale. Quando
una lista non e esaustiva, e marcata come tale.

## Layer Osservati

1. Entry point e orchestrazione CLI
   - Evidenza: `pyproject.toml` espone `hades`,
     `hades-agent`, `hades-acp`.
   - Evidenza: `hermes_cli/main.py` costruisce parser argparse e registra
     subcommand; `cli.py` contiene la CLI interattiva classica.

2. Core conversazionale
   - Evidenza: `run_agent.py` contiene `AIAgent` e `main()`.
   - Evidenza: `AGENTS.md` descrive `AIAgent.run_conversation()` come loop
     sincrono con tool calls.

3. Tool registry e toolsets
   - Evidenza: `tools/registry.py` importa moduli `tools/*.py` con chiamate
     top-level a `registry.register(...)`.
   - Evidenza: `model_tools.py` chiama `discover_builtin_tools()`.
   - Evidenza: `toolsets.py` definisce `_HERMES_CORE_TOOLS` e toolset.

4. Stato persistente
   - Evidenza: `hermes_state.py` definisce `SessionDB`, `SCHEMA_SQL`,
     tabelle `sessions`, `messages`, `state_meta`, `compression_locks` e FTS5.
   - Evidenza: `hermes_cli/kanban_db.py` definisce schema kanban separato.

5. Superfici UI/API
   - CLI/TUI: `cli.py`, `ui-tui/`, `tui_gateway/`.
   - Dashboard: `hermes_cli/web_server.py`, `web/`.
   - Desktop: `apps/desktop/`, `apps/shared/`.
   - ACP: `acp_adapter/`.

6. Gateway, delivery e automazioni
   - Evidenza: `gateway/run.py`, `gateway/platforms/`, `plugins/platforms/`.
   - Evidenza: `cron/jobs.py` salva job in `$HADES_HOME/cron/jobs.json` e
     output in `$HADES_HOME/cron/output/...`.
   - Evidenza: `cron/scheduler_provider.py` separa trigger provider da
     esecuzione/delivery.

## Flusso Dati Principale

Input utente o evento esterno -> CLI/gateway/TUI/desktop/dashboard -> runtime
provider e `AIAgent` -> messaggi e tool schemas -> provider LLM -> tool calls
via `model_tools.handle_function_call()` -> tool handler registrato -> stato,
file, rete o delivery -> risposta finale -> session store/log/delivery.

Evidenze:
- `AGENTS.md` descrive il loop con `client.chat.completions.create(...)` e
  `handle_function_call(...)`.
- `tools/registry.py` e `model_tools.py` descrivono la catena
  `tools/registry.py -> tools/*.py -> model_tools.py -> run_agent.py`.

## Entrypoint Osservati

- `hades`: `hermes_cli.main:main`.
- `hades-agent`: `run_agent:main`.
- `hades-acp`: `acp_adapter.entry:main`.
- Dashboard HTTP/FastAPI: `hermes_cli/web_server.py`.
- Gateway messaging: `gateway/run.py`.
- TUI backend: `tui_gateway/entry.py` e `tui_gateway/server.py`.

## Pattern Architetturali

- Registri centrali: `COMMAND_REGISTRY`, `ToolRegistry`, plugin `register(ctx)`.
- Availability gating: tool `check_fn` con cache TTL in `tools/registry.py`.
- Config/state profile-aware: `hermes_constants.py` e `agent/secret_scope.py`.
- Subcommand modulari: molti gruppi CLI stanno in `hermes_cli/subcommands/`.
- Plugin edge-first: nuove capability specialistiche appartengono ai plugin o
  MCP, non al core tool schema.

## Punti Fragili

- `run_agent.py`, `cli.py`, `hermes_cli/web_server.py` e `gateway/run.py` sono
  file molto grandi con molte responsabilita storiche.
- La route dashboard in `hermes_cli/web_server.py` e ampia; modifica piccola
  puo impattare file manager, git review, cron, profili, plugin o auth.
- `hermes_cli/kanban_db.py` contiene schema e logica estesi: leggere la zona
  specifica prima di cambiare migrazioni.
- Prompt caching e toolset stabili sono vincoli di costo e correttezza, non
  dettagli implementativi.
