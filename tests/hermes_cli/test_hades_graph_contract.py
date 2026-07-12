from pathlib import Path

from hermes_cli.hades_graph_contract import finalize_graph_artifact


def test_finalize_graph_artifact_records_source_and_quality(tmp_path: Path):
    graph = {
        "schema": "hades.php_graph.v1",
        "language": "php",
        "symbols": [{"id": "class:A", "name": "A", "kind": "class", "path": "a.php"}],
        "edges": [{"id": "calls:1", "kind": "calls", "source": "class:A", "target": "class:B"}],
        "truncated": False,
    }
    result = finalize_graph_artifact(
        graph,
        payload={"head_commit": "abc123", "branch": "main"},
        candidates=[tmp_path / "a.php"],
        omitted=[],
    )
    assert result["graph_contract"] == {
        "version": "hades.graph_artifact.v1",
        "extractor": {
            "name": "hades-native-php",
            "version": "1",
            "mode": "native",
            "quality": "full",
            "fallback_reason": None,
        },
        "coverage": {"languages": ["php"], "files_total": 1, "files_analyzed": 1, "files_failed": 0},
        "source": {"branch": "main", "head_commit": "abc123"},
    }
    assert result["head_commit"] == "abc123"


def test_finalize_graph_artifact_exposes_inventory_fallback(tmp_path: Path):
    result = finalize_graph_artifact(
        {
            "schema": "hades.code_graph.v1",
            "language": "typescript",
            "symbols": [],
            "edges": [],
            "truncated": True,
        },
        payload={"workspace_head_commit": "def456"},
        candidates=[tmp_path / "app.ts"],
        omitted=[{"path": "large.ts", "reason": "max_file_bytes"}],
    )
    assert result["graph_contract"]["extractor"]["quality"] == "inventory_only"
    assert result["graph_contract"]["extractor"]["fallback_reason"] == "no_relationships_extracted"
    assert result["graph_contract"]["coverage"]["files_failed"] == 1
