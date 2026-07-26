"""P2 integration tests: safe observer ingestion, gating, durable dedup, and notice delivery.

Every test uses an isolated HERMES_HOME root with synthetic error logs.
"""

import json
import pytest
from argparse import Namespace
from pathlib import Path

import hermes_constants as _hc


# ── Helpers ──────────────────────────────────────────────────────────────


def _setup_organism(tmp_path: Path, monkeypatch):
    """Init global lifecycle + Telos activation. Returns (org_root, organism_id, ledger)."""
    from hermes_cli.evolution.command import evolution_command

    org = tmp_path / "organism"
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir(parents=True, exist_ok=True)
    _patch_all_bindings(monkeypatch, org, hermes_home)

    evolution_command(Namespace(action="init", json=True))
    from hermes_cli.evolution.organism_identity import load_organism_identity
    ident = load_organism_identity(org)
    return org, ident.organism_id


def _patch_all_bindings(monkeypatch, org, hermes_home):
    """Patch every module-level binding of get_organism_home and get_hermes_home.

    Many Hermes evolution modules import these from hermes_constants at module
    load time, creating local references that a hermes_constants-level monkeypatch
    won't reach.  This helper patches every known binding so tests can safely
    redirect both the organism root and the profile log source.
    """
    from hermes_cli.evolution import (
        command as _cmd,
        experience_bridge as _eb,
        profile_ref as _pr,
    )
    # These modules now use call-time access via hermes_constants module:
    # command.py, experience_bridge.py, profile_ref.py.
    # But we also need their MODULE-LEVEL imports from hermes_constants.
    # ExperienceBridge __init__ already uses hermes_constants call-time.
    # profile_ref.get_profile_ref already uses hermes_constants call-time.
    # command._status / _legacy_state_present already use hermes_constants call-time.
    # Extra belt: patch the hermes_constants module directly.
    import hermes_constants as _hc_mod
    monkeypatch.setattr(_hc_mod, "get_organism_home", lambda: org)
    monkeypatch.setattr(_hc_mod, "get_hermes_home", lambda: hermes_home)
    monkeypatch.setattr(_hc_mod, "get_default_hermes_root", lambda: hermes_home)

    from hermes_cli.evolution import organism_home as _oh
    monkeypatch.setattr(_oh, "get_organism_home", lambda: org)
    monkeypatch.setattr(_oh, "get_default_hermes_root", lambda: hermes_home)

    from hermes_cli.evolution import lifecycle_global as _lg
    monkeypatch.setattr(_lg, "get_organism_home", lambda: org)


def _save_and_activate_telos(org, org_id, ledger):
    """Save a test Telos revision and activate it through the real host-approval path."""
    from hermes_cli.evolution.telos_contract import (
        TelosRevision, DesiredTrait, CapabilityDirection,
        Priority, ProactivityPolicy, Prohibition, SuccessIndicator,
    )
    from hermes_cli.evolution.telos_store import TelosStore
    tstore = TelosStore(org)
    telos = TelosRevision(
        schema_version=1, organism_id=org_id, parent_digest=None,
        purpose="Test Telos for P2 observer ingestion",
        desired_traits=(DesiredTrait("reliable", "High reliability", ("reliable",), 5),),
        capability_directions=(CapabilityDirection("system.runtime", "Runtime stability.", ("system.runtime",), 4),),
        priorities=(Priority("safety", "Safety.", ("safety",), 5),),
        tradeoffs=(), prohibitions=(Prohibition("none", "None.", ("none",), 5),),
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
        HostApprovalContext, SqliteTelosApprovalBroker,
    )

    source = SessionSource(
        platform=Platform.TELEGRAM, user_id="p2-test",
        chat_id="p2-chat", chat_type="dm",
    )
    context = HostApprovalContext(
        surface="gateway", actor_ref="telegram:p2-test",
        session_ref=build_session_key(source),
        request_id=None, telos_digest=digest, action="activate",
        nonce="p2-approval", context_digest="ignored",
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
        source=source, message_id="p2-message",
    )
    asyncio.run(runner._handle_approve_command(event))
    return digest


def _write_errors_log(logs_dir, lines):
    """Write lines to errors.log and return the file path."""
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = logs_dir / "errors.log"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _read_observer_state(org_root):
    """Read observer_state.json, return dict or empty."""
    state_file = org_root / "observer_state.json"
    if state_file.exists():
        return json.loads(state_file.read_text(encoding="utf-8"))
    return {}


