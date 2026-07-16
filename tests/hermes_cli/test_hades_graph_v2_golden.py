from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from referencing import Registry, Resource

from hermes_cli.hades_graph_contract import (
    canonical_json_bytes,
    load_json_bytes,
    normalize_source_path,
)


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = ROOT / "contracts" / "hades" / "graph-v2"
SCHEMAS = (
    "artifact.schema.json",
    "bundle.schema.json",
    "chunk.schema.json",
    "dashboard-query.schema.json",
    "dashboard-response.schema.json",
    "verification-work.schema.json",
    "verification-result.schema.json",
    "graph-overlay.schema.json",
)
FORMAT_CHECKER = FormatChecker()


def _all_object_schemas_are_closed(value: Any) -> bool:
    if isinstance(value, list):
        return all(_all_object_schemas_are_closed(item) for item in value)
    if not isinstance(value, dict):
        return True
    if value.get("type") == "object" and value.get("additionalProperties") is not False:
        return False
    return all(_all_object_schemas_are_closed(item) for item in value.values())


def _contract_document(name: str) -> dict[str, Any]:
    return json.loads((CONTRACT_ROOT / name).read_text(encoding="utf-8"))


def _contract_registry() -> Registry:
    registry = Registry()
    for name in SCHEMAS:
        document = _contract_document(name)
        registry = registry.with_resource(
            document["$id"], Resource.from_contents(document)
        )
    return registry


def validate_contract(name: str, instance: Any) -> None:
    Draft202012Validator(
        _contract_document(name),
        registry=_contract_registry(),
        format_checker=FORMAT_CHECKER,
    ).validate(instance)


def validate_artifact_definition(name: str, instance: Any) -> None:
    Draft202012Validator(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$ref": (
                "https://home-sweet-home.cloud/contracts/hades/graph-v2/"
                f"artifact.schema.json#/$defs/{name}"
            ),
        },
        registry=_contract_registry(),
        format_checker=FORMAT_CHECKER,
    ).validate(instance)


def strict_load_contract_json(raw: bytes) -> Any:
    return load_json_bytes(raw)


@pytest.fixture
def valid_artifact() -> dict[str, Any]:
    binding_id = "01KXJD1BDMQ2TFABMVJV6EFE8Q"
    node_id = "hades:node:v2:" + "c" * 64
    path = "src/Example.php"
    full_capabilities = {
        name: {"status": "full", "reasons": []}
        for name in (
            "inventory",
            "entrypoint_discovery",
            "symbol_resolution",
            "call_graph",
            "control_flow",
            "framework_lifecycle",
            "exceptions",
            "async",
            "data_access",
        )
    }
    return {
        "schema": "hades.code_graph.v2",
        "generated_at": "2026-07-16T12:00:00Z",
        "project": {
            "project_id": "01KXJD0SV73EBGWKNE2EK3M4KD",
            "workspace_binding_id": binding_id,
        },
        "source": {
            "head_commit": None,
            "tree_sha256": "a" * 64,
            "dirty": False,
            "branch": None,
        },
        "graph_contract": {
            "version": "hades.graph_artifact.v2",
            "artifact_graph_version": "b" * 64,
            "projection_state": "queued",
            "completeness": {
                "status": "full",
                "capabilities": full_capabilities,
                "languages": [],
            },
            "coverage": {
                "scope": {
                    "included_roots": ["."],
                    "excluded_config_sha256": "d" * 64,
                    "excluded_path_count": 0,
                },
                "files": {
                    "discovered": 1,
                    "hashed": 1,
                    "parser_candidates": 1,
                    "analyzed": 0,
                    "unsupported": 1,
                    "failed": 0,
                    "too_large": 0,
                    "budget_omitted": 0,
                },
                "entrypoints": {
                    "detected": 0,
                    "analyzed": 0,
                    "partial": 0,
                    "by_kind": {},
                },
                "records": {
                    "nodes": 1,
                    "structures": 0,
                    "edges": 0,
                    "flows": 0,
                    "flow_steps": 0,
                    "uncertainties": 0,
                    "omitted_by_bundle_budget": 0,
                },
            },
        },
        "frameworks": [],
        "languages": [],
        "entrypoints": [],
        "nodes": [
            {
                "id": node_id,
                "identity": {
                    "variant": "file",
                    "workspace_binding_id": binding_id,
                    "language": "php",
                    "kind": "file",
                    "path": path,
                },
                "kind": "file",
                "language": "php",
                "framework": None,
                "name": "Example.php",
                "qualified_name": path,
                "namespace": None,
                "uncertainty_id": None,
                "location": None,
                "properties": {
                    "file_sha256": "e" * 64,
                    "byte_size": 0,
                    "analysis_status": "unsupported",
                    "omission_reason": None,
                    "is_test": False,
                    "is_generated": False,
                },
                "evidence": {
                    "primary": {
                        "origin": "verified_from_code",
                        "extractor": "inventory.v2",
                        "source_locator": {"kind": "file", "path": path},
                        "source_fingerprint": "f" * 64,
                        "inference_rule": None,
                    },
                    "supporting": [],
                    "supporting_omitted_count": 0,
                },
            }
        ],
        "structures": [],
        "edges": [],
        "flows": [],
        "flow_steps": [],
        "uncertainties": [],
    }


