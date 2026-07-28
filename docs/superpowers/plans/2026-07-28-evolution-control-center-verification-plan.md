# Evolution Control Center Verification and Release Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove the complete Evolution Control Center is local-only, cross-profile, governed, accessible, and visually faithful before calling it complete.

**Architecture:** A real plugin-discovery E2E test exercises the Python service/API with a temporary global root. Frontend contract tests prove action semantics and privacy. Live browser verification against the local dashboard validates plugin loading, responsive behavior, keyboard operation, and the approved visual direction.

**Tech Stack:** pytest, FastAPI TestClient, Vitest/jsdom, local Hades dashboard, in-app browser control, screenshot comparison.

## Global Constraints

- Complete the foundation, API, and UI plans first.
- Use temporary roots for automated tests and a disposable local test profile for live verification.
- Do not point any test at a configured remote Hades backend.
- Do not weaken a failing security or governance assertion to make the suite green.
- A missing implementation stage must remain unavailable; verification must not add a fake control.
- Preserve the pre-existing untracked `README_MEMORY_COMMANDS.ms`.

---

### Task 1: Add a real local Evolution E2E test

**Files:**

- Create: `tests/e2e/test_evolution_control_center.py`
- Modify: `tests/plugins/test_evolution_dashboard_plugin.py` only for shared fixtures that are genuinely reusable

- [ ] **Step 1: Create an isolated application fixture**

The fixture must:

- set the default Hermes root to `tmp_path / ".hermes"`;
- define two active profile homes under that root;
- use the real dashboard plugin discovery code;
- mount the discovered Evolution router;
- use the real Gnothi builder with deterministic collector fixtures;
- use the real Telos store, ledger, Observer service, and blueprint repository;
- install a sentinel that fails if any Hades backend client constructor is called.

- [ ] **Step 2: Test the absent read and explicit initialization**

Sequence:

```python
missing = client.get("/api/plugins/evolution/snapshot")
assert missing.json()["state"] == "missing"
assert not organism_root.exists()

initialized = client.post("/api/plugins/evolution/initialize", json={})
assert initialized.status_code == 200
assert organism_root.joinpath("identity.json").is_file()
```

- [ ] **Step 3: Test global Gnothi and profile invariance**

Submit rebuild, poll to terminal state, and inspect a real node plus dependencies. Switch the active profile override and assert:

- organism ID prefix unchanged;
- lineage prefix unchanged;
- Gnothi revision ID/digest unchanged;
- Telos active digest unchanged;
- blueprint history unchanged.

- [ ] **Step 4: Test inert Telos draft and digest-bound confirmation**

Create a draft and prove active digest is unchanged. Prepare activation, mutate the expected current state, and prove the stale confirmation returns `409` with no pointer change. Prepare again with current state, type the exact phrase, confirm, and prove the active pointer equals the target digest.

- [ ] **Step 5: Test suggestion-to-blueprint idempotency**

Seed enough sanitized Observer facts for one eligible suggestion bound to active Telos. Create its blueprint twice and assert both responses resolve to the same blueprint ID and canonical digest.

- [ ] **Step 6: Test always-on research does not grant mutation**

Generate the public research handoff payload or exercise its pure frontend serializer. Assert:

- no authorization request/grant/consumption rows were inserted;
- no build/promotion event was inserted;
- no private evidence/path/log field entered the brief;
- the suggestion remains in its prior lifecycle state unless a separate lifecycle transition is explicitly performed.

- [ ] **Step 7: Run the E2E test**

```bash
./scripts/run_tests.sh tests/e2e/test_evolution_control_center.py -q
```

- [ ] **Step 8: Commit**

```bash
git add tests/e2e/test_evolution_control_center.py tests/plugins/test_evolution_dashboard_plugin.py
git commit -m "test(evolution): cover local control center lifecycle"
```

---

### Task 2: Add explicit privacy and remote-isolation regression tests

**Files:**

