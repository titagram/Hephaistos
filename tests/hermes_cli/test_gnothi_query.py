import copy
from pathlib import Path

import pytest

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


def _graph_artifact(revision: str = "rev-graph") -> dict:
    artifact = _artifact(revision)
    artifact["nodes"] = []
    artifact["edges"] = []
    add_node(
        artifact,
        node_id="capability:alpha",
        kind="capability",
        label="Alpha Capability",
        owner_class="core",
        owner_id="hermes",
        state={"available": True},
        evidence_refs=[
            *[f"evidence:{index:02d}" for index in range(24)],
        ],
    )
    add_node(
        artifact,
        node_id="capability:beta",
        kind="capability",
        label="Beta capability",
        owner_class="core",
        owner_id="hermes",
        state={"available": True},
        evidence_refs=["evidence:sk-abcdefghijklmnopqrstuvwxyz1234567890"],
    )
    add_node(
        artifact,
        node_id="capability:zeta",
        kind="capability",
        label="Zeta capability",
        owner_class="core",
        owner_id="hermes",
        state={"available": True},
        evidence_refs=["evidence:zeta"],
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
    add_node(
        artifact,
        node_id="runtime:local",
        kind="runtime",
        label="Local runtime",
        owner_class="core",
        owner_id="hermes",
        state={"available": False, "degraded": True},
        evidence_refs=["evidence:runtime"],
    )
    add_edge(
        artifact,
        edge_id="edge:provides",
        kind="provides",
        source="provider:terminal",
        target="capability:alpha",
    )
    add_edge(
        artifact,
        edge_id="edge:requires",
        kind="requires",
        source="capability:alpha",
        target="runtime:local",
    )
    add_edge(
        artifact,
        edge_id="edge:depends",
        kind="depends_on",
        source="capability:beta",
        target="capability:alpha",
    )
    add_edge(
        artifact,
        edge_id="edge:ignored",
        kind="observed_on",
        source="capability:zeta",
        target="capability:alpha",
    )
    return artifact


def test_subgraph_rejects_out_of_range_depth_and_limit(tmp_path: Path) -> None:
    store = OrganismRevisionStore(root=tmp_path)
    store.publish(_graph_artifact())
    query = OrganismQuery(store)

    for depth in (-1, 5):
        with pytest.raises(ValueError, match="invalid graph depth"):
            query.subgraph(
                root_id=None,
                depth=depth,
                limit=1,
                kinds=frozenset(),
                search="",
            )
    for limit in (0, 201):
        with pytest.raises(ValueError, match="invalid graph limit"):
            query.subgraph(
                root_id=None,
                depth=0,
                limit=limit,
                kinds=frozenset(),
                search="",
            )


def test_subgraph_is_stable_bounded_and_does_not_mutate_artifact(tmp_path: Path) -> None:
    store = OrganismRevisionStore(root=tmp_path)
    artifact = _graph_artifact()
    store.publish(artifact)
    query = OrganismQuery(store)
    before = copy.deepcopy(store.current())

    result = query.subgraph(
        root_id=None,
        depth=0,
        limit=2,
        kinds=frozenset(),
        search="",
    )

    assert [node["id"] for node in result["nodes"]] == [
        "capability:alpha",
        "capability:beta",
    ]
    assert result["total_nodes"] == 5
    assert result["total_edges"] == 3
    assert result["truncated"] is True
    assert result["edges"] == [
        {
            "id": "edge:depends",
            "kind": "depends_on",
            "from": "capability:beta",
            "to": "capability:alpha",
            "evidence_refs": [],
        }
    ]
    assert store.current() == before


def test_subgraph_traverses_both_dependency_directions_and_sanitizes_public_rows(
    tmp_path: Path,
) -> None:
    store = OrganismRevisionStore(root=tmp_path)
    store.publish(_graph_artifact())
    query = OrganismQuery(store)

    rooted = query.subgraph(
        root_id="capability:alpha",
        depth=1,
        limit=20,
        kinds=frozenset(),
        search="",
    )

    assert [node["id"] for node in rooted["nodes"]] == [
        "capability:alpha",
        "capability:beta",
        "provider:terminal",
        "runtime:local",
    ]
    assert {edge["kind"] for edge in rooted["edges"]} == {
        "provides",
        "requires",
        "depends_on",
    }
    assert [node["id"] for node in rooted["blockers"]] == ["runtime:local"]

    alpha = next(node for node in rooted["nodes"] if node["id"] == "capability:alpha")
    assert len(alpha["evidence_refs"]) == 20
    beta = next(node for node in rooted["nodes"] if node["id"] == "capability:beta")
    assert "abcdefghijklmnopqrstuvwxyz1234567890" not in str(beta["evidence_refs"])

    capabilities = query.subgraph(
        root_id="capability:alpha",
        depth=1,
        limit=20,
        kinds=frozenset({"capability"}),
        search="",
    )
    assert [node["id"] for node in capabilities["nodes"]] == [
        "capability:alpha",
        "capability:beta",
    ]
    assert capabilities["edges"] == [
        {
            "id": "edge:depends",
            "kind": "depends_on",
            "from": "capability:beta",
            "to": "capability:alpha",
            "evidence_refs": [],
        }
    ]

    by_id = query.subgraph(
        root_id=None,
        depth=0,
        limit=20,
        kinds=frozenset(),
        search="capability:ALPHA",
    )
    by_label = query.subgraph(
        root_id=None,
        depth=0,
        limit=20,
        kinds=frozenset(),
        search="bEtA",
    )
    assert [node["id"] for node in by_id["nodes"]] == ["capability:alpha"]
    assert [node["id"] for node in by_label["nodes"]] == ["capability:beta"]


def test_subgraph_uses_the_supplied_immutable_artifact(tmp_path: Path) -> None:
    store = OrganismRevisionStore(root=tmp_path)
    first = _graph_artifact("rev-graph-first")
    first["nodes"][0]["label"] = "Frozen Alpha"
    second = _graph_artifact("rev-graph-second")
    second["nodes"][0]["label"] = "Current Alpha"
    store.publish(first)
    frozen = store.current()
    assert frozen is not None
    store.publish(second)

    result = OrganismQuery(store, artifact=frozen).subgraph(
        root_id="capability:alpha",
        depth=0,
        limit=20,
        kinds=frozenset(),
        search="",
    )

    assert result["nodes"][0]["label"] == "Frozen Alpha"
