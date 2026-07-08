# Dependencies

## Python

Fonti:
- `pyproject.toml`
- `uv.lock`
- `setup.py`

Fatti verificati:
- Python range: `>=3.11,<3.14`.
- Build backend: setuptools.
- CLI scripts: `hades`, `hades-agent`, `hades-acp`.
- Dev extra include `pytest`, `pytest-asyncio`, `mcp`, `starlette`, `ty`,
  `ruff`, `setuptools`.
- Molte dipendenze core sono pin esatti; i commenti in `pyproject.toml`
  spiegano motivi supply-chain.

Setup CI:

```bash
uv sync --locked --python 3.11 --extra all --extra dev
```

## Node / TypeScript

Fonti:
- `package.json`
- `package-lock.json`
- `apps/desktop/package.json`
- `apps/shared/package.json`
- `apps/bootstrap-installer/package.json`
- `ui-tui/package.json`
- `web/package.json`
- `website/package.json`

Workspaces root:
- `apps/*`
- `ui-tui`
- `ui-tui/packages/*`
- `web`

Script root osservati:
- `install:root`
- `install:web`
- `install:tui`
- `install:desktop`
- `audit:*`

Workspace principali:
- `apps/desktop`: Electron + Vite + React + assistant-ui + nanostores.
- `web`: Vite + React dashboard + xterm + shared package.
- `ui-tui`: Ink + React + nanostores.
- `website`: Docusaurus.
- `apps/bootstrap-installer`: Tauri/Vite installer UI.
- `apps/shared`: shared TypeScript transport.

## Docker / OS Packages

Fonti:
- `Dockerfile`
- `docker-compose.yml`
- `.dockerignore`
- `.hadolint.yaml`

Fatti verificati:
- Dockerfile usa uv image, Node 22 source image, Debian 13.4.
- Installa `ripgrep`, `ffmpeg`, `git`, `openssh-client`, `docker-cli`,
  toolchain build e s6-overlay.
- Compose monta `~/.hermes` in `/opt/data`.

## CI/CD

Workflow osservati:
- `.github/workflows/ci.yml`
- `.github/workflows/tests.yml`
- `.github/workflows/lint.yml`
- `.github/workflows/typecheck.yml`
- `.github/workflows/docker*.yml`
- `.github/workflows/docs-site-checks.yml`
- `.github/workflows/osv-scanner.yml`
- `.github/workflows/supply-chain-audit.yml`
- `.github/workflows/uv-lockfile-check.yml`
- `.github/workflows/upload_to_pypi.yml`

Da verificare per ogni release/deploy: leggere il workflow specifico prima di
eseguire comandi analoghi localmente.

## Comandi Di Verifica

```bash
rg -n '^\\[project\\]|^\\[project.optional-dependencies\\]|^\\[project.scripts\\]|^\\[tool\\.' pyproject.toml
find .github/workflows -maxdepth 1 -type f | sort
```
