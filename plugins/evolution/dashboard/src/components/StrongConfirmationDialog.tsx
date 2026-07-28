import { evolutionApi } from "../api";
import { React, SDK } from "../sdk";
import { confirmationConsequences, isExactConfirmationPhrase, staleTransitionRecovery } from "../telos-model";
import type { TelosTransitionPreparation } from "../types";
import { useDialogFocus, type DialogFocusRef } from "./dialog-focus";

void React;

export interface StrongConfirmationDialogProps {
  organismId: string;
  currentDigest: string;
  targetDigest: string;
  action: "activate" | "rollback";
  onClose(): void;
  onConfirmed(): Promise<void>;
  onStale(warning: string): Promise<void>;
  returnFocusRef?: DialogFocusRef;
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
  returnFocusRef,
}: StrongConfirmationDialogProps): React.ReactElement {
  const { useEffect, useRef, useState } = SDK.hooks;
  const [prepared, setPrepared] = useState<TelosTransitionPreparation | null>(null);
  const [phrase, setPhrase] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasExpired, setHasExpired] = useState(false);
  const confirmedRef = useRef(false);
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const title = action === "activate" ? "Activate Telos revision" : "Roll back Telos revision";
  const { close, handleKeyDown } = useDialogFocus({ dialogRef, onClose, returnFocusRef });

  useEffect(() => {
    if (prepared === null) {
      setHasExpired(false);
      return;
    }

    const timestamp = Date.parse(prepared.expires_at);
    if (!Number.isFinite(timestamp)) return;

    let timeoutId: number | undefined;
    const scheduleExpiry = () => {
      if (timeoutId !== undefined) window.clearTimeout(timeoutId);
      const delay = timestamp - Date.now();
      if (delay <= 0) {
        setHasExpired(true);
        return;
      }
      setHasExpired(false);
      timeoutId = window.setTimeout(() => setHasExpired(true), delay);
    };
    const onVisibilityChange = () => {
      if (!document.hidden) scheduleExpiry();
    };

    scheduleExpiry();
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      if (timeoutId !== undefined) window.clearTimeout(timeoutId);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [prepared?.confirmation_id, prepared?.expires_at]);

  const handleStale = async (nextError: unknown) => {
    const recovery = staleTransitionRecovery(nextError);
    if (recovery === null) return false;
    close();
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
      setHasExpired(false);
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
      close();
    } catch (nextError) {
      if (!await handleStale(nextError)) setError(errorMessage(nextError));
    } finally {
      setSubmitting(false);
    }
  };

  const isPreparedExpired = prepared !== null && (hasExpired || expired(prepared));
  const canConfirm = prepared !== null && !isPreparedExpired && isExactConfirmationPhrase(phrase, prepared.required_phrase) && !submitting && !confirmedRef.current;
  return (
    <div ref={dialogRef} className="evo-confirmation-dialog" role="dialog" aria-modal="true" aria-labelledby="evo-telos-confirmation-title" aria-describedby="evo-telos-confirmation-description" tabIndex={-1} onKeyDown={handleKeyDown}>
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
            <input value={phrase} onChange={event => setPhrase(event.target.value)} autoComplete="off" aria-describedby="evo-telos-required-phrase" disabled={submitting || isPreparedExpired} />
            <span id="evo-telos-required-phrase">{prepared.required_phrase}</span>
          </label>
        )}
        {prepared !== null && isPreparedExpired ? <p role="alert">This confirmation expired. Close it and prepare a new transition.</p> : null}
        {error !== null ? <p role="alert">{error}</p> : null}
        <footer>
          <button type="button" onClick={close} disabled={submitting}>Cancel</button>
          {prepared !== null ? <button type="button" onClick={() => void confirm()} disabled={!canConfirm}>Confirm {action}</button> : null}
        </footer>
      </section>
    </div>
  );
}
