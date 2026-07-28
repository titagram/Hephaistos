import { evolutionApi } from "./api";
import { SDK } from "./sdk";
import type { EvolutionJob, EvolutionSnapshot } from "./types";
import {
  isActiveJobState,
  snapshotAfterRefreshFailure,
  warningForRefreshFailure,
  type RefreshWarning,
} from "./view-model";

const SNAPSHOT_POLL_INTERVAL_MS = 30_000;
const JOB_POLL_INTERVAL_MS = 3_000;

export interface EvolutionSnapshotStore {
  snapshot: EvolutionSnapshot | null;
  loading: boolean;
  refreshing: boolean;
  warning: RefreshWarning | null;
  activeJob: EvolutionJob | null;
  refresh(): Promise<void>;
  trackJob(job: EvolutionJob): void;
}

function documentIsVisible(): boolean {
  return typeof document === "undefined" || document.visibilityState === "visible";
}

export function useEvolutionSnapshot(): EvolutionSnapshotStore {
  const { useCallback, useEffect, useRef, useState } = SDK.hooks;
  const [snapshot, setSnapshot] = useState<EvolutionSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [warning, setWarning] = useState<RefreshWarning | null>(null);
  const [activeJob, setActiveJob] = useState<EvolutionJob | null>(null);
  const lastValidRef = useRef<EvolutionSnapshot | null>(null);
  const warningRef = useRef<RefreshWarning | null>(null);
  const refreshInFlightRef = useRef(false);

  const refresh = useCallback(async (): Promise<void> => {
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

  const trackJob = useCallback((job: EvolutionJob) => {
    setActiveJob(isActiveJobState(job.state) ? job : null);
  }, []);

  return { snapshot, loading, refreshing, warning, activeJob, refresh, trackJob };
}
