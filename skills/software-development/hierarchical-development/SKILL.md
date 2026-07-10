---
name: hierarchical-development
description: Use when coordinating delegated or durable Hades OrgRuns.
---

# Hierarchical development

Classify the request first.

- Use ephemeral delegation only for a short, self-contained task that can be abandoned safely with the parent session.
- Use a durable Hades OrgRun for multi-task, restart-safe, cross-workspace, backend-mapped, or integration-sensitive work.

For durable work, require a validated execution portfolio with repository-relative write scopes, dependencies, assignees, risk, and acceptance evidence. Materialize it with `hades org validate` then `hades org materialize`; do not upload raw plans, source, transcripts, reasoning, or secrets to the backend.

Execution protocol:

1. The planner decomposes bounded tasks and declares scope.
2. The marshal checks dependency order, overlap conflicts, and open blocking decisions.
3. Leaves implement only inside their declared scope and return evidence: changed files, test commands/results, commit or patch reference, and residual risks.
4. An independent reviewer verifies the evidence and scope.
5. The integration worker applies accepted work and runs the integration suite. Never auto-push or auto-merge.
6. Publish a backend result only after local completion evidence, integration, and org review have all passed.

Routing uses configured logical roles (`orchestrator`, `leaf`, `reviewer`) only. Do not accept or invent provider/model choices from task arguments. Keep one model and byte-stable system prompt per conversation.

Escalate instead of guessing when the contract drifts, a remote lease is missing/lost, scope overlaps cannot be serialized, tests fail, or an interface decision remains unresolved. Keep results bounded and redact secrets.
