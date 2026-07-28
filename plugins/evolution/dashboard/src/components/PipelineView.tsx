import { evolutionApi } from "../api";
import { fixedPipelineStages, publicResearchBrief } from "../pipeline-model";
import { React, SDK } from "../sdk";
import type { AuditResponse, EvolutionSnapshot, PipelineBlueprint, PipelineResponse, PipelineSuggestion } from "../types";
import { BlueprintInspector } from "./BlueprintInspector";
import { ExpandableText } from "./ExpandableText";
import { PipelineStages } from "./PipelineStages";
import { SuggestionInspector } from "./SuggestionInspector";

void React;

const PIPELINE_LIMIT = 50;
const AUDIT_LIMIT = 100;
const CONFLICT_MESSAGE = "The organism changed elsewhere. Refresh manually before continuing.";
const RESEARCH_HANDOFF_DELAY_MS = 750;

export interface PipelineViewProps {
  snapshot: EvolutionSnapshot | null;
  onRefresh(): Promise<void>;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "The requested local pipeline data is unavailable.";
}

function isConflict(error: unknown): boolean {
  if (typeof error === "object" && error !== null && "status" in error) return Reflect.get(error, "status") === 409;
  return error instanceof Error && /(^|\s)409(?::|\s|$)/.test(error.message);
}

function selectedById<T extends { suggestion_id: string }>(items: readonly T[], id: string | null): T | null {
  return items.find(item => item.suggestion_id === id) ?? items[0] ?? null;
}

function selectedBlueprint(items: readonly PipelineBlueprint[], id: string | null): PipelineBlueprint | null {
  return items.find(item => item.blueprint_id === id) ?? items[0] ?? null;
}

async function writeResearchBrief(text: string): Promise<void> {
  if (typeof navigator === "undefined" || navigator.clipboard === undefined) {
    throw new Error("Clipboard access is unavailable. Copy the public research brief manually.");
  }
  await navigator.clipboard.writeText(text);
}

export function scheduleResearchHandoff(
  navigate: (destination: string) => void,
  destination: string,
  delay = RESEARCH_HANDOFF_DELAY_MS,
): () => void {
  let cancelled = false;
  const timer = window.setTimeout(() => {
    if (!cancelled) navigate(destination);
  }, delay);
  return () => {
    cancelled = true;
    window.clearTimeout(timer);
  };
}

