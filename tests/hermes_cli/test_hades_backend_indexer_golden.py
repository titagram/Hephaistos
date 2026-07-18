"""Golden assertions for the canonical graph-v2 indexer boundary."""

from __future__ import annotations

from dataclasses import replace

import pytest

from hermes_cli.hades_graph_v2 import artifact_to_payload, validate_artifact
from hermes_cli.hades_index import build_canonical_graph
from hermes_cli.hades_index.aggregate import aggregate_adapter_results
from hermes_cli.hades_index.lifecycle.model import IRValidationError
from tests.hermes_cli.test_hades_lifecycle_ir import _valid_result
from tests.hermes_cli.test_hades_lifecycle_traversal import _complex_result, _context


def test_canonical_v2_builder_is_exposed_at_the_indexer_boundary(tmp_path):
    artifact = build_canonical_graph(
        _context(tmp_path),
        (_valid_result(),),
        generated_at=lambda: "2026-07-19T12:00:00Z",
    )

    assert artifact.schema == "hades.code_graph.v2"
    assert artifact.graph_contract.version == "hades.graph_artifact.v2"
    assert artifact.graph_contract.artifact_graph_version != "0" * 64
    assert tuple(item.id for item in artifact.nodes) == tuple(
        sorted(item.id for item in artifact.nodes)
    )
    assert tuple(item.id for item in artifact.edges) == tuple(
        sorted(item.id for item in artifact.edges)
    )
    validate_artifact(artifact)


def test_canonical_indexer_output_is_permutation_invariant(tmp_path):
    result = _complex_result()

    first = build_canonical_graph(
        _context(tmp_path),
        (result, result),
        generated_at=lambda: "2026-07-19T12:00:00Z",
    )
    second = build_canonical_graph(
        _context(tmp_path),
        tuple(reversed((result, result))),
        generated_at=lambda: "2026-07-19T12:00:00Z",
    )

    assert artifact_to_payload(first) == artifact_to_payload(second)


def test_v2_aggregation_rejects_cross_adapter_semantic_collisions():
    first = _valid_result()
    declaration = first.declarations[0]
    conflicting = replace(
        first,
        declarations=(replace(declaration, name="different_name"),),
    )

    with pytest.raises(IRValidationError, match="semantic_collision"):
        aggregate_adapter_results((first, conflicting))
