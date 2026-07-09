"""Entry point for pluggable code graph indexing — dispatches to per-language indexer modules."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def build_graph_for_workspace(
    workspace_root: Path | str,
    candidates: list[Path],
    omitted: list[dict[str, Any]],
    payload: dict[str, Any],
) -> dict[str, Any]:
    """
    Build a code graph for the workspace.

    Dispatches to the per-language indexer module (php, typescript, sql, python)
    based on the detected file types in `candidates`.

    Args:
        workspace_root: Root path of the workspace
        candidates: List of file paths to index
        omitted: List of omitted file records
        payload: Job payload with configuration (max_symbols, max_edges, etc.)

    Returns:
        dict with 'schema', 'summary', and other artifact fields
    """
    # Import here to avoid circular dependencies and allow future refactoring
    from hermes_cli.hades_backend_jobs import _attach_source_slice_candidates
    from hermes_cli.hades_index import php as php_indexer
    from hermes_cli.hades_index import python as python_indexer
    from hermes_cli.hades_index import sql as sql_indexer
    from hermes_cli.hades_index import typescript as typescript_indexer

    workspace_root = Path(workspace_root)
    max_symbols = int(payload.get("max_symbols") or 5_000)
    max_edges = int(payload.get("max_edges") or max_symbols * 2)
    max_file_bytes = int(payload.get("max_file_bytes") or 512_000)
    truncated = False

    # Detect language and build appropriate graph
    has_php = any(path.suffix.lower() == ".php" for path in candidates)
    has_ts = any(path.suffix.lower() in {".js", ".jsx", ".ts", ".tsx", ".prisma"} for path in candidates)
    has_sql = any(path.suffix.lower() == ".sql" for path in candidates)
    has_python = any(path.suffix.lower() == ".py" for path in candidates)

    if has_php:
        graph = php_indexer.build_graph(
            workspace_root,
            candidates,
            omitted,
            truncated=truncated,
            max_symbols=max_symbols,
            max_edges=max_edges,
            max_file_bytes=max_file_bytes,
        )
    elif has_ts:
        graph = typescript_indexer.build_graph(
            workspace_root,
            candidates,
            omitted,
            truncated=truncated,
            max_symbols=max_symbols,
            max_edges=max_edges,
            max_file_bytes=max_file_bytes,
        )
    elif has_sql:
        graph = sql_indexer.build_graph(
            workspace_root,
            candidates,
            omitted,
            truncated=truncated,
            max_symbols=max_symbols,
            max_edges=max_edges,
            max_file_bytes=max_file_bytes,
        )
    else:
        # Default to Python
        graph = python_indexer.build_graph(
            workspace_root,
            candidates,
            omitted,
            truncated=truncated,
            max_symbols=max_symbols,
            max_edges=max_edges,
            max_file_bytes=max_file_bytes,
        )

    # Optional parsers and analyzers only enrich the proven legacy graph. Any
    # failure here must leave that baseline usable.
    try:
        from hermes_cli.hades_index.resolution import enrich_graph_for_workspace

        enrich_graph_for_workspace(workspace_root, candidates, graph, payload)
    except Exception:
        graph.setdefault("analysis", {})["enrichment"] = {"status": "degraded"}

    # Attach source slice candidates (applies to all graph types)
    _attach_source_slice_candidates(workspace_root, graph, payload)

    return graph
