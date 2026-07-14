from __future__ import annotations

import hashlib
import json
from typing import Any

ORGANISM_SCHEMA = "hades.organism_graph.v1"
ORGANISM_CONTRACT_VERSION = "hades.gnothi_seauton.v1"
GENERATION_SCOPES = frozenset({"stable", "candidate", "historical"})


def stable_id(kind: str, identity: dict[str, Any]) -> str:
    """Return a deterministic ID from a semantic identity property bag."""

    encoded = json.dumps(
        identity,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]
    return f"{kind}:{digest}"


def new_artifact(
    *,
    revision_id: str,
    generation_id: str,
    generation_scope: str,
    head_commit: str | None,
    collected_at: str,
) -> dict[str, Any]:
    if generation_scope not in GENERATION_SCOPES:
        raise ValueError(f"unsupported generation scope: {generation_scope}")

    return {
        "schema": ORGANISM_SCHEMA,
        "organism_contract": {
            "version": ORGANISM_CONTRACT_VERSION,
            "revision_id": revision_id,
            "generation": {"id": generation_id, "scope": generation_scope},
            "source": {"head_commit": head_commit},
            "collected_at": collected_at,
            "status": "building",
            "coverage": {},
        },
        "nodes": [],
        "edges": [],
        "redactions": 0,
        "truncated": False,
        "raw_source_included": False,
        "retention_class": "organism_metadata",
    }


def add_node(
    artifact: dict[str, Any],
    *,
    node_id: str,
    kind: str,
    label: str,
    owner_class: str,
    owner_id: str,
    generation_scope: str | None = None,
    state: dict[str, bool] | None = None,
    evidence_refs: list[str] | None = None,
    properties: dict[str, Any] | None = None,
    verified_at: str | None = None,
) -> None:
    scope = generation_scope or artifact["organism_contract"]["generation"]["scope"]
    artifact["nodes"].append(
        {
            "id": node_id,
            "kind": kind,
            "label": label,
            "owner": {"class": owner_class, "id": owner_id},
            "generation_scope": scope,
            "state": state or {},
            "evidence_refs": evidence_refs or [],
            "properties": properties or {},
            "verified_at": verified_at,
        }
    )


def add_edge(
    artifact: dict[str, Any],
    *,
    edge_id: str,
    kind: str,
    source: str,
    target: str,
    evidence_refs: list[str] | None = None,
    properties: dict[str, Any] | None = None,
) -> None:
    artifact["edges"].append(
        {
            "id": edge_id,
            "kind": kind,
            "from": source,
            "to": target,
            "evidence_refs": evidence_refs or [],
            "properties": properties or {},
        }
    )


def validate_artifact(artifact: dict[str, Any]) -> list[str]:
    """Return contract violations without mutating the supplied artifact."""

    errors: list[str] = []
    if artifact.get("schema") != ORGANISM_SCHEMA:
        errors.append("invalid_schema")

    contract = artifact.get("organism_contract")
    if (
        not isinstance(contract, dict)
        or contract.get("version") != ORGANISM_CONTRACT_VERSION
    ):
        errors.append("invalid_organism_contract")

    node_rows = artifact.get("nodes", [])
    if not isinstance(node_rows, list):
        node_rows = []
        errors.append("invalid_nodes")
    nodes = {
        str(row.get("id")): row
        for row in node_rows
        if isinstance(row, dict) and row.get("id")
    }
    if len(nodes) != len(node_rows):
        errors.append("duplicate_or_invalid_node_id")

    for node_id, node in nodes.items():
        state = node.get("state") if isinstance(node.get("state"), dict) else {}
        evidence = (
            node.get("evidence_refs")
            if isinstance(node.get("evidence_refs"), list)
            else []
        )
        if state.get("verified") is True and (
            not evidence or not node.get("verified_at")
        ):
            errors.append(f"verified_without_current_evidence:{node_id}")

    edge_rows = artifact.get("edges", [])
    if not isinstance(edge_rows, list):
        return [*errors, "invalid_edges"]
    for edge in edge_rows:
        if not isinstance(edge, dict):
            errors.append("invalid_edge")
            continue
        edge_id = str(edge.get("id") or "")
        source = str(edge.get("from") or "")
        target = str(edge.get("to") or "")
        if source not in nodes or target not in nodes:
            errors.append(f"dangling_edge:{edge_id}")
            continue
        if nodes[source].get("generation_scope") != nodes[target].get(
            "generation_scope"
        ):
            errors.append(f"cross_generation_edge:{edge_id}")

    return errors
