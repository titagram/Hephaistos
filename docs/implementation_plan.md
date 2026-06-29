# Implementation Plan

Piano vivo per personalizzare questa fork di Hades Agent.

Regola: quando una voce viene completata, spuntarla da `- [ ]` a `- [x]` nello
stesso task in cui viene completata. Le nuove idee emerse in chat vanno aggiunte
qui come voci non spuntate.

## Stato Attuale

Fase corrente: rebranding principale Hades completato; follow-up residui
Hermes/Nous da classificare e bonificare in passaggi mirati.

L'utente ha confermato il passaggio all'implementazione per il primo step di
rebranding, poi ha validato il comando `hades`, il setup wizard, il banner e il
riepilogo setup.

## Priorita Immediate

- [x] Rebranding leggero iniziale: sostituire ASCII art/banner/testi visibili
  piu' facili e riferimenti utente evidenti a Hermes/Nous con Hades.
  Nota: primo passaggio applicato a banner CLI/TUI, metadata package, README,
  desktop/web copy principale e home path primario.
- [x] Audit rapido rebranding: identificare i file piu' semplici da aggiornare
  per primi, separando superfici utente da file mantenuti vicini a upstream.
  Nota: scansione eseguita con `rg`; restano residui interni/legacy da trattare
  in follow-up piu' mirati.
- [x] Definire se il comando `hades` resta temporaneamente alias di
  compatibilita' o se il primo passaggio introduce gia' il comando
  rebrandizzato.
  Decisione: `hades`, `hades-agent` e `hades-acp` sono primari; `hermes`,
  `hermes-agent` e `hermes-acp` restano alias legacy per compatibilita'.
- [x] Segnare il rebranding principale come fatto dopo verifica locale dei
  riferimenti rimasti a Hermes.
  Nota: ricerca eseguita con
  `rg -n "\bHermes\b|\bhermes\b|HERMES_" --glob '!hades_agent.egg-info/**'`.
  Risultato: restano 3034 file / 39275 righe con riferimenti Hermes, concentrati
  soprattutto in `tests`, `website`, `apps`, `hermes_cli`, `plugins`,
  `optional-skills`, `skills` e `agent`. Questi residui includono compatibilita'
  legacy (`hermes` alias, `HERMES_*`, `~/.hermes`), test, commenti, docs
  upstream/localizzate e plugin non ancora ripuliti. Non bloccano il primo
  rebranding utente validato, ma vanno trattati come backlog esplicito.

## Decisioni Emesse Dal Brainstorming

- [x] Direzione prodotto iniziale: agent per sviluppatori che legge la codebase
  locale e usa un backend autenticato come base di conoscenza condivisa tra
  developer e progetti.
- [x] Modalita' di conoscenza: memoria attiva, interrogata automaticamente a
  ogni task, non solo wiki consultabile su richiesta.
- [x] Approccio tecnico preferito: ibrido, con memory provider per recall/sync
  automatico e MCP/API per azioni esplicite e query strutturate.
- [x] Requisito realtime: aggiungere WebSocket/event stream con inbox locale
  nell'agent, cosi' le richieste tra agent possono arrivare mentre una sessione
  e' aperta e restare recuperabili anche se l'agent non puo' agire subito.
- [x] Backend di riferimento: Laravel, perche' il lavoro backend e' gia'
  iniziato li' e diventera' la superficie canonica per auth, API, MCP, kanban,
  realtime e validazione.
- [x] Subagent locali: la configurazione dei subagent resta locale all'agent;
  il backend non decide quali LLM usare. Va prevista una skill dedicata per
  guidare creazione, uso e manutenzione dei profili subagent locali.
- [x] Pulizia skill: le skill creative vanno escluse dalla distribuzione/default
  Hades.
- [x] Plugin platform: mantenere Telegram; mantenere il gateway multi-chat come
  architettura/superficie.
- [x] Plugin da escludere dalla distribuzione/default Hades: `image_gen`,
  `video_gen`, `spotify`, `google_meet`, `teams_pipeline`,
  `hermes-achievements`.
