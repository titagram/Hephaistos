# Task 6 report — Evolution dashboard API

Implemented the bundled Evolution dashboard manifest and authenticated FastAPI
adapter at `/api/plugins/evolution`.

- Added strict, bounded request/query validation and stable sanitized error
  envelopes for the read, job, observer, Telos, confirmation, and blueprint
  contracts.
- Kept all work local to the dashboard service, local job manager, and
  confirmation store. The adapter has no remote backend or command-execution
  boundary.
- Added a process-local root seam for isolated API tests and releases the
  process-local job manager through router lifespan shutdown.

Validated with:

```text
11 passed: tests/plugins/test_evolution_dashboard_plugin.py
           tests/plugins/test_plugin_dashboard_auth_contract.py
84 passed: tests/hermes_cli/evolution/test_dashboard_service.py
           tests/hermes_cli/evolution/test_dashboard_confirmations.py
           tests/hermes_cli/evolution/test_dashboard_jobs.py
```

The project test wrapper encounters an existing pytest temporary-directory
cleanup recursion in this environment after otherwise passing test bodies; the
commands above used an isolated `PYTEST_DEBUG_TEMPROOT` and retention policy to
verify their actual test results.
