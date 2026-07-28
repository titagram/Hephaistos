# Task 2 report — Evolution dashboard shell and state

Implemented the typed Evolution dashboard client, snapshot store, and shared
operational shell.

- The API client covers every fixed dashboard route and uses only the host
  `SDK.fetchJSON` helper; it neither accesses nor emits a session token.
- `useEvolutionSnapshot()` performs initial/manual loading, retains the last
  valid response after a refresh failure, polls visible tabs every 30 seconds,
  stops automatic polling after a `409` refresh-required conflict, and polls a
  tracked active job.
- The accessible shell provides the local/all-profiles heading, organism and
  lineage prefixes, proper internal navigation state, status text, a
  non-destructive warning, and an active-job strip. It intentionally contains
  no graph/form/navigation-controller scope reserved for later tasks.

## TDD receipt

The required pure state/view-model test was added first and failed because
`view-model.ts` did not exist. It passes after implementing the minimal model.
The shell-registration test was changed first and failed against the former
placeholder component; it passes once the shared shell is registered. The API
route-contract test was written after deleting the client and failed for its
missing module, then passed after the typed client was recreated.

## Verification

- `npm run test --workspace web -- --run src/plugins/evolution-plugin.test.ts`
  — 6 passed
- `npm run test --workspace web -- --run src/plugins/evolution-plugin.test.ts src/plugins/evolution-api.test.ts src/plugins/evolution-plugin-registration.test.ts`
  — 9 passed
- `npm run check:evolution --workspace web` — passed
- `node --check plugins/evolution/dashboard/dist/index.js` — passed via the
  check script
- Bundle scan found no `react-dom`, `react/jsx-runtime`, or
  `__HERMES_SESSION_TOKEN__` reference.
- `git diff --check` — clean

No internal review was run, per task scope.
