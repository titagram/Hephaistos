from hermes_cli.gnothi.contract import (
    ORGANISM_CONTRACT_VERSION,
    ORGANISM_SCHEMA,
    add_edge,
    add_node,
    new_artifact,
    stable_id,
    validate_artifact,
)


def test_stable_id_is_order_independent():
    assert stable_id("tool", {"name": "terminal", "owner": "core"}) == stable_id(
        "tool", {"owner": "core", "name": "terminal"}
    )


def test_artifact_requires_evidence_for_verified_capability():
    artifact = new_artifact(
        revision_id="rev-1",
        generation_id="git:abc",
        generation_scope="stable",
        head_commit="abc",
        collected_at="2026-07-11T00:00:00Z",
    )
    add_node(
        artifact,
        node_id="capability:terminal",
        kind="capability",
        label="terminal",
        owner_class="core",
        owner_id="hermes",
        state={"declared": True, "verified": True},
        evidence_refs=[],
    )
    errors = validate_artifact(artifact)
    assert "verified_without_current_evidence:capability:terminal" in errors


def test_artifact_rejects_cross_generation_edges():
    artifact = new_artifact(
        revision_id="rev-1",
        generation_id="git:abc",
        generation_scope="stable",
        head_commit="abc",
        collected_at="2026-07-11T00:00:00Z",
    )
    add_node(
        artifact,
        node_id="a",
        kind="component",
        label="a",
        owner_class="core",
        owner_id="hermes",
        generation_scope="stable",
        evidence_refs=["source:a"],
    )
    add_node(
        artifact,
        node_id="b",
        kind="component",
        label="b",
        owner_class="core",
        owner_id="hermes",
        generation_scope="candidate",
        evidence_refs=["source:b"],
    )
    add_edge(
        artifact,
        edge_id="e",
        kind="depends_on",
        source="a",
        target="b",
        evidence_refs=["source:e"],
    )
    assert "cross_generation_edge:e" in validate_artifact(artifact)


def test_contract_versions_are_exact():
    assert ORGANISM_SCHEMA == "hades.organism_graph.v1"
    assert ORGANISM_CONTRACT_VERSION == "hades.gnothi_seauton.v1"
