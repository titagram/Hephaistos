# Task 14 follow-up: Desktop source Python propagation

## Root cause

`cmd_gui()` launched Electron with a copied environment but did not identify the
Python interpreter that invoked Hades. For a `--hermes-root` worktree without
its own `.venv`, Electron's existing source-runtime resolver therefore fell back
to `python3` on `PATH`.

On the reproduced machine:

- invoking runtime: `/Users/gabriele/Dev/Hephaistos/.venv/bin/python` (PyYAML available)
- PATH fallback: `/opt/homebrew/opt/python@3.14/bin/python3.14` (`import yaml` fails)
- `/Users/gabriele/.hades/logs/desktop.log` records the selected worktree and
  `ModuleNotFoundError: No module named 'yaml'`.

History confirms `findPythonForRoot()` has intentionally prioritized a valid
`HERMES_DESKTOP_PYTHON` since the desktop app was introduced. The missing
producer was the Python launcher.

## Fix

After `with_hermes_node_path()` creates the child-only environment, `cmd_gui()`
uses `setdefault()` to pass `sys.executable` as `HERMES_DESKTOP_PYTHON`.

This keeps an explicit user override authoritative, preserves model/provider
environment variables, and does not mutate `os.environ`.

## TDD evidence

- RED: focused Python regression failed with
  `KeyError: 'HERMES_DESKTOP_PYTHON'` before the production change.
- GREEN: `pytest -q tests/hermes_cli/test_gui_command.py` -> `63 passed`.
- Electron behavior guard executes `findPythonForRoot()` and verifies the
  explicit runtime beats a checkout-local venv candidate.

## Verification

- `npm --prefix apps/desktop run test:desktop:platforms` -> 293 passed, 1 skipped.
- `npm --prefix apps/desktop run typecheck` -> exit 0.
- `npm --prefix apps/desktop run build` -> exit 0; dist assertion passed.
- `npx eslint electron/backend-probes.test.cjs` -> exit 0.
- `.venv/bin/ruff check hermes_cli/main.py tests/hermes_cli/test_gui_command.py` -> passed.
- `git diff --check` -> passed.
- Live `desktop --source --skip-build --ignore-existing --hermes-root <worktree>`
  smoke reached `HERMES_DASHBOARD_READY` and logged the shared Python 3.13 venv,
  followed by `Hades backend is ready`; Electron was then stopped with SIGINT.
- No listener remained on the smoke-test port after shutdown.

The full desktop lint command still reports three unrelated pre-existing errors
in `titlebar-overlay-width.cjs` and `prompt-overlays.test.tsx`; the changed
Electron test file passes ESLint independently.
