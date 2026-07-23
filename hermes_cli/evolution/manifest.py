"""Validation and identity projection for immutable evolution generations."""

from __future__ import annotations

import hashlib
import re
import stat
from collections.abc import Mapping
from pathlib import Path

from .contract import (
    EvolutionContractError,
    canonical_json_bytes,
    content_digest,
    require_digest,
    require_relative_posix_path,
)


_IDENTITY_EXCLUSIONS = frozenset({"generation_id", "created_at", "attestations"})
_TOP_LEVEL = frozenset({
    "schema_version", "generation_id", "parent_generation_id", "source_suggestion_id",
    "blueprint_digest", "stable_base", "compatibility_range", "components",
    "dependency_constraints", "resolved_versions", "credential_references",
    "service_prerequisites", "capabilities", "invariants", "verification_commands",
    "canary_policy", "resource_ceilings", "expected_organism_diff",
    "build_environment", "builder_version", "rollback_plan",
    "incompatibility_reasons", "created_at", "attestations",
})
_REQUIRED = _TOP_LEVEL - {"generation_id", "attestations"}
_COMPONENT_CLASSES = frozenset({"skill", "script", "plugin", "mcp"})
_SECRET_KEY = re.compile(r"(?:secret|password|api[_-]?key|access[_-]?token)\Z", re.I)
_SECRET_VALUE = re.compile(r"(?:sk|pk)[_-](?:live|test|proj)[_-]|ghp[_-]|github[_-]pat[_-]|glpat[_-]|xox[bp][_-]|akia", re.I)
_PACKAGE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._/-]*(?:@[a-zA-Z0-9._+-]+)?\Z")


def _fail(code: str = "invalid_manifest") -> None:
    raise EvolutionContractError(code)


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        _fail()
    return value


def _text(value: object, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value) or "\0" in value:
        _fail()
    return value


def _strings(value: object) -> list[str]:
    if not isinstance(value, list):
        _fail()
    values = [_text(item) for item in value]
    if len(values) != len(set(values)):
        _fail()
    return values


def _safe_values(value: object, *, key: str | None = None) -> None:
    if key is not None and _SECRET_KEY.search(key) and key != "credential_references":
        _fail()
    if isinstance(value, str):
        if "file://" in value.lower() or _SECRET_VALUE.search(value):
            _fail()
    elif isinstance(value, Mapping):
        for child_key, child in value.items():
            if not isinstance(child_key, str):
                _fail()
            _safe_values(child, key=child_key)
    elif isinstance(value, list):
        for child in value:
            _safe_values(child, key=key)
    elif value is not None and not isinstance(value, (bool, int, float)):
        _fail()


def _declared_files(manifest: Mapping[str, object]) -> list[tuple[str, str]]:
    files: list[tuple[str, str]] = []
    components = manifest["components"]
    if not isinstance(components, list):
        _fail()
    logical_ids: set[str] = set()
    paths: set[str] = set()
    for component in components:
        record = _mapping(component)
        if set(record) != {"class", "logical_id", "path", "digest", "source", "author", "license", "provenance", "capabilities", "lockfiles"}:
            _fail()
        if record["class"] not in _COMPONENT_CLASSES:
            _fail()
        logical_id = _text(record["logical_id"])
        if logical_id in logical_ids:
            _fail()
        logical_ids.add(logical_id)
        path = require_relative_posix_path(record["path"])
        digest = require_digest(record["digest"])
        if path in paths:
            _fail()
        paths.add(path)
        source = _text(record["source"])
        if not (source.startswith("https://") or _PACKAGE.fullmatch(source)):
            _fail()
        for name in ("author", "license", "provenance"):
            _text(record[name])
        _strings(record["capabilities"])
        files.append((path, digest))
        lockfiles = record["lockfiles"]
        if not isinstance(lockfiles, list):
            _fail()
        for lockfile in lockfiles:
            item = _mapping(lockfile)
            if set(item) != {"path", "digest"}:
                _fail()
            lock_path = require_relative_posix_path(item["path"])
            if lock_path in paths:
                _fail()
            paths.add(lock_path)
            files.append((lock_path, require_digest(item["digest"])))
    return files


def _hash_regular_file(root: Path, relative_path: str) -> str:
    path = root / relative_path
    current = root
    for segment in relative_path.split("/")[:-1]:
        current = current / segment
        try:
            parent_info = current.lstat()
        except OSError:
            _fail()
        if not current.is_dir() or stat.S_ISLNK(parent_info.st_mode):
            _fail()
    try:
        info = path.lstat()
    except OSError:
        _fail()
    if not path.is_file() or path.is_symlink() or info.st_nlink != 1:
        _fail()
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        _fail()
    raise AssertionError("unreachable")


def identity_payload(manifest: Mapping[str, object]) -> dict[str, object]:
    """Return the exact, mutable-field-free identity projection."""

    source = _mapping(manifest)
    return {key: value for key, value in source.items() if key not in _IDENTITY_EXCLUSIONS}


def generation_id_for(manifest: Mapping[str, object]) -> str:
    """Return the domain-separated digest of the canonical identity manifest."""

    return content_digest(identity_payload(manifest), domain="hades-evolution-generation-v1")


def validate_manifest(manifest: Mapping[str, object], root: Path | None = None) -> None:
    """Fail closed unless a schema-v1 manifest and its declared bytes are valid."""

    record = _mapping(manifest)
    if set(record) - _TOP_LEVEL or not _REQUIRED <= set(record):
        _fail()
    if record["schema_version"] != 1:
        _fail()
    _safe_values(record)
    for key in ("parent_generation_id", "blueprint_digest"):
        require_digest(record[key])
    _text(record["source_suggestion_id"])
    stable_base = _mapping(record["stable_base"])
    if set(stable_base) != {"release", "repository_commit", "compatibility_version", "configuration_fingerprint"}:
        _fail()
    _text(stable_base["release"])
    if stable_base["repository_commit"] is not None:
        commit = _text(stable_base["repository_commit"])
        if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
            _fail()
    _text(stable_base["compatibility_version"])
    require_digest(stable_base["configuration_fingerprint"])
    _text(record["compatibility_range"])
    files = _declared_files(record)
    for key in ("dependency_constraints", "credential_references", "service_prerequisites", "capabilities", "invariants", "verification_commands", "incompatibility_reasons"):
        _strings(record[key])
    for key in ("resolved_versions", "canary_policy", "resource_ceilings", "build_environment"):
        _mapping(record[key])
    for key in ("expected_organism_diff", "builder_version", "rollback_plan", "created_at"):
        _text(record[key])
    if "generation_id" in record and record["generation_id"] != generation_id_for(record):
        _fail()
    if root is not None:
        try:
            root_info = root.lstat()
        except OSError:
            _fail()
        if not root.is_dir() or root.is_symlink():
            _fail()
        declared = {path for path, _ in files}
        actual = {item.relative_to(root).as_posix() for item in root.rglob("*") if item.is_file() or item.is_symlink()}
        if not declared <= actual or actual - declared - {"manifest.json"}:
            _fail()
        for path, digest in files:
            if _hash_regular_file(root, path) != digest:
                _fail()
