"""Monotonic structural and interprocedural enrichment for Hades graphs."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from hermes_cli.hades_index.tree_sitter_adapter import ParsedFile, TreeSitterAdapter


_SAFE_REF_RE = re.compile(r"^[A-Za-z_$\\][A-Za-z0-9_$\\.:@<>\-]{0,511}$")
_TS_SUFFIXES = {".js", ".jsx", ".ts", ".tsx"}
_TS_COMPILER_SCRIPT = r"""
const fs = require('fs');
const path = require('path');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
function findTypeScript(root) {
  let cursor = root;
  while (true) {
    const candidate = path.join(cursor, 'node_modules', 'typescript', 'lib', 'typescript.js');
    if (fs.existsSync(candidate)) return candidate;
    const parent = path.dirname(cursor);
    if (parent === cursor) break;
    cursor = parent;
  }
  try { return require.resolve('typescript', {paths: [root]}); } catch (_) { return ''; }
}
const tsPath = findTypeScript(input.root);
if (!tsPath) { process.stdout.write(JSON.stringify({status: 'unavailable', edges: []})); process.exit(0); }
const ts = require(tsPath);
let options = {allowJs: true, checkJs: false, noEmit: true, skipLibCheck: true};
if (input.tsconfig) {
  const config = ts.readConfigFile(input.tsconfig, ts.sys.readFile);
  if (!config.error) options = {...options, ...ts.parseJsonConfigFileContent(config.config, ts.sys, path.dirname(input.tsconfig)).options};
}
const normalizedFiles = new Set(input.files.map(file => path.resolve(input.root, file)));
const program = ts.createProgram([...normalizedFiles], options);
const checker = program.getTypeChecker();
const edges = [];
function declarationName(node) {
  if (!node) return '';
  if (ts.isMethodDeclaration(node) || ts.isMethodSignature(node)) {
    const parent = node.parent && node.parent.name ? node.parent.name.getText() : '';
    const name = node.name ? node.name.getText() : '';
    return parent && name ? `${parent}.${name}` : name;
  }
  if ((ts.isFunctionDeclaration(node) || ts.isClassDeclaration(node)) && node.name) return node.name.getText();
  if (ts.isVariableDeclaration(node) && node.name) return node.name.getText();
  return '';
}
function visit(node, context, sourceFile) {
  let nextContext = context;
  if (ts.isFunctionDeclaration(node) || ts.isMethodDeclaration(node)) nextContext = declarationName(node) || context;
  if (ts.isVariableDeclaration(node) && node.initializer && (ts.isArrowFunction(node.initializer) || ts.isFunctionExpression(node.initializer))) nextContext = declarationName(node) || context;
  if (ts.isCallExpression(node) && nextContext && edges.length < input.maxEdges) {
    const signature = checker.getResolvedSignature(node);
    const declaration = signature && signature.declaration;
    const target = declarationName(declaration);
    if (target) {
      const pos = sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile));
      edges.push({kind: 'calls', from: nextContext, to: target, path: path.relative(input.root, sourceFile.fileName).split(path.sep).join('/'), line: pos.line + 1, analyzer: 'typescript_compiler'});
    }
  }
  ts.forEachChild(node, child => visit(child, nextContext, sourceFile));
}
for (const sourceFile of program.getSourceFiles()) {
  if (!normalizedFiles.has(path.resolve(sourceFile.fileName))) continue;
  visit(sourceFile, '', sourceFile);
}
process.stdout.write(JSON.stringify({status: 'ok', edges}));
"""


@dataclass(frozen=True, slots=True)
class AnalyzerResult:
    status: str
    edges: tuple[dict[str, Any], ...] = ()
    omitted: tuple[dict[str, str], ...] = ()


def _safe_ref(value: Any) -> str:
    text = str(value or "").strip()
    return text if _SAFE_REF_RE.fullmatch(text) else ""


def _edge_key(edge: dict[str, Any]) -> tuple[str, str, str, str, int]:
    return (
        str(edge.get("kind") or ""),
        str(edge.get("from") or ""),
        str(edge.get("to") or ""),
        str(edge.get("path") or ""),
        int(edge.get("line") or 0),
    )


def _append_unique(
    edges: list[dict[str, Any]],
    edge: dict[str, Any],
    seen: set[tuple[str, str, str, str, int]],
    max_edges: int,
) -> bool:
    key = _edge_key(edge)
    if key in seen:
        return True
    if len(edges) >= max_edges:
        return False
    seen.add(key)
    edges.append(edge)
    return True


def merge_structural_facts(
    graph: dict[str, Any], parsed: ParsedFile, *, max_symbols: int, max_edges: int
) -> bool:
    """Merge one parsed file and release it before the next file is parsed."""
    symbols = graph.setdefault("symbols", [])
    edges = graph.setdefault("edges", [])
    seen_edges = {_edge_key(edge) for edge in edges}
    symbol_index = {
        (str(symbol.get("path") or ""), str(symbol.get("name") or "")): symbol
        for symbol in symbols
    }
    truncated = False
    for item in parsed.symbols:
        existing = symbol_index.get((parsed.path, item.name))
        if existing is not None:
            existing.setdefault("end_line", item.end_line)
            existing.setdefault("parser", "tree_sitter")
            continue
        if len(symbols) >= max_symbols:
            truncated = True
            break
        symbol = {
            "kind": item.kind,
            "name": item.name,
            "path": parsed.path,
            "line": item.line,
            "end_line": item.end_line,
            "parser": "tree_sitter",
        }
        if item.container:
            symbol["container"] = item.container
        symbols.append(symbol)
        symbol_index[(parsed.path, item.name)] = symbol
    for item in parsed.imports:
        truncated = (
            not _append_unique(
                edges,
                {
                    "kind": "imports",
                    "from": parsed.path,
                    "to": item.target,
                    "path": parsed.path,
                    "line": item.line,
                    "parser": "tree_sitter",
                },
                seen_edges,
                max_edges,
            )
            or truncated
        )
    for item in parsed.calls:
        truncated = (
            not _append_unique(
                edges,
                {
                    "kind": "calls",
                    "from": item.caller,
                    "to": item.target,
                    "path": parsed.path,
                    "line": item.line,
                    "parser": "tree_sitter",
                    "confidence": 0.8,
                },
                seen_edges,
                max_edges,
            )
            or truncated
        )
    return truncated


def sanitize_analyzer_edges(
    workspace_root: Path,
    candidates: Iterable[Path],
    payload: Any,
    *,
    max_edges: int,
) -> list[dict[str, Any]]:
    """Apply an allow-list at the subprocess privacy boundary."""
    root = workspace_root.resolve()
    allowed_paths = {
        candidate.resolve().relative_to(root).as_posix()
        for candidate in candidates
        if candidate.is_file() and candidate.resolve().is_relative_to(root)
    }
    if not isinstance(payload, list):
        return []
    sanitized: list[dict[str, Any]] = []
    for raw in payload:
        if not isinstance(raw, dict) or len(sanitized) >= max_edges:
            continue
        kind = str(raw.get("kind") or "")
        source_ref = _safe_ref(raw.get("from"))
        target_ref = _safe_ref(raw.get("to"))
        path = str(raw.get("path") or "").replace("\\", "/")
        if (
            kind not in {"calls", "calls_method"}
            or not source_ref
            or not target_ref
            or path not in allowed_paths
        ):
            continue
        try:
            line = max(1, int(raw.get("line") or 1))
        except (TypeError, ValueError):
            continue
        analyzer = _safe_ref(raw.get("analyzer")) or "static_analyzer"
        sanitized.append({
            "kind": kind,
            "from": source_ref,
            "to": target_ref,
            "path": path,
            "line": line,
            "analyzer": analyzer,
            "confidence": 1.0,
            "resolved": True,
        })
    return sanitized


def _find_tsconfig(workspace_root: Path, candidates: list[Path]) -> Path | None:
    direct = workspace_root / "tsconfig.json"
    if direct.is_file():
        return direct
    candidate_parents = {path.parent for path in candidates}
    for parent in sorted(candidate_parents, key=lambda item: len(item.parts)):
        cursor = parent
        while cursor.is_relative_to(workspace_root):
            config = cursor / "tsconfig.json"
            if config.is_file():
                return config
            if cursor == workspace_root:
                break
            cursor = cursor.parent
    return None


def run_typescript_compiler(
    workspace_root: Path,
    candidates: list[Path],
    *,
    timeout_seconds: float,
    max_edges: int = 5_000,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> AnalyzerResult:
    """Run the local TypeScript compiler API without emitting source text."""
    ts_files = [
        path
        for path in candidates
        if path.suffix.lower() in _TS_SUFFIXES and path.is_file()
    ]
    tsconfig = _find_tsconfig(workspace_root, ts_files)
    if not ts_files or tsconfig is None:
        return AnalyzerResult(status="skipped")
    node = shutil.which("node")
    if node is None and runner is subprocess.run:
        return AnalyzerResult(status="unavailable")
    node = node or "node"
    root = workspace_root.resolve()
    request = {
        "root": str(root),
        "files": [
            path.resolve().relative_to(root).as_posix()
            for path in ts_files
            if path.resolve().is_relative_to(root)
        ],
        "tsconfig": str(tsconfig.resolve()),
        "maxEdges": max_edges,
    }
    try:
        completed = runner(
            [node, "-e", _TS_COMPILER_SCRIPT],
            input=json.dumps(request, separators=(",", ":")),
            cwd=root,
            capture_output=True,
            text=True,
            timeout=max(0.01, min(float(timeout_seconds), 30.0)),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return AnalyzerResult(
            status="timeout",
            omitted=(
                {"analyzer": "typescript_compiler", "reason": "analyzer_timeout"},
            ),
        )
    except (OSError, ValueError):
        return AnalyzerResult(status="unavailable")
    if completed.returncode != 0 or len(completed.stdout) > 4_000_000:
        return AnalyzerResult(status="error")
    try:
        response = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError):
        return AnalyzerResult(status="error")
    if not isinstance(response, dict):
        return AnalyzerResult(status="error")
    status = str(response.get("status") or "error")
    if status != "ok":
        return AnalyzerResult(
            status=status if status in {"unavailable", "skipped"} else "error"
        )
    edges = sanitize_analyzer_edges(
        root, ts_files, response.get("edges"), max_edges=max_edges
    )
    return AnalyzerResult(status="ok", edges=tuple(edges))


def _route_ref(route: dict[str, Any]) -> str:
    route_id = str(route.get("name") or "").strip()
    if not route_id:
        route_id = f"{route.get('method', '')} {route.get('uri') or route.get('path') or ''}".strip()
    return f"route:{route_id}" if route_id else ""


def resolve_call_graph(graph: dict[str, Any], *, max_edges: int) -> bool:
    """Derive local structural facts without claiming lifecycle reachability.

    The former depth-eight BFS and ``route_reaches_table`` summary have been
    removed.  A route-to-table relationship is a query-time summary over real
    v2 edges, not a producer-side lifecycle assertion.
    """
    edges = graph.setdefault("edges", [])
    seen = {_edge_key(edge) for edge in edges}
    truncated = False

    known_handlers = {
        str(symbol.get("name") or "") for symbol in graph.get("symbols") or []
    }
    for route in graph.get("routes") or []:
        handler = str(route.get("handler") or "")
        route_ref = _route_ref(route)
        if (
            not route_ref
            or not handler
            or (known_handlers and handler not in known_handlers)
        ):
            continue
        truncated = (
            not _append_unique(
                edges,
                {
                    "kind": "route_handler",
                    "from": route_ref,
                    "to": handler,
                    "framework": route.get("framework"),
                    "path": route.get("path") or route.get("source_path"),
                    "line": route.get("line"),
                    "resolver": "hades_static",
                },
                seen,
                max_edges,
            )
            or truncated
        )

    model_tables = {
        str(edge.get("from") or ""): str(edge.get("to") or "")
        for edge in edges
        if edge.get("kind") == "model_table"
        and str(edge.get("to") or "").startswith("table:")
    }
    for edge in list(edges):
        source_ref = str(edge.get("from") or "")
        target_ref = str(edge.get("to") or "")
        table_ref = ""
        model_ref = ""
        if target_ref.startswith("table:"):
            continue
        if "::" in target_ref:
            model_ref = target_ref.split("::", 1)[0]
            table_ref = model_tables.get(model_ref, "")
        if not table_ref and edge.get("table"):
            table_name = str(edge.get("table") or "")
            table_ref = (
                table_name if table_name.startswith("table:") else f"table:{table_name}"
            )
        if not source_ref or not table_ref:
            continue
        truncated = (
            not _append_unique(
                edges,
                {
                    "kind": "accesses_table",
                    "from": source_ref,
                    "to": table_ref,
                    "model": model_ref,
                    "via_kind": edge.get("kind"),
                    "path": edge.get("path"),
                    "line": edge.get("line"),
                    "resolver": "hades_static",
                    "confidence": 0.9,
                },
                seen,
                max_edges,
            )
            or truncated
        )

    return truncated


def _enabled(payload: dict[str, Any], key: str, *, default: bool = True) -> bool:
    value = payload.get(key, default)
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off", "disabled"}
    return bool(value)


def _source_language(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".ts", ".tsx"}:
        return "typescript"
    if suffix in {".js", ".jsx"}:
        return "javascript"
    if suffix == ".py":
        return "python"
    if suffix == ".php":
        return "php"
    return ""


def enrich_graph_for_workspace(
    workspace_root: Path,
    candidates: list[Path],
    graph: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    """Run graph enrichers after the mandatory structural parser canary."""
    detected_languages = tuple(
        sorted({
            language for path in candidates if (language := _source_language(path))
        })
    )
    adapter = TreeSitterAdapter()
    adapter.require_languages(detected_languages)

    max_symbols = int(payload.get("max_symbols") or 5_000)
    max_edges = int(payload.get("max_edges") or max_symbols * 2)
    max_file_bytes = int(payload.get("max_file_bytes") or 512_000)
    analysis = graph.setdefault("analysis", {})

    parser_status: dict[str, str] = {}
    for path in candidates:
        language = _source_language(path)
        if not language:
            continue
        rel = path.relative_to(workspace_root).as_posix()
        parse_result = adapter.parse_file(
            path, relative_path=rel, language=language, max_bytes=max_file_bytes
        )
        if parse_result.status == "failed" or parse_result.syntax is None:
            parser_status[language] = "degraded"
            if parse_result.coverage_event is not None:
                graph.setdefault("coverage_events", []).append({
                    "language": parse_result.coverage_event.language,
                    "capability": parse_result.coverage_event.capability.value,
                    "outcome": parse_result.coverage_event.outcome.value,
                    "reason_code": parse_result.coverage_event.reason_code,
                    "path": parse_result.coverage_event.path,
                    "represented_count": parse_result.coverage_event.represented_count,
                    "omitted_count": parse_result.coverage_event.omitted_count,
                })
            continue
        parser_status.setdefault(language, "ok")
        graph["truncated"] = merge_structural_facts(
            graph,
            parse_result.syntax.parsed_file,
            max_symbols=max_symbols,
            max_edges=max_edges,
        ) or bool(graph.get("truncated"))
    analysis["tree_sitter"] = parser_status or {"status": "not_applicable"}

    if _enabled(payload, "advanced_call_graph", default=True):
        timeout = float(payload.get("analyzer_timeout_seconds") or 5.0)
        analyzer = run_typescript_compiler(
            workspace_root,
            candidates,
            timeout_seconds=timeout,
            max_edges=max(0, max_edges - len(graph.get("edges") or [])),
        )
        analysis["typescript_compiler"] = {
            "status": analyzer.status,
            "edge_count": len(analyzer.edges),
        }
        edges = graph.setdefault("edges", [])
        seen = {_edge_key(edge) for edge in edges}
        for edge in analyzer.edges:
            if not _append_unique(edges, edge, seen, max_edges):
                graph["truncated"] = True
                break
        if analyzer.omitted:
            graph.setdefault("omitted", []).extend(analyzer.omitted)
            graph["truncated"] = True
        graph["truncated"] = resolve_call_graph(graph, max_edges=max_edges) or bool(
            graph.get("truncated")
        )
    else:
        analysis["typescript_compiler"] = {"status": "disabled", "edge_count": 0}
