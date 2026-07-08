# Hades Plugin And Skill Distribution Audit

This document is the maintained inventory policy for the Hades plugin and
skill surface. It explains what ships by default, what is official but optional,
what is community or third-party, and what remains in the source tree only for
compatibility or review history.

## Source Of Truth

| Surface | Source | Runtime owner |
| --- | --- | --- |
| Bundled skills | `skills/**/SKILL.md` | `tools/skills_sync.py` |
| Official optional skills | `optional-skills/**/SKILL.md` | `tools/skills_sync.py`, `tools/skills_hub.py` |
| Bundled plugins | `plugins/**/plugin.yaml` | `hermes_cli/plugins.py` and category-specific loaders |
| Hades exclusions | `hermes_cli/hades_exclusions.py` | Install, discovery, catalogs, and tests |
| Website skill index | `website/static/api/skills-index.json` | Website Skills Hub UI and prebuild scripts |

If these disagree, the Python source and tests win. Documentation must be
updated in the same change that changes distribution policy.

## Default Bundled Skills

Skills under `skills/` are official bundled Hades skills. On install and
update they are copied into the active profile's `~/.hermes/skills/` tree unless
the profile opted out with `.no-bundled-skills` or `hades skills opt-out`.

The sync manifest preserves local edits and local deletion. A user-modified
skill is not overwritten by update. A deleted bundled skill is not silently
re-added unless the user opts back in or restores it.

Hades now treats the in-repo bundled skills as a curated developer/AI-ops
surface. Default sync includes only:

- `autonomous-ai-agents/` skills for coordinating coding agents, Hades backend
  work, wiki sync, and other AI CLIs, except legacy Hermes agent docs
- `github/`
- `mlops/`
- `software-development/`
- `dogfood`

Hades excludes these bundled skill paths from default sync:

- `apple/`
- `autonomous-ai-agents/hermes-agent`
- `computer-use`
- `creative/`
- `data-science/`
- `email/`
- `media/`
- `note-taking/`
- `productivity/`
- `research/`
- `smart-home/`
- `social-media/`
- `yuanbao`

The allow/exclusion lists live in
`ALLOWED_BUNDLED_SKILL_REL_PREFIXES`,
`ALLOWED_BUNDLED_SKILL_REL_PATHS`, and
`EXCLUDED_BUNDLED_SKILL_REL_PREFIXES`.

## Official Optional Skills

Skills under `optional-skills/` are official Hades content, but they are not
active by default and are not copied into a profile during install. Users
install them explicitly:

```bash
hades skills install official/<category>/<skill>
/skills install official/<category>/<skill>
```

Optional skills are appropriate for heavyweight dependencies, paid-service
integrations, niche workflows, or capabilities that should not expand every
profile's prompt surface.

## Plugin-Provided Skills

Plugins may register read-only namespaced skills such as `plugin:skill`.
Plugin skills do not appear in the default system prompt skill index and do not
write into `~/.hermes/skills/`. They are loaded explicitly when a plugin's
documentation or command asks for the qualified name.

## Bundled Plugin Classes

`plugins/**/plugin.yaml` manifests are grouped by `kind` and by directory:

| Class | Runtime behavior | Examples |
| --- | --- | --- |
| `backend` | Bundled backends auto-load; provider selection is still config-gated by the category wrapper. | `browser/browser_use`, `dashboard_auth/basic`, `web/ddgs` |
| `platform` | Bundled messaging adapters register deferred loaders and import only when that platform is used. | `platforms/telegram`, `platforms/slack`, `platforms/google_chat` |
| `model-provider` | Indexed for introspection; loaded by provider runtime discovery, not by the general plugin loader. | `model-providers/openai-codex`, `model-providers/nous`, `model-providers/gemini` |
| `standalone` | Opt-in through `plugins.enabled` unless another category-specific loader owns it. | `disk-cleanup`, `security-guidance`, observability plugins |
| Memory providers | Activated by memory-provider configuration, not by default plugin autoload. | `memory/hades_backend`, `memory/honcho`, `memory/supermemory` |

User plugins live in `~/.hermes/plugins/`. Project plugins live under
`./.hermes/plugins/` and require `HERMES_ENABLE_PROJECT_PLUGINS=1`. Pip
plugins use the `hermes_agent.plugins` entry point. More local sources override
less local sources on key collision.

## Hades Exclusions

The Hades distribution keeps some upstream files in the repo, but hides them
from the local product surface. These exclusions are enforced centrally by
`hermes_cli/hades_exclusions.py`.

Excluded bundled plugin keys:

- `browser/firecrawl`
- `google_meet`
- `spotify`
- `teams_pipeline`
- `web/exa`
- `web/firecrawl`
- `web/tavily`

Excluded bundled plugin prefixes:

- `image_gen/`
- `video_gen/`

Excluded dashboard plugin names:

- `hermes-achievements`

Excluded lazy features:

- `image.fal`
- `search.exa`
- `search.firecrawl`

Excluded optional MCP catalog entries:

- `linear`
- `n8n`
- `unreal-engine`

Excluded toolsets:

- `computer_use`
- `context_engine`
- `cronjob`
- `discord`
- `discord_admin`
- `homeassistant`
- `image_gen`
- `tts`
- `video`
- `video_gen`
- `vision`
- `x_search`
- `yuanbao`
- `spotify`

These entries must not appear as configurable Hades defaults, model toolsets,
lazy-install features, bundled plugin loads, or optional MCP catalog entries.

## Website Skill Index

`website/static/api/skills-index.json` is a generated multi-source Skills Hub
index. It can contain official optional skills and community/upstream entries
from sources such as `skills.sh`, `clawhub`, `browse-sh`, `github`,
`lobehub`, and `claude-marketplace`.

Only entries with `source: "official"` and `trust_level: "builtin"` are
official Hades optional skills. All other sources must be presented as
community/upstream catalog data with visible source/trust metadata. Their text
must not be copied into user docs as official Hades copy.

The generated website pages under `website/docs/user-guide/skills/**` are
catalog output. They are allowed to preserve upstream terminology when they are
documenting imported community entries, but first-party Hades docs should use
the Hades product name and link back to this distribution boundary when the
distinction matters.

## Change Checklist

When adding, moving, or excluding a plugin or skill:

1. Choose the narrowest distribution class: bundled, official optional,
   standalone plugin, user plugin, project plugin, pip plugin, or MCP catalog.
2. Prefer `optional-skills/` for heavyweight or niche skills.
3. Prefer standalone plugin repos for third-party product integrations that
   Hades does not own.
4. Update `hermes_cli/hades_exclusions.py` for excluded Hades surfaces.
5. Update this document and the tests in
   `tests/hermes_cli/test_hades_local_surface.py`.
6. Regenerate website skill docs or the static skill index only when the
   release process requires it; do not hand-edit generated catalog pages.
