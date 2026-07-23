from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import threading
from types import ModuleType
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_cli.engineering_review import command, internal_cli
from hermes_cli.engineering_review.execution_policy import ExecutionDecision
from hermes_cli import main as main_module
from hermes_cli.subcommands.review import build_review_parser


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hermes")
    subparsers = parser.add_subparsers(dest="command")
    build_review_parser(subparsers, cmd_review=command.review_command)
    return parser


def test_review_parser_defaults_to_medium_and_accepts_all_targets() -> None:
    parser = _parser()

    local = parser.parse_args(["review"])
    pull_request = parser.parse_args([
        "review",
        "https://github.com/o/r/pull/42",
        "--effort",
        "high",
    ])
    diff_file = parser.parse_args(["review", "changes.diff", "--effort", "low"])

    assert local.target == "local"
    assert local.effort == "medium"
    assert pull_request.target.endswith("/pull/42")
    assert pull_request.effort == "high"
    assert diff_file.target == "changes.diff"
    assert diff_file.effort == "low"
    assert local.runner == "auto"


def test_review_parser_exposes_target_effort_and_registered_recovery(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        _parser().parse_args(["review", "--help"])
    assert exc_info.value.code == 0
    help_text = capsys.readouterr().out
    assert "[target]" in help_text
    assert "--effort {low,medium,high}" in help_text
    assert "--run RUN_ID" in help_text
    for hidden in ("runner", "session", "skill", "model", "provider", "operation"):
        assert f"--{hidden}" not in help_text


def test_public_review_cleanup_uses_post_session_recovery(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from hermes_cli.engineering_review import recovery

    seen: list[str] = []
    monkeypatch.setattr(
        recovery,
        "recover_review_run",
        lambda run_id: (
            seen.append(run_id)
            or {"runId": run_id, "status": "complete", "removed": []}
        ),
    )
    args = _parser().parse_args([
        "review",
        "cleanup",
        "--run",
        "registered-run-1234",
    ])

    assert main_module.cmd_review(args) == 0
    assert seen == ["registered-run-1234"]
    assert json.loads(capsys.readouterr().out)["status"] == "complete"


def test_public_review_preloads_skill_and_preserves_approvals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}
    monkeypatch.setattr(command, "launch_review_chat", lambda **kw: seen.update(kw))
    args = _parser().parse_args(["review", "local"])

    assert command.review_command(args) == 0
    assert seen["skills"] == ["requesting-code-review"]
    assert seen["auto_approve"] is False
    assert seen["pass_session_id"] is True
    assert seen["target"] == "local"
    assert seen["effort"] == "medium"
    assert "run ID" not in str(seen["query"])
    assert "plan" not in str(seen["query"]).lower()


def test_launch_review_chat_owns_authority_for_chat_lifecycle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    events: list[object] = []

    class FakeAuthority:
        def __init__(self, **kwargs: object) -> None:
            events.append(("create", kwargs))

        def start_serving(self) -> None:
            events.append("start")

        def close(self) -> None:
            events.append("close")

    def fake_chat(args: argparse.Namespace) -> None:
        events.append(("chat", args.skills, args.yolo, args.pass_session_id))
        assert args.query is None
        assert args.initial_query == "execute the skill"
        assert args.exit_after_initial_query is True
        assert "HERMES_YOLO_MODE" not in os.environ
        assert events == [
            (
                "chat",
                ["requesting-code-review"],
                False,
                True,
            )
        ]
        args.session_ready_callback("session-1")
        events.append("running")

    monkeypatch.setattr(command, "ReviewAuthority", FakeAuthority)
    monkeypatch.setattr(command, "_load_chat_command", lambda: fake_chat)
    monkeypatch.setattr(
        command,
        "_prune_completed_review_runs",
        lambda: events.append("prune"),
    )
    monkeypatch.setenv("HERMES_YOLO_MODE", "1")
    args = _parser().parse_args(["review", "HEAD~1..HEAD", "--effort", "high"])

    command.launch_review_chat(
        args=args,
        workspace=tmp_path,
        target="HEAD~1..HEAD",
        effort="high",
        skills=["requesting-code-review"],
        auto_approve=False,
        pass_session_id=True,
        query="execute the skill",
    )

    assert events[1][0] == "create"
    assert events[1][1] == {
        "workspace": tmp_path.resolve(),
        "target": "HEAD~1..HEAD",
        "effort": "high",
        "session_id": "session-1",
        "execution_decision": ExecutionDecision(
            mode="local",
            allowed=True,
            sanitized_env=command.decide_execution(
                target_kind="range",
                sandbox=os.environ.get("TERMINAL_ENV", "local"),
                allow_local=False,
            ).sanitized_env,
            network=True,
            reason="local_review_uses_existing_terminal_policy",
            backend=None,
        ),
    }
    assert events[2:] == ["start", "running", "close", "prune"]
    assert os.environ["HERMES_YOLO_MODE"] == "1"


def test_review_lifecycle_prunes_with_profile_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from hermes_cli.engineering_review import runs

    home = tmp_path / "hermes-home"
    home.mkdir()
    (home / "config.yaml").write_text(
        "review:\n  retention_runs: 7\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    seen: list[tuple[Path, int]] = []
    monkeypatch.setattr(
        runs,
        "prune_completed_runs",
        lambda configured_home, keep: seen.append((configured_home, keep)) or [],
    )

    command._prune_completed_review_runs()

    assert seen == [(home, 7)]


@pytest.mark.parametrize(
    ("backend", "approval_result", "expected_mode", "expected_allowed"),
    [
        ("local", "once", "local", True),
        ("local", "deny", "denied", False),
        ("docker", None, "sandbox", True),
    ],
)
def test_pr_execution_uses_existing_backend_or_explicit_consent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    backend: str,
    approval_result: str | None,
    expected_mode: str,
    expected_allowed: bool,
) -> None:
    import tools.terminal_tool as terminal_tool

    decisions: list[object] = []

    class FakeAuthority:
        def __init__(self, **kwargs: object) -> None:
            decisions.append(kwargs["execution_decision"])

        def start_serving(self) -> None:
            pass

        def close(self) -> None:
            pass

    approvals: list[str] = []

    def approval(command_text: str, _description: str, **_kwargs: object) -> str:
        approvals.append(command_text)
        assert approval_result is not None
        return approval_result

    monkeypatch.setenv("TERMINAL_ENV", backend)
    monkeypatch.setattr(command, "ReviewAuthority", FakeAuthority)
    monkeypatch.setattr(
        terminal_tool,
        "_get_approval_callback",
        lambda: approval if approval_result is not None else None,
    )
    monkeypatch.setattr(
        command,
        "_load_chat_command",
        lambda: lambda args: args.session_ready_callback("session-1"),
    )

    command.launch_review_chat(
        args=argparse.Namespace(),
        workspace=tmp_path,
        target="https://github.com/o/r/pull/42",
        effort="medium",
        skills=["requesting-code-review"],
        auto_approve=False,
        pass_session_id=True,
        query="review",
    )

    decision = decisions[0]
    assert decision.mode == expected_mode
    assert decision.allowed is expected_allowed
    assert len(approvals) == (1 if backend == "local" else 0)


@pytest.mark.parametrize(
    ("approval_result", "expected_allowed"),
    [("once", True), ("deny", False)],
)
def test_public_pr_review_hands_approval_to_live_one_shot_chat(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    approval_result: str,
    expected_allowed: bool,
) -> None:
    """Exercise review_command -> real cmd_chat without replacing its loader."""
    import cli as classic_cli
    import tools.terminal_tool as terminal_tool

    decisions: list[ExecutionDecision] = []
    lifecycle: list[str] = []

    class FakeAuthority:
        def __init__(self, **kwargs: object) -> None:
            decisions.append(kwargs["execution_decision"])  # type: ignore[arg-type]

        def start_serving(self) -> None:
            lifecycle.append("serving")

        def close(self) -> None:
            lifecycle.append("closed")

    def approval(*_args: object, **_kwargs: object) -> str:
        assert lifecycle == ["ui-running"]
        lifecycle.append(f"approval:{approval_result}")
        return approval_result

    def live_cli_main(**kwargs: object) -> None:
        # This is the contract classic cli.main presents only after selecting
        # its interactive branch. A single-query handoff would pass ``query``
        # and never have a prompt application capable of answering approval.
        assert kwargs.get("query") is None
        assert kwargs["initial_query"] == command._review_query(
            "https://github.com/o/r/pull/42", "medium"
        )
        assert kwargs["exit_after_initial_query"] is True
        assert kwargs["pass_session_id"] is True
        assert kwargs.get("yolo") is None
        lifecycle.append("ui-running")
        terminal_tool.set_approval_callback(approval)
        try:
            callback = kwargs["session_ready_callback"]
            assert callable(callback)
            callback("public-review-session")
        finally:
            terminal_tool.set_approval_callback(None)

    monkeypatch.setenv("TERMINAL_ENV", "local")
    monkeypatch.setattr(command, "ReviewAuthority", FakeAuthority)
    monkeypatch.setattr(command, "_prune_completed_review_runs", lambda: None)
    monkeypatch.setattr(classic_cli, "main", live_cli_main)
    monkeypatch.setattr(main_module, "_resolve_use_tui", lambda _args: False)
    monkeypatch.setattr(main_module, "_has_any_provider_configured", lambda: True)
    monkeypatch.setattr(
        main_module, "_termux_should_prefetch_update_check", lambda: False
    )
    monkeypatch.setattr(main_module, "_sync_bundled_skills_for_startup", lambda: None)
    monkeypatch.setattr(main_module, "_pin_kanban_board_env", lambda: None)
    monkeypatch.chdir(tmp_path)
    args = _parser().parse_args([
        "review",
        "https://github.com/o/r/pull/42",
    ])

    assert main_module.cmd_review(args) == 0

    assert decisions[0].allowed is expected_allowed
    assert decisions[0].mode == ("local" if expected_allowed else "denied")
    assert lifecycle == [
        "ui-running",
        f"approval:{approval_result}",
        "serving",
        "closed",
    ]


@pytest.mark.parametrize(
    ("approval_result", "expected_allowed"),
    [("once", True), ("deny", False)],
)
def test_public_pr_review_process_loop_thread_receives_local_consent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    approval_result: str,
    expected_allowed: bool,
) -> None:
    """The real process-loop thread entry installs its own TLS callbacks."""
    import cli as classic_cli
    import tools.terminal_tool as terminal_tool

    decisions: list[ExecutionDecision] = []
    approval_threads: list[int] = []
    process_thread_ids: list[int] = []
    errors: list[BaseException] = []
    global_secret_registrations: list[object] = []

    class FakeAuthority:
        def __init__(self, **kwargs: object) -> None:
            decisions.append(kwargs["execution_decision"])  # type: ignore[arg-type]

        def start_serving(self) -> None:
            pass

        def close(self) -> None:
            pass

    shell = classic_cli.HermesCLI.__new__(classic_cli.HermesCLI)
    shell._tool_callbacks_installed = False
    shell._sudo_password_callback = lambda: ""
    shell._secret_capture_callback = lambda *_args, **_kwargs: {}
    shell._computer_use_approval_callback = lambda *_args, **_kwargs: "deny"

    def approval(*_args: object, **_kwargs: object) -> str:
        approval_threads.append(threading.get_ident())
        return approval_result

    shell._approval_callback = approval

    def run_live_process_loop(args: argparse.Namespace) -> None:
        def process_loop() -> None:
            process_thread_ids.append(threading.get_ident())
            assert terminal_tool._get_approval_callback() is approval
            args.session_ready_callback("public-review-session")

        def thread_entry() -> None:
            try:
                shell._run_process_loop_thread(process_loop)
                assert terminal_tool._get_approval_callback() is None
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(
            target=thread_entry,
            name="process_loop",
            daemon=True,
        )
        thread.start()
        thread.join(timeout=5)
        assert not thread.is_alive()

    monkeypatch.setenv("TERMINAL_ENV", "local")
    monkeypatch.setattr(command, "ReviewAuthority", FakeAuthority)
    monkeypatch.setattr(command, "_prune_completed_review_runs", lambda: None)
    monkeypatch.setattr(command, "_load_chat_command", lambda: run_live_process_loop)
    monkeypatch.setattr(
        classic_cli,
        "set_secret_capture_callback",
        global_secret_registrations.append,
    )

    # Reproduce the production ordering: run() installs callbacks on the main
    # thread before it starts process_loop. The old object-wide boolean then
    # made process_loop skip its own thread-local installation.
    shell._install_tool_callbacks()
    try:
        command.launch_review_chat(
            args=argparse.Namespace(),
            workspace=tmp_path,
            target="https://github.com/o/r/pull/42",
            effort="medium",
            skills=["requesting-code-review"],
            auto_approve=False,
            pass_session_id=True,
            query="review",
        )
    finally:
        terminal_tool.set_sudo_password_callback(None)
        terminal_tool.set_approval_callback(None)

    assert errors == []
    assert len(process_thread_ids) == 1
    assert approval_threads == process_thread_ids
    assert global_secret_registrations == [shell._secret_capture_callback]
    assert decisions[0].allowed is expected_allowed
    assert decisions[0].mode == ("local" if expected_allowed else "denied")


