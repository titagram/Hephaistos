from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Mapping
from contextlib import contextmanager

import pytest

from hermes_cli.evolution import authorization
from hermes_cli.evolution.authorization import (
    AuthorizationDecision,
    AuthorizationError,
    AuthorizationGrant,
    create_authorization_request,
    deny_authorization_request,
    issue_grant,
    consume_grant,
)
from hermes_cli.evolution.contract import canonical_json_bytes, content_digest
from hermes_cli.evolution.ledger import EvolutionLedger, LifecycleEvent


NOW = "2026-07-23T10:00:00.000000Z"
AFTER_LOCK = "2026-07-23T10:01:00.000000Z"
LATER = "2026-07-23T10:02:01.000000Z"
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
SEPARATED_OPAQUE = "a1b2c3d4-e5f6g7h8-i9j0k1l2-m3n4o5p6"
TWO_CHUNK_OPAQUE = "a1b2c3d4e5f6g7h8-i9j0k1l2m3n4o5p6"
BASE32_OPAQUE = "mfrggzdfmztwq2lkorsxg5banvuw63tp"


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


def test_issue_rechecks_expiry_after_waiting_for_sqlite_write_lock(
    tmp_path, monkeypatch
) -> None:
    clock = {"value": NOW}
    monkeypatch.setattr(authorization, "_now", lambda: clock["value"])
    path = tmp_path / "evolution.db"
    first = EvolutionLedger(path)
    attempt_id = first.create_attempt("manual", "ticket-1")
    request = create_authorization_request(
        first,
        attempt_id=attempt_id,
        kind="research",
        subject_digest=DIGEST_A,
        scope=research_scope(),
        ttl_seconds=60,
    )
    confirmation = content_digest(
        request.canonical_payload(),
        domain="hades-evolution-authorization-request-v1",
    )
    second = EvolutionLedger(path)
    entered = threading.Event()
    original_transaction = second.transaction

    @contextmanager
    def marked_transaction():
        entered.set()
        with original_transaction() as connection:
            yield connection

    monkeypatch.setattr(second, "transaction", marked_transaction)
    outcome: list[str] = []
    first.connection.execute("BEGIN IMMEDIATE")

    def blocked_issue() -> None:
        try:
            issue_grant(
                second,
                request_id=request.request_id,
                approved_by="local-operator",
                confirmation_digest=confirmation,
            )
        except AuthorizationError as exc:
            outcome.append(exc.code)
        else:
            outcome.append("issued")

    thread = threading.Thread(target=blocked_issue)
    thread.start()
    assert entered.wait(timeout=5)
    clock["value"] = LATER
    first.connection.commit()
    thread.join(timeout=10)

    assert not thread.is_alive()
    assert outcome == ["request_unavailable"]
    assert first.connection.execute(
        "SELECT COUNT(*) FROM authorization_grants"
    ).fetchone()[0] == 0


def test_consume_rechecks_expiry_after_waiting_for_sqlite_write_lock(
    tmp_path, monkeypatch
) -> None:
    clock = {"value": NOW}
    monkeypatch.setattr(authorization, "_now", lambda: clock["value"])
    path = tmp_path / "evolution.db"
    first = EvolutionLedger(path)
    first.attempt_id = first.create_attempt("manual", "ticket-1")
    _, grant = request_and_issue(first)
    second = EvolutionLedger(path)
    entered = threading.Event()
    original_transaction = second.transaction

    @contextmanager
    def marked_transaction():
        entered.set()
        with original_transaction() as connection:
            yield connection

    monkeypatch.setattr(second, "transaction", marked_transaction)
    outcome: list[str] = []
    first.connection.execute("BEGIN IMMEDIATE")

    def blocked_consume() -> None:
        try:
            consume_grant(
                second,
                grant_id=grant.grant_id,
                expected_kind="research",
                expected_subject_digest=DIGEST_A,
                required_scope=research_scope(duration=30),
            )
        except AuthorizationError as exc:
            outcome.append(exc.code)
        else:
            outcome.append("consumed")

    thread = threading.Thread(target=blocked_consume)
    thread.start()
    assert entered.wait(timeout=5)
    clock["value"] = LATER
    first.connection.commit()
    thread.join(timeout=10)

    assert not thread.is_alive()
    assert outcome == ["grant_unavailable"]
    assert first.connection.execute(
        "SELECT COUNT(*) FROM authorization_consumptions"
    ).fetchone()[0] == 0


