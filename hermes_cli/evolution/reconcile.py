"""Deterministic startup reconciliation for evolution pointer state."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import stat
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Iterator, Literal, TypeVar

from hermes_constants import get_hermes_home

from .contract import canonical_json_bytes, content_digest
from .ledger import (
    EvolutionLedger,
    EvolutionLedgerError,
    LifecycleEvent,
    StoredEvent,
    _connect,
    _connect_committed_readonly,
    _open_protected_path,
    _open_readonly_protected_path,
    _validate_schema,
    _verify_retained_identity,
)
from .locking import lifecycle_lock
from .pointers import (
    PointerDocument,
    PointerError,
    _MAX_POINTER_BYTES,
    _open_existing_target,
    _open_parent,
    _require_linked_parent,
    _validate_target_info,
    _write_all,
    validate_pointer,
)
from .store import (
    ExistingGenerationProof,
    GenerationStore,
    VerifiedManifestDescriptor,
)


@dataclass(frozen=True)
class ReconciliationResult:
    status: Literal[
        "coherent",
        "restored_lkg",
        "base_only",
        "blocked",
    ]
    active: PointerDocument | None
    last_known_good: PointerDocument | None
    overlay_enabled: bool
    diagnostics: tuple[str, ...]


@dataclass(frozen=True)
class _PointerObservation:
    document: PointerDocument | None
    reason: str
    evidence_digest: str


_MISSING_POINTER_DIGEST = content_digest(
    {"observation": "missing"},
    domain="hades-evolution-pointer-observation-v1",
)

_MAX_SNAPSHOT_ATTEMPTS = 3
_MAX_SNAPSHOT_FILE_BYTES = 512 * 1024 * 1024
_MAX_SNAPSHOT_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
_MAX_SNAPSHOT_SECONDS = 10.0
_QueryResult = TypeVar("_QueryResult")


def _all_events(ledger: EvolutionLedger) -> list[StoredEvent]:
    events: list[StoredEvent] = []
    after = 0
    while True:
        page = ledger.history(limit=1000, after=after)
        if not page:
            return events
        events.extend(page)
        after = page[-1].event_sequence


def _metadata_observation(path: Path) -> _PointerObservation:
    """Describe unsafe/unreadable evidence without retaining its path or bytes."""

    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            kind = "symlink"
        elif stat.S_ISREG(info.st_mode):
            kind = "regular"
        elif stat.S_ISDIR(info.st_mode):
            kind = "directory"
        else:
            kind = "other"
        metadata: dict[str, object] = {
            "kind": kind,
            "mode": stat.S_IMODE(info.st_mode),
            "links": info.st_nlink,
            "owner_matches": (
                None
                if not hasattr(os, "geteuid")
                else info.st_uid == os.geteuid()
            ),
            "size_class": min(info.st_size // 1024, 65),
        }
    except OSError:
        metadata = {"kind": "unreadable"}
    return _PointerObservation(
        document=None,
        reason="unsafe_metadata",
        evidence_digest=content_digest(
            metadata,
            domain="hades-evolution-pointer-metadata-v1",
        ),
    )


def _observe_pointer(
    path: Path,
    ledger: EvolutionLedger,
    store: GenerationStore,
) -> _PointerObservation:
    parent_descriptor: int | None = None
    descriptor: int | None = None
    try:
        parent_descriptor = _open_parent(path)
        descriptor = _open_existing_target(
            parent_descriptor,
            path.name,
        )
        if descriptor is None:
            return _PointerObservation(
                document=None,
                reason="missing",
                evidence_digest=_MISSING_POINTER_DIGEST,
            )
        data = bytearray()
        while chunk := os.read(descriptor, 16 * 1024):
            data.extend(chunk)
            if len(data) > _MAX_POINTER_BYTES:
                return _PointerObservation(
                    document=None,
                    reason="oversized",
                    evidence_digest=content_digest(
                        {
                            "bounded_prefix_digest": hashlib.sha256(
                                data[:_MAX_POINTER_BYTES]
                            ).hexdigest(),
                            "observed_size_at_least": len(data),
                        },
                        domain="hades-evolution-pointer-oversized-v1",
                    ),
                )
        _require_linked_parent(path, parent_descriptor)
    except (PointerError, OSError, TypeError, ValueError):
        return _metadata_observation(path)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if parent_descriptor is not None:
            os.close(parent_descriptor)

    raw = bytes(data)
    evidence_digest = hashlib.sha256(raw).hexdigest()
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _PointerObservation(
            document=None,
            reason="malformed_json",
            evidence_digest=evidence_digest,
        )
    if not isinstance(value, dict):
        return _PointerObservation(
            document=None,
            reason="invalid_document",
            evidence_digest=evidence_digest,
        )
    try:
        if canonical_json_bytes(value) != raw:
            return _PointerObservation(
                document=None,
                reason="noncanonical",
                evidence_digest=evidence_digest,
            )
    except (TypeError, ValueError):
        return _PointerObservation(
            document=None,
            reason="invalid_document",
            evidence_digest=evidence_digest,
        )
    if (
        type(value.get("schema_version")) is int
        and value["schema_version"] != 1
    ):
        return _PointerObservation(
            document=None,
            reason="unsupported_schema",
            evidence_digest=evidence_digest,
        )
    try:
        if isinstance(store, _ExistingGenerationStore):
            store.reset_proof_observation()
        document = validate_pointer(value, ledger, store)
    except (
        PointerError,
        EvolutionLedgerError,
        sqlite3.Error,
        OSError,
        ValueError,
    ):
        if isinstance(store, _ExistingGenerationStore):
            proof = store.proof_observation
            if proof is not None and proof.failure_code is not None:
                return _PointerObservation(
                    document=None,
                    reason=f"store_{proof.failure_code}",
                    evidence_digest=content_digest(
                        {
                            "pointer_digest": evidence_digest,
                            "store_proof_digest": proof.evidence_digest,
                        },
                        domain=(
                            "hades-evolution-pointer-store-proof-v1"
                        ),
                    ),
                )
        return _PointerObservation(
            document=None,
            reason="unproven_evidence",
            evidence_digest=evidence_digest,
        )
    if isinstance(store, _ExistingGenerationStore):
        proof = store.proof_observation
        if proof is not None:
            evidence_digest = content_digest(
                {
                    "pointer_digest": evidence_digest,
                    "store_proof_digest": proof.evidence_digest,
                },
                domain="hades-evolution-pointer-store-proof-v1",
            )
    return _PointerObservation(
        document=document,
        reason="proven",
        evidence_digest=evidence_digest,
    )


def _open_existing_ledger(
    path: Path,
    *,
    repair: bool,
) -> EvolutionLedger:
    """Open one existing validated ledger without creating or migrating it."""

    guard = (
        _open_protected_path(path)
        if repair
        else _open_readonly_protected_path(path)
    )
    connection: sqlite3.Connection | None = None
    try:
        if guard.created:
            raise EvolutionLedgerError("invalid_ledger_database")
        if repair:
            connection, connection_fds = _connect(
                path,
                read_only=False,
            )
        else:
            connection, connection_fds = _connect_committed_readonly(path)
        identity_fds = connection_fds
        if connection_fds is not None:
            retained = os.fstat(guard.file_fd)
            if not any(
                _same_snapshot_file(descriptor, retained)
                for descriptor in connection_fds
            ):
                # A process-global SQLite main descriptor may predate this
                # connection. Its newly discovered WAL/SHM descriptors cannot
                # prove main-file ownership, so retain no FD claim here.
                identity_fds = None
        _verify_retained_identity(
            path,
            guard,
            connection,
            identity_fds,
        )
        connection.execute("PRAGMA foreign_keys=ON")
        if not repair:
            connection.execute("PRAGMA query_only=ON")
        _validate_schema(connection)
        _verify_retained_identity(
            path,
            guard,
            connection,
            identity_fds,
        )
        ledger = object.__new__(EvolutionLedger)
        ledger.path = path
        ledger._lock = threading.RLock()
        ledger.connection = connection
        ledger.journal_mode = str(
            connection.execute("PRAGMA journal_mode").fetchone()[0]
        )
        connection = None
        return ledger
    finally:
        if connection is not None:
            connection.close()
        guard.close()


def _same_snapshot_file(
    descriptor: int,
    expected: os.stat_result,
) -> bool:
    try:
        observed = os.fstat(descriptor)
    except (OSError, TypeError, NotImplementedError):
        return False
    return (observed.st_dev, observed.st_ino) == (
        expected.st_dev,
        expected.st_ino,
    )


class _ImmutableEvolutionLedger(EvolutionLedger):
    """Ledger view whose independent event proof also uses immutable main."""

    def prove_committed_event(self, expected: StoredEvent) -> StoredEvent:
        independent = _open_immutable_ledger(self.path)
        try:
            if independent.verify_chain():
                raise EvolutionLedgerError("uncommitted_ledger_evidence")
            events = independent.history(
                limit=1,
                after=expected.event_sequence - 1,
            )
            if (
                len(events) != 1
                or events[0].event_sequence != expected.event_sequence
                or events[0] != expected
            ):
                raise EvolutionLedgerError(
                    "uncommitted_ledger_evidence"
                )
            return events[0]
        finally:
            independent.connection.close()


def _ledger_from_connection(
    path: Path,
    connection: sqlite3.Connection,
    *,
    immutable: bool,
) -> EvolutionLedger:
    ledger_type = (
        _ImmutableEvolutionLedger
        if immutable
        else EvolutionLedger
    )
    ledger = object.__new__(ledger_type)
    ledger.path = path
    ledger._lock = threading.RLock()
    ledger.connection = connection
    ledger.journal_mode = str(
        connection.execute("PRAGMA journal_mode").fetchone()[0]
    )
    return ledger


def _open_immutable_ledger(path: Path) -> EvolutionLedger:
    guard = _open_readonly_protected_path(path)
    connection: sqlite3.Connection | None = None
    try:
        connection, connection_fds = _connect(path, read_only=True)
        _verify_retained_identity(
            path,
            guard,
            connection,
            connection_fds,
        )
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA query_only=ON")
        _validate_schema(connection)
        _verify_retained_identity(
            path,
            guard,
            connection,
            connection_fds,
        )
        ledger = _ledger_from_connection(
            path,
            connection,
            immutable=True,
        )
        connection = None
        return ledger
    finally:
        if connection is not None:
            connection.close()
        guard.close()


def _file_signature(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        stat.S_IMODE(info.st_mode),
        info.st_uid,
        info.st_nlink,
    )


def _validate_snapshot_file(info: os.stat_result) -> None:
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or (
            hasattr(os, "geteuid")
            and info.st_uid != os.geteuid()
        )
        or (
            os.name == "posix"
            and stat.S_IMODE(info.st_mode) != 0o600
        )
    ):
        raise EvolutionLedgerError("unsafe_ledger_snapshot")


def _open_snapshot_file(
    path: Path,
    directory_descriptor: int | None,
) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        linked = path.lstat()
        _validate_snapshot_file(linked)
        descriptor = (
            os.open(path.name, flags, dir_fd=directory_descriptor)
            if directory_descriptor is not None
            else os.open(path, flags)
        )
        opened = os.fstat(descriptor)
        _validate_snapshot_file(opened)
        if _file_signature(linked)[:2] != _file_signature(opened)[:2]:
            raise EvolutionLedgerError("unsafe_ledger_snapshot")
        return descriptor, opened
    except BaseException:
        if "descriptor" in locals():
            os.close(descriptor)
        raise


@dataclass
class _SnapshotBudget:
    started_at: float
    copied_bytes: int = 0

    @classmethod
    def start(cls) -> _SnapshotBudget:
        return cls(started_at=time.monotonic())

    def check_time(self) -> None:
        if time.monotonic() - self.started_at > _MAX_SNAPSHOT_SECONDS:
            raise EvolutionLedgerError("ledger_snapshot_time_limit")

    def check_pre_copy(self, sizes: list[int]) -> None:
        self.check_time()
        if any(size > _MAX_SNAPSHOT_FILE_BYTES for size in sizes):
            raise EvolutionLedgerError("ledger_snapshot_file_limit")
        if (
            sum(sizes) > _MAX_SNAPSHOT_TOTAL_BYTES
            or self.copied_bytes + sum(sizes)
            > _MAX_SNAPSHOT_TOTAL_BYTES
        ):
            raise EvolutionLedgerError("ledger_snapshot_total_limit")

    def consume(self, size: int) -> None:
        self.check_time()
        self.copied_bytes += size
        if self.copied_bytes > _MAX_SNAPSHOT_TOTAL_BYTES:
            raise EvolutionLedgerError("ledger_snapshot_total_limit")


class _RetryableSnapshotError(RuntimeError):
    """One transient capture attempt failed without unsafe source metadata."""


def _copy_descriptor(
    descriptor: int,
    destination: Path,
    budget: _SnapshotBudget,
) -> None:
    budget.check_time()
    target = os.open(
        destination,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(target, 0o600)
        os.lseek(descriptor, 0, os.SEEK_SET)
        file_bytes = 0
        while chunk := os.read(descriptor, 1024 * 1024):
            file_bytes += len(chunk)
            if file_bytes > _MAX_SNAPSHOT_FILE_BYTES:
                raise EvolutionLedgerError(
                    "ledger_snapshot_file_limit"
                )
            budget.consume(len(chunk))
            _write_all(target, chunk)
        os.fsync(target)
        budget.check_time()
    finally:
        os.close(target)


def _clean_snapshot_root(snapshot_root: Path) -> None:
    for candidate in snapshot_root.iterdir():
        candidate.unlink()


def _capture_wal_snapshot(
    path: Path,
    snapshot_root: Path,
    budget: _SnapshotBudget,
) -> Path:
    """Capture one stable descriptor-correlated main/WAL/SHM attempt."""

    budget.check_time()
    guard = _open_readonly_protected_path(path)
    descriptors: dict[str, tuple[int, os.stat_result]] = {
        path.name: (
            os.dup(guard.file_fd),
            os.fstat(guard.file_fd),
        )
    }
    try:
        for suffix in ("-wal", "-shm"):
            source = Path(f"{path}{suffix}")
            try:
                descriptors[source.name] = _open_snapshot_file(
                    source,
                    guard.directory_fd,
                )
            except FileNotFoundError as error:
                if suffix == "-wal":
                    raise _RetryableSnapshotError(
                        "snapshot_member_disappeared"
                    ) from error
        before = {
            name: _file_signature(info)
            for name, (_, info) in descriptors.items()
        }
        budget.check_pre_copy(
            [info.st_size for _, info in descriptors.values()]
        )
        for name, (descriptor, _) in descriptors.items():
            _copy_descriptor(
                descriptor,
                snapshot_root / name,
                budget,
            )
        for name, (descriptor, _) in descriptors.items():
            source = path.parent / name
            try:
                linked = source.lstat()
                opened = os.fstat(descriptor)
            except OSError as error:
                raise _RetryableSnapshotError(
                    "snapshot_member_changed"
                ) from error
            if (
                _file_signature(opened) != before[name]
                or _file_signature(linked) != before[name]
            ):
                raise _RetryableSnapshotError(
                    "snapshot_member_changed"
                )
        live_names = {
            candidate.name
            for candidate in path.parent.iterdir()
            if candidate.name in {
                path.name,
                f"{path.name}-wal",
                f"{path.name}-shm",
            }
        }
        if live_names != set(descriptors):
            raise _RetryableSnapshotError("snapshot_member_set_changed")
        budget.check_time()
        return snapshot_root / path.name
    finally:
        for descriptor, _ in descriptors.values():
            os.close(descriptor)
        guard.close()


@contextmanager
def _read_only_ledger_attempt(
    path: Path,
    snapshot_root: Path,
    budget: _SnapshotBudget,
) -> Iterator[EvolutionLedger]:
    """Open and validate one complete isolated reconciliation attempt."""

    budget.check_time()
    wal_path = Path(f"{path}-wal")
    if not wal_path.exists() and not wal_path.is_symlink():
        before = _file_signature(path.lstat())
        ledger = _open_immutable_ledger(path)
        try:
            if wal_path.exists() or wal_path.is_symlink():
                raise _RetryableSnapshotError(
                    "wal_appeared_during_immutable_open"
                )
            yield ledger
            budget.check_time()
            if (
                wal_path.exists()
                or wal_path.is_symlink()
                or _file_signature(path.lstat()) != before
            ):
                raise _RetryableSnapshotError(
                    "immutable_source_changed"
                )
        finally:
            ledger.connection.close()
        return

    snapshot_path = _capture_wal_snapshot(
        path,
        snapshot_root,
        budget,
    )
    try:
        ledger = _open_existing_ledger(
            snapshot_path,
            repair=False,
        )
    except EvolutionLedgerError as error:
        if error.args == ("invalid_ledger_database",):
            raise _RetryableSnapshotError(
                "snapshot_database_changed"
            ) from error
        raise
    except (sqlite3.DatabaseError, OSError) as error:
        raise _RetryableSnapshotError(
            "snapshot_database_changed"
        ) from error
    try:
        yield ledger
        budget.check_time()
    finally:
        ledger.connection.close()


def _evaluate_read_only(
    root: Path,
    path: Path,
) -> ReconciliationResult:
    """Evaluate under one shared retry, byte, and deadline state machine."""

    budget = _SnapshotBudget.start()
    with tempfile.TemporaryDirectory(
        prefix="hermes-evolution-reconcile-",
    ) as temporary:
        snapshot_root = Path(temporary)
        snapshot_root.chmod(0o700)
        for _ in range(_MAX_SNAPSHOT_ATTEMPTS):
            budget.check_time()
            _clean_snapshot_root(snapshot_root)
            try:
                with _read_only_ledger_attempt(
                    path,
                    snapshot_root,
                    budget,
                ) as ledger:
                    result = _evaluate_open_ledger(
                        root,
                        ledger,
                        repair=False,
                    )
                    if result == _base_only("ledger_unavailable"):
                        raise _RetryableSnapshotError(
                            "snapshot_database_changed"
                        )
                    budget.check_time()
            except EvolutionLedgerError:
                raise
            except (
                _RetryableSnapshotError,
                FileNotFoundError,
                OSError,
                PointerError,
                sqlite3.DatabaseError,
            ):
                continue
            return result
        raise EvolutionLedgerError("unstable_ledger_snapshot")


def read_evolution_snapshot(query: Callable[[EvolutionLedger], _QueryResult]) -> _QueryResult:
    """Run one bounded query against A6's immutable/WAL-safe ledger snapshot."""
    root = Path(get_hermes_home()) / "evolution"
    path = root / "evolution.db"
    budget = _SnapshotBudget.start()
    with tempfile.TemporaryDirectory(prefix="hermes-evolution-read-") as temporary:
        snapshot_root = Path(temporary)
        snapshot_root.chmod(0o700)
        for _ in range(_MAX_SNAPSHOT_ATTEMPTS):
            budget.check_time()
            _clean_snapshot_root(snapshot_root)
            try:
                with _read_only_ledger_attempt(path, snapshot_root, budget) as ledger:
                    value = query(ledger)
                    budget.check_time()
                    return value
            except EvolutionLedgerError:
                raise
            except (_RetryableSnapshotError, FileNotFoundError, OSError, sqlite3.DatabaseError):
                continue
    raise EvolutionLedgerError("unstable_ledger_snapshot")


