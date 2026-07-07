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
PHP_USE_RE = re.compile(
    r"^\s*use\s+(?P<class>[A-Za-z0-9_\\]+)(?:\s+as\s+(?P<alias>[A-Za-z_][A-Za-z0-9_]*))?\s*;",
    re.MULTILINE,
)
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
PHP_ROUTE_NAME_RE = re.compile(r"->name\(\s*['\"](?P<name>[^'\"]+)['\"]\s*\)")
PHP_ROUTE_MIDDLEWARE_RE = re.compile(r"->middleware\(\s*(?P<value>.*?)\s*\)", re.DOTALL)
PHP_QUOTED_VALUE_RE = re.compile(r"['\"](?P<value>[^'\"]+)['\"]")
PHP_MODEL_TABLE_RE = re.compile(r"\bprotected\s+\$table\s*=\s*['\"](?P<table>[^'\"]+)['\"]", re.MULTILINE)
PHP_CONFIG_RE = re.compile(r"\bconfig\s*\(\s*['\"](?P<key>[^'\"]+)['\"]", re.MULTILINE)
PHP_ENV_RE = re.compile(r"\benv\s*\(\s*['\"](?P<key>[^'\"]+)['\"]", re.MULTILINE)
PHP_GATE_POLICY_RE = re.compile(
    r"\bGate::policy\s*\(\s*(?P<model>\\?[A-Za-z0-9_\\]+)::class\s*,\s*(?P<policy>\\?[A-Za-z0-9_\\]+)::class",
    re.MULTILINE,
)
PHP_SCHEMA_ACTION_RE = re.compile(
    r"\bSchema::(?P<action>create|table|drop|dropIfExists)\s*\(\s*['\"](?P<table>[^'\"]+)['\"]",
    re.IGNORECASE | re.MULTILINE,
)
PHP_TABLE_CALL_RE = re.compile(
    r"\$table->(?P<type>[A-Za-z_][A-Za-z0-9_]*)\s*\((?P<args>[^)]*)\)(?P<chain>(?:\s*->[A-Za-z_][A-Za-z0-9_]*\([^)]*\))*)",
    re.MULTILINE,
)
TS_IMPORT_RE = re.compile(r"\bimport(?:\s+type)?(?:\s+[^;]*?\s+from)?\s+['\"](?P<target>[^'\"]+)['\"]", re.MULTILINE)
TS_EXPORT_DECL_RE = re.compile(
    r"\bexport\s+(?:default\s+)?(?:(?:async\s+)?function|class|const|let|var)\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)",
    re.MULTILINE,
)
TS_FUNCTION_RE = re.compile(r"\b(?:async\s+)?function\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*\(", re.MULTILINE)
TS_ARROW_COMPONENT_RE = re.compile(
    r"\b(?:export\s+)?(?:const|let|var)\s+(?P<name>[A-Z][A-Za-z0-9_$]*)\s*=\s*(?:\([^)]*\)|[A-Za-z_$][A-Za-z0-9_$]*)\s*=>",
    re.MULTILINE,
)
TS_CLASS_RE = re.compile(r"\bclass\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)", re.MULTILINE)
EXPRESS_ROUTE_RE = re.compile(
    r"\b(?P<router>app|router)\s*\.\s*(?P<method>get|post|put|patch|delete|options|all|use)\s*"
    r"\(\s*['\"](?P<path>[^'\"]+)['\"]\s*,\s*(?P<handler>[A-Za-z_$][A-Za-z0-9_.$]*)?",
    re.IGNORECASE | re.MULTILINE,
)
NEXT_ROUTE_FILE_RE = re.compile(r"(?:^|/)app/(?P<route>.+)/route\.(?:ts|tsx|js|jsx)$")
NEXT_PAGE_FILE_RE = re.compile(r"(?:^|/)app/(?P<route>.+)/page\.(?:ts|tsx|js|jsx)$")
NEXT_HTTP_EXPORT_RE = re.compile(r"\bexport\s+(?:async\s+)?function\s+(?P<method>GET|POST|PUT|PATCH|DELETE|OPTIONS)\s*\(")


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


def _php_use_map(content: str) -> dict[str, str]:
    uses: dict[str, str] = {}
    for match in PHP_USE_RE.finditer(content):
        fqcn = match.group("class").strip("\\")
        alias = match.group("alias") or _php_short_name(fqcn)
        uses[alias] = fqcn
    return uses


