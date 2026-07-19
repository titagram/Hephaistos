"""Graph v2 schema registry and strict JSON wire boundary."""

from __future__ import annotations

import json
import unicodedata
from functools import lru_cache
from importlib.resources import files
from typing import Any, TypeAlias

from jsonschema import Draft202012Validator, FormatChecker, ValidationError, validators
from referencing import Registry, Resource


JsonScalar: TypeAlias = None | bool | int | str
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


def _freeze_unique_item(value: object) -> tuple[object, ...]:
    """Make JSON equality hashable with jsonschema's scalar distinctions."""

    if value is None:
        return ("null",)
    if isinstance(value, bool):
        return ("boolean", value)
    if isinstance(value, (int, float)):
        # jsonschema treats 1 and 1.0 as equal, but never equates bool with int.
        return ("number", value)
    if isinstance(value, str):
        return ("string", value)
    if isinstance(value, list):
        return ("array", tuple(_freeze_unique_item(item) for item in value))
    if isinstance(value, dict):
        return (
            "object",
            tuple(
                sorted(
                    (key, _freeze_unique_item(item))
                    for key, item in value.items()
                )
            ),
        )
    raise TypeError("uniqueItems values must be JSON-compatible")


def _linear_unique_items(validator, unique_items, instance, schema):
    """Validate JSON-array uniqueness in linear time without changing semantics."""

    del validator, schema
    if not unique_items or not isinstance(instance, list):
        return
    seen: set[tuple[object, ...]] = set()
    for item in instance:
        frozen_item = _freeze_unique_item(item)
        if frozen_item in seen:
            yield ValidationError(f"{instance!r} has non-unique elements")
            return
        seen.add(frozen_item)


_LinearDraft202012Validator = validators.extend(
    Draft202012Validator,
    {"uniqueItems": _linear_unique_items},
)

GRAPH_SCHEMA = "hades.code_graph.v2"
GRAPH_CONTRACT_VERSION = "hades.graph_artifact.v2"
BUNDLE_SCHEMA = "hades.graph_bundle.v2"
CHUNK_SCHEMA = "hades.graph_chunk.v2"

SCHEMA_NAMES = frozenset({
    "artifact.schema.json",
    "bundle.schema.json",
    "chunk.schema.json",
    "dashboard-query.schema.json",
    "dashboard-response.schema.json",
    "verification-work.schema.json",
    "verification-result.schema.json",
    "graph-overlay.schema.json",
})

SAFE_INTEGER_MAX = 9_007_199_254_740_991
_GRAPH_V1_SCHEMAS = frozenset({
    "hades.code_graph.v1",
    "hades.php_graph.v1",
})
_GRAPH_V1_DOCUMENT_NAMES = frozenset({"artifact-v1.schema.json"})
_SOURCE_PATH_KEYS = frozenset({"path"})
_SOURCE_PATH_ARRAY_KEYS = frozenset({"configuration_paths", "paths_sample"})
_INCLUDED_ROOT_ARRAY_KEYS = frozenset({"included_roots"})
_STRUCTURAL_PATH_KEYS = frozenset({
    "ast_path",
    "structural_path",
    "structural_pointer",
})
_UTC_TIMESTAMP_KEYS = frozenset({
    "active_projection_generated_at",
    "exits_at",
    "generated_at",
    "merges_at",
})


