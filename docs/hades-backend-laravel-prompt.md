# Prompt backend Laravel per integrazione Hades

Usa questo prompt per far implementare o verificare il lato backend Laravel
necessario all'onboarding Hades. Il backend deve essere la fonte autorevole per
progetti, token, registrazione agent, memoria condivisa e Persephone.

## Contesto Hades

Hades e' una fork locale dell'agent Hermes. L'installazione principale avviene
tramite one-liner generato dalla dashboard Laravel. Il comando contiene backend
URL, project id e token project-scoped. Il token puo' essere riutilizzato per
installare piu' agent Hades sullo stesso progetto nella prima fase, ma deve
restare revocabile.

Un singolo profilo locale Hades puo' collegare piu' progetti backend. La
directory/workspace corrente deve risolvere il progetto backend tramite mapping
locale esplicito per path/repo root. Il mapping viene creato dall'install o da
un comando tipo `hades project link`, che invia una request al backend per
confermare il binding progetto/workspace/agent. Se il workspace non e'
riconosciuto, la shared memory deve essere disabilitata del tutto finche' il
workspace non viene linkato: niente recall, niente read e niente
create/update/delete sul backend. L'agent deve poter continuare a lavorare
localmente senza leggere o scrivere memoria project-scoped in modo ambiguo. Se
il workspace e' linkato ma backend/shared memory e' temporaneamente
irraggiungibile, Hades continua a usare e aggiornare la memoria locale; il
recall shared-memory backend resta disabilitato, mentre le create/update
destinate alla shared memory vengono accodate localmente come richieste/proposte
e ritentate. Le delete richieste durante il degrado si applicano solo alla
memoria/cache locale e non generano tombstone o retry di cancellazione verso il
backend. In generale l'agent locale non cancella mai direttamente memoria
condivisa sul backend: puo' solo inviare richieste/proposte di creazione,
aggiornamento o cancellazione con intent/provenance, e Laravel resta
autoritativo nel decidere se applicarle. Le richieste update/delete verso
Laravel devono includere almeno `memory_id`, `base_version`/`etag`, `intent`,
`provenance` e una sintesi del cambiamento richiesto, cosi' il backend puo'
accettare, rifiutare o marcare conflitto; lo schema definitivo va discusso con
l'agent backend. Quando Laravel rifiuta una proposta update/delete o la
considera non applicabile per conflitto, deve restituire una causa leggibile e
stabile; Hades la registra localmente come stato `refused` e la mostra come
`refused: <causa>`. Se una proposta resta pending troppo a lungo, Hades non
blocca il task: usa fallback a memoria locale e, se disponibile, una copia cache
locale della shared memory aggiornata all'ultimo sync valido. La cache e'
degradata/non autoritativa e deve essere tracciabile con eta', versione/etag e
numero di proposte pending. La soglia per considerare una proposta "pending
troppo a lungo" deve essere combinata: backend come fonte primaria tramite
discovery/capabilities o policy della proposta, config locale Hades come
fallback/guardrail quando il backend non espone la soglia o non e'
raggiungibile. Valore, precedenza e override vanno decisi con l'agent backend.

## Richiesta al backend

Implementa o conferma le seguenti superfici API:

1. Generazione comando install
   - Endpoint o controller dashboard che genera il one-liner per progetto.
   - Inclusione sicura di backend URL, project id e token.
   - Script hosted `install.sh` per Linux/macOS/WSL e `install.ps1` per
     Windows nativo sotto dominio Hades.
   - Nessuna apertura automatica di UI dopo install; output finale con check e
     istruzioni.

2. Token project-scoped
   - Token associato a un singolo progetto.
   - Token riutilizzabile per piu' agent sullo stesso progetto, per ora.
   - Revoca lato dashboard.
   - Endpoint di verifica token che restituisce progetto, capability abilitate
     e stato di revoca/scadenza.
   - Lo stesso token project-scoped/agent registration autentica anche
     l'endpoint job dedicato nella prima fase.

3. Registrazione agent
   - Endpoint per registrare un agent installato.
   - Input: project id, token, agent id generato localmente, label generata da
     macchina/profilo, versione Hades, piattaforma, capabilities locali.
   - Output: agent record, eventuale nome normalizzato, capability backend.
   - Endpoint per rinominare agent dopo la registrazione.

