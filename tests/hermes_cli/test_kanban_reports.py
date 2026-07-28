from __future__ import annotations

from dataclasses import replace
import hashlib
import json

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli.agentic_org_run import adopt_legacy_org_run, materialize_org_run
from hermes_cli.hierarchical_execution import (
    parse_execution_portfolio,
    validate_execution_portfolio,
)
from hermes_cli.implementation_plan import (
    IMPLEMENTATION_PLAN_SCHEMA,
    ImplementationPlan,
    ImplementationTask,
    PlanValidation,
    canonical_plan_json,
)
from hermes_cli.kanban_portfolio import create_org_run
from hermes_cli.kanban_reports import (
    get_report,
    list_reports,
    project_after_task_completion,
    project_org_run_cancellation,
    project_org_run_completion,
    project_task_completion,
)
from hermes_cli.org_run_store import (
    get_org_run,
    list_org_nodes,
    refresh_org_run_state,
    set_org_run_state,
)


def _plan(*, run_id: str = "reports-run-001") -> ImplementationPlan:
    return ImplementationPlan(
        schema=IMPLEMENTATION_PLAN_SCHEMA,
        run_id=run_id,
        objective="Project local terminal evidence",
        base_commit="a" * 40,
        acceptance_criteria=("Focused reports are deterministic",),
        tasks=(
            ImplementationTask(
                id="implementation",
                title="Implement report projection",
                role="leaf",
                risk="low",
                write_scope=("hermes_cli/kanban_reports.py",),
                depends_on=(),
                acceptance_criteria=("Persist a local report",),
                verification=("pytest tests/hermes_cli/test_kanban_reports.py",),
                independent_review=False,
            ),
        ),
        independent_review=False,
    )


def _validation(plan: ImplementationPlan) -> PlanValidation:
    return PlanValidation(
        plan_hash=hashlib.sha256(
            canonical_plan_json(plan).encode("utf-8")
        ).hexdigest(),
        ordered_dependencies={task.id: task.depends_on for task in plan.tasks},
        conflicts=(),
        resolved_profiles={
            "orchestrator": "orchestrator",
            "leaf": "leaf",
            "reviewer": "reviewer",
        },
        routed_roles=("orchestrator", "leaf", "reviewer"),
    )


def _complete(conn, task_id: str, *, summary: str, metadata: dict | None = None) -> None:
    assert kb.claim_task(conn, task_id, claimer="reports-test") is not None
    assert kb.complete_task(conn, task_id, summary=summary, metadata=metadata)


def test_terminal_task_projection_is_canonical_redacted_and_idempotent(tmp_path):
    """Breaks if the persisted task evidence loses provenance or replays twice."""
    metadata = {
        "changed_files": ["hermes_cli/kanban.py"],
        "tests_run": [{
            "command": "pytest tests/hermes_cli/test_kanban_cli.py",
            "status": "passed",
        }],
        "review": {"verdict": "pass", "findings": []},
        "regressions": [],
        "residual_risks": ["Legacy rows remain audit-only; token=supersecret123"],
    }
    with kb.connect(tmp_path / "kanban.db") as conn:
        task_id = kb.create_task(
            conn, title="Persist terminal evidence", board="default"
        )
        assert kb.claim_task(conn, task_id, claimer="failed-attempt") is not None
        assert kb.block_task(conn, task_id, reason="needs another attempt")
        assert kb.unblock_task(conn, task_id)
        _complete(conn, task_id, summary="Projected the terminal evidence.", metadata=metadata)

        first = project_task_completion(conn, task_id, board="default")
        replay = project_task_completion(conn, task_id, board="default")

        assert first is not None
        assert replay == first
        assert first == get_report(conn, first.id)
        assert list_reports(conn, report_type="task", subject_id=task_id) == [first]
        payload = json.loads(first.report_json)
        assert first.report_json == json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        assert payload["schema"] == "hades.kanban-task-report.v1"
        assert payload["board_slug"] == "default"
        assert payload["task_id"] == task_id
        assert payload["terminal_run_id"] == first.terminal_run_id
        assert payload["changed_files"] == ["hermes_cli/kanban.py"]
        assert payload["tests"] == metadata["tests_run"]
        assert payload["review"] == metadata["review"]
        assert payload["prior_attempts"][0]["outcome"] == "blocked"
        assert "supersecret123" not in first.report_json
        assert "***" in first.report_json
        assert first.report_markdown.startswith(f"# Development report: {task_id}\n")
        assert "## Objective\n" in first.report_markdown
        assert "## Changes\n" in first.report_markdown
        assert "## Verification\n" in first.report_markdown
        assert "## Review\n" in first.report_markdown
        assert "## Regressions and residual risk\n" in first.report_markdown
        assert "## Provenance\n" in first.report_markdown


