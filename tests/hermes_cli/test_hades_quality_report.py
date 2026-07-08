from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "hades" / "no_codebase_bug_cases.json"
SUITE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "hades" / "no_codebase_quality_suite.json"


def _passing_no_codebase_report() -> dict[str, object]:
    return {
        "status": "passed",
        "total": 0,
        "passed": 0,
        "failed": 0,
        "accuracy": 1.0,
        "root_cause_accuracy": 1.0,
        "insufficient_accuracy": 1.0,
        "evidence_ref_coverage": 1.0,
        "freshness_coverage": 1.0,
        "awareness_coverage": 1.0,
        "tool_coverage": 1.0,
        "tool_order_coverage": 1.0,
        "persistence_coverage": 1.0,
        "taxonomy_coverage": 1.0,
        "causal_pack_coverage": 1.0,
        "causal_chain_coverage": 1.0,
        "counterfactual_refusal_coverage": 1.0,
        "no_codebase_violations": [],
    }


def test_quality_report_passes_clean_no_codebase_eval_and_ready_awareness():
    from hermes_cli.hades_no_codebase_eval import evaluate_no_codebase_diagnoses, load_no_codebase_eval_fixture
    from hermes_cli.hades_quality_report import build_hades_quality_report

    fixtures, runs = load_no_codebase_eval_fixture(FIXTURE_PATH)
    no_codebase = evaluate_no_codebase_diagnoses(fixtures, runs).to_dict()

    report = build_hades_quality_report(
        no_codebase_report=no_codebase,
        support_report={
            "configured": True,
            "degraded": False,
            "awareness": {"status": "ready"},
        },
        generated_at=12345,
    )

    assert report["schema"] == "hades.quality_report.v1"
    assert report["generated_at"] == 12345
    assert report["status"] == "passed"
    assert report["summary"] == {"blockers": 0, "warnings": 0, "actions": 0}
    assert report["metrics"]["no_codebase"]["accuracy"] == 1.0
    assert report["metrics"]["no_codebase"]["freshness_coverage"] == 1.0
    assert report["metrics"]["no_codebase"]["awareness_coverage"] == 1.0
    assert report["metrics"]["no_codebase"]["tool_order_coverage"] == 1.0
    assert report["metrics"]["no_codebase"]["causal_pack_coverage"] == 1.0
    assert report["metrics"]["no_codebase"]["causal_chain_coverage"] == 1.0
    assert report["metrics"]["no_codebase"]["counterfactual_refusal_coverage"] == 1.0
    assert report["metrics"]["support"]["awareness_status"] == "ready"
    assert report["action_queue"] == []


def test_quality_suite_aggregates_no_codebase_fixtures():
    from hermes_cli.hades_quality_suite import load_quality_suite, run_quality_suite

    suite = load_quality_suite(SUITE_PATH)
    report = run_quality_suite(suite)

    assert report["schema"] == "hades.no_codebase_quality_suite_report.v1"
    assert report["status"] == "passed"
    assert report["total"] >= 1
    assert report["suites"][0]["id"] == "default_no_codebase"
    assert report["suites"][0]["status"] == "passed"
    assert report["suites"][0]["thresholds"]["min_causal_pack_coverage"] == 1.0
    assert report["suites"][0]["thresholds"]["min_causal_chain_coverage"] == 1.0
    assert report["suites"][0]["thresholds"]["min_counterfactual_refusal_coverage"] == 1.0


def test_quality_report_blocks_failed_quality_suite():
    from hermes_cli.hades_quality_report import build_hades_quality_report

    report = build_hades_quality_report(
        no_codebase_report={"status": "passed", "total": 1, "passed": 1, "failed": 0, "taxonomy_coverage": 1.0},
        suite_report={
            "schema": "hades.no_codebase_quality_suite_report.v1",
            "status": "failed",
            "failed": 1,
            "suites": [{"id": "rocket_club", "status": "failed"}],
        },
    )

    assert report["status"] == "failed"
    assert any(action["id"] == "fix_no_codebase_quality_suite" for action in report["action_queue"])


