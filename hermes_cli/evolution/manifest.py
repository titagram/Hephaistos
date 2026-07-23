"""Validation and identity projection for immutable evolution generations."""

from __future__ import annotations

import hashlib
import math
import os
import re
import stat
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from .contract import EvolutionContractError, content_digest, require_digest, require_relative_posix_path
from .authorization import _looks_like_credential_material


_EXCLUDED = frozenset({"generation_id", "created_at", "attestations"})
_TOP = frozenset({"schema_version", "generation_id", "parent_generation_id", "source_suggestion_id", "blueprint_digest", "stable_base", "compatibility_range", "components", "dependency_constraints", "resolved_versions", "credential_references", "service_prerequisites", "capabilities", "invariants", "verification_commands", "canary_policy", "resource_ceilings", "expected_organism_diff", "build_environment", "builder_version", "rollback_plan", "incompatibility_reasons", "created_at", "attestations"})
_REQUIRED = _TOP - {"generation_id", "attestations"}
_CLASSES = frozenset({"skill", "script", "plugin", "mcp"})
_COMPONENT_FIELDS = frozenset(
    {
        "class",
        "logical_id",
        "path",
        "digest",
        "source",
        "author",
        "license",
        "provenance",
        "capabilities",
        "lockfiles",
    }
)
_LOCKFILE_FIELDS = frozenset({"path", "digest"})
_SYMBOL = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z", re.ASCII)
_PACKAGE = re.compile(r"[a-z0-9][a-z0-9._-]*(?:/[a-z0-9][a-z0-9._-]*)?(?:@[a-z0-9._+-]+)?\Z", re.ASCII)
_SECRET = re.compile(r"(?:sk|pk)[_-](?:live|test|proj)[_-]|ghp[_-]|github[_-]pat[_-]|glpat[_-]|xox[bp][_-]|akia", re.I)
_HTTPS_URL = re.compile(r"https://[^\s\"'`]+", re.I)
_WINDOWS_PATH = re.compile(
    r"(?<![A-Za-z0-9_])[A-Za-z]:[\\/][^\s\"'`]+|(?<![A-Za-z0-9_])\\\\[^\s\"'`]+"
)
_POSIX_PATH = re.compile(r"(?<![:/])/(?:[^/\s\"'`]+/)*[^/\s\"'`]+")
_EXPLICIT_RELATIVE_PATH = re.compile(
    r"(?:^|[\s\"'`])(?:~|\.{1,2})[/\\][^\s\"'`]+"
)
_BARE_RELATIVE_PATH = re.compile(
    r"[^\s/\\\"'`]+(?:[/\\][^\s/\\\"'`]+)+\Z"
)
_LOCAL_ROOT_PATH = re.compile(
    r"(?<![A-Za-z0-9_./\\:])"
    r"(?:\.{1,2}|~|/{1,2}|[A-Za-z]:[\\/])"
    r"(?=$|[\s\"'`,;)\]])"
)
_PACKAGE_COORDINATE = re.compile(
    r"(?<![A-Za-z0-9._-])[a-z0-9][a-z0-9._-]*/"
    r"[a-z0-9][a-z0-9._-]*@[a-z0-9._+-]+"
    r"(?![A-Za-z0-9._+-])",
    re.ASCII,
)
_RESOLVED_COORDINATE = re.compile(
    r"[A-Za-z][A-Za-z0-9._-]{0,63}"
    r"(?:/[A-Za-z0-9][A-Za-z0-9._-]{0,63})?\Z",
    re.ASCII,
)
_SEMANTIC_VERSION = re.compile(
    r"(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*)){1,3}"
    r"(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?"
    r"(?:\+[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?\Z",
    re.ASCII,
)
_ENVIRONMENT_IDENTITY = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._+:-]{0,127}\Z",
    re.ASCII,
)
_COMMAND_CANDIDATE = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._+-]*",
    re.ASCII,
)
_CANARY_POLICY_FIELDS = frozenset({"side_effects"})
_RESOURCE_CEILING_LIMITS = {
    "cpu_seconds": 86_400,
    "wall_seconds": 604_800,
    "memory_bytes": 1 << 50,
    "disk_bytes": 1 << 50,
    "network_requests": 1_000_000,
    "process_count": 4_096,
}
_BUILD_ENVIRONMENT_TEXT_FIELDS = frozenset(
    {"builder", "version", "platform", "architecture", "python"}
)
_BUILD_ENVIRONMENT_DIGEST_FIELDS = frozenset(
    {"environment_digest", "toolchain_digest"}
)
_BUILD_ENVIRONMENT_REQUIRED_FIELDS = frozenset({"builder", "version"})
_MAX_ITEMS = 64
_MAX_TEXT = 512
_MAX_KEY = 64
_MAX_DEPTH = 8
_MAX_NODES = 512
_MAX_TOTAL_TEXT = 16 * 1024
_MAX_INTEGER = (1 << 63) - 1
_SLOT_CHILDREN = {
    "manifest": {
        "generation_id": "digest",
        "parent_generation_id": "digest",
        "blueprint_digest": "digest",
        "stable_base": "stable_base",
        "components": "components",
        "resolved_versions": "resolved_versions",
        "verification_commands": "verification_commands",
        "build_environment": "build_environment",
    },
    "stable_base": {
        "repository_commit": "repository_commit",
        "configuration_fingerprint": "digest",
    },
    "component": {
        "path": "declared_path",
        "digest": "digest",
        "source": "component_source",
        "lockfiles": "lockfiles",
    },
    "lockfile": {
        "path": "declared_path",
        "digest": "digest",
    },
    "build_environment": {
        "environment_digest": "digest",
        "toolchain_digest": "digest",
    },
}
_SLOT_ITEMS = {
    "components": "component",
    "lockfiles": "lockfile",
    "verification_commands": "command",
}
_DYNAMIC_MAPPING_VALUES = {
    "resolved_versions": "resolved_version",
}


