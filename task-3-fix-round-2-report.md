# Task 3 Fix Round 2 Report

## Scope

- Make the bounded `evolution_state_kind()` directory probe genuinely lazy.
- Preserve the unbounded caller classification contract and fail-closed bounded
  behavior.

## RED evidence

`test_dashboard_bounds_the_evolution_directory_probe_without_materializing`
was run with a task-scoped temporary directory and a unique pytest
`--basetemp`. It replaced `os.listdir()` with an assertion and instrumented
`os.scandir()`. The pre-fix code failed at `Path.iterdir()` because that API
uses `os.listdir()` before yielding its first child.

## GREEN implementation

- `evolution_state_kind()` now directly consumes `os.scandir(root)` inside its
  context manager.
- The bounded path converts only the consumed directory entries into `Path`
  values, observes no more than `max_members + 1` entries, and closes the
  scanner on every return path.
- The unbounded path retains its empty / lock-only / existing behavior while
  consuming at most two entries. Existing `lstat()` validation preserves the
  symlink and non-regular-file handling.

## Regression evidence

Each suite used a separately-created task-scoped `TMPDIR` and unique pytest
`--basetemp`.

| Target | Result |
| --- | --- |
| targeted lazy-scanner regression | 1 passed |
| `tests/hermes_cli/evolution/test_dashboard_service.py` | 39 passed |
| `tests/hermes_cli/evolution/test_bootstrap_matrix.py` | 11 passed |
| `tests/hermes_cli/evolution/test_command.py` | 11 passed |

## Static validation

- `python -m py_compile hermes_cli/evolution/bootstrap.py tests/hermes_cli/evolution/test_dashboard_service.py`
