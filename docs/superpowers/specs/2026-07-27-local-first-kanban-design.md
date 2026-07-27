# Local-First Hades Kanban With Optional Backend Sync

Date: 2026-07-27

## Summary

Hades Kanban must remain fully usable when no Hades backend is configured,
linked, or reachable. The local SQLite board is the operational source used by
the CLI, dashboard, dispatcher, workers, and lifecycle hooks. A linked backend
adds project-scoped synchronization; it does not replace the local runtime or
become a prerequisite for local cards.

The existing Kanban dashboard plugin will be reused. A focused
`hades kanban serve` command will launch the existing dashboard server and open
the selected local board directly.

## Goals

- Create, inspect, edit, decompose, dispatch, review, and complete local cards
  without configuring a backend.
- Synchronize remote cards and their lifecycle only when the current board's
  workspace has a valid backend binding.
- Keep local cards runnable when a linked backend becomes unavailable.
- Stop remote-origin cards before execution when their backend lease cannot be
  acquired, preventing duplicate remote work.
- Queue terminal remote results durably when delivery fails after a lease was
  acquired.
- Keep Kanban synchronization independent from the selected memory provider.
- Scope sync state, backoff, and errors to the current workspace binding.
- Provide a localhost graphical board by reusing the existing dashboard plugin.

## Non-Goals

- Replacing the local SQLite Kanban database with a backend database.
- Creating a second Kanban frontend, API server, or task model.
- Making local cards portable between machines without an explicit backend
  workflow.
- Allowing remote-origin cards to run without a valid remote lease.
- Coupling Kanban availability to project memory, artifacts, source slices, or
  general Hades awareness health.
- Exposing the localhost server beyond loopback by default.

## Product Contract

### Local-only

When no backend binding exists, Kanban reports `local_only`. This is a healthy
state:

- no backend client is constructed;
- no backend warning or degraded status is emitted;
- all local Kanban operations remain available;
- no memory, artifact, or project-awareness sync is attempted on Kanban's
  behalf.

### Linked and online

When the current workspace has a valid backend binding, Kanban imports eligible
remote work into the local board and publishes the lifecycle of remote-origin
cards. Local cards remain local unless a separate explicit publication workflow
is introduced in the future.

### Linked and offline

If a linked backend is temporarily unavailable:

- local-origin cards continue through the normal dispatcher;
- remote-origin cards cannot pass admission without a lease and remain
  deferred with `remote_backend_unavailable`;
- undelivered terminal results remain in a durable outbox;
- the board reports `backend_offline` without reporting local Kanban as broken.

This is fail-open for local work and fail-closed for remote work.

## Architecture

```text
CLI / dashboard / gateway / dispatcher / workers
                         |
                         v
                 Local Kanban SQLite
                         |
                         v
              Optional Kanban sync adapter
                         |
                         v
        Hades backend for the current binding only
```

The local database remains the only runtime dependency shared by all Kanban
surfaces. Backend behavior is implemented behind a bounded adapter and must not
leak backend setup requirements into `kanban_db`, the dispatcher, or the
dashboard API.

### Components

#### Local Kanban core

The existing `hermes_cli.kanban_db` module continues to own cards, dependencies,
runs, events, comments, boards, and worker lifecycle. Its ordinary operations
must not import or initialize backend modules.

#### Binding detector

A small read-only resolver maps the selected board or its workspace to one of:

- `local_only`
- `linked`
- `offline`
- `misconfigured`

No backend database or missing binding is `local_only`, not an exception.
Binding resolution must select the most specific workspace match and return at
most one binding. An error belonging to another project or binding must not
affect the selected board.

#### Kanban sync adapter

The adapter owns only Kanban-specific remote operations:

- pull eligible remote work items;
- materialize them idempotently as local cards;
- acquire and heartbeat remote leases;
- publish terminal success or failure;
- retry durable outbox entries;
- expose binding-scoped sync status.

It does not synchronize memory, artifacts, source slices, bug evidence,
Persephone messages, or unrelated backend jobs.

#### Dashboard launcher

`hades kanban serve` delegates to the existing dashboard server and dashboard
plugin. It does not introduce a standalone FastAPI app or duplicate the
compiled frontend.

## Data Model

Remote identity and lease state must become structured data rather than being
encoded only in task idempotency keys and comments.

### `kanban_remote_links`

One row links a local task to at most one remote work item:

| Field | Purpose |
|---|---|
| `task_id` | Local task primary key; unique |
| `project_id` | Authoritative backend project |
| `workspace_binding_id` | Authoritative backend workspace binding |
| `remote_work_item_id` | Remote work item; unique within its project |
| `lease_token` | Current remote lease token, when acquired |
| `lease_status` | `none`, `acquired`, `consumed`, or `expired` |
| `sync_status` | Last link-level synchronization state |
| `last_error` | Redacted bounded error text |
| `created_at` | Link creation timestamp |
| `updated_at` | Last state change timestamp |

The database must enforce uniqueness for `task_id` and for the complete remote
identity. Cross-project pages are rejected before any rows are written.

### `kanban_sync_outbox`

The outbox records remote mutations that must survive process termination:

| Field | Purpose |
|---|---|
| `id` | Local monotonic identifier |
| `task_id` | Linked local task |
| `operation` | `complete` or `fail` |
| `idempotency_key` | Stable unique delivery key |
| `payload` | Bounded JSON payload |
| `status` | `pending`, `retry`, `sent`, or `dead_letter` |
| `attempts` | Delivery attempt count |
| `next_attempt_at` | Per-entry retry deadline |
| `last_error` | Redacted bounded delivery error |
| `created_at` | Enqueue timestamp |
| `updated_at` | Last delivery transition |

Heartbeat traffic is not queued because a stale heartbeat cannot safely be
replayed later. Terminal results are queued because they represent durable
consequences of completed local work.

### Binding-scoped sync state

Background state keys include the workspace binding identifier rather than
using one global `background_sync` record. Status aggregation for a selected
board reads only its binding. Other binding failures may be displayed in a
separate profile-level diagnostic view, but cannot degrade the current board.

## Card Origins and Admission

A card without a `kanban_remote_links` row is local-origin. The dispatcher
admits it without consulting backend state.

A remote-origin card requires an acquired, unconsumed lease before spawn:

1. Resolve its exact stored project and workspace binding.
2. Reject any active runtime binding that conflicts with the stored identity.
3. Reuse an existing valid lease or request a new lease.
4. On success, persist the lease before allowing spawn.
5. On transient failure, defer the card with
   `remote_backend_unavailable`.
6. On identity or authorization failure, block the card with a typed
   capability reason requiring operator action.

A remote-origin card is never silently converted into a local card when the
backend disappears.

## Synchronization Triggers

Kanban synchronization is available through:

- explicit `hades kanban sync`;
- a bounded interval while `hades kanban watch` or the dispatcher is active;
- a bounded interval while `hades kanban serve` is active;
- a final outbox flush opportunity after a remote-origin task reaches a
  terminal state.

It must not be triggered as a generic side effect of every agent conversation.
General backend sync and memory-provider sync remain separate mechanisms.

Concurrent triggers share a per-binding lock. The adapter uses an interval and
per-binding exponential backoff so an offline backend is not hammered.

## Memory and General Backend Separation

`memory.provider` remains an exclusive memory selection:

- `holographic` uses local memory and Kanban sync never calls backend memory
  endpoints;
- `hades_backend` uses backend project memory through the memory provider.

Kanban synchronization is independent of both choices. Selecting local memory
does not disable an explicitly linked Kanban backend, and selecting backend
memory does not make Kanban require a binding when none exists.

General Hades backend synchronization must classify memory, Kanban, artifact,
job, and awareness failures independently. A Kanban sync failure may degrade
the selected binding's Kanban sync state, but not local memory health.

## CLI

### Local dashboard

```bash
hades kanban serve
hades kanban serve --board ariadne
hades kanban serve --board ariadne --no-open
```

The command:

- validates the board before starting;
- launches the existing dashboard server;
- binds to `127.0.0.1` by default;
- selects an available port using the dashboard's existing policy;
- opens `/kanban?board=<slug>` unless `--no-open` is present;
- runs in the foreground until interrupted;
- preserves the dashboard's existing token and cookie authentication.

Existing dashboard host and port options may be exposed where they can be
delegated without creating a second configuration surface. Non-loopback
binding retains the dashboard's existing security gates.

### Kanban sync

```bash
hades kanban sync
hades kanban sync --board ariadne
hades kanban sync --board ariadne --status
```

With no binding, the command exits successfully and reports `local_only`.
With a binding, it reports pulled, existing, leased, delivered, deferred, and
failed counts. `--status` performs no network mutation and shows the current
binding-scoped state and outbox depth.

