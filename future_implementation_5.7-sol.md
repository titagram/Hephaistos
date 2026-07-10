# Durable OrgRun and Optional Backend Kanban Sync

## Stato del documento

- **Tipo:** roadmap esecutiva successiva a `future_implementation_5.6-sol.md`
- **Data:** 2026-07-10
- **Obiettivo:** eseguire batch di task remoti in un Kanban locale durevole,
  opzionalmente sincronizzato con il Kanban backend
- **Regola architetturale:** estendere Kanban; non creare un secondo scheduler
- **Spec autoritativa:**
  `docs/superpowers/specs/2026-07-10-hades-durable-org-run-design.md`

## 1. Decisione

La release viene divisa in cinque work package indipendenti e ordinati. Ogni
package deve essere completato, verificato e committato prima di iniziare il
successivo.

| Priorità | Work package | Risultato utilizzabile |
|---|---|---|
| P0 | Contratti e dispatch admission | Seam sicura; zero variazioni legacy |
| P1 | OrgRun locale | Portfolio durevole senza backend |
| P2 | Sync Kanban opzionale | `off`, `pull_only`, `mirror` |
| P3 | Coordinamento e integrazione | Eventi tipizzati, review e merge gate |
| P4 | Delegazione effimera | Routing per ruolo e budget tree-wide |

Il valore principale arriva a P1: il sistema può già coordinare batch locali.
P2 aggiunge il backend senza renderlo obbligatorio. P3 aumenta sicurezza e
qualità dopo aver osservato casi reali. P4 ottimizza costo e latenza ma non è
necessario per la correttezza del percorso durevole.

## 2. Protocollo obbligatorio per agenti esecutori

Queste regole prevalgono su qualunque scorciatoia proposta dall'esecutore.

### 2.1 Prima di ogni task

Eseguire esattamente:

```bash
pwd
git branch --show-current
git status --short
```

Risultato atteso:

- `pwd` termina con `/Hephaistos`;
- il branch non è vuoto;
- `git status --short` non mostra modifiche inattese.

Se appare un file modificato non elencato nel task corrente: **STOP** con
codice `unexpected_dirty_worktree`. Non eseguire reset, checkout o clean.

### 2.2 Durante ogni task

1. Modificare soltanto i file elencati in `Files`.
2. Scrivere prima il test indicato.
3. Eseguire il singolo test e confermare il failure indicato.
4. Se fallisce per una ragione diversa: **STOP** con
   `unexpected_red_state`.
5. Implementare soltanto il codice necessario al test.
6. Eseguire il test focalizzato.
7. Eseguire la regressione indicata.
8. Ispezionare `git diff --check` e `git diff --stat`.
9. Committare soltanto i file del task.

### 2.3 Divieti

- Non inventare endpoint backend non presenti nel contratto verificato.
- Non cambiare nomi di funzioni, enum, status o config key mostrati nel piano.
- Non aggiungere nuove variabili ambiente `HERMES_*`.
- Non aggiungere model tool core.
- Non aggiungere dipendenze Python o JavaScript.
- Non modificare `run_agent.py`, `model_tools.py` o `toolsets.py` salvo un task
  che li nomini esplicitamente.
- Non trasformare un `blocked`, `defer` o claim conflict in `failed`.
- Non inviare source, reasoning, transcript, secret, lease token o path
  assoluti al backend.
- Non proseguire al work package successivo se il release gate corrente non è
  completamente verde.

### 2.4 Formato di handoff obbligatorio

Al termine di ogni task produrre:

```json
{
  "task": "P1-T03",
  "status": "completed",
  "commit": "<sha>",
  "changed_files": [],
  "tests": [
    {"command": "scripts/run_tests.sh tests/hermes_cli/test_kanban_portfolio.py -q", "result": "pass"}
  ],
  "scope_deviations": [],
  "residual_risks": []
}
```

Se `scope_deviations` non è vuoto, lo status deve essere `blocked`, non
`completed`.

## 3. Baseline verificata nel branch

Non reimplementare queste primitive:

- `hermes_cli.kanban_db`: task, link, commenti, eventi JSON, run, claim,
  heartbeat, retry e dispatcher;
- `hermes_cli.kanban_swarm`: root/blackboard, worker, verifier e synthesizer;
- `tools.kanban_tools`: lifecycle e commenti cross-task;
- `hermes_cli.hades_backend_db`: cache Hades, plugin work item e lease token;
- `hermes_cli.hades_plugin_work_items_client`: list, claim, heartbeat,
  complete e fail;
- `hermes_cli.hades_plugin_worker`: execution path remoto `direct`;
- `hermes_cli.hades_coordination`: presence e code claim con release in
  `finally`;
- lifecycle hook Kanban post-commit: observer-only, non vetoabile.

Premessa da non dimenticare: l'hook `kanban_task_claimed` scatta dopo il claim
locale e non può impedire lo spawn. Non è sufficiente per il claim remoto
tardivo; P0 introduce una admission seam esplicita.

