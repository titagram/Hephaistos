import { afterEach, describe, expect, it, vi } from "vitest";

interface FetchCall {
  path: string;
  init: RequestInit | undefined;
}

const originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window");

afterEach(() => {
  if (originalWindow === undefined) {
    Reflect.deleteProperty(globalThis, "window");
  } else {
    Object.defineProperty(globalThis, "window", originalWindow);
  }
  vi.resetModules();
});

async function loadApi(calls: FetchCall[]) {
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: {
      __HERMES_PLUGIN_SDK__: {
        React: {},
        hooks: {},
        fetchJSON<T>(path: string, init?: RequestInit): Promise<T> {
          calls.push({ path, init });
          return Promise.resolve({} as T);
        },
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
  return import("../../../plugins/evolution/dashboard/src/api");
}

describe("Evolution dashboard API client", () => {
  it("uses the host JSON client for every bounded read route", async () => {
    const calls: FetchCall[] = [];
    const { evolutionApi } = await loadApi(calls);

    await evolutionApi.snapshot();
    await evolutionApi.mutationContext();
    await evolutionApi.graph({ rootId: "capability:alpha", depth: 2, limit: 50, kinds: ["capability", "runtime"], search: "alpha", expectedRevision: "rev-1" });
    await evolutionApi.revisions(20);
    await evolutionApi.diff("rev-1", "rev-2");
    await evolutionApi.telos(20);
    await evolutionApi.pipeline("attempt-1", 20);
    await evolutionApi.audit(5, 20);
    await evolutionApi.job("00000000-0000-4000-8000-000000000000");

    expect(calls.map(call => call.path)).toEqual([
      "/api/plugins/evolution/snapshot",
      "/api/plugins/evolution/mutation-context",
      "/api/plugins/evolution/graph?root_id=capability%3Aalpha&depth=2&limit=50&kind=capability&kind=runtime&search=alpha&expected_revision=rev-1",
      "/api/plugins/evolution/revisions?limit=20",
      "/api/plugins/evolution/diff?left=rev-1&right=rev-2",
      "/api/plugins/evolution/telos?history_limit=20",
      "/api/plugins/evolution/pipeline?attempt_id=attempt-1&limit=20",
      "/api/plugins/evolution/audit?after=5&limit=20",
      "/api/plugins/evolution/jobs/00000000-0000-4000-8000-000000000000",
    ]);
    expect(calls.every(call => call.init?.headers !== undefined || call.init === undefined)).toBe(true);
  });

  it("submits every mutation through the host JSON client without a session token", async () => {
    const calls: FetchCall[] = [];
    const { evolutionApi } = await loadApi(calls);
    const context = { organism_id: "00000000-0000-4000-8000-000000000000", expected_snapshot_digest: "a".repeat(64) };
    const document = {
      schema_version: 1 as const,
      organism_id: context.organism_id,
      parent_digest: null,
      purpose: "Operate safely.",
      desired_traits: [], capability_directions: [], priorities: [], tradeoffs: [], prohibitions: [],
      proactivity_policy: { id: "safe", statement: "Stay safe.", tags: [], priority: 1 },
      success_indicators: [],
    };

    await evolutionApi.initialize();
    await evolutionApi.rebuild({ ...context, force: false, collectors: [] });
    await evolutionApi.observerScan(context);
    await evolutionApi.setObserver({ ...context, enabled: true });
    await evolutionApi.saveTelosDraft({ ...context, document });
    await evolutionApi.prepareTelosTransition({ current_digest: "b".repeat(64), target_digest: "c".repeat(64), action: "activate" });
    await evolutionApi.confirmTelosTransition({ confirmation_id: context.organism_id, current_digest: "b".repeat(64), target_digest: "c".repeat(64), action: "activate", phrase: "ACTIVATE" });
    await evolutionApi.createBlueprint(context.organism_id, { ...context, expected_suggestion_digest: "d".repeat(64) });

    expect(calls.map(call => call.path)).toEqual([
      "/api/plugins/evolution/initialize",
      "/api/plugins/evolution/jobs/organism-rebuild",
      "/api/plugins/evolution/jobs/observer-scan",
      "/api/plugins/evolution/observer",
      "/api/plugins/evolution/telos/drafts",
      "/api/plugins/evolution/telos/transitions/prepare",
      "/api/plugins/evolution/telos/transitions/confirm",
      "/api/plugins/evolution/suggestions/00000000-0000-4000-8000-000000000000/blueprint",
    ]);
    expect(calls.every(call => call.init?.method === "POST")).toBe(true);
    expect(JSON.stringify(calls)).not.toContain("__HERMES_SESSION_TOKEN__");
  });
});
