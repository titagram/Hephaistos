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

- Live memory search nel provider `hades_backend` usa timeout di 1 secondo via
  `runtime.client_from_config(timeout=1.0)`, cosi' il prefetch non blocca a
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
- Il tool usa timeout live di 1 secondo e non ha cache fallback, per evitare che
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
- Il tool usa timeout live di 1 secondo e non ha cache fallback, come bug
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
  timeout dedicato di 1.5 secondi e senza cache fallback.
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
- Delete/export project/user oltre lo scope workspace e dashboard policy UI.

## Esecuzione privacy/export/retention Hades - 2026-07-07

Stato: completata la tranche P0-8 workspace-scoped per export, delete e
retention cleanup di dati Hades content-bearing.

Backend remoto:

- Commit remoto `d383c4f feat: add Hades privacy retention controls`.
- Follow-up remoto `6559223 feat: audit Hades privacy operations`.
- Nuovo controller `App\Http\Controllers\Hades\DataPrivacyController`.
- Nuove route autenticate:
  - `GET /api/hades/v1/privacy/export`;
  - `POST /api/hades/v1/privacy/delete`;
  - `POST /api/hades/v1/privacy/retention-cleanup`.
- Tutte le route verificano `project_id`, `workspace_binding_id`, agent token e
  binding linked nello stesso scope.
- Export restituisce collezioni e conteggi per bug reports, bug evidence,
  source slices, evidence packs e diagnosis reports; `include_content=false`
  omette i campi content-bearing.
- Delete e retention cleanup sono dry-run per default e richiedono
  `confirm=true` quando `dry_run=false`.
- Ogni export/delete/retention riuscito scrive `audit_logs` con action,
  `hades_agent_id`, scope, conteggi, dry-run flag e retention metadata; i test
  verificano che payload audit non contenga source slice, evidence payload o
  diagnosis text.

Integrazione locale:

- `HadesBackendClient` espone `privacy_export`, `privacy_delete` e
  `privacy_retention_cleanup`.
- Nuovi comandi:
  - `hades backend privacy-export --json`;
  - `hades backend privacy-delete --json`;
  - `hades backend retention-cleanup --retention-days <days> --json`.
- Il CLI usa il workspace binding corrente, exporta metadata-only per default
  e invia `confirm=true` solo con `--yes`.
- OpenAPI locale e docs operative/runbook aggiornati.

Integrazione dashboard locale:

- Le operazioni privacy sono state estratte in
  `hermes_cli/hades_backend_actions.py` e riusate da CLI e web server.
- Nuove route dashboard:
  - `POST /api/hades/backend/privacy-export`;
  - `POST /api/hades/backend/privacy-delete`;
  - `POST /api/hades/backend/retention-cleanup`.
- La pagina Backend aggiunge il pannello `Privacy retention` con export
  metadata/content, delete dry-run/confirm e retention cleanup dry-run/confirm.
  Le azioni distruttive richiedono conferma UI e inviano `confirm=true` solo
  dopo conferma esplicita.

Verifiche eseguite:

- Remoto:
  `php -l app/Http/Controllers/Hades/DataPrivacyController.php` passato.
- Remoto:
  `vendor/bin/pint --test app/Http/Controllers/Hades/DataPrivacyController.php routes/api.php tests/Feature/Hades/HadesM3SharedMemoryTest.php`
  passato dopo formattazione.
- Remoto:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades/HadesM3SharedMemoryTest.php`
  passato: `31 passed / 393 assertions`.
- Remoto completo Hades + plugin auth:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades tests/Feature/PluginAuthTest.php`
  passato: `55 passed / 612 assertions`.
- Remoto follow-up audit:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades tests/Feature/PluginAuthTest.php`
  passato: `55 passed / 625 assertions`.
- Remoto:
  `git diff --check` passato prima del commit.
- Locale:
  `.venv/bin/python -m pytest -q tests/hermes_cli/test_hades_backend_client.py tests/hermes_cli/test_hades_backend_cmd.py`
  passato: `60 passed`.
- Locale lint/contract:
  `json.tool` su OpenAPI, `py_compile` e `ruff check` sui file client/CLI/test
  passati.
- Locale dashboard privacy, passato: `4 passed`.

  ```bash
  .venv/bin/pytest \
    tests/hermes_cli/test_hades_backend_cmd.py::test_backend_privacy_export_defaults_to_metadata_only \
    tests/hermes_cli/test_hades_backend_cmd.py::test_backend_privacy_delete_is_dry_run_unless_confirmed \
    tests/hermes_cli/test_hades_backend_cmd.py::test_backend_retention_cleanup_requires_yes_for_confirmed_delete \
    tests/hermes_cli/test_hades_backend_web_api.py::test_hades_backend_web_routes_run_privacy_and_retention_actions
  ```
- Locale frontend:
  `npm --prefix web run typecheck` passato; `npx eslint
  src/pages/BackendPage.tsx src/lib/api.ts` passato da `web/`.
- Locale client contract:
  `.venv/bin/pytest tests/hermes_cli/test_hades_backend_client.py` passato:
  `40 passed`.

Resta fuori da questa tranche:

- Export/delete project-wide o user-wide oltre allo scope workspace linked.
- Ruoli dashboard granulari oltre alla guardia auth locale gia' esistente.

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

Follow-up locale completato:

- Nuovo subcommand `hades backend ingest-deploy --deploy-commit <sha>`.
- `bug-intake` accetta `--deploy-commit` e allega automaticamente evidence
  `kind=deploy_version` al bug report creato.
- Payload `hades.deploy_version.v1`: conserva commit deployato, workspace head
  indicizzato, environment/source e `mismatch=true|false|null`.
- Summary cercabile: in caso di divergenza inizia con `Deploy commit mismatch`,
  cosi' una diagnosi senza source code puo' rilevare che sta guardando una
  fotografia progetto diversa da quella deployata.

Secondo follow-up locale completato:

- Nuovo subcommand `hades backend ingest-http --url <url>`.
- `bug-intake` accetta `--request-url`, `--request-method`,
  `--response-status`, `--request-file` e `--response-file`.
- Payload `hades.http_request.v1` e `hades.http_response.v1` con URL redatta,
  excerpt bounded e `retention_class=http_trace`.
- Questo chiude la parte locale del "request context" P1-1: il backend puo'
  cercare il sintomo HTTP e collegarlo a route/graph/source slices senza
  dipendere da descrizioni libere dell'utente.

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
- Il tool usa timeout write live di 2 secondi, rifiuta status di verifica non
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
- Tool live-only con timeout lookup di 1 secondo; degrada a `unmapped_project` senza
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

## Esecuzione dashboard diagnosis quality Hades - 2026-07-07

Stato: completata una tranche P1-4/P1-5 per rendere visibile in dashboard la
qualita' della diagnosi source-free.

Frontend locale:

- `web/src/pages/BackendPage.tsx` mostra ora il pannello "Diagnosis quality"
  nella pagina backend.
- Il pannello deriva tutto da `HadesBackendStatus`: binding source-free ready,
  binding bloccati, distribuzione `quality.confidence`, missing evidence
  aggregata, ultimo segnale di qualita' e next gate operativo.
- La card non introduce nuovi endpoint e non duplica la chat: rende leggibile lo
  stato gia' prodotto dal backend/status locale.

Verifiche eseguite:

- Locale:
  `npm run --prefix web typecheck`
  passato.

Resta fuori da questa tranche:

- Trend storici e drill-down sugli ultimi failure.
- Pagina bug case con evidence timeline, graph path, source slices e diagnosis
  report detail.
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

## Esecuzione note-quality backfill idempotente Hades - 2026-07-07

Stato: completata una seconda tranche P1-7 locale.

Agent locale:

- I candidate facts prodotti da `hades_note_quality` hanno ora un fingerprint
  stabile calcolato su kind, subject, predicate, objects ed evidence ref.
- `hades backend backfill-note --create-proposals` salta le proposals locali
  gia' presenti con lo stesso fingerprint, invece di duplicare la review queue.
- Il JSON del comando espone `skipped_duplicate_proposal_count` e
  `skipped_duplicate_proposal_ids`.

Verifiche eseguite:

- Locale:
  `.venv/bin/python -m pytest -q tests/hermes_cli/test_hades_note_quality.py`
  passato: `3 passed`.

Resta fuori da questa tranche:

- Promozione automatizzata candidate fact -> verified wiki/project memory.
- Ranking prima/dopo backfill su dataset reale.

## Esecuzione note-quality diagnostics Hades - 2026-07-07

Stato: completata una terza tranche P1-7 locale.

Agent locale:

- `hades_note_quality.analyze_note_quality()` espone ora
  `quality_score`, `quality_grade`, `quality_issues` e `promotion_state`.
- `hades backend backfill-note` mostra quality grade/score e promotion state
  anche nell'output testuale, non solo JSON.
- I raw chunk con candidate facts restano fuori da automatic recall ma vengono
  marcati come `review_candidate`; note libere senza facts diventano
  `needs_manual_structuring`.

Verifiche eseguite:

- Locale:
  `.venv/bin/python -m pytest -q tests/hermes_cli/test_hades_note_quality.py`
  passato: `4 passed`.
- Locale comando backend:
  `.venv/bin/python -m pytest -q tests/hermes_cli/test_hades_backend_cmd.py`
  passato: `22 passed`.
- Locale lint/compile:
  `ruff check hermes_cli/hades_note_quality.py hermes_cli/hades_backend_cmd.py tests/hermes_cli/test_hades_note_quality.py`
  passato con `.venv/bin/ruff`; `py_compile hermes_cli/hades_note_quality.py hermes_cli/hades_backend_cmd.py`
  passato.

Resta fuori da questa tranche:

- Promozione automatizzata candidate fact -> verified wiki/project memory.
- Ranking prima/dopo backfill su dataset reale.

## Esecuzione note-quality backend review gate Hades - 2026-07-07

Stato: completata una quarta tranche P1-7 local+backend.

Problema corretto:

- Il comando locale `hades backend backfill-note --create-proposals` creava
  correttamente candidate facts review-only, ma il backend auto-accettava ogni
  proposal `action=create`. Di conseguenza un `note_backfill_candidate` poteva
  finire subito in `project_memory_entries`, contro la policy raw-chunk
  quarantined/review-only.

Agent locale:

- `_sync_memory` ora interpreta una risposta backend `proposal.status=pending`
  come `submitted` locale, con reason backend conservata.
- Questo evita reinvii infiniti: la proposal e' stata consegnata al backend e
  resta in review remota, non nel backlog locale da ripostare.
- I flussi auto-accepted normali (`memory_write` low-risk) restano invariati:
  se il backend risponde `accepted`, la proposal locale diventa `accepted`.

Backend remoto:

- `MemoryProposalController` mantiene l'auto-accept per create low-risk.
- L'intent `note_backfill_candidate` richiede review manuale: status `pending`,
  `reason_code=manual_review_required`, nessun `memory_entry_id` e nessuna
  riga in `project_memory_entries`.
- Idempotency resta invariata su `workspace_binding_id + local_proposal_id`:
  un retry ritorna la stessa proposal pending senza duplicare memoria.

Verifiche eseguite:

- Locale mirato:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_cmd.py::test_backend_sync_updates_memory_cache_and_pending_proposals tests/hermes_cli/test_hades_backend_cmd.py::test_backend_sync_marks_backend_pending_proposals_as_submitted`
  passato: `2 passed`.
- Locale aggregato:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_cmd.py tests/hermes_cli/test_hades_note_quality.py`
  passato: `27 passed`.
- Locale lint:
  `.venv/bin/ruff check hermes_cli/hades_backend_cmd.py tests/hermes_cli/test_hades_backend_cmd.py tests/hermes_cli/test_hades_note_quality.py`
  passato.
- Remoto mirato:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades/HadesM3SharedMemoryTest.php --filter=backfill`
  passato: `1 passed (16 assertions)`.
- Remoto aggregato:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades tests/Feature/PluginAuthTest.php`
  passato: `61 passed (729 assertions)`.
- Remoto syntax:
  `php -l app/Http/Controllers/Hades/MemoryProposalController.php`
  passato nel container app.

Resta fuori da questa tranche:

- UI/backend admin action per promuovere esplicitamente un
  `note_backfill_candidate` in verified wiki/project memory.
- Freshness specifica del candidate fact promosso.
- Ranking prima/dopo backfill su dataset reale.

## Esecuzione note-quality manual promotion Hades - 2026-07-07

Stato: completata una quinta tranche P1-7 backend.

Backend remoto:

- L'endpoint dashboard
  `POST /api/dashboard/admin/hades/memory-proposals/{proposal}/review` ora
  promuove in modo idempotente una proposal `action=create` quando viene
  accettata e non ha ancora `memory_entry_id`.
- Per intent `note_backfill_candidate`, la memoria creata usa
  `kind=verified_note_fact`, `source=hades_agent`, summary revisionata e payload
  `hades.memory_proposal.v1` con provenance/candidate fact originale.
- Il payload promosso include freshness del workspace binding:
  `status=current` quando il binding ha HEAD commit,
  `workspace_head_commit`, `index_status=reviewed_note_fact` e `reviewed_at`.
- La promozione crea una sola riga `project_memory_entries`; review ripetute
  `accepted` riusano lo stesso `memory_entry_id`.
- Review `refused`/`conflicted` continuano a non creare memoria.

Verifiche eseguite:

- Remoto mirato:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades/HadesM5MvpCompletionTest.php --filter=promotes`
  passato: `1 passed (18 assertions)`.
- Remoto syntax:
  `php -l app/Http/Controllers/Dashboard/Api/DashboardHadesController.php`
  passato nel container app.
- Remoto aggregato:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades tests/Feature/PluginAuthTest.php`
  passato: `63 passed (754 assertions)`.

Resta fuori da questa tranche:

- UI copy/controls dedicati per distinguere promote/refuse dei
  `note_backfill_candidate`.
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

- Database schema adapters cross-framework.
- Parser strutturati profondi per JS/TS oltre al primo graph regex-bounded.
- Search/traversal semantico piu' profondo su graph JS/TS oltre al summary
  artifact e al traversal generico gia' disponibile.

## Esecuzione Python web graph Hades - 2026-07-07

Stato: completata una tranche locale P2-2.

Agent locale:

- `populate_backend_ast` produce `hades.code_graph.v1` per workspace Python web
  quando rileva route FastAPI o Django.
- FastAPI: estrae decorators `@app.<method>()` e `@router.<method>()`, include
  prefix `APIRouter(prefix=...)`, route name opzionale e route-handler edge.
- Django: estrae `path()`/`re_path()` in `urls.py`, handler function/class
  dotted e route name opzionale.
- Workspace Python senza route web continuano a produrre `hades.symbols.v1`,
  quindi il comportamento legacy resta compatibile.
- Gli artifact restano source-free: route, handler, path, linee, simboli ed
  edge, non codice sorgente.

Verifiche eseguite:

- Locale mirato:
  `.venv/bin/python -m pytest -q tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_python_web_graph_without_source tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_python_symbols_without_source`
  passato: `2 passed`.
- Locale aggregato:
  `.venv/bin/python -m pytest -q tests/hermes_cli/test_hades_backend_jobs.py`
  passato: `15 passed`.
- Locale lint/compile:
  `ruff check hermes_cli/hades_backend_jobs.py tests/hermes_cli/test_hades_backend_jobs.py`
  passato con `.venv/bin/ruff`; `py_compile hermes_cli/hades_backend_jobs.py`
  passato.

Resta fuori da questa tranche:

- Database schema adapters cross-framework oltre Django models.
- Parser strutturati profondi per JS/TS/Python/PHP oltre ai casi AST/regex
  bounded.

## Esecuzione Django DB graph Hades - 2026-07-07

Stato: completata una tranche locale P2-2.

Agent locale:

- `populate_backend_ast` estrae metadata source-free da classi Django
  `models.Model`.
- Il graph Python include `database.tables` con table name, app label, model,
  columns, field type, flag strutturali (`unique`, `db_index`, `null`,
  `primary_key`, `max_length`) e foreign key.
- I model-only projects senza route ora producono `hades.code_graph.v1` quando
  hanno schema DB utile, invece di degradare a `hades.symbols.v1`.
- Le FK risolvono `Meta.db_table` dei target nello stesso file quando
  disponibile, cosi' l'edge punta alla tabella reale.
- Gli artifact restano source-free: niente corpo dei model, niente chiamate
  `models.<Field>`.

Verifiche eseguite:

- Locale mirato:
  `.venv/bin/python -m pytest -q tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_python_web_graph_without_source tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_django_models_graph_without_routes tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_python_symbols_without_source`
  passato: `3 passed`.
- Locale aggregato:
  `.venv/bin/python -m pytest -q tests/hermes_cli/test_hades_backend_jobs.py`
  passato: `17 passed`.
- Locale lint/compile:
  `ruff check hermes_cli/hades_backend_jobs.py tests/hermes_cli/test_hades_backend_jobs.py`
  passato con `.venv/bin/ruff`; `py_compile hermes_cli/hades_backend_jobs.py`
  passato.

Resta fuori da questa tranche:

- Prisma/Drizzle, Doctrine e schema SQL raw adapters.
- Resolver FK Django cross-file/cross-app completo oltre alla mappa
  model-table nello stesso file.
- Parser AST piu' profondo per manager/queryset e migration operations Django.

## Esecuzione SQLAlchemy DB graph Hades - 2026-07-07

Stato: completata una tranche locale P2-2.

Agent locale:

- `populate_backend_ast` estrae metadata source-free da classi SQLAlchemy
  declarative con `__tablename__`.
- Supporta `Column(...)` e `mapped_column(...)`, con table, columns, type,
  `nullable`, `unique`, `index`, `primary_key` e `ForeignKey("table.column")`.
- I model-only projects SQLAlchemy ora producono `hades.code_graph.v1` con
  `framework: sqlalchemy` quando hanno schema DB utile.
- Gli artifact restano source-free: niente chiamate `Column(...)` o
  `ForeignKey(...)`.

Verifiche eseguite:

- Locale mirato:
  `.venv/bin/python -m pytest -q tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_python_web_graph_without_source tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_django_models_graph_without_routes tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_sqlalchemy_schema_graph_without_routes tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_python_symbols_without_source`
  passato: `4 passed`.
- Locale aggregato:
  `.venv/bin/python -m pytest -q tests/hermes_cli/test_hades_backend_jobs.py`
  passato: `18 passed`.
- Locale lint/compile:
  `ruff check hermes_cli/hades_backend_jobs.py tests/hermes_cli/test_hades_backend_jobs.py`
  passato con `.venv/bin/ruff`; `py_compile hermes_cli/hades_backend_jobs.py`
  passato.

Resta fuori da questa tranche:

- Drizzle, Doctrine e schema SQL raw adapters.
- SQLAlchemy relationship graph e resolver dichiarazioni FK piu' complessi.

## Esecuzione Prisma DB graph Hades - 2026-07-07

Stato: completata una tranche locale P2-2.

Agent locale:

- `populate_backend_ast` include `.prisma` nel graph Node/TS e gestisce anche
  progetti Prisma-only.
- Estrae `model`, `@@map`, campi scalari, `@id`, `@unique`, `@default`,
  optional/list type e `@relation(fields: [...], references: [...])`.
- Il graph include `database.tables`, simboli `prisma_model`, edge
  `model_table` e `foreign_key`, senza raw schema.
- Progetti solo Prisma producono `hades.code_graph.v1` con `language: prisma`
  e `framework: prisma`.

Verifiche eseguite:

- Locale mirato:
  `.venv/bin/python -m pytest -q tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_prisma_schema_graph_without_source tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_node_react_code_graph_without_source`
  passato: `2 passed`.
- Locale aggregato:
  `.venv/bin/python -m pytest -q tests/hermes_cli/test_hades_backend_jobs.py`
  passato: `19 passed`.
- Locale lint/compile:
  `ruff check hermes_cli/hades_backend_jobs.py tests/hermes_cli/test_hades_backend_jobs.py`
  passato con `.venv/bin/ruff`; `py_compile hermes_cli/hades_backend_jobs.py`
  passato.

Resta fuori da questa tranche:

- Doctrine adapter dedicato.
- Prisma relation edge piu' ricchi oltre alle FK tabella/colonna.

## Esecuzione Drizzle DB graph Hades - 2026-07-07

Stato: completata una tranche locale P2-2.

Agent locale:

- `populate_backend_ast` estrae schema source-free da dichiarazioni Drizzle
  `pgTable`, `mysqlTable` e `sqliteTable`.
- Estrae table var, table name, factory, colonne, tipo builder, `primaryKey`,
  `notNull`, `unique`, `default` e FK `.references(() => table.column)`.
- Le FK risolvono il nome reale della colonna target quando la tabella target e'
  nello stesso file.
- Il graph include `database.tables`, simboli `drizzle_table`, edge
  `model_table` e `foreign_key`, senza raw TS.

Verifiche eseguite:

- Locale mirato:
  `.venv/bin/python -m pytest -q tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_drizzle_schema_graph_without_source tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_node_react_code_graph_without_source tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_prisma_schema_graph_without_source`
  passato: `3 passed`.
- Locale aggregato:
  `.venv/bin/python -m pytest -q tests/hermes_cli/test_hades_backend_jobs.py`
  passato: `21 passed`.
- Locale lint/compile:
  `ruff check hermes_cli/hades_backend_jobs.py tests/hermes_cli/test_hades_backend_jobs.py`
  passato con `.venv/bin/ruff`; `py_compile hermes_cli/hades_backend_jobs.py`
  passato.

Resta fuori da questa tranche:

- Parser ORM piu' profondi oltre alle FK tabella/colonna bounded.
- Drizzle relation graph piu' ricco oltre alle FK tabella/colonna nello stesso
  file.

## Esecuzione Doctrine DB graph Hades - 2026-07-07

Stato: completata una tranche locale P2-2.

Agent locale:

- `populate_backend_ast` estrae schema source-free da entity Doctrine attribute.
- Supporta `#[ORM\Entity]`, `#[ORM\Table(name: ...)]`,
  `#[ORM\Column(...)]`, `#[ORM\Id]`, `#[ORM\ManyToOne(...)]` e
  `#[ORM\JoinColumn(...)]`.
- Il graph PHP marca `framework: doctrine` per progetti PHP Doctrine-only e
  aggiunge `database.tables`, `model_table` e `foreign_key`.
- Gli artifact restano source-free: niente attribute raw e niente corpo entity.

Verifiche eseguite:

- Locale mirato:
  `.venv/bin/python -m pytest -q tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_doctrine_schema_graph_without_source tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_symfony_php_graph_without_source tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source`
  passato: `3 passed`.
- Locale aggregato:
  `.venv/bin/python -m pytest -q tests/hermes_cli/test_hades_backend_jobs.py`
  passato: `22 passed`.
- Locale lint/compile:
  `ruff check hermes_cli/hades_backend_jobs.py tests/hermes_cli/test_hades_backend_jobs.py`
  passato con `.venv/bin/ruff`; `py_compile hermes_cli/hades_backend_jobs.py`
  passato.

Resta fuori da questa tranche:

- Doctrine annotations legacy, embeddables e relation graph piu' ricco oltre
  alle FK tabella/colonna bounded.

## Esecuzione SQL raw DB graph Hades - 2026-07-07

