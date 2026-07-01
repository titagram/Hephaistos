# Hades Installation

## Tokenized One-Liner

The preferred MVP install path is a dashboard-generated one-liner. The command
contains the backend URL, backend project id, and project-scoped bootstrap
token. The bootstrap token is used once locally to register an agent and is not
persisted after registration; Hades stores the derived agent token instead.

POSIX:

```bash
curl -fsSL https://home-sweet-home.cloud/install.sh | bash -s -- \
  --backend-url https://home-sweet-home.cloud \
  --backend-project-id <project-id> \
  --backend-project-token <bootstrap-token> \
  --backend-workspace "$PWD" \
  --backend-project-name "My Project"
```

Windows:

```powershell
irm https://home-sweet-home.cloud/install.ps1 | iex
.\install.ps1 -BackendUrl https://home-sweet-home.cloud `
  -BackendProjectId <project-id> `
  -BackendProjectToken <bootstrap-token> `
  -BackendWorkspace $PWD `
  -BackendProjectName "My Project"
```

## Backend Bootstrap

Installers call `hades backend bootstrap` after runtime setup when backend
flags are present. The command performs:

1. `hades backend setup`
2. local project creation or reuse
3. workspace link
4. initial `hades backend sync`

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

## Fallback Manual Setup

If the one-liner is unavailable, install Hades normally, then run the manual
bootstrap command above. If backend setup fails, local Hades still works without
shared backend memory.