- [x] Web provider esterni: escludere Firecrawl, Exa e Tavily; Brave resta solo
  come possibile fallback se i tool web/browser nativi non bastano.
- [x] Optional MCP da escludere: Linear, n8n e Unreal Engine.
- [x] Superfici da mantenere: gateway multi-chat, dashboard/web e cron.
- [x] Canale installazione primario: installer hosted stile
  `curl -fsSL https://hades-agent.miosito.com/install.sh | bash`, con
  script PowerShell equivalente per Windows nativo.
- [x] Requisito piattaforme: installazione supportata su Linux, macOS e Windows
  nativo; il comando Bash copre anche WSL/Git Bash dove presenti.
- [x] Backend onboarding: preparare istruzioni per configurare il backend
  Laravel; valutare se Laravel deve anche servire installer scaricabili o
  comandi tokenizzati per progetto/team.
- [x] Rebranding completo: le superfici utente della fork non devono contenere
  riferimenti a Hermes/Nous; nomi, comandi, installer, docs, UI, package
  metadata, config path e messaggi devono convergere sul brand Hades.
- [x] Naming realtime: l'endpoint WebSocket lato server e il relativo plugin
  lato agent si chiameranno `persephone`.

## Setup Documentale Iniziale

- [x] Ripristinare `AGENTS.md` allo stato originale/upstream.
- [x] Creare `docs/CODEX_AGENTS.md` come guida specifica per Codex su questa fork.
- [x] Creare `docs/implementation_plan.md` come checklist viva.
- [ ] Decidere se `scripts/docs_audit.py` deve diventare parte stabile del flusso
  oppure restare solo uno strumento di supporto.
- [ ] Decidere se aggiornare `docs/README.md` per rendere `CODEX_AGENTS.md` il
  primo documento operativo da leggere.

## Brainstorming Obiettivi Fork

- [ ] Definire lo scopo personale della fork: assistente locale, desktop app,
  automazioni, ricerca, sviluppo software, integrazione con servizi personali,
  o altro.
- [ ] Definire quali superfici mantenere centrali: CLI, TUI, desktop, gateway,
  dashboard, cron, plugin, skill.
- [ ] Elencare le parti upstream da non toccare per facilitare rebase futuri.
- [ ] Elencare le parti che possono divergere liberamente dalla upstream.
- [ ] Definire criteri di successo per la prima milestone.

## Personalizzazioni Da Valutare

- [x] Identita e naming della fork.
  Decisione: Hades e' il brand primario; il simbolo Pluto/Hades e' il riferimento
  visuale per ASCII art/banner.
- [x] Audit rebranding: mappare e sostituire riferimenti Hermes/Nous nelle
  superfici utente, mantenendo separati i file che restano deliberatamente
  vicini a upstream per facilita' di rebase.
  Nota: audit iniziale completato con `rg`; il primo rebranding utente e'
  applicato, mentre i residui sono stati classificati come follow-up.
- [ ] Bonifica residui Hermes nelle superfici utente ancora non coperte:
  `hermes_cli/status.py`, `hermes_cli/config.py`, `hermes_cli/claw.py`,
  `hermes_cli/inventory.py`, dashboard/web, TUI setup copy e plugin gateway.
- [ ] Decidere policy definitiva per compatibilita' legacy: alias `hermes`,
  env var `HERMES_*`, path `~/.hermes`, deep link `hermes://` e nomi tecnici
  interni `hermes_*`.
- [ ] Decidere se aggiornare o archiviare docs upstream/localizzate
  (`README.es.md`, `CONTRIBUTING.es.md`, `SECURITY.es.md`, `website/`) invece
  di lasciarle vicine a upstream.
- [ ] Config default e profili.
- [ ] Installazione quasi plug-and-play per Hades: one-liner o installer
  desktop che installa runtime, clona la fork corretta e avvia setup guidato.