Stato: completata una tranche locale P2-2.

Agent locale:

- `populate_backend_ast` produce `hades.code_graph.v1` per progetti SQL-only.
- Estrae `CREATE TABLE`, colonne, flag `PRIMARY KEY`, `NOT NULL`, `UNIQUE`,
  FK inline `REFERENCES` e FK table-level `FOREIGN KEY`.
- Il graph include `database.tables`, simboli table, edge `schema_table` e
  `foreign_key`, senza raw SQL.

Verifiche eseguite:

- Locale mirato:
  `.venv/bin/python -m pytest -q tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_sql_schema_graph_without_source tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_prisma_schema_graph_without_source tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_python_symbols_without_source`
  passato: `3 passed`.
- Locale aggregato:
  `.venv/bin/python -m pytest -q tests/hermes_cli/test_hades_backend_jobs.py`
  passato: `20 passed`.
- Locale lint/compile:
  `ruff check hermes_cli/hades_backend_jobs.py tests/hermes_cli/test_hades_backend_jobs.py`
  passato con `.venv/bin/ruff`; `py_compile hermes_cli/hades_backend_jobs.py`
  passato.

Resta fuori da questa tranche:

- Parser ORM piu' profondi oltre agli adapter completati.
- Dialect SQL avanzato oltre a `CREATE TABLE`/FK bounded parsing.

## Esecuzione Symfony graph Hades - 2026-07-07

Stato: completata una tranche locale P2-2.

Agent locale:

- `populate_backend_ast` continua a produrre `hades.php_graph.v1` per progetti
  PHP e ora marca `framework: symfony` quando rileva route Symfony o
  `bin/console`.
- Route Symfony estratte da attribute PHP `#[Route(...)]` e annotation legacy
  `@Route(...)`, con class-level prefix/name combinati con method-level route.
- Controller invocabili con route class-level e metodo `__invoke` producono una
  route-handler edge esplicita.
- Gli artifact restano source-free: uri, method, name, handler, controller,
  path, linee, simboli ed edge, non codice sorgente.

Verifiche eseguite:

- Locale mirato:
  `.venv/bin/python -m pytest -q tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_symfony_php_graph_without_source tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source`
  passato: `2 passed`.
- Locale aggregato:
  `.venv/bin/python -m pytest -q tests/hermes_cli/test_hades_backend_jobs.py`
  passato: `16 passed`.
- Locale lint/compile:
  `ruff check hermes_cli/hades_backend_jobs.py tests/hermes_cli/test_hades_backend_jobs.py`
  passato con `.venv/bin/ruff`; `py_compile hermes_cli/hades_backend_jobs.py`
  passato.

Resta fuori da questa tranche:

- Database schema adapters cross-framework oltre Django models.
- Parser PHP strutturati profondi oltre alle route Symfony attribute/annotation
  e al graph Laravel regex-bounded.

## Esecuzione performance/storage controls Hades - 2026-07-07

Stato: completata la prima tranche locale P2-3 del piano "Performance, Cost
And Storage Controls".

Agent locale:

- `_upload_job_artifact` calcola un hash canonico dell'artifact dopo
  l'arricchimento con `head_commit`/`indexed_head_commit`.
- La cache vive in `hades_backend.db` `sync_state` con chiave per workspace
  binding, schema e HEAD commit; se l'artifact e' identico viene loggato
  `artifact.skipped` e non viene ricaricato al backend.
- Il summary sync include `artifacts_skipped`; la CLI mostra il totale
  artifact upload+skip, mentre lo status espone `skipped_unchanged_last_sync`.
- L'awareness locale considera gli artifact invariati come copertura presente,
  evitando falsi `project_artifact_index` missing quando la sync risparmia un
  upload duplicato.

Verifiche eseguite:

- Locale:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_sync_runner.py tests/hermes_cli/test_hades_backend_mvp_smoke.py`
  passato: `23 passed`.

Resta fuori da questa tranche:

- Indexing incrementale file-level invece di rigenerare graph/tree completi.
- Compressione payload artifact e benchmark dataset medio/grande.
- Retention/cleanup dedicata delle chiavi cache di workspace non piu' linkati.

## Esecuzione sync duration metric Hades - 2026-07-07

Stato: completata una seconda tranche locale P2-3.

Agent locale:

- `run_backend_sync` misura `duration_ms` con `time.monotonic()` e lo include in
  `last_sync_summary`.
- La metrica viene quindi salvata in `hades_backend.db`, esposta da
  `hades backend status --json` e renderizzata dalla dashboard nel pannello
  "Last sync summary".
- Lo smoke test MVP verifica che `duration_ms` sia presente e non negativo senza
  rendere fragile il valore assoluto.

Verifiche eseguite:

- Locale:
  `.venv/bin/python -m pytest -q tests/hermes_cli/test_hades_backend_mvp_smoke.py tests/hermes_cli/test_hades_backend_sync_runner.py tests/hermes_cli/test_hades_backend_cmd.py`
  passato: `46 passed`.
- Locale lint/compile:
  `ruff check hermes_cli/hades_backend_sync.py tests/hermes_cli/test_hades_backend_mvp_smoke.py`
  passato; `py_compile` sugli stessi file passato.

Resta fuori da questa tranche:

- Benchmark dataset medio/grande con soglie automatiche.
- Compressione payload artifact.
- Delta-upload backend per usare il file-level manifest come protocollo remoto.

## Esecuzione file-level artifact delta Hades - 2026-07-07

Stato: completata una terza tranche locale P2-3.

Agent locale:

- `_upload_job_artifact` deriva un manifest per-file source-free dagli artifact
  (`files`, `routes`, `symbols`, `edges`, `database.tables`,
  `columns`, `foreign_keys`).
- La cache artifact in `sync_state` salva `file_manifest` e `file_delta` con
  conteggi `added`, `changed`, `removed`, `unchanged` e primi path coinvolti.
- I log `artifact.uploaded` includono `hades_file_count` e `hades_file_delta`;
  gli skip invariati includono `hades_file_count`.
- Il contratto backend resta invariato: questa tranche rende osservabile il
  delta file-level e prepara un eventuale delta-upload remoto.

Verifiche eseguite:

- Locale mirato:
  `.venv/bin/python -m pytest -q tests/hermes_cli/test_hades_backend_sync_runner.py::test_sync_runner_records_file_level_artifact_delta tests/hermes_cli/test_hades_backend_sync_runner.py::test_sync_runner_skips_unchanged_artifact_uploads`
  passato: `2 passed`.
- Locale aggregato:
  `.venv/bin/python -m pytest -q tests/hermes_cli/test_hades_backend_sync_runner.py`
  passato: `24 passed`.
- Locale lint/compile:
  `ruff check hermes_cli/hades_backend_sync.py tests/hermes_cli/test_hades_backend_sync_runner.py`
  passato con `.venv/bin/ruff`; `py_compile hermes_cli/hades_backend_sync.py`
  passato.
- Durante il gate aggregato sono stati riallineati tre assert di status al
  comportamento governance corrente: quando manca un quality report viene
  proposta l'azione `hades backend quality-report --record`.

Resta fuori da questa tranche:

- Delta-upload backend effettivo.
- Compressione payload artifact e benchmark dataset medio/grande.

## Esecuzione compressed artifact upload Hades - 2026-07-07

Stato: completata una quarta tranche P2-3 local+backend.

Agent locale:

- `_artifact_upload_fields` serializza l'artifact JSON in forma canonica e,
  sopra 64 KB, usa `gzip+base64` solo se il risultato e' piu' piccolo del raw.
- `_upload_job_artifact` invia `artifact_encoding`,
  `artifact_compressed`, dimensioni raw/compresse e
  `artifact_uncompressed_sha256`; il campo `sha256` continua a rappresentare
  l'hash canonico usato per dedup/cache.
- Se un backend rifiuta il payload compresso, l'agent ritenta lo stesso upload
  in formato raw e logga `hades_compression_fallback_raw`.
- I log artifact includono `hades_compressed`, byte originali, byte compressi e
  delta file-level gia' introdotto nella tranche precedente.

Backend remoto:

- `ArtifactController` accetta artifact raw o compressi con
  `artifact_encoding=gzip+base64`.
- Il backend decodifica base64/gzip, verifica dimensioni e sha256 del JSON
  non compresso quando presenti, valida che il JSON sia un oggetto, e archivia
  il payload decodificato nello stesso campo JSON degli artifact raw.
- Il contratto di storage resta invariato per search e awareness: la
  compressione e' solo trasporto client/backend.

Verifiche eseguite:

- Locale mirato:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_sync_runner.py::test_sync_runner_compresses_large_artifact_uploads tests/hermes_cli/test_hades_backend_sync_runner.py::test_sync_runner_retries_raw_when_compressed_artifact_upload_is_rejected tests/hermes_cli/test_hades_backend_sync_runner.py::test_sync_runner_records_file_level_artifact_delta`
  passato: `3 passed`.
- Locale aggregato:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_sync_runner.py`
  passato: `26 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_sync.py tests/hermes_cli/test_hades_backend_sync_runner.py`
  passato; `.venv/bin/python -m py_compile hermes_cli/hades_backend_sync.py`
  passato.
- Remoto mirato:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades/HadesM5MvpCompletionTest.php --filter=compressed`
  passato: `1 passed (8 assertions)`.
- Remoto aggregato:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades tests/Feature/PluginAuthTest.php`
  passato: `60 passed (713 assertions)`.

Resta fuori da questa tranche:

- Delta-upload backend effettivo basato sul manifest per-file.
- Benchmark dataset medio/grande con soglie automatiche.
- Eventuale compressione/chunking dedicata per source slices se il volume reale
  lo richiede.

## Esecuzione Hades backend benchmark locale - 2026-07-07

Stato: completata una quinta tranche P2-3 locale.

Agent locale:

- Nuovo modulo `hermes_cli/hades_backend_benchmark.py`.
- Nuovo comando `hades backend benchmark [--json]`.
- Il benchmark genera artifact `hades.code_graph.v1` sintetici source-free per
  casi medium/large, misura hash canonico, bytes raw, bytes compressi,
  compression ratio, upload mode e durata della preparazione payload.
- Il report `hades.backend_benchmark.v1` usa soglie warning per durata locale e
  ratio compressione; non richiede backend configurato e non legge source code.

Verifiche eseguite:

- Locale mirato:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_cmd.py::test_hades_backend_benchmark_reports_compressed_large_artifact tests/hermes_cli/test_hades_backend_cmd.py::test_backend_benchmark_command_emits_json`
  passato: `2 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_benchmark.py hermes_cli/hades_backend_cmd.py tests/hermes_cli/test_hades_backend_cmd.py`
  passato; `.venv/bin/python -m py_compile hermes_cli/hades_backend_benchmark.py hermes_cli/hades_backend_cmd.py`
  passato.

Resta fuori da questa tranche:

- Delta-upload backend effettivo basato sul manifest per-file.
- Benchmark su dataset reale di progetto, non solo sintetico.
- Soglie CI produttive calibrate su hardware target.

## Esecuzione support runbook Hades - 2026-07-07

Stato: completata la prima tranche locale P2-4 del piano "Documentation,
Runbooks And Support".

Agent locale:

- Nuovo comando `hades backend support-report [--json]`.
- Il report usa lo stato locale backend ma rimuove path assoluti, non include
  raw source/job payloads/token e redige segreti probabili nei messaggi errore.
- Il payload `hades.backend_support_report.v1` conserva setup, sync, awareness,
  binding readiness, azioni consigliate e contatori necessari al supporto.
- I runbook indicano `support-report --json` come evidenza preferita per ticket,
  lasciando `status --json` al debugging locale.

Verifiche eseguite:

- Locale:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_cmd.py::test_backend_support_report_json_redacts_paths_and_secrets`
  passato.

Resta fuori da questa tranche:

- Guida end-to-end specifica per no-codebase diagnosis su un bug reale.
- Export evidence pack completo e firmato per support escalation.

## Esecuzione governance quality report Hades - 2026-07-07

Stato: completata la prima tranche locale P2-5 del piano "Governance And
Continuous Quality Review".

Agent locale:

- Nuovo modulo `hermes_cli/hades_quality_report.py`.
- Nuovo comando `hades backend quality-report`.
- Il report `hades.quality_report.v1` aggrega metriche no-codebase diagnosis e,
  quando non escluso, support status locale.
- L'action queue distingue `blocker` per regressioni causal/privacy
  (`read_file` o altri source-access tool, evidence/tool/persistence coverage
  sotto 1.0, fixture fallite) e `warning` per setup/awareness locale non pronto.
- `--skip-local-status` rende il comando CI-friendly quando si vuole valutare
  solo la fixture no-codebase.

Verifiche eseguite:

- Locale:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_quality_report.py tests/agent/test_hades_bug_diagnosis_no_codebase.py`
  passato: `6 passed`.

Resta fuori da questa tranche:

- Scheduler audit periodico.
- Dashboard metrics/action queue nativa.
- Review queue dedicata per stale facts e low-confidence diagnosis reali.

## Esecuzione governance quality dashboard Hades - 2026-07-07

Stato: completata una tranche locale/dashboard P2-5.

Agent locale:

- `hades backend quality-report --record` salva il report generato nello stato
  locale `last_quality_report`.
- `hades backend status --json` e la route dashboard status espongono
  `quality.last_report` e `quality.last_report_updated_at`.
- Se l'ultimo report registrato e' `failed`, lo status locale diventa degraded e
  aggiunge un'action per rivedere i blocker del quality report.

Dashboard locale:

- `web/src/pages/BackendPage.tsx` mostra il pannello "Governance quality" con
  status, blocker, warning, action count, timestamp ultimo report e prime azioni
  della queue.
- `web/src/lib/api.ts` tipizza `HadesBackendQualityState` e
  `HadesBackendQualityReport`.

Verifiche eseguite:

- Locale:
  `.venv/bin/python -m pytest -q tests/hermes_cli/test_hades_quality_report.py tests/hermes_cli/test_hades_backend_web_api.py`
  passato: `8 passed`.
- Locale lint/compile:
  `ruff check hermes_cli/hades_backend_cmd.py hermes_cli/hades_backend_status.py tests/hermes_cli/test_hades_quality_report.py tests/hermes_cli/test_hades_backend_web_api.py`
  passato; `py_compile` sugli stessi file passato.
- Frontend:
  `npm run --prefix web typecheck` passato.
- Frontend lint mirato:
  `npm exec eslint -- --max-warnings=0 src/pages/BackendPage.tsx src/lib/api.ts`
  passato da `web/`.

Resta fuori da questa tranche:

- Scheduler periodico nativo per invocare `quality-report --record` su cadenza.
- Review queue dedicata per stale facts e low-confidence diagnosis reali.

## Esecuzione governance quality staleness Hades - 2026-07-07

Stato: completata una tranche locale/dashboard P2-5.

Agent locale:

- `hades.quality_report.v1` include ora `generated_at`.
- `hades backend status --json` espone `quality.staleness` con
  `missing`, `stale`, `age_seconds` e `stale_after_seconds`.
- Se manca un baseline report, lo status aggiunge un'action per eseguire
  `hades backend quality-report --record`.
- Se l'ultimo report e' piu' vecchio di 7 giorni, lo status aggiunge un'action
  di refresh; un report stale ma `passed` non marca il backend come degraded.

Verifiche eseguite:

- Locale:
  `.venv/bin/python -m pytest -q tests/hermes_cli/test_hades_quality_report.py tests/hermes_cli/test_hades_backend_web_api.py`
  passato: `10 passed`.
- Locale lint/compile:
  `ruff check hermes_cli/hades_quality_report.py hermes_cli/hades_backend_status.py tests/hermes_cli/test_hades_quality_report.py tests/hermes_cli/test_hades_backend_web_api.py`
  passato con `.venv/bin/ruff`; `py_compile hermes_cli/hades_quality_report.py hermes_cli/hades_backend_status.py`
  passato.

Resta fuori da questa tranche:

- Scheduler periodico nativo per invocare `quality-report --record` su cadenza.
- Review queue dedicata per stale facts e low-confidence diagnosis reali.

## Esecuzione Laravel graph metadata Hades - 2026-07-07

Stato: completata una seconda tranche locale P0-4 del piano "Bug Root Cause
Awareness".

Agent locale:

- `populate_backend_ast` arricchisce `hades.php_graph.v1` con route
  middleware, model-to-table edges, migration tables/columns/indexes/foreign
  keys, policy mappings e riferimenti `config()`/`env()` come chiavi redatte.
- Le static call PHP risolvono gli import `use` semplici, cosi' un controller
  che chiama un service importato genera un edge verso il FQCN del service e
  non verso il namespace corrente sbagliato.
- `Schema` e `Gate` non vengono duplicati come static-call generiche quando
  sono gia' rappresentati da migration metadata o policy edges.
- Il fixture Laravel locale verifica che l'artifact resti source-free:
  `raw_source_included=false` e niente stringhe di codice come `Route::get`,
  `Schema::create` o `config('...')` nel payload indicizzato.

Verifiche eseguite:

- Locale:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source`
  passato: `1 passed`.
- Locale aggregato:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/hermes_cli/test_hades_backend_sync_runner.py tests/hermes_cli/test_hades_backend_cmd.py tests/hermes_cli/test_hades_backend_client.py tests/hermes_cli/test_hades_backend_mvp_smoke.py tests/hermes_cli/test_hades_note_quality.py tests/hermes_cli/test_hades_quality_report.py tests/agent/test_hades_backend_memory_provider.py tests/agent/test_hades_bug_diagnosis_no_codebase.py tests/test_docs_hades_mvp.py`
  passato: `128 passed`.
- Lint/compile/docs:
  `ruff check` su indexer e test Hades toccati passato; `py_compile
  hermes_cli/hades_backend_jobs.py` passato; `scripts/docs_audit.py` passato:
  `docs audit passed: 18 required docs checked`.
- Remoto read-only:
  `ssh ubuntu@162.19.229.31 'cd /home/ubuntu/dev-sandbox/backend && grep -RIn "hades.php_graph.v1\\|hades.code_graph.v1" app docs routes tests database --include="*.php" --include="*.json" | sed -n "1,120p"'`
  conferma che `ArtifactController`, awareness, graph traversal, search,
  OpenAPI e feature tests accettano gia' `hades.php_graph.v1` e
  `hades.code_graph.v1`.

Resta fuori da questa tranche:

- Parser strutturato PHP/Laravel completo con AST reale.
- Request validation, form requests, jobs/events/listeners, scheduler/commands.
- DB query runtime (`DB::`, query builder) e viste Blade.
- Graph traversal specializzato per table/config/policy oltre agli edge gia'
  persistiti.

## Esecuzione evidence pack Hades - 2026-07-07

Stato: completata una tranche P0-5 del piano "Bug Root Cause Awareness".

Backend remoto:

- Commit remoto: `2a4b282 feat: add Hades evidence packs`.
- Nuova tabella `hades_evidence_packs` con project, workspace binding, bug
  report opzionale, evidence refs, graph refs, source slice ids, payload
  bounded, sha256, redactions, retention class e head commit.
- Nuovo controller `EvidencePackController`:
  - `POST /api/hades/v1/evidence-packs`;
  - `GET /api/hades/v1/evidence-packs`.
- `HadesEvidencePolicy` rifiuta evidence pack oltre 96 KB o con segreti non
  redatti (`evidence_pack_payload_too_large`,
  `unredacted_secret_detected`).
- Project awareness espone `coverage.evidence_packs`.
- Health, capabilities e OpenAPI remoto dichiarano la nuova route.

Agent locale:

- `HadesBackendClient` espone `create_evidence_pack` e `evidence_packs`.
- Il provider memoria Hades espone i tool service-gated:
  `hades_backend_evidence_pack_search` e
  `hades_backend_evidence_pack_create`.
- La skill `hades-bug-diagnosis` ora cerca evidence pack esistenti prima di
  ricostruire evidence e salva un pack quando refs e source slice ids sono
  raccolti.
- Docs operative/backend/runbook/OpenAPI aggiornati.

Verifiche eseguite:

- Locale:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_client.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `70 passed`.
- Locale lint/docs:
  `ruff check` su client/provider/test Hades passato; `py_compile` su client e
  provider passato; `scripts/docs_audit.py` passato: `docs audit passed: 18
  required docs checked`.
- Remoto:
  `vendor/bin/pint --test ...` passato su 8 file.
- Remoto:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades tests/Feature/PluginAuthTest.php`
  passato: `51 passed`.
- Remoto route list include `POST|GET /api/hades/v1/evidence-packs`.

Resta fuori da questa tranche:

- Evidence pack builder automatico a partire da un bug report.
- UI/dashboard dedicata per leggere e revisionare pack.
- FTS/vector ranking dedicato per evidence packs.

## Esecuzione diagnosis hard gate Hades - 2026-07-07

Stato: completata una tranche P0-3/P0-6 del piano "Bug Root Cause Awareness".

Backend remoto:

- Commit remoto: `2aa534c feat: enforce Hades diagnosis freshness gate`.
- Follow-up remoto: `74bc64c feat: gate Hades diagnoses on awareness
  coverage`.
- `DiagnosisReportController` rifiuta diagnosis report `high`/`medium` quando
  `evidence_refs` e' vuoto o `freshness.status` non e' `current`.
- Dopo il follow-up, gli stessi report precisi richiedono anche
  `project-awareness/status.diagnosable_without_source=true`; graph current e
  bug evidence da soli non bastano quando manca source slice coverage.
- Nuovi codici errore:
  `diagnosis_evidence_refs_required`, `diagnosis_freshness_not_current` e
  `diagnosis_awareness_not_diagnosable`.
- I report `low`/`insufficient` restano salvabili per preservare ipotesi o
  insufficient-evidence senza claim causali precisi.

Agent locale:

- `hades_backend_diagnosis_report_create` applica lo stesso gate prima della
  chiamata backend.
- Il tool schema accetta un oggetto `awareness`; per confidence high/medium il
  provider locale richiede `awareness.diagnosable_without_source=true` oltre a
  `evidence_refs` e `freshness.status=current`.
- Skill e runbook indicano di salvare `low`/`insufficient` quando freshness o
  refs non supportano una causa precisa.

Verifiche eseguite:

- Locale:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `36 passed`.
- Locale lint/docs:
  `ruff check` su provider/test Hades passato; `py_compile` provider passato;
  `scripts/docs_audit.py` passato: `docs audit passed: 18 required docs
  checked`.
- Remoto:
  `vendor/bin/pint --test app/Http/Controllers/Hades/DiagnosisReportController.php tests/Feature/Hades/HadesM3SharedMemoryTest.php`
  passato.
- Remoto:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades tests/Feature/PluginAuthTest.php`
  passato: `52 passed` prima del follow-up; dopo il gate awareness:
  `57 passed / 666 assertions`.
