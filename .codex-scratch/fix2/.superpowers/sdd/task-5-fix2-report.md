# Task 5 fix2 — navigation reconciliation

Status: complete

Commit: `26f0fd46c80ee78a1d361026608ef2903e721ee0`

## RED

Focused RED exposed three direct regressions:

- StrictMode effect replay left the initial deep-link in permanent `Loading graph…`.
- Same-project back/forward navigation with an identical scope list retained the repository scope instead of adopting the workspace-binding scope.
- `ProjectGraphPage` recreated `queryGraph` on a same-project URL rerender.

The path test was strengthened to inspect response status because a path response without edges correctly does not render its target in the compact selected-only SVG.

## Fix

- StrictMode cleanup clears `initialLoadKey` and invalidates tokens; effect replay launches a fresh current request and completes.
- Desired URL scope is part of the reconciliation context, so same-project scope changes reset state, invalidate old operations, update the select, and load the new symbol with the new exact scope.
- `queryGraph` is memoized by `projectId`.
- `updateQuery` is memoized and uses the functional `setSearchParams` form, preserving concurrent/current URL values.
- Changing path target increments `pathGeneration` and clears stale path/loading/error state before a late response can commit.

## Gates

- Focused: 2 suites, 12 tests passed.
- Full focused/API/source gate: 6 suites, 55 tests passed.
- TypeScript passed.
- CI production build compiled successfully without lint warnings.
- Cached diff check passed.

No ledger, backend, dependency, deploy, merge, or push change was included.
