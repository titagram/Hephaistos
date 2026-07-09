"""Golden harness for Hades code graph indexer — ensures refactors don't alter artifact structure."""

from pathlib import Path
from hermes_cli.hades_backend_jobs import execute_job

FIXTURES_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "hades" / "indexer"


def test_python_app_indexing():
    """Test Python app indexing produces stable artifact."""
    workspace_root = FIXTURES_ROOT / "python_app"
    job = {
        "capability": "populate_backend_ast",
        "payload": {"max_files": 100, "max_symbols": 1000},
    }
    result = execute_job(job, workspace_root=workspace_root)

    assert result["status"] == "completed"
    artifact = result["artifact"]

    # Schema validation
    assert artifact["schema"] in ("hades.code_graph.v1", "hades.symbols.v1")
    assert artifact.get("raw_source_included") is False

    # Artifact has content
    assert artifact.get("symbols"), "Expected symbols in Python artifact"
    assert len(artifact["symbols"]) > 0

    # Expected symbols: helper_function, main
    symbol_names = {s["name"] for s in artifact.get("symbols", [])}
    assert "helper_function" in symbol_names or "main" in symbol_names, \
        f"Expected main or helper_function in symbols, got {symbol_names}"

    # Edges: should have call edges
    edges = artifact.get("edges", [])
    call_edges = [e for e in edges if e.get("kind") == "calls"]
    assert len(call_edges) > 0, "Expected 'calls' edges in Python artifact"

    # Source slice candidates attached
    assert "source_slice_candidates" in artifact, \
        "Expected source_slice_candidates after attachment"


def test_laravel_app_indexing():
    """Test Laravel/PHP app indexing produces stable artifact."""
    workspace_root = FIXTURES_ROOT / "laravel_app"
    job = {
        "capability": "populate_backend_ast",
        "payload": {"max_files": 100, "max_symbols": 1000},
    }
    result = execute_job(job, workspace_root=workspace_root)

    assert result["status"] == "completed"
    artifact = result["artifact"]

    # Schema validation
    assert artifact["schema"] in ("hades.php_graph.v1", "hades.code_graph.v1")
    assert artifact.get("raw_source_included") is False

    # Artifact has content: symbols (controller)
    symbols = artifact.get("symbols", [])
    assert len(symbols) > 0, "Expected symbols in PHP artifact"

    # Expected symbols: OrderController and its methods
    symbol_names = {s.get("name") for s in symbols}
    assert any("OrderController" in name for name in symbol_names), \
        f"Expected OrderController in symbols, got {symbol_names}"

    # Edges: should have method calls
    edges = artifact.get("edges", [])
    method_edges = [e for e in edges if e.get("kind") in ("calls_method", "static_call")]
    assert len(method_edges) > 0, \
        "Expected method/static call edges in PHP artifact"

    # Source slice candidates attached
    assert "source_slice_candidates" in artifact, \
        "Expected source_slice_candidates after attachment"


def test_typescript_app_indexing():
    """Test TypeScript/JavaScript app indexing produces stable artifact."""
    workspace_root = FIXTURES_ROOT / "ts_app"
    job = {
        "capability": "populate_backend_ast",
        "payload": {"max_files": 100, "max_symbols": 1000},
    }
    result = execute_job(job, workspace_root=workspace_root)

    assert result["status"] == "completed"
    artifact = result["artifact"]

    # Schema validation
    assert artifact["schema"] in ("hades.code_graph.v1", "hades.symbols.v1")
    assert artifact.get("raw_source_included") is False

    # Artifact has content: symbols (exports/imports)
    symbols = artifact.get("symbols", [])
    assert len(symbols) > 0, "Expected symbols in TypeScript artifact"

    # Expected exports: add, multiply, calculateTotal
    symbol_names = {s["name"] for s in symbols}
    assert "add" in symbol_names or "multiply" in symbol_names, \
        f"Expected add/multiply in symbols, got {symbol_names}"

    # Edges: should have import edges
    edges = artifact.get("edges", [])
    import_edges = [e for e in edges if e.get("kind") == "imports"]
    assert len(import_edges) > 0 or len(symbols) > 1, \
        "Expected 'imports' edges or multiple symbols in TS artifact"

    # Source slice candidates attached
    assert "source_slice_candidates" in artifact, \
        "Expected source_slice_candidates after attachment"


def test_sql_app_indexing():
    """Test SQL schema indexing produces stable artifact."""
    workspace_root = FIXTURES_ROOT / "sql_app"
    job = {
        "capability": "populate_backend_ast",
        "payload": {"max_files": 100, "max_symbols": 1000},
    }
    result = execute_job(job, workspace_root=workspace_root)

    assert result["status"] == "completed"
    artifact = result["artifact"]

    # Schema validation
    assert artifact["schema"] in ("hades.code_graph.v1", "hades.symbols.v1")
    assert artifact.get("raw_source_included") is False

    # Artifact has content: tables
    tables = artifact.get("database", {}).get("tables", [])
    assert len(tables) > 0, "Expected tables in SQL artifact"

    # Expected tables: users, orders, order_items
    table_names = {t.get("table") for t in tables}
    assert "users" in table_names or "orders" in table_names, \
        f"Expected users/orders tables, got {table_names}"

    # Edges: should have foreign key edges
    edges = artifact.get("edges", [])
    fk_edges = [e for e in edges if e.get("kind") == "foreign_key"]
    assert len(fk_edges) > 0, "Expected 'foreign_key' edges in SQL artifact"

    # Source slice candidates attached
    assert "source_slice_candidates" in artifact, \
        "Expected source_slice_candidates after attachment"
