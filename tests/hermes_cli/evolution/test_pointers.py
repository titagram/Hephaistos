from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from hermes_cli.evolution.ledger import EvolutionLedger
from hermes_cli.evolution.pointers import (
    PointerDocument,
    PointerError,
    atomic_write_pointer,
    initialize_baseline_pointers,
    validate_pointer,
)
from hermes_cli.evolution.store import GenerationStore, StableBaseIdentity


def _baseline(store: GenerationStore):
    return store.initialize_baseline(
        StableBaseIdentity(
            release="1.0.0",
            repository_commit="a" * 40,
            compatibility_version="1",
            configuration_fingerprint="f" * 64,
        )
    )


def _initialized(tmp_path: Path):
    store = GenerationStore(tmp_path / "evolution" / "generations")
    ledger = EvolutionLedger(tmp_path / "evolution" / "evolution.db")
    baseline = _baseline(store)
    active, lkg = initialize_baseline_pointers(ledger, store, baseline)
    return ledger, store, baseline, active, lkg


@pytest.mark.parametrize(
    "field",
    [
        "profile_id",
        "generation_id",
        "manifest_digest",
        "lifecycle_sequence",
        "designated_at",
        "ledger_event_digest",
        "integrity_digest",
    ],
)
def test_validate_pointer_rejects_each_independent_tamper(
    tmp_path: Path, field: str
) -> None:
    ledger, store, baseline, active, _ = _initialized(tmp_path)
    payload = active.to_mapping()
    payload[field] = "0" * 64 if field != "lifecycle_sequence" else 2

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
    ],
)
def test_validate_pointer_rejects_closed_schema_and_noncanonical_values(
    tmp_path: Path, mutate
) -> None:
    ledger, store, _, active, _ = _initialized(tmp_path)
    payload = active.to_mapping()
    mutate(payload)

    with pytest.raises(PointerError):
        validate_pointer(payload, ledger, store)


def test_initialize_baseline_pointers_is_idempotent_and_byte_stable(
    tmp_path: Path,
) -> None:
    ledger, store, baseline, active, lkg = _initialized(tmp_path)
    active_path = store.root.parent / "active.json"
    lkg_path = store.root.parent / "last-known-good.json"
    before = (active_path.read_bytes(), lkg_path.read_bytes())

    repeated_active, repeated_lkg = initialize_baseline_pointers(ledger, store, baseline)

    assert ledger.history() == [ledger.history()[0]]
    assert repeated_active == active
    assert repeated_lkg == lkg
    assert (active_path.read_bytes(), lkg_path.read_bytes()) == before


def test_initialize_binds_the_active_profile_not_a_pointer_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HERMES_PROFILE", "operator_1")
    ledger, store, baseline, active, _ = _initialized(tmp_path)

    assert active.profile_id == "operator_1"
    payload = active.to_mapping()
    payload["profile_id"] = "default"
    with pytest.raises(PointerError):
        validate_pointer(payload, ledger, store)


def test_initialize_refuses_a_coherent_pointer_for_a_different_generation(
    tmp_path: Path,
) -> None:
    ledger, store, baseline, _, _ = _initialized(tmp_path)
    pointer_path = store.root.parent / "active.json"
    payload = json.loads(pointer_path.read_text())
    payload["generation_id"] = hashlib.sha256(b"other").hexdigest()
    payload["integrity_digest"] = "0" * 64
    pointer_path.write_bytes(json.dumps(payload).encode())

    with pytest.raises(PointerError):
        initialize_baseline_pointers(ledger, store, baseline)


def test_atomic_write_preserves_prior_document_when_file_fsync_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, _, active, _ = _initialized(tmp_path)
    pointer_path = tmp_path / "evolution" / "active-copy.json"
    atomic_write_pointer(pointer_path, active)
    before = pointer_path.read_bytes()

    def fail_fsync(descriptor: int) -> None:
        raise OSError("injected fsync failure")

    monkeypatch.setattr("hermes_cli.evolution.pointers.os.fsync", fail_fsync)
    with pytest.raises(PointerError):
        atomic_write_pointer(pointer_path, replace(active, designated_at="2026-07-23T00:00:01.000000Z"))

    assert pointer_path.read_bytes() == before
    assert list(pointer_path.parent.glob(".active.json.*")) == []


def test_atomic_write_rejects_symlink_target_and_nonprivate_parent(
    tmp_path: Path,
) -> None:
    _, store, _, active, _ = _initialized(tmp_path)
    parent = store.root.parent
    target = parent / "target.json"
    target.write_text("{}")
    link = parent / "pointer.json"
    link.symlink_to(target)

    with pytest.raises(PointerError):
        atomic_write_pointer(link, active)

    os.chmod(parent, 0o755)
    with pytest.raises(PointerError):
        atomic_write_pointer(parent / "mode.json", active)
