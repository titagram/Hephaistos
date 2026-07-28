"""Real behavior tests for TUI JSON-RPC Telos host approval.

Invokes the registered ``command.dispatch`` and ``approval.respond`` methods
with initialized temp organism state, captures emitted JSON-RPC frames, and
asserts field schema, same-session approval success, denial, cross-session
rejection, replay rejection, invalid digest no row, and dangerous-command
approval regression.
"""
from __future__ import annotations

import io
import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import hermes_constants as _hc
from hermes_cli.evolution.telos_contract import (
    CapabilityDirection,
    DesiredTrait,
    Priority,
    ProactivityPolicy,
    Prohibition,
    SuccessIndicator,
    TelosRevision,
)
from hermes_cli.evolution.telos_store import TelosStore

_original_stdout = sys.stdout


@pytest.fixture(autouse=True)
def _restore_stdout():
    yield
    sys.stdout = _original_stdout


def _make_telos(org_id, purpose, parent=None):
    return TelosRevision(
        schema_version=1, organism_id=org_id, parent_digest=parent,
        purpose=purpose,
        desired_traits=(DesiredTrait("t1", "d", ("t1",), 5),),
        capability_directions=(CapabilityDirection("c1", "d", ("c1",), 4),),
        priorities=(Priority("p1", "d", ("p1",), 5),),
        tradeoffs=(),
        prohibitions=(Prohibition("none", "None", ("none",), 5),),
        proactivity_policy=ProactivityPolicy("pass", "d", ("pass",), 3),
        success_indicators=(SuccessIndicator("i1", "d", ("i1",), 4),),
    )


def _setup_organism(tmp_path, monkeypatch):
    """Create a real organism with v5 ledger. Returns (org_root, organism_id)."""
    from hermes_cli.evolution import organism_home as _oh
    from hermes_cli.evolution.lifecycle_global import ensure_global_lifecycle_initialized

    org = tmp_path / "organism"
    monkeypatch.setattr(_hc, "get_organism_home", lambda: org)
    monkeypatch.setattr(_oh, "get_organism_home", lambda: org)
    monkeypatch.setattr(_hc, "get_default_hermes_root", lambda: tmp_path / ".hermes")
    from hermes_cli.evolution import lifecycle_global as _lg
    monkeypatch.setattr(_lg, "get_organism_home", lambda: org)

    ensure_global_lifecycle_initialized()
    from hermes_cli.evolution.organism_identity import load_organism_identity
    ident = load_organism_identity(org)
    return org, ident.organism_id


# ── Server fixture ────────────────────────────────────────────────────

@pytest.fixture()
def server():
    """Import tui_gateway.server with hermetic mocks."""
    with patch.dict("sys.modules", {
        "hermes_constants": MagicMock(get_hermes_home=MagicMock(return_value=Path("/tmp/hermes_test"))),
        "hermes_cli.env_loader": MagicMock(),
        "hermes_cli.banner": MagicMock(),
        "hermes_state": MagicMock(),
    }):
        import importlib
        mod = importlib.import_module("tui_gateway.server")
        yield mod
        mod._sessions.clear()
        mod._pending.clear()
        mod._answers.clear()


@pytest.fixture()
def capture(server):
    """Redirect server's real stdout to a StringIO."""
    buf = io.StringIO()
    server._real_stdout = buf
    return server, buf


def _register_tui_session(server, sid, session_key):
    """Register a minimal TUI session and patch organism path for Telos."""
    session = {"session_key": session_key}
    server._sessions[sid] = session
    return session


# ── command.dispatch: telos approve pending request ──

def test_command_dispatch_telos_approve_emits_pending(capture, tmp_path, monkeypatch):
    """command.dispatch with autopoiesis telos approve emits approval.request."""
    server, buf = capture
    org, org_id = _setup_organism(tmp_path, monkeypatch)

    monkeypatch.setattr(
        "hermes_cli.evolution.organism_home.get_organism_home", lambda: org,
    )

    t_a = _make_telos(org_id, "TUI Dispatch A")
    TelosStore(org).save_revision(t_a)
    digest = t_a.canonical_digest

    sid = "tui-session-1"
    session_key = "tui-key-1"
    _register_tui_session(server, sid, session_key)

    resp = server._methods["command.dispatch"](
        "r1",
        {
            "name": "autopoiesis",
            "arg": f"telos approve {digest}",
            "session_id": sid,
        },
    )

    assert resp.get("result", {}).get("type") == "telos_pending"
    assert resp["result"]["status"] == "pending"
    assert "request_id" in resp["result"]

    emitted = [json.loads(line) for line in buf.getvalue().strip().split("\n") if line.strip()]
    approval_requests = [
        e for e in emitted
        if e.get("params", {}).get("type") == "approval.request"
        and e["params"].get("payload", {}).get("domain") == "telos"
    ]
    assert len(approval_requests) == 1
    payload = approval_requests[0]["params"]["payload"]
    assert payload["domain"] == "telos"
    assert payload["request_id"] == resp["result"]["request_id"]
    assert payload["digest"] == digest
    assert payload["action"] == "activate"
    assert "bounded_summary" in payload
    assert "nonce" in payload
    assert "expires_at" in payload


