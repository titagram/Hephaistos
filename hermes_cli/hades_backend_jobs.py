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
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\((?P<params>[^)]*)\)",
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
PHP_TYPED_PARAM_RE = re.compile(r"(?P<class>\\?[A-Z][A-Za-z0-9_\\]+)\s+\$[A-Za-z_][A-Za-z0-9_]*")
PHP_VALIDATE_ARRAY_RE = re.compile(
    r"(?:\$[A-Za-z_][A-Za-z0-9_]*|request\s*\(\))->validate\s*\(\s*\[(?P<body>.*?)\]\s*\)",
    re.DOTALL,
)
PHP_ARRAY_FIELD_KEY_RE = re.compile(r"['\"](?P<field>[A-Za-z0-9_.*-]+)['\"]\s*=>")
PHP_LISTEN_ARRAY_RE = re.compile(
    r"(?P<event>\\?[A-Za-z0-9_\\]+)::class\s*=>\s*\[(?P<listeners>.*?)\]",
    re.DOTALL,
)
PHP_CLASS_CONST_RE = re.compile(r"(?P<class>\\?[A-Za-z0-9_\\]+)::class")
PHP_DISPATCH_JOB_RE = re.compile(r"\b(?P<class>\\?[A-Z][A-Za-z0-9_\\]+)::dispatch(?:Sync|AfterResponse)?\s*\(")
PHP_EVENT_FUNCTION_RE = re.compile(r"\bevent\s*\(\s*new\s+(?P<class>\\?[A-Z][A-Za-z0-9_\\]+)\s*\(")
PHP_EVENT_DISPATCH_RE = re.compile(
    r"\b(?:Event::dispatch|event)\s*\(\s*(?P<class>\\?[A-Z][A-Za-z0-9_\\]+)::class"
)
PHP_COMMAND_SIGNATURE_RE = re.compile(r"\bprotected\s+\$signature\s*=\s*['\"](?P<signature>[^'\"]+)['\"]")
PHP_SCHEDULE_COMMAND_RE = re.compile(
    r"\$schedule->command\s*\(\s*['\"](?P<command>[^'\"]+)['\"]\s*\)(?P<chain>(?:\s*->[A-Za-z_][A-Za-z0-9_]*\([^)]*\))*)",
    re.DOTALL,
)
PHP_SCHEDULE_JOB_RE = re.compile(
    r"\$schedule->job\s*\(\s*(?:new\s+)?(?P<class>\\?[A-Z][A-Za-z0-9_\\]+)(?:\([^)]*\))?\s*\)"
    r"(?P<chain>(?:\s*->[A-Za-z_][A-Za-z0-9_]*\([^)]*\))*)",
    re.DOTALL,
)
PHP_SCHEDULE_CADENCE_RE = re.compile(r"->(?P<cadence>[A-Za-z_][A-Za-z0-9_]*)\s*\(")
PHP_DB_TABLE_RE = re.compile(r"\bDB::table\s*\(\s*['\"](?P<table>[^'\"]+)['\"]")
PHP_QUERY_FROM_RE = re.compile(r"->from\s*\(\s*['\"](?P<table>[^'\"]+)['\"]")
PHP_QUERY_JOIN_RE = re.compile(r"->(?P<method>join|leftJoin|rightJoin|crossJoin)\s*\(\s*['\"](?P<table>[^'\"]+)['\"]")
PHP_CONTAINER_BIND_RE = re.compile(
    r"(?:\$this->app->|app\(\)->|App::)(?P<method>bind|singleton|scoped|instance)\s*"
    r"\(\s*(?P<abstract>\\?[A-Za-z0-9_\\]+)::class\s*,\s*"
    r"(?P<concrete>\\?[A-Za-z0-9_\\]+)::class",
    re.MULTILINE,
)
PHP_OBSERVER_RE = re.compile(
    r"(?P<model>\\?[A-Za-z0-9_\\]+)::observe\s*\(\s*(?P<observer>\\?[A-Za-z0-9_\\]+)::class\s*\)",
    re.MULTILINE,
)
PHP_VIEW_FUNCTION_RE = re.compile(r"\bview\s*\(\s*['\"](?P<view>[^'\"]+)['\"]", re.MULTILINE)
PHP_VIEW_MAKE_RE = re.compile(r"\bView::make\s*\(\s*['\"](?P<view>[^'\"]+)['\"]", re.MULTILINE)
PHP_INERTIA_RENDER_RE = re.compile(r"\bInertia::render\s*\(\s*['\"](?P<view>[^'\"]+)['\"]", re.MULTILINE)
PHP_BROADCAST_CHANNEL_RE = re.compile(
    r"\bBroadcast::channel\s*\(\s*['\"](?P<channel>[^'\"]+)['\"]\s*,\s*(?P<handler>.*?)\)",
    re.DOTALL,
)
PHP_SYMFONY_ROUTE_ATTRIBUTE_RE = re.compile(
    r"#\[\s*(?:[A-Za-z0-9_\\]+\\)?Route\s*\((?P<args>.*?)\)\s*\]",
    re.DOTALL,
)
PHP_SYMFONY_ROUTE_ANNOTATION_RE = re.compile(
    r"@\s*(?:[A-Za-z0-9_\\]+\\)?Route\s*\((?P<args>.*?)\)",
    re.DOTALL,
)
PHP_DOCBLOCK_RE = re.compile(r"/\*\*(?P<body>.*?)\*/", re.DOTALL)
PHP_NAMED_ROUTE_ARG_RE = re.compile(r"\b(?P<name>path|name)\s*[:=]\s*['\"](?P<value>[^'\"]+)['\"]")
PHP_ROUTE_METHODS_ARG_RE = re.compile(
    r"\bmethods\s*[:=]\s*(?P<value>\[[^\]]*\]|\{[^}]*\}|['\"][^'\"]+['\"])",
    re.DOTALL,
)
BLADE_EXTENDS_RE = re.compile(r"@extends\s*\(\s*['\"](?P<view>[^'\"]+)['\"]", re.MULTILINE)
BLADE_INCLUDE_RE = re.compile(
    r"@(?:include|includeIf|each)\s*\(\s*['\"](?P<view>[^'\"]+)['\"]",
    re.MULTILINE,
)
BLADE_CONDITIONAL_INCLUDE_RE = re.compile(
    r"@(?:includeWhen|includeUnless)\s*\(\s*[^,]+,\s*['\"](?P<view>[^'\"]+)['\"]",
    re.MULTILINE,
)
BLADE_COMPONENT_DIRECTIVE_RE = re.compile(r"@component\s*\(\s*['\"](?P<component>[^'\"]+)['\"]", re.MULTILINE)
BLADE_ANONYMOUS_COMPONENT_RE = re.compile(r"<x[-:](?P<component>[A-Za-z0-9_.:-]+)\b", re.MULTILINE)
BLADE_LIVEWIRE_RE = re.compile(
    r"(?:@livewire\s*\(\s*['\"](?P<directive>[^'\"]+)['\"]|<livewire:(?P<tag>[A-Za-z0-9_.:-]+)\b)",
    re.MULTILINE,
)
PHP_ELOQUENT_QUERY_METHODS = {
    "all",
    "count",
    "create",
    "doesntHave",
    "find",
    "first",
    "firstOrFail",
    "has",
    "query",
    "update",
    "where",
    "whereHas",
    "with",
}
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
PY_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "api_route", "route"}
PY_DJANGO_ROUTE_FUNCS = {"path", "re_path"}
PY_DJANGO_RELATION_FIELDS = {"ForeignKey", "OneToOneField", "ManyToManyField"}
PY_SQLALCHEMY_COLUMN_CALLS = {"Column", "mapped_column"}


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
    if path.startswith("app/Http/Requests/") or _php_short_name(extends) == "FormRequest" or short.endswith("Request"):
        return "form_request"
    if path.startswith("app/Models/") or _php_short_name(extends) == "Model":
        return "model"
    if path.startswith("app/Http/Middleware/") or short.endswith("Middleware"):
        return "middleware"
    if path.startswith("app/Jobs/") or short.endswith("Job"):
        return "job"
    if path.startswith("app/Events/") or short.endswith("Event"):
        return "event"
    if path.startswith("app/Listeners/") or short.endswith("Listener"):
        return "listener"
    if path.startswith("app/Console/Commands/") or _php_short_name(extends) == "Command" or short.endswith("Command"):
        return "artisan_command"
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


