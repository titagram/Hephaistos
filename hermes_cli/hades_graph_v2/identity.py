"""RFC 8785 canonical bytes and graph v2 identity/digest helpers."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime
from typing import Any, Mapping, Sequence, cast

from .schema import (
    GRAPH_CONTRACT_VERSION,
    GRAPH_SCHEMA,
    SAFE_INTEGER_MAX,
    GraphContractError,
    JsonValue,
)


NODE_PREFIX = "hades:node:v2:"
EDGE_PREFIX = "hades:edge:v2:"
FLOW_PREFIX = "hades:flow:v2:"
FLOW_STEP_PREFIX = "hades:flow-step:v2:"
BRANCH_PREFIX = "hades:branch:v2:"
CALL_SITE_PREFIX = "hades:call-site:v2:"
EXCEPTION_SCOPE_PREFIX = "hades:exception-scope:v2:"
UNCERTAINTY_PREFIX = "hades:uncertainty:v2:"

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_UTC_TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])"
    r"T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]Z$"
)
_FLOW_KINDS = frozenset({"request_lifecycle", "execution_flow", "async_flow"})
_STAGES = frozenset({
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
})
_ASYNC_CONTEXTS = frozenset({"synchronous", "linked_async"})
_NODE_IDENTITY_KEYS = {
    "source_declaration": frozenset({
        "variant",
        "workspace_binding_id",
        "language",
        "kind",
        "namespace",
        "qualified_name",
        "path",
    }),
    "file": frozenset({"variant", "workspace_binding_id", "language", "kind", "path"}),
    "source_occurrence": frozenset({
        "variant",
        "workspace_binding_id",
        "language",
        "kind",
        "owner_node_id",
        "structural_path",
        "ordinal",
        "semantic_role",
    }),
    "anonymous_callable": frozenset({
        "variant",
        "workspace_binding_id",
        "language",
        "kind",
        "owner_node_id",
        "structural_path",
        "ordinal",
    }),
    "entrypoint": frozenset({
        "variant",
        "workspace_binding_id",
        "language",
        "kind",
        "path",
        "entrypoint_identity",
    }),
    "semantic_resource": frozenset({
        "variant",
        "workspace_binding_id",
        "language",
        "kind",
        "framework",
        "namespace",
        "qualified_name",
        "public_resource_name",
        "protocol",
        "operation",
    }),
}
_EDGE_IDENTITY_KEYS = frozenset({
    "source_id",
    "target_id",
    "relation",
    "flow",
    "condition_hash",
    "branch_group_id",
    "call_site_id",
    "exception_scope_id",
    "occurrence",
})
_ARTIFACT_SEMANTIC_KEYS = (
    "schema",
    "project",
    "source",
    "graph_contract_version",
    "frameworks",
    "languages",
    "entrypoints",
    "nodes",
    "structures",
    "edges",
    "flows",
    "flow_steps",
    "uncertainties",
    "completeness",
    "coverage",
)
_ARTIFACT_RECORD_ARRAYS = (
    "frameworks",
    "languages",
    "entrypoints",
    "nodes",
    "structures",
    "edges",
    "flows",
    "flow_steps",
    "uncertainties",
)


def _has_isolated_surrogate(value: str) -> bool:
    return any(0xD800 <= ord(character) <= 0xDFFF for character in value)


def _normalized_string(value: str) -> str:
    if _has_isolated_surrogate(value):
        raise GraphContractError(
            "isolated_surrogate",
            "contract strings must contain Unicode scalar values",
        )
    return unicodedata.normalize("NFC", value)


def _has_forbidden_path_control(value: str) -> bool:
    return any(unicodedata.category(character) in {"Cc", "Cf"} for character in value)


def normalize_source_path(value: str) -> str:
    """Normalize one source-relative path to the graph v2 POSIX form."""

    if not isinstance(value, str):
        raise GraphContractError("unsafe_source_path", "source path must be a string")
    normalized = _normalized_string(value.replace("\\", "/"))
    parts = normalized.split("/")
    if (
        not normalized
        or normalized.startswith("/")
        or _WINDOWS_DRIVE_RE.match(normalized)
        or len(normalized.encode("utf-8")) > 4096
        or _has_forbidden_path_control(normalized)
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise GraphContractError(
            "unsafe_source_path",
            "source path is not a safe relative POSIX path",
        )
    return normalized


def normalize_structural_path(value: str) -> str:
    """Normalize one AST/config structural path without source locations."""

    if not isinstance(value, str):
        raise GraphContractError(
            "unsafe_structural_path",
            "structural path must be a string",
        )
    normalized = _normalized_string(value.replace("\\", "/"))
    parts = normalized.split("/")
    if (
        not normalized
        or normalized.startswith("/")
        or len(normalized.encode("utf-8")) > 1024
        or _has_forbidden_path_control(normalized)
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise GraphContractError(
            "unsafe_structural_path",
            "structural path is not normalized",
        )
    return normalized


def require_utc_timestamp(value: str) -> str:
    """Require an RFC 3339 UTC timestamp with whole seconds and ``Z``."""

    if not isinstance(value, str) or not _UTC_TIMESTAMP_RE.fullmatch(value):
        raise GraphContractError(
            "non_utc_timestamp",
            "contract timestamps must use RFC 3339 UTC whole seconds with Z",
        )
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise GraphContractError(
            "non_utc_timestamp",
            "contract timestamp is not a valid UTC calendar instant",
        ) from exc
    return value


def normalize_contract_value(value: JsonValue, *, _key: str | None = None) -> JsonValue:
    """Normalize strings, source paths, and structural paths before hashing."""

    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        if abs(value) > SAFE_INTEGER_MAX:
            raise GraphContractError(
                "unsafe_integer",
                "contract integer is outside the interoperable safe range",
            )
        return value
    if isinstance(value, float):
        raise GraphContractError("float_not_allowed", "contract JSON forbids floats")
    if isinstance(value, str):
        normalized = _normalized_string(value)
        if _key == "path":
            return normalize_source_path(normalized)
        if _key in {"structural_path", "ast_path", "structural_pointer"}:
            return normalize_structural_path(normalized)
        if _key is not None and _key.endswith("_at"):
            return require_utc_timestamp(normalized)
        return normalized
    if isinstance(value, list):
        return [normalize_contract_value(item) for item in value]
    if isinstance(value, dict):
        normalized: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise GraphContractError(
                    "non_string_object_key",
                    "contract JSON object keys must be strings",
                )
            normalized_key = _normalized_string(key)
            if normalized_key in normalized:
                raise GraphContractError(
                    "normalized_key_collision",
                    "object keys collide after Unicode NFC normalization",
                )
            normalized[normalized_key] = normalize_contract_value(
                item,
                _key=normalized_key,
            )
        return normalized
    raise GraphContractError(
        "unsupported_json_type",
        "contract value contains a non-JSON type",
    )


def _canonical_fragment(value: JsonValue) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        if abs(value) > SAFE_INTEGER_MAX:
            raise GraphContractError(
                "unsafe_integer",
                "contract integer is outside the interoperable safe range",
            )
        return str(value)
    if isinstance(value, float):
        raise GraphContractError("float_not_allowed", "contract JSON forbids floats")
    if isinstance(value, str):
        return json.dumps(
            _normalized_string(value),
            ensure_ascii=False,
            separators=(",", ":"),
        )
    if isinstance(value, list):
        return "[" + ",".join(_canonical_fragment(item) for item in value) + "]"
    if isinstance(value, dict):
        normalized = normalize_contract_value(value)
        if not isinstance(normalized, dict):
            raise AssertionError("normalized object changed type")
        keys = sorted(normalized, key=lambda key: key.encode("utf-16-be"))
        return (
            "{"
            + ",".join(
                f"{_canonical_fragment(key)}:{_canonical_fragment(normalized[key])}"
                for key in keys
            )
            + "}"
        )
    raise GraphContractError(
        "unsupported_json_type",
        "contract value contains a non-JSON type",
    )


def canonical_json_bytes(value: JsonValue) -> bytes:
    """Return RFC 8785 bytes for the graph contract's float-free safe subset."""

    return _canonical_fragment(value).encode("utf-8")


