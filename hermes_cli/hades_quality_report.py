"""Governance quality report helpers for Hades project awareness."""

from __future__ import annotations

import time
from typing import Any


def build_hades_quality_report(
    *,
    no_codebase_report: dict[str, Any] | None = None,
    support_report: dict[str, Any] | None = None,
    generated_at: int | None = None,
) -> dict[str, Any]:
    actions: list[dict[str, Any]] = []
    metrics: dict[str, Any] = {}

    if no_codebase_report is None:
        actions.append(
            _action(
                "run_no_codebase_eval",
                "warning",
                "Run the no-codebase diagnosis evaluation fixture before release.",
            )
        )
    else:
        metrics["no_codebase"] = _no_codebase_metrics(no_codebase_report)
        actions.extend(_no_codebase_actions(no_codebase_report))

    if support_report is not None:
        metrics["support"] = {
            "configured": bool(support_report.get("configured")),
            "degraded": bool(support_report.get("degraded")),
            "awareness_status": (support_report.get("awareness") or {}).get("status"),
        }
        actions.extend(_support_actions(support_report))

    blocker_count = sum(1 for action in actions if action["severity"] == "blocker")
    warning_count = sum(1 for action in actions if action["severity"] == "warning")
    status = "failed" if blocker_count else ("attention" if warning_count else "passed")
    return {
        "schema": "hades.quality_report.v1",
        "generated_at": int(generated_at if generated_at is not None else time.time()),
        "status": status,
        "metrics": metrics,
        "action_queue": actions,
        "summary": {
            "blockers": blocker_count,
            "warnings": warning_count,
            "actions": len(actions),
        },
    }


def _no_codebase_metrics(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": report.get("status"),
        "total": int(report.get("total") or 0),
        "passed": int(report.get("passed") or 0),
        "failed": int(report.get("failed") or 0),
        "accuracy": float(report.get("accuracy") or 0.0),
        "root_cause_accuracy": float(report.get("root_cause_accuracy") or 0.0),
        "insufficient_accuracy": float(report.get("insufficient_accuracy") or 0.0),
        "evidence_ref_coverage": float(report.get("evidence_ref_coverage") or 0.0),
        "freshness_coverage": float(report.get("freshness_coverage") or 0.0),
        "awareness_coverage": float(report.get("awareness_coverage") or 0.0),
        "tool_coverage": float(report.get("tool_coverage") or 0.0),
        "tool_order_coverage": float(report.get("tool_order_coverage") or 0.0),
        "persistence_coverage": float(report.get("persistence_coverage") or 0.0),
        "no_codebase_violations": len(report.get("no_codebase_violations") or []),
    }


def _no_codebase_actions(report: dict[str, Any]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    if report.get("status") != "passed" or int(report.get("failed") or 0) > 0:
        actions.append(
            _action(
                "fix_no_codebase_eval_failures",
                "blocker",
                "Fix failing no-codebase diagnosis fixtures before release.",
                count=int(report.get("failed") or 0),
            )
        )
    violations = report.get("no_codebase_violations") or []
    if violations:
        actions.append(
            _action(
                "remove_forbidden_source_access",
                "blocker",
                "No-codebase diagnosis used forbidden source-access tools.",
                count=len(violations),
            )
        )
    for key, action_id, message in (
        ("evidence_ref_coverage", "repair_evidence_ref_coverage", "Required evidence refs are missing from diagnosis reports."),
        ("freshness_coverage", "repair_freshness_coverage", "Diagnosis freshness did not match the fixture or precise claims used stale evidence."),
        ("awareness_coverage", "repair_awareness_coverage", "Diagnosis awareness did not prove the project is source-free diagnosable for precise claims."),
        ("tool_coverage", "repair_hades_tool_coverage", "Required Hades retrieval tools were not used by diagnosis runs."),
        ("tool_order_coverage", "repair_hades_tool_order", "Required Hades retrieval tools were not used in the diagnosis workflow order."),
        ("persistence_coverage", "repair_diagnosis_report_persistence", "Diagnosis reports were not persisted for every required fixture."),
    ):
        if float(report.get(key) or 0.0) < 1.0:
            actions.append(_action(action_id, "blocker", message, value=float(report.get(key) or 0.0)))
    return actions


def _support_actions(report: dict[str, Any]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    if not report.get("configured"):
        actions.append(_action("configure_hades_backend", "warning", "Backend shared memory is not configured locally."))
    awareness = report.get("awareness") if isinstance(report.get("awareness"), dict) else {}
    if awareness.get("status") not in {None, "ready"}:
        actions.append(
            _action(
                "repair_project_awareness",
                "warning",
                "Project awareness is not ready for source-free diagnosis on this device.",
                status=awareness.get("status"),
            )
        )
    if report.get("degraded"):
        actions.append(_action("resolve_backend_degraded_state", "warning", "Backend status is degraded locally."))
    return actions


def _action(action_id: str, severity: str, message: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": action_id,
        "severity": severity,
        "message": message,
        **{key: value for key, value in extra.items() if value not in (None, "")},
    }