class _ExistingGenerationStore(GenerationStore):
    proof_observation: ExistingGenerationProof | None = None

    def reset_proof_observation(self) -> None:
        self.proof_observation = None

    def verified_manifest_descriptor(
        self,
        generation_id: str,
    ) -> VerifiedManifestDescriptor:
        proof = self.observe_existing_generation(generation_id)
        self.proof_observation = proof
        if proof.descriptor is None:
            raise ValueError("generation integrity failure")
        return proof.descriptor


def _condition_digest(
    *,
    status: str,
    active: _PointerObservation,
    lkg: _PointerObservation,
) -> str:
    return content_digest(
        {
            "status": status,
            "active": {
                "reason": active.reason,
                "evidence_digest": active.evidence_digest,
            },
            "last_known_good": {
                "reason": lkg.reason,
                "evidence_digest": lkg.evidence_digest,
            },
        },
        domain="hades-evolution-reconciliation-v1",
    )


def _recovery_event(
    ledger: EvolutionLedger,
    *,
    status: Literal["restored_lkg", "base_only"],
    active: _PointerObservation,
    lkg: _PointerObservation,
) -> StoredEvent:
    condition = _condition_digest(
        status=status,
        active=active,
        lkg=lkg,
    )
    reason_code = (
        "active_restored_from_lkg"
        if status == "restored_lkg"
        else "stable_base_only"
    )
    reason_summary = (
        "restored active pointer from proven last known good"
        if status == "restored_lkg"
        else "evolution overlays disabled because no pointer was proven"
    )
    inputs = (
        condition,
        active.evidence_digest,
        lkg.evidence_digest,
    )
    matches = [
        event
        for event in _all_events(ledger)
        if event.event_type == "supervisor_recovery"
        and event.actor == "supervisor"
        and event.attempt_id is None
        and event.generation_id is None
        and event.prior_state is None
        and event.next_state is None
        and event.authorization_id is None
        and event.input_digests == inputs
        and event.reason_code == reason_code
        and event.reason_summary == reason_summary
    ]
    if matches:
        event = matches[0]
        if ledger.prove_committed_event(event) != event:
            raise EvolutionLedgerError("uncommitted_recovery_event")
        return event
    event = ledger.append_event(
        LifecycleEvent(
            event_id=f"recovery-{condition}",
            attempt_id=None,
            generation_id=None,
            event_type="supervisor_recovery",
            prior_state=None,
            next_state=None,
            actor="supervisor",
            input_digests=inputs,
            authorization_id=None,
            reason_code=reason_code,
            reason_summary=reason_summary,
            created_at=datetime.now(UTC).strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ"
            ),
        )
    )
    if ledger.prove_committed_event(event) != event:
        raise EvolutionLedgerError("uncommitted_recovery_event")
    return event


