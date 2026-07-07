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
    )

    assert report["schema"] == "hades.quality_report.v1"
    assert report["status"] == "passed"
    assert report["summary"] == {"blockers": 0, "warnings": 0, "actions": 0}
    assert report["metrics"]["no_codebase"]["accuracy"] == 1.0
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
