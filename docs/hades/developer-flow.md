# Hades Developer Flow

## Local-Only Coordination

Subagent profiles, model routing, budgets, and toolsets are local-only Hades
decisions. The Laravel backend does not choose models or expose those choices
to other agents.

## Subagent Profiles

MVP defaults should provide curated local roles for planning, implementation,
review, and verification. Profiles live in local config/skills and can be used
without backend changes.

Inspect the bundled profile catalog with:

```bash
hades backend profiles
hades backend profiles --json
```

The MVP profiles are `planner`, `implementer`, `reviewer`, `sync-curator`, and
`memory-steward`. Each profile points at a symbolic local model profile such as
`hades.planner`; the actual provider/model is resolved from local `config.yaml`
and is never sent to Laravel.

## Model Routing

Model routing should prefer stronger models for planning and review, and cheaper
or specialized local profiles for bounded implementation. The routing decision
must stay local-only and respect the user's configured providers.

## Recommended Loop

1. Link the workspace with `hades backend bootstrap` or `hades project link`.
2. Run `hades backend sync` before starting shared work.
3. Use `hades backend profiles --json` and the `hades-coordination` skill for
   local subagent/model routing.
4. Run `hades doctor` and focused tests before handing off.
