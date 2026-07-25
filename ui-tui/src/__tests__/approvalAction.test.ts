import { describe, expect, it } from 'vitest'

import { approvalAction, telosApprovalAction } from '../components/prompts.js'

describe('approvalAction — pure key dispatch for ApprovalPrompt', () => {
  it('maps Esc to deny — parity with global Ctrl+C cancellation', () => {
    expect(approvalAction('', { escape: true }, 0)).toEqual({ kind: 'choose', choice: 'deny' })
    expect(approvalAction('', { escape: true }, 2)).toEqual({ kind: 'choose', choice: 'deny' })
  })

  it('maps number keys 1..4 to once/session/always/deny in registration order', () => {
    expect(approvalAction('1', {}, 0)).toEqual({ kind: 'choose', choice: 'once' })
    expect(approvalAction('2', {}, 0)).toEqual({ kind: 'choose', choice: 'session' })
    expect(approvalAction('3', {}, 0)).toEqual({ kind: 'choose', choice: 'always' })
    expect(approvalAction('4', {}, 0)).toEqual({ kind: 'choose', choice: 'deny' })
  })

  it('ignores out-of-range numbers', () => {
    expect(approvalAction('0', {}, 1)).toEqual({ kind: 'noop' })
    expect(approvalAction('5', {}, 1)).toEqual({ kind: 'noop' })
    expect(approvalAction('9', {}, 1)).toEqual({ kind: 'noop' })
  })

  it('confirms the current selection on Enter', () => {
    expect(approvalAction('', { return: true }, 0)).toEqual({ kind: 'choose', choice: 'once' })
    expect(approvalAction('', { return: true }, 3)).toEqual({ kind: 'choose', choice: 'deny' })
  })

  it('moves selection up/down within bounds', () => {
    expect(approvalAction('', { upArrow: true }, 2)).toEqual({ kind: 'move', delta: -1 })
    expect(approvalAction('', { downArrow: true }, 1)).toEqual({ kind: 'move', delta: 1 })
  })

  it('clamps selection movement at the edges', () => {
    expect(approvalAction('', { upArrow: true }, 0)).toEqual({ kind: 'noop' })
    expect(approvalAction('', { downArrow: true }, 3)).toEqual({ kind: 'noop' })
  })

  it('Esc beats numeric/return — denying is always the first interpretation', () => {
    // If a terminal somehow delivers Esc + a digit in the same event, deny
    // wins.  Documents the precedence so a future refactor doesn't flip it.
    expect(approvalAction('1', { escape: true }, 0)).toEqual({ kind: 'choose', choice: 'deny' })
    expect(approvalAction('', { escape: true, return: true }, 1)).toEqual({ kind: 'choose', choice: 'deny' })
  })

  it('returns noop for unrelated keystrokes (printable letters etc.)', () => {
    expect(approvalAction('a', {}, 0)).toEqual({ kind: 'noop' })
    expect(approvalAction(' ', {}, 0)).toEqual({ kind: 'noop' })
  })

  it('respects a reduced option set when permanent allow is disabled', () => {
    // tirith content-security warning present → no "always"; the 3-item set is
    // once/session/deny, so 3 maps to deny and 4 is out of range.
    const opts = ['once', 'session', 'deny'] as const

    expect(approvalAction('3', {}, 0, opts)).toEqual({ kind: 'choose', choice: 'deny' })
    expect(approvalAction('4', {}, 0, opts)).toEqual({ kind: 'noop' })
    expect(approvalAction('', { downArrow: true }, 2, opts)).toEqual({ kind: 'noop' })
    expect(approvalAction('', { return: true }, 2, opts)).toEqual({ kind: 'choose', choice: 'deny' })
  })
})

describe('telosApprovalAction — pure key dispatch for TelosPrompt', () => {
  it('maps Enter to approve', () => {
    expect(telosApprovalAction('', { return: true })).toEqual({ kind: 'approve' })
  })

  it('maps y and Y to approve', () => {
    expect(telosApprovalAction('y', {})).toEqual({ kind: 'approve' })
    expect(telosApprovalAction('Y', {})).toEqual({ kind: 'approve' })
  })

  it('maps Esc to deny', () => {
    expect(telosApprovalAction('', { escape: true })).toEqual({ kind: 'deny' })
  })

  it('maps Ctrl+C to deny', () => {
    expect(telosApprovalAction('c', { ctrl: true })).toEqual({ kind: 'deny' })
  })

  it('maps n and N to deny', () => {
    expect(telosApprovalAction('n', {})).toEqual({ kind: 'deny' })
    expect(telosApprovalAction('N', {})).toEqual({ kind: 'deny' })
  })

  it('maps 1 to approve', () => {
    expect(telosApprovalAction('1', {})).toEqual({ kind: 'approve' })
  })

  it('maps 2 to deny', () => {
    expect(telosApprovalAction('2', {})).toEqual({ kind: 'deny' })
  })

  it('Esc beats Enter — deny wins', () => {
    expect(telosApprovalAction('', { escape: true, return: true })).toEqual({ kind: 'deny' })
  })

  it('returns noop for unrelated keystrokes', () => {
    expect(telosApprovalAction('x', {})).toEqual({ kind: 'noop' })
    expect(telosApprovalAction(' ', {})).toEqual({ kind: 'noop' })
  })

  it.each(['once', 'session', 'always'])('never emits dangerous choice %s', choice => {
    const actions = [
      telosApprovalAction('1', {}),
      telosApprovalAction('2', {}),
      telosApprovalAction('', { return: true }),
      telosApprovalAction('', { escape: true })
    ]

    expect(actions).not.toContainEqual({ kind: 'choose', choice })
  })
})