def test_command_dispatch_telos_rollback_emits_pending(capture, tmp_path, monkeypatch):
    """command.dispatch with autopoiesis telos rollback emits approval.request."""
    server, buf = capture
    org, org_id = _setup_organism(tmp_path, monkeypatch)

    monkeypatch.setattr(
        "hermes_cli.evolution.organism_home.get_organism_home", lambda: org,
    )

    t_a = _make_telos(org_id, "TUI Dispatch Rollback")
    TelosStore(org).save_revision(t_a)
    digest = t_a.canonical_digest

    sid = "tui-roll-1"
    _register_tui_session(server, sid, "tui-roll-key-1")

    resp = server._methods["command.dispatch"](
        "r2",
        {
            "name": "autopoiesis",
            "arg": f"telos rollback {digest}",
            "session_id": sid,
        },
    )

    assert resp["result"]["type"] == "telos_pending"
    assert resp["result"]["status"] == "pending"

    emitted = [json.loads(line) for line in buf.getvalue().strip().split("\n") if line.strip()]
    approval_requests = [
        e for e in emitted
        if e.get("params", {}).get("type") == "approval.request"
        and e["params"].get("payload", {}).get("domain") == "telos"
    ]
    assert len(approval_requests) == 1
    assert approval_requests[0]["params"]["payload"]["action"] == "rollback"


def test_command_dispatch_invalid_digest_fails_closed(capture, tmp_path, monkeypatch):
    """command.dispatch with invalid digest returns rejected, no approval.request."""
    server, buf = capture
    org, org_id = _setup_organism(tmp_path, monkeypatch)

    monkeypatch.setattr(
        "hermes_cli.evolution.organism_home.get_organism_home", lambda: org,
    )

    sid = "tui-invalid-1"
    _register_tui_session(server, sid, "tui-invalid-key-1")

    resp = server._methods["command.dispatch"](
        "r3",
        {
            "name": "autopoiesis",
            "arg": "telos approve bad_digest",
            "session_id": sid,
        },
    )

    assert "error" in resp
    assert "invalid" in resp["error"]["message"].lower() or "telos" in resp["error"]["message"].lower()

    emitted = [json.loads(line) for line in buf.getvalue().strip().split("\n") if line.strip()]
    telos_events = [
        e for e in emitted
        if e.get("params", {}).get("type") == "approval.request"
    ]
    assert len(telos_events) == 0

    from hermes_cli.evolution.ledger import EvolutionLedger

    ledger = EvolutionLedger(org / "evolution" / "evolution.db")
    try:
        count = ledger.connection.execute(
            "SELECT COUNT(*) FROM telos_approval_requests"
        ).fetchone()[0]
        assert count == 0
    finally:
        ledger.connection.close()


def test_slash_exec_telos_approve_uses_host_dispatch(capture, tmp_path, monkeypatch):
    """The public slash path creates and emits the same session-bound request."""
    server, buf = capture
    org, org_id = _setup_organism(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "hermes_cli.evolution.organism_home.get_organism_home", lambda: org,
    )
    revision = _make_telos(org_id, "Slash Dispatch")
    TelosStore(org).save_revision(revision)

    sid = "tui-slash-1"
    _register_tui_session(server, sid, "tui-slash-key-1")
    response = server._methods["slash.exec"](
        "r-slash-1",
        {
            "command": f"/autopoiesis telos approve {revision.canonical_digest}",
            "session_id": sid,
        },
    )

    assert response["result"]["type"] == "telos_pending"
    assert response["result"]["status"] == "pending"
    frames = [
        json.loads(line)
        for line in buf.getvalue().splitlines()
        if line.strip()
    ]
    payloads = [
        frame["params"]["payload"]
        for frame in frames
        if frame.get("params", {}).get("type") == "approval.request"
    ]
    assert len(payloads) == 1
    assert payloads[0]["domain"] == "telos"
    assert payloads[0]["request_id"] == response["result"]["request_id"]