def test_cmd_chat_forwards_private_session_ready_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}
    fake_cli = ModuleType("cli")
    fake_cli.main = lambda **kwargs: seen.update(kwargs)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "cli", fake_cli)
    monkeypatch.setattr(main_module, "_resolve_use_tui", lambda _args: False)
    monkeypatch.setattr(main_module, "_has_any_provider_configured", lambda: True)
    monkeypatch.setattr(
        main_module, "_termux_should_prefetch_update_check", lambda: False
    )
    monkeypatch.setattr(main_module, "_sync_bundled_skills_for_startup", lambda: None)
    monkeypatch.setattr(main_module, "_pin_kanban_board_env", lambda: None)
    callback = lambda _session_id: None
    args = SimpleNamespace(
        model=None,
        provider=None,
        toolsets=None,
        query=None,
        initial_query="review",
        exit_after_initial_query=True,
        session_ready_callback=callback,
        cli=True,
        tui=False,
    )

    main_module.cmd_chat(args)

    assert seen["session_ready_callback"] is callback
    assert "query" not in seen
    assert seen["initial_query"] == "review"
    assert seen["exit_after_initial_query"] is True


def test_classic_cli_list_path_does_not_open_session_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import cli as classic_cli

    events: list[object] = []

    class FakeHermesCLI:
        session_id = "session-1"
        system_prompt = ""
        preloaded_skills: list[str] = []

        def __init__(self, **kwargs: object) -> None:
            events.append("session")
            self.session_ready_callback = kwargs.get("session_ready_callback")

        def show_banner(self) -> None:
            events.append("banner")

        def show_tools(self) -> None:
            events.append("tools")

    def fake_preload(
        skills: object, *, task_id: str
    ) -> tuple[str, list[str], list[str]]:
        events.append(("skills", skills, task_id))
        return "", [], []

    monkeypatch.setattr(classic_cli, "HermesCLI", FakeHermesCLI)
    monkeypatch.setattr(classic_cli, "build_preloaded_skills_prompt", fake_preload)

    with pytest.raises(SystemExit) as exc_info:
        classic_cli.main(
            toolsets="terminal",
            skills=["requesting-code-review"],
            list_tools=True,
            session_ready_callback=lambda session_id: events.append((
                "authority",
                session_id,
            )),
        )

    assert exc_info.value.code == 0
    assert events[:3] == [
        "session",
        ("skills", ["requesting-code-review"], "session-1"),
        "banner",
    ]
    assert not any(
        isinstance(event, tuple) and event[0] == "authority" for event in events
    )


