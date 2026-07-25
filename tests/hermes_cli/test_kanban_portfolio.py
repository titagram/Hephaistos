from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli.hierarchical_execution import (
    parse_execution_portfolio,
    validate_execution_portfolio,
)
from hermes_cli.kanban_portfolio import create_org_run


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


def payload():
    return {
        "schema": "hades.execution-portfolio.v1",
        "org_run_id": "org_demo_001",
        "project_id": "proj_1",
        "repository_id": "repo_1",
        "workspace_binding_id": "wb_1",
        "base_commit": "a" * 40,
        "tasks": [{
            "remote_task_id": "HD-101",
            "work_item_id": "awi_101",
            "title": "Change contract",
            "body": "Implement the bounded change.",
            "assignee": "default",
            "priority": 10,
            "risk": "high",
            "depends_on": [],
            "write_scope": ["hermes_cli/contracts.py"],
        }],
    }


def test_create_org_run_separates_anchor_execution_review_and_completion(tmp_path):
    conn = kb.connect(tmp_path / "kanban.db")
    try:
        plan = parse_execution_portfolio(payload())
        created = create_org_run(conn, plan, validate_execution_portfolio(plan))
        remote = created.remote_tasks["HD-101"]
        assert kb.get_task(conn, created.anchor_id).status == "done"
        assert kb.get_task(conn, remote.anchor_id).status == "done"
        assert kb.get_task(conn, remote.execution_id).status == "ready"
        assert kb.get_task(conn, remote.review_id).status == "todo"
        assert kb.get_task(conn, remote.integration_ready_id).status == "todo"
        assert kb.get_task(conn, remote.completion_id).status == "todo"
        assert kb.parent_ids(conn, remote.execution_id) == [remote.anchor_id]
        assert kb.parent_ids(conn, remote.review_id) == [remote.execution_id]
        assert kb.parent_ids(conn, remote.integration_ready_id) == [remote.review_id]
        assert kb.parent_ids(conn, created.integration_id) == [remote.integration_ready_id]
        assert kb.parent_ids(conn, remote.completion_id) == [created.review_id]
    finally:
        conn.close()


def test_create_org_run_is_idempotent(tmp_path):
    conn = kb.connect(tmp_path / "kanban.db")
    try:
        plan = parse_execution_portfolio(payload())
        validation = validate_execution_portfolio(plan)
        first = create_org_run(conn, plan, validation)
        count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        second = create_org_run(conn, plan, validation)
        assert second == first
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == count
    finally:
        conn.close()


def test_inactive_org_run_stays_in_triage(tmp_path):
    conn = kb.connect(tmp_path / "kanban.db")
    try:
        plan = parse_execution_portfolio(payload())
        created = create_org_run(
            conn,
            plan,
            validate_execution_portfolio(plan),
            activate=False,
        )
        assert kb.get_task(conn, created.remote_tasks["HD-101"].execution_id).status == "triage"
    finally:
        conn.close()


def test_remote_dependency_links_integration_ready_to_execution(tmp_path):
    raw = payload()
    raw["tasks"].append({
        "remote_task_id": "HD-102",
        "work_item_id": "awi_102",
        "title": "Dependent change",
        "body": "Wait for the contract.",
        "assignee": "default",
        "priority": 1,
        "risk": "medium",
        "depends_on": ["HD-101"],
        "write_scope": ["hermes_cli/consumer.py"],
    })
    conn = kb.connect(tmp_path / "kanban.db")
    try:
        plan = parse_execution_portfolio(raw)
        created = create_org_run(conn, plan, validate_execution_portfolio(plan))
        parent_ready = created.remote_tasks["HD-101"].integration_ready_id
        dependent_execution = created.remote_tasks["HD-102"].execution_id
        assert parent_ready in kb.parent_ids(conn, dependent_execution)
        assert parent_ready != created.remote_tasks["HD-101"].completion_id
    finally:
        conn.close()


