"""Deterministic startup reconciliation for evolution pointer state."""

from __future__ import annotations

import os
import secrets
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

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
    _open_parent,
    _read_pointer,
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


def _all_events(ledger: EvolutionLedger) -> list[StoredEvent]:
    events: list[StoredEvent] = []
    after = 0
    while True:
        page = ledger.history(limit=1000, after=after)
        if not page:
            return events
        events.extend(page)
        after = page[-1].event_sequence


def _validated_pointer(
    path: Path,
    ledger: EvolutionLedger,
    store: GenerationStore,
) -> PointerDocument | None:
    try:
        value = _read_pointer(path)
        if value is None:
            return None
        return validate_pointer(value, ledger, store)
    except (PointerError, EvolutionLedgerError, sqlite3.Error, OSError, ValueError):
        return None


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
            connection, _ = _connect(path, read_only=False)
        else:
            connection, _ = _connect_committed_readonly(path)
        _verify_retained_identity(
            path,
            guard,
            connection,
            None,
        )
        connection.execute("PRAGMA foreign_keys=ON")
        if not repair:
            connection.execute("PRAGMA query_only=ON")
        _validate_schema(connection)
        _verify_retained_identity(
            path,
            guard,
            connection,
            None,
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


def _condition_digest(
    *,
    status: str,
    active: PointerDocument | None,
    lkg: PointerDocument | None,
) -> str:
    return content_digest(
        {
            "status": status,
            "active_integrity_digest": (
                None if active is None else active.integrity_digest
            ),
            "last_known_good_integrity_digest": (
                None if lkg is None else lkg.integrity_digest
            ),
        },
        domain="hades-evolution-reconciliation-v1",
    )


def _recovery_event(
    ledger: EvolutionLedger,
    *,
    status: Literal["restored_lkg", "base_only"],
    active: PointerDocument | None,
    lkg: PointerDocument | None,
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
        (condition, lkg.integrity_digest)
        if lkg is not None
        else (condition,)
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

    ledger: EvolutionLedger | None = None
    try:
        try:
            ledger = _open_existing_ledger(
                ledger_path,
                repair=repair,
            )
        except (EvolutionLedgerError, sqlite3.Error, OSError, ValueError):
            return _base_only("ledger_unavailable")
        if ledger.verify_chain():
            return _base_only("ledger_unavailable")

        store = GenerationStore(root / "generations")
        active_path = root / "active.json"
        lkg_path = root / "last-known-good.json"
        active = _validated_pointer(active_path, ledger, store)
        lkg = _validated_pointer(lkg_path, ledger, store)

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
                    active=None,
                    lkg=lkg,
                )
                _restore_active_from_lkg(active_path, lkg)
                restored = _validated_pointer(active_path, ledger, store)
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
                active=None,
                lkg=None,
            )
        return result
    finally:
        if ledger is not None:
            ledger.connection.close()


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