def test_denial_rechecks_expiry_after_waiting_for_sqlite_write_lock(
    tmp_path, monkeypatch
) -> None:
    clock = {"value": NOW}
    monkeypatch.setattr(authorization, "_now", lambda: clock["value"])
    path = tmp_path / "evolution.db"
    first = EvolutionLedger(path)
    attempt_id = first.create_attempt("manual", "ticket-1")
    request = create_authorization_request(
        first,
        attempt_id=attempt_id,
        kind="research",
        subject_digest=DIGEST_A,
        scope=research_scope(),
        ttl_seconds=60,
    )
    second = EvolutionLedger(path)
    entered = threading.Event()
    original_transaction = second.transaction

    @contextmanager
    def marked_transaction():
        entered.set()
        with original_transaction() as connection:
            yield connection

    monkeypatch.setattr(second, "transaction", marked_transaction)
    outcome: list[str] = []
    first.connection.execute("BEGIN IMMEDIATE")

    def blocked_denial() -> None:
        try:
            deny_authorization_request(
                second,
                request_id=request.request_id,
                decided_by="local-operator",
            )
        except AuthorizationError as exc:
            outcome.append(exc.code)
        else:
            outcome.append("denied")

    thread = threading.Thread(target=blocked_denial)
    thread.start()
    assert entered.wait(timeout=5)
    clock["value"] = LATER
    first.connection.commit()
    thread.join(timeout=10)

    assert not thread.is_alive()
    assert outcome == ["request_unavailable"]
    assert first.connection.execute(
        "SELECT COUNT(*) FROM authorization_decisions"
    ).fetchone()[0] == 0


def test_denial_audit_timestamp_is_read_after_waiting_for_write_lock(
    tmp_path, monkeypatch
) -> None:
    clock = {"value": NOW}
    monkeypatch.setattr(authorization, "_now", lambda: clock["value"])
    path = tmp_path / "evolution.db"
    first = EvolutionLedger(path)
    attempt_id = first.create_attempt("manual", "ticket-1")
    request = create_authorization_request(
        first,
        attempt_id=attempt_id,
        kind="research",
        subject_digest=DIGEST_A,
        scope=research_scope(),
        ttl_seconds=120,
    )
    second = EvolutionLedger(path)
    entered = threading.Event()
    original_transaction = second.transaction

    @contextmanager
    def marked_transaction():
        entered.set()
        with original_transaction() as connection:
            yield connection

    monkeypatch.setattr(second, "transaction", marked_transaction)
    decisions: list[AuthorizationDecision] = []
    first.connection.execute("BEGIN IMMEDIATE")

    def blocked_denial() -> None:
        decisions.append(
            deny_authorization_request(
                second,
                request_id=request.request_id,
                decided_by="local-operator",
            )
        )

    thread = threading.Thread(target=blocked_denial)
    thread.start()
    assert entered.wait(timeout=5)
    clock["value"] = AFTER_LOCK
    first.connection.commit()
    thread.join(timeout=10)

    assert not thread.is_alive()
    assert len(decisions) == 1
    assert decisions[0].created_at == AFTER_LOCK
    event = first.history()[-1]
    assert event.event_type == "authorization_denied"
    assert event.created_at == AFTER_LOCK