def _fail(code: str = "invalid_manifest") -> None:
    raise EvolutionContractError(code)


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or len(value) > _MAX_ITEMS or any(not isinstance(k, str) for k in value):
        _fail()
    return value


def _text(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > _MAX_TEXT or "\0" in value:
        _fail()
    return value


def _strings(value: object, *, symbolic: bool = False) -> list[str]:
    if not isinstance(value, list) or len(value) > _MAX_ITEMS:
        _fail()
    values = [_text(item) for item in value]
    if len(values) != len(set(values)) or (symbolic and any(_SYMBOL.fullmatch(item) is None or _looks_like_credential_material(item) for item in values)):
        _fail()
    return values


def _looks_like_local_path(value: str, *, slot: str) -> bool:
    if slot in {"declared_path", "component_source"}:
        return False
    without_urls = _HTTPS_URL.sub("", value)
    without_urls_or_packages = _PACKAGE_COORDINATE.sub("", without_urls)
    return any(
        pattern.search(without_urls_or_packages) is not None
        for pattern in (
            _WINDOWS_PATH,
            _POSIX_PATH,
            _EXPLICIT_RELATIVE_PATH,
            _BARE_RELATIVE_PATH,
            _LOCAL_ROOT_PATH,
        )
    )


def _is_sensitive_key(key: str) -> bool:
    separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key)
    tokens = tuple(
        token
        for token in re.sub(r"[^A-Za-z0-9]+", "_", separated).lower().split("_")
        if token
    )
    compact = "".join(tokens)
    forbidden_fragments = {
        "apikey",
        "accesstoken",
        "credentialvalue",
        "refreshtoken",
        "secret",
        "password",
        "prompt",
        "transcript",
        "stdout",
        "stderr",
        "output",
        "rawoutput",
    }
    return any(
        fragment in compact
        for fragment in forbidden_fragments
    )


def _contains_credential_material(value: str, *, slot: str) -> bool:
    if slot == "command":
        return any(
            _looks_like_credential_material(candidate)
            for candidate in _COMMAND_CANDIDATE.findall(value)
        )
    return _looks_like_credential_material(value)


