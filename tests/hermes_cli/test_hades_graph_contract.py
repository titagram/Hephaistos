from __future__ import annotations

import copy
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
