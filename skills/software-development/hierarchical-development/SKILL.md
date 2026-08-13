---
name: hierarchical-development
description: "Use when coordinating multi-agent software work through short-lived delegation or durable Hades OrgRuns. Classify the work, define bounded contracts and scopes, verify every child artifact, and integrate safely."
version: 2.0.0
author: Hades Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [subagents, delegation, hierarchical, orgrun, kanban, orchestration, review]
    related_skills: [plan, hades-coordination, requesting-code-review, test-driven-development]
---

# Hierarchical Development

## Overview

Coordinate complex software work through one of two execution modes:

- **Ephemeral delegation** for short, self-contained work that may safely disappear with the parent session.
- **Durable Hades OrgRun** for dependent, restart-safe, cross-workspace, integration-sensitive, or auditable work.

Both modes follow the same engineering discipline: decompose into bounded scopes, make dependencies explicit, verify child evidence directly, and run integration checks before completion. The difference is durability and coordination machinery, not quality standards.

## When to Use

Use this skill when work:

- spans several files, components, layers, or specialist concerns;
- contains independent units that can run in parallel;
- needs explicit ownership, dependencies, review, or integration;
- benefits from orchestrator, leaf, and reviewer roles;
- must survive a parent-session exit or machine restart.

Do not use it for a trivial edit, a single-file fix, or a linear task that the main agent can complete directly. For one focused reasoning or implementation subtask, call `delegate_task` directly without constructing a hierarchy.

## 1. Classify the Execution Mode

Choose the lightest mode that preserves the required guarantees.

| Observed need | Mode |
|---|---|
| Short, self-contained, result needed by the current parent | Ephemeral `delegate_task` |
| Parallel research or implementation with disjoint scopes, safe to abandon with the session | Ephemeral batch delegation |
| Work must survive restart or session loss | Durable OrgRun |
| Several dependent changes or worktrees | Durable OrgRun |
| Integration-sensitive, independently reviewed, or audit-reporting work | Durable OrgRun |
| Backend work-item lease or publishable execution trail | Durable OrgRun |

Do not create an OrgRun merely because several steps exist. Conversely, do not use ephemeral delegation when losing the parent process would lose important coordination state.

## 2. Verify Delegation Routing

Apply these branches exactly:

| Observed state | Required action |
|---|---|
| Routing is missing or incomplete | Run `hades delegation setup`. If no models are configured, let model onboarding finish, then resume setup. |
| All three logical role routes resolve | Preserve the configuration. Do not prompt and do not rewrite it. |
| The user explicitly requests different role models or limits | Run `hades delegation configure`, review the full preview, and require confirmation before applying it. |

Use only models already authenticated by Hades. Route through logical roles (`orchestrator`, `leaf`, `reviewer`); never accept or invent provider/model choices in task arguments. Keep one model and a byte-stable system prompt per conversation.

## 3. Decompose the Work

Create small, testable units with explicit boundaries. A good unit has:

- one deliverable or coherent concern;
- a repository-relative write scope;
- no overlap with concurrently executing siblings;
- explicit inputs and dependencies;
- checkable acceptance criteria;
- exact verification commands or artifact checks.

Prefer units completable in roughly 3–10 tool calls. Split a task that touches many unrelated files, needs more than about 30 iterations, or combines design, implementation, and integration without a clear reason.

For every unit record:

```text
id / title
objective and deliverable
in_scope / out_of_scope
workspace
write_scope
dependencies and input evidence
acceptance criteria
required verification
return schema
risk and review requirement
```

### Parallelism rules

- Run siblings in parallel only when their write scopes are disjoint.
- Serialize changes to a shared file or shared generated artifact.
- Dispatch dependencies first; pass their verified artifacts or decisions to dependants.
- Treat interface design as an explicit upstream deliverable when several children consume it.
- Escalate unresolved overlap instead of allowing competing writes.

## 4. Task Contracts and Authority

Before creating an `orchestrator`, provide a structured task contract containing:

