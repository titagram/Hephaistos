import type { ApprovalOverlay } from '../types.js'

/**
 * Build the RPC params for ``approval.respond`` from the active overlay and
 * the user's choice.  Returns ``null`` for invalid Telos choices (anything
 * other than ``"approved"`` or ``"denied"``) so the caller can reject without
 * sending a request.
 *
 * **Telos** (``kind === "telos"``):
 *   Accepts only ``"approved"`` or ``"denied"``.  Returns
 *   ``{domain: "telos", request_id, choice, session_id}`` using the session
 *   captured at event time (``capturedSessionId``), **not** the current
 *   session id.
 *
 * **Dangerous** (``kind === "dangerous"``):
 *   Returns ``{choice, session_id: currentSid}`` — exact existing behaviour.
 */
export function buildApprovalRespondParams(
  overlay: ApprovalOverlay,
  choice: string,
  currentSid: string,
): Record<string, unknown> | null {
  switch (overlay.kind) {
    case 'telos':
      if (choice !== 'approved' && choice !== 'denied') {
        return null
      }

      return {
        domain: 'telos',
        request_id: overlay.requestId,
        choice,
        session_id: overlay.capturedSessionId
      }

    case 'dangerous':
      if (!currentSid) {
        return null
      }

      return { choice, session_id: currentSid }
  }
}
