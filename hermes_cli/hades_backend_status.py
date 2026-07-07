"""Shared Hades backend status payloads for CLI and UI surfaces."""

from __future__ import annotations

from typing import Any

from hermes_cli import hades_backend_db as db
from hermes_cli.hades_backend_sync import BACKGROUND_SYNC_STATE_KEY


def load_backend_status_payload() -> dict[str, Any]:
    """Return the canonical local Hades backend status payload."""
    with db.connect_closing() as conn:
        agent = db.get_default_agent(conn)
        if agent:
            bindings = conn.execute(
                "SELECT * FROM workspace_bindings WHERE agent_id = ? ORDER BY updated_at DESC",
                (agent.agent_id,),
            ).fetchall()
            memory_caches = {
                str(workspace_binding_id): db.get_memory_cache(conn, str(workspace_binding_id))
                for binding in bindings
                if (workspace_binding_id := _binding_value(binding, "backend_workspace_binding_id"))
            }
            job_counts = db.count_jobs_by_status(conn)
            proposal_counts = db.count_memory_proposals_by_status(conn)
            inbox_counts = db.count_inbox_events(conn)
            last_summary = db.get_sync_state(conn, "last_sync_summary")
            last_error = db.get_sync_state(conn, "last_sync_error")
            background_sync = db.get_sync_state(conn, BACKGROUND_SYNC_STATE_KEY)
            last_summary_updated_at = db.get_sync_state_updated_at(conn, "last_sync_summary")
            last_error_updated_at = db.get_sync_state_updated_at(conn, "last_sync_error")
            background_sync_updated_at = db.get_sync_state_updated_at(conn, BACKGROUND_SYNC_STATE_KEY)
        else:
            bindings = []
            memory_caches = {}
            job_counts = {}
            proposal_counts = {}
            inbox_counts = {"total": 0, "unread": 0}
            last_summary = None
            last_error = None
            background_sync = None
            last_summary_updated_at = None
            last_error_updated_at = None
            background_sync_updated_at = None

    return backend_status_payload(
        agent=agent,
        bindings=bindings,
        memory_caches=memory_caches,
        job_counts=job_counts,
        proposal_counts=proposal_counts,
        inbox_counts=inbox_counts,
        last_summary=last_summary,
        last_error=last_error,
        background_sync=background_sync,
        last_summary_updated_at=last_summary_updated_at,
        last_error_updated_at=last_error_updated_at,
        background_sync_updated_at=background_sync_updated_at,
    )


def backend_status_payload(
    *,
    agent: Any,
    bindings: list[Any],
    job_counts: dict[str, Any],
    proposal_counts: dict[str, Any],
    inbox_counts: dict[str, Any],
    last_summary: dict[str, Any] | None,
    last_error: dict[str, Any] | None,
    memory_caches: dict[str, Any] | None = None,
    background_sync: dict[str, Any] | None = None,
    last_summary_updated_at: int | None = None,
    last_error_updated_at: int | None = None,
    background_sync_updated_at: int | None = None,
) -> dict[str, Any]:
    refused = _count(proposal_counts, "refused") + _count(proposal_counts, "conflicted")
    waiting = _count(job_counts, "waiting_confirmation")
    background_failed = bool(background_sync and background_sync.get("status") == "failed")
    actions: list[str] = []
    if waiting:
        actions.append(f"Review {waiting} backend job(s) waiting for confirmation.")
    if refused:
        actions.append(f"Review {refused} refused/conflicted memory proposal(s).")
    if last_error:
        actions.append("Inspect last backend sync error and rerun `hades backend sync`.")
    elif background_failed:
        actions.append("Background backend sync is backing off; run `hades backend sync` to retry now.")
    summary_scope = "binding" if len(bindings) == 1 else "aggregate"
    binding_payloads = [
        _binding_payload(
            binding,
            memory_cache=(memory_caches or {}).get(str(_binding_value(binding, "backend_workspace_binding_id"))),
            last_summary=last_summary,
            last_error=last_error,
            last_summary_updated_at=last_summary_updated_at,
            summary_scope=summary_scope,
        )
        for binding in bindings
    ]
    awareness = _awareness_summary(
        configured=agent is not None,
        binding_payloads=binding_payloads,
        last_error=last_error,
    )
    if awareness["status"] in {"partial", "degraded", "unmapped"} and not last_error and not background_failed:
        actions.append("Project awareness is incomplete; inspect `awareness` before source-free diagnosis.")
    return {
        "configured": agent is not None,
        "agent": None if agent is None else {
            "agent_id": agent.agent_id,
            "project_id": agent.project_id,
            "base_url": agent.base_url,
            "label": agent.label,
            "capabilities": agent.capabilities,
        },
        "bindings": binding_payloads,
        "awareness": awareness,
        "job_counts": job_counts,
        "proposal_counts": proposal_counts,
        "inbox_counts": inbox_counts,
        "sync": {
            "last_summary": last_summary,
            "last_summary_updated_at": last_summary_updated_at,
            "last_error": last_error,
            "last_error_updated_at": last_error_updated_at,
            "background": background_sync,
            "background_updated_at": background_sync_updated_at,
        },
        "degraded": bool(waiting or refused or last_error or background_failed),
        "actions": actions,
    }


def _count(source: dict[str, Any] | None, key: str) -> int:
    value = (source or {}).get(key, 0)
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)


