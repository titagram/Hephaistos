"""Hades backend performance benchmark helpers."""

from __future__ import annotations

from pathlib import Path
import time
from typing import Any

from hermes_cli.hades_backend_jobs import execute_job
from hermes_cli.hades_backend_sync import _artifact_payload_hash, _artifact_upload_fields


DEFAULT_CASES = (
    {"name": "medium_code_graph", "symbols": 750, "routes": 90, "edges": 1200},
    {"name": "large_code_graph", "symbols": 5000, "routes": 500, "edges": 9000},
)
DURATION_WARN_MS = 1000
LARGE_COMPRESSION_RATIO_WARN = 0.75


def run_hades_backend_benchmark(
    cases: list[dict[str, int | str]] | None = None,
    *,
    workspace: str | Path | None = None,
) -> dict[str, Any]:
    selected_cases = list(DEFAULT_CASES) if cases is None else cases
    results = [_run_synthetic_case(case) for case in selected_cases]
    if workspace is not None:
        results.extend(_run_workspace_cases(Path(workspace).expanduser()))
    warnings = [warning for result in results for warning in result["warnings"]]

    return {
        "schema": "hades.backend_benchmark.v1",
        "status": "warning" if warnings else "passed",
        "case_count": len(results),
        "duration_warn_ms": DURATION_WARN_MS,
        "large_compression_ratio_warn": LARGE_COMPRESSION_RATIO_WARN,
        "has_workspace_dataset": workspace is not None,
        "warnings": warnings,
        "cases": results,
    }


def _run_synthetic_case(case: dict[str, int | str]) -> dict[str, Any]:
    name = str(case.get("name") or "code_graph")
    symbol_count = int(case.get("symbols") or 0)
    route_count = int(case.get("routes") or 0)
    edge_count = int(case.get("edges") or 0)
    artifact = _synthetic_code_graph(symbol_count=symbol_count, route_count=route_count, edge_count=edge_count)
    result = _run_artifact_case(name=name, artifact=artifact)
    result.update(
        {
            "source": "synthetic",
            "symbol_count": symbol_count,
            "route_count": route_count,
            "edge_count": edge_count,
        }
    )
    return result


def _run_workspace_cases(workspace_root: Path) -> list[dict[str, Any]]:
    root = workspace_root.resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"workspace does not exist or is not a directory: {root}")

    cases: list[dict[str, Any]] = []
    jobs = [
        {
            "name": "workspace_git_tree",
            "capability": "sync_git_tree",
            "payload": {"max_files": 20_000, "max_bytes": 20_000_000, "max_file_bytes": 1_000_000},
        },
        {
            "name": "workspace_code_graph",
            "capability": "populate_backend_ast",
            "payload": {"max_files": 5_000, "max_symbols": 10_000, "max_edges": 20_000, "max_file_bytes": 512_000},
        },
    ]
    for job in jobs:
        started = time.perf_counter()
        result = execute_job({"capability": job["capability"], "payload": job["payload"]}, workspace_root=root)
        index_duration_ms = max(0, int((time.perf_counter() - started) * 1000))
        artifact = result.get("artifact") if isinstance(result, dict) else None
        if not isinstance(artifact, dict):
            cases.append(
                {
                    "name": job["name"],
                    "source": "workspace",
                    "workspace": root.name,
                    "job_capability": job["capability"],
                    "schema": None,
                    "status": "failed",
                    "index_duration_ms": index_duration_ms,
                    "duration_ms": 0,
                    "total_duration_ms": index_duration_ms,
                    "upload_mode": "none",
                    "original_bytes": 0,
                    "compressed_bytes": 0,
                    "compression_ratio": None,
                    "payload_sha256": None,
                    "warnings": [f"{job['name']}: no artifact produced"],
                }
            )
            continue
        case = _run_artifact_case(name=job["name"], artifact=artifact)
        case.update(
            {
                "source": "workspace",
                "workspace": root.name,
                "job_capability": job["capability"],
                "job_status": result.get("status"),
                "summary": result.get("summary"),
                "index_duration_ms": index_duration_ms,
                "total_duration_ms": index_duration_ms + int(case["duration_ms"]),
            }
        )
        cases.append(case)
    return cases


