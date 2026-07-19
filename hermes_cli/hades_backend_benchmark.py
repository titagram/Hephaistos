"""Hades backend performance benchmark helpers."""

from __future__ import annotations

import copy
import hashlib
from functools import lru_cache
from pathlib import Path
import time
from typing import Any

from hermes_cli.hades_backend_jobs import execute_job
from hermes_cli.hades_backend_sync import _artifact_payload_hash, _artifact_upload_fields


DURATION_WARN_MS = 1000
LARGE_COMPRESSION_RATIO_WARN = 0.75
GRAPH_V2_REQUESTED_COUNTS = {
    "entrypoints": 501,
    "nodes": 5_501,
    "edges": 10_501,
}
_GRAPH_V2_EDGE_FACT_COUNT = GRAPH_V2_REQUESTED_COUNTS["edges"]
_GRAPH_V2_FIXTURE_PATH = "benchmark/graph_v2.py"
_GRAPH_V2_FILE_SHA256 = "a" * 64
_GRAPH_V2_PROJECT_ID = "01KXJD0SV73EBGWKNE2EK3M4KD"
_GRAPH_V2_WORKSPACE_BINDING_ID = "01KXJD1BDMQ2TFABMVJV6EFE8Q"


def run_hades_backend_benchmark(
    cases: list[dict[str, int | str]] | None = None,
    *,
    workspace: str | Path | None = None,
) -> dict[str, Any]:
    # ``cases`` remains accepted for CLI/API compatibility, but legacy v1 graph
    # payload shapes are deliberately not benchmarked as upload candidates.
    del cases
    results = [copy.deepcopy(_cached_graph_v2_scale_case())]
    if workspace is not None:
        results.extend(_run_workspace_cases(Path(workspace).expanduser()))
    warnings = [warning for result in results for warning in result["warnings"]]

    return {
        "schema": "hades.backend_benchmark.v1",
        "status": "warning" if warnings else "passed",
        "case_count": len(results),
        "duration_warn_ms": DURATION_WARN_MS,
        "large_compression_ratio_warn": LARGE_COMPRESSION_RATIO_WARN,
        "has_workspace_dataset": workspace is not None,
        "warnings": warnings,
        "cases": results,
    }


