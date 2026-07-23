# Hades React Graph Explorer v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the technical, inert graph page with a lifecycle-first explorer that explains what an application does from an entrypoint, while retaining bounded element analysis and optional technical details.

**Architecture:** A thin `GraphPage` composes a feature-owned reducer and fetch-only hooks. The default flow bootstraps project scope, chooses an entrypoint, loads one bounded lifecycle backbone, and incrementally expands stages/branches without exceeding 200 canonical nodes. React Flow renders a controlled read-only canvas; an accessible tree exposes equivalent semantics. Technical IDs, raw counts, and advanced path comparison remain collapsed by default.

**Tech Stack:** React, TypeScript, Vitest, Testing Library, `@xyflow/react` 12.11.2, existing frontend API client/design system.

## Global Constraints

- Inherit `2026-07-16-graph-lifecycle-v2-master.md`.
- Work in Git repository `/home/ubuntu/dev-sandbox`; frontend files live under `frontend/`. Each task starts on a fresh checkpoint-recorded `codex/graph-v2-p4-frontend-task-N` branch from then-current clean, pulled `main`, after the accepted predecessor was reviewed, integrated, tested, and pushed. Do not revive the historical catch-all backend branch.
- Run every npm command from the Git root as `npm --prefix frontend ...`; paths passed to Vitest are relative to the frontend package and therefore start with `src/`, not `frontend/src/`.
- START GATE: Plan 2 dashboard golden responses are frozen and Plan 3 verification badges/status DTOs are available.
- Before Task 1, use the available React/frontend best-practices skill and read it completely.
- Inertia is absent and must remain absent.
- `GraphPage.tsx` owns routing/composition only; durable feature state lives in one reducer.
- Fetch hooks own no durable UI state.
- Every visible button/action has an interaction test and an actual effect.
- No lifecycle request is sent before valid project, scope, and entrypoint.
- Technical IDs, raw JSON, projection version, scope ULID, omission codes, and raw metric cards are invisible by default.

---

## File Structure

Create the exact feature tree from design section 13.1. Responsibilities:

- `GraphExplorer.tsx`: feature composition and reducer ownership.
- `GraphScopePicker.tsx`: authorized scope choice only.
- `GraphModeToggle.tsx`: follow entrypoint/analyze element.
- `EntrypointPicker.tsx`: debounced dynamic entrypoint search/filter.
- `LifecycleStageRail.tsx`: stage visibility/count knowledge.
- `LifecycleCanvas.tsx`: controlled React Flow nodes/edges/controls.
- `LifecycleAccessibleTree.tsx`: keyboard/text equivalent.
- `LifecycleNodeDrawer.tsx`: plain-language selected-node detail.
- `AsyncFlowsPanel.tsx`: linked async child flow selection.
- `ElementAnalysisPanel.tsx`: search/neighborhood/callers/dependencies/impact.
- `TechnicalGraphDetails.tsx`: raw counts/IDs and advanced connection comparison.
- `graphExplorerReducer.ts`: all durable state and stale-response guards.
- `lifecycleLayout.ts`: pure deterministic layout.
- `useGraphBootstrap.ts`, `useEntrypoints.ts`, `useLifecycle.ts`, `useElementAnalysis.ts`: fetch-only typed promises with `AbortSignal`.
- `graphCopy.ts`: all user-facing core copy.

Remove only after imports are migrated:

- `frontend/src/components/devboard/GraphExplorer.tsx`
- `frontend/src/pages/graphExplorerModel.ts`

No compatibility re-export remains.

---

### Task 1: Freeze TypeScript DTOs and API Error Semantics

**Files:**
- Modify: `frontend/src/types/devboard.ts`
- Modify: `frontend/src/lib/apiClient.ts`
- Modify: `frontend/src/lib/mockApi.ts`
- Create: `frontend/src/components/devboard/graph/__tests__/graphDtos.test.ts`

**Interfaces:**

