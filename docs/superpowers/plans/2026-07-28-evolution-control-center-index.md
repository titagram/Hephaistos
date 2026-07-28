# Evolution Control Center Implementation Plan Index

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the approved local-only Evolution Control Center at `/evolution` without mixing storage migration, domain APIs, and visual implementation into one unsafe change.

**Architecture:** Four ordered plans establish the local organism boundary first, add a typed in-process service and authenticated plugin API second, build the Cytoscape-based dashboard plugin third, and finish with end-to-end/privacy/visual verification. Each plan leaves the repository in a testable state and may be reviewed independently.

**Tech Stack:** Python 3, FastAPI, SQLite, React 19 supplied by the dashboard plugin SDK, TypeScript 6, Cytoscape.js 3.34, esbuild 0.28, Vitest 4, pytest.

## Global Constraints

- The canonical design is `docs/superpowers/specs/2026-07-28-evolution-control-center-design.md`.
- One installation owns one organism under `hermes_constants.get_organism_home()`; active dashboard profile never selects organism state.
- No Gnothi, Telos, Observer, blueprint, authorization, or lifecycle artifact may be uploaded to or queried through the remote Hades backend.
- Public read-only web research remains available through the existing agent/chat web capability and is not a network permission grant.
- `research_authorized` remains a lifecycle transition fact. Do not remove it from the state machine or weaken build/promotion gates.
- Read endpoints must not initialize directories, repair pointers, migrate state, or create an identity.
- Browser mutations call typed Python services directly. Never assemble CLI command strings or expose arbitrary shell execution.
- Preserve prompt caching and the core model-tool schema; this feature belongs at the dashboard/plugin edge.
- Keep `README_MEMORY_COMMANDS.ms` untouched; it is unrelated user work.

---

## Execution Order

- [ ] [Local organism foundation and remote isolation](2026-07-28-evolution-control-center-foundation-plan.md)
- [ ] [Domain service, jobs, and authenticated API](2026-07-28-evolution-control-center-api-plan.md)
- [ ] [Dashboard plugin and organism graph UI](2026-07-28-evolution-control-center-ui-plan.md)
- [ ] [End-to-end, privacy, accessibility, and visual verification](2026-07-28-evolution-control-center-verification-plan.md)

Do not begin Plan 2 until Plan 1 is merged locally. Do not begin Plan 3 until the snapshot, graph, and mutation contracts from Plan 2 are green. Plan 4 is the release gate for the complete feature.

## Cross-Plan Acceptance Matrix

| Design requirement | Owning plan | Release proof |
|---|---|---|
| One organism across profiles | Foundation | cross-profile pytest |
| No remote organism sync/query | Foundation | sync/provider negative tests |
| Non-mutating reads | Foundation + API | absent-root filesystem assertion |
| Bounded consistent snapshot | API | service contract tests |
| Bounded graph + truncation | API | graph query tests |
| Real Observer/Telos/Blueprint operations | API | service/API integration tests |
| Four graphical views | UI | Vitest render/interaction tests |
| Functional keyboard graph/list parity | UI + Verification | DOM/a11y checks |
| Always-on public research handoff | UI + Verification | no-grant lifecycle assertion |
| Strong digest-bound confirmation | API + Verification | stale/current confirmation E2E |
| Selected visual direction | UI + Verification | 1440×1024 and compact screenshots |

## Final Integrated Commands

Run these only after all four plans are complete:

```bash
./scripts/run_tests.sh \
  tests/hermes_cli/test_gnothi_store.py \
  tests/hermes_cli/test_hades_gnothi_cmd.py \
  tests/hermes_cli/evolution \
  tests/hermes_cli/test_hades_backend_sync_runner.py \
  tests/agent/test_hades_backend_memory_provider.py \
  tests/plugins/test_evolution_dashboard_plugin.py \
  tests/plugins/test_plugin_dashboard_auth_contract.py
npm run typecheck --workspace web
npm run test --workspace web -- --run \
  src/plugins/evolution-plugin.test.ts \
  src/plugins/evolution-accessibility.test.ts
npm run build:evolution --workspace web
node --check plugins/evolution/dashboard/dist/index.js
npm run build --workspace web
git diff --check
```