@lru_cache(maxsize=1)
def _cached_graph_v2_scale_case() -> dict[str, Any]:
    """Build the deterministic large v2 fixture once per benchmark process."""

    return _run_graph_v2_scale_case()


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _run_graph_v2_scale_case() -> dict[str, Any]:
    from hermes_cli.hades_graph_v2.bundle import (
        CHUNK_KINDS,
        BundleLimits,
        build_bundle_plan,
    )

    total_started = time.perf_counter()
    build_started = time.perf_counter()
    artifact, prune_ms = _synthetic_graph_v2_fixture()
    build_ms = max(0, _elapsed_ms(build_started) - prune_ms)
    source_counts = {
        kind: len(getattr(artifact, kind))
        for kind in CHUNK_KINDS
    }
    for kind, minimum in GRAPH_V2_REQUESTED_COUNTS.items():
        if source_counts[kind] < minimum:
            raise RuntimeError(
                f"graph v2 benchmark fixture produced {source_counts[kind]} {kind}; "
                f"expected at least {minimum}"
            )

    limits = BundleLimits(
        max_chunk_uncompressed_bytes=512 * 1024,
        max_bundle_uncompressed_bytes=128 * 1024 * 1024,
        max_chunks=512,
    )
    _verify_graph_v2_reference_closure(artifact)

    bundle_started = time.perf_counter()
    # The large complete-delivery artifact takes one public planning pass, which
    # validates the expanded model before producing any reportable chunk bytes.
    plan = build_bundle_plan(artifact, limits)
    bundle_ms = _elapsed_ms(bundle_started)
    deterministic = all(
        hashlib.sha256(raw).hexdigest() == descriptor["sha256"]
        and hashlib.sha256(compressed).hexdigest()
        == descriptor["compressed_sha256"]
        for descriptor, raw, compressed in zip(
            plan.manifest["chunks"],
            plan.uncompressed_chunks,
            plan.chunks,
            strict=True,
        )
    )
    if not deterministic:
        raise RuntimeError("graph v2 benchmark chunk digests do not close")

    delivered_counts = {
        kind: len(getattr(artifact, kind))
        for kind in CHUNK_KINDS
    }
    manifest_counts = {
        kind: int(plan.manifest["counts"][kind])
        for kind in CHUNK_KINDS
    }
    descriptor_counts = {kind: 0 for kind in CHUNK_KINDS}
    for descriptor in plan.manifest["chunks"]:
        descriptor_counts[str(descriptor["kind"])] += int(descriptor["record_count"])
    omitted_counts = {
        kind: source_counts[kind] - delivered_counts[kind]
        for kind in CHUNK_KINDS
    }
    if any(count < 0 for count in omitted_counts.values()):
        raise RuntimeError("graph v2 benchmark delivered more records than its source")
    if delivered_counts != manifest_counts or delivered_counts != descriptor_counts:
        raise RuntimeError("graph v2 benchmark chunk ledger does not close")
    omitted_record_count = sum(omitted_counts.values())
    coverage_omission_ledger = (
        artifact.graph_contract.coverage.records.omitted_by_bundle_budget
    )
    if coverage_omission_ledger != omitted_record_count:
        raise RuntimeError(
            "graph v2 benchmark omission ledger does not match omitted public records"
        )

    compressed_bytes = sum(len(chunk) for chunk in plan.chunks)
    raw_chunk_bytes = sum(len(chunk) for chunk in plan.uncompressed_chunks)
    compression_ratio = (
        round(compressed_bytes / raw_chunk_bytes, 4)
        if raw_chunk_bytes
        else None
    )
    total_ms = _elapsed_ms(total_started)
    artifact_graph_version = str(plan.manifest["artifact_graph_version"])
    manifest_sha256 = hashlib.sha256(plan.manifest_bytes).hexdigest()
    return {
        "name": "graph_v2_scale",
        "source": "synthetic_v2",
        "schema": artifact.schema,
        "requested_counts": dict(GRAPH_V2_REQUESTED_COUNTS),
        "source_counts": source_counts,
        "delivered_counts": delivered_counts,
        "manifest_counts": manifest_counts,
        "descriptor_counts": descriptor_counts,
        "omitted_counts": omitted_counts,
        "omitted_record_count": omitted_record_count,
        "coverage_omission_ledger": coverage_omission_ledger,
        "delivery_complete": omitted_record_count == 0,
        "delivery_status": (
            "complete" if omitted_record_count == 0 else "counted_omissions"
        ),
        "deterministic": deterministic,
        "chunk_count": len(plan.chunks),
        "artifact_graph_version": artifact_graph_version,
        "manifest_sha256": manifest_sha256,
        "payload_sha256": manifest_sha256,
        "logical_uncompressed_bytes": plan.logical_uncompressed_bytes,
        "original_bytes": raw_chunk_bytes,
        "compressed_bytes": compressed_bytes,
        "compression_ratio": compression_ratio,
        "upload_mode": "chunked",
        "raw_source_included": False,
        "truncated": omitted_record_count > 0,
        "redactions": 0,
        "symbol_count": delivered_counts["nodes"],
        "route_count": delivered_counts["entrypoints"],
        "edge_count": delivered_counts["edges"],
        "file_count": 1,
        "duration_ms": total_ms,
        "timing_ms": {
            "build": build_ms,
            "prune": prune_ms,
            "bundle": bundle_ms,
            "total": total_ms,
        },
        "warnings": [],
    }