def test_slash_exec_telos_extra_token_fails_without_request(
    capture, tmp_path, monkeypatch
):
    """The public slash path rejects non-exact syntax before persistence."""
    server, buf = capture
    org, org_id = _setup_organism(tmp_path, monkeypatch)
    revision = _make_telos(org_id, "Slash Invalid")
    TelosStore(org).save_revision(revision)

    sid = "tui-slash-invalid"
    _register_tui_session(server, sid, "tui-slash-invalid-key")
    response = server._methods["slash.exec"](
        "r-slash-2",
        {
            "command": (
                f"/autopoiesis telos approve "
                f"{revision.canonical_digest} unexpected"
            ),
            "session_id": sid,
        },
    )

    assert "error" in response
    assert buf.getvalue() == ""

    from hermes_cli.evolution.ledger import EvolutionLedger

    ledger = EvolutionLedger(org / "evolution" / "evolution.db")
    try:
        count = ledger.connection.execute(
            "SELECT COUNT(*) FROM telos_approval_requests"
        ).fetchone()[0]
        assert count == 0
    finally:
        ledger.connection.close()


# ── approval.respond: domain=telos ──

def test_approval_respond_telos_approve_activates_pointer(capture, tmp_path, monkeypatch):
    """approval.respond with domain=telos choice=approved activates the pointer."""
    server, buf = capture
    org, org_id = _setup_organism(tmp_path, monkeypatch)
    store = TelosStore(org)

    monkeypatch.setattr(
        "hermes_cli.evolution.organism_home.get_organism_home", lambda: org,
    )

    t_a = _make_telos(org_id, "TUI Approve Success")
    store.save_revision(t_a)
    digest = t_a.canonical_digest

    sid = "tui-approve-1"
    session_key = "tui-approve-key-1"
    _register_tui_session(server, sid, session_key)

    dispatch_resp = server._methods["command.dispatch"](
        "r10",
        {"name": "autopoiesis", "arg": f"telos approve {digest}", "session_id": sid},
    )
    assert dispatch_resp["result"]["status"] == "pending"
    request_id = dispatch_resp["result"]["request_id"]

    buf.truncate(0)
    buf.seek(0)

    respond_resp = server._methods["approval.respond"](
        "r11",
        {
            "domain": "telos",
            "request_id": request_id,
            "choice": "approved",
            "session_id": sid,
        },
    )
    assert respond_resp.get("result", {}).get("status") == "approved"
    assert store.get_active_digest() == digest


def test_approval_respond_telos_deny_no_pointer(capture, tmp_path, monkeypatch):
    """approval.respond with domain=telos choice=denied does NOT activate."""
    server, buf = capture
    org, org_id = _setup_organism(tmp_path, monkeypatch)
    store = TelosStore(org)

    monkeypatch.setattr(
        "hermes_cli.evolution.organism_home.get_organism_home", lambda: org,
    )

    t_a = _make_telos(org_id, "TUI Deny")
    store.save_revision(t_a)
    digest = t_a.canonical_digest

    sid = "tui-deny-1"
    session_key = "tui-deny-key-1"
    _register_tui_session(server, sid, session_key)

    dispatch_resp = server._methods["command.dispatch"](
        "r20",
        {"name": "autopoiesis", "arg": f"telos approve {digest}", "session_id": sid},
    )
    request_id = dispatch_resp["result"]["request_id"]

    respond_resp = server._methods["approval.respond"](
        "r21",
        {
            "domain": "telos",
            "request_id": request_id,
            "choice": "denied",
            "session_id": sid,
        },
    )
    assert respond_resp.get("result", {}).get("status") == "denied"
    assert store.get_active_digest() is None


def test_approval_respond_telos_cross_session_rejected(capture, tmp_path, monkeypatch):
    """approval.respond from a different session rejects."""
    server, buf = capture
    org, org_id = _setup_organism(tmp_path, monkeypatch)
    store = TelosStore(org)

    monkeypatch.setattr(
        "hermes_cli.evolution.organism_home.get_organism_home", lambda: org,
    )

    t_a = _make_telos(org_id, "Cross Session")
    store.save_revision(t_a)
    digest = t_a.canonical_digest

    sid_a = "tui-cs-a"
    sid_b = "tui-cs-b"
    _register_tui_session(server, sid_a, "tui-cs-key-a")
    _register_tui_session(server, sid_b, "tui-cs-key-b")

    dispatch_resp = server._methods["command.dispatch"](
        "r30",
        {"name": "autopoiesis", "arg": f"telos approve {digest}", "session_id": sid_a},
    )
    request_id = dispatch_resp["result"]["request_id"]

    respond_resp = server._methods["approval.respond"](
        "r31",
        {
            "domain": "telos",
            "request_id": request_id,
            "choice": "approved",
            "session_id": sid_b,
        },
    )
    assert respond_resp.get("result", {}).get("status") in ("rejected", "denied")
    assert store.get_active_digest() is None


