import { evolutionApi } from "../api";
import { React, SDK } from "../sdk";
import {
  createTelosDraft,
  serializeTelosDraft,
  staleTransitionRecovery,
  validateTelosDraft,
  type TelosDraft,
} from "../telos-model";
import type { EvolutionSnapshot, TelosResponse, TelosRevision } from "../types";
import { StrongConfirmationDialog } from "./StrongConfirmationDialog";
import { TelosDiff } from "./TelosDiff";
import { TelosEditor } from "./TelosEditor";

void React;

const HISTORY_LIMIT = 50;

export interface TelosViewProps {
  snapshot: EvolutionSnapshot | null;
  onRefresh(): Promise<void>;
}

function errorMessage(error: unknown): string {
  if (error instanceof Error && /^422(?::|\s|$)/.test(error.message)) return "The server rejected the structured Telos document. Correct the highlighted fields and try again.";
  return error instanceof Error ? error.message : "Local Telos data is unavailable.";
}

function draftAsRevision(draft: TelosDraft, digest: string, parentDigest: string | null): TelosRevision {
  return { digest, parent_digest: parentDigest, ...draft };
}

function transitionTarget(response: TelosResponse | null, saved: TelosRevision | null, digest: string | null): TelosRevision | null {
  if (digest === null) return null;
  const revisions = [response?.active_revision, ...(response?.history ?? []), saved];
  return revisions.find(item => item?.digest === digest) ?? null;
}

export function TelosView({ snapshot, onRefresh }: TelosViewProps): React.ReactElement {
  const { useCallback, useEffect, useMemo, useRef, useState } = SDK.hooks;
  const [telos, setTelos] = useState<TelosResponse | null>(null);
  const [draft, setDraft] = useState<TelosDraft | null>(null);
  const [savedRevision, setSavedRevision] = useState<TelosRevision | null>(null);
  const [selectedDigest, setSelectedDigest] = useState<string | null>(null);
  const [dialogAction, setDialogAction] = useState<"activate" | "rollback" | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [warning, setWarning] = useState<string | null>(null);
  const transitionTriggerRef = useRef<HTMLElement | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const next = await evolutionApi.telos(HISTORY_LIMIT);
      setTelos(next);
      setDraft(current => current ?? createTelosDraft(next.active_revision));
      setSelectedDigest(current => current ?? next.active_digest);
      setError(null);
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const unsafe = snapshot?.state === "corrupt" || snapshot?.state === "blocked" || snapshot?.telos.state === "corrupt" || snapshot?.telos.state === "blocked";
  const selected = useMemo(() => transitionTarget(telos, savedRevision, selectedDigest), [savedRevision, selectedDigest, telos]);
  const current = telos?.active_revision ?? null;
  const canTransition = !unsafe && current !== null && selected !== null && selected.digest !== current.digest;

  const recoverStale = useCallback(async (message: string) => {
    setDialogAction(null);
    setWarning(message);
    await onRefresh();
    await load();
  }, [load, onRefresh]);

  const saveDraft = async () => {
    if (draft === null || saving || unsafe) return;
    const errors = validateTelosDraft(draft);
    if (errors.length > 0) {
      setError(errors.join(" "));
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const context = await evolutionApi.mutationContext();
      const document = serializeTelosDraft(draft, context.organism_id, telos?.active_digest ?? null);
      const saved = await evolutionApi.saveTelosDraft({ ...context, document });
      const revision = draftAsRevision(draft, saved.digest, document.parent_digest);
      setSavedRevision(revision);
      setSelectedDigest(saved.digest);
      await onRefresh();
      await load();
    } catch (nextError) {
      const recovery = staleTransitionRecovery(nextError);
      if (recovery !== null) await recoverStale(recovery.warning);
      else setError(errorMessage(nextError));
    } finally {
      setSaving(false);
    }
  };

  if (snapshot === null || loading) return <section className="evo-telos" aria-busy="true"><p>Loading local Telos revisions…</p></section>;
  if (unsafe) return <section className="evo-telos" aria-live="polite"><h2>Telos is unavailable</h2><p>Telos details and changes are hidden until the local organism is safe to inspect.</p></section>;
  if (telos === null || draft === null) return <section className="evo-telos"><p role="alert">{error ?? "Telos data is unavailable."}</p></section>;

  const revisions = [telos.active_revision, ...telos.history, savedRevision].filter((item): item is TelosRevision => item !== null)
    .filter((item, index, items) => items.findIndex(candidate => candidate.digest === item.digest) === index);
  return (
    <section className="evo-telos" aria-labelledby="evo-telos-heading">
      <header>
        <h2 id="evo-telos-heading">Telos</h2>
        <p>Active digest: {telos.active_digest ?? "No active Telos revision"}</p>
      </header>
      {warning !== null ? <p role="status">{warning}</p> : null}
      {error !== null ? <p role="alert">{error}</p> : null}
      <TelosEditor draft={draft} parentDigest={telos.active_digest} disabled={saving} onChange={setDraft} />
      <button type="button" onClick={() => void saveDraft()} disabled={saving}>{saving ? "Saving inert draft…" : "Save inert Telos draft"}</button>
      <section aria-labelledby="evo-telos-history-heading">
        <h3 id="evo-telos-history-heading">Bounded revision history</h3>
        <p>The latest {HISTORY_LIMIT} inactive revisions are available for comparison and transition.</p>
        {telos.truncated ? <p>Additional immutable Telos history exists but is not displayed in this bounded view.</p> : null}
        <label>
          Compare or transition target
          <select value={selectedDigest ?? ""} onChange={event => setSelectedDigest(event.target.value || null)}>
            <option value="">Select an immutable Telos revision</option>
            {revisions.map(revision => <option key={revision.digest} value={revision.digest}>{revision.digest}{revision.digest === telos.active_digest ? " (active)" : ""}</option>)}
          </select>
        </label>
      </section>
      <TelosDiff current={current} target={selected} />
      <div className="evo-telos__actions">
        <button type="button" onClick={event => { transitionTriggerRef.current = event.currentTarget; setDialogAction("activate"); }} disabled={!canTransition}>Activate selected revision</button>
        <button type="button" onClick={event => { transitionTriggerRef.current = event.currentTarget; setDialogAction("rollback"); }} disabled={!canTransition}>Roll back to selected revision</button>
      </div>
      {dialogAction !== null && current !== null && selected !== null ? (
        <StrongConfirmationDialog
          organismId={snapshot.organism?.id_prefix ?? "local organism"}
          currentDigest={current.digest}
          targetDigest={selected.digest}
          action={dialogAction}
          onClose={() => setDialogAction(null)}
          onConfirmed={async () => { await onRefresh(); await load(); }}
          onStale={recoverStale}
          returnFocusRef={transitionTriggerRef}
        />
      ) : null}
    </section>
  );
}
