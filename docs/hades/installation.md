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
available. `set-token` prompts for the credential; do not put credentials on
the command line or in installer arguments.

Updates remain explicit too:

```bash
hades update
hades plugins update hades-backend
```

The first command updates Hades core only. The second updates the installed
plugin when the user requests it. Disabling or removing the plugin changes
only active plugin discovery; it does not erase existing project settings,
sessions, credentials, or local project state.
