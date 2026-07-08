"""Governance quality report helpers for Hades project awareness."""

from __future__ import annotations

from collections import Counter
import time
from typing import Any, Iterable


def build_hades_quality_report(
    *,
    no_codebase_report: dict[str, Any] | None = None,
    suite_report: dict[str, Any] | None = None,
    support_report: dict[str, Any] | None = None,
    note_backfill_report: dict[str, Any] | None = None,
    agent_work_report: dict[str, Any] | None = None,
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

    if suite_report is not None:
        metrics["no_codebase_suite"] = {
            "status": suite_report.get("status"),
            "total": int(suite_report.get("total") or 0),
            "passed": int(suite_report.get("passed") or 0),
            "failed": int(suite_report.get("failed") or 0),
        }
        if suite_report.get("status") != "passed":
            actions.append(
                _action(
                    "fix_no_codebase_quality_suite",
                    "blocker",
                    "Fix failing no-codebase quality suite entries.",
                    count=int(suite_report.get("failed") or 0),
                )
            )

    if support_report is not None:
        metrics["support"] = {
            "configured": bool(support_report.get("configured")),
            "degraded": bool(support_report.get("degraded")),
            "awareness_status": (support_report.get("awareness") or {}).get("status"),
        }
        actions.extend(_support_actions(support_report))

    if note_backfill_report is not None:
        metrics["note_backfill"] = note_backfill_report
        actions.extend(_note_backfill_actions(note_backfill_report))

    if agent_work_report is not None:
        metrics["agent_work"] = agent_work_report
        actions.extend(_agent_work_actions(agent_work_report))

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


def build_note_backfill_quality_report(proposals: Iterable[Any]) -> dict[str, Any]:
    note_proposals = [_proposal_projection(proposal) for proposal in proposals]
    note_proposals = [proposal for proposal in note_proposals if _is_note_backfill_candidate(proposal)]
    by_status = Counter(proposal["status"] for proposal in note_proposals)
    pending_review_count = sum(by_status.get(status, 0) for status in ("pending", "submitted"))
    rejected_count = sum(by_status.get(status, 0) for status in ("refused", "conflicted"))
    evidence_ready_count = sum(1 for proposal in note_proposals if _has_backfill_evidence_ref(proposal))
    missing_evidence_count = len(note_proposals) - evidence_ready_count
    return {
        "schema": "hades.note_backfill_quality.v1",
        "total": len(note_proposals),
        "by_status": dict(sorted(by_status.items())),
        "pending_review_count": pending_review_count,
        "rejected_count": rejected_count,
        "evidence_ref_coverage": (evidence_ready_count / len(note_proposals)) if note_proposals else 1.0,
        "missing_evidence_ref_count": missing_evidence_count,
    }


def build_agent_work_quality_report(work_items: Iterable[Any]) -> dict[str, Any]:
    items = [_work_item_projection(item) for item in work_items]
    by_status = Counter(item["status"] for item in items)
    required = [item for item in items if item["shared_memory_required"]]
    missing = [item for item in required if not item["has_shared_memory_context"]]
    completed_missing = [
        item
        for item in missing
        if item["status"] in {"completed", "completed_with_incomplete_memory"}
    ]
    return {
        "schema": "hades.agent_work_quality.v1",
        "total": len(items),
        "by_status": dict(sorted(by_status.items())),
        "shared_memory_required_count": len(required),
        "shared_memory_context_count": len(required) - len(missing),
        "missing_shared_memory_context_count": len(missing),
        "completed_missing_shared_memory_context_count": len(completed_missing),
        "shared_memory_context_coverage": ((len(required) - len(missing)) / len(required)) if required else 1.0,
        "missing_work_item_ids": [item["work_item_id"] for item in missing[:20]],
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
        "taxonomy_coverage": float(report.get("taxonomy_coverage") or 0.0),
        "causal_pack_coverage": _float_metric(report, "causal_pack_coverage", default=1.0),
        "causal_chain_coverage": _float_metric(report, "causal_chain_coverage", default=1.0),
        "counterfactual_refusal_coverage": _float_metric(report, "counterfactual_refusal_coverage", default=1.0),
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
        ("taxonomy_coverage", "repair_diagnosis_taxonomy", "Diagnosis taxonomy fields did not match the expected bug class or failure classification."),
        ("causal_pack_coverage", "repair_causal_pack_coverage", "Precise source-free diagnoses must reference replayable causal packs."),
        ("causal_chain_coverage", "repair_causal_chain_coverage", "Precise source-free diagnoses must include evidence-to-root-cause causal chains."),
        ("counterfactual_refusal_coverage", "repair_counterfactual_refusal", "Ambiguous source-free fixtures must refuse precise root-cause claims."),
    ):
        if float(report.get(key) or 0.0) < 1.0:
            actions.append(_action(action_id, "blocker", message, value=float(report.get(key) or 0.0)))
    return actions


def _float_metric(report: dict[str, Any], key: str, *, default: float = 0.0) -> float:
    if key not in report:
        return default
    return float(report.get(key) or 0.0)


def _note_backfill_actions(report: dict[str, Any]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    pending_review_count = int(report.get("pending_review_count") or 0)
    if pending_review_count > 0:
        actions.append(
            _action(
                "review_note_backfill_candidates",
                "warning",
                "Review note backfill candidates before relying on old raw notes.",
                count=pending_review_count,
            )
        )
    rejected_count = int(report.get("rejected_count") or 0)
    if rejected_count > 0:
        actions.append(
            _action(
                "acknowledge_rejected_note_backfill",
                "warning",
                "Acknowledge refused or conflicted note backfill proposals after review.",
                count=rejected_count,
            )
        )
    missing_evidence_count = int(report.get("missing_evidence_ref_count") or 0)
    if missing_evidence_count > 0:
        actions.append(
            _action(
                "repair_note_backfill_evidence_refs",
                "warning",
                "Note backfill proposals without evidence refs must be regenerated or manually repaired.",
                count=missing_evidence_count,
            )
        )
    return actions


def _agent_work_actions(report: dict[str, Any]) -> list[dict[str, Any]]:
    missing_count = int(report.get("missing_shared_memory_context_count") or 0)
    if missing_count <= 0:
        return []
    severity = "blocker" if int(report.get("completed_missing_shared_memory_context_count") or 0) > 0 else "warning"
    return [
        _action(
            "repair_agent_work_shared_memory",
            severity,
            "Agent work items that require shared memory must record memory refs or memory_search_status.",
            count=missing_count,
            coverage=float(report.get("shared_memory_context_coverage") or 0.0),
        )
    ]


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


def _proposal_projection(proposal: Any) -> dict[str, Any]:
    if isinstance(proposal, dict):
        return {
            "intent": str(proposal.get("intent") or ""),
            "status": str(proposal.get("status") or "unknown"),
            "provenance": proposal.get("provenance") if isinstance(proposal.get("provenance"), dict) else {},
        }
    return {
        "intent": str(getattr(proposal, "intent", "") or ""),
        "status": str(getattr(proposal, "status", "") or "unknown"),
        "provenance": getattr(proposal, "provenance", {}) if isinstance(getattr(proposal, "provenance", {}), dict) else {},
    }


def _is_note_backfill_candidate(proposal: dict[str, Any]) -> bool:
    provenance = proposal.get("provenance") if isinstance(proposal.get("provenance"), dict) else {}
    return proposal.get("intent") == "note_backfill_candidate" or provenance.get("source") == "hades_note_quality"


def _has_backfill_evidence_ref(proposal: dict[str, Any]) -> bool:
    provenance = proposal.get("provenance") if isinstance(proposal.get("provenance"), dict) else {}
    evidence_ref = provenance.get("evidence_ref")
    if not isinstance(evidence_ref, dict):
        candidate_fact = provenance.get("candidate_fact")
        if isinstance(candidate_fact, dict):
            evidence_ref = candidate_fact.get("evidence_ref")
    if not isinstance(evidence_ref, dict):
        return False
    return bool(evidence_ref.get("schema") and evidence_ref.get("sha256"))


def _work_item_projection(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        result = item.get("result") if isinstance(item.get("result"), dict) else None
        work_item_id = str(item.get("work_item_id") or item.get("id") or "")
        status = str(item.get("status") or "unknown")
        kind = str(item.get("kind") or payload.get("schema") or "")
    else:
        payload = getattr(item, "payload", {}) if isinstance(getattr(item, "payload", {}), dict) else {}
        result = getattr(item, "result", None) if isinstance(getattr(item, "result", None), dict) else None
        work_item_id = str(getattr(item, "work_item_id", "") or "")
        status = str(getattr(item, "status", "") or "unknown")
        kind = str(getattr(item, "kind", "") or payload.get("schema") or "")

    return {
        "work_item_id": work_item_id,
        "status": status,
        "kind": kind,
        "shared_memory_required": _shared_memory_required(payload, kind),
        "has_shared_memory_context": _has_shared_memory_context(payload, result),
    }


def _shared_memory_required(payload: dict[str, Any], kind: str) -> bool:
    return bool(
        payload.get("memory_required")
        or payload.get("project_awareness_required")
        or kind == "hades.kanban_task_work.v1"
        or payload.get("schema") == "hades.kanban_task_work.v1"
    )


def _has_shared_memory_context(payload: dict[str, Any], result: dict[str, Any] | None) -> bool:
    for container in (payload, result or {}):
        if _has_memory_refs(container):
            return True
        status = container.get("memory_search_status")
        if isinstance(status, dict) and str(status.get("status") or "").strip():
            return True
        memory_entry = container.get("memory_entry")
        if isinstance(memory_entry, dict) and _has_memory_refs(memory_entry):
            return True
        memory_payload = memory_entry.get("payload") if isinstance(memory_entry, dict) else None
        if isinstance(memory_payload, dict) and _has_memory_refs(memory_payload):
            return True
    return False


def _has_memory_refs(container: dict[str, Any]) -> bool:
    refs = container.get("memory_refs")
    if isinstance(refs, list) and refs:
        return True
    status = container.get("memory_search_status")
    if isinstance(status, dict):
        refs = status.get("refs")
        return isinstance(refs, list) and bool(refs)
    return False


def _action(action_id: str, severity: str, message: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": action_id,
        "severity": severity,
        "message": message,
        **{key: value for key, value in extra.items() if value not in (None, "")},
    }
