from __future__ import annotations

from dataclasses import dataclass
import fnmatch
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
from typing import Any
import unicodedata


class SourceIdentityError(RuntimeError):
    """A source snapshot cannot safely identify the indexed workspace."""

    def __init__(self, code: str, safe_path: str | None = None):
        self.code = code
        self.safe_path = safe_path
        super().__init__(f"{code}:{safe_path}" if safe_path else code)


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    """Private inventory result used to populate the public v2 source identity."""

    tree_sha256: str
    head_commit: str | None
    dirty: bool
    branch: str | None
    excluded_count: int
    partial_reasons: tuple[str, ...]


# These are source-scope policy, not user-configurable defaults.  A user may
# add exclusions but can never opt a credential back into the graph snapshot.
_DEFAULT_EXCLUDED_DIRECTORY_NAMES = frozenset(
    {
        ".cache",
        ".git",
        ".hg",
        ".svn",
        ".next",
        ".nuxt",
        ".venv",
        "build",
        "coverage",
        "dist",
        "node_modules",
        "out",
        "target",
        "vendor",
        "venv",
        "__pycache__",
    }
)
_DEFAULT_EXCLUDED_DIRECTORY_SEQUENCES = (
    ("var", "cache"),
    ("storage", "framework", "cache"),
)
_COMPULSORY_SECRET_NAMES = frozenset(
    {
        ".netrc",
        ".npmrc",
        ".pypirc",
        "credentials",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "id_rsa",
    }
)
_COMPULSORY_SECRET_SUFFIXES = frozenset({".cert", ".crt", ".der", ".key", ".p12", ".pem", ".pfx"})
_INVALID_SYMLINK_PREFIX = b"SYMLINK_INVALID\0"
_UNAVAILABLE_SUBMODULE_PREFIX = b"SUBMODULE_UNAVAILABLE\0"


_ROUTE_FIELDS = (
    "framework",
    "method",
    "http_method",
    "verb",
    "uri",
    "route",
    "route_path",
    "name",
    "handler",
    "defined_handler",
    "inherited",
    "path",
    "source_path",
    "file",
    "line",
)
_TEST_FIELDS = (
    "framework",
    "name",
    "class",
    "class_name",
    "test_class",
    "path",
    "source_path",
    "file",
    "line",
    "cases",
    "target_candidates",
)


def _text(value: object) -> str:
    return str(value).strip() if value not in (None, "") else ""


def _kind(item: dict[str, Any]) -> str:
    return _text(item.get("kind") or item.get("type")).lower()


def _route_name(item: dict[str, Any]) -> str:
    name = _text(item.get("name"))
    if name.lower().startswith("route:"):
        return name.split(":", 1)[1]
    return name


def _route_identity(item: dict[str, Any]) -> tuple[str, ...] | None:
    name = _route_name(item)
    if name:
        return ("name", name)
    method = _text(
        item.get("method") or item.get("http_method") or item.get("verb")
    ).upper()
    path = _text(item.get("path"))
    uri = _text(
        item.get("uri")
        or item.get("route")
        or item.get("route_path")
        or (path if path.startswith("/") else "")
    )
    handler = _text(item.get("handler"))
    if not any((method, uri, handler)):
        return None
    return ("signature", method, uri, handler)


def _test_name(item: dict[str, Any]) -> str:
    for key in ("name", "test_class", "class_name", "class"):
        value = _text(item.get(key))
        if value:
            return value
    path = _text(item.get("path") or item.get("source_path") or item.get("file"))
    return PurePosixPath(path.replace("\\", "/")).stem if path else ""


def _normalized_test_path(item: dict[str, Any]) -> str:
    raw = _text(item.get("path") or item.get("source_path") or item.get("file"))
    if not raw:
        return ""
    parts: list[str] = []
    for part in PurePosixPath(raw.replace("\\", "/")).parts:
        if part in {"", ".", "/"}:
            continue
        if part == ".." and parts and parts[-1] != "..":
            parts.pop()
        else:
            parts.append(part)
    return "/".join(parts)


def _test_identity(item: dict[str, Any]) -> tuple[str, ...] | None:
    name = _test_name(item)
    if not name:
        return None
    path = _normalized_test_path(item)
    return ("path", path, name) if path else ("name", name)


