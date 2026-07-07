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
    assert payload["freshness_coverage"] == 1.0
    assert payload["awareness_coverage"] == 1.0
    assert payload["tool_coverage"] == 1.0
    assert payload["tool_order_coverage"] == 1.0
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
        freshness_status=first.freshness_status,
        diagnosable_without_source=first.diagnosable_without_source,
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


def test_hades_no_codebase_eval_detects_namespaced_real_trajectory_tool_shapes(tmp_path):
    from hermes_cli.hades_no_codebase_eval import evaluate_no_codebase_diagnoses, load_no_codebase_eval_fixture

    fixture = tmp_path / "eval.json"
    fixture.write_text(
        json.dumps(
            {
                "schema": "hades.no_codebase_eval.v1",
                "fixtures": [
                    {
                        "id": "real_trajectory_shape",
                        "title": "Real trajectory shape",
                        "expected_root_cause_id": None,
                        "expected_confidence": "insufficient",
                        "requires_persisted_report": False,
                    }
                ],
                "runs": [
                    {
                        "fixture_id": "real_trajectory_shape",
                        "root_cause_id": None,
                        "confidence": "insufficient",
                        "tool_calls": [
                            {"function": {"name": "functions.exec_command"}},
                            {"recipient_name": "mcp__filesystem__read_file"},
                            "terminal.run",
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    fixtures, runs = load_no_codebase_eval_fixture(fixture)
    report = evaluate_no_codebase_diagnoses(fixtures, runs).to_dict()

    assert report["status"] == "failed"
    assert report["no_codebase_violations"] == [
        {"fixture_id": "real_trajectory_shape", "tool": "functions.exec_command"},
        {"fixture_id": "real_trajectory_shape", "tool": "mcp__filesystem__read_file"},
        {"fixture_id": "real_trajectory_shape", "tool": "terminal.run"},
    ]


def test_hades_no_codebase_eval_loads_sharegpt_trajectory_runs(tmp_path):
    from hermes_cli.hades_no_codebase_eval import evaluate_no_codebase_diagnoses, load_no_codebase_eval_fixture

    trajectory = tmp_path / "trajectory.jsonl"
    trajectory.write_text(
        json.dumps(
            {
                "conversations": [
                    {"from": "system", "value": "tools available"},
                    {"from": "human", "value": "Diagnose BUG-1 in no-codebase mode."},
                    {
                        "from": "gpt",
                        "value": "\n".join(
                            [
                                "<tool_call>",
                                json.dumps({"name": "hades_backend_project_awareness_status", "arguments": {}}),
                                "</tool_call>",
                                "<tool_call>",
                                json.dumps({"name": "hades_backend_bug_evidence_search", "arguments": {"query": "BUG-1"}}),
                                "</tool_call>",
                            ]
                        ),
                    },
                    {"from": "tool", "value": "<tool_response>{}</tool_response>"},
                    {
                        "from": "gpt",
                        "value": "\n".join(
                            [
                                "<tool_call>",
                                json.dumps({"name": "hades_backend_graph_search", "arguments": {"query": "OrderController"}}),
                                "</tool_call>",
                                "<tool_call>",
                                json.dumps(
                                    {
                                        "name": "hades_backend_source_slice_fetch",
                                        "arguments": {"source_slice_id": "slice.order_controller.show"},
                                    }
                                ),
                                "</tool_call>",
                                "<tool_call>",
                                json.dumps(
                                    {
                                        "name": "hades_backend_diagnosis_report_create",
                                        "arguments": {
                                            "root_cause": "rc.trajectory.order_customer_null",
                                            "confidence": "high",
                                            "evidence_refs": [
                                                {"type": "bug_evidence", "id": "ev.order.stack"},
                                                {"type": "source_slice", "id": "slice.order_controller.show"},
                                            ],
                                            "freshness": {"status": "current"},
                                            "awareness": {"diagnosable_without_source": True},
                                        },
                                    }
                                ),
                                "</tool_call>",
                            ]
                        ),
                    },
                    {
                        "from": "gpt",
                        "value": json.dumps(
                            {
                                "root_cause_id": "rc.trajectory.order_customer_null",
                                "confidence": "high",
                                "freshness": {"status": "current"},
                                "awareness": {"diagnosable_without_source": True},
                                "evidence_refs": [
                                    "bug_evidence:ev.order.stack",
                                    "source_slice:slice.order_controller.show",
                                ],
                            }
                        ),
                    },
                ],
                "completed": True,
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
                        "id": "trajectory_case",
                        "title": "Trajectory case",
                        "expected_root_cause_id": "rc.trajectory.order_customer_null",
                        "expected_confidence": "high",
                        "expected_freshness_status": "current",
                        "expected_diagnosable_without_source": True,
                        "required_evidence_refs": [
                            "bug_evidence:ev.order.stack",
                            "source_slice:slice.order_controller.show",
                        ],
                        "required_tool_calls": [
                            "hades_backend_project_awareness_status",
                            "hades_backend_bug_evidence_search",
                            "hades_backend_graph_search",
                            "hades_backend_source_slice_fetch",
                            "hades_backend_diagnosis_report_create",
                        ],
                    }
                ],
                "trajectory_runs": [{"fixture_id": "trajectory_case", "trajectory_path": "trajectory.jsonl"}],
            }
        ),
        encoding="utf-8",
    )

    fixtures, runs = load_no_codebase_eval_fixture(eval_file)
    report = evaluate_no_codebase_diagnoses(fixtures, runs).to_dict()

    assert report["status"] == "passed"
    assert report["total"] == 1
    assert report["tool_order_coverage"] == 1.0
    assert report["persistence_coverage"] == 1.0


def test_hades_no_codebase_eval_discovers_trajectory_globs(tmp_path):
    from hermes_cli.hades_no_codebase_eval import evaluate_no_codebase_diagnoses, load_no_codebase_eval_fixture

    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    (runs_dir / "glob_case.json").write_text(
        json.dumps(
            {
                "metadata": {"fixture_id": "glob_case"},
                "messages": [
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {"function": {"name": "hades_backend_project_awareness_status", "arguments": "{}"}},
                            {
                                "function": {
                                    "name": "hades_backend_diagnosis_report_create",
                                    "arguments": json.dumps(
                                        {
                                            "root_cause": "rc.glob.case",
                                            "confidence": "high",
                                            "evidence_refs": [{"type": "bug_evidence", "id": "ev.glob"}],
                                            "freshness": {"status": "current"},
                                            "awareness": {"diagnosable_without_source": True},
                                        }
                                    ),
                                }
                            },
                        ],
                    },
                    {
                        "role": "assistant",
                        "content": json.dumps(
                            {
                                "root_cause_id": "rc.glob.case",
                                "confidence": "high",
                                "freshness": {"status": "current"},
                                "awareness": {"diagnosable_without_source": True},
                                "evidence_refs": ["bug_evidence:ev.glob"],
                            }
                        ),
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    eval_file = tmp_path / "eval.json"
    eval_file.write_text(
        json.dumps(
            {
                "schema": "hades.no_codebase_eval.v1",
                "fixtures": [
                    {
                        "id": "glob_case",
                        "title": "Glob case",
                        "expected_root_cause_id": "rc.glob.case",
                        "expected_confidence": "high",
                        "expected_freshness_status": "current",
                        "expected_diagnosable_without_source": True,
                        "required_evidence_refs": ["bug_evidence:ev.glob"],
                        "required_tool_calls": [
                            "hades_backend_project_awareness_status",
                            "hades_backend_diagnosis_report_create",
                        ],
                    }
                ],
                "trajectory_globs": [{"pattern": "runs/*.json"}],
            }
        ),
        encoding="utf-8",
    )

    fixtures, runs = load_no_codebase_eval_fixture(eval_file)
    report = evaluate_no_codebase_diagnoses(fixtures, runs).to_dict()

    assert [run.fixture_id for run in runs] == ["glob_case"]
    assert report["status"] == "passed"
    assert report["total"] == 1


def test_hades_no_codebase_eval_fails_when_hades_tools_are_out_of_order():
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
        freshness_status=first.freshness_status,
        diagnosable_without_source=first.diagnosable_without_source,
        evidence_refs=first.evidence_refs,
        tool_calls=first.tool_calls[1:] + first.tool_calls[:1],
        missing_evidence=first.missing_evidence,
        persisted_report=first.persisted_report,
    )

    report = evaluate_no_codebase_diagnoses(fixtures, runs).to_dict()
    failed = [result for result in report["results"] if not result["passed"]]

    assert report["status"] == "failed"
    assert report["tool_coverage"] == 1.0
    assert report["tool_order_coverage"] < 1.0
    assert failed[0]["fixture_id"] == "laravel_service_dependency_null"
    assert failed[0]["failures"] == ["required Hades tool calls out of order"]


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
                freshness_status="current",
                diagnosable_without_source=False,
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
        "precise diagnosis requires source-free diagnosable awareness",
    ]


def test_hades_no_codebase_eval_blocks_precise_claim_when_freshness_is_stale():
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
        confidence="high",
        freshness_status="stale",
        diagnosable_without_source=first.diagnosable_without_source,
        evidence_refs=first.evidence_refs,
        tool_calls=first.tool_calls,
        missing_evidence=first.missing_evidence,
        persisted_report=first.persisted_report,
    )

    report = evaluate_no_codebase_diagnoses(fixtures, runs).to_dict()
    failed = [result for result in report["results"] if not result["passed"]]

    assert report["status"] == "failed"
    assert report["freshness_coverage"] < 1.0
    assert failed[0]["fixture_id"] == "laravel_service_dependency_null"
    assert failed[0]["failures"] == [
        "freshness status mismatch",
        "precise diagnosis requires current freshness",
    ]


def test_hades_no_codebase_eval_blocks_precise_claim_when_awareness_is_not_diagnosable():
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
        confidence="high",
        freshness_status="current",
        diagnosable_without_source=False,
        evidence_refs=first.evidence_refs,
        tool_calls=first.tool_calls,
        missing_evidence=first.missing_evidence,
        persisted_report=first.persisted_report,
    )

    report = evaluate_no_codebase_diagnoses(fixtures, runs).to_dict()
    failed = [result for result in report["results"] if not result["passed"]]

    assert report["status"] == "failed"
    assert report["awareness_coverage"] < 1.0
    assert failed[0]["fixture_id"] == "laravel_service_dependency_null"
    assert failed[0]["failures"] == [
        "diagnosable_without_source mismatch",
        "precise diagnosis requires source-free diagnosable awareness",
    ]
