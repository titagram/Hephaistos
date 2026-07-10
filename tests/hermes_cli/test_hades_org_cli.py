import json

from hermes_cli.hades_org_cmd import (
    materialize_portfolio_file,
    show_org_run,
    sync_kanban,
    validate_portfolio_file,
)


def payload():
    return {
        "schema": "hades.execution-portfolio.v1",
        "org_run_id": "org_cli_001",
        "project_id": "proj_1",
        "repository_id": "repo_1",
        "workspace_binding_id": "wb_1",
        "base_commit": "a" * 40,
        "tasks": [{
            "remote_task_id": "HD-CLI-1",
            "work_item_id": "awi_cli_1",
            "title": "CLI task",
            "body": "Create a local OrgRun.",
            "assignee": "default",
            "priority": 1,
            "risk": "low",
            "depends_on": [],
            "write_scope": ["hermes_cli/example.py"],
        }],
    }


def test_validate_materialize_and_show_round_trip(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    portfolio = tmp_path / "portfolio.json"
    portfolio.write_text(json.dumps(payload()), encoding="utf-8")

    valid, code = validate_portfolio_file(str(portfolio))
    assert code == 0
    assert valid["status"] == "valid"
    assert valid["task_count"] == 1

    materialized, code = materialize_portfolio_file(str(portfolio), board=None)
    assert code == 0
    assert materialized["status"] == "materialized"
    assert materialized["org_run_id"] == "org_cli_001"

    shown, code = show_org_run("org_cli_001", board=None)
    assert code == 0
    assert shown["status"] == "ok"
    assert shown["topology"] == materialized["topology"]


def test_validate_returns_exit_two_for_invalid_json(tmp_path):
    portfolio = tmp_path / "invalid.json"
    portfolio.write_text("{", encoding="utf-8")
    result, code = validate_portfolio_file(str(portfolio))
    assert code == 2
    assert result["status"] == "error"
    assert result["code"] == "invalid_portfolio"


def test_sync_defaults_to_safe_off_mode():
    result, code = sync_kanban(board=None, mode="off")
    assert code == 0
    assert result == {"status": "ok", "mode": "off", "pulled": 0}