_INVENTORY_KEYS = (
    "routes_detected",
    "routes_retained",
    "tests_detected",
    "tests_retained",
)


def _identity_tokens(values: object, *, kind: str) -> list[str]:
    if not isinstance(values, list):
        return []
    identity_fn = _route_identity if kind == "route" else _test_identity
    return sorted(
        {
            json.dumps(identity, separators=(",", ":"), ensure_ascii=False)
            for value in values
            if isinstance(value, dict)
            if (identity := identity_fn(value)) is not None
        }
    )


def inventory_coverage(
    *,
    routes_detected: object = None,
    routes_retained: object = None,
    tests_detected: object = None,
    tests_retained: object = None,
) -> dict[str, Any]:
    """Count inventory coverage by canonical identity, never by raw records."""

    identities = {
        "routes_detected": _identity_tokens(routes_detected, kind="route"),
        "routes_retained": _identity_tokens(routes_retained, kind="route"),
        "tests_detected": _identity_tokens(tests_detected, kind="test"),
        "tests_retained": _identity_tokens(tests_retained, kind="test"),
    }
    return {
        **{key: len(values) for key, values in identities.items()},
        "_identities": identities,
    }


def merge_inventory_coverage(
    *reports: object,
    dimensions: tuple[str, ...] = _INVENTORY_KEYS,
) -> dict[str, Any]:
    """Merge partial private reports without double-counting shared inventory."""

    keys = tuple(key for key in dimensions if key in _INVENTORY_KEYS)
    identities: dict[str, list[str]] = {}
    counts: dict[str, int] = {}
    for key in keys:
        identity_values = sorted(
            {
                value
                for report in reports
                if isinstance(report, dict)
                if isinstance(report.get("_identities"), dict)
                for value in (report["_identities"].get(key) or [])
                if isinstance(value, str)
            }
        )
        identities[key] = identity_values
        counts[key] = max(
            len(identity_values),
            max(
                (
                    int(report.get(key) or 0)
                    for report in reports
                    if isinstance(report, dict)
                ),
                default=0,
            ),
        )
    return {
        **counts,
        "_identities": identities,
    }


