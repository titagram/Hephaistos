import { afterEach, describe, expect, it, vi } from "vitest";
import {
  edgeStyleClass,
  filterGraph,
  graphPresentation,
  neighborId,
  nodeStateClass,
  toCytoscapeElements,
  truncationNotice,
} from "../../../plugins/evolution/dashboard/src/graph-model";
import type { GraphResponse } from "../../../plugins/evolution/dashboard/src/types";

const cytoscapeMock = vi.hoisted(() => {
  const core = {
    destroy: vi.fn(),
    fit: vi.fn(),
    zoom: vi.fn(() => 1),
    center: vi.fn(),
    getElementById: vi.fn(() => ({ select: vi.fn(), unselect: vi.fn(), empty: () => false })),
    elements: vi.fn(() => ({ unselect: vi.fn() })),
    on: vi.fn(),
    off: vi.fn(),
  };
  return { core, factory: vi.fn(() => core) };
});

vi.mock("cytoscape", () => ({ default: cytoscapeMock.factory }));

const originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window");

afterEach(() => {
  if (originalWindow === undefined) {
    Reflect.deleteProperty(globalThis, "window");
  } else {
    Object.defineProperty(globalThis, "window", originalWindow);
  }
  cytoscapeMock.factory.mockClear();
  cytoscapeMock.core.destroy.mockClear();
  cytoscapeMock.core.on.mockClear();
  vi.resetModules();
});

function graph(overrides: Partial<GraphResponse> = {}): GraphResponse {
  return {
    schema_version: 1,
    revision_id: "rev-1",
    revision_digest: "a".repeat(64),
    nodes: [
      {
        id: "capability:alpha",
        kind: "capability",
        label: "Alpha",
        owner_class: "core",
        generation_scope: "stable",
        state: { available: true },
        evidence_refs: ["evidence:alpha"],
      },
      {
        id: "runtime:beta",
        kind: "runtime",
        label: "Beta runtime",
        owner_class: "core",
        generation_scope: "stable",
        state: { degraded: true },
        evidence_refs: [],
      },
      {
        id: "skill:gamma",
        kind: "skill",
        label: "Gamma skill",
        owner_class: "plugin",
        generation_scope: "candidate",
        state: {},
        evidence_refs: [],
      },
    ],
    edges: [
      {
        id: "edge:provides",
        kind: "provides",
        from: "capability:alpha",
        to: "runtime:beta",
        evidence_refs: [],
      },
      {
        id: "edge:requires",
        kind: "requires",
        from: "runtime:beta",
        to: "skill:gamma",
        evidence_refs: [],
      },
      {
        id: "edge:depends",
        kind: "depends_on",
        from: "skill:gamma",
        to: "capability:alpha",
        evidence_refs: [],
      },
    ],
    blockers: [],
    total_nodes: 3,
    total_edges: 3,
    truncated: false,
    ...overrides,
  };
}

