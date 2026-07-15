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
    max_symbols = min(int(payload.get("max_symbols") or 5_000), 5_000)
    max_edges = min(int(payload.get("max_edges") or max_symbols * 2), 10_000)
    max_file_bytes = min(int(payload.get("max_file_bytes") or 512_000), 512_000)
    truncated = False

    manifest_names = {
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
    }
    adapter_specs = [
        ("php", php_indexer, lambda path: path.suffix.lower() == ".php"),
        (
            "typescript",
            typescript_indexer,
            lambda path: path.suffix.lower() in {".js", ".jsx", ".ts", ".tsx", ".prisma"}
            or path.name in manifest_names,
        ),
        ("sql", sql_indexer, lambda path: path.suffix.lower() == ".sql"),
        ("python", python_indexer, lambda path: path.suffix.lower() == ".py"),
    ]
    detected = [
        (name, indexer, [path for path in candidates if predicate(path)])
        for name, indexer, predicate in adapter_specs
        if any(predicate(path) and path.name not in manifest_names for path in candidates)
    ]
    if not detected:
        detected = [("python", python_indexer, candidates)]

    if len(detected) == 1:
        _name, indexer, adapter_candidates = detected[0]
        graph = indexer.build_graph(
            workspace_root,
            adapter_candidates,
            omitted,
            truncated=truncated,
            max_symbols=max_symbols,
            max_edges=max_edges,
            max_file_bytes=max_file_bytes,
        )
    else:
        artifacts: list[dict[str, Any]] = []
        for _name, indexer, adapter_candidates in detected:
            artifacts.append(
                indexer.build_graph(
                    workspace_root,
                    adapter_candidates,
                    list(omitted),
                    truncated=truncated,
                    max_symbols=max_symbols,
                    max_edges=max_edges,
                    max_file_bytes=max_file_bytes,
                )
            )
        from hermes_cli.hades_index.aggregate import merge_graph_artifacts

        graph = merge_graph_artifacts(
            artifacts,
            root=workspace_root.name,
            max_symbols=max_symbols,
            max_edges=max_edges,
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

    from hermes_cli.hades_graph_contract import finalize_graph_artifact

    return finalize_graph_artifact(graph, payload=payload, candidates=candidates, omitted=omitted)
