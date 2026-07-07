# Coordinamento con agente backend Laravel

Documento operativo per preparare e registrare il confronto con l'agente del
progetto backend Laravel. Non sostituisce `docs/implementation_plan.md`:
questo file raccoglie domande, risposte, migliorie emerse e conclusioni da
implementare in futuro.

## Fonti lette

- `docs/CODEX_AGENTS.md`
- `docs/implementation_plan.md`
- `docs/README.md`
- `docs/PROJECT_OVERVIEW.md`
- `docs/SOURCE_OF_TRUTH.md`
- `docs/hades-backend-laravel-prompt.md`

## Nota operativa per dubbi backend

Quando emergono dubbi sul contratto backend Laravel, si puo' riaprire in
autonomia il confronto con l'agente backend remoto via SSH:

```bash
ssh -tt ubuntu@162.19.229.31 'cd /home/ubuntu/dev-sandbox && codex resume --last --yolo'
```

Usare il comando per porre domande mirate, raccogliere risposte e aggiornare
questo documento con decisioni, rischi, migliorie e conclusioni operative.

## Contesto da portare al backend

Hades deve integrare un backend Laravel autorevole per progetti, token,
registrazione agent, shared memory, richieste operative locali e Persephone.
Il backend non decide modelli LLM, toolset locali o profili subagent. Hades
mantiene storage e fallback locali, ma non deve leggere o scrivere memoria
project-scoped backend senza progetto e workspace binding riconosciuti.

La prima fase privilegia:

- one-liner tokenizzato generato dalla dashboard Laravel;
- token project-scoped riutilizzabile per piu' agent sullo stesso progetto e
  revocabile;
- registrazione agent con `agent_id` locale e label macchina/profilo;
- mapping locale path/repo root -> progetto tramite `workspace_binding_id`;
- shared memory con Laravel autoritativo e Hades limitato a proposte;
- job read-only capability-scoped consegnati in pull/piggyback, non via
  Persephone;
- Persephone come layer realtime tra istanze Hades, non come canale job
  primario della prima fase.

## Domande preparate per il backend

### Installazione, token e registrazione agent

1. Quali route Laravel esistono gia' per generare il comando di installazione
   project-scoped e quali vanno create?
2. Il token project-scoped deve essere un token plain bearer, un token hashed in
   DB con lookup sicuro, o una coppia bootstrap token + agent token?
3. Quali campi restituisce l'endpoint di verifica token: progetto, capability,
   scadenza, revoca, URL realtime, policy job e memory?
4. La registrazione agent crea un token/credential derivato o continua a usare
   lo stesso token project-scoped?
5. Quali campi minimi vuole il backend per `agent_id`, label locale, versione
   Hades, piattaforma e capability locali?
6. Come vengono gestite revoca, rotazione e rinomina agent dopo la
   registrazione?

### Project linking e workspace binding

1. Quale route conferma il binding tra `project_id`, `agent_id` e workspace
   locale?
2. Il backend vuole ricevere path/repo root raw, hash/fingerprint, remote git
   URL, commit corrente, o una combinazione?
3. Quale definizione di `workspace_binding_id` stabile conviene usare e quando
   deve cambiare?
4. Come deve rispondere il backend quando lo stesso workspace viene linkato
   allo stesso progetto: idempotenza piena o refresh metadata?
5. Come deve rispondere quando lo stesso workspace risulta gia' legato a un
   progetto diverso?
6. Quale contratto serve per `hades project unlink`: notifica semplice,
   tombstone backend, stato archived, o solo evento audit?
7. Il backend deve conoscere gli stati locali `unlinked`, orphaned cache e
   cache disabled, oppure basta riceverli in health/doctor successivi?

### Shared memory

1. Quali entita' memory esistono gia' lato Laravel e quali campi sono
   obbligatori?
2. Quale schema usare per create/update/delete come proposta, inclusi
   `memory_id`, `base_version`/`etag`, `intent`, `provenance`, summary e actor?
3. Quali stati backend sono previsti per proposta accettata, rifiutata,
   conflicted, superseded, pending e applied?
4. La causa di rifiuto deve essere un codice stabile, un messaggio leggibile o
   entrambi?
5. Come vengono prodotti snapshot o delta versionati per consentire cache locale
   stale/cached su Hades?
6. Chi decide la soglia "pending troppo a lungo": policy discovery backend,
   policy per proposta o config locale Hades come fallback?
7. Quale policy backend deve applicare quando workspace/progetto non sono
   riconosciuti o memory capability e' disabilitata?

### Endpoint job e richieste operative locali

1. Quale route dedicata usera' Hades per il pull dei job, ad esempio
   `GET /api/hades/agent/jobs`?
2. Quali parametri sono obbligatori in request: `agent_id`, `project_id`,
   `workspace_binding_id`, capability locali, versione client, path fingerprint?
3. Quale schema job usare per `job_id`/idempotency id, capability, payload,
   deadline, retry/backoff, policy auto/confirm e limiti?
4. Confermiamo il lifecycle: `received`, `waiting_confirmation`, `started`,
   `completed`, `failed`, `expired`, `cancelled`, `unlinked`?
5. Quali endpoint servono per ack/stato, risultato, errore e cancellazione?
6. Quali job possono essere auto-eseguiti senza conferma e quali soglie devono
   imporre `waiting_confirmation`?
7. Le soglie auto/confirm arrivano sempre nel job, in discovery/capabilities, o
   entrambe?
8. Quale schema risultato vuole Laravel: `summary`, `provenance`, allegati
   bounded, metadata di truncation/redaction e lista omissioni?
9. Quale retention backend vuole per job terminali e risultati, e quale
   fallback locale Hades e' accettabile?

### `sync_git_tree` e `populate_backend_ast`

1. Esiste gia' uno schema AST lato Laravel? Se si', dove sono migration/model e
   quali campi aspettano?
2. Per `sync_git_tree`, il backend vuole file tree, git status, hash contenuti,
   commit HEAD, diff summary, linguaggi, dimensioni o altro?
3. Per `populate_backend_ast`, conviene parsare localmente in Hades oppure
   inviare sorgenti/metadata bounded al backend?
4. Quali linguaggi devono essere supportati nella prima fase?
5. Quali limiti di dimensione, numero file e profondita' simboli accetta il
   backend?
6. Come gestire redazione e omissione di file sensibili prima di popolare AST o
   git tree?

### Persephone realtime

1. Laravel prevede WebSocket, SSE o broadcast framework specifico per
   Persephone?
2. Quale auth/sessione realtime deriva dal token project-scoped o
   dall'agent registration?
3. Quali eventi minimi sono realistici nella prima fase: online/offline,
   message/inbox item, memory changed, task/status update, capability changed?
4. Serve persistenza inbox lato backend o Hades mantiene inbox locale e il
   backend consegna solo eventi?
5. Quale fallback HTTP polling deve dichiarare discovery quando Persephone non
   e' disponibile?

### Health, discovery e doctor

1. Quali endpoint health pubblici e authenticated capabilities esistono o vanno
   creati?
2. Quali capability deve leggere Hades durante setup: memory, jobs, realtime,
   project linking, AST, installer?
3. Quale forma hanno messaggi macchina leggibili per `doctor`?
4. Il backend puo' esporre policy suggerite per orphaned cache, pending memory,
   retention job e limiti payload, lasciando Hades autoritativo solo dove
   stabilito dal piano?
5. Quali esempi di risposta health/doctor possiamo usare come fixture futura?

## Risposte dell'agente backend

| Area | Risposta backend | Impatto su Hades |
| --- | --- | --- |
| Install/token | L'infrastruttura token piu' vicina e' `PluginTokenService` + `AuthenticatePluginToken`, ma `api_tokens.device_id` non modella bene agent multipli project-scoped. L'agente backend consiglia tabelle Hades dedicate. | Hades deve trattare il project token come bootstrap e preferire un agent token derivato dopo registrazione. |
| Agent registration | Serve registrazione esplicita di agent locale con `agent_id`, label, versione, platform e capabilities. | Hades deve generare/stabilizzare `agent_id`, salvare credential derivata e redigere token in log/status. |
| Project/workspace binding | Nessun contratto Hades/Persephone gia' presente nelle app path Laravel; il materiale piu' vicino e' workspace/plugin/work queue esistente. | Hades deve inviare fingerprint/redacted display path/git metadata, non assumere che path assoluti siano necessari o accettati. |
| Shared memory | Laravel deve restare autoritativo. Le proposte create/update/delete devono essere versionate, con provenance, policy e stati stabili. | Hades puo' proporre memoria, cache locale e gestire stale/degraded; create low-risk puo' essere auto-accepted da policy backend. |
| Jobs dispatcher | Esiste work queue vicina, ma serve endpoint Hades dedicato con lifecycle e capability scope. L'agente backend ha segnalato la differenza `canceled` vs `cancelled`. | Hades deve usare uno spelling API unico, consigliato `cancelled`, mappando internamente eventuali varianti backend. |
| `sync_git_tree` | Deve produrre snapshot bounded con limiti configurabili, no leakage di path/source raw, e test di validazione payload. | Hades deve redigere, limitare e tracciare omissioni/truncation. |
| `populate_backend_ast` | Deve importare artifact AST/symbol bounded; evitare invio grezzo di sorgenti e crescita incontrollata. | Hades deve partire da schema minimo versionato symbols/relations/provenance, non da full tree-sitter JSON come contratto iniziale. |
| Persephone | Prima milestone: inbox persistente + SSE; Reverb/WebSocket solo se serve realtime bidirezionale vero. | Hades deve progettare Persephone come server-relay persistente, non P2P e non canale primario job. |
| Health/discovery/doctor | Servono report doctor/capabilities, audit logs, metriche, pruning TTL e documentazione OpenAPI/contract. | Hades deve leggere discovery/policy backend e degradare in modo esplicito quando capability, realtime o memory non sono disponibili. |

## Domande del backend a Hades

| Domanda backend | Risposta Hades | Decisione/azione |
| --- | --- | --- |
| Hades puo' salvare/usare un token derivato dopo registrazione o deve usare il project token a ogni call? | Puo' e deve preferibilmente salvare un agent token derivato. Il project token e' bootstrap/project-scoped/revocabile. | Backend espone register che rilascia credential agent; Hades la persiste localmente in modo sicuro e la redige in output. |
| Persephone deve essere server-relay persistente o Hades prevede canali locali peer-to-peer? | Prima fase server-relay persistente. Niente P2P locale nella prima milestone. | Backend implementa inbox persistente + SSE/WS; Hades usa polling/SSE fallback e non usa Persephone per job primari. |
| Che schema AST puo' produrre Hades subito? | Nessuno schema fisso; Hades puo' produrre artifact/symbol summary bounded, versionato, con provenance, file hash/range e relations minime. | Backend propone schema minimo versionato; evitare full tree-sitter JSON come contratto obbligatorio iniziale. |
| Per i path locali basta `display_path` redatto + hash? | Si'. Non serve path assoluto leggibile in dashboard. | Identita' e auth usano fingerprint/git remote/project binding; display path serve solo per UX dopo redazione. |
| Le memory proposal `create` possono essere auto-accepted? | Si', per intent/capability low-risk secondo policy backend. Laravel resta autoritativo. | Create puo' diventare accepted/queued/rejected/transformed; update/delete richiedono etag/version/provenance e gating piu' severo. |

## Esecuzione M1 backend remoto - 2026-06-30

Stato: completata la slice M1 concordata nel backend remoto
`/home/ubuntu/dev-sandbox`, con tracking dettagliato in
`ai-sandbox/docs/hades-backend-m1-results-2026-06-30.md`.

Coordinamento SSH:

- Sessione backend agent riaperta con
  `ssh -tt ubuntu@162.19.229.31 'cd /home/ubuntu/dev-sandbox && codex resume --last --yolo'`.
- Primo brief M1 completo e secondo brief ridotto al solo test rosso sono
  rimasti bloccati in `Working` senza creare file.
- Ho interrotto lo stallo, implementato M1 direttamente via SSH e poi ho
  comunicato all'agente backend il risultato, il path del report remoto e le
  conclusioni operative per M2. L'agente backend ha risposto `ACK`.

M1 implementato:

- Route backend: `GET /api/hades/v1/health`,
  `POST /api/hades/v1/token/verify`, `POST /api/hades/v1/agents/register`,
  `GET /api/hades/v1/capabilities`.
- Tabelle dedicate: `hades_bootstrap_tokens`, `hades_agents`,
  `hades_agent_tokens`.
- Token dedicati Hades `hades_bootstrap_{ulid}|secret` e
  `hades_agent_{ulid}|secret`, con secret hashato, revoca, scadenza e scope.
- Registrazione agent idempotente per `project_id + external_agent_id`;
  `agent_id` locale resta separato da `backend_agent_id`.
- Intersezione capability dichiarate con policy backend; M1 supporta
  `read_files`, `sync_git_tree`, `populate_backend_ast`.
- `workspace_binding_id` non e' accettato dal client in M1; resta derivazione
  server-side per M2.
- Traefik espone `/api/hades/v1` senza BasicAuth, come gia' fatto per
  `/api/plugin/v1`, cosi' il client Hades puo' raggiungere il backend pubblico
  usando solo bearer token applicativi.

Verifiche remote:

- Test rosso iniziale: `7 failed`; health 404 e tabella
  `hades_bootstrap_tokens` mancante.
- Test verde Hades:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades/HadesM1ApiTest.php`
  ha passato `7 tests / 63 assertions`.
- Regressione auth:
  `HadesM1ApiTest.php + PluginAuthTest.php` ha passato
  `14 tests / 88 assertions`.
- `php -l` pulito su controller, middleware, service, migration e test Hades.
- `vendor/bin/pint --dirty` passato con `0 files`.
- `git diff --check` passato.
- `php artisan route:list --path=hades` mostra 4 route Hades.
- Migration runtime applicata con `php artisan migrate --force`.
- Smoke pubblico:
  `https://home-sweet-home.cloud/api/hades/v1/health` ritorna HTTP 200 JSON;
  `https://home-sweet-home.cloud/api/hades/v1/capabilities` senza token ritorna
  HTTP 401 JSON Laravel, non piu' BasicAuth Traefik.

Follow-up emersi da M1:

- [ ] Aggiungere provisioning/revoca bootstrap token da dashboard o comando
  setup, evitando inserimenti DB manuali.
