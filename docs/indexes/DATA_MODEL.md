# Data Model

## Session Store

Fonte: `hermes_state.py`.

Tabelle osservate:
- `schema_version`
- `sessions`
- `messages`
- `state_meta`
- `compression_locks`
- `telegram_dm_topic_mode` (migrazione esplicita topic Telegram)
- `telegram_dm_topic_bindings` (migrazione esplicita topic Telegram)

Indici/FTS osservati:
- `idx_sessions_source`
- `idx_sessions_source_id`
- `idx_sessions_parent`
- `idx_sessions_started`
- `idx_messages_session`
- `idx_compression_locks_expires`
- deferred indexes su `messages.active`, `sessions.session_key`,
  gateway peer.
- `messages_fts` FTS5.
- `messages_fts_trigram` FTS5 trigram per CJK/substring search.

Regola operativa: `SCHEMA_SQL` e `_reconcile_columns()` sono la fonte per
additive column migrations; leggere i commenti di `hermes_state.py` prima di
toccare schema o FTS.

## Kanban Store

Fonte: `hermes_cli/kanban_db.py`.

Tabelle osservate:
- `tasks`
- `task_links`
- `task_comments`
- `task_events`
- `task_runs`
- `task_attachments`
- `kanban_notify_subs`

Regola operativa: il file contiene migrazioni additive, lock e rebuild di
tabelle driftate. Non cambiare schema kanban senza test mirati.

## Project Store

Fonte: `hermes_cli/projects_db.py`.

Tabelle osservate:
- `projects`
- `project_folders`
- `project_meta`
- `discovered_repos`

## Cron Store

Fonte: `cron/jobs.py`.

Persistenza osservata:
- Job JSON: `$HERMES_HOME/cron/jobs.json`.
- Output: `$HERMES_HOME/cron/output/{job_id}/{timestamp}.md`.
- Lock: `<cron dir>/.jobs.lock`.
- Heartbeat ticker: file gestito da `cron/jobs.py` e `cron/scheduler.py`.

## Verification Evidence

Fonte: `agent/verification_evidence.py`.

Tabelle osservate:
- `meta`
- `verification_events`
- `verification_state`

## Plugin/Memory Store Osservati

Fonti:
- `plugins/memory/holographic/store.py`: `facts`, `entities`,
  `fact_entities`, `facts_fts`, `memory_banks`.
- `plugins/memory/retaindb/__init__.py`: tabella `pending`.

Da completare per plugin specifici: ogni plugin puo portare schema proprio o
file state sotto `$HERMES_HOME`.

## Comandi Di Verifica

```bash
rg -n 'CREATE TABLE|CREATE VIRTUAL TABLE|CREATE INDEX' hermes_state.py hermes_cli/kanban_db.py hermes_cli/projects_db.py agent/verification_evidence.py plugins -g '*.py'
```
