# Implementation Plan

Piano vivo per personalizzare questa fork di Hades Agent.

Regola: quando una voce viene completata, spuntarla da `- [ ]` a `- [x]` nello
stesso task in cui viene completata. SEMPRE. Se si chiude solo una decisione,
una spec o un sotto-passaggio, annotarlo sotto la voce senza spuntarla finche'
resta lavoro operativo. Le nuove idee emerse in chat vanno aggiunte qui come
voci non spuntate.

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
- [x] Decidere se `scripts/docs_audit.py` deve diventare parte stabile del flusso
  oppure restare solo uno strumento di supporto.
  Decisione: diventa parte stabile del flusso locale/agentico per modifiche a
  documentazione operativa o allo script di audit stesso. Non diventa ancora un
  gate CI globale; l'eventuale promozione a CI va valutata dopo stabilizzazione
  della documentazione.
- [x] Decidere se aggiornare `docs/README.md` per rendere `CODEX_AGENTS.md` il
  primo documento operativo da leggere.
  Decisione: no. `CODEX_AGENTS.md` resta una guida operativa temporanea per
  Codex durante il completamento del progetto; non va promosso nel README come
  primo documento stabile da leggere.

## Brainstorming Obiettivi Fork

- [x] Definire lo scopo personale della fork: assistente locale, desktop app,
  automazioni, ricerca, sviluppo software, integrazione con servizi personali,
  o altro.
  Decisione: Hades e' lo snodo locale dell'esperienza; il tratto
  piu' innovativo e' la comunicazione quasi realtime tra agent Hades tramite
  Persephone lato backend, insieme a una memoria condivisa Laravel con
  intelligence dashboard dedicata e accesso tramite token generato. La memoria
  Hades relativa a un progetto deve poter essere condivisa e riallineata con le
  altre installazioni/istanze Hades abilitate per lo stesso progetto.
- [x] Definire quali superfici mantenere centrali: CLI, TUI, desktop, gateway,
  dashboard, cron, plugin, skill.
  Decisione: l'app desktop Hades e' la superficie utente principale. CLI e TUI
  restano superfici tecniche/developer. Dashboard/web resta centrale per
  metriche, intelligence e stato progetto a colpo d'occhio; gateway multi-chat
  e cron restano superfici operative centrali. Plugin e skill restano il
  meccanismo primario di estensione, con default curati. Va valutato in un
  follow-up se e come integrare direttamente l'app desktop con il backend
  Laravel.
- [x] Elencare le parti upstream da non toccare per facilitare rebase futuri.
  Decisione: evitare modifiche non necessarie a core agent loop, prompt caching,
  alternanza messaggi, context compression, core tool schema, `tools/registry.py`,
  `model_tools.py`, `toolsets.py`, ABI/storage legacy (`HERMES_HOME`,
  `~/.hermes`, layout profili e alias tecnici), session DB/migrazioni, provider
  e credential/fallback resolution, contratti gateway upstream, CI, lockfile,
  Docker, dipendenze pesanti e `AGENTS.md`. Intervenire solo con necessita'
  chiara, test mirati e decisione documentata.
- [x] Elencare le parti che possono divergere liberamente dalla upstream.
  Decisione: possono divergere brand Hades, copy, docs locali e website utente,
  app desktop Hades come superficie principale, dashboard/web per metriche e
  intelligence, installer/onboarding/doctor/config default, integrazione backend
  Laravel, token/project registration, memory provider condiviso, MCP/API client,
  Persephone realtime/inbox/plugin, skill/plugin curated, default distribution,
  automazioni cron personali e packaging Hades, preservando gli alias legacy
  necessari.
- [x] Definire criteri di successo per la prima milestone.
  Decisione: la prima milestone richiede Hades installabile/avviabile
  localmente con branding e default coerenti; app desktop validata come
  superficie utente principale; CLI/TUI funzionanti come superfici tecniche;
  dashboard/web con stato progetto o metriche minime; connessione reale e
  funzionante al backend Laravel tramite token/progetto; memoria per progetto
  condivisibile/allineabile; contratto iniziale Persephone operativo o pronto
  per test end-to-end; skill/plugin default curati; docs e audit verdi senza
  rompere compatibilita' legacy o rebase futuri.

## Personalizzazioni Da Valutare

- [x] Identita e naming della fork.
  Decisione: Hades e' il brand primario; il simbolo Pluto/Hades e' il riferimento
  visuale per ASCII art/banner.
- [x] Audit rebranding: mappare e sostituire riferimenti Hermes/Nous nelle
  superfici utente, mantenendo separati i file che restano deliberatamente
  vicini a upstream per facilita' di rebase.
  Nota: audit iniziale completato con `rg`; il primo rebranding utente e'
  applicato, mentre i residui sono stati classificati come follow-up.
