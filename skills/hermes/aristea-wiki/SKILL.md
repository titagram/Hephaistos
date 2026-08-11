---
name: aristea-wiki
description: Consulta e aggiorna la Wiki Aristea tramite il server MCP remoto versionato.
version: 1.0.0
required_environment_variables:
  - name: ARISTEA_MCP_TOKEN
    prompt: Token di accesso al server MCP Aristea
    required_for: Connessione a mcp-aristea.persephone.cc
---

# Wiki Aristea

Usa il server MCP `aristea_wiki` per cercare, consultare e aggiornare contenuti della Wiki Aristea.

## Configurazione client

Il server MCP remoto è `https://mcp-aristea.persephone.cc/mcp` e richiede questo header configurato in `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  aristea_wiki:
    url: "https://mcp-aristea.persephone.cc/mcp"
    headers:
      Authorization: "Bearer ${ARISTEA_MCP_TOKEN}"
```

Non chiedere mai di inviare il token in chat. Quando la skill è caricata localmente, Hermes raccoglie il token tramite secure secret entry e non ne espone il valore al modello.

## Consultazione

1. Per cercare, usa `wiki_search` o `wiki_list_pages`.
2. Per una pagina nota, usa `wiki_get_page` e annota `currentRevision`.
3. Per capire decisioni precedenti o una modifica controversa, usa `wiki_get_revisions`.
4. Non inventare pagine, fonti, revisioni o contenuto assente.

## Modifica di una pagina

1. Leggi sempre la pagina con `wiki_get_page` immediatamente prima di modificarla.
2. Conserva testo, fonti e significato non interessati dalla richiesta.
3. Usa `wiki_update_page` con `baseRevision` uguale a `currentRevision` appena letto e con una `note` breve e descrittiva.
4. Se la Wiki risponde con conflitto `409`, non ritentare automaticamente: rileggi pagina e revisioni, spiega il conflitto e chiedi come procedere.
5. Dopo una scrittura, comunica slug, URL e revisione risultante.

## Creazione, commenti e restore

- Usa `wiki_create_page` solo quando la richiesta di creare una nuova pagina è esplicita.
- Usa `wiki_add_comment` per discussioni che non devono modificare il contenuto principale.
- `wiki_restore_page_revision` richiede `confirm: true`, una revisione di base corrente e una richiesta utente esplicita. Il restore crea una nuova revisione e non cancella la cronologia.

## Sicurezza

- Il server applica un Bearer token e rate limiting; non aggirare né riprovare ripetutamente dopo un `429`.
- Non fornire credenziali Basic Auth della wiki ai tool o nei documenti.
- Il token condiviso dell'MVP identifica gli aggiornamenti come `MCP Aristea`; non dichiarare attribuzioni personali non verificabili.
