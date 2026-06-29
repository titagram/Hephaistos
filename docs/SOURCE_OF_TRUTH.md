# Source Of Truth

Quando due fonti divergono, usa questa priorita e registra la divergenza nella
risposta o nel logbook.

## Priorita

1. Codice eseguito e test vicino al comportamento.
2. Configurazione package/CI che decide install, test e build.
3. Documentazione guida del progetto (`AGENTS.md`, `SECURITY.md`, `README.md`).
4. Documentazione derivata in `docs/` e `website/docs/`.
5. Inferenze o memoria conversazionale: mai trattarle come fonte finale.

## Fonti Verificate

| Area | Fonte primaria | Evidenza |
| --- | --- | --- |
| Python package/runtime | `pyproject.toml`, `uv.lock` | Nome, Python range, deps, scripts, pytest/ruff/ty config |
| Node workspaces | `package.json`, `package-lock.json`, workspace `package.json` | Script npm e engines |
| Test Python | `scripts/run_tests.sh`, `.github/workflows/tests.yml` | Runner canonico e setup CI |
| Lint Python | `pyproject.toml`, `.github/workflows/lint.yml` | `ruff check .`, `PLW1514`, Windows footguns |
| TypeScript CI | `.github/workflows/typecheck.yml` | Workspace typecheck e desktop build |
| CLI top-level | `hermes_cli/main.py`, `hermes_cli/subcommands/` | Parser argparse e command functions |
| Slash commands | `hermes_cli/commands.py` | `COMMAND_REGISTRY` |
| Tool registry | `tools/registry.py`, `model_tools.py`, `toolsets.py` | Tool discovery, tool schemas, core tool list |
| Session DB | `hermes_state.py` | `SCHEMA_SQL`, FTS5, migrations |
| Kanban DB | `hermes_cli/kanban_db.py` | Kanban tables, indexes, migrations |
| Dashboard API | `hermes_cli/web_server.py`, `hermes_cli/dashboard_auth/routes.py`, `hermes_cli/memory_oauth.py` | FastAPI decorators |
| Web dashboard routes | `web/src/App.tsx` | React Router route map |
| Desktop routes | `apps/desktop/src/app/routes.ts` | `APP_ROUTES` and session route helpers |
| Security boundary | `SECURITY.md` | OS-level isolation is the only boundary |
| Docker runtime | `Dockerfile`, `docker-compose.yml` | Image/runtime and compose services |
| Cron state | `cron/jobs.py`, `cron/scheduler.py`, `cron/scheduler_provider.py` | Job store, output, scheduler/delivery |

## Divergenze

- Se docs e codice divergono, il codice vince. Aggiorna docs solo se il task lo
  consente; altrimenti segnala la divergenza.
- Se README e CI divergono sui comandi di test/build, CI e script locali
  vincono.
- Se config examples e codice divergono su segreti/config, il codice di
  risoluzione (`hermes_constants.py`, `agent/secret_scope.py`,
  `hermes_cli/runtime_provider.py`) vince.
- Se `website/docs/` e `docs/` divergono, verifica quale e destinata agli utenti
  finali e quale ai worker locali prima di modificare.

## Strumenti Di Audit

- `python3 scripts/docs_audit.py` verifica questa struttura documentale.
- `rg` e `rg --files` sono lo strumento preferito per verificare riferimenti
  prima di modificare.
- `git status --short` mostra modifiche locali senza alterare il worktree.
