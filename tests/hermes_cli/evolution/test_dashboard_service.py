"""Behavior contracts for the read-only Evolution dashboard snapshot."""

from __future__ import annotations

import copy
import json
import stat
import shutil
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import hermes_cli.evolution.dashboard_service as dashboard_module
from hermes_cli.evolution.dashboard_service import (
    EvolutionDashboardConflict,
    EvolutionDashboardService,
)
from hermes_cli.evolution.lifecycle_global import ensure_global_lifecycle_initialized
from hermes_cli.evolution.organism_identity import (
    OrganismIdentity,
    create_organism_identity,
    probe_organism_identity,
)
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
from hermes_cli.evolution.observation_contract import ObservationEnvelope
from hermes_cli.evolution.observer_policy import OpportunityScore
from hermes_cli.evolution.suggestions import SuggestionRepository
from hermes_cli.evolution.blueprint_contract import blueprint_document_from_suggestion
from hermes_cli.evolution.blueprint_repository import BlueprintRepository
from hermes_cli.evolution.ledger import EvolutionLedger, LifecycleEvent
from hermes_cli.gnothi.contract import add_edge, add_node, new_artifact
from hermes_cli.gnothi.store import OrganismRevisionStore


def _publish_gnothi(
    root: Path,
    identity: OrganismIdentity,
    *,
    coverage: dict[str, dict[str, object]],
) -> None:
    artifact = new_artifact(
        revision_id="rev-dashboard-contract",
        generation_id=identity.lineage_root_digest,
        generation_scope="stable",
        head_commit="a" * 40,
        collected_at="2026-07-28T12:00:00Z",
    )
    artifact["organism_contract"].update(
        status=(
            "current"
            if all(row["status"] == "current" for row in coverage.values())
            else "partial"
        ),
        coverage=coverage,
    )
    OrganismRevisionStore(root / "gnothi_seauton").publish(
        artifact,
        published_at="2026-07-28T12:00:00Z",
    )


def _current_coverage() -> dict[str, dict[str, object]]:
    return {
        name: {"status": "current", "fingerprint": f"sha256:{name}"}
        for name in ("source", "capabilities", "runtime", "contracts")
    }


def _graph_artifact(
    identity: OrganismIdentity,
    *,
    revision_id: str,
    available: bool,
) -> dict[str, object]:
    artifact = new_artifact(
        revision_id=revision_id,
        generation_id=identity.lineage_root_digest,
        generation_scope="stable",
        head_commit="a" * 40,
        collected_at="2026-07-28T12:00:00Z",
    )
    artifact["organism_contract"].update(
        status="current",
        coverage=_current_coverage(),
    )
    add_node(
        artifact,
        node_id="capability:alpha",
        kind="capability",
        label=f"Alpha {identity.organism_id}",
        owner_class="core",
        owner_id=identity.organism_id,
        state={"available": available},
        evidence_refs=["evidence:alpha"],
    )
    add_node(
        artifact,
        node_id="provider:terminal",
        kind="provider",
        label="Terminal provider",
        owner_class="core",
        owner_id="hermes",
        state={"available": True},
        evidence_refs=["evidence:provider"],
    )
    add_edge(
        artifact,
        edge_id="edge:provides",
        kind="provides",
        source="provider:terminal",
        target="capability:alpha",
        evidence_refs=["evidence:provider"],
    )
    return artifact


def _write_telos(root: Path, identity: OrganismIdentity) -> str:
    revision = TelosRevision(
        schema_version=1,
        organism_id=identity.organism_id,
        parent_digest=None,
        purpose="Assist the user while preserving privacy and quality.",
        desired_traits=(
            DesiredTrait(
                "reliable", "Produce reliable results.", ("trait.reliable",), 5
            ),
        ),
        capability_directions=(
            CapabilityDirection(
                "local", "Prefer local operations.", ("capability.local",), 4
            ),
        ),
        priorities=(
            Priority("safety", "Prioritize user safety.", ("priority.safety",), 5),
        ),
        tradeoffs=(),
        prohibitions=(
            Prohibition(
                "no_leaks", "Do not disclose private data.", ("prohibition.privacy",), 5
            ),
        ),
        proactivity_policy=ProactivityPolicy(
            "bounded", "Offer bounded helpful suggestions.", ("proactivity.bounded",), 3
        ),
        success_indicators=(
            SuccessIndicator(
                "complete", "Complete requested tasks.", ("indicator.complete",), 4
            ),
        ),
    )
    digest = revision.canonical_digest
    revisions = root / "revisions"
    revisions.mkdir(parents=True)
    (revisions / f"{digest}.json").write_text(
        revision.to_canonical_json(), encoding="utf-8"
    )
    (root / "active.json").write_text(json.dumps({"digest": digest}), encoding="utf-8")
    return digest


def _dashboard_telos(
    identity: OrganismIdentity, *, parent_digest: str | None
) -> TelosRevision:
    return TelosRevision(
        schema_version=1,
        organism_id=identity.organism_id,
        parent_digest=parent_digest,
        purpose="Assist the user while preserving privacy and quality.",
        desired_traits=(
            DesiredTrait(
                "reliable", "Produce reliable results.", ("trait.reliable",), 5
            ),
        ),
        capability_directions=(
            CapabilityDirection(
                "local", "Prefer local operations.", ("capability.local",), 4
            ),
        ),
        priorities=(
            Priority("safety", "Prioritize user safety.", ("priority.safety",), 5),
        ),
        tradeoffs=(),
        prohibitions=(
            Prohibition(
                "no_leaks", "Do not disclose private data.", ("prohibition.privacy",), 5
            ),
        ),
        proactivity_policy=ProactivityPolicy(
            "bounded", "Offer bounded helpful suggestions.", ("proactivity.bounded",), 3
        ),
        success_indicators=(
            SuccessIndicator(
                "complete", "Complete requested tasks.", ("indicator.complete",), 4
            ),
        ),
    )