- `objective`
- `deliverable`
- `in_scope`
- `out_of_scope`
- `workspace`
- `write_scope`
- `input_evidence`
- `dependencies`
- `acceptance_criteria`
- `required_verification`
- `return_schema`

Do not replace these fields with a prose-only goal. Every child starts without the current conversation history, so include all necessary paths, conventions, interface decisions, errors, and verification commands.

Only a child's direct parent may command it or change its contract. The root may inspect or ask a descendant for information, but must not bypass the parent chain to command the descendant or mutate its scope. Apply this recursively: each orchestrator owns only its direct children, and each child reports through its parent.

## 5. Ephemeral Delegation Procedure

Use this path when the work is short-lived and safe to lose with the current session.

### Dispatch

For one unit, call `delegate_task` with a complete goal and context. For independent units, use one batched `tasks=[...]` call rather than separate serial calls.

Include:

- project and workspace path;
- exact task contract;
- relevant files and existing behavior;
- project conventions;
- known dependencies or prior verified outputs;
- permitted toolsets;
- exact test and verification commands;
- expected bounded return format.

Do not dispatch two parallel children that modify the same file. Children have isolated sessions and cannot rely on each other's unreported state.

### Addressed coordination

Inside an active delegated task, use the existing `delegate_task` coordination surface:

- `action="coordination_post"` with `recipient_id`, `event_type`, and `summary` posts a bounded event. Add `artifact`, `blocker`, or `evidence_refs` only when they establish relevance.
- `action="coordination_status"` reads bounded delivery state for self, a direct child, or another authorized target.
- `action="coordination_inspect"` reads the authorized manifest and never changes a contract.

The runtime binds actor identity, delegation root, and project. Never request, accept, or synthesize these identities in arguments. Leaves and reviewers cannot spawn children; only an orchestrator may use `action="delegate"` below the root.

Sibling communication requires an explicit dependency, shared interface or scope, named artifact, or blocker. Otherwise address the direct parent. Blackboard delivery is informational and does not authorize contract drift. Do not poll routinely; use status only for a real diagnostic need and rely on runtime delivery.

### Child return contract

Require children to return bounded evidence, not confidence claims:

- changed or created files;
- commands actually run and their results;
- artifact path, patch, or commit reference;
- acceptance criteria status;
- unresolved questions and residual risks.

A child summary is self-reported and is never sufficient proof by itself.

## 6. Durable OrgRun Procedure

Use this path for durable, dependent, restart-safe, or integration-sensitive work.

### Repository and board prerequisites

The selected Kanban board must have a Git `default_workdir` pointing to the root of the repository being changed. Prefer one board per project or repository. Do not point a coding OrgRun at a generic home directory.

Example:

```bash
hades kanban boards create my-project \
  --name "My Project" \
  --default-workdir /absolute/path/to/repository
```

The gateway-hosted Kanban dispatcher must be available when workers should execute. OrgRun itself never calls a model: it validates the plan and materializes the DAG; the dispatcher launches configured workers.

### Author the plan

The orchestrator writes a local `hades.implementation-plan.v1` containing:

- repository-relative write scopes;
- dependencies and logical roles;
- risk and independent-review requirements;
- acceptance criteria and verification;
- a base commit appropriate for the target repository.

Keep plans local. Do not upload raw plans, source, transcripts, reasoning, or secrets.

### Validate and materialize atomically

```bash
hades org validate <plan.json> --board <board>
hades org materialize <plan.json> --board <board>
```

Always use an explicit board. OrgRun writes the initial dependency DAG atomically; never reconstruct an OrgRun card-by-card. If validation fails, correct the plan or board prerequisite before materialization.

### Execute and integrate

1. The Kanban dispatcher schedules the DAG.
2. `orchestrator`, `leaf`, and `reviewer` are logical roles/profiles, not OrgRun stages.
3. Leaves work only inside their declared scope and return changed files, commands/results, commit or patch references, and residual risks.
4. The direct parent verifies each child's evidence and scope.
5. Use an independent, non-delegating reviewer only when explicitly required or when work is high-risk, disputed, or escalated.
6. The integration worker applies accepted work and runs the integration suite.
7. Never auto-push or auto-merge.
8. Amend an existing plan only through a versioned `hades org amend` operation.
9. Completion produces local task reports and one Final Development Report.

