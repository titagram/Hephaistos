from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path

import pytest

from hermes_cli.hades_graph_v2 import (
    GraphValidationError,
    artifact_from_payload,
    artifact_graph_version,
    artifact_to_payload,
    ast_source_fingerprint,
    edge_id,
    file_source_fingerprint,
    flow_id,
    flow_step_id,
    node_id,
    validate_artifact,
)
from hermes_cli.hades_graph_v2.bundle import BundleLimits, GraphBundleWriter
from tests.hermes_cli.test_hades_graph_contract import (
    _valid_flow_artifact,
    _valid_semantic_artifact,
)


def _rehash(payload: dict) -> None:
    payload["graph_contract"]["artifact_graph_version"] = artifact_graph_version(
        payload
    )


def _route_artifact(count: int, *, first_label_padding: int = 0) -> dict:
    """Extend the validated shared-handler fixture with independent routes."""

    payload = copy.deepcopy(_valid_flow_artifact())
    template_entrypoint = payload["entrypoints"][0]
    template_entrypoint_node = next(
        node for node in payload["nodes"] if node["id"] == template_entrypoint["id"]
    )
    route_edge = next(
        edge for edge in payload["edges"] if edge["relation"] == "routes_to"
    )
    dispatch_edge = next(
        edge for edge in payload["edges"] if edge["relation"] == "dispatches"
    )
    response_edge = next(
        edge for edge in payload["edges"] if edge["relation"] == "responds_with"
    )
    exit_edge = next(
        edge for edge in payload["edges"] if edge["relation"] == "exits_at"
    )
    sync_flow = next(flow for flow in payload["flows"] if flow["kind"] != "async_flow")
    child_flow = next(flow for flow in payload["flows"] if flow["kind"] == "async_flow")
    sync_steps = [
        step for step in payload["flow_steps"] if step["flow_id"] == sync_flow["id"]
    ]
    child_steps = [
        step for step in payload["flow_steps"] if step["flow_id"] == child_flow["id"]
    ]
    binding_id = payload["project"]["workspace_binding_id"]
    file_node = next(node for node in payload["nodes"] if node["kind"] == "file")
    path = file_node["identity"]["path"]
    file_digest = file_node["properties"]["file_sha256"]
    handler_id = template_entrypoint["handler_node_id"]
    job_id = child_flow["root_node_id"]

    for ordinal in range(1, count):
        padding = "a" * first_label_padding if ordinal == 1 else ""
        public_path = f"/{padding}route-{ordinal:04d}"
        label = f"GET {public_path}"
        public_name = f"App\\Http\\route{ordinal:04d}"
        structural_path = f"routes/generated/{ordinal:04d}"
        identity_fields = {
            "entrypoint_kind": "http_route",
            "framework": None,
            "method_semantics": "explicit",
            "methods": ["GET"],
            "public_path": public_path,
            "public_name": public_name,
            "trigger": {"kind": "http", "value": label},
            "match_constraints": {
                "host": None,
                "schemes": [],
                "condition_hash": None,
            },
            "registration_occurrence": {
                "kind": "ast",
                "path": path,
                "structural_path": structural_path,
                "ordinal": ordinal,
            },
        }
        entrypoint_node_identity = {
            "variant": "entrypoint",
            "workspace_binding_id": binding_id,
            "language": "php",
            "kind": "entrypoint",
            "path": path,
            "entrypoint_identity": identity_fields,
        }
        entrypoint_id = node_id(entrypoint_node_identity)
        evidence = copy.deepcopy(template_entrypoint["evidence"])
        evidence["primary"]["source_locator"] = {
            "kind": "ast",
            "path": path,
            "structural_path": structural_path,
        }
        evidence["primary"]["source_fingerprint"] = ast_source_fingerprint(
            file_digest, path, structural_path
        )
        node = copy.deepcopy(template_entrypoint_node)
        node.update({
            "id": entrypoint_id,
            "identity": entrypoint_node_identity,
            "name": label,
            "qualified_name": public_name,
            "evidence": evidence,
        })
        payload["nodes"].append(node)
        payload["entrypoints"].append({
            "id": entrypoint_id,
            "entrypoint_kind": "http_route",
            "label": label,
            "framework": None,
            "method_semantics": "explicit",
            "methods": ["GET"],
            "public_path": public_path,
            "public_name": public_name,
            "handler_node_id": handler_id,
            "uncertainty_id": None,
            "trigger": identity_fields["trigger"],
            "match_constraints": identity_fields["match_constraints"],
            "registration_occurrence": identity_fields["registration_occurrence"],
            "evidence": copy.deepcopy(evidence),
        })

        occurrence = {
            "kind": "ast",
            "owner_node_id": entrypoint_id,
            "ast_path": f"{structural_path}/handler",
            "ordinal": 0,
        }
        edge_identity = {
            "source_id": entrypoint_id,
            "target_id": handler_id,
            "relation": "routes_to",
            "flow": "always",
            "condition_hash": None,
            "branch_group_id": None,
            "call_site_id": None,
            "exception_scope_id": None,
            "occurrence": occurrence,
        }
        public_edge_id = edge_id(edge_identity)
        cloned_route_edge = copy.deepcopy(route_edge)
        cloned_route_edge.update({
            "id": public_edge_id,
            "source_id": entrypoint_id,
            "occurrence": occurrence,
            "evidence": copy.deepcopy(evidence),
            "location": {"path": path, "line": 1, "ordinal": ordinal},
        })
        payload["edges"].append(cloned_route_edge)

        sync_flow_id = flow_id(entrypoint_id, entrypoint_id, "request_lifecycle")
        child_flow_id = flow_id(entrypoint_id, job_id, "async_flow")
        new_sync = copy.deepcopy(sync_flow)
        new_sync.update({
            "id": sync_flow_id,
            "entrypoint_id": entrypoint_id,
            "root_node_id": entrypoint_id,
        })
        new_child = copy.deepcopy(child_flow)
        new_child.update({"id": child_flow_id, "entrypoint_id": entrypoint_id})
        payload["flows"].extend((new_sync, new_child))

        for template_step in sync_steps:
            step = copy.deepcopy(template_step)
            relation = next(
                edge["relation"]
                for edge in payload["edges"]
                if edge["id"] == template_step["edge_id"]
            )
            selected_edge = {
                "routes_to": cloned_route_edge,
                "dispatches": dispatch_edge,
                "responds_with": response_edge,
            }[relation]
            step.update({
                "id": flow_step_id(
                    sync_flow_id,
                    selected_edge["id"],
                    step["stage_from"],
                    step["stage_to"],
                    step["async_context"],
                ),
                "flow_id": sync_flow_id,
                "edge_id": selected_edge["id"],
                "async_child_flow_id": (
                    child_flow_id if relation == "dispatches" else None
                ),
                "order_key": (
                    f"{0 if step['stage_from'] == 'entry' else 5:02d}:"
                    f"{step['min_depth']:06d}:{selected_edge['source_id']}:"
                    f"{selected_edge['target_id']}:{selected_edge['id']}"
                ),
            })
            payload["flow_steps"].append(step)
        for template_step in child_steps:
            step = copy.deepcopy(template_step)
            step.update({
                "id": flow_step_id(
                    child_flow_id,
                    exit_edge["id"],
                    step["stage_from"],
                    step["stage_to"],
                    step["async_context"],
                ),
                "flow_id": child_flow_id,
            })
            payload["flow_steps"].append(step)

    for name in ("entrypoints", "nodes", "edges", "flows", "flow_steps"):
        payload[name].sort(key=lambda record: record["id"])
    coverage = payload["graph_contract"]["coverage"]
    coverage["entrypoints"].update(
        detected=count,
        analyzed=count,
        partial=0,
        by_kind={"http_route": count},
    )
    coverage["records"].update(
        nodes=len(payload["nodes"]),
        edges=len(payload["edges"]),
        flows=len(payload["flows"]),
        flow_steps=len(payload["flow_steps"]),
    )
    _rehash(payload)
    validate_artifact(payload)
    return payload


