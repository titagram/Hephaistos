import React, { act } from "react";
import { createRoot, Root } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";

declare const jest: any;
declare const beforeEach: any;
declare const afterEach: any;
declare const describe: any;
declare const it: any;
declare const expect: any;

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

const getProjects = require("jest-mock").fn();
const getGraph = require("jest-mock").fn();
const queryProjectGraph = require("jest-mock").fn();

jest.mock("@/api/devboardApi", () => ({ api: { getProjects, getGraph, queryProjectGraph } }), { virtual: true });

import GraphPage from "./GraphPage";

const project = {
  id: "project-1", key: "P1", name: "Project One", description: "", owner: "admin", repository_count: 1,
  open_tasks: 0, risk_level: "low", wiki_freshness: "passed", genesis_status: "passed", delta_status: "passed",
  graph_status: "passed", updated_at: "2026-07-14T10:00:00Z", status: "active", archived_at: null, deleted_at: null, restored_at: null,
};
const graph = {
  generated_at: "2026-07-14T10:00:00Z", source: { type: "canonical_graph", status: "verified_from_code", origin: "canonical projection", generated_at: "2026-07-14T10:00:00Z" },
  stats: { nodes: 4, edges: 3, modules: 1, routes: 1 }, nodes: [], edges: [], quality: "complete", projection_status: "ready",
};
const scopeResponse = {
  protocol_version: "v1", project_id: "project-1", query_type: "scopes", found: true, reason: null, scope: null,
  projection: { status: "unavailable", quality: null, generated_at: null, active_graph_version: null, node_count: 0, relationship_count: 0, unknown_kind_count: 0, missing_label_count: 0, excluded_node_count: 0 },
  node: null, items: [{ source_scope_type: "repository", source_scope_id: "repo-1", status: "ready", quality: "complete" }], edges: [], returned: 1, limit: 100,
  next_cursor: null, has_more: false, truncated: false, source: { type: "canonical_graph", status: "verified_from_code", origin: "canonical projection" },
};

let container: HTMLDivElement;
let root: Root;

async function settle() { await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); }); }

describe("GraphPage global project selection", () => {
  beforeEach(() => {
    container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container);
    getProjects.mockReset().mockResolvedValue([project]);
    getGraph.mockReset().mockResolvedValue(graph);
    queryProjectGraph.mockReset().mockImplementation((_: string, request: any) => request.type === "scopes" ? Promise.resolve(scopeResponse) : Promise.reject(new Error("unexpected")));
  });
  afterEach(() => { act(() => root.unmount()); container.remove(); });

  it("does not issue graph POSTs or undefined project URLs before a real project is chosen", async () => {
    await act(async () => {
      root.render(<MemoryRouter initialEntries={["/graph"]}><Routes><Route path="/graph" element={<GraphPage />} /><Route path="/projects/:projectId/graph" element={<GraphPage />} /></Routes></MemoryRouter>);
    });
    await settle();
    expect(container.textContent).toContain("Choose a project");
    expect(queryProjectGraph).not.toHaveBeenCalled();
    expect(getGraph).not.toHaveBeenCalledWith(undefined, expect.anything());

    const select = container.querySelector("select[aria-label='Project']") as HTMLSelectElement;
    await act(async () => { select.value = "project-1"; select.dispatchEvent(new Event("change", { bubbles: true })); });
    await settle();
    await settle();
    expect(window.location.pathname).not.toContain("undefined");
    expect(getGraph).toHaveBeenCalledWith("project-1", expect.anything());
    expect(queryProjectGraph).toHaveBeenCalledWith("project-1", expect.objectContaining({ type: "scopes" }));
  });
});