- Create: `tests/hermes_cli/evolution/test_dashboard_privacy.py`
- Modify: `tests/hermes_cli/test_hades_backend_sync_runner.py`
- Modify: `tests/agent/test_hades_backend_memory_provider.py`
- Modify: `web/src/plugins/evolution-pipeline.test.ts`

- [ ] **Step 1: Test response redaction and bounds**

Seed private paths, token-like text, oversized summaries, raw logs, and unbounded evidence arrays into test fixtures. Assert API responses:

- contain stable redacted summaries;
- contain no home directory or workspace absolute path;
- contain no token/cookie/authorization value;
- cap evidence, suggestions, blueprints, revisions, events, and graph records;
- mark truncation truthfully.

- [ ] **Step 2: Test outbound research brief allow-listing**

The frontend serializer must construct the brief from:

```typescript
interface PublicResearchBriefInput {
  suggestion_id: string;
  public_title: string;
  public_summary: string;
  capability_labels: string[];
  public_questions: string[];
}
```

It must ignore additional object properties at runtime by constructing a new object field-by-field. Assert private fixture fields do not appear in the serialized brief.

- [ ] **Step 3: Re-run remote negative contracts**

Assert:

- backend sync ignores old remote `organism` capability advertisements;
- graph tool schemas accept only `project`;
- direct `scope="organism"` fails before network I/O;
- Evolution plugin routes do not import the remote client.

- [ ] **Step 4: Run privacy tests**

```bash
./scripts/run_tests.sh \
  tests/hermes_cli/evolution/test_dashboard_privacy.py \
  tests/hermes_cli/test_hades_backend_sync_runner.py \
  tests/agent/test_hades_backend_memory_provider.py \
  tests/plugins/test_evolution_dashboard_plugin.py -q
npm run test --workspace web -- --run src/plugins/evolution-pipeline.test.ts
```

- [ ] **Step 5: Commit**

```bash
git add \
  tests/hermes_cli/evolution/test_dashboard_privacy.py \
  tests/hermes_cli/test_hades_backend_sync_runner.py \
  tests/agent/test_hades_backend_memory_provider.py \
  web/src/plugins/evolution-pipeline.test.ts
git commit -m "test(evolution): enforce privacy and remote isolation"
```

---

### Task 3: Verify keyboard and compact-layout behavior

**Files:**

- Modify: `web/src/plugins/evolution-accessibility.test.ts`
- Modify: Evolution UI files only if verification exposes a real defect

- [ ] **Step 1: Add keyboard interaction tests**

Test actual events:

- Tab reaches all internal views and actions in DOM order;
- arrow keys change selected graph node;
- Enter opens/focuses the inspector;
- Escape closes compact inspector and restores trigger focus;
- `+`, `-`, and `0` invoke zoom/fit;
- confirmation dialog traps focus and cancel restores focus;
- list view exposes every currently filtered graph node and relationship.

- [ ] **Step 2: Add compact-layout assertions**

Use `matchMedia`/viewport stubs to render desktop and compact states. Assert the inspector changes from aside to dialog/drawer without losing selected-node content.

- [ ] **Step 3: Run accessibility tests**

```bash
npm run test --workspace web -- --run src/plugins/evolution-accessibility.test.ts
npm run check:evolution --workspace web
```

- [ ] **Step 4: Commit only if tests or fixes changed files**

```bash
git add web/src/plugins/evolution-accessibility.test.ts plugins/evolution/dashboard/src plugins/evolution/dashboard/dist
git commit -m "test(evolution): verify keyboard and compact interactions"
```

---

### Task 4: Perform live browser and visual QA

**Files:**

- Reference: `docs/superpowers/specs/assets/evolution-control-center-organism-concept-v2.png`
- Modify: Evolution CSS/components only for defects observed in this task
- Produce temporary evidence:
  - `/private/tmp/evolution-control-center-1440x1024.png`
  - `/private/tmp/evolution-control-center-compact.png`

- [ ] **Step 1: Build production assets**

```bash
npm run build:evolution --workspace web
npm run build --workspace web
node --check plugins/evolution/dashboard/dist/index.js
```