- [ ] Implementare M2 workspace binding server-side e non accettare binding id
  arbitrari dal client.
- [ ] Aggiungere smoke Hades client reale con bootstrap token appena esiste il
  provisioning.

## Esecuzione memoria search backend remoto - 2026-07-06

Stato: completata la slice query-aware per shared memory Hades, con backend
Laravel remoto e provider locale Hades collegati.

Backend remoto:

- Branch remoto: `fase-2`.
- Commit remoto: `98a1f63 feat: add Hades memory search endpoint`.
- Route aggiunta: `GET /api/hades/v1/memory/search`, protetta da
  `hades.agent` e `throttle:plugin-api-light`.
- Controller aggiunto:
  `backend/app/Http/Controllers/Hades/MemorySearchController.php`.
- Contratto: richiede `project_id` e `workspace_binding_id`; accetta `query`,
  `domain` (`all`, `project_memory`, `logbook`, `wiki`, `agent_notes`,
  `source_chunks`), `limit` e `include_raw_chunks`.
- Il controller verifica che il token agent sia scoped allo stesso progetto e
  che il workspace binding sia linked per quell'agent.
- Cerca in `project_memory_entries` e nelle revisioni wiki correnti
  (`wiki_pages.current_revision_id` -> `wiki_revisions.id`).
- I raw source/file chunks restano esclusi per default, anche quando matchano
  la query; vengono restituiti solo con `include_raw_chunks=true`.
- La response include `version`/`etag`, `candidate_count`,
  `raw_chunks_omitted`, `freshness.workspace_head_commit`, `index_status` e
  `server_time`, cosi' Hades puo' distinguere live search, cache e staleness.

Integrazione Hades locale:

- `HadesBackendClient` espone `memory_search()`.
- Il provider memoria `hades_backend` prova prima la live search backend per
  auto-recall e tool `hades_backend_project_memory_search`.
- Se il backend non e' raggiungibile, non ha ancora la route o manca il token,
  il provider degrada alla cache locale esistente e redige l'errore live.
- Il tool result distingue `searched_cache_only: false` per risposte live e
  `searched_cache_only: true` per fallback cache.
- La fixture OpenAPI locale include `/api/hades/v1/memory/search`.

Verifiche remote:

- `php -l app/Http/Controllers/Hades/MemorySearchController.php`: passato.
- `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades/HadesM3SharedMemoryTest.php`:
  passato, `7 tests / 76 assertions`.
- `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades tests/Feature/PluginAuthTest.php`:
  passato, `31 tests / 289 assertions`.
- `vendor/bin/pint --test app/Http/Controllers/Hades/MemorySearchController.php routes/api.php tests/Feature/Hades/HadesM3SharedMemoryTest.php`:
  passato.
- `git diff --check`: passato.
- `php artisan route:list --path=hades`: mostra
  `GET|HEAD api/hades/v1/memory/search`.
- Smoke pubblico `https://home-sweet-home.cloud/api/hades/v1/health`: HTTP
  200.

Verifiche locali:

- `.venv/bin/python -m pytest tests/agent/test_hades_backend_memory_provider.py tests/hermes_cli/test_hades_backend_client.py`:
  passato, `33 passed`.
- `.venv/bin/python -m pytest tests/run_agent/test_run_agent.py tests/agent/test_hades_backend_memory_provider.py tests/hermes_cli/test_hades_backend_client.py`:
  passato, `439 passed`.
- `.venv/bin/python -m ruff check hermes_cli/hades_backend_client.py plugins/memory/hades_backend/__init__.py tests/agent/test_hades_backend_memory_provider.py tests/hermes_cli/test_hades_backend_client.py`:
  passato.
- `.venv/bin/python -m py_compile hermes_cli/hades_backend_client.py plugins/memory/hades_backend/__init__.py tests/agent/test_hades_backend_memory_provider.py tests/hermes_cli/test_hades_backend_client.py`:
  passato.

## Esecuzione project awareness slice - 2026-07-06

Stato: completata la slice su qualita' note, indicizzazione progetto e timeout
live-recall.

Backend remoto:

- Branch remoto: `fase-2`.
- Commit remoto: `317bb69 feat: index Hades project artifacts in memory search`,
  follow-up del commit `98a1f63`.
- `GET /api/hades/v1/memory/search` ora accetta anche `domain=artifacts` e,
  con `domain=all`, include gli artifact indicizzati del workspace linked.
- `MemorySearchController` legge `hades_agent_artifacts`, costruisce summary
  bounded dagli artifact `hades.git_tree.v1` con `project_index`, e restituisce
  risultati domain `artifacts` senza raw source.
- `MemoryImportService` mette in quarantena entry raw chunk
  (`file_chunk`, `source_chunk`, `hades.backend_wiki.file_chunk.v1`,
  `chunk_index`, `chunk_count`) come `raw_chunk_quarantined`, senza creare
  proposte di memoria. Questo separa source evidence da note/facts verificati.

Integrazione Hades locale:

- Live memory search nel provider `hades_backend` usa timeout di 2 secondi via
  `runtime.client_from_config(timeout=2.0)`, cosi' il prefetch non blocca a
  lungo il loop agent.
- Il tool `hades_backend_project_memory_search` accetta `domain=artifacts`.
- `sync_git_tree` continua a produrre `hades.git_tree.v1`, ma aggiunge
  `project_index` metadata-only (`hades.project_index.v1`) con:
  language counts, route Laravel, dependency manifests e migration list.
- L'artifact mantiene `raw_source_included=false`; l'indice espone struttura e
  provenance, non contenuto sorgente grezzo.

Verifiche remote:

- Test rossi confermati prima della modifica:
  `domain=artifacts` falliva con 422; import raw chunk creava 1 proposal.
- `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades tests/Feature/PluginAuthTest.php`:
  passato, `33 tests / 306 assertions`.
- `vendor/bin/pint --test app/Http/Controllers/Hades/MemorySearchController.php app/Services/MemoryImportService.php tests/Feature/Hades/HadesM3SharedMemoryTest.php`:
  passato.
- `git diff --check`: passato.

Verifiche locali:

- Test rossi confermati prima della modifica:
  live search chiamava il client senza timeout; `sync_git_tree` non esponeva
  `project_index`; il tool locale rifiutava `domain=artifacts`.
- `.venv/bin/python -m pytest tests/agent/test_hades_backend_memory_provider.py tests/hermes_cli/test_hades_backend_sync_runner.py tests/hermes_cli/test_hades_backend_client.py`:
  passato, `49 passed`.
- `.venv/bin/python -m pytest tests/run_agent/test_run_agent.py tests/agent/test_hades_backend_memory_provider.py tests/hermes_cli/test_hades_backend_sync_runner.py tests/hermes_cli/test_hades_backend_client.py`:
  passato, `455 passed`.
- `.venv/bin/python -m ruff check hermes_cli/hades_backend_jobs.py hermes_cli/hades_backend_runtime.py plugins/memory/hades_backend/__init__.py tests/agent/test_hades_backend_memory_provider.py tests/hermes_cli/test_hades_backend_sync_runner.py`:
  passato.

## Migliorie emerse

- [ ] Separare le tabelle Hades da `api_tokens.device_id` per supportare piu'
  agent sullo stesso progetto, revoca/rotazione e capability per agente.
- [ ] Standardizzare lo spelling pubblico API su `cancelled` e mappare
  eventuale `canceled` solo internamente o in compatibilita' transitoria.
- [ ] Aggiungere limiti configurabili, redazione e metadata di omissione per
  git tree, artifact AST e risultati job.
- [ ] Tenere Persephone in prima fase su inbox persistente + SSE, rimandando
  Reverb/WebSocket bidirezionale a quando esiste un requisito non coperto.
- [ ] Produrre contract docs/OpenAPI e fixture health/discovery/doctor prima
  della piena integrazione Hades.

## Conclusioni da implementare in futuro

- [ ] **M1 - Backend foundation.** Creare namespace Hades lato Laravel con
  migrations dedicate per projects/agents/workspace bindings/agent tokens o
  token grants, usando token hashed, revoca, rotazione, audit e capability
  scope. Endpoint minimi: install command, token verify, agent register,
  capabilities discovery.
- [ ] **M2 - Project linking.** Implementare binding idempotente tra
  `project_id`, `agent_id` e workspace fingerprint. Salvare `display_path`
  redatto, git remote hash/display, HEAD commit, platform e last_seen; rifiutare
  collisioni cross-project esplicite con codice stabile.
- [ ] **M3 - Shared memory.** Implementare snapshot/delta memory versionati,
  proposals create/update/delete con `base_version`/`etag`, provenance,
  `intent`, actor, status e reason code. Laravel resta source of truth; Hades
  usa cache locale solo come degraded/stale fallback.
- [ ] **M4 - Jobs endpoint.** Aggiungere pull dedicato tipo
  `GET /api/hades/agent/jobs` e update status/result/cancel. Payload con
  idempotency key, capability, policy `auto|confirm`, deadline, retry/backoff,
  limits, workspace binding e result schema bounded con redaction/truncation.
- [ ] **M5 - Git tree e AST.** Definire due artifact versionati:
  `sync_git_tree` per file tree/git metadata bounded e `populate_backend_ast`
  per symbols/relations/provenance. No raw source upload come default; includere
  omission metadata e limiti di file/bytes/depth.
- [ ] **M6 - Persephone.** Implementare inbox persistente project/agent-scoped,
  eventi minimi online/offline, inbox message, memory changed, capability
  changed e job status summary. Prima consegna via SSE/polling fallback;
  WebSocket/Reverb solo come evoluzione.
- [ ] **M7 - Doctor, docs e hardening.** Esporre health public, authenticated
  discovery, doctor report, audit logs, metriche, pruning TTL, retention jobs,
  OpenAPI/contract docs e fixture di errore. Hades deve mostrare stato degraded
  senza rompere operativita' locale quando backend non e' disponibile.

## Piano di implementazione concordato

### Backend Laravel

- Routes/API: aggiungere `routes/api.php` o file route dedicato Hades per
  `install-command`, `token/verify`, `agents/register`, `workspaces/bind`,
  `memory/snapshot`, `memory/proposals`, `agent/jobs`, `agent/jobs/{id}/status`,
  `agent/jobs/{id}/result`, `persephone/inbox`, `persephone/events`,
  `health`, `capabilities`, `doctor`.
- Controllers/services: creare controller sottili e servizi dedicati per token
  bootstrap, agent registry, workspace binding, memory proposal policy, job
  leasing/idempotency, artifact ingestion e Persephone inbox.
- Migrations/models: introdurre tabelle Hades dedicate per project tokens/agent
  tokens, agents, workspace bindings, memory records/proposals, jobs/results,
  artifact manifests/chunks, inbox events, audit logs e capability policy.
- Queues/jobs: usare Laravel queue per pruning TTL, materializzazione artifact,
  fanout eventi Persephone e retry job result processing. Non mettere
  esecuzione locale nel backend: il backend assegna richieste, Hades esegue.
- Tests: feature test per auth/revoca, idempotenza register/bind, conflitti
  workspace, memory proposal status, job lifecycle, result redaction,
  artifact limits, SSE/polling fallback e doctor payload. Unit test per policy
  memory/job/capability e hashing token.

### Hades

- Setup/install: consumare project token una tantum, registrare agent, salvare
  agent token derivato e aggiornare config/profile senza invalidare fallback
  locale.
- Binding: calcolare fingerprint stabile da repo/workspace, inviare path
  redatto e git metadata, gestire collisione e unlink con stato locale chiaro.
- Memory: leggere snapshot/delta, proporre mutazioni con etag/provenance,
  degradare a cache locale se backend e' down, esporre pending/conflicted in
  doctor.
- Jobs: implementare dispatcher pull/piggyback con capability allow-list,
  conferma utente quando policy lo richiede, idempotenza, cancellazione,
  risultati bounded e stati terminali.
- Artifacts: implementare `sync_git_tree` e `populate_backend_ast` come job
  locali con limiti, redazione, omission metadata, no raw source upload default
  e schema versionato.
- Persephone: client SSE/polling per inbox persistente; non usare Persephone
  per esecuzione job primaria nella prima fase.
- Doctor: aggiungere check backend URL/token/project/workspace/memory/jobs/
  realtime/artifacts con messaggi action-oriented.

## Avanzamento Hades locale 2026-06-30

- Implementato client HTTP Hades con namespace provvisorio `/api/hades/v1`,
  token bearer, redazione segreti e metodi per health/capabilities, verify,
  agent register, workspace bind, memory, jobs, artifacts e Persephone inbox.
- Implementato storage SQLite profilo-scoped `hades_backend.db` per agent,
  workspace bindings, job, memory proposals, memory cache e inbox events.
- Implementato `hades backend setup/status/sync`: setup usa project token come
  bootstrap e salva il token agent derivato; sync fa pull job, persiste in coda,
  esegue capability read-only allow-listate e carica risultato bounded/redatto.
- Implementato `hades project link/unlink` con workspace fingerprint, display
  path redatto, git metadata e binding id stabile restituito dal backend.
- Implementato memory provider `hades_backend` con recall da cache locale solo
  quando il workspace e' linkato e write trasformate in proposte pending.
- Implementato doctor base e RPC TUI `backend.status`.

Restano da coordinare con Laravel prima della piena integrazione: nomi route
finali/OpenAPI, schema definitivo AST/artifact, payload e limiti job, policy
memory proposal, health/doctor remoto e Persephone SSE/polling.

## Coordinamento remoto aggiornato 2026-06-30

Sessione SSH riaperta con:

```bash
ssh -tt ubuntu@162.19.229.31 'cd /home/ubuntu/dev-sandbox && codex resume --last --yolo'
```

Brief consegnato all'agente backend: Hades ha gia' implementato la slice MVP
con Bearer auth, namespace provvisorio `/api/hades/v1`, project token come
bootstrap, `agent_token` derivato dopo `agents/register`, workspace binding
idempotente, job pull read-only, memory snapshot/proposals, Persephone inbox e
doctor/status su health/capabilities.

Output backend creato:

```text
ai-sandbox/docs/hades-backend-implementation-plan-2026-06-30.md
```

Correzioni/decisioni backend rilevanti da recepire:

- `token/verify` deve accettare solo project token bootstrap; le route operative
  devono richiedere agent token.
- `agent_id` ricevuto da Hades va trattato come external/local id, non come PK
  trusted: Laravel crea il proprio `hades_agents.id` e mantiene
  `external_agent_id`.
