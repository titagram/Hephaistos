---
sidebar_position: 2.8
title: "Backend Setup"
description: "Connect Hades Agent to the Hades backend for shared project memory, bounded jobs, artifacts, and inbox events."
---

# Backend Setup

Hades Agent works locally without the hosted backend. The backend adds shared
project memory, workspace binding, bounded project jobs, artifact upload,
doctor reports, and Persephone inbox events.

## Bootstrap From A Project

The fastest path is the project dashboard one-liner. It installs Hades if
needed, registers this local agent, links the current workspace, and runs the
first sync.

Manual equivalent:

```bash
hades backend bootstrap \
  --url https://home-sweet-home.cloud \
  --project-id <project-id> \
  --project-token <bootstrap-token> \
  --workspace "$PWD" \
  --project-name "My Project" \
  --non-interactive
```

The project token is single-use bootstrap material. Hades stores the derived
agent token as a profile secret in `.env`; behavior settings stay in
`config.yaml`.

## Verify Status

Run these from the linked workspace:

```bash
hades doctor
hades backend status --json
hades backend sync
```

The JSON status should show a linked workspace, recent sync state, and no
pending actions. If it reports waiting jobs, refused memory proposals, stale
shared-memory cache, or inbox errors, follow the action text before continuing.

## Privacy Boundaries

- The backend does not choose your model, provider, subagent routing, budgets,
  or local tool settings.
- Shared-memory writes are proposals. The backend accepts, refuses, or marks
  conflicts; the local agent does not directly delete shared backend memory.
- Broad or policy-gated jobs wait for local confirmation.
- Artifact jobs skip secrets, ignored paths, generated dependency directories,
  symlinks, binary/archive files, and oversized files.
- Logs and doctor reports must not include backend tokens, bootstrap tokens,
  raw job payloads, raw source files, or local absolute paths.

## Troubleshooting

Collect safe diagnostics:

```bash
hades doctor
hades backend status --json
hades logs --level WARNING --session latest
```

Do not send `.env`, API keys, backend tokens, raw artifacts, local SQLite
databases, raw source files, or screenshots that show tokenized commands.

For local source checkouts, the detailed launch and support docs are in
`docs/hades/launch.md` and `docs/hades/support-runbook.md`.