def _observation(
    identity: OrganismIdentity,
    *,
    event_id: str,
    capability: str,
) -> ObservationEnvelope:
    return ObservationEnvelope(
        schema_version=1,
        event_id=event_id,
        organism_id=identity.organism_id,
        occurred_at="2026-07-28T12:00:00.000000Z",
        signal_type="capability_absence",
        provenance="explicit_user",
        source_profile_ref=f"profile_{event_id}",
        source_project_ref="project",
        source_session_ref=f"session_{event_id}",
        generation_id="a" * 64,
        gnothi_revision_digest=None,
        telos_digest=None,
        capability_key=capability,
        operation_key="operate",
        outcome_key="missing",
        constraint_key="none",
        severity="high",
        task_impact="high",
        retry_count=0,
        latency_bucket=None,
        explicit_user_intent=True,
        recovered=False,
        evidence_refs=(),
        redaction_status="verified_redacted",
    )


def _score() -> OpportunityScore:
    return OpportunityScore(
        score=0.9,
        user_intent=1.0,
        telos_alignment=0.75,
        impact=0.8,
        recurrence=0.1,
        confidence=0.95,
        reuse=0.25,
        risk=0.25,
        expected_cost=0.25,
    )


def _seed_governance_state(
    root: Path,
) -> tuple[OrganismIdentity, EvolutionLedger, tuple[str, str]]:
    """Build coherent local governance records through their public services."""
    identity = create_organism_identity(root)
    ledger = EvolutionLedger(root / "evolution" / "evolution.db")
    telos_store = TelosStore(root)
    grandparent = _dashboard_telos(identity, parent_digest=None)
    parent = replace(grandparent, parent_digest=grandparent.canonical_digest)
    active = replace(parent, parent_digest=parent.canonical_digest)
    telos_store.save_revision(grandparent)
    telos_store.save_revision(parent)
    telos_store.save_revision(active)
    (root / "telos" / "active.json").write_text(
        json.dumps({"digest": active.canonical_digest}), encoding="utf-8"
    )

    repository = SuggestionRepository(root / "evolution" / "evolution.db")
    safe_first = repository.upsert_suggestion(
        opportunity_key="b" * 64,
        initial_state="eligible",
        active_telos_digest=active.canonical_digest,
        score=_score(),
        envelopes=(_observation(identity, event_id="event-alpha", capability="alpha"),),
        summary_reason="Recurring local capability gap",
    )
    safe_second = repository.upsert_suggestion(
        opportunity_key="c" * 64,
        initial_state="eligible",
        active_telos_digest=active.canonical_digest,
        score=_score(),
        envelopes=(_observation(identity, event_id="event-beta", capability="beta"),),
        summary_reason="Recurring local reliability gap",
    )
    repository.upsert_suggestion(
        opportunity_key="d" * 64,
        initial_state="observing",
        active_telos_digest=active.canonical_digest,
        score=_score(),
        envelopes=(
            _observation(identity, event_id="event-private", capability="private"),
        ),
        summary_reason="Observed at /private/dashboard-secret",
    )
    blueprints = BlueprintRepository(ledger)
    first = blueprints.create_or_get(
        blueprint_document_from_suggestion(
            safe_first, active_telos_digest=active.canonical_digest
        )
    )
    second = blueprints.create_or_get(
        blueprint_document_from_suggestion(
            safe_second, active_telos_digest=active.canonical_digest
        )
    )
    ledger.append_event(
        LifecycleEvent(
            event_id="dashboard-audit-event",
            attempt_id=None,
            generation_id=None,
            event_type="dashboard_observed",
            prior_state="draft",
            next_state="draft",
            actor="operator",
            input_digests=(active.canonical_digest,),
            authorization_id=None,
            reason_code="dashboard_observed",
            reason_summary="Observed /private/dashboard-secret safely",
            created_at="2026-07-28T12:01:00.000000Z",
        )
    )
    return identity, ledger, (first.attempt_id, second.attempt_id)


def test_snapshot_missing_is_bounded_and_non_mutating(tmp_path: Path) -> None:
    """A read of no organism reports absence without initializing it."""
    root = tmp_path / "organism"

    result = EvolutionDashboardService(root).snapshot()

    assert result["state"] == "missing"
    assert result["organism"] is None
    assert result["gnothi"]["state"] == "missing"
    assert result["telos"]["state"] == "missing"
    assert result["observer"]["state"] == "not_ready"
    assert len(result["snapshot_digest"]) == 64
    assert not root.exists()


def test_snapshot_corrupt_identity_fails_closed_with_sanitized_diagnostic(
    tmp_path: Path,
) -> None:
    """Malformed identity must win state precedence without leaking its contents."""
    root = tmp_path / "organism"
    root.mkdir()
    (root / "identity.json").write_text(
        '{"organism_id":"private-secret"}', encoding="utf-8"
    )

    result = EvolutionDashboardService(root).snapshot()

    assert result["state"] == "corrupt"
    assert result["organism"] is None
    assert result["diagnostics"] == ["identity_corrupt"]
    assert "private-secret" not in json.dumps(result)


