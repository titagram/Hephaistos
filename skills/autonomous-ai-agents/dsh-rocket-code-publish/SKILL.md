---
name: dsh-rocket-code-publish
description: "Use when modifying Rocket projects through the DSH web workspace and publishing the verified change so it is visible to GitHub and the relevant runtime."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [dsh, rocket, git, github, deployment, workspace]
    related_skills: [github-pr-workflow, codex]
---

# DSH Rocket Code and Publish Workflow

## Overview

Use this workflow for code work performed in the isolated Rocket DSH service.
DSH edits the host-backed `/workspace` mount directly: no copy, sync job, or
container rebuild is needed for the file changes to appear in the DSH workspace
or in the host Git checkout. Publishing has distinct meanings and must be
verified separately:

| Target | What makes a change visible |
|---|---|
| DSH and host checkout | Saving the file: immediate through the bind mount |
| GitHub / Lovable synchronization | Commit and push the intended branch |
| Rocket Club live site | Explicit production build/recreate after a verified commit |

Never represent a saved file or a successful `git push` as a completed live
site deployment.

## When to Use

- The user asks DSH/Codex to change code in Rocket Club or Golden Cue Manager.
- The user asks to commit/push work done through the mobile DSH interface.
- The task needs a clear distinction between edit visibility, GitHub visibility,
  and a deployed application.

Do not use this workflow to alter the DSH service configuration, credentials,
Traefik configuration, or the project `.env` files.

## Workspace Selection

The DSH directory picker starts in its persistent state directory, not the
project mount. Select the curated shortcut:

```text
storages/rocket-ecosystem/
```

Then select one leaf project:

| Directory | Project | Purpose |
|---|---|---|
| `gilded-billiard-pro` | Golden Cue Manager | Customer/mobile PWA |
| `rocket-club` | Rocket Club | Existing live Rocket Club application |

Do not select `profiles`, `sessions`, or the `storages` directory itself as the
working directory. The root `/workspace` may be selected only for a task that
genuinely spans both repositories.

## Tight Loop: Inspect → Edit → Test → Publish

### 1. Establish the project and preserve existing work

Start from the selected project directory and identify its state before writing:

```bash
pwd
git status --short --branch
git remote -v
git log -1 --oneline
```

Read the project-local `AGENTS.md` and the task-relevant README/docs before
editing. If unrelated modified or untracked files already exist, stop and
report them; do not discard, stash, reset, or include them in a new commit.

Completion criterion: the agent can name the repository, current branch, base
commit, and every file it intends to modify.

### 2. Make a minimal, scoped change

Edit only files required by the request. Keep secrets out of prompts, diffs,
commits, terminal output, and Git history. In particular, never add `.env`,
credential exports, private access URLs, or generated local state.

After editing, inspect the patch rather than assuming the save succeeded:

```bash
git diff --check
git diff -- <changed-file>...
git status --short
```

Because `/workspace` is a read/write bind mount, these saved changes are already
visible immediately to DSH and on the host checkout. This does **not** publish
them to GitHub or update a running Docker image.

Completion criterion: the diff contains only the requested implementation and
has no whitespace errors.

### 3. Run the smallest relevant real verification

Discover project scripts/configuration first; do not invent a test command.
Run the narrowest useful test, formatter, build, or smoke check. For a change
that affects an executable path, run that path or a focused regression test.
Record the actual command and outcome.

If verification cannot run, report the exact blocker. Do not commit a claim
that tests passed when they did not run.

Completion criterion: a real verification result is available, or the blocking
failure is explicit and the user has directed the next action.

### 4. Publish through Git safely

Check the branch and remote state before staging:

```bash
git status --short --branch
git fetch origin
git status --short --branch
```

Stage only the intended paths, commit, then push the checked-out branch:

```bash
git add -- <changed-file>...
git diff --cached --check
git diff --cached --stat
git commit -m "<type>: <concise verified change>"
git push origin HEAD
git status --short --branch
```

Use conventional commit types such as `feat`, `fix`, `docs`, `test`, `refactor`,
or `chore`. Do not use `git add -A` when unrelated work may exist.

### 5. Project-specific Git constraints

#### Golden Cue Manager (`gilded-billiard-pro`)

This repository is synchronized with Lovable. Preserve published history:

- Do **not** force-push, rebase, amend, or squash pushed commits.
- Push ordinary new commits to the selected branch (normally `main`).
- A successful push makes the commit visible to GitHub immediately; confirm any
  downstream Lovable update in its own UI rather than assuming it completed.

#### Rocket Club (`rocket-club`)

