from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Mapping

import pytest

from hermes_cli.evolution import authorization
from hermes_cli.evolution.authorization import (
    AuthorizationError,
    AuthorizationGrant,
    create_authorization_request,
    deny_authorization_request,
    issue_grant,
    consume_grant,
)
from hermes_cli.evolution.contract import canonical_json_bytes, content_digest
from hermes_cli.evolution.ledger import EvolutionLedger


NOW = "2026-07-23T10:00:00.000000Z"
LATER = "2026-07-23T10:02:01.000000Z"
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64


def research_scope(**changes: object) -> dict[str, object]:
    scope: dict[str, object] = {
        "source_classes": ["documentation", "paper"],
        "domains": ["example.com", "research.example"],
        "operations": ["search", "retrieve"],
        "duration": 60,
    }
    scope.update(changes)
    return scope


def build_scope(**changes: object) -> dict[str, object]:
    scope: dict[str, object] = {
        "component_classes": ["skill", "script"],
        "source_families": ["official-docs", "signed-release"],
        "dependency_families": ["python"],
        "workspace_class": "candidate-only",
        "isolation_policy": {
            "network": "deny",
            "subprocess": False,
        },
        "side_effects": ["candidate_write", "quarantine_write"],
        "resource_limits": {"duration_seconds": 120, "memory_mb": 512},
    }
    scope.update(changes)
    return scope


def promotion_scope(**changes: object) -> dict[str, object]:
    scope: dict[str, object] = {
        "generation_id": DIGEST_A,
        "report_digest": DIGEST_B,
        "expected_active_id": DIGEST_C,
        "expected_lifecycle_sequence": 14,
        "operation": "switch_active",
    }
    scope.update(changes)
    return scope


@pytest.fixture
def ledger(tmp_path, monkeypatch) -> EvolutionLedger:
    monkeypatch.setattr(authorization, "_now", lambda: NOW)
    value = EvolutionLedger(tmp_path / "evolution.db")
    value.attempt_id = value.create_attempt("manual", "ticket-1")
    return value


def request_and_issue(
    ledger: EvolutionLedger,
    *,
    kind: str = "research",
    subject_digest: str = DIGEST_A,
    scope: Mapping[str, object] | None = None,
) -> tuple[object, AuthorizationGrant]:
    selected_scope = scope or research_scope()
    request = create_authorization_request(
        ledger,
        attempt_id=ledger.attempt_id,
        kind=kind,
        subject_digest=subject_digest,
        scope=selected_scope,
        ttl_seconds=120,
    )
    confirmation = content_digest(
        request.canonical_payload(),
        domain="hades-evolution-authorization-request-v1",
    )
    return request, issue_grant(
        ledger,
        request_id=request.request_id,
        approved_by="local-operator",
        confirmation_digest=confirmation,
    )


def test_request_and_grant_are_digest_bound_and_emit_atomic_events(
    ledger: EvolutionLedger,
) -> None:
    request, grant = request_and_issue(ledger)

    assert request.kind == "research"
    assert request.subject_digest == DIGEST_A
    assert grant.request_id == request.request_id
    assert grant.scope == request.scope
    assert grant.consumed_at is None
    assert [
        event.event_type for event in ledger.history()
    ] == ["authorization_requested", "authorization_granted"]
    assert ledger.verify_chain() == []


def test_confirmation_digest_must_match_canonical_request(
    ledger: EvolutionLedger,
) -> None:
    request = create_authorization_request(
        ledger,
        attempt_id=ledger.attempt_id,
        kind="research",
        subject_digest=DIGEST_A,
        scope=research_scope(),
        ttl_seconds=120,
    )

    with pytest.raises(AuthorizationError, match="confirmation_mismatch"):
        issue_grant(
            ledger,
            request_id=request.request_id,
            approved_by="local-operator",
            confirmation_digest=DIGEST_B,
        )

    assert ledger.connection.execute(
        "SELECT COUNT(*) FROM authorization_grants"
    ).fetchone()[0] == 0