- `workspace_binding_id` deve essere generato dal backend e non accettato dal
  client.
- `capabilities[]` dichiarate da Hades vanno sempre intersecate con policy e
  grants backend.
- `project_id`, `agent_id`, `workspace_binding_id` e job devono essere validati
  nello stesso project scope per status/result.
- `populate_backend_ast` parte da schema summary versionato, non full
  tree-sitter JSON e non raw source upload.

Il piano remoto conferma milestones Laravel M1-M4:

- M1: identity, token bootstrap, registration, health/capabilities.
- M2: workspace binding e shared memory proposals.
- M3: agent jobs, read-only dispatcher, status/result.
- M4: git tree, AST summary e Persephone inbox.

Avanzamento Hades aggiunto nello stesso step:

- `hades backend sync` ora fa anche snapshot memory, invio proposal pending e
  persistenza `last_sync_summary` / `last_sync_error`.
- `hades doctor` espone health/capabilities remoti, conteggi job/proposals e
  ultimo sync.
- RPC TUI `backend.status` espone conteggi job/proposals e stato sync.

## Esecuzione backend remoto M2-M4 - 2026-07-01

Stato: completate direttamente via SSH nel backend remoto
`/home/ubuntu/dev-sandbox/backend`, branch `fase-2`, le slice M2, M3 e M4
necessarie al client Hades locale gia' implementato. Non e' stato necessario
porre nuove domande bloccanti all'agente backend, ma resta valido il comando
SSH documentato sopra per dubbi futuri.

M2 implementato:

- `POST /api/hades/v1/workspaces/bind` autenticato con agent token.
- `POST /api/hades/v1/workspaces/{workspaceBinding}/unlink` autenticato con
  agent token.
- Nuova tabella `hades_workspace_bindings`.
- `workspace_binding_id` generato dal backend come ULID e non accettato dal
  client.
- Binding idempotente su `project_id + hades_agent_id + workspace_fingerprint`.
- Salvataggio solo di metadata redatti/hashati: `display_path`,
  `git_remote_display`, `git_remote_hash`, `head_commit`, `platform` e
  fingerprint. Nessun path raw richiesto dal client.
- Conflitto stabile `workspace_project_conflict` se lo stesso fingerprint e'
  gia' linkato a un progetto diverso.
- `unlink` conserva storico e marca il binding `unlinked`.

M3 implementato:

- `GET /api/hades/v1/memory/snapshot` per snapshot versionato della shared
  memory di progetto.
- `POST /api/hades/v1/memory/proposals` per proposal create/update/delete.
- Nuova tabella `hades_memory_proposals`; la source of truth resta
  `project_memory_entries`.
- Snapshot restituisce `version`, `snapshot_version`, `etag` e `items` con
  payload JSON decodificato.
- Proposal `create` low-risk auto-accepted con scrittura in
  `project_memory_entries` come `source=hades_agent`, `kind=proposal`.
- Idempotenza su `workspace_binding_id + local_proposal_id`.
- Binding `unlinked` blocca snapshot/proposal con codice
  `workspace_binding_unlinked`.

M4 implementato:

- `GET /api/hades/v1/agent/jobs` per pull di job `queued`, filtrati per
  `project_id`, `agent_id`, `workspace_binding_id` e `capabilities[]`.
- `POST /api/hades/v1/agent/jobs/{job}/status` per lifecycle
  `received`, `waiting_confirmation`, `started`, `completed`, `failed`,
  `expired`, `cancelled`, `unlinked`.
- `POST /api/hades/v1/agent/jobs/{job}/result` per risultati bounded gia'
  prodotti dal client Hades locale.
- Nuove tabelle `hades_agent_jobs` e `hades_agent_job_events`.
- Payload/result JSON restano strutturati; eventi status/result vengono
  tracciati separatamente.
- Lo spelling pubblico e' `cancelled`.
- `workspaces/{id}/unlink` marca `unlinked` i job non terminali collegati e
  lascia invariati quelli terminali.

Capability/discovery:

- `GET /api/hades/v1/capabilities` ora espone anche le route M2-M4.
- Policy aggiornata: `workspace_binding_required=true`, `memory=true`,
  `jobs=true`, `artifacts=false`, `persephone=false`.

Verifiche remote:

```bash
docker compose -f docker-compose.devboard.yaml exec -T app sh -lc \
  'APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades tests/Feature/PluginAuthTest.php'
```

Risultato: `23 passed`, `189 assertions`.

Altre verifiche:

- Test rossi eseguiti prima di ogni slice:
  - M2: endpoint workspace mancanti -> 404.
  - M3: endpoint memory mancanti -> 404.
  - M4: tabella `hades_agent_jobs` mancante -> errore atteso.
- PHP lint passato su controller, service, migration, route e test Hades.
- `./vendor/bin/pint` passato dopo formattazione dei nuovi file.
- `git diff --check` passato.
- `php artisan route:list --path=hades` mostra 11 route Hades.
- `php artisan migrate --force` ha applicato:
  - `2026_07_01_000001_create_hades_m2_workspace_bindings_table`
  - `2026_07_01_000002_create_hades_m3_memory_proposals_table`
  - `2026_07_01_000003_create_hades_m4_agent_jobs_tables`
- Smoke pubblico:
  - `https://home-sweet-home.cloud/api/hades/v1/health` -> HTTP 200.
  - `https://home-sweet-home.cloud/api/hades/v1/capabilities` senza bearer ->
    HTTP 401 JSON `Hades agent token is required.`

Verifica end-to-end client Hades locale -> backend pubblico:

- Creato un progetto Laravel temporaneo e bootstrap token a scadenza breve.
- Eseguito con `HERMES_HOME` temporaneo locale:
  - `hades backend setup --url https://home-sweet-home.cloud ...`
  - `hades project create ...`
  - `hades project link ...`
  - `hades backend sync`
- Seed remoto creato per una memory entry e un job `read_files` su
  `README.md` nel workspace temporaneo.
- Esito locale dopo sync:
  - job locale `completed`;
  - proposta memory locale `accepted`;
  - cache memory aggiornata con la seed remota;
  - `last_sync_summary`: `pulled=1`, `completed=1`,
    `memory_snapshots=1`, `proposals_synced=1`, errori `0`.
- Esito remoto dopo sync:
  - job Laravel `completed`;
  - 3 eventi job registrati;
  - proposal Laravel `accepted`.
- Cleanup sicurezza: revocati tutti i bootstrap token e agent token associati
  ai progetti temporanei E2E; conteggio finale token attivi E2E `0`.

Risultato operativo:

- Il backend remoto ora espone il contratto necessario per provare
  `hades backend setup`, `hades project link` e `hades backend sync` contro
  Laravel per setup, workspace binding, memory snapshot/proposals e job
  read-only.
- Restano fuori da M2-M4: provisioning dashboard dei bootstrap token,
  artifact upload M5, Persephone/inbox M6, OpenAPI definitivo, retention
  policy e admin UI per creare job/manual review.

## Open questions residue

- Nome esatto delle route e versione API (`/api/hades/v1/...` o namespace
  equivalente).
- Schema minimo definitivo per artifact AST e cardinalita' symbols/relations.
- Limiti iniziali numerici per file count, bytes, symbol depth, job result size
  e retention TTL.
- Policy di auto-accept memory create: allow-list intent/capability e audit
  richiesto.
- Forma finale di OpenAPI/contract fixtures condivise tra repo backend e Hades.
- Modalita' di migrazione se il backend vuole compatibilita' temporanea con
  `PluginTokenService`/`AuthenticatePluginToken`.

## Stato della conversazione remota 2026-06-30

La sessione SSH su `ubuntu@162.19.229.31` in
`/home/ubuntu/dev-sandbox` e' stata avviata con
`codex resume --last --yolo`. L'agente backend ha ispezionato parti del repo
Laravel, token service/middleware e migrations, poi ha fornito le domande e i
rischi riportati sopra. La richiesta di far scrivere direttamente un markdown
remoto e' rimasta bloccata in stato `Working` senza creare il file; la
consegna locale di questo documento usa quindi le risposte gia' ottenute dalla
conversazione e segna esplicitamente i punti ancora aperti.

## Punti da riportare nel piano vivo

Quando una conclusione diventa stabile, aggiornare anche
`docs/implementation_plan.md` sotto la voce pertinente senza spuntare la voce
principale finche' resta lavoro operativo.

## Fix esposizione Admin Hades 2026-07-01

Problema verificato: `https://home-sweet-home.cloud/admin/hades` era una route
Laravel/Inertia registrata e buildata, ma Traefik la inoltrava al container
`frontend` perche' il router Laravel pubblico copriva solo `/api`, `/sanctum`,
`/storage` e `/api/hades/v1`. Con BasicAuth valida la risposta era quindi la
SPA Nginx generica, non la pagina `Admin/Hades`.

Fix remoto applicato in `/home/ubuntu/dev-sandbox/docker-compose.devboard.traefik.yaml`:
aggiunto router `devboard-laravel-web` con priorita' 130 per `Path(/login)`,
`Path(/logout)`, `PathPrefix(/admin/hades)` e `PathPrefix(/build)`, mantenendo
`devboard-basic-auth`. Ricreato `devboard-app-1` con la compose base +
override Traefik.

Verifiche fresche:

- Senza BasicAuth: `/admin/hades` resta `HTTP 401` Traefik.
- Con BasicAuth: `/admin/hades` risponde da PHP/Laravel con redirect a
  `/login`, non piu' da Nginx frontend.
- Con BasicAuth: `/login` risponde `HTTP 200` con HTML Inertia e asset
  `/build/assets/app-BTNwdPjc.js` risponde `HTTP 200`.
- `https://home-sweet-home.cloud/api/hades/v1/health` resta `HTTP 200`.
- Suite remota: `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory:
  DB_URL= php artisan test tests/Feature/Hades tests/Feature/PluginAuthTest.php`
  -> 27 test passati.

## Diagnosi routing frontend 2026-07-01

Richiesta riportata all'agent backend remoto: il frontend sospetta che alcune
route pubbliche vadano ancora al frontend Laravel/Inertia legacy invece del
frontend React corretto. Ho chiesto diagnosi non distruttiva su route Laravel,
fallback SPA React, Traefik/nginx, `/login`, dashboard e asset/static serving.

Risposta sintetica dell'agent backend:

- Il sospetto e' confermato per le page-route: Laravel ha ancora pagine web
  legacy registrate per `/login` e `/admin/hades`, mentre il frontend React ha
  fallback SPA e asset statici propri.
- La causa operativa e' il router Traefik `devboard-laravel-web`, aggiunto nel
  fix precedente, che instrada verso Laravel anche `/login`, `/logout`,
  `PathPrefix(/admin/hades)` e probabilmente `/build`.
- I file/config coinvolti sono il compose override Traefik remoto
  `/home/ubuntu/dev-sandbox/docker-compose.devboard.traefik.yaml`, le route web
  Laravel/Inertia per login/admin e la build/fallback del frontend React.
- Public smoke senza BasicAuth: `/login` e `/api/dashboard/me` rispondono
  `401`; questo e' coerente con BasicAuth Traefik, ma il routing effettivo e'
  determinato dalle label Traefik sopra.

Fix consigliato dall'agent backend:

- Rimuovere `/login`, `/logout` e probabilmente `/build` dal router
  `devboard-laravel-web`.
- Lasciare a Laravel solo `/api/*`, `/sanctum/*`, `/storage/*` e le API
  agent-facing gia' separate.
- Far servire tutte le page-route pubbliche dal catch-all del frontend React.
- Per `/admin/hades`, decidere esplicitamente: se deve essere UI pubblica va
  portata nel frontend React; se resta un tool Laravel temporaneo, spostarla su
  un path legacy/admin separato per non interferire con la SPA.

Nessuna modifica remota applicata durante questa diagnosi.

## Fix routing React per Admin Hades 2026-07-01

Obiettivo: correggere il routing pubblico dopo la diagnosi sopra e verificare
che `/admin/hades` non venga piu' catturato dal frontend Laravel/Inertia.

Intervento remoto eseguito in `/home/ubuntu/dev-sandbox`:

- Rimosse dal compose override Traefik le label del router
  `devboard-laravel-web`, che catturavano `/login`, `/logout`,
  `PathPrefix(/admin/hades)` e `PathPrefix(/build)` verso Laravel.
- Ricreato `devboard-app-1` con:
  `docker compose -f docker-compose.devboard.yaml -f docker-compose.devboard.traefik.yaml up -d app`
  usando l'`APP_KEY` gia' presente nel container, senza stamparlo.
- Rimosso il backup temporaneo creato durante l'edit.

Verifiche fresche:

- `docker inspect devboard-app-1` non contiene piu' label
  `devboard-laravel-web`.
- Traefik ha ricaricato una config con:
  - `devboard-frontend`: `Host(home-sweet-home.cloud)`, priority `1`,
    servizio `devboard-frontend`, BasicAuth;
  - `devboard-web`: solo `PathPrefix(/api)`, `PathPrefix(/sanctum)` e
    `PathPrefix(/storage)`, servizio Laravel;
  - `devboard-hades`: solo `PathPrefix(/api/hades/v1)`, servizio Laravel;
  - `devboard-plugin`: solo `PathPrefix(/api/plugin/v1)`, servizio Laravel.
- HTTP pubblico senza BasicAuth:
  - `/admin/hades` -> `401 Unauthorized` da Traefik BasicAuth, coerente con il
    router frontend protetto;
  - `/login` -> `401 Unauthorized` da Traefik BasicAuth;
  - `/api/hades/v1/health` -> `200 OK` JSON Hades;
  - `/api/dashboard/me` -> `401 Unauthorized` da Traefik BasicAuth.
- Verifica diretta del frontend container:
  `docker exec devboard-frontend-1 wget -S -O - http://127.0.0.1/admin/hades`
  -> `HTTP/1.1 200 OK`, `Server: nginx/1.27.5`, HTML React con
  `DevBoard - Operational Dashboard`, `/static/js/main.e0cdc452.js` e
  `/static/css/main.4898dcfb.css`.
- Route Laravel esistono ancora internamente per `admin/hades` e `login`, ma
  non sono piu' esposte dal router pubblico Traefik per quelle page-route.
