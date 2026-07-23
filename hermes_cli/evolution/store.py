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
from typing import Literal

from hermes_constants import get_hermes_home

from .contract import canonical_json_bytes, content_digest, require_digest
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


@dataclass(frozen=True)
class VerifiedManifestDescriptor:
    """One descriptor-anchored generation manifest and its exact identity bytes."""

    generation: PublishedGeneration
    manifest_bytes: bytes
    manifest_digest: str


GenerationProofFailureCode = Literal[
    "store_unavailable",
    "generation_missing",
    "generation_unsafe",
    "manifest_missing",
    "manifest_unsafe",
    "manifest_malformed",
    "manifest_noncanonical",
    "manifest_invalid",
    "manifest_identity_mismatch",
    "published_tree_unsafe",
    "published_content_mismatch",
    "proof_limit_exceeded",
    "proof_changed",
]


@dataclass(frozen=True)
class ExistingGenerationProof:
    """Bounded typed evidence from one existing-generation proof attempt."""

    descriptor: VerifiedManifestDescriptor | None
    failure_code: GenerationProofFailureCode | None
    evidence_digest: str


_MAX_PROOF_MANIFEST_BYTES = 256 * 1024
_MAX_PROOF_ENTRIES = 4_096
_MAX_PROOF_FILE_BYTES = 64 * 1024 * 1024
_MAX_PROOF_TOTAL_BYTES = 256 * 1024 * 1024


def _error(message: str) -> ValueError:
    return ValueError(f"generation integrity failure: {message}")


def _directory_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _leaf_flags() -> int:
    return os.O_RDONLY | os.O_NOFOLLOW


def _same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _validate_private_directory(info: os.stat_result) -> None:
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise _error("managed hierarchy is unsafe")
    if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
        raise _error("managed hierarchy ownership")
    if stat.S_IMODE(info.st_mode) != 0o700:
        raise _error("managed hierarchy is not private")


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


def _proof_evidence_digest(
    code: str,
    material: Mapping[str, object],
) -> str:
    return content_digest(
        {"code": code, "material": material},
        domain="hades-evolution-generation-proof-v1",
    )


def _failed_proof(
    code: GenerationProofFailureCode,
    material: Mapping[str, object],
) -> ExistingGenerationProof:
    return ExistingGenerationProof(
        descriptor=None,
        failure_code=code,
        evidence_digest=_proof_evidence_digest(code, material),
    )


