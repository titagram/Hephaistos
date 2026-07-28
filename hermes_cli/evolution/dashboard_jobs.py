"""Persistent, bounded local jobs for the Evolution dashboard.

The manager deliberately accepts only a closed set of job kinds.  HTTP adapters
may select one of those kinds and pass its bounded data, but never a command or
callable to execute.  Durable records make polling safe across process restarts;
an interrupted process is reported as such rather than being mistaken for live
work.
"""

from __future__ import annotations

import json
import math
import os
import re
import stat
import tempfile
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterator, Literal

import hermes_constants
from agent.redact import redact_sensitive_text
from hermes_cli.gnothi.builder import build_organism_revision
from hermes_cli.gnothi.query import OrganismQuery
from hermes_cli.gnothi.store import OrganismRevisionStore

from .observer_service import ObserverService

try:  # pragma: no branch - platform dependent implementation
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

try:  # pragma: no branch - platform dependent implementation
    import msvcrt
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None  # type: ignore[assignment]


JobKind = Literal["organism_rebuild", "observer_scan", "revision_diff"]
JobState = Literal[
    "queued", "running", "completed", "failed", "cancelled", "unknown"
]

JOB_KINDS = frozenset({"organism_rebuild", "observer_scan", "revision_diff"})
JOB_STATES = frozenset(
    {"queued", "running", "completed", "failed", "cancelled", "unknown"}
)
_EXCLUSIVE_KINDS = frozenset({"organism_rebuild", "observer_scan"})
_PUBLIC_ERROR_CODES = frozenset(
    {
        "invalid_job_input",
        "job_failed",
        "job_interrupted",
        "job_not_cancellable",
        "job_not_active",
        "job_already_active",
        "process_interrupted",
    }
)
_MAX_RECORD_BYTES = 64 * 1024
MAX_JOB_RESULT_BYTES = 32 * 1024
_MAX_JOB_RESULT_ITEMS = 100
_MAX_PUBLIC_TEXT = 512
_MAX_JOB_RECORDS = 100
_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.ASCII,
)
_PROCESS_NONCE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", re.ASCII)
_REVISION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SERVER_PROCESS_NONCE = str(uuid.uuid4())


