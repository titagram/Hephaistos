from __future__ import annotations

import inspect
import sqlite3
import threading
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from hermes_cli.evolution import ledger as ledger_module
from hermes_cli.evolution.ledger import (
    EvolutionLedger,
    EvolutionLedgerError,
    LifecycleEvent,
)
from hermes_cli.evolution.state_machine import TransitionRequest


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
        created_at="2026-07-23T00:00:00.000000Z",
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
    ledger.connection.execute("DROP TRIGGER lifecycle_events_no_update")
    ledger.connection.execute(
        """
        UPDATE lifecycle_events
        SET reason_summary = 'changed'
        WHERE event_sequence = ?
        """,
        (first.event_sequence,),
    )

    assert ledger.verify_chain() == ["1"]


def test_verify_chain_has_the_exact_public_signature() -> None:
    assert list(inspect.signature(EvolutionLedger.verify_chain).parameters) == [
        "self"
    ]


def test_authorization_grants_are_immutable_in_real_sqlite(tmp_path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    attempt_id = ledger.create_attempt("manual", "ticket-1")
    ledger.connection.execute(
        """
        INSERT INTO authorization_requests(
            request_id, attempt_id, grant_kind, subject_digest, scope_json,
            ttl_seconds, expires_at, created_at
        ) VALUES (
            'request-1', ?, 'research', ?, ?, 60,
            '2026-07-23T00:01:00.000000Z',
            '2026-07-23T00:00:00.000000Z'
        )
        """,
        (
            attempt_id,
            DIGEST,
            '{"domains":[],"duration":60,"operations":["search","retrieve"],'
            '"source_classes":["documentation"]}',
        ),
    )
    ledger.connection.execute(
        """
        INSERT INTO authorization_decisions(
            decision_id, request_id, decision, decided_by,
            confirmation_digest, created_at
        ) VALUES (
            'decision-1', 'request-1', 'approved', 'operator', ?,
            '2026-07-23T00:00:00.000000Z'
        )
        """,
        (DIGEST,),
    )
    ledger.connection.execute(
        """
        INSERT INTO authorization_grants(
            grant_id, authorization_id, request_id, attempt_id, grant_kind,
            subject_digest, scope_json, expires_at, approved_by,
            confirmation_digest, created_at
        ) VALUES (
            ?, ?, 'request-1', ?, 'research', ?, ?,
            '2026-07-23T00:01:00.000000Z', 'operator', ?,
            '2026-07-23T00:00:00.000000Z'
        )
        """,
        (
            "grant-1",
            "grant-1",
            attempt_id,
            DIGEST,
            '{"domains":[],"duration":60,"operations":["search","retrieve"],'
            '"source_classes":["documentation"]}',
            DIGEST,
        ),
    )

    with pytest.raises(sqlite3.IntegrityError, match="immutable_authorization_grant"):
        ledger.connection.execute(
            "UPDATE authorization_grants SET consumed_at = ? WHERE authorization_id = ?",
            ("2026-07-23T00:01:00Z", "grant-1"),
        )
    with pytest.raises(sqlite3.IntegrityError, match="immutable_authorization_grant"):
        ledger.connection.execute(
            "DELETE FROM authorization_grants WHERE authorization_id = ?",
            ("grant-1",),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("event_id", " " ),
        ("attempt_id", "x" * 257),
        ("generation_id", "not-a-digest"),
        ("event_type", "x" * 129),
        ("actor", "operator\nsecret"),
        ("authorization_id", "x" * 257),
        ("reason_code", "x" * 129),
    ],
)
def test_append_rejects_unbounded_or_noncanonical_identity_fields(
    tmp_path, field: str, value: str
) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    attempt_id = ledger.create_attempt("manual", "ticket-1")
    malformed = replace(
        event(event_id="event-1", attempt_id=attempt_id),
        **{field: value},
    )

    with pytest.raises(EvolutionLedgerError, match="invalid_event"):
        ledger.append_event(malformed)
    assert ledger.history() == []


