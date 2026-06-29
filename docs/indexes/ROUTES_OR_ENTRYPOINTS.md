# Routes And Entrypoints

## Python Entrypoint

Fonte: `pyproject.toml`.

| Comando | Target |
| --- | --- |
| `hades` | `hermes_cli.main:main` |
| `hades-agent` | `run_agent:main` |
| `hades-acp` | `acp_adapter.entry:main` |

## CLI Top-Level

Fonti: `hermes_cli/main.py`, `hermes_cli/subcommands/`.

Subcommand built-in verificati in `_BUILTIN_SUBCOMMANDS` includono:
`acp`, `auth`, `backup`, `bundles`, `checkpoints`, `claw`, `completion`,
`computer-use`, `config`, `cron`, `curator`, `dashboard`, `serve`, `debug`,
`doctor`, `dump`, `fallback`, `gateway`, `hooks`, `import`, `insights`, `gui`,
`desktop`, `kanban`, `login`, `logout`, `logs`, `lsp`, `mcp`, `memory`,
`migrate`, `moa`, `model`, `pairing`, `pets`, `plugins`, `portal`,
`postinstall`, `profile`, `project`, `proxy`, `prompt-size`, `send`,
`sessions`, `setup`, `skills`, `slack`, `status`, `tools`, `uninstall`,
`update`, `version`, `webhook`, `whatsapp`, `whatsapp-cloud`, `chat`,
`secrets`, `security`.

Comando di verifica:

```bash
rg -n 'add_parser\\(|build_.*parser|_BUILTIN_SUBCOMMANDS' hermes_cli/main.py hermes_cli/subcommands
```

## Slash Commands

Fonte: `hermes_cli/commands.py` `COMMAND_REGISTRY`.

Categorie osservate:
- Session: `/new`, `/retry`, `/undo`, `/compress`, `/background`, `/goal`,
  `/status`, `/resume`, ecc.
- Configuration: `/model`, `/personality`, `/reasoning`, `/voice`, ecc.
- Tools & Skills: `/tools`, `/skills`, `/memory`, `/cron`, `/kanban`, ecc.
- Info/Exit: `/help`, `/usage`, `/version`, `/quit`, ecc.

Regola: aggiungere o modificare slash command solo nel registry centrale e nel
handler corrispondente.

## Dashboard FastAPI

Fonte: `hermes_cli/web_server.py` e router inclusi.

Route group osservati:
- `/api/media`
- `/api/files/*`, `/api/fs/*`
- `/api/git/*`
- `/api/status`, `/api/system/stats`
- `/api/curator/*`, `/api/portal`
- `/api/ops/*`
- `/api/gateway/*`
- `/api/hades/update*`
- `/api/audio/*`
- `/api/actions/{name}/status`
- `/api/sessions*`
- `/api/logs`
- `/api/cron/*`
- `/api/mcp/*`
- `/api/pairing*`
- `/api/webhooks*`
- `/api/credentials/pool*`
- `/api/memory*`
- `/api/skills*`
- `/api/tools/*`
- `/api/config*`, `/api/env*`, `/api/providers/*`
- `/api/messaging/*`
- `/api/profiles*`
- `/api/analytics/*`
- `/api/dashboard/*`
- `/dashboard-plugins/{plugin_name}/{file_path:path}`

WebSocket osservati:
- `/api/pty`
- `/api/ws`
- `/api/pub`
- `/api/events`
- `tui_gateway/ws.py` registra anche `/api/ws` per gateway detach.

Comando di verifica:

```bash
rg -n '@(app|router)\\.(get|post|put|patch|delete|websocket)' hermes_cli/web_server.py hermes_cli/dashboard_auth/routes.py hermes_cli/memory_oauth.py tui_gateway/ws.py
```

## Dashboard React Routes

Fonte: `web/src/App.tsx`.

Route built-in osservate:
`/`, `/chat`, `/sessions`, `/files`, `/analytics`, `/models`, `/logs`,
`/cron`, `/skills`, `/plugins`, `/mcp`, `/pairing`, `/channels`, `/webhooks`,
`/system`, `/profiles`, `/profiles/new`, `/config`, `/env`, `/docs`.

Nota: `/chat` puo essere host persistente e non sempre un route component
normale; `web/src/App.tsx` spiega che mantiene PTY/WebSocket/xterm montati.

## Desktop Routes

Fonte: `apps/desktop/src/app/routes.ts`.

Route osservate:
- `/` chat nuova o sessione.
- `/settings`
- `/command-center`
- `/skills`
- `/messaging`
- `/artifacts`
- `/cron`
- `/profiles`
- `/agents`
- `/<sessionId>` per session route non riservata.

## Gateway Platform Entrypoint

Fonte: `gateway/run.py`, `gateway/platforms/`, `plugins/platforms/`.

La lista platform e mista: alcuni adapter sono in `gateway/platforms/`, molti
sono plugin in `plugins/platforms/`. Da completare per task specifici con:

```bash
find gateway/platforms plugins/platforms -maxdepth 2 -type f | sort
rg -n 'def register\\(ctx\\)|register_platform|Platform' gateway plugins/platforms -g '*.py'
```

## Da Completare

- Elenco route dashboard completo con metodo/handler/nome e auth requirements.
- Route/plugin dinamiche montate da `app.include_router(router, prefix=...)` in
  `hermes_cli/web_server.py`.
- Elenco platform adapter completo per plugin installabili esterni.
