"""Deterministic local evidence reports derived from terminal Kanban rows."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from hermes_cli import kanban_db as kb
from hermes_cli.hades_backend_client import redact_secret
from hermes_cli.org_run_store import (
    KanbanReportRecord,
    get_org_run,
    insert_report,
    list_org_nodes,
    refresh_org_run_state,
)


_MAX_ITEMS = kb._CTX_MAX_PRIOR_ATTEMPTS
_MAX_TEXT = kb._CTX_MAX_FIELD_BYTES


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _safe_text(value: Any) -> str:
    text = redact_secret(str(value or "").strip())
    if len(text) <= _MAX_TEXT:
        return text
    return text[:_MAX_TEXT] + f"… [truncated, {len(text) - _MAX_TEXT} chars omitted]"


def _safe_value(value: Any, *, depth: int = 0) -> Any:
    """Keep report evidence bounded, ordered, and safe for local persistence."""
    if depth >= _MAX_ITEMS:
        return "[nested structured evidence truncated]"
    if isinstance(value, str):
        return _safe_text(value)
    if isinstance(value, dict):
        return {
            _safe_text(key): _safe_value(value[key], depth=depth + 1)
            for key in sorted(value, key=lambda item: str(item))[:_MAX_ITEMS]
        }
    if isinstance(value, (list, tuple)):
        return [_safe_value(item, depth=depth + 1) for item in value[:_MAX_ITEMS]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _safe_text(value)


def _metadata_list(metadata: dict[str, Any], key: str) -> list[Any]:
    value = metadata.get(key, [])
    return _safe_value(value) if isinstance(value, list) else []


def _prior_attempts(conn: sqlite3.Connection, task_id: str, terminal_run_id: int) -> list[dict[str, Any]]:
    attempts = [
        run for run in kb.list_runs(conn, task_id, include_active=False)
        if run.id != terminal_run_id and run.outcome != "completed"
    ][-_MAX_ITEMS:]
    return [
        {
            "run_id": run.id,
            "status": _safe_text(run.status),
            "outcome": _safe_text(run.outcome),
            "summary": _safe_text(run.summary),
            "error": _safe_text(run.error),
            "started_at": run.started_at,
            "ended_at": run.ended_at,
        }
        for run in attempts
    ]


def _task_source_version(conn: sqlite3.Connection, task_id: str) -> int:
    row = conn.execute(
        "SELECT r.plan_version FROM kanban_org_nodes n "
        "JOIN kanban_org_runs r ON r.run_id = n.run_id "
        "WHERE n.task_id = ? AND n.state = 'active' "
        "ORDER BY r.plan_version DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    return int(row["plan_version"]) if row is not None else 1


def _task_markdown(payload: dict[str, Any]) -> str:
    changed = payload["changed_files"] or ["None recorded."]
    tests = payload["tests"] or ["None recorded."]
    review = payload["review"] if payload["review"] is not None else "None recorded."
    risks = payload["residual_risks"] or ["None recorded."]
    lines = [
        f"# Development report: {payload['task_id']}",
        "",
        "## Objective",
        payload["title"],
        "",
        "## Changes",
        *[f"- {item}" for item in changed],
        "",
        "## Verification",
        *[f"- {_canonical_json(item) if isinstance(item, dict) else item}" for item in tests],
        "",
        "## Review",
        _canonical_json(review) if isinstance(review, dict) else str(review),
        "",
        "## Regressions and residual risk",
        "Regressions:",
        *[f"- {item}" for item in (payload["regressions"] or ["None recorded."])],
        "Residual risk:",
        *[f"- {item}" for item in risks],
        "",
        "## Provenance",
        f"- Board: {payload['board_slug']}",
        f"- Terminal task run: {payload['terminal_run_id']}",
        f"- Prior attempts: {len(payload['prior_attempts'])}",
        f"- Generated at: {payload['generated_at']}",
        "",
    ]
    return "\n".join(lines)


def get_report(conn: sqlite3.Connection, report_id: int) -> KanbanReportRecord | None:
    row = conn.execute("SELECT * FROM kanban_reports WHERE id = ?", (int(report_id),)).fetchone()
    if row is None:
        return None
    terminal_run_id = row["terminal_run_id"]
    return KanbanReportRecord(
        id=int(row["id"]), board_slug=str(row["board_slug"]), report_type=str(row["report_type"]),
        subject_id=str(row["subject_id"]),
        terminal_run_id=int(terminal_run_id) if terminal_run_id is not None else None,
        source_version=int(row["source_version"]), report_json=str(row["report_json"]),
        report_markdown=str(row["report_markdown"]), generated_at=int(row["generated_at"]),
        idempotency_key=str(row["idempotency_key"]),
    )


def list_reports(
    conn: sqlite3.Connection,
    *, report_type: str | None = None, subject_id: str | None = None, run_id: str | None = None,
) -> list[KanbanReportRecord]:
    clauses: list[str] = []
    params: list[Any] = []
    if report_type is not None:
        clauses.append("report_type = ?")
        params.append(report_type)
    if subject_id is not None:
        clauses.append("subject_id = ?")
        params.append(subject_id)
    if run_id is not None:
        clauses.append("subject_id = ?")
        params.append(run_id)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        "SELECT id FROM kanban_reports" + where + " ORDER BY source_version ASC, generated_at ASC, id ASC",
        params,
    ).fetchall()
    return [record for row in rows if (record := get_report(conn, int(row["id"]))) is not None]


def project_task_completion(
    conn: sqlite3.Connection, task_id: str, *, board: str,
) -> KanbanReportRecord | None:
    """Persist one canonical report for a completed task's terminal run."""
    task = kb.get_task(conn, task_id)
    if task is None or task.status != "done":
        return None
    completed = [
        run for run in kb.list_runs(conn, task_id, include_active=False)
        if run.outcome == "completed" and run.ended_at is not None
    ]
    if not completed:
        return None
    run = completed[-1]
    metadata = run.metadata if isinstance(run.metadata, dict) else {}
    source_version = _task_source_version(conn, task_id)
    generated_at = int(run.ended_at)
    payload = {
        "schema": "hades.kanban-task-report.v1",
        "board_slug": _safe_text(board),
        "task_id": task.id,
        "terminal_run_id": run.id,
        "title": _safe_text(task.title),
        "status": "completed",
        "summary": _safe_text(run.summary),
        "changed_files": _metadata_list(metadata, "changed_files"),
        "tests": _metadata_list(metadata, "tests_run"),
        "review": _safe_value(metadata.get("review")) if metadata.get("review") is not None else None,
        "regressions": _metadata_list(metadata, "regressions"),
        "residual_risks": _metadata_list(metadata, "residual_risks"),
        "prior_attempts": _prior_attempts(conn, task_id, run.id),
        "generated_at": generated_at,
    }
    return insert_report(
        conn,
        board_slug=payload["board_slug"], report_type="task", subject_id=task.id,
        terminal_run_id=run.id, source_version=source_version,
        report_json=_canonical_json(payload), report_markdown=_task_markdown(payload),
        generated_at=generated_at,
        idempotency_key=f"task:{task.id}:run:{run.id}:v{source_version}",
    )


