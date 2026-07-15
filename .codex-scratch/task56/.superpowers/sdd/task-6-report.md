# Task 6 report — Graph Explorer behavior coverage

Status: complete

Commit: `f0bf3016e5971f07fb7d028c3a021706849b446d`

## Coverage

- Two-scope flow with explicit selection; no arbitrary choice when multiple scopes exist.
- Debounced search and empty results.
- Opaque `node_handle` for detail, inbound/outbound neighborhood requests with `families: ["call", "dependency"]`, and impact with `max_depth: 2`.
- Selected symbol, callers, dependencies/callees, impact explanation/family/edge types/distance, returned/truncated/cursor, projection quality, and canonical source.
- Real path request with opaque `from_handle`/`to_handle`, bounded depth and limit.
- Projection unavailable retry and detail-error retry while preserving the selected handle through the URL-update callback.
- Global `/graph` issues no graph GET/POST for an undefined project and navigates only after a real selection.
- Project-scoped query-parameter test preserves `run_id` while writing `scope_type`, `scope_id`, and opaque `symbol`. This was added as post-implementation coverage and passed immediately; it is not represented as a RED test.
- Existing `Badges.test.tsx` canonical and future-source fallback tests remain green.

## Final gates

- `CI=true corepack yarn test --watchAll=false --runInBand src/api/httpApi.test.ts src/api/mockApi.test.ts src/pages/graphExplorerModel.test.ts src/components/devboard/GraphExplorer.test.tsx src/pages/GraphPage.test.tsx src/components/devboard/Badges.test.tsx`: 6 suites, 47 tests passed.
- `corepack yarn tsc --noEmit`: passed.
- `CI=true corepack yarn build`: passed.
- `git diff --cached --check`: passed before commit.

No `.superpowers/sdd/progress.md` change was included in the commit.