class GraphContractError(ValueError):
    """A safe, typed graph-contract boundary failure."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class GraphIdentityCollision(GraphContractError):
    """Raised when one public identity is bound to different canonical values."""

    def __init__(self, public_id: str) -> None:
        self.public_id = public_id
        super().__init__(
            "identity_collision",
            "same public ID has different canonical values",
        )


def _has_isolated_surrogate(value: str) -> bool:
    return any(0xD800 <= ord(character) <= 0xDFFF for character in value)


def _is_graph_v1_payload(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("schema") in _GRAPH_V1_SCHEMAS:
        return True
    graph_contract = payload.get("graph_contract")
    return bool(
        isinstance(graph_contract, dict)
        and graph_contract.get("version") == "hades.graph_artifact.v1"
    )


def _reject_v1(document_name: str, payload: object) -> None:
    if document_name in _GRAPH_V1_DOCUMENT_NAMES or _is_graph_v1_payload(payload):
        raise GraphContractError(
            "graph_v1_not_supported",
            "graph v1 is not accepted by the graph v2 contract facade",
        )


def _strict_object(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise GraphContractError(
                "duplicate_json_key",
                "contract JSON contains a duplicate object key",
            )
        result[key] = value
    return result


def _strict_integer(raw: str) -> int:
    value = int(raw)
    if abs(value) > SAFE_INTEGER_MAX:
        raise GraphContractError(
            "unsafe_integer",
            "contract JSON integer is outside the interoperable safe range",
        )
    return value


def _reject_float(_raw: str) -> Any:
    raise GraphContractError("float_not_allowed", "contract JSON forbids floats")


def load_json_bytes(raw: bytes | bytearray | memoryview) -> JsonValue:
    """Decode strict graph-contract JSON without losing lexical float intent."""

    if not isinstance(raw, (bytes, bytearray, memoryview)):
        raise GraphContractError("invalid_json", "contract JSON input must be bytes")
    try:
        decoded = bytes(raw).decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise GraphContractError(
            "invalid_json",
            "contract JSON must be valid UTF-8",
        ) from exc
    if decoded.startswith("\ufeff"):
        raise GraphContractError("invalid_json", "contract JSON must not contain a BOM")
    try:
        return json.loads(
            decoded,
            parse_float=_reject_float,
            parse_int=_strict_integer,
            parse_constant=_reject_float,
            object_pairs_hook=_strict_object,
        )
    except GraphContractError:
        raise
    except (json.JSONDecodeError, RecursionError) as exc:
        raise GraphContractError("invalid_json", "contract JSON is malformed") from exc


@lru_cache(maxsize=1)
def _schema_documents() -> dict[str, dict[str, Any]]:
    documents: dict[str, dict[str, Any]] = {}
    contract_root = files("hermes_cli.hades_graph_v2").joinpath("contracts")
    for name in sorted(SCHEMA_NAMES):
        try:
            document = json.loads(
                contract_root.joinpath(name).read_text(encoding="utf-8")
            )
            Draft202012Validator.check_schema(document)
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            raise GraphContractError(
                "schema_registry_invalid",
                "the graph v2 schema registry could not be loaded",
            ) from exc
        documents[name] = document
    return documents


@lru_cache(maxsize=1)
def _schema_registry() -> Registry:
    registry = Registry()
    for document in _schema_documents().values():
        registry = registry.with_resource(
            document["$id"],
            Resource.from_contents(document),
        )
    return registry


@lru_cache(maxsize=None)
def _validator(document_name: str) -> Draft202012Validator:
    document = _schema_documents()[document_name]
    return _LinearDraft202012Validator(
        document,
        registry=_schema_registry(),
        format_checker=FormatChecker(),
    )


def _validate_application_scalars(value: object, *, key: str | None = None) -> None:
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        if abs(value) > SAFE_INTEGER_MAX:
            raise GraphContractError(
                "unsafe_integer",
                "contract integer is outside the interoperable safe range",
            )
        return
    if isinstance(value, float):
        raise GraphContractError("float_not_allowed", "contract JSON forbids floats")
    if isinstance(value, str):
        if _has_isolated_surrogate(value):
            raise GraphContractError(
                "isolated_surrogate",
                "contract strings must contain Unicode scalar values",
            )
        if unicodedata.normalize("NFC", value) != value:
            raise GraphContractError(
                "non_nfc_string",
                "contract strings and object keys must be Unicode NFC",
            )
        if key == "namespace" and len(value.encode("utf-8")) > 512:
            raise GraphContractError(
                "string_byte_limit_exceeded",
                "contract string exceeds its UTF-8 byte limit",
            )
        if key in _SOURCE_PATH_KEYS or key in _SOURCE_PATH_ARRAY_KEYS:
            from .identity import normalize_source_path

            if normalize_source_path(value) != value:
                raise GraphContractError(
                    "unsafe_source_path",
                    "source paths must already be normalized NFC POSIX paths",
                )
        if key in _INCLUDED_ROOT_ARRAY_KEYS and value != ".":
            from .identity import normalize_source_path

            if normalize_source_path(value) != value:
                raise GraphContractError(
                    "unsafe_source_path",
                    "included roots must be dot or normalized NFC POSIX paths",
                )
        if key in _STRUCTURAL_PATH_KEYS:
            from .identity import normalize_structural_path

            if normalize_structural_path(value) != value:
                raise GraphContractError(
                    "unsafe_structural_path",
                    "structural paths must already be normalized",
                )
        if key in _UTC_TIMESTAMP_KEYS:
            from .identity import require_utc_timestamp

            try:
                require_utc_timestamp(value)
            except GraphContractError as exc:
                raise GraphContractError(
                    "schema_validation_failed",
                    "payload does not satisfy the selected graph v2 schema",
                ) from exc
        return
    if isinstance(value, list):
        item_key = (
            key
            if key in _SOURCE_PATH_ARRAY_KEYS or key in _INCLUDED_ROOT_ARRAY_KEYS
            else None
        )
        for item in value:
            _validate_application_scalars(item, key=item_key)
        return
    if isinstance(value, dict):
        for child_key, item in value.items():
            if not isinstance(child_key, str):
                raise GraphContractError(
                    "non_string_object_key",
                    "contract JSON object keys must be strings",
                )
            _validate_application_scalars(child_key)
            _validate_application_scalars(item, key=child_key)
        return
    raise GraphContractError(
        "unsupported_json_type",
        "contract value contains a non-JSON type",
    )


def validate_schema(document_name: str, payload: JsonValue) -> None:
    """Validate one payload through the closed eight-schema graph v2 registry."""

    _reject_v1(document_name, payload)
    if document_name not in SCHEMA_NAMES:
        raise GraphContractError(
            "unknown_schema_name",
            "the requested graph v2 schema is not registered",
        )
    _validate_application_scalars(payload)
    try:
        _validator(document_name).validate(payload)
    except ValidationError as exc:
        raise GraphContractError(
            "schema_validation_failed",
            "payload does not satisfy the selected graph v2 schema",
        ) from exc


def validate_json_bytes(document_name: str, raw: bytes) -> JsonValue:
    """Strictly decode raw JSON, then validate it against one v2 schema."""

    _reject_v1(document_name, {})
    payload = load_json_bytes(raw)
    validate_schema(document_name, payload)
    return payload


__all__ = [
    "BUNDLE_SCHEMA",
    "CHUNK_SCHEMA",
    "GRAPH_CONTRACT_VERSION",
    "GRAPH_SCHEMA",
    "JsonScalar",
    "JsonValue",
    "GraphContractError",
    "GraphIdentityCollision",
    "SAFE_INTEGER_MAX",
    "SCHEMA_NAMES",
    "load_json_bytes",
    "validate_json_bytes",
    "validate_schema",
]
