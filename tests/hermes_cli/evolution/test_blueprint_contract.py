"""Tests for the blueprint contract — canonical projection, closed validation, privacy."""
from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import pytest

from hermes_cli.evolution.suggestions import SuggestionRecord
from hermes_cli.evolution.contract import canonical_json_bytes, content_digest

from hermes_cli.evolution.blueprint_contract import (
    BlueprintContractError,
    BlueprintDocument,
    blueprint_document_from_suggestion,
    blueprint_document_from_json,
    validate_blueprint_document,
)


# ── fixtures ─────────────────────────────────────────────────────────

def eligible_suggestion(**overrides: Any) -> SuggestionRecord:
    kwargs: dict[str, Any] = dict(
        suggestion_id="test-suggestion-001",
        opportunity_key="b" * 64,
        state="eligible",
        active_telos_digest="a" * 64,
        score=0.75,
        user_intent=0.0,
        telos_alignment=0.0,
        impact=0.0,
        recurrence=0.0,
        confidence=0.0,
        reuse=0.0,
        risk=0.0,
        expected_cost=0.0,
        score_policy_version="v1",
        first_observed_at="2026-01-01T00:00:00Z",
        last_observed_at="2026-01-01T00:00:00Z",
        observation_count=3,
        distinct_session_count=2,
        summary_reason="Recurring capability gap",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )
    kwargs.update(overrides)
    return SuggestionRecord(**kwargs)


def observing_suggestion(**overrides: Any) -> SuggestionRecord:
    kwargs: dict[str, Any] = dict(
        suggestion_id="test-suggestion-002",
        opportunity_key="b" * 64,
        state="observing",
        active_telos_digest="a" * 64,
        score=0.5,
        user_intent=0.0,
        telos_alignment=0.0,
        impact=0.0,
        recurrence=0.0,
        confidence=0.0,
        reuse=0.0,
        risk=0.0,
        expected_cost=0.0,
        score_policy_version="v1",
        first_observed_at="2026-01-01T00:00:00Z",
        last_observed_at="2026-01-01T00:00:00Z",
        observation_count=1,
        distinct_session_count=1,
        summary_reason="Just observing",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )
    kwargs.update(overrides)
    return SuggestionRecord(**kwargs)


def valid_document_json(*, capability_hypothesis: str | None = None) -> str:
    doc: dict[str, object] = {
        "schema_version": 1,
        "suggestion_id": "sug-001",
        "opportunity_key": "b" * 64,
        "active_telos_digest": "a" * 64,
        "observer_snapshot": {
            "score": 0.75,
            "score_policy_version": "v1",
            "observation_count": 3,
            "distinct_session_count": 2,
            "summary_reason": "Recurring capability gap",
        },
        "capability_hypothesis": "Address observer opportunity: Recurring capability gap",
        "proposed_component_classes": [],
        "evidence_digests": [],
        "origin": "observer-v1",
    }
    if capability_hypothesis is not None:
        doc["capability_hypothesis"] = capability_hypothesis
    return json.dumps(doc, separators=(",", ":"))


def valid_document() -> BlueprintDocument:
    return blueprint_document_from_json(valid_document_json())


# ── projection tests ─────────────────────────────────────────────────

def test_projection_is_canonical_and_digest_bound() -> None:
    document = blueprint_document_from_suggestion(
        eligible_suggestion(), active_telos_digest="a" * 64
    )
    assert document.schema_version == 1
    assert document.origin == "observer-v1"
    assert document.proposed_component_classes == ()
    assert document.evidence_digests == ()
    assert document.canonical_json_bytes() == canonical_json_bytes(document.to_dict())
    assert document.canonical_digest == content_digest(
        document.to_dict(), domain="hades-autopoiesis-blueprint-v1"
    )


def test_projection_rejects_noneligible_and_stale_telos() -> None:
    with pytest.raises(BlueprintContractError, match="suggestion_not_eligible"):
        blueprint_document_from_suggestion(observing_suggestion(), active_telos_digest="a" * 64)
    with pytest.raises(BlueprintContractError, match="suggestion_telos_mismatch"):
        blueprint_document_from_suggestion(eligible_suggestion(), active_telos_digest="b" * 64)


# ── safe-text validation ─────────────────────────────────────────────

