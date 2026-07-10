from pathlib import Path
from types import SimpleNamespace


def test_conversation_piggyback_sync_is_scoped_to_runtime_cwd(monkeypatch):
    from agent.conversation_loop import _maybe_piggyback_hades_backend_sync
    import agent.runtime_cwd as runtime_cwd
    import hermes_cli.hades_backend_sync as hades_sync

    calls = []
    monkeypatch.setattr(runtime_cwd, "resolve_agent_cwd", lambda: Path("/tmp/current-project"))

    def fake_sync_for_workspace(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(status="started", reason="due")

    monkeypatch.setattr(hades_sync, "maybe_run_backend_sync_for_workspace", fake_sync_for_workspace)

    _maybe_piggyback_hades_backend_sync(SimpleNamespace(session_id="session-1"))

    assert calls == [
        {
            "cwd": Path("/tmp/current-project"),
            "force": False,
            "min_interval_seconds": 300,
        }
    ]
