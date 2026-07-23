"""Private, content-addressed immutable storage for evolution generations."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
import sys
from contextlib import contextmanager
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType

from hermes_constants import get_hermes_home

from .contract import canonical_json_bytes, require_digest
from .manifest import generation_id_for, validate_manifest


@dataclass(frozen=True)
class StableBaseIdentity:
    release: str
    repository_commit: str | None
    compatibility_version: str
    configuration_fingerprint: str


@dataclass(frozen=True)
class PublishedGeneration:
    generation_id: str
    root: Path
    manifest: Mapping[str, object]


def _error(message: str) -> ValueError:
    return ValueError(f"generation integrity failure: {message}")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _readonly_tree(root: Path) -> None:
    for current, directories, files in os.walk(root, topdown=False, followlinks=False):
        base = Path(current)
        for name in files:
            descriptor = os.open(base / name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                os.fchmod(descriptor, 0o444)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        for name in directories:
            os.chmod(base / name, 0o555)
        os.chmod(base, 0o555)
        _fsync_directory(base)


def _remove_owned_tree(root: Path) -> None:
    for current, directories, files in os.walk(root, topdown=False, followlinks=False):
        base = Path(current)
        for name in files:
            os.chmod(base / name, 0o600)
        for name in directories:
            os.chmod(base / name, 0o700)
        os.chmod(base, 0o700)
    shutil.rmtree(root)


def _write_all(descriptor: int, data: bytes) -> None:
    """Write all bytes, rejecting an I/O implementation that makes no progress."""

    view = memoryview(data)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise _error("short write")
        view = view[written:]


class GenerationStore:
    """Publish validated overlay bytes through same-filesystem directory rename."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else get_hermes_home() / "evolution" / "generations"

    def _secure_root(self) -> None:
        self._require_posix()
        try:
            self.root.mkdir(parents=True, mode=0o700)
            created = True
        except FileExistsError:
            created = False
        if created:
            info = self.root.lstat()
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
                raise _error("store root is unsafe")
            os.chmod(self.root, 0o700)
        info = self.root.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise _error("store root is unsafe")
        if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
            raise _error("store root ownership")
        if stat.S_IMODE(info.st_mode) != 0o700:
            raise _error("store root is not private")

    @staticmethod
    def _require_posix() -> None:
        if os.name != "posix" or not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
            raise _error("immutable generation store requires POSIX no-follow support")

    @contextmanager
    def _publication_lock(self):
        import fcntl

        lock = self.root / ".publish.lock"
        descriptor = os.open(lock, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise _error("publication lock is unsafe")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)

    @staticmethod
    def _read_source(root: Path, relative_path: str) -> bytes:
        directory = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
        try:
            parts = relative_path.split("/")
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory)
                os.close(directory)
                directory = child
            descriptor = os.open(parts[-1], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory)
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise _error("declared source is unsafe")
                chunks: list[bytes] = []
                while chunk := os.read(descriptor, 1024 * 1024):
                    chunks.append(chunk)
                return b"".join(chunks)
            finally:
                os.close(descriptor)
        finally:
            os.close(directory)

    @staticmethod
    def _declared_files(manifest: Mapping[str, object]) -> list[tuple[str, str]]:
        files: list[tuple[str, str]] = []
        for component in manifest["components"]:  # type: ignore[index]
            files.append((component["path"], component["digest"]))  # type: ignore[index]
            files.extend((lock["path"], lock["digest"]) for lock in component["lockfiles"])  # type: ignore[index]
        return files

    def _published(self, generation_id: str) -> PublishedGeneration:
        root = self.root / generation_id
        try:
            info = root.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise _error("published root is unsafe")
            manifest = json.loads(self._read_source(root, "manifest.json"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _error("published manifest is unreadable") from exc
        if not isinstance(manifest, dict):
            raise _error("published manifest is invalid")
        try:
            validate_manifest(manifest, root)
            if manifest.get("generation_id") != generation_id or generation_id_for(manifest) != generation_id:
                raise _error("published identity mismatch")
        except ValueError as exc:
            if str(exc).startswith("generation integrity failure"):
                raise
            raise _error("published content mismatch") from exc
        for current, directories, files in os.walk(root, followlinks=False):
            directory = Path(current)
            if directory.is_symlink() or stat.S_IMODE(directory.lstat().st_mode) & 0o222:
                raise _error("published directory is writable")
            for name in directories:
                child = directory / name
                if child.is_symlink():
                    raise _error("published content is symlinked")
            for name in files:
                path = directory / name
                if path.is_symlink() or stat.S_IMODE(path.lstat().st_mode) & 0o222:
                    raise _error("published content is writable")
        return PublishedGeneration(generation_id, root, MappingProxyType(manifest))

    def publish_staged(self, staged_root: Path, manifest: Mapping[str, object]) -> PublishedGeneration:
        """Validate, copy, fsync, atomically publish, then reopen one generation."""

        self._secure_root()
        staged_root = Path(staged_root)
        try:
            validate_manifest(manifest, staged_root)
        except ValueError as exc:
            raise _error("staged manifest or bytes") from exc
        generation_id = generation_id_for(manifest)
        destination = self.root / generation_id
        with self._publication_lock():
            if destination.exists() or destination.is_symlink():
                return self._published(generation_id)
            temporary = Path(tempfile.mkdtemp(prefix=".generation-", dir=self.root))
            try:
                final_manifest = dict(manifest)
                final_manifest["generation_id"] = generation_id
                for relative_path, digest in self._declared_files(final_manifest):
                    data = self._read_source(staged_root, relative_path)
                    if hashlib.sha256(data).hexdigest() != digest:
                        raise _error("source digest changed")
                    target = temporary / relative_path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
                    try:
                        _write_all(descriptor, data)
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
                manifest_path = temporary / "manifest.json"
                descriptor = os.open(manifest_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
                try:
                    _write_all(descriptor, canonical_json_bytes(final_manifest))
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                _readonly_tree(temporary)
                os.rename(temporary, destination)
                _fsync_directory(self.root)
                return self._published(generation_id)
            finally:
                if temporary.exists():
                    _remove_owned_tree(temporary)

    def verify(self, generation_id: str) -> PublishedGeneration:
        self._secure_root()
        require_digest(generation_id)
        return self._published(generation_id)

    def initialize_baseline(self, stable_base: StableBaseIdentity) -> PublishedGeneration:
        manifest: dict[str, object] = {
            "schema_version": 1, "parent_generation_id": "0" * 64,
            "source_suggestion_id": "baseline", "blueprint_digest": "0" * 64,
            "stable_base": {"release": stable_base.release, "repository_commit": stable_base.repository_commit, "compatibility_version": stable_base.compatibility_version, "configuration_fingerprint": stable_base.configuration_fingerprint},
            "compatibility_range": stable_base.compatibility_version, "components": [],
            "dependency_constraints": [], "resolved_versions": {}, "credential_references": [],
            "service_prerequisites": [], "capabilities": [], "invariants": [],
            "verification_commands": [], "canary_policy": {"side_effects": "none"},
            "resource_ceilings": {}, "expected_organism_diff": "empty overlay",
            "build_environment": {"builder": "hermes", "version": stable_base.release},
            "builder_version": stable_base.release, "rollback_plan": "remain on stable base",
            "incompatibility_reasons": [], "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        }
        stage = Path(tempfile.mkdtemp(prefix=".baseline-", dir=self.root.parent if self.root.parent.exists() else None))
        try:
            return self.publish_staged(stage, manifest)
        finally:
            shutil.rmtree(stage, ignore_errors=True)
