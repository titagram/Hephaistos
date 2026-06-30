# Logbook

Usa questo logbook per cambi documentali/processuali e per decisioni operative
che devono restare visibili al prossimo worker.

## Template

```markdown
### YYYY-MM-DD HH:MM - Titolo

**Richiesta**: ...
**Contesto consultato**:
- `path/file`

**Lavoro eseguito**:
- ...

**Verifiche**:
- `comando` -> esito

**Conclusione e razionale**:
...

**File modificati**:
- `path/file` - motivo

**Note / rischi residui**:
- ...
```

### 2026-06-29 15:37 - Creazione manuale operativo workspace

**Richiesta**: creare una struttura organizzativa completa e ripetibile per
futuri LLM e sviluppatori, derivata da codice, documentazione e comandi reali.

**Contesto consultato**:
- `AGENTS.md`
- `README.md`
- `SECURITY.md`
- `pyproject.toml`
- `package.json`
- `apps/desktop/package.json`
- `apps/shared/package.json`
- `apps/bootstrap-installer/package.json`
- `ui-tui/package.json`
- `web/package.json`
- `website/package.json`
- `scripts/run_tests.sh`
- `.github/workflows/tests.yml`
- `.github/workflows/lint.yml`
- `.github/workflows/typecheck.yml`
- `docker-compose.yml`
- `Dockerfile`
- `hermes_cli/main.py`
- `hermes_cli/commands.py`
- `hermes_cli/web_server.py`
- `web/src/App.tsx`
- `apps/desktop/src/app/routes.ts`
- `tools/registry.py`
- `toolsets.py`
- `hermes_state.py`
- `cron/jobs.py`
- `cron/scheduler.py`
- `cron/scheduler_provider.py`
- `agent/secret_scope.py`
- `tools/environments/local.py`
- `tools/code_execution_tool.py`

**Lavoro eseguito**:
- Aggiunta sezione operativa iniziale in `AGENTS.md` senza rimuovere la guida
  esistente.
- Creati documenti sintetici in `docs/`.
- Creati indici tecnici iniziali in `docs/indexes/`.
- Creato script di audit `scripts/docs_audit.py`.

**Verifiche**:
- `python3 scripts/docs_audit.py` -> da eseguire a fine creazione.

**Conclusione e razionale**:
La struttura e iniziale e source-backed. Gli indici route/data/side-effect sono
utili ma non esaustivi per tutte le superfici dinamiche e plugin.

**File modificati**:
- `AGENTS.md` - regole operative compatte per futuri worker.
- `docs/` - manuale locale e indici.
- `scripts/docs_audit.py` - audit documentale standard library.

**Note / rischi residui**:
- Elenco completo route dashboard/plugin da generare con script dedicato se
  diventa necessario.
- Schema completo kanban da dettagliare solo in task specifici.

### 2026-06-29 16:53 - Decisioni subagent locali e audit pulizia fork

**Richiesta**: chiarire che i subagent devono restare configurati localmente e
valutare quali parti del progetto rimuovere o disattivare nella fork.

**Contesto consultato**:
- `docs/CODEX_AGENTS.md`
- `docs/implementation_plan.md`
- `docs/README.md`
- `docs/PROJECT_OVERVIEW.md`
- `docs/ARCHITECTURE.md`
- `docs/SOURCE_OF_TRUTH.md`
- `docs/RUNTIME.md`
- `docs/MAINTENANCE.md`
- `skills/`
- `optional-skills/`
- `plugins/`
- `gateway/platforms/`
- `optional-mcps/`

**Lavoro eseguito**:
- Aggiunta decisione: i profili subagent restano locali e saranno guidati da
  una skill dedicata.
- Sostituita la voce "subagent server-driven" con profili subagent locali.
- Aggiunta voce di audit per classificare cosa tenere, disabilitare, rendere
  opzionale o rimuovere in seguito.

**Verifiche**:
- `python3 scripts/docs_audit.py` -> da eseguire a fine modifica.

**Conclusione e razionale**:
La pulizia della fork va trattata prima come curatela della distribuzione e
dei default, non come cancellazione immediata, cosi' resta piu' semplice
allinearsi a upstream durante la prima milestone.

**File modificati**:
- `docs/implementation_plan.md` - decisione subagent locali e backlog pulizia.
- `docs/LOGBOOK.md` - traccia della decisione.

**Note / rischi residui**:
- Serve un audit dedicato per mappare dipendenze reali prima di cancellare file
  o directory.

### 2026-06-29 16:57 - Decisioni iniziali sul footprint Hades

**Richiesta**: registrare quali skill, plugin e superfici mantenere o togliere
dalla fork.

**Contesto consultato**:
- `docs/implementation_plan.md`
- `skills/`
- `optional-skills/`
- `plugins/`
- `optional-mcps/`
- `toolsets.py`
- `model_tools.py`

**Lavoro eseguito**:
- Registrate le prime decisioni di pulizia: fuori dai default le skill
  creative; fuori dai default `image_gen`, `video_gen`, `spotify`,
  `google_meet`, `teams_pipeline`, `hades-achievements`, Firecrawl, Exa,
  Tavily, Linear, n8n e Unreal Engine.
- Registrato che Telegram resta tra i platform plugin.
- Registrato che gateway multi-chat, dashboard/web e cron restano.
- Registrato Brave come fallback da verificare, non come dipendenza
  automaticamente mantenuta.

