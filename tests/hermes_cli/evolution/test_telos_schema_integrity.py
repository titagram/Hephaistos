"""Adversarial SQL tests: prove referential integrity constraints on telos approval tables.

These tests INSERT directly through SQLite to verify trigger enforcement.
"""

import hashlib
import pytest
import sqlite3
from pathlib import Path


def _create_v4_db(path: Path):
    """Create a fresh v4 database with all v4 schema statements including triggers."""
    from hermes_cli.evolution import ledger as _ledger
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys=ON")
    for stmt in _ledger._SCHEMA_V4_STATEMENTS:
        conn.execute(stmt)
    conn.execute("INSERT INTO schema_version VALUES (1, 4)")
    conn.commit()
    return conn


def _create_v5_db(path: Path):
    """Create a fresh v5 database with the literal v5 schema and triggers."""
    from hermes_cli.evolution import ledger as _ledger
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys=ON")
    for stmt in _ledger._SCHEMA_V5_STATEMENTS:
        conn.execute(stmt)
    conn.execute("INSERT INTO schema_version VALUES (1, 5)")
    conn.commit()
    return conn


def _insert_request(conn, req_id="r1", org="org1", digest="a"*64, action="activate", ctx="ctx-digest", expires="2099-01-01T00:00:00.000000Z"):
    conn.execute(
        "INSERT INTO telos_approval_requests VALUES (?,?,?,?,?,?,?,?,?)",
        (req_id, org, digest, action, ctx, "nonce", "summary", "2026-01-01T00:00:00.000000Z", expires),
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


# ── v5 invariants ──────────────────────────────────────────────────────────

def test_v5_grant_rejects_decision_for_different_request(tmp_path):
    conn = _create_v5_db(tmp_path / "ev.db")
    _insert_request(conn, req_id="req-A", org="org-A", ctx="ctx-A")
    _insert_request(conn, req_id="req-B", org="org-B", ctx="ctx-B")
    _insert_decision(conn, dec_id="dec-B", req_id="req-B", ctx="ctx-B")
    with pytest.raises(sqlite3.IntegrityError, match="telos_v5_decision_request_mismatch"):
        conn.execute(
            "INSERT INTO telos_approval_grants VALUES (?,?,?,?,?,?,?,?)",
            ("g1", "req-A", "dec-B", "org-A", "a"*64, "activate", "2026-01-01T00:00:00.000000Z", "2099-01-01T00:00:00.000000Z"),
        )
    conn.close()


def test_v5_decision_rejects_wrong_host_context(tmp_path):
    conn = _create_v5_db(tmp_path / "ev.db")
    _insert_request(conn, req_id="req-A", ctx="expected-ctx-A")
    with pytest.raises(sqlite3.IntegrityError, match="telos_v5_context_mismatch"):
        conn.execute(
            "INSERT INTO telos_approval_decisions VALUES (?,?,?,?,?,?,?)",
            ("dec-A", "req-A", "approved", "cli", "actor", "WRONG-CONTEXT", "2026-01-01T00:00:00.000000Z"),
        )
    conn.close()


def test_v5_duplicate_decision_rejected(tmp_path):
    conn = _create_v5_db(tmp_path / "ev.db")
    _insert_request(conn, req_id="req-A")
    _insert_decision(conn, dec_id="dec-A", req_id="req-A")
    with pytest.raises(sqlite3.IntegrityError):
        _insert_decision(conn, dec_id="dec-A2", req_id="req-A")
    conn.close()


def test_v5_expired_request_decide_rejected(tmp_path):
    conn = _create_v5_db(tmp_path / "ev.db")
    _insert_request(conn, req_id="req-A", expires="2020-01-01T00:00:00.000000Z")
    with pytest.raises(sqlite3.IntegrityError, match="telos_request_expired"):
        _insert_decision(conn, dec_id="dec-A", req_id="req-A", ts="2026-01-01T00:00:00.000000Z")
    conn.close()


def test_v5_expired_grant_consumption_rejected(tmp_path):
    conn = _create_v5_db(tmp_path / "ev.db")
    d = "a" * 64
    _insert_request(conn, req_id="req-A", digest=d)
    _insert_decision(conn, dec_id="dec-A", req_id="req-A")
    conn.execute(
        "INSERT INTO telos_approval_grants VALUES (?,?,?,?,?,?,?,?)",
        ("g1", "req-A", "dec-A", "org1", d, "activate", "2026-01-01T00:00:00.000000Z", "2020-01-01T00:00:00.000000Z"),
    )
    with pytest.raises(sqlite3.IntegrityError, match="telos_grant_expired"):
        conn.execute(
            "INSERT INTO telos_approval_consumptions VALUES (?,?,?,?,?,?)",
            ("c1", "g1", "org1", d, "activate", "2026-06-01T00:00:00.000000Z"),
        )
    conn.close()


# ── v5 coherent full chain (happy path) ──

def test_v5_happy_path_full_chain(tmp_path):
    conn = _create_v5_db(tmp_path / "ev.db")
    d = "b" * 64
    _insert_request(conn, req_id="req-A", digest=d)
    _insert_decision(conn, dec_id="dec-A", req_id="req-A")
    conn.execute(
        "INSERT INTO telos_approval_grants VALUES (?,?,?,?,?,?,?,?)",
        ("g1", "req-A", "dec-A", "org1", d, "activate", "2026-01-01T00:00:00.000000Z", "2099-01-01T00:00:00.000000Z"),
    )
    conn.execute(
        "INSERT INTO telos_approval_consumptions VALUES (?,?,?,?,?,?)",
        ("c1", "g1", "org1", d, "activate", "2026-06-01T00:00:00.000000Z"),
    )
    assert conn.execute("SELECT 1 FROM telos_approval_consumptions WHERE consumption_id = 'c1'").fetchone() is not None
    conn.close()


# ── v5 telos_valid_approval_chains view ─────────────────────────────────────

def test_v5_view_coherent_chain_appears_once(tmp_path):
    conn = _create_v5_db(tmp_path / "ev.db")
    conn.row_factory = sqlite3.Row
    d = "c" * 64
    _insert_request(conn, req_id="req-A", digest=d)
    _insert_decision(conn, dec_id="dec-A", req_id="req-A")
    conn.execute(
        "INSERT INTO telos_approval_grants VALUES (?,?,?,?,?,?,?,?)",
        ("g1", "req-A", "dec-A", "org1", d, "activate",
         "2026-01-01T00:00:00.000000Z", "2099-01-01T00:00:00.000000Z"),
    )
    conn.execute(
        "INSERT INTO telos_approval_consumptions VALUES (?,?,?,?,?,?)",
        ("c1", "g1", "org1", d, "activate", "2026-06-01T00:00:00.000000Z"),
    )
    rows = conn.execute("SELECT * FROM telos_valid_approval_chains").fetchall()
    assert len(rows) == 1, f"Expected 1 chain, got {len(rows)}"
    row = rows[0]
    assert row["request_id"] == "req-A"
    assert row["decision_id"] == "dec-A"
    assert row["grant_id"] == "g1"
    assert row["consumption_id"] == "c1"
    assert row["organism_id"] == "org1"
    assert row["telos_digest"] == d
    assert row["action"] == "activate"
    assert row["expected_host_context_digest"] == "ctx-digest"
    assert row["display_nonce"] == "nonce"
    assert row["host_surface"] == "cli"
    assert row["host_actor_ref"] == "actor"
    assert row["host_context_digest"] == "ctx-digest"
    assert row["consumed_at"] == "2026-06-01T00:00:00.000000Z"
    assert row["request_expires_at"] == "2099-01-01T00:00:00.000000Z"
    conn.close()


def test_v5_view_excludes_denied_decision_chain(tmp_path):
    conn = _create_v5_db(tmp_path / "ev.db")
    d = "d" * 64
    _insert_request(conn, req_id="req-A", digest=d)
    _insert_decision(conn, dec_id="dec-A", req_id="req-A", decision="denied")
    with pytest.raises(sqlite3.IntegrityError, match="telos_grant_requires_approved_decision"):
        conn.execute(
            "INSERT INTO telos_approval_grants VALUES (?,?,?,?,?,?,?,?)",
            ("g1", "req-A", "dec-A", "org1", d, "activate",
             "2026-01-01T00:00:00.000000Z", "2099-01-01T00:00:00.000000Z"),
        )
    assert conn.execute("SELECT COUNT(*) FROM telos_valid_approval_chains").fetchone()[0] == 0
    conn.close()


def test_v5_view_excludes_missing_decision(tmp_path):
    conn = _create_v5_db(tmp_path / "ev.db")
    _insert_request(conn, req_id="req-A")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO telos_approval_grants VALUES (?,?,?,?,?,?,?,?)",
            ("g1", "req-A", "no-such-decision", "org1", "a" * 64, "activate",
             "now", "2099"),
        )
    assert conn.execute("SELECT COUNT(*) FROM telos_valid_approval_chains").fetchone()[0] == 0
    conn.close()


