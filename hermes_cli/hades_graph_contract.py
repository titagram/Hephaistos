from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote_to_bytes

from hermes_cli.hades_index.inventory import (
    inventory_coverage,
    merge_inventory_coverage,
    promote_graph_inventories,
)

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
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@#+-]*$")
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_IDENTITY_FINGERPRINT_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_FQCN_RE = re.compile(r"^\\?[A-Za-z_][A-Za-z0-9_]*(?:\\[A-Za-z_][A-Za-z0-9_]*)+$")
_DOTTED_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+$")
_BARE_SYMBOL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_METHOD_RE = re.compile(
    r"^(?:\\?[A-Za-z_][A-Za-z0-9_]*(?:[\\.][A-Za-z_][A-Za-z0-9_]*)*)"
    r"(?:::|@)[A-Za-z_][A-Za-z0-9_]*$"
)
_ROUTE_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.:-]*$")
_ROUTE_PATH_RE = re.compile(
    r"^/(?:[A-Za-z0-9._~:@%+\-]+|\{[A-Za-z_][A-Za-z0-9_]*\??\}|/)*$"
)
_ROUTE_METHODS = frozenset({
    "GET",
    "POST",
    "PUT",
    "PATCH",
    "DELETE",
    "OPTIONS",
    "HEAD",
    "ANY",
})
_DATA_REF_RE = re.compile(
    r"^(?:table|model|entity|column|schema|collection|class|symbol):[A-Za-z0-9_.:-]+$",
    re.IGNORECASE,
)
_SOURCE_SUFFIXES = (".php", ".py", ".ts", ".tsx", ".js", ".jsx", ".sql")
_SOURCE_REFERENCE_PREFIXES = frozenset({"test"})


def _stable_json(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )


def _deduplicate_omissions(*collections: object) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for collection in collections:
        if not isinstance(collection, list):
            continue
        for item in collection:
            if isinstance(item, dict):
                unique.setdefault(_stable_json(item), dict(item))
    return [unique[key] for key in sorted(unique)]


def _coverage_file_counts(
    candidates: list[Path],
    omissions: list[dict[str, Any]],
) -> tuple[int, int, int, int]:
    failed_reasons_by_path: dict[str, set[str]] = defaultdict(set)
    for item in omissions:
        path = _clean_text(item.get("path")).replace("\\", "/")
        if path:
            failed_reasons_by_path[path.removeprefix("./")].add(
                _clean_text(item.get("reason"))
            )
    failed_paths_by_name: dict[str, list[str]] = defaultdict(list)
    for failed_path in failed_reasons_by_path:
        failed_paths_by_name[PurePosixPath(failed_path).name].append(failed_path)

    def failed_path_for(candidate: Path) -> str:
        candidate_path = candidate.as_posix()
        for failed_path in failed_paths_by_name.get(candidate.name, []):
            if candidate_path == failed_path or candidate_path.endswith(
                f"/{failed_path}"
            ):
                return failed_path
        return ""

    candidate_failures = [failed_path_for(candidate) for candidate in candidates]
    failed_candidate_paths = {path for path in candidate_failures if path}
    analyzed = sum(1 for path in candidate_failures if not path)
    failed = len(failed_reasons_by_path)
    total = len(candidates) + failed - len(failed_candidate_paths)
    budget_omitted = sum(
        1
        for reasons in failed_reasons_by_path.values()
        if reasons & {"file_budget_exceeded", "byte_budget_exceeded"}
    )
    return total, analyzed, failed, budget_omitted


def _hashed_id(prefix: str, value: object) -> str:
    return prefix + hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _locator_fingerprint(value: object) -> str:
    return hashlib.sha256(str(value).encode("utf-8", errors="replace")).hexdigest()[:16]


def _clean_text(value: object) -> str:
    return str(value).strip() if value not in (None, "") else ""


def _utf8_size(value: str) -> int:
    return len(value.encode("utf-8", errors="replace"))


def _has_control(value: str) -> bool:
    return any(unicodedata.category(character) in {"Cc", "Cf"} for character in value)


def _has_dot_segment(value: str) -> bool:
    return bool(re.search(r"(?:^|[/:\\])\.{1,2}(?:$|[/:\\])", value))


