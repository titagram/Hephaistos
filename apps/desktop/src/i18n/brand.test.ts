import { describe, expect, it } from 'vitest'

import { en } from './en'
import { ja } from './ja'
import { zh } from './zh'
import { zhHant } from './zh-hant'

const strings = (value: unknown): string[] =>
  typeof value === 'string'
    ? [value]
    : value && typeof value === 'object'
      ? Object.values(value).flatMap(strings)
      : []

// Product copy is rebranded, while executable/config/URL compatibility
// identifiers remain `hermes`. Ignore only those explicit technical forms.
const withoutCompatibilityIdentifiers = (value: string) =>
  value.replace(/`[^`]*\bhermes\b[^`]*`/gi, '').replace(/(?:~\/\.|\/)hermes\b/gi, '')

describe('product-facing locale copy', () => {
  it.each([en, ja, zh, zhHant])('contains no Hermes product copy', locale => {
    expect(
      strings(locale).filter(value =>
        /\bHermes\b|hermes-(?:chan|san)/i.test(withoutCompatibilityIdentifiers(value))
      )
    ).toEqual([])
  })
})
