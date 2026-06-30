import { EventEmitter } from 'node:events'
import { readFileSync } from 'node:fs'

import { afterEach, describe, expect, it, vi } from 'vitest'

import { setupCommands } from '../app/slash/commands/setup.js'
import { buildSetupRequiredSections } from '../content/setup.js'

const spawnMock = vi.hoisted(() => vi.fn())
const readSource = (path: string) => readFileSync(new URL(path, import.meta.url), 'utf8')
const originalHermesBin = process.env.HERMES_BIN

vi.mock('node:child_process', () => ({
  spawn: spawnMock
}))

describe('setup branding', () => {
  afterEach(() => {
    if (originalHermesBin === undefined) {
      delete process.env.HERMES_BIN
    } else {
      process.env.HERMES_BIN = originalHermesBin
    }
    spawnMock.mockReset()
    vi.resetModules()
  })

  it('uses Hades copy and command hints in the setup-required panel', () => {
    const sections = buildSetupRequiredSections()
    const text = JSON.stringify(sections)

    expect(text).toContain('Hades needs a model provider')
    expect(text).toContain('`hades setup`')
    expect(text).not.toContain('Hermes')
    expect(text).not.toContain('`hermes setup`')
  })

  it('uses Hades command copy for the /setup slash command', () => {
    expect(setupCommands[0]?.help).toContain('`hades setup`')
    expect(setupCommands[0]?.help).not.toContain('`hermes setup`')
  })

  it('uses Hades setup and onboarding copy in TUI surfaces', () => {
    const setupHandoff = readSource('../app/setupHandoff.ts')
    const modelPicker = readSource('../components/modelPicker.tsx')
    const uiStore = readSource('../app/uiStore.ts')
    const useMainApp = readSource('../app/useMainApp.ts')

    expect(setupHandoff).toContain('`hades ${args.join')
    expect(setupHandoff).toContain('error launching Hades')
    expect(setupHandoff).not.toContain('error launching hermes')
    expect(modelPicker).toContain('run `hades model` to configure')
    expect(modelPicker).not.toContain('run `hermes model` to configure')
    expect(uiStore).toContain('summoning Hades')
    expect(uiStore).not.toContain('summoning hermes')
    expect(useMainApp).toContain("'Hades'")
    expect(useMainApp).not.toContain("'Hermes'")
  })

  it('launches the Hades CLI by default while preserving HERMES_BIN override compatibility', async () => {
    delete process.env.HERMES_BIN

    const child = new EventEmitter()
    spawnMock.mockReturnValue(child)

    const { launchHermesCommand } = await import('../lib/externalCli.js')
    const launched = launchHermesCommand(['setup'])

    expect(spawnMock).toHaveBeenCalledWith('hades', ['setup'], { stdio: 'inherit' })
    child.emit('exit', 0)
    await expect(launched).resolves.toEqual({ code: 0 })

    const overrideChild = new EventEmitter()
    spawnMock.mockReturnValue(overrideChild)
    process.env.HERMES_BIN = '/tmp/legacy-hermes'

    const overridden = launchHermesCommand(['setup'])

    expect(spawnMock).toHaveBeenLastCalledWith('/tmp/legacy-hermes', ['setup'], { stdio: 'inherit' })
    overrideChild.emit('exit', 0)
    await expect(overridden).resolves.toEqual({ code: 0 })
  })
})