@pytest.mark.parametrize(
    "created_at",
    [
        "2026-07-23T00:00:00Z",
        "2026-07-23T00:00:00.1Z",
        "2026-07-23T00:00:00.12345Z",
        "2026-07-23T00:00:00.1234567Z",
        "2026-07-23T00:00:00.000000+00:00",
        "2026-07-23T00:00:00.000000z",
        " 2026-07-23T00:00:00.000000Z",
        "2026-07-23 00:00:00.000000Z",
        "2026-02-30T00:00:00.000000Z",
        "2026-07-23T24:00:00.000000Z",
        "x" * 65,
        None,
    ],
)
def test_append_rejects_noncanonical_or_invalid_timestamp(
    tmp_path, created_at: object
) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    attempt_id = ledger.create_attempt("manual", "ticket-1")
    malformed = replace(
        event(event_id="event-1", attempt_id=attempt_id),
        created_at=created_at,
    )

    with pytest.raises(
        EvolutionLedgerError, match="invalid_event_timestamp"
    ):
        ledger.append_event(malformed)
    assert ledger.history() == []


def test_generated_timestamp_has_canonical_utc_microseconds(monkeypatch) -> None:
    class FrozenDateTime:
        @classmethod
        def now(cls, timezone):
            assert timezone is UTC
            return datetime(2026, 7, 23, 0, 0, 0, 0, tzinfo=UTC)

    monkeypatch.setattr(ledger_module, "datetime", FrozenDateTime)

    generated = ledger_module._now()

    assert generated == "2026-07-23T00:00:00.000000Z"