**Verifiche**:
- `python3 scripts/docs_audit.py` -> da eseguire a fine modifica.

**Conclusione e razionale**:
La direzione e ridurre il profilo Hades senza rimuovere ancora codice in
modo irreversibile. La rimozione fisica richiede una matrice keep /
disable-by-default / optional / remove-later e verifica delle dipendenze.

**File modificati**:
- `docs/implementation_plan.md` - decisioni di footprint.
- `docs/LOGBOOK.md` - traccia della decisione.

**Note / rischi residui**:
- Da distinguere, nel piano tecnico, tra gateway come architettura mantenuta e
  singoli adapter/platform da includere nei default.

### 2026-06-29 17:00 - Valutazione installazione plug-and-play

**Richiesta**: valutare se la fork e' facile da installare oggi e cosa serve
per renderla quasi plug-and-play.

**Contesto consultato**:
- `README.md`
- `pyproject.toml`
- `package.json`
- `scripts/install.sh`
- `scripts/install.ps1`
- `setup-hades.sh`
- `hermes_cli/setup.py`
- `hermes_cli/subcommands/setup.py`
- `hermes_cli/subcommands/postinstall.py`
- `hermes_cli/doctor.py`
- `apps/bootstrap-installer/`
- `docs/RUNTIME.md`
- `docs/implementation_plan.md`

**Lavoro eseguito**:
- Verificato che la repo eredita installer shell/PowerShell, setup wizard,
  postinstall, doctor e bootstrap installer da Hades upstream.
- Verificato che README, package name, URL installer e repo clone puntano ancora
  a Hades/Nous, quindi la fork non e' ancora un prodotto installabile in modo
  autonomo.
- Aggiunte al piano le voci per installer Hades, setup backend,
  dipendenze curate, doctor specifico e installazione non-interattiva.

**Verifiche**:
- `python3 scripts/docs_audit.py` -> da eseguire a fine modifica.

**Conclusione e razionale**:
La base installativa e buona, ma oggi installa e configura Hades upstream. Per
una UX plug-and-play serve un flusso Hades che porti un developer da zero
a "agent collegato al backend e al progetto" con pochi passi verificabili.

**File modificati**:
- `docs/implementation_plan.md` - backlog installazione plug-and-play.
- `docs/LOGBOOK.md` - traccia della valutazione.

**Note / rischi residui**:
- La scelta tra mantenere il comando `hades` o introdurre un comando
  rebrandizzato resta da decidere.

### 2026-06-29 17:07 - Decisione installer hosted e backend onboarding

**Richiesta**: confermare che va bene installare tramite comando hosted e
ricordare di preparare istruzioni per configurare il backend Laravel.

**Contesto consultato**:
- `docs/implementation_plan.md`
- `docs/LOGBOOK.md`

**Lavoro eseguito**:
- Registrato l'installer hosted come canale primario di distribuzione.
- Registrato il requisito cross-platform: Linux, macOS e Windows nativo.
- Registrata la necessita' di `install.sh` per Linux/macOS/WSL e `install.ps1`
  per Windows nativo.
- Registrata la necessita' di istruzioni backend Laravel per token, route,
  installer scaricabili, registrazione progetto/agent, MCP/API, WebSocket e
  health check.

**Verifiche**:
- `python3 scripts/docs_audit.py` -> da eseguire a fine modifica.

**Conclusione e razionale**:
L'installer hosted e il canale primario piu' pragmatico. PyPI/npm possono
restare canali secondari o wrapper, mentre il backend Laravel puo' diventare il
punto da cui generare comandi di installazione tokenizzati per team/progetto.

**File modificati**:
- `docs/implementation_plan.md` - decisioni install/backend e backlog.
- `docs/LOGBOOK.md` - traccia della decisione.

**Note / rischi residui**:
- Da decidere se gli installer saranno file statici serviti da Laravel/CDN o
  risposte generate/tokenizzate dal backend Laravel.

### 2026-06-29 17:11 - Decisione rebranding completo

**Richiesta**: registrare che la fork andra' completamente ribrandizzata e che
le superfici utente non dovranno contenere riferimenti a Hermes/Nous.

**Contesto consultato**:
- `docs/implementation_plan.md`
- `docs/LOGBOOK.md`

**Lavoro eseguito**:
- Aggiunta decisione di rebranding completo nelle decisioni di brainstorming.
- Aggiunta voce di audit rebranding nel backlog.

**Verifiche**:
- `python3 scripts/docs_audit.py` -> da eseguire a fine modifica.

**Conclusione e razionale**:
Il prodotto distribuito dovra' usare brand, comandi, installer, docs, UI,
metadata package, config path e messaggi coerenti con Hades o con il nome
finale. I file mantenuti vicini a upstream per strategia di rebase vanno
trattati separatamente.

**File modificati**:
- `docs/implementation_plan.md` - decisione e backlog rebranding.
- `docs/LOGBOOK.md` - traccia della decisione.

**Note / rischi residui**:
- Da decidere se il comando `hades` restera' come alias di compatibilita' o
  verra' rimosso del tutto dalla distribuzione finale.

### 2026-06-29 17:19 - Rebranding e ASCII art come priorita immediata

**Richiesta**: mettere in cima alla lista la modifica delle ASCII art e il
rebranding, come primo lavoro semplice da affrontare.

