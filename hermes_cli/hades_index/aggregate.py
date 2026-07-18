from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from hermes_cli.hades_index.inventory import (
    inventory_coverage,
    merge_inventory_coverage,
)
from hermes_cli.hades_index.lifecycle.model import AdapterResult, IRValidationError


_LOCAL_IR_FAMILIES = (
    "declarations",
    "blocks",
    "structures",
    "call_sites",
    "edge_facts",
    "exception_scopes",
    "terminals",
    "effects",
    "framework_segments",
    "unresolved_facts",
)


def aggregate_adapter_results(
    results: Sequence[AdapterResult],
) -> tuple[AdapterResult, ...]:
    """Validate v2 adapter facts and reject cross-adapter semantic collisions.

    Results remain immutable adapter-owned units because their local references
    are closed within each adapter boundary.  The canonical builder performs
    public-ID deduplication; this seam only proves that a reused local key has
    byte-for-byte identical meaning before aggregation proceeds.
    """

    collected = tuple(results)
    seen: dict[str, tuple[str, object]] = {}
    for result in collected:
        if type(result) is not AdapterResult:
            raise IRValidationError(
                "invalid_adapter_result",
                "lifecycle aggregation accepts exact AdapterResult objects only",
            )
        result.validate()
        for family in _LOCAL_IR_FAMILIES:
            for record in getattr(result, family):
                local_key = record.local_key
                previous = seen.setdefault(local_key, (family, record))
                if previous != (family, record):
                    raise IRValidationError(
                        "semantic_collision",
                        "one adapter-local key has conflicting semantic facts",
                    )
    return collected


def _stable_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def _stable_unique(values: list[Any]) -> list[Any]:
    unique: dict[str, Any] = {}
    for value in values:
        unique.setdefault(_stable_json(value), value)
    return [unique[key] for key in sorted(unique)]


def _round_robin_unique(
    artifacts: list[dict[str, Any]],
    key: str,
    *,
    limit: int,
) -> list[Any]:
    groups = [
        list(artifact.get(key) or [])
        if isinstance(artifact.get(key), list)
        else []
        for artifact in artifacts
    ]
    selected: list[Any] = []
    seen: set[str] = set()
    index = 0
    while len(selected) < max(0, limit) and any(index < len(group) for group in groups):
        for group in groups:
            if index >= len(group):
                continue
            value = group[index]
            fingerprint = _stable_json(value)
            if fingerprint not in seen:
                seen.add(fingerprint)
                selected.append(value)
                if len(selected) >= limit:
                    break
        index += 1
    return selected


