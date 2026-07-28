"""Behavior contracts for the read-only Evolution dashboard snapshot."""

from __future__ import annotations

import json
from pathlib import Path

from hermes_cli.evolution.dashboard_service import EvolutionDashboardService
from hermes_cli.evolution.lifecycle_global import ensure_global_lifecycle_initialized
from hermes_cli.evolution.organism_identity import (
    OrganismIdentity,
    create_organism_identity,
    probe_organism_identity,
)
from hermes_cli.gnothi.contract import new_artifact
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
