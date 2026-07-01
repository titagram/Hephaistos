"""Reusable Hades backend sync runner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from hermes_cli import hades_backend_db as db


@dataclass(frozen=True)
class SyncResult:
    summary: dict[str, int]
    exit_code: int


def run_backend_sync(
    *,
    client_factory: Callable[[], object] | None = None,
    now: int | None = None,
    quiet: bool = False,
) -> SyncResult:
    from hermes_cli import hades_backend_runtime as runtime
    from hermes_cli.hades_backend_cmd import (
        AUTO_JOB_CAPABILITIES,
        SKIP_JOB_STATUSES,
        _detect_default_capabilities,
        _job_capability,
        _job_id,
        _job_payload,
        _requires_confirmation,
        _response_jobs,
        _status_payload,
        _sync_memory,
    )
    from hermes_cli.hades_backend_client import redact_secret
    from hermes_cli.hades_backend_jobs import execute_job

    with db.connect_closing() as conn:
        agent = db.get_default_agent(conn)
        bindings = db.list_workspace_bindings(conn, status="linked") if agent else []
        expired_jobs = db.expire_waiting_jobs(conn, now=now) if agent else []

    if agent is None:
        return SyncResult({"error": 1}, 1)
    if not bindings:
        return SyncResult({"pulled": 0, "completed": 0, "waiting": 0, "failed": 0, "skipped": 0, "expired": len(expired_jobs)}, 0)

    try:
        client = client_factory() if client_factory is not None else runtime.client_from_config()
    except Exception:
        return SyncResult({"error": 1}, 1)

    pulled = completed = waiting = failed = skipped = 0
    memory_snapshots = proposals_synced = proposal_errors = 0
    artifacts_uploaded = artifact_errors = inbox_events = 0
    sync_errors = 0
    expired = 0

    for job in expired_jobs:
        with db.connect_closing() as conn:
            binding = db.get_binding_for_backend_id(conn, job.workspace_binding_id)
        if binding is None:
            continue
        try:
            client.update_job_status(
                job.job_id,
                **_status_payload(agent, binding, "expired", reason="deadline_expired"),
            )
        except Exception:
            sync_errors += 1
        expired += 1

    for binding in bindings:
        try:
            snapshots, synced, errors = _sync_memory(client, binding)
            memory_snapshots += snapshots
            proposals_synced += synced
            proposal_errors += errors
            sync_errors += errors
        except Exception as exc:
            sync_errors += 1
            _record_sync_error(binding, str(exc))
            if not quiet:
                print(f"backend sync: failed to sync memory for {binding.display_path}: {redact_secret(str(exc))}")

        try:
            inbox = client.list_inbox(project_id=binding.project_id)
            saved = _sync_inbox(inbox, binding.project_id)
            inbox_events += saved
        except AttributeError:
            pass
        except Exception as exc:
            sync_errors += 1
            _record_sync_error(binding, str(exc))
            if not quiet:
                print(f"backend sync: failed to poll Persephone inbox for {binding.display_path}: {redact_secret(str(exc))}")

        try:
            response = client.pull_jobs(
                project_id=binding.project_id,
                agent_id=agent.agent_id,
                workspace_binding_id=binding.backend_workspace_binding_id,
                capabilities=_detect_default_capabilities(),
            )
        except Exception as exc:
            sync_errors += 1
            _record_sync_error(binding, str(exc))
            if not quiet:
                print(f"backend sync: failed to pull jobs for {binding.display_path}: {redact_secret(str(exc))}")
            continue

        for job in _response_jobs(response):
            jid = _job_id(job)
            capability = _job_capability(job)
            payload = _job_payload(job)
            if not jid:
                skipped += 1
                continue
            pulled += 1

            with db.connect_closing() as conn:
                existing = db.get_job(conn, jid)
                if existing and existing.status in SKIP_JOB_STATUSES:
                    skipped += 1
                    continue
                db.upsert_job(
                    conn,
                    job_id=jid,
                    project_id=binding.project_id,
                    workspace_binding_id=binding.backend_workspace_binding_id,
                    capability=capability,
                    payload=payload,
                    status="received",
                )

            try:
                client.update_job_status(jid, **_status_payload(agent, binding, "received"))
                if capability not in AUTO_JOB_CAPABILITIES or _requires_confirmation(job):
                    with db.connect_closing() as conn:
                        db.update_job_status(conn, jid, "waiting_confirmation")
                    client.update_job_status(
                        jid,
                        **_status_payload(
                            agent,
                            binding,
                            "waiting_confirmation",
                            reason="local_confirmation_required",
                        ),
                    )
                    waiting += 1
                    continue

                with db.connect_closing() as conn:
                    db.update_job_status(conn, jid, "started")
                client.update_job_status(jid, **_status_payload(agent, binding, "started"))

                result = execute_job(
                    {"job_id": jid, "capability": capability, "payload": payload},
                    workspace_root=binding.repo_root,
                )
                final_status = str(result.get("status") or "completed")
                if final_status not in {"completed", "failed"}:
                    final_status = "completed"
                with db.connect_closing() as conn:
                    db.update_job_status(conn, jid, final_status, result=result)
                if final_status == "completed":
                    uploaded, upload_failed = _upload_job_artifact(client, agent, binding, jid, result)
                    artifacts_uploaded += uploaded
                    artifact_errors += upload_failed
                    sync_errors += upload_failed
                    client.submit_job_result(jid, **_status_payload(agent, binding, final_status, result=result))
                    completed += 1
                else:
                    client.update_job_status(
                        jid,
                        **_status_payload(agent, binding, final_status, error=redact_secret(result.get("summary", ""))),
                    )
                    failed += 1
            except Exception as exc:
                result = {"status": "failed", "summary": redact_secret(str(exc))}
                with db.connect_closing() as conn:
                    db.update_job_status(conn, jid, "failed", result=result)
                try:
                    client.update_job_status(jid, **_status_payload(agent, binding, "failed", error=result["summary"]))
                except Exception:
                    pass
                failed += 1

    summary = {
        "pulled": pulled,
        "completed": completed,
        "waiting": waiting,
        "failed": failed,
        "skipped": skipped,
        "expired": expired,
        "memory_snapshots": memory_snapshots,
        "proposals_synced": proposals_synced,
        "proposal_errors": proposal_errors,
        "artifacts_uploaded": artifacts_uploaded,
        "artifact_errors": artifact_errors,
        "inbox_events": inbox_events,
    }
    with db.connect_closing() as conn:
        db.record_sync_state(conn, "last_sync_summary", summary)

    return SyncResult(summary, 1 if sync_errors else 0)


def _record_sync_error(binding: db.WorkspaceBinding, message: str) -> None:
    from hermes_cli.hades_backend_client import redact_secret

    with db.connect_closing() as conn:
        db.record_sync_state(
            conn,
            "last_sync_error",
            {
                "workspace_binding_id": binding.backend_workspace_binding_id,
                "project_id": binding.project_id,
                "message": redact_secret(message),
            },
        )


def _upload_job_artifact(client: object, agent: db.BackendAgent, binding: db.WorkspaceBinding, job_id: str, result: dict) -> tuple[int, int]:
    artifact = result.get("artifact") if isinstance(result, dict) else None
    if not isinstance(artifact, dict):
        return (0, 0)
    schema = str(artifact.get("schema") or "").strip()
    if schema not in {"hades.git_tree.v1", "hades.symbols.v1"}:
        return (0, 0)
    try:
        client.upload_artifact(
            project_id=binding.project_id,
            agent_id=agent.agent_id,
            workspace_binding_id=binding.backend_workspace_binding_id,
            job_id=job_id,
            schema=schema,
            artifact=artifact,
            truncated=bool(artifact.get("truncated", False)),
            redactions=int(artifact.get("redactions", 0) or 0),
        )
        return (1, 0)
    except AttributeError:
        return (0, 0)
    except Exception as exc:
        _record_sync_error(binding, f"artifact upload failed: {exc}")
        return (0, 1)


def _sync_inbox(response: dict, project_id: str) -> int:
    events = response.get("events") if isinstance(response, dict) else None
    if not isinstance(events, list):
        return 0
    saved = 0
    with db.connect_closing() as conn:
        for event in events:
            if not isinstance(event, dict):
                continue
            event_id = str(event.get("id") or event.get("event_id") or "").strip()
            event_type = str(event.get("event_type") or "").strip()
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            if not event_id or not event_type:
                continue
            db.save_inbox_event(
                conn,
                event_id=event_id,
                project_id=str(event.get("project_id") or project_id),
                event_type=event_type,
                payload=payload,
            )
            saved += 1
    return saved
