"""Private, profile-local persistence for engineering review runs."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import stat
import threading
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
_EVIDENCE_AGENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_EVIDENCE_DOMAIN = b"Hermes engineering reviewer evidence v1\0"

RunStatus = Literal["active", "complete", "cleanup_failed"]
Effort = Literal["low", "medium", "high"]


@dataclass(slots=True)
class _RunCapability:
    secret: bytes
    bundle_hash: str
    bundle_bytes: bytes
    expected_evidence: dict[str, str]


_CAPABILITIES: dict[Path, _RunCapability] = {}
_CAPABILITY_LOCK = threading.RLock()


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
    if stat.S_IMODE(info.st_mode) != 0o700:
        raise ReviewRunError(f"directory is not private: {path}")


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


def _snapshot_bundle(path: Path) -> tuple[str, bytes]:
    """Read executable bytes once through a no-follow, identity-bound descriptor."""
    try:
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise ReviewRunError(
                "engineering bundle must be a regular non-symlink file"
            )
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ReviewRunError("engineering bundle could not be opened") from exc
    try:
        after = os.fstat(descriptor)
        if not stat.S_ISREG(after.st_mode) or (before.st_dev, before.st_ino) != (
            after.st_dev,
            after.st_ino,
        ):
            raise ReviewRunError("engineering bundle identity changed while opening")
        chunks: list[bytes] = []
        remaining = after.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                raise ReviewRunError("engineering bundle could not be fully read")
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if not data:
            raise ReviewRunError("engineering bundle is empty")
        return hashlib.sha256(data).hexdigest(), data
    finally:
        os.close(descriptor)


def _register_capability(root: Path, bundle_hash: str, bundle_bytes: bytes) -> None:
    with _CAPABILITY_LOCK:
        _CAPABILITIES[root] = _RunCapability(
            secret=secrets.token_bytes(32),
            bundle_hash=bundle_hash,
            bundle_bytes=bundle_bytes,
            expected_evidence={},
        )


def _drop_capability(root: Path) -> None:
    with _CAPABILITY_LOCK:
        capability = _CAPABILITIES.pop(root, None)
        if capability is not None:
            capability.secret = b"\0" * len(capability.secret)
            capability.bundle_bytes = b""
            capability.expected_evidence.clear()


def _evidence_mac(secret: bytes, run_id: str, agent_id: str, data: bytes) -> str:
    message = (
        _EVIDENCE_DOMAIN
        + run_id.encode("ascii")
        + b"\0"
        + agent_id.encode("ascii")
        + b"\0"
        + data
    )
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


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
        bundle: Path | None = None,
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
        bundle_hash, bundle_bytes = _snapshot_bundle(
            bundle_path() if bundle is None else Path(bundle)
        )
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
                "bundle_hash": bundle_hash,
                "provenance_hash": _provenance_hash(
                    canonical_workspace, target, effort
                ),
            }
            run = cls(run_id, root, canonical_workspace, target, effort, session_id)
            try:
                run._write_metadata(metadata)
                _register_capability(root, bundle_hash, bundle_bytes)
            except BaseException:
                _drop_capability(root)
                raise
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
    def load_cleanup_failed(cls, run_id: str) -> ReviewRun:
        """Resolve one terminal failed run without accepting a filesystem path."""
        run_id = _validate_run_id(run_id)
        reviews = _review_root(_canonical_home())
        matches: list[ReviewRun] = []
        try:
            sessions = list(reviews.iterdir())
        except OSError as exc:
            raise ReviewRunError("review runs could not be enumerated") from exc
        for session_root in sessions:
            try:
                session_id = _validate_session_id(session_root.name)
                _secure_directory(session_root)
                candidate = session_root / run_id
                if not candidate.exists() and not candidate.is_symlink():
                    continue
                _secure_directory(candidate)
                matches.append(
                    cls._from_metadata(
                        candidate,
                        run_id=run_id,
                        session_id=session_id,
                    )
                )
            except ReviewRunError:
                continue
        if len(matches) != 1:
            raise ReviewRunError("cleanup-failed review run was not found uniquely")
        run = matches[0]
        if run.status != "cleanup_failed":
            raise ReviewRunError("only cleanup_failed review runs can be recovered")
        return run

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
        if status in {"complete", "cleanup_failed"} and not isinstance(
            completed_at, str
        ):
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

    def _assert_hierarchy(self) -> Path:
        """Bind a mutable instance to its current profile-local run root."""
        run_id = _validate_run_id(self.run_id)
        session_id = _validate_session_id(self.session_id)
        home = _canonical_home()
        session_root = _session_root(home, session_id, create=False)
        expected_root = session_root / run_id
        try:
            supplied_root = Path(self.root)
        except TypeError as exc:
            raise ReviewRunError("review run root is invalid") from exc
        if supplied_root != expected_root:
            raise ReviewRunError("review run root is outside its canonical hierarchy")
        _secure_directory(expected_root)
        return expected_root

    def _validated_loaded(self) -> ReviewRun:
        """Return the on-disk run only when this instance has its identity."""
        self._assert_hierarchy()
        loaded = self.load(self.run_id, self.session_id)
        if (
            self.workspace != loaded.workspace
            or self.target != loaded.target
            or self.effort != loaded.effort
        ):
            raise ReviewRunError("review run identity does not match its metadata")
        return loaded

    def _atomic_write(
        self, name: str, data: bytes, *, allow_metadata: bool = False
    ) -> Path:
        if not allow_metadata:
            name = _artifact_name(name)
        root = self._assert_hierarchy()
        destination = root / name
        if destination.exists() or destination.is_symlink():
            _secure_file(destination)
        temporary = root / f".{name}.{secrets.token_urlsafe(12)}.tmp"
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
                if hasattr(os, "fchmod"):
                    os.fchmod(descriptor, 0o600)
                else:
                    os.chmod(temporary, 0o600)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.replace(temporary, destination)
            directory_descriptor = os.open(root, os.O_RDONLY)
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
        loaded = self._validated_loaded()
        return loaded._atomic_write(_artifact_name(name), data)

    def read_private_artifact(self, name: str) -> bytes:
        """Read one run-owned private artifact without following links."""
        loaded = self._validated_loaded()
        return _read_private_file(loaded.root / _artifact_name(name))

    def commit_reviewer_evidence(self, agent_id: str, data: bytes) -> str:
        """Commit an expected HMAC before harness evidence becomes authoritative.

        The secret and expected set exist only in the creating Hermes process.
        Loading the run in another process intentionally cannot recreate them.
        """
        if not isinstance(agent_id, str) or not _EVIDENCE_AGENT_ID_RE.fullmatch(
            agent_id
        ):
            raise ReviewRunError("reviewer evidence agent id is invalid")
        if not isinstance(data, bytes):
            raise TypeError("reviewer evidence must be bytes")
        loaded = self._validated_loaded()
        if loaded.status != "active":
            raise ReviewRunError("reviewer evidence requires an active run")
        with _CAPABILITY_LOCK:
            capability = _CAPABILITIES.get(loaded.root)
            if capability is None:
                raise ReviewRunError(
                    "review capability is unavailable; active runs cannot resume after restart"
                )
            digest = _evidence_mac(capability.secret, loaded.run_id, agent_id, data)
            capability.expected_evidence[agent_id] = digest
            return digest

    def authorize_engine_invocation(
        self,
        *,
        engine_bundle: Path | None,
        require_evidence: bool,
        workspace: Path,
    ) -> tuple[bytes, list[dict[str, Any]] | None]:
        """Return the run-bound executable bytes and optional evidence snapshot."""
        loaded = self._validated_loaded()
        if loaded.status != "active":
            raise ReviewRunError(
                "authoritative engine invocation requires an active run"
            )
        try:
            supplied_workspace = Path(workspace).resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise ReviewRunError("engine workspace could not be resolved") from exc
        if supplied_workspace != loaded.workspace:
            raise ReviewRunError("engine workspace does not match the registered run")
        with _CAPABILITY_LOCK:
            capability = _CAPABILITIES.get(loaded.root)
            if capability is None:
                raise ReviewRunError(
                    "review capability is unavailable; active runs cannot resume after restart"
                )
            if engine_bundle is not None:
                candidate_hash, candidate_bytes = _snapshot_bundle(Path(engine_bundle))
                if not hmac.compare_digest(
                    capability.bundle_hash, candidate_hash
                ) or not hmac.compare_digest(capability.bundle_bytes, candidate_bytes):
                    raise ReviewRunError(
                        "engineering bundle does not match the run capability"
                    )
            expected = dict(capability.expected_evidence)
            secret = capability.secret
            executable = capability.bundle_bytes

        if not require_evidence:
            return executable, None

        reviewers = loaded.root / "subagents" / "reviewers"
        try:
            _secure_directory(reviewers)
            names = {entry.name for entry in reviewers.iterdir()}
        except (OSError, ReviewRunError) as exc:
            raise ReviewRunError("reviewer evidence directory is unavailable") from exc
        expected_jsonl = {f"agent-{agent_id}.jsonl" for agent_id in expected}
        expected_auth = {f"agent-{agent_id}.auth.json" for agent_id in expected}
        actual_jsonl = {name for name in names if name.endswith(".jsonl")}
        actual_auth = {name for name in names if name.endswith(".auth.json")}
        if (
            not expected
            or actual_jsonl != expected_jsonl
            or actual_auth != expected_auth
        ):
            raise ReviewRunError("reviewer evidence set is missing or contains extras")

        records: list[dict[str, Any]] = []
        for agent_id in sorted(expected):
            # The JSONL remains for Qwen compatibility and operator inspection,
            # but its presence—not its mutable contents—is all authority uses.
            _secure_file(reviewers / f"agent-{agent_id}.jsonl")
            data = _read_private_file(reviewers / f"agent-{agent_id}.auth.json")
            actual_mac = _evidence_mac(secret, loaded.run_id, agent_id, data)
            if not hmac.compare_digest(expected[agent_id], actual_mac):
                raise ReviewRunError("reviewer evidence authentication failed")
            try:
                value = json.loads(data)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ReviewRunError(
                    "authenticated reviewer evidence is invalid JSON"
                ) from exc
            if not isinstance(value, dict) or value.get("agentId") != agent_id:
                raise ReviewRunError(
                    "authenticated reviewer evidence identity is invalid"
                )
            records.append(value)
        with _CAPABILITY_LOCK:
            current = _CAPABILITIES.get(loaded.root)
            if current is not capability or current.expected_evidence != expected:
                raise ReviewRunError(
                    "review capability changed during evidence snapshot"
                )
        return executable, records

    def authenticated_reviewer_records(
        self, engine_bundle: Path
    ) -> list[dict[str, Any]]:
        """Compatibility facade for callers that require evidence only."""
        _, records = self.authorize_engine_invocation(
            engine_bundle=engine_bundle,
            require_evidence=True,
            workspace=self.workspace,
        )
        assert records is not None
        return records

    def mark_complete(self) -> ReviewRun:
        """Transition an active run to its terminal complete state."""
        loaded = self._validated_loaded()
        if loaded.status == "complete":
            _drop_capability(loaded.root)
            return loaded
        if loaded.status != "active":
            raise ReviewRunError("only active review runs can complete")
        metadata = loaded._metadata()
        now = _now()
        metadata["status"] = "complete"
        metadata["updated_at"] = now
        metadata["completed_at"] = now
        loaded._write_metadata(metadata)
        _drop_capability(loaded.root)
        return replace(loaded, status="complete")

    def mark_cleanup_failed(self) -> ReviewRun:
        """Record terminal cleanup failure when metadata remains trustworthy."""
        loaded = self._validated_loaded()
        if loaded.status == "cleanup_failed":
            _drop_capability(loaded.root)
            return loaded
        if loaded.status != "active":
            raise ReviewRunError("only active review runs can fail cleanup")
        metadata = loaded._metadata()
        now = _now()
        metadata["status"] = "cleanup_failed"
        metadata["updated_at"] = now
        metadata["completed_at"] = now
        loaded._write_metadata(metadata)
        _drop_capability(loaded.root)
        return replace(loaded, status="cleanup_failed")

    def mark_recovered(self) -> ReviewRun:
        """Transition a cleanup_failed run after verified resource removal."""
        loaded = self._validated_loaded()
        if loaded.status != "cleanup_failed":
            raise ReviewRunError("only cleanup_failed review runs can be recovered")
        metadata = loaded._metadata()
        now = _now()
        metadata["status"] = "complete"
        metadata["updated_at"] = now
        metadata["completed_at"] = now
        loaded._write_metadata(metadata)
        _drop_capability(loaded.root)
        return replace(loaded, status="complete")

    def revoke_authority_capability(self) -> None:
        """Unconditionally destroy process-local authority without reading disk.

        Only the lifecycle owner calls this escape hatch. It intentionally does
        not trust mutable metadata: terminal-state persistence is a separate,
        best-effort operation and can never gate cryptographic revocation.
        """
        _drop_capability(self.root)

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
                run = ReviewRun._from_metadata(
                    root, run_id=run_id, session_id=session_id
                )
                if run.status == "complete":
                    metadata = run._metadata()
                    completed.append((str(metadata["completed_at"]), root))
            except ReviewRunError:
                continue

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
            _drop_capability(root)
            shutil.rmtree(tombstone)
        except FileNotFoundError:
            continue
        except (OSError, ReviewRunError):
            # A failed cleanup is a terminal state too: do not repeatedly risk
            # deleting an ambiguous directory on later retention passes.
            try:
                if tombstone.exists():
                    os.replace(tombstone, root)
                run = ReviewRun._from_metadata(
                    root, run_id=root.name, session_id=root.parent.name
                )
                metadata = run._metadata()
                metadata["status"] = "cleanup_failed"
                metadata["updated_at"] = _now()
                run._write_metadata(metadata)
            except (OSError, ReviewRunError):
                pass
            continue
        removed.append(root)
    return removed
