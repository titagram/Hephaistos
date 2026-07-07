from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "hades" / "no_codebase_bug_cases.json"


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
    assert report["metrics"]["support"]["awareness_status"] == "ready"
    assert report["action_queue"] == []


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

    assert rc == 0
    assert recorded == payload
    assert recorded_at is not None


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
