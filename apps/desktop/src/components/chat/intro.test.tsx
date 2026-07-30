import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { Intro } from './intro'

describe('Intro', () => {
  it('renders the Hades ASCII hero and accessible agent name', () => {
    const { container } = render(<Intro personality="none" seed={0} />)

    expect(screen.getByLabelText('Hades Agent')).toBeTruthy()
    expect(container.querySelector('pre')?.textContent).toContain('hades / pluto')
    expect(container.textContent).not.toMatch(/hermes-(?:chan|san)/i)
  })
})
