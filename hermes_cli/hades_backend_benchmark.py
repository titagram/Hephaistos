"""Synthetic Hades backend performance benchmark helpers."""

from __future__ import annotations

import time
from typing import Any

from hermes_cli.hades_backend_sync import _artifact_payload_hash, _artifact_upload_fields


DEFAULT_CASES = (
    {"name": "medium_code_graph", "symbols": 750, "routes": 90, "edges": 1200},
    {"name": "large_code_graph", "symbols": 5000, "routes": 500, "edges": 9000},
)
DURATION_WARN_MS = 1000
LARGE_COMPRESSION_RATIO_WARN = 0.75


def run_hades_backend_benchmark(cases: list[dict[str, int | str]] | None = None) -> dict[str, Any]:
    selected_cases = cases or list(DEFAULT_CASES)
    results = [_run_case(case) for case in selected_cases]
    warnings = [warning for result in results for warning in result["warnings"]]

    return {
        "schema": "hades.backend_benchmark.v1",
        "status": "warning" if warnings else "passed",
        "case_count": len(results),
        "duration_warn_ms": DURATION_WARN_MS,
        "large_compression_ratio_warn": LARGE_COMPRESSION_RATIO_WARN,
        "warnings": warnings,
        "cases": results,
    }


def _run_case(case: dict[str, int | str]) -> dict[str, Any]:
    name = str(case.get("name") or "code_graph")
    symbol_count = int(case.get("symbols") or 0)
    route_count = int(case.get("routes") or 0)
    edge_count = int(case.get("edges") or 0)
    artifact = _synthetic_code_graph(symbol_count=symbol_count, route_count=route_count, edge_count=edge_count)
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
        "symbol_count": symbol_count,
        "route_count": route_count,
        "edge_count": edge_count,
        "raw_source_included": artifact["raw_source_included"],
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
