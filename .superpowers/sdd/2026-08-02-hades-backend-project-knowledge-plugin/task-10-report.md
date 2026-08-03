# Task 10 — Backend optional-plugin cutover

## Result

The core no longer registers, parses, dispatches, or advertises the `backend`
command.  A normal enabled directory plugin provides both `hades backend` and
`/backend`; absent and disabled installations expose neither command and do not
import `hermes_cli.hades_backend_*` during CLI startup.

The old core parser/dispatcher and direct action implementations were removed,
as were the dedicated TUI RPCs, REST endpoints, dashboard page, Desktop status
polling/bug-intake route, installer bootstrap flags, and their core-only tests.
No change was made to the standalone plugin repository or to any live backend
state.

## TDD and verification

- RED: plugin-discovery test first found the core parser (`setup`, `bootstrap`,
  etc.) and an absent-command import of `hades_backend_db` through eager Kanban
  swarm imports.
- GREEN: `tests/hermes_cli/test_hades_backend_plugin_discovery.py` verifies
  enabled directory-plugin CLI help, disabled/absent command failure with an
  import guard, and enabled/disabled generic TUI catalog visibility.
- RED/GREEN: TUI catalog and completion tests proved that a registered plugin
  command was missing, then verified the generic plugin registry feeds both
  surfaces without a product allow-list.
- `424 passed in 10.51s`:
  `tests/hermes_cli/test_hades_backend_plugin_discovery.py`,
  `tests/hermes_cli/test_plugins.py`, `tests/test_plugin_skills.py`, and
  `tests/test_tui_gateway_server.py` (excluding the removed RPC test).
- Desktop typecheck passed; `desktop-slash-commands.test.ts`: 16 passed.
- TUI typecheck passed; `createSlashHandler.test.ts`: 66 passed.
- `bash -n scripts/install.sh`, Python compilation, `git diff --check`, and
  static project-token/legacy-route/legacy-command greps passed.
- `npm --prefix web run build` remains blocked by pre-existing unresolved
  `@nous-research/ui/*` workspace imports and implicit-any diagnostics across
  unrelated web files; no BackendPage/API reference is among the diagnostics.

## Retained `hades_backend_*` infrastructure

These files are intentionally retained coordination/runtime modules, not
registered Backend command or UI surfaces:

- `hades_backend_client.py`: used by task/worker, information worker, sync,
  doctor, coordination, Persephone, Kanban, indexers, runtime, project paths,
  jobs, and status.
- `hades_backend_db.py`: used by task/worker, information worker, sync,
  doctor, Kanban, agent coordination, status, projects, runtime, Gnothi
  runtime/source collectors, and Persephone receiver.
- `hades_backend_jobs.py`: used by sync, Gnothi source collector, and PHP,
  Python, SQL, and TypeScript indexers.
- `hades_backend_runtime.py`: used by task/worker, sync, doctor, Kanban,
  status, and projects.
- `hades_backend_status.py`: used only by Gnothi's runtime collector.
- `hades_backend_sync.py`: used by Kanban backend coordination and the Gnothi
  invariant declaration.

Their former startup path is avoided: Kanban swarm and Gnothi heavyweight
collectors now import lazily, so an absent `/backend` command does not pull this
infrastructure during CLI parser construction.

## Review round 2

- Replaced the discovery test's sibling-worktree copy with a minimal directory
  plugin written under each test's `tmp_path`.  Its manifest and `__init__.py`
  exercise the real generic registration ABI for the CLI parser and slash
  catalog, with only `set-token`, `status`, and `sync`; it has no machine-local
  repository path or standalone-plugin checkout dependency.
- Moved `COLLECTOR_ORDER` to the lightweight
  `hermes_cli.gnothi.collector_order` module, imported by both the command
  parser and builder.  This removes the duplicate command snapshot without
  importing collectors at startup.
- Restored the public, monkeypatchable `drift_status` and
  `build_organism_revision` symbols as module-level lazy wrappers.  The command
  calls those wrappers, so existing patches still control status/rebuild while
  fresh parser imports remain free of `hades_backend_*` modules.
- RED/GREEN evidence: the new shared-order/patchability test initially failed
  on the absent lightweight module; the prior review reproduction and existing
  status/rebuild tests failed on the removed wrapper symbols.  After the small
  implementation, `tests/hermes_cli/test_hades_gnothi_cmd.py` plus the hermetic
  plugin discovery tests passed: **10 passed**.  The Gnothi/coordination/Kanban
  bounded batch passed: **76 passed, 11 deselected**.
- Cross-surface regression: Desktop typecheck; Desktop slash test **16
  passed**; TUI typecheck; TUI slash test **66 passed**; web Vitest **92
  passed**.  Static route/RPC/legacy-command/credential gates and installer
  shell syntax passed.
- A fresh `git archive HEAD` extracted under `/private/tmp` ran the hermetic
  discovery file successfully: **3 passed**.  This archive contains no sibling
  standalone-plugin checkout.