def _privacy(
    value: object,
    *,
    key: str | None = None,
    depth: int = 0,
    budget: list[int] | None = None,
    slot: str = "generic",
) -> None:
    if budget is None:
        budget = [0, 0]
    budget[0] += 1
    if depth > _MAX_DEPTH or budget[0] > _MAX_NODES:
        _fail()
    if key and _is_sensitive_key(key) and key != "credential_references":
        _fail()
    if isinstance(value, str):
        _text(value)
        budget[1] += len(value)
        if slot == "digest":
            require_digest(value)
        elif slot == "repository_commit" and re.fullmatch(r"[0-9a-f]{40}", value) is None:
            _fail()
        opaque = slot not in {
            "component_source",
            "declared_path",
            "digest",
            "repository_commit",
        }
        if (
            budget[1] > _MAX_TOTAL_TEXT
            or "file://" in value.lower()
            or _SECRET.search(value)
            or _looks_like_local_path(value, slot=slot)
            or (opaque and _contains_credential_material(value, slot=slot))
        ):
            _fail()
    elif isinstance(value, Mapping):
        record = _mapping(value)
        for child_key, child in record.items():
            if not child_key or len(child_key) > _MAX_KEY or "\0" in child_key:
                _fail()
            budget[1] += len(child_key)
            if budget[1] > _MAX_TOTAL_TEXT:
                _fail()
            dynamic_value_slot = _DYNAMIC_MAPPING_VALUES.get(slot)
            _privacy(
                child,
                key=None if dynamic_value_slot is not None else child_key,
                depth=depth + 1,
                budget=budget,
                slot=(
                    dynamic_value_slot
                    or _SLOT_CHILDREN.get(slot, {}).get(child_key, "generic")
                ),
            )
    elif isinstance(value, list):
        if len(value) > _MAX_ITEMS:
            _fail()
        for child in value:
            _privacy(
                child,
                key=key,
                depth=depth + 1,
                budget=budget,
                slot=_SLOT_ITEMS.get(slot, "generic"),
            )
    elif isinstance(value, float):
        if not math.isfinite(value):
            _fail()
    elif isinstance(value, int) and not isinstance(value, bool):
        if abs(value) > _MAX_INTEGER:
            _fail()
    elif value is not None and not isinstance(value, bool):
        _fail()


def _source(value: object) -> None:
    source = _text(value)
    if source.startswith("https://"):
        parsed = urlsplit(source)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            _fail()
    elif _PACKAGE.fullmatch(source) is None:
        _fail()


def _resolved_versions(value: object) -> None:
    record = _mapping(value)
    for coordinate, version in record.items():
        if (
            len(coordinate) > _MAX_KEY
            or _RESOLVED_COORDINATE.fullmatch(coordinate) is None
        ):
            _fail()
        semantic_version = _text(version)
        if (
            len(semantic_version) > 128
            or _SEMANTIC_VERSION.fullmatch(semantic_version) is None
        ):
            _fail()


def _canary_policy(value: object) -> None:
    record = _mapping(value)
    if set(record) != _CANARY_POLICY_FIELDS:
        _fail()
    side_effects = _text(record["side_effects"])
    if _SYMBOL.fullmatch(side_effects) is None:
        _fail()


def _resource_ceilings(value: object) -> None:
    record = _mapping(value)
    if set(record) - _RESOURCE_CEILING_LIMITS.keys():
        _fail()
    for key, ceiling in record.items():
        if (
            isinstance(ceiling, bool)
            or not isinstance(ceiling, int)
            or not 1 <= ceiling <= _RESOURCE_CEILING_LIMITS[key]
        ):
            _fail()


def _build_environment(value: object) -> None:
    record = _mapping(value)
    allowed = (
        _BUILD_ENVIRONMENT_TEXT_FIELDS
        | _BUILD_ENVIRONMENT_DIGEST_FIELDS
    )
    if (
        set(record) - allowed
        or not _BUILD_ENVIRONMENT_REQUIRED_FIELDS <= set(record)
    ):
        _fail()
    for field in _BUILD_ENVIRONMENT_TEXT_FIELDS & record.keys():
        identity = _text(record[field])
        if _ENVIRONMENT_IDENTITY.fullmatch(identity) is None:
            _fail()
    for field in _BUILD_ENVIRONMENT_DIGEST_FIELDS & record.keys():
        require_digest(record[field])