def _replace_path(value: dict[str, Any], replacement: str) -> dict[str, Any]:
    mutated = copy.deepcopy(value)

    def visit(item: Any) -> bool:
        if isinstance(item, dict):
            for key, child in item.items():
                if key == "path" and isinstance(child, str):
                    item[key] = replacement
                    return True
                if visit(child):
                    return True
        elif isinstance(item, list):
            for child in item:
                if visit(child):
                    return True
        return False

    assert visit(mutated)
    return mutated


def _replace_first_integer(value: dict[str, Any], replacement: float) -> dict[str, Any]:
    mutated = copy.deepcopy(value)

    def visit(item: Any) -> bool:
        if isinstance(item, dict):
            for key, child in item.items():
                if isinstance(child, int) and not isinstance(child, bool):
                    item[key] = replacement
                    return True
                if visit(child):
                    return True
        elif isinstance(item, list):
            for index, child in enumerate(item):
                if isinstance(child, int) and not isinstance(child, bool):
                    item[index] = replacement
                    return True
                if visit(child):
                    return True
        return False

    assert visit(mutated)
    return mutated


def test_graph_v2_contract_inventory_is_closed_and_manifested():
    manifest = json.loads((CONTRACT_ROOT / "manifest.json").read_text(encoding="utf-8"))
    assert set(manifest) == {"schema", "files"}
    assert manifest["schema"] == "hades.graph_v2_contract_manifest.v1"
    assert [row["path"] for row in manifest["files"]] == sorted(SCHEMAS)
    assert all(set(row) == {"path", "sha256"} for row in manifest["files"])
    for name in SCHEMAS:
        document = _contract_document(name)
        Draft202012Validator.check_schema(document)
        assert document["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert document["$id"].endswith(f"/contracts/hades/graph-v2/{name}")
        assert document["type"] == "object"
        assert document["additionalProperties"] is False
        assert _all_object_schemas_are_closed(document)
        digest = hashlib.sha256((CONTRACT_ROOT / name).read_bytes()).hexdigest()
        assert (
            next(row["sha256"] for row in manifest["files"] if row["path"] == name)
            == digest
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda artifact: {**artifact, "schema": "hades.code_graph.v1"},
        lambda artifact: {**artifact, "unknown": True},
        lambda artifact: _replace_path(artifact, "/absolute/private.php"),
        lambda artifact: _replace_first_integer(artifact, 1.5),
    ],
)
def test_artifact_schema_rejects_non_v2_open_or_unsafe_payloads(
    valid_artifact: dict[str, Any], mutation: Any
) -> None:
    validate_contract("artifact.schema.json", valid_artifact)
    with pytest.raises(ValidationError):
        validate_contract("artifact.schema.json", mutation(valid_artifact))


def test_raw_wire_integral_float_is_rejected_before_schema_validation() -> None:
    golden = json.loads(
        (CONTRACT_ROOT / "golden" / "canonicalization.json").read_text(encoding="utf-8")
    )
    assert golden["raw_wire_negative_vectors"] == [
        {
            "kind": "lexical_integral_float",
            "raw_utf8_hex": "7b22617474656d70745f67656e65726174696f6e223a312e307d",
            "error_code": "float_not_allowed",
        }
    ]
    vector = golden["raw_wire_negative_vectors"][0]
    raw = bytes.fromhex(vector["raw_utf8_hex"])
    assert raw == b'{"attempt_generation":1.0}'

    parsed_by_ordinary_json = json.loads(raw)
    Draft202012Validator({"type": "integer"}).validate(
        parsed_by_ordinary_json["attempt_generation"]
    )
    with pytest.raises(ValueError, match=vector["error_code"]):
        strict_load_contract_json(raw)


