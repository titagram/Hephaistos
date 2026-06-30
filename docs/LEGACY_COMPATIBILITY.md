# Legacy Compatibility Policy

Questa policy decide come trattare i nomi Hermes rimasti dopo il rebranding
utente a Hades.

## Decisione

Hades e' il brand primario per nuove superfici utente, messaggi, help text,
wizard, dashboard, TUI, gateway copy, installer e documentazione operativa della
fork.

I nomi Hermes che sono gia' contratti runtime, storage, wire protocol,
compatibilita' CLI o identificatori di ecosistema restano supportati. Non vanno
rinominati in modo opportunistico durante bonifiche di copy. Un rename di questi
contratti richiede un task dedicato con migrazione esplicita, test di
backward-compatibility e una finestra in cui vecchio e nuovo nome convivono.

## Contratti Da Preservare

- CLI legacy: `hermes`, `hermes-agent` e `hermes-acp` restano alias di
  compatibilita'. La documentazione nuova deve preferire `hades`, `hades-agent`
  e `hades-acp`.
- Variabili ambiente: `HERMES_*` resta lo schema compatibile. Eventuali nuove
  variabili `HADES_*` devono essere introdotte solo con lettura duale,
  precedenza documentata e test di migrazione.
- Home directory e storage: `~/.hermes`, chiavi DB, file marker, cache,
  sessioni e nomi di campo persistiti con prefisso `hermes` restano invariati
  finche' non esiste una migrazione idempotente.
- Wire/API/plugin contracts: header, custom id, callback id, slug, topic,
  package extra, plugin identifier e deep link esistenti con nome Hermes restano
  accettati. Esempi: `X-Hermes-Session-Key`, `hermes_approve_once`, `/hermes`,
  `hermes photon`, `hermes-bot` quando e' identita' runtime di default.
- Moduli Python e package interni: `hermes_cli`, `hermes_state.py`,
  `get_hermes_home()` e nomi analoghi restano tecnici finche' non viene
  pianificato un rename a basso rischio.
- Integrazioni esterne gia' pubblicate o gestite da package manager possono
  mantenere nomi Hermes nelle istruzioni strettamente necessarie alla
  compatibilita' con quell'integrazione.

## Regole Operative

1. La copy visibile nuova usa Hades.
2. Un identificatore Hermes va cambiato solo se e' puramente presentazionale.
3. Se un nome Hermes puo' essere salvato da utenti, webhook, piattaforme,
   cron job, config, env, URL, token, database, storage locale, package manager
   o client esterni, trattarlo come contratto legacy.
4. Per introdurre un nome Hades equivalente a un contratto legacy, implementare
   prima dual-read o dual-registration, poi dual-write dove serve, poi test di
   migrazione. La rimozione del nome Hermes richiede una decisione separata.
5. I test di rebranding devono distinguere copy visibile da compatibilita':
   vietare residui Hermes nella copy utente, ma aggiungere guard espliciti per
   gli identificatori legacy che devono restare.

## Esempi

- Corretto: mostrare `hades setup` in un help text, ma continuare a rispettare
  `HERMES_BIN` come override esistente.
- Corretto: inviare notifiche gateway come "Hades update finished", ma filtrare
  anche vecchie notifiche "Hermes update finished" dalla history Discord.
- Corretto: creare nuovi progetti Photon come `Hades Agent`, ma riusare un
  progetto esistente chiamato `Hermes Agent`.
- Corretto: documentare Hades nel setup IRC, ma preservare `hermes-bot` come
  nickname di default se cambiarlo modificherebbe l'identita' runtime.
- Non corretto: rinominare `~/.hermes` o `HERMES_HOME` durante una bonifica
  copy senza migrazione e test end-to-end.
