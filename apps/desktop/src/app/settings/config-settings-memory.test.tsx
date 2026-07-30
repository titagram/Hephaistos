import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { createRef } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getElevenLabsVoices = vi.fn()
const getHermesConfigDefaults = vi.fn()
const getHermesConfigRecord = vi.fn()
const getHermesConfigSchema = vi.fn()
const getMemoryStatus = vi.fn()
const saveHermesConfig = vi.fn()

vi.mock('@/hermes', () => ({
  getElevenLabsVoices: () => getElevenLabsVoices(),
  getHermesConfigDefaults: () => getHermesConfigDefaults(),
  getHermesConfigRecord: () => getHermesConfigRecord(),
  getHermesConfigSchema: () => getHermesConfigSchema(),
  getMemoryStatus: () => getMemoryStatus(),
  saveHermesConfig: (config: unknown) => saveHermesConfig(config)
}))

vi.mock('@/store/notifications', () => ({
  notify: vi.fn(),
  notifyError: vi.fn()
}))

vi.mock('./memory/connect', () => ({
  MemoryConnect: () => null
}))

vi.mock('./provider-config-panel', () => ({
  ProviderConfigPanel: () => null
}))

beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn()
  Element.prototype.hasPointerCapture = vi.fn(() => false)
  Element.prototype.releasePointerCapture = vi.fn()
})

beforeEach(() => {
  getElevenLabsVoices.mockResolvedValue({ available: false, voices: [] })
  getHermesConfigDefaults.mockResolvedValue({})
  getHermesConfigRecord.mockResolvedValue({ memory: { provider: 'honcho' } })
  getHermesConfigSchema.mockResolvedValue({
    fields: {
      'memory.provider': {
        type: 'string',
        description: 'Memory provider plugin'
      }
    }
  })
  getMemoryStatus.mockResolvedValue({
    active: 'honcho',
    providers: [
      {
        name: 'honcho',
        description: 'Honcho memory.',
        configured: true,
        available: true
      }
    ],
    builtin_files: { memory: 0, user: 0 }
  })
  saveHermesConfig.mockResolvedValue({ ok: true })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

async function renderMemorySettings() {
  const { ConfigSettings } = await import('./config-settings')

  return render(
    <MemoryRouter>
      <ConfigSettings activeSectionId="memory" importInputRef={createRef<HTMLInputElement>()} />
    </MemoryRouter>
  )
}

async function openProviderSelector() {
  const selector = await screen.findByRole('combobox')
  fireEvent.click(selector)

  return selector
}

describe('ConfigSettings memory provider discovery', () => {
  it('renders every discovered provider with generic labels, descriptions, and availability', async () => {
    getMemoryStatus.mockResolvedValueOnce({
      active: 'hades_backend',
      providers: [
        {
          name: 'hades_backend',
          description: 'Stores memory in the active Hades backend.',
          configured: true,
          available: true
        },
        {
          name: 'future_memory',
          description: 'Needs additional credentials.',
          configured: false,
          available: false
        }
      ],
      builtin_files: { memory: 12, user: 4 }
    })

    await renderMemorySettings()
    const selector = await openProviderSelector()

    expect(selector.textContent).toContain('Hades Backend')
    expect(await screen.findByText('Stores memory in the active Hades backend.')).toBeTruthy()
    expect(screen.getByText('Available · Current / configured')).toBeTruthy()
    expect(screen.getByText('Future Memory')).toBeTruthy()
    expect(screen.getByText('Needs additional credentials.')).toBeTruthy()
    expect(screen.getByText('Unavailable')).toBeTruthy()
  })

  it('shows an active unavailable provider as selected and configured without claiming it is not configured', async () => {
    getMemoryStatus.mockResolvedValueOnce({
      active: 'hades_backend',
      providers: [
        {
          name: 'hades_backend',
          description: 'Stores memory in the active Hades backend.',
          configured: false,
          available: false
        }
      ],
      builtin_files: { memory: 0, user: 0 }
    })

    await renderMemorySettings()
    const selector = await openProviderSelector()
    const option = await screen.findByRole('option', { name: /Hades Backend/ })

    expect(selector.textContent).toContain('Hades Backend')
    expect(option.getAttribute('aria-selected')).toBe('true')
    expect(option.textContent).toContain('Unavailable · Current / configured')
    expect(option.textContent).not.toContain('not configured')
  })

  it('keeps the active provider identifiable when discovery does not return it', async () => {
    getMemoryStatus.mockResolvedValueOnce({
      active: 'legacy_memory',
      providers: [
        {
          name: 'hades_backend',
          description: 'Stores memory in the active Hades backend.',
          configured: true,
          available: true
        }
      ],
      builtin_files: { memory: 0, user: 0 }
    })

    await renderMemorySettings()
    const selector = await openProviderSelector()

    expect(selector.textContent).toContain('Legacy Memory')
    expect(screen.getByText('Unavailable (not discovered) · Current / configured')).toBeTruthy()
  })

  it('keeps Settings usable with only the current provider when discovery fails', async () => {
    getHermesConfigRecord.mockResolvedValueOnce({ memory: { provider: 'custom_memory' } })
    getMemoryStatus.mockRejectedValueOnce(new Error('backend offline'))

    await renderMemorySettings()

    expect(await screen.findByText('Provider discovery unavailable; keeping the current selection.')).toBeTruthy()
    const selector = await openProviderSelector()
    expect(selector.textContent).toContain('Custom Memory')
    expect(screen.getByText('Availability unknown · Current / configured')).toBeTruthy()
    expect(screen.queryByText('Hades Backend')).toBeNull()
  })

  it('saves a dynamically discovered selection through the debounced config path', async () => {
    getMemoryStatus.mockResolvedValueOnce({
      active: 'honcho',
      providers: [
        { name: 'honcho', description: 'Honcho memory.', configured: true, available: true },
        {
          name: 'hades_backend',
          description: 'Stores memory in the active Hades backend.',
          configured: true,
          available: true
        }
      ],
      builtin_files: { memory: 0, user: 0 }
    })

    await renderMemorySettings()
    await openProviderSelector()
    fireEvent.click(await screen.findByRole('option', { name: /Hades Backend/ }))

    await waitFor(
      () =>
        expect(saveHermesConfig).toHaveBeenCalledWith({
          memory: { provider: 'hades_backend' }
        }),
      { timeout: 1500 }
    )
  })
})