def _unsafe_path_like_reason(
    value: str,
    *,
    max_bytes: int,
    reject_separators: bool,
) -> str | None:
    """Classify unsafe identifier/locator text before semantic parsing.

    Explicit IDs use ``reject_separators=True`` because they are opaque
    identifiers, never filesystem or route locators. Endpoint locators allow
    separators only so their dedicated route/file grammar can validate them.
    Traversal, file URIs, absolute filesystem paths, controls, and oversized
    values are rejected consistently in both modes.
    """

    if not value or value != value.strip():
        return "empty_or_whitespace"
    if _utf8_size(value) > max_bytes:
        return "too_large"
    if _has_control(value):
        return "control_character"
    lowered = value.lower()
    if lowered.startswith("file:"):
        return "file_uri"
    if _has_dot_segment(value):
        return "dot_segment"
    if reject_separators and ("/" in value or "\\" in value):
        return "path_separator"
    if (
        value.startswith(("/", "//", "\\\\"))
        or _WINDOWS_DRIVE_RE.match(value)
        or (value.startswith("\\") and lowered.endswith(_SOURCE_SUFFIXES))
    ):
        return "absolute_path"
    return None


def _is_unsafe_path(value: str) -> bool:
    return (
        _unsafe_path_like_reason(
            value,
            max_bytes=MAX_ENDPOINT_LOCATOR_BYTES,
            reject_separators=False,
        )
        is not None
    )


def _valid_explicit_id(value: object) -> bool:
    return bool(
        isinstance(value, str)
        and _unsafe_path_like_reason(
            value,
            max_bytes=MAX_EXPLICIT_ID_BYTES,
            reject_separators=True,
        )
        is None
        and _ID_RE.fullmatch(value)
    )


def _bounded_label(value: str) -> str:
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) <= MAX_PLACEHOLDER_LABEL_BYTES:
        return value
    return encoded[:MAX_PLACEHOLDER_LABEL_BYTES].decode("utf-8", errors="ignore")


def _canonical_full_path(value: object) -> str:
    """Normalize a full path/URI for private hashing, never for publication."""

    raw = _clean_text(value).replace("\\", "/")
    if not raw:
        return ""
    lowered = raw.lower()
    is_file_uri = lowered.startswith("file:/")
    if is_file_uri:
        raw = raw[5:]
    is_unc = not is_file_uri and raw.startswith("//")
    drive = ""
    if re.match(r"^[A-Za-z]:/", raw):
        drive, raw = raw[:2].lower(), raw[2:]
    absolute = raw.startswith("/")
    parts: list[str] = []
    for part in raw.split("/"):
        if not part or part == ".":
            continue
        if part == "..":
            if parts and parts[-1] != "..":
                parts.pop()
            elif not absolute:
                parts.append(part)
            continue
        parts.append(part)
    normalized = "/".join(parts)
    if absolute:
        normalized = "/" + normalized
    if drive:
        normalized = drive + normalized
    if is_unc:
        normalized = "unc://" + normalized.lstrip("/")
    if is_file_uri:
        normalized = (
            "file://" + ("" if normalized.startswith("/") else "/") + normalized
        )
    return normalized or ("/" if absolute else "")


def _path_identity_token(value: object) -> str:
    normalized = _canonical_full_path(value)
    if not normalized:
        return ""
    digest = hashlib.sha256(normalized.encode("utf-8", errors="replace")).hexdigest()
    return f"sha256:{digest}"


def _safe_path_basename(value: object) -> str:
    normalized = _canonical_full_path(value)
    basename = PurePosixPath(normalized).name if normalized else ""
    basename = re.sub(r"[^A-Za-z0-9._@+ -]", "_", basename)
    return _bounded_label(basename)


def _normalized_path(value: object) -> str:
    """Return the bounded display basename, not a filesystem locator."""

    return _safe_path_basename(value)


