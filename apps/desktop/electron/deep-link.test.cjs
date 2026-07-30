'use strict'

const assert = require('node:assert/strict')
const test = require('node:test')

const { DESKTOP_BRAND } = require('./brand.cjs')
const { createDeepLinkDelivery } = require('./deep-link.cjs')

for (const fixture of [
  {
    label: 'current Hades',
    url: 'hades://blueprint/morning%20brief?slot=09%3A30&label=A+B',
    expected: {
      kind: 'blueprint',
      name: 'morning brief',
      params: { slot: '09:30', label: 'A B' }
    }
  },
  {
    label: 'legacy Hermes',
    url: 'hermes://blueprint/evening%2Fbrief?slot=18%3A00&label=C%2BD',
    expected: {
      kind: 'blueprint',
      name: 'evening/brief',
      params: { slot: '18:00', label: 'C+D' }
    }
  }
]) {
  test(`queued ${fixture.label} deep link is delivered once with its path and query intact`, () => {
    let windowReady = false
    const delivered = []
    const delivery = createDeepLinkDelivery({
      preferredProtocol: DESKTOP_BRAND.preferredProtocol,
      canDeliver: () => windowReady,
      deliver: payload => delivered.push(payload)
    })

    delivery.handle(fixture.url)
    assert.deepEqual(delivered, [])

    windowReady = true
    delivery.markReady()
    assert.deepEqual(delivered, [fixture.expected])

    delivery.markReady()
    assert.deepEqual(delivered, [fixture.expected])
  })
}

for (const protocol of ['hades', 'hermes']) {
  test(`malformed percent-encoding in a ${protocol} deep link is reported and never delivered`, () => {
    const malformed = []
    const delivered = []
    const delivery = createDeepLinkDelivery({
      preferredProtocol: DESKTOP_BRAND.preferredProtocol,
      canDeliver: () => true,
      deliver: payload => delivered.push(payload),
      onMalformed: url => malformed.push(url)
    })
    const url = `${protocol}://blueprint/%`

    assert.doesNotThrow(() => delivery.handle(url))
    delivery.markReady()

    assert.deepEqual(malformed, [url])
    assert.deepEqual(delivered, [])
  })
}