```ts
export interface GraphApiResponse<T> {
  protocol_version: 'hades.dashboard_graph.v2'
  graph_context: string | null
  projection: ProjectionDto | null
  data: T
}

export interface CountKnowledge {
  represented: number
  value: number | null
  knowledge: 'exact' | 'absence_verified' | 'unknown'
  reason: CompletenessReasonCode | null
}

export type GraphApiError =
  | {
    code: 'graph_not_ready' | 'graph_context_stale' | 'graph_projection_failed' | 'graph_search_snapshot_expired'
    message: string
    retryable: true
    details: Record<string, never>
  }
  | {
    code: 'graph_scope_mismatch' | 'graph_entrypoint_not_found' | 'graph_query_invalid' | 'graph_projection_partial' | 'graph_handle_invalid' | 'graph_cursor_invalid'
    message: string
    retryable: false
    details: Record<string, never>
  }
  | {
    code: 'graph_maintenance'
    message: string
    retryable: true
    details: {
      reason: 'backup' | 'cutover' | 'retirement' | 'operator'
      retry_after_seconds: number
    }
  }

export type GraphErrorCode = GraphApiError['code']

export interface GraphApiErrorResponse {
  protocol_version: 'hades.dashboard_graph.v2'
  error: GraphApiError
}
```

HTTP status is transport metadata held by `apiClient`; it is never parsed as a property of `GraphApiErrorResponse`. Tests enforce the schema's exact code→HTTP→retryable→details mapping, including the closed maintenance details object, and reject an invented alias or property.

- [ ] **Step 1: Add RED golden DTO tests**

Parse every response in `contracts/hades/graph-v2/golden/dashboard-protocol.json`; reject v1 protocol, unknown properties, null projection outside scopes, false zero for unknown, `lower_bound`/`exact_value` aliases, and malformed handle/context. Parse every error vector against the exact closed error envelope and stable transport-status table from design section 10.6. Import the canonicalization subset used by browser handles/cursors and assert TypeScript canonical bytes/digests equal the root vectors, completing the TypeScript side of G13.

- [ ] **Step 2: Run RED**

Run: `npm --prefix frontend test -- --run src/components/devboard/graph/__tests__/graphDtos.test.ts`

- [ ] **Step 3: Implement closed DTO decoders and one API call**

```ts
export async function queryProjectGraph<T>(
  projectId: string,
  body: DashboardGraphRequest,
  signal: AbortSignal,
): Promise<GraphApiResponse<T>> {
  return apiClient.post(`/projects/${projectId}/graph/query`, body, { signal })
}
```

Map backend error envelopes to `GraphApiError`; do not replace 409 with empty data.

- [ ] **Step 4: Run GREEN and commit**

```bash
npm --prefix frontend test -- --run src/components/devboard/graph/__tests__/graphDtos.test.ts
git add frontend/src/types/devboard.ts frontend/src/lib/apiClient.ts frontend/src/lib/mockApi.ts frontend/src/components/devboard/graph/__tests__/graphDtos.test.ts
git commit -m "feat(frontend): type graph explorer protocol v2"
```

### Task 2: Implement the Reducer and Request-Generation Guards

**Files:**
- Create: `frontend/src/components/devboard/graph/graphExplorerReducer.ts`
- Create: `frontend/src/components/devboard/graph/__tests__/graphExplorerReducer.test.ts`

**Interfaces:**

```ts
export interface GraphExplorerState {
  projectId: string | null
  scopes: ScopeDto[]
  scope: ScopeDto | null
  requestGeneration: number
  graphContext: string | null
  projectionKey: string | null
  mode: 'follow_entrypoint' | 'analyze_element'
  entrypoint: EntrypointDto | null
  entrypointFilters: EntrypointFilters
  baseLifecycle: LifecycleDto | null
  expansions: Record<string, LifecycleExpansionState>
  visibleStages: Set<LifecycleStage>
  selectedNode: LifecycleNodeDto | null
  drawer: 'closed' | 'node'
  viewport: ViewportState
  requests: Record<string, GraphRequestState>
  staleReloadAttempted: boolean
}
```