def _read_bounded_manifest(
    generation_descriptor: int,
) -> tuple[bytes, os.stat_result] | ExistingGenerationProof:
    try:
        linked = os.stat(
            "manifest.json",
            dir_fd=generation_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return _failed_proof("manifest_missing", {"manifest": "missing"})
    except OSError:
        return _failed_proof("manifest_unsafe", {"manifest": "unreadable"})
    if (
        not stat.S_ISREG(linked.st_mode)
        or linked.st_nlink != 1
        or linked.st_size > _MAX_PROOF_MANIFEST_BYTES
    ):
        code: GenerationProofFailureCode = (
            "proof_limit_exceeded"
            if linked.st_size > _MAX_PROOF_MANIFEST_BYTES
            else "manifest_unsafe"
        )
        return _failed_proof(
            code,
            {
                "kind": stat.S_IFMT(linked.st_mode),
                "links": linked.st_nlink,
                "size": min(linked.st_size, _MAX_PROOF_MANIFEST_BYTES + 1),
            },
        )
    try:
        descriptor = os.open(
            "manifest.json",
            _leaf_flags(),
            dir_fd=generation_descriptor,
        )
    except OSError:
        return _failed_proof("manifest_unsafe", {"manifest": "unreadable"})
    try:
        opened = os.fstat(descriptor)
        if (
            not _same_inode(linked, opened)
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
        ):
            return _failed_proof(
                "manifest_unsafe",
                {"manifest": "changed_or_unsafe"},
            )
        data = bytearray()
        while chunk := os.read(descriptor, 16 * 1024):
            data.extend(chunk)
            if len(data) > _MAX_PROOF_MANIFEST_BYTES:
                return _failed_proof(
                    "proof_limit_exceeded",
                    {
                        "bounded_prefix_digest": hashlib.sha256(
                            data[:_MAX_PROOF_MANIFEST_BYTES]
                        ).hexdigest(),
                        "size_at_least": len(data),
                    },
                )
        if not _same_inode(
            opened,
            os.stat(
                "manifest.json",
                dir_fd=generation_descriptor,
                follow_symlinks=False,
            ),
        ):
            return _failed_proof(
                "proof_changed",
                {"manifest": "changed"},
            )
        return bytes(data), opened
    except OSError:
        return _failed_proof("manifest_unsafe", {"manifest": "unreadable"})
    finally:
        os.close(descriptor)


def _bounded_tree_evidence(
    root_descriptor: int,
    *,
    initial_bytes: int,
) -> tuple[str, int] | ExistingGenerationProof:
    entries: list[dict[str, object]] = []
    total_bytes = initial_bytes

    def visit(directory: int, prefix: str = "") -> ExistingGenerationProof | None:
        nonlocal total_bytes
        try:
            names = sorted(os.listdir(directory))
        except OSError:
            return _failed_proof(
                "published_tree_unsafe",
                {"tree": "unreadable"},
            )
        for name in names:
            relative = f"{prefix}/{name}" if prefix else name
            try:
                info = os.stat(
                    name,
                    dir_fd=directory,
                    follow_symlinks=False,
                )
            except OSError:
                return _failed_proof(
                    "published_tree_unsafe",
                    {"tree": "entry_unreadable"},
                )
            entries.append(
                {
                    "relative_digest": hashlib.sha256(
                        relative.encode("utf-8", "surrogateescape")
                    ).hexdigest(),
                    "kind": stat.S_IFMT(info.st_mode),
                    "mode": stat.S_IMODE(info.st_mode),
                    "size": info.st_size,
                    "links": info.st_nlink,
                }
            )
            if len(entries) > _MAX_PROOF_ENTRIES:
                return _failed_proof(
                    "proof_limit_exceeded",
                    {
                        "limit": "entries",
                        "observed_at_least": len(entries),
                    },
                )
            if stat.S_ISDIR(info.st_mode):
                try:
                    child = os.open(
                        name,
                        _directory_flags(),
                        dir_fd=directory,
                    )
                except OSError:
                    return _failed_proof(
                        "published_tree_unsafe",
                        {"tree": "directory_unsafe"},
                    )
                try:
                    failure = visit(child, relative)
                    if failure is not None:
                        return failure
                finally:
                    os.close(child)
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                return _failed_proof(
                    "published_tree_unsafe",
                    {"tree": "entry_unsafe"},
                )
            if relative == "manifest.json":
                continue
            if (
                info.st_size > _MAX_PROOF_FILE_BYTES
                or total_bytes + info.st_size > _MAX_PROOF_TOTAL_BYTES
            ):
                return _failed_proof(
                    "proof_limit_exceeded",
                    {
                        "limit": (
                            "file"
                            if info.st_size > _MAX_PROOF_FILE_BYTES
                            else "total"
                        ),
                        "size": min(
                            info.st_size,
                            _MAX_PROOF_FILE_BYTES + 1,
                        ),
                    },
                )
            try:
                child = os.open(
                    name,
                    _leaf_flags(),
                    dir_fd=directory,
                )
            except OSError:
                return _failed_proof(
                    "published_tree_unsafe",
                    {"tree": "file_unsafe"},
                )
            digest = hashlib.sha256()
            read_size = 0
            try:
                while chunk := os.read(child, 1024 * 1024):
                    read_size += len(chunk)
                    total_bytes += len(chunk)
                    if (
                        read_size > _MAX_PROOF_FILE_BYTES
                        or total_bytes > _MAX_PROOF_TOTAL_BYTES
                    ):
                        return _failed_proof(
                            "proof_limit_exceeded",
                            {
                                "limit": (
                                    "file"
                                    if read_size > _MAX_PROOF_FILE_BYTES
                                    else "total"
                                ),
                                "size_at_least": read_size,
                            },
                        )
                    digest.update(chunk)
                if read_size != info.st_size:
                    return _failed_proof(
                        "proof_changed",
                        {"tree": "file_size_changed"},
                    )
                entries[-1]["content_digest"] = digest.hexdigest()
            except OSError:
                return _failed_proof(
                    "published_tree_unsafe",
                    {"tree": "file_unreadable"},
                )
            finally:
                os.close(child)
        return None

    failure = visit(root_descriptor)
    if failure is not None:
        return failure
    return (
        content_digest(
            {"entries": entries, "total_bytes": total_bytes},
            domain="hades-evolution-generation-tree-proof-v1",
        ),
        total_bytes,
    )


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
        self._trusted_anchor: Path | None = None
        if root is None:
            self._trusted_anchor = Path(get_hermes_home())
            self.root = self._trusted_anchor / "evolution" / "generations"
        else:
            self.root = Path(root)

    def _hierarchy_anchor(self) -> tuple[Path, tuple[str, ...]]:
        if self._trusted_anchor is not None:
            anchor = self._trusted_anchor
            parts = self.root.relative_to(anchor).parts
        else:
            anchor = self.root.parent
            parts = (self.root.name,)

        while True:
            try:
                anchor.lstat()
                return anchor, parts
            except FileNotFoundError:
                parent = anchor.parent
                if parent == anchor:
                    raise _error("managed hierarchy has no trusted ancestor")
                parts = (anchor.name, *parts)
                anchor = parent
            except OSError as error:
                raise _error("managed hierarchy is unsafe") from error

    @staticmethod
    def _open_private_child(parent_descriptor: int, name: str) -> int:
        created = False
        created_info: os.stat_result | None = None
        try:
            try:
                path_info = os.stat(
                    name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                try:
                    os.mkdir(name, 0o700, dir_fd=parent_descriptor)
                    created = True
                    created_info = os.stat(
                        name,
                        dir_fd=parent_descriptor,
                        follow_symlinks=False,
                    )
                except FileExistsError:
                    pass
                path_info = os.stat(
                    name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )

            _validate_private_directory(path_info)
            descriptor = os.open(
                name,
                _directory_flags(),
                dir_fd=parent_descriptor,
            )
        except ValueError:
            raise
        except (OSError, NotImplementedError, TypeError) as error:
            raise _error("managed hierarchy is unsafe") from error

        try:
            descriptor_info = os.fstat(descriptor)
            if not _same_inode(path_info, descriptor_info):
                raise _error("managed hierarchy changed during validation")
            if created_info is not None and not _same_inode(
                created_info,
                descriptor_info,
            ):
                raise _error("created directory changed during validation")
            if created:
                os.fchmod(descriptor, 0o700)
                descriptor_info = os.fstat(descriptor)
                path_info = os.stat(
                    name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                if not _same_inode(path_info, descriptor_info):
                    raise _error("created directory changed during validation")
            _validate_private_directory(descriptor_info)
            os.fsync(descriptor)
            os.fsync(parent_descriptor)
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    @staticmethod
    def _open_existing_private_child(
        parent_descriptor: int,
        name: str,
    ) -> int:
        """Open one existing managed directory without creating or repairing it."""

        try:
            path_info = os.stat(
                name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            _validate_private_directory(path_info)
            descriptor = os.open(
                name,
                _directory_flags(),
                dir_fd=parent_descriptor,
            )
        except ValueError:
            raise
        except (OSError, NotImplementedError, TypeError) as error:
            raise _error("managed hierarchy is unsafe") from error
        try:
            descriptor_info = os.fstat(descriptor)
            if not _same_inode(path_info, descriptor_info):
                raise _error("managed hierarchy changed during validation")
            _validate_private_directory(descriptor_info)
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def _secure_root(self) -> None:
        self._require_posix()
        try:
            anchor, parts = self._hierarchy_anchor()
            anchor_info = anchor.lstat()
            _validate_private_directory(anchor_info)
            descriptor = os.open(anchor, _directory_flags())
        except ValueError:
            raise
        except (OSError, NotImplementedError, TypeError) as error:
            raise _error("managed hierarchy is unsafe") from error

        try:
            descriptor_info = os.fstat(descriptor)
            if not _same_inode(anchor_info, descriptor_info):
                raise _error("managed hierarchy changed during validation")
            _validate_private_directory(descriptor_info)
            for name in parts:
                child = self._open_private_child(descriptor, name)
                os.close(descriptor)
                descriptor = child
            if not _same_inode(self.root.lstat(), os.fstat(descriptor)):
                raise _error("store root changed during validation")
        finally:
            os.close(descriptor)

    def _secure_existing_root(self) -> None:
        """Validate the full managed hierarchy without creating any component."""

        self._require_posix()
        try:
            anchor, parts = self._hierarchy_anchor()
            anchor_info = anchor.lstat()
            _validate_private_directory(anchor_info)
            descriptor = os.open(anchor, _directory_flags())
        except ValueError:
            raise
        except (OSError, NotImplementedError, TypeError) as error:
            raise _error("managed hierarchy is unsafe") from error
        try:
            descriptor_info = os.fstat(descriptor)
            if not _same_inode(anchor_info, descriptor_info):
                raise _error("managed hierarchy changed during validation")
            _validate_private_directory(descriptor_info)
            for name in parts:
                child = self._open_existing_private_child(
                    descriptor,
                    name,
                )
                os.close(descriptor)
                descriptor = child
            if not _same_inode(self.root.lstat(), os.fstat(descriptor)):
                raise _error("store root changed during validation")
        finally:
            os.close(descriptor)

    @staticmethod
    def _require_posix() -> None:
        required_functions = (
            "dup",
            "fchmod",
            "fsync",
            "fstat",
            "listdir",
            "mkdir",
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
            and os.mkdir in supports_dir_fd
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

    def _verified_manifest_descriptor(
        self,
        generation_id: str,
    ) -> VerifiedManifestDescriptor:
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
                manifest_bytes = _read_file_at(
                    generation_descriptor,
                    "manifest.json",
                )
                manifest = json.loads(manifest_bytes)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise _error("published manifest is unreadable") from exc
            if not isinstance(manifest, dict):
                raise _error("published manifest is invalid")
            if canonical_json_bytes(manifest) != manifest_bytes:
                raise _error("published manifest is not canonical")
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
            generation = PublishedGeneration(
                generation_id=generation_id,
                root=root,
                manifest=MappingProxyType(manifest),
            )
            return VerifiedManifestDescriptor(
                generation=generation,
                manifest_bytes=manifest_bytes,
                manifest_digest=hashlib.sha256(manifest_bytes).hexdigest(),
            )
        except OSError as exc:
            raise _error("published content is unreadable") from exc
        finally:
            os.close(generation_descriptor)
            os.close(store_descriptor)

    def _published(self, generation_id: str) -> PublishedGeneration:
        return self._verified_manifest_descriptor(generation_id).generation

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

    def observe_existing_generation(
        self,
        generation_id: str,
    ) -> ExistingGenerationProof:
        """Return bounded typed proof evidence without creating store state."""

        try:
            require_digest(generation_id)
        except ValueError:
            return _failed_proof(
                "generation_unsafe",
                {"generation_id": "invalid"},
            )
        identity_material = {"generation_id": generation_id}
        try:
            self._secure_existing_root()
            store_descriptor = os.open(self.root, _directory_flags())
        except (OSError, ValueError, TypeError, NotImplementedError):
            return _failed_proof("store_unavailable", identity_material)

        generation_descriptor: int | None = None
        try:
            try:
                linked = os.stat(
                    generation_id,
                    dir_fd=store_descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                return _failed_proof(
                    "generation_missing",
                    identity_material,
                )
            except OSError:
                return _failed_proof(
                    "generation_unsafe",
                    identity_material,
                )
            if not stat.S_ISDIR(linked.st_mode):
                return _failed_proof(
                    "generation_unsafe",
                    {
                        **identity_material,
                        "kind": stat.S_IFMT(linked.st_mode),
                    },
                )
            try:
                generation_descriptor = os.open(
                    generation_id,
                    _directory_flags(),
                    dir_fd=store_descriptor,
                )
            except OSError:
                return _failed_proof(
                    "generation_unsafe",
                    identity_material,
                )
            opened = os.fstat(generation_descriptor)
            if not _same_inode(linked, opened):
                return _failed_proof(
                    "proof_changed",
                    identity_material,
                )

            manifest_result = _read_bounded_manifest(
                generation_descriptor
            )
            if isinstance(manifest_result, ExistingGenerationProof):
                return manifest_result
            manifest_bytes, _ = manifest_result
            manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
            try:
                manifest = json.loads(manifest_bytes)
            except (UnicodeDecodeError, json.JSONDecodeError):
                return _failed_proof(
                    "manifest_malformed",
                    {"manifest_digest": manifest_digest},
                )
            if not isinstance(manifest, dict):
                return _failed_proof(
                    "manifest_invalid",
                    {"manifest_digest": manifest_digest},
                )
            try:
                canonical = canonical_json_bytes(manifest)
            except (TypeError, ValueError):
                return _failed_proof(
                    "manifest_invalid",
                    {"manifest_digest": manifest_digest},
                )
            if canonical != manifest_bytes:
                return _failed_proof(
                    "manifest_noncanonical",
                    {"manifest_digest": manifest_digest},
                )

            structural = dict(manifest)
            structural.pop("generation_id", None)
            try:
                validate_manifest(structural)
            except ValueError:
                return _failed_proof(
                    "manifest_invalid",
                    {"manifest_digest": manifest_digest},
                )
            if (
                manifest.get("generation_id") != generation_id
                or generation_id_for(manifest) != generation_id
            ):
                return _failed_proof(
                    "manifest_identity_mismatch",
                    {"manifest_digest": manifest_digest},
                )

            tree_result = _bounded_tree_evidence(
                generation_descriptor,
                initial_bytes=len(manifest_bytes),
            )
            if isinstance(tree_result, ExistingGenerationProof):
                return tree_result
            tree_digest, total_bytes = tree_result
            combined_material = {
                "manifest_digest": manifest_digest,
                "tree_digest": tree_digest,
                "total_bytes": total_bytes,
            }
            try:
                _validate_readonly_tree_at(generation_descriptor)
            except ValueError:
                return _failed_proof(
                    "published_tree_unsafe",
                    combined_material,
                )
            try:
                _validate_manifest_at_fd(
                    manifest,
                    generation_descriptor,
                    published=True,
                )
            except ValueError:
                return _failed_proof(
                    "published_content_mismatch",
                    combined_material,
                )

            current_generation = os.stat(
                generation_id,
                dir_fd=store_descriptor,
                follow_symlinks=False,
            )
            if (
                not _same_inode(opened, current_generation)
                or not _same_inode(
                    opened,
                    (self.root / generation_id).lstat(),
                )
                or not _same_inode(
                    os.fstat(store_descriptor),
                    self.root.lstat(),
                )
            ):
                return _failed_proof(
                    "proof_changed",
                    combined_material,
                )
            generation = PublishedGeneration(
                generation_id=generation_id,
                root=self.root / generation_id,
                manifest=MappingProxyType(manifest),
            )
            descriptor = VerifiedManifestDescriptor(
                generation=generation,
                manifest_bytes=manifest_bytes,
                manifest_digest=manifest_digest,
            )
            return ExistingGenerationProof(
                descriptor=descriptor,
                failure_code=None,
                evidence_digest=_proof_evidence_digest(
                    "proven",
                    combined_material,
                ),
            )
        except (OSError, TypeError, NotImplementedError):
            return _failed_proof(
                "proof_changed",
                identity_material,
            )
        finally:
            if generation_descriptor is not None:
                os.close(generation_descriptor)
            os.close(store_descriptor)

    def verify(self, generation_id: str) -> PublishedGeneration:
        self._secure_root()
        require_digest(generation_id)
        return self._published(generation_id)

    def verified_manifest_descriptor(
        self,
        generation_id: str,
    ) -> VerifiedManifestDescriptor:
        """Verify one generation and return its exact canonical manifest bytes."""

        self._secure_root()
        require_digest(generation_id)
        return self._verified_manifest_descriptor(generation_id)

    def verified_manifest_descriptor_existing(
        self,
        generation_id: str,
    ) -> VerifiedManifestDescriptor:
        """Verify an existing generation without creating managed directories."""

        self._secure_existing_root()
        require_digest(generation_id)
        return self._verified_manifest_descriptor(generation_id)

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