- [x] Bonifica residui Hermes nelle superfici utente ancora non coperte:
  `hermes_cli/status.py`, `hermes_cli/config.py`, `hermes_cli/claw.py`,
  `hermes_cli/inventory.py`, dashboard/web, TUI setup copy e plugin gateway.
  Nota: sotto-passaggi CLI, dashboard/web, TUI setup copy e plugin gateway
  completati. Restano fuori da questa voce i contratti legacy deliberati
  (`hermes`, `HERMES_*`, `~/.hermes`, deep link e nomi tecnici interni), da
  decidere nella policy dedicata.
  - [x] CLI: `hermes_cli/status.py`, `hermes_cli/config.py`,
    `hermes_cli/claw.py`, `hermes_cli/inventory.py`.
  - [x] Dashboard/web.
  - [x] TUI setup copy.
  - [x] Plugin gateway.
- [x] Decidere policy definitiva per compatibilita' legacy: alias `hermes`,
  env var `HERMES_*`, path `~/.hermes`, deep link `hermes://` e nomi tecnici
  interni `hermes_*`.
  Decisione: Hades e' il brand primario; i nomi Hermes gia' usati come
  contratti runtime/storage/wire/API/CLI restano supportati e non si rinominano
  durante bonifiche di copy. La policy completa e' in
  `docs/LEGACY_COMPATIBILITY.md`.
- [x] Decidere se aggiornare o archiviare docs upstream/localizzate
  (`README.es.md`, `CONTRIBUTING.es.md`, `SECURITY.es.md`, `website/`) invece
  di lasciarle vicine a upstream.
  Decisione: tutta la documentazione non inglese viene rimossa; la
  documentazione inglese resta canonica e aggiornata a Hades. La i18n runtime
  e app resta fuori scope e non va rimossa (`locales/`, `agent/i18n.py`,
  `web/src/i18n/`, `apps/desktop/src/i18n/`).
- [x] Config default e profili.
  Decisione/spec approvata il 2026-06-30:
  `docs/superpowers/specs/2026-06-30-config-default-profili-design.md`.
  Policy: Hades e' il brand; Hermes resta ABI/storage legacy per
  `HERMES_HOME`, `~/.hermes`, `%LOCALAPPDATA%\hermes` e layout profili.
  Implementato: default runtime storage Hermes-compatible; `HADES_HOME` e'
  solo alias/fallback e non supera `HERMES_HOME`; i profili restano isolati
  sotto `<root>/profiles/<name>` senza inheritance live; installer e guard docs
  impediscono il ritorno di `~/.hades` / `%LOCALAPPDATA%\hades` come default.
  Le sorgenti skill/plugin upstream restano abilitate come compatibili o
  community, non come ufficiali Hades salvo curatela esplicita.
- [ ] Installazione quasi plug-and-play per Hades: one-liner o installer
  desktop che installa runtime, clona la fork corretta e avvia setup guidato.
  Decisione: il one-liner hosted resta il canale principale di installazione
  per la prima fase. L'installer desktop resta una possibilita' successiva o
  complementare, ma non e' il percorso primario da pubblicizzare ora. Il
  percorso preferito e' un comando tokenizzato generato dalla dashboard Laravel
  con backend URL/progetto/token gia' incorporati; il wizard manuale resta come
  fallback quando il comando non contiene quei dati o quando il token fallisce.
  Per la prima fase, il token di installazione puo' essere riutilizzabile per
  installare piu' agent Hades sullo stesso progetto; deve comunque restare
  scoped al progetto e revocabile dal backend. Il comando tokenizzato e'
  generato per un progetto specifico dalla dashboard Laravel; permessi
  user/team e selezione multi-progetto lato install restano fuori dal percorso
  principale della prima fase. Strategia token: mantenere il
  comando tokenizzato diretto per ora e riusare i meccanismi esistenti di
  secret handling dell'agent: prompt mascherato nel fallback interattivo,
  persistenza in `.env` tramite env store, redazione in output/log/status. Non
  introdurre bootstrap-code separato nella prima fase; documentare che la
  persistenza locale e' plaintext protetto da permessi file, non cifratura
  at-rest. Il canale one-liner installa `main` nella prima fase; tag/release
  pinning resta evoluzione successiva quando esiste una milestone stabile.
  Identita' agent: il comando non deve richiedere un nome manuale; Hades genera
  automaticamente label/id locale da macchina e profilo, lo registra sul
  progetto backend e permette di rinominarlo successivamente. A fine install il
  one-liner deve eseguire un check finale e stampare istruzioni chiare per
  avviare CLI/TUI/dashboard/app, senza aprire automaticamente UI desktop o web.