def test_snapshot_missing_gnothi_pointer_is_partial_after_lifecycle_init(
    tmp_path: Path,
) -> None:
    """An initialized lifecycle with no Gnothi pointer is incomplete, not healthy."""
    root = tmp_path / "organism"
    ensure_global_lifecycle_initialized(root)

    result = EvolutionDashboardService(root).snapshot()

    assert result["state"] == "partial"
    assert result["gnothi"]["state"] == "missing"
    assert result["generations"]["state"] == "ready"
    assert "gnothi_pointer_missing" in result["diagnostics"]


def test_snapshot_partial_coverage_preserves_the_unknown_domain(tmp_path: Path) -> None:
    """A published partial revision remains visible as partial coverage."""
    root = tmp_path / "organism"
    ensure_global_lifecycle_initialized(root)
    identity = probe_organism_identity(root)
    assert identity is not None
    coverage = _current_coverage()
    coverage["capabilities"] = {
        "status": "partial",
        "fingerprint": "sha256:capabilities",
    }
    _publish_gnothi(root, identity, coverage=coverage)

    result = EvolutionDashboardService(root).snapshot()

    assert result["state"] == "partial"
    assert result["gnothi"]["state"] == "partial"
    assert result["gnothi"]["coverage"]["unknown_domains"] == ["capabilities"]
    assert result["gnothi"]["coverage"]["truncated"] is False


def test_snapshot_absent_ledger_is_partial_without_creating_one(tmp_path: Path) -> None:
    """A valid identity and Gnothi revision never cause a ledger read to initialize one."""
    root = tmp_path / "organism"
    identity = create_organism_identity(root)
    _publish_gnothi(root, identity, coverage=_current_coverage())

    result = EvolutionDashboardService(root).snapshot()

    assert result["state"] == "partial"
    assert result["gnothi"]["state"] == "ready"
    assert result["generations"]["state"] == "missing"
    assert "lifecycle_unavailable" in result["diagnostics"]
    assert not (root / "evolution" / "evolution.db").exists()


def test_snapshot_coherent_state_is_public_stable_and_digest_ignores_observed_at(
    tmp_path: Path,
) -> None:
    """A coherent local lifecycle and full Gnothi revision produce one stable public view."""
    root = tmp_path / "organism"
    ensure_global_lifecycle_initialized(root)
    identity = probe_organism_identity(root)
    assert identity is not None
    _publish_gnothi(root, identity, coverage=_current_coverage())

    first = EvolutionDashboardService(root).snapshot()
    second = EvolutionDashboardService(root).snapshot()

    assert first["state"] == "ready"
    assert first["organism"] == {
        "id_prefix": identity.organism_id[:8],
        "lineage_prefix": identity.lineage_root_digest[:12],
    }
    assert first["gnothi"]["state"] == "ready"
    assert first["generations"]["state"] == "ready"
    assert first["observed_at"] != second["observed_at"]
    assert first["snapshot_digest"] == second["snapshot_digest"]
    assert identity.organism_id not in json.dumps(first)


def test_snapshot_rejects_telos_directory_symlink(tmp_path: Path) -> None:
    """A substituted Telos parent cannot make external data appear ready."""
    root = tmp_path / "organism"
    identity = create_organism_identity(root)
    external_telos = tmp_path / "external-telos"
    _write_telos(external_telos, identity)
    shutil.rmtree(root / "telos")
    (root / "telos").symlink_to(external_telos, target_is_directory=True)

    result = EvolutionDashboardService(root).snapshot()

    assert result["telos"] == {"state": "corrupt", "active_digest_prefix": None}
    assert "telos_pointer_invalid" in result["diagnostics"]


def test_snapshot_rejects_telos_revisions_directory_symlink(tmp_path: Path) -> None:
    """A substituted revision parent cannot make an external revision active."""
    root = tmp_path / "organism"
    identity = create_organism_identity(root)
    external_telos = tmp_path / "external-telos"
    digest = _write_telos(external_telos, identity)
    telos = root / "telos"
    shutil.rmtree(telos / "revisions")
    (telos / "active.json").write_text(json.dumps({"digest": digest}), encoding="utf-8")
    (telos / "revisions").symlink_to(
        external_telos / "revisions", target_is_directory=True
    )

    result = EvolutionDashboardService(root).snapshot()

    assert result["telos"] == {"state": "corrupt", "active_digest_prefix": None}
    assert "telos_pointer_invalid" in result["diagnostics"]


def test_snapshot_rejects_non_directory_telos_revisions_parent(tmp_path: Path) -> None:
    """Telos revision storage must remain a directory, not an arbitrary file."""
    root = tmp_path / "organism"
    identity = create_organism_identity(root)
    external_telos = tmp_path / "external-telos"
    digest = _write_telos(external_telos, identity)
    telos = root / "telos"
    shutil.rmtree(telos / "revisions")
    (telos / "active.json").write_text(json.dumps({"digest": digest}), encoding="utf-8")
    (telos / "revisions").write_text("not a directory", encoding="utf-8")

    result = EvolutionDashboardService(root).snapshot()

    assert result["telos"] == {"state": "corrupt", "active_digest_prefix": None}
    assert "telos_pointer_invalid" in result["diagnostics"]


