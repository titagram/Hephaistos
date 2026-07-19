"""Contracts for optional structural parsing and call-graph enrichment."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


class _Node:
    def __init__(
        self,
        node_type: str,
        start_byte: int,
        end_byte: int,
        *,
        start_point: tuple[int, int] = (0, 0),
        end_point: tuple[int, int] = (0, 0),
        children: tuple["_Node", ...] = (),
        fields: dict[str, "_Node"] | None = None,
    ) -> None:
        self.type = node_type
        self.start_byte = start_byte
        self.end_byte = end_byte
        self.start_point = start_point
        self.end_point = end_point
        self.children = children
        self._fields = fields or {}

    def child_by_field_name(self, name: str) -> "_Node | None":
        return self._fields.get(name)


class _Parser:
    def __init__(self, root: _Node) -> None:
        self._root = root

    def parse(self, _source: bytes):
        return type("Tree", (), {"root_node": self._root})()


def test_tree_sitter_adapter_extracts_bounded_metadata_only():
    from hermes_cli.hades_index.tree_sitter_adapter import TreeSitterAdapter

    source = b"function checkout() { charge(); }"
    function_name = _Node("identifier", 9, 17)
    target = _Node("identifier", 22, 28)
    call = _Node(
        "call_expression", 22, 30, start_point=(0, 22), fields={"function": target}
    )
    function = _Node(
        "function_declaration",
        0,
        len(source),
        start_point=(0, 0),
        end_point=(0, len(source)),
        children=(function_name, call),
        fields={"name": function_name},
    )
    root = _Node("program", 0, len(source), children=(function,))
    adapter = TreeSitterAdapter(parser_loader=lambda _language: _Parser(root))

    parsed = adapter.parse_bytes(source, path="src/checkout.ts", language="typescript")

    assert parsed.status == "parsed"
    assert parsed.syntax is not None
    assert parsed.syntax.symbols[0].name == "checkout"
    assert parsed.syntax.calls[0].caller == "checkout"
    assert parsed.syntax.calls[0].target == "charge"
    assert "function checkout" not in repr(parsed)
    assert not hasattr(parsed, "source")


def test_required_parser_canary_fails_before_graph_enrichment():
    from hermes_cli.hades_index.tree_sitter_adapter import (
        RequiredParserUnavailable,
        TreeSitterAdapter,
    )

    def unavailable(_language: str):
        raise ImportError("required grammar missing")

    adapter = TreeSitterAdapter(parser_loader=unavailable)

    with pytest.raises(RequiredParserUnavailable) as raised:
        adapter.require_languages(("typescript", "javascript", "typescript"))

    assert raised.value.languages == ("javascript", "typescript")
    assert "required grammar missing" not in str(raised.value)


def test_required_canary_has_no_payload_bypass_or_partial_graph_mutation(
    tmp_path: Path, monkeypatch
):
    from hermes_cli.hades_index import resolution
    from hermes_cli.hades_index.tree_sitter_adapter import RequiredParserUnavailable

    source = tmp_path / "src" / "app.ts"
    source.parent.mkdir()
    source.write_text("export function app() {}", encoding="utf-8")
    graph = {"symbols": [], "edges": []}

    class BrokenAdapter:
        def require_languages(self, languages):
            assert tuple(languages) == ("typescript",)
            raise RequiredParserUnavailable(languages)

        def parse_file(self, *_args, **_kwargs):
            pytest.fail("no source file may be parsed after a failed canary")

    monkeypatch.setattr(resolution, "TreeSitterAdapter", BrokenAdapter)

    with pytest.raises(RequiredParserUnavailable):
        resolution.enrich_graph_for_workspace(
            tmp_path,
            [source],
            graph,
            {"tree_sitter": False},
        )

    assert graph == {"symbols": [], "edges": []}


def test_required_canary_failure_escapes_the_real_graph_publication_boundary(
    tmp_path: Path, monkeypatch
):
    from hermes_cli import hades_backend_jobs
    from hermes_cli.hades_graph_v2.bundle import GraphBundleWriter
    from hermes_cli.hades_index.tree_sitter_adapter import RequiredParserUnavailable

    source = tmp_path / "app.py"
    source.write_text("def app():\n    return 1\n", encoding="utf-8")
    published = []

    def fail_required_canary(*_args, **_kwargs):
        raise RequiredParserUnavailable(("python",))

    monkeypatch.setattr(
        "hermes_cli.hades_index.tree_sitter_adapter.TreeSitterAdapter.require_languages",
        fail_required_canary,
    )
    monkeypatch.setattr(
        GraphBundleWriter,
        "write",
        lambda *_args, **_kwargs: published.append("bundle"),
    )

    with pytest.raises(RequiredParserUnavailable) as raised:
        hades_backend_jobs._execute_populate_backend_ast(
            {
                "capability": "populate_backend_ast",
                "payload": {
                    "project_id": "proj_1",
                    "workspace_binding_id": "wb_1",
                },
            },
            tmp_path,
        )

    assert raised.value.languages == ("python",)
    assert published == []


def test_source_parse_failure_after_successful_canary_remains_partial():
    from hermes_cli.hades_index.lifecycle.model import CoverageOutcome
    from hermes_cli.hades_index.tree_sitter_adapter import TreeSitterAdapter

    class SelectiveParser:
        def __init__(self):
            self.calls = 0

        def parse(self, _source: bytes):
            self.calls += 1
            root = type("Root", (), {"has_error": self.calls > 1})()
            return type("Tree", (), {"root_node": root})()

    adapter = TreeSitterAdapter(parser_loader=lambda _language: SelectiveParser())
    adapter.require_languages(("typescript",))

    result = adapter.parse_bytes(b"invalid", path="src/bad.ts", language="typescript")

    assert result.status == "failed"
    assert result.failure is not None and result.failure.code == "parser_failed"
    assert result.coverage_event is not None
    assert result.coverage_event.outcome is CoverageOutcome.PARTIAL


def test_call_graph_resolution_is_monotonic_without_a_route_table_lifecycle_shortcut():
    from hermes_cli.hades_index.resolution import resolve_call_graph

    legacy_edge = {
        "kind": "route_handler",
        "from": "route:orders.store",
        "to": "OrderController@store",
        "path": "routes/web.php",
        "line": 7,
    }
    graph = {
        "symbols": [
            {
                "kind": "method",
                "name": "OrderController@store",
                "path": "app/Http/Controllers/OrderController.php",
                "line": 13,
            },
            {
                "kind": "method",
                "name": "OrderService@create",
                "path": "app/Services/OrderService.php",
                "line": 10,
            },
        ],
        "edges": [
            legacy_edge,
            {
                "kind": "calls_method",
                "from": "OrderController@store",
                "to": "OrderService@create",
                "path": "app/Http/Controllers/OrderController.php",
                "line": 15,
            },
            {
                "kind": "static_call",
                "from": "OrderService@create",
                "to": "App\\Models\\Order::create",
                "path": "app/Services/OrderService.php",
                "line": 12,
            },
            {"kind": "model_table", "from": "App\\Models\\Order", "to": "table:orders"},
        ],
    }

    resolve_call_graph(graph, max_edges=100)

    assert legacy_edge in graph["edges"]
    assert any(
        edge.get("kind") == "accesses_table"
        and edge.get("from") == "OrderService@create"
        and edge.get("to") == "table:orders"
        for edge in graph["edges"]
    )
    assert not any(edge.get("kind") == "route_reaches_table" for edge in graph["edges"])


def test_analyzer_output_is_allowlisted_and_workspace_scoped(tmp_path: Path):
    from hermes_cli.hades_index.resolution import sanitize_analyzer_edges

    candidate = tmp_path / "src" / "app.ts"
    candidate.parent.mkdir()
    candidate.write_text("const secret = 'do-not-leak';", encoding="utf-8")
    payload = [
        {
            "kind": "calls",
            "from": "run",
            "to": "work",
            "path": "src/app.ts",
            "line": 1,
            "source": "const secret = 'do-not-leak';",
            "snippet": "do-not-leak",
            "diagnostic": "do-not-leak",
            "analyzer": "typescript_compiler",
        },
        {
            "kind": "calls",
            "from": "escape",
            "to": "outside",
            "path": "../outside.ts",
            "line": 1,
        },
    ]

    edges = sanitize_analyzer_edges(tmp_path, [candidate], payload, max_edges=10)

    assert edges == [
        {
            "kind": "calls",
            "from": "run",
            "to": "work",
            "path": "src/app.ts",
            "line": 1,
            "analyzer": "typescript_compiler",
            "confidence": 1.0,
            "resolved": True,
        }
    ]
    assert "do-not-leak" not in repr(edges)


def test_typescript_analyzer_timeout_is_non_fatal(tmp_path: Path):
    from hermes_cli.hades_index.resolution import run_typescript_compiler

    source = tmp_path / "src" / "app.ts"
    source.parent.mkdir()
    source.write_text("export function run() {}", encoding="utf-8")
    (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")

    def timeout_runner(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="node", timeout=0.01)

    result = run_typescript_compiler(
        tmp_path,
        [source],
        timeout_seconds=0.01,
        runner=timeout_runner,
    )

    assert result.edges == ()
    assert result.status == "timeout"
    assert result.omitted == (
        {"analyzer": "typescript_compiler", "reason": "analyzer_timeout"},
    )
