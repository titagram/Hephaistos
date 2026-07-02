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

EXCLUDED_BUNDLED_SKILL_REL_PREFIXES = frozenset(
    {
        "autonomous-ai-agents/hermes-agent",
        "creative/",
        "productivity/teams-meeting-pipeline",
    }
)

EXCLUDED_TOOLSETS = frozenset({"image_gen", "video_gen", "spotify"})


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


def is_excluded_bundled_skill(skill_dir: Path, bundled_dir: Path) -> bool:
    """Return True if a bundled skill should not be synced by Hades install."""
    try:
        rel = skill_dir.relative_to(bundled_dir)
    except ValueError:
        return False
    rel_posix = PurePosixPath(rel.as_posix()).as_posix().strip("/")
    if rel_posix in EXCLUDED_BUNDLED_SKILL_REL_PREFIXES:
        return True
    rel_prefix = f"{rel_posix}/"
    return any(rel_prefix.startswith(prefix) for prefix in EXCLUDED_BUNDLED_SKILL_REL_PREFIXES)


def is_excluded_toolset(name: str) -> bool:
    """Return True if a toolset is not part of Hades' local-agent surface."""
    return name in EXCLUDED_TOOLSETS
