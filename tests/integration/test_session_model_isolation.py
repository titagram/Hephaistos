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
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator

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


def _route_tuples(captures: list[RouteCapture]) -> list[tuple[str, str, str]]:
    return [(capture.session_id, capture.endpoint, capture.model) for capture in captures]


def _marked_captures(routes: _RouteServer) -> list[RouteCapture]:
    with routes.capture_lock:
        return [capture for capture in routes.captures if capture.session_id]


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
        self._stderr_lines: deque[str] = deque(maxlen=100)
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._stderr_reader = threading.Thread(target=self._read_stderr, daemon=True)
        self._reader.start()
        self._stderr_reader.start()

    def _read_stdout(self) -> None:
        assert self.process.stdout is not None
        try:
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
        except (OSError, ValueError):
            return

    def _read_stderr(self) -> None:
        assert self.process.stderr is not None
        try:
            for line in self.process.stderr:
                self._stderr_lines.append(line.rstrip())
        except (OSError, ValueError):
            return

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
        raise AssertionError(
            f"timed out waiting for {method}; stderr={' | '.join(self._stderr_lines)}"
        )

    def close(self) -> None:
        """Best-effort shutdown that cannot strand a local gateway worker."""
        try:
            if self.process.stdin is not None and not self.process.stdin.closed:
                self.process.stdin.close()
            if self.process.poll() is None:
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait(timeout=5)
        except (OSError, ValueError):
            pass
        finally:
            for stream in (self.process.stdout, self.process.stderr):
                try:
                    if stream is not None and not stream.closed:
                        stream.close()
                except (OSError, ValueError):
                    pass
            self._reader.join(timeout=2)
            self._stderr_reader.join(timeout=2)


def _close_workers(*workers: _GatewayProcess) -> None:
    """Clean every worker even when a sibling's teardown has failed."""
    for worker in workers:
        try:
            worker.close()
        except Exception:
            # A remaining sibling still has to be shut down.  The postcondition
            # below reports any process that resisted close/terminate/kill.
            continue
    assert all(worker.process.poll() is not None for worker in workers)


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


def test_one_tui_process_keeps_two_live_session_routes_isolated(tmp_path: Path) -> None:
    """Actual TUI RPCs must not leak a --session switch through shared process state."""
    with _loopback_routes() as routes:
        home = tmp_path / "hermes-home"
        home.mkdir()
        canary = _write_canary_config(home, routes.server_port)
        worker = _GatewayProcess(home)
        try:
            session_a = _create_and_switch(worker, "route-a-model", "custom:route-a")
            session_b = _create_and_switch(worker, "route-b-model", "custom:route-b")
            assert (home / "config.yaml").read_bytes() == canary

            _submit_and_wait(worker, session_a, "same-process-a-first")
            _submit_and_wait(worker, session_b, "same-process-b-first")
            _submit_and_wait(worker, session_a, "same-process-a-second")
            _submit_and_wait(worker, session_b, "same-process-b-second")
        finally:
            _close_workers(worker)

        assert _route_tuples(_marked_captures(routes)) == [
            ("same-process-a-first", "/route-a/v1", "route-a-model"),
            ("same-process-b-first", "/route-b/v1", "route-b-model"),
            ("same-process-a-second", "/route-a/v1", "route-a-model"),
            ("same-process-b-second", "/route-b/v1", "route-b-model"),
        ]


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
            _close_workers(worker_a, worker_b)

        assert _route_tuples(_marked_captures(routes)) == [
            ("process-a-first", "/route-a/v1", "route-a-model"),
            ("process-b-first", "/route-b/v1", "route-b-model"),
            ("process-a-after-global", "/route-a/v1", "route-a-model"),
            ("process-b-after-global", "/route-b/v1", "route-b-model"),
            ("process-a-new-default", "/route-default/v1", "route-default-model"),
        ]
