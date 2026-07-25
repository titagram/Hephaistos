"""End-to-End Scenarios for Autopoiesis Project B (Global Telos and Opportunity Observer)."""

import asyncio
import pytest
from pathlib import Path

import hermes_constants as _hc


def _setup_organism(tmp_path: Path, monkeypatch):
    """Setup organism directories, lifecycle init, identity, store. Returns fixtures."""
    from hermes_cli.evolution import organism_home as _oh

    org = tmp_path / "organism"
    monkeypatch.setattr(_hc, "get_organism_home", lambda: org)
    monkeypatch.setattr(_oh, "get_organism_home", lambda: org)
    monkeypatch.setattr(_hc, "get_default_hermes_root", lambda: tmp_path / ".hermes")
    from hermes_cli.evolution import lifecycle_global as _lg
    monkeypatch.setattr(_lg, "get_organism_home", lambda: org)

    from hermes_cli.evolution.lifecycle_global import ensure_global_lifecycle_initialized
    from hermes_cli.evolution.ledger import EvolutionLedger
    from hermes_cli.evolution.organism_identity import load_organism_identity
    from hermes_cli.evolution.telos_store import TelosStore

    gen = ensure_global_lifecycle_initialized()
    ident = load_organism_identity(org)
    ledger = EvolutionLedger(org / "evolution" / "evolution.db")
    tstore = TelosStore(org)
    return org, ident.organism_id, ledger, tstore


def _save_telos(store, org_id, purpose="Test", parent=None):
    from hermes_cli.evolution.telos_contract import (
        TelosRevision, DesiredTrait, CapabilityDirection,
        Priority, ProactivityPolicy, Prohibition, SuccessIndicator,
    )
    t = TelosRevision(
        schema_version=1, organism_id=org_id, parent_digest=parent,
        purpose=purpose,
        desired_traits=(DesiredTrait("reliable", "High accuracy", ("reliable",), 5),),
        capability_directions=(
            CapabilityDirection("webcam", "Support camera image capture.", ("webcam",), 4),
            CapabilityDirection("performance", "High performance execution.", ("performance",), 4),
        ),
        priorities=(Priority("safety", "Safety first.", ("safety",), 5),),
        tradeoffs=(), prohibitions=(Prohibition("none", "None", ("none",), 5),),
        proactivity_policy=ProactivityPolicy("passive", "Passive suggestions.", ("passive",), 3),
        success_indicators=(SuccessIndicator("task_done", "High task completion", ("done",), 4),),
    )
    store.save_revision(t)
    return t


def _gateway_activate(ledger, org_id, digest):
    """Activate a saved revision through the real Gateway /approve router."""
    from gateway.config import Platform
    from gateway.platforms.base import MessageEvent
    from gateway.run import GatewayRunner
    from gateway.session import SessionSource, build_session_key
    from gateway.telos_coordinator import TelosCoordinator
    from hermes_cli.evolution.telos_approval import (
        HostApprovalContext,
        SqliteTelosApprovalBroker,
    )

    source = SessionSource(
        platform=Platform.TELEGRAM,
        user_id="e2e-user",
        chat_id="e2e-chat",
        chat_type="dm",
    )
    context = HostApprovalContext(
        surface="gateway",
        actor_ref="telegram:e2e-user",
        session_ref=build_session_key(source),
        request_id=None,
        telos_digest=digest,
        action="activate",
        nonce="e2e-approval",
        context_digest="ignored",
    )
    request_id = SqliteTelosApprovalBroker().create_request(
        ledger,
        org_id,
        digest,
        "activate",
        context,
        3600,
    )
    ledger.connection.close()

    runner = object.__new__(GatewayRunner)
    runner._telos_coordinator = TelosCoordinator()
    runner._pending_approvals = {}
    event = MessageEvent(
        text=f"/approve telos {request_id}",
        source=source,
        message_id="e2e-message",
    )
    result = asyncio.run(runner._handle_approve_command(event))
    assert "Telos activate completed" in result


# ── Scenario 1: Initial Telos Approval Boundary ──

