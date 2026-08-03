"""Read-only compatibility for external memory providers retired from core."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


RETIRED_MEMORY_PROVIDERS = frozenset({"hades_backend"})


@dataclass(frozen=True)
class MemoryProviderResolution:
    """Configured and usable memory-provider identities without config mutation."""

    configured: str
    effective: str
    retired: bool
    message: str


def resolve_effective_memory_provider(config: Mapping[str, Any]) -> MemoryProviderResolution:
    """Return the provider safe to activate while preserving the configured value."""
    memory = config.get("memory", {}) if isinstance(config, Mapping) else {}
    configured = memory.get("provider", "") if isinstance(memory, Mapping) else ""
    configured = configured.strip() if isinstance(configured, str) else ""

    if configured in RETIRED_MEMORY_PROVIDERS:
        return MemoryProviderResolution(
            configured=configured,
            effective="",
            retired=True,
            message=(
                "This retired memory selection is inactive. "
                "Run interactive `hades memory setup` to choose a provider."
            ),
        )

    return MemoryProviderResolution(
        configured=configured,
        effective=configured,
        retired=False,
        message="",
    )