## Dashboard UX

The existing `/kanban` plugin displays a compact board status:

- `Local only`
- `Backend synced`
- `Backend offline`
- `Sync error`

Remote-origin cards receive a `Remote` badge. A deferred remote card explains
that execution is waiting for a backend lease. Local cards retain their current
appearance and workflow.

The dashboard continues to read and write the local API. It never calls the
remote backend directly. WebSocket updates continue to derive from the local
append-only event table.

## Migration and Compatibility

Existing remote cards may encode identity in an idempotency key beginning with
`remote-kanban:` and lease state in comments beginning with
`HADES_REMOTE_LEASE`.

An idempotent local migration:

1. scans only cards with the recognized legacy markers;
2. validates the remote identifier and the card's authoritative project and
   binding;
3. creates `kanban_remote_links` rows;
4. imports the latest valid unconsumed lease when present;
5. records malformed or ambiguous legacy state as a local migration diagnostic;
6. performs no remote calls or mutations.

Legacy reads remain as a compatibility fallback for one release cycle. All new
writes use the structured tables.

## Error Handling

- Missing backend configuration: healthy `local_only`.
- Network timeout or connection error: `backend_offline`, local cards continue.
- Missing lease for a remote card: defer before spawn.
- Project, agent, or binding mismatch: fail closed for that remote card.
- Cross-project pull page: reject the page atomically.
- Remote terminal delivery failure: enqueue or retain an idempotent outbox
  entry.
- Permanent authorization or validation rejection: dead-letter the outbox
  entry and surface an operator action.
- Error text persisted locally or shown in the dashboard is bounded and
  redacted.

No failure from a non-selected workspace may change the selected board's sync
status or backoff.

## Security and Privacy

- The local server binds to loopback by default.
- Existing dashboard authentication applies unchanged.
- The browser receives local Kanban data only and never receives backend
  credentials or lease tokens.
- Lease tokens stay in the local database and are omitted from ordinary CLI,
  API, logs, events, and user-facing diagnostics.
- Remote payloads are validated for project and binding identity before local
  persistence.
- Outbox payloads contain only the bounded lifecycle fields required by the
  backend API.

## Verification Strategy

Tests are written before production changes and must demonstrate the intended
failure before implementation.

### Local-only integration

Using a temporary `HERMES_HOME` with no backend database or configuration:

- initialize and select boards;
- create, edit, decompose, link, dispatch, review, and complete cards;
- run watch and dispatcher cycles;
- confirm no backend module initializes a client;
- confirm status is healthy `local_only`;
- exercise the dashboard API against the real local database.

### Linked behavior

Against a contract-faithful fake backend client:

- import a remote card idempotently;
- reject cross-project and cross-binding pages atomically;
- acquire and persist a lease before spawn;
- publish terminal results once;
- retry outbox delivery without duplicating remote mutations.

### Offline behavior

- local cards continue while the backend client fails;
- remote cards defer before spawn;
- a terminal remote result survives process restart in the outbox;
- restored connectivity drains the outbox;
- backoff remains scoped to one binding.

### Migration

- valid legacy remote identity and lease markers migrate once;
- repeated migration is a no-op;
- malformed and ambiguous markers do not create remote links;
- migration performs no network operation.

### CLI and dashboard

- `hades kanban serve --board ariadne --no-open` delegates to the existing
  dashboard launcher with the direct Kanban route;
- invalid boards fail before the server starts;
- the status endpoint and UI distinguish local, synced, offline, and error
  states;
- remote badges and lease-wait explanations do not affect local cards.

### Live Hades validation

After targeted and broader regression tests pass, deploy through the normal
local Hades installation path and validate against the existing `ariadne`
board and session workflow:

1. inspect the board without requiring backend sync;
2. run a local-only disposable card to completion;
3. verify linked sync remains scoped to the current binding;
4. simulate or observe backend unavailability without blocking local work;
5. continue the already-running task chain and confirm no repeated capability
   or review-engine loop is reintroduced.

## Rollout

1. Add schema and migration with compatibility reads.
2. Introduce binding detection and binding-scoped status.
3. Move remote lease and terminal delivery to the structured adapter.
4. Add CLI sync and serve commands.
5. Add dashboard status and origin indicators.
6. Remove legacy writes after the compatibility cycle.

The rollout preserves existing local boards and does not automatically publish
local cards to the backend.
