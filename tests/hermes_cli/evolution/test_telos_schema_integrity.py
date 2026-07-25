"""Adversarial SQL tests: prove referential integrity constraints on telos approval tables.

These tests INSERT directly through SQLite to verify trigger enforcement.
"""

import hashlib
import pytest
import sqlite3
from pathlib import Path


def _create_v4_db(path: Path):
    """Create a fresh v4 database with all schema statements including triggers."""
    from hermes_cli.evolution import ledger as _ledger
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys=ON")
    for stmt in _ledger._SCHEMA_STATEMENTS:
        conn.execute(stmt)
    conn.execute("INSERT INTO schema_version VALUES (1, 4)")
    conn.commit()
    return conn


def _insert_request(conn, req_id="r1", org="org1", digest="a"*64, action="activate", expires="2099-01-01T00:00:00.000000Z"):
    conn.execute(
        "INSERT INTO telos_approval_requests VALUES (?,?,?,?,?,?,?,?,?)",
        (req_id, org, digest, action, "ctx-digest", "nonce", "summary", "2026-01-01T00:00:00.000000Z", expires),
    )


def _insert_decision(conn, dec_id="d1", req_id="r1", decision="approved", surface="cli", actor="actor", ctx="ctx-digest", ts="2026-01-01T00:00:00.000000Z"):
    conn.execute(
        "INSERT INTO telos_approval_decisions VALUES (?,?,?,?,?,?,?)",
        (dec_id, req_id, decision, surface, actor, ctx, ts),
    )


# ── Trigger: grant must match request organism/digest/action ──

def test_grant_organism_mismatch_rejected(tmp_path):
    conn = _create_v4_db(tmp_path / "ev.db")
    _insert_request(conn, org="org-A", digest="a"*64, action="activate")
    _insert_decision(conn)

    with pytest.raises(sqlite3.IntegrityError, match="telos_grant_organism_mismatch"):
        conn.execute(
            "INSERT INTO telos_approval_grants VALUES (?,?,?,?,?,?,?,?)",
            ("g1", "r1", "d1", "org-WRONG", "a"*64, "activate", "now", "2099"),
        )
    conn.close()


def test_grant_digest_mismatch_rejected(tmp_path):
    conn = _create_v4_db(tmp_path / "ev.db")
    _insert_request(conn, digest="a"*64)
    _insert_decision(conn)

    with pytest.raises(sqlite3.IntegrityError, match="telos_grant_digest_mismatch"):
        conn.execute(
            "INSERT INTO telos_approval_grants VALUES (?,?,?,?,?,?,?,?)",
            ("g1", "r1", "d1", "org1", "b"*64, "activate", "now", "2099"),
        )
    conn.close()


def test_grant_action_mismatch_rejected(tmp_path):
    conn = _create_v4_db(tmp_path / "ev.db")
    _insert_request(conn, action="activate")
    _insert_decision(conn)

    with pytest.raises(sqlite3.IntegrityError, match="telos_grant_action_mismatch"):
        conn.execute(
            "INSERT INTO telos_approval_grants VALUES (?,?,?,?,?,?,?,?)",
            ("g1", "r1", "d1", "org1", "a"*64, "rollback", "now", "2099"),
        )
    conn.close()


# ── Trigger: denied decision cannot produce grant ──

def test_denied_decision_cannot_produce_grant(tmp_path):
    conn = _create_v4_db(tmp_path / "ev.db")
    _insert_request(conn)
    _insert_decision(conn, decision="denied")

    with pytest.raises(sqlite3.IntegrityError, match="telos_grant_requires_approved_decision"):
        conn.execute(
            "INSERT INTO telos_approval_grants VALUES (?,?,?,?,?,?,?,?)",
            ("g1", "r1", "d1", "org1", "a"*64, "activate", "now", "2099"),
        )
    conn.close()


# ── Trigger: consumption must match grant ──

def test_consumption_mismatch_rejected(tmp_path):
    conn = _create_v4_db(tmp_path / "ev.db")
    _insert_request(conn, digest="a"*64, action="activate")
    _insert_decision(conn)
    conn.execute(
        "INSERT INTO telos_approval_grants VALUES (?,?,?,?,?,?,?,?)",
        ("g1", "r1", "d1", "org1", "a"*64, "activate", "2026-01-01T00:00:00.000000Z", "2099-01-01T00:00:00.000000Z"),
    )

    with pytest.raises(sqlite3.IntegrityError, match="telos_consumption_organism_mismatch"):
        conn.execute(
            "INSERT INTO telos_approval_consumptions VALUES (?,?,?,?,?,?)",
            ("c1", "g1", "wrong-org", "a"*64, "activate", "2026-01-01T00:00:00.000000Z"),
        )
    conn.close()


# ── FK: missing decision cannot produce grant ──

def test_missing_decision_grant_fails(tmp_path):
    conn = _create_v4_db(tmp_path / "ev.db")
    _insert_request(conn)
    # No decision inserted

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO telos_approval_grants VALUES (?,?,?,?,?,?,?,?)",
            ("g1", "r1", "no-such-decision", "org1", "a"*64, "activate", "now", "2099"),
        )
    conn.close()


# ── FK: duplicate grant rejected ──

def test_duplicate_request_grant_fails(tmp_path):
    conn = _create_v4_db(tmp_path / "ev.db")
    _insert_request(conn)
    _insert_decision(conn)
    conn.execute(
        "INSERT INTO telos_approval_grants VALUES (?,?,?,?,?,?,?,?)",
        ("g1", "r1", "d1", "org1", "a"*64, "activate", "2026-01-01T00:00:00.000000Z", "2099-01-01T00:00:00.000000Z"),
    )

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO telos_approval_grants VALUES (?,?,?,?,?,?,?,?)",
            ("g2", "r1", "d1", "org1", "a"*64, "activate", "now2", "2099"),
        )
    conn.close()


# ── UPDATE and DELETE forbidden ──

def test_update_telos_table_rejected(tmp_path):
    conn = _create_v4_db(tmp_path / "ev.db")
    _insert_request(conn)

    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("UPDATE telos_approval_requests SET organism_id = 'hacked' WHERE request_id = 'r1'")
    conn.close()


def test_delete_telos_table_rejected(tmp_path):
    conn = _create_v4_db(tmp_path / "ev.db")
    _insert_request(conn)

    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("DELETE FROM telos_approval_requests WHERE request_id = 'r1'")
    conn.close()