@pytest.mark.parametrize(
    ("kind", "scope"),
    [
        ("research", research_scope()),
        ("build", build_scope()),
        ("promotion", promotion_scope()),
    ],
)
def test_each_closed_scope_round_trips_as_deeply_immutable(
    ledger: EvolutionLedger,
    kind: str,
    scope: dict[str, object],
) -> None:
    request = create_authorization_request(
        ledger,
        attempt_id=ledger.attempt_id,
        kind=kind,
        subject_digest=DIGEST_A,
        scope=scope,
        ttl_seconds=120,
    )

    assert request.canonical_payload()["scope"] == json.loads(
        canonical_json_bytes(scope)
    )
    with pytest.raises(TypeError):
        request.scope["new"] = "authority"
    for value in request.scope.values():
        assert not isinstance(value, (dict, list, set))


@pytest.mark.parametrize(
    ("kind", "scope"),
    [
        ("research", research_scope(extra="authority")),
        ("build", build_scope(extra="authority")),
        ("promotion", promotion_scope(extra="authority")),
        ("research", research_scope(source_classes=["paper", "paper"])),
        ("research", research_scope(domains=["HTTPS://example.com"])),
        ("research", research_scope(operations=["retrieve", "search"])),
        ("research", research_scope(duration=True)),
        ("build", build_scope(component_classes=["wheel"])),
        ("build", build_scope(source_families=["https://example.com/pkg"])),
        ("build", build_scope(workspace_class="/tmp/candidate")),
        ("build", build_scope(resource_limits={"memory_mb": 0})),
        ("promotion", promotion_scope(operation="copy_then_switch")),
        ("promotion", promotion_scope(expected_lifecycle_sequence=True)),
    ],
)
def test_closed_scopes_reject_unknown_noncanonical_or_unsafe_authority(
    ledger: EvolutionLedger,
    kind: str,
    scope: dict[str, object],
) -> None:
    with pytest.raises(AuthorizationError, match="invalid_scope"):
        create_authorization_request(
            ledger,
            attempt_id=ledger.attempt_id,
            kind=kind,
            subject_digest=DIGEST_A,
            scope=scope,
            ttl_seconds=120,
        )


def test_kind_mismatch_and_wrong_subject_fail_closed(
    ledger: EvolutionLedger,
) -> None:
    _, grant = request_and_issue(ledger)

    with pytest.raises(AuthorizationError, match="grant_unavailable"):
        consume_grant(
            ledger,
            grant_id=grant.grant_id,
            expected_kind="build",
            expected_subject_digest=DIGEST_A,
            required_scope=build_scope(),
        )
    with pytest.raises(AuthorizationError, match="grant_unavailable"):
        consume_grant(
            ledger,
            grant_id=grant.grant_id,
            expected_kind="research",
            expected_subject_digest=DIGEST_B,
            required_scope=research_scope(),
        )


def test_expired_request_cannot_be_issued(ledger, monkeypatch) -> None:
    request = create_authorization_request(
        ledger,
        attempt_id=ledger.attempt_id,
        kind="research",
        subject_digest=DIGEST_A,
        scope=research_scope(),
        ttl_seconds=60,
    )
    confirmation = content_digest(
        request.canonical_payload(),
        domain="hades-evolution-authorization-request-v1",
    )
    monkeypatch.setattr(authorization, "_now", lambda: LATER)

    with pytest.raises(AuthorizationError, match="request_unavailable"):
        issue_grant(
            ledger,
            request_id=request.request_id,
            approved_by="local-operator",
            confirmation_digest=confirmation,
        )


def test_expired_grant_cannot_be_consumed(ledger, monkeypatch) -> None:
    _, grant = request_and_issue(ledger)
    monkeypatch.setattr(authorization, "_now", lambda: LATER)

    with pytest.raises(AuthorizationError, match="grant_unavailable"):
        consume_grant(
            ledger,
            grant_id=grant.grant_id,
            expected_kind="research",
            expected_subject_digest=DIGEST_A,
            required_scope=research_scope(duration=30),
        )


def test_denial_is_immutable_and_prevents_issue(ledger: EvolutionLedger) -> None:
    request = create_authorization_request(
        ledger,
        attempt_id=ledger.attempt_id,
        kind="research",
        subject_digest=DIGEST_A,
        scope=research_scope(),
        ttl_seconds=120,
    )
    decision = deny_authorization_request(
        ledger,
        request_id=request.request_id,
        decided_by="local-operator",
    )
    confirmation = content_digest(
        request.canonical_payload(),
        domain="hades-evolution-authorization-request-v1",
    )

    assert decision.decision == "denied"
    with pytest.raises(AuthorizationError, match="request_unavailable"):
        issue_grant(
            ledger,
            request_id=request.request_id,
            approved_by="local-operator",
            confirmation_digest=confirmation,
        )
    with pytest.raises(sqlite3.IntegrityError, match="immutable_authorization_decision"):
        ledger.connection.execute(
            "UPDATE authorization_decisions SET decision = 'approved'"
        )