@pytest.mark.parametrize(
    "summary_reason",
    [
        "/etc/passwd",
        "C:\\Users",
        "../secret",
        "file:///etc/passwd",
        "http://example.com",
        "Bearer ghp_abcdefghijklmnopqrstuvwxyz0123456789abcd",
        "ghp_abcdefghijklmnopqrstuvwxyz0123456789abcd",
        "x" * 131073,
        "mailto:user",
        "tel:1234",
        "data:,hello",
        "javascript:void",
    ],
)
def test_blueprint_document_rejects_unsafe_summary(summary_reason: str) -> None:
    sug = eligible_suggestion(summary_reason=summary_reason)
    with pytest.raises(BlueprintContractError) as exc:
        blueprint_document_from_suggestion(sug, active_telos_digest="a" * 64)
    assert summary_reason not in str(exc.value)


@pytest.mark.parametrize(
    "capability_hypothesis",
    [
        "/etc/passwd",
        "C:\\Users",
        "../secret",
        "file:///etc/passwd",
        "http://example.com",
        "Bearer ghp_abcdefghijklmnopqrstuvwxyz0123456789abcd",
        "ghp_abcdefghijklmnopqrstuvwxyz0123456789abcd",
        "x" * 131073,
    ],
)
def test_blueprint_document_rejects_unsafe_hypothesis(capability_hypothesis: str) -> None:
    raw = valid_document_json(capability_hypothesis=capability_hypothesis)
    with pytest.raises(BlueprintContractError) as exc:
        blueprint_document_from_json(raw)
    assert capability_hypothesis not in str(exc.value)


# ── component-class validation ───────────────────────────────────────

@pytest.mark.parametrize(
    "component_classes",
    [
        ("skill", "skill"),
        ("skill", "widget"),
    ],
)
def test_blueprint_document_rejects_invalid_component_classes(
    component_classes: tuple[str, ...],
) -> None:
    doc = valid_document()
    with pytest.raises(BlueprintContractError, match="invalid_component_classes"):
        validate_blueprint_document(
            BlueprintDocument(
                schema_version=doc.schema_version,
                suggestion_id=doc.suggestion_id,
                opportunity_key=doc.opportunity_key,
                active_telos_digest=doc.active_telos_digest,
                observer_snapshot=doc.observer_snapshot,
                capability_hypothesis=doc.capability_hypothesis,
                proposed_component_classes=component_classes,
                evidence_digests=doc.evidence_digests,
                origin=doc.origin,
            )
        )


# ── evidence-digest validation ───────────────────────────────────────

@pytest.mark.parametrize(
    "evidence_digests",
    [
        ("a" * 64, "a" * 64),
        ("z" * 64,),
        ("not-hex",),
    ],
)
def test_blueprint_document_rejects_invalid_evidence_digests(
    evidence_digests: tuple[str, ...],
) -> None:
    doc = valid_document()
    with pytest.raises(BlueprintContractError, match="invalid_evidence_digests"):
        validate_blueprint_document(
            BlueprintDocument(
                schema_version=doc.schema_version,
                suggestion_id=doc.suggestion_id,
                opportunity_key=doc.opportunity_key,
                active_telos_digest=doc.active_telos_digest,
                observer_snapshot=doc.observer_snapshot,
                capability_hypothesis=doc.capability_hypothesis,
                proposed_component_classes=doc.proposed_component_classes,
                evidence_digests=evidence_digests,
                origin=doc.origin,
            )
        )


# ── Deserialization boundary tests ───────────────────────────────────

def test_blueprint_document_from_json_malformed_json() -> None:
    with pytest.raises(BlueprintContractError, match="invalid_document"):
        blueprint_document_from_json("{bad")


def test_blueprint_document_from_json_non_object() -> None:
    for raw in ('"string"', "42", "null", "true", "false", "[]"):
        with pytest.raises(BlueprintContractError, match="invalid_document"):
            blueprint_document_from_json(raw)


_TOP_KEYS = frozenset({
    "schema_version", "suggestion_id", "opportunity_key", "active_telos_digest",
    "observer_snapshot", "capability_hypothesis", "proposed_component_classes",
    "evidence_digests", "origin",
})


def test_blueprint_document_from_json_missing_toplevel_keys() -> None:
    for key in _TOP_KEYS:
        doc = json.loads(valid_document_json())
        del doc[key]
        with pytest.raises(BlueprintContractError, match="invalid_document"):
            blueprint_document_from_json(json.dumps(doc, separators=(",", ":")))


def test_blueprint_document_from_json_extra_toplevel_keys() -> None:
    doc = json.loads(valid_document_json())
    doc["_extra"] = "bad"
    with pytest.raises(BlueprintContractError, match="invalid_document"):
        blueprint_document_from_json(json.dumps(doc, separators=(",", ":")))


_SNAP_KEYS = frozenset({
    "score", "score_policy_version", "observation_count",
    "distinct_session_count", "summary_reason",
})


