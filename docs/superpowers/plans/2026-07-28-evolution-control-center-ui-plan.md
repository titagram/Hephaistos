# Evolution Control Center Dashboard UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the bundled `/evolution` dashboard plugin with the approved organism-graph visual direction and four truthful operational views.

**Architecture:** The plugin uses the host's React 19 and Nous UI primitives through `window.__HERMES_PLUGIN_SDK__`. Cytoscape.js is bundled into the plugin IIFE for the interactive canvas; React itself is never bundled. Pure view-model functions isolate contract handling and are covered by Vitest. The graph always has an equivalent structured list and selected-node inspector.

**Tech Stack:** React 19 host SDK, TypeScript 6, Cytoscape.js 3.34.0, esbuild 0.28.1, CSS, Vitest 4, Testing Library, jsdom.

## Global Constraints

- Complete the API plan first and treat its HTTP schemas as fixed inputs.
- Use the official maintained Cytoscape.js package and built-in TypeScript declarations: `https://js.cytoscape.org/`.
- Use esbuild only to bundle the plugin source: `https://esbuild.github.io/`.
- Do not bundle React, React DOM, Nous UI, dashboard authentication, or a second router.
- All requests use `SDK.fetchJSON` or `SDK.authedFetch`; never read `window.__HERMES_SESSION_TOKEN__`.
- Organism is the default internal view.
- Do not display sample graph nodes when live data is missing.
- Unsupported pipeline stages have labels and explanations but no buttons.
- State is never conveyed by color alone.

---

### Task 1: Add the isolated plugin build and typecheck path

**Files:**

- Modify: `web/package.json`
- Modify: `web/tsconfig.app.json`
- Modify: `package-lock.json` through npm
- Create: `plugins/evolution/dashboard/src/sdk.ts`
- Create: `plugins/evolution/dashboard/src/types.ts`
- Create: `plugins/evolution/dashboard/src/index.tsx`
- Create: `plugins/evolution/dashboard/dist/.gitkeep`

- [ ] **Step 1: Add exact dependencies**

Run:

```bash
npm install --workspace web cytoscape@^3.34.0
npm install --workspace web --save-dev esbuild@^0.28.1
```

Do not add a React dependency to the plugin.

- [ ] **Step 2: Add build scripts**

Extend `web/package.json`:

```json
{
  "scripts": {
    "build:evolution": "esbuild ../plugins/evolution/dashboard/src/index.tsx --bundle --format=iife --platform=browser --target=es2022 --jsx=transform --jsx-factory=React.createElement --jsx-fragment=React.Fragment --outfile=../plugins/evolution/dashboard/dist/index.js",
    "check:evolution": "npm run typecheck && npm run build:evolution && node --check ../plugins/evolution/dashboard/dist/index.js"
  }
}
```

Preserve all existing scripts.

- [ ] **Step 3: Include plugin source in dashboard typechecking**

Add `../plugins/evolution/dashboard/src` to `web/tsconfig.app.json`'s `include`.

- [ ] **Step 4: Define the SDK adapter**

`sdk.ts` must use type-only React imports and runtime SDK globals:

```typescript
import type * as ReactTypes from "react";

export interface EvolutionPluginSdk {
  React: typeof ReactTypes;
  hooks: {
    useState: typeof ReactTypes.useState;
    useEffect: typeof ReactTypes.useEffect;
    useCallback: typeof ReactTypes.useCallback;
    useMemo: typeof ReactTypes.useMemo;
    useRef: typeof ReactTypes.useRef;
  };
  fetchJSON<T>(path: string, init?: RequestInit): Promise<T>;
  components: {
    Badge: ReactTypes.ComponentType<ReactTypes.ComponentProps<"span">>;
    Button: ReactTypes.ComponentType<ReactTypes.ComponentProps<"button">>;
    Checkbox: ReactTypes.ComponentType<
      ReactTypes.ComponentProps<"button"> & {
        checked?: boolean;
        onCheckedChange?(checked: boolean): void;
      }
    >;
    Input: ReactTypes.ComponentType<ReactTypes.ComponentProps<"input">>;
    Label: ReactTypes.ComponentType<ReactTypes.ComponentProps<"label">>;
    Select: ReactTypes.ComponentType<ReactTypes.ComponentProps<"select">>;
    SelectOption: ReactTypes.ComponentType<ReactTypes.ComponentProps<"option">>;
    Separator: ReactTypes.ComponentType<ReactTypes.ComponentProps<"div">>;
  };
  utils: {
    cn(...values: unknown[]): string;
    timeAgo(value: string): string;
    isoTimeAgo(value: string): string;
  };
}

export const SDK = window.__HERMES_PLUGIN_SDK__ as EvolutionPluginSdk;
export const React = SDK.React;
```

