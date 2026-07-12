from __future__ import annotations

from pathlib import Path
from typing import Any

GRAPH_CONTRACT_VERSION = "hades.graph_artifact.v1"


def finalize_graph_artifact(
    graph: dict[str, Any],
    *,
    payload: dict[str, Any],
    candidates: list[Path],
    omitted: list[dict[str, Any]],
) -> dict[str, Any]:
    language = str(graph.get("language") or "unknown").strip().lower() or "unknown"
    edges = graph.get("edges") if isinstance(graph.get("edges"), list) else graph.get("relationships", [])
    if not edges:
        quality, reason = "inventory_only", "no_relationships_extracted"
    elif bool(graph.get("truncated")) or omitted:
        quality, reason = "partial", "bounded_or_omitted_input"
    else:
        quality, reason = "full", None
    head = str(payload.get("head_commit") or payload.get("workspace_head_commit") or "").strip()
    branch = str(payload.get("branch") or payload.get("current_branch") or "").strip()
    graph["head_commit"] = head or None
    graph["workspace_head_commit"] = head or None
    graph["graph_contract"] = {
        "version": GRAPH_CONTRACT_VERSION,
        "extractor": {
            "name": f"hades-native-{language}",
            "version": "1",
            "mode": "native",
            "quality": quality,
            "fallback_reason": reason,
        },
        "coverage": {
            "languages": [language],
            "files_total": len(candidates) + len(omitted),
            "files_analyzed": len(candidates),
            "files_failed": len(omitted),
        },
        "source": {"branch": branch or None, "head_commit": head or None},
    }
    return graph