def _verify_graph_v2_reference_closure(artifact: Any) -> None:
    node_ids = {node.id for node in artifact.nodes}
    entrypoint_ids = {entrypoint.id for entrypoint in artifact.entrypoints}
    structure_ids = {structure.id for structure in artifact.structures}
    edge_ids = {edge.id for edge in artifact.edges}
    flow_ids = {flow.id for flow in artifact.flows}
    uncertainty_ids = {uncertainty.id for uncertainty in artifact.uncertainties}
    families = (
        ("nodes", node_ids, len(artifact.nodes)),
        ("entrypoints", entrypoint_ids, len(artifact.entrypoints)),
        ("structures", structure_ids, len(artifact.structures)),
        ("edges", edge_ids, len(artifact.edges)),
        ("flows", flow_ids, len(artifact.flows)),
        ("uncertainties", uncertainty_ids, len(artifact.uncertainties)),
    )
    if any(len(public_ids) != count for _name, public_ids, count in families):
        raise RuntimeError("graph v2 benchmark contains duplicate public record IDs")
    for entrypoint in artifact.entrypoints:
        if entrypoint.id not in node_ids or (
            entrypoint.handler_node_id is not None
            and entrypoint.handler_node_id not in node_ids
        ) or (
            entrypoint.uncertainty_id is not None
            and entrypoint.uncertainty_id not in uncertainty_ids
        ):
            raise RuntimeError("graph v2 benchmark has a dangling entrypoint reference")
    for structure in artifact.structures:
        if structure.owner_node_id not in node_ids or (
            structure.continuation_node_id is not None
            and structure.continuation_node_id not in node_ids
        ) or (
            structure.parent_structure_id is not None
            and structure.parent_structure_id not in structure_ids
        ):
            raise RuntimeError("graph v2 benchmark has a dangling structure reference")
    for edge in artifact.edges:
        if not {
            edge.source_id,
            edge.target_id,
            edge.occurrence.owner_node_id,
        } <= node_ids or any(
            value is not None and value not in structure_ids
            for value in (
                edge.branch_group_id,
                edge.call_site_id,
                edge.exception_scope_id,
            )
        ) or (
            edge.uncertainty_id is not None
            and edge.uncertainty_id not in uncertainty_ids
        ):
            raise RuntimeError("graph v2 benchmark has a dangling edge reference")
    for flow in artifact.flows:
        if flow.entrypoint_id not in entrypoint_ids or flow.root_node_id not in node_ids:
            raise RuntimeError("graph v2 benchmark has a dangling flow reference")
    for step in artifact.flow_steps:
        if step.flow_id not in flow_ids or step.edge_id not in edge_ids or (
            step.branch_group_id is not None
            and step.branch_group_id not in structure_ids
        ) or (
            step.async_child_flow_id is not None
            and step.async_child_flow_id not in flow_ids
        ):
            raise RuntimeError("graph v2 benchmark has a dangling flow-step reference")