def test_task_projection_bounds_nested_structured_evidence(tmp_path):
    """Breaks if nested maps can bypass the established report item cap."""
    deep: dict[str, object] = {}
    cursor = deep
    for _ in range(20):
        child: dict[str, object] = {}
        cursor["child"] = child
        cursor = child
    cursor["never_persisted"] = "secret=supersecret123"
    metadata = {
        "review": {
            **{f"finding-{index:02d}": {"nested": deep} for index in range(20)},
        },
    }
    with kb.connect(tmp_path / "kanban.db") as conn:
        task_id = kb.create_task(conn, title="Bound structured evidence")
        _complete(conn, task_id, summary="bounded", metadata=metadata)

        report = project_task_completion(conn, task_id, board="default")

        assert report is not None
        review = json.loads(report.report_json)["review"]
        assert len(review) == 10

        def assert_bounded(value, depth=0):
            if isinstance(value, dict):
                assert len(value) <= 10
                assert depth < 10
                for nested in value.values():
                    assert_bounded(nested, depth + 1)

        assert_bounded(review)
        assert "supersecret123" not in report.report_json


def test_projection_skips_nonterminal_tasks_and_org_run_until_all_gates_finish(tmp_path):
    """Breaks if a blocked card or incomplete OrgRun yields a final report."""
    plan = _plan()
    with kb.connect(tmp_path / "kanban.db") as conn:
        blocked_id = kb.create_task(
            conn, title="Blocked evidence", board="default", initial_status="blocked"
        )
        assert project_task_completion(conn, blocked_id, board="default") is None

        topology = materialize_org_run(
            conn, plan, _validation(plan), board="default"
        )
        assert project_org_run_completion(conn, plan.run_id, board="default") is None
        _complete(conn, topology.tasks["implementation"].execution_id, summary="implementation done")
        _complete(conn, topology.integration_id, summary="integration done")
        _complete(conn, topology.finalization_id, summary="final evidence done")

        report = project_org_run_completion(conn, plan.run_id, board="default")

        assert report is not None
        assert report.report_type == "org_run_final"
        assert report.source_version == 1
        assert get_org_run(conn, plan.run_id).base_commit == "a" * 40
        payload = json.loads(report.report_json)
        assert payload["schema"] == "hades.org-run-report.v1"
        assert payload["run_id"] == plan.run_id
        assert payload["board_slug"] == "default"
        assert payload["plan_version"] == 1
        assert payload["base_commit"] == "a" * 40
        assert topology.tasks["implementation"].execution_id in {
            task_report["task_id"] for task_report in payload["task_reports"]
        }
        assert report.report_markdown.startswith(f"# Development report: {plan.run_id}\n")


def test_final_projection_backfills_the_completed_anchor_task_report(tmp_path):
    """Breaks if materialization's terminal anchor run never gains a task report."""
    plan = _plan(run_id="reports-run-anchor")
    with kb.connect(tmp_path / "kanban.db") as conn:
        topology = materialize_org_run(
            conn, plan, _validation(plan), board="default"
        )
        _complete(
            conn,
            topology.tasks["implementation"].execution_id,
            summary="implementation done",
        )
        _complete(conn, topology.integration_id, summary="integration done")
        _complete(conn, topology.finalization_id, summary="final evidence done")

        active_task_ids = {
            node.task_id
            for node in list_org_nodes(conn, plan.run_id)
            if node.state == "active"
        }
        task_reports = list_reports(conn, report_type="task")

        assert {report.subject_id for report in task_reports} == active_task_ids
        assert len(task_reports) == len(active_task_ids)


def test_org_run_projection_rejects_a_forged_version_pointer(tmp_path):
    """Breaks if report versioning trusts a mutable run pointer without provenance."""
    plan = _plan(run_id="reports-run-versioned")
    with kb.connect(tmp_path / "kanban.db") as conn:
        topology = materialize_org_run(
            conn, plan, _validation(plan), board="default"
        )
        execution_id = topology.tasks["implementation"].execution_id
        _complete(conn, execution_id, summary="implementation done")
        task_records = project_after_task_completion(conn, execution_id, board="default")
        assert len(task_records) == 1
        _complete(conn, topology.integration_id, summary="integration done")
        _complete(conn, topology.finalization_id, summary="final evidence done")
        first = project_org_run_completion(conn, plan.run_id, board="default")
        assert first is not None

        conn.execute(
            "UPDATE kanban_org_runs SET plan_version = 2 WHERE run_id = ?",
            (plan.run_id,),
        )
        conn.commit()
        with pytest.raises(ValueError, match="incomplete stored topology"):
            project_org_run_completion(conn, plan.run_id, board="default")

        reports = list_reports(conn, report_type="org_run_final", run_id=plan.run_id)
        assert [report.source_version for report in reports] == [1]


def test_run_filter_selects_reports_through_org_node_ownership(tmp_path):
    """Breaks if run_id is incorrectly compared only with report.subject_id."""
    plan = _plan(run_id="reports-run-filter")
    with kb.connect(tmp_path / "kanban.db") as conn:
        topology = materialize_org_run(
            conn, plan, _validation(plan), board="default"
        )
        execution_id = topology.tasks["implementation"].execution_id
        _complete(conn, execution_id, summary="owned task complete")

        reports = list_reports(conn, run_id=plan.run_id)

        assert [report.subject_id for report in reports] == [execution_id]
        assert reports[0].report_type == "task"


