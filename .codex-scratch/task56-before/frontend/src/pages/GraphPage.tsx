import React, { useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api } from "@/api/devboardApi";
import { useApi } from "@/hooks/useApi";
import { PageHeader, MetricCard, Panel } from "@/components/devboard/Layout";
import { DataState } from "@/components/devboard/DataState";
import { RiskBadge, SourceMetaInline, Pill } from "@/components/devboard/Badges";
import { Button } from "@/components/ui/button";
import { GraphNodeKind, GraphView } from "@/types/devboard";
import { titleCase, relativeTime } from "@/lib/format";
import { Boxes, PlayCircle } from "lucide-react";

const KIND_COLOR: Record<GraphNodeKind, string> = {
  module: "#0ea5a3", file: "#64748b", class: "#7c5cff", function: "#f59e0b", route: "#38bdf8", model: "#34d399", service: "#fb7185", unknown: "#94a3b8",
};

function GraphCanvas({ g, selected, onSelect }: { g: GraphView; selected: string | null; onSelect: (id: string | null) => void }) {
  const size = 560;
  const pos = useMemo(() => {
    const cx = size / 2, cy = size / 2, r = size / 2 - 70;
    const m: Record<string, { x: number; y: number }> = {};
    g.nodes.forEach((n, i) => {
      const a = (i / g.nodes.length) * Math.PI * 2 - Math.PI / 2;
      m[n.id] = { x: cx + r * Math.cos(a), y: cy + r * Math.sin(a) };
    });
    return m;
  }, [g]);
  const connected = (id: string) => g.edges.filter((e) => e.from === id || e.to === id).flatMap((e) => [e.from, e.to]);
  const activeSet = selected ? new Set(connected(selected)) : null;

  return (
    <svg viewBox={`0 0 ${size} ${size}`} className="w-full" data-testid="graph-canvas" onClick={() => onSelect(null)}>
      {g.edges.map((e) => {
        const a = pos[e.from], b = pos[e.to];
        if (!a || !b) return null;
        const dim = selected && !(e.from === selected || e.to === selected);
        return <line key={e.id} x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="hsl(var(--border))" strokeWidth={dim ? 0.5 : 1.2} opacity={dim ? 0.25 : 0.9} />;
      })}
      {g.nodes.map((n) => {
        const p = pos[n.id];
        const dim = activeSet && !activeSet.has(n.id) && n.id !== selected;
        const rad = 6 + Math.min(n.degree, 7);
        return (
          <g key={n.id} transform={`translate(${p.x},${p.y})`} className="cursor-pointer" opacity={dim ? 0.3 : 1}
            onClick={(e) => { e.stopPropagation(); onSelect(n.id === selected ? null : n.id); }}>
            <circle r={rad} fill={KIND_COLOR[n.kind]} fillOpacity={0.2} stroke={KIND_COLOR[n.kind]} strokeWidth={n.id === selected ? 2.5 : 1.5} />
            <text textAnchor="middle" dy={rad + 11} className="fill-current text-foreground" fontSize="9" fontFamily="IBM Plex Mono">{n.label.length > 22 ? n.label.slice(0, 20) + "…" : n.label}</text>
          </g>
        );
      })}
    </svg>
  );
}

