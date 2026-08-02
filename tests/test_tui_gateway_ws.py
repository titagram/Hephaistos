import asyncio
import threading
import time

from hermes_cli import mcp_startup
from tui_gateway import server
from tui_gateway import ws as ws_mod


def _context_for(sid):
    from hermes_cli.plugin_command_context import create_plugin_command_context

    return create_plugin_command_context(
        cwd="/tmp",
        session_id=sid,
        surface="desktop",
        interactive=True,
    )


def test_ws_startup_starts_background_mcp_discovery(monkeypatch):
    """The desktop app and dashboard chat reach the agent through this WS
    sidecar, not through tui_gateway.entry.main() (which spawns the discovery
    thread for the stdio TUI). handle_ws must start discovery itself, otherwise
    _make_agent's wait_for_mcp_discovery no-ops and the agent snapshots an
    MCP-less tool list. Regression test for #38945."""
    calls = []
    monkeypatch.setattr(
        mcp_startup,
        "start_background_mcp_discovery",
        lambda **kw: calls.append(kw),
    )

    class FakeWS:
        async def accept(self):
            pass

        async def send_text(self, line):
            pass

        async def receive_text(self):
            raise ws_mod._WebSocketDisconnect()

        async def close(self):
            pass

    server._sessions.clear()
    try:
        asyncio.run(ws_mod.handle_ws(FakeWS()))
    finally:
        server._sessions.clear()

    assert calls == [{"logger": ws_mod._log, "thread_name": "tui-ws-mcp-discovery"}]


def _run_disconnect(monkeypatch, seed):
    """Drive handle_ws to its disconnect `finally`, seeding sessions against the
    live WSTransport the moment it exists. Returns nothing; inspect _sessions."""
    # Disable the grace-reap Timer: detached sessions normally schedule a
    # threading.Timer via _schedule_ws_orphan_reap, which would outlive the test
    # and fire _reap during interpreter teardown — touching _sessions/DB and
    # producing spurious post-run errors under the per-file CI runner. Grace=0
    # short-circuits the Timer (see _schedule_ws_orphan_reap) so the test leaves
    # no lingering thread.
    monkeypatch.setattr(server, "_WS_ORPHAN_REAP_GRACE_S", 0)

    # Mirror the real _finalize_session chokepoint: it is the single place that
    # closes the slash-worker (#38095). Stub it but keep that behavior so the
    # disconnect-reap path still exercises worker teardown.
    def _fake_finalize(s, end_reason="tui_close"):
        w = s.get("slash_worker")
        if w:
            w.close()

    monkeypatch.setattr(server, "_finalize_session", _fake_finalize)

    created = []
    real_transport = ws_mod.WSTransport
    monkeypatch.setattr(
        ws_mod, "WSTransport",
        lambda ws, loop, **kw: created.append(real_transport(ws, loop, **kw)) or created[-1],
    )

    class FakeWS:
        async def accept(self):
            pass

        async def send_text(self, line):
            pass

        async def receive_text(self):
            seed(created[0])  # transport now exists; attach it to sessions
            raise ws_mod._WebSocketDisconnect()

        async def close(self):
            pass

    asyncio.run(ws_mod.handle_ws(FakeWS()))


def test_ws_disconnect_reaps_flagged_session_and_closes_worker(monkeypatch):
    closed = []

    class FakeWorker:
        def close(self):
            closed.append(True)

    server._sessions.clear()
    try:
        _run_disconnect(
            monkeypatch,
            lambda t: server._sessions.update(
                flagged={
                    "transport": t,
                    "close_on_disconnect": True,
                    "slash_worker": FakeWorker(),
                    "session_key": "k",
                }
            ),
        )
        assert "flagged" not in server._sessions
        assert closed == [True]
    finally:
        server._sessions.clear()


def test_ws_disconnect_preserves_and_repoints_reconnectable_session(monkeypatch):
    server._sessions.clear()
    try:
        _run_disconnect(
            monkeypatch,
            lambda t: server._sessions.update(
                plain={"transport": t, "close_on_disconnect": False, "session_key": "k"}
            ),
        )
        assert server._sessions["plain"]["transport"] is server._detached_ws_transport
    finally:
        server._sessions.clear()


