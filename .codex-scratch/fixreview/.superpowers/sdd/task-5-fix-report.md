# Task 5 fix report — stale Graph Explorer state

Status: complete

Commit: `25573632d33f92c3cbc04f7e541731562f1f2a83`

## RED evidence

The focused RED command was:

`CI=true corepack yarn test --watchAll=false --runInBand src/components/devboard/GraphExplorer.test.tsx src/pages/GraphPage.test.tsx`

It produced four pertinent failures:

1. An older search completion replaced the newer result.
2. A project/scope/symbol rerender retained the previous symbol details.
3. The Impact panel did not render provenance/projection values from the impact response.
4. Global `/graph` did not load or render its read-only graph overview.

The deferred detail-bundle test also proves both neighborhood requests are started before any deferred response is resolved. Its final panel assertion was strengthened during GREEN to verify the opaque handle and symbol in the Symbol panel, rather than matching labels still present in search results.

## Fix

- Added independent generation tokens and loading/error state for search, detail bundles, and paths.
- Every async success, failure, and `finally` checks both component liveness and the current operation token.
- Scope/project changes and unmount invalidate all tokens; a new symbol invalidates the previous detail and path generations.
- React StrictMode remount sets component liveness back to true.
- Project/scope changes reset query, result, selection, details, path, errors, and loading.
- URL symbol clearing clears the selected graph; back/forward symbol changes load the new opaque handle.
- `initialLoadKey` is set when detail loading begins, preventing a URL update from issuing the same bundle twice.
- Impact renders its own source type/status/origin and projection status/quality.
- Cursor output is limited to search responses; impact remains non-paginated.

## Gates

- Focused component/page: 2 suites, 8 tests passed.
- Full focused/API/source suite: 6 suites, 51 tests passed.
- `corepack yarn tsc --noEmit`: passed.
- `CI=true corepack yarn build`: compiled successfully without ESLint warnings.
- `git diff --cached --check`: passed.

No backend, dependency, deploy, merge, push, or ledger change was included.
