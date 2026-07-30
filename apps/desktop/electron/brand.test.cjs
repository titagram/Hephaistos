'use strict'

const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const { DESKTOP_BRAND } = require('./brand.cjs')

test('desktop identity is Hades with a Hermes protocol alias', () => {
  assert.deepEqual(DESKTOP_BRAND, {
    productName: 'Hades',
    agentName: 'Hades Agent',
    copyright: 'Copyright © 2026 Hades Agent',
    preferredProtocol: 'hades',
    legacyProtocols: ['hermes'],
    termProgram: 'Hades'
  })
})

test('package metadata registers the preferred and legacy desktop protocols in order', () => {
  const pkg = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'package.json'), 'utf8'))

  assert.equal(pkg.productName, DESKTOP_BRAND.productName)
  assert.equal(pkg.build.executableName, DESKTOP_BRAND.productName)
  assert.deepEqual(pkg.build.protocols[0].schemes, [
    DESKTOP_BRAND.preferredProtocol,
    ...DESKTOP_BRAND.legacyProtocols
  ])
})
