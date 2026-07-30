# Hades desktop rebrand design

## Goal

Complete the visible rebrand of the native Electron desktop application from
Hermes/Nous to Hades while preserving the application's existing workflows,
stored user state, backend contracts, and alternative themes.

The selected visual direction is **Underworld Console**:

- obsidian surfaces, ash text, and bronze accents;
- the Pluto symbol `♇` as the primary product mark;
- Hades ASCII art in hero moments such as startup and empty chat;
- a restrained, professional application shell rather than a pervasive
  fantasy treatment.

The product name is **Hades**. Hero and informational surfaces may use
**Hades Agent** and the supporting line **Agent of the Underworld**.

This design applies to both launch paths:

- `hades desktop`, which builds and launches the packaged native application;
- `hades desktop --source`, which builds the same `apps/desktop` renderer and
  launches it through `electron .`.

There is no second source-mode GUI to rebrand.

## Design decisions

The implementation will use a shared Hades brand foundation rather than
replacing strings and images independently in each component. User-visible
branding changes comprehensively, while internal Hermes identifiers remain
when they are compatibility contracts.

The Hades theme becomes the default. Existing alternative built-in themes and
user themes remain selectable. The visible Nous theme is retired rather than
offered alongside Hades because it carries the identity being replaced.

The Pluto sigil is the canonical icon direction. It must remain recognizable at
16–32 px and scale cleanly to installer and application-store sizes. The icon
uses a bronze sigil on an obsidian field with minimal detail; it does not use
the current Nous portrait or a character illustration.

## Brand foundation

A small renderer branding module will be the source of truth for:

- product name: `Hades`;
- agent name: `Hades Agent`;
- supporting line: `Agent of the Underworld`;
- canonical renderer asset paths;
- reusable accessible labels for the product mark.

The existing `BrandMark` component will consume this foundation and render the
Pluto sigil. It must not depend on a remote font or network resource. It will
include an inline vector fallback so a missing optional raster asset never
produces a broken image tile.

Brand constants are for visible product identity only. They do not replace
backend names, types, IPC channel identifiers, storage keys, or environment
variables.

## Theme model

A built-in `hades` theme will implement Underworld Console and become
`DEFAULT_SKIN_NAME`. Its default presentation uses:

- near-black obsidian backgrounds;
- warm ash and bone foregrounds;
- bronze primary, focus, and selection accents;
- restrained ember tones for emphasis;
- existing semantic success, warning, and destructive distinctions with
  accessible contrast.

The theme must support the desktop application's light/dark/system mode
contract. Hades may remain dark-led, but every resolved mode must preserve
readability and the existing theme APIs.

The built-in alternatives `midnight`, `ember`, `mono`, `cyberpunk`, and
`slate`, plus installed user themes, remain selectable and retain their own
colors. Persistent color overrides and per-profile theme state continue to
apply.

A persisted skin value of `nous` normalizes to `hades`. The old identifier may
remain as a resolver alias for compatibility, but it is not listed in theme
pickers, slash-command suggestions, settings, or other visible catalogs.

## Visual surfaces

### Application and installer

The Pluto sigil will replace the current Nous portrait in:

- `assets/icon.png`, `assets/icon.icns`, and `assets/icon.ico`;
- favicon and Apple touch icon assets;
- macOS Dock and application switcher;
- Windows executable and installer assets;
- Linux application assets;
- DMG, MSI, NSIS, and other builder surfaces that consume the desktop icon.

Package metadata that is already Hades remains so. Residual titles,
descriptions, notifications, terminal metadata, native menu text, and About
metadata that still expose Hermes will use Hades.

The canonical icon artwork must have a version-controlled source from which
platform variants can be reproduced. Platform assets must be bundled; startup
and packaging cannot depend on network access.

### Startup and failure states

The pre-paint document title changes from Hermes to Hades. Its first-frame
colors match the Hades theme closely enough to prevent a white or blue brand
flash before React mounts.

Boot and install overlays use:

- the Pluto sigil or Hades ASCII mark;
- `Hades`/`Hades Agent` naming;
- the obsidian, ash, and bronze palette;
- current progress and recovery behavior unchanged.

Boot failure, reauthentication, repair, restart, and update messages receive
the same visible naming treatment. Rebranding must not obscure actionable
diagnostic details.

### Onboarding

The onboarding flow keeps its provider discovery, OAuth/API-key paths, model
confirmation, cancellation, and first-run persistence behavior. Its visible
identity changes to:

- the Hades mark instead of the Nous image;
- Hades Agent headings and copy;
- Underworld Console surfaces and focus states;
- restrained terminal/ASCII motion on the success moment.

Provider names such as Nous Portal remain unchanged when they identify a real
provider rather than the desktop product.

### Chat and shell

The shell receives a compact Hades mark in the brand location without adding
permanent decorative chrome. The transcript, composer, tool display, sidebars,
panes, titlebar controls, and statusbar keep their existing layout and
interaction model.

The empty chat state uses:

- `HADES AGENT`;
- the Pluto sigil or the established Hades ASCII art;
- personality-aware supporting copy with no residual Hermes persona names;
- the Underworld Console palette when the Hades theme is active.

ASCII art is limited to hero/empty states and startup. It does not appear
between messages, inside the composer, or around routine controls.

### Settings and About

Appearance settings list Hades as the default built-in theme and keep all
approved alternatives. About displays the Hades mark, Hades Desktop name,
canonical version, runtime details, and Hades Agent copyright.

Settings descriptions, provider flow copy, notifications, updater copy, and
native About text undergo a visible-string audit. Internal function names such
as `getHermesConfigRecord` are not part of this audit.

