import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { DesktopBootstrapState } from '@/global'

import { DesktopInstallOverlay } from './desktop-install-overlay'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('DesktopInstallOverlay', () => {
  it('identifies an active first-run install as Hades Agent', async () => {
    const state: DesktopBootstrapState = {
      active: true,
      completedAt: null,
      error: null,
      log: [],
      manifest: null,
      stages: {},
      startedAt: Date.now(),
      unsupportedPlatform: null
    }

    Object.defineProperty(window, 'hermesDesktop', {
      configurable: true,
      value: {
        getBootstrapState: vi.fn().mockResolvedValue(state),
        onBootstrapEvent: vi.fn().mockReturnValue(() => {})
      }
    })

    render(<DesktopInstallOverlay />)

    expect(await screen.findByRole('heading', { name: 'Hades Agent' })).toBeTruthy()
    expect(screen.getByText('Fetching installer manifest...')).toBeTruthy()
  })
})