def _count_surface_events(org_root, svc=None):
    """Count how many 'surfaced' events exist using the SuggestionRepository."""
    from hermes_cli.evolution.suggestions import SuggestionRepository
    repo = svc.repository if svc else SuggestionRepository(org_root / "evolution" / "evolution.db")
    import sqlite3
    conn = sqlite3.connect(f"{org_root}/evolution/evolution.db")
    rows = conn.execute(
        "SELECT COUNT(*) as cnt FROM opportunity_suggestion_events WHERE next_state = 'surfaced'"
    ).fetchone()
    conn.close()
    return rows[0]


# ── Fixture: standard setup with organism + activated Telos ──────────────


@pytest.fixture
def organism_telos(tmp_path, monkeypatch):
    """Set up a global organism with an activated Telos. Returns (org_root, org_id)."""
    org, org_id = _setup_organism(tmp_path, monkeypatch)
    from hermes_cli.evolution.ledger import EvolutionLedger
    ledger = EvolutionLedger(org / "evolution" / "evolution.db")
    _save_and_activate_telos(org, org_id, ledger)
    return org, org_id


# ── Test 1: No Telos → no observations, suggestions, or notices ──────────


def test_no_telos_no_observations(tmp_path, monkeypatch):
    """Without an active Telos, import and scan produce nothing."""
    org, _ = _setup_organism(tmp_path, monkeypatch)

    _write_errors_log(org / "logs", [
        "2026-07-25 10:00:00 [ERROR] FileNotFoundError: /tmp/foo.txt",
        "2026-07-25 10:01:00 [ERROR] TimeoutError: connection timed out",
    ])

    from hermes_cli.evolution.experience_bridge import ExperienceBridge
    from hermes_cli.evolution.observer_service import ObserverService
    from hermes_cli.evolution.telos_store import TelosStore

    # No active Telos
    assert TelosStore(org).get_active_digest() is None

    svc = ObserverService(org)
    bridge = ExperienceBridge(
        organism_id="00000000-0000-0000-0000-000000000000",
        profile_ref="prof_test",
        generation_id="a" * 64,
        hermes_home=org,
    )

    envelopes = bridge.import_new_error_events()
    assert len(envelopes) == 2

    for env in envelopes:
        svc.ingest_envelope(env)

    # Without active Telos, scan returns nothing meaningful
    suggestions = svc.scan_and_update_suggestions()
    assert suggestions == []

    # notices from empty list are empty
    from hermes_cli.evolution.notices import generate_notices
    notices = generate_notices(suggestions)
    assert notices == []


# ── Test 2: Durable dedup — two equivalent errors → one suggestion, one surface event ─


def test_durable_dedup_two_equivalent_errors(organism_telos):
    """Two equivalent errors produce one eligible suggestion; surfacing prevents re-emission."""
    org, org_id = organism_telos

    from hermes_cli.evolution.observation_contract import ObservationEnvelope
    from hermes_cli.evolution.ledger import EvolutionLedger
    from hermes_cli.evolution.observer_service import ObserverService

    ledger = EvolutionLedger(org / "evolution" / "evolution.db")
    svc = ObserverService(org)

    # Manually insert two equivalent envelopes with explicit_user_intent + capability_absence
    # (these pass eligibility Gate 1 and produce a high enough score for notices)
    def _env(eid, pref, sref):
        return ObservationEnvelope(
            schema_version=1, event_id=eid, organism_id=org_id,
            occurred_at="2026-07-25T12:00:00.000000Z",
            signal_type="capability_absence", provenance="explicit_user",
            source_profile_ref=pref, source_project_ref=None,
            source_session_ref=sref, generation_id="a" * 64,
            gnothi_revision_digest=None, telos_digest=None,
            capability_key="webcam", operation_key="capture",
            outcome_key="missing", constraint_key="unconstrained",
            severity="high", task_impact="high", retry_count=0,
            latency_bucket=None, explicit_user_intent=True,
            recovered=False, evidence_refs=(), redaction_status="verified_redacted",
        )

    svc.ingest_envelope(_env("dedup-001", "prof_a", "sess_a"))
    svc.ingest_envelope(_env("dedup-002", "prof_b", "sess_b"))

    # First scan: one eligible suggestion (two envelopes cluster by opportunity key)
    suggestions1 = svc.scan_and_update_suggestions()
    assert len(suggestions1) >= 1
    sug1 = suggestions1[0]
    assert sug1.state == "eligible"
    assert sug1.observation_count == 2

    # Emit a notice (simulating drain_autopoiesis_notices)
    from hermes_cli.evolution.notices import generate_notices
    notices1 = generate_notices(suggestions1, notice_min_score=0.0)
    assert len(notices1) == 1
    # Transition to surfaced (durable dedup)
    svc.repository.update_suggestion_state(sug1.suggestion_id, "surfaced", "notice_emitted")

    # Verify surface event recorded
    assert _count_surface_events(org, svc) == 1

    # Second scan: no additional envelopes; suggestion is now surfaced
    suggestions2 = svc.scan_and_update_suggestions()
    notices2 = generate_notices(suggestions2, notice_min_score=0.0)
    assert len(notices2) == 0  # Already surfaced, no re-emission

    # Still only one surface event
    assert _count_surface_events(org, svc) == 1

    ledger.connection.close()