**Contesto consultato**:
- `docs/CODEX_AGENTS.md`
- `docs/implementation_plan.md`
- `docs/LOGBOOK.md`

**Lavoro eseguito**:
- Aggiunta sezione `Priorita Immediate` in cima al piano.
- Inserito il rebranding leggero iniziale come primo item operativo.
- Inseriti audit rapido rebranding e decisione sul comando `hades` come passi
  immediatamente collegati.

**Verifiche**:
- `python3 scripts/docs_audit.py` -> da eseguire a fine modifica.

**Conclusione e razionale**:
ASCII art, banner e testi visibili sono il primo intervento piu' semplice e
ad alto segnale per distinguere la fork prima delle modifiche architetturali.

**File modificati**:
- `docs/implementation_plan.md` - priorita immediate.
- `docs/LOGBOOK.md` - traccia della decisione.

**Note / rischi residui**:
- La sostituzione effettiva dei riferimenti va fatta dopo un audit dei punti
  utente-facing per evitare cambi sparsi o difficili da mantenere.

### 2026-06-29 17:29 - Naming Persephone per realtime

**Richiesta**: aggiungere al piano che l'endpoint WebSocket lato server e il
relativo plugin si chiameranno `persephone`.

**Contesto consultato**:
- `docs/implementation_plan.md`
- `docs/LOGBOOK.md`

**Lavoro eseguito**:
- Aggiunta decisione di naming realtime: server WebSocket e plugin agent si
  chiameranno `persephone`.
- Aggiunta voce backlog per definire contratto endpoint WebSocket Laravel e
  plugin lato agent.

**Verifiche**:
- `python3 scripts/docs_audit.py` -> da eseguire a fine modifica.

**Conclusione e razionale**:
`persephone` diventa il nome stabile del layer realtime che colleghera'
backend Laravel e agent installati dagli sviluppatori.

**File modificati**:
- `docs/implementation_plan.md` - decisione e backlog `persephone`.
- `docs/LOGBOOK.md` - traccia della decisione.

**Note / rischi residui**:
- Il contratto eventi, auth WebSocket, retry e persistenza inbox restano da
  definire nella spec tecnica.

### 2026-06-29 17:45 - Primo passaggio rebranding Hades

**Richiesta**: passare all'implementazione del primo step del piano, usare il
brand `hades`, rinominare comando/package/config dove serve per far funzionare
la fork, e sostituire l'ASCII art con un adattamento del simbolo di Hades/Plutone.

**Contesto consultato**:
- `docs/CODEX_AGENTS.md`
- `docs/implementation_plan.md`
- `docs/README.md`
- `docs/PROJECT_OVERVIEW.md`
- `docs/SOURCE_OF_TRUTH.md`
- `docs/ARCHITECTURE.md`
- `docs/RUNTIME.md`
- `docs/CODING_STYLE.md`
- `docs/TESTING.md`
- `docs/MAINTENANCE.md`
- `docs/indexes/ROUTES_OR_ENTRYPOINTS.md`
- `docs/indexes/DEPENDENCIES.md`

**Lavoro eseguito**:
- Introdotto `hades` come comando primario e `hades-agent` come package
  Python primario, mantenendo gli entrypoint `hermes*` come alias legacy.
- Reso `HADES_HOME` e `.hades` il path primario per configurazione/state, con
  `HERMES_HOME` ancora accettato come compatibilita'.
- Aggiornati banner CLI/TUI, skin default, metadata Node/Desktop/Web, README e
  copy principali verso Hades.
- Sostituita l'hero art del banner con una sagoma del simbolo Hades/Plutone.
- Aggiornato il piano vivo segnando completato il primo step e documentando la
  decisione sugli alias legacy.

**Verifiche**:
- `python3 -m py_compile hermes_constants.py hermes_cli/main.py
  hermes_cli/banner.py hermes_cli/skin_engine.py hermes_cli/_parser.py
  hermes_cli/__init__.py hermes_cli/default_soul.py cli.py` -> ok
  (solo warning esistente in `cli.py` su `return` in `finally`).
- `node --check apps/desktop/electron/main.cjs` e helper Electron toccati -> ok.
- `node --test apps/desktop/electron/backend-env.test.cjs
  apps/desktop/electron/update-relaunch.test.cjs
  apps/desktop/electron/desktop-uninstall.test.cjs
  apps/desktop/electron/update-remote.test.cjs` -> 47/47 ok.
- `python3 -m json.tool package.json`, `apps/desktop/package.json` e
  `package-lock.json` -> ok.
- `python3 scripts/docs_audit.py` -> ok.
- `scripts/run_tests.sh ...` -> non eseguito: manca `.venv`/`venv`.
- `npm run --prefix ui-tui test -- src/__tests__/theme.test.ts` -> non
  eseguito: `vitest` non installato.

**Conclusione e razionale**:
Il primo passaggio rende Hades il brand visibile e il comando primario senza
fare ancora il refactor distruttivo dei moduli interni `hermes_*`. Gli alias
legacy restano necessari per migrazione, test storici e parti runtime che
ancora risolvono nomi upstream.

**File modificati**:
- `pyproject.toml`, `package.json`, `package-lock.json`
- `cli.py`, `hermes_constants.py`, `hermes_cli/*`
- `ui-tui/*`, `apps/desktop/*`, `apps/shared/*`, `web/*`
- `README.md`, `docs/*`

