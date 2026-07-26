"""Tests for create_or_get_blueprint_draft atomic persistence and verified repository reads."""
from __future__ import annotations

import collections.abc
import json
import uuid
from dataclasses import replace
from pathlib import Path

import pytest

from hermes_cli.evolution.contract import (
    canonical_json_bytes,
    content_digest,
    require_digest,
)
from hermes_cli.evolution.ledger import (
    BlueprintDraft,
    EvolutionLedger,
    EvolutionLedgerError,
)
from hermes_cli.evolution.blueprint_contract import (
    BlueprintContractError,
    BlueprintDocument,
    blueprint_document_from_json,
)
from hermes_cli.evolution.blueprint_repository import (
    BlueprintRepository,
    BlueprintRepositoryError,
    StoredBlueprint,
)


_DOMAIN = "hades-autopoiesis-blueprint-v1"
_SAMPLE_DOCUMENT = {
    "schema_version": 1,
    "suggestion_id": "sug_alpha",
    "opportunity_key": "b" * 64,
    "active_telos_digest": "a" * 64,
    "observer_snapshot": {
        "score": 0.75,
        "score_policy_version": "v2",
        "observation_count": 3,
        "distinct_session_count": 2,
        "summary_reason": "Recurring capability gap",
    },
    "capability_hypothesis": "Address observer opportunity: Recurring capability gap",
    "proposed_component_classes": [],
    "evidence_digests": [],
    "origin": "observer-v1",
}
_SAMPLE_JSON_BYTES = canonical_json_bytes(_SAMPLE_DOCUMENT)
DOCUMENT_JSON = _SAMPLE_JSON_BYTES.decode("utf-8")
DOCUMENT_DIGEST = content_digest(_SAMPLE_DOCUMENT, domain=_DOMAIN)


def _doc_for_sid(suggestion_id: str) -> tuple[str, str]:
    """Build a canonical document JSON and digest for the given suggestion_id."""
    doc = {**_SAMPLE_DOCUMENT, "suggestion_id": suggestion_id}
    j = canonical_json_bytes(doc).decode("utf-8")
    d = content_digest(doc, domain=_DOMAIN)
    return j, d


def count(ledger: EvolutionLedger, table: str) -> int:
    return ledger.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# ---------------------------------------------------------------------------
# Original Task 3 tests (preserved)
# ---------------------------------------------------------------------------


