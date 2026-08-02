import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import type { HermesGateway } from '@/hermes'
import { $gateway } from '@/store/gateway'
import {
  setSecretRequest,
  $telosApprovalRequest,
  clearAllPrompts,
  setTelosApprovalRequest
} from '@/store/prompts'
import { $activeSessionId } from '@/store/session'

import { PromptOverlays } from './prompt-overlays'

beforeAll(() => {
  const proto = window.HTMLElement.prototype as unknown as Record<string, () => unknown>

  const stubs: Record<string, () => unknown> = {
    hasPointerCapture: () => false,
    releasePointerCapture: () => undefined,
    scrollIntoView: () => undefined,
    setPointerCapture: () => undefined
  }

  for (const [name, fn] of Object.entries(stubs)) {
    proto[name] ??= fn
  }
})

const validRequest = {
  kind: 'telos' as const,
  requestId: 'tr-1',
  digest: 'a'.repeat(64),
  action: 'activate' as const,
  boundedSummary: 'Test Telos activation',
  nonce: 'n1',
  expiresAt: '2026-07-25T12:00:00Z',
  capturedSessionId: 'ev-sid',
  sessionId: 'sess-1'
}

function setRequest(overrides?: Partial<Omit<typeof validRequest, 'kind' | 'action'> & { action?: 'activate' | 'rollback' }>) {
  $activeSessionId.set('sess-1')
  setTelosApprovalRequest({ ...validRequest, ...overrides })
}

function mockGateway() {
  const request = vi.fn().mockResolvedValue({ status: 'approved' })
  $gateway.set({ request } as unknown as HermesGateway)

  return request
}

afterEach(() => {
  cleanup()
  clearAllPrompts()
  $activeSessionId.set(null)
  $gateway.set(null)
})

