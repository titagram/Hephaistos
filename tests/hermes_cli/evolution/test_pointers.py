"""Real-path contracts for baseline pointer integrity and recovery."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from dataclasses import replace
from pathlib import Path

import pytest

from hermes_cli.evolution import pointers as pointer_module
from hermes_cli.evolution.contract import (
    canonical_json_bytes,
    content_digest,
)
from hermes_cli.evolution.ledger import (
    EvolutionLedger,
    LifecycleEvent,
    StoredEvent,
)
from hermes_cli.evolution.pointers import (
    PointerDocument,
    PointerError,
    atomic_write_pointer,
    initialize_baseline_pointers,
    pointer_integrity_digest,
    validate_pointer,
)
from hermes_cli.evolution.store import GenerationStore, StableBaseIdentity


def _stable_base(seed: str = "a") -> StableBaseIdentity:
    return StableBaseIdentity(
        release=f"1.0.{ord(seed) - ord('a')}",
        repository_commit=seed * 40,
        compatibility_version="1",
        configuration_fingerprint=seed * 64,
    )


def _baseline(store: GenerationStore, seed: str = "a"):
    return store.initialize_baseline(_stable_base(seed))


def _initialized(tmp_path: Path, seed: str = "a"):
    store = GenerationStore(tmp_path / "evolution" / "generations")
    ledger = EvolutionLedger(tmp_path / "evolution" / "evolution.db")
    baseline = _baseline(store, seed)
    active, lkg = initialize_baseline_pointers(ledger, store, baseline)
    return ledger, store, baseline, active, lkg


def _counts(ledger: EvolutionLedger) -> tuple[int, int, int]:
    return tuple(
        ledger.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("attempts", "generations", "lifecycle_events")
    )


def _pointer_paths(store: GenerationStore) -> tuple[Path, Path]:
    return (
        store.root.parent / "active.json",
        store.root.parent / "last-known-good.json",
    )


def _with_integrity(mapping: dict[str, object]) -> dict[str, object]:
    payload = dict(mapping)
    payload.pop("integrity_digest", None)
    payload["integrity_digest"] = pointer_integrity_digest(
        payload,
        str(payload["ledger_event_digest"]),
    )
    return payload


def _with_raw_integrity(mapping: dict[str, object]) -> dict[str, object]:
    """Recompute framing without asking production to normalize bad types."""

    payload = dict(mapping)
    payload.pop("integrity_digest", None)
    event_digest = str(payload["ledger_event_digest"])
    payload["integrity_digest"] = hashlib.sha256(
        b"hades-evolution-pointer-v1\0"
        + canonical_json_bytes(payload)
        + event_digest.encode("ascii")
    ).hexdigest()
    return payload


def _write_mapping(
    path: Path,
    mapping: dict[str, object],
    *,
    canonical: bool = True,
) -> None:
    data = (
        canonical_json_bytes(mapping)
        if canonical
        else json.dumps(mapping, indent=2, sort_keys=False).encode("utf-8")
    )
    path.chmod(0o600)
    path.write_bytes(data)
    path.chmod(0o600)


def _profile_digest(profile_id: str) -> str:
    return content_digest(
        {"profile_id": profile_id},
        domain="hades-evolution-profile-v1",
    )


def _baseline_inputs(
    generation_id: str,
    manifest_digest: str,
    profile_id: str,
) -> tuple[str, ...]:
    return (
        generation_id,
        manifest_digest,
        _profile_digest(profile_id),
    )


def _append_baseline_designation(
    ledger: EvolutionLedger,
    generation_id: str,
    manifest_digest: str,
    profile_id: str,
    *,
    created_at: str = "2026-07-23T01:02:03.000000Z",
) -> StoredEvent:
    return ledger.append_event(
        LifecycleEvent(
            event_id=str(uuid.uuid4()),
            attempt_id=None,
            generation_id=None,
            event_type="baseline_designated",
            prior_state=None,
            next_state=None,
            actor="system",
            input_digests=_baseline_inputs(
                generation_id,
                manifest_digest,
                profile_id,
            ),
            authorization_id=None,
            reason_code="baseline",
            reason_summary="baseline designation",
            created_at=created_at,
        )
    )


def _document_for(
    event: StoredEvent,
    generation_id: str,
    manifest_digest: str,
    profile_id: str,
) -> PointerDocument:
    payload: dict[str, object] = {
        "schema_version": 1,
        "profile_id": profile_id,
        "generation_id": generation_id,
        "manifest_digest": manifest_digest,
        "lifecycle_sequence": event.event_sequence,
        "designated_at": event.created_at,
        "ledger_event_digest": event.event_digest,
    }
    return PointerDocument(
        **payload,
        integrity_digest=pointer_integrity_digest(
            payload,
            event.event_digest,
        ),
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


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("profile_id", "other-profile"),
        ("generation_id", "0" * 64),
        ("manifest_digest", "1" * 64),
        ("lifecycle_sequence", 999),
        ("designated_at", "2026-07-23T00:00:00.000000Z"),
        ("ledger_event_digest", "2" * 64),
        ("integrity_digest", "3" * 64),
    ],
)
def test_validate_pointer_rejects_each_independent_tamper_with_integrity_recomputed(
    tmp_path: Path,
    field: str,
    replacement: object,
) -> None:
    ledger, store, baseline, active, _ = _initialized(tmp_path)
    payload = active.to_mapping()
    payload[field] = replacement
    if field != "integrity_digest":
        payload = _with_integrity(payload)

    with pytest.raises(PointerError):
        validate_pointer(payload, ledger, store)

    assert store.verify(baseline.generation_id).generation_id == baseline.generation_id


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.__setitem__("unknown", "value"),
        lambda value: value.pop("profile_id"),
        lambda value: value.__setitem__("schema_version", True),
        lambda value: value.__setitem__("lifecycle_sequence", 1.0),
        lambda value: value.__setitem__("designated_at", "2026-07-23T00:00:00Z"),
        lambda value: value.__setitem__("generation_id", "F" * 64),
        lambda value: value.__setitem__("ledger_event_digest", "f" * 63),
    ],
)
def test_validate_pointer_rejects_closed_schema_and_noncanonical_values(
    tmp_path: Path,
    mutate,
) -> None:
    ledger, store, _, active, _ = _initialized(tmp_path)
    payload = active.to_mapping()
    mutate(payload)

    with pytest.raises(PointerError):
        validate_pointer(payload, ledger, store)


class _IntegerSubclass(int):
    pass


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 1.0),
        ("schema_version", _IntegerSubclass(1)),
        ("lifecycle_sequence", _IntegerSubclass(1)),
    ],
)
def test_pointer_integer_fields_require_exact_builtin_int(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    ledger, store, _, active, _ = _initialized(tmp_path)
    payload = active.to_mapping()
    payload[field] = value
    payload = _with_raw_integrity(payload)

    with pytest.raises(PointerError):
        validate_pointer(payload, ledger, store)


def test_named_profile_identity_comes_from_canonical_profile_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    default_home = tmp_path / ".hermes"
    profile_home = default_home / "profiles" / "operator_1"
    profile_home.mkdir(parents=True, mode=0o700)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(profile_home))
    monkeypatch.setenv("HADES_HOME", str(profile_home))
    monkeypatch.setenv("HERMES_PROFILE", "wrong-environment-claim")

    _, _, _, active, _ = _initialized(tmp_path / "state")

    assert active.profile_id == "operator_1"


def test_custom_home_profile_identity_is_stable_private_and_path_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_home = tmp_path / "private-customer-a"
    first_home.mkdir(mode=0o700)
    monkeypatch.setenv("HERMES_HOME", str(first_home))
    monkeypatch.setenv("HADES_HOME", str(first_home))
    first = pointer_module._active_profile()
    repeated = pointer_module._active_profile()

    second_home = tmp_path / "private-customer-b"
    second_home.mkdir(mode=0o700)
    monkeypatch.setenv("HERMES_HOME", str(second_home))
    monkeypatch.setenv("HADES_HOME", str(second_home))
    second = pointer_module._active_profile()

    assert first == repeated
    assert first.startswith("custom-")
    assert len(first) <= 64
    assert str(first_home) not in first
    assert first != second


def test_store_exposes_exact_canonical_manifest_bytes_and_digest(
    tmp_path: Path,
) -> None:
    store = GenerationStore(tmp_path / "evolution" / "generations")
    baseline = _baseline(store)
    raw = (baseline.root / "manifest.json").read_bytes()

    descriptor = store.verified_manifest_descriptor(baseline.generation_id)

    assert descriptor.generation == baseline
    assert descriptor.manifest_bytes == raw
    assert descriptor.manifest_digest == hashlib.sha256(raw).hexdigest()
    assert raw == canonical_json_bytes(dict(baseline.manifest))


def test_store_and_pointer_reject_semantically_equal_noncanonical_manifest_bytes(
    tmp_path: Path,
) -> None:
    ledger, store, _, active, _ = _initialized(tmp_path)
    manifest_path = store.root / active.generation_id / "manifest.json"
    generation_root = manifest_path.parent
    generation_root.chmod(0o755)
    manifest_path.chmod(0o600)
    manifest = json.loads(manifest_path.read_bytes())
    manifest_path.write_bytes(json.dumps(manifest, indent=2).encode("utf-8"))
    manifest_path.chmod(0o444)
    generation_root.chmod(0o555)

    with pytest.raises(ValueError, match="integrity"):
        store.verified_manifest_descriptor(active.generation_id)
    with pytest.raises(PointerError):
        validate_pointer(active.to_mapping(), ledger, store)


def test_pointer_manifest_digest_hashes_the_exact_published_bytes(
    tmp_path: Path,
) -> None:
    _, store, _, active, _ = _initialized(tmp_path)
    raw = (store.root / active.generation_id / "manifest.json").read_bytes()

    assert active.manifest_digest == hashlib.sha256(raw).hexdigest()


def test_initialize_baseline_pointers_is_idempotent_and_byte_stable(
    tmp_path: Path,
) -> None:
    ledger, store, baseline, active, lkg = _initialized(tmp_path)
    active_path, lkg_path = _pointer_paths(store)
    before = (active_path.read_bytes(), lkg_path.read_bytes())
    counts = _counts(ledger)

    repeated_active, repeated_lkg = initialize_baseline_pointers(
        ledger,
        store,
        baseline,
    )

    assert _counts(ledger) == counts
    assert repeated_active == active
    assert repeated_lkg == lkg
    assert (active_path.read_bytes(), lkg_path.read_bytes()) == before


def test_baseline_initialization_has_only_one_designation_ledger_side_effect(
    tmp_path: Path,
) -> None:
    ledger, _, _, _, _ = _initialized(tmp_path)

    assert _counts(ledger) == (0, 0, 1)
    [event] = ledger.history()
    assert event.event_type == "baseline_designated"
    assert event.generation_id is None
    assert event.attempt_id is None


def test_reentry_paginates_complete_history_past_one_thousand_events(
    tmp_path: Path,
) -> None:
    store = GenerationStore(tmp_path / "evolution" / "generations")
    ledger = EvolutionLedger(tmp_path / "evolution" / "evolution.db")
    attempt_id = ledger.create_attempt("manual", "history")
    with ledger.transaction() as connection:
        for index in range(1001):
            ledger._append(
                connection,
                LifecycleEvent(
                    event_id=f"history-{index}",
                    attempt_id=attempt_id,
                    generation_id=None,
                    event_type="observation",
                    prior_state=None,
                    next_state=None,
                    actor="system",
                    input_digests=("e" * 64,),
                    authorization_id=None,
                    reason_code="history",
                    reason_summary="history filler",
                    created_at="2026-07-23T00:00:00.000000Z",
                ),
            )
    baseline = _baseline(store)
    initialize_baseline_pointers(ledger, store, baseline)
    before = _counts(ledger)

    initialize_baseline_pointers(ledger, store, baseline)

    assert _counts(ledger) == before
    assert sum(
        event.event_type == "baseline_designated"
        for event in _all_events(ledger)
    ) == 1


def test_reentry_rejects_duplicate_baseline_designation_events(
    tmp_path: Path,
) -> None:
    ledger, store, baseline, active, _ = _initialized(tmp_path)
    _append_baseline_designation(
        ledger,
        baseline.generation_id,
        active.manifest_digest,
        active.profile_id,
    )
    before = _counts(ledger)

    with pytest.raises(PointerError):
        initialize_baseline_pointers(ledger, store, baseline)

    assert _counts(ledger) == before


@pytest.mark.parametrize("mutation", ["reverse", "duplicate"])
def test_event_input_digests_require_exact_order_and_cardinality(
    tmp_path: Path,
    mutation: str,
) -> None:
    ledger, store, _, active, _ = _initialized(tmp_path)
    event = ledger.history()[0]
    changed_inputs = (
        tuple(reversed(event.input_digests))
        if mutation == "reverse"
        else (*event.input_digests, event.input_digests[0])
    )
    changed = replace(event, input_digests=changed_inputs)
    changed_digest = content_digest(
        ledger._payload(changed, event.previous_event_digest),
        domain="hermes-evolution-lifecycle-event-v1",
    )
    ledger.connection.execute("DROP TRIGGER lifecycle_events_no_update")
    ledger.connection.execute(
        """
        UPDATE lifecycle_events
        SET input_digests_json = ?, event_digest = ?
        WHERE event_sequence = ?
        """,
        (
            canonical_json_bytes(list(changed_inputs)).decode("utf-8"),
            changed_digest,
            event.event_sequence,
        ),
    )
    payload = active.to_mapping()
    payload["ledger_event_digest"] = changed_digest
    payload = _with_integrity(payload)

    assert ledger.verify_chain() == []
    with pytest.raises(PointerError):
        validate_pointer(payload, ledger, store)


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("event_type", "not_baseline"),
        (
            "created_at",
            "2026-07-23T04:05:06.000000Z",
        ),
    ],
)
def test_pointer_rejects_rehashed_ledger_field_tamper(
    tmp_path: Path,
    column: str,
    value: object,
) -> None:
    ledger, store, _, active, _ = _initialized(tmp_path)
    event = ledger.history()[0]
    changed = replace(event, **{column: value})
    changed_digest = content_digest(
        ledger._payload(changed, event.previous_event_digest),
        domain="hermes-evolution-lifecycle-event-v1",
    )
    ledger.connection.execute("DROP TRIGGER lifecycle_events_no_update")
    ledger.connection.execute(
        f"""
        UPDATE lifecycle_events
        SET {column} = ?, event_digest = ?
        WHERE event_sequence = ?
        """,
        (value, changed_digest, event.event_sequence),
    )
    payload = active.to_mapping()
    payload["ledger_event_digest"] = changed_digest
    payload = _with_integrity(payload)

    assert ledger.verify_chain() == []
    with pytest.raises(PointerError):
        validate_pointer(payload, ledger, store)


def test_pointer_rejects_rehashed_ledger_generation_binding_tamper(
    tmp_path: Path,
) -> None:
    ledger, store, _, active, _ = _initialized(tmp_path)
    event = ledger.history()[0]
    changed_generation = (
        active.generation_id
        if event.generation_id is None
        else None
    )
    changed = replace(event, generation_id=changed_generation)
    changed_digest = content_digest(
        ledger._payload(changed, event.previous_event_digest),
        domain="hermes-evolution-lifecycle-event-v1",
    )
    ledger.connection.execute("PRAGMA foreign_keys=OFF")
    ledger.connection.execute("DROP TRIGGER lifecycle_events_no_update")
    ledger.connection.execute(
        """
        UPDATE lifecycle_events
        SET generation_id = ?, event_digest = ?
        WHERE event_sequence = ?
        """,
        (
            changed_generation,
            changed_digest,
            event.event_sequence,
        ),
    )
    payload = active.to_mapping()
    payload["ledger_event_digest"] = changed_digest
    payload = _with_integrity(payload)

    assert ledger.verify_chain() == []
    with pytest.raises(PointerError):
        validate_pointer(payload, ledger, store)


def test_pointer_rejects_rehashed_ledger_event_digest_tamper(
    tmp_path: Path,
) -> None:
    ledger, store, _, active, _ = _initialized(tmp_path)
    forged_digest = "d" * 64
    ledger.connection.execute("DROP TRIGGER lifecycle_events_no_update")
    ledger.connection.execute(
        "UPDATE lifecycle_events SET event_digest = ?",
        (forged_digest,),
    )
    payload = active.to_mapping()
    payload["ledger_event_digest"] = forged_digest
    payload = _with_integrity(payload)

    assert ledger.verify_chain()
    with pytest.raises(PointerError):
        validate_pointer(payload, ledger, store)


def test_pointer_rejects_ledger_event_sequence_tamper(
    tmp_path: Path,
) -> None:
    ledger, store, _, active, _ = _initialized(tmp_path)
    ledger.connection.execute("DROP TRIGGER lifecycle_events_no_update")
    ledger.connection.execute(
        "UPDATE lifecycle_events SET event_sequence = 2 WHERE event_sequence = 1"
    )

    assert ledger.verify_chain() == []
    with pytest.raises(PointerError):
        validate_pointer(
            _with_integrity(active.to_mapping()),
            ledger,
            store,
        )


def test_pointer_rejects_broken_ledger_chain_with_integrity_recomputed(
    tmp_path: Path,
) -> None:
    ledger, store, _, active, _ = _initialized(tmp_path)
    ledger.append_event(
        LifecycleEvent(
            event_id="after-baseline",
            attempt_id=None,
            generation_id=None,
            event_type="observation",
            prior_state=None,
            next_state=None,
            actor="system",
            input_digests=("a" * 64,),
            authorization_id=None,
            reason_code="observation",
            reason_summary="after baseline",
            created_at="2026-07-23T01:00:00.000000Z",
        )
    )
    ledger.connection.execute("DROP TRIGGER lifecycle_events_no_update")
    ledger.connection.execute(
        "UPDATE lifecycle_events SET reason_summary = 'tampered' WHERE event_sequence = 1"
    )

    assert ledger.verify_chain()
    with pytest.raises(PointerError):
        validate_pointer(_with_integrity(active.to_mapping()), ledger, store)


def test_pointer_rejects_missing_generation(
    tmp_path: Path,
) -> None:
    ledger, store, _, active, _ = _initialized(tmp_path)
    generation = store.root / active.generation_id
    generation.rename(store.root / f".removed-{active.generation_id}")

    with pytest.raises(PointerError):
        validate_pointer(active.to_mapping(), ledger, store)


def test_invalid_existing_pointer_is_rejected_before_any_ledger_mutation(
    tmp_path: Path,
) -> None:
    store = GenerationStore(tmp_path / "evolution" / "generations")
    ledger = EvolutionLedger(tmp_path / "evolution" / "evolution.db")
    baseline = _baseline(store)
    active_path, _ = _pointer_paths(store)
    active_path.write_bytes(b"{not-json")
    active_path.chmod(0o600)

    with pytest.raises(PointerError):
        initialize_baseline_pointers(ledger, store, baseline)

    assert _counts(ledger) == (0, 0, 0)


def test_noncanonical_existing_pointer_json_is_rejected_without_mutation(
    tmp_path: Path,
) -> None:
    ledger, store, baseline, active, _ = _initialized(tmp_path)
    active_path, _ = _pointer_paths(store)
    _write_mapping(active_path, active.to_mapping(), canonical=False)
    before = _counts(ledger)

    with pytest.raises(PointerError):
        initialize_baseline_pointers(ledger, store, baseline)

    assert _counts(ledger) == before


def test_partial_pointer_state_is_completed_from_same_event_and_bytes(
    tmp_path: Path,
) -> None:
    ledger, store, baseline, _, _ = _initialized(tmp_path)
    active_path, lkg_path = _pointer_paths(store)
    active_bytes = active_path.read_bytes()
    lkg_path.unlink()
    before = _counts(ledger)

    active, lkg = initialize_baseline_pointers(ledger, store, baseline)

    assert active == lkg
    assert lkg_path.read_bytes() == active_bytes
    assert _counts(ledger) == before


def test_second_pointer_write_fault_recovers_from_the_committed_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = GenerationStore(tmp_path / "evolution" / "generations")
    ledger = EvolutionLedger(tmp_path / "evolution" / "evolution.db")
    baseline = _baseline(store)
    original_write = pointer_module.atomic_write_pointer
    reached = 0

    def fail_second(path: Path, document: PointerDocument) -> None:
        nonlocal reached
        reached += 1
        if reached == 2:
            raise PointerError("injected second pointer failure")
        original_write(path, document)

    monkeypatch.setattr(
        pointer_module,
        "atomic_write_pointer",
        fail_second,
    )
    with pytest.raises(PointerError):
        initialize_baseline_pointers(ledger, store, baseline)

    active_path, lkg_path = _pointer_paths(store)
    assert reached == 2
    assert active_path.exists()
    assert not lkg_path.exists()
    assert _counts(ledger) == (0, 0, 1)

    monkeypatch.setattr(
        pointer_module,
        "atomic_write_pointer",
        original_write,
    )
    recovered_active, recovered_lkg = initialize_baseline_pointers(
        ledger,
        store,
        baseline,
    )

    assert recovered_active == recovered_lkg
    assert active_path.read_bytes() == lkg_path.read_bytes()
    assert _counts(ledger) == (0, 0, 1)


def test_coherent_other_generation_is_refused_without_ledger_mutation(
    tmp_path: Path,
) -> None:
    ledger, store, other, _, _ = _initialized(tmp_path, "a")
    requested = _baseline(store, "b")
    assert requested.generation_id != other.generation_id
    before = _counts(ledger)

    with pytest.raises(PointerError):
        initialize_baseline_pointers(ledger, store, requested)

    assert _counts(ledger) == before


def test_divergent_pointer_documents_are_rejected_even_for_same_generation(
    tmp_path: Path,
) -> None:
    ledger, store, baseline, active, _ = _initialized(tmp_path)
    second = _append_baseline_designation(
        ledger,
        baseline.generation_id,
        active.manifest_digest,
        active.profile_id,
    )
    divergent = _document_for(
        second,
        baseline.generation_id,
        active.manifest_digest,
        active.profile_id,
    )
    _, lkg_path = _pointer_paths(store)
    _write_mapping(lkg_path, divergent.to_mapping())
    before = _counts(ledger)

    with pytest.raises(PointerError):
        initialize_baseline_pointers(ledger, store, baseline)

    assert _counts(ledger) == before


@pytest.mark.parametrize("kind", ["mode", "hardlink", "symlink"])
def test_pointer_reads_reject_unsafe_target_metadata(
    tmp_path: Path,
    kind: str,
) -> None:
    ledger, store, baseline, _, _ = _initialized(tmp_path)
    active_path, lkg_path = _pointer_paths(store)
    if kind == "mode":
        active_path.chmod(0o640)
    elif kind == "hardlink":
        lkg_path.unlink()
        os.link(active_path, lkg_path)
    else:
        lkg_path.unlink()
        lkg_path.symlink_to(active_path.name)
    before = _counts(ledger)

    with pytest.raises(PointerError):
        initialize_baseline_pointers(ledger, store, baseline)

    assert _counts(ledger) == before


def test_pointer_read_rejects_apparent_target_owner_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger, store, baseline, _, _ = _initialized(tmp_path)
    active_path, _ = _pointer_paths(store)
    active_inode = active_path.stat().st_ino
    original_fstat = os.fstat
    reached = 0

    def forged_owner(descriptor: int):
        nonlocal reached
        info = original_fstat(descriptor)
        if info.st_ino == active_inode:
            reached += 1
            fields = list(info)
            fields[4] = os.geteuid() + 1
            return os.stat_result(fields)
        return info

    monkeypatch.setattr(pointer_module.os, "fstat", forged_owner)

    with pytest.raises(PointerError):
        initialize_baseline_pointers(ledger, store, baseline)

    assert reached >= 1


def _copy_target(
    tmp_path: Path,
) -> tuple[Path, PointerDocument, bytes]:
    _, store, _, active, _ = _initialized(tmp_path)
    path = store.root.parent / "pointer-copy.json"
    atomic_write_pointer(path, active)
    return path, active, path.read_bytes()


def test_atomic_write_retries_real_short_writes_until_complete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, active, _ = _copy_target(tmp_path)
    original_write = os.write
    reached = 0

    def short_write(descriptor: int, data) -> int:
        nonlocal reached
        reached += 1
        size = max(1, len(data) // 2)
        return original_write(descriptor, data[:size])

    monkeypatch.setattr(pointer_module.os, "write", short_write)

    atomic_write_pointer(path, active)

    assert reached > 1
    assert path.read_bytes() == canonical_json_bytes(active.to_mapping())


def test_atomic_write_zero_write_preserves_prior_and_foreign_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, active, before = _copy_target(tmp_path)
    foreign = path.parent / f".{path.name}.foreign"
    foreign.write_text("owned by another operation")
    foreign.chmod(0o600)
    reached = 0

    def zero_write(_descriptor: int, _data) -> int:
        nonlocal reached
        reached += 1
        return 0

    monkeypatch.setattr(pointer_module.os, "write", zero_write)

    with pytest.raises(PointerError):
        atomic_write_pointer(path, active)

    assert reached == 1
    assert path.read_bytes() == before
    assert foreign.read_text() == "owned by another operation"
    assert set(path.parent.glob(f".{path.name}.*")) == {foreign}


def test_atomic_write_file_fsync_failure_is_reached_and_preserves_prior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, active, before = _copy_target(tmp_path)
    original_fsync = os.fsync
    reached = 0

    def fail_file_fsync(descriptor: int) -> None:
        nonlocal reached
        if stat.S_ISREG(os.fstat(descriptor).st_mode):
            reached += 1
            raise OSError("injected file fsync failure")
        original_fsync(descriptor)

    monkeypatch.setattr(pointer_module.os, "fsync", fail_file_fsync)

    with pytest.raises(PointerError):
        atomic_write_pointer(path, active)

    assert reached == 1
    assert path.read_bytes() == before


def test_atomic_write_rename_failure_is_reached_and_preserves_prior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, active, before = _copy_target(tmp_path)
    reached = 0

    def fail_replace(*_args, **_kwargs) -> None:
        nonlocal reached
        reached += 1
        raise OSError("injected replace failure")

    monkeypatch.setattr(pointer_module.os, "replace", fail_replace)

    with pytest.raises(PointerError):
        atomic_write_pointer(path, active)

    assert reached == 1
    assert path.read_bytes() == before


def test_atomic_write_directory_fsync_failure_leaves_complete_new_document(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, active, _ = _copy_target(tmp_path)
    original_fsync = os.fsync
    reached = 0

    def fail_directory_fsync(descriptor: int) -> None:
        nonlocal reached
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            reached += 1
            raise OSError("injected directory fsync failure")
        original_fsync(descriptor)

    monkeypatch.setattr(pointer_module.os, "fsync", fail_directory_fsync)

    with pytest.raises(PointerError):
        atomic_write_pointer(path, active)

    assert reached == 1
    assert json.loads(path.read_bytes()) == active.to_mapping()
    assert path.read_bytes() == canonical_json_bytes(active.to_mapping())


def test_atomic_write_fchmods_new_file_to_exact_mode_under_hostile_umask(
    tmp_path: Path,
) -> None:
    _, store, _, active, _ = _initialized(tmp_path)
    path = store.root.parent / "umask-pointer.json"
    old_umask = os.umask(0)
    try:
        atomic_write_pointer(path, active)
    finally:
        os.umask(old_umask)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_atomic_write_rejects_symlink_parent_without_touching_target(
    tmp_path: Path,
) -> None:
    _, store, _, active, _ = _initialized(tmp_path)
    actual = tmp_path / "actual"
    actual.mkdir(mode=0o700)
    linked = tmp_path / "linked"
    linked.symlink_to(actual, target_is_directory=True)

    with pytest.raises(PointerError):
        atomic_write_pointer(linked / "active.json", active)

    assert not (actual / "active.json").exists()


def test_atomic_write_rejects_nonprivate_or_wrong_owner_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, store, _, active, _ = _initialized(tmp_path)
    parent = tmp_path / "pointer-parent"
    parent.mkdir(mode=0o700)
    parent.chmod(0o755)
    with pytest.raises(PointerError):
        atomic_write_pointer(parent / "active.json", active)

    parent.chmod(0o700)
    monkeypatch.setattr(pointer_module.os, "geteuid", lambda: os.getuid() + 1)
    with pytest.raises(PointerError):
        atomic_write_pointer(parent / "active.json", active)


def test_atomic_write_rejects_existing_target_with_extra_link(
    tmp_path: Path,
) -> None:
    path, active, before = _copy_target(tmp_path)
    alias = path.with_name("pointer-alias.json")
    os.link(path, alias)

    with pytest.raises(PointerError):
        atomic_write_pointer(path, active)

    assert path.read_bytes() == before
    assert alias.read_bytes() == before
