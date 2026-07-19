"""Golden assertions for the canonical graph-v2 indexer boundary."""

from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from hermes_cli.hades_graph_v2 import artifact_to_payload, validate_artifact
from hermes_cli.hades_index import build_canonical_graph
from hermes_cli.hades_index.aggregate import aggregate_adapter_results
from hermes_cli.hades_index.lifecycle.model import (
    AdapterResult,
    CoverageCapability,
    CoverageEvent,
    CoverageOutcome,
    IRValidationError,
    InventoryFile,
    FrameworkLocalTarget,
    AsyncSuccessor,
)
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


def _empty_result(*events: CoverageEvent) -> AdapterResult:
    return AdapterResult((), (), (), (), (), (), (), (), (), (), (), (), events, ())


def test_inventory_ledger_materializes_failure_only_file_and_counts_it(tmp_path):
    event = CoverageEvent(
        "python",
        CoverageCapability.INVENTORY,
        CoverageOutcome.PARTIAL,
        "file_read_failed",
        "src/unreadable.py",
        0,
        1,
    )
    context = replace(
        _context(tmp_path),
        inventory_files=(
            InventoryFile("src/unreadable.py", "b" * 64, "python", True),
        ),
        file_accessor=lambda _path: (_ for _ in ()).throw(OSError("denied")),
    )

    artifact = build_canonical_graph(
        context,
        (_empty_result(event),),
        generated_at=lambda: "2026-07-19T12:00:00Z",
    )
    file_node = next(node for node in artifact.nodes if node.kind.value == "file")

    assert file_node.qualified_name == "src/unreadable.py"
    assert file_node.properties.analysis_status.value == "failed"
    assert artifact.graph_contract.coverage.files.discovered == 1
    assert artifact.graph_contract.coverage.files.failed == 1


def test_distinct_polyglot_adapter_results_are_permutation_invariant(tmp_path):
    python = _complex_result()
    typescript = _empty_result(
        CoverageEvent(
            "typescript",
            CoverageCapability.CONTROL_FLOW,
            CoverageOutcome.UNSUPPORTED,
            "parser_unavailable",
            None,
            0,
            1,
        )
    )
    context = replace(
        _context(tmp_path), detected_languages=("python", "typescript")
    )

    first = build_canonical_graph(
        context,
        (python, typescript),
        generated_at=lambda: "2026-07-19T12:00:00Z",
    )
    second = build_canonical_graph(
        context,
        (typescript, python),
        generated_at=lambda: "2026-07-19T12:00:00Z",
    )

    assert artifact_to_payload(first) == artifact_to_payload(second)
    assert tuple(item.name for item in first.languages) == ("python", "typescript")
    assert tuple(
        item.language for item in first.graph_contract.completeness.languages
    ) == ("python", "typescript")


def test_empty_file_and_no_entrypoint_coverage_remain_distinct(tmp_path):
    artifact = build_canonical_graph(
        _context(tmp_path),
        (_valid_result(),),
        generated_at=lambda: "2026-07-19T12:00:00Z",
    )
    file_node = next(node for node in artifact.nodes if node.kind.value == "file")

    assert file_node.properties.byte_size == 0
    assert file_node.properties.analysis_status.value == "analyzed"

    no_entrypoint = build_canonical_graph(
        _context(tmp_path),
        (_empty_result(),),
        generated_at=lambda: "2026-07-19T12:00:00Z",
    )
    assert no_entrypoint.entrypoints == ()
    assert no_entrypoint.flows == ()
    assert no_entrypoint.graph_contract.coverage.entrypoints.detected == 0
    assert no_entrypoint.graph_contract.completeness.status.value == "partial"


def test_real_fastapi_pipeline_builds_ordered_framework_lifecycle(tmp_path):
    from hermes_cli.hades_index.lifecycle.frameworks.fastapi import (
        FastAPILifecycleAdapter,
    )
    from tests.hermes_cli.test_hades_lifecycle_fastapi import (
        _candidate,
        _context as fastapi_context,
        _prepare,
        _write,
    )

    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from fastapi import FastAPI
app = FastAPI()
@app.get('/items')
async def items():
    return {'ok': True}
""",
    )
    adapter = FastAPILifecycleAdapter()
    adapter_context = fastapi_context(tmp_path)
    candidate = _candidate(adapter, adapter_context, "items")
    actual_segments = adapter.pipeline(adapter_context, candidate)
    base = _valid_result()
    declaration = base.declarations[0]
    transformed = tuple(
        replace(
            segment,
            target=(
                FrameworkLocalTarget(declaration.local_key)
                if isinstance(segment.target, FrameworkLocalTarget)
                else segment.target
            ),
            short_circuit_successors=tuple(
                replace(successor, target_local_key=declaration.local_key)
                if isinstance(successor, AsyncSuccessor)
                else successor
                for successor in segment.short_circuit_successors
            ),
        )
        for segment in actual_segments
    )
    result = replace(
        base,
        edge_facts=(),
        framework_segments=tuple(sorted(transformed, key=lambda item: item.local_key)),
        entrypoints=(
            replace(
                candidate,
                handler_local_key=declaration.local_key,
                unresolved_fact_local_key=None,
            ),
        ),
        unresolved_facts=(),
    )
    result.validate()
    context = replace(
        _context(tmp_path),
        detected_frameworks=adapter_context.detected_frameworks,
        python_metadata=adapter_context.python_metadata,
        inventory_files=(
            InventoryFile(
                "app.py",
                hashlib.sha256((tmp_path / "app.py").read_bytes()).hexdigest(),
                "python",
                True,
            ),
            InventoryFile(
                "pyproject.toml",
                hashlib.sha256(
                    (tmp_path / "pyproject.toml").read_bytes()
                ).hexdigest(),
                None,
                False,
            ),
            InventoryFile("src/app.py", "a" * 64, "python", True),
        ),
        file_accessor=lambda path: (
            (tmp_path / path).read_bytes()
            if (tmp_path / path).exists()
            else b""
        ),
    )

    artifact = build_canonical_graph(
        context,
        (result,),
        generated_at=lambda: "2026-07-19T12:00:00Z",
    )
    framework_nodes = [
        node
        for node in artifact.nodes
        if hasattr(node.properties, "pipeline_order")
    ]

    assert len(actual_segments) >= 3
    assert sorted(node.properties.pipeline_order for node in framework_nodes) == list(
        range(len(actual_segments))
    )
    validate_artifact(artifact)
