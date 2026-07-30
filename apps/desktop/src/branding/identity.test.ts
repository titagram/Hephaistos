import { describe, expect, it } from 'vitest'

import { HADES_ASCII_ART, HADES_BRAND } from './identity'

describe('Hades renderer identity', () => {
  it('provides the Hades labels and asset paths consumed by the renderer', () => {
    expect(HADES_BRAND.productName).toBe('Hades')
    expect(HADES_BRAND.agentName).toBe('Hades Agent')
    expect(HADES_BRAND.tagline).toBe('Agent of the Underworld')
    expect(HADES_BRAND.symbol).toBe('♇')
    expect(HADES_BRAND.preferredProtocol).toBe('hades')
    expect(HADES_BRAND.markPath).toBe('/hades-mark.svg')
    expect(HADES_BRAND.touchIconPath).toBe('/apple-touch-icon.png')
  })

  it('ships Pluto hero artwork without Hermes copy', () => {
    expect(HADES_ASCII_ART).toContain('hades / pluto')
    expect(HADES_ASCII_ART).not.toMatch(/hermes/i)
  })
})