def test_v5_view_excludes_expired_request(tmp_path):
    conn = _create_v5_db(tmp_path / "ev.db")
    _insert_request(conn, req_id="req-A", expires="2020-01-01T00:00:00.000000Z")
    with pytest.raises(sqlite3.IntegrityError, match="telos_request_expired"):
        _insert_decision(conn, dec_id="dec-A", req_id="req-A", ts="2026-01-01T00:00:00.000000Z")
    assert conn.execute("SELECT COUNT(*) FROM telos_valid_approval_chains").fetchone()[0] == 0
    conn.close()


def test_v5_view_excludes_expired_grant(tmp_path):
    conn = _create_v5_db(tmp_path / "ev.db")
    d = "e" * 64
    _insert_request(conn, req_id="req-A", digest=d)
    _insert_decision(conn, dec_id="dec-A", req_id="req-A")
    conn.execute(
        "INSERT INTO telos_approval_grants VALUES (?,?,?,?,?,?,?,?)",
        ("g1", "req-A", "dec-A", "org1", d, "activate",
         "2026-01-01T00:00:00.000000Z", "2020-01-01T00:00:00.000000Z"),
    )
    with pytest.raises(sqlite3.IntegrityError, match="telos_grant_expired"):
        conn.execute(
            "INSERT INTO telos_approval_consumptions VALUES (?,?,?,?,?,?)",
            ("c1", "g1", "org1", d, "activate", "2026-06-01T00:00:00.000000Z"),
        )
    assert conn.execute("SELECT COUNT(*) FROM telos_valid_approval_chains").fetchone()[0] == 0
    conn.close()