# ── Test 3: Cursor avoids re-import ──────────────────────────────────────


def test_cursor_avoids_reimport(tmp_path, monkeypatch):
    """ExperienceBridge cursor advances and avoids re-importing already-seen lines."""
    org, org_id = _setup_organism(tmp_path, monkeypatch)

    _write_errors_log(org / "logs", [
        "2026-07-25 10:00:00 [ERROR] FileNotFoundError: /tmp/a.txt",
        "2026-07-25 10:01:00 [ERROR] TimeoutError: timeout",
    ])

    from hermes_cli.evolution.experience_bridge import ExperienceBridge

    bridge = ExperienceBridge(
        organism_id=org_id,
        profile_ref="prof_cursor",
        generation_id="a" * 64,
        hermes_home=org,
    )

    # First import: 2 envelopes
    env1 = bridge.import_new_error_events()
    assert len(env1) == 2

    # Append more lines
    _write_errors_log(org / "logs", [
        "2026-07-25 10:00:00 [ERROR] FileNotFoundError: /tmp/a.txt",
        "2026-07-25 10:01:00 [ERROR] TimeoutError: timeout",
        "2026-07-25 10:02:00 [ERROR] ConnectionError: refused",
    ])

    # Second import: only the new line
    env2 = bridge.import_new_error_events()
    assert len(env2) == 1
    # ConnectionError -> classify_error_line strips "Error" suffix -> "connection"
    assert env2[0].outcome_key == "connection"


# ── Test 4: Pause/config-off prevents import and scan ────────────────────


def test_pause_prevents_import_and_scan(tmp_path, monkeypatch):
    """When autopoiesis is disabled, observer_enabled() returns False and scan is gated."""
    org, _ = _setup_organism(tmp_path, monkeypatch)

    from hermes_cli.evolution.global_config import observer_enabled, load_global_config, save_global_config

    # Default: autopoiesis.enabled=False → observer_enabled=False
    assert observer_enabled() is False

    # Observer scan should return 'paused'
    from hermes_cli.evolution.command import _observer_scan
    result = _observer_scan(org)
    assert result["status"] == "paused"
    assert result["count"] == 0

    # Enable autopoiesis but keep observer.enabled=false
    cfg = load_global_config()
    cfg["autopoiesis"]["enabled"] = True
    cfg["autopoiesis"]["observer"]["enabled"] = False
    save_global_config(cfg["autopoiesis"])
    assert observer_enabled() is False

    # Now enable observer
    cfg["autopoiesis"]["observer"]["enabled"] = True
    save_global_config(cfg["autopoiesis"])
    assert observer_enabled() is True


# ── Test 5: Circuit open and corrupt breaker fail closed ─────────────────


def test_circuit_open_fails_closed(tmp_path, monkeypatch):
    """Open circuit breaker and corrupt state file both prevent observer operations."""
    org, _ = _setup_organism(tmp_path, monkeypatch)

    from hermes_cli.evolution.observer_service import ObserverService, CircuitBreakerOpen
    from hermes_cli.evolution.observation_contract import ObservationEnvelope

    # Create a fresh service, trip the breaker
    svc = ObserverService(org, max_consecutive_errors=2)
    svc.record_error()
    svc.record_error()
    assert svc.circuit_open is True

    # ingest_envelope should raise
    env = ObservationEnvelope(
        schema_version=1, event_id="fail-env-001",
        organism_id="00000000-0000-0000-0000-000000000000",
        occurred_at="2026-07-25T12:00:00.000000Z",
        signal_type="failure", provenance="measured_runtime",
        source_profile_ref="prof_fail", source_project_ref=None,
        source_session_ref=None, generation_id="a" * 64,
        gnothi_revision_digest=None, telos_digest=None,
        capability_key="system.runtime", operation_key="error.log",
        outcome_key="failure", constraint_key="unconstrained",
        severity="medium", task_impact="low", retry_count=0,
        latency_bucket=None, explicit_user_intent=False,
        recovered=False, evidence_refs=(), redaction_status="verified_redacted",
    )
    with pytest.raises(CircuitBreakerOpen):
        svc.ingest_envelope(env)

    # scan_and_update_suggestions should raise
    with pytest.raises(CircuitBreakerOpen):
        svc.scan_and_update_suggestions()

    # Corrupt state file
    state_file = org / "observer_state.json"
    state_file.write_text("{corrupt json", encoding="utf-8")
    svc2 = ObserverService(org)
    assert svc2.circuit_open is True
    assert svc2.degraded_reason == "observer_state_corrupted"