def test_approval_respond_telos_replay_rejected(capture, tmp_path, monkeypatch):
    """Second approval.respond for same request_id rejects."""
    server, buf = capture
    org, org_id = _setup_organism(tmp_path, monkeypatch)
    store = TelosStore(org)

    monkeypatch.setattr(
        "hermes_cli.evolution.organism_home.get_organism_home", lambda: org,
    )

    t_a = _make_telos(org_id, "Replay")
    store.save_revision(t_a)
    digest = t_a.canonical_digest

    sid = "tui-replay-1"
    session_key = "tui-replay-key-1"
    _register_tui_session(server, sid, session_key)

    dispatch_resp = server._methods["command.dispatch"](
        "r40",
        {"name": "autopoiesis", "arg": f"telos approve {digest}", "session_id": sid},
    )
    request_id = dispatch_resp["result"]["request_id"]

    # First approval
    r1 = server._methods["approval.respond"](
        "r41",
        {"domain": "telos", "request_id": request_id, "choice": "approved", "session_id": sid},
    )
    assert r1["result"]["status"] == "approved"

    # Second (replay)
    r2 = server._methods["approval.respond"](
        "r42",
        {"domain": "telos", "request_id": request_id, "choice": "approved", "session_id": sid},
    )
    assert r2["result"]["status"] in ("rejected", "denied")


def test_approval_respond_telos_unknown_request(capture, tmp_path, monkeypatch):
    """approval.respond with non-existent request_id rejects."""
    server, buf = capture
    org, org_id = _setup_organism(tmp_path, monkeypatch)

    monkeypatch.setattr(
        "hermes_cli.evolution.organism_home.get_organism_home", lambda: org,
    )

    sid = "tui-unknown-1"
    _register_tui_session(server, sid, "tui-unknown-key-1")

    resp = server._methods["approval.respond"](
        "r50",
        {
            "domain": "telos",
            "request_id": "nonexistent-req",
            "choice": "approved",
            "session_id": sid,
        },
    )
    assert resp["result"]["status"] == "rejected"


# ── Dangerous-command approval regression ──

def test_dangerous_command_approval_unchanged(server, monkeypatch):
    """approval.respond without domain or non-telos domain preserves original path."""
    sid = "tui-dc-1"
    session_key = "tui-dc-key-1"
    _register_tui_session(server, sid, session_key)

    from tools.approval import _ApprovalEntry, _gateway_queues

    entry = _ApprovalEntry({"command": "rm -rf /"})
    _gateway_queues[session_key] = [entry]

    resp = server._methods["approval.respond"](
        "r60",
        {"choice": "once", "session_id": sid},
    )
    assert resp["result"]["resolved"] >= 1
    assert entry.event.is_set()

    _gateway_queues.pop(session_key, None)


# ── A: Exact choice validation ──

@pytest.mark.parametrize("invalid_choice", ["once", "session", "always", "deny", "maybe", "", None])
def test_approval_respond_telos_rejects_non_approved_denied(capture, tmp_path, monkeypatch, invalid_choice):
    """approval.respond domain=telos rejects any choice other than exactly approved/denied."""
    server, buf = capture
    org, org_id = _setup_organism(tmp_path, monkeypatch)
    store = TelosStore(org)

    monkeypatch.setattr(
        "hermes_cli.evolution.organism_home.get_organism_home", lambda: org,
    )

    t_a = _make_telos(org_id, "Choice Val")
    store.save_revision(t_a)
    digest = t_a.canonical_digest

    sid = "tui-choice-1"
    session_key = "tui-choice-key-1"
    _register_tui_session(server, sid, session_key)

    dispatch_resp = server._methods["command.dispatch"](
        "r70",
        {"name": "autopoiesis", "arg": f"telos approve {digest}", "session_id": sid},
    )
    request_id = dispatch_resp["result"]["request_id"]

    params = {
        "domain": "telos",
        "request_id": request_id,
        "session_id": sid,
    }
    if invalid_choice is not None:
        params["choice"] = invalid_choice

    respond_resp = server._methods["approval.respond"]("r71", params)
    assert respond_resp.get("result", {}).get("status") == "rejected"
    assert "choice" in respond_resp["result"]["message"].lower()
    assert store.get_active_digest() is None