## 4. Configurazione pubblica

Il solo blocco nuovo user-facing è:

```yaml
backend:
  kanban_sync:
    mode: off
    pull_interval_seconds: 30
    push_interval_seconds: 60
    offline_grace_seconds: 120
    max_batch_size: 20
    execution_assignee: null
    reviewer_assignee: null
    integration_assignee: null
```

Valori ammessi per `mode`:

- `off`: default, nessuna sincronizzazione; card remote già mappate vengono
  congelate localmente senza rete, card local-only restano eseguibili;
- `pull_only`: importa e mostra, nessun claim/push;
- `mirror`: pull, claim tardivo, progress/result bounded.

In `mirror` le tre assignee sono obbligatorie e devono essere profili Hermes
esistenti. In `pull_only` le card importate restano in `triage` e non richiedono
profili eseguibili.

Il path `direct` esistente rimane separato. Non è consentito chiamare
contemporaneamente il worker direct e il mirror sullo stesso
`local_workspace_id`; il comando diagnostico deve segnalarlo.

## 5. Work package P0 — Contratti e dispatch admission

**Dipendenze:** nessuna.

**Deliverable:** il dispatcher accetta una callback generica di admission dopo
il claim locale e prima dello spawn. Senza callback tutti i test legacy
continuano a passare e lo stato DB resta invariato.

Task:

1. `P0-T01`: congelare la baseline del dispatcher.
2. `P0-T02`: aggiungere `DispatchAdmission` e `release_active_claim`.
3. `P0-T03`: collegare `admission_fn` al dispatcher mantenendo compatibilità.

Piano dettagliato:
`docs/superpowers/plans/2026-07-10-hades-org-run-p0-p1-local.md`.

Nel piano dettagliato seguire esclusivamente l'ordine dichiarato
`P0-T01 -> P0-T02 -> P0-T03 -> P1-T01 -> P1-T02 -> P1-T03 -> P1-T04`,
indipendentemente dalla posizione fisica delle sezioni nel file.

### Release gate P0

```bash
scripts/run_tests.sh \
  tests/hermes_cli/test_kanban_db.py \
  tests/hermes_cli/test_kanban_dispatch_lock.py \
  tests/hermes_cli/test_kanban_lifecycle_hooks.py \
  tests/hermes_cli/test_kanban_cli_dispatch_passthrough.py \
  -q
```

Atteso: exit code `0`. Qualunque failure blocca P1.

## 6. Work package P1 — OrgRun locale

**Dipendenze:** P0 verde.

**Deliverable:** un portfolio `hades.execution-portfolio.v1` viene validato e
materializzato idempotentemente nel Kanban locale. Non avviene alcuna call di
rete.

Task:

1. `P1-T01`: parser e dataclass del portfolio.
2. `P1-T02`: validazione ID/DAG e conflict graph deterministico.
3. `P1-T03`: materializzatore OrgRun idempotente sopra Kanban.
4. `P1-T04`: CLI locale e E2E restart.

### Invarianti P1

- Nessuna tabella `org_*`.
- Nessun modulo `org_scheduler.py`.
- Anchor e completion node distinti per ogni remote task.
- Overlap tra writer serializzato, non semplicemente segnalato.
- Task locali senza remote mapping restano completamente supportati.
- Nessuna rete, nemmeno mockata, nel materializzatore.

### Release gate P1

```bash
scripts/run_tests.sh \
  tests/hermes_cli/test_hierarchical_execution.py \
  tests/hermes_cli/test_kanban_portfolio.py \
  tests/hermes_cli/test_kanban_swarm.py \
  tests/hermes_cli/test_kanban_core_functionality.py \
  -q
```

Atteso: exit code `0` e nessun task `running` nel DB temporaneo dopo il test
restart.

## 7. Work package P2 — Sync Kanban opzionale

**Dipendenze:** P1 verde e backend capability audit completato.

**Deliverable:** `off`, `pull_only` e `mirror` funzionano senza modificare il
path direct. Solo i task con mapping remoto esplicito vengono sincronizzati.

Task:

1. `P2-T01`: schema mapping e outbox in `hades_backend.db`.
2. `P2-T02`: capability contract e client method mancanti.
3. `P2-T03`: config strict e mutua esclusione direct/mirror.
4. `P2-T04`: pull/upsert/materializzazione idempotente.
5. `P2-T05`: CLI dry-run deterministica.
6. `P2-T06`: remote admission/claim tramite seam P0.
7. `P2-T07`: outbox, heartbeat, revision e cancellation.

Piano dettagliato:
`docs/superpowers/plans/2026-07-10-hades-org-run-p2-backend-sync.md`.

### Invarianti P2

- `mode=off` non crea thread, timer, nuovi mapping o network call e blocca
  localmente eventuali card remote preesistenti.