**Note / rischi residui**:
- Restano riferimenti interni/legacy a Hermes nei nomi dei moduli, in molte
  variabili env storiche `HERMES_*` e in integrazioni provider/upstream.
- Serve un follow-up dedicato per installer script, Docker/CI e test suite
  completa dopo setup locale delle dipendenze.

### 2026-06-29 18:12 - Correzione prompt first-run Hades

**Richiesta**: correggere il prompt mostrato al primo avvio di `hades`, che
usava ancora "Hermes" e suggeriva `hermes setup`.

**Contesto consultato**:
- Screenshot utente del prompt first-run.
- `hermes_cli/main.py`
- `hermes_cli/setup.py`

**Lavoro eseguito**:
- Aggiornato il first-run guard per usare "Hades" e `hades setup`.
- Aggiornata la guidance non-interattiva dello stesso flusso per usare
  "Hades Setup" e comandi `hades config set`.
- Aggiunto un test sorgente mirato per bloccare regressioni di branding in
  questi messaggi.

**Verifiche**:
- Controllo Python diretto sui literal first-run e setup non-interattivo -> ok.
- `HADES_HOME=$(mktemp -d) .venv/bin/hades </dev/null` -> output Hades,
  uscita 1 attesa per stdin non interattivo.
- Avvio interattivo PTY con `HADES_HOME` temporaneo -> prompt mostra Hades e
  `hades setup`; risposta `n` stampa `hades setup`.
- `python3 -m py_compile hermes_cli/main.py hermes_cli/setup.py` -> ok.
- `git diff --check` -> ok.

**Note / rischi residui**:
- `scripts/run_tests.sh tests/hermes_cli/test_first_run_branding.py -q` richiede
  `pytest`, non presente nella venv installata con solo `pip install -e .`.

### 2026-06-29 18:22 - Correzione setup wizard iniziale

**Richiesta**: correggere lo wizard `hades setup`, che mostrava ancora
"Hermes Agent Setup Wizard", "How would you like to set up Hermes?" e
raccomandava "Quick Setup (Nous Portal)".

**Contesto consultato**:
- Output utente dello wizard.
- `hermes_cli/setup.py`
- `tests/hermes_cli/test_first_run_branding.py`

**Lavoro eseguito**:
- Aggiornato il banner dello wizard a "Hades Agent Setup Wizard".
- Aggiornata la domanda iniziale a "How would you like to set up Hades?".
- Rimosso il quick setup Nous dal menu first-time e reso "Full setup"
  l'opzione predefinita/raccomandata.
- Aggiornato il prompt OpenClaw che precede il menu per usare Hades e comandi
  `hades claw migrate`.
- Esteso il test sorgente di branding first-run/setup.

**Verifiche**:
- Controllo Python diretto sui literal dello wizard e OpenClaw -> ok.
- Smoke PTY con `HADES_HOME` temporaneo: banner Hades, prompt OpenClaw Hades,
  menu Hades con Full setup predefinito -> ok.
- `python3 -m py_compile hermes_cli/setup.py
  tests/hermes_cli/test_first_run_branding.py` -> ok.
- `python3 scripts/docs_audit.py` -> ok.
- `git diff --check` -> ok.

### 2026-06-29 18:29 - Correzione riepilogo setup Hades

**Richiesta**: correggere il riepilogo finale di `hades setup`, che mostrava
ancora comandi `hermes`, e chiarire perché un nuovo `hades` poteva chiedere
di rifare il setup.

**Contesto consultato**:
- Screenshot utente del riepilogo setup e del successivo prompt first-run.
- `hermes_cli/setup.py`
- `tests/hermes_cli/test_first_run_branding.py`

**Lavoro eseguito**:
- Aggiornato il riepilogo finale dello setup per suggerire `hades setup`,
  `hades config`, `hades gateway` e `hades doctor`.
- Aggiornati i messaggi utente dei percorsi gateway, Blank Slate e portal
  ancora legati a comandi `hermes`.
- Aggiornato il warning del client ausiliario Nous che poteva comparire
  durante il riepilogo con `run: hermes auth`.
- Il riepilogo non mostra più "Ready to go!" quando non esiste ancora un
  provider/modello configurato; suggerisce invece `hades setup model`.
- Esteso il test sorgente per bloccare regressioni nel riepilogo setup.

**Verifiche**:
- Controllo Python diretto su `tests/hermes_cli/test_first_run_branding.py`
  -> ok.
- `.venv/bin/python -m py_compile hermes_cli/setup.py hermes_cli/main.py
  tests/hermes_cli/test_first_run_branding.py` -> ok.
- `.venv/bin/python -m pytest tests/hermes_cli/test_first_run_branding.py`
  -> non eseguito: `pytest` non è installato nella venv.

**Note / rischi residui**:
- I link al sito docs storico `hermes-agent.nousresearch.com` restano invariati
  finché non esiste una destinazione Hades equivalente.

### 2026-06-29 19:02 - Rebranding segnato come completato nel piano

**Richiesta**: segnare il rebranding come fatto nell'implementation plan e
controllare con ricerca locale altri riferimenti a Hermes.

**Contesto consultato**:
- `docs/implementation_plan.md`
- Ricerca locale con `rg` dei riferimenti `Hermes`, `hermes` e `HERMES_`.