- Test remoto:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades tests/Feature/PluginAuthTest.php`
  -> 27 test passati, 239 assertions.

Stato remoto finale:

- `docker-compose.devboard.traefik.yaml` torna pulito rispetto a git.
- Rimane modificato solo `backend/bootstrap/app.php`, gia' presente prima di
  questo intervento e non toccato qui.

## Alias React Admin Hades 2026-07-01

Osservazione dopo il fix proxy: il routing Traefik mandava correttamente
`/admin/hades` al frontend React, ma `src/App.tsx` del frontend remoto aveva
solo la route `/admin`. Di conseguenza `/admin/hades` cadeva nel catch-all
React `*`, che naviga a `/`; da li' l'app puo' poi finire su `/projects` in
base al ruolo/sessione.

Intervento remoto eseguito nel repo frontend
`/home/ubuntu/emergent_devboard_frontend/frontend`:

- aggiunta una sola route:
  `/admin/hades` -> `<Section navKey="admin"><AdminPage /></Section>`;
- rebuild e redeploy del container:
  `docker compose -f docker-compose.devboard.yaml -f docker-compose.devboard.traefik.yaml up -d --build frontend`.

Verifiche fresche:

- Build Docker frontend completata con `craco build` e nuovo bundle
  `/static/js/main.41ad833e.js`.
- Il bundle deployato contiene la stringa `/admin/hades`.
- `docker exec devboard-frontend-1 wget -S -O - http://127.0.0.1/admin/hades`
  -> `HTTP/1.1 200 OK`, HTML React aggiornato con `main.41ad833e.js`.
- HTTP pubblico senza BasicAuth:
  - `/admin/hades` -> `401 Unauthorized` Traefik, cioe' la protezione esterna
    resta attiva;
  - `/login` -> `401 Unauthorized` Traefik;
  - `/api/hades/v1/health` -> `200 OK`.

Nota: non e' stato fatto un browser smoke autenticato perche' in questa
sessione non sono state usate credenziali BasicAuth. L'evidenza server-side
dimostra pero' che `/admin/hades` non e' piu' una route wildcard che cade su
`/projects`: il path esiste nel router React deployato e viene servito dal
frontend container.

## Fix cache index SPA Admin Hades 2026-07-01

Segnalazione successiva dal browser reale: DevTools mostrava ancora il body
HTML vecchio per `/admin/hades`, con script `/static/js/main.e0cdc452.js`, e
l'app finiva su `/projects`. La riga Network indicava document servito da disk
cache. Il deploy corretto invece aveva gia' generato `main.41ad833e.js` con la
route `/admin/hades`.

Intervento remoto nel frontend:

- aggiornato `nginx.conf` per rendere non cacheabile `index.html` e tutti i
  fallback SPA:
  - `Cache-Control: no-store, no-cache, must-revalidate`;
  - `Pragma: no-cache`;
  - `expires off`;
- mantenuto cache lungo solo su `/static/`, dove i file sono hashed;
- rebuild e redeploy di `devboard-frontend-1`.

Verifiche fresche dal container deployato:

- `wget -S -O - http://127.0.0.1/admin/hades` -> `HTTP/1.1 200 OK`.
- Header presenti: `Cache-Control: no-store, no-cache, must-revalidate` e
  `Pragma: no-cache`.
- Body aggiornato: script `/static/js/main.41ad833e.js`, non piu'
  `main.e0cdc452.js`.
- Bundle deployato: `/usr/share/nginx/html/static/js/main.41ad833e.js`
  contiene `/admin/hades`.

Nota operativa: i browser che hanno gia' messo in disk cache il vecchio
documento potrebbero richiedere un hard reload o "Disable cache" in DevTools
una volta. Dopo la prima fetch fresca, il nuovo `index.html` non dovrebbe piu'
essere riusato da cache.

## Porting React Admin Hades e installer 2026-07-01

Nuova segnalazione: `/admin` e `/admin/hades` mostravano la stessa pagina
React, e dalla UI non risultavano istruzioni di installazione Hades.

Diagnosi:

- Il backend Laravel aveva gia' le API Hades agent-facing M1-M5 sotto
  `/api/hades/v1/*`.
- Esistevano anche endpoint mutating dashboard per provisioning:
  `POST /api/dashboard/admin/hades/bootstrap-tokens`, revoke token, create job
  e review memory proposal.
- Mancava pero' un endpoint dashboard GET per lo snapshot Hades consumabile da
  React: la vecchia pagina Laravel/Inertia `GET /admin/hades` riceveva i dati
  server-side.
- La route React `/admin/hades` era stata solo aliasata ad `AdminPage`, quindi
  mostrava token/plugin/devices e non Hades.
- I comandi install generati dal backend puntavano a `/install.sh` e
  `/install.ps1`, ma il frontend pubblico serviva quei path come fallback SPA;
  inoltre Traefik li proteggeva con BasicAuth, rompendo `curl | bash`.

Interventi remoti:

- Backend `/home/ubuntu/dev-sandbox/backend`:
  - aggiunto `GET /api/dashboard/admin/hades` in
    `Dashboard\Api\DashboardHadesController@index`;
  - rimosso il web route legacy Laravel/Inertia `GET /admin/hades`;
  - aggiornato `tests/Feature/Hades/HadesM5MvpCompletionTest.php` per coprire
    lo snapshot dashboard Hades.
- Frontend `/home/ubuntu/emergent_devboard_frontend/frontend`:
  - aggiunta `src/pages/HadesAdminPage.tsx`;
  - `/admin/hades` ora punta a `HadesAdminPage`, non piu' ad `AdminPage`;
  - aggiunti tipi/metodi API Hades dashboard in `src/types/devboard.ts`,
    `src/api/devboardApi.ts`, `src/api/httpApi.ts` e mock fallback;
  - copiati gli installer Hades reali in `public/install.sh` e
    `public/install.ps1`.
- Traefik:
  - aggiunto router `devboard-install` per `Path(/install.sh) ||
    Path(/install.ps1)` verso `devboard-frontend`, senza BasicAuth.

Verifiche:

- Backend:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades/HadesM5MvpCompletionTest.php`
  -> 4 test passati, 59 assertions.
- Route list backend `--path=hades`:
  - non mostra piu' `GET admin/hades`;
  - mostra `GET api/dashboard/admin/hades` e le route Hades API.
- Frontend:
  - `npm run build` locale remoto passato;
  - Docker build/deploy passato con bundle `main.8f5b836d.js`;
  - bundle deployato contiene `Admin - Hades` e `/admin/hades`.
- Installer pubblici:
  - `https://home-sweet-home.cloud/install.sh` -> HTTP 200, script Bash Hades,
    non HTML React;
  - `https://home-sweet-home.cloud/install.ps1` -> HTTP 200, script PowerShell
    Hades, non HTML React.
- Hades API:
  - `https://home-sweet-home.cloud/api/hades/v1/health` -> HTTP 200 JSON.

## Menu React Hades e test installer pubblico 2026-07-01

Richiesta successiva: esporre nel menu React un accesso rapido a
`/admin/hades` e verificare il comando installer generato per il progetto
Carnovali.

Interventi remoti frontend:

- aggiunta voce visibile nel menu React:
  `Hades` -> `/admin/hades`, con ruolo `admin`;
- aggiornato `src/App.tsx` per usare `navKey="hades"` sulla route
  `/admin/hades`;
- rebuild e redeploy del container `devboard-frontend-1`;
- bundle deployato `main.7aa6d769.js` verificato con stringhe `Hades` e
  `/admin/hades`.

Problemi trovati durante il test dell'installer pubblico:

- gli installer pubblici puntavano al repository storico
  `gabriele/hades-agent`, non accessibile dal clone HTTPS;
- dopo la correzione al repository `titagram/Hephaistos`, il default `main`
  non conteneva ancora il subcommand `hades backend`;
- il default dichiarato `--backend-workspace PATH` come "current dir" era
  fragile: l'installer cambia directory internamente, quindi senza workspace
  esplicita il bootstrap poteva legare la cartella sbagliata.

Correzioni installer:

- `scripts/install.sh` e `scripts/install.ps1` ora usano:
  - SSH/HTTPS repo `titagram/Hephaistos`;
  - branch default `codex/hades-rebrand`, finche' l'implementazione Hades non
    viene mergiata su `main`;
  - la directory iniziale dello script come workspace backend di default,
    prima dei `cd` interni dell'installer;
  - in Bash, `backend bootstrap` viene eseguito con il Python del venv appena
    installato quando `--no-venv` non e' attivo, evitando launcher `hermes`
    vecchi su PATH.
- Aggiornamento naming comando:
  - il comando primario installato e documentato dagli installer e' `hades`;
  - `hermes` resta installato solo come alias di compatibilita';
  - su questo Mac e' stato creato subito `~/.local/bin/hades`.
- Copiati gli installer aggiornati in
  `/home/ubuntu/emergent_devboard_frontend/frontend/public/` e redeployato il
  frontend.

Verifiche:

- `curl https://home-sweet-home.cloud/install.sh` mostra:
  - `REPO_URL_HTTPS="https://github.com/titagram/Hephaistos.git"`;
  - `BRANCH="codex/hades-rebrand"`;
  - `INSTALLER_START_DIR="$PWD"`;
  - passaggio workspace con `BACKEND_WORKSPACE:-$INSTALLER_START_DIR`.
- `curl https://home-sweet-home.cloud/install.ps1` mostra repo/branch Hades e
  fallback workspace a `$InstallerStartDir`.
- Eseguito da `/Users/gabriele/Dev/sinervis/carnovali` il comando pubblico
  generato con token redatto nei log:
  - installazione completata;
  - checkout passato a `codex/hades-rebrand`;
  - `uv.lock` sul ramo Hades risulta da aggiornare: l'installer ha completato
    usando il fallback `uv pip install -e '.[all]'`;
  - `Backend setup complete`;
  - agent `ha_c8df14317e732d80`;
  - workspace corretta `/Users/gabriele/Dev/sinervis/carnovali`;
  - binding corretto `01KWEPJGFG5K0W0J7NJEQ8YF0N`;
  - sync finale: 0 job, 2 snapshot memoria, 0 proposal/artifact/inbox.
- Stato locale finale:
  - `hades backend status` -> 1 binding;
  - unico binding attivo verso `~/Dev/sinervis/carnovali`.

Pulizia:

- Un run intermedio, prima della correzione workspace, aveva creato localmente
  un progetto `carnovali-2` verso `~/.hermes/hermes-agent/ui-tui`.
- Il progetto e' stato archiviato e il binding/cache locale spurio sono stati
  rimossi; resta attivo solo `carnovali`.

## Fix memory proposal summary lunga 2026-07-01

Problema osservato durante il test locale nel workspace Carnovali:

- `hades backend sync` inviava una memory proposal locale con summary lunga;
- il backend rispondeva HTTP 500;
- Laravel log riportava PostgreSQL `SQLSTATE[22001] value too long for type
  character varying(255)` su `project_memory_entries.summary`;
- il contratto API validava gia' `summary` fino a 4000 caratteri e
  `hades_memory_proposals.summary` era gia' `text`, quindi il collo di
  bottiglia era solo la tabella condivisa `project_memory_entries`.

Interventi backend remoto:

- `project_memory_entries.summary` cambiato da `string` a `text` nella
  migration sorgente;
- aggiunta migration runtime
  `2026_07_01_000005_expand_project_memory_entry_summary_column.php`;
- applicata migration su PostgreSQL con `php artisan migrate --force`;
- verificato via DB che l'ultima entry `hades_agent/proposal` per Carnovali
  contiene summary lunga 1005 caratteri.

Interventi client Hades locale:

- `hades_backend_sync.run_backend_sync()` ora cancella `last_sync_error` quando
  una sync completa termina senza errori;
- aggiunto helper `clear_sync_state()` in `hades_backend_db`;
- aggiornato anche il managed checkout locale `~/.hermes/hermes-agent` per
  rendere subito corretto il comando `hades` installato su questo Mac.

Verifiche:

- Test rosso schema: `project_memory_entries.summary` era `varchar`;
- test verde remoto: `22 passed / 237 assertions` su
  `tests/Feature/Hades` + `ProjectWorkspaceMemoryQueueSchemaTest.php`;
- test verde locale: `13 passed` su backend command, doctor e TUI RPC;
- `hades backend sync` da `/Users/gabriele/Dev/sinervis/carnovali`:
  `memory 1, proposals 1`, senza errori;
- sync successiva: `proposal_errors: 0`, `last_error: null`;
- stato locale: proposal accettata `accepted: 1`; resta una vecchia proposal
  `refused: 1` da revisionare separatamente, non collegata al 500.

## Richieste backend su wiki, memory, OpenCode e import memoria 2026-07-01

Richiesta girata al backend agent remoto prima di proseguire i test:

- accesso wiki poco chiaro/non discoverable;
- popolamento/aggiornamento wiki non esposto al frontend React;
- inserimento manuale memory da dashboard con categoria/source
  "inserito dall'utente";
- configurazione API key OpenCode/OpenCode Go non funzionante;
- import/trasferimento di memory esistenti da altra cartella/workspace, con
  cooperazione tra Hades locale/frontend e backend.

Output backend:

- creato report remoto:
  `/home/ubuntu/dev-sandbox/ai-sandbox/docs/devboard-backend-blockers-report-2026-07-01.md`;
- aggiornato logbook remoto:
  `/home/ubuntu/dev-sandbox/ai-sandbox/logbooks/LOGBOOK_PROJECT.md`;
- nessun codice applicativo o dato runtime modificato.

Punti principali dal report:

- wiki: API read gia' presenti (`GET /api/dashboard/wiki`,
  `GET /api/dashboard/projects/{project}/wiki`,
  `GET /api/dashboard/wiki/pages/{page}`), ma `wiki` e' nascosta nella nav
  React; proposta backend: aggiungere `links.wiki` e `links.wiki_api` al
  payload progetto e verificare routing SPA;
- wiki refresh: oggi scrittura wiki esiste via plugin/revision service, ma
  manca un endpoint dashboard; proposta:
  `POST /api/dashboard/projects/{project}/wiki/refresh-requests`, che accoda un
  job Hades `populate_project_wiki`, con result applicato da
  `WikiRevisionService`;
- memory manuale: `POST /api/dashboard/projects/{project}/memory` esiste ma
  salva `source = dashboard_user`; proposta: per il form manuale salvare
  `source = user_inserted` e mantenerla nello snapshot Hades;
- OpenCode: backend puo' salvare provider generici cifrati, ma manca preset
  `opencode_go`, creazione profilo modello e validate endpoint; configurare
  OpenCode localmente non configura automaticamente Laravel;
