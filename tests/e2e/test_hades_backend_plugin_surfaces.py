"""Cross-surface contracts for the optional Hades Backend plugin.

These checks deliberately keep the ordinary host matrix self-contained.  A
developer can additionally set ``HADES_BACKEND_PLUGIN_REPO`` to validate the
released plugin checkout without making a sibling repository a CI dependency.
"""

from __future__ import annotations

import asyncio
from contextlib import ExitStack
import importlib.util
import io
import json
import os
from pathlib import Path
import queue
import select
import sqlite3
import subprocess
import sys
import threading
import time
from types import ModuleType
from typing import Any
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from hermes_cli.plugin_command_context import create_plugin_command_context
from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest


_MATRIX_STATES = (
    "absent",
    "disabled",
    "enabled-unconfigured",
    "enabled-unlinked",
    "enabled-linked-a",
    "enabled-linked-a-and-b",
)


@pytest.fixture()
def gateway_server():
    """Use the shared TUI/Desktop JSON-RPC dispatcher with no retained session."""
    from tui_gateway import server

    server._sessions.clear()
    server._pending.clear()
    server._pending_prompt_payloads.clear()
    server._pending_prompt_emissions.clear()
    server._answers.clear()
    yield server
    server._sessions.clear()
    server._pending.clear()
    server._pending_prompt_payloads.clear()
    server._pending_prompt_emissions.clear()
    server._answers.clear()


def _manager_with_backend_handler(handler) -> PluginManager:
    manager = PluginManager()
    context = PluginContext(
        PluginManifest(name="hades-backend", source="user"), manager
    )
    context.register_command(
        "backend", handler, "Optional project knowledge", "set-token|status|sync"
    )
    return manager


@pytest.fixture()
def fake_model_endpoint():
    """Reusable loopback-only OpenAI-compatible endpoint for real AIAgent turns."""
    requests: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            requests.append({"path": self.path, "payload": payload})
            if self.path == "/api/show":
                response: dict[str, Any] = {"capabilities": {}}
            elif self.path == "/v1/chat/completions":
                if payload.get("stream"):
                    chunks = [
                        {
                            "id": "task13-fake",
                            "object": "chat.completion.chunk",
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {
                                        "role": "assistant",
                                        "content": "ordinary fake-model response",
                                    },
                                    "finish_reason": None,
                                }
                            ],
                        },
                        {
                            "id": "task13-fake",
                            "object": "chat.completion.chunk",
                            "choices": [
                                {"index": 0, "delta": {}, "finish_reason": "stop"}
                            ],
                            "usage": {
                                "prompt_tokens": 1,
                                "completion_tokens": 1,
                                "total_tokens": 2,
                            },
                        },
                    ]
                    encoded = (
                        b"".join(
                            f"data: {json.dumps(chunk)}\n\n".encode()
                            for chunk in chunks
                        )
                        + b"data: [DONE]\n\n"
                    )
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Content-Length", str(len(encoded)))
                    self.end_headers()
                    self.wfile.write(encoded)
                    return
                response = {
                    "id": "task13-fake",
                    "object": "chat.completion",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "ordinary fake-model response",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                }
            else:  # pragma: no cover - diagnostic for unexpected model routes
                self.send_error(404)
                return
            encoded = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, _format: str, *_args: Any) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/v1", requests
    server.shutdown()
    thread.join(timeout=2)


@pytest.fixture()
def backend_fail_sentinel():
    """Count any forbidden automatic Backend request without using external I/O."""
    requests: list[tuple[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def _reject(self) -> None:
            requests.append((self.command, self.path))
            self.send_error(500, "automatic Backend access is forbidden")

        do_GET = _reject  # type: ignore[assignment]
        do_POST = _reject  # type: ignore[assignment]
        do_PUT = _reject  # type: ignore[assignment]
        do_DELETE = _reject  # type: ignore[assignment]

        def log_message(self, _format: str, *_args: Any) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", requests
    server.shutdown()
    thread.join(timeout=2)


@pytest.fixture()
def external_backend_endpoint():
    """Loopback-only Backend boundary for real external pairing/query/sync."""
    calls: list[dict[str, Any]] = []
    capabilities = {
        name: True
        for name in (
            "read_files",
            "read_source_slice",
            "project_inspection",
            "sync_git_tree",
            "populate_backend_ast",
            "populate_project_wiki",
            "verify_project_wiki",
            "write_project_logbook",
        )
    }

    class Handler(BaseHTTPRequestHandler):
        def _record(self, body: dict[str, Any] | None = None) -> None:
            calls.append({
                "method": self.command,
                "path": self.path,
                "authorization": self.headers.get("Authorization", ""),
                "body": body,
            })

        def _reply(self, payload: dict[str, Any]) -> None:
            encoded = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
            self._record(body)
            route = self.path.removeprefix("/api/hades/v1/")
            project = str(body.get("project_id") or "project")
            suffix = project.upper().replace("-", "_")
            if route == "token/verify":
                self._reply({"project_id": project, "valid": True})
            elif route == "agents/register":
                self._reply({
                    "agent_id": f"agent-{project}",
                    "agent_token": f"DERIVED_{suffix}",
                    "capabilities": capabilities,
                })
            elif route == "workspaces/bind":
                self._reply({"workspace_binding_id": f"binding-{project}"})
            elif route == "artifacts":
                self._reply({"ok": True})
            else:  # pragma: no cover - protocol diagnostic
                self.send_error(404)

        def do_GET(self) -> None:  # noqa: N802
            self._record()
            route = self.path.removeprefix("/api/hades/v1/").split("?", 1)[0]
            if route == "memory/search":
                self._reply({"items": [], "count": 0})
            elif route == "artifacts/lookup":
                self._reply({"exists": False})
            else:  # pragma: no cover - protocol diagnostic
                self.send_error(404)

        def log_message(self, _format: str, *_args: Any) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", calls
    server.shutdown()
    thread.join(timeout=2)


def _write_lifecycle_matrix_profile(
    profile: Path,
    *,
    state: str,
    memory_provider: str,
    model_url: str,
    backend_url: str,
) -> dict[str, bytes]:
    """Create one canonical matrix row and return exact memory snapshots."""
    profile.mkdir(parents=True)
    enabled = state.startswith("enabled")
    memory_block = (
        "memory:\n  provider: holographic\n  holographic:\n    namespace: task13\n"
        if memory_provider == "holographic"
        else "memory:\n  provider: ''\n  builtin:\n    namespace: task13\n"
    )
    config = (
        f"plugins:\n  enabled: [{'hades-backend' if enabled else ''}]\n"
        f"{memory_block}"
        "model:\n"
        "  default: task13-fake\n"
        "  provider: custom\n"
        f"  base_url: {model_url}\n"
        "  api_key: fake-key\n"
        "  api_mode: chat_completions\n"
    )
    if state in {
        "enabled-unlinked",
        "enabled-linked-a",
        "enabled-linked-a-and-b",
    }:
        config += "mcp_servers:\n  hades_backend:\n    command: python\n"
    (profile / "config.yaml").write_text(config, encoding="utf-8")
    if state != "absent":
        plugin = profile / "plugins" / "hades-backend"
        plugin.mkdir(parents=True)
        (plugin / "plugin.yaml").write_text(
            "name: hades-backend\nkind: standalone\nversion: 0.0.0-test\n",
            encoding="utf-8",
        )
        (plugin / "__init__.py").write_text(
            "def register(ctx):\n"
            "    ctx.register_command('backend', lambda _raw, _ctx: 'local-only', "
            "'Backend', 'set-token|status|sync')\n",
            encoding="utf-8",
        )

    linked = []
    if state in {"enabled-linked-a", "enabled-linked-a-and-b"}:
        linked.append("a")
    if state == "enabled-linked-a-and-b":
        linked.append("b")
    if linked:
        with sqlite3.connect(profile / "hades_backend.db") as connection:
            connection.executescript(
                "CREATE TABLE backend_agents ("
                "agent_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, "
                "base_url TEXT NOT NULL, label TEXT NOT NULL, "
                "token_env_key TEXT NOT NULL, capabilities TEXT NOT NULL);"
                "CREATE TABLE workspace_bindings ("
                "workspace_fingerprint TEXT PRIMARY KEY, project_id TEXT NOT NULL, "
                "agent_id TEXT NOT NULL, local_project_id TEXT NOT NULL, "
                "backend_workspace_binding_id TEXT NOT NULL, display_path TEXT NOT NULL, "
                "repo_root TEXT NOT NULL, git_remote_display TEXT, git_remote_hash TEXT, "
                "head_commit TEXT, status TEXT NOT NULL);"
            )
            env_lines = []
            for label in linked:
                workspace = profile / f"workspace-{label}"
                workspace.mkdir()
                token_key = f"TASK13_DERIVED_{label.upper()}"
                connection.execute(
                    "INSERT INTO backend_agents VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        f"agent-{label}",
                        f"project-{label}",
                        backend_url,
                        label,
                        token_key,
                        "{}",
                    ),
                )
                connection.execute(
                    "INSERT INTO workspace_bindings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        f"fingerprint-{label}",
                        f"project-{label}",
                        f"agent-{label}",
                        f"local-{label}",
                        f"binding-{label}",
                        str(workspace),
                        str(workspace),
                        "",
                        "",
                        "",
                        "linked",
                    ),
                )
                env_lines.append(f"{token_key}=DERIVED_{label.upper()}\n")
        (profile / ".env").write_text("".join(env_lines), encoding="utf-8")

    (profile / "MEMORY.md").write_text("task13 memory sentinel\n", encoding="utf-8")
    (profile / "USER.md").write_text("task13 user sentinel\n", encoding="utf-8")
    (profile / "memory_store.db").write_bytes(b"task13-memory-database")
    return _memory_snapshot(profile)


def _memory_snapshot(profile: Path) -> dict[str, bytes]:
    config = (profile / "config.yaml").read_text(encoding="utf-8")
    memory_lines = []
    collecting = False
    for line in config.splitlines(keepends=True):
        if line.startswith("memory:"):
            collecting = True
        elif collecting and line and not line.startswith((" ", "\t")):
            break
        if collecting:
            memory_lines.append(line)
    return {
        "memory_config": "".join(memory_lines).encode(),
        "MEMORY.md": (profile / "MEMORY.md").read_bytes(),
        "USER.md": (profile / "USER.md").read_bytes(),
        "memory_store.db": (profile / "memory_store.db").read_bytes(),
    }


def _prepare_external_profile(
    profile: Path,
    plugin_root: Path,
    *,
    memory_provider: str,
    model_url: str | None = None,
) -> dict[str, bytes]:
    profile.mkdir(parents=True)
    plugin_dir = profile / "plugins"
    plugin_dir.mkdir()
    (plugin_dir / "hades-backend").symlink_to(plugin_root, target_is_directory=True)
    memory_block = (
        "memory:\n  provider: holographic\n  holographic:\n    namespace: task13\n"
        if memory_provider == "holographic"
        else "memory:\n  provider: ''\n  builtin:\n    namespace: task13\n"
    )
    config = "plugins:\n  enabled: [hades-backend]\n" + memory_block
    if model_url is not None:
        config += (
            "model:\n"
            "  default: task13-fake\n"
            "  provider: custom\n"
            f"  base_url: {model_url}\n"
            "  api_key: fake-key\n"
            "  api_mode: chat_completions\n"
        )
    (profile / "config.yaml").write_text(config, encoding="utf-8")
    (profile / "MEMORY.md").write_text(
        f"task13 {memory_provider} memory sentinel\n", encoding="utf-8"
    )
    (profile / "USER.md").write_text(
        f"task13 {memory_provider} user sentinel\n", encoding="utf-8"
    )
    (profile / "memory_store.db").write_bytes(
        f"task13-{memory_provider}-memory-database".encode()
    )
    return _memory_snapshot(profile)


def _assert_external_secret_hygiene(
    profile: Path,
    *,
    bootstrap: str,
    derived: str,
    sinks: Any,
) -> None:
    """Scan persisted/runtime observer sinks, excluding the fake server recorder."""
    file_blobs: list[tuple[Path, bytes]] = []
    for path in profile.rglob("*"):
        if path.is_file() and not path.is_symlink():
            file_blobs.append((path, path.read_bytes()))
    assert all(bootstrap.encode() not in blob for _path, blob in file_blobs)
    rendered_sinks = json.dumps(sinks, sort_keys=True, default=str)
    assert bootstrap not in rendered_sinks
    env = (profile / ".env").read_text(encoding="utf-8")
    assert env.count(derived) == 1
    occurrences = sum(blob.count(derived.encode()) for _path, blob in file_blobs)
    assert occurrences == 1


