from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from hermes_cli import kanban_db as kb
from hermes_cli.hades_org_cmd import materialize_plan_file, show_org_run
from hermes_cli.kanban_reports import list_reports
from hermes_cli.org_run_store import get_org_run, list_org_nodes


BOARD = "offline-e2e"
RUN_ID = "offline-org-run-001"


def _git_repository(path: Path) -> str:
    git = shutil.which("git")
    assert git is not None
    path.mkdir()
    (path / "runtime.py").write_text("OFFLINE = True\n", encoding="utf-8")
    (path / "review.py").write_text("VERIFIED = True\n", encoding="utf-8")
    for argv in (
        [git, "init", "-q"],
        [git, "config", "user.email", "offline@example.invalid"],
        [git, "config", "user.name", "Offline E2E"],
        [git, "add", "runtime.py", "review.py"],
        [git, "commit", "-q", "-m", "offline fixture"],
    ):
        subprocess.run(argv, cwd=path, check=True)
    return subprocess.run(
        [git, "rev-parse", "HEAD"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _configure_profiles(home: Path) -> None:
    home.mkdir()
    (home / "config.yaml").write_text(
        """delegation:
  profiles:
    offline:
      provider: offline
      model: deterministic
      max_iterations: 1
      child_timeout_seconds: 30
  role_routes:
    leaf: offline
    reviewer: offline
    orchestrator: offline
""",
        encoding="utf-8",
    )
    for role in ("leaf", "reviewer", "orchestrator"):
        profile = home / "profiles" / role
        profile.mkdir(parents=True)
        (profile / "config.yaml").write_text("toolsets: []\n", encoding="utf-8")


def _plan(base_commit: str) -> dict:
    return {
        "schema": "hades.implementation-plan.v1",
        "run_id": RUN_ID,
        "objective": "Prove the local Agentic-Kanban OrgRun lifecycle",
        "base_commit": base_commit,
        "acceptance_criteria": [
            "Every active execution and review gate has structured evidence",
            "The resumed run produces one verified final report",
        ],
        "independent_review": True,
        "tasks": [
            {
                "id": "runtime",
                "title": "Exercise the offline runtime",
                "role": "leaf",
                "risk": "low",
                "write_scope": ["runtime.py"],
                "depends_on": [],
                "acceptance_criteria": ["Runtime stays local"],
                "verification": ["python -m pytest -q tests/runtime"],
                "independent_review": False,
            },
            {
                "id": "reviewed-change",
                "title": "Exercise independent review",
                "role": "leaf",
                "risk": "high",
                "write_scope": ["review.py"],
                "depends_on": ["runtime"],
                "acceptance_criteria": ["Independent review evidence is recorded"],
                "verification": ["python -m pytest -q tests/review"],
                "independent_review": True,
            },
        ],
    }


def _structured_completion(conn, task_id: str, *, phase: str) -> None:
    assert kb.complete_task(
        conn,
        task_id,
        summary=f"{phase} completed with local evidence.",
        metadata={
            "changed_files": [f"evidence/{phase}.json"],
            "tests_run": [
                {
                    "command": f"verify {phase}",
                    "status": "passed",
                }
            ],
            "review": {"verdict": "pass", "findings": []},
            "regressions": [],
            "residual_risks": ["none; token=must-not-survive"],
        },
    )


def _dispatch_one(conn) -> str:
    result = kb.dispatch_once(conn, board=BOARD, max_spawn=1)
    assert len(result.spawned) == 1
    return result.spawned[0][0]


def test_offline_org_run_resumes_after_interrupt_and_projects_one_final_report(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Breaks if the local OrgRun path gains remote/runtime coupling or loses resume evidence."""
    repository = tmp_path / "repository"
    base_commit = _git_repository(repository)
    home = tmp_path / ".hermes"
    _configure_profiles(home)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_BOARD", BOARD)
    for key in tuple(os.environ):
        if key.startswith(("HADES_BACKEND_", "HERMES_BACKEND_")):
            monkeypatch.delenv(key)

    kb.create_board(
        BOARD,
        name="Offline E2E",
        default_workdir=str(repository),
    )
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(_plan(base_commit)), encoding="utf-8")

    materialized, code = materialize_plan_file(str(plan_path), board=BOARD)
    assert code == 0
    assert materialized["status"] == "materialized"
    assert materialized["run_id"] == RUN_ID

    def forbidden_backend(*_args, **_kwargs):
        raise AssertionError("Agentic-Kanban attempted backend access")

    monkeypatch.setattr(
        "hermes_cli.hades_backend_client.HadesBackendClient",
        forbidden_backend,
    )

    popen_calls: list[tuple[list[str], dict]] = []

    class FakeProcess:
        pid = os.getpid()

    def fake_popen(argv, *args, **kwargs):
        popen_calls.append(([str(item) for item in argv], kwargs))
        return FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setenv("PATH", "")

    with kb.connect(board=BOARD) as conn:
        topology = materialized["topology"]

        runtime_id = _dispatch_one(conn)
        assert runtime_id == topology["tasks"]["runtime"]["execution_id"]
        _structured_completion(conn, runtime_id, phase="runtime")

        reviewed_id = _dispatch_one(conn)
        assert reviewed_id == topology["tasks"]["reviewed-change"]["execution_id"]
        assert kb.block_task(
            conn,
            reviewed_id,
            reason="legacy remote review authority unavailable",
            kind="capability",
        )
        assert kb.unblock_task(conn, reviewed_id)
        assert _dispatch_one(conn) == reviewed_id
        _structured_completion(conn, reviewed_id, phase="reviewed-change")

        task_review_id = _dispatch_one(conn)
        assert task_review_id == topology["tasks"]["reviewed-change"]["review_id"]
        _structured_completion(conn, task_review_id, phase="task-review")

        assert kb.get_task(conn, topology["integration_id"]).status == "ready"
        assert get_org_run(conn, RUN_ID).state == "running"

    with kb.connect(board=BOARD) as resumed:
        topology = materialized["topology"]
        assert kb.get_task(resumed, topology["integration_id"]).status == "ready"

        integration_id = _dispatch_one(resumed)
        assert integration_id == topology["integration_id"]
        _structured_completion(resumed, integration_id, phase="integration")

        review_id = _dispatch_one(resumed)
        assert review_id == topology["review_id"]
        _structured_completion(resumed, review_id, phase="global-review")

        finalization_id = _dispatch_one(resumed)
        assert finalization_id == topology["finalization_id"]
        _structured_completion(resumed, finalization_id, phase="finalization")

        active_completed_ids = {
            node.task_id
            for node in list_org_nodes(resumed, RUN_ID)
            if node.state == "active"
            and kb.get_task(resumed, node.task_id).status == "done"
        }
        task_reports = list_reports(resumed, report_type="task")
        final_reports = list_reports(
            resumed,
            report_type="org_run_final",
            run_id=RUN_ID,
        )
        duplicate_keys = resumed.execute(
            "SELECT idempotency_key FROM tasks WHERE idempotency_key IS NOT NULL "
            "GROUP BY idempotency_key HAVING COUNT(*) != 1"
        ).fetchall()
        remote_rows = {
            table: resumed.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "kanban_remote_links",
                "kanban_sync_outbox",
                "kanban_sync_state",
            )
        }
        reviewed_events = kb.list_events(resumed, reviewed_id)

        assert {report.subject_id for report in task_reports} == active_completed_ids
        assert len(task_reports) == len(active_completed_ids)
        assert len(final_reports) == 1
        assert json.loads(final_reports[0].report_json)["run_id"] == RUN_ID
        assert "must-not-survive" not in final_reports[0].report_json
        assert duplicate_keys == []
        assert remote_rows == {
            "kanban_remote_links": 0,
            "kanban_sync_outbox": 0,
            "kanban_sync_state": 0,
        }
        assert [event.kind for event in reviewed_events].count("blocked") == 1
        assert [event.kind for event in reviewed_events].count("unblocked") == 1

    shown, code = show_org_run(RUN_ID, board=BOARD)
    assert code == 0
    assert shown["state"] == "completed"
    assert len(shown["report_ids"]) == 1

    assert popen_calls
    forbidden_argv = (
        "hermes-agent",
        "hermes-review-engine",
        "--api-key",
        "backend-token",
        "project-token",
        "lease-token",
        "workspace-binding",
    )
    for argv, _kwargs in popen_calls:
        assert argv[:3] == [sys.executable, "-m", "hermes_cli.main"]
        lowered = [item.lower() for item in argv]
        assert not any(
            forbidden in item
            for item in lowered
            for forbidden in forbidden_argv
        )
