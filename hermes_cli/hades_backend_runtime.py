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


def current_agent() -> db.BackendAgent | None:
    with db.connect_closing() as conn:
        return db.get_default_agent(conn)


def agent_token(agent: db.BackendAgent) -> str:
    return get_secret(agent.token_env_key, "") or ""


def plugin_work_items_token(agent: db.BackendAgent) -> str:
    cfg = backend_config()
    env_key = str(cfg.get("plugin_token_env_key") or "").strip()
    if env_key:
        return get_secret(env_key, "") or ""
    return get_secret("HADES_BACKEND_PLUGIN_TOKEN", "") or agent_token(agent)


def client_from_config(*, timeout: float = 15.0) -> HadesBackendClient:
    agent = current_agent()
    if agent is None:
        raise RuntimeError("Hades backend is not configured; run `hades backend setup` first")
    token = agent_token(agent)
    if not token:
        raise RuntimeError(f"Hades backend token is missing from .env ({agent.token_env_key})")
    return HadesBackendClient(agent.base_url, token, timeout=timeout)


def plugin_work_items_client_from_config() -> HadesPluginWorkItemsClient:
    agent = current_agent()
    if agent is None:
        raise RuntimeError("Hades backend is not configured; run `hades backend setup` first")
    token = plugin_work_items_token(agent)
    if not token:
        raise RuntimeError(
            "Hades plugin API token is missing; set backend.plugin_token_env_key "
            "or HADES_BACKEND_PLUGIN_TOKEN, or rerun backend setup if the backend accepts agent tokens"
        )
    return HadesPluginWorkItemsClient(agent.base_url, token)


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