- [ ] Installer hosted cross-platform: pubblicare `install.sh` per
  Linux/macOS/WSL e `install.ps1` per Windows nativo sotto dominio Hades.
- [ ] Setup guidato backend Hades: URL backend, token generato dal
  backend, registrazione progetto, memory provider, MCP/API client e realtime
  inbox configurati in un unico flusso.
- [ ] Istruzioni backend Laravel: documentare route, variabili ambiente,
  generazione token, install command per developer, download installer,
  registrazione agent/progetto, MCP/API, WebSocket e controlli di salute.
- [ ] Profilo dipendenze curato per Hades, evitando `.[all]` upstream dove
  include componenti esclusi dai default della fork.
- [ ] Distribuzione PyPI della fork: decidere package name, console scripts,
  extras Hades e flusso `pipx install`/`pip install` con postinstall
  guidato.
- [ ] Distribuzione npm della fork: valutare wrapper npm leggero che installa o
  avvia il runtime Python, invece di pubblicare l'intera app Python come
  pacchetto npm.
- [ ] Diagnostica `doctor` specifica Hades: token valido, backend
  raggiungibile, progetto collegato, MCP configurato, memory provider attivo,
  WebSocket/inbox operativo.
- [ ] Modalita' installazione non-interattiva per team/CI: configurabile con
  token, backend URL e project id senza wizard TTY.
- [ ] Skill/plugin personali.
- [ ] Skill operativa per consultare e aggiornare il backend di conoscenza
  condivisa durante i task di sviluppo.
- [ ] Memory provider dedicato al backend condiviso, con recall automatico,
  sync turni, provenance e scoping per progetto/developer.
- [ ] MCP/API client per kanban, wiki, modifiche recenti e messaggistica tra
  agent installati dagli sviluppatori.
- [ ] Layer realtime interno all'agent per ricevere eventi WebSocket, salvare
  inbox locale, notificare la UI/sessione attiva e collegare gli eventi al
  memory provider.
- [ ] `persephone`: definire contratto endpoint WebSocket lato Laravel e plugin
  lato agent per realtime, inbox locale e notifiche tra agent.
- [ ] Policy per condividere memorie personali locali con il backend: opt-in,
  classificazione privacy, provenance, revisione/validazione server-side e
  possibilita' di revoca o non-pubblicazione.
- [ ] Profili subagent locali: ruoli predefiniti in config locale con
  modello/provider, budget, toolset e policy; skill dedicata per usarli senza
  esporre questa scelta al backend.
- [ ] Audit di pulizia della fork: classificare skill, plugin, toolset,
  superfici UI e gateway in keep / disable-by-default / optional /
  remove-later.
- [ ] Automazioni cron personali.
- [ ] Esperienza desktop/dashboard.
- [ ] Integrazioni locali o private.
- [ ] Riduzione del footprint rispetto a Hades upstream.
- [ ] Strategia di aggiornamento/rebase da upstream.

## Decisioni Operative Da Prendere

- [ ] Dove documentare decisioni stabili: `docs/LOGBOOK.md`, nuovo ADR, o sezione
  dedicata in questo piano.
- [ ] Quale comando minimo usare per audit docs.
- [ ] Quale comando minimo usare per verifiche Python.
- [ ] Quale comando minimo usare per verifiche TypeScript.
- [ ] Quando creare branch o commit.
- [ ] Come separare lavoro di brainstorming da implementazione.

## Da Verificare

- [ ] Stato reale della fork rispetto a upstream.
- [ ] Branch corrente e strategia Git.
- [ ] Presenza e stato di `.venv`.
- [ ] Presenza di install Node locale.
- [ ] Quali test sono ragionevoli da eseguire localmente senza setup pesante.

## Note

- `AGENTS.md` deve restare vicino alla versione originale del progetto.
- Le istruzioni specifiche per Codex e per questa fork vivono in
  `docs/CODEX_AGENTS.md`.
- Ogni task futuro deve aggiornare questo piano quando completa una voce.
