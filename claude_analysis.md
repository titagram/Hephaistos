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
| 1 | **Retrieval 100% lessicale**: nessun embedding, nessun vettoriale, nessun FTS sul progetto. Recall = substring + token-set + BM25 fatto a mano. | Un agent che "chiede al backend" trova solo ciò che è già nominato con le parole esatte. Sinonimi, parafrasi, match cross-file/cross-lingua vengono persi. | A + B |
| 2 | **Indicizzazione euristica**: solo Python usa AST reale; PHP/TS/SQL sono decine di regex. Nessun tree-sitter, nessun call-graph/data-flow interprocedurale, forte bias Laravel. | Fragile su codice reale (dispatch dinamico, façade, query costruite a stringa). Fedeltà insufficiente per un fix affidabile. | B |
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

**Metodo di estrazione**: Python via `ast` stdlib (`:11686`); PHP/TS/SQL via
regex pure (`_build_php_graph:8470`, `_build_ts_graph:11424`, `_build_sql_graph:11274`).
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

### G1 — Nessun retrieval semantico *(blocca A e B, priorità massima)*
Confermato via grep: nessun embedding/vector/FTS nell'intera pipeline di
progetto (unico hit "embed" è una stringa wiki in `hades_backend_jobs.py:3966`).
La memoria è un blob senza indice; il grafo è cercato con BM25 e overlap di
token fatti a mano (`__init__.py:2974`). **Conseguenza diretta**: il sogno
"chiedi al backend e risolvi il bug" è limitato dal fatto che il retrieval trova
solo ciò che è già stato nominato con le parole giuste. Questo è il singolo
cambiamento a più alto ritorno.

### G2 — Indicizzazione euristica e mono-linguaggio *(blocca B)*
Solo Python ha AST; PHP/TS/SQL sono regex. Nessun tree-sitter, ctags, jedi, LSP
usato per l'indicizzazione. Gli edge sono **dichiarativi** (import, route→handler,
model→table, test→symbol): **non c'è un call graph né data-flow interprocedurale**,
né edge cross-lingua. Le regex falliscono su dispatch dinamico, façade Laravel,
query a stringa. Cap silenziosi (`max_symbols=5000`) tagliano coverage su repo
grandi (`hades_backend_jobs.py:11929`). È il soffitto di *fedeltà*.

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
- **Drift client/OpenAPI**: `project-awareness/bootstrap` e la "graph search"
  sono chiamati dal client ma **assenti dalla spec** (`hades_backend_client.py:172`).
  Il contratto non è autoritativo.
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
   (tree-sitter + LSP)   │  │ Vector store   │   │ Graph store              │   │
   ─────────────────►    │  │ (pgvector)     │   │ (adjacency + CTE / graph)│   │
   symbols, call-graph,  │  │ code+memory+   │   │ call/data-flow, blast    │   │
   data-flow, slices     │  │ evidence embed │   │ radius, path queries     │   │
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

---

### FASE 0 — Fondamenta: contratto autoritativo + modularità

**0.1 Rendere l'OpenAPI la sorgente di verità**
- **Cosa**: test di contratto che verifica che ogni endpoint chiamato dal client
  esista nella spec, e che colma il drift (`project-awareness/bootstrap`,
  `graph/search`).
- **Dove**: nuovo `tests/hermes_cli/test_hades_openapi_contract.py`; correzione di
  `docs/hades/openapi-hades-v1.json`; enumerare le chiamate in
  `hades_backend_client.py:172-320`.
- **Perché**: senza contratto autoritativo, ogni fase successiva costruisce su
  API non garantite; il drift già esiste.
- **Verifica**: il test fallisce sugli endpoint mancanti, poi passa dopo il
  fix della spec.

**0.2 Registry di schemi versionati `hades.*.vN`**
- **Cosa**: un modulo che centralizza i nomi/versioni di schema
  (`hades.code_graph.v1`, `hades.kanban_task_work.v1`, `hades.causal_pack.v1`, …)
  con validazione e test di round-trip.
