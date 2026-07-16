from __future__ import annotations

import copy
import hashlib
import json
import unicodedata
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, ValidationError
from referencing import Registry, Resource


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
SAFE_INTEGER_MAX = 9_007_199_254_740_991


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
        registry = registry.with_resource(document["$id"], Resource.from_contents(document))
    return registry


def validate_contract(name: str, instance: Any) -> None:
    Draft202012Validator(
        _contract_document(name), registry=_contract_registry()
    ).validate(instance)


def _canonical_fragment(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        if not 0 <= value <= SAFE_INTEGER_MAX:
            raise ValueError("integer outside the graph-v2 interoperable range")
        return str(value)
    if isinstance(value, float):
        raise TypeError("graph-v2 canonical JSON rejects floats")
    if isinstance(value, str):
        normalized = unicodedata.normalize("NFC", value)
        return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, list):
        return "[" + ",".join(_canonical_fragment(item) for item in value) + "]"
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("canonical JSON object keys must be strings")
            nfc_key = unicodedata.normalize("NFC", key)
            if nfc_key in normalized:
                raise ValueError("NFC-normalized key collision")
            normalized[nfc_key] = item
        keys = sorted(normalized, key=lambda item: item.encode("utf-16-be"))
        return "{" + ",".join(
            f"{_canonical_fragment(key)}:{_canonical_fragment(normalized[key])}"
            for key in keys
        ) + "}"
    raise TypeError(f"unsupported canonical JSON value: {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    return _canonical_fragment(value).encode("utf-8")


def _normalize_source_path(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value.replace("\\", "/"))
    parts = normalized.split("/")
    if not normalized or normalized.startswith("/") or any(
        part in {"", ".", ".."} for part in parts
    ):
        raise ValueError("unsafe source path")
    return normalized


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
        assert next(
            row["sha256"] for row in manifest["files"] if row["path"] == name
        ) == digest


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


def test_bundle_and_each_chunk_discriminator_validate(valid_artifact: dict[str, Any]) -> None:
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
        assert overlay["evidence_digest"] == hashlib.sha256(
            canonical_json_bytes(result["evidence"])
        ).hexdigest()

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
        assert item["deduplication_key"] == hashlib.sha256(
            canonical_json_bytes(preimage)
        ).hexdigest()


def test_canonicalization_vectors_have_exact_bytes_and_digests() -> None:
    vectors = json.loads(
        (CONTRACT_ROOT / "golden" / "canonicalization.json").read_text(encoding="utf-8")
    )
    assert vectors["schema"] == "hades.graph_v2_canonicalization_golden.v1"
    assert {vector["kind"] for vector in vectors["vectors"]} >= {
        "unicode_nfc",
        "safe_integer",
        "node_id",
        "artifact_digest",
        "verification_dedupe",
        "result_digest",
    }
    for vector in vectors["vectors"]:
        canonical = canonical_json_bytes(vector["input"])
        assert canonical.hex() == vector["canonical_utf8_hex"]
        assert hashlib.sha256(canonical).hexdigest() == vector["sha256"]
    for vector in vectors["path_vectors"]:
        assert _normalize_source_path(vector["input"]) == vector["normalized"]
        assert vector["normalized"].encode("utf-8").hex() == vector["normalized_utf8_hex"]
    for vector in vectors["projection_versions"]:
        preimage = bytes.fromhex(vector["preimage_utf8_hex"])
        assert preimage == (
            vector["artifact_graph_version"]
            + ":"
            + vector["verification_set_hash"]
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
