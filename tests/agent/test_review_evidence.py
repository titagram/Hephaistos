"""Harness-authored evidence for engineering-review subagents."""

from __future__ import annotations

import json
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.review_evidence import write_reviewer_transcript
from hermes_cli.engineering_review.runs import ReviewRun


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "profile-home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


def _registered_run(fake_home: Path, tmp_path: Path) -> ReviewRun:
    del fake_home
    run = ReviewRun.create(
        tmp_path / "workspace", target="local", effort="medium", session_id="parent"
    )
    run.atomic_artifact("plan.json", b'{"diffPathAbsolute":"/tmp/review.diff"}')
    return run


def _reviewer(prompt: str, *, agent_id: str = "sa-0-reviewer") -> SimpleNamespace:
    return SimpleNamespace(
        _delegate_role="reviewer",
        _subagent_goal=prompt,
        _subagent_id=agent_id,
    )


def _child_result() -> dict[str, object]:
    return {
        "final_response": "Review complete. password=final-secret",
        "messages": [
            {"role": "user", "content": "unrelated user message with credential"},
            {
                "role": "assistant",
                "reasoning": "secret reasoning",
                "tool_calls": [
                    {
                        "id": "diff-read",
                        "function": {
                            "name": "read_file",
                            "arguments": json.dumps({
                                "file_path": "/tmp/review.diff",
                                "offset": 4,
                                "limit": 3,
                                "api_key": "argument-secret",
                                "environment": {
                                    "LANG": "C.UTF-8",
                                    "OPENAI_API_KEY": "nested-environment-secret",
                                },
                            }),
                        },
                    },
                    {
                        "id": "denied-read",
                        "function": {
                            "name": "read_file",
                            "arguments": json.dumps({"file_path": "/forbidden"}),
                        },
                    },
                    {
                        "id": "brief-read",
                        "function": {
                            "name": "read_file",
                            "arguments": json.dumps({
                                "file_path": "/tmp/chunk-1.brief.md"
                            }),
                        },
                    },
                    {
                        "id": "cancelled-read",
                        "function": {
                            "name": "read_file",
                            "arguments": json.dumps({"file_path": "/cancelled"}),
                        },
                    },
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "diff-read",
                "content": (
                    "diff contents\n"
                    "Authorization: Bearer result-secret\n"
                    "OPENAI_API_KEY=sk-openai-result\n"
                    "ANTHROPIC_API_KEY: sk-anthropic-result\n"
                    "HERMES_TOKEN=hermes-token-result\n"
                    "AWS_SECRET_ACCESS_KEY=aws-secret-result\n"
                    "cookie: cookie-result"
                ),
            },
            {
                "role": "tool",
                "tool_call_id": "denied-read",
                "content": "Denied by the approval policy",
            },
            {
                "role": "tool",
                "tool_call_id": "brief-read",
                "content": "brief contents",
            },
            {
                "role": "tool",
                "tool_call_id": "cancelled-read",
                "content": (
                    "[Tool execution cancelled — read_file was skipped due to "
                    "user interrupt]"
                ),
            },
            {
                "role": "assistant",
                "content": "unrelated intermediate text",
                "reasoning": "more secret reasoning",
            },
        ],
        "environment": {"API_KEY": "environment-secret"},
        "credentials": {"token": "credential-secret"},
    }


def test_only_active_registered_review_markers_create_private_evidence(
    fake_home: Path, tmp_path: Path
) -> None:
    run = _registered_run(fake_home, tmp_path)
    prompt = (
        f"Hermes-Review-Run: {run.run_id}\n"
        f"Hermes-Review-Plan: {run.root / 'plan.json'}\n"
        "Read the brief and diff."
    )

    path = write_reviewer_transcript("parent", _reviewer(prompt), _child_result())

    assert path == run.root / "subagents" / "reviewers" / "agent-sa-0-reviewer.jsonl"
    assert path.is_file()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.parent.parent.stat().st_mode) == 0o700
    assert not list(path.parent.glob(".*.tmp"))

    raw = path.read_text(encoding="utf-8")
    assert "secret reasoning" not in raw
    assert "unrelated user message" not in raw
    assert "environment-secret" not in raw
    assert "credential-secret" not in raw
    assert "argument-secret" not in raw
    assert "result-secret" not in raw
    assert "final-secret" not in raw
    assert "sk-openai-result" not in raw
    assert "sk-anthropic-result" not in raw
    assert "hermes-token-result" not in raw
    assert "aws-secret-result" not in raw
    assert "cookie-result" not in raw
    assert "cancelled-read" not in raw

    records = [json.loads(line) for line in raw.splitlines()]
    assert records[0]["type"] == "user"
    assert records[0]["message"]["parts"] == [{"text": prompt}]
    assert {record["agentId"] for record in records} == {"sa-0-reviewer"}
    assert {record["agentName"] for record in records} == {"reviewer"}
    assert all(isinstance(record["timestamp"], str) for record in records)

    calls = [
        part["functionCall"]
        for record in records
        for part in record["message"]["parts"]
        if "functionCall" in part
    ]
    assert [call["id"] for call in calls] == ["diff-read", "brief-read"]
    assert "api_key" not in calls[0]["args"]
    assert "environment" not in calls[0]["args"]
    assert records[-1]["type"] == "assistant"
    assert records[-1]["message"]["parts"] == [
        {"text": "Review complete. password=[REDACTED]"}
    ]