# ── Test 6: No raw error content or filesystem paths in output/persistence ──


def test_no_raw_content_or_paths(tmp_path, monkeypatch, organism_telos):
    """Envelopes, suggestions, and scan output contain no raw error text or filesystem paths."""
    import re
    org, org_id = organism_telos

    # Enable autopoiesis so observer gate does not block
    from hermes_cli.evolution.global_config import load_global_config, save_global_config
    (tmp_path / ".hermes").mkdir(parents=True, exist_ok=True)
    cfg = load_global_config()
    cfg["autopoiesis"]["enabled"] = True
    cfg["autopoiesis"]["observer"]["enabled"] = True
    save_global_config(cfg["autopoiesis"])

    # Write errors with path-like content
    _write_errors_log(org / "logs", [
        "2026-07-25 10:00:00 [ERROR] FileNotFoundError: /Users/me/secret.txt",
        "2026-07-25 10:01:00 [ERROR] PermissionError: /etc/shadow",
    ])

    from hermes_cli.evolution.experience_bridge import ExperienceBridge
    from hermes_cli.evolution.observer_service import ObserverService
    from hermes_cli.evolution.reconcile import reconcile_evolution_state

    gen = reconcile_evolution_state(repair=False, evolution_root=org / "evolution")
    bridge = ExperienceBridge(
        organism_id=org_id,
        profile_ref="prof_privacy",
        generation_id=gen.active.generation_id,
        hermes_home=org,
    )

    envelopes = bridge.import_new_error_events()
    # Envelopes must not contain raw error text nor paths
    for env in envelopes:
        raw = env.to_canonical_json()
        # No secret path content
        assert "secret.txt" not in raw
        assert "etc/shadow" not in raw
        # No filesystem path indicators in ref fields
        assert "/" not in env.source_profile_ref
        # Taxonomy keys are closed-form
        assert re.fullmatch(r"[a-z][a-z0-9_.-]+", env.capability_key)

    svc = ObserverService(org)
    for env in envelopes:
        svc.ingest_envelope(env)
    suggestions = svc.scan_and_update_suggestions()

    for sug in suggestions:
        # Summary reason must not contain raw paths or profiles
        assert "/Users/" not in sug.summary_reason
        assert "prof_privacy" not in sug.summary_reason

    # Scan output (via _observer_scan) must not leak paths
    from hermes_cli.evolution.command import _observer_scan
    scan_result = _observer_scan(org)
    assert scan_result["status"] == "completed"
    assert scan_result["count"] >= 1
    output_json = json.dumps(scan_result)
    assert "secret.txt" not in output_json
    assert "etc/shadow" not in output_json


# ── Test 7: Manual observer scan returns honest status ───────────────────


