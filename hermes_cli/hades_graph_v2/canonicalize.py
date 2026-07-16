"""Deterministic graph v2 record canonicalization and collision detection."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, cast

from .identity import canonical_json_bytes, normalize_contract_value
from .schema import GraphContractError, GraphIdentityCollision, JsonValue


_VERIFICATION_MEMBER_KEYS = frozenset({
    "artifact_graph_version",
    "assertion_fingerprint",
    "verdict",
    "overlay",
    "evidence",
})


def _normalized_record(record: Mapping[str, Any]) -> dict[str, JsonValue]:
    if not isinstance(record, Mapping):
        raise GraphContractError(
            "invalid_record",
            "canonical record collections may contain only objects",
        )
    normalized = normalize_contract_value(cast(JsonValue, dict(record)))
    if not isinstance(normalized, dict):
        raise AssertionError("normalized record changed type")
    return normalized


def canonicalize_records(
    records: Iterable[Mapping[str, Any]],
) -> list[dict[str, JsonValue]]:
    """Normalize, deduplicate, collision-check, and byte-sort records."""

    by_public_id: dict[str, bytes] = {}
    unique: dict[bytes, dict[str, JsonValue]] = {}
    for record in records:
        normalized = _normalized_record(record)
        canonical = canonical_json_bytes(normalized)
        public_id = normalized.get("id")
        if public_id is not None:
            if not isinstance(public_id, str) or not public_id:
                raise GraphContractError(
                    "invalid_public_id",
                    "record public ID must be a non-empty string",
                )
            previous = by_public_id.get(public_id)
            if previous is not None and previous != canonical:
                raise GraphIdentityCollision(public_id)
            by_public_id[public_id] = canonical
        unique.setdefault(canonical, normalized)

    def sort_key(canonical: bytes) -> tuple[Any, ...]:
        record = unique[canonical]
        public_id = record.get("id")
        if isinstance(public_id, str):
            return (0, public_id, canonical)
        if {
            "language",
            "name",
            "detector",
            "configuration_paths",
        }.issubset(record):
            return (
                1,
                canonical_json_bytes(record["language"]),
                canonical_json_bytes(record["name"]),
                canonical,
            )
        if {
            "name",
            "extractor",
            "extractor_version",
            "detected_file_count",
            "analyzed_file_count",
        }.issubset(record):
            return (2, canonical_json_bytes(record["name"]), canonical)
        return (3, canonical)

    return [unique[canonical] for canonical in sorted(unique, key=sort_key)]


def _evidence_sort_key(record: Mapping[str, JsonValue]) -> tuple[Any, ...]:
    kind = record.get("kind")
    if kind == "source_ref":
        expected = frozenset({
            "kind",
            "path",
            "line_start",
            "line_end",
            "file_sha256",
            "source_tree_sha256",
        })
        if frozenset(record) != expected:
            raise GraphContractError(
                "invalid_verification_evidence",
                "source evidence must contain its exact closed fields",
            )
        return (
            0,
            record["path"],
            record["line_start"],
            record["line_end"],
            record["file_sha256"],
            record["source_tree_sha256"],
        )
    if kind == "graph_ref":
        expected = frozenset({
            "kind",
            "record_kind",
            "public_id",
            "artifact_graph_version",
            "source_fingerprint",
        })
        if frozenset(record) != expected:
            raise GraphContractError(
                "invalid_verification_evidence",
                "graph evidence must contain its exact closed fields",
            )
        return (
            1,
            record["record_kind"],
            record["public_id"],
            record["artifact_graph_version"],
            record["source_fingerprint"],
        )
    raise GraphContractError(
        "invalid_verification_evidence",
        "verification evidence kind is not recognized",
    )


def canonicalize_verification_evidence(
    evidence: Sequence[Mapping[str, Any]],
) -> list[dict[str, JsonValue]]:
    """Apply section 11.3's exact evidence uniqueness and total order."""

    if not isinstance(evidence, Sequence) or isinstance(evidence, (str, bytes)):
        raise GraphContractError(
            "invalid_verification_evidence",
            "verification evidence must be an array",
        )
    normalized = [_normalized_record(record) for record in evidence]
    seen: set[bytes] = set()
    for record in normalized:
        canonical = canonical_json_bytes(record)
        if canonical in seen:
            raise GraphContractError(
                "duplicate_verification_evidence",
                "verification evidence contains a canonical duplicate",
            )
        seen.add(canonical)
    return sorted(normalized, key=_evidence_sort_key)


def canonicalize_verification_set(
    active_overlays: Sequence[Mapping[str, Any]],
) -> list[dict[str, JsonValue]]:
    """Build the exact sorted active-overlay array used by projection identity."""

    if not isinstance(active_overlays, Sequence) or isinstance(
        active_overlays,
        (str, bytes),
    ):
        raise GraphContractError(
            "invalid_verification_set",
            "active verification overlays must be an array",
        )
    rows: list[tuple[str, str, bytes, dict[str, JsonValue]]] = []
    overlay_ids: dict[str, bytes] = {}
    unique_members: set[bytes] = set()
    for active in active_overlays:
        if not isinstance(active, Mapping):
            raise GraphContractError(
                "invalid_verification_set",
                "active verification overlays may contain only objects",
            )
        allowed = _VERIFICATION_MEMBER_KEYS | {"id", "overlay_id"}
        if not _VERIFICATION_MEMBER_KEYS.issubset(active) or not set(active).issubset(
            allowed
        ):
            raise GraphContractError(
                "invalid_verification_set",
                "active overlay member does not have the exact projection fields",
            )
        evidence = active["evidence"]
        if not isinstance(evidence, list):
            raise GraphContractError(
                "invalid_verification_set",
                "active overlay evidence must be an array",
            )
        member = {
            key: cast(JsonValue, active[key])
            for key in (
                "artifact_graph_version",
                "assertion_fingerprint",
                "verdict",
                "overlay",
                "evidence",
            )
        }
        member["evidence"] = canonicalize_verification_evidence(evidence)
        normalized = _normalized_record(member)
        canonical = canonical_json_bytes(normalized)
        overlay_id = active.get("overlay_id", active.get("id", ""))
        if not isinstance(overlay_id, str):
            raise GraphContractError(
                "invalid_verification_set",
                "active overlay ID must be a string when supplied",
            )
        if overlay_id:
            previous = overlay_ids.get(overlay_id)
            if previous is not None and previous != canonical:
                raise GraphIdentityCollision(overlay_id)
            overlay_ids[overlay_id] = canonical
        if canonical in unique_members:
            continue
        unique_members.add(canonical)
        assertion = normalized["assertion_fingerprint"]
        if not isinstance(assertion, str):
            raise GraphContractError(
                "invalid_verification_set",
                "assertion fingerprint must be a string",
            )
        rows.append((assertion, overlay_id, canonical, normalized))
    rows.sort(key=lambda row: (row[0], row[1], row[2]))
    return [row[3] for row in rows]


__all__ = [
    "GraphIdentityCollision",
    "canonicalize_records",
    "canonicalize_verification_evidence",
    "canonicalize_verification_set",
]
