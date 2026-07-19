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
    "entrypoint_root": 10,
    "middleware_security_input": 20,
    "branch_unresolved": 30,
    "domain_data_integration": 40,
    "test": 50,
}

_PIPELINE_NODE_KINDS = frozenset({
    "middleware", "guard", "authorization", "validator", "binding",
})
_BRANCH_NODE_KINDS = frozenset({
    "branch", "merge", "loop", "unknown_boundary",
})
_DOMAIN_NODE_KINDS = frozenset({
    "service", "domain", "model", "repository", "table", "query", "cache",
    "storage", "integration", "external_boundary",
})


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
    if graph.get("schema") != "hades.code_graph.v2":
        return []
    candidates: dict[tuple[str, str], SourceSliceCandidate] = {}
    nodes = {
        str(node.get("id") or ""): node
        for node in _iter_mappings(graph.get("nodes"))
        if str(node.get("id") or "")
    }

    for entrypoint in _iter_mappings(graph.get("entrypoints")):
        symbol = str(
            entrypoint.get("public_name")
            or entrypoint.get("public_path")
            or entrypoint.get("label")
            or entrypoint.get("id")
            or ""
        )
        handler = nodes.get(str(entrypoint.get("handler_node_id") or ""))
        if handler is not None:
            _put_node_candidate(
                candidates,
                root=root,
                node=handler,
                symbol=symbol,
                reason="entrypoint_root",
                head_commit=head_commit,
            )
        occurrence = entrypoint.get("registration_occurrence")
        if isinstance(occurrence, Mapping):
            path = str(occurrence.get("path") or "").strip()
            if _path_allowed(root, path):
                _put_best(
                    candidates,
                    _candidate_for_path(
                        root=root,
                        path=path,
                        line=1,
                        symbol=symbol,
                        reason="entrypoint_root",
                        head_commit=head_commit,
                    ),
                )

    for node in nodes.values():
        kind = str(node.get("kind") or "").strip()
        location = node.get("location")
        source_path = (
            str(location.get("path") or "").strip()
            if isinstance(location, Mapping)
            else ""
        )
        reason = ""
        if kind in _PIPELINE_NODE_KINDS:
            reason = "middleware_security_input"
        elif kind in _BRANCH_NODE_KINDS or node.get("uncertainty_id"):
            reason = "branch_unresolved"
        elif kind in _DOMAIN_NODE_KINDS:
            reason = "domain_data_integration"
        elif kind == "test" or _is_test_source_path(source_path):
            reason = "test"
        if reason:
            _put_node_candidate(
                candidates,
                root=root,
                node=node,
                symbol=str(node.get("qualified_name") or node.get("name") or node.get("id") or ""),
                reason=reason,
                head_commit=head_commit,
            )

    for uncertainty in _iter_mappings(graph.get("uncertainties")):
        for source_ref in _iter_mappings(uncertainty.get("source_refs")):
            path = str(source_ref.get("path") or "").strip()
            if not _path_allowed(root, path):
                continue
            _put_best(
                candidates,
                _candidate_for_path(
                    root=root,
                    path=path,
                    line=_bounded_line(source_ref.get("line")),
                    symbol=str(uncertainty.get("id") or "unresolved"),
                    reason="branch_unresolved",
                    head_commit=head_commit,
                ),
            )

    ordered = sorted(candidates.values(), key=lambda item: (item.priority, item.path, item.start_line, item.symbol))
    return [candidate.to_dict() for candidate in ordered[: max(0, int(max_candidates))]]


def _iter_mappings(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _is_test_source_path(path: str) -> bool:
    """Recognize conventional test locations without changing graph node kinds."""

    normalized = path.replace("\\", "/").strip("/")
    if not normalized:
        return False
    parts = normalized.split("/")
    directories = {part.casefold() for part in parts[:-1]}
    if directories & {"test", "tests", "__tests__", "spec", "specs"}:
        return True
    name = parts[-1].casefold()
    stem = Path(name).stem
    return (
        stem.startswith("test_")
        or stem.endswith("_test")
        or name.endswith(
            (
                ".test.js",
                ".test.jsx",
                ".test.ts",
                ".test.tsx",
                ".spec.js",
                ".spec.jsx",
                ".spec.ts",
                ".spec.tsx",
                "test.php",
            )
        )
    )


def _put_node_candidate(
    candidates: dict[tuple[str, str], SourceSliceCandidate],
    *,
    root: Path,
    node: Mapping[str, Any],
    symbol: str,
    reason: str,
    head_commit: str,
) -> None:
    location = node.get("location")
    if not isinstance(location, Mapping):
        return
    path = str(location.get("path") or "").strip()
    if not _path_allowed(root, path):
        return
    _put_best(
        candidates,
        _candidate_for_path(
            root=root,
            path=path,
            line=_bounded_line(location.get("start_line")),
            symbol=symbol,
            reason=reason,
            head_commit=head_commit,
        ),
    )


def _candidate_for_path(*, root: Path, path: str, line: int, symbol: str, reason: str, head_commit: str) -> SourceSliceCandidate:
    center = line if line > 0 else 1
    start_line = max(1, center - 12)
    end_line = center + 24
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
