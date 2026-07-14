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


def _ordered_collectors(collectors=None):
    configured = list(collectors) if collectors is not None else _default_collectors()
    order = {name: index for index, name in enumerate(COLLECTOR_ORDER)}
    configured.sort(key=lambda item: (order.get(str(item.name), len(order)), str(item.name)))
    return configured


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


def _copy_collector_domain(
    artifact: dict[str, Any],
    current: dict[str, Any],
    name: str,
) -> None:
    """Copy one collector's immutable rows without changing their freshness."""
    copied_nodes = [
        copy.deepcopy(node)
        for node in current.get("nodes", [])
        if isinstance(node, dict)
        and node.get("properties", {}).get("collector") == name
    ]
    node_ids = {str(node.get("id")) for node in copied_nodes}
    copied_edges = [
        copy.deepcopy(edge)
        for edge in current.get("edges", [])
        if isinstance(edge, dict)
        and (
            edge.get("properties", {}).get("collector") == name
            or (
                not edge.get("properties", {}).get("collector")
                and (
                    str(edge.get("from")) in node_ids
                    or str(edge.get("to")) in node_ids
                )
            )
        )
    ]
    evidence_ids = {
        str(ref)
        for row in [*copied_nodes, *copied_edges]
        for ref in row.get("evidence_refs", [])
    }
    copied_evidence = [
        copy.deepcopy(row)
        for row in current.get("evidence", [])
        if isinstance(row, dict) and str(row.get("id")) in evidence_ids
    ]
    for node in copied_nodes:
        properties = node.setdefault("properties", {})
        properties.pop("carried_forward", None)
        properties.pop("carried_from_revision", None)
    existing_nodes = {str(row.get("id")) for row in artifact["nodes"]}
    existing_edges = {str(row.get("id")) for row in artifact["edges"]}
    existing_evidence = {str(row.get("id")) for row in artifact["evidence"]}
    artifact["nodes"].extend(
        row for row in copied_nodes if str(row.get("id")) not in existing_nodes
    )
    artifact["edges"].extend(
        row for row in copied_edges if str(row.get("id")) not in existing_edges
    )
    artifact["evidence"].extend(
        row for row in copied_evidence if str(row.get("id")) not in existing_evidence
    )


def _collector_context(
    root: Path,
    current: dict[str, Any] | None,
    *,
    collected_at: str,
    generation_scope: str,
) -> CollectorContext:
    generation_id = _git_generation(root)
    head_commit = generation_id.removeprefix("git:") if generation_id.startswith("git:") else None
    return CollectorContext(
        workspace_root=root,
        generation_id=generation_id,
        generation_scope=generation_scope,
        head_commit=head_commit,
        collected_at=collected_at,
        previous_artifact=current,
    )


def drift_status(
    workspace_root: str | Path,
    current: dict[str, Any] | None,
    *,
    collectors=None,
) -> dict[str, Any]:
    """Compare cheap collector probes with a revision's stored fingerprints."""
    if not current:
        return {
            "status": "missing",
            "domains": {},
            "invalidated_domains": [],
            "actions": ["rebuild"],
        }
    root = Path(workspace_root).resolve()
    contract = current.get("organism_contract", {})
    scope = contract.get("generation", {}).get("scope", "stable")
    context = _collector_context(
        root,
        current,
        collected_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        generation_scope=str(scope),
    )
    coverage = contract.get("coverage", {})
    domains: dict[str, dict[str, Any]] = {}
    for collector in _ordered_collectors(collectors):
        name = str(collector.name)
        stored = coverage.get(name) if isinstance(coverage, dict) else None
        try:
            probe = collector.probe_fingerprint(context)
        except Exception as exc:
            domains[name] = {
                "status": "unknown",
                "error_code": safe_exception_class(exc),
            }
            continue
        stored_fingerprint = stored.get("fingerprint") if isinstance(stored, dict) else None
        stored_status = stored.get("status") if isinstance(stored, dict) else None
        if not stored_fingerprint or stored_status != "current":
            status = "unknown"
        elif probe == stored_fingerprint:
            status = "current"
        else:
            status = "stale"
        domains[name] = {
            "status": status,
            "stored_fingerprint": stored_fingerprint,
            "probe_fingerprint": probe,
        }
    invalidated = [
        name for name in COLLECTOR_ORDER if domains.get(name, {}).get("status") == "stale"
    ]
    refresh = [
        name
        for name in COLLECTOR_ORDER
        if domains.get(name, {}).get("status") in {"stale", "unknown"}
    ]
    return {
        "status": "stale" if invalidated else ("unknown" if refresh else "current"),
        "domains": domains,
        "invalidated_domains": invalidated,
        "actions": [f"rebuild --collector {name}" for name in refresh],
    }


def build_organism_revision(
    workspace_root: str | Path,
    generation_scope: str = "stable",
    collectors=None,
    store: OrganismRevisionStore | None = None,
    now: str | datetime | None = None,
    *,
    force: bool = False,
    collector_names: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    root = Path(workspace_root).resolve()
    configured = _ordered_collectors(collectors)
    by_name = {str(collector.name): collector for collector in configured}
    selected_names = list(dict.fromkeys(collector_names or ()))
    invalid_names = sorted(
        (set(selected_names) - set(COLLECTOR_ORDER))
        | (set(selected_names) - set(by_name))
    )
    if invalid_names:
        invalid = ", ".join(invalid_names)
        raise ValueError(f"unknown collector: {invalid}")
    collected_at, compact_time = _timestamp(now)
    generation_id = _git_generation(root)
    head_commit = generation_id.removeprefix("git:") if generation_id.startswith("git:") else None
    revision_store = store or OrganismRevisionStore()
    current = revision_store.current()
    if selected_names and current is None:
        raise ValueError("targeted rebuild requires a current organism revision")
    previous = _prior_healthy(revision_store)
    if selected_names:
        configured = [by_name[name] for name in COLLECTOR_ORDER if name in selected_names]

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
    if selected_names and current:
        current_coverage = current.get("organism_contract", {}).get("coverage", {})
        for name in COLLECTOR_ORDER:
            if name in selected_names or name not in current_coverage:
                continue
            _copy_collector_domain(artifact, current, name)
            coverage_row = copy.deepcopy(current_coverage[name])
            artifact["organism_contract"]["coverage"][name] = coverage_row
            statuses[name] = str(coverage_row.get("status") or "missing")
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
