import React, { act } from "react";
import { createRoot, Root } from "react-dom/client";
import GraphExplorer from "./GraphExplorer";
import { DashboardGraphQueryRequest, DashboardGraphResponse, DashboardGraphScopeItem } from "@/types/devboard";

declare const jest: any;
declare const beforeEach: any;
declare const afterEach: any;
declare const describe: any;
declare const it: any;
declare const expect: any;

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

jest.mock("@/components/ui/button", () => ({ Button: ({ children, ...props }: any) => <button {...props}>{children}</button> }), { virtual: true });
jest.mock("@/components/ui/input", () => ({ Input: (props: any) => <input {...props} /> }), { virtual: true });
jest.mock("@/components/devboard/Layout", () => ({ Panel: ({ title, children }: any) => <section><h2>{title}</h2>{children}</section> }), { virtual: true });

const handles = { invoice: "opaque-invoice", caller: "opaque-caller", dependency: "opaque-dependency", target: "opaque-target" };
const source = { type: "canonical_graph" as const, status: "verified_from_code" as const, origin: "canonical projection" as const };
const projection = { status: "ready", quality: "complete", generated_at: "2026-07-14T10:00:00Z", active_graph_version: "graph-v1", node_count: 4, relationship_count: 3, unknown_kind_count: 0, missing_label_count: 0, excluded_node_count: 0 };
const scope = { source_scope_type: "workspace_binding" as const, source_scope_id: "binding-2" };
const node = (handle: string, label: string, extra = {}) => ({ handle, label, kind: "class" as const, ...extra });

function envelope(type: any, values: any = {}): DashboardGraphResponse {
  return {
    protocol_version: "v1", project_id: "project-1", query_type: type, found: true, reason: null,
    scope: { type: scope.source_scope_type, id: scope.source_scope_id }, projection, node: null, items: [], edges: [], returned: 0, limit: 50,
    next_cursor: null, has_more: false, truncated: false, source, ...values,
  } as DashboardGraphResponse;
}

const scopes: DashboardGraphScopeItem[] = [
  { source_scope_type: "repository", source_scope_id: "repo-1", status: "ready", quality: "complete" },
  { source_scope_type: "workspace_binding", source_scope_id: "binding-2", status: "ready", quality: "complete" },
];

function successfulQuery(request: DashboardGraphQueryRequest): Promise<DashboardGraphResponse> {
  if (request.type === "search") return Promise.resolve(envelope("search", {
    items: [node(handles.invoice, "InvoiceService"), node(handles.target, "InvoiceRepository")], returned: 2,
    has_more: true, next_cursor: "cursor-2",
  }));
  if (request.type === "detail") return Promise.resolve(envelope("detail", { node: node(handles.invoice, "InvoiceService") }));
  if (request.type === "neighborhood" && request.direction === "in") return Promise.resolve(envelope("neighborhood", {
    node: node(handles.invoice, "InvoiceService"), items: [node(handles.caller, "BillingController")], returned: 1,
    edges: [{ from_handle: handles.caller, to_handle: handles.invoice, edge_type: "CALLS_METHOD", family: "call" }],
  }));
  if (request.type === "neighborhood") return Promise.resolve(envelope("neighborhood", {
    node: node(handles.invoice, "InvoiceService"), items: [node(handles.dependency, "InvoiceRepository")], returned: 1,
    edges: [{ from_handle: handles.invoice, to_handle: handles.dependency, edge_type: "USES_DEPENDENCY", family: "dependency" }],
  }));
  if (request.type === "impact") return Promise.resolve(envelope("impact", {
    items: [node(handles.dependency, "InvoiceRepository", { why: "dependency edge USES_DEPENDENCY", family: "dependency", edge_types: ["USES_DEPENDENCY"], distance: 1 })],
    returned: 1, truncated: true,
  }));
  if (request.type === "path") return Promise.resolve(envelope("path", {
    items: [node(handles.invoice, "InvoiceService"), node(handles.target, "InvoiceRepository")], returned: 2,
    edges: [{ from_handle: handles.invoice, to_handle: handles.target, edge_type: "USES_DEPENDENCY", family: "dependency" }],
  }));
  return Promise.resolve(envelope(request.type));
}

let container: HTMLDivElement;
let root: Root;

async function settle(ms = 0) {
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, ms)); });
}

async function mount(queryGraph: any = jest.fn(successfulQuery), onQueryParamsChange?: any) {
  await act(async () => {
    root.render(<GraphExplorer projectId="project-1" scopes={scopes} queryGraph={queryGraph} onQueryParamsChange={onQueryParamsChange} />);
  });
  await settle();
  return queryGraph;
}

function input(label: string): HTMLInputElement {
  const element = container.querySelector(`input[aria-label="${label}"]`);
  if (!(element instanceof HTMLInputElement)) throw new Error(`Missing input ${label}`);
  return element;
}

function button(text: string): HTMLButtonElement {
  const element = Array.from(container.querySelectorAll("button")).find((candidate) => candidate.textContent?.includes(text));
  if (!(element instanceof HTMLButtonElement)) throw new Error(`Missing button ${text}`);
  return element;
}

function changeInput(element: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
  setter?.call(element, value);
  element.dispatchEvent(new Event("input", { bubbles: true }));
}

describe("GraphExplorer", () => {
  beforeEach(() => { container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container); });
  afterEach(() => { act(() => root.unmount()); container.remove(); });

  it("uses the chosen scope and opaque handle for search, detail, parallel neighborhoods, impact and path", async () => {
    const queryGraph = await mount();
    const scopeSelect = container.querySelector("select[aria-label='Graph scope']") as HTMLSelectElement;
    expect(scopeSelect.value).toBe("");
    await act(async () => { scopeSelect.value = "workspace_binding:binding-2"; scopeSelect.dispatchEvent(new Event("change", { bubbles: true })); });
    await act(async () => { changeInput(input("Search symbols"), "InvoiceService"); });
    await settle(300);
    expect(container.textContent).toContain("InvoiceService");
    await act(async () => { button("InvoiceService").click(); });
    await settle();
    await settle();

    expect(container.textContent).toContain("Symbol");
    expect(container.textContent).toContain("Callers");
    expect(container.textContent).toContain("BillingController");
    expect(container.textContent).toContain("Dependencies/Callees");
    expect(container.textContent).toContain("Impact");
    expect(container.textContent).toContain("dependency edge USES_DEPENDENCY");
    expect(container.textContent).toContain("distance 1");
    expect(container.textContent).toContain("complete");
    expect(container.textContent).toContain("truncated");
    expect(container.textContent).toContain("cursor-2");

    const calls = queryGraph.mock.calls.map(([request]: [DashboardGraphQueryRequest]) => request);
    expect(calls).toContainEqual(expect.objectContaining({ type: "detail", node_handle: handles.invoice }));
    expect(calls).toContainEqual(expect.objectContaining({ type: "neighborhood", node_handle: handles.invoice, direction: "in", families: ["call", "dependency"] }));
    expect(calls).toContainEqual(expect.objectContaining({ type: "neighborhood", node_handle: handles.invoice, direction: "out", families: ["call", "dependency"] }));
    expect(calls).toContainEqual(expect.objectContaining({ type: "impact", node_handle: handles.invoice, max_depth: 2 }));
    expect(JSON.stringify(calls)).not.toMatch(/credential|plugin_token|internal/i);

    await act(async () => { (container.querySelector("select[aria-label='Path target']") as HTMLSelectElement).value = handles.target; (container.querySelector("select[aria-label='Path target']") as HTMLSelectElement).dispatchEvent(new Event("change", { bubbles: true })); });
    await act(async () => { button("Find path").click(); });
    await settle();
    expect(queryGraph).toHaveBeenCalledWith(expect.objectContaining({ type: "path", from_handle: handles.invoice, to_handle: handles.target, max_depth: 3, limit: 50 }));
    expect(container.textContent).toContain("Path");
  });

  it("renders empty, unavailable and retryable error states while preserving the selected handle", async () => {
    const emptyQuery = jest.fn((request: DashboardGraphQueryRequest) => request.type === "search"
      ? Promise.resolve(envelope("search"))
      : successfulQuery(request));
    await mount(emptyQuery);
    const select = container.querySelector("select[aria-label='Graph scope']") as HTMLSelectElement;
    await act(async () => { select.value = "repository:repo-1"; select.dispatchEvent(new Event("change", { bubbles: true })); });
    await act(async () => { changeInput(input("Search symbols"), "Missing"); });
    await settle(300);
    expect(container.textContent).toContain("No matching symbols");

    act(() => root.unmount()); root = createRoot(container);
    await act(async () => { root.render(<GraphExplorer projectId="project-1" scopes={scopes} projectionUnavailable queryGraph={emptyQuery} />); });
    expect(container.textContent).toContain("Graph projection unavailable");

    act(() => root.unmount()); root = createRoot(container);
    let failed = false;
    const flaky = jest.fn((request: DashboardGraphQueryRequest) => {
      if (request.type === "detail" && !failed) { failed = true; return Promise.reject(new Error("boom")); }
      return successfulQuery(request);
    });
    const changeParams = jest.fn();
    await mount(flaky, changeParams);
    const retryScope = container.querySelector("select[aria-label='Graph scope']") as HTMLSelectElement;
    await act(async () => { retryScope.value = "workspace_binding:binding-2"; retryScope.dispatchEvent(new Event("change", { bubbles: true })); });
    await act(async () => { changeInput(input("Search symbols"), "InvoiceService"); });
    await settle(300);
    await act(async () => { button("InvoiceService").click(); });
    await settle();
    expect(container.textContent).toContain("Unable to load graph details");
    expect(changeParams).toHaveBeenCalledWith(expect.objectContaining({ symbol: handles.invoice }));
    await act(async () => { button("Retry").click(); });
    await settle();
    expect(container.textContent).toContain("InvoiceService");
  });
});