def _php_route_arg(args: str, name: str) -> str:
    for match in PHP_NAMED_ROUTE_ARG_RE.finditer(args or ""):
        if match.group("name") == name:
            return match.group("value")
    return ""


def _php_route_path_arg(args: str) -> str:
    path = _php_route_arg(args, "path")
    if path:
        return path
    stripped = str(args or "").lstrip()
    if stripped.startswith(("'", '"')):
        match = PHP_QUOTED_VALUE_RE.match(stripped)
        return match.group("value") if match else ""
    return ""


def _php_route_methods(args: str) -> list[str]:
    match = PHP_ROUTE_METHODS_ARG_RE.search(args or "")
    if not match:
        return []
    methods = [item.group("value").upper() for item in PHP_QUOTED_VALUE_RE.finditer(match.group("value"))]
    return sorted({method for method in methods if method})


def _php_route_metadata(args: str, *, line: int, source: str) -> dict[str, Any]:
    route = {
        "uri": _php_route_path_arg(args),
        "name": _php_route_arg(args, "name"),
        "methods": _php_route_methods(args),
        "line": line,
        "source": source,
    }
    return {key: value for key, value in route.items() if value not in ("", None, [])}


def _php_route_metadata_before(source: str, offset: int) -> list[dict[str, Any]]:
    start = max(0, offset - 2_000)
    segment = source[start:offset]
    routes: list[dict[str, Any]] = []
    for match in PHP_SYMFONY_ROUTE_ATTRIBUTE_RE.finditer(segment):
        tail = segment[match.end() :].strip()
        if tail and not tail.startswith("#["):
            continue
        routes.append(
            _php_route_metadata(
                match.group("args"),
                line=_line_number(source, start + match.start()),
                source="attribute",
            )
        )
    if routes:
        return routes

    docblocks = list(PHP_DOCBLOCK_RE.finditer(segment))
    if not docblocks:
        return []
    docblock = docblocks[-1]
    if segment[docblock.end() :].strip():
        return []
    body_start = start + docblock.start("body")
    return [
        _php_route_metadata(
            match.group("args"),
            line=_line_number(source, body_start + match.start()),
            source="annotation",
        )
        for match in PHP_SYMFONY_ROUTE_ANNOTATION_RE.finditer(docblock.group("body"))
    ]


def _php_combine_route_names(prefix: str, name: str) -> str:
    if prefix and name:
        return f"{prefix}{name}"
    return name or prefix


