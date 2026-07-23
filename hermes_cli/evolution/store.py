"""Private, content-addressed immutable storage for evolution generations."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType

from hermes_constants import get_hermes_home

from .contract import canonical_json_bytes, require_digest
from .manifest import (
    _validate_manifest_at_fd,
    generation_id_for,
    validate_manifest,
)


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


def _directory_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _leaf_flags() -> int:
    return os.O_RDONLY | os.O_NOFOLLOW


def _same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, _directory_flags())
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


def _read_file_at(root_descriptor: int, relative_path: str) -> bytes:
    directory = os.dup(root_descriptor)
    try:
        parts = relative_path.split("/")
        for part in parts[:-1]:
            child = os.open(
                part,
                _directory_flags(),
                dir_fd=directory,
            )
            os.close(directory)
            directory = child
        descriptor = os.open(
            parts[-1],
            _leaf_flags(),
            dir_fd=directory,
        )
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise _error("declared file is unsafe")
            chunks: list[bytes] = []
            while chunk := os.read(descriptor, 1024 * 1024):
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            os.close(descriptor)
    finally:
        os.close(directory)


def _validate_readonly_tree_at(root_descriptor: int) -> None:
    root_info = os.fstat(root_descriptor)
    if not stat.S_ISDIR(root_info.st_mode) or stat.S_IMODE(root_info.st_mode) & 0o222:
        raise _error("published directory is writable")

    def visit(directory: int) -> None:
        for name in os.listdir(directory):
            path_info = os.stat(
                name,
                dir_fd=directory,
                follow_symlinks=False,
            )
            if stat.S_ISDIR(path_info.st_mode):
                child = os.open(
                    name,
                    _directory_flags(),
                    dir_fd=directory,
                )
                try:
                    child_info = os.fstat(child)
                    if (
                        not _same_inode(path_info, child_info)
                        or stat.S_IMODE(child_info.st_mode) & 0o222
                    ):
                        raise _error("published directory is writable")
                    visit(child)
                finally:
                    os.close(child)
            elif stat.S_ISREG(path_info.st_mode):
                child = os.open(
                    name,
                    _leaf_flags(),
                    dir_fd=directory,
                )
                try:
                    child_info = os.fstat(child)
                    if (
                        not _same_inode(path_info, child_info)
                        or child_info.st_nlink != 1
                        or stat.S_IMODE(child_info.st_mode) & 0o222
                    ):
                        raise _error("published content is writable")
                finally:
                    os.close(child)
            else:
                raise _error("published content is unsafe")

    visit(root_descriptor)


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
            descriptor = os.open(self.root, _directory_flags())
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISDIR(info.st_mode):
                    raise _error("store root is unsafe")
                os.fchmod(descriptor, 0o700)
                os.fsync(descriptor)
                if not _same_inode(self.root.lstat(), info):
                    raise _error("store root changed during creation")
            finally:
                os.close(descriptor)
        info = self.root.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise _error("store root is unsafe")
        if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
            raise _error("store root ownership")
        if stat.S_IMODE(info.st_mode) != 0o700:
            raise _error("store root is not private")
        _fsync_directory(self.root.parent)

    @staticmethod
    def _require_posix() -> None:
        required_functions = (
            "dup",
            "fchmod",
            "fsync",
            "fstat",
            "listdir",
            "open",
            "stat",
        )
        supports_dir_fd = getattr(os, "supports_dir_fd", frozenset())
        supports_fd = getattr(os, "supports_fd", frozenset())
        supported = (
            os.name == "posix"
            and hasattr(os, "O_NOFOLLOW")
            and hasattr(os, "O_DIRECTORY")
            and all(hasattr(os, name) for name in required_functions)
            and os.open in supports_dir_fd
            and os.stat in supports_dir_fd
            and os.listdir in supports_fd
        )
        try:
            import fcntl  # noqa: F401
        except (ImportError, NotImplementedError):
            supported = False
        if not supported:
            raise _error("immutable generation store requires POSIX no-follow support")

    @contextmanager
    def _publication_lock(self):
        import fcntl

        root_descriptor = os.open(self.root, _directory_flags())
        descriptor: int | None = None
        try:
            for _ in range(3):
                try:
                    descriptor = os.open(
                        ".publish.lock",
                        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                        0o600,
                        dir_fd=root_descriptor,
                    )
                    break
                except FileNotFoundError:
                    if not _same_inode(
                        self.root.lstat(),
                        os.fstat(root_descriptor),
                    ):
                        raise _error("store root changed before publication")
            if descriptor is None:
                raise _error("publication lock could not be created")
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise _error("publication lock is unsafe")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            if not _same_inode(self.root.lstat(), os.fstat(root_descriptor)):
                raise _error("store root changed before publication")
            yield
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(root_descriptor)

    @staticmethod
    def _read_source(root: Path, relative_path: str) -> bytes:
        directory = os.open(root, _directory_flags())
        try:
            return _read_file_at(directory, relative_path)
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
            store_descriptor = os.open(self.root, _directory_flags())
            try:
                generation_descriptor = os.open(
                    generation_id,
                    _directory_flags(),
                    dir_fd=store_descriptor,
                )
            except BaseException:
                os.close(store_descriptor)
                raise
        except OSError as exc:
            raise _error("published manifest is unreadable") from exc
        try:
            try:
                manifest = json.loads(
                    _read_file_at(generation_descriptor, "manifest.json")
                )
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise _error("published manifest is unreadable") from exc
            if not isinstance(manifest, dict):
                raise _error("published manifest is invalid")
            try:
                _validate_manifest_at_fd(
                    manifest,
                    generation_descriptor,
                    published=True,
                )
                if (
                    manifest.get("generation_id") != generation_id
                    or generation_id_for(manifest) != generation_id
                ):
                    raise _error("published identity mismatch")
                _validate_readonly_tree_at(generation_descriptor)
            except ValueError as exc:
                if str(exc).startswith("generation integrity failure"):
                    raise
                raise _error("published content mismatch") from exc

            generation_info = os.fstat(generation_descriptor)
            linked_info = os.stat(
                generation_id,
                dir_fd=store_descriptor,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISDIR(linked_info.st_mode)
                or not _same_inode(generation_info, linked_info)
                or not _same_inode(generation_info, root.lstat())
                or not _same_inode(os.fstat(store_descriptor), self.root.lstat())
            ):
                raise _error("published root changed during verification")
            return PublishedGeneration(
                generation_id,
                root,
                MappingProxyType(manifest),
            )
        except OSError as exc:
            raise _error("published content is unreadable") from exc
        finally:
            os.close(generation_descriptor)
            os.close(store_descriptor)

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
