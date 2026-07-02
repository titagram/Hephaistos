# Workspace Manual Index

Questo indice raccoglie documentazione operativa per lavorare su Hades Agent
in modo ripetibile. Non sostituisce la documentazione prodotto in `website/docs/`
ne la guida estesa in `AGENTS.md`: serve come mappa rapida per futuri LLM e
sviluppatori nel checkout locale.

## Lettura Consigliata

- `docs/CODEX_AGENTS.md` - istruzioni specifiche per Codex su questa fork.
- `docs/implementation_plan.md` - checklist viva di brainstorming e lavoro.
- `AGENTS.md` - regole principali per agenti e contributori.
- `docs/PROJECT_OVERVIEW.md` - cosa contiene il progetto.
- `docs/ARCHITECTURE.md` - architettura osservata e flussi principali.
- `docs/RUNTIME.md` - prerequisiti, installazione, avvio, Docker.
- `docs/TESTING.md` - comandi reali di test, lint e typecheck.
- `docs/RELEASE_GATES.md` - matrice dei gate per backend MVP, PyPI, Docker,
  website, desktop e update.
- `docs/SOURCE_OF_TRUTH.md` - priorita tra codice, docs, test e CI.
- `docs/MAINTENANCE.md` - flusso operativo per iniziare e chiudere un task.
- `docs/LEGACY_COMPATIBILITY.md` - policy per alias, env var, path e nomi
  tecnici Hermes preservati dopo il rebranding Hades.
- `docs/backend-agent-coordination.md` - domande, risposte e conclusioni per il
  coordinamento con l'agente del backend Laravel.
- `docs/LOGBOOK.md` - log operativo dei cambi documentali/processuali.

## Indici Tecnici

- `docs/indexes/ROUTES_OR_ENTRYPOINTS.md`
- `docs/indexes/DATA_MODEL.md`
- `docs/indexes/SIDE_EFFECTS.md`
- `docs/indexes/DEPENDENCIES.md`
- `docs/indexes/SECURITY.md`

## Audit

Esegui:

```bash
python3 scripts/docs_audit.py
```

Lo script verifica documenti obbligatori, link Markdown locali, riferimenti
source-of-truth e presenza di almeno una entry nel logbook. E' parte stabile
del flusso locale/agentico quando si modificano questi docs o lo script di
audit; non e' ancora un gate CI globale.
