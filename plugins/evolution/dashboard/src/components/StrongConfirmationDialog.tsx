import { evolutionApi } from "../api";
import { React, SDK } from "../sdk";
import { confirmationConsequences, isExactConfirmationPhrase, staleTransitionRecovery } from "../telos-model";
import type { TelosTransitionPreparation } from "../types";

void React;

export interface StrongConfirmationDialogProps {
  organismId: string;
  currentDigest: string;
  targetDigest: string;
  action: "activate" | "rollback";
  onClose(): void;
  onConfirmed(): Promise<void>;
  onStale(warning: string): Promise<void>;
}

function errorMessage(error: unknown): string {
  if (error instanceof Error && /^422(?::|\s|$)/.test(error.message)) return "The server rejected this Telos transition. Review the displayed values and refresh before preparing it again.";
  return error instanceof Error ? error.message : "The Telos transition could not be completed.";
}

function expired(prepared: TelosTransitionPreparation): boolean {
  const timestamp = Date.parse(prepared.expires_at);
  return Number.isFinite(timestamp) && Date.now() >= timestamp;
}

export function StrongConfirmationDialog({
  organismId,
  currentDigest,
  targetDigest,
  action,
  onClose,
  onConfirmed,
  onStale,
}: StrongConfirmationDialogProps): React.ReactElement {
  const { useRef, useState } = SDK.hooks;
  const [prepared, setPrepared] = useState<TelosTransitionPreparation | null>(null);
  const [phrase, setPhrase] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const confirmedRef = useRef(false);
  const title = action === "activate" ? "Activate Telos revision" : "Roll back Telos revision";

  const handleStale = async (nextError: unknown) => {
    const recovery = staleTransitionRecovery(nextError);
    if (recovery === null) return false;
    onClose();
    await onStale(recovery.warning);
    return true;
  };

  const prepare = async () => {
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const context = await evolutionApi.mutationContext();
      const next = await evolutionApi.prepareTelosTransition({ ...context, current_digest: currentDigest, target_digest: targetDigest, action });
      setPrepared(next);
      setPhrase("");
    } catch (nextError) {
      if (!await handleStale(nextError)) setError(errorMessage(nextError));
    } finally {
      setSubmitting(false);
    }
  };

  const confirm = async () => {
    if (prepared === null || submitting || confirmedRef.current || expired(prepared) || !isExactConfirmationPhrase(phrase, prepared.required_phrase)) return;
    confirmedRef.current = true;
    setSubmitting(true);
    setError(null);
    try {
      const context = await evolutionApi.mutationContext();
      await evolutionApi.confirmTelosTransition({
        ...context,
        confirmation_id: prepared.confirmation_id,
        current_digest: prepared.current_digest,
        target_digest: prepared.target_digest,
        action: prepared.action,
        phrase,
      });
      await onConfirmed();
      onClose();
    } catch (nextError) {
      if (!await handleStale(nextError)) setError(errorMessage(nextError));
    } finally {
      setSubmitting(false);
    }
  };

  const canConfirm = prepared !== null && !expired(prepared) && isExactConfirmationPhrase(phrase, prepared.required_phrase) && !submitting && !confirmedRef.current;
  return (
    <div className="evo-confirmation-dialog" role="dialog" aria-modal="true" aria-labelledby="evo-telos-confirmation-title" aria-describedby="evo-telos-confirmation-description">
      <section className="evo-confirmation-dialog__content">
        <header><h2 id="evo-telos-confirmation-title">{title}</h2></header>
        <p id="evo-telos-confirmation-description">This is a consequential local Telos pointer change. Prepare the server-issued confirmation before it can be confirmed once.</p>
        <dl>
          <div><dt>Organism</dt><dd>{prepared?.organism_id ?? organismId}</dd></div>
          <div><dt>Current digest</dt><dd>{prepared?.current_digest ?? currentDigest}</dd></div>
          <div><dt>Target digest</dt><dd>{prepared?.target_digest ?? targetDigest}</dd></div>
          <div><dt>Action</dt><dd>{action}</dd></div>
          <div><dt>Consequences</dt><dd>{confirmationConsequences(action)}</dd></div>
          {prepared !== null ? <div><dt>Expires</dt><dd>{prepared.expires_at}</dd></div> : null}
        </dl>
        {prepared === null ? (
          <button type="button" onClick={() => void prepare()} disabled={submitting}>{submitting ? "Preparing…" : "Prepare confirmation"}</button>
        ) : (
          <label>
            Type the exact server phrase
            <input value={phrase} onChange={event => setPhrase(event.target.value)} autoComplete="off" aria-describedby="evo-telos-required-phrase" disabled={submitting || expired(prepared)} />
            <span id="evo-telos-required-phrase">{prepared.required_phrase}</span>
          </label>
        )}
        {prepared !== null && expired(prepared) ? <p role="alert">This confirmation expired. Close it and prepare a new transition.</p> : null}
        {error !== null ? <p role="alert">{error}</p> : null}
        <footer>
          <button type="button" onClick={onClose} disabled={submitting}>Cancel</button>
          {prepared !== null ? <button type="button" onClick={() => void confirm()} disabled={!canConfirm}>Confirm {action}</button> : null}
        </footer>
      </section>
    </div>
  );
}
