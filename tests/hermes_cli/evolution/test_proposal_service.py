"""TDD real-path tests for the operator proposal service and global safety gates.

Every test uses an isolated HERMES_HOME root with a real organism, real
Telos, and real observation-derived eligible suggestions.

RED phase: all tests fail because proposal_service does not yet exist.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import threading
from pathlib import Path

import pytest

from hermes_cli.evolution.ledger import EvolutionLedger, SCHEMA_VERSION

# ── Fixture helpers (inlined to avoid cross-module test coupling) ──────


def _patch_all_bindings(monkeypatch, org, hermes_home):
    """Patch every module-level binding of get_organism_home and get_hermes_home."""
    import hermes_constants as _hc_mod

    monkeypatch.setattr(_hc_mod, "get_organism_home", lambda: org)
    monkeypatch.setattr(_hc_mod, "get_hermes_home", lambda: hermes_home)
    monkeypatch.setattr(_hc_mod, "get_default_hermes_root", lambda: hermes_home)

    from hermes_cli.evolution import organism_home as _oh

    monkeypatch.setattr(_oh, "get_organism_home", lambda: org)
    monkeypatch.setattr(_oh, "get_default_hermes_root", lambda: hermes_home)

    from hermes_cli.evolution import lifecycle_global as _lg

    monkeypatch.setattr(_lg, "get_organism_home", lambda: org)


def _setup_organism(tmp_path, monkeypatch):
    """Init global lifecycle + identity + v6 ledger. Returns (org_root, organism_id)."""
    from hermes_cli.evolution.command import evolution_command
    from argparse import Namespace

    org = tmp_path / "organism"
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir(parents=True, exist_ok=True)
    _patch_all_bindings(monkeypatch, org, hermes_home)

    evolution_command(Namespace(action="init", json=True))
    from hermes_cli.evolution.organism_identity import load_organism_identity

    ident = load_organism_identity(org)
    return org, ident.organism_id


def _activate_telos(org, org_id):
    """Save and activate a Telos revision through the real host-approval path."""
    from hermes_cli.evolution.telos_contract import (
        TelosRevision,
        DesiredTrait,
        CapabilityDirection,
        Priority,
        ProactivityPolicy,
        Prohibition,
        SuccessIndicator,
    )
    from hermes_cli.evolution.telos_store import TelosStore

    tstore = TelosStore(org)
    telos = TelosRevision(
        schema_version=1,
        organism_id=org_id,
        parent_digest=None,
        purpose="Test Telos for proposal service",
        desired_traits=(DesiredTrait("reliable", "High reliability", ("reliable",), 5),),
        capability_directions=(
            CapabilityDirection("system.runtime", "Runtime stability.", ("system.runtime",), 4),
        ),
        priorities=(Priority("safety", "Safety.", ("safety",), 5),),
        tradeoffs=(),
        prohibitions=(Prohibition("none", "None.", ("none",), 5),),
        proactivity_policy=ProactivityPolicy("passive", "Passive.", ("passive",), 3),
        success_indicators=(SuccessIndicator("uptime", "High uptime.", ("uptime",), 4),),
    )
    tstore.save_revision(telos)
    digest = telos.canonical_digest

    import asyncio
    from gateway.config import Platform
    from gateway.platforms.base import MessageEvent
    from gateway.run import GatewayRunner
    from gateway.session import SessionSource, build_session_key
    from gateway.telos_coordinator import TelosCoordinator
    from hermes_cli.evolution.telos_approval import (
        HostApprovalContext,
        SqliteTelosApprovalBroker,
    )

    ledger = EvolutionLedger(org / "evolution" / "evolution.db")
    source = SessionSource(
        platform=Platform.TELEGRAM,
        user_id="prop-test",
        chat_id="prop-chat",
        chat_type="dm",
    )
    context = HostApprovalContext(
        surface="gateway",
        actor_ref="telegram:prop-test",
        session_ref=build_session_key(source),
        request_id=None,
        telos_digest=digest,
        action="activate",
        nonce="prop-approval",
        context_digest="ignored",
    )
    request_id = SqliteTelosApprovalBroker().create_request(
        ledger, org_id, digest, "activate", context, 3600,
    )
    ledger.connection.close()

    runner = object.__new__(GatewayRunner)
    runner._telos_coordinator = TelosCoordinator()
    runner._pending_approvals = {}
    event = MessageEvent(
        text=f"/approve telos {request_id}",
        source=source,
        message_id="prop-message",
    )
    asyncio.run(runner._handle_approve_command(event))
    return digest


def _enable_autopoiesis(tmp_path):
    """Enable autopoiesis + observer in global config."""
    from hermes_cli.evolution.global_config import load_global_config, save_global_config

    (tmp_path / ".hermes").mkdir(parents=True, exist_ok=True)
    cfg = load_global_config()
    cfg["autopoiesis"]["enabled"] = True
    cfg["autopoiesis"]["observer"]["enabled"] = True
    save_global_config(cfg["autopoiesis"])


def _create_eligible_suggestion(org, org_id):
    """Inject an envelope and scan to produce an eligible suggestion.

    Returns the SuggestionRecord if one was created.
    """
    from hermes_cli.evolution.observation_contract import ObservationEnvelope
    from hermes_cli.evolution.observer_service import ObserverService

    svc = ObserverService(org)
    env = ObservationEnvelope(
        schema_version=1,
        event_id="prop-sug-env-001",
        organism_id=org_id,
        occurred_at="2026-07-25T12:00:00.000000Z",
        signal_type="capability_absence",
        provenance="explicit_user",
        source_profile_ref="prof_proposal",
        source_project_ref="proj_proposal",
        source_session_ref="sess_proposal",
        generation_id="a" * 64,
        gnothi_revision_digest=None,
        telos_digest=None,
        capability_key="webcam",
        operation_key="capture",
        outcome_key="missing",
        constraint_key="unconstrained",
        severity="high",
        task_impact="high",
        retry_count=0,
        latency_bucket=None,
        explicit_user_intent=True,
        recovered=False,
        evidence_refs=(),
        redaction_status="verified_redacted",
    )
    svc.ingest_envelope(env)
    suggestions = svc.scan_and_update_suggestions()
    eligible = [s for s in suggestions if s.state == "eligible"]
    if eligible:
        return eligible[0]
    return None


# ── Real-path count helper ─────────────────────────────────────────────


def _count_all(db_path: Path) -> dict[str, int]:
    """Return row counts for the four proposal record tables."""
    uri = db_path.absolute().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        return {
            "attempts": conn.execute(
                "SELECT COUNT(*) FROM attempts"
            ).fetchone()[0],
            "blueprints": conn.execute(
                "SELECT COUNT(*) FROM blueprints"
            ).fetchone()[0],
            "blueprint_documents": conn.execute(
                "SELECT COUNT(*) FROM blueprint_documents"
            ).fetchone()[0],
            "lifecycle_events": conn.execute(
                "SELECT COUNT(*) FROM lifecycle_events"
            ).fetchone()[0],
        }
    finally:
        conn.close()


def _count_every_table(db_path: Path) -> dict[str, int]:
    uri = db_path.absolute().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        names = [
            str(row[0])
            for row in conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            )
        ]
        return {
            name: int(
                conn.execute(
                    f'SELECT COUNT(*) FROM "{name}"'
                ).fetchone()[0]
            )
            for name in names
        }
    finally:
        conn.close()


# ── Fixture: fully bootstrapped organism with eligible suggestion ──────


@pytest.fixture
def eligible_env(tmp_path, monkeypatch):
    """Set up organism + activated Telos + eligible suggestion.

    Returns (org_root, suggestion_record).
    """
    org, org_id = _setup_organism(tmp_path, monkeypatch)
    _activate_telos(org, org_id)
    _enable_autopoiesis(tmp_path)
    sug = _create_eligible_suggestion(org, org_id)
    assert sug is not None, "Expected at least one eligible suggestion"
    assert sug.state == "eligible"
    return org, sug


# ═══════════════════════════════════════════════════════════════════════
# Test 1: Happy path — eligible/current suggestion creates exactly one
#         attempt, blueprint, document and lifecycle event.
# ═══════════════════════════════════════════════════════════════════════


class TestHappyPath:
    def test_creates_exactly_one_of_each(self, eligible_env):
        from hermes_cli.evolution.proposal_service import propose_suggestion

        org, sug = eligible_env
        db_path = org / "evolution" / "evolution.db"
        before = _count_all(db_path)

        result = propose_suggestion(organism_root=org, suggestion_id=sug.suggestion_id)

        assert result.status == "created"
        assert result.blueprint.created is True
        after = _count_all(db_path)
        assert after["attempts"] == before["attempts"] + 1
        assert after["blueprints"] == before["blueprints"] + 1
        assert after["blueprint_documents"] == before["blueprint_documents"] + 1
        assert after["lifecycle_events"] == before["lifecycle_events"] + 1
        assert result.blueprint.state == "draft"

    def test_proposal_result_contains_blueprint_draft(self, eligible_env):
        from hermes_cli.evolution.proposal_service import propose_suggestion, ProposalResult

        org, sug = eligible_env
        result = propose_suggestion(organism_root=org, suggestion_id=sug.suggestion_id)
        assert isinstance(result, ProposalResult)
        assert result.blueprint.blueprint_id.startswith("bp_")
        assert result.blueprint.attempt_id is not None
        assert result.blueprint.canonical_digest is not None
        assert len(result.blueprint.canonical_digest) == 64
        assert result.blueprint.event is not None
        assert result.blueprint.event.event_type == "blueprint_proposed"

    def test_chain_remains_intact(self, eligible_env):
        from hermes_cli.evolution.proposal_service import propose_suggestion

        org, sug = eligible_env
        propose_suggestion(organism_root=org, suggestion_id=sug.suggestion_id)
        db_path = org / "evolution" / "evolution.db"
        ledger = EvolutionLedger(db_path)
        try:
            assert ledger.verify_chain() == []
        finally:
            ledger.connection.close()


# ═══════════════════════════════════════════════════════════════════════
# Test 2: Idempotency — equivalent proposals return existing.
# ═══════════════════════════════════════════════════════════════════════


class TestIdempotency:
    def test_second_identical_proposal_returns_existing(self, eligible_env):
        from hermes_cli.evolution.proposal_service import propose_suggestion

        org, sug = eligible_env
        db_path = org / "evolution" / "evolution.db"

        first = propose_suggestion(organism_root=org, suggestion_id=sug.suggestion_id)
        before = _count_all(db_path)

        second = propose_suggestion(organism_root=org, suggestion_id=sug.suggestion_id)

        assert second.status == "existing"
        assert second.blueprint.created is False
        assert second.blueprint.blueprint_id == first.blueprint.blueprint_id
        assert second.blueprint.attempt_id == first.blueprint.attempt_id
        assert second.blueprint.event is None

        after = _count_all(db_path)
        assert after == before

    def test_concurrent_callers_create_exactly_one(self, eligible_env):
        from hermes_cli.evolution.proposal_service import propose_suggestion

        org, sug = eligible_env
        db_path = org / "evolution" / "evolution.db"
        before = _count_all(db_path)
        results = []
        errors = []
        barrier = threading.Barrier(2)

        def propose():
            try:
                barrier.wait(timeout=30)
                r = propose_suggestion(
                    organism_root=org, suggestion_id=sug.suggestion_id
                )
                results.append(r)
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=propose)
        t2 = threading.Thread(target=propose)
        t1.start()
        t2.start()
        t1.join(timeout=60)
        t2.join(timeout=60)

        assert not t1.is_alive()
        assert not t2.is_alive()
        assert len(errors) == 0, f"Concurrent errors: {errors}"
        assert len(results) == 2
        assert sorted(r.status for r in results) == ["created", "existing"]
        ids = {r.blueprint.blueprint_id for r in results}
        assert len(ids) == 1
        after = _count_all(db_path)
        assert after["attempts"] == before["attempts"] + 1
        assert after["blueprints"] == before["blueprints"] + 1
        assert (
            after["blueprint_documents"]
            == before["blueprint_documents"] + 1
        )
        assert (
            after["lifecycle_events"]
            == before["lifecycle_events"] + 1
        )


# ═══════════════════════════════════════════════════════════════════════
# Test 3: Gate failures — each writes zero records.
# ═══════════════════════════════════════════════════════════════════════


class TestRejectedGates:
    def test_unknown_suggestion_id(self, eligible_env):
        from hermes_cli.evolution.proposal_service import propose_suggestion, ProposalError

        org, _ = eligible_env
        db_path = org / "evolution" / "evolution.db"
        before = _count_all(db_path)

        with pytest.raises(ProposalError, match="suggestion_missing"):
            propose_suggestion(organism_root=org, suggestion_id="sug_no_such_id")
        assert _count_all(db_path) == before

    def test_invalid_suggestion_id_format(self, eligible_env):
        from hermes_cli.evolution.proposal_service import propose_suggestion, ProposalError

        org, _ = eligible_env
        db_path = org / "evolution" / "evolution.db"
        before = _count_all(db_path)

        with pytest.raises(ProposalError, match="invalid_suggestion_id"):
            propose_suggestion(organism_root=org, suggestion_id="invalid/id!")
        assert _count_all(db_path) == before

        with pytest.raises(ProposalError, match="invalid_suggestion_id"):
            propose_suggestion(organism_root=org, suggestion_id="")
        assert _count_all(db_path) == before

    def test_missing_organism_identity(self, tmp_path, monkeypatch):
        from hermes_cli.evolution.proposal_service import propose_suggestion, ProposalError

        org, _ = _setup_organism(tmp_path, monkeypatch)
        org_id_path = org / "identity.json"
        org_id_path.unlink()
        db_path = org / "evolution" / "evolution.db"
        before = _count_all(db_path)

        with pytest.raises(ProposalError, match="organism_identity_missing"):
            propose_suggestion(organism_root=org, suggestion_id="sug_test")
        assert _count_all(db_path) == before

    def test_missing_ledger(self, tmp_path, monkeypatch):
        from hermes_cli.evolution.proposal_service import propose_suggestion, ProposalError

        org, _ = _setup_organism(tmp_path, monkeypatch)
        ledger_file = org / "evolution" / "evolution.db"
        ledger_file.unlink()

        with pytest.raises(ProposalError, match="ledger_not_found"):
            propose_suggestion(organism_root=org, suggestion_id="sug_test")
        assert not ledger_file.exists(), "Ledger must remain absent"

    def test_missing_ledger_remains_absent(self, tmp_path, monkeypatch):
        from hermes_cli.evolution.proposal_service import propose_suggestion, ProposalError

        org, _ = _setup_organism(tmp_path, monkeypatch)
        ledger_file = org / "evolution" / "evolution.db"
        ledger_file.unlink()

        with pytest.raises(ProposalError, match="ledger_not_found"):
            propose_suggestion(organism_root=org, suggestion_id="sug_test")
        assert not ledger_file.exists()

    def test_empty_ledger(self, tmp_path, monkeypatch):
        from hermes_cli.evolution.proposal_service import propose_suggestion, ProposalError

        org, _ = _setup_organism(tmp_path, monkeypatch)
        db_path = org / "evolution" / "evolution.db"
        conn = sqlite3.connect(f"{db_path.as_uri()}?mode=rw", uri=True)
        conn.execute("DROP TABLE IF EXISTS schema_version")
        conn.close()
        db_path.write_bytes(b"")
        before_size = db_path.stat().st_size
        assert before_size == 0

        with pytest.raises(ProposalError, match="ledger_empty"):
            propose_suggestion(organism_root=org, suggestion_id="sug_test")
        assert db_path.stat().st_size == 0

    def test_old_schema_version(self, tmp_path, monkeypatch):
        from hermes_cli.evolution.proposal_service import propose_suggestion, ProposalError

        org, _ = _setup_organism(tmp_path, monkeypatch)
        db_path = org / "evolution" / "evolution.db"
        # Drop the constrained schema_version table and recreate with version 5
        conn = sqlite3.connect(f"{db_path.as_uri()}?mode=rw", uri=True)
        conn.execute("DROP TABLE schema_version")
        conn.execute(
            "CREATE TABLE schema_version (singleton INTEGER NOT NULL PRIMARY KEY CHECK(singleton = 1), version INTEGER NOT NULL) WITHOUT ROWID"
        )
        conn.execute("INSERT INTO schema_version(singleton, version) VALUES (1, ?)", (SCHEMA_VERSION - 1,))
        conn.commit()
        conn.close()
        before = _count_all(db_path)

        with pytest.raises(ProposalError, match="ledger_unsupported_schema"):
            propose_suggestion(organism_root=org, suggestion_id="sug_test")
        assert _count_all(db_path) == before
        conn = sqlite3.connect(
            f"{db_path.absolute().as_uri()}?mode=ro",
            uri=True,
        )
        try:
            assert conn.execute(
                "SELECT version FROM schema_version"
            ).fetchone()[0] == SCHEMA_VERSION - 1
        finally:
            conn.close()

    def test_unsupported_schema_version(self, tmp_path, monkeypatch):
        from hermes_cli.evolution.proposal_service import propose_suggestion, ProposalError

        org, _ = _setup_organism(tmp_path, monkeypatch)
        db_path = org / "evolution" / "evolution.db"
        conn = sqlite3.connect(f"{db_path.as_uri()}?mode=rw", uri=True)
        conn.execute("DROP TABLE schema_version")
        conn.execute(
            "CREATE TABLE schema_version (singleton INTEGER NOT NULL PRIMARY KEY CHECK(singleton = 1), version INTEGER NOT NULL) WITHOUT ROWID"
        )
        conn.execute("INSERT INTO schema_version(singleton, version) VALUES (1, ?)", (SCHEMA_VERSION + 1,))
        conn.commit()
        conn.close()
        before = _count_all(db_path)

        with pytest.raises(ProposalError, match="ledger_unsupported_schema"):
            propose_suggestion(organism_root=org, suggestion_id="sug_test")
        assert _count_all(db_path) == before
        conn = sqlite3.connect(
            f"{db_path.absolute().as_uri()}?mode=ro",
            uri=True,
        )
        try:
            assert conn.execute(
                "SELECT version FROM schema_version"
            ).fetchone()[0] == SCHEMA_VERSION + 1
        finally:
            conn.close()

    def test_stale_telos(self, eligible_env):
        from hermes_cli.evolution.proposal_service import propose_suggestion, ProposalError
        from hermes_cli.evolution.telos_store import TelosStore

        org, sug = eligible_env
        db_path = org / "evolution" / "evolution.db"
        # Replace active Telos with a different digest
        conn = sqlite3.connect(f"{db_path.as_uri()}?mode=rw", uri=True)
        conn.execute(
            "UPDATE opportunity_suggestions SET active_telos_digest = ? WHERE suggestion_id = ?",
            ("f" * 64, sug.suggestion_id),
        )
        conn.commit()
        conn.close()
        before = _count_all(db_path)

        with pytest.raises(ProposalError, match="suggestion_telos_mismatch"):
            propose_suggestion(organism_root=org, suggestion_id=sug.suggestion_id)
        assert _count_all(db_path) == before

    def test_missing_telos(self, tmp_path, monkeypatch):
        from hermes_cli.evolution.proposal_service import propose_suggestion, ProposalError

        org, org_id = _setup_organism(tmp_path, monkeypatch)
        # No Telos activation
        _enable_autopoiesis(tmp_path)
        sug = _create_eligible_suggestion(org, org_id)
        if sug is None:
            sug = None  # No suggestion without Telos — skip suggestion part
        db_path = org / "evolution" / "evolution.db"
        before = _count_all(db_path)

        with pytest.raises(ProposalError, match="no_active_telos"):
            propose_suggestion(organism_root=org, suggestion_id="sug_dummy")
        assert _count_all(db_path) == before

    def test_malformed_active_telos_writes_nothing(self, eligible_env):
        from hermes_cli.evolution.proposal_service import (
            ProposalError,
            propose_suggestion,
        )

        org, sug = eligible_env
        db_path = org / "evolution" / "evolution.db"
        (org / "telos" / "active.json").write_text(
            '{"digest":"not-a-digest"}',
            encoding="utf-8",
        )
        before = _count_all(db_path)

        with pytest.raises(ProposalError, match="no_active_telos"):
            propose_suggestion(
                organism_root=org,
                suggestion_id=sug.suggestion_id,
            )
        assert _count_all(db_path) == before

    def test_missing_active_telos_revision_writes_nothing(
        self,
        eligible_env,
    ):
        from hermes_cli.evolution.proposal_service import (
            ProposalError,
            propose_suggestion,
        )
        from hermes_cli.evolution.telos_store import TelosStore

        org, sug = eligible_env
        store = TelosStore(org)
        digest = store.get_active_digest()
        assert digest is not None
        (store.revisions_dir / f"{digest}.json").unlink()
        db_path = org / "evolution" / "evolution.db"
        before = _count_all(db_path)

        with pytest.raises(ProposalError, match="no_active_telos"):
            propose_suggestion(
                organism_root=org,
                suggestion_id=sug.suggestion_id,
            )
        assert _count_all(db_path) == before

    def test_foreign_active_telos_revision_fails_global_identity_gate_without_write(
        self,
        eligible_env,
    ):
        from dataclasses import replace
        from uuid import uuid4

        from hermes_cli.evolution.proposal_service import (
            ProposalError,
            propose_suggestion,
        )
        from hermes_cli.evolution.telos_store import TelosStore

        org, sug = eligible_env
        store = TelosStore(org)
        current_digest = store.get_active_digest()
        assert current_digest is not None
        foreign_revision = replace(
            store.get_revision(current_digest),
            organism_id=str(uuid4()),
        )
        store.save_revision(foreign_revision)
        store.active_pointer.write_text(
            json.dumps({"digest": foreign_revision.canonical_digest}),
            encoding="utf-8",
        )

        db_path = org / "evolution" / "evolution.db"
        conn = sqlite3.connect(f"{db_path.as_uri()}?mode=rw", uri=True)
        try:
            conn.execute(
                """
                UPDATE opportunity_suggestions
                SET active_telos_digest = ?
                WHERE suggestion_id = ?
                """,
                (foreign_revision.canonical_digest, sug.suggestion_id),
            )
            conn.commit()
        finally:
            conn.close()
        before = _count_all(db_path)

        with pytest.raises(ProposalError, match="telos_organism_mismatch"):
            propose_suggestion(
                organism_root=org,
                suggestion_id=sug.suggestion_id,
            )
        assert _count_all(db_path) == before

    def test_suggestion_not_eligible(self, eligible_env):
        from hermes_cli.evolution.proposal_service import propose_suggestion, ProposalError

        org, sug = eligible_env
        db_path = org / "evolution" / "evolution.db"
        # Change state from eligible to observing
        conn = sqlite3.connect(f"{db_path.as_uri()}?mode=rw", uri=True)
        conn.execute(
            "UPDATE opportunity_suggestions SET state = 'observing' WHERE suggestion_id = ?",
            (sug.suggestion_id,),
        )
        conn.commit()
        conn.close()
        before = _count_all(db_path)

        with pytest.raises(ProposalError, match="suggestion_not_eligible"):
            propose_suggestion(organism_root=org, suggestion_id=sug.suggestion_id)
        assert _count_all(db_path) == before

    def test_corrupt_chain(self, tmp_path, monkeypatch):
        from hermes_cli.evolution.proposal_service import propose_suggestion, ProposalError

        org, org_id = _setup_organism(tmp_path, monkeypatch)
        _activate_telos(org, org_id)
        _enable_autopoiesis(tmp_path)
        sug = _create_eligible_suggestion(org, org_id)
        assert sug is not None

        db_path = org / "evolution" / "evolution.db"
        conn = sqlite3.connect(
            f"{db_path.absolute().as_uri()}?mode=rw",
            uri=True,
        )
        trigger_row = conn.execute(
            """
            SELECT sql
            FROM sqlite_master
            WHERE type = 'trigger' AND name = 'lifecycle_events_no_update'
            """
        ).fetchone()
        digest_row = conn.execute(
            """
            SELECT event_digest
            FROM lifecycle_events
            WHERE event_sequence = 1
            """
        ).fetchone()
        assert trigger_row is not None
        assert digest_row is not None
        trigger_sql = str(trigger_row[0])
        original_digest = str(digest_row[0])
        tampered_digest = (
            ("0" if original_digest[0] != "0" else "1")
            + original_digest[1:]
        )
        try:
            conn.execute("DROP TRIGGER lifecycle_events_no_update")
            conn.execute(
                """
                UPDATE lifecycle_events
                SET event_digest = ?
                WHERE event_sequence = 1
                """,
                (tampered_digest,),
            )
            conn.execute(trigger_sql)
            conn.commit()
        finally:
            conn.close()
        before = _count_all(db_path)

        with pytest.raises(ProposalError, match="lifecycle_chain_invalid"):
            propose_suggestion(organism_root=org, suggestion_id=sug.suggestion_id)
        assert _count_all(db_path) == before

    def test_missing_organism_root_is_not_created(
        self,
        tmp_path,
    ):
        from hermes_cli.evolution.proposal_service import (
            ProposalError,
            propose_suggestion,
        )

        missing = tmp_path / "missing-organism"
        with pytest.raises(ProposalError, match="organism_identity_missing"):
            propose_suggestion(
                organism_root=missing,
                suggestion_id="sug_test",
            )
        assert not missing.exists()

    def test_symlink_ledger_is_rejected_without_touching_target(
        self,
        tmp_path,
        monkeypatch,
    ):
        from hermes_cli.evolution.proposal_service import (
            ProposalError,
            propose_suggestion,
        )

        org, _ = _setup_organism(tmp_path, monkeypatch)
        db_path = org / "evolution" / "evolution.db"
        target = tmp_path / "ledger-target.db"
        shutil.copy2(db_path, target)
        original = target.read_bytes()
        db_path.unlink()
        db_path.symlink_to(target)

        with pytest.raises(ProposalError, match="ledger_unsafe"):
            propose_suggestion(
                organism_root=org,
                suggestion_id="sug_test",
            )
        assert db_path.is_symlink()
        assert target.read_bytes() == original


# ═══════════════════════════════════════════════════════════════════════
# Test 4: Profile-switch privacy — same organism, different profile.
# ═══════════════════════════════════════════════════════════════════════


class TestProfilePrivacy:
    def test_switching_profile_returns_same_blueprint(self, eligible_env, monkeypatch):
        from hermes_cli.evolution.proposal_service import propose_suggestion

        org, sug = eligible_env

        # Create the proposal under profile A
        result_a = propose_suggestion(
            organism_root=org, suggestion_id=sug.suggestion_id
        )
        assert result_a.status == "created"

        # Switch to a different profile
        other_home = org.parent / "PRIVATE_PROFILE_TOKEN"
        monkeypatch.setenv("HERMES_HOME", str(other_home))

        # Same organism root, same suggestion — should see existing
        result_b = propose_suggestion(
            organism_root=org, suggestion_id=sug.suggestion_id
        )
        assert result_b.status == "existing"
        assert result_b.blueprint.blueprint_id == result_a.blueprint.blueprint_id

    def test_no_private_identifiers_in_json(
        self,
        eligible_env,
        monkeypatch,
    ):
        from hermes_cli.evolution.proposal_service import propose_suggestion

        org, sug = eligible_env
        monkeypatch.setenv(
            "HERMES_HOME",
            str(org.parent / "PRIVATE_PROFILE_TOKEN"),
        )
        result = propose_suggestion(organism_root=org, suggestion_id=sug.suggestion_id)

        # Read the stored canonical JSON from the DB
        db_path = org / "evolution" / "evolution.db"
        uri = db_path.absolute().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        row = conn.execute(
            "SELECT canonical_document_json FROM blueprint_documents WHERE blueprint_id = ?",
            (result.blueprint.blueprint_id,),
        ).fetchone()
        conn.close()

        assert row is not None
        doc_json = row[0]
        doc = json.loads(doc_json)
        # No filesystem paths or URI schemes in canonical JSON
        raw = doc_json
        assert "/" not in raw
        assert "://" not in raw
        for private_token in (
            "private_profile_token",
            "prof_proposal",
            "proj_proposal",
            "sess_proposal",
        ):
            assert private_token not in raw.lower()
        assert raw.startswith('{"') and "schema_version" in raw
        assert doc["origin"] == "observer-v1"
        assert doc["schema_version"] == 1


# ═══════════════════════════════════════════════════════════════════════
# Test 5: No extra artifacts — no dirs, auth, generation, network, etc.
# ═══════════════════════════════════════════════════════════════════════


class TestNoExtraArtifacts:
    def test_no_unexpected_dirs_or_files(self, eligible_env):
        from hermes_cli.evolution.proposal_service import propose_suggestion

        org, sug = eligible_env
        db_path = org / "evolution" / "evolution.db"
        before_tables = _count_every_table(db_path)
        before_items = set(
            p.relative_to(org).as_posix()
            for p in org.rglob("*")
            if p.is_file() or p.is_dir()
        )

        propose_suggestion(organism_root=org, suggestion_id=sug.suggestion_id)

        after_items = set(
            p.relative_to(org).as_posix()
            for p in org.rglob("*")
            if p.is_file() or p.is_dir()
        )
        after_tables = _count_every_table(db_path)
        deltas = {
            name: after_tables[name] - before_tables[name]
            for name in before_tables
            if after_tables[name] != before_tables[name]
        }
        assert deltas == {
            "attempts": 1,
            "blueprint_documents": 1,
            "blueprints": 1,
            "lifecycle_events": 1,
        }
        # Only the known proposal tables should have new rows; no new files/dirs
        new_items = after_items - before_items
        # Expected: the WAL/shm/journal files from the write, or nothing extra
        actual_new = {n for n in new_items if not any(
            e in n for e in (".db-wal", ".db-shm", ".lifecycle.lock")
        )}
        assert (
            len(actual_new) == 0
        ), f"Unexpected new files/dirs: {actual_new}"


# ═══════════════════════════════════════════════════════════════════════
# Test 6: Error codes are stable, non-sensitive, never leak paths/msgs.
# ═══════════════════════════════════════════════════════════════════════


class TestErrorCodes:
    def test_error_codes_are_stable_strings(self, eligible_env):
        from hermes_cli.evolution.proposal_service import propose_suggestion, ProposalError

        org, _ = eligible_env

        tests = [
            (org, "", "invalid_suggestion_id"),
            (org, "bad/id!", "invalid_suggestion_id"),
        ]
        for root, sid, expected_code in tests:
            with pytest.raises(ProposalError) as exc:
                propose_suggestion(organism_root=root, suggestion_id=sid)
            assert exc.value.code == expected_code, (
                f"Expected code={expected_code!r}, got {exc.value.code!r}"
            )

    def test_error_message_never_leaks_paths_or_payloads(self, eligible_env):
        from hermes_cli.evolution.proposal_service import propose_suggestion, ProposalError

        org, _ = eligible_env

        with pytest.raises(ProposalError) as exc:
            propose_suggestion(organism_root=org, suggestion_id="sug_no_such_id")
        msg = str(exc.value)
        assert "/" not in msg
        assert org.as_posix() not in msg
        assert "sug_no_such_id" not in msg
        assert "evolution.db" not in msg


# ═══════════════════════════════════════════════════════════════════════
# Test 7: Ledger schema version matches expected
# ═══════════════════════════════════════════════════════════════════════


def test_ledger_schema_version_is_v6(eligible_env):
    org, _ = eligible_env
    db_path = org / "evolution" / "evolution.db"
    ledger = EvolutionLedger(db_path)
    try:
        assert ledger.schema_version == SCHEMA_VERSION
    finally:
        ledger.connection.close()