If a host component's public props differ from the HTML primitive shown above,
derive its exact type from `web/src/plugins/sdk.d.ts`. The completed adapter
must contain no `any` and no suppression comment.

- [ ] **Step 5: Define API response types**

`types.ts` mirrors the Python schemas and uses discriminated unions:

```typescript
export type HealthState =
  | "missing"
  | "ready"
  | "partial"
  | "stale"
  | "blocked"
  | "corrupt";

export type EvolutionView = "overview" | "organism" | "telos" | "pipeline";

export interface EvolutionSnapshot {
  schema_version: 1;
  state: HealthState;
  observed_at: string;
  snapshot_digest: string;
  organism: { id_prefix: string; lineage_prefix: string } | null;
  gnothi: GnothiSummary;
  telos: TelosSummary;
  observer: ObserverSummary;
  generations: GenerationSummary;
  pipeline: PipelineSummary;
  diagnostics: string[];
}
```

Define all graph, node, edge, revision, Telos, pipeline, audit, job, and error interfaces explicitly.

- [ ] **Step 6: Register a minimal component**

`index.tsx` must:

```typescript
import { React } from "./sdk";

function EvolutionPlugin(): React.ReactElement {
  return <main className="evo-shell">Evolution</main>;
}

window.__HERMES_PLUGINS__.register("evolution", EvolutionPlugin);
```

- [ ] **Step 7: Build and verify no React runtime is bundled**

Run:

```bash
npm run check:evolution --workspace web
node --check plugins/evolution/dashboard/dist/index.js
rg -n "react-dom|react/jsx-runtime|__HERMES_SESSION_TOKEN__" \
  plugins/evolution/dashboard/dist/index.js
```

Expected: syntax succeeds and ripgrep returns no matches.

- [ ] **Step 8: Commit**

```bash
git add \
  web/package.json web/tsconfig.app.json package-lock.json \
  plugins/evolution/dashboard/src \
  plugins/evolution/dashboard/dist
git commit -m "build(evolution): add isolated dashboard plugin bundle"
```

---

### Task 2: Build the API client, snapshot store, and shared shell

**Files:**

- Create: `plugins/evolution/dashboard/src/api.ts`
- Create: `plugins/evolution/dashboard/src/state.ts`
- Create: `plugins/evolution/dashboard/src/view-model.ts`
- Create: `plugins/evolution/dashboard/src/components/EvolutionShell.tsx`
- Create: `plugins/evolution/dashboard/src/components/StatusRail.tsx`
- Modify: `plugins/evolution/dashboard/src/index.tsx`
- Create: `web/src/plugins/evolution-plugin.test.ts`

- [ ] **Step 1: Write view-model tests**

Test:

- `initialView()` returns `organism`;
- readiness blockers are priority-ordered;
- a refresh error retains the last good snapshot;
- `409` maps to refresh-required and is never retried automatically;
- profile facet changes never replace organism identity;
- missing/corrupt snapshots expose only valid actions.

- [ ] **Step 2: Add the typed API client**

Every method uses the sanctioned host helper:

```typescript
const BASE = "/api/plugins/evolution";

export const evolutionApi = {
  snapshot: () => SDK.fetchJSON<EvolutionSnapshot>(`${BASE}/snapshot`),
  graph: (query: URLSearchParams) =>
    SDK.fetchJSON<GraphResponse>(`${BASE}/graph?${query}`),
  mutate: <T>(path: string, body: unknown) =>
    SDK.fetchJSON<T>(`${BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
};
```

Implement every API-plan route with typed parameters and results.

- [ ] **Step 3: Add a narrow snapshot hook**

`useEvolutionSnapshot()` owns:

- initial loading;
- manual refresh;
- 30-second passive polling while the tab is visible;
- last-valid snapshot retention;
- current warning;
- current background job polling.

It must not own graph filters, forms, dialogs, or view navigation.

- [ ] **Step 4: Build the shared shell**

The shell renders:

- `Evolution`;
- `Local organism · all profiles`;
- organism and lineage prefixes;
- internal nav buttons for Overview, Organism, Telos, Pipeline;
- `StatusRail`;
- non-destructive warning banner;
- active job strip.

Use `aria-current="page"` for the selected internal view and real `<button>` elements.

- [ ] **Step 5: Run tests and build**

```bash
npm run test --workspace web -- --run src/plugins/evolution-plugin.test.ts
npm run check:evolution --workspace web
```

- [ ] **Step 6: Commit**

```bash
git add plugins/evolution/dashboard/src web/src/plugins/evolution-plugin.test.ts plugins/evolution/dashboard/dist/index.js
git commit -m "feat(evolution): add control center shell and state"
```

---

### Task 3: Implement the functional Organism graph and list parity

**Files:**

- Create: `plugins/evolution/dashboard/src/graph-model.ts`
- Create: `plugins/evolution/dashboard/src/components/OrganismView.tsx`
- Create: `plugins/evolution/dashboard/src/components/OrganismGraph.tsx`
- Create: `plugins/evolution/dashboard/src/components/OrganismList.tsx`
- Create: `plugins/evolution/dashboard/src/components/NodeInspector.tsx`
- Create: `plugins/evolution/dashboard/src/components/RevisionDialog.tsx`
- Create: `web/src/plugins/evolution-graph.test.ts`

- [ ] **Step 1: Write pure graph-model tests**

Test:

- node/edge conversion preserves stable IDs;
- edge styles distinguish `provides`, `requires`, `depends_on`;
- state class names distinguish healthy, degraded, stale, missing, unknown;
- filtering never emits a dangling edge;
- keyboard neighbor selection is deterministic;
- truncation notice appears whenever `truncated` is true;
- graph and list receive identical filtered node/edge arrays.

- [ ] **Step 2: Create and destroy Cytoscape correctly**

`OrganismGraph` creates one instance in an effect and destroys it on cleanup:

```typescript
const cy = cytoscape({
  container: containerRef.current,
  elements,
  layout: { name: "cose", animate: false, fit: true, padding: 28 },
  style: graphStyles,
  minZoom: 0.25,
  maxZoom: 2.5,
  wheelSensitivity: 0.2,
});
return () => cy.destroy();
```

Node tap/select calls `onSelect(node.id())`. Do not store a Cytoscape object in React state.

- [ ] **Step 3: Add keyboard operations**

The graph container is focusable and labelled. Support:

- arrow keys: select deterministic adjacent node;
- Enter: focus/open inspector;
- `+` / `-`: zoom;
- `0`: fit;
- Escape: clear selection.

Render a visible keyboard-help line. The list toggle is always present; keyboard users are never forced to use the canvas.

- [ ] **Step 4: Build graph/list filters**

Controls:

- search stable ID or label;
- multi-toggle kinds: capability, runtime, invariant, skill, plugin, provider;
- graph/list toggle;
- fit;
- reset filters.

Selected-node inspector shows the exact design fields, capped evidence refs, dependencies, dependents, blockers, and affected capabilities.

- [ ] **Step 5: Add rebuild and comparison flows**

- `Rebuild organism` opens a dialog with optional collector selection and current snapshot digest.
- Submitting returns a job and closes only after `202`.
- `Compare revisions` selects two immutable revisions and displays semantic diff.
- `Export wiki` uses an authenticated blob endpoint only if Plan 2 implemented it; otherwise omit the button in v1 rather than presenting a fake action.

- [ ] **Step 6: Handle truthful states**

- missing: text + initialize/rebuild action, no graph instance;
- partial/stale: graph remains visible with banner and unknown domains;
- blocked/corrupt: hide unsafe detail and disable mutations;
- refresh failure: retain last graph behind warning;
- truncated: persistent visible marker with expansion guidance.

- [ ] **Step 7: Run tests and build**

```bash
npm run test --workspace web -- --run \
  src/plugins/evolution-plugin.test.ts \
  src/plugins/evolution-graph.test.ts
