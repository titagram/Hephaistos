from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

import hermes_cli.hades_graph_contract as facade
from hermes_cli.hades_graph_contract import (
    GRAPH_CONTRACT_VERSION,
    SCHEMA_NAMES,
    GraphContractError,
    GraphIdentityCollision,
    artifact_digest,
    artifact_graph_version,
    ast_source_fingerprint,
    branch_group_id,
    call_site_id,
    canonical_json_bytes,
    canonicalize_records,
    condition_hash,
    config_source_fingerprint,
    edge_id,
    evidence_digest,
    exception_scope_id,
    file_source_fingerprint,
    flow_id,
    flow_step_id,
    load_json_bytes,
    node_id,
    normalize_contract_value,
    normalize_source_path,
    projection_version,
    result_digest,
    sha256_jcs,
    uncertainty_id,
    validate_json_bytes,
    validate_schema,
    verification_deduplication_key,
    verification_set_hash,
)


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = ROOT / "contracts" / "hades" / "graph-v2"
PACKAGED_CONTRACT_ROOT = ROOT / "hermes_cli" / "hades_graph_v2" / "contracts"
CANONICALIZATION_GOLDEN = CONTRACT_ROOT / "golden" / "canonicalization.json"
VERIFICATION_GOLDEN = CONTRACT_ROOT / "golden" / "verification-results.json"
CONTRACT_LOCK = CONTRACT_ROOT / "contract-lock.json"
SCHEMA_SOURCE_COMMIT = "cbc07d447ad301a53468ad52093438cfe0160d1d"


def _golden() -> dict[str, Any]:
    return json.loads(CANONICALIZATION_GOLDEN.read_text(encoding="utf-8"))


def _vector(kind: str) -> dict[str, Any]:
    return next(item for item in _golden()["vectors"] if item["kind"] == kind)


def _verification_golden() -> dict[str, Any]:
    return json.loads(VERIFICATION_GOLDEN.read_text(encoding="utf-8"))


def _direct_verification_preimage(work: dict[str, Any]) -> dict[str, Any]:
    direct = {
        "kind": work["kind"],
        "project_id": work["project_id"],
        "workspace_binding_id": work["workspace_binding_id"],
        "target_id": work["target"]["id"],
        "target_version": work["target"]["version"],
        "attempt_generation": work["attempt_generation"],
    }
    if work["kind"] == "hades.verification.graph.v1":
        direct["assertion_fingerprint"] = work["assertion"]["fingerprint"]
    else:
        direct.update({
            "source_state": work["source_snapshot"]["state"],
            "artifact_graph_version": work["source_snapshot"]["artifact_graph_version"],
            "source_tree_sha256": work["source_snapshot"]["tree_sha256"],
        })
    return direct


def _framework(configuration_paths: list[str]) -> dict[str, Any]:
    return {
        "language": "php",
        "name": "laravel",
        "version": None,
        "detector": "composer.lock",
        "configuration_paths": configuration_paths,
        "knowledge": "verified",
    }


def _bundle_with_path_array(array_key: str, paths: list[str]) -> dict[str, Any]:
    bundle = copy.deepcopy(_golden()["contract_examples"]["bundle"])
    if array_key == "configuration_paths":
        bundle["frameworks"] = [_framework(paths)]
        bundle["counts"]["frameworks"] = 1
    else:
        bundle["graph_contract"]["completeness"]["status"] = "partial"
        bundle["graph_contract"]["completeness"]["capabilities"]["inventory"] = {
            "status": "partial",
            "reasons": [
                {
                    "code": "unsupported_language",
                    "count": 1,
                    "language": "php",
                    "paths_sample": paths,
                }
            ],
        }
    return bundle


def test_facade_is_v2_only() -> None:
    assert GRAPH_CONTRACT_VERSION == "hades.graph_artifact.v2"
    assert not hasattr(facade, "finalize_graph_artifact")
    assert not hasattr(facade, "DEFAULT_MAX_GRAPH_NODES")
    assert not hasattr(facade, "_NODE_ID_PREFIX")


def test_named_node_id_survives_unrelated_line_insertion() -> None:
    named_node = {
        "identity": _vector("node_id")["input"],
        "location": {
            "path": "src/Controller/WorkerController.php",
            "start_line": 206,
            "end_line": 240,
        },
    }
    moved = copy.deepcopy(named_node)
    moved["location"].update(start_line=900, end_line=905)

    assert node_id(named_node["identity"]) == node_id(moved["identity"])


def test_same_id_different_value_collision_is_fatal() -> None:
    public_id = "hades:node:v2:" + "a" * 64
    with pytest.raises(
        GraphIdentityCollision,
        match="same public ID has different canonical values",
    ):
        canonicalize_records([
            {"id": public_id, "label": "A"},
            {"id": public_id, "label": "B"},
        ])


def test_projection_version_hashes_exact_ascii_preimage() -> None:
    assert (
        projection_version("a" * 64, "b" * 64)
        == hashlib.sha256(("a" * 64 + ":" + "b" * 64).encode("ascii")).hexdigest()
    )


def test_canonical_json_is_rfc8785_safe_subset() -> None:
    value = {
        "\ue000": "BMP",
        "\U0001f600": "astral",
        "controls": '\x00\b\t\n\f\r"\\/\u2028',
        "decomposed": "Cafe\u0301",
        "flag": True,
        "negative": -1,
    }
    assert (
        canonical_json_bytes(value)
        == (
            '{"controls":"\\u0000\\b\\t\\n\\f\\r\\"\\\\/\u2028",'
            '"decomposed":"Caf\u00e9","flag":true,"negative":-1,'
            '"\U0001f600":"astral","\ue000":"BMP"}'
        ).encode()
    )


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ({"value": 1.0}, "float_not_allowed"),
        ({"value": 9_007_199_254_740_992}, "unsafe_integer"),
        ({"value": -9_007_199_254_740_992}, "unsafe_integer"),
        ({1: "not a string key"}, "non_string_object_key"),
        ({"value": "\ud800"}, "isolated_surrogate"),
        ({"Cafe\u0301": 1, "Caf\u00e9": 2}, "normalized_key_collision"),
        (("not", "a", "JSON array"), "unsupported_json_type"),
    ],
)
def test_canonical_json_rejects_values_outside_safe_subset(
    value: Any,
    code: str,
) -> None:
    with pytest.raises(GraphContractError) as exc_info:
        canonical_json_bytes(value)
    assert exc_info.value.code == code


def test_canonical_json_is_permutation_stable_and_boolean_is_not_integer() -> None:
    first = {"z": [True, 1, None], "a": {"b": 2, "a": 1}}
    second = {"a": {"a": 1, "b": 2}, "z": [True, 1, None]}
    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    assert canonical_json_bytes({"value": True}) == b'{"value":true}'


def test_path_normalization_is_nfc_posix_and_bounded() -> None:
    path_vector = _golden()["path_vectors"][0]
    assert normalize_source_path(path_vector["input"]) == path_vector["normalized"]
    for invalid in (
        "/absolute/private.php",
        "C:/private.php",
        "src/../private.php",
        "src//private.php",
        "src/\u202eprivate.php",
    ):
        with pytest.raises(GraphContractError) as exc_info:
            normalize_source_path(invalid)
        assert exc_info.value.code == "unsafe_source_path"


def test_hashing_rejects_non_utc_timestamp_fields() -> None:
    with pytest.raises(GraphContractError) as exc_info:
        sha256_jcs({"generated_at": "2026-07-16T12:00:00+00:00"})
    assert exc_info.value.code == "non_utc_timestamp"
    assert sha256_jcs({"generated_at": "2026-07-16T12:00:00Z"})


def test_raw_wire_integral_float_vector_is_rejected_before_schema() -> None:
    vector = _golden()["raw_wire_negative_vectors"][0]
    raw = bytes.fromhex(vector["raw_utf8_hex"])
    assert json.loads(raw)["attempt_generation"] == 1.0

    for operation in (
        lambda: load_json_bytes(raw),
        lambda: validate_json_bytes("verification-work.schema.json", raw),
    ):
        with pytest.raises(GraphContractError) as exc_info:
            operation()
        assert exc_info.value.code == vector["error_code"]


def test_schema_facade_uses_complete_registry_and_format_checker() -> None:
    assert SCHEMA_NAMES == frozenset({
        "artifact.schema.json",
        "bundle.schema.json",
        "chunk.schema.json",
        "dashboard-query.schema.json",
        "dashboard-response.schema.json",
        "verification-work.schema.json",
        "verification-result.schema.json",
        "graph-overlay.schema.json",
    })
    validate_schema(
        "bundle.schema.json",
        _golden()["contract_examples"]["bundle"],
    )
    invalid = copy.deepcopy(_golden()["contract_examples"]["bundle"])
    invalid["generated_at"] = "2026-02-31T12:00:00Z"
    with pytest.raises(GraphContractError) as exc_info:
        validate_schema("bundle.schema.json", invalid)
    assert exc_info.value.code == "schema_validation_failed"