def _synthetic_graph_v2_fixture() -> tuple[Any, int]:
    from dataclasses import replace

    from hermes_cli.hades_graph_config import load_hades_graph_index_config
    from hermes_cli.hades_graph_v2.identity import (
        artifact_graph_version,
        edge_id,
        node_id,
        sha256_jcs,
    )
    from hermes_cli.hades_graph_v2.model import (
        Edge,
        EdgeAstOccurrence,
        EdgeLocation,
        EntrypointKind,
        EvidenceOrigin,
        MethodSemantics,
        NodeKind,
        Relation,
        SourceDeclarationIdentity,
        SourceIdentity,
        SourceLocation,
        TriggerKind,
        artifact_to_payload,
    )
    from hermes_cli.hades_index.lifecycle.builder import GraphBuilder
    from hermes_cli.hades_graph_v2.bundle import BundleLimits
    from hermes_cli.hades_graph_v2.pruning import GraphBudgetPruner
    from hermes_cli.hades_index.lifecycle.model import (
        AdapterResult,
        AstLocatorIR,
        BasicBlock,
        ControlKind,
        CoverageCapability,
        CoverageEvent,
        CoverageOutcome,
        DeclarationIdentityKind,
        EntrypointCandidate,
        ExecutableDeclaration,
        ExtractionContext,
        IREvidence,
        InventoryFile,
        MatchConstraints,
        SourceLocationIR,
    )

    def local_key(family: str, ordinal: int) -> str:
        return sha256_jcs({"benchmark_family": family, "ordinal": ordinal})

    def locator(family: str, ordinal: int, *, line: int) -> AstLocatorIR:
        return AstLocatorIR(
            SourceLocationIR(
                _GRAPH_V2_FIXTURE_PATH,
                line,
                line,
                _GRAPH_V2_FILE_SHA256,
            ),
            f"benchmark/{family}/{ordinal:05d}",
            ordinal,
        )

    def evidence(value: AstLocatorIR) -> IREvidence:
        return IREvidence(
            EvidenceOrigin.VERIFIED_FROM_CODE,
            "hades.benchmark",
            value,
            None,
        )

    declaration_key = local_key("declaration", 0)
    block_key = local_key("block", 0)
    declaration_locator = locator("declaration", 0, line=1)
    block_locator = locator("block", 0, line=1)
    declaration = ExecutableDeclaration(
        declaration_key,
        "python",
        NodeKind.FUNCTION,
        DeclarationIdentityKind.NAMED,
        None,
        "benchmark_handler",
        "benchmark.benchmark_handler",
        "benchmark",
        (),
        (),
        None,
        declaration_locator,
        block_key,
        (),
        (),
    )
    block = BasicBlock(
        block_key,
        declaration_key,
        ControlKind.ENTRY,
        0,
        block_locator,
        (),
    )

    entrypoints = []
    for index in range(GRAPH_V2_REQUESTED_COUNTS["entrypoints"]):
        entrypoint_locator = locator("route", index, line=index + 1)
        public_path = f"/benchmark/{index:05d}"
        entrypoints.append(
            EntrypointCandidate(
                EntrypointKind.HTTP_ROUTE,
                None,
                MethodSemantics.EXPLICIT,
                ("GET",),
                public_path,
                None,
                TriggerKind.HTTP,
                public_path,
                MatchConstraints(None, (), None),
                entrypoint_locator,
                declaration_key,
                None,
                (),
                evidence(entrypoint_locator),
            )
        )

    coverage_events = tuple(
        sorted(
            (
                CoverageEvent(
                    "python",
                    capability,
                    (
                        CoverageOutcome.NOT_APPLICABLE
                        if capability is CoverageCapability.FRAMEWORK_LIFECYCLE
                        else CoverageOutcome.FULL
                    ),
                    None,
                    None,
                    1,
                    0,
                )
                for capability in CoverageCapability
            ),
            key=lambda item: (
                item.language,
                item.capability.value,
                item.outcome.value,
                item.reason_code or "",
                item.path or "",
            ),
        )
    )
    result = AdapterResult(
        (declaration,),
        (block,),
        (),
        (),
        (),
        (),
        (),
        (),
        (),
        (),
        tuple(
            sorted(
                entrypoints,
                key=lambda item: (
                    item.kind.value,
                    item.framework or "",
                    item.public_path or "",
                    item.public_name or "",
                    item.trigger_value,
                    item.registration_locator.source_location.path,
                    item.registration_locator.structural_path,
                    item.registration_locator.ordinal,
                ),
            )
        ),
        (),
        coverage_events,
        (),
    )
    context = ExtractionContext(
        workspace_root=Path("."),
        project_id=_GRAPH_V2_PROJECT_ID,
        workspace_binding_id=_GRAPH_V2_WORKSPACE_BINDING_ID,
        source_identity=SourceIdentity(None, "b" * 64, False, None),
        graph_config=load_hades_graph_index_config({}),
        detected_languages=("python",),
        detected_frameworks=(),
        composer_metadata=(),
        python_metadata=(),
        package_metadata=(),
        tsconfig_metadata=(),
        file_accessor=lambda _path: b"",
        inventory_files=(
            InventoryFile(
                _GRAPH_V2_FIXTURE_PATH,
                _GRAPH_V2_FILE_SHA256,
                "python",
                True,
            ),
        ),
        excluded_path_count=0,
    )
    base = GraphBuilder(generated_at=lambda: "2026-07-19T00:00:00Z").build(
        context,
        (result,),
    )
    prune_started = time.perf_counter()
    base = GraphBudgetPruner().select(
        base,
        BundleLimits(
            max_chunk_uncompressed_bytes=512 * 1024,
            max_bundle_uncompressed_bytes=128 * 1024 * 1024,
            max_chunks=512,
        ),
    )
    prune_ms = _elapsed_ms(prune_started)

    template = next(node for node in base.nodes if node.kind is NodeKind.FUNCTION)
    added_nodes = []
    for index in range(GRAPH_V2_REQUESTED_COUNTS["nodes"] - len(base.nodes)):
        qualified_name = f"benchmark.synthetic_{index:05d}"
        identity = SourceDeclarationIdentity(
            "source_declaration",
            _GRAPH_V2_WORKSPACE_BINDING_ID,
            "python",
            NodeKind.FUNCTION,
            "benchmark",
            qualified_name,
            _GRAPH_V2_FIXTURE_PATH,
        )
        public_id = node_id({
            "variant": "source_declaration",
            "workspace_binding_id": _GRAPH_V2_WORKSPACE_BINDING_ID,
            "language": "python",
            "kind": "function",
            "namespace": "benchmark",
            "qualified_name": qualified_name,
            "path": _GRAPH_V2_FIXTURE_PATH,
        })
        added_nodes.append(
            replace(
                template,
                id=public_id,
                identity=identity,
                name=f"synthetic_{index:05d}",
                qualified_name=qualified_name,
                location=SourceLocation(
                    _GRAPH_V2_FIXTURE_PATH,
                    index + 2,
                    index + 2,
                ),
            )
        )
    nodes = tuple(sorted((*base.nodes, *added_nodes), key=lambda item: item.id))
    callable_nodes = tuple(node for node in nodes if node.kind is NodeKind.FUNCTION)

    added_edges = []
    for index in range(_GRAPH_V2_EDGE_FACT_COUNT - len(base.edges)):
        source = callable_nodes[index % len(callable_nodes)]
        target = callable_nodes[(index + 1) % len(callable_nodes)]
        occurrence = EdgeAstOccurrence(
            "ast",
            source.id,
            f"benchmark/reference/{index:05d}",
            index,
        )
        identity = {
            "source_id": source.id,
            "target_id": target.id,
            "relation": Relation.REFERENCES.value,
            "flow": None,
            "condition_hash": None,
            "branch_group_id": None,
            "call_site_id": None,
            "exception_scope_id": None,
            "occurrence": {
                "kind": "ast",
                "owner_node_id": source.id,
                "ast_path": occurrence.ast_path,
                "ordinal": occurrence.ordinal,
            },
        }
        added_edges.append(
            Edge(
                edge_id(identity),
                source.id,
                target.id,
                Relation.REFERENCES,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                occurrence,
                template.evidence,
                EdgeLocation(_GRAPH_V2_FIXTURE_PATH, index + 1, index),
            )
        )
    edges = tuple(sorted((*base.edges, *added_edges), key=lambda item: item.id))
    records = replace(
        base.graph_contract.coverage.records,
        nodes=len(nodes),
        edges=len(edges),
    )
    coverage = replace(base.graph_contract.coverage, records=records)
    contract = replace(
        base.graph_contract,
        artifact_graph_version="0" * 64,
        coverage=coverage,
    )
    candidate = replace(
        base,
        graph_contract=contract,
        nodes=nodes,
        edges=edges,
    )
    version = artifact_graph_version(artifact_to_payload(candidate))
    return (
        replace(
            candidate,
            graph_contract=replace(contract, artifact_graph_version=version),
        ),
        prune_ms,
    )


