---
sidebar_position: 2.8
title: "Backend Plugin Setup"
description: "Explicitly install and pair the optional Hades Backend project-knowledge plugin."
---

# Backend Plugin Setup

Hades works locally without the Backend plugin. The optional standalone plugin
adds **workspace-scoped project knowledge**: project documentation, source and
graph indexes, and explicitly submitted evidence. It is not a memory provider:
`memory.provider` continues to select Holographic, Supermemory, or another
memory provider, never Backend.

Normal conversations, startup, shutdown, session resets, and context
compression do not contact Backend. A linked checkout is required for every
Backend query or sync. There is no default Backend project for a profile.

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
currently available. Copy a newly created project token separately from the
Backend dashboard, then run `set-token` from that project's root. The command
opens a masked prompt: never paste the token into a command, slash command, or
chat message. For non-interactive automation, use `--token-stdin`; it is the
only token input path and still keeps the value out of argv.

One profile can link many project roots, each with its own derived credential.
The local profile stores only opaque credential references for those roots; it
does not create a global/default project or reuse one project's credential for
another project.

## Update, disable, or remove

```bash
hades update
hades plugins update hades-backend
hades plugins disable hades-backend
hades plugins remove hades-backend
```

`hades update` changes Hades core only: it does not install, update, enable,
pair, reconfigure, or synchronize Backend. Plugin lifecycle changes are
explicit. Restart Hades (and any running TUI, Desktop, or gateway process)
after installing, updating, disabling, or removing the plugin so discovery is
reloaded. Disabling or removing it only changes active plugin discovery;
existing project state, sessions, local credentials, and configuration remain
in place.

## Verify status

Run these from a paired workspace:

```bash
hades doctor
hades backend status --json
```

The JSON status should show a linked workspace. `status` is local by default;
add `--live` only when an explicit live check is needed. Run a refresh only
when it is materially needed and only from the linked checkout:

```bash
hades backend sync --dry-run
hades backend sync --domain source_index
```

Sync is an explicit, workspace-scoped operation. It synchronizes project
documentation, source/index artifacts, graph data, and submitted evidence; it
does not synchronize MEMORY.md, USER.md, personal-memory providers, jobs,
inbox, logbook, cron, or personal context.

## Local Desktop transport is separate

`hades serve` is the local JSON-RPC/WebSocket transport used by Hades Desktop.
It is not Hades Backend, does not pair a project, and remains required for the
Desktop transport whether or not this plugin is installed.

## Privacy boundaries

- The plugin does not choose your model, provider, subagent routing, budgets,
  or local tool settings.
- Project facts, temporary IPs, credentials, flags, exploit steps, and
  challenge-specific hints belong in the scoped project workflow only when an
  allowed operation explicitly needs them. Do not copy them into personal
  memory.
- Sync is never automatic or a background lifecycle action.
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
