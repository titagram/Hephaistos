import { PassThrough } from 'node:stream'

import { Box, renderSync } from '@hades/ink'
import React, { type ReactElement } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { HelpHint } from '../components/helpHint.js'
import { PluginsHub } from '../components/pluginsHub.js'
import type { GatewayClient } from '../gatewayClient.js'
import { DEFAULT_THEME } from '../theme.js'

const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms))

async function renderView(element: ReactElement): Promise<string> {
  const stdout = new PassThrough()
  const stdin = new PassThrough()
  const stderr = new PassThrough()

  Object.assign(stdout, { columns: 100, isTTY: false, rows: 40 })
  Object.assign(stdin, {
    isTTY: true,
    ref: vi.fn(),
    setRawMode: vi.fn(),
    unref: vi.fn()
  })
  Object.assign(stderr, { isTTY: false })

  let captured = ''
  stdout.on('data', chunk => {
    captured += chunk.toString()
  })

  const instance = renderSync(element, {
    patchConsole: false,
    stderr: stderr as NodeJS.WriteStream,
    stdin: stdin as NodeJS.ReadStream,
    stdout: stdout as NodeJS.WriteStream
  })

  try {
    await delay(30)

    // eslint-disable-next-line no-control-regex
    return captured.replace(/\u001b\[[0-9;?]*[A-Za-z]/g, '')
  } finally {
    instance.unmount()
    instance.cleanup()
  }
}

describe('chat TUI branding', () => {
  it('uses Hades branding in the quick-help overlay', async () => {
    const frame = await renderView(
      <Box height={20} position="relative" width={100}>
        <HelpHint t={DEFAULT_THEME} />
      </Box>
    )

    expect(frame).toContain('exit Hades')
    expect(frame).not.toContain('exit hermes')
  })

  it('uses the Hades CLI in the empty plugins overlay', async () => {
    const gw = {
      request: vi.fn(async () => ({ bundled_count: 0, plugins: [], user_count: 0 }))
    } as unknown as GatewayClient

    const frame = await renderView(<PluginsHub gw={gw} onClose={vi.fn()} t={DEFAULT_THEME} />)

    expect(frame).toContain('hades plugins install owner/repo')
    expect(frame).not.toContain('hermes plugins install owner/repo')
  })
})
