from pathlib import Path

import pytest

from hermes_cli.gnothi.builder import build_organism_revision, drift_status
from hermes_cli.gnothi.collectors.base import CollectorResult
from hermes_cli.gnothi.store import OrganismRevisionStore


class _Collector:
    def __init__(
        self,
        name,
        calls,
        *,
        status="current",
        raises=False,
        fingerprint=None,
    ):
        self.name = name
        self.calls = calls
        self.status = status
        self.raises = raises
        self.fingerprint = fingerprint or f"sha256:{name}"

    def probe_fingerprint(self, context):
        return self.fingerprint

    def collect(self, context):
        self.calls.append(self.name)
        if self.raises:
            raise RuntimeError("secret collector failure")
        node_id = f"node:{self.name}"
        return CollectorResult(
            name=self.name,
            status=self.status,
            nodes=[
                {
                    "id": node_id,
                    "kind": "component",
                    "label": self.name,
                    "owner": {"class": "core", "id": "hermes"},
                    "generation_scope": context.generation_scope,
                    "state": {"verified": False},
                    "evidence_refs": [f"evidence:{self.name}"],
                    "properties": {"collector": self.name},
                    "verified_at": "2026-07-14T10:00:00Z",
                }
            ],
            edges=[],
            evidence=[{"id": f"evidence:{self.name}", "kind": "fixture"}],
            fingerprint=self.fingerprint,
            verified_at="2026-07-14T10:00:00Z",
            error_code=None if self.status == "current" else "FixturePartial",
        )


def _collectors(calls, *, source_raises=False, experience_status="missing"):
    return [
        _Collector("runtime", calls),
        _Collector("experience", calls, status=experience_status),
        _Collector("source", calls, raises=source_raises),
        _Collector("contracts", calls),
        _Collector("dependencies", calls),
        _Collector("capabilities", calls),
    ]


def test_builder_orders_collectors_and_isolates_failures(tmp_path: Path):
    calls = []
    artifact = build_organism_revision(
        tmp_path,
        collectors=_collectors(calls, source_raises=True),
        store=OrganismRevisionStore(root=tmp_path / "store"),
        now="2026-07-14T12:00:00Z",
    )

    assert calls == [
        "source",
        "capabilities",
        "runtime",
        "contracts",
        "dependencies",
        "experience",
    ]
    assert artifact["organism_contract"]["status"] == "partial"
    coverage = artifact["organism_contract"]["coverage"]
    assert coverage["source"]["status"] == "partial"
    assert coverage["source"]["error_code"] == "RuntimeError"
    assert coverage["capabilities"] == {
        "status": "current",
        "fingerprint": "sha256:capabilities",
        "verified_at": "2026-07-14T10:00:00Z",
        "error_code": None,
    }
    assert any(node["id"] == "node:capabilities" for node in artifact["nodes"])


def test_builder_carries_forward_failed_domain_with_original_freshness(tmp_path: Path):
    store = OrganismRevisionStore(root=tmp_path / "store")
    calls = []
    previous = build_organism_revision(
        tmp_path,
        collectors=_collectors(calls, experience_status="current"),
        store=store,
        now="2026-07-14T11:00:00Z",
    )
    current = build_organism_revision(
        tmp_path,
        collectors=_collectors([], source_raises=True),
        store=store,
        now="2026-07-14T12:00:00Z",
        force=True,
    )

    carried = next(node for node in current["nodes"] if node["id"] == "node:source")
    assert carried["verified_at"] == "2026-07-14T10:00:00Z"
    assert carried["properties"]["carried_forward"] is True
    assert carried["properties"]["carried_from_revision"] == previous[
        "organism_contract"
    ]["revision_id"]


def test_builder_skips_unchanged_revision_unless_forced(tmp_path: Path):
    store = OrganismRevisionStore(root=tmp_path / "store")
    first = build_organism_revision(
        tmp_path,
        collectors=_collectors([]),
        store=store,
        now="2026-07-14T11:00:00Z",
    )
    second = build_organism_revision(
        tmp_path,
        collectors=_collectors([]),
        store=store,
        now="2026-07-14T12:00:00Z",
    )
    forced = build_organism_revision(
        tmp_path,
        collectors=_collectors([]),
        store=store,
        now="2026-07-14T13:00:00Z",
        force=True,
    )

    assert second["build_result"] == "unchanged"
    assert second["organism_contract"]["revision_id"] == first[
        "organism_contract"
    ]["revision_id"]
    assert forced["organism_contract"]["revision_id"] != first[
        "organism_contract"
    ]["revision_id"]


