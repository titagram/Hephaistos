# Hades Agent — Analisi Architetturale e Piano di Implementazione "Cutting Edge"

> Documento redatto il 2026-07-09. Basato su un'analisi diretta del codice
> (`hermes_cli/hades_backend_*.py`, `plugins/memory/hades_backend/`,
> `docs/hades/`, i patch backend in `.hades-dev/tmp_remote_patch/backend/` e
> l'OpenAPI `docs/hades/openapi-hades-v1.json`).

---

## 1. Sintesi esecutiva

Hades è un fork di Hermes che aggiunge un **backend condiviso** (Laravel, API
`/api/hades/v1`) attorno a due promesse di prodotto:

- **Pilastro A — Memoria condivisa tra agenti.** Gli agent di più sviluppatori
  che lavorano sullo stesso progetto condividono conoscenza tramite il backend,
  così da "non pestarsi i piedi".
- **Pilastro B — Diagnosi/fix senza sorgente.** Il backend accumula conoscenza
  di progetto così precisa che un agent può diagnosticare (idealmente
  risolvere) un bug **senza il codice montato localmente**, interrogando solo il
  backend.

**Lo scaffolding è solido e maturo.** Esistono già: artefatti tipizzati
(code graph, bug evidence, evidence pack, **causal pack** replayabili),
gating della confidence lato server, source slice bounded e redatte, coda di
job con lease, proposte di memoria con concorrenza ottimistica, evaluator
"no-codebase" con gate di qualità. Questa è un'ottima base: la disciplina
"il modello è consumatore di conoscenza strutturata, non proprietario della
verità" (vedi `docs/superpowers/plans/2026-07-08-hades-causal-project-awareness.md`)
è esattamente quella giusta.

**Ma ci sono tre soffitti che impediscono lo stato dell'arte.** Se dovessi
riassumere in una frase: *il sistema oggi sa dove guardare, ma non sa cercare,
non vede abbastanza in profondità, e gli agent non si parlano davvero.*

| # | Soffitto | Impatto | Pilastro |
|---|----------|---------|----------|
| 1 | **Retrieval quasi tutto lessicale**: nessun embedding/vettoriale specifico Hades; la cache locale è un blob JSON senza indice; il graph search locale usa substring/token/BM25 fatto a mano. | Un agent che "chiede al backend" trova soprattutto ciò che è già nominato con parole vicine. Sinonimi, parafrasi, match cross-file/cross-lingua vengono persi o dipendono da coincidenze lessicali. | A + B |
| 2 | **Indicizzazione euristica**: Python usa AST e produce alcuni edge `calls`; PHP produce molti edge utili ma via regex; TS/SQL sono ancora più regex-driven. Nessun tree-sitter, nessun data-flow interprocedurale robusto, forte bias Laravel. | Fragile su codice reale (dispatch dinamico, façade, query costruite a stringa). Fedeltà insufficiente per un fix affidabile senza sorgente. | B |
| 3 | **Coordinamento grossolano**: l'unica esclusione è un *lease per work-item*. Nessuna presence, nessun lock su file/simboli, nessun conflict detection tra agent, nessun feed di attività, nessun canale agent↔agent. | La promessa "non pestarsi i piedi" **non è realizzata**: due agent possono modificare gli stessi file senza saperlo. | A |

Il resto del documento parte dallo stato attuale (§2), enumera i gap con
riferimenti puntuali al codice (§3), definisce l'architettura target (§4) e poi
il piano per fasi con **cosa / dove / perché / verifica** (§5). §6 tratta i temi
trasversali (sicurezza, migrazione, test), §7 dà la roadmap prioritizzata.

---

## 2. Stato attuale (as-is)

### 2.1 Flusso dati end-to-end

```
   Dev A (sorgente montato)                 Backend Laravel                Dev B (senza sorgente)
   ─────────────────────────                ───────────────                ──────────────────────
   hades backend sync                        /api/hades/v1                  hades backend status
     ├─ sync_git_tree ─────► artifact ──►  hades_agent_artifacts   ◄──── project-awareness/status
     ├─ populate_backend_ast ► code_graph    (JSON blobs)                  memory/search  ◄─┐
     ├─ source_slice_candidates              memory_proposals  ◄────────── memory/proposals  │  provider
     └─ read_source_slice (approvato) ►      hades_source_slices           graph/traverse   ─┤  hades_backend
                                             hades_bug_evidence            bug-evidence/search │
   Kanban task ──► agent_work_items ──►      causal_packs (+replay)        causal-packs/replay ┘
      (lease + heartbeat)                    diagnosis_reports ──► resolved_bug memory
```

### 2.2 Cosa viene estratto e caricato (indicizzazione)
Entry point `execute_job()` in `hermes_cli/hades_backend_jobs.py:12039`.

- **`hades.git_tree.v1`** (`_execute_sync_git_tree`, `hades_backend_jobs.py:3670`):
  manifest `{path, bytes, sha256}` — **nessun contenuto** (`raw_source_included: False`).
- **`hades.project_index.v1`** (`_build_project_index`, `:3480`): conteggi
  linguaggi, route Laravel, manifest di dipendenze, riassunto migrazioni DB.
- **`hades.code_graph.v1` / `hades.php_graph.v1`** (`_execute_populate_backend_ast`,
  `:11950`): il grafo strutturale reale — `symbols`, `edges`, `routes`,
  `database.tables`, `tests`, `logs`.
- **Source slice candidate** (`hades_source_slice_policy.py:62`): puntatori
  bounded (finestra `[riga-12, riga+24]`), prioritizzati per ruolo, cap a 200.
- **Source slice** on-demand (`_execute_read_source_slice`, `:3547`): unico
  percorso che carica codice vero, redatto, `max_lines=120`, `max_slice_bytes=64KB`.

**Metodo di estrazione**: Python via `ast` stdlib (`:11686`) con edge `calls`
euristici (`_append_python_call_edges`, `:10498`); PHP/TS/SQL principalmente via
regex (`_build_php_graph:8470`, `_build_ts_graph:11424`, `_build_sql_graph:11274`).
PHP ha molti edge specializzati (`calls_method`, `static_call`, DB/query,
Laravel/Blade/Livewire), ma restano pattern statici, non un grafo semantico
risolto da parser+type system.
`hades_backend_jobs.py` è un **monolite di ~12.058 righe**.

### 2.3 Memoria condivisa
Backend autoritativo; il locale è un client sottile (`plugins/memory/hades_backend/__init__.py`, 3633 righe).

- **Modello dato**: item di memoria = **dict JSON non tipizzati**, letti in modo
  difensivo (`_first_item_value`, `__init__.py:1990`). Domini in `SEARCH_DOMAINS`
  (`:82`); i "kind" sono stringhe che influenzano solo il ranking
  (`resolved_bug` +12, `verified_note_fact` +18 in `_score_item`, `:2166`).
- **Versioning a livello di snapshot**, non per-item (`hades_backend_cmd.py:1023`).
- **Conflict solo lato backend**: il locale registra passivamente
  `accepted/refused/conflicted` (`hades_backend_cmd.py:1032`). Nessun merge.
- **Cache locale** (`hades_backend_db.py:116`): **un unico blob JSON per
  workspace**, rimpiazzato in blocco a ogni sync (`replace_memory_cache:1020`).
  Nessun FTS, nessun indice.
- **Note quality** (`hades_note_quality.py`): l'unico fact-extractor automatico
  è **una singola regex** `ROUTE_HANDLER_RE` (`:16`). Tutto il resto sono
  proposte manuali.

### 2.4 Diagnosi senza sorgente
- **Causal pack** (`hades_causal_pack.py`): bundle di prova replayabile che lega
  bug evidence + graph refs + source slice + freshness + diagnosi. Un report
  high/medium source-free è **bloccato** se manca un causal pack valido
  (`no-codebase-diagnosis.md:141`). Ottima disciplina.
- **Gating server-side** (`DiagnosisReportController.php:64`): richiede
  `evidence_refs` non vuoto, `freshness.status=current`, `diagnosable_without_source`.
- **Freshness**: verdetto lato server per uguaglianza HEAD commit
  (`hash_equals`); nessun diff locale head-vs-deploy.
- **Replay** (`/causal-packs/{id}/replay`): controllo **strutturale**, non
  esecuzione runtime. Il fix non viene mai effettivamente verificato.

### 2.5 Coordinamento multi-agente
- **Work item plugin** (`hades_plugin_worker.py`): coda pull, `claim` →
  `lease_token`, heartbeat ogni 30s (`:355`). Il lease è **l'unica** primitiva
  di esclusività, ed è per work-item, **non per file/regione**.
- **Backend jobs**: capability-scoped, gated da `waiting_confirmation` +
  approvazione umana.
- **Persephone inbox** (`hades_backend_sync.py:990`, tabella `inbox_events`):
  poll/SSE, stato "letto/non letto". Non è presence.
- `WorkspaceBindingConflict` (`hades_backend_db.py:270`) scatta solo se *lo
  stesso workspace* viene rilinkato a un *progetto diverso*. Nessuna
  consapevolezza tra agent diversi sullo stesso progetto.

### 2.6 Stack backend
Laravel + DB relazionale (query builder raw, PK ULID, colonne JSON). Il "grafo"
è **JSON serializzato in tabelle relazionali** (`hades_agent_artifacts`);
`graph/traverse` è un walk applicativo. **Nessun** vector store, **nessun** graph
DB, **nessun** motore di ricerca dedicato (no Meilisearch/Scout/pgvector),
**nessun** broker di coda (no Redis/Horizon).

---

## 3. Gap analysis (perché non è ancora "cutting edge")

Ordinati per impatto sul valore di prodotto.

### G1 — Retrieval semantico assente *(blocca A e B, priorità massima)*
Confermato via grep sui moduli Hades: non c'è un indice embedding/vector per
memoria, bug evidence, source slice o graph nodes. La cache memoria locale è un
blob JSON per workspace (`hades_backend_db.py:116`); il ranking locale è
substring/token-set (`plugins/memory/hades_backend/__init__.py:2166`); il graph
search locale aggiunge BM25 manuale (`__init__.py:2974`) sopra documenti costruiti
da node/edge JSON. **Conseguenza diretta**: il sogno "chiedi al backend e risolvi
il bug" è limitato dal fatto che il retrieval trova soprattutto ciò che è già
stato nominato con parole giuste o molto vicine. Questo è il singolo cambiamento
a più alto ritorno.

### G2 — Indicizzazione euristica e non abbastanza semantica *(blocca B)*
Python ha AST e alcuni edge `calls`; PHP ha un set ricco di edge specializzati
(`calls_method`, `static_call`, route/model/table/query/session/cache/etc.) ma
sono quasi tutti prodotti da regex e pattern Laravel; TS/SQL sono ancora più
pattern-driven. Nessun tree-sitter, ctags, PHPStan/Psalm/TypeScript compiler API,
Jedi, o LSP è usato nel path di indicizzazione Hades. Manca un data-flow
interprocedurale affidabile e gli edge cross-lingua sono limitati. Le regex
falliscono su dispatch dinamico, façade, container binding non banali, metodi
generati e query costruite a stringa. Cap silenziosi (`max_symbols=5000`) tagliano
coverage su repo grandi (`hades_backend_jobs.py:11929`). È il soffitto di
*fedeltà*.

### G3 — Coordinamento assente al livello che conta *(blocca A)*
Nessun locking file/regione, nessuna presence, nessun conflict detection tra
agent, nessun feed di attività live, nessun canale agent↔agent. Lo stato git è
uno **snapshot una-tantum** al setup del worker (`hades_plugin_tasks.py:360`),
mai ripubblicato. La deduplicazione è solo "chi fa claim per primo" sullo stesso
task — non evita edit conflittuali da task diversi. **La promessa centrale del
prodotto è, ad oggi, aspirazionale.**

### G4 — Memoria loosely-typed, senza intelligenza *(blocca A)*
- Dedup solo per sha256 esatto (`hades_note_quality.py:133`) — nessun
  near-duplicate, nessuna entity resolution.
- Un solo tipo di fatto auto-estratto (una regex).
- Nessuna confidence/decadimento/rinforzo sugli item di memoria (la confidence
  esiste solo sui diagnosis report).
- Nessun knowledge graph *sui fatti* collegato al code graph.
- Versioning snapshot-level → edit concorrenti possono solo essere rifiutati,
  mai fusi.
- Cache sostituita in blocco → nessun delta sync, propagazione ritardata
  (piggyback a 60s, `__init__.py:22`).

### G5 — Il fix non viene mai verificato *(blocca B, "chiudere il cerchio")*
`replay` è strutturale. Non c'è sandbox né esecuzione runtime né esecuzione del
counterfactual. Per "risolvere un bug senza sorgente" in modo credibile, la
*patch proposta* deve essere buildata e testata da qualche parte.

### G6 — Governance del contratto e manutenibilità
- **Drift client/OpenAPI**: `project-awareness/bootstrap` è chiamato dal client
  (`hades_backend_client.py:255`) ma è assente dalla spec. La "graph search"
  invece oggi non è un endpoint dedicato: il provider implementa
  `hades_backend_graph_search` chiamando `memory/search` con `domain=artifacts`
  (`plugins/memory/hades_backend/__init__.py:1334`). Se si vuole un vero
  `GET /graph/search`, va introdotto esplicitamente in Fase 1, non trattato come
  endpoint già esistente.
- **Contratto non ancora autoritativo**: esiste `tests/hermes_cli/test_hades_backend_client.py`,
  ma copre casi enumerati manualmente. Serve un controllo che derivi i metodi dal
  client o almeno impedisca nuovi metodi non mappati nella spec.
- **Monolite** `hades_backend_jobs.py` (12k righe) → estrazione per-linguaggio
  non pluggabile, difficile da testare ed estendere.
- Nessun broker di coda / DAG di dipendenze tra job → niente fan-out/fan-in,
  scheduling piatto.

---

## 4. Architettura target ("non plus ultra")

```
                         ┌─────────────────────────────────────────────────────┐
                         │                 BACKEND (Laravel)                    │
   Indexer pluggabile    │  ┌───────────────┐   ┌──────────────────────────┐   │
   (tree-sitter + static │  │ Vector store   │   │ Graph store              │   │
   analysis, then LSP)   │  │ (pgvector se   │   │ (adjacency + CTE / graph)│   │
   ─────────────────►    │  │ DB=Postgres;   │   │ call/data-flow, blast    │   │
   symbols, call-graph,  │  │ altrimenti     │   │ radius, path queries     │   │
   data-flow, slices     │  │ adapter)       │   │                          │   │
                         │  └───────┬────────┘   └───────────┬──────────────┘   │
                         │          └─── Hybrid retrieval (BM25 + ANN + rerank) │
                         │  ┌──────────────────┐  ┌───────────────────────────┐│
                         │  │ Knowledge graph   │  │ Coordination service      ││
                         │  │ tipizzato (fatti, │  │ presence, code-claims,    ││
                         │  │ decisioni, conv.) │  │ conflict detect, msg bus  ││
                         │  │ conf + decay      │  │ (Redis/WebSocket/SSE)     ││
                         │  └──────────────────┘  └───────────────────────────┘│
                         │  ┌───────────────────────────────────────────────┐ │
                         │  │ Verifier sandbox job → build+test la patch      │ │
                         │  └───────────────────────────────────────────────┘ │
                         └─────────────────────────────────────────────────────┘
```

Principi da preservare (già corretti nel repo):
- **Hermes core resta stretto**: le capability nuove vivono al bordo Hades
  (provider di memoria, CLI, API backend, job, skill). Nessun nuovo model-tool
  del core.
- **Il modello è consumatore di conoscenza strutturata verificata**, mai
  self-certificante su freshness/awareness.
- **Solo slice bounded e redatte** lasciano la macchina; mai file interi.
- **Prompt caching preservato**: la guida arriva via skill/CLI/tool description,
  non mutando il system prompt a metà conversazione.

---

## 5. Piano di implementazione per fasi

Ogni item: **Cosa · Dove · Perché · Verifica**. Le fasi sono ordinate per
dipendenza; §7 dà la sequenza prioritizzata consigliata.

Convenzione: "locale" = repo Python `/Users/gabriele/Dev/Hephaistos`;
"backend" = Laravel (`app/Services/Hades/…`, `routes/api.php`, migrazioni).
Approccio: **TDD** e commit per task atomico, come già in uso nel repo.

### Regole di esecuzione per una sessione separata

Questa sezione è scritta per essere eseguita anche da un modello più leggero.
Non saltare passi e non accorpare task non correlati.

1. **Prima leggere i file citati dal task corrente**, non tutto il repo.
2. **Prima scrivere o aggiornare il test**, poi implementare.
3. **Non introdurre nuovi model-tool Hermes core.** Tutto resta in Hades:
   backend Laravel, CLI Hades, provider memoria, job, skill/docs.
4. **Non inviare contenuto sorgente non redatto al backend.** Gli embedding
   devono essere calcolati solo su contenuti già permessi: memoria, evidence,
   artifact metadata, source slice redatte e bounded.
5. **Non fare il refactor da 12k righe prima di avere golden test.**
   Estrarre codice senza harness rende impossibile capire se è cambiato il
   grafo prodotto.
6. **Non aggiungere `GET /graph/search` per inerzia.** Oggi il graph search
   live passa da `memory/search?domain=artifacts`; introdurre endpoint dedicato
   solo nel task che modifica anche backend, OpenAPI, client e provider.
7. **Ogni task termina con test mirati e commit atomico.** Se un test di
   regressione fallisce in modo non correlato, annotarlo nel commit message o
   nella risposta finale; non correggere aree estranee.
8. **Stop condition:** se il backend Laravel reale non è disponibile nella
   sessione, implementare solo i test/local client/docs e lasciare il task
   backend con stato esplicito "blocked: backend checkout missing".

---

### FASE 0 — Fondamenta leggere: contratto + safety harness

**0.1 OpenAPI contract: coprire `project-awareness/bootstrap`**
- **Cosa**: aggiornare il contratto per l'endpoint già chiamato dal client:
  `HadesBackendClient.bootstrap_project_awareness()` → `POST
  /api/hades/v1/project-awareness/bootstrap`.
- **Dove**:
  - `docs/hades/openapi-hades-v1.json`: aggiungere il path.
  - `tests/hermes_cli/test_hades_backend_client.py`: aggiungere un caso
    `CLIENT_ROUTE_CASES` per `bootstrap_project_awareness`.
- **Non fare**: non aggiungere `graph/search` in questo task.
- **Perché**: oggi il client chiama un endpoint non presente nella spec; questo
  è drift reale e già verificabile.
- **Verifica**:
  - Prima del fix: aggiungere il test e vedere fallire su route mancante.
  - Dopo il fix: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_client.py`.
  - `python3 -m json.tool docs/hades/openapi-hades-v1.json >/dev/null`.

**0.2 Contract guard: nessun nuovo client method senza mapping**
- **Cosa**: rendere esplicito il set dei metodi client che devono essere
  coperti da OpenAPI. Il test può partire da una whitelist manuale, ma deve
  fallire se si aggiunge un metodo pubblico a `HadesBackendClient` senza un caso
  in `CLIENT_ROUTE_CASES` o senza dichiararlo intenzionalmente unmapped.
- **Dove**: `tests/hermes_cli/test_hades_backend_client.py`.
- **Interfaccia attesa**:
  - Metodi esclusi: `close` e helper privati (`_request`, `_url`, ecc.).
  - Ogni altro metodo pubblico deve comparire in `CLIENT_ROUTE_CASES`.
- **Perché**: il test attuale copre la spec contro casi enumerati; manca il
  guard opposto che impedisce nuovi metodi non documentati.
- **Verifica**:
  - Aggiungere temporaneamente un metodo pubblico finto al client e verificare
    che il test fallisca; rimuoverlo.
  - Run finale: stesso pytest di 0.1.

**0.3 Golden harness per l'indexer prima di ogni refactor**
- **Cosa**: creare fixture piccole che coprano i quattro path principali:
  Python/FastAPI o Django, PHP/Laravel, TS/Next o Express, SQL/schema. Il test
  deve chiamare `execute_job({"capability": "populate_backend_ast", ...})` e
  verificare proprietà stabili dell'artefatto prodotto.
- **Dove**:
  - Fixture: `tests/fixtures/hades/indexer/{python_app,laravel_app,ts_app,sql_app}/`.
  - Test: `tests/hermes_cli/test_hades_backend_indexer_golden.py`.
- **Assert minimi**:
  - `artifact["schema"]` è `hades.code_graph.v1` o `hades.php_graph.v1`.
  - `raw_source_included` è `False`.
  - Almeno un symbol, una route o una table attesa è presente.
  - Edge noti: Python `calls`, PHP `calls_method` o `static_call`, TS `imports`,
    SQL `foreign_key` quando applicabile.
  - `source_slice_candidates` esiste dopo `_attach_source_slice_candidates`.
- **Perché**: il refactor dell'indexer è utile, ma solo se possiamo dimostrare
  che non cambia accidentalmente il grafo.
- **Verifica**:
  - `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_indexer_golden.py`.

**0.4 Solo dopo 0.3: seam minimo per indexer pluggabile**
- **Cosa**: introdurre un piccolo punto di dispatch senza spostare subito
  migliaia di righe:
  `hermes_cli/hades_index/__init__.py` con funzione
  `build_graph_for_workspace(workspace_root, candidates, omitted, payload)`.
  All'inizio questa funzione può chiamare le funzioni esistenti in
  `hades_backend_jobs.py`; il move fisico dei parser avviene in Fase 2.
- **Dove**:
  - Creare `hermes_cli/hades_index/__init__.py`.
  - Modificare solo `_execute_populate_backend_ast` in
    `hermes_cli/hades_backend_jobs.py`.
- **Perché**: serve un seam per Fase 2, ma non deve bloccare Fase 1 con un
  refactor enorme.
- **Verifica**:
  - Golden test 0.3 invariato.
  - `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/hermes_cli/test_hades_backend_indexer_golden.py`.

---

### FASE 1 — Retrieval semantico ibrido  ⭐ *(massimo ROI)*

**1.1 Backend: astrarre lo storage di ricerca prima del vector store**
- **Cosa**: introdurre un servizio backend unico per retrieval, senza ancora
  obbligare pgvector. Il servizio deve avere due modalità:
  - lexical: usa il meccanismo esistente su `hades_search_documents`;
  - semantic: disattivata se non esiste tabella/vector adapter.
- **Dove**:
  - Backend: `app/Services/Hades/HadesSearchService.php`.
  - Backend: controller esistente di `memory/search` e `bug-evidence/search`.
  - Test backend: feature test per `memory/search` che conferma parità con il
    comportamento attuale quando semantic è disattivato.
- **Interfaccia attesa**:
  - Input: `project_id`, `workspace_binding_id`, `query`, `domain`, `limit`,
    `include_raw_chunks`.
  - Output item: `id`, `domain`, `summary`, `score`, `match_type`,
    `provenance`, `raw_chunk`.
  - `match_type` iniziale ammesso: `lexical`.
- **Perché**: evita di legare subito il piano a Postgres/pgvector e crea il
  punto in cui aggiungere ANN/RRF senza toccare ogni controller.
- **Verifica**:
  - Test Laravel: query lessicale su memoria/evidence continua a trovare gli
    stessi item.
  - Nessuna migrazione vector in questo task.

**1.2 Backend: embedding table dietro adapter**
- **Cosa**: aggiungere storage embedding tenant-scoped dietro adapter. Se il DB
  è Postgres, implementare pgvector; altrimenti lasciare adapter `null` con
  capability `semantic_search=false`.
- **Dove**:
  - Backend migrazione: `hades_embeddings` con campi minimi
    `id`, `project_id`, `workspace_binding_id`, `domain`, `source_table`,
    `source_id`, `content_sha256`, `model`, `dim`, `embedding`, timestamps.
  - Backend service: `HadesEmbeddingStore`.
  - Backend capability/status: esporre se semantic search è disponibile.
- **Non fare**: non chiamare provider esterni da Laravel in questo task.
- **Perché**: separa schema/storage da generazione embedding e permette deploy
  anche su ambienti senza pgvector.
- **Verifica**:
  - Migration test: insert/upsert per stesso `(project_id, domain, source_id,
    content_sha256)` non duplica.
  - Test multi-tenant: query per `project_id=A` non legge embedding di
    `project_id=B`.

**1.3 Embedding generation privacy-first**
- **Cosa**: generare embedding solo da contenuti già ammessi alla ricerca:
  memory entries, bug evidence, evidence packs, source slice redatte, graph
  artifact summaries/nodes/edges; mai file raw.
- **Dove**:
  - Locale: nuovo `hermes_cli/hades_embeddings.py` se la generazione parte dal
    client.
  - Backend: `HadesSearchDocumentIndexer` se la generazione parte da documenti
    già indicizzati.
  - Redaction invariant: riusare `redact_secret` lato locale e il redactor
    backend già presente, se esiste.
- **Provider**: usare modello self-hosted/configurabile. Se manca provider,
  saltare semantic indexing e loggare `semantic_index_skipped`.
- **Perché**: l'embedding è una copia numerica del contenuto; deve rispettare
  la stessa policy delle source slice.
- **Verifica**:
  - Test che un contenuto con token/API key venga redatto prima dell'embedding.
  - Test che un file raw non possa entrare nel job di embedding.

**1.4 Hybrid retrieval: RRF tra lessicale e vettoriale**
- **Cosa**: combinare risultati lexical e semantic con Reciprocal Rank Fusion
  (RRF). Formula consigliata: `rrf_score += 1 / (60 + rank)` per ogni lista.
  Il campo `score` resta numerico e ordinabile; aggiungere `match_type`:
  `lexical`, `semantic`, `hybrid`.
- **Dove**:
  - Backend: `HadesSearchService`.
  - Endpoint esistenti: `GET /memory/search`, `GET /bug-evidence/search`.
  - Provider locale: nessun cambio necessario se il response shape resta
    compatibile.
- **Non fare**: non rimuovere il fallback locale in
  `plugins/memory/hades_backend/__init__.py`; serve quando il backend è
  irraggiungibile.
- **Perché**: migliora recall senza sacrificare precisione sui match esatti.
- **Verifica**:
  - Test 1: query esatta continua a mettere il match lessicale in alto.
  - Test 2: query parafrasata trova item semanticamente vicino che il lessicale
    puro non trova.
  - Test 3: se semantic adapter è off, l'endpoint ritorna solo `match_type=lexical`
    e non fallisce.

**1.5 Solo dopo 1.4: decidere se introdurre `GET /graph/search`**
- **Cosa**: scegliere tra due opzioni e implementarne una sola:
  - Opzione A, conservativa: mantenere `hades_backend_graph_search` sopra
    `memory/search?domain=artifacts`, ma indicizzare meglio graph nodes/edges
    come documenti di ricerca.
  - Opzione B, esplicita: aggiungere `GET /graph/search` con controller,
    OpenAPI, `HadesBackendClient.graph_search()`, e provider aggiornato.
- **Scelta consigliata**: Opzione A per primo rilascio. Opzione B solo se serve
  un payload graph-specific non rappresentabile bene come search document.
- **Dove se Opzione B**:
  - `docs/hades/openapi-hades-v1.json`.
  - `hermes_cli/hades_backend_client.py`.
  - `tests/hermes_cli/test_hades_backend_client.py`.
  - `plugins/memory/hades_backend/__init__.py`, `_handle_graph_search`.
- **Verifica**:
  - Opzione A: test esistente `test_hades_backend_graph_search_tool_queries_artifacts_live`.
  - Opzione B: stesso test deve verificare che venga chiamato `client.graph_search`,
    non `client.memory_search`.

**1.6 Cache locale per-item con FTS5, vector locale opzionale**
- **Cosa**: trasformare `memory_cache` da blob unico a cache per-item indicizzata.
  Mantenere lettura legacy del blob per migrazione.
- **Dove**:
  - `hermes_cli/hades_backend_db.py`: nuove tabelle
    `memory_cache_items` e, se disponibile, `memory_cache_items_fts`.
  - `replace_memory_cache`: upsert item per item e aggiorna versione snapshot.
  - `plugins/memory/hades_backend/__init__.py`: ricerca offline usa FTS5 quando
    presente, altrimenti fallback al blob/lista attuale.
- **Non fare**: non rendere `sqlite-vec` dipendenza obbligatoria.
- **Verifica**:
  - Test migrazione: cache legacy letta ancora correttamente.
  - Test delta: secondo sync con un item modificato non riscrive tutti gli item.
  - Test offline: query lessicale trova item dalla FTS locale.

---

### FASE 2 — Indicizzazione ad alta fedeltà

**2.1 Estrarre davvero gli indexer solo dopo golden harness**
- **Prerequisito**: Fase 0.3 verde.
- **Cosa**: spostare un linguaggio per commit, iniziando da TS o SQL (meno
  intrecciati), poi Python, infine PHP/Laravel.
- **Dove**:
  - `hermes_cli/hades_index/base.py`: protocollo `LanguageIndexer`.
  - `hermes_cli/hades_index/typescript.py`, `sql.py`, `python.py`, `php.py`.
  - `hermes_cli/hades_backend_jobs.py`: lasciare solo orchestration, file walk,
    policy/redaction, job routing.
- **Ordine obbligatorio**:
  1. Estrarre TS/SQL, run golden.
  2. Estrarre Python, run golden.
  3. Estrarre PHP, run golden e test esistenti del provider graph search.
- **Perché**: riduce blast radius. PHP è il più rischioso e va lasciato per
  ultimo.
- **Verifica**:
  - Golden test di Fase 0.3 passa dopo ogni estrazione.
  - `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/agent/test_hades_backend_memory_provider.py`.

**2.2 Tree-sitter come parser strutturale, non come sostituzione big-bang**
- **Cosa**: aggiungere tree-sitter dietro feature flag/config interna per
  linguaggio. Primo target consigliato: TypeScript/JavaScript, poi PHP. Il
  parser tree-sitter deve arricchire symbol/range/import; non deve rimuovere
  subito gli edge Laravel già coperti dalle regex.
- **Dove**:
  - `hermes_cli/hades_index/tree_sitter_adapter.py`.
  - Indexer per linguaggio estratti in 2.1.
  - Dipendenza opzionale/lazy, non import globale che rompe install minime.
- **Perché**: elimina fragilità sintattica senza buttare via edge domain-specific
  che oggi hanno valore.
- **Verifica**:
  - Fixture con sintassi che rompe la regex ma è parseable da tree-sitter.
  - Parità o aumento di symbol coverage rispetto al baseline golden.
  - Se tree-sitter non è installato, il sistema usa fallback e i test esistenti
    restano verdi.

**2.3 Call graph: static analyzers prima, LSP dopo**
- **Cosa**: costruire edge più affidabili con gli strumenti nativi dei linguaggi,
  evitando di partire subito da LSP batch se è instabile:
  - PHP: Composer autoload + PHPStan/Psalm metadata dove disponibile.
  - TS: TypeScript compiler API per references/calls quando `tsconfig` esiste.
  - Python: mantenere AST, poi valutare Jedi/pyright solo per progetti che lo
    configurano.
  - LSP: secondo step, dietro timeout e fallback, per `references`/`definition`.
- **Dove**:
  - `hermes_cli/hades_index/resolution.py`.
  - Nuovo schema `hades.code_graph.v2` solo quando gli edge arricchiti sono
    presenti; continuare a produrre `.v1` finché il backend non consuma `.v2`.
- **Perché**: i language server sono utili ma costosi/fragili in batch. Gli
  analyzer statici danno più controllo e testabilità.
- **Verifica**:
  - Test path route -> handler -> service -> table su fixture.
  - Test timeout: analyzer lento non blocca il job; produce artifact degradato
    con `truncated`/`omitted` esplicito.

**2.4 Indicizzazione incrementale + graph diff per commit**
- **Cosa**: indicizzare solo i file cambiati (`git diff` tra HEAD indicizzato e
  nuovo), mantenendo delta di grafo per commit; freshness diventa più fine di una
  semplice uguaglianza HEAD.
- **Dove**: `hades_backend_sync.py:377` (`_sync_baseline_artifacts`);
  `hades_backend_jobs.py:12039` (routing job); freshness calcolata anche localmente
  accanto al verdetto server (`hades_backend_status.py`).
- **Perché**: G2 + costo. Il full re-index non scala; il diff per commit abilita
  "questo bug è stato introdotto tra commit X e Y".
- **Verifica**: test che un cambio a 1 file produce un upload proporzionale, non
  un re-index completo.

---

### FASE 3 — Coordinamento multi-agente in tempo reale  ⭐ *(cuore del Pilastro A)*

**3.1 Backend presence minimale**
- **Cosa**: aggiungere presence senza locking: ultimo heartbeat per agent/binding.
- **Dove**:
  - Backend migration: `hades_agent_presence`.
  - Backend endpoint: `POST /api/hades/v1/presence/heartbeat`.
  - Backend endpoint: `GET /api/hades/v1/presence`.
  - OpenAPI + `HadesBackendClient` + test client.
- **Payload heartbeat minimo**:
  - `project_id`, `workspace_binding_id`, `agent_id`.
  - `current_branch`, `last_head_sha`, `dirty_status`.
  - `active_work_item_id` opzionale.
  - `observed_at`, `ttl_seconds`.
- **Privacy**: `display_path` opzionale e redatto; non inviare file content.
- **Perché**: prima bisogna sapere chi è online e su quale branch/HEAD.
- **Verifica**:
  - Due binding nello stesso project vedono presenza reciproca.
  - Presenza scaduta oltre TTL non appare come active.
  - Project A non vede presence di Project B.

**3.2 Locale: pubblicare heartbeat periodico**
- **Cosa**: riusare `_git_state` ma inviarlo periodicamente mentre il worker
  lavora e durante `hades backend sync`/poll, con backoff se backend non risponde.
- **Dove**:
  - `hermes_cli/hades_plugin_worker.py`: dentro heartbeat loop del work item.
  - `hermes_cli/hades_backend_sync.py`: piggyback leggero a intervallo, non a
    ogni tool call.
  - `hermes_cli/hades_plugin_tasks.py`: estrarre `_git_state` in helper
    riusabile se necessario.
- **Non fare**: non bloccare il worker se presence heartbeat fallisce.
- **Verifica**:
  - Test con fake client: `heartbeat_presence` chiamato almeno una volta durante
    work item lungo.
  - Test errore: exception nel presence heartbeat non interrompe `runner`.

**3.3 Backend code-claims advisory**
- **Cosa**: soft lock su path/symbol. Non impedisce tecnicamente il lavoro, ma
  ritorna conflitti visibili.
- **Dove**:
  - Backend migration: `hades_code_claims`.
  - Backend endpoints:
    - `POST /api/hades/v1/code-claims`
    - `POST /api/hades/v1/code-claims/{claim}/release`
    - `GET /api/hades/v1/code-claims/conflicts`
  - OpenAPI + client locale.
- **Schema claim minimo**:
  - `project_id`, `workspace_binding_id`, `agent_id`.
  - `work_item_id` opzionale.
  - `refs`: lista di oggetti `{type: "path"|"symbol", value: "..."}`
  - `scope`: `read`, `edit`, `refactor`, `verify`.
  - `branch`, `head_sha`, `ttl_seconds`, `status`.
- **Conflict rule minima**:
  - stesso `project_id`;
  - claim active non scaduto;
  - workspace/agent diverso;
  - almeno un ref uguale oppure path prefix overlap (`app/Foo` vs
    `app/Foo/Bar.php`);
  - `scope` conflict se uno dei due è `edit` o `refactor`.
- **Verifica**:
  - Due claim su stesso file: secondo response include `conflicts`.
  - Claim scaduto: non produce conflitto.
  - Project diverso: non produce conflitto.

**3.4 Locale: claim automatico per work item**
- **Cosa**: quando il payload del work item contiene path/symbol/repository
  scope, creare code-claim prima di lanciare l'agent runner. Rilasciarlo in
  `finally`.
- **Dove**:
  - `hermes_cli/hades_plugin_worker.py`.
  - `hermes_cli/hades_plugin_work_items_client.py`.
  - Eventuale helper in `hermes_cli/hades_coordination.py`.
- **Comportamento**:
  - Se claim ritorna conflitto, includere warning nel prompt/contesto del runner
    e nel risultato; non fallire automaticamente.
  - Se release fallisce, log warning e lascia scadere TTL.
- **Verifica**:
  - Fake client: claim chiamato prima di runner, release dopo runner.
  - Runner che solleva exception: release comunque chiamato.
  - Conflict response viene propagata nel prompt o nel metadata risultato.

**3.5 Messaggistica agent-agent tipizzata**
- **Cosa**: estendere Persephone con tipi minimi `conflict_warning`, `handoff`,
  `question`, `answer`; non costruire chat completa.
- **Dove**:
  - Backend: validazione payload di `POST /persephone/messages`.
  - Locale: consumo in `hades_backend_sync.py` e rendering sintetico nello stato.
- **Perché**: utile dopo i claim; prima dei claim diventerebbe solo inbox generica.
- **Verifica**:
  - Round-trip tra due binding con `conflict_warning`.
  - Messaggio per Project A non visibile a Project B.

**3.6 Distribuzione lavoro con dipendenze**
- **Cosa**: DAG di dipendenze tra work item + broker di coda (Redis/Horizon) al
  posto del pull piatto.
- **Dove**: backend coda; `hades_plugin_worker.py:35` (loop worker).
- **Perché**: G6. Fan-out/fan-in, priorità, e "il task B parte solo dopo A".
- **Verifica**: test che un item dipendente non venga claimato finché il
  predecessore non è `completed`.

---

### FASE 4 — Memoria condivisa intelligente

**4.1 Modello di memoria tipizzato**
- **Cosa**: gli item di memoria diventano tipizzati con schema:
  `fact`, `decision`, `convention`, `resolved_bug`, `entity`, `relationship`.
- **Dove**: backend modello memoria + `HadesSearchDocumentIndexer`;
  `plugins/memory/hades_backend/__init__.py` (`_first_item_value:1990`,
  `_score_item:2166`) legge campi tipizzati invece di sondare dict.
- **Perché**: G4. Tipi espliciti abilitano ranking, filtri e collegamento al
  grafo.
- **Verifica**: test che una `decision` e un `resolved_bug` siano filtrabili per
  tipo e abbiano provenance strutturata.

**4.2 Estrazione fatti/entità assistita da LLM**
- **Cosa**: sostituire la singola regex con estrazione LLM di fatti/entità/
  relazioni da note e conversazioni (con schema-forcing, così l'output è
  strutturato e verificabile).
- **Dove**: `hades_note_quality.py` (`_route_handler_facts:86` → pipeline
  generica); resta gated come *proposte* (revisione backend).
- **Perché**: G4. Un solo tipo di fatto auto-estratto è insufficiente.
- **Verifica**: fixture di note → set di fatti tipizzati; nessuna promozione
  automatica senza review.

**4.3 Dedup semantica + entity resolution + confidence/decay**
- **Cosa**: near-duplicate via embedding (Fase 1) + merge/supersede; score di
  confidence, peso per recency/uso, staleness e TTL sugli item.
- **Dove**: backend memoria (nuove colonne `confidence`, `last_used_at`,
  `superseded_by`); provider ranking; retention oggi crudo a tempo
  (`hades_backend_db.py:799`).
- **Perché**: G4. Evita l'accumulo di fatti ridondanti/obsoleti che avvelenano
  il recall nel tempo.
- **Verifica**: test che due fatti quasi-identici collassino e che un fatto non
  usato decada nel ranking.

**4.4 Knowledge graph sui fatti collegato al code graph**
- **Cosa**: collegare `verified_note_fact`/`decision` ai nodi del code-graph
  (symbol/route/table), così la memoria e il codice sono un unico grafo
  navigabile.
- **Dove**: backend (edge memoria↔grafo); `graph/traverse` estende i nodi
  memoria.
- **Perché**: G4 + B. "Perché questo codice è così?" trova la decision collegata.
- **Verifica**: traversal da un symbol che restituisce le decision collegate.

**4.5 Merge concorrente (oltre accept/refuse/conflict)**
- **Cosa**: versioning per-item + merge a 3 vie (o CRDT su campi additivi) per
  edit concorrenti di più agent.
- **Dove**: `hades_backend_cmd.py:1032` (oggi passivo); backend proposal service.
- **Perché**: G4. Con più sviluppatori, "solo rifiuto" produce attrito continuo.
- **Verifica**: test di due update concorrenti non conflittuali che vengono fusi.

---

### FASE 5 — Verifica del fix senza sorgente (chiudere il cerchio)

**5.1 Replay runtime in sandbox effimera**
- **Cosa**: `replay` del causal pack diventa esecuzione reale: su un device che
  *possiede* il sorgente (o in CI), si materializza un checkout sandbox al commit
  del pack, si riproduce il test fallente, si conferma il meccanismo.
- **Dove**: estendere `/causal-packs/{id}/replay`; nuovo job type
  `reproduce_bug`/`verify_patch` instradato ai binding source-owning; riuso dei
  backend di esecuzione già presenti (Docker/Daytona/Modal citati nel README).
- **Perché**: G5. Un causal pack "replayabile" oggi è solo strutturale; il valore
  vero è riprodurre e verificare.
- **Verifica**: test end-to-end su fixture rocket-club: il pack riproduce il
  fallimento e, applicata la patch proposta, il test passa.

**5.2 Proposta di patch source-free + verifica delegata**
- **Cosa**: l'agent senza sorgente propone una patch (diff) derivata dalla
  conoscenza backend; la verifica (build+test) è delegata a un worker
  source-owning che riporta l'esito.
- **Dove**: nuovo contratto `hades.patch_proposal.v1`; worker
  `hades_plugin_worker.py`; superficie nel no-codebase flow
  (`docs/hades/no-codebase-diagnosis.md`).
- **Perché**: B (la promessa piena "risolvere un bug senza vedere il sorgente").
- **Verifica**: eval che misura % di patch proposte source-free che passano i
  test alla verifica delegata.

**5.3 Esecuzione del counterfactual**
- **Cosa**: eseguire davvero il counterfactual invece di dargli solo uno score
  strutturale.
- **Dove**: `hades_no_codebase_eval.py` (metrica `counterfactual_refusal_coverage`);
  motore di replay 5.1.
- **Perché**: rende la metrica di rifiuto un fatto verificato, non un'euristica.
- **Verifica**: caso ambiguo in cui l'esecuzione conferma che l'evidenza è
  insufficiente e l'agent rifiuta correttamente.

---

### FASE 6 — Osservabilità, sicurezza, scala

- **6.1 Metriche di retrieval e diagnosi** (recall@k, MRR, tasso di rifiuto
  corretto, % fix verificati) nel quality report (`hades_quality_report.py`).
- **6.2 Isolamento multi-tenant** rigoroso sugli embedding e sul grafo
  (scope `project_id`+`binding`), audit trail di accessi a slice/evidence.
- **6.3 Broker di coda + backpressure** (Fase 3.4) per reggere molti agent.
- **6.4 Eval multi-linguaggio e su repo grandi** oltre al fixture Laravel
  rocket-club, per non over-fittare su Laravel.

---

## 6. Temi trasversali

- **Sicurezza & privacy**: l'embedding vale solo su contenuto **già redatto**
  (`redact_secret`); nessun file intero lascia la macchina (invariante attuale
  da preservare). Il vector/graph store è tenant-scoped. La postura supply-chain
  (pin esatti in `pyproject.toml`) è già eccellente — mantenere le nuove
  dipendenze (tree-sitter, pgvector client, sqlite-vec, analyzer SDK) come
  **extra lazy-installate**, non nel core.
- **Migrazione & retro-compatibilità**: introdurre `.v2` accanto a `.v1`
  (registry 0.2); il backend serve entrambi finché tutti i binding non sono
  ri-sincronizzati; nessun big-bang.
- **Test**: mantenere TDD e i gate no-codebase esistenti; aggiungere dataset di
  regressione per retrieval (recall@k) e per coordinamento (conflict detection).
- **Vincolo core stretto**: nessun nuovo model-tool nel core Hermes; tutto al
  bordo Hades (già regola di progetto).

---

## 7. Roadmap prioritizzata

Questa è la sequenza consigliata per lanciare esecuzione in un'altra sessione.
Se il modello è leggero, dargli **un blocco alla volta**.

**Blocco A — Contratto e guardrail (prima di tutto)**
1. Fare **0.1**: aggiungere OpenAPI path per `project-awareness/bootstrap` e
   relativo caso in `CLIENT_ROUTE_CASES`.
2. Fare **0.2**: test che ogni metodo pubblico di `HadesBackendClient` sia
   coperto o esplicitamente escluso.
3. Fermarsi e committare.
4. Test obbligatori:
   `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_client.py`
   e `python3 -m json.tool docs/hades/openapi-hades-v1.json >/dev/null`.

**Blocco B — Safety harness indexer, senza refactor grande**
1. Fare **0.3**: fixture golden multi-linguaggio e test dell'artefatto.
2. Fare **0.4** solo se 0.3 è verde: seam minimo `hades_index`, nessun move
   massivo di PHP/Python.
3. Fermarsi e committare.
4. Test obbligatori:
   `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/hermes_cli/test_hades_backend_jobs.py tests/hermes_cli/test_hades_backend_indexer_golden.py`.

**Blocco C — Retrieval backend, valore massimo**
1. Fare **1.1**: `HadesSearchService` lexical con shape stabile.
2. Fare **1.2**: embedding store adapter, semantic off se backend non supporta
   vector.
3. Fare **1.3**: generazione embedding privacy-first, senza provider esterno
   obbligatorio.
4. Fare **1.4**: RRF hybrid retrieval.
5. Decidere **1.5**: default consigliato Opzione A, niente nuovo
   `/graph/search` finché non serve.
6. Fermarsi e committare.
7. Test obbligatori: feature test Laravel per memory/evidence search, test
   multi-tenant embedding, test semantic-off fallback.

**Blocco D — Coordinamento, promessa "non pestarsi i piedi"**
1. Fare **3.1**: presence backend minima.
2. Fare **3.2**: heartbeat locale non bloccante.
3. Fare **3.3**: code-claims advisory e conflict detection.
4. Fare **3.4**: claim/release automatici nel worker.
5. Fare **3.5**: messaggi tipizzati solo dopo i claim.
6. Fermarsi e committare.
7. Test obbligatori: due binding simulati, TTL scaduto, project isolation,
   release in `finally` anche se il runner fallisce.

**Blocco E — Refactor/indexing avanzato**
1. Fare **2.1**: estrarre indexer un linguaggio alla volta, PHP per ultimo.
2. Fare **2.2**: tree-sitter dietro fallback.
3. Fare **2.3**: analyzer statici prima di LSP batch.
4. Fare **2.4**: incremental graph diff.
5. Fermarsi e committare dopo ogni linguaggio.
6. Test obbligatori: golden invariati dopo ogni estrazione.

**Blocchi successivi**
1. **Fase 4**: memoria tipizzata, dedup semantica, confidence/decay,
   knowledge graph.
2. **Fase 5**: verifica sandbox del fix e patch proposal source-free.
3. **Fase 6**: osservabilità, isolamento multi-tenant, scala.

**La tesi in una riga**: le Fasi 1 e 3 sono ciò che porta Hades da "scaffolding
impressionante" a "prodotto che mantiene le sue due promesse"; le Fasi 2, 4 e 5
sono ciò che lo rende *state of the art*.

---

## 8. Rischi e note

- **Qualità dell'estrazione LLM (4.2)**: mitigata dal restare *proposte* gated,
  mai promozione automatica — coerente con la disciplina già presente.
- **Costo/latenza embedding (1.2)**: mitigato da modello self-hosted + embedding
  solo su delta (Fase 2.3 + 1.4).
- **Complessità del grafo reale (2.3)**: iniziare con analyzer statici e edge
  verificabili; usare LSP batch solo dietro timeout/fallback.
- **Over-fitting su Laravel**: il fixture rocket-club è utile ma il sistema è
  visibilmente tarato su PHP/Laravel (decine di `_php_*` in `hades_backend_jobs.py`).
  La Fase 2.1 (tree-sitter) e la 6.4 (eval multi-linguaggio) sono la cura.
- **Coordinamento e privacy**: presence/feed non devono esporre path o contenuti
  sensibili tra tenant diversi; scoping rigoroso.
