import { describe, expect, it } from 'vitest'

import { hadesSymbol, logo } from '../banner.js'
import { DEFAULT_THEME } from '../theme.js'

const textOf = (lines: [string, string][]) => lines.map(([, text]) => text).join('\n')

describe('Hades TUI wide branding', () => {
  it('provides the Hades Agent wordmark instead of the Hermes Agent wordmark', () => {
    const frame = textOf(logo(DEFAULT_THEME.color))

    expect(frame).toContain('██╗  ██╗ █████╗ ██████╗')
    expect(frame).not.toContain('██╗  ██╗███████╗██████╗')
  })

  it('provides the Hades Pluto symbol instead of the Hermes caduceus', () => {
    const frame = textOf(hadesSymbol(DEFAULT_THEME.color))

    expect(frame).toContain('⣠⣶⣿⣿⣿⣿⣿⣶⣄')
    expect(frame).not.toContain('⣩⡿⣿⡿⠻⣿⡇')
  })
})