def _copy_fields(item: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {
        key: item[key]
        for key in fields
        if key in item and item[key] not in (None, "", [], {})
    }


def _merge_missing(
    existing: dict[str, Any],
    incoming: dict[str, Any],
) -> None:
    for key, value in incoming.items():
        if key not in existing or existing[key] in (None, "", [], {}):
            existing[key] = value


def _merge_test_fields(existing: dict[str, Any], incoming: dict[str, Any]) -> None:
    for key in ("cases", "target_candidates"):
        values = [
            *(
                existing.get(key)
                if isinstance(existing.get(key), list)
                else []
            ),
            *(
                incoming.get(key)
                if isinstance(incoming.get(key), list)
                else []
            ),
        ]
        if values:
            unique = {
                json.dumps(value, sort_keys=True, ensure_ascii=False, default=str): value
                for value in values
            }
            existing[key] = [unique[fingerprint] for fingerprint in sorted(unique)]
    _merge_missing(existing, incoming)


def _promote_routes(
    declarations: list[Any],
    routes: object,
) -> tuple[int, int, int]:
    records = [item for item in routes if isinstance(item, dict)] if isinstance(routes, list) else []
    existing_by_identity = {
        identity: declaration
        for declaration in declarations
        if isinstance(declaration, dict)
        if _kind(declaration) in {"route", "endpoint", "http_endpoint"}
        if (identity := _route_identity(declaration)) is not None
    }
    promoted = 0
    merged = 0
    for record in records:
        identity = _route_identity(record)
        if identity is None:
            continue
        route = {"kind": "route", **_copy_fields(record, _ROUTE_FIELDS)}
        normalized_name = _route_name(record)
        if normalized_name:
            route["name"] = normalized_name
        method = _text(
            record.get("method")
            or record.get("http_method")
            or record.get("verb")
        )
        if method:
            route["method"] = method.upper()
        path = _text(record.get("path"))
        uri = _text(
            record.get("uri")
            or record.get("route")
            or record.get("route_path")
            or (path if path.startswith("/") else "")
        )
        if uri:
            route["uri"] = uri
        existing = existing_by_identity.get(identity)
        if existing is None:
            declarations.append(route)
            existing_by_identity[identity] = route
            promoted += 1
            continue
        if normalized_name and _route_name(existing) == normalized_name:
            existing["name"] = normalized_name
        _merge_missing(existing, route)
        merged += 1
    return len(records), promoted, merged


def _promote_tests(
    declarations: list[Any],
    tests: object,
) -> tuple[int, int, int]:
    records = [item for item in tests if isinstance(item, dict)] if isinstance(tests, list) else []
    existing_by_identity = {
        identity: declaration
        for declaration in declarations
        if isinstance(declaration, dict)
        if _kind(declaration) in {"test", "test_case", "test_class"}
        if (identity := _test_identity(declaration)) is not None
    }
    promoted = 0
    merged = 0
    for record in records:
        name = _test_name(record)
        identity = _test_identity(record)
        if not name or identity is None:
            continue
        test = {
            "kind": "test",
            "name": name,
            **_copy_fields(record, _TEST_FIELDS),
        }
        test["name"] = name
        normalized_path = _normalized_test_path(record)
        if normalized_path:
            for key in ("path", "source_path", "file"):
                if key in test:
                    test[key] = normalized_path
                    break
        existing = existing_by_identity.get(identity)
        if existing is None:
            declarations.append(test)
            existing_by_identity[identity] = test
            promoted += 1
            continue
        _merge_test_fields(existing, test)
        merged += 1
    return len(records), promoted, merged


def promote_graph_inventories(graph: dict[str, Any]) -> dict[str, dict[str, int]]:
    """Promote uniform route and test inventories to first-class declarations.

    Language adapters remain responsible for extracting framework semantics.
    This shared boundary only consumes their uniform inventories, so PHP,
    Python, TypeScript, and future adapters receive identical canonical graph
    behavior.
    """

    declarations_key = "symbols" if isinstance(graph.get("symbols"), list) else "nodes"
    raw_declarations = graph.get(declarations_key)
    declarations = (
        [dict(item) if isinstance(item, dict) else item for item in raw_declarations]
        if isinstance(raw_declarations, list)
        else []
    )

    route_detected, route_promoted, route_merged = _promote_routes(
        declarations,
        graph.get("routes"),
    )
    tests = graph.get("tests")
    test_files = tests.get("files") if isinstance(tests, dict) else []
    test_detected, test_promoted, test_merged = _promote_tests(
        declarations,
        test_files,
    )
    # Keep adapter evidence byte-compatible. Canonicalization consumes this
    # private working collection and removes it before returning the artifact.
    graph["_canonical_declarations"] = declarations
    return {
        "route_inventory": {
            "detected": route_detected,
            "promoted": route_promoted,
            "merged": route_merged,
        },
        "test_inventory": {
            "detected": test_detected,
            "promoted": test_promoted,
            "merged": test_merged,
        },
    }


# ---------------------------------------------------------------------------
# Graph v2 source identity
# ---------------------------------------------------------------------------


def _safe_source_relative_path(raw: str) -> str:
    """Return the canonical safe path, without ever accepting an absolute one."""

    normalized = unicodedata.normalize("NFC", raw)
    if (
        not normalized
        or normalized.startswith("/")
        or "\\" in normalized
        or len(normalized.encode("utf-8")) > 4_096
    ):
        raise SourceIdentityError("source_path_invalid")
    parts = normalized.split("/")
    if any(
        not part
        or part in {".", ".."}
        or "\x00" in part
        or "\\" in part
        or any(ord(character) < 32 or ord(character) == 127 for character in part)
        for part in parts
    ):
        raise SourceIdentityError("source_path_invalid")
    # A Windows drive must not become a legal artifact source path on POSIX.
    if len(parts[0]) >= 2 and parts[0][1] == ":":
        raise SourceIdentityError("source_path_invalid")
    return normalized


def validate_normalized_source_paths(paths: list[str]) -> tuple[str, ...]:
    """Normalize a candidate inventory and fail on NFC identity collisions."""

    normalized_paths: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        normalized = _safe_source_relative_path(raw)
        if normalized in seen:
            # The normalized path is safe.  Do not surface the raw spelling:
            # one form can contain a credential-like filename.
            raise SourceIdentityError("source_path_normalization_collision", normalized)
        seen.add(normalized)
        normalized_paths.append(normalized)
    return tuple(normalized_paths)


def _is_compulsory_secret(path: str) -> bool:
    name = PurePosixPath(path).name.lower()
    if name == ".env":
        return True
    if name.startswith(".env.") and name != ".env.example":
        return True
    if name in _COMPULSORY_SECRET_NAMES or name.startswith("secret") or name.startswith("secrets."):
        return True
    return PurePosixPath(name).suffix.lower() in _COMPULSORY_SECRET_SUFFIXES


def _is_baseline_excluded(path: str) -> bool:
    parts = tuple(PurePosixPath(path).parts)
    if any(part in _DEFAULT_EXCLUDED_DIRECTORY_NAMES for part in parts[:-1]):
        return True
    if any(
        parts[index : index + len(sequence)] == sequence
        for sequence in _DEFAULT_EXCLUDED_DIRECTORY_SEQUENCES
        for index in range(0, len(parts) - len(sequence) + 1)
    ):
        return True
    return _is_compulsory_secret(path)


def _is_user_excluded(path: str, user_excluded_paths: tuple[str, ...]) -> bool:
    for pattern in user_excluded_paths:
        if path == pattern or path.startswith(pattern + "/"):
            return True
        if any(token in pattern for token in "*?[") and fnmatch.fnmatchcase(path, pattern):
            return True
    return False


def _stream_file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise SourceIdentityError("source_snapshot_unreadable") from exc
    return digest.hexdigest()


def _invalid_symlink_sha256(path: Path) -> str:
    try:
        target = os.fsencode(os.readlink(path))
    except OSError as exc:
        raise SourceIdentityError("source_snapshot_unreadable") from exc
    return hashlib.sha256(_INVALID_SYMLINK_PREFIX + target).hexdigest()


def _symlink_file_sha256(path: Path, root: Path) -> tuple[str, bool]:
    """Return ``(file_digest, is_invalid)`` without leaking the link target."""

    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
        if resolved.is_file():
            return _stream_file_sha256(resolved), False
    except (OSError, RuntimeError, ValueError):
        pass
    return _invalid_symlink_sha256(path), True


def _git_command(root: Path, *args: str) -> bytes | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *args],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return None
    return completed.stdout if completed.returncode == 0 else None


