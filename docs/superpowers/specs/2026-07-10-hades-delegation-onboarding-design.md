# Hades delegation onboarding design

## Goal

Make local subagent model routing usable without hand-editing YAML. The guided
flow uses only already authenticated providers and models, preserves existing
choices, and adds a real `reviewer` runtime role.

## Commands and entry conditions

`hades delegation setup` creates a routing only when no valid routing exists.
It reads the local authenticated-model inventory. If the inventory is empty,
it launches the existing `hades model` picker; a successful picker returns to
the delegation wizard, while cancellation ends the flow without writing files.

`hades delegation configure` is the explicit path for inspecting or changing
an existing routing. The hierarchical-development skill must never rewrite an
existing routing: it suggests `setup` when routing is absent and `configure`
only when the user requests a change.

Both commands operate on the active profile's `config.yaml`. They must use an
atomic config update and must not write, display, or request credentials.

## Wizard

The wizard offers only models from configured, authenticated providers. It
recommends, but never silently selects, a profile for each role:

| Role | Recommendation goal |
| --- | --- |
| `orchestrator` | strongest available agentic coding/reasoning model |
| `reviewer` | strongest available verification/reasoning model |
| `leaf` | fastest, lowest-cost compatible agentic model |

Recommendations derive from the existing local model inventory metadata:
agentic/coding capability, context, pricing, and availability. When metadata
is incomplete, the wizard states that limitation and uses the current model as
the fallback recommendation. With one model, it explicitly assigns that model
to every role. The final screen shows the generated `delegation.profiles` and
`delegation.role_routes` YAML and requires confirmation before saving.

The saved profiles contain only `provider`, `model`, optional
`reasoning_effort`, `max_iterations`, and `child_timeout_seconds`. Profile
credentials remain in normal provider configuration; secret fields are invalid
in delegation profiles.

## Runtime role contract

The runtime supports `orchestrator`, `leaf`, and `reviewer` as real routing
roles. `leaf` and `reviewer` cannot delegate. `reviewer` receives an
independent-review prompt requiring scope verification, evidence checks,
regression assessment, and a bounded conclusion.

An `orchestrator` may be spawned only with a structured task contract. The
contract is validated locally before spawning and requires:

- objective and explicit deliverable;
- in-scope and out-of-scope boundaries;
- repository/workspace and declared write scope;
- input evidence and known dependencies;
- acceptance criteria and required verification;
- required return schema: child plan, delegated work units, evidence, open
  risks, and escalation conditions.

Missing or blank required fields return a clear tool error and no child is
created. The orchestrator prompt repeats this contract, requires bounded child
tasks, and directs the agent to escalate ambiguity rather than infer missing
requirements. This makes the delegation boundary executable and auditable;
it does not claim to make model behavior infallible.

`role_routes` maps all three roles. Legacy `delegation.model` and
`delegation.provider` behavior remains available when no named route applies.
An existing valid route is never altered by automatic onboarding. A partial,
invalid, or unauthenticated route is surfaced by `configure` and repaired only
after confirmation.

## Components

- A pure delegation-onboarding module reads routing and inventory, validates
  state, ranks candidates, and produces a proposed config patch.
- The Hades CLI command layer owns interactive prompts, transition to and from
  the existing model picker, confirmation, and atomic persistence.
- Delegation routing and tool schemas accept the three runtime roles and
  enforce the orchestrator task contract before child construction.
- The hierarchical-development skill contains only routing decisions and the
  command invocation guidance; it never embeds a model name or configuration
  fragment.

## Failure handling

- No configured models: hand off to the model picker and resume only after
  successful completion.
- Model picker cancelled: terminate without creating a delegation config.
- Existing valid routing: `setup` reports it and directs to `configure`.
- Existing invalid routing: expose validation errors; require explicit repair.
- Missing credentials or stale provider: do not offer that route as valid.
- Declined confirmation or failed write: leave the old file unchanged.

## Verification

Tests cover empty and populated inventories, deterministic recommendations,
one-model fallback, picker resume/cancellation, confirmation and atomic
persistence, legacy config compatibility, and no secret leakage. Delegation
tests cover actual routing for all three roles, reviewer non-delegation, and
rejection of incomplete orchestrator contracts before child creation.