def test_quality_report_blocks_missing_causal_awareness_metrics():
    from hermes_cli.hades_quality_report import build_hades_quality_report

    report = build_hades_quality_report(
        no_codebase_report={
            "status": "failed",
            "total": 1,
            "passed": 0,
            "failed": 1,
            "accuracy": 0.0,
            "evidence_ref_coverage": 1.0,
            "freshness_coverage": 1.0,
            "awareness_coverage": 1.0,
            "tool_coverage": 1.0,
            "tool_order_coverage": 1.0,
            "persistence_coverage": 1.0,
            "taxonomy_coverage": 1.0,
            "causal_pack_coverage": 0.0,
            "causal_chain_coverage": 0.0,
            "counterfactual_refusal_coverage": 0.0,
        }
    )
    actions = {action["id"]: action for action in report["action_queue"]}

    assert report["status"] == "failed"
    assert actions["repair_causal_pack_coverage"]["severity"] == "blocker"
    assert actions["repair_causal_chain_coverage"]["severity"] == "blocker"
    assert actions["repair_counterfactual_refusal"]["severity"] == "blocker"


def test_note_backfill_quality_report_counts_review_candidates():
    from hermes_cli.hades_quality_report import build_hades_quality_report, build_note_backfill_quality_report

    note_backfill = build_note_backfill_quality_report(
        [
            SimpleNamespace(
                intent="note_backfill_candidate",
                status="submitted",
                provenance={
                    "source": "hades_note_quality",
                    "evidence_ref": {
                        "schema": "hades.backend_wiki.file_chunk.v1",
                        "sha256": "abc123",
                    },
                },
            ),
            SimpleNamespace(
                intent="create_memory",
                status="pending",
                provenance={"source": "manual"},
            ),
        ]
    )
    report = build_hades_quality_report(note_backfill_report=note_backfill, generated_at=12345)
    actions = {action["id"]: action for action in report["action_queue"]}

    assert note_backfill["total"] == 1
    assert note_backfill["by_status"] == {"submitted": 1}
    assert note_backfill["pending_review_count"] == 1
    assert note_backfill["evidence_ref_coverage"] == 1.0
    assert report["status"] == "attention"
    assert actions["review_note_backfill_candidates"]["count"] == 1
    assert "repair_note_backfill_evidence_refs" not in actions


def test_note_backfill_quality_report_flags_rejected_or_incomplete_candidates():
    from hermes_cli.hades_quality_report import build_hades_quality_report, build_note_backfill_quality_report

    note_backfill = build_note_backfill_quality_report(
        [
            {
                "intent": "note_backfill_candidate",
                "status": "conflicted",
                "provenance": {"source": "hades_note_quality"},
            }
        ]
    )
    report = build_hades_quality_report(note_backfill_report=note_backfill)
    actions = {action["id"]: action for action in report["action_queue"]}

    assert note_backfill["rejected_count"] == 1
    assert note_backfill["missing_evidence_ref_count"] == 1
    assert note_backfill["evidence_ref_coverage"] == 0.0
    assert actions["acknowledge_rejected_note_backfill"]["count"] == 1
    assert actions["repair_note_backfill_evidence_refs"]["count"] == 1