- Remoto mirato dopo il follow-up:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades/HadesM3SharedMemoryTest.php tests/Feature/Hades/HadesNoCodebaseDiagnosisTest.php`
  passato: `33 passed / 447 assertions`.
- Locale provider dopo il follow-up:
  `.venv/bin/python -m pytest -q tests/agent/test_hades_backend_memory_provider.py`
  passato: `31 passed`.

Resta fuori da questa tranche:

- UI wizard che degrada automaticamente confidence quando il gate fallisce.
- Deploy-aware freshness distinta dal solo workspace/artifact HEAD.

## Esecuzione PHP graph runtime edges Hades - 2026-07-07

Stato: completata una tranche P0-4 locale del piano "Bug Root Cause Awareness".

Agent locale:

- `populate_backend_ast` continua a produrre `hades.php_graph.v1`, senza
  cambiare il contratto backend gia' accettato.
- Il grafo PHP/Laravel ora include anche:
  - FormRequest/request validation fields;
  - job dispatch;
  - event emission e event listeners;
  - Artisan command signatures;
  - scheduler command/job edges;
  - DB query-table edges;
  - Eloquent query calls.
- Gli edge restano metadata/symbol-only: non vengono salvati raw source, blocchi
  `validate`, `DB::table`, scheduler source o altre righe sorgente.

Verifiche eseguite:

- Locale mirato:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source`
  passato: `1 passed`.
- Locale aggregato:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/hermes_cli/test_hades_backend_sync_runner.py tests/agent/test_hades_bug_diagnosis_no_codebase.py`
  passato: `39 passed`.
- Locale lint/compile:
  `ruff check hermes_cli/hades_backend_jobs.py tests/hermes_cli/test_hades_backend_jobs.py`
  passato; `py_compile hermes_cli/hades_backend_jobs.py` passato.

Resta fuori da questa tranche:

- Parser PHP/Laravel AST reale invece di pattern conservativi.
- Dependency injection/container bindings, observer/broadcasting e Blade/view
  references.
- Query builder avanzato oltre a table/from/join e principali Eloquent static
  query calls.

## Esecuzione PHP graph Laravel service edges Hades - 2026-07-07

Stato: completata una tranche P0-4 locale del piano "Bug Root Cause Awareness".

Agent locale:

- `hades.php_graph.v1` aggiunge edge metadata-only per dependency injection da
  type hints, container bindings, model observers, view/Inertia refs e broadcast
  channels.
- I nuovi edge non salvano source raw: restano path, linee, simboli, view key,
  channel name e handler FQCN quando presente.
- `Order::observe(...)` non viene piu' duplicato come `static_call`: il graph
  conserva l'edge piu' causale `observed_by`.

Verifiche eseguite:

- Locale mirato:
  `.venv/bin/python -m pytest -q tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source`
  passato: `1 passed`.
- Locale aggregato:
  `.venv/bin/python -m pytest -q tests/hermes_cli/test_hades_backend_jobs.py`
  passato: `14 passed`.
- Locale sync runner:
  `.venv/bin/python -m pytest -q tests/hermes_cli/test_hades_backend_sync_runner.py tests/hermes_cli/test_hades_backend_jobs.py`
  passato: `37 passed`.

Resta fuori da questa tranche:

- Parser PHP/Laravel AST reale invece di pattern conservativi.
- Query builder avanzato oltre ai casi gia' coperti.

## Esecuzione PHP graph Blade dependency Hades - 2026-07-07

Stato: completata una tranche P0-4 locale del piano "Bug Root Cause Awareness".

Agent locale:

- `hades.php_graph.v1` aggiunge simboli metadata-only per Blade views e Blade
  components sotto `resources/views`.
- I controller che gia' producevano `view_ref` verso `view:<name>` ora possono
  essere attraversati verso layout, partial include, componenti Blade e
  componenti Livewire referenziati dai template.
- Gli edge restano source-free: vengono salvati solo `view:*`, `component:*`,
  `livewire:*`, path e linee, non direttive Blade o markup.

Verifiche eseguite:

- Locale mirato:
  `.venv/bin/python -m pytest -q tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source`
  passato: `1 passed`.
- Locale aggregato:
  `.venv/bin/python -m pytest -q tests/hermes_cli/test_hades_backend_jobs.py tests/hermes_cli/test_hades_backend_sync_runner.py`
  passato: `37 passed`.
- Locale diagnosis no-codebase:
  `.venv/bin/python -m pytest -q tests/agent/test_hades_bug_diagnosis_no_codebase.py`
  passato: `5 passed`.
- Locale lint/compile:
  `ruff check hermes_cli/hades_backend_jobs.py tests/hermes_cli/test_hades_backend_jobs.py`
  passato con `.venv/bin/ruff`; `py_compile hermes_cli/hades_backend_jobs.py`
  passato.
- Controllo remoto SSH:
  backend `fase-2` accetta gia' `hades.php_graph.v1` in ArtifactController,
  search/traversal e test Hades; non e' servita una patch Laravel.

Resta fuori da questa tranche:

- Parser PHP/Laravel AST reale invece di pattern conservativi.
- Query builder avanzato oltre ai casi gia' coperti.

## Esecuzione Hades graph search/traversal cache fallback - 2026-07-07

Stato: completata una tranche locale P1-3/P0-4 del piano "Bug Root Cause
Awareness".

Agent locale:

- `hades_backend_graph_search` continua a preferire il backend live; se il live
  fallisce, cerca nodi/edge nei graph artifact locali e restituisce `graph_ref`
  compatti con ranking strutturato.
- `hades_backend_graph_traverse` continua a preferire il backend live.
- Se il backend live fallisce ma esiste un artifact locale
  `hades.php_graph.v1` o `hades.code_graph.v1` nella memory cache o nei job
  completati, il provider esegue traversal BFS bounded direttamente sul grafo
  locale.
- Il risultato fallback e' marcato esplicitamente con
  `searched_cache_only=true`, `freshness.status=cached` e
  `freshness.index_status=local_graph_cache`; non viene presentato come
  evidenza live/current.
- Il traversal normalizza route, simboli, tabelle, view Blade, componenti,
  middleware, config/env e nodi inferiti dagli edge, mantenendo solo metadati,
  path/linee e provenance.

Verifiche eseguite:

- Locale mirato graph search:
  `.venv/bin/python -m pytest -q tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_falls_back_to_local_graph_cache`
  passato: `1 passed`.
- Locale mirato:
  `.venv/bin/python -m pytest -q tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_traverse_falls_back_to_local_graph_cache`
  passato: `1 passed`.
- Locale provider completo:
  `.venv/bin/python -m pytest -q tests/agent/test_hades_backend_memory_provider.py`
  passato: `35 passed`.
- Locale diagnosis no-codebase:
  `.venv/bin/python -m pytest -q tests/agent/test_hades_bug_diagnosis_no_codebase.py`
  passato: `5 passed`.
- Locale lint/compile:
  `ruff check plugins/memory/hades_backend/__init__.py tests/agent/test_hades_backend_memory_provider.py`
  passato con `.venv/bin/ruff`; `py_compile plugins/memory/hades_backend/__init__.py`
  passato.

Resta fuori da questa tranche:

- FTS/vector/rerank generalizzato oltre al ranking strutturato locale.
- Freshness deploy-aware distinta dal solo commit indicizzato.

## Esecuzione Hades graph search BM25 locale - 2026-07-07

Stato: completata una tranche locale P1-3.

Agent locale:

- Il fallback locale di `hades_backend_graph_search` usa ranking
  `local_bm25` sui nodi/edge costruiti da `hades.php_graph.v1` e
  `hades.code_graph.v1`.
- La tokenizzazione spezza camelCase/PascalCase, snake case, FQCN PHP,
  route/path e component names, cosi' query ambigue trovano meglio simboli e
  edge anche senza backend live.
- I match esatti su id/label/path continuano a vincere sul ranking testuale;
  BM25 viene usato come boost/rerank del fallback cache e appare nei
  `match_fields`.

Verifiche eseguite:

- Locale mirato:
  `.venv/bin/python -m pytest -q tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_falls_back_to_local_graph_cache`
  passato: `1 passed`.
- Locale provider completo:
  `.venv/bin/python -m pytest -q tests/agent/test_hades_backend_memory_provider.py`
  passato: `35 passed`.
- Locale diagnosis no-codebase:
  `.venv/bin/python -m pytest -q tests/agent/test_hades_bug_diagnosis_no_codebase.py`
  passato: `5 passed`.
- Locale lint/compile:
  `ruff check plugins/memory/hades_backend/__init__.py tests/agent/test_hades_backend_memory_provider.py`
  passato con `.venv/bin/ruff`; `py_compile plugins/memory/hades_backend/__init__.py`
  passato.

Resta fuori da questa tranche:

- FTS/vector/rerank backend per evidence testuale e query cross-domain.
- Freshness deploy-aware distinta dal solo commit indicizzato.

## Esecuzione Hades memory search ranking backend - 2026-07-07

Stato: completata una tranche backend P1-3.

Backend remoto:

- `MemorySearchController::score()` non usa piu' solo phrase + token count
  piatto.
- Il ranking ora pesa i campi (`summary`, payload, kind, source, agent key),
  conta occorrenze bounded, premia match multi-token completi e aggiunge un
  density bonus: a parita' di token, una nota breve e precisa batte una nota
  lunga e rumorosa anche se quest'ultima e' piu' recente.
- Il filtro DB resta LIKE-based per compatibilita' e assenza di migrazioni; la
  modifica e' un rerank deterministico in memoria sui candidati bounded.

Verifiche eseguite:

- Remoto mirato:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades/HadesM3SharedMemoryTest.php --filter=concise`
  passato: `1 passed (7 assertions)`.
- Remoto syntax:
  `php -l app/Http/Controllers/Hades/MemorySearchController.php`
  passato nel container app.
- Remoto aggregato:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades tests/Feature/PluginAuthTest.php`
  passato: `62 passed (736 assertions)`.

Resta fuori da questa tranche:

- FTS/vector backend reale per dataset grandi e query cross-domain.
- Benchmark dataset medio/grande con soglie automatiche.
- Rerank condiviso per bug evidence/source slices/evidence packs oltre al
  memory search controller.

## Esecuzione Hades memory kind filter - 2026-07-07

Stato: completata una tranche P1-3 locale.

Agent locale:

- `hades_backend_project_memory_search` accetta ora `kind`, ad esempio
  `resolved_bug`, per restringere il recall a memoria strutturata.
- Il provider passa `kind` al backend live solo quando valorizzato, mantenendo
  invariati prefetch e graph search.
- Il risultato live viene filtrato anche lato provider quando necessario, cosi'
  un backend vecchio che ignora `kind` non mescola note generiche in una ricerca
  strutturata.
- Il fallback cache usa lo stesso filtro `kind`.

Verifiche eseguite:

- Locale:
  `.venv/bin/python -m pytest -q tests/agent/test_hades_backend_memory_provider.py`
  passato: `31 passed`.

Resta fuori da questa tranche:

- FTS/ranking backend per text evidence e query ambigue.
- Structured filters backend per commit/symbol oltre al filtro kind locale.

## Esecuzione Hades structured memory search filters - 2026-07-07

Stato: completata una tranche P1-3 locale/backend.

Agent locale:

- `hades_backend_project_memory_search` accetta ora anche `schema`, `source`,
  `symbol` e `path`, oltre a `kind`.
- Il provider passa i filtri al backend live e applica comunque un filtro
  difensivo sul risultato, cosi' un backend non aggiornato non mescola domini o
  memory kind non richiesti.
- Il fallback cache locale applica gli stessi filtri su payload/provenance
  annidati, incluse liste come `affected_symbols`.
- Il risultato tool espone `filters` e conserva `match_fields` quando arrivano
  dal backend.

Backend remoto:

- Commit remoto `6927013 feat: add Hades structured memory search filters`.
- `GET /api/hades/v1/memory/search` accetta `kind`, `schema`, `source`, `symbol`
  e `path`.
- `MemorySearchController` applica i filtri a project memory, wiki revisions e
  artifacts; gli item ritornano `match_fields` e uno score boost per match
  strutturati.
- I graph artifacts possono essere cercati per schema/symbol/path senza dover
  dipendere solo dalla query testuale.

Verifiche eseguite:

- Locale:
  `.venv/bin/python -m pytest -q tests/agent/test_hades_backend_memory_provider.py`
  passato: `33 passed`.
- Remoto:
  `vendor/bin/pint --test app/Http/Controllers/Hades/MemorySearchController.php tests/Feature/Hades/HadesM3SharedMemoryTest.php`
  passato.
- Remoto:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades`
  passato: `52 passed / 680 assertions`.
- Remoto:
  `git diff --check` passato prima del commit.

Resta fuori da questa tranche:

- FTS/BM25 o vector rerank per testo libero ambiguo.
- Filtri espliciti per commit/versione oltre alla freshness gia' restituita.

## Esecuzione no-codebase freshness eval Hades - 2026-07-07

Stato: completata una tranche P0-7 locale del piano "Bug Root Cause Awareness".

Agent locale:

- `hades_no_codebase_eval` ora valuta anche `freshness_status` per ogni run.
- I casi high/medium falliscono se la freshness non e' `current`, anche quando
  root cause ed evidence refs sono presenti.
- La fixture canonica `tests/fixtures/hades/no_codebase_bug_cases.json` include
  expected/run freshness: i 5 casi completi sono `current`, il caso missing
  source slice resta `current`, il caso stale graph e' `stale`.
- `hades_quality_report` espone `freshness_coverage` e produce il blocker
  `repair_freshness_coverage` quando un run preciso usa evidence stale o la
  freshness non corrisponde alla fixture.
- `.github/workflows/tests.yml` ora include il job dedicato `Hades no-codebase
  quality gate`, che esegue il comando reale
  `hades backend quality-report --no-codebase-eval ... --skip-local-status
  --json` e fallisce se lo status non e' `passed`.

Verifiche eseguite:

- Locale:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_hades_bug_diagnosis_no_codebase.py tests/hermes_cli/test_hades_quality_report.py`
  passato: `8 passed`.
- Locale lint/compile:
  `ruff check hermes_cli/hades_no_codebase_eval.py hermes_cli/hades_quality_report.py tests/agent/test_hades_bug_diagnosis_no_codebase.py tests/hermes_cli/test_hades_quality_report.py`
  passato; `py_compile hermes_cli/hades_no_codebase_eval.py
  hermes_cli/hades_quality_report.py` passato.

Resta fuori da questa tranche:

- Valutazione remota end-to-end con modello/agent reale invece di solo API
  contract backend.

## Esecuzione live awareness gate provider - 2026-07-07

Stato: completata una tranche locale P0-3 sul blocco automatico dei report
precisi.

Agent locale:

- `hades_backend_diagnosis_report_create` rilegge live
  `project-awareness/status` prima di salvare report `high` o `medium`.
- Il provider usa la freshness live restituita dal backend nel report salvato.
- Se il backend live indica `freshness.status!=current` o
  `diagnosable_without_source=false`, il provider blocca il report preciso e
  restituisce actions/coverage per salvare un report `low` o `insufficient`.
- Questo impedisce al modello di bypassare il gate passando manualmente
  `freshness.status=current` nei tool args.

Verifiche eseguite:

- Locale:
  `.venv/bin/python -m pytest -q tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato.
- Locale lint/compile:
  `ruff check plugins/memory/hades_backend/__init__.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato.
- Locale:
  `git diff --check` passato.

Resta fuori da questa tranche:

- Valutazione E2E con modello/agent reale e guidance visuale dedicata nelle UI.

## Esecuzione runtime evidence graph refs - 2026-07-07

Stato: completata una tranche locale P1-1/P1-3 sulla correlazione evidence ->
graph.

Agent locale:

- `hades backend ingest-test` e `ingest-log` aggiungono `excerpt_sha256`,
  `frame_refs` e, per runtime logs, `log_refs` ai payload evidence.
- I refs restano metadata-only: path, line, query hint e source-slice hint; non
  aggiungono source content.
- `hades_backend_bug_evidence_search` espone `graph_refs` derivati da
  `payload.frame_refs` o da `payload.frames`, cosi' l'agent puo' collegare
  evidence runtime a graph/source-slice senza interpretare raw log text.

Verifiche eseguite:

- Locale:
  `.venv/bin/python -m pytest -q tests/hermes_cli/test_hades_backend_cmd.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato.
- Locale lint/compile:
  `ruff check hermes_cli/hades_backend_cmd.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_cmd.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato.
- Locale:
  `git diff --check` passato.

Resta fuori da questa tranche:

- Matching automatico semantico fra runtime log message e log-template hash; i
  refs path/line sono il primo ponte affidabile senza raw source.

## Esecuzione no-codebase trajectory discovery - 2026-07-07

Stato: completata una tranche locale P0-7 sulla discovery delle run reali.

Agent locale:

- `load_no_codebase_eval_fixture` supporta `trajectory_globs` e
  `trajectory_dirs` oltre a `trajectory_runs` espliciti.
- Il fixture id viene ricavato da `fixture_id`, `case_id`, `eval_id`, `id`,
  `metadata.*` o dal nome file.
- Questo permette di usare directory/glob di trajectory reali in quality-report
  senza trascrivere ogni file nel JSON eval.

Verifiche eseguite:

- Locale:
  `.venv/bin/python -m pytest -q tests/agent/test_hades_bug_diagnosis_no_codebase.py tests/hermes_cli/test_hades_quality_report.py tests/test_docs_hades_mvp.py`
  passato.
- Locale lint/compile:
  `ruff check hermes_cli/hades_no_codebase_eval.py tests/agent/test_hades_bug_diagnosis_no_codebase.py`
  passato; `py_compile` sugli stessi file passato.
- Locale:
  `git diff --check` passato.

Resta fuori da questa tranche:

- Esecuzione nightly con modello reale; questa tranche rende ripetibile il
  caricamento delle trajectory prodotte.

## Esecuzione PHP method-level graph edges - 2026-07-07

Stato: completata una tranche locale P0-4 sulla precisione del graph PHP.

Agent locale:

- `hades.php_graph.v1` conserva gli edge class-level esistenti e aggiunge edge
  duplicati method-level quando il contesto metodo e' riconoscibile.
- I nuovi edge coprono validation inline, static/service calls, job/event
  dispatch, query-table, Eloquent query, view refs, config/env refs e
  instantiation.
- Una traversata source-free puo' quindi seguire `route -> Controller@method ->`
  service/table/view/config/job/event senza leggere source code.

Verifiche eseguite:

- Locale:
  `.venv/bin/python -m pytest -q tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato.
- Locale lint/compile:
  `ruff check hermes_cli/hades_backend_jobs.py tests/hermes_cli/test_hades_backend_jobs.py`
  passato; `py_compile` sugli stessi file passato.
- Locale:
  `git diff --check` passato.

Resta fuori da questa tranche:

- Parser PHP AST/type-aware reale e query builder/data-flow avanzato.

## Esecuzione source-free test map graph - 2026-07-07

Stato: completata una tranche locale P2-2 sull'indicizzazione vera dei
progetti.

Agent locale:

- `populate_backend_ast` aggiunge una sezione `tests` source-free agli artifact
  `hades.php_graph.v1`, `hades.code_graph.v1` e al fallback `hades.symbols.v1`.
- La test map contiene file test riconosciuti, framework test, conteggio casi,
  nomi/linee dei casi, target candidate e refs a route/symbol/import gia'
  indicizzati.
- Il graph aggiunge edge metadata-only `test_covers_symbol`,
  `test_covers_route` e `test_imports`.
- I file test non vengono piu' mescolati nel symbol graph applicativo: restano
  separati come nodi `test:<path>`.
- Il fallback locale di `hades_backend_graph_search` costruisce nodi
  `test_file`, quindi un agent senza source code puo' cercare test rilevanti
  partendo da nome file, symbol o route indicizzati.

Verifiche eseguite:

- Locale:
  `.venv/bin/python -m pytest -q tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `65 passed`.
- Locale lint/compile:
  `ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato.
- Locale:
  `git diff --check` passato.

Resta fuori da questa tranche:

- Log map strutturata e parser AST piu' profondi; questa tranche collega gia'
  test->route/symbol/import ma non estrae stack/log assertion semantics.

## Esecuzione Python AST import/call graph - 2026-07-07

Stato: completata una tranche locale P2-2 sui parser strutturati.

Agent locale:

- `populate_backend_ast` per Python aggiunge edge source-free `imports` usando
  `ast.Import` / `ast.ImportFrom`.
- Il graph Python aggiunge edge source-free `calls` da funzioni e metodi verso
  callable chiamati, con risoluzione semplice degli alias importati.
- La fixture FastAPI/Django ora dimostra il collegamento
  `show_order -> app.services.OrderService -> service.load`, senza salvare
  corpo funzione o argomenti.
- La test map resta separata dai simboli applicativi.

Verifiche eseguite:

- Locale:
  `.venv/bin/python -m pytest -q tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `65 passed`.
- Locale lint/compile:
  `ruff check hermes_cli/hades_backend_jobs.py tests/hermes_cli/test_hades_backend_jobs.py`
  passato; `py_compile` sugli stessi file passato.
- Locale:
  `git diff --check` passato.

Resta fuori da questa tranche:

- Risoluzione type-aware/data-flow e call graph intermodulo profondo; questa
  tranche e' volutamente conservativa e metadata-only.

## Esecuzione Python log map graph - 2026-07-07

Stato: completata una tranche locale P2-2 sulla log map metadata-only.

Agent locale:

- Il graph Python include `logs.schema=hades.log_map.v1` con eventi logging
  source-free.
- Ogni evento conserva context, logger, level, path, line, lunghezza e SHA-256
  del messaggio redatto; non viene salvato il template raw.
- Il graph aggiunge edge `emits_log` dal context Python al nodo `log:<hash>`.
- Il fallback locale di `hades_backend_graph_search` indicizza nodi `log_event`
  con level/logger/hash, quindi l'agent puo' cercare punti di emissione log
  anche quando il backend live non risponde.

Verifiche eseguite:

- Locale:
  `.venv/bin/python -m pytest -q tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `66 passed`.
- Locale lint/compile:
  `ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato.
- Locale:
  `git diff --check` passato.

Resta fuori da questa tranche:

- Log map PHP e correlazione automatica con runtime log evidence; questa
  tranche prepara il nodo/edge model Python senza importare log raw in memory.

## Esecuzione TypeScript log map graph - 2026-07-07

Stato: completata una tranche locale P2-2 sulla log map metadata-only per
Node/TypeScript/JavaScript.

Agent locale:

- Il graph `hades.code_graph.v1` include `logs.schema=hades.log_map.v1` anche
  per chiamate `console.*`, `logger.*` e `log.*`.
- Ogni evento conserva context, logger, level, path, line, lunghezza e SHA-256
  del messaggio redatto; non viene salvato il template raw.
- Il graph aggiunge edge `emits_log` dal file TypeScript/JavaScript al nodo
  `log:<hash>`.

Verifiche eseguite:

- Locale:
  `.venv/bin/python -m pytest -q tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato.
- Locale lint/compile:
  `ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato.
- Locale:
  `git diff --check` passato.

Resta fuori da questa tranche:

- Correlazione automatica con runtime log evidence; questa tranche resta
  source-free e metadata-only.

## Esecuzione PHP log map graph - 2026-07-07

Stato: completata una tranche locale P2-2 sulla log map metadata-only per
PHP/Laravel.

Agent locale:

- Il graph `hades.php_graph.v1` include `logs.schema=hades.log_map.v1` per
  `Log::level(...)`, logger PSR su variabile/proprieta',
  `logger()->level(...)` e `logger(...)`.
- Ogni evento conserva context, logger, level, path, line, lunghezza e SHA-256
  del messaggio redatto; non viene salvato il template raw.
- Il graph aggiunge edge `emits_log` dal metodo PHP quando riconoscibile, o dal
  contesto classe/file in fallback, al nodo `log:<hash>`.

Verifiche eseguite:

- Locale:
  `.venv/bin/python -m pytest -q tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato.