def test_observer_scan_returns_honest_status(tmp_path, monkeypatch):
    """Manual observer scan CLI returns correct machine-readable status through all gates."""
    from hermes_cli.evolution.command import _observer_scan

    org, _ = _setup_organism(tmp_path, monkeypatch)

    # Enable autopoiesis + observer so gates do not block before Telos check
    from hermes_cli.evolution.global_config import load_global_config, save_global_config
    (tmp_path / ".hermes").mkdir(parents=True, exist_ok=True)
    cfg = load_global_config()
    cfg["autopoiesis"]["enabled"] = True
    cfg["autopoiesis"]["observer"]["enabled"] = True
    save_global_config(cfg["autopoiesis"])

    # Before Telos activation: not_ready
    result = _observer_scan(org)
    assert result["status"] == "not_ready"
    assert result["reason"] == "no_active_telos"

    # Activate Telos and scan again: should complete (even with empty errors)
    from hermes_cli.evolution.ledger import EvolutionLedger
    ledger = EvolutionLedger(org / "evolution" / "evolution.db")
    _save_and_activate_telos(org, _parse_org_id(org), ledger)

    result2 = _observer_scan(org)
    assert result2["status"] == "completed"
    assert "count" in result2

    # Corrupt observer state → degraded
    state_file = org / "observer_state.json"
    state_file.write_text("{garbage}", encoding="utf-8")
    result3 = _observer_scan(org)
    assert result3["status"] == "degraded"

    # Repair state
    state_file.write_text('{"consecutive_errors": 0, "circuit_open": false}', encoding="utf-8")

    # Trip circuit → degraded
    from hermes_cli.evolution.observer_service import ObserverService
    svc = ObserverService(org, max_consecutive_errors=1)
    svc.record_error()
    result4 = _observer_scan(org)
    assert result4["status"] == "degraded"


def _parse_org_id(org_path):
    """Read organism_id from identity.json."""
    data = json.loads((org_path / "identity.json").read_text(encoding="utf-8"))
    return data["organism_id"]


# ── Test 8: Manual scan imports errors with active Telos digest ───────────


def test_observer_scan_manual_imports_with_telos_digest(tmp_path, monkeypatch, organism_telos):
    """Manual _observer_scan imports profile-local errors and persists envelopes with active Telos digest."""
    import sqlite3
    org, org_id = organism_telos

    # Enable autopoiesis + observer in global config
    from hermes_cli.evolution.global_config import load_global_config, save_global_config
    (tmp_path / ".hermes").mkdir(parents=True, exist_ok=True)
    cfg = load_global_config()
    cfg["autopoiesis"]["enabled"] = True
    cfg["autopoiesis"]["observer"]["enabled"] = True
    save_global_config(cfg["autopoiesis"])

    # Write synthetic errors to profile-local hermes home
    profile_home = tmp_path / "hermes_home"
    monkeypatch.setattr(_hc, "get_hermes_home", lambda: profile_home)
    _write_errors_log(profile_home / "logs", [
        "2026-07-25 10:00:00 [ERROR] FileNotFoundError: /tmp/secret.txt",
        "2026-07-25 10:01:00 [ERROR] ConnectionError: db timeout",
    ])

    from hermes_cli.evolution.command import _observer_scan
    from hermes_cli.evolution.telos_store import TelosStore
    digest = TelosStore(org).get_active_digest()
    assert digest is not None

    result = _observer_scan(org)
    assert result["status"] == "completed", f"Got status={result.get('status')} reason={result.get('reason')}"
    assert result["count"] >= 1

    # Persisted envelopes carry the active Telos digest
    conn = sqlite3.connect(f"{org}/evolution/evolution.db")
    rows = conn.execute(
        "SELECT canonical_envelope_json FROM observation_envelopes ORDER BY occurred_at ASC"
    ).fetchall()
    conn.close()
    assert len(rows) >= 1
    for row in rows:
        env = json.loads(row[0])
        assert env["telos_digest"] == digest, f"Missing or wrong telos_digest: {env['telos_digest']}"

    # JSON output contains no raw path or error content
    output_json = json.dumps(result)
    assert "secret.txt" not in output_json
    assert "/tmp/" not in output_json
    assert "hermes_home" not in output_json
    assert "profile_ref" not in output_json


# ── Test 9: Re-scan with unchanged log does not duplicate ─────────────────


def test_observer_scan_dedup_no_duplicate_import(tmp_path, monkeypatch, organism_telos):
    """Repeated _observer_scan with no new errors must not produce new observations."""
    import sqlite3
    org, org_id = organism_telos

    from hermes_cli.evolution.global_config import load_global_config, save_global_config
    (tmp_path / ".hermes").mkdir(parents=True, exist_ok=True)
    cfg = load_global_config()
    cfg["autopoiesis"]["enabled"] = True
    cfg["autopoiesis"]["observer"]["enabled"] = True
    save_global_config(cfg["autopoiesis"])

    profile_home = tmp_path / "dedup_home"
    monkeypatch.setattr(_hc, "get_hermes_home", lambda: profile_home)
    _write_errors_log(profile_home / "logs", [
        "2026-07-25 10:00:00 [ERROR] FileNotFoundError: test.txt",
        "2026-07-25 10:01:00 [ERROR] TimeoutError: slow",
    ])

    from hermes_cli.evolution.command import _observer_scan

    # First scan
    result1 = _observer_scan(org)
    assert result1["status"] == "completed"
    assert result1["count"] >= 1

    conn = sqlite3.connect(f"{org}/evolution/evolution.db")
    before = conn.execute("SELECT COUNT(*) FROM observation_envelopes").fetchone()[0]
    conn.close()
    assert before >= 1

    # Second scan with identical log — no new observations
    result2 = _observer_scan(org)
    assert result2["status"] == "completed"

    conn = sqlite3.connect(f"{org}/evolution/evolution.db")
    after = conn.execute("SELECT COUNT(*) FROM observation_envelopes").fetchone()[0]
    conn.close()
    assert after == before, f"Observations grew from {before} to {after}; expected no change"