def test_agent_work_quality_report_flags_required_work_without_memory_context():
    from hermes_cli.hades_quality_report import build_agent_work_quality_report, build_hades_quality_report

    agent_work = build_agent_work_quality_report(
        [
            {
                "work_item_id": "awi_missing",
                "kind": "hades.kanban_task_work.v1",
                "status": "completed",
                "payload": {
                    "schema": "hades.kanban_task_work.v1",
                    "memory_required": True,
                },
                "result": {"final_response": "done"},
            },
            {
                "work_item_id": "awi_ready",
                "kind": "hades.kanban_task_work.v1",
                "status": "queued",
                "payload": {
                    "schema": "hades.kanban_task_work.v1",
                    "memory_required": True,
                    "memory_search_status": {"status": "empty", "refs": []},
                },
            },
        ]
    )
    report = build_hades_quality_report(agent_work_report=agent_work, no_codebase_report=_passing_no_codebase_report())
    actions = {action["id"]: action for action in report["action_queue"]}

    assert agent_work["shared_memory_required_count"] == 2
    assert agent_work["shared_memory_context_count"] == 1
    assert agent_work["missing_shared_memory_context_count"] == 1
    assert agent_work["completed_missing_shared_memory_context_count"] == 1
    assert agent_work["missing_work_item_ids"] == ["awi_missing"]
    assert report["status"] == "failed"
    assert actions["repair_agent_work_shared_memory"]["severity"] == "blocker"
    assert actions["repair_agent_work_shared_memory"]["count"] == 1


def test_agent_work_quality_report_passes_when_memory_refs_are_recorded():
    from hermes_cli.hades_quality_report import build_agent_work_quality_report, build_hades_quality_report

    agent_work = build_agent_work_quality_report(
        [
            {
                "work_item_id": "awi_1",
                "kind": "hades.kanban_task_work.v1",
                "status": "completed",
                "payload": {"schema": "hades.kanban_task_work.v1", "memory_required": True},
                "result": {
                    "memory_refs": [{"type": "project_memory", "id": "mem_1"}],
                    "final_response": "done",
                },
            }
        ]
    )
    report = build_hades_quality_report(agent_work_report=agent_work, no_codebase_report=_passing_no_codebase_report())

    assert agent_work["shared_memory_context_coverage"] == 1.0
    assert agent_work["missing_shared_memory_context_count"] == 0
    assert report["action_queue"] == []
    assert report["status"] == "passed"


def test_quality_report_blocks_forbidden_source_access_regressions():
    from hermes_cli.hades_no_codebase_eval import (
        NoCodebaseDiagnosisRun,
        evaluate_no_codebase_diagnoses,
        load_no_codebase_eval_fixture,
    )
    from hermes_cli.hades_quality_report import build_hades_quality_report

    fixtures, runs = load_no_codebase_eval_fixture(FIXTURE_PATH)
    first = runs[0]
    runs[0] = NoCodebaseDiagnosisRun(
        fixture_id=first.fixture_id,
        root_cause_id=first.root_cause_id,
        confidence=first.confidence,
        freshness_status=first.freshness_status,
        diagnosable_without_source=first.diagnosable_without_source,
        evidence_refs=first.evidence_refs,
        tool_calls=first.tool_calls + ("read_file",),
        missing_evidence=first.missing_evidence,
        causal_pack_refs=first.causal_pack_refs,
        causal_chain=first.causal_chain,
        counterfactual_refused=first.counterfactual_refused,
        persisted_report=first.persisted_report,
    )

    no_codebase = evaluate_no_codebase_diagnoses(fixtures, runs).to_dict()
    report = build_hades_quality_report(no_codebase_report=no_codebase)
    actions = {action["id"]: action for action in report["action_queue"]}

    assert report["status"] == "failed"
    assert report["summary"]["blockers"] == 2
    assert actions["fix_no_codebase_eval_failures"]["severity"] == "blocker"
    assert actions["remove_forbidden_source_access"]["count"] == 1