- Locale lint/compile:
  `ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato.
- Locale:
  `git diff --check` passato.

Resta fuori da questa tranche:

- Correlazione automatica con runtime log evidence; questa tranche resta
  source-free e metadata-only.

## Esecuzione note backfill quality gate - 2026-07-07

Stato: completata una tranche locale P1-7 sulla qualita' delle note e sulla
governance del backfill.

Agent locale:

- `hades backend quality-report` include ora metriche `note_backfill` costruite
  dalla coda locale `memory_proposals`.
- Le proposal `note_backfill_candidate` pendenti o `submitted` generano action
  `review_note_backfill_candidates` e lasciano il report in `attention`.
- Proposal refused/conflicted e proposal senza evidence refs generano action
  dedicate, cosi' i raw chunk trasformabili non restano invisibili ai gate
  periodici.
- Nessun raw chunk viene promosso automaticamente: il cambiamento e' di
  osservabilita'/governance della review, non di auto-recall.

Verifiche eseguite:

- Locale:
  `.venv/bin/python -m pytest -q tests/hermes_cli/test_hades_quality_report.py tests/hermes_cli/test_hades_note_quality.py tests/test_docs_hades_mvp.py`
  passato: `24 passed`.
- Locale lint/compile:
  `ruff check hermes_cli/hades_quality_report.py hermes_cli/hades_backend_cmd.py tests/hermes_cli/test_hades_quality_report.py tests/hermes_cli/test_hades_note_quality.py`
  passato; `py_compile hermes_cli/hades_quality_report.py hermes_cli/hades_backend_cmd.py`
  passato.

Resta fuori da questa tranche:

- Trend storico remoto/lungo periodo della qualita' note; il report locale ora
  espone il dato, ma non lo aggrega ancora lato backend.

## Esecuzione guided bug intake desktop Hades - 2026-07-07

Stato: completata una tranche locale P2-1.

Agent locale / desktop:

- La logica condivisa `create_bug_intake` vive ora in
  `hermes_cli/hades_backend_actions.py` e viene riusata sia dalla dashboard
  FastAPI sia dal JSON-RPC desktop `backend.bug_intake`.
- Il desktop espone `/bug-intake` e alias `/bug` come azione locale; non passa
  dal backend slash worker e apre un overlay dedicato.
- Nuovo overlay `apps/desktop/src/app/bug-intake/`: selezione workspace binding,
  severity, title/symptom, expected/actual, reproduction steps, failing test e
  runtime log con import file bounded 64 KB, HTTP context, deploy commit,
  preview redatta locale e submit con timeout 15 secondi.
- Lo status stack Hades mostra un ingresso contestuale `Bug` quando il backend
  richiede attenzione.

Verifiche eseguite:

- Locale Python:
  `.venv/bin/python -m pytest -q tests/test_tui_gateway_server.py::test_backend_bug_intake_rpc_calls_shared_action tests/hermes_cli/test_hades_backend_web_api.py`
  passato: `6 passed`.
- Locale Python:
  `ruff check hermes_cli/hades_backend_actions.py hermes_cli/web_server.py tui_gateway/server.py tests/test_tui_gateway_server.py`
  passato; `py_compile` sugli stessi moduli passato.
- Desktop:
  `npm --prefix apps/desktop run typecheck` passato.
- Desktop:
  `npm --prefix apps/desktop run test:ui -- src/app/bug-intake/index.test.ts src/lib/desktop-slash-commands.test.ts src/app/chat/composer/status-stack/hades-backend-status.test.ts`
  passato: `3 passed / 21 tests`.
- Desktop:
  lint mirato da `apps/desktop` sui file toccati passato.

Resta fuori da questa tranche:

- Screenshot/manual smoke dell'overlay in Electron.
- File upload binary/screenshot come evidence nativa; il wizard importa solo
  testo bounded per test e log.

## Esecuzione Hades delta-upload backend e benchmark reale - 2026-07-07

Stato: completata una tranche locale/remota P2-3.

Backend remoto:

- Commit remoto `39db2a7 feat: deduplicate Hades artifact uploads`.
- Aggiunta route autenticata `GET /api/hades/v1/artifacts/lookup` per verificare
  se un artifact bounded esiste gia' per project, workspace binding, schema e
  sha256.
- `POST /api/hades/v1/artifacts` deduplica ora lo stesso hash server-side e
  restituisce l'artifact esistente con `deduplicated=true` invece di creare
  storage/search documents duplicati.
- Aggiunto indice DB non-unique
  `(project_id, workspace_binding_id, schema, sha256)` per lookup rapido senza
  rompere eventuali duplicati legacy.

Agent locale:

- `HadesBackendClient.artifact_lookup()` espone il nuovo contratto.
- `_upload_job_artifact()` continua a usare prima la cache locale; quando la
  cache locale e' vuota, chiede al backend se l'hash esiste gia'. Se esiste,
  registra `artifact.skipped`, aggiorna la cache locale con
  `backend_artifact_id` e non invia il payload artifact.
- Se il backend e' legacy o la lookup non e' disponibile, il sync degrada al
  comportamento precedente e tenta l'upload normale.
- `hades backend benchmark --workspace <path>` aggiunge casi reali read-only
  `workspace_git_tree` e `workspace_code_graph` usando gli indexer Hades
  esistenti, senza upload e senza raw source, accanto ai casi sintetici.

Verifiche eseguite:

- Locale:
  `.venv/bin/python -m pytest -q tests/hermes_cli/test_hades_backend_client.py tests/hermes_cli/test_hades_backend_sync_runner.py`
  passato: `67 passed`.
- Locale benchmark:
  `.venv/bin/python -m pytest -q tests/hermes_cli/test_hades_backend_cmd.py::test_hades_backend_benchmark_reports_real_workspace_artifacts tests/hermes_cli/test_hades_backend_cmd.py::test_hades_backend_benchmark_reports_compressed_large_artifact tests/hermes_cli/test_hades_backend_cmd.py::test_backend_benchmark_command_emits_json`
  passato: `3 passed`.
- Locale lint/compile:
  `ruff check` e `py_compile` sui moduli Hades benchmark/client/sync passati;
  `json.tool docs/hades/openapi-hades-v1.json` passato.
- Remoto:
  `vendor/bin/pint --test app/Http/Controllers/Hades/ArtifactController.php routes/api.php database/migrations/2026_07_07_000007_add_hades_agent_artifacts_lookup_index.php tests/Feature/Hades/HadesM5MvpCompletionTest.php`
  passato.
- Remoto mirato:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades/HadesM5MvpCompletionTest.php --filter=artifacts`
  passato: `3 passed / 44 assertions`.
- Remoto completo Hades + plugin auth:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades tests/Feature/PluginAuthTest.php`
  passato: `67 passed / 805 assertions`.

Resta fuori da questa tranche:

- Tuning soglie/storage dopo benchmark su dataset reali piu' grandi e continui.

## Esecuzione dashboard guided bug intake Hades - 2026-07-07

Stato: completata una tranche locale P2-1.

Agent locale:

- Nuova route dashboard `POST /api/hades/backend/bug-intake`.
- La route crea un bug report backend usando il workspace binding esplicito
  scelto dalla UI, quindi funziona anche quando il dashboard non e' avviato
  dentro la repo collegata.
- Il payload usa `hades.bug_intake.v1` e salva symptom, steps,
  expected/actual, severity, environment e agent id.
- Evidenze inline supportate: failing test, runtime log, deploy version,
  HTTP request e HTTP response.
- Failing test, log e URL passano dalla stessa redazione usata dalla CLI prima
  dell'invio al backend.
- La dashboard backend aggiunge il pannello `Bug intake` con selezione
  workspace, campi bug principali, evidenze opzionali, stato busy e toast con
  bug report id/evidence count.

Verifiche eseguite:

- Locale:
  `.venv/bin/python -m py_compile hermes_cli/web_server.py tests/hermes_cli/test_hades_backend_web_api.py`
  passato.
- Locale:
  `.venv/bin/python -m pytest -q tests/hermes_cli/test_hades_backend_web_api.py`
  passato: `3 passed`.
- Frontend:
  `npm run typecheck` in `web/` passato.
- Frontend:
  `npx eslint src/pages/BackendPage.tsx src/lib/api.ts --max-warnings=0` in
  `web/` passato.

Resta fuori da questa tranche:

- Wizard desktop.

## Esecuzione no-codebase diagnosis guide Hades - 2026-07-07

Stato: completata una tranche locale P2-4.

Docs locali:

- Nuova guida pubblica `docs/hades/no-codebase-diagnosis.md`.
- La guida copre precondizioni source-free, refresh awareness, bug intake,
  sequenza diagnosi senza source locale, quality audit, recovery blockers e
  support bundle sicuro.
- `docs/hades/README.md`, `docs/hades/launch.md` e
  `docs/hades/support-runbook.md` linkano la guida.
- `tests/test_docs_hades_mvp.py` include la nuova pagina nel set stabile delle
  docs Hades e verifica il link dal support runbook.

Verifiche eseguite:

- Locale:
  `.venv/bin/python -m pytest -q tests/test_docs_hades_mvp.py`
  passato: `6 passed`.

Resta fuori da questa tranche:

- Tutorial desktop in-app.

## Esecuzione diagnosis promotion controls Hades - 2026-07-07

Stato: completata una tranche locale P1-4.

Agent locale:

- Nuova action condivisa `promote_diagnosis_report` in
  `hermes_cli/hades_backend_actions.py`.
- Nuovo comando `hades backend promote-diagnosis <diagnosis_report_id>`.
- Nuova route dashboard `POST /api/hades/backend/promote-diagnosis`.
- Nuovo pannello dashboard `Diagnosis promotion` con diagnosis id,
  verification status, fix commit/PR, affected symbols, regression tests e
  notes.
- Le note vengono redatte lato Python prima della chiamata backend.
- Il payload usa il contratto gia' esposto dal provider tool
  `hades_backend_resolved_bug_promote`, senza aggiungere model tool al core.

Verifiche eseguite:

- Locale:
  `.venv/bin/python -m py_compile hermes_cli/hades_backend_actions.py hermes_cli/hades_backend_cmd.py hermes_cli/web_server.py tests/hermes_cli/test_hades_backend_cmd.py tests/hermes_cli/test_hades_backend_web_api.py`
  passato.
- Locale:
  `.venv/bin/python -m ruff check hermes_cli/hades_backend_actions.py hermes_cli/hades_backend_cmd.py hermes_cli/web_server.py tests/hermes_cli/test_hades_backend_cmd.py tests/hermes_cli/test_hades_backend_web_api.py`
  passato.
- Locale:
  `.venv/bin/python -m pytest -q tests/hermes_cli/test_hades_backend_cmd.py tests/hermes_cli/test_hades_backend_web_api.py`
  passato: `33 passed`.
- Frontend:
  `npm run typecheck` in `web/` passato.
- Frontend:
  `npx eslint src/pages/BackendPage.tsx src/lib/api.ts --max-warnings=0` in
  `web/` passato.

Resta fuori da questa tranche:

- Eventuale contratto backend dedicato per reindex selettivo per artifact/slice.
- Lista dashboard dei diagnosis report disponibili.

## Esecuzione dashboard sync-now controls Hades - 2026-07-07

Stato: completata una tranche locale P1-4.

Agent locale:

- Nuova route dashboard `POST /api/hades/backend/sync`.
- La route invoca `run_backend_sync(quiet=True)` e restituisce `ok/status` piu'
  summary sync.
- Il pannello dashboard `Policy controls` aggiunge `Sync now`, vicino a source
  slices, bug evidence e policy blockers.
- Il pulsante riusa `runReviewAction`, quindi mostra busy state, toast e refresh
  dello status come le altre action.
- Questa copre il controllo manuale di reindex/refresh usando il sync runner
  esistente, senza inventare un nuovo contratto backend.

Verifiche eseguite:

- Locale:
  `.venv/bin/python -m py_compile hermes_cli/web_server.py tests/hermes_cli/test_hades_backend_web_api.py`
  passato.
- Locale:
  `.venv/bin/python -m ruff check hermes_cli/web_server.py tests/hermes_cli/test_hades_backend_web_api.py`
  passato.
- Locale:
  `.venv/bin/python -m pytest -q tests/hermes_cli/test_hades_backend_web_api.py`
  passato: `5 passed`.
- Frontend:
  `npm run typecheck` in `web/` passato.
- Frontend:
  `npx eslint src/pages/BackendPage.tsx src/lib/api.ts --max-warnings=0` in
  `web/` passato.

Resta fuori da questa tranche:

- Eventuale contratto backend dedicato per reindex selettivo per artifact/slice.

## Esecuzione scheduled quality audit Hades - 2026-07-07

Stato: completata una tranche locale P2-5.

Agent locale:

- Nuovo comando `hades backend schedule-quality`.
- Il comando scrive `HERMES_HOME/scripts/hades_backend_quality_report.py`.
- Lo script esegue `hades backend quality-report --record --json` tramite
  `hades_backend_command`, quindi registra lo snapshot locale e aggiorna la
  history senza passare da un prompt modello.
- Il comando crea o aggiorna in modo idempotente un job cron no-agent con nome
  stabile `Hades backend quality report`.
- Opzioni: `--schedule`, `--name`, `--deliver`, `--no-codebase-eval`, `--json`.
- Se il quality report fallisce, il processo cron fallisce: la regressione resta
  visibile anche nel sistema cron, oltre che nello status Hades.

Verifiche eseguite:

- Locale:
  `.venv/bin/python -m py_compile hermes_cli/hades_backend_cmd.py tests/hermes_cli/test_hades_backend_cmd.py`
  passato.
- Locale:
  `.venv/bin/python -m ruff check hermes_cli/hades_backend_cmd.py tests/hermes_cli/test_hades_backend_cmd.py`
  passato.
- Locale:
  `.venv/bin/python -m pytest -q tests/hermes_cli/test_hades_backend_cmd.py`
  passato: `28 passed`.

Resta fuori da questa tranche:

- Scheduler/reporting remoto centralizzato.
- Trend lunghi in dashboard.

## Esecuzione dashboard bug-intake file upload Hades - 2026-07-07

Stato: completata una tranche locale P2-1.

Agent locale:

- Il pannello dashboard `Bug intake` accetta file per failing test output e
  runtime log.
- La lettura avviene lato browser, con limite `64_000` byte per file.
- I file piu' grandi vengono caricati nel form come testo troncato con marker
  `... [truncated]`.
- Il testo caricato resta modificabile prima del submit e passa dalla privacy
  preview locale gia' presente.
- Il submit continua a usare `/api/hades/backend/bug-intake`, quindi la
  redazione backend e i test FastAPI esistenti restano applicati.

Verifiche eseguite:

- Frontend:
  `npm run typecheck` in `web/` passato.
- Frontend:
  `npx eslint src/pages/BackendPage.tsx --max-warnings=0` in `web/` passato.

Resta fuori da questa tranche:

- Wizard desktop.

## Esecuzione dashboard bug-intake privacy preview Hades - 2026-07-07

Stato: completata una tranche locale P2-1.

Agent locale:

- Il pannello dashboard `Bug intake` mostra una `Privacy preview` quando sono
  compilati failing test, runtime log o request URL.
- La preview usa pattern equivalenti al client backend locale per mascherare
  chiavi `sk-*`, bearer token, `token=`/`token:` e `api_key=`/`api-key=`.
- La UI mostra il conteggio redazioni e il testo mascherato prima del submit.
- La redazione server resta autoritativa: la route `/api/hades/backend/bug-intake`
  continua a redigere lato Python prima di inviare evidenze al backend.

Verifiche eseguite:

- Frontend:
  `npm run typecheck` in `web/` passato.
- Frontend:
  `npx eslint src/pages/BackendPage.tsx --max-warnings=0` in `web/` passato.

Resta fuori da questa tranche:

- Wizard desktop.

## Esecuzione timeout live Hades agent - 2026-07-07

Stato: completata una tranche locale P2-3 sui timeout del provider Hades usato
nel loop agent.

Modifiche locali:

- Lookup/search/status live Hades (`project_memory_search`,
  `bug_evidence_search`, `graph_traverse`, `evidence_pack_search`,
  `project_awareness_status`) passano da 1.0s a 0.75s.
- `source_slice_fetch` passa da 1.5s a 1.25s.
- I write live (`evidence_pack_create`, `diagnosis_report_create`,
  `resolved_bug_promote`) restano a 2.0s per non perdere persistenza utile.

Verifiche eseguite:

- Locale: `.venv/bin/python -m pytest -q tests/agent/test_hades_backend_memory_provider.py`
  passato: `35 passed`.
- Locale lint/compile:
  `ruff check plugins/memory/hades_backend/__init__.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato.

## Esecuzione ranking verified note fact - 2026-07-07

Stato: completata una tranche locale P1-7 sulla qualita' delle note e sul
ranking dopo backfill.

Modifiche locali:

- Il provider Hades locale assegna un boost a `kind=verified_note_fact`.
- I raw chunk ricevono una penalita' leggera quando l'utente li include
  esplicitamente con `include_raw_chunks=true`.
- Il raw chunk resta consultabile, ma un fatto verificato e promosso dal
  backfill ranka sopra il chunk rumoroso originario.

Verifiche eseguite:

- Locale:
  `.venv/bin/python -m pytest -q tests/agent/test_hades_backend_memory_provider.py`
  passato: `36 passed`.
- Locale lint/compile:
  `ruff check plugins/memory/hades_backend/__init__.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato.

## Esecuzione dashboard policy controls - 2026-07-07

Stato: completata una tranche locale P1-4 sulla dashboard backend.

Modifiche locali:

- Nuovo pannello `Policy controls` in `web/src/pages/BackendPage.tsx`.
- Mostra coverage source slices e bug evidence per binding.
- Mostra policy blockers derivati da missing evidence/source/policy.
- Porta in primo piano i backend jobs di source/evidence/artifact/indexing e
  riusa le azioni dashboard approve/refuse gia' esistenti.

Verifiche eseguite:

- Web typecheck: `npm run typecheck` in `web/` passato.
- Web lint mirato: `npx eslint src/pages/BackendPage.tsx --max-warnings=0`
  passato.
- `npm run lint -- --max-warnings=0 src/pages/BackendPage.tsx` fallisce
  perche' lo script linta tutto `web/` e intercetta errori preesistenti in file
  non toccati; il file modificato passa con ESLint diretto.

## Esecuzione no-codebase tool order gate Hades - 2026-07-07

Stato: completata tranche locale P0-6/P0-7/P1-5.

Agent locale:

- `hades_no_codebase_eval` espone ora `tool_order_coverage`.
- La suite accetta tool extra, ma richiede che i `required_tool_calls` della
  fixture compaiano come sottosequenza ordinata nel run di diagnosi.
- Un run che usa tutti gli strumenti corretti ma inverte il workflow fallisce
  con `required Hades tool calls out of order`, mantenendo `tool_coverage=1.0`
  e abbassando solo `tool_order_coverage`.
- `hades_quality_report` propaga `tool_order_coverage` e genera il blocker
  `repair_hades_tool_order`.
- Il piano production-readiness chiarisce che presence e order sono gate
  distinti per la diagnosi source-free.

Verifiche eseguite:

- Locale:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_hades_bug_diagnosis_no_codebase.py tests/hermes_cli/test_hades_quality_report.py`
  passato: `15 passed`.
- Locale fixture:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m json.tool tests/fixtures/hades/no_codebase_bug_cases.json`
  passato.

Resta fuori da questa tranche:

- Valutazione remota end-to-end con modello/agent reale invece di fixture
  strutturata.

## Esecuzione no-codebase agent loop test Hades - 2026-07-07

Stato: completata tranche locale P0-6/P0-7.

Agent locale:

- `tests/run_agent/test_run_agent.py` copre ora un percorso `run_conversation`
  completo con modello finto e dispatcher tool finto.
- Il test espone al modello solo la toolchain Hades no-codebase:
  `hades_backend_project_awareness_status`,
  `hades_backend_bug_evidence_search`, `hades_backend_graph_search`,
  `hades_backend_source_slice_fetch` e
  `hades_backend_diagnosis_report_create`.
- Il modello finto chiama i tool in ordine e il test verifica che il dispatcher
  li riceva nella stessa sequenza prima della risposta finale.
- Il payload tool inviato al modello non include tool filesystem/shell come
  `read_file`, `rg`, `terminal` o `exec_command`.

Verifiche eseguite:

- Locale mirato:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/run_agent/test_run_agent.py::TestExecuteToolCalls::test_no_codebase_hades_diagnosis_runs_ordered_tool_chain`
  passato: `1 passed`.
- Locale allargato:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/run_agent/test_run_agent.py::TestExecuteToolCalls::test_no_codebase_hades_diagnosis_runs_ordered_tool_chain tests/agent/test_hades_bug_diagnosis_no_codebase.py tests/hermes_cli/test_hades_quality_report.py`
  passato: `16 passed`.
- Locale lint/compile:
  `ruff check tests/run_agent/test_run_agent.py tests/agent/test_hades_bug_diagnosis_no_codebase.py tests/hermes_cli/test_hades_quality_report.py hermes_cli/hades_no_codebase_eval.py hermes_cli/hades_quality_report.py`
  passato; `py_compile tests/run_agent/test_run_agent.py` passato.

Resta fuori da questa tranche:

- Provider reale/LLM reale in no-codebase mode, perche' richiede credenziali,
  costi e variabilita' esterna; il loop Hermes, i tool esposti e la sequenza
  di dispatch sono pero' coperti localmente.

## Esecuzione Hades quality history and drilldown - 2026-07-07

Stato: completata tranche locale/dashboard P1-5.

Agent locale e dashboard:

- `hades backend quality-report --record` salva ancora `last_quality_report`,
  ma mantiene anche `quality_report_history` in `sync_state`.
- Lo storico e' bounded agli ultimi 10 snapshot e conserva status, summary e
  action queue troncata.
- `hades backend status --json` espone `quality.history` con `entries`,
  `by_status`, `latest_failure` e action ids.
- La dashboard backend mostra nel pannello `Governance quality` i recent report,
  conteggi per stato e latest failure/action ids.

Verifiche eseguite:

- Locale:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_quality_report.py tests/run_agent/test_run_agent.py::TestExecuteToolCalls::test_no_codebase_hades_diagnosis_runs_ordered_tool_chain`
  passato: `11 passed`.
- Locale lint/compile:
  `ruff check hermes_cli/hades_backend_cmd.py hermes_cli/hades_backend_status.py tests/hermes_cli/test_hades_quality_report.py`
  passato; `py_compile` sugli stessi file passato.
- Frontend:
  `npm run --prefix web typecheck` passato.

Resta fuori da questa tranche:

- Storico remoto/team-wide e trend di lungo periodo; questa tranche rende
  visibile lo storico locale registrato sul device.

## Esecuzione Hades artifact search documents backend - 2026-07-07

Stato: completata tranche remota P1-3.

Backend remoto:

- Commit remoto `fa67a91 feat: index Hades artifacts for search`.
- Nuova migration `2026_07_07_000005_create_hades_search_documents_table.php`.
- Nuova tabella `hades_search_documents` con source table/id, domain, kind,
  schema, title, body, metadata e checksum.
- `ArtifactController` indicizza ogni artifact Hades al momento dell'upload,
  inclusi artifact compressi dopo decompressione/verifica.
- `MemorySearchController` usa gli indexed artifact ids come prefiltro per
  search artifact quando disponibili, mantenendo fallback compatibile sugli
  artifact legacy non indicizzati.
- Feature test M5 estesi per verificare che graph/code artifact upload generi
  documenti cercabili contenenti simboli come `OrderController`, `OrdersPage` e
  `OrderComponent300`.

Verifiche eseguite:

- Remoto mirato:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades/HadesM5MvpCompletionTest.php`
  passato: `6 passed / 95 assertions`.
- Remoto completo Hades + plugin auth:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades tests/Feature/PluginAuthTest.php`
  passato: `63 passed / 758 assertions`.
- Remoto formatter:
  `vendor/bin/pint --test app/Http/Controllers/Hades/ArtifactController.php app/Http/Controllers/Hades/MemorySearchController.php tests/Feature/Hades/HadesM5MvpCompletionTest.php database/migrations/2026_07_07_000005_create_hades_search_documents_table.php`
  passato nel container app.
- Remoto:
  `git diff --check` passato prima del commit.
- Runtime dev:
  `php artisan migrate --force` applicato; health pubblico
  `https://home-sweet-home.cloud/api/hades/v1/health` ha risposto HTTP 200.

Resta fuori da questa tranche:

- FTS/vector backend vero per memory/wiki/evidence e backfill degli artifact
  legacy gia' salvati prima della migration.

## Esecuzione Hades materialized search documents backend - 2026-07-07

Stato: completata tranche remota P1-3.

Backend remoto:

- Commit remoto `b9a2a2c feat: materialize Hades search documents`.
- Nuovo servizio `App\Services\Hades\HadesSearchDocumentIndexer`.
- `hades_search_documents` non indicizza piu' solo artifact: ora indicizza
  project memory, wiki revisions, bug evidence, source slices ed evidence packs.
- I documenti di project memory sono project-level
  (`workspace_binding_id=null`) per restare portabili tra device; il workspace
  binding resta nel payload/provenance della memoria.
- `MemorySearchController` usa i documenti indicizzati come candidate set per
  memory/wiki/artifact e conserva fallback LIKE quando non esistono documenti
  indicizzati per record legacy.
- `BugEvidenceController`, `SourceSliceController` ed `EvidencePackController`
  indicizzano al momento della scrittura e usano gli indexed source ids come
  prefiltro nelle rispettive search.
- `WikiRevisionService`, `MemoryProposalController`,
  `DiagnosisReportController` e la review dashboard Hades indicizzano le
  revisioni/wiki facts/memorie risolte appena vengono create.

Verifiche eseguite:

- Remoto mirato materialized search:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades/HadesM3SharedMemoryTest.php --filter=materialized`
  passato: `2 passed / 13 assertions`.
- Remoto no-codebase:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades/HadesNoCodebaseDiagnosisTest.php`
  passato: `3 passed / 66 assertions`.
- Remoto M3 completo:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades/HadesM3SharedMemoryTest.php`
  passato: `36 passed / 459 assertions`.
- Remoto completo Hades + plugin auth:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades tests/Feature/PluginAuthTest.php`
  passato: `65 passed / 775 assertions`.
- Remoto formatter:
  `vendor/bin/pint --test` sui 12 file modificati passato.
- Remoto:
  `git diff --check` passato prima del commit.
- Runtime dev:
  health pubblico `https://home-sweet-home.cloud/api/hades/v1/health` ha
  risposto HTTP 200 dopo il commit.

Resta fuori da questa tranche:

- FTS/vector backend vero sopra `hades_search_documents`.
- Scheduler automatico per reindex periodico, se vorremo renderlo operativo
  senza comando manuale.
- Ranking derivato direttamente dai documenti indicizzati invece del ranking
  finale ancora calcolato sul record sorgente restituito.

## Esecuzione Hades search documents backfill backend - 2026-07-07

Stato: completata tranche remota P1-3.

Backend remoto:

- Commit remoto `296f9a9 feat: backfill Hades search documents`.
- Nuovo comando Artisan `hades:search-documents-reindex`.
- Opzioni supportate: `--project`, `--workspace-binding`, `--domain`,
  `--limit`, `--dry-run`, `--json`.
- Il comando usa `App\Services\Hades\HadesSearchDocumentIndexer` per
  reindicizzare project memory, wiki revisions, artifacts, bug evidence,
  source slices ed evidence packs.
- Il backfill e' idempotente sul vincolo unico `(source_table, source_id)` di
  `hades_search_documents`.
- I filtri workspace si applicano alle sorgenti workspace-scoped; project
  memory e wiki restano portabili a livello progetto.

Runtime dev:

- Dry-run:
  `php artisan hades:search-documents-reindex --limit=100000 --dry-run --json`
  ha prodotto `1358` sorgenti scansionate/indicizzabili:
  `memory=1355`, `wiki=3`, `artifacts=0`, `bug_evidence=0`,
  `source_slices=0`, `evidence_packs=0`.
- Backfill reale:
  `php artisan hades:search-documents-reindex --limit=100000 --json` ha
  completato gli stessi `1358` documenti sul runtime dev.

Verifiche eseguite:

- Comando registrato:
  `php artisan list --raw | grep hades:search-documents-reindex`.
- Remoto lint/syntax:
  `php -l` su comando, `bootstrap/app.php` e test M3 passato.
- Remoto formatter:
  `vendor/bin/pint --test app/Console/Commands/Hades/ReindexSearchDocumentsCommand.php bootstrap/app.php tests/Feature/Hades/HadesM3SharedMemoryTest.php app/Services/Hades/HadesSearchDocumentIndexer.php`
  passato.
- Remoto completo Hades + plugin auth:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades tests/Feature/PluginAuthTest.php`
  passato: `66 passed / 786 assertions`.
- Remoto:
  `git diff --check` passato prima del commit.
- Public health:
  `https://home-sweet-home.cloud/api/hades/v1/health` ha risposto HTTP 200.

Resta fuori da questa tranche:

- FTS/vector backend vero sopra `hades_search_documents`.
- Scheduler/cron per reindex periodico, se serve.

## Esecuzione Hades search document scoring backend - 2026-07-07

Stato: completata tranche remota P1-3.

Backend remoto:

- Commit remoto `d65492a feat: score Hades search documents`.
- `HadesSearchDocumentIndexer` espone ora `matchingSourceScores()` oltre a
  `matchingSourceIds()`.
- Memory/wiki/artifacts/bug evidence/source slices/evidence packs usano il
  punteggio del documento indicizzato come base di ranking quando disponibile.
- Il vecchio scoring sui record sorgente resta fallback e complemento: se il
  match esiste solo in `hades_search_documents`, il risultato non resta piu'
  con score zero o basso.
- I test materialized memory/wiki ora verificano che i candidati trovati solo
  nel documento indicizzato abbiano score positivo.

Verifiche eseguite:

- Remoto syntax:
  `php -l` su `HadesSearchDocumentIndexer`, controller search Hades toccati e
  `HadesM3SharedMemoryTest.php` passato.
- Remoto formatter:
  `vendor/bin/pint --test app/Services/Hades/HadesSearchDocumentIndexer.php app/Http/Controllers/Hades/MemorySearchController.php app/Http/Controllers/Hades/BugEvidenceController.php app/Http/Controllers/Hades/SourceSliceController.php app/Http/Controllers/Hades/EvidencePackController.php tests/Feature/Hades/HadesM3SharedMemoryTest.php`
  passato.
- Remoto mirato materialized:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades/HadesM3SharedMemoryTest.php --filter=materialized`
  passato: `3 passed / 26 assertions`.
- Remoto no-codebase:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades/HadesNoCodebaseDiagnosisTest.php`
  passato: `3 passed / 66 assertions`.
- Remoto completo Hades + plugin auth:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades tests/Feature/PluginAuthTest.php`
  passato: `66 passed / 788 assertions`.
- Remoto:
  `git diff --check` passato prima del commit.
- Public health:
  `https://home-sweet-home.cloud/api/hades/v1/health` ha risposto HTTP 200.

Resta fuori da questa tranche:

- Vector search opzionale sopra `hades_search_documents`; FULLTEXT e scheduler
  sono stati completati nelle tranche successive.

## Esecuzione Hades search documents FULLTEXT backend - 2026-07-07

Stato: completata tranche remota P1-3.

Backend remoto:

- Commit remoto `13e5abf feat: add Hades search fulltext index`.
- Nuova migration
  `2026_07_07_000006_add_hades_search_documents_fulltext_index.php`.
- Su driver MySQL, la migration aggiunge FULLTEXT
  `hades_search_documents_fulltext_idx` su `title`, `body` e `source_schema`.
- `HadesSearchDocumentIndexer::matchingSourceScores()` usa
  `MATCH(title, body, source_schema) AGAINST (... IN BOOLEAN MODE)` quando il
  driver supporta FULLTEXT.
- SQLite/test e ambienti senza indice restano compatibili: il servizio conserva
  fallback LIKE e, se il percorso FULLTEXT solleva `QueryException`, ripiega al
  percorso LIKE.
- Runtime dev migrato con `php artisan migrate --force`.

Verifiche eseguite:

- Runtime migration:
  `2026_07_07_000006_add_hades_search_documents_fulltext_index` applicata con
  esito `DONE`.
- Remoto syntax:
  `php -l app/Services/Hades/HadesSearchDocumentIndexer.php` e `php -l` sulla
  migration passati.
- Remoto formatter:
  `vendor/bin/pint --test app/Services/Hades/HadesSearchDocumentIndexer.php database/migrations/2026_07_07_000006_add_hades_search_documents_fulltext_index.php`
  passato.
- Remoto mirato materialized:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades/HadesM3SharedMemoryTest.php --filter=materialized`
  passato: `3 passed / 26 assertions`.
- Remoto no-codebase:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades/HadesNoCodebaseDiagnosisTest.php`
  passato: `3 passed / 66 assertions`.
- Remoto completo Hades + plugin auth:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades tests/Feature/PluginAuthTest.php`
  passato: `66 passed / 788 assertions`.
- Remoto:
  `git diff --check` passato prima del commit.
- Public health:
  `https://home-sweet-home.cloud/api/hades/v1/health` ha risposto HTTP 200.

Resta fuori da questa tranche:

- Vector search opzionale; non e' richiesto per claim precisi finche' FTS,
  graph, source slices e evidence refs sono disponibili.

## Esecuzione Hades scheduled search reindex backend - 2026-07-07

Stato: completata tranche remota P1-3.

Backend remoto:

- Commit remoto `bf5647d feat: schedule Hades search reindex`.
- `bootstrap/app.php` pianifica
  `php artisan hades:search-documents-reindex --limit=100000` ogni giorno alle
  `03:45`.
- La schedule usa `withoutOverlapping()` per evitare reindex concorrenti.

Verifiche eseguite:

- Remoto syntax/formatter:
  `php -l bootstrap/app.php` e `vendor/bin/pint --test bootstrap/app.php`
  passati.
- Remoto schedule:
  `php artisan schedule:list | grep hades:search-documents-reindex` mostra
  `45 3 * * * php artisan hades:search-documents-reindex --limit=100000`.
- Remoto:
  `git diff --check` passato prima del commit.

Resta fuori da questa tranche:

- Eventuali metriche di durata storica del reindex schedulato; il comando gia'
  emette JSON per run manuali e output standard per schedule logs.

## Esecuzione Hades agent timeout budgets - 2026-07-07

Stato: completata una tranche locale P0-6.

Agent locale:

- Il provider `hades_backend` distingue i timeout live per classe di operazione:
  lookup/status/search/traversal a 1 secondo, source-slice fetch a 1.5 secondi,
  write/create/promote a 2 secondi.
- Restano live-only e senza fallback cache autorevole i tool di bug evidence,
  source slice, evidence pack, diagnosis report, resolved bug e project
  awareness. `graph traversal` ha ora un fallback locale separato e marcato
  come cached.
- Il comportamento desiderato in caso di timeout e' degradare esplicitamente a
  backend unavailable/unmapped invece di bloccare il loop agent o presentare
  memoria generica come evidenza corrente.

Verifiche eseguite:

- Locale:
  `.venv/bin/python -m pytest -q tests/agent/test_hades_backend_memory_provider.py`
  passato: `31 passed`.

Resta fuori da questa tranche:

- Valutazione remota end-to-end con modello/agent reale invece di solo API
  contract backend.

## Esecuzione multi-device identity recovery Hades - 2026-07-07

Stato: completata una tranche locale P1-6.

Agent locale:

- `hades backend status --json` mantiene la separazione `identity` fra
  personal memory locale, project memory portabile e workspace binding locale.
- Nuovo blocco `identity.login_recovery`:
  `can_use_project_memory_without_old_device`, `current_workspace_mapped`,
  `source_free_diagnosis_ready`, `requires_workspace_binding_for_indexing` e
  `recommended_next_action`.
- `identity.workspace_binding` espone anche `current_status` e
  `current_source_free_ready`.
- L'output testuale di `hades backend status` stampa `Next identity step`, cosi'
  un device nuovo o un workspace non pronto mostra direttamente link/sync/evidence
  come prossimo passo.
- La dashboard `web/src/pages/BackendPage.tsx` mostra lo stesso recovery flow
  nel pannello `Identity recovery`: project memory portability, workspace
  corrente, source-free diagnosis readiness e next action.

Verifiche eseguite:

- Locale:
  `.venv/bin/python -m pytest -q tests/hermes_cli/test_hades_backend_sync_runner.py tests/hermes_cli/test_hades_backend_cmd.py`
  passato: `45 passed`.
- Frontend:
  `npm run --prefix web typecheck` passato.
- Locale lint/compile:
  `ruff check hermes_cli/hades_backend_status.py hermes_cli/hades_backend_cmd.py tests/hermes_cli/test_hades_backend_sync_runner.py tests/hermes_cli/test_hades_backend_cmd.py`
  passato; `py_compile` sugli stessi moduli passato.

Resta fuori da questa tranche:

- Onboarding remoto/dashboard per generare e visualizzare il recovery flow senza
  CLI.

## Esecuzione no-codebase awareness quality gate Hades - 2026-07-07

Stato: completata una tranche locale P0-6/P1-5/P2-5.

Agent locale:

- `hades_no_codebase_eval` legge ora `expected_diagnosable_without_source` dalle
  fixture e `diagnosable_without_source` dai run.
- La suite produce la metrica `awareness_coverage` e fallisce un run
  high/medium quando l'awareness non prova
  `diagnosable_without_source=true`, anche se root cause, evidence refs e
  freshness risultano corretti.
- `hades_quality_report` propaga `awareness_coverage` e genera il blocker
  `repair_awareness_coverage`.
- La fixture canonica dichiara source-free awareness true per i 5 casi completi
  e false per missing source slice / stale graph.
- La skill `hades-bug-diagnosis` allinea i guardrail: niente salvataggio
  high/medium senza `freshness.status=current`, `evidence_refs` e
  `awareness.diagnosable_without_source=true`.

Verifiche eseguite:

- Locale:
  `.venv/bin/python -m pytest -q tests/agent/test_hades_bug_diagnosis_no_codebase.py tests/hermes_cli/test_hades_quality_report.py`
  passato: `10 passed`.
- Locale lint/compile:
  `ruff check hermes_cli/hades_no_codebase_eval.py hermes_cli/hades_quality_report.py tests/agent/test_hades_bug_diagnosis_no_codebase.py tests/hermes_cli/test_hades_quality_report.py`
  passato; `py_compile` sugli stessi file passato.
- Locale fixture:
  `.venv/bin/python -m json.tool tests/fixtures/hades/no_codebase_bug_cases.json`
  passato.

Resta fuori da questa tranche:

- Valutazione remota end-to-end con modello/agent reale invece di solo API
  contract backend.

## Esecuzione no-codebase evaluator namespaced tools - 2026-07-07

Stato: completata una tranche locale P0-7/P0-6.

Agent locale:

- `hermes_cli/hades_no_codebase_eval.py` estrae ora tool call anche da shape
  realistiche di traiettoria: `function.name` e `recipient_name`, oltre a
  `name`, `tool` e `tool_name`.
- Il gate no-codebase rileva source/shell tool vietati anche se namespaced o
  MCP-style, per esempio `functions.exec_command`,
  `mcp__filesystem__read_file` e `terminal.run`.
- Questo riduce un falso negativo critico: un eval poteva bloccare `read_file`
  bare ma non un tool equivalente registrato con namespace.

Verifiche eseguite:

- Locale:
  `.venv/bin/python -m pytest -q tests/agent/test_hades_bug_diagnosis_no_codebase.py`
  passato: `7 passed`.
- Locale lint/compile:
  `ruff check hermes_cli/hades_no_codebase_eval.py tests/agent/test_hades_bug_diagnosis_no_codebase.py`
  passato; `py_compile` sugli stessi file passato.

Resta fuori da questa tranche:

- Collegamento automatico dal database sessioni al file eval; i trajectory run
  reali sono supportati nella fixture, ma vanno ancora referenziati nel JSON.

## Esecuzione no-codebase trajectory-runs quality gate - 2026-07-07

Stato: completata una tranche locale P0-7/P0-6.

Agent locale:

- `load_no_codebase_eval_fixture` supporta ora `trajectory_runs` oltre a
  `runs`, con path relativo al file eval verso `.json` o `.jsonl` di trajectory
  salvate.
- Il loader normalizza ShareGPT `<tool_call>` e shape OpenAI `tool_calls` /
  `function_call`, estrae il JSON finale della diagnosi e ricava confidence,
  root cause, evidence refs, freshness, awareness e persistence.
- `hades backend quality-report --no-codebase-eval <file>` puo' quindi
  valutare run agent reali senza trascrivere a mano ogni tool call.

Verifiche eseguite:

- Locale:
  `.venv/bin/python -m pytest -q tests/agent/test_hades_bug_diagnosis_no_codebase.py tests/hermes_cli/test_hades_quality_report.py`
  passato: `19 passed`.
- Locale lint/compile:
  `ruff check hermes_cli/hades_no_codebase_eval.py tests/agent/test_hades_bug_diagnosis_no_codebase.py tests/hermes_cli/test_hades_quality_report.py`
  passato; `py_compile` sugli stessi file passato.

Resta fuori da questa tranche:

- Discovery automatica dei file trajectory da session id o cartella; il gate
  accetta path espliciti nel JSON eval per mantenere ripetibilita' e scope
  chiaro.

## Esecuzione no-codebase feature test backend - 2026-07-07

Stato: completate prime tranche remote P0-7.

Backend remoto:

- Commit remoto `62691ce test: cover Hades no-codebase diagnosis path`.
- Follow-up remoto `74bc64c feat: gate Hades diagnoses on awareness coverage`.
- Follow-up remoto `aead8bc test: cover stale no-codebase diagnosis`.
- Nuovo test `tests/Feature/Hades/HadesNoCodebaseDiagnosisTest.php`.
- Il test usa solo API Hades e fixture payload, senza leggere filesystem source:
  registra agent, lega workspace, carica `hades.php_graph.v1`, crea bug report,
  stack-trace evidence, source slice bounded, evidence pack, verifica
  `project-awareness/status`, `graph/traverse`, `source-slices`,
  `evidence-packs` e salva un diagnosis report `high/final` con freshness
  current ed evidence refs.
- Il follow-up aggiunge il caso negativo: graph current e bug evidence senza
  source slice coverage producono `diagnosable_without_source=false`; il backend
  rifiuta il report `high/final` con `diagnosis_awareness_not_diagnosable`, ma
  accetta un report `insufficient`.
- Il secondo follow-up aggiunge il caso stale: graph artifact e source slice con
  head commit vecchio producono `freshness.status=stale`,
  `diagnosable_without_source=false`, blocco del report `high/final` con
  `diagnosis_freshness_not_current`, e salvataggio consentito di un report
  `insufficient`.

Verifiche eseguite:

- Remoto:
  `vendor/bin/pint --test tests/Feature/Hades/HadesNoCodebaseDiagnosisTest.php`
  passato.
- Remoto:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades/HadesNoCodebaseDiagnosisTest.php`
  passato: `1 passed / 28 assertions` prima del follow-up.
- Remoto mirato dopo il follow-up:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades/HadesM3SharedMemoryTest.php tests/Feature/Hades/HadesNoCodebaseDiagnosisTest.php`
  passato: `33 passed / 447 assertions`.
- Remoto mirato dopo il secondo follow-up:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades/HadesNoCodebaseDiagnosisTest.php`
  passato: `3 passed / 63 assertions`.
- Remoto completo Hades + plugin auth:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades tests/Feature/PluginAuthTest.php`
  passato: `56 passed / 653 assertions` prima del follow-up; dopo il follow-up:
  `57 passed / 666 assertions`; dopo il secondo follow-up:
  `58 passed / 688 assertions`.
- Remoto:
  `git diff --check` passato prima del commit.

Resta fuori da questa tranche:

- Valutazione remota end-to-end con modello/agent reale invece di solo API
  contract backend.

## Esecuzione dashboard bug case lookup Hades - 2026-07-07

Stato: completata una tranche locale P1-4 per investigation surface.

Integrazione locale/dashboard:

- Nuova action condivisa `get_bug_report_detail` in
  `hermes_cli/hades_backend_actions.py`; usa il workspace binding corrente e
  chiama `HadesBackendClient.get_bug_report` con `project_id` e
  `workspace_binding_id`.
- Nuova route dashboard `GET /api/hades/backend/bug-reports/{bug_report_id}`.
- Nuovo metodo frontend `api.getHadesBackendBugReport` e tipi per bug report,
  evidence item, evidence pack e diagnosis report summary.
- La dashboard Backend aggiunge il pannello `Bug case lookup`: carica un bug
  report per id e mostra summary, evidence timeline, evidence packs con
  conteggi graph/source refs e diagnosis reports restituiti dal backend.

Verifiche eseguite:

- Locale:
  `.venv/bin/pytest tests/hermes_cli/test_hades_backend_web_api.py::test_hades_backend_web_route_reads_bug_report_detail`
  passato: `1 passed`.
- Frontend:
  `npm --prefix web run typecheck` passato.
- Frontend lint mirato:
  `npx eslint src/pages/BackendPage.tsx src/lib/api.ts` passato da `web/`.

## Esecuzione Laravel middleware graph Hades - 2026-07-07

Stato: completata una tranche locale P0-4 per indicizzazione Laravel.

Integrazione locale:

- `hades.php_graph.v1` include ora `middleware.schema=hades.laravel_middleware.v1`.
- L'indexer legge `app/Http/Kernel.php` metadata-only e indicizza:
  - alias middleware (`middleware:auth`, `middleware:verified`);
  - gruppi middleware (`middleware_group:web`);
  - classi middleware risolte tramite `use` PHP;
  - edge `middleware_alias_class`, `middleware_group_member`,
    `route_middleware_group` e `route_middleware_class`.