**Lavoro eseguito**:
- Aggiornato lo stato del piano: rebranding principale Hades completato, con
  residui Hermes/Nous da trattare in passaggi mirati.
- Spuntate le voci di identita/naming e audit rebranding iniziale.
- Aggiunto il risultato della ricerca locale: 3034 file / 39275 righe ancora
  contengono riferimenti Hermes, soprattutto in `tests`, `website`, `apps`,
  `hermes_cli`, `plugins`, `optional-skills`, `skills` e `agent`.
- Aggiunti follow-up non spuntati per bonifica superfici utente residue,
  policy legacy (`hermes`, `HERMES_*`, `~/.hermes`, `hermes://`) e docs
  upstream/localizzate.

**Verifiche**:
- `rg -n "\bHermes\b|\bhermes\b|HERMES_" --glob '!hades_agent.egg-info/**'
  --glob '!*.pyc' --glob '!__pycache__/**' .` -> eseguito; output troppo
  ampio, sintetizzato in conteggi e directory principali.
- `python3 scripts/docs_audit.py` -> ok.
- `git diff --check` -> ok.

### 2026-06-29 19:39 - Bonifica CLI residui Hermes

**Richiesta**: procedere con la parte successiva dell'implementation plan usando
subagent-driven development.

**Contesto consultato**:
- `docs/CODEX_AGENTS.md`
- `docs/implementation_plan.md`
- `docs/README.md`
- `docs/PROJECT_OVERVIEW.md`
- `docs/SOURCE_OF_TRUTH.md`
- `docs/TESTING.md`
- `docs/CODING_STYLE.md`
- `docs/RUNTIME.md`
- `docs/MAINTENANCE.md`
- `docs/indexes/ROUTES_OR_ENTRYPOINTS.md`
- `docs/indexes/SIDE_EFFECTS.md`
- `hermes_cli/status.py`
- `hermes_cli/config.py`
- `hermes_cli/claw.py`
- `hermes_cli/inventory.py`
- `tests/hermes_cli/test_status.py`
- `tests/hermes_cli/test_config.py`
- `tests/hermes_cli/test_claw.py`
- `tests/hermes_cli/test_inventory.py`
- `tests/hermes_cli/test_first_run_branding.py`

**Lavoro eseguito**:
- Usato subagent-driven development con audit preliminare, worker di
  implementazione, spec review e code-quality review.
- Aggiornati banner e suggerimenti di comando visibili in `status`, `config`,
  `claw` e `inventory` per usare Hades/hades.
- Mantenuti invariati i nomi di compatibilita' non ancora decisi:
  `HERMES_*`, `~/.hermes`, `hermes_cli`, `get_hermes_home`, provider slug,
  nomi package/image e label reali Nous Portal.
- Aggiunta copertura mirata nei test vicini e ristretto il guard sorgente per
  evitare falsi positivi su commenti/compatibilita'.
- Aggiornato il piano vivo segnando completato solo il sotto-passaggio CLI
  della bonifica residui.

**Verifiche**:
- `python3 -m py_compile hermes_cli/status.py hermes_cli/config.py
  hermes_cli/claw.py hermes_cli/inventory.py
  tests/hermes_cli/test_first_run_branding.py tests/hermes_cli/test_status.py
  tests/hermes_cli/test_config.py tests/hermes_cli/test_claw.py
  tests/hermes_cli/test_inventory.py` -> ok.
- `git diff --check` -> ok.
- `scripts/run_tests.sh tests/hermes_cli/test_first_run_branding.py
  tests/hermes_cli/test_status.py tests/hermes_cli/test_status_model_provider.py
  tests/hermes_cli/test_config.py tests/hermes_cli/test_set_config_value.py
  tests/hermes_cli/test_claw.py tests/hermes_cli/test_inventory.py -q` ->
  non eseguiti: `.venv/bin/python` non ha `pytest` installato.
- `ruff check ...` -> non eseguito: `ruff` non disponibile nel PATH.

**Conclusione e razionale**:
Lo slice CLI della bonifica rebranding e' chiuso senza toccare la policy legacy.
La voce principale resta aperta per dashboard/web, TUI setup copy e plugin
gateway.

**File modificati**:
- `hermes_cli/status.py` - banner e command hint Hades.
- `hermes_cli/config.py` - banner, messaggi utente e command hint Hades.
- `hermes_cli/claw.py` - migrazione OpenClaw verso Hades e command hint Hades.
- `hermes_cli/inventory.py` - hint picker `hades model`.
- `tests/hermes_cli/test_first_run_branding.py` - guard sorgente mirato.
- `tests/hermes_cli/test_status.py` - copertura output status.
- `tests/hermes_cli/test_config.py` - copertura output/config guidance.
- `tests/hermes_cli/test_claw.py` - copertura output migration/cleanup.
- `tests/hermes_cli/test_inventory.py` - copertura hint picker.
- `docs/implementation_plan.md` - stato parziale della bonifica.
- `docs/LOGBOOK.md` - traccia operativa.

**Note / rischi residui**:
- I test pytest non sono stati eseguiti per assenza di `pytest` nella venv.
- Restano da completare dashboard/web, TUI setup copy e plugin gateway.
- La policy legacy su `hermes`, `HERMES_*`, `~/.hermes` e nomi tecnici interni
  resta da decidere prima di rinomini piu' profondi.

