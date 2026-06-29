# Side Effects

## File System

Superfici osservate:
- File/session state: `hermes_state.py`, `hermes_cli/projects_db.py`,
  `hermes_cli/kanban_db.py`.
- Cron job/output: `cron/jobs.py`.
- File manager dashboard: `/api/files/*` e `/api/fs/*` in
  `hermes_cli/web_server.py`.
- Tool file operations: `tools/file_tools.py`.
- Checkpoint store: `tools/checkpoint_manager.py`, `hermes_cli/checkpoints.py`.

Regola: prima di cambiare path state/profile, leggere `hermes_constants.py`.

## Subprocess

Superfici osservate:
- Terminal backend locale/remoto/container: `tools/environments/`.
- Code execution: `tools/code_execution_tool.py`.
- TUI/desktop/backend process spawn: `hermes_cli/main.py`,
  `apps/desktop/electron/` (da verificare quando si tocca desktop spawn).
- Cron pre-run scripts e no-agent jobs: `cron/scheduler.py`.

Guardrail osservati:
- `tools.environments.local.hades_subprocess_env()`.
- `_sanitize_subprocess_env(...)` in `tools/environments/local.py`.
- `_scrub_child_env(...)` in `tools/code_execution_tool.py`.
- `python scripts/check-windows-footguns.py --all`.

## Network / HTTP / WebSocket

Superfici osservate:
- Dashboard FastAPI: `hermes_cli/web_server.py`.
- Auth dashboard: `hermes_cli/dashboard_auth/routes.py`.
- Gateway adapters: `gateway/`, `plugins/platforms/`.
- Browser/CDP: `tools/browser_tool.py`, `tools/browser_cdp_tool.py`,
  `tools/browser_supervisor.py`.
- Web search/extract providers: `tools/web_tools.py`, `plugins/web/*`.
- TUI/detach WebSocket: `tui_gateway/ws.py`, `hermes_cli/web_server.py`.

## Delivery / Messaging

Superfici osservate:
- `tools/send_message_tool.py`.
- `cron/scheduler.py` `_deliver_result`.
- Gateway platform adapters in `gateway/platforms/` e `plugins/platforms/`.

Regola: adapter network-exposed richiedono allowlist come da `SECURITY.md`.

## Cron / Scheduling

Fonti:
- `cron/jobs.py`
- `cron/scheduler.py`
- `cron/scheduler_provider.py`
- `tools/cronjob_tools.py`
- `hermes_cli/subcommands/cron.py`

Side effect:
- crea/aggiorna job;
- esegue agent turn o script;
- scrive output;
- consegna messaggi;
- puo avviare scheduler provider esterno via plugin.

## Git / Worktree

Superfici osservate:
- Dashboard git review API in `hermes_cli/web_server.py`.
- Desktop right sidebar/review in `apps/desktop/src/app/right-sidebar/`.
- CLI/project/kanban worktree logic in `hermes_cli/projects_cmd.py` e
  `hermes_cli/kanban.py`.

Non eseguire revert/reset/clean distruttivi senza richiesta esplicita.

## Da Completare

- Lista completa di ogni `subprocess.run/Popen` con scopo e env.
- Lista completa di ogni route che scrive file o modifica git.
- Side effect specifici per plugin installabili dall'utente.