def test_blueprint_document_from_json_missing_nested_keys() -> None:
    for key in _SNAP_KEYS:
        doc = json.loads(valid_document_json())
        del doc["observer_snapshot"][key]
        with pytest.raises(BlueprintContractError, match="invalid_document"):
            blueprint_document_from_json(json.dumps(doc, separators=(",", ":")))


def test_blueprint_document_from_json_extra_nested_keys() -> None:
    doc = json.loads(valid_document_json())
    doc["observer_snapshot"]["_extra"] = "bad"
    with pytest.raises(BlueprintContractError, match="invalid_document"):
        blueprint_document_from_json(json.dumps(doc, separators=(",", ":")))


def test_blueprint_document_from_json_duplicate_keys() -> None:
    with pytest.raises(BlueprintContractError, match="invalid_document"):
        raw = (
            '{"schema_version":1,"schema_version":2'
            ',"suggestion_id":"s","opportunity_key":"' + "b" * 64 + '"'
            ',"active_telos_digest":"' + "a" * 64 + '"'
            ',"observer_snapshot":{"score":0.5,"score_policy_version":"v1"'
            ',"observation_count":1,"distinct_session_count":1'
            ',"summary_reason":"t"}'
            ',"capability_hypothesis":"Address observer opportunity: t"'
            ',"proposed_component_classes":[],"evidence_digests":[],"origin":"observer-v1"}'
        )
        blueprint_document_from_json(raw)


def test_blueprint_document_from_json_duplicate_nested_keys() -> None:
    with pytest.raises(BlueprintContractError, match="invalid_document"):
        raw = (
            '{"schema_version":1,"suggestion_id":"s","opportunity_key":"' + "b" * 64 + '"'
            ',"active_telos_digest":"' + "a" * 64 + '"'
            ',"observer_snapshot":{"score":0.5,"score":0.6'
            ',"score_policy_version":"v1","observation_count":1'
            ',"distinct_session_count":1,"summary_reason":"t"}'
            ',"capability_hypothesis":"Address observer opportunity: t"'
            ',"proposed_component_classes":[],"evidence_digests":[],"origin":"observer-v1"}'
        )
        blueprint_document_from_json(raw)


# ── Type guard tests ─────────────────────────────────────────────────

def test_blueprint_document_rejects_bool_schema_version() -> None:
    doc = json.loads(valid_document_json())
    doc["schema_version"] = True
    with pytest.raises(BlueprintContractError, match="invalid_schema_version"):
        blueprint_document_from_json(json.dumps(doc, separators=(",", ":")))


def test_blueprint_document_rejects_bool_score() -> None:
    doc = json.loads(valid_document_json())
    doc["observer_snapshot"]["score"] = True
    with pytest.raises(BlueprintContractError, match="invalid_score"):
        blueprint_document_from_json(json.dumps(doc, separators=(",", ":")))


def test_blueprint_document_rejects_bool_observation_count() -> None:
    doc = json.loads(valid_document_json())
    doc["observer_snapshot"]["observation_count"] = True
    with pytest.raises(BlueprintContractError, match="invalid_observation_count"):
        blueprint_document_from_json(json.dumps(doc, separators=(",", ":")))


def test_blueprint_document_rejects_bool_distinct_session_count() -> None:
    doc = json.loads(valid_document_json())
    doc["observer_snapshot"]["distinct_session_count"] = True
    with pytest.raises(BlueprintContractError, match="invalid_distinct_session_count"):
        blueprint_document_from_json(json.dumps(doc, separators=(",", ":")))


def test_blueprint_document_rejects_string_component_classes() -> None:
    doc = json.loads(valid_document_json())
    doc["proposed_component_classes"] = "skill"
    with pytest.raises(BlueprintContractError, match="invalid_component_classes"):
        blueprint_document_from_json(json.dumps(doc, separators=(",", ":")))


def test_blueprint_document_rejects_string_evidence_digests() -> None:
    doc = json.loads(valid_document_json())
    doc["evidence_digests"] = "abc"
    with pytest.raises(BlueprintContractError, match="invalid_evidence_digests"):
        blueprint_document_from_json(json.dumps(doc, separators=(",", ":")))


# ── Digest / ID validation tests ─────────────────────────────────────

def test_blueprint_document_rejects_invalid_active_telos_digest() -> None:
    doc = json.loads(valid_document_json())
    doc["active_telos_digest"] = "not-a-digest"
    with pytest.raises(BlueprintContractError, match="invalid_active_telos_digest"):
        blueprint_document_from_json(json.dumps(doc, separators=(",", ":")))