def _limits(*, chunk: int = 64 * 1024, total: int = 16 * 1024 * 1024) -> BundleLimits:
    return BundleLimits(
        max_chunk_uncompressed_bytes=chunk,
        max_bundle_uncompressed_bytes=total,
        max_chunks=512,
    )


def _logical_bytes(bundle) -> int:
    from hermes_cli.hades_graph_v2 import canonical_json_bytes

    return len(canonical_json_bytes(bundle.manifest)) + sum(
        descriptor["uncompressed_bytes"] for descriptor in bundle.manifest["chunks"]
    )


def _omitted_public_records(before: dict, after: dict) -> int:
    kinds = (
        "entrypoints",
        "nodes",
        "structures",
        "edges",
        "flows",
        "flow_steps",
        "uncertainties",
    )
    return sum(
        len({record["id"] for record in before[kind]})
        - len({record["id"] for record in after[kind]})
        for kind in kinds
    )


def _assert_validator_and_writer_reject(
    payload: dict,
    tmp_path: Path,
    *,
    code: str,
) -> None:
    with pytest.raises(GraphValidationError) as validation_error:
        validate_artifact(payload)
    assert validation_error.value.code == code

    with pytest.raises(GraphValidationError) as writer_error:
        GraphBundleWriter().write(payload, tmp_path / code, _limits())
    assert writer_error.value.code == code


