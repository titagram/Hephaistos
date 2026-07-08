---
name: hades-coordination
description: "Coordinate Hades shared-backend work with local-only subagent profiles and model routing."
version: 1.0.0
author: Hades Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [hades, coordination, subagents, model-routing, shared-backend]
    related_skills: [hermes-agent, codex, plan, requesting-code-review]
---

# Hades Coordination

Use this skill when Hades is linked to a shared backend and work needs local
subagents, model routing, shared-memory proposals, backend jobs, or artifact
sync. The backend stores project/workspace truth, jobs, proposals, artifacts,
doctor reports, and Persephone inbox events. It does not choose or receive local
provider/model routing decisions.

## First Checks

1. Run `hades backend status --json`.
2. Run `hades backend profiles --json` to inspect the curated local profiles.
3. If the backend is configured, run `hades backend sync` before starting shared
   work and after producing artifacts or memory proposals.
4. Use `hades doctor` for local diagnostics. Use `hades doctor --report-backend`
   only when explicit reporting to Laravel is wanted.

## Curated Profiles

- `planner` decomposes shared work and chooses the smallest useful delegation.
- `implementer` executes one bounded code task and returns focused evidence.
- `reviewer` checks diffs, contracts, tests, and MVP readiness.
- `sync-curator` prepares read-only `hades.git_tree.v1` and
  `hades.symbols.v1` artifacts.
- `memory-steward` drafts or reviews project-scoped shared-memory proposals.

The profile names map to local `config.yaml` model profiles such as
`hades.planner`, `hades.implementer`, and `hades.reviewer`. Do not write the resolved model, provider, API key name, or token into shared memory, backend artifacts, Persephone messages, or doctor reports.

## Operating Rules

- Keep model/provider choice local-only. The backend should see capabilities,
  job status, artifacts, and proposal summaries, not routing internals.
- Use Persephone for durable inbox/notification events. Do not use it as the
  primary job transport.
- Use backend jobs only through `hades backend sync`; respect
  `waiting_confirmation`, refusals, conflicts, deadlines, and cancelled status.
- For shared memory, create proposals with provenance and let backend policy
  accept, refuse, or mark conflicts. Do not publish personal memory by default.
- For artifacts, prefer bounded read-only snapshots and redact secrets before
  upload.

## Natural-Language Bug Intake

When the user describes a bug informally in a Hades-linked project, do not make
them manually format the full `hades backend bug-intake` command. Extract a
structured intake locally, then run the CLI command yourself.

Map the user's text into:

- `title`: a short noun phrase naming the failing behavior.
- `symptom`: the visible failure, exception, bad state, or user-facing problem.
- `steps`: reproduction steps when stated; otherwise a concise "not specified"
  phrase rather than inventing steps.
- `expected`: expected behavior when stated; otherwise "not specified".
- `actual`: actual behavior when stated; otherwise repeat the concrete symptom.
- `severity`: only when the user states or strongly implies impact.
- `environment`: current project/workspace plus any stated browser, route,
  deploy, local/staging/production, or commit context.
- evidence files: attach logs, failing test output, HTTP request/response files,
  or deploy commit only when the user provides paths or values.

Before running intake, check `hades backend status --json` from the current
workspace. If the workspace is not linked, say so and stop. If the description
is missing only optional fields, proceed with explicit "not specified" values.
Ask a clarification only when both `title` and `symptom` cannot be recovered.

Use this command shape:

```bash
hades backend bug-intake \
  --title "..." \
  --symptom "..." \
  --steps "..." \
  --expected "..." \
  --actual "..." \
  --environment "..."
```

After intake, run `hades backend status --json` or `hades backend sync` as
needed, then continue with `hades-bug-diagnosis` if the user wants diagnosis.

## Handoff Checklist

- `hades backend status --json` has no unexpected degraded action.
- Relevant focused tests or checks have run.
- Any generated `hades.git_tree.v1` or `hades.symbols.v1` artifact was uploaded
  by sync or explicitly accounted for.
- Memory proposals are accepted, pending with clear reason, or intentionally
  refused/conflicted.
