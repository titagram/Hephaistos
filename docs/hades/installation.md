# Hades Installation

## Install Hades

Install Hades with the normal platform installer. It is deliberately generic:
it does not configure a project, contact a Backend service, create a pairing,
or accept project credentials.

POSIX:

```bash
curl -fsSL https://home-sweet-home.cloud/install.sh | bash
```

Windows:

```powershell
irm https://home-sweet-home.cloud/install.ps1 | iex
```

The installer defaults to the public `main` channel. Beta, release-candidate,
and branch-specific validation must pin the source explicitly:

```bash
curl -fsSL https://home-sweet-home.cloud/install.sh | bash -s -- \
  --branch <branch-or-tag>
```

```powershell
$env:HADES_INSTALL_BRANCH = "<branch-or-tag>"
irm https://home-sweet-home.cloud/install.ps1 | iex
```

## Optional project knowledge plugin

Project knowledge is an explicit, per-project plugin. Once the standalone
release is published, install and activate it, then pair from the repository
that should use it:

```bash
hades plugins install titagram/hades-backend-plugin --enable
cd /path/to/the/project
hades backend set-token --url https://backend.example.test --project-id project-test
```

The standalone plugin is not yet published from this checkout, so the command
above documents the release identity rather than claiming that it is currently
available. Copy a project token separately from the Backend dashboard and run
`set-token` from the project's root; it prompts for the token masked. Do not
put it in a command, chat message, installer argument, or shell history.

Each profile can link multiple project roots with distinct derived credentials;
there is no default/global Backend project. Backend is optional project
knowledge, not a memory provider: `memory.provider` remains Holographic,
Supermemory, or another real memory provider.

Updates remain explicit too:

```bash
hades update
hades plugins update hades-backend
```

The first command updates Hades core only; it never updates or reconfigures
Backend. The second updates the installed plugin when the user requests it.
Restart the affected Hades CLI, TUI, Desktop, or gateway after a plugin
lifecycle change. Disabling or removing the plugin changes only active plugin
discovery; it does not erase existing project settings, sessions, credentials,
or local project state.
