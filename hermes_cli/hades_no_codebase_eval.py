"""Evaluation helpers for Hades no-codebase bug diagnosis runs."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
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

TOOL_CALL_RE = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL | re.IGNORECASE)
THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


@dataclass(frozen=True)
class NoCodebaseDiagnosisFixture:
    fixture_id: str
    title: str
    expected_root_cause_id: str | None
    expected_confidence: str
    expected_bug_class: str = ""
    expected_failure_classification: str = ""
    expected_freshness_status: str = ""
    expected_diagnosable_without_source: bool | None = None
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
    bug_class: str = ""
    failure_classification: str = ""
    freshness_status: str = ""
    diagnosable_without_source: bool | None = None
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
    expected_bug_class: str = ""
    actual_bug_class: str = ""
    expected_failure_classification: str = ""
    actual_failure_classification: str = ""
    expected_freshness_status: str = ""
    actual_freshness_status: str = ""
    expected_diagnosable_without_source: bool | None = None
    actual_diagnosable_without_source: bool | None = None
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
    awareness_coverage: float
    tool_coverage: float
    tool_order_coverage: float
    persistence_coverage: float
    taxonomy_coverage: float
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
            "awareness_coverage": self.awareness_coverage,
            "tool_coverage": self.tool_coverage,
            "tool_order_coverage": self.tool_order_coverage,
            "persistence_coverage": self.persistence_coverage,
            "taxonomy_coverage": self.taxonomy_coverage,
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
                    "expected_bug_class": result.expected_bug_class,
                    "actual_bug_class": result.actual_bug_class,
                    "expected_failure_classification": result.expected_failure_classification,
                    "actual_failure_classification": result.actual_failure_classification,
                    "expected_freshness_status": result.expected_freshness_status,
                    "actual_freshness_status": result.actual_freshness_status,
                    "expected_diagnosable_without_source": result.expected_diagnosable_without_source,
                    "actual_diagnosable_without_source": result.actual_diagnosable_without_source,
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
    fixture_path = Path(path)
    data = json.loads(fixture_path.read_text(encoding="utf-8"))
    fixtures = [_fixture_from_mapping(item) for item in data.get("fixtures", [])]
    runs = [_run_from_mapping(item) for item in data.get("runs", [])]
    runs.extend(_trajectory_run_from_mapping(item, base_dir=fixture_path.parent) for item in data.get("trajectory_runs", []))
    for item in data.get("trajectory_globs", []):
        runs.extend(_trajectory_runs_from_glob(item, base_dir=fixture_path.parent))
    for item in data.get("trajectory_dirs", []):
        runs.extend(_trajectory_runs_from_dir(item, base_dir=fixture_path.parent))
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
    awareness_checks = 0
    awareness_hits = 0
    tool_checks = 0
    tool_hits = 0
    tool_order_checks = 0
    tool_order_hits = 0
    persistence_checks = 0
    persistence_hits = 0
    taxonomy_checks = 0
    taxonomy_hits = 0

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
                    expected_bug_class=fixture.expected_bug_class,
                    expected_failure_classification=fixture.expected_failure_classification,
                    expected_freshness_status=fixture.expected_freshness_status,
                    expected_diagnosable_without_source=fixture.expected_diagnosable_without_source,
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
            if str(run.root_cause_id or "").strip().lower() not in {"", "not_determined", "not determined", "null", "none", "n/a", "not_applicable"}:
                failures.append("insufficient case must not claim a precise root cause")
            missing = set(fixture.expected_missing_evidence)
            if missing and not missing.issubset(set(run.missing_evidence)):
                failures.append("missing evidence classification does not match fixture")
        else:
            if run.root_cause_id != fixture.expected_root_cause_id:
                failures.append("root cause id mismatch")
            if run.confidence != fixture.expected_confidence:
                failures.append("confidence mismatch")
        if fixture.expected_bug_class:
            taxonomy_checks += 1
            if run.bug_class == fixture.expected_bug_class:
                taxonomy_hits += 1
            else:
                failures.append("bug class mismatch")
        if fixture.expected_failure_classification:
            taxonomy_checks += 1
            if run.failure_classification == fixture.expected_failure_classification:
                taxonomy_hits += 1
            else:
                failures.append("failure classification mismatch")

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
        expected_awareness = fixture.expected_diagnosable_without_source
        if expected_awareness is not None:
            awareness_checks += 1
            if run.diagnosable_without_source is expected_awareness:
                awareness_hits += 1
            else:
                failures.append("diagnosable_without_source mismatch")
        elif run.confidence in {"high", "medium"}:
            awareness_checks += 1
            if run.diagnosable_without_source is True:
                awareness_hits += 1
        if run.confidence in {"high", "medium"} and run.diagnosable_without_source is not True:
            failures.append("precise diagnosis requires source-free diagnosable awareness")

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
        elif fixture.required_tool_calls:
            tool_order_checks += 1
            if _required_tools_in_order(fixture.required_tool_calls, run.tool_calls):
                tool_order_hits += 1
            else:
                failures.append("required Hades tool calls out of order")

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
                expected_bug_class=fixture.expected_bug_class,
                actual_bug_class=run.bug_class,
                expected_failure_classification=fixture.expected_failure_classification,
                actual_failure_classification=run.failure_classification,
                expected_freshness_status=fixture.expected_freshness_status,
                actual_freshness_status=run.freshness_status,
                expected_diagnosable_without_source=fixture.expected_diagnosable_without_source,
                actual_diagnosable_without_source=run.diagnosable_without_source,
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
        awareness_coverage=_ratio(awareness_hits, awareness_checks),
        tool_coverage=_ratio(tool_hits, tool_checks),
        tool_order_coverage=_ratio(tool_order_hits, tool_order_checks),
        persistence_coverage=_ratio(persistence_hits, persistence_checks),
        taxonomy_coverage=_ratio(taxonomy_hits, taxonomy_checks),
        no_codebase_violations=tuple(violations),
        results=tuple(results),
    )


def _fixture_from_mapping(data: Mapping[str, Any]) -> NoCodebaseDiagnosisFixture:
    return NoCodebaseDiagnosisFixture(
        fixture_id=str(data["id"]),
        title=str(data.get("title") or data["id"]),
        expected_root_cause_id=_optional_str(data.get("expected_root_cause_id")),
        expected_confidence=str(data["expected_confidence"]),
        expected_bug_class=str(data.get("expected_bug_class") or ""),
        expected_failure_classification=str(data.get("expected_failure_classification") or ""),
        expected_freshness_status=str(data.get("expected_freshness_status") or ""),
        expected_diagnosable_without_source=_optional_bool(data.get("expected_diagnosable_without_source")),
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
        bug_class=str(data.get("bug_class") or ""),
        failure_classification=str(data.get("failure_classification") or ""),
        freshness_status=str(data.get("freshness_status") or ""),
        diagnosable_without_source=_optional_bool(data.get("diagnosable_without_source")),
        evidence_refs=tuple(_evidence_refs(data.get("evidence_refs", []))),
        tool_calls=tuple(_tool_names(data.get("tool_calls", []))),
        missing_evidence=tuple(_string_values(data.get("missing_evidence", []))),
        persisted_report=bool(data.get("persisted_report", False)),
    )


def _trajectory_run_from_mapping(data: Mapping[str, Any], *, base_dir: Path) -> NoCodebaseDiagnosisRun:
    trajectory_path = data.get("trajectory_path") or data.get("path")
    if not trajectory_path:
        fixture_id = str(data.get("fixture_id") or "<unknown>")
        raise ValueError(f"trajectory_runs entry for {fixture_id} is missing trajectory_path")
    path = Path(str(trajectory_path)).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    entry = _load_trajectory_entry(path, index=data.get("index"))
    fixture_id = str(data.get("fixture_id") or _trajectory_fixture_id(entry, path))
    return _trajectory_run_from_entry(
        entry,
        fixture_id=fixture_id,
        persisted_report=data.get("persisted_report"),
    )


def _trajectory_runs_from_glob(data: Any, *, base_dir: Path) -> list[NoCodebaseDiagnosisRun]:
    if isinstance(data, Mapping):
        pattern = str(data.get("pattern") or data.get("path") or "").strip()
        fixture_id = _optional_str(data.get("fixture_id"))
        index = data.get("index")
        persisted_report = data.get("persisted_report")
    else:
        pattern = str(data or "").strip()
        fixture_id = None
        index = None
        persisted_report = None
    if not pattern:
        raise ValueError("trajectory_globs entry is missing pattern")
    paths = sorted(path for path in base_dir.glob(pattern) if path.is_file() and path.suffix in {".json", ".jsonl"})
    return [_trajectory_run_from_path(path, fixture_id=fixture_id, index=index, persisted_report=persisted_report) for path in paths]


def _trajectory_runs_from_dir(data: Any, *, base_dir: Path) -> list[NoCodebaseDiagnosisRun]:
    if isinstance(data, Mapping):
        raw_path = str(data.get("path") or data.get("dir") or "").strip()
        pattern = str(data.get("pattern") or "*.json*").strip()
        fixture_id = _optional_str(data.get("fixture_id"))
        index = data.get("index")
        persisted_report = data.get("persisted_report")
        recursive = bool(data.get("recursive", False))
    else:
        raw_path = str(data or "").strip()
        pattern = "*.json*"
        fixture_id = None
        index = None
        persisted_report = None
        recursive = False
    if not raw_path:
        raise ValueError("trajectory_dirs entry is missing path")
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    globber = path.rglob if recursive else path.glob
    paths = sorted(candidate for candidate in globber(pattern) if candidate.is_file() and candidate.suffix in {".json", ".jsonl"})
    return [_trajectory_run_from_path(candidate, fixture_id=fixture_id, index=index, persisted_report=persisted_report) for candidate in paths]


def _trajectory_run_from_path(
    path: Path,
    *,
    fixture_id: str | None = None,
    index: Any = None,
    persisted_report: Any = None,
) -> NoCodebaseDiagnosisRun:
    entry = _load_trajectory_entry(path, index=index)
    return _trajectory_run_from_entry(
        entry,
        fixture_id=fixture_id or _trajectory_fixture_id(entry, path),
        persisted_report=persisted_report,
    )


def _trajectory_run_from_entry(
    entry: Any,
    *,
    fixture_id: str,
    persisted_report: Any = None,
) -> NoCodebaseDiagnosisRun:
    conversations = _trajectory_conversations(entry)
    tool_calls = _trajectory_tool_calls(conversations)
    final_payload = _trajectory_final_payload(conversations)
    diagnosis_args = _last_tool_arguments(tool_calls, "hades_backend_diagnosis_report_create")
    freshness = _mapping_value(final_payload.get("freshness")) or _mapping_value(diagnosis_args.get("freshness"))
    awareness = _mapping_value(final_payload.get("awareness")) or _mapping_value(diagnosis_args.get("awareness"))
    confidence = final_payload.get("confidence") or diagnosis_args.get("confidence") or ""
    evidence_refs = final_payload.get("evidence_refs")
    if evidence_refs is None:
        evidence_refs = diagnosis_args.get("evidence_refs", [])
    if persisted_report is None:
        persisted_report = bool(diagnosis_args)
    return NoCodebaseDiagnosisRun(
        fixture_id=fixture_id,
        root_cause_id=_optional_str(
            final_payload.get("root_cause_id")
            or diagnosis_args.get("root_cause_id")
            or final_payload.get("root_cause")
            or diagnosis_args.get("root_cause")
        ),
        confidence=str(confidence or ""),
        bug_class=str(final_payload.get("bug_class") or diagnosis_args.get("bug_class") or ""),
        failure_classification=str(
            final_payload.get("failure_classification") or diagnosis_args.get("failure_classification") or ""
        ),
        freshness_status=str(final_payload.get("freshness_status") or freshness.get("status") or ""),
        diagnosable_without_source=_optional_bool(
            final_payload.get("diagnosable_without_source") if "diagnosable_without_source" in final_payload else awareness.get("diagnosable_without_source")
        ),
        evidence_refs=tuple(_evidence_refs(evidence_refs if isinstance(evidence_refs, Iterable) and not isinstance(evidence_refs, (str, bytes)) else [])),
        tool_calls=tuple(name for name, _args in tool_calls),
        missing_evidence=tuple(_string_values(final_payload.get("missing_evidence", []))),
        persisted_report=bool(persisted_report),
    )


def _trajectory_fixture_id(entry: Any, path: Path) -> str:
    if isinstance(entry, Mapping):
        for key in ("fixture_id", "case_id", "eval_id", "id"):
            if value := _optional_str(entry.get(key)):
                return value
        metadata = entry.get("metadata")
        if isinstance(metadata, Mapping):
            for key in ("fixture_id", "case_id", "eval_id", "id"):
                if value := _optional_str(metadata.get(key)):
                    return value
    return path.stem


def _load_trajectory_entry(path: Path, *, index: Any = None) -> Any:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".jsonl":
        entries = [json.loads(line) for line in text.splitlines() if line.strip()]
        if not entries:
            raise ValueError(f"trajectory file is empty: {path}")
        selected = int(index) if index is not None else -1
        return entries[selected]
    return json.loads(text)


def _trajectory_conversations(entry: Any) -> list[Mapping[str, Any]]:
    if isinstance(entry, list):
        raw = entry
    elif isinstance(entry, Mapping):
        raw = entry.get("conversations") or entry.get("messages") or entry.get("trajectory") or []
    else:
        raw = []
    return [item for item in raw if isinstance(item, Mapping)]


def _trajectory_tool_calls(conversations: Sequence[Mapping[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    calls: list[tuple[str, dict[str, Any]]] = []
    for message in conversations:
        raw_tool_calls = message.get("tool_calls")
        if isinstance(raw_tool_calls, Iterable) and not isinstance(raw_tool_calls, (str, bytes, Mapping)):
            for raw_call in raw_tool_calls:
                call = _tool_call_from_mapping(raw_call)
                if call is not None:
                    calls.append(call)
        function_call = message.get("function_call")
        if isinstance(function_call, Mapping):
            call = _tool_call_from_mapping(function_call)
            if call is not None:
                calls.append(call)
        content = _message_text(message)
        if content:
            calls.extend(_tool_calls_from_text(content))
    return calls


def _tool_call_from_mapping(value: Any) -> tuple[str, dict[str, Any]] | None:
    if not isinstance(value, Mapping):
        return None
    function = value.get("function")
    function_args = function.get("arguments") if isinstance(function, Mapping) else None
    raw_args = value.get("arguments", function_args)
    name = value.get("name") or value.get("tool") or value.get("tool_name") or value.get("recipient_name")
    if not name and isinstance(function, Mapping):
        name = function.get("name")
    text = str(name or "").strip()
    if not text:
        return None
    return text, _json_object(raw_args)


def _tool_calls_from_text(value: str) -> list[tuple[str, dict[str, Any]]]:
    calls: list[tuple[str, dict[str, Any]]] = []
    for match in TOOL_CALL_RE.finditer(value):
        payload = _json_object(match.group(1))
        call = _tool_call_from_mapping(payload)
        if call is not None:
            calls.append(call)
    return calls


def _trajectory_final_payload(conversations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    for message in reversed(conversations):
        role = str(message.get("role") or message.get("from") or "").lower()
        if role not in {"assistant", "gpt"}:
            continue
        payload = _json_object(_strip_trajectory_markup(_message_text(message)))
        if payload:
            return payload
    return {}


def _last_tool_arguments(tool_calls: Sequence[tuple[str, dict[str, Any]]], tool_name: str) -> dict[str, Any]:
    for name, args in reversed(tool_calls):
        if name == tool_name:
            return args
    return {}


def _message_text(message: Mapping[str, Any]) -> str:
    value = message.get("value")
    if value is None:
        value = message.get("content")
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for part in value:
            if isinstance(part, Mapping):
                text = part.get("text") or part.get("content")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(parts)
    return ""


def _strip_trajectory_markup(value: str) -> str:
    stripped = THINK_RE.sub("", value)
    stripped = TOOL_CALL_RE.sub("", stripped)
    return stripped.strip()


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if not isinstance(value, str):
        return {}
    text = value.strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _mapping_value(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if text.lower() in {"null", "none", "n/a", "not_applicable"}:
        return None
    return text or None


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return None


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
            function = value.get("function")
            function_name = function.get("name") if isinstance(function, Mapping) else None
            name = (
                value.get("name")
                or value.get("tool")
                or value.get("tool_name")
                or value.get("recipient_name")
                or function_name
            )
        else:
            name = value
        text = str(name or "").strip()
        if text:
            names.append(text)
    return names


def _forbidden_tool_calls(tool_calls: Iterable[str]) -> list[str]:
    forbidden: list[str] = []
    for name in tool_calls:
        if any(fragment in FORBIDDEN_NO_CODEBASE_TOOLS for fragment in _tool_name_fragments(name)):
            forbidden.append(str(name))
    return forbidden


def _tool_name_fragments(name: str) -> tuple[str, ...]:
    normalized = str(name).strip().lower()
    if not normalized:
        return ()
    fragments = {normalized}
    pending = [normalized]
    for separator in (".", "::", ":", "/", "\\", "__", " "):
        next_pending: list[str] = []
        for item in pending:
            parts = [part for part in item.split(separator) if part]
            if len(parts) > 1:
                fragments.update(parts)
                next_pending.extend(parts)
        pending.extend(next_pending)
    return tuple(fragments)


def _required_tools_in_order(required: Sequence[str], actual: Sequence[str]) -> bool:
    if not required:
        return True
    next_index = 0
    for tool_name in actual:
        if tool_name == required[next_index]:
            next_index += 1
            if next_index == len(required):
                return True
    return False


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 1.0
    return round(numerator / denominator, 4)
