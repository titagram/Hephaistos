import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Panel } from "@/components/devboard/Layout";
import {
  DashboardGraphDataResponse,
  DashboardGraphDirection,
  DashboardGraphQueryRequest,
  DashboardGraphResponse,
  DashboardGraphScopeItem,
  DashboardGraphScopeType,
} from "@/types/devboard";
import { buildGraphViewModel } from "../../pages/graphExplorerModel";

interface GraphExplorerProps {
  projectId: string;
  scopes: readonly DashboardGraphScopeItem[];
  queryGraph: (request: DashboardGraphQueryRequest) => Promise<DashboardGraphResponse>;
  projectionUnavailable?: boolean;
  initialScopeType?: DashboardGraphScopeType;
  initialScopeId?: string;
  initialSymbol?: string;
  onQueryParamsChange?: (values: { scope_type?: DashboardGraphScopeType; scope_id?: string; symbol?: string }) => void;
  onRetry?: () => void;
}

interface DetailBundle {
  detail: DashboardGraphDataResponse;
  incoming: DashboardGraphDataResponse;
  outgoing: DashboardGraphDataResponse;
  impact: DashboardGraphDataResponse;
}

function dataResponse(response: DashboardGraphResponse): DashboardGraphDataResponse {
  if (response.query_type === "scopes") throw new Error("Unexpected scope response");
  return response;
}

function scopeKey(scope: Pick<DashboardGraphScopeItem, "source_scope_type" | "source_scope_id">): string {
  return `${scope.source_scope_type}:${scope.source_scope_id}`;
}

function responseStatus(response: DashboardGraphDataResponse | null): React.ReactNode {
  if (!response) return null;
  return (
    <p className="text-[11px] text-muted-foreground">
      returned {response.returned}
      {response.truncated ? " · truncated" : ""}
      {response.has_more ? ` · more available${response.next_cursor ? ` · cursor ${response.next_cursor}` : ""}` : ""}
    </p>
  );
}

function NodeList({ title, response }: { title: string; response: DashboardGraphDataResponse | null }) {
  return (
    <Panel title={title}>
      {response && response.items.length > 0 ? (
        <ul className="space-y-2 text-sm">
          {response.items.map((item) => (
            <li key={item.handle} className="rounded border border-border/60 p-2">
              <span className="font-mono">{item.label ?? item.handle}</span>
              {item.why && <p className="text-xs text-muted-foreground">{item.why}</p>}
              {(item.family || item.edge_types?.length || item.distance !== undefined) && (
                <p className="text-[11px] text-muted-foreground">
                  {[item.family, item.edge_types?.join(", "), item.distance === undefined ? null : `distance ${item.distance}`].filter(Boolean).join(" · ")}
                </p>
              )}
            </li>
          ))}
        </ul>
      ) : <p className="text-sm text-muted-foreground">No related symbols</p>}
      {responseStatus(response)}
    </Panel>
  );
}

function CompactGraph({ response, selectedHandle }: { response: DashboardGraphDataResponse; selectedHandle: string }) {
  const model = useMemo(() => buildGraphViewModel(response, selectedHandle), [response, selectedHandle]);
  const positions = useMemo(() => {
    const result = new Map<string, { x: number; y: number }>();
    model.visibleNodes.forEach((node, index) => {
      const angle = (index / Math.max(model.visibleNodes.length, 1)) * Math.PI * 2;
      result.set(node.handle, { x: 160 + Math.cos(angle) * 110, y: 110 + Math.sin(angle) * 75 });
    });
    return result;
  }, [model.visibleNodes]);
  const visibleHandles = useMemo(() => new Set(model.visibleNodes.map((node) => node.handle)), [model.visibleNodes]);
  const renderableEdges = useMemo(
    () => model.selectedEdges.filter((edge) => visibleHandles.has(edge.from_handle) && visibleHandles.has(edge.to_handle)),
    [model.selectedEdges, visibleHandles],
  );

  if (model.visibleNodes.length === 0) return null;
  return (
    <svg viewBox="0 0 320 220" className="max-h-64 w-full" aria-label="Selected relationship graph">
      {renderableEdges.map((edge) => {
        const from = positions.get(edge.from_handle);
        const to = positions.get(edge.to_handle);
        if (!from || !to) return null;
        return <line key={`${edge.from_handle}:${edge.to_handle}:${edge.edge_type}`} x1={from.x} y1={from.y} x2={to.x} y2={to.y} stroke="currentColor" opacity="0.35" />;
      })}
      {model.visibleNodes.map((node) => {
        const position = positions.get(node.handle)!;
        return (
          <g key={node.handle} transform={`translate(${position.x},${position.y})`}>
            <circle r={node.handle === selectedHandle ? 13 : 9} fill={node.handle === selectedHandle ? "#7c5cff" : "#0ea5a3"} />
            <text y="22" textAnchor="middle" fontSize="9" className="fill-current">{(node.label ?? node.handle).slice(0, 24)}</text>
          </g>
        );
      })}
    </svg>
  );
}

