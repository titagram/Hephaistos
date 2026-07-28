import type { GraphEdge, GraphNode, GraphResponse } from "./types";

export type GraphArrowKey = "ArrowUp" | "ArrowDown" | "ArrowLeft" | "ArrowRight";

export interface GraphFilters {
  kinds: ReadonlySet<string>;
  search: string;
}

export interface FilteredGraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface CytoscapeNodeElement {
  data: { id: string; label: string; kind: string };
  classes: string;
}

export interface CytoscapeEdgeElement {
  data: { id: string; source: string; target: string };
  classes: string;
}

export interface CytoscapeElements {
  nodes: CytoscapeNodeElement[];
  edges: CytoscapeEdgeElement[];
}

export interface GraphPresentation {
  graph: FilteredGraph;
  list: FilteredGraph;
}

const EMPTY_FILTERS: GraphFilters = { kinds: new Set(), search: "" };

export function edgeStyleClass(kind: string): string {
  switch (kind) {
    case "provides":
      return "evo-edge--provides";
    case "requires":
      return "evo-edge--requires";
    case "depends_on":
      return "evo-edge--depends-on";
    default:
      return "evo-edge--unknown";
  }
}

export function nodeStateClass(node: GraphNode): string {
  const state = node.state;
  if (state.missing === true || state.available === false || state.installed === false) {
    return "evo-node--missing";
  }
  if (state.stale === true) return "evo-node--stale";
  if (state.degraded === true) return "evo-node--degraded";
  if (state.available === true || state.installed === true || state.verified === true) {
    return "evo-node--healthy";
  }
  return "evo-node--unknown";
}

function matchesNode(node: GraphNode, filters: GraphFilters): boolean {
  if (filters.kinds.size > 0 && !filters.kinds.has(node.kind)) return false;
  const search = filters.search.trim().toLocaleLowerCase();
  return search === "" || node.id.toLocaleLowerCase().includes(search) || node.label.toLocaleLowerCase().includes(search);
}

export function filterGraph(
  graph: Pick<GraphResponse, "nodes" | "edges">,
  filters: GraphFilters = EMPTY_FILTERS,
): FilteredGraph {
  const nodes = graph.nodes.filter(node => matchesNode(node, filters));
  const ids = new Set(nodes.map(node => node.id));
  const edges = graph.edges.filter(edge => ids.has(edge.from) && ids.has(edge.to));
  return { nodes, edges };
}

export function graphPresentation(
  graph: Pick<GraphResponse, "nodes" | "edges">,
  filters: GraphFilters = EMPTY_FILTERS,
): GraphPresentation {
  const filtered = filterGraph(graph, filters);
  return { graph: filtered, list: filtered };
}

export function toCytoscapeElements(graph: Pick<GraphResponse, "nodes" | "edges">): CytoscapeElements {
  return {
    nodes: graph.nodes.map(node => ({
      data: { id: node.id, label: node.label, kind: node.kind },
      classes: `evo-node ${nodeStateClass(node)} evo-kind--${node.kind}`,
    })),
    edges: graph.edges.map(edge => ({
      data: { id: edge.id, source: edge.from, target: edge.to },
      classes: `evo-edge ${edgeStyleClass(edge.kind)}`,
    })),
  };
}

function sortedNeighbors(graph: Pick<GraphResponse, "edges">, nodeId: string, outgoing: boolean): string[] {
  const neighbors = graph.edges
    .filter(edge => (outgoing ? edge.from === nodeId : edge.to === nodeId))
    .map(edge => (outgoing ? edge.to : edge.from));
  return [...new Set(neighbors)].sort((left, right) => left.localeCompare(right));
}

export function neighborId(
  graph: Pick<GraphResponse, "nodes" | "edges">,
  selectedId: string | null,
  key: GraphArrowKey,
): string | null {
  const nodes = [...graph.nodes].sort((left, right) => left.id.localeCompare(right.id));
  if (nodes.length === 0) return null;
  if (selectedId === null || !nodes.some(node => node.id === selectedId)) {
    return key === "ArrowLeft" || key === "ArrowUp" ? nodes.at(-1)!.id : nodes[0]!.id;
  }

  const preferInbound = key === "ArrowLeft" || key === "ArrowUp";
  const primary = sortedNeighbors(graph, selectedId, !preferInbound);
  const fallback = sortedNeighbors(graph, selectedId, preferInbound);
  return primary[0] ?? fallback[0] ?? selectedId;
}

export function truncationNotice(graph: Pick<GraphResponse, "truncated">): string | null {
  return graph.truncated
    ? "This is a bounded graph view and is not complete. Select a node to expand its local neighborhood."
    : null;
}
