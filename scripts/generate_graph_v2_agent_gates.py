#!/usr/bin/env python3
"""Run each frozen graph-v2 producer gate and write auditable evidence."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PYTHON = Path(os.environ.get("HADES_GATE_PYTHON", sys.executable))
DEFAULT_OUTPUT = ROOT / ".codex-artifacts" / "graph-v2" / "agent-gates.json"
GATES = (
    ("G01", "tests/hermes_cli/test_hades_graph_contract.py::test_v2_only"),
    ("G02", "tests/hermes_cli/test_hades_graph_v2_golden.py::test_identity_vectors"),
    ("G03", "tests/hermes_cli/test_hades_graph_contract.py::test_reference_resolution"),
    ("G04", "tests/hermes_cli/test_hades_graph_contract.py::test_privacy_rejection"),
    (
        "G05",
        "tests/hermes_cli/test_hades_graph_contract.py::test_evidence_flow_completeness_orthogonal",
    ),
    ("G06", "tests/hermes_cli/test_hades_graph_contract.py::test_omission_ledgers"),
    ("G07", "tests/hermes_cli/test_hades_graph_bundle.py::test_large_bundle"),
    ("G08", "tests/hermes_cli/test_hades_lifecycle_control_flow.py::test_cfg_matrix"),
    (
        "G09",
        "tests/hermes_cli/test_hades_lifecycle_framework_adapter.py::test_all_required_framework_golden_suites_are_registered",
    ),
    (
        "G10",
        "tests/hermes_cli/test_hades_lifecycle_traversal.py::test_async_terminal_semantics",
    ),
    ("G11", "tests/hermes_cli/test_hades_backend_indexer_golden.py::test_polyglot"),
    (
        "G12",
        "tests/hermes_cli/test_hades_index_enrichment.py::test_required_canary_failure_escapes_the_real_graph_publication_boundary",
    ),
    (
        "G13",
        "tests/hermes_cli/test_hades_graph_v2_golden.py::test_python_vectors_match_locked_contract",
    ),
    (
        "G14",
        "tests/hermes_cli/test_hades_graph_contract.py::test_base_provenance_and_candidate_ownership",
    ),
)


def _passed_count(output: str) -> int:
    lowered = output.lower()
    forbidden = (" skipped", " xfailed", " xpassed", " deselected")
    if any(marker in lowered for marker in forbidden):
        raise ValueError(
            "gate subprocess reported skipped, xfailed, or deselected tests"
        )
    matches = re.findall(r"(?m)(\d+) passed(?:\s|,|$)", lowered)
    if len(matches) != 1 or int(matches[0]) < 1:
        raise ValueError("gate subprocess did not report a passing test")
    return int(matches[0])


def _resolved_python(python: Path) -> Path:
    candidate = python.expanduser()
    if not candidate.is_absolute():
        candidate = ROOT / candidate
    # Preserve a virtualenv executable symlink: resolving the symlink itself
    # would record the base interpreter and silently lose the venv context.
    candidate = Path(os.path.abspath(candidate))
    if not candidate.is_file():
        raise FileNotFoundError(f"gate interpreter does not exist: {candidate}")
    return candidate


def generate(*, python: Path, output: Path) -> dict[str, object]:
    python = _resolved_python(python)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    records: list[dict[str, object]] = []
    for gate, node_id in GATES:
        command = [str(python), "-m", "pytest", node_id, "-q"]
        started = time.perf_counter()
        completed = subprocess.run(
            command,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        duration = time.perf_counter() - started
        if completed.returncode != 0:
            raise RuntimeError(
                f"{gate} failed with exit code {completed.returncode}:\n"
                f"{completed.stdout}"
            )
        try:
            _passed_count(completed.stdout)
        except ValueError as exc:
            raise RuntimeError(
                f"{gate} produced invalid pytest evidence:\n{completed.stdout}"
            ) from exc
        records.append({
            "gate": gate,
            "command": shlex.join(command),
            "passed": True,
            "duration_seconds": round(duration, 6),
        })
    report: dict[str, object] = {"gates": records}
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=output.parent,
        prefix=f".{output.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        json.dump(report, handle, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(output)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, default=DEFAULT_PYTHON)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    generate(python=args.python, output=args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
