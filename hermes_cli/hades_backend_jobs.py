"""Local execution for Hades backend requested read-only jobs."""

from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import re
import tomllib
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
LANGUAGE_SUFFIXES = {
    ".css": "css",
    ".go": "go",
    ".js": "javascript",
    ".jsx": "javascript",
    ".md": "markdown",
    ".php": "php",
    ".py": "python",
    ".rb": "ruby",
    ".rs": "rust",
    ".sh": "shell",
    ".sql": "sql",
    ".ts": "typescript",
    ".tsx": "typescript",
}
DEPENDENCY_MANIFESTS = {
    "composer.json": "composer",
    "package.json": "npm",
    "pyproject.toml": "python",
    "requirements.txt": "python",
}
ROUTE_CALL_RE = re.compile(
    r"Route::(?P<method>get|post|put|patch|delete|options|any)\s*"
    r"\(\s*['\"](?P<uri>[^'\"]+)['\"]\s*,\s*(?P<handler>.*?)\)\s*"
    r"(?:->name\(\s*['\"](?P<name>[^'\"]+)['\"]\s*\))?",
    re.IGNORECASE | re.DOTALL,
)
LARAVEL_HANDLER_RE = re.compile(
    r"\[\s*(?P<class>[A-Za-z0-9_\\\\]+)::class\s*,\s*['\"](?P<method>[A-Za-z0-9_]+)['\"]\s*\]"
)
PHP_NAMESPACE_RE = re.compile(r"^\s*namespace\s+(?P<namespace>[A-Za-z0-9_\\]+)\s*;", re.MULTILINE)
PHP_CLASS_RE = re.compile(
    r"\b(?P<kind>class|interface|trait|enum)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?:\s+extends\s+(?P<extends>[A-Za-z0-9_\\]+))?",
    re.MULTILINE,
)
PHP_METHOD_RE = re.compile(
    r"\b(?P<visibility>public|protected|private)\s+(?:static\s+)?function\s+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\(",
    re.MULTILINE,
)
PHP_ELOQUENT_RELATION_RE = re.compile(
    r"\$this->(?P<relation>hasOne|hasMany|belongsTo|belongsToMany|morphOne|morphMany|morphToMany)"
    r"\s*\(\s*(?P<target>[A-Za-z0-9_\\]+)::class",
    re.MULTILINE,
)
PHP_STATIC_CALL_RE = re.compile(r"\b(?P<class>[A-Z][A-Za-z0-9_\\]+)::(?P<method>[A-Za-z_][A-Za-z0-9_]*)\s*\(")
PHP_NEW_RE = re.compile(r"\bnew\s+(?P<class>[A-Z][A-Za-z0-9_\\]+)\s*\(")


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


def _io_error_reason(prefix: str, exc: OSError) -> str:
    errno = getattr(exc, "errno", None)
    return f"{prefix}:{errno}" if errno is not None else prefix


def _safe_exception_reason(exc: Exception) -> str:
    if isinstance(exc, OSError):
        return _io_error_reason("read_error", exc)
    if isinstance(exc, ValueError):
        return str(exc)
    return exc.__class__.__name__


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
    if path.is_symlink():
        return "symlink"
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


def _language_for_path(path: str) -> str:
    suffix = Path(path).suffix.lower()
    return LANGUAGE_SUFFIXES.get(suffix, "other")


