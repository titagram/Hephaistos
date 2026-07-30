import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { HadesAsciiMark } from './hades-ascii-mark'

afterEach(cleanup)

describe('HadesAsciiMark', () => {
  it('renders the Pluto artwork as clipped presentational preformatted text', () => {
    const { container } = render(<HadesAsciiMark className="custom-mark" />)
    const mark = container.querySelector('pre')

    expect(mark?.textContent).toContain('hades / pluto')
    expect(mark?.getAttribute('aria-hidden')).toBe('true')
    expect(mark?.className).toContain('overflow-hidden')
    expect(mark?.className).toContain('custom-mark')
  })
})