def _exercise_external_query_and_sync(
    plugin_root: Path,
    *,
    profile: Path,
    workspace: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    entrypoint = _load_external_entrypoint(plugin_root)
    package = f"{entrypoint.__name__}.hades_backend_plugin"
    service = importlib.import_module(f"{package}.service")
    sync = importlib.import_module(f"{package}.sync")
    query = service.ProjectKnowledgeService(profile_home=profile).project_search(
        workspace, "task13"
    )
    synced = sync.sync_project_knowledge(
        sync.ProjectSyncRequest(
            workspace=workspace,
            domain="source_index",
            profile_home=profile,
        )
    )
    assert query["status"] == "ok"
    assert synced["status"] == "ok"
    assert synced["network_attempted"] is True
    return query, synced


@pytest.mark.parametrize("surface", ["tui", "desktop"])
def test_secure_backend_pairing_cancel_is_session_scoped_and_side_effect_free(
    monkeypatch, gateway_server, tmp_path: Path, surface: str
) -> None:
    """A cancelled secret overlay must not reach a pairing or Backend client path."""
    pair_calls: list[dict[str, str]] = []

    def backend_handler(_raw_args: str, context) -> str:
        token = context.request_secret("Bootstrap token")
        if not token:
            return "Backend pairing cancelled."
        pair_calls.append({"cwd": str(context.cwd), "token": token})
        return "Backend project paired."

    monkeypatch.setattr(
        "hermes_cli.plugins._plugin_manager",
        _manager_with_backend_handler(backend_handler),
    )
    workspace = tmp_path / surface / "project"
    workspace.mkdir(parents=True)
    session_id = f"{surface}-cancel"
    gateway_server._sessions[session_id] = {
        "session_key": session_id,
        "cwd": str(workspace),
        "source": surface,
        "agent": None,
    }
    response: dict[str, Any] = {}

    def invoke() -> None:
        response.update(
            gateway_server.handle_request({
                "id": session_id,
                "method": "slash.exec",
                "params": {
                    "command": "backend set-token --url https://fake.invalid --project-id project-a",
                    "session_id": session_id,
                },
            })
        )

    worker = threading.Thread(target=invoke)
    worker.start()
    request_id = _wait_for_owned_secret_request(gateway_server, session_id)
    reply = gateway_server.handle_request({
        "id": f"cancel-{surface}",
        "method": "secret.respond",
        "params": {"request_id": request_id, "session_id": session_id, "value": ""},
    })
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert reply["result"] == {"status": "ok"}
    assert response["result"]["output"] == "Backend pairing cancelled."
    assert pair_calls == []
    assert list((tmp_path / surface).rglob("*")) == [workspace]


@pytest.mark.parametrize("surface", ["tui", "desktop"])
def test_secure_backend_pairing_redacts_bootstrap_token_from_json_rpc_output(
    monkeypatch, gateway_server, tmp_path: Path, surface: str
) -> None:
    """The common dispatcher must redact secret-overlay values before its response boundary."""
    canary = f"BOOTSTRAP_{surface.upper()}_CANARY"
    pair_calls: list[dict[str, str]] = []

    def backend_handler(_raw_args: str, context) -> str:
        token = context.request_secret("Bootstrap token")
        assert token == canary
        pair_calls.append({"cwd": str(context.cwd), "token": token})
        return f"paired with {token}"

    monkeypatch.setattr(
        "hermes_cli.plugins._plugin_manager",
        _manager_with_backend_handler(backend_handler),
    )
    workspace = tmp_path / surface / "project"
    workspace.mkdir(parents=True)
    session_id = f"{surface}-complete"
    gateway_server._sessions[session_id] = {
        "session_key": session_id,
        "cwd": str(workspace),
        "source": surface,
        "agent": None,
    }
    response: dict[str, Any] = {}

    worker = threading.Thread(
        target=lambda: response.update(
            gateway_server.handle_request({
                "id": session_id,
                "method": "slash.exec",
                "params": {
                    "command": "backend set-token --url https://fake.invalid --project-id project-a",
                    "session_id": session_id,
                },
            })
        )
    )
    worker.start()
    request_id = _wait_for_owned_secret_request(gateway_server, session_id)
    gateway_server.handle_request({
        "id": f"complete-{surface}",
        "method": "secret.respond",
        "params": {"request_id": request_id, "session_id": session_id, "value": canary},
    })
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert pair_calls == [{"cwd": str(workspace.resolve()), "token": canary}]
    serialized = repr(response)
    assert canary not in serialized
    assert "[secret]" in serialized


def test_plugin_command_context_cannot_mutate_an_inflight_prompt_or_tool_schema(
    tmp_path: Path,
) -> None:
    """Plugin pairing is invocation-scoped; it owns no agent prompt/tool mutation capability."""
    context = create_plugin_command_context(
        cwd=tmp_path,
        session_id="stable-session",
        surface="desktop",
        interactive=True,
        request_secret=lambda _prompt: "DERIVED_TOKEN_CANARY",
    )

    assert context.cwd == tmp_path.resolve()
    assert not hasattr(context, "messages")
    assert not hasattr(context, "tools")
    assert (
        context.render(f"token={context.request_secret('Bootstrap token')}")
        == "token=[secret]"
    )


def test_desktop_plugin_invocations_keep_profiles_scoped_across_success_and_cancel(
    monkeypatch, gateway_server, tmp_path: Path
) -> None:
    """Direct plugin commands bind one Desktop profile for their whole secret lifecycle."""
    from hermes_constants import get_hermes_home

    launch_home = tmp_path / "launch-profile"
    profiles = {
        "success": tmp_path / "profile-success",
        "cancel": tmp_path / "profile-cancel",
    }
    for profile in (launch_home, *profiles.values()):
        profile.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(launch_home))
    observed: list[tuple[str, str]] = []

    def profile_handler(_raw_args: str, context) -> str:
        before = str(get_hermes_home())
        token = context.request_secret("Profile-scoped token")
        after = str(get_hermes_home())
        observed.append((before, after))
        return "cancelled" if not token else "completed"

    manager = PluginManager()
    context = PluginContext(
        PluginManifest(name="profile-probe", source="user"), manager
    )
    context.register_command("profile-probe", profile_handler, "Profile probe")
    monkeypatch.setattr("hermes_cli.plugins._plugin_manager", manager)
    for label, profile in profiles.items():
        gateway_server._sessions[f"desktop-{label}"] = {
            "agent": None,
            "cwd": str(tmp_path),
            "profile_home": str(profile),
            "session_key": f"desktop-{label}",
            "source": "desktop",
        }

    responses: dict[str, dict[str, Any]] = {}
    workers = [
        threading.Thread(
            target=lambda label=label: responses.setdefault(
                label,
                gateway_server.handle_request({
                    "id": f"{label}-command",
                    "method": "slash.exec",
                    "params": {
                        "session_id": f"desktop-{label}",
                        "command": "profile-probe",
                    },
                }),
            )
        )
        for label in profiles
    ]
    for worker in workers:
        worker.start()
    request_ids = {
        label: _wait_for_owned_secret_request(gateway_server, f"desktop-{label}")
        for label in profiles
    }
    for label, value in (("success", "DERIVED_SUCCESS_ONLY"), ("cancel", "")):
        gateway_server.handle_request({
            "id": f"{label}-secret",
            "method": "secret.respond",
            "params": {
                "request_id": request_ids[label],
                "session_id": f"desktop-{label}",
                "value": value,
            },
        })
    for worker in workers:
        worker.join(timeout=2)
        assert not worker.is_alive()

    assert responses["success"]["result"]["output"] == "completed"
    assert responses["cancel"]["result"]["output"] == "cancelled"
    assert set(observed) == {
        (str(profiles["success"]), str(profiles["success"])),
        (str(profiles["cancel"]), str(profiles["cancel"])),
    }
    assert str(get_hermes_home()) == str(launch_home)
    assert "DERIVED_SUCCESS_ONLY" not in repr(responses)


def test_dispatched_plugin_invocations_scope_concurrent_success_cancel_and_error(
    monkeypatch, gateway_server, tmp_path: Path
) -> None:
    """Fallback dispatch owns the same per-session profile scope as slash.exec."""
    from hermes_cli import plugin_command_context
    from hermes_constants import get_hermes_home

    launch_home = tmp_path / "launch-profile"
    outcomes = ("success", "cancel", "error")
    profiles = {outcome: tmp_path / f"profile-{outcome}" for outcome in outcomes}
    for profile in (launch_home, *profiles.values()):
        profile.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(launch_home))

    created: dict[str, str] = {}
    observed: dict[str, tuple[str, str]] = {}
    original_create_context = plugin_command_context.create_plugin_command_context

    def create_context(**kwargs: Any):
        created[kwargs["session_id"]] = str(get_hermes_home())
        return original_create_context(**kwargs)

    def profile_handler(raw_args: str, context) -> str:
        before = str(get_hermes_home())
        token = context.request_secret("Profile-scoped token")
        after = str(get_hermes_home())
        observed[raw_args] = (before, after)
        if raw_args == "error":
            raise RuntimeError("profile-probe-error")
        return "cancelled" if not token else "completed"

    manager = PluginManager()
    plugin_context = PluginContext(
        PluginManifest(name="profile-probe", source="user"), manager
    )
    plugin_context.register_command("profile-probe", profile_handler, "Profile probe")
    monkeypatch.setattr(
        plugin_command_context, "create_plugin_command_context", create_context
    )
    monkeypatch.setattr("hermes_cli.plugins._plugin_manager", manager)

    for outcome, profile in profiles.items():
        session_id = f"desktop-dispatch-{outcome}"
        gateway_server._sessions[session_id] = {
            "agent": None,
            "cwd": str(tmp_path),
            "profile_home": str(profile),
            "session_key": session_id,
            "source": "desktop",
        }

    responses: dict[str, dict[str, Any]] = {}

    def invoke(outcome: str) -> None:
        responses[outcome] = gateway_server.handle_request({
            "id": f"{outcome}-command",
            "method": "command.dispatch",
            "params": {
                "session_id": f"desktop-dispatch-{outcome}",
                "name": "profile-probe",
                "arg": outcome,
            },
        })

    workers = {
        outcome: threading.Thread(target=invoke, args=(outcome,))
        for outcome in outcomes
    }
    for worker in workers.values():
        worker.start()
    request_ids = {
        outcome: _wait_for_owned_secret_request(
            gateway_server, f"desktop-dispatch-{outcome}"
        )
        for outcome in outcomes
    }
    for outcome, value in (
        ("success", "DERIVED_SUCCESS_ONLY"),
        ("cancel", ""),
        ("error", "DERIVED_ERROR_ONLY"),
    ):
        gateway_server.handle_request({
            "id": f"{outcome}-secret",
            "method": "secret.respond",
            "params": {
                "request_id": request_ids[outcome],
                "session_id": f"desktop-dispatch-{outcome}",
                "value": value,
            },
        })
    for worker in workers.values():
        worker.join(timeout=2)
        assert not worker.is_alive()

    assert created == {
        f"desktop-dispatch-{outcome}": str(profiles[outcome]) for outcome in outcomes
    }
    assert observed == {
        outcome: (str(profiles[outcome]), str(profiles[outcome]))
        for outcome in outcomes
    }
    assert responses["success"]["result"]["output"] == "completed"
    assert responses["cancel"]["result"]["output"] == "cancelled"
    assert (
        "Plugin command error: profile-probe-error"
        in responses["error"]["result"]["output"]
    )
    assert str(get_hermes_home()) == str(launch_home)
    assert "DERIVED_SUCCESS_ONLY" not in repr(responses)
    assert "DERIVED_ERROR_ONLY" not in repr(responses)


