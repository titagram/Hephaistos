"""Cross-surface contracts for the optional Hades Backend plugin.

These checks deliberately keep the ordinary host matrix self-contained.  A
developer can additionally set ``HADES_BACKEND_PLUGIN_REPO`` to validate the
released plugin checkout without making a sibling repository a CI dependency.
"""

from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import os
from pathlib import Path
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
    profiles = {"success": tmp_path / "profile-success", "cancel": tmp_path / "profile-cancel"}
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
    context = PluginContext(PluginManifest(name="profile-probe", source="user"), manager)
    context.register_command("profile-probe", profile_handler, "Profile probe")
    monkeypatch.setattr("hermes_cli.plugins._plugin_manager", manager)
    for label, profile in profiles.items():
        gateway_server._sessions[f"desktop-{label}"] = {
            "agent": None, "cwd": str(tmp_path), "profile_home": str(profile),
            "session_key": f"desktop-{label}", "source": "desktop",
        }

    responses: dict[str, dict[str, Any]] = {}
    workers = [threading.Thread(target=lambda label=label: responses.setdefault(label, gateway_server.handle_request({
        "id": f"{label}-command", "method": "slash.exec",
        "params": {"session_id": f"desktop-{label}", "command": "profile-probe"},
    }))) for label in profiles]
    for worker in workers:
        worker.start()
    request_ids = {label: _wait_for_owned_secret_request(gateway_server, f"desktop-{label}") for label in profiles}
    for label, value in (("success", "DERIVED_SUCCESS_ONLY"), ("cancel", "")):
        gateway_server.handle_request({
            "id": f"{label}-secret", "method": "secret.respond",
            "params": {"request_id": request_ids[label], "session_id": f"desktop-{label}", "value": value},
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
        lambda kwargs: resolved.append(kwargs) or {
            "provider": "opencode-go",
            "base_url": "http://fake.invalid/v1",
            "api_key": "fake-key",
            "api_mode": "chat_completions",
            "credential_pool": None,
        },
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
    assert [agent["provider"] for agent in constructed] == ["opencode-go", "opencode-go"]
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
    monkeypatch.setattr(gateway_server, "_sync_agent_model_with_config", lambda *_args: None)
    monkeypatch.setattr(gateway_server, "make_stream_renderer", lambda _cols: None)
    monkeypatch.setattr(gateway_server, "render_message", lambda _raw, _cols: None)

    with patch("agent.title_generator.maybe_auto_title") as title:
        response = gateway_server.handle_request({
            "id": f"{surface}-prompt",
            "method": "prompt.submit",
            "params": {"session_id": session_id, "text": "ordinary question"},
        })

    assert response["result"] == {"status": "streaming"}
    assert received == [{
        "prompt": "ordinary question",
        "history": [],
        "task_id": f"{surface}-stored-session",
    }]
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
    assert all(call.kwargs["main_runtime"] == {
        "model": "deepseek-v4-flash",
        "provider": "opencode-go",
        "base_url": "http://fake.invalid/v1",
        "api_key": "fake-key",
        "api_mode": "chat_completions",
    } for call in title.call_args_list)


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

    assert reply == {"jsonrpc": "2.0", "id": "task13-sessions", "result": {"sessions": []}}
    assert process.returncode is not None
    assert "HERMES_DASHBOARD_READY port=" in "".join(transcript)


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
        initialized = _mcp_stdio_request(process, {
            "jsonrpc": "2.0",
            "id": "initialize",
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "task13", "version": "1"},
            },
        })
        assert initialized["result"]["serverInfo"]["name"] == "hades-backend"
        assert process.stdin is not None
        process.stdin.write(
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
            + "\n"
        )
        process.stdin.flush()
        listed = _mcp_stdio_request(process, {
            "jsonrpc": "2.0",
            "id": "tools-list",
            "method": "tools/list",
            "params": {},
        })
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
        unlinked = _mcp_stdio_request(process, {
            "jsonrpc": "2.0",
            "id": "unlinked",
            "method": "tools/call",
            "params": {
                "name": "project_status",
                "arguments": {"workspace": str(workspace)},
            },
        })
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
    pairing = importlib.import_module(f"{entrypoint.__name__}.hades_backend_plugin.pairing")
    profile, workspace = tmp_path / "profile", tmp_path / "linked-workspace"
    workspace.mkdir()
    calls: list[tuple[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            calls.append((self.path, self.headers.get("Authorization", "")))
            if self.path.endswith("token/verify"):
                payload: dict[str, Any] = {"project_id": "project-a", "valid": True}
            elif self.path.endswith("agents/register"):
                payload = {"agent_id": "agent-a", "agent_token": "DERIVED_MCP_A", "capabilities": dict.fromkeys(pairing.PROJECT_KNOWLEDGE_CAPABILITIES, True)}
            elif self.path.endswith("workspaces/bind"):
                payload = {"workspace_binding_id": "binding-a"}
            else:
                self.send_error(404)
                return
            encoded = json.dumps(payload).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(encoded))); self.end_headers(); self.wfile.write(encoded)

        def do_GET(self) -> None:  # noqa: N802
            calls.append((self.path, self.headers.get("Authorization", "")))
            assert self.path.startswith("/api/hades/v1/memory/search?")
            encoded = json.dumps({"items": [], "count": 0}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(encoded))); self.end_headers(); self.wfile.write(encoded)

        def log_message(self, _format: str, *_args: Any) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        pairing.pair_project(base_url=f"http://127.0.0.1:{server.server_port}", project_id="project-a", bootstrap_token="BOOTSTRAP_MCP_A", workspace=workspace, profile_home=profile)
        process = subprocess.Popen([sys.executable, "-m", "hades_backend_plugin.mcp_server"], cwd=workspace, env={**os.environ, "HERMES_HOME": str(profile), "PYTHONPATH": str(root), "PYTHONDONTWRITEBYTECODE": "1"}, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            _mcp_stdio_request(process, {"jsonrpc": "2.0", "id": "initialize", "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "task13", "version": "1"}}})
            response = _mcp_stdio_request(process, {"jsonrpc": "2.0", "id": "search", "method": "tools/call", "params": {"name": "project_search", "arguments": {"workspace": str(workspace), "query": "needle"}}})
        finally:
            _terminate_process(process)
    finally:
        server.shutdown(); thread.join(timeout=2)

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
    assert "BOOTSTRAP_MCP_A" not in (profile / "hades_backend.db").read_text(errors="ignore")
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
    workspace = tmp_path / "workspace"; workspace.mkdir()
    profiles = {"a": tmp_path / "profile-a", "b": tmp_path / "profile-b"}

    class PairClient:
        def __init__(self, label: str, token: str) -> None: self.label, self.token = label, token
        def verify_token(self, *, project_id: str) -> dict[str, Any]:
            assert (project_id, self.token) == (f"project-{self.label}", f"BOOTSTRAP_{self.label.upper()}")
            return {"valid": True}
        def register_agent(self, **_kwargs: Any) -> dict[str, Any]:
            return {"agent_id": f"agent-{self.label}", "agent_token": f"DERIVED_{self.label.upper()}", "capabilities": dict.fromkeys(pairing.PROJECT_KNOWLEDGE_CAPABILITIES, True)}
        def bind_workspace(self, **_kwargs: Any) -> dict[str, Any]: return {"workspace_binding_id": f"binding-{self.label}"}
        def close(self) -> None: return None

    for label, profile in profiles.items():
        pairing.pair_project(base_url=f"https://backend-{label}.invalid", project_id=f"project-{label}", bootstrap_token=f"BOOTSTRAP_{label.upper()}", workspace=workspace, profile_home=profile, client_factory=lambda _url, token, label=label: PairClient(label, token))

    constructed: list[tuple[str, str]] = []
    class StrictClient:
        def __init__(self, base_url: str, token: str) -> None: constructed.append((base_url, token))
        def project_search(self, **payload: Any) -> dict[str, Any]: return {"items": [], **payload}
        def close(self) -> None: return None

    monkeypatch.setattr(service, "BackendApiClient", StrictClient)
    assert service.ProjectKnowledgeService(profile_home=profiles["a"]).project_search(workspace, "a")["project_id"] == "project-a"
    assert service.ProjectKnowledgeService(profile_home=profiles["b"]).project_search(workspace, "b")["project_id"] == "project-b"
    assert constructed == [("https://backend-a.invalid", "DERIVED_A"), ("https://backend-b.invalid", "DERIVED_B")]
    with sqlite3.connect(profiles["a"] / "hades_backend.db") as connection:
        connection.execute("UPDATE workspace_bindings SET status = 'revoked'")
        connection.commit()
    revoked = service.ProjectKnowledgeService(profile_home=profiles["a"]).project_search(workspace, "no-fallback")
    assert revoked["status"] == "revoked_or_auth_failed" and revoked["network_attempted"] is False
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
        resolver=lambda _workspace: contracts.WorkspaceResolution(workspace, binding=binding),
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
                "capabilities": dict.fromkeys(pairing.PROJECT_KNOWLEDGE_CAPABILITIES, True),
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
    assert b"BOOTSTRAP_STABLE_CANARY" not in prompt_before + schema_before + schema_after


@pytest.mark.live_system_guard_bypass
def test_external_pairing_keeps_live_agent_request_system_and_tools_byte_identical(monkeypatch, tmp_path: Path) -> None:
    """A live agent's request contract does not change when same-profile pairing completes."""
    entrypoint = _load_external_entrypoint(_external_plugin_root_or_skip())
    pairing = importlib.import_module(f"{entrypoint.__name__}.hades_backend_plugin.pairing")
    profile, workspace = tmp_path / "profile", tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(profile))
    calls: list[tuple[str, dict[str, Any]]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append((self.path, body))
            assert self.path in {"/api/show", "/v1/chat/completions"}
            response = json.dumps({"id": "fake", "object": "chat.completion", "choices": [{"index": 0, "message": {"role": "assistant", "content": "ordinary reply"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(response))); self.end_headers(); self.wfile.write(response)
        def log_message(self, _format: str, *_args: Any) -> None: return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    from run_agent import AIAgent
    agent = AIAgent(model="deepseek-v4-flash", provider="opencode-go", base_url=f"http://127.0.0.1:{server.server_port}/v1", api_key="fake-key", api_mode="chat_completions", quiet_mode=True, skip_context_files=True, skip_memory=True)
    agent._disable_streaming = True

    class PairingClient:
        def verify_token(self, **_kwargs: Any) -> dict[str, Any]: return {"valid": True}
        def register_agent(self, **_kwargs: Any) -> dict[str, Any]: return {"agent_id": "agent-a", "agent_token": "DERIVED_ONLY", "capabilities": dict.fromkeys(pairing.PROJECT_KNOWLEDGE_CAPABILITIES, True)}
        def bind_workspace(self, **_kwargs: Any) -> dict[str, Any]: return {"workspace_binding_id": "binding-a"}
        def close(self) -> None: return None

    try:
        first = agent.run_conversation("first")
        pairing.pair_project(base_url="https://backend.invalid", project_id="project-a", bootstrap_token="BOOTSTRAP_ONLY", workspace=workspace, profile_home=profile, client_factory=lambda _url, _token: PairingClient())
        agent.run_conversation("second", conversation_history=first["messages"])
    finally:
        server.shutdown(); thread.join(timeout=2)
    requests = [body for path, body in calls if path == "/v1/chat/completions"]
    assert len(requests) == 2
    shape = lambda request: json.dumps({"system": next(message for message in request["messages"] if message["role"] == "system"), "tools": request["tools"]}, sort_keys=True, separators=(",", ":")).encode()
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
        await websocket.send(json.dumps({
            "jsonrpc": "2.0",
            "id": "task13-sessions",
            "method": "session.list",
            "params": {},
        }))
        while True:
            message = json.loads(await asyncio.wait_for(websocket.recv(), timeout=10))
            if message.get("id") == "task13-sessions":
                return message


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