export function PipelineView({ snapshot, onRefresh }: PipelineViewProps): React.ReactElement {
  const { useCallback, useEffect, useMemo, useRef, useState } = SDK.hooks;
  const [attemptId, setAttemptId] = useState<string | null>(null);
  const [pipeline, setPipeline] = useState<PipelineResponse | null>(null);
  const [audit, setAudit] = useState<AuditResponse | null>(null);
  const [selectedSuggestionId, setSelectedSuggestionId] = useState<string | null>(null);
  const [selectedBlueprintId, setSelectedBlueprintId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [mutating, setMutating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const cancelResearchHandoffRef = useRef<(() => void) | null>(null);

  useEffect(() => () => cancelResearchHandoffRef.current?.(), []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [nextPipeline, nextAudit] = await Promise.all([
        evolutionApi.pipeline(attemptId ?? undefined, PIPELINE_LIMIT),
        evolutionApi.audit(undefined, AUDIT_LIMIT),
      ]);
      setPipeline(nextPipeline);
      setAudit(nextAudit);
      setSelectedSuggestionId(current => selectedById(nextPipeline.suggestions, current)?.suggestion_id ?? null);
      setSelectedBlueprintId(current => selectedBlueprint(nextPipeline.blueprints, current)?.blueprint_id ?? null);
      setError(null);
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setLoading(false);
    }
  }, [attemptId]);

  useEffect(() => { void load(); }, [load, snapshot?.snapshot_digest]);

  const unsafe = snapshot?.state === "blocked" || snapshot?.state === "corrupt" || snapshot?.pipeline.state === "blocked" || snapshot?.pipeline.state === "corrupt";
  const suggestion = selectedById(pipeline?.suggestions ?? [], selectedSuggestionId);
  const blueprint = selectedBlueprint(pipeline?.blueprints ?? [], selectedBlueprintId);
  const stages = useMemo(() => pipeline === null ? [] : fixedPipelineStages(pipeline), [pipeline]);

  const createBlueprint = useCallback(async (candidate: PipelineSuggestion) => {
    if (mutating || unsafe) return;
    setMutating(true);
    setError(null);
    try {
      // The mutable request is built immediately before the call: it always
      // uses the full server-issued organism and snapshot context plus the
      // exact immutable digest currently displayed for this suggestion.
      const context = await evolutionApi.mutationContext();
      const created = await evolutionApi.createBlueprint(candidate.suggestion_id, {
        ...context,
        expected_suggestion_digest: candidate.suggestion_digest,
      });
      setSelectedBlueprintId(created.blueprint_id);
      await onRefresh();
      await load();
      setToast(created.status === "existing" ? "Existing immutable blueprint displayed." : "Immutable blueprint created and displayed.");
    } catch (nextError) {
      if (isConflict(nextError)) {
        await onRefresh();
        await load();
        setError(CONFLICT_MESSAGE);
      } else {
        setError(errorMessage(nextError));
      }
    } finally {
      setMutating(false);
    }
  }, [load, mutating, onRefresh, unsafe]);

  const research = useCallback(async (candidate: PipelineSuggestion) => {
    setError(null);
    try {
      const brief = publicResearchBrief(candidate);
      await writeResearchBrief(brief.text);
      setToast(brief.toast);
      cancelResearchHandoffRef.current?.();
      cancelResearchHandoffRef.current = scheduleResearchHandoff(destination => window.location.assign(destination), brief.destination);
    } catch (nextError) {
      setError(errorMessage(nextError));
    }
  }, []);

  if (snapshot === null || loading) return <section className="evo-pipeline" aria-busy="true"><p>Loading bounded local pipeline data…</p></section>;
  if (unsafe) return <section className="evo-pipeline" aria-live="polite"><h2>Pipeline is unavailable</h2><p>Pipeline details and mutable actions are hidden until local diagnostics are safe.</p></section>;
  if (pipeline === null) return <section className="evo-pipeline"><p role="alert">{error ?? "Pipeline data is unavailable."}</p></section>;

  return (
    <section className="evo-pipeline" aria-labelledby="evo-pipeline-heading">
      <header>
        <h2 id="evo-pipeline-heading">Evolution pipeline</h2>
        <p>All displayed records are bounded local projections. Blueprint documents are immutable.</p>
      </header>
      {error !== null ? <p role="alert">{error}</p> : null}
      {toast !== null ? <p role="status" aria-live="polite">{toast}</p> : null}
      <PipelineStages stages={stages} />
      <label>
        Attempt
        <select value={attemptId ?? ""} onChange={event => setAttemptId(event.target.value || null)}>
          <option value="">Latest local attempt</option>
          {pipeline.attempts.map(attempt => <option key={attempt.attempt_id} value={attempt.attempt_id}>{attempt.attempt_id} · {attempt.state}</option>)}
        </select>
      </label>
      {pipeline.attempts_truncated ? <p>Only the latest bounded attempts are available for selection.</p> : null}
      <div className="evo-pipeline__content">
        <section aria-labelledby="evo-pipeline-suggestions-heading">
          <h3 id="evo-pipeline-suggestions-heading">Suggestions</h3>
          {pipeline.suggestions.length === 0 ? <p>No local suggestions are available for this attempt.</p> : (
            <ul>{pipeline.suggestions.map(item => <li key={item.suggestion_id}><button type="button" aria-pressed={item.suggestion_id === suggestion?.suggestion_id} onClick={() => setSelectedSuggestionId(item.suggestion_id)}>{item.state} · score {item.score.toFixed(2)}</button></li>)}</ul>
          )}
          {pipeline.suggestions_truncated ? <p>Suggestions are capped to this bounded local view.</p> : null}
        </section>
        <SuggestionInspector suggestion={suggestion} blueprints={pipeline.blueprints} disabled={mutating} onCreateBlueprint={candidate => void createBlueprint(candidate)} onResearch={candidate => void research(candidate)} />
        <section aria-labelledby="evo-pipeline-blueprints-heading">
          <h3 id="evo-pipeline-blueprints-heading">Blueprints</h3>
          {pipeline.blueprints.length === 0 ? <p>No immutable blueprint has been created for this attempt.</p> : (
            <ul>{pipeline.blueprints.map(item => <li key={item.blueprint_id}><button type="button" aria-pressed={item.blueprint_id === blueprint?.blueprint_id} onClick={() => setSelectedBlueprintId(item.blueprint_id)}>{item.blueprint_id} · {item.state}</button></li>)}</ul>
          )}
          {pipeline.blueprints_truncated ? <p>Blueprints are capped to this bounded local view.</p> : null}
        </section>
        <BlueprintInspector blueprint={blueprint} auditEvents={audit?.events ?? []} />
      </div>
      <section aria-labelledby="evo-pipeline-audit-heading">
        <h3 id="evo-pipeline-audit-heading">Append-only audit history</h3>
        {audit === null || audit.events.length === 0 ? <p>No durable audit events are available.</p> : <ol>{audit.events.map(event => <li key={event.event_id}>#{event.sequence} · <ExpandableText text={event.summary} label="audit summary" /></li>)}</ol>}
        {audit?.truncated ? <p>Only a bounded append-only audit prefix is displayed.</p> : null}
      </section>
    </section>
  );
}
