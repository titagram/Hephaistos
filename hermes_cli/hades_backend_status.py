"""Shared Hades backend status payloads for CLI and UI surfaces."""

from __future__ import annotations

from pathlib import Path
import re
import time
from typing import Any

from hermes_cli import hades_backend_db as db
from hermes_cli.config import load_config
from hermes_cli.hades_backend_client import redact_secret
from hermes_cli.hades_backend_sync import BACKGROUND_SYNC_STATE_KEY

ABSOLUTE_PATH_RE = re.compile(r"(?<![A-Za-z0-9:/])(?:/[^\s,;:]+)+")
WINDOWS_PATH_RE = re.compile(r"\b[A-Za-z]:\\[^\s,;:]+")
QUALITY_REPORT_STALE_SECONDS = 7 * 24 * 60 * 60
QUALITY_REPORT_HISTORY_KEY = "quality_report_history"
QUALITY_REPORT_HISTORY_LIMIT = 10


def load_backend_status_payload() -> dict[str, Any]:
    """Return the canonical local Hades backend status payload."""
    config = load_config()
    memory_config = config.get("memory") if isinstance(config.get("memory"), dict) else {}
    backend_config = config.get("backend") if isinstance(config.get("backend"), dict) else {}
    memory_provider = str(memory_config.get("provider") or "local").strip() or "local"
    plugin_local_workspace_id = str(backend_config.get("plugin_local_workspace_id") or "").strip()
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
            last_quality_report = db.get_sync_state(conn, "last_quality_report")
            quality_report_history = db.get_sync_state(conn, QUALITY_REPORT_HISTORY_KEY)
            plugin_work_items = [
                item
                for item in db.list_plugin_work_items(conn)
                if item.project_id == agent.project_id
            ]
            last_summary_updated_at = db.get_sync_state_updated_at(conn, "last_sync_summary")
            last_error_updated_at = db.get_sync_state_updated_at(conn, "last_sync_error")
            background_sync_updated_at = db.get_sync_state_updated_at(conn, BACKGROUND_SYNC_STATE_KEY)
            last_quality_report_updated_at = db.get_sync_state_updated_at(conn, "last_quality_report")
            quality_report_history_updated_at = db.get_sync_state_updated_at(conn, QUALITY_REPORT_HISTORY_KEY)
        else:
            bindings = []
            memory_caches = {}
            job_counts = {}
            proposal_counts = {}
            inbox_counts = {"total": 0, "unread": 0}
            last_summary = None
            last_error = None
            background_sync = None
            last_quality_report = None
            quality_report_history = None
            plugin_work_items = []
            last_summary_updated_at = None
            last_error_updated_at = None
            background_sync_updated_at = None
            last_quality_report_updated_at = None
            quality_report_history_updated_at = None

    remote_awarenesses = _load_remote_awarenesses(agent, bindings)
    return backend_status_payload(
        agent=agent,
        bindings=bindings,
        memory_caches=memory_caches,
        remote_awarenesses=remote_awarenesses,
        job_counts=job_counts,
        proposal_counts=proposal_counts,
        inbox_counts=inbox_counts,
        last_summary=last_summary,
        last_error=last_error,
        background_sync=background_sync,
        memory_provider=memory_provider,
        last_summary_updated_at=last_summary_updated_at,
        last_error_updated_at=last_error_updated_at,
        background_sync_updated_at=background_sync_updated_at,
        last_quality_report=last_quality_report,
        last_quality_report_updated_at=last_quality_report_updated_at,
        quality_report_history=quality_report_history,
        quality_report_history_updated_at=quality_report_history_updated_at,
        plugin_work_items=plugin_work_items,
        plugin_local_workspace_id=plugin_local_workspace_id,
    )


