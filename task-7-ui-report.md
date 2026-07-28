# Task 7 — Evolution Control Center UI

## Delivered

- Added the plugin-scoped `plugins/evolution/dashboard/dist/style.css` referenced by the existing manifest. It applies the approved control-center direction: a dark technical surface, thin square borders, mono-led technical labels, pale mint foreground, restrained amber/red state accents, minimal elevation, and a continuous graph-plus-inspector desktop surface.
- Made the internal page and graph/list navigations real labelled tablists with one selected tab and associated panels. The graph/list view preserves the structured list fallback.
- Added explicit text health status for selected nodes; color remains a supporting signal rather than the only state indicator.
- Added a compact inspector drawer with native close control, Escape dismissal, and focus restoration to the originating graph/list control. Desktop retains the stable right-hand inspector.
- Added shared dialog focus containment for rebuild/comparison and consequential Telos confirmation: an initial focus target, basic Tab/Shift+Tab containment, Escape/cancel handling, and trigger-focus restoration. Revision dialogs now also have descriptions and an explicit cancel path.
- Rebuilt the isolated plugin bundle and kept CSS external to the bundle as declared by the manifest.

## TDD evidence

`web/src/plugins/evolution-accessibility.test.tsx` was written before the UI changes. Its first focused run failed all five intended behavioral checks:

1. internal tab semantics;
2. graph/list selected-tab fallback;
3. textual node health status;
4. revision-dialog description and focus containment;
5. consequential confirmation-dialog focus containment.

The drawer restoration expectation and a selected-graph-node textual-health expectation were then added, each observed failing, and implemented before the final green run.

## Verification

- `npm --workspace web test -- evolution-accessibility.test.tsx evolution-organism-view.test.ts evolution-strong-confirmation.test.tsx evolution-graph.test.ts evolution-plugin.test.ts` — 28 passed.
- `npm --workspace web test` — 80 passed across 18 files.
- `npm --workspace web run typecheck` — passed.
- `npm --workspace web run check:evolution` — passed; rebuilt `plugins/evolution/dashboard/dist/index.js` and checked its syntax.
- `npm --workspace web run build` — passed.
- `/Users/gabriele/Dev/Hephaistos/.venv/bin/python -m pytest -q --basetemp=<fresh /private/tmp/evolution-task7-ui-auth.*> tests/plugins/test_plugin_dashboard_auth_contract.py` — 4 passed.
- `git diff --check` — no whitespace errors.

## Visual validation note

The source visual reference was reviewed before implementation. A real dashboard-host preview could not be opened in this sandbox: the dashboard command was denied permission to bind a loopback port (`operation not permitted`) and the unrelated `hermes-lcm` plugin attempted a read-only database write. Build and rendered jsdom interaction verification remain green; no live browser screenshot was possible in this environment.