def _run_workspace_cases(workspace_root: Path) -> list[dict[str, Any]]:
    root = workspace_root.resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"workspace does not exist or is not a directory: {root}")

    cases: list[dict[str, Any]] = []
    jobs = [
        {
            "name": "workspace_git_tree",
            "capability": "sync_git_tree",
            "payload": {"max_files": 20_000, "max_bytes": 20_000_000, "max_file_bytes": 1_000_000},
        },
        {
            "name": "workspace_code_graph",
            "capability": "populate_backend_ast",
            "payload": {
                "project_id": _GRAPH_V2_PROJECT_ID,
                "workspace_binding_id": _GRAPH_V2_WORKSPACE_BINDING_ID,
            },
        },
    ]
    for job in jobs:
        started = time.perf_counter()
        result = execute_job({"capability": job["capability"], "payload": job["payload"]}, workspace_root=root)
        index_duration_ms = max(0, int((time.perf_counter() - started) * 1000))
        artifact = result.get("artifact") if isinstance(result, dict) else None
        if not isinstance(artifact, dict):
            cases.append(
                {
                    "name": job["name"],
                    "source": "workspace",
                    "workspace": root.name,
                    "job_capability": job["capability"],
                    "schema": None,
                    "status": "failed",
                    "index_duration_ms": index_duration_ms,
                    "duration_ms": 0,
                    "total_duration_ms": index_duration_ms,
                    "upload_mode": "none",
                    "original_bytes": 0,
                    "compressed_bytes": 0,
                    "compression_ratio": None,
                    "payload_sha256": None,
                    "warnings": [f"{job['name']}: no artifact produced"],
                }
            )
            continue
        case = (
            _run_graph_v2_manifest_case(name=job["name"], artifact=artifact)
            if job["capability"] == "populate_backend_ast"
            else _run_artifact_case(name=job["name"], artifact=artifact)
        )
        case.update(
            {
                "source": "workspace",
                "workspace": root.name,
                "job_capability": job["capability"],
                "job_status": result.get("status"),
                "summary": result.get("summary"),
                "index_duration_ms": index_duration_ms,
                "total_duration_ms": index_duration_ms + int(case["duration_ms"]),
            }
        )
        cases.append(case)
    return cases


