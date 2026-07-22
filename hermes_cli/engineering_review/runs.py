"""Private, profile-local persistence for engineering review runs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import stat
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Mapping

from hermes_constants import get_hermes_home

from .bridge import bundle_path


RUN_SCHEMA_VERSION = 1
DEFAULT_RETENTION_RUNS = 30
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_EFFORTS = frozenset({"low", "medium", "high"})
_STATUSES = frozenset({"active", "complete", "cleanup_failed"})
_METADATA_NAME = "run.json"

RunStatus = Literal["active", "complete", "cleanup_failed"]
Effort = Literal["low", "medium", "high"]


class ReviewRunError(ValueError):
    """A review run's identity, metadata, or filesystem boundary is unsafe."""


def normalize_retention_runs(value: object) -> int:
    """Return a bounded-retention count, falling back for invalid values."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return DEFAULT_RETENTION_RUNS
    return value


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _validate_run_id(run_id: object) -> str:
    if not isinstance(run_id, str) or not _RUN_ID_RE.fullmatch(run_id):
        raise ReviewRunError("run id is invalid")
    return run_id


def _validate_session_id(session_id: object) -> str:
    if not isinstance(session_id, str) or not _SESSION_ID_RE.fullmatch(session_id):
        raise ReviewRunError("session id is invalid")
    return session_id


def _validate_effort(effort: object) -> Effort:
    if not isinstance(effort, str) or effort not in _EFFORTS:
        raise ReviewRunError("effort must be low, medium, or high")
    return effort  # type: ignore[return-value]


def _owned_by_current_user(path: Path, info: os.stat_result) -> None:
    if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
        raise ReviewRunError(f"{path} is not owned by the current user")


def _secure_directory(path: Path, *, create: bool = False) -> None:
    """Require a non-symlink, user-owned private directory."""
    if create:
        try:
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError as exc:
            raise ReviewRunError(f"could not create {path}") from exc
    try:
        info = path.lstat()
    except OSError as exc:
        raise ReviewRunError(f"could not inspect {path}") from exc
    if stat.S_ISLNK(info.st_mode):
        raise ReviewRunError(f"symlink is not allowed: {path}")
    if not stat.S_ISDIR(info.st_mode):
        raise ReviewRunError(f"expected directory: {path}")
    _owned_by_current_user(path, info)
    try:
        os.chmod(path, 0o700)
    except OSError as exc:
        raise ReviewRunError(f"could not secure {path}") from exc


def _secure_file(path: Path) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ReviewRunError(f"could not inspect {path}") from exc
    if stat.S_ISLNK(info.st_mode):
        raise ReviewRunError(f"symlink is not allowed: {path}")
    if not stat.S_ISREG(info.st_mode):
        raise ReviewRunError(f"expected regular file: {path}")
    _owned_by_current_user(path, info)
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise ReviewRunError(f"artifact is not private: {path}")


def _read_private_file(path: Path) -> bytes:
    _secure_file(path)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ReviewRunError(f"could not open {path}") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ReviewRunError(f"expected regular file: {path}")
        _owned_by_current_user(path, info)
        chunks: list[bytes] = []
        remaining = info.st_size
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                raise ReviewRunError(f"could not fully read {path}")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _canonical_home(home: Path | None = None) -> Path:
    raw_home = get_hermes_home() if home is None else home
    try:
        return Path(raw_home).expanduser().resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ReviewRunError("Hermes home could not be resolved") from exc


def _review_root(home: Path) -> Path:
    root = home / "reviews"
    _secure_directory(root, create=True)
    return root


def _session_root(home: Path, session_id: str, *, create: bool) -> Path:
    reviews = _review_root(home)
    root = reviews / _validate_session_id(session_id)
    _secure_directory(root, create=create)
    try:
        root.relative_to(reviews)
    except ValueError as exc:  # defensive; ids are already path components.
        raise ReviewRunError("session root escapes reviews directory") from exc
    return root


def _sha256_file(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
    except OSError:
        return None


def _provenance_hash(workspace: Path, target: str, effort: str) -> str:
    value = json.dumps(
        {"effort": effort, "target": target, "workspace": str(workspace)},
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _artifact_name(name: object) -> str:
    if not isinstance(name, str) or not name or name in {".", "..", _METADATA_NAME}:
        raise ReviewRunError("artifact name is invalid")
    candidate = Path(name)
    if candidate.is_absolute() or len(candidate.parts) != 1:
        raise ReviewRunError("artifact name must be a single filename")
    return name


@dataclass(frozen=True, slots=True)
class ReviewRun:
    """The validated on-disk identity for one engineering review invocation."""

    run_id: str
    root: Path
    workspace: Path
    target: str
    effort: Effort
    session_id: str
    status: RunStatus = "active"

    @classmethod
    def create(
        cls,
        workspace: Path | str,
        *,
        target: str,
        effort: Effort,
        session_id: str,
    ) -> ReviewRun:
        if not isinstance(target, str) or not target:
            raise ReviewRunError("target must be a non-empty string")
        effort = _validate_effort(effort)
        session_id = _validate_session_id(session_id)
        try:
            canonical_workspace = Path(workspace).expanduser().resolve(strict=False)
        except (TypeError, OSError, RuntimeError) as exc:
            raise ReviewRunError("workspace could not be resolved") from exc

        home = _canonical_home()
        session_root = _session_root(home, session_id, create=True)
        for _ in range(10):
            run_id = secrets.token_urlsafe(18)
            root = session_root / run_id
            try:
                root.mkdir(mode=0o700)
            except FileExistsError:
                continue
            except OSError as exc:
                raise ReviewRunError("could not create review run") from exc
            _secure_directory(root)
            created_at = _now()
            metadata = {
                "schema_version": RUN_SCHEMA_VERSION,
                "run_id": run_id,
                "workspace": str(canonical_workspace),
                "target": target,
                "effort": effort,
                "session_id": session_id,
                "created_at": created_at,
                "updated_at": created_at,
                "completed_at": None,
                "status": "active",
                "bundle_hash": _sha256_file(bundle_path()),
                "provenance_hash": _provenance_hash(canonical_workspace, target, effort),
            }
            run = cls(run_id, root, canonical_workspace, target, effort, session_id)
            run._write_metadata(metadata)
            return run
        raise ReviewRunError("could not allocate a unique review run id")

    @classmethod
    def load(cls, run_id: str, session_id: str) -> ReviewRun:
        run_id = _validate_run_id(run_id)
        session_id = _validate_session_id(session_id)
        home = _canonical_home()
        session_root = _session_root(home, session_id, create=False)
        root = session_root / run_id
        _secure_directory(root)
        try:
            root.relative_to(session_root)
        except ValueError as exc:
            raise ReviewRunError("run root escapes session directory") from exc
        return cls._from_metadata(root, run_id=run_id, session_id=session_id)

    @classmethod
    def _from_metadata(cls, root: Path, *, run_id: str, session_id: str) -> ReviewRun:
        try:
            metadata = json.loads(_read_private_file(root / _METADATA_NAME))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ReviewRunError("run metadata is invalid JSON") from exc
        if not isinstance(metadata, Mapping):
            raise ReviewRunError("run metadata must be an object")
        if metadata.get("schema_version") != RUN_SCHEMA_VERSION:
            raise ReviewRunError("unknown run metadata schema")
        if metadata.get("run_id") != run_id:
            raise ReviewRunError("run id does not match metadata")
        if metadata.get("session_id") != session_id:
            raise ReviewRunError("session id does not match metadata")
        status = metadata.get("status")
        if not isinstance(status, str) or status not in _STATUSES:
            raise ReviewRunError("unknown review run status")
        target = metadata.get("target")
        if not isinstance(target, str) or not target:
            raise ReviewRunError("run target is invalid")
        effort = _validate_effort(metadata.get("effort"))
        workspace_value = metadata.get("workspace")
        if not isinstance(workspace_value, str):
            raise ReviewRunError("run workspace is invalid")
        try:
            workspace = Path(workspace_value).resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise ReviewRunError("run workspace could not be resolved") from exc
        if str(workspace) != workspace_value:
            raise ReviewRunError("run workspace is not canonical")
        for key in ("created_at", "updated_at"):
            if not isinstance(metadata.get(key), str):
                raise ReviewRunError(f"run {key} is invalid")
        completed_at = metadata.get("completed_at")
        if completed_at is not None and not isinstance(completed_at, str):
            raise ReviewRunError("run completed_at is invalid")
        if status in {"complete", "cleanup_failed"} and not isinstance(completed_at, str):
            raise ReviewRunError("terminal review run has no completion timestamp")
        if status == "active" and completed_at is not None:
            raise ReviewRunError("active review run has a completion timestamp")
        for key in ("bundle_hash", "provenance_hash"):
            value = metadata.get(key)
            if value is not None and (not isinstance(value, str) or len(value) != 64):
                raise ReviewRunError(f"run {key} is invalid")
        return cls(run_id, root, workspace, target, effort, session_id, status)

    def _write_metadata(self, metadata: Mapping[str, Any]) -> None:
        encoded = json.dumps(
            dict(metadata), allow_nan=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        self._atomic_write(_METADATA_NAME, encoded, allow_metadata=True)

    def _atomic_write(self, name: str, data: bytes, *, allow_metadata: bool = False) -> Path:
        if not allow_metadata:
            name = _artifact_name(name)
        _secure_directory(self.root)
        destination = self.root / name
        if destination.exists() or destination.is_symlink():
            _secure_file(destination)
        temporary = self.root / f".{name}.{secrets.token_urlsafe(12)}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(temporary, flags, 0o600)
            try:
                view = memoryview(data)
                while view:
                    written = os.write(descriptor, view)
                    if written <= 0:
                        raise OSError("short artifact write")
                    view = view[written:]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.chmod(temporary, 0o600)
            os.replace(temporary, destination)
            os.chmod(destination, 0o600)
            directory_descriptor = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise ReviewRunError(f"could not atomically write {name}") from exc
        return destination

    def atomic_artifact(self, name: str, data: bytes) -> Path:
        """Atomically persist a private artifact directly under this run root."""
        if not isinstance(data, bytes):
            raise TypeError("artifact data must be bytes")
        return self._atomic_write(_artifact_name(name), data)

    def mark_complete(self) -> ReviewRun:
        """Transition an active run to its terminal complete state."""
        loaded = self.load(self.run_id, self.session_id)
        if loaded.status == "complete":
            return loaded
        if loaded.status != "active":
            raise ReviewRunError("only active review runs can complete")
        metadata = loaded._metadata()
        now = _now()
        metadata["status"] = "complete"
        metadata["updated_at"] = now
        metadata["completed_at"] = now
        loaded._write_metadata(metadata)
        return replace(loaded, status="complete")

    def _metadata(self) -> dict[str, Any]:
        try:
            value = json.loads(_read_private_file(self.root / _METADATA_NAME))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ReviewRunError("run metadata is invalid JSON") from exc
        if not isinstance(value, dict):
            raise ReviewRunError("run metadata must be an object")
        return value


def prune_completed_runs(home: Path, keep: int) -> list[Path]:
    """Remove the oldest completed runs, never an active or invalid run."""
    keep = normalize_retention_runs(keep)
    canonical_home = _canonical_home(home)
    try:
        reviews = _review_root(canonical_home)
    except ReviewRunError:
        return []

    completed: list[tuple[str, Path]] = []
    try:
        session_paths = list(reviews.iterdir())
    except OSError:
        return []
    for session_root in session_paths:
        try:
            session_id = _validate_session_id(session_root.name)
            _secure_directory(session_root)
            run_paths = list(session_root.iterdir())
        except (OSError, ReviewRunError):
            continue
        for root in run_paths:
            try:
                run_id = _validate_run_id(root.name)
                _secure_directory(root)
                run = ReviewRun._from_metadata(root, run_id=run_id, session_id=session_id)
            except ReviewRunError:
                continue
            if run.status == "complete":
                metadata = run._metadata()
                completed.append((str(metadata["completed_at"]), root))

    completed.sort(key=lambda item: (item[0], item[1].name), reverse=True)
    removed: list[Path] = []
    for _, root in completed[keep:]:
        tombstone = root.with_name(f".pruning-{root.name}-{secrets.token_urlsafe(8)}")
        try:
            _secure_directory(root)
            run = ReviewRun._from_metadata(
                root, run_id=root.name, session_id=root.parent.name
            )
            if run.status != "complete":
                continue
            # Claim the completed directory before deleting it. ``replace`` is
            # atomic on the shared parent filesystem, so concurrent pruners can
            # never both remove (or report removal of) the same run.
            os.replace(root, tombstone)
            shutil.rmtree(tombstone)
        except FileNotFoundError:
            continue
        except (OSError, ReviewRunError):
            # A failed cleanup is a terminal state too: do not repeatedly risk
            # deleting an ambiguous directory on later retention passes.
            try:
                cleanup_root = tombstone if tombstone.exists() else root
                run = ReviewRun._from_metadata(
                    cleanup_root, run_id=root.name, session_id=root.parent.name
                )
                metadata = run._metadata()
                metadata["status"] = "cleanup_failed"
                metadata["updated_at"] = _now()
                run._write_metadata(metadata)
                if cleanup_root == tombstone:
                    os.replace(tombstone, root)
            except (OSError, ReviewRunError):
                pass
            continue
        removed.append(root)
    return removed
