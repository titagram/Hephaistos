"""Focused SQLite persistence for local OrgRuns and deterministic reports."""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3
import time

from hermes_cli.kanban_db import write_txn
from tools.delegation_routing import ALLOWED_ROLES


ORG_RUN_STATES = frozenset({
    "draft",
    "validated",
    "materialized",
    "running",
    "integrating",
    "reviewing",
    "completed",
    "blocked",
    "cancelled",
})


@dataclass(frozen=True)
class OrgRunRecord:
    run_id: str
    board_slug: str
    plan_version: int
    plan_hash: str
    base_commit: str
    origin: str
    state: str
    anchor_task_id: str
    created_at: int
    updated_at: int


@dataclass(frozen=True)
class OrgNodeRecord:
    run_id: str
    node_id: str
    task_id: str
    node_kind: str
    plan_version: int
    contract_hash: str
    logical_role: str
    state: str


@dataclass(frozen=True)
class KanbanReportRecord:
    id: int
    board_slug: str
    report_type: str
    subject_id: str
    terminal_run_id: int | None
    source_version: int
    report_json: str
    report_markdown: str
    generated_at: int
    idempotency_key: str


def _timestamp(value: int | None) -> int:
    return int(time.time()) if value is None else int(value)


def _validate_org_run_state(state: str) -> str:
    value = str(state)
    if value not in ORG_RUN_STATES:
        raise ValueError(f"invalid OrgRun state: {value}")
    return value


def _validate_logical_role(role: str) -> str:
    value = str(role)
    if value not in ALLOWED_ROLES:
        raise ValueError(f"unsupported OrgRun logical role: {value}")
    return value


def _org_run_from_row(row: sqlite3.Row) -> OrgRunRecord:
    return OrgRunRecord(
        run_id=str(row["run_id"]),
        board_slug=str(row["board_slug"]),
        plan_version=int(row["plan_version"]),
        plan_hash=str(row["plan_hash"]),
        base_commit=str(row["base_commit"]),
        origin=str(row["origin"]),
        state=str(row["state"]),
        anchor_task_id=str(row["anchor_task_id"]),
        created_at=int(row["created_at"]),
        updated_at=int(row["updated_at"]),
    )


def _org_node_from_row(row: sqlite3.Row) -> OrgNodeRecord:
    return OrgNodeRecord(
        run_id=str(row["run_id"]),
        node_id=str(row["node_id"]),
        task_id=str(row["task_id"]),
        node_kind=str(row["node_kind"]),
        plan_version=int(row["plan_version"]),
        contract_hash=str(row["contract_hash"]),
        logical_role=str(row["logical_role"]),
        state=str(row["state"]),
    )


def _report_from_row(row: sqlite3.Row) -> KanbanReportRecord:
    terminal_run_id = row["terminal_run_id"]
    return KanbanReportRecord(
        id=int(row["id"]),
        board_slug=str(row["board_slug"]),
        report_type=str(row["report_type"]),
        subject_id=str(row["subject_id"]),
        terminal_run_id=int(terminal_run_id) if terminal_run_id is not None else None,
        source_version=int(row["source_version"]),
        report_json=str(row["report_json"]),
        report_markdown=str(row["report_markdown"]),
        generated_at=int(row["generated_at"]),
        idempotency_key=str(row["idempotency_key"]),
    )


