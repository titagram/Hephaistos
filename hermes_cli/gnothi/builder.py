from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hermes_cli.gnothi.collectors.base import CollectorContext, CollectorResult
from hermes_cli.gnothi.collectors.capabilities import CapabilityCollector
from hermes_cli.gnothi.collectors.contracts import ContractCollector
from hermes_cli.gnothi.collectors.dependencies import DependencyCollector
from hermes_cli.gnothi.collectors.experience import ExperienceCollector
from hermes_cli.gnothi.collectors.runtime import RuntimeCollector, _git_generation
from hermes_cli.gnothi.collectors.source import SourceCollector
from hermes_cli.gnothi.contract import new_artifact, validate_artifact
from hermes_cli.gnothi.redaction import redact_value, safe_exception_class
from hermes_cli.gnothi.store import OrganismRevisionStore

COLLECTOR_ORDER = (
    "source",
    "capabilities",
    "runtime",
    "contracts",
    "dependencies",
    "experience",
)
REQUIRED_COLLECTORS = frozenset({"source", "capabilities", "runtime", "contracts"})


def _default_collectors():
    return [
        SourceCollector(),
        CapabilityCollector(),
        RuntimeCollector(),
        ContractCollector(),
        DependencyCollector(),
        ExperienceCollector(),
    ]


def _timestamp(value: str | datetime | None) -> tuple[str, str]:
    if value is None:
        moment = datetime.now(UTC)
    elif isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=UTC)
        moment = moment.astimezone(UTC)
    else:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    return (
        moment.isoformat().replace("+00:00", "Z"),
        moment.strftime("%Y%m%dT%H%M%SZ"),
    )


def _failed_result(name: str, exc: Exception) -> CollectorResult:
    error_code = safe_exception_class(exc)
    digest = hashlib.sha256(f"{name}:{error_code}".encode()).hexdigest()
    return CollectorResult(
        name=name,
        status="partial",
        nodes=[],
        edges=[],
        evidence=[],
        fingerprint=f"sha256:{digest}",
        verified_at=None,
        error_code=error_code,
    )


