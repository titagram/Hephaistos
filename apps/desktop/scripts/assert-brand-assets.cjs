'use strict'

const fs = require('node:fs')
const path = require('node:path')

const { PNG } = require('pngjs')

const REQUIRED_ASSETS = [
  'assets/icon.png',
  'assets/icon.ico',
  'assets/icon.icns',
  'public/apple-touch-icon.png',
  'public/hades-mark.svg'
]

function validatePng(desktopRoot, relativePath, errors) {
  try {
    const png = PNG.sync.read(fs.readFileSync(path.join(desktopRoot, relativePath)))
    if (png.width !== 1024 || png.height !== 1024) {
      errors.push(`${relativePath}#dimensions`)
    }
    if (png.colorType !== 6) {
      errors.push(`${relativePath}#rgba`)
    }

    const cornerOffsets = [
      3,
      (png.width - 1) * 4 + 3,
      (png.height - 1) * png.width * 4 + 3,
      (png.height * png.width - 1) * 4 + 3
    ]
    if (!cornerOffsets.some(offset => png.data[offset] === 0)) {
      errors.push(`${relativePath}#transparent-corner`)
    }

    let hasOpaquePixel = false
    for (let offset = 3; offset < png.data.length; offset += 4) {
      if (png.data[offset] === 255) {
        hasOpaquePixel = true
        break
      }
    }
    if (!hasOpaquePixel) {
      errors.push(`${relativePath}#opaque-tile`)
    }
  } catch {
    errors.push(`${relativePath}#png`)
  }
}

function hasMagic(desktopRoot, relativePath, expected) {
  return fs.readFileSync(path.join(desktopRoot, relativePath)).subarray(0, expected.length).equals(expected)
}

function assertBrandAssets(desktopRoot = path.resolve(__dirname, '..')) {
  const errors = []
  const missing = new Set()

  for (const relativePath of REQUIRED_ASSETS) {
    if (!fs.existsSync(path.join(desktopRoot, relativePath))) {
      errors.push(relativePath)
      missing.add(relativePath)
    }
  }

  for (const relativePath of ['assets/icon.png', 'public/apple-touch-icon.png']) {
    if (!missing.has(relativePath)) {
      validatePng(desktopRoot, relativePath, errors)
    }
  }

  if (!missing.has('assets/icon.ico')) {
    try {
      if (!hasMagic(desktopRoot, 'assets/icon.ico', Buffer.from([0, 0, 1, 0]))) {
        errors.push('assets/icon.ico#magic')
      }
    } catch {
      errors.push('assets/icon.ico#read')
    }
  }

  if (!missing.has('assets/icon.icns')) {
    try {
      if (!hasMagic(desktopRoot, 'assets/icon.icns', Buffer.from('icns'))) {
        errors.push('assets/icon.icns#magic')
      }
    } catch {
      errors.push('assets/icon.icns#read')
    }
  }

  try {
    const packageJson = JSON.parse(fs.readFileSync(path.join(desktopRoot, 'package.json'), 'utf8'))
    if (packageJson.build?.icon !== 'assets/icon') {
      errors.push('package.json#build.icon')
    }
  } catch {
    errors.push('package.json')
  }

  return errors.length === 0 ? { ok: true } : { ok: false, errors }
}

function main() {
  const result = assertBrandAssets()
  if (!result.ok) {
    console.error(`Brand asset validation failed:\n${result.errors.map(error => `- ${error}`).join('\n')}`)
    process.exitCode = 1
    return
  }
  console.log('Brand asset validation passed.')
}

if (require.main === module) {
  main()
}

module.exports = { assertBrandAssets }