- [ ] Installer hosted cross-platform: pubblicare `install.sh` per
  Linux/macOS/WSL e `install.ps1` per Windows nativo sotto dominio Hades.
  Decisione: Laravel serve e genera il comando di installazione. La dashboard
  Laravel produce il one-liner tokenizzato e rende disponibili gli script
  `install.sh` / `install.ps1` sotto dominio Hades, mantenendo il controllo su
  backend URL, project id, token e messaggi di onboarding. La prima fase deve
  supportare ufficialmente Linux, macOS, WSL e Windows nativo; se serve a
  rendere il flusso Windows piu' affidabile o uniforme, e' accettabile
  introdurre un wrapper leggero Node o equivalente.
- [ ] Setup guidato backend Hades: URL backend, token generato dal
  backend, registrazione progetto, memory provider, MCP/API client e realtime
  inbox configurati in un unico flusso.
  Decisione: il setup tokenizzato deve configurare subito backend/progetto e,
  quando il backend li espone, attivare anche memory provider condiviso e
  Persephone/realtime inbox nello stesso flusso, senza richiedere passaggi
  manuali separati. Se il backend non espone ancora memory provider condiviso
  o Persephone, l'installazione deve completare con warning e lasciare quei
  moduli disabilitati; deve fallire solo quando backend URL, token o progetto
  non sono validi. Punto aperto da specificare: quando il backend espone piu'
  progetti, Hades non deve bloccare il developer; il comando project-scoped
  deve risolvere un progetto preciso. Decisione: Hades deve supportare piu'
  progetti backend nello stesso profilo locale; la directory/workspace corrente
  risolve il progetto backend tramite mapping locale esplicito per path/repo
  root. Il mapping viene creato da install oppure da un comando tipo
  `hades project link`, che invia una request al backend per confermare il
  binding progetto/workspace/agent e riceve un `workspace_binding_id` stabile
  lato backend; path/repo root restano contesto locale. Il dettaglio va discusso
  con l'agent backend. Non devono esistere due binding per lo stesso path/repo
  root: se il path e' gia' associato allo stesso progetto, il link e'
  idempotente e non crea duplicati; se e' associato a un progetto diverso,
  Hades blocca e richiede conferma esplicita di re-link mostrando progetto
  attuale e nuovo progetto. Deve esistere anche `hades project unlink`, che
  rimuove il binding locale e notifica il backend senza cancellare memory o
  storico job. Quando un workspace viene unlinked, i job locali collegati a
  quel `workspace_binding_id` restano nello storico SQLite ma non vengono piu'
  eseguiti; i job non terminali non ancora `started` passano a stato
  terminale `unlinked` e la transizione viene comunicata al backend. Se ci sono
  job non terminali, `hades project unlink` deve mostrare un alert semplice con
  risposta `y/n`; `y` procede con unlink e transizione dei job, `n` annulla
  senza modifiche. In modalita' non interattiva, se esistono job non terminali,
  il comando abortisce di default con errore esplicito e procede solo con flag
  intenzionale tipo `--yes`. Il
  comando/link flow deve essere disponibile sia in
  CLI/TUI, percorso dominante per il workflow developer, sia nella desktop app
  come superficie principale/user-facing. Se il workspace non e' riconosciuto,
  Hades deve mostrare un warning e suggerire `hades project link`, senza
  bloccare il lavoro locale. La shared memory deve restare disabilitata del
  tutto finche' il workspace non viene linkato: niente recall/read e niente
  create/update/delete sul backend. Regola di ownership: l'agent locale non
  cancella mai direttamente memoria condivisa sul backend. Per la shared memory
  backend Hades puo' solo inviare richieste/proposte di creazione,
  aggiornamento o cancellazione con intent/provenance; Laravel resta
  autoritativo e decide se applicarle, rifiutarle o trasformarle in altro stato.
  Le richieste di update/delete verso Laravel devono includere almeno
  `memory_id`, `base_version`/`etag`, `intent`, `provenance` e una sintesi del
  cambiamento richiesto, cosi' il backend puo' accettare, rifiutare o marcare
  conflitto; lo schema definitivo va discusso con l'agent backend. Quando
  Laravel rifiuta una proposta update/delete o la considera non applicabile per
  conflitto, Hades deve registrare ed esporre stato locale `refused` con causa
  obbligatoria, mostrato all'utente e in `doctor`/status come
  `refused: <causa>`. Se una proposta verso Laravel resta pending troppo a
  lungo, Hades non blocca il task: usa fallback a memoria locale e, se presente,
  una copia cache locale della shared memory aggiornata all'ultimo sync valido.
  Il backend live resta autoritativo e la cache deve essere mostrata come
  cached/stale con eta', versione/etag e numero di proposte pending. La soglia
  per considerare una proposta "pending troppo a lungo" e' combinata: backend
  come fonte primaria tramite discovery/capabilities o policy della proposta,
  config locale Hades come fallback/guardrail quando il backend non la espone o
  non e' raggiungibile. Il valore e la precedenza definitiva vanno decisi
  parlando con l'agent backend.
  Se backend/shared memory e' irraggiungibile, una delete richiesta localmente si
  applica solo alla memoria/cache locale e non genera tombstone o retry di
  cancellazione verso il backend. Dopo `hades project unlink`, cache e stato
  locale della shared memory per quel progetto/binding restano nello storage
  locale come storico/cache disabilitata, senza recall o sync finche' il
  workspace non viene rilinkato. Quando un workspace viene rilinkato dopo
  unlink, Hades puo' riattivare quella cache solo se il backend conferma stesso
  progetto e binding/fingerprint compatibile; prima di consentire recall deve
  eseguire sync/refresh dal backend. Se il sync/refresh fallisce, il link resta
  valido ma la shared memory rimane disabilitata/degradata finche' il refresh
  non riesce; Hades mostra un warning e continua il lavoro locale senza
  recall/sync backend. Se il backend conferma lo stesso progetto ma
  binding/fingerprint non compatibile, il link resta valido come nuovo binding:
  la vecchia cache resta orfana/disabilitata e non viene usata
  automaticamente. Le cache orfane restano nello storage locale finche' non
  scade una retention locale configurabile e devono essere visibili in
  `doctor`/status come orphaned cache. La durata della retention si configura
  in `config.yaml` locale Hades con la chiave
  `memory.orphaned_cache_retention_days`, come singolo valore globale per tutto
  il profilo, con default iniziale 90 giorni. Il valore deve essere un intero
  `>= 0`: valori positivi indicano giorni di retention, `0` significa retention
  infinita e quindi nessuna cache scade per eta'. Se la chiave manca si usa il
  default 90; se e' invalida, Hades mostra warning e usa fallback 90. Nella
  prima fase non sono previsti override per progetto, workspace o binding. Il
  backend puo' solo suggerire default o limiti tramite discovery/capabilities,
  senza essere fonte autoritativa. Deve esistere anche un comando manuale di cleanup:
  `hades doctor cleanup --orphaned-cache`. Di default rimuove solo cache orfane
  gia' scadute; il flag `--all` include anche cache orfane non ancora scadute.
  Eccezione: se `memory.orphaned_cache_retention_days` e' `0`, il cleanup
  manuale senza `--all` puo' rimuovere tutte le cache orfane, trattando la
  retention infinita come pulizia manuale libera. Con retention `0`,
  `doctor`/status mostra le cache orfane come warning piu' severo, evidenziando
  che non scadranno mai automaticamente e richiedono cleanup manuale se si vuole
  rimuoverle; questo warning non modifica l'exit code di `doctor`, che resta
  `0` e non blocca script o CI.
  Prima della conferma mostra un riepilogo con conteggi e dimensione stimata
  per progetto/binding, senza dettagli dei contenuti. Il comando chiede
  conferma `y/n` in modalita' interattiva; in modalita' non interattiva
  richiede `--yes` e altrimenti abortisce senza modifiche. Il
  cleanup e' solo locale: non notifica il backend con evento dedicato e il
  backend vede al massimo lo stato aggiornato tramite `doctor`/status
  successivo. Le cache orfane scadute non vengono pulite automaticamente dai
  cicli Hades: `doctor`/status le segnala e il cleanup resta manuale. Il
  namespace `doctor` diventa il riferimento per comandi analoghi di
  manutenzione/diagnostica non frequenti.
  Se il workspace e' linkato ma backend/shared memory e' temporaneamente
  irraggiungibile durante un task, Hades continua a usare e aggiornare la
  memoria locale. Il recall shared-memory backend live resta disabilitato
  finche' il backend non torna disponibile; se esiste una copia cache locale
  della shared memory per quel binding, Hades puo' usarla come fallback di
  recall degradato, marcandola sempre come cached/stale e mai come stato
  autoritativo. Le richieste/proposte create/update destinate alla shared memory
  vengono salvate in coda locale e ritentate, mentre le delete restano solo
  locali e non vengono sincronizzate come cancellazioni backend. Il degrado, la
  cache shared-memory usata come fallback e la coda pending devono essere
  visibili in `doctor`/status.
  Il
  developer puo' continuare a lavorare
  localmente, ma Hades non deve leggere o scrivere memoria project-scoped in
  modo ambiguo. Da approfondire: il
  backend puo' esporre o inviare JSON con richieste specifiche per l'agent
  locale e relativi dati; questa superficie va trattata come protocollo
  dichiarativo/capability-scoped, non come prompt libero. La prima fase deve
  gia' includere richieste operative read-only del tipo "raccogli questi
  file/info dal progetto e inviali al backend": Hades le salva in una coda o
  crontable locale dell'agent e le dispatcha quando possibile, dopo validazione
  di progetto, workspace, capability e scope. Il delivery principale e'
  pull/piggyback: durante i cicli memory, setup, health o sync, Hades invoca un
  endpoint job dedicato del backend, ad esempio `GET /api/hades/agent/jobs`,
  usando lo stesso token project-scoped/agent registration previsto per backend
  e memory, con `agent_id`, `project_id` e `workspace_binding_id` nella request;
  path/repo root possono essere inviati come contesto locale. Poi inserisce le
  richieste pendenti nella coda/crontable locale. Ack job in due
  fasi: `received` appena Hades persiste il job nella coda locale, poi
  `started`, `completed` o `failed` durante l'esecuzione reale. Retry/backoff:
  il backend e' autorevole e puo' specificare `max_attempts`, `retry_after` e
  `deadline`; Hades usa default locali conservativi solo se quei campi mancano,
  evitando retry infiniti o richieste troppo frequenti. Esecuzione automatica:
  `read_files` e intent safe/metadata possono partire automaticamente quando
  restano entro limiti locali/backend; i job che leggono molti file o producono
  payload ampi devono richiedere conferma locale prima dell'esecuzione. Le
  soglie auto/confirm sono definite principalmente dal backend nel job, con
  fallback locale prudente quando mancano; soglie e flag vanno discussi con
  l'agent backend. La conferma locale viene esposta prima nella desktop app,
  superficie utente principale, con CLI/TUI come fallback tecnico per ambienti
  developer/headless; i job in attesa di conferma devono avere stato visibile
  tipo `waiting_confirmation`. Un job in `waiting_confirmation` resta pendente
  fino alla `deadline` indicata dal backend; se la deadline passa senza
  decisione utente, Hades lo marca `expired` e lo comunica al backend. Formato
  risultato job: payload strutturato con `summary` leggibile/indicizzabile,
  `provenance` precisa e allegati/parti bounded per contenuti raw o voluminosi;
  evitare risultati raw monolitici non limitati. Prima dell'invio al backend,
  Hades applica redazione locale automatica di segreti e dati sensibili su
  summary, provenance utile e allegati; il payload include metadata su redazioni
  e truncation, ma non invia segreti raw a Laravel. Se un allegato richiesto
  contiene segreti o dati sensibili in modo limitato, Hades invia la versione
  redatta inline; se la densita' di segreti e' alta o la redazione rende il
  contenuto poco sicuro/utile, Hades omette l'allegato e invia solo
  metadata/provenance.
  Persephone non e' il
  canale primario per questi job; resta soprattutto il layer realtime per
  comunicazione tra istanze Hades di developer diversi, con possibili
  interazioni Laravel da definire dopo. Le
  richieste operative read-only sono divise in capability separate:
  `read_files`, deterministica e limitata a file/path/glob esplicitamente
  richiesti dal backend, e `project_inspection`, piu' intelligente, che consente
  all'agent locale di cercare file rilevanti nel workspace linkato, leggere
  manifest/config/test o documenti utili e sintetizzare una risposta con
  provenance. Per `project_inspection`, il
  payload deve includere un `intent` obbligatorio da allow-list per applicare
  policy e limiti; puo' includere una `question` opzionale in linguaggio
  naturale per guidare la sintesi, ma la question non puo' ampliare capability,
  workspace o scope autorizzati dall'intent. Intent iniziali:
  `summarize_project`, `find_config`, `list_routes`,
  `detect_test_framework`, `inspect_dependencies`, `summarize_recent_changes`,
  `find_docs`, `sync_git_tree` per mantenere aggiornato il git tree sul
  backend e `populate_backend_ast` per produrre dati destinati all'AST backend.
  Il contratto di `sync_git_tree` e `populate_backend_ast` va discusso e
  validato con l'agent del progetto backend; in particolare, la scelta tra
  parsing AST locale in Hades e invio di sorgenti/metadata al backend resta
  sospesa finche' non viene chiarito lo schema AST esistente lato Laravel.
  Nota implementazione 2026-06-30: aggiunta una slice locale MVP per backend
  Hades. `hades backend setup` verifica il bootstrap token, registra l'agent,
  salva il token derivato in `.env`, abilita `memory.provider:
  hades_backend` e inizializza lo storage SQLite profilo-scoped. `hades
  project link/unlink` crea e disabilita binding workspace con fingerprint,
  display path redatto e metadata git. `hades backend sync` effettua un pull
  one-shot dei job, persiste idempotentemente la coda locale, esegue solo
  capability read-only allow-listate, mette in `waiting_confirmation` i job che
  lo richiedono e invia risultati bounded/redatti al backend. Restano aperti:
  conferma utente desktop/TUI per job sospesi, retry/backoff completo,
  cleanup job/cache, integrazione piggyback nei cicli agent e validazione con
  contract OpenAPI Laravel.
  Nota implementazione 2026-06-30 bis: `hades backend sync` ora legge anche
  `memory/snapshot`, aggiorna la cache shared memory locale, invia proposal
  pending a `memory/proposals`, aggiorna stati `accepted/refused/conflicted` e
  registra `last_sync_summary` / `last_sync_error` redatti nello SQLite locale.