- [ ] **Step 2: Start or reuse the local dashboard**

Use the repository's existing local runtime and expose it at `http://127.0.0.1:9129/`. Do not point the dashboard at a remote backend. If an existing process owns the port, inspect it before deciding whether a restart is required.

- [ ] **Step 3: Open the page with the browser-control skill**

Use `browser:control-in-app-browser` to navigate to:

```text
http://127.0.0.1:9129/evolution
```

Verify:

- sidebar tab label and icon;
- default Organism view;
- persistent `Local organism · all profiles`;
- graph pan/zoom/select;
- list parity;
- all four views;
- missing/partial/stale/blocked/corrupt fixtures or seeded states;
- rebuild/scan polling;
- Telos confirmation;
- research handoff with no grant dialog;
- unavailable later stages have no controls.

- [ ] **Step 4: Capture desktop and compact screenshots**

Capture exactly:

- `1440 × 1024`;
- `900 × 900` compact desktop.

Compare against the approved concept for typography, background, borders, graph density, inspector width, active nav, mint/amber/red restraint, and empty-state fidelity.

- [ ] **Step 5: Fix only observed discrepancies and rerun checks**

For every fix:

```bash
npm run test --workspace web -- --run \
  src/plugins/evolution-plugin.test.ts \
  src/plugins/evolution-graph.test.ts \
  src/plugins/evolution-telos.test.ts \
  src/plugins/evolution-pipeline.test.ts \
  src/plugins/evolution-accessibility.test.ts
npm run check:evolution --workspace web
```

- [ ] **Step 6: Commit only if visual fixes changed tracked files**

```bash
git add plugins/evolution/dashboard/src plugins/evolution/dashboard/dist
git commit -m "fix(evolution): align control center visual behavior"
```

The temporary screenshots are verification evidence and are not committed unless the maintainer explicitly asks to add golden images.

---

### Task 5: Run final release gate and review the diff

- [ ] **Step 1: Run the integrated suite**

```bash
./scripts/run_tests.sh \
  tests/hermes_cli/test_gnothi_store.py \
  tests/hermes_cli/test_hades_gnothi_cmd.py \
  tests/hermes_cli/evolution \
  tests/hermes_cli/test_hades_backend_sync_runner.py \
  tests/agent/test_hades_backend_memory_provider.py \
  tests/plugins/test_evolution_dashboard_plugin.py \
  tests/plugins/test_plugin_dashboard_auth_contract.py \
  tests/e2e/test_evolution_control_center.py -q
npm run test --workspace web -- --run \
  src/plugins/evolution-plugin.test.ts \
  src/plugins/evolution-graph.test.ts \
  src/plugins/evolution-telos.test.ts \
  src/plugins/evolution-pipeline.test.ts \
  src/plugins/evolution-accessibility.test.ts
npm run check:evolution --workspace web
npm run build --workspace web
git diff --check
```

- [ ] **Step 2: Scan for forbidden implementation patterns**

```bash
rg -n \
  "__HERMES_SESSION_TOKEN__|shell=True|os\\.system|subprocess\\.|BackendClient|scope == [\"']organism[\"']" \
  plugins/evolution hermes_cli/evolution/dashboard_*.py
```

Expected: no matches.

- [ ] **Step 3: Review design coverage**

Check every acceptance criterion in `docs/superpowers/specs/2026-07-28-evolution-control-center-design.md` against a passing test or live-browser observation. Record the criterion-to-evidence mapping in the implementation handoff message; do not create a new permanent report unless requested.

- [ ] **Step 4: Use the completion verification skill**

Before claiming completion, use `superpowers:verification-before-completion` and inspect the fresh command output. Then use `superpowers:requesting-code-review` for a final review of the completed diff.

- [ ] **Step 5: Inspect repository state**

```bash
git status --short --branch
git log --oneline --decorate -12
```

Expected: tracked implementation committed, branch clean except the unrelated pre-existing `README_MEMORY_COMMANDS.ms`. Do not push or merge unless the user explicitly requests it.
