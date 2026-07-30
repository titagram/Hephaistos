'use strict'

const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const test = require('node:test')

const { PNG } = require('pngjs')
const { generateBrandAssets } = require('./generate-brand-assets.cjs')

function temporaryDesktopRoot(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'hades-brand-assets-'))
  t.after(() => fs.rmSync(root, { recursive: true, force: true }))
  return root
}

function desktopTextFiles(root) {
  const ignoredDirectories = new Set(['.git', 'build', 'coverage', 'dist', 'node_modules', 'release'])
  const textExtensions = new Set([
    '.cjs',
    '.css',
    '.html',
    '.js',
    '.json',
    '.md',
    '.mjs',
    '.svg',
    '.ts',
    '.tsx',
    '.txt',
    '.yaml',
    '.yml'
  ])
  const files = []

  const visit = directory => {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      if (entry.isDirectory() && ignoredDirectories.has(entry.name)) continue
      const target = path.join(directory, entry.name)
      if (entry.isDirectory()) visit(target)
      else if (entry.isFile() && textExtensions.has(path.extname(entry.name))) files.push(target)
    }
  }

  visit(root)
  return files
}

test('generateBrandAssets writes the required platform asset formats', t => {
  const root = temporaryDesktopRoot(t)

  generateBrandAssets(root)

  const png = PNG.sync.read(fs.readFileSync(path.join(root, 'assets/icon.png')))
  assert.equal(png.width, 1024)
  assert.equal(png.height, 1024)
  assert.equal(fs.readFileSync(path.join(root, 'assets/icon.ico')).subarray(0, 4).toString('hex'), '00000100')
  assert.equal(fs.readFileSync(path.join(root, 'assets/icon.icns')).subarray(0, 4).toString(), 'icns')
  assert.deepEqual(
    fs.readFileSync(path.join(root, 'public/apple-touch-icon.png')),
    fs.readFileSync(path.join(root, 'assets/icon.png'))
  )
  assert.match(fs.readFileSync(path.join(root, 'public/hades-mark.svg'), 'utf8'), /^<svg /)
})

test('generateBrandAssets is byte-for-byte deterministic', t => {
  const firstRoot = temporaryDesktopRoot(t)
  const secondRoot = temporaryDesktopRoot(t)
  const generatedPaths = [
    'assets/icon.png',
    'assets/icon.ico',
    'assets/icon.icns',
    'public/apple-touch-icon.png',
    'public/hades-mark.svg'
  ]

  generateBrandAssets(firstRoot)
  generateBrandAssets(secondRoot)

  for (const relativePath of generatedPaths) {
    assert.deepEqual(
      fs.readFileSync(path.join(firstRoot, relativePath)),
      fs.readFileSync(path.join(secondRoot, relativePath)),
      relativePath
    )
  }
})

test('desktop contributor and runtime files do not reference deleted legacy artwork', () => {
  const desktopRoot = path.resolve(__dirname, '..')
  const forbiddenNames = [
    ['nous', 'girl'].join('-'),
    ['hermes', 'frame'].join('-'),
    ['hermes', 'sprite'].join('-'),
    `${'hermes'}${'.png'}`
  ]
  const matches = []

  for (const file of desktopTextFiles(desktopRoot)) {
    const relativePath = path.relative(desktopRoot, file)
    const lines = fs.readFileSync(file, 'utf8').split(/\r?\n/)
    for (const [index, line] of lines.entries()) {
      for (const forbiddenName of forbiddenNames) {
        if (line.toLowerCase().includes(forbiddenName)) {
          matches.push(`${relativePath}:${index + 1}:${forbiddenName}`)
        }
      }
    }
  }

  assert.deepEqual(matches, [])
})