- [ ] Istruzioni backend Laravel: documentare route, variabili ambiente,
  generazione token, install command per developer, download installer,
  registrazione agent/progetto, MCP/API, WebSocket e controlli di salute.
  Prompt operativo iniziale: `docs/hades-backend-laravel-prompt.md`. Canale
  operativo opzionale: quando serve chiarire o verificare dettagli backend, e'
  possibile collegarsi via SSH al server backend, avviare una sessione Codex
  nel progetto Laravel e coordinarsi con l'agent di quel progetto. Da usare in
  particolare per definire endpoint job dedicato, payload, limiti e schema di
  persistenza per `sync_git_tree` e `populate_backend_ast`.
  Decisione 2026-06-30: le domande lato backend non vanno chiuse per
  supposizione locale; quando servono contratti Laravel, endpoint, schema dati,
  limiti, job, AST, memory o Persephone, l'utente fara' dialogare Codex con
  l'agent backend e quella conversazione diventera' la fonte operativa per la
  spec Hades.
  Nota 2026-06-30: creato `docs/backend-agent-coordination.md` come documento
  operativo per preparare le domande all'agente backend, registrare risposte,
  domande inverse, migliorie e conclusioni future da implementare.
  Nota 2026-06-30 bis: agente backend educato via SSH con il contratto Hades
  gia' implementato; ha creato il piano remoto
  `ai-sandbox/docs/hades-backend-implementation-plan-2026-06-30.md` e ha
  confermato correzioni importanti: `agent_id` Hades come external id,
  `workspace_binding_id` generato dal backend, project token solo bootstrap,
  route operative con agent token e capability sempre intersecate con policy
  backend. Sintesi registrata in `docs/backend-agent-coordination.md`.