@pytest.mark.parametrize(
    ("kind", "scope"),
    [
        (
            "research",
            research_scope(
                source_classes=[
                    "sk-proj-a1b2c3d4e5f60718293a4b5c6d7e8f90"
                ]
            ),
        ),
        (
            "research",
            research_scope(
                source_classes=["abcdef0123456789abcdef0123456789"]
            ),
        ),
        (
            "build",
            build_scope(source_families=["policy.yaml"]),
        ),
    ],
)
def test_nested_scope_symbols_reject_credentials_files_and_secret_material(
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


@pytest.mark.parametrize(
    ("decision", "actor"),
    [
        ("approve", "operator notes"),
        (
            "approve",
            "ghp_a1b2c3d4e5f60718293a4b5c6d7e8f90",
        ),
        ("approve", "hf_a1b2c3d4e5f60718"),
        ("deny", "glpat-a1b2c3d4e5f60718"),
        ("deny", "config.yaml"),
        ("deny", "a1b2c3d4e5f60718293a4b5c6d7e8f90"),
    ],
)
def test_persisted_decision_actor_must_be_privacy_safe_symbol(
    ledger: EvolutionLedger,
    decision: str,
    actor: str,
) -> None:
    request = create_authorization_request(
        ledger,
        attempt_id=ledger.attempt_id,
        kind="research",
        subject_digest=DIGEST_A,
        scope=research_scope(),
        ttl_seconds=120,
    )
    with pytest.raises(AuthorizationError, match="invalid_approver"):
        if decision == "approve":
            confirmation = content_digest(
                request.canonical_payload(),
                domain="hades-evolution-authorization-request-v1",
            )
            issue_grant(
                ledger,
                request_id=request.request_id,
                approved_by=actor,
                confirmation_digest=confirmation,
            )
        else:
            deny_authorization_request(
                ledger,
                request_id=request.request_id,
                decided_by=actor,
            )


def test_ordinary_symbolic_families_policies_and_actor_remain_valid(
    ledger: EvolutionLedger,
) -> None:
    request = create_authorization_request(
        ledger,
        attempt_id=ledger.attempt_id,
        kind="build",
        subject_digest=DIGEST_A,
        scope=build_scope(
            source_families=[
                "official-docs",
                "api-client",
                "token-broker",
                "sk-live-credential",
                "sk_live",
                "authorization-protocol-version-2026",
                "python-3.13",
                "sha256-rsa2048-pkcs1-v1",
            ],
            dependency_families=[
                "python3",
                "sqlite",
                "key-rotation-v2",
            ],
            isolation_policy={
                "network-access": "deny",
                "sandbox-profile": "strict",
                "credential-access": "deny",
                "secret-handling": "deny",
            },
            resource_limits={
                "duration_seconds": 120,
                "password-retries": 2,
            },
        ),
        ttl_seconds=120,
    )
    confirmation = content_digest(
        request.canonical_payload(),
        domain="hades-evolution-authorization-request-v1",
    )

    grant = issue_grant(
        ledger,
        request_id=request.request_id,
        approved_by="auth-operator",
        confirmation_digest=confirmation,
    )

    assert grant.approved_by == "auth-operator"


@pytest.mark.parametrize(
    "surface",
    ["scope-value", "scope-key", "actor", "domain-label"],
)
@pytest.mark.parametrize(
    "material",
    [SEPARATED_OPAQUE, TWO_CHUNK_OPAQUE, BASE32_OPAQUE],
)
def test_separated_opaque_material_is_rejected_without_echo(
    ledger: EvolutionLedger,
    surface: str,
    material: str,
) -> None:
    if surface == "actor":
        request = create_authorization_request(
            ledger,
            attempt_id=ledger.attempt_id,
            kind="research",
            subject_digest=DIGEST_A,
            scope=research_scope(),
            ttl_seconds=120,
        )
        confirmation = content_digest(
            request.canonical_payload(),
            domain="hades-evolution-authorization-request-v1",
        )
        operation = lambda: issue_grant(
            ledger,
            request_id=request.request_id,
            approved_by=material,
            confirmation_digest=confirmation,
        )
        expected_code = "invalid_approver"
    elif surface == "domain-label":
        operation = lambda: create_authorization_request(
            ledger,
            attempt_id=ledger.attempt_id,
            kind="research",
            subject_digest=DIGEST_A,
            scope=research_scope(
                domains=[f"{material}.example.com"]
            ),
            ttl_seconds=120,
        )
        expected_code = "invalid_scope"
    else:
        scope = (
            build_scope(source_families=[material])
            if surface == "scope-value"
            else build_scope(
                isolation_policy={material: "deny"}
            )
        )
        operation = lambda: create_authorization_request(
            ledger,
            attempt_id=ledger.attempt_id,
            kind="build",
            subject_digest=DIGEST_A,
            scope=scope,
            ttl_seconds=120,
        )
        expected_code = "invalid_scope"

    with pytest.raises(AuthorizationError) as captured:
        operation()

    assert captured.value.code == expected_code
    assert material not in str(captured.value)


@pytest.mark.parametrize(
    ("segment", "expected"),
    [
        ("a1b2c3d4e5f6g7h", False),
        ("a1b2c3d4e5f6g7h8", True),
        ("abcdefghi1j2k3l4", False),
        ("abcdefg1h2i3j4kl", True),
        ("abcdefghijklmnopqrabcdefghijklm", False),
        ("abcdefghijklmnopqabcdefghijklmno", False),
        ("abcdefghijklmnopqrabcdefghijklmn", True),
    ],
)
def test_opaque_segment_threshold_boundaries(
    segment: str,
    expected: bool,
) -> None:
    assert authorization._looks_like_opaque_segment(segment) is expected


@pytest.mark.parametrize(
    ("material", "expected"),
    [
        ("abcdefabcdefabcdefabcde", False),
        ("abcdefabcdefabcdefabcdef", True),
    ],
)
def test_hex_material_length_boundary(
    material: str,
    expected: bool,
) -> None:
    assert (
        authorization._looks_like_credential_material(material)
        is expected
    )


@pytest.mark.parametrize(
    "domain",
    [
        "sk-proj-a1b2c3d4e5f60718293a4b5c6d7e8f90.example.com",
        "a1b2c3d4e5f60718293a4b5c6d7e8f90.example.org",
    ],
)
def test_domain_labels_reject_credential_and_high_entropy_material(
    ledger: EvolutionLedger,
    domain: str,
) -> None:
    with pytest.raises(AuthorizationError, match="invalid_scope"):
        create_authorization_request(
            ledger,
            attempt_id=ledger.attempt_id,
            kind="research",
            subject_digest=DIGEST_A,
            scope=research_scope(domains=[domain]),
            ttl_seconds=120,
        )


def test_semantic_domain_labels_remain_valid(
    ledger: EvolutionLedger,
) -> None:
    request = create_authorization_request(
        ledger,
        attempt_id=ledger.attempt_id,
        kind="research",
        subject_digest=DIGEST_A,
        scope=research_scope(
            domains=["api.example.com", "auth.example.org"]
        ),
        ttl_seconds=120,
    )

    assert request.scope["domains"] == (
        "api.example.com",
        "auth.example.org",
    )


@pytest.mark.parametrize(
    ("decision", "confirmation"),
    [
        ("approved", None),
        ("approved", DIGEST_B),
        ("denied", DIGEST_A),
    ],
)
def test_sqlite_decision_check_closes_confirmation_shape(
    ledger: EvolutionLedger,
    decision: str,
    confirmation: str | None,
) -> None:
    request = create_authorization_request(
        ledger,
        attempt_id=ledger.attempt_id,
        kind="research",
        subject_digest=DIGEST_A,
        scope=research_scope(),
        ttl_seconds=120,
    )

    with pytest.raises(sqlite3.IntegrityError):
        ledger.connection.execute(
            """
            INSERT INTO authorization_decisions(
                decision_id, request_id, decision, decided_by,
                confirmation_digest, created_at
            ) VALUES (?, ?, ?, 'local-operator', ?, ?)
            """,
            (
                str(uuid.uuid4()),
                request.request_id,
                decision,
                confirmation,
                NOW,
            ),
        )


def _request_with_raw_approval(
    ledger: EvolutionLedger,
) -> tuple[object, str, str]:
    request = create_authorization_request(
        ledger,
        attempt_id=ledger.attempt_id,
        kind="research",
        subject_digest=DIGEST_A,
        scope=research_scope(),
        ttl_seconds=120,
    )
    confirmation = content_digest(
        request.canonical_payload(),
        domain="hades-evolution-authorization-request-v1",
    )
    ledger.connection.execute(
        """
        INSERT INTO authorization_decisions(
            decision_id, request_id, decision, decided_by,
            confirmation_digest, created_at
        ) VALUES (?, ?, 'approved', 'local-operator', ?, ?)
        """,
        (str(uuid.uuid4()), request.request_id, confirmation, NOW),
    )
    scope_json = ledger.connection.execute(
        """
        SELECT scope_json
        FROM authorization_requests
        WHERE request_id = ?
        """,
        (request.request_id,),
    ).fetchone()[0]
    return request, confirmation, scope_json


def _insert_raw_grant(
    connection,
    *,
    request,
    confirmation: str,
    request_scope_json: str,
    **changes: object,
) -> None:
    values: dict[str, object] = {
        "attempt_id": request.attempt_id,
        "grant_kind": request.kind,
        "subject_digest": request.subject_digest,
        "scope_json": request_scope_json,
        "expires_at": request.expires_at,
        "approved_by": "local-operator",
        "confirmation_digest": confirmation,
        "created_at": NOW,
    }
    values.update(changes)
    grant_id = str(uuid.uuid4())
    connection.execute(
        """
        INSERT INTO authorization_grants(
            grant_id, authorization_id, request_id, attempt_id, grant_kind,
            subject_digest, scope_json, expires_at, approved_by,
            confirmation_digest, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            grant_id,
            grant_id,
            request.request_id,
            values["attempt_id"],
            values["grant_kind"],
            values["subject_digest"],
            values["scope_json"],
            values["expires_at"],
            values["approved_by"],
            values["confirmation_digest"],
            values["created_at"],
        ),
    )


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("attempt_id", "other-attempt"),
        ("grant_kind", "build"),
        ("subject_digest", DIGEST_B),
        (
            "scope_json",
            '{"domains":[],"duration":60,"operations":["search","retrieve"],'
            '"source_classes":["paper"]}',
        ),
        ("expires_at", LATER),
        ("approved_by", "other-operator"),
        ("confirmation_digest", DIGEST_B),
        ("created_at", LATER),
    ],
)
def test_sqlite_rejects_grant_incoherent_with_request_or_approved_decision(
    ledger: EvolutionLedger,
    field: str,
    replacement: object,
) -> None:
    ledger.connection.execute(
        """
        INSERT INTO attempts(
            attempt_id, source_kind, source_ref, state, created_at
        ) VALUES (
            'other-attempt', 'manual', 'ticket-2', 'draft', ?
        )
        """,
        (NOW,),
    )
    request, confirmation, scope_json = _request_with_raw_approval(ledger)

    with pytest.raises(sqlite3.IntegrityError, match="authorization_grant"):
        _insert_raw_grant(
            ledger.connection,
            request=request,
            confirmation=confirmation,
            request_scope_json=scope_json,
            **{field: replacement},
        )

    assert ledger.connection.execute(
        "SELECT COUNT(*) FROM authorization_grants"
    ).fetchone()[0] == 0


def test_sqlite_accepts_exact_request_decision_grant_coherence(
    ledger: EvolutionLedger,
) -> None:
    request, confirmation, scope_json = _request_with_raw_approval(ledger)

    _insert_raw_grant(
        ledger.connection,
        request=request,
        confirmation=confirmation,
        request_scope_json=scope_json,
    )

    assert ledger.connection.execute(
        "SELECT COUNT(*) FROM authorization_grants"
    ).fetchone()[0] == 1


def test_grant_coherence_trigger_failure_rolls_back_prior_event(
    ledger: EvolutionLedger,
) -> None:
    request, confirmation, scope_json = _request_with_raw_approval(ledger)
    history_before = ledger.history()

    with pytest.raises(sqlite3.IntegrityError, match="authorization_grant"):
        with ledger.transaction() as connection:
            ledger._append(
                connection,
                LifecycleEvent(
                    event_id=str(uuid.uuid4()),
                    attempt_id=ledger.attempt_id,
                    generation_id=None,
                    event_type="test_event",
                    prior_state=None,
                    next_state=None,
                    actor="host",
                    input_digests=(DIGEST_A,),
                    authorization_id=request.request_id,
                    reason_code="test",
                    reason_summary="test",
                    created_at=NOW,
                ),
            )
            _insert_raw_grant(
                connection,
                request=request,
                confirmation=confirmation,
                request_scope_json=scope_json,
                subject_digest=DIGEST_B,
            )

    assert ledger.history() == history_before
    assert ledger.connection.execute(
        "SELECT COUNT(*) FROM authorization_grants"
    ).fetchone()[0] == 0


def test_decision_check_failure_rolls_back_prior_event(
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
    history_before = ledger.history()

    with pytest.raises(sqlite3.IntegrityError):
        with ledger.transaction() as connection:
            ledger._append(
                connection,
                LifecycleEvent(
                    event_id=str(uuid.uuid4()),
                    attempt_id=ledger.attempt_id,
                    generation_id=None,
                    event_type="test_event",
                    prior_state=None,
                    next_state=None,
                    actor="host",
                    input_digests=(DIGEST_A,),
                    authorization_id=request.request_id,
                    reason_code="test",
                    reason_summary="test",
                    created_at=NOW,
                ),
            )
            connection.execute(
                """
                INSERT INTO authorization_decisions(
                    decision_id, request_id, decision, decided_by,
                    confirmation_digest, created_at
                ) VALUES (?, ?, 'denied', 'local-operator', ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    request.request_id,
                    DIGEST_B,
                    NOW,
                ),
            )

    assert ledger.history() == history_before
    assert ledger.connection.execute(
        "SELECT COUNT(*) FROM authorization_decisions"
    ).fetchone()[0] == 0


@pytest.mark.parametrize(
    ("operation", "expected_code"),
    [
        ("issue", "request_unavailable"),
        ("deny", "request_unavailable"),
        ("consume", "grant_unavailable"),
    ],
)
def test_noncanonical_lookup_uuid_is_rejected_before_sql(
    ledger: EvolutionLedger,
    operation: str,
    expected_code: str,
) -> None:
    _request, grant = request_and_issue(ledger)

    def deny_select(
        action_code,
        _arg1,
        _arg2,
        _database_name,
        _trigger_name,
    ):
        if action_code == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    ledger.connection.set_authorizer(deny_select)
    try:
        with pytest.raises(AuthorizationError, match=expected_code):
            if operation == "issue":
                issue_grant(
                    ledger,
                    request_id="not-a-canonical-uuid",
                    approved_by="local-operator",
                    confirmation_digest=grant.confirmation_digest,
                )
            elif operation == "deny":
                deny_authorization_request(
                    ledger,
                    request_id="not-a-canonical-uuid",
                    decided_by="local-operator",
                )
            else:
                consume_grant(
                    ledger,
                    grant_id="not-a-canonical-uuid",
                    expected_kind="research",
                    expected_subject_digest=DIGEST_A,
                    required_scope=research_scope(duration=30),
                )
    finally:
        ledger.connection.set_authorizer(None)
