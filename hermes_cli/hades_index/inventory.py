from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import Any


_ROUTE_FIELDS = (
    "framework",
    "method",
    "http_method",
    "verb",
    "uri",
    "route",
    "route_path",
    "name",
    "handler",
    "defined_handler",
    "inherited",
    "path",
    "source_path",
    "file",
    "line",
)
_TEST_FIELDS = (
    "framework",
    "name",
    "class",
    "class_name",
    "test_class",
    "path",
    "source_path",
    "file",
    "line",
    "cases",
    "target_candidates",
)


def _text(value: object) -> str:
    return str(value).strip() if value not in (None, "") else ""


def _kind(item: dict[str, Any]) -> str:
    return _text(item.get("kind") or item.get("type")).lower()


def _route_name(item: dict[str, Any]) -> str:
    name = _text(item.get("name"))
    if name.lower().startswith("route:"):
        return name.split(":", 1)[1]
    return name


def _route_identity(item: dict[str, Any]) -> tuple[str, ...] | None:
    name = _route_name(item)
    if name:
        return ("name", name)
    method = _text(
        item.get("method") or item.get("http_method") or item.get("verb")
    ).upper()
    path = _text(item.get("path"))
    uri = _text(
        item.get("uri")
        or item.get("route")
        or item.get("route_path")
        or (path if path.startswith("/") else "")
    )
    handler = _text(item.get("handler"))
    if not any((method, uri, handler)):
        return None
    return ("signature", method, uri, handler)


def _test_name(item: dict[str, Any]) -> str:
    for key in ("name", "test_class", "class_name", "class"):
        value = _text(item.get(key))
        if value:
            return value
    path = _text(item.get("path") or item.get("source_path") or item.get("file"))
    return PurePosixPath(path.replace("\\", "/")).stem if path else ""


def _normalized_test_path(item: dict[str, Any]) -> str:
    raw = _text(item.get("path") or item.get("source_path") or item.get("file"))
    if not raw:
        return ""
    parts: list[str] = []
    for part in PurePosixPath(raw.replace("\\", "/")).parts:
        if part in {"", ".", "/"}:
            continue
        if part == ".." and parts and parts[-1] != "..":
            parts.pop()
        else:
            parts.append(part)
    return "/".join(parts)


def _test_identity(item: dict[str, Any]) -> tuple[str, ...] | None:
    name = _test_name(item)
    if not name:
        return None
    path = _normalized_test_path(item)
    return ("path", path, name) if path else ("name", name)


def _unique_identity_count(values: object, *, kind: str) -> int:
    if not isinstance(values, list):
        return 0
    identity_fn = _route_identity if kind == "route" else _test_identity
    return len(
        {
            identity
            for value in values
            if isinstance(value, dict)
            if (identity := identity_fn(value)) is not None
        }
    )


def inventory_coverage(
    *,
    routes_detected: object = None,
    routes_retained: object = None,
    tests_detected: object = None,
    tests_retained: object = None,
) -> dict[str, int]:
    """Count inventory coverage by canonical identity, never by raw records."""

    return {
        "routes_detected": _unique_identity_count(
            routes_detected, kind="route"
        ),
        "routes_retained": _unique_identity_count(
            routes_retained, kind="route"
        ),
        "tests_detected": _unique_identity_count(tests_detected, kind="test"),
        "tests_retained": _unique_identity_count(tests_retained, kind="test"),
    }


def merge_inventory_coverage(*reports: object) -> dict[str, int]:
    """Merge partial private reports without double-counting shared inventory."""

    keys = (
        "routes_detected",
        "routes_retained",
        "tests_detected",
        "tests_retained",
    )
    return {
        key: max(
            (
                int(report.get(key) or 0)
                for report in reports
                if isinstance(report, dict)
            ),
            default=0,
        )
        for key in keys
    }