def test_contract_lock_pins_manifest_and_all_registered_schemas() -> None:
    lock = json.loads(CONTRACT_LOCK.read_text(encoding="utf-8"))
    manifest_path = CONTRACT_ROOT / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert lock["schema"] == "hades.graph_v2_contract_lock.v1"
    assert lock["schema_source_commit"] == SCHEMA_SOURCE_COMMIT
    assert (
        lock["manifest_sha256"]
        == hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    )
    assert lock["schema_digests"] == {
        row["path"]: row["sha256"]
        for row in manifest["files"]
        if row["path"] in SCHEMA_NAMES
    }
    assert set(lock["schema_digests"]) == SCHEMA_NAMES


def test_packaged_schema_resources_are_byte_identical_to_contract_sources() -> None:
    for schema_name in SCHEMA_NAMES:
        source = (CONTRACT_ROOT / schema_name).read_bytes()
        packaged = (PACKAGED_CONTRACT_ROOT / schema_name).read_bytes()
        assert packaged == source
        assert (
            hashlib.sha256(packaged).hexdigest() == hashlib.sha256(source).hexdigest()
        )


def test_schema_facade_rejects_unknown_names_and_v1_with_typed_codes() -> None:
    with pytest.raises(GraphContractError) as exc_info:
        validate_schema("unknown.schema.json", {})
    assert exc_info.value.code == "unknown_schema_name"

    with pytest.raises(GraphContractError) as exc_info:
        validate_schema("artifact.schema.json", {"schema": "hades.code_graph.v1"})
    assert exc_info.value.code == "graph_v1_not_supported"

    with pytest.raises(GraphContractError) as exc_info:
        validate_schema("artifact-v1.schema.json", {})
    assert exc_info.value.code == "graph_v1_not_supported"

    for legacy_payload in (
        {"schema": "hades.php_graph.v1"},
        {"graph_contract": {"version": "hades.graph_artifact.v1"}},
    ):
        with pytest.raises(GraphContractError) as exc_info:
            validate_schema("artifact.schema.json", legacy_payload)
        assert exc_info.value.code == "graph_v1_not_supported"


@pytest.mark.parametrize(
    "document_name",
    [
        "preview10.schema.json",
        "graph-preview10.schema.json",
        "revision1-preview.schema.json",
        "nav1.schema.json",
        "kv1-preview.schema.json",
    ],
)
def test_schema_facade_does_not_misclassify_unknown_names_as_v1(
    document_name: str,
) -> None:
    with pytest.raises(GraphContractError) as exc_info:
        validate_schema(document_name, {})
    assert exc_info.value.code == "unknown_schema_name"


def test_schema_boundary_rejects_non_nfc_contract_strings() -> None:
    bundle = copy.deepcopy(_golden()["contract_examples"]["bundle"])
    bundle["source"]["branch"] = "Cafe\u0301"
    with pytest.raises(GraphContractError) as exc_info:
        validate_schema("bundle.schema.json", bundle)
    assert exc_info.value.code == "non_nfc_string"


@pytest.mark.parametrize("array_key", ["configuration_paths", "paths_sample"])
def test_schema_boundary_applies_source_path_limits_inside_path_arrays(
    array_key: str,
) -> None:
    bundle = _bundle_with_path_array(array_key, ["é" * 3000])

    with pytest.raises(GraphContractError) as exc_info:
        validate_schema("bundle.schema.json", bundle)
    assert exc_info.value.code == "unsafe_source_path"


@pytest.mark.parametrize("array_key", ["configuration_paths", "paths_sample"])
def test_schema_boundary_rejects_backslashes_inside_path_arrays(
    array_key: str,
) -> None:
    bundle = _bundle_with_path_array(array_key, [r"config\services.yaml"])

    with pytest.raises(GraphContractError) as exc_info:
        validate_schema("bundle.schema.json", bundle)
    assert exc_info.value.code == "unsafe_source_path"


@pytest.mark.parametrize("array_key", ["configuration_paths", "paths_sample"])
def test_artifact_digest_normalizes_every_source_path_array(array_key: str) -> None:
    posix = copy.deepcopy(_vector("artifact_digest")["input"])
    backslash = copy.deepcopy(posix)
    if array_key == "configuration_paths":
        posix["frameworks"] = [_framework(["config/services.yaml"])]
        backslash["frameworks"] = [_framework([r"config\services.yaml"])]
    else:
        reason = {
            "code": "unsupported_language",
            "count": 1,
            "language": "php",
            "paths_sample": ["src/Unsupported.php"],
        }
        posix["completeness"]["capabilities"]["inventory"] = {
            "status": "partial",
            "reasons": [reason],
        }
        backslash_reason = copy.deepcopy(reason)
        backslash_reason["paths_sample"] = [r"src\Unsupported.php"]
        backslash["completeness"]["capabilities"]["inventory"] = {
            "status": "partial",
            "reasons": [backslash_reason],
        }

    assert artifact_digest(posix) == artifact_digest(backslash)


def test_included_roots_keeps_dot_semantics_and_non_path_arrays_are_untouched() -> None:
    assert normalize_contract_value({"included_roots": ["."]}) == {
        "included_roots": ["."]
    }
    assert normalize_contract_value({"methods": [r"A\B"]}) == {"methods": [r"A\B"]}


def test_all_golden_identity_and_digest_helpers_match_exact_preimages() -> None:
    node = _vector("node_id")
    assert node_id(node["input"]) == "hades:node:v2:" + node["sha256"]

    edge = _vector("edge_id")
    assert edge_id(edge["input"]) == "hades:edge:v2:" + edge["sha256"]

    condition = _vector("condition_hash")
    assert condition_hash(condition["input"]["normalized_full"]) == condition["sha256"]

    ast = _vector("ast_source_fingerprint")
    assert (
        ast_source_fingerprint(
            ast["input"]["file_sha256"],
            ast["input"]["path"],
            ast["input"]["structural_path"],
        )
        == ast["sha256"]
    )

    config = _vector("config_source_fingerprint")
    assert (
        config_source_fingerprint(
            config["input"]["file_sha256"],
            config["input"]["path"],
            config["input"]["structural_pointer"],
        )
        == config["sha256"]
    )

    file_vector = _vector("file_source_fingerprint")
    assert (
        file_source_fingerprint(
            file_vector["input"]["file_sha256"],
            file_vector["input"]["path"],
        )
        == file_vector["sha256"]
    )

    flow = _vector("flow_id")
    assert (
        flow_id(
            flow["input"]["entrypoint_id"],
            flow["input"]["root_node_id"],
            flow["input"]["kind"],
        )
        == "hades:flow:v2:" + flow["sha256"]
    )

    step = _vector("flow_step_id")
    assert (
        flow_step_id(
            step["input"]["flow_id"],
            step["input"]["edge_id"],
            step["input"]["stage_from"],
            step["input"]["stage_to"],
            step["input"]["async_context"],
        )
        == "hades:flow-step:v2:" + step["sha256"]
    )

    for kind, helper in (
        ("call_site_id", call_site_id),
        ("branch_group_id", branch_group_id),
        ("exception_scope_id", exception_scope_id),
    ):
        vector = _vector(kind)
        assert helper(vector["input"]) == vector["public_id"]

    uncertainty = _vector("uncertainty_id")
    assert uncertainty_id(uncertainty["input"]) == (
        "hades:uncertainty:v2:" + uncertainty["sha256"]
    )

    evidence = _vector("evidence_digest")
    assert evidence_digest(evidence["input"]) == evidence["sha256"]

    empty_set = _vector("empty_verification_set_hash")
    assert verification_set_hash(empty_set["input"]) == empty_set["sha256"]

    verification_set = _vector("verification_set_hash")
    assert (
        verification_set_hash(verification_set["input"]) == verification_set["sha256"]
    )

    dedupe = _vector("verification_dedupe")
    assert verification_deduplication_key(dedupe["input"]) == dedupe["sha256"]

    result = _vector("result_digest")
    assert result_digest(result["input"]) == result["sha256"]

    artifact = _vector("artifact_digest")
    assert artifact_digest(artifact["input"]) == artifact["sha256"]


def test_verification_dedupe_accepts_exact_graph_and_wiki_direct_preimages() -> None:
    work_items = _verification_golden()["work_items"]
    for work in (work_items[0], work_items[3], work_items[4]):
        assert (
            verification_deduplication_key(_direct_verification_preimage(work))
            == work["deduplication_key"]
        )


def test_verification_dedupe_derives_exact_preimage_from_full_work_payloads() -> None:
    for work in _verification_golden()["work_items"]:
        assert verification_deduplication_key(work) == work["deduplication_key"]


def test_verification_dedupe_rejects_every_missing_or_extra_direct_field() -> None:
    work_items = _verification_golden()["work_items"]
    for work in (work_items[0], work_items[3], work_items[4]):
        direct = _direct_verification_preimage(work)
        invalid_values = [
            {key: value for key, value in direct.items() if key != missing}
            for missing in direct
        ]
        invalid_values.append({**direct, "extra": "not normative"})
        for invalid in invalid_values:
            with pytest.raises(GraphContractError) as exc_info:
                verification_deduplication_key(invalid)
            assert exc_info.value.code == "invalid_verification_preimage"


