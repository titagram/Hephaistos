# Task 6 fix report — review regression coverage

Status: complete

Commit: `25573632d33f92c3cbc04f7e541731562f1f2a83`

Coverage added:

- Deferred A-then-B detail bundles resolved B-then-A; the Symbol panel remains on B.
- Both inbound and outbound neighborhood requests plus detail and impact are observed before any deferred bundle response resolves.
- Deferred first/second searches resolved second/first; the stale first result is ignored.
- Rerender from project 1/repository 1/symbol A to project 2/repository 2/symbol B loads B with exact new scope fields; clearing the URL symbol removes B.
- Every data request in the normal flow carries exact `scope_type` and `scope_id`.
- Impact uses `max_depth: 2`, has no cursor/plugin/internal credentials, and path retains bounded opaque `from_handle`/`to_handle` behavior.
- Impact source and projection fixtures are distinct from detail and must appear visibly.
- Global `/graph` GETs `getGraph(undefined, {runId, snapshotId})`, renders overview metrics/source, issues zero graph POSTs, never creates `projects/undefined`, and preserves safe run/snapshot parameters when selecting a project.

Final result: 6 suites and 51 tests passed; TypeScript and production build passed.