### 2026-06-29 20:35 - Bonifica dashboard/web residui Hermes

**Richiesta**: proseguire con il prossimo step dell'implementation plan usando
subagent-driven development.

**Contesto consultato**:
- `docs/CODEX_AGENTS.md`
- `docs/implementation_plan.md`
- `docs/README.md`
- `docs/PROJECT_OVERVIEW.md`
- `docs/SOURCE_OF_TRUTH.md`
- `docs/TESTING.md`
- `docs/CODING_STYLE.md`
- `docs/MAINTENANCE.md`
- `web/src/App.tsx`
- `web/src/pages/ChannelsPage.tsx`
- `web/src/pages/ChatPage.tsx`
- `web/src/pages/ConfigPage.tsx`
- `web/src/pages/McpPage.tsx`
- `web/src/pages/SkillsPage.tsx`
- `web/src/pages/SystemPage.tsx`
- `web/src/lib/api.ts`
- `web/src/lib/gatewayClient.ts`
- `web/src/themes/presets.ts`
- `web/src/i18n/*.ts`
- `hermes_cli/dashboard_auth/login_page.py`
- `hermes_cli/web_server.py`
- `tests/hermes_cli/test_dashboard_auth_password_login.py`
- `tests/hermes_cli/test_web_server.py`

**Lavoro eseguito**:
- Usato subagent-driven development con audit mirato dashboard/web, worker,
  spec review, fix di re-review e code-quality review.
- Aggiornata la copy utente dashboard/web da Hermes/Hermes Agent a Hades/Hades
  Agent nelle superfici visibili: sidebar, login, errori dashboard, pagine
  channels/chat/config/MCP/skills/system, temi built-in e cataloghi API che
  alimentano la UI.
- Aggiornati gli esempi comando visibili a `hades ...` nelle superfici
  dashboard/web (`hades dashboard`, `hades update`, `hades gateway start`,
  `hades skills search`, `hades plugins`, `hades auth add`, `hades memory setup`).
- Mantenuti invariati gli identificatori di compatibilita' e wire/storage:
  `HERMES_*`, `X-Hermes-Session-Token`, `/api/hermes/update`, `hermes-update`,
  `hermes_home`, `hermes_version`, `canUpdateHermes`, `~/.hermes`, storage key,
  package/module name e label reali come Nous Portal.
- Aggiunti test statici mirati per la copy dashboard/web, piu' assert vicini su
  login page e temi built-in.
- Aggiornato il piano vivo segnando completato solo il sotto-passaggio
  dashboard/web della bonifica residui.

**Verifiche**:
- Spec review subagent -> pass dopo correzione della stringa i18n ungherese.
- Code-quality review subagent -> approved.
- `python3 -m py_compile hermes_cli/dashboard_auth/login_page.py
  hermes_cli/web_server.py tests/hermes_cli/test_dashboard_branding_copy.py
  tests/hermes_cli/test_dashboard_auth_password_login.py
  tests/hermes_cli/test_web_server.py` -> ok.
- `git diff --check` -> ok.
- `scripts/run_tests.sh tests/hermes_cli/test_dashboard_branding_copy.py
  tests/hermes_cli/test_dashboard_auth_password_login.py
  tests/hermes_cli/test_web_server.py -q` -> non eseguiti: `.venv/bin/python`
  non ha `pytest` installato.
- `npm run --prefix web typecheck` -> non eseguito: `tsc` non disponibile
  per dipendenze web mancanti.

**Conclusione e razionale**:
Lo slice dashboard/web della bonifica rebranding e' chiuso senza rinominare
contratti tecnici legacy. La voce principale resta aperta per TUI setup copy e
plugin gateway.

**File modificati**:
- `web/src/App.tsx` - wordmark sidebar Hades.
- `web/src/pages/ChannelsPage.tsx` - fallback command hint `hades gateway start`.
- `web/src/pages/ChatPage.tsx` - guida apertura dashboard `hades dashboard`.
- `web/src/pages/ConfigPage.tsx` - nome export config `hades-config.json`.
- `web/src/pages/McpPage.tsx` - copy catalogo MCP Hades-curated.
- `web/src/pages/SkillsPage.tsx` - copy skill learning e hint `hades skills search`.
- `web/src/pages/SystemPage.tsx` - copy update, credentials, backup e supporto.
- `web/src/lib/api.ts` - errore dashboard server Hades.
- `web/src/lib/gatewayClient.ts` - errore dashboard server Hades.
- `web/src/themes/presets.ts` - label/descrizioni Hades Teal.
- `web/src/i18n/*.ts` - headline plugin e copy i18n Hades.
- `hermes_cli/dashboard_auth/login_page.py` - titoli login Hades Agent.
- `hermes_cli/web_server.py` - copy/payload UI dashboard Hades.
- `tests/hermes_cli/test_dashboard_branding_copy.py` - guard regressione copy.
- `tests/hermes_cli/test_dashboard_auth_password_login.py` - assert login Hades.
- `tests/hermes_cli/test_web_server.py` - assert temi built-in Hades.
- `docs/implementation_plan.md` - stato parziale della bonifica.
- `docs/LOGBOOK.md` - traccia operativa.

