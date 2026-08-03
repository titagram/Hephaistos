"""Cross-surface contracts for the optional Hades Backend plugin.

These checks deliberately keep the ordinary host matrix self-contained.  A
developer can additionally set ``HADES_BACKEND_PLUGIN_REPO`` to validate the
released plugin checkout without making a sibling repository a CI dependency.
"""

from __future__ import annotations

import importlib.util
import io
import os
from pathlib import Path
import sys
import threading
import time
from types import ModuleType
from typing import Any

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
