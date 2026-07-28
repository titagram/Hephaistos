# Task 1 fix — round 1

## Outcome

Corrected `RevisionDiffResponse` so it models the structured rows emitted by
the Python `revision_diff` API. Added/removed, invariant, and runtime rows are
`GraphNode[]`; state, dependency, quality, and coverage rows use explicit
interfaces. Required fields remain non-optional, and the type documentation
records the API's 200-row truncation bound.

## TDD receipt

Added `web/src/plugins/evolution-revision-diff-types.test.ts` first. Its
complete structured API fixture failed `npm run typecheck` with the old
`string[]` declarations, reporting incompatible object rows for each changed
field. It passes after the types-only change.

## Verification

- `npm run typecheck`
- `npm test -- src/plugins/evolution-plugin-registration.test.ts src/plugins/evolution-revision-diff-types.test.ts`
- `npm run build:evolution`
- `npm run check:evolution`
- `git diff --check`

All passed.
