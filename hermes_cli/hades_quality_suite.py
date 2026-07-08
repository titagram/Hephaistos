"""Hades no-codebase quality suite loader and aggregator."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hermes_cli.hades_no_codebase_eval import evaluate_no_codebase_diagnoses, load_no_codebase_eval_fixture


def load_quality_suite(path: str | Path) -> dict[str, Any]:
    suite_path = Path(path)
    data = json.loads(suite_path.read_text(encoding="utf-8"))
    data["_base_dir"] = str(suite_path.parent)
    return data


def run_quality_suite(suite: dict[str, Any]) -> dict[str, Any]:
    base_dir = Path(str(suite.get("_base_dir") or "."))
    suite_results: list[dict[str, Any]] = []
    total = passed = failed = 0
    for item in suite.get("suites") or []:
        if not isinstance(item, dict):
            continue
        fixture_path = Path(str(item.get("fixture") or ""))
        if not fixture_path.is_absolute():
            fixture_path = base_dir / fixture_path
        fixtures, runs = load_no_codebase_eval_fixture(fixture_path)
        report = evaluate_no_codebase_diagnoses(fixtures, runs).to_dict()
        min_accuracy = float(item.get("min_accuracy") or 1.0)
        min_root = float(item.get("min_root_cause_accuracy") or 1.0)
        min_insufficient = float(item.get("min_insufficient_accuracy") or 1.0)
        min_causal_pack = float(item.get("min_causal_pack_coverage") or 1.0)
        min_causal_chain = float(item.get("min_causal_chain_coverage") or 1.0)
        min_counterfactual = float(item.get("min_counterfactual_refusal_coverage") or 1.0)
        suite_passed = (
            report["status"] == "passed"
            and report["accuracy"] >= min_accuracy
            and report["root_cause_accuracy"] >= min_root
            and report["insufficient_accuracy"] >= min_insufficient
            and report["causal_pack_coverage"] >= min_causal_pack
            and report["causal_chain_coverage"] >= min_causal_chain
            and report["counterfactual_refusal_coverage"] >= min_counterfactual
        )
        total += 1
        passed += 1 if suite_passed else 0
        failed += 0 if suite_passed else 1
        suite_results.append(
            {
                "id": str(item.get("id") or fixture_path.stem),
                "fixture": str(fixture_path),
                "status": "passed" if suite_passed else "failed",
                "thresholds": {
                    "min_accuracy": min_accuracy,
                    "min_root_cause_accuracy": min_root,
                    "min_insufficient_accuracy": min_insufficient,
                    "min_causal_pack_coverage": min_causal_pack,
                    "min_causal_chain_coverage": min_causal_chain,
                    "min_counterfactual_refusal_coverage": min_counterfactual,
                },
                "metrics": report,
            }
        )
    return {
        "schema": "hades.no_codebase_quality_suite_report.v1",
        "status": "passed" if failed == 0 else "failed",
        "total": total,
        "passed": passed,
        "failed": failed,
        "suites": suite_results,
    }
