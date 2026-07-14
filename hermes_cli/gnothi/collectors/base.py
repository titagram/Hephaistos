from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

COLLECTOR_STATUSES = frozenset({"current", "partial", "missing", "stale"})


def fingerprint_payload(value: Any) -> str:
    """Hash a bounded, secret-free probe payload deterministically."""
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


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

    def probe_fingerprint(self, context: CollectorContext) -> str: ...

    def collect(self, context: CollectorContext) -> CollectorResult: ...