- [ ] **Step 1: Add RED transition/race tests**

Test project/scope/entrypoint/mode reset boundaries, generation increments, bootstrap context adoption only from null, contextual triple equality guard, one automatic stale reload, second stale error visible, late/aborted response ignored, clean bootstrap resets stale flag only after projection stored, selected node/expansion LRU protection, and URL-handle clear action.

- [ ] **Step 2: Run RED**

Run: `npm --prefix frontend test -- --run src/components/devboard/graph/__tests__/graphExplorerReducer.test.ts`

- [ ] **Step 3: Implement a closed action union**

```ts
export type GraphExplorerAction =
  | { type: 'projectChanged'; projectId: string | null }
  | { type: 'scopesLoaded'; generation: number; scopes: ScopeDto[] }
  | { type: 'scopeSelected'; scope: ScopeDto }
  | { type: 'contextAdopted'; generation: number; context: string; projectionKey: string }
  | { type: 'entrypointSelected'; entrypoint: EntrypointDto }
  | { type: 'responseAccepted'; key: string; generation: number; requestedContext: string | null; returnedContext: string | null; payload: GraphPayload }
  | { type: 'responseFailed'; key: string; generation: number; error: GraphApiError }
  | { type: 'staleContextDetected'; generation: number; error: GraphApiError }
  | { type: 'stageVisibilityChanged'; stage: LifecycleStage; visible: boolean }
  | { type: 'expansionUsed'; selector: string; counter: number }
  | { type: 'resetView' }
```

Implement all state families from design section 13.4.1; do not add one global error field.

- [ ] **Step 4: Run GREEN and commit**

```bash
npm --prefix frontend test -- --run src/components/devboard/graph/__tests__/graphExplorerReducer.test.ts
git add frontend/src/components/devboard/graph/graphExplorerReducer.ts frontend/src/components/devboard/graph/__tests__/graphExplorerReducer.test.ts
git commit -m "feat(frontend): guard graph explorer state generations"
```

### Task 3: Implement Bootstrap, Scope Selection, and Default Mode

**Files:**
- Create: `frontend/src/components/devboard/graph/useGraphBootstrap.ts`
- Create: `frontend/src/components/devboard/graph/GraphScopePicker.tsx`
- Create: `frontend/src/components/devboard/graph/GraphModeToggle.tsx`
- Create: `frontend/src/components/devboard/graph/graphCopy.ts`
- Create: `frontend/src/components/devboard/graph/__tests__/useGraphBootstrap.test.tsx`
- Create: `frontend/src/components/devboard/graph/__tests__/GraphScopePicker.test.tsx`

**Interfaces:**
- Storage key: `hades.graph.scope.${projectId}`.
- Modes: `follow_entrypoint|analyze_element`.
- Bootstrap order: scopes → valid scope → overview without context → adopt context → entrypoints with exact context.

- [ ] **Step 1: Add RED state-machine tests**

Test zero/one/multiple scopes, unauthorized saved scope ignored, no scope-bound request before choice, overview and entrypoints not raced, recommended mode mapping, project/scope resets user toggle, full no-entrypoints switches to analysis, partial empty stays follow with explanation, and scope ULID hidden.

- [ ] **Step 2: Run RED**

Run: `npm --prefix frontend test -- --run src/components/devboard/graph/__tests__/useGraphBootstrap.test.tsx src/components/devboard/graph/__tests__/GraphScopePicker.test.tsx`

- [ ] **Step 3: Implement exact copy and bootstrap**

```ts
export const graphCopy = {
  title: 'Explore how this application works',
  httpPurpose: 'Choose an endpoint to see what can happen from request to response.',
  nonHttpPurpose: 'Choose an entry point to follow its execution from start to finish.',
  lifecycleMode: 'Follow an entry point',
  analysisMode: 'Analyze an element',
  stageSidebar: 'Show lifecycle stages',
  partialBadge: 'Some paths may be missing',
  unknownCount: 'Unknown — this part of the graph is incomplete',
  verifiedEmpty: 'No matching relationships were found in the complete indexed scope.',
  staleReload: 'The graph changed while you were exploring it. We reloaded the latest version.',
  collapsed: 'More nodes are available. Expand this section to inspect them.',
  maintenance: 'The graph explorer is being upgraded. Other project features remain available.',
} as const
```