def test_snapshot_reads_valid_telos_without_posix_openat_flags(
    tmp_path: Path, monkeypatch
) -> None:
    """A Windows-style reader still exposes valid local Telos data."""
    root = tmp_path / "organism"
    identity = create_organism_identity(root)
    shutil.rmtree(root / "telos")
    digest = _write_telos(root / "telos", identity)
    service = EvolutionDashboardService(root)
    monkeypatch.delattr(dashboard_module.os, "O_DIRECTORY", raising=False)
    monkeypatch.delattr(dashboard_module.os, "O_NOFOLLOW", raising=False)

    result = service.snapshot()

    assert result["telos"] == {
        "state": "ready",
        "active_digest_prefix": digest[:12],
    }
    assert "telos_pointer_invalid" not in result["diagnostics"]


def test_snapshot_windows_fallback_rejects_telos_reparse_parent(
    tmp_path: Path, monkeypatch
) -> None:
    """A Windows reparse-point parent cannot make Telos data appear ready."""
    root = tmp_path / "organism"
    identity = create_organism_identity(root)
    shutil.rmtree(root / "telos")
    _write_telos(root / "telos", identity)
    service = EvolutionDashboardService(root)
    reparse_path = root / "telos"
    original_lstat = Path.lstat

    def lstat_with_reparse(path: Path):
        info = original_lstat(path)
        if path == reparse_path:
            return SimpleNamespace(
                st_mode=info.st_mode,
                st_dev=info.st_dev,
                st_ino=info.st_ino,
                st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT,
            )
        return info

    monkeypatch.setattr(Path, "lstat", lstat_with_reparse)
    monkeypatch.setattr(
        dashboard_module, "_supports_posix_descriptor_reads", lambda: False
    )

    result = service.snapshot()

    assert result["telos"] == {"state": "corrupt", "active_digest_prefix": None}
    assert "telos_pointer_invalid" in result["diagnostics"]


def test_snapshot_windows_fallback_rejects_telos_directory_symlink(
    tmp_path: Path, monkeypatch
) -> None:
    """A Windows-style reader rejects a substituted Telos parent."""
    root = tmp_path / "organism"
    identity = create_organism_identity(root)
    external_telos = tmp_path / "external-telos"
    _write_telos(external_telos, identity)
    shutil.rmtree(root / "telos")
    (root / "telos").symlink_to(external_telos, target_is_directory=True)
    service = EvolutionDashboardService(root)
    monkeypatch.setattr(
        dashboard_module, "_supports_posix_descriptor_reads", lambda: False
    )

    result = service.snapshot()

    assert result["telos"] == {"state": "corrupt", "active_digest_prefix": None}
    assert "telos_pointer_invalid" in result["diagnostics"]


def test_snapshot_windows_fallback_rejects_telos_revisions_directory_symlink(
    tmp_path: Path, monkeypatch
) -> None:
    """A Windows-style reader rejects a substituted revisions parent."""
    root = tmp_path / "organism"
    identity = create_organism_identity(root)
    external_telos = tmp_path / "external-telos"
    digest = _write_telos(external_telos, identity)
    telos = root / "telos"
    shutil.rmtree(telos / "revisions")
    (telos / "active.json").write_text(json.dumps({"digest": digest}), encoding="utf-8")
    (telos / "revisions").symlink_to(
        external_telos / "revisions", target_is_directory=True
    )
    service = EvolutionDashboardService(root)
    monkeypatch.setattr(
        dashboard_module, "_supports_posix_descriptor_reads", lambda: False
    )

    result = service.snapshot()

    assert result["telos"] == {"state": "corrupt", "active_digest_prefix": None}
    assert "telos_pointer_invalid" in result["diagnostics"]


def test_snapshot_windows_fallback_rejects_non_directory_revisions_parent(
    tmp_path: Path, monkeypatch
) -> None:
    """A Windows-style reader rejects a non-directory revisions parent."""
    root = tmp_path / "organism"
    identity = create_organism_identity(root)
    external_telos = tmp_path / "external-telos"
    digest = _write_telos(external_telos, identity)
    telos = root / "telos"
    shutil.rmtree(telos / "revisions")
    (telos / "active.json").write_text(json.dumps({"digest": digest}), encoding="utf-8")
    (telos / "revisions").write_text("not a directory", encoding="utf-8")
    service = EvolutionDashboardService(root)
    monkeypatch.setattr(
        dashboard_module, "_supports_posix_descriptor_reads", lambda: False
    )

    result = service.snapshot()

    assert result["telos"] == {"state": "corrupt", "active_digest_prefix": None}
    assert "telos_pointer_invalid" in result["diagnostics"]


def test_snapshot_windows_fallback_rejects_telos_reparse_leaf(
    tmp_path: Path, monkeypatch
) -> None:
    """A Windows reparse-point pointer cannot make Telos data appear ready."""
    root = tmp_path / "organism"
    identity = create_organism_identity(root)
    shutil.rmtree(root / "telos")
    _write_telos(root / "telos", identity)
    service = EvolutionDashboardService(root)
    reparse_path = root / "telos" / "active.json"
    original_lstat = Path.lstat

    def lstat_with_reparse(path: Path):
        info = original_lstat(path)
        if path == reparse_path:
            return SimpleNamespace(
                st_mode=info.st_mode,
                st_dev=info.st_dev,
                st_ino=info.st_ino,
                st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT,
            )
        return info

    monkeypatch.setattr(Path, "lstat", lstat_with_reparse)
    monkeypatch.setattr(
        dashboard_module, "_supports_posix_descriptor_reads", lambda: False
    )

    result = service.snapshot()

    assert result["telos"] == {"state": "corrupt", "active_digest_prefix": None}
    assert "telos_pointer_invalid" in result["diagnostics"]


