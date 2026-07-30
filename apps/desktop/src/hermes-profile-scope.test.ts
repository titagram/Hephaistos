import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  checkHermesUpdate,
  getActionStatus,
  getMemoryProviderConfig,
  getMemoryProviderOAuthStatus,
  getMemoryStatus,
  getStatus,
  restartGateway,
  saveHermesConfig,
  saveMemoryProviderConfig,
  selectMemoryProvider,
  setApiRequestProfile,
  startMemoryProviderOAuth,
  updateHermes
} from './hermes'

// Contract: every backend-targeted action helper must carry the active gateway
// profile, so a multi-profile / global-remote user's restart, status poll, and
// update hit the backend they're actually on — not the primary/default. The
// System-panel "restart does nothing" bug was these helpers dropping it.
describe('backend action helpers are profile-scoped', () => {
  const api = vi.fn(async (_req: { path: string; profile?: string }) => ({}) as never)

  beforeEach(() => {
    ;(window as { hermesDesktop?: unknown }).hermesDesktop = { api }
    api.mockClear()
  })

  afterEach(() => {
    setApiRequestProfile(null)
    delete (window as { hermesDesktop?: unknown }).hermesDesktop
  })

  const lastProfile = () => api.mock.calls.at(-1)?.[0].profile

  it('omits profile when none is active (single-profile users unaffected)', () => {
    void getStatus()
    expect(lastProfile()).toBeUndefined()
  })

  it('forwards the active profile to every backend action', () => {
    setApiRequestProfile('coder')

    void getStatus()
    void restartGateway()
    void updateHermes()
    void checkHermesUpdate()
    void getActionStatus('gateway-restart')

    for (const call of api.mock.calls) {
      expect(call[0].profile).toBe('coder')
    }
  })

  it('forwards the active profile to every memory API helper', () => {
    setApiRequestProfile('hades')

    void getMemoryStatus()
    void selectMemoryProvider('hades_backend')
    void saveHermesConfig({ memory: { provider: 'hades_backend' } })
    void getMemoryProviderConfig('hades_backend')
    void saveMemoryProviderConfig('hades_backend', { endpoint: 'https://memory.example' })
    void getMemoryProviderOAuthStatus('hades_backend')
    void startMemoryProviderOAuth('hades_backend')

    expect(api.mock.calls.map(([request]) => [request.path, request.profile])).toEqual([
      ['/api/memory', 'hades'],
      ['/api/memory/provider', 'hades'],
      ['/api/config', 'hades'],
      ['/api/memory/providers/hades_backend/config', 'hades'],
      ['/api/memory/providers/hades_backend/config', 'hades'],
      ['/api/memory/providers/hades_backend/oauth/status', 'hades'],
      ['/api/memory/providers/hades_backend/oauth/start', 'hades']
    ])
  })
})