**Note / rischi residui**:
- Pytest e typecheck web non sono stati eseguiti per dipendenze locali mancanti.
- Restano da completare TUI setup copy e plugin gateway.
- La policy legacy su `hermes`, `HERMES_*`, `~/.hermes` e nomi tecnici interni
  resta da decidere prima di rinomini piu' profondi.

### 2026-06-30 01:34 - Bonifica TUI setup copy e plugin gateway

**Richiesta**: procedere con TUI setup copy e subito dopo Plugin gateway usando
subagent-driven development.

**Contesto consultato**:
- `docs/CODEX_AGENTS.md`
- `docs/implementation_plan.md`
- `docs/README.md`
- `docs/PROJECT_OVERVIEW.md`
- `docs/SOURCE_OF_TRUTH.md`
- `docs/TESTING.md`
- `docs/CODING_STYLE.md`
- `docs/MAINTENANCE.md`
- `ui-tui/src/content/setup.ts`
- `ui-tui/src/app/setupHandoff.ts`
- `ui-tui/src/app/slash/commands/setup.ts`
- `ui-tui/src/lib/externalCli.ts`
- `ui-tui/src/components/modelPicker.tsx`
- `tui_gateway/server.py`
- `gateway/run.py`
- `gateway/slash_commands.py`
- `locales/en.yaml`
- `plugins/platforms/*`
- `tests/tui_gateway/test_protocol.py`
- `tests/test_tui_gateway_server.py`
- `tests/gateway/*`
- `tests/plugins/platforms/photon/*`

**Lavoro eseguito**:
- Usato subagent-driven development con explorer, worker, spec review e code
  review per TUI setup copy e Plugin gateway.
- Aggiornata la copy TUI di setup/onboarding verso Hades: pannello "setup
  required", `/setup`, handoff al processo esterno, model picker, stato
  iniziale, fallback title e README TUI.
- Cambiato il launcher TUI a `hades` come default, mantenendo `HERMES_BIN` come
  override legacy di compatibilita'.
- Aggiornata la copy del gateway TUI Python per hint `hades setup`, messaggi di
  provider mancante e blocco comandi che richiedono terminale completo.
- Bonificata la copy visibile nei plugin gateway/platform: manifest, setup
  interattivi, messaggi utente, slash command/help, notifiche di update/restart,
  home channel e istruzioni operative.
- Preservati i contratti legacy dove sono runtime/wire compatibility: Slack
  `/hermes`, `hermes_approve_once`, extra `hermes-agent[slack]`, header
  `X-Hermes-Session-Key`, alias/slug Photon `hermes photon`, default IRC
  `hermes-bot`, path/env `HERMES_*` e naming tecnico `hermes_*`.
- Corretto il filtro storico Discord per riconoscere sia `Hermes update` legacy
  sia `Hades update`, evitando che vecchi messaggi di stato entrino nel
  contesto conversazionale.
- Aggiornato Photon per usare `Hades Agent` come nuovo display name ma riusare
  un progetto legacy `Hermes Agent` prima di crearne uno nuovo.
- Aggiornato il resolver update gateway per provare `hades`, poi `hermes`, poi
  `python -m hermes_cli.main`, e allineata la diagnostica utente.
- Aggiunti guard statici e test mirati per copy Hades e confini di
  compatibilita'.
- Aggiornato il piano vivo segnando completati TUI setup copy, Plugin gateway e
  la voce padre della bonifica residui utente.

**Verifiche**:
- TUI spec review subagent -> pass.
- TUI code-quality review subagent -> approved dopo fix import-order e
  isolamento `HERMES_BIN`.
- Plugin gateway spec review subagent -> pass dopo esclusione esplicita dei
  website docs fuori scope.
- Plugin gateway code-quality review subagent -> changes requested; risolti i
  punti su Discord history, default IRC, progetto Photon legacy, resolver
  update e guard troppo aggressivo.
- Review finale post-fix subagent -> approved.
- `python3 -m py_compile gateway/run.py gateway/platforms/whatsapp_common.py
  tui_gateway/server.py plugins/platforms/*/adapter.py
  plugins/platforms/photon/auth.py plugins/platforms/photon/cli.py
  gateway/slash_commands.py tests/gateway/test_plugin_gateway_branding_copy.py
  tests/gateway/test_update_command.py tests/tui_gateway/test_protocol.py
  tests/test_tui_gateway_server.py tests/plugins/platforms/photon/test_auth.py
  tests/plugins/platforms/photon/test_setup_access.py` -> ok.
- `python3 scripts/docs_audit.py` -> ok.
- `git diff --check` -> ok.
- Guard statico Plugin gateway importato ed eseguito direttamente -> ok.
- Smoke statico Node per copy TUI setup -> ok dopo allineamento alle substring
  reali.
- `npm run --prefix ui-tui test -- setupBranding.test.ts` -> non eseguito:
  `vitest` non disponibile.
- `npm run --prefix ui-tui typecheck` -> non eseguito: `tsc` non disponibile.
- `scripts/run_tests.sh ... -q` sui test TUI/gateway/plugin mirati -> non
  eseguiti: `.venv/bin/python` non ha `pytest` installato.
- `ruff check ...` -> non eseguito: `ruff` non disponibile nel PATH.

