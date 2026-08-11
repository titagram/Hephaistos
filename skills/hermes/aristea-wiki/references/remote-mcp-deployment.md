# Deploy e verifica MCP remoto Aristea

Riferimento operativo per il server MCP remoto Aristea dopo una modifica a Docker, Traefik o DNS.

## Ordine di verifica

1. **Config Docker senza segreti in output**
   - Usare un `.env.mcp` locale, con permessi restrittivi, escluso da Git.
   - Validare la composizione prima dell'avvio: `docker compose -f compose.yaml -f compose.traefik.yaml config --quiet`.

2. **DNS**
   - Verificare il record `mcp-aristea.persephone.cc` con resolver locale e almeno un resolver pubblico.
   - DNS risolto non prova che Traefik abbia caricato il router.

3. **TLS e routing**
   - L'handshake deve presentare un certificato emesso per `mcp-aristea.persephone.cc`, non `TRAEFIK DEFAULT CERT`.
   - `GET https://mcp-aristea.persephone.cc/health` deve restituire `200` e `{"status":"ok"}`.
   - Un `404` con certificato di fallback indica tipicamente router/container non ancora pubblicato; non dichiarare il deploy riuscito.

4. **Perimetro di sicurezza**
   - `POST /mcp` senza Authorization deve restituire `401`.
   - Non leggere, stampare o trasmettere il token per provare una chiamata pubblica autenticata. Gli smoke test autenticati vanno eseguiti localmente con un token di test effimero o dal client autorizzato.

5. **Protocollo MCP**
   - Inizializzare il protocollo e richiedere `tools/list`.
   - Streamable HTTP può restituire eventi SSE (`event: message` / `data: {...}`) anche quando la richiesta è HTTP POST: il verifier deve estrarre `data:` prima di fare JSON parse.
   - Verificare che siano esposti gli otto tool Aristea e fare almeno una chiamata read-only (`wiki_list_pages` o `wiki_search`) contro la Wiki reale.

6. **Rate limit**
   - In un ambiente di test, superare il burst da un IP isolato e verificare `429` e `Retry-After`.
   - Non eseguire burst artificiali contro l'endpoint di produzione dopo il go-live.

## Criterio di go-live

Il go-live è completo soltanto quando DNS, certificato corretto, `/health` 200, `401` senza token, discovery MCP e una lettura autenticata sono tutti verificati.
