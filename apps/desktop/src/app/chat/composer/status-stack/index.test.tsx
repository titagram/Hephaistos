// @vitest-environment jsdom
import { act, cleanup, render } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n'

import { ComposerStatusStack } from './index'

const { requestGateway } = vi.hoisted(() => ({
  requestGateway: vi.fn()
}))

vi.mock('@/app/gateway/hooks/use-gateway-request', () => ({
  useGatewayRequest: () => ({ requestGateway })
}))

describe('ComposerStatusStack Backend status polling', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    requestGateway.mockResolvedValue({ configured: false })
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
    vi.useRealTimers()
  })

  it('keeps mount and idle polling on local-only backend.status', async () => {
    render(
      <MemoryRouter>
        <I18nProvider configClient={null}>
          <ComposerStatusStack queue={null} sessionId={null} />
        </I18nProvider>
      </MemoryRouter>
    )

    await act(async () => Promise.resolve())
    await act(async () => vi.advanceTimersByTimeAsync(30_001))

    expect(requestGateway).toHaveBeenCalledTimes(3)

    for (const [method, params] of requestGateway.mock.calls) {
      expect(method).toBe('backend.status')
      expect(params).toEqual({})
      expect(params).not.toHaveProperty('live')
    }
  })
})
