import { describe, expect, it } from 'vitest'

import { buildTelosRespondParams, parseTelosPayload } from '@/lib/telos-approval'
import type { TelosApprovalRequest } from '@/store/prompts'

const validPayload: Record<string, unknown> = {
  domain: 'telos',
  request_id: 'req-123',
  digest: 'a'.repeat(64),
  action: 'activate',
  bounded_summary: 'Test activation',
  nonce: 'n1',
  expires_at: '2026-07-25T12:00:00Z'
}

describe('parseTelosPayload', () => {
  it('returns parsed fields for a valid payload', () => {
    const result = parseTelosPayload(validPayload, 'ev-sid')
    expect(result).not.toBeNull()
    expect(result!.requestId).toBe('req-123')
    expect(result!.digest).toBe('a'.repeat(64))
    expect(result!.action).toBe('activate')
    expect(result!.boundedSummary).toBe('Test activation')
    expect(result!.nonce).toBe('n1')
    expect(result!.expiresAt).toBe('2026-07-25T12:00:00Z')
    expect(result!.sessionId).toBe('ev-sid')
  })

  it('returns null for missing domain', () => {
    const { domain: _, ...noDomain } = validPayload
    expect(parseTelosPayload(noDomain, 'ev-sid')).toBeNull()
  })

  it('returns null for wrong domain', () => {
    expect(parseTelosPayload({ ...validPayload, domain: 'dangerous' }, 'ev-sid')).toBeNull()
  })

  it('returns null for empty request_id', () => {
    expect(parseTelosPayload({ ...validPayload, request_id: '' }, 'ev-sid')).toBeNull()
  })

  it('returns null for non-hex digest', () => {
    expect(parseTelosPayload({ ...validPayload, digest: 'g'.repeat(64) }, 'ev-sid')).toBeNull()
  })

  it('returns null for uppercase hex digest', () => {
    expect(parseTelosPayload({ ...validPayload, digest: 'A'.repeat(64) }, 'ev-sid')).toBeNull()
  })

  it('returns null for short digest', () => {
    expect(parseTelosPayload({ ...validPayload, digest: 'a'.repeat(63) }, 'ev-sid')).toBeNull()
  })

  it('returns null for invalid action', () => {
    expect(parseTelosPayload({ ...validPayload, action: 'approve' }, 'ev-sid')).toBeNull()
  })

  it('returns null for empty bounded_summary', () => {
    expect(parseTelosPayload({ ...validPayload, bounded_summary: '' }, 'ev-sid')).toBeNull()
  })

  it('returns null for empty nonce', () => {
    expect(parseTelosPayload({ ...validPayload, nonce: '' }, 'ev-sid')).toBeNull()
  })

  it('returns null for empty expires_at', () => {
    expect(parseTelosPayload({ ...validPayload, expires_at: '' }, 'ev-sid')).toBeNull()
  })

  it('returns null for missing event session id', () => {
    expect(parseTelosPayload(validPayload, '')).toBeNull()
    expect(parseTelosPayload(validPayload, null)).toBeNull()
    expect(parseTelosPayload(validPayload, undefined)).toBeNull()
  })

  it('returns null for non-object payload', () => {
    expect(parseTelosPayload(undefined, 'ev-sid')).toBeNull()
  })

  it('accepts rollback action', () => {
    const result = parseTelosPayload({ ...validPayload, action: 'rollback' }, 'ev-sid')
    expect(result).not.toBeNull()
    expect(result!.action).toBe('rollback')
  })

  it('returns null for non-string request_id', () => {
    expect(parseTelosPayload({ ...validPayload, request_id: 123 }, 'ev-sid')).toBeNull()
  })

  it('returns null for missing request_id', () => {
    const { request_id: _, ...noReq } = validPayload
    expect(parseTelosPayload(noReq, 'ev-sid')).toBeNull()
  })
})

describe('buildTelosRespondParams', () => {
  const req: TelosApprovalRequest = {
    kind: 'telos',
    requestId: 'req-123',
    digest: 'a'.repeat(64),
    action: 'activate',
    boundedSummary: 'Test',
    nonce: 'n1',
    expiresAt: '2026-07-25T12:00:00Z',
    capturedSessionId: 'ev-sid',
    sessionId: 'ev-sid'
  }

  it('returns exact params for approved', () => {
    const params = buildTelosRespondParams(req, 'approved')
    expect(params).toEqual({
      domain: 'telos',
      request_id: 'req-123',
      choice: 'approved',
      session_id: 'ev-sid'
    })
  })

  it('returns exact params for denied', () => {
    const params = buildTelosRespondParams(req, 'denied')
    expect(params).toEqual({
      domain: 'telos',
      request_id: 'req-123',
      choice: 'denied',
      session_id: 'ev-sid'
    })
  })

  it('returns null for invalid choice', () => {
    expect(buildTelosRespondParams(req, 'once')).toBeNull()
    expect(buildTelosRespondParams(req, 'session')).toBeNull()
    expect(buildTelosRespondParams(req, 'always')).toBeNull()
    expect(buildTelosRespondParams(req, 'deny')).toBeNull()
    expect(buildTelosRespondParams(req, '')).toBeNull()
  })

  it('uses captured session id even when request has a different sessionId', () => {
    const other: TelosApprovalRequest = {
      ...req,
      capturedSessionId: 'original-sid',
      sessionId: 'current-active-sid'
    }

    const params = buildTelosRespondParams(other, 'approved')
    expect(params!.session_id).toBe('original-sid')
    expect(params!.session_id).not.toBe('current-active-sid')
  })
})