def test_snapshot_windows_fallback_rejects_nonregular_telos_leaf(
    tmp_path: Path, monkeypatch
) -> None:
    """A Windows-style reader rejects a directory substituted for the pointer."""
    root = tmp_path / "organism"
    identity = create_organism_identity(root)
    shutil.rmtree(root / "telos")
    _write_telos(root / "telos", identity)
    pointer = root / "telos" / "active.json"
    pointer.unlink()
    pointer.mkdir()
    service = EvolutionDashboardService(root)
    monkeypatch.setattr(
        dashboard_module, "_supports_posix_descriptor_reads", lambda: False
    )

    result = service.snapshot()

    assert result["telos"] == {"state": "corrupt", "active_digest_prefix": None}
    assert "telos_pointer_invalid" in result["diagnostics"]


def test_graph_and_revision_reads_are_bounded_public_and_non_mutating(
    tmp_path: Path,
) -> None:
    """Dashboard graph reads expose bounded public rows from immutable revisions."""
    root = tmp_path / "organism"
    identity = create_organism_identity(root)
    first = _graph_artifact(identity, revision_id="rev-graph-1", available=False)
    second = _graph_artifact(identity, revision_id="rev-graph-2", available=True)
    store = OrganismRevisionStore(root / "gnothi_seauton")
    store.publish(first, published_at="2026-07-28T12:00:00Z")
    pointer = store.publish(second, published_at="2026-07-28T13:00:00Z")
    before = copy.deepcopy(store.current())
    service = EvolutionDashboardService(root)

    graph = service.graph(root_id="capability:alpha", depth=1, limit=20)

    assert graph["schema_version"] == 1
    assert graph["revision_id"] == "rev-graph-2"
    assert graph["revision_digest"] == pointer["sha256"]
    assert [node["id"] for node in graph["nodes"]] == [
        "capability:alpha",
        "provider:terminal",
    ]
    assert graph["total_nodes"] == 2
    assert graph["total_edges"] == 1
    assert graph["truncated"] is False
    assert identity.organism_id not in json.dumps(graph)
    assert store.current() == before

    revisions = service.revisions(limit=1)
    assert revisions["schema_version"] == 1
    assert revisions["total_revisions"] == 2
    assert revisions["truncated"] is True
    assert revisions["items"] == [
        {
            "revision_id": "rev-graph-2",
            "revision_digest": pointer["sha256"],
            "collected_at": "2026-07-28T12:00:00Z",
            "status": "current",
            "node_count": 2,
            "edge_count": 1,
        }
    ]

    diff = service.revision_diff("rev-graph-1", "rev-graph-2")
    assert diff["schema_version"] == 1
    assert diff["left_revision_id"] == "rev-graph-1"
    assert diff["right_revision_id"] == "rev-graph-2"
    assert diff["changed_state"] == [
        {
            "id": "capability:alpha",
            "before": {"available": False},
            "after": {"available": True},
        }
    ]
    assert identity.organism_id not in json.dumps(diff)


def test_graph_rejects_bad_bounds_and_stale_expected_revision(tmp_path: Path) -> None:
    root = tmp_path / "organism"
    identity = create_organism_identity(root)
    store = OrganismRevisionStore(root / "gnothi_seauton")
    store.publish(_graph_artifact(identity, revision_id="rev-current", available=True))
    service = EvolutionDashboardService(root)

    for depth in (-1, 5):
        with pytest.raises(ValueError, match="invalid graph depth"):
            service.graph(depth=depth)
    for limit in (0, 201):
        with pytest.raises(ValueError, match="invalid graph limit"):
            service.graph(limit=limit)
    with pytest.raises(EvolutionDashboardConflict) as conflict:
        service.graph(expected_revision="rev-stale")
    assert conflict.value.code == "gnothi_revision_changed"
    assert str(conflict.value) == "gnothi_revision_changed"
    for limit in (0, 51):
        with pytest.raises(ValueError, match="invalid revision limit"):
            service.revisions(limit=limit)


def test_revision_diff_omits_non_scalar_private_evidence_refs(tmp_path: Path) -> None:
    root = tmp_path / "organism"
    identity = create_organism_identity(root)
    first = _graph_artifact(identity, revision_id="rev-private-1", available=True)
    second = _graph_artifact(identity, revision_id="rev-private-2", available=True)
    add_node(
        second,
        node_id="capability:private-evidence",
        kind="capability",
        label="Private evidence capability",
        owner_class="core",
        owner_id="hermes",
        state={"available": True, identity.organism_id: True},
        evidence_refs=[{"private": identity.organism_id}],
    )
    store = OrganismRevisionStore(root / "gnothi_seauton")
    store.publish(first)
    store.publish(second)

    result = EvolutionDashboardService(root).revision_diff(
        "rev-private-1", "rev-private-2"
    )

    assert result["added_capabilities"][0]["evidence_refs"] == []
    assert identity.organism_id not in json.dumps(result)