- [ ] Documentazione progetto Hades: creare una documentazione organica e
  stabile per utenti/developer del progetto, distinta dall'uso temporaneo di
  `docs/CODEX_AGENTS.md`. Deve coprire overview, installazione one-liner,
  setup backend Laravel, project linking, shared memory, job operativi,
  Persephone, doctor/troubleshooting e flussi developer.
- [ ] Profilo dipendenze curato per Hades, evitando `.[all]` upstream dove
  include componenti esclusi dai default della fork.
  Decisione: il one-liner installa Python e le dipendenze minime necessarie per
  runtime, dashboard e TUI. Browser tools e componenti pesanti restano opzionali
  o best-effort; ogni componente mancante deve pero' essere installabile
  successivamente con un singolo prompt/comando guidato, senza configurazione
  manuale dispersiva.
- [ ] Distribuzione PyPI della fork: decidere package name, console scripts,
  extras Hades e flusso `pipx install`/`pip install` con postinstall
  guidato.
- [ ] Distribuzione npm della fork: valutare wrapper npm leggero che installa o
  avvia il runtime Python, invece di pubblicare l'intera app Python come
  pacchetto npm.
- [ ] Diagnostica `doctor` specifica Hades: token valido, backend
  raggiungibile, progetto collegato, MCP configurato, memory provider attivo,
  WebSocket/inbox operativo, endpoint job dedicato raggiungibile, coda SQLite
  job apribile, conteggi job per stato, job bloccati, stato degraded della
  shared memory, coda create/update shared-memory pending, cache shared-memory
  orfane/disabilitate e ultimi errori del dispatcher. Quando
  `memory.orphaned_cache_retention_days` e' `0`, le cache orfane devono essere
  mostrate con warning piu' severo perche' non scadranno automaticamente, ma
  senza cambiare l'exit code di `doctor`.
  `doctor` e' anche il
  namespace per comandi di manutenzione/diagnostica non frequenti, incluso
  `hades doctor cleanup --orphaned-cache`.
  Nota implementazione 2026-06-30: `hades doctor` ora include una sezione
  Hades Backend con configurazione agent, presenza token e conteggio workspace
  linkati. Mancano ancora health remoto, conteggi job/proposte/cache, stato
  degraded dettagliato, Persephone e cleanup `doctor cleanup --orphaned-cache`.
  Nota implementazione 2026-06-30 bis: `hades doctor` ora include anche health
  e capabilities remoti best-effort, conteggi job/proposte, ultimo sync e
  ultimo errore sync redatto. Mancano ancora cleanup cache/job, reporting
  doctor verso Laravel e stato Persephone reale.
