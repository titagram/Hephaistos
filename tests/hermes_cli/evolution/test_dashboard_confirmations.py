"""Contract tests for host-owned dashboard Telos confirmations."""

from __future__ import annotations

import importlib
import json
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import hermes_cli.evolution.telos_store as telos_store_module
from hermes_cli.evolution.dashboard_service import EvolutionDashboardConflict, EvolutionDashboardService
from hermes_cli.evolution.lifecycle_global import ensure_global_lifecycle_initialized
from hermes_cli.evolution.organism_identity import OrganismIdentity, probe_organism_identity
from hermes_cli.evolution.telos_contract import (
    CapabilityDirection,
    DesiredTrait,
    Priority,
    ProactivityPolicy,
    Prohibition,
    SuccessIndicator,
    TelosRevision,
)
from hermes_cli.evolution.telos_store import TelosStore


def _confirmations_module():
    """Fail as a test failure, not a collection error, while the feature is absent."""
    try:
        return importlib.import_module("hermes_cli.evolution.dashboard_confirmations")
    except ModuleNotFoundError:
        pytest.fail("DashboardConfirmationStore has not been implemented")


def _revision(identity: OrganismIdentity, *, parent_digest: str | None, purpose: str) -> TelosRevision:
    return TelosRevision(
        schema_version=1,
        organism_id=identity.organism_id,
        parent_digest=parent_digest,
        purpose=purpose,
        desired_traits=(DesiredTrait("reliable", "Produce reliable results.", ("reliable",), 5),),
        capability_directions=(
            CapabilityDirection("local", "Prefer local operations.", ("local",), 4),
        ),
        priorities=(Priority("safety", "Prioritize safety.", ("safety",), 5),),
        tradeoffs=(),
        prohibitions=(Prohibition("no_leaks", "Do not disclose private data.", ("privacy",), 5),),
        proactivity_policy=ProactivityPolicy("bounded", "Be bounded.", ("bounded",), 3),
        success_indicators=(SuccessIndicator("complete", "Complete tasks.", ("complete",), 4),),
    )


def _prepared_transition(tmp_path: Path):
    root = tmp_path / "organism"
    ensure_global_lifecycle_initialized(root)
    identity = probe_organism_identity(root)
    assert identity is not None
    telos = TelosStore(root)
    current = _revision(identity, parent_digest=None, purpose="Current Telos.")
    target = replace(
        _revision(identity, parent_digest=current.canonical_digest, purpose="Target Telos."),
        parent_digest=current.canonical_digest,
    )
    telos.save_revision(current)
    telos.save_revision(target)
    (root / "telos" / "active.json").write_text(
        json.dumps({"digest": current.canonical_digest}), encoding="utf-8"
    )
    service = EvolutionDashboardService(root)
    expected_snapshot_digest = service.snapshot()["snapshot_digest"]
    module = _confirmations_module()
    store = module.DashboardConfirmationStore(root)
    prepared = store.prepare(
        organism_id=identity.organism_id,
        expected_snapshot_digest=expected_snapshot_digest,
        current_digest=current.canonical_digest,
        target_digest=target.canonical_digest,
        action="activate",
    )
    return root, identity, current, target, expected_snapshot_digest, store, prepared


def _confirm(store, prepared: dict[str, str], expected_snapshot_digest: str, *, phrase: str | None = None, current_digest: str | None = None, target_digest: str | None = None, organism_id: str | None = None):
    return store.confirm(
        confirmation_id=prepared["confirmation_id"],
        organism_id=prepared["organism_id"] if organism_id is None else organism_id,
        expected_snapshot_digest=expected_snapshot_digest,
        current_digest=prepared["current_digest"] if current_digest is None else current_digest,
        target_digest=prepared["target_digest"] if target_digest is None else target_digest,
        action=prepared["action"],
        phrase=prepared["required_phrase"] if phrase is None else phrase,
    )


def test_prepare_returns_only_public_transition_fields_and_host_secret_stays_private(
    tmp_path: Path,
) -> None:
    """Returning a host session/context digest would allow a browser to forge approval."""
    _, identity, current, target, _, _, prepared = _prepared_transition(tmp_path)

    assert prepared == {
        "confirmation_id": prepared["confirmation_id"],
        "display_nonce": prepared["display_nonce"],
        "organism_id": identity.organism_id,
        "current_digest": current.canonical_digest,
        "target_digest": target.canonical_digest,
        "action": "activate",
        "expires_at": prepared["expires_at"],
        "required_phrase": (
            f"ACTIVATE {identity.organism_id[:8]} {target.canonical_digest[:12]} "
            f"{prepared['display_nonce']}"
        ),
    }
    assert "session" not in json.dumps(prepared).lower()
    assert "context_digest" not in json.dumps(prepared)


