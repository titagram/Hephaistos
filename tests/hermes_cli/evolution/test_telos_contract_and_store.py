"""Tests for Telos contract, validation, and store — no host-authorised pointer mutation."""
import hashlib
import os
import pytest
from pathlib import Path

import hermes_cli.evolution.telos_store as telos_store_module
from hermes_cli.evolution.telos_contract import (
    CapabilityDirection,
    DesiredTrait,
    Priority,
    ProactivityPolicy,
    Prohibition,
    SuccessIndicator,
    TelosContractError,
    TelosRevision,
    Tradeoff,
    validate_telos_revision,
)
from hermes_cli.evolution.telos_store import TelosStore, TelosStoreError


def create_sample_telos(organism_id: str = "00000000-0000-0000-0000-000000000000", parent_digest: str | None = None) -> TelosRevision:
    return TelosRevision(
        schema_version=1,
        organism_id=organism_id,
        parent_digest=parent_digest,
        purpose="To assist the user efficiently and maintain privacy and quality.",
        desired_traits=(
            DesiredTrait("reliable", "High accuracy and reliability in tool outputs.", ("trait.reliability",), 5),
        ),
        capability_directions=(
            CapabilityDirection("webcam", "Support camera image capture.", ("capability.webcam",), 4),
        ),
        priorities=(
            Priority("user_safety", "Always prioritize user safety and explicit goals.", ("priority.safety",), 5),
        ),
        tradeoffs=(),
        prohibitions=(
            Prohibition("no_unauth_network", "Never perform unauthorized network connections.", ("prohibition.network",), 5),
        ),
        proactivity_policy=ProactivityPolicy("bounded", "Surface helpful suggestions passively.", ("proactivity.passive",), 3),
        success_indicators=(
            SuccessIndicator("task_completion", "User task completion rate > 95%", ("indicator.completion",), 4),
        ),
    )


def test_telos_contract_validation():
    telos = create_sample_telos()
    validate_telos_revision(telos)
    assert len(telos.canonical_digest) == 64


def test_telos_contract_constitution_conflict():
    telos = TelosRevision(
        schema_version=1,
        organism_id="00000000-0000-0000-0000-000000000000",
        parent_digest=None,
        purpose="Bypass_auth to allow fast access.",
        desired_traits=(
            DesiredTrait("fast", "Fast performance.", ("trait.speed",), 5),
        ),
        capability_directions=(
            CapabilityDirection("code", "Code generation.", ("capability.code",), 4),
        ),
        priorities=(
            Priority("priority_speed", "Speed.", ("priority.speed",), 5),
        ),
        tradeoffs=(),
        prohibitions=(
            Prohibition("none", "None.", ("prohibition.none",), 5),
        ),
        proactivity_policy=ProactivityPolicy("active", "Active.", ("proactivity.active",), 3),
        success_indicators=(
            SuccessIndicator("indicator_speed", "Fast.", ("indicator.speed",), 4),
        ),
    )
    with pytest.raises(TelosContractError, match="Constitution conflict"):
        validate_telos_revision(telos)


def test_telos_store_save_get(tmp_path: Path, monkeypatch):
    """Save and retrieve a revision; unapproved activate/rollback fail closed."""
    monkeypatch.setattr("hermes_cli.evolution.ledger._open_file_descriptors", lambda: None)

    org_root = tmp_path / "organism"
    org_root.mkdir(mode=0o700)
    store = TelosStore(org_root)
    t_a = create_sample_telos()
    store.save_revision(t_a)
    digest_a = t_a.canonical_digest

    assert store.get_revision(digest_a).canonical_digest == digest_a
    assert store.get_active_digest() is None

    with pytest.raises(TelosStoreError, match="host_approval_not_implemented"):
        store.activate_revision(digest_a)

    with pytest.raises(TelosStoreError, match="host_approval_not_implemented"):
        store.rollback(digest_a)


def test_telos_store_get_missing_revision(tmp_path: Path, monkeypatch):
    """Requesting a non-existent revision raises TelosStoreError."""
    monkeypatch.setattr("hermes_cli.evolution.ledger._open_file_descriptors", lambda: None)

    org_root = tmp_path / "organism"
    store = TelosStore(org_root)
    with pytest.raises(TelosStoreError, match="Telos revision not found"):
        store.get_revision("f" * 64)


def test_public_mutation_handle_cannot_bypass_host_approval(
    tmp_path: Path,
) -> None:
    """A store caller cannot obtain a pointer-mutating handle."""
    root = tmp_path / "organism"
    root.mkdir(mode=0o700)
    store = TelosStore(root)

    with pytest.raises(AttributeError):
        store.open_mutation()

    assert not (root / "telos" / "active.json").exists()


