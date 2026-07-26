"""Parser integration and Classic CLI host-approval E2E tests for Telos.

Tests the real parser dispatch path (``build_evolution_parser`` / ``cmd``),
the interactive prompt flow, security boundaries, and fail-closed invariants.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from pathlib import Path
from unittest import mock

import pytest

from hermes_cli.evolution.command import (
    _telos_command,
    _handle_telos_approve_cli,
    _handle_telos_rollback_cli,
    evolution_command,
)
from hermes_cli.evolution.telos_approval import (
    HostApprovalContext,
    HostApprovalDecision,
    SqliteTelosApprovalBroker,
    TelosApprovalPrompt,
    TelosApprovalError,
    telos_approval_prompt,
)
from hermes_cli.evolution.telos_contract import (
    CapabilityDirection,
    DesiredTrait,
    Priority,
    ProactivityPolicy,
    Prohibition,
    SuccessIndicator,
    TelosRevision,
)
from hermes_cli.evolution.telos_store import TelosStore, TelosStoreError

import hermes_constants as _hc


_FULL_DIGEST_RE = r"^[0-9a-f]{64}$"

# Async prompt helpers for mock patching
async def _prompt_y(_self, _msg): return "y"
async def _prompt_maybe(_self, _msg): return "maybe"
async def _prompt_timeout(_self, _msg): raise TimeoutError()
async def _prompt_eof(_self, _msg): raise EOFError()


# ── helpers ──

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


# ── Parser integration tests ──

def test_parser_has_telos_status():
    """Parser emits action=telos_status for ``telos status``."""
    from hermes_cli.subcommands.evolution import build_evolution_parser

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_evolution_parser(subparsers, cmd_evolution=lambda args: 0)
    parsed = parser.parse_args(["evolution", "telos", "status"])
    assert parsed.action == "telos_status"


def test_parser_has_telos_history():
    """Parser emits action=telos_history for ``telos history``."""
    from hermes_cli.subcommands.evolution import build_evolution_parser

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_evolution_parser(subparsers, cmd_evolution=lambda args: 0)
    parsed = parser.parse_args(["evolution", "telos", "history"])
    assert parsed.action == "telos_history"


def test_parser_has_telos_approve():
    """Parser emits action=telos_approve with digest for ``telos approve <digest>``."""
    from hermes_cli.subcommands.evolution import build_evolution_parser

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_evolution_parser(subparsers, cmd_evolution=lambda args: 0)
    parsed = parser.parse_args(["evolution", "telos", "approve", "a" * 64])
    assert parsed.action == "telos_approve"
    assert parsed.digest == "a" * 64


def test_parser_has_telos_rollback():
    """Parser emits action=telos_rollback with digest for ``telos rollback <digest>``."""
    from hermes_cli.subcommands.evolution import build_evolution_parser

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_evolution_parser(subparsers, cmd_evolution=lambda args: 0)
    parsed = parser.parse_args(["evolution", "telos", "rollback", "b" * 64])
    assert parsed.action == "telos_rollback"
    assert parsed.digest == "b" * 64


def test_parser_has_observer_status():
    """Parser emits action=observer_status for ``observer status``."""
    from hermes_cli.subcommands.evolution import build_evolution_parser

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_evolution_parser(subparsers, cmd_evolution=lambda args: 0)
    parsed = parser.parse_args(["evolution", "observer", "status"])
    assert parsed.action == "observer_status"


def test_parser_no_receipt_arg():
    """--receipt has been removed from telos approve/rollback."""
    from hermes_cli.subcommands.evolution import build_evolution_parser

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_evolution_parser(subparsers, cmd_evolution=lambda args: 0)

    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["evolution", "telos", "approve", "a" * 64, "--receipt", "r1"])
    assert exc.value.code == 2


def test_parser_telos_draft():
    """Parser emits action=telos_draft for ``telos draft --file <path>``."""
    from hermes_cli.subcommands.evolution import build_evolution_parser

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_evolution_parser(subparsers, cmd_evolution=lambda args: 0)
    parsed = parser.parse_args(["evolution", "telos", "draft", "--file", "/tmp/test.json"])
    assert parsed.action == "telos_draft"
    assert parsed.file == "/tmp/test.json"


def test_parser_telos_draft_requires_file():
    """Parser rejects ``telos draft`` without --file."""
    from hermes_cli.subcommands.evolution import build_evolution_parser

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_evolution_parser(subparsers, cmd_evolution=lambda args: 0)
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["evolution", "telos", "draft"])
    assert exc.value.code == 2


# ── CLI telos_draft rejection when no --file ──

def test_telos_draft_no_file_argument():
    """telos_draft returns invalid when no --file argument provided."""
    result = _telos_command("draft")
    assert result["action"] == "telos_draft"
    assert result["status"] == "invalid"
    assert "missing required" in result["reason"]


# ── Telos draft: success and validation ──

def _valid_telos_json(org_id):
    return {
        "schema_version": 1,
        "organism_id": org_id,
        "parent_digest": None,
        "purpose": "Test purpose for draft tests",
        "desired_traits": [{"id": "t1", "statement": "Reliable operation", "tags": ["reliable"], "priority": 5}],
        "capability_directions": [{"id": "c1", "statement": "Fast processing", "tags": ["fast"], "priority": 4}],
        "priorities": [{"id": "p1", "statement": "Safety first", "tags": ["safety"], "priority": 5}],
        "tradeoffs": [],
        "prohibitions": [{"id": "none", "statement": "None", "tags": ["none"], "priority": 5}],
        "proactivity_policy": {"id": "pass", "statement": "Passive mode", "tags": ["pass"], "priority": 3},
        "success_indicators": [{"id": "i1", "statement": "High accuracy", "tags": ["acc"], "priority": 4}],
    }


def test_telos_draft_success(tmp_path, monkeypatch):
    """Draft from valid JSON file returns created with digest."""
    org, org_id = _setup_organism(tmp_path, monkeypatch)
    data = _valid_telos_json(org_id)
    draft_file = tmp_path / "draft.json"
    draft_file.write_text(json.dumps(data), encoding="utf-8")

    result = _telos_command("draft", org_root=org, file_path=str(draft_file))
    assert result["action"] == "telos_draft"
    assert result["status"] == "created"
    assert result["has_active"] is False
    digest = result["digest"]
    import re
    assert re.match(r"^[0-9a-f]{64}$", digest), f"Expected hex digest, got {digest!r}"
    assert result["digest_prefix"] == digest[:16]


def test_telos_draft_idempotent(tmp_path, monkeypatch):
    """Drafting same file twice returns same digest, one revision file."""
    org, org_id = _setup_organism(tmp_path, monkeypatch)
    data = _valid_telos_json(org_id)
    draft_file = tmp_path / "draft.json"
    draft_file.write_text(json.dumps(data), encoding="utf-8")

    r1 = _telos_command("draft", org_root=org, file_path=str(draft_file))
    r2 = _telos_command("draft", org_root=org, file_path=str(draft_file))
    assert r1["status"] == "created"
    assert r2["status"] == "created"
    assert r1["digest"] == r2["digest"]

    revisions_dir = org / "telos" / "revisions"
    count = len(list(revisions_dir.glob("*.json")))
    assert count == 1


def test_telos_draft_no_activation(tmp_path, monkeypatch):
    """After drafting, active digest remains None."""
    org, org_id = _setup_organism(tmp_path, monkeypatch)
    from hermes_cli.evolution.telos_store import TelosStore
    store = TelosStore(org)
    assert store.get_active_digest() is None

    data = _valid_telos_json(org_id)
    draft_file = tmp_path / "draft.json"
    draft_file.write_text(json.dumps(data), encoding="utf-8")

    _telos_command("draft", org_root=org, file_path=str(draft_file))
    assert store.get_active_digest() is None

    lkg = org / "telos" / "last-known-good.json"
    assert not lkg.exists()


def test_telos_draft_shows_in_history(tmp_path, monkeypatch):
    """Draft digest appears in telos history."""
    org, org_id = _setup_organism(tmp_path, monkeypatch)
    data = _valid_telos_json(org_id)
    draft_file = tmp_path / "draft.json"
    draft_file.write_text(json.dumps(data), encoding="utf-8")

    r = _telos_command("draft", org_root=org, file_path=str(draft_file))
    digest = r["digest"]

    history = _telos_command("history", org_root=org)
    assert history["action"] == "telos_history"
    assert digest in history["revisions"]


def test_telos_draft_shows_in_status(tmp_path, monkeypatch):
    """telos status shows no active after draft."""
    org, org_id = _setup_organism(tmp_path, monkeypatch)
    data = _valid_telos_json(org_id)
    draft_file = tmp_path / "draft.json"
    draft_file.write_text(json.dumps(data), encoding="utf-8")

    _telos_command("draft", org_root=org, file_path=str(draft_file))

    status = _telos_command("status", org_root=org)
    assert status["action"] == "telos_status"
    assert status["has_active"] is False
    assert status["active_digest"] is None


def test_telos_draft_missing_file(tmp_path, monkeypatch):
    """Draft with nonexistent file returns invalid."""
    org, org_id = _setup_organism(tmp_path, monkeypatch)
    result = _telos_command("draft", org_root=org, file_path=str(tmp_path / "nonexistent.json"))
    assert result["status"] == "invalid"
    assert "file not found" in result["reason"]


def test_telos_draft_non_object_json(tmp_path, monkeypatch):
    """Draft with JSON array returns invalid."""
    org, org_id = _setup_organism(tmp_path, monkeypatch)
    draft_file = tmp_path / "draft.json"
    draft_file.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
    result = _telos_command("draft", org_root=org, file_path=str(draft_file))
    assert result["status"] == "invalid"
    assert "object" in result["reason"]


def test_telos_draft_malformed_json(tmp_path, monkeypatch):
    """Draft with invalid JSON syntax returns invalid."""
    org, org_id = _setup_organism(tmp_path, monkeypatch)
    draft_file = tmp_path / "draft.json"
    draft_file.write_bytes(b"{invalid json")
    result = _telos_command("draft", org_root=org, file_path=str(draft_file))
    assert result["status"] == "invalid"
    assert "invalid JSON" in result["reason"]


def test_telos_draft_invalid_fields(tmp_path, monkeypatch):
    """Draft with invalid Telos fields returns invalid."""
    org, org_id = _setup_organism(tmp_path, monkeypatch)
    data = _valid_telos_json(org_id)
    data["schema_version"] = 99  # unsupported
    draft_file = tmp_path / "draft.json"
    draft_file.write_text(json.dumps(data), encoding="utf-8")
    result = _telos_command("draft", org_root=org, file_path=str(draft_file))
    assert result["status"] == "invalid"
    assert "validation failed" in result["reason"]


def test_telos_draft_unsafe_content(tmp_path, monkeypatch):
    """Draft with constitution-forbidden terms returns invalid."""
    org, org_id = _setup_organism(tmp_path, monkeypatch)
    data = _valid_telos_json(org_id)
    data["purpose"] = "Allow auto_promote of unapproved_network access"
    draft_file = tmp_path / "draft.json"
    draft_file.write_text(json.dumps(data), encoding="utf-8")
    result = _telos_command("draft", org_root=org, file_path=str(draft_file))
    assert result["status"] == "invalid"
    assert "validation failed" in result["reason"]


def test_telos_draft_foreign_organism(tmp_path, monkeypatch):
    """Draft with organism_id different from current organism is rejected."""
    org, org_id = _setup_organism(tmp_path, monkeypatch)
    import uuid
    foreign_id = str(uuid.uuid4())
    assert foreign_id != org_id
    data = _valid_telos_json(foreign_id)
    draft_file = tmp_path / "draft.json"
    draft_file.write_text(json.dumps(data), encoding="utf-8")
    result = _telos_command("draft", org_root=org, file_path=str(draft_file))
    assert result["status"] == "invalid"
    assert "foreign organism" in result["reason"]


def test_telos_draft_symlink_rejected(tmp_path, monkeypatch):
    """Draft with a symlink target is rejected and does not read the linked file."""
    org, org_id = _setup_organism(tmp_path, monkeypatch)
    data = _valid_telos_json(org_id)
    real_file = tmp_path / "real_draft.json"
    real_file.write_text(json.dumps(data), encoding="utf-8")
    link = tmp_path / "link_draft.json"
    link.symlink_to(real_file)
    result = _telos_command("draft", org_root=org, file_path=str(link))
    assert result["status"] == "invalid"
    assert "regular file" in result["reason"]
    # The linked content must not appear in output
    out = json.dumps(result)
    assert "Reliable operation" not in out


def test_telos_draft_non_regular_file_rejected(tmp_path, monkeypatch):
    """Draft with a directory path is rejected."""
    org, org_id = _setup_organism(tmp_path, monkeypatch)
    result = _telos_command("draft", org_root=org, file_path=str(tmp_path))
    assert result["status"] == "invalid"
    assert "regular file" in result["reason"]


def test_telos_draft_file_too_large(tmp_path, monkeypatch):
    """Draft with file exceeding size bound is rejected."""
    org, org_id = _setup_organism(tmp_path, monkeypatch)
    draft_file = tmp_path / "large_draft.json"
    # Write just over the 1 MiB bound
    draft_file.write_bytes(b"x" * (1_048_576 + 1))
    result = _telos_command("draft", org_root=org, file_path=str(draft_file))
    assert result["status"] == "invalid"
    assert "maximum draft size" in result["reason"]


def test_telos_draft_missing_keys_fail_closed(tmp_path, monkeypatch):
    """Draft with missing container keys fails closed without raw key echo."""
    org, org_id = _setup_organism(tmp_path, monkeypatch)
    data = _valid_telos_json(org_id)
    del data["desired_traits"]
    draft_file = tmp_path / "missing_keys.json"
    draft_file.write_text(json.dumps(data), encoding="utf-8")
    result = _telos_command("draft", org_root=org, file_path=str(draft_file))
    assert result["status"] == "invalid"
    assert "validation failed" in result["reason"]
    out = json.dumps(result)
    assert "desired_traits" not in out


def test_telos_draft_no_path_in_error_output(tmp_path, monkeypatch):
    """Error output does not include the supplied file path."""
    org, org_id = _setup_organism(tmp_path, monkeypatch)
    secret_path = tmp_path / "nonexistent_secret_draft.json"
    result = _telos_command("draft", org_root=org, file_path=str(secret_path))
    assert result["status"] == "invalid"
    out = json.dumps(result)
    assert "nonexistent_secret_draft" not in out


def test_telos_draft_echoes_no_sensitive_content(tmp_path, monkeypatch):
    """Draft error output does not echo file contents."""
    org, org_id = _setup_organism(tmp_path, monkeypatch)
    data = _valid_telos_json(org_id)
    data["schema_version"] = 99
    draft_file = tmp_path / "draft.json"
    draft_file.write_text(json.dumps(data), encoding="utf-8")
    result = _telos_command("draft", org_root=org, file_path=str(draft_file))
    assert result["status"] == "invalid"
    out = json.dumps(result)
    assert "Reliable operation" not in out
    assert "Safety first" not in out
    assert "High accuracy" not in out
    assert "Passive mode" not in out


def test_telos_draft_approvable(tmp_path, monkeypatch):
    """A drafted revision can be approved through the existing CLI flow."""
    from unittest import mock
    org, org_id = _setup_organism(tmp_path, monkeypatch)
    data = _valid_telos_json(org_id)
    draft_file = tmp_path / "draft.json"
    draft_file.write_text(json.dumps(data), encoding="utf-8")

    r = _telos_command("draft", org_root=org, file_path=str(draft_file))
    digest = r["digest"]
    assert r["status"] == "created"

    async def _prompt_y(_self, _msg):
        return "y"

    from hermes_cli.evolution.organism_home import get_organism_home
    monkeypatch.setattr(
        "hermes_cli.evolution.organism_home.get_organism_home", lambda: org,
    )

    with mock.patch("prompt_toolkit.shortcuts.PromptSession.prompt_async", _prompt_y):
        result = _handle_telos_approve_cli(digest, org_root=org)

    assert result["status"] == "approved", f"Expected approved, got: {result}"
    from hermes_cli.evolution.telos_store import TelosStore
    store = TelosStore(org)
    assert store.get_active_digest() == digest


# ── CLI E2E: approve yes activates A ──

def test_approve_yes_activates_pointer(tmp_path, monkeypatch):
    """Approve 'y' activates revision A and pointer is published."""
    org, org_id = _setup_organism(tmp_path, monkeypatch)
    store = TelosStore(org)

    t_a = _make_telos(org_id, "E2E Approve A")
    store.save_revision(t_a)
    digest_a = t_a.canonical_digest

    monkeypatch.setattr(
        "hermes_cli.evolution.organism_home.get_organism_home", lambda: org,
    )

    with mock.patch("prompt_toolkit.shortcuts.PromptSession.prompt_async", _prompt_y):
        result = _handle_telos_approve_cli(digest_a, org_root=org)

    assert result["status"] == "approved", f"Expected approved, got: {result}"
    assert result["request_id"] is not None

    assert store.get_active_digest() == digest_a


# ── CLI E2E: approve B updates LKG=A ──

def test_approve_b_updates_lkg(tmp_path, monkeypatch):
    """Amendment B: LKG=A before active=B."""
    org, org_id = _setup_organism(tmp_path, monkeypatch)
    store = TelosStore(org)

    t_a = _make_telos(org_id, "LKG-A")
    store.save_revision(t_a)
    digest_a = t_a.canonical_digest

    t_b = _make_telos(org_id, "LKG-B", parent=digest_a)
    store.save_revision(t_b)
    digest_b = t_b.canonical_digest

    from hermes_cli.evolution.organism_home import get_organism_home
    monkeypatch.setattr(
        "hermes_cli.evolution.organism_home.get_organism_home", lambda: org,
    )
    monkeypatch.setattr(
        "hermes_cli.evolution.organism_home.get_organism_home", lambda: org,
    )

    # Activate A
    with mock.patch("prompt_toolkit.shortcuts.PromptSession.prompt_async", _prompt_y):
        r1 = _handle_telos_approve_cli(digest_a, org_root=org)
    assert r1["status"] == "approved"
    assert store.get_active_digest() == digest_a

    # Activate B
    with mock.patch("prompt_toolkit.shortcuts.PromptSession.prompt_async", _prompt_y):
        r2 = _handle_telos_approve_cli(digest_b, org_root=org)
    assert r2["status"] == "approved"
    assert store.get_active_digest() == digest_b

    lkg = json.loads((org / "telos" / "last-known-good.json").read_text())
    assert lkg["digest"] == digest_a


# ── CLI E2E: rollback A restores A and preserves B ──

def test_rollback_restores_a_preserves_b(tmp_path, monkeypatch):
    """Rollback to A: active=A, B revision still exists."""
    org, org_id = _setup_organism(tmp_path, monkeypatch)
    store = TelosStore(org)

    t_a = _make_telos(org_id, "RB-A")
    store.save_revision(t_a)
    digest_a = t_a.canonical_digest

    t_b = _make_telos(org_id, "RB-B", parent=digest_a)
    store.save_revision(t_b)
    digest_b = t_b.canonical_digest

    from hermes_cli.evolution.organism_home import get_organism_home
    monkeypatch.setattr(
        "hermes_cli.evolution.organism_home.get_organism_home", lambda: org,
    )
    monkeypatch.setattr(
        "hermes_cli.evolution.organism_home.get_organism_home", lambda: org,
    )

    with mock.patch("prompt_toolkit.shortcuts.PromptSession.prompt_async", _prompt_y):
        _handle_telos_approve_cli(digest_a, org_root=org)
    with mock.patch("prompt_toolkit.shortcuts.PromptSession.prompt_async", _prompt_y):
        _handle_telos_approve_cli(digest_b, org_root=org)

    assert store.get_active_digest() == digest_b

    with mock.patch("prompt_toolkit.shortcuts.PromptSession.prompt_async", _prompt_y):
        r3 = _handle_telos_rollback_cli(digest_a, org_root=org)
    assert r3["status"] == "approved"
    assert store.get_active_digest() == digest_a

    assert store.get_revision(digest_b).canonical_digest == digest_b


# ── Prompt timeout/EOF/invalid denies, no pointer/grant/consumption ──

def test_prompt_timeout_denies_no_pointer(tmp_path, monkeypatch):
    """Timeout produces denial; no pointer, grant, or consumption created."""
    org, org_id = _setup_organism(tmp_path, monkeypatch)
    store = TelosStore(org)

    t_a = _make_telos(org_id, "Timeout Deny")
    store.save_revision(t_a)
    digest_a = t_a.canonical_digest

    from hermes_cli.evolution.organism_home import get_organism_home
    monkeypatch.setattr(
        "hermes_cli.evolution.organism_home.get_organism_home", lambda: org,
    )
    monkeypatch.setattr(
        "hermes_cli.evolution.organism_home.get_organism_home", lambda: org,
    )

    with mock.patch("prompt_toolkit.shortcuts.PromptSession.prompt_async", _prompt_timeout):
        result = _handle_telos_approve_cli(digest_a, org_root=org)

    assert result["status"] == "denied"
    assert store.get_active_digest() is None

    from hermes_cli.evolution.ledger import EvolutionLedger
    ledger = EvolutionLedger(org / "evolution" / "evolution.db")
    try:
        grants = ledger.connection.execute("SELECT COUNT(*) as c FROM telos_approval_grants").fetchone()
        assert grants["c"] == 0
        consumptions = ledger.connection.execute("SELECT COUNT(*) as c FROM telos_approval_consumptions").fetchone()
        assert consumptions["c"] == 0
    finally:
        ledger.connection.close()


def test_prompt_eof_denies_no_pointer(tmp_path, monkeypatch):
    """EOF produces denial; no pointer published."""
    org, org_id = _setup_organism(tmp_path, monkeypatch)
    store = TelosStore(org)

    t_a = _make_telos(org_id, "EOF Deny")
    store.save_revision(t_a)
    digest_a = t_a.canonical_digest

    from hermes_cli.evolution.organism_home import get_organism_home
    monkeypatch.setattr(
        "hermes_cli.evolution.organism_home.get_organism_home", lambda: org,
    )
    monkeypatch.setattr(
        "hermes_cli.evolution.organism_home.get_organism_home", lambda: org,
    )

    with mock.patch("prompt_toolkit.shortcuts.PromptSession.prompt_async", _prompt_eof):
        result = _handle_telos_approve_cli(digest_a, org_root=org)

    assert result["status"] == "denied"
    assert store.get_active_digest() is None


def test_prompt_invalid_input_denies_no_pointer(tmp_path, monkeypatch):
    """Invalid/non-'y' input produces denial; no pointer published."""
    org, org_id = _setup_organism(tmp_path, monkeypatch)
    store = TelosStore(org)

    t_a = _make_telos(org_id, "Invalid Deny")
    store.save_revision(t_a)
    digest_a = t_a.canonical_digest

    from hermes_cli.evolution.organism_home import get_organism_home
    monkeypatch.setattr(
        "hermes_cli.evolution.organism_home.get_organism_home", lambda: org,
    )
    monkeypatch.setattr(
        "hermes_cli.evolution.organism_home.get_organism_home", lambda: org,
    )

    with mock.patch("prompt_toolkit.shortcuts.PromptSession.prompt_async", _prompt_maybe):
        result = _handle_telos_approve_cli(digest_a, org_root=org)

    assert result["status"] == "denied"
    assert store.get_active_digest() is None


# ── Missing/tampered/cross-organism revision fails before decision ──

def test_missing_revision_fails_before_decision(tmp_path, monkeypatch):
    """Non-existent digest fails BEFORE any broker request or prompt."""
    org, org_id = _setup_organism(tmp_path, monkeypatch)

    from hermes_cli.evolution.organism_home import get_organism_home
    monkeypatch.setattr(
        "hermes_cli.evolution.organism_home.get_organism_home", lambda: org,
    )
    monkeypatch.setattr(
        "hermes_cli.evolution.organism_home.get_organism_home", lambda: org,
    )

    with mock.patch("prompt_toolkit.shortcuts.PromptSession.prompt_async") as mock_prompt:
        result = _handle_telos_approve_cli("f" * 64, org_root=org)

    mock_prompt.assert_not_called()
    assert result["status"] == "rejected"
    assert "revision not found" in result["message"]


def test_cross_organism_revision_fails_before_decision(tmp_path, monkeypatch):
    """Revision belonging to a different organism fails BEFORE prompt."""
    org, org_id = _setup_organism(tmp_path, monkeypatch)
    store = TelosStore(org)

    other_org_id = str(uuid.uuid4())
    t_a = _make_telos(other_org_id, "Cross - Other Org")
    store.save_revision(t_a)
    digest_a = t_a.canonical_digest

    from hermes_cli.evolution.organism_home import get_organism_home
    monkeypatch.setattr(
        "hermes_cli.evolution.organism_home.get_organism_home", lambda: org,
    )
    monkeypatch.setattr(
        "hermes_cli.evolution.organism_home.get_organism_home", lambda: org,
    )

    with mock.patch("prompt_toolkit.shortcuts.PromptSession.prompt_async") as mock_prompt:
        result = _handle_telos_approve_cli(digest_a, org_root=org)

    mock_prompt.assert_not_called()
    assert result["status"] == "rejected"
    assert "organism mismatch" in result["message"]


def test_invalid_digest_length_fails_before_decision(tmp_path, monkeypatch):
    """Invalid digest format fails immediately, no prompt shown."""
    org, org_id = _setup_organism(tmp_path, monkeypatch)

    from hermes_cli.evolution.organism_home import get_organism_home
    monkeypatch.setattr(
        "hermes_cli.evolution.organism_home.get_organism_home", lambda: org,
    )
    monkeypatch.setattr(
        "hermes_cli.evolution.organism_home.get_organism_home", lambda: org,
    )

    with mock.patch("prompt_toolkit.shortcuts.PromptSession.prompt_async") as mock_prompt:
        result = _handle_telos_approve_cli("bad", org_root=org)

    mock_prompt.assert_not_called()
    assert result["status"] == "invalid"


# ── Public/model-facing APIs remain fail-closed ──

def test_telos_store_activate_fail_closed(tmp_path, monkeypatch):
    """TelosStore.activate_revision raises host_approval_not_implemented."""
    monkeypatch.setattr("hermes_cli.evolution.ledger._open_file_descriptors", lambda: None)
    org_root = tmp_path / "organism"
    store = TelosStore(org_root)

    with pytest.raises(TelosStoreError, match="host_approval_not_implemented"):
        store.activate_revision("a" * 64)


def test_telos_store_rollback_fail_closed(tmp_path, monkeypatch):
    """TelosStore.rollback raises host_approval_not_implemented."""
    monkeypatch.setattr("hermes_cli.evolution.ledger._open_file_descriptors", lambda: None)
    org_root = tmp_path / "organism"
    store = TelosStore(org_root)

    with pytest.raises(TelosStoreError, match="host_approval_not_implemented"):
        store.rollback("a" * 64)


def test_direct_broker_grants_no_pointer(tmp_path, monkeypatch):
    """Creating coherent broker rows alone never publishes a pointer."""
    org, org_id = _setup_organism(tmp_path, monkeypatch)
    store = TelosStore(org)

    t_a = _make_telos(org_id, "Direct Broker")
    store.save_revision(t_a)
    digest = t_a.canonical_digest

    from hermes_cli.evolution.ledger import EvolutionLedger
    from hermes_cli.evolution.telos_approval import compute_context_digest

    ledger = EvolutionLedger(org / "evolution" / "evolution.db")
    try:
        broker = SqliteTelosApprovalBroker()
        ctx_create = HostApprovalContext(
            surface="classic_cli", actor_ref="actor", session_ref="s",
            request_id=None, telos_digest=digest, action="activate",
            nonce="n1", context_digest="ignored",
        )
        req_id = broker.create_request(ledger, org_id, digest, "activate", ctx_create, 3600)

        correct_digest = compute_context_digest("classic_cli", "actor", "s", req_id, "n1")
        ctx_decide = HostApprovalContext(
            surface="classic_cli", actor_ref="actor", session_ref="s",
            request_id=req_id, telos_digest=digest, action="activate",
            nonce="n1", context_digest=correct_digest,
        )
        dec_id = broker.record_host_decision(ledger, ctx_decide, "approved")
        grant_id = broker.issue_grant(ledger, req_id, dec_id)
        broker.consume_grant(ledger, grant_id, org_id, digest, "activate")
    finally:
        ledger.connection.close()

    assert store.get_active_digest() is None


def test_telos_command_unknown_subcommand_fail_closed():
    """_telos_command returns error for unknown subcommands (not approve/rollback)."""
    result = _telos_command("nonexistent")
    assert result["error"] == "unknown_subcommand"


# ── No receipt/clarify/capability registry reintroduced ──

def test_no_receipt_in_host_approval_context():
    """HostApprovalContext dataclass has no receipt field."""
    import inspect
    from dataclasses import fields
    field_names = {f.name for f in fields(HostApprovalContext)}
    assert "receipt" not in field_names
    assert "capability" not in field_names


def test_no_clarify_in_host_transition():
    """perform_telos_transition does not import or use clarify."""
    from hermes_cli.evolution.host_transition import perform_telos_transition
    import inspect
    source = inspect.getsource(perform_telos_transition)
    assert "clarify" not in source.lower()


def test_approve_command_from_args_dispatch(monkeypatch, capsys):
    """evolution_command with action=telos_approve returns nonzero for invalid/rejected."""
    from argparse import Namespace
    monkeypatch.setenv("HERMES_HOME", str(Path("/tmp/nonexistent")))

    args = Namespace(action="telos_approve", digest="", json=True, org_root=None)
    rc = evolution_command(args)
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["status"] in ("invalid", "rejected")


def test_rollback_command_from_args_dispatch(monkeypatch, capsys):
    """evolution_command with action=telos_rollback returns nonzero for invalid/rejected."""
    from argparse import Namespace
    monkeypatch.setenv("HERMES_HOME", str(Path("/tmp/nonexistent")))

    args = Namespace(action="telos_rollback", digest="", json=True, org_root=None)
    rc = evolution_command(args)
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["status"] in ("invalid", "rejected")


# ── Prompt API signature: real PromptSession.prompt has no timeout kwarg ──

def test_prompt_session_prompt_has_no_timeout_parameter():
    """PromptSession.prompt does NOT accept a timeout keyword."""
    import inspect
    from prompt_toolkit.shortcuts import PromptSession
    sig = inspect.signature(PromptSession.prompt)
    for name, param in sig.parameters.items():
        assert param.kind not in (param.VAR_KEYWORD,), (
            f"PromptSession.prompt should not have **kwargs but found {name}"
        )


def test_prompt_session_prompt_async_exists():
    """PromptSession.prompt_async is the documented async API."""
    from prompt_toolkit.shortcuts import PromptSession
    assert hasattr(PromptSession, "prompt_async")


# ── observer_scan with real initialized organism ──

def test_observer_scan_on_real_organism(tmp_path, monkeypatch):
    """observer_scan on a real initialized organism returns completed/degraded."""
    org, org_id = _setup_organism(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "hermes_cli.evolution.organism_home.get_organism_home", lambda: org,
    )

    # Enable autopoiesis so the config gate passes
    from hermes_cli.evolution.global_config import load_global_config, save_global_config
    cfg = load_global_config()
    cfg["autopoiesis"]["enabled"] = True
    save_global_config(cfg["autopoiesis"])

    from hermes_cli.evolution.command import _observer_scan
    result = _observer_scan(org)
    assert result["action"] == "observer_scan"
    assert result["status"] in ("completed", "degraded", "not_ready")
    assert isinstance(result["count"], int)


# ── host_transition caller-context verification ──

def test_transition_rejects_caller_action_mismatch(tmp_path, monkeypatch):
    """perform_telos_transition rejects if caller context action != persisted."""
    org, org_id = _setup_organism(tmp_path, monkeypatch)
    store = TelosStore(org)

    t_a = _make_telos(org_id, "Action Mismatch")
    store.save_revision(t_a)
    digest = t_a.canonical_digest

    from hermes_cli.evolution.ledger import EvolutionLedger
    from hermes_cli.evolution.host_transition import perform_telos_transition
    from hermes_cli.evolution.telos_approval import compute_context_digest

    ledger = EvolutionLedger(org / "evolution" / "evolution.db")
    try:
        broker = SqliteTelosApprovalBroker()
        ctx_create = HostApprovalContext(
            surface="classic_cli", actor_ref="actor", session_ref="s",
            request_id=None, telos_digest=digest, action="activate",
            nonce="n1", context_digest="ignored",
        )
        req_id = broker.create_request(ledger, org_id, digest, "activate", ctx_create, 3600)

        correct_digest = compute_context_digest("classic_cli", "actor", "s", req_id, "n1")
        ctx_mismatch = HostApprovalContext(
            surface="classic_cli", actor_ref="actor", session_ref="s",
            request_id=req_id, telos_digest=digest, action="rollback",  # wrong action!
            nonce="n1", context_digest=correct_digest,
        )

        result = perform_telos_transition(ledger, store, ctx_mismatch, "approved")
        assert result.status == "rejected"
        assert "caller action" in result.message.lower() or "action" in result.message.lower()
    finally:
        ledger.connection.close()


def test_transition_rejects_invalid_decision(tmp_path, monkeypatch):
    """perform_telos_transition rejects invalid decision string."""
    org, org_id = _setup_organism(tmp_path, monkeypatch)

    from hermes_cli.evolution.ledger import EvolutionLedger
    from hermes_cli.evolution.host_transition import perform_telos_transition

    ledger = EvolutionLedger(org / "evolution" / "evolution.db")
    try:
        ctx = HostApprovalContext(
            surface="x", actor_ref="x", session_ref="x",
            request_id="nonexistent", telos_digest="", action="",
            nonce="", context_digest="",
        )
        result = perform_telos_transition(ledger, None, ctx, "maybe")
        assert result.status == "rejected"
        assert "invalid decision" in result.message
    finally:
        ledger.connection.close()


def test_transition_rejects_approval_without_store(tmp_path, monkeypatch):
    """perform_telos_transition rejects approved decision when store is None."""
    org, org_id = _setup_organism(tmp_path, monkeypatch)
    store = TelosStore(org)

    t_a = _make_telos(org_id, "No Store")
    store.save_revision(t_a)
    digest = t_a.canonical_digest

    from hermes_cli.evolution.ledger import EvolutionLedger
    from hermes_cli.evolution.host_transition import perform_telos_transition
    from hermes_cli.evolution.telos_approval import compute_context_digest

    ledger = EvolutionLedger(org / "evolution" / "evolution.db")
    try:
        broker = SqliteTelosApprovalBroker()
        ctx_create = HostApprovalContext(
            surface="classic_cli", actor_ref="actor", session_ref="s",
            request_id=None, telos_digest=digest, action="activate",
            nonce="n1", context_digest="ignored",
        )
        req_id = broker.create_request(ledger, org_id, digest, "activate", ctx_create, 3600)
        correct_digest = compute_context_digest("classic_cli", "actor", "s", req_id, "n1")
        ctx = HostApprovalContext(
            surface="classic_cli", actor_ref="actor", session_ref="s",
            request_id=req_id, telos_digest=digest, action="activate",
            nonce="n1", context_digest=correct_digest,
        )
        result = perform_telos_transition(ledger, None, ctx, "approved")
        assert result.status == "rejected"
        assert "store" in result.message.lower()
    finally:
        ledger.connection.close()


# ── Digest validation: 64 nonhex / uppercase / unsupported action ──

def test_nonhex_64char_digest_rejected_before_ledger(tmp_path, monkeypatch):
    """64 nonhex chars rejected before any ledger/request mutation."""
    org, org_id = _setup_organism(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "hermes_cli.evolution.organism_home.get_organism_home", lambda: org,
    )

    with mock.patch("prompt_toolkit.shortcuts.PromptSession.prompt_async") as mock_prompt:
        result = _handle_telos_approve_cli("g" * 64, org_root=org)
    mock_prompt.assert_not_called()
    assert result["status"] == "invalid"
    assert result["request_id"] is None


def test_uppercase_hex_digest_rejected_before_ledger(tmp_path, monkeypatch):
    """Uppercase hex digest rejected — only lowercase [0-9a-f] accepted."""
    org, org_id = _setup_organism(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "hermes_cli.evolution.organism_home.get_organism_home", lambda: org,
    )

    with mock.patch("prompt_toolkit.shortcuts.PromptSession.prompt_async") as mock_prompt:
        result = _handle_telos_approve_cli("A" * 64, org_root=org)
    mock_prompt.assert_not_called()
    assert result["status"] == "invalid"
    assert result["request_id"] is None


def test_unsupported_action_rejected_before_ledger(tmp_path, monkeypatch):
    """Action other than activate/rollback rejected before any DB mutation."""
    org, org_id = _setup_organism(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "hermes_cli.evolution.organism_home.get_organism_home", lambda: org,
    )

    from hermes_cli.evolution.command import _handle_telos_cli_transition
    with mock.patch("prompt_toolkit.shortcuts.PromptSession.prompt_async") as mock_prompt:
        result = _handle_telos_cli_transition("a" * 64, "approve", org_root=org)
    mock_prompt.assert_not_called()
    assert result["status"] == "invalid"
    assert "unsupported" in result["message"]


# ── Persisted-row prompt field verification ──

def test_prompt_fields_equal_persisted_row(tmp_path, monkeypatch):
    """The real CLI handler builds its prompt from the persisted request row."""
    org, org_id = _setup_organism(tmp_path, monkeypatch)
    store = TelosStore(org)

    t_a = _make_telos(org_id, "Persisted Fields")
    store.save_revision(t_a)
    digest = t_a.canonical_digest

    from hermes_cli.evolution.ledger import EvolutionLedger
    from hermes_cli.evolution.telos_approval import (
        HostApprovalDecision,
    )

    captured = {}

    def deny(prompt, timeout):
        captured["prompt"] = prompt
        captured["timeout"] = timeout
        return HostApprovalDecision(
            request_id=prompt.request_id,
            decision="denied",
            host_surface="classic_cli",
            host_actor_ref="interactive",
            timestamp="2026-07-25T00:00:00.000000Z",
        )

    monkeypatch.setattr(
        "hermes_cli.evolution.telos_approval.telos_approval_prompt",
        deny,
    )
    result = _handle_telos_approve_cli(digest, org_root=org)
    assert result["status"] == "denied"
    assert captured["timeout"] == 120

    ledger = EvolutionLedger(org / "evolution" / "evolution.db")
    try:
        persisted = ledger.connection.execute(
            """SELECT request_id, organism_id, telos_digest, action,
                      display_nonce, bounded_summary,
                      expected_host_context_digest, expires_at
               FROM telos_approval_requests
               WHERE request_id LIKE ?""",
            (result["request_id"] + "%",),
        ).fetchone()
        prompt = captured["prompt"]
        assert persisted is not None
        assert prompt.request_id == persisted["request_id"]
        assert prompt.organism_id == persisted["organism_id"] == org_id
        assert prompt.telos_digest == persisted["telos_digest"] == digest
        assert prompt.action == persisted["action"] == "activate"
        assert prompt.display_nonce == persisted["display_nonce"]
        assert prompt.bounded_summary == persisted["bounded_summary"]
        assert prompt.host_context_digest == persisted["expected_host_context_digest"]
        assert prompt.expires_at == persisted["expires_at"]
    finally:
        ledger.connection.close()


def test_mismatched_prompt_row_cannot_transition(tmp_path, monkeypatch):
    """A prompt built from a mismatched row ID must not be used for transition."""
    org, org_id = _setup_organism(tmp_path, monkeypatch)
    store = TelosStore(org)

    t_a = _make_telos(org_id, "Mismatch Row")
    store.save_revision(t_a)
    digest = t_a.canonical_digest

    monkeypatch.setattr(
        "hermes_cli.evolution.organism_home.get_organism_home", lambda: org,
    )

    from hermes_cli.evolution.ledger import EvolutionLedger
    from hermes_cli.evolution.host_transition import perform_telos_transition
    from hermes_cli.evolution.telos_approval import (
        SqliteTelosApprovalBroker, HostApprovalContext, compute_context_digest,
    )

    broker = SqliteTelosApprovalBroker()
    ledger = EvolutionLedger(org / "evolution" / "evolution.db")
    try:
        ctx_create = HostApprovalContext(
            surface="classic_cli", actor_ref="actor", session_ref="s",
            request_id=None, telos_digest=digest, action="activate",
            nonce="p1", context_digest="ignored",
        )
        req_id = broker.create_request(ledger, org_id, digest, "activate", ctx_create, 3600)
        correct_digest = compute_context_digest("classic_cli", "actor", "s", req_id, "p1")

        # Use a context with a non-existent request_id
        ctx_bad = HostApprovalContext(
            surface="classic_cli", actor_ref="actor", session_ref="s",
            request_id="nonexistent-request", telos_digest=digest, action="activate",
            nonce="p1", context_digest=correct_digest,
        )
        result = perform_telos_transition(ledger, store, ctx_bad, "approved")
        assert result.status == "rejected"
        assert "not found" in result.message
    finally:
        ledger.connection.close()


# ── Event-loop guard ──

def test_telos_approval_prompt_denies_in_running_loop():
    """telos_approval_prompt returns denied when asyncio loop is already running."""
    import asyncio
    from hermes_cli.evolution.telos_approval import telos_approval_prompt, TelosApprovalPrompt

    prompt = TelosApprovalPrompt(
        request_id="req-1", organism_id="org-1", telos_digest="a" * 64,
        action="activate", display_nonce="1234",
        bounded_summary="Test", host_context_digest="c" * 64,
    )

    async def _run_in_loop():
        return telos_approval_prompt(prompt, timeout=1)

    decision = asyncio.run(_run_in_loop())
    assert decision.decision == "denied"
