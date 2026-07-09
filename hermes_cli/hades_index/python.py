"""Python code graph indexer, extracted from hades_backend_jobs."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import Any

from hermes_cli import hades_backend_jobs as _hades_backend_jobs
from hermes_cli.hades_backend_client import redact_secret
from hermes_cli.hades_backend_jobs import (
    _build_test_map,
    _edge_append,
    _io_error_reason,
    _is_test_path,
    _join_url_path,
    _snake_name,
    MAX_LOG_EVENTS,
)


PY_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "api_route", "route"}
PY_DJANGO_ROUTE_FUNCS = {"path", "re_path"}
PY_DJANGO_RELATION_FIELDS = {"ForeignKey", "OneToOneField", "ManyToManyField"}
PY_SQLALCHEMY_COLUMN_CALLS = {"Column", "mapped_column"}
PY_LOG_LEVELS = {"debug", "info", "warning", "warn", "error", "exception", "critical"}


def _py_graph_summary(
    routes: list[dict[str, str]],
    symbols: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    framework: str,
    database: dict[str, Any] | None = None,
    tests: dict[str, Any] | None = None,
    logs: dict[str, Any] | None = None,
) -> str:
    kind_counts: dict[str, int] = {}
    for symbol in symbols:
        kind = str(symbol.get("kind") or "symbol")
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
    kinds = ", ".join(f"{kind}:{count}" for kind, count in sorted(kind_counts.items())[:8])
    table_count = len((database or {}).get("tables") or [])
    test_count = int((tests or {}).get("file_count") or 0)
    log_count = int((logs or {}).get("event_count") or 0)
    return f"Code graph; framework:{framework}; routes:{len(routes)}; symbols:{len(symbols)}; edges:{len(edges)}; tables:{table_count}; tests:{test_count}; logs:{log_count}; {kinds or 'symbols:none'}"


def _py_dotted_name(node: ast.AST | None) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _py_dotted_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    if isinstance(node, ast.Call):
        return _py_dotted_name(node.func)
    return ""


def _py_string(node: ast.AST | None) -> str:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else ""


def _py_keyword_string(node: ast.Call, name: str) -> str:
    for keyword in node.keywords:
        if keyword.arg == name:
            return _py_string(keyword.value)
    return ""


def _py_keyword_bool(node: ast.Call, name: str) -> bool | None:
    for keyword in node.keywords:
        if keyword.arg == name and isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, bool):
            return keyword.value.value
    return None


def _py_keyword_int(node: ast.Call, name: str) -> int | None:
    for keyword in node.keywords:
        if keyword.arg == name and isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, int):
            return keyword.value.value
    return None


def _py_route_id(route: dict[str, Any]) -> str:
    return str(route.get("name") or f"{route.get('method', '')} {route.get('path', '')}".strip())


def _py_import_target(module: str, name: str, level: int = 0) -> str:
    prefix = "." * max(0, level)
    if module and name:
        return f"{prefix}{module}.{name}"
    return f"{prefix}{module or name}".strip(".") or prefix


def _py_import_aliases_and_edges(
    tree: ast.AST,
    rel: str,
    edges: list[dict[str, Any]],
    *,
    max_edges: int,
) -> tuple[dict[str, str], bool]:
    aliases: dict[str, str] = {}
    truncated = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                target = alias.name
                local = alias.asname or alias.name.split(".", 1)[0]
                aliases[local] = target
                truncated = not _edge_append(
                    edges,
                    {"kind": "imports", "from": rel, "to": target, "path": rel, "line": node.lineno},
                    max_edges=max_edges,
                ) or truncated
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                target = _py_import_target(module, alias.name, node.level)
                local = alias.asname or alias.name
                aliases[local] = target
                truncated = not _edge_append(
                    edges,
                    {"kind": "imports", "from": rel, "to": target, "path": rel, "line": node.lineno},
                    max_edges=max_edges,
                ) or truncated
    return aliases, truncated


def _py_resolve_call_name(call_name: str, imports: dict[str, str]) -> str:
    if not call_name:
        return ""
    head, separator, tail = call_name.partition(".")
    target = imports.get(head)
    if not target:
        return call_name
    return f"{target}{separator}{tail}" if separator else target


def _py_log_event_from_call(
    call: ast.Call,
    *,
    call_name: str,
    context: str,
    rel: str,
) -> dict[str, Any] | None:
    parts = call_name.split(".")
    level = parts[-1].lower() if parts else ""
    if level not in PY_LOG_LEVELS:
        return None
    logger = ".".join(parts[:-1]) or "logger"
    if not (
        logger == "logging"
        or logger.endswith(".logging")
        or logger.endswith(".logger")
        or logger in {"logger", "log", "self.logger"}
    ):
        return None
    message = ""
    if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
        message = redact_secret(call.args[0].value)
    payload = {
        "context": context,
        "logger": logger,
        "level": "warning" if level == "warn" else level,
        "path": rel,
        "line": getattr(call, "lineno", 0),
        "message_sha256": hashlib.sha256(message.encode("utf-8")).hexdigest() if message else "",
        "message_length": len(message) if message else 0,
    }
    return {key: value for key, value in payload.items() if value not in ("", None, 0)}


def _py_callable_contexts(tree: ast.AST) -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    contexts: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []
    body = getattr(tree, "body", [])
    for item in body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            contexts.append((item.name, item))
        elif isinstance(item, ast.ClassDef):
            for child in item.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    contexts.append((f"{item.name}.{child.name}", child))
    return contexts


def _append_python_call_edges(
    tree: ast.AST,
    rel: str,
    imports: dict[str, str],
    edges: list[dict[str, Any]],
    log_events: list[dict[str, Any]],
    *,
    max_edges: int,
) -> bool:
    truncated = False
    for context, node in _py_callable_contexts(tree):
        seen_calls: set[tuple[str, int]] = set()
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            call_name = _py_resolve_call_name(_py_dotted_name(child.func), imports)
            if not call_name or call_name in {context, "super"}:
                continue
            line = getattr(child, "lineno", getattr(node, "lineno", 0))
            key = (call_name, line)
            if key in seen_calls:
                continue
            seen_calls.add(key)
            truncated = not _edge_append(
                edges,
                {
                    "kind": "calls",
                    "from": context,
                    "to": call_name,
                    "path": rel,
                    "line": line,
                },
                max_edges=max_edges,
            ) or truncated
            log_event = _py_log_event_from_call(child, call_name=call_name, context=context, rel=rel)
            if log_event is None:
                continue
            if len(log_events) >= MAX_LOG_EVENTS:
                truncated = True
                continue
            log_id_payload = json.dumps(log_event, sort_keys=True, separators=(",", ":")).encode("utf-8")
            log_id = hashlib.sha256(log_id_payload).hexdigest()[:16]
            log_event = {"id": f"log:{log_id}", **log_event}
            log_events.append(log_event)
            truncated = not _edge_append(
                edges,
                {
                    "kind": "emits_log",
                    "from": context,
                    "to": log_event["id"],
                    "level": log_event.get("level"),
                    "logger": log_event.get("logger"),
                    "path": rel,
                    "line": log_event.get("line"),
                },
                max_edges=max_edges,
            ) or truncated
    return truncated


def _py_app_label(rel: str) -> str:
    parts = rel.split("/")
    if "models" in parts:
        index = parts.index("models")
        if index > 0:
            return parts[index - 1]
    if rel.endswith("/models.py") and len(parts) >= 2:
        return parts[-2]
    return parts[0] if parts else "app"


def _py_django_model_base(node: ast.ClassDef) -> bool:
    for base in node.bases:
        base_name = _py_dotted_name(base)
        if base_name == "Model" or base_name.endswith(".Model"):
            return True
    return False


def _py_django_meta_table(node: ast.ClassDef) -> str:
    for item in node.body:
        if not isinstance(item, ast.ClassDef) or item.name != "Meta":
            continue
        for meta_item in item.body:
            if not isinstance(meta_item, ast.Assign):
                continue
            if not any(isinstance(target, ast.Name) and target.id == "db_table" for target in meta_item.targets):
                continue
            table = _py_string(meta_item.value)
            if table:
                return table
    return ""


def _py_django_relation_target(call: ast.Call) -> str:
    if not call.args:
        return ""
    target = call.args[0]
    if isinstance(target, ast.Constant) and isinstance(target.value, str):
        return target.value
    return _py_dotted_name(target)


def _py_django_target_table(target: str, app_label: str, current_table: str, model_tables: dict[str, str]) -> str:
    if not target:
        return ""
    if target == "self":
        return current_table
    clean = target.strip("'\"")
    model_name = clean.rsplit(".", 1)[-1]
    if model_name in model_tables:
        return model_tables[model_name]
    if "." in clean and not clean.startswith("settings."):
        app, model = clean.rsplit(".", 1)
        return f"{app}_{_snake_name(model)}"
    if clean.startswith("settings."):
        return f"setting:{clean}"
    return f"{app_label}_{_snake_name(clean.split('.')[-1])}"


def _py_django_model_table(node: ast.ClassDef, rel: str) -> tuple[str, str]:
    app_label = _py_app_label(rel)
    return _py_django_meta_table(node) or f"{app_label}_{_snake_name(node.name)}", app_label


def _py_django_model_fields(
    node: ast.ClassDef,
    table: str,
    app_label: str,
    rel: str,
    model_tables: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    columns: list[dict[str, Any]] = []
    foreign_keys: list[dict[str, Any]] = []
    for item in node.body:
        value: ast.AST | None = None
        field_name = ""
        if isinstance(item, ast.Assign) and len(item.targets) == 1 and isinstance(item.targets[0], ast.Name):
            field_name = item.targets[0].id
            value = item.value
        elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            field_name = item.target.id
            value = item.value
        if not field_name or not isinstance(value, ast.Call):
            continue
        field_type = _py_dotted_name(value.func).split(".")[-1]
        if not (field_type.endswith("Field") or field_type in PY_DJANGO_RELATION_FIELDS):
            continue
        relation = field_type in PY_DJANGO_RELATION_FIELDS
        column_name = f"{field_name}_id" if relation and field_type != "ManyToManyField" else field_name
        column = {
            "name": column_name,
            "field": field_name,
            "type": field_type,
            "path": rel,
            "line": getattr(item, "lineno", getattr(value, "lineno", 0)),
        }
        for keyword in ("null", "blank", "unique", "db_index", "primary_key"):
            keyword_value = _py_keyword_bool(value, keyword)
            if keyword_value is not None:
                column[keyword] = keyword_value
        max_length = _py_keyword_int(value, "max_length")
        if max_length is not None:
            column["max_length"] = max_length
        target = _py_django_relation_target(value) if relation else ""
        if target:
            column["relation_model"] = target
        columns.append(column)
        references_table = _py_django_target_table(target, app_label, table, model_tables)
        if references_table and field_type != "ManyToManyField":
            foreign_keys.append(
                {
                    "table": table,
                    "column": column_name,
                    "references_table": references_table,
                    "path": rel,
                    "line": column["line"],
                }
            )
    return columns, foreign_keys


def _py_assign_name(item: ast.AST) -> str:
    if isinstance(item, ast.Assign) and len(item.targets) == 1 and isinstance(item.targets[0], ast.Name):
        return item.targets[0].id
    if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
        return item.target.id
    return ""


def _py_assign_value(item: ast.AST) -> ast.AST | None:
    if isinstance(item, ast.Assign):
        return item.value
    if isinstance(item, ast.AnnAssign):
        return item.value
    return None


def _py_sqlalchemy_table_name(node: ast.ClassDef) -> str:
    for item in node.body:
        if not isinstance(item, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "__tablename__" for target in item.targets):
            continue
        table = _py_string(item.value)
        if table:
            return table
    return ""


def _py_sqlalchemy_column_type(arg: ast.AST | None) -> str:
    if arg is None:
        return ""
    if isinstance(arg, ast.Call):
        return _py_dotted_name(arg.func).split(".")[-1]
    return _py_dotted_name(arg).split(".")[-1]


def _py_sqlalchemy_foreign_key(call: ast.Call) -> tuple[str, str]:
    for arg in call.args:
        if not isinstance(arg, ast.Call) or _py_dotted_name(arg.func).split(".")[-1] != "ForeignKey" or not arg.args:
            continue
        target = _py_string(arg.args[0])
        if not target:
            continue
        if "." in target:
            table, column = target.split(".", 1)
            return table, column
        return target, ""
    return "", ""


def _py_sqlalchemy_column(field_name: str, value: ast.AST | None, rel: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not field_name or not isinstance(value, ast.Call):
        return None, None
    call_name = _py_dotted_name(value.func).split(".")[-1]
    if call_name not in PY_SQLALCHEMY_COLUMN_CALLS:
        return None, None

    args = list(value.args)
    column_name = field_name
    type_arg: ast.AST | None = None
    if args and isinstance(args[0], ast.Constant) and isinstance(args[0].value, str):
        column_name = args[0].value
        type_arg = args[1] if len(args) > 1 else None
    elif args:
        type_arg = args[0]

    column = {
        "name": column_name,
        "field": field_name,
        "type": _py_sqlalchemy_column_type(type_arg),
        "path": rel,
        "line": getattr(value, "lineno", 0),
    }
    for keyword in ("nullable", "unique", "index", "primary_key"):
        keyword_value = _py_keyword_bool(value, keyword)
        if keyword_value is not None:
            column[keyword] = keyword_value

    ref_table, ref_column = _py_sqlalchemy_foreign_key(value)
    foreign_key = None
    if ref_table:
        foreign_key = {
            "column": column_name,
            "references_table": ref_table,
            "references_column": ref_column,
            "path": rel,
            "line": column["line"],
        }
    return column, foreign_key


def _py_sqlalchemy_model_fields(node: ast.ClassDef, table: str, rel: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    columns: list[dict[str, Any]] = []
    foreign_keys: list[dict[str, Any]] = []
    for item in node.body:
        field_name = _py_assign_name(item)
        column, foreign_key = _py_sqlalchemy_column(field_name, _py_assign_value(item), rel)
        if column is None:
            continue
        columns.append(column)
        if foreign_key is not None:
            foreign_key["table"] = table
            foreign_keys.append(foreign_key)
    return columns, foreign_keys


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
    symbols: list[dict[str, Any]] = []
    routes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    database: dict[str, Any] = {"tables": []}
    log_events: list[dict[str, Any]] = []
    frameworks: set[str] = set()
    python_files = [path for path in candidates if path.suffix == ".py"]

    for path in python_files:
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
            tree = ast.parse(source)
        except SyntaxError as exc:
            omitted.append({"path": rel, "reason": f"syntax_error:{exc.lineno}"})
            continue
        except OSError as exc:
            omitted.append({"path": rel, "reason": _io_error_reason("read_error", exc)})
            continue

        py_imports, import_truncated = _py_import_aliases_and_edges(tree, rel, edges, max_edges=max_edges)
        truncated = truncated or import_truncated
        router_prefixes: dict[str, str] = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                continue
            if _py_dotted_name(node.value.func).split(".")[-1] != "APIRouter":
                continue
            prefix = _py_keyword_string(node.value, "prefix")
            for target in node.targets:
                if isinstance(target, ast.Name):
                    router_prefixes[target.id] = prefix

        django_model_tables: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and _py_django_model_base(node):
                table, _app_label = _py_django_model_table(node, rel)
                django_model_tables[node.name] = table

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                symbol = {"kind": "class", "name": node.name, "path": rel, "line": node.lineno}
                if _py_django_model_base(node):
                    table, app_label = _py_django_model_table(node, rel)
                    columns, foreign_keys = _py_django_model_fields(node, table, app_label, rel, django_model_tables)
                    if columns or foreign_keys:
                        symbol["role"] = "django_model"
                        database["tables"].append(
                            {
                                "table": table,
                                "model": node.name,
                                "app_label": app_label,
                                "path": rel,
                                "line": node.lineno,
                                "columns": columns[:200],
                                "foreign_keys": foreign_keys[:100],
                            }
                        )
                        frameworks.add("django")
                        truncated = not _edge_append(
                            edges,
                            {
                                "kind": "model_table",
                                "from": node.name,
                                "to": f"table:{table}",
                                "framework": "django",
                                "path": rel,
                                "line": node.lineno,
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
                                    "framework": "django",
                                    "path": rel,
                                    "line": foreign_key.get("line"),
                                },
                                max_edges=max_edges,
                            ) or truncated
                else:
                    table = _py_sqlalchemy_table_name(node)
                    if table:
                        columns, foreign_keys = _py_sqlalchemy_model_fields(node, table, rel)
                        if columns or foreign_keys:
                            symbol["role"] = "sqlalchemy_model"
                            database["tables"].append(
                                {
                                    "table": table,
                                    "model": node.name,
                                    "orm": "sqlalchemy",
                                    "path": rel,
                                    "line": node.lineno,
                                    "columns": columns[:200],
                                    "foreign_keys": foreign_keys[:100],
                                }
                            )
                            frameworks.add("sqlalchemy")
                            truncated = not _edge_append(
                                edges,
                                {
                                    "kind": "model_table",
                                    "from": node.name,
                                    "to": f"table:{table}",
                                    "framework": "sqlalchemy",
                                    "path": rel,
                                    "line": node.lineno,
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
                                        "framework": "sqlalchemy",
                                        "path": rel,
                                        "line": foreign_key.get("line"),
                                    },
                                    max_edges=max_edges,
                                ) or truncated
                if len(symbols) < max_symbols:
                    symbols.append(symbol)
                else:
                    truncated = True
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if len(symbols) < max_symbols:
                    symbols.append({"kind": "function", "name": node.name, "path": rel, "line": node.lineno})
                else:
                    truncated = True
                for decorator in node.decorator_list:
                    if not isinstance(decorator, ast.Call):
                        continue
                    decorator_name = _py_dotted_name(decorator.func)
                    decorator_parts = decorator_name.split(".")
                    method = decorator_parts[-1] if decorator_parts else ""
                    router_name = decorator_parts[-2] if len(decorator_parts) >= 2 else ""
                    if method not in PY_HTTP_METHODS or not decorator.args:
                        continue
                    route_path = _py_string(decorator.args[0])
                    if not route_path:
                        continue
                    route = {
                        "framework": "fastapi",
                        "method": "ANY" if method in {"api_route", "route"} else method.upper(),
                        "path": _join_url_path(router_prefixes.get(router_name, ""), route_path),
                        "handler": node.name,
                        "source_path": rel,
                        "line": getattr(decorator, "lineno", node.lineno),
                    }
                    route_name = _py_keyword_string(decorator, "name")
                    if route_name:
                        route["name"] = route_name
                    routes.append(route)
                    frameworks.add("fastapi")
                    truncated = not _edge_append(
                        edges,
                        {
                            "kind": "route_handler",
                            "from": f"route:{_py_route_id(route)}",
                            "to": node.name,
                            "framework": "fastapi",
                            "path": rel,
                            "line": getattr(decorator, "lineno", node.lineno),
                        },
                        max_edges=max_edges,
                    ) or truncated
            if len(symbols) >= max_symbols:
                truncated = True

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            call_name = _py_dotted_name(node.func).split(".")[-1]
            if call_name not in PY_DJANGO_ROUTE_FUNCS or len(node.args) < 2:
                continue
            route_path = _py_string(node.args[0])
            handler = _py_dotted_name(node.args[1])
            if not route_path or not handler:
                continue
            route = {
                "framework": "django",
                "method": "ROUTE",
                "path": route_path,
                "handler": handler,
                "source_path": rel,
                "line": getattr(node, "lineno", 0),
            }
            route_name = _py_keyword_string(node, "name")
            if route_name:
                route["name"] = route_name
            routes.append(route)
            frameworks.add("django")
            truncated = not _edge_append(
                edges,
                {
                    "kind": "route_handler",
                    "from": f"route:{_py_route_id(route)}",
                    "to": handler,
                    "framework": "django",
                    "path": rel,
                    "line": getattr(node, "lineno", 0),
                },
                max_edges=max_edges,
            ) or truncated

        truncated = _append_python_call_edges(
            tree,
            rel,
            py_imports,
            edges,
            log_events,
            max_edges=max_edges,
        ) or truncated
        if len(symbols) >= max_symbols:
            break

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
    framework = "python_web" if len(frameworks) > 1 else next(iter(frameworks), "python")
    graph_database = {**database, "tables": database["tables"][:500]}
    graph = {
        "schema": "hades.code_graph.v1",
        "language": "python",
        "framework": framework,
        "root": workspace_root.name,
        "routes": routes[:500],
        "symbols": symbols,
        "edges": edges,
        "database": graph_database,
        "tests": tests,
        "logs": logs,
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
    }
    graph["summary"] = _py_graph_summary(
        graph["routes"],
        symbols,
        edges,
        framework=framework,
        database=graph_database,
        tests=tests,
        logs=logs,
    )
    return graph


