from __future__ import annotations

import hashlib
import json
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from hermes_cli.gnothi.collectors.base import CollectorContext, CollectorResult
from hermes_cli.gnothi.contract import stable_id
from hermes_cli.gnothi.redaction import safe_exception_class


def _manifest() -> dict[str, Any]:
    resource = resources.files("hermes_cli.gnothi").joinpath("invariants.yaml")
    content = resource.read_text(encoding="utf-8")
    value = yaml.safe_load(content)
    if not isinstance(value, dict):
        raise ValueError("invalid invariant manifest")
    return value


def _fingerprint(nodes, edges, evidence) -> str:
    encoded = json.dumps(
        {"nodes": nodes, "edges": edges, "evidence": evidence},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


class ContractCollector:
    name = "contracts"

    def collect(self, context: CollectorContext) -> CollectorResult:
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        evidence: list[dict[str, Any]] = []
        missing = False
        try:
            root = context.workspace_root.resolve()
            manifest = _manifest()
            version = str(manifest.get("version") or "")
            for invariant in manifest.get("invariants", []):
                if not isinstance(invariant, dict):
                    continue
                invariant_id = str(invariant.get("id") or "")
                if not invariant_id.startswith("invariant:"):
                    continue
                refs: list[str] = []
                for pattern in invariant.get("evidence_globs", []):
                    for path in sorted(root.glob(str(pattern))):
                        if not path.is_file():
                            continue
                        resolved = path.resolve()
                        try:
                            relative = resolved.relative_to(root).as_posix()
                        except ValueError:
                            continue
                        checksum = hashlib.sha256(resolved.read_bytes()).hexdigest()
                        evidence_id = stable_id(
                            "evidence",
                            {
                                "manifest": version,
                                "path": relative,
                                "sha256": checksum,
                            },
                        )
                        if evidence_id not in refs:
                            refs.append(evidence_id)
                            evidence.append(
                                {
                                    "id": evidence_id,
                                    "kind": "versioned_file",
                                    "path": relative,
                                    "sha256": checksum,
                                    "manifest_version": version,
                                }
                            )
                if not refs:
                    missing = True
                nodes.append(
                    {
                        "id": invariant_id,
                        "kind": "invariant",
                        "label": str(invariant.get("title") or invariant_id),
                        "owner": {"class": "core", "id": "hermes"},
                        "generation_scope": context.generation_scope,
                        "state": {
                            "declared": True,
                            "installed": True,
                            "available": bool(refs),
                            "active": True,
                            "verified": bool(refs),
                            "degraded": not bool(refs),
                            "candidate": False,
                        },
                        "evidence_refs": refs,
                        "properties": {
                            "collector": self.name,
                            "description": str(invariant.get("description") or "")[:1000],
                            "manifest_version": version,
                        },
                        "verified_at": context.collected_at if refs else None,
                    }
                )
                for evidence_id in refs:
                    edges.append(
                        {
                            "id": stable_id(
                                "edge",
                                {
                                    "kind": "protected_by",
                                    "source": invariant_id,
                                    "target": evidence_id,
                                },
                            ),
                            "kind": "protected_by",
                            "from": invariant_id,
                            "to": evidence_id,
                            "evidence_refs": [evidence_id],
                            "properties": {"collector": self.name},
                        }
                    )
        except Exception as exc:
            return CollectorResult(
                name=self.name,
                status="partial",
                nodes=nodes,
                edges=edges,
                evidence=evidence,
                fingerprint=_fingerprint(nodes, edges, evidence),
                verified_at=None,
                error_code=safe_exception_class(exc),
            )
        return CollectorResult(
            name=self.name,
            status="partial" if missing else "current",
            nodes=nodes,
            edges=edges,
            evidence=evidence,
            fingerprint=_fingerprint(nodes, edges, evidence),
            verified_at=None if missing else context.collected_at,
            error_code="MissingEvidence" if missing else None,
        )
