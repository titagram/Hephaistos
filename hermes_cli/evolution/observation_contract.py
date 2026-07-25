"""Observation envelope contract, closed signal/provenance schemas, and validation."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from agent.redact import redact_sensitive_text

SIGNAL_TYPES = frozenset({
    "failure",
    "capability_absence",
    "friction",
    "user_feedback",
    "telos_gap",
})

PROVENANCE_CLASSES = frozenset({
    "measured_runtime",
    "explicit_user",
    "gnothi_verified",
    "structured_tool_result",
    "agent_inference",
    "legacy_log_import",
})

PROVENANCE_WEIGHTS = {
    "measured_runtime": 1.00,
    "explicit_user": 0.95,
    "gnothi_verified": 0.90,
    "structured_tool_result": 0.80,
    "agent_inference": 0.40,
    "legacy_log_import": 0.30,
}

SEVERITY_LEVELS = frozenset({"critical", "high", "medium", "low", "unknown"})
TASK_IMPACTS = frozenset({"critical", "high", "medium", "low", "unknown"})
LATENCY_BUCKETS = frozenset({"lt_1s", "1s_to_5s", "5s_to_15s", "15s_to_60s", "gt_60s"})
REDACTION_STATUSES = frozenset({"verified_redacted", "redaction_failed"})

_TAXONOMY_KEY_PATTERN = re.compile(r"[a-z][a-z0-9_.-]{0,127}\Z", re.ASCII)
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_UUID_PATTERN = re.compile(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\Z", re.ASCII)
_OPAQUE_REF_PATTERN = re.compile(r"[a-zA-Z0-9_-]{1,128}\Z", re.ASCII)
_PATH_INDICATOR = re.compile(r"(/|\\|file://)", re.IGNORECASE)


class ObservationContractError(Exception):
    """Raised when ObservationEnvelope validation fails."""


@dataclass(frozen=True)
class ObservationEnvelope:
    schema_version: int
    event_id: str
    organism_id: str
    occurred_at: str
    signal_type: str
    provenance: str
    source_profile_ref: str
    source_project_ref: str | None
    source_session_ref: str | None
    generation_id: str
    gnothi_revision_digest: str | None
    telos_digest: str | None
    capability_key: str
    operation_key: str
    outcome_key: str
    constraint_key: str
    severity: str
    task_impact: str
    retry_count: int
    latency_bucket: str | None
    explicit_user_intent: bool
    recovered: bool
    evidence_refs: tuple[str, ...]
    redaction_status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "organism_id": self.organism_id,
            "occurred_at": self.occurred_at,
            "signal_type": self.signal_type,
            "provenance": self.provenance,
            "source_profile_ref": self.source_profile_ref,
            "source_project_ref": self.source_project_ref,
            "source_session_ref": self.source_session_ref,
            "generation_id": self.generation_id,
            "gnothi_revision_digest": self.gnothi_revision_digest,
            "telos_digest": self.telos_digest,
            "capability_key": self.capability_key,
            "operation_key": self.operation_key,
            "outcome_key": self.outcome_key,
            "constraint_key": self.constraint_key,
            "severity": self.severity,
            "task_impact": self.task_impact,
            "retry_count": self.retry_count,
            "latency_bucket": self.latency_bucket,
            "explicit_user_intent": self.explicit_user_intent,
            "recovered": self.recovered,
            "evidence_refs": list(self.evidence_refs),
            "redaction_status": self.redaction_status,
        }

    def to_canonical_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def validate_observation_envelope(envelope: ObservationEnvelope) -> None:
    if envelope.schema_version != 1:
        raise ObservationContractError(f"Unsupported schema version: {envelope.schema_version}")

    raw_json = envelope.to_canonical_json()
    if len(raw_json.encode("utf-8")) > 4096:
        raise ObservationContractError("Serialized ObservationEnvelope exceeds max 4096 bytes")

    # Validate timestamps
    try:
        datetime.strptime(envelope.occurred_at, "%Y-%m-%dT%H:%M:%S.%fZ")
    except (ValueError, TypeError):
        raise ObservationContractError(f"Invalid timestamp format for occurred_at: {envelope.occurred_at!r}")

    # Validate organism_id and digests
    if _UUID_PATTERN.fullmatch(envelope.organism_id) is None and not _OPAQUE_REF_PATTERN.fullmatch(envelope.organism_id):
        raise ObservationContractError(f"Invalid organism_id format: {envelope.organism_id!r}")

    if not _SHA256_PATTERN.fullmatch(envelope.generation_id):
        raise ObservationContractError(f"Invalid generation_id digest: {envelope.generation_id!r}")

    if envelope.gnothi_revision_digest is not None and not _SHA256_PATTERN.fullmatch(envelope.gnothi_revision_digest):
        raise ObservationContractError(f"Invalid gnothi_revision_digest: {envelope.gnothi_revision_digest!r}")

    if envelope.telos_digest is not None and not _SHA256_PATTERN.fullmatch(envelope.telos_digest):
        raise ObservationContractError(f"Invalid telos_digest: {envelope.telos_digest!r}")

    # Validate opaque references
    refs = [
        ("source_profile_ref", envelope.source_profile_ref),
        ("source_project_ref", envelope.source_project_ref),
        ("source_session_ref", envelope.source_session_ref),
    ]

    for ref_name, ref_val in refs:
        if ref_val is None:
            continue
        if _PATH_INDICATOR.search(ref_val):
            raise ObservationContractError(f"Reference {ref_name} contains path/URI characters: {ref_val!r}")
        if not _OPAQUE_REF_PATTERN.fullmatch(ref_val):
            raise ObservationContractError(f"Reference {ref_name} is not a valid opaque token: {ref_val!r}")
        if redact_sensitive_text(ref_val, force=True) != ref_val:
            raise ObservationContractError(f"Reference {ref_name} contains sensitive/secret material: {ref_val!r}")

    if envelope.signal_type not in SIGNAL_TYPES:
        raise ObservationContractError(f"Invalid signal_type: {envelope.signal_type!r}")

    if envelope.provenance not in PROVENANCE_CLASSES:
        raise ObservationContractError(f"Invalid provenance: {envelope.provenance!r}")

    if envelope.severity not in SEVERITY_LEVELS:
        raise ObservationContractError(f"Invalid severity: {envelope.severity!r}")

    if envelope.task_impact not in TASK_IMPACTS:
        raise ObservationContractError(f"Invalid task_impact: {envelope.task_impact!r}")

    if envelope.latency_bucket is not None and envelope.latency_bucket not in LATENCY_BUCKETS:
        raise ObservationContractError(f"Invalid latency_bucket: {envelope.latency_bucket!r}")

    if envelope.redaction_status not in REDACTION_STATUSES:
        raise ObservationContractError(f"Invalid redaction_status: {envelope.redaction_status!r}")

    if not (0 <= envelope.retry_count <= 1000):
        raise ObservationContractError("retry_count must be in range 0..1000")

    if len(envelope.evidence_refs) > 16:
        raise ObservationContractError("Max 16 evidence_refs allowed")

    for ref in envelope.evidence_refs:
        if not _SHA256_PATTERN.fullmatch(ref):
            raise ObservationContractError(f"Invalid evidence_ref format: {ref!r}")

    taxonomy_keys = [
        ("capability_key", envelope.capability_key),
        ("operation_key", envelope.operation_key),
        ("outcome_key", envelope.outcome_key),
        ("constraint_key", envelope.constraint_key),
    ]

    for name, val in taxonomy_keys:
        if not _TAXONOMY_KEY_PATTERN.fullmatch(val):
            raise ObservationContractError(f"Invalid taxonomy key for {name}: {val!r}")
        if _PATH_INDICATOR.search(val):
            raise ObservationContractError(f"Taxonomy key {name} contains path characters: {val!r}")


def observation_envelope_from_dict(data: dict[str, Any]) -> ObservationEnvelope:
    env = ObservationEnvelope(
        schema_version=int(data["schema_version"]),
        event_id=str(data["event_id"]),
        organism_id=str(data["organism_id"]),
        occurred_at=str(data["occurred_at"]),
        signal_type=str(data["signal_type"]),
        provenance=str(data["provenance"]),
        source_profile_ref=str(data["source_profile_ref"]),
        source_project_ref=data.get("source_project_ref"),
        source_session_ref=data.get("source_session_ref"),
        generation_id=str(data["generation_id"]),
        gnothi_revision_digest=data.get("gnothi_revision_digest"),
        telos_digest=data.get("telos_digest"),
        capability_key=str(data["capability_key"]),
        operation_key=str(data["operation_key"]),
        outcome_key=str(data["outcome_key"]),
        constraint_key=str(data["constraint_key"]),
        severity=str(data["severity"]),
        task_impact=str(data["task_impact"]),
        retry_count=int(data["retry_count"]),
        latency_bucket=data.get("latency_bucket"),
        explicit_user_intent=bool(data["explicit_user_intent"]),
        recovered=bool(data["recovered"]),
        evidence_refs=tuple(str(r) for r in data.get("evidence_refs", [])),
        redaction_status=str(data["redaction_status"]),
    )
    validate_observation_envelope(env)
    return env
