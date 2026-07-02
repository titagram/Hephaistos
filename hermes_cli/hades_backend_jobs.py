"""Local execution for Hades backend requested read-only jobs."""

from __future__ import annotations

import ast
import hashlib
import os
from pathlib import Path
from typing import Any

from hermes_cli.hades_backend_client import redact_secret


SKIP_DIRS = {
    ".cache",
    ".git",
    ".mypy_cache",
    ".next",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".turbo",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "target",
    "vendor",
}
SECRET_FILE_NAMES = {
    ".env",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "credentials",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
}
SECRET_SUFFIXES = {
    ".cert",
    ".crt",
    ".der",
    ".key",
    ".p12",
    ".pem",
    ".pfx",
}
BINARY_SUFFIXES = {
    ".7z",
    ".avi",
    ".bmp",
    ".dmg",
    ".exe",
    ".gif",
    ".gz",
    ".ico",
    ".jpeg",
    ".jpg",
    ".mov",
    ".mp3",
    ".mp4",
    ".pdf",
    ".png",
    ".tar",
    ".webm",
    ".webp",
    ".woff",
    ".woff2",
    ".zip",
}


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


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_text_bounded(path: Path, max_bytes: int) -> tuple[str, bool, str]:
    limit = max(0, max_bytes)
    with path.open("rb") as handle:
        raw = handle.read(limit + 1)
    truncated = len(raw) > limit or path.stat().st_size > limit
    bounded = raw[:limit]
    digest = _hash_bytes(bounded)
    return bounded.decode("utf-8", errors="replace"), truncated, digest


def _read_lines(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return handle.readlines()


def _gitignore_spec(root: Path):
    ignore_file = root / ".gitignore"
    if not ignore_file.is_file():
        return None
    try:
        import pathspec

        return pathspec.PathSpec.from_lines("gitignore", _read_lines(ignore_file))
    except Exception:
        return None


def _skip_file_reason(path: Path, rel: str, ignore_spec=None) -> str | None:
    name = path.name
    lowered = name.lower()
    if lowered in SECRET_FILE_NAMES or lowered.startswith(".env."):
        return "sensitive_name"
    if lowered.startswith("secret") or lowered.startswith("secrets."):
        return "sensitive_name"
    if path.suffix.lower() in SECRET_SUFFIXES:
        return "sensitive_suffix"
    if path.suffix.lower() in BINARY_SUFFIXES:
        return "binary_or_archive"
    if ignore_spec is not None and ignore_spec.match_file(rel):
        return "gitignored"
    return None


def _execute_read_files(job: dict[str, Any], workspace_root: Path) -> dict[str, Any]:
    payload = job.get("payload") or {}
    paths = [_safe_relpath(p) for p in payload.get("paths") or []]
    max_bytes = int(payload.get("max_bytes") or 512_000)
    ignore_spec = _gitignore_spec(workspace_root)
    attachments: list[dict[str, Any]] = []
    omitted: list[dict[str, str]] = []
    for rel in paths[: int(payload.get("max_files") or 20)]:
        try:
            path = _resolve_inside(workspace_root, rel)
            if not path.is_file():
                omitted.append({"path": rel, "reason": "not_file"})
                continue
            skip_reason = _skip_file_reason(path, rel, ignore_spec)
            if skip_reason:
                omitted.append({"path": rel, "reason": skip_reason})
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
        "redactions": sum(1 for attachment in attachments if attachment["redactions"]) + len(omitted),
        "retention_class": "source_content",
    }


def _iter_workspace_files(root: Path, *, max_files: int) -> tuple[list[Path], list[dict[str, str]], bool]:
    ignore_spec = _gitignore_spec(root)
    files: list[Path] = []
    omitted: list[dict[str, str]] = []
    for current, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(names):
            path = Path(current) / name
            rel = path.relative_to(root).as_posix()
            skip_reason = _skip_file_reason(path, rel, ignore_spec)
            if skip_reason:
                omitted.append({"path": rel, "reason": skip_reason})
                continue
            if path.is_file():
                files.append(path)
                if len(files) >= max_files:
                    return files, omitted, True
    return files, omitted, False


def _execute_sync_git_tree(job: dict[str, Any], workspace_root: Path) -> dict[str, Any]:
    payload = job.get("payload") or {}
    max_files = int(payload.get("max_files") or 10_000)
    max_bytes = int(payload.get("max_bytes") or 2_000_000)
    max_file_bytes = int(payload.get("max_file_bytes") or 1_000_000)
    files: list[dict[str, Any]] = []
    total_bytes = 0
    candidates, omitted, truncated = _iter_workspace_files(workspace_root, max_files=max_files)
    for path in candidates:
        rel = path.relative_to(workspace_root).as_posix()
        size = path.stat().st_size
        if size > max_file_bytes:
            omitted.append({"path": rel, "reason": "file_too_large"})
            truncated = True
            continue
        if total_bytes + size > max_bytes:
            truncated = True
            omitted.append({"path": rel, "reason": "byte_budget_exceeded"})
            break
        total_bytes += size
        files.append(
            {
                "path": rel,
                "bytes": size,
                "sha256": _hash_file(path),
            }
        )
    return {
        "status": "completed",
        "summary": f"Collected {len(files)} git tree entrie(s).",
        "artifact": {
            "schema": "hades.git_tree.v1",
            "root": workspace_root.name,
            "files": files,
            "omitted": omitted,
            "truncated": truncated,
            "redactions": len(omitted),
            "retention_class": "source_metadata",
            "raw_source_included": False,
        },
    }


def _execute_populate_backend_ast(job: dict[str, Any], workspace_root: Path) -> dict[str, Any]:
    payload = job.get("payload") or {}
    max_files = int(payload.get("max_files") or 1_000)
    max_symbols = int(payload.get("max_symbols") or 5_000)
    max_file_bytes = int(payload.get("max_file_bytes") or 512_000)
    symbols: list[dict[str, Any]] = []
    candidates, omitted, truncated = _iter_workspace_files(workspace_root, max_files=max_files)
    for path in candidates:
        if path.suffix != ".py":
            continue
        rel = path.relative_to(workspace_root).as_posix()
        if path.stat().st_size > max_file_bytes:
            omitted.append({"path": rel, "reason": "file_too_large"})
            truncated = True
            continue
        try:
            source, was_truncated, _digest = _read_text_bounded(path, max_file_bytes)
            if was_truncated:
                omitted.append({"path": rel, "reason": "file_too_large"})
                truncated = True
                continue
            tree = ast.parse(source)
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
            "truncated": truncated or len(symbols) >= max_symbols,
            "redactions": len(omitted),
            "retention_class": "source_symbols",
            "raw_source_included": False,
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
