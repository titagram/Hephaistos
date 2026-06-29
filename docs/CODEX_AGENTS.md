# CODEX_AGENTS.md

Istruzioni operative per Codex su questa fork di Hades Agent.

Questo file non sostituisce `AGENTS.md`: `AGENTS.md` deve restare vicino allo
stato upstream del progetto originale. Per lavorare sulla fork personale,
Codex deve usare la cartella `docs/` come base di lavoro e di contesto.

## Scope

Workspace root corrente:

```text
/Users/gabriele/Dev/Hephaistos
```

Progetto:

```text
hades-agent
```

Documentazione operativa della fork:

```text
/Users/gabriele/Dev/Hephaistos/docs
```

Piano vivo di brainstorming e implementazione:

```text
/Users/gabriele/Dev/Hephaistos/docs/implementation_plan.md
```

## Regola Fondamentale

Prima di lavorare su questa fork, Codex deve leggere `docs/` e trattarla come
manuale operativo locale. Se `AGENTS.md` e `docs/` divergono:

1. `AGENTS.md` descrive le regole generali/upstream di Hades Agent.
2. `docs/CODEX_AGENTS.md` descrive come Codex deve lavorare su questa fork.
3. I documenti tecnici in `docs/` descrivono il contesto locale osservato.
4. Il codice resta la fonte finale quando bisogna verificare un comportamento.

Non modificare `AGENTS.md` per aggiungere regole della fork, salvo richiesta
esplicita. Le regole della fork vanno in `docs/CODEX_AGENTS.md` e nei documenti
collegati.

## Lettura Iniziale Obbligatoria

All'inizio di ogni nuovo lavoro su questa fork:

1. Leggere `docs/CODEX_AGENTS.md`.
2. Leggere `docs/implementation_plan.md`.
3. Leggere `docs/README.md` per orientarsi nella documentazione locale.
4. Leggere `docs/PROJECT_OVERVIEW.md` per capire la struttura del progetto.
5. Leggere `docs/SOURCE_OF_TRUTH.md` per sapere quali fonti contano di piu'.
6. Leggere il documento tecnico pertinente:
   - `docs/ARCHITECTURE.md` per modifiche architetturali.
   - `docs/CODING_STYLE.md` per stile e pattern.
   - `docs/RUNTIME.md` per runtime, Docker, config e segreti.
   - `docs/TESTING.md` per verifiche e comandi.
   - `docs/MAINTENANCE.md` per flusso operativo.
7. Consultare gli indici in `docs/indexes/` quando il task riguarda route,
   entrypoint, dati, side effect, dipendenze o sicurezza.

## Classificazione Iniziale Del Task

Prima di agire, classificare la richiesta. Se una richiesta ricade in piu'
categorie, applicare tutte le regole rilevanti.

| Tipo task | Esempi | Regole principali |
| --- | --- | --- |
| Brainstorming fork | priorita personali, idee prodotto, identita della fork | Non modificare codice; aggiornare `docs/implementation_plan.md` |
| Documentazione operativa | `docs/*.md`, indici, logbook, audit docs | Usare `docs/` come source locale; verificare con `python3 scripts/docs_audit.py` se disponibile |
| Core Hades | `run_agent.py`, `model_tools.py`, `toolsets.py`, `agent/`, `tools/` | Leggere `AGENTS.md`, `docs/ARCHITECTURE.md`, `docs/SOURCE_OF_TRUTH.md`; evitare nuovi core tool non necessari |
| CLI / Gateway / Cron | `hermes_cli/`, `gateway/`, `cron/` | Verificare entrypoint, route, side effect e comandi reali |
| Frontend / Desktop / TUI | `apps/desktop/`, `web/`, `ui-tui/`, `tui_gateway/` | Leggere package scripts e pattern locali prima di cambiare UI |
| Runtime / Docker / CI | `Dockerfile`, compose, workflows, packaging | Non eseguire install/deploy/build pesanti senza conferma |
| Sicurezza / credenziali | `.env`, `config.yaml`, auth, subprocess, plugin | Segreti in `.env`, config in `config.yaml`; rispettare `SECURITY.md` |

## Regola Del Piano Vivo

`docs/implementation_plan.md` e' la checklist viva del lavoro sulla fork.

Ogni volta che una voce viene completata:

1. aprire `docs/implementation_plan.md`;
2. cambiare la voce da `- [ ]` a `- [x]`;
3. aggiungere una breve nota se serve spiegare cosa e' stato deciso o fatto;
4. non lasciare il piano disallineato rispetto al lavoro concluso.

Se nasce una nuova idea durante brainstorming o implementazione, aggiungerla al
piano come voce non spuntata invece di tenerla solo in chat.

## Regole Per La Fase Attuale

Per ora il lavoro e' in brainstorming:

- non modificare codice applicativo;
- non cambiare dipendenze, lockfile, Docker o CI;
- non avviare refactor;
- non fare commit o PR salvo richiesta;
- usare `docs/implementation_plan.md` per raccogliere decisioni e backlog;
- distinguere sempre tra idee, decisioni prese e punti da verificare.

## Come Usare `docs/`

- `docs/README.md`: indice della documentazione operativa.
- `docs/PROJECT_OVERVIEW.md`: mappa del progetto.
- `docs/ARCHITECTURE.md`: architettura osservata.
- `docs/CODING_STYLE.md`: stile reale del codice.
- `docs/RUNTIME.md`: prerequisiti, setup, Docker, config, state.
- `docs/TESTING.md`: comandi verificati e controlli minimi.
- `docs/SOURCE_OF_TRUTH.md`: priorita tra codice, docs, test e CI.
- `docs/MAINTENANCE.md`: flusso di lavoro.
- `docs/LOGBOOK.md`: traccia operativa.
- `docs/indexes/`: indici tecnici iniziali.

Quando un documento contiene "da verificare", non trattarlo come fatto. Prima
di usarlo per una modifica concreta, verifica nel codice o nei comandi reali.

## Cosa Non Fare Senza Richiesta Esplicita

- Non modificare `AGENTS.md` per istruzioni specifiche della fork.
- Non modificare codice applicativo durante brainstorming.
- Non eseguire comandi distruttivi, migrazioni, deploy, release o install
  pesanti.
- Non aggiungere tool core o nuove superfici permanenti senza una decisione
  documentata.
- Non cancellare documentazione esistente senza leggerla.

## Checklist Prima Di Concludere Un Task

- `docs/implementation_plan.md` e' aggiornato e le voci completate sono
  spuntate.
- Le fonti lette sono dichiarate.
- Le decisioni sono separate da idee e punti da verificare.
- Se sono stati modificati docs o audit, eseguire:

```bash
python3 scripts/docs_audit.py
```

- Se una verifica non e' stata eseguita, indicare il motivo.
