import { evolutionApi } from "../api";
import { filterGraph, graphPresentation, truncationNotice } from "../graph-model";
import { React, SDK } from "../sdk";
import type { EvolutionJob, EvolutionSnapshot, GraphResponse, MutationContext } from "../types";
import { NodeInspector } from "./NodeInspector";
import { OrganismGraph } from "./OrganismGraph";
import { OrganismList } from "./OrganismList";
import { RevisionDialog, type RevisionDialogMode } from "./RevisionDialog";

void React;

const FILTER_KINDS = ["capability", "runtime", "invariant", "skill", "plugin", "provider"] as const;
const GRAPH_NEIGHBORHOOD_DEPTH = 2;
const GRAPH_RESPONSE_LIMIT = 200;
const API_FILTER_KINDS: ReadonlySet<string> = new Set(FILTER_KINDS);

type OrganismSurface = "graph" | "list";

export interface OrganismViewProps {
  snapshot: EvolutionSnapshot | null;
  onRefresh(): Promise<void>;
  onTrackJob(job: EvolutionJob): void;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "The requested local organism data is unavailable.";
}

function stateNotice(snapshot: EvolutionSnapshot): string | null {
  if (snapshot.state === "partial") return "Some local collector domains are incomplete or unknown. Displayed graph data remains bounded to the current immutable revision.";
  if (snapshot.state === "stale") return "This organism revision is stale. Inspect the last verified graph and rebuild when ready.";
  return null;
}