- [ ] Modalita' installazione non-interattiva per team/CI: configurabile con
  token, backend URL e project id senza wizard TTY.
- [ ] Skill/plugin personali.
- [ ] Skill operativa per consultare e aggiornare il backend di conoscenza
  condivisa durante i task di sviluppo.
- [ ] Memory provider dedicato al backend condiviso, con recall automatico,
  sync turni, provenance e scoping per progetto/developer.
  Decisione degrado: se il workspace e' linkato ma il backend condiviso e'
  irraggiungibile, Hades mantiene attiva la memoria locale per recall e update,
  disabilita il recall shared-memory backend e accoda localmente richieste
  create/update destinate alla shared memory per retry successivo. Le delete
  richieste durante il degrado si applicano solo alla memoria/cache locale e non
  vengono sincronizzate come cancellazioni backend. Anche quando il backend e'
  raggiungibile, Hades non cancella mai direttamente la memoria condivisa:
  invia solo richieste/proposte di aggiornamento o cancellazione e Laravel
  decide se applicarle. Le richieste update/delete devono portare almeno
  `memory_id`, `base_version`/`etag`, `intent`, `provenance` e sintesi del
  cambiamento; dettaglio da validare con l'agent backend. Se Laravel rifiuta o
  marca non applicabile la proposta, Hades registra lo stato locale `refused`
  con causa obbligatoria e lo mostra come `refused: <causa>`. Se una proposta
  resta pending troppo a lungo, Hades continua con fallback a memoria locale e
  copia cache locale della shared memory dall'ultimo sync valido, marcata come
  cached/stale con eta', versione/etag e conteggio pending in `doctor`/status.
  La soglia pending e' combinata: policy backend come primaria, config locale
  Hades come fallback/guardrail; dettaglio e precedenza da decidere con l'agent
  backend.
  Nota implementazione 2026-06-30: aggiunto plugin memory provider
  `hades_backend`. Il provider si attiva solo con agent registrato e workspace
  linkato, usa cache locale `memory_cache` come recall shared/stale e converte
  write memory in proposte locali pending invece di scrivere direttamente sul
  backend. Mancano sync snapshot/delta live, retry proposte pending, gestione
  refused/conflict completa e UI/status dedicati.
  Nota implementazione 2026-06-30 bis: sync manuale aggiorna cache locale da
  snapshot backend e invia proposal pending aggiornandone lo stato. Restano da
  integrare retry/piggyback nel ciclo agent e UX per refused/conflict.
