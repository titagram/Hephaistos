# Hades Default Capabilities and LCM Design

## Goal

Make the selected edge capabilities available in the standard Hades
distribution and install a reviewed, pinned release of `hermes-lcm` as the
default active context engine, while preserving a one-command rollback to the
built-in compressor.

## Scope

The Hades distribution will stop excluding these existing Hermes surfaces:

- `context_engine`
- `vision`
- `cronjob`
- `computer_use`
- Exa web search
- Firecrawl web search/extract/crawl
- Firecrawl browser provider

Other currently excluded surfaces remain excluded. In particular, this change
does not re-enable image generation, video generation, Spotify, Home Assistant,
TTS, X search, Discord administration, or platform-specific adapters.

## Context-engine behavior

The built-in `ContextCompressor` remains in the codebase and remains the
fail-safe. A successful curated LCM installation makes `lcm` the selected
engine for new and existing Hades installs.

LCM is single-select:

```yaml
context:
  engine: lcm
```

Rollback is:

```bash
hades config set context.engine compressor
```

Disabling the plugin is an additional containment action:

```bash
hades plugins disable hermes-lcm
```

If LCM cannot be loaded, the existing agent initialization path falls back to
`ContextCompressor` rather than preventing Hades from starting.

## Curated plugin distribution

LCM remains a standalone third-party plugin rather than being copied into the
Hades core tree. Hades owns a small curated-plugin lock containing:

- canonical HTTPS repository URL;
- immutable release tag;
- expected commit SHA;
- plugin manifest name;
- context-engine name.

The first pinned release is:

| Field | Value |
|---|---|
| repository | `https://github.com/stephenschoettler/hermes-lcm.git` |
| tag | `v0.20.0` |
| commit | `49e99a272d2d461e5c90732e7ef2bc20e96f0826` |
| plugin | `hermes-lcm` |
| engine | `lcm` |

Hades clones the pinned tag into a temporary directory and verifies the
resolved commit before installation. A moved tag, wrong manifest, symlinked
destination, or unexpected existing unmanaged directory is rejected.

The installed directory contains a Hades ownership marker recording the
source, tag, commit, and activation state. Updates may replace only directories
carrying a valid matching ownership marker. An existing user-managed
`hermes-lcm` directory is never overwritten.

Replacement is backup-first and atomic at the directory boundary:

1. stage and verify the new plugin;
2. rename the current managed plugin to a sibling backup;
3. rename the staged plugin into place;
4. remove the backup only after the new install is complete;
5. restore the backup if the final rename fails.

The plugin's `.git` directory is removed from the installed copy. This prevents
`hades plugins update hermes-lcm` from drifting away from the Hades-reviewed
revision. Hades updates the plugin only by changing its curated lock in a
reviewed Hades release.

## Activation and user choice

On the first successful Hades-managed installation:

- `hermes-lcm` is added to `plugins.enabled`;
- it is removed from `plugins.disabled`;
- `context.engine` becomes `lcm` if it was absent or `compressor`;
- a different explicitly selected context engine is preserved.

Activation is written only on the first managed installation. Later
`hades update` runs verify or update the managed plugin but do not overwrite
the user's current context-engine selection. Therefore selecting `compressor`
is a durable rollback.

## Installation lifecycle

Curated plugin synchronization runs:

- during the normal setup path, including pip post-install;
- after a successful `hades update`;
- through a module-level repair command used by the shell installer.

Network or plugin installation failure is non-fatal to Hades setup/update.
The operator receives a warning and Hades continues with the built-in
compressor fallback.

## Prompt-cache boundary

The active engine and its tool schemas are selected only when an agent/session
is constructed. Hades does not swap engines or mutate the tool schema inside a
live conversation. A config change takes effect on the next session or process
restart.

The `context_engine` toolset remains empty when the built-in compressor is
active. When LCM is active, its runtime tools are added once during agent
initialization.

## Security and privacy

The exact LCM revision is reviewed before release because a plugin executes
inside the Hades process. The review covers:

- subprocess and shell execution;
- network calls;
- dynamic imports and code loading;
- filesystem containment and symlink handling;
- SQLite query construction;
- secret handling and redaction;
- destructive repair/cleanup paths;
- plugin hooks and lifecycle boundaries.

LCM stores its database beneath the active profile's `HERMES_HOME`. Hades does
not enable LCM's optional slash-command mutation paths, semantic embedding
providers, large-output rewriting, or cleanup/repair apply paths as part of
this change.

## Verification

Acceptance requires:

- Hades tool configuration exposes the selected capabilities;
- excluded capabilities outside scope remain excluded;
- curated install rejects a moved tag or wrong commit;
- unmanaged plugin directories are preserved;
- managed updates are atomic and rollback-safe;
- first install activates LCM once;
- manual rollback to `compressor` survives later syncs;
- real `hermes-lcm` v0.20.0 tests pass;
- a real Hades agent initializes with LCM and exposes its runtime tools;
- focused Hades context-engine, plugin, toolset, setup, and update tests pass;
- static checks and the scoped security review pass.
