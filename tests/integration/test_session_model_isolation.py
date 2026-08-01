"""End-to-end routing isolation for live TUI sessions.

The break this catches is a session model switch escaping into a sibling
session (or another TUI process) through configuration or process-global
runtime state.  The loopback server is deliberately OpenAI-compatible: real
``AIAgent`` clients make the HTTP requests, while no request can leave the
test host.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator

import pytest


@dataclass(frozen=True)
class RouteCapture:
    endpoint: str
    model: str
    session_id: str


class _RouteServer(ThreadingHTTPServer):
    captures: list[RouteCapture]
    capture_lock: threading.Lock


@contextmanager
def _loopback_routes() -> Iterator[_RouteServer]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            if not self.path.endswith("/models"):
                self.send_error(404)
                return
            encoded = json.dumps(
                {"object": "list", "data": [{"id": "route-loopback", "object": "model"}]}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
            messages = body.get("messages") or []
            text = "\n".join(
                str(message.get("content") or "")
                for message in messages
                if isinstance(message, dict)
            )
            markers = [
                line.removeprefix("session=")
                for line in text.splitlines()
                if line.startswith("session=")
            ]
            # Conversation history includes prior probe markers; the final
            # user message is the one this request is serving.
            marker = markers[-1] if markers else ""
            if self.path.endswith("/chat/completions"):
                with self.server.capture_lock:  # type: ignore[attr-defined]
                    self.server.captures.append(  # type: ignore[attr-defined]
                        RouteCapture(
                            endpoint=self.path.removesuffix("/chat/completions"),
                            model=str(body.get("model") or ""),
                            session_id=marker,
                        )
                    )
            chunks = (
                {
                    "id": "loopback-route",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": body.get("model") or "",
                    "choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": None}],
                },
                {
                    "id": "loopback-route",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": body.get("model") or "",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                },
            )
            encoded = b"".join(
                b"data: " + json.dumps(chunk).encode() + b"\n\n" for chunk in chunks
            ) + b"data: [DONE]\n\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = _RouteServer(("127.0.0.1", 0), Handler)
    server.captures = []
    server.capture_lock = threading.Lock()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _write_canary_config(home: Path, port: int) -> bytes:
    config = f"""# routing-canary: 0a9f7e46
model:
  default: route-default-model
  provider: custom:route-default
custom_providers:
  - name: route-a
    base_url: http://127.0.0.1:{port}/route-a/v1
    api_key: loopback-a
    api_mode: chat_completions
  - name: route-b
    base_url: http://127.0.0.1:{port}/route-b/v1
    api_key: loopback-b
    api_mode: chat_completions
  - name: route-default
    base_url: http://127.0.0.1:{port}/route-default/v1
    api_key: loopback-default
    api_mode: chat_completions
"""
    path = home / "config.yaml"
    path.write_text(config, encoding="utf-8")
    return path.read_bytes()


def _override(name: str) -> dict[str, str]:
    return {"model": f"route-{name}-model", "provider": f"custom:route-{name}"}


def _route_tuples(captures: list[RouteCapture]) -> list[tuple[str, str, str]]:
    return [(capture.session_id, capture.endpoint, capture.model) for capture in captures]


def _marked_captures(routes: _RouteServer) -> list[RouteCapture]:
    with routes.capture_lock:
        return [capture for capture in routes.captures if capture.session_id]


def test_interleaved_live_agents_keep_session_routes_and_canary_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Four real client turns must retain their session-specific endpoint/model.

    Removing ``model_override`` from ``_make_agent`` or resurrecting a process
    environment write in the switch path makes this assertion fail by routing
    at least one interleaved turn to the wrong endpoint.
    """
    with _loopback_routes() as routes:
        home = tmp_path / "hermes-home"
        home.mkdir()
        canary = _write_canary_config(home, routes.server_port)
        monkeypatch.setenv("HERMES_HOME", str(home))
        monkeypatch.setenv("HERMES_TUI_PASS_SESSION_ID", "1")

        from hermes_constants import reset_hermes_home_override, set_hermes_home_override
        from tui_gateway import server

        # ``server`` is normally imported before this test's temporary home is
        # installed.  Scope its long-lived launch-home and config cache to the
        # canary profile without mutating the process environment at runtime.
        old_home = server._hermes_home
        old_cache, old_mtime, old_path = server._cfg_cache, server._cfg_mtime, server._cfg_path
        server._hermes_home = home
        server._cfg_cache = server._cfg_mtime = server._cfg_path = None
        token = set_hermes_home_override(home)
        agents = []
        try:
            agent_a = server._make_agent(
                "live-a", "session-a", session_id="session-a", model_override=_override("a")
            )
            agent_b = server._make_agent(
                "live-b", "session-b", session_id="session-b", model_override=_override("b")
            )
            agents.extend((agent_a, agent_b))
            for agent, session_id in (
                (agent_a, "session-a"),
                (agent_b, "session-b"),
                (agent_a, "session-a"),
                (agent_b, "session-b"),
            ):
                result = agent.run_conversation(f"session={session_id}\nroute probe")
                assert result["final_response"] == "ok"
        finally:
            for agent in agents:
                agent.close()
            reset_hermes_home_override(token)
            server._hermes_home = old_home
            server._cfg_cache, server._cfg_mtime, server._cfg_path = old_cache, old_mtime, old_path

        assert _route_tuples(_marked_captures(routes)) == [
            ("session-a", "/route-a/v1", "route-a-model"),
            ("session-b", "/route-b/v1", "route-b-model"),
            ("session-a", "/route-a/v1", "route-a-model"),
            ("session-b", "/route-b/v1", "route-b-model"),
        ]
        assert (home / "config.yaml").read_bytes() == canary


