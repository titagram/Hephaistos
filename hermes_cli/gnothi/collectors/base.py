from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

COLLECTOR_STATUSES = frozenset({"current", "partial", "missing", "stale"})


@dataclass(frozen=True)
class CollectorContext:
    workspace_root: Path
    generation_id: str
    generation_scope: str
    head_commit: str | None
    collected_at: str
    previous_artifact: dict[str, Any] | None = None


@dataclass
class CollectorResult:
    name: str
    status: str
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    fingerprint: str
    verified_at: str | None
    error_code: str | None = None

    def __post_init__(self) -> None:
        if self.status not in COLLECTOR_STATUSES:
            raise ValueError(f"unsupported collector status: {self.status}")


class Collector(Protocol):
    name: str

    def collect(self, context: CollectorContext) -> CollectorResult: ...
