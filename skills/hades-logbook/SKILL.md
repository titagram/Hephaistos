---
name: hades-logbook
description: Record one concise, factual Hades project-logbook entry after a durable mutation outcome.
---

# Hades project logbook

Use this skill only after one of these outcomes is already known:

- a durable project mutation completed;
- a meaningful terminal failure occurred; or
- a mutation was rolled back.

Do not write a logbook entry for a read, search, inspection, plan, hypothesis,
or progress update. Do not invent evidence, entry IDs, commit SHAs, test
results, reference handles, actors, or outcomes. Obtain every stated fact from
the completed workflow.

Write at most one concise factual entry for the outcome. Choose the matching
event type (`change`, `creation`, `failure`, `rollback`, `decision`, and so
on), a plain-language summary of at most 240 characters, a stable idempotency
key for this exact outcome, and only project-local references you actually
have. Put an optional short narrative in a regular UTF-8 file; never use stdin
or construct it through shell interpolation.

```bash
hades backend logbook write \
  --type change \
  --summary 'Migrated the durable outbox state.' \
  --idempotency-key 'migration:<stable-operation-id>' \
  --reference commit:<actual-commit-sha> \
  --narrative-file /path/to/factual-narrative.md
```

The command persists the request locally before contacting the backend. A
queued/retry or dead-letter result is degraded state, not success: preserve its
output and use the stated recovery action. A capability denial requires an
administrator to grant `write_project_logbook` and a re-registration with
`hades backend setup`; never claim that the entry was recorded remotely until
the command or a later sync reports `sent`.
