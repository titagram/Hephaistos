"""Local execution for Hades backend requested read-only jobs."""

from __future__ import annotations

import ast
import hashlib
import os
from pathlib import Path
from typing import Any

from hermes_cli.hades_backend_client import redact_secret


SKIP_DIRS = {".git", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", "node_modules"}


def _safe_relpath(path: str) -> str:
    return str(path).replace("\\", "/").lstrip("/")


def _resolve_inside(root: Path, rel: str) -> Path:
    candidate = (root / rel).resolve()
    root_resolved = root.resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"path escapes workspace: {rel}") from exc
    return candidate


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_text_bounded(path: Path, max_bytes: int) -> tuple[str, bool, str]:
    limit = max(0, max_bytes)
    raw = path.read_bytes()[:limit]
    truncated = path.stat().st_size > len(raw)
    digest = _hash_bytes(raw)
    return raw.decode("utf-8", errors="replace"), truncated, digest


def _execute_read_files(job: dict[str, Any], workspace_root: Path) -> dict[str, Any]:
    payload = job.get("payload") or {}
    paths = [_safe_relpath(p) for p in payload.get("paths") or []]
    max_bytes = int(payload.get("max_bytes") or 512_000)
    attachments: list[dict[str, Any]] = []
    omitted: list[dict[str, str]] = []
    for rel in paths[: int(payload.get("max_files") or 20)]:
        try:
            path = _resolve_inside(workspace_root, rel)
            if not path.is_file():
                omitted.append({"path": rel, "reason": "not_file"})
                continue
            text, truncated, digest = _read_text_bounded(path, max_bytes)
            redacted = redact_secret(text)
            attachments.append(
                {
                    "path": rel,
                    "sha256": digest,
                    "content": redacted,
                    "truncated": truncated,
                    "redactions": 1 if redacted != text else 0,
                }
            )
        except Exception as exc:
            omitted.append({"path": rel, "reason": str(exc)})
    return {
        "status": "completed",
        "summary": f"Read {len(attachments)} file(s); omitted {len(omitted)}.",
        "attachments": attachments,
        "omitted": omitted,
    }


def _iter_workspace_files(root: Path, *, max_files: int) -> list[Path]:
    files: list[Path] = []
    for current, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".venv")]
        for name in sorted(names):
            path = Path(current) / name
            if path.is_file():
                files.append(path)
                if len(files) >= max_files:
                    return files
    return files


def _execute_sync_git_tree(job: dict[str, Any], workspace_root: Path) -> dict[str, Any]:
    payload = job.get("payload") or {}
    max_files = int(payload.get("max_files") or 10_000)
    max_bytes = int(payload.get("max_bytes") or 2_000_000)
    files: list[dict[str, Any]] = []
    total_bytes = 0
    truncated = False
    for path in _iter_workspace_files(workspace_root, max_files=max_files):
        rel = path.relative_to(workspace_root).as_posix()
        size = path.stat().st_size
        if total_bytes + size > max_bytes:
            truncated = True
            break
        total_bytes += size
        files.append(
            {
                "path": rel,
                "bytes": size,
                "sha256": _hash_bytes(path.read_bytes()),
            }
        )
    return {
        "status": "completed",
        "summary": f"Collected {len(files)} git tree entrie(s).",
        "artifact": {
            "schema": "hades.git_tree.v1",
            "root": workspace_root.name,
            "files": files,
            "truncated": truncated,
        },
    }


def _execute_populate_backend_ast(job: dict[str, Any], workspace_root: Path) -> dict[str, Any]:
    payload = job.get("payload") or {}
    max_files = int(payload.get("max_files") or 1_000)
    max_symbols = int(payload.get("max_symbols") or 5_000)
    symbols: list[dict[str, Any]] = []
    omitted: list[dict[str, str]] = []
    for path in _iter_workspace_files(workspace_root, max_files=max_files):
        if path.suffix != ".py":
            continue
        rel = path.relative_to(workspace_root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError as exc:
            omitted.append({"path": rel, "reason": f"syntax_error:{exc.lineno}"})
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                symbols.append({"kind": "class", "name": node.name, "path": rel, "line": node.lineno})
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbols.append({"kind": "function", "name": node.name, "path": rel, "line": node.lineno})
            if len(symbols) >= max_symbols:
                break
        if len(symbols) >= max_symbols:
            break
    return {
        "status": "completed",
        "summary": f"Collected {len(symbols)} symbol(s).",
        "artifact": {
            "schema": "hades.symbols.v1",
            "symbols": symbols,
            "omitted": omitted,
            "truncated": len(symbols) >= max_symbols,
        },
    }


def execute_job(job: dict[str, Any], *, workspace_root: str | Path) -> dict[str, Any]:
    root = Path(workspace_root).resolve()
    capability = str(job.get("capability") or "")
    if capability == "read_files":
        return _execute_read_files(job, root)
    if capability == "sync_git_tree":
        return _execute_sync_git_tree(job, root)
    if capability == "populate_backend_ast":
        return _execute_populate_backend_ast(job, root)
    if capability == "project_inspection":
        return _execute_sync_git_tree(job, root)
    return {
        "status": "failed",
        "summary": f"Unsupported Hades backend job capability: {capability}",
        "omitted": [{"reason": "unsupported_capability"}],
    }