export function OrganismView({ snapshot, onRefresh, onTrackJob }: OrganismViewProps): React.ReactElement {
  const { useCallback, useEffect, useMemo, useState } = SDK.hooks;
  const [graph, setGraph] = useState<GraphResponse | null>(null);
  const [graphLoading, setGraphLoading] = useState(false);
  const [graphError, setGraphError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [kinds, setKinds] = useState<Set<string>>(new Set());
  const [surface, setSurface] = useState<OrganismSurface>("graph");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [graphRootId, setGraphRootId] = useState<string | null>(null);
  const [dialog, setDialog] = useState<RevisionDialogMode | null>(null);
  const [mutationContext, setMutationContext] = useState<MutationContext | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [initializing, setInitializing] = useState(false);
  const dialogTriggerRef = SDK.hooks.useRef<HTMLElement | null>(null);
  const inspectorTriggerRef = SDK.hooks.useRef<HTMLElement | null>(null);

  const gnothiIsUnsafe = snapshot?.gnothi.state === "corrupt";
  const hasSafeGraph = snapshot !== null
    && snapshot.state !== "missing"
    && snapshot.state !== "blocked"
    && snapshot.state !== "corrupt"
    && !gnothiIsUnsafe
    && snapshot.gnothi.revision_id !== null;

  const expectedRevision = snapshot?.gnothi.revision_id ?? undefined;
  const requestedKinds = useMemo(
    () => [...kinds].filter(kind => API_FILTER_KINDS.has(kind)).sort(),
    [kinds],
  );

  useEffect(() => {
    if (!hasSafeGraph || snapshot === null) {
      setGraph(null);
      return;
    }
    let current = true;
    setGraphLoading(true);
    setGraphError(null);
    void evolutionApi.graph({
      rootId: graphRootId ?? undefined,
      depth: graphRootId === null ? undefined : GRAPH_NEIGHBORHOOD_DEPTH,
      limit: GRAPH_RESPONSE_LIMIT,
      kinds: requestedKinds,
      search: search === "" ? undefined : search,
      expectedRevision,
    }).then(next => {
      if (!current) return;
      setGraph(next);
    }).catch(nextError => {
      if (!current) return;
      setGraphError(errorMessage(nextError));
    }).finally(() => {
      if (current) setGraphLoading(false);
    });
    return () => { current = false; };
  }, [expectedRevision, graphRootId, hasSafeGraph, requestedKinds, search]);

  const toggleKind = useCallback((kind: string) => {
    setKinds(previous => {
      const next = new Set(previous);
      if (next.has(kind)) next.delete(kind);
      else next.add(kind);
      return next;
    });
  }, []);

  const filters = useMemo(() => ({ kinds, search }), [kinds, search]);
  const presentation = useMemo(
    () => graph === null ? null : graphPresentation(graph, filters),
    [filters, graph],
  );
  const selectedNode = presentation?.graph.nodes.find(node => node.id === selectedId) ?? null;

  const resetFilters = useCallback(() => {
    setSearch("");
    setKinds(new Set());
    setGraphRootId(null);
    setSelectedId(null);
    setInspectorOpen(false);
  }, []);

  const expandSelectedNeighborhood = useCallback(() => {
    if (selectedId !== null) setGraphRootId(selectedId);
  }, [selectedId]);

  const openRebuild = useCallback(async () => {
    setActionError(null);
    try {
      const context = await evolutionApi.mutationContext();
      setMutationContext(context);
      setDialog("rebuild");
    } catch (error) {
      setActionError(errorMessage(error));
    }
  }, []);

  const initialize = useCallback(async () => {
    if (initializing) return;
    setInitializing(true);
    setActionError(null);
    try {
      await evolutionApi.initialize();
      await onRefresh();
    } catch (error) {
      setActionError(errorMessage(error));
    } finally {
      setInitializing(false);
    }
  }, [initializing, onRefresh]);

  const openInspector = useCallback((id: string) => {
    if (typeof document !== "undefined" && document.activeElement instanceof HTMLElement) {
      inspectorTriggerRef.current = document.activeElement;
    }
    setSelectedId(id);
    setInspectorOpen(true);
    if (typeof document !== "undefined") {
      globalThis.setTimeout(() => document.getElementById("evo-node-inspector")?.focus(), 0);
    }
  }, []);

  const closeInspector = useCallback(() => {
    setInspectorOpen(false);
    const trigger = inspectorTriggerRef.current;
    if (trigger !== null && typeof document !== "undefined" && document.contains(trigger)) trigger.focus();
  }, []);

  const onJobStarted = useCallback((job: EvolutionJob) => {
    onTrackJob(job);
    void onRefresh();
  }, [onRefresh, onTrackJob]);

  if (snapshot === null) {
    return <section className="evo-organism" aria-busy="true"><p>Loading local organism status…</p></section>;
  }

  if (snapshot.state === "missing" && snapshot.organism === null) {
    return (
      <section className="evo-organism evo-organism--missing">
        <h2>Organism graph is not initialized</h2>
        <p>No local organism revision exists yet. No sample graph is shown.</p>
        {actionError !== null ? <p role="alert">{actionError}</p> : null}
        <button type="button" onClick={() => void initialize()} disabled={initializing}>
          {initializing ? "Initializing…" : "Initialize local organism"}
        </button>
        <p>After initialization, rebuild the organism to publish its first immutable graph revision.</p>
      </section>
    );
  }

  if (snapshot.gnothi.state === "missing") {
    return (
      <section className="evo-organism evo-organism--missing">
        <h2>Organism graph is not initialized</h2>
        <p>The local organism exists, but it has no immutable graph revision yet.</p>
        {actionError !== null ? <p role="alert">{actionError}</p> : null}
        <button
          type="button"
          onClick={event => {
            dialogTriggerRef.current = event.currentTarget;
            void openRebuild();
          }}
        >
          Rebuild organism
        </button>
        <p>Queue a local rebuild to publish the first immutable graph revision.</p>
        {dialog === "rebuild" ? (
          <RevisionDialog
            mode="rebuild"
            context={mutationContext}
            onClose={() => setDialog(null)}
            onJobStarted={onJobStarted}
            returnFocusRef={dialogTriggerRef}
          />
        ) : null}
      </section>
    );
  }

  if (snapshot.state === "blocked" || snapshot.state === "corrupt" || gnothiIsUnsafe) {
    return (
      <section className="evo-organism evo-organism--unavailable" aria-live="polite">
        <h2>Organism details are unavailable</h2>
        <p>{snapshot.state === "corrupt" || snapshot.gnothi.state === "corrupt"
          ? "Unsafe organism details and mutations are hidden because local validation failed."
          : "The local organism is blocked. Details and mutations remain disabled until the blocker is resolved."}</p>
        {snapshot.diagnostics.length > 0 ? <ul>{snapshot.diagnostics.slice(0, 12).map(diagnostic => <li key={diagnostic}>{diagnostic}</li>)}</ul> : null}
      </section>
    );
  }

  const stateWarning = stateNotice(snapshot);
  const incompleteNotice = graph === null ? null : truncationNotice(graph);
  const filtered = presentation?.graph ?? filterGraph({ nodes: [], edges: [] }, filters);

  return (
    <section className="evo-organism">
      <header className="evo-organism__header">
        <div>
          <h2>Organism</h2>
          <p>Revision {snapshot.gnothi.revision_id ?? "unavailable"} · {snapshot.gnothi.node_count ?? 0} nodes · {snapshot.gnothi.edge_count ?? 0} edges</p>
        </div>
        <div className="evo-organism__actions">
          <button type="button" onClick={event => { dialogTriggerRef.current = event.currentTarget; void openRebuild(); }}>Rebuild organism</button>
          <button type="button" onClick={event => { dialogTriggerRef.current = event.currentTarget; setDialog("compare"); }}>Compare revisions</button>
        </div>
      </header>
      {stateWarning !== null ? <p className={`evo-organism__notice evo-organism__notice--${snapshot.state}`}>{stateWarning}</p> : null}
      {graphError !== null ? <p className="evo-organism__notice" role="status">The last valid graph remains visible. {graphError}</p> : null}
      {incompleteNotice !== null ? <p className="evo-organism__notice evo-organism__notice--truncated">{incompleteNotice}</p> : null}
      {actionError !== null ? <p role="alert">{actionError}</p> : null}
      <section className="evo-organism__filters" aria-label="Organism graph filters">
        <label>
          Search stable ID or label
          <input value={search} onChange={event => setSearch(event.target.value)} placeholder="Filter graph" />
        </label>
        <fieldset>
          <legend>Node kinds</legend>
          {FILTER_KINDS.map(kind => (
            <label key={kind}>
              <input type="checkbox" checked={kinds.has(kind)} onChange={() => toggleKind(kind)} />
              {kind}
            </label>
          ))}
        </fieldset>
        <button type="button" onClick={resetFilters}>Reset filters and graph</button>
        <div className="evo-organism__surface-toggle" role="tablist" aria-label="Organism presentation">
          <button id="evo-organism-graph-tab" type="button" role="tab" aria-controls="evo-organism-graph-panel" aria-selected={surface === "graph"} onClick={() => setSurface("graph")}>Graph</button>
          <button id="evo-organism-list-tab" type="button" role="tab" aria-controls="evo-organism-list-panel" aria-selected={surface === "list"} onClick={() => setSurface("list")}>List</button>
        </div>
      </section>
      <section className="evo-organism__legend" aria-label="Relationship legend">
        <p>Provides: solid arrow · Requires: dotted arrow · Depends on: dashed arrow</p>
        <p>Health states: healthy, degraded, stale, missing, unknown</p>
      </section>
      {graphLoading && graph === null ? <p role="status">Loading the current immutable graph revision…</p> : null}
      {graph?.truncated && selectedNode !== null ? (
        <button type="button" onClick={expandSelectedNeighborhood} disabled={graphRootId === selectedNode.id}>
          {graphRootId === selectedNode.id ? "Showing selected neighborhood" : "Expand selected neighborhood"}
        </button>
      ) : null}
      {presentation !== null ? (
        <div className="evo-organism__surface">
          <div
            id={surface === "graph" ? "evo-organism-graph-panel" : "evo-organism-list-panel"}
            className="evo-organism__visualization"
            role="tabpanel"
            aria-labelledby={surface === "graph" ? "evo-organism-graph-tab" : "evo-organism-list-tab"}
          >
            {surface === "graph" ? (
              <OrganismGraph
                nodes={presentation.graph.nodes}
                edges={presentation.graph.edges}
                selectedId={selectedId}
                onSelect={setSelectedId}
                onOpenInspector={openInspector}
              />
            ) : (
              <OrganismList
                nodes={presentation.list.nodes}
                edges={presentation.list.edges}
                selectedId={selectedId}
                onSelect={openInspector}
              />
            )}
          </div>
          <NodeInspector
            node={selectedNode}
            nodes={filtered.nodes}
            edges={filtered.edges}
            blockers={graph?.blockers ?? []}
            drawerOpen={inspectorOpen}
            onClose={closeInspector}
          />
        </div>
      ) : null}
      {dialog !== null ? (
        <RevisionDialog
          mode={dialog}
          context={dialog === "rebuild" ? mutationContext : null}
          onClose={() => setDialog(null)}
          onJobStarted={onJobStarted}
          returnFocusRef={dialogTriggerRef}
        />
      ) : null}
    </section>
  );
}
