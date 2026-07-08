"""Deterministic source-slice candidate policy for Hades project awareness."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
import hashlib
from pathlib import Path
from typing import Any


SENSITIVE_NAMES = {".env", ".env.local", ".env.production", "id_rsa", "id_ed25519"}
SKIP_PARTS = {".git", "vendor", "node_modules", "__pycache__", "storage", "var", "tmp"}
ROLE_PRIORITY = {
    "controller": 10,
    "laravel_controller": 10,
    "eloquent_model": 20,
    "model": 20,
    "policy": 30,
    "authorization_policy": 30,
    "middleware": 40,
    "form_request": 50,
    "schema_migration": 60,
    "route_file": 70,
    "test": 80,
    "log": 90,
}


@dataclass(frozen=True)
class SourceSliceCandidate:
    path: str
    start_line: int
    end_line: int
    symbol: str
    reason: str
    priority: int
    head_commit: str = ""
    raw_source_included: bool = False
    retention_class: str = "source_slice_candidate"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["candidate_key"] = candidate_key(self, self.head_commit)
        return data


def candidate_key(candidate: SourceSliceCandidate, head_commit: str) -> str:
    material = "|".join(
        [
            str(head_commit or ""),
            candidate.path,
            str(candidate.start_line),
            str(candidate.end_line),
            candidate.symbol,
            candidate.reason,
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def plan_source_slice_candidates(
    workspace_root: Path,
    graph: Mapping[str, Any],
    *,
    head_commit: str = "",
    max_candidates: int = 200,
) -> list[dict[str, Any]]:
    root = Path(workspace_root)
    candidates: dict[tuple[str, str], SourceSliceCandidate] = {}
    for symbol in _iter_mappings(graph.get("symbols")):
        path = str(symbol.get("path") or "").strip()
        if not _path_allowed(root, path):
            continue
        reason = _reason_for_symbol(symbol, path)
        candidate = _candidate_for_path(
            root=root,
            path=path,
            line=_bounded_line(symbol.get("line")),
            symbol=str(symbol.get("name") or symbol.get("symbol") or ""),
            reason=reason,
            head_commit=head_commit,
        )
        _put_best(candidates, candidate)

    database = graph.get("database")
    tables = database.get("tables") if isinstance(database, Mapping) else None
    for table in _iter_mappings(tables):
        path = str(table.get("path") or "").strip()
        if not _path_allowed(root, path):
            continue
        candidate = _candidate_for_path(
            root=root,
            path=path,
            line=_bounded_line(table.get("line")),
            symbol=str(table.get("name") or ""),
            reason="schema_migration",
            head_commit=head_commit,
        )
        _put_best(candidates, candidate)

    for route in _iter_mappings(graph.get("routes")):
        path = str(route.get("path") or route.get("source_path") or "").strip()
        if not path or not _path_allowed(root, path):
            continue
        candidate = _candidate_for_path(
            root=root,
            path=path,
            line=_bounded_line(route.get("line")),
            symbol=str(route.get("name") or route.get("handler") or route.get("path") or ""),
            reason="route_file",
            head_commit=head_commit,
        )
        _put_best(candidates, candidate)

    ordered = sorted(candidates.values(), key=lambda item: (item.priority, item.path, item.start_line, item.symbol))
    return [candidate.to_dict() for candidate in ordered[: max(0, int(max_candidates))]]


def _iter_mappings(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _reason_for_symbol(symbol: Mapping[str, Any], path: str) -> str:
    role = str(symbol.get("role") or "").strip().lower()
    lowered = path.lower()
    if "controller" in role or "/controllers/" in lowered:
        return "laravel_controller"
    if "policy" in role or "/policies/" in lowered:
        return "authorization_policy"
    if "middleware" in role or "/middleware/" in lowered:
        return "middleware"
    if "request" in role or "/requests/" in lowered:
        return "form_request"
    if "eloquent" in role or "/models/" in lowered:
        return "eloquent_model"
    return role or "symbol"


def _candidate_for_path(*, root: Path, path: str, line: int, symbol: str, reason: str, head_commit: str) -> SourceSliceCandidate:
    total_lines = _line_count(root / path)
    center = line if line > 0 else 1
    start_line = max(1, center - 12)
    end_line = min(max(total_lines, start_line), center + 24)
    return SourceSliceCandidate(
        path=path,
        start_line=start_line,
        end_line=end_line,
        symbol=symbol,
        reason=reason,
        priority=ROLE_PRIORITY.get(reason, 500),
        head_commit=head_commit,
    )


def _put_best(candidates: dict[tuple[str, str], SourceSliceCandidate], candidate: SourceSliceCandidate) -> None:
    key = (candidate.path, candidate.symbol)
    existing = candidates.get(key)
    if existing is None or candidate.priority < existing.priority:
        candidates[key] = candidate


def _path_allowed(root: Path, rel: str) -> bool:
    if not rel or rel.startswith("/") or ".." in Path(rel).parts:
        return False
    path = Path(rel)
    if path.name in SENSITIVE_NAMES:
        return False
    if any(part in SKIP_PARTS for part in path.parts):
        return False
    resolved = (root / rel).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        return False
    return resolved.is_file()


def _bounded_line(value: Any) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 1


def _line_count(path: Path) -> int:
    try:
        return len(path.read_text(encoding="utf-8", errors="replace").splitlines()) or 1
    except OSError:
        return 1