def _run_artifact_case(*, name: str, artifact: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    upload_fields, compression = _artifact_upload_fields(artifact)
    payload_hash = _artifact_payload_hash(artifact)
    duration_ms = max(0, int((time.perf_counter() - started) * 1000))
    original_bytes = int(compression.get("original_bytes") or 0)
    compressed_bytes = int(compression.get("compressed_bytes") or 0)
    ratio = round(compressed_bytes / original_bytes, 4) if original_bytes and compressed_bytes else None
    upload_mode = "compressed" if upload_fields.get("artifact_encoding") == "gzip+base64" else "raw"
    warnings = _case_warnings(
        name=name,
        duration_ms=duration_ms,
        upload_mode=upload_mode,
        compression_ratio=ratio,
        original_bytes=original_bytes,
    )

    return {
        "name": name,
        "schema": artifact["schema"],
        "symbol_count": len(artifact.get("symbols") or []),
        "route_count": len(artifact.get("routes") or []),
        "edge_count": len(artifact.get("edges") or []),
        "file_count": len(artifact.get("files") or []),
        "raw_source_included": bool(artifact.get("raw_source_included", False)),
        "truncated": bool(artifact.get("truncated", False)),
        "redactions": int(artifact.get("redactions") or 0),
        "payload_sha256": payload_hash,
        "upload_mode": upload_mode,
        "original_bytes": original_bytes,
        "compressed_bytes": compressed_bytes,
        "compression_ratio": ratio,
        "duration_ms": duration_ms,
        "warnings": warnings,
    }


def _case_warnings(
    *,
    name: str,
    duration_ms: int,
    upload_mode: str,
    compression_ratio: float | None,
    original_bytes: int,
) -> list[str]:
    warnings: list[str] = []
    if duration_ms > DURATION_WARN_MS:
        warnings.append(f"{name}: artifact serialization/compression exceeded {DURATION_WARN_MS}ms")
    if original_bytes >= 256 * 1024 and upload_mode != "compressed":
        warnings.append(f"{name}: large artifact did not use compressed upload")
    if compression_ratio is not None and original_bytes >= 256 * 1024 and compression_ratio > LARGE_COMPRESSION_RATIO_WARN:
        warnings.append(f"{name}: compressed payload ratio {compression_ratio} is above {LARGE_COMPRESSION_RATIO_WARN}")
    return warnings


def _synthetic_code_graph(*, symbol_count: int, route_count: int, edge_count: int) -> dict[str, Any]:
    symbols = [
        {
            "kind": "controller_method" if index % 5 == 0 else "service_method",
            "name": f"OrderWorkflowSymbol{index}",
            "path": f"app/Services/OrderWorkflow{index % 37}.php",
            "line": 10 + index,
        }
        for index in range(symbol_count)
    ]
    routes = [
        {
            "method": "GET" if index % 3 else "POST",
            "uri": f"/api/orders/{index}",
            "name": f"orders.synthetic.{index}",
            "handler": f"App\\Http\\Controllers\\OrderController@synthetic{index % 53}",
            "path": "routes/api.php",
            "line": 20 + index,
        }
        for index in range(route_count)
    ]
    edges = [
        {
            "kind": "calls" if index % 2 else "uses_model",
            "from": f"OrderWorkflowSymbol{index % max(1, symbol_count)}",
            "to": f"OrderWorkflowSymbol{(index + 17) % max(1, symbol_count)}",
            "path": f"app/Services/OrderWorkflow{index % 37}.php",
            "line": 30 + index,
        }
        for index in range(edge_count)
    ]
    return {
        "schema": "hades.code_graph.v1",
        "framework": "synthetic",
        "routes": routes,
        "symbols": symbols,
        "edges": edges,
        "truncated": False,
        "redactions": 0,
        "raw_source_included": False,
    }
