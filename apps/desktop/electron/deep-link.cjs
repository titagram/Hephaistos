'use strict'

function parseDeepLinkPayload(url, onMalformed) {
  if (!url || typeof url !== 'string') return null

  let parsed
  try {
    parsed = new URL(url)
  } catch {
    onMalformed?.(url)
    return null
  }

  const kind = parsed.hostname || ''
  const name = decodeURIComponent((parsed.pathname || '').replace(/^\//, ''))
  const params = {}
  parsed.searchParams.forEach((value, key) => {
    params[key] = value
  })

  return { kind, name, params }
}

function queuedDeepLinkUrl(payload, preferredProtocol) {
  return (
    `${preferredProtocol}://${payload.kind}/${encodeURIComponent(payload.name)}` +
    (Object.keys(payload.params).length ? '?' + new URLSearchParams(payload.params).toString() : '')
  )
}

function createDeepLinkDelivery({ preferredProtocol, canDeliver, deliver, onMalformed }) {
  let pending = null
  let ready = false

  function handle(url) {
    const payload = parseDeepLinkPayload(url, onMalformed)
    if (!payload) return

    if (!ready || !canDeliver()) {
      pending = payload
      return
    }

    deliver(payload)
  }

  function markReady() {
    ready = true
    if (!pending) return

    const queued = pending
    pending = null
    handle(queuedDeepLinkUrl(queued, preferredProtocol))
  }

  return { handle, markReady }
}

module.exports = {
  createDeepLinkDelivery,
  parseDeepLinkPayload,
  queuedDeepLinkUrl
}
