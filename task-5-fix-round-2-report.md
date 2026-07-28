# Task 5 — Fix Round 2

Status: DONE

## Fixed blockers

- `TelosStore` no longer exposes `open_mutation()`.  Pointer publication is
  now a module-private bridge that requires the non-serialisable capability
  owned by `host_transition`; the bridge still repeats the revision proof
  through retained descriptors before publishing.
- `TelosStore.__init__` now only binds paths.  It creates no directories and
  changes no permissions.  Explicit mutation initialization and inert revision
  writes first require all descriptor primitives, retain the organism root
  descriptor, and create `telos` / `revisions` only relative to that anchor.
- Dashboard draft persistence uses the inert revision-write API with an
  anchored expected-parent check, rather than receiving a general mutation
  handle.

## TDD evidence

The new contract tests were run red before implementation:

- Direct `store.open_mutation()` was reachable.
- A root replacement could be triggered by constructor directory creation.
- Removing descriptor primitives still allowed constructor filesystem writes.
- The private pointer bridge did not yet reject an untrusted capability.

They now cover public-bypass rejection, untrusted capability rejection,
constructor non-mutation, deterministic root replacement before anchoring,
and unsupported-primitives-before-write behavior.

## Verification

Each test command used a distinct `--basetemp` root:

- Telos contract/store: 10 passed
- Telos adversarial: 14 passed
- Telos approval security: 2 passed
- Telos gateway dispatch: 8 passed
- Telos CLI approval: 56 passed
- Dashboard confirmations: 5 passed
- Dashboard service: 53 passed
- TUI Telos approval: 23 passed
- Global lifecycle migration and P0 lifecycle: 18 passed
- Constructor-side-effect caller regressions: 40 passed

`ruff check`, `python -m compileall`, and `git diff --check` passed.
