"""Reusable Hades backend sync runner."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import threading
import time
from typing import Callable

from hermes_cli import hades_backend_db as db

logger = logging.getLogger("hermes_cli.hades_backend")


@dataclass(frozen=True)
class SyncResult:
    summary: dict[str, int]
    exit_code: int


@dataclass(frozen=True)
class BackgroundSyncDecision:
    status: str
    reason: str
    summary: dict[str, int] | None = None


BACKGROUND_SYNC_STATE_KEY = "background_sync"
_BACKGROUND_SYNC_LOCK = threading.Lock()
_BACKGROUND_SYNC_RUNNING = False


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
        _sync_memory,
    )
    from hermes_cli.hades_backend_actions import status_payload as _status_payload
    from hermes_cli.hades_backend_client import redact_secret
    from hermes_cli.hades_backend_jobs import execute_job

    with db.connect_closing() as conn:
        agent = db.get_default_agent(conn)
        bindings = db.list_workspace_bindings(conn, status="linked") if agent else []
        expired_jobs = db.expire_waiting_jobs(conn, now=now) if agent else []

    if agent is None:
        logger.info(
            "hades_backend.sync.skipped",
            extra={"hades_event": "sync.skipped", "hades_reason": "not_configured"},
        )
        return SyncResult({"error": 1}, 1)
    if not bindings:
        logger.info(
            "hades_backend.sync.skipped",
            extra={
                "hades_event": "sync.skipped",
                "hades_reason": "no_linked_workspace",
                "hades_agent_id": agent.agent_id,
                "hades_project_id": agent.project_id,
                "hades_expired_jobs": len(expired_jobs),
            },
        )
        return SyncResult({"pulled": 0, "completed": 0, "waiting": 0, "failed": 0, "skipped": 0, "expired": len(expired_jobs)}, 0)

    logger.info(
        "hades_backend.sync.start",
        extra={
            "hades_event": "sync.start",
            "hades_agent_id": agent.agent_id,
            "hades_project_id": agent.project_id,
            "hades_binding_count": len(bindings),
            "hades_expired_jobs": len(expired_jobs),
        },
    )

    try:
        client = client_factory() if client_factory is not None else runtime.client_from_config()
    except Exception as exc:
        logger.warning(
            "hades_backend.sync.client_error",
            extra={
                "hades_event": "sync.client_error",
                "hades_agent_id": agent.agent_id,
                "hades_project_id": agent.project_id,
                "hades_error": redact_secret(str(exc)),
            },
        )
        return SyncResult({"error": 1}, 1)

    pulled = completed = waiting = failed = skipped = 0
    memory_snapshots = proposals_synced = proposal_errors = 0
    artifacts_uploaded = artifact_errors = source_slices_uploaded = source_slice_errors = inbox_events = 0
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
                    slices_uploaded, slices_failed = _upload_job_source_slice(client, agent, binding, jid, result)
                    artifacts_uploaded += uploaded
                    artifact_errors += upload_failed
                    source_slices_uploaded += slices_uploaded
                    source_slice_errors += slices_failed
                    sync_errors += upload_failed
                    sync_errors += slices_failed
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
        "source_slices_uploaded": source_slices_uploaded,
        "source_slice_errors": source_slice_errors,
        "inbox_events": inbox_events,
    }
    with db.connect_closing() as conn:
        db.record_sync_state(conn, "last_sync_summary", summary)
        if sync_errors == 0:
            db.clear_sync_state(conn, "last_sync_error")
            db.clear_sync_state(conn, BACKGROUND_SYNC_STATE_KEY)

    logger.info(
        "hades_backend.sync.complete",
        extra={
            "hades_event": "sync.complete",
            "hades_agent_id": agent.agent_id,
            "hades_project_id": agent.project_id,
            "hades_exit_code": 1 if sync_errors else 0,
            "hades_summary": summary,
        },
    )
    return SyncResult(summary, 1 if sync_errors else 0)


def maybe_run_backend_sync(
    *,
    now: int | None = None,
    min_interval_seconds: int = 300,
    failure_base_delay_seconds: int = 60,
    max_backoff_seconds: int = 3600,
    force: bool = False,
    run_inline: bool = False,
    client_factory: Callable[[], object] | None = None,
    sync_runner: Callable[..., SyncResult] = run_backend_sync,
) -> BackgroundSyncDecision:
    """Start a bounded piggyback sync if the profile is linked and due."""
    current = int(now if now is not None else time.time())
    if not db.hades_backend_db_path().exists():
        return BackgroundSyncDecision("skipped", "not_configured")

    with db.connect_closing() as conn:
        agent = db.get_default_agent(conn)
        bindings = db.list_workspace_bindings(conn, status="linked") if agent else []
        state = db.get_sync_state(conn, BACKGROUND_SYNC_STATE_KEY) or {}
    if agent is None or not bindings:
        return BackgroundSyncDecision("skipped", "not_configured")

    if not force:
        next_attempt = _as_int(state.get("next_attempt_at"))
        if next_attempt and current < next_attempt:
            return BackgroundSyncDecision("skipped", "backoff")
        last_attempt = _as_int(state.get("last_attempt_at"))
        if last_attempt and current - last_attempt < max(0, int(min_interval_seconds)):
            return BackgroundSyncDecision("skipped", "interval")

    global _BACKGROUND_SYNC_RUNNING
    with _BACKGROUND_SYNC_LOCK:
        if _BACKGROUND_SYNC_RUNNING:
            return BackgroundSyncDecision("skipped", "already_running")
        _BACKGROUND_SYNC_RUNNING = True

    _record_background_sync_state(
        {
            "status": "running",
            "last_attempt_at": current,
            "failure_count": _as_int(state.get("failure_count")),
            "next_attempt_at": current + max(0, int(min_interval_seconds)),
        }
    )

    if run_inline:
        return _run_background_sync_once(
            started_at=current,
            previous_state=state,
            min_interval_seconds=min_interval_seconds,
            failure_base_delay_seconds=failure_base_delay_seconds,
            max_backoff_seconds=max_backoff_seconds,
            client_factory=client_factory,
            sync_runner=sync_runner,
        )

    thread = threading.Thread(
        target=_run_background_sync_once,
        kwargs={
            "started_at": current,
            "previous_state": state,
            "min_interval_seconds": min_interval_seconds,
            "failure_base_delay_seconds": failure_base_delay_seconds,
            "max_backoff_seconds": max_backoff_seconds,
            "client_factory": client_factory,
            "sync_runner": sync_runner,
        },
        name="hades-backend-sync",
        daemon=True,
    )
    thread.start()
    return BackgroundSyncDecision("started", "due")


def _run_background_sync_once(
    *,
    started_at: int,
    previous_state: dict,
    min_interval_seconds: int,
    failure_base_delay_seconds: int,
    max_backoff_seconds: int,
    client_factory: Callable[[], object] | None,
    sync_runner: Callable[..., SyncResult],
) -> BackgroundSyncDecision:
    global _BACKGROUND_SYNC_RUNNING
    try:
        kwargs: dict[str, object] = {"quiet": True}
        if client_factory is not None:
            kwargs["client_factory"] = client_factory
        result = sync_runner(**kwargs)
        finished_at = started_at
        if result.exit_code == 0:
            state = {
                "status": "ok",
                "last_attempt_at": started_at,
                "last_success_at": finished_at,
                "failure_count": 0,
                "next_attempt_at": finished_at + max(0, int(min_interval_seconds)),
                "summary": result.summary,
                "exit_code": result.exit_code,
            }
            _record_background_sync_state(state)
            return BackgroundSyncDecision("ran", "ok", result.summary)

        failure_count = _as_int(previous_state.get("failure_count")) + 1
        delay = min(
            max(0, int(max_backoff_seconds)),
            max(0, int(failure_base_delay_seconds)) * (2 ** max(0, failure_count - 1)),
        )
        state = {
            "status": "failed",
            "last_attempt_at": started_at,
            "last_success_at": previous_state.get("last_success_at"),
            "failure_count": failure_count,
            "next_attempt_at": finished_at + delay,
            "summary": result.summary,
            "exit_code": result.exit_code,
        }
        _record_background_sync_state(state)
        return BackgroundSyncDecision("ran", "failed", result.summary)
    finally:
        with _BACKGROUND_SYNC_LOCK:
            _BACKGROUND_SYNC_RUNNING = False


def _record_background_sync_state(value: dict) -> None:
    with db.connect_closing() as conn:
        db.record_sync_state(conn, BACKGROUND_SYNC_STATE_KEY, value)


def _as_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _record_sync_error(binding: db.WorkspaceBinding, message: str) -> None:
    from hermes_cli.hades_backend_client import redact_secret

    redacted = redact_secret(message)
    logger.warning(
        "hades_backend.sync.error",
        extra={
            "hades_event": "sync.error",
            "hades_project_id": binding.project_id,
            "hades_workspace_binding_id": binding.backend_workspace_binding_id,
            "hades_error": redacted,
        },
    )
    with db.connect_closing() as conn:
        db.record_sync_state(
            conn,
            "last_sync_error",
            {
                "workspace_binding_id": binding.backend_workspace_binding_id,
                "project_id": binding.project_id,
                "message": redacted,
            },
        )


def _upload_job_artifact(client: object, agent: db.BackendAgent, binding: db.WorkspaceBinding, job_id: str, result: dict) -> tuple[int, int]:
    artifact = result.get("artifact") if isinstance(result, dict) else None
    if not isinstance(artifact, dict):
        return (0, 0)
    schema = str(artifact.get("schema") or "").strip()
    if schema not in {"hades.git_tree.v1", "hades.symbols.v1", "hades.php_graph.v1"}:
        return (0, 0)
    artifact_payload = dict(artifact)
    head_commit = str(binding.head_commit or "").strip()
    if head_commit:
        artifact_payload.setdefault("head_commit", head_commit)
        artifact_payload.setdefault("indexed_head_commit", head_commit)
        artifact_payload.setdefault("workspace_head_commit", head_commit)
    try:
        client.upload_artifact(
            project_id=binding.project_id,
            agent_id=agent.agent_id,
            workspace_binding_id=binding.backend_workspace_binding_id,
            job_id=job_id,
            schema=schema,
            artifact=artifact_payload,
            truncated=bool(artifact_payload.get("truncated", False)),
            redactions=int(artifact_payload.get("redactions", 0) or 0),
        )
        logger.info(
            "hades_backend.artifact.uploaded",
            extra={
                "hades_event": "artifact.uploaded",
                "hades_project_id": binding.project_id,
                "hades_workspace_binding_id": binding.backend_workspace_binding_id,
                "hades_job_id": job_id,
                "hades_schema": schema,
                "hades_truncated": bool(artifact_payload.get("truncated", False)),
                "hades_redactions": int(artifact_payload.get("redactions", 0) or 0),
            },
        )
        return (1, 0)
    except AttributeError:
        return (0, 0)
    except Exception as exc:
        _record_sync_error(binding, f"artifact upload failed: {exc}")
        return (0, 1)


def _upload_job_source_slice(client: object, agent: db.BackendAgent, binding: db.WorkspaceBinding, job_id: str, result: dict) -> tuple[int, int]:
    source_slice = result.get("source_slice") if isinstance(result, dict) else None
    if not isinstance(source_slice, dict):
        return (0, 0)
    source_slice_payload = dict(source_slice)
    head_commit = str(binding.head_commit or "").strip()
    if head_commit:
        source_slice_payload.setdefault("head_commit", head_commit)
    try:
        client.create_source_slice(
            project_id=binding.project_id,
            agent_id=agent.agent_id,
            workspace_binding_id=binding.backend_workspace_binding_id,
            job_id=job_id,
            **source_slice_payload,
        )
        logger.info(
            "hades_backend.source_slice.uploaded",
            extra={
                "hades_event": "source_slice.uploaded",
                "hades_project_id": binding.project_id,
                "hades_workspace_binding_id": binding.backend_workspace_binding_id,
                "hades_job_id": job_id,
                "hades_path": source_slice_payload.get("path"),
                "hades_truncated": bool(source_slice_payload.get("truncated", False)),
                "hades_redactions": int(source_slice_payload.get("redactions", 0) or 0),
            },
        )
        return (1, 0)
    except AttributeError:
        return (0, 0)
    except Exception as exc:
        _record_sync_error(binding, f"source slice upload failed: {exc}")
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
