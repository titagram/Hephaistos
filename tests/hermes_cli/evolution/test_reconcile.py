"""Deterministic truth-table and fault contracts for evolution reconciliation."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import uuid
from dataclasses import replace
from pathlib import Path

import pytest

from hermes_cli.evolution import pointers as pointer_module
from hermes_cli.evolution import reconcile as reconcile_module
from hermes_cli.evolution.contract import canonical_json_bytes, content_digest
from hermes_cli.evolution.ledger import (
    EvolutionLedger,
    EvolutionLedgerError,
    LifecycleEvent,
    SCHEMA_VERSION,
)
from hermes_cli.evolution.pointers import (
    PointerDocument,
    atomic_write_pointer,
    initialize_baseline_pointers,
    pointer_integrity_digest,
)
from hermes_cli.evolution.reconcile import reconcile_evolution_state
from hermes_cli.evolution.store import GenerationStore, StableBaseIdentity


def _setup(home: Path):
    store = GenerationStore(home / "evolution" / "generations")
    ledger = EvolutionLedger(home / "evolution" / "evolution.db")
    baseline = store.initialize_baseline(
        StableBaseIdentity("1.0.0", "a" * 40, "1", "b" * 64)
    )
    active, lkg = initialize_baseline_pointers(ledger, store, baseline)
    return ledger, store, active, lkg


@pytest.fixture
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "hermes-home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HADES_HOME", str(home))
    ledger, store, active, lkg = _setup(home)
    return home, ledger, store, active, lkg


def _paths(home: Path) -> tuple[Path, Path]:
    return home / "evolution" / "active.json", home / "evolution" / "last-known-good.json"


def _events(ledger: EvolutionLedger) -> list:
    result = []
    after = 0
    while page := ledger.history(limit=1000, after=after):
        result.extend(page)
        after = page[-1].event_sequence
    return result


def _snapshot(home: Path, ledger: EvolutionLedger) -> tuple[object, ...]:
    active, lkg = _paths(home)
    return (
        active.read_bytes() if active.exists() and not active.is_symlink() else None,
        lkg.read_bytes() if lkg.exists() and not lkg.is_symlink() else None,
        tuple(_events(ledger)),
    )


def _tree_inventory(root: Path) -> dict[str, tuple[str, int, bytes | None]]:
    inventory: dict[str, tuple[str, int, bytes | None]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        info = path.lstat()
        if path.is_symlink():
            inventory[relative] = (
                "symlink",
                stat.S_IMODE(info.st_mode),
                os.readlink(path).encode(),
            )
        elif path.is_file():
            inventory[relative] = (
                "file",
                stat.S_IMODE(info.st_mode),
                path.read_bytes(),
            )
        else:
            inventory[relative] = (
                "directory",
                stat.S_IMODE(info.st_mode),
                None,
            )
    return inventory


def _second_valid_document(
    ledger: EvolutionLedger,
    source: PointerDocument,
) -> PointerDocument:
    event = ledger.append_event(
        LifecycleEvent(
            event_id=str(uuid.uuid4()),
            attempt_id=None,
            generation_id=None,
            event_type="baseline_designated",
            prior_state=None,
            next_state=None,
            actor="system",
            input_digests=ledger.history()[0].input_digests,
            authorization_id=None,
            reason_code="baseline",
            reason_summary="baseline designation",
            created_at="2026-07-24T00:00:00.000000Z",
        )
    )
    payload = {
        **source.to_mapping(),
        "lifecycle_sequence": event.event_sequence,
        "designated_at": event.created_at,
        "ledger_event_digest": event.event_digest,
    }
    payload.pop("integrity_digest")
    return PointerDocument(
        **payload,
        integrity_digest=pointer_integrity_digest(payload, event.event_digest),
    )


@pytest.mark.parametrize(
    ("row", "repair", "status", "overlay", "diagnostics"),
    [
        ("coherent", False, "coherent", True, ()),
        ("coherent", True, "coherent", True, ()),
        ("missing_active", False, "restored_lkg", True, ("active_pointer_unproven",)),
        ("missing_active", True, "restored_lkg", True, ("active_pointer_unproven",)),
        ("invalid_active", False, "restored_lkg", True, ("active_pointer_unproven",)),
        ("invalid_active", True, "restored_lkg", True, ("active_pointer_unproven",)),
        ("missing_lkg", False, "blocked", False, ("last_known_good_pointer_unproven",)),
        ("missing_lkg", True, "blocked", False, ("last_known_good_pointer_unproven",)),
        ("invalid_lkg", False, "blocked", False, ("last_known_good_pointer_unproven",)),
        ("invalid_lkg", True, "blocked", False, ("last_known_good_pointer_unproven",)),
        ("divergent", False, "blocked", False, ("pointer_divergence",)),
        ("divergent", True, "blocked", False, ("pointer_divergence",)),
        ("both_missing", False, "base_only", False, ("evolution_state_unproven",)),
        ("both_missing", True, "base_only", False, ("evolution_state_unproven",)),
        ("both_invalid", False, "base_only", False, ("evolution_state_unproven",)),
        ("both_invalid", True, "base_only", False, ("evolution_state_unproven",)),
    ],
)
def test_complete_reconciliation_truth_table(
    state,
    row: str,
    repair: bool,
    status: str,
    overlay: bool,
    diagnostics: tuple[str, ...],
) -> None:
    home, ledger, _, active, lkg = state
    active_path, lkg_path = _paths(home)
    lkg_bytes = lkg_path.read_bytes()
    if row == "missing_active":
        active_path.unlink()
    elif row == "invalid_active":
        active_path.write_bytes(b"{invalid")
        active_path.chmod(0o600)
    elif row == "missing_lkg":
        lkg_path.unlink()
    elif row == "invalid_lkg":
        lkg_path.write_bytes(b"{invalid-lkg")
        lkg_path.chmod(0o600)
    elif row == "divergent":
        atomic_write_pointer(lkg_path, _second_valid_document(ledger, lkg))
    elif row == "both_missing":
        active_path.unlink()
        lkg_path.unlink()
    elif row == "both_invalid":
        active_path.write_bytes(b"{invalid-active")
        lkg_path.write_bytes(b"{invalid-lkg")
        active_path.chmod(0o600)
        lkg_path.chmod(0o600)
    before = _snapshot(home, ledger)

    result = reconcile_evolution_state(repair=repair)

    assert (result.status, result.overlay_enabled, result.diagnostics) == (
        status,
        overlay,
        diagnostics,
    )
    if status == "coherent":
        assert result.active == active
        assert result.last_known_good == lkg
    elif status == "restored_lkg":
        assert result.active == result.last_known_good == lkg
    elif status == "blocked" and row in {"missing_lkg", "invalid_lkg"}:
        assert result.active == active
        assert result.last_known_good is None
    if not repair or status in {"coherent", "blocked"}:
        assert _snapshot(home, ledger) == before
    elif status == "restored_lkg":
        assert active_path.read_bytes() == lkg_bytes
        assert len(_events(ledger)) == len(before[2]) + 1
    else:
        if row == "both_missing":
            assert not active_path.exists()
            assert not lkg_path.exists()
        else:
            assert active_path.read_bytes() == b"{invalid-active"
            assert lkg_path.read_bytes() == b"{invalid-lkg"
        assert len(_events(ledger)) == len(before[2]) + 1


def test_read_only_reconciliation_never_takes_lock_or_writes_state(
    state,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home, ledger, _, _, _ = state
    _paths(home)[0].unlink()
    before = _snapshot(home, ledger)

    def forbidden_lock(*_args, **_kwargs):
        raise AssertionError("read-only reconciliation took lifecycle lock")

    monkeypatch.setattr(reconcile_module, "lifecycle_lock", forbidden_lock)
    result = reconcile_evolution_state(repair=False)

    assert result.status == "restored_lkg"
    assert _snapshot(home, ledger) == before


def test_read_only_reconciliation_preserves_complete_live_tree_without_wal(
    state,
) -> None:
    home, ledger, _, _, _ = state
    root = home / "evolution"
    ledger.connection.close()
    assert not (root / "evolution.db-wal").exists()
    assert not (root / "evolution.db-shm").exists()
    before = _tree_inventory(root)

    result = reconcile_evolution_state(repair=False)

    assert result.status == "coherent"
    assert _tree_inventory(root) == before


def test_read_only_reconciliation_copies_committed_live_wal_without_mutation(
    state,
) -> None:
    home, ledger, _, _, _ = state
    root = home / "evolution"
    ledger.connection.execute("PRAGMA wal_autocheckpoint=0")
    ledger.append_event(
        LifecycleEvent(
            event_id="committed-in-live-wal",
            attempt_id=None,
            generation_id=None,
            event_type="observation",
            prior_state=None,
            next_state=None,
            actor="system",
            input_digests=("f" * 64,),
            authorization_id=None,
            reason_code="wal",
            reason_summary="committed WAL evidence",
            created_at="2026-07-24T00:00:00.000000Z",
        )
    )
    assert (root / "evolution.db-wal").exists()
    assert (root / "evolution.db-shm").exists()
    before = _tree_inventory(root)

    result = reconcile_evolution_state(repair=False)

    assert result.status == "coherent"
    assert _tree_inventory(root) == before


def test_read_only_snapshot_excludes_real_outer_uncommitted_sqlite_event(
    state,
) -> None:
    home, ledger, _, active, lkg = state
    root = home / "evolution"
    ledger.connection.execute("PRAGMA wal_autocheckpoint=0")
    ledger.connection.execute("BEGIN IMMEDIATE")
    uncommitted = ledger._append(
        ledger.connection,
        LifecycleEvent(
            event_id="outer-uncommitted",
            attempt_id=None,
            generation_id=None,
            event_type="observation",
            prior_state=None,
            next_state=None,
            actor="system",
            input_digests=("e" * 64,),
            authorization_id=None,
            reason_code="uncommitted",
            reason_summary="must not enter committed reconciliation",
            created_at="2026-07-24T00:00:00.000000Z",
        ),
    )
    before = _tree_inventory(root)
    try:
        result = reconcile_evolution_state(repair=False)
        after = _tree_inventory(root)
    finally:
        ledger.connection.execute("ROLLBACK")

    assert result.status == "coherent"
    assert result.active == active
    assert result.last_known_good == lkg
    assert after == before
    assert all(
        event.event_sequence != uncommitted.event_sequence
        for event in _events(ledger)
    )


@pytest.mark.parametrize("repair", [False, True])
def test_missing_generation_store_is_unproven_and_never_recreated(
    state,
    repair: bool,
) -> None:
    home, ledger, store, _, _ = state
    root = store.root
    ledger.connection.close()
    for path in root.rglob("*"):
        if path.is_file():
            path.chmod(0o600)
        elif path.is_dir():
            path.chmod(0o700)
    root.chmod(0o700)
    shutil.rmtree(root)
    before = _tree_inventory(home / "evolution")

    result = reconcile_evolution_state(repair=repair)

    assert result.status == "base_only"
    assert not root.exists()
    if not repair:
        assert _tree_inventory(home / "evolution") == before


@pytest.mark.parametrize("repair", [False, True])
def test_future_ledger_schema_fails_closed_without_rewriting_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    repair: bool,
) -> None:
    home = tmp_path / f"future-{repair}"
    root = home / "evolution"
    root.mkdir(mode=0o700, parents=True)
    home.chmod(0o700)
    path = root / "evolution.db"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE schema_version (version INTEGER NOT NULL)"
    )
    connection.execute(
        "INSERT INTO schema_version VALUES (?)",
        (SCHEMA_VERSION + 1,),
    )
    connection.commit()
    connection.close()
    path.chmod(0o600)
    before = path.read_bytes()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HADES_HOME", str(home))

    result = reconcile_evolution_state(repair=repair)

    assert result == reconcile_module._base_only("ledger_unavailable")
    assert path.read_bytes() == before
    assert not (root / "evolution.db-wal").exists()
    assert not (root / "evolution.db-shm").exists()


@pytest.mark.parametrize("kind", ["mode", "symlink"])
@pytest.mark.parametrize("repair", [False, True])
def test_invalid_active_metadata_restores_exact_lkg_when_repairing(
    state,
    tmp_path: Path,
    kind: str,
    repair: bool,
) -> None:
    home, ledger, _, _, lkg = state
    active_path, lkg_path = _paths(home)
    expected = lkg_path.read_bytes()
    if kind == "mode":
        active_path.chmod(0o640)
    else:
        active_path.unlink()
        target = tmp_path / "target"
        target.write_text("do not follow", encoding="utf-8")
        active_path.symlink_to(target)
    before_events = len(_events(ledger))

    result = reconcile_evolution_state(repair=repair)

    assert result.status == "restored_lkg"
    assert result.active == lkg
    if repair:
        assert not active_path.is_symlink()
        assert active_path.read_bytes() == expected
        assert stat.S_IMODE(active_path.stat().st_mode) == 0o600
        assert len(_events(ledger)) == before_events + 1
    else:
        assert len(_events(ledger)) == before_events


@pytest.mark.parametrize(
    "corruption",
    [
        "future_schema",
        "missing_generation",
        "manifest_bytes",
        "ledger_chain",
        "uncommitted_proof",
    ],
)
def test_unprovable_committed_evidence_disables_both_pointers(
    state,
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    home, ledger, store, active, _ = state
    active_path, lkg_path = _paths(home)
    if corruption == "future_schema":
        for path in (active_path, lkg_path):
            value = json.loads(path.read_bytes())
            value["schema_version"] = 2
            value["integrity_digest"] = hashlib.sha256(
                b"hades-evolution-pointer-v1\0"
                + canonical_json_bytes({k: v for k, v in value.items() if k != "integrity_digest"})
                + str(value["ledger_event_digest"]).encode("ascii")
            ).hexdigest()
            path.write_bytes(canonical_json_bytes(value))
    elif corruption == "missing_generation":
        generation = store.root / active.generation_id
        generation.rename(store.root / f".removed-{active.generation_id}")
    elif corruption == "manifest_bytes":
        manifest = store.root / active.generation_id / "manifest.json"
        root = manifest.parent
        root.chmod(0o755)
        manifest.chmod(0o600)
        manifest.write_bytes(manifest.read_bytes() + b"\n")
        manifest.chmod(0o444)
        root.chmod(0o555)
    elif corruption == "ledger_chain":
        ledger.connection.execute("DROP TRIGGER lifecycle_events_no_update")
        ledger.connection.execute(
            "UPDATE lifecycle_events SET reason_summary = 'tampered'"
        )
    else:
        monkeypatch.setattr(
            EvolutionLedger,
            "prove_committed_event",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                EvolutionLedgerError("uncommitted_ledger_evidence")
            ),
        )

    before = _snapshot(home, ledger)
    result = reconcile_evolution_state(repair=False)

    assert result.status == "base_only"
    assert result.active is None
    assert result.last_known_good is None
    assert not result.overlay_enabled
    assert _snapshot(home, ledger) == before


def test_corrupt_or_missing_ledger_is_base_only_without_attempted_append(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, data in (("missing", None), ("corrupt", b"not sqlite")):
        home = tmp_path / name
        home.mkdir(mode=0o700)
        evolution = home / "evolution"
        evolution.mkdir(mode=0o700)
        if data is not None:
            path = evolution / "evolution.db"
            path.write_bytes(data)
            path.chmod(0o600)
        monkeypatch.setenv("HERMES_HOME", str(home))
        monkeypatch.setenv("HADES_HOME", str(home))

        result = reconcile_evolution_state(repair=True)

        assert result.status == "base_only"
        assert result.diagnostics == ("ledger_unavailable",)
        assert not (evolution / "active.json").exists()
        assert not (evolution / "last-known-good.json").exists()
        if data is None:
            assert not (evolution / "evolution.db").exists()
        else:
            assert (evolution / "evolution.db").read_bytes() == data


def test_recovery_diagnostics_are_idempotent_per_condition(state) -> None:
    home, ledger, _, _, _ = state
    active_path, lkg_path = _paths(home)
    active_path.unlink()
    lkg_path.unlink()

    first = reconcile_evolution_state(repair=True)
    count = len(_events(ledger))
    second = reconcile_evolution_state(repair=True)

    assert first == second
    assert len(_events(ledger)) == count
    [recovery] = [
        event for event in _events(ledger)
        if event.event_type == "supervisor_recovery"
    ]
    assert recovery.actor == "supervisor"
    assert recovery.attempt_id is None
    assert recovery.generation_id is None
    assert recovery.authorization_id is None
    assert len(recovery.reason_code) <= 128
    assert len(recovery.reason_summary) <= 512
    assert all(len(digest) == 64 for digest in recovery.input_digests)


def test_distinct_invalid_pointer_evidence_has_distinct_idempotency_keys(
    state,
) -> None:
    home, ledger, _, _, _ = state
    active_path, lkg_path = _paths(home)
    lkg_path.unlink()
    active_path.write_bytes(b"{first corruption")
    active_path.chmod(0o600)

    first = reconcile_evolution_state(repair=True)
    first_events = [
        event
        for event in _events(ledger)
        if event.event_type == "supervisor_recovery"
    ]
    same = reconcile_evolution_state(repair=True)
    same_events = [
        event
        for event in _events(ledger)
        if event.event_type == "supervisor_recovery"
    ]
    active_path.write_bytes(b"{second corruption")
    second = reconcile_evolution_state(repair=True)
    second_events = [
        event
        for event in _events(ledger)
        if event.event_type == "supervisor_recovery"
    ]

    assert first == same == second
    assert len(first_events) == len(same_events) == 1
    assert len(second_events) == 2
    assert first_events[0].input_digests != second_events[1].input_digests
    assert all(
        len(digest) == 64
        for event in second_events
        for digest in event.input_digests
    )
    serialized = repr(second_events)
    assert "first corruption" not in serialized
    assert "second corruption" not in serialized
    assert str(active_path) not in serialized


def test_missing_and_corrupt_pointer_evidence_do_not_deduplicate(state) -> None:
    home, ledger, _, _, _ = state
    active_path, lkg_path = _paths(home)
    active_path.unlink()
    lkg_path.unlink()

    reconcile_evolution_state(repair=True)
    active_path.write_bytes(b"{corrupt")
    active_path.chmod(0o600)
    reconcile_evolution_state(repair=True)

    recovery = [
        event
        for event in _events(ledger)
        if event.event_type == "supervisor_recovery"
    ]
    assert len(recovery) == 2
    assert recovery[0].input_digests != recovery[1].input_digests


def test_restore_fault_before_rename_is_idempotently_retried(
    state,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home, ledger, _, _, _ = state
    active_path, lkg_path = _paths(home)
    active_path.unlink()
    expected = lkg_path.read_bytes()
    original = reconcile_module._restore_active_from_lkg
    calls = 0

    def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("injected before rename")
        return original(*args, **kwargs)

    monkeypatch.setattr(reconcile_module, "_restore_active_from_lkg", fail_once)
    with pytest.raises(OSError, match="injected"):
        reconcile_evolution_state(repair=True)
    count = len(_events(ledger))
    assert not active_path.exists()

    result = reconcile_evolution_state(repair=True)

    assert result.status == "restored_lkg"
    assert active_path.read_bytes() == expected
    assert len(_events(ledger)) == count


def test_failure_after_rename_converges_without_second_event(
    state,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home, ledger, _, _, _ = state
    active_path, lkg_path = _paths(home)
    active_path.unlink()
    expected = lkg_path.read_bytes()
    original = reconcile_module._restore_active_from_lkg

    def rename_then_fail(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("injected after rename")

    monkeypatch.setattr(
        reconcile_module,
        "_restore_active_from_lkg",
        rename_then_fail,
    )
    with pytest.raises(OSError, match="after rename"):
        reconcile_evolution_state(repair=True)
    count = len(_events(ledger))
    assert active_path.read_bytes() == expected

    monkeypatch.setattr(reconcile_module, "_restore_active_from_lkg", original)
    result = reconcile_evolution_state(repair=True)

    assert result.status == "coherent"
    assert len(_events(ledger)) == count


def test_repair_holds_lock_across_read_event_and_pointer_write(
    state,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home, _, _, _, _ = state
    _paths(home)[0].unlink()
    held = False
    real_lock = reconcile_module.lifecycle_lock
    real_restore = reconcile_module._restore_active_from_lkg

    from contextlib import contextmanager

    @contextmanager
    def observed_lock(*args, **kwargs):
        nonlocal held
        with real_lock(*args, **kwargs) as lease:
            held = True
            try:
                yield lease
            finally:
                held = False

    def observed_restore(*args, **kwargs):
        assert held
        return real_restore(*args, **kwargs)

    monkeypatch.setattr(reconcile_module, "lifecycle_lock", observed_lock)
    monkeypatch.setattr(
        reconcile_module,
        "_restore_active_from_lkg",
        observed_restore,
    )

    reconcile_evolution_state(repair=True)

    assert not held


def test_repair_event_lock_and_pointer_write_counts_are_exact(
    state,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home, _, _, _, _ = state
    _paths(home)[0].unlink()
    counts = {"lock": 0, "event": 0, "write": 0}
    real_lock = reconcile_module.lifecycle_lock
    real_append = EvolutionLedger.append_event
    real_restore = reconcile_module._restore_active_from_lkg

    from contextlib import contextmanager

    @contextmanager
    def counted_lock(*args, **kwargs):
        counts["lock"] += 1
        with real_lock(*args, **kwargs) as lease:
            yield lease

    def counted_append(self, *args, **kwargs):
        counts["event"] += 1
        return real_append(self, *args, **kwargs)

    def counted_restore(*args, **kwargs):
        counts["write"] += 1
        return real_restore(*args, **kwargs)

    monkeypatch.setattr(reconcile_module, "lifecycle_lock", counted_lock)
    monkeypatch.setattr(EvolutionLedger, "append_event", counted_append)
    monkeypatch.setattr(
        reconcile_module,
        "_restore_active_from_lkg",
        counted_restore,
    )

    first = reconcile_evolution_state(repair=True)
    assert first.status == "restored_lkg"
    assert len(first.diagnostics) == 1
    assert counts == {"lock": 1, "event": 1, "write": 1}

    second = reconcile_evolution_state(repair=True)
    assert second.status == "coherent"
    assert len(second.diagnostics) == 0
    assert counts == {"lock": 2, "event": 1, "write": 1}


def test_repair_passes_connection_fds_to_both_identity_checks(
    state,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[set[int] | None] = []
    real_verify = reconcile_module._verify_retained_identity

    def observed_verify(
        path,
        guard,
        connection=None,
        connection_fds=None,
    ):
        if connection is not None:
            observed.append(connection_fds)
        return real_verify(path, guard, connection, connection_fds)

    monkeypatch.setattr(
        reconcile_module,
        "_verify_retained_identity",
        observed_verify,
    )

    result = reconcile_evolution_state(repair=True)

    assert result.status == "coherent"
    assert len(observed) == 2
    assert all(descriptors for descriptors in observed)