def test_canonical_timestamp_round_trips_through_hash_chain(tmp_path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    attempt_id = ledger.create_attempt("manual", "ticket-1")
    expected = "2026-07-23T12:34:56.123456Z"

    stored = ledger.append_event(
        replace(
            event(event_id="event-1", attempt_id=attempt_id),
            created_at=expected,
        )
    )

    assert stored.created_at == expected
    assert ledger.history()[0].created_at == expected
    assert ledger.verify_chain() == []


def test_append_bounds_digest_count(tmp_path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    attempt_id = ledger.create_attempt("manual", "ticket-1")
    malformed = replace(
        event(event_id="event-1", attempt_id=attempt_id),
        input_digests=(DIGEST,) * 65,
    )

    with pytest.raises(EvolutionLedgerError, match="invalid_event_digests"):
        ledger.append_event(malformed)
    assert ledger.history() == []


def test_append_accepts_the_bounded_digest_count_limit(tmp_path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    attempt_id = ledger.create_attempt("manual", "ticket-1")
    bounded = replace(
        event(event_id="event-1", attempt_id=attempt_id),
        input_digests=(DIGEST,) * 64,
    )

    stored = ledger.append_event(bounded)

    assert len(stored.input_digests) == 64
    assert ledger.verify_chain() == []


def test_append_hashes_the_same_normalized_values_it_persists(tmp_path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    attempt_id = ledger.create_attempt(" manual ", " ticket-1 ")

    stored = ledger.append_event(
        replace(
            event(event_id=" event-1 ", attempt_id=attempt_id),
            event_type=" attempt_recorded ",
            actor=" operator ",
            reason_code=" created ",
            reason_summary="  started   safely  ",
        )
    )

    assert stored.event_id == "event-1"
    assert stored.event_type == "attempt_recorded"
    assert stored.actor == "operator"
    assert stored.reason_code == "created"
    assert stored.reason_summary == "started safely"
    assert ledger.verify_chain() == []
    row = ledger.connection.execute(
        "SELECT source_kind, source_ref FROM attempts WHERE attempt_id = ?",
        (attempt_id,),
    ).fetchone()
    assert tuple(row) == ("manual", "ticket-1")


@pytest.mark.parametrize(
    "source_ref",
    [
        "/Users/alice/private/ticket",
        "file:///tmp/ticket",
        "https://example.invalid/ticket",
        "../ticket",
        "tickets/one",
        r"tickets\one",
        "C:\\ticket",
    ],
)
def test_create_attempt_rejects_path_like_source_refs_without_storing_them(
    tmp_path, source_ref: str
) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")

    with pytest.raises(EvolutionLedgerError, match="invalid_attempt_source") as error:
        ledger.create_attempt("manual", source_ref)

    assert source_ref not in str(error.value)
    assert ledger.connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 0


def test_transition_rolls_back_state_when_event_append_fails(
    tmp_path, monkeypatch
) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    attempt_id = ledger.create_attempt("manual", "ticket-1")

    def fail_append(*_args, **_kwargs):
        raise RuntimeError("injected")

    monkeypatch.setattr(ledger, "_append", fail_append)
    request = TransitionRequest(
        attempt_id=attempt_id,
        prior_state="draft",
        next_state="rejected",
        actor="operator",
        input_digests=(DIGEST,),
        authorization_id=None,
        reason="not suitable",
    )

    with pytest.raises(RuntimeError, match="injected"):
        ledger.transition(request)
    assert ledger.connection.execute(
        "SELECT state FROM attempts WHERE attempt_id = ?", (attempt_id,)
    ).fetchone()[0] == "draft"


def test_transaction_rolls_back_base_exception_and_remains_usable(tmp_path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")

    class StopNow(BaseException):
        pass

    with pytest.raises(StopNow):
        with ledger.transaction() as connection:
            connection.execute(
                """
                INSERT INTO attempts(
                    attempt_id, source_kind, source_ref, state, created_at
                ) VALUES ('doomed', 'manual', 'ticket-1', 'draft', 'now')
                """
            )
            raise StopNow

    assert ledger.create_attempt("manual", "ticket-2")
    assert ledger.connection.execute(
        "SELECT COUNT(*) FROM attempts WHERE attempt_id = 'doomed'"
    ).fetchone()[0] == 0


def test_transaction_recovers_from_real_deferred_constraint_commit_failure(
    tmp_path,
) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")

    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        with ledger.transaction() as connection:
            connection.execute("PRAGMA defer_foreign_keys=ON")
            connection.execute(
                """
                INSERT INTO candidates(
                    candidate_id, attempt_id, state, created_at
                ) VALUES ('candidate-1', 'missing', 'draft', 'now')
                """
            )

    assert not ledger.connection.in_transaction
    assert ledger.create_attempt("manual", "ticket-2")


def test_separate_ledgers_append_concurrently_without_breaking_chain(tmp_path) -> None:
    path = tmp_path / "evolution.db"
    first = EvolutionLedger(path)
    attempt_id = first.create_attempt("manual", "ticket-1")
    second = EvolutionLedger(path)
    barrier = threading.Barrier(2)
    failures: list[BaseException] = []

    def append_many(ledger: EvolutionLedger, prefix: str) -> None:
        try:
            barrier.wait()
            for index in range(25):
                ledger.append_event(
                    event(event_id=f"{prefix}-{index}", attempt_id=attempt_id)
                )
        except BaseException as exc:
            failures.append(exc)

    threads = [
        threading.Thread(target=append_many, args=(first, "first")),
        threading.Thread(target=append_many, args=(second, "second")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert failures == []
    assert len(first.history(limit=1000)) == 50
    assert first.verify_chain() == []


def test_same_instance_serializes_transactions(tmp_path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    attempt_id = ledger.create_attempt("manual", "ticket-1")
    barrier = threading.Barrier(2)
    failures: list[BaseException] = []

    def append_one(identifier: str) -> None:
        try:
            barrier.wait()
            ledger.append_event(event(event_id=identifier, attempt_id=attempt_id))
        except BaseException as exc:
            failures.append(exc)

    threads = [
        threading.Thread(target=append_one, args=("event-1",)),
        threading.Thread(target=append_one, args=("event-2",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert failures == []
    assert len(ledger.history()) == 2
    assert ledger.verify_chain() == []


def test_verify_chain_streams_past_one_thousand_events(tmp_path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    attempt_id = ledger.create_attempt("manual", "ticket-1")
    with ledger.transaction() as connection:
        for index in range(1005):
            ledger._append(
                connection,
                event(event_id=f"event-{index}", attempt_id=attempt_id),
            )
    ledger.connection.execute("DROP TRIGGER lifecycle_events_no_update")
    ledger.connection.execute(
        """
        UPDATE lifecycle_events
        SET reason_summary = 'corrupted'
        WHERE event_sequence = 1001
        """
    )

    assert ledger.verify_chain() == ["1001"]


def test_verify_chain_preserves_order_around_malformed_payloads(tmp_path) -> None:
    ledger = EvolutionLedger(tmp_path / "evolution.db")
    attempt_id = ledger.create_attempt("manual", "ticket-1")
    for index in range(3):
        ledger.append_event(
            event(event_id=f"event-{index}", attempt_id=attempt_id)
        )
    ledger.connection.execute("DROP TRIGGER lifecycle_events_no_update")
    ledger.connection.execute(
        """
        UPDATE lifecycle_events
        SET input_digests_json = 'not-json'
        WHERE event_sequence = 2
        """
    )

    assert ledger.verify_chain() == ["2"]
