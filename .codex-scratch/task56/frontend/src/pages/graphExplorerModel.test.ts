import { buildGraphViewModel } from "./graphExplorerModel";
import { DashboardGraphDataResponse, DashboardGraphEdge, DashboardGraphNode } from "@/types/devboard";

declare const describe: any;
declare const it: any;
declare const expect: any;

const source = { type: "canonical_graph" as const, status: "verified_from_code" as const, origin: "canonical projection" as const };
const projection = {
  status: "ready", quality: "complete", generated_at: "2026-07-14T10:00:00Z",
  active_graph_version: "v1", node_count: 0, relationship_count: 0,
  unknown_kind_count: 0, missing_label_count: 0, excluded_node_count: 0,
};

function node(handle: string): DashboardGraphNode {
  return { handle, kind: "class", label: handle };
}

function response(items: DashboardGraphNode[], edges: DashboardGraphEdge[]): DashboardGraphDataResponse {
  return {
    protocol_version: "v1", project_id: "project-1", query_type: "neighborhood",
    found: true, reason: null, scope: { type: "repository", id: "repo-1" },
    projection: { ...projection, node_count: items.length, relationship_count: edges.length },
    node: items[0] ?? null, items, edges, returned: items.length, limit: 50,
    next_cursor: null, has_more: false, truncated: false, source,
  };
}

describe("buildGraphViewModel", () => {
  it("returns empty immutable collections for an empty response", () => {
    const model = buildGraphViewModel(response([], []), null);
    expect(model.nodesByHandle.size).toBe(0);
    expect(model.visibleNodes).toEqual([]);
    expect(Object.isFrozen(model.visibleNodes)).toBe(true);
  });

  it("builds inbound and outbound adjacency from opaque handles", () => {
    const root = node("opaque-root");
    const caller = node("opaque-caller");
    const dependency = node("opaque-dependency");
    const model = buildGraphViewModel(response([root, caller, dependency], [
      { from_handle: caller.handle, to_handle: root.handle, edge_type: "CALLS_METHOD", family: "call" },
      { from_handle: root.handle, to_handle: dependency.handle, edge_type: "USES_DEPENDENCY", family: "dependency" },
    ]), root.handle);

    expect(model.incomingByHandle.get(root.handle)?.map((value) => value.handle)).toEqual([caller.handle]);
    expect(model.outgoingByHandle.get(root.handle)?.map((value) => value.handle)).toEqual([dependency.handle]);
    expect(model.selectedEdges).toHaveLength(2);
    expect(model.visibleNodes.map((value) => value.handle)).toEqual([root.handle, caller.handle, dependency.handle]);
  });

  it("returns no selected relations for an unknown handle", () => {
    const model = buildGraphViewModel(response([node("opaque-a")], []), "not-present");
    expect(model.selectedEdges).toEqual([]);
    expect(model.visibleNodes).toEqual([]);
  });

  it("deduplicates edges by from/to/type without mutating the response", () => {
    const root = node("opaque-a");
    const peer = node("opaque-b");
    const duplicate = { from_handle: root.handle, to_handle: peer.handle, edge_type: "CALLS_METHOD", family: "call" as const };
    const payload = response([root, peer], [duplicate, { ...duplicate }]);
    const before = JSON.stringify(payload);
    const model = buildGraphViewModel(payload, root.handle);
    expect(model.selectedEdges).toHaveLength(1);
    expect(model.edgesByHandle.get(root.handle)).toHaveLength(1);
    expect(JSON.stringify(payload)).toBe(before);
  });

  it("indexes a 5,000-node and 12,000-edge response into maps", () => {
    const items = Array.from({ length: 5000 }, (_, index) => node(`opaque-${index}`));
    const edges = Array.from({ length: 12000 }, (_, index) => ({
      from_handle: items[index % items.length].handle,
      to_handle: items[(index + 1) % items.length].handle,
      edge_type: `EDGE_${index}`,
      family: "dependency" as const,
    }));
    const model = buildGraphViewModel(response(items, edges), items[0].handle);
    expect(model.nodesByHandle.size).toBe(5000);
    expect(model.edgesByHandle.get(items[0].handle)?.length).toBeGreaterThan(0);
    expect(model.visibleNodes[0].handle).toBe(items[0].handle);
  });
});
