# Hades Durable OrgRun Design

## Decisione

Hades deve eseguire batch di task remoti attraverso il Kanban locale senza
introdurre un secondo scheduler. Il backend resta autoritativo per mandato,
priorità, acceptance criteria, revisioni e cancellazione. Il runtime locale
resta autoritativo per piano esecutivo, DAG, worktree, run, evidence e verdict
di review fino all'accettazione del risultato remoto.

`OrgRun` è inizialmente un aggregato logico sopra le primitive Kanban
esistenti. Non è un nuovo servizio, non ha un nuovo model tool e non introduce
subito tabelle `org_*`.

## Obiettivi

1. Ingerire più task remoti dello stesso progetto senza claimarli al pull.
2. Costruire un portfolio locale, rilevare conflitti e limitare concorrenza.
3. Materializzare il portfolio nel Kanban esistente in modo idempotente.
4. Claimare ogni work item remoto immediatamente prima del primo lavoro
   mutante che lo riguarda.
5. Conservare localmente dettagli esecutivi e inviare al backend solo eventi
   semantici redatti.
6. Coordinare worker tramite eventi tipizzati, non tramite chat libera.
7. Integrare e verificare patch in un gate indipendente prima della completion
   remota.

## Non-obiettivi del primo rilascio

- Nessuna chat mesh tra sibling.
- Nessun nuovo tool core `org_*`.
- Nessun nuovo scheduler parallelo a `kanban_db.dispatch_once`.
- Nessun auto-merge o auto-push Git.
- Nessuna pubblicazione automatica di project memory.
- Nessun claim batch atomico finché il backend non espone un contratto
  versionato per farlo.
- Nessuna modifica dinamica del system prompt o del toolset di una sessione.

## Autorità per campo

| Dominio | Autorità | Regola |
|---|---|---|
| Mandato remoto | Backend | ID, descrizione, acceptance, priorità, rischio |
| Lifecycle remoto | Backend | revision, readiness, cancellation, lease |
| Piano di implementazione | Locale | non viene caricato integralmente sul backend |
| DAG e profili | Locale | restano nel Kanban locale |
| Run, log e worktree | Locale | mai sincronizzati raw |
| Risultato proposto | Locale | diventa remoto solo dopo accepted completion |
| Project memory | Backend | modificata soltanto tramite proposal revisionata |

## Topologia Kanban

Una root Kanban completata immediatamente è l'anchor/blackboard dell'OrgRun.
Per ogni work item remoto si creano nodi distinti:

```text
OrgRun anchor (done, blackboard)
  |
  +-- Remote anchor HD-101 (done, identity/audit)
  |     |
  |     +-- Execute HD-101 (ready/running)
  |           |
  |           +-- Review HD-101
  |                 |
  |                 +-- Ready HD-101 for integration
  |
  +-- Remote anchor HD-102 (stessa topologia a quattro nodi)
  |
  +-- Integration (depends on all ready-for-integration nodes)
        |
        +-- Org review
              |
              +-- Publish HD-101  <-- remote completion node
              +-- Publish HD-102  <-- remote completion node
                    |
                    +-- Final synthesis
```

L'anchor remoto e il completion node non devono essere confusi. Il primo è
un punto durevole di identità; il secondo viene sbloccato soltanto dopo
integration e org review e rappresenta la fine verificata del subtree.
Questo evita di lasciare una parent card `running` mentre i child dipendono da
essa, situazione incompatibile con il parent-gating del Kanban attuale.

## Identità e storage

Il database Kanban conserva soltanto task, link, commenti, eventi e run. Il
database `hades_backend.db` conserva mapping Hades-specifici, lease e outbox.

Chiave del mapping:

```text
(backend_origin, project_id, work_item_id, workspace_binding_id, board)
```

Campi minimi:

```text
org_run_id
local_anchor_task_id
local_execution_task_id
local_completion_task_id
remote_revision
sync_state
last_remote_cursor
```

Il lease token resta nella tabella locale Hades già dedicata ai plugin work
item e non entra mai in card, commenti o event payload Kanban.

## Flusso dati

```text
pull bounded
  -> validate hades.kanban_task_work.v1/v2
  -> upsert cache Hades
  -> create portfolio locale
  -> validate base commit, DAG e overlap
  -> materialize OrgRun in triage/todo
  -> promote executable nodes
  -> local claim
  -> remote admission/claim
  -> spawn worker
  -> local evidence + review + integration
  -> semantic outbox
  -> backend complete/block/fail
```

Un remote claim rifiutato è una condizione di coordinamento, non un worker
failure. Il nodo locale viene reso non eseguibile e l'OrgRun viene ricalcolato
senza consumare il failure budget.

## Sincronizzazione opzionale dei Kanban

Il Kanban locale deve continuare a funzionare senza backend. La
sincronizzazione è opt-in e usa tre modalità in `config.yaml`:

```yaml
backend:
  kanban_sync:
    mode: off               # off | pull_only | mirror
    pull_interval_seconds: 30
    push_interval_seconds: 60
    offline_grace_seconds: 120
    execution_assignee: null
    reviewer_assignee: null
    integration_assignee: null
```

