"""Executable semantic validation for immutable graph v2 artifacts."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, fields
from pathlib import PurePosixPath
from typing import Mapping, cast

from .identity import (
    artifact_graph_version,
    ast_source_fingerprint,
    branch_group_id,
    call_site_id,
    canonical_json_bytes,
    config_source_fingerprint,
    edge_id,
    exception_scope_id,
    file_source_fingerprint,
    flow_id,
    flow_step_id,
    node_id,
    uncertainty_fingerprint,
)
from .model import (
    AnonymousCallableIdentity,
    AstSourceLocator,
    AsyncContext,
    BackboneRole,
    CallSiteSubject,
    CallableProperties,
    CandidateSetKnowledge,
    Capability,
    CapabilityStatus,
    CompletenessStatus,
    ConfigSourceLocator,
    Edge,
    EdgeAstOccurrence,
    EdgeConfigOccurrence,
    EdgeFlow,
    EdgeSubject,
    EntrypointKind,
    EntrypointNodeIdentity,
    EvidenceEnvelope,
    EvidenceItem,
    EvidenceOrigin,
    FileIdentity,
    FileProperties,
    FileSourceLocator,
    Flow,
    FlowKind,
    FrameworkProperties,
    GraphArtifactV2,
    Knowledge,
    Node,
    NodeKind,
    ReasonCode,
    RegistrationAst,
    RegistrationConfig,
    Relation,
    ResolutionKind,
    SemanticResourceIdentity,
    SourceDeclarationIdentity,
    SourceOccurrenceIdentity,
    Stage,
    Structure,
    StructureKind,
    StructureSubtype,
    Uncertainty,
    artifact_from_payload,
    artifact_to_payload,
)
from .schema import GraphContractError, JsonValue, validate_schema


class GraphValidationError(GraphContractError):
    """A deterministic, privacy-safe semantic artifact failure."""


def _fail(code: str, message: str) -> None:
    raise GraphValidationError(code, message)


_RESERVED_BASE_ORIGINS = frozenset({"agent_verified", "observed_runtime"})
_ORIGIN_RANK = {
    EvidenceOrigin.VERIFIED_FROM_CODE: 1,
    EvidenceOrigin.INFERRED: 3,
    EvidenceOrigin.UNRESOLVED: 4,
}
_SECRET_LITERAL_RE = re.compile(
    r"(?i)(?:[?&](?:api[_-]?key|password|secret|token)=|"
    r"[a-z][a-z0-9+.-]*://[^/@\s:]+:[^/@\s]+@|"
    r"(?:api[_-]?key|password|secret|token)\s*[:=]\s*[^<\s]+)"
)
_CAPABILITY_ORDER = (
    "inventory",
    "entrypoint_discovery",
    "symbol_resolution",
    "call_graph",
    "control_flow",
    "framework_lifecycle",
    "exceptions",
    "async_",
    "data_access",
)
_STAGE_ORDER = tuple(Stage)
_STRUCTURAL_RELATIONS = frozenset({
    Relation.DECLARES,
    Relation.CONTAINS,
    Relation.IMPORTS,
    Relation.INHERITS,
    Relation.IMPLEMENTS,
    Relation.REFERENCES,
    Relation.TESTS,
    Relation.DOCUMENTS,
    Relation.MAPS_TO,
})
_CALLABLE_OWNER_KINDS = frozenset({
    NodeKind.MODULE,
    NodeKind.ENTRYPOINT,
    NodeKind.FUNCTION,
    NodeKind.METHOD,
    NodeKind.CONTROLLER,
    NodeKind.SERVICE,
    NodeKind.DOMAIN,
    NodeKind.REPOSITORY,
    NodeKind.MIDDLEWARE,
    NodeKind.GUARD,
    NodeKind.AUTHORIZATION,
    NodeKind.VALIDATOR,
    NodeKind.BINDING,
    NodeKind.LISTENER,
    NodeKind.JOB,
})
_TERMINAL_KINDS = frozenset({
    NodeKind.RESPONSE,
    NodeKind.REDIRECT,
    NodeKind.ABORT,
    NodeKind.EXCEPTION,
    NodeKind.EXIT,
    NodeKind.EXTERNAL_BOUNDARY,
    NodeKind.FRAMEWORK_BOUNDARY,
    NodeKind.UNKNOWN_BOUNDARY,
})


@dataclass(frozen=True, slots=True)
class _RecordIndex:
    nodes: dict[str, Node]
    structures: dict[str, Structure]
    edges: dict[str, Edge]
    flows: dict[str, Flow]
    uncertainties: dict[str, Uncertainty]
    file_nodes_by_path: dict[str, Node]


def _reserved_origin_in_payload(value: object) -> bool:
    if isinstance(value, dict):
        if value.get("origin") in _RESERVED_BASE_ORIGINS:
            return True
        return any(_reserved_origin_in_payload(child) for child in value.values())
    if isinstance(value, list | tuple):
        return any(_reserved_origin_in_payload(child) for child in value)
    return False


def _coerce_artifact(
    artifact: GraphArtifactV2 | Mapping[str, JsonValue],
) -> GraphArtifactV2:
    if isinstance(artifact, GraphArtifactV2):
        payload = artifact_to_payload(artifact)
        try:
            validate_schema("artifact.schema.json", payload)
        except GraphContractError as exc:
            raise GraphValidationError(exc.code, exc.message) from exc
        return artifact
    if not isinstance(artifact, Mapping):
        _fail("schema_invalid", "artifact must be a closed v2 object")
    payload = dict(artifact)
    if _reserved_origin_in_payload(payload):
        _fail(
            "base_evidence_origin",
            "base artifact evidence origin is reserved for server overlays",
        )
    try:
        return artifact_from_payload(cast(Mapping[str, JsonValue], payload))
    except GraphContractError as exc:
        raise GraphValidationError(exc.code, exc.message) from exc
    except (TypeError, ValueError) as exc:
        raise GraphValidationError(
            "model_decode_failed",
            "artifact could not be decoded into the closed v2 model",
        ) from exc


def _iter_evidence(artifact: GraphArtifactV2) -> tuple[EvidenceEnvelope, ...]:
    return tuple(
        [record.evidence for record in artifact.nodes]
        + [record.evidence for record in artifact.entrypoints]
        + [record.evidence for record in artifact.structures]
        + [record.evidence for record in artifact.edges]
    )


def _iter_strings(value: JsonValue) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        return tuple(item for child in value for item in _iter_strings(child))
    if isinstance(value, dict):
        return tuple(item for child in value.values() for item in _iter_strings(child))
    return ()


def validate_scalar_and_privacy_rules(artifact: GraphArtifactV2) -> None:
    payload = artifact_to_payload(artifact)
    if any(_SECRET_LITERAL_RE.search(value) for value in _iter_strings(payload)):
        _fail(
            "secret_like_literal",
            "artifact contains a secret-like literal instead of a redacted value",
        )
    for envelope in _iter_evidence(artifact):
        for evidence in (envelope.primary, *envelope.supporting):
            if evidence.origin not in {
                EvidenceOrigin.VERIFIED_FROM_CODE,
                EvidenceOrigin.INFERRED,
                EvidenceOrigin.UNRESOLVED,
            }:
                _fail(
                    "base_evidence_origin",
                    "base artifact evidence origin is reserved for server overlays",
                )
            if (evidence.origin is EvidenceOrigin.INFERRED) != bool(
                evidence.inference_rule
            ):
                _fail(
                    "evidence_inference_rule",
                    "inferred evidence requires exactly one inference rule",
                )


def _evidence_key(item: EvidenceItem) -> tuple[object, ...]:
    locator = artifact_to_payload_value(item.source_locator)
    return (
        _ORIGIN_RANK[item.origin],
        item.extractor,
        item.source_fingerprint,
        repr(locator),
    )


def artifact_to_payload_value(value: object) -> JsonValue:
    """Encode one closed model value without exposing the private encoder."""

    from dataclasses import is_dataclass
    from enum import Enum

    if isinstance(value, Enum):
        return cast(str, value.value)
    if value is None or type(value) in {str, int, bool}:
        return cast(JsonValue, value)
    if isinstance(value, tuple):
        return [artifact_to_payload_value(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        result: dict[str, JsonValue] = {}
        for item in fields(value):
            item_value = getattr(value, item.name)
            if item.metadata.get("omit_none") and item_value is None:
                continue
            result[cast(str, item.metadata.get("wire_name", item.name))] = (
                artifact_to_payload_value(item_value)
            )
        return result
    _fail("model_encoding_failed", "closed model value could not be encoded")


def _require_sorted_unique(
    values: tuple[object, ...],
    *,
    key: object,
    code: str,
    message: str,
) -> None:
    key_fn = cast(object, key)
    keyed = [key_fn(value) for value in values]  # type: ignore[operator]
    if keyed != sorted(keyed) or len(keyed) != len(set(keyed)):
        _fail(code, message)


def validate_sorted_unique_records(artifact: GraphArtifactV2) -> None:
    _require_sorted_unique(
        artifact.frameworks,
        key=lambda record: (record.language, record.name),
        code="record_order",
        message="framework records must be unique and canonically sorted",
    )
    _require_sorted_unique(
        artifact.languages,
        key=lambda record: record.name,
        code="record_order",
        message="language records must be unique and canonically sorted",
    )
    for records in (
        artifact.entrypoints,
        artifact.nodes,
        artifact.structures,
        artifact.edges,
        artifact.flows,
        artifact.flow_steps,
        artifact.uncertainties,
    ):
        _require_sorted_unique(
            records,
            key=lambda record: record.id,
            code="record_order",
            message="public records must be unique and sorted by ID",
        )
    for framework in artifact.frameworks:
        if framework.configuration_paths != tuple(
            sorted(set(framework.configuration_paths))
        ):
            _fail("record_order", "framework paths must be unique and sorted")
    for entrypoint in artifact.entrypoints:
        if entrypoint.methods != tuple(sorted(set(entrypoint.methods))):
            _fail("record_order", "entrypoint methods must be unique and sorted")
        if entrypoint.match_constraints.schemes != tuple(
            sorted(set(entrypoint.match_constraints.schemes))
        ):
            _fail("record_order", "entrypoint schemes must be unique and sorted")
    all_capabilities = [artifact.graph_contract.completeness.capabilities]
    all_capabilities.extend(
        language.capabilities
        for language in artifact.graph_contract.completeness.languages
    )
    all_capabilities.extend(flow.completeness.capabilities for flow in artifact.flows)
    for capabilities in all_capabilities:
        for name in _CAPABILITY_ORDER:
            capability = getattr(capabilities, name)
            keys = [
                (
                    reason.code.value,
                    "" if reason.language is None else reason.language,
                    reason.paths_sample[0] if reason.paths_sample else "",
                )
                for reason in capability.reasons
            ]
            if keys != sorted(keys) or len(keys) != len(set(keys)):
                _fail("record_order", "capability reasons must be unique and sorted")
            for reason in capability.reasons:
                if reason.paths_sample != tuple(sorted(set(reason.paths_sample))):
                    _fail(
                        "record_order", "reason path samples must be unique and sorted"
                    )
    for envelope in _iter_evidence(artifact):
        supporting_keys = tuple(_evidence_key(item) for item in envelope.supporting)
        if supporting_keys != tuple(sorted(set(supporting_keys))):
            _fail("evidence_order", "supporting evidence must be unique and sorted")
        if supporting_keys and _evidence_key(envelope.primary) > supporting_keys[0]:
            _fail("evidence_order", "primary evidence must be first in evidence order")
    for uncertainty in artifact.uncertainties:
        for values in (
            uncertainty.evidence_requirements,
            uncertainty.candidate_target_node_ids,
            uncertainty.candidate_edge_ids,
        ):
            if values != tuple(sorted(set(values))):
                _fail("record_order", "uncertainty arrays must be unique and sorted")
        source_refs = tuple((item.path, item.line) for item in uncertainty.source_refs)
        if source_refs != tuple(sorted(set(source_refs))):
            _fail("record_order", "uncertainty source refs must be unique and sorted")


def _unique_index(records: tuple[object, ...], label: str) -> dict[str, object]:
    result: dict[str, object] = {}
    for record in records:
        public_id = cast(str, getattr(record, "id"))
        if public_id in result:
            _fail("duplicate_public_id", f"{label} public IDs must be unique")
        result[public_id] = record
    return result


def build_record_index(artifact: GraphArtifactV2) -> _RecordIndex:
    nodes = cast(dict[str, Node], _unique_index(artifact.nodes, "node"))
    file_nodes: dict[str, Node] = {}
    for node in artifact.nodes:
        if isinstance(node.identity, FileIdentity):
            if node.identity.path in file_nodes:
                _fail("duplicate_file_node", "each represented path has one file node")
            file_nodes[node.identity.path] = node
    return _RecordIndex(
        nodes=nodes,
        structures=cast(
            dict[str, Structure], _unique_index(artifact.structures, "structure")
        ),
        edges=cast(dict[str, Edge], _unique_index(artifact.edges, "edge")),
        flows=cast(dict[str, Flow], _unique_index(artifact.flows, "flow")),
        uncertainties=cast(
            dict[str, Uncertainty],
            _unique_index(artifact.uncertainties, "uncertainty"),
        ),
        file_nodes_by_path=file_nodes,
    )


def validate_identity_recomputation(
    artifact: GraphArtifactV2,
    index: _RecordIndex,
) -> None:
    del index
    for node in artifact.nodes:
        identity = cast(Mapping[str, object], artifact_to_payload_value(node.identity))
        if node.id != node_id(identity):
            _fail("node_id_mismatch", "serialized node ID does not match its identity")
    for structure in artifact.structures:
        identity = {
            "kind": structure.kind.value,
            "owner_node_id": structure.owner_node_id,
            "structural_path": structure.structural_path,
            "ordinal": structure.ordinal,
            "subtype": structure.subtype.value,
        }
        helper = {
            StructureKind.CALL_SITE: call_site_id,
            StructureKind.BRANCH_GROUP: branch_group_id,
            StructureKind.EXCEPTION_SCOPE: exception_scope_id,
        }[structure.kind]
        if structure.id != helper(identity):
            _fail(
                "structure_id_mismatch",
                "serialized structure ID does not match its identity",
            )
    for edge in artifact.edges:
        identity = {
            "source_id": edge.source_id,
            "target_id": edge.target_id,
            "relation": edge.relation.value,
            "flow": None if edge.flow is None else edge.flow.value,
            "condition_hash": None if edge.condition is None else edge.condition.hash,
            "branch_group_id": edge.branch_group_id,
            "call_site_id": edge.call_site_id,
            "exception_scope_id": edge.exception_scope_id,
            "occurrence": artifact_to_payload_value(edge.occurrence),
        }
        if edge.id != edge_id(identity):
            _fail("edge_id_mismatch", "serialized edge ID does not match its identity")
    for flow in artifact.flows:
        if flow.id != flow_id(flow.entrypoint_id, flow.root_node_id, flow.kind.value):
            _fail("flow_id_mismatch", "serialized flow ID does not match its identity")
    for step in artifact.flow_steps:
        if step.id != flow_step_id(
            step.flow_id,
            step.edge_id,
            step.stage_from.value,
            step.stage_to.value,
            step.async_context.value,
        ):
            _fail(
                "flow_step_id_mismatch",
                "serialized flow-step ID does not match its identity",
            )
    for uncertainty in artifact.uncertainties:
        identity = {
            "domain": "graph",
            "project_id": artifact.project.project_id,
            "workspace_binding_id": artifact.project.workspace_binding_id,
            "subject": artifact_to_payload_value(uncertainty.subject),
            "resolution_kind": uncertainty.resolution_kind.value,
            "reason_code": uncertainty.reason_code.value,
            "question": uncertainty.question,
        }
        fingerprint = uncertainty_fingerprint(identity)
        if uncertainty.fingerprint != fingerprint or uncertainty.id != (
            "hades:uncertainty:v2:" + fingerprint
        ):
            _fail(
                "uncertainty_id_mismatch",
                "serialized uncertainty identity or fingerprint does not match",
            )


def _node(index: _RecordIndex, public_id: str) -> Node:
    try:
        return index.nodes[public_id]
    except KeyError:
        _fail("dangling_node_reference", "node reference is not local to this artifact")


def _structure(
    index: _RecordIndex,
    public_id: str,
    expected: StructureKind | None = None,
) -> Structure:
    try:
        result = index.structures[public_id]
    except KeyError:
        _fail(
            "dangling_structure_reference",
            "structure reference is not local to this artifact",
        )
    if expected is not None and result.kind is not expected:
        _fail("structure_type_mismatch", "structure reference has the wrong type")
    return result


def _validate_locator(
    locator: FileSourceLocator | AstSourceLocator | ConfigSourceLocator,
    fingerprint: str,
    index: _RecordIndex,
    *,
    file_only: bool,
) -> None:
    try:
        file_node = index.file_nodes_by_path[locator.path]
    except KeyError:
        _fail(
            "dangling_file_locator",
            "source locator does not resolve to a local represented file",
        )
    if not isinstance(file_node.properties, FileProperties):
        _fail("file_property_mismatch", "file node does not use file properties")
    file_digest = file_node.properties.file_sha256
    if isinstance(locator, FileSourceLocator):
        if not file_only:
            _fail("file_evidence_scope", "file evidence is legal only on its file node")
        expected = file_source_fingerprint(file_digest, locator.path)
    elif isinstance(locator, AstSourceLocator):
        if file_only:
            _fail("file_evidence_scope", "file node requires inventory file evidence")
        expected = ast_source_fingerprint(
            file_digest, locator.path, locator.structural_path
        )
    else:
        if file_only:
            _fail("file_evidence_scope", "file node requires inventory file evidence")
        expected = config_source_fingerprint(
            file_digest, locator.path, locator.structural_pointer
        )
    if fingerprint != expected:
        _fail(
            "evidence_fingerprint_mismatch",
            "source evidence fingerprint does not match its local file locator",
        )


def _validate_evidence(
    envelope: EvidenceEnvelope,
    index: _RecordIndex,
    *,
    file_only: bool,
) -> None:
    for item in (envelope.primary, *envelope.supporting):
        _validate_locator(
            item.source_locator,
            item.source_fingerprint,
            index,
            file_only=file_only,
        )


def _require_file_path(path: str, index: _RecordIndex) -> None:
    if path not in index.file_nodes_by_path:
        _fail(
            "dangling_file_locator",
            "source path does not resolve to a local represented file",
        )


def _validate_node(node: Node, artifact: GraphArtifactV2, index: _RecordIndex) -> None:
    identity = node.identity
    if identity.workspace_binding_id != artifact.project.workspace_binding_id:
        _fail("scope_mismatch", "node identity is outside the artifact binding")
    if (
        getattr(identity, "kind") != node.kind
        and getattr(identity, "kind") != node.kind.value
    ):
        _fail("node_identity_mismatch", "node kind differs from its identity")
    if getattr(identity, "language") != node.language:
        _fail("node_identity_mismatch", "node language differs from its identity")
    if isinstance(identity, SourceDeclarationIdentity):
        if (
            identity.qualified_name != node.qualified_name
            or identity.namespace != node.namespace
            or node.location is None
            or node.location.path != identity.path
        ):
            _fail("node_display_mismatch", "source declaration display fields disagree")
    elif isinstance(identity, FileIdentity):
        if (
            node.kind is not NodeKind.FILE
            or node.location is not None
            or node.qualified_name != identity.path
            or node.name != PurePosixPath(identity.path).name
            or not isinstance(node.properties, FileProperties)
        ):
            _fail("file_node_mismatch", "inventory file node fields disagree")
        status = node.properties.analysis_status.value
        omission = node.properties.omission_reason
        if (status in {"analyzed", "unsupported"}) != (omission is None):
            _fail(
                "file_omission_mismatch", "file omission reason disagrees with status"
            )
    elif isinstance(identity, (SourceOccurrenceIdentity, AnonymousCallableIdentity)):
        owner = _node(index, identity.owner_node_id)
        if owner.kind not in _CALLABLE_OWNER_KINDS:
            _fail(
                "node_owner_mismatch", "occurrence owner is not a containing callable"
            )
        if node.location is None:
            _fail("node_location_mismatch", "source occurrence requires a location")
        if isinstance(identity, AnonymousCallableIdentity):
            if node.name != "<anonymous>":
                _fail(
                    "node_display_mismatch", "anonymous callable label is not canonical"
                )
        elif node.qualified_name is not None:
            _fail(
                "node_display_mismatch", "source occurrence qualified name must be null"
            )
    elif isinstance(identity, EntrypointNodeIdentity):
        if node.location is None or node.location.path != identity.path:
            _fail(
                "node_location_mismatch", "entrypoint node registration path disagrees"
            )
    elif isinstance(identity, SemanticResourceIdentity):
        if (
            node.qualified_name != identity.qualified_name
            or node.namespace != identity.namespace
        ):
            _fail("node_display_mismatch", "semantic resource display fields disagree")
    if node.location is not None:
        _require_file_path(node.location.path, index)
        if node.location.end_line < node.location.start_line:
            _fail("node_location_mismatch", "source location line range is inverted")
    _validate_evidence(node.evidence, index, file_only=node.kind is NodeKind.FILE)


def _validate_entrypoint(
    entrypoint: object,
    artifact: GraphArtifactV2,
    index: _RecordIndex,
) -> None:
    record = cast(object, entrypoint)
    node = _node(index, cast(str, getattr(record, "id")))
    if not isinstance(node.identity, EntrypointNodeIdentity):
        _fail("entrypoint_node_mismatch", "entrypoint ID does not reference its node")
    identity = node.identity.entrypoint_identity
    for name in (
        "entrypoint_kind",
        "framework",
        "method_semantics",
        "methods",
        "public_path",
        "public_name",
        "trigger",
        "match_constraints",
        "registration_occurrence",
    ):
        if getattr(identity, name) != getattr(record, name):
            _fail("entrypoint_identity_mismatch", "entrypoint identity fields disagree")
    occurrence = cast(
        RegistrationAst | RegistrationConfig, getattr(record, "registration_occurrence")
    )
    if node.identity.path != occurrence.path:
        _fail("entrypoint_path_mismatch", "entrypoint registration paths disagree")
    _require_file_path(occurrence.path, index)
    handler = cast(str | None, getattr(record, "handler_node_id"))
    uncertainty_id = cast(str | None, getattr(record, "uncertainty_id"))
    if (handler is None) != (uncertainty_id is not None):
        _fail(
            "entrypoint_handler_mismatch", "entrypoint handler resolution is ambiguous"
        )
    if handler is not None:
        _node(index, handler)
    _validate_evidence(
        cast(EvidenceEnvelope, getattr(record, "evidence")), index, file_only=False
    )


def validate_references(artifact: GraphArtifactV2, index: _RecordIndex) -> None:
    for node in artifact.nodes:
        _validate_node(node, artifact, index)
    for framework in artifact.frameworks:
        for path in framework.configuration_paths:
            _require_file_path(path, index)
    for entrypoint in artifact.entrypoints:
        _validate_entrypoint(entrypoint, artifact, index)
    for structure in artifact.structures:
        owner = _node(index, structure.owner_node_id)
        if owner.kind not in _CALLABLE_OWNER_KINDS:
            _fail(
                "structure_owner_mismatch", "structure owner is not a containing node"
            )
        if structure.continuation_node_id is not None:
            _node(index, structure.continuation_node_id)
        if structure.parent_structure_id is not None:
            parent = _structure(index, structure.parent_structure_id)
            if parent.kind is StructureKind.CALL_SITE:
                _fail(
                    "structure_parent_mismatch", "call sites cannot contain structures"
                )
            if (
                structure.kind is StructureKind.CALL_SITE
                and parent.kind is not StructureKind.EXCEPTION_SCOPE
            ):
                _fail(
                    "structure_parent_mismatch",
                    "call-site parent must be an exception scope",
                )
            if parent.owner_node_id != structure.owner_node_id:
                _fail(
                    "structure_parent_mismatch", "nested structures must share an owner"
                )
        _validate_evidence(structure.evidence, index, file_only=False)
    for structure in artifact.structures:
        seen = {structure.id}
        parent_id = structure.parent_structure_id
        while parent_id is not None:
            if parent_id in seen:
                _fail(
                    "structure_parent_cycle",
                    "structure parent chain must be acyclic",
                )
            seen.add(parent_id)
            parent_id = _structure(index, parent_id).parent_structure_id
    for edge in artifact.edges:
        source = _node(index, edge.source_id)
        target = _node(index, edge.target_id)
        owner = _node(index, edge.occurrence.owner_node_id)
        if owner.kind not in _CALLABLE_OWNER_KINDS:
            _fail("edge_owner_mismatch", "edge occurrence owner is not containing code")
        if isinstance(edge.occurrence, EdgeConfigOccurrence):
            _require_file_path(edge.occurrence.path, index)
        if edge.location is not None:
            _require_file_path(edge.location.path, index)
        if edge.source_id == edge.target_id:
            recursive = (
                edge.relation is Relation.INVOKES
                and edge.call_site_id is not None
                and edge.occurrence.owner_node_id == edge.source_id
            )
            loop = edge.flow is EdgeFlow.LOOP
            if not (recursive or loop):
                _fail(
                    "edge_self_reference",
                    "edge self-reference is not a loop or recursion",
                )
        if edge.branch_group_id is not None:
            branch = _structure(index, edge.branch_group_id, StructureKind.BRANCH_GROUP)
            if branch.owner_node_id != edge.occurrence.owner_node_id:
                _fail(
                    "edge_owner_mismatch",
                    "branch group and edge occurrence owners differ",
                )
            if (
                edge.flow is EdgeFlow.ALTERNATIVE
                and (
                    edge.condition is None
                    or edge.condition.polarity.value not in {"case", "default"}
                )
                and branch.subtype is not StructureSubtype.DYNAMIC_DISPATCH
            ):
                _fail(
                    "edge_condition_mismatch",
                    "alternative condition requires dynamic dispatch",
                )
        if edge.call_site_id is not None:
            call_site = _structure(index, edge.call_site_id, StructureKind.CALL_SITE)
            if call_site.owner_node_id != edge.occurrence.owner_node_id:
                _fail(
                    "edge_owner_mismatch", "call site and edge occurrence owners differ"
                )
            if (
                edge.relation is Relation.RETURNS_TO
                and edge.target_id != call_site.continuation_node_id
            ):
                _fail(
                    "return_continuation_mismatch",
                    "return does not target call-site continuation",
                )
            if edge.relation is Relation.RETURNS_TO and not any(
                candidate.relation is Relation.INVOKES
                and candidate.call_site_id == edge.call_site_id
                for candidate in artifact.edges
            ):
                _fail(
                    "return_invocation_missing",
                    "return has no matching invocation for its call site",
                )
        if edge.relation is Relation.INVOKES and edge.call_site_id is None:
            _fail(
                "invocation_structure_missing", "invocation requires a local call site"
            )
        if edge.exception_scope_id is not None:
            scope = _structure(
                index, edge.exception_scope_id, StructureKind.EXCEPTION_SCOPE
            )
            if scope.owner_node_id != edge.occurrence.owner_node_id:
                _fail("edge_owner_mismatch", "exception scope and edge owners differ")
        if (
            edge.relation is Relation.THROWS_TO
            and edge.exception_scope_id is None
            and target.kind is not NodeKind.EXCEPTION
        ):
            _fail(
                "exception_target_mismatch",
                "unhandled throw must target an exception terminal",
            )
        del source
        _validate_evidence(edge.evidence, index, file_only=False)
    for step in artifact.flow_steps:
        if step.flow_id not in index.flows:
            _fail(
                "dangling_flow_reference",
                "flow-step flow reference is not local to this artifact",
            )
        if step.edge_id not in index.edges:
            _fail(
                "dangling_edge_reference",
                "flow-step edge reference is not local to this artifact",
            )
        if (
            step.async_child_flow_id is not None
            and step.async_child_flow_id not in index.flows
        ):
            _fail(
                "dangling_flow_reference",
                "async child flow reference is not local to this artifact",
            )
        if step.branch_group_id is not None:
            _structure(index, step.branch_group_id, StructureKind.BRANCH_GROUP)
    for flow in artifact.flows:
        _node(index, flow.entrypoint_id)
        _node(index, flow.root_node_id)
    for uncertainty in artifact.uncertainties:
        for ref in uncertainty.source_refs:
            _require_file_path(ref.path, index)
        for node_ref in uncertainty.candidate_target_node_ids:
            _node(index, node_ref)
        for edge_ref in uncertainty.candidate_edge_ids:
            if edge_ref not in index.edges:
                _fail(
                    "dangling_edge_reference",
                    "uncertainty candidate edge is not local to this artifact",
                )


_RESOLUTION_MATRIX = {
    ResolutionKind.CALL_TARGET: (
        frozenset({Relation.INVOKES}),
        frozenset({EdgeFlow.ALWAYS, EdgeFlow.CONDITIONAL, EdgeFlow.ALTERNATIVE}),
        frozenset({
            NodeKind.FUNCTION,
            NodeKind.METHOD,
            NodeKind.CONTROLLER,
            NodeKind.SERVICE,
            NodeKind.DOMAIN,
            NodeKind.REPOSITORY,
            NodeKind.MIDDLEWARE,
            NodeKind.GUARD,
            NodeKind.AUTHORIZATION,
            NodeKind.VALIDATOR,
            NodeKind.BINDING,
            NodeKind.LISTENER,
            NodeKind.JOB,
        }),
        (1, 20),
    ),
    ResolutionKind.ENTRYPOINT_HANDLER: (
        frozenset({Relation.ROUTES_TO}),
        frozenset({EdgeFlow.ALWAYS, EdgeFlow.CONDITIONAL, EdgeFlow.ALTERNATIVE}),
        frozenset({
            NodeKind.FUNCTION,
            NodeKind.METHOD,
            NodeKind.CONTROLLER,
            NodeKind.SERVICE,
            NodeKind.LISTENER,
            NodeKind.JOB,
        }),
        (1, 1),
    ),
    ResolutionKind.ASYNC_TARGET: (
        frozenset({Relation.EMITS, Relation.DISPATCHES, Relation.SCHEDULES}),
        frozenset({EdgeFlow.ASYNC}),
        frozenset({
            NodeKind.EVENT,
            NodeKind.LISTENER,
            NodeKind.JOB,
            NodeKind.QUEUE,
            NodeKind.ASYNC_BOUNDARY,
            NodeKind.FUNCTION,
            NodeKind.METHOD,
            NodeKind.SERVICE,
        }),
        (1, 20),
    ),
    ResolutionKind.EXCEPTION_TARGET: (
        frozenset({Relation.THROWS_TO, Relation.HANDLES}),
        frozenset({EdgeFlow.EXCEPTION}),
        frozenset({
            NodeKind.EXCEPTION,
            NodeKind.LISTENER,
            NodeKind.FRAMEWORK_BOUNDARY,
            NodeKind.FUNCTION,
            NodeKind.METHOD,
            NodeKind.SERVICE,
        }),
        (1, 20),
    ),
    ResolutionKind.FRAMEWORK_TARGET: (
        frozenset({
            Relation.PASSES_THROUGH,
            Relation.BINDS,
            Relation.VALIDATES,
            Relation.AUTHORIZES,
            Relation.ROUTES_TO,
            Relation.HANDLES,
        }),
        frozenset({EdgeFlow.ALWAYS, EdgeFlow.CONDITIONAL, EdgeFlow.ALTERNATIVE}),
        frozenset({
            NodeKind.MIDDLEWARE,
            NodeKind.GUARD,
            NodeKind.AUTHORIZATION,
            NodeKind.VALIDATOR,
            NodeKind.BINDING,
            NodeKind.CONTROLLER,
            NodeKind.FRAMEWORK_BOUNDARY,
            NodeKind.FUNCTION,
            NodeKind.METHOD,
            NodeKind.SERVICE,
        }),
        (1, 20),
    ),
    ResolutionKind.EXTERNAL_TARGET: (
        frozenset({
            Relation.CALLS_EXTERNAL,
            Relation.READS,
            Relation.WRITES,
            Relation.QUERIES,
        }),
        frozenset({
            EdgeFlow.ALWAYS,
            EdgeFlow.CONDITIONAL,
            EdgeFlow.ALTERNATIVE,
            EdgeFlow.ASYNC,
        }),
        frozenset({
            NodeKind.INTEGRATION,
            NodeKind.EXTERNAL_BOUNDARY,
            NodeKind.MODEL,
            NodeKind.REPOSITORY,
            NodeKind.TABLE,
            NodeKind.QUERY,
            NodeKind.CACHE,
            NodeKind.STORAGE,
            NodeKind.QUEUE,
        }),
        (1, 20),
    ),
}


def _subject_edge(
    uncertainty: Uncertainty,
    carrying: tuple[Edge, ...],
    index: _RecordIndex,
) -> Edge:
    if isinstance(uncertainty.subject, EdgeSubject):
        try:
            return index.edges[uncertainty.subject.edge_id]
        except KeyError:
            _fail("uncertainty_ownership", "uncertainty ownership subject is not local")
    matches = tuple(
        edge
        for edge in carrying
        if edge.call_site_id == uncertainty.subject.call_site_id
        and edge.relation is Relation.INVOKES
    )
    if len(matches) != 1:
        _fail(
            "uncertainty_ownership",
            "uncertainty ownership requires one exact unresolved invocation",
        )
    return matches[0]


def _validate_resolution_edge(
    uncertainty: Uncertainty,
    edge: Edge,
    index: _RecordIndex,
    *,
    placeholder: bool,
) -> None:
    relations, flows, targets, _ = _RESOLUTION_MATRIX[uncertainty.resolution_kind]
    if edge.relation not in relations or edge.flow not in flows:
        _fail(
            "uncertainty_semantics", "uncertainty subject violates its resolution row"
        )
    if not placeholder and _node(index, edge.target_id).kind not in targets:
        _fail(
            "uncertainty_target_kind",
            "uncertainty candidate target kind is incompatible",
        )
    if uncertainty.resolution_kind is ResolutionKind.CALL_TARGET:
        if edge.call_site_id is None:
            _fail(
                "uncertainty_structure", "call-target uncertainty requires a call site"
            )
        _structure(index, edge.call_site_id, StructureKind.CALL_SITE)
        if (
            isinstance(uncertainty.subject, CallSiteSubject)
            and edge.call_site_id != uncertainty.subject.call_site_id
        ):
            _fail(
                "uncertainty_structure",
                "call-target candidate does not share the subject call site",
            )
    if (
        uncertainty.resolution_kind is ResolutionKind.EXCEPTION_TARGET
        and edge.exception_scope_id is None
    ):
        _fail(
            "uncertainty_structure", "exception uncertainty requires an exception scope"
        )
    if uncertainty.resolution_kind is ResolutionKind.ENTRYPOINT_HANDLER:
        source = _node(index, edge.source_id)
        if source.kind is not NodeKind.ENTRYPOINT:
            _fail(
                "uncertainty_structure",
                "handler uncertainty source is not an entrypoint",
            )


def validate_uncertainty_ownership(
    artifact: GraphArtifactV2,
    index: _RecordIndex,
) -> None:
    for node in artifact.nodes:
        if (
            node.uncertainty_id is not None
            and node.uncertainty_id not in index.uncertainties
        ):
            _fail("uncertainty_ownership", "node uncertainty ownership is not local")
        unresolved = any(
            item.origin is EvidenceOrigin.UNRESOLVED
            for item in (node.evidence.primary, *node.evidence.supporting)
        )
        if node.kind is NodeKind.UNKNOWN_BOUNDARY:
            if not unresolved or node.uncertainty_id is None:
                _fail(
                    "uncertainty_ownership",
                    "unknown boundary lacks exact uncertainty ownership",
                )
        elif unresolved or node.uncertainty_id is not None:
            _fail(
                "uncertainty_ownership",
                "non-boundary node carries unresolved ownership",
            )
    for edge in artifact.edges:
        if (
            edge.uncertainty_id is not None
            and edge.uncertainty_id not in index.uncertainties
        ):
            _fail("uncertainty_ownership", "edge uncertainty ownership is not local")
        unresolved = any(
            item.origin is EvidenceOrigin.UNRESOLVED
            for item in (edge.evidence.primary, *edge.evidence.supporting)
        )
        if unresolved and edge.uncertainty_id is None:
            _fail(
                "uncertainty_ownership",
                "unresolved edge lacks exact uncertainty ownership",
            )
    for entrypoint in artifact.entrypoints:
        if (
            entrypoint.uncertainty_id is not None
            and entrypoint.uncertainty_id not in index.uncertainties
        ):
            _fail(
                "uncertainty_ownership", "entrypoint uncertainty ownership is not local"
            )
        unresolved = any(
            item.origin is EvidenceOrigin.UNRESOLVED
            for item in (entrypoint.evidence.primary, *entrypoint.evidence.supporting)
        )
        if unresolved != (entrypoint.uncertainty_id is not None):
            _fail(
                "uncertainty_ownership",
                "entrypoint unresolved evidence and uncertainty ownership disagree",
            )

    for uncertainty in artifact.uncertainties:
        carrying = tuple(
            edge for edge in artifact.edges if edge.uncertainty_id == uncertainty.id
        )
        boundaries = tuple(
            node for node in artifact.nodes if node.uncertainty_id == uncertainty.id
        )
        candidate_edges = tuple(
            index.edges[item] for item in uncertainty.candidate_edge_ids
        )
        if uncertainty.resolution_kind is ResolutionKind.ENTRYPOINT_HANDLER:
            subject_for_entrypoint = (
                index.edges[uncertainty.subject.edge_id]
                if isinstance(uncertainty.subject, EdgeSubject)
                else None
            )
            matching_entrypoints = tuple(
                record
                for record in artifact.entrypoints
                if subject_for_entrypoint is not None
                and record.id == subject_for_entrypoint.source_id
                and record.uncertainty_id == uncertainty.id
            )
            if len(matching_entrypoints) != 1:
                _fail(
                    "uncertainty_ownership",
                    "handler uncertainty is not owned by its exact entrypoint",
                )
        if uncertainty.candidate_set_knowledge is CandidateSetKnowledge.COMPLETE:
            if boundaries:
                _fail(
                    "uncertainty_ownership",
                    "complete candidate set cannot own a placeholder",
                )
            if {edge.id for edge in carrying} != set(uncertainty.candidate_edge_ids):
                _fail(
                    "uncertainty_ownership",
                    "complete candidate edge ownership is not exact",
                )
            targets = tuple(sorted({edge.target_id for edge in candidate_edges}))
            if targets != uncertainty.candidate_target_node_ids:
                _fail(
                    "uncertainty_ownership",
                    "complete candidate target closure is not exact",
                )
            if len(targets) != len(candidate_edges):
                _fail(
                    "uncertainty_ownership",
                    "complete candidate edges must have distinct targets",
                )
            minimum, maximum = _RESOLUTION_MATRIX[uncertainty.resolution_kind][3]
            if not minimum <= len(candidate_edges) <= maximum:
                _fail(
                    "uncertainty_cardinality",
                    "complete candidate cardinality is invalid",
                )
            if isinstance(uncertainty.subject, EdgeSubject) and (
                not uncertainty.candidate_edge_ids
                or uncertainty.subject.edge_id != uncertainty.candidate_edge_ids[0]
            ):
                _fail(
                    "uncertainty_ownership",
                    "edge subject is not the canonical first candidate",
                )
            for edge in candidate_edges:
                _validate_resolution_edge(uncertainty, edge, index, placeholder=False)
                expected_origin = (
                    EvidenceOrigin.UNRESOLVED
                    if isinstance(uncertainty.subject, EdgeSubject)
                    and edge.id == uncertainty.subject.edge_id
                    else EvidenceOrigin.INFERRED
                )
                if edge.evidence.primary.origin is not expected_origin:
                    _fail(
                        "uncertainty_ownership", "candidate evidence origin is invalid"
                    )
            assertion_keys = {
                (
                    edge.source_id,
                    edge.relation,
                    edge.flow,
                    canonical_json_bytes(artifact_to_payload_value(edge.occurrence)),
                    edge.call_site_id,
                    edge.exception_scope_id,
                    canonical_json_bytes(artifact_to_payload_value(edge.condition)),
                    edge.order,
                )
                for edge in candidate_edges
            }
            if len(assertion_keys) != 1:
                _fail(
                    "uncertainty_ownership",
                    "complete candidates do not express one semantic assertion",
                )
            dynamic_groups = {
                edge.branch_group_id
                for edge in candidate_edges
                if edge.branch_group_id is not None
                and index.structures[edge.branch_group_id].subtype
                is StructureSubtype.DYNAMIC_DISPATCH
            }
            if len(candidate_edges) > 1:
                if len(dynamic_groups) != 1 or any(
                    edge.branch_group_id not in dynamic_groups
                    for edge in candidate_edges
                ):
                    _fail(
                        "uncertainty_dynamic_dispatch",
                        "multi-target candidates require one dynamic-dispatch group",
                    )
                dynamic_group = next(iter(dynamic_groups))
                if any(
                    edge.branch_group_id == dynamic_group
                    and edge.uncertainty_id != uncertainty.id
                    for edge in artifact.edges
                ) or any(
                    step.branch_group_id == dynamic_group
                    and step.edge_id not in uncertainty.candidate_edge_ids
                    for step in artifact.flow_steps
                ):
                    _fail(
                        "uncertainty_dynamic_dispatch",
                        "dynamic-dispatch group is not assertion-exclusive",
                    )
            elif dynamic_groups:
                _fail(
                    "uncertainty_dynamic_dispatch",
                    "one-target candidate set cannot create dynamic dispatch",
                )
        else:
            if (
                len(boundaries) != 1
                or boundaries[0].kind is not NodeKind.UNKNOWN_BOUNDARY
            ):
                _fail(
                    "uncertainty_ownership",
                    "uncertainty ownership requires one placeholder",
                )
            subject = _subject_edge(uncertainty, carrying, index)
            if (
                subject.uncertainty_id != uncertainty.id
                or subject.target_id != boundaries[0].id
            ):
                _fail(
                    "uncertainty_ownership",
                    "semantic subject does not own its placeholder",
                )
            if subject.evidence.primary.origin is not EvidenceOrigin.UNRESOLVED:
                _fail(
                    "uncertainty_ownership",
                    "semantic subject evidence must be unresolved",
                )
            _validate_resolution_edge(uncertainty, subject, index, placeholder=True)
            if (
                uncertainty.candidate_set_knowledge
                is CandidateSetKnowledge.NOT_APPLICABLE
            ):
                if (
                    uncertainty.candidate_edge_ids
                    or uncertainty.candidate_target_node_ids
                ):
                    _fail(
                        "uncertainty_ownership",
                        "not-applicable candidate arrays must be empty",
                    )
            else:
                if not uncertainty.candidate_target_node_ids:
                    _fail(
                        "uncertainty_ownership",
                        "incomplete candidates require target hints",
                    )
            permitted = {subject.id, *uncertainty.candidate_edge_ids}
            if {edge.id for edge in carrying} != permitted:
                _fail("uncertainty_ownership", "incomplete hint ownership is not exact")
            for edge in candidate_edges:
                if edge.evidence.primary.origin is not EvidenceOrigin.INFERRED:
                    _fail("uncertainty_ownership", "incomplete hint must be inferred")
                if edge.target_id not in uncertainty.candidate_target_node_ids:
                    _fail(
                        "uncertainty_ownership",
                        "candidate edge target is not a listed hint",
                    )
                _validate_resolution_edge(uncertainty, edge, index, placeholder=False)
            incoming = tuple(
                edge for edge in artifact.edges if edge.target_id == boundaries[0].id
            )
            outgoing = tuple(
                edge for edge in artifact.edges if edge.source_id == boundaries[0].id
            )
            if incoming != (subject,) or outgoing:
                _fail("uncertainty_ownership", "placeholder is not assertion-exclusive")
            boundary_id = boundaries[0].id
            if (
                any(
                    structure.continuation_node_id == boundary_id
                    for structure in artifact.structures
                )
                or any(
                    flow.entrypoint_id == boundary_id
                    or flow.root_node_id == boundary_id
                    for flow in artifact.flows
                )
                or any(
                    entrypoint.id == boundary_id
                    or entrypoint.handler_node_id == boundary_id
                    for entrypoint in artifact.entrypoints
                )
                or any(
                    boundary_id in other.candidate_target_node_ids
                    for other in artifact.uncertainties
                )
            ):
                _fail(
                    "uncertainty_ownership",
                    "placeholder is referenced outside its semantic assertion",
                )


def validate_flow_membership(artifact: GraphArtifactV2, index: _RecordIndex) -> None:
    steps_by_flow: dict[str, list[object]] = defaultdict(list)
    for step in artifact.flow_steps:
        flow = index.flows[step.flow_id]
        edge = index.edges[step.edge_id]
        if edge.relation in _STRUCTURAL_RELATIONS:
            _fail("flow_membership", "structural edge cannot be a lifecycle flow step")
        if step.branch_group_id != edge.branch_group_id:
            _fail("flow_membership", "flow-step branch identity differs from its edge")
        expected_context = (
            AsyncContext.LINKED_ASYNC
            if flow.kind is FlowKind.ASYNC_FLOW
            else AsyncContext.SYNCHRONOUS
        )
        if step.async_context is not expected_context:
            _fail("flow_membership", "flow-step async context differs from its flow")
        if edge.flow is EdgeFlow.ASYNC:
            if step.backbone_role is not BackboneRole.ASYNC:
                _fail("flow_backbone", "async edge must use the async backbone role")
        elif edge.flow is EdgeFlow.EXCEPTION:
            if step.backbone_role is not BackboneRole.EXCEPTION:
                _fail("flow_backbone", "exception edge must use the exception role")
        elif edge.flow is EdgeFlow.LOOP:
            if step.backbone_role is not BackboneRole.LOOP:
                _fail("flow_backbone", "loop edge must use the loop role")
        elif (
            step.backbone_role is BackboneRole.MANDATORY
            and edge.flow is not EdgeFlow.ALWAYS
        ):
            _fail("flow_backbone", "mandatory flow step requires an always edge")
        if step.async_child_flow_id is not None:
            if (
                edge.flow is not EdgeFlow.ASYNC
                or step.backbone_role is not BackboneRole.ASYNC
            ):
                _fail(
                    "flow_async_link", "child flow link requires an async dispatch edge"
                )
        if step.async_cycle and step.async_child_flow_id is None:
            _fail("flow_async_link", "async cycle requires a child flow link")
        if edge.uncertainty_id is not None:
            uncertainty = index.uncertainties[edge.uncertainty_id]
            allowed = (
                edge.id in uncertainty.candidate_edge_ids
                if uncertainty.candidate_set_knowledge is CandidateSetKnowledge.COMPLETE
                else (
                    isinstance(uncertainty.subject, EdgeSubject)
                    and edge.id == uncertainty.subject.edge_id
                )
                or (
                    isinstance(uncertainty.subject, CallSiteSubject)
                    and edge.call_site_id == uncertainty.subject.call_site_id
                    and edge.evidence.primary.origin is EvidenceOrigin.UNRESOLVED
                )
            )
            if not allowed:
                _fail(
                    "flow_frontier", "incomplete hint edge cannot have flow membership"
                )
            if edge.flow is EdgeFlow.ASYNC and step.async_child_flow_id is not None:
                _fail(
                    "flow_frontier",
                    "uncertain async frontier cannot materialize a child",
                )
        steps_by_flow[flow.id].append(step)
    for flow in artifact.flows:
        entrypoint = next(
            (
                record
                for record in artifact.entrypoints
                if record.id == flow.entrypoint_id
            ),
            None,
        )
        if entrypoint is None:
            _fail("flow_membership", "flow entrypoint is not an entrypoint record")
        if (
            flow.kind is FlowKind.REQUEST_LIFECYCLE
            and entrypoint.entrypoint_kind is not EntrypointKind.HTTP_ROUTE
        ):
            _fail("flow_kind", "request lifecycle must have an HTTP entrypoint")
        if (
            flow.kind is FlowKind.EXECUTION_FLOW
            and entrypoint.entrypoint_kind is EntrypointKind.HTTP_ROUTE
        ):
            _fail("flow_kind", "HTTP entrypoint must use request lifecycle")
        if (
            flow.kind is not FlowKind.ASYNC_FLOW
            and flow.root_node_id != flow.entrypoint_id
        ):
            _fail("flow_root", "synchronous flow root must equal its entrypoint")
        flow_steps = steps_by_flow[flow.id]
        frontier_targets = {
            index.edges[step.edge_id].target_id
            for step in flow_steps
            if index.edges[step.edge_id].uncertainty_id is not None
        }
        if any(
            index.edges[step.edge_id].source_id in frontier_targets
            for step in flow_steps
        ):
            _fail("flow_frontier", "flow continues beyond an uncertainty frontier")
        order_keys = [step.order_key for step in flow_steps]
        if len(order_keys) != len(set(order_keys)):
            _fail("flow_membership", "flow-step order keys must be unique per flow")
    synchronous = Counter(
        flow.entrypoint_id
        for flow in artifact.flows
        if flow.kind is not FlowKind.ASYNC_FLOW
    )
    if any(synchronous[entrypoint.id] != 1 for entrypoint in artifact.entrypoints):
        _fail("flow_membership", "every entrypoint requires one synchronous flow")


def _validate_capability(capability: Capability) -> None:
    complete = capability.status in {
        CapabilityStatus.FULL,
        CapabilityStatus.NOT_APPLICABLE,
    }
    if complete != (not capability.reasons):
        _fail("completeness_reason_mismatch", "capability status and reasons disagree")


def _validate_count_shape(count: object) -> None:
    represented = cast(int, getattr(count, "represented"))
    value = cast(int | None, getattr(count, "value"))
    knowledge = cast(Knowledge, getattr(count, "knowledge"))
    reason = cast(ReasonCode | None, getattr(count, "reason"))
    if knowledge is Knowledge.EXACT:
        valid = represented == value and represented > 0 and reason is None
    elif knowledge is Knowledge.ABSENCE_VERIFIED:
        valid = represented == 0 and value == 0 and reason is None
    else:
        valid = value is None and represented >= 0 and reason is not None
    if not valid:
        _fail(
            "count_knowledge_mismatch", "count knowledge violates zero-versus-unknown"
        )


def _require_represented(count: object, represented: int) -> None:
    _validate_count_shape(count)
    if getattr(count, "represented") != represented:
        _fail(
            "flow_count_mismatch", "flow count does not close over represented records"
        )


def _validate_count_relation(
    count: object,
    represented: int,
    capabilities: tuple[Capability, ...],
) -> None:
    _require_represented(count, represented)
    reasons = sorted(
        {
            reason.code
            for capability in capabilities
            if capability.status
            in {CapabilityStatus.PARTIAL, CapabilityStatus.UNSUPPORTED}
            for reason in capability.reasons
        },
        key=lambda item: item.value,
    )
    knowledge = cast(Knowledge, getattr(count, "knowledge"))
    reason = cast(ReasonCode | None, getattr(count, "reason"))
    if reasons:
        if knowledge is not Knowledge.UNKNOWN or reason is not reasons[0]:
            _fail(
                "count_completeness_mismatch",
                "unknown count does not use the first applicable completeness reason",
            )
    else:
        expected = Knowledge.EXACT if represented else Knowledge.ABSENCE_VERIFIED
        if knowledge is not expected:
            _fail(
                "count_completeness_mismatch",
                "complete capability count is not exact or absence-verified",
            )


def _status_matches_capabilities(
    status: CompletenessStatus,
    capabilities: object,
) -> bool:
    partial = any(
        getattr(capabilities, name).status
        in {CapabilityStatus.PARTIAL, CapabilityStatus.UNSUPPORTED}
        for name in _CAPABILITY_ORDER
    )
    return (status is CompletenessStatus.PARTIAL) == partial


def validate_coverage_and_counts(artifact: GraphArtifactV2) -> None:
    contract = artifact.graph_contract
    all_capabilities = [contract.completeness.capabilities]
    all_capabilities.extend(
        item.capabilities for item in contract.completeness.languages
    )
    all_capabilities.extend(item.completeness.capabilities for item in artifact.flows)
    for capabilities in all_capabilities:
        for name in _CAPABILITY_ORDER:
            _validate_capability(getattr(capabilities, name))
    for language in contract.completeness.languages:
        if not _status_matches_capabilities(language.status, language.capabilities):
            _fail(
                "completeness_status_mismatch",
                "language completeness does not close over capabilities",
            )
    for flow in artifact.flows:
        if not _status_matches_capabilities(
            flow.completeness.status, flow.completeness.capabilities
        ):
            _fail(
                "completeness_status_mismatch",
                "flow completeness does not close over capabilities",
            )
    any_partial = (
        any(
            capability.status
            in {CapabilityStatus.PARTIAL, CapabilityStatus.UNSUPPORTED}
            for capabilities in all_capabilities
            for capability in (
                getattr(capabilities, name) for name in _CAPABILITY_ORDER
            )
        )
        or any(
            item.status is CompletenessStatus.PARTIAL
            for item in contract.completeness.languages
        )
        or any(
            item.completeness.status is CompletenessStatus.PARTIAL
            for item in artifact.flows
        )
    )
    if (contract.completeness.status is CompletenessStatus.PARTIAL) != any_partial:
        _fail(
            "completeness_status_mismatch",
            "global completeness does not close over capabilities",
        )

    record_counts = contract.coverage.records
    expected_records = {
        "nodes": len(artifact.nodes),
        "structures": len(artifact.structures),
        "edges": len(artifact.edges),
        "flows": len(artifact.flows),
        "flow_steps": len(artifact.flow_steps),
        "uncertainties": len(artifact.uncertainties),
    }
    if any(
        getattr(record_counts, name) != value
        for name, value in expected_records.items()
    ):
        _fail(
            "record_coverage_mismatch",
            "record coverage does not match represented records",
        )

    files = tuple(
        node for node in artifact.nodes if isinstance(node.identity, FileIdentity)
    )
    coverage = contract.coverage.files
    if coverage.discovered != len(files) or coverage.hashed != len(files):
        _fail("file_coverage_mismatch", "file coverage does not match inventory nodes")
    status_counts = Counter(
        cast(FileProperties, node.properties).analysis_status.value for node in files
    )
    for field_name in (
        "analyzed",
        "unsupported",
        "failed",
        "too_large",
        "budget_omitted",
    ):
        if getattr(coverage, field_name) != status_counts[field_name]:
            _fail("file_coverage_mismatch", "file status coverage does not close")
    if (
        coverage.parser_candidates > coverage.discovered
        or coverage.analyzed > coverage.parser_candidates
    ):
        _fail("file_coverage_mismatch", "parser candidate coverage is inconsistent")

    kind_counts = Counter(item.entrypoint_kind.value for item in artifact.entrypoints)
    entrypoint_coverage = contract.coverage.entrypoints
    if entrypoint_coverage.detected != len(artifact.entrypoints):
        _fail("entrypoint_coverage_mismatch", "entrypoint coverage does not close")
    represented_kind_counts = {
        item.metadata.get("wire_name", item.name): getattr(
            entrypoint_coverage.by_kind, item.name
        )
        for item in fields(entrypoint_coverage.by_kind)
        if getattr(entrypoint_coverage.by_kind, item.name) is not None
    }
    if represented_kind_counts != dict(kind_counts):
        _fail("entrypoint_coverage_mismatch", "entrypoint kind counts do not close")
    full_entrypoints = sum(
        1
        for flow in artifact.flows
        if flow.kind is not FlowKind.ASYNC_FLOW
        and flow.completeness.status is CompletenessStatus.FULL
    )
    partial_entrypoints = sum(
        1
        for flow in artifact.flows
        if flow.kind is not FlowKind.ASYNC_FLOW
        and flow.completeness.status is CompletenessStatus.PARTIAL
    )
    if (
        entrypoint_coverage.analyzed != full_entrypoints
        or entrypoint_coverage.partial != partial_entrypoints
    ):
        _fail("entrypoint_coverage_mismatch", "entrypoint analysis counts do not close")

    file_language_counts = Counter(
        node.identity.language
        for node in files
        if isinstance(node.identity, FileIdentity)
        and node.identity.language is not None
    )
    analyzed_language_counts = Counter(
        node.identity.language
        for node in files
        if isinstance(node.identity, FileIdentity)
        and node.identity.language is not None
        and cast(FileProperties, node.properties).analysis_status.value == "analyzed"
    )
    if {item.name for item in artifact.languages} != set(file_language_counts):
        _fail(
            "language_coverage_mismatch", "language records do not match file inventory"
        )
    for language in artifact.languages:
        if (
            language.detected_file_count != file_language_counts[language.name]
            or language.analyzed_file_count != analyzed_language_counts[language.name]
        ):
            _fail("language_coverage_mismatch", "language file counts do not close")
    if {item.language for item in contract.completeness.languages} != {
        item.name for item in artifact.languages
    }:
        _fail(
            "language_coverage_mismatch", "language completeness records do not close"
        )

    edges_by_id = {edge.id: edge for edge in artifact.edges}
    steps_by_flow: dict[str, list[object]] = defaultdict(list)
    for step in artifact.flow_steps:
        steps_by_flow[step.flow_id].append(step)
    for flow in artifact.flows:
        steps = steps_by_flow[flow.id]
        if flow.represented_step_count != len(steps):
            _fail("flow_count_mismatch", "represented flow-step count does not close")
        linked = len({
            step.async_child_flow_id for step in steps if step.async_child_flow_id
        })
        uncertainties = len({
            edges_by_id[step.edge_id].uncertainty_id
            for step in steps
            if edges_by_id[step.edge_id].uncertainty_id is not None
        })
        terminals = len({
            edges_by_id[step.edge_id].target_id
            for step in steps
            if next(
                node
                for node in artifact.nodes
                if node.id == edges_by_id[step.edge_id].target_id
            ).kind
            in _TERMINAL_KINDS
        })
        capabilities = flow.completeness.capabilities
        _validate_count_relation(
            flow.linked_async_flow_count,
            linked,
            (capabilities.inventory, capabilities.call_graph, capabilities.async_),
        )
        _validate_count_relation(
            flow.uncertainty_count,
            uncertainties,
            tuple(getattr(capabilities, name) for name in _CAPABILITY_ORDER),
        )
        _validate_count_relation(
            flow.terminal_count,
            terminals,
            (
                capabilities.inventory,
                capabilities.call_graph,
                capabilities.control_flow,
                capabilities.exceptions,
            ),
        )
        stage_members: dict[Stage, set[str]] = defaultdict(set)
        stage_members[Stage.ENTRY].add(flow.root_node_id)
        for step in steps:
            edge = edges_by_id[step.edge_id]
            stage_members[step.stage_from].add(edge.source_id)
            stage_members[step.stage_to].add(edge.target_id)
        for model_field in fields(flow.stage_counts):
            count = getattr(flow.stage_counts, model_field.name)
            stage = Stage(
                cast(str, model_field.metadata.get("wire_name", model_field.name))
            )
            represented = len(stage_members[stage])
            if count is None:
                if represented:
                    _fail("flow_count_mismatch", "applicable stage count is missing")
            else:
                stage_capabilities = {
                    Stage.ENTRY: (capabilities.entrypoint_discovery,),
                    Stage.ROUTING: (
                        capabilities.entrypoint_discovery,
                        capabilities.framework_lifecycle,
                    ),
                    Stage.MIDDLEWARE: (capabilities.framework_lifecycle,),
                    Stage.SECURITY: (capabilities.framework_lifecycle,),
                    Stage.INPUT: (capabilities.framework_lifecycle,),
                    Stage.HANDLER: (capabilities.symbol_resolution,),
                    Stage.DOMAIN: (
                        capabilities.call_graph,
                        capabilities.control_flow,
                    ),
                    Stage.DATA: (capabilities.data_access,),
                    Stage.INTEGRATION: (capabilities.data_access,),
                    Stage.ASYNC: (capabilities.async_,),
                    Stage.RESPONSE: (capabilities.control_flow,),
                    Stage.ERROR: (capabilities.exceptions,),
                }[stage]
                _validate_count_relation(count, represented, stage_capabilities)

    omitted_reasons: set[ReasonCode] = set()
    if coverage.failed:
        omitted_reasons.add(ReasonCode.FILE_READ_FAILED)
    if coverage.too_large:
        omitted_reasons.add(ReasonCode.FILE_TOO_LARGE)
    if coverage.budget_omitted or record_counts.omitted_by_bundle_budget:
        omitted_reasons.add(ReasonCode.RESOURCE_BUDGET_REACHED)
    global_reason_codes = {
        reason.code
        for name in _CAPABILITY_ORDER
        for reason in getattr(contract.completeness.capabilities, name).reasons
    }
    if omitted_reasons - global_reason_codes:
        _fail("coverage_omission_reason", "in-scope omission lacks a counted reason")


def validate_artifact_digest(artifact: GraphArtifactV2) -> None:
    payload = artifact_to_payload(artifact)
    if artifact.graph_contract.artifact_graph_version != artifact_graph_version(
        payload
    ):
        _fail(
            "artifact_digest_mismatch",
            "artifact graph version does not match the semantic payload digest",
        )


def validate_artifact(
    artifact: GraphArtifactV2 | Mapping[str, JsonValue],
) -> None:
    """Validate one base artifact in the frozen normative pass order."""

    model = _coerce_artifact(artifact)
    validate_scalar_and_privacy_rules(model)
    validate_sorted_unique_records(model)
    index = build_record_index(model)
    validate_identity_recomputation(model, index)
    validate_references(model, index)
    validate_uncertainty_ownership(model, index)
    validate_flow_membership(model, index)
    validate_coverage_and_counts(model)
    validate_artifact_digest(model)


__all__ = [
    "GraphValidationError",
    "build_record_index",
    "validate_artifact",
    "validate_artifact_digest",
    "validate_coverage_and_counts",
    "validate_flow_membership",
    "validate_identity_recomputation",
    "validate_references",
    "validate_scalar_and_privacy_rules",
    "validate_sorted_unique_records",
    "validate_uncertainty_ownership",
]
