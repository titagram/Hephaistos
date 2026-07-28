"""Behavior contracts for the read-only Evolution dashboard snapshot."""

from __future__ import annotations

import copy
import json
import stat
import shutil
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