npm run check:evolution --workspace web
```

- [ ] **Step 8: Commit**

```bash
git add plugins/evolution/dashboard/src web/src/plugins/evolution-graph.test.ts plugins/evolution/dashboard/dist/index.js
git commit -m "feat(evolution): render interactive organism explorer"
```

---

### Task 4: Implement Overview readiness and Observer controls

**Files:**

- Create: `plugins/evolution/dashboard/src/components/OverviewView.tsx`
- Create: `plugins/evolution/dashboard/src/components/ReadinessSummary.tsx`
- Create: `plugins/evolution/dashboard/src/components/AuditTimeline.tsx`
- Modify: `web/src/plugins/evolution-plugin.test.ts`

- [ ] **Step 1: Add rendering behavior tests**

Assert:

- blockers are ordered and link to the correct internal view;
- coverage rows use text and icon in addition to color;
- pause/resume labels match current Observer state;
- scan starts a job and exposes progress;
- one primary action is shown;
- corrupt state exposes diagnostics but no mutation buttons.

- [ ] **Step 2: Build the view**

Render one readiness statement, blocker list, domain coverage, Observer/Telos readiness, eligible suggestion count, pending decisions, and bounded audit history. Avoid a generic metric-card grid.

- [ ] **Step 3: Wire Observer mutations**

Pause/resume requests carry:

```typescript
{
  organism_id: fullOrganismIdFromMutationContext,
  expected_snapshot_digest: snapshot.snapshot_digest,
  enabled: boolean
}
```

Load the full UUID from Plan 2's authenticated `/mutation-context` endpoint
immediately before opening a mutation dialog; never reconstruct it from the
displayed prefix. On success refresh once. On `409`, refresh and show “State
changed; review before retrying.”

- [ ] **Step 4: Run tests and build**

```bash
npm run test --workspace web -- --run src/plugins/evolution-plugin.test.ts
npm run check:evolution --workspace web
```

- [ ] **Step 5: Commit**

```bash
git add plugins/evolution/dashboard/src web/src/plugins/evolution-plugin.test.ts plugins/evolution/dashboard/dist/index.js
git commit -m "feat(evolution): add readiness and observer controls"
```

---

### Task 5: Implement Telos history, editor, diff, and strong confirmation

**Files:**

- Create: `plugins/evolution/dashboard/src/telos-model.ts`
- Create: `plugins/evolution/dashboard/src/components/TelosView.tsx`
- Create: `plugins/evolution/dashboard/src/components/TelosEditor.tsx`
- Create: `plugins/evolution/dashboard/src/components/TelosDiff.tsx`
- Create: `plugins/evolution/dashboard/src/components/StrongConfirmationDialog.tsx`
- Create: `web/src/plugins/evolution-telos.test.ts`

- [ ] **Step 1: Write model and interaction tests**

Test:

- editor serializes every `TelosRevision` field;
- parent digest is the selected base revision;
- save creates an inert revision and does not change active digest;
- semantic diff groups additions/removals/changes;
- dialog shows organism, current digest, target digest, action, and consequences;
- submit remains disabled until the exact server-provided phrase matches;
- stale confirmation closes, refreshes, and does not retry;
- activation and rollback use the same confirmation component with different consequences.

- [ ] **Step 2: Build structured Telos rendering**

Render purpose, desired traits, capability directions, priorities, tradeoffs, prohibitions, proactivity policy, success indicators, revision parent, and digest.

- [ ] **Step 3: Build the editor**

Use bounded repeated-field inputs matching the Python contract limits. Do not accept raw arbitrary JSON as the primary editor. Show field-level errors from local validation and server `422`.

- [ ] **Step 4: Wire two-step confirmation**

1. POST prepare with target/current/snapshot digest.
2. Render the returned exact phrase and expiry.
3. POST confirm with `confirmation_id` and typed phrase.
4. Refresh on success; never auto-repeat.

Do not expose pending context secrets or call the generic Telos store directly.

- [ ] **Step 5: Run tests and build**

```bash
npm run test --workspace web -- --run src/plugins/evolution-telos.test.ts
npm run check:evolution --workspace web
```

- [ ] **Step 6: Commit**

```bash
git add plugins/evolution/dashboard/src web/src/plugins/evolution-telos.test.ts plugins/evolution/dashboard/dist/index.js
git commit -m "feat(evolution): add governed telos workspace"
```

---

### Task 6: Implement Pipeline with real and unavailable stages

**Files:**

- Create: `plugins/evolution/dashboard/src/pipeline-model.ts`
- Create: `plugins/evolution/dashboard/src/components/PipelineView.tsx`
- Create: `plugins/evolution/dashboard/src/components/PipelineStages.tsx`
- Create: `plugins/evolution/dashboard/src/components/SuggestionInspector.tsx`
- Create: `plugins/evolution/dashboard/src/components/BlueprintInspector.tsx`
- Create: `web/src/plugins/evolution-pipeline.test.ts`

- [ ] **Step 1: Write stage/action tests**

Assert:

- stages are always ordered Suggestion → Research → Blueprint → Build → Canary → Promotion → Stable;
- unavailable stages have `aria-disabled`, explanatory text, and no button;
- eligible suggestions expose blueprint creation;
- ineligible suggestions explain the failed gate;
- repeated blueprint creation returns/displays the same immutable blueprint;
- research has no permission dialog and creates no authorization call;
- audit rows are append-only ordered.

- [ ] **Step 2: Build attempt selection and inspectors**

Render score, evidence facts, Telos alignment, research references/local summaries, blueprint component classes, requested scope, and authorization history. Cap all long text visually and provide explicit expansion.

- [ ] **Step 3: Add public research handoff**

The `Research public documentation` action:

1. builds a sanitized brief from public suggestion fields only;
2. copies the brief to clipboard;
3. navigates to `/chat`;
4. shows “Research brief copied — paste it in Chat.”

It must not send raw evidence, paths, logs, memory, prompts, private source, or organism artifacts. It must not call an evolution authorization endpoint.

- [ ] **Step 4: Wire blueprint creation**

Send the suggestion ID plus organism/snapshot/suggestion digests. On success display the returned existing-or-created blueprint. On conflict refresh and require review.

- [ ] **Step 5: Run tests and build**

```bash
npm run test --workspace web -- --run src/plugins/evolution-pipeline.test.ts
npm run check:evolution --workspace web
```

- [ ] **Step 6: Commit**

```bash
git add plugins/evolution/dashboard/src web/src/plugins/evolution-pipeline.test.ts plugins/evolution/dashboard/dist/index.js
git commit -m "feat(evolution): add governed evolution pipeline"
```

---

### Task 7: Apply the approved visual system and responsive inspector

**Files:**

- Create: `plugins/evolution/dashboard/dist/style.css`
- Modify: all Evolution component files only where semantic class names are needed
- Create: `web/src/plugins/evolution-accessibility.test.ts`

- [ ] **Step 1: Add accessibility structure tests**

Assert:

- one page-level heading;
- internal nav is labelled;
- all controls have accessible names;
- graph has list fallback;
- dialogs have title/description and focusable cancel;
- selected states use `aria-current`, `aria-selected`, or checked state;
- no action is a clickable `<div>`;
- status text accompanies every status class.

- [ ] **Step 2: Implement visual tokens**

Use plugin-scoped CSS variables:

```css
.evo-shell {
  --evo-bg: #0b1110;
  --evo-panel: #101817;
  --evo-line: rgba(195, 232, 217, 0.18);
  --evo-text: #d9f2e7;
  --evo-muted: #8ca79d;
  --evo-mint: #9ee6c5;
  --evo-amber: #e7b86a;
  --evo-red: #e27b72;
}
```

Match the selected concept: square corners, thin borders, technical typography, restrained accents, minimal elevation, one continuous graph surface.

- [ ] **Step 3: Add responsive behavior**

At desktop width, graph and inspector use a two-column layout with a stable inspector width. At compact widths, inspector becomes a focus-managed drawer. Internal tabs remain horizontally scrollable and labelled; content must not disappear.

- [ ] **Step 4: Respect motion and contrast preferences**

Disable nonessential transitions under `prefers-reduced-motion`. Confirm focus outlines and text contrast. Graph layout animation remains off.

- [ ] **Step 5: Run UI tests and production build**

```bash
npm run test --workspace web -- --run \
  src/plugins/evolution-plugin.test.ts \
  src/plugins/evolution-graph.test.ts \
  src/plugins/evolution-telos.test.ts \
  src/plugins/evolution-pipeline.test.ts \
  src/plugins/evolution-accessibility.test.ts
