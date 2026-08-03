"""Runtime helpers shared by Hades backend CLI, projects, doctor and provider."""

from __future__ import annotations

import hashlib
import os
import platform
import socket
import subprocess
from pathlib import Path
from typing import Any

from agent.secret_scope import get_secret
from hermes_cli.config import load_config
from hermes_cli.hades_backend_client import HadesBackendClient
from hermes_cli.hades_plugin_work_items_client import HadesPluginWorkItemsClient
from hermes_cli import hades_backend_db as db


def backend_config() -> dict[str, Any]:
    cfg = load_config()
    value = cfg.get("backend", {})
    return value if isinstance(value, dict) else {}


def select_workspace_binding(
    bindings: list[db.WorkspaceBinding],
    path: str | Path,
    *,
    preferred_agent: db.BackendAgent | None = None,
) -> db.WorkspaceBinding | None:
    """Return the most specific linked binding containing ``path``.

    ``list_workspace_bindings`` is ordered newest-first, so the stable sort
    below preserves recency when two live bindings have the same repository
    root while still allowing a nested project to override its parent.
    """
    try:
        resolved = Path(path).expanduser().resolve()
    except OSError:
        return None
    matches: list[tuple[int, db.WorkspaceBinding]] = []
    for binding in bindings:
        try:
            root = Path(binding.repo_root).expanduser().resolve()
            resolved.relative_to(root)
        except (OSError, ValueError):
            continue
        matches.append((len(str(root)), binding))
    if preferred_agent is not None:
        preferred_matches = [
            item
            for item in matches
            if item[1].agent_id == preferred_agent.agent_id
            and item[1].project_id == preferred_agent.project_id
        ]
        if preferred_matches:
            matches = preferred_matches
    matches.sort(key=lambda item: item[0], reverse=True)
    return matches[0][1] if matches else None


def current_workspace_agent_binding(
    path: str | Path | None = None,
) -> tuple[db.BackendAgent, db.WorkspaceBinding] | None:
    """Resolve the backend identity owned by the current workspace."""
    probe = Path.cwd() if path is None else path
    with db.connect_closing() as conn:
        default_agent = db.get_default_agent(conn)
        binding = select_workspace_binding(
            db.list_workspace_bindings(conn, status="linked"),
            probe,
            preferred_agent=default_agent,
        )
        if binding is None:
            return None
        agent = db.get_agent(conn, binding.agent_id)
    if agent is None or agent.project_id != binding.project_id:
        return None
    return agent, binding


def current_agent() -> db.BackendAgent | None:
    scoped = current_workspace_agent_binding()
    if scoped is not None:
        return scoped[0]
    with db.connect_closing() as conn:
        return db.get_default_agent(conn)


def agent_token(agent: db.BackendAgent) -> str:
    return get_secret(agent.token_env_key, "") or ""


def plugin_work_items_token(agent: db.BackendAgent) -> str:
    cfg = backend_config()
    env_key = str(cfg.get("plugin_token_env_key") or "").strip()
    if env_key:
        return get_secret(env_key, "") or ""
    return get_secret("HADES_BACKEND_PLUGIN_TOKEN", "")


def plugin_device_secret() -> str:
    env_key = str(backend_config().get("plugin_device_secret_env_key") or "").strip()
    return get_secret(env_key, "") if env_key else ""


def client_from_config(*, timeout: float = 15.0) -> HadesBackendClient:
    agent = current_agent()
    if agent is None:
        raise RuntimeError("Hades backend is not configured; configure a backend project link first")
    return client_for_agent(agent, timeout=timeout)


def client_for_agent(agent: db.BackendAgent, *, timeout: float = 15.0) -> HadesBackendClient:
    token = agent_token(agent)
    if not token:
        raise RuntimeError(f"Hades backend token is missing from .env ({agent.token_env_key})")
    return HadesBackendClient(agent.base_url, token, timeout=timeout)


def plugin_work_items_client_from_config() -> HadesPluginWorkItemsClient:
    agent = current_agent()
    if agent is None:
        raise RuntimeError("Hades backend is not configured; configure a backend project link first")
    token = plugin_work_items_token(agent)
    if not token:
        raise RuntimeError(
            "Hades plugin API token is missing; set backend.plugin_token_env_key "
            "or HADES_BACKEND_PLUGIN_TOKEN. Do not use the Hades agent token for plugin work."
        )
    cfg = backend_config()
    return HadesPluginWorkItemsClient(
        agent.base_url,
        token,
        device_id=str(cfg.get("plugin_device_id") or "").strip(),
        device_secret=plugin_device_secret(),
    )


def plugin_local_workspace_id() -> str:
    cfg = backend_config()
    return str(cfg.get("plugin_local_workspace_id") or "").strip()


def default_worker_id(agent: db.BackendAgent | None = None) -> str:
    selected = agent or current_agent()
    agent_id = selected.agent_id if selected is not None else "unconfigured"
    profile = os.environ.get("HERMES_PROFILE", "default")
    return f"{agent_id}:{socket.gethostname()}:{profile}"


def default_agent_label() -> str:
    return f"{socket.gethostname()}:{os.environ.get('HERMES_PROFILE', 'default')}"


def default_agent_id(project_id: str, label: str) -> str:
    material = f"{project_id}|{label}|{platform.system()}|{platform.machine()}".encode("utf-8")
    return "ha_" + hashlib.sha256(material).hexdigest()[:16]


def workspace_fingerprint(path: str | Path, project_id: str) -> str:
    root = Path(path).expanduser().resolve()
    material = f"{project_id}|{root}".encode("utf-8")
    return "wf_" + hashlib.sha256(material).hexdigest()[:20]


def display_path(path: str | Path) -> str:
    resolved = str(Path(path).expanduser().resolve())
    home = str(Path.home())
    if resolved == home:
        return "~"
    if resolved.startswith(home + os.sep):
        return "~" + resolved[len(home):]
    return resolved


def git_metadata(path: str | Path) -> dict[str, str]:
    root = Path(path).expanduser().resolve()

    def _git(*args: str) -> str:
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

    remote = _git("config", "--get", "remote.origin.url")
    head = _git("rev-parse", "HEAD")
    remote_hash = hashlib.sha256(remote.encode("utf-8")).hexdigest() if remote else ""
    remote_display = remote
    if "@" in remote_display and ":" in remote_display:
        remote_display = remote_display.split("@", 1)[1].replace(":", "/", 1)
    if remote_display.startswith("https://"):
        remote_display = remote_display.removeprefix("https://")
    return {
        "git_remote_display": remote_display,
        "git_remote_hash": remote_hash,
        "head_commit": head,
    }
