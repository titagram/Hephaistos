'use strict'

const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const test = require('node:test')

const { PNG } = require('pngjs')
const { assertBrandAssets } = require('./assert-brand-assets.cjs')
const { generateBrandAssets } = require('./generate-brand-assets.cjs')

function generatedDesktopRoot(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'hades-brand-validation-'))
  t.after(() => fs.rmSync(root, { recursive: true, force: true }))
  fs.writeFileSync(
    path.join(root, 'package.json'),
    `${JSON.stringify({ build: { icon: 'assets/icon' } }, null, 2)}\n`
  )
  generateBrandAssets(root)
  return root
}

function writePng(root, relativePath, png, options) {
  fs.writeFileSync(path.join(root, relativePath), PNG.sync.write(png, options))
}

test('assertBrandAssets accepts generated Hades assets and the package icon contract', t => {
  const root = generatedDesktopRoot(t)

  assert.deepEqual(assertBrandAssets(root), { ok: true })
})

test('assertBrandAssets reports the exact missing generated path', t => {
  const root = generatedDesktopRoot(t)
  const missingPath = 'public/hades-mark.svg'
  fs.unlinkSync(path.join(root, missingPath))

  assert.deepEqual(assertBrandAssets(root), { ok: false, errors: [missingPath] })
})

test('assertBrandAssets rejects a package icon path that bypasses the generated assets', t => {
  const root = generatedDesktopRoot(t)
  fs.writeFileSync(
    path.join(root, 'package.json'),
    `${JSON.stringify({ build: { icon: 'public/apple-touch-icon.png' } }, null, 2)}\n`
  )

  const result = assertBrandAssets(root)
  assert.equal(result.ok, false)
  assert.ok(result.errors.includes('package.json#build.icon'))
})

test('assertBrandAssets rejects wrong PNG dimensions', t => {
  const root = generatedDesktopRoot(t)
  writePng(root, 'assets/icon.png', new PNG({ width: 2, height: 2 }))

  const result = assertBrandAssets(root)
  assert.equal(result.ok, false)
  assert.ok(result.errors.includes('assets/icon.png#dimensions'))
})

test('assertBrandAssets rejects a PNG without RGBA color type', t => {
  const root = generatedDesktopRoot(t)
  const pngPath = path.join(root, 'assets/icon.png')
  const png = PNG.sync.read(fs.readFileSync(pngPath))
  writePng(root, 'assets/icon.png', png, { colorType: 2, inputColorType: 6 })

  const result = assertBrandAssets(root)
  assert.equal(result.ok, false)
  assert.ok(result.errors.includes('assets/icon.png#rgba'))
})

test('assertBrandAssets rejects a PNG without a transparent corner', t => {
  const root = generatedDesktopRoot(t)
  const pngPath = path.join(root, 'assets/icon.png')
  const png = PNG.sync.read(fs.readFileSync(pngPath))
  const cornerOffsets = [
    3,
    (png.width - 1) * 4 + 3,
    (png.height - 1) * png.width * 4 + 3,
    (png.height * png.width - 1) * 4 + 3
  ]
  for (const offset of cornerOffsets) png.data[offset] = 255
  writePng(root, 'assets/icon.png', png)

  const result = assertBrandAssets(root)
  assert.equal(result.ok, false)
  assert.ok(result.errors.includes('assets/icon.png#transparent-corner'))
})

test('assertBrandAssets rejects a PNG without an opaque tile pixel', t => {
  const root = generatedDesktopRoot(t)
  const pngPath = path.join(root, 'assets/icon.png')
  const png = PNG.sync.read(fs.readFileSync(pngPath))
  for (let offset = 3; offset < png.data.length; offset += 4) {
    png.data[offset] = Math.min(png.data[offset], 254)
  }
  writePng(root, 'assets/icon.png', png)

  const result = assertBrandAssets(root)
  assert.equal(result.ok, false)
  assert.ok(result.errors.includes('assets/icon.png#opaque-tile'))
})

test('assertBrandAssets rejects invalid platform container magic', t => {
  const root = generatedDesktopRoot(t)
  fs.writeFileSync(path.join(root, 'assets/icon.ico'), Buffer.from('not-ico'))
  fs.writeFileSync(path.join(root, 'assets/icon.icns'), Buffer.from('not-icns'))

  const result = assertBrandAssets(root)
  assert.equal(result.ok, false)
  assert.ok(result.errors.includes('assets/icon.ico#magic'))
  assert.ok(result.errors.includes('assets/icon.icns#magic'))
})