A pushed commit is source control publication, not a running-site update. Its
production Compose file builds the `app` and `nginx` images from the repository
context. For a user-authorized live deployment, first re-check the exact target
and health/backup requirements documented in that repository's `AGENTS.md`,
then run its approved deployment procedure. Do not restart/recreate production
containers merely because a source file was saved or pushed.

## Pre-authorized DSH Capabilities

For this managed Rocket DSH service, the agent is authorized to complete the ordinary **edit → verify → commit → normal push** loop without requesting an extra confirmation for each mechanical step:

- It may run repository-defined checks such as `npm run build`, lint, and focused tests.
- If frontend dependencies are missing or incompatible, it may use the lockfile-preserving command `npm ci` (not an unbounded dependency upgrade) and rerun the failed verification.
- It may use the persistent DSH Git transport to run `git push origin HEAD` for its own verified commits on the intended branch.
- It must still inspect branch/remote state first and must never force-push, rebase, amend published commits, reset/clean unrelated work, or embed credentials in repository files.
- It may deploy the current verified non-`main` feature/fix branch only to the explicitly authorized Rocket Club staging target (`gtsystems.tech`) through the restricted SSH gateway below. It must never deploy/recreate any other service or target, alter `.env` files, alter the DSH credential store, or change Traefik.

## Restricted Rocket Club Staging Gateway

The DSH environment has a dedicated private SSH key with a forced-command gateway. It grants **no interactive shell**, no forwarding, and no access outside the Rocket Club staging operations. Invoke it explicitly because the service user's OpenSSH home differs from `$HOME`:

```bash
SSH='ssh -F /var/lib/dsh/.ssh/config -o BatchMode=yes rocket-staging'
$SSH 'rocket status'
$SSH 'rocket build'
$SSH 'rocket deploy-staging'
$SSH 'rocket logs nginx'
```

`rocket deploy-staging` is intentionally constrained to the current clean, pushed `feat/*`, `fix/*`, or `chore/*` branch; it rejects `main`/`master`, validates the stable Compose project `rocket`, builds the app and nginx images, runs the project migration/cache steps, recreates the Rocket services only, and reports the public HTTPS status. It does not edit branch history, `.env`, Traefik, or unrelated host resources.

For safe app diagnostics, an allowlisted Artisan call can use a base64-encoded JSON array, for example:

```bash
ARGS=$(printf '%s' '["migrate:status"]' | base64 -w0)
$SSH "rocket artisan $ARGS"
```

## Visibility Matrix

| Event | DSH sees it | Host checkout sees it | GitHub sees it | Live Rocket Club sees it |
|---|---:|---:|---:|---:|
| Save file under `/workspace` | Yes, immediate | Yes, immediate | No | No |
| Commit locally | Yes | Yes | No | No |
| `git push origin HEAD` succeeds | Yes | Yes | Yes | No |
| Authorized production deploy succeeds | Yes | Yes | Yes | Yes |

## Common Pitfalls

1. **Choosing `profiles` or `sessions` in the picker.** They are DSH state,
   not project source. Use `storages/rocket-ecosystem/<project>`.
2. **Treating push as deploy.** Git publication and the Rocket Club Docker
   runtime are distinct; rebuild/recreate only through an authorized deploy.
3. **Using destructive Git commands to get clean.** Never reset, clean, force
   push, rebase, amend, or squash to hide pre-existing work.
4. **Committing secrets.** Inspect the staged diff and use explicit path staging.
5. **Claiming success without verification.** A diff is not a test, and a local
   commit is not a remote push. Check each boundary.
6. **Recreating DSH casually.** DSH source edits need no service restart. If the
   DSH container itself is recreated, its proxy sidecar must be recreated too
   because it shares DSH's network namespace.

## Completion Report

Report all five facts concisely:

1. Project directory and branch.
2. Files changed and the user-visible behavior changed.
3. Verification command(s) and actual result(s).
4. Commit SHA and whether `git push` succeeded.
5. Deployment state: `not requested`, `not applicable`, or the exact verified
   deployed target.

## Verification Checklist

- [ ] Correct project selected under `storages/rocket-ecosystem/`
- [ ] Project instructions and pre-existing Git state inspected
- [ ] Diff limited to the requested change and passes `git diff --check`
- [ ] Relevant real test/build/smoke check run, or a blocker disclosed
- [ ] Only intended files staged; staged diff reviewed
- [ ] Commit created without rewriting history
- [ ] `git push origin HEAD` succeeded and final branch status is clean/synced
- [ ] Live deployment status reported separately from Git publication