export default function GraphPage() {
  const { projectId } = useParams();
  const [params] = useSearchParams();
  const runId = params.get("run_id") || undefined;
  const snapshotId = params.get("snapshot_id") || undefined;
  const state = useApi(() => api.getGraph(projectId, { runId, snapshotId }), [projectId, runId, snapshotId]);
  const [selected, setSelected] = useState<string | null>(null);

  return (
    <div className="space-y-5" data-testid="graph-page">
      <PageHeader
        title="Graph"
        subtitle={projectId ? "Project code nodes and relationships summarized from imported artifacts." : "Code nodes and relationships summarized from imported artifacts."}
        meta={<div className="flex flex-wrap items-center gap-1.5">
          {projectId && <Link to={`/projects/${encodeURIComponent(projectId)}`} className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"><Boxes className="h-3.5 w-3.5" />Project {projectId}</Link>}
          {(runId || snapshotId) && <Pill tone="slate">Scoped to {runId ? `run ${runId}` : `snapshot ${snapshotId}`}</Pill>}
        </div>}
      />

      <DataState state={state}>
        {(g) => {
          const sel = g.nodes.find((n) => n.id === selected);
          const isPreview = g.nodes.length < g.stats.nodes || g.edges.length < g.stats.edges;
          if (g.stats.nodes === 0) {
            return (
              <>
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                  <MetricCard label="Nodes" value={0} />
                  <MetricCard label="Edges" value={0} />
                  <MetricCard label="Modules" value={0} />
                  <MetricCard label="Routes" value={0} />
                </div>
                <Panel title="No graph snapshot">
                  <div className="space-y-3 text-sm text-muted-foreground">
                    <p>This scope does not have an imported graph snapshot yet.</p>
                    {projectId && (
                      <Button size="sm" variant="outline" asChild>
                        <Link to={`/projects/${encodeURIComponent(projectId)}/runs`}><PlayCircle className="mr-1.5 h-3.5 w-3.5" /> Runs</Link>
                      </Button>
                    )}
                  </div>
                </Panel>
              </>
            );
          }
          return (
            <>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <MetricCard label="Nodes" value={g.stats.nodes} />
                <MetricCard label="Edges" value={g.stats.edges} />
                <MetricCard label="Modules" value={g.stats.modules} />
                <MetricCard label="Routes" value={g.stats.routes} />
              </div>

              <div className="rounded-md border border-border bg-card/40 px-4 py-2">
                <SourceMetaInline source={g.source} />
                <span className="ml-2 text-[11px] text-muted-foreground">· generated {relativeTime(g.generated_at)}</span>
                {isPreview && (
                  <span className="ml-2 text-[11px] text-muted-foreground">
                    · preview {g.nodes.length}/{g.stats.nodes} nodes, {g.edges.length}/{g.stats.edges} edges
                  </span>
                )}
              </div>

              <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
                <Panel title="Relationship map" className="lg:col-span-2">
                  <div className="mb-2 flex flex-wrap gap-2">
                    {(Object.keys(KIND_COLOR) as GraphNodeKind[]).map((k) => (
                      <span key={k} className="flex items-center gap-1 text-[11px] text-muted-foreground"><span className="h-2.5 w-2.5 rounded-full" style={{ background: KIND_COLOR[k] }} />{titleCase(k)}</span>
                    ))}
                  </div>
                  <GraphCanvas g={g} selected={selected} onSelect={setSelected} />
                  <p className="text-center text-[11px] text-muted-foreground">Click a node to highlight its relationships.</p>
                </Panel>

                <div className="space-y-4">
                  {sel ? (
                    <Panel title="Node detail">
                      <p className="font-mono text-sm font-semibold">{sel.label}</p>
                      <div className="mt-2 flex flex-wrap items-center gap-1.5"><Pill tone="teal">{titleCase(sel.kind)}</Pill><RiskBadge level={sel.risk} /><Pill tone="slate">degree {sel.degree}</Pill></div>
                      <p className="mt-2 text-xs text-muted-foreground">Repository: <span className="font-mono">{sel.repository}</span></p>
                      <div className="mt-2"><SourceMetaInline source={sel.source} /></div>
                      <div className="mt-3 border-t border-border pt-2">
                        <p className="mb-1 text-[11px] uppercase tracking-wide text-muted-foreground">Relationships</p>
                        <ul className="space-y-1 text-xs">
                          {g.edges.filter((e) => e.from === sel.id || e.to === sel.id).map((e) => (
                            <li key={e.id} className="font-mono text-muted-foreground">{e.from === sel.id ? "→" : "←"} {titleCase(e.kind)}: {e.from === sel.id ? e.to : e.from}</li>
                          ))}
                        </ul>
                      </div>
                    </Panel>
                  ) : (
                    <Panel title="Nodes" dense>
                      <ul className="max-h-[480px] divide-y divide-border/60 overflow-y-auto">
                        {[...g.nodes].sort((a, b) => b.degree - a.degree).map((n) => (
                          <li key={n.id}><button onClick={() => setSelected(n.id)} className="flex w-full items-center justify-between gap-2 px-4 py-2 text-left hover:bg-accent/50" data-testid={`graph-node-${n.id}`}>
                            <span className="flex items-center gap-2 truncate"><span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: KIND_COLOR[n.kind] }} /><span className="truncate font-mono text-xs">{n.label}</span></span>
                            <span className="flex items-center gap-1.5"><Pill tone="slate">{n.degree}</Pill></span>
                          </button></li>
                        ))}
                      </ul>
                    </Panel>
                  )}
                </div>
              </div>
            </>
          );
        }}
      </DataState>
    </div>
  );
}