def test_phrase_failure_consumes_host_context_and_never_moves_pointer(tmp_path: Path) -> None:
    """Keeping a rejected request live would turn confirmation into a replay primitive."""
    root, _, current, _, expected, store, prepared = _prepared_transition(tmp_path)
    active = root / "telos" / "active.json"
    before = active.read_bytes()

    with pytest.raises(EvolutionDashboardConflict, match="confirmation_phrase_mismatch"):
        _confirm(store, prepared, expected, phrase="ACTIVATE wrong phrase")
    assert active.read_bytes() == before

    with pytest.raises(EvolutionDashboardConflict, match="confirmation_not_found"):
        _confirm(store, prepared, expected)
    assert json.loads(active.read_text(encoding="utf-8"))["digest"] == current.canonical_digest


def test_transition_revalidates_current_target_and_full_organism_before_pointer_write(
    tmp_path: Path,
) -> None:
    """Skipping any bound field would permit a stale or foreign confirmation to activate Telos."""
    root, identity, current, target, expected, store, prepared = _prepared_transition(tmp_path)
    active = root / "telos" / "active.json"
    changed_before = active.read_bytes()

    with pytest.raises(EvolutionDashboardConflict, match="telos_current_changed"):
        _confirm(store, prepared, expected, current_digest=target.canonical_digest)
    assert active.read_bytes() == changed_before

    _, _, _, _, expected, store, prepared = _prepared_transition(tmp_path / "foreign")
    active = tmp_path / "foreign" / "organism" / "telos" / "active.json"
    foreign_before = active.read_bytes()
    with pytest.raises(EvolutionDashboardConflict, match="organism_changed"):
        _confirm(
            store,
            prepared,
            expected,
            organism_id="00000000-0000-4000-8000-000000000000",
        )
    assert active.read_bytes() == foreign_before
    assert identity.organism_id
    assert current.canonical_digest


def test_transition_rejects_target_mismatch_expiry_and_replay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Accepting an expired or mismatched target would bypass the one-time confirmation contract."""
    root, _, current, target, expected, store, prepared = _prepared_transition(tmp_path)
    active = root / "telos" / "active.json"
    before = active.read_bytes()

    with pytest.raises(EvolutionDashboardConflict, match="telos_target_changed"):
        _confirm(store, prepared, expected, target_digest="a" * 64)
    assert active.read_bytes() == before

    _, _, _, _, expected, store, prepared = _prepared_transition(tmp_path / "expired")
    module = _confirmations_module()
    monkeypatch.setattr(module, "_utcnow", lambda: datetime.now(UTC) + timedelta(hours=1))
    with pytest.raises(EvolutionDashboardConflict, match="confirmation_expired"):
        _confirm(store, prepared, expected)
    monkeypatch.undo()

    _, _, current, target, expected, store, prepared = _prepared_transition(tmp_path / "approved")
    approved = _confirm(store, prepared, expected)
    approved_pointer = tmp_path / "approved" / "organism" / "telos" / "active.json"
    assert approved["status"] == "approved"
    assert json.loads(approved_pointer.read_text(encoding="utf-8"))["digest"] == target.canonical_digest
    with pytest.raises(EvolutionDashboardConflict, match="confirmation_not_found"):
        _confirm(store, prepared, expected)
    assert current.canonical_digest != target.canonical_digest


def test_confirmation_telos_directory_swap_cannot_redirect_pointer_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A path swap after revision validation must fail before an external pointer write."""
    root, _, _, target, expected, store, prepared = _prepared_transition(tmp_path)
    external_telos = tmp_path / "external-telos"
    external_revisions = external_telos / "revisions"
    external_revisions.mkdir(parents=True)
    revision_path = root / "telos" / "revisions" / f"{target.canonical_digest}.json"
    (external_revisions / revision_path.name).write_bytes(revision_path.read_bytes())
    active = root / "telos" / "active.json"
    active_before = active.read_bytes()
    swapped = False
    original_open_directory = telos_store_module._TelosMutation._open_directory

    def racing_open_directory(self, parent_descriptor, path, name):
        nonlocal swapped
        if name == "revisions" and not swapped:
            swapped = True
            (root / "telos" / "revisions").rename(
                root / "telos" / "retained-revisions"
            )
            os.symlink(
                external_revisions,
                root / "telos" / "revisions",
                target_is_directory=True,
            )
        return original_open_directory(self, parent_descriptor, path, name)

    monkeypatch.setattr(
        telos_store_module._TelosMutation,
        "_open_directory",
        racing_open_directory,
    )
    try:
        with pytest.raises(EvolutionDashboardConflict, match="telos_transition_rejected"):
            _confirm(store, prepared, expected)

        assert swapped
        assert active.read_bytes() == active_before
    finally:
        live_revisions = root / "telos" / "revisions"
        if os.path.islink(live_revisions):
            os.unlink(live_revisions)
        retained = root / "telos" / "retained-revisions"
        if os.path.lexists(retained):
            os.rename(retained, live_revisions)