def _files(manifest: Mapping[str, object]) -> list[tuple[str, str]]:
    components = manifest["components"]
    if not isinstance(components, list) or len(components) > _MAX_ITEMS:
        _fail()
    result: list[tuple[str, str]] = []
    logical_ids: set[str] = set()
    paths: set[str] = set()
    for component in components:
        record = _mapping(component)
        if set(record) != _COMPONENT_FIELDS or record["class"] not in _CLASSES:
            _fail()
        logical_id = _text(record["logical_id"])
        if logical_id in logical_ids:
            _fail()
        logical_ids.add(logical_id)
        path = require_relative_posix_path(record["path"])
        if path in paths:
            _fail()
        paths.add(path)
        _source(record["source"])
        for field in ("author", "license", "provenance"):
            _text(record[field])
        _strings(record["capabilities"])
        result.append((path, require_digest(record["digest"])))
        locks = record["lockfiles"]
        if not isinstance(locks, list) or len(locks) > _MAX_ITEMS:
            _fail()
        for lock in locks:
            item = _mapping(lock)
            if set(item) != _LOCKFILE_FIELDS:
                _fail()
            lock_path = require_relative_posix_path(item["path"])
            if lock_path in paths:
                _fail()
            paths.add(lock_path)
            result.append((lock_path, require_digest(item["digest"])))
    return result


def _directory_flags() -> int:
    try:
        return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    except AttributeError:
        _fail()
    raise AssertionError("unreachable")


def _leaf_flags() -> int:
    try:
        return os.O_RDONLY | os.O_NOFOLLOW
    except AttributeError:
        _fail()
    raise AssertionError("unreachable")


