"""Tests for optional per-workspace Kanban backend context."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from hermes_cli import hades_backend_db as hdb
from hermes_cli.kanban_backend import make_kanban_client, resolve_kanban_backend_context


@pytest.fixture
def linked_backends(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    workspace_one = tmp_path / "one"
    workspace_two = tmp_path / "two"
    workspace_one.mkdir()
    workspace_two.mkdir()
    with hdb.connect_closing() as conn:
        for suffix, workspace in (("one", workspace_one), ("two", workspace_two)):
            hdb.save_agent(
                conn, agent_id=f"agent-{suffix}", project_id=f"project-{suffix}",
                base_url="https://backend.example", label=suffix,
                token_env_key=f"TOKEN_{suffix.upper()}", capabilities={"jobs": True},
            )
            hdb.upsert_workspace_binding(
                conn, project_id=f"project-{suffix}", agent_id=f"agent-{suffix}",
                local_project_id=f"local-{suffix}",
                workspace_fingerprint=f"fingerprint-{suffix}",
                display_path=str(workspace), repo_root=str(workspace),
                git_remote_display="", git_remote_hash="", head_commit="",
                backend_workspace_binding_id=f"binding-{suffix}",
            )
    return SimpleNamespace(workspace_one=workspace_one, workspace_two=workspace_two)


def test_context_is_healthy_local_only_without_backend_db(tmp_path, monkeypatch):
    """Missing backend state leaves a local board healthy without creating it."""
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))

    context = resolve_kanban_backend_context(cwd=tmp_path)

    assert context.mode == "local_only"
    assert context.workspace_root == tmp_path.resolve()
    assert context.workspace_binding_id is None
    assert context.error is None
    assert not home.exists()


def test_context_selects_only_current_workspace_binding(linked_backends):
    """A workspace must resolve its own linked binding, never another one."""
    current = resolve_kanban_backend_context(cwd=linked_backends.workspace_one)

    assert current.mode == "linked"
    assert current.workspace_binding_id == "binding-one"
    assert current.project_id == "project-one"
    assert current.agent_id == "agent-one"
    assert current.local_workspace_id == "local-one"


def test_client_uses_the_context_agent_not_the_profile_default(linked_backends):
    """The selected workspace's agent owns its client even when another is newer."""
    context = resolve_kanban_backend_context(cwd=linked_backends.workspace_one)

    client = make_kanban_client(
        context,
        client_factory=lambda agent: {"agent_id": agent.agent_id, "project_id": agent.project_id},
    )

    assert client == {"agent_id": "agent-one", "project_id": "project-one"}
