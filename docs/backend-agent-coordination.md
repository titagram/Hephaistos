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
