"""Shared local actions for Hades backend review surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from hermes_cli import hades_backend_db as db
from hermes_cli.hades_backend_client import redact_secret
from hermes_cli.hades_backend_jobs import execute_job


@dataclass(frozen=True)
class BackendActionResult:
    ok: bool
    status: str
    summary: str
    payload: dict[str, Any]


class BackendActionError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def status_payload(agent: db.BackendAgent, binding: db.WorkspaceBinding, status: str, **extra: Any) -> dict[str, Any]:
    payload = {
        "project_id": binding.project_id,
        "agent_id": agent.agent_id,
        "workspace_binding_id": binding.backend_workspace_binding_id,
        "status": status,
    }
    payload.update(extra)
    return payload


def job_payload_for_display(job: db.BackendJob) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "project_id": job.project_id,
        "workspace_binding_id": job.workspace_binding_id,
        "capability": job.capability,
        "status": job.status,
        "payload_keys": sorted(str(key) for key in job.payload.keys()),
        "result": job.result,
    }


def proposal_payload_for_display(proposal: db.MemoryProposal) -> dict[str, Any]:
    return {
        "proposal_id": proposal.id,
        "project_id": proposal.project_id,
        "workspace_binding_id": proposal.workspace_binding_id,
        "action": proposal.action,
        "intent": proposal.intent,
        "summary": proposal.summary,
        "status": proposal.status,
        "reason": proposal.reason,
    }


def list_backend_jobs(*, statuses: Iterable[str] | None = None) -> list[dict[str, Any]]:
    with db.connect_closing() as conn:
        jobs = db.list_jobs(conn, statuses=statuses)
    return [job_payload_for_display(job) for job in jobs]


def list_memory_proposals(*, statuses: Iterable[str] | None = None) -> list[dict[str, Any]]:
    with db.connect_closing() as conn:
        proposals = db.list_memory_proposals(conn) if statuses is None else db.list_memory_proposals_by_status(conn, statuses)
    return [proposal_payload_for_display(proposal) for proposal in proposals]


def approve_backend_job(job_id: str) -> BackendActionResult:
    from hermes_cli import hades_backend_runtime as runtime
    from hermes_cli.hades_backend_sync import _upload_job_artifact

    job_id = str(job_id or "").strip()
    with db.connect_closing() as conn:
        agent = db.get_default_agent(conn)
        job = db.get_job(conn, job_id)
        binding = db.get_binding_for_backend_id(conn, job.workspace_binding_id) if job else None
    if agent is None:
        raise BackendActionError("backend not configured", status_code=409)
    if job is None:
        raise BackendActionError(f"job not found: {job_id}", status_code=404)
    if binding is None:
        raise BackendActionError(f"workspace binding not found: {job.workspace_binding_id}", status_code=409)
    if job.status != "waiting_confirmation":
        raise BackendActionError(f"job {job_id} is {job.status}, not waiting_confirmation", status_code=409)

    client = None
    try:
        client = runtime.client_from_config()
        with db.connect_closing() as conn:
            db.update_job_status(conn, job_id, "started")
        client.update_job_status(job_id, **status_payload(agent, binding, "started"))
        result = execute_job(
            {"job_id": job.job_id, "capability": job.capability, "payload": job.payload},
            workspace_root=binding.repo_root,
        )
        final_status = str(result.get("status") or "completed")
        if final_status not in {"completed", "failed"}:
            final_status = "completed"
        with db.connect_closing() as conn:
            updated = db.update_job_status(conn, job_id, final_status, result=result)
        if final_status == "completed":
            _upload_job_artifact(client, agent, binding, job_id, result)
            client.submit_job_result(job_id, **status_payload(agent, binding, final_status, result=result))
        else:
            client.update_job_status(
                job_id,
                **status_payload(agent, binding, final_status, error=redact_secret(result.get("summary", ""))),
            )
        summary = str(result.get("summary") or final_status)
        return BackendActionResult(
            ok=final_status == "completed",
            status=final_status,
            summary=summary,
            payload={"job": job_payload_for_display(updated or job)},
        )
    except Exception as exc:
        result = {"status": "failed", "summary": redact_secret(str(exc))}
        with db.connect_closing() as conn:
            updated = db.update_job_status(conn, job_id, "failed", result=result)
        if client is not None:
            try:
                client.update_job_status(job_id, **status_payload(agent, binding, "failed", error=result["summary"]))
            except Exception:
                pass
        return BackendActionResult(
            ok=False,
            status="failed",
            summary=result["summary"],
            payload={"job": job_payload_for_display(updated or job)},
        )


def refuse_backend_job(job_id: str, *, reason: str = "local_refused") -> BackendActionResult:
    from hermes_cli import hades_backend_runtime as runtime

    job_id = str(job_id or "").strip()
    reason = redact_secret(str(reason or "local_refused"))
    with db.connect_closing() as conn:
        agent = db.get_default_agent(conn)
        job = db.get_job(conn, job_id)
        binding = db.get_binding_for_backend_id(conn, job.workspace_binding_id) if job else None
    if agent is None:
        raise BackendActionError("backend not configured", status_code=409)
    if job is None:
        raise BackendActionError(f"job not found: {job_id}", status_code=404)
    if binding is None:
        raise BackendActionError(f"workspace binding not found: {job.workspace_binding_id}", status_code=409)
    if job.status != "waiting_confirmation":
        raise BackendActionError(f"job {job_id} is {job.status}, not waiting_confirmation", status_code=409)

    try:
        client = runtime.client_from_config()
        client.update_job_status(job_id, **status_payload(agent, binding, "cancelled", reason=reason))
    except Exception as exc:
        raise BackendActionError(redact_secret(str(exc)), status_code=502) from exc
    with db.connect_closing() as conn:
        updated = db.update_job_status(conn, job_id, "cancelled", result={"summary": reason})
    return BackendActionResult(
        ok=True,
        status="cancelled",
        summary=reason,
        payload={"job": job_payload_for_display(updated or job)},
    )


def acknowledge_memory_proposal(proposal_id: str) -> BackendActionResult:
    proposal_id = str(proposal_id or "").strip()
    with db.connect_closing() as conn:
        proposals = db.list_memory_proposals(conn, ids=[proposal_id])
        if not proposals:
            raise BackendActionError(f"proposal not found: {proposal_id}", status_code=404)
        proposal = proposals[0]
        if proposal.status not in {"refused", "conflicted"}:
            raise BackendActionError(
                f"proposal {proposal_id} is {proposal.status}, not refused/conflicted",
                status_code=409,
            )
        db.mark_memory_proposal_status(conn, proposal_id, "acknowledged", proposal.reason)
        updated = db.list_memory_proposals(conn, ids=[proposal_id])[0]
    return BackendActionResult(
        ok=True,
        status="acknowledged",
        summary=updated.reason or "acknowledged",
        payload={"proposal": proposal_payload_for_display(updated)},
    )