def test_common_scalar_definitions_reject_noncanonical_values() -> None:
    validate_artifact_definition("ulid", "01KXJD0SV73EBGWKNE2EK3M4KD")
    validate_artifact_definition("utcTimestamp", "2026-07-16T12:00:00Z")
    validate_artifact_definition("safePath", "src/Example.php")
    validate_artifact_definition("structuralPath", "body/3/consequence/1/call/0")

    for definition, invalid in (
        ("ulid", "81KXJD0SV73EBGWKNE2EK3M4KD"),
        ("utcTimestamp", "2026-13-16T12:00:00Z"),
        ("utcTimestamp", "2026-07-16T12:00:00+00:00"),
        ("safePath", "src//Example.php"),
        ("safePath", "src/Example.php/"),
        ("structuralPath", "body\\3\\call\\0"),
        ("structuralPath", "body/3/\u0001/call/0"),
        ("structuralPath", "/body/3/call/0"),
        ("structuralPath", "body//call/0"),
        ("structuralPath", "body/./call/0"),
        ("structuralPath", "body/../call/0"),
        ("structuralPath", "body/call/0/"),
    ):
        with pytest.raises(ValidationError):
            validate_artifact_definition(definition, invalid)


def test_identity_namespaces_are_limited_to_512_characters() -> None:
    identity = {
        "variant": "source_declaration",
        "workspace_binding_id": "01KXJD1BDMQ2TFABMVJV6EFE8Q",
        "language": "php",
        "kind": "method",
        "namespace": "N" * 512,
        "qualified_name": "Example::run",
        "path": "src/Example.php",
    }
    validate_artifact_definition("sourceDeclarationIdentity", identity)

    identity["namespace"] = "N" * 513
    with pytest.raises(ValidationError):
        validate_artifact_definition("sourceDeclarationIdentity", identity)


def test_file_evidence_is_confined_to_file_nodes(
    valid_artifact: dict[str, Any],
) -> None:
    examples = _contract_document("golden/canonicalization.json")["contract_examples"]
    file_node = valid_artifact["nodes"][0]
    validate_artifact_definition("node", file_node)

    file_with_ast_evidence = copy.deepcopy(file_node)
    file_with_ast_evidence["evidence"]["primary"]["source_locator"] = {
        "kind": "ast",
        "path": "src/Example.php",
        "structural_path": "declaration/0",
    }
    with pytest.raises(ValidationError):
        validate_artifact_definition("node", file_with_ast_evidence)

    declaration = {
        "id": "hades:node:v2:" + "d" * 64,
        "identity": {
            "variant": "source_declaration",
            "workspace_binding_id": "01KXJD1BDMQ2TFABMVJV6EFE8Q",
            "language": "php",
            "kind": "method",
            "namespace": "App",
            "qualified_name": "App\\Example::run",
            "path": "src/Example.php",
        },
        "kind": "method",
        "language": "php",
        "framework": None,
        "name": "run",
        "qualified_name": "App\\Example::run",
        "namespace": "App",
        "uncertainty_id": None,
        "location": {
            "path": "src/Example.php",
            "start_line": 1,
            "end_line": 2,
        },
        "properties": {},
        "evidence": copy.deepcopy(examples["entrypoints"][0]["evidence"]),
    }
    validate_artifact_definition("node", declaration)

    producer_records = [
        declaration,
        examples["entrypoints"][0],
        examples["structures"][0],
        examples["edges"][0],
    ]
    definition_names = ["node", "entrypoint", "structure", "edge"]
    for definition_name, record in zip(definition_names, producer_records, strict=True):
        mutated = copy.deepcopy(record)
        mutated["evidence"]["primary"]["source_locator"] = {
            "kind": "file",
            "path": "src/Example.php",
        }
        with pytest.raises(ValidationError):
            validate_artifact_definition(definition_name, mutated)


def test_structural_edges_reject_every_executable_field() -> None:
    edge = copy.deepcopy(
        _contract_document("golden/canonicalization.json")["contract_examples"][
            "edges"
        ][0]
    )
    edge.update({
        "relation": "declares",
        "flow": None,
        "condition": None,
        "branch_group_id": None,
        "call_site_id": None,
        "exception_scope_id": None,
        "order": None,
    })
    validate_artifact_definition("edge", edge)

    executable_values = {
        "flow": "always",
        "condition": {
            "kind": "predicate",
            "normalized": "ready",
            "hash": "1" * 64,
            "polarity": "true",
        },
        "branch_group_id": "hades:branch:v2:" + "2" * 64,
        "call_site_id": "hades:call-site:v2:" + "3" * 64,
        "exception_scope_id": "hades:exception-scope:v2:" + "4" * 64,
        "order": 0,
    }
    for field, value in executable_values.items():
        mutated = copy.deepcopy(edge)
        mutated[field] = value
        with pytest.raises(ValidationError):
            validate_artifact_definition("edge", mutated)


