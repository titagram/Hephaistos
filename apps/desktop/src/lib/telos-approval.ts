import type { TelosApprovalRequest } from '@/store/prompts'

/**
 * Parsed result from a raw Telos approval payload.  The TS type is permissive
 * (optional fields) so every field is validated at runtime.
 */
export interface TelosParsedPayload {
  requestId: string
  digest: string
  action: 'activate' | 'rollback'
  boundedSummary: string
  nonce: string
  expiresAt: string
  sessionId: string
}

/**
 * Validate an unknown Telos approval.request payload and return the parsed
 * fields, or ``null`` when any required constraint is violated.
 *
 * Required fields and constraints:
 * - ``request_id`` — nonempty string
 * - ``digest`` — exact 64 lowercase hex chars
 * - ``action`` — exactly ``"activate"`` or ``"rollback"``
 * - ``bounded_summary`` — nonempty string
 * - ``nonce`` — nonempty string
 * - ``expires_at`` — nonempty string
 * - ``session_id`` (the event-level sessionId, not inside payload) — nonempty string
 */
export function parseTelosPayload(
  payload: unknown,
  sessionId: string | undefined | null,
): TelosParsedPayload | null {
  if (!payload || typeof payload !== 'object') {
    return null
  }

  const obj = payload as Record<string, unknown>

  if (obj.domain !== 'telos') {
    return null
  }

  const requestId = obj.request_id
  const digest = obj.digest
  const action = obj.action
  const boundedSummary = obj.bounded_summary
  const nonce = obj.nonce
  const expiresAt = obj.expires_at

  if (
    typeof requestId !== 'string' ||
    requestId.length === 0 ||
    typeof digest !== 'string' ||
    !/^[0-9a-f]{64}$/.test(digest) ||
    (action !== 'activate' && action !== 'rollback') ||
    typeof boundedSummary !== 'string' ||
    boundedSummary.length === 0 ||
    typeof nonce !== 'string' ||
    nonce.length === 0 ||
    typeof expiresAt !== 'string' ||
    expiresAt.length === 0 ||
    typeof sessionId !== 'string' ||
    sessionId.length === 0
  ) {
    return null
  }

  return {
    requestId,
    digest,
    action,
    boundedSummary,
    nonce,
    expiresAt,
    sessionId
  }
}

/**
 * Build the exact RPC params for ``approval.respond`` from a previously parked
 * ``TelosApprovalRequest`` and the user's choice.
 *
 * Accepts only ``"approved"`` or ``"denied"``; returns ``null`` for invalid choices.
 * Uses the ``capturedSessionId`` from the original event, **not** the current
 * active session id.
 */
export function buildTelosRespondParams(
  request: TelosApprovalRequest,
  choice: string,
): Record<string, unknown> | null {
  if (choice !== 'approved' && choice !== 'denied') {
    return null
  }

  return {
    domain: 'telos',
    request_id: request.requestId,
    choice,
    session_id: request.capturedSessionId
  }
}