@pytest.mark.parametrize(
    ("session_id", "role", "prompt_factory"),
    [
        (
            "parent",
            "leaf",
            lambda run: (
                f"Hermes-Review-Run: {run.run_id}\n"
                f"Hermes-Review-Plan: {run.root / 'plan.json'}"
            ),
        ),
        ("parent", "reviewer", lambda run: "Hermes-Review-Run: forged"),
        (
            "other-parent",
            "reviewer",
            lambda run: (
                f"Hermes-Review-Run: {run.run_id}\n"
                f"Hermes-Review-Plan: {run.root / 'plan.json'}"
            ),
        ),
        (
            "parent",
            "reviewer",
            lambda run: (
                f"Hermes-Review-Run: {run.run_id}\n"
                f"Hermes-Review-Run: {run.run_id}\n"
                f"Hermes-Review-Plan: {run.root / 'plan.json'}"
            ),
        ),
        (
            "parent",
            "reviewer",
            lambda run: (
                f"prefix Hermes-Review-Run: {run.run_id}\n"
                f"Hermes-Review-Plan: {run.root / 'plan.json'}"
            ),
        ),
        (
            "parent",
            "reviewer",
            lambda run: (
                f"Hermes-Review-Run: {run.run_id}\n"
                f"Hermes-Review-Plan: {run.root / 'other.json'}"
            ),
        ),
    ],
)
def test_nonreviewer_and_forged_markers_write_nothing(
    fake_home: Path,
    tmp_path: Path,
    session_id: str,
    role: str,
    prompt_factory,
) -> None:
    run = _registered_run(fake_home, tmp_path)
    child = _reviewer(prompt_factory(run))
    child._delegate_role = role

    assert write_reviewer_transcript(session_id, child, _child_result()) is None
    assert not (run.root / "subagents").exists()


def test_completed_run_and_unsafe_agent_id_write_nothing(
    fake_home: Path, tmp_path: Path
) -> None:
    run = _registered_run(fake_home, tmp_path)
    prompt = (
        f"Hermes-Review-Run: {run.run_id}\nHermes-Review-Plan: {run.root / 'plan.json'}"
    )

    assert (
        write_reviewer_transcript(
            "parent", _reviewer(prompt, agent_id="../escape"), _child_result()
        )
        is None
    )
    run.mark_complete()
    assert (
        write_reviewer_transcript("parent", _reviewer(prompt), _child_result()) is None
    )
    assert not (run.root / "subagents").exists()
