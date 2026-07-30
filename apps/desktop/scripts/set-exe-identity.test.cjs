'use strict'

const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const test = require('node:test')

const { stampExeIdentity } = require('./set-exe-identity.cjs')

test('stampExeIdentity stamps the Windows executable with Hades product metadata', async t => {
  const desktopRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'hades-exe-identity-'))
  t.after(() => fs.rmSync(desktopRoot, { recursive: true, force: true }))

  const exe = path.join(desktopRoot, 'Hades.exe')
  const icon = path.join(desktopRoot, 'assets', 'icon.ico')
  fs.mkdirSync(path.dirname(icon), { recursive: true })
  fs.writeFileSync(exe, '')
  fs.writeFileSync(icon, '')

  const calls = []
  await stampExeIdentity(exe, desktopRoot, async (target, options) => {
    calls.push({ target, options })
  })

  assert.equal(calls.length, 1)
  assert.equal(calls[0].target, exe)
  assert.equal(calls[0].options.icon, icon)
  assert.equal(calls[0].options['version-string'].ProductName, 'Hades')
  assert.equal(calls[0].options['version-string'].FileDescription, 'Hades')
})
