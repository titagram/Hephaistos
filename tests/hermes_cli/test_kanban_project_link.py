"""Kanban <-> Projects integration: project-linked tasks get a deterministic
worktree path + branch instead of the random ``wt/<task-id>`` fallback."""

from __future__ import annotations

import json
import os

import pytest

from hermes_cli import hades_kanban_sync
from hermes_cli import kanban as kc
from hermes_cli import kanban_backend
from hermes_cli import kanban_db as kb
from hermes_cli import projects_db as pdb


@pytest.fixture
def kanban_conn(tmp_path, monkeypatch):
    """A board-backed connection so dispatch uses the same isolated lock path."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr("hermes_cli.kanban_db.Path.home", lambda: tmp_path)
    kb.init_db()
    c = kb.connect()
    try:
        yield c
    finally:
        c.close()


def _make_project(name="Web App", repo="/tmp/webapp"):
    with pdb.connect_closing() as pc:
        pid = pdb.create_project(pc, name=name, folders=[repo])
        return pdb.get_project(pc, pid)


def test_project_linked_task_gets_deterministic_worktree_and_branch(kanban_conn):
    proj = _make_project()
    tid = kb.create_task(kanban_conn, title="Add login", project_id=proj.slug)
    task = kb.get_task(kanban_conn, tid)

    assert task.project_id == proj.id
    assert task.workspace_kind == "worktree"
    # Worktree dir anchored under the project's primary repo, keyed on task id.
    assert task.workspace_path == os.path.join(proj.primary_path, ".worktrees", tid)
    # Deterministic branch: <slug>/<task-id>-<title-slug>. NOT a random wt/...
    assert task.branch_name == f"{proj.slug}/{tid}-add-login"
    assert not task.branch_name.startswith("wt/")


def test_explicit_branch_overrides_project_default(kanban_conn):
    proj = _make_project()
    tid = kb.create_task(
        kanban_conn,
        title="x",
        project_id=proj.slug,
        workspace_kind="worktree",
        branch_name="feature/custom",
    )
    task = kb.get_task(kanban_conn, tid)
    assert task.branch_name == "feature/custom"


def test_unlinked_task_unchanged(kanban_conn):
    tid = kb.create_task(kanban_conn, title="plain")
    task = kb.get_task(kanban_conn, tid)

    assert task.project_id is None
    assert task.workspace_kind == "scratch"
    # No branch is persisted — the worker still owns the wt/<id> fallback for
    # genuinely ad-hoc worktree tasks, but unlinked scratch tasks have none.
    assert task.branch_name is None


def test_project_linked_task_is_still_local_without_remote_link(
    kanban_conn, monkeypatch, tmp_path,
):
    """The CLI admission path does not treat project_id as a remote link."""
    monkeypatch.setattr("hermes_cli.profiles.profile_exists", lambda _name: True)

    def _forbid_remote_edge(*_args, **_kwargs):
        raise AssertionError(
            "a project-linked local card must not use a backend client, "
            "sync, or remote admission"
        )

    monkeypatch.setattr(kanban_backend, "make_kanban_client", _forbid_remote_edge)
    monkeypatch.setattr(hades_kanban_sync, "_make_remote_client", _forbid_remote_edge)
    monkeypatch.setattr(kanban_backend, "maybe_run_kanban_sync", _forbid_remote_edge)
    monkeypatch.setattr(hades_kanban_sync, "make_remote_admission", _forbid_remote_edge)
    spawned = []
    monkeypatch.setattr(
        kb,
        "_default_spawn",
        lambda task, *_args, **_kwargs: spawned.append(task.id) or 12345,
    )
    project = _make_project()
    local_workspace = tmp_path / "local-project-workspace"
    local_workspace.mkdir()
    task_id = kb.create_task(
        kanban_conn,
        title="local project work",
        project_id=project.slug,
        assignee="leaf",
        workspace_kind="dir",
        workspace_path=str(local_workspace),
    )
    payload = json.loads(kc.run_slash("dispatch --json"))

    assert [entry["task_id"] for entry in payload["spawned"]] == [task_id]
    assert spawned == [task_id]
    assert kb.get_remote_link(kanban_conn, task_id) is None
    assert f"Completed {task_id}" in kc.run_slash(
        f"complete {task_id} --result 'local project complete'"
    )
    task = kb.get_task(kanban_conn, task_id)
    assert task is not None and task.status == "done"


def test_unknown_project_id_falls_back_gracefully(kanban_conn):
    # A project id that doesn't resolve must not crash task creation; the task
    # is created as-is (scratch) and project_id stays unset.
    tid = kb.create_task(kanban_conn, title="x", project_id="does-not-exist")
    task = kb.get_task(kanban_conn, tid)
    assert task.workspace_kind == "scratch"
    assert task.project_id is None