def sha256_jcs(value: JsonValue) -> str:
    """Hash a fully normalized graph-contract value as canonical JSON."""

    return hashlib.sha256(
        canonical_json_bytes(normalize_contract_value(value))
    ).hexdigest()


def _require_digest(value: str) -> str:
    if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
        raise GraphContractError(
            "invalid_digest",
            "digest must be exactly 64 lower-case hexadecimal characters",
        )
    return value


def _require_prefixed_id(value: str, prefix: str) -> str:
    if not isinstance(value, str) or not value.startswith(prefix):
        raise GraphContractError("invalid_public_id", "public ID has the wrong prefix")
    _require_digest(value.removeprefix(prefix))
    return value


def _require_mapping(value: Mapping[str, Any], *, label: str) -> dict[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise GraphContractError("invalid_identity", f"{label} must be an object")
    return cast(dict[str, JsonValue], dict(value))


def _require_exact_keys(
    value: Mapping[str, JsonValue],
    expected: frozenset[str],
    *,
    label: str,
) -> None:
    if frozenset(value) != expected:
        raise GraphContractError(
            "invalid_identity",
            f"{label} must contain its exact closed identity fields",
        )


def prefixed_id(prefix: str, identity: JsonValue) -> str:
    if not isinstance(prefix, str) or not prefix.isascii() or not prefix:
        raise GraphContractError(
            "invalid_id_prefix", "ID prefix must be non-empty ASCII"
        )
    return prefix + sha256_jcs(normalize_contract_value(identity))


def node_id(identity: Mapping[str, Any]) -> str:
    value = _require_mapping(identity, label="node identity")
    variant = value.get("variant")
    expected = _NODE_IDENTITY_KEYS.get(cast(str, variant))
    if expected is None:
        raise GraphContractError("invalid_identity", "unknown node identity variant")
    _require_exact_keys(value, expected, label="node identity")
    return prefixed_id(NODE_PREFIX, value)


def edge_id(identity: Mapping[str, Any]) -> str:
    value = _require_mapping(identity, label="edge identity")
    _require_exact_keys(value, _EDGE_IDENTITY_KEYS, label="edge identity")
    _require_prefixed_id(cast(str, value["source_id"]), NODE_PREFIX)
    _require_prefixed_id(cast(str, value["target_id"]), NODE_PREFIX)
    return prefixed_id(EDGE_PREFIX, value)


def flow_id(entrypoint_id: str, root_node_id: str, kind: str) -> str:
    _require_prefixed_id(entrypoint_id, NODE_PREFIX)
    _require_prefixed_id(root_node_id, NODE_PREFIX)
    if kind not in _FLOW_KINDS:
        raise GraphContractError("invalid_flow_kind", "flow kind is not recognized")
    return prefixed_id(
        FLOW_PREFIX,
        {
            "entrypoint_id": entrypoint_id,
            "root_node_id": root_node_id,
            "kind": kind,
        },
    )


def flow_step_id(
    flow_id: str,
    edge_id: str,
    stage_from: str,
    stage_to: str,
    async_context: str,
) -> str:
    _require_prefixed_id(flow_id, FLOW_PREFIX)
    _require_prefixed_id(edge_id, EDGE_PREFIX)
    if stage_from not in _STAGES or stage_to not in _STAGES:
        raise GraphContractError("invalid_stage", "flow step stage is not recognized")
    if async_context not in _ASYNC_CONTEXTS:
        raise GraphContractError(
            "invalid_async_context",
            "flow step async context is not recognized",
        )
    return prefixed_id(
        FLOW_STEP_PREFIX,
        {
            "flow_id": flow_id,
            "edge_id": edge_id,
            "stage_from": stage_from,
            "stage_to": stage_to,
            "async_context": async_context,
        },
    )


def _structure_id(
    identity: Mapping[str, Any],
    *,
    kind: str,
    prefix: str,
) -> str:
    value = _require_mapping(identity, label=f"{kind} identity")
    expected = frozenset({
        "kind",
        "owner_node_id",
        "structural_path",
        "ordinal",
        "subtype",
    })
    _require_exact_keys(value, expected, label=f"{kind} identity")
    if value["kind"] != kind:
        raise GraphContractError("invalid_identity", "structure identity kind is wrong")
    _require_prefixed_id(cast(str, value["owner_node_id"]), NODE_PREFIX)
    return prefixed_id(prefix, value)


def call_site_id(identity: Mapping[str, Any]) -> str:
    return _structure_id(identity, kind="call_site", prefix=CALL_SITE_PREFIX)


def branch_group_id(identity: Mapping[str, Any]) -> str:
    return _structure_id(identity, kind="branch_group", prefix=BRANCH_PREFIX)


def exception_scope_id(identity: Mapping[str, Any]) -> str:
    return _structure_id(
        identity,
        kind="exception_scope",
        prefix=EXCEPTION_SCOPE_PREFIX,
    )


def condition_hash(normalized_full: str) -> str:
    if not isinstance(normalized_full, str) or not normalized_full:
        raise GraphContractError(
            "invalid_condition",
            "condition preimage must be a non-empty redacted string",
        )
    return sha256_jcs({"normalized_full": normalized_full})


def ast_source_fingerprint(
    file_sha256: str,
    path: str,
    structural_path: str,
) -> str:
    return sha256_jcs({
        "file_sha256": _require_digest(file_sha256),
        "occurrence_kind": "ast",
        "path": normalize_source_path(path),
        "structural_path": normalize_structural_path(structural_path),
    })


def config_source_fingerprint(
    file_sha256: str,
    path: str,
    structural_pointer: str,
) -> str:
    return sha256_jcs({
        "file_sha256": _require_digest(file_sha256),
        "occurrence_kind": "config",
        "path": normalize_source_path(path),
        "structural_pointer": normalize_structural_path(structural_pointer),
    })


def file_source_fingerprint(file_sha256: str, path: str) -> str:
    return sha256_jcs({
        "file_sha256": _require_digest(file_sha256),
        "occurrence_kind": "file",
        "path": normalize_source_path(path),
    })


def uncertainty_fingerprint(identity: Mapping[str, Any]) -> str:
    value = _require_mapping(identity, label="uncertainty identity")
    expected = frozenset({
        "domain",
        "project_id",
        "workspace_binding_id",
        "subject",
        "resolution_kind",
        "reason_code",
        "question",
    })
    _require_exact_keys(value, expected, label="uncertainty identity")
    if value["domain"] != "graph":
        raise GraphContractError("invalid_identity", "uncertainty domain must be graph")
    return sha256_jcs(value)


def uncertainty_id(identity: Mapping[str, Any]) -> str:
    return UNCERTAINTY_PREFIX + uncertainty_fingerprint(identity)


def evidence_digest(evidence: Sequence[Mapping[str, Any]]) -> str:
    from .canonicalize import canonicalize_verification_evidence

    return sha256_jcs(canonicalize_verification_evidence(evidence))


def verification_set_hash(active_overlays: Sequence[Mapping[str, Any]]) -> str:
    from .canonicalize import canonicalize_verification_set

    return sha256_jcs(canonicalize_verification_set(active_overlays))


def verification_deduplication_key(value: Mapping[str, Any]) -> str:
    item = _require_mapping(value, label="verification deduplication preimage")
    if "target_id" in item:
        preimage = item
    else:
        target = item.get("target")
        source_snapshot = item.get("source_snapshot")
        if not isinstance(target, Mapping):
            raise GraphContractError(
                "invalid_verification_preimage",
                "verification target is required",
            )
        common: dict[str, JsonValue] = {
            "kind": cast(JsonValue, item.get("kind")),
            "project_id": cast(JsonValue, item.get("project_id")),
            "workspace_binding_id": cast(JsonValue, item.get("workspace_binding_id")),
            "target_id": cast(JsonValue, target.get("id")),
            "target_version": cast(JsonValue, target.get("version")),
        }
        if item.get("domain") == "graph":
            assertion = item.get("assertion")
            if not isinstance(assertion, Mapping):
                raise GraphContractError(
                    "invalid_verification_preimage",
                    "graph verification assertion is required",
                )
            common["assertion_fingerprint"] = cast(
                JsonValue,
                assertion.get("fingerprint"),
            )
        else:
            if not isinstance(source_snapshot, Mapping):
                raise GraphContractError(
                    "invalid_verification_preimage",
                    "Wiki source snapshot is required",
                )
            common.update({
                "source_state": cast(JsonValue, source_snapshot.get("state")),
                "artifact_graph_version": cast(
                    JsonValue,
                    source_snapshot.get("artifact_graph_version"),
                ),
                "source_tree_sha256": cast(
                    JsonValue,
                    source_snapshot.get("tree_sha256"),
                ),
            })
        common["attempt_generation"] = cast(
            JsonValue,
            item.get("attempt_generation"),
        )
        preimage = common
    return sha256_jcs(cast(JsonValue, preimage))


def result_digest(result: Mapping[str, Any]) -> str:
    from .canonicalize import canonicalize_verification_evidence

    value = _require_mapping(result, label="verification result")
    evidence = value.get("evidence")
    if not isinstance(evidence, list):
        raise GraphContractError(
            "invalid_verification_result",
            "verification result evidence must be an array",
        )
    value["evidence"] = canonicalize_verification_evidence(evidence)
    return sha256_jcs(value)


def _canonical_artifact_arrays(
    preimage: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    from .canonicalize import canonicalize_records

    result = dict(preimage)
    for key in _ARTIFACT_RECORD_ARRAYS:
        records = result[key]
        if not isinstance(records, list):
            raise GraphContractError(
                "invalid_artifact_preimage",
                "artifact semantic record collections must be arrays",
            )
        result[key] = canonicalize_records(records)
    return result


def artifact_digest(semantic_preimage: Mapping[str, Any]) -> str:
    value = _require_mapping(semantic_preimage, label="artifact semantic preimage")
    if tuple(value) != _ARTIFACT_SEMANTIC_KEYS and set(value) != set(
        _ARTIFACT_SEMANTIC_KEYS
    ):
        raise GraphContractError(
            "invalid_artifact_preimage",
            "artifact semantic preimage must contain exactly the normative keys",
        )
    if value["schema"] != GRAPH_SCHEMA:
        if value["schema"] in {"hades.code_graph.v1", "hades.php_graph.v1"}:
            raise GraphContractError(
                "graph_v1_not_supported",
                "graph v1 is not accepted by the graph v2 identity helpers",
            )
        raise GraphContractError(
            "invalid_artifact_preimage",
            "artifact semantic preimage has the wrong schema",
        )
    if value["graph_contract_version"] != GRAPH_CONTRACT_VERSION:
        raise GraphContractError(
            "invalid_artifact_preimage",
            "artifact semantic preimage has the wrong contract version",
        )
    return sha256_jcs(_canonical_artifact_arrays(value))


def artifact_semantic_preimage(artifact: Mapping[str, Any]) -> dict[str, JsonValue]:
    value = _require_mapping(artifact, label="graph artifact")
    if value.get("schema") in {"hades.code_graph.v1", "hades.php_graph.v1"}:
        raise GraphContractError(
            "graph_v1_not_supported",
            "graph v1 is not accepted by the graph v2 identity helpers",
        )
    graph_contract = value.get("graph_contract")
    if not isinstance(graph_contract, Mapping):
        raise GraphContractError(
            "invalid_artifact_preimage",
            "graph artifact contract metadata is required",
        )
    try:
        preimage: dict[str, JsonValue] = {
            "schema": cast(JsonValue, value["schema"]),
            "project": cast(JsonValue, value["project"]),
            "source": cast(JsonValue, value["source"]),
            "graph_contract_version": cast(JsonValue, graph_contract["version"]),
            "frameworks": cast(JsonValue, value["frameworks"]),
            "languages": cast(JsonValue, value["languages"]),
            "entrypoints": cast(JsonValue, value["entrypoints"]),
            "nodes": cast(JsonValue, value["nodes"]),
            "structures": cast(JsonValue, value["structures"]),
            "edges": cast(JsonValue, value["edges"]),
            "flows": cast(JsonValue, value["flows"]),
            "flow_steps": cast(JsonValue, value["flow_steps"]),
            "uncertainties": cast(JsonValue, value["uncertainties"]),
            "completeness": cast(JsonValue, graph_contract["completeness"]),
            "coverage": cast(JsonValue, graph_contract["coverage"]),
        }
    except KeyError as exc:
        raise GraphContractError(
            "invalid_artifact_preimage",
            "graph artifact is missing a semantic field",
        ) from exc
    return _canonical_artifact_arrays(preimage)


def artifact_graph_version(artifact: Mapping[str, Any]) -> str:
    return artifact_digest(artifact_semantic_preimage(artifact))


def projection_version(artifact_digest: str, verification_set_hash: str) -> str:
    artifact = _require_digest(artifact_digest)
    verification = _require_digest(verification_set_hash)
    return hashlib.sha256(f"{artifact}:{verification}".encode("ascii")).hexdigest()


__all__ = [
    "BRANCH_PREFIX",
    "CALL_SITE_PREFIX",
    "EDGE_PREFIX",
    "EXCEPTION_SCOPE_PREFIX",
    "FLOW_PREFIX",
    "FLOW_STEP_PREFIX",
    "NODE_PREFIX",
    "UNCERTAINTY_PREFIX",
    "artifact_digest",
    "artifact_graph_version",
    "artifact_semantic_preimage",
    "ast_source_fingerprint",
    "branch_group_id",
    "call_site_id",
    "canonical_json_bytes",
    "condition_hash",
    "config_source_fingerprint",
    "edge_id",
    "evidence_digest",
    "exception_scope_id",
    "file_source_fingerprint",
    "flow_id",
    "flow_step_id",
    "node_id",
    "normalize_contract_value",
    "normalize_source_path",
    "normalize_structural_path",
    "prefixed_id",
    "projection_version",
    "require_utc_timestamp",
    "result_digest",
    "sha256_jcs",
    "uncertainty_fingerprint",
    "uncertainty_id",
    "verification_deduplication_key",
    "verification_set_hash",
]