def _php_fqcn(namespace: str, name: str) -> str:
    clean = name.strip("\\")
    if "\\" in clean or namespace == "":
        return clean
    return f"{namespace}\\{clean}"


def _php_fqcn_resolved(namespace: str, name: str, uses: dict[str, str]) -> str:
    clean = name.strip("\\")
    if "\\" in clean:
        return clean
    return uses.get(clean) or _php_fqcn(namespace, clean)


def _php_short_name(name: str) -> str:
    return name.strip("\\").split("\\")[-1]


def _php_context_id(class_info: dict[str, Any] | None, rel: str) -> str:
    return str(class_info["name"]) if class_info else rel


def _php_route_id(route: dict[str, Any]) -> str:
    return str(route.get("name") or f"{route.get('method', '')} {route.get('uri', '')}".strip())


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


def _route_chain(content: str, start: int) -> str:
    end = content.find(";", start)
    if end == -1:
        return content[start : start + 240]
    return content[start:end]


def _route_middleware_values(chain: str) -> list[str]:
    values: list[str] = []
    for match in PHP_ROUTE_MIDDLEWARE_RE.finditer(chain):
        raw = match.group("value")
        quoted = [item.group("value").strip() for item in PHP_QUOTED_VALUE_RE.finditer(raw)]
        if quoted:
            values.extend(quoted)
            continue
        clean = raw.strip().strip("'\"")
        if clean:
            values.append(clean)
    return sorted({value for value in values if value})


def _laravel_routes(root: Path, files: list[dict[str, Any]], *, max_routes: int = 500) -> list[dict[str, Any]]:
    routes: list[dict[str, Any]] = []
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
            chain = _route_chain(content, match.end())
            name = match.group("name")
            if not name:
                name_match = PHP_ROUTE_NAME_RE.search(chain)
                name = name_match.group("name") if name_match else None
            route = {
                "method": match.group("method").upper(),
                "uri": match.group("uri"),
                "handler": _normalize_laravel_handler(match.group("handler")),
                "path": rel,
                "line": _line_number(content, match.start()),
            }
            if name:
                route["name"] = name
            middleware = _route_middleware_values(chain)
            if middleware:
                route["middleware"] = middleware
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


def _first_quoted_arg(args: str) -> str:
    match = PHP_QUOTED_VALUE_RE.search(args)
    return match.group("value") if match else ""


def _migration_column_name(call_type: str, args: str) -> str:
    quoted = _first_quoted_arg(args)
    if quoted:
        return quoted
    if call_type == "id":
        return "id"
    if call_type == "timestamps":
        return "created_at,updated_at"
    if call_type == "softDeletes":
        return "deleted_at"
    return ""


def _foreign_table_from_column(column: str) -> str:
    if column.endswith("_id") and len(column) > 3:
        stem = column[:-3]
        return stem + "ies" if stem.endswith("y") else stem + "s"
    return ""