def test_blueprint_document_rejects_invalid_opportunity_key() -> None:
    doc = json.loads(valid_document_json())
    doc["opportunity_key"] = 42
    with pytest.raises(BlueprintContractError, match="invalid_opportunity_key"):
        blueprint_document_from_json(json.dumps(doc, separators=(",", ":")))


def test_blueprint_document_rejects_invalid_suggestion_id() -> None:
    doc = json.loads(valid_document_json())
    doc["suggestion_id"] = "123-invalid"
    with pytest.raises(BlueprintContractError, match="invalid_suggestion_id"):
        blueprint_document_from_json(json.dumps(doc, separators=(",", ":")))


def test_blueprint_document_rejects_invalid_score_policy_version() -> None:
    doc = json.loads(valid_document_json())
    doc["observer_snapshot"]["score_policy_version"] = " v1"
    with pytest.raises(BlueprintContractError, match="invalid_score_policy_version"):
        blueprint_document_from_json(json.dumps(doc, separators=(",", ":")))


# ── Hypothesis match validation ──────────────────────────────────────

def test_blueprint_document_rejects_hypothesis_mismatch() -> None:
    doc = json.loads(valid_document_json())
    doc["capability_hypothesis"] = "Something else entirely"
    with pytest.raises(BlueprintContractError, match="invalid_document"):
        blueprint_document_from_json(json.dumps(doc, separators=(",", ":")))


# ── Count bounds ─────────────────────────────────────────────────────

def test_blueprint_document_observation_count_upper_bound() -> None:
    doc = valid_document()
    with pytest.raises(BlueprintContractError, match="invalid_observation_count"):
        validate_blueprint_document(replace(
            doc,
            observer_snapshot=replace(doc.observer_snapshot, observation_count=2**63),
        ))
    validate_blueprint_document(replace(
        doc,
        observer_snapshot=replace(doc.observer_snapshot, observation_count=2**63 - 1),
    ))


def test_blueprint_document_distinct_session_count_upper_bound() -> None:
    doc = valid_document()
    with pytest.raises(BlueprintContractError, match="invalid_distinct_session_count"):
        validate_blueprint_document(replace(
            doc,
            observer_snapshot=replace(doc.observer_snapshot, distinct_session_count=2**63),
        ))
    validate_blueprint_document(replace(
        doc,
        observer_snapshot=replace(doc.observer_snapshot, distinct_session_count=2**63 - 1),
    ))


# ── Non-string sequence guard in direct validation ──────────────────

def test_validate_document_rejects_string_component_classes() -> None:
    doc = valid_document()
    with pytest.raises(BlueprintContractError, match="invalid_component_classes"):
        validate_blueprint_document(BlueprintDocument(
            schema_version=doc.schema_version,
            suggestion_id=doc.suggestion_id,
            opportunity_key=doc.opportunity_key,
            active_telos_digest=doc.active_telos_digest,
            observer_snapshot=doc.observer_snapshot,
            capability_hypothesis=doc.capability_hypothesis,
            proposed_component_classes="skill",
            evidence_digests=doc.evidence_digests,
            origin=doc.origin,
        ))


def test_validate_document_rejects_string_evidence_digests() -> None:
    doc = valid_document()
    with pytest.raises(BlueprintContractError, match="invalid_evidence_digests"):
        validate_blueprint_document(BlueprintDocument(
            schema_version=doc.schema_version,
            suggestion_id=doc.suggestion_id,
            opportunity_key=doc.opportunity_key,
            active_telos_digest=doc.active_telos_digest,
            observer_snapshot=doc.observer_snapshot,
            capability_hypothesis=doc.capability_hypothesis,
            proposed_component_classes=doc.proposed_component_classes,
            evidence_digests="abc",
            origin=doc.origin,
        ))


# ── Type guard: validate_blueprint_document ─────────────────────────

def test_validate_document_rejects_non_blueprint_document() -> None:
    with pytest.raises(BlueprintContractError, match="invalid_document"):
        validate_blueprint_document("not a document")  # type: ignore[arg-type]


def test_validate_document_rejects_non_observer_snapshot() -> None:
    with pytest.raises(BlueprintContractError, match="invalid_document"):
        validate_blueprint_document(BlueprintDocument(
            schema_version=1,
            suggestion_id="sug-001",
            opportunity_key="b" * 64,
            active_telos_digest="a" * 64,
            observer_snapshot="not-a-snapshot",  # type: ignore[arg-type]
            capability_hypothesis="Address observer opportunity: Recurring capability gap",
            proposed_component_classes=(),
            evidence_digests=(),
            origin="observer-v1",
        ))