- [ ] **Step 4: Run GREEN and commit**

```bash
npm --prefix frontend test -- --run src/components/devboard/graph/__tests__/useGraphBootstrap.test.tsx src/components/devboard/graph/__tests__/GraphScopePicker.test.tsx
git add -- frontend/src/components/devboard/graph/useGraphBootstrap.ts frontend/src/components/devboard/graph/GraphScopePicker.tsx frontend/src/components/devboard/graph/GraphModeToggle.tsx frontend/src/components/devboard/graph/graphCopy.ts frontend/src/components/devboard/graph/__tests__/useGraphBootstrap.test.tsx frontend/src/components/devboard/graph/__tests__/GraphScopePicker.test.tsx
git commit -m "feat(frontend): bootstrap graph scope and exploration mode"
```

### Task 4: Implement Entrypoint Discovery and Selection

**Files:**
- Create: `frontend/src/components/devboard/graph/useEntrypoints.ts`
- Create: `frontend/src/components/devboard/graph/EntrypointPicker.tsx`
- Create: `frontend/src/components/devboard/graph/__tests__/EntrypointPicker.test.tsx`

**Interfaces:**

```ts
export function fetchEntrypoints(
  input: { projectId: string; graphContext: string; filters: EntrypointFilters; cursor?: string },
  signal: AbortSignal,
): Promise<GraphApiResponse<EntrypointPageDto>>
```

- [ ] **Step 1: Add RED discovery tests**

Test 250ms NFC/collapsed-whitespace debounce, obsolete abort, empty query cancellation, kind filters, HTTP `METHOD /path` label with name/handler secondary, non-HTTP kind/name label, no lifecycle before selection, selected compact bar + Change, opaque URL handle only, stale/invalid handle removed via `history.replaceState`, Load more cursor/context guard.

- [ ] **Step 2: Run RED**

Run: `npm --prefix frontend test -- --run src/components/devboard/graph/__tests__/EntrypointPicker.test.tsx`

- [ ] **Step 3: Implement fetch-only hook/component, run GREEN, commit**

```bash
npm --prefix frontend test -- --run src/components/devboard/graph/__tests__/EntrypointPicker.test.tsx
git add frontend/src/components/devboard/graph/useEntrypoints.ts frontend/src/components/devboard/graph/EntrypointPicker.tsx frontend/src/components/devboard/graph/__tests__/EntrypointPicker.test.tsx
git commit -m "feat(frontend): select dynamic graph entrypoints"
```

### Task 5: Implement Deterministic Lifecycle Layout and 200-Node LRU

**Files:**
- Create: `frontend/src/components/devboard/graph/lifecycleLayout.ts`
- Create: `frontend/src/components/devboard/graph/__tests__/lifecycleLayout.test.ts`

**Interfaces:**

```ts
export interface HiddenStageSummary {
  kind: 'hidden_stage'
  stage: LifecycleStage
  count: CountKnowledge
  sourceHandles: string[]
  targetHandles: string[]
}

export function layoutLifecycle(
  nodes: readonly LifecycleNodeDto[],
  prior: ReadonlyMap<string, XYPosition>,
): ReadonlyMap<string, XYPosition>

export function planExpansionEvictions(
  state: GraphExplorerState,
  requestedLimit: number,
): { evictSelectors: string[]; requestLimit: number; blockedReason: string | null }
```

- [ ] **Step 1: Add RED golden layout tests**

Assert stage x=`ordinal*420`; node 240x72; y=`min_depth*128+(laneSeed+lane_ordinal)*88`; 16px collision padding; 88px downward displacement; `order_key` processing; prior positions unchanged; no label/random dependence; hidden connector sorted handles/count label; base/selected/requested expansions protected; LRU eviction; request limit `min(100,remaining)`; canonical count <=200.