def test_quality_report_blocks_stale_precise_diagnosis_regressions():
    from hermes_cli.hades_no_codebase_eval import (
        NoCodebaseDiagnosisRun,
        evaluate_no_codebase_diagnoses,
        load_no_codebase_eval_fixture,
    )
    from hermes_cli.hades_quality_report import build_hades_quality_report

    fixtures, runs = load_no_codebase_eval_fixture(FIXTURE_PATH)
    first = runs[0]
    runs[0] = NoCodebaseDiagnosisRun(
        fixture_id=first.fixture_id,
        root_cause_id=first.root_cause_id,
        confidence=first.confidence,
        freshness_status="stale",
        diagnosable_without_source=first.diagnosable_without_source,
        evidence_refs=first.evidence_refs,
        tool_calls=first.tool_calls,
        missing_evidence=first.missing_evidence,
        causal_pack_refs=first.causal_pack_refs,
        causal_chain=first.causal_chain,
        counterfactual_refused=first.counterfactual_refused,
        persisted_report=first.persisted_report,
    )

    no_codebase = evaluate_no_codebase_diagnoses(fixtures, runs).to_dict()
    report = build_hades_quality_report(no_codebase_report=no_codebase)
    actions = {action["id"]: action for action in report["action_queue"]}

    assert report["status"] == "failed"
    assert report["metrics"]["no_codebase"]["freshness_coverage"] < 1.0
    assert actions["repair_freshness_coverage"]["severity"] == "blocker"


def test_quality_report_blocks_hades_tool_order_regressions():
    from hermes_cli.hades_no_codebase_eval import (
        NoCodebaseDiagnosisRun,
        evaluate_no_codebase_diagnoses,
        load_no_codebase_eval_fixture,
    )
    from hermes_cli.hades_quality_report import build_hades_quality_report

    fixtures, runs = load_no_codebase_eval_fixture(FIXTURE_PATH)
    first = runs[0]
    runs[0] = NoCodebaseDiagnosisRun(
        fixture_id=first.fixture_id,
        root_cause_id=first.root_cause_id,
        confidence=first.confidence,
        freshness_status=first.freshness_status,
        diagnosable_without_source=first.diagnosable_without_source,
        evidence_refs=first.evidence_refs,
        tool_calls=first.tool_calls[1:] + first.tool_calls[:1],
        missing_evidence=first.missing_evidence,
        causal_pack_refs=first.causal_pack_refs,
        causal_chain=first.causal_chain,
        counterfactual_refused=first.counterfactual_refused,
        persisted_report=first.persisted_report,
    )

    no_codebase = evaluate_no_codebase_diagnoses(fixtures, runs).to_dict()
    report = build_hades_quality_report(no_codebase_report=no_codebase)
    actions = {action["id"]: action for action in report["action_queue"]}

    assert report["status"] == "failed"
    assert report["metrics"]["no_codebase"]["tool_coverage"] == 1.0
    assert report["metrics"]["no_codebase"]["tool_order_coverage"] < 1.0
    assert actions["repair_hades_tool_order"]["severity"] == "blocker"


def test_quality_report_blocks_undiagnosable_awareness_regressions():
    from hermes_cli.hades_no_codebase_eval import (
        NoCodebaseDiagnosisRun,
        evaluate_no_codebase_diagnoses,
        load_no_codebase_eval_fixture,
    )
    from hermes_cli.hades_quality_report import build_hades_quality_report

    fixtures, runs = load_no_codebase_eval_fixture(FIXTURE_PATH)
    first = runs[0]
    runs[0] = NoCodebaseDiagnosisRun(
        fixture_id=first.fixture_id,
        root_cause_id=first.root_cause_id,
        confidence=first.confidence,
        freshness_status=first.freshness_status,
        diagnosable_without_source=False,
        evidence_refs=first.evidence_refs,
        tool_calls=first.tool_calls,
        missing_evidence=first.missing_evidence,
        causal_pack_refs=first.causal_pack_refs,
        causal_chain=first.causal_chain,
        counterfactual_refused=first.counterfactual_refused,
        persisted_report=first.persisted_report,
    )

    no_codebase = evaluate_no_codebase_diagnoses(fixtures, runs).to_dict()
    report = build_hades_quality_report(no_codebase_report=no_codebase)
    actions = {action["id"]: action for action in report["action_queue"]}

    assert report["status"] == "failed"
    assert report["metrics"]["no_codebase"]["awareness_coverage"] < 1.0
    assert actions["repair_awareness_coverage"]["severity"] == "blocker"