def _php_symfony_route(
    class_route: dict[str, Any],
    method_route: dict[str, Any],
    *,
    handler: str,
    controller: str,
    rel: str,
    fallback_line: int,
) -> dict[str, Any]:
    methods = method_route.get("methods") or class_route.get("methods") or ["ANY"]
    route = {
        "framework": "symfony",
        "method": "|".join(methods),
        "uri": _join_url_path(str(class_route.get("uri") or ""), str(method_route.get("uri") or "")),
        "handler": handler,
        "controller": controller,
        "path": rel,
        "line": int(method_route.get("line") or class_route.get("line") or fallback_line),
    }
    name = _php_combine_route_names(str(class_route.get("name") or ""), str(method_route.get("name") or ""))
    if name:
        route["name"] = name
    return route


def _php_array_field_keys(source: str, body: str, base_offset: int) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in PHP_ARRAY_FIELD_KEY_RE.finditer(body):
        field = match.group("field")
        if field in seen:
            continue
        seen.add(field)
        fields.append({"field": field, "line": _line_number(source, base_offset + match.start())})
    return fields


def _php_rules_method_body(source: str) -> tuple[str, int] | None:
    match = re.search(r"\bfunction\s+rules\s*\([^)]*\)", source)
    if not match:
        return None
    next_method = re.search(r"\bfunction\s+[A-Za-z_][A-Za-z0-9_]*\s*\(", source[match.end() :])
    end = match.end() + next_method.start() if next_method else len(source)
    return source[match.end() : end], match.end()


def _php_schedule_cadence(chain: str) -> str:
    ignored = {"name", "timezone", "withoutOverlapping", "onOneServer", "runInBackground", "evenInMaintenanceMode"}
    for match in PHP_SCHEDULE_CADENCE_RE.finditer(chain or ""):
        cadence = match.group("cadence")
        if cadence not in ignored:
            return cadence
    return ""


def _php_command_name(signature: str) -> str:
    return str(signature or "").split(maxsplit=1)[0].strip()


def _blade_view_name(path: str) -> str:
    prefix = "resources/views/"
    suffix = ".blade.php"
    if not path.startswith(prefix) or not path.endswith(suffix):
        return ""
    return path[len(prefix) : -len(suffix)].replace("/", ".")


def _blade_component_symbol(view_name: str) -> str:
    prefix = "components."
    if not view_name.startswith(prefix):
        return ""
    component = view_name[len(prefix) :].replace("::", ".").replace(":", ".")
    return f"component:{component}" if component else ""


def _blade_component_target(raw: str) -> str:
    component = (raw or "").strip().replace("::", ".").replace(":", ".")
    if component in {"dynamic-component", "slot"}:
        return ""
    if component.startswith("components."):
        component = component[len("components.") :]
    return f"component:{component}" if component else ""


