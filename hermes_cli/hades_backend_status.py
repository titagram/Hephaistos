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
