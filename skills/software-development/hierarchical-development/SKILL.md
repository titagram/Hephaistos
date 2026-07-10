---
name: hierarchical-development
description: Use when coordinating delegated or durable Hades OrgRuns.
---

# Hierarchical development

Classify the request first.

- Use ephemeral delegation only for a short, self-contained task that can be abandoned safely with the parent session.
- Use a durable Hades OrgRun for multi-task, restart-safe, cross-workspace, backend-mapped, or integration-sensitive work.

## Delegation routing

Apply these branches exactly:

| Observed state | Required action |
|---|---|
| Routing is missing or incomplete | Run `hades delegation setup`. If no models are configured, let it run model onboarding, then resume setup. |
| All three role routes already resolve | Preserve the configuration. Do not prompt and do not rewrite it. |
| The user explicitly asks to change role models or limits | Run `hades delegation configure`, review the complete preview, and require confirmation. |

Use only models already authenticated by Hades. Never infer credentials or change valid routing merely because another model looks preferable.

## Task contract and review ownership

Before creating an `orchestrator`, provide a structured task contract with: `objective`, `deliverable`, `in_scope`, `out_of_scope`, `workspace`, `write_scope`, `input_evidence`, `dependencies`, `acceptance_criteria`, `required_verification`, and `return_schema`. Make each assignment bounded and explicit; do not replace these fields with a prose goal.

The parent performs normal review of its direct child's scope, evidence packet, verification, and residual risk. Escalate to a dedicated non-delegating independent reviewer only when independent review is explicitly requested or the parent identifies a high-risk or disputed result. A reviewer reports findings first and returns a bounded pass/fail conclusion; it does not command leaves.

For durable work, require a validated execution portfolio with repository-relative write scopes, dependencies, assignees, risk, and acceptance evidence. Materialize it with `hades org validate` then `hades org materialize`; do not upload raw plans, source, transcripts, reasoning, or secrets to the backend.

Execution protocol:

1. The planner decomposes bounded tasks and declares scope.
2. The marshal checks dependency order, overlap conflicts, and open blocking decisions.
3. Leaves implement only inside their declared scope and return evidence: changed files, test commands/results, commit or patch reference, and residual risks.
4. The task's parent verifies the evidence and scope; use a dedicated reviewer only under the escalation rule above.
5. The integration worker applies accepted work and runs the integration suite. Never auto-push or auto-merge.
6. Publish a backend result only after local completion evidence, integration, and org review have all passed.

Routing uses configured logical roles (`orchestrator`, `leaf`, `reviewer`) only. Do not accept or invent provider/model choices from task arguments. Keep one model and byte-stable system prompt per conversation.

Escalate instead of guessing when the contract drifts, a remote lease is missing/lost, scope overlaps cannot be serialized, tests fail, or an interface decision remains unresolved. Keep results bounded and redact secrets.
