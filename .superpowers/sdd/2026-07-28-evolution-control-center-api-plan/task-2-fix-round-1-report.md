# Task 2 — Fix Round 1 Report

## Scope

Fixed the Task 2 review findings in the bounded Gnothi query and Evolution
dashboard read surfaces.

## Changes

- Centralized redaction of embedded POSIX and Windows absolute paths in the
  existing Gnothi sanitization contract. Full workspace paths still retain the
  established workspace-relative representation; embedded paths become
  `[ABSOLUTE_PATH]` while surrounding useful text is preserved.
- Calculated the complete dependency-change set before bounding its response
  rows, so a 201st change correctly sets `truncated`.
- Normalized graph/public (`owner_class`) and raw contract
  (`owner: {"class": ...}`) owner representations without exposing owner IDs.

## TDD Evidence

Fresh, unique-base red runs established the three defects before production
changes:

- `test_gnothi_query.py`: embedded POSIX/Windows paths leaked, the public owner
  form was not accepted, and 201 dependency changes reported
  `truncated: false`.
- `test_dashboard_service.py`: an added raw diff node lost `owner_class` and
  leaked its embedded paths.

The first combined run shared one pytest base across two parallel file workers,
which created an unrelated fixture-cleanup collision. Each red/green command
below then used its own fresh `mktemp` base.

## Verification

- `tests/hermes_cli/test_gnothi_query.py`: 8 passed.
- `tests/hermes_cli/evolution/test_dashboard_service.py`: 21 passed.
- `tests/hermes_cli/test_gnothi_redaction.py`: 3 passed.
- `tests/hermes_cli/test_gnothi_e2e.py`: 1 passed.
- The repository virtual-environment Python completed `py_compile` for the
  three changed production modules.
- `git diff --check`: passed.

No remote paths, raw owner IDs, or mutation behavior were introduced.