class EvolutionJobError(RuntimeError):
    """Base class carrying a stable public job error code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class EvolutionJobValidationError(EvolutionJobError):
    """A caller supplied a value outside the fixed local job contract."""


class EvolutionJobConflict(EvolutionJobError):
    """The requested state transition cannot be made safely."""


class EvolutionJobStorageError(EvolutionJobError):
    """The private durable-job directory cannot be safely used."""


@dataclass(frozen=True)
class EvolutionJob:
    job_id: str
    kind: JobKind
    state: JobState
    progress: int
    created_at: str
    started_at: str | None
    finished_at: str | None
    process_nonce: str
    result: dict[str, Any] | None
    error_code: str | None


@dataclass(frozen=True)
class _JobRequest:
    force: bool = False
    collector_names: tuple[str, ...] = ()
    left: str | None = None
    right: str | None = None


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _private_mode(info: os.stat_result, mode: int) -> bool:
    return os.name != "posix" or stat.S_IMODE(info.st_mode) == mode


def _safe_public_text(value: object, *, limit: int = _MAX_PUBLIC_TEXT) -> str:
    # Bound before redaction: an exception or plugin response must not make a
    # public-job result expensive merely because it is later truncated.
    raw = str(value if value is not None else "")[:limit]
    return redact_sensitive_text(raw, force=True, file_read=True)[:limit]


def _bounded_value(value: object, *, depth: int = 0) -> Any:
    """Keep worker output JSON-only, small, and safe for a local dashboard."""
    if depth >= 5:
        return "[truncated]"
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return _safe_public_text(value)
    if isinstance(value, (list, tuple)):
        return [_bounded_value(item, depth=depth + 1) for item in value[:_MAX_JOB_RESULT_ITEMS]]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key in sorted(value, key=lambda item: str(item))[:_MAX_JOB_RESULT_ITEMS]:
            if not isinstance(key, str):
                continue
            result[_safe_public_text(key, limit=128)] = _bounded_value(
                value[key], depth=depth + 1
            )
        return result
    return _safe_public_text(value)


def _fit_result(result: dict[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(result, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) <= MAX_JOB_RESULT_BYTES:
        return result
    return {"kind": result["kind"], "truncated": True}


def _is_bounded_public_value(value: object, *, depth: int = 0) -> bool:
    if depth > 5:
        return False
    if value is None or isinstance(value, bool) or isinstance(value, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, str):
        return len(value) <= _MAX_PUBLIC_TEXT
    if isinstance(value, list):
        return len(value) <= _MAX_JOB_RESULT_ITEMS and all(
            _is_bounded_public_value(item, depth=depth + 1) for item in value
        )
    if isinstance(value, dict):
        return (
            len(value) <= _MAX_JOB_RESULT_ITEMS
            and all(
                isinstance(key, str)
                and len(key) <= 128
                and _is_bounded_public_value(item, depth=depth + 1)
                for key, item in value.items()
            )
        )
    return False


def _validate_result_payload(kind: str, result: dict[str, Any]) -> None:
    """Reject storage records that are not one of the fixed public shapes."""
    if result.get("kind") != kind:
        raise EvolutionJobStorageError("job_record_invalid")
    if result.get("truncated") is True and set(result) == {"kind", "truncated"}:
        return
    if kind == "organism_rebuild":
        if set(result) != {"kind", "revision_id", "revision_digest", "build_result"}:
            raise EvolutionJobStorageError("job_record_invalid")
        if not all(
            isinstance(result[field], str) and len(result[field]) <= limit
            for field, limit in (
                ("revision_id", 128),
                ("revision_digest", 64),
                ("build_result", 32),
            )
        ):
            raise EvolutionJobStorageError("job_record_invalid")
        return
    if kind == "observer_scan":
        value = result.get("updated_suggestions")
        if (
            set(result) != {"kind", "updated_suggestions"}
            or type(value) is not int
            or not 0 <= value <= _MAX_JOB_RESULT_ITEMS
        ):
            raise EvolutionJobStorageError("job_record_invalid")
        return
    if kind == "revision_diff":
        if set(result) != {"kind", "left", "right", "diff"}:
            raise EvolutionJobStorageError("job_record_invalid")
        if (
            not isinstance(result["left"], str)
            or not isinstance(result["right"], str)
            or not _REVISION_ID.fullmatch(result["left"])
            or not _REVISION_ID.fullmatch(result["right"])
            or not isinstance(result["diff"], dict)
            or not _is_bounded_public_value(result["diff"])
        ):
            raise EvolutionJobStorageError("job_record_invalid")
        return
    raise EvolutionJobStorageError("job_record_invalid")


def _public_result(kind: str, result: dict[str, Any]) -> dict[str, Any]:
    """Redact durable payload text again before returning it to the dashboard."""
    _validate_result_payload(kind, result)
    if result.get("truncated") is True:
        return {"kind": kind, "truncated": True}
    if kind == "organism_rebuild":
        return {
            "kind": kind,
            "revision_id": _safe_public_text(result["revision_id"], limit=128),
            "revision_digest": _safe_public_text(result["revision_digest"], limit=64),
            "build_result": _safe_public_text(result["build_result"], limit=32),
        }
    if kind == "observer_scan":
        return {"kind": kind, "updated_suggestions": result["updated_suggestions"]}
    return _fit_result(
        {
            "kind": kind,
            "left": result["left"],
            "right": result["right"],
            "diff": _bounded_value(result["diff"]),
        }
    )


class EvolutionJobManager:
    """Run the fixed dashboard workload through a small durable local queue."""

    def __init__(
        self,
        organism_root: Path | None = None,
        *,
        workspace_root: Path | None = None,
        process_nonce: str | None = None,
    ) -> None:
        self.root = Path(
            hermes_constants.get_organism_home() if organism_root is None else organism_root
        )
        self.workspace_root = self._validated_workspace_root(
            Path.cwd() if workspace_root is None else Path(workspace_root)
        )
        # An explicit root makes the invariant visible: this is always the
        # cross-profile global store, never the active profile's storage.
        self.store = OrganismRevisionStore(self.root / "gnothi_seauton")
        self.process_nonce = self._validated_nonce(process_nonce or _SERVER_PROCESS_NONCE)
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="evolution-job")
        self._lock = threading.RLock()
        self._futures: dict[str, Future[None]] = {}
        self._requests: dict[str, _JobRequest] = {}

    @property
    def jobs_dir(self) -> Path:
        return self.root / "evolution" / "dashboard-jobs"

    @staticmethod
    def _validated_workspace_root(value: Path) -> Path:
        try:
            root = value.resolve(strict=True)
            info = root.lstat()
            git_info = (root / ".git").lstat()
        except (OSError, RuntimeError, ValueError):
            raise EvolutionJobValidationError("invalid_workspace_root") from None
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise EvolutionJobValidationError("invalid_workspace_root")
        # A worktree uses a regular .git file; a normal checkout uses a
        # directory.  Do not follow a symlink supplied through a browser path.
        if stat.S_ISLNK(git_info.st_mode) or not (
            stat.S_ISDIR(git_info.st_mode) or stat.S_ISREG(git_info.st_mode)
        ):
            raise EvolutionJobValidationError("invalid_workspace_root")
        return root

    @staticmethod
    def _validated_nonce(value: object) -> str:
        raw = str(value)
        if not _PROCESS_NONCE_PATTERN.fullmatch(raw):
            raise EvolutionJobValidationError("invalid_process_nonce")
        return raw

    @staticmethod
    def _validated_job_id(value: object) -> str:
        raw = str(value)
        if not _UUID.fullmatch(raw):
            raise EvolutionJobValidationError("invalid_job_id")
        return raw

    @staticmethod
    def _validate_private_directory(path: Path) -> os.stat_result:
        try:
            info = path.lstat()
        except (OSError, TypeError, NotImplementedError):
            raise EvolutionJobStorageError("job_storage_unsafe") from None
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISDIR(info.st_mode)
            or not _private_mode(info, 0o700)
        ):
            raise EvolutionJobStorageError("job_storage_unsafe")
        return info

    @staticmethod
    def _validate_private_file(path: Path) -> os.stat_result:
        try:
            info = path.lstat()
        except (OSError, TypeError, NotImplementedError):
            raise EvolutionJobStorageError("job_storage_unsafe") from None
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or not _private_mode(info, 0o600)
        ):
            raise EvolutionJobStorageError("job_storage_unsafe")
        return info

    @classmethod
    def _ensure_private_directory(cls, path: Path) -> os.stat_result:
        try:
            info = path.lstat()
        except FileNotFoundError:
            try:
                path.mkdir(mode=0o700)
            except FileExistsError:
                pass
            except (OSError, TypeError, NotImplementedError):
                raise EvolutionJobStorageError("job_storage_unsafe") from None
        except (OSError, TypeError, NotImplementedError):
            raise EvolutionJobStorageError("job_storage_unsafe") from None
        return cls._validate_private_directory(path)

    def _ensure_jobs_directory(self) -> Path:
        # Create only as part of submission or a worker state update.  Each
        # component is validated first so mkdir cannot quietly traverse a link.
        self._ensure_private_directory(self.root)
        self._ensure_private_directory(self.root / "evolution")
        self._ensure_private_directory(self.jobs_dir)
        return self.jobs_dir

    def _jobs_directory_for_read(self) -> Path | None:
        try:
            root_info = self.root.lstat()
        except FileNotFoundError:
            return None
        except (OSError, TypeError, NotImplementedError):
            raise EvolutionJobStorageError("job_storage_unsafe") from None
        if (
            stat.S_ISLNK(root_info.st_mode)
            or not stat.S_ISDIR(root_info.st_mode)
            or not _private_mode(root_info, 0o700)
        ):
            raise EvolutionJobStorageError("job_storage_unsafe")
        evolution = self.root / "evolution"
        try:
            evolution.lstat()
        except FileNotFoundError:
            return None
        self._validate_private_directory(evolution)
        try:
            self.jobs_dir.lstat()
        except FileNotFoundError:
            return None
        self._validate_private_directory(self.jobs_dir)
        return self.jobs_dir

    @staticmethod
    def _open_private_lock(path: Path, *, create: bool) -> int | None:
        try:
            existing = path.lstat()
        except FileNotFoundError:
            existing = None
        except OSError:
            raise EvolutionJobStorageError("job_storage_unsafe") from None
        flags = os.O_RDWR | getattr(os, "O_BINARY", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor: int | None = None
        try:
            if existing is None:
                if not create:
                    return None
                try:
                    descriptor = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)
                except FileExistsError:
                    existing = path.lstat()
                else:
                    os.fchmod(descriptor, 0o600)
            if descriptor is None:
                if existing is None:
                    existing = path.lstat()
                if (
                    stat.S_ISLNK(existing.st_mode)
                    or not stat.S_ISREG(existing.st_mode)
                    or existing.st_nlink != 1
                    or not _private_mode(existing, 0o600)
                ):
                    raise EvolutionJobStorageError("job_storage_unsafe")
                descriptor = os.open(path, flags)
            opened = os.fstat(descriptor)
            linked = path.lstat()
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or not _private_mode(opened, 0o600)
                or not _same_inode(opened, linked)
            ):
                raise EvolutionJobStorageError("job_storage_unsafe")
            return descriptor
        except BaseException:
            if descriptor is not None:
                os.close(descriptor)
            raise

    @staticmethod
    def _acquire_file_lock(descriptor: int) -> None:
        if fcntl is not None:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            return
        if msvcrt is not None:  # pragma: no cover - Windows
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b" ")
                os.fsync(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
            return
        raise EvolutionJobStorageError("job_lock_unavailable")

    @staticmethod
    def _release_file_lock(descriptor: int) -> None:
        if fcntl is not None:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        elif msvcrt is not None:  # pragma: no cover - Windows
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)

    @contextmanager
    def _storage_lock(self, *, create: bool) -> Iterator[Path | None]:
        with self._lock:
            directory = self._ensure_jobs_directory() if create else self._jobs_directory_for_read()
            if directory is None:
                yield None
                return
            descriptor = self._open_private_lock(directory / ".jobs.lock", create=create)
            if descriptor is None:
                # Existing records are atomically replaced, so a non-creating
                # reader can safely proceed if a legacy directory lacks a lock.
                yield directory
                return
            try:
                self._acquire_file_lock(descriptor)
                yield directory
            finally:
                self._release_file_lock(descriptor)
                os.close(descriptor)

    @contextmanager
    def _kind_lock(self, kind: JobKind) -> Iterator[None]:
        if kind not in _EXCLUSIVE_KINDS:
            yield
            return
        with self._storage_lock(create=False) as directory:
            if directory is None:
                raise EvolutionJobStorageError("job_storage_unsafe")
            descriptor = self._open_private_lock(directory / f".{kind}.lock", create=True)
            assert descriptor is not None
        try:
            self._acquire_file_lock(descriptor)
            yield
        finally:
            self._release_file_lock(descriptor)
            os.close(descriptor)

    @staticmethod
    def _record_path(directory: Path, job_id: str) -> Path:
        return directory / f"{job_id}.json"

    def _read_record(self, directory: Path, job_id: str) -> EvolutionJob | None:
        path = self._record_path(directory, job_id)
        try:
            expected = path.lstat()
        except FileNotFoundError:
            return None
        except OSError:
            raise EvolutionJobStorageError("job_storage_unsafe") from None
        self._validate_private_file(path)
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor: int | None = None
        try:
            descriptor = os.open(path, flags)
            opened = os.fstat(descriptor)
            if not _same_inode(expected, opened):
                raise EvolutionJobStorageError("job_storage_unsafe")
            data = os.read(descriptor, _MAX_RECORD_BYTES + 1)
            if len(data) > _MAX_RECORD_BYTES:
                raise EvolutionJobStorageError("job_record_invalid")
        except EvolutionJobError:
            raise
        except OSError:
            raise EvolutionJobStorageError("job_storage_unsafe") from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
        try:
            value = json.loads(data)
        except (TypeError, ValueError):
            raise EvolutionJobStorageError("job_record_invalid") from None
        return self._job_from_value(value, expected_id=job_id)

    @staticmethod
    def _job_from_value(value: object, *, expected_id: str) -> EvolutionJob:
        if not isinstance(value, dict) or set(value) != {
            "job_id",
            "kind",
            "state",
            "progress",
            "created_at",
            "started_at",
            "finished_at",
            "process_nonce",
            "result",
            "error_code",
        }:
            raise EvolutionJobStorageError("job_record_invalid")
        try:
            job_id = EvolutionJobManager._validated_job_id(value["job_id"])
            kind = value["kind"]
            state = value["state"]
            progress = value["progress"]
            nonce = EvolutionJobManager._validated_nonce(value["process_nonce"])
        except EvolutionJobValidationError:
            raise EvolutionJobStorageError("job_record_invalid") from None
        if (
            job_id != expected_id
            or kind not in JOB_KINDS
            or state not in JOB_STATES
            or type(progress) is not int
            or not 0 <= progress <= 100
        ):
            raise EvolutionJobStorageError("job_record_invalid")
        created_at = value["created_at"]
        started_at = value["started_at"]
        finished_at = value["finished_at"]
        error_code = value["error_code"]
        if (
            not isinstance(created_at, str)
            or (started_at is not None and not isinstance(started_at, str))
            or (finished_at is not None and not isinstance(finished_at, str))
            or (error_code is not None and error_code not in _PUBLIC_ERROR_CODES)
        ):
            raise EvolutionJobStorageError("job_record_invalid")
        result = value["result"]
        if result is not None:
            if not isinstance(result, dict):
                raise EvolutionJobStorageError("job_record_invalid")
            try:
                encoded = json.dumps(result, sort_keys=True, separators=(",", ":")).encode("utf-8")
            except (TypeError, ValueError):
                raise EvolutionJobStorageError("job_record_invalid") from None
            if len(encoded) > MAX_JOB_RESULT_BYTES:
                raise EvolutionJobStorageError("job_record_invalid")
            _validate_result_payload(kind, result)
        return EvolutionJob(
            job_id=job_id,
            kind=kind,
            state=state,
            progress=progress,
            created_at=created_at,
            started_at=started_at,
            finished_at=finished_at,
            process_nonce=nonce,
            result=result,
            error_code=error_code,
        )

    @staticmethod
    def _record_bytes(job: EvolutionJob) -> bytes:
        return json.dumps(
            asdict(job), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")

    def _write_record(self, directory: Path, job: EvolutionJob) -> None:
        path = self._record_path(directory, job.job_id)
        try:
            existing = path.lstat()
        except FileNotFoundError:
            existing = None
        except OSError:
            raise EvolutionJobStorageError("job_storage_unsafe") from None
        if existing is not None:
            self._validate_private_file(path)
        content = self._record_bytes(job)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{job.job_id}.", suffix=".tmp", dir=directory
        )
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            view = memoryview(content)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise EvolutionJobStorageError("job_storage_unavailable")
                view = view[written:]
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            os.replace(temporary, path)
            self._validate_private_file(path)
            directory_descriptor = os.open(
                directory,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
            )
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except EvolutionJobError:
            raise
        except OSError:
            raise EvolutionJobStorageError("job_storage_unavailable") from None
        finally:
            if descriptor != -1:
                os.close(descriptor)
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def _visible_job(self, job: EvolutionJob) -> EvolutionJob:
        # A queued job also cannot be resumed after a process restart because
        # request inputs live only in the submitting process.  Do not pretend
        # it will run.  Crucially this is an overlay, so reads remain pure.
        if job.process_nonce != self.process_nonce and job.state in {"queued", "running"}:
            return replace(
                job,
                state="unknown",
                finished_at=job.finished_at or _utc_now(),
                error_code="process_interrupted",
            )
        if job.result is not None:
            return replace(job, result=_public_result(job.kind, job.result))
        return job

    def get_job(self, job_id: str) -> EvolutionJob | None:
        safe_id = self._validated_job_id(job_id)
        with self._storage_lock(create=False) as directory:
            if directory is None:
                return None
            job = self._read_record(directory, safe_id)
        return self._visible_job(job) if job is not None else None

    def list_jobs(self) -> list[EvolutionJob]:
        with self._storage_lock(create=False) as directory:
            if directory is None:
                return []
            try:
                entries = sorted(
                    (entry for entry in directory.iterdir() if entry.name.endswith(".json")),
                    key=lambda entry: entry.name,
                    reverse=True,
                )
            except OSError:
                raise EvolutionJobStorageError("job_storage_unsafe") from None
            if len(entries) > _MAX_JOB_RECORDS:
                raise EvolutionJobStorageError("job_list_limit")
            jobs: list[EvolutionJob] = []
            for entry in entries:
                job_id = entry.stem
                # Treat every .json member as a record; a bad name must not be
                # silently skipped because it could otherwise hide a link.
                safe_id = self._validated_job_id(job_id)
                job = self._read_record(directory, safe_id)
                if job is not None:
                    jobs.append(self._visible_job(job))
        return sorted(jobs, key=lambda job: (job.created_at, job.job_id), reverse=True)

    @staticmethod
    def _validate_collectors(value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, (list, tuple)) or len(value) > 32:
            raise EvolutionJobValidationError("invalid_job_input")
        names: list[str] = []
        for name in value:
            if not isinstance(name, str) or not name or len(name) > 64:
                raise EvolutionJobValidationError("invalid_job_input")
            names.append(name)
        return tuple(dict.fromkeys(names))

    @staticmethod
    def _validate_revision(value: object) -> str:
        if not isinstance(value, str) or not _REVISION_ID.fullmatch(value):
            raise EvolutionJobValidationError("invalid_job_input")
        return value

    def submit(
        self,
        kind: JobKind,
        *,
        force: bool = False,
        collector_names: list[str] | tuple[str, ...] | None = None,
        left: str | None = None,
        right: str | None = None,
    ) -> EvolutionJob:
        if kind not in JOB_KINDS:
            raise EvolutionJobValidationError("invalid_job_kind")
        request: _JobRequest
        if kind == "organism_rebuild":
            if type(force) is not bool or left is not None or right is not None:
                raise EvolutionJobValidationError("invalid_job_input")
            request = _JobRequest(force=force, collector_names=self._validate_collectors(collector_names))
        elif kind == "observer_scan":
            if force or collector_names is not None or left is not None or right is not None:
                raise EvolutionJobValidationError("invalid_job_input")
            request = _JobRequest()
        else:
            if force or collector_names is not None:
                raise EvolutionJobValidationError("invalid_job_input")
            request = _JobRequest(left=self._validate_revision(left), right=self._validate_revision(right))

        with self._storage_lock(create=True) as directory:
            assert directory is not None
            if kind in _EXCLUSIVE_KINDS:
                for existing in self._list_records_locked(directory):
                    if (
                        existing.kind == kind
                        and existing.process_nonce == self.process_nonce
                        and existing.state in {"queued", "running"}
                    ):
                        raise EvolutionJobConflict("job_already_active")
            job = EvolutionJob(
                job_id=str(uuid.uuid4()),
                kind=kind,
                state="queued",
                progress=0,
                created_at=_utc_now(),
                started_at=None,
                finished_at=None,
                process_nonce=self.process_nonce,
                result=None,
                error_code=None,
            )
            self._write_record(directory, job)
            self._requests[job.job_id] = request
            self._futures[job.job_id] = self.executor.submit(self._run, job.job_id)
        return job

    def _list_records_locked(self, directory: Path) -> list[EvolutionJob]:
        try:
            entries = list(directory.glob("*.json"))
        except OSError:
            raise EvolutionJobStorageError("job_storage_unsafe") from None
        if len(entries) > _MAX_JOB_RECORDS:
            raise EvolutionJobStorageError("job_list_limit")
        records: list[EvolutionJob] = []
        for entry in entries:
            records.append(self._read_record(directory, self._validated_job_id(entry.stem)))
        return records

    def submit_rebuild(
        self,
        *,
        force: bool = False,
        collector_names: list[str] | tuple[str, ...] | None = None,
    ) -> EvolutionJob:
        return self.submit("organism_rebuild", force=force, collector_names=collector_names)

    def submit_observer_scan(self) -> EvolutionJob:
        return self.submit("observer_scan")

    def submit_revision_diff(self, left: str, right: str) -> EvolutionJob:
        return self.submit("revision_diff", left=left, right=right)

    def _claim(self, job_id: str) -> EvolutionJob | None:
        with self._storage_lock(create=False) as directory:
            if directory is None:
                return None
            job = self._read_record(directory, job_id)
            if job is None or job.process_nonce != self.process_nonce or job.state != "queued":
                return None
            running = replace(job, state="running", started_at=_utc_now(), progress=max(job.progress, 1))
            self._write_record(directory, running)
            return running

    def _finish(self, job_id: str, *, result: dict[str, Any] | None, error_code: str | None) -> None:
        with self._storage_lock(create=False) as directory:
            if directory is None:
                return
            job = self._read_record(directory, job_id)
            if job is None or job.process_nonce != self.process_nonce or job.state != "running":
                return
            if error_code is None:
                completed = replace(
                    job,
                    state="completed",
                    progress=100,
                    finished_at=_utc_now(),
                    result=result,
                    error_code=None,
                )
            else:
                completed = replace(
                    job,
                    state="failed",
                    finished_at=_utc_now(),
                    result=None,
                    error_code=error_code,
                )
            self._write_record(directory, completed)

    @staticmethod
    def _error_code(error: BaseException) -> str:
        if isinstance(error, EvolutionJobValidationError):
            return "invalid_job_input"
        # Domain errors intentionally reduce to one stable local public code;
        # exception text may contain paths, config, or evidence.
        return "job_failed"

    def _run(self, job_id: str) -> None:
        try:
            job = self.get_job(job_id)
            if job is None or job.state != "queued":
                return
            with self._kind_lock(job.kind):
                job = self._claim(job_id)
                if job is None:
                    return
                result = self._run_fixed(job)
                self._finish(job_id, result=result, error_code=None)
        except BaseException as exc:
            # A storage failure may make this final update impossible.  Do not
            # leak it or synthesize a completed state; the existing record then
            # remains conservatively running/unknown across restart.
            try:
                self._finish(job_id, result=None, error_code=self._error_code(exc))
            except EvolutionJobError:
                pass

    def _run_fixed(self, job: EvolutionJob) -> dict[str, Any]:
        runner = _FIXED_JOB_CALLABLES.get(job.kind)
        if runner is None:
            raise EvolutionJobValidationError("invalid_job_input")
        return runner(self, job)

    def update_progress(self, job_id: str, progress: int) -> EvolutionJob:
        safe_id = self._validated_job_id(job_id)
        if type(progress) is not int:
            raise EvolutionJobValidationError("invalid_job_input")
        with self._storage_lock(create=False) as directory:
            if directory is None:
                raise EvolutionJobConflict("job_not_active")
            job = self._read_record(directory, safe_id)
            if (
                job is None
                or job.process_nonce != self.process_nonce
                or job.state not in {"queued", "running"}
            ):
                raise EvolutionJobConflict("job_not_active")
            updated = replace(job, progress=max(0, min(progress, 100)))
            self._write_record(directory, updated)
            return updated

    def cancel_job(self, job_id: str) -> EvolutionJob:
        safe_id = self._validated_job_id(job_id)
        with self._storage_lock(create=False) as directory:
            if directory is None:
                raise EvolutionJobConflict("job_not_cancellable")
            job = self._read_record(directory, safe_id)
            if (
                job is None
                or job.process_nonce != self.process_nonce
                or job.state != "queued"
            ):
                raise EvolutionJobConflict("job_not_cancellable")
            cancelled = replace(job, state="cancelled", finished_at=_utc_now())
            self._write_record(directory, cancelled)
            future = self._futures.get(safe_id)
            if future is not None:
                future.cancel()
            return cancelled

    def wait(self, job_id: str, *, timeout: float | None = None) -> EvolutionJob | None:
        safe_id = self._validated_job_id(job_id)
        future = self._futures.get(safe_id)
        if future is not None:
            try:
                future.result(timeout=timeout)
            except BaseException:
                # Worker failures are persisted as a stable job state.
                pass
        return self.get_job(safe_id)

    def shutdown(self) -> None:
        self.executor.shutdown(wait=True)


def _run_organism_rebuild(
    manager: EvolutionJobManager, job: EvolutionJob
) -> dict[str, Any]:
    request = manager._requests.get(job.job_id)
    if request is None:
        raise EvolutionJobValidationError("invalid_job_input")
    artifact = build_organism_revision(
        manager.workspace_root,
        store=manager.store,
        force=request.force,
        collector_names=list(request.collector_names),
    )
    contract = artifact.get("organism_contract", {}) if isinstance(artifact, dict) else {}
    if not isinstance(contract, dict):
        contract = {}
    return _fit_result(
        {
            "kind": "organism_rebuild",
            "revision_id": _safe_public_text(contract.get("revision_id"), limit=128),
            "revision_digest": _safe_public_text(
                contract.get("semantic_fingerprint"), limit=64
            ),
            "build_result": _safe_public_text(
                artifact.get("build_result") if isinstance(artifact, dict) else None,
                limit=32,
            ),
        }
    )


def _run_observer_scan(manager: EvolutionJobManager, job: EvolutionJob) -> dict[str, Any]:
    records = ObserverService(manager.root).scan_and_update_suggestions(max_events=1000)
    return {
        "kind": "observer_scan",
        "updated_suggestions": min(len(records), _MAX_JOB_RESULT_ITEMS),
    }


def _run_revision_diff(manager: EvolutionJobManager, job: EvolutionJob) -> dict[str, Any]:
    request = manager._requests.get(job.job_id)
    if request is None or request.left is None or request.right is None:
        raise EvolutionJobValidationError("invalid_job_input")
    diff = OrganismQuery(manager.store).diff(request.left, request.right)
    return _fit_result(
        {
            "kind": "revision_diff",
            "left": request.left,
            "right": request.right,
            "diff": _bounded_value(diff),
        }
    )


# This closed registry is the only execution surface.  It intentionally maps
# persisted job kinds to local Python callables; it is never populated from an
# HTTP request and it has no command/string-execution escape hatch.
_FIXED_JOB_CALLABLES: dict[
    JobKind, Callable[[EvolutionJobManager, EvolutionJob], dict[str, Any]]
] = {
    "organism_rebuild": _run_organism_rebuild,
    "observer_scan": _run_observer_scan,
    "revision_diff": _run_revision_diff,
}
