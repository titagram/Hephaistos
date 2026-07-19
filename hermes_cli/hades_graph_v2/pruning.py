"""Atomic semantic-unit selection for graph-v2 byte and chunk budgets."""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from collections.abc import Iterable, Mapping
import copy
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, cast

from .bundle import (
    CHUNK_KINDS,
    BundleLimits,
    GraphEnvelopeTooLargeError,
    GraphRecoverableCapacityError,
    GraphUnitRecordTooLargeError,
    build_bundle_plan,
    record_fits_chunk,
)
from .identity import artifact_graph_version
from .model import (
    AnalysisStatus,
    CapabilityStatus,
    CompletenessStatus,
    EntrypointKind,
    FileIdentity,
    FlowKind,
    GraphArtifactV2,
    ReasonCode,
    artifact_from_payload,
    artifact_to_payload,
)
from .validation import validate_artifact


Token = tuple[str, str]
_TOPOLOGY_KINDS = ("nodes", "structures", "edges", "uncertainties")
_RECORD_COUNT_KINDS = (
    "nodes",
    "structures",
    "edges",
    "flows",
    "flow_steps",
    "uncertainties",
)
_CAPABILITY_ORDER = (
    "inventory",
    "entrypoint_discovery",
    "symbol_resolution",
    "call_graph",
    "control_flow",
    "framework_lifecycle",
    "exceptions",
    "async",
    "data_access",
)
_ENTRYPOINT_CAPABILITIES = frozenset({
    "entrypoint_discovery",
    "symbol_resolution",
    "call_graph",
    "control_flow",
    "exceptions",
    "async",
    "data_access",
})
_STRUCTURAL_CAPABILITIES = frozenset({
    "symbol_resolution",
    "call_graph",
    "control_flow",
    "exceptions",
    "async",
    "data_access",
})


class GraphBudgetError(RuntimeError):
    """The graph cannot produce a valid envelope within its hard budgets."""

    code = "graph_budget_invalid"

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(f"{self.code}: {message}")


class GraphRequiredEnvelopeTooLargeError(GraphBudgetError):
    """Required record or manifest metadata cannot fit its hard envelope."""

    code = "graph_record_too_large"


class GraphBundleBudgetTooSmallError(GraphBudgetError):
    """Even the final valid selected graph cannot fit total capacity."""

    code = "graph_bundle_budget_too_small"


@dataclass(frozen=True, slots=True)
class _Unit:
    family: str
    sort_key: tuple[Any, ...]
    tokens: frozenset[Token]
    capabilities: frozenset[str]


@dataclass(frozen=True, slots=True)
class _Rejection:
    unit: _Unit
    reason: ReasonCode


class _GraphIndex:
    def __init__(self, payload: Mapping[str, Any]) -> None:
        self.payload = payload
        self.records: dict[str, dict[str, dict[str, Any]]] = {
            kind: {record["id"]: record for record in cast(list[dict], payload[kind])}
            for kind in CHUNK_KINDS
        }
        self.file_by_path = {
            record["identity"]["path"]: record["id"]
            for record in self.records["nodes"].values()
            if record["identity"]["variant"] == "file"
        }
        self.steps_by_flow: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for step in self.records["flow_steps"].values():
            self.steps_by_flow[step["flow_id"]].append(step)
        self.flows_by_entrypoint: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for flow in self.records["flows"].values():
            self.flows_by_entrypoint[flow["entrypoint_id"]].append(flow)

    def record(self, token: Token) -> dict[str, Any] | None:
        return self.records.get(token[0], {}).get(token[1])

    def token_paths(self, token: Token) -> tuple[str, ...]:
        record = self.record(token)
        if record is None:
            return ()
        paths: set[str] = set()

        def visit(value: object, key: str | None = None) -> None:
            if isinstance(value, dict):
                for child_key, child in value.items():
                    visit(child, child_key)
            elif isinstance(value, list):
                for child in value:
                    visit(child, key)
            elif isinstance(value, str) and key in {
                "path",
                "configuration_paths",
                "paths_sample",
            }:
                paths.add(value)

        visit(record)
        return tuple(sorted(paths))

    def token_language(self, token: Token) -> str | None:
        kind, public_id = token
        record = self.record(token)
        if record is None:
            return None
        if kind == "nodes":
            return cast(str | None, record.get("language"))
        if kind == "entrypoints":
            node = self.records["nodes"].get(public_id)
            return None if node is None else cast(str | None, node.get("language"))
        if kind == "edges":
            node = self.records["nodes"].get(record["source_id"])
            return None if node is None else cast(str | None, node.get("language"))
        if kind == "structures":
            node = self.records["nodes"].get(record["owner_node_id"])
            return None if node is None else cast(str | None, node.get("language"))
        if kind == "flows":
            node = self.records["nodes"].get(record["entrypoint_id"])
            return None if node is None else cast(str | None, node.get("language"))
        if kind == "flow_steps":
            edge = self.records["edges"].get(record["edge_id"])
            if edge is None:
                return None
            node = self.records["nodes"].get(edge["source_id"])
            return None if node is None else cast(str | None, node.get("language"))
        if kind == "uncertainties":
            subject = record["subject"]
            if "edge_id" in subject:
                edge = self.records["edges"].get(subject["edge_id"])
            else:
                edge = next(
                    (
                        item
                        for item in self.records["edges"].values()
                        if item.get("call_site_id") == subject["call_site_id"]
                        and item.get("uncertainty_id") == public_id
                    ),
                    None,
                )
            if edge is not None:
                node = self.records["nodes"].get(edge["source_id"])
                return None if node is None else cast(str | None, node.get("language"))
        return None

    def _file_dependencies(self, record: Mapping[str, Any]) -> set[Token]:
        result: set[Token] = set()

        def visit(value: object, key: str | None = None) -> None:
            if isinstance(value, Mapping):
                for child_key, child in value.items():
                    visit(child, cast(str, child_key))
            elif isinstance(value, list):
                for child in value:
                    visit(child, key)
            elif isinstance(value, str) and key in {
                "path",
                "configuration_paths",
                "paths_sample",
            }:
                file_id = self.file_by_path.get(value)
                if file_id is not None:
                    result.add(("nodes", file_id))

        visit(record)
        return result

    def direct_dependencies(self, token: Token) -> set[Token]:
        kind, public_id = token
        record = self.record(token)
        if record is None:
            return set()
        dependencies = self._file_dependencies(record)
        if kind == "entrypoints":
            dependencies.add(("nodes", public_id))
            if record.get("handler_node_id") is not None:
                dependencies.add(("nodes", record["handler_node_id"]))
            if record.get("uncertainty_id") is not None:
                dependencies.add(("uncertainties", record["uncertainty_id"]))
        elif kind == "nodes":
            identity = record["identity"]
            if identity.get("owner_node_id") is not None:
                dependencies.add(("nodes", identity["owner_node_id"]))
            if record.get("uncertainty_id") is not None:
                dependencies.add(("uncertainties", record["uncertainty_id"]))
        elif kind == "structures":
            dependencies.add(("nodes", record["owner_node_id"]))
            if record.get("continuation_node_id") is not None:
                dependencies.add(("nodes", record["continuation_node_id"]))
            if record.get("parent_structure_id") is not None:
                dependencies.add(("structures", record["parent_structure_id"]))
        elif kind == "edges":
            dependencies.update({
                ("nodes", record["source_id"]),
                ("nodes", record["target_id"]),
                ("nodes", record["occurrence"]["owner_node_id"]),
            })
            for field in ("branch_group_id", "call_site_id", "exception_scope_id"):
                if record.get(field) is not None:
                    dependencies.add(("structures", record[field]))
            if record.get("uncertainty_id") is not None:
                dependencies.add(("uncertainties", record["uncertainty_id"]))
        elif kind == "flows":
            dependencies.update({
                ("nodes", record["entrypoint_id"]),
                ("nodes", record["root_node_id"]),
                ("entrypoints", record["entrypoint_id"]),
            })
        elif kind == "flow_steps":
            dependencies.update({
                ("flows", record["flow_id"]),
                ("edges", record["edge_id"]),
            })
            if record.get("branch_group_id") is not None:
                dependencies.add(("structures", record["branch_group_id"]))
            if record.get("async_child_flow_id") is not None:
                dependencies.add(("flows", record["async_child_flow_id"]))
        elif kind == "uncertainties":
            subject = record["subject"]
            if "edge_id" in subject:
                dependencies.add(("edges", subject["edge_id"]))
            else:
                dependencies.add(("structures", subject["call_site_id"]))
            dependencies.update(
                ("nodes", node_id_value)
                for node_id_value in record["candidate_target_node_ids"]
            )
            dependencies.update(
                ("edges", edge_id_value)
                for edge_id_value in record["candidate_edge_ids"]
            )
        return {
            dependency
            for dependency in dependencies
            if self.record(dependency) is not None
        }

    def closure(self, roots: Iterable[Token]) -> frozenset[Token]:
        selected: set[Token] = set()
        pending = list(roots)
        while pending:
            token = pending.pop()
            if token in selected or self.record(token) is None:
                continue
            selected.add(token)
            pending.extend(self.direct_dependencies(token) - selected)
            if token[0] == "flows":
                pending.extend(
                    ("flow_steps", step["id"]) for step in self.steps_by_flow[token[1]]
                )
        return frozenset(selected)

    def entrypoint_unit(self, entrypoint: Mapping[str, Any]) -> _Unit:
        public_id = cast(str, entrypoint["id"])
        roots: list[Token] = [("entrypoints", public_id), ("nodes", public_id)]
        roots.extend(
            ("flows", flow["id"])
            for flow in self.flows_by_entrypoint[public_id]
            if flow["kind"] != FlowKind.ASYNC_FLOW.value
        )
        ordinal = list(EntrypointKind).index(
            EntrypointKind(entrypoint["entrypoint_kind"])
        )
        return _Unit(
            "entrypoint",
            (ordinal, entrypoint["label"], public_id),
            self.closure(roots),
            _ENTRYPOINT_CAPABILITIES,
        )


