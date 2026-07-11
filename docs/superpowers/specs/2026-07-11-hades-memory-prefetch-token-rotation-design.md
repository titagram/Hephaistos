# Hades memory prefetch and token-rotation safety

## Problem

Two independent failure modes can make a healthy Hades project appear to have no usable backend memory.

First, project memory can contain thousands of exact source or wiki chunks. A broad automatic search may rank those raw chunks before compact memory and real wiki revisions. When raw chunks are excluded only after limiting results, the prefetch becomes empty even though relevant wiki pages exist.

Second, a long-lived Hades process can retain an agent token after another process rotates and persists a replacement token. A later `401` from the stale process currently marks the shared workspace binding as `auth_failed`, preventing the process with the valid replacement token from syncing or initializing project memory.

## Goals

- Raw source/wiki chunks must not consume result slots when `include_raw_chunks=false`.
- Automatic recall must use relevant wiki revisions when broad compact recall is empty.
- A stale process must not quarantine a binding after credentials have been rotated.
- A genuine `401` for the currently configured credential must continue to fail closed.
- Existing prompt-caching and user-message injection behavior must remain unchanged.

## Non-goals

- Automatically injecting the entire source or wiki corpus into every turn.
- Treating raw chunks as compact, authoritative memory.
- Suppressing valid authentication failures.
- Changing project-memory persistence, proposal review, or wiki revision schemas.
- Adding a new model tool.

## Design

### Backend search ordering

The backend memory-search query will apply the raw-chunk exclusion before ranking, pagination, and the requested limit whenever `include_raw_chunks=false`. The response will continue reporting how many raw candidates were omitted when that count is available, but omitted rows cannot occupy returned result slots.

This corrects the API contract for every client, rather than relying solely on a Hermes-side workaround.

### Defensive client prefetch

`HadesBackendMemoryProvider.prefetch()` will retain its current broad search first. If the formatted broad result contains no usable compact entries, it will perform one bounded fallback search with `domain="wiki"` and `include_raw_chunks=false`.

The fallback result is injected through the existing ephemeral memory-context path. It does not mutate the system prompt, conversation history, or tool schema. It runs at most once per prefetch and does not run when broad recall already produced usable context.

If both live searches fail, the existing local snapshot fallback remains authoritative. Backend errors remain redacted.

### Credential-generation guard

Each sync route will capture a non-secret fingerprint of the agent credential used for its HTTP client. When a route finishes with an unauthorized-only observation, the sync will reload the currently configured credential and compare fingerprints before persisting quarantine state.

- Same fingerprint: persist the existing unauthorized observation and allow the normal repeated-401 quarantine policy to mark the binding `auth_failed`.
- Different fingerprint: classify the observation as stale-credential output, do not increment the current route's unauthorized streak, and do not change the binding status.
- Missing replacement credential: retain fail-closed behavior and process the unauthorized observation normally.

Only one-way hashes are retained in process memory; raw tokens are never written to logs, SQLite, status payloads, or user-visible output.

### Concurrency and lifecycle

The comparison happens immediately before the existing auth-observation persistence transaction. This keeps the current database locking and route-level quarantine model intact. No cross-process lock or forced process restart is introduced.

Processes with old credentials may continue to receive `401`, but their results cannot invalidate a binding owned by a newer configured credential. New processes continue to initialize the provider from bindings whose status is `linked`.

## Error handling

- Wiki fallback timeout or transport failure falls back to the local memory snapshot.
- A malformed backend search response is treated as no usable live result, without exposing raw content.
- Credential fingerprint comparison failure is fail-closed: the unauthorized result is handled by the existing policy.
- `429`, `5xx`, timeouts, and transport failures never become authentication quarantine observations.

## Tests

### Backend

- A search corpus with more raw chunks than the result limit still returns a matching wiki revision when raw chunks are excluded.
- `include_raw_chunks=true` retains the existing raw-chunk behavior.

### Hermes provider

- Broad recall containing only omitted raw chunks triggers exactly one wiki-domain fallback.
- A successful broad compact recall does not trigger the fallback.
- A failed wiki fallback retains the existing local-cache behavior.

### Sync authentication

- A `401` followed by a changed configured-token fingerprint does not record an unauthorized streak or mark the binding `auth_failed`.
- A `401` with an unchanged fingerprint follows the existing quarantine threshold.
- Non-auth failures do not interact with the credential-generation guard.

## Acceptance criteria

- A natural-language question matching an imported Carnovali wiki page produces non-empty automatic prefetch context without requesting raw chunks.
- Explicit wiki-domain search continues to return the same wiki pages.
- Rotating credentials while an older Hades process remains alive cannot cause the new binding to become `auth_failed`.
- Real invalid current credentials still produce a visible `401` and eventual quarantine.
- Focused backend and Hermes regression suites pass with no new warnings.