def test_executable_edge_flow_condition_and_structure_matrix() -> None:
    edge = copy.deepcopy(
        _contract_document("golden/canonicalization.json")["contract_examples"][
            "edges"
        ][0]
    )
    predicate = {
        "kind": "predicate",
        "normalized": "ready",
        "hash": "1" * 64,
        "polarity": "true",
    }

    conditional = copy.deepcopy(edge)
    conditional.update({"flow": "conditional", "condition": predicate})
    validate_artifact_definition("edge", conditional)
    conditional["condition"] = None
    with pytest.raises(ValidationError):
        validate_artifact_definition("edge", conditional)

    for flow in ("async", "loop"):
        invalid = copy.deepcopy(edge)
        invalid.update({"flow": flow, "condition": predicate})
        with pytest.raises(ValidationError):
            validate_artifact_definition("edge", invalid)

    invocation = copy.deepcopy(edge)
    invocation.update({
        "relation": "invokes",
        "call_site_id": "hades:call-site:v2:" + "3" * 64,
    })
    validate_artifact_definition("edge", invocation)
    invocation["call_site_id"] = None
    with pytest.raises(ValidationError):
        validate_artifact_definition("edge", invocation)

    for relation, flow in (
        ("routes_to", "async"),
        ("reads", "exception"),
        ("enters", "alternative"),
    ):
        invalid = copy.deepcopy(edge)
        invalid.update({"relation": relation, "flow": flow})
        with pytest.raises(ValidationError):
            validate_artifact_definition("edge", invalid)

    branch = copy.deepcopy(edge)
    branch.update({
        "relation": "branches_to",
        "flow": "conditional",
        "condition": predicate,
    })
    with pytest.raises(ValidationError):
        validate_artifact_definition("edge", branch)


def test_alternative_edges_require_branch_group_and_preserve_dynamic_dispatch() -> None:
    edge = copy.deepcopy(
        _contract_document("golden/canonicalization.json")["contract_examples"][
            "edges"
        ][0]
    )
    branch_group_id = "hades:branch:v2:" + "2" * 64
    case_condition = {
        "kind": "predicate",
        "normalized": "case admin",
        "hash": "3" * 64,
        "polarity": "case",
    }

    ordinary = copy.deepcopy(edge)
    ordinary.update({
        "flow": "alternative",
        "condition": case_condition,
        "branch_group_id": branch_group_id,
    })
    validate_artifact_definition("edge", ordinary)

    dynamic_dispatch = copy.deepcopy(edge)
    dynamic_dispatch.update({
        "flow": "alternative",
        "condition": None,
        "branch_group_id": branch_group_id,
    })
    validate_artifact_definition("edge", dynamic_dispatch)

    copied_outer_candidate = copy.deepcopy(dynamic_dispatch)
    copied_outer_candidate["condition"] = {
        "kind": "predicate",
        "normalized": "authorized",
        "hash": "4" * 64,
        "polarity": "true",
    }
    validate_artifact_definition("edge", copied_outer_candidate)

    missing_group = copy.deepcopy(dynamic_dispatch)
    missing_group["branch_group_id"] = None
    with pytest.raises(ValidationError):
        validate_artifact_definition("edge", missing_group)


def test_structure_discriminator_closes_id_subtype_continuation_and_parent() -> None:
    call_site = copy.deepcopy(
        _contract_document("golden/canonicalization.json")["contract_examples"][
            "structures"
        ][0]
    )
    validate_artifact_definition("structure", call_site)

    invalid_call_sites = []
    for field, value in (
        ("id", "hades:branch:v2:" + "1" * 64),
        ("subtype", "if"),
        ("continuation_node_id", None),
        ("parent_structure_id", "hades:call-site:v2:" + "2" * 64),
        ("parent_structure_id", "hades:branch:v2:" + "2" * 64),
    ):
        mutated = copy.deepcopy(call_site)
        mutated[field] = value
        invalid_call_sites.append(mutated)

    branch = copy.deepcopy(call_site)
    branch.update({
        "id": "hades:branch:v2:" + "2" * 64,
        "kind": "branch_group",
        "subtype": "if",
        "continuation_node_id": None,
        "parent_structure_id": "hades:exception-scope:v2:" + "3" * 64,
    })
    validate_artifact_definition("structure", branch)

    exception_scope = copy.deepcopy(call_site)
    exception_scope.update({
        "id": "hades:exception-scope:v2:" + "3" * 64,
        "kind": "exception_scope",
        "subtype": "try_catch",
        "continuation_node_id": None,
        "parent_structure_id": "hades:branch:v2:" + "2" * 64,
    })
    validate_artifact_definition("structure", exception_scope)

    invalid_structures = invalid_call_sites
    for record, field, value in (
        (branch, "id", "hades:exception-scope:v2:" + "3" * 64),
        (branch, "subtype", "call"),
        (branch, "parent_structure_id", "hades:call-site:v2:" + "1" * 64),
        (exception_scope, "id", "hades:branch:v2:" + "2" * 64),
        (exception_scope, "subtype", "if"),
        (
            exception_scope,
            "parent_structure_id",
            "hades:call-site:v2:" + "1" * 64,
        ),
    ):
        mutated = copy.deepcopy(record)
        mutated[field] = value
        invalid_structures.append(mutated)

    for invalid in invalid_structures:
        with pytest.raises(ValidationError):
            validate_artifact_definition("structure", invalid)