def test_create_org_run_runnable_nodes_inherit_board_default_workdir(kanban_home):
    """When the selected board has a non-empty default_workdir, all
    runnable OrgRun nodes inherit workspace_kind=dir."""
    project = kanban_home / "project"
    project.mkdir()

    kb.create_board("org-board", default_workdir=str(project))

    with kb.connect(board="org-board") as conn:
        plan = parse_execution_portfolio(payload())
        created = create_org_run(
            conn, plan, validate_execution_portfolio(plan), board="org-board",
        )

        remote = created.remote_tasks["HD-101"]

        # Anchor nodes remain scratch (metadata only).
        anchor = kb.get_task(conn, created.anchor_id)
        assert anchor is not None
        assert anchor.workspace_kind == "scratch"
        assert anchor.workspace_path is None

        remote_anchor = kb.get_task(conn, remote.anchor_id)
        assert remote_anchor is not None
        assert remote_anchor.workspace_kind == "scratch"

        # Runnable nodes: all inherit dir + board default_workdir.
        runnable_tids = [
            remote.execution_id, remote.review_id, remote.integration_ready_id,
            created.integration_id, created.review_id,
            remote.completion_id, created.synthesis_id,
        ]
        for tid in runnable_tids:
            t = kb.get_task(conn, tid)
            assert t is not None
            assert t.workspace_kind == "dir", f"task {tid} expected dir, got {t.workspace_kind}"
            assert t.workspace_path == str(project)


def test_create_org_run_no_board_default_preserves_scratch(kanban_home):
    """When the selected board has no default_workdir, runnable OrgRun
    nodes keep the default scratch workspace."""
    kb.create_board("no-def-board")

    with kb.connect(board="no-def-board") as conn:
        plan = parse_execution_portfolio(payload())
        created = create_org_run(
            conn, plan, validate_execution_portfolio(plan), board="no-def-board",
        )

        remote = created.remote_tasks["HD-101"]

        # Every runnable node should stay scratch with no workspace_path.
        for tid in [
            remote.execution_id,
            remote.review_id,
            remote.integration_ready_id,
            created.integration_id,
            created.review_id,
            remote.completion_id,
            created.synthesis_id,
        ]:
            t = kb.get_task(conn, tid)
            assert t is not None
            assert t.workspace_kind == "scratch", (
                f"task {tid} expected scratch, got {t.workspace_kind}"
            )

        # Anchors are also scratch (unchanged).
        anchor = kb.get_task(conn, created.anchor_id)
        assert anchor is not None
        assert anchor.workspace_kind == "scratch"


def test_create_org_run_inherits_explicit_board_not_default_board(kanban_home):
    """OrgRun workspace inheritance respects the explicitly selected board,
    not the default board."""
    project_a = kanban_home / "proj-a"
    project_a.mkdir()

    kb.create_board("board-a", default_workdir=str(project_a))
    kb.create_board("board-b")

    with kb.connect(board="board-a") as conn:
        plan = parse_execution_portfolio(payload())
        created = create_org_run(
            conn, plan, validate_execution_portfolio(plan), board="board-a",
        )

        remote = created.remote_tasks["HD-101"]
        exec_task = kb.get_task(conn, remote.execution_id)
        assert exec_task is not None
        assert exec_task.workspace_kind == "dir"
        assert exec_task.workspace_path == str(project_a)

    # Same plan on board-b (no default_workdir) should produce scratch.
    with kb.connect(board="board-b") as conn:
        plan2 = parse_execution_portfolio(payload())
        created2 = create_org_run(
            conn, plan2, validate_execution_portfolio(plan2), board="board-b",
        )
        remote2 = created2.remote_tasks["HD-101"]
        exec_task2 = kb.get_task(conn, remote2.execution_id)
        assert exec_task2 is not None
        assert exec_task2.workspace_kind == "scratch"