- [ ] **Step 2: Run RED**

Run: `npm --prefix frontend test -- --run src/components/devboard/graph/__tests__/lifecycleLayout.test.ts`

- [ ] **Step 3: Implement pure functions, run GREEN, commit**

Lane seeds are exactly `backbone=0,alternative=1,exception=2,loop=3,async=4`. Collapsing removes only that expansion's positions; reset removes every non-base position.

```bash
npm --prefix frontend test -- --run src/components/devboard/graph/__tests__/lifecycleLayout.test.ts
git add frontend/src/components/devboard/graph/lifecycleLayout.ts frontend/src/components/devboard/graph/__tests__/lifecycleLayout.test.ts
git commit -m "feat(frontend): lay out bounded lifecycle graphs deterministically"
```

### Task 6: Render the Stage Rail, Canvas, and Accessible Tree

**Files:**
- Create: `frontend/src/components/devboard/graph/LifecycleStageRail.tsx`
- Create: `frontend/src/components/devboard/graph/LifecycleCanvas.tsx`
- Create: `frontend/src/components/devboard/graph/LifecycleAccessibleTree.tsx`
- Create: `frontend/src/components/devboard/graph/__tests__/LifecycleCanvas.test.tsx`
- Create: `frontend/src/components/devboard/graph/__tests__/LifecycleAccessibleTree.test.tsx`
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`

**Interfaces:**
- `@xyflow/react@12.11.2` exactly.
- Read-only controlled canvas; no drag/connect/delete.
- Zoom 0.2–1.8; fit padding 0.15; minimap >=1024px; viewport 640 desktop/520 mobile.

- [ ] **Step 1: Add RED interaction/accessibility tests**

Test stage full-empty omitted, unknown `?`, toggle hides/reveals with truthful connector, never-expanded recheck calls expand once, node opens drawer callback, branch text labels, loop marker, async dotted boundary, distinct terminal text/icons, reset behavior, zoom/reset controls, keyboard node buttons/tree order, text-equivalent edges/branches, reduced motion, minimap breakpoint, and no drag/connect/delete.

- [ ] **Step 2: Run RED**

Run: `npm --prefix frontend test -- --run src/components/devboard/graph/__tests__/LifecycleCanvas.test.tsx src/components/devboard/graph/__tests__/LifecycleAccessibleTree.test.tsx`

- [ ] **Step 3: Add dependency and implement rendering**

Import `@xyflow/react/dist/style.css` exactly once in `LifecycleCanvas.tsx`. Use `smoothstep`: mandatory solid, alternatives labelled, exceptions dashed, async dotted, loops marked, unresolved ends at warning boundary. Every distinction has text/icon in addition to color.

- [ ] **Step 4: Run GREEN and commit**

```bash
npm --prefix frontend test -- --run src/components/devboard/graph/__tests__/LifecycleCanvas.test.tsx src/components/devboard/graph/__tests__/LifecycleAccessibleTree.test.tsx
git add -- frontend/package.json frontend/package-lock.json frontend/src/components/devboard/graph/LifecycleStageRail.tsx frontend/src/components/devboard/graph/LifecycleCanvas.tsx frontend/src/components/devboard/graph/LifecycleAccessibleTree.tsx frontend/src/components/devboard/graph/__tests__/LifecycleCanvas.test.tsx frontend/src/components/devboard/graph/__tests__/LifecycleAccessibleTree.test.tsx
git commit -m "feat(frontend): render accessible lifecycle canvas"
```

### Task 7: Implement Lifecycle Fetch, Expansion, Async Flows, and Node Drawer

**Files:**
- Create: `frontend/src/components/devboard/graph/useLifecycle.ts`
- Create: `frontend/src/components/devboard/graph/AsyncFlowsPanel.tsx`
- Create: `frontend/src/components/devboard/graph/LifecycleNodeDrawer.tsx`
- Create: `frontend/src/components/devboard/graph/__tests__/useLifecycle.test.tsx`
- Create: `frontend/src/components/devboard/graph/__tests__/LifecycleNodeDrawer.test.tsx`

**Interfaces:**
- Fetch functions accept explicit DTO inputs + `AbortSignal`, return typed promises, own no UI state.
- Expansion response applies only with matching generation/context/cursor/selector.

- [ ] **Step 1: Add RED fetch/detail tests**

Test lifecycle only after selection; initial backbone <=120; stage/branch/node/load-more selector; request capacity enforcement; stale expansion ignored; async child selected separately; main flow stops visually at dispatch; drawer plain role/kind/qualified name/source/relationships/branch/effects/integrations/evidence/uncertainty; advanced IDs collapsed; focus enters and returns to node.

- [ ] **Step 2: Run RED**

Run: `npm --prefix frontend test -- --run src/components/devboard/graph/__tests__/useLifecycle.test.tsx src/components/devboard/graph/__tests__/LifecycleNodeDrawer.test.tsx`

- [ ] **Step 3: Implement, run GREEN, commit**

```bash
npm --prefix frontend test -- --run src/components/devboard/graph/__tests__/useLifecycle.test.tsx src/components/devboard/graph/__tests__/LifecycleNodeDrawer.test.tsx
git add -- frontend/src/components/devboard/graph/useLifecycle.ts frontend/src/components/devboard/graph/AsyncFlowsPanel.tsx frontend/src/components/devboard/graph/LifecycleNodeDrawer.tsx frontend/src/components/devboard/graph/__tests__/useLifecycle.test.tsx frontend/src/components/devboard/graph/__tests__/LifecycleNodeDrawer.test.tsx
git commit -m "feat(frontend): inspect lifecycle branches and node evidence"
```

### Task 8: Implement Analyze an Element and Optional Technical Comparison

**Files:**
- Create: `frontend/src/components/devboard/graph/useElementAnalysis.ts`
- Create: `frontend/src/components/devboard/graph/ElementAnalysisPanel.tsx`
- Create: `frontend/src/components/devboard/graph/TechnicalGraphDetails.tsx`
- Create: `frontend/src/components/devboard/graph/__tests__/ElementAnalysisPanel.test.tsx`

**Interfaces:**
- Search examples adapt to route/class/function/file/plain-language intent.
- Result shows match basis.
- Selection loads bounded neighborhood, callers, dependencies/callees, impact, and entrypoint lifecycle memberships.
- Advanced `Compare connection to…` alone calls `path`.

- [ ] **Step 1: Add RED semantic/interaction tests**

Test route URI and named symbol search, match explanation, no false zero, verified empty copy, family unknown copy, primary view has no Find path, technical disclosure hidden by default, compare picker required before path request, path success/no-path/unknown/error states, technical raw counters/IDs available only after expansion.

- [ ] **Step 2: Run RED**

Run: `npm --prefix frontend test -- --run src/components/devboard/graph/__tests__/ElementAnalysisPanel.test.tsx`

- [ ] **Step 3: Implement, run GREEN, commit**

```bash
npm --prefix frontend test -- --run src/components/devboard/graph/__tests__/ElementAnalysisPanel.test.tsx
git add frontend/src/components/devboard/graph/useElementAnalysis.ts frontend/src/components/devboard/graph/ElementAnalysisPanel.tsx frontend/src/components/devboard/graph/TechnicalGraphDetails.tsx frontend/src/components/devboard/graph/__tests__/ElementAnalysisPanel.test.tsx
git commit -m "feat(frontend): analyze bounded graph elements honestly"
```

### Task 9: Compose Graph Explorer and Remove the Legacy Explorer

**Files:**
- Create: `frontend/src/components/devboard/graph/GraphExplorer.tsx`
- Create: `frontend/src/components/devboard/graph/GraphMaintenanceScreen.tsx`
- Modify: `frontend/src/pages/GraphPage.tsx`
- Create: `frontend/src/components/devboard/graph/__tests__/GraphExplorer.test.tsx`
- Modify: `frontend/src/pages/__tests__/GraphPage.test.tsx`
- Modify: `frontend/src/pages/__tests__/GraphPageProjectTransition.test.tsx`
- Remove: `frontend/src/components/devboard/GraphExplorer.tsx`
- Remove: `frontend/src/pages/graphExplorerModel.ts`

**Interfaces:**
- Visible order is exactly title/purpose, mode, picker, selected header, stage+canvas work area, async panel, technical disclosure.

- [ ] **Step 1: Add RED composition/state tests**

Test plain purpose/default mode, project transition abort/clear, scope transition, selected header badges, partial usable persistent warning, stable loading skeleton/no zero flash, full no-entrypoint analysis switch, partial empty explanation, stale 409 one reload notice, second stale error, projection failure retry/previous-ready, all visible controls functional, no old raw metric hierarchy, no black screen on API error, and `VITE_GRAPH_V2_MAINTENANCE=true` rendering only the maintenance screen with zero graph request.

- [ ] **Step 2: Run RED**

Run: `npm --prefix frontend test -- --run src/components/devboard/graph/__tests__/GraphExplorer.test.tsx src/pages/__tests__/GraphPage.test.tsx src/pages/__tests__/GraphPageProjectTransition.test.tsx`

- [ ] **Step 3: Compose thin page and delete legacy files**

```tsx
function ActiveGraphPage() {
  const project = useSelectedProject()
  return <GraphExplorer projectId={project?.id ?? null} />
}