def test_create_or_get_blueprint_draft_writes_every_record_atomically(tmp_path: Path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    result = ledger.create_or_get_blueprint_draft(
        suggestion_id="sug_alpha",
        canonical_document_json=DOCUMENT_JSON,
        canonical_digest=DOCUMENT_DIGEST,
        input_digests=(DOCUMENT_DIGEST, "a" * 64),
    )
    assert result.created is True
    assert result.state == "draft"
    assert result.event is not None
    assert ledger.verify_chain() == []
    assert count(ledger, "attempts") == 1
    assert count(ledger, "blueprints") == 1
    assert count(ledger, "blueprint_documents") == 1
    assert count(ledger, "lifecycle_events") == 1


def test_equivalent_document_returns_existing_without_second_attempt_or_event(tmp_path: Path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    first = ledger.create_or_get_blueprint_draft(
        suggestion_id="sug_alpha",
        canonical_document_json=DOCUMENT_JSON,
        canonical_digest=DOCUMENT_DIGEST,
        input_digests=(DOCUMENT_DIGEST, "a" * 64),
    )
    second = ledger.create_or_get_blueprint_draft(
        suggestion_id="sug_alpha",
        canonical_document_json=DOCUMENT_JSON,
        canonical_digest=DOCUMENT_DIGEST,
        input_digests=(DOCUMENT_DIGEST, "a" * 64),
    )
    assert second.created is False
    assert second.attempt_id == first.attempt_id
    assert second.blueprint_id == first.blueprint_id
    assert second.event is None
    assert count(ledger, "attempts") == count(ledger, "blueprints") == 1
    assert count(ledger, "blueprint_documents") == count(ledger, "lifecycle_events") == 1
    assert ledger.verify_chain() == []
    assert second.state == "draft"
    assert second.canonical_digest == first.canonical_digest
    assert second.created_at == first.created_at


def test_noncanonical_json_raises_without_mutation(tmp_path: Path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    noncanonical = json.dumps(_SAMPLE_DOCUMENT, sort_keys=True, indent=2)
    with pytest.raises(EvolutionLedgerError, match="noncanonical_document"):
        ledger.create_or_get_blueprint_draft(
            suggestion_id="sug_alpha",
            canonical_document_json=noncanonical,
            canonical_digest=DOCUMENT_DIGEST,
            input_digests=(DOCUMENT_DIGEST, "a" * 64),
        )
    assert count(ledger, "attempts") == 0
    assert count(ledger, "blueprints") == 0
    assert count(ledger, "blueprint_documents") == 0
    assert count(ledger, "lifecycle_events") == 0


def test_wrong_domain_digest_raises_without_mutation(tmp_path: Path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    wrong_digest = content_digest(_SAMPLE_DOCUMENT, domain="wrong-domain")
    with pytest.raises(EvolutionLedgerError, match="digest_mismatch"):
        ledger.create_or_get_blueprint_draft(
            suggestion_id="sug_alpha",
            canonical_document_json=DOCUMENT_JSON,
            canonical_digest=wrong_digest,
            input_digests=(DOCUMENT_DIGEST, "a" * 64),
        )
    assert count(ledger, "attempts") == 0
    assert count(ledger, "blueprints") == 0
    assert count(ledger, "blueprint_documents") == 0
    assert count(ledger, "lifecycle_events") == 0


def test_invalid_suggestion_id_raises_without_mutation(tmp_path: Path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    with pytest.raises(EvolutionLedgerError, match="invalid_suggestion_id"):
        ledger.create_or_get_blueprint_draft(
            suggestion_id="",
            canonical_document_json=DOCUMENT_JSON,
            canonical_digest=DOCUMENT_DIGEST,
            input_digests=(DOCUMENT_DIGEST, "a" * 64),
        )
    assert count(ledger, "attempts") == 0
    assert count(ledger, "blueprints") == 0
    assert count(ledger, "blueprint_documents") == 0
    assert count(ledger, "lifecycle_events") == 0


def test_too_many_input_digests_raises_without_mutation(tmp_path: Path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    digests = tuple("a" * 64 for _ in range(65))
    with pytest.raises(EvolutionLedgerError, match="invalid_input_digests"):
        ledger.create_or_get_blueprint_draft(
            suggestion_id="sug_alpha",
            canonical_document_json=DOCUMENT_JSON,
            canonical_digest=DOCUMENT_DIGEST,
            input_digests=digests,
        )
    assert count(ledger, "attempts") == 0
    assert count(ledger, "blueprints") == 0
    assert count(ledger, "blueprint_documents") == 0
    assert count(ledger, "lifecycle_events") == 0


def test_append_failure_rolls_back_all_tables(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    original_append = EvolutionLedger._append
    call_count = [0]

    def failing_append(self, connection, event):
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("injected _append failure")
        return original_append(self, connection, event)

    monkeypatch.setattr(EvolutionLedger, "_append", failing_append)

    with pytest.raises(RuntimeError, match="injected _append failure"):
        ledger.create_or_get_blueprint_draft(
            suggestion_id="sug_alpha",
            canonical_document_json=DOCUMENT_JSON,
            canonical_digest=DOCUMENT_DIGEST,
            input_digests=(DOCUMENT_DIGEST, "a" * 64),
        )
    assert count(ledger, "attempts") == 0
    assert count(ledger, "blueprints") == 0
    assert count(ledger, "blueprint_documents") == 0
    assert count(ledger, "lifecycle_events") == 0


def test_input_digests_missing_canonical_raises_without_mutation(tmp_path: Path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    with pytest.raises(EvolutionLedgerError, match="invalid_input_digests"):
        ledger.create_or_get_blueprint_draft(
            suggestion_id="sug_alpha",
            canonical_document_json=DOCUMENT_JSON,
            canonical_digest=DOCUMENT_DIGEST,
            input_digests=("a" * 64,),
        )
    assert count(ledger, "attempts") == 0
    assert count(ledger, "blueprints") == 0
    assert count(ledger, "blueprint_documents") == 0
    assert count(ledger, "lifecycle_events") == 0


# ---------------------------------------------------------------------------
# Hardening: valid suggestion_id acceptance
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("valid_id", ["a", "sug_alpha", "sug-alpha", "hyphen-"])
def test_valid_suggestion_ids_accepted(tmp_path: Path, valid_id: str) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    doc_json, doc_digest = _doc_for_sid(valid_id)
    result = ledger.create_or_get_blueprint_draft(
        suggestion_id=valid_id,
        canonical_document_json=doc_json,
        canonical_digest=doc_digest,
        input_digests=(doc_digest, "a" * 64),
    )
    assert result.created is True
    assert result.state == "draft"
    assert count(ledger, "attempts") == 1


# ---------------------------------------------------------------------------
# Hardening: suggestion_id invalid classes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "invalid_id",
    [
        "  leading_whitespace",
        "trailing_whitespace ",
        "spaces in id",
        "colon:in:id",
        "punctuation!here",
        "slash/in/id",
        "backslash\\in\\id",
        "weird\xe9char",
        "\u00e9pure",
        "1starts_digit",
        "a" * 65,
    ],
)
def test_invalid_suggestion_id_classes_rejected(tmp_path: Path, invalid_id: str) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    with pytest.raises(EvolutionLedgerError, match="invalid_suggestion_id"):
        ledger.create_or_get_blueprint_draft(
            suggestion_id=invalid_id,
            canonical_document_json=DOCUMENT_JSON,
            canonical_digest=DOCUMENT_DIGEST,
            input_digests=(DOCUMENT_DIGEST, "a" * 64),
        )
    assert count(ledger, "attempts") == 0
    assert count(ledger, "blueprints") == 0


# ---------------------------------------------------------------------------
# Hardening: canonical_document_json — duplicate keys
# ---------------------------------------------------------------------------


def test_duplicate_top_level_json_key_raises_noncanonical(tmp_path: Path) -> None:
    """Inject a duplicate top-level key into otherwise-valid canonical JSON."""
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    # Canonical form ends with …,"schema_version":1,"suggestion_id":"sug_alpha"}
    # Inject a duplicate suggestion_id
    duped = DOCUMENT_JSON.replace(
        ',"suggestion_id":"sug_alpha"}',
        ',"suggestion_id":"sug_alpha","suggestion_id":"sug_alpha"}',
    )
    assert duped != DOCUMENT_JSON
    with pytest.raises(EvolutionLedgerError, match="noncanonical_document"):
        ledger.create_or_get_blueprint_draft(
            suggestion_id="sug_alpha",
            canonical_document_json=duped,
            canonical_digest=DOCUMENT_DIGEST,
            input_digests=(DOCUMENT_DIGEST, "a" * 64),
        )
    assert count(ledger, "attempts") == 0


def test_duplicate_nested_json_key_raises_noncanonical(tmp_path: Path) -> None:
    """Inject a duplicate key inside a nested object in otherwise-valid JSON."""
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    # observer_snapshot canonical: {"distinct_session_count":2,"observation_count":3,"score":0.75,"score_policy_version":"v2","summary_reason":"Recurring capability gap"}
    original = (
        '"observer_snapshot":{'
        '"distinct_session_count":2,'
        '"observation_count":3,'
        '"score":0.75,'
        '"score_policy_version":"v2",'
        '"summary_reason":"Recurring capability gap"'
        "}"
    )
    duped_section = (
        '"observer_snapshot":{'
        '"distinct_session_count":2,'
        '"observation_count":3,'
        '"score":0.75,'
        '"score":0.80,'
        '"score_policy_version":"v2",'
        '"summary_reason":"Recurring capability gap"'
        "}"
    )
    duped = DOCUMENT_JSON.replace(original, duped_section)
    assert duped != DOCUMENT_JSON
    with pytest.raises(EvolutionLedgerError, match="noncanonical_document"):
        ledger.create_or_get_blueprint_draft(
            suggestion_id="sug_alpha",
            canonical_document_json=duped,
            canonical_digest=DOCUMENT_DIGEST,
            input_digests=(DOCUMENT_DIGEST, "a" * 64),
        )
    assert count(ledger, "attempts") == 0


# ---------------------------------------------------------------------------
# Hardening: canonical_document_json — NaN, Infinity, non-object, size, decode
# ---------------------------------------------------------------------------


def test_noncanonical_nan_value_raises_noncanonical(tmp_path: Path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    with pytest.raises(EvolutionLedgerError, match="noncanonical_document"):
        ledger.create_or_get_blueprint_draft(
            suggestion_id="sug_alpha",
            canonical_document_json=DOCUMENT_JSON.replace("0.75", "NaN"),
            canonical_digest=DOCUMENT_DIGEST,
            input_digests=(DOCUMENT_DIGEST, "a" * 64),
        )
    assert count(ledger, "attempts") == 0


def test_noncanonical_infinity_value_raises_noncanonical(tmp_path: Path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    with pytest.raises(EvolutionLedgerError, match="noncanonical_document"):
        ledger.create_or_get_blueprint_draft(
            suggestion_id="sug_alpha",
            canonical_document_json=DOCUMENT_JSON.replace("0.75", "Infinity"),
            canonical_digest=DOCUMENT_DIGEST,
            input_digests=(DOCUMENT_DIGEST, "a" * 64),
        )
    assert count(ledger, "attempts") == 0


def test_non_object_document_raises_noncanonical(tmp_path: Path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    with pytest.raises(EvolutionLedgerError, match="noncanonical_document"):
        ledger.create_or_get_blueprint_draft(
            suggestion_id="sug_alpha",
            canonical_document_json='["not","an","object"]',
            canonical_digest=DOCUMENT_DIGEST,
            input_digests=(DOCUMENT_DIGEST, "a" * 64),
        )
    assert count(ledger, "attempts") == 0


def test_json_too_large_raises_noncanonical(tmp_path: Path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    huge = '{"x":"' + "y" * 131072 + '"}'
    with pytest.raises(EvolutionLedgerError, match="noncanonical_document"):
        ledger.create_or_get_blueprint_draft(
            suggestion_id="sug_alpha",
            canonical_document_json=huge,
            canonical_digest=DOCUMENT_DIGEST,
            input_digests=(DOCUMENT_DIGEST, "a" * 64),
        )
    assert count(ledger, "attempts") == 0


def test_json_too_small_raises_noncanonical(tmp_path: Path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    with pytest.raises(EvolutionLedgerError, match="noncanonical_document"):
        ledger.create_or_get_blueprint_draft(
            suggestion_id="sug_alpha",
            canonical_document_json="",
            canonical_digest=DOCUMENT_DIGEST,
            input_digests=(DOCUMENT_DIGEST, "a" * 64),
        )
    assert count(ledger, "attempts") == 0


def test_jsondecode_error_raises_noncanonical(tmp_path: Path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    with pytest.raises(EvolutionLedgerError, match="noncanonical_document"):
        ledger.create_or_get_blueprint_draft(
            suggestion_id="sug_alpha",
            canonical_document_json="{not valid json",
            canonical_digest=DOCUMENT_DIGEST,
            input_digests=(DOCUMENT_DIGEST, "a" * 64),
        )
    assert count(ledger, "attempts") == 0


@pytest.mark.parametrize("raw", [b"{}", 42, None])
def test_document_json_rejects_non_string_input(
    tmp_path: Path, raw: object
) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    with pytest.raises(EvolutionLedgerError, match="noncanonical_document"):
        ledger.create_or_get_blueprint_draft(
            suggestion_id="sug_alpha",
            canonical_document_json=raw,  # type: ignore[arg-type]
            canonical_digest=DOCUMENT_DIGEST,
            input_digests=(DOCUMENT_DIGEST, "a" * 64),
        )
    assert count(ledger, "attempts") == 0


def test_document_json_rejects_unpaired_unicode_surrogate(tmp_path: Path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    surrogate = chr(0xD800)
    raw = '{"suggestion_id":"sug_alpha","value":"' + surrogate + '"}'
    with pytest.raises(EvolutionLedgerError, match="noncanonical_document") as exc:
        ledger.create_or_get_blueprint_draft(
            suggestion_id="sug_alpha",
            canonical_document_json=raw,
            canonical_digest=DOCUMENT_DIGEST,
            input_digests=(DOCUMENT_DIGEST, "a" * 64),
        )
    assert surrogate not in str(exc.value)
    assert count(ledger, "attempts") == 0


def test_document_json_rejects_excessive_nesting_stably(tmp_path: Path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    raw = (
        '{"suggestion_id":"sug_alpha","value":'
        + "[" * 2000
        + "0"
        + "]" * 2000
        + "}"
    )
    with pytest.raises(EvolutionLedgerError, match="noncanonical_document"):
        ledger.create_or_get_blueprint_draft(
            suggestion_id="sug_alpha",
            canonical_document_json=raw,
            canonical_digest=DOCUMENT_DIGEST,
            input_digests=(DOCUMENT_DIGEST, "a" * 64),
        )
    assert count(ledger, "attempts") == 0


# ---------------------------------------------------------------------------
# Hardening: canonical_digest validation split
# ---------------------------------------------------------------------------


def test_invalid_canonical_digest_format_raises(tmp_path: Path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    with pytest.raises(EvolutionLedgerError, match="invalid_canonical_digest"):
        ledger.create_or_get_blueprint_draft(
            suggestion_id="sug_alpha",
            canonical_document_json=DOCUMENT_JSON,
            canonical_digest="not-a-valid-digest",
            input_digests=(DOCUMENT_DIGEST, "a" * 64),
        )
    assert count(ledger, "attempts") == 0


def test_invalid_canonical_digest_wrong_length_raises(tmp_path: Path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    with pytest.raises(EvolutionLedgerError, match="invalid_canonical_digest"):
        ledger.create_or_get_blueprint_draft(
            suggestion_id="sug_alpha",
            canonical_document_json=DOCUMENT_JSON,
            canonical_digest="a" * 63,
            input_digests=(DOCUMENT_DIGEST, "a" * 64),
        )
    assert count(ledger, "attempts") == 0


def test_invalid_canonical_digest_non_hex_raises(tmp_path: Path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    with pytest.raises(EvolutionLedgerError, match="invalid_canonical_digest"):
        ledger.create_or_get_blueprint_draft(
            suggestion_id="sug_alpha",
            canonical_document_json=DOCUMENT_JSON,
            canonical_digest="g" * 64,
            input_digests=(DOCUMENT_DIGEST, "a" * 64),
        )
    assert count(ledger, "attempts") == 0


def test_canonical_digest_correct_format_wrong_content_raises_digest_mismatch(tmp_path: Path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    wrong = content_digest(_SAMPLE_DOCUMENT, domain="wrong-domain")
    with pytest.raises(EvolutionLedgerError, match="digest_mismatch"):
        ledger.create_or_get_blueprint_draft(
            suggestion_id="sug_alpha",
            canonical_document_json=DOCUMENT_JSON,
            canonical_digest=wrong,
            input_digests=(DOCUMENT_DIGEST, "a" * 64),
        )
    assert count(ledger, "attempts") == 0


# ---------------------------------------------------------------------------
# Hardening: suggestion_document_mismatch
# ---------------------------------------------------------------------------


def test_suggestion_id_mismatch_between_param_and_document_raises(tmp_path: Path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    with pytest.raises(EvolutionLedgerError, match="suggestion_document_mismatch"):
        ledger.create_or_get_blueprint_draft(
            suggestion_id="sug_beta",
            canonical_document_json=DOCUMENT_JSON,
            canonical_digest=DOCUMENT_DIGEST,
            input_digests=(DOCUMENT_DIGEST, "a" * 64),
        )
    assert count(ledger, "attempts") == 0


# ---------------------------------------------------------------------------
# Hardening: input_digests accepts collections.abc.Sequence, rejects str/bytes
# ---------------------------------------------------------------------------


def test_input_digests_accepts_custom_sequence(tmp_path: Path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")

    class CustomSeq(collections.abc.Sequence):
        def __init__(self, items):
            self._items = list(items)

        def __getitem__(self, index):
            return self._items[index]

        def __len__(self):
            return len(self._items)

    custom = CustomSeq([DOCUMENT_DIGEST, "a" * 64])
    result = ledger.create_or_get_blueprint_draft(
        suggestion_id="sug_alpha",
        canonical_document_json=DOCUMENT_JSON,
        canonical_digest=DOCUMENT_DIGEST,
        input_digests=custom,
    )
    assert result.created is True
    assert count(ledger, "attempts") == 1


def test_input_digests_rejects_str(tmp_path: Path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    with pytest.raises(EvolutionLedgerError, match="invalid_input_digests"):
        ledger.create_or_get_blueprint_draft(
            suggestion_id="sug_alpha",
            canonical_document_json=DOCUMENT_JSON,
            canonical_digest=DOCUMENT_DIGEST,
            input_digests=DOCUMENT_DIGEST,
        )
    assert count(ledger, "attempts") == 0


def test_input_digests_rejects_bytes(tmp_path: Path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    with pytest.raises(EvolutionLedgerError, match="invalid_input_digests"):
        ledger.create_or_get_blueprint_draft(
            suggestion_id="sug_alpha",
            canonical_document_json=DOCUMENT_JSON,
            canonical_digest=DOCUMENT_DIGEST,
            input_digests=b"0" * 64,
        )
    assert count(ledger, "attempts") == 0


def test_input_digests_rejects_duplicates(tmp_path: Path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    with pytest.raises(EvolutionLedgerError, match="invalid_input_digests"):
        ledger.create_or_get_blueprint_draft(
            suggestion_id="sug_alpha",
            canonical_document_json=DOCUMENT_JSON,
            canonical_digest=DOCUMENT_DIGEST,
            input_digests=(DOCUMENT_DIGEST, DOCUMENT_DIGEST),
        )
    assert count(ledger, "attempts") == 0


def test_input_digests_empty_raises(tmp_path: Path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    with pytest.raises(EvolutionLedgerError, match="invalid_input_digests"):
        ledger.create_or_get_blueprint_draft(
            suggestion_id="sug_alpha",
            canonical_document_json=DOCUMENT_JSON,
            canonical_digest=DOCUMENT_DIGEST,
            input_digests=(),
        )
    assert count(ledger, "attempts") == 0


def test_input_digests_invalid_element_raises(tmp_path: Path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    with pytest.raises(EvolutionLedgerError, match="invalid_input_digests"):
        ledger.create_or_get_blueprint_draft(
            suggestion_id="sug_alpha",
            canonical_document_json=DOCUMENT_JSON,
            canonical_digest=DOCUMENT_DIGEST,
            input_digests=(DOCUMENT_DIGEST, "not-a-digest"),
        )
    assert count(ledger, "attempts") == 0


def test_input_digests_non_str_element_raises(tmp_path: Path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    with pytest.raises(EvolutionLedgerError, match="invalid_input_digests"):
        ledger.create_or_get_blueprint_draft(
            suggestion_id="sug_alpha",
            canonical_document_json=DOCUMENT_JSON,
            canonical_digest=DOCUMENT_DIGEST,
            input_digests=(DOCUMENT_DIGEST, 42),  # type: ignore[arg-type]
        )
    assert count(ledger, "attempts") == 0


# ---------------------------------------------------------------------------
# Hardening: incoherent replay guards
# ---------------------------------------------------------------------------


def _make_coherent_rows() -> tuple[str, str, str]:
    """Return (attempt_id, blueprint_id, now) for constructing replay tests."""
    return (
        "attempt-" + str(uuid.uuid4())[:12],
        "bp_" + str(uuid.uuid4())[:12],
        "2026-07-23T00:00:00.000000Z",
    )


def _insert_attempt(conn, attempt_id, digest, now, extra=None):
    conn.execute(
        """INSERT INTO attempts(attempt_id, source_kind, source_ref, state, created_at)
        VALUES (?, ?, ?, ?, ?)""",
        (attempt_id, "observer-proposal-v1", digest, "draft", now),
    )


def _insert_blueprint(conn, blueprint_id, attempt_id, digest, now):
    conn.execute(
        """INSERT INTO blueprints(blueprint_id, attempt_id, canonical_digest, state, created_at)
        VALUES (?, ?, ?, ?, ?)""",
        (blueprint_id, attempt_id, digest, "draft", now),
    )


def _insert_document(conn, blueprint_id, attempt_id, suggestion_id, digest, json_str, now):
    conn.execute(
        """INSERT INTO blueprint_documents(blueprint_id, attempt_id, suggestion_id, canonical_digest, canonical_document_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?)""",
        (blueprint_id, attempt_id, suggestion_id, digest, json_str, now),
    )


def _insert_lifecycle_event(conn, attempt_id, digest_list, now, event_id=None, **overrides):
    """Insert a lifecycle event row matching the blueprint_proposed template."""
    eid = event_id or str(uuid.uuid4())
    params = {
        "event_id": eid,
        "attempt_id": attempt_id,
        "generation_id": None,
        "event_type": "blueprint_proposed",
        "prior_state": None,
        "next_state": "draft",
        "actor": "operator",
        "authorization_id": None,
        "reason_code": "blueprint_proposed",
        "reason_summary": "observer proposal created",
        "created_at": now,
        "previous_event_digest": None,
        "event_digest": "e" * 64,
    }
    params.update(overrides)
    if not overrides.get("input_digests_json"):
        params["input_digests_json"] = canonical_json_bytes(list(digest_list)).decode()
    cols = [
        "event_id", "attempt_id", "generation_id", "event_type", "prior_state",
        "next_state", "actor", "input_digests_json", "authorization_id",
        "reason_code", "reason_summary", "created_at",
        "previous_event_digest", "event_digest",
    ]
    conn.execute(
        f"INSERT INTO lifecycle_events({','.join(cols)}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        tuple(params[c] for c in cols),
    )


def test_replay_incoherent_missing_lifecycle_event(tmp_path: Path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    digests = (DOCUMENT_DIGEST, "a" * 64)
    attempt_id, blueprint_id, now = _make_coherent_rows()
    with ledger.transaction() as conn:
        _insert_attempt(conn, attempt_id, DOCUMENT_DIGEST, now)
        _insert_blueprint(conn, blueprint_id, attempt_id, DOCUMENT_DIGEST, now)
        _insert_document(conn, blueprint_id, attempt_id, "sug_alpha", DOCUMENT_DIGEST, DOCUMENT_JSON, now)
        # No lifecycle event inserted

    with pytest.raises(EvolutionLedgerError, match="incoherent_blueprint_draft"):
        ledger.create_or_get_blueprint_draft(
            suggestion_id="sug_alpha",
            canonical_document_json=DOCUMENT_JSON,
            canonical_digest=DOCUMENT_DIGEST,
            input_digests=digests,
        )


def test_replay_incoherent_extra_lifecycle_event(tmp_path: Path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    digests = (DOCUMENT_DIGEST, "a" * 64)
    attempt_id, blueprint_id, now = _make_coherent_rows()
    with ledger.transaction() as conn:
        _insert_attempt(conn, attempt_id, DOCUMENT_DIGEST, now)
        _insert_blueprint(conn, blueprint_id, attempt_id, DOCUMENT_DIGEST, now)
        _insert_document(conn, blueprint_id, attempt_id, "sug_alpha", DOCUMENT_DIGEST, DOCUMENT_JSON, now)
        _insert_lifecycle_event(conn, attempt_id, digests, now, event_id="event-001", event_digest="a" * 64)
        _insert_lifecycle_event(conn, attempt_id, digests, now, event_id="event-002", event_digest="b" * 64)

    with pytest.raises(EvolutionLedgerError, match="incoherent_blueprint_draft"):
        ledger.create_or_get_blueprint_draft(
            suggestion_id="sug_alpha",
            canonical_document_json=DOCUMENT_JSON,
            canonical_digest=DOCUMENT_DIGEST,
            input_digests=digests,
        )


def test_replay_incoherent_mismatched_timestamps(tmp_path: Path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    digests = (DOCUMENT_DIGEST, "a" * 64)
    attempt_id, blueprint_id, now = _make_coherent_rows()
    other_now = "2025-01-01T00:00:00.000000Z"
    with ledger.transaction() as conn:
        _insert_attempt(conn, attempt_id, DOCUMENT_DIGEST, now)
        _insert_blueprint(conn, blueprint_id, attempt_id, DOCUMENT_DIGEST, other_now)
        _insert_document(conn, blueprint_id, attempt_id, "sug_alpha", DOCUMENT_DIGEST, DOCUMENT_JSON, now)
        _insert_lifecycle_event(conn, attempt_id, digests, now, event_digest="c" * 64)

    with pytest.raises(EvolutionLedgerError, match="incoherent_blueprint_draft"):
        ledger.create_or_get_blueprint_draft(
            suggestion_id="sug_alpha",
            canonical_document_json=DOCUMENT_JSON,
            canonical_digest=DOCUMENT_DIGEST,
            input_digests=digests,
        )


def test_replay_incoherent_wrong_event_type(tmp_path: Path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    digests = (DOCUMENT_DIGEST, "a" * 64)
    attempt_id, blueprint_id, now = _make_coherent_rows()
    with ledger.transaction() as conn:
        _insert_attempt(conn, attempt_id, DOCUMENT_DIGEST, now)
        _insert_blueprint(conn, blueprint_id, attempt_id, DOCUMENT_DIGEST, now)
        _insert_document(conn, blueprint_id, attempt_id, "sug_alpha", DOCUMENT_DIGEST, DOCUMENT_JSON, now)
        _insert_lifecycle_event(conn, attempt_id, digests, now, event_digest="d" * 64,
                                event_type="wrong_type")

    with pytest.raises(EvolutionLedgerError, match="incoherent_blueprint_draft"):
        ledger.create_or_get_blueprint_draft(
            suggestion_id="sug_alpha",
            canonical_document_json=DOCUMENT_JSON,
            canonical_digest=DOCUMENT_DIGEST,
            input_digests=digests,
        )


def test_replay_incoherent_wrong_actor(tmp_path: Path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    digests = (DOCUMENT_DIGEST, "a" * 64)
    attempt_id, blueprint_id, now = _make_coherent_rows()
    with ledger.transaction() as conn:
        _insert_attempt(conn, attempt_id, DOCUMENT_DIGEST, now)
        _insert_blueprint(conn, blueprint_id, attempt_id, DOCUMENT_DIGEST, now)
        _insert_document(conn, blueprint_id, attempt_id, "sug_alpha", DOCUMENT_DIGEST, DOCUMENT_JSON, now)
        _insert_lifecycle_event(conn, attempt_id, digests, now, event_digest="f" * 64,
                                actor="intruder")

    with pytest.raises(EvolutionLedgerError, match="incoherent_blueprint_draft"):
        ledger.create_or_get_blueprint_draft(
            suggestion_id="sug_alpha",
            canonical_document_json=DOCUMENT_JSON,
            canonical_digest=DOCUMENT_DIGEST,
            input_digests=digests,
        )


def test_replay_incoherent_wrong_input_digests(tmp_path: Path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    digests = (DOCUMENT_DIGEST, "a" * 64)
    different_digests = (DOCUMENT_DIGEST, "b" * 64)
    attempt_id, blueprint_id, now = _make_coherent_rows()
    with ledger.transaction() as conn:
        _insert_attempt(conn, attempt_id, DOCUMENT_DIGEST, now)
        _insert_blueprint(conn, blueprint_id, attempt_id, DOCUMENT_DIGEST, now)
        _insert_document(conn, blueprint_id, attempt_id, "sug_alpha", DOCUMENT_DIGEST, DOCUMENT_JSON, now)
        _insert_lifecycle_event(conn, attempt_id, different_digests, now, event_digest="g" * 64)

    with pytest.raises(EvolutionLedgerError, match="incoherent_blueprint_draft"):
        ledger.create_or_get_blueprint_draft(
            suggestion_id="sug_alpha",
            canonical_document_json=DOCUMENT_JSON,
            canonical_digest=DOCUMENT_DIGEST,
            input_digests=digests,
        )


def test_replay_incoherent_invalid_event_digest(tmp_path: Path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    digests = (DOCUMENT_DIGEST, "a" * 64)
    attempt_id, blueprint_id, now = _make_coherent_rows()
    with ledger.transaction() as conn:
        _insert_attempt(conn, attempt_id, DOCUMENT_DIGEST, now)
        _insert_blueprint(conn, blueprint_id, attempt_id, DOCUMENT_DIGEST, now)
        _insert_document(
            conn,
            blueprint_id,
            attempt_id,
            "sug_alpha",
            DOCUMENT_DIGEST,
            DOCUMENT_JSON,
            now,
        )
        _insert_lifecycle_event(
            conn,
            attempt_id,
            digests,
            now,
            event_digest="f" * 64,
        )

    with pytest.raises(EvolutionLedgerError, match="incoherent_blueprint_draft"):
        ledger.create_or_get_blueprint_draft(
            suggestion_id="sug_alpha",
            canonical_document_json=DOCUMENT_JSON,
            canonical_digest=DOCUMENT_DIGEST,
            input_digests=digests,
        )


def test_replay_incoherent_wrong_suggestion_id_in_document_row(tmp_path: Path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    digests = (DOCUMENT_DIGEST, "a" * 64)
    attempt_id, blueprint_id, now = _make_coherent_rows()
    with ledger.transaction() as conn:
        _insert_attempt(conn, attempt_id, DOCUMENT_DIGEST, now)
        _insert_blueprint(conn, blueprint_id, attempt_id, DOCUMENT_DIGEST, now)
        _insert_document(conn, blueprint_id, attempt_id, "sug_beta", DOCUMENT_DIGEST, DOCUMENT_JSON, now)
        _insert_lifecycle_event(conn, attempt_id, digests, now, event_digest="h" * 64)

    with pytest.raises(EvolutionLedgerError, match="incoherent_blueprint_draft"):
        ledger.create_or_get_blueprint_draft(
            suggestion_id="sug_alpha",
            canonical_document_json=DOCUMENT_JSON,
            canonical_digest=DOCUMENT_DIGEST,
            input_digests=digests,
        )


def test_replay_incoherent_cross_wired_blueprint_attempt(tmp_path: Path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    digests = (DOCUMENT_DIGEST, "a" * 64)
    attempt_id_1, blueprint_id, now = _make_coherent_rows()
    attempt_id_2 = "attempt-" + str(uuid.uuid4())[:12]
    with ledger.transaction() as conn:
        conn.execute(
            "INSERT INTO attempts(attempt_id, source_kind, source_ref, state, created_at) VALUES (?, ?, ?, ?, ?)",
            (attempt_id_1, "observer-proposal-v1", DOCUMENT_DIGEST, "draft", now),
        )
        conn.execute(
            "INSERT INTO attempts(attempt_id, source_kind, source_ref, state, created_at) VALUES (?, ?, ?, ?, ?)",
            (attempt_id_2, "observer-proposal-v1", DOCUMENT_DIGEST, "draft", now),
        )
        _insert_blueprint(conn, blueprint_id, attempt_id_2, DOCUMENT_DIGEST, now)
        _insert_document(conn, blueprint_id, attempt_id_1, "sug_alpha", DOCUMENT_DIGEST, DOCUMENT_JSON, now)
        _insert_lifecycle_event(conn, attempt_id_1, digests, now, event_digest="i" * 64)

    with pytest.raises(EvolutionLedgerError, match="incoherent_blueprint_draft"):
        ledger.create_or_get_blueprint_draft(
            suggestion_id="sug_alpha",
            canonical_document_json=DOCUMENT_JSON,
            canonical_digest=DOCUMENT_DIGEST,
            input_digests=digests,
        )


def test_replay_incoherent_wrong_document_json(tmp_path: Path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    digests = (DOCUMENT_DIGEST, "a" * 64)
    attempt_id, blueprint_id, now = _make_coherent_rows()
    with ledger.transaction() as conn:
        _insert_attempt(conn, attempt_id, DOCUMENT_DIGEST, now)
        _insert_blueprint(conn, blueprint_id, attempt_id, DOCUMENT_DIGEST, now)
        _insert_document(conn, blueprint_id, attempt_id, "sug_alpha", DOCUMENT_DIGEST,
                         DOCUMENT_JSON.replace('"observer-v1"', '"attacker"'), now)
        _insert_lifecycle_event(conn, attempt_id, digests, now, event_digest="j" * 64)

    with pytest.raises(EvolutionLedgerError, match="incoherent_blueprint_draft"):
        ledger.create_or_get_blueprint_draft(
            suggestion_id="sug_alpha",
            canonical_document_json=DOCUMENT_JSON,
            canonical_digest=DOCUMENT_DIGEST,
            input_digests=digests,
        )


# ---------------------------------------------------------------------------
# Repository tests (Task 4 — verified reads)
# ---------------------------------------------------------------------------


def repository_with_one_blueprint(
    tmp_path: Path,
) -> tuple[BlueprintRepository, StoredBlueprint]:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    result = ledger.create_or_get_blueprint_draft(
        suggestion_id="sug_alpha",
        canonical_document_json=DOCUMENT_JSON,
        canonical_digest=DOCUMENT_DIGEST,
        input_digests=(DOCUMENT_DIGEST, "a" * 64),
    )
    repository = BlueprintRepository(ledger)
    document = blueprint_document_from_json(DOCUMENT_JSON)
    stored = StoredBlueprint(
        blueprint_id=result.blueprint_id,
        attempt_id=result.attempt_id,
        canonical_digest=result.canonical_digest,
        state=result.state,
        created_at=result.created_at,
        document=document,
    )
    return repository, stored


def test_repository_get_reparses_and_reverifies_document(tmp_path: Path) -> None:
    repository, created = repository_with_one_blueprint(tmp_path)
    loaded = repository.get(created.blueprint_id)
    assert loaded is not None
    assert loaded.document.canonical_digest == created.canonical_digest
    assert loaded.blueprint_id == created.blueprint_id
    assert loaded.state == "draft"
    assert loaded.created_at == created.created_at


def test_repository_get_unknown_id_returns_none(tmp_path: Path) -> None:
    repository, _ = repository_with_one_blueprint(tmp_path)
    result = repository.get("bp_00000000-0000-0000-0000-000000000000")
    assert result is None


def test_repository_get_invalid_id_raises(tmp_path: Path) -> None:
    repository, _ = repository_with_one_blueprint(tmp_path)
    with pytest.raises(BlueprintRepositoryError, match="invalid_blueprint_id"):
        repository.get("")
    with pytest.raises(BlueprintRepositoryError, match="invalid_blueprint_id"):
        repository.get("naked_id")
    with pytest.raises(BlueprintRepositoryError, match="invalid_blueprint_id"):
        repository.get("no_prefix")
    with pytest.raises(BlueprintRepositoryError, match="invalid_blueprint_id"):
        repository.get(42)  # type: ignore[arg-type]


def test_repository_get_fails_closed_on_tampered_document(tmp_path: Path) -> None:
    repository, created = repository_with_one_blueprint(tmp_path)
    repository.ledger.connection.execute("DROP TRIGGER blueprint_documents_no_update")
    repository.ledger.connection.execute(
        "UPDATE blueprint_documents SET canonical_document_json = '{}' WHERE blueprint_id = ?",
        (created.blueprint_id,),
    )
    with pytest.raises(BlueprintRepositoryError, match="blueprint_document_incoherent"):
        repository.get(created.blueprint_id)


def test_repository_get_fails_on_digest_tamper(tmp_path: Path) -> None:
    repository, created = repository_with_one_blueprint(tmp_path)
    repository.ledger.connection.execute("DROP TRIGGER blueprint_documents_no_update")
    current = repository.ledger.connection.execute(
        "SELECT canonical_digest FROM blueprint_documents WHERE blueprint_id = ?",
        (created.blueprint_id,),
    ).fetchone()[0]
    tampered = ("0" if current[0] != "0" else "1") + current[1:]
    repository.ledger.connection.execute(
        "UPDATE blueprint_documents SET canonical_digest = ? WHERE blueprint_id = ?",
        (tampered, created.blueprint_id),
    )
    with pytest.raises(BlueprintRepositoryError, match="blueprint_document_incoherent"):
        repository.get(created.blueprint_id)


def test_repository_get_fails_on_suggestion_id_mismatch(tmp_path: Path) -> None:
    repository, created = repository_with_one_blueprint(tmp_path)
    repository.ledger.connection.execute("DROP TRIGGER blueprint_documents_no_update")
    repository.ledger.connection.execute(
        "UPDATE blueprint_documents SET suggestion_id = 'sug_wrong' WHERE blueprint_id = ?",
        (created.blueprint_id,),
    )
    with pytest.raises(BlueprintRepositoryError, match="blueprint_document_incoherent"):
        repository.get(created.blueprint_id)


def test_repository_get_fails_on_state_incoherence(tmp_path: Path) -> None:
    repository, created = repository_with_one_blueprint(tmp_path)
    repository.ledger.connection.execute(
        "UPDATE blueprints SET state = 'rejected' WHERE blueprint_id = ?",
        (created.blueprint_id,),
    )
    with pytest.raises(BlueprintRepositoryError, match="blueprint_document_incoherent"):
        repository.get(created.blueprint_id)


def test_repository_get_fails_on_source_kind_incoherence(tmp_path: Path) -> None:
    repository, created = repository_with_one_blueprint(tmp_path)
    row = repository.ledger.connection.execute(
        "SELECT attempt_id FROM blueprints WHERE blueprint_id = ?",
        (created.blueprint_id,),
    ).fetchone()
    assert row is not None
    attempt_id = row[0]
    repository.ledger.connection.execute(
        "UPDATE attempts SET source_kind = 'manual' WHERE attempt_id = ?",
        (attempt_id,),
    )
    with pytest.raises(BlueprintRepositoryError, match="blueprint_document_incoherent"):
        repository.get(created.blueprint_id)


def test_repository_get_fails_on_timestamp_incoherence(tmp_path: Path) -> None:
    repository, created = repository_with_one_blueprint(tmp_path)
    repository.ledger.connection.execute(
        "UPDATE blueprints SET created_at = '2025-01-01T00:00:00.000000Z' WHERE blueprint_id = ?",
        (created.blueprint_id,),
    )
    with pytest.raises(BlueprintRepositoryError, match="blueprint_document_incoherent"):
        repository.get(created.blueprint_id)


def test_repository_get_fails_when_proposal_event_is_missing(tmp_path: Path) -> None:
    repository, created = repository_with_one_blueprint(tmp_path)
    repository.ledger.connection.execute(
        "DROP TRIGGER lifecycle_events_no_delete"
    )
    repository.ledger.connection.execute(
        "DELETE FROM lifecycle_events WHERE attempt_id = ?",
        (created.attempt_id,),
    )

    with pytest.raises(BlueprintRepositoryError, match="blueprint_document_incoherent"):
        repository.get(created.blueprint_id)


def test_repository_get_rejects_matching_unsafe_record_timestamps(
    tmp_path: Path,
) -> None:
    repository, created = repository_with_one_blueprint(tmp_path)
    connection = repository.ledger.connection
    connection.execute("DROP TRIGGER blueprint_documents_no_update")
    connection.execute(
        "UPDATE attempts SET created_at = ? WHERE attempt_id = ?",
        ("/private/profile-token", created.attempt_id),
    )
    connection.execute(
        "UPDATE blueprints SET created_at = ? WHERE blueprint_id = ?",
        ("/private/profile-token", created.blueprint_id),
    )
    connection.execute(
        "UPDATE blueprint_documents SET created_at = ? WHERE blueprint_id = ?",
        ("/private/profile-token", created.blueprint_id),
    )

    with pytest.raises(BlueprintRepositoryError, match="blueprint_document_incoherent"):
        repository.get(created.blueprint_id)


def test_repository_get_no_row_when_document_missing(tmp_path: Path) -> None:
    repository, created = repository_with_one_blueprint(tmp_path)
    repository.ledger.connection.execute("DROP TRIGGER blueprint_documents_no_delete")
    repository.ledger.connection.execute(
        "DELETE FROM blueprint_documents WHERE blueprint_id = ?",
        (created.blueprint_id,),
    )
    assert repository.get(created.blueprint_id) is None


def test_repository_get_incoherent_when_joined_blueprint_missing(tmp_path: Path) -> None:
    repository, created = repository_with_one_blueprint(tmp_path)
    repository.ledger.connection.execute("PRAGMA foreign_keys=OFF")
    repository.ledger.connection.execute("DROP TRIGGER blueprint_documents_no_delete")
    repository.ledger.connection.execute(
        "DELETE FROM blueprints WHERE blueprint_id = ?",
        (created.blueprint_id,),
    )
    repository.ledger.connection.execute("PRAGMA foreign_keys=ON")
    with pytest.raises(BlueprintRepositoryError, match="blueprint_document_incoherent"):
        repository.get(created.blueprint_id)


def test_repository_list_returns_verified_blueprints(tmp_path: Path) -> None:
    repository, created = repository_with_one_blueprint(tmp_path)
    results = repository.list(limit=100)
    assert len(results) >= 1
    assert results[0].blueprint_id == created.blueprint_id
    assert results[0].canonical_digest == created.canonical_digest
    assert results[0].document == created.document


def test_repository_list_newest_first(tmp_path: Path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    doc2, dig2 = _doc_for_sid("sug_beta")
    r1 = ledger.create_or_get_blueprint_draft(
        suggestion_id="sug_alpha",
        canonical_document_json=DOCUMENT_JSON,
        canonical_digest=DOCUMENT_DIGEST,
        input_digests=(DOCUMENT_DIGEST, "a" * 64),
    )
    r2 = ledger.create_or_get_blueprint_draft(
        suggestion_id="sug_beta",
        canonical_document_json=doc2,
        canonical_digest=dig2,
        input_digests=(dig2, "a" * 64),
    )
    repository = BlueprintRepository(ledger)
    results = repository.list(limit=100)
    assert len(results) >= 2
    assert results[0].blueprint_id == r2.blueprint_id
    assert results[1].blueprint_id == r1.blueprint_id


def test_repository_list_three_items_newest_first(tmp_path: Path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    doc2, dig2 = _doc_for_sid("sug_beta")
    doc3, dig3 = _doc_for_sid("sug_gamma")
    r1 = ledger.create_or_get_blueprint_draft(
        suggestion_id="sug_alpha",
        canonical_document_json=DOCUMENT_JSON,
        canonical_digest=DOCUMENT_DIGEST,
        input_digests=(DOCUMENT_DIGEST, "a" * 64),
    )
    r2 = ledger.create_or_get_blueprint_draft(
        suggestion_id="sug_beta",
        canonical_document_json=doc2,
        canonical_digest=dig2,
        input_digests=(dig2, "a" * 64),
    )
    r3 = ledger.create_or_get_blueprint_draft(
        suggestion_id="sug_gamma",
        canonical_document_json=doc3,
        canonical_digest=dig3,
        input_digests=(dig3, "a" * 64),
    )
    repository = BlueprintRepository(ledger)
    results = repository.list(limit=100)
    assert len(results) >= 3
    assert results[0].blueprint_id == r3.blueprint_id
    assert results[1].blueprint_id == r2.blueprint_id
    assert results[2].blueprint_id == r1.blueprint_id


def test_repository_list_limit_one(tmp_path: Path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    doc2, dig2 = _doc_for_sid("sug_beta")
    r1 = ledger.create_or_get_blueprint_draft(
        suggestion_id="sug_alpha",
        canonical_document_json=DOCUMENT_JSON,
        canonical_digest=DOCUMENT_DIGEST,
        input_digests=(DOCUMENT_DIGEST, "a" * 64),
    )
    r2 = ledger.create_or_get_blueprint_draft(
        suggestion_id="sug_beta",
        canonical_document_json=doc2,
        canonical_digest=dig2,
        input_digests=(dig2, "a" * 64),
    )
    repository = BlueprintRepository(ledger)
    results = repository.list(limit=1)
    assert len(results) == 1
    assert results[0].blueprint_id == r2.blueprint_id


def test_repository_list_invalid_limits_raise(tmp_path: Path) -> None:
    repository, _ = repository_with_one_blueprint(tmp_path)
    with pytest.raises(BlueprintRepositoryError, match="invalid_list_limit"):
        repository.list(limit=0)
    with pytest.raises(BlueprintRepositoryError, match="invalid_list_limit"):
        repository.list(limit=101)
    with pytest.raises(BlueprintRepositoryError, match="invalid_list_limit"):
        repository.list(limit=True)  # type: ignore[arg-type]


def test_repository_list_missing_item_fails_closed(tmp_path: Path) -> None:
    repository, created = repository_with_one_blueprint(tmp_path)
    repository.ledger.connection.execute("PRAGMA foreign_keys=OFF")
    repository.ledger.connection.execute("DROP TRIGGER blueprint_documents_no_delete")
    repository.ledger.connection.execute(
        "DELETE FROM blueprints WHERE blueprint_id = ?",
        (created.blueprint_id,),
    )
    repository.ledger.connection.execute("PRAGMA foreign_keys=ON")
    with pytest.raises(BlueprintRepositoryError, match="blueprint_document_incoherent"):
        repository.list(limit=100)


def test_repository_list_empty_returns_empty_list(tmp_path: Path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    repository = BlueprintRepository(ledger)
    results = repository.list(limit=100)
    assert results == []


def test_repository_list_limit_negative_raises(tmp_path: Path) -> None:
    repository, _ = repository_with_one_blueprint(tmp_path)
    with pytest.raises(BlueprintRepositoryError, match="invalid_list_limit"):
        repository.list(limit=-1)


def test_repository_create_or_get_delegates_and_returns_draft(tmp_path: Path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    repository = BlueprintRepository(ledger)
    document = blueprint_document_from_json(DOCUMENT_JSON)
    result = repository.create_or_get(document)
    assert isinstance(result, BlueprintDraft)
    assert result.blueprint_id is not None
    assert result.canonical_digest == DOCUMENT_DIGEST
    assert result.state == "draft"
    assert result.created is True


def test_repository_create_or_get_is_idempotent(tmp_path: Path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    repository = BlueprintRepository(ledger)
    document = blueprint_document_from_json(DOCUMENT_JSON)
    first = repository.create_or_get(document)
    second = repository.create_or_get(document)
    assert isinstance(second, BlueprintDraft)
    assert second.blueprint_id == first.blueprint_id
    assert second.canonical_digest == first.canonical_digest
    assert second.state == first.state
    assert second.created is False


def test_repository_create_or_get_rejects_invalid_json(tmp_path: Path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    repository = BlueprintRepository(ledger)
    with pytest.raises(BlueprintContractError, match="invalid_document"):
        blueprint_document_from_json("not json")


def test_repository_get_fails_on_attempt_id_cross_wire(tmp_path: Path) -> None:
    """get() must detect when b.attempt_id != d.attempt_id."""
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    digests = (DOCUMENT_DIGEST, "a" * 64)
    repo = BlueprintRepository(ledger)

    doc2, dig2 = _doc_for_sid("sug_beta")

    r1 = ledger.create_or_get_blueprint_draft(
        suggestion_id="sug_alpha",
        canonical_document_json=DOCUMENT_JSON,
        canonical_digest=DOCUMENT_DIGEST,
        input_digests=digests,
    )
    r2 = ledger.create_or_get_blueprint_draft(
        suggestion_id="sug_beta",
        canonical_document_json=doc2,
        canonical_digest=dig2,
        input_digests=(dig2, "a" * 64),
    )

    # Cross-wire: set blueprint's attempt_id to r2.attempt_id (a valid FK target)
    # without changing d.attempt_id which still points to r1.attempt_id.
    ledger.connection.execute(
        "UPDATE blueprints SET attempt_id = ? WHERE blueprint_id = ?",
        (r2.attempt_id, r1.blueprint_id),
    )

    with pytest.raises(BlueprintRepositoryError, match="blueprint_document_incoherent"):
        repo.get(r1.blueprint_id)


def test_repository_list_tie_break_same_created_at(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two blueprints sharing the exact created_at sort by blueprint_id DESC."""
    fixed_ts = "2026-07-01T00:00:00.000000Z"
    monkeypatch.setattr("hermes_cli.evolution.ledger._now", lambda: fixed_ts)

    ledger = EvolutionLedger(tmp_path / "evolution.db")
    repo = BlueprintRepository(ledger)

    doc2, dig2 = _doc_for_sid("sug_beta")

    r1 = ledger.create_or_get_blueprint_draft(
        suggestion_id="sug_alpha",
        canonical_document_json=DOCUMENT_JSON,
        canonical_digest=DOCUMENT_DIGEST,
        input_digests=(DOCUMENT_DIGEST, "a" * 64),
    )
    r2 = ledger.create_or_get_blueprint_draft(
        suggestion_id="sug_beta",
        canonical_document_json=doc2,
        canonical_digest=dig2,
        input_digests=(dig2, "a" * 64),
    )

    results = repo.list(limit=100)
    assert len(results) >= 2
    expected = sorted([r1.blueprint_id, r2.blueprint_id], reverse=True)
    assert [r.blueprint_id for r in results[:2]] == expected


def test_repository_get_fails_on_missing_attempt_row(tmp_path: Path) -> None:
    """Deleting the attempt row while blueprint_documents remains must raise."""
    repository, created = repository_with_one_blueprint(tmp_path)
    old_fk = repository.ledger.connection.execute(
        "PRAGMA foreign_keys"
    ).fetchone()[0]
    repository.ledger.connection.execute("PRAGMA foreign_keys=OFF")
    try:
        repository.ledger.connection.execute(
            "DELETE FROM attempts WHERE attempt_id = ?",
            (created.attempt_id,),
        )
    finally:
        repository.ledger.connection.execute(f"PRAGMA foreign_keys={old_fk}")
    with pytest.raises(BlueprintRepositoryError, match="blueprint_document_incoherent"):
        repository.get(created.blueprint_id)


def test_repository_get_fails_on_attempt_state_tamper(tmp_path: Path) -> None:
    """Changing attempts.state to non-draft must be detected."""
    repository, created = repository_with_one_blueprint(tmp_path)
    repository.ledger.connection.execute(
        "UPDATE attempts SET state = 'rejected' WHERE attempt_id = ?",
        (created.attempt_id,),
    )
    with pytest.raises(BlueprintRepositoryError, match="blueprint_document_incoherent"):
        repository.get(created.blueprint_id)


def test_repository_get_fails_on_attempt_source_ref_tamper(tmp_path: Path) -> None:
    """Changing attempts.source_ref must be detected."""
    repository, created = repository_with_one_blueprint(tmp_path)
    current = repository.ledger.connection.execute(
        "SELECT source_ref FROM attempts WHERE attempt_id = ?",
        (created.attempt_id,),
    ).fetchone()[0]
    tampered = ("0" if current[0] != "0" else "1") + current[1:]
    repository.ledger.connection.execute(
        "UPDATE attempts SET source_ref = ? WHERE attempt_id = ?",
        (tampered, created.attempt_id),
    )
    with pytest.raises(BlueprintRepositoryError, match="blueprint_document_incoherent"):
        repository.get(created.blueprint_id)


def test_repository_get_fails_on_blueprint_canonical_digest_tamper(tmp_path: Path) -> None:
    """Changing blueprints.canonical_digest must be detected."""
    repository, created = repository_with_one_blueprint(tmp_path)
    current = repository.ledger.connection.execute(
        "SELECT canonical_digest FROM blueprints WHERE blueprint_id = ?",
        (created.blueprint_id,),
    ).fetchone()[0]
    tampered = ("0" if current[0] != "0" else "1") + current[1:]
    repository.ledger.connection.execute(
        "UPDATE blueprints SET canonical_digest = ? WHERE blueprint_id = ?",
        (tampered, created.blueprint_id),
    )
    with pytest.raises(BlueprintRepositoryError, match="blueprint_document_incoherent"):
        repository.get(created.blueprint_id)


def test_repository_get_fails_on_attempt_created_at_tamper(tmp_path: Path) -> None:
    """Changing attempts.created_at to a different timestamp must be detected."""
    repository, created = repository_with_one_blueprint(tmp_path)
    repository.ledger.connection.execute(
        "UPDATE attempts SET created_at = '2025-01-01T00:00:00.000000Z' WHERE attempt_id = ?",
        (created.attempt_id,),
    )
    with pytest.raises(BlueprintRepositoryError, match="blueprint_document_incoherent"):
        repository.get(created.blueprint_id)


def test_repository_create_or_get_rejects_wrong_object(tmp_path: Path) -> None:
    """Passing a non-BlueprintDocument object must raise BlueprintRepositoryError."""
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    repository = BlueprintRepository(ledger)
    with pytest.raises(BlueprintRepositoryError, match="invalid_document"):
        repository.create_or_get("not a document")  # type: ignore[arg-type]


def test_repository_create_or_get_rejects_invalid_blueprint_document(tmp_path: Path) -> None:
    """A manually constructed BlueprintDocument with invalid fields must raise."""
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    repository = BlueprintRepository(ledger)
    valid = blueprint_document_from_json(DOCUMENT_JSON)
    bad = replace(valid, origin="attacker-v1")
    with pytest.raises(BlueprintRepositoryError, match="invalid_origin"):
        repository.create_or_get(bad)


def test_repository_create_or_get_rejects_wrong_schema_version(tmp_path: Path) -> None:
    """A BlueprintDocument with wrong schema_version must raise with mapped code."""
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    repository = BlueprintRepository(ledger)
    valid = blueprint_document_from_json(DOCUMENT_JSON)
    bad = replace(valid, schema_version=99)
    with pytest.raises(BlueprintRepositoryError, match="invalid_schema_version"):
        repository.create_or_get(bad)


def test_repository_blueprint_repository_error_code() -> None:
    err = BlueprintRepositoryError("test_code")
    assert err.code == "test_code"
    assert str(err) == "test_code"
