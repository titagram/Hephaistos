# C1 Final Specification Review

**Base:** `d328860f47fde018ea6cf07b5f0faa3d27886c67`

**Reviewed implementation:** `74c27506639195bb4ddff595821b312f4f1fdd6f`

**Reviewer:** fresh delegated specification reviewer

**Verdict:** Approved

## Spec compliance

- Direct deterministic evidence covers every indexed scan at two scales:
  call-site structures, unresolved/invocation edge scans, ordinary and
  framework branch arms, `INVOKES`/`RETURNS_TO` scans, call-site and scope
  lookup keys, and candidate-target set construction.
- Counters directly instrument tuple consumption or key hash/equality work;
  no wall-clock or trace/source-line oracle remains.
- Production indexes preserve the original semantic checks and error paths,
  including the exact non-inferred unresolved-subject predicate and exact
  diagnostic/order characterization.
- The change is limited to the C1 lifecycle model, IR regression, and frozen
  fixture. It does not touch backend code, framework semantics, Docker, or
  Carnovali.

## Findings

- Critical: none.
- Important: none.
- Minor: none.

## Residual boundary

Live RSS/Carnovali behavior remains intentionally unexercised. A bounded real
fixture with explicit RSS/elapsed monitoring remains mandatory before any
future Carnovali attempt.