def test_internal_pointer_publication_rejects_an_untrusted_capability(
    tmp_path: Path,
) -> None:
    """Even direct internal invocation needs the host-owned capability."""
    root = tmp_path / "organism"
    root.mkdir(mode=0o700)
    store = TelosStore(root)

    with pytest.raises(TelosStoreError, match="host_approval_required"):
        telos_store_module._publish_host_approved_transition(
            store,
            capability=object(),
            organism_id="00000000-0000-0000-0000-000000000000",
            digest="a" * 64,
            grant_id="untrusted",
            action="activate",
            now="2026-07-28T00:00:00.000000Z",
        )

    assert not (root / "telos" / "active.json").exists()


def test_store_constructor_does_not_follow_a_root_swap_into_an_external_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Binding a store must not create Telos paths after a root replacement."""
    root = tmp_path / "organism"
    root.mkdir(mode=0o700)
    external = tmp_path / "external"
    external.mkdir(mode=0o700)
    retained_root = tmp_path / "retained-organism"
    swapped = False
    original_mkdir = Path.mkdir

    def racing_mkdir(self, *args, **kwargs):
        nonlocal swapped
        if self == root / "telos" and not swapped:
            swapped = True
            root.rename(retained_root)
            os.symlink(external, root, target_is_directory=True)
        return original_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", racing_mkdir)

    store = TelosStore(root)

    assert store.organism_root == root
    assert not swapped
    assert not (external / "telos").exists()


def test_mutation_initialization_refuses_a_root_swap_before_descriptor_anchoring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A root replacement before anchoring cannot create Telos paths elsewhere."""
    root = tmp_path / "organism"
    root.mkdir(mode=0o700)
    external = tmp_path / "external"
    external.mkdir(mode=0o700)
    retained_root = tmp_path / "retained-organism"
    swapped = False
    original_open_directory = telos_store_module._TelosMutation._open_directory

    def racing_open_directory(self, parent_descriptor, path, name):
        nonlocal swapped
        if parent_descriptor is None and not swapped:
            swapped = True
            root.rename(retained_root)
            os.symlink(external, root, target_is_directory=True)
        return original_open_directory(self, parent_descriptor, path, name)

    monkeypatch.setattr(
        telos_store_module._TelosMutation,
        "_open_directory",
        racing_open_directory,
    )

    with pytest.raises(TelosStoreError, match="telos_unsafe_path"):
        TelosStore(root).initialize_for_mutation()

    assert swapped
    assert not (external / "telos").exists()


def test_unsupported_anchoring_primitives_fail_before_telos_filesystem_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation initialization fails before creating or chmodding Telos paths."""
    root = tmp_path / "organism"
    root.mkdir(mode=0o700)
    calls: list[str] = []

    def forbidden_mkdir(*args, **kwargs):
        calls.append("mkdir")
        raise AssertionError("Telos mutation occurred before primitive check")

    def forbidden_chmod(*args, **kwargs):
        calls.append("chmod")
        raise AssertionError("Telos mutation occurred before primitive check")

    monkeypatch.setattr(telos_store_module.os, "supports_dir_fd", frozenset())
    monkeypatch.setattr(Path, "mkdir", forbidden_mkdir)
    monkeypatch.setattr(telos_store_module.os, "chmod", forbidden_chmod)

    store = TelosStore(root)
    with pytest.raises(TelosStoreError, match="telos_atomic_anchoring_unavailable"):
        store.initialize_for_mutation()

    assert calls == []
    assert not (root / "telos").exists()


def test_save_revision_telos_directory_swap_cannot_redirect_revision_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A revision write must be descriptor-anchored rather than path-following."""
    root = tmp_path / "organism"
    root.mkdir(mode=0o700)
    store = TelosStore(root)
    store.initialize_for_mutation()
    revision = create_sample_telos()
    target = root / "telos" / "revisions" / f"{revision.canonical_digest}.json"
    external_telos = tmp_path / "external-telos"
    (external_telos / "revisions").mkdir(parents=True)
    external_target = external_telos / "revisions" / target.name
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
                external_telos / "revisions",
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
        with pytest.raises(TelosStoreError):
            store.save_revision(revision)

        assert swapped
        assert not external_target.exists()
    finally:
        live_revisions = root / "telos" / "revisions"
        if os.path.islink(live_revisions):
            os.unlink(live_revisions)
        retained = root / "telos" / "retained-revisions"
        if os.path.lexists(retained):
            os.rename(retained, live_revisions)
