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
from .organism_home import OrganismHomeError, resolve_organism_root

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
_MAX_OBSERVER_UPDATES = 1000
_MAX_DIFF_ROWS = 100
_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.ASCII,
)
_PROCESS_NONCE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", re.ASCII)
_REVISION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SERVER_PROCESS_NONCE = str(uuid.uuid4())
_SERVER_PROCESS_NONCE_PID = os.getpid()


def _server_process_nonce() -> str:
    """Return a process-instance nonce, regenerating inherited fork state."""
    global _SERVER_PROCESS_NONCE, _SERVER_PROCESS_NONCE_PID
    pid = os.getpid()
    if pid != _SERVER_PROCESS_NONCE_PID:
        _SERVER_PROCESS_NONCE = str(uuid.uuid4())
        _SERVER_PROCESS_NONCE_PID = pid
    return _SERVER_PROCESS_NONCE


def _reset_nonce_after_fork() -> None:
    global _SERVER_PROCESS_NONCE, _SERVER_PROCESS_NONCE_PID
    _SERVER_PROCESS_NONCE = str(uuid.uuid4())
    _SERVER_PROCESS_NONCE_PID = os.getpid()


if hasattr(os, "register_at_fork"):  # pragma: no branch - unavailable on Windows
    os.register_at_fork(after_in_child=_reset_nonce_after_fork)


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


@dataclass
class _JobDirectory:
    """Retained private directory descriptors for one storage transaction."""

    path: Path
    root_fd: int | None = None
    evolution_fd: int | None = None
    fd: int | None = None

    def close(self) -> None:
        for descriptor in (self.fd, self.evolution_fd, self.root_fd):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        self.fd = None
        self.evolution_fd = None
        self.root_fd = None


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


_DIFF_FIELDS = frozenset(
    {
        "added_capabilities",
        "removed_capabilities",
        "changed_state",
        "dependency_changes",
        "invariant_impact",
        "runtime_changes",
        "quality_changes",
        "coverage_changes",
        "truncated",
    }
)


def _public_diff_text(value: object, *, limit: int) -> str:
    return _safe_public_text(value, limit=limit)


def _public_diff_node(value: object) -> dict[str, Any]:
    row = value if isinstance(value, dict) else {}
    state = row.get("state")
    public_state = (
        {
            _public_diff_text(key, limit=128): item
            for key, item in list(state.items())[:_MAX_DIFF_ROWS]
            if isinstance(key, str) and isinstance(item, bool)
        }
        if isinstance(state, dict)
        else {}
    )
    refs = row.get("evidence_refs")
    return {
        "id": _public_diff_text(row.get("id"), limit=256),
        "kind": _public_diff_text(row.get("kind"), limit=128),
        "label": _public_diff_text(row.get("label"), limit=500),
        "owner_class": _public_diff_text(row.get("owner_class"), limit=128),
        "generation_scope": _public_diff_text(row.get("generation_scope"), limit=64),
        "state": public_state,
        "evidence_refs": [
            _public_diff_text(item, limit=256)
            for item in (refs[:20] if isinstance(refs, list) else [])
            if isinstance(item, (str, int, float))
        ],
    }


def _bounded_diff_rows(value: object) -> tuple[list[object], bool]:
    if not isinstance(value, list):
        return [], value is not None
    return value[:_MAX_DIFF_ROWS], len(value) > _MAX_DIFF_ROWS