def test_validate_document_rejects_non_string_summary() -> None:
    doc = valid_document()
    with pytest.raises(BlueprintContractError, match="invalid_document"):
        validate_blueprint_document(replace(
            doc,
            observer_snapshot=replace(doc.observer_snapshot, summary_reason=42),  # type: ignore[arg-type]
        ))


def test_validate_document_rejects_non_string_score_policy() -> None:
    doc = valid_document()
    with pytest.raises(BlueprintContractError, match="invalid_score_policy_version"):
        validate_blueprint_document(replace(
            doc,
            observer_snapshot=replace(doc.observer_snapshot, score_policy_version=42),  # type: ignore[arg-type]
        ))


# ── Unhashable element guards ───────────────────────────────────────

def test_validate_document_rejects_unhashable_component_classes() -> None:
    doc = valid_document()
    with pytest.raises(BlueprintContractError, match="invalid_component_classes"):
        validate_blueprint_document(replace(
            doc,
            proposed_component_classes=[["nested_list"]],  # type: ignore[arg-type]
        ))


def test_validate_document_rejects_unhashable_evidence_digests() -> None:
    doc = valid_document()
    with pytest.raises(BlueprintContractError, match="invalid_evidence_digests"):
        validate_blueprint_document(replace(
            doc,
            evidence_digests=[["nested"]],  # type: ignore[arg-type]
        ))


# ── blueprint_document_from_suggestion error wrapping ───────────────

def test_blueprint_document_from_suggestion_rejects_invalid_digest() -> None:
    with pytest.raises(BlueprintContractError, match="invalid_active_telos_digest"):
        blueprint_document_from_suggestion(
            eligible_suggestion(active_telos_digest="not-a-digest"),
            active_telos_digest="not-a-digest",
        )


def test_blueprint_document_from_suggestion_rejects_malformed_suggestion() -> None:
    with pytest.raises(BlueprintContractError, match="invalid_document"):
        blueprint_document_from_suggestion(
            "not-a-suggestion",  # type: ignore[arg-type]
            active_telos_digest="a" * 64,
        )


# ── Bytes / UTF-8 / size / surrogate ────────────────────────────────

def test_blueprint_document_from_json_invalid_utf8() -> None:
    with pytest.raises(BlueprintContractError, match="invalid_document"):
        blueprint_document_from_json(b"\xff\xfe")


def test_blueprint_document_from_json_rejects_bytes() -> None:
    doc = valid_document()
    raw = doc.canonical_json_bytes()
    with pytest.raises(BlueprintContractError, match="invalid_document"):
        blueprint_document_from_json(raw)


def test_blueprint_document_from_json_rejects_surrogate() -> None:
    raw = '{"x": "' + chr(0xD800) + '"}'
    with pytest.raises(BlueprintContractError, match="invalid_document") as exc:
        blueprint_document_from_json(raw)
    assert chr(0xD800) not in str(exc.value)


def test_blueprint_document_from_json_input_too_large() -> None:
    raw = "{" + "a" * 131072 + "}"
    with pytest.raises(BlueprintContractError, match="document_too_large"):
        blueprint_document_from_json(raw)


# ── Round trip ───────────────────────────────────────────────────────

def test_blueprint_document_round_trip() -> None:
    doc1 = valid_document()
    raw = doc1.canonical_json_bytes().decode("utf-8")
    doc2 = blueprint_document_from_json(raw)
    assert doc1.to_dict() == doc2.to_dict()
    assert doc1.canonical_digest == doc2.canonical_digest
    assert doc1.schema_version == doc2.schema_version
    assert doc1.suggestion_id == doc2.suggestion_id
    assert doc1.opportunity_key == doc2.opportunity_key
    assert doc1.active_telos_digest == doc2.active_telos_digest
    assert doc1.observer_snapshot.score == doc2.observer_snapshot.score
    assert doc1.observer_snapshot.score_policy_version == doc2.observer_snapshot.score_policy_version
    assert doc1.observer_snapshot.observation_count == doc2.observer_snapshot.observation_count
    assert doc1.observer_snapshot.distinct_session_count == doc2.observer_snapshot.distinct_session_count
    assert doc1.observer_snapshot.summary_reason == doc2.observer_snapshot.summary_reason
    assert doc1.capability_hypothesis == doc2.capability_hypothesis
    assert doc1.proposed_component_classes == doc2.proposed_component_classes
    assert doc1.evidence_digests == doc2.evidence_digests
    assert doc1.origin == doc2.origin