def _record_sort_path(index: _GraphIndex, token: Token) -> tuple[str, str]:
    paths = index.token_paths(token)
    return (paths[0] if paths else "", token[1])


def _structural_units(
    index: _GraphIndex,
    accepted: set[Token],
    excluded_entrypoint_ids: set[str],
) -> tuple[_Unit, ...]:
    residual: set[Token] = set()
    for kind in _TOPOLOGY_KINDS:
        for public_id, record in index.records[kind].items():
            token = (kind, public_id)
            if token in accepted:
                continue
            if kind == "nodes" and record["kind"] in {"file", "entrypoint"}:
                continue
            residual.add(token)

    invalid: set[Token] = set()
    changed = True
    while changed:
        changed = False
        for token in residual - invalid:
            dependencies = index.direct_dependencies(token)
            if (
                any(
                    dependency[0] in {"nodes", "entrypoints"}
                    and dependency[1] in excluded_entrypoint_ids
                    for dependency in dependencies
                )
                or dependencies & invalid
            ):
                invalid.add(token)
                changed = True
    residual -= invalid

    adjacency: dict[Token, set[Token]] = defaultdict(set)
    for token in residual:
        for dependency in index.direct_dependencies(token) & residual:
            adjacency[token].add(dependency)
            adjacency[dependency].add(token)

    units: list[_Unit] = []
    remaining = set(residual)
    while remaining:
        root = min(remaining, key=lambda token: _record_sort_path(index, token))
        component: set[Token] = set()
        pending = [root]
        while pending:
            token = pending.pop()
            if token in component:
                continue
            component.add(token)
            pending.extend(adjacency[token] - component)
        remaining -= component
        closure = index.closure(component)
        if any(
            token[0] in {"entrypoints", "flows", "flow_steps"}
            or (
                token[0] == "nodes"
                and cast(dict, index.record(token)).get("kind") == "entrypoint"
            )
            for token in closure
        ):
            continue
        sort_key = min(_record_sort_path(index, token) for token in component)
        units.append(_Unit("structural", sort_key, closure, _STRUCTURAL_CAPABILITIES))
    return tuple(sorted(units, key=lambda unit: unit.sort_key))