- import memory: esistono workspace binding e proposals, ma manca import batch
  con dedupe/provenance; proposta:
  `GET /api/dashboard/projects/{project}/workspace-bindings`,
  `POST /api/dashboard/projects/{project}/memory/imports` e
  `POST /api/hades/v1/memory/import-bundles`.

Ordine consigliato dal backend:

1. Rendere discoverable la wiki e aggiungere link payload.
2. Salvare memory manuale con `source = user_inserted`.
3. Esporre wiki refresh request come job Hades.
4. Applicare result wiki Hades tramite `WikiRevisionService`.
5. Aggiungere provider/profile/validate per OpenCode.
6. Implementare import batch e bundle Hades per trasferimento memory.

## Fix Socrates queue e visibilita' wiki upload 2026-07-01

Root cause verificata sul backend remoto:

- `POST /api/dashboard/projects/{project}/agent-work` accettava
  `assigned_agent_key = socrates`, ma nessun consumer server-side processava
  quei work item; il plugin locale claimava solo `local_agent`.
- Il worker Docker era fermo da ore per un vecchio errore sulla tabella
  `cache`, ma il bug Socrates non dipendeva dal worker: mancava proprio il
  processore per `socrates`.
- Il job wiki locale era `completed`, ma `WikiRefreshResultService` leggeva
  solo `result.pages`; l'agent locale aveva prodotto `result.wiki_revisions`,
  quindi `applied.pages_written` restava `0`.

Implementazione applicata:

- aggiunto `App\Services\ServerAgentWorkService`;
- `DashboardAgentWorkController` ora processa immediatamente i work item
  server-side per `socrates` usando il profilo `socrate_supervisor` e provider
  OpenAI-compatible configurato;
- la risposta Socrates viene salvata come `project_memory_entries` con
  `source = server_agent`, `kind = agent_note`, `agent_key = socrates`, e
  collegata a `agent_work_items.result_memory_entry_id`;
- errori provider diventano stato `failed` visibile sul work item, non una
  coda muta;
- `WikiRefreshResultService` accetta `pages`, `wiki_revisions` e `revisions`,
  supporta evidence legacy (`evidence`, `citations`, `code_evidence`) e
  restituisce anche `title` nelle pagine applicate;
- frontend React `WikiPage` mostra nel pannello refresh il numero di pagine
  applicate e link/chip alle pagine scritte.

Dati runtime riparati:

- riapplicato job wiki `01KWF3RFFDA7NZ1E5GMV42AQH4`;
- scritte 3 pagine Carnovali: `overview`, `architecture`,
  `development-operations`;
- processato work item Socrates `01KWF3BYJNG2EPVK5CT63TBJ4M`;
- generata memory entry Socrates `01KWF52Q8F0TH4XA94EQPHCSB7`;
- worker Docker riavviato e verificato in esecuzione con
  `php artisan queue:work`.

Verifiche:

- `php -l` su service/controller modificati;
- `composer dump-autoload`;
- `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Dashboard/AgentWorkDashboardApiTest.php tests/Feature/Dashboard/WikiRefreshDashboardApiTest.php --display-warnings`
  passato: `20 passed / 161 assertions`;
- `vendor/bin/pint --test` passato sui file toccati;
- `npx tsc --noEmit` passato;
- `npm run build` passato;
- rebuild Docker frontend completato con bundle `main.27ff9256.js`;
- `php artisan queue:failed` non mostra failed jobs.

## Nota futura: domini memoria separati 2026-07-01

Richiesta utente da tenere come requisito per la prossima slice backend/Hades:
la memoria di progetto non deve restare un unico stream indistinto. Deve
esistere una separazione interrogabile per dominio:

- `logbook`: cronologia/diario operativo e decisioni temporali del progetto;
- `wiki`: conoscenza documentale strutturata, evidence-backed e navigabile;
- `agent_notes`: note generiche e handoff prodotti dagli agent locali o dagli
  agent server-side.

Ogni dominio deve essere interrogabile separatamente sia dall'agente locale
Hades sia dagli agent presenti nel backend. Il contratto dovrebbe quindi
prevedere filtri/scope espliciti negli endpoint snapshot/search/recall e nella
UI dashboard, evitando che wiki, logbook e note operative vengano mescolati
senza intenzione.

Nota collegata sul graph: se il graph backend non e' accessibile, gli agent
frontend/backend perdono un canale utile per orientarsi su relazioni fra
progetti, repository, run, wiki, artifact, task e simboli. Conviene ripristinare
un accesso graph read-only e interrogabile esplicitamente dagli agent, ma come
scope separato dalla memoria: utile per discovery/relazioni, non come recall
generico sempre iniettato.

Nota UI frontend backend: nella pagina React `/admin`, la sezione `Model
profiles` ha un'impaginazione poco leggibile. Da riprendere come miglioramento
di layout dedicato: ridurre densita' visiva disordinata, rendere confrontabili
profile/provider/model/agent associati e tenere azioni/configurazione in
controlli prevedibili.

Nota admin AI: in `/admin` deve essere possibile gestire in modo esplicito la
rimozione di `model profiles` e la cancellazione delle `model provider
credentials`, con azioni amministrative visibili e conferme adeguate.

Nota agent work/chat: in
`/projects/01KWEP8MV76QKQJ4CWXAJMTZ4T/agent-work` le righe delle risposte /
work item agent dovrebbero essere cliccabili e aprire un dettaglio. Dal
dettaglio dovrebbe essere possibile proseguire la conversazione o aggiungere
follow-up collegati allo stesso contesto.

Nota chat persistente agent-scoped: serve un'area chat dedicata dove scegliere
con quale agent parlare, mantenere thread persistenti e riaprire chat passate,
in stile mini ChatGPT. Questa richiede un contratto backend dedicato per
conversation/thread/messages, selezione agent, stato e memoria del thread; non
va modellata solo come singolo `agent_work` fire-and-forget.

## Dropdown progetto sidebar React 2026-07-01

Stato: implementato e deployato nel frontend remoto.

- `AppShell` ora carica i progetti attivi con `api.getProjects("active")`.
- La sidebar desktop e il menu mobile mostrano un dropdown `Project scope`.
- Selezionando un progetto si naviga allo scope project-specific e, dove
  possibile, si conserva la sezione corrente (`/kanban`, `/ask`, `/memory`,
  `/wiki`, `/engineering`).
- Selezionando `Global workspace` si torna a `/projects`.
- La topbar mostra il nome progetto quando disponibile, altrimenti l'id
  decodificato.

Verifiche:

- `npx tsc --noEmit` passato.
- `npm run build` passato.
- container `devboard-frontend-1` ricreato con bundle `main.c6aecc29.js`.
- asset container verificato: `index.html` punta a
  `/static/js/main.c6aecc29.js`.
- browser in-app Codex non ha potuto aprire `https://home-sweet-home.cloud/projects`
  per `net::ERR_BLOCKED_BY_CLIENT`; verifica renderizzata pubblica da fare con
  Chrome autenticato dell'utente o dopo refresh manuale della tab.

## Admin AI model profile e provider credentials 2026-07-01

Stato: implementato e deployato.

- Backend: aggiunto `DELETE /api/dashboard/admin/ai-model-profiles/{profile}`.
- Backend: la cancellazione di un model profile fallisce con `409` se il
  profilo e' ancora assegnato a uno o piu' controlled agents.
- Backend: aggiunti audit log `ai_model_profile.deleted` e test per clear key,
  delete profile, blocco delete assegnato e divieto non-admin.
- Frontend `/admin`: il provider mostra lo stato della key e un'azione esplicita
  `Clear key` con conferma.
- Frontend `/admin`: ogni model profile mostra `Delete` con conferma; il
  pulsante e' disabilitato quando il profilo e' assegnato a un agent.

Verifiche:

- `php -l` sui controller/service toccati.
- `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Dashboard/AiAgentRegistryDashboardTest.php --display-warnings`
  passato: `11 passed / 96 assertions`.
- `./vendor/bin/pint --test app/Assistants/AiAgentRegistry.php app/Http/Controllers/Dashboard/Api/DashboardAiAgentController.php routes/web.php tests/Feature/Dashboard/AiAgentRegistryDashboardTest.php`
  passato.
- `php artisan route:list --path=ai-model-profiles` mostra `POST`, `PUT` e
  `DELETE`.
- `npx tsc --noEmit` passato.
- `npm run build` passato.
- container `devboard-frontend-1` ricreato con bundle `main.c2e1f226.js`.
- asset container verificato: `index.html` punta a
  `/static/js/main.c2e1f226.js` e il bundle contiene `Clear key`,
  `Delete profile` e `Project scope`.

## Memory/wiki/agent-work slice 2026-07-01

Stato: implementato e deployato nel backend remoto e nel frontend React
standalone.

- Skill locale creata:
  `skills/autonomous-ai-agents/hades-wiki-push/SKILL.md`.
- Backend agent coordinato su logbook/wiki/agent_notes, graph read-only,
  agent tools, agent-work detail e ripristino agenti.
- Backend: aggiunto `DELETE
  /api/dashboard/projects/{project}/memory/{memory}` con audit
  `project_memory.deleted`.
- Backend: aggiunti `POST /api/dashboard/projects/{project}/wiki/pages` e
  `PATCH /api/dashboard/wiki/pages/{page}` tramite `WikiRevisionService`.
- Backend: audit wiki corretto per segnare `actor_type=user` quando scrive un
  utente dashboard.
- Frontend React: Memory ora supporta domini `logbook`, `wiki`, `agent_notes`,
  ricerca e delete delle entry non-wiki.
- Frontend React: Wiki ora permette creazione pagina/sezione da Markdown e
  modifica Markdown/stato da dettaglio pagina.
- Frontend React: Agent Work ha righe cliccabili con dettaglio eventi,
  memoria risultato e messaggi chat persistiti; il follow-up rimanda ad Ask
  per nuova richiesta agent-scoped.

Verifiche:

- Skill validator: `Skill is valid!`.
- Backend targeted:
  `php artisan test tests/Feature/Dashboard/ProjectMemoryDashboardApiTest.php tests/Feature/Dashboard/WikiManualDashboardApiTest.php --display-warnings`
  passato: `19 passed / 96 assertions`.
- Backend full suite:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test --display-warnings`
  passato: `290 passed / 2493 assertions`.
- Backend Pint:
  `./vendor/bin/pint --test ...` passato sui file toccati.
- Frontend: `npm run build` passato.
- Frontend: `CI=true npm test -- --watchAll=false` passato:
  `4 suites / 31 tests`.
- OpenCode Go runtime validation: provider `opencode_go` trovato con key e
  base URL configurati ma `enabled=false`; abilitato con audit di sistema e
  validato live. Esito: `ready_for_runtime`, `api_reachable=true`,
  `checked_model=glm-5.2`, nessun errore redatto.
- `git diff --check` pulito su backend e frontend.
- Container `devboard-frontend-1` ricreato con bundle `main.2a8a2e0f.js`.
- Smoke interno container: `/projects` serve `main.2a8a2e0f.js` e
  `/admin/hades` serve `DevBoard - Operational Dashboard`.
- Smoke pubblico con `curl` senza sessione restituisce `401`, coerente con
  accesso autenticato; verifica browser da fare con sessione Chrome utente.

## Agent chat backend contract 2026-07-01

Stato: implementato e migrato nel backend remoto.

- Backend: aggiunte tabelle `agent_chat_threads` e `agent_chat_messages` per
  una chat persistente agent-scoped, separata da `agent_work_items`.
- Backend: esposti endpoint dashboard dedicati:
  - `GET /api/dashboard/projects/{project}/agent-chats`
  - `POST /api/dashboard/projects/{project}/agent-chats`
  - `GET /api/dashboard/projects/{project}/agent-chats/{thread}`
  - `POST /api/dashboard/projects/{project}/agent-chats/{thread}/messages`
- Il thread conserva `agent_key`, scope project/repository/task, stato,
  ultimo `agent_work_item` e ultimo `assistant_run`.
- Ogni turno utente crea un messaggio persistente e un `agent_work_item`
  collegato con payload `devboard.agent_chat_turn.v1`.
- Per `socrates`, `platon` e `aristoteles` il controller riusa il runner
  `ServerAgentWorkService`, poi copia la risposta assistant nel thread.
- Per `local_agent` il turno resta in `pending_local_agent` con work item
  queued, pronto per completamento lato agente locale.
- Backend plugin: `POST /api/plugin/v1/agent-work-items/{workItem}/complete`
  accetta anche `chat_message` opzionale; quando il work item arriva da
  `devboard.agent_chat_turn.v1`, il completamento appende un messaggio
  `assistant` nel thread.
- Backend plugin: `POST /api/plugin/v1/agent-work-items/{workItem}/fail`
  appende un messaggio `system` nel thread collegato e porta il thread a
  `failed`.
- Permessi: PM/Developer/Admin possono creare e proseguire chat; Sysadmin puo'
  leggere ma non scrivere; progetti archived/deleted bloccano le scritture.

Verifiche:

- `php artisan route:list --path=agent-chats` mostra 4 route dedicate.
- `./vendor/bin/pint --test app/Http/Controllers/Dashboard/Api/DashboardAgentChatController.php app/Dashboard/DashboardApiReader.php routes/web.php database/migrations/2026_07_01_000009_create_agent_chat_thread_tables.php tests/Feature/Dashboard/AgentChatDashboardApiTest.php`
  passato.
- Targeted:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Dashboard/AgentChatDashboardApiTest.php tests/Feature/Dashboard/AgentWorkDashboardApiTest.php tests/Feature/Plugin/PluginSharedMemoryAndWorkQueueTest.php --display-warnings`
  passato: `42 passed / 291 assertions`.