def test_candidate_set_knowledge_is_the_same_closed_union_in_artifact_and_work() -> (
    None
):
    golden = _contract_document("golden/canonicalization.json")
    uncertainty = copy.deepcopy(golden["contract_examples"]["uncertainties"][0])
    work = copy.deepcopy(
        _contract_document("golden/verification-results.json")["work_items"][0]
    )
    node_id = "hades:node:v2:" + "a" * 64
    edge_id = "hades:edge:v2:" + "b" * 64

    valid_combinations = (
        ("complete", [node_id], [edge_id]),
        ("incomplete", [node_id], []),
        ("incomplete", [node_id], [edge_id]),
        ("not_applicable", [], []),
    )
    invalid_combinations = (
        ("complete", [], []),
        ("complete", [node_id], []),
        ("complete", [], [edge_id]),
        ("incomplete", [], []),
        ("incomplete", [], [edge_id]),
        ("not_applicable", [node_id], []),
        ("not_applicable", [], [edge_id]),
    )

    def apply_combination(
        record: dict[str, Any],
        knowledge: str,
        targets: list[str],
        edges: list[str],
    ) -> None:
        record["candidate_set_knowledge"] = knowledge
        record["candidate_target_node_ids"] = targets
        record["candidate_edge_ids"] = edges

    for knowledge, targets, edges in valid_combinations:
        artifact_case = copy.deepcopy(uncertainty)
        work_case = copy.deepcopy(work)
        apply_combination(artifact_case, knowledge, targets, edges)
        apply_combination(work_case["assertion"], knowledge, targets, edges)
        validate_artifact_definition("uncertainty", artifact_case)
        validate_contract("verification-work.schema.json", work_case)

    for knowledge, targets, edges in invalid_combinations:
        artifact_case = copy.deepcopy(uncertainty)
        work_case = copy.deepcopy(work)
        apply_combination(artifact_case, knowledge, targets, edges)
        apply_combination(work_case["assertion"], knowledge, targets, edges)
        with pytest.raises(ValidationError):
            validate_artifact_definition("uncertainty", artifact_case)
        with pytest.raises(ValidationError):
            validate_contract("verification-work.schema.json", work_case)


