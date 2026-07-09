"""Local execution for Hades backend requested read-only jobs."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tomllib
from typing import Any

from hermes_cli.hades_backend_client import redact_secret
from hermes_cli.hades_source_slice_policy import plan_source_slice_candidates


SKIP_DIRS = {
    ".cache",
    ".devboard",
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
SOURCE_DIR_PRIORITY = {
    "src": 0,
    "app": 1,
    "routes": 2,
    "config": 3,
    "database": 4,
    "migrations": 5,
    "tests": 6,
    "test": 7,
    "resources": 8,
    "templates": 9,
    "assets": 20,
    "public": 30,
    "docs": 40,
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


def _workspace_dir_sort_key(dirname: str) -> tuple[int, str]:
    return (SOURCE_DIR_PRIORITY.get(dirname, 10), dirname)

LANGUAGE_SUFFIXES = {
    ".css": "css",
    ".go": "go",
    ".js": "javascript",
    ".jsx": "javascript",
    ".md": "markdown",
    ".php": "php",
    ".prisma": "prisma",
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
ROUTE_RESOURCE_RE = re.compile(
    r"Route::(?P<kind>resource|apiResource)\s*"
    r"\(\s*['\"](?P<resource>[^'\"]+)['\"]\s*,\s*(?P<controller>\\?[A-Za-z0-9_\\]+)::class\s*\)",
    re.IGNORECASE | re.DOTALL,
)
LARAVEL_HANDLER_RE = re.compile(
    r"\[\s*(?P<class>[A-Za-z0-9_\\\\]+)::class\s*,\s*['\"](?P<method>[A-Za-z0-9_]+)['\"]\s*\]"
)
PHP_USE_RE = re.compile(
    r"^\s*use\s+(?P<class>[A-Za-z0-9_\\]+)(?:\s+as\s+(?P<alias>[A-Za-z_][A-Za-z0-9_]*))?\s*;",
    re.MULTILINE,
)
PHP_ROUTE_NAME_RE = re.compile(r"->name\(\s*['\"](?P<name>[^'\"]+)['\"]\s*\)")
PHP_ROUTE_MIDDLEWARE_RE = re.compile(r"->middleware\(\s*(?P<value>.*?)\s*\)", re.DOTALL)
PHP_QUOTED_VALUE_RE = re.compile(r"['\"](?P<value>[^'\"]+)['\"]")
PHP_TEST_METHOD_RE = re.compile(r"\bfunction\s+(?P<name>test[A-Za-z0-9_]*|it_[A-Za-z0-9_]+)\s*\(")
PY_TEST_FUNCTION_RE = re.compile(r"\b(?:async\s+)?def\s+(?P<name>test_[A-Za-z0-9_]+)\s*\(")
JS_TEST_CALL_RE = re.compile(r"\b(?:it|test)\s*\(")
PY_IMPORT_LINE_RE = re.compile(r"^\s*(?:from\s+(?P<from>[A-Za-z0-9_.]+)\s+import|import\s+(?P<import>[A-Za-z0-9_., ]+))", re.MULTILINE)
TS_IMPORT_RE = re.compile(r"\bimport(?:\s+type)?(?:\s+[^;]*?\s+from)?\s+['\"](?P<target>[^'\"]+)['\"]", re.MULTILINE)
TEST_FILE_SUFFIXES = {".php", ".py", ".js", ".jsx", ".ts", ".tsx"}
MAX_TEST_FILES = 500
MAX_TEST_CASES_PER_FILE = 50
MAX_TEST_REFS_PER_FILE = 25
MAX_LOG_EVENTS = 500


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


def _workspace_content_fingerprint(files: list[dict[str, Any]]) -> str:
    payload = [
        {
            "path": str(item.get("path") or ""),
            "bytes": int(item.get("bytes") or 0),
            "sha256": str(item.get("sha256") or ""),
        }
        for item in sorted(files, key=lambda file_item: str(file_item.get("path") or ""))
    ]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _git_status_porcelain(root: Path, *, max_entries: int = 200) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain=v1", "-z", "--untracked-files=all"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=2.0,
        )
    except Exception:
        return {"available": False, "dirty": False, "changed_count": 0, "changed_paths": [], "truncated": False}
    if completed.returncode != 0:
        return {"available": False, "dirty": False, "changed_count": 0, "changed_paths": [], "truncated": False}

    raw_entries = [entry for entry in completed.stdout.decode("utf-8", errors="replace").split("\0") if entry]
    changed_paths: list[str] = []
    counts = {"modified": 0, "deleted": 0, "renamed": 0, "untracked": 0, "other": 0}
    index = 0
    while index < len(raw_entries):
        entry = raw_entries[index]
        status = entry[:2]
        rel = entry[3:] if len(entry) > 3 else ""
        if status.startswith(("R", "C")) and index + 1 < len(raw_entries):
            index += 1
        index += 1
        clean_rel = _safe_relpath(rel)
        if not clean_rel:
            continue
        if _skip_file_reason(root / clean_rel, clean_rel) is not None:
            continue
        if status == "??":
            counts["untracked"] += 1
        elif "D" in status:
            counts["deleted"] += 1
        elif "R" in status or "C" in status:
            counts["renamed"] += 1
        elif "M" in status or "A" in status:
            counts["modified"] += 1
        else:
            counts["other"] += 1
        changed_paths.append(clean_rel)

    return {
        "available": True,
        "dirty": bool(changed_paths),
        "changed_count": len(changed_paths),
        "changed_paths": sorted(changed_paths)[:max_entries],
        "truncated": len(changed_paths) > max_entries,
        **counts,
    }


def _workspace_state_summary(workspace_root: Path, files: list[dict[str, Any]], payload: dict[str, Any]) -> dict[str, Any]:
    head_commit = str(payload.get("head_commit") or payload.get("workspace_head_commit") or "").strip()
    git_status = _git_status_porcelain(workspace_root)
    return {
        "schema": "hades.workspace_state.v1",
        "head_commit": head_commit or None,
        "workspace_head_commit": head_commit or None,
        "content_fingerprint": _workspace_content_fingerprint(files),
        "file_count": len(files),
        "git_status": git_status,
    }


def _is_test_path(rel: str) -> bool:
    path = _safe_relpath(rel)
    lowered = path.lower()
    suffix = Path(path).suffix.lower()
    if suffix not in TEST_FILE_SUFFIXES:
        return False
    parts = lowered.split("/")
    if any(part in {"tests", "test", "spec", "__tests__", "__specs__"} for part in parts):
        return True
    stem = Path(lowered).stem
    return (
        stem.startswith("test_")
        or stem.startswith("test-")
        or stem.endswith("_test")
        or stem.endswith("-test")
        or stem.endswith(".test")
        or stem.endswith(".spec")
        or stem.endswith("test")
        or stem.endswith("spec")
    )


def _test_framework_for_path(rel: str) -> str:
    path = _safe_relpath(rel)
    suffix = Path(path).suffix.lower()
    lowered = path.lower()
    if suffix == ".php":
        return "phpunit"
    if suffix == ".py":
        return "pytest"
    if "/cypress/" in lowered or lowered.startswith("cypress/"):
        return "cypress"
    if "/playwright/" in lowered or lowered.startswith("playwright/"):
        return "playwright"
    return "js_test"


def _test_cases_from_source(source: str, rel: str) -> list[dict[str, Any]]:
    suffix = Path(rel).suffix.lower()
    cases: list[dict[str, Any]] = []
    if suffix == ".php":
        pattern = PHP_TEST_METHOD_RE
    elif suffix == ".py":
        pattern = PY_TEST_FUNCTION_RE
    else:
        pattern = JS_TEST_CALL_RE
    for index, match in enumerate(pattern.finditer(source)):
        if len(cases) >= MAX_TEST_CASES_PER_FILE:
            break
        name = match.groupdict().get("name") or f"test@{_line_number(source, match.start())}"
        cases.append(
            {
                "name": str(name)[:120],
                "line": _line_number(source, match.start()),
                "ordinal": index + 1,
            }
        )
    return cases


def _test_import_refs(source: str, rel: str) -> list[dict[str, Any]]:
    suffix = Path(rel).suffix.lower()
    refs: list[dict[str, Any]] = []
    if suffix == ".php":
        for match in PHP_USE_RE.finditer(source):
            refs.append({"target": match.group("class").strip("\\"), "line": _line_number(source, match.start())})
    elif suffix == ".py":
        for match in PY_IMPORT_LINE_RE.finditer(source):
            raw = match.group("from") or match.group("import") or ""
            for target in raw.split(","):
                clean = target.strip().split(" ", 1)[0]
                if clean:
                    refs.append({"target": clean, "line": _line_number(source, match.start())})
    else:
        for match in TS_IMPORT_RE.finditer(source):
            refs.append({"target": match.group("target"), "line": _line_number(source, match.start())})
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ref in refs:
        target = str(ref.get("target") or "").strip()
        if not target or target in seen:
            continue
        seen.add(target)
        deduped.append(ref)
        if len(deduped) >= MAX_TEST_REFS_PER_FILE:
            break
    return deduped


def _test_target_candidates_from_path(rel: str) -> list[str]:
    path = _safe_relpath(rel)
    name = Path(path).name
    stem = name
    for suffix in (".test.tsx", ".test.ts", ".test.jsx", ".test.js", ".spec.tsx", ".spec.ts", ".spec.jsx", ".spec.js"):
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    if stem == name:
        stem = Path(path).stem
    candidates = {stem}
    for prefix in ("test_", "test-"):
        if stem.lower().startswith(prefix):
            candidates.add(stem[len(prefix) :])
    for suffix in ("Test", "Tests", "_test", "-test", ".test", ".spec", "Spec"):
        if stem.endswith(suffix):
            candidates.add(stem[: -len(suffix)])
    parent = Path(path).parent.name
    if parent and parent not in {"tests", "test", "spec", "__tests__", "__specs__"}:
        candidates.add(parent)
    return sorted(candidate for candidate in candidates if candidate)


def _match_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _symbol_refs_for_test(candidates: list[str], symbols: list[dict[str, Any]], *, test_path: str) -> list[str]:
    candidate_keys = {_match_key(candidate) for candidate in candidates if _match_key(candidate)}
    refs: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        name = str(symbol.get("name") or "").strip()
        if not name:
            continue
        if str(symbol.get("path") or "") == test_path:
            continue
        values = [
            name,
            symbol.get("short_name"),
            symbol.get("class"),
            symbol.get("method"),
            Path(str(symbol.get("path") or "")).stem,
        ]
        symbol_keys = {_match_key(value) for value in values if _match_key(value)}
        if not any(
            candidate == symbol_key
            or (len(candidate) >= 4 and candidate in symbol_key)
            for candidate in candidate_keys
            for symbol_key in symbol_keys
        ):
            continue
        if name in seen:
            continue
        seen.add(name)
        refs.append(name)
        if len(refs) >= MAX_TEST_REFS_PER_FILE:
            break
    return refs


def _route_ref(route: dict[str, Any]) -> str:
    name = str(route.get("name") or "").strip()
    if name:
        return f"route:{name}"
    route_path = str(route.get("uri") or route.get("path") or "").strip()
    method = str(route.get("method") or "").strip()
    return f"route:{method} {route_path}".strip()


def _route_refs_for_test(source: str, routes: list[dict[str, Any]]) -> list[str]:
    refs: list[str] = []
    seen: set[str] = set()
    for route in routes:
        route_path = str(route.get("uri") or route.get("path") or "").strip()
        ref = _route_ref(route)
        if not route_path or not ref or route_path not in source or ref in seen:
            continue
        seen.add(ref)
        refs.append(ref)
        if len(refs) >= MAX_TEST_REFS_PER_FILE:
            break
    return refs


def _build_test_map(
    workspace_root: Path,
    candidates: list[Path],
    routes: list[dict[str, Any]],
    symbols: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    max_edges: int,
    max_file_bytes: int,
) -> tuple[dict[str, Any], bool]:
    files: list[dict[str, Any]] = []
    truncated = False
    for path in candidates:
        rel = path.relative_to(workspace_root).as_posix()
        if not _is_test_path(rel):
            continue
        if len(files) >= MAX_TEST_FILES:
            truncated = True
            break
        try:
            if path.stat().st_size > max_file_bytes:
                truncated = True
                continue
            source, was_truncated, _digest = _read_text_bounded(path, max_file_bytes)
            if was_truncated:
                truncated = True
                continue
        except OSError:
            truncated = True
            continue

        cases = _test_cases_from_source(source, rel)
        import_refs = _test_import_refs(source, rel)
        target_candidates = _test_target_candidates_from_path(rel)
        symbol_refs = _symbol_refs_for_test(target_candidates, symbols, test_path=rel)
        route_refs = _route_refs_for_test(source, routes)
        test_node = f"test:{rel}"
        for ref in symbol_refs:
            truncated = not _edge_append(
                edges,
                {"kind": "test_covers_symbol", "from": test_node, "to": ref, "path": rel},
                max_edges=max_edges,
            ) or truncated
        for ref in route_refs:
            truncated = not _edge_append(
                edges,
                {"kind": "test_covers_route", "from": test_node, "to": ref, "path": rel},
                max_edges=max_edges,
            ) or truncated
        for ref in import_refs:
            truncated = not _edge_append(
                edges,
                {
                    "kind": "test_imports",
                    "from": test_node,
                    "to": str(ref.get("target") or ""),
                    "path": rel,
                    "line": ref.get("line"),
                },
                max_edges=max_edges,
            ) or truncated

        files.append(
            {
                "path": rel,
                "language": _language_for_path(rel),
                "framework": _test_framework_for_path(rel),
                "test_count": len(cases),
                "cases": cases,
                "target_candidates": target_candidates[:MAX_TEST_REFS_PER_FILE],
                "symbol_refs": symbol_refs,
                "route_refs": route_refs,
                "import_count": len(import_refs),
            }
        )
    return {
        "schema": "hades.test_map.v1",
        "file_count": len(files),
        "files": files,
        "truncated": truncated,
        "raw_source_included": False,
    }, truncated


def _normalize_laravel_handler(raw: str) -> str:
    compact = " ".join(str(raw or "").split())
    match = LARAVEL_HANDLER_RE.search(compact)
    if match:
        class_name = match.group("class").split("\\")[-1]
        return f"{class_name}@{match.group('method')}"
    return compact[:160]


def _laravel_resource_param(resource: str) -> str:
    tail = str(resource or "").strip("/").split("/")[-1].replace("-", "_")
    if tail.endswith("ies") and len(tail) > 3:
        return tail[:-3] + "y"
    if tail.endswith("s") and len(tail) > 1:
        return tail[:-1]
    return tail or "id"


def _laravel_resource_routes(
    *,
    resource: str,
    controller: str,
    api: bool,
    rel: str,
    line: int,
    chain: str,
) -> list[dict[str, Any]]:
    base_uri = "/" + str(resource or "").strip("/")
    route_name = base_uri.strip("/").replace("/", ".")
    param = _laravel_resource_param(resource)
    actions = [
        ("GET", base_uri, "index"),
        ("POST", base_uri, "store"),
        ("GET", f"{base_uri}/{{{param}}}", "show"),
        ("PUT", f"{base_uri}/{{{param}}}", "update"),
        ("PATCH", f"{base_uri}/{{{param}}}", "update"),
        ("DELETE", f"{base_uri}/{{{param}}}", "destroy"),
    ]
    if not api:
        actions.insert(2, ("GET", f"{base_uri}/create", "create"))
        actions.insert(4, ("GET", f"{base_uri}/{{{param}}}/edit", "edit"))
    middleware = _route_middleware_values(chain)
    routes: list[dict[str, Any]] = []
    controller_short = _php_short_name(controller)
    for method, uri, action in actions:
        route = {
            "framework": "laravel",
            "method": method,
            "uri": uri,
            "handler": f"{controller_short}@{action}",
            "path": rel,
            "line": line,
            "name": f"{route_name}.{action}",
            "resource": resource,
            "resource_action": action,
        }
        if middleware:
            route["middleware"] = middleware
        routes.append(route)
    return routes


def _line_number(content: str, offset: int) -> int:
    return content.count("\n", 0, max(0, offset)) + 1


def _php_short_name(name: str) -> str:
    return name.strip("\\").split("\\")[-1]


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
        for match in ROUTE_RESOURCE_RE.finditer(content):
            chain = _route_chain(content, match.end())
            resource_routes = _laravel_resource_routes(
                resource=match.group("resource"),
                controller=match.group("controller"),
                api=str(match.group("kind") or "").lower() == "apiresource",
                rel=rel,
                line=_line_number(content, match.start()),
                chain=chain,
            )
            for route in resource_routes:
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
    candidate_key = str(payload.get("candidate_key") or "").strip()
    if re.fullmatch(r"[a-fA-F0-9]{64}", candidate_key):
        source_slice["candidate_key"] = candidate_key.lower()
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
        for dirname in sorted(dirs, key=_workspace_dir_sort_key):
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
    workspace_state = _workspace_state_summary(workspace_root, files, payload)
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
            "workspace_state": workspace_state,
            "workspace_fingerprint": workspace_state["content_fingerprint"],
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


def _wiki_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return slug[:80] or "project"


def _wiki_cell(value: Any, *, max_chars: int = 120) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    text = text.replace("|", "\\|")
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _wiki_table(headers: list[str], rows: list[list[Any]], *, max_rows: int = 80) -> list[str]:
    if not rows:
        return []
    lines = [
        "| " + " | ".join(_wiki_cell(header, max_chars=80) for header in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows[:max_rows]:
        padded = list(row[: len(headers)]) + [""] * max(0, len(headers) - len(row))
        lines.append("| " + " | ".join(_wiki_cell(value) for value in padded) + " |")
    if len(rows) > max_rows:
        lines.append(f"\n... {len(rows) - max_rows} row(s) omitted from this bounded wiki page.")
    return lines


def _wiki_bounded(lines: list[str], *, max_chars: int = 24_000) -> str:
    text = "\n".join(lines).strip() + "\n"
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 120].rstrip() + "\n\n... [bounded wiki page truncated]\n"


def _artifact_evidence(artifact: dict[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(artifact, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return {
        "kind": "artifact_ref",
        "schema": artifact.get("schema"),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "raw_source_included": bool(artifact.get("raw_source_included")),
    }


def _file_evidence(files_by_path: dict[str, dict[str, Any]], path: Any) -> dict[str, Any] | None:
    rel = str(path or "").strip()
    if not rel:
        return None
    item = files_by_path.get(rel)
    evidence = {"kind": "file_ref", "path": rel}
    if isinstance(item, dict):
        if item.get("sha256"):
            evidence["hash"] = item.get("sha256")
        if item.get("bytes") is not None:
            evidence["bytes"] = item.get("bytes")
    return evidence


def _dedupe_evidence(refs: list[dict[str, Any] | None], *, limit: int = 80) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        key = json.dumps(ref, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(ref)
        if len(deduped) >= limit:
            break
    return deduped


def _wiki_page(
    *,
    root_slug: str,
    suffix: str,
    title: str,
    lines: list[str],
    evidence_refs: list[dict[str, Any] | None],
    page_type: str = "technical",
) -> dict[str, Any]:
    return {
        "slug": f"{root_slug}-{suffix}",
        "title": title,
        "page_type": page_type,
        "producer": "hades",
        "source_status": "verified_from_code",
        "content_markdown": _wiki_bounded(lines),
        "evidence_refs": _dedupe_evidence(evidence_refs),
    }


def _wiki_route_rows(routes: list[dict[str, Any]]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for route in routes:
        rows.append(
            [
                route.get("method") or "",
                route.get("uri") or route.get("path") or "",
                route.get("name") or "",
                route.get("handler") or "",
                route.get("source_path") or route.get("path") or "",
                route.get("line") or "",
            ]
        )
    return rows


def _wiki_symbol_rows(symbols: list[dict[str, Any]]) -> list[list[Any]]:
    role_rank = {
        "controller": 0,
        "model": 1,
        "service": 2,
        "command": 3,
        "livewire_component": 4,
        "class": 5,
        "function": 6,
        "method": 7,
    }

    def sort_key(symbol: dict[str, Any]) -> tuple[int, str, str]:
        role = str(symbol.get("role") or symbol.get("kind") or "")
        return (role_rank.get(role, 50), str(symbol.get("path") or ""), str(symbol.get("name") or ""))

    rows: list[list[Any]] = []
    for symbol in sorted(symbols, key=sort_key):
        rows.append(
            [
                symbol.get("name") or symbol.get("class") or "",
                symbol.get("role") or symbol.get("kind") or "",
                symbol.get("path") or "",
                symbol.get("line") or "",
            ]
        )
    return rows


def _wiki_table_rows(database: dict[str, Any]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for table in database.get("tables") or []:
        columns = table.get("columns") or []
        foreign_keys = table.get("foreign_keys") or []
        rows.append(
            [
                table.get("table") or "",
                table.get("model") or table.get("source") or table.get("action") or "",
                len(columns),
                len(foreign_keys),
                table.get("path") or "",
                table.get("line") or "",
            ]
        )
    return rows


def _wiki_test_rows(tests: dict[str, Any]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for item in tests.get("files") or []:
        cases = item.get("cases") or []
        refs = item.get("symbol_refs") or item.get("route_refs") or item.get("import_refs") or []
        rows.append([item.get("path") or "", item.get("framework") or "", len(cases), ", ".join(str(ref) for ref in refs[:6])])
    return rows


def _execute_populate_project_wiki(job: dict[str, Any], workspace_root: Path) -> dict[str, Any]:
    payload = job.get("payload") or {}
    max_files = int(payload.get("max_files") or 10_000)
    max_symbols = int(payload.get("max_symbols") or 5_000)
    max_file_bytes = int(payload.get("max_file_bytes") or 512_000)
    tree_result = _execute_sync_git_tree(
        {"capability": "sync_git_tree", "payload": {**payload, "max_files": max_files}},
        workspace_root,
    )
    graph_result = _execute_populate_backend_ast(
        {
            "capability": "populate_backend_ast",
            "payload": {
                **payload,
                "max_files": min(max_files, int(payload.get("max_ast_files") or 1_000)),
                "max_symbols": max_symbols,
                "max_file_bytes": max_file_bytes,
            },
        },
        workspace_root,
    )
    tree = tree_result.get("artifact") if isinstance(tree_result, dict) else {}
    graph = graph_result.get("artifact") if isinstance(graph_result, dict) else {}
    if not isinstance(tree, dict) or not isinstance(graph, dict):
        return {
            "status": "failed",
            "summary": "Unable to build local project wiki artifacts.",
            "schema": "devboard.wiki_refresh_result.v1",
            "pages": [],
        }

    root_name = str(graph.get("root") or tree.get("root") or workspace_root.name)
    root_slug = _wiki_slug(root_name)
    files = tree.get("files") if isinstance(tree.get("files"), list) else []
    files_by_path = {str(item.get("path") or ""): item for item in files if isinstance(item, dict)}
    project_index = tree.get("project_index") if isinstance(tree.get("project_index"), dict) else {}
    language_counts = project_index.get("language_counts") if isinstance(project_index.get("language_counts"), dict) else {}
    dependency_manifests = project_index.get("dependency_manifests")
    if not isinstance(dependency_manifests, list):
        dependency_manifests = graph.get("dependency_manifests") if isinstance(graph.get("dependency_manifests"), list) else []
    routes = graph.get("routes") if isinstance(graph.get("routes"), list) else project_index.get("routes") or []
    symbols = graph.get("symbols") if isinstance(graph.get("symbols"), list) else []
    edges = graph.get("edges") if isinstance(graph.get("edges"), list) else []
    database = graph.get("database") if isinstance(graph.get("database"), dict) else project_index.get("database") or {}
    tests = graph.get("tests") if isinstance(graph.get("tests"), dict) else {}
    logs = graph.get("logs") if isinstance(graph.get("logs"), dict) else {}

    artifact_refs = [_artifact_evidence(tree), _artifact_evidence(graph)]
    manifest_refs = [
        _file_evidence(files_by_path, manifest.get("path"))
        for manifest in dependency_manifests
        if isinstance(manifest, dict)
    ]
    overview_lines = [
        f"# {root_name} Project Overview",
        "",
        "Generated by Hades from bounded local project artifacts. Raw source is not embedded in this wiki page.",
        "",
        "## Artifact Summary",
        "",
        f"- Metadata tree: {tree.get('summary') or project_index.get('summary') or 'available'}",
        f"- Code graph: {graph.get('summary') or 'available'}",
        f"- Files indexed: {len(files)}",
        f"- Routes indexed: {len(routes)}",
        f"- Symbols indexed: {len(symbols)}",
        f"- Edges indexed: {len(edges)}",
        f"- Database tables/migrations indexed: {len(database.get('tables') or []) or int(database.get('migration_count') or 0)}",
        f"- Test files indexed: {int(tests.get('file_count') or len(tests.get('files') or []))}",
        f"- Log events indexed: {int(logs.get('event_count') or 0)}",
        "",
        "## Languages",
        "",
    ]
    language_rows = [
        [language, counts.get("files", 0), counts.get("bytes", 0)]
        for language, counts in sorted(language_counts.items())
        if isinstance(counts, dict)
    ]
    overview_lines.extend(_wiki_table(["Language", "Files", "Bytes"], language_rows, max_rows=30) or ["No language counts were available."])
    overview_lines.extend(["", "## Dependencies", ""])
    dependency_rows = [
        [manifest.get("manager"), manifest.get("path"), ", ".join(str(pkg) for pkg in (manifest.get("packages") or [])[:12])]
        for manifest in dependency_manifests
        if isinstance(manifest, dict)
    ]
    overview_lines.extend(_wiki_table(["Manager", "Manifest", "Packages"], dependency_rows, max_rows=30) or ["No dependency manifests were detected."])

    pages = [
        _wiki_page(
            root_slug=root_slug,
            suffix="overview",
            title=f"{root_name} Project Overview",
            lines=overview_lines,
            evidence_refs=[*artifact_refs, *manifest_refs],
        )
    ]

    if routes:
        route_refs = [
            _file_evidence(files_by_path, route.get("source_path") or route.get("path"))
            for route in routes
            if isinstance(route, dict)
        ]
        route_lines = [
            f"# {root_name} Entrypoints And Routes",
            "",
            "Routes and public entrypoints detected from framework metadata and static route declarations.",
            "",
        ]
        route_lines.extend(_wiki_table(["Method", "Path", "Name", "Handler", "Source", "Line"], _wiki_route_rows(routes), max_rows=120))
        pages.append(
            _wiki_page(
                root_slug=root_slug,
                suffix="entrypoints",
                title=f"{root_name} Entrypoints And Routes",
                lines=route_lines,
                evidence_refs=[*artifact_refs, *route_refs],
            )
        )

    table_rows = _wiki_table_rows(database)
    if table_rows:
        table_refs = [
            _file_evidence(files_by_path, table.get("path"))
            for table in database.get("tables") or []
            if isinstance(table, dict)
        ]
        data_lines = [
            f"# {root_name} Data Model",
            "",
            "Tables, models, migrations, and foreign-key signals detected by Hades.",
            "",
        ]
        data_lines.extend(_wiki_table(["Table", "Model/Source", "Columns", "Foreign Keys", "Source", "Line"], table_rows, max_rows=160))
        pages.append(
            _wiki_page(
                root_slug=root_slug,
                suffix="data-model",
                title=f"{root_name} Data Model",
                lines=data_lines,
                evidence_refs=[*artifact_refs, *table_refs],
            )
        )

    if symbols:
        symbol_refs = [
            _file_evidence(files_by_path, symbol.get("path"))
            for symbol in symbols
            if isinstance(symbol, dict)
        ]
        symbol_lines = [
            f"# {root_name} Symbol Map",
            "",
            "High-signal symbols detected in the local code graph. This is intended for source-free orientation and bug triage.",
            "",
        ]
        symbol_lines.extend(_wiki_table(["Symbol", "Role", "Source", "Line"], _wiki_symbol_rows(symbols), max_rows=180))
        pages.append(
            _wiki_page(
                root_slug=root_slug,
                suffix="symbol-map",
                title=f"{root_name} Symbol Map",
                lines=symbol_lines,
                evidence_refs=[*artifact_refs, *symbol_refs],
            )
        )

    test_rows = _wiki_test_rows(tests)
    if test_rows or int(logs.get("event_count") or 0):
        test_refs = [
            _file_evidence(files_by_path, item.get("path"))
            for item in tests.get("files") or []
            if isinstance(item, dict)
        ]
        quality_lines = [
            f"# {root_name} Tests And Runtime Signals",
            "",
            f"- Test files: {int(tests.get('file_count') or len(tests.get('files') or []))}",
            f"- Log events: {int(logs.get('event_count') or 0)}",
            "",
        ]
        quality_lines.extend(_wiki_table(["Test File", "Framework", "Cases", "Coverage Refs"], test_rows, max_rows=120) or ["No test files were indexed."])
        pages.append(
            _wiki_page(
                root_slug=root_slug,
                suffix="tests-quality",
                title=f"{root_name} Tests And Runtime Signals",
                lines=quality_lines,
                evidence_refs=[*artifact_refs, *test_refs],
            )
        )

    max_pages = max(1, int(payload.get("max_pages") or 8))
    pages = pages[:max_pages]
    return {
        "status": "completed",
        "schema": "devboard.wiki_refresh_result.v1",
        "summary": f"Generated {len(pages)} project wiki page(s) from local Hades artifacts for {root_name}.",
        "pages": pages,
        "source_artifacts": [
            {"schema": tree.get("schema"), "summary": tree.get("summary"), "raw_source_included": bool(tree.get("raw_source_included"))},
            {"schema": graph.get("schema"), "summary": graph.get("summary"), "raw_source_included": bool(graph.get("raw_source_included"))},
        ],
        "raw_source_included": False,
        "truncated": bool(tree.get("truncated")) or bool(graph.get("truncated")),
        "retention_class": "project_wiki",
    }


def _ts_graph_summary(
    routes: list[dict[str, str]],
    symbols: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    framework: str,
    database: dict[str, Any] | None = None,
    tests: dict[str, Any] | None = None,
    logs: dict[str, Any] | None = None,
) -> str:
    kind_counts: dict[str, int] = {}
    for symbol in symbols:
        kind = str(symbol.get("kind") or "symbol")
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
    kinds = ", ".join(f"{kind}:{count}" for kind, count in sorted(kind_counts.items())[:8])
    table_count = len((database or {}).get("tables") or [])
    test_count = int((tests or {}).get("file_count") or 0)
    log_count = int((logs or {}).get("event_count") or 0)
    return f"Code graph; framework:{framework}; routes:{len(routes)}; symbols:{len(symbols)}; edges:{len(edges)}; tables:{table_count}; tests:{test_count}; logs:{log_count}; {kinds or 'symbols:none'}"


def _join_url_path(prefix: str, path: str) -> str:
    if not prefix:
        return path or "/"
    if not path:
        return prefix
    return f"/{prefix.strip('/')}/{path.strip('/')}".replace("//", "/")


def _snake_name(name: str) -> str:
    return re.sub(r"(?<!^)([A-Z])", r"_\1", name).lower()


def _balanced_end(source: str, start: int, open_char: str, close_char: str) -> int:
    depth = 0
    quote = ""
    escape = False
    for index in range(start, len(source)):
        char = source[index]
        if quote:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == quote:
                quote = ""
            continue
        if char in {"'", '"', "`"}:
            quote = char
            continue
        if char == open_char:
            depth += 1
        elif char == close_char:
            depth -= 1
            if depth == 0:
                return index
    return -1


def _split_top_level_items(body: str) -> list[tuple[str, int]]:
    items: list[tuple[str, int]] = []
    start = 0
    parens = 0
    braces = 0
    brackets = 0
    quote = ""
    escape = False
    for index, char in enumerate(body):
        if quote:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == quote:
                quote = ""
            continue
        if char in {"'", '"', "`"}:
            quote = char
            continue
        if char == "(":
            parens += 1
        elif char == ")" and parens > 0:
            parens -= 1
        elif char == "{":
            braces += 1
        elif char == "}" and braces > 0:
            braces -= 1
        elif char == "[":
            brackets += 1
        elif char == "]" and brackets > 0:
            brackets -= 1
        elif char == "," and parens == 0 and braces == 0 and brackets == 0:
            raw = body[start:index]
            stripped = raw.strip()
            if stripped:
                items.append((stripped, start + len(raw) - len(raw.lstrip())))
            start = index + 1
    raw_tail = body[start:]
    tail = raw_tail.strip()
    if tail:
        items.append((tail, start + len(raw_tail) - len(raw_tail.lstrip())))
    return items


def _execute_populate_backend_ast(job: dict[str, Any], workspace_root: Path) -> dict[str, Any]:
    from hermes_cli.hades_index import build_graph_for_workspace

    payload = job.get("payload") or {}
    max_files = int(payload.get("max_files") or 1_000)
    candidates, omitted, truncated = _iter_workspace_files(workspace_root, max_files=max_files)

    # Dispatch to pluggable indexer (currently a seam over existing functions)
    artifact = build_graph_for_workspace(workspace_root, candidates, omitted, payload)

    return {
        "status": "completed",
        "summary": artifact.get("summary") or f"Collected {len(artifact.get('symbols') or [])} symbol(s).",
        "artifact": artifact,
    }


def _attach_source_slice_candidates(workspace_root: Path, artifact: dict[str, Any], payload: dict[str, Any]) -> None:
    head_commit = str(payload.get("head_commit") or payload.get("workspace_head_commit") or "")
    source_slice_candidates = plan_source_slice_candidates(
        workspace_root,
        artifact,
        head_commit=head_commit,
        max_candidates=int(payload.get("max_source_slice_candidates") or 200),
    )
    artifact["source_slice_candidates"] = source_slice_candidates
    summary = str(artifact.get("summary") or f"Collected {len(artifact.get('symbols') or [])} symbol(s).")
    artifact["summary"] = f"{summary}; source_slice_candidates:{len(source_slice_candidates)}"


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
    if capability == "populate_project_wiki":
        return _execute_populate_project_wiki(job, root)
    if capability == "project_inspection":
        return _execute_project_inspection(job, root)
    return {
        "status": "failed",
        "summary": f"Unsupported Hades backend job capability: {capability}",
        "omitted": [{"reason": "unsupported_capability"}],
    }
