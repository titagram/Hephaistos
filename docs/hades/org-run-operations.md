# Hades OrgRun operations

## Operating model

An OrgRun is a local, model-free way to version and materialize an implementation plan as an Agentic-Kanban DAG. Agentic-Kanban is local and never synchronizes cards with the backend. `hades kanban sync` and `hades org sync` therefore return the typed non-retryable boundary `agentic_kanban_has_no_remote_sync`.

OrgRun never calls a model. The orchestrator authors the plan; OrgRun materializes the DAG. The Kanban dispatcher schedules ready cards. `orchestrator`, `leaf`, and `reviewer` are logical roles resolved by local routing, not lifecycle stages or model choices stored in the plan.

Use the `hades.implementation-plan.v1` plan format. It contains bounded tasks, repository-relative write scopes, dependencies, risk, acceptance criteria, verification, and logical roles. Plans must not contain provider, model, or credential fields.

## Create and inspect a run

The orchestrator writes the plan, then the operator validates and materializes it on an explicit local board:

```bash
hades org validate plan.json --board <board>
hades org materialize plan.json --board <board>
hades org show <run-id> --board <board>
```

Materialization validates the plan and writes the initial DAG atomically. Do not create an OrgRun card-by-card. Use `hades org show` to inspect the plan version, state, topology, blocked work, and dispatchable work; use `hades org list --board <board>` to find runs on that board.

An amendment is the only way to change an existing managed plan:

```bash
hades org amend amendment.json --board <board>
```

The amendment is versioned against its base plan version. Revalidate its changed scopes and dependencies before applying it; do not edit managed cards directly to simulate a plan change.

## States and recovery

| State | Meaning | Operator action |
|---|---|---|
| `draft` | Plan record exists but is not ready to schedule. | Complete and validate the plan. |
| `validated` | The plan passed structural and routing checks. | Materialize it on the intended board. |
| `materialized` | The complete initial DAG exists locally. | Inspect it, then allow dispatcher scheduling. |
| `running` | Leaf work is active or ready behind dependencies. | Monitor evidence and direct-parent review. |
| `integrating` | Accepted leaf work awaits integration. | Run the integration checks and attach local evidence. |
| `reviewing` | Integrated work awaits parent or explicitly required independent review. | Record the review conclusion and remediation. |
| `completed` | All required local work, checks, and reports are complete. | Inspect and retain the reports. |
| `blocked` | A dependency, scope decision, failed check, or amendment conflict prevents progress. | Record the blocker, resolve it, then amend or resume through the DAG. |
| `cancelled` | The local run is intentionally stopped. | Preserve its local evidence; create a new plan if work restarts. |

For a blocked run, identify the named dependency or decision in `hades org show`, create any necessary remediation through the plan amendment, and let dependency gates reopen the affected nodes. Do not bypass a blocked card with direct edits or a new unmanaged duplicate.

## Authority, triage, and swarm

Only a leaf's direct parent may command that leaf or modify its task contract. The root/main agent may inspect or query a leaf for information, but must not command it or change its task contract unless it is that leaf's direct parent. Apply this rule recursively at every level. The direct parent normally checks each direct child's declared scope, evidence packet, verification records, and residual risks. Independent review is only when independent review is explicitly requested or the result is high-risk, disputed, or escalated.

Native triage decomposition does not apply to OrgRun cards. It remains available for ordinary unmanaged triage cards, which are separate from a materialized plan. Swarm is an explicit alternative, never an OrgRun stage: choose it manually for a separate swarm task, rather than inserting it into a managed run.

## Reports

Leaves return local task reports with changed files, verification commands and results, commit or patch references, and residual risk. Inspect these reports and the run status in the dashboard's local Kanban view. When the run completes, inspect the generated Final Development Report there as the local synthesis of task evidence, integration, and review results.

Never auto-push or auto-merge. Keep reports local and redact secrets, raw source, hidden reasoning, and transcripts.
