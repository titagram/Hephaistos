from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from hermes_cli.gnothi.collectors.base import (
    CollectorContext,
    CollectorResult,
    fingerprint_payload,
)
from hermes_cli.gnothi.contract import stable_id
from hermes_cli.gnothi.redaction import redact_value, safe_exception_class
from hermes_cli.hades_backend_jobs import execute_job

MAX_FILES = 10_000
MAX_BYTES = 2_000_000
MAX_SYMBOLS = 5_000
MAX_EDGES = 10_000
_SOURCE_SUFFIXES = frozenset(
    {
        ".c", ".cc", ".cpp", ".cs", ".go", ".h", ".hpp", ".java",
        ".js", ".jsx", ".kt", ".php", ".py", ".rb", ".rs", ".sh",
        ".swift", ".ts", ".tsx",
    }
)
_IGNORED_DIRS = frozenset({".git", ".venv", "venv", "node_modules", "dist", "build"})


def _source_path(path: str | Path) -> bool:
    value = Path(path)
    return value.suffix.lower() in _SOURCE_SUFFIXES and not any(
        part in _IGNORED_DIRS for part in value.parts
    )


def _probe_source(context: CollectorContext) -> str:
    rows = []
    root = context.workspace_root
    for directory, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in _IGNORED_DIRS)
        directory_path = Path(directory)
        for filename in sorted(filenames):
            path = directory_path / filename
            relative = path.relative_to(root)
            if not _source_path(relative):
                continue
            stat = path.stat()
            rows.append((relative.as_posix(), stat.st_size, stat.st_mtime_ns))
            if len(rows) >= MAX_FILES:
                return fingerprint_payload({"head": context.head_commit, "files": rows})
    return fingerprint_payload({"head": context.head_commit, "files": rows})