def test_v5_view_excludes_cross_wired_at_insert(tmp_path):
    conn = _create_v5_db(tmp_path / "ev.db")
    _insert_request(conn, req_id="req-A", org="org-A", ctx="ctx-A")
    _insert_request(conn, req_id="req-B", org="org-B", ctx="ctx-B")
    _insert_decision(conn, dec_id="dec-B", req_id="req-B", ctx="ctx-B")
    with pytest.raises(sqlite3.IntegrityError, match="telos_v5_decision_request_mismatch"):
        conn.execute(
            "INSERT INTO telos_approval_grants VALUES (?,?,?,?,?,?,?,?)",
            ("g1", "req-A", "dec-B", "org-A", "a" * 64, "activate",
             "2026-01-01T00:00:00.000000Z", "2099-01-01T00:00:00.000000Z"),
        )
    assert conn.execute("SELECT COUNT(*) FROM telos_valid_approval_chains").fetchone()[0] == 0
    conn.close()


def test_v5_view_excludes_wrong_context_at_insert(tmp_path):
    conn = _create_v5_db(tmp_path / "ev.db")
    _insert_request(conn, req_id="req-A", ctx="expected-ctx-A")
    with pytest.raises(sqlite3.IntegrityError, match="telos_v5_context_mismatch"):
        conn.execute(
            "INSERT INTO telos_approval_decisions VALUES (?,?,?,?,?,?,?)",
            ("dec-A", "req-A", "approved", "cli", "actor", "WRONG-CONTEXT",
             "2026-01-01T00:00:00.000000Z"),
        )
    assert conn.execute("SELECT COUNT(*) FROM telos_valid_approval_chains").fetchone()[0] == 0
    conn.close()


# ── v5 quarantine immutability ──────────────────────────────────────────────

def test_v5_quarantine_no_update(tmp_path):
    conn = _create_v5_db(tmp_path / "ev.db")
    conn.execute(
        "INSERT INTO telos_approval_quarantine_v4(quarantined_at, reason, table_name, row_id) VALUES (?,?,?,?)",
        ("now", "test", "t1", "r1"),
    )
    with pytest.raises(sqlite3.IntegrityError, match="telos_quarantine_immutable"):
        conn.execute("UPDATE telos_approval_quarantine_v4 SET reason = 'hacked' WHERE id = 1")
    conn.close()


def test_v5_quarantine_no_delete(tmp_path):
    conn = _create_v5_db(tmp_path / "ev.db")
    conn.execute(
        "INSERT INTO telos_approval_quarantine_v4(quarantined_at, reason, table_name, row_id) VALUES (?,?,?,?)",
        ("now", "test", "t1", "r1"),
    )
    with pytest.raises(sqlite3.IntegrityError, match="telos_quarantine_immutable"):
        conn.execute("DELETE FROM telos_approval_quarantine_v4 WHERE id = 1")
    conn.close()


def test_v5_quarantine_unique_enforced(tmp_path):
    conn = _create_v5_db(tmp_path / "ev.db")
    conn.execute(
        "INSERT INTO telos_approval_quarantine_v4(quarantined_at, reason, table_name, row_id) VALUES (?,?,?,?)",
        ("now", "cross_wired", "telos_approval_grants", "g1"),
    )
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        conn.execute(
            "INSERT INTO telos_approval_quarantine_v4(quarantined_at, reason, table_name, row_id) VALUES (?,?,?,?)",
            ("later", "cross_wired", "telos_approval_grants", "g1"),
        )
    # Different reason is a separate unique tuple
    conn.execute(
        "INSERT INTO telos_approval_quarantine_v4(quarantined_at, reason, table_name, row_id) VALUES (?,?,?,?)",
        ("now", "wrong_host_context", "telos_approval_grants", "g1"),
    )
    assert conn.execute("SELECT COUNT(*) FROM telos_approval_quarantine_v4").fetchone()[0] == 2
    conn.close()
