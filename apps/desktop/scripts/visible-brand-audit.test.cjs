'use strict'

const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const test = require('node:test')

const { auditVisibleBrand } = require('./visible-brand-audit.cjs')

function fixtureTree(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'hades-visible-brand-audit-'))
  t.after(() => fs.rmSync(root, { recursive: true, force: true }))

  return {
    root,
    write(relativePath, contents) {
      const file = path.join(root, relativePath)
      fs.mkdirSync(path.dirname(file), { recursive: true })
      fs.writeFileSync(file, contents)
      return file
    }
  }
}

test('rejects visible Hermes product prose', t => {
  const fixture = fixtureTree(t)
  const file = fixture.write('copy.ts', `export const copy = 'Hermes Desktop is ready'`)

  const result = auditVisibleBrand({ desktopRoot: fixture.root, extraFiles: [file] })

  assert.equal(result.ok, false)
  assert.match(result.violations[0].value, /Hermes Desktop/)
})

test('allows compatibility contracts and provider identities', t => {
  const fixture = fixtureTree(t)
  const file = fixture.write(
    'compatibility.ts',
    `
      export const values = [
        'HERMES_DESKTOP_HERMES_ROOT',
        'hermes:version',
        'hermes://blueprint/demo',
        '~/.hermes',
        'HermesGateway',
        'Hermes.exe',
        'Nous Portal',
        'NousResearch',
        'Hosted Hermes & Nous-trained models',
        'hermes-3-llama-3.1-70b'
      ]
    `
  )

  assert.equal(auditVisibleBrand({ desktopRoot: fixture.root, extraFiles: [file] }).ok, true)
})

test('rejects residual Nous product copy and mascot identities', t => {
  const fixture = fixtureTree(t)
  fixture.write(
    'src/copy.ts',
    `
      export const values = [
        'Nous Desktop',
        'Nous Agent',
        'Ask Nous',
        'Open in Nous',
        'nous-chan',
        'nous-san'
      ]
    `
  )

  const result = auditVisibleBrand({ desktopRoot: fixture.root })

  assert.equal(result.ok, false)
  assert.deepEqual(
    result.violations.map(violation => violation.value).sort(),
    ['Ask Nous', 'Nous Agent', 'Nous Desktop', 'Open in Nous', 'nous-chan', 'nous-san'].sort()
  )
})

test('rejects a forbidden Nous product phrase beside an allowed provider identity', t => {
  const fixture = fixtureTree(t)
  fixture.write('src/mixed.ts', `export const copy = 'Open Nous Portal, then Ask Nous'`)

  const result = auditVisibleBrand({ desktopRoot: fixture.root })

  assert.equal(result.ok, false)
  assert.deepEqual(
    result.violations.map(violation => violation.value),
    ['Open Nous Portal, then Ask Nous']
  )
})

test('extracts source strings, template chunks, JSX text, and JSX attribute values', t => {
  const fixture = fixtureTree(t)
  fixture.write(
    'src/copy.tsx',
    `
      export const plain = 'Hermes settings'
      export const template = \`Open Hermes \${plain}\`
      export const view = <section title="Hermes account">Ask Hermes</section>
    `
  )

  const result = auditVisibleBrand({ desktopRoot: fixture.root })

  assert.equal(result.ok, false)
  assert.deepEqual(
    result.violations.map(violation => violation.value).sort(),
    ['Ask Hermes', 'Hermes account', 'Hermes settings', 'Open Hermes ']
  )
})

test('does not inspect comments, identifier names, property names, or module specifiers', t => {
  const fixture = fixtureTree(t)
  fixture.write(
    'src/contracts.ts',
    `
      // Hermes Desktop remains in historical notes.
      /* Hermes should not be found here either. */
      import HermesAdapter from './HermesAdapter'
      const HermesDesktop = { 'Hermes label': 'Hades' }
      export { HermesAdapter, HermesDesktop }
    `
  )

  assert.deepEqual(auditVisibleBrand({ desktopRoot: fixture.root }), {
    ok: true,
    violations: []
  })
})

