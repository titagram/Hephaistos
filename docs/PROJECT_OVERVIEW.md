# Project Overview

## Fatti Verificati

- Nome package Python: `hades-agent` in `pyproject.toml`.
- Root locale analizzata: `/Users/gabriele/Dev/Hephaistos`.
- Il progetto e esistente, non nuovo: contiene core Python, app TypeScript,
  Docker, CI, plugin, skill e test.
- Descrizione da `README.md`: Hades Agent e un agente AI personale con CLI,
  gateway messaging, TUI, desktop app, memoria, skill, subagent e cron.

## Parti Principali

- Core agente: `run_agent.py`, `agent/`, `model_tools.py`, `toolsets.py`.
- CLI classica e subcommand: `cli.py`, `hermes_cli/main.py`,
  `hermes_cli/subcommands/`.
- Tool model-side: `tools/registry.py`, `tools/*.py`, `tools/environments/`.
- Gateway messaging: `gateway/`, `gateway/platforms/`, `plugins/platforms/`.
- Cron e automazioni: `cron/`, `tools/cronjob_tools.py`.
- TUI: `ui-tui/` (Ink/React) e `tui_gateway/` (backend JSON-RPC Python).
- Dashboard web: `hermes_cli/web_server.py` e `web/`.
- Desktop Electron: `apps/desktop/`, con codice condiviso in `apps/shared/`.
- ACP/editor integration: `acp_adapter/`.
- Plugin e skill: `plugins/`, `skills/`, `optional-skills/`,
  `optional-mcps/`.
- Documentazione sito: `website/docs/`.

## Domini Principali

- Conversazioni e prompt caching.
- Tool discovery, toolset e MCP.
- Provider/modelli e credenziali.
- Session store SQLite e ricerca FTS5.
- Gateway multi-piattaforma e delivery.
- Runtime terminale locale/remoto/container/cloud.
- Scheduler cron e automazioni.
- UI TUI, dashboard e desktop.
- Plugin, skill e estensioni.

## Stabilita Osservata

- Stabile/load-bearing: `AGENTS.md`, `SECURITY.md`, `pyproject.toml`,
  `uv.lock`, `scripts/run_tests.sh`, `tools/registry.py`, `toolsets.py`,
  `hermes_state.py`.
- Ampio e da toccare con cautela: `run_agent.py`, `cli.py`,
  `hermes_cli/web_server.py`, `gateway/run.py`, `hermes_cli/kanban_db.py`.
- Sperimentale o da verificare per singolo task: plugin specifici in
  `plugins/`, skill opzionali, integrazioni provider meno usate, dashboard
  plugin dinamici.

## Da Verificare

- Stato runtime locale (`.venv`, credenziali, Docker daemon) prima di eseguire
  comandi che dipendono dall'ambiente.
- Copertura esatta di ogni plugin o skill opzionale: la repo ne contiene molti
  e alcuni sono attivati solo da configurazione o credenziali.
