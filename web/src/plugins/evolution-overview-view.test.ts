import { afterEach, describe, expect, it, vi } from "vitest";
import type { EvolutionJob, EvolutionSnapshot } from "../../../plugins/evolution/dashboard/src/types";

type Element = { type: unknown; props: Record<string, unknown> };

const originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window");

afterEach(() => {
  if (originalWindow === undefined) Reflect.deleteProperty(globalThis, "window");
  else Object.defineProperty(globalThis, "window", originalWindow);
  vi.resetModules();
});

function snapshot(): EvolutionSnapshot {
  return {
    schema_version: 1, state: "ready", observed_at: "2026-07-28T10:00:00Z", snapshot_digest: "a".repeat(64), diagnostics: [],
    organism: { id_prefix: "organism", lineage_prefix: "lineage" },
    gnothi: { state: "ready", revision_id: "rev-1", revision_digest: "b".repeat(64), node_count: 1, edge_count: 0, coverage: { current_domains: 4, total_domains: 4, unknown_domains: [], truncated: false, drifted_domains: [], drift_truncated: false, collector_status: [], collector_status_truncated: false } },
    telos: { state: "ready", active_digest_prefix: "c".repeat(12), revision_summary: { parent_digest_prefix: null, purpose: "Operate safely.", desired_trait_count: 1, capability_direction_count: 1, priority_count: 1, prohibition_count: 1, success_indicator_count: 1 } },
    observer: { state: "ready", enabled: true, circuit_open: false, degraded_reason: null },
    generations: { state: "ready", active_generation_prefix: null, last_known_good_generation_prefix: null, overlay_enabled: false },
    pipeline: { state: "ready", suggestions: { total: 0, by_state: {}, truncated: false }, blueprints: { total: 0, by_state: {}, truncated: false }, lifecycle: { pending_approval_count: 0, decision_count: 0 } },
  };
}

function job(state: EvolutionJob["state"]): EvolutionJob {
  return {
    job_id: "observer-job", kind: "observer_scan", state, progress: 12,
    created_at: "2026-07-28T10:00:00Z", started_at: null, finished_at: null,
    process_nonce: "nonce", result: null, error_code: null,
  };
}

function find(element: unknown, predicate: (candidate: Element) => boolean): Element | null {
  if (element === null || typeof element !== "object") return null;
  const candidate = element as Element;
  if (candidate.props !== undefined && predicate(candidate)) return candidate;
  for (const child of Array.isArray(candidate.props.children) ? candidate.props.children : [candidate.props.children]) {
    const found = find(child, predicate);
    if (found !== null) return found;
  }
  return null;
}

describe("OverviewView observer scan action", () => {
  it("disables a queued or running observer scan without suppressing the truthful pause control", async () => {
    const paths: string[] = [];
    Object.defineProperty(globalThis, "window", {
      configurable: true,
      value: {
        __HERMES_PLUGIN_SDK__: {
          React: { createElement: (type: unknown, props: Record<string, unknown> | null, ...children: unknown[]) => ({ type, props: { ...props, children } }) },
          hooks: {
            useCallback: <T,>(callback: T) => callback,
            useEffect: (effect: () => void | (() => void)) => { effect(); },
            useState: <T,>(initial: T) => [initial, vi.fn()] as const,
          },
          fetchJSON: (path: string) => {
            paths.push(path);
            if (path.endsWith("/pipeline?limit=12")) return Promise.resolve({ suggestions: [] });
            if (path.endsWith("/audit?limit=12")) return Promise.resolve({ events: [] });
            if (path.endsWith("/mutation-context")) return Promise.resolve({ organism_id: "organism", expected_snapshot_digest: "a".repeat(64) });
            return Promise.resolve({});
          },
          components: { Badge: () => null, Button: () => null, Checkbox: () => null, Input: () => null, Label: () => null, Select: () => null, SelectOption: () => null, Separator: () => null },
          utils: { cn: () => "", timeAgo: () => "", isoTimeAgo: () => "" },
        },
      },
    });
    const { OverviewView } = await import("../../../plugins/evolution/dashboard/src/components/OverviewView");
    const render = (activeJob: EvolutionJob | null) => OverviewView({
      snapshot: snapshot(), activeJob, onRefresh: vi.fn(async () => {}), onTrackJob: vi.fn(), onNavigate: vi.fn(),
    }) as unknown as Element;

    for (const activeJob of [job("queued"), job("running")]) {
      const tree = render(activeJob);
      const runScan = find(tree, item => item.type === "button" && item.props.className === "evo-action--primary");
      const pause = find(tree, item => item.type === "button" && item.props.className !== "evo-action--primary");

      expect(runScan?.props.disabled).toBe(true);
      expect(pause?.props.disabled).toBe(false);
      (runScan?.props.onClick as () => void)();
    }

    await Promise.resolve();
    await Promise.resolve();
    expect(paths).not.toContain("/api/plugins/evolution/jobs/observer-scan");

    const terminalTree = render(job("completed"));
    const terminalRunScan = find(terminalTree, item => item.type === "button" && item.props.className === "evo-action--primary");
    expect(terminalRunScan?.props.disabled).toBe(false);
  });
});