- Le route con middleware parametrizzati continuano a conservare l'alias route;
  la risoluzione classe usa il nome base prima dei parametri.
- Il payload non include sorgente raw, nomi degli array PHP o corpo di Kernel.

Verifiche eseguite:

- Locale mirato:
  `.venv/bin/pytest tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source -q`
  passato: `1 passed`.
- Locale graph/provider/docs:
  `.venv/bin/pytest tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `67 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py tests/hermes_cli/test_hades_backend_jobs.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel query builder graph Hades - 2026-07-07

Stato: completata una tranche locale P0-4 per query awareness source-free.

Integrazione locale:

- `hades.php_graph.v1` mantiene gli edge compatibili `query_table` e aggiunge
  edge metadata-only per catene `DB::table(...)->...`.
- I nuovi edge `query_operation` puntano a nodi `query:<table>:<operation>` e
  conservano solo tabella, operazione, accesso (`filter`, `join`, `read`,
  `write`, `shape`), path e linea.
- I nuovi edge `query_read` e `query_write` collegano controller/metodo alle
  tabelle coinvolte, cosi' una traversata no-codebase puo' distinguere una
  route che legge `orders` da una che aggiorna `orders`.
- Il payload non include literal della query, argomenti `where`, valori
  aggiornati o sorgente raw.

Verifiche eseguite:

- Locale mirato:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source`
  passato: `1 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `68 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel Eloquent query graph Hades - 2026-07-07

Stato: completata una tranche locale P0-4 per model query awareness
source-free.

Integrazione locale:

- `hades.php_graph.v1` aggiunge un pre-index metadata-only dei model Laravel
  per risolvere `App\Models\*` verso tabella tramite `$table` o convenzione
  Laravel.
- Le catene Eloquent statiche su model risolti, ad esempio
  `Order::where(...)->first()` e `Order::where(...)->update(...)`, producono
  ora edge `query_operation`, `query_read` e `query_write` con attributo
  `model`.
- Gli edge legacy `eloquent_query` restano invariati per compatibilita', ma le
  traversate no-codebase possono ora arrivare dalla route/metodo alla tabella
  letta o scritta anche quando il codice usa Eloquent e non `DB::table`.
- Il payload non conserva literal `where`, valori update o sorgente raw.

Verifiche eseguite:

- Locale mirato:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source`
  passato: `1 passed`.
- Locale lint/compile mirato:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py tests/hermes_cli/test_hades_backend_jobs.py`
  passato; `py_compile` sugli stessi file passato.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `68 passed`.
- Locale lint/compile completo:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione graph search query-edge summaries Hades - 2026-07-07

Stato: completata una tranche locale P1-3/P0-4 per retrieval quality
source-free.

Integrazione locale:

- I risultati fallback di `hades_backend_graph_search` per edge locali
  includono ora nel `summary` dettagli bounded da provenance:
  `operation`, `query_method`, `access`, `table`, `model`, `path`, `line`.
- Gli edge restano invariati nel payload strutturato; cambia solo il riassunto
  leggibile dal modello, cosi' una ricerca come `orders update write` mostra
  subito che l'edge rappresenta una write query su `orders`.
- Non viene aggiunto source raw: i dettagli sono gli stessi metadati gia'
  presenti nel graph ref.

Verifiche eseguite:

- Locale mirato:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_query_write_edges`
  passato: `1 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `68 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check plugins/memory/hades_backend/__init__.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel route-model binding graph Hades - 2026-07-07

Stato: completata una tranche locale P0-4 per data-flow metadata-only.

Integrazione locale:

- `hades.php_graph.v1` riconosce ora route-model binding Laravel implicito:
  quando una route come `/orders/{order}` punta a `OrderController@show` e il
  metodo riceve `Order $order`, il graph aggiunge edge `route_model_binding`.
- Il graph aggiunge anche `route_model_table` verso la tabella risolta tramite
  mappa `model_table` o convenzione Laravel, cosi' una traversata source-free
  puo' passare direttamente da route a model/table.
- Il fallback locale di `hades_backend_graph_search` include ora nei summary
  edge anche `param`, `handler` e `uri`, oltre ai dettagli query gia' aggiunti.
- Il payload non include source raw o corpo del controller: solo route id,
  handler, parametro, model, table, path e line.

Verifiche eseguite:

- Locale mirato graph:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source`
  passato: `1 passed`.
- Locale mirato provider:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_route_model_bindings`
  passato: `1 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `69 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel resource routes graph Hades - 2026-07-07

Stato: completata una tranche locale P0-4 per route coverage metadata-only.

Integrazione locale:

- `_laravel_routes` espande ora `Route::resource(...)` e
  `Route::apiResource(...)` in route CRUD standard Laravel.
- Le route generate includono `name` (`invoices.index`, `invoices.show`, ...),
  handler (`InvoiceController@index`), uri, method, middleware ereditato dalla
  chain e metadati `resource` / `resource_action`.
- `apiResource` omette `create` ed `edit`, coerentemente col comportamento
  Laravel API.
- Il fallback locale di `hades_backend_graph_search` indicizza
  `resource/resource_action` negli attributi route, cosi' query come
  `invoices resource index` trovano l'entrypoint anche senza source.
- Il payload non include `Route::apiResource` o sorgente raw.

Verifiche eseguite:

- Locale mirato graph:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source`
  passato: `1 passed`.
- Locale mirato provider:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_resource_routes`
  passato: `1 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `72 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel authorization graph Hades - 2026-07-07

Stato: completata una tranche locale P0-4 per authorization awareness
metadata-only.

Integrazione locale:

- `hades.php_graph.v1` riconosce `$this->authorize('ability', $model)` e
  `Gate::authorize/allows/denies/check('ability', $model)`.
- Il graph aggiunge edge method-level `authorization_check`,
  `authorization_model` e `authorization_table` con ability, target param,
  model e table quando risolvibili dai type hints.
- Il graph aggiunge anche edge route-level `route_authorization`,
  `route_authorization_model` e `route_authorization_table`, cosi' una ricerca
  source-free puo' partire dalla route e arrivare al vincolo policy/table.
- Il fallback locale di `hades_backend_graph_search` mostra nei summary edge
  `ability`, `target_model`, `target_param`, `source_path` e `source_line`.
- Il payload non include corpo metodo o expression raw: solo ability, param,
  model/table, handler, path e linee.

Verifiche eseguite:

- Locale mirato graph:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source`
  passato: `1 passed`.
- Locale mirato provider:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_authorization_edges`
  passato: `1 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `71 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel route validation graph Hades - 2026-07-07

Stato: completata una tranche locale P0-4 per validation awareness
metadata-only.

Integrazione locale:

- `hades.php_graph.v1` pre-indicizza i campi validati dalle FormRequest
  Laravel tramite `rules()` senza salvare il corpo del metodo.
- Quando una route punta a un controller method che riceve una FormRequest, il
  graph aggiunge `route_uses_form_request` e `route_request_validation`.
- Gli inline `$request->validate([...])` aggiungono anch'essi
  `route_request_validation` usando il method context gia' risolto.
- Il fallback locale di `hades_backend_graph_search` include nei summary edge
  anche `request_class`, `validation_path`, `validation_line` e `source`.
- Il payload resta source-free: route id, handler, request class, field, path e
  linee, non regole raw o literal validation.

Verifiche eseguite:

- Locale mirato graph:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source`
  passato: `1 passed`.
- Locale mirato provider:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_route_validation_edges`
  passato: `1 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `70 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel model metadata graph Hades - 2026-07-07

Stato: completata una tranche locale P0-4 per Eloquent model metadata
metadata-only.

Integrazione locale:

- `hades.php_graph.v1` riconosce ora metadata Eloquent model
  `protected $fillable`, `protected $guarded` e `protected $casts`, con supporto
  anche al metodo `casts(): array` quando restituisce un array letterale.
- Il graph aggiunge edge `model_fillable`, `model_guarded` e `model_cast` dal
  model al campo tabella risolto (`table:<table>.<field>`) o a un campo model
  quando la tabella non e' nota.
- Gli edge salvano solo `field`, `cast_type` per i cast, tabella, path e line:
  non conservano l'array sorgente, valori runtime o raw model source.
- Il fallback locale di `hades_backend_graph_search` mostra ora nei summary
  edge anche `field` e `cast_type`, cosi' una query su mass assignment o cast
  trova direttamente i metadata rilevanti senza leggere il filesystem.

Verifiche eseguite:

- Locale mirato graph:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source`
  passato: `1 passed`.
- Locale mirato provider:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_model_metadata_edges`
  passato: `1 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `73 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel Eloquent scope graph Hades - 2026-07-07

Stato: completata una tranche locale P0-4 per local scope awareness
metadata-only.

Integrazione locale:

- `hades.php_graph.v1` indicizza i local scope Eloquent definiti come metodi
  `scopeXxx` sui model Laravel.
- Il graph aggiunge edge `model_scope` dal model allo scope e `scope_method`
  dallo scope al metodo concreto, salvando solo nome scope, model, path e line.
- Quando il codice chiama `Model::xxx()` e `xxx` corrisponde a uno scope
  indicizzato, il graph aggiunge `eloquent_scope_call` dal context corrente
  allo scope, con model/table quando risolti.
- Le catene successive alla chiamata scope continuano a produrre edge
  `query_operation`/`query_read`/`query_write`, quindi una diagnosi source-free
  puo' seguire `route -> controller method -> scope -> table/read-write`.
- Il fallback locale di `hades_backend_graph_search` mostra `scope=...` nei
  summary edge; il payload non conserva corpo dello scope o literal query.

Verifiche eseguite:

- Locale mirato graph:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source`
  passato: `1 passed`.
- Locale mirato provider:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_model_scope_edges`
  passato: `1 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `74 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel Eloquent attribute graph Hades - 2026-07-07

Stato: completata una tranche locale P0-4 per accessor/mutator awareness
metadata-only.

Integrazione locale:

- `hades.php_graph.v1` indicizza gli accessor/mutator Eloquent classici
  `getXAttribute` / `setXAttribute` sui model Laravel.
- Il graph riconosce anche metodi moderni che restituiscono
  `Attribute::make(get: ..., set: ...)`, distinguendo edge `model_accessor` e
  `model_mutator`.
- Gli edge salvano solo campo, direzione (`get`/`set`), stile
  (`classic`/`attribute_object`), metodo, tabella, path e line; non conservano
  corpo metodo, closure o literal di trasformazione.
- Il fallback locale di `hades_backend_graph_search` mostra `direction`,
  `attribute_style` e `attribute_method` nei summary edge, cosi' una diagnosi
  source-free puo' individuare trasformazioni attributo rilevanti per payload,
  cast e persistenza.
- Il parser salta `Attribute::make` come static call generica per evitare rumore
  non diagnostico nel graph.

Verifiche eseguite:

- Locale mirato graph:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source`
  passato: `1 passed`.
- Locale mirato provider:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_model_attribute_edges`
  passato: `1 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `75 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel Eloquent serialization metadata graph Hades - 2026-07-07

Stato: completata una tranche locale P0-4 per serialization awareness
metadata-only.

Integrazione locale:

- `hades.php_graph.v1` indicizza ora i metadata Eloquent `$hidden`,
  `$visible` e `$appends` sui model Laravel.
- Il graph aggiunge edge `model_hidden`, `model_visible` e
  `model_appended_attribute`, con `property` e `field` espliciti.
- Gli `$appends` puntano a `model_attribute:<model>.<field>` invece che a
  `table:<table>.<field>`, cosi' il graph distingue attributi virtuali da
  colonne DB.
- Il fallback locale di `hades_backend_graph_search` mostra `property=...` nei
  summary edge, utile per diagnosticare campi JSON mancanti, nascosti o
  aggiunti senza leggere il model source.
- Il payload non conserva array raw o sorgente del model.

Verifiche eseguite:

- Locale mirato graph:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source`
  passato: `1 passed`.
- Locale mirato provider:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_model_serialization_edges`
  passato: `1 passed`.
- Locale regressione traversal:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_traverse_falls_back_to_local_graph_cache`
  passato: `1 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `76 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel API Resource graph Hades - 2026-07-07

Stato: completata una tranche locale P0-4 per API Resource awareness
metadata-only.

Integrazione locale:

- `hades.php_graph.v1` riconosce classi Laravel `JsonResource` e
  `ResourceCollection` come ruolo `api_resource`.
- Il graph collega le resource al model/table risolti per convenzione con edge
  `api_resource_model` e `api_resource_table`.
- Quando controller o service usano `new Resource`, `Resource::make(...)` o
  `Resource::collection(...)`, il graph aggiunge `api_resource_ref` dal context
  corrente alla resource, con `resource_method`, model e table quando noti.
- Il fallback locale di `hades_backend_graph_search` mostra
  `resource_method=...` nei summary edge, utile per diagnosticare payload API
  e trasformazioni JSON senza leggere source live.
- Il payload non conserva il corpo `toArray`, array di output o sorgente raw.

Verifiche eseguite:

- Locale mirato graph:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source`
  passato: `1 passed`.
- Locale mirato provider:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_api_resource_edges`
  passato: `1 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `77 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel validation rule metadata graph Hades - 2026-07-07

Stato: completata una tranche locale P0-4 per validation rule awareness
metadata-only.

Integrazione locale:

- `hades.php_graph.v1` arricchisce ora edge `request_validation` e
  `route_request_validation` con `validation_rules`.
- Il parser estrae solo nomi regola bounded come `required`, `integer`,
  `string`, `exists` da FormRequest e inline `$request->validate([...])`.
- Parametri raw e sorgente regole non vengono salvati: ad esempio
  `exists:customers,id` viene ridotto a `exists`.
- Il fallback locale di `hades_backend_graph_search` mostra
  `validation_rules=...` nei summary edge, utile per diagnosticare bug 422 e
  input contract mismatch senza leggere source live.

Verifiche eseguite:

- Locale mirato graph:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source`
  passato: `1 passed`.
- Locale mirato provider:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_route_validation_edges`
  passato: `1 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `77 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel API Resource field graph Hades - 2026-07-07

Stato: completata una tranche locale P0-4 per API Resource response-field
awareness metadata-only.

Integrazione locale:

- `hades.php_graph.v1` estrae da `JsonResource::toArray()` edge
  `api_resource_field` verso `response_field:*`.
- Gli edge salvano solo la chiave di risposta (`field`), model/table risolti,
  path e line; non conservano corpo `toArray`, valori restituiti o sorgente raw.
- Il fallback locale di `hades_backend_graph_search` rende cercabili i campi
  esposti nei Resource e mostra `field=...`, `model=...` e `table=...` nei
  summary edge.
- Questa tranche completa il legame source-free `controller -> resource ->
  response_field`, utile per diagnosticare payload API mancanti o trasformati
  senza accesso live alla codebase.

Verifiche eseguite:

- Locale mirato graph:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source`
  passato: `1 passed`.
- Locale mirato provider:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_api_resource_field_edges`
  passato: `1 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `78 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel validation database rule graph Hades - 2026-07-07

Stato: completata una tranche locale P0-4 per validation DB-rule awareness
metadata-only.

Integrazione locale:

- `hades.php_graph.v1` estrae riferimenti DB bounded dalle validation rules
  Laravel `exists` e `unique`.
- Il parser conserva solo identificatori semplici table/column, ad esempio
  `exists:customers,id` diventa `table=customers`, `column=id`; parametri
  complessi, condizioni e valori raw vengono scartati.
- Il graph aggiunge `validation_database_rule` dal FormRequest/controller
  context e `route_validation_database_rule` dalla route quando il campo e'
  associato a una route.
- Il fallback locale di `hades_backend_graph_search` espone nei summary
  `field=...`, `rule=...`, `table=...` e `column=...`, utile per diagnosticare
  422 causati da mismatch tra input validation e database senza leggere source.

Verifiche eseguite:

- Locale mirato parser/graph:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_php_validation_database_rule_refs_keep_only_sanitized_identifiers tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source`
  passato: `2 passed`.
- Locale mirato provider:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_validation_database_rule_edges`
  passato: `1 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `80 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel FormRequest authorization graph Hades - 2026-07-07

Stato: completata una tranche locale P0-4 per FormRequest authorization
awareness metadata-only.

Integrazione locale:

- `hades.php_graph.v1` rileva `authorize()` nei Laravel FormRequest.
- Il graph aggiunge `request_authorization` dal FormRequest e
  `route_request_authorization` dalla route che usa quel FormRequest.
- Il payload salva solo risultato statico bounded `allow`/`deny` quando il
  metodo ritorna letteralmente `true`/`false`, oppure `dynamic`, piu' path/line;
  non conserva corpo metodo o condizioni raw.
- Il fallback locale di `hades_backend_graph_search` mostra
  `authorization_result=...` e `authorization_path=...`, utile per diagnosticare
  403 che nascono prima del controller.
- Durante la tranche e' stata corretta una regressione di indentazione nel
  blocco class-level `api_resource_table`, verificando che i FormRequest non
  ricevano edge API Resource.

Verifiche eseguite:

- Locale mirato graph:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source`
  passato: `1 passed`.
- Locale mirato provider:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_form_request_authorization_edges`
  passato: `1 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `81 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel FormRequest input mutation graph Hades - 2026-07-07

Stato: completata una tranche locale P0-4 per FormRequest input mutation
awareness metadata-only.

Integrazione locale:

- `hades.php_graph.v1` rileva mutazioni input in
  `prepareForValidation()` e `passedValidation()` quando usano
  `$this->merge([...])` o `$this->replace([...])`.
- Il parser estrae solo chiavi top-level dell'array passato a `merge/replace`,
  evitando di trattare nested keys come campi request principali.
- Il graph aggiunge `request_input_mutation` dal FormRequest e
  `route_request_input_mutation` dalla route che usa quel FormRequest.
- Il payload salva solo `field`, operazione `merge`/`replace`, stage
  (`prepare_for_validation`/`passed_validation`) e path/line; non conserva
  valori calcolati o corpo metodo.
- Il fallback locale di `hades_backend_graph_search` mostra `field=...`,
  `operation=...` e `mutation_stage=...`, utile per diagnosticare input
  normalizzati o riscritti prima della validation/controller.

Verifiche eseguite:

- Locale mirato helper/graph:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_php_top_level_array_field_keys_ignores_nested_keys tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source`
  passato: `2 passed`.
- Locale mirato provider:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_form_request_input_mutation_edges`
  passato: `1 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `83 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel HTTP abort graph Hades - 2026-07-07

Stato: completata una tranche locale P0-4 per HTTP abort awareness
metadata-only.

Integrazione locale:

- `hades.php_graph.v1` rileva helper Laravel `abort()`, `abort_if()` e
  `abort_unless()` quando lo status code e' un literal numerico semplice.
- Il graph aggiunge `http_abort` dal metodo controller e `route_http_abort`
  dalla route che punta allo stesso handler.
- Il payload salva solo `status_code`, helper (`abort`/`abort_if`/
  `abort_unless`) e path/line; non conserva condizioni, messaggi o corpo
  controller.
- Il fallback locale di `hades_backend_graph_search` mostra `status_code=...`
  e `abort_helper=...`, utile per diagnosticare 403/404 generati dal controller
  anche quando l'agent consulta solo artifact/cache.

Verifiche eseguite:

- Locale mirato graph:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source`
  passato: `1 passed`.
- Locale mirato provider:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_http_abort_edges`
  passato: `1 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `84 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel method call graph Hades - 2026-07-07

Stato: completata una tranche locale P0-4 per call graph method-level
metadata-only.

Integrazione locale:

- `hades.php_graph.v1` costruisce un indice globale dei metodi PHP rilevati
  nel workspace non-test.
- Le static/service calls continuano a produrre gli edge compatibili
  `static_call` verso `FQCN::method`.
- Quando il target risolve a un metodo indicizzato, il graph aggiunge anche
  `calls_method` verso il simbolo metodo reale, ad esempio
  `OrderController@show -> OrderService@format`.
- Il payload salva solo target class, target method, call type `static` e
  path/line della chiamata; non conserva argomenti o corpo metodo.
- Il fallback locale di `hades_backend_graph_search` e
  `hades_backend_graph_traverse` puo' ora seguire `route -> controller ->
  service method` senza accesso al source.

Verifiche eseguite:

- Locale mirato graph:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source`
  passato: `1 passed`.
- Locale mirato provider/traversal:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_method_call_edges tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_traverse_falls_back_to_local_graph_cache`
  passato: `2 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `85 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel instance method call graph Hades - 2026-07-07

Stato: completata una tranche locale P0-4 per call graph instance
metadata-only su parametri tipizzati.

Integrazione locale:

- `hades.php_graph.v1` rileva chiamate `$param->method(...)` dentro un metodo
  PHP quando `$param` deriva da un type hint dello stesso metodo.
- Se il target class/method e' presente nell'indice globale dei metodi PHP, il
  graph aggiunge `calls_method` verso il simbolo metodo reale.
- Il payload salva solo receiver, target class, target method, call type
  `instance` e path/line; non conserva argomenti o corpo metodo.
- La fixture Laravel copre `OrderService $formatter` in un controller action e
  `$formatter->format($order)`, verificando anche che l'espressione raw non
  compaia nell'artifact.
- Il fallback locale di `hades_backend_graph_search` puo' distinguere
  `call_type=instance` e `receiver=formatter` per spiegare il percorso
  `route -> controller -> injected service method` senza source.

Verifiche eseguite:

- Locale mirato graph:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source`
  passato: `1 passed`.
- Locale mirato provider/search/traversal:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_instance_method_call_edges tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_method_call_edges tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_traverse_falls_back_to_local_graph_cache`
  passato: `3 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `86 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel property method call graph Hades - 2026-07-07

Stato: completata una tranche locale P0-4 per call graph su proprieta'
tipizzate e constructor-injected services.

Integrazione locale:

- `hades.php_graph.v1` costruisce una mappa metadata-only delle proprieta'
  tipizzate da property declaration, constructor promotion e assegnazioni
  `$this->prop = $param` quando `$param` ha un type hint semplice.
- Le chiamate `$this->prop->method(...)` producono `calls_method` verso il
  simbolo metodo reale quando target class/method sono indicizzati.
- Il payload salva solo receiver, target class, target method, call type
  `property` e path/line; non conserva argomenti, constructor body o corpo
  metodo.
- La fixture Laravel copre constructor promotion `private OrderService $orders`
  e `$this->orders->format($order)`, verificando che le espressioni raw non
  compaiano nell'artifact.
- Il fallback locale di `hades_backend_graph_search` puo' distinguere
  `call_type=property` e `receiver=orders`, rendendo traversabile
  `route -> controller -> property-injected service method` senza source.

Verifiche eseguite:

- Locale mirato graph:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source`
  passato: `1 passed`.
- Locale mirato provider/search:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_property_method_call_edges tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_instance_method_call_edges tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_method_call_edges`
  passato: `3 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `87 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel exception throw graph Hades - 2026-07-07

Stato: completata una tranche locale P0-4 per terminal exception awareness
metadata-only.

Integrazione locale:

- `hades.php_graph.v1` rileva `throw new ExceptionClass(...)` nei metodi PHP.
- Il graph aggiunge `throws_exception` dal metodo al target exception class
  risolto tramite namespace/import PHP.
- Se il throw e' direttamente nel route handler, il graph puo' aggiungere anche
  `route_throws_exception`; per throw in service il percorso resta traversabile
  via `route -> controller -> calls_method -> service -> throws_exception`.
- Il payload salva solo exception class, short name e path/line; non conserva
  messaggi, argomenti o corpo metodo.
- La fixture Laravel copre un service che lancia `OrderLockedException`,
  verificando che `throw new` e la call raw non compaiano nell'artifact.

Verifiche eseguite:

- Locale mirato graph:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source`
  passato: `1 passed`.
