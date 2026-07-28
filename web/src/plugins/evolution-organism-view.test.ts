import { afterEach, describe, expect, it, vi } from "vitest";
import type { EvolutionSnapshot, GraphResponse } from "../../../plugins/evolution/dashboard/src/types";

type Element = { type: unknown; props: Record<string, unknown> };

const originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window");

afterEach(() => {
  if (originalWindow === undefined) Reflect.deleteProperty(globalThis, "window");
  else Object.defineProperty(globalThis, "window", originalWindow);
  vi.resetModules();
});

function graph(): GraphResponse {
  return {
    schema_version: 1, revision_id: "rev-1", revision_digest: "a".repeat(64), blockers: [], total_nodes: 1, total_edges: 0, truncated: true,
    nodes: [{ id: "capability:alpha", kind: "capability", label: "Alpha", owner_class: "core", generation_scope: "stable", state: { available: true }, evidence_refs: [] }],
    edges: [],
  };
}

function snapshot(): EvolutionSnapshot {
  return {
    schema_version: 1, state: "ready", observed_at: "2026-07-28T10:00:00Z", snapshot_digest: "b".repeat(64), diagnostics: [],
    organism: { id_prefix: "organism", lineage_prefix: "lineage" },
    gnothi: { state: "ready", revision_id: "rev-1", revision_digest: "a".repeat(64), node_count: 1, edge_count: 0 },
    telos: { state: "ready", active_digest: "c".repeat(64), revision_count: 1 },
    observer: { state: "ready", enabled: true, last_scan_at: null, observation_count: 0 },
    generations: { state: "ready", active_generation_id: null, generation_count: 0 },
    pipeline: { state: "ready", suggestions: { total: 0, by_state: {}, truncated: false }, blueprints: { total: 0, by_state: {}, truncated: false }, lifecycle: { pending_approval_count: 0, decision_count: 0 } },
  };
}

function find(element: unknown, predicate: (candidate: Element) => boolean): Element | null {
  if (element === null || typeof element !== "object") return null;
  const candidate = element as Element;
  if (candidate.props !== undefined && predicate(candidate)) return candidate;
  const children = candidate.props?.children;
  for (const child of Array.isArray(children) ? children : [children]) {
    const found = find(child, predicate);
    if (found !== null) return found;
  }
  return null;
}

describe("OrganismView bounded neighborhood expansion", () => {
  it("refetches the selected bounded neighborhood with the active graph query", async () => {
    const paths: string[] = [];
    const state: unknown[] = [];
    let stateIndex = 0;
    Object.defineProperty(globalThis, "window", {
      configurable: true,
      value: {
        __HERMES_PLUGIN_SDK__: {
          React: { createElement: (type: unknown, props: Record<string, unknown> | null, ...children: unknown[]) => ({ type, props: { ...props, children } }) },
          hooks: {
            useCallback: <T,>(callback: T) => callback,
            useEffect: (effect: () => void | (() => void)) => { effect(); },
            useMemo: <T,>(factory: () => T) => factory(),
            useRef: <T,>(initial: T) => ({ current: initial }),
            useState: <T,>(initial: T) => {
              const index = stateIndex++;
              if (state[index] === undefined) state[index] = initial;
              return [state[index] as T, (next: T | ((previous: T) => T)) => { state[index] = typeof next === "function" ? (next as (previous: T) => T)(state[index] as T) : next; }] as const;
            },
          },
          fetchJSON: (path: string) => { paths.push(path); return Promise.resolve(graph()); },
          components: { Badge: () => null, Button: () => null, Checkbox: () => null, Input: () => null, Label: () => null, Select: () => null, SelectOption: () => null, Separator: () => null },
          utils: { cn: () => "", timeAgo: () => "", isoTimeAgo: () => "" },
        },
      },
    });
    const { OrganismView } = await import("../../../plugins/evolution/dashboard/src/components/OrganismView");
    const render = () => {
      stateIndex = 0;
      return OrganismView({ snapshot: snapshot(), onRefresh: vi.fn(async () => {}), onTrackJob: vi.fn() }) as unknown as Element;
    };

    render();
    await Promise.resolve();
    let tree = render();
    const graphElement = find(tree, item => typeof item.type === "function" && (item.type as Function).name === "OrganismGraph");
    (graphElement?.props.onOpenInspector as (id: string) => void)("capability:alpha");
    tree = render();
    const expand = find(tree, item => item.type === "button" && String(item.props.children).includes("Expand selected neighborhood"));

    expect(expand).not.toBeNull();
    (expand?.props.onClick as () => void)();
    render();

    expect(paths.at(-1)).toBe("/api/plugins/evolution/graph?root_id=capability%3Aalpha&depth=2&limit=200&expected_revision=rev-1");
  });
});
