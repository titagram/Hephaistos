from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from hermes_cli import kanban_db as kb
from hermes_cli.hierarchical_execution import (
    parse_execution_portfolio,
    validate_execution_portfolio,
)
from hermes_cli.kanban_portfolio import create_org_run


REPOSITORY = Path(__file__).resolve().parents[2]


def _head() -> str:
    return subprocess.run(
        ["git", "-C", str(REPOSITORY), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _plan_payload() -> dict:
    return {
        "schema": "hades.implementation-plan.v1",
        "run_id": "local-run-001",
        "objective": "Ship an offline OrgRun",
        "base_commit": _head(),
        "acceptance_criteria": ["All focused tests pass"],
        "tasks": [{
            "id": "runtime",
            "title": "Disconnect runtime sync",
            "role": "leaf",
            "risk": "high",
            "write_scope": ["hermes_cli/kanban.py"],
            "depends_on": [],
            "acceptance_criteria": ["No backend client is constructed"],
            "verification": ["pytest tests/hermes_cli/test_kanban_cli.py"],
            "independent_review": True,
        }],
    }


def _legacy_payload(*, run_id: str) -> dict:
    return {
        "schema": "hades.execution-portfolio.v1",
        "org_run_id": run_id,
        "project_id": "legacy-project",
        "repository_id": "legacy-repository",
        "workspace_binding_id": "must-not-be-read",
        "base_commit": _head(),
        "tasks": [{
            "remote_task_id": "runtime",
            "work_item_id": "legacy-work-item",
            "title": "Legacy runtime",
            "body": "Implement the bounded change.",
            "assignee": "default",
            "priority": 1,
            "risk": "high",
            "depends_on": [],
            "write_scope": ["hermes_cli/legacy.py"],
        }],
    }


def _configure_local_board(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    (home / "config.yaml").write_text(
        """delegation:
  profiles:
    local:
      provider: local
      model: local-model
      max_iterations: 1
      child_timeout_seconds: 1
  role_routes:
    leaf: local
    orchestrator: local
    reviewer: local
""",
        encoding="utf-8",
    )
    kb.write_board_metadata("default", default_workdir=str(REPOSITORY))


def _write_json(tmp_path: Path, name: str, payload: dict) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_local_plan_cli_validates_materializes_amends_and_shows_state(tmp_path, monkeypatch):
    """Breaks if org still accepts legacy portfolios or exposes routing details."""
    from hermes_cli.hades_org_cmd import (
        amend_org_run_file,
        materialize_plan_file,
        show_org_run,
        validate_plan_file,
    )

    _configure_local_board(tmp_path, monkeypatch)
    plan_path = _write_json(tmp_path, "plan.json", _plan_payload())

    valid, code = validate_plan_file(str(plan_path), board="default")

    assert code == 0
    assert valid == {
        "status": "valid",
        "schema": "hades.implementation-plan.v1",
        "run_id": "local-run-001",
        "task_count": 1,
        "conflict_count": 0,
        "plan_hash": valid["plan_hash"],
        "resolved_profiles": {
            "leaf": "leaf",
            "orchestrator": "orchestrator",
            "reviewer": "reviewer",
        },
        "routed_roles": ["leaf", "orchestrator", "reviewer"],
    }

    materialized, code = materialize_plan_file(str(plan_path), board="default")

    assert code == 0
    assert materialized["status"] == "materialized"
    assert materialized["run_id"] == "local-run-001"
    assert materialized["plan_hash"] == valid["plan_hash"]

    shown, code = show_org_run("local-run-001", board="default")

    assert code == 0
    assert shown["status"] == "ok"
    assert shown["state"] == "materialized"
    assert shown["plan_version"] == 1
    assert shown["plan_hash"] == valid["plan_hash"]
    assert shown["topology"] == materialized["topology"]
    assert shown["blocked_nodes"] == []
    assert shown["dispatchable_nodes"] == [
        "org-run:local-run-001:task:runtime"
    ]
    assert shown["report_ids"] == []

    amendment_path = _write_json(
        tmp_path,
        "amendment.json",
        {
            "schema": "hades.implementation-amendment.v1",
            "run_id": "local-run-001",
            "base_plan_version": 1,
            "reason": "Add a regression check",
            "add_tasks": [{
                "id": "regression",
                "title": "Add regression coverage",
                "role": "leaf",
                "risk": "low",
                "write_scope": ["tests/hermes_cli/test_hades_org_cli.py"],
                "depends_on": ["runtime"],
                "acceptance_criteria": ["Regression is covered"],
                "verification": ["pytest tests/hermes_cli/test_hades_org_cli.py"],
                "independent_review": False,
            }],
            "replace_tasks": [],
            "cancel_task_ids": [],
        },
    )

    amended, code = amend_org_run_file(str(amendment_path), board="default")

    assert code == 0
    assert amended["status"] == "amended"
    assert amended["run_id"] == "local-run-001"
    assert amended["plan_version"] == 2
    assert set(amended["topology"]["tasks"]) == {"runtime", "regression"}


def test_list_marks_legacy_runs_until_adoption_without_recreating_cards(tmp_path, monkeypatch):
    """Breaks if legacy cards disappear from discovery or adoption rematerializes them."""
    from hermes_cli.hades_org_cmd import adopt_legacy_run, list_org_runs

    _configure_local_board(tmp_path, monkeypatch)
    with kb.connect(board="default") as conn:
        legacy = create_org_run(
            conn,
            parse_execution_portfolio(_legacy_payload(run_id="legacy-run-001")),
            validate_execution_portfolio(
                parse_execution_portfolio(_legacy_payload(run_id="legacy-run-001"))
            ),
            board="default",
        )
        task_count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]

    listed, code = list_org_runs(board="default")

    assert code == 0
    assert listed == {
        "status": "ok",
        "runs": [{
            "run_id": "legacy-run-001",
            "state": "legacy_unadopted",
            "origin": "legacy",
            "plan_version": None,
            "plan_hash": None,
        }],
    }

    adopted, code = adopt_legacy_run("legacy-run-001", board="default")

    assert code == 0
    assert adopted["status"] == "adopted"
    assert adopted["run_id"] == "legacy-run-001"
    assert adopted["topology"]["anchor_id"] == legacy.anchor_id
    with kb.connect(board="default") as conn:
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == task_count

    listed, code = list_org_runs(board="default")

    assert code == 0
    assert [run["run_id"] for run in listed["runs"]] == ["legacy-run-001"]
    assert listed["runs"][0]["state"] != "legacy_unadopted"
    assert listed["runs"][0]["plan_version"] == 1


def test_list_includes_an_interrupted_legacy_anchor_without_full_topology(tmp_path, monkeypatch):
    """Breaks if interrupted legacy runs vanish before an operator can adopt them."""
    from hermes_cli.hades_org_cmd import list_org_runs

    _configure_local_board(tmp_path, monkeypatch)
    with kb.connect(board="default") as conn:
        kb.create_task(
            conn,
            title="Interrupted legacy OrgRun",
            idempotency_key="org-run:interrupted-001:anchor",
            board="default",
        )

    listed, code = list_org_runs(board="default")

    assert code == 0
    assert listed == {
        "status": "ok",
        "runs": [{
            "run_id": "interrupted-001",
            "state": "legacy_unadopted",
            "origin": "legacy",
            "plan_version": None,
            "plan_hash": None,
        }],
    }


def test_org_sync_is_the_same_typed_non_retryable_local_boundary(tmp_path, monkeypatch, capsys):
    """Breaks if the compatibility parser invokes backend synchronization."""
    from hermes_cli.hades_org_cmd import build_parser, org_command, sync_kanban

    _configure_local_board(tmp_path, monkeypatch)
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    build_parser(sub, cmd_org=org_command)
    args = parser.parse_args(["org", "sync", "--mode", "pull_only", "--json"])

    assert org_command(args) == 2
    assert json.loads(capsys.readouterr().out) == {
        "state": "unsupported",
        "code": "agentic_kanban_has_no_remote_sync",
        "retryable": False,
    }
    assert sync_kanban(board="default", mode="off") == (
        {
            "state": "unsupported",
            "code": "agentic_kanban_has_no_remote_sync",
            "retryable": False,
        },
        2,
    )


def test_plan_commands_report_a_typed_missing_board_workspace(tmp_path, monkeypatch):
    """Breaks if validation masks a board without a local Git worktree."""
    from hermes_cli.hades_org_cmd import validate_plan_file

    _configure_local_board(tmp_path, monkeypatch)
    kb.write_board_metadata("default", default_workdir="")
    plan_path = _write_json(tmp_path, "plan.json", _plan_payload())

    result, code = validate_plan_file(str(plan_path), board="default")

    assert code == 2
    assert result["status"] == "error"
    assert result["code"] == "board_workspace_missing"


def test_org_cli_cold_import_has_no_backend_sync_or_lease_dependencies():
    """A fresh interpreter prevents other tests from masking a prohibited import."""
    blocked = {
        "hermes_cli.hades_kanban_sync",
        "hermes_cli.kanban_backend",
        "hermes_cli.hades_backend_client",
        "hermes_cli.hades_backend_sync",
    }
    probe = """
import importlib
import json
import sys

importlib.import_module('hermes_cli.hades_org_cmd')
blocked = %r
print(json.dumps(sorted(set(sys.modules).intersection(blocked))))
""" % sorted(blocked)
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=REPOSITORY,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == []
