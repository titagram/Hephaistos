import { atom, computed, type ReadableAtom } from 'nanostores'

import { $clarifyRequest } from './clarify'
import { $activeSessionId } from './session'

// Blocking interactive prompts the gateway raises mid-turn. Each maps to a
// `*.request` event the Python side emits while it blocks the agent thread
// waiting for a `*.respond` RPC. Without a renderer for these, the agent
// silently stalls until its timeout (default 5 min) and the tool is BLOCKED.
//
// Like clarify, every prompt is parked under the runtime session id that raised
// it (not one shared slot), so a *background* session running concurrently can
// raise an approval/sudo/secret prompt and have it wait — surfaced via the
// sidebar "needs input" badge — until the user switches to that chat. The
// exported $*Request view is scoped to the active session, so a background
// prompt never hijacks the foreground.

const keyFor = (sessionId: string | null | undefined): string => sessionId ?? ''

interface KeyedPrompt {
  sessionId: string | null
}

interface PromptStore<T extends KeyedPrompt> {
  $active: ReadableAtom<null | T>
  clear: (sessionId?: string | null, requestId?: string) => void
  reset: () => void
  set: (request: T) => void
}

// One per-session prompt kind: a map keyed by session, plus an active-session
// view for the overlays. `clear` drops one session's entry (a request-id
// mismatch is a no-op so a stale resolve can't wipe a newer prompt); with no
// session hint it drops every entry, optionally filtered by request id.
function keyedPromptStore<T extends KeyedPrompt>(): PromptStore<T> {
  const $all = atom<Record<string, T>>({})
  const idOf = (value: T): string | undefined => (value as { requestId?: string }).requestId

  return {
    $active: computed([$all, $activeSessionId], (all, activeId) => all[keyFor(activeId)] ?? null),
    reset: () => $all.set({}),
    set: request => $all.set({ ...$all.get(), [keyFor(request.sessionId)]: request }),
    clear(sessionId, requestId) {
      const all = $all.get()

      if (sessionId !== undefined) {
        const key = keyFor(sessionId)
        const current = all[key]

        if (current && !(requestId && idOf(current) !== requestId)) {
          const next = { ...all }
          delete next[key]
          $all.set(next)
        }

        return
      }

      const next = Object.fromEntries(Object.entries(all).filter(([, v]) => requestId && idOf(v) !== requestId))

      if (Object.keys(next).length !== Object.keys(all).length) {
        $all.set(next as Record<string, T>)
      }
    }
  }
}

// Approval is session-keyed on the backend (one in-flight approval per session,
// resolved via approval.respond {choice, session_id}). It carries no request_id,
// unlike sudo/secret which are _block()-style request/response.
export interface ApprovalRequest extends KeyedPrompt {
  // false when the backend won't honor a permanent allow (tirith warning) → hide "Always allow".
  allowPermanent?: boolean
  command: string
  description: string
}

export interface SudoRequest extends KeyedPrompt {
  requestId: string
}

export interface SecretRequest extends KeyedPrompt {
  envVar: string
  prompt: string
  requestId: string
}

/** Telos host-approval request — domain="telos" on the wire. */
export interface TelosApprovalRequest extends KeyedPrompt {
  kind: 'telos'
  requestId: string
  digest: string
  action: 'activate' | 'rollback'
  boundedSummary: string
  nonce: string
  expiresAt: string
  /** Session id captured from the event — used for the respond RPC. */
  capturedSessionId: string
}

const approval = keyedPromptStore<ApprovalRequest>()
const sudo = keyedPromptStore<SudoRequest>()
const secret = keyedPromptStore<SecretRequest>()
const telosApproval = keyedPromptStore<TelosApprovalRequest>()
const $approvalInlineAnchorCount = atom(0)
const dismissedSecretRequests = new Set<string>()
const MAX_DISMISSED_SECRET_REQUESTS = 256
const secretRequestKey = (sessionId: string | null | undefined, requestId: string) =>
  `${keyFor(sessionId)}\u0000${requestId}`

export const $approvalRequest = approval.$active
export const setApprovalRequest = approval.set
export const clearApprovalRequest = approval.clear
export const $approvalInlineVisible = computed($approvalInlineAnchorCount, count => count > 0)

export function registerApprovalInlineAnchor(): () => void {
  $approvalInlineAnchorCount.set($approvalInlineAnchorCount.get() + 1)

  return () => {
    $approvalInlineAnchorCount.set(Math.max(0, $approvalInlineAnchorCount.get() - 1))
  }
}

export const $sudoRequest = sudo.$active
export const setSudoRequest = sudo.set
export const clearSudoRequest = sudo.clear

export const $secretRequest = secret.$active
export function setSecretRequest(request: SecretRequest): void {
  if (!dismissedSecretRequests.has(secretRequestKey(request.sessionId, request.requestId))) {
    secret.set(request)
  }
}
export const clearSecretRequest = secret.clear

export function dismissSecretRequest(sessionId: string | null | undefined, requestId: string): void {
  if (!requestId) {
    return
  }
  dismissedSecretRequests.add(secretRequestKey(sessionId, requestId))
  while (dismissedSecretRequests.size > MAX_DISMISSED_SECRET_REQUESTS) {
    const oldest = dismissedSecretRequests.values().next().value
    if (oldest === undefined) {
      break
    }
    dismissedSecretRequests.delete(oldest)
  }
  secret.clear(sessionId, requestId)
}

export const $telosApprovalRequest = telosApproval.$active
export const setTelosApprovalRequest = telosApproval.set
export const clearTelosApprovalRequest = telosApproval.clear

// True when the active session is blocked on the user (clarify question or an
// approval / sudo / secret / telos prompt). Mirrors the pet's `awaitingInput` concept
// (agent/pet/state.py): the turn is paused on you, not working — so callers can
// suppress "thinking" indicators and the Esc-to-interrupt shortcut while you
// decide, instead of treating the wait as an in-flight turn.
export const $activeSessionAwaitingInput = computed(
  [$clarifyRequest, $approvalRequest, $sudoRequest, $secretRequest, $telosApprovalRequest],
  (clarify, approval, sudo, secret, telos) => Boolean(clarify || approval || sudo || secret || telos)
)

// Drop in-flight prompts for `sessionId` (a turn ended) across all three kinds —
// or every parked prompt when no session is given (global reset / tests).
export function clearAllPrompts(sessionId?: string | null): void {
  if (sessionId === undefined) {
    approval.reset()
    sudo.reset()
    secret.reset()
    telosApproval.reset()
    dismissedSecretRequests.clear()
    $approvalInlineAnchorCount.set(0)

    return
  }

  approval.clear(sessionId)
  sudo.clear(sessionId)
  secret.clear(sessionId)
  telosApproval.clear(sessionId)
}