def test_run_filter_excludes_unrelated_task_report_on_subject_collision(tmp_path):
    """Breaks if run_id matches arbitrary task report subjects directly."""
    with kb.connect(tmp_path / "kanban.db") as conn:
        unrelated_id = kb.create_task(
            conn,
            title="Unrelated task whose id will collide with a run",
            board="default",
        )
        _complete(conn, unrelated_id, summary="unrelated complete")
        unrelated_report = project_task_completion(
            conn, unrelated_id, board="default"
        )
        assert unrelated_report is not None
        plan = _plan(run_id=unrelated_id)
        materialize_org_run(
            conn, plan, _validation(plan), board="default"
        )

        assert list_reports(conn, run_id=plan.run_id) == []


def test_actual_cancellation_projects_one_canonical_redacted_report(tmp_path):
    """Breaks if cancellation is unreported, duplicated, or leaks plan text."""
    plan = replace(
        _plan(run_id="reports-run-cancelled"),
        objective="Cancel safely; token=supersecret123",
    )
    with kb.connect(tmp_path / "kanban.db") as conn:
        materialize_org_run(conn, plan, _validation(plan), board="default")

        set_org_run_state(conn, plan.run_id, "cancelled", now=123)
        set_org_run_state(conn, plan.run_id, "cancelled", now=124)

        reports = list_reports(
            conn,
            report_type="org_run_cancelled",
            run_id=plan.run_id,
        )
        assert len(reports) == 1
        report = reports[0]
        payload = json.loads(report.report_json)
        assert report == project_org_run_cancellation(
            conn, plan.run_id, board="default"
        )
        assert report.report_json == json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        assert payload["schema"] == "hades.org-run-cancellation-report.v1"
        assert payload["state"] == "cancelled"
        assert payload["run_id"] == plan.run_id
        assert "supersecret123" not in report.report_json
        assert "***" in report.report_json


def test_blocked_org_run_never_projects_a_cancellation_report(tmp_path):
    """Breaks if a recoverable blocked run is rendered as terminal cancellation."""
    plan = _plan(run_id="reports-run-blocked")
    with kb.connect(tmp_path / "kanban.db") as conn:
        topology = materialize_org_run(
            conn, plan, _validation(plan), board="default"
        )
        execution_id = topology.tasks["implementation"].execution_id
        assert kb.block_task(
            conn, execution_id, reason="needs operator", kind="needs_input"
        )
        assert refresh_org_run_state(conn, plan.run_id) == "blocked"

        assert project_org_run_cancellation(
            conn, plan.run_id, board="default"
        ) is None
        assert list_reports(
            conn,
            report_type="org_run_cancelled",
            run_id=plan.run_id,
        ) == []


def test_final_projection_rejects_live_contract_or_dag_drift(tmp_path):
    """Breaks if done statuses alone can produce a trusted final report."""
    plan = _plan(run_id="reports-run-drift")
    with kb.connect(tmp_path / "kanban.db") as conn:
        topology = materialize_org_run(
            conn, plan, _validation(plan), board="default"
        )
        for task_id in (
            topology.tasks["implementation"].execution_id,
            topology.integration_id,
            topology.finalization_id,
        ):
            _complete(conn, task_id, summary="done")
        conn.execute(
            "UPDATE tasks SET assignee='reviewer' WHERE id=?",
            (topology.finalization_id,),
        )
        conn.execute(
            "DELETE FROM kanban_reports WHERE report_type='org_run_final' "
            "AND subject_id=?",
            (plan.run_id,),
        )
        conn.commit()

        with pytest.raises(ValueError, match="managed plan drift"):
            project_org_run_completion(conn, plan.run_id, board="default")

        assert list_reports(
            conn,
            report_type="org_run_final",
            run_id=plan.run_id,
        ) == []


def test_final_projection_rejects_adopted_live_contract_drift(tmp_path):
    """Breaks if adopted final projection trusts terminal statuses alone."""
    payload = {
        "schema": "hades.execution-portfolio.v1",
        "org_run_id": "legacy-report-run",
        "project_id": "proj-legacy-report-run",
        "repository_id": "repo-1",
        "workspace_binding_id": "binding-unused",
        "base_commit": "b" * 40,
        "tasks": [
            {
                "remote_task_id": "runtime",
                "work_item_id": "work-legacy-report-run",
                "title": "Legacy runtime",
                "body": "Implement the bounded change.",
                "assignee": "default",
                "priority": 10,
                "risk": "high",
                "depends_on": [],
                "write_scope": ["hermes_cli/legacy.py"],
            }
        ],
    }
    with kb.connect(tmp_path / "kanban.db") as conn:
        legacy_plan = parse_execution_portfolio(payload)
        create_org_run(
            conn, legacy_plan, validate_execution_portfolio(legacy_plan)
        )
        topology = adopt_legacy_org_run(
            conn, "legacy-report-run", board="default"
        )
        conn.execute(
            "UPDATE tasks SET title='tampered' WHERE id=?",
            (topology.finalization_id,),
        )
        conn.commit()

        with pytest.raises(ValueError, match="managed plan drift"):
            project_org_run_completion(
                conn, "legacy-report-run", board="default"
            )
