# Hades Launch Guide

This is the public path for a new Hades install with the backend MVP enabled.
It is intentionally user-facing: install, bootstrap, verify, troubleshoot, and
understand the privacy boundaries without reading coordination logs.

## 1. Install Hades

Use the desktop installer on macOS or Windows, or install the CLI directly:

```bash
curl -fsSL https://hades-agent.local/install.sh | bash
```

Native Windows users can run:

```powershell
iex (irm https://hades-agent.local/install.ps1)
```

The public installer defaults to the `main` branch. For a controlled beta or a
release-candidate smoke, pin the channel explicitly:

```bash
curl -fsSL https://hades-agent.local/install.sh | bash -s -- --branch <branch-or-tag>
```

```powershell
iex (irm https://hades-agent.local/install.ps1)
.\install.ps1 -Branch <branch-or-tag>
```

The detailed platform notes live in [installation.md](installation.md).

## 2. Configure A Model

After install, run the setup wizard or configure a provider directly:

```bash
hades setup
hades model
```

The backend does not choose your provider or model. Model routing, subagent
profiles, budgets, and local tool choices remain local configuration.

## 3. Bootstrap The Backend

The preferred backend setup path is the dashboard-generated one-liner. It
passes the backend URL, project id, single-use project bootstrap token, local
workspace, and display project name to the installer.

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

The bootstrap token is used once to register this local agent. Hades stores the
derived agent token as a profile secret in `.env`; behavior settings stay in
`config.yaml`.

If bootstrap fails, local Hades still works. Re-run `hades backend bootstrap`
after fixing the underlying issue, or use the manual setup details in
[backend.md](backend.md).

## 4. Verify The Install

Run these commands from the linked workspace:

```bash
hades doctor
hades backend status --json
hades backend sync
```

A healthy setup reports a reachable backend, a linked workspace binding, recent
sync state, and no pending actions. Pending jobs, refused memory proposals,
stale shared-memory cache, and inbox errors are surfaced as explicit actions in
the JSON status payload.

For release and staging checks, use the no-network MVP smoke in
[operations.md](operations.md#mvp-smoke). Production release gates live in
[../RELEASE_GATES.md](../RELEASE_GATES.md).
For source-free bug diagnosis, follow
[no-codebase-diagnosis.md](no-codebase-diagnosis.md) after the backend status
shows current project awareness and evidence coverage.

## 5. Privacy And Data Boundaries

Hades uses the backend for project coordination, shared memory, bounded job
metadata, artifacts, doctor reports, and Persephone inbox events. The local
agent keeps these boundaries:

- Model provider choices and resolved model names stay local.
- Shared memory writes are proposals; the backend owns acceptance and conflict
  decisions.
- Job execution stays bounded to the linked workspace and waits for local
  confirmation when a job is broad or policy-gated.
- Artifact jobs skip secrets, ignored paths, generated dependency directories,
  symlinks, binary/archive files, and oversized files.
- Logs and doctor reports must not include backend tokens, bootstrap tokens,
  raw job payloads, raw source files, or local absolute paths.

The operational details live in [operations.md](operations.md). The support
collection rules live in [support-runbook.md](support-runbook.md).

## 6. Troubleshoot Safely

Start with:

```bash
hades doctor
hades backend status --json
hades logs --level WARNING --session latest
```

Use [doctor-troubleshooting.md](doctor-troubleshooting.md) for degraded local
states and cleanup. Use [support-runbook.md](support-runbook.md) for launch
failure recovery, including expired tokens, failed bootstrap, workspace binding
conflicts, waiting jobs, refused proposals, Docker permissions, Windows PATH
issues, and desktop/backend version mismatch.

Do not send `.env`, API keys, backend tokens, raw artifacts, local SQLite
databases, raw source files, or screenshots that show tokenized commands.

## 7. Developer Source Of Truth

Developers should treat these as the source of truth for the backend MVP:

- `docs/hades/openapi-hades-v1.json` for the local client contract fixture.
- `hermes_cli/hades_backend_cmd.py` for CLI command behavior.
- `hermes_cli/hades_backend_sync.py` for sync and backend status state.
- `hermes_cli/hades_backend_jobs.py` for bounded local job execution.
- `tui_gateway/server.py` for backend status RPC exposure.
- `tests/test_docs_hades_mvp.py` and `tests/hermes_cli/test_hades_backend_mvp_smoke.py`
  for the minimum documentation and no-network smoke gates.
- `docs/hades/plugin-skill-distribution.md` for official, optional,
  community/upstream, and excluded plugin/skill boundaries.

Historical coordination files, including `docs/backend-agent-coordination.md`
and `docs/mvp_checkpoint_2026-07-01.md`, are maintainer evidence. They are not
the primary user path for install, bootstrap, privacy, or support.