def _inventory_units(index: _GraphIndex, accepted: set[Token]) -> tuple[_Unit, ...]:
    units = []
    for public_id, record in index.records["nodes"].items():
        token = ("nodes", public_id)
        if token in accepted or record["kind"] != "file":
            continue
        path = record["identity"]["path"]
        units.append(
            _Unit(
                "inventory",
                (path, public_id),
                frozenset({token}),
                frozenset({"inventory"}),
            )
        )
    return tuple(sorted(units, key=lambda unit: unit.sort_key))


def _reason_impacts(index: _GraphIndex, token: Token) -> frozenset[str]:
    if token[0] in {"entrypoints", "flows", "flow_steps"}:
        return _ENTRYPOINT_CAPABILITIES
    if token[0] == "nodes":
        record = index.record(token)
        if record is not None and record["kind"] == "file":
            return frozenset({"inventory"})
        if record is not None and record["kind"] == "entrypoint":
            return _ENTRYPOINT_CAPABILITIES
    return _STRUCTURAL_CAPABILITIES


def _merge_reason(
    capability: dict[str, Any],
    *,
    code: ReasonCode,
    count: int,
    language: str | None,
    paths: Iterable[str],
) -> None:
    if capability["status"] == CapabilityStatus.NOT_APPLICABLE.value:
        return
    key = (code.value, language)
    reasons = {
        (reason["code"], reason["language"]): copy.deepcopy(reason)
        for reason in capability["reasons"]
    }
    existing = reasons.get(key)
    if existing is None:
        reasons[key] = {
            "code": code.value,
            "count": count,
            "language": language,
            "paths_sample": sorted(set(paths))[:10],
        }
    else:
        existing["count"] += count
        existing["paths_sample"] = sorted({*existing["paths_sample"], *paths})[:10]
    if capability["status"] == CapabilityStatus.FULL.value:
        capability["status"] = CapabilityStatus.PARTIAL.value
    capability["reasons"] = sorted(
        reasons.values(),
        key=lambda reason: (
            reason["code"],
            "" if reason["language"] is None else reason["language"],
            reason["paths_sample"][0] if reason["paths_sample"] else "",
        ),
    )


def _status(capabilities: Mapping[str, Any]) -> str:
    return (
        CompletenessStatus.PARTIAL.value
        if any(
            capability["status"]
            in {CapabilityStatus.PARTIAL.value, CapabilityStatus.UNSUPPORTED.value}
            for capability in capabilities.values()
        )
        else CompletenessStatus.FULL.value
    )