export function GraphPage() {
  if (import.meta.env.VITE_GRAPH_V2_MAINTENANCE === 'true') {
    return <GraphMaintenanceScreen message={graphCopy.maintenance} />
  }
  return <ActiveGraphPage />
}
```

Search all imports before deletion; no compatibility re-export.

- [ ] **Step 4: Run GREEN and commit**

```bash
npm --prefix frontend test -- --run src/components/devboard/graph/__tests__/GraphExplorer.test.tsx src/pages/__tests__/GraphPage.test.tsx src/pages/__tests__/GraphPageProjectTransition.test.tsx
git add -- frontend/src/components/devboard/graph/GraphExplorer.tsx frontend/src/components/devboard/graph/GraphMaintenanceScreen.tsx frontend/src/pages/GraphPage.tsx frontend/src/components/devboard/graph/__tests__/GraphExplorer.test.tsx frontend/src/pages/__tests__/GraphPage.test.tsx frontend/src/pages/__tests__/GraphPageProjectTransition.test.tsx frontend/src/components/devboard/GraphExplorer.tsx frontend/src/pages/graphExplorerModel.ts
git diff --cached --name-only
git commit -m "feat(frontend): replace graph page with lifecycle explorer"
```

The staged-name output must equal the eight Task 9 paths above (including deleted paths) and contain nothing else; otherwise unstage the unrelated path and stop before commit.

### Task 10: Close U01–U12, Build, and Responsive/Accessibility QA

**Files:**
- Modify only the nine exact mapped frontend test files below when acceptance coverage is missing; production code/styles return to their owning Task 3–9 branch and are never repaired under Task 10
- Create: `.codex-artifacts/graph-v2/frontend-gates.json`

- [ ] **Step 1: Map every frontend gate to an exact test node**

```json
{
  "U01": "frontend/src/pages/__tests__/GraphPage.test.tsx::uses_recommended_mode_and_plain_purpose",
  "U02": "frontend/src/pages/__tests__/GraphPageProjectTransition.test.tsx::does_not_query_lifecycle_before_project_scope_entrypoint",
  "U03": "frontend/src/components/devboard/graph/__tests__/GraphExplorer.test.tsx::route_selection_renders_backbone_stages_branches_terminals_async",
  "U04": "frontend/src/components/devboard/graph/__tests__/useLifecycle.test.tsx::expansions_send_current_context_and_reject_stale",
  "U05": "frontend/src/components/devboard/graph/__tests__/ElementAnalysisPanel.test.tsx::unknown_is_never_zero_and_partial_stays_visible",
  "U06": "frontend/src/components/devboard/graph/__tests__/ElementAnalysisPanel.test.tsx::path_is_advanced_and_distinguishes_all_states",
  "U07": "frontend/src/components/devboard/graph/__tests__/GraphExplorer.test.tsx::technical_ids_and_raw_metrics_are_collapsed",
  "U08": "frontend/src/components/devboard/graph/__tests__/LifecycleCanvas.test.tsx::canvas_never_exceeds_200_canonical_nodes",
  "U09": "frontend/src/components/devboard/graph/__tests__/GraphExplorer.test.tsx::every_visible_action_has_an_effect",
  "U10": "frontend/src/components/devboard/graph/__tests__/LifecycleAccessibleTree.test.tsx::keyboard_focus_text_equivalent_mobile_and_motion_accessibility",
  "U11": "frontend/src/components/devboard/graph/__tests__/graphExplorerReducer.test.ts::changes_abort_and_late_responses_cannot_mutate_state",
  "U12": "frontend/src/components/devboard/graph/__tests__/lifecycleLayout.test.ts::golden_layout_is_stable_and_lru_preserves_coordinates"
}
```

- [ ] **Step 2: Run complete feature tests**

```bash
npm --prefix frontend test -- --run \
  src/components/devboard/graph/__tests__ \
  src/pages/__tests__/GraphPage.test.tsx \
  src/pages/__tests__/GraphPageProjectTransition.test.tsx