def test_validation_errors_block_publication(tmp_path: Path, monkeypatch):
    store = OrganismRevisionStore(root=tmp_path / "store")
    monkeypatch.setattr(
        "hermes_cli.gnothi.builder.validate_artifact",
        lambda artifact: ["fixture_invalid"],
    )

    with pytest.raises(ValueError, match="fixture_invalid"):
        build_organism_revision(
            tmp_path,
            collectors=_collectors([]),
            store=store,
            now="2026-07-14T12:00:00Z",
        )
    assert store.current() is None


def test_drift_status_invalidates_only_changed_collector(tmp_path: Path):
    store = OrganismRevisionStore(root=tmp_path / "store")
    initial_collectors = _collectors([], experience_status="current")
    current = build_organism_revision(
        tmp_path,
        collectors=initial_collectors,
        store=store,
        now="2026-07-14T10:00:00Z",
    )
    probes = _collectors([], experience_status="current")
    capabilities = next(row for row in probes if row.name == "capabilities")
    capabilities.fingerprint = "sha256:capabilities-changed"

    status = drift_status(tmp_path, current, collectors=probes)

    assert status["invalidated_domains"] == ["capabilities"]
    assert status["domains"]["capabilities"]["status"] == "stale"
    assert all(
        row["status"] == "current"
        for name, row in status["domains"].items()
        if name != "capabilities"
    )
    assert status["actions"] == ["rebuild --collector capabilities"]


def test_targeted_rebuild_runs_only_selected_and_carries_other_domains(tmp_path: Path):
    store = OrganismRevisionStore(root=tmp_path / "store")
    build_organism_revision(
        tmp_path,
        collectors=_collectors([], experience_status="current"),
        store=store,
        now="2026-07-14T10:00:00Z",
    )
    calls: list[str] = []
    changed = _collectors(calls, experience_status="current")
    next(row for row in changed if row.name == "capabilities").fingerprint = (
        "sha256:capabilities-changed"
    )

    artifact = build_organism_revision(
        tmp_path,
        collectors=changed,
        collector_names=["capabilities"],
        store=store,
        now="2026-07-14T11:00:00Z",
    )

    assert calls == ["capabilities"]
    assert artifact["organism_contract"]["coverage"]["capabilities"]["fingerprint"] == (
        "sha256:capabilities-changed"
    )
    source = next(node for node in artifact["nodes"] if node["id"] == "node:source")
    assert source["verified_at"] == "2026-07-14T10:00:00Z"
    assert source["properties"].get("carried_forward", False) is False


def test_targeted_rebuild_rejects_unknown_name_before_collection(tmp_path: Path):
    calls: list[str] = []
    with pytest.raises(ValueError, match="unknown collector"):
        build_organism_revision(
            tmp_path,
            collectors=_collectors(calls),
            collector_names=["not-real"],
            store=OrganismRevisionStore(root=tmp_path / "store"),
        )
    assert calls == []


def test_targeted_rebuild_converges_with_full_rebuild(tmp_path: Path):
    targeted_store = OrganismRevisionStore(root=tmp_path / "targeted")
    full_store = OrganismRevisionStore(root=tmp_path / "full")
    initial = _collectors([], experience_status="current")
    build_organism_revision(
        tmp_path,
        collectors=initial,
        store=targeted_store,
        now="2026-07-14T10:00:00Z",
    )
    changed = _collectors([], experience_status="current")
    next(row for row in changed if row.name == "dependencies").fingerprint = (
        "sha256:dependencies-changed"
    )
    targeted = build_organism_revision(
        tmp_path,
        collectors=changed,
        collector_names=["dependencies"],
        store=targeted_store,
        now="2026-07-14T11:00:00Z",
    )
    full = build_organism_revision(
        tmp_path,
        collectors=changed,
        store=full_store,
        now="2026-07-14T11:00:00Z",
    )

    assert targeted["organism_contract"]["semantic_fingerprint"] == full[
        "organism_contract"
    ]["semantic_fingerprint"]
