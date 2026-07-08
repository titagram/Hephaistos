"""Curated local-only Hades coordination profiles."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


_PROFILE_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "id": "planner",
        "title": "Planning and decomposition",
        "description": "Break shared backend work into bounded local tasks before delegation.",
        "skill": "autonomous-ai-agents/hades-coordination",
        "model_routing": {
            "local_model_profile": "hades.planner",
            "selector": "strongest_allowed",
            "provider_source": "config.yaml",
        },
        "budget": {"max_turns": 8, "max_runtime_seconds": 900},
        "toolsets": ["filesystem", "terminal", "git"],
        "policies": ["no_backend_model_disclosure", "sync_before_handoff"],
        "backend_visible": False,
    },
    {
        "id": "implementer",
        "title": "Bounded implementation",
        "description": "Execute a narrow task in the linked workspace and keep changes reviewable.",
        "skill": "autonomous-ai-agents/hades-coordination",
        "model_routing": {
            "local_model_profile": "hades.implementer",
            "selector": "cheapest_capable",
            "provider_source": "config.yaml",
        },
        "budget": {"max_turns": 12, "max_runtime_seconds": 1200},
        "toolsets": ["filesystem", "terminal", "git"],
        "policies": ["bounded_scope", "focused_tests_required", "no_backend_model_disclosure"],
        "backend_visible": False,
    },
    {
        "id": "reviewer",
        "title": "Review and verification",
        "description": "Review diffs, validate contracts, and decide whether the MVP gate is met.",
        "skill": "software-development/requesting-code-review",
        "model_routing": {
            "local_model_profile": "hades.reviewer",
            "selector": "strongest_allowed",
            "provider_source": "config.yaml",
        },
        "budget": {"max_turns": 6, "max_runtime_seconds": 900},
        "toolsets": ["filesystem", "terminal", "git"],
        "policies": ["findings_first", "verification_required", "no_backend_model_disclosure"],
        "backend_visible": False,
    },
    {
        "id": "sync-curator",
        "title": "Artifact sync curator",
        "description": "Prepare read-only git tree and symbol artifacts for backend ingestion.",
        "skill": "autonomous-ai-agents/hades-coordination",
        "model_routing": {
            "local_model_profile": "hades.sync",
            "selector": "fast_local_preferred",
            "provider_source": "config.yaml",
        },
        "budget": {"max_turns": 4, "max_runtime_seconds": 600},
        "toolsets": ["filesystem", "terminal"],
        "capabilities": ["sync_git_tree", "populate_backend_ast"],
        "policies": ["read_only_artifacts", "redact_secrets", "no_backend_model_disclosure"],
        "backend_visible": False,
    },
    {
        "id": "memory-steward",
        "title": "Shared memory steward",
        "description": "Draft and review project-scoped memory proposals without publishing personal memory.",
        "skill": "autonomous-ai-agents/hades-coordination",
        "model_routing": {
            "local_model_profile": "hades.memory",
            "selector": "balanced_allowed",
            "provider_source": "config.yaml",
        },
        "budget": {"max_turns": 5, "max_runtime_seconds": 600},
        "toolsets": ["filesystem"],
        "policies": ["project_memory_only", "proposal_review_required", "no_backend_model_disclosure"],
        "backend_visible": False,
    },
)


def hades_coordination_profiles() -> list[dict[str, Any]]:
    """Return copy-safe local Hades coordination profile definitions."""

    return deepcopy(list(_PROFILE_DEFINITIONS))


def hades_coordination_profile(profile_id: str) -> dict[str, Any] | None:
    """Return a copy of one curated profile by id."""

    for profile in _PROFILE_DEFINITIONS:
        if profile["id"] == profile_id:
            return deepcopy(profile)
    return None
