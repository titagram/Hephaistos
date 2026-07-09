# Hierarchical Execution Power-Up

## Stato del documento

- **Tipo:** proposta di implementazione futura
- **Obiettivo:** usare un modello molto potente solo dove produce il massimo
  valore, delegando l'esecuzione bounded a modelli più piccoli ed economici
- **Ambito:** Hades/Hermes, `delegate_task`, skill, profili e Kanban
- **Principio guida:** planner forte, esecuzione economica, verifica indipendente

---

## 1. Sintesi

Hades possiede già quasi tutte le primitive necessarie per costruire una
gerarchia di agenti:

- ogni subagent creato da `delegate_task` è una `AIAgent` separata;
- ogni subagent ha conversazione, session id, task id, terminal session,
  toolset e iteration budget propri;
- il parent riceve soltanto il risultato sintetico, non il reasoning e le tool
  call intermedie;
- `role="orchestrator"` permette a un child di creare ulteriori child;
- `delegation.max_spawn_depth` limita la profondità dell'albero;
- la TUI conosce `subagent_id`, `parent_id` e profondità e può quindi
  rappresentare la topologia;
- Kanban offre una seconda forma di orchestrazione: un task graph durevole,
  persistito in SQLite e lavorato da profili separati.

La feature proposta non deve quindi inventare un nuovo sistema di agenti. Deve
completare e mettere in sicurezza le primitive esistenti, aggiungendo:

1. routing del modello per ruolo;
2. piano di esecuzione strutturato e validabile;
3. budget condiviso per tutto l'albero;
4. ownership esplicita di file e simboli;
5. gate deterministici e escalation;
6. una skill che scelga tra delegazione effimera e Kanban durevole;
7. integrazione con Kanban per task lunghi, dipendenti o restart-safe;
8. sincronizzazione del Kanban autoritativo del backend Hades nel Kanban
   locale dell'agent.

L'architettura target è:

```text
God-like planner
  |
  +-- crea e valida ExecutionPlan v1
  |
  +-- Maresciallo A, modello medio
  |     +-- Worker A1, modello piccolo
  |     +-- Worker A2, modello piccolo
  |     +-- integra e sintetizza A
  |
  +-- Maresciallo B, modello medio
  |     +-- Worker B1, modello piccolo
  |     +-- Worker B2, modello piccolo
  |     +-- integra e sintetizza B
  |
  +-- Verifier indipendente
  |
  +-- review finale o replan del God-like planner
```

Il modello più potente prende decisioni ad alto leverage. I modelli piccoli
eseguono task con spazio decisionale ridotto. Nessun worker deve essere costretto
a improvvisare quando il piano non corrisponde più alla realtà.

---

## 2. Stato attuale verificato

### 2.1 Skill installata

La skill official `subagent-driven-development` è installata e abilitata in:

```text
~/.hermes/skills/software-development/subagent-driven-development/SKILL.md
```

La skill implementa il pattern:

```text
piano -> implementer -> spec review -> quality review -> task successivo
```

È un'ottima base metodologica, ma oggi è prevalentemente piatta:

- chiama `delegate_task` senza `role="orchestrator"`;
- crea un subagent fresco per task;
- non costruisce una gerarchia maresciallo-worker;
- non assegna modelli diversi in base al ruolo;
- non definisce un budget globale condiviso dall'albero;
- assume un workflow sequenziale con review per-task.

Non va modificata direttamente: essendo una skill official installata, un
aggiornamento potrebbe sovrascrivere le personalizzazioni. Va creata una nuova
skill locale o bundled che la componga concettualmente.

### 2.2 Isolamento dei subagent

Il runtime `tools/delegate_tool.py` crea per ogni child:

- una nuova conversazione senza history del parent;
- un nuovo `session_id` collegato tramite `parent_session_id`;
- un nuovo `task_id` usato anche per terminal e file-state;
- un system prompt focalizzato su goal e context delegati;
- una nuova iteration budget;
- toolset ristretto;
- memoria disabilitata;
- context file non riletti automaticamente;
- callback e telemetria associate a un `subagent_id` stabile.

Il parent vede soltanto il summary finale. Questo protegge il contesto del
modello principale dall'accumulo di tool output e reasoning operativo.

L'isolamento non è assoluto:

- il filesystem del workspace può essere condiviso;
- task paralleli possono modificare gli stessi file;
- un maresciallo accumula i summary dei propri worker;
- la qualità del summary resta responsabilità del child;
- le delegazioni live sono process-local e non sopravvivono a un restart;
- i child non hanno memoria condivisa e devono ricevere tutto il contesto utile.

### 2.3 Delegazione annidata

La delegazione gerarchica è già supportata:

```yaml
delegation:
  max_spawn_depth: 2
  orchestrator_enabled: true
```

Con `max_spawn_depth: 2`:

```text
depth 0: parent principale
depth 1: child orchestrator
depth 2: worker leaf
```

Il child depth 1 conserva `delegate_task`; i child depth 2 vengono degradati a
`leaf`. Le chiamate fatte da un orchestrator subagent sono sincrone, perché il
maresciallo deve ricevere i risultati dei worker prima di produrre il proprio
summary. Le chiamate fatte dal top-level restano background.

### 2.4 Configurazione attuale

Nel profilo attivo non è presente una sezione `delegation`. Di conseguenza sono
attivi i default:

- modello child ereditato dal parent;
- `max_spawn_depth: 1`;
- tutti i child effettivamente `leaf`;
- `max_concurrent_children: 3`;
- `max_iterations: 50` per child;
- nessun timeout hard per child;
- dangerous command auto-denied.