def _binding_payload(
    binding: Any,
    *,
    memory_cache: Any | None = None,
    last_summary: dict[str, Any] | None = None,
    last_error: dict[str, Any] | None = None,
    last_summary_updated_at: int | None = None,
    summary_scope: str = "aggregate",
) -> dict[str, Any]:
    payload = {
        "workspace_fingerprint": _binding_value(binding, "workspace_fingerprint"),
        "workspace_binding_id": _binding_value(binding, "backend_workspace_binding_id"),
        "project_id": _binding_value(binding, "project_id"),
        "local_project_id": _binding_value(binding, "local_project_id"),
        "display_path": _binding_value(binding, "display_path"),
        "head_commit": _binding_value(binding, "head_commit"),
        "status": _binding_value(binding, "status"),
    }
    payload["awareness"] = _binding_awareness_payload(
        binding=payload,
        memory_cache=memory_cache,
        last_summary=last_summary,
        last_error=last_error,
        last_summary_updated_at=last_summary_updated_at,
        summary_scope=summary_scope,
    )
    return payload


def _binding_awareness_payload(
    *,
    binding: dict[str, Any],
    memory_cache: Any | None,
    last_summary: dict[str, Any] | None,
    last_error: dict[str, Any] | None,
    last_summary_updated_at: int | None,
    summary_scope: str,
) -> dict[str, Any]:
    memory_items = len(getattr(memory_cache, "items", []) or [])
    artifacts_uploaded = _count(last_summary, "artifacts_uploaded")
    artifact_errors = _count(last_summary, "artifact_errors")
    source_slices_uploaded = _count(last_summary, "source_slices_uploaded")
    source_slice_errors = _count(last_summary, "source_slice_errors")
    proposal_errors = _count(last_summary, "proposal_errors")
    bug_evidence_items = (
        _count(last_summary, "bug_evidence_items")
        + _count(last_summary, "bug_evidence_uploaded")
        + _count(last_summary, "bug_evidence_count")
    )
    is_linked = binding["status"] == "linked"
    is_binding_scoped = summary_scope == "binding"
    missing: list[str] = []
    if not is_linked:
        missing.append("workspace_link")
    if memory_items == 0:
        missing.append("shared_memory_cache")
    if artifacts_uploaded == 0 or not is_binding_scoped:
        missing.append("project_artifact_index")
    if source_slices_uploaded == 0 or not is_binding_scoped:
        missing.append("source_slice_index")
    if bug_evidence_items == 0 or not is_binding_scoped:
        missing.append("bug_evidence")

    has_errors = bool(last_error or artifact_errors or source_slice_errors or proposal_errors)
    diagnosable_without_source = bool(is_linked and not has_errors and not missing)
    if not is_linked:
        status = "unlinked"
    elif has_errors:
        status = "degraded"
    elif diagnosable_without_source:
        status = "ready"
    else:
        status = "partial"

    return {
        "status": status,
        "diagnosable_without_source": diagnosable_without_source,
        "coverage": {
            "memory_cache": {
                "status": "present" if memory_items else "missing",
                "items": memory_items,
                "version": getattr(memory_cache, "version", None),
                "updated_at": getattr(memory_cache, "updated_at", None),
            },
            "project_artifacts": {
                "status": _coverage_status(artifacts_uploaded, summary_scope),
                "uploaded_last_sync": artifacts_uploaded,
                "errors_last_sync": artifact_errors,
            },
            "source_slices": {
                "status": _coverage_status(source_slices_uploaded, summary_scope),
                "uploaded_last_sync": source_slices_uploaded,
                "errors_last_sync": source_slice_errors,
            },
            "bug_evidence": {
                "status": _coverage_status(bug_evidence_items, summary_scope, missing_status="unknown"),
                "items_last_sync": bug_evidence_items,
            },
        },
        "quality": {
            "confidence": "ready" if diagnosable_without_source else ("blocked" if has_errors or not is_linked else "incomplete"),
            "missing": missing,
            "summary_scope": summary_scope,
            "last_sync_summary_updated_at": last_summary_updated_at,
        },
    }


def _coverage_status(count: int, summary_scope: str, *, missing_status: str = "missing") -> str:
    if count <= 0:
        return missing_status
    if summary_scope == "binding":
        return "present"
    return "aggregate"


def _awareness_summary(
    *,
    configured: bool,
    binding_payloads: list[dict[str, Any]],
    last_error: dict[str, Any] | None,
) -> dict[str, Any]:
    if not configured:
        return {
            "status": "not_configured",
            "bindings": 0,
            "ready_bindings": 0,
            "partial_bindings": 0,
            "degraded_bindings": 0,
            "diagnosable_without_source_bindings": 0,
        }
    if not binding_payloads:
        return {
            "status": "unmapped",
            "bindings": 0,
            "ready_bindings": 0,
            "partial_bindings": 0,
            "degraded_bindings": 0,
            "diagnosable_without_source_bindings": 0,
        }

    statuses = [str(binding["awareness"]["status"]) for binding in binding_payloads]
    ready = statuses.count("ready")
    partial = statuses.count("partial")
    degraded = statuses.count("degraded")
    diagnosable = sum(1 for binding in binding_payloads if binding["awareness"]["diagnosable_without_source"])
    if last_error or degraded:
        status = "degraded"
    elif partial:
        status = "partial"
    elif ready == len(binding_payloads):
        status = "ready"
    else:
        status = "unmapped"
    return {
        "status": status,
        "bindings": len(binding_payloads),
        "ready_bindings": ready,
        "partial_bindings": partial,
        "degraded_bindings": degraded,
        "diagnosable_without_source_bindings": diagnosable,
    }


def _binding_value(binding: Any, key: str) -> Any:
    try:
        return binding[key]
    except (KeyError, TypeError, IndexError):
        return getattr(binding, key, None)