def test_validator_and_writer_reject_silent_missing_file_with_zero_omission_ledger(
    tmp_path: Path,
):
    payload = copy.deepcopy(_valid_semantic_artifact())
    payload["nodes"] = []
    coverage = payload["graph_contract"]["coverage"]
    coverage["files"].update(analyzed=0, budget_omitted=1)
    coverage["records"].update(nodes=0, omitted_by_bundle_budget=0)
    payload["languages"][0]["analyzed_file_count"] = 0
    _rehash(payload)

    _assert_validator_and_writer_reject(
        payload,
        tmp_path,
        code="coverage_omission_ledger",
    )


def test_validator_and_writer_reject_missing_php_file_with_full_language_scope(
    tmp_path: Path,
):
    payload = copy.deepcopy(_valid_semantic_artifact())
    completeness = payload["graph_contract"]["completeness"]
    completeness["languages"][0]["capabilities"] = copy.deepcopy(
        completeness["languages"][0]["capabilities"]
    )
    payload["nodes"] = []
    coverage = payload["graph_contract"]["coverage"]
    coverage["files"].update(analyzed=0, budget_omitted=1)
    coverage["records"].update(nodes=0, omitted_by_bundle_budget=1)
    payload["languages"][0]["analyzed_file_count"] = 0
    completeness["status"] = "partial"
    completeness["capabilities"]["inventory"] = {
        "status": "partial",
        "reasons": [
            {
                "code": "resource_budget_reached",
                "count": 1,
                "language": None,
                "paths_sample": ["src/Example.php"],
            }
        ],
    }
    assert completeness["languages"][0]["status"] == "full"
    assert completeness["languages"][0]["capabilities"]["inventory"] == {
        "status": "full",
        "reasons": [],
    }
    _rehash(payload)

    _assert_validator_and_writer_reject(
        payload,
        tmp_path,
        code="coverage_omission_completeness",
    )


