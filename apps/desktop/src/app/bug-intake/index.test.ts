import { describe, expect, it } from 'vitest'

import { redactBugIntakePreview } from './index'

describe('redactBugIntakePreview', () => {
  it('redacts bearer, key-value, and provider token shapes', () => {
    expect(
      redactBugIntakePreview(
        'Authorization: Bearer abcdefghijklmnopqrstuvwxyz\napi_key=super-secret-value\nsk_live_1234567890abcdef'
      )
    ).toBe('Authorization: Bearer [redacted]\napi_key=[redacted]\n[redacted-token]')
  })
})