Every shipped locale catalog must use the invariant Hades product name and
remove translated or transliterated Hermes product references. Existing
non-brand terminology and locale fallback behavior remain unchanged.

### Legacy public artwork

The current `hermes.png`, `hermes-sprite.png`, and
`public/hermes-frames/*` assets will be traced to their runtime consumers:

- if they are unused, remove them;
- if they still provide a built-in product mascot, replace them with Hades
  artwork and update references;
- if they belong solely to user-selectable Petdex content, keep the feature
  separate from product branding but remove any Hermes mascot from the default
  product experience.

No Hermes or Nous mascot remains as the default desktop identity.

## CLI launcher surface

The `desktop` command remains canonical and `gui` remains its compatibility
alias. User-visible parser descriptions and help use Hades naming, including:

- desktop application description;
- PATH/runtime-resolution descriptions;
- source-root wording;
- build, sandbox, launch, and recovery output.

Technical option names and their environment bridges remain unchanged. In
particular, `--hermes-root` and `HERMES_DESKTOP_HERMES_ROOT` are compatibility
interfaces and are not renamed as part of this rebrand.

Source mode and packaged mode share the same renderer and assets. Tests must
exercise both launch branches so the source build cannot retain stale titles
or artwork that the packaged build replaces later.

## Compatibility and migration

The following compatibility contracts remain:

- `HERMES_*` environment variables;
- `hermes:*` Electron IPC channels;
- renderer and backend type names such as `HermesGateway`;
- existing localStorage keys, including `hermes-boot-*`;
- filesystem/config locations such as `~/.hermes`;
- the `hermes://` protocol as a registered legacy alias.

The GUI generates and documents `hades://` links. Existing `hermes://` links
continue to open the application.

Theme resolution maps a stored `nous` selection to `hades` without resetting:

- color mode;
- color overrides;
- active profile;
- layout;
- onboarding state;
- sessions;
- user-installed themes.

No bulk storage rewrite is required. Reading and writing the existing storage
keys is safer than duplicating state under new keys.

## Error handling

- Missing optional brand raster: render the inline vector sigil.
- Missing required packaging asset: fail the asset validation/build with the
  exact missing path rather than producing a partially branded package.
- Malformed or retired theme name: resolve to `hades`.
- Stored `nous` theme: resolve deterministically to `hades`.
- Alternative or user theme failure: preserve the existing safe theme
  fallback, now pointing to Hades.
- Missing localization entry: use the English Hades string through the current
  i18n fallback mechanism.
- Boot/update/runtime failure: show Hades-facing copy while preserving concrete
  paths, exit codes, logs, and recovery actions.

Diagnostic logs may retain technical Hermes identifiers where those names
identify actual commands, files, variables, or IPC contracts.

## Verification

### Focused tests

Tests will cover:

- brand constants and accessible product labels;
- `BrandMark` normal and fallback rendering;
- Hades intro/empty-state rendering;
- onboarding, startup, settings, and About brand surfaces;
- default `hades` theme selection;
- persisted `nous` normalization to `hades`;
- alternative built-in and user themes remaining selectable and isolated;
- both `hades://` and `hermes://` protocol registration;
- Hades window titles, notification defaults, and native About metadata;
- Hades CLI `desktop`/`gui` help copy;
- removal or replacement of legacy public artwork.

A focused visible-brand audit will reject residual user-facing `Hermes` and
Nous-product references. Its allowlist must be semantic and narrow: authorized
internal API, environment, IPC, storage, filesystem, provider, and
compatibility references only. It must not become a blanket filename or
directory exclusion that hides visible regressions.

### Asset validation

Automated validation will assert:

- required PNG, ICO, and ICNS files exist;
- PNG dimensions and alpha/channel expectations are correct;
- Electron builder references resolve;
- renderer favicon/touch-icon paths resolve;
- packaged output contains the intended Hades assets.

### Project checks

The implementation must pass:

- focused red/green tests for each changed behavior;
- desktop renderer unit tests;
- Electron main-process tests;
- TypeScript typecheck;
- lint;
- desktop production build;
- relevant CLI/parser Python tests.

### Runtime smoke tests

Manual or automated smoke tests will inspect:

1. `hades desktop --source`;
2. the packaged application launched by `hades desktop`;
3. startup and a controlled boot-failure state;
4. first-run onboarding and its success state;
5. empty chat and an active transcript;
6. Hades default theme and at least one alternative theme;
7. Appearance settings and About;
8. native icon/title/notification surfaces available on the current platform.

## Non-goals

- Renaming the Hermes-derived backend architecture.
- Renaming environment variables, IPC channels, storage keys, config roots, or
  internal TypeScript/Python symbols solely for aesthetics.
- Redesigning chat, settings navigation, panes, onboarding steps, or provider
  behavior.
- Forcing Hades colors onto alternative or user-installed themes.
- Removing the legacy `hermes://` protocol.
- Rebranding real third-party provider names such as Nous Portal.

## Acceptance criteria

The rebrand is complete when:

- a fresh desktop launch presents Hades from the first painted frame through
  chat and About;
- `hades desktop` and `hades desktop --source` show the same Hades identity;
- the Pluto sigil is used across application, renderer, and installer assets;
- Hades is the default selectable theme and approved alternative themes still
  work;
- an existing installation stored as `nous` opens as Hades without losing
  user state;
- no default Hermes/Nous portrait, mascot, title, or product copy remains
  visible;
- legacy technical contracts continue to work;
- focused tests, full relevant suites, builds, and runtime smoke checks pass.