def _open_parent_at(root_descriptor: int, relative: str) -> tuple[int, str]:
    flags = _directory_flags()
    descriptor = os.dup(root_descriptor)
    try:
        for segment in relative.split("/")[:-1]:
            child = os.open(segment, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor, relative.rsplit("/", 1)[-1]
    except BaseException:
        os.close(descriptor)
        raise


def _digest_file_at(root_descriptor: int, relative: str) -> str:
    parent, leaf = _open_parent_at(root_descriptor, relative)
    try:
        descriptor = os.open(leaf, _leaf_flags(), dir_fd=parent)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                _fail()
            digest = hashlib.sha256()
            while chunk := os.read(descriptor, 1024 * 1024):
                digest.update(chunk)
            return digest.hexdigest()
        finally:
            os.close(descriptor)
    except OSError:
        _fail()
    finally:
        os.close(parent)
    raise AssertionError("unreachable")


def _inventory_at(
    root_descriptor: int, declared: set[str], *, published: bool
) -> None:
    expected_dirs = {"/".join(path.split("/")[:index]) for path in declared for index in range(1, len(path.split("/")))}
    files = set(declared)
    if published:
        files.add("manifest.json")
    found_dirs: set[str] = set()
    def visit(descriptor: int, prefix: str = "") -> None:
        for name in os.listdir(descriptor):
            relative = f"{prefix}/{name}" if prefix else name
            info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                found_dirs.add(relative)
                child = os.open(
                    name, _directory_flags(), dir_fd=descriptor
                )
                try:
                    visit(child, relative)
                finally:
                    os.close(child)
            elif not stat.S_ISREG(info.st_mode) or relative not in files or info.st_nlink != 1:
                _fail()
    try:
        visit(root_descriptor)
    except OSError:
        _fail()
    if found_dirs != expected_dirs:
        _fail()


def _validate_files_at(
    manifest: Mapping[str, object],
    root_descriptor: int,
    *,
    published: bool,
) -> None:
    info = os.fstat(root_descriptor)
    if not stat.S_ISDIR(info.st_mode):
        _fail()
    files = _files(manifest)
    _inventory_at(
        root_descriptor,
        {path for path, _ in files},
        published=published,
    )
    for path, digest in files:
        if _digest_file_at(root_descriptor, path) != digest:
            _fail()


def _same_inode(path: Path, descriptor: int) -> bool:
    path_info = path.lstat()
    descriptor_info = os.fstat(descriptor)
    return (
        not stat.S_ISLNK(path_info.st_mode)
        and stat.S_ISDIR(path_info.st_mode)
        and (path_info.st_dev, path_info.st_ino)
        == (descriptor_info.st_dev, descriptor_info.st_ino)
    )


def _is_unsupported_descriptor_type_error(error: TypeError) -> bool:
    message = str(error).lower()
    descriptor_keywords = ("dir_fd", "follow_symlinks")
    unsupported_signatures = (
        "unexpected keyword",
        "invalid keyword",
        "not supported",
        "unsupported",
        "unavailable",
    )
    return any(keyword in message for keyword in descriptor_keywords) and any(
        signature in message for signature in unsupported_signatures
    )


def identity_payload(manifest: Mapping[str, object]) -> dict[str, object]:
    return {key: value for key, value in _mapping(manifest).items() if key not in _EXCLUDED}


def generation_id_for(manifest: Mapping[str, object]) -> str:
    return content_digest(identity_payload(manifest), domain="hades-evolution-generation-v1")


def validate_manifest(manifest: Mapping[str, object], root: Path | None = None) -> None:
    record = _mapping(manifest)
    if (
        set(record) - _TOP
        or not _REQUIRED <= set(record)
        or type(record["schema_version"]) is not int
        or record["schema_version"] != 1
    ):
        _fail()
    _privacy(record, slot="manifest")
    for key in ("parent_generation_id", "blueprint_digest"):
        require_digest(record[key])
    _text(record["source_suggestion_id"])
    stable = _mapping(record["stable_base"])
    if set(stable) != {"release", "repository_commit", "compatibility_version", "configuration_fingerprint"}:
        _fail()
    _text(stable["release"]); _text(stable["compatibility_version"]); require_digest(stable["configuration_fingerprint"])
    if stable["repository_commit"] is not None and re.fullmatch(r"[0-9a-f]{40}", _text(stable["repository_commit"])) is None:
        _fail()
    _text(record["compatibility_range"])
    _files(record)
    for key in ("dependency_constraints", "service_prerequisites", "capabilities", "invariants", "verification_commands", "incompatibility_reasons"):
        _strings(record[key])
    _strings(record["credential_references"], symbolic=True)
    _resolved_versions(record["resolved_versions"])
    _canary_policy(record["canary_policy"])
    _resource_ceilings(record["resource_ceilings"])
    _build_environment(record["build_environment"])
    for key in ("expected_organism_diff", "builder_version", "rollback_plan"):
        _text(record[key])
    try:
        parsed = datetime.strptime(_text(record["created_at"]), "%Y-%m-%dT%H:%M:%S.%fZ")
        if parsed.strftime("%Y-%m-%dT%H:%M:%S.%fZ") != record["created_at"]:
            _fail()
    except ValueError:
        _fail()
    if "generation_id" in record and record["generation_id"] != generation_id_for(record):
        _fail()
    if root is not None:
        try:
            root_descriptor = os.open(root, _directory_flags())
            try:
                _validate_files_at(
                    record,
                    root_descriptor,
                    published="generation_id" in record,
                )
                if not _same_inode(root, root_descriptor):
                    _fail()
            finally:
                os.close(root_descriptor)
        except TypeError as error:
            if _is_unsupported_descriptor_type_error(error):
                _fail()
            raise
        except (OSError, NotImplementedError):
            _fail()


def _validate_manifest_at_fd(
    manifest: Mapping[str, object],
    root_descriptor: int,
    *,
    published: bool,
) -> None:
    """Validate one manifest and all declared bytes from an anchored root FD."""

    validate_manifest(manifest)
    try:
        _validate_files_at(manifest, root_descriptor, published=published)
    except TypeError as error:
        if _is_unsupported_descriptor_type_error(error):
            _fail()
        raise
    except (OSError, NotImplementedError):
        _fail()
