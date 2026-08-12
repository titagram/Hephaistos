---
name: aristea-wiki
description: Use when consulting or safely updating the Aristea Wiki through its remote MCP server.
version: 1.3.0
author: Hermes Agent
license: MIT
required_environment_variables:
  - name: MCP_ARISTEA_WIKI_API_KEY
    prompt: Token di accesso al server MCP Aristea
    required_for: Connessione a mcp-aristea.persephone.cc
metadata:
  hermes:
    tags: [mcp, wiki, aristea, versioning, knowledge-base]
    related_skills: []
---

# Wiki Aristea

## Overview

Usa il server MCP remoto `aristea_wiki` per consultare e aggiornare la Wiki Aristea tramite le sue API versionate. Il server applica autenticazione Bearer e rate limiting; la Wiki resta l'autorità per validazione, revisioni e conflitti.

Per il deploy e la verifica pubblica del server, consulta [`references/remote-mcp-deployment.md`](references/remote-mcp-deployment.md): separa DNS, TLS, routing, autenticazione e framing SSE del protocollo.

## When to Use

- L'utente chiede di cercare, leggere o aggiornare contenuti nella Wiki Aristea.
- Serve consultare revisioni, aggiungere commenti o ripristinare una revisione specifica.
- Serve modificare una pagina preservando fonti, metadati e significato esistente.

Non usare per contenuti non destinati alla Wiki o per aggirare conflitti, limiti di richiesta o autenticazione.

## Configurazione client

Configura il server MCP senza incorporare segreti in file versionati:

```yaml
mcp_servers:
  aristea_wiki:
    url: "https://mcp-aristea.persephone.cc/mcp"
    headers:
      Authorization: "Bearer ${MCP_ARISTEA_WIKI_API_KEY}"
    timeout: 30
```

`MCP_ARISTEA_WIKI_API_KEY` è un secret locale richiesto. Non chiedere mai di inviarlo in chat, non stamparlo e non salvarlo in `config.yaml`, skill, codice o repository. Su canali chat il secret deve essere già configurato nell'ambiente del client.

**Criterio di completamento:** il client espone `aristea_wiki` e il token non compare in output o sorgenti versionate.

## Consultazione

1. Usa `wiki_search` soltanto per discovery globale: può restituire risultati `page`, `question` e `decision` sintetici.
2. Non interpretare l'excerpt di una `question` come risposta: può contenere soltanto il suo contesto.
3. Per una pagina usa `wiki_get_page`; per una domanda usa `wiki_get_question`; per una decisione usa `wiki_get_decision` prima di formulare conclusioni.
4. Usa `wiki_get_revisions` quando servono contesto storico, autore o contenuto di una revisione di pagina.
5. Riporta soltanto informazioni restituite dalla Wiki; non inventare pagine, fonti, revisioni, risposte o decisioni.

**Criterio di completamento:** ogni affermazione è riconducibile alla rappresentazione completa dell'entità restituita dalla Wiki, non al solo risultato sintetico di ricerca.

## Domande: retrieval e risposta

Le `question` sono quesiti di dominio da risolvere. Non sono query dell'utente e non sono automaticamente fonti informative.

1. Elenca o filtra con `wiki_list_questions`, usando stato, priorità, area, destinatario o testo quando disponibili.
2. Prima di interpretare o rispondere, chiama sempre `wiki_get_question` sull'ID stabile, per esempio `ECO-01`.
3. Tratta `question`, `context` e `answer` come campi distinti: `context` spiega il problema ma **non è la risposta**.
4. Considera una domanda risolta soltanto quando `status` è `Risolta` e `answer` è non vuota.
5. Per rispondere usa `wiki_update_question`, preservando dalla lettura corrente area, priorità, origine, destinatario, testo, contesto e pagine collegate. Non inventare la risposta e non copiare automaticamente `context` in `answer`.
6. Usa `In discussione` per una risposta ancora da validare; usa `Risolta` solo quando la risposta è effettivamente confermata.
7. Dopo l'aggiornamento comunica ID, stato e risposta registrata.

**Criterio di completamento:** la risposta compare nel campo `answer` della domanda riletta e lo stato è coerente; il contesto non è stato presentato come risposta.

## Decisioni