def _finalize_candidate(
    original: Mapping[str, Any],
    index: _GraphIndex,
    selected: set[Token],
    rejections: tuple[_Rejection, ...],
) -> GraphArtifactV2:
    payload = copy.deepcopy(dict(original))
    for kind in CHUNK_KINDS:
        payload[kind] = [
            copy.deepcopy(record)
            for public_id, record in index.records[kind].items()
            if (kind, public_id) in selected
        ]
        payload[kind].sort(key=lambda record: record["id"])

    omitted_tokens = {
        (kind, public_id)
        for kind in CHUNK_KINDS
        for public_id in index.records[kind]
        if (kind, public_id) not in selected
    }
    reason_by_token: dict[Token, ReasonCode] = {}
    capability_by_token: dict[Token, frozenset[str]] = {}
    for rejection in rejections:
        for token in rejection.unit.tokens:
            if token not in omitted_tokens:
                continue
            capability_by_token[token] = (
                capability_by_token.get(token, frozenset())
                | rejection.unit.capabilities
            )
            previous = reason_by_token.get(token)
            if previous is None or rejection.reason is ReasonCode.RECORD_TOO_LARGE:
                reason_by_token[token] = rejection.reason
    for token in omitted_tokens:
        reason_by_token.setdefault(token, ReasonCode.RESOURCE_BUDGET_REACHED)
        capability_by_token.setdefault(token, _reason_impacts(index, token))

    contract = payload["graph_contract"]
    coverage = contract["coverage"]
    records = coverage["records"]
    for kind in _RECORD_COUNT_KINDS:
        records[kind] = len(payload[kind])
    records["omitted_by_bundle_budget"] = len(omitted_tokens)

    files = [node for node in payload["nodes"] if node["identity"]["variant"] == "file"]
    original_file_ids = {
        public_id
        for public_id, node in index.records["nodes"].items()
        if node["identity"]["variant"] == "file"
    }
    selected_file_ids = {node["id"] for node in files}
    missing_files = len(original_file_ids - selected_file_ids)
    status_counts = Counter(node["properties"]["analysis_status"] for node in files)
    file_coverage = coverage["files"]
    file_coverage.update(
        analyzed=status_counts[AnalysisStatus.ANALYZED.value],
        unsupported=status_counts[AnalysisStatus.UNSUPPORTED.value],
        failed=status_counts[AnalysisStatus.FAILED.value],
        too_large=status_counts[AnalysisStatus.TOO_LARGE.value],
        budget_omitted=status_counts[AnalysisStatus.BUDGET_OMITTED.value]
        + missing_files,
    )
    analyzed_languages = Counter(
        node["language"]
        for node in files
        if node["language"] is not None
        and node["properties"]["analysis_status"] == AnalysisStatus.ANALYZED.value
    )
    for language in payload["languages"]:
        language["analyzed_file_count"] = analyzed_languages[language["name"]]

    synchronous_flows = [
        flow for flow in payload["flows"] if flow["kind"] != FlowKind.ASYNC_FLOW.value
    ]
    partial_flows = sum(
        flow["completeness"]["status"] == CompletenessStatus.PARTIAL.value
        for flow in synchronous_flows
    )
    entrypoint_coverage = coverage["entrypoints"]
    rejected_entrypoints = entrypoint_coverage["detected"] - len(payload["entrypoints"])
    entrypoint_coverage["analyzed"] = len(payload["entrypoints"])
    entrypoint_coverage["partial"] = partial_flows + rejected_entrypoints

    reason_rows: dict[tuple[ReasonCode, str | None, str], tuple[int, set[str]]] = {}
    mutable_counts: Counter[tuple[ReasonCode, str | None, str]] = Counter()
    mutable_paths: dict[tuple[ReasonCode, str | None, str], set[str]] = defaultdict(set)
    for token in omitted_tokens:
        language = index.token_language(token)
        reason = reason_by_token[token]
        for capability in capability_by_token[token]:
            key = (reason, language, capability)
            mutable_counts[key] += 1
            mutable_paths[key].update(index.token_paths(token))
    reason_rows = {
        key: (count, mutable_paths[key]) for key, count in mutable_counts.items()
    }

    completeness = contract["completeness"]
    language_rows = {row["language"]: row for row in completeness["languages"]}
    for (reason, language, capability_name), (count, paths) in reason_rows.items():
        _merge_reason(
            completeness["capabilities"][capability_name],
            code=reason,
            count=count,
            language=language,
            paths=paths,
        )
        if language in language_rows:
            _merge_reason(
                language_rows[cast(str, language)]["capabilities"][capability_name],
                code=reason,
                count=count,
                language=language,
                paths=paths,
            )
    for row in completeness["languages"]:
        row["status"] = _status(row["capabilities"])
    completeness["status"] = _status(completeness["capabilities"])

    contract["artifact_graph_version"] = "0" * 64
    contract["artifact_graph_version"] = artifact_graph_version(payload)
    model = artifact_from_payload(payload)
    validate_artifact(model)
    return model


