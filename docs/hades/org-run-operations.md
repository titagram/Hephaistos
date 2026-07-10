# Hades OrgRun operations

## Scope and authority

An OrgRun is a local Kanban DAG for executing a validated Hades portfolio. The backend owns remote mandate and lease lifecycle; the local board owns execution, worktree state, evidence, integration, and review.

| Mode | Remote reads | Remote writes | Local behavior |
|---|---:|---:|---|
| `off` | No | No | Local-only Kanban work continues. |
| `pull_only` | Yes | No | Remote work is imported as `triage`; no claim, heartbeat, or result publish occurs. |
| `mirror` | Yes | Bounded lifecycle only | A mapped task claims its remote lease before mutable work; verified results publish only after the integration gate. |

`mirror` is opt-in. It must not be enabled until backend capability and profile routing are verified. Local-only cards are never uploaded merely because sync is enabled.

## Gate sequence

```text
execution evidence → task review → integration-ready
  → integration → org review → local completion evidence
  → bounded remote publish → synthesis
```

Remote publication is refused when integration or org review is not `done`, when completion evidence is incomplete, or when the execution node has no usable lease. The system does not auto-push, auto-merge, or auto-create a pull request.

## Rollout

1. Run `hades org validate portfolio.json`.
2. Run `hades org materialize portfolio.json`; inspect `hades org show <org_run_id>`.
3. Enable `pull_only` and confirm imported cards remain in `triage`.
4. Verify assignees, backend capability, workspace binding, and lease behavior in a non-production board.
5. Enable `mirror` only for mapped work items; monitor deferred admissions and lease failures.
6. Require evidence, independent review, and integration tests before publication.

## Rollback and incident response

- Switch to `off` to stop all new backend traffic. Do not delete the local board or evidence.
- A remote claim refusal is a coordination defer, not a local worker failure; replan or investigate the lease owner.
- If backend connectivity is lost after a claim, preserve local evidence and do not publish a guessed result. Retry only through an idempotent lifecycle path after lease audit.
- If an interface or scope decision is unresolved, stop the impacted subtree and record a typed blocker/decision notice.
- If integration tests fail, leave completion/publish nodes incomplete and attach bounded failure evidence to the local board.

## Operator signals

Inspect each OrgRun snapshot for `phase`, `blocked`, and `dispatchable` IDs. Track these local signals per rollout:

- admission defers and supersedes;
- remote claim/heartbeat/publish failures;
- tasks blocked by scope or interface decisions;
- integration and org-review pass/fail rate;
- completion-to-synthesis latency.

Do not place secrets, raw source, reasoning, transcripts, or absolute paths in backend-facing messages or evidence references.
