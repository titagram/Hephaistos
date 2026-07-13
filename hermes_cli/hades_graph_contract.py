from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

GRAPH_CONTRACT_VERSION = "hades.graph_artifact.v1"
MAX_CANONICALIZATION_ISSUES = 50
DEFAULT_MAX_GRAPH_NODES = 5_000
MAX_EXPLICIT_ID_LENGTH = 512

_NODE_ID_PREFIX = "hades:node:v1:"
_EDGE_ID_PREFIX = "hades:edge:v1:"
_EDGE_SOURCE_KEYS = ("source_id", "source", "from")
_EDGE_TARGET_KEYS = ("target_id", "target", "to")
_LOCATION_ONLY_KEYS = {
    "line",
    "line_start",
    "line_end",
    "offset",
    "column",
    "column_start",
    "column_end",
}


def _stable_json(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )


def _hashed_id(prefix: str, value: object) -> str:
    return prefix + hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _locator_fingerprint(value: object) -> str:
    return hashlib.sha256(str(value).encode("utf-8", errors="replace")).hexdigest()[:16]


def _clean_text(value: object) -> str:
    return str(value).strip() if value not in (None, "") else ""


def _valid_explicit_id(value: object) -> bool:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not value
        or len(value) > MAX_EXPLICIT_ID_LENGTH
    ):
        return False
    return not any(ord(character) < 32 or ord(character) == 127 for character in value)


def _normalized_path(value: object) -> str:
    path = _clean_text(value).replace("\\", "/")
    path = re.sub(r"/{2,}", "/", path)
    while path.startswith("./"):
        path = path[2:]
    return path