def test_classic_cli_opens_authority_only_after_agent_session_is_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import cli as classic_cli
    from gateway.session_context import get_session_env, set_current_session_id
    from hermes_cli import mcp_startup

    events: list[object] = []
    shell: object | None = None

    def authority_ready(session_id: str) -> None:
        assert shell is not None
        assert shell.agent is agent
        assert shell.session_id == session_id
        assert agent.session_id == session_id
        assert get_session_env("HERMES_SESSION_ID", "") == session_id
        assert os.environ["HERMES_SESSION_ID"] == session_id
        events.append(("authority-serving", session_id))

    shell = classic_cli.HermesCLI(
        compact=True,
        session_ready_callback=authority_ready,
    )
    shell._session_db = object()
    shell._resumed = False
    shell.conversation_history = []
    shell._install_tool_callbacks = lambda: None
    shell._ensure_tirith_security = lambda: None
    shell._ensure_runtime_credentials = lambda: True
    monkeypatch.setattr(mcp_startup, "wait_for_mcp_discovery", lambda: None)

    agent = SimpleNamespace()

    def build_agent(*_args: object, **kwargs: object) -> object:
        agent.session_id = kwargs["session_id"]
        set_current_session_id(agent.session_id)
        events.append(("agent", agent.session_id))
        return agent

    monkeypatch.setattr(classic_cli, "AIAgent", build_agent)

    assert shell._init_agent() is True
    assert events == [
        ("agent", shell.session_id),
        ("authority-serving", shell.session_id),
    ]
    assert shell._init_agent() is True
    assert events.count(("authority-serving", shell.session_id)) == 1