def _public_revision_diff(value: object) -> dict[str, Any]:
    """Project only the fixed public ``OrganismQuery.diff`` contract."""
    raw = value if isinstance(value, dict) else {}
    truncated = bool(raw.get("truncated", False))

    def nodes(name: str) -> list[dict[str, Any]]:
        nonlocal truncated
        rows, cut = _bounded_diff_rows(raw.get(name))
        truncated = truncated or cut
        return [_public_diff_node(row) for row in rows]

    changed_rows, changed_cut = _bounded_diff_rows(raw.get("changed_state"))
    dependency_rows, dependency_cut = _bounded_diff_rows(raw.get("dependency_changes"))
    quality_rows, quality_cut = _bounded_diff_rows(raw.get("quality_changes"))
    coverage_rows, coverage_cut = _bounded_diff_rows(raw.get("coverage_changes"))
    truncated = truncated or changed_cut or dependency_cut or quality_cut or coverage_cut

    return {
        "added_capabilities": nodes("added_capabilities"),
        "removed_capabilities": nodes("removed_capabilities"),
        "changed_state": [
            {
                "id": _public_diff_text(row.get("id"), limit=256),
                "before": {
                    _public_diff_text(key, limit=128): item
                    for key, item in list(row.get("before", {}).items())[:_MAX_DIFF_ROWS]
                    if isinstance(key, str) and isinstance(item, bool)
                },
                "after": {
                    _public_diff_text(key, limit=128): item
                    for key, item in list(row.get("after", {}).items())[:_MAX_DIFF_ROWS]
                    if isinstance(key, str) and isinstance(item, bool)
                },
            }
            for row in changed_rows
            if isinstance(row, dict)
        ],
        "dependency_changes": [
            {
                "kind": _public_diff_text(row[0], limit=128),
                "from": _public_diff_text(row[1], limit=256),
                "to": _public_diff_text(row[2], limit=256),
            }
            for row in dependency_rows
            if isinstance(row, (tuple, list)) and len(row) == 3
        ],
        "invariant_impact": nodes("invariant_impact"),
        "runtime_changes": nodes("runtime_changes"),
        "quality_changes": [
            {
                "before": _public_diff_text(row.get("before"), limit=32),
                "after": _public_diff_text(row.get("after"), limit=32),
            }
            for row in quality_rows
            if isinstance(row, dict)
        ],
        "coverage_changes": [
            {
                "domain": _public_diff_text(row.get("domain"), limit=64),
                "before": _public_diff_text(row.get("before"), limit=32),
                "after": _public_diff_text(row.get("after"), limit=32),
            }
            for row in coverage_rows
            if isinstance(row, dict)
        ],
        "truncated": truncated,
    }