- Full suite:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test --display-warnings`
  passato: `299 passed / 2562 assertions`.
- Runtime migration: `php artisan migrate --force` passato, inclusa
  `2026_07_01_000009_create_agent_chat_thread_tables`.
- `git diff --check` pulito sul backend.

## Agent chat React surface 2026-07-01

Stato: implementato e deployato nel frontend React remoto.

- Frontend React: aggiunta pagina `/agent-chat` e route project-scoped
  `/projects/{project}/agent-chat`.
- Frontend React: aggiunta voce primaria `Chat` nel menu, project-scoped come
  Work/Ask/Memory/Wiki/Engineering; Sysadmin vede la chat in read-only.
- Frontend React: aggiunti tipi e metodi adapter per:
  - `GET /api/dashboard/projects/{project}/agent-chats`
  - `POST /api/dashboard/projects/{project}/agent-chats`
  - `GET /api/dashboard/projects/{project}/agent-chats/{thread}`
  - `POST /api/dashboard/projects/{project}/agent-chats/{thread}/messages`
- Frontend React: pagina Chat con lista thread persistenti, selezione agent per
  nuova chat, composer, stati `active`, `waiting_for_agent`,
  `pending_local_agent`, `failed`, polling leggero degli stati pending e
  dettaglio messaggi.
- Frontend React: `Ask` ora avvia una chat persistente invece di creare solo un
  work item isolato.
- Frontend React: dettaglio `Agent Work` rimanda a `Chat` precompilando agente e
  prompt del work item.
- Mock adapter: aggiunta implementazione locale di thread agent-scoped; per
  `local_agent` crea work item pending, per agent server genera risposta e
  memoria mock.

Verifiche:

- Frontend targeted:
  `npm test -- --watchAll=false src/lib/nav.test.ts src/api/httpApi.test.ts src/api/mockApi.test.ts`
  passato: `3 suites / 28 tests`.
- Frontend build locale:
  `npm run build` passato con bundle `main.a5b6585b.js`.
- Frontend container:
  `DEVBOARD_APP_KEY=... docker compose -f docker-compose.devboard.yaml -f docker-compose.devboard.traefik.yaml up -d --build frontend`
  passato con bundle deployato `main.be94e134.js`.
- Smoke interno container: `/agent-chat`,
  `/projects/01KWEP8MV76QKQJ4CWXAJMTZ4T/agent-chat` e `/admin/hades` servono
  `main.be94e134.js`.
- Smoke pubblico senza credenziali: `https://home-sweet-home.cloud/agent-chat`
  risponde `401`, coerente con basic-auth Traefik.
- Backend route smoke: `php artisan route:list --path=agent-chats` mostra le 4
  route dashboard dedicate.
- Backend targeted:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Dashboard/AgentChatDashboardApiTest.php tests/Feature/Plugin/PluginSharedMemoryAndWorkQueueTest.php --display-warnings`
  passato: `24 passed / 156 assertions`.
- Backend Pint targeted passato sui file agent-chat/agent-work/server-agent.
- Locale Hades:
  `.venv/bin/python -m pytest tests/hermes_cli/test_hades_backend_cmd.py tests/hermes_cli/test_hades_backend_sync_runner.py tests/hermes_cli/test_hades_backend_client.py`
  passato: `15 passed`.
- OpenCode Go runtime validation:
  `app(App\Assistants\AiAgentRegistry::class)->validateProvider('opencode_go')`
  passato con `status=ready_for_runtime`, `api_reachable=true`,
  `checked_model=glm-5.2`.

Aggiornamento locale 2026-07-01:

- Il backend plugin accetta gia' `chat_message` su
  `POST /api/plugin/v1/agent-work-items/{workItem}/complete` e lo persiste nel
  thread chat collegato.
- Nel repo locale Hephaistos e' stato aggiunto un client dedicato
  `/api/plugin/v1/agent-work-items` separato dal sync Hades `/api/hades/v1`.
- `hades backend worker --once --local-workspace-id <id>` lista gli item
  `queued`, claim con `local_workspace_id`, mantiene heartbeat periodico mentre
  l'agente locale lavora e completa passando il testo finale anche in
  `chat_message`.
- Se il backend richiede un plugin token distinto dal token agente Hades,
  configurare `backend.plugin_token_env_key` o `HADES_BACKEND_PLUGIN_TOKEN`.

## Production readiness execution 2026-07-02

Stato: verifica remota non distruttiva completata durante implementazione P0
locale.

- Backend remoto: `php artisan route:list --path=hades` mostra 22 route Hades,
  incluse le route dashboard admin per bootstrap token/job/proposal review e le
  route operative `/api/hades/v1` coperte dal fixture OpenAPI locale.
- Backend remoto: suite `tests/Feature/Hades` passata in container con SQLite
  in-memory: `21 passed / 229 assertions`.
- Repo locale: P0-5 implementato centralizzando lo status backend condiviso da
  CLI e TUI RPC; il payload include `configured`, `degraded`, `actions`,
  conteggi job/proposte/inbox e stato sync.
- Repo locale: P0-4 rafforzato per artifact Hades; `sync_git_tree` e AST
  inviano metadati/simboli, non sorgenti raw, omettendo env/segreti, ignored,
  generated, binari/archivi e file oltre budget.
- Repo locale: P0-3 avviato con comandi CLI di review:
  `hades backend jobs`, `approve-job`, `refuse-job`, `proposals`,
  `ack-proposal`.
- Repo locale: P0-6 avviato con sync piggyback asincrono nel loop agente. Lo
  stato di backoff e fallimento vive in `hades_backend.db` `sync_state`, non
  blocca la chat, e viene ripulito da un `hades backend sync` manuale riuscito.
- Repo locale: P1-1 avviato con lifecycle locale esplicito: cleanup dry-run per
  job terminali, memory proposal accettate/acknowledged, inbox stale e cache
  shared-memory orfana via `hades doctor cleanup`. Refused/conflicted proposal
  restano visibili finche' non vengono acknowledge.
- Repo locale: P1-4 avviato con smoke MVP no-network
  `tests/hermes_cli/test_hades_backend_mvp_smoke.py`, che compone agent/link,
  sync memoria/proposal, job/artifact, inbox, doctor report e status TUI.
- Repo locale: P1-2 avviato con logging strutturato e redatto su logger
  `hermes_cli.hades_backend` per sync, artifact upload, worker plugin e doctor
  report; docs Hades ora includono eventi e runbook operator.
- Repo locale: P1-6 avviato con `docs/RELEASE_GATES.md`: matrice dei gate per
  backend MVP, PyPI, Docker, website, desktop e update flow. Corretto anche il
  release script locale per includere `apps/desktop/package.json` nel commit
  automatico di bump versione.
- Repo locale: P1-3 avviato con `docs/security/dashboard-auth-matrix.md` e
  test `tests/hermes_cli/test_dashboard_auth_inventory.py`: l'allowlist pubblica
  dashboard/API deve restare read-only salvo endpoint self-authenticated come
  `/api/cron/fire`.
- Repo locale: P1-5 avviato rafforzando `hades_backend_jobs`: file/directory
  symlink sono omessi con reason `symlink`, directory gitignored vengono
  potate prima del walk; in quella slice l'AST era ancora scope Python-only
  bounded senza sorgente raw.

Aggiornamento verifica contratto 2026-07-02:

- Divergenza trovata e chiusa: il backend esponeva anche
  `POST /api/hades/v1/memory/import-bundles`, mancante dal fixture locale.
  Il fixture `docs/hades/openapi-hades-v1.json` e `HadesBackendClient` ora lo
  coprono come `import_memory_bundle`.
- Confronto fresco via `php artisan route:list --path=hades --json`: le 17
  route operative `/api/hades/v1` remote coincidono esattamente con il fixture
  OpenAPI locale (`remote_only=[]`, `fixture_only=[]`).
- Suite remota fresca:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades`
  passata: `21 passed / 229 assertions`.
- Repo locale: P1-7 avviato con `docs/hades/support-runbook.md`: safe support
  bundle, matrice symptom/command/evidence/recovery/escalation per bootstrap,
  token, workspace link, job/proposal, artifact, Docker, Windows PATH e
  desktop/backend mismatch.

Aggiornamento verifica backend Laravel 2026-07-02:

- Backend remoto: branch `fase-2` pulito su `3e7b3a7` (`Stabilize DevBoard
  deployment and project scope workflows`), senza diff non committati.
- Commit ispezionato: aggiunge profilo Docker prod DevBoard, route dashboard
  project-scoped per memory/wiki/graph, update della memoria progetto e nuova
  semantica di delete model profile assegnato con clear degli agent collegati.
- Route smoke: `php artisan route:list --path=hades` mostra 22 route; `php
  artisan route:list --path=api/dashboard/projects` include `PATCH
  /projects/{project}/memory/{memory}`, project wiki page e project graph.
- Suite remota Hades + plugin auth:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php
  artisan test tests/Feature/Hades tests/Feature/PluginAuthTest.php` passata:
  `28 passed / 254 assertions`.
- Suite remota dashboard toccata dal commit:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php
  artisan test tests/Feature/Dashboard/AiAgentRegistryDashboardTest.php
  tests/Feature/Dashboard/DashboardApiContractTest.php
  tests/Feature/Dashboard/ProjectMemoryDashboardApiTest.php` passata:
  `30 passed / 281 assertions`.
- Smoke pubblico Hades: `https://home-sweet-home.cloud/api/hades/v1/health`
  risponde `HTTP/2 200` con `status=ok`.
- Compose prod: `docker compose -f docker-compose.devboard.prod.yaml config
  --quiet` passato con env dummy; file prod Docker/Nginx/PHP presenti.
- Nota di review: `PATCH /api/dashboard/projects/{project}/memory/{memory}` e'
  implementata come sostituzione completa dei riferimenti opzionali; se il
  frontend invia patch parziali senza `repository_id`, `task_id` o `run_id`, i
  riferimenti esistenti vengono azzerati.

## Esecuzione bug evidence Hades - 2026-07-07

Stato: completata la prima slice P0 del piano "Bug Root Cause Awareness":
substrato tipizzato per bug report/evidence e tool agent per consultarlo.

Backend remoto:

- Branch remoto: `fase-2`, senza push.
- Nuova migration:
  `database/migrations/2026_07_07_000001_create_hades_bug_evidence_tables.php`.
- Nuove tabelle:
  `hades_bug_reports` e `hades_bug_evidence_items`.
- Nuovi controller:
  `app/Http/Controllers/Hades/BugReportController.php` e
  `app/Http/Controllers/Hades/BugEvidenceController.php`.
- Nuove route Hades v1:
  - `POST /api/hades/v1/bug-reports`
  - `GET /api/hades/v1/bug-reports/{bugReport}`
  - `POST /api/hades/v1/bug-evidence`
  - `GET /api/hades/v1/bug-evidence/search`
- Tutte le operazioni sono scoped al token agent, `project_id` e
  `workspace_binding_id` linked.
- Bug evidence supporta kind tipizzati: stack trace, log excerpt, failing test,
  HTTP trace, browser console, deploy version, config snapshot, user steps,
  screenshot reference e `other`.
- Ogni evidence item conserva payload bounded, `sha256`, `redactions`,
  `retention_class`, source e timestamp. La search restituisce freshness con
  `workspace_head_commit` e `index_status=live_query`.
- `CapabilitiesController` e `HealthController` espongono i nuovi endpoint.

Integrazione Hades locale:

- `HadesBackendClient` espone `create_bug_report()`, `get_bug_report()`,
  `create_bug_evidence()` e `bug_evidence_search()`.
- Il provider memoria `hades_backend` espone il nuovo tool service-gated
  `hades_backend_bug_evidence_search`.
- Il tool usa timeout live di 2 secondi e non ha cache fallback, per evitare che
  bug evidence stale venga trattata come autorevole.
- I payload evidence vengono restituiti bounded; se superano budget, il tool
  produce excerpt marcato `truncated`.
- La fixture OpenAPI locale documenta i quattro endpoint nuovi.

Verifiche eseguite:

- Locale:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_hades_backend_memory_provider.py tests/hermes_cli/test_hades_backend_client.py`
  passato: `41 passed`.
- Remoto mirato:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades/HadesM3SharedMemoryTest.php`
  passato: `11 passed / 129 assertions`.

Nota operativa:

- Il primo run remoto ha fallito per una migration con literal PHP senza
  virgolette, causata dal metodo di patch iniziale via shell. Corretto
  sostituendo la migration con diff generato dal file remoto reale; il test
  Hades M3 ora passa.

## Esecuzione freshness/coverage Hades - 2026-07-07

Stato: completata la slice P0-3 del piano "Bug Root Cause Awareness": lo stato
di project awareness e' ora una superficie API/tool consultabile prima di claim
precisi senza accesso al source code.

Backend remoto:

- Branch remoto: `fase-2`, senza push.
- Nuovo controller:
  `app/Http/Controllers/Hades/ProjectAwarenessStatusController.php`.
- Nuovo service:
  `app/Services/Hades/HadesProjectAwareness.php`.
- Nuova route Hades v1:
  `GET /api/hades/v1/project-awareness/status`.
- Lo status calcola freshness confrontando `hades_workspace_bindings.head_commit`
  con il commit indicizzato negli artifact quando presente.
- La response espone `freshness.status`, `stale_reason`, coverage per memoria,
  artifact, bug evidence, source slices e code graph, `overall_status`,
  `diagnosable_without_source` e azioni correttive.
- `MemorySearchController` e `BugEvidenceController` riusano lo stesso calcolo
  di freshness, mantenendo `index_status=live_query`.
- `CapabilitiesController` e `HealthController` espongono la nuova route.

Integrazione Hades locale:

- `HadesBackendClient` espone `project_awareness_status()`.
- Il provider memoria `hades_backend` espone il nuovo tool service-gated
  `hades_backend_project_awareness_status`.
- Il tool usa timeout live di 2 secondi e non ha cache fallback, come bug
  evidence.
- `hades backend sync` arricchisce gli artifact caricati con
  `head_commit`, `indexed_head_commit` e `workspace_head_commit` dal binding
  locale quando disponibile.
- La fixture OpenAPI locale documenta il nuovo endpoint.

Verifiche eseguite:

- Locale:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_hades_backend_memory_provider.py tests/hermes_cli/test_hades_backend_client.py tests/hermes_cli/test_hades_backend_sync_runner.py`
  passato: `58 passed`.
- Locale:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m ruff check hermes_cli/hades_backend_client.py hermes_cli/hades_backend_sync.py plugins/memory/hades_backend/__init__.py tests/agent/test_hades_backend_memory_provider.py tests/hermes_cli/test_hades_backend_client.py tests/hermes_cli/test_hades_backend_sync_runner.py`
  passato.
- Locale:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/docs_audit.py`
  passato: `docs audit passed: 18 required docs checked`.
- Remoto mirato:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades/HadesM3SharedMemoryTest.php`
  passato: `14 passed / 169 assertions`.
