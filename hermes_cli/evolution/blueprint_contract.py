"""Closed, immutable blueprint document contract with canonical encoding and privacy boundaries."""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from collections.abc import Sequence as _AbstractSequence
from typing import Sequence

from agent.redact import redact_sensitive_text
from hermes_cli.evolution.contract import (
    canonical_json_bytes,
    content_digest,
    require_digest,
)
from hermes_cli.evolution.suggestions import SuggestionRecord


_DOMAIN = "hades-autopoiesis-blueprint-v1"
_MAX_DOCUMENT_BYTES = 131072
_COMPONENT_CLASSES = frozenset({"skill", "script", "plugin", "mcp"})
_SCHEME_WITH_AUTHORITY = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")
_SCHEME_COLON = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:")
_SUGGESTION_ID_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
_SCORE_POLICY_VERSION_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}\Z")


class BlueprintContractError(ValueError):
    """A contract violation identified by a stable, non-sensitive code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ObserverSnapshot:
    score: float
    score_policy_version: str
    observation_count: int
    distinct_session_count: int
    summary_reason: str


@dataclass(frozen=True)
class BlueprintDocument:
    schema_version: int
    suggestion_id: str
    opportunity_key: str
    active_telos_digest: str
    observer_snapshot: ObserverSnapshot
    capability_hypothesis: str
    proposed_component_classes: Sequence[str]
    evidence_digests: Sequence[str]
    origin: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "suggestion_id": self.suggestion_id,
            "opportunity_key": self.opportunity_key,
            "active_telos_digest": self.active_telos_digest,
            "observer_snapshot": {
                "score": self.observer_snapshot.score,
                "score_policy_version": self.observer_snapshot.score_policy_version,
                "observation_count": self.observer_snapshot.observation_count,
                "distinct_session_count": self.observer_snapshot.distinct_session_count,
                "summary_reason": self.observer_snapshot.summary_reason,
            },
            "capability_hypothesis": self.capability_hypothesis,
            "proposed_component_classes": list(self.proposed_component_classes),
            "evidence_digests": list(self.evidence_digests),
            "origin": self.origin,
        }

    def canonical_json_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict())

    @property
    def canonical_digest(self) -> str:
        return content_digest(self.to_dict(), domain=_DOMAIN)


def _check_uri_colon_scheme(value: str) -> None:
    if _SCHEME_COLON.search(value):
        raise BlueprintContractError("unsafe_text")


def _check_safe_text(value: str, *, limit: int) -> None:
    if not value.isprintable():
        raise BlueprintContractError("unsafe_text")
    if "/" in value or "\\" in value:
        raise BlueprintContractError("unsafe_text")
    if redact_sensitive_text(value) != value:
        raise BlueprintContractError("unsafe_text")
    if "file:" in value.lower():
        raise BlueprintContractError("unsafe_text")
    if _SCHEME_WITH_AUTHORITY.search(value):
        raise BlueprintContractError("unsafe_text")
    encoded = value.encode("utf-8")
    if len(encoded) > limit:
        raise BlueprintContractError("unsafe_text")


def _parse_json_object(raw: str) -> dict[str, object]:
    def _make_dict(pairs: list[tuple[str, object]]) -> dict[str, object]:
        seen: set[str] = set()
        for key, _ in pairs:
            if key in seen:
                raise BlueprintContractError("invalid_document")
            seen.add(key)
        return dict(pairs)

    try:
        result = json.loads(raw, object_pairs_hook=_make_dict)
    except json.JSONDecodeError:
        raise BlueprintContractError("invalid_document")
    if not isinstance(result, dict):
        raise BlueprintContractError("invalid_document")
    return result


def _validate_document(document: BlueprintDocument) -> None:
    if not isinstance(document, BlueprintDocument):
        raise BlueprintContractError("invalid_document")
    if not isinstance(document.observer_snapshot, ObserverSnapshot):
        raise BlueprintContractError("invalid_document")

    if isinstance(document.schema_version, bool) or not isinstance(document.schema_version, int) or document.schema_version != 1:
        raise BlueprintContractError("invalid_schema_version")
    if document.origin != "observer-v1":
        raise BlueprintContractError("invalid_origin")

    if not isinstance(document.suggestion_id, str) or _SUGGESTION_ID_PATTERN.match(document.suggestion_id) is None:
        raise BlueprintContractError("invalid_suggestion_id")

    score = document.observer_snapshot.score
    if (
        isinstance(score, bool)
        or not isinstance(score, (int, float))
        or not math.isfinite(score)
        or not (0.0 <= score <= 1.0)
    ):
        raise BlueprintContractError("invalid_score")

    spv = document.observer_snapshot.score_policy_version
    if not isinstance(spv, str) or _SCORE_POLICY_VERSION_PATTERN.match(spv) is None:
        raise BlueprintContractError("invalid_score_policy_version")

    oc = document.observer_snapshot.observation_count
    if isinstance(oc, bool) or not isinstance(oc, int) or oc < 0 or oc > 2**63 - 1:
        raise BlueprintContractError("invalid_observation_count")

    dc = document.observer_snapshot.distinct_session_count
    if isinstance(dc, bool) or not isinstance(dc, int) or dc < 0 or dc > 2**63 - 1:
        raise BlueprintContractError("invalid_distinct_session_count")

    if not isinstance(document.observer_snapshot.summary_reason, str):
        raise BlueprintContractError("invalid_document")
    _check_safe_text(document.observer_snapshot.summary_reason, limit=512)
    _check_uri_colon_scheme(document.observer_snapshot.summary_reason)

    expected_hypothesis = f"Address observer opportunity: {document.observer_snapshot.summary_reason}"
    if document.capability_hypothesis != expected_hypothesis:
        raise BlueprintContractError("invalid_document")
    _check_safe_text(document.capability_hypothesis, limit=768)
    _check_uri_colon_scheme(document.observer_snapshot.summary_reason)

    if isinstance(document.proposed_component_classes, (str, bytes)) or not isinstance(document.proposed_component_classes, _AbstractSequence):
        raise BlueprintContractError("invalid_component_classes")
    seen_classes: set[str] = set()
    for cls_name in document.proposed_component_classes:
        if not isinstance(cls_name, str):
            raise BlueprintContractError("invalid_component_classes")
        if cls_name in seen_classes:
            raise BlueprintContractError("invalid_component_classes")
        seen_classes.add(cls_name)
        if cls_name not in _COMPONENT_CLASSES:
            raise BlueprintContractError("invalid_component_classes")

    if isinstance(document.evidence_digests, (str, bytes)) or not isinstance(document.evidence_digests, _AbstractSequence):
        raise BlueprintContractError("invalid_evidence_digests")
    seen_digests: set[str] = set()
    for digest in document.evidence_digests:
        if not isinstance(digest, str):
            raise BlueprintContractError("invalid_evidence_digests")
        if digest in seen_digests:
            raise BlueprintContractError("invalid_evidence_digests")
        seen_digests.add(digest)
        try:
            require_digest(digest)
        except ValueError:
            raise BlueprintContractError("invalid_evidence_digests")

    try:
        require_digest(document.active_telos_digest)
    except ValueError:
        raise BlueprintContractError("invalid_active_telos_digest")

    try:
        require_digest(document.opportunity_key)
    except ValueError:
        raise BlueprintContractError("invalid_opportunity_key")

    if len(document.canonical_json_bytes()) > _MAX_DOCUMENT_BYTES:
        raise BlueprintContractError("document_too_large")


def validate_blueprint_document(document: BlueprintDocument) -> None:
    _validate_document(document)


def blueprint_document_from_suggestion(
    suggestion: SuggestionRecord, *, active_telos_digest: str
) -> BlueprintDocument:
    try:
        if suggestion.state != "eligible":
            raise BlueprintContractError("suggestion_not_eligible")
        if suggestion.active_telos_digest != active_telos_digest:
            raise BlueprintContractError("suggestion_telos_mismatch")

        try:
            require_digest(active_telos_digest)
        except ValueError:
            raise BlueprintContractError("invalid_active_telos_digest")

        if not isinstance(suggestion.summary_reason, str):
            raise BlueprintContractError("invalid_document")
        _check_safe_text(suggestion.summary_reason, limit=512)

        hypothesis = f"Address observer opportunity: {suggestion.summary_reason}"
        _check_safe_text(hypothesis, limit=768)

        document = BlueprintDocument(
            schema_version=1,
            suggestion_id=suggestion.suggestion_id,
            opportunity_key=suggestion.opportunity_key,
            active_telos_digest=active_telos_digest,
            observer_snapshot=ObserverSnapshot(
                score=suggestion.score,
                score_policy_version=suggestion.score_policy_version,
                observation_count=suggestion.observation_count,
                distinct_session_count=suggestion.distinct_session_count,
                summary_reason=suggestion.summary_reason,
            ),
            capability_hypothesis=hypothesis,
            proposed_component_classes=(),
            evidence_digests=(),
            origin="observer-v1",
        )
        _validate_document(document)
        return document
    except AttributeError:
        raise BlueprintContractError("invalid_document") from None


def blueprint_document_from_json(raw: str) -> BlueprintDocument:
    if not isinstance(raw, str):
        raise BlueprintContractError("invalid_document")
    try:
        raw_bytes = raw.encode("utf-8")
    except UnicodeEncodeError:
        raise BlueprintContractError("invalid_document")

    if len(raw_bytes) > _MAX_DOCUMENT_BYTES:
        raise BlueprintContractError("document_too_large")

    data = _parse_json_object(raw)

    _TOP_KEYS = frozenset({
        "schema_version", "suggestion_id", "opportunity_key", "active_telos_digest",
        "observer_snapshot", "capability_hypothesis", "proposed_component_classes",
        "evidence_digests", "origin",
    })
    if set(data.keys()) != _TOP_KEYS:
        raise BlueprintContractError("invalid_document")

    snap_raw = data["observer_snapshot"]
    if not isinstance(snap_raw, dict):
        raise BlueprintContractError("invalid_document")

    _SNAP_KEYS = frozenset({
        "score", "score_policy_version", "observation_count",
        "distinct_session_count", "summary_reason",
    })
    if set(snap_raw.keys()) != _SNAP_KEYS:
        raise BlueprintContractError("invalid_document")

    sv = data["schema_version"]
    if isinstance(sv, bool) or not isinstance(sv, int):
        raise BlueprintContractError("invalid_schema_version")

    score = snap_raw["score"]
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise BlueprintContractError("invalid_score")

    oc = snap_raw["observation_count"]
    if isinstance(oc, bool) or not isinstance(oc, int) or oc < 0:
        raise BlueprintContractError("invalid_observation_count")

    dc = snap_raw["distinct_session_count"]
    if isinstance(dc, bool) or not isinstance(dc, int) or dc < 0:
        raise BlueprintContractError("invalid_distinct_session_count")

    pcc = data["proposed_component_classes"]
    if isinstance(pcc, str) or not isinstance(pcc, list):
        raise BlueprintContractError("invalid_component_classes")

    ed = data["evidence_digests"]
    if isinstance(ed, str) or not isinstance(ed, list):
        raise BlueprintContractError("invalid_evidence_digests")

    sid = data["suggestion_id"]

    capability_hypothesis = data["capability_hypothesis"]
    if not isinstance(capability_hypothesis, str):
        raise BlueprintContractError("invalid_document")

    origin_val = data["origin"]
    if not isinstance(origin_val, str):
        raise BlueprintContractError("invalid_document")

    summary_reason = snap_raw["summary_reason"]
    if not isinstance(summary_reason, str):
        raise BlueprintContractError("invalid_document")

    spv = snap_raw["score_policy_version"]
    if not isinstance(spv, str):
        raise BlueprintContractError("invalid_document")

    expected_hypothesis = f"Address observer opportunity: {summary_reason}"
    if capability_hypothesis != expected_hypothesis:
        raise BlueprintContractError("invalid_document")

    snapshot = ObserverSnapshot(
        score=score,
        score_policy_version=spv,
        observation_count=oc,
        distinct_session_count=dc,
        summary_reason=summary_reason,
    )

    document = BlueprintDocument(
        schema_version=sv,
        suggestion_id=sid,
        opportunity_key=data["opportunity_key"],
        active_telos_digest=data["active_telos_digest"],
        observer_snapshot=snapshot,
        capability_hypothesis=capability_hypothesis,
        proposed_component_classes=tuple(pcc),
        evidence_digests=tuple(ed),
        origin=origin_val,
    )
    _validate_document(document)
    return document
