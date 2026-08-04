---
name: langchain-knowledge
description: Use when consulting the LangChain/LangSmith knowledge base stored in Supermemory (project:langchain) — querying docs by topic, or refreshing/adding new documentation to the knowledge base.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [langchain, langsmith, supermemory, knowledge, retrieval]
    related_skills: [supermemory-taxonomy, hermes-memory-providers]
---

# LangChain Knowledge — Consultazione della knowledge base in Supermemory

## Overview

Una knowledge base strutturata sulla documentazione LangChain/LangSmith è salvata in Supermemory sotto il container `hermes`, taggata con `[project:langchain]`. È stata costruita a partire da `https://docs.langchain.com/llms.txt` (162 pagine scaricate, 51 chunk aggregati per topic e caricati come documenti).

Questa skill definisce come **consultarla** (retrieval) e come **aggiornarla** (nuovi caricamenti), rispettando i limiti del server.

## When to Use

- L'utente chiede informazioni su LangChain, LangSmith, valutazioni, tracing, deployment, self-hosted, LLM gateway, Fleet, Agent Server, prompt engineering.
- L'utente chiede di aggiornare la knowledge base con nuova documentazione.
- L'utente chiede "quello che sappiamo su X di LangChain" o "cerca nella knowledge di LangChain".

Non usare per: domande generiche su LLM che non riguardano la piattaforma LangChain/LangSmith.

## Knowledge Base — Struttura

Ogni documento ha il formato:

```
[project:langchain] [topic:<topic>] [type:reference] Documentazione LangSmith/LangChain (<topic>)
<contenuto della documentazione>
```

Topic disponibili (12):

| Topic | Copertura |
|---|---|
| `evaluation` | Evals offline/online, LLM-as-judge, code evaluator, composite evaluator |
| `observability` | Tracing, distributed tracing, semantic search, annotazioni, feedback |
| `prompts` | Prompt engineering, template format, commit, context hub, playground |
| `datasets` | Dataset JSON types, transformations, target function |
| `agent-server` | Agent Server: overview, scale, feedback, distributed tracing |
| `fleet` | Fleet agents, managed deep agents |
| `llm-gateway` | Gateway: access, API formats, routing, fallbacks, custom providers |
| `deployment` | Deploy cloud/self-hosted, server MCP, custom endpoints |
| `self-hosted` | Self-hosting completo: terraform, SSO, FIPS, disaster recovery, scale |
| `admin-billing` | Admin, billing, pricing, auth, ABAC, audit logs, data export |
| `agents-human` | Background run, cron jobs, human-in-the-loop, checkpointer |
| `api-reference` | Reference API REST LangSmith |

## Consultazione (Retrieval)

### Pattern 1: Ricerca per topic

Sempre scoped con il tag `[project:langchain]`:

```
supermemory_search(query="[project:langchain] [topic:evaluation] LLM as judge")
supermemory_search(query="[project:langchain] [topic:self-hosted] terraform aws")
supermemory_search(query="[project:langchain] [topic:llm-gateway] routing fallbacks")
```

### Pattern 2: Ricerca trasversale

Quando l'argomento attraversa più topic, includi `[project:langchain]` ma non il topic:

```
supermemory_search(query="[project:langchain] checkpointer persist")
supermemory_search(query="[project:langchain] pricing")
```

### Pattern 3: Verifica stato knowledge base

Per sapere se l'ingestione è completa (dopo un caricamento):

1. `supermemory_search(query="[project:langchain]", limit=5)`
2. Controlla che i risultati abbiano il campo `memory` non vuoto (non solo `id`).
3. Se tutti i `memory` sono vuoti → l'ingestione AI asincrona è ancora in corso o il server è sovraccarico.

## Aggiornamento (Scrittura)

**REGOLA D'ORO: mai caricare più di 3-5 documenti di fila.** Il server Supermemory (v0.0.6 self-hosted, VPS con RAM limitata) va in OOM con caricamenti in raffica: 46 documenti in 15s hanno mandato il server in crash (2026-08-04). Ogni documento passa da estrazione → embedding → summarizzazione AI in parallelo.

### Procedura sicura di caricamento

1. Prepara i chunk (testo pulito, ~10-18KB max per chunk).
2. Carica **1 documento alla volta** con pausa di **10-15 secondi**.
3. Dopo ogni 3 documenti, fai una pausa di 60 secondi.
4. Mai più di 20 documenti in una sessione di caricamento.
5. Usa il client del plugin, NON curl con chiavi in chiaro:

```python
# Da /home/ubuntu/Hephaistos con .venv/bin/python
import sys; sys.path.insert(0, '/home/ubuntu/Hephaistos')
from plugins.memory.supermemory import _SupermemoryClient
key = [l.split('=',1)[1].strip() for l in open('/home/ubuntu/.hermes/.env')
       if l.startswith('SUPERMEMORY_API_KEY=')][0]
c = _SupermemoryClient(api_key=key, timeout=15.0,
                       container_tag='hermes', base_url='https://persephone.cc')
res = c.add_memory("[project:langchain] [topic:...] [type:reference] ...",
                   metadata={"project": "langchain", "topic": "...", "type": "reference"})
print(res)
```

### Limiti di dimensione

- Chunk ottimali: **10-18KB**. Oltre ~20KB il server fatica nell'estrazione.
- Non caricare HTML grezzo: estrai prima il testo (le pagine `pricing-plans` ecc. di docs.langchain.com sono HTML, non markdown — verifica che il file non inizi con `<!DOCTYPE`).
- Contenuti con URL `\\&` escapati falliscono con "Cannot extract content: All extractors rejected" — sostituire `\\&` con `&` prima del caricamento.

## Pitfalls

1. **Caricamento in raffica → OOM.** 46 documenti in 15s hanno crashato il server (memoria esaurita). Rispetta i limiti della sezione Aggiornamento.
2. **Chunk troppo grandi.** Oltre ~20KB l'estrazione fallisce o degrada. Splitta su separatori `---` o capoversi.
3. **HTML grezzo al posto del markdown.** Alcune pagine di docs.langchain.com (es. pricing-plans, 288KB) sono HTML. Controlla sempre l'inizio del file.
4. **URL con `\\&`.** Il server li rigetta con 400 "Cannot extract content". Fix: `re.sub(r'\\&', '&', text)`.
5. **Memory entry vuote dopo l'upload.** L'ingestione è asincrona: i documenti restano in stato `embedding`/`indexing` per minuti. Non ri-caricare per "velocizzare" — peggiora il problema.
6. **Topic con estensione `.md`.** Se il nome file è `fleet.md`, il topic diventa `fleet.md` — normalizza togliendo l'estensione (`name.split('.')[0]`).
7. **Confondere documenti e memory entries.** `add_memory` ritorna un DOCUMENT id; `forget` vuole il MEMORY ENTRY id dalla ricerca.
8. **Server giù dopo un crash.** Probes: `GET /v3/openapi` → 200 = su. `GET /v4/reference` → 401 = BasicAuth atteso, NON è un errore.

## Verification Checklist

- [ ] Query sempre scoped con `[project:langchain]`
- [ ] Risultati con campo `memory` vuoto → ingestione in corso, non conclusa
- [ ] Prima di un caricamento: chunk ≤18KB, niente HTML, URL fixati
- [ ] Max 1 documento / 10-15s, max 3 di fila, pausa lunga dopo
- [ ] Topic normalizzati senza estensione `.md`
- [ ] Dopo il caricamento: verificare con una search taggata che i `summary` si popolino
