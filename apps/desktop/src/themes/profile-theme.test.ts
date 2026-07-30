import { beforeEach, describe, expect, it } from 'vitest'

import { DEFAULT_THEME_MODE, modePref, skinPref } from './context'
import { DEFAULT_SKIN_NAME } from './presets'

// Skin and mode share one per-profile contract, so assert it once over both.
interface Pref {
  resolve: (profile: string) => string
  assign: (profile: string, value: string) => void
}

const cases = [
  {
    name: 'skin',
    pref: skinPref as unknown as Pref,
    fallback: DEFAULT_SKIN_NAME,
    a: 'ember',
    b: 'midnight',
    junk: 'nope'
  },
  { name: 'mode', pref: modePref as unknown as Pref, fallback: 'dark', a: 'light', b: 'system', junk: 'dusk' }
]

describe('default Hades appearance', () => {
  beforeEach(() => window.localStorage.clear())

  // Catches a fresh installation falling back to the former light-mode default
  // instead of opening with the intended dark Hades appearance.
  it('starts a fresh Hades profile in dark mode', () => {
    expect(DEFAULT_THEME_MODE).toBe('dark')
    expect(modePref.resolve('default')).toBe('dark')
  })

  // Catches migration code that mutates an existing preference during lookup;
  // boot-time resolution must be safe before a user explicitly saves a theme.
  it('migrates a persisted Nous skin to Hades without rewriting storage', () => {
    window.localStorage.setItem('hermes-desktop-theme-v2', 'nous')

    expect(skinPref.resolve('default')).toBe('hades')
    expect(window.localStorage.getItem('hermes-desktop-theme-v2')).toBe('nous')
  })
})

describe.each(cases)('per-profile $name', ({ pref, fallback, a, b, junk }) => {
  beforeEach(() => window.localStorage.clear())

  it('falls back to the default when unassigned', () => {
    expect(pref.resolve('default')).toBe(fallback)
    expect(pref.resolve('work')).toBe(fallback)
  })

  it('keeps each profile on its own value', () => {
    pref.assign('work', a)
    pref.assign('default', b)
    expect(pref.resolve('work')).toBe(a)
    expect(pref.resolve('default')).toBe(b)
  })

  it('lets unassigned profiles inherit the default profile as the global fallback', () => {
    pref.assign('default', a)
    expect(pref.resolve('never-themed')).toBe(a)
  })

  it('normalizes an unknown stored value back to the default', () => {
    pref.assign('work', junk)
    expect(pref.resolve('work')).toBe(fallback)
  })
})
