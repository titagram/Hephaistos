# Task 4 — Typed graph configuration and source snapshot identity

## Scope delivered

- Added `hermes_cli.hades_graph_config` as the only typed, immutable reader for
  the closed `hades.graph_index` subsection.
- Added exact defaults and closed range/type validation, including strict
  booleans, unknown-key rejection at the explicit index boundary, and the
  chunk-at-most-bundle invariant.
- Added compiled dependency/build and compulsory-secret exclusions that are
  always unioned with user `excluded_paths`; user configuration is additive and
  cannot put secret files back in source scope.
- Added deterministic source inventory hashing in `hades_index.inventory`:
  sorted NFC source-relative paths, streaming whole-file hashes, exact
  `path_utf8 + NUL + lowercase_file_sha256_ascii + LF` tree preimage, NFC
  collision failure, safe in-root file symlinks, and opaque invalid-symlink
  markers.
- Added Git/non-Git metadata, dirty-worktree detection, unavailable gitlink
  submodule marker hashing, and a pre/post extraction check in the backend AST
  job that raises `source_changed_during_index` on digest drift.

Committed as `c24586766 feat(hades): add graph v2 source identity and config`.

## Configuration source

`cli-config.yaml` is not versioned in this repository. The canonical runtime
defaults are `hermes_cli.config.DEFAULT_CONFIG`; the tracked user-facing
template is `cli-config.yaml.example`, which was updated instead of creating a
second, ad-hoc source of truth.

## TDD evidence

1. RED: `pytest tests/hermes_cli/test_hades_graph_config.py tests/hermes_cli/test_hades_backend_jobs.py -k 'source_identity or graph_index_config' -q`
   initially failed 13 tests with the expected missing
   `hermes_cli.hades_graph_config` module.
2. RED: the backend-job mutation test initially failed with `DID NOT RAISE`,
   proving the pre/post source check was not yet wired.
3. GREEN:
   - `pytest tests/hermes_cli/test_hades_graph_config.py tests/hermes_cli/test_hades_backend_jobs.py -k 'source_identity or graph_index_config' -q` → 16 passed
   - `pytest tests/hermes_cli/test_hades_graph_config.py tests/hermes_cli/test_hades_backend_jobs.py::test_populate_backend_ast_rejects_source_change_during_extraction -q` → 26 passed
   - `pytest tests/hermes_cli/test_hades_backend_jobs.py -k 'sync_git_tree_returns_bounded_manifest or workspace_file_iteration_prioritizes_source_dirs_before_assets or populate_backend_ast_rejects_source_change_during_extraction' -q` → 3 passed
   - `pytest tests/hermes_cli/test_config.py -q` → 131 passed
   - `pytest tests/hermes_cli/test_config_validation.py tests/hermes_cli/test_config_drift.py -q` → 22 passed
   - Ruff check, compileall, and `git diff --check` pass.

## Security/identity checks

- Invalid symlink preimages use `SHA256(SYMLINK_INVALID + NUL + exact link
  target bytes)` only. Link targets do not appear in public `SourceIdentity`,
  partial-reason values, or error messages.
- Git dirty and non-Git metadata are covered; unavailable submodules use their
  gitlink commit in a non-source marker digest and record
  `submodule_unavailable` privately for later completeness accounting.

## Residual limitations intentionally deferred

- The current legacy graph producer still cannot publish a v2 artifact; the
  v2 lifecycle producer/mapping is handled by later plan tasks. This task
  supplies the pre/post snapshot boundary it will use.
- `SourceSnapshot.partial_reasons` is private inventory data. Later lifecycle
  tasks must map those reasons into the v2 coverage/capability ledger.