describe("Evolution organism graph model", () => {
  it("preserves public stable ids in Cytoscape node and edge data", () => {
    const elements = toCytoscapeElements(graph());

    expect(elements.nodes.map(node => node.data.id)).toEqual([
      "capability:alpha",
      "runtime:beta",
      "skill:gamma",
    ]);
    expect(elements.edges.map(edge => edge.data)).toEqual([
      { id: "edge:provides", source: "capability:alpha", target: "runtime:beta" },
      { id: "edge:requires", source: "runtime:beta", target: "skill:gamma" },
      { id: "edge:depends", source: "skill:gamma", target: "capability:alpha" },
    ]);
  });

  it("gives provides, requires, and depends_on distinct semantic edge classes", () => {
    expect(edgeStyleClass("provides")).toBe("evo-edge--provides");
    expect(edgeStyleClass("requires")).toBe("evo-edge--requires");
    expect(edgeStyleClass("depends_on")).toBe("evo-edge--depends-on");
  });

  it("maps all public node health conditions to distinct semantic classes", () => {
    const base = graph().nodes[0]!;

    expect(nodeStateClass({ ...base, state: { available: true } })).toBe("evo-node--healthy");
    expect(nodeStateClass({ ...base, state: { degraded: true } })).toBe("evo-node--degraded");
    expect(nodeStateClass({ ...base, state: { stale: true } })).toBe("evo-node--stale");
    expect(nodeStateClass({ ...base, state: { available: false } })).toBe("evo-node--missing");
    expect(nodeStateClass({ ...base, state: {} })).toBe("evo-node--unknown");
  });

  it("filters nodes by exact kind and text without producing dangling edges", () => {
    const filtered = filterGraph(graph(), {
      kinds: new Set(["capability", "runtime"]),
      search: "",
    });

    expect(filtered.nodes.map(node => node.id)).toEqual(["capability:alpha", "runtime:beta"]);
    expect(filtered.edges.map(edge => edge.id)).toEqual(["edge:provides"]);
    expect(filtered.edges.every(edge =>
      filtered.nodes.some(node => node.id === edge.from) &&
      filtered.nodes.some(node => node.id === edge.to),
    )).toBe(true);
  });

  it("chooses keyboard neighbors deterministically from sorted inbound and outbound edges", () => {
    const navigationGraph = graph({
      nodes: [
        { ...graph().nodes[0]!, id: "node:a", label: "A" },
        { ...graph().nodes[0]!, id: "node:b", label: "B" },
        { ...graph().nodes[0]!, id: "node:c", label: "C" },
        { ...graph().nodes[0]!, id: "node:d", label: "D" },
      ],
      edges: [
        { id: "edge:d", kind: "provides", from: "node:a", to: "node:d", evidence_refs: [] },
        { id: "edge:b", kind: "provides", from: "node:a", to: "node:b", evidence_refs: [] },
        { id: "edge:c", kind: "requires", from: "node:c", to: "node:a", evidence_refs: [] },
      ],
    });

    expect(neighborId(navigationGraph, "node:a", "ArrowRight")).toBe("node:b");
    expect(neighborId(navigationGraph, "node:a", "ArrowLeft")).toBe("node:c");
    expect(neighborId(navigationGraph, null, "ArrowDown")).toBe("node:a");
  });

  it("exposes a visible notice whenever the server bounded the graph", () => {
    expect(truncationNotice(graph({ truncated: true }))).toContain("not complete");
    expect(truncationNotice(graph({ truncated: false }))).toBeNull();
  });

  it("provides graph and list views the same filtered nodes and edges", () => {
    const presentation = graphPresentation(graph(), {
      kinds: new Set(["capability", "runtime"]),
      search: "",
    });

    expect(presentation.graph.nodes).toBe(presentation.list.nodes);
    expect(presentation.graph.edges).toBe(presentation.list.edges);
  });

  it("creates one Cytoscape instance and destroys it when the graph unmounts", async () => {
    const container = {} as HTMLDivElement;
    let cleanup: (() => void) | undefined;
    let refIndex = 0;
    Object.defineProperty(globalThis, "window", {
      configurable: true,
      value: {
        __HERMES_PLUGIN_SDK__: {
          React: { createElement: vi.fn() },
          hooks: {
            useCallback: <T,>(callback: T) => callback,
            useEffect: (effect: () => void | (() => void)) => {
              const nextCleanup = effect();
              if (typeof nextCleanup === "function") cleanup = nextCleanup;
            },
            useMemo: <T,>(factory: () => T) => factory(),
            useRef: <T,>() => {
              const values = [{ current: container }, { current: null }];
              return values[refIndex++] as { current: T };
            },
          },
          fetchJSON: vi.fn(),
          components: {
            Badge: () => null,
            Button: () => null,
            Checkbox: () => null,
            Input: () => null,
            Label: () => null,
            Select: () => null,
            SelectOption: () => null,
            Separator: () => null,
          },
          utils: { cn: () => "", timeAgo: () => "", isoTimeAgo: () => "" },
        },
      },
    });

    const { OrganismGraph } = await import("../../../plugins/evolution/dashboard/src/components/OrganismGraph");
    const data = graph();
    OrganismGraph({ nodes: data.nodes, edges: data.edges, selectedId: null, onSelect: vi.fn(), onOpenInspector: vi.fn() });

    expect(cytoscapeMock.factory).toHaveBeenCalledTimes(1);
    expect(cytoscapeMock.factory).toHaveBeenCalledWith(expect.objectContaining({ container }));
    cleanup?.();
    expect(cytoscapeMock.core.destroy).toHaveBeenCalledTimes(1);
  });

  it("opens the compact inspector on graph tap while Enter still opens the selected node", async () => {
    const container = {} as HTMLDivElement;
    let refIndex = 0;
    Object.defineProperty(globalThis, "window", {
      configurable: true,
      value: {
        __HERMES_PLUGIN_SDK__: {
          React: { createElement: (type: unknown, props: Record<string, unknown> | null, ...children: unknown[]) => ({ type, props, children }) },
          hooks: {
            useCallback: <T,>(callback: T) => callback,
            useEffect: (effect: () => void | (() => void)) => { effect(); },
            useMemo: <T,>(factory: () => T) => factory(),
            useRef: <T,>() => {
              const values = [{ current: container }, { current: null }];
              return values[refIndex++] as { current: T };
            },
          },
          fetchJSON: vi.fn(),
          components: { Badge: () => null, Button: () => null, Checkbox: () => null, Input: () => null, Label: () => null, Select: () => null, SelectOption: () => null, Separator: () => null },
          utils: { cn: () => "", timeAgo: () => "", isoTimeAgo: () => "" },
        },
      },
    });
    const { OrganismGraph } = await import("../../../plugins/evolution/dashboard/src/components/OrganismGraph");
    const onSelect = vi.fn();
    const onOpenInspector = vi.fn();
    const data = graph();
    const tree = OrganismGraph({ nodes: data.nodes, edges: data.edges, selectedId: data.nodes[0]!.id, onSelect, onOpenInspector }) as unknown as { props: { children: unknown[] } };
    const tap = cytoscapeMock.core.on.mock.calls.find(call => call[0] === "tap")?.[2] as ((event: { target: { id(): string } }) => void) | undefined;

    tap?.({ target: { id: () => data.nodes[1]!.id } });
    const canvas = (tree.props.children[1] as { props: { onKeyDown(event: { key: string; preventDefault(): void }): void } }).props;
    canvas.onKeyDown({ key: "Enter", preventDefault: vi.fn() });

    expect(onSelect).toHaveBeenCalledWith(data.nodes[1]!.id);
    expect(onOpenInspector).toHaveBeenCalledWith(data.nodes[1]!.id);
    expect(onOpenInspector).toHaveBeenCalledWith(data.nodes[0]!.id);
  });

  it("gives the interactive canvas a responsive nonzero minimum height before host CSS loads", async () => {
    const container = {} as HTMLDivElement;
    let refIndex = 0;
    Object.defineProperty(globalThis, "window", {
      configurable: true,
      value: {
        __HERMES_PLUGIN_SDK__: {
          React: {
            createElement: (type: unknown, props: Record<string, unknown> | null, ...children: unknown[]) => ({ type, props, children }),
          },
          hooks: {
            useCallback: <T,>(callback: T) => callback,
            useEffect: (effect: () => void | (() => void)) => { effect(); },
            useMemo: <T,>(factory: () => T) => factory(),
            useRef: <T,>() => {
              const values = [{ current: container }, { current: null }];
              return values[refIndex++] as { current: T };
            },
          },
          fetchJSON: vi.fn(),
          components: {
            Badge: () => null, Button: () => null, Checkbox: () => null, Input: () => null,
            Label: () => null, Select: () => null, SelectOption: () => null, Separator: () => null,
          },
          utils: { cn: () => "", timeAgo: () => "", isoTimeAgo: () => "" },
        },
      },
    });

    const { OrganismGraph } = await import("../../../plugins/evolution/dashboard/src/components/OrganismGraph");
    const data = graph();
    const tree = OrganismGraph({ nodes: data.nodes, edges: data.edges, selectedId: null, onSelect: vi.fn(), onOpenInspector: vi.fn() }) as unknown as { props: { children: unknown[] } };
    const canvas = ((tree.props.children[1] as { props: Record<string, unknown> }).props);

    expect(canvas.className).toBe("evo-organism-graph__canvas");
    expect(canvas.style).toMatchObject({ minHeight: "20rem", width: "100%" });
  });
});