def _first_text(node: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = _clean_text(node.get(key))
        if value:
            return value
    return ""


def _node_semantic_identity(node: dict[str, Any]) -> dict[str, str] | None:
    kind = _first_text(node, "kind", "type").lower() or "symbol"
    name = _first_text(node, "name", "qualified_name", "fqcn", "symbol", "label")
    signature = _first_text(node, "signature")
    namespace = _first_text(node, "namespace")
    class_name = _first_text(node, "class", "class_name")
    method = _first_text(node, "method", "method_name")
    route_method = _first_text(node, "http_method", "verb")
    route_uri = _first_text(node, "uri", "route", "route_path")
    table = _first_text(node, "table", "table_name")
    path = _normalized_path(
        node.get("path") or node.get("source_path") or node.get("file")
    )

    if not signature and class_name and method:
        signature = f"{class_name}::{method}"
    if not signature and route_uri:
        signature = f"{route_method} {route_uri}".strip()
    if not name and table:
        name = f"table:{table}"
    if not any((name, signature, namespace, path)):
        return None
    return {
        "kind": kind,
        "name": name,
        "signature": signature,
        "namespace": namespace or class_name,
        "path": path,
    }


def _node_aliases(node: dict[str, Any], canonical_id: str) -> set[str]:
    aliases = {canonical_id}
    for key in ("id", "symbol_id", "name", "qualified_name", "fqcn", "signature"):
        value = _clean_text(node.get(key))
        if value:
            aliases.add(value)
            if "\\" in value:
                aliases.add(value.lstrip("\\"))

    class_name = _first_text(node, "class", "class_name")
    method = _first_text(node, "method", "method_name")
    if class_name and method:
        class_name = class_name.lstrip("\\")
        short_name = class_name.split("\\")[-1]
        aliases.update({
            f"{class_name}@{method}",
            f"{class_name}::{method}",
            f"{short_name}@{method}",
            f"{short_name}::{method}",
        })

    kind = _first_text(node, "kind", "type").lower()
    name = _first_text(node, "name")
    if kind == "route" and name:
        aliases.update({f"route:{name}", f"route_name:{name}"})
    table = _first_text(node, "table", "table_name")
    if table:
        aliases.add(f"table:{table}")
    if kind in {"file", "source_file", "module"}:
        path = _normalized_path(
            node.get("path") or node.get("source_path") or node.get("file")
        )
        if path:
            aliases.add(path)
    return {alias for alias in aliases if alias}


def _edge_endpoint(edge: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = _clean_text(edge.get(key))
        if value:
            return value
    return ""


def _external_kind(locator: str) -> str:
    lowered = locator.lower()
    if lowered.startswith("table:"):
        return "table"
    if lowered.startswith(("route:", "route_name:")):
        return "route"
    if "@" in locator or "::" in locator:
        return "method_reference"
    if locator.endswith((".php", ".py", ".ts", ".tsx", ".js", ".jsx", ".sql")):
        return "file"
    if "\\" in locator:
        return "external_class"
    return "external_symbol"


def _external_node(locator: str) -> dict[str, Any]:
    kind = _external_kind(locator)
    node: dict[str, Any] = {
        "kind": kind,
        "name": locator,
        "path": "",
        "external": True,
        "inferred_from_edge": True,
    }
    if kind == "file":
        node["path"] = locator
    return node


def _canonicalize_graph(graph: dict[str, Any], *, max_nodes: int) -> dict[str, Any]:
    raw_nodes_value = (
        graph.get("symbols")
        if isinstance(graph.get("symbols"), list)
        else graph.get("nodes", [])
    )
    raw_edges_value = (
        graph.get("edges")
        if isinstance(graph.get("edges"), list)
        else graph.get("relationships", [])
    )
    raw_nodes = [node for node in raw_nodes_value if isinstance(node, dict)]
    raw_edges = [edge for edge in raw_edges_value if isinstance(edge, dict)]
    issues: list[dict[str, Any]] = []
    issues_count = 0
    issue_reason_counts: Counter[str] = Counter()

    def issue(reason: str, entity: str, value: object | None = None) -> None:
        nonlocal issues_count
        issues_count += 1
        issue_reason_counts[reason] += 1
        if len(issues) >= MAX_CANONICALIZATION_ISSUES:
            return
        item: dict[str, Any] = {"reason": reason, "entity": entity}
        if value not in (None, ""):
            item["locator_sha256"] = _locator_fingerprint(value)
        issues.append(item)

    explicit_counts = Counter(
        value
        for node in raw_nodes
        for value in {_clean_text(node.get("id")), _clean_text(node.get("symbol_id"))}
        if _valid_explicit_id(value)
    )
    candidates_by_id: dict[str, dict[str, Any]] = {}
    invalid_nodes = 0
    duplicate_nodes = 0
    for raw_node in raw_nodes:
        node = dict(raw_node)
        explicit = next(
            (
                value
                for value in (
                    _clean_text(node.get("id")),
                    _clean_text(node.get("symbol_id")),
                )
                if _valid_explicit_id(value) and explicit_counts[value] == 1
            ),
            "",
        )
        semantic = _node_semantic_identity(node)
        if not explicit and semantic is None:
            invalid_nodes += 1
            issue("missing_node_identity", "node")
            continue
        canonical_id = explicit or _hashed_id(_NODE_ID_PREFIX, semantic)
        node["id"] = canonical_id
        existing = candidates_by_id.get(canonical_id)
        if existing is not None:
            comparable_existing = {
                key: value
                for key, value in existing["node"].items()
                if key not in _LOCATION_ONLY_KEYS
            }
            comparable_node = {
                key: value
                for key, value in node.items()
                if key not in _LOCATION_ONLY_KEYS
            }
            if comparable_existing == comparable_node:
                duplicate_nodes += 1
                issue("duplicate_node", "node", canonical_id)
                continue
            invalid_nodes += 1
            issue("canonical_node_id_collision", "node", canonical_id)
            existing["collided"] = True
            continue
        candidates_by_id[canonical_id] = {
            "id": canonical_id,
            "node": node,
            "aliases": _node_aliases(node, canonical_id),
            "synthetic": False,
            "collided": False,
        }

    collided_ids = {
        candidate_id
        for candidate_id, candidate in candidates_by_id.items()
        if candidate["collided"]
    }
    for candidate_id in collided_ids:
        candidates_by_id.pop(candidate_id, None)
        invalid_nodes += 1

    alias_ids: dict[str, set[str]] = defaultdict(set)
    for candidate in candidates_by_id.values():
        for alias in candidate["aliases"]:
            alias_ids[alias].add(candidate["id"])
    ambiguous_aliases = {alias for alias, ids in alias_ids.items() if len(ids) > 1}
    for alias in sorted(ambiguous_aliases):
        issue("ambiguous_node_alias", "alias", alias)
    unique_aliases = {
        alias: next(iter(ids)) for alias, ids in alias_ids.items() if len(ids) == 1
    }

    parsed_edges: list[dict[str, Any]] = []
    unresolved_locators: Counter[str] = Counter()
    endpoint_unresolved = 0
    for edge in raw_edges:
        source = _edge_endpoint(edge, _EDGE_SOURCE_KEYS)
        target = _edge_endpoint(edge, _EDGE_TARGET_KEYS)
        endpoint_states: list[tuple[str, str]] = []
        for endpoint in (source, target):
            if not endpoint:
                endpoint_unresolved += 1
                endpoint_states.append(("missing", ""))
            elif endpoint in ambiguous_aliases:
                endpoint_states.append(("ambiguous", endpoint))
            elif endpoint in unique_aliases:
                endpoint_states.append(("resolved", unique_aliases[endpoint]))
            else:
                endpoint_unresolved += 1
                unresolved_locators[endpoint] += 1
                endpoint_states.append(("external", endpoint))
        parsed_edges.append({
            "edge": edge,
            "source": endpoint_states[0],
            "target": endpoint_states[1],
        })

    external_id_by_locator: dict[str, str] = {}
    for locator in sorted(unresolved_locators):
        node = _external_node(locator)
        semantic = _node_semantic_identity(node)
        canonical_id = _hashed_id(_NODE_ID_PREFIX, semantic)
        # A malicious explicit identifier must not make an absent endpoint
        # alias an existing unrelated node.
        if canonical_id in candidates_by_id:
            issue("external_node_id_collision", "node", locator)
            continue
        candidate = {
            "id": canonical_id,
            "node": {**node, "id": canonical_id},
            "aliases": {locator, canonical_id},
            "synthetic": True,
            "collided": False,
        }
        candidates_by_id[canonical_id] = candidate
        external_id_by_locator[locator] = canonical_id

    reference_counts: Counter[str] = Counter()
    for parsed in parsed_edges:
        for side in ("source", "target"):
            state, value = parsed[side]
            candidate_id = (
                value
                if state == "resolved"
                else external_id_by_locator.get(value, "")
                if state == "external"
                else ""
            )
            if candidate_id:
                reference_counts[candidate_id] += 1

    max_nodes = max(0, min(int(max_nodes), DEFAULT_MAX_GRAPH_NODES))
    ranked_candidates = sorted(
        candidates_by_id.values(),
        key=lambda candidate: (
            0 if reference_counts[candidate["id"]] else 1,
            -reference_counts[candidate["id"]],
            1 if candidate["synthetic"] else 0,
            candidate["id"],
        ),
    )
    selected = ranked_candidates[:max_nodes]
    selected_ids = {candidate["id"] for candidate in selected}
    emitted_nodes = [
        candidate["node"]
        for candidate in sorted(selected, key=lambda candidate: candidate["id"])
    ]
    selected_external_ids = {
        candidate["id"] for candidate in selected if candidate["synthetic"]
    }
    omitted_real = (
        invalid_nodes
        + duplicate_nodes
        + sum(
            1
            for candidate in ranked_candidates[max_nodes:]
            if not candidate["synthetic"]
        )
    )
    omitted_external = sum(
        1 for candidate in ranked_candidates[max_nodes:] if candidate["synthetic"]
    )
    for candidate in ranked_candidates[max_nodes:]:
        issue(
            "node_capacity_exceeded",
            "external_node" if candidate["synthetic"] else "node",
            candidate["id"],
        )

    endpoint_after_resolved = endpoint_after_synthesized = endpoint_after_unresolved = (
        endpoint_after_ambiguous
    ) = 0
    for parsed in parsed_edges:
        for state, value in (parsed["source"], parsed["target"]):
            if state == "ambiguous":
                endpoint_after_ambiguous += 1
            elif state == "missing":
                endpoint_after_unresolved += 1
            elif state == "resolved":
                if value in selected_ids:
                    endpoint_after_resolved += 1
                else:
                    endpoint_after_unresolved += 1
            else:
                candidate_id = external_id_by_locator.get(value, "")
                if candidate_id in selected_ids:
                    endpoint_after_synthesized += 1
                else:
                    endpoint_after_unresolved += 1

    explicit_edge_counts = Counter(
        _clean_text(parsed["edge"].get("id"))
        for parsed in parsed_edges
        if _valid_explicit_id(_clean_text(parsed["edge"].get("id")))
    )
    emitted_edges: list[dict[str, Any]] = []
    emitted_edge_ids: set[str] = set()
    edges_omitted = 0
    for parsed in parsed_edges:
        resolved: list[str] = []
        failed_reason = ""
        failed_value = ""
        for state, value in (parsed["source"], parsed["target"]):
            if state == "ambiguous":
                failed_reason, failed_value = "ambiguous_endpoint_alias", value
                break
            if state == "missing":
                failed_reason = "missing_edge_endpoint"
                break
            candidate_id = (
                value if state == "resolved" else external_id_by_locator.get(value, "")
            )
            if not candidate_id or candidate_id not in selected_ids:
                failed_reason, failed_value = "endpoint_node_not_selected", value
                break
            resolved.append(candidate_id)
        if failed_reason:
            edges_omitted += 1
            issue(failed_reason, "edge", failed_value)
            continue

        edge = dict(parsed["edge"])
        source_id, target_id = resolved
        edge["source_id"] = source_id
        edge["target_id"] = target_id
        explicit_id = _clean_text(edge.get("id"))
        if not (
            _valid_explicit_id(explicit_id) and explicit_edge_counts[explicit_id] == 1
        ):
            semantic_edge = {
                "kind": _first_text(edge, "kind", "type").lower() or "relationship",
                "source_id": source_id,
                "target_id": target_id,
                "properties": {
                    key: value
                    for key, value in edge.items()
                    if key not in {"id", *_EDGE_SOURCE_KEYS, *_EDGE_TARGET_KEYS}
                },
            }
            explicit_id = _hashed_id(_EDGE_ID_PREFIX, semantic_edge)
        edge["id"] = explicit_id
        if explicit_id in emitted_edge_ids:
            issue("duplicate_edge", "edge", explicit_id)
            continue
        emitted_edge_ids.add(explicit_id)
        emitted_edges.append(edge)

    if isinstance(graph.get("symbols"), list):
        # `symbols/edges` are the long-standing local indexer contract. Keep
        # their evidence fields and endpoint locators intact for local callers,
        # while publishing the closed canonical projection in the additive
        # `nodes/relationships` fields preferred by the backend normalizer.
        graph["symbols"] = raw_nodes
        graph["edges"] = raw_edges
        graph["nodes"] = emitted_nodes
        graph["relationships"] = emitted_edges
    else:
        graph["nodes"] = emitted_nodes
        graph["relationships"] = emitted_edges
    return {
        "nodes_input": len(raw_nodes),
        "nodes_emitted": len(emitted_nodes),
        "nodes_synthesized": len(selected_external_ids),
        "nodes_omitted": omitted_real,
        "external_nodes_omitted": omitted_external,
        "edges_input": len(raw_edges),
        "edges_emitted": len(emitted_edges),
        "edges_omitted": edges_omitted,
        "duplicate_edges_omitted": len(raw_edges) - edges_omitted - len(emitted_edges),
        "aliases_total": len(alias_ids),
        "aliases_resolved": len(unique_aliases),
        "ambiguous_aliases": len(ambiguous_aliases),
        "endpoint_aliases_missing_before_synthesis": endpoint_unresolved,
        "endpoint_aliases_resolved": endpoint_after_resolved,
        "endpoint_aliases_synthesized": endpoint_after_synthesized,
        "endpoint_aliases_unresolved": endpoint_after_unresolved,
        "endpoint_aliases_ambiguous": endpoint_after_ambiguous,
        "issues_count": issues_count,
        "issue_reasons": dict(sorted(issue_reason_counts.items())),
        "issues": issues,
        "issues_truncated": issues_count > len(issues),
    }


def finalize_graph_artifact(
    graph: dict[str, Any],
    *,
    payload: dict[str, Any],
    candidates: list[Path],
    omitted: list[dict[str, Any]],
) -> dict[str, Any]:
    canonicalization = _canonicalize_graph(
        graph,
        # ``max_symbols`` bounds extractor declarations, not the canonical
        # graph's inferred endpoint nodes. Keep a separate hard global cap so
        # small test/index jobs do not lose valid declarations to placeholders.
        max_nodes=int(payload.get("max_graph_nodes") or DEFAULT_MAX_GRAPH_NODES),
    )
    language = str(graph.get("language") or "unknown").strip().lower() or "unknown"
    edges = (
        graph.get("relationships")
        if isinstance(graph.get("relationships"), list)
        else graph.get("edges", [])
    )
    canonicalization_omitted = bool(
        canonicalization["nodes_omitted"]
        or canonicalization["external_nodes_omitted"]
        or canonicalization["edges_omitted"]
    )
    if not edges:
        quality, reason = "inventory_only", "no_relationships_extracted"
    elif bool(graph.get("truncated")) or omitted:
        quality, reason = "partial", "bounded_or_omitted_input"
    elif canonicalization_omitted:
        quality, reason = "partial", "canonicalization_omissions"
    else:
        quality, reason = "full", None
    head = str(
        payload.get("head_commit") or payload.get("workspace_head_commit") or ""
    ).strip()
    branch = str(payload.get("branch") or payload.get("current_branch") or "").strip()
    graph["head_commit"] = head or None
    graph["workspace_head_commit"] = head or None
    graph["canonicalization"] = canonicalization
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