# ── Test 10: Gate failure prevents cursor creation and persistence ────────


def test_observer_scan_gate_autopoiesis_disabled(tmp_path, monkeypatch, organism_telos):
    """With autopoiesis disabled, manual scan returns paused without cursor or observations."""
    import sqlite3
    org, org_id = organism_telos

    # Explicitly disable autopoiesis to guarantee gate closure
    from hermes_cli.evolution.global_config import load_global_config, save_global_config
    (tmp_path / ".hermes").mkdir(parents=True, exist_ok=True)
    cfg = load_global_config()
    cfg["autopoiesis"]["enabled"] = False
    cfg["autopoiesis"]["observer"]["enabled"] = True
    save_global_config(cfg["autopoiesis"])

    profile_home = tmp_path / "gated_home"
    monkeypatch.setattr(_hc, "get_hermes_home", lambda: profile_home)
    _write_errors_log(profile_home / "logs", [
        "2026-07-25 10:00:00 [ERROR] FileNotFoundError: test.txt",
    ])

    from hermes_cli.evolution.command import _observer_scan
    result = _observer_scan(org)
    assert result["status"] == "paused"
    assert result["reason"] == "autopoiesis_disabled"
    assert result["count"] == 0

    # No cursor file created
    cursor_file = profile_home / "logs" / ".experience_cursor.json"
    assert not cursor_file.exists(), "Cursor file must not be created when gate fails"

    # No observations persisted
    conn = sqlite3.connect(f"{org}/evolution/evolution.db")
    count = conn.execute("SELECT COUNT(*) FROM observation_envelopes").fetchone()[0]
    conn.close()
    assert count == 0, f"Expected 0 observations, found {count}"


# ── Test 11: Post-response drain passes active Telos digest ───────────────


def test_post_response_passes_telos_digest(tmp_path, monkeypatch):
    """Verify drain_autopoiesis_notices passes the active Telos digest into ExperienceBridge."""
    org, org_id = _setup_organism(tmp_path, monkeypatch)

    from hermes_cli.evolution.ledger import EvolutionLedger
    ledger = EvolutionLedger(org / "evolution" / "evolution.db")
    digest = _save_and_activate_telos(org, org_id, ledger)

    # Enable autopoiesis + observer
    from hermes_cli.evolution.global_config import load_global_config, save_global_config
    (tmp_path / ".hermes").mkdir(parents=True, exist_ok=True)
    cfg = load_global_config()
    cfg["autopoiesis"]["enabled"] = True
    cfg["autopoiesis"]["observer"]["enabled"] = True
    save_global_config(cfg["autopoiesis"])

    # Profile home with errors.log
    profile_home = tmp_path / "post_home"
    monkeypatch.setattr(_hc, "get_hermes_home", lambda: profile_home)
    _write_errors_log(profile_home / "logs", [
        "2026-07-25 10:00:00 [ERROR] FileNotFoundError: test.txt",
    ])

    # Patch ExperienceBridge to capture telos_digest
    from hermes_cli.evolution.experience_bridge import ExperienceBridge as _RealEB

    captured: dict = {}

    class _CaptureBridge(_RealEB):
        def __init__(self, *args, **kwargs):
            captured["telos_digest"] = kwargs.get("telos_digest")
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(
        "hermes_cli.evolution.experience_bridge.ExperienceBridge",
        _CaptureBridge,
    )

    # Create minimal mock agent and invoke the method
    from unittest.mock import MagicMock

    mock_agent = MagicMock()
    from run_agent import AIAgent
    AIAgent.drain_autopoiesis_notices(mock_agent)

    assert captured.get("telos_digest") == digest, (
        f"Expected telos_digest={digest}, got {captured.get('telos_digest')}"
    )
