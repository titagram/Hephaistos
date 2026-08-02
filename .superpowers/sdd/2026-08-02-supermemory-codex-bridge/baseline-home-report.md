# Baseline HOME test repair report

## Files changed

- `tests/agent/test_copilot_acp_client.py`: import `hermes_constants` and force host mode only in `test_run_prompt_preserves_real_home_when_profile_home_available`.
- `.superpowers/sdd/2026-08-02-supermemory-codex-bridge/baseline-home-report.md`: this report.

Production code was not changed.

## RED evidence

Command:

```text
.venv/bin/python -m pytest tests/agent/test_copilot_acp_client.py::test_run_prompt_preserves_real_home_when_profile_home_available -q
```

Result before the test change: `1 failed in 1.74s`.
The assertion expected the real HOME (`.../real-home`) but the captured subprocess environment contained the profile HOME (`.../hermes/home`), reproducing the known VPS container-mode failure.

The prescribed `scripts/run_tests.sh` runner only accepts test-file paths, so supplying a node selector reported `No test files to run`; the direct venv pytest invocation above was used solely to capture the required single-test RED evidence.

## GREEN evidence

```text
scripts/run_tests.sh tests/agent/test_copilot_acp_client.py -q
11 tests passed, 0 failed

scripts/run_tests.sh tests/test_subprocess_home_isolation.py -q
21 tests passed, 0 failed
```
