// @vitest-environment jsdom
import React from "react";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { EvolutionSnapshot, GraphResponse, GraphNode, MutationContext } from "../../../plugins/evolution/dashboard/src/types";

const cytoscapeMock = vi.hoisted(() => {
  const core = {
    center: vi.fn(),
    destroy: vi.fn(),
    elements: vi.fn(() => ({ unselect: vi.fn() })),
    fit: vi.fn(),
    getElementById: vi.fn(() => ({ empty: () => true, select: vi.fn() })),
    off: vi.fn(),
    on: vi.fn(),
    zoom: vi.fn(() => 1),
  };
  return { core, factory: vi.fn(() => core) };
});

vi.mock("cytoscape", () => ({ default: cytoscapeMock.factory }));

let root: Root | null = null;
const originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window");

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

function snapshot(): EvolutionSnapshot {
  return {
    schema_version: 1,
    state: "ready",
    observed_at: "2026-07-28T10:00:00Z",
    snapshot_digest: "a".repeat(64),
    diagnostics: [],
    organism: { id_prefix: "organism", lineage_prefix: "lineage" },
    gnothi: { state: "ready", revision_id: "rev-1", revision_digest: "b".repeat(64), node_count: 1, edge_count: 0, coverage: { current_domains: 4, total_domains: 4, unknown_domains: [], truncated: false, drifted_domains: [], drift_truncated: false, collector_status: [], collector_status_truncated: false } },
    telos: { state: "ready", active_digest_prefix: "c".repeat(12), revision_summary: { parent_digest_prefix: null, purpose: "Operate safely.", desired_trait_count: 1, capability_direction_count: 1, priority_count: 1, prohibition_count: 1, success_indicator_count: 1 } },
    observer: { state: "ready", enabled: true, circuit_open: false, degraded_reason: null },
    generations: { state: "ready", active_generation_prefix: null, last_known_good_generation_prefix: null, overlay_enabled: false },
    pipeline: {
      state: "ready",
      suggestions: { total: 0, by_state: {}, truncated: false },
      blueprints: { total: 0, by_state: {}, truncated: false },
      lifecycle: { pending_approval_count: 0, decision_count: 0 },
    },
  };
}

function graph(): GraphResponse {
  return {
    schema_version: 1,
    revision_id: "rev-1",
    revision_digest: "b".repeat(64),
    nodes: [node()],
    edges: [],
    blockers: [],
    total_nodes: 1,
    total_edges: 0,
    truncated: false,
  };
}

function node(): GraphNode {
  return {
    id: "runtime:degraded",
    kind: "runtime",
    label: "Degraded runtime",
    owner_class: "core",
    generation_scope: "stable",
    state: { degraded: true },
    evidence_refs: [],
  };
}

function installSdk(fetchJSON: ReturnType<typeof vi.fn> = vi.fn(<T,>(path: string) => {
  if (path.includes("/graph")) return Promise.resolve(graph() as T);
  return new Promise<T>(() => {});
})) {
  Object.assign(window, {
    __HERMES_PLUGIN_SDK__: {
      React,
      hooks: React,
      fetchJSON,
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
  });
}

async function render(element: React.ReactElement): Promise<HTMLElement> {
  const container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => {
    root?.render(element);
    await Promise.resolve();
    await Promise.resolve();
  });
  return container;
}

function button(container: HTMLElement, name: string): HTMLButtonElement {
  const element = [...container.querySelectorAll("button")].find(candidate => candidate.textContent === name);
  if (!(element instanceof HTMLButtonElement)) throw new Error(`Missing button: ${name}`);
  return element;
}

function keydown(target: HTMLElement, key: string, shiftKey = false) {
  target.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, key, shiftKey }));
}

afterEach(async () => {
  await act(async () => { root?.unmount(); });
  root = null;
  document.body.replaceChildren();
  cytoscapeMock.factory.mockClear();
  vi.resetModules();
  if (originalWindow === undefined) Reflect.deleteProperty(globalThis, "window");
  else Object.defineProperty(globalThis, "window", originalWindow);
});

