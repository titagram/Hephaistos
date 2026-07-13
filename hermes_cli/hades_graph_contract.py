from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from typing import Any

GRAPH_CONTRACT_VERSION = "hades.graph_artifact.v1"
MAX_CANONICALIZATION_ISSUES = 50
DEFAULT_MAX_GRAPH_NODES = 5_000
MAX_EXPLICIT_ID_BYTES = 512
MAX_ENDPOINT_LOCATOR_BYTES = 512
MAX_PLACEHOLDER_LABEL_BYTES = 256

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
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/#+-]*$")
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")
_FQCN_RE = re.compile(r"^\\?[A-Za-z_][A-Za-z0-9_]*(?:\\[A-Za-z_][A-Za-z0-9_]*)+$")
_DOTTED_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+$")
_BARE_SYMBOL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_METHOD_RE = re.compile(
    r"^(?:\\?[A-Za-z_][A-Za-z0-9_]*(?:[\\.][A-Za-z_][A-Za-z0-9_]*)*)"
    r"(?:::|@)[A-Za-z_][A-Za-z0-9_]*$"
)
_ROUTE_RE = re.compile(r"^route(?:_name)?:[A-Za-z0-9_.:/{}-]+$", re.IGNORECASE)
_ROUTE_METHOD_RE = re.compile(
    r"^route:(?:GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD|ANY) /[A-Za-z0-9_./{}-]*$",
    re.IGNORECASE,
)
_DATA_REF_RE = re.compile(
    r"^(?:table|model|entity|column|schema|collection|class|symbol):[A-Za-z0-9_.:-]+$",
    re.IGNORECASE,
)
_SOURCE_SUFFIXES = (".php", ".py", ".ts", ".tsx", ".js", ".jsx", ".sql")


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


def _utf8_size(value: str) -> int:
    return len(value.encode("utf-8", errors="replace"))


def _has_control(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _is_unsafe_path(value: str) -> bool:
    lowered = value.lower()
    return bool(
        value.startswith(("/", "//", "\\\\"))
        or lowered.startswith("file://")
        or _WINDOWS_DRIVE_RE.match(value)
    )


def _valid_explicit_id(value: object) -> bool:
    return bool(
        isinstance(value, str)
        and value == value.strip()
        and value
        and _utf8_size(value) <= MAX_EXPLICIT_ID_BYTES
        and not _has_control(value)
        and not _is_unsafe_path(value)
        and _ID_RE.fullmatch(value)
    )


def _bounded_label(value: str) -> str:
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) <= MAX_PLACEHOLDER_LABEL_BYTES:
        return value
    return encoded[:MAX_PLACEHOLDER_LABEL_BYTES].decode("utf-8", errors="ignore")


def _normalized_path(value: object) -> str:
    path = _clean_text(value).replace("\\", "/")
    path = re.sub(r"/{2,}", "/", path)
    while path.startswith("./"):
        path = path[2:]
    if _is_unsafe_path(_clean_text(value)):
        path = PurePosixPath(path).name
    if _utf8_size(path) > MAX_ENDPOINT_LOCATOR_BYTES:
        path = PurePosixPath(path).name
    return (
        _bounded_label(path) if _utf8_size(path) > MAX_ENDPOINT_LOCATOR_BYTES else path
    )


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

    identity_values = (kind, name, signature, namespace, class_name, method, route_uri)
    if any(_utf8_size(value) > MAX_ENDPOINT_LOCATOR_BYTES for value in identity_values):
        return None
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


def _canonical_node(raw_node: dict[str, Any], canonical_id: str) -> dict[str, Any]:
    node = dict(raw_node)
    node["id"] = canonical_id
    for key in ("path", "source_path", "file"):
        if key in node and node[key] not in (None, ""):
            node[key] = _normalized_path(node[key])
    return node


def _safe_alias(value: object) -> str:
    if not isinstance(value, str) or value != value.strip() or not value:
        return ""
    if _utf8_size(value) > MAX_ENDPOINT_LOCATOR_BYTES or _has_control(value):
        return ""
    if _is_unsafe_path(value):
        return ""
    return value


