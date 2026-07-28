# Agentic-Kanban e OrgRun locale

**Data:** 2026-07-28

**Stato:** design conversazionale approvato; specifica scritta in attesa di revisione

**Ambito:** Kanban locale, hierarchical-development, OrgRun, profili di esecuzione e report locali

## 1. Decisione

Il Kanban locale di Hermes/Hades diventa **Agentic-Kanban**: il piano
operativo privato del programmatore e degli agenti. Non è una replica del
Kanban PM del backend e non sincronizza card, stato o lifecycle con esso.

Il flusso durevole per lo sviluppo gerarchico è:

```text
hierarchical-development
        |
        | definisce autorità e produce/valida un implementation plan
        v
OrgRun
        |
        | valida e materializza deterministicamente il DAG
        v
Agentic-Kanban
        |
        | esegue tramite orchestrator, leaf e reviewer
        v
Report locali terminali
```

`hierarchical-development` è la policy di coordinamento. `OrgRun` è un
materializzatore e state tracker deterministico, privo di modello.
Agentic-Kanban è l'unico runtime e scheduler. Non viene introdotto un secondo
scheduler, un nuovo model tool o un nuovo protocollo di chat tra agenti.

## 2. Relazione con i design precedenti

Questa specifica:

- sostituisce il contratto di sincronizzazione delle card descritto in
  `2026-07-27-local-first-kanban-design.md`;
- sostituisce le parti backend-centriche e la topologia `5*N+4` del primo
  `2026-07-10-hades-durable-org-run-design.md`;
- mantiene le primitive locali già implementate: board SQLite, task,
  dipendenze, run, eventi, dispatcher, profili, role routing e dashboard;
- non modifica il logbook remoto definito in
  `2026-07-21-hades-mvp-logbook-and-exploration-design.md`;
- non richiede il processo live `hermes-review-engine` descritto in
  `2026-07-22-engineering-review-engine-design.md`.

Le tabelle di link, lease e outbox remote già presenti non vengono eliminate
in modo distruttivo. Diventano dati legacy di sola ispezione e non possono
influenzare admission, dispatch o completamento delle card Agentic-Kanban.

## 3. Obiettivi

1. Rendere Agentic-Kanban completamente operativo senza backend configurato,
   collegato o raggiungibile.
2. Eliminare le invocazioni rigide a eseguibili non disponibili quali
   `hermes-agent` e `hermes-review-engine`.
3. Rendere non ambigua la relazione tra hierarchical-development, OrgRun,
   profili Kanban, auto-decompose nativo e swarm.
4. Permettere a un orchestrator di creare un implementation plan senza
   costruire manualmente il DAG card per card.
5. Materializzare e riprendere il DAG in modo atomico e idempotente.
6. Produrre un logbook locale consultabile come proiezione terminale delle
   evidenze Kanban, separato per task e OrgRun.
7. Completare come acceptance reale i task ancora in corso nella sessione
   Hades `20260727_191342_7b2122`.

## 4. Non-obiettivi

- Sincronizzare Agentic-Kanban con il Kanban PM del backend.
- Importare automaticamente task PM in una board locale.
- Pubblicare automaticamente report, progress o completion nel backend.
- Progettare ora il futuro passaggio `Backend task -> Inbox locale -> run`.
- Progettare ora la pubblicazione di report nel logbook o nella Wiki remota.
- Implementare in questo slice lo switch grafico della memoria
  `Local / Backend`.
- Modificare provider o toolset durante una conversazione già avviata.
- Integrare `kanban swarm` nel flusso OrgRun.
- Aggiungere un nuovo tool core Hermes.

## 5. Autorità e responsabilità

| Componente | Autorità | Non può fare |
|---|---|---|
| hierarchical-development | policy, ruoli, limiti di delega, forma del piano | schedulare worker o mutare il DAG direttamente |
| orchestrator | creare il piano, ordinare priorità compatibili col DAG, supervisionare, proporre amendment, finalizzare | materializzare manualmente il DAG iniziale o alterare contratti completati |
| OrgRun | validare, materializzare, versionare e proiettare lo stato del piano | chiamare modelli, scegliere provider, eseguire task |
| Agentic-Kanban | persistere card/run/eventi, risolvere dipendenze, dispatchare | sincronizzare card col backend o ridecomporre card OrgRun |
| leaf | implementare un task entro write scope e acceptance | modificare il piano o coordinare sibling |
| reviewer | produrre review indipendente ed evidenza | diventare orchestrator o modificare il codice in review |
| report projector | derivare JSON e Markdown dalle evidenze terminali | inventare evidenze o mutare lo stato operativo |

L'orchestrator può scrivere direttamente un implementation plan oppure
accettarne uno già fornito. Richiede poi a OrgRun la validazione e
materializzazione atomica. Durante l'esecuzione, leaf e reviewer segnalano
risultati, blocker e finding; solo l'orchestrator o il parent diretto
autorizzato può proporre un amendment.