- Remoto completo Hades + plugin auth:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades tests/Feature/PluginAuthTest.php`
  passato: `38 passed / 382 assertions`.
- Remoto Pint:
  `vendor/bin/pint --test app/Services/Hades/HadesProjectAwareness.php app/Http/Controllers/Hades/ProjectAwarenessStatusController.php app/Http/Controllers/Hades/MemorySearchController.php app/Http/Controllers/Hades/BugEvidenceController.php app/Http/Controllers/Hades/CapabilitiesController.php app/Http/Controllers/Hades/HealthController.php routes/api.php tests/Feature/Hades/HadesM3SharedMemoryTest.php`
  passato.
- Route list remota: `php artisan route:list --path=hades` mostra 28 route e
  include `GET|HEAD api/hades/v1/project-awareness/status`.
- Smoke pubblico: `https://home-sweet-home.cloud/api/hades/v1/health` risponde
  `HTTP/2 200` e include `project_awareness_status`.

## Esecuzione PHP graph artifact Hades - 2026-07-07

Stato: completata la slice P0-4 base del piano "Bug Root Cause Awareness".
`populate_backend_ast` non e' piu' solo Python per workspace PHP/Laravel:
produce un grafo metadata/symbol bounded e caricabile sul backend condiviso.

Backend remoto:

- Branch remoto: `fase-2`, senza push.
- Commit remoto: `fb2e4a2 feat: support Hades PHP graph artifacts`.
- `ArtifactController` accetta anche `hades.php_graph.v1`.
- `MemorySearchController` produce summary searchable per grafi PHP: route,
  classi/metodi, ruoli e conteggio edge per kind.
- `HadesProjectAwareness` trattava gia' `hades.php_graph.v1` come code graph
  current; i test ora verificano che la coverage `code_graph` diventi
  `current` quando l'artifact e' allineato al commit del workspace.

Integrazione Hades locale:

- `populate_backend_ast` produce `hades.php_graph.v1` quando trova file `.php`;
  per workspace Python mantiene `hades.symbols.v1`.
- Il grafo include route Laravel, class/interface/trait/enum, metodi,
  `extends`, relazioni Eloquent, static call e instantiation edge.
- Il grafo non include raw source content; conserva path/line/simboli/edge e
  omission reasons.
- `hades backend sync` carica anche `hades.php_graph.v1`.
- Documentazione operativa e fixture OpenAPI locale aggiornate.

Verifiche eseguite:

- Locale mirato:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/hermes_cli/test_hades_backend_sync_runner.py`
  passato: `26 passed`.
- Remoto syntax:
  `php -l app/Http/Controllers/Hades/MemorySearchController.php && php -l app/Http/Controllers/Hades/ArtifactController.php`
  passato nel container app.
- Remoto Pint:
  `vendor/bin/pint --test app/Http/Controllers/Hades/ArtifactController.php app/Http/Controllers/Hades/MemorySearchController.php tests/Feature/Hades/HadesM3SharedMemoryTest.php tests/Feature/Hades/HadesM5MvpCompletionTest.php`
  passato.
- Remoto completo Hades + plugin auth:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades tests/Feature/PluginAuthTest.php`
  passato: `40 passed / 407 assertions`.

## Esecuzione source slices Hades - 2026-07-07

Stato: completata la slice P0-5 base del piano "Bug Root Cause Awareness".
Hades puo' ora salvare e recuperare source slices bounded/redatte dal backend,
senza trasformarle in memoria ordinaria o auto-prefetch.

Backend remoto:

- Branch remoto: `fase-2`, senza push.
- Nuova migration: `hades_source_slices`.
- Nuovo controller: `SourceSliceController` con:
  - `POST /api/hades/v1/source-slices` per store bounded/redacted;
  - `GET /api/hades/v1/source-slices` per fetch/search filtrato per id,
    path, symbol, line o query.
- `HadesProjectAwareness` calcola `coverage.source_slices` da tabella reale e
  confronta `head_commit` della slice col commit del workspace.
- Health/capabilities espongono la route source slices.
- La suite remota verifica anche `diagnosable_without_source=true` quando
  memoria, artifact current, code graph, bug evidence e source slices sono
  tutti current.

Integrazione Hades locale:

- Nuova capability dichiarata: `read_source_slice`.
- `read_source_slice` non e' auto-eseguita in piggyback sync: resta
  policy-gated/manual-review.
- `hades_backend_jobs` legge solo finestre lineari bounded, redatte e con path
  containment/secret-file guard.
- `hades backend sync` e `hades backend approve-job` caricano source slices con
  `head_commit` del binding locale quando disponibile.
- `HadesBackendClient` espone `create_source_slice()` e `source_slices()`.
- Provider memoria espone `hades_backend_source_slice_fetch`, live-only con
  timeout breve e senza cache fallback.
- Fixture OpenAPI e docs operative aggiornate.

Verifiche eseguite:

- Locale:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/hermes_cli/test_hades_backend_sync_runner.py tests/hermes_cli/test_hades_backend_client.py tests/agent/test_hades_backend_memory_provider.py`
  passato: `77 passed`.
- Locale:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m ruff check hermes_cli/hades_backend_jobs.py hermes_cli/hades_backend_sync.py hermes_cli/hades_backend_actions.py hermes_cli/hades_backend_client.py hermes_cli/hades_backend_cmd.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/hermes_cli/test_hades_backend_sync_runner.py tests/hermes_cli/test_hades_backend_client.py tests/agent/test_hades_backend_memory_provider.py`
  passato.
- Locale:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m py_compile hermes_cli/hades_backend_jobs.py hermes_cli/hades_backend_sync.py hermes_cli/hades_backend_actions.py hermes_cli/hades_backend_client.py hermes_cli/hades_backend_cmd.py plugins/memory/hades_backend/__init__.py`
  passato.
- Remoto Pint:
  `vendor/bin/pint --test app/Http/Controllers/Hades/SourceSliceController.php app/Http/Controllers/Hades/CapabilitiesController.php app/Http/Controllers/Hades/HealthController.php app/Services/Hades/HadesProjectAwareness.php app/Services/Hades/HadesCapabilityPolicy.php app/Services/Hades/HadesTokenService.php app/Http/Controllers/Dashboard/Api/DashboardHadesController.php routes/api.php database/migrations/2026_07_07_000002_create_hades_source_slices_table.php tests/Feature/Hades/HadesM3SharedMemoryTest.php`
  passato.
- Remoto completo Hades + plugin auth:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades tests/Feature/PluginAuthTest.php`
  passato: `42 passed / 438 assertions`.

## Esecuzione workflow agent bug diagnosis Hades - 2026-07-07

Stato: completata la prima slice P0-6 del piano "Bug Root Cause Awareness".

Integrazione Hades locale:

- Provider memoria: aggiunto tool `hades_backend_graph_search`, live-only,
  implementato sopra backend artifact search (`domain=artifacts`) per route,
  symbol, graph edge e artifact PHP graph.
- Provider memoria: `hades_backend_source_slice_fetch` e'
  disponibile nel workflow diagnosis e resta live-only senza cache fallback.
- Nuova skill built-in:
  `skills/autonomous-ai-agents/hades-bug-diagnosis/SKILL.md`.
- La skill impone ordine operativo:
  awareness status -> bug evidence -> graph search -> source slice fetch ->
  persisted diagnosis report con root cause, mechanism, evidence refs,
  freshness, confidence e next verification.
- Provider memoria: aggiunto tool `hades_backend_diagnosis_report_create`,
  live-only, per salvare report finali o insufficient-evidence senza cache
  fallback.
- Guardrail espliciti: niente line-level cause senza source slice current,
  niente call path esatto senza graph current, niente raw chunks come evidence
  automatico.

Integrazione backend remota:

- Commit remoto `f6443a1 feat: add Hades diagnosis reports`.
- Nuova tabella `hades_diagnosis_reports` con project, workspace, bug report
  opzionale, status, confidence, root cause, mechanism, evidence refs,
  freshness, payload bounded e redactions.
- Nuovo endpoint `POST /api/hades/v1/diagnosis-reports` esposto in health e
  capabilities.
- Feature test backend: salvataggio diagnosis report con evidence refs per
  workspace linkato.

Verifiche eseguite:

- Locale:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_hades_backend_memory_provider.py`
  passato: `20 passed`.
- Locale mirato Hades:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_client.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `58 passed`.
- Locale:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m ruff check plugins/memory/hades_backend/__init__.py tests/agent/test_hades_backend_memory_provider.py`
  passato.
- Remoto:
  `vendor/bin/pint --test app/Http/Controllers/Hades/DiagnosisReportController.php app/Http/Controllers/Hades/CapabilitiesController.php app/Http/Controllers/Hades/HealthController.php routes/api.php database/migrations/2026_07_07_000003_create_hades_diagnosis_reports_table.php tests/Feature/Hades/HadesM3SharedMemoryTest.php`
  passato.
- Remoto completo Hades + plugin auth:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades tests/Feature/PluginAuthTest.php`
  passato: `43 passed / 447 assertions`.

Resta fuori da questa slice:

- Completamento P0-7 remoto/CI della suite no-codebase con bug fixture e
  valutazione strutturata; la prima slice locale e' tracciata sotto.

## Esecuzione no-codebase evaluation suite Hades - 2026-07-07

Stato: avviata la slice P0-7 del piano "Bug Root Cause Awareness".

Integrazione locale:

- Nuovo evaluator `hermes_cli/hades_no_codebase_eval.py` per confrontare output
  diagnosis strutturati contro fixture no-codebase.
- Nuova fixture `tests/fixtures/hades/no_codebase_bug_cases.json` con 7 casi:
  5 bug completi e 2 casi insufficient-evidence.
- Nuovo test `tests/agent/test_hades_bug_diagnosis_no_codebase.py`.
- Il report calcola total/pass/fail, complete accuracy, insufficient accuracy,
  evidence ref coverage, tool coverage, persistence coverage e violazioni
  no-codebase.
- Il gate fallisce se una run usa tool raw source/file/shell come `read_file`,
  `rg`, `shell` o `terminal`.

Verifiche eseguite:

- Locale:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_hades_bug_diagnosis_no_codebase.py`
  passato: `3 passed`.
- Locale:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m ruff check hermes_cli/hades_no_codebase_eval.py tests/agent/test_hades_bug_diagnosis_no_codebase.py`
  passato.

Resta fuori da questa slice:

- Feature tests remoti P0-7 con SQLite in-memory.
- CI gate dedicato lightweight.

## Esecuzione evidence safety policy Hades - 2026-07-07

Stato: avviata la slice P0-8 del piano "Bug Root Cause Awareness".

Integrazione backend remota:

- Commit remoto `a110d52 feat: enforce Hades evidence safety policy`.
- Nuovo service `App\Services\Hades\HadesEvidencePolicy`.
- `POST /api/hades/v1/bug-evidence` rifiuta payload oltre 64 KB e contenuti
  con bearer token, API key, cookie, password, private key o secret assignment
  non redatti.
- `POST /api/hades/v1/source-slices` rifiuta source slice oltre 64 KB o ancora
  contenenti segreti non redatti.
- `POST /api/hades/v1/diagnosis-reports` rifiuta payload oltre 32 KB e root
  cause/mechanism/payload con segreti non redatti.

Verifiche eseguite:

- Remoto:
  `vendor/bin/pint --test app/Services/Hades/HadesEvidencePolicy.php app/Http/Controllers/Hades/BugEvidenceController.php app/Http/Controllers/Hades/SourceSliceController.php app/Http/Controllers/Hades/DiagnosisReportController.php tests/Feature/Hades/HadesM3SharedMemoryTest.php`
  passato.
- Remoto:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades/HadesM3SharedMemoryTest.php`
  passato: `21 passed / 243 assertions`.
- Remoto completo Hades + plugin auth:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades tests/Feature/PluginAuthTest.php`
  passato: `45 passed / 459 assertions`.

Resta fuori da questa slice:

- Dashboard policy UI prima di abilitare source slices.
- Delete/export project/user e retention cleanup backend.

## Esecuzione runtime/test evidence ingestion Hades - 2026-07-07

Stato: avviata la slice P1-1 del piano "Bug Root Cause Awareness".

Integrazione locale:

- Nuovi subcommand `hades backend ingest-test <file>` e
  `hades backend ingest-log <file>`.
- Entrambi richiedono una workspace corrente linkata al backend Hades.
- `ingest-test` carica evidence `kind=failing_test` con
  `retention_class=test_failure`.
- `ingest-log` carica evidence `kind=log_excerpt` con
  `retention_class=log_excerpt`.
- Gli excerpt sono bounded a 64 KB, passano da `redact_secret`, includono
  schema payload (`hades.test_output.v1` o `hades.runtime_log_excerpt.v1`) e
  provano a estrarre frame `file:line`.

Verifiche eseguite:

- Locale:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_cmd.py`
  passato: `16 passed`.
- Locale:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m ruff check hermes_cli/hades_backend_cmd.py tests/hermes_cli/test_hades_backend_cmd.py`
  passato.

Resta fuori da questa slice:

- Parser CI/deploy specifici.
- Upload automatico dopo failure.
- Normalizzazione frame verso graph symbols/source slices oltre al file:line.

## Esecuzione resolved bug memory Hades - 2026-07-07

Stato: completata la prima tranche P1-2 del piano "Bug Root Cause Awareness".

Backend remoto:

- Commit remoto `4f78566 feat: promote Hades diagnoses to resolved bug memory`.
- Nuovo endpoint
  `POST /api/hades/v1/diagnosis-reports/{diagnosisReport}/promote`.
- Promozione consentita solo per diagnosis report `final` con confidence
  `high` o `medium`.
- La richiesta richiede `verification_status` tra `user_confirmed`,
  `test_passed` e `manual_review`.
- La promozione crea `project_memory_entries.kind=resolved_bug` con
  `source=hades_diagnosis_report`, payload `hades.resolved_bug.v1`, links verso
  diagnosis report, bug report ed evidence refs.
- `memory/search` assegna boost ai `resolved_bug` e ritorna `stale` /
  `stale_reason=workspace_head_changed` quando il commit della diagnosi non
  coincide piu' con l'HEAD del workspace.

Agent locale:

- `HadesBackendClient.promote_diagnosis_report()`.
- Nuovo tool provider `hades_backend_resolved_bug_promote`.
- Il tool usa timeout live di 2 secondi, rifiuta status di verifica non
  supportati e degrada a `unmapped_project` se la directory non e' linkata.
- Memory search locale espone `kind`, `stale` e `stale_reason`, e il fallback
  cache assegna priorita' ai `resolved_bug`.

Verifiche eseguite:

- Remoto:
  `vendor/bin/pint --test app/Http/Controllers/Hades/DiagnosisReportController.php app/Http/Controllers/Hades/MemorySearchController.php routes/api.php tests/Feature/Hades/HadesM3SharedMemoryTest.php`
  passato.
- Remoto:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades/HadesM3SharedMemoryTest.php`
  passato: `23 passed / 274 assertions`.