- **Dove**: nuovo `hermes_cli/hades_schemas.py`; riferimenti sparsi oggi in
  `hades_backend_sync.py:795`, `hades_kanban_task_contract.py`, `hades_causal_pack.py`.
- **Perché**: le fasi 1–5 introducono `.v2` (grafo con call-edge, memoria
  tipizzata). Serve migrazione ordinata e retro-compatibilità.
- **Verifica**: test che ogni artefatto prodotto valida contro lo schema
  dichiarato.

**0.3 Spezzare il monolite dell'indexer**
- **Cosa**: estrarre gli estrattori per-linguaggio in un package con
  interfaccia comune `LanguageIndexer.build_graph(root, files) -> GraphArtifact`.
- **Dove**: nuovo package `hermes_cli/hades_index/` (`python.py`, `php.py`,
  `typescript.py`, `sql.py`, `base.py`), spostando `_build_php_graph`
  (`hades_backend_jobs.py:8470`), `_build_ts_graph:11424`, `_build_sql_graph:11274`,
  e il ramo Python `:11660-11940`.
- **Perché**: 12k righe in un file rendono G2 non aggredibile; una registry di
  indexer pluggabili è il prerequisito per tree-sitter (Fase 2) e per nuovi
  linguaggi.
- **Verifica**: gli artefatti prodotti sono byte-identici prima/dopo il refactor
  (test di golden-artifact su un repo fixture).

---

### FASE 1 — Retrieval semantico ibrido  ⭐ *(massimo ROI)*

**1.1 Vector store sul backend**
- **Cosa**: aggiungere `pgvector` (Postgres) e tabelle di embedding per tre
  corpora: memoria, bug-evidence, nodi/slice del code-graph. Colonne
  `embedding vector(N)`, `model`, `dim`, `content_sha256`.
- **Dove**: backend — migrazioni `create_hades_embeddings_table`, servizio
  `app/Services/Hades/HadesEmbeddingService.php`; l'indexer di ricerca esistente
  `HadesSearchDocumentIndexer` diventa il punto in cui si calcola/aggiorna
  l'embedding (oggi chiamato da `DiagnosisReportController.php:320`).
- **Perché**: G1. È l'abilitatore di "chiedi al backend qualunque cosa".
- **Verifica**: feature test Laravel che inserisce due memorie parafrasate e
  verifica che la query semantica le trovi entrambe (recall che il BM25 non dà).

**1.2 Servizio di embedding (privacy-first)**
- **Cosa**: generare embedding con un **modello di code-embedding eseguibile
  localmente/self-hosted** (es. un modello di embedding servito via il proxy
  provider già presente), non un servizio esterno. Solo contenuto già redatto
  viene embeddato.