def test_resolution_kind_binds_subject_and_complete_cardinality_in_both_roots() -> None:
    artifact_template = copy.deepcopy(
        _contract_document("golden/canonicalization.json")["contract_examples"][
            "uncertainties"
        ][0]
    )
    work_template = copy.deepcopy(
        _contract_document("golden/verification-results.json")["work_items"][0]
    )
    call_site_subject = {
        "call_site_id": "hades:call-site:v2:" + "1" * 64,
    }
    edge_subject = {"edge_id": "hades:edge:v2:" + "2" * 64}
    resolution_kinds = (
        "call_target",
        "entrypoint_handler",
        "async_target",
        "exception_target",
        "framework_target",
        "external_target",
    )

    def records_for(
        resolution_kind: str,
        subject: dict[str, str],
        knowledge: str = "not_applicable",
        candidate_count: int = 0,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        artifact = copy.deepcopy(artifact_template)
        work = copy.deepcopy(work_template)
        targets = [
            f"hades:node:v2:{candidate_index:064x}"
            for candidate_index in range(1, candidate_count + 1)
        ]
        edges = [
            f"hades:edge:v2:{candidate_index:064x}"
            for candidate_index in range(33, 33 + candidate_count)
        ]
        for record in (artifact, work["assertion"]):
            record["resolution_kind"] = resolution_kind
            record["subject"] = subject
            record["candidate_set_knowledge"] = knowledge
            record["candidate_target_node_ids"] = targets
            record["candidate_edge_ids"] = edges
        return artifact, work

    for resolution_kind in resolution_kinds:
        legal_subject = (
            call_site_subject if resolution_kind == "call_target" else edge_subject
        )
        illegal_subject = (
            edge_subject if resolution_kind == "call_target" else call_site_subject
        )
        valid_artifact, valid_work = records_for(resolution_kind, legal_subject)
        validate_artifact_definition("uncertainty", valid_artifact)
        validate_contract("verification-work.schema.json", valid_work)

        invalid_artifact, invalid_work = records_for(resolution_kind, illegal_subject)
        with pytest.raises(ValidationError):
            validate_artifact_definition("uncertainty", invalid_artifact)
        with pytest.raises(ValidationError):
            validate_contract("verification-work.schema.json", invalid_work)

    handler_artifact, handler_work = records_for(
        "entrypoint_handler", edge_subject, "complete", 1
    )
    validate_artifact_definition("uncertainty", handler_artifact)
    validate_contract("verification-work.schema.json", handler_work)

    handler_artifact, handler_work = records_for(
        "entrypoint_handler", edge_subject, "complete", 2
    )
    with pytest.raises(ValidationError):
        validate_artifact_definition("uncertainty", handler_artifact)
    with pytest.raises(ValidationError):
        validate_contract("verification-work.schema.json", handler_work)

    for candidate_count in (1, 20):
        for resolution_kind in (
            "call_target",
            "async_target",
            "exception_target",
            "framework_target",
            "external_target",
        ):
            subject = (
                call_site_subject if resolution_kind == "call_target" else edge_subject
            )
            artifact, work = records_for(
                resolution_kind, subject, "complete", candidate_count
            )
            validate_artifact_definition("uncertainty", artifact)
            validate_contract("verification-work.schema.json", work)


def test_bundle_and_each_chunk_discriminator_validate(
    valid_artifact: dict[str, Any],
) -> None:
    golden = json.loads(
        (CONTRACT_ROOT / "golden" / "canonicalization.json").read_text(encoding="utf-8")
    )
    validate_contract("bundle.schema.json", golden["contract_examples"]["bundle"])
    records_by_kind = {
        "entrypoints": valid_artifact["entrypoints"],
        "nodes": valid_artifact["nodes"],
        "structures": valid_artifact["structures"],
        "edges": valid_artifact["edges"],
        "flows": valid_artifact["flows"],
        "flow_steps": valid_artifact["flow_steps"],
        "uncertainties": valid_artifact["uncertainties"],
    }
    for index, (kind, records) in enumerate(records_by_kind.items()):
        example_records = records or golden["contract_examples"][kind]
        validate_contract(
            "chunk.schema.json",
            {
                "schema": "hades.graph_chunk.v2",
                "index": index,
                "kind": kind,
                "records": example_records,
            },
        )


def test_dashboard_protocol_golden_payloads_validate() -> None:
    golden = json.loads(
        (CONTRACT_ROOT / "golden" / "dashboard-protocol.json").read_text(
            encoding="utf-8"
        )
    )
    assert golden["schema"] == "hades.dashboard_protocol_golden.v1"
    assert {case["request"]["query"]["type"] for case in golden["cases"]} == {
        "scopes",
        "overview",
        "entrypoints",
        "lifecycle",
        "lifecycle_expand",
        "search",
        "detail",
        "neighborhood",
        "impact",
        "path",
    }
    for case in golden["cases"]:
        validate_contract("dashboard-query.schema.json", case["request"])
        validate_contract("dashboard-response.schema.json", case["response"])
    for response in golden["errors"]:
        validate_contract("dashboard-response.schema.json", response)


def test_verification_golden_payloads_validate() -> None:
    golden = json.loads(
        (CONTRACT_ROOT / "golden" / "verification-results.json").read_text(
            encoding="utf-8"
        )
    )
    assert golden["schema"] == "hades.verification_golden.v1"
    assert {item["kind"] for item in golden["work_items"]} == {
        "hades.verification.graph.v1",
        "hades.verification.wiki.v1",
    }
    assert {result["verdict"] for result in golden["results"]} == {
        "verified",
        "contradicted",
        "deferred",
    }
    for item in golden["work_items"]:
        validate_contract("verification-work.schema.json", item)
    for result in golden["results"]:
        validate_contract("verification-result.schema.json", result)
    assert golden["result_digests"] == [
        hashlib.sha256(canonical_json_bytes(result)).hexdigest()
        for result in golden["results"]
    ]
    assert len(golden["overlays"]) == len(golden["overlay_result_indexes"])
    for overlay, result_index in zip(
        golden["overlays"], golden["overlay_result_indexes"], strict=True
    ):
        validate_contract("graph-overlay.schema.json", overlay)
        result = golden["results"][result_index]
        assert overlay["result_digest"] == golden["result_digests"][result_index]
        assert (
            overlay["evidence_digest"]
            == hashlib.sha256(canonical_json_bytes(result["evidence"])).hexdigest()
        )

    for item in golden["work_items"]:
        if item["domain"] == "graph":
            preimage = {
                "kind": item["kind"],
                "project_id": item["project_id"],
                "workspace_binding_id": item["workspace_binding_id"],
                "target_id": item["target"]["id"],
                "target_version": item["target"]["version"],
                "assertion_fingerprint": item["assertion"]["fingerprint"],
                "attempt_generation": item["attempt_generation"],
            }
        else:
            preimage = {
                "kind": item["kind"],
                "project_id": item["project_id"],
                "workspace_binding_id": item["workspace_binding_id"],
                "target_id": item["target"]["id"],
                "target_version": item["target"]["version"],
                "source_state": item["source_snapshot"]["state"],
                "artifact_graph_version": item["source_snapshot"][
                    "artifact_graph_version"
                ],
                "source_tree_sha256": item["source_snapshot"]["tree_sha256"],
                "attempt_generation": item["attempt_generation"],
            }
        assert (
            item["deduplication_key"]
            == hashlib.sha256(canonical_json_bytes(preimage)).hexdigest()
        )


def test_verification_goldens_are_closed_linked_scenarios() -> None:
    golden = _contract_document("golden/verification-results.json")
    work_by_target: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in golden["work_items"]:
        key = (item["target"]["type"], item["target"]["id"], item["target"]["version"])
        assert key not in work_by_target
        work_by_target[key] = item
        if item["domain"] != "graph":
            continue
        assertion = item["assertion"]
        if assertion["resolution_kind"] == "call_target":
            assert set(assertion["subject"]) == {"call_site_id"}
        else:
            assert set(assertion["subject"]) == {"edge_id"}
        if assertion["candidate_set_knowledge"] == "complete":
            candidate_count = len(assertion["candidate_target_node_ids"])
            assert candidate_count == len(assertion["candidate_edge_ids"])
            if assertion["resolution_kind"] == "entrypoint_handler":
                assert candidate_count == 1
            else:
                assert 1 <= candidate_count <= 20

    assert {
        result["verdict"] for result in golden["results"] if result["domain"] == "graph"
    } == {"verified", "contradicted", "deferred"}
    assert {
        result["verdict"] for result in golden["results"] if result["domain"] == "wiki"
    } == {"verified", "contradicted", "deferred"}
    assert {
        result["graph"]["operation"]
        for result in golden["results"]
        if result["graph"] is not None
    } == {
        "resolve_candidate_set",
        "resolve_call_targets",
        "resolve_edge_targets",
        "reject_unresolved_subject",
    }

    non_deferred_graph_indexes: list[int] = []
    for index, result in enumerate(golden["results"]):
        target = result["target"]
        key = (target["type"], target["id"], target["version"])
        assert key in work_by_target
        work = work_by_target[key]
        assert work["domain"] == result["domain"]

        if result["domain"] == "graph":
            assert work["assertion"]["fingerprint"] == target["id"].removeprefix(
                "hades:uncertainty:v2:"
            )
            assert (
                target["version"] == work["source_snapshot"]["artifact_graph_version"]
            )
            if result["verdict"] == "deferred":
                assert result["graph"] is None
                continue

            non_deferred_graph_indexes.append(index)
            graph = result["graph"]
            assertion = work["assertion"]
            assert graph["uncertainty_id"] == target["id"]
            knowledge = assertion["candidate_set_knowledge"]
            resolution_kind = assertion["resolution_kind"]
            if graph["operation"] == "resolve_candidate_set":
                assert knowledge == "complete"
            elif graph["operation"] == "resolve_call_targets":
                assert resolution_kind == "call_target"
                assert knowledge in {"incomplete", "not_applicable"}
                assert graph["call_site_id"] == assertion["subject"]["call_site_id"]
            elif graph["operation"] == "resolve_edge_targets":
                assert resolution_kind != "call_target"
                assert knowledge in {"incomplete", "not_applicable"}
                assert graph["subject_edge_id"] == assertion["subject"]["edge_id"]
            else:
                assert graph["operation"] == "reject_unresolved_subject"
                assert result["verdict"] == "contradicted"
                assert knowledge in {"incomplete", "not_applicable"}

            for evidence in result["evidence"]:
                if evidence["kind"] == "source_ref":
                    assert (
                        evidence["source_tree_sha256"]
                        == work["source_snapshot"]["tree_sha256"]
                    )
        else:
            if result["verdict"] in {"verified", "contradicted"}:
                assert work["source_snapshot"]["state"] == "available"
                assert result["wiki"]["page_id"] == target["id"]
                assert result["wiki"]["expected_revision_id"] == target["version"]
            else:
                assert result["deferred"] is not None
                if result["deferred"]["blocker_code"] == "source_unavailable":
                    assert work["source_snapshot"]["state"] == "unavailable"

    assert golden["overlay_result_indexes"] == non_deferred_graph_indexes
    assert len(golden["overlays"]) == len(non_deferred_graph_indexes)
    for overlay, result_index in zip(
        golden["overlays"], golden["overlay_result_indexes"], strict=True
    ):
        result = golden["results"][result_index]
        work = work_by_target[
            (
                result["target"]["type"],
                result["target"]["id"],
                result["target"]["version"],
            )
        ]
        assert overlay["artifact_graph_version"] == result["target"]["version"]
        assert overlay["uncertainty_id"] == result["target"]["id"]
        assert overlay["assertion_fingerprint"] == work["assertion"]["fingerprint"]
        assert overlay["operation"] == result["graph"]["operation"]


def test_dashboard_token_vectors_have_complete_signed_preimages() -> None:
    golden = _contract_document("golden/dashboard-protocol.json")
    assert {vector["kind"] for vector in golden["token_vectors"]} == {
        "handle",
        "cursor",
    }
    key = bytes.fromhex(golden["token_hmac_key_utf8_hex"])
    expected_payload_keys = {
        "handle": {
            "v",
            "type",
            "public_id",
            "project_id",
            "source_scope_type",
            "source_scope_id",
            "projection_version",
            "expires_at",
        },
        "cursor": {
            "v",
            "query_type",
            "project_id",
            "source_scope_type",
            "source_scope_id",
            "projection_version",
            "filters_sha256",
            "last_sort",
            "search_snapshot_id",
            "expires_at",
        },
    }
    for vector in golden["token_vectors"]:
        payload_bytes = canonical_json_bytes(vector["payload"])
        assert set(vector["payload"]) == expected_payload_keys[vector["kind"]]
        assert vector["payload"]["v"] == 2
        assert payload_bytes.hex() == vector["canonical_utf8_hex"]
        signature = hmac.new(key, payload_bytes, hashlib.sha256).digest()
        assert signature.hex() == vector["signature_hex"]
        encoded_payload = base64.urlsafe_b64encode(payload_bytes).rstrip(b"=")
        encoded_signature = base64.urlsafe_b64encode(signature).rstrip(b"=")
        assert vector["token"] == (encoded_payload + b"." + encoded_signature).decode(
            "ascii"
        )


def test_canonicalization_vectors_have_exact_bytes_and_digests() -> None:
    vectors = json.loads(
        (CONTRACT_ROOT / "golden" / "canonicalization.json").read_text(encoding="utf-8")
    )
    assert vectors["schema"] == "hades.graph_v2_canonicalization_golden.v1"
    expected_kinds = {
        "unicode_nfc",
        "safe_integer",
        "node_id",
        "edge_id",
        "condition_hash",
        "ast_source_fingerprint",
        "config_source_fingerprint",
        "file_source_fingerprint",
        "flow_id",
        "flow_step_id",
        "call_site_id",
        "branch_group_id",
        "exception_scope_id",
        "uncertainty_id",
        "evidence_digest",
        "empty_verification_set_hash",
        "verification_set_hash",
        "artifact_digest",
        "verification_dedupe",
        "result_digest",
    }
    actual_kinds = [vector["kind"] for vector in vectors["vectors"]]
    assert len(actual_kinds) == len(set(actual_kinds))
    assert set(actual_kinds) == expected_kinds
    for vector in vectors["vectors"]:
        canonical = canonical_json_bytes(vector["input"])
        assert canonical.hex() == vector["canonical_utf8_hex"]
        assert hashlib.sha256(canonical).hexdigest() == vector["sha256"]

    structure_prefixes = {
        "call_site_id": "hades:call-site:v2:",
        "branch_group_id": "hades:branch:v2:",
        "exception_scope_id": "hades:exception-scope:v2:",
    }
    for kind, prefix in structure_prefixes.items():
        vector = next(item for item in vectors["vectors"] if item["kind"] == kind)
        assert vector["public_id"] == prefix + vector["sha256"]

    verification_vector = next(
        item for item in vectors["vectors"] if item["kind"] == "verification_set_hash"
    )
    assert len(verification_vector["input"]) == 1
    assert any(
        projection["verification_set_hash"] == verification_vector["sha256"]
        for projection in vectors["projection_versions"]
    )
    for active_overlay in verification_vector["input"]:
        assert set(active_overlay) == {
            "artifact_graph_version",
            "assertion_fingerprint",
            "verdict",
            "overlay",
            "evidence",
        }
        assert active_overlay["overlay"]
    for vector in vectors["path_vectors"]:
        assert normalize_source_path(vector["input"]) == vector["normalized"]
        assert (
            vector["normalized"].encode("utf-8").hex() == vector["normalized_utf8_hex"]
        )
    for vector in vectors["projection_versions"]:
        preimage = bytes.fromhex(vector["preimage_utf8_hex"])
        assert preimage == (
            vector["artifact_graph_version"] + ":" + vector["verification_set_hash"]
        ).encode("ascii")
        assert hashlib.sha256(preimage).hexdigest() == vector["projection_version"]

    result_vector = next(
        vector for vector in vectors["vectors"] if vector["kind"] == "result_digest"
    )
    verification = json.loads(
        (CONTRACT_ROOT / "golden" / "verification-results.json").read_text(
            encoding="utf-8"
        )
    )
    assert result_vector["input"] == verification["results"][0]
    assert result_vector["sha256"] == verification["result_digests"][0]
