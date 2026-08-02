from __future__ import annotations

import builtins
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def linked_workspace(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text(
        "memory:\n  provider: holographic\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))

    workspace = tmp_path / "repo"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    from hermes_cli import projects_db

    with projects_db.connect_closing() as conn:
        projects_db.create_project(
            conn,
            name="Linked workspace",
            folders=[str(workspace)],
        )

    return workspace


def _run_fake_conversation(monkeypatch):
    import agent.conversation_loop as conversation_loop
    import agent.turn_finalizer as turn_finalizer

    context = SimpleNamespace(
        user_message="hello",
        original_user_message="hello",
        messages=[{"role": "user", "content": "hello"}],
        conversation_history=[],
        active_system_prompt="system",
        effective_task_id="task-1",
        turn_id="turn-1",
        current_turn_user_idx=0,
        should_review_memory=False,
        plugin_user_context=None,
        ext_prefetch_cache=None,
    )
    monkeypatch.setattr(conversation_loop, "build_turn_context", lambda *args, **kwargs: context)
    monkeypatch.setattr(
        turn_finalizer,
        "finalize_turn",
        lambda *args, **kwargs: {"final_response": "ok"},
    )

    agent = SimpleNamespace(
        api_mode="chat_completions",
        max_iterations=0,
        iteration_budget=SimpleNamespace(remaining=1),
        _budget_grace_call=False,
    )
    return conversation_loop.run_conversation(agent, "hello")


def test_normal_turn_never_imports_backend_sync(monkeypatch, linked_workspace):
    imported = []
    real_import = builtins.__import__

    monkeypatch.delitem(sys.modules, "hermes_cli.hades_backend_sync", raising=False)

    def fail_backend_sync_import(name, *args, **kwargs):
        if name == "hermes_cli.hades_backend_sync":
            imported.append(name)
            raise AssertionError("ordinary agent lifecycle attempted Backend sync import")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_backend_sync_import)

    result = _run_fake_conversation(monkeypatch)

    assert result["final_response"] == "ok"
    assert imported == []
