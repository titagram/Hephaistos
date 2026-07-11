# Hades OrgRun operations

## Scope and authority

An OrgRun is a local Kanban DAG for executing a validated Hades portfolio. The backend owns remote mandate and lease lifecycle; the local board owns execution, worktree state, evidence, integration, and review.

| Mode | Remote reads | Remote writes | Local behavior |
|---|---:|---:|---|
| `off` | No | No | Local-only Kanban work continues. |
| `pull_only` | Yes | No | Remote work is imported as `triage`; no claim, heartbeat, or result publish occurs. |
| `mirror` | Yes | Bounded lifecycle only | A mapped task claims its remote lease before mutable work; verified results publish only after the integration gate. |

`mirror` is opt-in. It must not be enabled until backend capability and profile routing are verified. Local-only cards are never uploaded merely because sync is enabled.

### Remote mandate projection

The project-manager Kanban is authoritative and may contain imperfect natural-language mandates. Hades stores only the stable remote task ID and observed version on the local OrgRun anchor; local execution/review cards are a derived organizational DAG, not replicated backend rows. Every observation is scoped by the backend project UUID. Work from another project must never be imported or correlated, even when another Hades instance is active there.

When an accepted remote version changes, Hades marks that projection stale, invalidates its local evidence, and pauses only the dependent local subtree plus its integration products. It does not silently accept the new wording. A human must review the change, rebuild affected contracts when necessary, and explicitly accept the new version before work resumes.

Clarification questions, local decisions, progress summaries, and verified completion proposals are bounded, append-only Persephone messages with stable idempotency IDs. They are information records only: Hades never rewrites a project-manager card. Automatic read-only information exchange is allowed; any proposal that would cause a remote change remains subject to human approval. Cursor/offline state is explicit, retries retain the stable message ID, and switching sync to `off` performs no network work.

Proposal publication always derives the backend project UUID from the durable OrgRun topology. A caller-supplied expected project may only narrow this authority; a mismatch is rejected before persistence. Proposals enter the durable Persephone outbox before any network attempt, then use the normal capability-gated retry/dead-letter sender. This preserves offline and restart recovery without bypassing queue policy.

Evidence packets used by projected execution are registered with project, OrgRun anchor, remote mandate, node and mandate-version provenance. A mandate version change marks matching packets stale in the evidence store; stale or cross-project packet references are rejected by the consumption/publication validator. Accepting new wording does not revive old evidence.

Reconciliation follows `current → awaiting_human → accepted`. Acceptance requires an identified human, approval evidence, and replacement contract hashes/version entries for the complete affected subtree. The transition is single-use and atomic: partial or rejected approval leaves the subtree blocked. Eligible nodes resume according to their dependency gates, while evidence remains invalid until regenerated against the accepted contracts.

## Delegation preflight

Resolve local model routing before materializing or dispatching an OrgRun:

| Routing state | Operator action |
|---|---|
| Missing or incomplete | Run `hades delegation setup`. The wizard starts model onboarding when no authenticated models are available. |
| Valid for `orchestrator`, `leaf`, and `reviewer` | Preserve it; setup must not prompt or overwrite the existing routing. |
| User explicitly requests different models or capacity limits | Run `hades delegation configure`, inspect the full configuration preview, then confirm the single atomic write. |

Do not use `configure` as an automatic upgrade step. Both commands select only models already authenticated through Hades; routing data never contains credentials.

Every orchestrator dispatch requires a structured task contract containing objective, deliverable, in/out scope, workspace, write scope, input evidence, dependencies, acceptance criteria, required verification, and return schema. Reject the dispatch before child creation when the contract is missing or invalid.

Only a leaf's direct parent may command that leaf or modify its task contract. The root/main agent may inspect or query a leaf for information, but must not command it or change its task contract unless it is that leaf's direct parent. Apply this rule recursively at every level: each orchestrator commands and revises contracts only for its direct children, and every child reports through its parent chain.

Review responsibility follows the same delegation tree. The direct parent normally checks each direct child's declared scope, evidence packet, verification records, and residual risks. Use the non-delegating `reviewer` role only when independent review is explicitly requested or the result is high-risk, disputed, or escalated; its output is findings-first with a bounded pass/fail conclusion. Evidence packets contain bounded facts and references, never transcripts or hidden reasoning.

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
6. Require evidence, parent review, and integration tests before publication.

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
