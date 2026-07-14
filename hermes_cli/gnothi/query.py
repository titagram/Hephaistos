from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

from hermes_cli.gnothi.store import OrganismRevisionStore

MAX_RESULTS = 200


class OrganismQuery:
    def __init__(self, store: OrganismRevisionStore):
        self.store = store

    def status(self) -> dict[str, Any]:
        artifact = self.store.current()
        if not artifact:
            return {"status": "missing", "actions": ["rebuild"]}
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
        dependency_changes = sorted((edge_old ^ edge_new))[:MAX_RESULTS]
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
            + len(dependency_changes)
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
