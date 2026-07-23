from __future__ import annotations

import argparse
import json
import os
import sys
from types import ModuleType
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_cli.engineering_review import command, internal_cli
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


def test_review_parser_exposes_only_target_and_effort(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        _parser().parse_args(["review", "--help"])
    assert exc_info.value.code == 0
    help_text = capsys.readouterr().out
    assert "[target]" in help_text
    assert "--effort {low,medium,high}" in help_text
    for hidden in ("runner", "session", "skill", "model", "provider", "operation"):
        assert f"--{hidden}" not in help_text


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
    }
    assert events[2:] == ["start", "running", "close"]
    assert os.environ["HERMES_YOLO_MODE"] == "1"


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
        query="review",
        session_ready_callback=callback,
        cli=True,
        tui=False,
    )

    main_module.cmd_chat(args)

    assert seen["session_ready_callback"] is callback
    assert seen["query"] == "review"


def test_classic_cli_opens_session_authority_before_skill_preprocessing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import cli as classic_cli

    events: list[object] = []

    class FakeHermesCLI:
        session_id = "session-1"
        system_prompt = ""
        preloaded_skills: list[str] = []

        def __init__(self, **_kwargs: object) -> None:
            events.append("session")

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
        ("authority", "session-1"),
        ("skills", ["requesting-code-review"], "session-1"),
    ]


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
