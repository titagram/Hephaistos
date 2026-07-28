---
name: hierarchical-development
description: Use when coordinating delegated or durable Hades OrgRuns.
---

# Hierarchical development

Classify the request first.

- Use ephemeral delegation only for a short, self-contained task that can be abandoned safely with the parent session.
- Use a durable Hades OrgRun for multi-task, restart-safe, cross-workspace, or integration-sensitive work.

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

Only a leaf's direct parent may command that leaf or modify its task contract. The root/main agent may inspect or query a leaf for information, but must not command it or change its task contract unless it is that leaf's direct parent. Apply this rule recursively at every level: each orchestrator commands and revises contracts only for its direct children, and every child reports through its parent chain.

## Addressed coordination

Inside an active delegated task, use the existing `delegate_task` surface; do not invent sender or namespace fields:

- `action="coordination_post"` with `recipient_id`, `event_type`, and `summary` posts a bounded addressed event. Add `artifact`, `blocker`, or `evidence_refs` only when they prove why that recipient is relevant.
- `action="coordination_status"` reads bounded delivery state for self, a direct child, or an otherwise authorized target.
- `action="coordination_inspect"` reads the authorized manifest view. It never changes a contract.

The runtime binds `actor_id`, delegation root, and project from the active child; never request, accept, or synthesize those identities in arguments. Leaves and reviewers cannot use this surface to spawn children. Only an orchestrator may use `action="delegate"` below the root.

Questions and answers between siblings require an explicit dependency, shared interface/scope, named artifact, or blocker. If relevance is absent, address the direct parent instead. Blackboard delivery occurs only at a safe trailing tool boundary; it is information-only and does not authorize contract drift. The root may post a read-only question to a descendant but may not answer as that descendant, command it, or mutate its task. Do not poll: use status only for an actual diagnostic need, and let the dirty-generation wakeup deliver new information.

The direct parent performs normal review of each direct child's scope, evidence packet, verification, and residual risk. Escalate to a dedicated non-delegating independent reviewer only when independent review is explicitly requested or the result is high-risk, disputed, or escalated. A reviewer reports findings first and returns a bounded pass/fail conclusion; it does not command leaves.

For durable work, the orchestrator writes a `hades.implementation-plan.v1` with repository-relative write scopes, dependencies, logical roles, risk, acceptance criteria, and verification. Agentic-Kanban is local and never synchronizes cards with the backend. OrgRun never calls a model. The orchestrator authors the plan; OrgRun materializes the DAG. Keep plans local: do not upload raw plans, source, transcripts, reasoning, or secrets.

Execution protocol:

1. The orchestrator writes `hades.implementation-plan.v1`.
2. Run `hades org validate <plan.json> --board <board>`, then `hades org materialize <plan.json> --board <board>` with an explicit board.
3. OrgRun validates and writes the initial DAG atomically; do not create it card-by-card.
4. The Kanban dispatcher schedules the DAG. Orchestrator, leaf, and reviewer are logical roles/profiles; they are not OrgRun stages.
5. Leaves implement only inside their declared scope and return local evidence: changed files, test commands/results, commit or patch reference, and residual risks. Leaf and reviewer work only through their direct-parent authority.
6. The direct parent verifies evidence and scope; use a dedicated reviewer only under the escalation rule above. The integration worker applies accepted work and runs the integration suite. Never auto-push or auto-merge.
7. Changes to the plan use a versioned `hades org amend` operation. Completion produces local task reports and one Final Development Report.

Native triage decomposition does not apply to OrgRun cards: use it only for ordinary unmanaged triage cards. Swarm is an explicit alternative, never an OrgRun stage; start it only when deliberately choosing manual swarm work instead of an OrgRun.

Routing uses configured logical roles (`orchestrator`, `leaf`, `reviewer`) only. Do not accept or invent provider/model choices from task arguments. Keep one model and byte-stable system prompt per conversation.

Escalate instead of guessing when the contract drifts, scope overlaps cannot be serialized, tests fail, or an interface decision remains unresolved. Keep results bounded and redact secrets.

When a gate discovers new remediation work, create the remediation card first,
then call `kanban_block` with `kind="dependency"` and that card's id as
`dependency_task_id`. This atomically adds the remediation as a parent of the
gate. Never dependency-wait without naming or already having an unfinished
parent: a todo card whose parents are all done is immediately promotable and
will otherwise churn through a fresh worker run every dispatcher tick.