```

Expected: PASS with zero skipped U01–U12 assertions.

- [ ] **Step 3: Run static/build checks**

```bash
npm --prefix frontend run typecheck
npm --prefix frontend run lint
npm --prefix frontend run build
```

Expected: all PASS; bundle has one React Flow CSS import and no Inertia dependency/import.

- [ ] **Step 4: Run browser matrix**

Use authenticated local/staging backend golden data. Verify widths 1440, 1024, 767, 390; light/dark themes; keyboard-only; reduced motion; empty/partial/stale/error/loading; drawer bottom sheet under 768; console has zero uncaught error/warning caused by Graph Explorer.

- [ ] **Step 5: Verify every action registry**

Maintain a test table with action → handler effect:

```ts
const actions = [
  'change entrypoint', 'toggle mode', 'toggle stage', 'expand summary',
  'select node', 'select async flow', 'load more', 'reset view', 'retry request',
  'toggle technical details', 'choose compare target', 'compare connection', 'close drawer',
] as const
```

Each action must have at least one test that observes state, API call, focus, or viewport change. No console-only handler passes.

- [ ] **Step 6: Record, review, commit**

```bash
git diff --check
git add -- frontend/src/pages/__tests__/GraphPage.test.tsx frontend/src/pages/__tests__/GraphPageProjectTransition.test.tsx frontend/src/components/devboard/graph/__tests__/GraphExplorer.test.tsx frontend/src/components/devboard/graph/__tests__/useLifecycle.test.tsx frontend/src/components/devboard/graph/__tests__/ElementAnalysisPanel.test.tsx frontend/src/components/devboard/graph/__tests__/LifecycleCanvas.test.tsx frontend/src/components/devboard/graph/__tests__/LifecycleAccessibleTree.test.tsx frontend/src/components/devboard/graph/__tests__/graphExplorerReducer.test.ts frontend/src/components/devboard/graph/__tests__/lifecycleLayout.test.ts .codex-artifacts/graph-v2/frontend-gates.json
git diff --cached --name-only
git commit -m "test(frontend): close graph explorer UX gates"
```

## Plan 4 Exit Gate

- U01–U12 are green.
- `/graph` explains its purpose before selection.
- Route lifecycle displays stages, branches, terminals, and async links from backend v2 data.
- Unknown is never zero; technical detail is optional and collapsed.
- No primary Find path button or inert control remains.
- Canvas never stores/renders more than 200 canonical nodes.
- Keyboard/text equivalent, focus return, mobile bottom sheet, reduced motion, and AA contrast pass.
- Production build succeeds and no legacy explorer/Inertia import remains.
