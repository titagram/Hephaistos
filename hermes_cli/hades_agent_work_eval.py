"""Quality bridges from backend agent work items to no-codebase eval runs."""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from hermes_cli.hades_no_codebase_eval import (
    NoCodebaseDiagnosisFixture,
    NoCodebaseDiagnosisRun,
    evaluate_no_codebase_diagnoses,
)


def build_agent_work_no_codebase_report(
    fixtures: Sequence[NoCodebaseDiagnosisFixture],
    work_items: Iterable[Any],
) -> dict[str, Any]:
    """Evaluate completed kanban bug work items that opt into a fixture id."""

    fixture_by_id = {fixture.fixture_id: fixture for fixture in fixtures}
    runs: list[NoCodebaseDiagnosisRun] = []
    requested_fixture_ids: list[str] = []
    skipped: list[dict[str, str]] = []

    for item in work_items:
        projection = _project_work_item(item)
        payload = projection["payload"]
        result = projection["result"]
        work_item_id = projection["work_item_id"]
        fixture_id = _fixture_id(payload)
        if not fixture_id:
            continue
        requested_fixture_ids.append(fixture_id)
        if fixture_id not in fixture_by_id:
            skipped.append(
                {
                    "work_item_id": work_item_id,
                    "fixture_id": fixture_id,
                    "reason": "unknown_fixture",
                }
            )
            continue
        if projection["status"] not in {"completed", "completed_with_incomplete_memory"}:
            skipped.append(
                {
                    "work_item_id": work_item_id,
                    "fixture_id": fixture_id,
                    "reason": "not_completed",
                }
            )
            continue
        run = _diagnosis_run_from_result(fixture_id, result)
        if run is None:
            skipped.append(
                {
                    "work_item_id": work_item_id,
                    "fixture_id": fixture_id,
                    "reason": "missing_structured_diagnosis",
                }
            )
            continue
        runs.append(run)

    selected_fixtures = [
        fixture_by_id[fixture_id]
        for fixture_id in dict.fromkeys(requested_fixture_ids)
        if fixture_id in fixture_by_id
    ]
    if selected_fixtures:
        report = evaluate_no_codebase_diagnoses(selected_fixtures, runs).to_dict()
    else:
        report = _empty_pass_report()

    report["schema"] = "hades.agent_work_no_codebase_quality.v1"
    report["eligible_work_items"] = len(requested_fixture_ids)
    report["evaluated_work_items"] = len(runs)
    report["skipped_work_items"] = skipped
    return report


def _empty_pass_report() -> dict[str, Any]:
    return {
        "status": "passed",
        "total": 0,
        "passed": 0,
        "failed": 0,
        "complete_total": 0,
        "complete_passed": 0,
        "insufficient_total": 0,
        "insufficient_passed": 0,
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
        "results": [],
    }


def _project_work_item(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        return {
            "work_item_id": str(item.get("work_item_id") or item.get("id") or ""),
            "status": str(item.get("status") or ""),
            "payload": payload,
            "result": result,
        }
    payload = getattr(item, "payload", {}) if isinstance(getattr(item, "payload", {}), dict) else {}
    result = getattr(item, "result", {}) if isinstance(getattr(item, "result", {}), dict) else {}
    return {
        "work_item_id": str(getattr(item, "work_item_id", "") or ""),
        "status": str(getattr(item, "status", "") or ""),
        "payload": payload,
        "result": result,
    }


def _fixture_id(payload: dict[str, Any]) -> str:
    quality_eval = payload.get("quality_eval")
    if isinstance(quality_eval, dict):
        value = quality_eval.get("no_codebase_fixture_id") or quality_eval.get("fixture_id")
        if isinstance(value, str) and value.strip():
            return value.strip()
    value = payload.get("no_codebase_fixture_id")
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _diagnosis_run_from_result(fixture_id: str, result: dict[str, Any]) -> NoCodebaseDiagnosisRun | None:
    diagnosis = result.get("no_codebase_diagnosis")
    if not isinstance(diagnosis, dict):
        diagnosis = result.get("diagnosis")
    if not isinstance(diagnosis, dict):
        return None

    freshness = diagnosis.get("freshness") if isinstance(diagnosis.get("freshness"), dict) else {}
    awareness = diagnosis.get("awareness") if isinstance(diagnosis.get("awareness"), dict) else {}
    return NoCodebaseDiagnosisRun(
        fixture_id=fixture_id,
        root_cause_id=_optional_str(diagnosis.get("root_cause_id") or diagnosis.get("root_cause")),
        confidence=str(diagnosis.get("confidence") or ""),
        bug_class=str(diagnosis.get("bug_class") or ""),
        failure_classification=str(diagnosis.get("failure_classification") or ""),
        freshness_status=str(diagnosis.get("freshness_status") or freshness.get("status") or ""),
        diagnosable_without_source=_optional_bool(
            diagnosis.get("diagnosable_without_source")
            if "diagnosable_without_source" in diagnosis
            else awareness.get("diagnosable_without_source")
        ),
        evidence_refs=tuple(_string_values(diagnosis.get("evidence_refs", []))),
        tool_calls=tuple(_tool_names(diagnosis.get("tool_calls", []))),
        missing_evidence=tuple(_string_values(diagnosis.get("missing_evidence", []))),
        causal_pack_refs=tuple(_string_values(diagnosis.get("causal_pack_refs", []))),
        causal_chain=tuple(_string_values(diagnosis.get("causal_chain", []))),
        counterfactual_refused=bool(diagnosis.get("counterfactual_refused", False)),
        persisted_report=bool(diagnosis.get("persisted_report", False) or result.get("diagnosis_report_id")),
    )


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    clean = str(value).strip()
    return clean or None


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        clean = value.strip().lower()
        if clean in {"true", "1", "yes"}:
            return True
        if clean in {"false", "0", "no"}:
            return False
    return None


def _string_values(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    refs: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            refs.append(item.strip())
        elif isinstance(item, dict):
            kind = str(item.get("kind") or item.get("schema") or "").strip()
            ref = str(item.get("id") or item.get("ref") or item.get("name") or "").strip()
            if kind and ref:
                refs.append(f"{kind}:{ref}")
            elif ref:
                refs.append(ref)
    return refs


def _tool_names(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    names: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            names.append(item.strip())
        elif isinstance(item, dict):
            name = str(item.get("name") or item.get("tool") or item.get("function") or "").strip()
            if name:
                names.append(name)
    return names
