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
            "--idempotency-key", "cli-idempotency-key-0001", "--reference", "commit:" + "a" * 40,
        ]
    )
    assert args.backend_action == "logbook"
    assert args.logbook_action == "write"
    assert args.reference == ["commit:" + "a" * 40]
    assert "write_project_logbook" in _detect_default_capabilities()


def test_parser_accepts_repeatable_and_comma_separated_logbook_list_types():
    from hermes_cli.hades_backend_cmd import build_backend_parser

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    build_backend_parser(subparsers, cmd_backend=lambda args: 0)
    args = parser.parse_args(["backend", "logbook", "list", "--type", "change,import", "--type", "wiki"])
    assert args.event_type == ["change,import", "wiki"]


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
        event_type=["change,import", "wiki"], actor="agent_1", severity="info", cursor="cursor_1", limit=10,
        client=Client(),
    )
    assert result.exit_code == 0
    assert calls == [{
        "project_id": "project_1", "workspace_binding_id": "binding_1", "types": ["change", "import", "wiki"],
        "actor": "agent_1", "severity": "info", "cursor": "cursor_1", "limit": 10,
    }]


@pytest.mark.parametrize("key", [
    "too-short",
    "unicode-idempotency-é",
    "control-idempotency\x01-key",
    " leading-idempotency-key",
    "internal idempotency-key",
    "trailing-idempotency-key ",
    "x" * 129,
])
def test_logbook_write_rejects_noncanonical_idempotency_key_before_outbox_persistence(key, tmp_path):
    from hermes_cli import hades_backend_db as db
    from hermes_cli.hades_logbook_actions import run_logbook_write

    binding = SimpleNamespace(project_id="project_1", backend_workspace_binding_id="binding_1")
    conn = db.connect(tmp_path / "backend.db")

    class Client:
        calls = 0

        def create_logbook_entry(self, *_args, **_kwargs):
            self.calls += 1
            raise AssertionError("invalid logbook input must not reach the network")

    client = Client()
    with pytest.raises(ValueError, match="idempotency key"):
        run_logbook_write(conn, command={
            "event_type": "change", "summary": "Done", "idempotency_key": key, "references": [],
        }, binding=binding, client=client, now=1000)
    assert db.list_logbook_outbox_entries(conn) == []
    assert client.calls == 0


@pytest.mark.parametrize("references", [
    [{"kind": "commit", "id": str(index)} for index in range(21)],
    [{"kind": "graph_projection", "id": "projection_1"}],
    [{"kind": "commit", "id": "a" * 39}],
    [{"kind": "commit", "id": "A" * 40}],
    *[[{"kind": "file", "id": path}] for path in (
        "/absolute", "\\absolute", "C:\\absolute", "folder//file", "folder/./file", "folder/../file",
        "folder\\..\\file", "x" * 2049, "é" * 1025,
    )],
    [{"kind": "commit", "id": "a" * 40}, {"kind": "commit", "id": "a" * 40}],
])
def test_logbook_write_rejects_noncanonical_references_before_outbox_persistence(references, tmp_path):
    from hermes_cli import hades_backend_db as db
    from hermes_cli.hades_logbook_actions import run_logbook_write

    binding = SimpleNamespace(project_id="project_1", backend_workspace_binding_id="binding_1")
    conn = db.connect(tmp_path / "backend.db")
    class Client:
        calls = 0

        def create_logbook_entry(self, *_args, **_kwargs):
            self.calls += 1
            raise AssertionError("invalid logbook references must not reach the network")

    client = Client()
    with pytest.raises(ValueError, match="reference"):
        run_logbook_write(conn, command={
            "event_type": "change", "summary": "Done", "idempotency_key": "action-idempotency-0001",
            "references": references,
        }, binding=binding, client=client, now=1000)
    assert db.list_logbook_outbox_entries(conn) == []
    assert client.calls == 0


@pytest.mark.parametrize("field, value", [
    ("summary", "<b>Done</b>"),
    ("narrative_markdown", "A factual note with <script>alert(1)</script>."),
])
def test_logbook_write_rejects_raw_html_before_outbox_or_network(field, value, tmp_path):
    from hermes_cli import hades_backend_db as db
    from hermes_cli.hades_logbook_actions import run_logbook_write

    binding = SimpleNamespace(project_id="project_1", backend_workspace_binding_id="binding_1")
    conn = db.connect(tmp_path / "backend.db")

    class Client:
        calls = 0

        def create_logbook_entry(self, *_args, **_kwargs):
            self.calls += 1
            raise AssertionError("raw HTML must not reach the network")

    client = Client()
    command = {
        "event_type": "change", "summary": "**Markdown** stays valid",
        "idempotency_key": "html-idempotency-key-0001", "references": [],
    }
    command[field] = value
    with pytest.raises(ValueError, match="HTML"):
        run_logbook_write(conn, command=command, binding=binding, client=client, now=1000)
    assert db.list_logbook_outbox_entries(conn) == []
    assert client.calls == 0


def test_logbook_references_are_canonicalized_by_kind_and_id():
    from hermes_cli.hades_logbook_actions import canonical_logbook_request

    binding = SimpleNamespace(backend_workspace_binding_id="binding_1")
    request = canonical_logbook_request({
        "event_type": "change", "summary": "Done", "idempotency_key": "canonical-idempotency-0001",
        "references": ["file:src/main.py", "commit:" + "a" * 40, "repository:repo_1"],
    }, binding)
    assert request["references"] == [
        {"kind": "commit", "id": "a" * 40},
        {"kind": "file", "id": "src/main.py"},
        {"kind": "repository", "id": "repo_1"},
    ]


def test_logbook_accepts_markdown_without_raw_html():
    from hermes_cli.hades_logbook_actions import canonical_logbook_request

    request = canonical_logbook_request({
        "event_type": "note", "summary": "**Markdown** is factual",
        "narrative_markdown": "- a markdown item\n- another item",
        "idempotency_key": "markdown-idempotency-0001", "references": [],
    }, SimpleNamespace(backend_workspace_binding_id="binding_1"))
    assert request["summary"] == "**Markdown** is factual"