def test_validator_and_writer_reject_silent_missing_entrypoint_with_zero_ledger(
    tmp_path: Path,
):
    from hermes_cli.hades_graph_v2.pruning import GraphBudgetPruner

    payload = _route_artifact(1)
    measured = GraphBundleWriter().write(
        payload, tmp_path / "measure", _limits(total=32 * 1024 * 1024)
    )
    selected = GraphBudgetPruner().select(
        artifact_from_payload(payload),
        _limits(total=_logical_bytes(measured) - 1),
    )
    forged = artifact_to_payload(selected)
    assert forged["graph_contract"]["coverage"]["entrypoints"]["detected"] == 1
    assert forged["entrypoints"] == []
    forged["graph_contract"]["coverage"]["records"]["omitted_by_bundle_budget"] = 0
    _rehash(forged)

    _assert_validator_and_writer_reject(
        forged,
        tmp_path,
        code="coverage_omission_ledger",
    )


def test_validator_and_writer_reject_arbitrary_observable_budget_reason_count(
    tmp_path: Path,
):
    payload = copy.deepcopy(_valid_semantic_artifact())
    file_node = payload["nodes"][0]
    file_node["properties"].update(
        analysis_status="budget_omitted",
        omission_reason="resource_budget_reached",
    )
    coverage = payload["graph_contract"]["coverage"]
    coverage["files"].update(analyzed=0, budget_omitted=1)
    payload["languages"][0]["analyzed_file_count"] = 0
    reason = {
        "code": "resource_budget_reached",
        "count": 999,
        "language": "php",
        "paths_sample": [file_node["identity"]["path"]],
    }
    completeness = payload["graph_contract"]["completeness"]
    completeness["status"] = "partial"
    completeness["capabilities"]["inventory"] = {
        "status": "partial",
        "reasons": [copy.deepcopy(reason)],
    }
    completeness["languages"][0]["status"] = "partial"
    completeness["languages"][0]["capabilities"]["inventory"] = {
        "status": "partial",
        "reasons": [copy.deepcopy(reason)],
    }
    _rehash(payload)

    _assert_validator_and_writer_reject(
        payload,
        tmp_path,
        code="capability_reason_count_mismatch",
    )


def test_validator_and_writer_require_ledger_to_cover_disjoint_file_and_entrypoint_gaps(
    tmp_path: Path,
):
    payload = copy.deepcopy(_valid_semantic_artifact())
    completeness = payload["graph_contract"]["completeness"]
    completeness["languages"][0]["capabilities"] = copy.deepcopy(
        completeness["languages"][0]["capabilities"]
    )
    payload["nodes"] = []
    coverage = payload["graph_contract"]["coverage"]
    coverage["files"].update(analyzed=0, budget_omitted=1)
    coverage["entrypoints"].update(
        detected=1,
        analyzed=0,
        partial=1,
        by_kind={"http_route": 1},
    )
    coverage["records"].update(nodes=0, omitted_by_bundle_budget=1)
    payload["languages"][0]["analyzed_file_count"] = 0
    inventory_reason = {
        "code": "resource_budget_reached",
        "count": 1,
        "language": "php",
        "paths_sample": ["src/Example.php"],
    }
    entrypoint_reason = {
        "code": "resource_budget_reached",
        "count": 1,
        "language": None,
        "paths_sample": [],
    }
    completeness["status"] = "partial"
    completeness["capabilities"]["inventory"] = {
        "status": "partial",
        "reasons": [copy.deepcopy(inventory_reason)],
    }
    completeness["capabilities"]["entrypoint_discovery"] = {
        "status": "partial",
        "reasons": [copy.deepcopy(entrypoint_reason)],
    }
    completeness["languages"][0]["status"] = "partial"
    completeness["languages"][0]["capabilities"]["inventory"] = {
        "status": "partial",
        "reasons": [copy.deepcopy(inventory_reason)],
    }
    _rehash(payload)

    _assert_validator_and_writer_reject(
        payload,
        tmp_path,
        code="coverage_omission_ledger",
    )


