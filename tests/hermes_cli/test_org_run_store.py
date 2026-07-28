from __future__ import annotations

import sqlite3

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli.org_run_store import (
    KanbanReportRecord,
    OrgNodeRecord,
    OrgRunRecord,
    get_org_run,
    insert_org_node,
    insert_org_run,
    insert_plan_version,
    insert_report,
    list_org_nodes,
    set_org_run_state,
)


def _insert_run(conn: sqlite3.Connection, *, run_id: str = "run-1") -> OrgRunRecord:
    return insert_org_run(
        conn,
        run_id=run_id,
        board_slug="default",
        plan_version=1,
        plan_hash="plan-hash-1",
        base_commit="abc123",
        origin="local",
        state="validated",
        anchor_task_id=f"{run_id}-anchor",
        now=100,
    )


def test_store_round_trips_run_and_nodes_in_stable_order(tmp_path):
    with kb.connect(tmp_path / "kanban.db") as conn:
        inserted = _insert_run(conn)
        insert_org_node(
            conn,
            run_id="run-1",
            node_id="node-b",
            task_id="task-b",
            node_kind="execution",
            plan_version=1,
            contract_hash="contract-b",
            logical_role="leaf",
        )
        insert_org_node(
            conn,
            run_id="run-1",
            node_id="node-a",
            task_id="task-a",
            node_kind="anchor",
            plan_version=1,
            contract_hash="contract-a",
            logical_role="orchestrator",
        )

        assert inserted == OrgRunRecord(
            run_id="run-1",
            board_slug="default",
            plan_version=1,
            plan_hash="plan-hash-1",
            base_commit="abc123",
            origin="local",
            state="validated",
            anchor_task_id="run-1-anchor",
            created_at=100,
            updated_at=100,
        )
        assert get_org_run(conn, "run-1") == inserted
        assert get_org_run(conn, "missing") is None
        assert list_org_nodes(conn, "run-1") == [
            OrgNodeRecord(
                run_id="run-1",
                node_id="node-a",
                task_id="task-a",
                node_kind="anchor",
                plan_version=1,
                contract_hash="contract-a",
                logical_role="orchestrator",
                state="active",
            ),
            OrgNodeRecord(
                run_id="run-1",
                node_id="node-b",
                task_id="task-b",
                node_kind="execution",
                plan_version=1,
                contract_hash="contract-b",
                logical_role="leaf",
                state="active",
            ),
        ]


def test_store_enforces_run_node_and_plan_version_uniqueness(tmp_path):
    with kb.connect(tmp_path / "kanban.db") as conn:
        _insert_run(conn)
        insert_plan_version(
            conn,
            run_id="run-1",
            plan_version=1,
            plan_hash="plan-hash-1",
            plan_json='{"run_id":"run-1"}',
            now=101,
        )
        insert_org_node(
            conn,
            run_id="run-1",
            node_id="execute",
            task_id="task-1",
            node_kind="execution",
            plan_version=1,
            contract_hash="contract-1",
            logical_role="leaf",
        )

        with pytest.raises(sqlite3.IntegrityError):
            insert_org_run(
                conn,
                run_id="run-1",
                board_slug="other",
                plan_version=1,
                plan_hash="other-hash",
                base_commit="def456",
                origin="local",
                state="draft",
                anchor_task_id="other-anchor",
                now=102,
            )
        with pytest.raises(sqlite3.IntegrityError):
            insert_plan_version(
                conn,
                run_id="run-1",
                plan_version=1,
                plan_hash="other-hash",
                plan_json='{"run_id":"changed"}',
                now=102,
            )
        with pytest.raises(sqlite3.IntegrityError):
            insert_org_node(
                conn,
                run_id="run-1",
                node_id="execute",
                task_id="task-2",
                node_kind="execution",
                plan_version=1,
                contract_hash="contract-2",
                logical_role="leaf",
            )


def test_insert_org_node_rejects_default_role_without_persisting(tmp_path):
    with kb.connect(tmp_path / "kanban.db") as conn:
        with pytest.raises(ValueError, match="unsupported OrgRun logical role: default"):
            insert_org_node(
                conn,
                run_id="run-1",
                node_id="execute",
                task_id="task-1",
                node_kind="execution",
                plan_version=1,
                contract_hash="contract-1",
                logical_role="default",
            )

        assert conn.execute(
            "SELECT COUNT(*) AS n FROM kanban_org_nodes"
        ).fetchone()["n"] == 0


def test_state_validation_and_updates(tmp_path):
    with kb.connect(tmp_path / "kanban.db") as conn:
        _insert_run(conn)

        set_org_run_state(conn, "run-1", "running", now=200)
        assert get_org_run(conn, "run-1").state == "running"
        assert get_org_run(conn, "run-1").updated_at == 200

        with pytest.raises(ValueError, match="invalid OrgRun state"):
            set_org_run_state(conn, "run-1", "finished")
        with pytest.raises(ValueError, match="invalid OrgRun state"):
            insert_org_run(
                conn,
                run_id="run-2",
                board_slug="default",
                plan_version=1,
                plan_hash="hash",
                base_commit="abc123",
                origin="local",
                state="finished",
                anchor_task_id="run-2-anchor",
            )


def test_store_participates_in_an_existing_transaction(tmp_path):
    with kb.connect(tmp_path / "kanban.db") as conn:
        conn.execute("BEGIN IMMEDIATE")
        _insert_run(conn)
        assert conn.in_transaction is True
        conn.execute("ROLLBACK")

        assert get_org_run(conn, "run-1") is None


def test_report_insertion_is_idempotent_by_key(tmp_path):
    with kb.connect(tmp_path / "kanban.db") as conn:
        first = insert_report(
            conn,
            board_slug="default",
            report_type="task",
            subject_id="task-1",
            terminal_run_id=7,
            source_version=1,
            report_json='{"schema":"hades.kanban-task-report.v1"}',
            report_markdown="# Task report",
            generated_at=300,
            idempotency_key="task:task-1:run:7",
        )
        replay = insert_report(
            conn,
            board_slug="default",
            report_type="task",
            subject_id="task-1",
            terminal_run_id=7,
            source_version=1,
            report_json='{"schema":"hades.kanban-task-report.v1"}',
            report_markdown="# Task report",
            generated_at=300,
            idempotency_key="task:task-1:run:7",
        )

        assert first == replay
        assert first == KanbanReportRecord(
            id=first.id,
            board_slug="default",
            report_type="task",
            subject_id="task-1",
            terminal_run_id=7,
            source_version=1,
            report_json='{"schema":"hades.kanban-task-report.v1"}',
            report_markdown="# Task report",
            generated_at=300,
            idempotency_key="task:task-1:run:7",
        )
        assert conn.execute("SELECT COUNT(*) AS n FROM kanban_reports").fetchone()["n"] == 1

        with pytest.raises(ValueError, match="idempotency key"):
            insert_report(
                conn,
                board_slug="default",
                report_type="task",
                subject_id="task-1",
                terminal_run_id=8,
                source_version=2,
                report_json='{"schema":"changed"}',
                report_markdown="# Changed",
                generated_at=301,
                idempotency_key="task:task-1:run:7",
            )