def test_request_cannot_be_issued_twice(ledger: EvolutionLedger) -> None:
    request, _ = request_and_issue(ledger)
    confirmation = content_digest(
        request.canonical_payload(),
        domain="hades-evolution-authorization-request-v1",
    )

    with pytest.raises(AuthorizationError, match="request_unavailable"):
        issue_grant(
            ledger,
            request_id=request.request_id,
            approved_by="local-operator",
            confirmation_digest=confirmation,
        )


def test_consumption_is_single_use_and_grant_row_stays_immutable(
    ledger: EvolutionLedger,
) -> None:
    _, grant = request_and_issue(ledger)
    consumed = consume_grant(
        ledger,
        grant_id=grant.grant_id,
        expected_kind="research",
        expected_subject_digest=DIGEST_A,
        required_scope=research_scope(duration=30),
    )

    assert consumed.consumed_at == NOW
    persisted = ledger.connection.execute(
        "SELECT * FROM authorization_grants WHERE grant_id = ?",
        (grant.grant_id,),
    ).fetchone()
    assert persisted["consumed_at"] is None
    with pytest.raises(AuthorizationError, match="grant_unavailable"):
        consume_grant(
            ledger,
            grant_id=grant.grant_id,
            expected_kind="research",
            expected_subject_digest=DIGEST_A,
            required_scope=research_scope(duration=30),
        )
    with pytest.raises(sqlite3.IntegrityError, match="immutable_authorization_grant"):
        ledger.connection.execute(
            "UPDATE authorization_grants SET expires_at = ?",
            (LATER,),
        )


@pytest.mark.parametrize(
    ("kind", "granted", "required"),
    [
        ("research", research_scope(), research_scope(source_classes=["paper"], domains=[])),
        (
            "build",
            build_scope(),
            build_scope(
                component_classes=["skill"],
                source_families=["official-docs"],
                dependency_families=[],
                side_effects=["candidate_write"],
                resource_limits={"duration_seconds": 60, "memory_mb": 256},
            ),
        ),
    ],
)
def test_research_and_build_allow_structurally_narrower_scopes(
    ledger: EvolutionLedger,
    kind: str,
    granted: dict[str, object],
    required: dict[str, object],
) -> None:
    _, grant = request_and_issue(ledger, kind=kind, scope=granted)

    consumed = consume_grant(
        ledger,
        grant_id=grant.grant_id,
        expected_kind=kind,
        expected_subject_digest=DIGEST_A,
        required_scope=required,
    )

    assert consumed.consumed_at == NOW


@pytest.mark.parametrize(
    ("kind", "granted", "required"),
    [
        ("research", research_scope(source_classes=["paper"]), research_scope()),
        ("research", research_scope(duration=30), research_scope(duration=60)),
        (
            "build",
            build_scope(component_classes=["skill"]),
            build_scope(),
        ),
        (
            "build",
            build_scope(resource_limits={"memory_mb": 256}),
            build_scope(resource_limits={"memory_mb": 512}),
        ),
        (
            "build",
            build_scope(),
            build_scope(isolation_policy={"network": "allow", "subprocess": False}),
        ),
        (
            "promotion",
            promotion_scope(),
            promotion_scope(expected_lifecycle_sequence=15),
        ),
    ],
)
def test_broader_or_nonmatching_required_scope_is_rejected(
    ledger: EvolutionLedger,
    kind: str,
    granted: dict[str, object],
    required: dict[str, object],
) -> None:
    _, grant = request_and_issue(ledger, kind=kind, scope=granted)

    with pytest.raises(AuthorizationError, match="grant_unavailable"):
        consume_grant(
            ledger,
            grant_id=grant.grant_id,
            expected_kind=kind,
            expected_subject_digest=DIGEST_A,
            required_scope=required,
        )


def test_scope_input_mutation_after_issue_cannot_widen_grant(
    ledger: EvolutionLedger,
) -> None:
    scope = research_scope(source_classes=["paper"])
    _, grant = request_and_issue(ledger, scope=scope)
    scope["source_classes"].append("documentation")

    assert grant.scope["source_classes"] == ("paper",)
    with pytest.raises(AuthorizationError, match="grant_unavailable"):
        consume_grant(
            ledger,
            grant_id=grant.grant_id,
            expected_kind="research",
            expected_subject_digest=DIGEST_A,
            required_scope=scope,
        )


