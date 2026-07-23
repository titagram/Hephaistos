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
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator, Literal

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
from .store import GenerationStore


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
        document = validate_pointer(value, ledger, store)
    except (
        PointerError,
        EvolutionLedgerError,
        sqlite3.Error,
        OSError,
        ValueError,
    ):
        return _PointerObservation(
            document=None,
            reason="unproven_evidence",
            evidence_digest=evidence_digest,
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
                # SQLite can reuse a process-global main-database descriptor
                # while opening only WAL/SHM descriptors for this connection.
                # Keep the observed delta while retaining the independently
                # verified main descriptor as the correlation fallback.
                identity_fds = {*connection_fds, guard.file_fd}
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


def _copy_descriptor(descriptor: int, destination: Path) -> None:
    target = os.open(
        destination,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(target, 0o600)
        os.lseek(descriptor, 0, os.SEEK_SET)
        while chunk := os.read(descriptor, 1024 * 1024):
            _write_all(target, chunk)
        os.fsync(target)
    finally:
        os.close(target)


@contextmanager
def _stable_wal_snapshot(path: Path) -> Iterator[Path]:
    """Copy one descriptor-correlated committed WAL set outside evolution."""

    with tempfile.TemporaryDirectory(
        prefix="hermes-evolution-reconcile-",
    ) as temporary:
        snapshot_root = Path(temporary)
        snapshot_root.chmod(0o700)
        snapshot_path = snapshot_root / path.name
        for _ in range(3):
            for candidate in snapshot_root.iterdir():
                candidate.unlink()
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
                    except FileNotFoundError:
                        if suffix == "-wal":
                            raise EvolutionLedgerError(
                                "unstable_ledger_snapshot"
                            ) from None
                before = {
                    name: _file_signature(info)
                    for name, (_, info) in descriptors.items()
                }
                for name, (descriptor, _) in descriptors.items():
                    _copy_descriptor(
                        descriptor,
                        snapshot_root / name,
                    )
                stable = True
                for name, (descriptor, _) in descriptors.items():
                    source = path.parent / name
                    try:
                        linked = source.lstat()
                        opened = os.fstat(descriptor)
                    except OSError:
                        stable = False
                        break
                    if (
                        _file_signature(opened) != before[name]
                        or _file_signature(linked) != before[name]
                    ):
                        stable = False
                        break
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
                    stable = False
                if stable:
                    yield snapshot_path
                    return
            finally:
                for descriptor, _ in descriptors.values():
                    os.close(descriptor)
                guard.close()
        raise EvolutionLedgerError("unstable_ledger_snapshot")


@contextmanager
def _read_only_ledger(path: Path) -> Iterator[EvolutionLedger]:
    wal_path = Path(f"{path}-wal")
    if wal_path.exists() or wal_path.is_symlink():
        with _stable_wal_snapshot(path) as snapshot_path:
            ledger = _open_existing_ledger(
                snapshot_path,
                repair=False,
            )
            try:
                yield ledger
            finally:
                ledger.connection.close()
        return

    before = _file_signature(path.lstat())
    ledger = _open_immutable_ledger(path)
    try:
        if wal_path.exists() or wal_path.is_symlink():
            raise EvolutionLedgerError("unstable_ledger_snapshot")
        yield ledger
        if (
            wal_path.exists()
            or wal_path.is_symlink()
            or _file_signature(path.lstat()) != before
        ):
            raise EvolutionLedgerError("unstable_ledger_snapshot")
    finally:
        ledger.connection.close()


class _ExistingGenerationStore(GenerationStore):
    def verified_manifest_descriptor(self, generation_id: str):
        return self.verified_manifest_descriptor_existing(generation_id)


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
        with _read_only_ledger(ledger_path) as ledger:
            return _evaluate_open_ledger(root, ledger, repair=False)
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
