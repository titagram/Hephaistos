(() => {
  // ../plugins/evolution/dashboard/src/sdk.ts
  function getSdk() {
    const hostSdk = window.__HERMES_PLUGIN_SDK__;
    if (hostSdk === void 0) {
      throw new Error("Hermes plugin SDK is unavailable");
    }
    const {
      Badge,
      Button,
      Checkbox,
      Input,
      Label,
      Select,
      SelectOption,
      Separator
    } = hostSdk.components;
    if (Badge === void 0 || Button === void 0 || Checkbox === void 0 || Input === void 0 || Label === void 0 || Select === void 0 || SelectOption === void 0 || Separator === void 0) {
      throw new Error("Hermes plugin UI components are unavailable");
    }
    return {
      React: hostSdk.React,
      hooks: hostSdk.hooks,
      fetchJSON: hostSdk.fetchJSON,
      components: { Badge, Button, Checkbox, Input, Label, Select, SelectOption, Separator },
      utils: hostSdk.utils
    };
  }
  var SDK = getSdk();
  var React = SDK.React;

  // ../plugins/evolution/dashboard/src/api.ts
  var BASE = "/api/plugins/evolution";
  function getQuery(values) {
    const parameters = new URLSearchParams();
    for (const [key, value] of Object.entries(values)) {
      if (value !== void 0) parameters.set(key, String(value));
    }
    const query = parameters.toString();
    return query === "" ? "" : `?${query}`;
  }
  function graphQuery(query) {
    const parameters = new URLSearchParams();
    if (query.rootId !== void 0) parameters.set("root_id", query.rootId);
    if (query.depth !== void 0) parameters.set("depth", String(query.depth));
    if (query.limit !== void 0) parameters.set("limit", String(query.limit));
    for (const kind of query.kinds ?? []) parameters.append("kind", kind);
    if (query.search !== void 0) parameters.set("search", query.search);
    if (query.expectedRevision !== void 0) parameters.set("expected_revision", query.expectedRevision);
    const encoded = parameters.toString();
    return encoded === "" ? "" : `?${encoded}`;
  }
  function mutate(path, body) {
    return SDK.fetchJSON(`${BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
  }
  var evolutionApi = {
    snapshot: () => SDK.fetchJSON(`${BASE}/snapshot`),
    mutationContext: () => SDK.fetchJSON(`${BASE}/mutation-context`),
    graph: (query = {}) => SDK.fetchJSON(`${BASE}/graph${graphQuery(query)}`),
    revisions: (limit) => SDK.fetchJSON(`${BASE}/revisions${getQuery({ limit })}`),
    diff: (left, right) => SDK.fetchJSON(`${BASE}/diff${getQuery({ left, right })}`),
    telos: (historyLimit) => SDK.fetchJSON(`${BASE}/telos${getQuery({ history_limit: historyLimit })}`),
    pipeline: (attemptId, limit) => SDK.fetchJSON(`${BASE}/pipeline${getQuery({ attempt_id: attemptId, limit })}`),
    audit: (after, limit) => SDK.fetchJSON(`${BASE}/audit${getQuery({ after, limit })}`),
    job: (jobId) => SDK.fetchJSON(`${BASE}/jobs/${jobId}`),
    initialize: () => mutate("/initialize", {}),
    rebuild: (request) => mutate("/jobs/organism-rebuild", request),
    observerScan: (context) => mutate("/jobs/observer-scan", context),
    setObserver: (request) => mutate("/observer", request),
    saveTelosDraft: (request) => mutate("/telos/drafts", request),
    prepareTelosTransition: (request) => mutate("/telos/transitions/prepare", request),
    confirmTelosTransition: (request) => mutate("/telos/transitions/confirm", request),
    createBlueprint: (suggestionId, request) => mutate(`/suggestions/${suggestionId}/blueprint`, request)
  };

  // ../plugins/evolution/dashboard/src/view-model.ts
  var BLOCKER_PRIORITY = {
    corrupt: 6,
    blocked: 5,
    partial: 4,
    stale: 3,
    missing: 2,
    not_ready: 1,
    ready: 0
  };
  var BLOCKER_LABELS = {
    snapshot: "Overall organism state",
    gnothi: "Organism graph",
    telos: "Telos",
    observer: "Observer",
    generations: "Generations",
    pipeline: "Pipeline"
  };
  var BLOCKER_ORDER = [
    "snapshot",
    "gnothi",
    "telos",
    "observer",
    "generations",
    "pipeline"
  ];
  function isNonReady(state) {
    return state !== "ready";
  }
  function stateOfPipeline(pipeline) {
    return pipeline.state;
  }
  function initialView() {
    return "organism";
  }
  function readinessBlockers(snapshot) {
    const sources = [
      ["snapshot", snapshot.state],
      ["gnothi", snapshot.gnothi.state],
      ["telos", snapshot.telos.state],
      ["observer", snapshot.observer.state],
      ["generations", snapshot.generations.state],
      ["pipeline", stateOfPipeline(snapshot.pipeline)]
    ];
    return sources.filter(([, state]) => isNonReady(state)).map(([source, state]) => ({ source, state, label: BLOCKER_LABELS[source] })).sort((left, right) => {
      const priority = BLOCKER_PRIORITY[right.state] - BLOCKER_PRIORITY[left.state];
      return priority !== 0 ? priority : BLOCKER_ORDER.indexOf(left.source) - BLOCKER_ORDER.indexOf(right.source);
    });
  }
  function snapshotAfterRefreshFailure(lastValid) {
    return lastValid;
  }
  function statusFromError(error) {
    if (typeof error === "object" && error !== null && "status" in error) {
      const status = Reflect.get(error, "status");
      return typeof status === "number" ? status : null;
    }
    if (error instanceof Error) {
      const match = /^(\d{3})(?::|\s|$)/.exec(error.message);
      return match === null ? null : Number(match[1]);
    }
    return null;
  }
  function warningForRefreshFailure(error) {
    if (statusFromError(error) === 409) {
      return {
        code: "refresh_required",
        message: "The organism changed elsewhere. Refresh manually before continuing.",
        retryable: false
      };
    }
    return {
      code: "refresh_failed",
      message: "The latest snapshot could not be loaded. The last valid snapshot remains visible.",
      retryable: true
    };
  }
  function organismFacet(snapshot, _profile) {
    return {
      label: "Local organism \xB7 all profiles",
      organism: snapshot.organism
    };
  }
  function isActiveJobState(state) {
    return state === "queued" || state === "running";
  }

  // ../plugins/evolution/dashboard/src/state.ts
  var SNAPSHOT_POLL_INTERVAL_MS = 3e4;
  var JOB_POLL_INTERVAL_MS = 3e3;
  function documentIsVisible() {
    return typeof document === "undefined" || document.visibilityState === "visible";
  }
  function useEvolutionSnapshot() {
    const { useCallback, useEffect, useRef, useState } = SDK.hooks;
    const [snapshot, setSnapshot] = useState(null);
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [warning, setWarning] = useState(null);
    const [activeJob, setActiveJob] = useState(null);
    const lastValidRef = useRef(null);
    const warningRef = useRef(null);
    const refreshInFlightRef = useRef(false);
    const refresh = useCallback(async () => {
      if (refreshInFlightRef.current) return;
      refreshInFlightRef.current = true;
      setRefreshing(true);
      try {
        const next = await evolutionApi.snapshot();
        lastValidRef.current = next;
        warningRef.current = null;
        setSnapshot(next);
        setWarning(null);
      } catch (error) {
        const nextWarning = warningForRefreshFailure(error);
        warningRef.current = nextWarning;
        setSnapshot(snapshotAfterRefreshFailure(lastValidRef.current));
        setWarning(nextWarning);
      } finally {
        refreshInFlightRef.current = false;
        setLoading(false);
        setRefreshing(false);
      }
    }, []);
    useEffect(() => {
      void refresh();
    }, [refresh]);
    useEffect(() => {
      const poll = () => {
        if (!documentIsVisible() || warningRef.current?.code === "refresh_required") return;
        void refresh();
      };
      const interval = globalThis.setInterval(poll, SNAPSHOT_POLL_INTERVAL_MS);
      if (typeof document !== "undefined") {
        document.addEventListener("visibilitychange", poll);
      }
      return () => {
        globalThis.clearInterval(interval);
        if (typeof document !== "undefined") {
          document.removeEventListener("visibilitychange", poll);
        }
      };
    }, [refresh]);
    useEffect(() => {
      if (activeJob === null || !isActiveJobState(activeJob.state)) return;
      const poll = async () => {
        if (!documentIsVisible()) return;
        try {
          const next = await evolutionApi.job(activeJob.job_id);
          setActiveJob(isActiveJobState(next.state) ? next : null);
        } catch (error) {
          const nextWarning = warningForRefreshFailure(error);
          warningRef.current = nextWarning;
          setWarning(nextWarning);
        }
      };
      const interval = globalThis.setInterval(() => void poll(), JOB_POLL_INTERVAL_MS);
      void poll();
      return () => globalThis.clearInterval(interval);
    }, [activeJob]);
    const trackJob = useCallback((job) => {
      setActiveJob(isActiveJobState(job.state) ? job : null);
    }, []);
    return { snapshot, loading, refreshing, warning, activeJob, refresh, trackJob };
  }

  // ../plugins/evolution/dashboard/src/components/StatusRail.tsx
  function humanize(value) {
    return value.replaceAll("_", " ");
  }
  function StatusRail({ snapshot, loading }) {
    if (snapshot === null) {
      return /* @__PURE__ */ React.createElement("section", { className: "evo-status-rail", "aria-label": "Evolution status", "aria-busy": loading }, /* @__PURE__ */ React.createElement("p", null, loading ? "Loading local organism status\u2026" : "No organism status is available."));
    }
    const blockers = readinessBlockers(snapshot);
    return /* @__PURE__ */ React.createElement("section", { className: "evo-status-rail", "aria-label": "Evolution status" }, /* @__PURE__ */ React.createElement("p", null, "Overall status: ", humanize(snapshot.state)), blockers.length === 0 ? /* @__PURE__ */ React.createElement("p", null, "All monitored local organism systems are ready.") : /* @__PURE__ */ React.createElement("ol", null, blockers.map((blocker) => /* @__PURE__ */ React.createElement("li", { key: blocker.source }, blocker.label, ": ", humanize(blocker.state)))));
  }

  // ../plugins/evolution/dashboard/src/components/EvolutionShell.tsx
  var VIEWS = [
    { id: "overview", label: "Overview" },
    { id: "organism", label: "Organism" },
    { id: "telos", label: "Telos" },
    { id: "pipeline", label: "Pipeline" }
  ];
  function EvolutionShell() {
    const { useState } = SDK.hooks;
    const [view, setView] = useState(initialView());
    const store = useEvolutionSnapshot();
    const facet = store.snapshot === null ? null : organismFacet(store.snapshot, null);
    return /* @__PURE__ */ React.createElement("main", { className: "evo-shell" }, /* @__PURE__ */ React.createElement("header", { className: "evo-shell__header" }, /* @__PURE__ */ React.createElement("h1", null, "Evolution"), /* @__PURE__ */ React.createElement("p", null, "Local organism \xB7 all profiles"), facet !== null && facet.organism !== null ? /* @__PURE__ */ React.createElement("p", null, "Organism ", facet.organism.id_prefix, " \xB7 Lineage ", facet.organism.lineage_prefix) : null), /* @__PURE__ */ React.createElement("nav", { className: "evo-shell__nav", "aria-label": "Evolution views" }, VIEWS.map((item) => /* @__PURE__ */ React.createElement(
      "button",
      {
        key: item.id,
        type: "button",
        "aria-current": view === item.id ? "page" : void 0,
        onClick: () => setView(item.id)
      },
      item.label
    ))), store.warning !== null ? /* @__PURE__ */ React.createElement("section", { className: "evo-warning", role: "status", "aria-live": "polite" }, /* @__PURE__ */ React.createElement("p", null, store.warning.message), /* @__PURE__ */ React.createElement("button", { type: "button", onClick: () => void store.refresh(), disabled: store.refreshing }, "Refresh now")) : null, store.activeJob !== null ? /* @__PURE__ */ React.createElement("section", { className: "evo-job-strip", role: "status", "aria-live": "polite" }, /* @__PURE__ */ React.createElement("p", null, store.activeJob.kind.replaceAll("_", " "), ": ", store.activeJob.state, " (", store.activeJob.progress, "%)")) : null, /* @__PURE__ */ React.createElement(StatusRail, { snapshot: store.snapshot, loading: store.loading }), /* @__PURE__ */ React.createElement("section", { className: "evo-shell__content", "aria-label": `${VIEWS.find((item) => item.id === view)?.label ?? "Evolution"} view` }, /* @__PURE__ */ React.createElement("p", null, VIEWS.find((item) => item.id === view)?.label, " view will appear here.")));
  }

  // ../plugins/evolution/dashboard/src/index.tsx
  function EvolutionPlugin() {
    return React.createElement(EvolutionShell);
  }
  var registry = window.__HERMES_PLUGINS__;
  if (registry === void 0) {
    throw new Error("Hermes plugin registry is unavailable");
  }
  registry.register("evolution", EvolutionPlugin);
})();
