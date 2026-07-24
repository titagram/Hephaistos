# Autopoiesis lifecycle contracts v1

Project A owns the local, profile-scoped foundation at
`$HERMES_HOME/evolution`. The SQLite ledger is authoritative; generation
manifests and pointers are independently verifiable immutable projections.

## Versions and identities

- The ledger schema is v2 (`hermes_cli.evolution.ledger.SCHEMA_VERSION`), with
  the v1-to-v2 authorization migration performed atomically and fail-closed.
  V2 replaces empty v1 authorization placeholders with immutable request,
  decision, grant, and consumption relations; it preserves valid v1 lifecycle
  facts and rejects any unmigratable authorization record rather than adopting
  it. Event and authorization rows are append-only (SQLite UPDATE/DELETE
  barriers); `schema_version` is the singleton migration record.
- Manifests and pointer documents are schema v1. Canonical JSON is UTF-8,
  sorted-key, compact JSON without NaN. `content_digest` is SHA-256 over
  `ASCII-domain + NUL + canonical-json`.
- Lifecycle events use `hermes-evolution-lifecycle-event-v1`; generation IDs
  use `hades-evolution-generation-v1`; configuration fingerprints use
  `hades-evolution-config-v1`. Pointers bind generation ID, manifest digest,
  profile digest, committed event sequence/digest, designation time, and a
  pointer-integrity proof (`hades-evolution-pointer-v1`).
- Every remaining domain is literal: `hades-evolution-authorization-request-v1`,
  `hades-evolution-authorization-scope-v1`, `hades-evolution-profile-v1`,
  `hades-evolution-custom-profile-v1`, `hades-evolution-generation-proof-v1`,
  `hades-evolution-generation-tree-proof-v1`,
  `hades-evolution-pointer-observation-v1`,
  `hades-evolution-pointer-metadata-v1`, `hades-evolution-pointer-oversized-v1`,
  `hades-evolution-pointer-store-proof-v1`, and
  `hades-evolution-reconciliation-v1`. The proof/observation/reconciliation
  domains bind bounded integrity or diagnostic evidence, not public IDs.
- A generation identity binds the normalized manifest identity payload; its
  immutable tree proof and per-file digest are checked before use. Source
  paths are relative POSIX paths only.

## Lifecycle and authority

The closed attempt states are `draft`, `research_authorized`,
`blueprint_ready`, `build_approved`, `building`, `quarantined`,
`canary_running`, `promotion_ready`, `active`, `stable`, `rejected`,
`research_expired`, `build_failed`, `canary_failed`, `rolled_back`, and
`retired`. Only `state_machine.validate_transition` permits the v1 edges;
`rejected`, failure, expiry, and `retired` states do not re-enter the normal
path.

The exact edges are draft→research_authorized/rejected;
research_authorized→blueprint_ready/rejected/research_expired;
blueprint_ready→build_approved/rejected; build_approved→building/rejected;
building→quarantined/build_failed; quarantined→canary_running;
canary_running→promotion_ready/canary_failed; promotion_ready→active/rejected;
active→stable/rolled_back; stable→rolled_back/retired; and rolled_back→retired.
Research, build, and promotion approval-bound edges require their respective
authorization. Actors are exact: `operator` owns draft→research_authorized,
blueprint_ready→build_approved, and draft/research_authorized/blueprint_ready/
build_approved/promotion_ready→rejected; `workshop` owns
research_authorized→blueprint_ready/research_expired; `builder` owns
build_approved→building and building→quarantined/build_failed; `supervisor`
owns quarantined→canary_running, canary_running→promotion_ready/canary_failed,
promotion_ready→active, active→stable/rolled_back, stable→rolled_back/retired,
and rolled_back→retired.

`authorization.create_authorization_request`, `issue_grant`,
`deny_authorization_request`, and `consume_grant` are host-owned, append-only
facts. Research keys are `source_classes`, `domains`, `operations`, and
`duration`. Build keys are `component_classes`, `source_families`,
`dependency_families`, `workspace_class`, `isolation_policy`, `side_effects`,
and `resource_limits`. Promotion keys are `generation_id`, `report_digest`,
`expected_active_id`, `expected_lifecycle_sequence`, and `operation` exactly
`switch_active`. A grant is bound to kind, subject digest,
canonical request-confirmation digest, scope,
created/expiry timestamps, and one consumption. TTL values are normalized from
`evolution.authorization`: research/build 1800 seconds and promotion 900
seconds by default; expiry is rechecked under the write lock.

