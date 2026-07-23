"""Focused delegate-tool integration tests for review evidence recording."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tools.delegate_tool import DELEGATE_TASK_SCHEMA, _run_single_child


def _parent() -> MagicMock:
    parent = MagicMock()
    parent.session_id = "parent"
    parent._current_task_id = None
    parent._active_children = []
    parent._active_children_lock = None
    return parent


def _child() -> MagicMock:
    child = MagicMock()
    child._delegate_role = "reviewer"
    child._delegate_task_contract = None
    child._subagent_id = "sa-0-reviewer"
    child.run_conversation.return_value = {
        "final_response": "Reviewed.",
        "completed": True,
        "interrupted": False,
        "api_calls": 1,
        "messages": [],
    }
    return child


def test_run_single_child_returns_only_run_relative_review_evidence_ref(
    tmp_path: Path,
) -> None:
    root = tmp_path / "run"
    evidence = root / "subagents" / "reviewers" / "agent-sa-0-reviewer.jsonl"
    state = SimpleNamespace(base_commit="a" * 40, diff_hash="same", file_hashes=())

    with (
        patch("tools.delegate_tool.capture_git_state", return_value=state),
        patch(
            "agent.review_evidence.write_reviewer_transcript", return_value=evidence
        ) as recorder,
    ):
        entry = _run_single_child(0, "review prompt", _child(), _parent())

    recorder.assert_called_once()
    assert entry["review_evidence_ref"] == (
        "subagents/reviewers/agent-sa-0-reviewer.jsonl"
    )
    assert not Path(entry["review_evidence_ref"]).is_absolute()
    assert "review_evidence_path" not in entry
    assert "review_evidence_ref" not in DELEGATE_TASK_SCHEMA["parameters"]["properties"]


def test_evidence_recorder_failure_does_not_change_child_result() -> None:
    state = SimpleNamespace(base_commit="a" * 40, diff_hash="same", file_hashes=())

    with (
        patch("tools.delegate_tool.capture_git_state", return_value=state),
        patch(
            "agent.review_evidence.write_reviewer_transcript",
            side_effect=OSError("disk full"),
        ),
    ):
        entry = _run_single_child(0, "review prompt", _child(), _parent())

    assert entry["status"] == "completed"
    assert entry["summary"] == "Reviewed."
    assert "review_evidence_ref" not in entry
