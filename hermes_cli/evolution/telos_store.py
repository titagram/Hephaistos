"""Telos storage and pointer-management — no host-authorised transition methods."""
from __future__ import annotations

import json
import os
import secrets
import stat
from pathlib import Path
from typing import Any, Callable

from .organism_home import ensure_organism_directories
from .telos_contract import TelosRevision, telos_revision_from_dict, validate_telos_revision


class TelosStoreError(Exception):
    """Raised when Telos storage, activation, or rollback operations fail."""


_JsonReader = Callable[[Path], dict[str, Any] | None]
_MAX_TELOS_FILE_BYTES = 1024 * 1024


def _same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _validate_directory(info: os.stat_result) -> None:
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
        or (
            hasattr(os, "geteuid")
            and info.st_uid != os.geteuid()
        )
        or (
            os.name == "posix"
            and stat.S_IMODE(info.st_mode) != 0o700
        )
    ):
        raise TelosStoreError("telos_unsafe_path")


def _validate_file(info: os.stat_result, *, strict_mode: bool) -> None:
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or (
            hasattr(os, "geteuid")
            and info.st_uid != os.geteuid()
        )
        or (
            strict_mode
            and os.name == "posix"
            and stat.S_IMODE(info.st_mode) != 0o600
        )
    ):
        raise TelosStoreError("telos_unsafe_path")


def _require_atomic_anchoring() -> None:
    """Require the no-follow, dir-fd primitives needed for safe publication.

    A path-validation fallback cannot make a mutable Telos publication safe on
    Windows because parent directories can be replaced between checks.  Refuse
    to mutate rather than provide a best-effort write there.
    """
    supported = getattr(os, "supports_dir_fd", frozenset())
    if not (
        os.name == "posix"
        and hasattr(os, "O_DIRECTORY")
        and hasattr(os, "O_NOFOLLOW")
        and os.open in supported
        and os.stat in supported
        and os.rename in supported
        and os.link in supported
        and all(hasattr(os, name) for name in ("fchmod", "fsync", "fstat"))
    ):
        raise TelosStoreError("telos_atomic_anchoring_unavailable")


