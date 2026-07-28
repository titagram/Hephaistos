# Task 3 — Evolution Control Center Organism Explorer

## Delivered

- Added a pure graph model with stable Cytoscape IDs, semantic edge/state classes,
  exact client-side filters, dangling-edge removal, deterministic keyboard
  navigation, truncation notices, and one shared graph/list projection.
- Added the functional Organism view: graph/list toggle, filters, legend,
  keyboard-operable Cytoscape canvas, bounded node inspector, and truthful
  missing, partial, stale, blocked, corrupt, refresh-failure, and truncated
  states. No sample graph or export action is rendered.
- Added rebuild and immutable revision-comparison dialogs. Rebuild fetches the
  authenticated mutation context immediately before opening, displays the full
  organism ID and current digest, supports optional collectors, and only closes
  after the successful job response. Comparison uses the revision and semantic
  diff endpoints.
- Integrated the Organism view into the existing Evolution shell and regenerated
  the isolated plugin bundle.

## TDD and verification

- Added graph-model tests before implementation and observed the expected RED
  failure because `graph-model.ts` did not exist.
- Added the Cytoscape lifecycle test before `OrganismGraph` and observed the
  expected RED import failure; the mock now verifies one instance creation and
  cleanup destruction.
- `npm run test --workspace web -- --run src/plugins/evolution-plugin.test.ts src/plugins/evolution-graph.test.ts` — 14 passing.
- `npm run check:evolution --workspace web` — passed typecheck, plugin bundle,
  and bundle syntax check.

## Rendered-host validation

The local dashboard host could build but could not bind `127.0.0.1:8765` in
the workspace sandbox (`operation not permitted`). Browser interaction was
therefore not possible in this environment; no product state was mutated.