def _identity_fingerprint(node: dict[str, Any]) -> tuple[bool, str]:
    properties = node.get("properties")
    if not isinstance(properties, dict) or "identity_fingerprint" not in properties:
        return False, ""
    value = properties["identity_fingerprint"]
    if not isinstance(value, str) or not _IDENTITY_FINGERPRINT_RE.fullmatch(value):
        return True, ""
    return True, value


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
    fingerprint_present, fingerprint = _identity_fingerprint(node)
    if fingerprint_present and not fingerprint:
        return None
    path = (
        fingerprint
        or _clean_text(node.get("_private_path_identity"))
        or _path_identity_token(
            node.get("path") or node.get("source_path") or node.get("file")
        )
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


def _canonical_node(
    raw_node: dict[str, Any],
    canonical_id: str,
    *,
    identity_fingerprint: str = "",
) -> dict[str, Any]:
    node = {
        key: value for key, value in raw_node.items() if not key.startswith("_private_")
    }
    node["id"] = canonical_id
    if identity_fingerprint:
        properties = node.get("properties")
        properties = dict(properties) if isinstance(properties, dict) else {}
        properties["identity_fingerprint"] = identity_fingerprint
        node["properties"] = properties
    for key in ("path", "source_path", "file"):
        if key in node and node[key] not in (None, ""):
            node[key] = _normalized_path(node[key])
    return node


def _safe_alias(value: object) -> str:
    if not isinstance(value, str) or value != value.strip() or not value:
        return ""
    if _utf8_size(value) > MAX_ENDPOINT_LOCATOR_BYTES or _has_control(value):
        return ""
    if (
        _unsafe_path_like_reason(
            value,
            max_bytes=MAX_ENDPOINT_LOCATOR_BYTES,
            reject_separators=False,
        )
        is not None
    ):
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
            _canonical_full_path(
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


def _classify_route_locator(locator: str) -> tuple[str, str, str] | None:
    lowered = locator.lower()
    if lowered.startswith("route_name:"):
        route_name = locator.split(":", 1)[1]
        return ("route", locator, "") if _ROUTE_NAME_RE.fullmatch(route_name) else None
    if not lowered.startswith("route:"):
        return None
    route_value = locator.split(":", 1)[1]
    method, separator, possible_path = route_value.partition(" ")
    if separator:
        if method.upper() not in _ROUTE_METHODS:
            return None
        route_path = possible_path
    elif route_value.startswith("/"):
        route_path = route_value
    else:
        return ("route", locator, "") if _ROUTE_NAME_RE.fullmatch(route_value) else None
    if (
        not route_path.startswith("/")
        or not _ROUTE_PATH_RE.fullmatch(route_path)
        or not _safe_route_path_encoding(route_path)
    ):
        return None
    return "route", locator, ""


def _safe_route_path_encoding(route_path: str) -> bool:
    candidate = route_path
    for depth in range(4):
        if _has_control(candidate) or _has_dot_segment(candidate):
            return False
        if re.search(r"%(?:2f|5c)", candidate, re.IGNORECASE):
            return False
        if re.search(r"%(?![0-9A-Fa-f]{2})", candidate):
            # A percent produced by decoding an explicit ``%25`` is data, not
            # a second malformed wire encoding. The original wire form was
            # already checked strictly at depth zero.
            return depth > 0
        try:
            decoded = unquote_to_bytes(candidate).decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return False
        if _has_control(decoded) or _has_dot_segment(decoded):
            return False
        if decoded == candidate:
            return True
        candidate = decoded
    return "%" not in candidate


def _valid_relative_source_path(locator: str) -> bool:
    if _is_unsafe_path(locator) or _utf8_size(locator) > MAX_ENDPOINT_LOCATOR_BYTES:
        return False
    normalized = locator.replace("\\", "/")
    if not normalized or ":" in normalized or normalized.startswith("/"):
        return False
    segments = normalized.split("/")
    if not segments or any(
        not segment
        or segment in {".", ".."}
        or not re.fullmatch(r"[A-Za-z0-9_@+ .-]+", segment)
        for segment in segments
    ):
        return False
    return normalized.lower().endswith(_SOURCE_SUFFIXES)


def _classify_external_locator(locator: str) -> tuple[str, str, str] | None:
    lowered = locator.lower()
    route = _classify_route_locator(locator)
    if route is not None:
        return route
    source_prefix, separator, source_path = locator.partition(":")
    if (
        separator
        and source_prefix.lower() in _SOURCE_REFERENCE_PREFIXES
        and _valid_relative_source_path(source_path)
    ):
        label = _safe_path_basename(source_path)
        return "file", label, label
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
        normalized = _safe_path_basename(locator)
        if normalized and _valid_relative_source_path(locator):
            return "file", normalized, normalized
    return None


def _external_node(locator: str, classified: tuple[str, str, str]) -> dict[str, Any]:
    kind, label, path = classified
    node = {
        "kind": kind,
        "name": _bounded_label(label),
        "path": _bounded_label(path),
        "external": True,
        "inferred_from_edge": True,
    }
    if kind == "file":
        node["_private_path_identity"] = _path_identity_token(locator)
    return node


def _explicit_values(
    entity: dict[str, Any], keys: tuple[str, ...]
) -> tuple[bool, list[object]]:
    values = [entity[key] for key in keys if key in entity and entity[key] is not None]
    return bool(values), values


def _canonicalize_graph(graph: dict[str, Any], *, max_nodes: int) -> dict[str, Any]:
    promoted_nodes = graph.pop("_canonical_declarations", None)
    raw_nodes_value = (
        promoted_nodes
        if isinstance(promoted_nodes, list)
        else graph.get("symbols")
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
        fingerprint_present, fingerprint = _identity_fingerprint(raw_node)
        if fingerprint_present and not fingerprint:
            nodes_omitted += 1
            issue("invalid_identity_fingerprint", "node")
            continue
        private_path = _clean_text(raw_node.get("_private_path_identity")) or (
            fingerprint
            or _path_identity_token(
                raw_node.get("path")
                or raw_node.get("source_path")
                or raw_node.get("file")
            )
        )
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
        node = _canonical_node(
            raw_node,
            canonical_id,
            identity_fingerprint=private_path,
        )
        comparable = {
            key: value
            for key, value in node.items()
            if key not in {*_LOCATION_ONLY_KEYS, "symbol_id"}
        }
        node_groups[canonical_id].append({
            "id": canonical_id,
            "node": node,
            "semantic_key": _stable_json({
                "semantic": semantic,
                "node": comparable,
            }),
            "semantic": semantic,
            "aliases": _node_aliases(raw_node, canonical_id),
            "synthetic": False,
            "kind": _first_text(raw_node, "kind", "type").lower() or "symbol",
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
        selected["aliases"] = set().union(
            *(candidate["aliases"] for candidate in group)
        )
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
                "node": _canonical_node(
                    node,
                    canonical_id,
                    identity_fingerprint=semantic["path"],
                ),
                "semantic_key": semantic_key,
                "semantic": semantic,
                "aliases": {locator, canonical_id},
                "synthetic": True,
                "kind": _first_text(node, "kind", "type").lower() or "symbol",
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
            0
            if not candidate["synthetic"]
            and candidate["kind"] in {"route", "http_endpoint", "endpoint", "test"}
            else 1
            if reference_counts[candidate["id"]]
            else 2
            if not candidate["synthetic"]
            else 3,
            -reference_counts[candidate["id"]],
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
    effective_omissions = _deduplicate_omissions(graph.get("omitted"), omitted)
    graph["omitted"] = effective_omissions
    private_inventory = graph.pop("_inventory_coverage", None)
    tests = graph.get("tests")
    private_test_inventory = (
        tests.pop("_inventory_coverage", None) if isinstance(tests, dict) else None
    )
    retained_inventory = inventory_coverage(
        routes_detected=graph.get("routes"),
        routes_retained=graph.get("routes"),
        tests_detected=tests.get("files") if isinstance(tests, dict) else None,
        tests_retained=tests.get("files") if isinstance(tests, dict) else None,
    )
    effective_inventory = merge_inventory_coverage(
        retained_inventory,
        private_inventory,
        private_test_inventory,
    )
    inventory_report = promote_graph_inventories(graph)
    canonicalization = _canonicalize_graph(
        graph,
        # ``max_symbols`` bounds extractor declarations, not the canonical
        # graph's inferred endpoint nodes. Keep a separate hard global cap so
        # small test/index jobs do not lose valid declarations to placeholders.
        max_nodes=int(payload.get("max_graph_nodes") or DEFAULT_MAX_GRAPH_NODES),
    )
    canonicalization.update(inventory_report)
    language = str(graph.get("language") or "unknown").strip().lower() or "unknown"
    graph_languages = graph.get("languages")
    languages = sorted(
        {
            str(item).strip().lower()
            for item in graph_languages
            if str(item).strip()
        }
    ) if isinstance(graph_languages, list) else [language]
    canonicalization_loss = bool(
        canonicalization["nodes_omitted"]
        or canonicalization["external_nodes_omitted"]
        or canonicalization["edges_omitted"]
    )
    bounded_input_loss = bool(graph.get("truncated")) or bool(effective_omissions)
    if canonicalization["edges_emitted"] == 0:
        quality = "inventory_only"
        if canonicalization_loss:
            reason = "canonicalization_omissions"
        elif bounded_input_loss:
            reason = "bounded_or_omitted_input"
        else:
            reason = "no_relationships_extracted"
    elif canonicalization_loss:
        quality, reason = "partial", "canonicalization_omissions"
    elif bounded_input_loss:
        quality, reason = "partial", "bounded_or_omitted_input"
    else:
        quality, reason = "full", None
    head = str(
        payload.get("head_commit") or payload.get("workspace_head_commit") or ""
    ).strip()
    branch = str(payload.get("branch") or payload.get("current_branch") or "").strip()
    files_total, files_analyzed, files_failed, files_budget_omitted = (
        _coverage_file_counts(candidates, effective_omissions)
    )
    routes_promoted = int(effective_inventory["routes_retained"])
    tests_promoted = int(effective_inventory["tests_retained"])
    routes_detected = int(effective_inventory["routes_detected"])
    tests_detected = int(effective_inventory["tests_detected"])
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
            "languages": languages or [language],
            "files_total": files_total,
            "files_analyzed": files_analyzed,
            "files_failed": files_failed,
            "files_budget_omitted": files_budget_omitted,
            "routes_promoted": routes_promoted,
            "routes_omitted": max(0, routes_detected - routes_promoted),
            "tests_promoted": tests_promoted,
            "tests_omitted": max(0, tests_detected - tests_promoted),
            "nodes_capacity_omitted": int(
                canonicalization["issue_reasons"].get("node_capacity_exceeded", 0)
            ),
        },
        "source": {"branch": branch or None, "head_commit": head or None},
    }
    return graph