def test_verification_dedupe_rejects_kind_and_key_set_mismatches() -> None:
    graph, wiki = _verification_golden()["work_items"][0::3][:2]
    graph_direct = _direct_verification_preimage(graph)
    wiki_direct = _direct_verification_preimage(wiki)
    invalid_values = [
        {**graph_direct, "kind": "hades.verification.wiki.v1"},
        {**wiki_direct, "kind": "hades.verification.graph.v1"},
        {**graph_direct, "kind": "hades.verification.future.v2"},
    ]
    for invalid in invalid_values:
        with pytest.raises(GraphContractError) as exc_info:
            verification_deduplication_key(invalid)
        assert exc_info.value.code == "invalid_verification_preimage"


def test_artifact_graph_version_uses_only_exact_semantic_preimage() -> None:
    vector = _vector("artifact_digest")
    preimage = copy.deepcopy(vector["input"])
    artifact = {
        "schema": preimage["schema"],
        "generated_at": "2026-07-16T12:00:00Z",
        "project": preimage["project"],
        "source": preimage["source"],
        "graph_contract": {
            "version": preimage["graph_contract_version"],
            "artifact_graph_version": "0" * 64,
            "projection_state": "queued",
            "completeness": preimage["completeness"],
            "coverage": preimage["coverage"],
        },
        "frameworks": preimage["frameworks"],
        "languages": preimage["languages"],
        "entrypoints": preimage["entrypoints"],
        "nodes": preimage["nodes"],
        "structures": preimage["structures"],
        "edges": preimage["edges"],
        "flows": preimage["flows"],
        "flow_steps": preimage["flow_steps"],
        "uncertainties": preimage["uncertainties"],
    }
    assert artifact_graph_version(artifact) == vector["sha256"]
    artifact["generated_at"] = "2026-07-16T12:00:01Z"
    artifact["graph_contract"]["projection_state"] = "ready"
    artifact["graph_contract"]["artifact_graph_version"] = "f" * 64
    assert artifact_graph_version(artifact) == vector["sha256"]


def test_canonicalize_records_sorts_and_deduplicates_identical_records() -> None:
    a_id = "hades:node:v2:" + "a" * 64
    b_id = "hades:node:v2:" + "b" * 64
    records = [
        {"id": b_id, "label": "Cafe\u0301"},
        {"label": "Caf\u00e9", "id": b_id},
        {"id": a_id, "label": "A"},
    ]
    expected = [
        {"id": a_id, "label": "A"},
        {"id": b_id, "label": "Caf\u00e9"},
    ]
    assert canonicalize_records(records) == expected
    assert canonicalize_records(list(reversed(records))) == expected


def test_verification_set_hash_is_permutation_stable() -> None:
    first = copy.deepcopy(_vector("verification_set_hash")["input"][0])
    second = copy.deepcopy(first)
    second["assertion_fingerprint"] = "1" * 64
    second["overlay"]["assertion_fingerprint"] = "1" * 64
    second["overlay"]["uncertainty_id"] = "hades:uncertainty:v2:" + "1" * 64
    assert verification_set_hash([first, second]) == verification_set_hash([
        second,
        first,
    ])


def test_projection_version_rejects_noncanonical_digests() -> None:
    for artifact_version, overlay_hash in (
        ("A" * 64, "b" * 64),
        ("a" * 63, "b" * 64),
        ("a" * 64, "b" * 65),
    ):
        with pytest.raises(GraphContractError) as exc_info:
            projection_version(artifact_version, overlay_hash)
        assert exc_info.value.code == "invalid_digest"


def _task3_api() -> tuple[Any, Any, Any]:
    from hermes_cli.hades_graph_v2 import coverage, model, validation

    return coverage, model, validation