def test_backend_quality_report_command_emits_json_for_fixture(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    import hermes_cli.hades_backend_cmd as cmd

    rc = cmd.hades_backend_command(
        SimpleNamespace(
            backend_action="quality-report",
            no_codebase_eval=str(FIXTURE_PATH),
            skip_local_status=True,
            json=True,
        )
    )
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["schema"] == "hades.quality_report.v1"
    assert payload["status"] == "passed"
    assert payload["metrics"]["no_codebase"]["total"] == 7
    assert payload["action_queue"] == []


def test_backend_quality_report_command_accepts_quality_suite(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    import hermes_cli.hades_backend_cmd as cmd

    rc = cmd.hades_backend_command(
        SimpleNamespace(
            backend_action="quality-report",
            no_codebase_eval=None,
            suite=str(SUITE_PATH),
            skip_local_status=True,
            json=True,
        )
    )
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["status"] == "attention"
    assert payload["metrics"]["no_codebase_suite"]["status"] == "passed"
    assert payload["metrics"]["no_codebase_suite"]["total"] == 1
    assert payload["action_queue"] == [
        {
            "id": "run_no_codebase_eval",
            "message": "Run the no-codebase diagnosis evaluation fixture before release.",
            "severity": "warning",
        }
    ]


def test_backend_quality_report_includes_pending_note_backfill_proposals(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    import hermes_cli.hades_backend_cmd as cmd
    from hermes_cli import hades_backend_db as db

    with db.connect_closing() as conn:
        db.create_memory_proposal(
            conn,
            project_id="proj_1",
            workspace_binding_id="wb_1",
            action="create",
            intent="note_backfill_candidate",
            summary="Controller.php handles 3 taxonomy routes.",
            provenance={
                "source": "hades_note_quality",
                "evidence_ref": {
                    "schema": "hades.backend_wiki.file_chunk.v1",
                    "sha256": "abc123",
                },
            },
        )

    rc = cmd.hades_backend_command(
        SimpleNamespace(
            backend_action="quality-report",
            no_codebase_eval=str(FIXTURE_PATH),
            skip_local_status=True,
            json=True,
        )
    )
    payload = json.loads(capsys.readouterr().out)
    actions = {action["id"]: action for action in payload["action_queue"]}

    assert rc == 0
    assert payload["status"] == "attention"
    assert payload["metrics"]["note_backfill"]["total"] == 1
    assert payload["metrics"]["note_backfill"]["by_status"] == {"pending": 1}
    assert payload["metrics"]["note_backfill"]["pending_review_count"] == 1
    assert payload["metrics"]["note_backfill"]["evidence_ref_coverage"] == 1.0
    assert actions["review_note_backfill_candidates"]["severity"] == "warning"


def test_backend_quality_report_flags_cached_agent_work_without_memory_context(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    import hermes_cli.hades_backend_cmd as cmd
    from hermes_cli import hades_backend_db as db

    with db.connect_closing() as conn:
        db.upsert_plugin_work_item(
            conn,
            work_item_id="awi_missing_memory",
            project_id="proj_1",
            agent_key="local_agent",
            kind="hades.kanban_task_work.v1",
            status="completed",
            payload={
                "schema": "hades.kanban_task_work.v1",
                "memory_required": True,
                "title": "Diagnose checkout bug",
            },
            result={"final_response": "Done without memory refs."},
        )

    rc = cmd.hades_backend_command(
        SimpleNamespace(
            backend_action="quality-report",
            no_codebase_eval=str(FIXTURE_PATH),
            skip_local_status=True,
            json=True,
        )
    )
    payload = json.loads(capsys.readouterr().out)
    actions = {action["id"]: action for action in payload["action_queue"]}

    assert rc == 1
    assert payload["status"] == "failed"
    assert payload["metrics"]["agent_work"]["total"] == 1
    assert payload["metrics"]["agent_work"]["missing_shared_memory_context_count"] == 1
    assert payload["metrics"]["agent_work"]["missing_work_item_ids"] == ["awi_missing_memory"]
    assert actions["repair_agent_work_shared_memory"]["severity"] == "blocker"


def test_backend_quality_report_command_accepts_trajectory_runs(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    trajectory = tmp_path / "trajectory.jsonl"
    trajectory.write_text(
        json.dumps(
            {
                "conversations": [
                    {"from": "human", "value": "Diagnose without source."},
                    {
                        "from": "gpt",
                        "value": "\n".join(
                            [
                                "<tool_call>",
                                json.dumps({"name": "hades_backend_project_awareness_status", "arguments": {}}),
                                "</tool_call>",
                            ]
                        ),
                    },
                    {
                        "from": "gpt",
                        "value": json.dumps(
                            {
                                "confidence": "insufficient",
                                "missing_evidence": ["source_slice"],
                            }
                        ),
                    },
                ]
            }
        )
        + "\n",
        encoding="utf-8",
    )
    eval_file = tmp_path / "eval.json"
    eval_file.write_text(
        json.dumps(
            {
                "schema": "hades.no_codebase_eval.v1",
                "fixtures": [
                    {
                        "id": "trajectory_insufficient",
                        "expected_root_cause_id": None,
                        "expected_confidence": "insufficient",
                        "required_tool_calls": ["hades_backend_project_awareness_status"],
                        "expected_missing_evidence": ["source_slice"],
                        "requires_persisted_report": False,
                    }
                ],
                "trajectory_runs": [{"fixture_id": "trajectory_insufficient", "trajectory_path": "trajectory.jsonl"}],
            }
        ),
        encoding="utf-8",
    )

    import hermes_cli.hades_backend_cmd as cmd

    rc = cmd.hades_backend_command(
        SimpleNamespace(
            backend_action="quality-report",
            no_codebase_eval=str(eval_file),
            skip_local_status=True,
            json=True,
        )
    )
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["status"] == "passed"
    assert payload["metrics"]["no_codebase"]["total"] == 1
    assert payload["metrics"]["no_codebase"]["tool_coverage"] == 1.0


def test_backend_quality_report_command_records_latest_snapshot(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    import hermes_cli.hades_backend_cmd as cmd
    from hermes_cli import hades_backend_db as db

    rc = cmd.hades_backend_command(
        SimpleNamespace(
            backend_action="quality-report",
            no_codebase_eval=str(FIXTURE_PATH),
            skip_local_status=True,
            record=True,
            json=True,
        )
    )
    payload = json.loads(capsys.readouterr().out)

    with db.connect_closing() as conn:
        recorded = db.get_sync_state(conn, "last_quality_report")
        recorded_at = db.get_sync_state_updated_at(conn, "last_quality_report")
        history = db.get_sync_state(conn, "quality_report_history")

    assert rc == 0
    assert recorded == payload
    assert recorded_at is not None
    assert history is not None
    assert history["schema"] == "hades.quality_report_history.v1"
    assert history["entries"][0]["status"] == "passed"
    assert history["entries"][0]["summary"] == {"blockers": 0, "warnings": 0, "actions": 0}


def test_backend_quality_report_command_records_history_for_status_drilldown(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    data["runs"][0]["tool_calls"].append("read_file")
    failed_fixture = tmp_path / "failed-no-codebase.json"
    failed_fixture.write_text(json.dumps(data), encoding="utf-8")

    import hermes_cli.hades_backend_cmd as cmd
    from hermes_cli import hades_backend_db as db
    from hermes_cli.hades_backend_status import backend_status_payload

    clean_rc = cmd.hades_backend_command(
        SimpleNamespace(
            backend_action="quality-report",
            no_codebase_eval=str(FIXTURE_PATH),
            skip_local_status=True,
            record=True,
            json=True,
        )
    )
    capsys.readouterr()
    failed_rc = cmd.hades_backend_command(
        SimpleNamespace(
            backend_action="quality-report",
            no_codebase_eval=str(failed_fixture),
            skip_local_status=True,
            record=True,
            json=True,
        )
    )
    latest = json.loads(capsys.readouterr().out)

    with db.connect_closing() as conn:
        history = db.get_sync_state(conn, "quality_report_history")
        history_updated_at = db.get_sync_state_updated_at(conn, "quality_report_history")
        latest_updated_at = db.get_sync_state_updated_at(conn, "last_quality_report")

    status = backend_status_payload(
        agent=SimpleNamespace(agent_id="agent_1", project_id="proj_1", base_url="https://backend.example", label="dev", capabilities={}),
        bindings=[],
        job_counts={},
        proposal_counts={},
        inbox_counts={"total": 0, "unread": 0},
        last_summary=None,
        last_error=None,
        last_quality_report=latest,
        last_quality_report_updated_at=latest_updated_at,
        quality_report_history=history,
        quality_report_history_updated_at=history_updated_at,
        now=1_000,
    )

    assert clean_rc == 0
    assert failed_rc == 1
    assert history is not None
    assert [entry["status"] for entry in history["entries"]] == ["failed", "passed"]
    quality_history = status["quality"]["history"]
    assert quality_history["total"] == 2
    assert quality_history["by_status"] == {"failed": 1, "passed": 1}
    assert quality_history["latest_failure"]["status"] == "failed"
    assert "remove_forbidden_source_access" in quality_history["latest_failure"]["action_ids"]


def test_backend_status_flags_missing_quality_report_baseline():
    from hermes_cli.hades_backend_status import backend_status_payload

    payload = backend_status_payload(
        agent=SimpleNamespace(agent_id="agent_1", project_id="proj_1", base_url="https://backend.example", label="dev", capabilities={}),
        bindings=[],
        job_counts={},
        proposal_counts={},
        inbox_counts={"total": 0, "unread": 0},
        last_summary=None,
        last_error=None,
        last_quality_report=None,
        last_quality_report_updated_at=None,
        now=1_000,
    )

    assert payload["quality"]["staleness"]["missing"] is True
    assert payload["quality"]["staleness"]["stale"] is False
    assert any("quality-report --record" in action for action in payload["actions"])


def test_backend_status_flags_stale_quality_report_without_degrading_backend():
    from hermes_cli.hades_backend_status import QUALITY_REPORT_STALE_SECONDS, backend_status_payload

    payload = backend_status_payload(
        agent=SimpleNamespace(agent_id="agent_1", project_id="proj_1", base_url="https://backend.example", label="dev", capabilities={}),
        bindings=[],
        job_counts={},
        proposal_counts={},
        inbox_counts={"total": 0, "unread": 0},
        last_summary=None,
        last_error=None,
        last_quality_report={
            "schema": "hades.quality_report.v1",
            "generated_at": 10,
            "status": "passed",
            "summary": {"blockers": 0, "warnings": 0, "actions": 0},
            "metrics": {},
            "action_queue": [],
        },
        last_quality_report_updated_at=10,
        now=10 + QUALITY_REPORT_STALE_SECONDS + 1,
    )

    assert payload["quality"]["last_report"]["generated_at"] == 10
    assert payload["quality"]["staleness"]["missing"] is False
    assert payload["quality"]["staleness"]["stale"] is True
    assert payload["quality"]["staleness"]["age_seconds"] == QUALITY_REPORT_STALE_SECONDS + 1
    assert payload["degraded"] is False
    assert any("Refresh stale Hades quality report" in action for action in payload["actions"])
