from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

from agent.redact import redact_sensitive_text
from hermes_cli.gnothi.redaction import redact_value
from hermes_cli.gnothi.store import (
    OrganismRevisionStore,
    legacy_profile_store_state,
)

MAX_RESULTS = 200
MAX_GRAPH_DEPTH = 4
MAX_GRAPH_EVIDENCE_REFS = 20
GRAPH_EDGE_KINDS = frozenset({"provides", "requires", "depends_on"})


def _public_text(value: object, *, limit: int = 500) -> str:
    """Return bounded text suitable for a local dashboard response."""
    safe, _ = redact_value(str(value if value is not None else ""))
    return redact_sensitive_text(str(safe), force=True, file_read=True)[:limit]


def _public_evidence_refs(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return sorted(
        {
            _public_text(ref, limit=256)
            for ref in value
            if isinstance(ref, (str, int, float)) and str(ref)
        }
    )[:MAX_GRAPH_EVIDENCE_REFS]


def _owner_class(node: dict[str, Any]) -> object:
    """Read the owner class from either raw graph or public-row schema."""
    public_owner_class = node.get("owner_class")
    if isinstance(public_owner_class, (str, int, float)):
        return public_owner_class
    owner = node.get("owner")
    return owner.get("class") if isinstance(owner, dict) else None


def _public_node(node: dict[str, Any]) -> dict[str, Any]:
    state = node.get("state")
    public_state = (
        {str(key): value for key, value in state.items() if isinstance(value, bool)}
        if isinstance(state, dict)
        else {}
    )
    return {
        "id": _public_text(node.get("id"), limit=256),
        "kind": _public_text(node.get("kind"), limit=128),
        "label": _public_text(node.get("label")),
        "owner_class": _public_text(_owner_class(node), limit=128),
        "generation_scope": _public_text(node.get("generation_scope"), limit=64),
        "state": public_state,
        "evidence_refs": _public_evidence_refs(node.get("evidence_refs")),
    }


def _public_edge(edge: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _public_text(edge.get("id"), limit=256),
        "kind": _public_text(edge.get("kind"), limit=128),
        "from": _public_text(edge.get("from"), limit=256),
        "to": _public_text(edge.get("to"), limit=256),
        "evidence_refs": _public_evidence_refs(edge.get("evidence_refs")),
    }


class OrganismQuery:
    def __init__(
        self,
        store: OrganismRevisionStore,
        *,
        artifact: dict[str, Any] | None = None,
    ):
        self.store = store
        self._artifact = artifact

    def status(self) -> dict[str, Any]:
        artifact = self.store.current()
        if not artifact:
            result = {"status": "missing", "actions": ["rebuild"]}
            legacy_state = legacy_profile_store_state()
            if legacy_state == "detected":
                result["diagnostics"] = ["legacy_profile_state_detected"]
            elif legacy_state == "unreadable":
                result["diagnostics"] = ["legacy_profile_state_unreadable"]
            return result
        contract = artifact["organism_contract"]
        coverage = contract.get("coverage", {})
        unknown = sorted(
            name for name, row in coverage.items() if row.get("status") in {"missing", "partial", "stale"}
        )
        return {
            "revision_id": contract.get("revision_id"),
            "generation_id": contract.get("generation", {}).get("id"),
            "status": contract.get("status"),
            "coverage": coverage,
            "counts": {"nodes": len(artifact.get("nodes", [])), "edges": len(artifact.get("edges", []))},
            "unknown_domains": unknown,
            "actions": ["rebuild"] if unknown else [],
        }

    def inspect(self, component: str) -> dict[str, Any]:
        artifact = self.store.current()
        if not artifact:
            return {"match": None, "status": "missing"}
        nodes = artifact.get("nodes", [])
        exact = next((node for node in nodes if str(node.get("id")) == component), None)
        if exact:
            return {"match": exact, "ambiguous": False}
        matches = [node for node in nodes if str(node.get("label", "")).casefold() == component.casefold()]
        if len(matches) == 1:
            return {"match": matches[0], "ambiguous": False}
        return {"match": None, "ambiguous": len(matches) > 1, "matches": matches[:MAX_RESULTS]}

    def explain(self, capability: str) -> dict[str, Any]:
        artifact = self.store.current()
        if not artifact:
            return {"nodes": [], "edges": [], "blockers": [], "truncated": False}
        match = self.inspect(capability).get("match")
        if not match:
            return {"nodes": [], "edges": [], "blockers": [], "truncated": False}
        nodes = {str(node.get("id")): node for node in artifact.get("nodes", [])}
        adjacency = defaultdict(list)
        for edge in artifact.get("edges", []):
            if edge.get("kind") not in {"provides", "requires", "depends_on"}:
                continue
            adjacency[str(edge.get("from"))].append(edge)
            adjacency[str(edge.get("to"))].append(edge)
        seen = {str(match["id"])}
        chosen_edges = []
        queue = deque([(str(match["id"]), 0)])
        while queue and len(seen) < MAX_RESULTS:
            node_id, depth = queue.popleft()
            if depth >= 4:
                continue
            for edge in adjacency[node_id]:
                if edge not in chosen_edges and len(chosen_edges) < MAX_RESULTS:
                    chosen_edges.append(edge)
                other = str(edge.get("to")) if str(edge.get("from")) == node_id else str(edge.get("from"))
                if other in nodes and other not in seen:
                    seen.add(other)
                    queue.append((other, depth + 1))
        selected = [nodes[node_id] for node_id in sorted(seen)]
        blockers = [node for node in selected if node.get("state", {}).get("available") is False or node.get("state", {}).get("degraded") is True]
        return {"nodes": selected, "edges": chosen_edges, "blockers": blockers, "truncated": len(seen) >= MAX_RESULTS}

    def subgraph(
        self,
        *,
        root_id: str | None,
        depth: int,
        limit: int,
        kinds: frozenset[str],
        search: str,
    ) -> dict[str, Any]:
        """Return a deterministic, bounded public neighborhood of one revision."""
        if type(depth) is not int or not 0 <= depth <= MAX_GRAPH_DEPTH:
            raise ValueError("invalid graph depth")
        if type(limit) is not int or not 1 <= limit <= MAX_RESULTS:
            raise ValueError("invalid graph limit")
        if not isinstance(kinds, frozenset) or not all(
            isinstance(kind, str) for kind in kinds
        ):
            raise ValueError("invalid graph kinds")
        if not isinstance(search, str):
            raise ValueError("invalid graph search")

        artifact = self._artifact if self._artifact is not None else self.store.current()
        if not artifact:
            return {
                "nodes": [],
                "edges": [],
                "blockers": [],
                "total_nodes": 0,
                "total_edges": 0,
                "truncated": False,
            }

        raw_nodes = artifact.get("nodes")
        nodes = {
            node_id: node
            for node in (raw_nodes if isinstance(raw_nodes, list) else [])
            if isinstance(node, dict)
            if isinstance(node.get("id"), str)
            if (node_id := str(node["id"]))
        }
        raw_edges = artifact.get("edges")
        dependency_edges = sorted(
            (
                edge
                for edge in (raw_edges if isinstance(raw_edges, list) else [])
                if isinstance(edge, dict)
                if edge.get("kind") in GRAPH_EDGE_KINDS
                if isinstance(edge.get("from"), str)
                if isinstance(edge.get("to"), str)
                if str(edge["from"]) in nodes and str(edge["to"]) in nodes
            ),
            key=lambda edge: (
                str(edge.get("id") or ""),
                str(edge.get("kind") or ""),
                str(edge.get("from") or ""),
                str(edge.get("to") or ""),
            ),
        )

        if root_id is None:
            traversal = sorted(nodes)
        elif root_id not in nodes:
            traversal = []
        else:
            adjacency: dict[str, list[str]] = defaultdict(list)
            for edge in dependency_edges:
                source, target = str(edge["from"]), str(edge["to"])
                adjacency[source].append(target)
                adjacency[target].append(source)
            for neighbours in adjacency.values():
                neighbours.sort()

            seen = {root_id}
            queue = deque([(root_id, 0)])
            traversal = []
            while queue:
                node_id, node_depth = queue.popleft()
                traversal.append(node_id)
                if node_depth >= depth:
                    continue
                for neighbour in adjacency[node_id]:
                    if neighbour not in seen:
                        seen.add(neighbour)
                        queue.append((neighbour, node_depth + 1))

        needle = search.casefold()

        def matches(node_id: str) -> bool:
            node = nodes[node_id]
            if kinds and str(node.get("kind")) not in kinds:
                return False
            return not needle or (
                needle in node_id.casefold()
                or needle in str(node.get("label") or "").casefold()
            )

        matching_ids = [node_id for node_id in traversal if matches(node_id)]
        matching_set = set(matching_ids)
        matching_edges = [
            edge
            for edge in dependency_edges
            if str(edge["from"]) in matching_set and str(edge["to"]) in matching_set
        ]

        chosen_ids = matching_ids[:limit]
        chosen_set = set(chosen_ids)
        chosen_edges = [
            edge
            for edge in matching_edges
            if str(edge["from"]) in chosen_set and str(edge["to"]) in chosen_set
        ][:limit]
        public_nodes = [_public_node(nodes[node_id]) for node_id in sorted(chosen_ids)]
        public_edges = [_public_edge(edge) for edge in chosen_edges]
        blockers = [
            node
            for node in public_nodes
            if node["state"].get("available") is False
            or node["state"].get("degraded") is True
        ]
        return {
            "nodes": public_nodes,
            "edges": public_edges,
            "blockers": blockers,
            "total_nodes": len(matching_ids),
            "total_edges": len(matching_edges),
            "truncated": len(matching_ids) > len(chosen_ids)
            or len(matching_edges) > len(chosen_edges),
        }

    def diff(self, a: str, b: str) -> dict[str, Any]:
        left, right = self.store.get(a), self.store.get(b)
        if not left or not right:
            raise ValueError("unknown organism revision")
        old = {str(node.get("id")): node for node in left.get("nodes", [])}
        new = {str(node.get("id")): node for node in right.get("nodes", [])}
        added = [new[key] for key in sorted(new.keys() - old.keys())]
        removed = [old[key] for key in sorted(old.keys() - new.keys())]
        changed = [
            {"id": key, "before": old[key].get("state", {}), "after": new[key].get("state", {})}
            for key in sorted(old.keys() & new.keys())
            if old[key].get("state", {}) != new[key].get("state", {})
        ]
        edge_old = {(e.get("kind"), e.get("from"), e.get("to")) for e in left.get("edges", [])}
        edge_new = {(e.get("kind"), e.get("from"), e.get("to")) for e in right.get("edges", [])}
        all_dependency_changes = sorted(edge_old ^ edge_new)
        dependency_changes = all_dependency_changes[:MAX_RESULTS]
        quality_changes = []
        if left["organism_contract"].get("status") != right["organism_contract"].get("status"):
            quality_changes.append({"before": left["organism_contract"].get("status"), "after": right["organism_contract"].get("status")})
        left_coverage = left["organism_contract"].get("coverage", {})
        right_coverage = right["organism_contract"].get("coverage", {})
        coverage_changes = []
        for domain in sorted(set(left_coverage) | set(right_coverage)):
            before = left_coverage.get(domain, {})
            after = right_coverage.get(domain, {})
            before_semantic = (
                before.get("status"),
                before.get("fingerprint"),
                before.get("error_code"),
            )
            after_semantic = (
                after.get("status"),
                after.get("fingerprint"),
                after.get("error_code"),
            )
            if before_semantic != after_semantic:
                coverage_changes.append(
                    {
                        "domain": domain,
                        "before": before.get("status", "missing"),
                        "after": after.get("status", "missing"),
                    }
                )
        total = (
            len(added)
            + len(removed)
            + len(changed)
            + len(all_dependency_changes)
            + len(coverage_changes)
        )
        return {
            "added_capabilities": [n for n in added if n.get("kind") == "capability"][:MAX_RESULTS],
            "removed_capabilities": [n for n in removed if n.get("kind") == "capability"][:MAX_RESULTS],
            "changed_state": changed[:MAX_RESULTS],
            "dependency_changes": dependency_changes,
            "invariant_impact": [n for n in added + removed if n.get("kind") == "invariant"][:MAX_RESULTS],
            "runtime_changes": [n for n in added + removed if n.get("kind") == "runtime"][:MAX_RESULTS],
            "quality_changes": quality_changes,
            "coverage_changes": coverage_changes[:MAX_RESULTS],
            "truncated": total > MAX_RESULTS,
        }
