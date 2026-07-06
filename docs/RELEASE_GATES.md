# Release Gates

This file maps release targets to the checks that must be green before a
production release. Workflow files remain the executable source of truth; this
document is the operator checklist that ties those workflows to release
decisions.

## Baseline

Every production candidate starts from a clean branch or tag candidate:

```bash
git status --short --branch
python3 scripts/docs_audit.py
python scripts/release.py --bump patch
```

Public installer scripts must resolve to `main` or an explicit release tag by
default. Feature branches are allowed only for beta/release-candidate smoke when
the command pins `--branch` or `HADES_INSTALL_BRANCH`.

For a pull request, branch protection should require only the
`CI / All required checks pass` job from `.github/workflows/ci.yml`. That
aggregate currently blocks on Python tests, Python lint, TypeScript, docs-site
checks, history/contributor checks, `uv.lock`, Docker metadata lint,
supply-chain audit, and OSV scanner results. Skipped change-detected lanes are
accepted by the aggregate gate.

Docker is intentionally not part of `all-checks-pass` today because it is slow.
For a production release, treat Docker as required for any release that ships or
documents container images. If Docker is deferred, record the manual signoff and
the issue that will restore it as a hard gate.

## Target Matrix

| Target | Required automated gate | Required manual or staging check | Artifact identity |
| --- | --- | --- | --- |
| Backend MVP | `CI / All required checks pass`; focused Hades backend pytest; `python3 scripts/docs_audit.py` | No-network MVP smoke plus live staging bootstrap against a disposable `HERMES_HOME` | `hades backend status --json` must show the expected project, sync state, and versioned local agent registration |
| Hades no-codebase diagnosis | `tests/agent/test_hades_bug_diagnosis_no_codebase.py`; focused Hades provider/client pytest | Review fixture coverage before claiming source-free diagnosis quality; confirm no raw source/file/shell tool violations | Report must show 5 complete bug fixtures, 2 insufficient-evidence fixtures, 100% evidence/tool/persistence coverage, and zero no-codebase violations |
| PyPI | `.github/workflows/upload_to_pypi.yml` build, publish, sign jobs after the release tag | `python scripts/release.py --bump <part>` dry run before publish; inspect wheel/sdist names before attaching assets | `pyproject.toml`, `hermes_cli/__init__.py`, `apps/desktop/package.json`, and ACP registry manifest stay version-locked |
| Docker | `.github/workflows/docker.yml` amd64 and arm64 build/test jobs plus merge manifest on main/release | Inspect published image digest and run a container smoke before announcing Docker support | CI passes `HERMES_GIT_SHA`; `Dockerfile` writes `/opt/hermes/.hermes_build_sha` and OCI revision labels |
| Website docs | `.github/workflows/docs-site-checks.yml`; deploy-site workflow after release publish or approved manual dispatch | Check generated skill docs/catalog pages when skills changed | Published site commit or release tag matches the release notes |
| Desktop packages | `.github/workflows/typecheck.yml` matrix plus `desktop-build` job | Run package smoke for every desktop platform being distributed; at minimum run `npm run --prefix apps/desktop test:desktop:platforms` on the release branch | `apps/desktop/package.json` version matches the Python package version |
| Update flow | CI baseline plus release script dry run | Install/update from the candidate channel on a clean profile, then run `hades version`, `hades doctor`, and a restart/update smoke | User-visible version and release date match the tag and release notes |

## Backend MVP Smoke

The no-network smoke is the minimum local proof that backend MVP plumbing still
composes:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/hermes_cli/test_hades_backend_mvp_smoke.py
```

The no-codebase diagnosis gate is the minimum local proof that source-free bug
diagnosis contracts still compose:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/agent/test_hades_bug_diagnosis_no_codebase.py
```

The live staging smoke must use a disposable home and dashboard-generated
bootstrap credentials:

```bash
export HERMES_HOME="$(mktemp -d)"
hades backend status --json
hades backend sync
hades doctor --report-backend
```

Do not preserve bootstrap tokens, derived agent tokens, raw job payloads, or
absolute local paths in release logs.

## Release Steps

1. Start from a clean branch with CI green.
2. Run `python scripts/release.py --bump <major|minor|patch>` and review the
   generated notes, target CalVer tag, and SemVer bump.
3. Run the target-specific smoke checks above.
4. Publish with `python scripts/release.py --bump <part> --publish` only after
   the required gates are green.
5. Watch `.github/workflows/upload_to_pypi.yml`, `.github/workflows/docker.yml`,
   and `.github/workflows/deploy-site.yml` until the release artifacts and docs
   are available.
6. Verify the released install/update path from a clean profile before public
   announcement.

## Rollback

Prefer a forward patch release over rewriting a published tag. If a release is
bad after publication:

- PyPI: yank the bad version when appropriate, then publish a patch release.
- Docker: retag or document the previous known-good digest, then publish a
  patch image tag.
- Website: redeploy the previous known-good docs build or publish a corrective
  docs commit.
- Desktop: disable the affected update channel if available, then ship a patch
  build with the same gate matrix.
- Backend MVP: revoke affected bootstrap/project tokens, pause new bootstrap
  issuance if needed, and keep local fallback memory/sync behavior available.

Record the incident, rollback choice, and follow-up owner in the release notes
or coordination log before reopening the release channel.
