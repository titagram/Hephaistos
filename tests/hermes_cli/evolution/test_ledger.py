from __future__ import annotations

import sqlite3
from dataclasses import replace

import pytest

from hermes_cli.evolution.ledger import EvolutionLedger, LifecycleEvent


DIGEST = "0123456789abcdef" * 4


def event(*, event_id: str, attempt_id: str, reason_summary: str = "started") -> LifecycleEvent:
    return LifecycleEvent(
        event_id=event_id,
        attempt_id=attempt_id,
        generation_id=None,
        event_type="attempt_recorded",
        prior_state=None,
        next_state="draft",
        actor="operator",
        input_digests=(DIGEST,),
        authorization_id=None,
        reason_code="created",
        reason_summary=reason_summary,
        created_at="2026-07-23T00:00:00Z",
    )


def test_append_records_an_immutable_hash_chain(tmp_path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    attempt_id = ledger.create_attempt("manual", "ticket-1")

    first = ledger.append_event(event(event_id="event-1", attempt_id=attempt_id))
    second = ledger.append_event(event(event_id="event-2", attempt_id=attempt_id))

    assert second.previous_event_digest == first.event_digest
    with pytest.raises(sqlite3.IntegrityError):
        ledger.connection.execute(
            "UPDATE lifecycle_events SET reason_summary = 'changed' WHERE event_id = ?",
            (first.event_id,),
        )
    with pytest.raises(sqlite3.IntegrityError):
        ledger.connection.execute(
            "DELETE FROM lifecycle_events WHERE event_id = ?", (first.event_id,)
        )


def test_verify_chain_identifies_the_changed_sequence(tmp_path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    attempt_id = ledger.create_attempt("manual", "ticket-1")
    first = ledger.append_event(event(event_id="event-1", attempt_id=attempt_id))
    ledger.append_event(event(event_id="event-2", attempt_id=attempt_id))

    copied = replace(first, reason_summary="changed")
    assert ledger.verify_chain([copied]) == ["1"]
