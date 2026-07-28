# Task 6 report — Evolution dashboard API

Implemented the bundled Evolution dashboard manifest and authenticated FastAPI
adapter at `/api/plugins/evolution`.

- Added strict, bounded request/query validation and stable sanitized error
  envelopes for the read, job, observer, Telos, confirmation, and blueprint
  contracts.
- Kept all work local to the dashboard service, local job manager, and
  confirmation store. The adapter has no remote backend or command-execution
  boundary.
- Added a process-local root seam for isolated API tests and releases the
  process-local job manager through router lifespan shutdown.

Validated with:

```text
11 passed: tests/plugins/test_evolution_dashboard_plugin.py
           tests/plugins/test_plugin_dashboard_auth_contract.py
84 passed: tests/hermes_cli/evolution/test_dashboard_service.py
           tests/hermes_cli/evolution/test_dashboard_confirmations.py
           tests/hermes_cli/evolution/test_dashboard_jobs.py
```

The project test wrapper encounters an existing pytest temporary-directory
cleanup recursion in this environment after otherwise passing test bodies; the
commands above used an isolated `PYTEST_DEBUG_TEMPROOT` and retention policy to
verify their actual test results.

---

# Task 6 report — Evolution pipeline UI

## Result

Implemented the local Evolution Pipeline view with a fixed stage rail,
bounded attempt selection, suggestion and immutable-blueprint inspectors, and
append-only audit presentation.

- Unsupported Build, Canary, Promotion, and Stable stages are descriptive
  `aria-disabled` items with no action buttons.
- Blueprint creation is exposed only for eligible suggestions, sends a fresh
  full mutation context and exact suggestion digest, refreshes on conflict
  without retrying, and selects the returned existing-or-created immutable
  blueprint.
- Public research copies a numeric, public-only brief to the clipboard and
  takes the user to `/chat`; it intentionally contains no summaries, evidence,
  paths, logs, memory, prompts, private-source data, or organism artifacts,
  and makes no Evolution authorization call.
- Long local summaries and requested scope are capped with explicit expansion
  controls. The UI also names unavailable public research references and
  authorization history rather than fabricating them.

## TDD evidence

`web/src/plugins/evolution-pipeline.test.ts` was added before
`pipeline-model.ts`; its initial focused run failed because that module did
not exist. The implementation then made all five pipeline behavior tests pass.
A second red/green cycle tightened the research-brief exclusion so even the
word `private` is not present in copied text.

## Verification

- `npm run test --workspace web -- --run` for all ten Evolution plugin test
  files: **39 passed**.
- `npm run check:evolution --workspace web`: passed; regenerated
  `plugins/evolution/dashboard/dist/index.js` and validated it with Node.
- `npm run build --workspace web`: passed.
- `git diff --check`: clean.