- **Dove**: riuso di `hermes_cli/proxy/` (già astrae i provider); nuovo
  `hermes_cli/hades_embeddings.py` per il lato locale (embedding delle slice
  prima dell'upload, opzionale) e chiamata backend per i corpora server-side.
- **Perché**: privacy (§6) + nessun lock-in provider (coerente con il pitch
  "use any model you want" del README).
- **Verifica**: test che nessun contenuto non-redatto entri nel path di
  embedding (riuso di `redact_secret`, `hades_backend_client.py:61`).

**1.3 Ricerca ibrida + reranking**
- **Cosa**: endpoint `GET /memory/search` e `GET /graph/search` che combinano
  BM25 (lessicale) + ANN (vettoriale) con fusione RRF e un rerank finale;
  restituiscono `score`, `match_type`, `provenance`.
- **Dove**: backend `HadesSearchService`; `routes/api.php`; client
  `hades_backend_client.py` (`memory_search:216`, e nuovo `graph_search` oggi
  mancante); provider `plugins/memory/hades_backend/__init__.py` — sostituire il
  ranking lessicale di `_score_item` (`:2166`) e il BM25 locale (`:2974`) con la
  chiamata ibrida quando il backend è raggiungibile, mantenendo il fallback
  lessicale offline.
- **Perché**: G1. Recall robusto su sinonimi/parafrasi/cross-file.
- **Verifica**: gate di qualità nuovo `recall@k` su un set di query/gold; deve
  battere il baseline lessicale su un dataset di regressione.

**1.4 Indice semantico anche in cache locale**
- **Cosa**: cache locale con FTS5 + (opzionale) indice vettoriale su SQLite
  (`sqlite-vec`), per-item invece del blob unico.
- **Dove**: `hades_backend_db.py:116` (`memory_cache`) → tabella per-item con
  colonne indicizzabili; `replace_memory_cache:1020` diventa upsert per-item con
  delta.
- **Perché**: G1 + G4.7 (delta sync). Recall decente anche in modalità degradata
  offline.
- **Verifica**: test che una ricerca offline post-sync trovi un item per
  sinonimo, e che un secondo sync applichi solo il delta.

---

### FASE 2 — Indicizzazione ad alta fedeltà

**2.1 Tree-sitter come parser universale**
- **Cosa**: sostituire le regex PHP/TS/SQL con grammatiche tree-sitter (40+
  linguaggi), estraendo simboli, import, definizioni con posizioni precise.
- **Dove**: `hermes_cli/hades_index/*` (Fase 0.3); dipendenza opzionale
  lazy-installata via `tools/lazy_deps.py` (coerente con la policy delle extra
  in `pyproject.toml`).
- **Perché**: G2. Elimina la fragilità delle regex e il bias Laravel; abilita
  nuovi linguaggi senza scrivere nuovi parser.
- **Verifica**: golden-artifact su fixture multi-linguaggio; parità o
  superiorità di copertura simboli vs. l'estrattore regex attuale.

**2.2 Call graph e data-flow via LSP**
- **Cosa**: costruire edge `calls`, `references`, `implements`, `data_flow`
  interprocedurali sfruttando i language server. Il modulo `agent/lsp` esiste
  già (oggi solo per editing live): riusarlo in modalità batch di indicizzazione.
- **Dove**: nuovo `hades_index/lsp_resolver.py`; estende gli edge in
  `hades.code_graph.v2`; backend: modello grafo reale (tabelle di adiacenza +
  query CTE ricorsive per path-finding/blast-radius, o graph DB dedicato) al
  posto del walk su JSON.
- **Perché**: G2. Localizzazione precisa del bug e analisi d'impatto
  ("cosa rompe questo fix") richiedono un vero grafo di chiamate.
- **Verifica**: test di path-finding (dato route→…→tabella) e di blast-radius su
  fixture; il causal pack può ora citare una *catena* verificabile, non refs
  isolati.

**2.3 Indicizzazione incrementale + graph diff per commit**
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

**3.1 Presence + activity feed**
- **Cosa**: heartbeat continuo dello stato agent (branch, file dirty, focus
  corrente, task attivo) e promozione di Persephone a servizio di presence +
  feed attività, non semplice inbox.
- **Dove**: `hades_plugin_tasks.py:360` (`_git_state` da snapshot una-tantum a
  heartbeat periodico); `hades_backend_sync.py:990` (Persephone); backend: nuovi
  `POST /presence/heartbeat`, `GET /presence`, canale `GET /persephone/events`
  esteso con eventi di presence.
- **Perché**: G3. "Chi sta lavorando su cosa, ora" è il prerequisito di tutto il
  resto della coordinazione.
- **Verifica**: due binding simulati; l'uno vede l'altro attivo entro l'intervallo
  di heartbeat.

**3.2 Code-claims (soft lock su file/simboli) + conflict detection**
- **Cosa**: quando un agent inizia a lavorare su un insieme di file/simboli,
  pubblica un *claim* di intenzione; altri agent con claim/branch sovrapposti
  ricevono un avviso di conflitto **prima** di iniziare.
- **Dove**: backend: tabella `hades_code_claims` (project, binding, refs,
  scope, ttl, stato) + endpoint `claim`/`release`/`GET conflicts`; locale:
  estendere il contratto di claim (oggi solo `work_item_id`+`local_workspace_id`,
  `hades_plugin_work_items_client.py:178`) con scope path/symbol; superficie nel
  contesto agent (skill `hades-coordination` in `hades_coordination.py`).
- **Perché**: G3. È la traduzione letterale di "non pestarsi i piedi": non
  esiste oggi alcun lock a grana file/regione.
- **Verifica**: test in cui due agent claimano refs sovrapposte → il secondo
  riceve `conflict` con il riferimento all'agent e al task in corso.

**3.3 Canale di messaggistica agent↔agent**
- **Cosa**: hand-off e negoziazione dirette tra agent (es. "sto rifattorizzando
  BookingController, aspetta prima di toccarlo") via messaggi Persephone tipizzati.
- **Dove**: backend `POST /persephone/messages` (già presente) esteso con
  tipi `handoff`, `conflict_warning`, `question`; locale: consumo nel loop di
  sync e superficie nel contesto.
- **Perché**: G3. Trasforma coordinazione implicita (coda) in negoziazione
  esplicita.
- **Verifica**: test round-trip di un messaggio tipizzato tra due binding.

**3.4 Distribuzione lavoro con dipendenze**
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
  dipendenze (tree-sitter, pgvector client, sqlite-vec) come **extra
  lazy-installate**, non nel core.
- **Migrazione & retro-compatibilità**: introdurre `.v2` accanto a `.v1`
  (registry 0.2); il backend serve entrambi finché tutti i binding non sono
  ri-sincronizzati; nessun big-bang.
- **Test**: mantenere TDD e i gate no-codebase esistenti; aggiungere dataset di
  regressione per retrieval (recall@k) e per coordinamento (conflict detection).
- **Vincolo core stretto**: nessun nuovo model-tool nel core Hermes; tutto al
  bordo Hades (già regola di progetto).

---

## 7. Roadmap prioritizzata

**Quick wins (settimane, alto valore, basso rischio)**
1. **Fase 0.1 + 0.3** — contratto autoritativo + spezzare il monolite. Sblocca
   tutto il resto.
2. **Fase 1.1–1.3** — retrieval semantico ibrido. **Singolo cambiamento a più
   alto ROI**: moltiplica il valore di ogni conoscenza già nel backend.

**Medio termine (i due pilastri)**
3. **Fase 3.1–3.2** — presence + code-claims + conflict detection. Realizza la
   promessa "non pestarsi i piedi" (Pilastro A), che oggi è solo aspirazione.
4. **Fase 2.1–2.2** — tree-sitter + call graph. Alza il soffitto di fedeltà
   (Pilastro B).

**Lungo termine (differenziazione "non plus ultra")**
5. **Fase 4** — memoria tipizzata, intelligente, con knowledge graph.
6. **Fase 5** — verifica del fix in sandbox: chiude il cerchio del Pilastro B ed
   è ciò che distingue "diagnosi plausibile" da "fix affidabile senza sorgente".
7. **Fase 6** — scala e osservabilità.

**La tesi in una riga**: le Fasi 1 e 3 sono ciò che porta Hades da "scaffolding
impressionante" a "prodotto che mantiene le sue due promesse"; le Fasi 2, 4 e 5
sono ciò che lo rende *state of the art*.

---

## 8. Rischi e note

- **Qualità dell'estrazione LLM (4.2)**: mitigata dal restare *proposte* gated,
  mai promozione automatica — coerente con la disciplina già presente.
- **Costo/latenza embedding (1.2)**: mitigato da modello self-hosted + embedding
  solo su delta (Fase 2.3 + 1.4).
- **Complessità del grafo reale (2.2)**: iniziare con edge `calls`/`references`
  via LSP sui linguaggi con language server maturi, poi allargare.
- **Over-fitting su Laravel**: il fixture rocket-club è utile ma il sistema è
  visibilmente tarato su PHP/Laravel (decine di `_php_*` in `hades_backend_jobs.py`).
  La Fase 2.1 (tree-sitter) e la 6.4 (eval multi-linguaggio) sono la cura.
- **Coordinamento e privacy**: presence/feed non devono esporre path o contenuti
  sensibili tra tenant diversi; scoping rigoroso.
```
