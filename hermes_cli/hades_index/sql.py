"""SQL schema code graph indexer, extracted from hades_backend_jobs."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Sequence

from hermes_cli import hades_backend_jobs as _hades_backend_jobs
from hermes_cli.hades_backend_jobs import (
    _edge_append,
    _io_error_reason,
    _line_number,
    _ts_graph_summary,
)
from hermes_cli.hades_index.lifecycle.entrypoints import (
    EntrypointExtraction,
    sql_entrypoint_extraction,
)
from hermes_cli.hades_index.lifecycle.frameworks import FrameworkAdapterRegistry
from hermes_cli.hades_index.lifecycle.model import ExtractionContext
from hermes_cli.hades_index.tree_sitter_adapter import SyntaxIR


SQL_CREATE_TABLE_RE = re.compile(
    r"\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?P<table>[`\"A-Za-z0-9_.]+)\s*\((?P<body>.*?)\)\s*;",
    re.IGNORECASE | re.DOTALL,
)


def extract_lifecycle_entrypoints(
    context: ExtractionContext,
    syntax: Sequence[SyntaxIR],
    *,
    registry: FrameworkAdapterRegistry | None = None,
) -> EntrypointExtraction:
    """Emit SQL's explicit not-applicable executable capability records only."""

    if registry is not None:
        raise ValueError("SQL does not accept framework lifecycle adapters")
    del context
    return sql_entrypoint_extraction(syntax)
SQL_INLINE_REFERENCE_RE = re.compile(
    r"\bREFERENCES\s+(?P<table>[`\"A-Za-z0-9_.]+)\s*\(\s*(?P<column>[`\"A-Za-z0-9_]+)\s*\)",
    re.IGNORECASE,
)
SQL_TABLE_FOREIGN_KEY_RE = re.compile(
    r"\bFOREIGN\s+KEY\s*\(\s*(?P<column>[`\"A-Za-z0-9_]+)\s*\)\s*REFERENCES\s+"
    r"(?P<table>[`\"A-Za-z0-9_.]+)\s*\(\s*(?P<ref_column>[`\"A-Za-z0-9_]+)\s*\)",
    re.IGNORECASE,
)


def _sql_identifier(raw: str) -> str:
    return str(raw or "").strip().strip("`\"")


def _sql_split_items(body: str) -> list[tuple[str, int]]:
    items: list[tuple[str, int]] = []
    start = 0
    depth = 0
    quote = ""
    for index, char in enumerate(body):
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in {"'", '"', "`"}:
            quote = char
            continue
        if char == "(":
            depth += 1
        elif char == ")" and depth > 0:
            depth -= 1
        elif char == "," and depth == 0:
            raw = body[start:index]
            items.append((raw.strip(), start + len(raw) - len(raw.lstrip())))
            start = index + 1
    raw_tail = body[start:]
    tail = raw_tail.strip()
    if tail:
        items.append((tail, start + len(raw_tail) - len(raw_tail.lstrip())))
    return items


def _sql_schema_graph(
    source: str,
    rel: str,
    *,
    max_symbols: int,
    max_edges: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], bool]:
    tables: list[dict[str, Any]] = []
    symbols: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    truncated = False
    for match in SQL_CREATE_TABLE_RE.finditer(source):
        table_name = _sql_identifier(match.group("table").split(".")[-1])
        body = match.group("body")
        columns: list[dict[str, Any]] = []
        foreign_keys: list[dict[str, Any]] = []
        line = _line_number(source, match.start())
        if len(symbols) < max_symbols:
            symbols.append({"kind": "table", "name": f"table:{table_name}", "table": table_name, "path": rel, "line": line})
        else:
            truncated = True

        for item, body_offset in _sql_split_items(body):
            upper = item.upper()
            item_line = _line_number(source, match.start("body") + body_offset)
            table_fk = SQL_TABLE_FOREIGN_KEY_RE.search(item)
            if table_fk:
                foreign_keys.append(
                    {
                        "table": table_name,
                        "column": _sql_identifier(table_fk.group("column")),
                        "references_table": _sql_identifier(table_fk.group("table").split(".")[-1]),
                        "references_column": _sql_identifier(table_fk.group("ref_column")),
                        "path": rel,
                        "line": item_line,
                    }
                )
                continue
            if upper.startswith(("CONSTRAINT ", "PRIMARY KEY", "UNIQUE ", "KEY ", "INDEX ", "CHECK ")):
                continue
            tokens = item.split()
            if len(tokens) < 2:
                continue
            column_name = _sql_identifier(tokens[0])
            column_type = tokens[1].strip(",")
            column = {
                "name": column_name,
                "type": column_type,
                "path": rel,
                "line": item_line,
            }
            if "PRIMARY KEY" in upper:
                column["primary_key"] = True
            if "NOT NULL" in upper:
                column["nullable"] = False
            if " UNIQUE" in f" {upper}":
                column["unique"] = True
            columns.append(column)
            inline_fk = SQL_INLINE_REFERENCE_RE.search(item)
            if inline_fk:
                foreign_keys.append(
                    {
                        "table": table_name,
                        "column": column_name,
                        "references_table": _sql_identifier(inline_fk.group("table").split(".")[-1]),
                        "references_column": _sql_identifier(inline_fk.group("column")),
                        "path": rel,
                        "line": item_line,
                    }
                )
        tables.append(
            {
                "table": table_name,
                "source": "sql",
                "path": rel,
                "line": line,
                "columns": columns[:200],
                "foreign_keys": foreign_keys[:100],
            }
        )
        truncated = not _edge_append(
            edges,
            {
                "kind": "schema_table",
                "from": rel,
                "to": f"table:{table_name}",
                "framework": "sql",
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
                    "framework": "sql",
                    "path": rel,
                    "line": foreign_key.get("line"),
                },
                max_edges=max_edges,
            ) or truncated
    return tables, symbols, edges, truncated


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
    sql_files = [path for path in candidates if path.suffix.lower() == ".sql"]
    symbols: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    database: dict[str, Any] = {"tables": []}
    for path in sql_files:
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
        tables, sql_symbols, sql_edges, sql_truncated = _sql_schema_graph(
            source,
            rel,
            max_symbols=max(0, max_symbols - len(symbols)),
            max_edges=max(0, max_edges - len(edges)),
        )
        database["tables"].extend(tables)
        symbols.extend(sql_symbols)
        edges.extend(sql_edges)
        truncated = truncated or sql_truncated
    graph_database = {**database, "tables": database["tables"][:500]}
    graph = {
        "schema": "hades.code_graph.v1",
        "language": "sql",
        "framework": "sql",
        "root": workspace_root.name,
        "routes": [],
        "symbols": symbols,
        "edges": edges,
        "database": graph_database,
        "summary": "",
        "omitted": omitted,
        "truncated": truncated or len(symbols) >= max_symbols or len(edges) >= max_edges or len(database["tables"]) > 500,
        "redactions": len(omitted),
        "retention_class": "source_symbols",
        "raw_source_included": False,
    }
    graph["summary"] = _ts_graph_summary([], symbols, edges, framework="sql", database=graph_database)
    return graph