def test_fake_model_factory_preserves_the_configured_opencode_runtime(
    monkeypatch,
) -> None:
    """The TUI/Desktop factory must not silently route a configured model elsewhere."""
    from tui_gateway import server
    import run_agent

    prompt = "Task 13 fixed prompt\nDo not add Backend tools."
    constructed: list[dict[str, Any]] = []
    resolved: list[dict[str, Any]] = []

    class FakeModelAgent:
        def __init__(self, **kwargs: Any) -> None:
            constructed.append(kwargs)

    monkeypatch.setattr(run_agent, "AIAgent", FakeModelAgent)
    monkeypatch.setattr(
        server,
        "_load_cfg",
        lambda: {
            "model": {
                "provider": "opencode-go",
                "default": "deepseek-v4-flash",
            },
            "agent": {"system_prompt": prompt},
        },
    )
    monkeypatch.setattr(
        server,
        "_resolve_startup_runtime",
        lambda: ("deepseek-v4-flash", "opencode-go"),
    )
    monkeypatch.setattr(
        server,
        "_resolve_runtime_with_fallback",
        lambda kwargs: (
            resolved.append(kwargs)
            or {
                "provider": "opencode-go",
                "base_url": "http://fake.invalid/v1",
                "api_key": "fake-key",
                "api_mode": "chat_completions",
                "credential_pool": None,
            }
        ),
    )

    server._make_agent("tui-surface", "session-a")
    server._make_agent("desktop-surface", "session-b")

    assert resolved == [
        {"requested": "opencode-go", "target_model": "deepseek-v4-flash"},
        {"requested": "opencode-go", "target_model": "deepseek-v4-flash"},
    ]
    assert [agent["model"] for agent in constructed] == [
        "deepseek-v4-flash",
        "deepseek-v4-flash",
    ]
    assert [agent["provider"] for agent in constructed] == [
        "opencode-go",
        "opencode-go",
    ]
    prompts = [agent["ephemeral_system_prompt"].encode() for agent in constructed]
    assert prompts == [prompt.encode(), prompt.encode()]


@pytest.mark.parametrize("surface", ["tui", "desktop"])
def test_fake_model_turn_routes_title_after_an_ordinary_surface_conversation(
    monkeypatch, gateway_server, surface: str
) -> None:
    """The shared TUI/Desktop dispatcher emits an ordinary fake-model turn and title."""
    received: list[dict[str, Any]] = []
    emitted: list[tuple[str, str, dict[str, Any]]] = []

    class FakeModelAgent:
        model = "deepseek-v4-flash"
        provider = "opencode-go"
        base_url = "http://fake.invalid/v1"
        api_key = "fake-key"
        api_mode = "chat_completions"

        def run_conversation(
            self,
            prompt: str,
            conversation_history: list[dict[str, Any]] | None = None,
            stream_callback=None,
            task_id: str | None = None,
        ) -> dict[str, Any]:
            received.append({
                "prompt": prompt,
                "history": list(conversation_history or []),
                "task_id": task_id,
            })
            if stream_callback is not None:
                stream_callback("ordinary fake-model response")
            return {
                "final_response": "ordinary fake-model response",
                "messages": [
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": "ordinary fake-model response"},
                ],
            }

    class ImmediateThread:
        def __init__(self, target=None, daemon=None, **_kwargs: Any) -> None:
            self._target = target

        def start(self) -> None:
            assert self._target is not None
            self._target()

    session_id = f"{surface}-ordinary-turn"
    gateway_server._sessions[session_id] = {
        "agent": FakeModelAgent(),
        "attached_images": [],
        "cols": 80,
        "cwd": os.getcwd(),
        "history": [],
        "history_lock": threading.Lock(),
        "history_version": 0,
        "image_counter": 0,
        "running": False,
        "session_key": f"{surface}-stored-session",
        "slash_worker": None,
        "source": surface,
        "tool_progress_mode": "all",
    }
    monkeypatch.setattr(gateway_server.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(
        gateway_server,
        "_emit",
        lambda event, sid, payload=None: emitted.append((event, sid, payload or {})),
    )
    monkeypatch.setattr(gateway_server, "_get_db", lambda: None)
    monkeypatch.setattr(
        gateway_server, "_sync_agent_model_with_config", lambda *_args: None
    )
    monkeypatch.setattr(gateway_server, "make_stream_renderer", lambda _cols: None)
    monkeypatch.setattr(gateway_server, "render_message", lambda _raw, _cols: None)

    with patch("agent.title_generator.maybe_auto_title") as title:
        response = gateway_server.handle_request({
            "id": f"{surface}-prompt",
            "method": "prompt.submit",
            "params": {"session_id": session_id, "text": "ordinary question"},
        })

    assert response["result"] == {"status": "streaming"}
    assert received == [
        {
            "prompt": "ordinary question",
            "history": [],
            "task_id": f"{surface}-stored-session",
        }
    ]
    complete = next(
        payload
        for event, sid, payload in emitted
        if event == "message.complete" and sid == session_id
    )
    assert complete["text"] == "ordinary fake-model response"
    assert complete["status"] == "complete"
    title.assert_called_once()
    assert title.call_args.args[1:4] == (
        f"{surface}-stored-session",
        "ordinary question",
        "ordinary fake-model response",
    )
    assert title.call_args.kwargs["main_runtime"] == {
        "model": "deepseek-v4-flash",
        "provider": "opencode-go",
        "base_url": "http://fake.invalid/v1",
        "api_key": "fake-key",
        "api_mode": "chat_completions",
    }


def test_classic_fake_model_turn_and_resume_keep_one_runtime_and_title(
    monkeypatch,
) -> None:
    """The actual classic ``HermesCLI.chat`` route preserves its fake-model transcript."""
    import cli

    calls: list[dict[str, Any]] = []

    class FakeModelAgent:
        session_id = "classic-session"
        max_iterations = 90

        def run_conversation(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(kwargs)
            text = str(kwargs["user_message"])
            return {
                "final_response": f"answer: {text}",
                "messages": list(kwargs["conversation_history"])
                + [
                    {"role": "user", "content": text},
                    {"role": "assistant", "content": f"answer: {text}"},
                ],
                "completed": True,
                "api_calls": 1,
            }

        def drain_autopoiesis_notices(self) -> None:
            return None

    class QuietConsole:
        width = 80

        def print(self, *_args: Any, **_kwargs: Any) -> None:
            return None

    classic = cli.HermesCLI.__new__(cli.HermesCLI)
    classic.agent = FakeModelAgent()
    classic.api_mode = "chat_completions"
    classic.api_key = "fake-key"
    classic.base_url = "http://fake.invalid/v1"
    classic.bell_on_complete = False
    classic.conversation_history = []
    classic.console = QuietConsole()
    classic.final_response_markdown = False
    classic.model = "deepseek-v4-flash"
    classic.provider = "opencode-go"
    classic.session_id = "classic-session"
    classic.show_reasoning = False
    classic._active_agent_route_signature = "fixed-route"
    classic._clarify_freetext = False
    classic._clarify_state = None
    classic._interrupt_queue = __import__("queue").Queue()
    classic._last_turn_interrupted = False
    classic._pending_model_switch_note = None
    classic._pending_skills_reload_note = None
    classic._pending_moa_config = None
    classic._prompt_start_time = None
    classic._session_db = None
    classic._stream_box_opened = False
    classic._stream_started = False
    classic._voice_continuous = False
    classic._voice_mode = False
    classic._voice_tts = False
    classic._ensure_runtime_credentials = lambda: True
    classic._flush_credit_notices = lambda: None
    classic._flush_stream = lambda: None
    classic._invalidate = lambda **_kwargs: None
    classic._reset_stream_state = lambda: None
    classic._resolve_turn_agent_config = lambda _message: {
        "signature": "fixed-route",
        "model": "deepseek-v4-flash",
        "runtime": {"provider": "opencode-go"},
    }
    classic._scrollback_box_width = lambda: 80
    classic._secret_capture_callback = lambda *_args: None
    classic._sudo_password_callback = lambda *_args: None
    classic._approval_callback = lambda *_args: None
    classic._transfer_session_yolo = lambda *_args: None

    monkeypatch.setattr(cli, "ChatConsole", QuietConsole)
    monkeypatch.setattr(cli, "Panel", lambda value, **_kwargs: value)
    monkeypatch.setattr(cli, "_cprint", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)
    with patch("agent.title_generator.maybe_auto_title") as title:
        assert classic.chat("first question") == "answer: first question"
        assert classic.chat("resume question") == "answer: resume question"

    assert [call["task_id"] for call in calls] == ["classic-session", "classic-session"]
    assert calls[0]["conversation_history"] == []
    assert calls[1]["conversation_history"] == [
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "answer: first question"},
    ]
    assert title.call_count == 2
    assert all(
        call.kwargs["main_runtime"]
        == {
            "model": "deepseek-v4-flash",
            "provider": "opencode-go",
            "base_url": "http://fake.invalid/v1",
            "api_key": "fake-key",
            "api_mode": "chat_completions",
        }
        for call in title.call_args_list
    )


def _classic_cli_with_agent(agent, model_url: str):
    import cli

    class QuietConsole:
        width = 80

        def print(self, *_args: Any, **_kwargs: Any) -> None:
            return None

    classic = cli.HermesCLI.__new__(cli.HermesCLI)
    classic.agent = agent
    classic.api_mode = "chat_completions"
    classic.api_key = "fake-key"
    classic.base_url = model_url
    classic.bell_on_complete = False
    classic.conversation_history = []
    classic.console = QuietConsole()
    classic.final_response_markdown = False
    classic.model = "task13-fake"
    classic.provider = "opencode-go"
    classic.session_id = agent.session_id
    classic.show_reasoning = False
    classic._active_agent_route_signature = "task13-fixed-route"
    classic._clarify_freetext = False
    classic._clarify_state = None
    classic._interrupt_queue = queue.Queue()
    classic._last_turn_interrupted = False
    classic._pending_model_switch_note = None
    classic._pending_skills_reload_note = None
    classic._pending_moa_config = None
    classic._prompt_start_time = None
    classic._session_db = getattr(agent, "_session_db", None)
    classic._stream_box_opened = False
    classic._stream_started = False
    classic._voice_continuous = False
    classic._voice_mode = False
    classic._voice_tts = False
    classic._ensure_runtime_credentials = lambda: True
    classic._flush_credit_notices = lambda: None
    classic._flush_stream = lambda: None
    classic._invalidate = lambda **_kwargs: None
    classic._reset_stream_state = lambda: None
    classic._resolve_turn_agent_config = lambda _message: {
        "signature": "task13-fixed-route",
        "model": "task13-fake",
        "runtime": {"provider": "opencode-go"},
    }
    classic._scrollback_box_width = lambda: 80
    classic._secret_capture_callback = lambda *_args: None
    classic._sudo_password_callback = lambda *_args: None
    classic._approval_callback = lambda *_args: None
    classic._transfer_session_yolo = lambda *_args: None
    return classic, QuietConsole


@pytest.mark.live_system_guard_bypass
@pytest.mark.parametrize("state", _MATRIX_STATES)
@pytest.mark.parametrize("memory_provider", ["holographic", "builtin-only"])
def test_every_canonical_row_runs_real_classic_and_tui_agent_lifecycle_without_backend(
    monkeypatch,
    gateway_server,
    tmp_path: Path,
    fake_model_endpoint,
    backend_fail_sentinel,
    state: str,
    memory_provider: str,
) -> None:
    """Every profile state supports ordinary/resumed real-agent work without Backend."""
    import cli
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from hermes_state import SessionDB
    from run_agent import AIAgent

    model_url, model_requests = fake_model_endpoint
    backend_url, backend_requests = backend_fail_sentinel
    profile = tmp_path / f"{state}-{memory_provider}"
    before = _write_lifecycle_matrix_profile(
        profile,
        state=state,
        memory_provider=memory_provider,
        model_url=model_url,
        backend_url=backend_url,
    )
    workspace = profile / "ordinary-workspace"
    workspace.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(profile))
    home_token = set_hermes_home_override(profile)
    backend_count = len(backend_requests)
    model_count = len(model_requests)
    titles: list[dict[str, Any]] = []

    def capture_title(*_args: Any, **kwargs: Any) -> None:
        titles.append(kwargs["main_runtime"])

    def make_agent(session_id: str, db_path: Path) -> AIAgent:
        agent = AIAgent(
            model="task13-fake",
            provider="opencode-go",
            base_url=model_url,
            api_key="fake-key",
            api_mode="chat_completions",
            quiet_mode=True,
            session_id=session_id,
            skip_context_files=True,
            skip_memory=True,
            session_db=SessionDB(db_path=db_path),
        )
        agent._disable_streaming = True
        return agent

    try:
        classic_agent = make_agent("task13-classic", profile / "classic-state.db")
        classic, quiet_console = _classic_cli_with_agent(classic_agent, model_url)
        with (
            patch.object(cli, "ChatConsole", quiet_console),
            patch.object(cli, "Panel", lambda value, **_kwargs: value),
            patch.object(cli, "_cprint", lambda *_args, **_kwargs: None),
            patch.object(cli.time, "sleep", lambda _seconds: None),
            patch("agent.title_generator.maybe_auto_title", side_effect=capture_title),
        ):
            assert classic.chat("classic ordinary") == "ordinary fake-model response"
            assert classic.chat("classic resumed") == "ordinary fake-model response"
        assert [message["content"] for message in classic.conversation_history] == [
            "classic ordinary",
            "ordinary fake-model response",
            "classic resumed",
            "ordinary fake-model response",
        ]

        tui_db = SessionDB(db_path=profile / "state.db")
        tui_agent = make_agent("task13-tui-stored", profile / "state.db")
        session_id = "task13-tui-live"
        ready = threading.Event()
        ready.set()
        gateway_server._sessions[session_id] = {
            "agent": tui_agent,
            "agent_error": None,
            "agent_ready": ready,
            "attached_images": [],
            "cols": 80,
            "created_at": time.time(),
            "cwd": str(workspace),
            "explicit_cwd": True,
            "history": [],
            "history_lock": threading.Lock(),
            "history_version": 0,
            "image_counter": 0,
            "inflight_turn": None,
            "last_active": time.time(),
            "profile_home": str(profile),
            "running": False,
            "session_key": "task13-tui-stored",
            "slash_worker": None,
            "source": "tui",
            "tool_progress_mode": "all",
            "transport": gateway_server._stdio_transport,
        }
        emitted: list[tuple[str, str, dict[str, Any]]] = []

        def wait_for_completions(expected: int) -> None:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                completed = [
                    event
                    for event, _sid, _payload in emitted
                    if event == "message.complete"
                ]
                if len(completed) >= expected:
                    return
                time.sleep(0.01)
            pytest.fail(f"expected {expected} completed TUI turns; events={emitted!r}")

        def wait_for_titles(expected: int) -> None:
            deadline = time.monotonic() + 2
            while len(titles) < expected and time.monotonic() < deadline:
                time.sleep(0.01)
            assert len(titles) >= expected

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    gateway_server,
                    "_emit",
                    lambda event, sid, payload=None: emitted.append((
                        event,
                        sid,
                        payload or {},
                    )),
                )
            )
            stack.enter_context(patch.object(gateway_server, "_get_db", lambda: tui_db))
            stack.enter_context(
                patch.object(
                    gateway_server, "_sync_agent_model_with_config", lambda *_args: None
                )
            )
            stack.enter_context(
                patch.object(gateway_server, "make_stream_renderer", lambda _cols: None)
            )
            stack.enter_context(
                patch.object(gateway_server, "render_message", lambda _raw, _cols: None)
            )
            stack.enter_context(
                patch(
                    "agent.title_generator.maybe_auto_title", side_effect=capture_title
                )
            )
            catalog = gateway_server.handle_request({
                "id": "catalog",
                "method": "commands.catalog",
                "params": {},
            })
            settings = gateway_server.handle_request({
                "id": "settings",
                "method": "config.get",
                "params": {"key": "full"},
            })
            first = gateway_server.handle_request({
                "id": "first",
                "method": "prompt.submit",
                "params": {"session_id": session_id, "text": "tui ordinary"},
            })
            wait_for_completions(1)
            wait_for_titles(3)
            resumed = gateway_server.handle_request({
                "id": "resume",
                "method": "session.resume",
                "params": {
                    "session_id": "task13-tui-stored",
                    "eager_build": True,
                },
            })
            resumed_id = resumed["result"]["session_id"]
            second = gateway_server.handle_request({
                "id": "second",
                "method": "prompt.submit",
                "params": {"session_id": resumed_id, "text": "tui resumed"},
            })
            wait_for_completions(2)
            wait_for_titles(4)
            closed = gateway_server.handle_request({
                "id": "close",
                "method": "session.close",
                "params": {"session_id": resumed_id},
            })

        assert "result" in catalog and "result" in settings
        assert first["result"] == {"status": "streaming"}
        assert second["result"] == {"status": "streaming"}
        assert closed["result"] == {"closed": True}
        assert (
            len([
                event
                for event, _sid, _payload in emitted
                if event == "message.complete"
            ])
            == 2
        )
    finally:
        reset_hermes_home_override(home_token)

    ordinary_requests = [
        request
        for request in model_requests[model_count:]
        if request["path"] == "/v1/chat/completions"
    ]
    assert len(ordinary_requests) == 4
    assert {request["payload"]["model"] for request in ordinary_requests} == {
        "task13-fake"
    }
    assert len(titles) == 4
    assert all(
        runtime
        == {
            "model": "task13-fake",
            "provider": "opencode-go",
            "base_url": model_url,
            "api_key": "fake-key",
            "api_mode": "chat_completions",
        }
        for runtime in titles
    )
    assert backend_requests[backend_count:] == []
    assert _memory_snapshot(profile) == before


