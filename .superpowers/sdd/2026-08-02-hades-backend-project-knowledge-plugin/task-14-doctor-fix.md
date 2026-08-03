# Task 14 follow-up — generic Doctor cutover

Date: 2026-08-03

## Scope and premise

- Starting Hades HEAD: `4dc95d666f6130ef5c3ed5673230c63c867bc704`.
- The Task 10 optional-plugin contract and Task 14 final review require the
  standalone plugin to own all Backend diagnostics. Core `hades doctor` must be
  generic and must not resolve a default Backend agent or contact Laravel.
- No plugin repository, live model, Backend service, sync endpoint, remote,
  or user profile was used. Every test profile/base directory was created
  below `/private/tmp`.

## Strict RED

Regression tests were added before production changes:

```text
HERMES_HOME=$TMP/home PYTHONDONTWRITEBYTECODE=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  --basetemp=$TMP/pytest \
  tests/hermes_cli/test_doctor.py \
    -k 'never_imports_backend_modules or never_reads_backend_state_or_constructs_client' \
  tests/hermes_cli/test_subcommands_batch.py \
    -k 'doctor_parser_rejects_backend_specific_legacy_surfaces or never_imports_backend_modules or never_reads_backend_state_or_constructs_client'
```

Result before implementation: **8 failed, 96 deselected**.

- Absent, disabled, and enabled plugin rows each observed an attempted
  `hades_backend_db` import.
- The state sentinel observed reads of default agent, bindings, jobs,
  proposals, inbox, and sync state; the configured token path reached client
  construction.
- The parser still accepted both `--report-backend` and `cleanup`.

These failures directly reproduced the final-review blocker.

## Minimal implementation

- Removed `_check_hades_backend`, `_hades_backend_client_from_config`, the
  unconditional Hades Backend section, and their logger/imports from
  `hermes_cli/doctor.py`.
- Removed the same legacy integration's explicit default-agent report and
  Backend-state cleanup paths. This leaves no `hades_backend_*` import or
  Backend client route anywhere in core Doctor.
- Removed `--report-backend` and the `cleanup` subparser from
  `hermes_cli/subcommands/doctor.py`; updated the slash-command registry to
  advertise only generic `--fix`.
- Deleted the obsolete positive legacy suite
  `tests/hermes_cli/test_hades_backend_doctor.py`; retained DB/client unit
  coverage in their owning modules.
- Updated current Doctor/operations/support/release documentation and stale
  catalog/doc expectations. Backend status remains plugin-owned; no generic
  plugin diagnostic API was added.

## GREEN and proportional evidence

Focused regression rerun: **8 passed, 96 deselected in 1.04s**.

Focused Doctor/cutover/docs selection: **118 passed in 12.31s**.

The first proportional run found two intentional stale expectations: one
still required `/doctor cleanup`, and one documentation fixture used a stale
phrase. After correcting those expectations, the proportional selection
passed **173 tests in 12.68s**:

```text
tests/hermes_cli/test_doctor.py
tests/hermes_cli/test_doctor_command_install.py
tests/hermes_cli/test_doctor_dedicated_provider_skip.py
tests/hermes_cli/test_subcommands_batch.py
tests/hermes_cli/test_security_advisories.py
tests/hermes_cli/test_managed_scope_surfacing.py
tests/hermes_cli/test_hades_backend_plugin_discovery.py
tests/hermes_cli/test_hades_local_surface.py
tests/integration/test_hades_backend_plugin_cutover.py
tests/agent/test_hades_backend_is_not_automatic.py
tests/test_docs_hades_mvp.py
```

Actual candidate help is now:

```text
usage: hades doctor [-h] [--fix] [--ack ADVISORY_ID]
```

It contains no Backend-specific argument or subcommand.

The final fresh pre-commit rerun of the same proportional selection passed
**173 tests in 13.11s**.

## Static gates

- `ruff check` on every changed Python source/test file: **PASS**.
- `py_compile` on every changed Python source/test file, with its cache rooted
  under `/private/tmp`: **PASS**.
- `git diff --check`: **PASS**.
- Exact active-surface search for `hades_backend`, `Hades Backend`,
  `report_backend`, `report-backend`, `doctor_action`, `doctor cleanup`,
  `_check_hades_backend`, and `_hades_backend_client` across core Doctor,
  parser, and registry: **no matches**.
- Current user-doc search for `hades doctor --report-backend` and
  `hades doctor cleanup`: **no matches**.

`ruff format --check` was diagnostic-only and reported broad pre-existing
format drift in six long-standing files (notably all of `commands.py` and
`test_docs_hades_mvp.py`); no formatter was run and no unrelated mechanical
rewrite was introduced.