La prima sperimentazione richiede quindi almeno l'abilitazione esplicita della
profondità 2.

---

## 3. Limite principale: un solo modello di delegazione

Oggi `delegation.provider` e `delegation.model` valgono per tutti i child. Se si
configura un modello economico, lo useranno sia i marescialli sia i worker. Se
non si configura nulla, tutti erediteranno il modello principale.

Manca quindi questo mapping:

```text
top-level          -> god-like planner
role=orchestrator  -> modello medio/forte
role=leaf          -> modello piccolo/economico
review             -> modello medio indipendente
```

Non va aggiunto un parametro arbitrario `model` al tool. Permettere al modello
di selezionare qualunque provider per singola chiamata renderebbe costi e policy
imprevedibili.

La soluzione proposta è un routing locale allow-listed.

```yaml
delegation:
  max_spawn_depth: 2
  orchestrator_enabled: true
  max_concurrent_children: 3
  max_total_descendants: 12

  profiles:
    marshal:
      provider: openrouter
      model: <modello-medio>
      reasoning_effort: high
      max_iterations: 30
      child_timeout_seconds: 1200

    worker:
      provider: openrouter
      model: <modello-flash>
      reasoning_effort: medium
      max_iterations: 15
      child_timeout_seconds: 600

  role_routes:
    orchestrator: marshal
    leaf: worker
```

Il modello continua a scegliere solo `role`, parametro già esistente. È la
configurazione locale a trasformare il ruolo in provider, modello e budget.

Per una fase successiva si potrà introdurre un ruolo logico `reviewer`, ma non è
necessario per il primo rilascio: la review finale può essere eseguita dal
top-level forte oppure da un profilo Kanban dedicato.

---

## 4. ExecutionPlan v1

Un piano lungo in linguaggio naturale non è sufficiente. Serve un contratto
strutturato, validabile e legato allo stato del repository.

Formato proposto:

```yaml
schema: hades.execution-plan.v1
plan_id: hp_20260709_example
created_at: 2026-07-09T12:00:00Z
workspace: /absolute/local/path
base_commit: abc123
objective: "Implementare la capability X senza regressioni"

global_invariants:
  - "I golden test restano verdi"
  - "Nessuna dipendenza obbligatoria nuova"
  - "Nessun file fuori scope viene modificato"

budgets:
  max_total_descendants: 12
  max_parallel_workers: 3
  max_replans: 2
  max_worker_attempts: 2

tasks:
  - id: T01
    owner_role: marshal
    objective: "Aggiungere il contratto dati"
    depends_on: []
    read_scope:
      - src/contracts.py
      - tests/test_contracts.py
    write_scope:
      - src/contracts.py
      - tests/test_contracts.py
    forbidden_scope:
      - migrations/
      - gateway/
    preconditions:
      - "ContractRegistry esiste"
      - "Il test baseline è verde"
    steps:
      - "Scrivere il failing regression test"
      - "Eseguire il test e confermare il failure atteso"
      - "Implementare la modifica minima"
      - "Eseguire test focalizzati e regressione"
    verification:
      - command: "scripts/run_tests.sh tests/test_contracts.py -q"
        expected: "pass"
    acceptance:
      - "Il nuovo contratto viene registrato"
      - "Il comportamento legacy resta invariato"
    escalation:
      - "Le precondizioni non sono vere"
      - "È necessario modificare un file fuori write_scope"
      - "Il test fallisce per una causa non prevista"
```

Il piano deve essere salvato localmente. Non deve essere inviato al backend se
contiene path sensibili, snippet o dettagli non redatti.

### 4.1 Proprietà obbligatorie

Ogni task deve specificare:

- obiettivo singolo;
- dipendenze;
- read scope;
- write scope;
- forbidden scope;
- precondizioni;
- passi operativi;
- comandi di verifica;
- criteri di accettazione;
- condizioni di escalation.

### 4.2 Preflight e drift

Prima del dispatch il controller deve verificare:

- il commit corrente rispetto a `base_commit`;
- esistenza dei file dichiarati;
- verità delle precondizioni controllabili;
- assenza di overlap non autorizzato tra write scope paralleli;
- disponibilità dei profili e dei modelli;
- disponibilità dei comandi di test;
- stato dirty del worktree.

Il piano non è "a prova di scemo" se il repository è cambiato dopo la sua
creazione. In caso di drift il comportamento corretto è replan o escalation,
non esecuzione cieca.

---

## 5. Cosa fa Kanban

Kanban è un task board multi-agent durevole basato su SQLite. Non è una variante
grafica di `delegate_task`: è una coda di lavoro e una state machine persistente.

Il database principale è:

```text
~/.hermes/kanban.db
```

È possibile creare board separate per progetto. Ogni board possiede database,
workspace, log ed event stream propri.

### 5.1 Lifecycle dei task

Un task Kanban attraversa gli stati:

```text
triage -> todo -> ready -> running -> done
                           |          |
                           +-> blocked+
```

Ogni task può avere:

- titolo e body;
- assignee, che corrisponde a un profilo Hermes;
- priorità;
- tenant;
- parent e child dependency;
- workspace;
- branch;
- skill aggiuntive;
- idempotency key;
- max runtime e retry;
- commenti;
- run history;
- summary e metadata strutturati.

### 5.2 Dispatcher

Il dispatcher Kanban gira normalmente dentro il gateway e, a ogni tick:

1. rileva worker crashati;
2. recupera claim scaduti;
3. promuove da `todo` a `ready` i task con dipendenze soddisfatte;
4. effettua un claim atomico;
5. risolve il profilo assegnato;
6. avvia `hermes -p <assignee> chat -q <prompt>`;
7. monitora processo, heartbeat, timeout e protocol termination.

Un crash del processo principale non cancella task, dipendenze, commenti o
risultati già registrati. Al riavvio il dispatcher riprende dal database.

### 5.3 Profili come worker lane

Il nome dell'assignee è il nome di un profilo Hermes. Ogni profilo può avere:

- provider e modello propri;
- skill proprie;
- toolset ristretto;
- memoria separata;
- persona e istruzioni dedicate;
- limiti operativi specifici.

Questo risolve già il routing multi-modello per il percorso durevole:

```text
god-planner   -> modello più potente
marshal       -> modello medio
worker-flash  -> modello piccolo
reviewer      -> modello medio/forte indipendente
```

Il dispatcher non deve sapere nulla sui provider: avvia il profilo assegnato e
quel profilo risolve localmente il proprio runtime.

### 5.4 Dipendenze e DAG

Kanban registra link parent-child. Un task rimane in `todo` finché tutti i
parent non sono `done`, poi viene promosso a `ready`.

Esempio:

```text
Worker A ----+
             +--> Verifier --> Synthesizer
Worker B ----+
```

Esiste già `hermes kanban swarm`, che crea:

- root/blackboard;
- N worker paralleli;
- verifier dipendente da tutti i worker;
- synthesizer dipendente dal verifier.

Questo helper dimostra che la topologia proposta è coerente con il modello dati
esistente.

### 5.5 Tool surface dei worker

I worker non devono eseguire comandi shell `hermes kanban`. Il dispatcher
inietta un toolset task-scoped:

- `kanban_show` legge task, dipendenze, commenti e handoff;
- `kanban_heartbeat` segnala liveness;
- `kanban_complete` chiude con summary e metadata;
- `kanban_block` interrompe con una ragione tipizzata;
- `kanban_comment` registra evidenza e comunicazioni;
- `kanban_create` e `kanban_link` sono disponibili agli orchestrator;
- `kanban_unblock` riattiva task bloccati.

Il worker deve terminare ogni run con `kanban_complete` o `kanban_block`.
Un'uscita normale senza protocol termination viene trattata come violazione.

### 5.6 Workspace

Kanban supporta:

- `scratch`: directory effimera per task senza repository persistente;
- `dir:<path>`: directory condivisa esistente;
- `worktree`: git worktree isolata per task di codice.

Per il power-up gerarchico:

- ricerca e analisi possono usare `scratch`;
- task sequenziali possono usare `dir:<repo>`;
- task di codice paralleli dovrebbero usare worktree separate;
- un integration task del maresciallo deve raccogliere patch o commit prodotti
  dai worker solo se il workflow autorizza esplicitamente tali commit;
- in assenza di commit automatici, i worker devono produrre patch artifact o
  lavorare in write scope disgiunti nello stesso workspace.

### 5.7 Retry, heartbeat e audit

Kanban offre già:

- claim atomici;
- heartbeat;
- stale reclaim;
- crash detection tramite PID;
- max runtime per task;
- failure circuit breaker;
- run history;
- event log append-only;
- stdout/stderr per task;
- comment thread durevole;
- human-in-the-loop tramite comment, block e unblock.

Queste proprietà lo rendono il substrato corretto per workflow lunghi o
importanti.

---

## 6. Delegate tree e Kanban: responsabilità diverse

Le due primitive devono coesistere.

| Proprietà | `delegate_task` | Kanban |
|---|---|---|
| Durata | breve o media | media o lunga |
| Persistenza restart | no | sì |
| Risultato | ritorna al parent | resta nel board |
| Topologia | albero caller-child | DAG peer-to-peer |
| Identità | subagent effimero | profilo nominato |
| Human-in-the-loop | limitato | nativo |
| Audit | sessione child | DB, eventi, run e commenti |
| Routing modello | oggi globale | per profilo |
| Uso ideale | microtask interni | workflow di progetto |

### 6.1 Fast path effimero

Usare `delegate_task` quando:

- il lavoro deve completarsi nella sessione corrente;
- i task sono pochi;
- non serve sopravvivere a restart;
- il maresciallo deve aggregare immediatamente i risultati;
- non è richiesto intervento umano;
- il write scope è piccolo e controllabile.

### 6.2 Durable path

Usare Kanban quando:

- il piano ha molte dipendenze;
- il lavoro può durare ore o giorni;
- sono necessari retry e recovery;
- più profili devono collaborare;
- serve un audit trail;
- è previsto human review;
- i task devono essere osservabili dal dashboard;
- il processo può essere riavviato.

### 6.3 Composizione

Un worker Kanban può usare `delegate_task` internamente. La composizione target è:

```text
Kanban root task
  |
  +-- profilo marshal avviato dal dispatcher
        |
        +-- delegate_task(worker leaf 1)
        +-- delegate_task(worker leaf 2)
        +-- verifica locale
        +-- kanban_complete / kanban_block
```

Kanban mantiene la verità durevole del macro-task. `delegate_task` gestisce il
micro-fan-out effimero interno alla singola run del maresciallo.

---

## 7. Skill proposta: hierarchical-development

Creare una nuova skill, senza modificare la skill official installata.

Percorso iniziale consigliato:

```text
~/.hermes/skills/software-development/hierarchical-development/SKILL.md
```

Quando stabilizzata potrà diventare bundled o optional nel repository.

### 7.1 Responsabilità della skill

La skill deve:

1. caricare o creare `hades.execution-plan.v1`;
2. validare precondizioni, scope e dipendenze;
3. classificare il workflow come ephemeral o durable;
4. scegliere una topologia bounded;
5. assegnare ownership ai marescialli;
6. passare a ogni child solo il contesto necessario;
7. imporre output strutturato;
8. verificare evidence e diff;
9. fermarsi ed escalare in caso di drift;
10. produrre un report finale con rischi residui.

### 7.2 Regole del God-like planner

Il planner:

- esplora il repository una sola volta in profondità;
- identifica invarianti e contratti;
- crea task con write scope disgiunti quando possibile;
- decide quali task richiedono un maresciallo;
- non esegue modifiche meccaniche;
- non riceve tool output dettagliati dei worker;
- riceve solo summary verificati e problemi escalati;
- effettua replan soltanto quando cambia una premessa.

### 7.3 Regole del maresciallo

Il maresciallo:

- possiede un sottosistema o un insieme di file;
- non modifica aree fuori ownership;
- scompone solo entro il contratto ricevuto;
- crea worker leaf con task atomici;
- raccoglie risultati sincroni;
- verifica i risultati dei worker;
- integra il sottosistema;
- restituisce un summary bounded al parent;
- non cambia architettura senza escalation.

### 7.4 Regole del worker

Il worker:

- è sempre `leaf`;
- riceve un task atomico;
- segue TDD quando produce codice;
- non amplia lo scope;
- non sceglie modelli o nuovi subagent;
- non modifica configurazione globale;
- non accetta automaticamente comandi pericolosi;
- si ferma quando una precondizione è falsa;
- restituisce evidence, non una dichiarazione generica di successo.

### 7.5 Output contract

Ogni worker deve produrre:

```json
{
  "status": "completed|blocked|failed",
  "task_id": "T01-W02",
  "changed_files": [],
  "commands_run": [],
  "tests": [
    {
      "command": "...",
      "result": "pass|fail",
      "summary": "..."
    }
  ],
  "scope_deviations": [],
  "unresolved_questions": [],
  "residual_risks": []
}
```

Il parent deve trattare questo output come self-report e verificare almeno:

- file realmente modificati;
- diff fuori scope;
- test dichiarati;
- artifact e path dichiarati;
- assenza di regressioni richieste dal piano.

---

## 8. Integrazione Kanban proposta

### 8.1 Profili

Creare almeno quattro profili:

```text
god-planner
marshal
worker-flash
reviewer
```

Ruoli:

| Profilo | Modello | Toolset | Compito |
|---|---|---|---|
| `god-planner` | massimo disponibile | file, search, terminal read-only, kanban | piano e replan |
| `marshal` | medio/forte | kanban, file, terminal, delegation | integrazione sottosistema |
| `worker-flash` | piccolo | file, terminal | task bounded |
| `reviewer` | medio/forte | file, terminal, kanban | verifica indipendente |

Il profilo orchestrator deve preferibilmente escludere tool di implementazione
quando il suo unico compito è produrre il DAG.

### 8.2 Decomposer forte

Kanban possiede già `auxiliary.kanban_decomposer`. Va configurato con il modello
forte, senza cambiare il modello della chat principale:

```yaml
auxiliary:
  kanban_decomposer:
    provider: <provider-forte>
    model: <modello-god-like>
    timeout: 300
```

Il decomposer attuale produce un task graph JSON e assegna i child ai profili in
base alle descrizioni. Per il power-up deve essere esteso o preceduto dal
validator di `ExecutionPlan v1`, così il DAG non sia soltanto plausibile ma
contrattuale.

### 8.3 Config Kanban

Configurazione iniziale:

```yaml
kanban:
  dispatch_in_gateway: true
  dispatch_interval_seconds: 15
  failure_limit: 2
  auto_decompose: false
  orchestrator_profile: marshal
  default_assignee: worker-flash
  max_in_progress: 4
  max_in_progress_per_profile: 3
  auto_promote_children: false
  dispatch_stale_timeout_seconds: 14400
```

Scelte deliberate:

- `auto_decompose: false` durante la prima fase, per approvare il piano prima
  del fan-out;
- `auto_promote_children: false` per consentire il preflight del DAG;
- `failure_limit: 2` per evitare loop costosi;
- concorrenza limitata globalmente e per profilo;
- dispatcher nel gateway per recovery automatico.

Quando il sistema sarà affidabile, `auto_decompose` e `auto_promote_children`
potranno essere abilitati.

### 8.4 Materializzazione del piano

Il planner deve convertire `ExecutionPlan v1` in task Kanban in una transazione:

1. creare root task con riferimento al piano;
2. creare task maresciallo per sottosistema;
3. creare task worker con assignee e workspace;
4. creare task verifier;
5. creare task synthesizer/final review;
6. collegare tutte le dipendenze;
7. allegare invarianti e acceptance criteria ai body;
8. lasciare i task in `todo` finché il DAG non supera il preflight;
9. promuovere in `ready` soltanto le root operative senza blocker.

La creazione deve essere idempotente tramite `plan_id`/idempotency key.

### 8.5 Blackboard

Il root task agisce come blackboard e contiene soltanto conoscenza condivisa
bounded:

- objective;
- invarianti;
- decisioni architetturali;
- mapping task-owner;
- riferimenti a artifact;
- stato di integrazione;
- problemi escalati.

Non deve contenere raw source, segreti, log completi o transcript. I commenti
devono contenere summary e pointer.

### 8.6 Review flow

Per task che modificano codice:

```text
worker completa implementazione
  -> commenta evidence strutturata
  -> blocca con review-required
  -> reviewer legge task, diff e test
  -> reviewer approva o chiede modifiche
  -> task viene sbloccato o chiuso
```

In alternativa il worker task può diventare `done` e un reviewer task separato
può dipendere da esso. Il secondo approccio è preferibile per DAG completamente
automatici perché separa chiaramente implementazione e verifica.

---

## 8A. Bridge Backend Kanban -> Agent Kanban

### 8A.1 Stato attuale

Il backend Hades possiede già un Kanban dashboard che può assegnare lavoro
all'agent locale. Oggi il flusso è:

```text
Backend Kanban task
  -> normalizzazione backend
  -> agent_work_items
  -> payload hades.kanban_task_work.v1
  -> hades_plugin_worker
  -> esecuzione diretta di una AIAgent
  -> complete/fail sul backend
```

Il contratto `hades.kanban_task_work.v1` include già:

- `task_id` e `project_id` autoritativi;
- repository scope;
- titolo, descrizione e acceptance criteria bounded;
- problema normalizzato;
- task type, priorità e rischio;
- readiness e clarification status;
- required context e source access policy;
- provenance;
- eventuali bug report ed evidence refs.

Il client locale sa già elencare i work item queued, effettuare claim atomico,
mantenere il lease con heartbeat e riportare complete/fail. Tuttavia il worker
li esegue direttamente: non vengono materializzati in `~/.hermes/kanban.db`.

Di conseguenza il lavoro remoto non beneficia ancora di DAG locale, profili
specializzati, worktree, reviewer task, retry durevoli, dashboard locale,
comment thread, swarm e recovery del dispatcher.

### 8A.2 Decisione

Il backend rimane la sorgente autoritativa del task remoto. Il Kanban locale
diventa una **proiezione esecutiva durevole**.

```text
BACKEND AUTORITATIVO                         KANBAN LOCALE

Kanban task                                  root task
agent_work_item       -- pull/upsert -->     external task mapping
remote revision                              child DAG locale
remote cancellation   -- controllo ---->    cancel/block subtree

completion/result      <-- push bounded --   evidence + stato aggregato
heartbeat/progress     <-- throttled ------  stato della run/subtree
```

Non bisogna copiare tabelle alla cieca o creare due master. La sincronizzazione
deve usare contratti versionati, revisioni e ownership dei campi.

### 8A.3 Ownership

Campi backend-authoritative:

- remote task id;
- project e repository id;
- titolo e normalized problem;
- acceptance criteria;
- priorità, rischio e task type;
- source access policy;
- readiness e clarification;
- cancellation;
- remote revision/etag;
- evidence refs iniziali.

Campi local-authoritative:

- profili assegnati;
- child DAG generato localmente;
- workspace/worktree;
- attempt e run id;
- gate status;
- test evidence;
- diff/patch refs locali;
- reviewer verdict;
- budget e routing locale;
- artifact non pubblicabili.

Il backend riceve soltanto stato aggregato ed evidence bounded. Non riceve
provider/model routing, reasoning, transcript, raw source, lease token, log
completi o path assoluti non redatti.

### 8A.4 Identità e mapping

Ogni task remoto deve mappare a un solo task root locale per workspace binding.

Chiave logica:

```text
(backend_origin, project_id, remote_task_id, workspace_binding_id)
```

Mapping minimo:

```text
work_item_id
local_task_id
remote_revision
last_remote_cursor
sync_state
last_synced_at
lease_state
```

Non usare il titolo come identità. Pull ripetuti devono produrre upsert
idempotenti.

È preferibile una tabella ponte nel DB Kanban:

```sql
CREATE TABLE kanban_external_task_refs (
    provider TEXT NOT NULL,
    project_id TEXT NOT NULL,
    remote_task_id TEXT NOT NULL,
    workspace_binding_id TEXT NOT NULL,
    work_item_id TEXT,
    local_task_id TEXT NOT NULL,
    remote_revision TEXT,
    sync_state TEXT NOT NULL,
    last_remote_cursor TEXT,
    last_synced_at TEXT NOT NULL,
    PRIMARY KEY (
        provider,
        project_id,
        remote_task_id,
        workspace_binding_id
    ),
    UNIQUE (local_task_id)
);
```

Il lease token non deve entrare in body, commenti o metadata. Se deve
sopravvivere a restart, va conservato nello storage Hades locale che già
gestisce i plugin work item e collegato al `local_task_id`.

### 8A.5 Pull incrementale

Il bridge deve supportare cursor, revisioni e tombstone:

```text
GET agent-work-items?project_id=...&agent_key=local_agent&after=<cursor>
```

```json
{
  "cursor": "opaque-next-cursor",
  "items": [],
  "tombstones": []
}
```

Se il backend non espone ancora un cursor, il primo rilascio può usare listing
bounded più upsert per id. Il contratto target deve includere:

- cursor opaco;
- revision o etag;
- `updated_at`;
- cancellation/tombstone;
- parent task ids;
- assignment target;
- desired execution state.

Il pull viene eseguito:

- da `hades backend sync`;
- periodicamente dal gateway;
- manualmente tramite comando diagnostico;
- prima del durable hierarchical workflow.

Comando proposto:

```text
hades backend kanban-sync [--pull] [--push] [--dry-run] [--json]
```

Il dry-run mostra create/update/cancel/conflict senza mutare il board.

### 8A.6 Materializzazione

Per ogni work item remoto valido:

1. validare `hades.kanban_task_work.v1`;
2. rifiutare item non ready o con contract drift;
3. risolvere project, repository e workspace binding;
4. cercare il mapping esterno;
5. creare o aggiornare il root task locale;
6. applicare idempotency key derivata dall'identità remota;
7. impostare workspace `dir:<linked-root>` o una worktree policy;
8. lasciare il task in `triage` o `todo`, senza claim remoto;
9. eseguire planning, decomposizione e preflight locali;
10. promuovere a `ready` soltanto dopo i gate.

Il body locale contiene un envelope bounded:

```yaml
origin:
  provider: hades_backend
  remote_task_id: "..."
  work_item_id: "..."
  project_id: "..."
  repository_id: "..."
  remote_revision: "..."

problem: "..."
acceptance_criteria: []
required_context: []
source_access_policy: {}
```

### 8A.7 Task remoto semplice e DAG remoto

Per un task remoto semplice:

```text
1 remote task
  -> 1 local root
  -> local planner crea N child
  -> verifier + synthesis
  -> 1 risultato aggregato al backend
```

Se il backend espone già dipendenze:

```text
remote A ----+
              +--> remote C
remote B ----+
```

il bridge replica identità e link nel DAG locale. La decomposizione locale può
aggiungere child sotto ogni nodo, ma non deve riscrivere dipendenze remote.

Per questo servirà `hades.kanban_task_work.v2`, o un'estensione compatibile:

```yaml
remote_revision: "..."
parent_task_ids: []
dependency_policy: all_parents_done
desired_state: queued
cancelled_at: null
```

Il v1 continua a funzionare come root task senza dipendenze remote.

### 8A.8 Claim e lease

Il download non deve effettuare claim. Un task mirrored può restare in triage a
lungo; trattenere il lease impedirebbe ad altri binding di lavorarlo.

Lifecycle corretto:

```text
remote queued
  -> local mirrored, senza lease
  -> local planning/preflight
  -> local root ready
  -> dispatcher sta per avviare il root
  -> claim backend atomico
  -> local subtree running
  -> heartbeat backend
  -> aggregate complete/block/fail
```

Servono hook tra dispatcher e bridge:

- `pre_task_run`: claim remoto;
- `task_heartbeat`: rinnovo lease throttled;
- `task_completed`: complete remoto;
- `task_blocked`: block/progress remoto;
- `task_failed`: fail remoto solo per failure terminali;
- `task_cancelled_remote`: interrompi il subtree locale.

Il root remoto non deve essere completato quando il maresciallo finisce il solo
fan-out. Resta semanticamente running finché child e verifier non sono terminali.

Il backend oggi espone complete e fail, ma non transizioni ricche per mirrored,
blocked, progress, release e cancellation. Non mappare `needs_input` a failure.
Endpoint proposti:

```text
POST /agent-work-items/{id}/accept
POST /agent-work-items/{id}/progress
POST /agent-work-items/{id}/block
POST /agent-work-items/{id}/resume
POST /agent-work-items/{id}/release
```

### 8A.9 Concorrenza e conflitti

Più device possono scaricare lo stesso task. Il pull non assegna il diritto di
esecuzione:

1. il mapping unique evita duplicati nello stesso workspace;
2. il claim atomico backend sceglie il workspace binding esecutore.

Se il claim viene rifiutato, il task locale viene marcato blocked/superseded,
non conta come worker failure e non viene ritentato in loop.

Per aggiornamenti remoti:

- revision nuova, task non avviato: aggiornare campi remote-owned;
- revision nuova, task running: `remote_update_pending` e safe stop;
- acceptance criteria cambiate: replan obbligatorio;
- repository scope cambiato: invalidare workspace e claim;
- cancellation remota: interrompere l'intero subtree;
- mai usare last-write-wins cieco.

### 8A.10 Push bounded

Non inviare ogni evento locale. Inviare eventi semantici e throttled:

- mirrored/accepted;
- execution started;
- progress aggregato;
- needs input;
- blocked;
- completed;
- failed terminale;
- cancelled;
- residual risk.

Payload finale esempio:

```json
{
  "status": "completed",
  "summary": "Implemented the bounded task and passed required checks.",
  "verification": [
    {
      "command": "scripts/run_tests.sh tests/example.py -q",
      "result": "pass"
    }
  ],
  "changed_file_refs": ["src/example.py", "tests/example.py"],
  "evidence_refs": [],
  "residual_risk": [],
  "local_execution": {
    "worker_count": 3,
    "review_verdict": "approved",
    "attempt_count": 1
  }
}
```

### 8A.11 Offline e recovery

Quando il backend è irraggiungibile:

- i task mirrored restano visibili;
- task non claimati non iniziano nuovo lavoro remoto;
- task claimed continuano solo entro una grace window;
- heartbeat failure prolungato porta il subtree a safe stop;
- completion viene accodata in una outbox idempotente;
- al reconnect si verificano lease e revision prima del push;
- lease perso impedisce completion con token stale;
- il risultato locale resta recuperabile anche se il backend rifiuta il push.

### 8A.12 Relazione con ExecutionPlan v1

Il payload backend è input autoritativo, non piano di implementazione:

```text
hades.kanban_task_work.v1
  -> local root task
  -> God-like planner
  -> hades.execution-plan.v1
  -> local Kanban DAG
  -> execution + review
  -> bounded aggregate result
  -> backend completion
```

Il `plan_id` viene collegato al remote mapping per audit e replan senza
pubblicare il piano integrale.

### 8A.13 Implementazione

Nuovo modulo suggerito:

```text
hermes_cli/hades_kanban_bridge.py
```

Responsabilità:

- pull incrementale;
- contract validation;
- idempotent upsert locale;
- remote/local mapping;
- conflict detection;
- dispatcher hooks;
- lease lifecycle;
- progress aggregation;
- completion outbox;
- cancellation propagation;
- privacy redaction;
- diagnostics.

Riutilizzare:

- `hades_plugin_work_items_client.py` per HTTP e lease;
- `hades_kanban_task_contract.py` per v1/v2;
- `hades_plugin_worker.py` come compatibility path;
- `kanban_db` per task, mapping, link e lifecycle.

Migrazione:

```yaml
backend:
  kanban_bridge:
    mode: mirror             # direct | mirror
    pull_interval_seconds: 30
    offline_grace_seconds: 120
    push_progress_interval_seconds: 60
    auto_plan: false
    auto_promote: false
```

Il default iniziale resta `direct`. `mirror` diventa default solo dopo test
end-to-end e recovery.

### 8A.14 Test obbligatori

- doppio pull crea una sola card;
- contract invalido non entra nel board;
- task non ready non viene eseguito;
- claim avviene al dispatch, non al pull;
- claim conflict non conta come worker failure;
- heartbeat locale rinnova lease remoto;
- root non completa prima di child e verifier;
- blocked locale non diventa fail remoto;
- cancellation remota interrompe tutti i discendenti;
- completion outbox è idempotente;
- restart riprende mapping e outbox;
- lease stale impedisce completion;
- parent link backend viene replicato localmente;
- v1 senza parent diventa root semplice;
- project/workspace diversi non collidono;
- due agent importano lo stesso task, uno solo ottiene il claim;
- nessun payload outbound contiene source, secret, reasoning o routing.

---

## 9. Budget e sicurezza dell'albero

La profondità da sola non basta. Con branching factor 3 e depth 3 si possono
creare rapidamente decine di agenti.

Serve un `DelegationTreeBudget` condiviso:

```yaml
delegation:
  max_spawn_depth: 2
  max_concurrent_children: 3
  max_total_descendants: 12
  max_total_iterations: 180
  max_total_failures: 4
  max_replans: 2
```

Il budget deve essere ereditato per riferimento dai child e decrementato
atomicamente. Un child non deve ricevere una budget indipendente illimitata.

Guardrail:

- nessuna recursive delegation per i leaf;
- nessun overlap di write scope tra worker paralleli;
- nessun dangerous-command auto-approve di default;
- nessun toolset più ampio del parent;
- massimo due tentativi per task;
- stop immediato su precondizione falsa;
- escalation al maresciallo, poi al planner;
- circuit breaker su errori ripetuti;
- cost rollup per ramo e per piano;
- cancellazione propagata ai discendenti;
- privacy redaction su summary ed evidence.

---

## 10. Strategia di contesto

Le sottosessioni riducono la context degradation, ma il beneficio dipende dal
modo in cui vengono passati i dati.

### 10.1 Cosa riceve il worker

- task corrente completo;
- invarianti globali rilevanti;
- file scope;
- acceptance criteria;
- output sintetico delle dipendenze;
- comandi di verifica;
- condizioni di escalation.

### 10.2 Cosa non riceve

- intera conversazione del God-like planner;
- intero piano se non necessario;
- reasoning di altri worker;
- log completi;
- source non necessario;
- memoria personale;
- decisioni non pertinenti al proprio scope.

### 10.3 Cosa risale

```text
worker -> evidence bounded -> maresciallo
maresciallo -> subsystem summary -> planner
reviewer -> findings/verdict -> planner
```

Ogni livello comprime semantica operativa in contratti verificabili. Il planner
non deve ricevere ogni dettaglio, ma deve poter seguire pointer ad artifact e
sessioni child quando serve investigare.

---

## 11. Prompt caching e modello

Non cambiare modello nella stessa conversazione per trasformare il planner in
worker. Questo rende più fragile il caching e mescola responsabilità.

Usare invece:

- sessione top-level stabile con modello forte;
- child session dedicate per marescialli e worker;
- profili Kanban separati per routing durevole;
- auxiliary slot per decomposer forte;
- system prompt stabile per ogni sessione.

Il modello forte viene chiamato per:

- piano iniziale;
- decisioni architetturali;
- replan;
- escalation ambigue;
- final review ad alto rischio.

Non viene chiamato per:

- edit meccanici;
- esecuzione ripetitiva di test;
- rename bounded;
- modifica di fixture;
- raccolta di evidenze;
- operazioni già completamente specificate.

---

## 12. Piano di implementazione

### Fase 0 - Esperimento senza modifiche core

1. Creare profili `marshal`, `worker-flash`, `reviewer`.
2. Configurare modelli e toolset per profilo.
3. Abilitare `delegation.max_spawn_depth: 2`.
4. Creare la skill locale `hierarchical-development`.
5. Eseguire un piano piccolo con un maresciallo e due worker.
6. Misurare costo, latenza, conflitti e qualità dei summary.

Questa fase prova la topologia, ma maresciallo e worker useranno lo stesso
`delegation.model` nel percorso `delegate_task`.

### Fase 1 - Routing per ruolo

Modificare:

- `hermes_cli/config.py`: aggiungere `delegation.profiles`, `role_routes` e
  budget globali;
- `tools/delegate_tool.py`: risolvere credenziali e limiti in base a ruolo e
  profondità;
- test delegation: verificare orchestrator->marshal e leaf->worker;
- documentazione config e tool description dinamica.

Non aggiungere un parametro model-facing arbitrario.

### Fase 2 - Budget condiviso

Introdurre una struttura process-local condivisa da tutto l'albero:

```text
DelegationTreeBudget
  root_id
  remaining_nodes
  remaining_iterations
  remaining_failures
  remaining_replans
```

Testare:

- limite totale nodi;
- race tra sibling;
- propagazione cancel;
- budget exhausted;
- profondità massima;
- rollback della reservation se il child non parte.

### Fase 3 - ExecutionPlan validator

Creare un modulo locale, ad esempio:

```text
hermes_cli/hierarchical_execution.py
```

Responsabilità:

- parse schema;
- validazione campi;
- DAG cycle detection;
- overlap detection;
- workspace preflight;
- materializzazione handoff;
- evidence validation;
- stato del run.

Preferire CLI + skill, non un nuovo model tool core.

### Fase 4 - Kanban materializer

Riutilizzare `kanban_db` per:

- creare root e child atomicamente;
- collegare dipendenze;
- impostare assignee e workspace;
- aggiungere idempotency key;
- allegare skill;
- creare verifier e synthesizer;
- promuovere task dopo preflight.

Valutare se estendere `hermes kanban swarm` con un input plan invece di creare
un secondo materializer parallelo.

### Fase 4B - Backend Kanban bridge

1. Estendere il contratto backend con remote revision, cursor e cancellation.
2. Implementare `hades_kanban_bridge.py` con pull e dry-run.
3. Aggiungere mapping unique remote task -> local root.
4. Materializzare payload v1 senza effettuare claim.
5. Collegare claim e heartbeat al lifecycle del dispatcher.
6. Aggiungere progress, block, resume e release al backend.
7. Implementare completion outbox idempotente.
8. Propagare cancellation remota all'intero subtree.
9. Testare multi-device, offline e restart.
10. Mantenere direct mode durante la migrazione.

### Fase 5 - UI e osservabilità

Mostrare nel dashboard:

- plan id;
- albero/DAG;
- ruolo e modello logico, senza credenziali;
- budget residuo;
- costo per ramo;
- write scope;
- gate status;
- escalation;
- test evidence;
- replan count.

Non inviare provider/model routing al backend Hades condiviso. Questi dati
restano locali.

---

## 13. Test richiesti

### Delegation

- child ha sessione distinta e parent id corretto;
- parent history non entra nel child;
- orchestrator depth 1 può delegare;
- leaf depth 2 non può delegare;
- role routing seleziona profilo corretto;
- budget globale impedisce fan-out eccessivo;
- cancel top-level interrompe tutti i discendenti;
- summary bounded è l'unico contenuto reiniettato nel parent.

### Execution plan

- DAG valido accettato;
- ciclo rifiutato;
- write scope overlap rifiutato o serializzato;
- precondizione falsa produce escalation;
- base commit drift produce replan;
- unknown task dependency rifiutata;
- evidence fuori scope rifiutata;
- retry oltre limite bloccato.

### Kanban

- materializzazione idempotente;
- dipendenze promosse correttamente;
- profilo mancante non causa fallback silenzioso;
- worker crashato viene recuperato;
- verifier parte solo dopo tutti i worker;
- review failure crea follow-up o blocca;
- restart del gateway riprende il board;
- workspace isolation preservata;
- metadata non contiene raw source o segreti.

### Backend Kanban bridge

- pull remoto idempotente;
- mapping stabile tra remote task e local root;
- claim ritardato fino al dispatch;
- lease e heartbeat coerenti con il subtree;
- cancellation remota propagata;
- completion outbox restart-safe;
- block distinto da fail;
- conflitti di revision causano replan;
- risultati outbound bounded e redatti.

### End-to-end

Scenario minimo:

```text
1 planner forte
2 marescialli
4 worker flash
1 reviewer
1 final synthesis
```

Verificare:

- il planner non modifica file;
- ogni worker resta nel proprio scope;
- i task indipendenti girano in parallelo;
- i task dipendenti aspettano;
- i test focalizzati passano;
- i golden restano verdi;
- il reviewer trova una violazione intenzionalmente introdotta;
- il sistema corregge o escalata senza loop infinito;
- il costo del modello forte è concentrato su piano e review.

---

## 14. Metriche

Misurare per ogni execution plan:

- costo totale;
- costo del planner;
- costo dei worker;
- token per ruolo;
- wall-clock time;
- first-pass success rate;
- retry rate;
- escalation rate;
- replan rate;
- scope violation rate;
- regressioni sfuggite al reviewer;
- percentuale di task eseguita da modelli piccoli;
- percentuale di output worker verificata deterministicamente.

La feature ha successo se riduce il costo senza abbassare la qualità e senza
aumentare significativamente replan e defect escape rate.

---

## 15. Decisione raccomandata

Procedere in questo ordine:

1. sperimentare subito l'albero depth 2 con skill locale;
2. implementare routing per ruolo senza esporre modelli nel tool schema;
3. introdurre budget globale e scope gate;
4. definire `hades.execution-plan.v1`;
5. usare `delegate_task` come fast path;
6. usare Kanban come durable path;
7. scaricare i task del Kanban backend nel Kanban locale tramite bridge
   idempotente;
8. estendere `kanban swarm` o il decomposer per materializzare il piano;
9. aggiungere reviewer e final synthesis come task indipendenti;
10. riportare al backend soltanto stato ed evidence bounded;
11. automatizzare solo dopo aver raccolto metriche su task reali.

La tesi finale è:

> Il modello god-like non deve fare più lavoro. Deve prendere meno decisioni,
> ma quelle decisive. I marescialli trasformano decisioni architetturali in
> responsabilità locali; i worker economici eseguono contratti bounded; Kanban
> rende il tutto durevole, osservabile e recuperabile.