class _GatewayProcess:
    def __init__(self, home: Path):
        env = os.environ.copy()
        env.update(
            {
                "HERMES_HOME": str(home),
                "HERMES_TUI_PASS_SESSION_ID": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        self.process = subprocess.Popen(
            [sys.executable, "-m", "tui_gateway.entry"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=Path(__file__).parents[2],
            env=env,
        )
        self._events: queue.Queue[dict] = queue.Queue()
        self._event_log: list[dict] = []
        self._event_condition = threading.Condition()
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader.start()

    def _read_stdout(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("method") == "event":
                with self._event_condition:
                    self._event_log.append(message)
                    self._event_condition.notify_all()
            self._events.put(message)

    def event_cursor(self) -> int:
        with self._event_condition:
            return len(self._event_log)

    def wait_for_event(self, event_type: str, sid: str, cursor: int, *, timeout: float = 30) -> dict:
        deadline = time.monotonic() + timeout
        with self._event_condition:
            while time.monotonic() < deadline:
                for event in self._event_log[cursor:]:
                    params = event.get("params") or {}
                    if params.get("type") == event_type and params.get("session_id") == sid:
                        return event
                self._event_condition.wait(timeout=min(0.2, deadline - time.monotonic()))
        raise AssertionError(f"timed out waiting for {event_type} in session {sid}")

    def call(self, method: str, params: dict, *, timeout: float = 30) -> dict:
        request_id = f"test-{time.monotonic_ns()}"
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps({"id": request_id, "method": method, "params": params}) + "\n")
        self.process.stdin.flush()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                message = self._events.get(timeout=min(0.2, deadline - time.monotonic()))
            except queue.Empty:
                continue
            if message.get("id") == request_id:
                if "error" in message:
                    raise AssertionError(f"{method} failed: {message['error']}")
                return message["result"]
        stderr = ""
        if self.process.stderr is not None:
            stderr = self.process.stderr.read(4_000)
        raise AssertionError(f"timed out waiting for {method}; stderr={stderr}")

    def close(self) -> None:
        if self.process.poll() is None:
            assert self.process.stdin is not None
            self.process.stdin.close()
            self.process.wait(timeout=10)


def _create_and_switch(worker: _GatewayProcess, model: str, provider: str) -> str:
    cursor = worker.event_cursor()
    created = worker.call("session.create", {"source": "tui"})
    sid = created["session_id"]
    # A model switch targets the live agent.  Wait for the real deferred build
    # rather than racing it: the user-facing desktop/TUI command path does the
    # same by acting on an initialized session.
    worker.wait_for_event("session.info", sid, cursor)
    worker.call(
        "config.set",
        {
            "session_id": sid,
            "key": "model",
            "value": f"{model} --provider {provider} --session",
            "confirm_expensive_model": True,
        },
    )
    return sid


def _submit_and_wait(worker: _GatewayProcess, sid: str, marker: str) -> None:
    cursor = worker.event_cursor()
    result = worker.call("prompt.submit", {"session_id": sid, "text": f"session={marker}\nroute probe"})
    assert result["status"] in {"started", "streaming"}
    worker.wait_for_event("message.complete", sid, cursor)


def test_two_tui_processes_isolate_session_switches_and_only_global_changes_default(
    tmp_path: Path,
) -> None:
    """Session switches are process-local; only an explicit global switch writes the canary."""
    with _loopback_routes() as routes:
        home = tmp_path / "hermes-home"
        home.mkdir()
        canary = _write_canary_config(home, routes.server_port)
        worker_a = _GatewayProcess(home)
        worker_b = _GatewayProcess(home)
        try:
            session_a = _create_and_switch(worker_a, "route-a-model", "custom:route-a")
            session_b = _create_and_switch(worker_b, "route-b-model", "custom:route-b")
            assert (home / "config.yaml").read_bytes() == canary

            _submit_and_wait(worker_a, session_a, "process-a-first")
            _submit_and_wait(worker_b, session_b, "process-b-first")

            # The only durable mutation in this test. Existing sessions remain
            # pinned; a new session observes this changed profile default.
            worker_a.call(
                "config.set",
                {
                    "key": "model",
                    "value": "route-default-model --provider custom:route-default --global",
                    "confirm_expensive_model": True,
                },
            )
            assert (home / "config.yaml").read_bytes() != canary
            assert sha256((home / "config.yaml").read_bytes()).hexdigest() != sha256(canary).hexdigest()

            _submit_and_wait(worker_a, session_a, "process-a-after-global")
            _submit_and_wait(worker_b, session_b, "process-b-after-global")
            new_session = worker_a.call("session.create", {"source": "tui"})["session_id"]
            _submit_and_wait(worker_a, new_session, "process-a-new-default")

            deadline = time.monotonic() + 30
            while len(_marked_captures(routes)) < 5 and time.monotonic() < deadline:
                time.sleep(0.05)
        finally:
            worker_a.close()
            worker_b.close()

        assert _route_tuples(_marked_captures(routes)) == [
            ("process-a-first", "/route-a/v1", "route-a-model"),
            ("process-b-first", "/route-b/v1", "route-b-model"),
            ("process-a-after-global", "/route-a/v1", "route-a-model"),
            ("process-b-after-global", "/route-b/v1", "route-b-model"),
            ("process-a-new-default", "/route-default/v1", "route-default-model"),
        ]
