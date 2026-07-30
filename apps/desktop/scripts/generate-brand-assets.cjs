'use strict'

const fs = require('node:fs')
const path = require('node:path')

const { PNG } = require('pngjs')

const OBSIDIAN = [0x11, 0x10, 0x0d]
const BRONZE = [0xcf, 0x8a, 0x2e]

function segmentDistance(px, py, ax, ay, bx, by) {
  const abx = bx - ax
  const aby = by - ay
  const t = Math.max(0, Math.min(1, ((px - ax) * abx + (py - ay) * aby) / (abx * abx + aby * aby)))
  return Math.hypot(px - (ax + t * abx), py - (ay + t * aby))
}

function circleDistance(px, py, cx, cy, radius) {
  return Math.abs(Math.hypot(px - cx, py - cy) - radius)
}

function insideRoundedTile(x, y, radius = 0.16) {
  const qx = Math.abs(x - 0.5) - (0.5 - radius)
  const qy = Math.abs(y - 0.5) - (0.5 - radius)
  return Math.hypot(Math.max(qx, 0), Math.max(qy, 0)) + Math.min(Math.max(qx, qy), 0) <= radius
}

function insideSigil(x, y) {
  const stroke = 0.026
  const circle = circleDistance(x, y, 0.5, 0.265, 0.17) <= stroke
  const stem = segmentDistance(x, y, 0.5, 0.43, 0.5, 0.79) <= stroke
  const cross = segmentDistance(x, y, 0.31, 0.61, 0.69, 0.61) <= stroke
  const arcLeft = segmentDistance(x, y, 0.32, 0.83, 0.5, 0.76) <= stroke
  const arcRight = segmentDistance(x, y, 0.5, 0.76, 0.68, 0.83) <= stroke
  return circle || stem || cross || arcLeft || arcRight
}

function renderHadesPng(size = 1024) {
  const png = new PNG({ width: size, height: size })
  const samples = 4

  for (let py = 0; py < size; py += 1) {
    for (let px = 0; px < size; px += 1) {
      let tileSamples = 0
      let sigilSamples = 0

      for (let sy = 0; sy < samples; sy += 1) {
        for (let sx = 0; sx < samples; sx += 1) {
          const x = (px + (sx + 0.5) / samples) / size
          const y = (py + (sy + 0.5) / samples) / size
          if (insideRoundedTile(x, y)) {
            tileSamples += 1
            if (insideSigil(x, y)) sigilSamples += 1
          }
        }
      }

      const offset = (py * size + px) * 4
      const alpha = tileSamples / (samples * samples)
      const bronze = tileSamples ? sigilSamples / tileSamples : 0
      png.data[offset] = Math.round(OBSIDIAN[0] * (1 - bronze) + BRONZE[0] * bronze)
      png.data[offset + 1] = Math.round(OBSIDIAN[1] * (1 - bronze) + BRONZE[1] * bronze)
      png.data[offset + 2] = Math.round(OBSIDIAN[2] * (1 - bronze) + BRONZE[2] * bronze)
      png.data[offset + 3] = Math.round(255 * alpha)
    }
  }

  return PNG.sync.write(png)
}

function wrapPngAsIco(png, size = 256) {
  const header = Buffer.alloc(22)
  header.writeUInt16LE(0, 0)
  header.writeUInt16LE(1, 2)
  header.writeUInt16LE(1, 4)
  header[6] = size >= 256 ? 0 : size
  header[7] = size >= 256 ? 0 : size
  header.writeUInt16LE(1, 10)
  header.writeUInt16LE(32, 12)
  header.writeUInt32LE(png.length, 14)
  header.writeUInt32LE(header.length, 18)
  return Buffer.concat([header, png])
}

function wrapPngAsIcns(png) {
  const chunk = Buffer.alloc(8 + png.length)
  chunk.write('ic10', 0, 'ascii')
  chunk.writeUInt32BE(chunk.length, 4)
  png.copy(chunk, 8)
  const header = Buffer.alloc(8)
  header.write('icns', 0, 'ascii')
  header.writeUInt32BE(header.length + chunk.length, 4)
  return Buffer.concat([header, chunk])
}

function generateBrandAssets(desktopRoot = path.resolve(__dirname, '..')) {
  const write = (relativePath, data) => {
    const target = path.join(desktopRoot, relativePath)
    fs.mkdirSync(path.dirname(target), { recursive: true })
    fs.writeFileSync(target, data)
  }
  const png1024 = renderHadesPng(1024)
  const png256 = renderHadesPng(256)
  write('assets/icon.png', png1024)
  write('assets/icon.ico', wrapPngAsIco(png256, 256))
  write('assets/icon.icns', wrapPngAsIcns(png1024))
  write('public/apple-touch-icon.png', png1024)
  write(
    'public/hades-mark.svg',
    Buffer.from(
      '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="10" fill="#11100d"/><g fill="none" stroke="#cf8a2e" stroke-linecap="round" stroke-width="4"><circle cx="32" cy="17" r="11"/><path d="M32 28v25M20 41h24M21 54c7-4 15-4 22 0"/></g></svg>\n'
    )
  )
}

if (require.main === module) {
  generateBrandAssets()
}

module.exports = {
  circleDistance,
  generateBrandAssets,
  insideRoundedTile,
  insideSigil,
  renderHadesPng,
  segmentDistance,
  wrapPngAsIcns,
  wrapPngAsIco
}
