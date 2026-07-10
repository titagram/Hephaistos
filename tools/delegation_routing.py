"""Allow-listed local routing profiles for delegated child agents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


ALLOWED_ROLES = frozenset({"orchestrator", "leaf", "reviewer"})
_SECRET_KEYS = frozenset({"api_key", "token", "secret", "password"})


@dataclass(frozen=True)
class DelegationProfile:
    provider: str
    model: str
    reasoning_effort: str | None
    max_iterations: int
    child_timeout_seconds: int


@dataclass(frozen=True)
class DelegationRouting:
    profiles: dict[str, DelegationProfile]
    role_routes: dict[str, str]
    capacity_mode: str = "legacy"
    max_spawn_depth: int = 1
    max_concurrent_children: int = 3
    max_async_children: int = 3


def _text(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} is required")
    return result


def _positive(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a positive integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a positive integer") from exc
    if result <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return result


def load_delegation_routing(config: Mapping[str, Any]) -> DelegationRouting:
    """Parse only config.yaml values; no environment credentials are read."""
    raw = config.get("delegation", {}) if isinstance(config, Mapping) else {}
    if not isinstance(raw, Mapping):
        raise ValueError("delegation must be an object")
    raw_profiles = raw.get("profiles", {})
    raw_routes = raw.get("role_routes", {})
    if not isinstance(raw_profiles, Mapping) or not isinstance(raw_routes, Mapping):
        raise ValueError("delegation.profiles and delegation.role_routes must be objects")
    profiles: dict[str, DelegationProfile] = {}
    for name, value in raw_profiles.items():
        profile_name = _text(name, "profile name")
        if not isinstance(value, Mapping):
            raise ValueError(f"delegation.profiles.{profile_name} must be an object")
        forbidden = _SECRET_KEYS.intersection(value)
        if forbidden:
            raise ValueError(f"delegation profile {profile_name} contains secret field")
        effort = str(value.get("reasoning_effort") or "").strip() or None
        profiles[profile_name] = DelegationProfile(
            provider=_text(value.get("provider"), f"delegation.profiles.{profile_name}.provider"),
            model=_text(value.get("model"), f"delegation.profiles.{profile_name}.model"),
            reasoning_effort=effort,
            max_iterations=_positive(value.get("max_iterations"), f"delegation.profiles.{profile_name}.max_iterations"),
            child_timeout_seconds=_positive(value.get("child_timeout_seconds"), f"delegation.profiles.{profile_name}.child_timeout_seconds"),
        )
    routes: dict[str, str] = {}
    for role, profile_name in raw_routes.items():
        role_name = _text(role, "role")
        if role_name not in ALLOWED_ROLES:
            raise ValueError(f"unsupported delegation role: {role_name}")
        target = _text(profile_name, f"delegation.role_routes.{role_name}")
        if target not in profiles:
            raise ValueError(f"delegation route {role_name} references unknown profile: {target}")
        routes[role_name] = target
    capacity_mode = str(raw.get("capacity_mode") or "legacy").strip()
    if capacity_mode not in {"legacy", "balanced"}:
        raise ValueError("delegation.capacity_mode must be legacy or balanced")
    return DelegationRouting(
        profiles=profiles,
        role_routes=routes,
        capacity_mode=capacity_mode,
        max_spawn_depth=_positive(raw.get("max_spawn_depth", 1), "delegation.max_spawn_depth"),
        max_concurrent_children=_positive(raw.get("max_concurrent_children", 3), "delegation.max_concurrent_children"),
        max_async_children=_positive(raw.get("max_async_children", 3), "delegation.max_async_children"),
    )


def resolve_role_profile(routing: DelegationRouting, role: str) -> DelegationProfile | None:
    if role not in ALLOWED_ROLES:
        raise ValueError(f"unsupported delegation role: {role}")
    profile_name = routing.role_routes.get(role)
    return routing.profiles[profile_name] if profile_name else None
