from __future__ import annotations

import json
from pathlib import Path


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "hades" / "no_codebase_bug_cases.json"


def test_hades_no_codebase_eval_suite_reports_accuracy_and_coverage():
    from hermes_cli.hades_no_codebase_eval import (
        evaluate_no_codebase_diagnoses,
        load_no_codebase_eval_fixture,
    )

    fixtures, runs = load_no_codebase_eval_fixture(FIXTURE_PATH)
    report = evaluate_no_codebase_diagnoses(fixtures, runs)
    payload = report.to_dict()

    assert payload["status"] == "passed"
    assert payload["total"] == 7
    assert payload["passed"] == 7
    assert payload["complete_total"] == 5
    assert payload["complete_passed"] == 5
    assert payload["insufficient_total"] == 2
    assert payload["insufficient_passed"] == 2
    assert payload["accuracy"] == 1.0
    assert payload["root_cause_accuracy"] == 1.0
    assert payload["insufficient_accuracy"] == 1.0
    assert payload["evidence_ref_coverage"] == 1.0
    assert payload["tool_coverage"] == 1.0
    assert payload["persistence_coverage"] == 1.0
    assert payload["no_codebase_violations"] == []
    assert json.loads(json.dumps(payload)) == payload


def test_hades_no_codebase_eval_fails_on_source_file_tool_access():
    from hermes_cli.hades_no_codebase_eval import (
        NoCodebaseDiagnosisRun,
        evaluate_no_codebase_diagnoses,
        load_no_codebase_eval_fixture,
    )

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

    report = evaluate_no_codebase_diagnoses(fixtures, runs).to_dict()

    assert report["status"] == "failed"
    assert report["failed"] == 1
    assert report["no_codebase_violations"] == [
        {"fixture_id": "laravel_service_dependency_null", "tool": "read_file"}
    ]
    failed = [result for result in report["results"] if not result["passed"]]
    assert failed[0]["failures"] == ["diagnosis used forbidden source-access tools"]


def test_hades_no_codebase_eval_blocks_precise_claim_when_evidence_is_missing():
    from hermes_cli.hades_no_codebase_eval import (
        NoCodebaseDiagnosisRun,
        evaluate_no_codebase_diagnoses,
        load_no_codebase_eval_fixture,
    )

    fixtures, runs = load_no_codebase_eval_fixture(FIXTURE_PATH)
    bad_runs = []
    for run in runs:
        if run.fixture_id != "incomplete_missing_source_slice":
            bad_runs.append(run)
            continue
        bad_runs.append(
            NoCodebaseDiagnosisRun(
                fixture_id=run.fixture_id,
                root_cause_id="rc.guessed.from.memory",
                confidence="high",
                evidence_refs=run.evidence_refs,
                tool_calls=run.tool_calls,
                missing_evidence=(),
                persisted_report=run.persisted_report,
            )
        )

    report = evaluate_no_codebase_diagnoses(fixtures, bad_runs).to_dict()
    failed = {result["fixture_id"]: result for result in report["results"] if not result["passed"]}

    assert report["status"] == "failed"
    assert report["insufficient_passed"] == 1
    assert failed["incomplete_missing_source_slice"]["failures"] == [
        "expected insufficient confidence",
        "insufficient case must not claim a precise root cause",
        "missing evidence classification does not match fixture",
    ]

