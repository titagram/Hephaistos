"""TypeScript/JavaScript code graph indexer, extracted from hades_backend_jobs."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from hermes_cli import hades_backend_jobs as _hades_backend_jobs
from hermes_cli.hades_backend_client import redact_secret
from hermes_cli.hades_backend_jobs import (
    _balanced_end,
    _build_test_map,
    _dependency_manifests,
    _edge_append,
    _io_error_reason,
    _is_test_path,
    _line_number,
    _split_top_level_items,
    _ts_graph_summary,
    TS_IMPORT_RE,
    MAX_LOG_EVENTS,
)
from hermes_cli.hades_index.inventory import inventory_coverage


TS_EXPORT_DECL_RE = re.compile(
    r"\bexport\s+(?:default\s+)?(?:(?:async\s+)?function|class|const|let|var)\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)",
    re.MULTILINE,
)
TS_FUNCTION_RE = re.compile(r"\b(?:async\s+)?function\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*\(", re.MULTILINE)
TS_ARROW_COMPONENT_RE = re.compile(
    r"\b(?:export\s+)?(?:const|let|var)\s+(?P<name>[A-Z][A-Za-z0-9_$]*)\s*=\s*(?:\([^)]*\)|[A-Za-z_$][A-Za-z0-9_$]*)\s*=>",
    re.MULTILINE,
)
TS_CLASS_RE = re.compile(r"\bclass\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)", re.MULTILINE)
TS_LOG_CALL_RE = re.compile(
    r"\b(?P<logger>console|logger|log)\s*\.\s*(?P<level>debug|info|warn|warning|error|exception|critical|log)\s*"
    r"\(\s*(?P<quote>['\"])(?P<message>(?:\\.|(?! (?P=quote)).)*?)(?P=quote)",
    re.MULTILINE | re.DOTALL | re.VERBOSE,
)
EXPRESS_ROUTE_RE = re.compile(
    r"\b(?P<router>app|router)\s*\.\s*(?P<method>get|post|put|patch|delete|options|all|use)\s*"
    r"\(\s*['\"](?P<path>[^'\"]+)['\"]\s*,\s*(?P<handler>[A-Za-z_$][A-Za-z0-9_.$]*)?",
    re.IGNORECASE | re.MULTILINE,
)
DRIZZLE_TABLE_RE = re.compile(
    r"(?:export\s+)?(?:const|let|var)\s+(?P<var>[A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*"
    r"(?P<fn>pgTable|mysqlTable|sqliteTable)\s*\(\s*['\"](?P<table>[^'\"]+)['\"]\s*,\s*\{",
    re.MULTILINE,
)
DRIZZLE_FIELD_RE = re.compile(r"^\s*(?P<field>[A-Za-z_$][A-Za-z0-9_$]*)\s*:\s*(?P<expr>.+)$", re.DOTALL)
DRIZZLE_COLUMN_RE = re.compile(r"(?P<type>[A-Za-z_$][A-Za-z0-9_$]*)\s*\(\s*['\"](?P<column>[^'\"]+)['\"]", re.DOTALL)
DRIZZLE_REFERENCES_RE = re.compile(
    r"\.references\s*\(\s*\(\s*\)\s*=>\s*(?P<table>[A-Za-z_$][A-Za-z0-9_$]*)\.(?P<column>[A-Za-z_$][A-Za-z0-9_$]*)",
    re.DOTALL,
)
PRISMA_MODEL_RE = re.compile(r"^model\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\{(?P<body>.*?)^\}", re.MULTILINE | re.DOTALL)
PRISMA_FIELD_RE = re.compile(r"^\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s+(?P<type>[A-Za-z_][A-Za-z0-9_]*(?:\[\])?\??)(?P<attrs>.*)$", re.MULTILINE)
PRISMA_MAP_RE = re.compile(r"@@map\(\s*['\"](?P<table>[^'\"]+)['\"]\s*\)")
PRISMA_RELATION_RE = re.compile(r"@relation\((?P<body>[^)]*)\)")
PRISMA_LIST_ARG_RE = re.compile(r"\b(?P<name>fields|references)\s*:\s*\[(?P<values>[^\]]+)\]")
PRISMA_SCALAR_TYPES = {"String", "Boolean", "Int", "BigInt", "Float", "Decimal", "DateTime", "Json", "Bytes"}
NEXT_ROUTE_FILE_RE = re.compile(r"(?:^|/)app/(?P<route>.+)/route\.(?:ts|tsx|js|jsx)$")
NEXT_PAGE_FILE_RE = re.compile(r"(?:^|/)app/(?P<route>.+)/page\.(?:ts|tsx|js|jsx)$")
NEXT_HTTP_EXPORT_RE = re.compile(r"\bexport\s+(?:async\s+)?function\s+(?P<method>GET|POST|PUT|PATCH|DELETE|OPTIONS)\s*\(")


def _prisma_table_name(model_name: str, body: str) -> str:
    match = PRISMA_MAP_RE.search(body)
    return match.group("table") if match else model_name


def _drizzle_table_declarations(source: str, rel: str) -> list[dict[str, Any]]:
    declarations: list[dict[str, Any]] = []
    for match in DRIZZLE_TABLE_RE.finditer(source):
        object_start = match.end() - 1
        object_end = _balanced_end(source, object_start, "{", "}")
        if object_end == -1:
            continue
        body = source[object_start + 1 : object_end]
        fields: list[dict[str, Any]] = []
        for item, item_offset in _split_top_level_items(body):
            field_match = DRIZZLE_FIELD_RE.match(item)
            if not field_match:
                continue
            expr = field_match.group("expr").strip()
            column_match = DRIZZLE_COLUMN_RE.search(expr)
            if not column_match:
                continue
            field = {
                "field": field_match.group("field"),
                "name": column_match.group("column"),
                "type": column_match.group("type"),
                "path": rel,
                "line": _line_number(source, object_start + 1 + item_offset),
                "primary_key": ".primaryKey(" in expr,
                "nullable": False if ".notNull(" in expr else None,
                "unique": True if ".unique(" in expr else None,
                "has_default": True if ".default(" in expr else None,
            }
            reference_match = DRIZZLE_REFERENCES_RE.search(expr)
            if reference_match:
                field["references_table_var"] = reference_match.group("table")
                field["references_field"] = reference_match.group("column")
            fields.append({key: value for key, value in field.items() if value is not None})
        declarations.append(
            {
                "var": match.group("var"),
                "table": match.group("table"),
                "factory": match.group("fn"),
                "path": rel,
                "line": _line_number(source, match.start()),
                "fields": fields,
            }
        )
    return declarations


def _drizzle_schema_graph(
    source: str,
    rel: str,
    *,
    max_symbols: int,
    max_edges: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], bool]:
    declarations = _drizzle_table_declarations(source, rel)
    table_by_var = {item["var"]: item["table"] for item in declarations}
    column_by_var_field = {
        (item["var"], field["field"]): field["name"]
        for item in declarations
        for field in item.get("fields", [])
    }
    tables: list[dict[str, Any]] = []
    symbols: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    truncated = False
    for declaration in declarations:
        table_name = str(declaration["table"])
        foreign_keys: list[dict[str, Any]] = []
        if len(symbols) < max_symbols:
            symbols.append(
                {
                    "kind": "model",
                    "name": str(declaration["var"]),
                    "role": "drizzle_table",
                    "path": rel,
                    "line": declaration["line"],
                }
            )
        else:
            truncated = True
        for field in declaration.get("fields", []):
            target_var = field.get("references_table_var")
            target_field = field.get("references_field")
            if not target_var or not target_field:
                continue
            references_table = table_by_var.get(str(target_var), str(target_var))
            references_column = column_by_var_field.get((str(target_var), str(target_field)), str(target_field))
            foreign_keys.append(
                {
                    "table": table_name,
                    "column": field["name"],
                    "references_table": references_table,
                    "references_column": references_column,
                    "path": rel,
                    "line": field["line"],
                }
            )
        tables.append(
            {
                "table": table_name,
                "model": str(declaration["var"]),
                "orm": "drizzle",
                "factory": str(declaration["factory"]),
                "path": rel,
                "line": declaration["line"],
                "columns": [
                    {
                        key: value
                        for key, value in field.items()
                        if key not in {"references_table_var", "references_field"}
                    }
                    for field in declaration.get("fields", [])
                ][:200],
                "foreign_keys": foreign_keys[:100],
            }
        )
        truncated = not _edge_append(
            edges,
            {
                "kind": "model_table",
                "from": str(declaration["var"]),
                "to": f"table:{table_name}",
                "framework": "drizzle",
                "path": rel,
                "line": declaration["line"],
            },
            max_edges=max_edges,
        ) or truncated
        for foreign_key in foreign_keys:
            truncated = not _edge_append(
                edges,
                {
                    "kind": "foreign_key",
                    "from": f"table:{foreign_key['table']}.{foreign_key['column']}",
                    "to": f"table:{foreign_key['references_table']}",
                    "framework": "drizzle",
                    "path": rel,
                    "line": foreign_key.get("line"),
                },
                max_edges=max_edges,
            ) or truncated
    return tables, symbols, edges, truncated


def _prisma_list_arg(relation_body: str, name: str) -> list[str]:
    for match in PRISMA_LIST_ARG_RE.finditer(relation_body or ""):
        if match.group("name") != name:
            continue
        return [item.strip() for item in match.group("values").split(",") if item.strip()]
    return []


def _prisma_model_graph(
    source: str,
    rel: str,
    *,
    max_symbols: int,
    max_edges: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], bool]:
    model_matches = list(PRISMA_MODEL_RE.finditer(source))
    table_by_model = {match.group("name"): _prisma_table_name(match.group("name"), match.group("body")) for match in model_matches}
    symbols: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    truncated = False

    for model_match in model_matches:
        model_name = model_match.group("name")
        body = model_match.group("body")
        table_name = table_by_model[model_name]
        columns: list[dict[str, Any]] = []
        foreign_keys: list[dict[str, Any]] = []
        line = _line_number(source, model_match.start())

        if len(symbols) < max_symbols:
            symbols.append(
                {
                    "kind": "model",
                    "name": model_name,
                    "role": "prisma_model",
                    "path": rel,
                    "line": line,
                }
            )
        else:
            truncated = True

        for field_match in PRISMA_FIELD_RE.finditer(body):
            field_name = field_match.group("name")
            raw_type = field_match.group("type")
            base_type = raw_type.rstrip("?").removesuffix("[]")
            attrs = field_match.group("attrs") or ""
            field_line = _line_number(source, model_match.start("body") + field_match.start())
            if base_type in PRISMA_SCALAR_TYPES:
                column = {
                    "name": field_name,
                    "field": field_name,
                    "type": base_type,
                    "path": rel,
                    "line": field_line,
                    "optional": raw_type.endswith("?"),
                    "list": raw_type.endswith("[]") or raw_type.endswith("[]?"),
                }
                if "@id" in attrs:
                    column["primary_key"] = True
                if "@unique" in attrs:
                    column["unique"] = True
                if "@default" in attrs:
                    column["has_default"] = True
                columns.append(column)
                continue

            relation_match = PRISMA_RELATION_RE.search(attrs)
            if not relation_match:
                continue
            fields = _prisma_list_arg(relation_match.group("body"), "fields")
            references = _prisma_list_arg(relation_match.group("body"), "references")
            target_table = table_by_model.get(base_type, base_type)
            for index, field in enumerate(fields):
                foreign_keys.append(
                    {
                        "table": table_name,
                        "column": field,
                        "references_table": target_table,
                        "references_column": references[index] if index < len(references) else "",
                        "path": rel,
                        "line": field_line,
                    }
                )

        tables.append(
            {
                "table": table_name,
                "model": model_name,
                "orm": "prisma",
                "path": rel,
                "line": line,
                "columns": columns[:200],
                "foreign_keys": foreign_keys[:100],
            }
        )
        truncated = not _edge_append(
            edges,
            {
                "kind": "model_table",
                "from": model_name,
                "to": f"table:{table_name}",
                "framework": "prisma",
                "path": rel,
                "line": line,
            },
            max_edges=max_edges,
        ) or truncated
        for foreign_key in foreign_keys:
            truncated = not _edge_append(
                edges,
                {
                    "kind": "foreign_key",
                    "from": f"table:{foreign_key['table']}.{foreign_key['column']}",
                    "to": f"table:{foreign_key['references_table']}",
                    "framework": "prisma",
                    "path": rel,
                    "line": foreign_key.get("line"),
                },
                max_edges=max_edges,
            ) or truncated
    return tables, symbols, edges, truncated


def _ts_framework(root: Path, files: list[Path], dependency_manifests: list[dict[str, Any]]) -> str:
    packages = {
        str(package)
        for manifest in dependency_manifests
        for package in (manifest.get("packages") or [])
    }
    if "next" in packages or any(NEXT_ROUTE_FILE_RE.search(path.relative_to(root).as_posix()) for path in files):
        return "nextjs"
    if "react" in packages or any(path.suffix.lower() in {".tsx", ".jsx"} for path in files):
        return "react"
    if "express" in packages:
        return "express"
    return "node"


def _route_from_next_path(rel: str) -> str:
    for pattern in (NEXT_ROUTE_FILE_RE, NEXT_PAGE_FILE_RE):
        match = pattern.search(rel)
        if not match:
            continue
        route = match.group("route")
        clean = "/" + route.replace("/(group)", "").replace("index", "").strip("/")
        return clean if clean != "/" else "/"
    return ""


def _append_ts_symbol(
    symbols: list[dict[str, Any]],
    symbol: dict[str, Any],
    *,
    max_symbols: int,
) -> bool:
    if len(symbols) >= max_symbols:
        return False
    symbols.append({key: value for key, value in symbol.items() if value not in ("", None)})
    return True


def _append_ts_log_events(
    source: str,
    rel: str,
    edges: list[dict[str, Any]],
    log_events: list[dict[str, Any]],
    *,
    max_edges: int,
) -> bool:
    truncated = False
    for match in TS_LOG_CALL_RE.finditer(source):
        if len(log_events) >= MAX_LOG_EVENTS:
            truncated = True
            break
        level = match.group("level").lower()
        if level in {"warn", "log"}:
            level = "warning" if level == "warn" else "info"
        message = redact_secret(match.group("message") or "")
        payload = {
            "context": rel,
            "logger": match.group("logger"),
            "level": level,
            "path": rel,
            "line": _line_number(source, match.start()),
            "message_sha256": hashlib.sha256(message.encode("utf-8")).hexdigest() if message else "",
            "message_length": len(message) if message else 0,
        }
        log_event = {key: value for key, value in payload.items() if value not in ("", None, 0)}
        log_id_payload = json.dumps(log_event, sort_keys=True, separators=(",", ":")).encode("utf-8")
        log_id = hashlib.sha256(log_id_payload).hexdigest()[:16]
        log_event = {"id": f"log:{log_id}", **log_event}
        log_events.append(log_event)
        truncated = not _edge_append(
            edges,
            {
                "kind": "emits_log",
                "from": rel,
                "to": log_event["id"],
                "level": log_event.get("level"),
                "logger": log_event.get("logger"),
                "path": rel,
                "line": log_event.get("line"),
            },
            max_edges=max_edges,
        ) or truncated
    return truncated


def build_graph(
    workspace_root: Path,
    candidates: list[Path],
    omitted: list[dict[str, str]],
    *,
    truncated: bool,
    max_symbols: int,
    max_edges: int,
    max_file_bytes: int,
) -> dict[str, Any]:
    ts_files = [path for path in candidates if path.suffix.lower() in {".js", ".jsx", ".ts", ".tsx"}]
    prisma_files = [path for path in candidates if path.suffix.lower() == ".prisma"]
    file_refs = [{"path": path.relative_to(workspace_root).as_posix(), "bytes": path.stat().st_size} for path in candidates if path.is_file()]
    dependency_manifests = _dependency_manifests(workspace_root, file_refs)
    framework = _ts_framework(workspace_root, ts_files, dependency_manifests)
    routes: list[dict[str, str]] = []
    symbols: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    database: dict[str, Any] = {"tables": []}
    log_events: list[dict[str, Any]] = []

    for path in ts_files:
        rel = path.relative_to(workspace_root).as_posix()
        if _is_test_path(rel):
            continue
        try:
            size = path.stat().st_size
        except OSError as exc:
            omitted.append({"path": rel, "reason": _io_error_reason("stat_error", exc)})
            continue
        if size > max_file_bytes:
            omitted.append({"path": rel, "reason": "file_too_large"})
            truncated = True
            continue
        try:
            source, was_truncated, _digest = _hades_backend_jobs._read_text_bounded(path, max_file_bytes)
            if was_truncated:
                omitted.append({"path": rel, "reason": "file_too_large"})
                truncated = True
                continue
        except OSError as exc:
            omitted.append({"path": rel, "reason": _io_error_reason("read_error", exc)})
            continue

        route_path = _route_from_next_path(rel)
        if route_path:
            for match in NEXT_HTTP_EXPORT_RE.finditer(source):
                routes.append(
                    {
                        "framework": "nextjs",
                        "method": match.group("method"),
                        "path": route_path,
                        "handler": f"{rel}:{match.group('method')}",
                        "source_path": rel,
                    }
                )
            if rel.endswith(("/page.tsx", "/page.jsx", "/page.ts", "/page.js")):
                routes.append(
                    {
                        "framework": "nextjs",
                        "method": "PAGE",
                        "path": route_path,
                        "handler": rel,
                        "source_path": rel,
                    }
                )

        for match in EXPRESS_ROUTE_RE.finditer(source):
            routes.append(
                {
                    "framework": "express",
                    "method": match.group("method").upper(),
                    "path": match.group("path"),
                    "handler": match.group("handler") or "",
                    "source_path": rel,
                }
            )

        truncated = _append_ts_log_events(source, rel, edges, log_events, max_edges=max_edges) or truncated
        for match in TS_IMPORT_RE.finditer(source):
            truncated = not _edge_append(
                edges,
                {
                    "kind": "imports",
                    "from": rel,
                    "to": match.group("target"),
                    "path": rel,
                    "line": _line_number(source, match.start()),
                },
                max_edges=max_edges,
            ) or truncated

        for kind, pattern in (
            ("export", TS_EXPORT_DECL_RE),
            ("function", TS_FUNCTION_RE),
            ("component", TS_ARROW_COMPONENT_RE),
            ("class", TS_CLASS_RE),
        ):
            for match in pattern.finditer(source):
                name = match.group("name")
                symbol_kind = "component" if kind == "component" or (path.suffix.lower() in {".tsx", ".jsx"} and name[:1].isupper()) else kind
                truncated = not _append_ts_symbol(
                    symbols,
                    {
                        "kind": symbol_kind,
                        "name": name,
                        "path": rel,
                        "line": _line_number(source, match.start()),
                        "framework": framework,
                    },
                    max_symbols=max_symbols,
                ) or truncated
                if len(symbols) >= max_symbols:
                    break
            if len(symbols) >= max_symbols:
                break

        drizzle_tables, drizzle_symbols, drizzle_edges, drizzle_truncated = _drizzle_schema_graph(
            source,
            rel,
            max_symbols=max(0, max_symbols - len(symbols)),
            max_edges=max(0, max_edges - len(edges)),
        )
        database["tables"].extend(drizzle_tables)
        symbols.extend(drizzle_symbols)
        edges.extend(drizzle_edges)
        truncated = truncated or drizzle_truncated

    for path in prisma_files:
        rel = path.relative_to(workspace_root).as_posix()
        try:
            size = path.stat().st_size
        except OSError as exc:
            omitted.append({"path": rel, "reason": _io_error_reason("stat_error", exc)})
            continue
        if size > max_file_bytes:
            omitted.append({"path": rel, "reason": "file_too_large"})
            truncated = True
            continue
        try:
            source, was_truncated, _digest = _hades_backend_jobs._read_text_bounded(path, max_file_bytes)
            if was_truncated:
                omitted.append({"path": rel, "reason": "file_too_large"})
                truncated = True
                continue
        except OSError as exc:
            omitted.append({"path": rel, "reason": _io_error_reason("read_error", exc)})
            continue
        prisma_tables, prisma_symbols, prisma_edges, prisma_truncated = _prisma_model_graph(
            source,
            rel,
            max_symbols=max(0, max_symbols - len(symbols)),
            max_edges=max(0, max_edges - len(edges)),
        )
        database["tables"].extend(prisma_tables)
        symbols.extend(prisma_symbols)
        edges.extend(prisma_edges)
        truncated = truncated or prisma_truncated

    if database["tables"] and framework == "node" and not routes:
        table_orms = {str(item.get("orm") or item.get("source") or "") for item in database["tables"]}
        if table_orms == {"drizzle"}:
            framework = "drizzle"
        elif table_orms == {"prisma"}:
            framework = "prisma"
        elif table_orms == {"sql"}:
            framework = "sql"
    if prisma_files and not ts_files:
        framework = "prisma"
    tests, tests_truncated = _build_test_map(
        workspace_root,
        candidates,
        routes,
        symbols,
        edges,
        max_edges=max_edges,
        max_file_bytes=max_file_bytes,
    )
    truncated = truncated or tests_truncated
    logs = {
        "schema": "hades.log_map.v1",
        "event_count": len(log_events),
        "events": log_events[:MAX_LOG_EVENTS],
        "truncated": len(log_events) > MAX_LOG_EVENTS,
        "raw_source_included": False,
    }
    graph_database = {**database, "tables": database["tables"][:500]}
    language = "prisma"
    if ts_files:
        language = "typescript" if any(path.suffix.lower() in {".ts", ".tsx"} for path in ts_files) else "javascript"
    retained_routes = routes[:500]
    graph = {
        "schema": "hades.code_graph.v1",
        "language": language,
        "framework": framework,
        "root": workspace_root.name,
        "routes": retained_routes,
        "symbols": symbols,
        "edges": edges,
        "database": graph_database,
        "tests": tests,
        "logs": logs,
        "dependency_manifests": dependency_manifests,
        "summary": "",
        "omitted": omitted,
        "truncated": truncated
        or len(symbols) >= max_symbols
        or len(edges) >= max_edges
        or len(routes) > 500
        or len(database["tables"]) > 500,
        "redactions": len(omitted),
        "retention_class": "source_symbols",
        "raw_source_included": False,
        "_inventory_coverage": inventory_coverage(
            routes_detected=routes,
            routes_retained=retained_routes,
        ),
    }
    graph["summary"] = _ts_graph_summary(
        graph["routes"],
        symbols,
        edges,
        framework=framework,
        database=graph_database,
        tests=tests,
        logs=logs,
    )
    return graph

