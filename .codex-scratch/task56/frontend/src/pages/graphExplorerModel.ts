import {
  DashboardGraphEdge,
  DashboardGraphNode,
  DashboardGraphResponse,
} from "@/types/devboard";

export interface GraphViewModel {
  nodesByHandle: ReadonlyMap<string, DashboardGraphNode>;
  edgesByHandle: ReadonlyMap<string, readonly DashboardGraphEdge[]>;
  incomingByHandle: ReadonlyMap<string, readonly DashboardGraphNode[]>;
  outgoingByHandle: ReadonlyMap<string, readonly DashboardGraphNode[]>;
  selectedEdges: readonly DashboardGraphEdge[];
  visibleNodes: readonly DashboardGraphNode[];
}

const EMPTY_EDGES = Object.freeze([]) as readonly DashboardGraphEdge[];
const EMPTY_NODES = Object.freeze([]) as readonly DashboardGraphNode[];

export function buildGraphViewModel(
  response: DashboardGraphResponse,
  selectedHandle: string | null,
): GraphViewModel {
  const dataItems = response.query_type === "scopes" ? [] : response.items;
  const nodesByHandle = new Map<string, DashboardGraphNode>();
  if (response.query_type !== "scopes" && response.node) {
    nodesByHandle.set(response.node.handle, response.node);
  }
  dataItems.forEach((node) => nodesByHandle.set(node.handle, node));

  const edgeBuckets = new Map<string, DashboardGraphEdge[]>();
  const incomingBuckets = new Map<string, DashboardGraphNode[]>();
  const outgoingBuckets = new Map<string, DashboardGraphNode[]>();
  const uniqueEdges: DashboardGraphEdge[] = [];
  const seenEdges = new Set<string>();

  response.edges.forEach((edge) => {
    const key = `${edge.from_handle}\u0000${edge.to_handle}\u0000${edge.edge_type}`;
    if (seenEdges.has(key)) return;
    seenEdges.add(key);
    uniqueEdges.push(edge);

    const fromEdges = edgeBuckets.get(edge.from_handle) ?? [];
    fromEdges.push(edge);
    edgeBuckets.set(edge.from_handle, fromEdges);
    if (edge.to_handle !== edge.from_handle) {
      const toEdges = edgeBuckets.get(edge.to_handle) ?? [];
      toEdges.push(edge);
      edgeBuckets.set(edge.to_handle, toEdges);
    }

    const fromNode = nodesByHandle.get(edge.from_handle);
    const toNode = nodesByHandle.get(edge.to_handle);
    if (fromNode && toNode) {
      const incoming = incomingBuckets.get(toNode.handle) ?? [];
      incoming.push(fromNode);
      incomingBuckets.set(toNode.handle, incoming);
      const outgoing = outgoingBuckets.get(fromNode.handle) ?? [];
      outgoing.push(toNode);
      outgoingBuckets.set(fromNode.handle, outgoing);
    }
  });

  const edgesByHandle = new Map<string, readonly DashboardGraphEdge[]>();
  edgeBuckets.forEach((edges, handle) => edgesByHandle.set(handle, Object.freeze([...edges])));
  const incomingByHandle = new Map<string, readonly DashboardGraphNode[]>();
  incomingBuckets.forEach((nodes, handle) => incomingByHandle.set(handle, Object.freeze([...nodes])));
  const outgoingByHandle = new Map<string, readonly DashboardGraphNode[]>();
  outgoingBuckets.forEach((nodes, handle) => outgoingByHandle.set(handle, Object.freeze([...nodes])));

  const selectedNode = selectedHandle ? nodesByHandle.get(selectedHandle) : undefined;
  const selectedEdges = selectedNode ? (edgesByHandle.get(selectedNode.handle) ?? EMPTY_EDGES) : EMPTY_EDGES;
  const visibleNodes = selectedNode
    ? Object.freeze([
      selectedNode,
      ...Array.from(new Set(selectedEdges.flatMap((edge) => [edge.from_handle, edge.to_handle])))
        .filter((handle) => handle !== selectedNode.handle)
        .map((handle) => nodesByHandle.get(handle))
        .filter((node): node is DashboardGraphNode => Boolean(node)),
    ])
    : EMPTY_NODES;

  return {
    nodesByHandle,
    edgesByHandle,
    incomingByHandle,
    outgoingByHandle,
    selectedEdges,
    visibleNodes,
  };
}