export default function GraphExplorer({
  projectId,
  scopes,
  queryGraph,
  projectionUnavailable = false,
  initialScopeType,
  initialScopeId,
  initialSymbol,
  onQueryParamsChange,
  onRetry,
}: GraphExplorerProps) {
  const initialScope = initialScopeType && initialScopeId
    ? scopes.find((scope) => scope.source_scope_type === initialScopeType && scope.source_scope_id === initialScopeId)
    : undefined;
  const [selectedScopeKey, setSelectedScopeKey] = useState(() => initialScope
    ? scopeKey(initialScope)
    : scopes.length === 1 ? scopeKey(scopes[0]) : "");
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState<DashboardGraphDataResponse | null>(null);
  const [selectedHandle, setSelectedHandle] = useState<string | null>(initialSymbol ?? null);
  const [details, setDetails] = useState<DetailBundle | null>(null);
  const [path, setPath] = useState<DashboardGraphDataResponse | null>(null);
  const [pathTarget, setPathTarget] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selectedScope = useMemo(
    () => scopes.find((scope) => scopeKey(scope) === selectedScopeKey),
    [scopes, selectedScopeKey],
  );
  const scopedRequest = useCallback((request: DashboardGraphQueryRequest): DashboardGraphQueryRequest => ({
    ...request,
    ...(selectedScope ? {
      scope_type: selectedScope.source_scope_type,
      scope_id: selectedScope.source_scope_id,
    } : {}),
  }), [selectedScope]);

  useEffect(() => {
    if (scopes.length === 1 && !selectedScopeKey) setSelectedScopeKey(scopeKey(scopes[0]));
  }, [scopes, selectedScopeKey]);

  useEffect(() => {
    if (!selectedScope || query.trim() === "") {
      setSearch(null);
      return;
    }
    const timer = window.setTimeout(() => {
      setLoading(true);
      setError(null);
      queryGraph(scopedRequest({ type: "search", query: query.trim(), limit: 50 }))
        .then((response) => setSearch(dataResponse(response)))
        .catch(() => setError("Unable to search graph"))
        .finally(() => setLoading(false));
    }, 250);
    return () => window.clearTimeout(timer);
  }, [projectId, query, queryGraph, scopedRequest, selectedScope]);

  const loadDetails = useCallback(async (handle: string) => {
    setSelectedHandle(handle);
    setLoading(true);
    setError(null);
    setDetails(null);
    setPath(null);
    onQueryParamsChange?.({
      scope_type: selectedScope?.source_scope_type,
      scope_id: selectedScope?.source_scope_id,
      symbol: handle,
    });
    try {
      const families = ["call", "dependency"] as const;
      const [detailResponse, incomingResponse, outgoingResponse, impactResponse] = await Promise.all([
        queryGraph(scopedRequest({ type: "detail", node_handle: handle, limit: 1 })),
        queryGraph(scopedRequest({ type: "neighborhood", node_handle: handle, direction: "in", families: [...families], limit: 50 })),
        queryGraph(scopedRequest({ type: "neighborhood", node_handle: handle, direction: "out", families: [...families], limit: 50 })),
        queryGraph(scopedRequest({ type: "impact", node_handle: handle, max_depth: 2, limit: 50 })),
      ]);
      setDetails({
        detail: dataResponse(detailResponse),
        incoming: dataResponse(incomingResponse),
        outgoing: dataResponse(outgoingResponse),
        impact: dataResponse(impactResponse),
      });
    } catch {
      setError("Unable to load graph details");
    } finally {
      setLoading(false);
    }
  }, [onQueryParamsChange, queryGraph, scopedRequest, selectedScope]);

  const initialLoadKey = useRef<string | null>(null);
  useEffect(() => {
    if (!initialSymbol || !selectedScope) return;
    const key = `${scopeKey(selectedScope)}:${initialSymbol}`;
    if (initialLoadKey.current === key) return;
    initialLoadKey.current = key;
    void loadDetails(initialSymbol);
  }, [initialSymbol, loadDetails, selectedScope]);

  const selectScope = (key: string) => {
    setSelectedScopeKey(key);
    setSearch(null);
    setDetails(null);
    setSelectedHandle(null);
    const scope = scopes.find((candidate) => scopeKey(candidate) === key);
    onQueryParamsChange?.({ scope_type: scope?.source_scope_type, scope_id: scope?.source_scope_id, symbol: undefined });
  };

  const runPath = async () => {
    if (!selectedHandle || !pathTarget) return;
    setLoading(true);
    setError(null);
    try {
      setPath(dataResponse(await queryGraph(scopedRequest({
        type: "path", from_handle: selectedHandle, to_handle: pathTarget, max_depth: 3, limit: 50,
      }))));
    } catch {
      setError("Unable to find a path");
    } finally {
      setLoading(false);
    }
  };

  if (projectionUnavailable) {
    return <div role="status" aria-live="polite" className="rounded border border-border p-4">
      Graph projection unavailable
      {onRetry && <Button className="ml-2" size="sm" variant="outline" onClick={onRetry}>Retry</Button>}
    </div>;
  }

  return (
    <div className="space-y-4" data-testid="graph-explorer">
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-sm">Graph scope
          <select aria-label="Graph scope" value={selectedScopeKey} onChange={(event) => selectScope(event.target.value)} className="mt-1 block w-full rounded border border-input bg-background px-3 py-2">
            {scopes.length > 1 && <option value="">Choose a scope</option>}
            {scopes.map((scope) => <option key={scopeKey(scope)} value={scopeKey(scope)} label={`${scope.source_scope_type}: ${scope.source_scope_id}`} />)}
          </select>
        </label>
        <label className="text-sm">Search symbols
          <Input aria-label="Search symbols" value={query} disabled={!selectedScope} onChange={(event) => setQuery(event.target.value)} placeholder={selectedScope ? "Class, method, route…" : "Choose a scope first"} />
        </label>
      </div>

      <div role="status" aria-live="polite" className="text-sm text-muted-foreground">
        {loading ? "Loading graph…" : error ?? ""}
        {error && selectedHandle && <Button className="ml-2" size="sm" variant="outline" onClick={() => void loadDetails(selectedHandle)}>Retry</Button>}
      </div>

      {search && (
        <Panel title="Search results">
          {search.items.length === 0 ? <p>No matching symbols</p> : (
            <div className="flex flex-wrap gap-2">
              {search.items.map((item) => <Button key={item.handle} variant="outline" onClick={() => void loadDetails(item.handle)}>{item.label ?? item.handle}</Button>)}
            </div>
          )}
          {responseStatus(search)}
        </Panel>
      )}

      {details && selectedHandle && (
        <>
          <div className="rounded border border-border bg-card/40 px-4 py-2 text-[11px] text-muted-foreground">
            {details.detail.source.type} · {details.detail.source.status} · {details.detail.source.origin}
            {details.detail.projection.quality && ` · ${details.detail.projection.quality}`}
          </div>
          <Panel title="Symbol">
            <p className="font-mono font-semibold">{details.detail.node?.label ?? selectedHandle}</p>
            <p className="text-xs text-muted-foreground">opaque handle: {selectedHandle}</p>
            <CompactGraph
              selectedHandle={selectedHandle}
              response={{
                ...details.detail,
                items: [
                  ...(details.detail.node ? [details.detail.node] : []),
                  ...details.incoming.items,
                  ...details.outgoing.items,
                ],
                edges: [...details.incoming.edges, ...details.outgoing.edges],
              }}
            />
          </Panel>
          <div className="grid gap-4 lg:grid-cols-3">
            <NodeList title="Callers" response={details.incoming} />
            <NodeList title="Dependencies/Callees" response={details.outgoing} />
            <NodeList title="Impact" response={details.impact} />
          </div>
          <Panel title="Path">
            <div className="flex flex-wrap gap-2">
              <select aria-label="Path target" value={pathTarget} onChange={(event) => setPathTarget(event.target.value)} className="rounded border border-input bg-background px-3 py-2">
                <option value="">Choose target</option>
                {(search?.items ?? []).filter((item) => item.handle !== selectedHandle).map((item) => <option key={item.handle} value={item.handle} label={item.label ?? item.handle} />)}
              </select>
              <Button disabled={!pathTarget} onClick={() => void runPath()}>Find path</Button>
            </div>
            {path && (path.items.length > 0 ? <CompactGraph response={path} selectedHandle={selectedHandle} /> : <p>No path found</p>)}
            {responseStatus(path)}
          </Panel>
        </>
      )}
    </div>
  );
}