4. Progetti multipli nello stesso profilo locale
   - Endpoint per confermare che un project id esiste ed e' accessibile dal
     token.
   - Risposte chiare quando un token e' valido ma il progetto non e'
     accessibile.
   - Contratto per collegare piu' progetti allo stesso agent/profilo senza
     reinstallare.
   - Endpoint per confermare un mapping locale esplicito path/repo root tramite
     `hades project link`, associando project id, agent id e workspace binding.
     Il backend deve restituire un `workspace_binding_id` stabile; path/repo
     root restano contesto locale. Dettaglio da validare con l'agent backend.
   - Non devono esistere due binding per lo stesso path/repo root. Se il path e'
     gia' associato allo stesso progetto, il link e' idempotente e non crea
     duplicati; se e' associato a un progetto diverso, Hades deve bloccare e
     richiedere conferma esplicita di re-link mostrando progetto attuale e nuovo
     progetto.
   - Endpoint/contratto per `hades project unlink`: rimuove il binding locale e
     notifica il backend, senza cancellare memory o storico job.
   - Dopo unlink, i job locali collegati a quel `workspace_binding_id` devono
     restare nello storico SQLite ma non essere piu' eseguibili; i job non
     terminali non ancora `started` passano a stato terminale `unlinked` e
     Hades comunica la transizione al backend.
   - Se ci sono job non terminali, `hades project unlink` deve mostrare un
     alert semplice `y/n`: `y` procede con unlink e transizione dei job, `n`
     annulla senza modifiche.
   - In modalita' non interattiva, se esistono job non terminali, `hades project
     unlink` deve abortire di default con errore esplicito e procedere solo con
     flag intenzionale tipo `--yes`.
   - Il link flow deve funzionare sia da CLI/TUI, percorso principale del
     workflow developer, sia da desktop app come superficie user-facing.
   - Quando Hades si trova in una directory/workspace non linkato, deve mostrare
     un warning e suggerire `hades project link` senza bloccare il lavoro locale.
   - Regola backend: ogni operazione di shared memory deve includere project id
     e workspace/repo binding riconosciuto, oppure essere rifiutata.
   - Se il workspace e' linkato ma backend/shared memory e' temporaneamente
     irraggiungibile durante un task, Hades continua a usare e aggiornare la
     memoria locale. Il recall shared-memory backend resta disabilitato finche'
     il backend non torna disponibile; le richieste/proposte create/update
     destinate alla shared memory vengono salvate in coda locale e ritentate,
     mentre le delete restano solo locali e non vengono sincronizzate come
     cancellazioni backend. Il degrado e la coda pending devono essere visibili
     in `doctor`/status.
   - Dopo `hades project unlink`, cache e stato locale della shared memory per
     quel progetto/binding restano nello storage locale come storico/cache
     disabilitata, senza recall o sync finche' il workspace non viene rilinkato.
   - Quando un workspace viene rilinkato dopo unlink, Hades puo' riattivare
     quella cache solo se il backend conferma stesso progetto e
     binding/fingerprint compatibile; prima di consentire recall deve eseguire
     sync/refresh dal backend.
   - Se il sync/refresh dopo relink fallisce, il link resta valido ma la shared
     memory rimane disabilitata/degradata finche' il refresh non riesce; Hades
     mostra un warning e continua il lavoro locale senza recall/sync backend.
   - Se il backend conferma lo stesso progetto ma binding/fingerprint non
     compatibile, il link resta valido come nuovo binding: la vecchia cache
     resta orfana/disabilitata e non viene usata automaticamente.
   - Le cache orfane restano nello storage locale finche' non scade una
     retention locale configurabile e devono essere visibili in `doctor`/status
     come orphaned cache. La durata della retention si configura in
     `config.yaml` locale Hades con la chiave
     `memory.orphaned_cache_retention_days`, come singolo valore globale per
     tutto il profilo, con default iniziale 90 giorni. Il valore deve essere un
     intero `>= 0`: valori positivi indicano giorni di retention, `0` significa
     retention infinita e quindi nessuna cache scade per eta'. Se la chiave
     manca si usa il default 90; se e' invalida, Hades mostra warning e usa
     fallback 90. Nella prima fase non sono previsti override per progetto,
     workspace o binding. Il backend puo' solo suggerire default o limiti
     tramite discovery/capabilities, senza essere fonte autoritativa. Deve
     esistere anche un comando manuale di cleanup:
     `hades doctor cleanup --orphaned-cache`. Di default rimuove solo cache
     orfane gia' scadute; il flag `--all` include anche cache orfane non ancora
     scadute. Eccezione: se `memory.orphaned_cache_retention_days` e' `0`, il
     cleanup manuale senza `--all` puo' rimuovere tutte le cache orfane,
     trattando la retention infinita come pulizia manuale libera. Con retention
     `0`, `doctor`/status mostra le cache orfane come warning piu' severo,
     evidenziando che non scadranno mai automaticamente e richiedono cleanup
     manuale se si vuole rimuoverle; questo warning non modifica l'exit code di
     `doctor`, che resta `0` e non blocca script o CI. Prima della conferma mostra un riepilogo con conteggi e
     dimensione stimata per progetto/binding, senza dettagli dei contenuti. Il
     namespace `doctor` diventa il riferimento per comandi analoghi di
     manutenzione/diagnostica non frequenti. Il comando chiede conferma `y/n` in
     modalita' interattiva; in modalita' non interattiva richiede `--yes` e
     altrimenti abortisce senza modifiche. Il cleanup e' solo locale: non
     richiede endpoint o evento dedicato verso il backend; il backend vede al
     massimo lo stato aggiornato tramite `doctor`/status successivo. Le cache
     orfane scadute non vengono pulite automaticamente dai cicli Hades:
     `doctor`/status le segnala e il cleanup resta manuale.

5. Memory provider condiviso
   - Endpoint per read/recall memory project-scoped.
   - Endpoint per richieste/proposte create/update/delete memory
     project-scoped, con Laravel autoritativo sull'applicazione effettiva.
   - L'agent locale non deve mai poter cancellare direttamente memoria
     condivisa sul backend; una cancellazione e' sempre una richiesta
     valutabile/rifiutabile dal backend.
   - Le richieste update/delete devono includere almeno `memory_id`,
     `base_version`/`etag`, `intent`, `provenance` e sintesi del cambiamento;
     validare con l'agent backend se servono campi separati per conflict status,
     approvazione, superseded/rejected e actor audit.
   - Se Laravel rifiuta o marca non applicabile una proposta update/delete,
     deve rispondere con causa obbligatoria; Hades espone lo stato locale come
     `refused: <causa>` in UI/CLI/TUI e `doctor`/status.
   - Se una proposta resta pending troppo a lungo, Hades deve poter continuare
     con fallback a memoria locale e copia cache locale della shared memory
     dall'ultimo sync valido, marcata cached/stale. Validare con l'agent backend
     soglie/metadata e formato risposta per mostrare eta', versione/etag e
     conteggio pending. La soglia e' combinata: policy backend primaria,
     config locale Hades fallback/guardrail; decidere con l'agent backend
     precedenza, default e override.
   - Il backend deve supportare sync/cache locale della shared memory tramite
     snapshot o delta versionati per progetto/workspace binding, in modo che
     Hades possa usare una copia locale degradata quando il backend live non e'
     disponibile o una proposta resta pending.
   - Policy di rifiuto per tutte le operazioni shared memory quando il
     workspace non e' riconosciuto.
   - Policy di degrado per backend/shared memory temporaneamente irraggiungibile
     con workspace linkato: memoria locale attiva e aggiornata, recall
     shared-memory backend disabilitato, richieste/proposte create/update
     shared-memory in coda locale per retry, delete solo locali e non
     sincronizzate come cancellazioni backend.
   - Schema minimo: project id, workspace id/path fingerprint, source agent id,
     actor/user, content, tags/type, timestamps, provenance.
   - Risposte distinguibili per: token invalido, progetto invalido, workspace
     non riconosciuto, capability memory disabilitata.

6. Persephone realtime/inbox
   - Endpoint WebSocket o SSE per realtime tra agent Hades dello stesso progetto.
   - Autenticazione con token o sessione derivata.
   - Join per project id e agent id.
   - Eventi minimi: agent online/offline, message/inbox item, memory changed,
     task/status update, backend capability changed.
   - Fallback HTTP polling o warning se Persephone non e' disponibile.
   - Persephone e' principalmente il layer per comunicazione realtime tra
     istanze Hades di developer diversi. L'eventuale interazione diretta con
     Laravel e l'uso per job backend sono follow-up da definire, non il canale
     primario delle richieste operative in prima fase.

7. Health e discovery
   - Endpoint health pubblico/non sensibile.
   - Endpoint authenticated capabilities/discovery.
   - Versione API, feature flags, URL realtime, stato memory provider.
   - Stato/capability dell'endpoint job dedicato, incluse capability operative
     abilitate e policy note al backend.
   - Messaggi macchina leggibili per il doctor Hades.
   - Il doctor Hades deve poter combinare segnali backend e locali: backend
     raggiungibile, endpoint job raggiungibile, auth/token/progetto validi,
     workspace linkato, memory provider attivo, coda SQLite locale apribile,
     conteggi job per stato, job bloccati, stato degraded shared-memory, coda
     create/update shared-memory pending, cache shared-memory
     orfane/disabilitate e ultimi errori dispatcher. Con
     `memory.orphaned_cache_retention_days` a `0`, le cache orfane devono essere
     mostrate con warning piu' severo perche' non scadranno automaticamente,
     senza modificare l'exit code di `doctor`.

8. Configurazione backend
   - Elenco variabili ambiente Laravel richieste.
   - Permessi storage/queue/cache necessari.
   - Requisiti database e migration.
   - Requisiti broadcast/WebSocket se Persephone usa canali realtime.

9. Richieste JSON verso agent locale
   - Contratto per esporre o inviare payload JSON con richieste specifiche per
     l'agent locale e dati correlati.
   - Il JSON deve essere dichiarativo e capability-scoped: configurazione,
     discovery, link workspace, health check, sync metadata, o richieste di
     dati esplicite.
   - La prima fase deve includere anche richieste operative read-only, per
     esempio "raccogli questi file/info dal progetto e inviali al backend".
   - Capability iniziali separate:
     - `read_files`: lettura deterministica di file/path/glob esplicitamente
       indicati dal backend, sempre dentro il workspace linkato.
     - `project_inspection`: raccolta intelligente bounded; l'agent puo'
       cercare file rilevanti, leggere manifest/config/test/documenti utili e
       sintetizzare una risposta con provenance.
   - `project_inspection` deve avere un `intent` obbligatorio da allow-list,
     usato da Hades per policy e limiti; puo' includere una `question`
     opzionale in linguaggio naturale per guidare la sintesi. La `question` non
     puo' ampliare capability, workspace o scope autorizzati dall'intent.
   - Intent iniziali: `summarize_project`, `find_config`, `list_routes`,
     `detect_test_framework`, `inspect_dependencies`,
     `summarize_recent_changes`, `find_docs`, `sync_git_tree`,
     `populate_backend_ast`.
   - `sync_git_tree` deve produrre una fotografia aggiornata e bounded del git
     tree/worktree per il backend.
   - `populate_backend_ast` deve produrre dati strutturati per popolare l'AST
     presente lato backend; schema, granularita', linguaggi supportati e limiti
     vanno definiti con l'agent backend.
   - Non decidere a priori se `populate_backend_ast` debba parsare localmente in
     Hades o inviare sorgenti/metadata al backend: questa scelta va presa dopo
     confronto con l'agent backend e verifica dello schema AST Laravel.
   - Il backend non deve inviare prompt liberi o comandi arbitrari da eseguire.
   - Ogni richiesta deve avere tipo, idempotency/request id, project id,
     workspace binding quando richiesto, capability richiesta, payload,
     scadenza e aspettativa di risposta.
   - Hades deve poter salvare queste richieste in una coda/crontable locale
     dell'agent e dispatcharle quando possibile.
   - Delivery principale: endpoint dedicato invocato dai cicli locali. Quando
     Hades esegue cicli memory, setup, health o sync, deve chiamare un endpoint
     tipo `GET /api/hades/agent/jobs` per ottenere richieste pendenti da
     inserire nella coda locale. Non richiedere WebSocket/Persephone per
     consegnare job nella prima fase.
   - Auth endpoint job: usare lo stesso token project-scoped/agent registration
     di backend/memory, includendo `agent_id`, `project_id` e
     `workspace_binding_id` nella request; path/repo root possono essere
     contesto locale.
   - Il contratto dell'endpoint job dedicato va discusso e validato con l'agent
     backend.
   - Ack e stato in due fasi: Hades invia `received` appena il job viene
     persistito nella coda locale; invia poi `started`, `completed` o `failed`
     durante l'esecuzione reale.
   - Il backend deve prevedere endpoint per ack/stato, risultato, errore e
     retry/backoff delle richieste operative.
   - Retry/backoff: il backend e' autorevole e dovrebbe includere campi come
     `max_attempts`, `retry_after` e `deadline`; se mancano, Hades puo' usare
     default locali conservativi per evitare retry infiniti o polling troppo
     aggressivo.
   - Esecuzione automatica: `read_files` e intent safe/metadata possono partire
     automaticamente quando restano entro limiti locali/backend; job che
     leggono molti file o producono payload ampi devono richiedere conferma
     locale prima dell'esecuzione.
   - Soglie auto/confirm: il backend dovrebbe specificarle nel payload del job
     o tramite capability/policy discovery; se mancano, Hades usa fallback
     locali prudenti. Questa policy va discussa e validata con l'agent backend.
   - Conferma locale: quando richiesta, Hades la espone prima nella desktop app
     e offre CLI/TUI come fallback tecnico per ambienti developer/headless. Il
     backend deve poter ricevere/mostrare uno stato tipo `waiting_confirmation`.
   - I job in `waiting_confirmation` restano pendenti fino alla `deadline`; se
     la deadline passa senza decisione utente, Hades marca il job `expired` e
     lo comunica al backend.
   - Risultati job: payload strutturato con `summary` leggibile/indicizzabile,
     `provenance` precisa e allegati/parti bounded per contenuti raw o
     voluminosi. Evitare risultati raw monolitici non limitati.
   - Redazione locale: prima dell'invio, Hades deve applicare redazione
     automatica di segreti e dati sensibili su summary, provenance utile e
     allegati. Il backend riceve contenuto redatto e metadata su redazioni e
     truncation, non segreti raw.
   - Se un file/allegato contiene segreti in modo limitato, Hades puo' inviare
     la versione redatta inline; se la densita' di segreti e' alta o la
     redazione rende il contenuto poco sicuro/utile, Hades deve omettere
     l'allegato e inviare solo metadata/provenance.
   - Storage locale Hades: coda job persistita in SQLite nello storage del
     profilo, interrogabile da desktop app, CLI e TUI. Il backend deve usare
     request id/idempotency id stabili per evitare duplicati.
   - Idempotenza: se Hades riceve di nuovo lo stesso `job_id`/idempotency id,
     non deve creare una nuova run e non deve rieseguire il job; puo'
     aggiornare solo metadati non distruttivi mantenendo lo stato locale
     autorevole.
   - Cancellazione: il backend puo' cancellare un job gia' consegnato solo se
     non e' ancora `started`, portandolo a `cancelled`. Se il job e' gia' in
     esecuzione, Hades puo' tentare interruzione solo se safe e best-effort;
     altrimenti comunica lo stato reale finale.
   - Unlink workspace: quando un workspace binding viene rimosso, i job locali
     associati restano nello storico SQLite ma non vengono piu' eseguiti; i job
     non terminali non ancora `started` passano a `unlinked` e Hades notifica il
     backend. Se ci sono job non terminali, Hades chiede solo conferma `y/n`
     prima di procedere; in modalita' non interattiva abortisce di default e
     richiede un flag esplicito tipo `--yes`.
   - Avvio dispatcher prima fase: Hades invoca il dispatcher dai cicli esistenti
     memory/setup/health/sync quando l'agent e' attivo. Non richiedere un daemon
     separato sempre attivo nella prima fase.
   - Headless: se un job richiede conferma locale e non c'e' desktop app
     disponibile, resta `waiting_confirmation` fino alla deadline e puo' essere
     approvato via CLI/TUI. Non introdurre auto-approvazione implicita basata su
     flag trusted backend.
   - Retention: il backend dovrebbe comunicare policy di conservazione per job e
     risultati terminali, eventualmente per stato/capability; se assente, Hades
     usa fallback locale prudente e cleanup periodico.
   - Gestione risultati/memory: Hades non deve scrivere direttamente shared
     memory quando completa un job. I job sono richiesti dal backend; Hades
     invia risultato, provenance e job id, poi Laravel decide se indicizzare,
     archiviare o promuovere il risultato a memoria condivisa.
   - Le richieste operative devono essere allow-listate e limitate al workspace
     linkato; azioni con side effect locale devono richiedere conferma lato
     Hades o restare fuori scope prima fase.

## Output atteso dal backend

Restituisci:

- Lista route con metodo, path, auth richiesta e payload JSON.
- Schema risposte di successo/errore.
- Policy token: durata, riuso, revoca, rotazione.
- Policy project/workspace mapping.
- Workspace binding: `workspace_binding_id` stabile restituito da
  `hades project link`; path/repo root come contesto locale, da validare con
  l'agent backend.
- Policy relink/cache: riattivazione della cache locale shared-memory solo dopo
  conferma backend di stesso progetto e binding/fingerprint compatibile, con
  sync/refresh obbligatorio prima del recall; se il refresh fallisce, link
  valido ma shared memory disabilitata/degradata finche' non riesce un refresh
  successivo. Se lo stesso progetto viene rilinkato con binding/fingerprint non
  compatibile, trattarlo come nuovo binding e lasciare la vecchia cache
  orfana/disabilitata, senza uso automatico. Le cache orfane restano fino a
  scadenza di una retention locale configurabile in `config.yaml` come
  `memory.orphaned_cache_retention_days`, singolo valore globale di profilo,
  default iniziale 90 giorni, intero `>= 0` con `0` per retention infinita,
  visibili in `doctor`/status; il backend puo' solo suggerire default o limiti
  via discovery/capabilities. Prevedere comando manuale
  `hades doctor cleanup --orphaned-cache` per cache orfane scadute, con `--all`
  per includere quelle non scadute; quando la retention e' `0`, cleanup senza
  `--all` puo' rimuovere tutte le cache orfane e `doctor`/status le segnala con
  warning piu' severo perche' non scadranno automaticamente, senza exit code
  non-zero. Riepilogo pre-conferma con conteggi e
  dimensione stimata per progetto/binding senza dettagli dei contenuti,
  conferma `y/n` in interattivo e `--yes` obbligatorio in non-interactive;
  cleanup solo locale, senza evento backend dedicato e senza cleanup automatico
  dai cicli Hades.
- Contratto JSON per richieste backend verso agent locale, se previsto.
- Contratto di scheduling/ack/stato/risultato per richieste operative locali.
- Contratto dell'endpoint dedicato per delivery pull delle richieste operative
  durante memory/setup/health/sync, da validare con l'agent backend.
- Auth endpoint job: stesso token project-scoped/agent registration di
  backend/memory, con `agent_id`, `project_id` e `workspace_binding_id`.
- Lifecycle job: `received`, `waiting_confirmation`, `started`, `completed`,
  `failed`, `expired`, `cancelled`, `unlinked`.
- Policy retry/backoff: backend autorevole con fallback locale conservativo.
- Policy auto/confirm per job operativi, incluse soglie o flag per richieste
  che leggono molti file o producono payload ampi; backend nel payload con
  fallback locale, da discutere con l'agent backend.
- Superficie conferma locale: desktop primaria, CLI/TUI fallback.
- Schema risultato job: `summary`, `provenance`, allegati bounded,
  truncation/size metadata, redaction metadata.
- Policy degrado shared memory: se backend/shared memory e' temporaneamente
  irraggiungibile ma workspace linkato, memoria locale attiva e aggiornata,
  recall shared-memory backend disabilitato, richieste/proposte create/update
  shared-memory accodate localmente e ritentate, delete solo locali e non
  sincronizzate come cancellazioni backend; stato degraded e coda pending
  visibili in doctor/status.
- Schema richieste update/delete shared memory: almeno `memory_id`,
  `base_version`/`etag`, `intent`, `provenance` e sintesi del cambiamento, da
  validare con l'agent backend insieme alla gestione conflitti e stati di
  accettazione/rifiuto. Per rifiuto o conflitto non applicabile, risposta
  backend con causa obbligatoria e stato locale Hades visualizzato come
  `refused: <causa>`.
- Cache shared memory locale: snapshot/delta versionati lato backend per
  permettere a Hades fallback su memoria locale + cache shared-memory
  cached/stale quando backend live non risponde o una proposta resta pending
  troppo a lungo; da discutere con l'agent backend per soglie, versione/etag e
  metadata di stato. La soglia pending e' combinata: backend primario via
  discovery/capabilities o policy proposta, config locale Hades come
  fallback/guardrail; precedenza e default da decidere con l'agent backend.
- Policy redazione risultati: redazione locale automatica prima dell'upload;
  Laravel non deve richiedere o ricevere segreti raw nei risultati job; allegati
  omessi quando la densita' di segreti e' alta.
- Storage locale job: SQLite per profilo Hades, con request id/idempotency id
  stabili lato backend e deduplicazione idempotente lato Hades.
- Policy cancellazione job: backend puo' cancellare prima di `started`; dopo
  `started` interruzione solo safe/best-effort.
- Policy unlink job: job locali storicizzati ma non piu' eseguibili quando il
  relativo `workspace_binding_id` viene rimosso; non terminali non ancora
  `started` portati a `unlinked` e notificati al backend; alert locale `y/n`
  prima di procedere quando esistono job non terminali; in modalita' non
  interattiva abort di default e procedura consentita solo con flag esplicito
  tipo `--yes`.
- Avvio dispatcher: integrato nei cicli Hades esistenti in prima fase, senza
  daemon separato obbligatorio.
- Policy headless: attesa `waiting_confirmation` fino a deadline, approvazione
  CLI/TUI possibile, nessuna auto-approvazione trusted implicita.
- Policy retention job/risultati terminali: backend autorevole con fallback
  locale e cleanup periodico.
- Policy risultati/memory: Hades invia risultati job al backend; Laravel decide
  indicizzazione, archiviazione o promozione a shared memory.
- Contratto dati per `sync_git_tree` e `populate_backend_ast`, da validare con
  l'agent backend.
- Lista capability esposte a Hades durante setup.
- Esempio di comando install generato dalla dashboard.
- Esempio di risposta `doctor`/health, includendo endpoint job, capability
  operative e segnali necessari a diagnosticare coda locale/job bloccati.
- Policy cache orfane shared-memory: retention configurabile in `config.yaml`
  locale con chiave `memory.orphaned_cache_retention_days`, come singolo valore
  globale di profilo, con default iniziale 90 giorni e validazione intero
  `>= 0` (`0` = retention infinita); backend solo come fonte di default/limiti
  suggeriti via discovery,
  visibilita' in doctor/status e comando manuale
  `hades doctor cleanup --orphaned-cache` per cache scadute, `--all` per
  includere cache non scadute, con retention `0` che consente cleanup manuale
  di tutte le cache orfane senza `--all` e impone warning piu' severo in
  doctor/status senza modificare l'exit code, riepilogo pre-conferma con conteggi e dimensione
  stimata per progetto/binding senza dettagli dei contenuti, conferma `y/n` in
  interattivo e `--yes` obbligatorio in non-interactive; cleanup solo locale,
  senza notifica backend dedicata e senza cleanup automatico dai cicli Hades.

## Vincoli

- Il backend non decide quale modello LLM usare.
- Non introdurre dipendenza da una sola macchina: piu' agent possono collegarsi
  allo stesso progetto.
- Non accettare read/recall o richieste create/update/delete di shared memory
  senza progetto e workspace riconosciuti.
- Non esporre all'agent locale una delete diretta della shared memory backend:
  l'agent puo' solo richiederla e il backend decide.
- Non usare richieste JSON backend come canale di esecuzione arbitraria:
  devono restare tipizzate, limitate da capability e verificabili da Hades.
- In assenza di memory provider o Persephone, Hades deve completare
  l'installazione con warning e moduli disabilitati.

## Coordinamento operativo opzionale

Se servono dettagli non documentati, e' possibile usare una sessione SSH sul
server backend, avviare Codex nel progetto Laravel e chiedere all'agent backend
di ispezionare route, migration, controller o config esistenti. Questo canale
serve solo per coordinamento tecnico e non e' un requisito runtime di Hades.
Usarlo in particolare per definire payload, limiti, persistenza e schema dati
degli intent `sync_git_tree` e `populate_backend_ast`.