describe("Evolution dashboard accessibility", () => {
  it("sends every selected server-supported kind together, including a capability and provider filter", async () => {
    const paths: string[] = [];
    const fetchJSON = vi.fn(<T,>(path: string) => {
      paths.push(path);
      if (path.includes("/graph")) return Promise.resolve(graph() as T);
      return new Promise<T>(() => {});
    });
    installSdk(fetchJSON);
    const { OrganismView } = await import("../../../plugins/evolution/dashboard/src/components/OrganismView");
    const container = await render(<OrganismView snapshot={snapshot()} onRefresh={vi.fn().mockResolvedValue(undefined)} onTrackJob={vi.fn()} />);
    const checkboxes = [...container.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')];

    await act(async () => {
      checkboxes[0]?.click();
      checkboxes[5]?.click();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(paths.at(-1)).toBe("/api/plugins/evolution/graph?limit=200&kind=capability&kind=provider&expected_revision=rev-1");
  });

  it("labels a truncated audit response as bounded history rather than recent activity", async () => {
    installSdk();
    const { AuditTimeline } = await import("../../../plugins/evolution/dashboard/src/components/AuditTimeline");
    const container = await render(<AuditTimeline audit={{ schema_version: 1, state: "ready", events: [], total_events: 2, truncated: true, next_after: 1, mutable_actions: [] }} loading={false} error={null} />);

    expect(container.textContent).toContain("Bounded audit history");
    expect(container.textContent).toContain("earliest available events");
    expect(container.textContent).not.toContain("Recent audit activity");
  });

  it("keeps the host page heading unique while exposing the plugin title as a section heading", async () => {
    installSdk();
    const { EvolutionShell } = await import("../../../plugins/evolution/dashboard/src/components/EvolutionShell");
    const container = await render(
      <>
        <h1>Evolution</h1>
        <EvolutionShell />
      </>,
    );

    expect(container.querySelectorAll("h1")).toHaveLength(1);
    expect(container.querySelector("h1")?.textContent).toBe("Evolution");
    expect(container.querySelectorAll(".evo-shell h1")).toHaveLength(0);
    expect(container.querySelector(".evo-shell__title")?.tagName).toBe("H2");
    expect(container.querySelector(".evo-shell__title")?.textContent).toBe("Evolution");
    const navigation = container.querySelector('[role="tablist"][aria-label="Evolution views"]');
    expect(navigation).not.toBeNull();
    expect(navigation?.querySelectorAll('[role="tab"]')).toHaveLength(4);
    expect(navigation?.querySelectorAll('[role="tab"][aria-selected="true"]')).toHaveLength(1);
    expect(container.querySelectorAll('div[role="button"], section[role="button"], aside[role="button"]')).toHaveLength(0);
  });

  it("offers selected graph and list tabs so the same organism data has a structured fallback", async () => {
    installSdk();
    const { OrganismView } = await import("../../../plugins/evolution/dashboard/src/components/OrganismView");
    const container = await render(<OrganismView snapshot={snapshot()} onRefresh={vi.fn().mockResolvedValue(undefined)} onTrackJob={vi.fn()} />);

    const presentation = container.querySelector('[role="tablist"][aria-label="Organism presentation"]');
    expect(presentation).not.toBeNull();
    expect(presentation?.querySelector('[role="tab"][aria-selected="true"]')?.textContent).toBe("Graph");
    expect(container.querySelector('[role="application"][aria-label="Interactive organism graph"]')).not.toBeNull();

    await act(async () => { button(container, "List").click(); });

    expect(presentation?.querySelector('[role="tab"][aria-selected="true"]')?.textContent).toBe("List");
    expect(container.querySelector('[aria-label="Organism graph as a list"]')).not.toBeNull();

    const nodeControl = [...container.querySelectorAll<HTMLButtonElement>(".evo-organism-list__select")].find(item => item.textContent?.includes("Degraded runtime"));
    if (nodeControl === undefined) throw new Error("Missing structured node control");
    nodeControl.focus();
    await act(async () => { nodeControl.click(); });
    const inspector = container.querySelector("#evo-node-inspector");
    expect(inspector?.getAttribute("data-drawer-open")).toBe("true");
    await act(async () => { button(container, "Close node inspector").click(); });
    expect(inspector?.getAttribute("data-drawer-open")).toBe("false");
    expect(document.activeElement).toBe(nodeControl);
  });

  it("announces selected node health in text instead of conveying it only by the status color", async () => {
    installSdk();
    const { NodeInspector } = await import("../../../plugins/evolution/dashboard/src/components/NodeInspector");
    const container = await render(<NodeInspector node={node()} nodes={[node()]} edges={[]} blockers={[]} />);

    const health = container.querySelector('[role="status"]');
    expect(health).not.toBeNull();
    expect(health?.textContent).toContain("Health: degraded");
  });

  it("announces the selected graph node health alongside its colored graph treatment", async () => {
    installSdk();
    const { OrganismGraph } = await import("../../../plugins/evolution/dashboard/src/components/OrganismGraph");
    const container = await render(<OrganismGraph nodes={[node()]} edges={[]} selectedId={node().id} onSelect={vi.fn()} onOpenInspector={vi.fn()} />);

    const selectionStatus = container.querySelector('[role="status"]');
    expect(selectionStatus).not.toBeNull();
    expect(selectionStatus?.textContent).toContain("Selected node Degraded runtime. Health: degraded.");
  });

  it("gives revision dialogs a description and retains focus inside until Escape restores the trigger", async () => {
    installSdk();
    const { RevisionDialog } = await import("../../../plugins/evolution/dashboard/src/components/RevisionDialog");
    const trigger = document.createElement("button");
    trigger.textContent = "Open rebuild";
    document.body.append(trigger);
    trigger.focus();
    const onClose = vi.fn();
    const context: MutationContext = { organism_id: "00000000-0000-4000-8000-000000000000", expected_snapshot_digest: "a".repeat(64) };
    const props = {
      mode: "rebuild" as const,
      context,
      onClose,
      onJobStarted: vi.fn(),
      returnFocusRef: { current: trigger },
    };
    const container = await render(<RevisionDialog {...props} />);
    const dialog = container.querySelector('[role="dialog"]');
    if (!(dialog instanceof HTMLElement)) throw new Error("Missing revision dialog");

    expect(dialog.getAttribute("aria-labelledby")).toBeTruthy();
    expect(dialog.getAttribute("aria-describedby")).toBeTruthy();
    expect(dialog.contains(document.activeElement)).toBe(true);
    const controls = [...dialog.querySelectorAll<HTMLButtonElement>("button:not([disabled])")];
    controls.at(-1)?.focus();
    keydown(dialog, "Tab");
    expect(document.activeElement).toBe(controls[0]);
    keydown(dialog, "Escape");
    expect(onClose).toHaveBeenCalledOnce();
    expect(document.activeElement).toBe(trigger);
  });

  it("keeps consequential Telos confirmation dialogs named, described, cancellable, and keyboard-contained", async () => {
    installSdk();
    const { StrongConfirmationDialog } = await import("../../../plugins/evolution/dashboard/src/components/StrongConfirmationDialog");
    const trigger = document.createElement("button");
    trigger.textContent = "Open Telos confirmation";
    document.body.append(trigger);
    trigger.focus();
    const onClose = vi.fn();
    const props = {
      action: "activate" as const,
      currentDigest: "a".repeat(64),
      organismId: "00000000-0000-4000-8000-000000000000",
      onClose,
      onConfirmed: vi.fn().mockResolvedValue(undefined),
      onStale: vi.fn().mockResolvedValue(undefined),
      returnFocusRef: { current: trigger },
      targetDigest: "b".repeat(64),
    };
    const container = await render(<StrongConfirmationDialog {...props} />);
    const dialog = container.querySelector('[role="dialog"]');
    if (!(dialog instanceof HTMLElement)) throw new Error("Missing confirmation dialog");

    expect(dialog.getAttribute("aria-labelledby")).toBeTruthy();
    expect(dialog.getAttribute("aria-describedby")).toBeTruthy();
    expect(button(container, "Cancel").disabled).toBe(false);
    expect(dialog.contains(document.activeElement)).toBe(true);
    const controls = [...dialog.querySelectorAll<HTMLButtonElement>("button:not([disabled])")];
    controls.at(-1)?.focus();
    keydown(dialog, "Tab");
    expect(document.activeElement).toBe(controls[0]);
    keydown(dialog, "Escape");
    expect(onClose).toHaveBeenCalledOnce();
    expect(document.activeElement).toBe(trigger);
  });
});