def _semantic_fingerprint(artifact: dict[str, Any]) -> str:
    contract = artifact["organism_contract"]
    coverage = {
        name: {
            "status": row.get("status"),
            "fingerprint": row.get("fingerprint"),
            "error_code": row.get("error_code"),
        }
        for name, row in sorted(contract.get("coverage", {}).items())
    }
    nodes = [
        {key: value for key, value in node.items() if key != "verified_at"}
        for node in artifact["nodes"]
    ]
    payload = {
        "generation": contract.get("generation"),
        "status": contract.get("status"),
        "coverage": coverage,
        "nodes": sorted(nodes, key=lambda row: str(row.get("id"))),
        "edges": sorted(artifact["edges"], key=lambda row: str(row.get("id"))),
        "evidence": sorted(artifact.get("evidence", []), key=lambda row: str(row.get("id"))),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _prior_healthy(store: OrganismRevisionStore) -> dict[str, Any] | None:
    current = store.current()
    if current and current.get("organism_contract", {}).get("status") == "current":
        return current
    return store.previous_healthy()


def _carry_forward(
    artifact: dict[str, Any],
    result: CollectorResult,
    previous: dict[str, Any] | None,
    generation_scope: str,
) -> None:
    if result.status == "current" or not previous:
        return
    previous_id = previous.get("organism_contract", {}).get("revision_id")
    existing = {str(node.get("id")) for node in artifact["nodes"]}
    for old in previous.get("nodes", []):
        if not isinstance(old, dict):
            continue
        if old.get("properties", {}).get("collector") != result.name:
            continue
        if old.get("generation_scope") != generation_scope:
            continue
        if str(old.get("id")) in existing:
            continue
        carried = copy.deepcopy(old)
        carried.setdefault("properties", {})["carried_forward"] = True
        carried["properties"]["carried_from_revision"] = previous_id
        artifact["nodes"].append(carried)
        existing.add(str(carried.get("id")))


def _synthesize_missing_endpoints(artifact: dict[str, Any]) -> None:
    known = {str(node.get("id")) for node in artifact["nodes"]}
    scope = artifact["organism_contract"]["generation"]["scope"]
    additions = []
    for edge in artifact["edges"]:
        for endpoint in (str(edge.get("from") or ""), str(edge.get("to") or "")):
            if not endpoint or endpoint in known:
                continue
            additions.append(
                {
                    "id": endpoint,
                    "kind": "reference",
                    "label": endpoint,
                    "owner": {"class": "core", "id": "hermes"},
                    "generation_scope": scope,
                    "state": {"verified": False},
                    "evidence_refs": list(edge.get("evidence_refs") or []),
                    "properties": {"collector": "builder", "synthetic": True},
                    "verified_at": None,
                }
            )
            known.add(endpoint)
    artifact["nodes"].extend(additions)


def build_organism_revision(
    workspace_root: str | Path,
    generation_scope: str = "stable",
    collectors=None,
    store: OrganismRevisionStore | None = None,
    now: str | datetime | None = None,
    *,
    force: bool = False,
) -> dict[str, Any]:
    root = Path(workspace_root).resolve()
    collected_at, compact_time = _timestamp(now)
    generation_id = _git_generation(root)
    head_commit = generation_id.removeprefix("git:") if generation_id.startswith("git:") else None
    revision_store = store or OrganismRevisionStore()
    previous = _prior_healthy(revision_store)
    configured = list(collectors) if collectors is not None else _default_collectors()
    order = {name: index for index, name in enumerate(COLLECTOR_ORDER)}
    configured.sort(key=lambda item: (order.get(str(item.name), len(order)), str(item.name)))

    artifact = new_artifact(
        revision_id="pending",
        generation_id=generation_id,
        generation_scope=generation_scope,
        head_commit=head_commit,
        collected_at=collected_at,
    )
    artifact["evidence"] = []
    context = CollectorContext(
        workspace_root=root,
        generation_id=generation_id,
        generation_scope=generation_scope,
        head_commit=head_commit,
        collected_at=collected_at,
        previous_artifact=previous,
    )

    statuses: dict[str, str] = {}
    total_redactions = 0
    for collector in configured:
        try:
            result = collector.collect(context)
        except Exception as exc:
            result = _failed_result(str(collector.name), exc)
        safe, redactions = redact_value(asdict(result), workspace_root=root)
        total_redactions += redactions
        result = CollectorResult(**safe)
        statuses[result.name] = result.status
        artifact["organism_contract"]["coverage"][result.name] = {
            "status": result.status,
            "fingerprint": result.fingerprint,
            "verified_at": result.verified_at,
            "error_code": result.error_code,
        }
        artifact["nodes"].extend(result.nodes)
        artifact["edges"].extend(result.edges)
        artifact["evidence"].extend(result.evidence)
        _carry_forward(artifact, result, previous, generation_scope)

    for required in REQUIRED_COLLECTORS:
        statuses.setdefault(required, "missing")
        artifact["organism_contract"]["coverage"].setdefault(
            required,
            {"status": "missing", "fingerprint": "", "verified_at": None, "error_code": None},
        )
    artifact["organism_contract"]["status"] = (
        "current" if statuses and all(status == "current" for status in statuses.values()) else "partial"
    )
    artifact["redactions"] = total_redactions
    _synthesize_missing_endpoints(artifact)
    semantic = _semantic_fingerprint(artifact)
    artifact["organism_contract"]["semantic_fingerprint"] = semantic
    artifact["organism_contract"]["revision_id"] = f"rev:{compact_time}:{semantic[:12]}"

    current = revision_store.current()
    if (
        not force
        and current
        and current.get("organism_contract", {}).get("semantic_fingerprint") == semantic
    ):
        unchanged = copy.deepcopy(current)
        unchanged["build_result"] = "unchanged"
        return unchanged

    errors = validate_artifact(artifact)
    if errors:
        raise ValueError(f"invalid organism artifact: {', '.join(errors)}")
    revision_store.publish(artifact, published_at=collected_at)
    artifact["build_result"] = "published"
    return artifact