def test_revision_diff_preserves_raw_owner_class_and_redacts_embedded_paths(
    tmp_path: Path,
) -> None:
    root = tmp_path / "organism"
    identity = create_organism_identity(root)
    first = _graph_artifact(identity, revision_id="rev-owner-1", available=True)
    second = _graph_artifact(identity, revision_id="rev-owner-2", available=True)
    add_node(
        second,
        node_id="capability:private-path",
        kind="capability",
        label="Plugin at /private/secret/plugin.py is unavailable",
        owner_class="third-party",
        owner_id="private-owner",
        state={"available": True},
        evidence_refs=[r"See C:\\Users\\secret\\plugin.py before retrying"],
    )
    store = OrganismRevisionStore(root / "gnothi_seauton")
    store.publish(first)
    store.publish(second)

    result = EvolutionDashboardService(root).revision_diff("rev-owner-1", "rev-owner-2")

    added = result["added_capabilities"]
    assert added == [
        {
            "id": "capability:private-path",
            "kind": "capability",
            "label": "Plugin at [ABSOLUTE_PATH] is unavailable",
            "owner_class": "third-party",
            "generation_scope": "stable",
            "state": {"available": True},
            "evidence_refs": ["See [ABSOLUTE_PATH] before retrying"],
        }
    ]
    assert "/private/secret" not in json.dumps(result)
    assert r"C:\Users\secret" not in json.dumps(result)


def test_revision_diff_redacts_network_paths_without_consuming_public_context(
    tmp_path: Path,
) -> None:
    root = tmp_path / "organism"
    identity = create_organism_identity(root)
    first = _graph_artifact(identity, revision_id="rev-network-1", available=True)
    second = _graph_artifact(identity, revision_id="rev-network-2", available=True)
    add_node(
        second,
        node_id="capability:network-path",
        kind="capability",
        label=r'Open "\\server\share\secret\plugin.py:42:7"',
        owner_class="third-party",
        owner_id="private-owner",
        state={"available": True},
        evidence_refs=["[//server/share/private/trace.log:4], https://example.test/docs"],
    )
    store = OrganismRevisionStore(root / "gnothi_seauton")
    store.publish(first)
    store.publish(second)

    result = EvolutionDashboardService(root).revision_diff(
        "rev-network-1", "rev-network-2"
    )

    assert result["added_capabilities"] == [
        {
            "id": "capability:network-path",
            "kind": "capability",
            "label": 'Open "[ABSOLUTE_PATH]:42:7"',
            "owner_class": "third-party",
            "generation_scope": "stable",
            "state": {"available": True},
            "evidence_refs": ["[[ABSOLUTE_PATH]:4], https://example.test/docs"],
        }
    ]


def test_revision_diff_redacts_file_uris_at_the_public_dashboard_boundary(
    tmp_path: Path,
) -> None:
    root = tmp_path / "organism"
    identity = create_organism_identity(root)
    first = _graph_artifact(identity, revision_id="rev-file-uri-1", available=True)
    second = _graph_artifact(identity, revision_id="rev-file-uri-2", available=True)
    add_node(
        second,
        node_id="capability:file-uri",
        kind="capability",
        label="Read file://server/share/private/trace.log:4",
        owner_class="third-party",
        owner_id="private-owner",
        state={"available": True},
        evidence_refs=["FILE:///C:/Users/alice/private/tool.py:8:3"],
    )
    store = OrganismRevisionStore(root / "gnothi_seauton")
    store.publish(first)
    store.publish(second)

    result = EvolutionDashboardService(root).revision_diff(
        "rev-file-uri-1", "rev-file-uri-2"
    )

    added = result["added_capabilities"]
    assert added[0]["label"] == "Read [ABSOLUTE_PATH]:4"
    assert added[0]["evidence_refs"] == ["[ABSOLUTE_PATH]:8:3"]
    assert "file://" not in json.dumps(result).lower()
    assert "users/alice" not in json.dumps(result).lower()
    assert "server/share" not in json.dumps(result).lower()


def test_graph_and_revision_reads_leave_an_absent_root_absent(tmp_path: Path) -> None:
    root = tmp_path / "organism"
    service = EvolutionDashboardService(root)

    assert service.graph() == {
        "schema_version": 1,
        "revision_id": None,
        "revision_digest": None,
        "nodes": [],
        "edges": [],
        "blockers": [],
        "total_nodes": 0,
        "total_edges": 0,
        "truncated": False,
    }
    assert service.revisions() == {
        "schema_version": 1,
        "items": [],
        "total_revisions": 0,
        "truncated": False,
    }
    assert not root.exists()


def test_telos_read_binds_active_and_history_to_the_local_organism(
    tmp_path: Path,
) -> None:
    root = tmp_path / "organism"
    identity, ledger, _ = _seed_governance_state(root)

    result = EvolutionDashboardService(root).telos(history_limit=1)

    assert result["state"] == "ready"
    assert result["active_digest"] == result["active_revision"]["digest"]
    assert len(result["history"]) == 1
    assert result["total_revisions"] == 3
    assert result["truncated"] is True
    for revision in [result["active_revision"], *result["history"]]:
        document = json.loads(
            (root / "telos" / "revisions" / f"{revision['digest']}.json").read_text(
                encoding="utf-8"
            )
        )
        assert document["organism_id"] == identity.organism_id
    ledger.connection.close()


def test_telos_read_does_not_repair_or_repermission_a_partial_organism(
    tmp_path: Path,
) -> None:
    root = tmp_path / "organism"
    _, ledger, _ = _seed_governance_state(root)
    shutil.rmtree(root / "archives")
    root.chmod(0o750)

    result = EvolutionDashboardService(root).telos()

    assert result["state"] == "ready"
    assert not (root / "archives").exists()
    assert stat.S_IMODE(root.stat().st_mode) == 0o750
    ledger.connection.close()


