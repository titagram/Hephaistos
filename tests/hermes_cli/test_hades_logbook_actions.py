from __future__ import annotations

import argparse
import os
from types import SimpleNamespace

import pytest


def test_narrative_file_must_be_regular_utf8_and_at_most_8000_code_points(tmp_path):
    from hermes_cli.hades_logbook_actions import read_narrative_file

    valid = tmp_path / "narrative.md"
    valid.write_text("done", encoding="utf-8")
    assert read_narrative_file(valid) == "done"

    oversized = tmp_path / "oversized.md"
    oversized.write_text("x" * 8001, encoding="utf-8")
    with pytest.raises(ValueError, match="8,000"):
        read_narrative_file(oversized)

    directory = tmp_path / "directory"
    directory.mkdir()
    with pytest.raises(ValueError, match="regular"):
        read_narrative_file(directory)


def test_narrative_file_is_read_through_one_nofollow_file_descriptor(monkeypatch, tmp_path):
    """The validation must describe the same file descriptor that is read."""

    from hermes_cli import hades_logbook_actions as actions

    narrative = tmp_path / "narrative.md"
    narrative.write_text("done", encoding="utf-8")
    opened: list[int] = []
    real_open = os.open

    def tracking_open(path, flags, *args):
        opened.append(flags)
        return real_open(path, flags, *args)

    monkeypatch.setattr(os, "open", tracking_open)
    monkeypatch.setattr(
        actions.Path,
        "read_bytes",
        lambda _path: (_ for _ in ()).throw(AssertionError("must read the opened descriptor")),
    )

    assert actions.read_narrative_file(narrative) == "done"
    assert opened
    if hasattr(os, "O_NOFOLLOW"):
        assert opened[0] & os.O_NOFOLLOW


def test_parser_accepts_bounded_logbook_commands_and_advertises_capability():
    from hermes_cli.hades_backend_cmd import _detect_default_capabilities, build_backend_parser

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    build_backend_parser(subparsers, cmd_backend=lambda args: 0)
    args = parser.parse_args(
        [
            "backend", "logbook", "write", "--type", "change", "--summary", "Done",
            "--idempotency-key", "key_1", "--reference", "commit:abc123",
        ]
    )
    assert args.backend_action == "logbook"
    assert args.logbook_action == "write"
    assert args.reference == ["commit:abc123"]
    assert "write_project_logbook" in _detect_default_capabilities()


def test_run_logbook_list_forwards_only_bound_workspace_filters(monkeypatch):
    from hermes_cli import hades_logbook_actions as actions

    calls: list[dict[str, object]] = []

    class Client:
        def list_logbook_entries(self, project_id, **payload):
            calls.append({"project_id": project_id, **payload})
            return {"items": []}

    binding = SimpleNamespace(project_id="project_1", backend_workspace_binding_id="binding_1")
    monkeypatch.setattr(actions, "_current_agent_binding", lambda: (SimpleNamespace(), binding))
    result = actions.run_logbook_list(
        event_type="change", actor="agent_1", severity="info", cursor="cursor_1", limit=10, client=Client()
    )
    assert result.exit_code == 0
    assert calls == [{
        "project_id": "project_1", "workspace_binding_id": "binding_1", "types": "change",
        "actor": "agent_1", "severity": "info", "cursor": "cursor_1", "limit": 10,
    }]