def test_scenario_initial_telos_approval_boundary(tmp_path, monkeypatch):
    """Draft exists but is inert. Direct activation fails. Host-approved chain exists."""
    org, org_id, ledger, tstore = _setup_organism(tmp_path, monkeypatch)

    # 1. draft/revision exists but is inert
    telos = _save_telos(tstore, org_id, "Initial Telos")
    digest = telos.canonical_digest
    assert tstore.get_active_digest() is None

    # 2. direct activation fails (public API always fails closed)
    from hermes_cli.evolution.telos_store import TelosStoreError
    with pytest.raises(TelosStoreError, match="host_approval_not_implemented"):
        tstore.activate_revision(digest)

    # 3. model can create SQLite rows but cannot activate through public API
    from hermes_cli.evolution.telos_approval import (
        HostApprovalContext,
        SqliteTelosApprovalBroker,
        compute_context_digest,
    )
    model_broker = SqliteTelosApprovalBroker()
    model_ctx = HostApprovalContext(
        surface="model", actor_ref="model", session_ref="m",
        request_id=None, telos_digest=digest, action="activate",
        nonce="m", context_digest="ignored",
    )
    req_id = model_broker.create_request(ledger, org_id, digest, "activate", model_ctx, 3600)
    correct_digest = compute_context_digest("model", "model", "m", req_id, "m")
    dec_ctx = HostApprovalContext(
        surface="model", actor_ref="model", session_ref="m",
        request_id=req_id, telos_digest=digest, action="activate",
        nonce="m", context_digest=correct_digest,
    )
    model_broker.record_host_decision(ledger, dec_ctx, "approved")
    # Model has SQLite rows but cannot publish pointers — no mutation
    assert tstore.get_active_digest() is None

    # 4. public API remains closed even with coherent model-created rows
    with pytest.raises(TelosStoreError, match="host_approval_not_implemented"):
        tstore.activate_revision(digest)

    assert tstore.get_active_digest() is None

    # 5. a real Gateway host event approves and activates the exact revision
    _gateway_activate(ledger, org_id, digest)
    assert tstore.get_active_digest() == digest


# ── Scenario 2: Missing Webcam Capability ──

def test_scenario_missing_webcam_capability(tmp_path, monkeypatch):
    """Two profiles report webcam absence → one eligible suggestion."""
    org, org_id, ledger, tstore = _setup_organism(tmp_path, monkeypatch)
    telos = _save_telos(tstore, org_id, "Webcam Telos")
    digest = telos.canonical_digest
    _gateway_activate(ledger, org_id, digest)

    from hermes_cli.evolution.observation_contract import ObservationEnvelope
    from hermes_cli.evolution.observer_service import ObserverService

    service = ObserverService(org)

    def _env(eid: str, pref: str, sref: str):
        return ObservationEnvelope(
            schema_version=1, event_id=eid, organism_id=org_id,
            occurred_at="2026-07-24T12:00:00.000000Z",
            signal_type="capability_absence", provenance="explicit_user",
            source_profile_ref=pref, source_project_ref=None,
            source_session_ref=sref, generation_id="a" * 64,
            gnothi_revision_digest=None, telos_digest=digest,
            capability_key="webcam", operation_key="video.stream",
            outcome_key="device_missing", constraint_key="unconstrained",
            severity="high", task_impact="high", retry_count=1,
            latency_bucket=None, explicit_user_intent=True,
            recovered=False, evidence_refs=(), redaction_status="verified_redacted",
        )

    service.ingest_envelope(_env("11111111-1111-1111-1111-111111111111", "prof_a", "sess_a"))
    service.ingest_envelope(_env("22222222-2222-2222-2222-222222222222", "prof_b", "sess_b"))

    suggestions = service.scan_and_update_suggestions()
    assert len(suggestions) == 1
    assert suggestions[0].state == "eligible"
    assert "prof_a" not in suggestions[0].summary_reason
    ledger.connection.close()


# ── Scenario 3: Performance Feedback ──

