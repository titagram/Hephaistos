# Task 4 fix round 2

## Change

Dashboard job storage now requires POSIX dir-fd anchoring. If the required
safe primitives are unavailable, all job storage access fails with the stable
`job_storage_unsafe` error before creating a directory, lock, or record.
The prior pathname-based directory, lock, record, and temporary-file fallback
has been removed.

## Regression coverage

`test_unanchored_storage_rejects_submission_before_any_path_write` disables
anchored storage, arms a parent-swap mkdir spy plus an open spy, and verifies
read and submission paths fail with the stable error without touching either
the configured root or the swapped outside target. The existing POSIX
submission tests cover the anchored flow.

## Verification

- `python -m pytest tests/hermes_cli/evolution/test_dashboard_jobs.py tests/hermes_cli/evolution/test_dashboard_service.py -q --basetemp /private/tmp/evolution-jobs-final` — 65 passed
- `python -m compileall -q hermes_cli/evolution/dashboard_jobs.py`
- `git diff --check`

The repository test wrapper ran both focused files successfully, but its
shared pytest temporary-directory cleanup then raised an unrelated recursive
cleanup error. The isolated-basetemp focused run above completed cleanly.