def test_validator_and_writer_bound_budget_reason_counts_by_explicit_ledger(
    tmp_path: Path,
):
    from hermes_cli.hades_graph_v2.pruning import GraphBudgetPruner

    payload = _route_artifact(1)
    measured = GraphBundleWriter().write(
        payload, tmp_path / "bounded-measure", _limits(total=32 * 1024 * 1024)
    )
    selected = GraphBudgetPruner().select(
        artifact_from_payload(payload),
        _limits(total=_logical_bytes(measured) - 1),
    )
    forged = artifact_to_payload(selected)
    ledger = forged["graph_contract"]["coverage"]["records"]["omitted_by_bundle_budget"]
    assert ledger > 0
    completeness = forged["graph_contract"]["completeness"]
    scopes = [completeness["capabilities"]]
    scopes.extend(row["capabilities"] for row in completeness["languages"])
    mutated = 0
    for capabilities in scopes:
        for capability in capabilities.values():
            for reason in capability["reasons"]:
                if reason["code"] in {
                    "record_too_large",
                    "resource_budget_reached",
                }:
                    reason["count"] = 999
                    mutated += 1
    assert mutated > 0
    _rehash(forged)

    _assert_validator_and_writer_reject(
        forged,
        tmp_path,
        code="capability_reason_count_mismatch",
    )


def test_single_flow_over_budget_is_rejected_atomically_and_counted(tmp_path: Path):
    from hermes_cli.hades_graph_v2.pruning import GraphBudgetPruner

    payload = _route_artifact(1)
    generous = GraphBundleWriter().write(
        payload, tmp_path / "generous", _limits(total=32 * 1024 * 1024)
    )
    selected = GraphBudgetPruner().select(
        artifact_from_payload(payload),
        _limits(total=_logical_bytes(generous) - 1),
    )
    result = artifact_to_payload(selected)

    assert result["entrypoints"] == []
    assert result["flows"] == []
    assert result["flow_steps"] == []
    assert result["graph_contract"]["coverage"]["entrypoints"] == {
        "detected": 1,
        "analyzed": 0,
        "partial": 1,
        "by_kind": {"http_route": 1},
    }
    assert result["graph_contract"]["coverage"]["records"][
        "omitted_by_bundle_budget"
    ] == _omitted_public_records(payload, result)
    assert "resource_budget_reached" in {
        reason["code"]
        for reason in result["graph_contract"]["completeness"]["capabilities"][
            "entrypoint_discovery"
        ]["reasons"]
    }
    validate_artifact(selected)


def test_shared_topology_survives_rejected_large_flow_and_smaller_later_unit_fits():
    from hermes_cli.hades_graph_v2.pruning import GraphBudgetPruner

    payload = _route_artifact(3, first_label_padding=220)
    selected = GraphBudgetPruner().select(
        artifact_from_payload(payload),
        _limits(total=23_000),
    )
    result = artifact_to_payload(selected)

    labels = {record["label"] for record in result["entrypoints"]}
    assert labels == {"GET /jobs"}
    handler_id = payload["entrypoints"][0]["handler_node_id"]
    assert sum(node["id"] == handler_id for node in result["nodes"]) == 1
    assert result["graph_contract"]["coverage"]["entrypoints"]["detected"] == 3
    assert result["graph_contract"]["coverage"]["entrypoints"]["partial"] >= 1
    validate_artifact(selected)


def test_oversized_record_rejects_its_whole_unit_with_record_reason():
    from hermes_cli.hades_graph_v2.pruning import GraphBudgetPruner

    payload = _route_artifact(2, first_label_padding=220)
    selected = GraphBudgetPruner().select(
        artifact_from_payload(payload), _limits(chunk=1_900)
    )
    result = artifact_to_payload(selected)

    assert not any(
        entrypoint["label"].startswith("GET /aaa")
        for entrypoint in result["entrypoints"]
    )
    assert "record_too_large" in {
        reason["code"]
        for reason in result["graph_contract"]["completeness"]["capabilities"][
            "entrypoint_discovery"
        ]["reasons"]
    }
    validate_artifact(selected)


