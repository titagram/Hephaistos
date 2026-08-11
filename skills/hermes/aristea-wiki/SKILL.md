---
name: aristea-wiki
description: Use when consulting or safely updating the Aristea Wiki through its remote MCP server.
version: 1.1.0
author: Hermes Agent
license: MIT
required_environment_variables:
  - name: ARISTEA_MCP_TOKEN
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
      Authorization: "Bearer ${ARISTEA_MCP_TOKEN}"
    timeout: 30
```

`ARISTEA_MCP_TOKEN` è un secret locale richiesto. Non chiedere mai di inviarlo in chat, non stamparlo e non salvarlo in `config.yaml`, skill, codice o repository. Su canali chat il secret deve essere già configurato nell'ambiente del client.

**Criterio di completamento:** il client espone `aristea_wiki` e il token non compare in output o sorgenti versionate.

## Consultazione

1. Cerca con `wiki_search` oppure sfoglia con `wiki_list_pages`.
2. Leggi la pagina scelta con `wiki_get_page`; per un'eventuale modifica annota `currentRevision`.
3. Usa `wiki_get_revisions` quando servono contesto storico, autore o contenuto di una revisione.
4. Riporta soltanto informazioni restituite dalla Wiki; non inventare pagine, fonti, revisioni o contenuto assente.

**Criterio di completamento:** ogni affermazione sulla pagina è riconducibile alla risposta della Wiki.

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
3. **Token in chat o commit.** Mantieni `ARISTEA_MCP_TOKEN` esclusivamente nel secure secret store o nell'ambiente locale del client.
4. **Attribuzione inventata.** Nell'MVP il server usa l'autore di servizio `MCP Aristea`; non attribuire modifiche a persone senza fonte verificabile.

## Verification Checklist

- [ ] Il client usa `https://mcp-aristea.persephone.cc/mcp`.
- [ ] Il Bearer token è referenziato come `${ARISTEA_MCP_TOKEN}`, non scritto in chiaro.
- [ ] La pagina è stata riletta prima di ogni aggiornamento.
- [ ] `baseRevision`, autore e nota sono presenti in ogni modifica.
- [ ] Un eventuale `409` non è stato ritentato automaticamente.
- [ ] La risposta finale riporta pagina e revisione risultante oppure il conflitto bloccante.
