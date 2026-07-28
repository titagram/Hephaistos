import cytoscape from "cytoscape";
import { React, SDK } from "../sdk";
import { neighborId, nodeStateClass, toCytoscapeElements, type GraphArrowKey } from "../graph-model";
import type { GraphEdge, GraphNode } from "../types";

void React;

const graphStyles = [
  {
    selector: "node",
    style: {
      "background-color": "#0c1918",
      "border-color": "#a7f3d0",
      "border-width": 1,
      color: "#d1fae5",
      content: "data(label)",
      "font-size": 10,
      height: 36,
      label: "data(label)",
      shape: "rectangle",
      "text-halign": "center",
      "text-valign": "center",
      width: 110,
    },
  },
  {
    selector: ".evo-node--degraded",
    style: { "border-color": "#fbbf24" },
  },
  {
    selector: ".evo-node--stale",
    style: { "border-color": "#fb923c" },
  },
  {
    selector: ".evo-node--missing",
    style: { "border-color": "#f87171", "border-style": "dashed" },
  },
  {
    selector: ".evo-node--unknown",
    style: { "border-color": "#94a3b8", "border-style": "dotted" },
  },
  {
    selector: "edge",
    style: {
      "curve-style": "bezier",
      "line-color": "#a7f3d0",
      "target-arrow-color": "#a7f3d0",
      "target-arrow-shape": "triangle",
      width: 1,
    },
  },
  {
    selector: ".evo-edge--requires",
    style: { "line-style": "dotted" },
  },
  {
    selector: ".evo-edge--depends-on",
    style: { "line-style": "dashed" },
  },
] satisfies cytoscape.StylesheetJson;

export interface OrganismGraphProps {
  nodes: GraphNode[];
  edges: GraphEdge[];
  selectedId: string | null;
  onSelect(id: string | null): void;
  onOpenInspector(id: string): void;
}

function isGraphArrowKey(key: string): key is GraphArrowKey {
  return key === "ArrowUp" || key === "ArrowDown" || key === "ArrowLeft" || key === "ArrowRight";
}

function healthLabel(node: GraphNode): string {
  return nodeStateClass(node).replace("evo-node--", "");
}

export function OrganismGraph({
  nodes,
  edges,
  selectedId,
  onSelect,
  onOpenInspector,
}: OrganismGraphProps): React.ReactElement {
  const { useCallback, useEffect, useMemo, useRef } = SDK.hooks;
  const containerRef = useRef<HTMLDivElement | null>(null);
  const cyRef = useRef<cytoscape.Core | null>(null);
  const elements = useMemo(() => toCytoscapeElements({ nodes, edges }), [nodes, edges]);
  const selectedNode = nodes.find(node => node.id === selectedId) ?? null;

  useEffect(() => {
    if (containerRef.current === null) return;
    const cy = cytoscape({
      container: containerRef.current,
      elements,
      layout: { name: "cose", animate: false, fit: true, padding: 28 },
      style: graphStyles,
      minZoom: 0.25,
      maxZoom: 2.5,
      wheelSensitivity: 0.2,
    });
    cyRef.current = cy;
    const handleTap = (event: cytoscape.EventObject) => {
      const id = event.target.id();
      onSelect(id);
      onOpenInspector(id);
    };
    cy.on("tap", "node", handleTap);
    return () => {
      cy.off("tap", "node", handleTap);
      cy.destroy();
      if (cyRef.current === cy) cyRef.current = null;
    };
  }, [elements, onOpenInspector, onSelect]);

  useEffect(() => {
    if (selectedId === null) return;
    const cy = cyRef.current;
    if (cy === null) return;
    const selected = cy.getElementById(selectedId);
    if (selected.empty()) return;
    cy.elements().unselect();
    selected.select();
  }, [selectedId]);

  const fit = useCallback(() => {
    cyRef.current?.fit();
  }, []);

  const adjustZoom = useCallback((delta: number) => {
    const cy = cyRef.current;
    if (cy === null) return;
    cy.zoom(Math.max(0.25, Math.min(2.5, cy.zoom() + delta)));
    cy.center();
  }, []);

  const handleKeyDown = useCallback((event: React.KeyboardEvent<HTMLDivElement>) => {
    if (isGraphArrowKey(event.key)) {
      event.preventDefault();
      const next = neighborId({ nodes, edges }, selectedId, event.key);
      if (next !== null) onSelect(next);
      return;
    }
    if (event.key === "Enter" && selectedId !== null) {
      event.preventDefault();
      onOpenInspector(selectedId);
      return;
    }
    if (event.key === "+" || event.key === "=") {
      event.preventDefault();
      adjustZoom(0.15);
      return;
    }
    if (event.key === "-") {
      event.preventDefault();
      adjustZoom(-0.15);
      return;
    }
    if (event.key === "0") {
      event.preventDefault();
      fit();
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      cyRef.current?.elements().unselect();
      onSelect(null);
    }
  }, [adjustZoom, edges, fit, nodes, onOpenInspector, onSelect, selectedId]);

  return (
    <section className="evo-organism-graph" aria-label="Organism graph">
      <div className="evo-organism-graph__controls" aria-label="Graph controls">
        <button type="button" onClick={() => adjustZoom(0.15)} aria-label="Zoom in">+</button>
        <button type="button" onClick={() => adjustZoom(-0.15)} aria-label="Zoom out">−</button>
        <button type="button" onClick={fit}>Fit graph</button>
      </div>
      <div
        ref={containerRef}
        className="evo-organism-graph__canvas"
        role="application"
        tabIndex={0}
        aria-label="Interactive organism graph"
        aria-describedby="evo-organism-graph-keyboard-help"
        onKeyDown={handleKeyDown}
        style={{ minHeight: "20rem", width: "100%" }}
      />
      <p id="evo-organism-graph-keyboard-help" className="evo-organism-graph__keyboard-help">
        Use arrow keys to move between related nodes. Enter opens details. + and − zoom. 0 fits the graph. Escape clears selection.
      </p>
      <p className="evo-organism-graph__selection-status" role="status">
        {selectedNode === null ? "No graph node selected." : `Selected node ${selectedNode.label}. Health: ${healthLabel(selectedNode)}.`}
      </p>
    </section>
  );
}
