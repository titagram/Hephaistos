from __future__ import annotations

from dataclasses import dataclass
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


def _is_out_of_scope(path: str, user_excluded_paths: tuple[str, ...]) -> bool:
    # Import lazily to keep the config reader and inventory usable without an
    # import-time cycle.  The compiled policy itself has one source of truth in
    # hades_graph_config as required by the v2 source-identity contract.
    from hermes_cli.hades_graph_config import is_graph_source_excluded

    return is_graph_source_excluded(path, user_excluded_paths)


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


def _symlink_file_sha256(
    path: Path,
    root: Path,
    user_excluded_paths: tuple[str, ...],
) -> tuple[str | None, bool]:
    """Return ``(file_digest, is_invalid)`` without leaking the link target."""

    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
        target_path = _safe_source_relative_path(resolved.relative_to(root).as_posix())
        if _is_out_of_scope(target_path, user_excluded_paths):
            return None, False
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


@dataclass(frozen=True, slots=True)
class _GitHeadEntry:
    """A scoped historical entry with the repository that owns its blob."""

    mode: bytes
    kind: bytes
    object_id: bytes
    repository: Path


def _git_head_inventory(
    root: Path,
    user_excluded_paths: tuple[str, ...],
) -> tuple[tuple[tuple[str, str], ...] | None, bool]:
    """Return the scoped historical inventory and a pinned-submodule mismatch flag.

    A gitlink is a source-tree boundary, not its source identity.  If its
    checkout is present, both the live inventory and the historical inventory
    must recurse through the child at the exact commit pinned by the parent.
    If it is unavailable, both use the explicit unavailable marker instead.
    """

    entries: dict[str, _GitHeadEntry] = {}
    pinned_submodule_mismatch = False

    def scoped_path(prefix: str, relative_path: str) -> str:
        return (
            relative_path
            if not prefix
            else _safe_source_relative_path(f"{prefix}/{relative_path}")
        )

    def collect_tree(repository: Path, treeish: str, prefix: str) -> bool:
        nonlocal pinned_submodule_mismatch
        output = _git_command(repository, "ls-tree", "-r", "-z", treeish)
        if output is None:
            return False
        for item in output.split(b"\0"):
            if not item or b"\t" not in item:
                continue
            metadata, raw_path = item.split(b"\t", 1)
            fields = metadata.split()
            if len(fields) != 3:
                continue
            mode, kind, object_id = fields
            try:
                relative_path = _safe_source_relative_path(os.fsdecode(raw_path))
                path = scoped_path(prefix, relative_path)
            except (UnicodeError, SourceIdentityError):
                continue
            if _is_out_of_scope(path, user_excluded_paths):
                continue
            if mode == b"160000" and kind == b"commit":
                checkout = root / path
                if not _submodule_is_available(checkout):
                    entries[path] = _GitHeadEntry(mode, kind, object_id, repository)
                    continue
                checked_out_head = _git_command(checkout, "rev-parse", "HEAD")
                if checked_out_head is None or checked_out_head.strip() != object_id:
                    pinned_submodule_mismatch = True
                if not collect_tree(checkout, object_id.decode("ascii"), path):
                    # A visible checkout without the parent-pinned object is
                    # not equivalent to an unavailable gitlink.  Mark it
                    # dirty, while leaving no synthetic source record behind.
                    pinned_submodule_mismatch = True
                continue
            entries[path] = _GitHeadEntry(mode, kind, object_id, repository)
        return True

    if not collect_tree(root, "HEAD", ""):
        return None, False

    content_cache: dict[tuple[Path, bytes], bytes] = {}

    def blob_content(entry: _GitHeadEntry) -> bytes | None:
        cache_key = (entry.repository, entry.object_id)
        cached = content_cache.get(cache_key)
        if cached is not None:
            return cached
        content = _git_command(
            entry.repository,
            "cat-file",
            "blob",
            entry.object_id.decode("ascii"),
        )
        if content is not None:
            content_cache[cache_key] = content
        return content

    def relative_link_target(path: str, target: bytes) -> str | None:
        try:
            target_text = os.fsdecode(target)
        except UnicodeError:
            return None
        if target_text.startswith("/") or "\\" in target_text:
            return None
        parts = list(PurePosixPath(path).parent.parts)
        for part in PurePosixPath(target_text).parts:
            if part in {"", "."}:
                continue
            if part == "..":
                if not parts:
                    return None
                parts.pop()
                continue
            parts.append(part)
        try:
            return _safe_source_relative_path("/".join(parts))
        except (SourceIdentityError, UnicodeEncodeError):
            return None

    def resolve_entry(path: str, seen: frozenset[str] = frozenset()) -> str | None:
        entry = entries.get(path)
        if entry is None:
            return None
        if entry.mode == b"160000" and entry.kind == b"commit":
            return hashlib.sha256(_UNAVAILABLE_SUBMODULE_PREFIX + entry.object_id).hexdigest()
        if entry.kind != b"blob":
            return None
        content = blob_content(entry)
        if content is None:
            return None
        if entry.mode != b"120000":
            return hashlib.sha256(content).hexdigest()
        if path in seen:
            return hashlib.sha256(_INVALID_SYMLINK_PREFIX + content).hexdigest()
        target_path = relative_link_target(path, content)
        if target_path is None:
            return hashlib.sha256(_INVALID_SYMLINK_PREFIX + content).hexdigest()
        if _is_out_of_scope(target_path, user_excluded_paths):
            return None
        target_digest = resolve_entry(target_path, seen | {path})
        return target_digest if target_digest is not None else hashlib.sha256(
            _INVALID_SYMLINK_PREFIX + content
        ).hexdigest()

    records: list[tuple[str, str]] = []
    for path in sorted(entries):
        if _is_out_of_scope(path, user_excluded_paths):
            continue
        digest = resolve_entry(path)
        if digest is not None:
            records.append((path, digest))
    return tuple(sorted(records)), pinned_submodule_mismatch


def _git_metadata(
    root: Path,
    user_excluded_paths: tuple[str, ...],
    current_records: tuple[tuple[str, str], ...],
) -> tuple[str | None, bool, str | None]:
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

    head_records, pinned_submodule_mismatch = _git_head_inventory(
        root,
        user_excluded_paths,
    )
    dirty = (
        bool(current_records)
        if head_records is None
        else pinned_submodule_mismatch or current_records != head_records
    )
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
        if _is_out_of_scope(path, user_excluded_paths):
            continue
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
            if _is_out_of_scope(normalized, user_excluded_paths):
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
            if _is_out_of_scope(normalized, user_excluded_paths):
                excluded_count += 1
                continue
            raw_paths.append(raw)
            if entry.is_symlink():
                digest, invalid = _symlink_file_sha256(
                    entry,
                    root,
                    user_excluded_paths,
                )
                if digest is None:
                    excluded_count += 1
                    continue
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
    current_records = tuple(checked_records)
    head_commit, dirty, branch = _git_metadata(
        root,
        user_excluded_paths,
        current_records,
    )
    return SourceSnapshot(
        tree_sha256=tree.hexdigest(),
        head_commit=head_commit,
        dirty=dirty,
        branch=branch,
        excluded_count=excluded_count,
        partial_reasons=tuple(sorted(partial_reasons)),
    )
