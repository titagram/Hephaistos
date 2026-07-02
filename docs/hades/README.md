# Hades MVP Documentation

This directory is the stable user and developer documentation for the Hades MVP.
It replaces ad-hoc operational notes for install and backend onboarding.

## Install

- For the end-to-end public path, start with [launch.md](launch.md).
- Start with [installation.md](installation.md) for the tokenized one-liner and
  platform notes.
- Use [backend.md](backend.md) for manual backend setup, project linking, and
  shared memory behavior.

## Backend

The MVP backend is the Laravel Hades API under `/api/hades/v1`. The canonical
local contract fixture is [openapi-hades-v1.json](openapi-hades-v1.json).

## Operations

- [operations.md](operations.md) covers shared memory, read-only jobs,
  `waiting_confirmation`, and Persephone.
- [docker-production.md](docker-production.md) covers the supported self-hosted
  Docker production profile, auth, egress, backups, restore, updates, and
  break-glass host networking.
- [doctor-troubleshooting.md](doctor-troubleshooting.md) covers `hades doctor`,
  cleanup, degraded states, and recovery.
- [support-runbook.md](support-runbook.md) maps launch failures to safe support
  commands, recovery actions, and escalation.
- [developer-flow.md](developer-flow.md) covers local-only subagent and model
  routing defaults.

## Troubleshooting

Run:

```bash
hades doctor
hades backend status --json
hades backend sync
```

Tokens are stored as profile secrets in `.env`; behavioral settings stay in
`config.yaml`.

Historical coordination files such as `docs/backend-agent-coordination.md` are
maintainer evidence, not the primary user documentation path.
