"""Tests for session autopoiesis pinning — model_config._autopoiesis_pin helpers."""

import json
import pytest
from pathlib import Path


def _make_test_session_db(tmp_path: Path):
    """Helper: create a minimal SessionDB in tmp_path."""
    import hermes_constants
    from hermes_state import SessionDB

    home = tmp_path / ".hermes"
    home.mkdir(parents=True)
    db_path = home / "state.db"
    db = SessionDB(db_path=db_path)
    db._init_schema()
    return db


def _create_session(db, sid: str, model_config: dict | None = None):
    """Insert a session row with optional model_config."""
    cfg_json = json.dumps(model_config) if model_config else None

    def _insert(conn):
        conn.execute(
            "INSERT INTO sessions (id, title, source, model_config, started_at) VALUES (?,?,?,?,?)",
            (sid, sid, "cli", cfg_json, "2026-01-01T00:00:00.000000Z"),
        )

    db._execute_write(_insert)


def test_set_and_get_pin_preserves_existing_keys(tmp_path):
    from hermes_cli.evolution.session_pinning import (
        get_session_autopoiesis_pin,
        set_session_autopoiesis_pin_if_absent,
    )
    db = _make_test_session_db(tmp_path)
    sid = "session-1"

    _create_session(db, sid, {"_delegate_from": "parent-1", "_branched_from": "branch-1"})

    pin = {"organism_id": "org1", "telos_digest": "a" * 64, "generation_id": "gen1"}
    assert set_session_autopoiesis_pin_if_absent(db, sid, pin) is True

    loaded = get_session_autopoiesis_pin(db, sid)
    assert loaded["organism_id"] == "org1"
    assert loaded["telos_digest"] == "a" * 64

    # Existing keys preserved
    row = db._conn.execute(
        "SELECT model_config FROM sessions WHERE id = ?", (sid,)
    ).fetchone()
    cfg = json.loads(row["model_config"])
    assert cfg["_delegate_from"] == "parent-1"
    assert cfg["_branched_from"] == "branch-1"

    # Second set is no-op
    assert set_session_autopoiesis_pin_if_absent(db, sid, {"different": True}) is False
    loaded2 = get_session_autopoiesis_pin(db, sid)
    assert loaded2["organism_id"] == "org1"


def test_inherit_pin_copies_to_child(tmp_path):
    from hermes_cli.evolution.session_pinning import (
        set_session_autopoiesis_pin_if_absent,
        inherit_session_autopoiesis_pin,
        get_session_autopoiesis_pin,
    )
    db = _make_test_session_db(tmp_path)
    _create_session(db, "parent")
    _create_session(db, "child")

    pin = {"organism_id": "org1"}
    assert set_session_autopoiesis_pin_if_absent(db, "parent", pin) is True
    assert inherit_session_autopoiesis_pin(db, "parent", "child") is True
    assert get_session_autopoiesis_pin(db, "child") == pin


def test_get_pin_returns_none_for_missing_session(tmp_path):
    from hermes_cli.evolution.session_pinning import get_session_autopoiesis_pin
    db = _make_test_session_db(tmp_path)
    assert get_session_autopoiesis_pin(db, "nonexistent") is None


def test_set_pin_if_absent_only_creates_once(tmp_path):
    from hermes_cli.evolution.session_pinning import (
        set_session_autopoiesis_pin_if_absent,
        get_session_autopoiesis_pin,
    )
    db = _make_test_session_db(tmp_path)
    _create_session(db, "s1")

    assert set_session_autopoiesis_pin_if_absent(db, "s1", {"v": 1}) is True
    assert set_session_autopoiesis_pin_if_absent(db, "s1", {"v": 2}) is False
    assert get_session_autopoiesis_pin(db, "s1") == {"v": 1}


def test_inherit_returns_false_when_parent_has_no_pin(tmp_path):
    from hermes_cli.evolution.session_pinning import inherit_session_autopoiesis_pin
    db = _make_test_session_db(tmp_path)
    _create_session(db, "parent", {"other_key": "val"})
    _create_session(db, "child")
    assert inherit_session_autopoiesis_pin(db, "parent", "child") is False


def test_organism_session_pin_to_dict():
    from hermes_cli.evolution.session_pinning import OrganismSessionPin

    pin = OrganismSessionPin(
        organism_id="org1",
        active_telos_digest="a" * 64,
        active_generation_id="gen1",
    )
    d = pin.to_dict()
    assert d["organism_id"] == "org1"
    assert d["active_telos_digest"] == "a" * 64
    assert d["active_generation_id"] == "gen1"
    assert "gnothi_seauton_revision_digest" not in d
