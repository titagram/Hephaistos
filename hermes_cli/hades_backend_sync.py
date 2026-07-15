"""Reusable Hades backend sync runner."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import gzip
import hashlib
import json
import logging
from pathlib import Path
import re
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
ARTIFACT_UPLOAD_CACHE_PREFIX = "artifact_upload_cache"
ARTIFACT_COMPRESSION_MIN_BYTES = 64 * 1024
ORGANISM_GRAPH_SCHEMA = "hades.organism_graph.v1"
_BACKGROUND_SYNC_LOCK = threading.Lock()
_BACKGROUND_SYNC_RUNNING = False


def _credential_fingerprint(secret: str) -> str | None:
    value = str(secret or "")
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if value else None


def _persisted_credential_fingerprint(agent: db.BackendAgent) -> str | None:
    from hermes_cli.config import load_env

    return _credential_fingerprint(load_env().get(agent.token_env_key, ""))


def run_backend_sync(
    *,
    client_factory: Callable[[], object] | None = None,
    now: int | None = None,
    quiet: bool = False,
    project_id: str | None = None,
    workspace_binding_ids: list[str] | tuple[str, ...] | None = None,
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
    from hermes_cli.hades_information_worker import execute_stored_information_request
    from hermes_cli.hades_persephone_messages import BACKEND_CAPABILITY
    from hermes_cli.hades_persephone_receiver import PersephoneReceiver
    from hermes_cli.hades_persephone_store import get_cursor

    with db.connect_closing() as conn:
        agent = db.get_default_agent(conn)
        bindings = db.list_workspace_bindings(conn, status="linked") if agent else []
        bindings = _filter_sync_bindings(
            bindings,
            project_id=project_id,
            workspace_binding_ids=workspace_binding_ids,
        )
        agents = {
            binding.agent_id: loaded
            for binding in bindings
            if (loaded := db.get_agent(conn, binding.agent_id)) is not None
        }
        expired_jobs = db.expire_waiting_jobs(
            conn,
            now=now,
            project_id=project_id,
            workspace_binding_ids=[binding.backend_workspace_binding_id for binding in bindings],
        ) if agent and bindings else []

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
    started_monotonic = time.monotonic()

    clients: dict[str, object] = {}
    queue_capabilities: dict[str, bool] = {}
    advertised_capabilities: dict[str, dict[str, object]] = {}
    polled_agent_queues: set[tuple[str, str]] = set()
    route_auth: dict[tuple[str, str], dict[str, bool | int]] = {}
    used_credential_fingerprints: dict[str, str | None] = {}
    receiver = PersephoneReceiver(
        information_executor=execute_stored_information_request,
        now=(lambda: int(time.time()) if now is None else int(now)),
    )
    receiver.refresh_bindings(bindings, agents=agents)

    def client_for_agent(sync_agent: db.BackendAgent) -> object:
        if sync_agent.agent_id not in used_credential_fingerprints:
            used_credential_fingerprints[sync_agent.agent_id] = (
                _credential_fingerprint(runtime.agent_token(sync_agent))
            )
        if client_factory is not None:
            return client_factory()
        existing = clients.get(sync_agent.agent_id)
        if existing is not None:
            return existing
        if sync_agent.agent_id == agent.agent_id and sync_agent.project_id == agent.project_id:
            created = runtime.client_from_config()
        else:
            created = runtime.client_for_agent(sync_agent)
        clients[sync_agent.agent_id] = created
        return created

    pulled = completed = waiting = failed = skipped = 0
    memory_snapshots = proposals_synced = proposal_errors = 0
    artifacts_uploaded = artifact_errors = artifacts_skipped = source_slices_uploaded = source_slice_errors = inbox_events = 0
    source_slice_candidates = source_slice_jobs_waiting = 0
    sync_errors = 0
    expired = 0

    for job in expired_jobs:
        with db.connect_closing() as conn:
            binding = db.get_binding_for_backend_id(conn, job.workspace_binding_id)
        if binding is None:
            continue
        binding_agent = agents.get(binding.agent_id)
        if binding_agent is None:
            sync_errors += 1
            _record_sync_error(binding, f"missing local backend agent for {binding.agent_id}")
            continue
        try:
            client = client_for_agent(binding_agent)
            client.update_job_status(
                job.job_id,
                **_status_payload(binding_agent, binding, "expired", reason="deadline_expired"),
            )
        except Exception as exc:
            sync_errors += 1
            _record_sync_error(binding, str(exc))
        expired += 1

    for binding in bindings:
        binding_agent = agents.get(binding.agent_id)
        if binding_agent is None:
            sync_errors += 1
            _record_sync_error(binding, f"missing local backend agent for {binding.agent_id}")
            continue

        route_key = (binding.project_id, binding_agent.agent_id)
        auth_observation = route_auth.setdefault(
            route_key,
            {"success": False, "unauthorized": False, "unauthorized_errors": 0},
        )

        try:
            client = client_for_agent(binding_agent)
        except Exception as exc:
            if _is_unauthorized_error(exc):
                auth_observation["unauthorized"] = True
                auth_observation["unauthorized_errors"] += 1
            sync_errors += 1
            _record_sync_error(binding, str(exc))
            if not quiet:
                print(f"backend sync: failed to configure client for {binding.display_path}: {redact_secret(str(exc))}")
            continue

        if binding_agent.agent_id not in queue_capabilities:
            try:
                advertised = client.capabilities()
                auth_observation["success"] = True
            except Exception as exc:
                if _is_unauthorized_error(exc):
                    auth_observation["unauthorized"] = True
                    auth_observation["unauthorized_errors"] += 1
                advertised = {}
            advertised_capabilities[binding_agent.agent_id] = (
                advertised if isinstance(advertised, dict) else {}
            )
            queue_capabilities[binding_agent.agent_id] = bool(
                isinstance(advertised, dict)
                and advertised.get(BACKEND_CAPABILITY) is True
            )
        queue_supported = queue_capabilities[binding_agent.agent_id]
        receiver.set_queue_capability(
            project_id=binding.project_id,
            agent_id=binding_agent.agent_id,
            supported=queue_supported,
        )

        try:
            snapshots, synced, errors = _sync_memory(client, binding)
            auth_observation["success"] = True
            memory_snapshots += snapshots
            proposals_synced += synced
            proposal_errors += errors
            sync_errors += errors
        except Exception as exc:
            if _is_unauthorized_error(exc):
                auth_observation["unauthorized"] = True
                auth_observation["unauthorized_errors"] += 1
            sync_errors += 1
            _record_sync_error(binding, str(exc))
            if not quiet:
                print(f"backend sync: failed to sync memory for {binding.display_path}: {redact_secret(str(exc))}")

        queue_key = (binding.project_id, binding_agent.agent_id)
        if not queue_supported or queue_key not in polled_agent_queues:
            try:
                inbox_params = {"project_id": binding.project_id}
                if queue_supported:
                    inbox_params["target_agent_id"] = binding_agent.agent_id
                    with db.connect_closing() as conn:
                        cursor = get_cursor(
                            conn,
                            project_id=binding.project_id,
                            target_agent_id=binding_agent.agent_id,
                        )
                    if cursor:
                        inbox_params["cursor"] = cursor
                    inbox_params["limit"] = receiver.batch_size
                    polled_agent_queues.add(queue_key)
                inbox = client.list_inbox(**inbox_params)
                auth_observation["success"] = True
                saved = _sync_inbox(
                    inbox,
                    binding.project_id,
                    receiver=receiver if queue_supported else None,
                    target_agent_id=(
                        binding_agent.agent_id if queue_supported else None
                    ),
                )
                inbox_events += saved
            except AttributeError:
                pass
            except Exception as exc:
                if _is_unauthorized_error(exc):
                    auth_observation["unauthorized"] = True
                    auth_observation["unauthorized_errors"] += 1
                sync_errors += 1
                _record_sync_error(binding, str(exc))
                if not quiet:
                    print(f"backend sync: failed to poll Persephone inbox for {binding.display_path}: {redact_secret(str(exc))}")

        try:
            response = client.pull_jobs(
                project_id=binding.project_id,
                agent_id=binding_agent.agent_id,
                workspace_binding_id=binding.backend_workspace_binding_id,
                capabilities=_detect_default_capabilities(),
            )
            auth_observation["success"] = True
        except Exception as exc:
            if _is_unauthorized_error(exc):
                auth_observation["unauthorized"] = True
                auth_observation["unauthorized_errors"] += 1
            sync_errors += 1
            _record_sync_error(binding, str(exc))
            if not quiet:
                print(f"backend sync: failed to pull jobs for {binding.display_path}: {redact_secret(str(exc))}")
            continue

        binding_pulled = 0
        for job in _response_jobs(response):
            jid = _job_id(job)
            capability = _job_capability(job)
            payload = _job_payload(job)
            if not jid:
                skipped += 1
                continue
            pulled += 1
            binding_pulled += 1

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
                client.update_job_status(jid, **_status_payload(binding_agent, binding, "received"))
                if capability not in AUTO_JOB_CAPABILITIES or _requires_confirmation(job):
                    with db.connect_closing() as conn:
                        db.update_job_status(conn, jid, "waiting_confirmation")
                    client.update_job_status(
                        jid,
                        **_status_payload(
                            binding_agent,
                            binding,
                            "waiting_confirmation",
                            reason="local_confirmation_required",
                        ),
                    )
                    waiting += 1
                    if capability == "read_source_slice":
                        source_slice_jobs_waiting += 1
                    continue

                with db.connect_closing() as conn:
                    db.update_job_status(conn, jid, "started")
                client.update_job_status(jid, **_status_payload(binding_agent, binding, "started"))

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
                    uploaded, upload_failed, upload_skipped = _upload_job_artifact(client, binding_agent, binding, jid, result)
                    slices_uploaded, slices_failed = _upload_job_source_slice(client, binding_agent, binding, jid, result)
                    artifact = result.get("artifact") if isinstance(result, dict) else None
                    candidates = artifact.get("source_slice_candidates") if isinstance(artifact, dict) else None
                    if isinstance(candidates, list):
                        source_slice_candidates += len(candidates)
                    artifacts_uploaded += uploaded
                    artifact_errors += upload_failed
                    artifacts_skipped += upload_skipped
                    source_slices_uploaded += slices_uploaded
                    source_slice_errors += slices_failed
                    sync_errors += upload_failed
                    sync_errors += slices_failed
                    client.submit_job_result(jid, **_status_payload(binding_agent, binding, final_status, result=result))
                    completed += 1
                else:
                    client.update_job_status(
                        jid,
                        **_status_payload(binding_agent, binding, final_status, error=redact_secret(result.get("summary", ""))),
                    )
                    failed += 1
            except Exception as exc:
                result = {"status": "failed", "summary": redact_secret(str(exc))}
                with db.connect_closing() as conn:
                    db.update_job_status(conn, jid, "failed", result=result)
                try:
                    client.update_job_status(jid, **_status_payload(binding_agent, binding, "failed", error=result["summary"]))
                except Exception:
                    pass
                failed += 1

        if binding_pulled == 0:
            try:
                (
                    baseline_uploaded,
                    baseline_failed,
                    baseline_skipped,
                    baseline_candidates,
                ) = _sync_baseline_artifacts(client, binding_agent, binding, execute_job=execute_job)
                artifacts_uploaded += baseline_uploaded
                artifact_errors += baseline_failed
                artifacts_skipped += baseline_skipped
                source_slice_candidates += baseline_candidates
                sync_errors += baseline_failed
            except Exception as exc:
                sync_errors += 1
                _record_sync_error(binding, str(exc))
                if not quiet:
                    print(
                        f"backend sync: failed to upload baseline artifacts for {binding.display_path}: "
                        f"{redact_secret(str(exc))}"
                    )

        if _supports_organism_graph(
            advertised_capabilities.get(binding_agent.agent_id, {})
        ):
            organism_uploaded, organism_failed, organism_skipped = (
                _sync_current_organism_artifact(client, binding_agent, binding)
            )
            artifacts_uploaded += organism_uploaded
            artifact_errors += organism_failed
            artifacts_skipped += organism_skipped
            sync_errors += organism_failed

    with db.connect_closing() as conn:
        active_binding_ids = {
            binding.backend_workspace_binding_id for binding in bindings
        }
        source_slice_jobs_waiting = max(
            source_slice_jobs_waiting,
            sum(
                1
                for job in db.list_jobs(conn, statuses=["waiting_confirmation"])
                if job.capability == "read_source_slice"
                and job.workspace_binding_id in active_binding_ids
            ),
        )

    auth_failed_routes = auth_quarantined_routes = stale_auth_routes = 0
    with db.connect_closing() as conn:
        for (route_project_id, route_agent_id), observation in route_auth.items():
            unauthorized_cycle = bool(
                observation["unauthorized"] and not observation["success"]
            )
            route_agent = agents.get(route_agent_id)
            used_fingerprint = used_credential_fingerprints.get(route_agent_id)
            persisted_fingerprint = (
                _persisted_credential_fingerprint(route_agent)
                if route_agent is not None
                else None
            )
            stale_credential = bool(
                unauthorized_cycle
                and used_fingerprint
                and persisted_fingerprint
                and used_fingerprint != persisted_fingerprint
            )
            if stale_credential:
                stale_auth_routes += 1
                sync_errors = max(
                    0,
                    sync_errors - int(observation["unauthorized_errors"]),
                )
                continue
            if unauthorized_cycle:
                auth_failed_routes += 1
            elif not observation["success"]:
                continue
            outcome = db.record_route_auth_cycle(
                conn,
                project_id=route_project_id,
                agent_id=route_agent_id,
                unauthorized=unauthorized_cycle,
                now=now,
            )
            if outcome["quarantined"]:
                auth_quarantined_routes += 1

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
        "artifacts_skipped": artifacts_skipped,
        "artifact_errors": artifact_errors,
        "source_slices_uploaded": source_slices_uploaded,
        "source_slice_errors": source_slice_errors,
        "source_slice_candidates": source_slice_candidates,
        "source_slice_jobs_waiting": source_slice_jobs_waiting,
        "inbox_events": inbox_events,
        "auth_failed_routes": auth_failed_routes,
        "auth_quarantined_routes": auth_quarantined_routes,
        "stale_auth_routes": stale_auth_routes,
        "duration_ms": max(0, int((time.monotonic() - started_monotonic) * 1000)),
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
    for client in clients.values():
        close = getattr(client, "close", None)
        if callable(close):
            close()
    return SyncResult(summary, 1 if sync_errors else 0)


def _sync_baseline_artifacts(
    client: object,
    agent: db.BackendAgent,
    binding: db.WorkspaceBinding,
    *,
    execute_job: Callable[..., dict[str, object]],
) -> tuple[int, int, int, int]:
    uploaded = failed = skipped = source_slice_candidates = 0
    head_commit = str(binding.head_commit or "").strip()
    for capability in ("sync_git_tree", "populate_backend_ast"):
        if not _agent_has_capability(agent, capability):
            continue
        payload: dict[str, object] = {
            "head_commit": head_commit,
            "workspace_head_commit": head_commit,
            "max_source_slice_candidates": 25,
        }
        result = execute_job(
            {"job_id": None, "capability": capability, "payload": payload},
            workspace_root=binding.repo_root,
        )
        final_status = str(result.get("status") or "completed")
        if final_status != "completed":
            failed += 1
            continue
        artifact = result.get("artifact") if isinstance(result, dict) else None
        candidates = artifact.get("source_slice_candidates") if isinstance(artifact, dict) else None
        if isinstance(candidates, list):
            source_slice_candidates += len(candidates)
        artifact_uploaded, artifact_failed, artifact_skipped = _upload_job_artifact(
            client,
            agent,
            binding,
            None,
            result,
        )
        uploaded += artifact_uploaded
        failed += artifact_failed
        skipped += artifact_skipped
    return uploaded, failed, skipped, source_slice_candidates


def _supports_organism_graph(advertised: dict[str, object]) -> bool:
    if advertised.get("organism_graph_schema") == ORGANISM_GRAPH_SCHEMA:
        return True
    scopes = advertised.get("graph_scopes")
    return isinstance(scopes, list) and "organism" in scopes


def _sync_current_organism_artifact(
    client: object,
    agent: db.BackendAgent,
    binding: db.WorkspaceBinding,
) -> tuple[int, int, int]:
    """Upload the current revision when it belongs to this binding.

    Sync is intentionally non-constructive: absence or a workspace mismatch is
    a clean skip and never triggers an implicit organism rebuild.
    """
    from hermes_cli.gnothi.store import OrganismRevisionStore

    try:
        artifact = OrganismRevisionStore().current()
    except (OSError, ValueError):
        return (0, 1, 0)
    if not artifact or not _organism_artifact_matches_binding(artifact, binding):
        return (0, 0, 0)
    return _upload_job_artifact(
        client,
        agent,
        binding,
        None,
        {"artifact": artifact},
    )


def _organism_artifact_matches_binding(
    artifact: dict[str, object],
    binding: db.WorkspaceBinding,
) -> bool:
    contract = artifact.get("organism_contract")
    if not isinstance(contract, dict):
        return False
    source = contract.get("source")
    artifact_head = str(source.get("head_commit") or "") if isinstance(source, dict) else ""
    binding_head = str(binding.head_commit or "")
    if artifact_head and binding_head and artifact_head != binding_head:
        return False
    workspace_labels = {
        str(node.get("label") or "")
        for node in artifact.get("nodes", [])
        if isinstance(node, dict) and node.get("kind") == "workspace"
    }
    return not workspace_labels or Path(binding.repo_root).name in workspace_labels


def _agent_has_capability(agent: db.BackendAgent, capability: str) -> bool:
    capabilities = agent.capabilities if isinstance(agent.capabilities, dict) else {}
    if not capabilities:
        return True
    if capability in capabilities:
        return bool(capabilities.get(capability))
    if capability == "sync_git_tree":
        return bool(capabilities.get("artifacts", True))
    if capability == "populate_backend_ast":
        return bool(capabilities.get("populate_backend_ast", capabilities.get("artifacts", True)))
    return True


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
    project_id: str | None = None,
    workspace_binding_ids: list[str] | tuple[str, ...] | None = None,
) -> BackgroundSyncDecision:
    """Start a bounded piggyback sync if the profile is linked and due."""
    current = int(now if now is not None else time.time())
    if not db.hades_backend_db_path().exists():
        return BackgroundSyncDecision("skipped", "not_configured")

    with db.connect_closing() as conn:
        agent = db.get_default_agent(conn)
        bindings = db.list_workspace_bindings(conn, status="linked") if agent else []
        bindings = _filter_sync_bindings(
            bindings,
            project_id=project_id,
            workspace_binding_ids=workspace_binding_ids,
        )
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
            project_id=project_id,
            workspace_binding_ids=workspace_binding_ids,
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
            "project_id": project_id,
            "workspace_binding_ids": workspace_binding_ids,
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
    project_id: str | None = None,
    workspace_binding_ids: list[str] | tuple[str, ...] | None = None,
) -> BackgroundSyncDecision:
    global _BACKGROUND_SYNC_RUNNING
    try:
        kwargs: dict[str, object] = {"quiet": True}
        if client_factory is not None:
            kwargs["client_factory"] = client_factory
        if project_id is not None:
            kwargs["project_id"] = project_id
        if workspace_binding_ids is not None:
            kwargs["workspace_binding_ids"] = list(workspace_binding_ids)
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


def _filter_sync_bindings(
    bindings: list[db.WorkspaceBinding],
    *,
    project_id: str | None,
    workspace_binding_ids: list[str] | tuple[str, ...] | None,
) -> list[db.WorkspaceBinding]:
    clean_project_id = str(project_id or "").strip()
    clean_binding_ids = {
        str(binding_id).strip()
        for binding_id in (workspace_binding_ids or [])
        if str(binding_id or "").strip()
    }
    filtered: list[db.WorkspaceBinding] = []
    for binding in bindings:
        if clean_project_id and binding.project_id != clean_project_id:
            continue
        if clean_binding_ids and binding.backend_workspace_binding_id not in clean_binding_ids:
            continue
        filtered.append(binding)
    return filtered


def _binding_contains_path(binding: db.WorkspaceBinding, path: str | Path) -> bool:
    try:
        candidate = Path(path).expanduser().resolve()
        root = Path(binding.repo_root).expanduser().resolve()
        candidate.relative_to(root)
        return True
    except Exception:
        return False


def _matching_workspace_binding_ids(
    *,
    cwd: str | Path | None = None,
    changed_paths: list[str] | tuple[str, ...] | set[str] | None = None,
) -> list[str]:
    if not db.hades_backend_db_path().exists():
        return []
    probes: list[str | Path] = []
    if cwd:
        probes.append(cwd)
    probes.extend(str(path) for path in (changed_paths or []) if str(path or "").strip())
    if not probes:
        return []
    with db.connect_closing() as conn:
        agent = db.get_default_agent(conn)
        bindings = db.list_workspace_bindings(conn, status="linked") if agent else []
    if agent is None:
        return []
    bindings = [
        binding
        for binding in bindings
        if binding.agent_id == agent.agent_id and binding.project_id == agent.project_id
    ]
    matches: list[db.WorkspaceBinding] = []
    seen: set[str] = set()
    for binding in bindings:
        if any(_binding_contains_path(binding, probe) for probe in probes):
            binding_id = binding.backend_workspace_binding_id
            if binding_id in seen:
                continue
            seen.add(binding_id)
            matches.append(binding)
    # ``list_workspace_bindings`` returns newest first.  Python's stable sort
    # therefore preserves that ordering for duplicate bindings with the same
    # repository root while preferring the most specific containing root.
    matches.sort(key=lambda binding: len(str(Path(binding.repo_root).expanduser().resolve())), reverse=True)
    return [matches[0].backend_workspace_binding_id] if matches else []


def maybe_run_backend_sync_for_workspace(
    *,
    cwd: str | Path | None = None,
    changed_paths: list[str] | tuple[str, ...] | set[str] | None = None,
    force: bool = True,
    run_inline: bool = False,
    min_interval_seconds: int = 0,
    sync_runner: Callable[..., SyncResult] = run_backend_sync,
) -> BackgroundSyncDecision:
    """Start a scoped sync for the workspace touched by an agent turn."""
    binding_ids = _matching_workspace_binding_ids(cwd=cwd, changed_paths=changed_paths)
    if not binding_ids:
        return BackgroundSyncDecision("skipped", "no_matching_workspace")
    return maybe_run_backend_sync(
        force=force,
        run_inline=run_inline,
        min_interval_seconds=min_interval_seconds,
        workspace_binding_ids=binding_ids,
        sync_runner=sync_runner,
    )


def _as_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _is_unauthorized_error(exc: Exception) -> bool:
    from hermes_cli.hades_backend_client import HadesBackendError

    return isinstance(exc, HadesBackendError) and exc.status_code == 401


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


def _artifact_upload_cache_key(binding: db.WorkspaceBinding, schema: str) -> str:
    return f"{ARTIFACT_UPLOAD_CACHE_PREFIX}:{binding.backend_workspace_binding_id}:{schema}"


def _artifact_payload_hash(artifact_payload: dict[str, object]) -> str:
    encoded = json.dumps(artifact_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _artifact_upload_fields(artifact_payload: dict[str, object]) -> tuple[dict[str, object], dict[str, object]]:
    encoded = json.dumps(artifact_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(encoded) < ARTIFACT_COMPRESSION_MIN_BYTES:
        return {"artifact": artifact_payload}, {"compressed": False, "original_bytes": len(encoded), "compressed_bytes": 0}

    compressed = gzip.compress(encoded)
    if len(compressed) >= len(encoded):
        return {"artifact": artifact_payload}, {"compressed": False, "original_bytes": len(encoded), "compressed_bytes": len(compressed)}

    return (
        {
            "artifact_encoding": "gzip+base64",
            "artifact_compressed": base64.b64encode(compressed).decode("ascii"),
            "artifact_uncompressed_sha256": hashlib.sha256(encoded).hexdigest(),
            "artifact_uncompressed_bytes": len(encoded),
            "artifact_compressed_bytes": len(compressed),
        },
        {"compressed": True, "original_bytes": len(encoded), "compressed_bytes": len(compressed)},
    )


def _artifact_file_manifest(artifact_payload: dict[str, object]) -> dict[str, object]:
    path_items: dict[str, list[str]] = {}

    def add_item(item: object, *, allow_route_path: bool = False) -> None:
        if not isinstance(item, dict):
            return
        path = str(item.get("path") or item.get("source_path") or "").strip()
        path = path.replace("\\", "/")
        if (
            not path
            or (path.startswith("/") and not allow_route_path)
            or re.match(r"^[A-Za-z]:/", path)
            or any(part in {".", ".."} for part in path.split("/"))
        ):
            return
        if "sha256" in item:
            item_hash = str(item.get("sha256") or "")
        else:
            item_hash = _artifact_payload_hash(item)
        if not item_hash:
            return
        path_items.setdefault(path, []).append(item_hash)

    for item in artifact_payload.get("files") or []:
        add_item(item)
    for section in ("routes", "symbols", "edges"):
        for item in artifact_payload.get(section) or []:
            add_item(item, allow_route_path=section == "routes")
    for section in ("nodes", "relationships"):
        for item in artifact_payload.get(section) or []:
            if not isinstance(item, dict):
                continue
            properties = item.get("properties")
            if not isinstance(properties, dict):
                continue
            add_item({"path": properties.get("path"), "row": item})
    database = artifact_payload.get("database")
    if isinstance(database, dict):
        for table in database.get("tables") or []:
            add_item(table)
            if isinstance(table, dict):
                for item in table.get("columns") or []:
                    add_item(item)
                for item in table.get("foreign_keys") or []:
                    add_item(item)

    paths = {
        path: hashlib.sha256("".join(sorted(hashes)).encode("utf-8")).hexdigest()
        for path, hashes in sorted(path_items.items())
    }
    return {"paths": paths, "count": len(paths), "sha256": _artifact_payload_hash(paths)}


def _artifact_file_delta(cached_manifest: object, current_manifest: dict[str, object]) -> dict[str, object]:
    previous_paths = {}
    if isinstance(cached_manifest, dict) and isinstance(cached_manifest.get("paths"), dict):
        previous_paths = {str(path): str(value) for path, value in cached_manifest["paths"].items()}
    raw_current_paths = current_manifest.get("paths")
    current_paths = {str(path): str(value) for path, value in raw_current_paths.items()} if isinstance(raw_current_paths, dict) else {}
    added = sorted(path for path in current_paths if path not in previous_paths)
    removed = sorted(path for path in previous_paths if path not in current_paths)
    changed = sorted(path for path, value in current_paths.items() if previous_paths.get(path) not in {None, value})
    unchanged = sorted(path for path, value in current_paths.items() if previous_paths.get(path) == value)
    return {
        "added": len(added),
        "changed": len(changed),
        "removed": len(removed),
        "unchanged": len(unchanged),
        "added_paths": added[:100],
        "changed_paths": changed[:100],
        "removed_paths": removed[:100],
    }


def _upload_job_artifact(
    client: object,
    agent: db.BackendAgent,
    binding: db.WorkspaceBinding,
    job_id: str | None,
    result: dict,
) -> tuple[int, int, int]:
    artifact = result.get("artifact") if isinstance(result, dict) else None
    if not isinstance(artifact, dict):
        return (0, 0, 0)
    schema = str(artifact.get("schema") or "").strip()
    if schema not in {
        "hades.git_tree.v1",
        "hades.symbols.v1",
        "hades.php_graph.v1",
        "hades.code_graph.v1",
        ORGANISM_GRAPH_SCHEMA,
    }:
        return (0, 0, 0)
    artifact_payload = dict(artifact)
    head_commit = str(binding.head_commit or "").strip()
    if head_commit:
        artifact_payload.setdefault("head_commit", head_commit)
        artifact_payload.setdefault("indexed_head_commit", head_commit)
        artifact_payload.setdefault("workspace_head_commit", head_commit)
    payload_hash = _artifact_payload_hash(artifact_payload)
    file_manifest = _artifact_file_manifest(artifact_payload)
    cache_key = _artifact_upload_cache_key(binding, schema)
    with db.connect_closing() as conn:
        cached = db.get_sync_state(conn, cache_key) or {}
    file_delta = _artifact_file_delta(cached.get("file_manifest"), file_manifest)
    if (
        str(cached.get("sha256") or "") == payload_hash
        and str(cached.get("head_commit") or "") == head_commit
        and str(cached.get("schema") or "") == schema
    ):
        logger.info(
            "hades_backend.artifact.skipped",
            extra={
                "hades_event": "artifact.skipped",
                "hades_project_id": binding.project_id,
                "hades_workspace_binding_id": binding.backend_workspace_binding_id,
                "hades_job_id": job_id,
                "hades_schema": schema,
                "hades_reason": "unchanged",
                "hades_file_count": int(file_manifest.get("count") or 0),
            },
        )
        return (0, 0, 1)
    try:
        try:
            lookup = client.artifact_lookup(
                project_id=binding.project_id,
                agent_id=agent.agent_id,
                workspace_binding_id=binding.backend_workspace_binding_id,
                schema=schema,
                sha256=payload_hash,
            )
        except AttributeError:
            lookup = None
        except Exception:
            lookup = None
            logger.info(
                "hades_backend.artifact.lookup_unavailable",
                extra={
                    "hades_event": "artifact.lookup_unavailable",
                    "hades_project_id": binding.project_id,
                    "hades_workspace_binding_id": binding.backend_workspace_binding_id,
                    "hades_job_id": job_id,
                    "hades_schema": schema,
                },
            )
        if isinstance(lookup, dict) and lookup.get("exists") is True:
            artifact = lookup.get("artifact") if isinstance(lookup.get("artifact"), dict) else {}
            logger.info(
                "hades_backend.artifact.skipped",
                extra={
                    "hades_event": "artifact.skipped",
                    "hades_project_id": binding.project_id,
                    "hades_workspace_binding_id": binding.backend_workspace_binding_id,
                    "hades_job_id": job_id,
                    "hades_schema": schema,
                    "hades_reason": "unchanged_on_backend",
                    "hades_artifact_id": artifact.get("id"),
                    "hades_file_count": int(file_manifest.get("count") or 0),
                    "hades_file_delta": file_delta,
                },
            )
            with db.connect_closing() as conn:
                db.record_sync_state(
                    conn,
                    cache_key,
                    {
                        "schema": schema,
                        "sha256": payload_hash,
                        "head_commit": head_commit,
                        "job_id": job_id,
                        "backend_artifact_id": artifact.get("id"),
                        "backend_skip_reason": "unchanged_on_backend",
                        "file_manifest": file_manifest,
                        "file_delta": file_delta,
                    },
                )
            return (0, 0, 1)

        artifact_fields, compression = _artifact_upload_fields(artifact_payload)
        upload_payload = {
            "project_id": binding.project_id,
            "agent_id": agent.agent_id,
            "workspace_binding_id": binding.backend_workspace_binding_id,
            "job_id": job_id,
            "schema": schema,
            **artifact_fields,
            "sha256": payload_hash,
            "truncated": bool(artifact_payload.get("truncated", False)),
            "redactions": int(artifact_payload.get("redactions", 0) or 0),
        }
        try:
            client.upload_artifact(**upload_payload)
        except AttributeError:
            raise
        except Exception:
            if not compression.get("compressed"):
                raise
            upload_payload = {
                "project_id": binding.project_id,
                "agent_id": agent.agent_id,
                "workspace_binding_id": binding.backend_workspace_binding_id,
                "job_id": job_id,
                "schema": schema,
                "artifact": artifact_payload,
                "sha256": payload_hash,
                "truncated": bool(artifact_payload.get("truncated", False)),
                "redactions": int(artifact_payload.get("redactions", 0) or 0),
            }
            client.upload_artifact(**upload_payload)
            compression = {**compression, "fallback_raw": True}
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
                "hades_file_count": int(file_manifest.get("count") or 0),
                "hades_file_delta": file_delta,
                "hades_compressed": bool(compression.get("compressed")),
                "hades_compression_fallback_raw": bool(compression.get("fallback_raw")),
                "hades_original_bytes": int(compression.get("original_bytes") or 0),
                "hades_compressed_bytes": int(compression.get("compressed_bytes") or 0),
            },
        )
        with db.connect_closing() as conn:
            db.record_sync_state(
                conn,
                cache_key,
                {
                    "schema": schema,
                    "sha256": payload_hash,
                    "head_commit": head_commit,
                    "job_id": job_id,
                    "file_manifest": file_manifest,
                    "file_delta": file_delta,
                },
            )
        return (1, 0, 0)
    except AttributeError:
        return (0, 0, 0)
    except Exception as exc:
        _record_sync_error(binding, f"artifact upload failed: {exc}")
        return (0, 1, 0)


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


def _sync_inbox(
    response: dict,
    project_id: str,
    *,
    receiver: object | None = None,
    target_agent_id: str | None = None,
    workspace_binding_id: str | None = None,
) -> int:
    events = response.get("events") if isinstance(response, dict) else None
    if not isinstance(events, list):
        return 0
    saved = 0
    with db.connect_closing() as conn:
        for event in events:
            if not isinstance(event, dict):
                continue
            if (
                receiver is not None
                and getattr(receiver, "is_agent_event")(event)
            ):
                disposition = getattr(receiver, "ingest_event")(
                    event,
                    expected_project_id=project_id,
                    expected_target_agent_id=target_agent_id,
                    expected_workspace_binding_id=workspace_binding_id,
                )
                if disposition not in {"invalid_agent_message", "not_agent_message"}:
                    saved += 1
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