def _valid_public_diff(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != _DIFF_FIELDS:
        return False
    if type(value.get("truncated")) is not bool:
        return False
    for name in (
        "added_capabilities",
        "removed_capabilities",
        "changed_state",
        "dependency_changes",
        "invariant_impact",
        "runtime_changes",
        "quality_changes",
        "coverage_changes",
    ):
        rows = value.get(name)
        if not isinstance(rows, list) or len(rows) > _MAX_DIFF_ROWS:
            return False
    for name in ("added_capabilities", "removed_capabilities", "invariant_impact", "runtime_changes"):
        for row in value[name]:
            if not isinstance(row, dict) or set(row) != {
                "id", "kind", "label", "owner_class", "generation_scope", "state", "evidence_refs"
            }:
                return False
            if not all(
                isinstance(row[field], str) and len(row[field]) <= limit
                for field, limit in (("id", 256), ("kind", 128), ("label", 500), ("owner_class", 128), ("generation_scope", 64))
            ):
                return False
            if (
                not isinstance(row["state"], dict)
                or len(row["state"]) > _MAX_DIFF_ROWS
                or not all(isinstance(key, str) and len(key) <= 128 and isinstance(item, bool) for key, item in row["state"].items())
                or not isinstance(row["evidence_refs"], list)
                or len(row["evidence_refs"]) > 20
                or not all(isinstance(item, str) and len(item) <= 256 for item in row["evidence_refs"])
            ):
                return False
    for row in value["changed_state"]:
        if not isinstance(row, dict) or set(row) != {"id", "before", "after"} or not isinstance(row["id"], str) or len(row["id"]) > 256:
            return False
        for state in (row["before"], row["after"]):
            if not isinstance(state, dict) or len(state) > _MAX_DIFF_ROWS or not all(isinstance(key, str) and len(key) <= 128 and isinstance(item, bool) for key, item in state.items()):
                return False
    for row in value["dependency_changes"]:
        if not isinstance(row, dict) or set(row) != {"kind", "from", "to"} or not all(isinstance(row[field], str) and len(row[field]) <= limit for field, limit in (("kind", 128), ("from", 256), ("to", 256))):
            return False
    for name, fields, limits in (
        ("quality_changes", ("before", "after"), (32, 32)),
        ("coverage_changes", ("domain", "before", "after"), (64, 32, 32)),
    ):
        for row in value[name]:
            if not isinstance(row, dict) or set(row) != set(fields) or not all(isinstance(row[field], str) and len(row[field]) <= limit for field, limit in zip(fields, limits)):
                return False
    return True


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
        normal = (
            set(result) == {"kind", "updated_suggestions"}
            and type(value) is int
            and 0 <= value <= _MAX_OBSERVER_UPDATES
        )
        truncated = (
            set(result)
            == {"kind", "updated_suggestions", "total_updated_suggestions", "truncated"}
            and type(value) is int
            and value == _MAX_OBSERVER_UPDATES
            and type(result["total_updated_suggestions"]) is int
            and result["total_updated_suggestions"] > _MAX_OBSERVER_UPDATES
            and result["truncated"] is True
        )
        if not normal and not truncated:
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
            or not _valid_public_diff(result["diff"])
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
        public = {"kind": kind, "updated_suggestions": result["updated_suggestions"]}
        if result.get("truncated") is True:
            public["total_updated_suggestions"] = result["total_updated_suggestions"]
            public["truncated"] = True
        return public
    return _fit_result(
        {
            "kind": kind,
            "left": result["left"],
            "right": result["right"],
            "diff": result["diff"],
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
        raw_root = hermes_constants.get_organism_home() if organism_root is None else organism_root
        self.root = Path(os.path.abspath(os.fspath(raw_root)))
        try:
            resolve_organism_root(self.root)
        except OrganismHomeError:
            raise EvolutionJobStorageError("job_storage_unsafe") from None
        self.workspace_root = self._validated_workspace_root(
            Path.cwd() if workspace_root is None else Path(workspace_root)
        )
        # An explicit root makes the invariant visible: this is always the
        # cross-profile global store, never the active profile's storage.
        self.store = OrganismRevisionStore(self.root / "gnothi_seauton")
        self.process_nonce = self._validated_nonce(process_nonce or _server_process_nonce())
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="evolution-job")
        self._lock = threading.RLock()
        self._lifecycle_lock = threading.RLock()
        self._closed = False
        self._futures: dict[str, Future[None]] = {}
        self._requests: dict[str, _JobRequest] = {}

    @property
    def jobs_dir(self) -> Path:
        return self.root / "evolution" / "dashboard-jobs"

    @staticmethod
    def _validated_workspace_root(value: Path) -> Path:
        try:
            root = Path(os.path.abspath(os.fspath(value)))
            walked = Path(root.anchor)
            for part in root.parts[1:]:
                walked = walked / part
                info = walked.lstat()
                if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                    raise EvolutionJobValidationError("invalid_workspace_root")
            info = root.lstat()
            git_info = (root / ".git").lstat()
        except (OSError, RuntimeError, ValueError, EvolutionJobValidationError):
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
    def _validate_private_directory_info(info: os.stat_result) -> None:
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISDIR(info.st_mode)
            or not _private_mode(info, 0o700)
        ):
            raise EvolutionJobStorageError("job_storage_unsafe")

    @staticmethod
    def _validate_private_file_info(info: os.stat_result) -> None:
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or not _private_mode(info, 0o600)
        ):
            raise EvolutionJobStorageError("job_storage_unsafe")

    @classmethod
    def _validate_private_directory(cls, path: Path) -> os.stat_result:
        try:
            info = path.lstat()
        except (OSError, TypeError, NotImplementedError):
            raise EvolutionJobStorageError("job_storage_unsafe") from None
        cls._validate_private_directory_info(info)
        return info

    @classmethod
    def _validate_private_file(cls, path: Path) -> os.stat_result:
        try:
            info = path.lstat()
        except (OSError, TypeError, NotImplementedError):
            raise EvolutionJobStorageError("job_storage_unsafe") from None
        cls._validate_private_file_info(info)
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

    @staticmethod
    def _supports_anchored_storage() -> bool:
        supported = getattr(os, "supports_dir_fd", frozenset())
        return (
            os.name == "posix"
            and hasattr(os, "O_DIRECTORY")
            and hasattr(os, "O_NOFOLLOW")
            and os.open in supported
            and os.stat in supported
            and os.mkdir in supported
        )

    @staticmethod
    def _directory_flags() -> int:
        return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW

    @classmethod
    def _open_or_create_child_directory(
        cls, parent_fd: int, name: str, *, create: bool, private: bool = True
    ) -> int | None:
        try:
            info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            if not create:
                return None
            try:
                os.mkdir(name, 0o700, dir_fd=parent_fd)
            except FileExistsError:
                pass
            info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except (OSError, TypeError, NotImplementedError):
            raise EvolutionJobStorageError("job_storage_unsafe") from None
        if private:
            cls._validate_private_directory_info(info)
        elif stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise EvolutionJobStorageError("job_storage_unsafe")
        try:
            descriptor = os.open(name, cls._directory_flags(), dir_fd=parent_fd)
            opened = os.fstat(descriptor)
            if private:
                cls._validate_private_directory_info(opened)
            elif not stat.S_ISDIR(opened.st_mode):
                raise EvolutionJobStorageError("job_storage_unsafe")
            if not _same_inode(info, opened):
                raise EvolutionJobStorageError("job_storage_unsafe")
            return descriptor
        except BaseException:
            if "descriptor" in locals():
                os.close(descriptor)
            raise

    def _open_anchored_root(self, *, create: bool) -> int | None:
        descriptor = os.open(self.root.anchor, self._directory_flags())
        try:
            parts = self.root.parts[1:]
            for index, part in enumerate(parts):
                child = self._open_or_create_child_directory(
                    descriptor,
                    part,
                    create=create,
                    private=index == len(parts) - 1,
                )
                if child is None:
                    os.close(descriptor)
                    return None
                os.close(descriptor)
                descriptor = child
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def _verify_linked_directory(self, directory: _JobDirectory) -> None:
        """Reject a lexical path swap while retained descriptors keep I/O safe."""
        try:
            resolve_organism_root(self.root)
            expected = (
                (self.root, directory.root_fd),
                (self.root / "evolution", directory.evolution_fd),
                (self.jobs_dir, directory.fd),
            )
            for path, descriptor in expected:
                if descriptor is None:
                    continue
                linked = path.lstat()
                self._validate_private_directory_info(linked)
                if not _same_inode(linked, os.fstat(descriptor)):
                    raise EvolutionJobStorageError("job_storage_unsafe")
        except OrganismHomeError:
            raise EvolutionJobStorageError("job_storage_unsafe") from None
        except (OSError, TypeError, NotImplementedError):
            raise EvolutionJobStorageError("job_storage_unsafe") from None

    def _open_jobs_directory(self, *, create: bool) -> _JobDirectory | None:
        if not self._supports_anchored_storage():
            try:
                resolve_organism_root(self.root)
            except OrganismHomeError:
                raise EvolutionJobStorageError("job_storage_unsafe") from None
            if create:
                self._ensure_private_directory(self.root)
                self._ensure_private_directory(self.root / "evolution")
                self._ensure_private_directory(self.jobs_dir)
            else:
                try:
                    self._validate_private_directory(self.root)
                    self._validate_private_directory(self.root / "evolution")
                    self._validate_private_directory(self.jobs_dir)
                except FileNotFoundError:
                    return None
            return _JobDirectory(self.jobs_dir)

        root_fd = self._open_anchored_root(create=create)
        if root_fd is None:
            return None
        evolution_fd: int | None = None
        jobs_fd: int | None = None
        try:
            evolution_fd = self._open_or_create_child_directory(
                root_fd, "evolution", create=create
            )
            if evolution_fd is None:
                os.close(root_fd)
                return None
            jobs_fd = self._open_or_create_child_directory(
                evolution_fd, "dashboard-jobs", create=create
            )
            if jobs_fd is None:
                os.close(evolution_fd)
                os.close(root_fd)
                return None
            directory = _JobDirectory(
                self.jobs_dir,
                root_fd=root_fd,
                evolution_fd=evolution_fd,
                fd=jobs_fd,
            )
            self._verify_linked_directory(directory)
            return directory
        except BaseException:
            for descriptor in (jobs_fd, evolution_fd, root_fd):
                if descriptor is not None:
                    os.close(descriptor)
            raise

    @staticmethod
    def _private_file_info_at(directory: _JobDirectory, name: str) -> os.stat_result | None:
        try:
            if directory.fd is not None:
                return os.stat(name, dir_fd=directory.fd, follow_symlinks=False)
            return (directory.path / name).lstat()
        except FileNotFoundError:
            return None
        except (OSError, TypeError, NotImplementedError):
            raise EvolutionJobStorageError("job_storage_unsafe") from None

    @classmethod
    def _open_private_lock(
        cls, directory: _JobDirectory, name: str, *, create: bool
    ) -> int | None:
        existing = cls._private_file_info_at(directory, name)
        flags = os.O_RDWR | getattr(os, "O_BINARY", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor: int | None = None
        try:
            if existing is None:
                if not create:
                    return None
                try:
                    if directory.fd is not None:
                        descriptor = os.open(
                            name, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory.fd
                        )
                    else:
                        descriptor = os.open(
                            directory.path / name, flags | os.O_CREAT | os.O_EXCL, 0o600
                        )
                except FileExistsError:
                    existing = cls._private_file_info_at(directory, name)
                else:
                    os.fchmod(descriptor, 0o600)
            if descriptor is None:
                if existing is None:
                    existing = cls._private_file_info_at(directory, name)
                assert existing is not None
                cls._validate_private_file_info(existing)
                descriptor = (
                    os.open(name, flags, dir_fd=directory.fd)
                    if directory.fd is not None
                    else os.open(directory.path / name, flags)
                )
            opened = os.fstat(descriptor)
            linked = cls._private_file_info_at(directory, name)
            if linked is None:
                raise EvolutionJobStorageError("job_storage_unsafe")
            cls._validate_private_file_info(opened)
            cls._validate_private_file_info(linked)
            if not _same_inode(opened, linked):
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
    def _storage_lock(self, *, create: bool) -> Iterator[_JobDirectory | None]:
        with self._lock:
            directory = self._open_jobs_directory(create=create)
            if directory is None:
                yield None
                return
            descriptor = self._open_private_lock(directory, ".jobs.lock", create=create)
            if descriptor is None:
                try:
                    self._verify_linked_directory(directory)
                    yield directory
                finally:
                    directory.close()
                return
            try:
                self._acquire_file_lock(descriptor)
                self._verify_linked_directory(directory)
                yield directory
            finally:
                self._release_file_lock(descriptor)
                os.close(descriptor)
                directory.close()

    @contextmanager
    def _kind_lock(self, kind: JobKind) -> Iterator[None]:
        if kind not in _EXCLUSIVE_KINDS:
            yield
            return
        with self._storage_lock(create=False) as directory:
            if directory is None:
                raise EvolutionJobStorageError("job_storage_unsafe")
            descriptor = self._open_private_lock(directory, f".{kind}.lock", create=True)
            assert descriptor is not None
        try:
            self._acquire_file_lock(descriptor)
            yield
        finally:
            self._release_file_lock(descriptor)
            os.close(descriptor)

    @staticmethod
    def _record_name(job_id: str) -> str:
        return f"{job_id}.json"

    def _read_record(
        self, directory: _JobDirectory, job_id: str
    ) -> EvolutionJob | None:
        name = self._record_name(job_id)
        expected = self._private_file_info_at(directory, name)
        if expected is None:
            return None
        self._validate_private_file_info(expected)
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor: int | None = None
        try:
            descriptor = (
                os.open(name, flags, dir_fd=directory.fd)
                if directory.fd is not None
                else os.open(directory.path / name, flags)
            )
            opened = os.fstat(descriptor)
            linked = self._private_file_info_at(directory, name)
            if linked is None or not _same_inode(expected, opened) or not _same_inode(opened, linked):
                raise EvolutionJobStorageError("job_storage_unsafe")
            self._validate_private_file_info(opened)
            self._validate_private_file_info(linked)
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

    def _write_record(self, directory: _JobDirectory, job: EvolutionJob) -> None:
        name = self._record_name(job.job_id)
        existing = self._private_file_info_at(directory, name)
        if existing is not None:
            self._validate_private_file_info(existing)
        content = self._record_bytes(job)
        if len(content) > _MAX_RECORD_BYTES:
            raise EvolutionJobStorageError("job_record_invalid")
        descriptor: int | None = None
        temporary_name: str | None = None
        try:
            if directory.fd is None:
                descriptor, temporary_path = tempfile.mkstemp(
                    prefix=f".{job.job_id}.", suffix=".tmp", dir=directory.path
                )
                temporary_name = temporary_path
            else:
                for _ in range(16):
                    candidate = f".{job.job_id}.{uuid.uuid4().hex}.tmp"
                    try:
                        descriptor = os.open(
                            candidate,
                            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                            0o600,
                            dir_fd=directory.fd,
                        )
                    except FileExistsError:
                        continue
                    temporary_name = candidate
                    break
                if descriptor is None or temporary_name is None:
                    raise EvolutionJobStorageError("job_storage_unavailable")
            os.fchmod(descriptor, 0o600)
            view = memoryview(content)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise EvolutionJobStorageError("job_storage_unavailable")
                view = view[written:]
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            if directory.fd is None:
                assert temporary_name is not None
                os.replace(temporary_name, directory.path / name)
            else:
                assert temporary_name is not None
                self._verify_linked_directory(directory)
                os.replace(
                    temporary_name,
                    name,
                    src_dir_fd=directory.fd,
                    dst_dir_fd=directory.fd,
                )
                os.fsync(directory.fd)
            linked = self._private_file_info_at(directory, name)
            if linked is None:
                raise EvolutionJobStorageError("job_storage_unsafe")
            self._validate_private_file_info(linked)
        except EvolutionJobError:
            raise
        except (OSError, TypeError, NotImplementedError):
            raise EvolutionJobStorageError("job_storage_unavailable") from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if temporary_name is not None:
                try:
                    if directory.fd is None:
                        Path(temporary_name).unlink()
                    else:
                        os.unlink(temporary_name, dir_fd=directory.fd)
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
                finished_at=job.finished_at,
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
            jobs: list[EvolutionJob] = []
            for safe_id in self._iter_record_ids(directory):
                job = self._read_record(directory, safe_id)
                if job is not None:
                    jobs.append(self._visible_job(job))
        return sorted(jobs, key=lambda job: (job.created_at, job.job_id), reverse=True)

    def _iter_record_ids(self, directory: _JobDirectory) -> Iterator[str]:
        """Yield at most the fixed durable cap without materializing a directory."""
        try:
            scan_target: int | Path = (
                os.dup(directory.fd) if directory.fd is not None else directory.path
            )
            with os.scandir(scan_target) as entries:
                count = 0
                for entry in entries:
                    if not entry.name.endswith(".json"):
                        continue
                    count += 1
                    if count > _MAX_JOB_RECORDS:
                        raise EvolutionJobStorageError("job_list_limit")
                    yield self._validated_job_id(entry.name[:-5])
        except EvolutionJobError:
            raise
        except (OSError, TypeError, NotImplementedError):
            raise EvolutionJobStorageError("job_storage_unsafe") from None

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

        with self._lifecycle_lock:
            if self._closed:
                raise EvolutionJobConflict("job_manager_closed")
            with self._storage_lock(create=True) as directory:
                assert directory is not None
                count = 0
                for existing_id in self._iter_record_ids(directory):
                    count += 1
                    if kind in _EXCLUSIVE_KINDS:
                        existing = self._read_record(directory, existing_id)
                        if (
                            existing is not None
                            and existing.kind == kind
                            and existing.process_nonce == self.process_nonce
                            and existing.state in {"queued", "running"}
                        ):
                            raise EvolutionJobConflict("job_already_active")
                if count >= _MAX_JOB_RECORDS:
                    raise EvolutionJobConflict("job_capacity_reached")
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
                try:
                    future = self.executor.submit(self._run, job.job_id)
                except BaseException:
                    terminal = replace(
                        job,
                        state="failed",
                        finished_at=_utc_now(),
                        error_code="job_failed",
                    )
                    self._write_record(directory, terminal)
                    self._requests.pop(job.job_id, None)
                    return terminal
                self._futures[job.job_id] = future
                return job

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

    def _workspace_for_work(self) -> Path:
        """Revalidate the server-owned repository binding before a fixed worker runs."""
        root = self._validated_workspace_root(self.workspace_root)
        if root != self.workspace_root:
            raise EvolutionJobValidationError("invalid_workspace_root")
        return root

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
        with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
            self.executor.shutdown(wait=True)


def _run_organism_rebuild(
    manager: EvolutionJobManager, job: EvolutionJob
) -> dict[str, Any]:
    request = manager._requests.get(job.job_id)
    if request is None:
        raise EvolutionJobValidationError("invalid_job_input")
    artifact = build_organism_revision(
        manager._workspace_for_work(),
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
    if len(records) > _MAX_OBSERVER_UPDATES:
        return {
            "kind": "observer_scan",
            "updated_suggestions": _MAX_OBSERVER_UPDATES,
            "total_updated_suggestions": len(records),
            "truncated": True,
        }
    return {
        "kind": "observer_scan",
        "updated_suggestions": len(records),
    }


def _run_revision_diff(manager: EvolutionJobManager, job: EvolutionJob) -> dict[str, Any]:
    request = manager._requests.get(job.job_id)
    if request is None or request.left is None or request.right is None:
        raise EvolutionJobValidationError("invalid_job_input")
    diff = _public_revision_diff(
        OrganismQuery(manager.store).diff(request.left, request.right)
    )
    return _fit_result(
        {
            "kind": "revision_diff",
            "left": request.left,
            "right": request.right,
            "diff": diff,
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