- `pull_only` non chiama claim, heartbeat, complete, block, fail o release.
- `mirror` non carica task local-only.
- Un claim conflict non incrementa `consecutive_failures`.
- Un lease token non compare in Kanban DB, log o JSON CLI.
- L'outbox usa idempotency key stabile e sopravvive al restart.
- Il default resta `off` per la sync e `direct` per il worker esistente.

### Release gate P2

```bash
scripts/run_tests.sh \
  tests/hermes_cli/test_hades_kanban_bridge_db.py \
  tests/hermes_cli/test_hades_kanban_bridge.py \
  tests/hermes_cli/test_hades_plugin_work_items_client.py \
  tests/hermes_cli/test_hades_plugin_worker.py \
  tests/hermes_cli/test_hades_kanban_sync_cli.py \
  tests/gateway/test_hades_kanban_sync_watcher.py \
  -q
```

Atteso: exit code `0`; test spy conferma zero chiamate outbound in `off` e
`pull_only`.

## 8. Work package P3 — Coordinamento tipizzato e integration gate

**Dipendenze:** almeno P1; P2 richiesto solo per scenari remoti.

**Deliverable:** worker e reviewer comunicano tramite eventi bounded; una
modifica d'interfaccia blocca i consumer impattati; integration e review
precedono il risultato finale.

Task:

1. `P3-T01`: schema `hades.org-message.v1`.
2. `P3-T02`: estensione tipizzata di `kanban_comment`.
3. `P3-T03`: inbox e decisioni come proiezioni.
4. `P3-T04`: composizione dei dispatch gate.
5. `P3-T05`: standup derivato e piano d'integrazione.
6. `P3-T05B`: code claim strict cross-device per writer.
7. `P3-T06`: E2E con modifica di contratto incompatibile.

Piano dettagliato:
`docs/superpowers/plans/2026-07-10-hades-org-run-p3-coordination.md`.

### Invarianti P3

- Nessun direct message arbitrario tra worker.
- Nessun worker legge l'intero blackboard se lo scope non lo riguarda.
- Ogni decisione possiede stato, approvatore e scope impattato.
- Gli standup sono pure projection dello stato persistito.
- I code claim vengono sempre rilasciati in `finally`.
- Merge/apply non avviene se reviewer o test gate non sono verdi.

## 9. Work package P4 — Delegazione effimera

**Dipendenze:** indipendente da P2/P3, ma da eseguire per ultimo per non
mescolare ottimizzazione di costo e correttezza del durable path.

**Deliverable:** `delegate_task` risolve profili allow-listed per ruolo e tutti
i discendenti condividono un budget atomico.

Task:

1. `P4-T01`: parser `delegation.profiles` e `role_routes`.
2. `P4-T02`: `DelegationTreeBudget` thread-safe con reservation/rollback.
3. `P4-T03`: wiring routing/budget in `delegate_task`.
4. `P4-T04`: skill `hierarchical-development` e gate E2E.

Il modello non riceve mai un parametro provider/model libero. Continua a
scegliere soltanto `role=leaf|orchestrator`; la config locale determina il
runtime.

## 10. Metriche release

Registrare per OrgRun:

- numero task remoti scoperti, mappati, claimati e persi;
- tempo speso in planning prima del claim;
- numero overlap statici e serializzazioni;
- parallelismo massimo reale;
- message amplification per task;
- decisioni aperte e latenza di risoluzione;
- retry, replan e safe stop;
- merge conflict rate;
- first-pass reviewer success;
- completion outbox latency;
- costo/token per ruolo;
- payload outbound rifiutati dalla redaction.

Non introdurre telemetry outbound. Queste metriche restano locali finché non
esiste un opt-in generico.

## 11. Rollback

- P0: omettere `admission_fn`; comportamento legacy.
- P1: archiviare l'OrgRun; le card restano leggibili.
- P2: impostare `backend.kanban_sync.mode: off`; l'outbox resta locale.
- P3: disabilitare i gate tipizzati; i commenti restano leggibili.
- P4: rimuovere `role_routes`; i child ereditano il runtime legacy.

Nessun rollback elimina DB, worktree, patch o evidence automaticamente.

## 12. Definition of done complessiva

La feature è completa soltanto quando:

1. tutti i release gate P0-P4 passano;
2. `mode=off` è indistinguibile dalla baseline per task local-only e network
   calls, mentre congela card remote mappate residue;
3. un OrgRun locale funziona senza backend;
4. `pull_only` non produce side effect remoti;
5. `mirror` gestisce claim conflict, offline, restart e cancellation;
6. una modifica d'interfaccia blocca correttamente un consumer parallelo;
7. reviewer e integration gate precedono ogni completion remota;
8. una scansione dei payload non trova secret, reasoning, transcript, source,
   lease token o path assoluti;
9. il path direct esistente continua a superare i suoi test;
10. documentazione e config reference descrivono chiaramente sync opt-in.