**Conclusione e razionale**:
La bonifica delle superfici utente residue elencate nel piano e' chiusa. I
nomi Hermes rimasti appartengono a compatibilita', storage/wire contract,
package/module name, upstream docs/localizzazioni fuori scope o policy legacy
ancora da decidere.

**File modificati principali**:
- `ui-tui/src/content/setup.ts` - copy setup-required Hades.
- `ui-tui/src/app/setupHandoff.ts` - handoff e errori setup Hades.
- `ui-tui/src/app/slash/commands/setup.ts` - help `/setup` Hades.
- `ui-tui/src/lib/externalCli.ts` - default launcher `hades` con override
  `HERMES_BIN`.
- `ui-tui/src/components/modelPicker.tsx` - hint `hades model`.
- `tui_gateway/server.py` - copy setup/provider/CLI-blocked Hades.
- `gateway/run.py` - copy gateway/update/restart Hades e resolver update
  `hades` -> `hermes` -> module fallback.
- `gateway/slash_commands.py` - copy managed-update Hades.
- `locales/en.yaml` - messaggi gateway update Hades.
- `plugins/platforms/*` - copy plugin/platform visibile Hades, con compatibilita'
  legacy preservata dove necessaria.
- `tests/gateway/test_plugin_gateway_branding_copy.py` - guard Hades/legacy.
- `tests/gateway/test_discord_free_response.py` - copertura update history
  legacy + corrente.
- `tests/gateway/test_irc_adapter.py` - default IRC legacy preservato.
- `tests/gateway/test_update_command.py` - lookup update e copy Hades.
- `tests/plugins/platforms/photon/test_auth.py` - lookup progetto Photon legacy.
- `tests/plugins/platforms/photon/test_setup_access.py` - setup Photon riusa il
  progetto legacy.
- `ui-tui/src/__tests__/setupBranding.test.ts` - guard TUI setup copy.
- `docs/implementation_plan.md` - voce bonifica completata.
- `docs/LOGBOOK.md` - traccia operativa.

**Note / rischi residui**:
- La suite pytest e i test Vitest/typecheck non sono stati eseguiti per
  dipendenze locali mancanti.
- La policy definitiva su alias `hermes`, `HERMES_*`, `~/.hermes`, deep link e
  nomi tecnici interni resta una voce separata del piano.

### 2026-06-30 01:42 - Chiusura verifiche locali e policy legacy

**Richiesta**: terminare i due punti residui appena scritti: dipendenze locali
mancanti per le verifiche e policy definitiva di compatibilita' legacy Hermes.

**Lavoro eseguito**:
- Installato nell'ambiente locale `.venv` l'extra `.[dev]`, rendendo disponibili
  `pytest==9.0.2`, `pytest-asyncio==1.3.0` e `ruff==0.15.10`.
- Installate le dipendenze del workspace `ui-tui` dai manifest esistenti, senza
  modificare `package.json` o `package-lock.json`.
- Generato il bundle ignorato `ui-tui/packages/hermes-ink/dist/` richiesto dai
  test Vitest che importano `@hades/ink`.
- Corretto un test non ermetico in `tests/test_tui_gateway_server.py`: il caso
  "no supported Chromium executable" ora forza `platform.system()` a `Linux`,
  perche' su macOS il codice produce correttamente un comando manuale
  `open -a "Google Chrome"` anche senza candidate list.
- Creata la policy stabile `docs/LEGACY_COMPATIBILITY.md`.
- Aggiornati `docs/README.md` e `docs/implementation_plan.md`, marcando come
  decisa la policy legacy e verificati `.venv`, Node e il sottoinsieme di test
  locali ragionevole per questa bonifica.

**Decisione policy legacy**:
- Hades e' il brand primario per copy, help, wizard, dashboard, TUI, gateway e
  documentazione operativa nuova.
- I nomi Hermes gia' usati come contratti runtime, storage, wire protocol,
  CLI compatibility, package/plugin identifier o integrazione esterna restano
  supportati.
- I contratti legacy si rinominano solo con task dedicato, dual-read o
  dual-registration, test di backward compatibility e migrazione esplicita.

**Verifiche**:
- `npm run build --prefix ui-tui/packages/hermes-ink` -> ok.
- `npm run --prefix ui-tui test -- setupBranding.test.ts` -> 1 file, 4 test ok.
- `npm run --prefix ui-tui typecheck` -> ok.
- `scripts/run_tests.sh tests/tui_gateway/test_protocol.py
  tests/test_tui_gateway_server.py tests/gateway/test_plugin_gateway_branding_copy.py
  tests/gateway/test_discord_free_response.py tests/gateway/test_discord_connect.py
  tests/gateway/test_discord_slash_commands.py tests/gateway/test_email.py
  tests/gateway/test_homeassistant.py tests/gateway/test_irc_adapter.py
  tests/gateway/test_max_concurrent_sessions.py
  tests/gateway/test_restart_notification.py
  tests/gateway/test_telegram_topic_mode.py tests/gateway/test_update_command.py
  tests/plugins/platforms/photon/test_auth.py
  tests/plugins/platforms/photon/test_setup_access.py -q` -> 806 test ok.
- `.venv/bin/ruff check ...` sui file Python toccati -> ok.

**Conclusione e razionale**:
I due residui della voce precedente sono chiusi: le verifiche non sono piu'
bloccate da dipendenze mancanti, e la policy Hermes legacy e' documentata come
contratto operativo per i prossimi passaggi.
