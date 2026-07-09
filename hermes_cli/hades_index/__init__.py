"""Entry point for pluggable code graph indexing — currently proxies to hades_backend_jobs."""

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

    Currently this is a minimal seam that proxies to existing indexer functions
    in hades_backend_jobs. In future phases, this will dispatch to pluggable
    language-specific indexers.

    Args:
        workspace_root: Root path of the workspace
        candidates: List of file paths to index
        omitted: List of omitted file records
        payload: Job payload with configuration (max_symbols, max_edges, etc.)

    Returns:
        dict with 'schema', 'summary', and other artifact fields
    """
    # Import here to avoid circular dependencies and allow future refactoring
    from hermes_cli.hades_backend_jobs import (
        _build_python_artifact,
        _build_php_graph,
        _build_ts_graph,
        _build_sql_graph,
        _attach_source_slice_candidates,
    )

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
        graph = _build_php_graph(
            workspace_root,
            candidates,
            omitted,
            truncated=truncated,
            max_symbols=max_symbols,
            max_edges=max_edges,
            max_file_bytes=max_file_bytes,
        )
    elif has_ts:
        graph = _build_ts_graph(
            workspace_root,
            candidates,
            omitted,
            truncated=truncated,
            max_symbols=max_symbols,
            max_edges=max_edges,
            max_file_bytes=max_file_bytes,
        )
    elif has_sql:
        graph = _build_sql_graph(
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
        graph = _build_python_artifact(
            workspace_root,
            candidates,
            omitted,
            truncated=truncated,
            max_symbols=max_symbols,
            max_edges=max_edges,
            max_file_bytes=max_file_bytes,
        )

    # Attach source slice candidates (applies to all graph types)
    _attach_source_slice_candidates(workspace_root, graph, payload)

    return graph