class GraphBudgetPruner:
    """Select whole flow/component/inventory units in the normative order."""

    def select(
        self, artifact: GraphArtifactV2, limits: BundleLimits
    ) -> GraphArtifactV2:
        if not isinstance(artifact, GraphArtifactV2):
            artifact = artifact_from_payload(cast(Mapping[str, Any], artifact))
        validate_artifact(artifact)
        original = artifact_to_payload(artifact)
        index = _GraphIndex(original)

        # The common case is an already-valid graph that fits unchanged.  Besides
        # avoiding needless coverage rewrites, planning it once keeps selection
        # linear for large inventories; the unit loop below is intentionally more
        # expensive because every tentative acceptance needs an exact ledger.
        try:
            complete_plan = build_bundle_plan(
                artifact, limits, enforce_total=False, _validated=True
            )
        except GraphEnvelopeTooLargeError as exc:
            raise GraphRequiredEnvelopeTooLargeError(exc.message) from exc
        except GraphRecoverableCapacityError:
            complete_plan = None
        if (
            complete_plan is not None
            and complete_plan.logical_uncompressed_bytes <= limits.bundle_ceiling
        ):
            return artifact

        accepted: set[Token] = set()
        for framework in cast(list[dict[str, Any]], original["frameworks"]):
            for path in framework["configuration_paths"]:
                file_id = index.file_by_path.get(path)
                if file_id is not None:
                    accepted.add(("nodes", file_id))
        for token in accepted:
            record = cast(dict[str, Any], index.record(token))
            if not record_fits_chunk(token[0], record, limits):
                raise GraphRequiredEnvelopeTooLargeError(
                    "required envelope record exceeds the chunk ceiling"
                )

        rejections: list[_Rejection] = []

        def consider(unit: _Unit) -> None:
            added = unit.tokens - accepted
            if any(
                not record_fits_chunk(
                    token[0], cast(dict[str, Any], index.record(token)), limits
                )
                for token in added
            ):
                rejections.append(_Rejection(unit, ReasonCode.RECORD_TOO_LARGE))
                return
            candidate_tokens = accepted | set(unit.tokens)
            try:
                candidate = _finalize_candidate(
                    original, index, candidate_tokens, tuple(rejections)
                )
                plan = build_bundle_plan(
                    candidate, limits, enforce_total=False, _validated=True
                )
            except GraphUnitRecordTooLargeError:
                rejections.append(_Rejection(unit, ReasonCode.RECORD_TOO_LARGE))
                return
            except GraphEnvelopeTooLargeError as exc:
                raise GraphRequiredEnvelopeTooLargeError(exc.message) from exc
            except GraphRecoverableCapacityError:
                rejections.append(_Rejection(unit, ReasonCode.RESOURCE_BUDGET_REACHED))
                return
            if plan.logical_uncompressed_bytes <= limits.bundle_ceiling:
                accepted.update(unit.tokens)
            else:
                rejections.append(_Rejection(unit, ReasonCode.RESOURCE_BUDGET_REACHED))

        entrypoint_units = tuple(
            sorted(
                (
                    index.entrypoint_unit(entrypoint)
                    for entrypoint in index.records["entrypoints"].values()
                ),
                key=lambda unit: unit.sort_key,
            )
        )
        for unit in entrypoint_units:
            consider(unit)

        accepted_entrypoints = {
            public_id for kind, public_id in accepted if kind == "entrypoints"
        }
        excluded_entrypoints = set(index.records["entrypoints"]) - accepted_entrypoints
        for unit in _structural_units(index, accepted, excluded_entrypoints):
            consider(unit)
        for unit in _inventory_units(index, accepted):
            consider(unit)

        selected = _finalize_candidate(original, index, accepted, tuple(rejections))
        try:
            plan = build_bundle_plan(
                selected, limits, enforce_total=False, _validated=True
            )
        except GraphEnvelopeTooLargeError as exc:
            raise GraphRequiredEnvelopeTooLargeError(exc.message) from exc
        except GraphRecoverableCapacityError as exc:
            raise GraphBundleBudgetTooSmallError(exc.message) from exc
        if plan.logical_uncompressed_bytes > limits.bundle_ceiling:
            raise GraphBundleBudgetTooSmallError(
                "empty valid artifact envelope exceeds the total-byte limit"
            )
        validate_artifact(selected)
        return selected


__all__ = [
    "GraphBudgetError",
    "GraphBudgetPruner",
    "GraphBundleBudgetTooSmallError",
    "GraphRequiredEnvelopeTooLargeError",
]
