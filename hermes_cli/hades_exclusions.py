"""Central Hades distribution exclusions.

The Hades fork keeps some upstream Hermes files in the source tree for
compatibility and review history, but those features must not be exposed by the
local Hades distribution.  Keep the policy here so installers, discovery code,
and catalogs agree on what is hidden.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath


EXCLUDED_BUNDLED_PLUGIN_KEYS = frozenset(
    {
        "browser/firecrawl",
        "google_meet",
        "spotify",
        "teams_pipeline",
        "web/exa",
        "web/firecrawl",
        "web/tavily",
    }
)

EXCLUDED_BUNDLED_PLUGIN_PREFIXES = frozenset(
    {
        "image_gen/",
        "video_gen/",
    }
)

EXCLUDED_DASHBOARD_PLUGIN_NAMES = frozenset({"hermes-achievements"})

EXCLUDED_LAZY_FEATURES = frozenset(
    {
        "image.fal",
        "search.exa",
        "search.firecrawl",
    }
)

EXCLUDED_OPTIONAL_MCP_NAMES = frozenset({"linear", "n8n", "unreal-engine"})

_KNOWN_HADES_BUNDLED_SKILL_TOP_LEVELS = frozenset(
    {
        "apple",
        "autonomous-ai-agents",
        "computer-use",
        "creative",
        "data-science",
        "dogfood",
        "email",
        "github",
        "media",
        "mlops",
        "note-taking",
        "productivity",
        "research",
        "smart-home",
        "social-media",
        "software-development",
        "yuanbao",
    }
)

ALLOWED_BUNDLED_SKILL_REL_PREFIXES = frozenset(
    {
        "autonomous-ai-agents/",
        "github/",
        "mlops/",
        "software-development/",
    }
)

ALLOWED_BUNDLED_SKILL_REL_PATHS = frozenset(
    {
        "dogfood",
    }
)

EXCLUDED_BUNDLED_SKILL_REL_PREFIXES = frozenset(
    {
        "apple/",
        "autonomous-ai-agents/hermes-agent",
        "computer-use",
        "creative/",
        "data-science/",
        "email/",
        "media/",
        "note-taking/",
        "productivity/",
        "research/",
        "smart-home/",
        "social-media/",
        "yuanbao",
    }
)

HADES_VISIBLE_SLASH_COMMANDS = frozenset(
    {
        "agents",
        "backend",
        "background",
        "branch",
        "browser",
        "clear",
        "codex-runtime",
        "compress",
        "config",
        "debug",
        "doctor",
        "fast",
        "goal",
        "help",
        "history",
        "kanban",
        "learn",
        "memory",
        "model",
        "moa",
        "new",
        "plugins",
        "profile",
        "project",
        "prompt",
        "quit",
        "reasoning",
        "reload",
        "reload-mcp",
        "reload-skills",
        "resume",
        "retry",
        "rollback",
        "save",
        "sessions",
        "skills",
        "status",
        "stop",
        "subgoal",
        "title",
        "tools",
        "toolsets",
        "undo",
        "uninstall",
        "update",
        "usage",
        "version",
        "yolo",
    }
)

EXCLUDED_TOOLSETS = frozenset(
    {
        "computer_use",
        "context_engine",
        "cronjob",
        "discord",
        "discord_admin",
        "homeassistant",
        "image_gen",
        "spotify",
        "tts",
        "video",
        "video_gen",
        "vision",
        "x_search",
        "yuanbao",
    }
)


def is_excluded_bundled_plugin(
    *,
    key: str | None,
    name: str | None = None,
    source: str | None = None,
) -> bool:
    """Return True for bundled plugins excluded from the Hades distribution."""
    if source and source != "bundled":
        return False
    candidates = {value.strip("/") for value in (key or "", name or "") if value}
    for candidate in candidates:
        if candidate in EXCLUDED_BUNDLED_PLUGIN_KEYS:
            return True
        if any(candidate.startswith(prefix) for prefix in EXCLUDED_BUNDLED_PLUGIN_PREFIXES):
            return True
    return False


def is_excluded_dashboard_plugin(*, name: str, source: str | None = None) -> bool:
    """Return True for bundled dashboard extensions hidden in Hades."""
    return (not source or source == "bundled") and name in EXCLUDED_DASHBOARD_PLUGIN_NAMES


def is_excluded_lazy_feature(feature: str) -> bool:
    """Return True if a lazy-install feature is disabled for Hades."""
    return feature in EXCLUDED_LAZY_FEATURES


def is_excluded_optional_mcp(name: str) -> bool:
    """Return True if an optional MCP should not appear in the Hades catalog."""
    return name in EXCLUDED_OPTIONAL_MCP_NAMES


def _bundled_skill_rel_posix(skill_dir: Path, bundled_dir: Path) -> str | None:
    try:
        rel = skill_dir.relative_to(bundled_dir)
    except ValueError:
        return None
    return PurePosixPath(rel.as_posix()).as_posix().strip("/")


def is_allowed_bundled_skill_rel_path(rel_posix: str) -> bool:
    """Return True if a bundled skill path belongs to Hades' developer/AI surface.

    Unknown top-level categories are allowed so tests and explicit custom
    ``HERMES_BUNDLED_SKILLS`` trees keep working. The repository's known
    top-level categories are enumerated above; tests guard that this list stays
    in sync when the in-repo catalog changes.
    """
    rel_posix = PurePosixPath(rel_posix).as_posix().strip("/")
    if not rel_posix:
        return False
    rel_prefix = f"{rel_posix}/"
    if rel_posix in EXCLUDED_BUNDLED_SKILL_REL_PREFIXES:
        return False
    if any(rel_prefix.startswith(prefix) for prefix in EXCLUDED_BUNDLED_SKILL_REL_PREFIXES):
        return False
    if rel_posix in ALLOWED_BUNDLED_SKILL_REL_PATHS:
        return True
    if any(rel_prefix.startswith(prefix) for prefix in ALLOWED_BUNDLED_SKILL_REL_PREFIXES):
        return True
    top_level = rel_posix.split("/", 1)[0]
    return top_level not in _KNOWN_HADES_BUNDLED_SKILL_TOP_LEVELS


def is_excluded_bundled_skill(skill_dir: Path, bundled_dir: Path) -> bool:
    """Return True if a bundled skill should not be synced by Hades install."""
    rel_posix = _bundled_skill_rel_posix(skill_dir, bundled_dir)
    if rel_posix is None:
        return False
    return not is_allowed_bundled_skill_rel_path(rel_posix)


def is_allowed_bundled_skill_path(skill_dir: Path, bundled_dir: Path) -> bool:
    """Return True if a concrete bundled skill directory is in Hades' surface."""
    rel_posix = _bundled_skill_rel_posix(skill_dir, bundled_dir)
    return bool(rel_posix and is_allowed_bundled_skill_rel_path(rel_posix))


def is_excluded_toolset(name: str) -> bool:
    """Return True if a toolset is not part of Hades' local-agent surface."""
    return name in EXCLUDED_TOOLSETS


def is_hades_visible_slash_command_name(name: str) -> bool:
    """Return True if a canonical slash command belongs in local Hades help."""
    return str(name or "").lower().lstrip("/") in HADES_VISIBLE_SLASH_COMMANDS
