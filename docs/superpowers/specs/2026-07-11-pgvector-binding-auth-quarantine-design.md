# Pgvector reset and binding auth quarantine

## Scope

This change completes the two pending embedding migrations in the temporary
Laravel backend and prevents stale local Hades bindings from producing an
unbounded train of authenticated requests that all fail with HTTP 401.

The backend database contains development-only test data. It may be reset, but
the user seeder must run successfully before the work is considered complete.

## PostgreSQL and migrations

The compose file already pins `pgvector/pgvector:pg16`, while the running
container was created from `postgres:16-alpine`. The database volume will be
removed and PostgreSQL recreated from the compose definition. The acceptance
sequence is:

1. Stop the compose stack and remove the PostgreSQL development volume.
2. Recreate PostgreSQL from the pinned pgvector image.
3. Prove that the `vector` extension is available.
4. Run all Laravel migrations from an empty database.
5. Run the existing user seeder explicitly.
6. Verify the expected users exist without printing credentials or hashes.
7. Run the Hades and related authentication regression suites.

No migration will silently skip vector setup on PostgreSQL. The two embedding
migrations must both be recorded as ran, the `embedding vector(1536)` column
must exist, and the HNSW cosine index must exist.

## Local binding authentication quarantine

One failed request is not enough to unlink or quarantine a binding. Network
failures, 5xx responses, rate limits, and other errors do not count toward the
authentication threshold.

Hades records authentication health per exact `(project_id, agent_id)` route.
After a complete manual or background sync cycle:

- a route with an HTTP 401 increments its consecutive authentication failure
  count once, even if several operations for that route returned 401;
- any successful authenticated response resets the route count to zero;
- after three consecutive failed cycles, every linked binding owned by that
  exact route moves to `auth_failed` quarantine;
- quarantined bindings are excluded from sync and Persephone receiver route
  inventories, so they cannot continue generating 401 requests;
- no binding, token, cached data, inbox message, or agent row is deleted.

The quarantine state stores safe metadata only: failure count, first/last
failure timestamps, and a fixed reason code. It never stores the bearer token
or raw response body.

## Recovery and operator UX

Backend status and support reports expose quarantined-route counts and a clear
re-authentication action. Running the existing backend setup/bootstrap flow for
the same workspace and project replaces the agent token and restores the
binding to `linked`, clearing its authentication failure state.

Manual sync remains successful when at least one route is healthy. Errors are
reported per route; a quarantined historical route does not make an unrelated
current project degraded.

## Testing

Tests must prove:

- first and second 401 cycles retain a linked binding;
- the third consecutive 401 cycle quarantines only the exact failing route;
- multiple 401 operations in one cycle count once;
- a successful authenticated cycle resets the counter;
- 403, 404, 429, 5xx, timeouts, and connection errors never trigger auth
  quarantine;
- another agent or project remains linked and continues syncing;
- setup/bootstrap restores an `auth_failed` binding;
- receiver/status route counts exclude quarantined bindings;
- existing migration and Hades contract suites remain green;
- an empty pgvector database migrates fully and the user seeder repopulates
  users.

## Non-goals

- No automatic backend agent deletion.
- No automatic token rotation without a bootstrap credential.
- No preservation of the current development database contents.
- No change to the Persephone message envelope or authorization contract.
