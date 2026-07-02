"""Shared Hades backend status payloads for CLI and UI surfaces."""

from __future__ import annotations

from typing import Any

from hermes_cli import hades_backend_db as db


def load_backend_status_payload() -> dict[str, Any]:
    """Return the canonical local Hades backend status payload."""
    with db.connect_closing() as conn:
        agent = db.get_default_agent(conn)
        if agent:
            bindings = conn.execute(
                "SELECT * FROM workspace_bindings WHERE agent_id = ? ORDER BY updated_at DESC",
                (agent.agent_id,),
            ).fetchall()
            job_counts = db.count_jobs_by_status(conn)
            proposal_counts = db.count_memory_proposals_by_status(conn)
            inbox_counts = db.count_inbox_events(conn)
            last_summary = db.get_sync_state(conn, "last_sync_summary")
            last_error = db.get_sync_state(conn, "last_sync_error")
        else:
            bindings = []
            job_counts = {}
            proposal_counts = {}
            inbox_counts = {"total": 0, "unread": 0}
            last_summary = None
            last_error = None

    return backend_status_payload(
        agent=agent,
        bindings=bindings,
        job_counts=job_counts,
        proposal_counts=proposal_counts,
        inbox_counts=inbox_counts,
        last_summary=last_summary,
        last_error=last_error,
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
) -> dict[str, Any]:
    refused = _count(proposal_counts, "refused") + _count(proposal_counts, "conflicted")
    waiting = _count(job_counts, "waiting_confirmation")
    actions: list[str] = []
    if waiting:
        actions.append(f"Review {waiting} backend job(s) waiting for confirmation.")
    if refused:
        actions.append(f"Review {refused} refused/conflicted memory proposal(s).")
    if last_error:
        actions.append("Inspect last backend sync error and rerun `hades backend sync`.")
    return {
        "configured": agent is not None,
        "agent": None if agent is None else {
            "agent_id": agent.agent_id,
            "project_id": agent.project_id,
            "base_url": agent.base_url,
            "label": agent.label,
            "capabilities": agent.capabilities,
        },
        "bindings": [_binding_payload(binding) for binding in bindings],
        "job_counts": job_counts,
        "proposal_counts": proposal_counts,
        "inbox_counts": inbox_counts,
        "sync": {"last_summary": last_summary, "last_error": last_error},
        "degraded": bool(waiting or refused or last_error),
        "actions": actions,
    }


def _count(source: dict[str, Any], key: str) -> int:
    value = source.get(key, 0)
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)


def _binding_payload(binding: Any) -> dict[str, Any]:
    return {
        "workspace_fingerprint": _binding_value(binding, "workspace_fingerprint"),
        "workspace_binding_id": _binding_value(binding, "backend_workspace_binding_id"),
        "project_id": _binding_value(binding, "project_id"),
        "local_project_id": _binding_value(binding, "local_project_id"),
        "display_path": _binding_value(binding, "display_path"),
        "status": _binding_value(binding, "status"),
    }


def _binding_value(binding: Any, key: str) -> Any:
    try:
        return binding[key]
    except (KeyError, TypeError, IndexError):
        return getattr(binding, key, None)