def _copy_fields(item: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {
        key: item[key]
        for key in fields
        if key in item and item[key] not in (None, "", [], {})
    }


def _merge_missing(
    existing: dict[str, Any],
    incoming: dict[str, Any],
) -> None:
    for key, value in incoming.items():
        if key not in existing or existing[key] in (None, "", [], {}):
            existing[key] = value


def _merge_test_fields(existing: dict[str, Any], incoming: dict[str, Any]) -> None:
    for key in ("cases", "target_candidates"):
        values = [
            *(
                existing.get(key)
                if isinstance(existing.get(key), list)
                else []
            ),
            *(
                incoming.get(key)
                if isinstance(incoming.get(key), list)
                else []
            ),
        ]
        if values:
            unique = {
                json.dumps(value, sort_keys=True, ensure_ascii=False, default=str): value
                for value in values
            }
            existing[key] = [unique[fingerprint] for fingerprint in sorted(unique)]
    _merge_missing(existing, incoming)


def _promote_routes(
    declarations: list[Any],
    routes: object,
) -> tuple[int, int, int]:
    records = [item for item in routes if isinstance(item, dict)] if isinstance(routes, list) else []
    existing_by_identity = {
        identity: declaration
        for declaration in declarations
        if isinstance(declaration, dict)
        if _kind(declaration) in {"route", "endpoint", "http_endpoint"}
        if (identity := _route_identity(declaration)) is not None
    }
    promoted = 0
    merged = 0
    for record in records:
        identity = _route_identity(record)
        if identity is None:
            continue
        route = {"kind": "route", **_copy_fields(record, _ROUTE_FIELDS)}
        normalized_name = _route_name(record)
        if normalized_name:
            route["name"] = normalized_name
        method = _text(
            record.get("method")
            or record.get("http_method")
            or record.get("verb")
        )
        if method:
            route["method"] = method.upper()
        path = _text(record.get("path"))
        uri = _text(
            record.get("uri")
            or record.get("route")
            or record.get("route_path")
            or (path if path.startswith("/") else "")
        )
        if uri:
            route["uri"] = uri
        existing = existing_by_identity.get(identity)
        if existing is None:
            declarations.append(route)
            existing_by_identity[identity] = route
            promoted += 1
            continue
        if normalized_name and _route_name(existing) == normalized_name:
            existing["name"] = normalized_name
        _merge_missing(existing, route)
        merged += 1
    return len(records), promoted, merged


def _promote_tests(
    declarations: list[Any],
    tests: object,
) -> tuple[int, int, int]:
    records = [item for item in tests if isinstance(item, dict)] if isinstance(tests, list) else []
    existing_by_identity = {
        identity: declaration
        for declaration in declarations
        if isinstance(declaration, dict)
        if _kind(declaration) in {"test", "test_case", "test_class"}
        if (identity := _test_identity(declaration)) is not None
    }
    promoted = 0
    merged = 0
    for record in records:
        name = _test_name(record)
        identity = _test_identity(record)
        if not name or identity is None:
            continue
        test = {
            "kind": "test",
            "name": name,
            **_copy_fields(record, _TEST_FIELDS),
        }
        test["name"] = name
        normalized_path = _normalized_test_path(record)
        if normalized_path:
            for key in ("path", "source_path", "file"):
                if key in test:
                    test[key] = normalized_path
                    break
        existing = existing_by_identity.get(identity)
        if existing is None:
            declarations.append(test)
            existing_by_identity[identity] = test
            promoted += 1
            continue
        _merge_test_fields(existing, test)
        merged += 1
    return len(records), promoted, merged


def promote_graph_inventories(graph: dict[str, Any]) -> dict[str, dict[str, int]]:
    """Promote uniform route and test inventories to first-class declarations.

    Language adapters remain responsible for extracting framework semantics.
    This shared boundary only consumes their uniform inventories, so PHP,
    Python, TypeScript, and future adapters receive identical canonical graph
    behavior.
    """

    declarations_key = "symbols" if isinstance(graph.get("symbols"), list) else "nodes"
    raw_declarations = graph.get(declarations_key)
    declarations = (
        [dict(item) if isinstance(item, dict) else item for item in raw_declarations]
        if isinstance(raw_declarations, list)
        else []
    )

    route_detected, route_promoted, route_merged = _promote_routes(
        declarations,
        graph.get("routes"),
    )
    tests = graph.get("tests")
    test_files = tests.get("files") if isinstance(tests, dict) else []
    test_detected, test_promoted, test_merged = _promote_tests(
        declarations,
        test_files,
    )
    # Keep adapter evidence byte-compatible. Canonicalization consumes this
    # private working collection and removes it before returning the artifact.
    graph["_canonical_declarations"] = declarations
    return {
        "route_inventory": {
            "detected": route_detected,
            "promoted": route_promoted,
            "merged": route_merged,
        },
        "test_inventory": {
            "detected": test_detected,
            "promoted": test_promoted,
            "merged": test_merged,
        },
    }