# ── H: Invalid choice leaves request pending, valid retry succeeds ──

def test_invalid_choice_leaves_request_pending_valid_retry_succeeds(capture, tmp_path, monkeypatch):
    """Invalid choice does NOT consume the request; valid approved retry succeeds."""
    server, buf = capture
    org, org_id = _setup_organism(tmp_path, monkeypatch)
    store = TelosStore(org)

    monkeypatch.setattr(
        "hermes_cli.evolution.organism_home.get_organism_home", lambda: org,
    )

    t_a = _make_telos(org_id, "Retry OK")
    store.save_revision(t_a)
    digest = t_a.canonical_digest

    sid = "tui-retry-1"
    session_key = "tui-retry-key-1"
    _register_tui_session(server, sid, session_key)

    dispatch_resp = server._methods["command.dispatch"](
        "r80",
        {"name": "autopoiesis", "arg": f"telos approve {digest}", "session_id": sid},
    )
    request_id = dispatch_resp["result"]["request_id"]

    # Invalid choice "once" → rejected, no decision, no pointer
    r1 = server._methods["approval.respond"](
        "r81",
        {"domain": "telos", "request_id": request_id, "choice": "once", "session_id": sid},
    )
    assert r1["result"]["status"] == "rejected"
    assert store.get_active_digest() is None

    # Valid "approved" retry on same request → succeeds
    r2 = server._methods["approval.respond"](
        "r82",
        {"domain": "telos", "request_id": request_id, "choice": "approved", "session_id": sid},
    )
    assert r2["result"]["status"] == "approved"
    assert store.get_active_digest() == digest


# ── B: Exact 3-token syntax / extra tokens / missing digest ──

def test_autopoiesis_telos_extra_token_fails_closed(capture, tmp_path, monkeypatch):
    """Extra token after digest fails closed — no request or event."""
    server, buf = capture
    org, org_id = _setup_organism(tmp_path, monkeypatch)

    monkeypatch.setattr(
        "hermes_cli.evolution.organism_home.get_organism_home", lambda: org,
    )

    t_a = _make_telos(org_id, "Extra Token")
    TelosStore(org).save_revision(t_a)
    digest = t_a.canonical_digest

    sid = "tui-extra-1"
    _register_tui_session(server, sid, "tui-extra-key-1")

    resp = server._methods["command.dispatch"](
        "r90",
        {"name": "autopoiesis", "arg": f"telos approve {digest} extra_baggage", "session_id": sid},
    )
    # Should be an error (extra token), not a pending telos request
    assert "error" in resp
    assert "exactly a single" in resp["error"]["message"].lower()

    emitted = [json.loads(line) for line in buf.getvalue().strip().split("\n") if line.strip()]
    telos_events = [
        e for e in emitted
        if e.get("params", {}).get("payload", {}).get("domain") == "telos"
    ]
    assert len(telos_events) == 0


def test_autopoiesis_telos_missing_digest_fails_closed(capture, tmp_path, monkeypatch):
    """Missing digest fails closed — no request or event."""
    server, buf = capture
    org, org_id = _setup_organism(tmp_path, monkeypatch)

    monkeypatch.setattr(
        "hermes_cli.evolution.organism_home.get_organism_home", lambda: org,
    )

    sid = "tui-miss-1"
    _register_tui_session(server, sid, "tui-miss-key-1")

    resp = server._methods["command.dispatch"](
        "r91",
        {"name": "autopoiesis", "arg": "telos approve", "session_id": sid},
    )
    assert "error" in resp
    assert "exactly a single" in resp["error"]["message"].lower()


# ── D: Empty/missing session_key rejected ──

def test_empty_session_key_rejected_before_request(server):
    """No session → no telos request created."""
    server._sessions["s2"] = {"session_key": ""}

    resp = server._methods["command.dispatch"](
        "r100",
        {"name": "autopoiesis", "arg": f"telos approve {'a' * 64}", "session_id": "s2"},
    )
    assert "error" in resp
    assert "session" in resp["error"]["message"].lower()


def test_empty_session_key_rejected_in_respond(server):
    """Empty session_key in respond → rejected."""
    server._sessions["s3"] = {"session_key": ""}

    resp = server._methods["approval.respond"](
        "r101",
        {"domain": "telos", "request_id": "req-1", "choice": "approved", "session_id": "s3"},
    )
    assert "error" in resp
    assert "session" in resp["error"]["message"].lower()
