from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from hermes_constants import get_hermes_home
from hermes_cli.gnothi.collectors.base import CollectorContext, CollectorResult
from hermes_cli.gnothi.contract import stable_id

MAX_EVENT_LINES = 10_000
MAX_EVENT_BYTES = 8 * 1024 * 1024


def _read_tail(path: Path) -> tuple[list[bytes], bool]:
    size = path.stat().st_size
    truncated = size > MAX_EVENT_BYTES
    with path.open("rb") as handle:
        if truncated:
            handle.seek(size - MAX_EVENT_BYTES)
            handle.readline()
        data = handle.read(MAX_EVENT_BYTES)
    lines = data.splitlines()
    if len(lines) > MAX_EVENT_LINES:
        truncated = True
        lines = lines[-MAX_EVENT_LINES:]
    return lines, truncated


def _fingerprint(nodes, edges, evidence) -> str:
    encoded = json.dumps(
        {"nodes": nodes, "edges": edges, "evidence": evidence},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


class ExperienceCollector:
    name = "experience"

    def collect(self, context: CollectorContext) -> CollectorResult:
        path = get_hermes_home() / "logs" / "organism-events.jsonl"
        if not path.is_file():
            return CollectorResult(
                name=self.name,
                status="missing",
                nodes=[],
                edges=[],
                evidence=[],
                fingerprint=_fingerprint([], [], []),
                verified_at=None,
            )
        lines, partial = _read_tail(path)
        groups: dict[str, dict[str, Any]] = {}
        event_ids: dict[str, set[str]] = {}
        for raw in lines:
            try:
                row = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError):
                partial = True
                continue
            if not isinstance(row, dict):
                partial = True
                continue
            signature = str(row.get("bounded_signature") or "")
            if not signature.startswith("sha256:"):
                partial = True
                continue
            occurred = str(row.get("occurred_at") or "")
            group = groups.setdefault(
                signature,
                {
                    "count": 0,
                    "first_seen": occurred,
                    "last_seen": occurred,
                    "severity": str(row.get("severity") or "unknown"),
                    "recovered": bool(row.get("recovered")),
                    "generation_id": str(row.get("generation_id") or ""),
                    "component_id": str(row.get("component_id") or ""),
                    "capability_id": str(row.get("capability_id") or ""),
                    "operation": str(row.get("operation") or ""),
                    "failure_class": str(row.get("failure_class") or ""),
                },
            )
            group["count"] += 1
            group["first_seen"] = min(group["first_seen"], occurred)
            group["last_seen"] = max(group["last_seen"], occurred)
            group["recovered"] = group["recovered"] or bool(row.get("recovered"))
            event_ids.setdefault(signature, set()).add(str(row.get("event_id") or ""))

        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        evidence: list[dict[str, Any]] = []
        for signature, properties in sorted(groups.items()):
            refs = sorted(ref for ref in event_ids[signature] if ref)
            evidence.extend(
                {"id": ref, "kind": "experience_event", "bounded_signature": signature}
                for ref in refs
            )
            node_id = stable_id("observation", {"bounded_signature": signature})
            nodes.append(
                {
                    "id": node_id,
                    "kind": "observation",
                    "label": f"{properties['operation']}:{properties['failure_class']}",
                    "owner": {"class": "core", "id": "hermes"},
                    "generation_scope": context.generation_scope,
                    "state": {
                        "declared": False,
                        "installed": True,
                        "available": True,
                        "active": True,
                        "verified": True,
                        "degraded": not properties["recovered"],
                        "candidate": False,
                    },
                    "evidence_refs": refs,
                    "properties": {"collector": self.name, **properties},
                    "verified_at": properties["last_seen"],
                }
            )
            for target in (properties["component_id"], properties["capability_id"]):
                if not target:
                    continue
                edges.append(
                    {
                        "id": stable_id(
                            "edge",
                            {"kind": "observed_on", "source": node_id, "target": target},
                        ),
                        "kind": "observed_on",
                        "from": node_id,
                        "to": target,
                        "evidence_refs": refs,
                        "properties": {"collector": self.name},
                    }
                )
        return CollectorResult(
            name=self.name,
            status="partial" if partial else "current",
            nodes=nodes,
            edges=edges,
            evidence=evidence,
            fingerprint=_fingerprint(nodes, edges, evidence),
            verified_at=context.collected_at,
            error_code="MalformedOrTruncatedEvents" if partial else None,
        )