def _capabilities(status: str = "full") -> dict[str, Any]:
    return {
        name: {"status": status, "reasons": []}
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


def _valid_semantic_artifact() -> dict[str, Any]:
    binding_id = "01KXJD1BDMQ2TFABMVJV6EFE8Q"
    path = "src/Example.php"
    file_digest = "e" * 64
    identity = {
        "variant": "file",
        "workspace_binding_id": binding_id,
        "language": "php",
        "kind": "file",
        "path": path,
    }
    file_node_id = node_id(identity)
    capabilities = _capabilities()
    capabilities["framework_lifecycle"] = {
        "status": "not_applicable",
        "reasons": [],
    }
    artifact = {
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
            "artifact_graph_version": "0" * 64,
            "projection_state": "queued",
            "completeness": {
                "status": "full",
                "capabilities": capabilities,
                "languages": [
                    {
                        "language": "php",
                        "status": "full",
                        "capabilities": capabilities,
                    }
                ],
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
                    "analyzed": 1,
                    "unsupported": 0,
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
        "languages": [
            {
                "name": "php",
                "extractor": "php.generic.v2",
                "extractor_version": "2",
                "detected_file_count": 1,
                "analyzed_file_count": 1,
            }
        ],
        "entrypoints": [],
        "nodes": [
            {
                "id": file_node_id,
                "identity": identity,
                "kind": "file",
                "language": "php",
                "framework": None,
                "name": "Example.php",
                "qualified_name": path,
                "namespace": None,
                "uncertainty_id": None,
                "location": None,
                "properties": {
                    "file_sha256": file_digest,
                    "byte_size": 0,
                    "analysis_status": "analyzed",
                    "omission_reason": None,
                    "is_test": False,
                    "is_generated": False,
                },
                "evidence": {
                    "primary": {
                        "origin": "verified_from_code",
                        "extractor": "inventory.v2",
                        "source_locator": {"kind": "file", "path": path},
                        "source_fingerprint": file_source_fingerprint(
                            file_digest, path
                        ),
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
    artifact["graph_contract"]["artifact_graph_version"] = artifact_graph_version(
        artifact
    )
    return artifact


def _with_external_uncertainty() -> dict[str, Any]:
    artifact = _valid_semantic_artifact()
    binding_id = artifact["project"]["workspace_binding_id"]
    path = artifact["nodes"][0]["identity"]["path"]
    file_digest = artifact["nodes"][0]["properties"]["file_sha256"]
    declaration_identity = {
        "variant": "source_declaration",
        "workspace_binding_id": binding_id,
        "language": "php",
        "kind": "method",
        "namespace": "App",
        "qualified_name": "App\\Example::run",
        "path": path,
    }
    declaration_id = node_id(declaration_identity)
    boundary_identity = {
        "variant": "source_occurrence",
        "workspace_binding_id": binding_id,
        "language": "php",
        "kind": "unknown_boundary",
        "owner_node_id": declaration_id,
        "structural_path": "body/call/0/unknown_target",
        "ordinal": 0,
        "semantic_role": "external_target",
    }
    boundary_id = node_id(boundary_identity)
    occurrence = {
        "kind": "ast",
        "owner_node_id": declaration_id,
        "ast_path": "body/call/0",
        "ordinal": 0,
    }
    edge_identity = {
        "source_id": declaration_id,
        "target_id": boundary_id,
        "relation": "calls_external",
        "flow": "always",
        "condition_hash": None,
        "branch_group_id": None,
        "call_site_id": None,
        "exception_scope_id": None,
        "occurrence": occurrence,
    }
    subject_edge_id = edge_id(edge_identity)
    question = "Which external target does this source occurrence reach?"
    uncertainty_preimage = {
        "domain": "graph",
        "project_id": artifact["project"]["project_id"],
        "workspace_binding_id": binding_id,
        "subject": {"edge_id": subject_edge_id},
        "resolution_kind": "external_target",
        "reason_code": "external_boundary_unresolved",
        "question": question,
    }
    unresolved_id = uncertainty_id(uncertainty_preimage)
    locator = {"kind": "ast", "path": path, "structural_path": "body/call/0"}
    source_fingerprint = ast_source_fingerprint(
        file_digest, path, locator["structural_path"]
    )

    def evidence(origin: str) -> dict[str, Any]:
        return {
            "primary": {
                "origin": origin,
                "extractor": "php.generic.v2",
                "source_locator": locator,
                "source_fingerprint": source_fingerprint,
                "inference_rule": None,
            },
            "supporting": [],
            "supporting_omitted_count": 0,
        }

    artifact["nodes"].extend([
        {
            "id": declaration_id,
            "identity": declaration_identity,
            "kind": "method",
            "language": "php",
            "framework": None,
            "name": "run",
            "qualified_name": "App\\Example::run",
            "namespace": "App",
            "uncertainty_id": None,
            "location": {"path": path, "start_line": 1, "end_line": 2},
            "properties": {},
            "evidence": evidence("verified_from_code"),
        },
        {
            "id": boundary_id,
            "identity": boundary_identity,
            "kind": "unknown_boundary",
            "language": "php",
            "framework": None,
            "name": "Unresolved external target",
            "qualified_name": None,
            "namespace": None,
            "uncertainty_id": unresolved_id,
            "location": {"path": path, "start_line": 2, "end_line": 2},
            "properties": {"reason_code": "external_boundary_unresolved"},
            "evidence": evidence("unresolved"),
        },
    ])
    artifact["nodes"].sort(key=lambda record: record["id"])
    artifact["edges"] = [
        {
            "id": subject_edge_id,
            "source_id": declaration_id,
            "target_id": boundary_id,
            "relation": "calls_external",
            "flow": "always",
            "condition": None,
            "branch_group_id": None,
            "call_site_id": None,
            "exception_scope_id": None,
            "order": 0,
            "uncertainty_id": unresolved_id,
            "occurrence": occurrence,
            "evidence": evidence("unresolved"),
            "location": {"path": path, "line": 2, "ordinal": 0},
        }
    ]
    artifact["uncertainties"] = [
        {
            "id": unresolved_id,
            **{key: uncertainty_preimage[key] for key in ("domain", "subject")},
            "resolution_kind": "external_target",
            "reason_code": "external_boundary_unresolved",
            "question": question,
            "evidence_requirements": ["inspect_external_configuration"],
            "source_refs": [{"path": path, "line": 2}],
            "candidate_target_node_ids": [],
            "candidate_edge_ids": [],
            "candidate_set_knowledge": "not_applicable",
            "priority": "normal",
            "impact": "May change the external data effect.",
            "fingerprint": unresolved_id.removeprefix("hades:uncertainty:v2:"),
        }
    ]
    records = artifact["graph_contract"]["coverage"]["records"]
    records.update(nodes=3, edges=1, uncertainties=1)
    artifact["graph_contract"]["completeness"]["status"] = "partial"
    artifact["graph_contract"]["completeness"]["capabilities"]["data_access"] = {
        "status": "partial",
        "reasons": [
            {
                "code": "external_boundary_unresolved",
                "count": 1,
                "language": "php",
                "paths_sample": [path],
            }
        ],
    }
    artifact["graph_contract"]["completeness"]["languages"][0]["status"] = "partial"
    artifact["graph_contract"]["completeness"]["languages"][0]["capabilities"][
        "data_access"
    ] = copy.deepcopy(
        artifact["graph_contract"]["completeness"]["capabilities"]["data_access"]
    )
    artifact["graph_contract"]["artifact_graph_version"] = artifact_graph_version(
        artifact
    )
    return artifact


def _append_executable_node(
    artifact: dict[str, Any],
    *,
    kind: str,
    structural_path: str,
    owner_node_id: str | None = None,
) -> str:
    file_node = artifact["nodes"][0]
    path = file_node["identity"]["path"]
    binding_id = artifact["project"]["workspace_binding_id"]
    if owner_node_id is None:
        qualified_name = f"App\\Example::{kind}"
        identity = {
            "variant": "source_declaration",
            "workspace_binding_id": binding_id,
            "language": "php",
            "kind": kind,
            "namespace": "App",
            "qualified_name": qualified_name,
            "path": path,
        }
        name = kind
        namespace = "App"
    else:
        qualified_name = None
        identity = {
            "variant": "source_occurrence",
            "workspace_binding_id": binding_id,
            "language": "php",
            "kind": kind,
            "owner_node_id": owner_node_id,
            "structural_path": structural_path,
            "ordinal": 0,
            "semantic_role": "continuation",
        }
        name = "Continuation"
        namespace = None
    public_id = node_id(identity)
    artifact["nodes"].append({
        "id": public_id,
        "identity": identity,
        "kind": kind,
        "language": "php",
        "framework": None,
        "name": name,
        "qualified_name": qualified_name,
        "namespace": namespace,
        "uncertainty_id": None,
        "location": {"path": path, "start_line": 1, "end_line": 1},
        "properties": {},
        "evidence": _producer_evidence(artifact, structural_path),
    })
    artifact["nodes"].sort(key=lambda record: record["id"])
    return public_id


def _producer_evidence(
    artifact: dict[str, Any], structural_path: str
) -> dict[str, Any]:
    file_node = next(
        node for node in artifact["nodes"] if node["identity"]["variant"] == "file"
    )
    path = file_node["identity"]["path"]
    return {
        "primary": {
            "origin": "verified_from_code",
            "extractor": "php.generic.v2",
            "source_locator": {
                "kind": "ast",
                "path": path,
                "structural_path": structural_path,
            },
            "source_fingerprint": ast_source_fingerprint(
                file_node["properties"]["file_sha256"], path, structural_path
            ),
            "inference_rule": None,
        },
        "supporting": [],
        "supporting_omitted_count": 0,
    }


def test_partial_family_never_returns_verified_zero() -> None:
    coverage, model, _ = _task3_api()

    count = coverage.count_knowledge(
        represented=0,
        omitted=2,
        capability_status="partial",
    )

    assert count == model.CountKnowledge(
        represented=0,
        value=None,
        knowledge=model.Knowledge.UNKNOWN,
        reason=model.ReasonCode.RESOURCE_BUDGET_REACHED,
    )


def test_count_knowledge_uses_only_frozen_exact_absent_unknown_union() -> None:
    coverage, model, _ = _task3_api()

    assert coverage.count_knowledge(3, 0, "full") == model.CountKnowledge(
        represented=3,
        value=3,
        knowledge=model.Knowledge.EXACT,
        reason=None,
    )
    assert coverage.count_knowledge(0, 0, "full") == model.CountKnowledge(
        represented=0,
        value=0,
        knowledge=model.Knowledge.ABSENCE_VERIFIED,
        reason=None,
    )
    assert {member.value for member in model.Knowledge} == {
        "exact",
        "absence_verified",
        "unknown",
    }
    assert not hasattr(model.CountKnowledge, "exact_value")


def test_artifact_payload_round_trip_is_schema_gated_and_frozen() -> None:
    _, model, validation = _task3_api()
    payload = _valid_semantic_artifact()

    artifact = model.artifact_from_payload(payload)
    assert model.artifact_to_payload(artifact) == payload
    assert isinstance(artifact.nodes, tuple)
    assert all(
        dataclasses.is_dataclass(instance)
        and instance.__dataclass_params__.frozen
        and hasattr(instance, "__slots__")
        for instance in (artifact, artifact.project, artifact.nodes[0])
    )
    validation.validate_artifact(artifact)


def test_artifact_payload_round_trip_preserves_explicit_null_property() -> None:
    _, model, _ = _task3_api()
    payload = _valid_semantic_artifact()
    file_node = payload["nodes"][0]
    identity = {
        "variant": "source_declaration",
        "workspace_binding_id": payload["project"]["workspace_binding_id"],
        "language": "php",
        "kind": "module",
        "namespace": None,
        "qualified_name": "App\\Example",
        "path": file_node["identity"]["path"],
    }
    module_node = copy.deepcopy(file_node)
    module_node.update({
        "id": node_id(identity),
        "identity": identity,
        "kind": "module",
        "name": "Example",
        "qualified_name": "App\\Example",
        "properties": {"package": None},
    })
    module_node["evidence"]["primary"].update({
        "extractor": "php.generic.v2",
        "source_locator": {
            "kind": "ast",
            "path": file_node["identity"]["path"],
            "structural_path": "declaration[0]",
        },
        "source_fingerprint": ast_source_fingerprint(
            file_node["properties"]["file_sha256"],
            file_node["identity"]["path"],
            "declaration[0]",
        ),
    })
    payload["nodes"].append(module_node)

    parsed = model.artifact_from_payload(payload)

    assert model.artifact_to_payload(parsed) == payload


def test_schema_and_dataclass_public_field_inventories_match() -> None:
    _, model, _ = _task3_api()
    artifact_schema = json.loads(
        (CONTRACT_ROOT / "artifact.schema.json").read_text(encoding="utf-8")
    )
    definitions = artifact_schema["$defs"]
    inventory = {
        "GraphArtifactV2": (None, artifact_schema),
        "ProjectIdentity": ("project", definitions["project"]),
        "SourceIdentity": ("source", definitions["source"]),
        "GraphContractMetadata": ("graphContract", definitions["graphContract"]),
        "LanguageRecord": ("language", definitions["language"]),
        "FrameworkRecord": ("framework", definitions["framework"]),
        "CapabilityReason": ("capabilityReason", definitions["capabilityReason"]),
        "Capability": ("capability", definitions["capabilityIncomplete"]),
        "Capabilities": ("capabilities", definitions["capabilities"]),
        "LanguageCompleteness": (
            "languageCompleteness",
            definitions["languageCompleteness"],
        ),
        "Completeness": ("completeness", definitions["completeness"]),
        "FlowCompleteness": ("flowCompleteness", definitions["flowCompleteness"]),
        "CoverageScope": ("coverageScope", definitions["coverageScope"]),
        "FileCoverage": ("fileCoverage", definitions["fileCoverage"]),
        "EntrypointKindCounts": (
            "entrypointKindCounts",
            definitions["entrypointKindCounts"],
        ),
        "EntrypointCoverage": (
            "entrypointCoverage",
            definitions["entrypointCoverage"],
        ),
        "RecordCoverage": ("recordCoverage", definitions["recordCoverage"]),
        "Coverage": ("coverage", definitions["coverage"]),
        "RegistrationAst": ("registrationAst", definitions["registrationAst"]),
        "RegistrationConfig": (
            "registrationConfig",
            definitions["registrationConfig"],
        ),
        "Trigger": ("trigger", definitions["trigger"]),
        "MatchConstraints": ("matchConstraints", definitions["matchConstraints"]),
        "EntrypointIdentity": (
            "entrypointIdentity",
            definitions["entrypointIdentity"],
        ),
        "SourceDeclarationIdentity": (
            "sourceDeclarationIdentity",
            definitions["sourceDeclarationIdentity"],
        ),
        "FileIdentity": ("fileIdentity", definitions["fileIdentity"]),
        "SourceOccurrenceIdentity": (
            "sourceOccurrenceIdentity",
            definitions["sourceOccurrenceIdentity"],
        ),
        "AnonymousCallableIdentity": (
            "anonymousCallableIdentity",
            definitions["anonymousCallableIdentity"],
        ),
        "EntrypointNodeIdentity": (
            "entrypointNodeIdentity",
            definitions["entrypointNodeIdentity"],
        ),
        "SemanticResourceIdentity": (
            "semanticResourceIdentity",
            definitions["semanticResourceIdentity"],
        ),
        "FileSourceLocator": ("fileLocator", definitions["fileLocator"]),
        "AstSourceLocator": ("astLocator", definitions["astLocator"]),
        "ConfigSourceLocator": ("configLocator", definitions["configLocator"]),
        "EvidenceItem": ("producerEvidenceItem", definitions["producerEvidenceItem"]),
        "EvidenceEnvelope": (
            "producerEvidenceEnvelope",
            definitions["producerEvidenceEnvelope"],
        ),
        "SourceLocation": ("sourceLocation", definitions["sourceLocation"]),
        "FileProperties": ("fileProperties", definitions["fileProperties"]),
        "ModuleProperties": ("moduleProperties", definitions["moduleProperties"]),
        "TypeProperties": ("typeProperties", definitions["typeProperties"]),
        "CallableProperties": (
            "callableProperties",
            definitions["callableProperties"],
        ),
        "ControlProperties": ("controlProperties", definitions["controlProperties"]),
        "FrameworkProperties": (
            "frameworkProperties",
            definitions["frameworkProperties"],
        ),
        "DataProperties": ("dataProperties", definitions["dataProperties"]),
        "IntegrationProperties": (
            "integrationProperties",
            definitions["integrationProperties"],
        ),
        "TerminalProperties": (
            "terminalProperties",
            definitions["terminalProperties"],
        ),
        "AsyncProperties": ("asyncProperties", definitions["asyncProperties"]),
        "TestProperties": ("testProperties", definitions["testProperties"]),
        "BoundaryProperties": (
            "boundaryProperties",
            definitions["boundaryProperties"],
        ),
        "Entrypoint": ("entrypoint", definitions["entrypoint"]),
        "Node": ("node", definitions["node"]),
        "Structure": ("structure", definitions["structure"]),
        "Condition": ("condition", definitions["condition"]),
        "EdgeAstOccurrence": (
            "edgeAstOccurrence",
            definitions["edgeAstOccurrence"],
        ),
        "EdgeConfigOccurrence": (
            "edgeConfigOccurrence",
            definitions["edgeConfigOccurrence"],
        ),
        "EdgeLocation": ("edgeLocation", definitions["edgeLocation"]),
        "Edge": ("edge", definitions["edge"]),
        "StageCounts": ("stageCounts", definitions["stageCounts"]),
        "Flow": ("flow", definitions["flow"]),
        "FlowStep": ("flowStep", definitions["flowStep"]),
        "CallSiteSubject": ("callSiteSubject", definitions["callSiteSubject"]),
        "EdgeSubject": ("edgeSubject", definitions["edgeSubject"]),
        "SourceRef": ("sourceRef", definitions["sourceRef"]),
        "Uncertainty": ("uncertainty", definitions["uncertainty"]),
        "CountKnowledge": ("countKnowledge", definitions["countUnknown"]),
    }
    for class_name, (_, schema) in inventory.items():
        field_names = model.dataclass_wire_fields(getattr(model, class_name))
        assert field_names == set(schema["properties"]), class_name
    public_dataclasses = {
        name
        for name, value in vars(model).items()
        if not name.startswith("_")
        and isinstance(value, type)
        and dataclasses.is_dataclass(value)
    }
    assert public_dataclasses == set(inventory)
    assert all(
        getattr(model, name).__dataclass_params__.frozen
        and hasattr(getattr(model, name), "__slots__")
        for name in public_dataclasses
    )


def test_base_artifact_rejects_agent_verified_evidence() -> None:
    _, _, validation = _task3_api()
    payload = _valid_semantic_artifact()
    payload["nodes"][0]["evidence"]["primary"]["origin"] = "agent_verified"

    with pytest.raises(validation.GraphValidationError) as exc_info:
        validation.validate_artifact(payload)

    assert exc_info.value.code == "base_evidence_origin"
    assert "base artifact evidence origin" in exc_info.value.message


def test_unresolved_target_requires_exact_uncertainty_subject() -> None:
    _, _, validation = _task3_api()
    payload = _with_external_uncertainty()
    payload["edges"][0]["uncertainty_id"] = None

    with pytest.raises(validation.GraphValidationError) as exc_info:
        validation.validate_artifact(payload)

    assert exc_info.value.code == "uncertainty_ownership"
    assert "uncertainty ownership" in exc_info.value.message


def test_dangling_evidence_locator_is_rejected_without_path_disclosure() -> None:
    _, _, validation = _task3_api()
    payload = _valid_semantic_artifact()
    private_path = "private/credentials.php"
    evidence = payload["nodes"][0]["evidence"]["primary"]
    evidence["source_locator"]["path"] = private_path
    evidence["source_fingerprint"] = file_source_fingerprint("e" * 64, private_path)

    with pytest.raises(validation.GraphValidationError) as exc_info:
        validation.validate_artifact(payload)

    assert exc_info.value.code == "dangling_file_locator"
    assert private_path not in str(exc_info.value)


def test_serialized_ids_are_recomputed_not_trusted() -> None:
    _, _, validation = _task3_api()
    payload = _valid_semantic_artifact()
    payload["nodes"][0]["id"] = "hades:node:v2:" + "f" * 64

    with pytest.raises(validation.GraphValidationError) as exc_info:
        validation.validate_artifact(payload)

    assert exc_info.value.code == "node_id_mismatch"


def test_references_are_isolated_to_the_current_artifact() -> None:
    _, _, validation = _task3_api()
    other = _valid_semantic_artifact()
    other["nodes"][0]["identity"]["path"] = "src/Elsewhere.php"
    other["nodes"][0]["qualified_name"] = "src/Elsewhere.php"
    other["nodes"][0]["name"] = "Elsewhere.php"
    other["nodes"][0]["evidence"]["primary"]["source_locator"]["path"] = (
        "src/Elsewhere.php"
    )
    other["nodes"][0]["id"] = node_id(other["nodes"][0]["identity"])
    other["nodes"][0]["evidence"]["primary"]["source_fingerprint"] = (
        file_source_fingerprint("e" * 64, "src/Elsewhere.php")
    )
    other["graph_contract"]["artifact_graph_version"] = artifact_graph_version(other)
    validation.validate_artifact(other)

    payload = _valid_semantic_artifact()
    evidence = payload["nodes"][0]["evidence"]["primary"]
    evidence["source_locator"]["path"] = "src/Elsewhere.php"
    evidence["source_fingerprint"] = file_source_fingerprint(
        "e" * 64, "src/Elsewhere.php"
    )
    with pytest.raises(validation.GraphValidationError) as exc_info:
        validation.validate_artifact(payload)
    assert exc_info.value.code == "dangling_file_locator"


def test_return_edge_requires_a_matching_invocation() -> None:
    _, model, validation = _task3_api()
    payload = _valid_semantic_artifact()
    caller_id = _append_executable_node(
        payload,
        kind="method",
        structural_path="declaration/method/caller",
    )
    continuation_id = _append_executable_node(
        payload,
        kind="basic_block",
        structural_path="body/call/0/continuation",
        owner_node_id=caller_id,
    )
    structure_identity = {
        "kind": "call_site",
        "owner_node_id": caller_id,
        "structural_path": "body/call/0",
        "ordinal": 0,
        "subtype": "call",
    }
    structure_id = call_site_id(structure_identity)
    payload["structures"] = [
        {
            "id": structure_id,
            **structure_identity,
            "continuation_node_id": continuation_id,
            "parent_structure_id": None,
            "evidence": _producer_evidence(payload, "body/call/0"),
        }
    ]
    occurrence = {
        "kind": "ast",
        "owner_node_id": caller_id,
        "ast_path": "body/call/0/return",
        "ordinal": 0,
    }
    identity = {
        "source_id": caller_id,
        "target_id": continuation_id,
        "relation": "returns_to",
        "flow": "always",
        "condition_hash": None,
        "branch_group_id": None,
        "call_site_id": structure_id,
        "exception_scope_id": None,
        "occurrence": occurrence,
    }
    payload["edges"] = [
        {
            "id": edge_id(identity),
            **{key: identity[key] for key in ("source_id", "target_id", "relation")},
            "flow": "always",
            "condition": None,
            "branch_group_id": None,
            "call_site_id": structure_id,
            "exception_scope_id": None,
            "order": None,
            "uncertainty_id": None,
            "occurrence": occurrence,
            "evidence": _producer_evidence(payload, "body/call/0/return"),
            "location": {
                "path": next(
                    node["identity"]["path"]
                    for node in payload["nodes"]
                    if node["identity"]["variant"] == "file"
                ),
                "line": 1,
                "ordinal": 0,
            },
        }
    ]
    artifact = model.artifact_from_payload(payload)

    with pytest.raises(validation.GraphValidationError) as exc_info:
        validation.validate_references(
            artifact, validation.build_record_index(artifact)
        )

    assert exc_info.value.code == "return_invocation_missing"


def test_structure_parent_chain_cannot_cycle() -> None:
    _, model, validation = _task3_api()
    payload = _valid_semantic_artifact()
    owner_id = _append_executable_node(
        payload,
        kind="method",
        structural_path="declaration/method/owner",
    )
    first_identity = {
        "kind": "branch_group",
        "owner_node_id": owner_id,
        "structural_path": "body/branch/0",
        "ordinal": 0,
        "subtype": "if",
    }
    second_identity = {
        "kind": "branch_group",
        "owner_node_id": owner_id,
        "structural_path": "body/branch/0/branch/0",
        "ordinal": 0,
        "subtype": "if",
    }
    first_id = branch_group_id(first_identity)
    second_id = branch_group_id(second_identity)
    payload["structures"] = sorted(
        [
            {
                "id": first_id,
                **first_identity,
                "continuation_node_id": None,
                "parent_structure_id": second_id,
                "evidence": _producer_evidence(payload, "body/branch/0"),
            },
            {
                "id": second_id,
                **second_identity,
                "continuation_node_id": None,
                "parent_structure_id": first_id,
                "evidence": _producer_evidence(payload, "body/branch/0/branch/0"),
            },
        ],
        key=lambda record: record["id"],
    )
    artifact = model.artifact_from_payload(payload)

    with pytest.raises(validation.GraphValidationError) as exc_info:
        validation.validate_references(
            artifact, validation.build_record_index(artifact)
        )

    assert exc_info.value.code == "structure_parent_cycle"


def test_flow_step_membership_cannot_reference_another_artifact() -> None:
    _, _, validation = _task3_api()
    payload = _valid_semantic_artifact()
    external_flow_id = "hades:flow:v2:" + "a" * 64
    external_edge_id = "hades:edge:v2:" + "b" * 64
    payload["flow_steps"] = [
        {
            "id": flow_step_id(
                external_flow_id,
                external_edge_id,
                "entry",
                "routing",
                "synchronous",
            ),
            "flow_id": external_flow_id,
            "edge_id": external_edge_id,
            "stage_from": "entry",
            "stage_to": "routing",
            "min_depth": 0,
            "branch_group_id": None,
            "async_context": "synchronous",
            "async_child_flow_id": None,
            "async_cycle": False,
            "backbone_role": "mandatory",
            "order_key": "00:000000:entry:routing:edge",
        }
    ]
    payload["graph_contract"]["coverage"]["records"]["flow_steps"] = 1

    with pytest.raises(validation.GraphValidationError) as exc_info:
        validation.validate_artifact(payload)

    assert exc_info.value.code == "dangling_flow_reference"


def test_coverage_record_counts_close_over_represented_records() -> None:
    _, _, validation = _task3_api()
    payload = _valid_semantic_artifact()
    payload["graph_contract"]["coverage"]["records"]["nodes"] = 0

    with pytest.raises(validation.GraphValidationError) as exc_info:
        validation.validate_artifact(payload)

    assert exc_info.value.code == "record_coverage_mismatch"


def test_artifact_digest_is_the_last_semantic_closure_check() -> None:
    _, _, validation = _task3_api()
    payload = _valid_semantic_artifact()
    payload["graph_contract"]["artifact_graph_version"] = "f" * 64

    with pytest.raises(validation.GraphValidationError) as exc_info:
        validation.validate_artifact(payload)

    assert exc_info.value.code == "artifact_digest_mismatch"


def _exact_count(value: int) -> dict[str, Any]:
    return {
        "represented": value,
        "value": value,
        "knowledge": "exact" if value else "absence_verified",
        "reason": None,
    }


def _order_key(
    stage_from: str,
    min_depth: int,
    source_id: str,
    target_id: str,
    public_edge_id: str,
) -> str:
    stage_ordinal = [
        "entry",
        "routing",
        "middleware",
        "security",
        "input",
        "handler",
        "domain",
        "data",
        "integration",
        "async",
        "response",
        "error",
    ].index(stage_from)
    return (
        f"{stage_ordinal:02d}:{min_depth:06d}:{source_id}:{target_id}:{public_edge_id}"
    )


def _append_edge(
    artifact: dict[str, Any],
    *,
    source_id: str,
    target_id: str,
    relation: str,
    flow: str,
    owner_node_id: str,
    ast_path: str,
    order: int,
) -> str:
    occurrence = {
        "kind": "ast",
        "owner_node_id": owner_node_id,
        "ast_path": ast_path,
        "ordinal": 0,
    }
    identity = {
        "source_id": source_id,
        "target_id": target_id,
        "relation": relation,
        "flow": flow,
        "condition_hash": None,
        "branch_group_id": None,
        "call_site_id": None,
        "exception_scope_id": None,
        "occurrence": occurrence,
    }
    public_id = edge_id(identity)
    file_path = next(
        node["identity"]["path"]
        for node in artifact["nodes"]
        if node["identity"]["variant"] == "file"
    )
    artifact["edges"].append({
        "id": public_id,
        "source_id": source_id,
        "target_id": target_id,
        "relation": relation,
        "flow": flow,
        "condition": None,
        "branch_group_id": None,
        "call_site_id": None,
        "exception_scope_id": None,
        "order": order,
        "uncertainty_id": None,
        "occurrence": occurrence,
        "evidence": _producer_evidence(artifact, ast_path),
        "location": {"path": file_path, "line": 1, "ordinal": order},
    })
    return public_id


def _append_flow_step(
    artifact: dict[str, Any],
    *,
    public_flow_id: str,
    public_edge_id: str,
    stage_from: str,
    stage_to: str,
    min_depth: int,
    async_context: str,
    child_flow_id: str | None = None,
    async_cycle: bool = False,
    backbone_role: str = "mandatory",
) -> None:
    edge = next(
        record for record in artifact["edges"] if record["id"] == public_edge_id
    )
    artifact["flow_steps"].append({
        "id": flow_step_id(
            public_flow_id,
            public_edge_id,
            stage_from,
            stage_to,
            async_context,
        ),
        "flow_id": public_flow_id,
        "edge_id": public_edge_id,
        "stage_from": stage_from,
        "stage_to": stage_to,
        "min_depth": min_depth,
        "branch_group_id": edge["branch_group_id"],
        "async_context": async_context,
        "async_child_flow_id": child_flow_id,
        "async_cycle": async_cycle,
        "backbone_role": backbone_role,
        "order_key": _order_key(
            stage_from,
            min_depth,
            edge["source_id"],
            edge["target_id"],
            public_edge_id,
        ),
    })


def _valid_flow_artifact() -> dict[str, Any]:
    artifact = _valid_semantic_artifact()
    binding_id = artifact["project"]["workspace_binding_id"]
    path = artifact["nodes"][0]["identity"]["path"]

    entrypoint_identity = {
        "entrypoint_kind": "http_route",
        "framework": None,
        "method_semantics": "explicit",
        "methods": ["GET"],
        "public_path": "/jobs",
        "public_name": "App\\Http\\jobs",
        "trigger": {"kind": "http", "value": "GET /jobs"},
        "match_constraints": {
            "host": None,
            "schemes": [],
            "condition_hash": None,
        },
        "registration_occurrence": {
            "kind": "ast",
            "path": path,
            "structural_path": "routes/jobs",
            "ordinal": 0,
        },
    }
    entrypoint_node_identity = {
        "variant": "entrypoint",
        "workspace_binding_id": binding_id,
        "language": "php",
        "kind": "entrypoint",
        "path": path,
        "entrypoint_identity": entrypoint_identity,
    }
    entrypoint_id = node_id(entrypoint_node_identity)
    handler_id = _append_executable_node(
        artifact,
        kind="method",
        structural_path="declaration/controller/jobs",
    )
    job_id = _append_executable_node(
        artifact,
        kind="job",
        structural_path="declaration/job/send",
    )
    response_id = _append_executable_node(
        artifact,
        kind="response",
        structural_path="body/response/0",
        owner_node_id=handler_id,
    )
    async_exit_id = _append_executable_node(
        artifact,
        kind="exit",
        structural_path="body/exit/0",
        owner_node_id=job_id,
    )
    artifact["nodes"].append({
        "id": entrypoint_id,
        "identity": entrypoint_node_identity,
        "kind": "entrypoint",
        "language": "php",
        "framework": None,
        "name": "GET /jobs",
        "qualified_name": "App\\Http\\jobs",
        "namespace": None,
        "uncertainty_id": None,
        "location": {"path": path, "start_line": 1, "end_line": 1},
        "properties": {},
        "evidence": _producer_evidence(artifact, "routes/jobs"),
    })
    artifact["nodes"].sort(key=lambda record: record["id"])
    artifact["entrypoints"] = [
        {
            "id": entrypoint_id,
            "entrypoint_kind": "http_route",
            "label": "GET /jobs",
            "framework": None,
            "method_semantics": "explicit",
            "methods": ["GET"],
            "public_path": "/jobs",
            "public_name": "App\\Http\\jobs",
            "handler_node_id": handler_id,
            "uncertainty_id": None,
            "trigger": entrypoint_identity["trigger"],
            "match_constraints": entrypoint_identity["match_constraints"],
            "registration_occurrence": entrypoint_identity["registration_occurrence"],
            "evidence": _producer_evidence(artifact, "routes/jobs"),
        }
    ]

    route_edge_id = _append_edge(
        artifact,
        source_id=entrypoint_id,
        target_id=handler_id,
        relation="routes_to",
        flow="always",
        owner_node_id=entrypoint_id,
        ast_path="routes/jobs/handler",
        order=0,
    )
    dispatch_edge_id = _append_edge(
        artifact,
        source_id=handler_id,
        target_id=job_id,
        relation="dispatches",
        flow="async",
        owner_node_id=handler_id,
        ast_path="body/dispatch/0",
        order=1,
    )
    response_edge_id = _append_edge(
        artifact,
        source_id=handler_id,
        target_id=response_id,
        relation="responds_with",
        flow="always",
        owner_node_id=handler_id,
        ast_path="body/response/0",
        order=2,
    )
    async_exit_edge_id = _append_edge(
        artifact,
        source_id=job_id,
        target_id=async_exit_id,
        relation="exits_at",
        flow="always",
        owner_node_id=job_id,
        ast_path="body/exit/0",
        order=0,
    )
    artifact["edges"].sort(key=lambda record: record["id"])

    sync_flow_id = flow_id(entrypoint_id, entrypoint_id, "request_lifecycle")
    child_flow_id = flow_id(entrypoint_id, job_id, "async_flow")
    capabilities = copy.deepcopy(
        artifact["graph_contract"]["completeness"]["capabilities"]
    )
    artifact["flows"] = [
        {
            "id": sync_flow_id,
            "entrypoint_id": entrypoint_id,
            "root_node_id": entrypoint_id,
            "kind": "request_lifecycle",
            "represented_step_count": 3,
            "terminal_count": _exact_count(1),
            "linked_async_flow_count": _exact_count(1),
            "stage_counts": {
                "entry": _exact_count(1),
                "handler": _exact_count(1),
                "async": _exact_count(1),
                "response": _exact_count(1),
            },
            "completeness": {"status": "full", "capabilities": capabilities},
            "uncertainty_count": _exact_count(0),
        },
        {
            "id": child_flow_id,
            "entrypoint_id": entrypoint_id,
            "root_node_id": job_id,
            "kind": "async_flow",
            "represented_step_count": 1,
            "terminal_count": _exact_count(1),
            "linked_async_flow_count": _exact_count(0),
            "stage_counts": {
                "entry": _exact_count(1),
                "response": _exact_count(1),
            },
            "completeness": {
                "status": "full",
                "capabilities": copy.deepcopy(capabilities),
            },
            "uncertainty_count": _exact_count(0),
        },
    ]
    artifact["flows"].sort(key=lambda record: record["id"])
    artifact["flow_steps"] = []
    _append_flow_step(
        artifact,
        public_flow_id=sync_flow_id,
        public_edge_id=route_edge_id,
        stage_from="entry",
        stage_to="handler",
        min_depth=0,
        async_context="synchronous",
    )
    _append_flow_step(
        artifact,
        public_flow_id=sync_flow_id,
        public_edge_id=dispatch_edge_id,
        stage_from="handler",
        stage_to="async",
        min_depth=1,
        async_context="synchronous",
        child_flow_id=child_flow_id,
        backbone_role="async",
    )
    _append_flow_step(
        artifact,
        public_flow_id=sync_flow_id,
        public_edge_id=response_edge_id,
        stage_from="handler",
        stage_to="response",
        min_depth=1,
        async_context="synchronous",
    )
    _append_flow_step(
        artifact,
        public_flow_id=child_flow_id,
        public_edge_id=async_exit_edge_id,
        stage_from="entry",
        stage_to="response",
        min_depth=0,
        async_context="linked_async",
    )
    artifact["flow_steps"].sort(key=lambda record: record["id"])

    coverage = artifact["graph_contract"]["coverage"]
    coverage["entrypoints"].update(
        detected=1,
        analyzed=1,
        partial=0,
        by_kind={"http_route": 1},
    )
    coverage["records"].update(
        nodes=len(artifact["nodes"]),
        structures=0,
        edges=len(artifact["edges"]),
        flows=len(artifact["flows"]),
        flow_steps=len(artifact["flow_steps"]),
        uncertainties=0,
    )
    artifact["graph_contract"]["artifact_graph_version"] = artifact_graph_version(
        artifact
    )
    return artifact


def _rehash_artifact(artifact: dict[str, Any]) -> None:
    artifact["graph_contract"]["artifact_graph_version"] = artifact_graph_version(
        artifact
    )


def test_valid_semantic_sync_and_async_flow_fixture() -> None:
    _, _, validation = _task3_api()

    validation.validate_artifact(_valid_flow_artifact())


def _flow_by_kind(artifact: dict[str, Any], kind: str) -> dict[str, Any]:
    return next(flow for flow in artifact["flows"] if flow["kind"] == kind)


def _step_by_relation(
    artifact: dict[str, Any], relation: str, *, flow_kind: str | None = None
) -> dict[str, Any]:
    edge_ids = {
        edge["id"] for edge in artifact["edges"] if edge["relation"] == relation
    }
    flow_id_value = (
        None if flow_kind is None else _flow_by_kind(artifact, flow_kind)["id"]
    )
    return next(
        step
        for step in artifact["flow_steps"]
        if step["edge_id"] in edge_ids
        and (flow_id_value is None or step["flow_id"] == flow_id_value)
    )


def test_unknown_boundary_requires_unresolved_primary_evidence() -> None:
    _, _, validation = _task3_api()
    payload = _with_external_uncertainty()
    boundary = next(
        node for node in payload["nodes"] if node["kind"] == "unknown_boundary"
    )
    unresolved = copy.deepcopy(boundary["evidence"]["primary"])
    boundary["evidence"]["primary"]["origin"] = "verified_from_code"
    boundary["evidence"]["supporting"] = [unresolved]
    _rehash_artifact(payload)

    with pytest.raises(validation.GraphValidationError) as exc_info:
        validation.validate_artifact(payload)

    assert exc_info.value.code == "unknown_boundary_primary"


def test_file_inventory_evidence_authenticates_its_own_record() -> None:
    _, model, validation = _task3_api()
    payload = _valid_semantic_artifact()
    binding_id = payload["project"]["workspace_binding_id"]
    other_path = "src/Other.php"
    other_digest = "f" * 64
    other_identity = {
        "variant": "file",
        "workspace_binding_id": binding_id,
        "language": "php",
        "kind": "file",
        "path": other_path,
    }
    payload["nodes"].append({
        "id": node_id(other_identity),
        "identity": other_identity,
        "kind": "file",
        "language": "php",
        "framework": None,
        "name": "Other.php",
        "qualified_name": other_path,
        "namespace": None,
        "uncertainty_id": None,
        "location": None,
        "properties": {
            "file_sha256": other_digest,
            "byte_size": 0,
            "analysis_status": "analyzed",
            "omission_reason": None,
            "is_test": False,
            "is_generated": False,
        },
        "evidence": {
            "primary": {
                "origin": "verified_from_code",
                "extractor": "inventory.v2",
                "source_locator": {"kind": "file", "path": other_path},
                "source_fingerprint": file_source_fingerprint(other_digest, other_path),
                "inference_rule": None,
            },
            "supporting": [],
            "supporting_omitted_count": 0,
        },
    })
    original = next(
        node
        for node in payload["nodes"]
        if node["identity"]["variant"] == "file"
        and node["identity"]["path"] != other_path
    )
    original["evidence"]["primary"]["source_locator"]["path"] = other_path
    original["evidence"]["primary"]["source_fingerprint"] = file_source_fingerprint(
        other_digest, other_path
    )
    payload["nodes"].sort(key=lambda record: record["id"])
    artifact = model.artifact_from_payload(payload)

    with pytest.raises(validation.GraphValidationError) as exc_info:
        validation.validate_references(
            artifact, validation.build_record_index(artifact)
        )

    assert exc_info.value.code == "file_evidence_record_mismatch"


def test_incomplete_target_only_hint_respects_resolution_target_kind() -> None:
    _, _, validation = _task3_api()
    payload = _with_external_uncertainty()
    uncertainty = payload["uncertainties"][0]
    method_id = next(
        node["id"] for node in payload["nodes"] if node["kind"] == "method"
    )
    uncertainty["candidate_set_knowledge"] = "incomplete"
    uncertainty["candidate_target_node_ids"] = [method_id]
    _rehash_artifact(payload)

    with pytest.raises(validation.GraphValidationError) as exc_info:
        validation.validate_artifact(payload)

    assert exc_info.value.code == "uncertainty_target_kind"


def test_async_flow_cannot_be_orphaned_from_verified_parent_dispatch() -> None:
    _, _, validation = _task3_api()
    payload = _valid_flow_artifact()
    parent = _step_by_relation(payload, "dispatches", flow_kind="request_lifecycle")
    parent["async_child_flow_id"] = None
    _flow_by_kind(payload, "request_lifecycle")["linked_async_flow_count"] = (
        _exact_count(0)
    )
    _rehash_artifact(payload)

    with pytest.raises(validation.GraphValidationError) as exc_info:
        validation.validate_artifact(payload)

    assert exc_info.value.code == "flow_async_orphan"


def test_async_parent_link_matches_child_entrypoint_and_target() -> None:
    _, _, validation = _task3_api()
    payload = _valid_flow_artifact()
    parent = _step_by_relation(payload, "dispatches", flow_kind="request_lifecycle")
    parent["async_child_flow_id"] = _flow_by_kind(payload, "request_lifecycle")["id"]
    _rehash_artifact(payload)

    with pytest.raises(validation.GraphValidationError) as exc_info:
        validation.validate_artifact(payload)

    assert exc_info.value.code == "flow_async_parent_mismatch"


def test_async_cycle_flag_requires_child_to_be_an_ancestor() -> None:
    _, _, validation = _task3_api()
    payload = _valid_flow_artifact()
    parent = _step_by_relation(payload, "dispatches", flow_kind="request_lifecycle")
    parent["async_cycle"] = True
    _rehash_artifact(payload)

    with pytest.raises(validation.GraphValidationError) as exc_info:
        validation.validate_artifact(payload)

    assert exc_info.value.code == "flow_async_cycle"


def test_flow_min_depth_is_recomputed_from_root() -> None:
    _, _, validation = _task3_api()
    payload = _valid_flow_artifact()
    step = _step_by_relation(payload, "routes_to")
    step["min_depth"] = 999
    step["order_key"] = _order_key(
        step["stage_from"],
        step["min_depth"],
        next(
            edge["source_id"]
            for edge in payload["edges"]
            if edge["id"] == step["edge_id"]
        ),
        next(
            edge["target_id"]
            for edge in payload["edges"]
            if edge["id"] == step["edge_id"]
        ),
        step["edge_id"],
    )
    _rehash_artifact(payload)

    with pytest.raises(validation.GraphValidationError) as exc_info:
        validation.validate_artifact(payload)

    assert exc_info.value.code == "flow_min_depth"


def test_flow_order_key_is_canonical() -> None:
    _, _, validation = _task3_api()
    payload = _valid_flow_artifact()
    _step_by_relation(payload, "routes_to")["order_key"] = "bogus"
    _rehash_artifact(payload)

    with pytest.raises(validation.GraphValidationError) as exc_info:
        validation.validate_artifact(payload)

    assert exc_info.value.code == "flow_order_key"


def test_flow_stage_assignment_matches_relation_and_target() -> None:
    _, _, validation = _task3_api()
    payload = _valid_flow_artifact()
    step = _step_by_relation(payload, "routes_to")
    step["stage_to"] = "data"
    step["id"] = flow_step_id(
        step["flow_id"],
        step["edge_id"],
        step["stage_from"],
        step["stage_to"],
        step["async_context"],
    )
    _flow_by_kind(payload, "request_lifecycle")["stage_counts"]["data"] = _exact_count(
        1
    )
    payload["flow_steps"].sort(key=lambda record: record["id"])
    _rehash_artifact(payload)

    with pytest.raises(validation.GraphValidationError) as exc_info:
        validation.validate_artifact(payload)

    assert exc_info.value.code == "flow_stage_mismatch"


def test_flow_backbone_role_is_recomputed_from_topology() -> None:
    _, _, validation = _task3_api()
    payload = _valid_flow_artifact()
    _step_by_relation(payload, "routes_to")["backbone_role"] = "branch"
    _rehash_artifact(payload)

    with pytest.raises(validation.GraphValidationError) as exc_info:
        validation.validate_artifact(payload)

    assert exc_info.value.code == "flow_backbone"


def test_every_flow_step_is_reachable_from_its_flow_root() -> None:
    _, _, validation = _task3_api()
    payload = _valid_flow_artifact()
    sync_flow = _flow_by_kind(payload, "request_lifecycle")
    async_exit_step = _step_by_relation(payload, "exits_at", flow_kind="async_flow")
    _append_flow_step(
        payload,
        public_flow_id=sync_flow["id"],
        public_edge_id=async_exit_step["edge_id"],
        stage_from="entry",
        stage_to="response",
        min_depth=0,
        async_context="synchronous",
    )
    payload["flow_steps"].sort(key=lambda record: record["id"])
    sync_flow["represented_step_count"] = 4
    sync_flow["terminal_count"] = _exact_count(2)
    sync_flow["stage_counts"]["entry"] = _exact_count(2)
    sync_flow["stage_counts"]["response"] = _exact_count(2)
    payload["graph_contract"]["coverage"]["records"]["flow_steps"] = 5
    _rehash_artifact(payload)

    with pytest.raises(validation.GraphValidationError) as exc_info:
        validation.validate_artifact(payload)

    assert exc_info.value.code == "flow_reachability"


def test_entrypoint_handler_must_be_an_executable_handler_kind() -> None:
    _, _, validation = _task3_api()
    payload = _valid_flow_artifact()
    file_id = next(node["id"] for node in payload["nodes"] if node["kind"] == "file")
    payload["entrypoints"][0]["handler_node_id"] = file_id
    _rehash_artifact(payload)

    with pytest.raises(validation.GraphValidationError) as exc_info:
        validation.validate_artifact(payload)

    assert exc_info.value.code == "entrypoint_handler_kind"


def test_call_site_continuation_must_be_owned_executable_occurrence() -> None:
    _, model, validation = _task3_api()
    payload = _valid_semantic_artifact()
    owner_id = _append_executable_node(
        payload,
        kind="method",
        structural_path="declaration/method/owner",
    )
    file_id = next(node["id"] for node in payload["nodes"] if node["kind"] == "file")
    identity = {
        "kind": "call_site",
        "owner_node_id": owner_id,
        "structural_path": "body/call/0",
        "ordinal": 0,
        "subtype": "call",
    }
    payload["structures"] = [
        {
            "id": call_site_id(identity),
            **identity,
            "continuation_node_id": file_id,
            "parent_structure_id": None,
            "evidence": _producer_evidence(payload, "body/call/0"),
        }
    ]
    artifact = model.artifact_from_payload(payload)

    with pytest.raises(validation.GraphValidationError) as exc_info:
        validation.validate_references(
            artifact, validation.build_record_index(artifact)
        )

    assert exc_info.value.code == "structure_continuation_mismatch"


def test_async_flow_root_must_be_a_compatible_async_target() -> None:
    _, _, validation = _task3_api()
    payload = _valid_flow_artifact()
    child = _flow_by_kind(payload, "async_flow")
    old_flow_id = child["id"]
    child["root_node_id"] = next(
        node["id"] for node in payload["nodes"] if node["kind"] == "file"
    )
    child["id"] = flow_id(child["entrypoint_id"], child["root_node_id"], child["kind"])
    child["stage_counts"]["entry"] = _exact_count(2)
    for step in payload["flow_steps"]:
        if step["flow_id"] == old_flow_id:
            step["flow_id"] = child["id"]
            step["id"] = flow_step_id(
                step["flow_id"],
                step["edge_id"],
                step["stage_from"],
                step["stage_to"],
                step["async_context"],
            )
        if step["async_child_flow_id"] == old_flow_id:
            step["async_child_flow_id"] = child["id"]
    payload["flows"].sort(key=lambda record: record["id"])
    payload["flow_steps"].sort(key=lambda record: record["id"])
    _rehash_artifact(payload)

    with pytest.raises(validation.GraphValidationError) as exc_info:
        validation.validate_artifact(payload)

    assert exc_info.value.code == "flow_root_kind"


def test_capability_reason_count_matches_affected_records() -> None:
    _, _, validation = _task3_api()
    payload = _with_external_uncertainty()
    global_reason = payload["graph_contract"]["completeness"]["capabilities"][
        "data_access"
    ]["reasons"][0]
    language_reason = payload["graph_contract"]["completeness"]["languages"][0][
        "capabilities"
    ]["data_access"]["reasons"][0]
    global_reason["count"] = 999
    language_reason["count"] = 999
    _rehash_artifact(payload)

    with pytest.raises(validation.GraphValidationError) as exc_info:
        validation.validate_artifact(payload)

    assert exc_info.value.code == "capability_reason_count_mismatch"


def test_global_and_language_capability_reason_counts_reconcile() -> None:
    _, _, validation = _task3_api()
    payload = json.loads(json.dumps(_with_external_uncertainty()))
    payload["graph_contract"]["completeness"]["capabilities"]["data_access"]["reasons"][
        0
    ]["count"] = 2
    _rehash_artifact(payload)

    with pytest.raises(validation.GraphValidationError) as exc_info:
        validation.validate_artifact(payload)

    assert exc_info.value.code == "capability_reason_scope_mismatch"