def insert_org_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    board_slug: str,
    plan_version: int,
    plan_hash: str,
    base_commit: str,
    origin: str,
    state: str,
    anchor_task_id: str,
    now: int | None = None,
) -> OrgRunRecord:
    """Insert one OrgRun, participating in an existing transaction when present."""
    timestamp = _timestamp(now)
    checked_state = _validate_org_run_state(state)
    with write_txn(conn):
        conn.execute(
            "INSERT INTO kanban_org_runs "
            "(run_id, board_slug, plan_version, plan_hash, base_commit, origin, "
            "state, anchor_task_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                board_slug,
                int(plan_version),
                plan_hash,
                base_commit,
                origin,
                checked_state,
                anchor_task_id,
                timestamp,
                timestamp,
            ),
        )
        row = conn.execute(
            "SELECT * FROM kanban_org_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
    assert row is not None
    return _org_run_from_row(row)


def get_org_run(conn: sqlite3.Connection, run_id: str) -> OrgRunRecord | None:
    row = conn.execute(
        "SELECT * FROM kanban_org_runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    return _org_run_from_row(row) if row is not None else None


def set_org_run_state(
    conn: sqlite3.Connection,
    run_id: str,
    state: str,
    *,
    now: int | None = None,
) -> None:
    checked_state = _validate_org_run_state(state)
    with write_txn(conn):
        updated = conn.execute(
            "UPDATE kanban_org_runs SET state = ?, updated_at = ? WHERE run_id = ?",
            (checked_state, _timestamp(now), run_id),
        )
        if updated.rowcount != 1:
            raise KeyError(f"unknown OrgRun: {run_id}")


def update_org_run_plan(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    plan_version: int,
    plan_hash: str,
    now: int | None = None,
) -> None:
    """Advance the current immutable plan pointer inside the caller's transaction."""
    with write_txn(conn):
        updated = conn.execute(
            "UPDATE kanban_org_runs "
            "SET plan_version = ?, plan_hash = ?, updated_at = ? "
            "WHERE run_id = ?",
            (int(plan_version), plan_hash, _timestamp(now), run_id),
        )
        if updated.rowcount != 1:
            raise KeyError(f"unknown OrgRun: {run_id}")


def set_org_nodes_state(
    conn: sqlite3.Connection,
    run_id: str,
    node_ids: tuple[str, ...],
    state: str,
) -> None:
    """Update durable lifecycle state for an exact set of owned nodes."""
    if not node_ids:
        return
    placeholders = ",".join("?" for _ in node_ids)
    with write_txn(conn):
        updated = conn.execute(
            f"UPDATE kanban_org_nodes SET state = ? "
            f"WHERE run_id = ? AND node_id IN ({placeholders})",
            (state, run_id, *node_ids),
        )
        if updated.rowcount != len(node_ids):
            raise ValueError(f"OrgRun {run_id} has incomplete stored topology")


def refresh_org_run_state(conn: sqlite3.Connection, run_id: str) -> str:
    """Derive and persist OrgRun state solely from durable Kanban rows."""
    with write_txn(conn):
        run = get_org_run(conn, run_id)
        if run is None:
            raise KeyError(f"unknown OrgRun: {run_id}")
        if run.state == "cancelled":
            return "cancelled"

        rows = conn.execute(
            "SELECT n.node_kind, t.status "
            "FROM kanban_org_nodes AS n "
            "JOIN tasks AS t ON t.id = n.task_id "
            "WHERE n.run_id = ? AND n.state = 'active'",
            (run_id,),
        ).fetchall()
        by_kind: dict[str, list[str]] = {}
        for row in rows:
            by_kind.setdefault(str(row["node_kind"]), []).append(
                str(row["status"])
            )

        required_statuses = [
            status
            for kind, statuses in by_kind.items()
            if kind != "anchor"
            for status in statuses
        ]
        finalization_done = "done" in by_kind.get("finalization", ())
        final_report_exists = conn.execute(
            "SELECT 1 FROM kanban_reports "
            "WHERE board_slug = ? AND subject_id = ? "
            "AND source_version = ? "
            "AND report_type IN ('org_run', 'org_run_final') LIMIT 1",
            (run.board_slug, run_id, run.plan_version),
        ).fetchone() is not None
        global_review_started = any(
            status in {"running", "done"}
            for status in by_kind.get("global_review", ())
        )
        integration_started = any(
            status in {"running", "done"}
            for status in by_kind.get("integration", ())
        )
        execution_started = any(
            status in {"running", "done"}
            for status in by_kind.get("execution", ())
        )

        if "blocked" in required_statuses:
            state = "blocked"
        elif finalization_done and final_report_exists:
            state = "completed"
        elif global_review_started:
            state = "reviewing"
        elif integration_started:
            state = "integrating"
        elif execution_started:
            state = "running"
        else:
            state = "materialized"
        set_org_run_state(conn, run_id, state)
        return state


def insert_plan_version(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    plan_version: int,
    plan_hash: str,
    plan_json: str,
    reason: str | None = None,
    now: int | None = None,
) -> None:
    """Persist one immutable canonical plan version."""
    with write_txn(conn):
        conn.execute(
            "INSERT INTO kanban_org_plan_versions "
            "(run_id, plan_version, plan_hash, plan_json, reason, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                run_id,
                int(plan_version),
                plan_hash,
                plan_json,
                reason,
                _timestamp(now),
            ),
        )


def insert_org_node(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    node_id: str,
    task_id: str,
    node_kind: str,
    plan_version: int,
    contract_hash: str,
    logical_role: str,
    state: str = "active",
) -> OrgNodeRecord:
    """Persist one immutable node identity for an OrgRun topology."""
    checked_role = _validate_logical_role(logical_role)
    with write_txn(conn):
        conn.execute(
            "INSERT INTO kanban_org_nodes "
            "(run_id, node_id, task_id, node_kind, plan_version, contract_hash, "
            "logical_role, state) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                node_id,
                task_id,
                node_kind,
                int(plan_version),
                contract_hash,
                checked_role,
                state,
            ),
        )
        row = conn.execute(
            "SELECT * FROM kanban_org_nodes WHERE run_id = ? AND node_id = ?",
            (run_id, node_id),
        ).fetchone()
    assert row is not None
    return _org_node_from_row(row)


def list_org_nodes(
    conn: sqlite3.Connection,
    run_id: str,
) -> list[OrgNodeRecord]:
    rows = conn.execute(
        "SELECT * FROM kanban_org_nodes WHERE run_id = ? ORDER BY node_id",
        (run_id,),
    ).fetchall()
    return [_org_node_from_row(row) for row in rows]


def insert_report(
    conn: sqlite3.Connection,
    *,
    board_slug: str,
    report_type: str,
    subject_id: str,
    terminal_run_id: int | None,
    source_version: int,
    report_json: str,
    report_markdown: str,
    generated_at: int,
    idempotency_key: str,
) -> KanbanReportRecord:
    """Insert a report once, returning an identical replay by idempotency key."""
    values = (
        board_slug,
        report_type,
        subject_id,
        int(terminal_run_id) if terminal_run_id is not None else None,
        int(source_version),
        report_json,
        report_markdown,
        int(generated_at),
        idempotency_key,
    )
    with write_txn(conn):
        existing = conn.execute(
            "SELECT * FROM kanban_reports WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        if existing is not None:
            record = _report_from_row(existing)
            if (
                record.board_slug,
                record.report_type,
                record.subject_id,
                record.terminal_run_id,
                record.source_version,
                record.report_json,
                record.report_markdown,
                record.generated_at,
                record.idempotency_key,
            ) != values:
                raise ValueError(
                    "report idempotency key is already bound to different content"
                )
            return record
        cursor = conn.execute(
            "INSERT INTO kanban_reports "
            "(board_slug, report_type, subject_id, terminal_run_id, source_version, "
            "report_json, report_markdown, generated_at, idempotency_key) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            values,
        )
        row = conn.execute(
            "SELECT * FROM kanban_reports WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
    assert row is not None
    return _report_from_row(row)