def test_exact_serialized_bundle_ceiling_is_accepted(tmp_path: Path):
    from hermes_cli.hades_graph_v2.pruning import GraphBudgetPruner

    payload = _route_artifact(2)
    bundle = GraphBundleWriter().write(
        payload, tmp_path / "measure", _limits(total=32 * 1024 * 1024)
    )
    selected = GraphBudgetPruner().select(
        artifact_from_payload(payload), _limits(total=_logical_bytes(bundle))
    )

    assert artifact_to_payload(selected) == payload
    validate_artifact(selected)


def _many_file_artifact(count: int) -> dict:
    payload = copy.deepcopy(_valid_semantic_artifact())
    template = payload["nodes"][0]
    binding = payload["project"]["workspace_binding_id"]
    for ordinal in range(1, count):
        path = f"src/generated/file-{ordinal:05d}.php"
        digest = f"{ordinal:064x}"
        identity = {
            "variant": "file",
            "workspace_binding_id": binding,
            "language": "php",
            "kind": "file",
            "path": path,
        }
        node = copy.deepcopy(template)
        node.update({
            "id": node_id(identity),
            "identity": identity,
            "name": Path(path).name,
            "qualified_name": path,
            "properties": {
                **template["properties"],
                "file_sha256": digest,
            },
        })
        node["evidence"]["primary"]["source_locator"] = {
            "kind": "file",
            "path": path,
        }
        node["evidence"]["primary"]["source_fingerprint"] = file_source_fingerprint(
            digest, path
        )
        payload["nodes"].append(node)
    payload["nodes"].sort(key=lambda record: record["id"])
    files = payload["graph_contract"]["coverage"]["files"]
    files.update(
        discovered=count,
        hashed=count,
        parser_candidates=count,
        analyzed=count,
    )
    payload["languages"][0].update(detected_file_count=count, analyzed_file_count=count)
    payload["graph_contract"]["coverage"]["records"]["nodes"] = count
    _rehash(payload)
    validate_artifact(payload)
    return payload


def test_more_than_5000_nodes_has_no_entity_count_truncation():
    from hermes_cli.hades_graph_v2.pruning import GraphBudgetPruner

    payload = _many_file_artifact(5_001)
    selected = GraphBudgetPruner().select(
        artifact_from_payload(payload),
        _limits(chunk=8 * 1024 * 1024, total=64 * 1024 * 1024),
    )

    assert len(selected.nodes) == 5_001
    assert selected.graph_contract.coverage.records.omitted_by_bundle_budget == 0


def _many_edge_artifact(count: int) -> dict:
    payload = copy.deepcopy(_valid_semantic_artifact())
    file_node = payload["nodes"][0]
    path = file_node["identity"]["path"]
    file_digest = file_node["properties"]["file_sha256"]
    binding = payload["project"]["workspace_binding_id"]
    owner_identity = {
        "variant": "source_declaration",
        "workspace_binding_id": binding,
        "language": "php",
        "kind": "method",
        "namespace": "App",
        "qualified_name": "App\\EdgeOwner::run",
        "path": path,
    }
    owner_id = node_id(owner_identity)
    owner = {
        "id": owner_id,
        "identity": owner_identity,
        "kind": "method",
        "language": "php",
        "framework": None,
        "name": "run",
        "qualified_name": "App\\EdgeOwner::run",
        "namespace": "App",
        "uncertainty_id": None,
        "location": {"path": path, "start_line": 1, "end_line": 1},
        "properties": {},
        "evidence": {
            "primary": {
                "origin": "verified_from_code",
                "extractor": "test.task15",
                "source_locator": {
                    "kind": "ast",
                    "path": path,
                    "structural_path": "declaration/edge-owner",
                },
                "source_fingerprint": ast_source_fingerprint(
                    file_digest, path, "declaration/edge-owner"
                ),
                "inference_rule": None,
            },
            "supporting": [],
            "supporting_omitted_count": 0,
        },
    }
    payload["nodes"].append(owner)
    for ordinal in range(count):
        ast_path = f"body/reference/{ordinal}"
        occurrence = {
            "kind": "ast",
            "owner_node_id": owner_id,
            "ast_path": ast_path,
            "ordinal": ordinal,
        }
        identity = {
            "source_id": owner_id,
            "target_id": file_node["id"],
            "relation": "references",
            "flow": None,
            "condition_hash": None,
            "branch_group_id": None,
            "call_site_id": None,
            "exception_scope_id": None,
            "occurrence": occurrence,
        }
        payload["edges"].append({
            "id": edge_id(identity),
            "source_id": owner_id,
            "target_id": file_node["id"],
            "relation": "references",
            "flow": None,
            "condition": None,
            "branch_group_id": None,
            "call_site_id": None,
            "exception_scope_id": None,
            "order": None,
            "uncertainty_id": None,
            "occurrence": occurrence,
            "evidence": copy.deepcopy(owner["evidence"]),
            "location": {"path": path, "line": 1, "ordinal": ordinal},
        })
    payload["nodes"].sort(key=lambda record: record["id"])
    payload["edges"].sort(key=lambda record: record["id"])
    records = payload["graph_contract"]["coverage"]["records"]
    records.update(nodes=2, edges=count)
    _rehash(payload)
    validate_artifact(payload)
    return payload