- [ ] MCP/API client per kanban, wiki, modifiche recenti e messaggistica tra
  agent installati dagli sviluppatori.
- [ ] Dispatcher locale per richieste operative backend: ricevere JSON
  capability-scoped, salvarlo in coda/crontable locale, eseguire quando
  possibile, pubblicare stato/risultato/errori al backend e bloccare payload non
  allow-listati o fuori workspace. Capability iniziali: `read_files` per file
  espliciti e `project_inspection` per raccolte intelligenti bounded. Lifecycle
  stato: `received` dopo persistenza locale, poi `started`, `completed` o
  `failed`. Retry/backoff governati dal backend con fallback locale
  conservativo. Esecuzione automatica solo per `read_files` e intent
  safe/metadata entro soglia; conferma locale per job che leggono molti file o
  producono payload ampi. Soglie nel payload backend, fallback locale prudente,
  da validare con l'agent backend. Superficie approvazione: desktop app
  primaria, CLI/TUI fallback; stato `waiting_confirmation` per job sospesi in
  attesa dell'utente, con transizione a `expired` alla deadline. Risultati:
  `summary`, `provenance` e allegati bounded, con redazione locale automatica
  prima dell'upload e metadata di redazione; allegati omessi quando la densita'
  di segreti e' alta. Storage locale: SQLite nello storage del profilo Hades,
  con stato persistente interrogabile da desktop app, CLI e TUI. Idempotenza:
  se Hades riceve di nuovo lo stesso `job_id`/idempotency id, non crea una
  nuova run e non lo esegue due volte; aggiorna solo metadati non distruttivi e
  mantiene lo stato locale autorevole. Cancellazione backend: un job consegnato
  puo' passare a `cancelled` se non e' ancora `started`; se e' gia' in
  esecuzione, Hades prova a interromperlo solo quando l'operazione e' safe e
  best-effort, altrimenti completa/fallisce secondo stato reale. Unlink
  workspace: quando un workspace binding viene rimosso, i job collegati restano
  nello storico SQLite ma non vengono piu' eseguiti; quelli non terminali e non
  ancora `started` passano a `unlinked` e Hades notifica il backend. Se ci sono
  job non terminali, il comando mostra solo un alert `y/n`: `y` procede, `n`
  annulla. In modalita' non interattiva, abortisce di default e richiede un
  flag esplicito tipo `--yes` per procedere. Avvio
  dispatcher prima fase: integrato nei cicli Hades esistenti
  memory/setup/health/sync quando l'agent e' attivo; niente daemon separato
  obbligatorio. In ambiente headless, i job che richiedono conferma restano
  `waiting_confirmation` fino alla deadline e possono essere approvati via
  CLI/TUI quando disponibile; nessuna auto-approvazione implicita da flag
  trusted backend. Retention locale di job e risultati terminali: policy dal
  backend per stato/capability, con fallback locale prudente e cleanup
  periodico. I risultati job non aggiornano direttamente la shared memory da
  Hades: per definizione sono richiesti dal backend, quindi Hades invia
  risultato/provenance/job id e il backend decide se indicizzare, archiviare o
  promuovere a memoria condivisa. Auth endpoint job: stesso token
  project-scoped/agent registration usato per backend e memory, con `agent_id`,
  `project_id` e `workspace_binding_id`.
  Nota implementazione 2026-06-30: implementati storage SQLite job,
  executor locale read-only per `read_files`, `project_inspection`,
  `sync_git_tree` e `populate_backend_ast`, redazione segreti, path containment
  e `hades backend sync` come primo pull/persist/execute/upload manuale. Restano
  da fare integrazione nei cicli agent, approvazioni utente, cancellazione,
  scadenze, retry/backoff e policy result retention.
  Nota implementazione 2026-06-30 bis: aggiunta persistenza diagnostica del
  sync (`last_sync_summary`, `last_sync_error`) e visibilita' in RPC TUI
  `backend.status`, con conteggi job/proposte per stato.
