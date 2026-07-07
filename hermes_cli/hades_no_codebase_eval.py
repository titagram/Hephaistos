"""Evaluation helpers for Hades no-codebase bug diagnosis runs."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any


FORBIDDEN_NO_CODEBASE_TOOLS = {
    "cat",
    "exec",
    "exec_command",
    "find_files",
    "grep",
    "list_files",
    "open_file",
    "read_file",
    "ripgrep",
    "rg",
    "run_command",
    "shell",
    "terminal",
    "view_file",
}


@dataclass(frozen=True)
class NoCodebaseDiagnosisFixture:
    fixture_id: str
    title: str
    expected_root_cause_id: str | None
    expected_confidence: str
    expected_freshness_status: str = ""
    required_evidence_refs: tuple[str, ...] = ()
    required_tool_calls: tuple[str, ...] = ()
    expected_missing_evidence: tuple[str, ...] = ()
    requires_persisted_report: bool = True

    @property
    def expects_insufficient_evidence(self) -> bool:
        return self.expected_root_cause_id is None or self.expected_confidence == "insufficient"


@dataclass(frozen=True)
class NoCodebaseDiagnosisRun:
    fixture_id: str
    root_cause_id: str | None
    confidence: str
    freshness_status: str = ""
    evidence_refs: tuple[str, ...] = ()
    tool_calls: tuple[str, ...] = ()
    missing_evidence: tuple[str, ...] = ()
    persisted_report: bool = False


@dataclass(frozen=True)
class NoCodebaseFixtureResult:
    fixture_id: str
    passed: bool
    failures: tuple[str, ...] = ()
    expected_root_cause_id: str | None = None
    actual_root_cause_id: str | None = None
    expected_confidence: str = ""
    actual_confidence: str = ""
    expected_freshness_status: str = ""
    actual_freshness_status: str = ""
    expected_evidence_refs: tuple[str, ...] = ()
    actual_evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class NoCodebaseEvaluationReport:
    status: str
    total: int
    passed: int
    failed: int
    complete_total: int
    complete_passed: int
    insufficient_total: int
    insufficient_passed: int
    accuracy: float
    root_cause_accuracy: float
    insufficient_accuracy: float
    evidence_ref_coverage: float
    freshness_coverage: float
    tool_coverage: float
    persistence_coverage: float
    no_codebase_violations: tuple[dict[str, str], ...] = ()
    results: tuple[NoCodebaseFixtureResult, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "complete_total": self.complete_total,
            "complete_passed": self.complete_passed,
            "insufficient_total": self.insufficient_total,
            "insufficient_passed": self.insufficient_passed,
            "accuracy": self.accuracy,
            "root_cause_accuracy": self.root_cause_accuracy,
            "insufficient_accuracy": self.insufficient_accuracy,
            "evidence_ref_coverage": self.evidence_ref_coverage,
            "freshness_coverage": self.freshness_coverage,
            "tool_coverage": self.tool_coverage,
            "persistence_coverage": self.persistence_coverage,
            "no_codebase_violations": list(self.no_codebase_violations),
            "results": [
                {
                    "fixture_id": result.fixture_id,
                    "passed": result.passed,
                    "failures": list(result.failures),
                    "expected_root_cause_id": result.expected_root_cause_id,
                    "actual_root_cause_id": result.actual_root_cause_id,
                    "expected_confidence": result.expected_confidence,
                    "actual_confidence": result.actual_confidence,
                    "expected_freshness_status": result.expected_freshness_status,
                    "actual_freshness_status": result.actual_freshness_status,
                    "expected_evidence_refs": list(result.expected_evidence_refs),
                    "actual_evidence_refs": list(result.actual_evidence_refs),
                }
                for result in self.results
            ],
        }


def load_no_codebase_eval_fixture(path: str | Path) -> tuple[
    list[NoCodebaseDiagnosisFixture],
    list[NoCodebaseDiagnosisRun],
]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    fixtures = [_fixture_from_mapping(item) for item in data.get("fixtures", [])]
    runs = [_run_from_mapping(item) for item in data.get("runs", [])]
    return fixtures, runs


def evaluate_no_codebase_diagnoses(
    fixtures: Sequence[NoCodebaseDiagnosisFixture],
    runs: Sequence[NoCodebaseDiagnosisRun],
) -> NoCodebaseEvaluationReport:
    runs_by_id = {run.fixture_id: run for run in runs}
    results: list[NoCodebaseFixtureResult] = []
    violations: list[dict[str, str]] = []
    evidence_checks = 0
    evidence_hits = 0
    freshness_checks = 0
    freshness_hits = 0
    tool_checks = 0
    tool_hits = 0
    persistence_checks = 0
    persistence_hits = 0

    for fixture in fixtures:
        run = runs_by_id.get(fixture.fixture_id)
        if run is None:
            results.append(
                NoCodebaseFixtureResult(
                    fixture_id=fixture.fixture_id,
                    passed=False,
                    failures=("missing diagnosis run",),
                    expected_root_cause_id=fixture.expected_root_cause_id,
                    expected_confidence=fixture.expected_confidence,
                    expected_freshness_status=fixture.expected_freshness_status,
                    expected_evidence_refs=fixture.required_evidence_refs,
                )
            )
            continue

        failures: list[str] = []
        forbidden = _forbidden_tool_calls(run.tool_calls)
        for tool_name in forbidden:
            violations.append({"fixture_id": fixture.fixture_id, "tool": tool_name})
        if forbidden:
            failures.append("diagnosis used forbidden source-access tools")

        if fixture.expects_insufficient_evidence:
            if run.confidence != "insufficient":
                failures.append("expected insufficient confidence")
            if run.root_cause_id not in ("", None, "not_determined", "not determined"):
                failures.append("insufficient case must not claim a precise root cause")
            missing = set(fixture.expected_missing_evidence)
            if missing and not missing.issubset(set(run.missing_evidence)):
                failures.append("missing evidence classification does not match fixture")
        else:
            if run.root_cause_id != fixture.expected_root_cause_id:
                failures.append("root cause id mismatch")
            if run.confidence != fixture.expected_confidence:
                failures.append("confidence mismatch")

        expected_freshness = fixture.expected_freshness_status
        if expected_freshness:
            freshness_checks += 1
            if run.freshness_status == expected_freshness:
                freshness_hits += 1
            else:
                failures.append("freshness status mismatch")
        elif run.confidence in {"high", "medium"}:
            freshness_checks += 1
            if run.freshness_status == "current":
                freshness_hits += 1
        if run.confidence in {"high", "medium"} and run.freshness_status != "current":
            failures.append("precise diagnosis requires current freshness")

        required_refs = set(fixture.required_evidence_refs)
        actual_refs = set(run.evidence_refs)
        evidence_checks += len(required_refs)
        evidence_hits += len(required_refs.intersection(actual_refs))
        if not required_refs.issubset(actual_refs):
            failures.append("required evidence refs missing")

        required_tools = set(fixture.required_tool_calls)
        actual_tools = set(run.tool_calls)
        tool_checks += len(required_tools)
        tool_hits += len(required_tools.intersection(actual_tools))
        if not required_tools.issubset(actual_tools):
            failures.append("required Hades tool calls missing")

        if fixture.requires_persisted_report:
            persistence_checks += 1
            if run.persisted_report:
                persistence_hits += 1
            else:
                failures.append("diagnosis report was not persisted")

        results.append(
            NoCodebaseFixtureResult(
                fixture_id=fixture.fixture_id,
                passed=not failures,
                failures=tuple(failures),
                expected_root_cause_id=fixture.expected_root_cause_id,
                actual_root_cause_id=run.root_cause_id,
                expected_confidence=fixture.expected_confidence,
                actual_confidence=run.confidence,
                expected_freshness_status=fixture.expected_freshness_status,
                actual_freshness_status=run.freshness_status,
                expected_evidence_refs=fixture.required_evidence_refs,
                actual_evidence_refs=run.evidence_refs,
            )
        )

    total = len(results)
    passed = sum(1 for result in results if result.passed)
    complete_fixtures = [fixture for fixture in fixtures if not fixture.expects_insufficient_evidence]
    insufficient_fixtures = [fixture for fixture in fixtures if fixture.expects_insufficient_evidence]
    result_by_id = {result.fixture_id: result for result in results}
    complete_passed = sum(1 for fixture in complete_fixtures if result_by_id.get(fixture.fixture_id, None) and result_by_id[fixture.fixture_id].passed)
    insufficient_passed = sum(1 for fixture in insufficient_fixtures if result_by_id.get(fixture.fixture_id, None) and result_by_id[fixture.fixture_id].passed)

    return NoCodebaseEvaluationReport(
        status="passed" if total > 0 and passed == total and not violations else "failed",
        total=total,
        passed=passed,
        failed=total - passed,
        complete_total=len(complete_fixtures),
        complete_passed=complete_passed,
        insufficient_total=len(insufficient_fixtures),
        insufficient_passed=insufficient_passed,
        accuracy=_ratio(passed, total),
        root_cause_accuracy=_ratio(complete_passed, len(complete_fixtures)),
        insufficient_accuracy=_ratio(insufficient_passed, len(insufficient_fixtures)),
        evidence_ref_coverage=_ratio(evidence_hits, evidence_checks),
        freshness_coverage=_ratio(freshness_hits, freshness_checks),
        tool_coverage=_ratio(tool_hits, tool_checks),
        persistence_coverage=_ratio(persistence_hits, persistence_checks),
        no_codebase_violations=tuple(violations),
        results=tuple(results),
    )


def _fixture_from_mapping(data: Mapping[str, Any]) -> NoCodebaseDiagnosisFixture:
    return NoCodebaseDiagnosisFixture(
        fixture_id=str(data["id"]),
        title=str(data.get("title") or data["id"]),
        expected_root_cause_id=_optional_str(data.get("expected_root_cause_id")),
        expected_confidence=str(data["expected_confidence"]),
        expected_freshness_status=str(data.get("expected_freshness_status") or ""),
        required_evidence_refs=tuple(_string_values(data.get("required_evidence_refs", []))),
        required_tool_calls=tuple(_string_values(data.get("required_tool_calls", []))),
        expected_missing_evidence=tuple(_string_values(data.get("expected_missing_evidence", []))),
        requires_persisted_report=bool(data.get("requires_persisted_report", True)),
    )


def _run_from_mapping(data: Mapping[str, Any]) -> NoCodebaseDiagnosisRun:
    return NoCodebaseDiagnosisRun(
        fixture_id=str(data["fixture_id"]),
        root_cause_id=_optional_str(data.get("root_cause_id")),
        confidence=str(data.get("confidence") or ""),
        freshness_status=str(data.get("freshness_status") or ""),
        evidence_refs=tuple(_evidence_refs(data.get("evidence_refs", []))),
        tool_calls=tuple(_tool_names(data.get("tool_calls", []))),
        missing_evidence=tuple(_string_values(data.get("missing_evidence", []))),
        persisted_report=bool(data.get("persisted_report", False)),
    )


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _string_values(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if text:
            result.append(text)
    return result


def _evidence_refs(values: Iterable[Any]) -> list[str]:
    refs: list[str] = []
    for value in values:
        if isinstance(value, Mapping):
            ref_id = value.get("id") or value.get("ref") or value.get("evidence_id")
            ref_type = value.get("type")
            text = str(ref_id or "").strip()
            if text and ref_type:
                text = f"{ref_type}:{text}"
        else:
            text = str(value).strip()
        if text:
            refs.append(text)
    return refs


def _tool_names(values: Iterable[Any]) -> list[str]:
    names: list[str] = []
    for value in values:
        if isinstance(value, Mapping):
            name = value.get("name") or value.get("tool") or value.get("tool_name")
        else:
            name = value
        text = str(name or "").strip()
        if text:
            names.append(text)
    return names


def _forbidden_tool_calls(tool_calls: Iterable[str]) -> list[str]:
    forbidden: list[str] = []
    for name in tool_calls:
        normalized = str(name).strip().lower()
        if normalized in FORBIDDEN_NO_CODEBASE_TOOLS:
            forbidden.append(str(name))
    return forbidden


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 1.0
    return round(numerator / denominator, 4)