def test_pipeline_counts_only_the_bounded_suggestions_and_resolves_blueprints(
    tmp_path: Path,
) -> None:
    root = tmp_path / "organism"
    _, ledger, attempts = _seed_governance_state(root)

    result = EvolutionDashboardService(root).pipeline(
        attempt_id=attempts[0], limit=1
    )

    assert result["state"] == "ready"
    assert result["attempt_id"] == attempts[0]
    assert len(result["suggestions"]) == 1
    assert sum(result["suggestion_counts"].values()) == len(result["suggestions"])
    assert result["suggestions_truncated"] is False
    assert len(result["blueprints"]) == 1
    assert result["blueprints_truncated"] is False
    repository = BlueprintRepository(ledger)
    assert all(
        repository.get(row["blueprint_id"]) is not None
        for row in result["blueprints"]
    )
    assert "/private/dashboard-secret" not in json.dumps(result)
    ledger.connection.close()


def test_pipeline_reads_suggestions_from_the_immutable_ledger_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "organism"
    _, ledger, _ = _seed_governance_state(root)
    from hermes_cli.evolution import suggestions as suggestions_module

    def unexpected_reopen(*_args, **_kwargs):
        raise AssertionError("dashboard read reopened the source database")

    monkeypatch.setattr(suggestions_module, "_connect_existing", unexpected_reopen)

    result = EvolutionDashboardService(root).pipeline()

    assert result["state"] == "ready"
    assert result["total_suggestions"] > 0
    ledger.connection.close()


def test_pipeline_caps_the_suggestion_database_read_at_the_public_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "organism"
    identity, ledger, _ = _seed_governance_state(root)
    active_digest = json.loads((root / "telos" / "active.json").read_text())["digest"]
    repository = SuggestionRepository(root / "evolution" / "evolution.db")
    for index in range(51):
        repository.upsert_suggestion(
            opportunity_key=f"{index + 1000:064x}",
            initial_state="observing",
            active_telos_digest=active_digest,
            score=_score(),
            envelopes=(
                _observation(
                    identity,
                    event_id=f"event-extra-{index}",
                    capability=f"extra-{index}",
                ),
            ),
            summary_reason="Bounded dashboard suggestion.",
        )

    original = SuggestionRepository.list_suggestions
    seen_limits: list[int | None] = []

    def list_with_required_cap(self, state=None, *, limit=None):
        seen_limits.append(limit)
        if limit is None:
            raise AssertionError("dashboard did not cap the suggestion read")
        return original(self, state, limit=limit)

    monkeypatch.setattr(SuggestionRepository, "list_suggestions", list_with_required_cap)

    result = EvolutionDashboardService(root).pipeline(limit=50)

    assert result["state"] == "ready"
    assert seen_limits == [50]
    assert len(result["suggestions"]) == 50
    assert result["total_suggestions"] == 54
    assert result["suggestions_truncated"] is True
    ledger.connection.close()


def test_pipeline_binds_selected_attempt_to_its_blueprint_and_suggestion(
    tmp_path: Path,
) -> None:
    root = tmp_path / "organism"
    _, ledger, attempts = _seed_governance_state(root)
    selected = attempts[0]

    result = EvolutionDashboardService(root).pipeline(attempt_id=selected, limit=1)

    assert result["state"] == "ready"
    assert result["attempt_id"] == selected
    assert result["attempts"][0]["attempt_id"] == selected
    assert {row["attempt_id"] for row in result["blueprints"]} == {selected}
    assert {
        row["suggestion_id"] for row in result["suggestions"]
    } == {row["suggestion_id"] for row in result["blueprints"]}
    ledger.connection.close()


def test_pipeline_selected_attempt_uses_only_the_selected_attempt_scope(
    tmp_path: Path,
) -> None:
    """An attempt detail view must not retain unrelated attempt page rows."""
    root = tmp_path / "organism"
    _, ledger, attempts = _seed_governance_state(root)
    selected = attempts[0]

    result = EvolutionDashboardService(root).pipeline(attempt_id=selected, limit=2)

    assert result["state"] == "ready"
    assert [row["attempt_id"] for row in result["attempts"]] == [selected]
    assert result["total_attempts"] == 1
    assert result["attempts_truncated"] is False
    ledger.connection.close()


def test_telos_fails_closed_when_revision_count_exceeds_the_read_cap(
    tmp_path: Path,
) -> None:
    root = tmp_path / "organism"
    _, ledger, _ = _seed_governance_state(root)
    store = TelosStore(root)
    active_digest = json.loads((root / "telos" / "active.json").read_text())["digest"]
    revision = store.get_revision(active_digest)
    for _ in range(49):
        revision = replace(revision, parent_digest=revision.canonical_digest)
        store.save_revision(revision)

    result = EvolutionDashboardService(root).telos(history_limit=50)

    assert result["state"] == "blocked"
    assert result["active_revision"] is None
    assert result["history"] == []
    ledger.connection.close()


def test_telos_allows_the_active_revision_and_fifty_history_entries(
    tmp_path: Path,
) -> None:
    root = tmp_path / "organism"
    _, ledger, _ = _seed_governance_state(root)
    store = TelosStore(root)
    active_digest = json.loads((root / "telos" / "active.json").read_text())["digest"]
    revision = store.get_revision(active_digest)
    for _ in range(48):
        revision = replace(revision, parent_digest=revision.canonical_digest)
        store.save_revision(revision)

    result = EvolutionDashboardService(root).telos(history_limit=50)

    assert result["state"] == "ready"
    assert result["total_revisions"] == 51
    assert len(result["history"]) == 50
    assert result["truncated"] is False
    ledger.connection.close()