@pytest.mark.live_system_guard_bypass
def test_spawned_serve_runs_the_real_desktop_websocket_transport_hermetically(
    tmp_path: Path,
) -> None:
    """``hades serve`` exposes the Desktop JSON-RPC gateway without a web build.

    The temporary dist deliberately contains no user artifact: it verifies the
    supported ``HERMES_WEB_DIST`` + ``--skip-build`` seam while exercising the
    same spawned server and WebSocket route the Electron app uses.
    """
    dist = tmp_path / "web-dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html><body>test</body></html>", encoding="utf-8")
    profile = tmp_path / "profile"
    token = "task13-desktop-websocket-token"
    process = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-m",
            "hermes_cli.main",
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            "0",
            "--skip-build",
        ],
        cwd=tmp_path,
        env={
            **os.environ,
            "HERMES_HOME": str(profile),
            "HERMES_WEB_DIST": str(dist),
            "HERMES_DASHBOARD_SESSION_TOKEN": token,
            "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
            "PYTHONUNBUFFERED": "1",
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    transcript: list[str] = []
    try:
        port = _wait_for_spawned_serve_port(process, transcript)
        reply = asyncio.run(_desktop_session_list_over_websocket(port, token))
    finally:
        stdout, stderr = _terminate_process(process)
        transcript.extend([stdout, stderr])

    assert reply == {
        "jsonrpc": "2.0",
        "id": "task13-sessions",
        "result": {"sessions": []},
    }
    assert process.returncode is not None
    assert "HERMES_DASHBOARD_READY port=" in "".join(transcript)


@pytest.mark.live_system_guard_bypass
@pytest.mark.parametrize("state", _MATRIX_STATES)
def test_every_canonical_row_runs_spawned_desktop_websocket_lifecycle_without_backend(
    tmp_path: Path,
    fake_model_endpoint,
    backend_fail_sentinel,
    state: str,
) -> None:
    """Every opt-in row survives the real ``serve`` + Desktop lifecycle."""
    model_url, model_requests = fake_model_endpoint
    backend_url, backend_requests = backend_fail_sentinel
    profile = tmp_path / "profile"
    memory_provider = (
        "holographic" if _MATRIX_STATES.index(state) % 2 == 0 else "builtin-only"
    )
    before = _write_lifecycle_matrix_profile(
        profile,
        state=state,
        memory_provider=memory_provider,
        model_url=model_url,
        backend_url=backend_url,
    )
    workspace = tmp_path / "desktop-workspace"
    workspace.mkdir()
    process, transcript = _spawn_serve_process(tmp_path, profile)
    backend_count = len(backend_requests)
    model_count = len(model_requests)
    try:
        port = _wait_for_spawned_serve_port(process, transcript)
        result = asyncio.run(
            _desktop_lifecycle_over_websocket(
                port,
                "task13-desktop-websocket-token",
                workspace=workspace,
                expected_backend_visible=state.startswith("enabled"),
            )
        )
    finally:
        stdout, stderr = _terminate_process(process)
        transcript.extend([stdout, stderr])

    chat_requests = [
        request
        for request in model_requests[model_count:]
        if request["path"] == "/v1/chat/completions"
    ]
    # The two streamed requests are the ordinary/resumed conversation. Title
    # generation is asynchronous and may finish once or once per persisted turn.
    assert (
        len([request for request in chat_requests if request["payload"].get("stream")])
        == 2
    )
    assert len(chat_requests) >= 3
    assert {request["payload"]["model"] for request in chat_requests} == {"task13-fake"}
    assert backend_requests[backend_count:] == []
    assert _memory_snapshot(profile) == before
    assert process.returncode is not None
    assert "HERMES_DASHBOARD_READY port=" in "".join(transcript)
    assert backend_url not in json.dumps(result["frames"])


@pytest.mark.live_system_guard_bypass
def test_spawned_desktop_keeps_holographic_and_builtin_profiles_isolated(
    tmp_path: Path,
    fake_model_endpoint,
    backend_fail_sentinel,
) -> None:
    """One Desktop backend scopes real lifecycle state to two memory profiles."""
    model_url, model_requests = fake_model_endpoint
    backend_url, backend_requests = backend_fail_sentinel
    launch_profile = tmp_path / "launch"
    launch_before = _write_lifecycle_matrix_profile(
        launch_profile,
        state="absent",
        memory_provider="builtin-only",
        model_url=model_url,
        backend_url=backend_url,
    )
    profiles = {
        "task13-holographic": "holographic",
        "task13-builtin": "builtin-only",
    }
    snapshots = {}
    for name, memory_provider in profiles.items():
        snapshots[name] = _write_lifecycle_matrix_profile(
            launch_profile / "profiles" / name,
            state="enabled-linked-a-and-b",
            memory_provider=memory_provider,
            model_url=model_url,
            backend_url=backend_url,
        )
    workspaces = {name: tmp_path / f"desktop-{name}" for name in profiles}
    for workspace in workspaces.values():
        workspace.mkdir()

    process, transcript = _spawn_serve_process(tmp_path, launch_profile)
    backend_count = len(backend_requests)
    model_count = len(model_requests)
    results: dict[str, dict[str, Any]] = {}
    try:
        port = _wait_for_spawned_serve_port(process, transcript)
        for name in profiles:
            results[name] = asyncio.run(
                _desktop_lifecycle_over_websocket(
                    port,
                    "task13-desktop-websocket-token",
                    workspace=workspaces[name],
                    expected_backend_visible=False,
                    profile=name,
                )
            )
    finally:
        stdout, stderr = _terminate_process(process)
        transcript.extend([stdout, stderr])

    chat_requests = [
        request
        for request in model_requests[model_count:]
        if request["path"] == "/v1/chat/completions"
    ]
    assert (
        len([request for request in chat_requests if request["payload"].get("stream")])
        == 4
    )
    assert len(chat_requests) >= 6
    assert {request["payload"]["model"] for request in chat_requests} == {"task13-fake"}
    assert backend_requests[backend_count:] == []
    assert _memory_snapshot(launch_profile) == launch_before
    for name in profiles:
        profile = launch_profile / "profiles" / name
        assert _memory_snapshot(profile) == snapshots[name]
        assert (profile / "state.db").is_file()
    assert (
        results["task13-holographic"]["stored_session_id"]
        != results["task13-builtin"]["stored_session_id"]
    )
    assert process.returncode is not None


def test_external_backend_plugin_registration_contract_is_opt_in_and_narrow() -> None:
    """The real standalone checkout is validated only when explicitly supplied."""
    root = _external_plugin_root_or_skip()
    entrypoint = _load_external_entrypoint(root)
    registered: dict[str, list[str]] = {
        "cli": [],
        "slash": [],
        "skill": [],
        "memory": [],
        "tools": [],
        "hooks": [],
    }

    class Context:
        def register_cli_command(self, *, name: str, **_kwargs: Any) -> None:
            registered["cli"].append(name)

        def register_command(self, name: str, *_args: Any, **_kwargs: Any) -> None:
            registered["slash"].append(name)

        def register_skill(self, name: str, *_args: Any, **_kwargs: Any) -> None:
            registered["skill"].append(name)

        def register_memory_provider(self, *args: Any, **_kwargs: Any) -> None:
            registered["memory"].append(str(args))

        def register_tool(self, *args: Any, **_kwargs: Any) -> None:
            registered["tools"].append(str(args))

        def register_hook(self, *args: Any, **_kwargs: Any) -> None:
            registered["hooks"].append(str(args))

    entrypoint.register(Context())

    assert registered == {
        "cli": ["backend"],
        "slash": ["backend"],
        "skill": ["project-knowledge"],
        "memory": [],
        "tools": [],
        "hooks": [],
    }


def test_external_backend_plugin_cli_slash_and_mcp_stay_explicit(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    """The supplied plugin exposes only explicit pairing and lazy MCP construction."""
    entrypoint = _load_external_entrypoint(_external_plugin_root_or_skip())
    package = f"{entrypoint.__name__}.hades_backend_plugin"
    cli = sys.modules[f"{package}.cli"]
    mcp = importlib.import_module(f"{package}.mcp")
    parser = __import__("argparse").ArgumentParser()
    cli.build_backend_parser(parser)
    parsed = parser.parse_args([
        "set-token",
        "--url",
        "https://fake.invalid",
        "--project-id",
        "project-a",
        "--token-stdin",
    ])
    terminal_calls: list[dict[str, Any]] = []
    bootstrap = "BOOTSTRAP_TERMINAL_CANARY"
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(f"{bootstrap}\n"))

    assert (
        cli.backend_command(parsed, pair=lambda **kwargs: terminal_calls.append(kwargs))
        == 0
    )
    terminal_output = capsys.readouterr()
    assert terminal_calls == [
        {
            "base_url": "https://fake.invalid",
            "project_id": "project-a",
            "bootstrap_token": bootstrap,
            "workspace": None,
        }
    ]
    assert bootstrap not in (terminal_output.out + terminal_output.err)

    slash_calls: list[dict[str, Any]] = []
    slash_bootstrap = "BOOTSTRAP_SLASH_CANARY"
    monkeypatch.setattr(
        cli, "pair_project", lambda **kwargs: slash_calls.append(kwargs)
    )

    class Context:
        cwd = tmp_path
        interactive = True
        surface = "desktop"

        @staticmethod
        def request_secret(_prompt: str) -> str:
            return slash_bootstrap

    result = cli.handle_backend_slash(
        "set-token --url https://fake.invalid --project-id project-a", Context()
    )
    assert result == "Backend project paired."
    assert slash_calls == [
        {
            "base_url": "https://fake.invalid",
            "project_id": "project-a",
            "bootstrap_token": slash_bootstrap,
            "workspace": tmp_path,
        }
    ]
    assert bootstrap not in result
    assert slash_bootstrap not in result

    constructed: list[object] = []

    def never_construct_service() -> object:
        constructed.append(object())
        raise AssertionError("MCP startup must not resolve a Backend client")

    mcp.create_mcp_server(service_factory=never_construct_service)
    assert constructed == []


@pytest.mark.live_system_guard_bypass
def test_external_plugin_mcp_stdio_exposes_only_project_tools_when_unlinked(
    tmp_path: Path,
) -> None:
    """The shipped MCP executable is lazy, project-only, and local when unlinked."""
    root = _external_plugin_root_or_skip()
    profile = tmp_path / "profile"
    workspace = tmp_path / "unlinked-workspace"
    workspace.mkdir()
    process = subprocess.Popen(
        [sys.executable, "-m", "hades_backend_plugin.mcp_server"],
        cwd=workspace,
        env={
            **os.environ,
            "HERMES_HOME": str(profile),
            "PYTHONPATH": str(root),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        initialized = _mcp_stdio_request(
            process,
            {
                "jsonrpc": "2.0",
                "id": "initialize",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "task13", "version": "1"},
                },
            },
        )
        assert initialized["result"]["serverInfo"]["name"] == "hades-backend"
        assert process.stdin is not None
        process.stdin.write(
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
        )
        process.stdin.flush()
        listed = _mcp_stdio_request(
            process,
            {
                "jsonrpc": "2.0",
                "id": "tools-list",
                "method": "tools/list",
                "params": {},
            },
        )
        names = {tool["name"] for tool in listed["result"]["tools"]}
        assert names == {
            "project_status",
            "project_search",
            "graph_search",
            "graph_traverse",
            "source_slice_fetch",
            "bug_evidence_search",
            "evidence_pack_search",
            "causal_pack_fetch",
            "diagnosis_report_create",
            "resolved_bug_promote",
        }
        assert not {name for name in names if "memory" in name or "sync" in name}
        unlinked = _mcp_stdio_request(
            process,
            {
                "jsonrpc": "2.0",
                "id": "unlinked",
                "method": "tools/call",
                "params": {
                    "name": "project_status",
                    "arguments": {"workspace": str(workspace)},
                },
            },
        )
        payload = json.loads(unlinked["result"]["content"][0]["text"])
        assert payload["network_attempted"] is False
    finally:
        _terminate_process(process)


@pytest.mark.live_system_guard_bypass
def test_external_plugin_mcp_stdio_query_uses_the_linked_derived_credential(
    tmp_path: Path,
) -> None:
    """A linked MCP project query resolves one persisted binding and its derived token."""
    root = _external_plugin_root_or_skip()
    entrypoint = _load_external_entrypoint(root)
    pairing = importlib.import_module(
        f"{entrypoint.__name__}.hades_backend_plugin.pairing"
    )
    profile, workspace = tmp_path / "profile", tmp_path / "linked-workspace"
    workspace.mkdir()
    calls: list[tuple[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            calls.append((self.path, self.headers.get("Authorization", "")))
            if self.path.endswith("token/verify"):
                payload: dict[str, Any] = {"project_id": "project-a", "valid": True}
            elif self.path.endswith("agents/register"):
                payload = {
                    "agent_id": "agent-a",
                    "agent_token": "DERIVED_MCP_A",
                    "capabilities": dict.fromkeys(
                        pairing.PROJECT_KNOWLEDGE_CAPABILITIES, True
                    ),
                }
            elif self.path.endswith("workspaces/bind"):
                payload = {"workspace_binding_id": "binding-a"}
            else:
                self.send_error(404)
                return
            encoded = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self) -> None:  # noqa: N802
            calls.append((self.path, self.headers.get("Authorization", "")))
            assert self.path.startswith("/api/hades/v1/memory/search?")
            encoded = json.dumps({"items": [], "count": 0}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, _format: str, *_args: Any) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        pairing.pair_project(
            base_url=f"http://127.0.0.1:{server.server_port}",
            project_id="project-a",
            bootstrap_token="BOOTSTRAP_MCP_A",
            workspace=workspace,
            profile_home=profile,
        )
        process = subprocess.Popen(
            [sys.executable, "-m", "hades_backend_plugin.mcp_server"],
            cwd=workspace,
            env={
                **os.environ,
                "HERMES_HOME": str(profile),
                "PYTHONPATH": str(root),
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            _mcp_stdio_request(
                process,
                {
                    "jsonrpc": "2.0",
                    "id": "initialize",
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "clientInfo": {"name": "task13", "version": "1"},
                    },
                },
            )
            response = _mcp_stdio_request(
                process,
                {
                    "jsonrpc": "2.0",
                    "id": "search",
                    "method": "tools/call",
                    "params": {
                        "name": "project_search",
                        "arguments": {"workspace": str(workspace), "query": "needle"},
                    },
                },
            )
        finally:
            _terminate_process(process)
    finally:
        server.shutdown()
        thread.join(timeout=2)

    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["status"] == "ok"
    assert calls[-1][1] == "Bearer DERIVED_MCP_A"
    assert "project_id=project-a" in calls[-1][0]
    assert "workspace_binding_id=binding-a" in calls[-1][0]
    assert [authorization for _path, authorization in calls[:2]] == [
        "Bearer BOOTSTRAP_MCP_A",
        "Bearer BOOTSTRAP_MCP_A",
    ]
    assert "BOOTSTRAP_MCP_A" not in (profile / ".env").read_text(encoding="utf-8")
    assert "BOOTSTRAP_MCP_A" not in (profile / "hades_backend.db").read_text(
        errors="ignore"
    )
    assert "BOOTSTRAP_MCP_A" not in repr(response)


def test_external_plugin_real_pairing_service_and_profile_routes_are_scoped(
    tmp_path: Path,
) -> None:
    """Actual plugin state selects A/B/nested C without a default-route fallback."""
    entrypoint = _load_external_entrypoint(_external_plugin_root_or_skip())
    package = f"{entrypoint.__name__}.hades_backend_plugin"
    pairing = importlib.import_module(f"{package}.pairing")
    service_module = importlib.import_module(f"{package}.service")
    workspace_module = importlib.import_module(f"{package}.workspace")
    config = importlib.import_module(f"{package}.config")

    profile = tmp_path / "profile"
    project_a = tmp_path / "workspace-a"
    project_b = tmp_path / "workspace-b"
    project_c = project_a / "nested-c"
    for workspace in (project_a, project_b, project_c):
        workspace.mkdir(parents=True)
    (project_a / "docs").mkdir(exist_ok=True)
    (project_a / "docs" / "readme.md").write_text("# A\n", encoding="utf-8")
    memory_before = {
        "config": "# comment\nmemory:\n  provider: holographic\n  custom: keep\n",
        "MEMORY.md": "memory stays local\n",
        "USER.md": "user stays local\n",
        "memory_store.db": b"memory-db-canary",
    }
    (profile / "config.yaml").parent.mkdir(parents=True)
    (profile / "config.yaml").write_text(memory_before["config"], encoding="utf-8")
    for name, value in memory_before.items():
        if name != "config":
            path = profile / name
            if isinstance(value, str):
                path.write_text(value, encoding="utf-8")
            else:
                path.write_bytes(value)

    calls: list[tuple[str, str, str]] = []

    class PairingClient:
        def __init__(self, token: str) -> None:
            self.token = token

        def verify_token(self, *, project_id: str) -> dict[str, Any]:
            calls.append(("verify", project_id, self.token))
            return {"project_id": project_id, "valid": True}

        def register_agent(self, **payload: Any) -> dict[str, Any]:
            project = str(payload["project_id"])
            calls.append(("register", project, self.token))
            return {
                "agent_id": f"agent-{project}",
                "agent_token": f"DERIVED_{project}",
                "capabilities": dict.fromkeys(
                    pairing.PROJECT_KNOWLEDGE_CAPABILITIES, True
                ),
            }

        def bind_workspace(self, **payload: Any) -> dict[str, Any]:
            project = str(payload["project_id"])
            calls.append(("bind", project, self.token))
            return {"workspace_binding_id": f"binding-{project}"}

        def close(self) -> None:
            return None

    def pair(project: str, workspace: Path) -> None:
        pairing.pair_project(
            base_url="https://fake.invalid",
            project_id=project,
            bootstrap_token=f"BOOTSTRAP_{project}",
            workspace=workspace,
            profile_home=profile,
            client_factory=lambda _url, token: PairingClient(token),
        )

    for project, workspace in (("A", project_a), ("B", project_b), ("C", project_c)):
        pair(project, workspace)
    config.configure_mcp_server(
        plugin_root=_external_plugin_root_or_skip(),
        python_executable=Path(sys.executable),
        config_path=profile / "config.yaml",
    )

    assert [name for name, _project, _token in calls] == [
        "verify",
        "register",
        "bind",
    ] * 3
    assert (
        workspace_module.resolve_linked_workspace(
            project_a / "child", profile_home=profile
        ).binding.project_id
        == "A"
    )
    assert (
        workspace_module.resolve_linked_workspace(
            project_b / "child", profile_home=profile
        ).binding.project_id
        == "B"
    )
    assert (
        workspace_module.resolve_linked_workspace(
            project_c / "child", profile_home=profile
        ).binding.project_id
        == "C"
    )
    env = (profile / ".env").read_text(encoding="utf-8")
    database = (profile / "hades_backend.db").read_bytes()
    for project in ("A", "B", "C"):
        assert env.count(f"DERIVED_{project}") == 1
        assert f"BOOTSTRAP_{project}" not in env
        assert f"BOOTSTRAP_{project}".encode() not in database
    assert "mcp_servers:\n  hades_backend:" in (profile / "config.yaml").read_text(
        encoding="utf-8"
    )
    for name, value in memory_before.items():
        path = profile / ("config.yaml" if name == "config" else name)
        assert value in (
            path.read_text(encoding="utf-8")
            if isinstance(value, str)
            else path.read_bytes()
        )

    class QueryClient:
        def __init__(self, binding) -> None:
            self.binding = binding
            self.closed = 0

        def project_search(self, **kwargs: Any) -> dict[str, Any]:
            assert kwargs["project_id"] == self.binding.project_id
            assert (
                kwargs["workspace_binding_id"]
                == self.binding.backend_workspace_binding_id
            )
            return {
                "items": [],
                "project_id": kwargs["project_id"],
                "workspace_binding_id": kwargs["workspace_binding_id"],
            }

        def close(self) -> None:
            self.closed += 1

    issued: list[QueryClient] = []
    service = service_module.ProjectKnowledgeService(
        profile_home=profile,
        client_factory=lambda binding: (
            issued.append(QueryClient(binding)) or issued[-1]
        ),
    )
    assert service.project_search(project_a / "child", "a")["project_id"] == "A"
    assert service.project_search(project_b / "child", "b")["project_id"] == "B"
    assert service.project_search(project_c / "child", "c")["project_id"] == "C"
    assert [client.closed for client in issued] == [1, 1, 1]

    with sqlite3.connect(profile / "hades_backend.db") as connection:
        connection.execute(
            "UPDATE workspace_bindings SET status = 'revoked' WHERE repo_root = ?",
            (str(project_a.resolve()),),
        )
    revoked = service.project_search(project_a / "child", "must-not-fallback")
    assert revoked["status"] == "revoked_or_auth_failed"
    assert revoked["network_attempted"] is False
    assert len(issued) == 3


def test_external_default_service_client_uses_each_profile_derived_credential(
    monkeypatch, tmp_path: Path
) -> None:
    """Default service construction never borrows a route or token from another profile."""
    entrypoint = _load_external_entrypoint(_external_plugin_root_or_skip())
    package = f"{entrypoint.__name__}.hades_backend_plugin"
    pairing = importlib.import_module(f"{package}.pairing")
    service = importlib.import_module(f"{package}.service")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    profiles = {"a": tmp_path / "profile-a", "b": tmp_path / "profile-b"}

    class PairClient:
        def __init__(self, label: str, token: str) -> None:
            self.label, self.token = label, token

        def verify_token(self, *, project_id: str) -> dict[str, Any]:
            assert (project_id, self.token) == (
                f"project-{self.label}",
                f"BOOTSTRAP_{self.label.upper()}",
            )
            return {"valid": True}

        def register_agent(self, **_kwargs: Any) -> dict[str, Any]:
            return {
                "agent_id": f"agent-{self.label}",
                "agent_token": f"DERIVED_{self.label.upper()}",
                "capabilities": dict.fromkeys(
                    pairing.PROJECT_KNOWLEDGE_CAPABILITIES, True
                ),
            }

        def bind_workspace(self, **_kwargs: Any) -> dict[str, Any]:
            return {"workspace_binding_id": f"binding-{self.label}"}

        def close(self) -> None:
            return None

    for label, profile in profiles.items():
        pairing.pair_project(
            base_url=f"https://backend-{label}.invalid",
            project_id=f"project-{label}",
            bootstrap_token=f"BOOTSTRAP_{label.upper()}",
            workspace=workspace,
            profile_home=profile,
            client_factory=lambda _url, token, label=label: PairClient(label, token),
        )

    constructed: list[tuple[str, str]] = []

    class StrictClient:
        def __init__(self, base_url: str, token: str) -> None:
            constructed.append((base_url, token))

        def project_search(self, **payload: Any) -> dict[str, Any]:
            return {"items": [], **payload}

        def close(self) -> None:
            return None

    monkeypatch.setattr(service, "BackendApiClient", StrictClient)
    assert (
        service.ProjectKnowledgeService(profile_home=profiles["a"]).project_search(
            workspace, "a"
        )["project_id"]
        == "project-a"
    )
    assert (
        service.ProjectKnowledgeService(profile_home=profiles["b"]).project_search(
            workspace, "b"
        )["project_id"]
        == "project-b"
    )
    assert constructed == [
        ("https://backend-a.invalid", "DERIVED_A"),
        ("https://backend-b.invalid", "DERIVED_B"),
    ]
    with sqlite3.connect(profiles["a"] / "hades_backend.db") as connection:
        connection.execute("UPDATE workspace_bindings SET status = 'revoked'")
        connection.commit()
    revoked = service.ProjectKnowledgeService(
        profile_home=profiles["a"]
    ).project_search(workspace, "no-fallback")
    assert (
        revoked["status"] == "revoked_or_auth_failed"
        and revoked["network_attempted"] is False
    )
    assert len(constructed) == 2


def test_external_plugin_sync_is_one_scoped_project_transaction_only(
    tmp_path: Path,
) -> None:
    """An actual plugin sync may touch only its exact project-knowledge protocol."""
    entrypoint = _load_external_entrypoint(_external_plugin_root_or_skip())
    package = f"{entrypoint.__name__}.hades_backend_plugin"
    contracts = importlib.import_module(f"{package}.contracts")
    state = importlib.import_module(f"{package}.state")
    sync = importlib.import_module(f"{package}.sync")
    workspace = tmp_path / "project"
    (workspace / "src").mkdir(parents=True)
    (workspace / "src" / "app.py").write_text("def run(): return 1\n", encoding="utf-8")
    (workspace / "docs").mkdir()
    (workspace / "docs" / "runbook.md").write_text("# Runbook\n", encoding="utf-8")
    (workspace / ".env").write_text("TOKEN=SYNC_CANARY\n", encoding="utf-8")
    binding = state.WorkspaceBinding(
        "fingerprint",
        "project-a",
        "agent-a",
        "local-a",
        "binding-a",
        str(workspace),
        str(workspace),
        "",
        "",
        "",
        "linked",
    )
    forbidden = {
        "memory_snapshot",
        "submit_memory_proposal",
        "pull_jobs",
        "list_inbox",
        "append_logbook",
        "persephone_poll",
    }

    class StrictFakeClient:
        def __init__(self) -> None:
            self.calls: set[str] = set()
            self.uploads: list[dict[str, Any]] = []
            self.closed = 0

        def __getattr__(self, name: str) -> Any:
            if name in forbidden:
                raise AssertionError(f"forbidden sync call: {name}")
            raise AttributeError(name)

        def bind_workspace(self, **_payload: Any) -> dict[str, str]:
            self.calls.add("bind_workspace")
            return {"workspace_binding_id": "binding-a"}

        def artifact_lookup(self, **_payload: Any) -> dict[str, bool]:
            self.calls.add("artifact_lookup")
            return {"exists": False}

        def upload_artifact(self, **payload: Any) -> dict[str, bool]:
            self.calls.add("upload_artifact")
            self.uploads.append(payload)
            return {"ok": True}

        def close(self) -> None:
            self.closed += 1

    client = StrictFakeClient()
    summaries: list[dict[str, Any]] = []
    result = sync.sync_project_knowledge(
        sync.ProjectSyncRequest(workspace=workspace, domain="source_index"),
        resolver=lambda _workspace: contracts.WorkspaceResolution(
            workspace, binding=binding
        ),
        client_factory=lambda _binding: client,
        summary_writer=summaries.append,
    )

    assert result["status"] == "ok"
    assert client.calls == {"bind_workspace", "artifact_lookup", "upload_artifact"}
    assert client.closed == 1
    assert len(client.uploads) == 1
    assert client.uploads[0]["schema"] == "hades.symbols.v1"
    assert not (forbidden & client.calls)
    assert summaries == [result]
    assert "SYNC_CANARY" not in repr(client.uploads)


def test_external_pairing_cannot_mutate_a_live_agent_prompt_or_tool_schema(
    monkeypatch, tmp_path: Path
) -> None:
    """Pairing is stateful, but one already-live conversation stays byte-identical."""
    entrypoint = _load_external_entrypoint(_external_plugin_root_or_skip())
    package = f"{entrypoint.__name__}.hades_backend_plugin"
    pairing = importlib.import_module(f"{package}.pairing")
    profile = tmp_path / "agent-profile"
    monkeypatch.setenv("HERMES_HOME", str(profile))
    from model_tools import get_tool_definitions
    from run_agent import AIAgent

    agent = AIAgent(
        model="deepseek-v4-flash",
        provider="opencode-go",
        base_url="http://fake.invalid/v1",
        api_key="fake-key",
        quiet_mode=True,
        session_id="task13-stable-conversation",
        skip_context_files=True,
        skip_memory=True,
    )
    prompt_before = agent._build_system_prompt().encode()
    schema_before = json.dumps(
        get_tool_definitions(quiet_mode=True), sort_keys=True, separators=(",", ":")
    ).encode()
    workspace = tmp_path / "project"
    workspace.mkdir()

    class PairingClient:
        def verify_token(self, **_kwargs: Any) -> dict[str, Any]:
            return {"project_id": "project-a", "valid": True}

        def register_agent(self, **_kwargs: Any) -> dict[str, Any]:
            return {
                "agent_id": "agent-a",
                "agent_token": "DERIVED_STABLE",
                "capabilities": dict.fromkeys(
                    pairing.PROJECT_KNOWLEDGE_CAPABILITIES, True
                ),
            }

        def bind_workspace(self, **_kwargs: Any) -> dict[str, Any]:
            return {"workspace_binding_id": "binding-a"}

        def close(self) -> None:
            return None

    pairing.pair_project(
        base_url="https://fake.invalid",
        project_id="project-a",
        bootstrap_token="BOOTSTRAP_STABLE_CANARY",
        workspace=workspace,
        profile_home=profile,
        client_factory=lambda _url, _token: PairingClient(),
    )

    assert agent._build_system_prompt().encode() == prompt_before
    schema_after = json.dumps(
        get_tool_definitions(quiet_mode=True), sort_keys=True, separators=(",", ":")
    ).encode()
    assert schema_after == schema_before
    assert (
        b"BOOTSTRAP_STABLE_CANARY" not in prompt_before + schema_before + schema_after
    )


@pytest.mark.live_system_guard_bypass
def test_external_pairing_keeps_live_agent_request_system_and_tools_byte_identical(
    monkeypatch, tmp_path: Path
) -> None:
    """A live agent's request contract does not change when same-profile pairing completes."""
    entrypoint = _load_external_entrypoint(_external_plugin_root_or_skip())
    pairing = importlib.import_module(
        f"{entrypoint.__name__}.hades_backend_plugin.pairing"
    )
    profile, workspace = tmp_path / "profile", tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(profile))
    calls: list[tuple[str, dict[str, Any]]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append((self.path, body))
            assert self.path in {"/api/show", "/v1/chat/completions"}
            response = json.dumps({
                "id": "fake",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ordinary reply"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, _format: str, *_args: Any) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    from run_agent import AIAgent

    agent = AIAgent(
        model="deepseek-v4-flash",
        provider="opencode-go",
        base_url=f"http://127.0.0.1:{server.server_port}/v1",
        api_key="fake-key",
        api_mode="chat_completions",
        quiet_mode=True,
        skip_context_files=True,
        skip_memory=True,
    )
    agent._disable_streaming = True

    class PairingClient:
        def verify_token(self, **_kwargs: Any) -> dict[str, Any]:
            return {"valid": True}

        def register_agent(self, **_kwargs: Any) -> dict[str, Any]:
            return {
                "agent_id": "agent-a",
                "agent_token": "DERIVED_ONLY",
                "capabilities": dict.fromkeys(
                    pairing.PROJECT_KNOWLEDGE_CAPABILITIES, True
                ),
            }

        def bind_workspace(self, **_kwargs: Any) -> dict[str, Any]:
            return {"workspace_binding_id": "binding-a"}

        def close(self) -> None:
            return None

    try:
        first = agent.run_conversation("first")
        pairing.pair_project(
            base_url="https://backend.invalid",
            project_id="project-a",
            bootstrap_token="BOOTSTRAP_ONLY",
            workspace=workspace,
            profile_home=profile,
            client_factory=lambda _url, _token: PairingClient(),
        )
        agent.run_conversation("second", conversation_history=first["messages"])
    finally:
        server.shutdown()
        thread.join(timeout=2)
    requests = [body for path, body in calls if path == "/v1/chat/completions"]
    assert len(requests) == 2
    shape = lambda request: json.dumps(
        {
            "system": next(
                message
                for message in request["messages"]
                if message["role"] == "system"
            ),
            "tools": request["tools"],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    assert shape(requests[1]) == shape(requests[0])
    assert "BOOTSTRAP_ONLY" not in repr(calls)


@pytest.mark.parametrize("surface", ["tui", "desktop"])
def test_external_plugin_is_discovered_by_real_manager_before_contextual_dispatch(
    monkeypatch, gateway_server, tmp_path: Path, surface: str
) -> None:
    """The actual plugin handler reaches both shared TUI/Desktop dispatchers."""
    root = _external_plugin_root_or_skip()
    from hermes_cli.plugins import PluginContext, PluginManifest

    manager = PluginManager()
    manifest = PluginManifest(
        name="hades-backend", source="user", path=str(root), key="hades-backend"
    )
    entrypoint = manager._load_directory_module(manifest)
    entrypoint.register(PluginContext(manifest, manager))
    cli = importlib.import_module(f"{entrypoint.__name__}.hades_backend_plugin.cli")
    received: list[dict[str, Any]] = []
    monkeypatch.setattr(cli, "pair_project", lambda **kwargs: received.append(kwargs))
    monkeypatch.setattr("hermes_cli.plugins._plugin_manager", manager)
    session_id = f"actual-plugin-{surface}"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    gateway_server._sessions[session_id] = {
        "session_key": session_id,
        "cwd": str(workspace),
        "source": surface,
        "agent": None,
    }
    response: dict[str, Any] = {}
    worker = threading.Thread(
        target=lambda: response.update(
            gateway_server.handle_request({
                "id": session_id,
                "method": "slash.exec",
                "params": {
                    "command": "backend set-token --url https://fake.invalid --project-id project-a",
                    "session_id": session_id,
                },
            })
        )
    )
    worker.start()
    request_id = _wait_for_owned_secret_request(gateway_server, session_id)
    canary = "BOOTSTRAP_HOST_DISPATCH_CANARY"
    gateway_server.handle_request({
        "id": "secret-response",
        "method": "secret.respond",
        "params": {"request_id": request_id, "session_id": session_id, "value": canary},
    })
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert response["result"]["output"] == "Backend project paired."
    assert repr(response).find(canary) == -1
    assert received == [
        {
            "base_url": "https://fake.invalid",
            "project_id": "project-a",
            "bootstrap_token": canary,
            "workspace": workspace,
        }
    ]


@pytest.mark.live_system_guard_bypass
@pytest.mark.parametrize("memory_provider", ["holographic", "builtin-only"])
def test_external_classic_masked_pair_cancel_success_query_and_sync_are_isolated(
    monkeypatch,
    tmp_path: Path,
    capsys,
    external_backend_endpoint,
    memory_provider: str,
) -> None:
    """The classic masked callback persists only a derived project credential."""
    import argparse
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    root = _external_plugin_root_or_skip()
    entrypoint = _load_external_entrypoint(root)
    cli = importlib.import_module(f"{entrypoint.__name__}.hades_backend_plugin.cli")
    backend_url, backend_calls = external_backend_endpoint
    profile = tmp_path / f"classic-{memory_provider}"
    before = _prepare_external_profile(profile, root, memory_provider=memory_provider)
    workspace = tmp_path / f"classic-workspace-{memory_provider}"
    (workspace / "src").mkdir(parents=True)
    (workspace / "src" / "main.py").write_text("VALUE = 13\n", encoding="utf-8")
    project = f"classic-{memory_provider}"
    bootstrap = f"BOOTSTRAP_CLASSIC_{memory_provider.upper().replace('-', '_')}"
    derived = f"DERIVED_{project.upper().replace('-', '_')}"
    parser = argparse.ArgumentParser()
    cli.build_backend_parser(parser)
    args = parser.parse_args([
        "set-token",
        "--url",
        backend_url,
        "--project-id",
        project,
        "--workspace",
        str(workspace),
    ])
    monkeypatch.setenv("HERMES_HOME", str(profile))
    home_token = set_hermes_home_override(profile)
    try:
        monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: "")
        assert cli.backend_command(args) == 2
        cancelled = capsys.readouterr()
        assert backend_calls == []
        assert not (profile / ".env").exists()
        assert not (profile / "hades_backend.db").exists()
        assert _memory_snapshot(profile) == before

        monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: bootstrap)
        assert cli.backend_command(args) == 0
        paired = capsys.readouterr()
    finally:
        reset_hermes_home_override(home_token)

    query, synced = _exercise_external_query_and_sync(
        root, profile=profile, workspace=workspace
    )
    assert [call["path"] for call in backend_calls[:3]] == [
        "/api/hades/v1/token/verify",
        "/api/hades/v1/agents/register",
        "/api/hades/v1/workspaces/bind",
    ]
    assert [call["authorization"] for call in backend_calls[:3]] == [
        f"Bearer {bootstrap}",
        f"Bearer {bootstrap}",
        f"Bearer {derived}",
    ]
    assert _memory_snapshot(profile) == before
    _assert_external_secret_hygiene(
        profile,
        bootstrap=bootstrap,
        derived=derived,
        sinks={
            "cancelled": cancelled,
            "paired": paired,
            "args": vars(args),
            "query": query,
            "sync": synced,
            "environment": {
                key: value
                for key, value in os.environ.items()
                if key.startswith(("HERMES_", "HADES_"))
            },
        },
    )


@pytest.mark.live_system_guard_bypass
def test_external_tui_overlay_pair_cancel_success_is_persisted_and_secret_safe(
    monkeypatch,
    gateway_server,
    tmp_path: Path,
    external_backend_endpoint,
) -> None:
    """The actual external handler owns cancellation and success through TUI overlay."""
    root = _external_plugin_root_or_skip()
    manager = PluginManager()
    manifest = PluginManifest(
        name="hades-backend", source="user", path=str(root), key="hades-backend"
    )
    entrypoint = manager._load_directory_module(manifest)
    entrypoint.register(PluginContext(manifest, manager))
    monkeypatch.setattr("hermes_cli.plugins._plugin_manager", manager)
    backend_url, backend_calls = external_backend_endpoint
    profile = tmp_path / "tui-profile"
    before = _prepare_external_profile(profile, root, memory_provider="builtin-only")
    workspace = tmp_path / "tui-workspace"
    (workspace / "src").mkdir(parents=True)
    (workspace / "src" / "main.py").write_text("VALUE = 13\n", encoding="utf-8")
    session_id = "task13-external-tui"
    gateway_server._sessions[session_id] = {
        "session_key": session_id,
        "cwd": str(workspace),
        "source": "tui",
        "profile_home": str(profile),
        "agent": None,
    }
    project = "tui-project"
    bootstrap = "BOOTSTRAP_TUI_OVERLAY_CANARY"
    derived = "DERIVED_TUI_PROJECT"
    command = (
        f"backend set-token --url {backend_url} --project-id {project} "
        f"--workspace {workspace}"
    )

    def invoke(response: dict[str, Any], request_id: str) -> threading.Thread:
        worker = threading.Thread(
            target=lambda: response.update(
                gateway_server.handle_request({
                    "id": request_id,
                    "method": "slash.exec",
                    "params": {"command": command, "session_id": session_id},
                })
            )
        )
        worker.start()
        return worker

    cancelled: dict[str, Any] = {}
    worker = invoke(cancelled, "task13-tui-cancel")
    request_id = _wait_for_owned_secret_request(gateway_server, session_id)
    gateway_server.handle_request({
        "id": "task13-tui-cancel-secret",
        "method": "secret.respond",
        "params": {"request_id": request_id, "session_id": session_id, "value": ""},
    })
    worker.join(timeout=5)
    assert cancelled["result"]["output"] == "Backend pairing cancelled."
    assert backend_calls == []
    assert not (profile / ".env").exists()
    assert not (profile / "hades_backend.db").exists()

    paired: dict[str, Any] = {}
    worker = invoke(paired, "task13-tui-success")
    request_id = _wait_for_owned_secret_request(gateway_server, session_id)
    gateway_server.handle_request({
        "id": "task13-tui-success-secret",
        "method": "secret.respond",
        "params": {
            "request_id": request_id,
            "session_id": session_id,
            "value": bootstrap,
        },
    })
    worker.join(timeout=10)
    assert not worker.is_alive()
    assert paired["result"]["output"] == "Backend project paired."
    assert _memory_snapshot(profile) == before
    _assert_external_secret_hygiene(
        profile,
        bootstrap=bootstrap,
        derived=derived,
        sinks={"cancelled": cancelled, "paired": paired, "command": command},
    )


@pytest.mark.live_system_guard_bypass
def test_external_spawned_desktop_overlay_pair_cancel_success_is_persisted_and_safe(
    tmp_path: Path,
    fake_model_endpoint,
    external_backend_endpoint,
) -> None:
    """The real Desktop WebSocket overlay composes with external persistence."""
    root = _external_plugin_root_or_skip()
    model_url, _model_calls = fake_model_endpoint
    backend_url, backend_calls = external_backend_endpoint
    profile = tmp_path / "desktop-external-profile"
    before = _prepare_external_profile(
        profile,
        root,
        memory_provider="holographic",
        model_url=model_url,
    )
    workspace = tmp_path / "desktop-external-workspace"
    (workspace / "src").mkdir(parents=True)
    (workspace / "src" / "main.py").write_text("VALUE = 13\n", encoding="utf-8")
    project = "desktop-project"
    bootstrap = "BOOTSTRAP_DESKTOP_OVERLAY_CANARY"
    derived = "DERIVED_DESKTOP_PROJECT"
    process, transcript = _spawn_serve_process(tmp_path, profile)
    try:
        port = _wait_for_spawned_serve_port(process, transcript)
        result = asyncio.run(
            _desktop_pair_over_websocket(
                port,
                "task13-desktop-websocket-token",
                workspace=workspace,
                backend_url=backend_url,
                project_id=project,
                bootstrap=bootstrap,
            )
        )
    finally:
        stdout, stderr = _terminate_process(process)
        transcript.extend([stdout, stderr])

    query, synced = _exercise_external_query_and_sync(
        root, profile=profile, workspace=workspace
    )
    assert result["cancelled"] == "Backend pairing cancelled."
    assert result["paired"] == "Backend project paired."
    assert [call["path"] for call in backend_calls[:3]] == [
        "/api/hades/v1/token/verify",
        "/api/hades/v1/agents/register",
        "/api/hades/v1/workspaces/bind",
    ]
    assert _memory_snapshot(profile) == before
    _assert_external_secret_hygiene(
        profile,
        bootstrap=bootstrap,
        derived=derived,
        sinks={
            "frames": result["frames"],
            "stdout_stderr": transcript,
            "query": query,
            "sync": synced,
            "argv": ["serve", "--host", "127.0.0.1", "--port", "0"],
        },
    )
    assert process.returncode is not None


def test_external_plugin_terminal_pairing_uses_only_fake_verify_register_bind(
    tmp_path: Path,
) -> None:
    """A real host subprocess may pair only through stdin and the three explicit HTTP calls."""
    root = _external_plugin_root_or_skip()
    profile = tmp_path / "profile"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    plugin_dir = profile / "plugins"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "hades-backend").symlink_to(root, target_is_directory=True)
    (profile / "config.yaml").write_text(
        "plugins:\n  enabled: [hades-backend]\n", encoding="utf-8"
    )
    calls: list[tuple[str, str]] = []

    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append((self.path, self.headers.get("Authorization", "")))
            if self.path.endswith("token/verify"):
                response: dict[str, Any] = {
                    "project_id": body["project_id"],
                    "valid": True,
                }
            elif self.path.endswith("agents/register"):
                response = {
                    "agent_id": "agent-a",
                    "agent_token": "DERIVED_TERMINAL_A",
                    "capabilities": {
                        key: True
                        for key in (
                            "read_files",
                            "read_source_slice",
                            "project_inspection",
                            "sync_git_tree",
                            "populate_backend_ast",
                            "populate_project_wiki",
                            "verify_project_wiki",
                            "write_project_logbook",
                        )
                    },
                }
            elif self.path.endswith("workspaces/bind"):
                response = {"workspace_binding_id": "binding-a"}
            else:  # pragma: no cover - protocol diagnostic
                self.send_error(404)
                return
            encoded = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, _format: str, *_args: Any) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    bootstrap = "BOOTSTRAP_TERMINAL_PROCESS_CANARY"
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "hermes_cli.main",
                "backend",
                "set-token",
                "--url",
                f"http://127.0.0.1:{server.server_port}",
                "--project-id",
                "project-a",
                "--workspace",
                str(workspace),
                "--token-stdin",
            ],
            cwd=workspace,
            env={
                **os.environ,
                "HERMES_HOME": str(profile),
                "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
            },
            input=f"{bootstrap}\n",
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)

    assert result.returncode == 0, result.stderr
    assert bootstrap not in (result.stdout + result.stderr)
    assert calls == [
        ("/api/hades/v1/token/verify", f"Bearer {bootstrap}"),
        ("/api/hades/v1/agents/register", f"Bearer {bootstrap}"),
        ("/api/hades/v1/workspaces/bind", "Bearer DERIVED_TERMINAL_A"),
    ]
    assert (profile / ".env").read_text(encoding="utf-8").count(
        "DERIVED_TERMINAL_A"
    ) == 1
    assert bootstrap not in (profile / "hades_backend.db").read_text(errors="ignore")


def _wait_for_owned_secret_request(server: ModuleType, session_id: str) -> str:
    for _ in range(200):
        with server._prompt_lock:
            requests = [
                request_id
                for request_id, (owner, _event) in server._pending.items()
                if owner == session_id
            ]
        if requests:
            return requests[0]
        time.sleep(0.01)
    pytest.fail(f"secret.request was not emitted for {session_id}")


def _mcp_stdio_request(
    process: subprocess.Popen[str], message: dict[str, Any]
) -> dict[str, Any]:
    assert process.stdin is not None
    assert process.stdout is not None
    process.stdin.write(json.dumps(message) + "\n")
    process.stdin.flush()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        ready, _, _ = select.select([process.stdout], [], [], 0.1)
        if ready:
            response = json.loads(process.stdout.readline())
            if response.get("id") == message["id"]:
                return response
        if process.poll() is not None:
            break
    stderr = process.stderr.read() if process.stderr is not None else ""
    pytest.fail(f"MCP stdio request did not return: {message['id']}; stderr: {stderr}")


def _terminate_process(process: subprocess.Popen[str]) -> tuple[str, str]:
    process.terminate()
    try:
        return process.communicate(timeout=2)
    except subprocess.TimeoutExpired:  # pragma: no cover - failure cleanup
        process.kill()
        return process.communicate(timeout=2)


def _spawn_serve_process(
    tmp_path: Path, profile: Path
) -> tuple[subprocess.Popen[str], list[str]]:
    dist = tmp_path / "web-dist"
    (dist / "assets").mkdir(parents=True, exist_ok=True)
    (dist / "index.html").write_text(
        "<html><body>task13</body></html>", encoding="utf-8"
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-m",
            "hermes_cli.main",
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            "0",
            "--skip-build",
        ],
        cwd=tmp_path,
        env={
            **os.environ,
            "HERMES_HOME": str(profile),
            "HERMES_WEB_DIST": str(dist),
            "HERMES_DASHBOARD_SESSION_TOKEN": "task13-desktop-websocket-token",
            "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
            "PYTHONUNBUFFERED": "1",
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return process, []


def _wait_for_spawned_serve_port(
    process: subprocess.Popen[str], transcript: list[str]
) -> int:
    assert process.stdout is not None
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        ready, _, _ = select.select([process.stdout], [], [], 0.1)
        if ready:
            line = process.stdout.readline()
            transcript.append(line)
            if line.startswith("HERMES_DASHBOARD_READY port="):
                return int(line.rsplit("=", 1)[1])
        if process.poll() is not None:
            break
    stderr = process.stderr.read() if process.stderr is not None else ""
    exit_status = process.poll()
    pytest.fail(
        "hades serve did not announce a ready port; output: "
        f"{''.join(transcript)} stderr: {stderr} exit_status: {exit_status}"
    )


async def _desktop_session_list_over_websocket(port: int, token: str) -> dict[str, Any]:
    from websockets.asyncio.client import connect

    async with connect(
        f"ws://127.0.0.1:{port}/api/ws?token={token}", open_timeout=10
    ) as websocket:
        ready = json.loads(await asyncio.wait_for(websocket.recv(), timeout=10))
        assert ready["params"]["type"] == "gateway.ready"
        await websocket.send(
            json.dumps({
                "jsonrpc": "2.0",
                "id": "task13-sessions",
                "method": "session.list",
                "params": {},
            })
        )
        while True:
            message = json.loads(await asyncio.wait_for(websocket.recv(), timeout=10))
            if message.get("id") == "task13-sessions":
                return message


async def _desktop_pair_over_websocket(
    port: int,
    token: str,
    *,
    workspace: Path,
    backend_url: str,
    project_id: str,
    bootstrap: str,
) -> dict[str, Any]:
    from websockets.asyncio.client import connect

    frames: list[dict[str, Any]] = []
    counter = 0
    async with connect(
        f"ws://127.0.0.1:{port}/api/ws?token={token}", open_timeout=10
    ) as websocket:
        frames.append(json.loads(await asyncio.wait_for(websocket.recv(), timeout=10)))

        async def request(method: str, params: dict[str, Any]) -> dict[str, Any]:
            nonlocal counter
            counter += 1
            request_id = f"task13-desktop-pair-{counter}"
            await websocket.send(
                json.dumps({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params,
                })
            )
            while True:
                message = json.loads(
                    await asyncio.wait_for(websocket.recv(), timeout=30)
                )
                frames.append(message)
                if message.get("id") == request_id:
                    return message

        catalog = await request("commands.catalog", {})
        assert "/backend" in dict(catalog["result"]["pairs"])
        created = await request(
            "session.create",
            {
                "cwd": str(workspace),
                "source": "desktop",
                "model": "task13-fake",
                "provider": "custom",
            },
        )
        session_id = created["result"]["session_id"]
        command = (
            f"backend set-token --url {backend_url} --project-id {project_id} "
            f"--workspace {workspace}"
        )

        async def pair(value: str, label: str) -> str:
            slash_id = f"task13-desktop-{label}"
            secret_response_id = f"task13-desktop-{label}-secret"
            await websocket.send(
                json.dumps({
                    "jsonrpc": "2.0",
                    "id": slash_id,
                    "method": "slash.exec",
                    "params": {"command": command, "session_id": session_id},
                })
            )
            secret_reply_seen = False
            while True:
                message = json.loads(
                    await asyncio.wait_for(websocket.recv(), timeout=30)
                )
                frames.append(message)
                params = message.get("params", {})
                if params.get("type") == "secret.request":
                    request_id = params["payload"]["request_id"]
                    assert params["session_id"] == session_id
                    await websocket.send(
                        json.dumps({
                            "jsonrpc": "2.0",
                            "id": secret_response_id,
                            "method": "secret.respond",
                            "params": {
                                "request_id": request_id,
                                "session_id": session_id,
                                "value": value,
                            },
                        })
                    )
                elif message.get("id") == secret_response_id:
                    assert message["result"] == {"status": "ok"}
                    secret_reply_seen = True
                elif message.get("id") == slash_id:
                    assert secret_reply_seen
                    return message["result"]["output"]

        cancelled = await pair("", "cancel")
        paired = await pair(bootstrap, "success")
        closed = await request("session.close", {"session_id": session_id})
        assert closed["result"] == {"closed": True}

    return {"frames": frames, "cancelled": cancelled, "paired": paired}


async def _desktop_lifecycle_over_websocket(
    port: int,
    token: str,
    *,
    workspace: Path,
    expected_backend_visible: bool,
    profile: str | None = None,
) -> dict[str, Any]:
    """Drive the real Desktop create/turn/title/close/resume/shutdown contract."""
    from websockets.asyncio.client import connect

    frames: list[dict[str, Any]] = []
    pending_events: list[dict[str, Any]] = []
    counter = 0

    async with connect(
        f"ws://127.0.0.1:{port}/api/ws?token={token}", open_timeout=10
    ) as websocket:
        ready = json.loads(await asyncio.wait_for(websocket.recv(), timeout=10))
        frames.append(ready)
        assert ready["params"]["type"] == "gateway.ready"

        async def request(method: str, params: dict[str, Any]) -> dict[str, Any]:
            nonlocal counter
            counter += 1
            request_id = f"task13-{counter}-{method}"
            await websocket.send(
                json.dumps({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params,
                })
            )
            while True:
                message = json.loads(
                    await asyncio.wait_for(websocket.recv(), timeout=30)
                )
                frames.append(message)
                if message.get("id") == request_id:
                    return message
                pending_events.append(message)

        async def wait_event(event_type: str, session_id: str) -> dict[str, Any]:
            for index, message in enumerate(pending_events):
                params = message.get("params", {})
                if (
                    params.get("type") == event_type
                    and params.get("session_id") == session_id
                ):
                    return pending_events.pop(index)
            while True:
                message = json.loads(
                    await asyncio.wait_for(websocket.recv(), timeout=30)
                )
                frames.append(message)
                params = message.get("params", {})
                if (
                    params.get("type") == event_type
                    and params.get("session_id") == session_id
                ):
                    return message
                pending_events.append(message)

        catalog = await request("commands.catalog", {})
        pairs = dict(catalog["result"]["pairs"])
        assert ("/backend" in pairs) is expected_backend_visible
        settings = await request("config.get", {"key": "full"})
        assert settings["result"]["config"]["model"]["default"] == "task13-fake"
        create_params: dict[str, Any] = {
            "cwd": str(workspace),
            "source": "desktop",
            "model": "task13-fake",
            "provider": "custom",
        }
        if profile:
            create_params["profile"] = profile
        created = await request("session.create", create_params)
        live_id = created["result"]["session_id"]
        stored_id = created["result"]["stored_session_id"]
        first = await request(
            "prompt.submit",
            {"session_id": live_id, "text": "desktop ordinary"},
        )
        assert first["result"] == {"status": "streaming"}
        first_complete = await wait_event("message.complete", live_id)
        assert first_complete["params"]["payload"]["text"] == (
            "ordinary fake-model response"
        )
        await wait_event("session.title", live_id)
        closed_first = await request("session.close", {"session_id": live_id})
        assert closed_first["result"] == {"closed": True}

        resume_params: dict[str, Any] = {
            "session_id": stored_id,
            "eager_build": True,
        }
        if profile:
            resume_params["profile"] = profile
        resumed = await request("session.resume", resume_params)
        resumed_id = resumed["result"]["session_id"]
        assert resumed["result"]["message_count"] >= 2
        second = await request(
            "prompt.submit",
            {"session_id": resumed_id, "text": "desktop resumed"},
        )
        assert second["result"] == {"status": "streaming"}
        second_complete = await wait_event("message.complete", resumed_id)
        assert second_complete["params"]["payload"]["text"] == (
            "ordinary fake-model response"
        )
        closed_second = await request("session.close", {"session_id": resumed_id})
        assert closed_second["result"] == {"closed": True}
        if profile is None:
            listed = await request("session.list", {})
            assert any(row["id"] == stored_id for row in listed["result"]["sessions"])

    return {"frames": frames, "stored_session_id": stored_id}


def _external_plugin_root_or_skip() -> Path:
    raw = os.environ.get("HADES_BACKEND_PLUGIN_REPO")
    if not raw:
        pytest.skip(
            "set HADES_BACKEND_PLUGIN_REPO to validate the standalone plugin contract"
        )
    root = Path(raw).expanduser().resolve()
    if not (root / "plugin.yaml").is_file() or not (root / "__init__.py").is_file():
        pytest.skip(f"HADES_BACKEND_PLUGIN_REPO is not a plugin checkout: {root}")
    return root


def _load_external_entrypoint(root: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "task13_external_hades_backend_plugin",
        root / "__init__.py",
        submodule_search_locations=[str(root)],
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
    return module