- `off` è il default: nessuna lettura o scrittura remota; il board è locale.
- `pull_only` importa/upserta task remoti mappati, ma non li claim-a e non
  invia progress o completion. Serve per dry-run e verifica del mapping.
- `mirror` importa task remoti e invia soltanto lifecycle/progress/result
  bounded per i task che possiedono un mapping remoto.

Le tre assignee devono riferirsi a profili Hermes realmente esistenti prima di
abilitare `mirror`; non esiste fallback silenzioso. `pull_only` può importare
task in `triage` anche senza profili configurati.

La modalità `direct` del worker Hades esistente non è una modalità di sync:
rimane un execution path separato e compatibile durante la migrazione.

Quando si passa da `mirror` a `off`, eventuali card remote già mappate vengono
bloccate localmente con reason `backend_sync_disabled`; non viene creato alcun
client e non avviene alcuna call di rete. Le card local-only continuano invece
a essere dispatchabili.

La sincronizzazione non deve mai caricare automaticamente sul backend task
creati soltanto nel Kanban locale. L'unico outbound ammesso riguarda card con
mapping remoto esplicito. Il comando diagnostico deve supportare sempre
`--dry-run` e mostrare create, update, cancellation, conflict e outbound senza
mutare nessuno dei due lati.

## Ammissione al dispatch

Gli hook Kanban esistenti sono observer best-effort e non possono impedire lo
spawn. Il bridge richiede quindi una seam generica e sincrona nel dispatcher:

```python
DispatchAdmission(action="allow" | "defer" | "supersede", reason="backend offline")
```

La callback viene eseguita dopo il claim atomico locale e prima della
risoluzione workspace/spawn. `defer` rilascia la run locale senza failure e
riporta il task a `ready`; `supersede` chiude la run come `released` e sposta
la card in `blocked` con reason tipizzata. Se non viene fornita una callback,
il comportamento del dispatcher resta byte-for-byte equivalente.

## Conflitti di scope

Nel primo rilascio gli scope sono advisory e deterministici:

- path canonicalizzati e relativi alla repo;
- overlap tra writer paralleli produce serializzazione nel DAG;
- cambi a API, schema, config globale o contratto pubblico richiedono review;
- i code claim Hades già esistenti forniscono segnale cross-device, ma non
  sostituiscono il gate locale;
- il merge e i test sull'integration worktree restano l'autorità finale.

Non vengono introdotti lock dinamici a simbolo nel primo rilascio.

## Comunicazione

Il protocollo v1 estende il blackboard strutturato già usato da
`kanban_swarm`. I tipi ammessi sono:

```text
fyi
handoff
blocker
decision_proposal
decision_resolution
interface_change
review_request
integration_notice
```

Ogni messaggio contiene summary bounded, scope, related task IDs,
`required_action` ed evidence refs. L'inbox è inizialmente una query/proiezione
degli eventi e commenti; non ha una tabella dedicata.

Gli standup sono snapshot derivati dallo stato Kanban. Non vengono generati
periodicamente da un LLM.

## Error handling

- Contract drift: non materializzare; mantenere item remoto non claimato.
- Claim remoto perso: `supersede`, nessun retry loop, replan bounded.
- Backend offline prima del claim: `defer`, nessun lavoro mutante.
- Backend offline dopo il claim: grace window, poi safe stop.
- Completion offline: outbox idempotente.
- Lease stale: non completare; conservare risultato locale e richiedere audit.
- Cancellation remota: stop del subtree al checkpoint successivo; SIGTERM solo
  per eventi critici.
- Decisione pendente che impatta scope: il task non è dispatchabile.

## Sicurezza e caching

- Nessun secret, reasoning, transcript, raw source o path assoluto nei payload.
- Nessuna configurazione comportamentale tramite nuove variabili `HERMES_*`.
- Nessun cambio di modello nella stessa conversazione.
- Profili e routing restano locali in `config.yaml`.
- I nuovi comandi sono CLI/skill o tool Kanban service-gated, mai tool core.

## Strategia di rilascio

1. P0: contratti e seam generiche con comportamento legacy invariato.
2. P1: OrgRun locale, nessuna rete.
3. P2: mirror backend opt-in, default ancora `direct`.
4. P3: comunicazione tipizzata e integration gate.
5. P4: routing e budget della delegazione effimera.

Ogni fase ha un feature flag in `config.yaml`, test E2E su `HERMES_HOME`
temporaneo e rollback che lascia leggibili DB e artifact esistenti.

## Success criteria

Il prototipo è accettato quando uno scenario con quattro work item, due scope
in conflitto, un claim remoto perso e una modifica d'interfaccia intenzionale:

1. non avvia writer incompatibili in parallelo;
2. non conta il claim perso come worker failure;
3. notifica e blocca il consumer della modifica d'interfaccia;
4. sopravvive al restart prima della completion;
5. integra e verifica le patch in ordine deterministico;
6. invia un solo risultato bounded per work item;
7. non invia dati vietati al backend.