def _write_all(descriptor: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise TelosStoreError("telos_write_failed")
        view = view[written:]


class _TelosMutation:
    """Retained descriptor set for one Telos mutation, not an authority API."""

    def __init__(self, store: "TelosStore") -> None:
        _require_atomic_anchoring()
        self.store = store
        self.root_descriptor: int | None = None
        self.telos_descriptor: int | None = None
        self.revisions_descriptor: int | None = None
        self._open()

    def _open_directory(
        self,
        parent_descriptor: int | None,
        path: Path,
        name: str | None,
    ) -> int:
        try:
            if parent_descriptor is None:
                linked = path.lstat()
                _validate_directory(linked)
                descriptor = os.open(
                    path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                )
            else:
                if name is None:
                    raise TelosStoreError("telos_unsafe_path")
                linked = os.stat(
                    name, dir_fd=parent_descriptor, follow_symlinks=False
                )
                _validate_directory(linked)
                descriptor = os.open(
                    name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=parent_descriptor,
                )
            opened = os.fstat(descriptor)
            _validate_directory(opened)
            if not _same_inode(linked, opened):
                raise TelosStoreError("telos_unsafe_path")
            if parent_descriptor is None:
                relinked = path.lstat()
            else:
                relinked = os.stat(
                    name, dir_fd=parent_descriptor, follow_symlinks=False
                )
            _validate_directory(relinked)
            if not _same_inode(opened, relinked):
                raise TelosStoreError("telos_unsafe_path")
            return descriptor
        except TelosStoreError:
            if "descriptor" in locals():
                os.close(descriptor)
            raise
        except (OSError, TypeError, NotImplementedError, AttributeError) as exc:
            if "descriptor" in locals():
                os.close(descriptor)
            raise TelosStoreError("telos_unsafe_path") from exc

    def _open(self) -> None:
        try:
            self.root_descriptor = self._open_directory(
                None, self.store.organism_root, None
            )
            self.telos_descriptor = self._open_directory(
                self.root_descriptor, self.store.telos_dir, "telos"
            )
            self.revisions_descriptor = self._open_directory(
                self.telos_descriptor, self.store.revisions_dir, "revisions"
            )
            self._verify_links()
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        for attribute in (
            "revisions_descriptor",
            "telos_descriptor",
            "root_descriptor",
        ):
            descriptor = getattr(self, attribute)
            if descriptor is not None:
                try:
                    os.close(descriptor)
                finally:
                    setattr(self, attribute, None)

    def __enter__(self) -> "_TelosMutation":
        return self

    def __exit__(self, *unused: object) -> None:
        self.close()

    def _verify_links(self) -> None:
        if (
            self.root_descriptor is None
            or self.telos_descriptor is None
            or self.revisions_descriptor is None
        ):
            raise TelosStoreError("telos_unsafe_path")
        try:
            root_link = self.store.organism_root.lstat()
            telos_link = os.stat(
                "telos", dir_fd=self.root_descriptor, follow_symlinks=False
            )
            revisions_link = os.stat(
                "revisions", dir_fd=self.telos_descriptor, follow_symlinks=False
            )
            for linked, descriptor in (
                (root_link, self.root_descriptor),
                (telos_link, self.telos_descriptor),
                (revisions_link, self.revisions_descriptor),
            ):
                _validate_directory(linked)
                opened = os.fstat(descriptor)
                _validate_directory(opened)
                if not _same_inode(linked, opened):
                    raise TelosStoreError("telos_unsafe_path")
        except TelosStoreError:
            raise
        except (OSError, TypeError, NotImplementedError) as exc:
            raise TelosStoreError("telos_unsafe_path") from exc

    def _descriptor(self, name: str) -> int:
        descriptor = getattr(self, name)
        if descriptor is None:
            raise TelosStoreError("telos_unsafe_path")
        return descriptor

    @staticmethod
    def _read_descriptor(descriptor: int) -> bytes:
        chunks: list[bytes] = []
        size = 0
        while chunk := os.read(descriptor, 64 * 1024):
            size += len(chunk)
            if size > _MAX_TELOS_FILE_BYTES:
                raise TelosStoreError("telos_file_too_large")
            chunks.append(chunk)
        return b"".join(chunks)

    def _read_named(self, name: str, *, directory: int) -> bytes | None:
        descriptor: int | None = None
        try:
            try:
                linked = os.stat(name, dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                return None
            _validate_file(linked, strict_mode=False)
            descriptor = os.open(
                name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory
            )
            opened = os.fstat(descriptor)
            _validate_file(opened, strict_mode=False)
            relinked = os.stat(name, dir_fd=directory, follow_symlinks=False)
            _validate_file(relinked, strict_mode=False)
            if not _same_inode(linked, opened) or not _same_inode(opened, relinked):
                raise TelosStoreError("telos_unsafe_path")
            data = self._read_descriptor(descriptor)
            self._verify_links()
            return data
        except TelosStoreError:
            raise
        except (OSError, TypeError, NotImplementedError) as exc:
            raise TelosStoreError("telos_unsafe_path") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def active_digest(self) -> str | None:
        data = self._read_named(
            "active.json", directory=self._descriptor("telos_descriptor")
        )
        if data is None:
            return None
        try:
            document = json.loads(data)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TelosStoreError("telos_active_pointer_invalid") from exc
        if not isinstance(document, dict):
            raise TelosStoreError("telos_active_pointer_invalid")
        digest = document.get("digest")
        return digest if isinstance(digest, str) else ""

    def revision(self, digest: str) -> TelosRevision:
        data = self._read_named(
            f"{digest}.json", directory=self._descriptor("revisions_descriptor")
        )
        if data is None:
            raise TelosStoreError(f"Telos revision not found for digest: {digest}")
        try:
            document = json.loads(data)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TelosStoreError("telos_revision_invalid") from exc
        if not isinstance(document, dict):
            raise TelosStoreError("telos_revision_invalid")
        return telos_revision_from_dict(document)

    def save_revision(self, revision: TelosRevision) -> None:
        revisions_descriptor = self._descriptor("revisions_descriptor")
        name = f"{revision.canonical_digest}.json"
        data = revision.to_canonical_json().encode("utf-8")
        existing = self._read_named(name, directory=revisions_descriptor)
        if existing is not None:
            if existing != data:
                raise TelosStoreError("telos_revision_content_conflict")
            return

        temporary_name: str | None = None
        temporary_descriptor: int | None = None
        try:
            self._verify_links()
            for _ in range(16):
                candidate = f".{name}.{secrets.token_hex(16)}"
                try:
                    temporary_descriptor = os.open(
                        candidate,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600,
                        dir_fd=revisions_descriptor,
                    )
                except FileExistsError:
                    continue
                temporary_name = candidate
                break
            if temporary_descriptor is None or temporary_name is None:
                raise TelosStoreError("telos_write_failed")
            os.fchmod(temporary_descriptor, 0o600)
            _validate_file(os.fstat(temporary_descriptor), strict_mode=True)
            _write_all(temporary_descriptor, data)
            os.fsync(temporary_descriptor)
            os.close(temporary_descriptor)
            temporary_descriptor = None
            self._verify_links()
            try:
                os.link(
                    temporary_name,
                    name,
                    src_dir_fd=revisions_descriptor,
                    dst_dir_fd=revisions_descriptor,
                    follow_symlinks=False,
                )
            except FileExistsError:
                existing = self._read_named(name, directory=revisions_descriptor)
                if existing != data:
                    raise TelosStoreError("telos_revision_content_conflict")
            os.unlink(temporary_name, dir_fd=revisions_descriptor)
            temporary_name = None
            os.fsync(revisions_descriptor)
        except TelosStoreError:
            raise
        except (OSError, TypeError, NotImplementedError) as exc:
            raise TelosStoreError("telos_write_failed") from exc
        finally:
            if temporary_descriptor is not None:
                os.close(temporary_descriptor)
            if temporary_name is not None:
                try:
                    os.unlink(temporary_name, dir_fd=revisions_descriptor)
                except OSError:
                    pass

    def _replace_telos_document(self, name: str, document: dict[str, Any]) -> None:
        telos_descriptor = self._descriptor("telos_descriptor")
        data = json.dumps(document, sort_keys=True).encode("utf-8")
        temporary_name: str | None = None
        temporary_descriptor: int | None = None
        try:
            existing = self._read_named(name, directory=telos_descriptor)
            if existing is not None:
                self._verify_links()
            for _ in range(16):
                candidate = f".{name}.{secrets.token_hex(16)}"
                try:
                    temporary_descriptor = os.open(
                        candidate,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600,
                        dir_fd=telos_descriptor,
                    )
                except FileExistsError:
                    continue
                temporary_name = candidate
                break
            if temporary_descriptor is None or temporary_name is None:
                raise TelosStoreError("telos_write_failed")
            os.fchmod(temporary_descriptor, 0o600)
            _validate_file(os.fstat(temporary_descriptor), strict_mode=True)
            _write_all(temporary_descriptor, data)
            os.fsync(temporary_descriptor)
            os.close(temporary_descriptor)
            temporary_descriptor = None
            self._verify_links()
            os.rename(
                temporary_name,
                name,
                src_dir_fd=telos_descriptor,
                dst_dir_fd=telos_descriptor,
            )
            temporary_name = None
            os.fsync(telos_descriptor)
        except TelosStoreError:
            raise
        except (OSError, TypeError, NotImplementedError) as exc:
            raise TelosStoreError("telos_write_failed") from exc
        finally:
            if temporary_descriptor is not None:
                os.close(temporary_descriptor)
            if temporary_name is not None:
                try:
                    os.unlink(temporary_name, dir_fd=telos_descriptor)
                except OSError:
                    pass

    def transition(self, *, digest: str, grant_id: str, action: str, now: str) -> None:
        telos_descriptor = self._descriptor("telos_descriptor")
        if action == "activate":
            current_data = self._read_named("active.json", directory=telos_descriptor)
            if current_data is not None:
                try:
                    current = json.loads(current_data)
                    previous_digest = current["digest"]
                except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise TelosStoreError("telos_active_pointer_invalid") from exc
                if not isinstance(previous_digest, str):
                    raise TelosStoreError("telos_active_pointer_invalid")
                self._replace_telos_document(
                    "last-known-good.json", {"digest": previous_digest}
                )
            self._replace_telos_document(
                "active.json",
                {"digest": digest, "activated_at": now, "grant_id": grant_id},
            )
        elif action == "rollback":
            self._replace_telos_document(
                "active.json",
                {
                    "digest": digest,
                    "activated_at": now,
                    "grant_id": grant_id,
                    "rollback": True,
                },
            )
        else:
            raise TelosStoreError("telos_invalid_transition")


class TelosStore:
    def __init__(self, organism_root: Path | None = None) -> None:
        self.organism_root = ensure_organism_directories(organism_root)
        self.telos_dir = self.organism_root / "telos"
        self.revisions_dir = self.telos_dir / "revisions"
        self.active_pointer = self.telos_dir / "active.json"
        self.lkg_pointer = self.telos_dir / "last-known-good.json"

    @classmethod
    def from_verified_read_root(cls, organism_root: Path) -> "TelosStore":
        """Bind a store to a caller-verified existing root without mutation.

        Dashboard readers and stale-request preflights use this factory so they
        never create Telos directories before every requested condition is
        proven current.
        """
        store = cls.__new__(cls)
        store.organism_root = Path(organism_root)
        store.telos_dir = store.organism_root / "telos"
        store.revisions_dir = store.telos_dir / "revisions"
        store.active_pointer = store.telos_dir / "active.json"
        store.lkg_pointer = store.telos_dir / "last-known-good.json"
        return store

    def open_mutation(self) -> _TelosMutation:
        """Open retained descriptors for a caller that already owns authority."""
        return _TelosMutation(self)

    def save_revision(self, revision: TelosRevision) -> Path:
        validate_telos_revision(revision)
        digest = revision.canonical_digest
        with self.open_mutation() as mutation:
            mutation.save_revision(revision)
        return self.telos_dir / "revisions" / f"{digest}.json"

    def get_revision(
        self, digest: str, *, read_json: _JsonReader | None = None
    ) -> TelosRevision:
        path = self.revisions_dir / f"{digest}.json"
        if read_json is not None:
            data = read_json(path)
        else:
            if not path.exists():
                raise TelosStoreError(f"Telos revision not found for digest: {digest}")
            data = json.loads(path.read_text(encoding="utf-8"))
        if data is None:
            raise TelosStoreError(f"Telos revision not found for digest: {digest}")
        return telos_revision_from_dict(data)

    def activate_revision(
        self,
        digest: str,
        receipt_id: str = "",
        *,
        grant_id: str | None = None,
    ) -> None:
        """Public activation — always fails closed."""
        raise TelosStoreError("host_approval_not_implemented")

    def get_active_digest(self, *, read_json: _JsonReader | None = None) -> str | None:
        if read_json is not None:
            data = read_json(self.active_pointer)
            return None if data is None else str(data.get("digest", ""))
        if not self.active_pointer.exists():
            return None
        try:
            data = json.loads(self.active_pointer.read_text(encoding="utf-8"))
            return str(data.get("digest", ""))
        except Exception:
            return None

    def get_active_revision(self) -> TelosRevision | None:
        digest = self.get_active_digest()
        if not digest:
            return None
        return self.get_revision(digest)

    def rollback(
        self,
        target_digest: str,
        receipt_id: str = "",
        *,
        grant_id: str | None = None,
    ) -> None:
        """Public rollback — always fails closed for model callers."""
        raise TelosStoreError("host_approval_not_implemented")