def test_ws_disconnect_cancels_interactive_invocations_for_close_and_detach(monkeypatch):
    """Both WS policies revoke contexts and release prompts immediately."""
    monkeypatch.setattr(server, "_WS_ORPHAN_REAP_GRACE_S", 0)
    transport = object()
    server._sessions.clear()
    server._pending.clear()
    server._pending_prompt_payloads.clear()
    server._answers.clear()
    server._plugin_invocations.clear()
    try:
        contexts = {}
        events = {}
        for sid, closes in (("closing", True), ("detaching", False)):
            server._sessions[sid] = {
                "transport": transport,
                "close_on_disconnect": closes,
                "session_key": sid,
            }
            contexts[sid] = _context_for(sid)
            assert server._register_plugin_invocation(sid, contexts[sid]) is True
            events[sid] = threading.Event()
            with server._prompt_lock:
                server._pending[f"prompt-{sid}"] = (sid, events[sid])
                server._pending_prompt_payloads[f"prompt-{sid}"] = (
                    "secret.request",
                    {"request_id": f"prompt-{sid}", "session_id": sid},
                )

        assert server._close_sessions_for_transport(transport) == (1, 1)

        assert "closing" not in server._sessions
        assert server._sessions["detaching"]["transport"] is server._detached_ws_transport
        assert all(context.invocation.cancelled for context in contexts.values())
        assert all(event.is_set() for event in events.values())
        assert not server._pending
        assert not server._pending_prompt_payloads
        assert not server._plugin_invocations
    finally:
        server._clear_pending()
        server._sessions.clear()
        server._pending.clear()
        server._pending_prompt_payloads.clear()
        server._answers.clear()
        server._plugin_invocations.clear()


def test_ws_reconnect_accepts_a_fresh_invocation_without_prompt_replay(monkeypatch):
    """A detached session reattaches cleanly; cancelled prompts never replay."""
    monkeypatch.setattr(server, "_WS_ORPHAN_REAP_GRACE_S", 0)
    old_transport = object()
    new_transport = object()
    sid = "reconnectable"
    server._sessions.clear()
    server._pending.clear()
    server._pending_prompt_payloads.clear()
    server._plugin_invocations.clear()
    try:
        server._sessions[sid] = {
            "transport": old_transport,
            "close_on_disconnect": False,
            "session_key": sid,
        }
        stale = _context_for(sid)
        assert server._register_plugin_invocation(sid, stale) is True
        prompt = threading.Event()
        with server._prompt_lock:
            server._pending["stale-prompt"] = (sid, prompt)
            server._pending_prompt_payloads["stale-prompt"] = (
                "secret.request",
                {"request_id": "stale-prompt", "session_id": sid},
            )

        assert server._close_sessions_for_transport(old_transport) == (0, 1)
        assert stale.invocation.cancelled
        assert prompt.is_set()
        assert not server._pending_prompt_payloads

        # This is the production live-reuse transition performed by resume/activate
        # before the next client request reaches plugin dispatch.
        server._sessions[sid]["transport"] = new_transport
        fresh = _context_for(sid)
        assert server._register_plugin_invocation(sid, fresh) is True
        assert not fresh.invocation.cancelled
        assert server._pending_prompt_payloads == {}
    finally:
        server._clear_pending()
        server._sessions.clear()
        server._pending.clear()
        server._pending_prompt_payloads.clear()
        server._plugin_invocations.clear()


def test_ws_grace_reap_closes_detached_session_after_immediate_plugin_cancel(monkeypatch):
    """Grace reaping finalizes an already-cancelled, prompt-free orphan."""
    monkeypatch.setattr(server, "_WS_ORPHAN_REAP_GRACE_S", 0.02)
    monkeypatch.setattr(server, "_teardown_session", lambda *_args, **_kwargs: None)
    transport = object()
    sid = "grace-reap"
    context = _context_for(sid)
    server._sessions.clear()
    server._plugin_invocations.clear()
    try:
        server._sessions[sid] = {
            "transport": transport,
            "close_on_disconnect": False,
            "session_key": sid,
            "running": False,
        }
        assert server._register_plugin_invocation(sid, context) is True

        assert server._close_sessions_for_transport(transport) == (0, 1)
        deadline = time.time() + 1
        while sid in server._sessions and time.time() < deadline:
            time.sleep(0.01)

        assert context.invocation.cancelled
        assert sid not in server._sessions
        assert sid not in server._plugin_invocations
    finally:
        server._clear_pending()
        server._sessions.clear()
        server._plugin_invocations.clear()


def test_ws_write_loop_stall_does_not_latch_transport(monkeypatch):
    """A write that times out because the event loop is stalled (GIL-heavy
    agent turn) must NOT latch the transport closed — the frame is already
    scheduled and flushes when the loop recovers. Latching here permanently
    silenced live watch windows after one slow write."""
    monkeypatch.setattr(ws_mod, "_WS_WRITE_TIMEOUT_S", 0.05)
    sent = []

    class FakeWS:
        async def send_text(self, line):
            sent.append(line)

    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        transport = ws_mod.WSTransport(FakeWS(), loop, peer="stall-test")
        # Stall the loop well past the write timeout, then write from this
        # (non-loop) thread: the wait times out but the send stays in flight.
        loop.call_soon_threadsafe(time.sleep, 0.3)
        assert transport.write({"a": 1}) is True
        assert transport._closed is False

        # Once the loop breathes again, both the stalled frame and new writes
        # must reach the socket.
        assert transport.write({"b": 2}) is True
        deadline = time.time() + 2
        while len(sent) < 2 and time.time() < deadline:
            time.sleep(0.01)
        assert len(sent) == 2
        assert transport._closed is False
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=2)
        loop.close()
