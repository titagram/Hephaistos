"""Shared local actions for Hades backend review surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from hermes_cli import hades_backend_db as db
from hermes_cli.hades_backend_client import HadesBackendError, redact_secret
from hermes_cli.hades_backend_jobs import execute_job

RESOLVED_BUG_VERIFICATION_STATUSES = {"user_confirmed", "test_passed", "manual_review"}


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


def _clean_text(value: Any) -> str | None:
    clean = str(value or "").strip()
    return clean or None


def _select_workspace_binding(workspace_binding_id: str | None = None) -> tuple[db.BackendAgent, db.WorkspaceBinding]:
    from hermes_cli.hades_backend_cmd import _current_workspace_binding

    clean_id = _clean_text(workspace_binding_id)
    if not clean_id:
        return _current_workspace_binding()

    with db.connect_closing() as conn:
        agent = db.get_default_agent(conn)
        if agent is None:
            raise BackendActionError("Hades backend is not configured", status_code=409)
        binding = db.get_binding_for_backend_id(conn, clean_id)

    if binding is None:
        raise BackendActionError(f"Hades backend workspace binding {clean_id} is not known locally", status_code=404)
    if binding.project_id != agent.project_id:
        raise BackendActionError(
            f"Hades backend workspace binding {clean_id} belongs to project {binding.project_id}, "
            f"not configured project {agent.project_id}",
            status_code=409,
        )
    return agent, binding


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


def _remote_job_missing(exc: BaseException) -> bool:
    if not isinstance(exc, HadesBackendError):
        return False
    if exc.status_code != 404:
        return False
    return exc.code in {None, "", "job_not_found"}


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


def privacy_export(*, include_content: bool = False) -> BackendActionResult:
    from hermes_cli import hades_backend_runtime as runtime

    _agent, binding = _select_workspace_binding()
    try:
        client = runtime.client_from_config()
        try:
            response = client.privacy_export(
                project_id=binding.project_id,
                workspace_binding_id=binding.backend_workspace_binding_id,
                include_content=bool(include_content),
            )
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()
    except Exception as exc:
        raise BackendActionError(redact_secret(str(exc)), status_code=502) from exc

    return BackendActionResult(
        ok=True,
        status="exported",
        summary="Privacy export completed",
        payload=response if isinstance(response, dict) else {"export": response},
    )


def privacy_delete(*, confirm: bool = False) -> BackendActionResult:
    from hermes_cli import hades_backend_runtime as runtime

    _agent, binding = _select_workspace_binding()
    dry_run = not bool(confirm)
    try:
        client = runtime.client_from_config()
        try:
            response = client.privacy_delete(
                project_id=binding.project_id,
                workspace_binding_id=binding.backend_workspace_binding_id,
                dry_run=dry_run,
                confirm=not dry_run,
            )
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()
    except Exception as exc:
        raise BackendActionError(redact_secret(str(exc)), status_code=502) from exc

    return BackendActionResult(
        ok=True,
        status="dry_run" if dry_run else "deleted",
        summary="Privacy delete dry-run completed" if dry_run else "Privacy delete completed",
        payload=response if isinstance(response, dict) else {"delete": response},
    )


def retention_cleanup(*, retention_days: int, confirm: bool = False) -> BackendActionResult:
    from hermes_cli import hades_backend_runtime as runtime

    try:
        days = int(retention_days)
    except (TypeError, ValueError) as exc:
        raise BackendActionError("retention_days must be an integer", status_code=400) from exc
    if days < 1:
        raise BackendActionError("retention_days must be greater than zero", status_code=400)

    _agent, binding = _select_workspace_binding()
    dry_run = not bool(confirm)
    try:
        client = runtime.client_from_config()
        try:
            response = client.privacy_retention_cleanup(
                project_id=binding.project_id,
                workspace_binding_id=binding.backend_workspace_binding_id,
                retention_days=days,
                dry_run=dry_run,
                confirm=not dry_run,
            )
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()
    except Exception as exc:
        raise BackendActionError(redact_secret(str(exc)), status_code=502) from exc

    return BackendActionResult(
        ok=True,
        status="dry_run" if dry_run else "deleted",
        summary="Retention cleanup dry-run completed" if dry_run else "Retention cleanup completed",
        payload=response if isinstance(response, dict) else {"cleanup": response},
    )


def approve_backend_job(job_id: str) -> BackendActionResult:
    from hermes_cli import hades_backend_runtime as runtime
    from hermes_cli.hades_backend_sync import _upload_job_artifact, _upload_job_source_slice

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
            _upload_job_source_slice(client, agent, binding, job_id, result)
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
        if _remote_job_missing(exc):
            result = {
                "status": "expired",
                "summary": "Remote Hades job no longer exists; local cached job expired.",
            }
            with db.connect_closing() as conn:
                updated = db.update_job_status(conn, job_id, "expired", result=result)
            return BackendActionResult(
                ok=False,
                status="expired",
                summary=result["summary"],
                payload={"job": job_payload_for_display(updated or job)},
            )

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


def _create_inline_bug_evidence(
    client: object,
    binding: db.WorkspaceBinding,
    *,
    bug_report_id: str | None,
    text: str,
    kind: str,
    source: str,
    retention_class: str,
) -> str | None:
    from hermes_cli.hades_backend_cmd import _evidence_payload, _first_interesting_line

    redacted = redact_secret(text)
    response = client.create_bug_evidence(
        project_id=binding.project_id,
        workspace_binding_id=binding.backend_workspace_binding_id,
        bug_report_id=bug_report_id,
        kind=kind,
        summary=_first_interesting_line(redacted),
        payload=_evidence_payload(kind, redacted, source, False),
        source=source,
        redactions=1 if redacted != text else 0,
        retention_class=retention_class,
    )
    evidence = response.get("evidence") if isinstance(response.get("evidence"), dict) else response
    return str(evidence.get("id")) if isinstance(evidence, dict) and evidence.get("id") else None


def create_bug_intake(
    *,
    title: str,
    symptom: str,
    workspace_binding_id: str | None = None,
    steps: str | None = None,
    expected: str | None = None,
    actual: str | None = None,
    severity: str | None = None,
    environment: str | None = None,
    failing_test: str | None = None,
    runtime_log: str | None = None,
    deploy_commit: str | None = None,
    workspace_head: str | None = None,
    request_url: str | None = None,
    request_method: str | None = None,
    response_status: int | None = None,
    source: str = "desktop",
) -> BackendActionResult:
    from hermes_cli import hades_backend_runtime as runtime
    from hermes_cli.hades_backend_cmd import (
        _bug_report_id,
        _clean_commit,
        _create_deploy_version_evidence,
        _create_http_context_evidence,
    )

    clean_title = _clean_text(title)
    clean_symptom = _clean_text(symptom)
    if not clean_title:
        raise BackendActionError("bug title is required", status_code=400)
    if not clean_symptom:
        raise BackendActionError("bug symptom is required", status_code=400)

    agent, binding = _select_workspace_binding(workspace_binding_id)
    client = runtime.client_from_config()
    try:
        report_response = client.create_bug_report(
            project_id=binding.project_id,
            workspace_binding_id=binding.backend_workspace_binding_id,
            title=clean_title,
            symptom=clean_symptom,
            payload={
                "schema": "hades.bug_intake.v1",
                "source": source,
                "steps": _clean_text(steps),
                "expected": _clean_text(expected),
                "actual": _clean_text(actual),
                "severity": _clean_text(severity),
                "environment": _clean_text(environment),
                "agent_id": agent.agent_id,
            },
        )
        bug_report_id = _bug_report_id(report_response)
        evidence_ids: list[str | None] = []

        if clean_failing_test := _clean_text(failing_test):
            evidence_ids.append(
                _create_inline_bug_evidence(
                    client,
                    binding,
                    bug_report_id=bug_report_id,
                    text=clean_failing_test,
                    kind="failing_test",
                    source=f"{source}_failing_test",
                    retention_class="test_failure",
                )
            )

        if clean_runtime_log := _clean_text(runtime_log):
            evidence_ids.append(
                _create_inline_bug_evidence(
                    client,
                    binding,
                    bug_report_id=bug_report_id,
                    text=clean_runtime_log,
                    kind="log_excerpt",
                    source=f"{source}_runtime_log",
                    retention_class="log_excerpt",
                )
            )

        if clean_deploy_commit := _clean_commit(deploy_commit):
            evidence_ids.append(
                _create_deploy_version_evidence(
                    client,
                    binding,
                    bug_report_id=bug_report_id,
                    deploy_commit=clean_deploy_commit,
                    workspace_head_commit=_clean_text(workspace_head) or binding.head_commit,
                    environment=_clean_text(environment),
                    source=f"{source}_deploy",
                )
            )

        if _clean_text(request_url) or response_status is not None:
            evidence_ids.extend(
                _create_http_context_evidence(
                    client,
                    binding,
                    bug_report_id=bug_report_id,
                    method=_clean_text(request_method) or "GET",
                    url=_clean_text(request_url) or "",
                    status=response_status,
                    request_file=None,
                    response_file=None,
                    environment=_clean_text(environment),
                    source=f"{source}_http",
                )
            )
    except BackendActionError:
        raise
    except Exception as exc:
        raise BackendActionError(redact_secret(str(exc)), status_code=502) from exc
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()

    return BackendActionResult(
        ok=True,
        status="created",
        summary="Bug report created",
        payload={
            "bug_report_id": bug_report_id,
            "project_id": binding.project_id,
            "workspace_binding_id": binding.backend_workspace_binding_id,
            "evidence_ids": evidence_ids,
            "agent_id": agent.agent_id,
        },
    )


def get_bug_report_detail(bug_report_id: str) -> BackendActionResult:
    from hermes_cli import hades_backend_runtime as runtime

    clean_id = _clean_text(bug_report_id)
    if not clean_id:
        raise BackendActionError("bug report id is required", status_code=400)

    _agent, binding = _select_workspace_binding()
    try:
        client = runtime.client_from_config()
        try:
            response = client.get_bug_report(
                clean_id,
                project_id=binding.project_id,
                workspace_binding_id=binding.backend_workspace_binding_id,
            )
            packs_method = getattr(client, "evidence_packs", None)
            pack_response = None
            pack_error = None
            if callable(packs_method):
                try:
                    pack_response = packs_method(
                        project_id=binding.project_id,
                        workspace_binding_id=binding.backend_workspace_binding_id,
                        bug_report_id=clean_id,
                        limit=5,
                    )
                except Exception as exc:
                    pack_error = redact_secret(str(exc))
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()
    except Exception as exc:
        raise BackendActionError(redact_secret(str(exc)), status_code=502) from exc

    payload = dict(response) if isinstance(response, dict) else {"bug_report": response}
    payload.setdefault("project_id", binding.project_id)
    payload.setdefault("workspace_binding_id", binding.backend_workspace_binding_id)
    if isinstance(pack_response, dict):
        packs = pack_response.get("items") or pack_response.get("evidence_packs") or []
        payload["evidence_packs"] = packs if isinstance(packs, list) else []
        payload["evidence_pack_count"] = pack_response.get("count", len(payload["evidence_packs"]))
        if isinstance(pack_response.get("freshness"), dict):
            payload["evidence_pack_freshness"] = pack_response["freshness"]
    if pack_error:
        payload["evidence_pack_error"] = pack_error
    report = payload.get("bug_report") if isinstance(payload.get("bug_report"), dict) else {}
    title = _clean_text(report.get("title")) if isinstance(report, dict) else None
    return BackendActionResult(
        ok=True,
        status="loaded",
        summary=f"Bug report {clean_id} loaded" if not title else f"Bug report loaded: {title}",
        payload=payload,
    )


def promote_diagnosis_report(
    diagnosis_report_id: str,
    *,
    verification_status: str,
    fix_commit: str | None = None,
    fix_pr_url: str | None = None,
    affected_symbols: list[str] | None = None,
    regression_tests: list[str] | None = None,
    notes: str | None = None,
) -> BackendActionResult:
    from hermes_cli import hades_backend_runtime as runtime
    from hermes_cli.hades_backend_cmd import _current_workspace_binding

    clean_id = str(diagnosis_report_id or "").strip()
    if not clean_id:
        raise BackendActionError("diagnosis report id is required", status_code=400)
    clean_verification = str(verification_status or "").strip()
    if clean_verification not in RESOLVED_BUG_VERIFICATION_STATUSES:
        raise BackendActionError(
            f"unsupported verification status: {clean_verification}",
            status_code=400,
        )
    agent, binding = _current_workspace_binding()
    redacted_notes = redact_secret(notes or "").strip()
    payload = {"notes": redacted_notes} if redacted_notes else None
    redactions = 1 if notes and redacted_notes != notes else 0
    try:
        client = runtime.client_from_config()
        try:
            response = client.promote_diagnosis_report(
                clean_id,
                project_id=binding.project_id,
                workspace_binding_id=binding.backend_workspace_binding_id,
                verification_status=clean_verification,
                fix_commit=str(fix_commit or "").strip() or None,
                fix_pr_url=str(fix_pr_url or "").strip() or None,
                affected_symbols=[str(item).strip() for item in affected_symbols or [] if str(item).strip()],
                regression_tests=[str(item).strip() for item in regression_tests or [] if str(item).strip()],
                payload=payload,
                redactions=redactions,
            )
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()
    except Exception as exc:
        raise BackendActionError(redact_secret(str(exc)), status_code=502) from exc

    memory = response.get("resolved_bug_memory") if isinstance(response, dict) else None
    memory_id = memory.get("id") if isinstance(memory, dict) else None
    already_promoted = bool(response.get("already_promoted")) if isinstance(response, dict) else False
    status = "already_promoted" if already_promoted else "promoted"
    return BackendActionResult(
        ok=True,
        status=status,
        summary=f"Diagnosis {clean_id} promoted to resolved bug memory",
        payload={
            "project_id": binding.project_id,
            "workspace_binding_id": binding.backend_workspace_binding_id,
            "diagnosis_report_id": clean_id,
            "resolved_bug_memory_id": memory_id,
            "resolved_bug": response,
            "agent_id": agent.agent_id,
        },
    )