def _language_counts(files: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for item in files:
        language = _language_for_path(str(item.get("path") or ""))
        current = counts.setdefault(language, {"files": 0, "bytes": 0})
        current["files"] += 1
        current["bytes"] += int(item.get("bytes") or 0)
    return {key: counts[key] for key in sorted(counts)}


def _dependency_packages_from_json(path: Path, manager: str) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    packages: set[str] = set()
    if manager == "composer":
        for section in ("require", "require-dev"):
            values = data.get(section)
            if isinstance(values, dict):
                packages.update(str(name) for name in values if str(name).lower() != "php")
    elif manager == "npm":
        for section in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
            values = data.get(section)
            if isinstance(values, dict):
                packages.update(str(name) for name in values)
    return sorted(packages)


def _dependency_packages_from_pyproject(path: Path) -> list[str]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    packages: set[str] = set()
    project = data.get("project")
    if isinstance(project, dict):
        dependencies = project.get("dependencies")
        if isinstance(dependencies, list):
            packages.update(str(dep).split(";", 1)[0].split("[", 1)[0].split("=", 1)[0].strip() for dep in dependencies)
        optional = project.get("optional-dependencies")
        if isinstance(optional, dict):
            for values in optional.values():
                if isinstance(values, list):
                    packages.update(str(dep).split(";", 1)[0].split("[", 1)[0].split("=", 1)[0].strip() for dep in values)
    tool = data.get("tool")
    poetry = tool.get("poetry") if isinstance(tool, dict) else None
    if isinstance(poetry, dict):
        for section in ("dependencies", "dev-dependencies"):
            values = poetry.get(section)
            if isinstance(values, dict):
                packages.update(str(name) for name in values if str(name).lower() != "python")
    return sorted(pkg for pkg in packages if pkg)


def _dependency_packages_from_requirements(path: Path) -> list[str]:
    packages: set[str] = set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        clean = line.strip()
        if not clean or clean.startswith("#") or clean.startswith(("-", "git+")):
            continue
        packages.add(re.split(r"[<>=~!;\[]", clean, maxsplit=1)[0].strip())
    return sorted(pkg for pkg in packages if pkg)


def _dependency_manifests(root: Path, files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    manifests: list[dict[str, Any]] = []
    for item in files:
        rel = str(item.get("path") or "")
        name = Path(rel).name
        manager = DEPENDENCY_MANIFESTS.get(name)
        if manager is None:
            continue
        path = root / rel
        try:
            if name in {"composer.json", "package.json"}:
                packages = _dependency_packages_from_json(path, manager)
            elif name == "pyproject.toml":
                packages = _dependency_packages_from_pyproject(path)
            else:
                packages = _dependency_packages_from_requirements(path)
        except Exception:
            packages = []
        manifests.append({"manager": manager, "path": rel, "packages": packages[:200]})
    return sorted(manifests, key=lambda item: (item["manager"], item["path"]))


def _normalize_laravel_handler(raw: str) -> str:
    compact = " ".join(str(raw or "").split())
    match = LARAVEL_HANDLER_RE.search(compact)
    if match:
        class_name = match.group("class").split("\\")[-1]
        return f"{class_name}@{match.group('method')}"
    return compact[:160]


def _line_number(content: str, offset: int) -> int:
    return content.count("\n", 0, max(0, offset)) + 1


def _php_namespace(content: str) -> str:
    match = PHP_NAMESPACE_RE.search(content)
    return match.group("namespace") if match else ""


def _php_fqcn(namespace: str, name: str) -> str:
    clean = name.strip("\\")
    if "\\" in clean or namespace == "":
        return clean
    return f"{namespace}\\{clean}"


def _php_short_name(name: str) -> str:
    return name.strip("\\").split("\\")[-1]


def _php_role(path: str, class_name: str, extends: str) -> str:
    short = _php_short_name(class_name)
    if path.startswith("app/Http/Controllers/") or short.endswith("Controller"):
        return "controller"
    if path.startswith("app/Models/") or _php_short_name(extends) == "Model":
        return "model"
    if path.startswith("app/Http/Middleware/") or short.endswith("Middleware"):
        return "middleware"
    if path.startswith("app/Jobs/") or short.endswith("Job"):
        return "job"
    if path.startswith("app/Events/") or short.endswith("Event"):
        return "event"
    if path.startswith("app/Policies/") or short.endswith("Policy"):
        return "policy"
    if path.startswith("app/Services/") or short.endswith("Service"):
        return "service"
    return "php_class"


def _class_context(classes: list[dict[str, Any]], offset: int) -> dict[str, Any] | None:
    current = None
    for item in classes:
        if int(item["offset"]) > offset:
            break
        current = item
    return current


def _edge_append(edges: list[dict[str, Any]], edge: dict[str, Any], *, max_edges: int) -> bool:
    if len(edges) >= max_edges:
        return False
    edges.append({key: value for key, value in edge.items() if value not in ("", None)})
    return True


def _laravel_routes(root: Path, files: list[dict[str, Any]], *, max_routes: int = 500) -> list[dict[str, str]]:
    routes: list[dict[str, str]] = []
    for item in files:
        rel = str(item.get("path") or "")
        if not (rel.startswith("routes/") and rel.endswith(".php")):
            continue
        try:
            content, truncated, _digest = _read_text_bounded(root / rel, 256_000)
        except OSError:
            continue
        if truncated:
            continue
        for match in ROUTE_CALL_RE.finditer(content):
            route = {
                "method": match.group("method").upper(),
                "uri": match.group("uri"),
                "handler": _normalize_laravel_handler(match.group("handler")),
                "path": rel,
            }
            name = match.group("name")
            if name:
                route["name"] = name
            routes.append(route)
            if len(routes) >= max_routes:
                return routes
    return routes


def _database_summary(files: list[dict[str, Any]]) -> dict[str, Any]:
    migrations = sorted(
        str(item.get("path") or "")
        for item in files
        if str(item.get("path") or "").startswith("database/migrations/")
    )
    return {"migrations": migrations[:500], "migration_count": len(migrations)}


def _project_index_summary(index: dict[str, Any]) -> str:
    route_bits = [
        f"{route['method']} {route['uri']} -> {route.get('handler', '')}".strip()
        for route in index.get("routes", [])[:5]
    ]
    package_bits: list[str] = []
    for manifest in index.get("dependency_manifests", [])[:4]:
        packages = manifest.get("packages") or []
        if packages:
            package_bits.extend(str(pkg) for pkg in packages[:5])
    migration_count = int((index.get("database") or {}).get("migration_count") or 0)
    parts = [
        f"routes: {', '.join(route_bits) or 'none'}",
        f"dependencies: {', '.join(package_bits) or 'none'}",
        f"migrations: {migration_count}",
    ]
    return "Project index; " + "; ".join(parts)


def _build_project_index(root: Path, files: list[dict[str, Any]]) -> dict[str, Any]:
    index = {
        "schema": "hades.project_index.v1",
        "source_schema": "hades.git_tree.v1",
        "root": root.name,
        "language_counts": _language_counts(files),
        "routes": _laravel_routes(root, files),
        "dependency_manifests": _dependency_manifests(root, files),
        "database": _database_summary(files),
        "raw_source_included": False,
    }
    index["summary"] = _project_index_summary(index)
    return index


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
            omitted.append({"path": rel, "reason": _safe_exception_reason(exc)})
    return {
        "status": "completed",
        "summary": f"Read {len(attachments)} file(s); omitted {len(omitted)}.",
        "attachments": attachments,
        "omitted": omitted,
        "redactions": sum(1 for attachment in attachments if attachment["redactions"]) + len(omitted),
        "retention_class": "source_content",
    }


def _line_window(content: str, start_line: int, end_line: int) -> tuple[str, int, int]:
    lines = content.splitlines()
    if not lines:
        return "", 1, 1
    start = max(1, start_line)
    end = max(start, end_line)
    bounded_start = min(start, len(lines))
    bounded_end = min(end, len(lines))
    selected = lines[bounded_start - 1 : bounded_end]
    return "\n".join(selected), bounded_start, bounded_end


def _execute_read_source_slice(job: dict[str, Any], workspace_root: Path) -> dict[str, Any]:
    payload = job.get("payload") or {}
    rel = _safe_relpath(str(payload.get("path") or ""))
    if not rel:
        return {
            "status": "failed",
            "summary": "Missing source slice path.",
            "omitted": [{"reason": "missing_path"}],
            "retention_class": "source_slice",
        }
    start_line = max(1, int(payload.get("start_line") or payload.get("line") or 1))
    end_line = max(start_line, int(payload.get("end_line") or start_line))
    if end_line - start_line + 1 > int(payload.get("max_lines") or 120):
        end_line = start_line + int(payload.get("max_lines") or 120) - 1
    max_file_bytes = int(payload.get("max_file_bytes") or 512_000)
    max_slice_bytes = int(payload.get("max_slice_bytes") or 64_000)
    ignore_spec = _gitignore_spec(workspace_root)

    try:
        path = _resolve_inside(workspace_root, rel)
        if not path.is_file():
            return {
                "status": "failed",
                "summary": f"Source slice path is not a file: {rel}",
                "omitted": [{"path": rel, "reason": "not_file"}],
                "retention_class": "source_slice",
            }
        skip_reason = _skip_file_reason(path, rel, ignore_spec)
        if skip_reason:
            return {
                "status": "failed",
                "summary": f"Source slice path omitted: {skip_reason}",
                "omitted": [{"path": rel, "reason": skip_reason}],
                "retention_class": "source_slice",
            }
        size = path.stat().st_size
        if size > max_file_bytes:
            return {
                "status": "failed",
                "summary": f"Source slice file exceeds max_file_bytes: {rel}",
                "omitted": [{"path": rel, "reason": "file_too_large"}],
                "retention_class": "source_slice",
            }
        source, was_truncated, _digest = _read_text_bounded(path, max_file_bytes)
    except Exception as exc:
        return {
            "status": "failed",
            "summary": f"Failed to read source slice: {_safe_exception_reason(exc)}",
            "omitted": [{"path": rel, "reason": _safe_exception_reason(exc)}],
            "retention_class": "source_slice",
        }

    content, bounded_start, bounded_end = _line_window(source, start_line, end_line)
    redacted = redact_secret(content)
    redaction_count = 1 if redacted != content else 0
    encoded = redacted.encode("utf-8")
    truncated = was_truncated
    if len(encoded) > max_slice_bytes:
        redacted = encoded[:max_slice_bytes].decode("utf-8", errors="ignore")
        truncated = True
    source_slice = {
        "path": rel,
        "start_line": bounded_start,
        "end_line": bounded_end,
        "language": _language_for_path(rel),
        "symbol": str(payload.get("symbol") or "").strip(),
        "content_redacted": redacted,
        "sha256": _hash_bytes(redacted.encode("utf-8")),
        "redactions": redaction_count,
        "truncated": truncated,
        "retention_class": "source_slice",
        "policy": str(payload.get("policy") or "manual_review"),
        "raw_source_included": True,
    }
    return {
        "status": "completed",
        "summary": f"Read source slice {rel}:{bounded_start}-{bounded_end}; redactions {source_slice['redactions']}.",
        "source_slice": {key: value for key, value in source_slice.items() if value not in ("", None)},
        "redactions": source_slice["redactions"],
        "retention_class": "source_slice",
    }


def _iter_workspace_files(root: Path, *, max_files: int) -> tuple[list[Path], list[dict[str, str]], bool]:
    ignore_spec = _gitignore_spec(root)
    files: list[Path] = []
    omitted: list[dict[str, str]] = []
    for current, dirs, names in os.walk(root):
        current_path = Path(current)
        kept_dirs: list[str] = []
        for dirname in sorted(dirs):
            dir_path = current_path / dirname
            rel = dir_path.relative_to(root).as_posix()
            if dirname in SKIP_DIRS:
                omitted.append({"path": rel, "reason": "generated_or_dependency_dir"})
                continue
            if dir_path.is_symlink():
                omitted.append({"path": rel, "reason": "symlink"})
                continue
            if ignore_spec is not None and (
                ignore_spec.match_file(rel) or ignore_spec.match_file(rel + "/")
            ):
                omitted.append({"path": rel, "reason": "gitignored"})
                continue
            kept_dirs.append(dirname)
        dirs[:] = kept_dirs
        for name in sorted(names):
            path = current_path / name
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
        try:
            size = path.stat().st_size
        except OSError as exc:
            omitted.append({"path": rel, "reason": _io_error_reason("stat_error", exc)})
            continue
        if size > max_file_bytes:
            omitted.append({"path": rel, "reason": "file_too_large"})
            truncated = True
            continue
        if total_bytes + size > max_bytes:
            truncated = True
            omitted.append({"path": rel, "reason": "byte_budget_exceeded"})
            break
        try:
            digest = _hash_file(path)
        except OSError as exc:
            omitted.append({"path": rel, "reason": _io_error_reason("read_error", exc)})
            continue
        total_bytes += size
        files.append(
            {
                "path": rel,
                "bytes": size,
                "sha256": digest,
            }
        )
    project_index = _build_project_index(workspace_root, files)
    return {
        "status": "completed",
        "summary": (
            f"Collected {len(files)} git tree entries; "
            f"indexed {len(project_index['routes'])} route(s), "
            f"{len(project_index['dependency_manifests'])} dependency manifest(s)."
        ),
        "artifact": {
            "schema": "hades.git_tree.v1",
            "root": workspace_root.name,
            "files": files,
            "project_index": project_index,
            "summary": project_index["summary"],
            "omitted": omitted,
            "truncated": truncated,
            "redactions": len(omitted),
            "retention_class": "source_metadata",
            "raw_source_included": False,
        },
    }


def _execute_project_inspection(job: dict[str, Any], workspace_root: Path) -> dict[str, Any]:
    result = _execute_sync_git_tree(job, workspace_root)
    artifact = result.get("artifact")
    if isinstance(artifact, dict):
        artifact["requested_capability"] = "project_inspection"
        artifact["inspection_mode"] = "metadata_tree"
    files = artifact.get("files", []) if isinstance(artifact, dict) else []
    result["summary"] = f"Collected {len(files)} project metadata entries; raw source not included."
    return result


def _php_graph_summary(routes: list[dict[str, str]], symbols: list[dict[str, Any]], edges: list[dict[str, Any]]) -> str:
    role_counts: dict[str, int] = {}
    for symbol in symbols:
        role = str(symbol.get("role") or symbol.get("kind") or "symbol")
        role_counts[role] = role_counts.get(role, 0) + 1
    roles = ", ".join(f"{role}:{count}" for role, count in sorted(role_counts.items())[:8])
    return f"PHP graph; routes:{len(routes)}; symbols:{len(symbols)}; edges:{len(edges)}; {roles or 'roles:none'}"


def _php_route_edges(routes: list[dict[str, str]], edges: list[dict[str, Any]], *, max_edges: int) -> bool:
    truncated = False
    for route in routes:
        route_id = route.get("name") or f"{route.get('method', '')} {route.get('uri', '')}".strip()
        handler = route.get("handler", "")
        if "@" not in handler:
            continue
        if not _edge_append(
            edges,
            {
                "kind": "route_handler",
                "from": f"route:{route_id}",
                "to": handler,
                "method": route.get("method"),
                "uri": route.get("uri"),
                "path": route.get("path"),
            },
            max_edges=max_edges,
        ):
            truncated = True
            break
    return truncated


def _build_php_graph(
    workspace_root: Path,
    candidates: list[Path],
    omitted: list[dict[str, str]],
    *,
    truncated: bool,
    max_symbols: int,
    max_edges: int,
    max_file_bytes: int,
) -> dict[str, Any]:
    php_files = [path for path in candidates if path.suffix.lower() == ".php"]
    file_refs = [{"path": path.relative_to(workspace_root).as_posix()} for path in php_files]
    routes = _laravel_routes(workspace_root, file_refs)
    symbols: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    truncated = _php_route_edges(routes, edges, max_edges=max_edges) or truncated

    for path in php_files:
        rel = path.relative_to(workspace_root).as_posix()
        try:
            size = path.stat().st_size
        except OSError as exc:
            omitted.append({"path": rel, "reason": _io_error_reason("stat_error", exc)})
            continue
        if size > max_file_bytes:
            omitted.append({"path": rel, "reason": "file_too_large"})
            truncated = True
            continue
        try:
            source, was_truncated, _digest = _read_text_bounded(path, max_file_bytes)
            if was_truncated:
                omitted.append({"path": rel, "reason": "file_too_large"})
                truncated = True
                continue
        except OSError as exc:
            omitted.append({"path": rel, "reason": _io_error_reason("read_error", exc)})
            continue

        namespace = _php_namespace(source)
        classes: list[dict[str, Any]] = []
        for match in PHP_CLASS_RE.finditer(source):
            class_name = match.group("name")
            extends = match.group("extends") or ""
            fqcn = _php_fqcn(namespace, class_name)
            role = _php_role(rel, fqcn, extends)
            class_symbol = {
                "kind": match.group("kind"),
                "name": fqcn,
                "short_name": class_name,
                "role": role,
                "path": rel,
                "line": _line_number(source, match.start()),
                "extends": _php_fqcn(namespace, extends) if extends else None,
                "offset": match.start(),
            }
            classes.append(class_symbol)
            if len(symbols) < max_symbols:
                symbols.append({key: value for key, value in class_symbol.items() if key != "offset" and value not in ("", None)})
            else:
                truncated = True
            if extends:
                truncated = not _edge_append(
                    edges,
                    {
                        "kind": "extends",
                        "from": fqcn,
                        "to": _php_fqcn(namespace, extends),
                        "path": rel,
                        "line": _line_number(source, match.start()),
                    },
                    max_edges=max_edges,
                ) or truncated

        classes.sort(key=lambda item: int(item["offset"]))

        for match in PHP_METHOD_RE.finditer(source):
            class_info = _class_context(classes, match.start())
            if class_info is None:
                continue
            method_name = match.group("name")
            fqcn = str(class_info["name"])
            if len(symbols) < max_symbols:
                symbols.append(
                    {
                        "kind": "method",
                        "name": f"{_php_short_name(fqcn)}@{method_name}",
                        "class": fqcn,
                        "method": method_name,
                        "visibility": match.group("visibility"),
                        "role": class_info.get("role"),
                        "path": rel,
                        "line": _line_number(source, match.start()),
                    }
                )
            else:
                truncated = True

        for match in PHP_ELOQUENT_RELATION_RE.finditer(source):
            class_info = _class_context(classes, match.start())
            if class_info is None:
                continue
            truncated = not _edge_append(
                edges,
                {
                    "kind": "eloquent_relation",
                    "from": class_info["name"],
                    "to": _php_fqcn(namespace, match.group("target")),
                    "relation": match.group("relation"),
                    "path": rel,
                    "line": _line_number(source, match.start()),
                },
                max_edges=max_edges,
            ) or truncated

        for match in PHP_STATIC_CALL_RE.finditer(source):
            class_name = match.group("class")
            if _php_short_name(class_name) in {"self", "static", "parent", "Route"}:
                continue
            class_info = _class_context(classes, match.start())
            truncated = not _edge_append(
                edges,
                {
                    "kind": "static_call",
                    "from": class_info["name"] if class_info else rel,
                    "to": f"{_php_fqcn(namespace, class_name)}::{match.group('method')}",
                    "path": rel,
                    "line": _line_number(source, match.start()),
                },
                max_edges=max_edges,
            ) or truncated

        for match in PHP_NEW_RE.finditer(source):
            class_info = _class_context(classes, match.start())
            truncated = not _edge_append(
                edges,
                {
                    "kind": "instantiates",
                    "from": class_info["name"] if class_info else rel,
                    "to": _php_fqcn(namespace, match.group("class")),
                    "path": rel,
                    "line": _line_number(source, match.start()),
                },
                max_edges=max_edges,
            ) or truncated

    graph = {
        "schema": "hades.php_graph.v1",
        "language": "php",
        "framework": "laravel" if routes or (workspace_root / "artisan").exists() else "php",
        "root": workspace_root.name,
        "routes": routes,
        "symbols": symbols,
        "edges": edges,
        "database": _database_summary(file_refs),
        "summary": "",
        "omitted": omitted,
        "truncated": truncated or len(symbols) >= max_symbols or len(edges) >= max_edges,
        "redactions": len(omitted),
        "retention_class": "source_symbols",
        "raw_source_included": False,
    }
    graph["summary"] = _php_graph_summary(routes, symbols, edges)
    return graph


def _execute_populate_backend_ast(job: dict[str, Any], workspace_root: Path) -> dict[str, Any]:
    payload = job.get("payload") or {}
    max_files = int(payload.get("max_files") or 1_000)
    max_symbols = int(payload.get("max_symbols") or 5_000)
    max_file_bytes = int(payload.get("max_file_bytes") or 512_000)
    candidates, omitted, truncated = _iter_workspace_files(workspace_root, max_files=max_files)
    if any(path.suffix.lower() == ".php" for path in candidates):
        max_edges = int(payload.get("max_edges") or max_symbols * 2)
        graph = _build_php_graph(
            workspace_root,
            candidates,
            omitted,
            truncated=truncated,
            max_symbols=max_symbols,
            max_edges=max_edges,
            max_file_bytes=max_file_bytes,
        )
        return {
            "status": "completed",
            "summary": graph["summary"],
            "artifact": graph,
        }

    symbols: list[dict[str, Any]] = []
    for path in candidates:
        if path.suffix != ".py":
            continue
        rel = path.relative_to(workspace_root).as_posix()
        try:
            size = path.stat().st_size
        except OSError as exc:
            omitted.append({"path": rel, "reason": _io_error_reason("stat_error", exc)})
            continue
        if size > max_file_bytes:
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
        except OSError as exc:
            omitted.append({"path": rel, "reason": _io_error_reason("read_error", exc)})
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
    if capability == "read_source_slice":
        return _execute_read_source_slice(job, root)
    if capability == "sync_git_tree":
        return _execute_sync_git_tree(job, root)
    if capability == "populate_backend_ast":
        return _execute_populate_backend_ast(job, root)
    if capability == "project_inspection":
        return _execute_project_inspection(job, root)
    return {
        "status": "failed",
        "summary": f"Unsupported Hades backend job capability: {capability}",
        "omitted": [{"reason": "unsupported_capability"}],
    }
