import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { BrandMark } from './brand-mark'

afterEach(cleanup)

describe('BrandMark', () => {
  it('renders the bundled Hades mark with an inline vector fallback', () => {
    const { container } = render(<BrandMark />)

    expect(screen.getByRole('img', { name: 'Hades' })).toBeTruthy()
    expect(container.querySelector('img')?.getAttribute('src')).toBe('/hades-mark.svg')
    expect(container.querySelector('svg')).toBeTruthy()
  })

  it('removes a failed raster without removing the accessible vector mark', () => {
    const { container } = render(<BrandMark />)

    fireEvent.error(container.querySelector('img')!)

    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('svg')).toBeTruthy()
    expect(screen.getByRole('img', { name: 'Hades' })).toBeTruthy()
  })
})