- Remoto:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades tests/Feature/PluginAuthTest.php`
  passato: `47 passed / 490 assertions`.
- Locale:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_hades_backend_memory_provider.py tests/hermes_cli/test_hades_backend_client.py`
  passato: `56 passed`.
- Locale:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m ruff check plugins/memory/hades_backend/__init__.py hermes_cli/hades_backend_client.py tests/agent/test_hades_backend_memory_provider.py tests/hermes_cli/test_hades_backend_client.py`
  passato.

Resta fuori da questa tranche:

- Dashboard bug case detail.
- Invalidation symbol-level basata su graph diff; oggi lo stale e' commit-level.

## Esecuzione graph traversal Hades - 2026-07-07

Stato: completata la prima tranche P1-3 del piano "Bug Root Cause Awareness".

Backend remoto:

- Commit remoto `2fba3fb feat: add Hades graph traversal endpoint`.
- Nuovo controller `GraphTraversalController`.
- Nuovo endpoint `GET /api/hades/v1/graph/traverse`.
- `health` e `capabilities` dichiarano `graph_traverse`.
- Il traversal usa l'ultimo artifact `hades.php_graph.v1`/`hades.code_graph.v1`
  del workspace, parte da route/symbol/file/class/method e restituisce nodes,
  edges, `match_fields`, freshness e provenance.
- Traversal bounded: `max_depth` 1-3, `limit` 1-50.

Agent locale:

- `HadesBackendClient.graph_traverse()`.
- Nuovo tool provider `hades_backend_graph_traverse`.
- Tool live-only con timeout 2 secondi; degrada a `unmapped_project` senza
  workspace linkata.
- Output compatto con nodes/edges, freshness e provenance.

Verifiche eseguite:

- Remoto:
  `vendor/bin/pint --test app/Http/Controllers/Hades/GraphTraversalController.php app/Http/Controllers/Hades/CapabilitiesController.php app/Http/Controllers/Hades/HealthController.php routes/api.php tests/Feature/Hades/HadesM3SharedMemoryTest.php`
  passato.
- Remoto:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades/HadesM3SharedMemoryTest.php`
  passato: `25 passed / 295 assertions`.
- Remoto:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades tests/Feature/PluginAuthTest.php`
  passato: `49 passed / 511 assertions`.
- Locale:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_hades_backend_memory_provider.py tests/hermes_cli/test_hades_backend_client.py`
  passato: `58 passed`.
- Locale:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m ruff check plugins/memory/hades_backend/__init__.py hermes_cli/hades_backend_client.py tests/agent/test_hades_backend_memory_provider.py tests/hermes_cli/test_hades_backend_client.py`
  passato.

Resta fuori da questa tranche:

- FTS/vector search per evidence testuale.
- Filtri strutturati dedicati per commit/symbol su bug evidence.
- Traversal verso migration/log oltre agli edge gia' presenti nel graph artifact.

## Esecuzione awareness status locale Hades - 2026-07-07

Stato: completata la prima tranche P1-5 del piano "Observability And Diagnosis
Quality SLOs".

Agent locale:

- `hades backend status --json` include ora `awareness` top-level.
- Ogni binding include `awareness.status`, `diagnosable_without_source`,
  `coverage` per memory cache, project artifacts, source slices, bug evidence e
  `quality.missing`.
- Il payload e' conservativo: senza bug evidence nota, source slices, artifact
  index e memory cache, una diagnosi senza source code resta `partial` e
  `diagnosable_without_source=false`.
- Se un profilo ha piu' workspace binding, i contatori dell'ultima sync restano
  `aggregate` e non rendono pronto nessun binding specifico.
- Lo status locale non fa chiamate backend live; resta un segnale operativo
  locale. Il tool/API `project_awareness_status` resta la fonte live per
  freshness backend.

Verifiche eseguite:

- Locale:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_sync_runner.py tests/hermes_cli/test_hades_backend_mvp_smoke.py tests/hermes_cli/test_hades_backend_cmd.py tests/hermes_cli/test_hades_backend_web_api.py tests/test_docs_hades_mvp.py`
  passato: `44 passed`.
- Locale:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m ruff check hermes_cli/hades_backend_status.py tests/hermes_cli/test_hades_backend_sync_runner.py tests/hermes_cli/test_hades_backend_mvp_smoke.py`
  passato.
- Locale:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/docs_audit.py`
  passato.
- Locale:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m py_compile hermes_cli/hades_backend_status.py`
  passato.

Resta fuori da questa tranche:

- Dashboard trend e ultimi failure.
- Metriche aggregate su diagnosis confidence distribution.
- Log strutturati per ogni tool diagnosis con evidence refs usati.

## Esecuzione dashboard/desktop awareness Hades - 2026-07-07

Stato: completata la prima tranche P1-4 del piano "Dashboard And Desktop
Investigation Surfaces".

Frontend locale:

- `web/src/pages/BackendPage.tsx` mostra una sezione "Project awareness" con
  stato top-level, binding source-free ready, ready/partial/degraded bindings e
  missing evidence aggregata.
- Ogni workspace linked mostra ora awareness status, commit HEAD locale e
  coverage per memory cache, project artifacts, source slices e bug evidence.
- `web/src/lib/api.ts` tipizza il nuovo payload `awareness`.
- `apps/desktop/src/app/chat/composer/status-stack/hades-backend-status.ts`
  sintetizza awareness `partial`, `degraded` e `unmapped` nel warning compatto
  del composer anche quando non arriva un'action testuale.

Verifiche eseguite:

- Locale:
  `npm --prefix web run typecheck`
  passato.
- Locale:
  `npm exec vitest run apps/desktop/src/app/chat/composer/status-stack/hades-backend-status.test.ts`
  passato: `1 passed`, `4 passed`.
- Locale:
  `npm exec eslint -- --max-warnings=0 src/pages/BackendPage.tsx src/lib/api.ts`
  eseguito da `web/`, passato.
- Nota: `npm --prefix web run lint -- --max-warnings=0 src/pages/BackendPage.tsx src/lib/api.ts`
  invoca `eslint .` dal package script e fallisce su errori preesistenti fuori
  scope in componenti/pagine non modificate.

Resta fuori da questa tranche:

- Trend storici e ultimi failure in dashboard.
- Bug case detail con evidence timeline, graph path e source slices.
- Controlli UI completi per source/evidence policy e diagnosis promotion.

## Esecuzione identity domains Hades - 2026-07-07

Stato: completata la prima tranche P1-6 del piano "Multi-Device Identity And
Project Binding UX".

Agent locale:

- `hades backend status --json` include ora `identity`.
- `identity.personal_memory` descrive la memoria del profilo locale e la marca
  come non portabile tra device.
- `identity.project_memory` descrive la memoria condivisa del backend project,
  include `project_id`, `cached_items` e `portable_between_devices=true` quando
  il backend e' configurato.
- `identity.workspace_binding` descrive lo stato locale del workspace:
  binding totali/linkati, binding corrente se il comando gira dentro una repo
  linkata, e quanti binding sono source-free ready.
- L'output umano di `hades backend status` mostra personal memory, project
  memory e workspace scope.

Verifiche eseguite:

- Locale:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_sync_runner.py tests/hermes_cli/test_hades_backend_cmd.py tests/hermes_cli/test_hades_backend_mvp_smoke.py tests/hermes_cli/test_hades_backend_web_api.py tests/test_docs_hades_mvp.py`
  passato: `44 passed`.
- Locale:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m ruff check hermes_cli/hades_backend_status.py hermes_cli/hades_backend_cmd.py tests/hermes_cli/test_hades_backend_sync_runner.py`
  passato.
- Locale:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m py_compile hermes_cli/hades_backend_status.py hermes_cli/hades_backend_cmd.py`
  passato.
- Locale:
  `npm --prefix web run typecheck`
  passato.

Resta fuori da questa tranche:

- Recovery/onboarding UI per nuovo device.
- Test remoti multi-device/multi-agent.
- Policy UI completa per source-slice ACL.

## Esecuzione note-quality backfill locale Hades - 2026-07-07

Stato: completata la prima tranche P1-7 del piano "Notes And Raw Chunk
Backfill".

Agent locale:

- Nuovo modulo `hermes_cli/hades_note_quality.py`.
- Nuovo comando `hades backend backfill-note <path> [--json]`.
- Il comando legge un file bounded, classifica raw chunks come
  `classification=raw_chunk`, mantiene `automatic_recall_allowed=false` e
  `memory_proposal_ready=false`.
- Con `--create-proposals`, salva i candidate facts come local
  `memory_proposals` pending con intent `note_backfill_candidate`; resta una
  review queue, non memoria accettata.
- Per chunk tipo `hades.backend_wiki.file_chunk.v1` con edge
  `route:* --handled_by--> file:*`, il backfill raggruppa le route per handler
  in candidate facts `route_handler_group` con evidence refs a batch/schema/path,
  chunk index e sha256.
- La preview non crea project memory e non promuove facts: serve una review
  esplicita prima di salvare memoria condivisa.

Verifiche eseguite:

- Locale:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_note_quality.py tests/hermes_cli/test_hades_backend_cmd.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `49 passed`.
- Locale:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m ruff check hermes_cli/hades_note_quality.py hermes_cli/hades_backend_cmd.py tests/hermes_cli/test_hades_note_quality.py`
  passato.
- Locale:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m py_compile hermes_cli/hades_note_quality.py hermes_cli/hades_backend_cmd.py`
  passato.
- Locale:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/docs_audit.py`
  passato.

Resta fuori da questa tranche:

- Upload backend della review queue via sync e' disponibile come proposal flow;
  resta da implementare una review queue dedicata lato backend.
- Promozione automatizzata candidate fact -> verified wiki/project memory.
- Ranking prima/dopo backfill su dataset reale.

## Esecuzione guided bug intake CLI Hades - 2026-07-07

Stato: completata la prima tranche P2-1 del piano "Guided Bug Intake".

Agent locale:

- Nuovo comando `hades backend bug-intake`.
- Il comando richiede `--title` e `--symptom`, accetta `--steps`,
  `--expected`, `--actual`, `--severity`, `--environment`, e crea un bug report
  backend dal workspace linkato.
- `--test-output` e `--log` sono ripetibili e creano evidence redatte
  rispettivamente `failing_test` e `log_excerpt` legate al bug report creato.
- Il payload intake usa schema `hades.bug_intake.v1`; test/log riusano i limiti
  e la redazione gia' usati da `ingest-test`/`ingest-log`.

Verifiche eseguite:

- Locale:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_cmd.py tests/hermes_cli/test_hades_backend_client.py tests/test_docs_hades_mvp.py`
  passato: `57 passed`.
- Locale:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m ruff check hermes_cli/hades_backend_cmd.py tests/hermes_cli/test_hades_backend_cmd.py`
  passato.
- Locale:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m py_compile hermes_cli/hades_backend_cmd.py`
  passato.
- Locale:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/docs_audit.py`
  passato.

Resta fuori da questa tranche:

- Wizard desktop/dashboard.
- Privacy preview visuale prima dell'invio.
- Intake di screenshot/browser console/http trace da UI.

## Esecuzione framework expansion Node/TS/React Hades - 2026-07-07

Stato: completata la prima tranche locale e backend P2-2 del piano "Framework
Expansion".

Agent locale:

- `populate_backend_ast` ora produce `hades.code_graph.v1` quando trova
  workspace JavaScript/TypeScript senza PHP prioritario.
- Il graph rileva framework (`nextjs`, `react`, `express`, `node`), route
  handlers Next, page entrypoint Next, route Express, simboli
  component/function/class/export, import edge e dependency manifests.
- Gli artifact restano source-symbol: `raw_source_included=false`, retention
  `source_symbols`, omission reasons bounded e nessun chunk di sorgente grezzo.
- `hades backend sync` carica anche artifact `hades.code_graph.v1`.
- OpenAPI e runbook locali dichiarano il nuovo schema accanto a
  `hades.git_tree.v1`, `hades.symbols.v1` e `hades.php_graph.v1`.

Backend remoto:

- Commit remoto `635aed5 feat: accept Hades code graph artifacts`.
- `ArtifactController` accetta upload `hades.code_graph.v1`.
- `MemorySearchController` tratta `hades.code_graph.v1` come code graph
  searchable insieme a `hades.php_graph.v1`, usando anche `path` per route
  Next/Express e includendo il framework nel summary.
- OpenAPI remoto dichiara `hades.code_graph.v1` nell'enum artifact.

Verifiche eseguite:

- Locale:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/hermes_cli/test_hades_backend_sync_runner.py`
  passato: `34 passed`.
- Locale:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m ruff check hermes_cli/hades_backend_jobs.py hermes_cli/hades_backend_sync.py tests/hermes_cli/test_hades_backend_jobs.py tests/hermes_cli/test_hades_backend_sync_runner.py`
  passato.
- Locale:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m py_compile hermes_cli/hades_backend_jobs.py hermes_cli/hades_backend_sync.py`
  passato.
- Remoto:
  `vendor/bin/pint --test app/Http/Controllers/Hades/ArtifactController.php app/Http/Controllers/Hades/MemorySearchController.php tests/Feature/Hades/HadesM3SharedMemoryTest.php tests/Feature/Hades/HadesM5MvpCompletionTest.php`
  passato.
- Remoto:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades/HadesM3SharedMemoryTest.php tests/Feature/Hades/HadesM5MvpCompletionTest.php`
  passato: `30 passed`.
- Remoto:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades tests/Feature/PluginAuthTest.php`
  passato: `50 passed`.

Resta fuori da questa tranche:

- Symfony, FastAPI/Django e database schema adapters.
- Parser strutturati profondi per JS/TS oltre al primo graph regex-bounded.
- Search/traversal semantico piu' profondo su graph JS/TS oltre al summary
  artifact e al traversal generico gia' disponibile.