- Locale mirato provider/search/traversal:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_exception_throw_edges tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_traverse_falls_back_to_local_graph_cache`
  passato: `2 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `88 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel HTTP response status graph Hades - 2026-07-07

Stato: completata una tranche locale P0-4 per HTTP response status awareness
metadata-only.

Integrazione locale:

- `hades.php_graph.v1` rileva `response()->json(..., <status>)` e
  `response()->noContent(<status>)` quando lo status code e' un literal
  numerico semplice.
- Il graph aggiunge `http_response_status` dal metodo e
  `route_http_response_status` dalla route che punta allo stesso handler.
- Il payload salva solo `status_code`, helper (`response_json`/
  `response_nocontent`) e path/line; non conserva response body, messaggi o
  argomenti.
- La fixture Laravel copre un `InvoiceController@update` resource route che
  ritorna `409`, verificando che `response()->json` e body literal non
  compaiano nell'artifact.
- Il fallback locale di `hades_backend_graph_search` espone
  `status_code=409`, `response_helper=response_json` e handler/route metadata.

Verifiche eseguite:

- Locale mirato graph:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source`
  passato: `1 passed`.
- Locale mirato provider/search:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_http_response_status_edges`
  passato: `1 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `89 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel authorization policy method graph Hades - 2026-07-07

Stato: completata una tranche locale P0-4 per policy method awareness
metadata-only.

Integrazione locale:

- `hades.php_graph.v1` costruisce un indice `model -> policy` dai mapping
  `Gate::policy(Model::class, Policy::class)`.
- Quando un authorization check ha ability e target model risolti, e il metodo
  policy esiste nell'indice simboli PHP, il graph aggiunge
  `authorization_policy_method` e `route_authorization_policy_method`.
- Il payload salva solo ability, policy class, target model/table, path/line e
  metadata route; non conserva corpo policy, condizioni o argomenti raw.
- Il fallback locale di `hades_backend_graph_search` espone `policy_class` nei
  dettagli cercabili dell'edge.

Verifiche eseguite:

- Locale mirato graph:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source`
  passato: `1 passed`.
- Locale mirato provider/search:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_authorization_edges`
  passato: `1 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `89 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel policies property graph Hades - 2026-07-07

Stato: completata una tranche locale P0-4 per policy mapping awareness
metadata-only.

Integrazione locale:

- `hades.php_graph.v1` risolve anche i mapping Laravel
  `protected $policies = [Model::class => Policy::class]`, oltre a
  `Gate::policy(...)`.
- Lo stesso helper alimenta sia l'indice `model -> policy` usato dagli edge
  authorization-policy-method sia gli edge `policy_for`.
- Il payload salva solo model class, policy class, source del mapping
  (`policies_property` o `gate_policy`), path e line; non conserva body provider
  o condizioni raw.
- Il fallback locale di `hades_backend_graph_search` puo' recuperare il mapping
  `policy_for` da cache artifact anche a backend offline.

Verifiche eseguite:

- Locale mirato graph:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source`
  passato: `1 passed`.
- Locale mirato provider/search:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_policy_mapping_edges tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_authorization_edges`
  passato: `2 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `90 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel event listener method graph Hades - 2026-07-07

Stato: completata una tranche locale P0-4 per event-driven causal awareness
metadata-only.

Integrazione locale:

- `hades.php_graph.v1` usa l'indice metodi PHP per collegare i listener
  Laravel dichiarati in `$listen` al metodo eseguibile `Listener@handle`.
- Il graph aggiunge `event_listener_method` da event class a listener method,
  `emits_event_listener` dal metodo che emette l'evento al listener method e
  `route_emits_event_listener` dalla route collegata allo stesso handler.
- Il payload salva solo event class, listener class, handler, route metadata e
  path/line di emissione/registrazione; non conserva payload evento, corpo
  listener o argomenti.
- Il fallback locale di `hades_backend_graph_search` espone event/listener
  metadata per diagnosi source-free di bug event-driven anche a backend offline.

Verifiche eseguite:

- Locale mirato graph:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source`
  passato: `1 passed`.
- Locale mirato provider/search/traversal:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_event_listener_edges tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_traverse_falls_back_to_local_graph_cache`
  passato: `2 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `91 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel job dispatch method graph Hades - 2026-07-07

Stato: completata una tranche locale P0-4 per queue/job causal awareness
metadata-only.

Integrazione locale:

- `hades.php_graph.v1` usa l'indice metodi PHP per collegare
  `Job::dispatch(...)`, `dispatchSync(...)` e `dispatchAfterResponse(...)` al
  metodo eseguibile `Job@handle`, quando il job e' indicizzato.
- Il graph aggiunge `dispatches_job_method` dal metodo che dispatcha al job
  handle e `route_dispatches_job_method` dalla route collegata allo stesso
  handler.
- Il payload salva solo job class, job method, dispatch method, handler, route
  metadata e path/line; non conserva payload del job, argomenti o corpo job.
- Il fallback locale di `hades_backend_graph_search` espone job/dispatch
  metadata e la traversata locale da route raggiunge `Job@handle` anche a
  backend offline.

Verifiche eseguite:

- Locale mirato graph:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source`
  passato: `1 passed`.
- Locale mirato provider/search/traversal:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_job_dispatch_method_edges tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_traverse_falls_back_to_local_graph_cache`
  passato: `2 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `92 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel scheduler handle graph Hades - 2026-07-07

Stato: completata una tranche locale P0-4 per scheduler/cron causal awareness
metadata-only.

Integrazione locale:

- `hades.php_graph.v1` costruisce un indice `command name -> Command@handle`
  dagli Artisan command con `$signature` e metodo `handle` indicizzato.
- Il graph aggiunge `artisan_command_method` da `command:*` al command handle,
  `scheduled_command_method` da `Console\\Kernel` al command handle e
  `scheduled_job_method` da `Console\\Kernel` al job handle.
- Il payload salva solo command/job class, command/job method, cadence,
  scheduler path/line e command path/line dove rilevante; non conserva body,
  argomenti o payload schedulati.
- Il fallback locale di `hades_backend_graph_search` e
  `hades_backend_graph_traverse` puo' seguire scheduler -> handle anche a
  backend offline.

Verifiche eseguite:

- Locale mirato graph:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source`
  passato: `1 passed`.
- Locale mirato provider/search/traversal:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_scheduled_handle_edges tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_traverse_finds_local_scheduled_handle_edges`
  passato: `2 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `94 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel mail notification graph Hades - 2026-07-07

Stato: completata una tranche locale P0-4 per mail/notification side-effect
awareness metadata-only.

Integrazione locale:

- `hades.php_graph.v1` rileva chiamate conservative a
  `Mail::to(...)->send/queue/later(new Mailable(...))`,
  `Mail::send/queue/later(new Mailable(...))`, `->notify(new Notification(...))`
  e `Notification::send/sendNow(..., new Notification(...))`.
- Il graph aggiunge `sends_mail`, `sends_mail_method`,
  `route_sends_mail_method`, `sends_notification`,
  `sends_notification_method` e `route_sends_notification_method`.
- I method edge puntano a `Mailable@build/content/envelope` o
  `Notification@toMail/toArray/toDatabase/toBroadcast/via`, scegliendo il primo
  metodo indicizzato disponibile.
- Il payload salva solo mailable/notification class, metodo target, metodo di
  invio/source, route metadata e path/line; non conserva destinatari, payload,
  template data o argomenti raw.
- Il fallback locale di `hades_backend_graph_search` espone questi dettagli da
  cache artifact anche a backend offline.

Verifiche eseguite:

- Locale mirato graph:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source`
  passato: `1 passed`.
- Locale mirato provider/search/traversal:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_mail_notification_edges tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_traverse_falls_back_to_local_graph_cache`
  passato: `2 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `95 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel container-bound service call graph Hades - 2026-07-07

Stato: completata una tranche locale P0-4 per DI/container causal awareness
metadata-only.

Integrazione locale:

- `hades.php_graph.v1` costruisce un indice dei container binding Laravel
  `abstract -> concrete` da `$this->app->bind/singleton/scoped/instance`,
  `app()->...` e `App::...`.
- Quando una instance/property call su parametro o proprieta' tipizzata non
  trova il metodo sull'abstract, il graph prova il concreto bindato e aggiunge
  `calls_method` verso il metodo reale se indicizzato.
- Il payload dell'edge salva `abstract_class`, concrete `target_class`, binding
  type, receiver, target method e path/line; non conserva container runtime
  state, argomenti o corpo metodo.
- Il fallback locale di `hades_backend_graph_search` espone abstract/binding
  metadata, cosi' una diagnosi source-free puo' attraversare interfacce DI fino
  al service concreto.

Verifiche eseguite:

- Locale mirato graph:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source`
  passato: `1 passed`.
- Locale mirato provider/search:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_instance_method_call_edges`
  passato: `1 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `95 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel middleware handle graph Hades - 2026-07-08

Stato: completata una tranche locale P0-4 per middleware causal awareness
metadata-only.

Integrazione locale:

- `hades.php_graph.v1` usa l'indice metodi PHP per collegare i middleware
  Laravel risolti da alias/gruppi al metodo eseguibile `Middleware@handle`.
- Il graph aggiunge `route_middleware_method` dalla route al middleware handle,
  mantenendo gli edge esistenti `route_middleware`, `route_middleware_group` e
  `route_middleware_class`.
- Il payload salva solo alias middleware, classe middleware, eventuale `via`
  del gruppo, route method/URI e path/line; non conserva corpo middleware,
  condizioni, request payload o argomenti.
- Il fallback locale di `hades_backend_graph_search` espone middleware/class/via
  metadata da cache artifact anche a backend offline.

Verifiche eseguite:

- Locale mirato graph:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source`
  passato: `1 passed`.
- Locale mirato provider/search:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_middleware_method_edges`
  passato: `1 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `96 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel HTTP redirect graph Hades - 2026-07-08

Stato: completata una tranche locale P0-4 per redirect causal awareness
metadata-only.

Integrazione locale:

- `hades.php_graph.v1` rileva redirect Laravel conservativi:
  `redirect()->route(...)`, `redirect()->to(...)`, `redirect(...)` e `back()`.
- Il graph aggiunge `http_redirect` dal metodo eseguibile e
  `route_http_redirect` dalla route collegata allo stesso handler.
- Il payload salva solo redirect type, target route/path se letterale, helper,
  eventuale status 3xx, route metadata e path/line; non conserva parametri
  passati al redirect, request payload, condizioni o corpo metodo.
- Il fallback locale di `hades_backend_graph_search` espone
  `redirect_type`/`redirect_target`/`redirect_status` da cache artifact anche a
  backend offline.

Verifiche eseguite:

- Locale mirato graph:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source`
  passato: `1 passed`.
- Locale mirato provider/search:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_http_redirect_edges`
  passato: `1 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `97 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel session key graph Hades - 2026-07-08

Stato: completata una tranche locale P0-4 per session/flash causal awareness
metadata-only.

Integrazione locale:

- `hades.php_graph.v1` rileva accessi conservativi a chiavi sessione Laravel:
  `session('key')`, `session(['key' => ...])`, `session()->get/put/flash/...`,
  `$request->session()->...` e `Session::...`.
- Il graph aggiunge `session_access` dal metodo eseguibile e
  `route_session_access` dalla route collegata allo stesso handler.
- Il payload salva solo session key, operazione (`read`, `write`, `flash`,
  `check`, `delete`, `read_delete`), helper e path/line; non conserva valori
  sessione, messaggi flash, payload request o condizioni.
- Il fallback locale di `hades_backend_graph_search` espone
  `session_key`/`session_operation`/`session_method` da cache artifact anche a
  backend offline.

Verifiche eseguite:

- Locale mirato graph:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source`
  passato: `1 passed`.
- Locale mirato provider/search:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_session_access_edges`
  passato: `1 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `98 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel cache key graph Hades - 2026-07-08

Stato: completata una tranche locale P0-4 per cache causal awareness
metadata-only.

Integrazione locale:

- `hades.php_graph.v1` rileva accessi conservativi a chiavi cache Laravel:
  `cache('key')`, `cache(['key' => ...])`, `cache()->get/put/remember/...` e
  `Cache::get/put/remember/forget/...`.
- Il graph aggiunge `cache_access` dal metodo eseguibile e
  `route_cache_access` dalla route collegata allo stesso handler.
- Il payload salva solo cache key, operazione (`read`, `write`, `read_write`,
  `check`, `delete`, `read_delete`), helper, presenza TTL e path/line; non
  conserva valori cache, closure, TTL literal o condizioni.
- Il fallback locale di `hades_backend_graph_search` espone
  `cache_key`/`cache_operation`/`cache_method`/`cache_ttl_present` da cache
  artifact anche a backend offline.

Verifiche eseguite:

- Locale mirato graph:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source`
  passato: `1 passed`.
- Locale mirato provider/search:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_cache_access_edges`
  passato: `1 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `99 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel outbound HTTP graph Hades - 2026-07-08

Stato: completata una tranche locale P0-4 per outbound HTTP causal awareness
metadata-only.

Integrazione locale:

- `hades.php_graph.v1` rileva chiamate conservative al client HTTP Laravel
  `Http::get/post/put/patch/delete/head/send(...)` e chain semplici come
  `Http::withToken(...)->post(...)`, quando l'URL HTTP/HTTPS e' letterale.
- Il graph aggiunge `outbound_http_call` dal metodo eseguibile e
  `route_outbound_http_call` dalla route collegata allo stesso handler.
- Il payload salva solo client, metodo HTTP, schema, host, path senza query,
  helper e path/line; non conserva query string, header, payload o URL completi.
- Il fallback locale di `hades_backend_graph_search` espone
  `http_client`/`http_method`/`http_host`/`http_path` da cache artifact anche a
  backend offline.

Verifiche eseguite:

- Locale mirato graph:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source`
  passato: `1 passed`.
- Locale mirato provider/search:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_outbound_http_call_edges`
  passato: `1 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `100 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel Storage graph Hades - 2026-07-08

Stato: completata una tranche locale P0-4/P0-5 per filesystem side-effect
awareness metadata-only.

Integrazione locale:

- `hades.php_graph.v1` rileva operazioni conservative su Laravel Storage:
  `Storage::get/put/delete/url/...` e chain `Storage::disk('...')->put(...)`
  quando il path e' un literal sicuro.
- Il graph aggiunge `storage_access` dal metodo eseguibile e
  `route_storage_access` dalla route collegata allo stesso handler.
- Il payload salva solo disk, path, operazione (`read`, `write`, `delete`,
  `check`, `list`, `resolve`), helper e path/line; non conserva contenuti file,
  payload scritti, stream o condizioni.
- Il fallback locale di `hades_backend_graph_search` espone
  `storage_disk`/`storage_path`/`storage_operation`/`storage_method` da cache
  artifact anche a backend offline.

Verifiche eseguite:

- Locale mirato graph:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source`
  passato: `1 passed`.
- Locale mirato provider/search:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_storage_access_edges`
  passato: `1 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `101 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel request input graph Hades - 2026-07-08

Stato: completata una tranche locale P0-4 per request input causal awareness
metadata-only.

Integrazione locale:

- `hades.php_graph.v1` rileva letture conservative da request Laravel:
  `$request->input/get/query/header/cookie/route(...)`, `request()->...` e
  `request('field')`.
- Il graph aggiunge `request_input_access` dal metodo eseguibile e
  `route_request_input_access` dalla route collegata allo stesso handler.
- Il payload salva solo field, sorgente input (`input`, `query`, `header`,
  `cookie`, `route_param`), helper e path/line; non conserva valori request,
  default value, payload, header value o query string.
- Il fallback locale di `hades_backend_graph_search` espone
  `field`/`input_source`/`input_method` da cache artifact anche a backend
  offline.

Verifiche eseguite:

- Locale mirato graph:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source`
  passato: `1 passed`.
- Locale mirato provider/search:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_request_input_access_edges`
  passato: `1 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `102 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel request file graph Hades - 2026-07-08

Stato: completata una tranche locale P0-4 per upload/request-file awareness
metadata-only.

Integrazione locale:

- `hades.php_graph.v1` rileva letture e check conservativi di upload Laravel:
  `$request->file(...)`, `$request->hasFile(...)`, `request()->file(...)` e
  `request()->hasFile(...)`.
- Il graph aggiunge `request_file_access` dal metodo eseguibile e
  `route_request_file_access` dalla route collegata allo stesso handler.
- Il payload salva solo campo file, operazione (`read`/`check`), helper e
  path/line; non conserva filename caricati, contenuti file, stream, payload o
  condizioni.
- Il fallback locale di `hades_backend_graph_search` espone
  `file_field`/`file_operation`/`file_method` da cache artifact anche a backend
  offline.

Verifiche eseguite:

- Locale mirato graph:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source`
  passato: `1 passed`.
- Locale mirato provider/search:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_request_file_access_edges`
  passato: `1 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `103 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel response cookie graph Hades - 2026-07-08

Stato: completata una tranche locale P0-4 per response cookie causal awareness
metadata-only.

Integrazione locale:

- `hades.php_graph.v1` rileva set/delete conservativi di cookie Laravel:
  `cookie(...)`, `Cookie::queue/make/forget(...)` e
  `response()->cookie/withoutCookie(...)`.
- Il graph aggiunge `cookie_access` dal metodo eseguibile e
  `route_cookie_access` dalla route collegata allo stesso handler.
- Il payload salva solo nome cookie, operazione (`set`/`delete`), helper e
  path/line; non conserva valori cookie, payload, attributi sensibili o
  condizioni.
- Il fallback locale di `hades_backend_graph_search` espone
  `cookie_name`/`cookie_operation`/`cookie_method` da cache artifact anche a
  backend offline.

Verifiche eseguite:

- Locale mirato graph:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source`
  passato: `1 passed`.
- Locale mirato provider/search:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_cookie_access_edges`
  passato: `1 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `104 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel DB transaction graph Hades - 2026-07-08

Stato: completata una tranche locale P0-4 per transaction causal awareness
metadata-only.

Integrazione locale:

- `hades.php_graph.v1` rileva transazioni Laravel conservative:
  `DB::transaction(...)`, `DB::beginTransaction()`, `DB::commit()` e
  `DB::rollBack()`.
- Il graph aggiunge `db_transaction` dal metodo eseguibile e
  `route_db_transaction` dalla route collegata allo stesso handler.
- Il payload salva solo operazione (`transaction`, `begin`, `commit`,
  `rollback`), helper e path/line; non conserva closure, condizioni o query
  payload.
- Il fallback locale di `hades_backend_graph_search` espone
  `transaction_operation`/`transaction_method` da cache artifact anche a
  backend offline.

Verifiche eseguite:

- Locale mirato graph:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source`
  passato: `1 passed`.
- Locale mirato provider/search:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_db_transaction_edges`
  passato: `1 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `105 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel query modifier graph Hades - 2026-07-08

Stato: completata una tranche locale P0-4 per Eloquent soft-delete e lock
query awareness metadata-only.

Integrazione locale:

- `hades.php_graph.v1` estende le `query_operation` esistenti invece di
  introdurre un nuovo edge kind: `withTrashed`, `onlyTrashed`,
  `withoutTrashed`, `withoutGlobalScope(s)`, `restore`, `forceDelete`,
  `lockForUpdate`, `sharedLock` e `lock` diventano operazioni cercabili.
- L'accesso e' classificato come `scope` per i modifier soft-delete/global
  scope, `lock` per i lock pessimisti e `write` per `restore`/`forceDelete`.
- Il payload resta metadata-only: table/model, operation, access, path e line;
  non conserva condizioni `where`, argomenti raw o payload della query.
- Il fallback locale di `hades_backend_graph_search` trova questi edge da
  cache artifact anche con backend remoto offline.

Resta fuori da questa tranche:

- Verifica AST reale del trait `SoftDeletes` e analisi data-flow delle
  condizioni query; questa tranche espone il comportamento chiamato dal
  controller/metodo senza salvare source raw.

Verifiche eseguite:

- Locale mirato graph + provider:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_query_modifier_edges`
  passato: `2 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `106 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel model trait graph Hades - 2026-07-08

Stato: completata una tranche locale P0-4 per model trait awareness
metadata-only.

Integrazione locale:

- `hades.php_graph.v1` aggiunge edge `model_trait` per trait dichiarati dentro
  model Laravel, risolti tramite namespace/import PHP quando possibile.
- Il caso `SoftDeletes` diventa cercabile e traversabile come fatto causale
  source-free collegato al model/table, utile insieme agli edge
  `withTrashed`/`onlyTrashed`/`restore`/`forceDelete`.
- Il payload salva solo model, trait class, trait short name, table, path e
  line; non conserva corpo del trait o source raw del model.
- Il fallback locale di `hades_backend_graph_search` espone
  `trait_class`/`trait_short_name` da cache artifact anche a backend offline.

Resta fuori da questa tranche:

- Analisi AST del corpo del trait e inferenza automatica di comportamenti
  introdotti da trait custom; questa tranche indicizza la presenza del trait
  come metadata verificabile.

Verifiche eseguite:

- Locale mirato graph + provider:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_model_trait_edges`
  passato: `2 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `107 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel observer method graph Hades - 2026-07-08

Stato: completata una tranche locale P0-4 per observer lifecycle awareness
metadata-only.

Integrazione locale:

- `hades.php_graph.v1` mantiene `observed_by` per il binding
  `Model::observe(Observer::class)` e aggiunge `observed_by_method` verso i
  lifecycle methods realmente presenti sull'observer, ad esempio
  `OrderObserver@updated`.
- Gli edge salvano solo model, observer class, observer method/lifecycle event,
  table, path e line; non conservano corpo observer o argomenti runtime.
- Il fallback locale di `hades_backend_graph_search` espone
  `observer_class`/`observer_method`/`lifecycle_event` da cache artifact anche a
  backend offline.

Resta fuori da questa tranche:

- Inferenza automatica che collega ogni singola `Model::update/delete` al
  lifecycle event potenzialmente emesso; questa tranche rende gia' traversabile
  il metodo observer eseguibile dal model.

Verifiche eseguite:

- Locale mirato graph + provider:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_observer_method_edges`
  passato: `2 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `108 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel model instance operation graph Hades - 2026-07-08

Stato: completata una tranche locale P0-4 per model instance operation
awareness metadata-only.

Integrazione locale:

- `hades.php_graph.v1` aggiunge `model_instance_operation` per operazioni
  Eloquent su parametri tipizzati come `Order $order` e
  `route_model_instance_operation` dalla route collegata allo stesso handler.
- Le operazioni coperte sono conservative e causalmente rilevanti:
  `save`, `update`, `delete`, `restore`, `forceDelete`, `touch`, `push`,
  `increment` e `decrement`.
- Il payload salva solo receiver, model, table, operation, access, handler,
  path/line e source path/line; non conserva array update, condizioni o valori.
