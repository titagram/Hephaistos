---
sidebar_position: 11.5
title: "Plugin And Skill Distribution"
description: "How Hades Agent separates bundled skills, official optional skills, plugin capabilities, and community catalog entries."
---

# Plugin And Skill Distribution

Hades Agent keeps the core narrow and puts most capability at the edges:
skills, plugins, MCP servers, provider backends, and user configuration. This
page explains what is first-party, what is optional, and what comes from
community or upstream catalogs.

## Bundled Skills

Bundled skills ship in the repository under `skills/`. On install and update,
Hades copies them into the active profile's `~/.hermes/skills/` directory unless
the profile opted out with `hades skills opt-out` or `--no-skills`.

Bundled skills are official Hades content, but your local copy is yours:
updates preserve local edits and local deletion.

## Official Optional Skills

Official optional skills live under `optional-skills/`. They ship with Hades
but are not active by default. Install them explicitly:

```bash
hades skills install official/<category>/<skill>
/skills install official/<category>/<skill>
```

Use optional skills for heavier, niche, or service-specific workflows that do
not belong in every profile.

## Plugins

Plugins add tools, hooks, providers, platform adapters, dashboard auth, memory
backends, and namespaced plugin skills.

Sources:

- Bundled plugins under `plugins/`
- User plugins under `~/.hermes/plugins/`
- Trusted project plugins under `./.hermes/plugins/` when
  `HERMES_ENABLE_PROJECT_PLUGINS=1`
- Pip packages exposing the `hermes_agent.plugins` entry point

Bundled backend plugins can auto-load, platform adapters load lazily on first
use, model providers are loaded by provider discovery, and standalone plugins
are opt-in through `plugins.enabled`.

## Community Skill Catalog

The Skills Hub index is multi-source. It can include official Hades optional
skills and community/upstream entries from sources such as Skills Hub mirrors,
GitHub, ClawHub, and other public catalogs.

Only entries marked `source: "official"` and `trust_level: "builtin"` are
official Hades optional skills. Other entries should be treated as community
catalog data and inspected before installation.

## Excluded Surfaces

Some upstream files remain in the source tree for compatibility or review
history but are intentionally hidden from the Hades distribution. Excluded
surfaces include selected image/video generation backends, Spotify, Teams
pipeline helpers, and selected optional MCP catalog entries.

The maintained policy lives in the source checkout at
`docs/hades/plugin-skill-distribution.md` and is enforced by tests against
`hermes_cli/hades_exclusions.py`.