def test_pipeline_keeps_contract_only_stages_unavailable_without_actions(
    tmp_path: Path,
) -> None:
    root = tmp_path / "organism"
    _, ledger, _ = _seed_governance_state(root)

    stages = EvolutionDashboardService(root).pipeline()["stages"]

    unavailable = [stage for stage in stages if not stage["available"]]
    assert [stage["id"] for stage in unavailable] == [
        "build",
        "canary",
        "promotion",
        "stable",
    ]
    assert all("action" not in stage for stage in unavailable)
    ledger.connection.close()


def test_audit_read_is_monotonic_and_redacts_each_summary(tmp_path: Path) -> None:
    root = tmp_path / "organism"
    _, ledger, _ = _seed_governance_state(root)

    result = EvolutionDashboardService(root).audit(after=0, limit=1)

    sequences = [event["sequence"] for event in result["events"]]
    assert result["state"] == "ready"
    assert sequences == sorted(sequences)
    assert len(sequences) == len(set(sequences))
    assert result["truncated"] is True
    assert "/private/dashboard-secret" not in json.dumps(result)
    assert result["mutable_actions"] == []
    ledger.connection.close()


def test_corrupt_event_chain_disables_pipeline_and_audit_actions(
    tmp_path: Path,
) -> None:
    root = tmp_path / "organism"
    _, ledger, _ = _seed_governance_state(root)
    from hermes_cli.evolution import ledger as ledger_module

    ledger.connection.execute("DROP TRIGGER lifecycle_events_no_update")
    ledger.connection.execute(
        (
            "UPDATE lifecycle_events SET reason_summary = 'tampered' "
            "WHERE event_sequence = 1"
        )
    )
    ledger.connection.execute(
        next(
            statement
            for statement in ledger_module._SCHEMA_STATEMENTS
            if "CREATE TRIGGER lifecycle_events_no_update" in statement
        )
    )
    ledger.connection.commit()

    service = EvolutionDashboardService(root)

    assert service.pipeline()["state"] == "corrupt"
    assert service.pipeline()["mutable_actions"] == []
    assert service.audit()["state"] == "corrupt"
    assert service.audit()["mutable_actions"] == []
    ledger.connection.close()


def test_dashboard_blocks_an_oversized_valid_lifecycle_chain(
    tmp_path: Path,
) -> None:
    """A dashboard read cannot treat a verified prefix as a healthy lifecycle."""
    root = tmp_path / "organism"
    _, ledger, _ = _seed_governance_state(root)
    for index in range(256):
        ledger.append_event(
            LifecycleEvent(
                event_id=f"dashboard-budget-{index}",
                attempt_id=None,
                generation_id=None,
                event_type="dashboard_budget_recorded",
                prior_state=None,
                next_state="draft",
                actor="operator",
                input_digests=(),
                authorization_id=None,
                reason_code="dashboard_budget_recorded",
                reason_summary="Dashboard budget evidence recorded.",
                created_at="2026-07-28T12:02:00.000000Z",
            )
        )
    assert ledger.verify_chain() == []

    service = EvolutionDashboardService(root)

    pipeline = service.pipeline()
    audit = service.audit()
    snapshot = service.snapshot()

    assert pipeline["state"] == "blocked"
    assert pipeline["mutable_actions"] == []
    assert audit["state"] == "blocked"
    assert audit["mutable_actions"] == []
    assert snapshot["generations"]["state"] == "blocked"
    assert "lifecycle_unavailable" in snapshot["diagnostics"]
    ledger.connection.close()


def test_dashboard_bounds_the_evolution_directory_probe_before_materialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An oversized evolution directory is unavailable, not a partial lifecycle."""
    root = tmp_path / "organism"
    create_organism_identity(root)
    evolution_root = root / "evolution"
    for index in range(66):
        (evolution_root / f"untrusted-member-{index}").write_text(
            "untrusted", encoding="utf-8"
        )

    seen_members: list[str] = []
    original_iterdir = Path.iterdir

    def tracked_iterdir(path: Path):
        for child in original_iterdir(path):
            if path == evolution_root:
                seen_members.append(child.name)
            yield child

    monkeypatch.setattr(Path, "iterdir", tracked_iterdir)

    result = EvolutionDashboardService(root).pipeline()

    assert result["state"] == "blocked"
    assert result["mutable_actions"] == []
    assert len(seen_members) == 65


def test_governance_reads_leave_an_absent_root_absent(tmp_path: Path) -> None:
    root = tmp_path / "organism"
    service = EvolutionDashboardService(root)

    assert service.telos()["state"] == "missing"
    assert service.pipeline()["state"] == "missing"
    assert service.audit()["state"] == "missing"
    assert not root.exists()


def test_governance_reads_reject_bad_bounds(tmp_path: Path) -> None:
    root = tmp_path / "organism"
    service = EvolutionDashboardService(root)

    for history_limit in (0, 51):
        with pytest.raises(ValueError, match="invalid telos history limit"):
            service.telos(history_limit=history_limit)
    for limit in (0, 51):
        with pytest.raises(ValueError, match="invalid pipeline limit"):
            service.pipeline(limit=limit)
    for after, limit in ((-1, 1), (0, 0), (0, 101)):
        with pytest.raises(ValueError, match="invalid audit bounds"):
            service.audit(after=after, limit=limit)
    assert not root.exists()