def _latest_task_reports(
    conn: sqlite3.Connection, task_ids: list[str],
) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for task_id in task_ids[:_MAX_ITEMS]:
        rows = list_reports(conn, report_type="task", subject_id=task_id)
        if rows:
            reports.append(json.loads(rows[-1].report_json))
    return reports


def _org_markdown(payload: dict[str, Any]) -> str:
    return _task_markdown({
        "task_id": payload["run_id"], "title": payload["objective"],
        "changed_files": payload["changed_files"], "tests": payload["tests"],
        "review": payload["review"], "regressions": payload["regressions"],
        "residual_risks": payload["residual_risks"], "board_slug": payload["board_slug"],
        "terminal_run_id": payload["finalization_run_id"],
        "prior_attempts": payload["blockers_resolved"], "generated_at": payload["generated_at"],
    })


def project_org_run_completion(
    conn: sqlite3.Connection, run_id: str, *, board: str,
) -> KanbanReportRecord | None:
    """Persist the final report only after every active OrgRun gate is done."""
    org_run = get_org_run(conn, run_id)
    if org_run is None or org_run.board_slug != board:
        return None
    nodes = [node for node in list_org_nodes(conn, run_id) if node.state == "active"]
    non_anchor = [node for node in nodes if node.node_kind != "anchor"]
    if not non_anchor or any((task := kb.get_task(conn, node.task_id)) is None or task.status != "done" for node in non_anchor):
        return None
    finalization = next((node for node in nodes if node.node_kind == "finalization"), None)
    if finalization is None:
        return None
    final_runs = [
        run for run in kb.list_runs(conn, finalization.task_id, include_active=False)
        if run.outcome == "completed" and run.ended_at is not None
    ]
    if not final_runs:
        return None
    final_run = final_runs[-1]
    for node in nodes:
        project_task_completion(conn, node.task_id, board=board)
    critical_node_kinds = {"integration", "task_review", "global_review", "finalization"}
    critical_task_ids = [
        node.task_id for node in non_anchor if node.node_kind in critical_node_kinds
    ]
    other_task_ids = sorted(
        node.task_id for node in non_anchor if node.task_id not in set(critical_task_ids)
    )
    task_reports = _latest_task_reports(conn, critical_task_ids + other_task_ids)
    changed_files = [item for report in task_reports for item in report.get("changed_files", [])][:_MAX_ITEMS]
    tests = [item for report in task_reports for item in report.get("tests", [])][:_MAX_ITEMS]
    regressions = [item for report in task_reports for item in report.get("regressions", [])][:_MAX_ITEMS]
    residual_risks = [item for report in task_reports for item in report.get("residual_risks", [])][:_MAX_ITEMS]
    blockers = [
        attempt for report in task_reports for attempt in report.get("prior_attempts", [])
        if attempt.get("outcome") == "blocked"
    ][:_MAX_ITEMS]
    plan_row = conn.execute(
        "SELECT plan_json FROM kanban_org_plan_versions WHERE run_id = ? AND plan_version = ?",
        (run_id, org_run.plan_version),
    ).fetchone()
    plan = json.loads(plan_row["plan_json"]) if plan_row is not None else {}
    review_nodes = [node for node in non_anchor if node.node_kind in {"task_review", "global_review"}]
    review = {
        "integration": next((report for report in task_reports if report["task_id"] == next((n.task_id for n in non_anchor if n.node_kind == "integration"), "")), None),
        "reviews": [report for report in task_reports if report["task_id"] in {node.task_id for node in review_nodes}],
        "finalization": next((report for report in task_reports if report["task_id"] == finalization.task_id), None),
    }
    payload = {
        "schema": "hades.org-run-report.v1", "board_slug": _safe_text(board),
        "run_id": run_id, "plan_version": org_run.plan_version,
        "plan_hash": org_run.plan_hash, "base_commit": _safe_text(org_run.base_commit),
        "objective": _safe_text(plan.get("objective", "")),
        "task_reports": task_reports, "changed_files": changed_files, "tests": tests,
        "review": review, "regressions": regressions, "blockers_resolved": blockers,
        "residual_risks": residual_risks, "finalization_run_id": final_run.id,
        "generated_at": int(final_run.ended_at),
    }
    record = insert_report(
        conn, board_slug=payload["board_slug"], report_type="org_run_final", subject_id=run_id,
        terminal_run_id=None, source_version=org_run.plan_version,
        report_json=_canonical_json(payload), report_markdown=_org_markdown(payload),
        generated_at=payload["generated_at"],
        idempotency_key=f"org-run:{run_id}:final-report:v{org_run.plan_version}",
    )
    refresh_org_run_state(conn, run_id)
    return record


def project_after_task_completion(
    conn: sqlite3.Connection, task_id: str, *, board: str,
) -> tuple[KanbanReportRecord, ...]:
    """Project terminal task evidence and its OrgRun report when now eligible."""
    records: list[KanbanReportRecord] = []
    task_report = project_task_completion(conn, task_id, board=board)
    if task_report is not None:
        records.append(task_report)
    rows = conn.execute(
        "SELECT DISTINCT run_id FROM kanban_org_nodes WHERE task_id = ? AND state = 'active'",
        (task_id,),
    ).fetchall()
    for row in rows:
        org_report = project_org_run_completion(conn, str(row["run_id"]), board=board)
        if org_report is not None:
            records.append(org_report)
    return tuple(records)
