from pathlib import Path

import pytest

from hermes_cli.gnothi.builder import build_organism_revision
from hermes_cli.gnothi.collectors.base import CollectorResult
from hermes_cli.gnothi.store import OrganismRevisionStore


class _Collector:
    def __init__(self, name, calls, *, status="current", raises=False):
        self.name = name
        self.calls = calls
        self.status = status
        self.raises = raises

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
            fingerprint=f"sha256:{self.name}",
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
