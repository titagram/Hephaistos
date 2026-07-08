# Runtime

## Prerequisiti Verificati

- Python: `>=3.11,<3.14` da `pyproject.toml`.
- Node: root `package.json` richiede `>=20.0.0`; CI typecheck usa Node 22.
- Package Python: `uv` con `uv.lock`.
- Package Node: `npm` con `package-lock.json` e workspaces.
- `ripgrep` e `ffmpeg` sono installati dal Dockerfile e usati dagli strumenti.

## Setup Python Locale

Comando CI osservato:

```bash
uv sync --locked --python 3.11 --extra all --extra dev
```

Attivazione venv osservata in `AGENTS.md` e `scripts/run_tests.sh`:

```bash
source .venv/bin/activate
```

Fallback: `venv` o `$HOME/.hermes/hades-agent/venv`.

## Setup Node

Install root/workspaces:

```bash
npm ci
```

Script root osservati:

```bash
npm run install:root
npm run install:web
npm run install:tui
npm run install:desktop
```

## Avvio Locale

Da `README.md`:

```bash
hades
hades model
hades tools
hades setup
hades gateway
hades doctor
```

Da `hermes_cli/subcommands/dashboard.py` e `hermes_cli/main.py`:

```bash
hades dashboard --no-open
hades serve
```

Da verificare localmente: credenziali, `HERMES_HOME`, `.venv`, Node install e
porte libere.

## Docker

Comando commentato in `docker-compose.yml`:

```bash
HERMES_UID=$(id -u) HERMES_GID=$(id -g) docker compose up -d
```

Fatti verificati:
- Servizi compose: `gateway`, `dashboard`.
- Volume: `~/.hermes:/opt/data`.
- Dashboard command: `["dashboard", "--host", "127.0.0.1", "--no-open"]`.
- `Dockerfile` usa uv, Node 22, Debian 13.4, s6-overlay e utente non-root
  `hades`.

Regola operativa: non cambiare bind dashboard/API verso `0.0.0.0` senza
richiesta esplicita e piano auth/network.

## Configurazione E Segreti

- Config utente: `$HERMES_HOME/config.yaml`, helper in `hermes_constants.py`.
- Segreti: `$HERMES_HOME/.env`, esempio in `.env.example`.
- `SECURITY.md` e `AGENTS.md` indicano: `.env` per credenziali, `config.yaml`
  per comportamento.
- Multiprofilo: `agent/secret_scope.py` evita leakage cross-profile.

## Database/State

- Sessioni: SQLite gestito da `hermes_state.py`.
- Cron: JSON sotto `$HERMES_HOME/cron/jobs.json` e output Markdown sotto
  `$HERMES_HOME/cron/output/`.
- Kanban: SQLite/schema separato in `hermes_cli/kanban_db.py`.
- Progetti desktop/dashboard: `hermes_cli/projects_db.py`.

## Problemi Noti / Cautela

- Test e runtime possono dipendere da credenziali; CI azzera `OPENROUTER_API_KEY`,
  `OPENAI_API_KEY`, `NOUS_API_KEY` nei test.
- Docker bypassando `/init` salta setup s6-overlay; `docker-compose.yml` avvisa
  di non sovrascrivere entrypoint senza mantenerlo.
- Setup locale senza `.venv` fa fallire `scripts/run_tests.sh`.