## 6. Implementation plan

Il contratto locale è `hades.implementation-plan.v1`. Non contiene
project ID backend, workspace binding, credenziali, provider o nomi di modelli.

Campi minimi:

```json
{
  "schema": "hades.implementation-plan.v1",
  "run_id": "run-stable-id",
  "objective": "Risultato osservabile del run",
  "base_commit": "full-git-sha",
  "acceptance_criteria": ["criterio verificabile"],
  "tasks": [
    {
      "id": "task-stable-id",
      "title": "Titolo operativo",
      "role": "leaf",
      "risk": "low",
      "write_scope": ["src/example.py", "tests/test_example.py"],
      "depends_on": [],
      "acceptance_criteria": ["comportamento verificabile"],
      "verification": ["pytest tests/test_example.py"],
      "independent_review": true
    }
  ]
}
```

Regole:

- `run_id` e task ID sono stabili e unici nella board scelta;
- `base_commit` deve esistere nel workspace della board;
- ogni dipendenza deve riferirsi a un task del piano e il grafo deve essere
  aciclico;
- gli scope devono essere relativi al repository e canonicalizzati;
- overlap tra writer eseguibili in parallelo produce serializzazione
  deterministica oppure un errore di validazione esplicito;
- acceptance e verification non possono essere vuoti;
- i ruoli sono logici e devono essere risolvibili localmente al momento della
  validazione;
- un piano invalido non produce alcuna mutazione.

La board non è implicita nel documento. È un parametro operativo:

```text
hades org validate plan.json --board <slug>
hades org materialize plan.json --board <slug>
```

Un'origine futura può essere allegata come envelope separato
`local | backend`, ma non cambia lo schema del piano né introduce sync.

## 7. Identità, versioni e provenance

Ogni run conserva:

```text
run_id
board_slug
plan_version
plan_hash
base_commit
origin
state
created_at
updated_at
```

Ogni card materializzata conserva metadata strutturati:

```text
managed_by = "orgrun"
run_id
node_id
node_kind
plan_version
contract_hash
logical_role
```

La chiave `(board_slug, run_id, node_id, contract_hash)` rende la
materializzazione idempotente. Ripetere il comando con lo stesso piano non
crea duplicati. Lo stesso `run_id` con hash incompatibile viene rifiutato e
richiede un amendment.

## 8. Topologia del DAG

OrgRun materializza solo i nodi necessari:

```text
Run anchor (completed, identity/blackboard)
   |
   +-- Task A (leaf) -- optional Task A review
   |
   +-- Task B (leaf) -- optional Task B review
   |
   +-- Task C (leaf) -- optional Task C review
                         |
                         v
                    Integration
                         |
                         v
              Optional independent review
                         |
                         v
              Orchestrator finalization
```

Le dipendenze dichiarate nel piano collegano i task o i rispettivi review
gate. Integration dipende dal terminal gate di tutti i task. La review
indipendente del run è presente quando richiesta dal rischio o dal piano.
Finalization dipende dall'ultimo gate disponibile.

Non vengono creati automaticamente remote anchor, publish node o readiness
node. Nessuna card OrgRun entra nel decomposer nativo.

## 9. Profili, modelli e processi

`orchestrator`, `leaf` e `reviewer` sono ruoli logici che, nella configurazione
locale corrente, hanno profili Hermes omonimi. Il dispatcher risolve un ruolo
attraverso il registry dei profili realmente installati e poi applica
`delegation.role_routes` per modello e provider.

Il piano non sceglie il modello. OrgRun non usa il modello della chat e non
chiama alcun modello. L'orchestrator usa il modello della propria sessione o
del proprio profilo; leaf e reviewer usano le rispettive route locali.

Non esiste fallback silenzioso a `default`. Un profilo mancante durante la
validazione rende invalido il piano senza materializzarlo. Se un profilo viene
rimosso o diventa indisponibile dopo la materializzazione, il dispatcher
produce `profile_unavailable` prima dello spawn e blocca il nodo con istruzioni
configurative. Il dispatcher non prova nomi legacy e, in particolare, non
esegue:

```text
hermes-agent ...
hermes-review-engine ...
```

La review è un normale task Kanban assegnato al profilo `reviewer`. Eventuali
check deterministici disponibili possono essere invocati dalla task, ma
l'assenza di un'autorità live esterna non deve generare loop o bloccare
indefinitamente il runtime.

## 10. Auto-decompose e swarm

L'auto-decompose nativo resta disponibile per card di triage create
manualmente. Il dispatcher/decomposer deve escludere ogni card con
`managed_by="orgrun"`. Questo rende impossibile una seconda decomposizione del
piano.

