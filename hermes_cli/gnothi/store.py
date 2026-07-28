from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from stat import S_ISDIR, S_ISLNK, S_ISREG
from typing import Any

import hermes_constants
from hermes_cli.gnothi.contract import validate_artifact

POINTER_SCHEMA = "hades.gnothi_pointer.v1"
_SAFE_REVISION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def _encoded_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def legacy_profile_store_state() -> str:
    """Return the read-only legacy profile pointer state."""
    legacy = hermes_constants.get_hermes_home() / "gnothi_seauton"
    canonical = hermes_constants.get_organism_home() / "gnothi_seauton"
    if legacy.absolute() == canonical.absolute():
        return "absent"
    pointer = legacy / "current.json"
    try:
        mode = pointer.lstat().st_mode
    except FileNotFoundError:
        return "absent"
    except OSError:
        return "unreadable"
    if S_ISLNK(mode) or not S_ISREG(mode):
        return "unreadable"
    return "detected"


class OrganismRevisionStore:
    """Immutable local organism revisions with an atomic current pointer."""

    def __init__(self, root: Path | None = None) -> None:
        self.organism_root = (
            hermes_constants.get_organism_home() if root is None else None
        )
        self.root = (
            root
            if root is not None
            else self.organism_root / "gnothi_seauton"
        )
        self.revisions_dir = self.root / "revisions"
        self.current_path = self.root / "current.json"

    @staticmethod
    def _validate_revision_id(revision_id: object) -> str:
        value = str(revision_id or "")
        if not _SAFE_REVISION_ID.fullmatch(value):
            raise ValueError(f"unsafe revision id: {value!r}")
        return value

    @staticmethod
    def _unsafe_path(label: str) -> ValueError:
        return ValueError(f"unsafe organism {label}")

    @classmethod
    def _path_stat(cls, path: Path, *, label: str) -> os.stat_result | None:
        try:
            return path.lstat()
        except FileNotFoundError:
            return None
        except OSError:
            raise cls._unsafe_path(label) from None

    @classmethod
    def _regular_file_stat(
        cls, path: Path, *, label: str
    ) -> os.stat_result | None:
        info = cls._path_stat(path, label=label)
        if info is None:
            return None
        if S_ISLNK(info.st_mode) or not S_ISREG(info.st_mode):
            raise cls._unsafe_path(label)
        return info

    @classmethod
    def _read_regular_bytes(cls, path: Path, *, label: str) -> bytes | None:
        expected = cls._regular_file_stat(path, label=label)
        if expected is None:
            return None
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags)
        except OSError:
            raise cls._unsafe_path(label) from None
        try:
            current = os.fstat(descriptor)
            if (
                not S_ISREG(current.st_mode)
                or (current.st_dev, current.st_ino)
                != (expected.st_dev, expected.st_ino)
            ):
                raise cls._unsafe_path(label)
            with os.fdopen(descriptor, "rb") as handle:
                descriptor = -1
                return handle.read()
        finally:
            if descriptor != -1:
                os.close(descriptor)

    @classmethod
    def _decode_json(cls, content: bytes, *, path: Path) -> dict[str, Any]:
        try:
            value = json.loads(content)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid JSON in {path}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"expected JSON object in {path}")
        return value

    def _root_exists_for_read(self) -> bool:
        info = self._path_stat(self.root, label="store root")
        if info is None:
            return False
        if S_ISLNK(info.st_mode) or not S_ISDIR(info.st_mode):
            raise self._unsafe_path("store root")
        return True

    def _revisions_exist_for_read(self) -> bool:
        if not self._root_exists_for_read():
            return False
        info = self._path_stat(self.revisions_dir, label="revisions directory")
        if info is None:
            return False
        if S_ISLNK(info.st_mode) or not S_ISDIR(info.st_mode):
            raise self._unsafe_path("revisions directory")
        return True

    @classmethod
    def _ensure_secure_directory(cls, path: Path, *, label: str) -> None:
        info = cls._path_stat(path, label=label)
        if info is None:
            try:
                path.mkdir(mode=0o700, parents=True, exist_ok=False)
            except FileExistsError:
                pass
            except OSError:
                raise cls._unsafe_path(label) from None
            info = cls._path_stat(path, label=label)
        if info is None or S_ISLNK(info.st_mode) or not S_ISDIR(info.st_mode):
            raise cls._unsafe_path(label)
        try:
            os.chmod(path, 0o700)
        except OSError:
            raise cls._unsafe_path(label) from None

    def _initialize_for_mutation(self) -> None:
        if self.organism_root is not None:
            self._ensure_secure_directory(
                self.organism_root, label="organism root"
            )
        self._ensure_secure_directory(self.root, label="store root")
        self._ensure_secure_directory(
            self.revisions_dir, label="revisions directory"
        )

    @classmethod
    def _write_atomic(cls, path: Path, content: bytes, *, label: str) -> None:
        cls._regular_file_stat(path, label=label)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, path)
            try:
                os.chmod(path, 0o600)
            except OSError:
                raise cls._unsafe_path(label) from None
        finally:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass

    def _read_pointer(self) -> dict[str, Any] | None:
        if not self._root_exists_for_read():
            return None
        content = self._read_regular_bytes(
            self.current_path, label="current pointer"
        )
        if content is None:
            return None
        pointer = self._decode_json(content, path=self.current_path)
        if pointer.get("schema") != POINTER_SCHEMA:
            raise ValueError("invalid organism current pointer")
        return pointer

    def publish(
        self,
        artifact: dict[str, Any],
        *,
        published_at: str | None = None,
    ) -> dict[str, Any]:
        errors = validate_artifact(artifact)
        if errors:
            raise ValueError(f"invalid organism artifact: {', '.join(errors)}")

        contract = artifact.get("organism_contract", {})
        revision_id = self._validate_revision_id(contract.get("revision_id"))
        content = _encoded_json(artifact)
        digest = _sha256(content)
        self._initialize_for_mutation()
        revision_path = self.revisions_dir / f"{revision_id}.json"

        revision_content = self._read_regular_bytes(revision_path, label="revision")
        if revision_content is not None:
            if revision_content != content:
                raise ValueError(f"conflicting revision: {revision_id}")
            current_pointer = self._read_pointer()
            if current_pointer and (
                current_pointer.get("revision_id") == revision_id
                and current_pointer.get("sha256") == digest
            ):
                return current_pointer
        else:
            self._write_atomic(revision_path, content, label="revision")

        pointer = {
            "schema": POINTER_SCHEMA,
            "revision_id": revision_id,
            "sha256": digest,
            "published_at": published_at or _utc_now(),
        }
        self._write_atomic(
            self.current_path,
            _encoded_json(pointer),
            label="current pointer",
        )
        return pointer

    def get(self, revision_id: str) -> dict[str, Any] | None:
        safe_revision_id = self._validate_revision_id(revision_id)
        if not self._revisions_exist_for_read():
            return None
        path = self.revisions_dir / f"{safe_revision_id}.json"
        content = self._read_regular_bytes(path, label="revision")
        if content is None:
            return None
        return self._decode_json(content, path=path)

    def current(self) -> dict[str, Any] | None:
        pointer = self._read_pointer()
        if pointer is None:
            return None
        revision_id = self._validate_revision_id(pointer.get("revision_id"))
        if not self._revisions_exist_for_read():
            raise ValueError(f"missing current organism revision: {revision_id}")
        path = self.revisions_dir / f"{revision_id}.json"
        content = self._read_regular_bytes(path, label="revision")
        if content is None:
            raise ValueError(f"missing current organism revision: {revision_id}")
        if _sha256(content) != pointer.get("sha256"):
            raise ValueError(f"current organism revision digest mismatch: {revision_id}")
        return self._decode_json(content, path=path)

    def list_revisions(self) -> list[dict[str, Any]]:
        if not self._revisions_exist_for_read():
            return []
        revisions = [
            self._decode_json(content, path=path)
            for path in self.revisions_dir.glob("*.json")
            if (content := self._read_regular_bytes(path, label="revision"))
            is not None
        ]

        def sort_key(artifact: dict[str, Any]) -> tuple[str, str]:
            contract = artifact.get("organism_contract", {})
            return (
                str(contract.get("collected_at") or ""),
                str(contract.get("revision_id") or ""),
            )

        return sorted(revisions, key=sort_key, reverse=True)

    def previous_healthy(self) -> dict[str, Any] | None:
        pointer = self._read_pointer()
        current_id = str(pointer.get("revision_id") or "") if pointer else ""
        for artifact in self.list_revisions():
            contract = artifact.get("organism_contract", {})
            if str(contract.get("revision_id") or "") == current_id:
                continue
            if contract.get("status") in {"current", "stale"}:
                return artifact
        return None