def _migration_columns(source: str, rel: str, table: str, start: int, end: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    columns: list[dict[str, Any]] = []
    indexes: list[dict[str, Any]] = []
    foreign_keys: list[dict[str, Any]] = []
    body = source[start:end]
    for match in PHP_TABLE_CALL_RE.finditer(body):
        call_type = match.group("type")
        args = match.group("args")
        chain = match.group("chain") or ""
        column = _migration_column_name(call_type, args)
        line = _line_number(source, start + match.start())
        if call_type in {"index", "unique", "primary", "foreign"}:
            target = column or _first_quoted_arg(args)
            if target:
                indexes.append({"table": table, "column": target, "kind": call_type, "path": rel, "line": line})
            continue
        if column:
            columns.append(
                {
                    "name": column,
                    "type": call_type,
                    "path": rel,
                    "line": line,
                    "nullable": "->nullable(" in chain,
                    "indexed": "->index(" in chain or "->unique(" in chain,
                }
            )
        if call_type == "foreignId" or "->constrained(" in chain:
            foreign_table = _first_quoted_arg(chain) or _foreign_table_from_column(column)
            if column and foreign_table:
                foreign_keys.append(
                    {
                        "table": table,
                        "column": column,
                        "references_table": foreign_table,
                        "path": rel,
                        "line": line,
                    }
                )
    return columns, indexes, foreign_keys


def _laravel_migration_tables(source: str, rel: str) -> list[dict[str, Any]]:
    matches = list(PHP_SCHEMA_ACTION_RE.finditer(source))
    tables: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        table = match.group("table")
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
        columns, indexes, foreign_keys = _migration_columns(source, rel, table, start, end)
        tables.append(
            {
                "table": table,
                "action": match.group("action"),
                "path": rel,
                "line": _line_number(source, match.start()),
                "columns": columns[:200],
                "indexes": indexes[:100],
                "foreign_keys": foreign_keys[:100],
            }
        )
    return tables


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


def _php_graph_summary(
    routes: list[dict[str, Any]],
    symbols: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    database: dict[str, Any] | None = None,
) -> str:
    role_counts: dict[str, int] = {}
    for symbol in symbols:
        role = str(symbol.get("role") or symbol.get("kind") or "symbol")
        role_counts[role] = role_counts.get(role, 0) + 1
    roles = ", ".join(f"{role}:{count}" for role, count in sorted(role_counts.items())[:8])
    table_count = len((database or {}).get("tables") or [])
    return f"PHP graph; routes:{len(routes)}; symbols:{len(symbols)}; edges:{len(edges)}; tables:{table_count}; {roles or 'roles:none'}"


def _php_route_edges(routes: list[dict[str, Any]], edges: list[dict[str, Any]], *, max_edges: int) -> bool:
    truncated = False
    for route in routes:
        route_id = _php_route_id(route)
        handler = route.get("handler", "")
        if "@" in handler:
            if not _edge_append(
                edges,
                {
                    "kind": "route_handler",
                    "from": f"route:{route_id}",
                    "to": handler,
                    "method": route.get("method"),
                    "uri": route.get("uri"),
                    "path": route.get("path"),
                    "line": route.get("line"),
                },
                max_edges=max_edges,
            ):
                truncated = True
                break
        for middleware in route.get("middleware") or []:
            if not _edge_append(
                edges,
                {
                    "kind": "route_middleware",
                    "from": f"route:{route_id}",
                    "to": f"middleware:{middleware}",
                    "method": route.get("method"),
                    "uri": route.get("uri"),
                    "path": route.get("path"),
                    "line": route.get("line"),
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
    database = _database_summary(file_refs)
    database["tables"] = []
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
        uses = _php_use_map(source)
        classes: list[dict[str, Any]] = []
        for match in PHP_CLASS_RE.finditer(source):
            class_name = match.group("name")
            extends = match.group("extends") or ""
            fqcn = _php_fqcn(namespace, class_name)
            extends_fqcn = _php_fqcn_resolved(namespace, extends, uses) if extends else None
            role = _php_role(rel, fqcn, extends)
            class_symbol = {
                "kind": match.group("kind"),
                "name": fqcn,
                "short_name": class_name,
                "role": role,
                "path": rel,
                "line": _line_number(source, match.start()),
                "extends": extends_fqcn,
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
                        "to": extends_fqcn,
                        "path": rel,
                        "line": _line_number(source, match.start()),
                    },
                    max_edges=max_edges,
                ) or truncated

        classes.sort(key=lambda item: int(item["offset"]))

        model_table = ""
        model_table_match = PHP_MODEL_TABLE_RE.search(source)
        if model_table_match:
            model_table = model_table_match.group("table")
        elif classes and classes[0].get("role") == "model":
            short = _php_short_name(str(classes[0]["name"]))
            model_table = re.sub(r"(?<!^)([A-Z])", r"_\1", short).lower() + "s"
        if model_table and classes:
            for class_info in classes:
                if class_info.get("role") != "model":
                    continue
                truncated = not _edge_append(
                    edges,
                    {
                        "kind": "model_table",
                        "from": class_info["name"],
                        "to": f"table:{model_table}",
                        "path": rel,
                        "line": _line_number(source, model_table_match.start()) if model_table_match else class_info.get("line"),
                    },
                    max_edges=max_edges,
                ) or truncated

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
                    "to": _php_fqcn_resolved(namespace, match.group("target"), uses),
                    "relation": match.group("relation"),
                    "path": rel,
                    "line": _line_number(source, match.start()),
                },
                max_edges=max_edges,
            ) or truncated

        for match in PHP_STATIC_CALL_RE.finditer(source):
            class_name = match.group("class")
            if _php_short_name(class_name) in {"self", "static", "parent", "Route", "Schema", "Gate"}:
                continue
            class_info = _class_context(classes, match.start())
            truncated = not _edge_append(
                edges,
                {
                    "kind": "static_call",
                    "from": class_info["name"] if class_info else rel,
                    "to": f"{_php_fqcn_resolved(namespace, class_name, uses)}::{match.group('method')}",
                    "path": rel,
                    "line": _line_number(source, match.start()),
                },
                max_edges=max_edges,
            ) or truncated

        for match in PHP_GATE_POLICY_RE.finditer(source):
            truncated = not _edge_append(
                edges,
                {
                    "kind": "policy_for",
                    "from": _php_fqcn(namespace, match.group("model")),
                    "to": _php_fqcn(namespace, match.group("policy")),
                    "path": rel,
                    "line": _line_number(source, match.start()),
                },
                max_edges=max_edges,
            ) or truncated

        for kind, pattern, prefix in (
            ("config_ref", PHP_CONFIG_RE, "config"),
            ("env_ref", PHP_ENV_RE, "env"),
        ):
            for match in pattern.finditer(source):
                class_info = _class_context(classes, match.start())
                truncated = not _edge_append(
                    edges,
                    {
                        "kind": kind,
                        "from": _php_context_id(class_info, rel),
                        "to": f"{prefix}:{match.group('key')}",
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
                    "to": _php_fqcn_resolved(namespace, match.group("class"), uses),
                    "path": rel,
                    "line": _line_number(source, match.start()),
                },
                max_edges=max_edges,
            ) or truncated

        if rel.startswith("database/migrations/"):
            for table_info in _laravel_migration_tables(source, rel):
                database["tables"].append(table_info)
                table_name = str(table_info["table"])
                if len(symbols) < max_symbols:
                    symbols.append(
                        {
                            "kind": "table",
                            "name": f"table:{table_name}",
                            "table": table_name,
                            "role": "database_table",
                            "path": rel,
                            "line": table_info.get("line"),
                        }
                    )
                else:
                    truncated = True
                truncated = not _edge_append(
                    edges,
                    {
                        "kind": "migration_table",
                        "from": rel,
                        "to": f"table:{table_name}",
                        "action": table_info.get("action"),
                        "path": rel,
                        "line": table_info.get("line"),
                    },
                    max_edges=max_edges,
                ) or truncated
                for foreign_key in table_info.get("foreign_keys") or []:
                    truncated = not _edge_append(
                        edges,
                        {
                            "kind": "foreign_key",
                            "from": f"table:{foreign_key['table']}.{foreign_key['column']}",
                            "to": f"table:{foreign_key['references_table']}",
                            "path": rel,
                            "line": foreign_key.get("line"),
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
        "database": database,
        "summary": "",
        "omitted": omitted,
        "truncated": truncated or len(symbols) >= max_symbols or len(edges) >= max_edges,
        "redactions": len(omitted),
        "retention_class": "source_symbols",
        "raw_source_included": False,
    }
    graph["summary"] = _php_graph_summary(routes, symbols, edges, database)
    return graph


def _ts_graph_summary(
    routes: list[dict[str, str]],
    symbols: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    framework: str,
) -> str:
    kind_counts: dict[str, int] = {}
    for symbol in symbols:
        kind = str(symbol.get("kind") or "symbol")
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
    kinds = ", ".join(f"{kind}:{count}" for kind, count in sorted(kind_counts.items())[:8])
    return f"Code graph; framework:{framework}; routes:{len(routes)}; symbols:{len(symbols)}; edges:{len(edges)}; {kinds or 'symbols:none'}"


def _ts_framework(root: Path, files: list[Path], dependency_manifests: list[dict[str, Any]]) -> str:
    packages = {
        str(package)
        for manifest in dependency_manifests
        for package in (manifest.get("packages") or [])
    }
    if "next" in packages or any(NEXT_ROUTE_FILE_RE.search(path.relative_to(root).as_posix()) for path in files):
        return "nextjs"
    if "react" in packages or any(path.suffix.lower() in {".tsx", ".jsx"} for path in files):
        return "react"
    if "express" in packages:
        return "express"
    return "node"


def _route_from_next_path(rel: str) -> str:
    for pattern in (NEXT_ROUTE_FILE_RE, NEXT_PAGE_FILE_RE):
        match = pattern.search(rel)
        if not match:
            continue
        route = match.group("route")
        clean = "/" + route.replace("/(group)", "").replace("index", "").strip("/")
        return clean if clean != "/" else "/"
    return ""


def _append_ts_symbol(
    symbols: list[dict[str, Any]],
    symbol: dict[str, Any],
    *,
    max_symbols: int,
) -> bool:
    if len(symbols) >= max_symbols:
        return False
    symbols.append({key: value for key, value in symbol.items() if value not in ("", None)})
    return True


def _build_ts_graph(
    workspace_root: Path,
    candidates: list[Path],
    omitted: list[dict[str, str]],
    *,
    truncated: bool,
    max_symbols: int,
    max_edges: int,
    max_file_bytes: int,
) -> dict[str, Any]:
    ts_files = [path for path in candidates if path.suffix.lower() in {".js", ".jsx", ".ts", ".tsx"}]
    file_refs = [{"path": path.relative_to(workspace_root).as_posix(), "bytes": path.stat().st_size} for path in candidates if path.is_file()]
    dependency_manifests = _dependency_manifests(workspace_root, file_refs)
    framework = _ts_framework(workspace_root, ts_files, dependency_manifests)
    routes: list[dict[str, str]] = []
    symbols: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    for path in ts_files:
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

        route_path = _route_from_next_path(rel)
        if route_path:
            for match in NEXT_HTTP_EXPORT_RE.finditer(source):
                routes.append(
                    {
                        "framework": "nextjs",
                        "method": match.group("method"),
                        "path": route_path,
                        "handler": f"{rel}:{match.group('method')}",
                        "source_path": rel,
                    }
                )
            if rel.endswith(("/page.tsx", "/page.jsx", "/page.ts", "/page.js")):
                routes.append(
                    {
                        "framework": "nextjs",
                        "method": "PAGE",
                        "path": route_path,
                        "handler": rel,
                        "source_path": rel,
                    }
                )

        for match in EXPRESS_ROUTE_RE.finditer(source):
            routes.append(
                {
                    "framework": "express",
                    "method": match.group("method").upper(),
                    "path": match.group("path"),
                    "handler": match.group("handler") or "",
                    "source_path": rel,
                }
            )

        for match in TS_IMPORT_RE.finditer(source):
            truncated = not _edge_append(
                edges,
                {
                    "kind": "imports",
                    "from": rel,
                    "to": match.group("target"),
                    "path": rel,
                    "line": _line_number(source, match.start()),
                },
                max_edges=max_edges,
            ) or truncated

        for kind, pattern in (
            ("export", TS_EXPORT_DECL_RE),
            ("function", TS_FUNCTION_RE),
            ("component", TS_ARROW_COMPONENT_RE),
            ("class", TS_CLASS_RE),
        ):
            for match in pattern.finditer(source):
                name = match.group("name")
                symbol_kind = "component" if kind == "component" or (path.suffix.lower() in {".tsx", ".jsx"} and name[:1].isupper()) else kind
                truncated = not _append_ts_symbol(
                    symbols,
                    {
                        "kind": symbol_kind,
                        "name": name,
                        "path": rel,
                        "line": _line_number(source, match.start()),
                        "framework": framework,
                    },
                    max_symbols=max_symbols,
                ) or truncated
                if len(symbols) >= max_symbols:
                    break
            if len(symbols) >= max_symbols:
                break

    graph = {
        "schema": "hades.code_graph.v1",
        "language": "typescript" if any(path.suffix.lower() in {".ts", ".tsx"} for path in ts_files) else "javascript",
        "framework": framework,
        "root": workspace_root.name,
        "routes": routes[:500],
        "symbols": symbols,
        "edges": edges,
        "dependency_manifests": dependency_manifests,
        "summary": "",
        "omitted": omitted,
        "truncated": truncated or len(symbols) >= max_symbols or len(edges) >= max_edges or len(routes) > 500,
        "redactions": len(omitted),
        "retention_class": "source_symbols",
        "raw_source_included": False,
    }
    graph["summary"] = _ts_graph_summary(graph["routes"], symbols, edges, framework=framework)
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
    if any(path.suffix.lower() in {".js", ".jsx", ".ts", ".tsx"} for path in candidates):
        max_edges = int(payload.get("max_edges") or max_symbols * 2)
        graph = _build_ts_graph(
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
