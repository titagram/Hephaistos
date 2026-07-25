import { describe, expect, it } from 'vitest'

import type { ApprovalReq, TelosApprovalReq } from '../types.js'

import { buildApprovalRespondParams } from './telosApproval.js'

const telos: TelosApprovalReq = {
  action: 'activate',
  boundedSummary: 'Activate the reviewed Telos',
  capturedSessionId: 'captured-session',
  digest: 'a'.repeat(64),
  expiresAt: '2026-07-25T12:00:00.000000Z',
  kind: 'telos',
  nonce: 'nonce-1',
  requestId: 'full-request-id'
}

const dangerous: ApprovalReq = {
  allowPermanent: true,
  command: 'echo safe',
  description: 'test command',
  kind: 'dangerous'
}

describe('buildApprovalRespondParams', () => {
  it.each(['approved', 'denied'])('builds exact Telos %s payload', choice => {
    expect(buildApprovalRespondParams(telos, choice, 'different-current-session')).toEqual({
      choice,
      domain: 'telos',
      request_id: 'full-request-id',
      session_id: 'captured-session'
    })
  })

  it.each(['once', 'session', 'always', 'deny', '', 'maybe'])(
    'rejects invalid Telos choice %s',
    choice => {
      expect(buildApprovalRespondParams(telos, choice, 'current-session')).toBeNull()
    }
  )

  it('preserves the dangerous-command payload contract', () => {
    expect(buildApprovalRespondParams(dangerous, 'once', 'current-session')).toEqual({
      choice: 'once',
      session_id: 'current-session'
    })
  })

  it('fails closed for a dangerous approval without a current session', () => {
    expect(buildApprovalRespondParams(dangerous, 'deny', '')).toBeNull()
  })
})