Una `decision` formalizza una scelta progettuale. Resta distinta sia dalla domanda sorgente sia dalla sua risposta.

1. Elenca con `wiki_list_decisions` e leggi il record completo con `wiki_get_decision`.
2. Crea una decisione con `wiki_create_decision` solo su richiesta esplicita o quando l'utente conferma che una risposta costituisce una scelta da formalizzare.
3. Se deriva da una domanda, valorizza `sourceQuestionId`; non usare il body della decisione al posto del campo `answer` della domanda.
4. Prima di `wiki_update_decision`, rileggi la decisione e preserva `sourceQuestionId`, `sourceCommentId` e pagine collegate non coinvolte dalla richiesta.
5. Usa gli stati `Proposta`, `Approvata` e `Superata` secondo il livello di conferma effettivo; non promuovere automaticamente una proposta.
6. Dopo una scrittura comunica ID, stato, domanda sorgente e pagine collegate.

**Criterio di completamento:** la scelta è registrata come decisione distinta e i suoi collegamenti sorgente sono preservati.

## Modifica sicura

1. Rileggi sempre la pagina con `wiki_get_page` immediatamente prima di scrivere.
2. Verifica testo, fonti e metadati; conserva tutto ciò che non è oggetto della richiesta.
3. Per `wiki_update_page`, invia `baseRevision` uguale alla `currentRevision` appena letta, un autore e una `note` breve ma descrittiva.
4. Se la Wiki restituisce `409`, non ritentare automaticamente: rileggi pagina e revisioni, presenta il conflitto e chiedi come procedere.
5. Dopo una scrittura, comunica slug, URL e revisione risultante.

**Criterio di completamento:** l'aggiornamento ha una nuova revisione confermata dalla Wiki, oppure un conflitto è esplicitamente presentato senza overwrite.

## Creazione, commenti e restore

- Chiama `wiki_create_page` solo per una richiesta esplicita di nuova pagina.
- Usa `wiki_add_comment` per discussioni che non devono cambiare il contenuto principale.
- Chiama `wiki_restore_page_revision` solo su richiesta esplicita, con `confirm: true` e una revisione di base corrente. Il restore crea una nuova revisione e non cancella la cronologia.

**Criterio di completamento:** pagina, commento o revisione risultante è identificabile nella risposta del tool.

## Common Pitfalls

1. **Scrittura da una revisione obsoleta.** Rileggi appena prima di aggiornare e usa `baseRevision` corrente.
2. **Retry cieco dopo `409` o `429`.** Il primo richiede una rilettura e decisione dell'utente; il secondo una pausa secondo `Retry-After`.
3. **Token in chat o commit.** Mantieni `MCP_ARISTEA_WIKI_API_KEY` esclusivamente nel secure secret store o nell'ambiente locale del client.
4. **Attribuzione inventata.** Nell'MVP il server usa l'autore di servizio `MCP Aristea`; non attribuire modifiche a persone senza fonte verificabile.
5. **Context scambiato per answer.** Un risultato di ricerca per una question può mostrare il contesto come excerpt: usa `wiki_get_question` e verifica esplicitamente `status` e `answer`.
6. **Decisione usata come risposta.** Se una scelta deriva da una domanda, aggiorna la domanda e registra separatamente la decisione con `sourceQuestionId` quando richiesto.

## Verification Checklist

- [ ] Il client usa `https://mcp-aristea.persephone.cc/mcp`.
- [ ] Il Bearer token è referenziato come `${MCP_ARISTEA_WIKI_API_KEY}`, non scritto in chiaro.
- [ ] La pagina è stata riletta prima di ogni aggiornamento.
- [ ] `baseRevision`, autore e nota sono presenti in ogni modifica.
- [ ] Un eventuale `409` non è stato ritentato automaticamente.
- [ ] Un risultato `question` o `decision` di `wiki_search` è stato verificato con il relativo tool `get`.
- [ ] Una domanda è dichiarata risolta solo con `status=Risolta` e `answer` non vuota; `context` non è stato usato come risposta.
- [ ] Una decisione derivata da una domanda conserva `sourceQuestionId` e resta distinta dalla risposta.
- [ ] La risposta finale riporta l'entità e lo stato/revisione risultante oppure il conflitto bloccante.