def test_more_than_10000_edges_has_no_entity_count_truncation():
    from hermes_cli.hades_graph_v2.pruning import GraphBudgetPruner

    payload = _many_edge_artifact(10_001)
    selected = GraphBudgetPruner().select(
        artifact_from_payload(payload),
        _limits(chunk=8 * 1024 * 1024, total=128 * 1024 * 1024),
    )

    assert len(selected.edges) == 10_001
    assert selected.graph_contract.coverage.records.omitted_by_bundle_budget == 0


def test_edge_only_ceiling_finalizes_one_candidate(monkeypatch):
    import hermes_cli.hades_graph_v2.pruning as pruning

    payload = _many_edge_artifact(101)
    real_finalize = pruning._finalize_candidate
    finalize_calls = 0

    def tracked_finalize(*args, **kwargs):
        nonlocal finalize_calls
        finalize_calls += 1
        return real_finalize(*args, **kwargs)

    monkeypatch.setattr(pruning, "_finalize_candidate", tracked_finalize)
    selected = pruning.GraphBudgetPruner().select(
        artifact_from_payload(payload),
        replace(
            _limits(chunk=8 * 1024 * 1024, total=128 * 1024 * 1024),
            max_edges=100,
        ),
    )

    assert len(selected.edges) == 100
    assert selected.graph_contract.coverage.records.omitted_by_bundle_budget == 1
    assert finalize_calls == 1


def test_more_than_500_routes_has_no_entity_count_truncation():
    from hermes_cli.hades_graph_v2.pruning import GraphBudgetPruner

    payload = _route_artifact(501)
    selected = GraphBudgetPruner().select(
        artifact_from_payload(payload),
        _limits(chunk=8 * 1024 * 1024, total=128 * 1024 * 1024),
    )

    assert len(selected.entrypoints) == 501
    assert selected.graph_contract.coverage.records.omitted_by_bundle_budget == 0


def test_empty_envelope_that_cannot_fit_is_a_hard_failure():
    from hermes_cli.hades_graph_v2.pruning import GraphBudgetError, GraphBudgetPruner

    with pytest.raises(GraphBudgetError, match="graph_bundle_budget_too_small"):
        GraphBudgetPruner().select(
            artifact_from_payload(_valid_semantic_artifact()),
            _limits(chunk=512, total=32),
        )