def _node_aliases(node: dict[str, Any], canonical_id: str) -> set[str]:
    aliases = {canonical_id}
    for key in ("id", "symbol_id", "name", "qualified_name", "fqcn", "signature"):
        value = _safe_alias(node.get(key))
        if value:
            aliases.add(value)
            if "\\" in value:
                aliases.add(value.lstrip("\\"))

    class_name = _safe_alias(_first_text(node, "class", "class_name"))
    method = _safe_alias(_first_text(node, "method", "method_name"))
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
    name = _safe_alias(node.get("name"))
    if kind == "route" and name:
        aliases.update({f"route:{name}", f"route_name:{name}"})
    table = _safe_alias(node.get("table") or node.get("table_name"))
    if table:
        aliases.add(f"table:{table}")
    if kind in {"file", "source_file", "module"}:
        path = _safe_alias(
            _normalized_path(
                node.get("path") or node.get("source_path") or node.get("file")
            )
        )
        if path:
            aliases.add(path)
    return {alias for alias in aliases if _safe_alias(alias)}


def _raw_endpoint(edge: dict[str, Any], keys: tuple[str, ...]) -> object:
    for key in keys:
        if edge.get(key) not in (None, ""):
            return edge[key]
    return ""


def _classify_external_locator(locator: str) -> tuple[str, str, str] | None:
    lowered = locator.lower()
    if _ROUTE_RE.fullmatch(locator) or _ROUTE_METHOD_RE.fullmatch(locator):
        return "route", locator, ""
    if _DATA_REF_RE.fullmatch(locator):
        return lowered.split(":", 1)[0], locator, ""
    if _METHOD_RE.fullmatch(locator):
        return "method_reference", locator.lstrip("\\"), ""
    if _FQCN_RE.fullmatch(locator) or (
        _DOTTED_NAME_RE.fullmatch(locator) and not lowered.endswith(_SOURCE_SUFFIXES)
    ):
        return "external_class", locator.lstrip("\\"), ""
    if _BARE_SYMBOL_RE.fullmatch(locator):
        return "external_symbol", locator, ""
    if lowered.endswith(_SOURCE_SUFFIXES):
        normalized = _normalized_path(locator)
        if normalized and not _is_unsafe_path(locator):
            return "file", PurePosixPath(normalized).name, normalized
    return None


def _external_node(locator: str, classified: tuple[str, str, str]) -> dict[str, Any]:
    kind, label, path = classified
    return {
        "kind": kind,
        "name": _bounded_label(label),
        "path": _bounded_label(path),
        "external": True,
        "inferred_from_edge": True,
    }


