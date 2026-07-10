"""Deterministic preflight decisions for bounded delegation."""

from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import os
from typing import Literal, Mapping, Optional


CapacityAction = Literal["allow", "queue", "degrade_to_leaf", "replan"]


@dataclass(frozen=True)
class CapacityRequest:
    """Inputs known by the delegation caller before constructing a child."""

    role: str
    requested_iterations: int
    depth: int
    max_depth: int
    budget_snapshot: Mapping[str, int]
    active_processes: Optional[int] = None
    max_processes: Optional[int] = None
    active_agents: Optional[int] = None
    max_active_agents: Optional[int] = None
    provider_available: Optional[bool] = None


@dataclass(frozen=True)
class CapacitySnapshot:
    """Resolved capacity facts used by :func:`decide_capacity`."""

    role: str
    requested_iterations: int
    depth: int
    max_depth: int
    iterations_remaining: int
    children_remaining: int
    active_processes: Optional[int] = None
    max_processes: Optional[int] = None
    active_agents: Optional[int] = None
    max_active_agents: Optional[int] = None
    provider_available: Optional[bool] = None
    memory_pressure: Optional[float] = None


@dataclass(frozen=True)
class CapacityDecision:
    action: CapacityAction
    reason: str


def _stdlib_memory_pressure() -> Optional[float]:
    """Return host memory pressure using ``os.sysconf`` when supported."""

    try:
        total_pages = int(os.sysconf("SC_PHYS_PAGES"))
        available_pages = int(os.sysconf("SC_AVPHYS_PAGES"))
    except (AttributeError, OSError, TypeError, ValueError):
        return None
    if total_pages <= 0 or available_pages < 0:
        return None
    return min(1.0, max(0.0, 1.0 - (available_pages / total_pages)))


def _memory_pressure() -> Optional[float]:
    """Probe memory safely, preferring stdlib and never installing extras."""

    pressure = _stdlib_memory_pressure()
    if pressure is not None:
        return pressure
    if importlib.util.find_spec("psutil") is None:
        return None
    try:
        import psutil  # type: ignore[import-not-found]

        return min(1.0, max(0.0, float(psutil.virtual_memory().percent) / 100.0))
    except (AttributeError, ImportError, OSError, TypeError, ValueError):
        return None


def probe_capacity(request: CapacityRequest) -> CapacitySnapshot:
    """Combine caller facts with safe host probes without relaxing ceilings."""

    return CapacitySnapshot(
        role=request.role,
        requested_iterations=request.requested_iterations,
        depth=request.depth,
        max_depth=request.max_depth,
        iterations_remaining=int(
            request.budget_snapshot.get("iterations_remaining", 0)
        ),
        children_remaining=int(request.budget_snapshot.get("children_remaining", 0)),
        active_processes=request.active_processes,
        max_processes=request.max_processes,
        active_agents=request.active_agents,
        max_active_agents=request.max_active_agents,
        provider_available=request.provider_available,
        memory_pressure=_memory_pressure(),
    )


def decide_capacity(snapshot: CapacitySnapshot) -> CapacityDecision:
    """Apply a stable policy while keeping configured ceilings authoritative."""

    if (
        snapshot.iterations_remaining <= 0
        or snapshot.children_remaining <= 0
        or snapshot.requested_iterations > snapshot.iterations_remaining
    ):
        return CapacityDecision("replan", "delegation tree budget exhausted")
    if snapshot.depth >= snapshot.max_depth:
        return CapacityDecision("degrade_to_leaf", "spawn depth ceiling reached")
    if (
        snapshot.max_processes is not None
        and snapshot.active_processes is not None
        and snapshot.active_processes >= snapshot.max_processes
    ) or (
        snapshot.max_active_agents is not None
        and snapshot.active_agents is not None
        and snapshot.active_agents >= snapshot.max_active_agents
    ):
        return CapacityDecision("queue", "configured concurrency ceiling reached")
    if snapshot.provider_available is False or (
        snapshot.memory_pressure is not None and snapshot.memory_pressure >= 0.90
    ):
        return CapacityDecision("queue", "capacity temporarily unavailable")
    return CapacityDecision("allow", "capacity available")
