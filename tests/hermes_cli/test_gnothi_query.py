from pathlib import Path

from hermes_cli.gnothi.contract import add_edge, add_node, new_artifact
from hermes_cli.gnothi.query import OrganismQuery
from hermes_cli.gnothi.store import OrganismRevisionStore


def _artifact(revision, *, available=True):
    artifact = new_artifact(
        revision_id=revision,
        generation_id="git:abc",
        generation_scope="stable",
        head_commit="abc",
        collected_at="2026-07-14T12:00:00Z",
    )
    artifact["organism_contract"].update(
        status="current",
        coverage={"source": {"status": "current"}},
        semantic_fingerprint=revision,
    )
    add_node(
        artifact,
        node_id="provider:terminal",
        kind="provider",
        label="Terminal provider",
        owner_class="core",
        owner_id="hermes",
        state={"available": available, "degraded": not available},
        evidence_refs=["evidence:p"],
    )
    add_node(
        artifact,
        node_id="capability:terminal",
        kind="capability",
        label="Terminal",
        owner_class="core",
        owner_id="hermes",
        state={"available": available, "degraded": not available},
        evidence_refs=["evidence:c"],
    )
    add_edge(
        artifact,
        edge_id="edge:provides",
        kind="provides",
        source="provider:terminal",
        target="capability:terminal",
        evidence_refs=["evidence:c"],
    )
    return artifact


def test_query_status_inspect_explain_and_diff(tmp_path: Path):
    store = OrganismRevisionStore(root=tmp_path)
    first = _artifact("rev-1", available=False)
    second = _artifact("rev-2", available=True)
    store.publish(first, published_at="2026-07-14T12:00:00Z")
    store.publish(second, published_at="2026-07-14T13:00:00Z")
    query = OrganismQuery(store)

    status = query.status()
    assert status["revision_id"] == "rev-2"
    assert status["generation_id"] == "git:abc"
    assert status["counts"] == {"nodes": 2, "edges": 1}
    assert status["unknown_domains"] == []

    assert query.inspect("capability:terminal")["match"]["id"] == "capability:terminal"
    assert query.inspect("terminal")["match"]["label"] == "Terminal"

    explanation = query.explain("terminal")
    assert {node["id"] for node in explanation["nodes"]} == {
        "provider:terminal",
        "capability:terminal",
    }
    assert explanation["blockers"] == []

    diff = query.diff("rev-1", "rev-2")
    assert diff["changed_state"][0]["id"] == "capability:terminal"
    assert diff["quality_changes"] == []
    assert diff["truncated"] is False
