"""Validation and identity projection for immutable evolution generations."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from .contract import EvolutionContractError, content_digest, require_digest, require_relative_posix_path


_EXCLUDED = frozenset({"generation_id", "created_at", "attestations"})
_TOP = frozenset({"schema_version", "generation_id", "parent_generation_id", "source_suggestion_id", "blueprint_digest", "stable_base", "compatibility_range", "components", "dependency_constraints", "resolved_versions", "credential_references", "service_prerequisites", "capabilities", "invariants", "verification_commands", "canary_policy", "resource_ceilings", "expected_organism_diff", "build_environment", "builder_version", "rollback_plan", "incompatibility_reasons", "created_at", "attestations"})
_REQUIRED = _TOP - {"generation_id", "attestations"}
_CLASSES = frozenset({"skill", "script", "plugin", "mcp"})
_SYMBOL = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z", re.ASCII)
_PACKAGE = re.compile(r"[a-z0-9][a-z0-9._-]*(?:/[a-z0-9][a-z0-9._-]*)?(?:@[a-z0-9._+-]+)?\Z", re.ASCII)
_SENSITIVE = re.compile(r"(?:secret|password|api[_-]?key|access[_-]?token)\Z", re.I)
_PATH_KEY = re.compile(r"(?:path|root|workspace|cwd|directory)\Z", re.I)
_SECRET = re.compile(r"(?:sk|pk)[_-](?:live|test|proj)[_-]|ghp[_-]|github[_-]pat[_-]|glpat[_-]|xox[bp][_-]|akia", re.I)
_MAX_ITEMS = 64
_MAX_TEXT = 512


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
    if len(values) != len(set(values)) or (symbolic and any(_SYMBOL.fullmatch(item) is None for item in values)):
        _fail()
    return values


def _privacy(value: object, *, key: str | None = None, depth: int = 0) -> None:
    if depth > 8:
        _fail()
    if key and _SENSITIVE.search(key) and key != "credential_references":
        _fail()
    if isinstance(value, str):
        if "file://" in value.lower() or _SECRET.search(value) or (key and key != "path" and _PATH_KEY.search(key) and (value.startswith("/") or value.startswith("~"))):
            _fail()
    elif isinstance(value, Mapping):
        for child_key, child in _mapping(value).items():
            _privacy(child, key=child_key, depth=depth + 1)
    elif isinstance(value, list):
        if len(value) > _MAX_ITEMS:
            _fail()
        for child in value:
            _privacy(child, key=key, depth=depth + 1)
    elif value is not None and not isinstance(value, (bool, int, float)):
        _fail()


def _source(value: object) -> None:
    source = _text(value)
    if source.startswith("https://"):
        parsed = urlsplit(source)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            _fail()
    elif _PACKAGE.fullmatch(source) is None:
        _fail()


def _files(manifest: Mapping[str, object]) -> list[tuple[str, str]]:
    components = manifest["components"]
    if not isinstance(components, list) or len(components) > _MAX_ITEMS:
        _fail()
    result: list[tuple[str, str]] = []
    logical_ids: set[str] = set()
    paths: set[str] = set()
    expected = {"class", "logical_id", "path", "digest", "source", "author", "license", "provenance", "capabilities", "lockfiles"}
    for component in components:
        record = _mapping(component)
        if set(record) != expected or record["class"] not in _CLASSES:
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
            if set(item) != {"path", "digest"}:
                _fail()
            lock_path = require_relative_posix_path(item["path"])
            if lock_path in paths:
                _fail()
            paths.add(lock_path)
            result.append((lock_path, require_digest(item["digest"])))
    return result


def _open_parent(root: Path, relative: str) -> tuple[int, str]:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(root, flags)
    try:
        for segment in relative.split("/")[:-1]:
            child = os.open(segment, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor, relative.rsplit("/", 1)[-1]
    except BaseException:
        os.close(descriptor)
        raise


def _digest_file(root: Path, relative: str) -> str:
    parent, leaf = _open_parent(root, relative)
    try:
        descriptor = os.open(leaf, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent)
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


def _inventory(root: Path, declared: set[str], *, published: bool) -> None:
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
                child = os.open(name, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0), dir_fd=descriptor)
                try:
                    visit(child, relative)
                finally:
                    os.close(child)
            elif not stat.S_ISREG(info.st_mode) or relative not in files or info.st_nlink != 1:
                _fail()
    try:
        root_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
        try:
            visit(root_fd)
        finally:
            os.close(root_fd)
    except OSError:
        _fail()
    if found_dirs != expected_dirs:
        _fail()


def identity_payload(manifest: Mapping[str, object]) -> dict[str, object]:
    return {key: value for key, value in _mapping(manifest).items() if key not in _EXCLUDED}


def generation_id_for(manifest: Mapping[str, object]) -> str:
    return content_digest(identity_payload(manifest), domain="hades-evolution-generation-v1")


def validate_manifest(manifest: Mapping[str, object], root: Path | None = None) -> None:
    record = _mapping(manifest)
    if set(record) - _TOP or not _REQUIRED <= set(record) or record["schema_version"] != 1:
        _fail()
    _privacy(record)
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
    files = _files(record)
    for key in ("dependency_constraints", "service_prerequisites", "capabilities", "invariants", "verification_commands", "incompatibility_reasons"):
        _strings(record[key])
    _strings(record["credential_references"], symbolic=True)
    for key in ("resolved_versions", "canary_policy", "resource_ceilings", "build_environment"):
        _mapping(record[key])
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
            info = root.lstat()
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
                _fail()
            declared = {path for path, _ in files}
            _inventory(root, declared, published="generation_id" in record)
            for path, digest in files:
                if _digest_file(root, path) != digest:
                    _fail()
        except OSError:
            _fail()
