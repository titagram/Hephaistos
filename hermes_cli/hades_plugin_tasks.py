"""Task-oriented helpers for Hades backend plugin work items."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import os
from pathlib import Path
import platform
import socket
import subprocess
from typing import Any

from hermes_cli import hades_backend_db as db
from hermes_cli.config import load_config, save_config
from hermes_cli.hades_backend_client import HadesBackendError, redact_secret
from hermes_cli.hades_plugin_worker import _prompt_from_work_item_payload
from hermes_cli import hades_backend_runtime as runtime


@dataclass(frozen=True)
class WorkerSetupResult:
    payload: dict[str, Any]
    exit_code: int


def setup_plugin_worker(
    *,
    workspace: str | Path | None = None,
    repository_id: str | None = None,
    client: object | None = None,
) -> WorkerSetupResult:
    """Register this checkout as a plugin local workspace and persist ids."""

    agent = runtime.current_agent()
    if agent is None:
        return WorkerSetupResult(
            _error(
                "not_configured",
                "Hades backend is not configured.",
                "Run `hades backend setup` or `hades backend bootstrap` first.",
            ),
            1,
        )

    root = Path(workspace or os.getcwd()).expanduser().resolve()
    selected_client = None
    try:
        selected_client = client or runtime.plugin_work_items_client_from_config()
        device = selected_client.register_device(
            name=runtime.default_agent_label(),
            fingerprint_hash=_device_fingerprint_hash(agent.agent_id),
            platform_os=platform.system().lower() or "unknown",
            platform_arch=platform.machine() or "unknown",
            plugin_version=_version(),
        )
        device_id = str(device.get("device_id") or "").strip()
        if not device_id:
            raise RuntimeError("device registration response did not include device_id")

        repo = _select_repository(
            selected_client.list_repositories(agent.project_id),
            repository_id=repository_id,
            workspace=root,
        )
        repo_id = str(repo["repository_id"])
        git_state = _git_state(root)
        registered = selected_client.register_local_workspace(
            repo_id,
            device_id=device_id,
            local_root_hash=_local_root_hash(root),
            display_path=runtime.display_path(root),
            current_branch=git_state["current_branch"],
            last_head_sha=git_state["last_head_sha"],
            dirty_status=git_state["dirty_status"],
            remote_name=git_state["remote_name"],
            remote_url_host=git_state["remote_url_host"],
            remote_url_hash=git_state["remote_url_hash"],
            upstream_branch=git_state["upstream_branch"],
            ahead_count=git_state["ahead_count"],
            behind_count=git_state["behind_count"],
            git_state_observed_at=datetime.now(UTC).isoformat(),
        )
    except Exception as exc:
        return WorkerSetupResult(
            _error(
                "worker_setup_failed",
                f"Failed to register plugin worker workspace: {redact_secret(str(exc))}",
                "Verify the plugin token, backend project, repository selection, and workspace path.",
            ),
            1,
        )
    finally:
        if client is None and selected_client is not None:
            _close_client(selected_client)

    local_workspace_id = str(registered.get("local_workspace_id") or "").strip()
    if not local_workspace_id:
        return WorkerSetupResult(
            _error(
                "missing_local_workspace_id",
                "Backend did not return local_workspace_id.",
                "Retry `hades backend worker-setup`; if it repeats, inspect backend plugin local workspace logs.",
            ),
            1,
        )

    cfg = load_config()
    backend = cfg.setdefault("backend", {})
    backend["plugin_device_id"] = device_id
    backend["plugin_repository_id"] = repo_id
    backend["plugin_local_workspace_id"] = local_workspace_id
    save_config(cfg)

    return WorkerSetupResult(
        {
            "status": "linked",
            "project_id": agent.project_id,
            "repository_id": repo_id,
            "repository_name": repo.get("name"),
            "device_id": device_id,
            "local_workspace_id": local_workspace_id,
            "display_path": runtime.display_path(root),
        },
        0,
    )


def list_plugin_tasks(
    *,
    project_id: str | None = None,
    repository_id: str | None = None,
    agent_key: str = "local_agent",
    status: str = "queued",
    limit: int = 20,
    client: object | None = None,
) -> dict[str, Any]:
    agent = runtime.current_agent()
    if agent is None:
        return _error(
            "not_configured",
            "Hades backend is not configured.",
            "Run `hades backend setup` or `hades backend bootstrap` first.",
        )

    selected_project_id = str(project_id or agent.project_id).strip()
    selected_client = None
    try:
        selected_client = client or runtime.plugin_work_items_client_from_config()
        response = selected_client.list_agent_work_items(
            project_id=selected_project_id,
            repository_id=repository_id,
            agent_key=agent_key,
            status=status,
            limit=max(1, int(limit or 20)),
        )
    except Exception as exc:
        code, message, next_step = _plugin_exception_error(
            exc,
            fallback_code="list_work_items_failed",
            fallback_message="Failed to list plugin work items",
            fallback_next_step="Check backend connectivity, plugin token scope, and project/workspace binding.",
        )
        return _error(
            code,
            message,
            next_step,
        )
    finally:
        if client is None and selected_client is not None:
            _close_client(selected_client)

    items = [_task_from_item(item) for item in _response_items(response)]
    _cache_items(items, selected_project_id=selected_project_id, local_workspace_id=runtime.plugin_local_workspace_id())
    return {
        "status": "ok",
        "project_id": selected_project_id,
        "agent_key": agent_key,
        "count": len(items),
        "items": items,
    }


def plugin_tasks_status(*, project_id: str | None = None) -> dict[str, Any]:
    agent = runtime.current_agent()
    selected_project_id = str(project_id or (agent.project_id if agent is not None else "")).strip()
    if not selected_project_id:
        return _error(
            "not_configured",
            "Hades backend is not configured.",
            "Run `hades backend setup` or pass --project-id to inspect cached task work.",
        )

    with db.connect_closing() as conn:
        items = [
            item
            for item in db.list_plugin_work_items(conn)
            if item.project_id == selected_project_id
        ]
    quality = _agent_work_quality(items)
    by_status: dict[str, int] = {}
    for item in items:
        by_status[item.status] = by_status.get(item.status, 0) + 1
    missing_count = int(quality.get("missing_shared_memory_context_count") or 0)
    next_step = "Run `hades backend tasks list` to refresh available backend work."
    if missing_count:
        next_step = "Run `hades backend quality-report --record` and repair work items missing memory_search_status."
    elif by_status.get("queued", 0) > 0:
        next_step = "Run `hades backend tasks work --once` to process queued work."
    elif items:
        next_step = "Use `hades backend tasks explain <work_item_id>` for details."
    return {
        "status": "ok",
        "project_id": selected_project_id,
        "total": len(items),
        "by_status": dict(sorted(by_status.items())),
        "quality": quality,
        "next_step": next_step,
    }


def explain_plugin_task(work_item_id: str) -> dict[str, Any]:
    clean_id = str(work_item_id or "").strip()
    if not clean_id:
        return _error(
            "missing_work_item_id",
            "A backend work item id is required.",
            "Run `hades backend tasks list --json` and pass one item work_item_id.",
        )
    with db.connect_closing() as conn:
        item = db.get_plugin_work_item(conn, clean_id)
    if item is None:
        return _error(
            "work_item_not_found",
            f"Cached backend work item {clean_id!r} was not found.",
            "Run `hades backend tasks list` to refresh the local work item cache.",
        )
    quality = _agent_work_quality([item])
    return {
        "status": "ok",
        "item": {
            "work_item_id": item.work_item_id,
            "project_id": item.project_id,
            "repository_id": item.repository_id or None,
            "local_workspace_id": item.local_workspace_id or None,
            "agent_key": item.agent_key,
            "kind": item.kind,
            "status": item.status,
            "payload": item.payload,
            "result": item.result,
        },
        "quality": quality,
    }


def _task_from_item(item: dict[str, Any]) -> dict[str, Any]:
    from hermes_cli.hades_kanban_task_contract import KANBAN_TASK_WORK_SCHEMA, kanban_task_contract_status

    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    prompt = _prompt_from_work_item_payload(payload)
    task_id = str(item.get("task_id") or payload.get("task_id") or "").strip()
    title = str(item.get("title") or payload.get("title") or payload.get("normalized_problem") or "").strip()
    if not title and prompt:
        title = prompt.splitlines()[0][:120]
    task = {
        "work_item_id": str(item.get("id") or item.get("work_item_id") or "").strip(),
        "task_id": task_id or None,
        "project_id": str(item.get("project_id") or payload.get("project_id") or "").strip() or None,
        "repository_id": str(item.get("repository_id") or payload.get("repository_id") or "").strip() or None,
        "agent_key": str(item.get("agent_key") or item.get("assigned_agent_key") or "local_agent").strip(),
        "status": str(item.get("status") or "queued").strip(),
        "priority": str(item.get("priority") or payload.get("priority") or "normal").strip(),
        "kind": str(item.get("kind") or payload.get("kind") or payload.get("schema") or "unknown").strip(),
        "title": title,
        "prompt_preview": prompt[:240],
        "payload": payload,
    }
    if str(payload.get("schema") or "").strip() == KANBAN_TASK_WORK_SCHEMA:
        task["contract"] = kanban_task_contract_status(payload)
    return task


def _cache_items(items: list[dict[str, Any]], *, selected_project_id: str, local_workspace_id: str) -> None:
    with db.connect_closing() as conn:
        for item in items:
            work_item_id = str(item.get("work_item_id") or "").strip()
            if not work_item_id:
                continue
            db.upsert_plugin_work_item(
                conn,
                work_item_id=work_item_id,
                project_id=str(item.get("project_id") or selected_project_id),
                repository_id=item.get("repository_id") if isinstance(item.get("repository_id"), str) else None,
                local_workspace_id=local_workspace_id or None,
                agent_key=str(item.get("agent_key") or "local_agent"),
                kind=str(item.get("kind") or "unknown"),
                status=str(item.get("status") or "queued"),
                payload=item.get("payload") if isinstance(item.get("payload"), dict) else {},
            )


def _agent_work_quality(items: list[Any]) -> dict[str, Any]:
    from hermes_cli.hades_quality_report import build_agent_work_quality_report

    return build_agent_work_quality_report(items)


def _plugin_exception_error(
    exc: Exception,
    *,
    fallback_code: str,
    fallback_message: str,
    fallback_next_step: str,
) -> tuple[str, str, str]:
    if isinstance(exc, HadesBackendError):
        code = str(exc.code or fallback_code)
        next_step = str(exc.next_step or fallback_next_step)
    else:
        code = fallback_code
        next_step = fallback_next_step
    return code, f"{fallback_message}: {redact_secret(str(exc))}", next_step


def _response_items(response: dict[str, Any]) -> list[dict[str, Any]]:
    value = response.get("items", response.get("data", response.get("work_items", [])))
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _select_repository(response: dict[str, Any], *, repository_id: str | None, workspace: Path) -> dict[str, Any]:
    repositories = response.get("repositories") if isinstance(response.get("repositories"), list) else []
    candidates = [repo for repo in repositories if isinstance(repo, dict)]
    if repository_id:
        for repo in candidates:
            if str(repo.get("repository_id") or "") == repository_id:
                return repo
        raise RuntimeError(f"repository_id {repository_id!r} was not found in backend project repositories")
    if len(candidates) == 1:
        return candidates[0]

    basename = workspace.name.lower()
    matches = [
        repo
        for repo in candidates
        if basename in {str(repo.get("slug") or "").lower(), str(repo.get("name") or "").lower()}
    ]
    if len(matches) == 1:
        return matches[0]

    available = ", ".join(
        f"{repo.get('name') or repo.get('slug') or 'repository'} ({repo.get('repository_id')})"
        for repo in candidates
    )
    raise RuntimeError(
        "could not infer backend repository for this workspace; pass --repository-id"
        + (f" (available: {available})" if available else "")
    )


def _git_state(root: Path) -> dict[str, Any]:
    remote = _git(root, "config", "--get", "remote.origin.url")
    remote_hash = hashlib.sha256(remote.encode("utf-8")).hexdigest() if remote else ""
    return {
        "current_branch": _git(root, "branch", "--show-current") or "unknown",
        "last_head_sha": _git(root, "rev-parse", "HEAD") or None,
        "dirty_status": "dirty" if _git(root, "status", "--porcelain") else "clean",
        "remote_name": "origin" if remote else None,
        "remote_url_host": _remote_host(remote),
        "remote_url_hash": f"sha256:{remote_hash}" if remote_hash else None,
        "upstream_branch": _git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}") or None,
        **_ahead_behind(root),
    }


def _ahead_behind(root: Path) -> dict[str, int | None]:
    value = _git(root, "rev-list", "--left-right", "--count", "HEAD...@{u}")
    if not value:
        return {"ahead_count": None, "behind_count": None}
    parts = value.split()
    if len(parts) != 2:
        return {"ahead_count": None, "behind_count": None}
    try:
        return {"ahead_count": int(parts[0]), "behind_count": int(parts[1])}
    except ValueError:
        return {"ahead_count": None, "behind_count": None}


def _git(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except Exception:
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _remote_host(remote: str) -> str | None:
    if not remote:
        return None
    if remote.startswith("https://") or remote.startswith("http://"):
        without_scheme = remote.split("://", 1)[1]
        return without_scheme.split("/", 1)[0] or None
    if "@" in remote and ":" in remote:
        return remote.split("@", 1)[1].split(":", 1)[0] or None
    return None


def _local_root_hash(root: Path) -> str:
    return "sha256:" + hashlib.sha256(str(root).encode("utf-8")).hexdigest()


def _device_fingerprint_hash(agent_id: str) -> str:
    profile = os.environ.get("HERMES_PROFILE", "default")
    material = f"{agent_id}|{socket.gethostname()}|{profile}|{platform.system()}|{platform.machine()}"
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _error(code: str, message: str, next_step: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "next_step": next_step}}


def _version() -> str:
    try:
        from hermes_cli import __version__

        return str(__version__)
    except Exception:
        return "0.0.0"


def _close_client(client: object) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        close()