- [ ] Layer realtime interno all'agent per ricevere eventi WebSocket, salvare
  inbox locale, notificare la UI/sessione attiva e collegare gli eventi al
  memory provider.
- [ ] `persephone`: definire contratto endpoint WebSocket lato Laravel e plugin
  lato agent per realtime, inbox locale e notifiche tra agent. Persephone e'
  principalmente il layer di comunicazione tra istanze Hades di developer
  diversi; l'interazione diretta con Laravel e l'eventuale uso per job backend
  sono follow-up da decidere, non il meccanismo primario della prima fase.
- [ ] Policy per condividere memorie personali locali con il backend: opt-in,
  classificazione privacy, provenance, revisione/validazione server-side e
  possibilita' di revoca o non-pubblicazione.
- [ ] Profili subagent locali: ruoli predefiniti in config locale con
  modello/provider, budget, toolset e policy; skill dedicata per usarli senza
  esporre questa scelta al backend.
- [ ] Skill decisionale di model-routing per software engineering: usare il
  modello piu' forte per pianificazione/decisione e assegnare dinamicamente ai
  subagent profili locali piu' economici o specializzati in base a task, ruolo,
  toolset, costo, latenza e confidenza. L'agente deve scegliere autonomamente
  tra i modelli consentiti dalla configurazione locale, senza richiedere
  assegnazioni manuali per ogni delega.
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
- [x] Presenza e stato di `.venv`.
  Nota: `.venv` usa Python 3.13.14; installato l'extra locale `.[dev]` per
  rendere disponibili `pytest`, `pytest-asyncio` e `ruff`.
- [x] Presenza di install Node locale.
  Nota: Node v26.3.0 e npm 11.16.0 presenti; il workspace `ui-tui` installa
  `vitest` e `typescript` tramite i manifest esistenti.
- [x] Quali test sono ragionevoli da eseguire localmente senza setup pesante.
  Nota: per la bonifica rebranding corrente hanno girato `scripts/run_tests.sh`
  sui test TUI/gateway/plugin mirati, `npm run --prefix ui-tui test --
  setupBranding.test.ts`, `npm run --prefix ui-tui typecheck`, `ruff check`
  sui file Python toccati, `python3 scripts/docs_audit.py` e `git diff --check`.

## Note

- `AGENTS.md` deve restare vicino alla versione originale del progetto.
- Le istruzioni specifiche per Codex e per questa fork vivono in
  `docs/CODEX_AGENTS.md`.
- Ogni task futuro deve aggiornare questo piano quando completa una voce.
