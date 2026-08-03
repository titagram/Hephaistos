---
sidebar_position: 2.8
title: "Backend Plugin Setup"
description: "Explicitly install and pair the optional Hades Backend project-knowledge plugin."
---

# Backend Plugin Setup

Hades works locally without the Backend plugin. Project knowledge, workspace
binding, bounded project jobs, artifacts, and inbox events are available only
after a user explicitly installs and pairs the standalone plugin.

## Install and pair from a project

Once the standalone release is published, run the following from the project
that should use Backend project knowledge:

```bash
hades plugins install titagram/hades-backend-plugin --enable
cd /path/to/the/project
hades backend set-token --url https://backend.example.test --project-id project-test
```

The standalone plugin is not yet published from this checkout. The install
line records the frozen release identity; it does not claim the package is
currently available. The pairing command requests the credential interactively,
so credentials never appear in shell history, installer arguments, or logs.

## Update, disable, or remove

```bash
hades update
hades plugins update hades-backend
hades plugins disable hades-backend
hades plugins remove hades-backend
```

Core updates do not install, update, enable, pair, or synchronize the plugin.
Plugin changes are explicit. Disabling or removing it only changes active
plugin discovery: existing project state, sessions, local credentials, and
configuration remain in place.

## Verify status

Run these from a paired workspace:

```bash
hades doctor
hades backend status --json
```

The JSON status should show a linked workspace and recent plugin-controlled
state. Follow its safe action text before continuing.

## Privacy boundaries

- The plugin does not choose your model, provider, subagent routing, budgets,
  or local tool settings.
- Shared-memory writes are proposals. The Backend accepts, refuses, or marks
  conflicts; the local agent does not directly delete shared Backend memory.
- Broad or policy-gated jobs wait for local confirmation.
- Artifact jobs skip secrets, ignored paths, generated dependency directories,
  symlinks, binary/archive files, and oversized files.
- Logs and doctor reports must not include Backend credentials, raw job
  payloads, raw source files, or local absolute paths.

## Troubleshooting

Collect safe diagnostics:

```bash
hades doctor
hades backend status --json
hades logs --level WARNING --session latest
```

Do not send `.env`, API keys, Backend credentials, raw artifacts, local SQLite
databases, raw source files, or screenshots that show credentials.