def test_scenario_performance_feedback_and_project_isolation(tmp_path, monkeypatch):
    """Friction signal creates a performance diagnosis suggestion."""
    org, org_id, ledger, tstore = _setup_organism(tmp_path, monkeypatch)
    telos = _save_telos(tstore, org_id, "Perf Telos")
    digest = telos.canonical_digest
    _gateway_activate(ledger, org_id, digest)

    from hermes_cli.evolution.observation_contract import ObservationEnvelope
    from hermes_cli.evolution.observer_service import ObserverService

    service = ObserverService(org)
    env = ObservationEnvelope(
        schema_version=1, event_id="33333333-3333-3333-3333-333333333333",
        organism_id=org_id, occurred_at="2026-07-24T12:10:00.000000Z",
        signal_type="friction", provenance="measured_runtime",
        source_profile_ref="prof_c", source_project_ref=None,
        source_session_ref="sess_c", generation_id="a" * 64,
        gnothi_revision_digest=None, telos_digest=digest,
        capability_key="performance", operation_key="query.execution",
        outcome_key="high_latency", constraint_key="unconstrained",
        severity="medium", task_impact="medium", retry_count=2,
        latency_bucket="15s_to_60s", explicit_user_intent=False,
        recovered=True, evidence_refs=(), redaction_status="verified_redacted",
    )
    service.ingest_envelope(env)
    suggestions = service.scan_and_update_suggestions()
    assert len(suggestions) == 1
    assert suggestions[0].score > 0.0
    ledger.connection.close()


# -- Pass J: Project Isolation --

def test_project_isolation_two_profiles_one_organism(tmp_path, monkeypatch):
    """Two profiles share one organism. Raw logs separate. Suggestion deduplicates.
    No profile/project/session identity leaks into summary."""
    import hermes_constants as _hc
    from hermes_cli.evolution import organism_home as _oh

    # Shared organism root
    org = tmp_path / "organism"
    monkeypatch.setattr(_hc, "get_organism_home", lambda: org)
    monkeypatch.setattr(_oh, "get_organism_home", lambda: org)
    monkeypatch.setattr(_hc, "get_default_hermes_root", lambda: tmp_path / ".hermes")
    from hermes_cli.evolution import lifecycle_global as _lg
    monkeypatch.setattr(_lg, "get_organism_home", lambda: org)

    org_root, org_id, ledger, tstore = _setup_organism(tmp_path, monkeypatch)
    telos = _save_telos(tstore, org_id, "Project Isolation Telos")
    digest = telos.canonical_digest
    _gateway_activate(ledger, org_id, digest)

    # Two distinct profiles with separate workspace roots
    profile_a = tmp_path / "profile_a"
    profile_b = tmp_path / "profile_b"
    profile_a.mkdir()
    profile_b.mkdir()

    from hermes_cli.evolution.observation_contract import ObservationEnvelope
    from hermes_cli.evolution.observer_service import ObserverService

    service = ObserverService(org)

    def _env(eid, pref, proj, sref):
        return ObservationEnvelope(
            schema_version=1, event_id=eid, organism_id=org_id,
            occurred_at="2026-07-24T12:00:00.000000Z",
            signal_type="capability_absence", provenance="explicit_user",
            source_profile_ref=pref, source_project_ref=proj,
            source_session_ref=sref, generation_id="a" * 64,
            gnothi_revision_digest=None, telos_digest=digest,
            capability_key="webcam", operation_key="capture",
            outcome_key="missing", constraint_key="none",
            severity="high", task_impact="high", retry_count=1,
            latency_bucket=None, explicit_user_intent=True,
            recovered=False, evidence_refs=(), redaction_status="verified_redacted",
        )

    service.ingest_envelope(_env("11111111-1111-1111-1111-111111111111", "profA", "projA", "sessA"))
    service.ingest_envelope(_env("22222222-2222-2222-2222-222222222222", "profB", "projB", "sessB"))

    suggestions = service.scan_and_update_suggestions()
    assert len(suggestions) == 1  # deduplication
    sug = suggestions[0]
    assert sug.observation_count == 2
    # No raw profile/project/session IDs leak
    assert "profA" not in sug.summary_reason
    assert "projA" not in sug.summary_reason
    assert "sessA" not in sug.summary_reason
    assert "profB" not in sug.summary_reason
    assert "projB" not in sug.summary_reason
    assert "sessB" not in sug.summary_reason
    ledger.connection.close()