def _restore_active_from_lkg(
    active_path: Path,
    lkg: PointerDocument,
) -> None:
    """Atomically copy the exact canonical LKG document over active."""

    data = canonical_json_bytes(lkg.to_mapping())
    parent_descriptor = _open_parent(active_path)
    temporary_name: str | None = None
    temporary_descriptor: int | None = None
    try:
        for _ in range(16):
            candidate = (
                f".{active_path.name}.reconcile-{secrets.token_hex(16)}"
            )
            try:
                temporary_descriptor = os.open(
                    candidate,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=parent_descriptor,
                )
            except FileExistsError:
                continue
            temporary_name = candidate
            break
        if temporary_descriptor is None or temporary_name is None:
            raise PointerError("pointer_write_failed")
        os.fchmod(temporary_descriptor, 0o600)
        _validate_target_info(os.fstat(temporary_descriptor))
        _write_all(temporary_descriptor, data)
        os.fsync(temporary_descriptor)
        os.close(temporary_descriptor)
        temporary_descriptor = None
        _require_linked_parent(active_path, parent_descriptor)
        os.replace(
            temporary_name,
            active_path.name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
        )
        temporary_name = None
        os.fsync(parent_descriptor)
    finally:
        if temporary_descriptor is not None:
            try:
                os.close(temporary_descriptor)
            except OSError:
                pass
        if temporary_name is not None:
            try:
                os.unlink(temporary_name, dir_fd=parent_descriptor)
            except OSError:
                pass
        os.close(parent_descriptor)