Per rendere visibile la separazione, nella dashboard la sezione generica
`Orchestration settings` viene rinominata **Native triage decomposition** e
spiega che:

- `orchestrator_profile` possiede la root prodotta dal decomposer nativo;
- `default_assignee` è il fallback delle sole card native;
- le profile descriptions sono hint testuali del decomposer nativo;
- nessuna di queste impostazioni modifica autorità, DAG o routing OrgRun.

`kanban swarm` resta un template esplicito e indipendente per una fan-out
locale rapida con worker, verifier e synthesizer. Non è un livello sopra o
sotto OrgRun, non viene richiamato da hierarchical-development e non viene
attivato automaticamente. Un singolo run usa OrgRun oppure swarm, mai entrambi.

## 11. Lifecycle e amendment

Stati OrgRun:

```text
draft -> validated -> materialized -> running
      -> integrating -> reviewing -> completed
```

`blocked` e `cancelled` sono stati espliciti. Un blocked run è riprendibile;
un cancelled run è terminale.

Il piano materializzato è una baseline immutabile. Un amendment:

1. riferisce `run_id` e `base_plan_version`;
2. descrive nodi aggiunti, sostituiti o cancellati e la motivazione;
3. non riscrive card o contratti già completati;
4. viene validato integralmente contro stato corrente, DAG e scope;
5. incrementa `plan_version` e muta il grafo in un'unica transazione;
6. non produce alcuna modifica se la validazione o la transazione fallisce.

## 12. Agentic-Kanban esclusivamente locale

La pagina `/kanban` mostra il titolo **Agentic-Kanban**. Il badge
`Local only` viene rimosso perché la località non è uno stato degradato.

Le superfici `watch`, `serve`, dashboard, dispatcher e worker:

- non costruiscono un client backend;
- non importano task remoti;
- non acquisiscono lease remoti;
- non pubblicano progress o risultati remoti;
- non cambiano comportamento in base al memory provider;
- continuano a funzionare se il backend è assente o guasto.

I controlli di sync e lo stato backend vengono rimossi dalla pagina
Agentic-Kanban. Il comando storico `hades kanban sync` deve fallire
immediatamente con un risultato tipizzato e non retryable che spiega che le
board Agentic-Kanban non hanno sincronizzazione remota. `hades backend sync`
può continuare a gestire capability backend estranee alle card, ma non può
importare o esportare task Agentic-Kanban.

Eventuali `kanban_remote_links`, lease o outbox storici sono ignorati dal
runtime locale e leggibili solo per audit/migrazione futura. Non richiedono
backend admission e non devono impedire la ripresa delle card esistenti.

La board è collegata al workspace locale tramite il suo `default_workdir` e
viene scelta esplicitamente. Non esiste pairing automatico tra board locale e
Backend Project.

## 13. Logbook come sottoprodotto del Kanban

Kanban rimane la source of truth. Non viene introdotto un secondo flusso di
scrittura manuale per leaf e reviewer.

Quando una card task raggiunge `completed` con un run terminale durevole, il
report projector genera un **Task Completion Report**. Quando OrgRun supera
integration, eventuale review e finalization, genera un
**Final Development Report**. Un OrgRun cancellato produce un cancellation
report; un run semplicemente blocked non produce un falso report terminale.

La proiezione usa:

- descrizione, acceptance e write scope del contratto;
- risultati e summary dei run;
- eventi, blocker e tentativi precedenti;
- commit e file modificati quando disponibili;
- comandi di test ed esiti;
- verdict e finding di review;
- regressioni note, rischio residuo e limiti;
- provenance di board, task, OrgRun, versione del piano e base commit.

Il JSON è il formato canonico e verificabile. Il Markdown è un rendering
deterministico per l'interfaccia. Una narrativa dell'orchestrator può essere
inclusa come campo bounded già presente nelle evidenze, ma né validità né
completezza dipendono da una nuova chiamata LLM.

Tabella minima:

```text
kanban_reports
  id
  board_slug
  report_type          # task_completion | org_run_final | org_run_cancelled
  subject_id           # task_id o run_id
  terminal_run_id
  source_version
  report_json
  report_markdown
  generated_at
  idempotency_key
```

`idempotency_key` è univoca. Rigenerare la stessa proiezione non duplica il
report. Una correzione futura crea una nuova `source_version` e conserva la
versione precedente.

La dashboard Agentic-Kanban offre:

```text
Board | Logbook
```

Logbook elenca report task e OrgRun, consente filtro per tipo/stato/run e
collega ogni report alle card e alle evidenze locali. Non crea
`docs/Hades/logbook.md`. Una futura pubblicazione backend consumerà soltanto
Final Development Report verificati, attraverso un workflow separato.

## 14. Error handling

- **Piano invalido:** errore strutturato, nessuna riga o card creata.
- **Materializzazione fallita:** rollback completo della transazione.
- **Profilo mancante:** nodo blocked con `profile_unavailable`, nessun fallback
  e nessun retry automatico infinito.