def _run_graph_v2_manifest_case(
    *, name: str, artifact: dict[str, Any]
) -> dict[str, Any]:
    from hermes_cli.hades_graph_v2.bundle import CHUNK_KINDS
    from hermes_cli.hades_graph_v2.identity import canonical_json_bytes

    manifest = artifact.get("bundle")
    if artifact.get("schema") != "hades.code_graph.v2" or not isinstance(
        manifest, dict
    ) or manifest.get("schema") != "hades.graph_bundle.v2":
        raise ValueError("workspace graph benchmark requires a graph v2 bundle")
    started = time.perf_counter()
    manifest_bytes = canonical_json_bytes(manifest)
    manifest_counts = {
        kind: int(manifest["counts"][kind])
        for kind in CHUNK_KINDS
    }
    descriptor_counts = {kind: 0 for kind in CHUNK_KINDS}
    for descriptor in manifest["chunks"]:
        descriptor_counts[str(descriptor["kind"])] += int(descriptor["record_count"])
    if descriptor_counts != manifest_counts:
        raise RuntimeError("workspace graph v2 chunk ledger does not close")
    original_bytes = len(manifest_bytes) + sum(
        int(descriptor["uncompressed_bytes"])
        for descriptor in manifest["chunks"]
    )
    compressed_bytes = len(manifest_bytes) + sum(
        int(descriptor["compressed_bytes"])
        for descriptor in manifest["chunks"]
    )
    duration_ms = _elapsed_ms(started)
    compression_ratio = (
        round(compressed_bytes / original_bytes, 4)
        if original_bytes
        else None
    )
    payload_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    return {
        "name": name,
        "schema": "hades.code_graph.v2",
        "bundle_schema": "hades.graph_bundle.v2",
        "artifact_graph_version": artifact["artifact_graph_version"],
        "manifest_counts": manifest_counts,
        "descriptor_counts": descriptor_counts,
        "chunk_count": len(manifest["chunks"]),
        "symbol_count": manifest_counts["nodes"],
        "route_count": manifest_counts["entrypoints"],
        "edge_count": manifest_counts["edges"],
        "file_count": None,
        "raw_source_included": False,
        "truncated": bool(
            manifest["graph_contract"]["coverage"]["records"]
            ["omitted_by_bundle_budget"]
        ),
        "redactions": 0,
        "payload_sha256": payload_sha256,
        "manifest_sha256": payload_sha256,
        "upload_mode": "chunked",
        "original_bytes": original_bytes,
        "compressed_bytes": compressed_bytes,
        "compression_ratio": compression_ratio,
        "duration_ms": duration_ms,
        "warnings": [],
    }


def _run_artifact_case(*, name: str, artifact: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    upload_fields, compression = _artifact_upload_fields(artifact)
    payload_hash = _artifact_payload_hash(artifact)
    duration_ms = max(0, int((time.perf_counter() - started) * 1000))
    original_bytes = int(compression.get("original_bytes") or 0)
    compressed_bytes = int(compression.get("compressed_bytes") or 0)
    ratio = round(compressed_bytes / original_bytes, 4) if original_bytes and compressed_bytes else None
    upload_mode = "compressed" if upload_fields.get("artifact_encoding") == "gzip+base64" else "raw"
    warnings = _case_warnings(
        name=name,
        duration_ms=duration_ms,
        upload_mode=upload_mode,
        compression_ratio=ratio,
        original_bytes=original_bytes,
    )

    return {
        "name": name,
        "schema": artifact["schema"],
        "symbol_count": len(artifact.get("symbols") or []),
        "route_count": len(artifact.get("routes") or []),
        "edge_count": len(artifact.get("edges") or []),
        "file_count": len(artifact.get("files") or []),
        "raw_source_included": bool(artifact.get("raw_source_included", False)),
        "truncated": bool(artifact.get("truncated", False)),
        "redactions": int(artifact.get("redactions") or 0),
        "payload_sha256": payload_hash,
        "upload_mode": upload_mode,
        "original_bytes": original_bytes,
        "compressed_bytes": compressed_bytes,
        "compression_ratio": ratio,
        "duration_ms": duration_ms,
        "warnings": warnings,
    }


def _case_warnings(
    *,
    name: str,
    duration_ms: int,
    upload_mode: str,
    compression_ratio: float | None,
    original_bytes: int,
) -> list[str]:
    warnings: list[str] = []
    if duration_ms > DURATION_WARN_MS:
        warnings.append(f"{name}: artifact serialization/compression exceeded {DURATION_WARN_MS}ms")
    if original_bytes >= 256 * 1024 and upload_mode != "compressed":
        warnings.append(f"{name}: large artifact did not use compressed upload")
    if compression_ratio is not None and original_bytes >= 256 * 1024 and compression_ratio > LARGE_COMPRESSION_RATIO_WARN:
        warnings.append(f"{name}: compressed payload ratio {compression_ratio} is above {LARGE_COMPRESSION_RATIO_WARN}")
    return warnings
