// @vitest-environment jsdom
import React from "react";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { TelosTransitionPreparation } from "../../../plugins/evolution/dashboard/src/types";

const originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
let root: Root | null = null;

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

function button(label: string): HTMLButtonElement {
  const element = [...document.querySelectorAll("button")].find(candidate => candidate.textContent === label);
  if (!(element instanceof HTMLButtonElement)) throw new Error(`Missing button: ${label}`);
  return element;
}

function installSdk(fetchJSON: ReturnType<typeof vi.fn>) {
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

afterEach(() => {
  root?.unmount();
  root = null;
  document.body.replaceChildren();
  vi.useRealTimers();
  vi.resetModules();
  if (originalWindow !== undefined) Object.defineProperty(globalThis, "window", originalWindow);
});

describe("StrongConfirmationDialog", () => {
  it("transitions to expired at the server deadline without an unrelated rerender", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-28T12:00:00.000Z"));
    const expiresAt = new Date(Date.now() + 1_000).toISOString();
    const preparation: TelosTransitionPreparation = {
      confirmation_id: "confirm-1",
      display_nonce: "nonce-1",
      organism_id: "00000000-0000-4000-8000-000000000000",
      current_digest: "a".repeat(64),
      target_digest: "b".repeat(64),
      action: "activate",
      expires_at: expiresAt,
      required_phrase: "ACTIVATE nonce-1",
    };
    const fetchJSON = vi.fn((path: string) => {
      if (path.endsWith("/mutation-context")) return Promise.resolve({ organism_id: preparation.organism_id, expected_snapshot_digest: "c".repeat(64) });
      if (path.endsWith("/telos/transitions/prepare")) return Promise.resolve(preparation);
      if (path.endsWith("/telos/transitions/confirm")) return Promise.resolve({ status: "approved" });
      throw new Error(`Unexpected request: ${path}`);
    });
    installSdk(fetchJSON);

    const { StrongConfirmationDialog } = await import("../../../plugins/evolution/dashboard/src/components/StrongConfirmationDialog");
    const onClose = vi.fn();
    const container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    await act(async () => {
      root?.render(<StrongConfirmationDialog
        action="activate"
        currentDigest={preparation.current_digest}
        organismId={preparation.organism_id}
        onClose={onClose}
        onConfirmed={vi.fn().mockResolvedValue(undefined)}
        onStale={vi.fn().mockResolvedValue(undefined)}
        targetDigest={preparation.target_digest}
      />);
    });

    await act(async () => {
      button("Prepare confirmation").click();
    });
    const input = document.querySelector("input");
    if (!(input instanceof HTMLInputElement)) throw new Error("Missing phrase input");
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(input, preparation.required_phrase);
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(button("Confirm activate").disabled).toBe(false);

    act(() => {
      vi.advanceTimersByTime(1_000);
    });

    expect(document.querySelector("[role=alert]")?.textContent).toBe("This confirmation expired. Close it and prepare a new transition.");
    expect(button("Confirm activate").disabled).toBe(true);
    expect(button("Cancel").disabled).toBe(false);
    button("Confirm activate").click();
    expect(fetchJSON).not.toHaveBeenCalledWith(expect.stringContaining("/telos/transitions/confirm"), expect.anything());

    button("Cancel").click();
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