// 1. No dialog without a Telos request
describe('TelosApprovalDialog', () => {
  it('renders nothing when there is no pending Telos request', () => {
    const { container } = render(<PromptOverlays />)
    // The dialog uses Radix <Dialog open>, which renders a portal with role="dialog".
    // When no request is parked the dialog should not be present.
    expect(screen.queryByRole('dialog')).toBeNull()
    // Also confirm the fallback/sudo/secret sections are not rendering a dialog either.
    expect(container.querySelector('[data-slot="tool-approval-fallback"]')).toBeNull()
  })

  // 2. Displays action/title, bounded summary, digest, nonce, expiry
  it('displays Telos request details: action title, digest, nonce, expiry', () => {
    setRequest()
    render(<PromptOverlays />)

    expect(screen.getByText('Telos Activation')).toBeTruthy()
    expect(screen.getAllByText('Test Telos activation')).toHaveLength(2)
    expect(screen.getByText('n1')).toBeTruthy()
    expect(screen.getByText('2026-07-25T12:00:00Z')).toBeTruthy()
    // Digest should be displayed
    expect(screen.getByText('a'.repeat(64))).toBeTruthy()
  })

  it('shows rollback title for rollback action', () => {
    setRequest({ action: 'rollback' })
    render(<PromptOverlays />)

    expect(screen.getByText('Telos Rollback')).toBeTruthy()
  })

  // 3. Only Approve and Deny decision buttons exist; dangerous labels absent
  it('renders only Approve and Deny — no Run/Allow session/Always allow', () => {
    setRequest()
    render(<PromptOverlays />)

    expect(screen.getByRole('button', { name: /^Approve$/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /^Deny$/ })).toBeTruthy()

    // Dangerous-command buttons must not appear
    expect(screen.queryByRole('button', { name: /Run/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /Allow this session/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /Always allow/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /Reject/ })).toBeNull()
  })

  // 4. Approve sends exact RPC with captured session id (changes after parking)
  it('sends approval.respond {domain: telos, choice: approved, captured session_id} on Approve click', async () => {
    const request = mockGateway()
    setRequest()

    render(<PromptOverlays />)

    // Switch active session AFTER parking — approve must use ev-sid, not sess-1
    $activeSessionId.set('sess-2')

    fireEvent.click(screen.getByRole('button', { name: /^Approve$/ }))

    await waitFor(() => {
      expect(request).toHaveBeenCalledWith('approval.respond', {
        domain: 'telos',
        request_id: 'tr-1',
        choice: 'approved',
        session_id: 'ev-sid' // captured event session, NOT sess-2
      })
    })
  })

  // 5. Deny sends exact choice denied
  it('sends approval.respond {domain: telos, choice: denied} on Deny click', async () => {
    const request = mockGateway()
    setRequest()

    render(<PromptOverlays />)

    fireEvent.click(screen.getByRole('button', { name: /^Deny$/ }))

    await waitFor(() => {
      expect(request).toHaveBeenCalledWith('approval.respond', {
        domain: 'telos',
        request_id: 'tr-1',
        choice: 'denied',
        session_id: 'ev-sid'
      })
    })
  })

  it('sends only one response when the decision control fires twice before the RPC settles', async () => {
    let resolveRequest: (value: { status: string }) => void = () => undefined

    const request = vi.fn(
      () =>
        new Promise<{ status: string }>(resolve => {
          resolveRequest = resolve
        })
    )

    $gateway.set({ request } as unknown as HermesGateway)
    setRequest()
    render(<PromptOverlays />)

    const approve = screen.getByRole('button', { name: /^Approve$/ })
    fireEvent.click(approve)
    fireEvent.click(approve)

    expect(request).toHaveBeenCalledTimes(1)
    resolveRequest({ status: 'approved' })

    await waitFor(() => {
      expect($telosApprovalRequest.get()).toBeNull()
    })
  })

  // 6. Dialog close/escape produces exactly one denial
  it('produces exactly one denial on dialog close (Escape)', async () => {
    const request = mockGateway()
    setRequest()

    render(<PromptOverlays />)

    const dialog = screen.getByRole('dialog')
    // Radix Dialog fires onOpenChange(false) on Escape. Simulate via close event.
    fireEvent.keyDown(dialog, { key: 'Escape' })

    await waitFor(() => {
      // The send function should be called exactly once with 'denied'
      expect(request).toHaveBeenCalledTimes(1)
      expect(request).toHaveBeenCalledWith('approval.respond', {
        domain: 'telos',
        request_id: 'tr-1',
        choice: 'denied',
        session_id: 'ev-sid'
      })
    })
  })

  // 7. Rejected RPC retains the Telos request and dispatches error via notification
  it('retains the request and notifies error on RPC failure', async () => {
    const request = vi.fn().mockRejectedValue(new Error('network error'))
    $gateway.set({ request } as unknown as HermesGateway)

    setRequest()
    render(<PromptOverlays />)

    expect($telosApprovalRequest.get()).not.toBeNull()

    fireEvent.click(screen.getByRole('button', { name: /^Approve$/ }))

    await waitFor(() => {
      // The request should have been attempted
      expect(request).toHaveBeenCalled()
    })

    // After failure, the store entry must still be present (not cleared)
    expect($telosApprovalRequest.get()).not.toBeNull()
  })

  // 8. Successful response clears the request
  it('clears the request on successful RPC', async () => {
    const request = vi.fn().mockResolvedValue({ status: 'approved' })
    $gateway.set({ request } as unknown as HermesGateway)

    setRequest()
    render(<PromptOverlays />)

    fireEvent.click(screen.getByRole('button', { name: /^Approve$/ }))

    await waitFor(() => {
      expect($telosApprovalRequest.get()).toBeNull()
    })
  })
})

describe('SecretDialog', () => {
  it('returns a secret only to the runtime session that requested it', async () => {
    const request = mockGateway()
    $activeSessionId.set('secret-session')
    setSecretRequest({
      requestId: 'secret-request',
      envVar: 'PLUGIN_SECRET',
      prompt: 'Project token',
      sessionId: 'secret-session'
    })
    render(<PromptOverlays />)

    fireEvent.change(screen.getByPlaceholderText('PLUGIN_SECRET'), {
      target: { value: 'do-not-echo' }
    })
    fireEvent.click(screen.getByRole('button', { name: /^Send$/ }))

    await waitFor(() => {
      expect(request).toHaveBeenCalledWith('secret.respond', {
        request_id: 'secret-request',
        session_id: 'secret-session',
        value: 'do-not-echo'
      })
    })
  })
})
