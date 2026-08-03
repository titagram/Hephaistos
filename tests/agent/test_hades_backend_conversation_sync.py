from types import SimpleNamespace


def test_normal_turn_never_runs_backend_sync(monkeypatch):
    import agent.conversation_loop as conversation_loop
    import agent.turn_finalizer as turn_finalizer
    import hermes_cli.hades_backend_sync as hades_sync

    calls = []

    def fail_backend_sync(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("ordinary agent lifecycle attempted Backend sync")

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
    monkeypatch.setattr(hades_sync, "run_backend_sync", fail_backend_sync)

    agent = SimpleNamespace(
        api_mode="chat_completions",
        max_iterations=0,
        iteration_budget=SimpleNamespace(remaining=1),
        _budget_grace_call=False,
    )
    result = conversation_loop.run_conversation(agent, "hello")

    assert result["final_response"] == "ok"
    assert calls == []