def _append_blade_view_graph(
    source: str,
    rel: str,
    symbols: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    max_symbols: int,
    max_edges: int,
) -> bool:
    view_name = _blade_view_name(rel)
    if not view_name:
        return False

    truncated = False
    view_id = f"view:{view_name}"
    if len(symbols) < max_symbols:
        symbols.append(
            {
                "kind": "blade_view",
                "name": view_id,
                "view": view_name,
                "role": "blade_view",
                "path": rel,
                "line": 1,
            }
        )
    else:
        truncated = True

    component_symbol = _blade_component_symbol(view_name)
    if component_symbol:
        if len(symbols) < max_symbols:
            symbols.append(
                {
                    "kind": "blade_component",
                    "name": component_symbol,
                    "component": component_symbol.removeprefix("component:"),
                    "role": "blade_component",
                    "path": rel,
                    "line": 1,
                }
            )
        else:
            truncated = True

    seen_edges: set[tuple[str, str, int]] = set()

    def append_edge(kind: str, target: str, offset: int) -> None:
        nonlocal truncated
        if not target:
            return
        line = _line_number(source, offset)
        key = (kind, target, line)
        if key in seen_edges:
            return
        seen_edges.add(key)
        truncated = not _edge_append(
            edges,
            {
                "kind": kind,
                "from": view_id,
                "to": target,
                "path": rel,
                "line": line,
            },
            max_edges=max_edges,
        ) or truncated

    for match in BLADE_EXTENDS_RE.finditer(source):
        append_edge("blade_extends", f"view:{match.group('view')}", match.start())
    for match in BLADE_INCLUDE_RE.finditer(source):
        append_edge("blade_include", f"view:{match.group('view')}", match.start())
    for match in BLADE_CONDITIONAL_INCLUDE_RE.finditer(source):
        append_edge("blade_include", f"view:{match.group('view')}", match.start())
    for match in BLADE_COMPONENT_DIRECTIVE_RE.finditer(source):
        append_edge("blade_component", _blade_component_target(match.group("component")), match.start())
    for match in BLADE_ANONYMOUS_COMPONENT_RE.finditer(source):
        append_edge("blade_component", _blade_component_target(match.group("component")), match.start())
    for match in BLADE_LIVEWIRE_RE.finditer(source):
        livewire_name = match.group("directive") or match.group("tag") or ""
        append_edge("livewire_component", f"livewire:{livewire_name}", match.start())

    return truncated


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
                "framework": "laravel",
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

        truncated = _append_blade_view_graph(
            source,
            rel,
            symbols,
            edges,
            max_symbols=max_symbols,
            max_edges=max_edges,
        ) or truncated

        namespace = _php_namespace(source)
        uses = _php_use_map(source)
        classes: list[dict[str, Any]] = []
        symfony_class_routes: dict[str, list[dict[str, Any]]] = {}
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
            route_metadata = _php_route_metadata_before(source, match.start())
            if route_metadata:
                symfony_class_routes[fqcn] = route_metadata
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
            method_symbol = f"{_php_short_name(fqcn)}@{method_name}"
            if len(symbols) < max_symbols:
                symbols.append(
                    {
                        "kind": "method",
                        "name": method_symbol,
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
            for param_match in PHP_TYPED_PARAM_RE.finditer(match.group("params") or ""):
                param_class = _php_fqcn_resolved(namespace, param_match.group("class"), uses)
                param_short = _php_short_name(param_class)
                if param_short.endswith("Request"):
                    truncated = not _edge_append(
                        edges,
                        {
                            "kind": "uses_form_request",
                            "from": method_symbol,
                            "to": param_class,
                            "path": rel,
                            "line": _line_number(source, match.start()),
                        },
                        max_edges=max_edges,
                    ) or truncated
                else:
                    truncated = not _edge_append(
                        edges,
                        {
                            "kind": "uses_dependency",
                            "from": method_symbol,
                            "to": param_class,
                            "path": rel,
                            "line": _line_number(source, match.start()),
                        },
                        max_edges=max_edges,
                    ) or truncated

            method_routes = _php_route_metadata_before(source, match.start())
            if not method_routes and method_name == "__invoke":
                method_routes = [{}] if symfony_class_routes.get(fqcn) else []
            for class_route in symfony_class_routes.get(fqcn) or [{}]:
                for method_route in method_routes:
                    route = _php_symfony_route(
                        class_route,
                        method_route,
                        handler=method_symbol,
                        controller=fqcn,
                        rel=rel,
                        fallback_line=_line_number(source, match.start()),
                    )
                    routes.append(route)
                    truncated = not _edge_append(
                        edges,
                        {
                            "kind": "route_handler",
                            "from": f"route:{_php_route_id(route)}",
                            "to": method_symbol,
                            "framework": "symfony",
                            "method": route.get("method"),
                            "uri": route.get("uri"),
                            "path": rel,
                            "line": route.get("line"),
                        },
                        max_edges=max_edges,
                    ) or truncated

        rules_body = _php_rules_method_body(source)
        if rules_body is not None:
            body, base_offset = rules_body
            for class_info in classes:
                if class_info.get("role") != "form_request":
                    continue
                for field_info in _php_array_field_keys(source, body, base_offset):
                    truncated = not _edge_append(
                        edges,
                        {
                            "kind": "request_validation",
                            "from": class_info["name"],
                            "to": f"validation:{field_info['field']}",
                            "path": rel,
                            "line": field_info["line"],
                        },
                        max_edges=max_edges,
                    ) or truncated

        for match in PHP_VALIDATE_ARRAY_RE.finditer(source):
            class_info = _class_context(classes, match.start())
            for field_info in _php_array_field_keys(source, match.group("body"), match.start()):
                truncated = not _edge_append(
                    edges,
                    {
                        "kind": "request_validation",
                        "from": _php_context_id(class_info, rel),
                        "to": f"validation:{field_info['field']}",
                        "path": rel,
                        "line": field_info["line"],
                    },
                    max_edges=max_edges,
                ) or truncated

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
            method_name = match.group("method")
            if _php_short_name(class_name) in {
                "self",
                "static",
                "parent",
                "Route",
                "Schema",
                "Gate",
                "DB",
                "Broadcast",
                "View",
                "Inertia",
            }:
                continue
            if method_name == "observe":
                continue
            class_info = _class_context(classes, match.start())
            resolved_class = _php_fqcn_resolved(namespace, class_name, uses)
            truncated = not _edge_append(
                edges,
                {
                    "kind": "static_call",
                    "from": class_info["name"] if class_info else rel,
                    "to": f"{resolved_class}::{method_name}",
                    "path": rel,
                    "line": _line_number(source, match.start()),
                },
                max_edges=max_edges,
            ) or truncated
            if method_name in PHP_ELOQUENT_QUERY_METHODS and _php_short_name(resolved_class) != "DB":
                truncated = not _edge_append(
                    edges,
                    {
                        "kind": "eloquent_query",
                        "from": class_info["name"] if class_info else rel,
                        "to": f"{resolved_class}::{method_name}",
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

        for match in PHP_CONTAINER_BIND_RE.finditer(source):
            truncated = not _edge_append(
                edges,
                {
                    "kind": "container_binding",
                    "from": _php_fqcn_resolved(namespace, match.group("abstract"), uses),
                    "to": _php_fqcn_resolved(namespace, match.group("concrete"), uses),
                    "binding": match.group("method"),
                    "path": rel,
                    "line": _line_number(source, match.start()),
                },
                max_edges=max_edges,
            ) or truncated

        for match in PHP_OBSERVER_RE.finditer(source):
            truncated = not _edge_append(
                edges,
                {
                    "kind": "observed_by",
                    "from": _php_fqcn_resolved(namespace, match.group("model"), uses),
                    "to": _php_fqcn_resolved(namespace, match.group("observer"), uses),
                    "path": rel,
                    "line": _line_number(source, match.start()),
                },
                max_edges=max_edges,
            ) or truncated

        for match in PHP_LISTEN_ARRAY_RE.finditer(source):
            event_class = _php_fqcn_resolved(namespace, match.group("event"), uses)
            for listener_match in PHP_CLASS_CONST_RE.finditer(match.group("listeners")):
                truncated = not _edge_append(
                    edges,
                    {
                        "kind": "event_listener",
                        "from": event_class,
                        "to": _php_fqcn_resolved(namespace, listener_match.group("class"), uses),
                        "path": rel,
                        "line": _line_number(source, match.start()),
                    },
                    max_edges=max_edges,
                ) or truncated

        for match in PHP_DISPATCH_JOB_RE.finditer(source):
            class_info = _class_context(classes, match.start())
            truncated = not _edge_append(
                edges,
                {
                    "kind": "dispatches_job",
                    "from": _php_context_id(class_info, rel),
                    "to": _php_fqcn_resolved(namespace, match.group("class"), uses),
                    "path": rel,
                    "line": _line_number(source, match.start()),
                },
                max_edges=max_edges,
            ) or truncated

        for event_pattern in (PHP_EVENT_FUNCTION_RE, PHP_EVENT_DISPATCH_RE):
            for match in event_pattern.finditer(source):
                class_info = _class_context(classes, match.start())
                truncated = not _edge_append(
                    edges,
                    {
                        "kind": "emits_event",
                        "from": _php_context_id(class_info, rel),
                        "to": _php_fqcn_resolved(namespace, match.group("class"), uses),
                        "path": rel,
                        "line": _line_number(source, match.start()),
                    },
                    max_edges=max_edges,
                ) or truncated

        for match in PHP_COMMAND_SIGNATURE_RE.finditer(source):
            class_info = _class_context(classes, match.start())
            command_name = _php_command_name(match.group("signature"))
            if class_info is None or not command_name:
                continue
            truncated = not _edge_append(
                edges,
                {
                    "kind": "artisan_command",
                    "from": class_info["name"],
                    "to": f"command:{command_name}",
                    "path": rel,
                    "line": _line_number(source, match.start()),
                },
                max_edges=max_edges,
            ) or truncated

        for match in PHP_SCHEDULE_COMMAND_RE.finditer(source):
            class_info = _class_context(classes, match.start())
            command_name = _php_command_name(match.group("command"))
            truncated = not _edge_append(
                edges,
                {
                    "kind": "scheduled_command",
                    "from": _php_context_id(class_info, rel),
                    "to": f"command:{command_name}",
                    "cadence": _php_schedule_cadence(match.group("chain")),
                    "path": rel,
                    "line": _line_number(source, match.start()),
                },
                max_edges=max_edges,
            ) or truncated

        for match in PHP_SCHEDULE_JOB_RE.finditer(source):
            class_info = _class_context(classes, match.start())
            truncated = not _edge_append(
                edges,
                {
                    "kind": "scheduled_job",
                    "from": _php_context_id(class_info, rel),
                    "to": _php_fqcn_resolved(namespace, match.group("class"), uses),
                    "cadence": _php_schedule_cadence(match.group("chain")),
                    "path": rel,
                    "line": _line_number(source, match.start()),
                },
                max_edges=max_edges,
            ) or truncated

        for table_pattern in (PHP_DB_TABLE_RE, PHP_QUERY_FROM_RE, PHP_QUERY_JOIN_RE):
            for match in table_pattern.finditer(source):
                class_info = _class_context(classes, match.start())
                edge = {
                    "kind": "query_table",
                    "from": _php_context_id(class_info, rel),
                    "to": f"table:{match.group('table')}",
                    "path": rel,
                    "line": _line_number(source, match.start()),
                }
                if "method" in match.groupdict():
                    edge["query_method"] = match.group("method")
                truncated = not _edge_append(edges, edge, max_edges=max_edges) or truncated

        for kind, pattern, prefix in (
            ("view_ref", PHP_VIEW_FUNCTION_RE, "view"),
            ("view_ref", PHP_VIEW_MAKE_RE, "view"),
            ("inertia_view", PHP_INERTIA_RENDER_RE, "inertia"),
        ):
            for match in pattern.finditer(source):
                class_info = _class_context(classes, match.start())
                truncated = not _edge_append(
                    edges,
                    {
                        "kind": kind,
                        "from": _php_context_id(class_info, rel),
                        "to": f"{prefix}:{match.group('view')}",
                        "path": rel,
                        "line": _line_number(source, match.start()),
                    },
                    max_edges=max_edges,
                ) or truncated

        for match in PHP_BROADCAST_CHANNEL_RE.finditer(source):
            class_info = _class_context(classes, match.start())
            handler_match = PHP_CLASS_CONST_RE.search(match.group("handler") or "")
            edge = {
                "kind": "broadcast_channel",
                "from": _php_context_id(class_info, rel),
                "to": f"broadcast:{match.group('channel')}",
                "path": rel,
                "line": _line_number(source, match.start()),
            }
            if handler_match:
                edge["handler"] = _php_fqcn_resolved(namespace, handler_match.group("class"), uses)
            truncated = not _edge_append(edges, edge, max_edges=max_edges) or truncated

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

    route_frameworks = {str(route.get("framework")) for route in routes if route.get("framework")}
    has_laravel = "laravel" in route_frameworks or (workspace_root / "artisan").exists()
    has_symfony = "symfony" in route_frameworks or (workspace_root / "bin" / "console").exists()
    if has_laravel and has_symfony:
        framework = "php_web"
    elif has_laravel:
        framework = "laravel"
    elif has_symfony:
        framework = "symfony"
    else:
        framework = "php"

    graph = {
        "schema": "hades.php_graph.v1",
        "language": "php",
        "framework": framework,
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


def _py_graph_summary(
    routes: list[dict[str, str]],
    symbols: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    framework: str,
    database: dict[str, Any] | None = None,
) -> str:
    kind_counts: dict[str, int] = {}
    for symbol in symbols:
        kind = str(symbol.get("kind") or "symbol")
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
    kinds = ", ".join(f"{kind}:{count}" for kind, count in sorted(kind_counts.items())[:8])
    table_count = len((database or {}).get("tables") or [])
    return f"Code graph; framework:{framework}; routes:{len(routes)}; symbols:{len(symbols)}; edges:{len(edges)}; tables:{table_count}; {kinds or 'symbols:none'}"


def _py_dotted_name(node: ast.AST | None) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _py_dotted_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    if isinstance(node, ast.Call):
        return _py_dotted_name(node.func)
    return ""


def _py_string(node: ast.AST | None) -> str:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else ""


def _py_keyword_string(node: ast.Call, name: str) -> str:
    for keyword in node.keywords:
        if keyword.arg == name:
            return _py_string(keyword.value)
    return ""


def _py_keyword_bool(node: ast.Call, name: str) -> bool | None:
    for keyword in node.keywords:
        if keyword.arg == name and isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, bool):
            return keyword.value.value
    return None


def _py_keyword_int(node: ast.Call, name: str) -> int | None:
    for keyword in node.keywords:
        if keyword.arg == name and isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, int):
            return keyword.value.value
    return None


def _join_url_path(prefix: str, path: str) -> str:
    if not prefix:
        return path or "/"
    if not path:
        return prefix
    return f"/{prefix.strip('/')}/{path.strip('/')}".replace("//", "/")


def _py_route_id(route: dict[str, Any]) -> str:
    return str(route.get("name") or f"{route.get('method', '')} {route.get('path', '')}".strip())


def _snake_name(name: str) -> str:
    return re.sub(r"(?<!^)([A-Z])", r"_\1", name).lower()


def _py_app_label(rel: str) -> str:
    parts = rel.split("/")
    if "models" in parts:
        index = parts.index("models")
        if index > 0:
            return parts[index - 1]
    if rel.endswith("/models.py") and len(parts) >= 2:
        return parts[-2]
    return parts[0] if parts else "app"


def _py_django_model_base(node: ast.ClassDef) -> bool:
    for base in node.bases:
        base_name = _py_dotted_name(base)
        if base_name == "Model" or base_name.endswith(".Model"):
            return True
    return False


def _py_django_meta_table(node: ast.ClassDef) -> str:
    for item in node.body:
        if not isinstance(item, ast.ClassDef) or item.name != "Meta":
            continue
        for meta_item in item.body:
            if not isinstance(meta_item, ast.Assign):
                continue
            if not any(isinstance(target, ast.Name) and target.id == "db_table" for target in meta_item.targets):
                continue
            table = _py_string(meta_item.value)
            if table:
                return table
    return ""


def _py_django_relation_target(call: ast.Call) -> str:
    if not call.args:
        return ""
    target = call.args[0]
    if isinstance(target, ast.Constant) and isinstance(target.value, str):
        return target.value
    return _py_dotted_name(target)


def _py_django_target_table(target: str, app_label: str, current_table: str, model_tables: dict[str, str]) -> str:
    if not target:
        return ""
    if target == "self":
        return current_table
    clean = target.strip("'\"")
    model_name = clean.rsplit(".", 1)[-1]
    if model_name in model_tables:
        return model_tables[model_name]
    if "." in clean and not clean.startswith("settings."):
        app, model = clean.rsplit(".", 1)
        return f"{app}_{_snake_name(model)}"
    if clean.startswith("settings."):
        return f"setting:{clean}"
    return f"{app_label}_{_snake_name(clean.split('.')[-1])}"


def _py_django_model_table(node: ast.ClassDef, rel: str) -> tuple[str, str]:
    app_label = _py_app_label(rel)
    return _py_django_meta_table(node) or f"{app_label}_{_snake_name(node.name)}", app_label


def _py_django_model_fields(
    node: ast.ClassDef,
    table: str,
    app_label: str,
    rel: str,
    model_tables: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    columns: list[dict[str, Any]] = []
    foreign_keys: list[dict[str, Any]] = []
    for item in node.body:
        value: ast.AST | None = None
        field_name = ""
        if isinstance(item, ast.Assign) and len(item.targets) == 1 and isinstance(item.targets[0], ast.Name):
            field_name = item.targets[0].id
            value = item.value
        elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            field_name = item.target.id
            value = item.value
        if not field_name or not isinstance(value, ast.Call):
            continue
        field_type = _py_dotted_name(value.func).split(".")[-1]
        if not (field_type.endswith("Field") or field_type in PY_DJANGO_RELATION_FIELDS):
            continue
        relation = field_type in PY_DJANGO_RELATION_FIELDS
        column_name = f"{field_name}_id" if relation and field_type != "ManyToManyField" else field_name
        column = {
            "name": column_name,
            "field": field_name,
            "type": field_type,
            "path": rel,
            "line": getattr(item, "lineno", getattr(value, "lineno", 0)),
        }
        for keyword in ("null", "blank", "unique", "db_index", "primary_key"):
            keyword_value = _py_keyword_bool(value, keyword)
            if keyword_value is not None:
                column[keyword] = keyword_value
        max_length = _py_keyword_int(value, "max_length")
        if max_length is not None:
            column["max_length"] = max_length
        target = _py_django_relation_target(value) if relation else ""
        if target:
            column["relation_model"] = target
        columns.append(column)
        references_table = _py_django_target_table(target, app_label, table, model_tables)
        if references_table and field_type != "ManyToManyField":
            foreign_keys.append(
                {
                    "table": table,
                    "column": column_name,
                    "references_table": references_table,
                    "path": rel,
                    "line": column["line"],
                }
            )
    return columns, foreign_keys


def _py_assign_name(item: ast.AST) -> str:
    if isinstance(item, ast.Assign) and len(item.targets) == 1 and isinstance(item.targets[0], ast.Name):
        return item.targets[0].id
    if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
        return item.target.id
    return ""


def _py_assign_value(item: ast.AST) -> ast.AST | None:
    if isinstance(item, ast.Assign):
        return item.value
    if isinstance(item, ast.AnnAssign):
        return item.value
    return None


def _py_sqlalchemy_table_name(node: ast.ClassDef) -> str:
    for item in node.body:
        if not isinstance(item, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "__tablename__" for target in item.targets):
            continue
        table = _py_string(item.value)
        if table:
            return table
    return ""


def _py_sqlalchemy_column_type(arg: ast.AST | None) -> str:
    if arg is None:
        return ""
    if isinstance(arg, ast.Call):
        return _py_dotted_name(arg.func).split(".")[-1]
    return _py_dotted_name(arg).split(".")[-1]


def _py_sqlalchemy_foreign_key(call: ast.Call) -> tuple[str, str]:
    for arg in call.args:
        if not isinstance(arg, ast.Call) or _py_dotted_name(arg.func).split(".")[-1] != "ForeignKey" or not arg.args:
            continue
        target = _py_string(arg.args[0])
        if not target:
            continue
        if "." in target:
            table, column = target.split(".", 1)
            return table, column
        return target, ""
    return "", ""


def _py_sqlalchemy_column(field_name: str, value: ast.AST | None, rel: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not field_name or not isinstance(value, ast.Call):
        return None, None
    call_name = _py_dotted_name(value.func).split(".")[-1]
    if call_name not in PY_SQLALCHEMY_COLUMN_CALLS:
        return None, None

    args = list(value.args)
    column_name = field_name
    type_arg: ast.AST | None = None
    if args and isinstance(args[0], ast.Constant) and isinstance(args[0].value, str):
        column_name = args[0].value
        type_arg = args[1] if len(args) > 1 else None
    elif args:
        type_arg = args[0]

    column = {
        "name": column_name,
        "field": field_name,
        "type": _py_sqlalchemy_column_type(type_arg),
        "path": rel,
        "line": getattr(value, "lineno", 0),
    }
    for keyword in ("nullable", "unique", "index", "primary_key"):
        keyword_value = _py_keyword_bool(value, keyword)
        if keyword_value is not None:
            column[keyword] = keyword_value

    ref_table, ref_column = _py_sqlalchemy_foreign_key(value)
    foreign_key = None
    if ref_table:
        foreign_key = {
            "column": column_name,
            "references_table": ref_table,
            "references_column": ref_column,
            "path": rel,
            "line": column["line"],
        }
    return column, foreign_key


def _py_sqlalchemy_model_fields(node: ast.ClassDef, table: str, rel: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    columns: list[dict[str, Any]] = []
    foreign_keys: list[dict[str, Any]] = []
    for item in node.body:
        field_name = _py_assign_name(item)
        column, foreign_key = _py_sqlalchemy_column(field_name, _py_assign_value(item), rel)
        if column is None:
            continue
        columns.append(column)
        if foreign_key is not None:
            foreign_key["table"] = table
            foreign_keys.append(foreign_key)
    return columns, foreign_keys


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


def _build_python_artifact(
    workspace_root: Path,
    candidates: list[Path],
    omitted: list[dict[str, str]],
    *,
    truncated: bool,
    max_symbols: int,
    max_edges: int,
    max_file_bytes: int,
) -> dict[str, Any]:
    symbols: list[dict[str, Any]] = []
    routes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    database: dict[str, Any] = {"tables": []}
    frameworks: set[str] = set()
    python_files = [path for path in candidates if path.suffix == ".py"]

    for path in python_files:
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

        router_prefixes: dict[str, str] = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                continue
            if _py_dotted_name(node.value.func).split(".")[-1] != "APIRouter":
                continue
            prefix = _py_keyword_string(node.value, "prefix")
            for target in node.targets:
                if isinstance(target, ast.Name):
                    router_prefixes[target.id] = prefix

        django_model_tables: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and _py_django_model_base(node):
                table, _app_label = _py_django_model_table(node, rel)
                django_model_tables[node.name] = table

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                symbol = {"kind": "class", "name": node.name, "path": rel, "line": node.lineno}
                if _py_django_model_base(node):
                    table, app_label = _py_django_model_table(node, rel)
                    columns, foreign_keys = _py_django_model_fields(node, table, app_label, rel, django_model_tables)
                    if columns or foreign_keys:
                        symbol["role"] = "django_model"
                        database["tables"].append(
                            {
                                "table": table,
                                "model": node.name,
                                "app_label": app_label,
                                "path": rel,
                                "line": node.lineno,
                                "columns": columns[:200],
                                "foreign_keys": foreign_keys[:100],
                            }
                        )
                        frameworks.add("django")
                        truncated = not _edge_append(
                            edges,
                            {
                                "kind": "model_table",
                                "from": node.name,
                                "to": f"table:{table}",
                                "framework": "django",
                                "path": rel,
                                "line": node.lineno,
                            },
                            max_edges=max_edges,
                        ) or truncated
                        for foreign_key in foreign_keys:
                            truncated = not _edge_append(
                                edges,
                                {
                                    "kind": "foreign_key",
                                    "from": f"table:{foreign_key['table']}.{foreign_key['column']}",
                                    "to": f"table:{foreign_key['references_table']}",
                                    "framework": "django",
                                    "path": rel,
                                    "line": foreign_key.get("line"),
                                },
                                max_edges=max_edges,
                            ) or truncated
                else:
                    table = _py_sqlalchemy_table_name(node)
                    if table:
                        columns, foreign_keys = _py_sqlalchemy_model_fields(node, table, rel)
                        if columns or foreign_keys:
                            symbol["role"] = "sqlalchemy_model"
                            database["tables"].append(
                                {
                                    "table": table,
                                    "model": node.name,
                                    "orm": "sqlalchemy",
                                    "path": rel,
                                    "line": node.lineno,
                                    "columns": columns[:200],
                                    "foreign_keys": foreign_keys[:100],
                                }
                            )
                            frameworks.add("sqlalchemy")
                            truncated = not _edge_append(
                                edges,
                                {
                                    "kind": "model_table",
                                    "from": node.name,
                                    "to": f"table:{table}",
                                    "framework": "sqlalchemy",
                                    "path": rel,
                                    "line": node.lineno,
                                },
                                max_edges=max_edges,
                            ) or truncated
                            for foreign_key in foreign_keys:
                                truncated = not _edge_append(
                                    edges,
                                    {
                                        "kind": "foreign_key",
                                        "from": f"table:{foreign_key['table']}.{foreign_key['column']}",
                                        "to": f"table:{foreign_key['references_table']}",
                                        "framework": "sqlalchemy",
                                        "path": rel,
                                        "line": foreign_key.get("line"),
                                    },
                                    max_edges=max_edges,
                                ) or truncated
                if len(symbols) < max_symbols:
                    symbols.append(symbol)
                else:
                    truncated = True
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if len(symbols) < max_symbols:
                    symbols.append({"kind": "function", "name": node.name, "path": rel, "line": node.lineno})
                else:
                    truncated = True
                for decorator in node.decorator_list:
                    if not isinstance(decorator, ast.Call):
                        continue
                    decorator_name = _py_dotted_name(decorator.func)
                    decorator_parts = decorator_name.split(".")
                    method = decorator_parts[-1] if decorator_parts else ""
                    router_name = decorator_parts[-2] if len(decorator_parts) >= 2 else ""
                    if method not in PY_HTTP_METHODS or not decorator.args:
                        continue
                    route_path = _py_string(decorator.args[0])
                    if not route_path:
                        continue
                    route = {
                        "framework": "fastapi",
                        "method": "ANY" if method in {"api_route", "route"} else method.upper(),
                        "path": _join_url_path(router_prefixes.get(router_name, ""), route_path),
                        "handler": node.name,
                        "source_path": rel,
                        "line": getattr(decorator, "lineno", node.lineno),
                    }
                    route_name = _py_keyword_string(decorator, "name")
                    if route_name:
                        route["name"] = route_name
                    routes.append(route)
                    frameworks.add("fastapi")
                    truncated = not _edge_append(
                        edges,
                        {
                            "kind": "route_handler",
                            "from": f"route:{_py_route_id(route)}",
                            "to": node.name,
                            "framework": "fastapi",
                            "path": rel,
                            "line": getattr(decorator, "lineno", node.lineno),
                        },
                        max_edges=max_edges,
                    ) or truncated
            if len(symbols) >= max_symbols:
                truncated = True

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            call_name = _py_dotted_name(node.func).split(".")[-1]
            if call_name not in PY_DJANGO_ROUTE_FUNCS or len(node.args) < 2:
                continue
            route_path = _py_string(node.args[0])
            handler = _py_dotted_name(node.args[1])
            if not route_path or not handler:
                continue
            route = {
                "framework": "django",
                "method": "ROUTE",
                "path": route_path,
                "handler": handler,
                "source_path": rel,
                "line": getattr(node, "lineno", 0),
            }
            route_name = _py_keyword_string(node, "name")
            if route_name:
                route["name"] = route_name
            routes.append(route)
            frameworks.add("django")
            truncated = not _edge_append(
                edges,
                {
                    "kind": "route_handler",
                    "from": f"route:{_py_route_id(route)}",
                    "to": handler,
                    "framework": "django",
                    "path": rel,
                    "line": getattr(node, "lineno", 0),
                },
                max_edges=max_edges,
            ) or truncated

        if len(symbols) >= max_symbols:
            break

    if routes or database["tables"]:
        framework = "python_web" if len(frameworks) > 1 else next(iter(frameworks), "python")
        graph_database = {**database, "tables": database["tables"][:500]}
        graph = {
            "schema": "hades.code_graph.v1",
            "language": "python",
            "framework": framework,
            "root": workspace_root.name,
            "routes": routes[:500],
            "symbols": symbols,
            "edges": edges,
            "database": graph_database,
            "summary": "",
            "omitted": omitted,
            "truncated": truncated
            or len(symbols) >= max_symbols
            or len(edges) >= max_edges
            or len(routes) > 500
            or len(database["tables"]) > 500,
            "redactions": len(omitted),
            "retention_class": "source_symbols",
            "raw_source_included": False,
        }
        graph["summary"] = _py_graph_summary(graph["routes"], symbols, edges, framework=framework, database=graph_database)
        return graph

    return {
        "schema": "hades.symbols.v1",
        "symbols": symbols,
        "omitted": omitted,
        "truncated": truncated or len(symbols) >= max_symbols,
        "redactions": len(omitted),
        "retention_class": "source_symbols",
        "raw_source_included": False,
    }


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

    max_edges = int(payload.get("max_edges") or max_symbols * 2)
    artifact = _build_python_artifact(
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
        "summary": artifact.get("summary") or f"Collected {len(artifact.get('symbols') or [])} symbol(s).",
        "artifact": artifact,
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
