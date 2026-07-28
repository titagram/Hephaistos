# Task 4 — Evolution control-center overview

Implemented the dashboard Overview view with a single readiness statement,
priority blocker links, explicit graph coverage text and icon, Observer/Telos
readiness, one eligible suggestion, durable pending-decision count, and a
12-event bounded audit timeline.

Observer controls fetch a fresh mutation context immediately before each
mutation and send its organism ID plus snapshot digest. Successful mutations
refresh once. A `409` refreshes once, shows “The organism changed elsewhere.
Refresh manually before continuing.”, and does not retry. Paused observers
cannot start scans; submitted scan jobs are tracked in the shell progress strip.
Corrupt snapshots surface diagnostics with no mutation controls.

Validation run:

- `npm --workspace web test -- evolution-plugin.test.ts evolution-graph.test.ts`
- `npm --workspace web run check:evolution`