npm run check:evolution --workspace web
npm run build --workspace web
./scripts/run_tests.sh tests/plugins/test_plugin_dashboard_auth_contract.py -q
```

- [ ] **Step 6: Commit**

```bash
git add \
  plugins/evolution/dashboard/src \
  plugins/evolution/dashboard/dist \
  web/src/plugins/evolution-accessibility.test.ts
git commit -m "style(evolution): match organism control center design"
```

---

### Task 8: Run the UI regression gate

- [ ] **Step 1: Verify bundles and dependency isolation**

```bash
npm run check:evolution --workspace web
node --check plugins/evolution/dashboard/dist/index.js
rg -n "react-dom|react/jsx-runtime|__HERMES_SESSION_TOKEN__" \
  plugins/evolution/dashboard/dist/index.js
```

Expected: no forbidden matches.

- [ ] **Step 2: Run all frontend tests**

```bash
npm run test --workspace web -- --run \
  src/plugins/evolution-plugin.test.ts \
  src/plugins/evolution-graph.test.ts \
  src/plugins/evolution-telos.test.ts \
  src/plugins/evolution-pipeline.test.ts \
  src/plugins/evolution-accessibility.test.ts
```

- [ ] **Step 3: Verify repository formatting**

```bash
git diff --check
git status --short
```