def _explicit_values(
    entity: dict[str, Any], keys: tuple[str, ...]
) -> tuple[bool, list[object]]:
    values = [entity[key] for key in keys if key in entity and entity[key] is not None]
    return bool(values), values


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
    raw_node_entries = raw_nodes_value if isinstance(raw_nodes_value, list) else []
    raw_edge_entries = raw_edges_value if isinstance(raw_edges_value, list) else []
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

    node_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    nodes_omitted = 0
    nodes_deduplicated = 0
    for raw_node in raw_node_entries:
        if not isinstance(raw_node, dict):
            nodes_omitted += 1
            issue("invalid_node_shape", "node")
            continue
        semantic = _node_semantic_identity(raw_node)
        present, raw_explicit_values = _explicit_values(raw_node, ("id", "symbol_id"))
        if present:
            if any(not _valid_explicit_id(value) for value in raw_explicit_values):
                nodes_omitted += 1
                issue("invalid_node_id", "node", raw_explicit_values[0])
                continue
            explicit_values = {str(value) for value in raw_explicit_values}
            if len(explicit_values) != 1:
                nodes_omitted += 1
                issue("conflicting_node_ids", "node")
                continue
            explicit = next(iter(explicit_values))
        else:
            explicit = ""
        if semantic is None:
            nodes_omitted += 1
            issue("missing_node_identity", "node")
            continue
        derived_id = _hashed_id(_NODE_ID_PREFIX, semantic)
        if explicit.startswith(_NODE_ID_PREFIX) and explicit != derived_id:
            nodes_omitted += 1
            issue("invalid_reserved_node_id", "node", explicit)
            continue
        canonical_id = explicit or derived_id
        node = _canonical_node(raw_node, canonical_id)
        comparable = {
            key: value
            for key, value in node.items()
            if key not in {*_LOCATION_ONLY_KEYS, "symbol_id"}
        }
        node_groups[canonical_id].append({
            "id": canonical_id,
            "node": node,
            "semantic_key": _stable_json(comparable),
            "semantic": semantic,
            "synthetic": False,
        })

    candidates_by_id: dict[str, dict[str, Any]] = {}
    for canonical_id, group in sorted(node_groups.items()):
        semantic_keys = {candidate["semantic_key"] for candidate in group}
        if len(semantic_keys) > 1:
            nodes_omitted += len(group)
            for _candidate in group:
                issue("canonical_node_id_collision", "node", canonical_id)
            continue
        selected = min(group, key=lambda candidate: _stable_json(candidate["node"]))
        nodes_deduplicated += len(group) - 1
        selected["aliases"] = _node_aliases(selected["node"], canonical_id)
        candidates_by_id[canonical_id] = selected

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
    external_locators: dict[str, tuple[str, str, str]] = {}
    endpoint_unresolved = 0
    edges_omitted = 0
    edges_deduplicated = 0
    for raw_edge in raw_edge_entries:
        if not isinstance(raw_edge, dict):
            edges_omitted += 1
            issue("invalid_edge_shape", "edge")
            continue
        endpoint_states: list[tuple[str, str]] = []
        endpoint_failed = False
        for endpoint_value in (
            _raw_endpoint(raw_edge, _EDGE_SOURCE_KEYS),
            _raw_endpoint(raw_edge, _EDGE_TARGET_KEYS),
        ):
            if endpoint_value in (None, ""):
                endpoint_states.append(("invalid", "missing_edge_endpoint"))
                endpoint_failed = True
                endpoint_unresolved += 1
                continue
            if not isinstance(endpoint_value, str):
                endpoint_states.append(("invalid", "unrecognized_endpoint_locator"))
                endpoint_failed = True
                endpoint_unresolved += 1
                issue("unrecognized_endpoint_locator", "edge")
                continue
            if _utf8_size(endpoint_value) > MAX_ENDPOINT_LOCATOR_BYTES:
                endpoint_states.append(("invalid", "endpoint_locator_too_large"))
                endpoint_failed = True
                endpoint_unresolved += 1
                issue("endpoint_locator_too_large", "edge", endpoint_value)
                continue
            endpoint = endpoint_value.strip()
            if (
                endpoint != endpoint_value
                or _has_control(endpoint)
                or _is_unsafe_path(endpoint)
            ):
                endpoint_states.append(("invalid", "unsafe_endpoint_locator"))
                endpoint_failed = True
                endpoint_unresolved += 1
                issue("unsafe_endpoint_locator", "edge", endpoint_value)
                continue
            if endpoint in ambiguous_aliases:
                endpoint_states.append(("ambiguous", endpoint))
            elif endpoint in unique_aliases:
                endpoint_states.append(("resolved", unique_aliases[endpoint]))
            else:
                classified = _classify_external_locator(endpoint)
                if classified is None:
                    endpoint_states.append(("invalid", "unrecognized_endpoint_locator"))
                    endpoint_failed = True
                    endpoint_unresolved += 1
                    issue("unrecognized_endpoint_locator", "edge", endpoint)
                else:
                    endpoint_unresolved += 1
                    external_locators[endpoint] = classified
                    endpoint_states.append(("external", endpoint))
        parsed_edges.append({
            "edge": raw_edge,
            "source": endpoint_states[0],
            "target": endpoint_states[1],
            "endpoint_failed": endpoint_failed,
        })

    external_id_by_locator: dict[str, str] = {}
    for locator in sorted(external_locators):
        node = _external_node(locator, external_locators[locator])
        semantic = _node_semantic_identity(node)
        if semantic is None:
            issue("invalid_external_node", "node", locator)
            continue
        canonical_id = _hashed_id(_NODE_ID_PREFIX, semantic)
        existing = candidates_by_id.get(canonical_id)
        semantic_key = _stable_json(semantic)
        if existing is not None:
            if _stable_json(existing["semantic"]) != semantic_key:
                issue("external_node_id_collision", "node", locator)
                continue
            existing["aliases"].add(locator)
        else:
            candidates_by_id[canonical_id] = {
                "id": canonical_id,
                "node": {**node, "id": canonical_id},
                "semantic_key": semantic_key,
                "semantic": semantic,
                "aliases": {locator, canonical_id},
                "synthetic": True,
            }
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
    capacity_real = sum(
        1 for candidate in ranked_candidates[max_nodes:] if not candidate["synthetic"]
    )
    omitted_external = sum(
        1 for candidate in ranked_candidates[max_nodes:] if candidate["synthetic"]
    )
    nodes_omitted += capacity_real
    for candidate in ranked_candidates[max_nodes:]:
        issue(
            "node_capacity_exceeded",
            "external_node" if candidate["synthetic"] else "node",
            candidate["id"],
        )

    endpoint_after_resolved = 0
    endpoint_after_synthesized = 0
    endpoint_after_unresolved = 0
    endpoint_after_ambiguous = 0
    for parsed in parsed_edges:
        for state, value in (parsed["source"], parsed["target"]):
            if state == "ambiguous":
                endpoint_after_ambiguous += 1
            elif state == "resolved":
                if value in selected_ids:
                    endpoint_after_resolved += 1
                else:
                    endpoint_after_unresolved += 1
            elif state == "external":
                candidate_id = external_id_by_locator.get(value, "")
                if candidate_id in selected_ids:
                    endpoint_after_synthesized += 1
                else:
                    endpoint_after_unresolved += 1
            else:
                endpoint_after_unresolved += 1

    edge_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for parsed in parsed_edges:
        if parsed["endpoint_failed"]:
            edges_omitted += 1
            continue
        resolved: list[str] = []
        failed_reason = ""
        failed_value = ""
        for state, value in (parsed["source"], parsed["target"]):
            if state == "ambiguous":
                failed_reason, failed_value = "ambiguous_endpoint_alias", value
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

        raw_edge = parsed["edge"]
        source_id, target_id = resolved
        properties = {
            key: value
            for key, value in raw_edge.items()
            if key not in {"id", *_EDGE_SOURCE_KEYS, *_EDGE_TARGET_KEYS}
        }
        semantic_edge = {
            "kind": _first_text(raw_edge, "kind", "type").lower() or "relationship",
            "source_id": source_id,
            "target_id": target_id,
            "properties": properties,
        }
        derived_id = _hashed_id(_EDGE_ID_PREFIX, semantic_edge)
        present, raw_explicit_values = _explicit_values(raw_edge, ("id",))
        if present:
            explicit = raw_explicit_values[0]
            if not _valid_explicit_id(explicit):
                edges_omitted += 1
                issue("invalid_edge_id", "edge", explicit)
                continue
            explicit_id = str(explicit)
            if explicit_id.startswith(_EDGE_ID_PREFIX) and explicit_id != derived_id:
                edges_omitted += 1
                issue("invalid_reserved_edge_id", "edge", explicit_id)
                continue
            final_id = explicit_id
        else:
            final_id = derived_id
        edge = dict(raw_edge)
        edge["source_id"] = source_id
        edge["target_id"] = target_id
        edge["id"] = final_id
        edge_groups[final_id].append({
            "edge": edge,
            "semantic_key": _stable_json(semantic_edge),
        })

    emitted_edges: list[dict[str, Any]] = []
    for final_id, group in sorted(edge_groups.items()):
        semantic_keys = {candidate["semantic_key"] for candidate in group}
        if len(semantic_keys) > 1:
            edges_omitted += len(group)
            for _candidate in group:
                issue("edge_id_collision", "edge", final_id)
            continue
        emitted_edges.append(
            min(group, key=lambda candidate: _stable_json(candidate["edge"]))["edge"]
        )
        edges_deduplicated += len(group) - 1

    # Never rewrite legacy evidence. In particular, invalid non-dict entries
    # remain byte-for-byte representable for local callers and diagnostics.
    graph["nodes"] = emitted_nodes
    graph["relationships"] = emitted_edges
    return {
        "nodes_input": len(raw_node_entries),
        "nodes_emitted": len(emitted_nodes),
        "nodes_synthesized": len(selected_external_ids),
        "nodes_omitted": nodes_omitted,
        "nodes_deduplicated": nodes_deduplicated,
        "external_nodes_omitted": omitted_external,
        "edges_input": len(raw_edge_entries),
        "edges_emitted": len(emitted_edges),
        "edges_omitted": edges_omitted,
        "edges_deduplicated": edges_deduplicated,
        "duplicate_edges_omitted": edges_deduplicated,
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
    canonicalization_omitted = bool(
        canonicalization["nodes_omitted"]
        or canonicalization["external_nodes_omitted"]
        or canonicalization["edges_omitted"]
    )
    if canonicalization["edges_emitted"] == 0:
        quality = "inventory_only"
        reason = (
            "no_relationships_extracted"
            if canonicalization["edges_input"] == 0
            else "canonicalization_omissions"
        )
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
