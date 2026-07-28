import { evolutionApi } from "../api";
import { React, SDK } from "../sdk";
import type { AuditResponse, EvolutionJob, EvolutionSnapshot, EvolutionView, PipelineResponse } from "../types";
import { observerControl, overviewPrimaryAction } from "../view-model";
import { AuditTimeline } from "./AuditTimeline";
import { ReadinessSummary } from "./ReadinessSummary";

void React;

const AUDIT_LIMIT = 12;
const CONFLICT_MESSAGE = "The organism changed elsewhere. Refresh manually before continuing.";

export interface OverviewViewProps {
  snapshot: EvolutionSnapshot | null;
  onRefresh(): Promise<void>;
  onTrackJob(job: EvolutionJob): void;
  onNavigate(view: EvolutionView): void;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "The requested local evolution data is unavailable.";
}

function isConflict(error: unknown): boolean {
  if (typeof error === "object" && error !== null && "status" in error) return Reflect.get(error, "status") === 409;
  return error instanceof Error && /(^|\s)409(?::|\s|$)/.test(error.message);
}

function eligibleSuggestion(pipeline: PipelineResponse | null): string | null {
  const suggestion = pipeline?.suggestions.find(item => item.state === "eligible") ?? null;
  return suggestion === null ? null : suggestion.summary;
}

export function OverviewView({ snapshot, onRefresh, onTrackJob, onNavigate }: OverviewViewProps): React.ReactElement {
  const { useCallback, useEffect, useState } = SDK.hooks;
  const [pipeline, setPipeline] = useState<PipelineResponse | null>(null);
  const [audit, setAudit] = useState<AuditResponse | null>(null);
  const [loadingDetails, setLoadingDetails] = useState(false);
  const [detailsError, setDetailsError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [mutating, setMutating] = useState(false);

  useEffect(() => {
    let current = true;
    setLoadingDetails(true);
    setDetailsError(null);
    void Promise.all([evolutionApi.pipeline(undefined, AUDIT_LIMIT), evolutionApi.audit(undefined, AUDIT_LIMIT)])
      .then(([nextPipeline, nextAudit]) => {
        if (!current) return;
        setPipeline(nextPipeline);
        setAudit(nextAudit);
      })
      .catch(error => {
        if (current) setDetailsError(errorMessage(error));
      })
      .finally(() => { if (current) setLoadingDetails(false); });
    return () => { current = false; };
  }, [snapshot?.snapshot_digest]);

  const resolveConflict = useCallback(async () => {
    await onRefresh();
    setActionError(CONFLICT_MESSAGE);
  }, [onRefresh]);

  const mutateObserver = useCallback(async (enabled: boolean) => {
    if (mutating) return;
    setMutating(true);
    setActionError(null);
    try {
      const context = await evolutionApi.mutationContext();
      await evolutionApi.setObserver({
        organism_id: context.organism_id,
        expected_snapshot_digest: context.expected_snapshot_digest,
        enabled,
      });
      await onRefresh();
    } catch (error) {
      if (isConflict(error)) await resolveConflict();
      else setActionError(errorMessage(error));
    } finally {
      setMutating(false);
    }
  }, [mutating, onRefresh, resolveConflict]);

  const runScan = useCallback(async () => {
    if (mutating || snapshot === null || !snapshot.observer.enabled) return;
    setMutating(true);
    setActionError(null);
    try {
      const context = await evolutionApi.mutationContext();
      const job = await evolutionApi.observerScan({
        organism_id: context.organism_id,
        expected_snapshot_digest: context.expected_snapshot_digest,
      });
      onTrackJob(job);
      await onRefresh();
    } catch (error) {
      if (isConflict(error)) await resolveConflict();
      else setActionError(errorMessage(error));
    } finally {
      setMutating(false);
    }
  }, [mutating, onRefresh, onTrackJob, resolveConflict, snapshot]);

  const initialize = useCallback(async () => {
    if (mutating) return;
    setMutating(true);
    setActionError(null);
    try {
      await evolutionApi.initialize();
      await onRefresh();
    } catch (error) {
      setActionError(errorMessage(error));
    } finally {
      setMutating(false);
    }
  }, [mutating, onRefresh]);

  const primary = snapshot === null ? null : overviewPrimaryAction(snapshot);
  const observer = snapshot === null ? null : observerControl(snapshot);
  const runPrimary = () => {
    if (primary?.action === "initialize") void initialize();
    if (primary?.action === "scan") void runScan();
    if (primary?.action === "resume") void mutateObserver(true);
  };

  return (
    <section className="evo-overview" aria-label="Evolution readiness overview">
      <ReadinessSummary snapshot={snapshot} onNavigate={onNavigate} />
      {snapshot?.state === "corrupt" ? (
        <section className="evo-diagnostics" aria-labelledby="evo-diagnostics-heading">
          <h2 id="evo-diagnostics-heading">Local diagnostics</h2>
          <p>Mutations are unavailable while local diagnostics report corruption.</p>
          {snapshot.diagnostics.length > 0 ? <ul>{snapshot.diagnostics.map(diagnostic => <li key={diagnostic}>{diagnostic}</li>)}</ul> : null}
        </section>
      ) : null}
      {primary !== null ? (
        <section className="evo-overview__actions" aria-label="Evolution actions">
          <button className="evo-action--primary" type="button" onClick={runPrimary} disabled={mutating}>
            {mutating ? "Working…" : primary.label}
          </button>
          {observer !== null && observer.action !== primary.action ? (
            <button type="button" onClick={() => void mutateObserver(observer.action === "resume")} disabled={mutating}>
              {observer.label}
            </button>
          ) : null}
          {snapshot !== null && !snapshot.observer.enabled ? <p>Observer scans are unavailable while the observer is paused.</p> : null}
        </section>
      ) : null}
      {actionError !== null ? <p role="alert">{actionError}</p> : null}
      <section className="evo-pipeline-summary" aria-labelledby="evo-pipeline-heading">
        <h2 id="evo-pipeline-heading">Pipeline</h2>
        {loadingDetails ? <p role="status">Loading bounded pipeline data…</p> : null}
        {detailsError !== null ? <p role="status">{detailsError}</p> : null}
        {!loadingDetails && detailsError === null ? (
          <>
            <p>{eligibleSuggestion(pipeline) === null ? "No eligible suggestion is available." : `Eligible suggestion: ${eligibleSuggestion(pipeline)}`}</p>
            <p>Durable pending decisions: {snapshot?.pipeline.lifecycle.pending_approval_count ?? "unavailable"}</p>
          </>
        ) : null}
      </section>
      <AuditTimeline audit={audit} loading={loadingDetails} error={detailsError} />
    </section>
  );
}
