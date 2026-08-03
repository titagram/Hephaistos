# Task 9 report: detach Backend from the core memory lifecycle

Date: 2026-08-03

## Outcome

Hades core no longer discovers a bundled `hades_backend` memory provider and no ordinary agent, file/terminal turn finalizer, or gateway lifecycle starts Backend work automatically. The legacy explicit sync command remains temporarily available for Task 10 parity, but it is now restricted to project artifacts and source-slice candidate discovery.

## Changes

- Deleted `plugins/memory/hades_backend/` and its provider-positive test suites without changing the generic memory provider, manager, prefetch, or write-bridge contracts.
- Reworked `run_backend_sync()` into an explicit, binding-scoped project-artifact runner. It no longer reads memory configuration, mirrors memory, exchanges proposals, pulls remote jobs, polls inbox/Persephone, flushes logbook entries, or reports those legacy counters.
- Removed the legacy background-sync APIs and moved the historical background-state key helper into status code solely so existing persisted state remains readable.
- Removed the gateway-owned automatic Persephone receiver supervisor, startup, shutdown, retry, and health-monitor lifecycle.
- Updated the explicit CLI output to describe artifacts, unchanged artifacts, artifact errors, and source candidates only.
- Replaced obsolete provider/piggyback/lifecycle positive tests with negative invariants for provider discovery, inert removed-provider selection, normal/file/terminal turns, memory-config preservation, explicit-sync isolation, and absent gateway automatic lifecycle methods.

## TDD evidence

The new cutover invariants first failed for the intended reasons:

- provider discovery still returned `hades_backend`;
- selecting that provider still activated Backend memory;
- explicit sync still made memory/job/inbox/logbook calls;
- automatic sync entry points still existed;
- the gateway still exposed its automatic Persephone lifecycle.

After the cutover, the focused suite passed:

```text
342 passed, 1 deselected in 7.20s
```

Command scope: Task 9's nine-file focused suite plus `tests/gateway/test_hades_persephone_lifecycle.py`.

Proportional status and gateway startup/shutdown coverage passed:

```text
45 passed, 1 deselected in 20.43s
```

One unrelated platform-sensitive test was excluded from that proportional run: `test_gateway_stop_systemd_service_restart_exits_cleanly` expects Linux/systemd exit code `0`, while this macOS host intentionally follows the existing `sys.platform == "darwin"` branch and returns launchd restart code `75`. It reproduces in isolation and is unrelated to the removed receiver lifecycle.

Static verification also passed:

- Ruff on every touched Python file.
- `compileall` for the touched production modules.
- `git diff --check`.
- provider package absence check.
- caller/dead-import searches across agent, CLI, gateway, TUI, desktop, plugin, and test surfaces.

## Boundary audit

- Agent lifecycle: no Backend sync import or automatic turn-finalizer call remains.
- CLI/web: Backend sync is reachable only through explicit project-sync commands/endpoints retained for Task 10 parity.
- Gateway: no Backend/Persephone receiver lifecycle remains.
- TUI/Desktop: status and explicit action surfaces remain, but neither starts project sync automatically; generic memory UI follows provider discovery, where `hades_backend` is now absent.
- Generic memory: external provider tests remain green and the user's existing `memory` configuration is left byte-for-byte unchanged by explicit project sync.

Commit subject: `refactor(hades): detach Backend from memory lifecycle`
