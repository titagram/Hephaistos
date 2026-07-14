import inspect

from agent import tool_executor


def test_failed_tool_event_uses_bounded_category_without_result_preview(monkeypatch):
    emitted = []
    monkeypatch.setattr(tool_executor, "emit_experience_event", lambda **row: emitted.append(row))
    agent = type("AgentFixture", (), {"generation_id": "git:abc"})()

    tool_executor._record_tool_failure_event(
        agent,
        "terminal",
        '{"exit_code": 7, "error": "token=raw-secret"}',
    )
    tool_executor._record_tool_failure_event(
        agent,
        "web_search",
        'Error: request failed with token=another-secret',
    )

    assert emitted == [
        {
            "event_type": "tool.failed",
            "generation_id": "git:abc",
            "component_id": "tool:terminal",
            "capability_id": "capability:terminal",
            "operation": "tool_call",
            "failure_class": "ExitCode",
            "severity": "error",
        },
        {
            "event_type": "tool.failed",
            "generation_id": "git:abc",
            "component_id": "tool:web_search",
            "capability_id": "capability:web_search",
            "operation": "tool_call",
            "failure_class": "ToolError",
            "severity": "error",
        },
    ]
    assert "raw-secret" not in str(emitted)
    assert "another-secret" not in str(emitted)


def test_sequential_and_concurrent_paths_record_failures():
    assert "_record_tool_failure_event(" in inspect.getsource(
        tool_executor.execute_tool_calls_sequential
    )
    assert "_record_tool_failure_event(" in inspect.getsource(
        tool_executor.execute_tool_calls_concurrent
    )
