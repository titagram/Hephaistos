"""Contract tests for contextual plugin slash commands.

Each test names a host change that would break a plugin command: calling a
legacy handler with an extra argument, losing the per-session workspace, or
letting a secret cross a JSON-RPC session boundary.
"""

from __future__ import annotations

from pathlib import Path
import threading
import time
import importlib

import pytest

from hermes_cli.plugin_command_context import (
    PluginCommandContext,
    create_plugin_command_context,
)
from hermes_cli.plugins import (
    PluginContext,
    PluginManager,
    PluginManifest,
    invoke_plugin_command,
)


@pytest.fixture()
def server():
    """Use the real JSON-RPC prompt broker with clean session-scoped state."""
    module = importlib.import_module("tui_gateway.server")
    module._sessions.clear()
    module._pending.clear()
    module._pending_prompt_payloads.clear()
    module._answers.clear()
    yield module
    module._sessions.clear()
    module._pending.clear()
    module._pending_prompt_payloads.clear()
    module._answers.clear()


def _registered_manager(handler):
    manager = PluginManager()
    context = PluginContext(PluginManifest(name="context-test", source="user"), manager)
    context.register_command("context-test", handler)
    return manager


def _context(tmp_path: Path, *, session_id: str = "session-a", secret=None):
    return create_plugin_command_context(
        cwd=tmp_path,
        session_id=session_id,
        surface="tui",
        interactive=True,
        request_secret=secret,
    )


def test_legacy_handler_receives_exact_raw_argument_and_result(monkeypatch, tmp_path):
    """Fail if dispatch adds context to the established one-argument ABI."""
    received = []
    manager = _registered_manager(lambda raw_args: received.append(raw_args) or raw_args)
    monkeypatch.setattr("hermes_cli.plugins._plugin_manager", manager)

    raw_args = "  preserve \t every byte  "
    assert invoke_plugin_command("context-test", raw_args, _context(tmp_path)) == raw_args
    assert received == [raw_args]


def test_contextual_handler_receives_resolved_immutable_session_context(monkeypatch, tmp_path):
    """Fail if dispatch loses session ownership or lets a handler mutate context."""
    manager = _registered_manager(
        lambda raw_args, context: (
            raw_args,
            context.cwd,
            context.session_id,
            context.surface,
            context.interactive,
        )
    )
    monkeypatch.setattr("hermes_cli.plugins._plugin_manager", manager)

    context = _context(tmp_path / "workspace", session_id="session-42")
    result = invoke_plugin_command("context-test", "arg", context)

    assert result == ("arg", (tmp_path / "workspace").resolve(), "session-42", "tui", True)
    with pytest.raises((AttributeError, TypeError)):
        context.session_id = "other"  # type: ignore[misc]


def test_handler_typeerror_is_not_retried_as_legacy_handler(monkeypatch, tmp_path):
    """Fail if a two-argument handler's own TypeError triggers a second call."""
    calls = []

    def handler(raw_args, context):
        calls.append((raw_args, context.session_id))
        raise TypeError("handler bug")

    manager = _registered_manager(handler)
    monkeypatch.setattr("hermes_cli.plugins._plugin_manager", manager)

    with pytest.raises(TypeError, match="handler bug"):
        invoke_plugin_command("context-test", "arg", _context(tmp_path))
    assert calls == [("arg", "session-a")]


def test_secret_values_are_redacted_from_handler_output(monkeypatch, tmp_path):
    """Fail if a command can reflect an out-of-band secret into command output."""
    manager = _registered_manager(lambda _raw, context: {"token": context.request_secret("Token")})
    monkeypatch.setattr("hermes_cli.plugins._plugin_manager", manager)

    result = invoke_plugin_command("context-test", "", _context(tmp_path, secret=lambda _prompt: "s3cr3t"))
    assert result == {"token": "[secret]"}


def test_tui_and_desktop_dispatch_keep_context_and_secrets_per_session(monkeypatch, server, tmp_path):
    """Fail if either JSON-RPC surface borrows another session's cwd or secret."""
    seen = {}

    def handler(_raw_args, context):
        seen[context.session_id] = {
            "cwd": context.cwd,
            "surface": context.surface,
            "secret": context.request_secret("Project token"),
        }
        return f"done:{context.session_id}"

    manager = _registered_manager(handler)
    monkeypatch.setattr("hermes_cli.plugins._plugin_manager", manager)
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    server._sessions.update({
        "session-a": {"session_key": "a", "cwd": str(workspace_a), "source": "tui", "agent": None},
        "session-b": {"session_key": "b", "cwd": str(workspace_b), "source": "desktop", "agent": None},
    })
    responses = {}

    def dispatch(sid):
        responses[sid] = server.handle_request({
            "id": sid,
            "method": "slash.exec",
            "params": {"command": "context-test", "session_id": sid},
        })

    workers = [threading.Thread(target=dispatch, args=(sid,)) for sid in ("session-a", "session-b")]
    for worker in workers:
        worker.start()
    for _ in range(100):
        with server._prompt_lock:
            pending = dict(server._pending)
        if len(pending) == 2:
            break
        time.sleep(0.01)
    else:  # pragma: no cover - diagnostic guard
        pytest.fail("both session-owned secret requests were not registered")

    for request_id, (owner, _event) in pending.items():
        assert server.handle_request({
            "id": f"reply-{owner}",
            "method": "secret.respond",
            "params": {"request_id": request_id, "session_id": owner, "value": f"secret-{owner}"},
        })["result"] == {"status": "ok"}
    for worker in workers:
        worker.join(timeout=1)

    assert seen == {
        "session-a": {"cwd": workspace_a.resolve(), "surface": "tui", "secret": "secret-session-a"},
        "session-b": {"cwd": workspace_b.resolve(), "surface": "desktop", "secret": "secret-session-b"},
    }
    assert responses == {
        "session-a": {"jsonrpc": "2.0", "id": "session-a", "result": {"output": "done:session-a"}},
        "session-b": {"jsonrpc": "2.0", "id": "session-b", "result": {"output": "done:session-b"}},
    }


def test_tui_secret_response_is_owned_by_its_session(server):
    """Fail if a response routed from session B can release session A's request."""
    values = []

    def request():
        values.append(server._block("secret.request", "session-a", {"prompt": "Token", "env_var": "PLUGIN_SECRET"}, timeout=1))

    worker = threading.Thread(target=request)
    worker.start()
    for _ in range(100):
        with server._prompt_lock:
            request_ids = list(server._pending)
        if request_ids:
            break
        time.sleep(0.01)
    else:  # pragma: no cover - diagnostic guard
        pytest.fail("secret request was never registered")

    rid = request_ids[0]
    wrong = server.handle_request({
        "id": "wrong-session",
        "method": "secret.respond",
        "params": {"request_id": rid, "session_id": "session-b", "value": "wrong"},
    })
    assert wrong["error"]["code"] == 4009

    right = server.handle_request({
        "id": "right-session",
        "method": "secret.respond",
        "params": {"request_id": rid, "session_id": "session-a", "value": "right"},
    })
    assert right["result"] == {"status": "ok"}
    worker.join(timeout=1)
    assert values == ["right"]
