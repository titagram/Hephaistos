# Coding Style

Questo documento descrive lo stile reale osservato, non uno stile ideale.

## Python

- Naming: funzioni e variabili in `snake_case`; classi in `PascalCase`.
  Evidenza: `SessionDB`, `discover_builtin_tools`, `build_gateway_parser`.
- File grandi esistenti usano sezioni commentate con header; non introdurre
  refactor ampi senza richiesta. Evidenza: `cli.py`, `run_agent.py`,
  `hermes_cli/web_server.py`.
- I subcommand nuovi tendono a vivere in `hermes_cli/subcommands/<name>.py`
  con funzione `build_<name>_parser(...)`. Evidenza:
  `hermes_cli/subcommands/*.py`.
- I tool si registrano a livello modulo con `registry.register(...)`.
  Evidenza: `tools/registry.py`, `tools/file_tools.py`.
- I check di disponibilita tool usano `check_fn`, spesso con credenziali o
  runtime gating. Evidenza: `tools/registry.py`, `toolsets.py`.
- I/O testuale deve specificare `encoding=` fuori dai path ignorati. Evidenza:
  `pyproject.toml` abilita `ruff` rule `PLW1514`.
- Error handling: molti confini runtime usano fail-soft con logging su
  operazioni opzionali, ma fail-closed sui segreti multiplexati. Evidenza:
  `agent/secret_scope.py`.

## TypeScript / React

- Workspaces npm: root `package.json` include `apps/*`, `ui-tui`,
  `ui-tui/packages/*`, `web`.
- Desktop: React/Electron, nanostores, assistant-ui, Vite. Evidenza:
  `apps/desktop/package.json`.
- Web dashboard: React/Vite, React Router, xterm, shared package. Evidenza:
  `web/package.json`, `web/src/App.tsx`.
- TUI: Ink/React, nanostores, Vitest. Evidenza: `ui-tui/package.json`.
- Route desktop centralizzate in `apps/desktop/src/app/routes.ts`.
- Dashboard route React integrate in `web/src/App.tsx`; plugin tab dinamici si
  aggiungono da manifest.

## Test

- Python: preferire `scripts/run_tests.sh <path>` rispetto a `pytest` diretto.
- TS: usare script workspace, per esempio:
  `npm run --prefix apps/desktop typecheck`,
  `npm run --prefix web test`,
  `npm run --prefix ui-tui lint`.
- Per regressioni su configurazione, filesystem, security boundary o remote
  backend, evitare solo mock quando un test reale con temp `HERMES_HOME` e
  import reali e fattibile.

## Pattern Da Copiare

- Parser CLI modulari in `hermes_cli/subcommands/`.
- Registry self-registering per tool e plugin.
- Config in `config.yaml`, segreti in `.env`.
- Scrubbing env centralizzato per subprocess.
- Test che verificano invarianti, non conteggi volatili.

## Pattern Da Evitare

- Nuovi core tools senza passare dalla Footprint Ladder in `AGENTS.md`.
- Lettura raw di segreti da `os.environ` in path multiplexati.
- `os.environ.copy()` per subprocess senza scrubber esistente.
- Docs che dicono "imposta un env var" per configurazione non segreta.
- Dashboard React che reimplementa la chat TUI del dashboard.