def test_private_initial_query_requires_live_prompt_application() -> None:
    import cli as classic_cli

    shell = classic_cli.HermesCLI.__new__(classic_cli.HermesCLI)
    shell._initial_query = "review the pull request"
    shell._pending_input = queue.Queue()
    shell._app = SimpleNamespace(is_running=False)

    with pytest.raises(
        RuntimeError,
        match="requires a running prompt application",
    ):
        shell._submit_initial_query_after_app_start()
    assert shell._pending_input.empty()

    shell._app.is_running = True
    shell._submit_initial_query_after_app_start()

    assert shell._initial_query_pending is True
    assert shell._pending_input.get_nowait() == "review the pull request"


def test_internal_start_requires_current_session_and_live_authority(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[str] = []

    class FakeClient:
        def __init__(self, session_id: str) -> None:
            calls.append(session_id)

        def start(self) -> dict[str, str]:
            return {"runId": "run-1", "planPath": "/private/run-1/plan.json"}

    monkeypatch.setenv("HERMES_SESSION_ID", "session-1")
    monkeypatch.setattr(internal_cli, "ReviewAuthorityClient", FakeClient)

    assert internal_cli.main(["start", "--session-id", "session-1"]) == 0
    assert calls == ["session-1"]
    assert json.loads(capsys.readouterr().out) == {
        "runId": "run-1",
        "planPath": "/private/run-1/plan.json",
    }

    with pytest.raises(SystemExit):
        internal_cli.main(["start", "--session-id", "another-session"])
    assert calls == ["session-1"]


def test_internal_operation_forwards_exact_validated_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    request_wire = {
        "protocolVersion": 1,
        "requestId": "request-1",
        "command": "capture-target",
        "workspace": "/workspace",
        "artifactRoot": "/private/run",
        "input": {"target": "local"},
    }
    request_file = tmp_path / "request.json"
    request_file.write_text(json.dumps(request_wire), encoding="utf-8")
    parsed = SimpleNamespace(command="capture-target")
    response = SimpleNamespace(
        to_wire=lambda: {
            "protocolVersion": 1,
            "requestId": "request-1",
            "status": "passed",
            "output": {},
            "diagnostics": [],
        }
    )
    seen: dict[str, object] = {}

    class FakeClient:
        def __init__(self, session_id: str) -> None:
            seen["session_id"] = session_id

        def invoke(self, request: object, *, timeout: float) -> object:
            seen["request"] = request
            seen["timeout"] = timeout
            return response

    monkeypatch.setenv("HERMES_SESSION_ID", "session-1")

    def parse_request(value: object) -> object:
        seen["wire"] = value
        return parsed

    monkeypatch.setattr(internal_cli.EngineRequest, "from_wire", parse_request)
    monkeypatch.setattr(internal_cli, "ReviewAuthorityClient", FakeClient)

    assert (
        internal_cli.main([
            "capture-target",
            str(request_file),
            "--session-id",
            "session-1",
        ])
        == 0
    )
    assert seen["wire"] == request_wire
    assert seen["request"] is parsed
    assert seen["session_id"] == "session-1"
    assert json.loads(capsys.readouterr().out)["requestId"] == "request-1"


def test_internal_operation_rejects_mismatched_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    request_file = tmp_path / "request.json"
    request_file.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("HERMES_SESSION_ID", "session-1")
    monkeypatch.setattr(
        internal_cli.EngineRequest,
        "from_wire",
        lambda _value: SimpleNamespace(command="build-prompts"),
    )

    with pytest.raises(SystemExit):
        internal_cli.main([
            "capture-target",
            str(request_file),
            "--session-id",
            "session-1",
        ])


def test_internal_cleanup_run_uses_current_live_authority(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: dict[str, object] = {}
    response = SimpleNamespace(
        to_wire=lambda: {
            "protocolVersion": 1,
            "requestId": "cleanup-1",
            "status": "passed",
            "output": {"runId": "registered-run-1234"},
            "diagnostics": [],
        }
    )

    class FakeClient:
        def __init__(self, session_id: str) -> None:
            seen["session_id"] = session_id

        def cleanup(self, run_id: str, *, timeout: float) -> object:
            seen["run_id"] = run_id
            seen["timeout"] = timeout
            return response

    monkeypatch.setenv("HERMES_SESSION_ID", "session-1")
    monkeypatch.setattr(internal_cli, "ReviewAuthorityClient", FakeClient)

    assert (
        internal_cli.main([
            "cleanup",
            "--run",
            "registered-run-1234",
        ])
        == 0
    )
    assert seen == {
        "session_id": "session-1",
        "run_id": "registered-run-1234",
        "timeout": 600.0,
    }
    assert json.loads(capsys.readouterr().out)["status"] == "passed"
