import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/store/updates', async () => {
  const { atom } = await import('nanostores')

  return {
    $desktopVersion: atom(null),
    $updateApply: atom({
      applying: false,
      command: null,
      error: null,
      log: [],
      message: '',
      percent: null,
      stage: 'idle'
    }),
    $updateChecking: atom(false),
    $updateStatus: atom(null),
    checkUpdates: vi.fn(),
    openUpdatesWindow: vi.fn(),
    refreshDesktopVersion: vi.fn(),
    startActiveUpdate: vi.fn()
  }
})

import { AboutSettings } from './about-settings'

afterEach(cleanup)

describe('AboutSettings', () => {
  it('identifies the desktop app with the accessible Hades brand', () => {
    render(<AboutSettings />)

    expect(screen.getByRole('heading', { name: 'Hades Desktop' })).toBeTruthy()
    expect(screen.getByRole('img', { name: 'Hades' })).toBeTruthy()
  })
})