def _git_submodules(root: Path) -> dict[str, str]:
    output = _git_command(root, "ls-files", "--stage", "-z")
    if output is None:
        return {}
    result: dict[str, str] = {}
    for entry in output.split(b"\0"):
        if not entry or b"\t" not in entry:
            continue
        metadata, raw_path = entry.split(b"\t", 1)
        fields = metadata.split()
        if len(fields) != 3 or fields[0] != b"160000":
            continue
        try:
            path = _safe_source_relative_path(os.fsdecode(raw_path))
            commit = fields[1].decode("ascii")
        except (UnicodeError, SourceIdentityError):
            continue
        if len(commit) == 40 and all(character in "0123456789abcdef" for character in commit):
            result[path] = commit
    return result


def _submodule_is_available(path: Path) -> bool:
    if not path.is_dir():
        return False
    try:
        return any(child.name != ".git" for child in path.iterdir())
    except OSError:
        return False


def _git_metadata(root: Path, user_excluded_paths: tuple[str, ...]) -> tuple[str | None, bool, str | None]:
    inside = _git_command(root, "rev-parse", "--is-inside-work-tree")
    if inside is None or inside.strip() != b"true":
        return None, False, None

    raw_head = _git_command(root, "rev-parse", "HEAD")
    head = raw_head.decode("ascii", errors="ignore").strip() if raw_head else ""
    if len(head) != 40 or any(character not in "0123456789abcdef" for character in head):
        head = ""
    raw_branch = _git_command(root, "symbolic-ref", "--quiet", "--short", "HEAD")
    branch = raw_branch.decode("utf-8", errors="replace").strip() if raw_branch else None
    if branch is not None and (not branch or len(branch.encode("utf-8")) > 255):
        branch = None

    status = _git_command(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    dirty = False
    if status:
        rows = status.split(b"\0")
        index = 0
        while index < len(rows):
            row = rows[index]
            index += 1
            if len(row) < 4:
                continue
            try:
                path = _safe_source_relative_path(os.fsdecode(row[3:]))
            except (UnicodeError, SourceIdentityError):
                continue
            if not _is_baseline_excluded(path) and not _is_user_excluded(path, user_excluded_paths):
                dirty = True
                break
            # Rename/copy records contain one more NUL-delimited pathname.
            if row[:1] in {b"R", b"C"} or row[1:2] in {b"R", b"C"}:
                index += 1
    return head or None, dirty, branch


def build_source_snapshot(root: Path, *, user_excluded_paths: tuple[str, ...] = ()) -> SourceSnapshot:
    """Build the deterministic source inventory used by graph v2.

    File content is deliberately hashed before extraction budgets are applied:
    an oversized or binary source file still participates in the source
    identity even if a later adapter cannot parse it.
    """

    root = Path(root).resolve()
    if not root.is_dir():
        raise SourceIdentityError("source_root_unavailable")

    records: list[tuple[str, str]] = []
    raw_paths: list[str] = []
    excluded_count = 0
    partial_reasons: set[str] = set()
    submodules = _git_submodules(root)
    unavailable_submodules: set[str] = set()
    for path, commit in sorted(submodules.items()):
        candidate = root / path
        if not _submodule_is_available(candidate):
            unavailable_submodules.add(path)
            raw_paths.append(path)
            records.append(
                (
                    path,
                    hashlib.sha256(_UNAVAILABLE_SUBMODULE_PREFIX + commit.encode("ascii")).hexdigest(),
                )
            )
            partial_reasons.add("submodule_unavailable")

    for current, dirs, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        retained_dirs: list[str] = []
        for name in dirs:
            entry = current_path / name
            raw = entry.relative_to(root).as_posix()
            normalized = _safe_source_relative_path(raw)
            if _is_baseline_excluded(normalized) or _is_user_excluded(normalized, user_excluded_paths):
                excluded_count += 1
                continue
            if normalized in unavailable_submodules:
                continue
            if entry.is_symlink():
                raw_paths.append(raw)
                records.append((normalized, _invalid_symlink_sha256(entry)))
                partial_reasons.add("invalid_symlink")
                continue
            retained_dirs.append(name)
        dirs[:] = retained_dirs

        for name in files:
            entry = current_path / name
            raw = entry.relative_to(root).as_posix()
            normalized = _safe_source_relative_path(raw)
            if _is_baseline_excluded(normalized) or _is_user_excluded(normalized, user_excluded_paths):
                excluded_count += 1
                continue
            raw_paths.append(raw)
            if entry.is_symlink():
                digest, invalid = _symlink_file_sha256(entry, root)
                if invalid:
                    partial_reasons.add("invalid_symlink")
            elif entry.is_file():
                digest = _stream_file_sha256(entry)
            else:
                # A non-regular entry does not have a safe file-byte identity.
                continue
            records.append((normalized, digest))

    normalized_paths = validate_normalized_source_paths(raw_paths)
    normalized_by_raw = dict(zip(raw_paths, normalized_paths, strict=True))
    # The path used in each record is normalized above; re-create the pairs
    # from raw paths to make collisions a preimage-level failure, not a sort
    # accident.  Unavailable submodules have already supplied normalized paths.
    checked_records: list[tuple[str, str]] = []
    for path, digest in records:
        checked_records.append((normalized_by_raw.get(path, _safe_source_relative_path(path)), digest))
    checked_records.sort(key=lambda item: item[0])

    tree = hashlib.sha256()
    for path, file_digest in checked_records:
        tree.update(path.encode("utf-8"))
        tree.update(b"\0")
        tree.update(file_digest.encode("ascii"))
        tree.update(b"\n")
    head_commit, dirty, branch = _git_metadata(root, user_excluded_paths)
    return SourceSnapshot(
        tree_sha256=tree.hexdigest(),
        head_commit=head_commit,
        dirty=dirty,
        branch=branch,
        excluded_count=excluded_count,
        partial_reasons=tuple(sorted(partial_reasons)),
    )