- Il fallback locale di `hades_backend_graph_search` trova questi edge da cache
  artifact anche con backend remoto offline.

Resta fuori da questa tranche:

- Inferenza completa di dispatch observer/eventi per ogni operazione model;
  questa tranche espone il fatto route/controller -> model instance write in
  modo traversabile e combinabile con `observed_by_method`.

Verifiche eseguite:

- Locale mirato graph + provider:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_model_instance_operation_edges`
  passato: `2 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `109 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel middleware parameter graph Hades - 2026-07-08

Stato: completata una tranche locale P0-4 per route middleware parameter
awareness metadata-only.

Integrazione locale:

- `hades.php_graph.v1` conserva i target middleware esistenti e aggiunge
  `middleware_params` strutturati per middleware parametrizzati come
  `throttle:60,1`.
- I parametri vengono salvati solo quando sono safe token bounded
  (`[A-Za-z0-9_.-]`, massimo 8 valori), evitando payload arbitrari.
- I parametri sono disponibili su `route_middleware`,
  `route_middleware_class` e `route_middleware_method`, quindi una ricerca
  source-free puo' collegare `route -> throttle -> ThrottleRequests@handle`
  con i limiti configurati.
- Il fallback locale di `hades_backend_graph_search` espone
  `middleware_params` da cache artifact anche a backend offline.

Resta fuori da questa tranche:

- Interpretazione semantica dei parametri per ogni middleware custom; questa
  tranche rende disponibili i token configurati senza fingere conoscenza del
  significato applicativo.

Verifiche eseguite:

- Locale mirato graph + provider:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_middleware_parameter_edges tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_middleware_method_edges`
  passato: `3 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `110 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel Blade route graph Hades - 2026-07-08

Stato: completata una tranche locale P0-4 per Blade route reference awareness
metadata-only.

Integrazione locale:

- `hades.php_graph.v1` aggiunge edge `blade_route_ref` per chiamate
  `route('name')` dentro template Blade.
- Il target e' `route:<name>`, quindi una traversata source-free puo'
  collegare una view/rendered form alla route/controller nominata.
- Il payload salva solo route name, view id, path e line; non conserva
  parametri route, form body o source raw del template.
- Il fallback locale di `hades_backend_graph_search` espone `route_name` da
  cache artifact anche a backend offline.

Resta fuori da questa tranche:

- Analisi completa di metodi HTTP Blade/form spoofing e parametri route; questa
  tranche collega gia' il template alla route nominata senza salvare payload.

Verifiche eseguite:

- Locale mirato graph + provider:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_blade_route_refs`
  passato: `2 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `111 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel Blade form metadata graph Hades - 2026-07-08

Stato: completata una tranche locale P0-4 per Blade form method/CSRF awareness
metadata-only.

Integrazione locale:

- `hades.php_graph.v1` aggiunge `blade_form_method` per `@method('...')` nei
  template Blade e `blade_csrf_token` per `@csrf`/`csrf_field()`.
- Questo rende diagnosticabili source-free classi di bug 405/419 quando una
  view punta a una route ma usa method spoofing o token CSRF inattesi.
- Il payload salva solo view id, metodo HTTP spoofato o presenza CSRF, path e
  line; non conserva form body, parametri o sorgente raw.
- Il fallback locale di `hades_backend_graph_search` espone
  `form_method`/`csrf` da cache artifact anche a backend offline.

Resta fuori da questa tranche:

- Correlazione automatica tra singola form, route target e metodo route
  accettato; questa tranche espone i fatti minimi per la traversata source-free.

Verifiche eseguite:

- Locale mirato graph + provider:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_blade_form_metadata`
  passato: `2 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `112 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel Blade form route-method graph Hades - 2026-07-08

Stato: completata una tranche locale P0-4 per correlazione Blade form route
method metadata-only.

Integrazione locale:

- `hades.php_graph.v1` aggiunge `blade_form_route_method` quando una stessa
  form Blade contiene `route('name')` e `@method('...')`, e la route nominata
  e' nota al graph.
- L'edge collega la view alla route nominata e salva `form_method`,
  `route_method` e `route_method_match`, rendendo diagnosticabili casi 405
  source-free.
- Il payload salva solo route name, form method, route method, match boolean,
  path e line; non conserva form body, parametri route o source raw.
- Il fallback locale di `hades_backend_graph_search` espone questi dettagli da
  cache artifact anche a backend offline.

Resta fuori da questa tranche:

- Parsing completo di form annidate/HTML malformato e inferenza su action URL
  non generate da `route('name')`; questa tranche resta conservativa sui casi
  Blade standard.

Verifiche eseguite:

- Locale mirato graph + provider:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_blade_form_route_method_edges`
  passato: `2 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `113 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel Blade native form method graph Hades - 2026-07-08

Stato: completata una tranche locale P0-4 per correlazione Blade form route
method anche su metodi HTML nativi metadata-only.

Integrazione locale:

- `hades.php_graph.v1` estende `blade_form_route_method` ai form Blade che
  contengono `route('name')` e un attributo HTML `method="GET|POST"`.
- Se e' presente `@method(...)`, il metodo spoofato resta il metodo effettivo;
  se manca `@method`, viene usato il metodo HTML; se manca anche `method`,
  viene usato il default HTML `GET` per la sola correlazione route-method.
- L'edge salva route name, metodo form effettivo, metodo route,
  `route_method_match`, path e line; non conserva action completa, parametri
  route, form body o source raw.
- Il fallback locale di `hades_backend_graph_search` espone gia' questi campi
  da cache artifact anche a backend offline.

Resta fuori da questa tranche:

- Parsing HTML completo per form malformati, metodi non standard e route action
  generate dinamicamente; la tranche resta limitata ai pattern Blade standard.

Verifiche eseguite:

- Locale mirato graph + provider:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_blade_form_route_method_edges`
  passato: `2 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `113 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel Blade route parameter graph Hades - 2026-07-08

Stato: completata una tranche locale P0-4 per route parameter awareness nei
template Blade metadata-only.

Integrazione locale:

- `hades.php_graph.v1` aggiunge edge `blade_route_param` per chiavi array
  letterali passate a `route('name', ['param' => ...])`.
- Per route note con parametri URI richiesti, quando una call Blade
  `route('name')` non passa argomenti viene prodotto `route_param_status=missing`.
- Gli edge salvano solo route name, param name, status `provided`/`missing`,
  boolean `route_param_required` e `route_param_match`, path e line; non
  conservano valori parametri, action complete, query string o source raw.
- Il fallback locale di `hades_backend_graph_search` espone questi dettagli da
  cache artifact anche a backend offline.

Resta fuori da questa tranche:

- Inferenza su parametri dinamici non array, spread, helper custom e default
  Laravel complessi; la tranche resta conservativa per evitare falsi positivi.

Verifiche eseguite:

- Locale mirato graph + provider:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_blade_route_params`
  passato: `2 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `114 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel Blade authorization graph Hades - 2026-07-08

Stato: completata una tranche locale P0-4 per authorization awareness nei
template Blade metadata-only.

Integrazione locale:

- `hades.php_graph.v1` aggiunge edge `blade_authorization` per direttive
  Blade `@can`, `@cannot`, `@elsecan` e `@elsecannot` con ability letterale
  safe.
- L'edge collega la view a `ability:<name>` e salva solo `ability`,
  `authorization_helper`, path e line, cosi' una diagnosi source-free puo'
  correlare UI nascosta/mostrata con policy/controller/FormRequest gia'
  presenti nel graph.
- Il fallback locale di `hades_backend_graph_search` espone questi dettagli da
  cache artifact anche a backend offline.

Resta fuori da questa tranche:

- Array di ability, espressioni dinamiche, model argument e variabili Blade;
  questa tranche indicizza solo ability singole letterali sicure.

Verifiche eseguite:

- Locale mirato graph + provider:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_blade_authorization_edges`
  passato: `2 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `115 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel Blade canany authorization graph Hades - 2026-07-08

Stato: completata una tranche locale P0-4 per authorization awareness di
array ability nei template Blade metadata-only.

Integrazione locale:

- `hades.php_graph.v1` estende edge `blade_authorization` a direttive Blade
  `@canany` e `@elsecanany` con array di ability letterali safe.
- Ogni ability letterale produce un edge separato verso `ability:<name>` e
  salva solo `ability`, `authorization_helper`, path e line.
- Il parser resta bounded e non conserva array raw, expression dinamiche,
  variabili Blade contenenti ability o template/source raw.
- Il fallback locale di `hades_backend_graph_search` espone gli edge canany da
  cache artifact anche a backend offline.

Resta fuori da questa tranche:

- Risoluzione del model argument, array costruiti dinamicamente, variabili
  contenenti ability e correlazione completa con policy method signature.

Verifiche eseguite:

- Locale mirato graph + provider:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_blade_authorization_edges`
  passato: `2 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `118 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel Blade authorization subject graph Hades - 2026-07-08

Stato: completata una tranche locale P0-4 per collegare authorization Blade
al soggetto safe usato dalla policy.

Integrazione locale:

- `hades.php_graph.v1` aggiunge `authorization_subject` agli edge
  `blade_authorization` quando `@can`, `@cannot`, `@elsecan`, `@elsecannot`,
  `@canany` o `@elsecanany` ricevono un model argument variabile semplice,
  ad esempio `$order`.
- L'edge continua a collegare la view a `ability:<name>` e salva solo ability,
  helper, subject variable name, path e line.
- Il parser ignora espressioni, property access, array dinamici, valori raw e
  template/source raw.
- Il fallback locale di `hades_backend_graph_search` include
  `authorization_subject` nei summary, cosi' l'agent puo' cercare UI
  authorization su `order` anche a backend offline.

Resta fuori da questa tranche:

- Risoluzione type-aware del subject verso `App\Models\Order`, collegamento
  automatico Blade subject -> route model binding -> policy class, e signature
  analysis dei metodi policy.

Verifiche eseguite:

- Locale mirato graph + provider:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_blade_authorization_edges`
  passato: `2 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `118 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel Blade form field graph Hades - 2026-07-08

Stato: completata una tranche locale P0-4 per collegare template Blade,
campi form e validation metadata-only.

Integrazione locale:

- `hades.php_graph.v1` aggiunge edge `blade_form_field` per tag
  `input/select/textarea` con `name="..."` safe.
- Aggiunge `blade_old_input` per `old('field')` e `blade_validation_error`
  per `@error('field')`, cosi' una diagnosi source-free puo' correlare view,
  campi request e `route_request_validation`.
- Gli edge salvano solo field name, tag/helper, path e line; non conservano
  valori input, messaggi errore, markup completo o source raw.
- Il fallback locale di `hades_backend_graph_search` espone questi dettagli da
  cache artifact anche a backend offline.

Resta fuori da questa tranche:

- Parsing completo di componenti custom, `wire:model`, array field dinamici e
  value/default; questa tranche resta su attributi e helper letterali safe.

Verifiche eseguite:

- Locale mirato graph + provider:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_blade_form_field_edges`
  passato: `2 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `116 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel Blade Livewire wire:model graph Hades - 2026-07-08

Stato: completata una tranche locale P0-4 per Livewire field binding awareness
nei template Blade metadata-only.

Integrazione locale:

- `hades.php_graph.v1` aggiunge edge `blade_wire_model` per attributi
  `wire:model` e varianti con modifiers safe bounded come `.defer`.
- L'edge collega la view a `livewire_property:<name>` e salva solo property,
  lista modifiers, path e line, cosi' una diagnosi source-free puo' correlare
  campi Livewire a validation/request graph senza leggere il template.
- Il fallback locale di `hades_backend_graph_search` espone questi dettagli da
  cache artifact anche a backend offline.

Resta fuori da questa tranche:

- Mapping classe Livewire/metodi action, `wire:click`, Alpine `x-model` e
  property dinamiche; questa tranche resta limitata a `wire:model` letterali.

Verifiche eseguite:

- Locale mirato graph + provider:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_blade_wire_model_edges`
  passato: `2 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `117 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel Blade Livewire wire action graph Hades - 2026-07-08

Stato: completata una tranche locale P0-4 per Livewire action awareness nei
template Blade metadata-only.

Integrazione locale:

- `hades.php_graph.v1` aggiunge edge `blade_wire_action` per attributi
  `wire:click`, `wire:submit`, `wire:change`, `wire:keydown`, `wire:keyup`,
  `wire:blur` e `wire:focus` con action name letterale safe.
- L'edge collega la view a `livewire_action:<name>` e salva solo action,
  event, modifiers safe bounded, path e line; non conserva argomenti, payload
  o expression raw.
- Il fallback locale di `hades_backend_graph_search` espone questi dettagli da
  cache artifact anche a backend offline.

Resta fuori da questa tranche:

- Mapping alla classe Livewire e verifica che il metodo esista; questa tranche
  rende cercabili le azioni invocate dalla view senza leggere template/source.

Verifiche eseguite:

- Locale mirato graph + provider:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_blade_wire_action_edges`
  passato: `2 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `118 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel Livewire class/action method graph Hades - 2026-07-08

Stato: completata una tranche locale P0-4 per collegare Livewire Blade refs a
classi e metodi PHP metadata-only.

Integrazione locale:

- `hades.php_graph.v1` riconosce classi Livewire convenzionali in
  `app/Livewire` e `app/Http/Livewire`, includendole come ruolo
  `livewire_component`.
- Aggiunge edge `livewire_component_class` da alias Blade come
  `livewire:orders-status` alla classe PHP risolta per convenzione.
- Quando una view referenzia un componente Livewire e contiene una
  `blade_wire_action` con action name che esiste come metodo pubblico della
  classe, aggiunge `blade_wire_action_method` verso il symbol metodo.
- Il payload salva solo alias, classe, action, path e line; non conserva
  argomenti, payload Livewire o template/source raw.

Resta fuori da questa tranche:

- Component discovery registrata dinamicamente, namespace custom non
  convenzionali e disambiguazione di view con piu' componenti che espongono la
  stessa action.

Verifiche eseguite:

- Locale mirato graph + provider:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_blade_wire_action_edges`
  passato: `2 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `118 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel Livewire wire:model property graph Hades - 2026-07-08

Stato: completata una tranche locale P0-4 per collegare `wire:model` alle
public property Livewire metadata-only.

Integrazione locale:

- `hades.php_graph.v1` estende l'indice Livewire con public properties
  dichiarate sulle classi component risolte per convenzione.
- Aggiunge edge `blade_wire_model_property` quando un binding `wire:model`
  nella view corrisponde a una public property della classe Livewire
  referenziata dalla stessa view.
- L'edge salva solo alias, classe, binding, property, tipo dichiarato safe,
  path e line; non conserva valori runtime, default property o template/source
  raw.
- Il fallback locale di `hades_backend_graph_search` espone questi dettagli da
  cache artifact anche a backend offline.

Resta fuori da questa tranche:

- Property dinamiche, computed properties e binding nested oltre al root
  property match; questi casi restano conservativi per evitare falsi positivi.

Verifiche eseguite:

- Locale mirato graph + provider:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_blade_wire_model_edges`
  passato: `2 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `118 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel Livewire validation graph Hades - 2026-07-08

Stato: completata una tranche locale P0-4 per collegare validation rules
Livewire ai componenti e ai binding `wire:model` metadata-only.

Integrazione locale:

- `hades.php_graph.v1` estende l'indice Livewire con validation fields estratti
  da property `$rules` e metodo `rules()` sui componenti convenzionali.
- Aggiunge edge `livewire_validation` dalla classe componente a
  `validation:<field>` con rule names safe.
- Aggiunge edge `blade_wire_model_validation` quando un binding `wire:model`
  nella view referenzia un root property validato dal componente Livewire
  presente nella stessa view.
- Il payload salva solo alias, classe, binding, field, rule names, path e line;
  non conserva valori runtime, default property, argomenti Livewire o
  template/source raw.
- Il fallback locale di `hades_backend_graph_search` espone questi dettagli da
  cache artifact anche a backend offline.

Resta fuori da questa tranche:

- Validation rules dinamiche costruite a runtime, closure rules e binding nested
  oltre al root field match; questi casi restano conservativi per evitare falsi
  positivi source-free.

Verifiche eseguite:

- Locale mirato graph + provider:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_blade_wire_model_edges`
  passato: `2 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `118 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel Livewire nested validation graph Hades - 2026-07-08

Stato: completata una tranche locale P0-4 per correlare binding Livewire
nested a validation fields nested metadata-only.

Integrazione locale:

- `blade_wire_model_validation` ora considera sia il match root gia' coperto
  sia il match esatto tra binding e validation field, ad esempio
  `wire:model="order.status"` verso `validation:order.status`.
- Il matcher supporta anche wildcard segment-safe nei validation field, cosi'
  un pattern come `items.*.name` puo' collegarsi a binding con la stessa forma
  segmentata senza conservare payload runtime.
- `blade_wire_model_property` continua a puntare alla root public property,
  mentre `blade_wire_model_validation` conserva il field validato completo.
- Il payload salva solo alias, classe, binding, root property, field, rule
  names, path e line; non conserva valori runtime, default property, argomenti
  Livewire o template/source raw.
- Il fallback locale di `hades_backend_graph_search` espone questi dettagli da
  cache artifact anche a backend offline.

Resta fuori da questa tranche:

- Validation rules costruite dinamicamente a runtime e disambiguazione tra piu'
  componenti Livewire con lo stesso field nested validato nella stessa view.

Verifiche eseguite:

- Locale mirato graph + provider:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_blade_wire_model_edges`
  passato: `2 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `118 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel Blade Alpine x-model graph Hades - 2026-07-08

Stato: completata una tranche locale P0-4 per rendere cercabile lo stato
frontend Alpine nei template Blade metadata-only.

Integrazione locale:

- `hades.php_graph.v1` aggiunge edge `blade_alpine_model` per attributi
  `x-model` con state path letterale safe e modifiers bounded.
- L'edge collega la view a `alpine_state:<path>` e salva solo state path,
  modifiers, path e line.
- Il parser resta conservativo: non conserva expression raw, payload runtime o
  template/source raw e ignora binding dinamici non letterali.
- Il fallback locale di `hades_backend_graph_search` espone `alpine_model` e
  `alpine_modifiers` nei summary da cache artifact anche a backend offline.

Resta fuori da questa tranche:

- `x-data`, `x-on`/`@click`, store Alpine dinamici e correlazione con form submit
  o Livewire action; questa tranche copre solo binding `x-model` letterali.

Verifiche eseguite:

- Locale mirato graph + provider:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_blade_wire_model_edges`
  passato: `2 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `118 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel Blade Alpine action graph Hades - 2026-07-08

Stato: completata una tranche locale P0-4 per rendere cercabili gli handler
frontend Alpine nei template Blade metadata-only.

Integrazione locale:

- `hades.php_graph.v1` aggiunge edge `blade_alpine_action` per attributi
  `x-on:event` e shorthand `@event` con event bounded e handler letterale safe.
- L'edge collega la view a `alpine_action:<handler>` e salva solo handler,
  event, modifiers, path e line.
- Il parser resta conservativo: non conserva expression raw, payload runtime o
  template/source raw e ignora handler dinamici o expression assignment.
- Il fallback locale di `hades_backend_graph_search` espone `alpine_action`,
  `alpine_event` e `alpine_modifiers` nei summary da cache artifact anche a
  backend offline.

Resta fuori da questa tranche:

- Parsing di `x-data`, store Alpine dinamici, `$dispatch` payload, assignment
  expressions e correlazione causale con submit/Livewire action; questa tranche
  copre solo handler letterali safe.

Verifiche eseguite:

- Locale mirato graph + provider:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_blade_wire_model_edges`
  passato: `2 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `118 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel Blade Alpine x-data graph Hades - 2026-07-08

Stato: completata una tranche locale P0-4 per rendere cercabile lo stato
iniziale dichiarato da Alpine nei template Blade metadata-only.

Integrazione locale:

- `hades.php_graph.v1` aggiunge edge `blade_alpine_data` per chiavi top-level
  di oggetti `x-data` letterali.
- L'edge collega la view a `alpine_state:<key>` e salva solo key, source type,
  path e line.
- Il parser ignora valori, nested keys, expression raw, payload runtime e
  template/source raw; questo evita di conservare default sensibili o logica JS
  inline.
- Il fallback locale di `hades_backend_graph_search` espone `alpine_data_key`
  e `alpine_data_source` nei summary da cache artifact anche a backend offline.

Resta fuori da questa tranche:

- `x-data` factory dinamiche, store Alpine globali, nested state graph completo,
  `$dispatch` payload e correlazione causale tra state, handler e submit.

Verifiche eseguite:

- Locale mirato graph + provider:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_blade_wire_model_edges`
  passato: `2 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `118 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel Blade Alpine model-data graph Hades - 2026-07-08

Stato: completata una tranche locale P0-4 per correlare binding `x-model` alla
root state dichiarata da `x-data` nella stessa view metadata-only.

Integrazione locale:

- `hades.php_graph.v1` aggiunge edge `blade_alpine_model_data` quando il root
  di un `x-model` coincide con una chiave top-level `x-data` nella stessa view.
- L'edge collega `alpine_state:<model>` a `alpine_state:<root>` e salva solo
  model, data key, path e line.
- Il parser resta conservativo: non conserva valori iniziali, nested values,
  expression raw, payload runtime o template/source raw.
- Il fallback locale di `hades_backend_graph_search` espone la correlazione da
  cache artifact anche a backend offline.

Resta fuori da questa tranche:

- Correlazione multi-componente Alpine, scope DOM annidati, store globali e
  data-flow causale fra handler, state e submit.

Verifiche eseguite:

- Locale mirato graph + provider:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_blade_wire_model_edges`
  passato: `2 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `118 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.

## Esecuzione Laravel Blade Alpine x-data factory graph Hades - 2026-07-08

Stato: completata una tranche locale P0-4 per rendere cercabili factory Alpine
referenziate da `x-data` metadata-only.

Integrazione locale:

- `hades.php_graph.v1` aggiunge edge `blade_alpine_data_factory` per `x-data`
  factory/identifier safe senza argomenti, ad esempio `filtersForm()`.
- L'edge collega la view a `alpine_factory:<name>` e salva solo factory name,
  source type, path e line.
- Il parser resta conservativo: non conserva argomenti, expression raw, body JS,
  payload runtime o template/source raw.
- Il fallback locale di `hades_backend_graph_search` espone
  `alpine_data_factory` e `alpine_data_source` nei summary da cache artifact
  anche a backend offline.

Resta fuori da questa tranche:

- Risoluzione della factory JS/PHP, argomenti dinamici, store Alpine globali e
  data-flow causale fra factory, handler, state e submit.

Verifiche eseguite:

- Locale mirato graph + provider:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_extracts_laravel_php_graph_without_source tests/agent/test_hades_backend_memory_provider.py::test_hades_backend_graph_search_finds_local_blade_wire_model_edges`
  passato: `2 passed`.
- Locale graph/provider/docs:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py tests/test_docs_hades_mvp.py`
  passato: `118 passed`.
- Locale lint/compile:
  `.venv/bin/ruff check hermes_cli/hades_backend_jobs.py plugins/memory/hades_backend/__init__.py tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`
  passato; `py_compile` sugli stessi file passato; `git diff --check` passato.