Agentic Kanban is local and does not synchronize cards with a backend. Native triage decomposition is for ordinary unmanaged triage cards, not OrgRun cards. Swarm is an explicit alternative to OrgRun, never an OrgRun stage.

### Remediation dependencies

When a gate discovers new remediation work:

1. Create the remediation card first.
2. Call `kanban_block` with `kind="dependency"` and that card's ID as `dependency_task_id`.

This atomically makes the remediation a parent of the gate. Never wait on an unnamed dependency or block a card whose parents are all complete; such a card is immediately promotable and may churn through repeated dispatcher runs.

## 7. Review and Verification

The direct parent reviews each direct child's:

- declared versus actual scope;
- evidence packet;
- acceptance criteria;
- verification output;
- residual risk.

Use a dedicated reviewer only under the escalation rule. A reviewer reports findings first, returns a bounded pass/fail conclusion, and never commands leaves.

Verify child claims directly from the parent or integration workspace:

1. Read or inspect every claimed artifact.
2. Confirm every modified path is within the contract's write scope.
3. Run the specified focused tests.
4. Run relevant lint, formatting, and type checks.
5. After integration, run the full project test suite or the broadest applicable suite.
6. Inspect the final Git diff for omissions, conflicts, generated noise, and unrelated changes.

When verification fails, create a focused remediation assignment containing the exact failure output. Do not restart the entire workflow unless the plan itself is invalid.

## 8. Integration Completion Criteria

A hierarchical workflow is complete only when:

- every task has a terminal accepted or explicitly cancelled state;
- every claimed artifact has been independently verified;
- all changes are within approved scopes;
- dependency outputs and interfaces agree;
- focused checks and integration checks pass;
- no orphaned imports, dead code, conflict markers, or unrelated files remain;
- residual risks and intentionally deferred work are documented;
- no push or merge occurred without explicit authorization.

## Common Pitfalls

1. **Trusting summaries.** “Tests pass” or “file written” is not evidence. Read artifacts and execute checks yourself.
2. **Context starvation.** Children have no parent history. Supply paths, conventions, errors, dependencies, and expected output explicitly.
3. **Overlapping parallel scopes.** Concurrent writes to one file cause conflicts or silent loss. Serialize them or assign integration ownership.
4. **Tasks that are too wide.** A large undifferentiated assignment defeats hierarchical execution. Split by coherent, verifiable concern.
5. **Excessive nesting.** Each level increases debugging cost. Keep the tree shallow unless a real decomposition boundary requires another level.
6. **Wrong durability choice.** Do not use ephemeral delegation for restart-sensitive work or OrgRun for a trivial edit.
7. **Missing Git workspace.** OrgRun validation requires the board to resolve to the intended Git repository root.
8. **Contract drift.** Coordination messages do not grant new scope. Amend the contract or OrgRun plan explicitly.
9. **Unnecessary routing changes.** Preserve valid role routing unless the user explicitly asks to alter it.
10. **Premature integration.** Child-level tests do not replace the final integrated suite and Git-diff inspection.

## Verification Checklist

- [ ] Execution mode chosen from durability and coordination needs
- [ ] Role routing preserved or configured through the supported workflow
- [ ] Tasks are bounded, testable, and have disjoint parallel write scopes
- [ ] Orchestrator and child contracts contain every required field
- [ ] Dependencies and interfaces are explicit
- [ ] OrgRun board resolves to the intended Git repository when durable mode is used
- [ ] Child evidence was verified rather than trusted
- [ ] Focused tests and integration checks passed
- [ ] Final diff contains only approved changes
- [ ] Risks and deferred work are documented
- [ ] No unauthorized push or merge occurred
