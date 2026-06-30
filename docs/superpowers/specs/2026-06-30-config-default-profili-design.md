# Config Default E Profili

Data: 2026-06-30
Stato: approvato per implementation planning

## Decisione

Hades usa il brand Hades per copy, documentazione e superfici utente, ma mantiene
Hermes come ABI/storage legacy per configurazione e profili.

Il default di storage resta:

- POSIX: `~/.hermes`
- Windows nativo: `%LOCALAPPDATA%\hermes`
- variabile primaria di runtime: `HERMES_HOME`

`HADES_HOME` puo' essere accettato come alias compatibile per wrapper Hades e
deployment nuovi, ma non deve creare un secondo root concorrente e non deve
diventare il solo contratto documentato per codice, plugin o profili.

## Obiettivi

- Evitare una migrazione dati non necessaria per utenti e profili esistenti.
- Preservare compatibilita' con skill, plugin, servizi gateway e tooling che
  assumono `HERMES_HOME` o path sotto `~/.hermes`.
- Rendere coerenti codice, test, installer e docs con una sola policy:
  Hades come brand, Hermes come contratto runtime/storage legacy.
- Mantenere i profili completamente isolati, senza ereditarieta' live dal
  profilo default.
- Tenere abilitate le sorgenti upstream/community di skill e plugin, etichettate
  come compatibili o community quando non sono curate dalla fork Hades.

## Non Obiettivi

- Rinominare il comando legacy `hermes` o rimuovere alias compatibili.
- Migrare automaticamente `~/.hermes` verso `~/.hades`.
- Introdurre ereditarieta' dinamica tra profili.
- Bloccare lo skills hub upstream, GitHub direct install, `skills.sh`,
  ClawHub, LobeHub, well-known skill endpoints o install URL diretti.
- Creare uno store plugin centralizzato Hades in questo step.

## Architettura

`hermes_constants.get_hermes_home()` resta il punto unico per risolvere la home
runtime. La sua policy deve essere:

1. override in-process esplicito, se presente;
2. `HADES_HOME` o `HERMES_HOME`, con entrambi propagati ai subprocess quando un
   wrapper Hades seleziona un profilo;
3. fallback platform-native compatibile Hermes (`~/.hermes` o
   `%LOCALAPPDATA%\hermes`).

Il nome della funzione e dei moduli puo' restare Hermes: e' parte dell'ABI
interno e riduce churn. Eventuali commenti e messaggi utente devono distinguere
tra brand Hades e contratto storage Hermes.

I profili restano sotto `<root>/profiles/<name>`, dove `<root>` e' il default
compatibile Hermes o il root custom indicato da `HERMES_HOME`/`HADES_HOME`.
`active_profile` rimane root-anchored, non profile-anchored, per permettere a
`hades -p coder profile list` e ai processi gateway di vedere tutti i profili.

## Flusso Profili

- `profile create` crea un profilo fresco con directory, `.env`, `SOUL.md`,
  skills seedate salvo opt-out, e metadata separati in `profile.yaml`.
- `--clone` copia configurazione, `.env`, SOUL, skills e identita' selezionata
  dalla sorgente; migra la config clonata allo schema corrente.
- `--clone-all` resta una snapshot piu' ampia, filtrando runtime/history e
  infrastruttura del default.
- Le modifiche future al default non si riflettono automaticamente nei profili
  gia' creati. Questo preserva cache, isolamento credenziali e prevedibilita'.

## Skill E Plugin

Le sorgenti skill upstream restano supportate. Hades puo' aggiungere un indice
curato proprio in futuro, ma questo step deve mantenere:

- official optional skills dalla repo locale;
- indice upstream/compatibile se ancora referenziato;
- GitHub repo/path e custom taps;
- `skills.sh`, well-known endpoints, marketplace community e URL diretti.

I plugin continuano a essere installati da repository Git o path locali nel
profilo attivo. Un plugin Hermes compatibile deve restare installabile finche'
usa l'API plugin mantenuta. La UI/docs devono evitare di presentare plugin o
skill upstream come "ufficiali Hades" se non sono curati dalla fork.

## Error Handling

- Se entrambi `HADES_HOME` e `HERMES_HOME` sono impostati a path diversi, il
  runtime deve scegliere una precedenza esplicita e produrre un warning chiaro.
- Se un `active_profile` punta a un profilo assente, il processo deve cadere sul
  default o fallire con messaggio azionabile in base alla superficie chiamante,
  senza scrivere nello storage sbagliato in silenzio.
- I test devono coprire il fallback default e la selezione profilo con env unset,
  `HERMES_HOME`, `HADES_HOME`, entrambi impostati e path custom/container.

## Testing

La verifica dell'implementazione deve includere:

- unit/regression test su `hermes_constants.get_hermes_home()` e
  `get_default_hermes_root()`;
- test su `_apply_profile_override()` per `-p`, sticky `active_profile`,
  `HERMES_HOME` root, `HADES_HOME` alias e profilo gia' selezionato;
- test su `hermes_cli.profiles` per creazione, clone, default root, Docker/custom
  root e isolamento path;
- guard docs/rebrand per impedire il ritorno di `~/.hades` o `HADES_HOME` come
  default documentato nei file correnti ad alta visibilita';
- `python3 scripts/docs_audit.py`;
- test mirati profile/config dopo le modifiche.

## Implementation Notes

L'implementazione va fatta subagent-driven: un subagent esplora codice e test
runtime/profili, uno controlla docs/installer/website ad alta visibilita', e un
reviewer finale cerca residui di split-brain `~/.hades`/`HADES_HOME`.

La correzione deve essere conservativa: niente refactor ampi dei profili, niente
migrazione dati, niente rimozione di sorgenti skill/plugin upstream.
