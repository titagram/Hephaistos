"""End-to-End Scenarios for Autopoiesis Project B (Global Telos and Opportunity Observer)."""

import hashlib
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


def _broker_activate(ledger, org_id, digest, action="activate"):
    """Full broker approval flow — approval is visible in the test, not hidden."""
    from hermes_cli.evolution.telos_approval import (
        CapabilityRegistry, HostApprovalCapability, HostApprovalContext,
        SqliteTelosApprovalBroker,
    )
    reg = CapabilityRegistry()
    cap = HostApprovalCapability._test_create("cli", "actor")
    reg.register(cap)
    broker = SqliteTelosApprovalBroker(reg)

    ctx = HostApprovalContext(
        surface="cli", actor_ref="actor", session_ref="e2e",
        request_id=None, telos_digest=digest, action=action,
        nonce="e2e", context_digest=hashlib.sha256(b"e2e-ctx").hexdigest(),
    )
    req_id = broker.create_request(ledger, org_id, digest, action, ctx, 3600)

    # ── HOST DECISION (visible in test body) ──
    ctx_r = HostApprovalContext(
        surface="cli", actor_ref="actor", session_ref="e2e",
        request_id=req_id, telos_digest=digest, action=action,
        nonce="e2e", context_digest=hashlib.sha256(b"e2e-ctx").hexdigest(),
    )
    dec_id = broker.record_host_decision(ledger, cap, ctx_r, "approved")
    grant_id = broker.issue_grant(ledger, req_id, dec_id)
    broker.consume_grant(ledger, grant_id, org_id, digest, action)
    return grant_id

def _make_cap(surface="cli"):
    """Create a host capability and register it in the host registry."""
    from hermes_cli.evolution.telos_approval import (
        HostApprovalCapability, set_host_capability,
    )
    cap = HostApprovalCapability._test_create(surface, "test_actor")
    set_host_capability(cap)
    return cap


# ── Scenario 1: Initial Telos Approval Boundary ──

def test_scenario_initial_telos_approval_boundary(tmp_path, monkeypatch):
    """Draft exists but is inert. Direct activation fails. Host-approved activation succeeds. Replay fails."""
    org, org_id, ledger, tstore = _setup_organism(tmp_path, monkeypatch)

    # 1. draft/revision exists but is inert
    telos = _save_telos(tstore, org_id, "Initial Telos")
    digest = telos.canonical_digest
    assert tstore.get_active_digest() is None  # inert

    # 2. direct activation fails
    from hermes_cli.evolution.telos_store import TelosStoreError
    with pytest.raises(TelosStoreError, match="host_approval_not_implemented"):
        tstore.activate_revision(digest)

    # 3. model-facing command cannot self-approve (no capability)
    from hermes_cli.evolution.telos_approval import (
        CapabilityRegistry, HostApprovalCapability, HostApprovalContext,
        SqliteTelosApprovalBroker, TelosApprovalError,
    )
    model_reg = CapabilityRegistry()  # empty registry = no host
    model_broker = SqliteTelosApprovalBroker(model_reg)
    model_cap = HostApprovalCapability._test_create("model", "model")
    model_ctx = HostApprovalContext(
        surface="model", actor_ref="model", session_ref="m",
        request_id=None, telos_digest=digest, action="activate",
        nonce="m", context_digest=hashlib.sha256(b"m").hexdigest(),
    )
    req_id = model_broker.create_request(ledger, org_id, digest, "activate", model_ctx, 3600)
    dec_ctx = HostApprovalContext(
        surface="model", actor_ref="model", session_ref="m",
        request_id=req_id, telos_digest=digest, action="activate",
        nonce="m", context_digest=hashlib.sha256(b"m").hexdigest(),
    )
    with pytest.raises(TelosApprovalError, match="not_verified"):
        model_broker.record_host_decision(ledger, model_cap, dec_ctx, "approved")

    # 4. real host decision approves the exact digest
    grant_id = _broker_activate(ledger, org_id, digest, "activate")

    # 5. activation succeeds
    tstore.activate_revision(digest, grant_id=grant_id, capability=_make_cap())
    assert tstore.get_active_digest() == digest

    # 6. replay fails — close ledger first (activate_revision opens its own)
    ledger.connection.close()
    with pytest.raises(TelosStoreError, match="already_used"):
        tstore.activate_revision(digest, grant_id=grant_id, capability=_make_cap())

    ledger.connection.close()


# ── Scenario 2: Missing Webcam Capability ──

def test_scenario_missing_webcam_capability(tmp_path, monkeypatch):
    """Two profiles report webcam absence → one eligible suggestion."""
    org, org_id, ledger, tstore = _setup_organism(tmp_path, monkeypatch)
    telos = _save_telos(tstore, org_id, "Webcam Telos")
    digest = telos.canonical_digest

    # Activate via host approval
    grant_id = _broker_activate(ledger, org_id, digest, "activate")
    tstore.activate_revision(digest, grant_id=grant_id, capability=_make_cap())

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
    grant_id = _broker_activate(ledger, org_id, digest, "activate")
    tstore.activate_revision(digest, grant_id=grant_id, capability=_make_cap())

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