def support_report_payload(status: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a token-free, source-free report suitable for support tickets."""
    payload = status if status is not None else load_backend_status_payload()
    agent = payload.get("agent") if isinstance(payload.get("agent"), dict) else None
    bindings = payload.get("bindings") if isinstance(payload.get("bindings"), list) else []
    sync = payload.get("sync") if isinstance(payload.get("sync"), dict) else {}
    last_error = sync.get("last_error") if isinstance(sync.get("last_error"), dict) else None
    background = sync.get("background") if isinstance(sync.get("background"), dict) else None
    return {
        "schema": "hades.backend_support_report.v1",
        "configured": bool(payload.get("configured")),
        "degraded": bool(payload.get("degraded")),
        "agent": None if agent is None else {
            "project_id": agent.get("project_id"),
            "agent_id": agent.get("agent_id"),
            "label": agent.get("label"),
            "base_url": _safe_text(agent.get("base_url")),
            "capabilities": sorted((agent.get("capabilities") or {}).keys())
            if isinstance(agent.get("capabilities"), dict)
            else [],
        },
        "awareness": payload.get("awareness") if isinstance(payload.get("awareness"), dict) else {},
        "bindings": [_support_binding(binding) for binding in bindings if isinstance(binding, dict)],
        "job_counts": payload.get("job_counts") if isinstance(payload.get("job_counts"), dict) else {},
        "proposal_counts": payload.get("proposal_counts") if isinstance(payload.get("proposal_counts"), dict) else {},
        "inbox_counts": payload.get("inbox_counts") if isinstance(payload.get("inbox_counts"), dict) else {},
        "task_work": payload.get("task_work") if isinstance(payload.get("task_work"), dict) else {},
        "sync": {
            "last_summary": _numeric_summary(sync.get("last_summary")),
            "last_summary_updated_at": sync.get("last_summary_updated_at"),
            "last_error": None if last_error is None else {
                "present": True,
                "project_id": last_error.get("project_id"),
                "workspace_binding_id": last_error.get("workspace_binding_id"),
                "message": _safe_text(last_error.get("message")),
            },
            "last_error_updated_at": sync.get("last_error_updated_at"),
            "background": None if background is None else {
                "status": background.get("status"),
                "failure_count": background.get("failure_count"),
                "next_attempt_at": background.get("next_attempt_at"),
                "exit_code": background.get("exit_code"),
            },
        },
        "actions": [_safe_text(action) for action in payload.get("actions", []) if isinstance(action, str)],
    }


def _support_binding(binding: dict[str, Any]) -> dict[str, Any]:
    head_commit = str(binding.get("head_commit") or "")
    return {
        "workspace_binding_id": binding.get("workspace_binding_id"),
        "project_id": binding.get("project_id"),
        "local_project_id": binding.get("local_project_id"),
        "status": binding.get("status"),
        "head_commit_short": head_commit[:12] if head_commit else None,
        "display_path": _path_shape(binding.get("display_path")),
        "awareness": binding.get("awareness") if isinstance(binding.get("awareness"), dict) else {},
    }


def _numeric_summary(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, int] = {}
    for key, item in value.items():
        if isinstance(item, bool):
            continue
        if isinstance(item, int):
            result[str(key)] = item
    return result


def _path_shape(value: Any) -> dict[str, Any]:
    text = str(value or "")
    return {
        "present": bool(text),
        "kind": "home_relative" if text.startswith("~/") else ("absolute_redacted" if Path(text).is_absolute() else "relative_or_label"),
    }


def _safe_text(value: Any) -> str:
    redacted = redact_secret(value)
    redacted = WINDOWS_PATH_RE.sub("[path]", redacted)
    return ABSOLUTE_PATH_RE.sub("[path]", redacted)


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
    remote_awarenesses: dict[str, dict[str, Any]] | None = None,
    memory_provider: str = "local",
    background_sync: dict[str, Any] | None = None,
    last_summary_updated_at: int | None = None,
    last_error_updated_at: int | None = None,
    background_sync_updated_at: int | None = None,
    last_quality_report: dict[str, Any] | None = None,
    last_quality_report_updated_at: int | None = None,
    quality_report_history: dict[str, Any] | None = None,
    quality_report_history_updated_at: int | None = None,
    plugin_work_items: list[Any] | None = None,
    plugin_local_workspace_id: str = "",
    now: int | None = None,
) -> dict[str, Any]:
    current_time = int(now if now is not None else time.time())
    refused = _count(proposal_counts, "refused") + _count(proposal_counts, "conflicted")
    waiting = _count(job_counts, "waiting_confirmation")
    background_failed = bool(background_sync and background_sync.get("status") == "failed")
    quality_state = _quality_payload(
        last_quality_report,
        last_quality_report_updated_at,
        history=quality_report_history,
        history_updated_at=quality_report_history_updated_at,
        now=current_time,
    )
    quality_stale = bool(quality_state.get("staleness", {}).get("stale"))
    quality_missing = bool(quality_state.get("staleness", {}).get("missing"))
    quality_failed = bool(last_quality_report and last_quality_report.get("status") == "failed")
    quality_attention = bool(last_quality_report and last_quality_report.get("status") == "attention")
    task_work = _task_work_payload(
        plugin_work_items or [],
        project_id=getattr(agent, "project_id", None),
        plugin_local_workspace_id=plugin_local_workspace_id,
    )
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
            remote_awareness=(remote_awarenesses or {}).get(str(_binding_value(binding, "backend_workspace_binding_id"))),
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
    identity = _identity_payload(
        agent=agent,
        binding_payloads=binding_payloads,
        awareness=awareness,
        memory_provider=memory_provider,
    )
    if awareness["status"] in {"partial", "degraded", "unmapped"} and not last_error and not background_failed:
        actions.append("Project awareness is incomplete; inspect `awareness` before source-free diagnosis.")
    if quality_failed:
        actions.append("Review latest Hades quality report blocker(s).")
    elif quality_attention:
        actions.append("Review latest Hades quality report warning(s).")
    elif quality_missing:
        actions.append("Run `hades backend quality-report --record` to establish a governance baseline.")
    elif quality_stale:
        actions.append("Refresh stale Hades quality report with `hades backend quality-report --record`.")
    task_work_next_step = task_work.get("next_step")
    worker_setup = task_work.get("worker_setup") if isinstance(task_work.get("worker_setup"), dict) else {}
    if agent is not None and worker_setup.get("status") == "missing":
        actions.append("Run `hades backend worker-setup` in this checkout before claiming backend task work.")
    if task_work.get("failed"):
        actions.append("Inspect failed backend task work with `hades backend tasks status`.")
    if task_work.get("missing_shared_memory_context"):
        actions.append("Repair backend task work missing shared memory context before relying on agent output.")
    elif task_work.get("queued"):
        actions.append("Process queued backend task work with `hades backend tasks work --once`.")
    elif task_work_next_step and task_work.get("total"):
        actions.append(str(task_work_next_step))
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
        "identity": identity,
        "quality": quality_state,
        "job_counts": job_counts,
        "task_work": task_work,
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
        "degraded": bool(waiting or refused or last_error or background_failed or quality_failed or task_work.get("failed")),
        "actions": actions,
    }


def _task_work_payload(
    work_items: list[Any],
    *,
    project_id: str | None,
    plugin_local_workspace_id: str = "",
) -> dict[str, Any]:
    from hermes_cli.hades_quality_report import build_agent_work_quality_report

    quality = build_agent_work_quality_report(work_items)
    by_status = quality.get("by_status") if isinstance(quality.get("by_status"), dict) else {}
    queued = _nonnegative_int(by_status.get("queued"))
    claimed = _nonnegative_int(by_status.get("claimed")) + _nonnegative_int(by_status.get("running")) + _nonnegative_int(by_status.get("in_progress"))
    failed = _nonnegative_int(by_status.get("failed"))
    missing_shared_memory = _nonnegative_int(quality.get("missing_shared_memory_context_count"))

    next_step = "Run `hades backend tasks list` to refresh available backend work."
    if failed:
        next_step = "Run `hades backend tasks explain <work_item_id>` on failed items, then retry or update the kanban task."
    elif missing_shared_memory:
        next_step = "Run `hades backend quality-report --record` and repair work items missing memory_search_status."
    elif not plugin_local_workspace_id:
        next_step = "Run `hades backend worker-setup` in this checkout before claiming backend task work."
    elif queued:
        next_step = "Run `hades backend tasks work --once` to process queued backend work."
    elif int(quality.get("total") or 0):
        next_step = "Use `hades backend tasks explain <work_item_id>` for cached task details."

    return {
        "schema": "hades.backend_task_work_status.v1",
        "project_id": project_id,
        "total": int(quality.get("total") or 0),
        "queued": queued,
        "claimed": claimed,
        "failed": failed,
        "by_status": by_status,
        "shared_memory_required": int(quality.get("shared_memory_required_count") or 0),
        "shared_memory_context": int(quality.get("shared_memory_context_count") or 0),
        "missing_shared_memory_context": missing_shared_memory,
        "shared_memory_context_coverage": float(quality.get("shared_memory_context_coverage") or 0.0),
        "missing_work_item_ids": quality.get("missing_work_item_ids") if isinstance(quality.get("missing_work_item_ids"), list) else [],
        "worker_setup": {
            "status": "linked" if plugin_local_workspace_id else "missing",
            "local_workspace_id_present": bool(plugin_local_workspace_id),
            "next_step": "Run `hades backend worker-setup` in this checkout." if not plugin_local_workspace_id else "Worker setup is linked for this checkout.",
        },
        "next_step": next_step,
    }


def _quality_payload(
    report: dict[str, Any] | None,
    updated_at: int | None,
    *,
    history: dict[str, Any] | None = None,
    history_updated_at: int | None = None,
    now: int | None = None,
) -> dict[str, Any]:
    current_time = int(now if now is not None else time.time())
    age_seconds = max(0, current_time - int(updated_at or 0)) if updated_at else None
    staleness = {
        "missing": not isinstance(report, dict),
        "stale": bool(age_seconds is not None and age_seconds > QUALITY_REPORT_STALE_SECONDS),
        "age_seconds": age_seconds,
        "stale_after_seconds": QUALITY_REPORT_STALE_SECONDS,
    }
    history_payload = _quality_history_payload(history, history_updated_at)
    if not isinstance(report, dict):
        return {
            "last_report": None,
            "last_report_updated_at": None,
            "staleness": staleness,
            "history": history_payload,
        }
    action_queue = report.get("action_queue") if isinstance(report.get("action_queue"), list) else []
    return {
        "last_report": {
            "schema": report.get("schema"),
            "generated_at": report.get("generated_at"),
            "status": report.get("status"),
            "summary": report.get("summary") if isinstance(report.get("summary"), dict) else {},
            "metrics": report.get("metrics") if isinstance(report.get("metrics"), dict) else {},
            "action_queue": [action for action in action_queue if isinstance(action, dict)][:10],
        },
        "last_report_updated_at": updated_at,
        "staleness": staleness,
        "history": history_payload,
    }


def _quality_history_payload(history: dict[str, Any] | None, updated_at: int | None) -> dict[str, Any]:
    raw_entries = history.get("entries") if isinstance(history, dict) else []
    entries: list[dict[str, Any]] = []
    if isinstance(raw_entries, list):
        for raw in raw_entries[:QUALITY_REPORT_HISTORY_LIMIT]:
            if not isinstance(raw, dict):
                continue
            summary = raw.get("summary") if isinstance(raw.get("summary"), dict) else {}
            action_queue = raw.get("action_queue") if isinstance(raw.get("action_queue"), list) else []
            actions = [action for action in action_queue if isinstance(action, dict)][:5]
            action_ids = [str(action.get("id")) for action in actions if action.get("id")]
            entries.append(
                {
                    "generated_at": raw.get("generated_at"),
                    "recorded_at": raw.get("recorded_at"),
                    "status": raw.get("status"),
                    "summary": {
                        "blockers": _nonnegative_int(summary.get("blockers")),
                        "warnings": _nonnegative_int(summary.get("warnings")),
                        "actions": _nonnegative_int(summary.get("actions")),
                    },
                    "action_ids": action_ids,
                    "action_queue": actions,
                }
            )

    by_status: dict[str, int] = {}
    for entry in entries:
        status = str(entry.get("status") or "unknown")
        by_status[status] = by_status.get(status, 0) + 1
    latest_failure = next(
        (
            entry
            for entry in entries
            if entry.get("status") in {"failed", "attention"}
            or _nonnegative_int(entry.get("summary", {}).get("blockers")) > 0
            or _nonnegative_int(entry.get("summary", {}).get("warnings")) > 0
        ),
        None,
    )
    return {
        "schema": "hades.quality_report_history.v1",
        "updated_at": updated_at,
        "limit": QUALITY_REPORT_HISTORY_LIMIT,
        "total": len(entries),
        "by_status": by_status,
        "entries": entries,
        "latest_failure": latest_failure,
    }


def _nonnegative_int(value: Any) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)


def _identity_payload(
    *,
    agent: Any,
    binding_payloads: list[dict[str, Any]],
    awareness: dict[str, Any],
    memory_provider: str,
) -> dict[str, Any]:
    linked_bindings = [binding for binding in binding_payloads if binding.get("status") == "linked"]
    cached_items = sum(
        int(binding.get("awareness", {}).get("coverage", {}).get("memory_cache", {}).get("items") or 0)
        for binding in linked_bindings
    )
    current_binding = _current_binding_payload(linked_bindings)
    project_id = getattr(agent, "project_id", None) if agent is not None else None
    current_awareness = current_binding.get("awareness") if isinstance(current_binding, dict) else {}
    current_source_free_ready = bool(
        isinstance(current_awareness, dict)
        and current_awareness.get("diagnosable_without_source")
    )
    current_status = current_awareness.get("status") if isinstance(current_awareness, dict) else None
    return {
        "personal_memory": {
            "scope": "local_profile",
            "provider": memory_provider,
            "portable_between_devices": False,
        },
        "project_memory": {
            "scope": "backend_project",
            "provider": "hades_backend" if agent is not None else None,
            "project_id": project_id,
            "available": agent is not None,
            "cached_items": cached_items,
            "portable_between_devices": agent is not None,
        },
        "workspace_binding": {
            "scope": "local_workspace",
            "total_bindings": len(binding_payloads),
            "linked_bindings": len(linked_bindings),
            "current_workspace_binding_id": current_binding.get("workspace_binding_id") if current_binding else None,
            "current_display_path": current_binding.get("display_path") if current_binding else None,
            "current_status": current_status or "unmapped",
            "current_source_free_ready": current_source_free_ready,
            "source_free_ready": awareness.get("diagnosable_without_source_bindings", 0),
        },
        "login_recovery": {
            "can_use_project_memory_without_old_device": agent is not None,
            "current_workspace_mapped": current_binding is not None,
            "source_free_diagnosis_ready": current_source_free_ready,
            "requires_workspace_binding_for_indexing": not current_source_free_ready,
            "recommended_next_action": _identity_next_action(
                configured=agent is not None,
                current_binding=current_binding,
                current_source_free_ready=current_source_free_ready,
            ),
        },
    }


def _load_remote_awarenesses(agent: Any, bindings: list[Any]) -> dict[str, dict[str, Any]]:
    if agent is None or not bindings:
        return {}
    try:
        from hermes_cli import hades_backend_runtime as runtime

        client = runtime.client_from_config(timeout=5.0)
    except Exception:
        return {}

    awarenesses: dict[str, dict[str, Any]] = {}
    try:
        for binding in bindings:
            workspace_binding_id = str(_binding_value(binding, "backend_workspace_binding_id") or "")
            project_id = str(_binding_value(binding, "project_id") or "")
            if not workspace_binding_id or not project_id:
                continue
            try:
                payload = client.project_awareness_status(
                    project_id=project_id,
                    workspace_binding_id=workspace_binding_id,
                )
            except Exception:
                continue
            if isinstance(payload, dict):
                awarenesses[workspace_binding_id] = payload
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()
    return awarenesses


def _current_binding_payload(bindings: list[dict[str, Any]]) -> dict[str, Any] | None:
    cwd = Path.cwd().resolve()
    matches: list[tuple[int, dict[str, Any]]] = []
    with db.connect_closing() as conn:
        for binding in bindings:
            workspace_binding_id = str(binding.get("workspace_binding_id") or "")
            if not workspace_binding_id:
                continue
            stored = db.get_binding_for_backend_id(conn, workspace_binding_id)
            if stored is None:
                continue
            try:
                repo_root = Path(stored.repo_root).resolve()
                cwd.relative_to(repo_root)
            except (OSError, ValueError):
                continue
            matches.append((len(str(repo_root)), binding))
    matches.sort(key=lambda item: item[0], reverse=True)
    return matches[0][1] if matches else None


def _identity_next_action(
    *,
    configured: bool,
    current_binding: dict[str, Any] | None,
    current_source_free_ready: bool,
) -> str:
    if not configured:
        return "Run `hades backend bootstrap ...` with a project bootstrap token on this device."
    if current_binding is None:
        return "Link this workspace with `hades backend bootstrap ...` or `hades project link <project>`, then run `hades backend sync`."
    if not current_source_free_ready:
        return "Run `hades backend sync`, then capture current bug evidence and source slices before source-free diagnosis."
    return "Project memory and source-free diagnosis are ready on this device."


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
    remote_awareness: dict[str, Any] | None = None,
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
        remote_awareness=remote_awareness,
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
    remote_awareness: dict[str, Any] | None,
    last_summary: dict[str, Any] | None,
    last_error: dict[str, Any] | None,
    last_summary_updated_at: int | None,
    summary_scope: str,
) -> dict[str, Any]:
    if isinstance(remote_awareness, dict):
        return _remote_binding_awareness_payload(
            remote_awareness,
            memory_cache=memory_cache,
            last_error=last_error,
            last_summary_updated_at=last_summary_updated_at,
        )

    memory_items = len(getattr(memory_cache, "items", []) or [])
    artifacts_uploaded = _count(last_summary, "artifacts_uploaded")
    artifacts_skipped = _count(last_summary, "artifacts_skipped")
    artifact_items = artifacts_uploaded + artifacts_skipped
    artifact_errors = _count(last_summary, "artifact_errors")
    source_slices_uploaded = _count(last_summary, "source_slices_uploaded")
    source_slice_errors = _count(last_summary, "source_slice_errors")
    source_slice_candidates = _count(last_summary, "source_slice_candidates")
    source_slice_jobs_waiting = _count(last_summary, "source_slice_jobs_waiting")
    causal_packs_valid = _count(last_summary, "causal_packs_valid") + _count(last_summary, "causal_pack_valid")
    causal_packs_invalid = _count(last_summary, "causal_packs_invalid") + _count(last_summary, "causal_pack_invalid")
    causal_packs_missing = _count(last_summary, "causal_packs_missing_for_open_bugs")
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
    if artifact_items == 0 or not is_binding_scoped:
        missing.append("project_artifact_index")
    if source_slices_uploaded == 0 or not is_binding_scoped:
        missing.append("source_slice_index")
    if bug_evidence_items == 0 or not is_binding_scoped:
        missing.append("bug_evidence")

    has_errors = bool(last_error or artifact_errors or source_slice_errors or proposal_errors)
    diagnosable_without_source = bool(is_linked and not has_errors and not missing)
    quality_actions: list[str] = []
    if source_slice_jobs_waiting:
        quality_actions.append("approve_source_slice_jobs")
    elif "source_slice_index" in missing and is_linked:
        quality_actions.append("sync_source_slices")
    if causal_packs_missing:
        quality_actions.append("create_causal_pack")
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
                "status": _coverage_status(artifact_items, summary_scope),
                "uploaded_last_sync": artifacts_uploaded,
                "skipped_unchanged_last_sync": artifacts_skipped,
                "errors_last_sync": artifact_errors,
            },
            "source_slices": {
                "status": _coverage_status(source_slices_uploaded, summary_scope),
                "uploaded_last_sync": source_slices_uploaded,
                "errors_last_sync": source_slice_errors,
            },
            "source_slice_candidates": {
                "status": _source_slice_candidate_status(
                    candidates=source_slice_candidates,
                    waiting_jobs=source_slice_jobs_waiting,
                    summary_scope=summary_scope,
                ),
                "count": source_slice_candidates,
                "waiting_jobs": source_slice_jobs_waiting,
            },
            "bug_evidence": {
                "status": _coverage_status(bug_evidence_items, summary_scope, missing_status="unknown"),
                "items_last_sync": bug_evidence_items,
            },
            "causal_packs": {
                "status": _causal_pack_status(
                    valid=causal_packs_valid,
                    invalid=causal_packs_invalid,
                    missing_for_open_bugs=causal_packs_missing,
                    summary_scope=summary_scope,
                ),
                "valid": causal_packs_valid,
                "invalid": causal_packs_invalid,
                "missing_for_open_bugs": causal_packs_missing,
            },
        },
        "quality": {
            "confidence": "ready" if diagnosable_without_source else ("blocked" if has_errors or not is_linked else "incomplete"),
            "missing": missing,
            "actions": quality_actions,
            "summary_scope": summary_scope,
            "last_sync_summary_updated_at": last_summary_updated_at,
        },
    }


def _remote_binding_awareness_payload(
    remote_awareness: dict[str, Any],
    *,
    memory_cache: Any | None,
    last_error: dict[str, Any] | None,
    last_summary_updated_at: int | None,
) -> dict[str, Any]:
    coverage = remote_awareness.get("coverage") if isinstance(remote_awareness.get("coverage"), dict) else {}
    memory = coverage.get("memory") if isinstance(coverage.get("memory"), dict) else {}
    artifacts = coverage.get("artifacts") if isinstance(coverage.get("artifacts"), dict) else {}
    source_slices = coverage.get("source_slices") if isinstance(coverage.get("source_slices"), dict) else {}
    source_slice_candidates = (
        coverage.get("source_slice_candidates") if isinstance(coverage.get("source_slice_candidates"), dict) else {}
    )
    bug_evidence = coverage.get("bug_evidence") if isinstance(coverage.get("bug_evidence"), dict) else {}
    causal_packs = coverage.get("causal_packs") if isinstance(coverage.get("causal_packs"), dict) else {}
    code_graph = coverage.get("code_graph") if isinstance(coverage.get("code_graph"), dict) else {}

    memory_count = _remote_count(memory, "count", fallback=len(getattr(memory_cache, "items", []) or []))
    artifact_count = _remote_count(artifacts, "count")
    source_slice_count = _remote_count(source_slices, "count")
    bug_evidence_count = _remote_count(bug_evidence, "count")
    waiting_jobs = _remote_count(source_slice_candidates, "waiting_jobs")
    candidate_count = _remote_count(source_slice_candidates, "count")
    causal_packs_valid = _remote_count(causal_packs, "valid")
    causal_packs_invalid = _remote_count(causal_packs, "invalid")
    causal_packs_missing = _remote_count(causal_packs, "missing_for_open_bugs")

    missing: list[str] = []
    if memory_count == 0:
        missing.append("shared_memory_cache")
    if _remote_status(artifacts) not in {"present", "current"}:
        missing.append("project_artifact_index")
    if _remote_status(source_slices) != "current":
        missing.append("source_slice_index")
    if _remote_status(bug_evidence) != "current":
        missing.append("bug_evidence")
    if _remote_status(code_graph) != "current":
        missing.append("code_graph")

    has_errors = bool(last_error)
    diagnosable_without_source = bool(remote_awareness.get("diagnosable_without_source")) and not has_errors
    quality_actions: list[str] = []
    if waiting_jobs:
        quality_actions.append("approve_source_slice_jobs")
    elif "source_slice_index" in missing:
        quality_actions.append("sync_source_slices")
    if causal_packs_missing:
        quality_actions.append("create_causal_pack")

    status = str(remote_awareness.get("overall_status") or "").strip()
    if has_errors:
        status = "degraded"
    elif diagnosable_without_source:
        status = "ready"
    elif status in {"missing_index", "stale"}:
        status = "partial"
    elif status not in {"ready", "partial", "degraded", "unlinked"}:
        status = "partial"

    return {
        "status": status,
        "diagnosable_without_source": diagnosable_without_source,
        "coverage": {
            "memory_cache": {
                "status": "present" if memory_count else "missing",
                "items": memory_count,
                "version": getattr(memory_cache, "version", None),
                "updated_at": memory.get("updated_at") or getattr(memory_cache, "updated_at", None),
            },
            "project_artifacts": {
                "status": _remote_status(artifacts),
                "count": artifact_count,
                "schemas": artifacts.get("schemas") if isinstance(artifacts.get("schemas"), dict) else {},
                "latest_schema": artifacts.get("latest_schema"),
                "updated_at": artifacts.get("updated_at"),
                "errors_last_sync": 0,
            },
            "source_slices": {
                "status": _remote_status(source_slices),
                "count": source_slice_count,
                "updated_at": source_slices.get("updated_at"),
                "errors_last_sync": 0,
            },
            "source_slice_candidates": {
                "status": str(source_slice_candidates.get("status") or "none"),
                "count": candidate_count,
                "waiting_jobs": waiting_jobs,
            },
            "bug_evidence": {
                "status": _remote_status(bug_evidence, missing_status="unknown"),
                "count": bug_evidence_count,
                "items_last_sync": bug_evidence_count,
            },
            "causal_packs": {
                "status": str(causal_packs.get("status") or "none"),
                "valid": causal_packs_valid,
                "invalid": causal_packs_invalid,
                "missing_for_open_bugs": causal_packs_missing,
            },
            "code_graph": {
                "status": _remote_status(code_graph),
                "count": _remote_count(code_graph, "count"),
                "schema": code_graph.get("schema"),
                "coverage_type": code_graph.get("coverage_type"),
            },
        },
        "quality": {
            "confidence": "ready" if diagnosable_without_source else ("blocked" if has_errors else "incomplete"),
            "missing": missing,
            "actions": quality_actions,
            "summary_scope": "backend",
            "last_sync_summary_updated_at": last_summary_updated_at,
            "backend_actions": [
                action for action in remote_awareness.get("actions", []) if isinstance(action, str)
            ][:10],
        },
    }


def _remote_status(payload: dict[str, Any], *, missing_status: str = "missing") -> str:
    status = str(payload.get("status") or "").strip()
    if status == "current":
        return "current"
    if status in {"present", "pending", "partial", "stale", "unknown", "ready", "none"}:
        return status
    if status == "missing":
        return missing_status
    return missing_status


def _remote_count(payload: dict[str, Any], key: str, *, fallback: int = 0) -> int:
    try:
        parsed = int(payload.get(key, fallback) or 0)
    except (TypeError, ValueError):
        return max(0, fallback)
    return max(0, parsed)


def _coverage_status(count: int, summary_scope: str, *, missing_status: str = "missing") -> str:
    if count <= 0:
        return missing_status
    if summary_scope == "binding":
        return "present"
    return "aggregate"


def _source_slice_candidate_status(*, candidates: int, waiting_jobs: int, summary_scope: str) -> str:
    if waiting_jobs > 0:
        return "pending"
    if candidates <= 0:
        return "none"
    if summary_scope == "binding":
        return "present"
    return "aggregate"


def _causal_pack_status(*, valid: int, invalid: int, missing_for_open_bugs: int, summary_scope: str) -> str:
    if summary_scope != "binding":
        return "unknown"
    if missing_for_open_bugs > 0:
        return "partial" if valid > 0 else "missing"
    if valid > 0:
        return "ready"
    if invalid > 0:
        return "invalid"
    return "none"


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
