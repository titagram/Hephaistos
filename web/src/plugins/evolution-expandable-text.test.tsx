// @vitest-environment jsdom
import React from "react";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AuditEvent, PipelineBlueprint } from "../../../plugins/evolution/dashboard/src/types";

let root: Root | null = null;

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

function installSdk() {
  Object.assign(window, {
    __HERMES_PLUGIN_SDK__: {
      React,
      hooks: React,
      fetchJSON: vi.fn(),
      components: {
        Badge: () => null, Button: () => null, Checkbox: () => null, Input: () => null,
        Label: () => null, Select: () => null, SelectOption: () => null, Separator: () => null,
      },
      utils: { cn: () => "", timeAgo: () => "", isoTimeAgo: () => "" },
    },
  });
}

const longSummary = "A".repeat(500);
const event = (sequence: number): AuditEvent => ({
  sequence, event_id: `event-${sequence}`, attempt_id: "attempt-1", generation_id: null, event_type: "authorized",
  prior_state: "eligible", next_state: "draft", actor: "local", reason_code: "test", summary: `${sequence}:${longSummary}`,
  created_at: "2026-07-28T10:00:00Z", event_digest: "a".repeat(64),
});

const blueprint: PipelineBlueprint = {
  blueprint_id: "blueprint-1", attempt_id: "attempt-1", canonical_digest: "b".repeat(64), state: "draft",
  created_at: "2026-07-28T10:00:00Z", suggestion_id: "suggestion-1", active_telos_digest: "c".repeat(64),
  summary: "summary", capability_hypothesis: "hypothesis", proposed_component_classes: ["skill"],
};

function button(label: string): HTMLButtonElement {
  const element = [...document.querySelectorAll("button")].find(item => item.textContent === label);
  if (!(element instanceof HTMLButtonElement)) throw new Error(`Missing button: ${label}`);
  return element;
}

afterEach(() => {
  root?.unmount();
  root = null;
  document.body.replaceChildren();
  vi.resetModules();
});

describe("bounded audit summaries", () => {
  it("caps authorization summaries, expands the full text, and retains server order", async () => {
    installSdk();
    const { BlueprintInspector } = await import("../../../plugins/evolution/dashboard/src/components/BlueprintInspector");
    const container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    await act(async () => { root?.render(<BlueprintInspector blueprint={blueprint} auditEvents={[event(2), event(1)]} />); });

    const items = [...document.querySelectorAll("#evo-blueprint-auth-heading + ol > li")];
    expect(items.map(item => item.textContent?.slice(0, 2))).toEqual(["#2", "#1"]);
    expect(items[0]?.textContent).not.toContain(longSummary);
    expect(button("Show full authorization summary").getAttribute("aria-expanded")).toBe("false");

    await act(async () => { button("Show full authorization summary").click(); });
    expect(document.querySelector("#evo-blueprint-auth-heading + ol > li")?.textContent).toContain(longSummary);
    expect(button("Show less authorization summary").getAttribute("aria-expanded")).toBe("true");
  });
});