@pytest.mark.parametrize(
    ("operation", "blocked_event"),
    [
        ("request", "authorization_requested"),
        ("grant", "authorization_granted"),
        ("denial", "authorization_denied"),
        ("consumption", "authorization_consumed"),
    ],
)
def test_authorization_fact_and_matching_event_are_atomic(
    ledger: EvolutionLedger,
    operation: str,
    blocked_event: str,
) -> None:
    request = None
    grant = None
    if operation != "request":
        request = create_authorization_request(
            ledger,
            attempt_id=ledger.attempt_id,
            kind="research",
            subject_digest=DIGEST_A,
            scope=research_scope(),
            ttl_seconds=120,
        )
    if operation == "consumption":
        confirmation = content_digest(
            request.canonical_payload(),
            domain="hades-evolution-authorization-request-v1",
        )
        grant = issue_grant(
            ledger,
            request_id=request.request_id,
            approved_by="local-operator",
            confirmation_digest=confirmation,
        )
    ledger.connection.execute(
        f"""
        CREATE TRIGGER reject_test_event
        BEFORE INSERT ON lifecycle_events
        WHEN NEW.event_type = '{blocked_event}'
        BEGIN SELECT RAISE(ABORT, 'blocked_test_event'); END
        """
    )

    with pytest.raises(sqlite3.IntegrityError, match="blocked_test_event"):
        if operation == "request":
            create_authorization_request(
                ledger,
                attempt_id=ledger.attempt_id,
                kind="research",
                subject_digest=DIGEST_A,
                scope=research_scope(),
                ttl_seconds=120,
            )
        elif operation == "grant":
            confirmation = content_digest(
                request.canonical_payload(),
                domain="hades-evolution-authorization-request-v1",
            )
            issue_grant(
                ledger,
                request_id=request.request_id,
                approved_by="local-operator",
                confirmation_digest=confirmation,
            )
        elif operation == "denial":
            deny_authorization_request(
                ledger,
                request_id=request.request_id,
                decided_by="local-operator",
            )
        else:
            consume_grant(
                ledger,
                grant_id=grant.grant_id,
                expected_kind="research",
                expected_subject_digest=DIGEST_A,
                required_scope=research_scope(),
            )

    table = {
        "request": "authorization_requests",
        "grant": "authorization_grants",
        "denial": "authorization_decisions",
        "consumption": "authorization_consumptions",
    }[operation]
    assert ledger.connection.execute(
        f"SELECT COUNT(*) FROM {table}"
    ).fetchone()[0] == 0


def test_model_statement_cannot_substitute_for_issued_grant(
    ledger: EvolutionLedger,
) -> None:
    with pytest.raises(AuthorizationError, match="grant_unavailable"):
        consume_grant(
            ledger,
            grant_id="The user approved this research.",
            expected_kind="research",
            expected_subject_digest=DIGEST_A,
            required_scope=research_scope(),
        )


def test_two_connections_racing_to_consume_have_exactly_one_winner(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(authorization, "_now", lambda: NOW)
    path = tmp_path / "evolution.db"
    first = EvolutionLedger(path)
    first.attempt_id = first.create_attempt("manual", "ticket-1")
    _, grant = request_and_issue(first)
    second = EvolutionLedger(path)
    barrier = threading.Barrier(2)
    results: list[str] = []
    lock = threading.Lock()

    def race(candidate: EvolutionLedger) -> None:
        barrier.wait()
        try:
            consume_grant(
                candidate,
                grant_id=grant.grant_id,
                expected_kind="research",
                expected_subject_digest=DIGEST_A,
                required_scope=research_scope(duration=30),
            )
        except AuthorizationError as exc:
            outcome = exc.code
        else:
            outcome = "consumed"
        with lock:
            results.append(outcome)

    threads = [
        threading.Thread(target=race, args=(candidate,))
        for candidate in (first, second)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert all(not thread.is_alive() for thread in threads)
    assert sorted(results) == ["consumed", "grant_unavailable"]
    assert first.connection.execute(
        "SELECT COUNT(*) FROM authorization_consumptions"
    ).fetchone()[0] == 1
    assert first.verify_chain() == []
