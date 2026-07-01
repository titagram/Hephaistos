# Hades Doctor and Troubleshooting

## Doctor

Run:

```bash
hades doctor
```

The Hades Backend section checks registration, agent token presence, linked
workspaces, backend health, capabilities, job counts, proposal counts, and last
sync state.

Doctor does not send diagnostics to Laravel by default. To submit a compact
backend report explicitly, run:

```bash
hades doctor --report-backend
```

The report contains aggregate Hades state such as binding counts, job/proposal
counts, inbox counts, and last sync status. It does not include backend tokens,
job payload contents, or local absolute paths.

## Cleanup

Maintenance commands live under the doctor namespace. MVP cleanup includes
local-only cache/job cleanup such as:

```bash
hades doctor cleanup --orphaned-cache
hades doctor cleanup --orphaned-cache --all --yes
```

Cleanup does not delete backend memory. It only removes local stale or orphaned
state after confirmation.

## Degraded States

Common degraded states:

- backend unreachable
- token missing or revoked
- no linked workspace
- jobs in `waiting_confirmation`
- refused or conflicted memory proposals
- stale shared memory cache

Use `hades backend status --json` to get machine-readable actions.