- **Worker fallito:** tentativo ed evidenza conservati; task/run bloccati
  secondo il failure budget, senza completion sintetica.
- **Review non conclusiva:** integration non passa e final report non viene
  prodotto.
- **Amendment invalido:** nessuna mutazione e nessun incremento di versione.
- **Crash o restart:** resume dalla board e dagli eventi; la
  materializzazione idempotente non duplica nodi.
- **Backend offline:** nessun effetto osservabile sulle operazioni locali.
- **Report incompleto:** il gate terminale resta reviewing/blocked e il report
  non viene dichiarato verificato.
- **Proiezione interrotta:** retry idempotente dalla stessa source version.

Gli errori riportano sempre componente, run/task, tipo, retryability e
istruzione operativa. Credenziali, transcript di reasoning e payload backend
non entrano negli eventi o nei report.

## 15. Test richiesti

### Contratti e unità

- validazione schema, DAG, scope, base commit e profili;
- hashing e identità stabili;
- conflitti di scope e serializzazione;
- state machine e amendment;
- rendering JSON/Markdown e idempotenza dei report.

### Integrazione locale

Con un `HERMES_HOME` temporaneo e nessun backend:

- creare una board e materializzare un piano;
- provare rollback completo su piano e transazione invalidi;
- dispatchare leaf, reviewer, integration e finalization;
- dimostrare che OrgRun non passa dall'auto-decompose;
- dimostrare che OrgRun non richiama swarm;
- dimostrare che non viene costruito un backend client;
- interrompere e riprendere senza duplicare card o report;
- verificare profilo mancante senza fallback;
- verificare task report e final report dalla reale evidenza dei run.

I test osservano subprocess e argv per provare l'assenza di chiamate a
`hermes-agent` e `hermes-review-engine`, non soltanto il risultato finale.

### Dashboard

- branding `Agentic-Kanban`;
- assenza di badge e controlli sync;
- tab `Board | Logbook`;
- filtro e navigazione task/OrgRun;
- rendering sicuro di Markdown ed errori locali non distruttivi.

### Acceptance sulla sessione reale

Dopo i test isolati, Hades riprende la sessione
`20260727_191342_7b2122` sulla board originale:

1. fotografa stato e task incompleti prima della mutazione;
2. riprende il flusso senza backend e senza processi legacy;
3. completa i task ancora validi rispettando dipendenze e profili;
4. esegue test e review richiesti dai rispettivi contratti;
5. verifica assenza di retry loop, card duplicate e falsi completed;
6. produce Task Completion Report e Final Development Report;
7. conserva per audit gli errori storici già avvenuti.

La sessione è acceptance evidence, non sostituisce i test riproducibili.

## 16. Migrazione e rollout

1. Aggiungere metadata/versioni OrgRun e `kanban_reports` con migrazioni
   additive.
2. Disabilitare tutti i trigger e le admission remote delle board locali.
3. Rimuovere dalla UI branding e controlli di sync.
4. Introdurre il nuovo contratto plan e il materializzatore atomico.
5. Collegare role routing esclusivamente ai profili disponibili.
6. Aggiungere report projector e tab Logbook.
7. Eseguire suite locale ed E2E offline.
8. Riprendere la sessione Hades reale e raccogliere l'acceptance evidence.

Il rollout non cancella dati remoti legacy e non modifica il backend. Il
rollback del codice lascia leggibili board, eventi e report già creati.

## 17. Slice successive

Tre design indipendenti seguiranno questo lavoro:

1. **Backend task -> Inbox locale -> development run:** import esplicito di un
   mandato PM, senza mirroring di board.
2. **Pubblicazione del Final Development Report:** backend sync aggiorna il
   logbook remoto e propone eventuali modifiche Wiki dopo review.
3. **Switch memoria Local / Backend:** controllo grafico che modifica
   `memory.provider` per la sessione successiva; nessun hot swap della
   conversazione corrente, così prompt cache e toolset restano stabili.

## 18. Criteri di accettazione

Il lavoro è completo quando:

- Agentic-Kanban crea, esegue, riprende e rende consultabile un OrgRun senza
  backend;
- hierarchical-development materializza tramite OrgRun e non crea direttamente
  il DAG;
- OrgRun è model-free, atomico e idempotente;
- profili mancanti falliscono chiaramente e nessun processo legacy viene
  invocato;
- auto-decompose e swarm restano isolati dal flusso;
- nessuna API backend viene chiamata da Kanban;
- task e OrgRun completati producono report locali verificabili;
- la dashboard espone `Board | Logbook` sotto il titolo Agentic-Kanban;
- la sessione `20260727_191342_7b2122` viene ripresa e completata senza
  duplicazioni, retry loop o completion non supportate da evidenza.