def test_overlapping_rejections_preserve_inventory_provenance_across_total_band():
    from hermes_cli.hades_graph_v2.bundle import build_bundle_plan
    from hermes_cli.hades_graph_v2.pruning import (
        GraphBudgetPruner,
        GraphBundleBudgetTooSmallError,
    )

    artifact = artifact_from_payload(_valid_flow_artifact())
    selected_at_5000 = GraphBudgetPruner().select(
        artifact,
        _limits(chunk=2_048, total=5_000),
    )
    validate_artifact(selected_at_5000)
    empty_payload = artifact_to_payload(selected_at_5000)
    assert all(
        not empty_payload[kind]
        for kind in (
            "entrypoints",
            "nodes",
            "structures",
            "edges",
            "flows",
            "flow_steps",
            "uncertainties",
        )
    )
    empty_plan = build_bundle_plan(
        selected_at_5000,
        _limits(chunk=2_048, total=5_000),
    )
    exact_empty_total = empty_plan.logical_uncompressed_bytes
    assert 3_000 < exact_empty_total < 5_000

    ceilings = set(range(3_000, 5_001, 250))
    ceilings.update({exact_empty_total - 1, exact_empty_total})
    for ceiling in sorted(ceilings):
        limits = _limits(chunk=2_048, total=ceiling)
        if ceiling < exact_empty_total:
            with pytest.raises(GraphBundleBudgetTooSmallError) as exc_info:
                GraphBudgetPruner().select(artifact, limits)
            assert exc_info.value.code == "graph_bundle_budget_too_small"
            continue

        selected = GraphBudgetPruner().select(artifact, limits)
        validate_artifact(selected)
        plan = build_bundle_plan(selected, limits)
        assert plan.logical_uncompressed_bytes <= ceiling
        assert selected.graph_contract.completeness.status.value == "partial"


@pytest.mark.parametrize("manifest_ceiling", [4_000, 4_025, 4_050])
def test_overlapping_rejections_preserve_inventory_in_record_derived_manifest_band(
    monkeypatch,
    manifest_ceiling: int,
):
    from hermes_cli.hades_graph_v2 import bundle as bundle_module
    from hermes_cli.hades_graph_v2.pruning import GraphBudgetPruner

    artifact = artifact_from_payload(_valid_flow_artifact())
    limits = _limits(chunk=2_048, total=32 * 1024 * 1024)
    full = bundle_module.build_bundle_plan(artifact, limits)
    assert len(full.manifest_bytes) > manifest_ceiling
    monkeypatch.setattr(bundle_module, "MAX_MANIFEST_BYTES", manifest_ceiling)

    selected = GraphBudgetPruner().select(artifact, limits)
    validate_artifact(selected)
    payload = artifact_to_payload(selected)
    assert all(
        not payload[kind]
        for kind in (
            "entrypoints",
            "nodes",
            "structures",
            "edges",
            "flows",
            "flow_steps",
            "uncertainties",
        )
    )
    plan = bundle_module.build_bundle_plan(selected, limits)
    assert len(plan.manifest_bytes) <= manifest_ceiling
    inventory = selected.graph_contract.completeness.capabilities.inventory
    assert inventory.status.value == "partial"
    assert {reason.code.value for reason in inventory.reasons} & {
        "resource_budget_reached",
        "record_too_large",
    }


@pytest.mark.parametrize("reverse", [False, True])
def test_unit_selection_is_permutation_invariant(reverse: bool):
    from hermes_cli.hades_graph_v2.pruning import GraphBudgetPruner

    payload = _route_artifact(3, first_label_padding=200)
    if reverse:
        # The model boundary canonicalizes these arrays before selection; the
        # semantic input remains the same even when construction order differs.
        for name in ("entrypoints", "nodes", "edges", "flows", "flow_steps"):
            payload[name] = list(reversed(payload[name]))
            payload[name].sort(key=lambda record: record["id"])
    selected = GraphBudgetPruner().select(
        artifact_from_payload(payload), _limits(total=22_000)
    )
    result = artifact_to_payload(selected)

    assert [entrypoint["label"] for entrypoint in result["entrypoints"]] == [
        "GET /jobs"
    ]
    assert result["graph_contract"]["coverage"]["records"][
        "omitted_by_bundle_budget"
    ] == _omitted_public_records(payload, result)
    assert result["graph_contract"]["artifact_graph_version"] == artifact_graph_version(
        result
    )