def _base_only(diagnostic: str) -> ReconciliationResult:
    return ReconciliationResult(
        status="base_only",
        active=None,
        last_known_good=None,
        overlay_enabled=False,
        diagnostics=(diagnostic,),
    )


def _evaluate(*, repair: bool) -> ReconciliationResult:
    root = Path(get_hermes_home()) / "evolution"
    ledger_path = root / "evolution.db"
    if not ledger_path.exists() or ledger_path.is_symlink():
        return _base_only("ledger_unavailable")

    if repair:
        try:
            ledger = _open_existing_ledger(
                ledger_path,
                repair=True,
            )
        except (EvolutionLedgerError, sqlite3.Error, OSError, ValueError):
            return _base_only("ledger_unavailable")
        try:
            return _evaluate_open_ledger(root, ledger, repair=True)
        finally:
            ledger.connection.close()

    try:
        return _evaluate_read_only(root, ledger_path)
    except (EvolutionLedgerError, sqlite3.Error, OSError, ValueError):
        return _base_only("ledger_unavailable")


def _evaluate_open_ledger(
    root: Path,
    ledger: EvolutionLedger,
    *,
    repair: bool,
) -> ReconciliationResult:
    """Evaluate one already-open, appropriately isolated ledger view."""

    try:
        if ledger.verify_chain():
            return _base_only("ledger_unavailable")

        store = _ExistingGenerationStore()
        active_path = root / "active.json"
        lkg_path = root / "last-known-good.json"
        active_observation = _observe_pointer(active_path, ledger, store)
        lkg_observation = _observe_pointer(lkg_path, ledger, store)
        active = active_observation.document
        lkg = lkg_observation.document

        if active is not None and lkg is not None:
            if active == lkg:
                return ReconciliationResult(
                    status="coherent",
                    active=active,
                    last_known_good=lkg,
                    overlay_enabled=True,
                    diagnostics=(),
                )
            return ReconciliationResult(
                status="blocked",
                active=active,
                last_known_good=lkg,
                overlay_enabled=False,
                diagnostics=("pointer_divergence",),
            )

        if active is None and lkg is not None:
            result = ReconciliationResult(
                status="restored_lkg",
                active=lkg,
                last_known_good=lkg,
                overlay_enabled=True,
                diagnostics=("active_pointer_unproven",),
            )
            if repair:
                _recovery_event(
                    ledger,
                    status="restored_lkg",
                    active=active_observation,
                    lkg=lkg_observation,
                )
                _restore_active_from_lkg(active_path, lkg)
                restored = _observe_pointer(
                    active_path,
                    ledger,
                    store,
                ).document
                if restored != lkg:
                    raise PointerError("pointer_write_failed")
            return result

        if active is not None:
            return ReconciliationResult(
                status="blocked",
                active=active,
                last_known_good=None,
                overlay_enabled=False,
                diagnostics=("last_known_good_pointer_unproven",),
            )

        result = _base_only("evolution_state_unproven")
        if repair:
            _recovery_event(
                ledger,
                status="base_only",
                active=active_observation,
                lkg=lkg_observation,
            )
        return result
    except (EvolutionLedgerError, sqlite3.Error, ValueError):
        return _base_only("ledger_unavailable")


def reconcile_evolution_state(
    *,
    repair: bool,
) -> ReconciliationResult:
    """Prove current pointer state or deterministically disable overlays."""

    if type(repair) is not bool:
        raise ValueError("invalid_reconciliation_mode")
    if not repair:
        return _evaluate(repair=False)
    with lifecycle_lock():
        return _evaluate(repair=True)