## Durable layout and consistency

`evolution/`, `generations/`, and mutable lifecycle state are owner-private:
directories are `0700`, and `evolution.db`, pointer documents, and lifecycle
locks are `0600`. Each published generation directory is `0555`; each
published file (including `manifest.json`) is `0444`. The baseline consists of
one empty-overlay generation plus one committed `baseline_designated` event;
`active.json` and `last-known-good.json` are byte-identical proofs of it.
The pointer fields are `schema_version`, `profile_id`, `generation_id`,
`manifest_digest`, `lifecycle_sequence`, `designated_at`,
`ledger_event_digest`, and `integrity_digest`.

```
$HERMES_HOME/evolution/                 0700
  .lifecycle.lock                       0600
  evolution.db                          0600
  active.json                           0600
  last-known-good.json                  0600
  generations/                          0700
    .publish.lock                       0600
    <generation_id>/                    0555
      manifest.json                     0444
```

`lifecycle_lock()` serializes bootstrap and mutating reconciliation compound
filesystem/ledger operations. Ordinary ledger/authorization writes use SQLite
write transactions plus append-only and coherence constraints; generation-store
publication and pointers use their own publication/atomic-replace mechanics.
`reconcile_evolution_state` and `read_evolution_snapshot` verify chain,
manifest, pointer, schema, ownership, and modes before returning data. Read
commands take immutable snapshots and do not mutate the live evolution tree.
Bootstrap can fill a missing baseline
pointer only when the existing committed baseline proof is coherent; it never
silently repairs foreign or arbitrary partial state.

## Operator contract

`hermes evolution init|status|history|show` emits one canonical JSON line.
`status` reports uninitialized/coherent/blocked with stable fields; `history`
is bounded (`--limit` 1..1000, `--after` non-negative); `show` accepts only
the four closed kinds (`suggestion`, `blueprint`, `generation`, `report`) and
returns a stable missing envelope with exit 1. Parser-invalid input exits 2.
Status uses `schema_version`, `status`, `initialized`, `overlay_enabled`,
`active_generation_id`, `last_known_good_generation_id`, and `diagnostics`;
history uses `schema_version`, `status`, `items`, and `next_after`; show uses
`schema_version`, `status`, `kind`, and `record`.
Defaults are `evolution.enabled=true`, observer enabled with threshold 3,
300-second scan interval and 0.65 notice score; retention is 10 workspaces and
30 days of evidence.

## Project B boundary

Project B plans future repository functions `import_new_events(ledger,
max_events, max_bytes)`, `evaluate_suggestions(ledger, policy, now)`,
`list_suggestions(ledger, states, limit, after)`, and `mark_surfaced(ledger,
suggestion_id)`. The host owns ledger construction and connection lifetime.
Its current Project A allowlist is public `append_event`, `history`,
`verify_chain`, and `LifecycleEvent`; repository-internal `transaction()` and
`_append(connection, LifecycleEvent)` are permitted only together for planned
atomic import/projection plus observer-event updates. `_append` is intentional
repository coupling, not authority or general orchestration. No transition,
authorization, pointer, or store calls are
allowed. It must not issue or consume
grants, write pointers, publish a
generation, create a candidate workspace, invoke adapters, open the web, or
change active/LKG state.

Only immutable stable-base baseline activation exists in v1. Candidate build,
canary, promotion, rollback, session activation, and Observer behavior are not
implemented by Project A.

## Evidence

The source contracts are `contract.py`, `state_machine.py`, `ledger.py`,
`authorization.py`, `store.py`, `pointers.py`, `locking.py`, `reconcile.py`,
and `command.py`; their behavioral suites live under
`tests/hermes_cli/evolution/` and the real process gate is
`tests/integration/test_autopoiesis_foundation.py`.