def _merge_test_maps(artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    maps = [item for artifact in artifacts if isinstance((item := artifact.get("tests")), dict)]
    files = _stable_unique(
        [
            item
            for test_map in maps
            for item in (test_map.get("files") or [])
            if isinstance(item, dict)
        ]
    )[:500]
    return {
        "schema": "hades.test_map.v1",
        "file_count": len(files),
        "test_count": sum(int(item.get("test_count") or len(item.get("cases") or [])) for item in files),
        "files": files,
        "truncated": any(bool(test_map.get("truncated")) for test_map in maps)
        or sum(len(test_map.get("files") or []) for test_map in maps) > len(files),
        "raw_source_included": False,
    }


def _merge_log_maps(artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    maps = [item for artifact in artifacts if isinstance((item := artifact.get("logs")), dict)]
    events = _stable_unique(
        [
            item
            for log_map in maps
            for item in (log_map.get("events") or [])
            if isinstance(item, dict)
        ]
    )[:500]
    return {
        "schema": "hades.log_map.v1",
        "event_count": len(events),
        "events": events,
        "truncated": any(bool(log_map.get("truncated")) for log_map in maps)
        or sum(len(log_map.get("events") or []) for log_map in maps) > len(events),
        "raw_source_included": False,
    }


def merge_graph_artifacts(
    artifacts: list[dict[str, Any]],
    *,
    root: str,
    max_symbols: int,
    max_edges: int,
) -> dict[str, Any]:
    """Merge bounded per-language artifacts without privileging one adapter."""

    languages = sorted(
        {
            str(artifact.get("language") or "unknown").strip().lower()
            for artifact in artifacts
            if str(artifact.get("language") or "").strip()
        }
    )
    frameworks = sorted(
        {
            str(artifact.get("framework") or "unknown").strip().lower()
            for artifact in artifacts
            if str(artifact.get("framework") or "").strip()
        }
    )
    symbols = _round_robin_unique(artifacts, "symbols", limit=max_symbols)
    edges = _round_robin_unique(artifacts, "edges", limit=max_edges)
    routes = _round_robin_unique(artifacts, "routes", limit=500)
    all_routes = [
        route
        for artifact in artifacts
        for route in (artifact.get("routes") or [])
        if isinstance(route, dict)
    ]
    tables = _stable_unique(
        [
            table
            for artifact in artifacts
            if isinstance(artifact.get("database"), dict)
            for table in (artifact["database"].get("tables") or [])
            if isinstance(table, dict)
        ]
    )[:500]
    omitted = _stable_unique(
        [
            item
            for artifact in artifacts
            for item in (artifact.get("omitted") or [])
            if isinstance(item, dict)
        ]
    )
    dependency_manifests = _stable_unique(
        [
            item
            for artifact in artifacts
            for item in (artifact.get("dependency_manifests") or [])
            if isinstance(item, dict)
        ]
    )
    analysis = {
        str(artifact.get("language") or f"adapter_{index}"): artifact["analysis"]
        for index, artifact in enumerate(artifacts)
        if isinstance(artifact.get("analysis"), dict)
    }
    tests = _merge_test_maps(artifacts)
    all_test_files = [
        test_file
        for artifact in artifacts
        if isinstance(artifact.get("tests"), dict)
        for test_file in (artifact["tests"].get("files") or [])
        if isinstance(test_file, dict)
    ]
    child_inventory_reports = [
        report
        for artifact in artifacts
        for report in (
            artifact.get("_inventory_coverage"),
            (
                artifact["tests"].get("_inventory_coverage")
                if isinstance(artifact.get("tests"), dict)
                else None
            ),
        )
        if isinstance(report, dict)
    ]
    detected_inventory = merge_inventory_coverage(
        inventory_coverage(
            routes_detected=all_routes,
            tests_detected=all_test_files,
        ),
        *child_inventory_reports,
        dimensions=("routes_detected", "tests_detected"),
    )
    retained_inventory = inventory_coverage(
        routes_retained=routes,
        tests_retained=tests.get("files") or [],
    )
    graph = {
        "schema": "hades.code_graph.v1",
        "language": "polyglot" if len(languages) > 1 else (languages[0] if languages else "unknown"),
        "languages": languages or ["unknown"],
        "framework": "polyglot" if len(frameworks) > 1 else (frameworks[0] if frameworks else "unknown"),
        "frameworks": frameworks,
        "root": root,
        "routes": routes,
        "symbols": symbols,
        "edges": edges,
        "database": {"tables": tables},
        "tests": tests,
        "logs": _merge_log_maps(artifacts),
        "dependency_manifests": dependency_manifests,
        "analysis": analysis,
        "summary": (
            f"Collected polyglot graph for {', '.join(languages)}: "
            f"{len(routes)} route(s), {len(symbols)} symbol(s), "
            f"{len(edges)} edge(s)."
        ),
        "omitted": omitted,
        "truncated": any(bool(artifact.get("truncated")) for artifact in artifacts)
        or sum(len(artifact.get("symbols") or []) for artifact in artifacts) > len(symbols)
        or sum(len(artifact.get("edges") or []) for artifact in artifacts) > len(edges)
        or sum(len(artifact.get("routes") or []) for artifact in artifacts) > len(routes),
        "redactions": len(omitted),
        "retention_class": "source_symbols",
        "raw_source_included": False,
        "_inventory_coverage": merge_inventory_coverage(
            detected_inventory,
            retained_inventory,
        ),
    }
    return graph
