import json
import threading
from pathlib import Path

import pytest

from hermes_cli.gnothi.collectors.base import CollectorContext
from hermes_cli.gnothi.collectors.experience import ExperienceCollector
from hermes_cli.gnothi.events import emit_experience_event


def _emit() -> None:
    emit_experience_event(
        event_type="tool.failed",
        generation_id="git:abc",
        component_id="tool:terminal",
        capability_id="capability:terminal",
        operation="execute",
        failure_class="ExitCode",
        severity="error",
        task_impact="blocked",
        occurred_at="2026-07-14T12:00:00Z",
    )


def test_event_writer_is_bounded_deterministic_and_private(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    _emit()
    _emit()

    path = tmp_path / "logs" / "organism-events.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(rows) == 2
    assert rows[0]["event_id"] == rows[1]["event_id"]
    assert rows[0]["bounded_signature"] == rows[1]["bounded_signature"]
    assert "result" not in rows[0]
    assert "message" not in rows[0]
    assert path.stat().st_mode & 0o777 == 0o600


def test_event_writer_keeps_concurrent_lines_valid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    threads = [threading.Thread(target=_emit) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    path = tmp_path / "logs" / "organism-events.jsonl"
    assert len([json.loads(line) for line in path.read_text().splitlines()]) == 20


def test_experience_collector_aggregates_and_marks_malformed_input_partial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _emit()
    _emit()
    path = tmp_path / "logs" / "organism-events.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write("not-json\n")

    context = CollectorContext(
        workspace_root=tmp_path,
        generation_id="git:abc",
        generation_scope="stable",
        head_commit="abc",
        collected_at="2026-07-14T13:00:00Z",
    )
    result = ExperienceCollector().collect(context)

    assert result.status == "partial"
    assert len(result.nodes) == 1
    assert result.nodes[0]["properties"]["count"] == 2
    assert result.nodes[0]["properties"]["first_seen"] == "2026-07-14T12:00:00Z"
    assert result.nodes[0]["properties"]["last_seen"] == "2026-07-14T12:00:00Z"
    assert {edge["kind"] for edge in result.edges} == {"observed_on"}