test('inspects JSON and JSONL string values but not object keys', t => {
  const fixture = fixtureTree(t)
  fixture.write(
    'config.json',
    JSON.stringify({
      'Hermes legacy key': 'Hades',
      nested: ['Hades', { label: 'Hermes workspace' }]
    })
  )
  fixture.write(
    'events.jsonl',
    [
      JSON.stringify({ 'Hermes event key': 'Hades' }),
      JSON.stringify({ message: 'Hermes notification' })
    ].join('\n')
  )

  const result = auditVisibleBrand({ desktopRoot: fixture.root })

  assert.equal(result.ok, false)
  assert.deepEqual(
    result.violations.map(violation => violation.value).sort(),
    ['Hermes notification', 'Hermes workspace']
  )
})

test('inspects index.html visible text and title attributes without reading comments', t => {
  const fixture = fixtureTree(t)
  fixture.write(
    'index.html',
    `
      <!doctype html>
      <!-- Hermes comment -->
      <html>
        <head>
          <title>Hermes Desktop</title>
          <style>.Hermes-brand-marker { color: red; }</style>
        </head>
        <body>
          <main title="Open Hermes">Welcome to Hermes</main>
          <script>const ignored = 'Hermes script body'</script>
        </body>
      </html>
    `
  )

  const result = auditVisibleBrand({ desktopRoot: fixture.root })

  assert.equal(result.ok, false)
  assert.deepEqual(
    result.violations.map(violation => violation.value).sort(),
    ['Hermes Desktop', 'Open Hermes', 'Welcome to Hermes']
  )
})

test('does not treat data-title metadata as an HTML title attribute', t => {
  const fixture = fixtureTree(t)
  fixture.write(
    'index.html',
    '<!doctype html><html><body data-title="Hermes internal key">Hades</body></html>'
  )

  assert.deepEqual(auditVisibleBrand({ desktopRoot: fixture.root }), {
    ok: true,
    violations: []
  })
})

test('inspects unquoted HTML title attribute values', t => {
  const fixture = fixtureTree(t)
  fixture.write(
    'index.html',
    '<!doctype html><html><body><main title=Hermes>Hades</main></body></html>'
  )

  assert.deepEqual(auditVisibleBrand({ desktopRoot: fixture.root }), {
    ok: false,
    violations: [{ file: 'index.html', value: 'Hermes' }]
  })
})

test('does not treat title-like text inside another HTML attribute value as an attribute', t => {
  const fixture = fixtureTree(t)
  fixture.write(
    'index.html',
    `<!doctype html><html><body><main data-example=' title="Hermes internal"'>Hades</main></body></html>`
  )

  assert.deepEqual(auditVisibleBrand({ desktopRoot: fixture.root }), {
    ok: true,
    violations: []
  })
})

test('excludes tests, generated output, release output, and dependencies', t => {
  const fixture = fixtureTree(t)
  fixture.write('src/brand.test.ts', `export const testCopy = 'Hermes test fixture'`)
  const compoundTestFile = fixture.write(
    'src/copy.test.generated.ts',
    `export const generatedTestCopy = 'Hermes generated test fixture'`
  )
  fixture.write('dist/bundle.js', `export const builtCopy = 'Hermes bundle'`)
  fixture.write('build/stamp.json', JSON.stringify({ brand: 'Hermes build' }))
  fixture.write('release/app/resources.json', JSON.stringify({ brand: 'Hermes release' }))
  fixture.write('node_modules/example/index.js', `module.exports = 'Hermes dependency'`)
  fixture.write('src/brand.ts', `export const copy = 'Hades Desktop'`)

  assert.deepEqual(auditVisibleBrand({ desktopRoot: fixture.root, extraFiles: [compoundTestFile] }), {
    ok: true,
    violations: []
  })
})

test('allows only the legacy occurrence covered by a compatibility phrase', t => {
  const fixture = fixtureTree(t)
  fixture.write(
    'src/mixed.ts',
    `export const copy = 'Use the existing Hermes CLI, then open Hermes Desktop'`
  )

  const result = auditVisibleBrand({ desktopRoot: fixture.root })

  assert.equal(result.ok, false)
  assert.deepEqual(result.violations.map(violation => violation.value), [
    'Use the existing Hermes CLI, then open Hermes Desktop'
  ])
})
