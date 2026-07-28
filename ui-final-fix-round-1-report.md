# Evolution UI final fix — round 1

## Scope completed

- Job polling is keyed to `job_id` and `state`, polls immediately then every
  three seconds, refreshes a completed job's snapshot, and keeps all terminal
  states visible without re-polling.
- Public research briefs use the server-owned, deterministic topic **“Local
  capability improvement”**. It is not derived from summaries, evidence,
  filesystem paths, logs, or private artifacts; no authorization route was
  added.
- The organism graph forwards every visible supported kind to the server,
  including mixed `capability` and `provider` filters. The API validates the
  same expanded public kind set.
- Selecting a graph node by tap now opens its compact inspector, while Enter
  continues to open the currently selected node.
- The Evolution shell opens on the Organism view.
- Audit copy now accurately calls the response bounded history rather than
  recent activity.
- The TypeScript snapshot contract now matches the dashboard projection for
  Gnothi coverage, Telos prefixes and revision summaries, Observer
  paused/degraded status, generation prefixes and overlay state, pipeline
  status, and blocker-priority coverage.

## Regression evidence

Focused RED tests were added before implementation for all seven changes.
The focused GREEN pass was 43/43: 41 frontend tests and 2 Python tests.

Final verification completed successfully:

- `npm --workspace web test` — 20 files, 90 tests passed.
- `npm --workspace web run check:evolution` — typecheck, rebuilt
  `plugins/evolution/dashboard/dist/index.js`, and syntax check passed.
- `npm --workspace web run build` — production Vite build passed.
- `../graph-lifecycle-v2-agent/.venv/bin/python -m pytest -q
  tests/hermes_cli/evolution/test_dashboard_service.py
  tests/plugins/test_evolution_dashboard_plugin.py` — 77 tests passed.
- `git diff --check` — no whitespace errors.

The Python suite used the available adjacent-worktree virtual environment. Its
pytest process emitted an atexit temporary-directory cleanup recursion warning
after reporting 77 passing tests and exiting successfully; it did not change
the test result.

## Browser validation note

The browser session was started and named for Evolution validation. The local
Vite app hosts the dashboard shell, while Evolution is a plugin injected by the
Python dashboard host; without that host running, `/evolution` resolves to the
shell and its API proxy cannot reach port 9119. The user-visible tap/Enter,
filter, and initial-view paths are therefore verified with the jsdom component
regressions above rather than claiming an unavailable integrated browser flow.
