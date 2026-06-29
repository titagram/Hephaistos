# Maintenance Workflow

## 1. Iniziare Un Task

- Leggi `AGENTS.md`.
- Esegui `git status --short` per vedere modifiche preesistenti.
- Identifica il tipo task: core, CLI, gateway, dashboard, desktop, TUI, cron,
  docs, packaging.
- Leggi i file source-of-truth elencati in `docs/SOURCE_OF_TRUTH.md` per
  quell'area.
- Usa `rg` o `rg --files`; evita ricerche lente e non mirate.

## 2. Cercare Contesto

Comandi sicuri e utili:

```bash
rg --files -g 'README*' -g 'AGENTS.md' -g 'docs/**'
rg -n 'COMMAND_REGISTRY|CommandDef' hermes_cli/commands.py
rg -n '@app\\.|@router\\.' hermes_cli/web_server.py hermes_cli/dashboard_auth/routes.py
rg -n 'registry.register\\(' tools
rg -n 'CREATE TABLE|CREATE VIRTUAL TABLE' hermes_state.py hermes_cli/kanban_db.py
```

Annota sempre:
- fatti verificati;
- inferenze;
- punti da verificare;
- decisioni operative.

## 3. Modificare

- Mantieni lo scope piccolo.
- Non cancellare docs esistenti senza leggerli.
- Per docs/processi, aggiorna `docs/LOGBOOK.md`.
- Per codice, segui pattern locali invece di introdurre nuove superfici.
- Per segreti/config, non aggiungere nuovi env var user-facing per impostazioni
  non segrete.

## 4. Verificare

Scegli la verifica piu stretta:

```bash
python3 scripts/docs_audit.py
scripts/run_tests.sh tests/path/test_file.py -q
ruff check .
python scripts/check-windows-footguns.py --all
npm run --prefix apps/desktop typecheck
npm run --prefix web typecheck
npm run --prefix ui-tui test
```

Non eseguire deploy, release, migrazioni distruttive, Docker cleanup o install
pesanti senza conferma esplicita.

## 5. Aggiornare Docs

Aggiorna docs quando:
- cambi comandi reali;
- cambi route/entrypoint;
- cambi schema o persistenza;
- cambi security boundary, credenziali o runtime;
- aggiungi/rimuovi una superficie utente.

Se non puoi verificare una cosa, scrivi "da verificare" invece di riempire il
vuoto.

## 6. Chiudere Il Task

Risposta finale minima:
- cosa e stato cambiato;
- file modificati;
- fonti consultate;
- verifiche eseguite con esito;
- rischi residui o sezioni da completare.

Non dire che il progetto e completamente documentato se restano indici iniziali
o aree marcate "da verificare".