def _fingerprint(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
) -> str:
    stable_nodes = [
        {
            key: value
            for key, value in node.items()
            if key not in {"verified_at"}
        }
        for node in nodes
    ]
    encoded = json.dumps(
        {"nodes": stable_nodes, "edges": edges, "evidence": evidence},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _result(
    *,
    context: CollectorContext,
    status: str,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    error_code: str | None = None,
) -> CollectorResult:
    return CollectorResult(
        name="source",
        status=status,
        nodes=nodes,
        edges=edges,
        evidence=evidence,
        fingerprint=_fingerprint(nodes, edges, evidence),
        verified_at=context.collected_at if status == "current" else None,
        error_code=error_code,
    )


def _evidence(
    *,
    schema: str,
    path: str,
    checksum: str,
    head_commit: str | None,
) -> dict[str, Any]:
    identity = {
        "schema": schema,
        "path": path,
        "checksum": checksum,
        "head_commit": head_commit,
    }
    return {
        "id": stable_id("evidence", identity),
        "kind": "source_checksum",
        **identity,
    }


def _node(
    *,
    node_id: str,
    kind: str,
    label: str,
    owner_class: str,
    owner_id: str,
    context: CollectorContext,
    evidence_refs: list[str],
    properties: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": node_id,
        "kind": kind,
        "label": label,
        "owner": {"class": owner_class, "id": owner_id},
        "generation_scope": context.generation_scope,
        "state": {
            "declared": True,
            "installed": True,
            "available": True,
            "active": True,
            "verified": True,
        },
        "evidence_refs": evidence_refs,
        "properties": properties or {},
        "verified_at": context.collected_at,
    }


def _edge(
    *,
    kind: str,
    source: str,
    target: str,
    evidence_refs: list[str],
    properties: dict[str, Any] | None = None,
) -> dict[str, Any]:
    identity = {"kind": kind, "source": source, "target": target}
    return {
        "id": stable_id("edge", identity),
        "kind": kind,
        "from": source,
        "to": target,
        "evidence_refs": evidence_refs,
        "properties": properties or {},
    }


class SourceCollector:
    name = "source"

    def probe_fingerprint(self, context: CollectorContext) -> str:
        return _probe_source(context)

    def collect(self, context: CollectorContext) -> CollectorResult:
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        evidence: list[dict[str, Any]] = []

        try:
            tree_result = execute_job(
                {
                    "capability": "sync_git_tree",
                    "payload": {
                        "max_files": MAX_FILES,
                        "max_bytes": MAX_BYTES,
                        "head_commit": context.head_commit,
                    },
                },
                workspace_root=context.workspace_root,
            )
            if tree_result.get("status") != "completed":
                raise RuntimeError("sync_git_tree did not complete")
            tree = tree_result.get("artifact")
            if not isinstance(tree, dict):
                raise TypeError("sync_git_tree returned no artifact")
            self._adapt_tree(context, tree, nodes, edges, evidence)
        except Exception as exc:
            return _result(
                context=context,
                status="partial",
                nodes=nodes,
                edges=edges,
                evidence=evidence,
                error_code=safe_exception_class(exc),
            )

        try:
            graph_result = execute_job(
                {
                    "capability": "populate_backend_ast",
                    "payload": {
                        "max_files": MAX_FILES,
                        "max_symbols": MAX_SYMBOLS,
                        "max_edges": MAX_EDGES,
                        "head_commit": context.head_commit,
                    },
                },
                workspace_root=context.workspace_root,
            )
            if graph_result.get("status") != "completed":
                raise RuntimeError("populate_backend_ast did not complete")
            graph = graph_result.get("artifact")
            if not isinstance(graph, dict):
                raise TypeError("populate_backend_ast returned no artifact")
            self._adapt_graph(context, graph, nodes, edges, evidence)
        except Exception as exc:
            return _result(
                context=context,
                status="partial",
                nodes=nodes,
                edges=edges,
                evidence=evidence,
                error_code=safe_exception_class(exc),
            )

        result = _result(
            context=context,
            status="current",
            nodes=nodes,
            edges=edges,
            evidence=evidence,
        )
        result.fingerprint = self.probe_fingerprint(context)
        return result

    @staticmethod
    def _adapt_tree(
        context: CollectorContext,
        tree: dict[str, Any],
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        evidence: list[dict[str, Any]],
    ) -> None:
        schema = str(tree.get("schema") or "hades.git_tree.v1")
        workspace_checksum = _probe_source(context)
        workspace_evidence = _evidence(
            schema=schema,
            path=".",
            checksum=workspace_checksum,
            head_commit=context.head_commit,
        )
        evidence.append(workspace_evidence)
        workspace_id = stable_id(
            "workspace",
            {"name": context.workspace_root.name},
        )
        nodes.append(
            _node(
                node_id=workspace_id,
                kind="workspace",
                label=context.workspace_root.name,
                owner_class="workspace",
                owner_id=workspace_id,
                context=context,
                evidence_refs=[workspace_evidence["id"]],
                properties={"collector": "source", "schema": schema},
            )
        )

        for row in tree.get("files", []):
            if not isinstance(row, dict):
                continue
            path = str(row.get("path") or "")
            checksum = str(row.get("sha256") or "")
            if not path or not checksum or not _source_path(path):
                continue
            file_evidence = _evidence(
                schema=schema,
                path=path,
                checksum=checksum,
                head_commit=context.head_commit,
            )
            evidence.append(file_evidence)
            file_id = stable_id("source_file", {"path": path})
            nodes.append(
                _node(
                    node_id=file_id,
                    kind="source_file",
                    label=path,
                    owner_class="workspace",
                    owner_id=workspace_id,
                    context=context,
                    evidence_refs=[file_evidence["id"]],
                    properties={
                        "collector": "source",
                        "path": path,
                        "bytes": int(row.get("bytes") or 0),
                    },
                )
            )
            edges.append(
                _edge(
                    kind="contains",
                    source=workspace_id,
                    target=file_id,
                    evidence_refs=[file_evidence["id"]],
                )
            )

    @staticmethod
    def _adapt_graph(
        context: CollectorContext,
        graph: dict[str, Any],
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        evidence: list[dict[str, Any]],
    ) -> None:
        workspace_node = next(node for node in nodes if node["kind"] == "workspace")
        workspace_id = workspace_node["id"]
        file_nodes = {
            str(node.get("properties", {}).get("path")): node
            for node in nodes
            if node["kind"] == "source_file"
        }
        evidence_by_path = {
            str(item.get("path")): str(item.get("id"))
            for item in evidence
            if item.get("path") not in (None, ".")
        }
        workspace_evidence = workspace_node["evidence_refs"]
        schema = str(graph.get("schema") or "hades.code_graph.v1")
        graph_rows = graph.get("nodes")
        if not isinstance(graph_rows, list):
            graph_rows = graph.get("symbols", [])

        endpoint_ids: dict[str, str] = {}
        name_ids: dict[str, list[str]] = {}
        for row in graph_rows:
            if not isinstance(row, dict):
                continue
            canonical_id = str(row.get("id") or "")
            name = str(row.get("label") or row.get("name") or canonical_id)
            source_kind = str(row.get("kind") or row.get("type") or "symbol")
            properties = row.get("properties") if isinstance(row.get("properties"), dict) else {}
            path = str(row.get("path") or properties.get("path") or "")
            if not name or (path and not _source_path(path)):
                continue
            identity = {
                "canonical_id": canonical_id or None,
                "kind": source_kind,
                "name": name,
                "path": path or None,
            }
            node_id = stable_id("source_symbol", identity)
            if canonical_id:
                endpoint_ids[canonical_id] = node_id
            name_ids.setdefault(name, []).append(node_id)
            refs = [evidence_by_path[path]] if path in evidence_by_path else workspace_evidence
            safe_properties, _ = redact_value(
                {
                    "collector": "source",
                    "source_schema": schema,
                    "source_kind": source_kind,
                    "path": path or None,
                    "line": row.get("line") or properties.get("line"),
                },
                workspace_root=context.workspace_root,
            )
            nodes.append(
                _node(
                    node_id=node_id,
                    kind="symbol",
                    label=name,
                    owner_class="workspace",
                    owner_id=workspace_id,
                    context=context,
                    evidence_refs=refs,
                    properties=safe_properties,
                )
            )
            parent_id = file_nodes[path]["id"] if path in file_nodes else workspace_id
            edges.append(
                _edge(
                    kind="contains",
                    source=parent_id,
                    target=node_id,
                    evidence_refs=refs,
                )
            )

        relationship_rows = graph.get("relationships")
        if not isinstance(relationship_rows, list):
            relationship_rows = graph.get("edges", [])
        for row in relationship_rows:
            if not isinstance(row, dict):
                continue
            raw_source = str(row.get("source_id") or row.get("from") or "")
            raw_target = str(row.get("target_id") or row.get("to") or "")
            source_id = endpoint_ids.get(raw_source)
            target_id = endpoint_ids.get(raw_target)
            if source_id is None and len(name_ids.get(raw_source, [])) == 1:
                source_id = name_ids[raw_source][0]
            if target_id is None and len(name_ids.get(raw_target, [])) == 1:
                target_id = name_ids[raw_target][0]
            if not source_id or not target_id:
                continue
            kind = str(row.get("kind") or row.get("type") or "relates_to")
            refs = sorted(
                {
                    *next(node for node in nodes if node["id"] == source_id)["evidence_refs"],
                    *next(node for node in nodes if node["id"] == target_id)["evidence_refs"],
                }
            )
            edges.append(
                _edge(
                    kind=kind,
                    source=source_id,
                    target=target_id,
                    evidence_refs=refs,
                    properties={"collector": "source", "source_schema": schema},
                )
            )
