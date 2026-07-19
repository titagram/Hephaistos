"""Canonical graph-v2 producer for frozen lifecycle adapter facts.

The builder is deliberately the only place where adapter-local keys become
public graph identities.  It never accepts a legacy graph payload and never
repairs an invalid adapter result: the read-only IR boundary is validated
before any canonical record is materialized.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import TypeVar

from hermes_cli.hades_graph_v2 import (
    artifact_graph_version,
    ast_source_fingerprint,
    branch_group_id,
    call_site_id,
    condition_hash,
    config_source_fingerprint,
    edge_id,
    exception_scope_id,
    file_source_fingerprint,
    flow_id,
    flow_step_id,
    node_id,
    sha256_jcs,
    uncertainty_fingerprint,
    validate_artifact,
)
from hermes_cli.hades_graph_v2.coverage import count_knowledge
from hermes_cli.hades_graph_v2.model import (
    AnalysisStatus,
    AsyncContext,
    AsyncProperties,
    BackboneRole,
    BoundaryProperties,
    CallableProperties,
    Capabilities,
    Capability,
    CapabilityReason,
    CapabilityStatus,
    Completeness,
    CompletenessStatus,
    Condition,
    ConditionPolarity,
    ControlProperties,
    CountKnowledge,
    Coverage,
    CoverageScope,
    DataProperties,
    Edge,
    EdgeAstOccurrence,
    EdgeConfigOccurrence,
    EdgeFlow,
    EdgeLocation,
    EdgeSubject,
    Entrypoint,
    EntrypointCoverage,
    EntrypointIdentity,
    EntrypointKind,
    EntrypointKindCounts,
    EntrypointNodeIdentity,
    EvidenceEnvelope,
    EvidenceItem,
    EvidenceOrigin,
    EXECUTABLE_SOURCE_DECLARATION_KINDS,
    FileCoverage,
    FileIdentity,
    FileProperties,
    FileSourceLocator,
    Flow,
    FlowCompleteness,
    FlowKind,
    FlowStep,
    FrameworkProperties,
    FrameworkRecord,
    GraphArtifactV2,
    GraphContractMetadata,
    LanguageCompleteness,
    LanguageRecord,
    MatchConstraints,
    Node,
    NodeKind,
    ProjectIdentity,
    ReasonCode,
    Relation,
    RecordCoverage,
    RegistrationAst,
    RegistrationConfig,
    SemanticResourceIdentity,
    IntegrationProperties,
    SourceDeclarationIdentity,
    SourceLocation,
    SourceOccurrenceIdentity,
    SourceRef,
    Stage,
    StageCounts,
    Structure,
    StructureKind,
    StructureSubtype,
    TerminalProperties,
    TestProperties,
    Trigger,
    TypeProperties,
    Uncertainty,
)
from hermes_cli.hades_index.lifecycle.model import (
    AdapterResult,
    AlwaysSuccessor,
    AstLocatorIR,
    AsyncSuccessor,
    BasicBlock,
    BlockEffectSource,
    BoundaryTarget,
    BranchSuccessor,
    CallSite,
    CallSiteEffectSource,
    CallSiteSubjectIR,
    CandidateSetKnowledge,
    ConfigLocatorIR,
    ControlKind,
    CoverageCapability,
    CoverageEvent,
    CoverageOutcome,
    DataNodeIR,
    DeclarationIdentityKind,
    EdgeFactIR,
    EdgeSubjectIR,
    Effect,
    EffectKind,
    EntrypointCandidate,
    ExceptionScope,
    ExecutableDeclaration,
    ExtractionContext,
    FileLocatorIR,
    FrameworkBoundaryTarget,
    FrameworkLocalTarget,
    FrameworkPipelineSegment,
    IREvidence,
    IRValidationError,
    LocalNodeTarget,
    LoopRole,
    LoopSuccessor,
    Modifier,
    Priority,
    ResolutionKind,
    StructureIR,
    ExceptionSuccessor,
    ReturnSuccessor,
    SourceNodeIR,
    Terminal,
    TerminalKind,
    UnresolvedFact,
)
from hermes_cli.hades_index.lifecycle.traversal import (
    CanonicalTopology,
    build_callable_summaries,
    build_lifecycle_flows,
)


_T = TypeVar("_T")
_ZERO_DIGEST = "0" * 64
_STAGE_ORDER = tuple(Stage)
_CAPABILITY_FIELDS = (
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
_CALLABLE_OWNER_KINDS = frozenset({
    NodeKind.MODULE,
    NodeKind.ENTRYPOINT,
    *EXECUTABLE_SOURCE_DECLARATION_KINDS,
})
_CAPABILITY_BY_IR = {
    CoverageCapability.INVENTORY: "inventory",
    CoverageCapability.ENTRYPOINT_DISCOVERY: "entrypoint_discovery",
    CoverageCapability.SYMBOL_RESOLUTION: "symbol_resolution",
    CoverageCapability.CALL_GRAPH: "call_graph",
    CoverageCapability.CONTROL_FLOW: "control_flow",
    CoverageCapability.FRAMEWORK_LIFECYCLE: "framework_lifecycle",
    CoverageCapability.EXCEPTIONS: "exceptions",
    CoverageCapability.ASYNC: "async_",
    CoverageCapability.DATA_ACCESS: "data_access",
}
_CAPABILITY_BY_RESOLUTION = {
    "call_target": "call_graph",
    "entrypoint_handler": "entrypoint_discovery",
    "async_target": "async_",
    "exception_target": "exceptions",
    "framework_target": "framework_lifecycle",
    "external_target": "data_access",
}
_CONTROL_NODE_KIND = {
    ControlKind.ENTRY: NodeKind.BASIC_BLOCK,
    ControlKind.STRAIGHT_LINE: NodeKind.BASIC_BLOCK,
    ControlKind.BRANCH: NodeKind.BRANCH,
    ControlKind.MERGE: NodeKind.MERGE,
    ControlKind.LOOP_HEADER: NodeKind.LOOP,
    ControlKind.LOOP_BODY: NodeKind.LOOP,
    ControlKind.CATCH: NodeKind.BASIC_BLOCK,
    ControlKind.FINALLY: NodeKind.BASIC_BLOCK,
    ControlKind.RETURN: NodeKind.BASIC_BLOCK,
    ControlKind.THROW: NodeKind.BASIC_BLOCK,
    ControlKind.ASYNC_DISPATCH: NodeKind.BASIC_BLOCK,
}
_TERMINAL_NODE_KIND = {
    TerminalKind.RESPONSE: NodeKind.RESPONSE,
    TerminalKind.REDIRECT: NodeKind.REDIRECT,
    TerminalKind.ABORT: NodeKind.ABORT,
    TerminalKind.EXCEPTION: NodeKind.EXCEPTION,
    TerminalKind.EXIT: NodeKind.EXIT,
}


def _framework_node_kind(role: str) -> NodeKind:
    if "middleware" in role:
        return NodeKind.MIDDLEWARE
    if "authorization" in role or "permission" in role or "policy" in role:
        return NodeKind.AUTHORIZATION
    if "auth" in role or "guard" in role or "security" in role:
        return NodeKind.GUARD
    if "validat" in role:
        return NodeKind.VALIDATOR
    if "bind" in role or "dependency" in role:
        return NodeKind.BINDING
    if "unresolved" in role or role.endswith("_boundary"):
        return NodeKind.FRAMEWORK_BOUNDARY
    return NodeKind.MIDDLEWARE


_EFFECT_KIND_MAPPING: dict[EffectKind, tuple[NodeKind, Relation, EdgeFlow]] = {
    EffectKind.DATA_READ: (NodeKind.QUERY, Relation.READS, EdgeFlow.ALWAYS),
    EffectKind.DATA_WRITE: (NodeKind.QUERY, Relation.WRITES, EdgeFlow.ALWAYS),
    EffectKind.CACHE_READ: (NodeKind.CACHE, Relation.READS, EdgeFlow.ALWAYS),
    EffectKind.CACHE_WRITE: (NodeKind.CACHE, Relation.WRITES, EdgeFlow.ALWAYS),
    EffectKind.STORAGE_READ: (NodeKind.STORAGE, Relation.READS, EdgeFlow.ALWAYS),
    EffectKind.STORAGE_WRITE: (NodeKind.STORAGE, Relation.WRITES, EdgeFlow.ALWAYS),
    EffectKind.EXTERNAL_CALL: (
        NodeKind.EXTERNAL_BOUNDARY,
        Relation.CALLS_EXTERNAL,
        EdgeFlow.ALWAYS,
    ),
    EffectKind.EVENT_EMIT: (NodeKind.EVENT, Relation.EMITS, EdgeFlow.ASYNC),
    EffectKind.JOB_DISPATCH: (NodeKind.JOB, Relation.DISPATCHES, EdgeFlow.ASYNC),
    EffectKind.QUEUE_DISPATCH: (NodeKind.QUEUE, Relation.DISPATCHES, EdgeFlow.ASYNC),
}


def effect_kind_mapping(kind: EffectKind) -> tuple[NodeKind, Relation, EdgeFlow]:
    """Return the frozen public graph mapping for one semantic effect."""

    return _EFFECT_KIND_MAPPING[kind]


def _default_generated_at() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


def _deduplicate(
    records: Iterable[_T], *, key: Callable[[_T], object]
) -> tuple[_T, ...]:
    """Deduplicate identical facts and reject one local identity with two values."""

    by_key: dict[object, _T] = {}
    for record in records:
        identity = key(record)
        previous = by_key.get(identity)
        if previous is not None and previous != record:
            raise IRValidationError(
                "conflicting_local_key",
                "adapter results disagree about one deterministic local identity",
            )
        by_key.setdefault(identity, record)
    return tuple(by_key[item] for item in sorted(by_key, key=repr))


def _evidence_item_key(item: EvidenceItem) -> tuple[object, ...]:
    return (
        {
            EvidenceOrigin.VERIFIED_FROM_CODE: 1,
            EvidenceOrigin.INFERRED: 3,
            EvidenceOrigin.UNRESOLVED: 4,
        }[item.origin],
        item.extractor,
        item.source_fingerprint,
        repr(item.source_locator),
    )


def _merge_evidence(
    left: EvidenceEnvelope, right: EvidenceEnvelope
) -> EvidenceEnvelope:
    values = {
        _evidence_item_key(item): item
        for envelope in (left, right)
        for item in (envelope.primary, *envelope.supporting)
    }
    ordered = tuple(values[key] for key in sorted(values))
    omitted = left.supporting_omitted_count + right.supporting_omitted_count
    supporting = ordered[1:8]
    omitted += max(0, len(ordered) - 8)
    return EvidenceEnvelope(ordered[0], supporting, omitted)


def _insert_public_record(
    by_id: dict[str, _T],
    record: _T,
    *,
    record_name: str,
) -> None:
    """Apply the normative public-ID collision rule to one evidenced record."""

    public_id = getattr(record, "id")
    previous = by_id.get(public_id)
    if previous is None:
        by_id[public_id] = record
        return
    if previous == record:
        return
    previous_evidence = getattr(previous, "evidence")
    record_evidence = getattr(record, "evidence")
    if replace(record, evidence=previous_evidence) == previous:
        by_id[public_id] = replace(
            previous,
            evidence=_merge_evidence(previous_evidence, record_evidence),
        )
        return
    raise IRValidationError(
        "semantic_collision",
        f"one canonical {record_name} identity maps to different semantic values",
    )


def _deduplicate_public_records(
    records: Iterable[_T], *, record_name: str
) -> tuple[_T, ...]:
    by_id: dict[str, _T] = {}
    for record in records:
        _insert_public_record(by_id, record, record_name=record_name)
    return tuple(by_id[key] for key in sorted(by_id))


def _deduplicate_edges(records: Iterable[Edge]) -> tuple[Edge, ...]:
    return _deduplicate_public_records(records, record_name="edge")


def _locator_path(locator: FileLocatorIR | AstLocatorIR | ConfigLocatorIR) -> str:
    if type(locator) is FileLocatorIR:
        return locator.path
    return locator.source_location.path


def _locator_digest(locator: FileLocatorIR | AstLocatorIR | ConfigLocatorIR) -> str:
    if type(locator) is FileLocatorIR:
        return locator.file_sha256
    return locator.source_location.file_sha256


def _source_location(locator: AstLocatorIR | ConfigLocatorIR) -> SourceLocation:
    location = locator.source_location
    return SourceLocation(location.path, location.start_line, location.end_line)


def _source_locator(locator: AstLocatorIR | ConfigLocatorIR):
    if type(locator) is AstLocatorIR:
        from hermes_cli.hades_graph_v2.model import AstSourceLocator

        return AstSourceLocator(
            "ast", locator.source_location.path, locator.structural_path
        )
    from hermes_cli.hades_graph_v2.model import ConfigSourceLocator

    return ConfigSourceLocator(
        "config", locator.source_location.path, locator.structural_pointer
    )


def _fingerprint(locator: AstLocatorIR | ConfigLocatorIR) -> str:
    location = locator.source_location
    if type(locator) is AstLocatorIR:
        return ast_source_fingerprint(
            location.file_sha256, location.path, locator.structural_path
        )
    return config_source_fingerprint(
        location.file_sha256, location.path, locator.structural_pointer
    )


def _evidence(
    evidence: IREvidence,
    *,
    origin: EvidenceOrigin | None = None,
    inference_rule: str | None = None,
) -> EvidenceEnvelope:
    locator = evidence.locator
    if type(locator) is FileLocatorIR:
        raise IRValidationError(
            "invalid_file_locator", "semantic graph facts require AST/config evidence"
        )
    selected_origin = evidence.origin if origin is None else origin
    selected_rule = evidence.inference_rule if origin is None else inference_rule
    return EvidenceEnvelope(
        EvidenceItem(
            selected_origin,
            evidence.extractor,
            _source_locator(locator),
            _fingerprint(locator),
            selected_rule,
        ),
        (),
        0,
    )


def _synthetic_evidence(
    locator: AstLocatorIR | ConfigLocatorIR,
    *,
    extractor: str = "hades.builder.v2",
    origin: EvidenceOrigin = EvidenceOrigin.VERIFIED_FROM_CODE,
    inference_rule: str | None = None,
) -> EvidenceEnvelope:
    return EvidenceEnvelope(
        EvidenceItem(
            origin,
            extractor,
            _source_locator(locator),
            _fingerprint(locator),
            inference_rule,
        ),
        (),
        0,
    )


def _registration(locator: AstLocatorIR | ConfigLocatorIR):
    if type(locator) is AstLocatorIR:
        return RegistrationAst(
            "ast",
            locator.source_location.path,
            locator.structural_path,
            locator.ordinal,
        )
    return RegistrationConfig(
        "config",
        locator.source_location.path,
        locator.structural_pointer,
        locator.ordinal,
    )


def _entrypoint_label(candidate: EntrypointCandidate) -> str:
    if candidate.kind is EntrypointKind.HTTP_ROUTE:
        method = (
            "|".join(candidate.methods)
            if candidate.method_semantics.value == "explicit"
            else "ALL"
        )
        return f"{method} {candidate.public_path}"
    return candidate.public_name or candidate.trigger_value


def _entrypoint_identity(
    context: ExtractionContext, candidate: EntrypointCandidate, language: str
) -> tuple[EntrypointIdentity, EntrypointNodeIdentity, str]:
    label = _entrypoint_label(candidate)
    trigger = Trigger(
        candidate.trigger,
        label
        if candidate.kind is EntrypointKind.HTTP_ROUTE
        else candidate.trigger_value,
    )
    constraints = MatchConstraints(
        candidate.match_constraints.host,
        candidate.match_constraints.schemes,
        candidate.match_constraints.condition_hash,
    )
    occurrence = _registration(candidate.registration_locator)
    entrypoint_identity = EntrypointIdentity(
        candidate.kind,
        candidate.framework,
        candidate.method_semantics,
        candidate.methods,
        candidate.public_path,
        candidate.public_name,
        trigger,
        constraints,
        occurrence,
    )
    identity = EntrypointNodeIdentity(
        "entrypoint",
        context.workspace_binding_id,
        language,
        "entrypoint",
        candidate.registration_locator.source_location.path,
        entrypoint_identity,
    )
    payload = {
        "variant": "entrypoint",
        "workspace_binding_id": context.workspace_binding_id,
        "language": language,
        "kind": "entrypoint",
        "path": identity.path,
        "entrypoint_identity": {
            "entrypoint_kind": candidate.kind.value,
            "framework": candidate.framework,
            "method_semantics": candidate.method_semantics.value,
            "methods": list(candidate.methods),
            "public_path": candidate.public_path,
            "public_name": candidate.public_name,
            "trigger": {"kind": trigger.kind.value, "value": trigger.value},
            "match_constraints": {
                "host": constraints.host,
                "schemes": list(constraints.schemes),
                "condition_hash": constraints.condition_hash,
            },
            "registration_occurrence": (
                {
                    "kind": "ast",
                    "path": occurrence.path,
                    "structural_path": occurrence.structural_path,
                    "ordinal": occurrence.ordinal,
                }
                if isinstance(occurrence, RegistrationAst)
                else {
                    "kind": "config",
                    "path": occurrence.path,
                    "structural_pointer": occurrence.structural_pointer,
                    "ordinal": occurrence.ordinal,
                }
            ),
        },
    }
    return entrypoint_identity, identity, node_id(payload)


def _reason_code(value: str) -> ReasonCode:
    try:
        return ReasonCode(value)
    except ValueError:
        return ReasonCode.INVALID_SOURCE_FACT


_EXCEPTION_BASES_BY_LANGUAGE: dict[str, dict[str, tuple[str, ...]]] = {
    "python": {
        "BaseException": (),
        "Exception": ("BaseException",),
        "ArithmeticError": ("Exception",),
        "AssertionError": ("Exception",),
        "AttributeError": ("Exception",),
        "EOFError": ("Exception",),
        "ImportError": ("Exception",),
        "ModuleNotFoundError": ("ImportError",),
        "LookupError": ("Exception",),
        "IndexError": ("LookupError",),
        "KeyError": ("LookupError",),
        "NameError": ("Exception",),
        "OSError": ("Exception",),
        "FileNotFoundError": ("OSError",),
        "PermissionError": ("OSError",),
        "TimeoutError": ("OSError",),
        "RuntimeError": ("Exception",),
        "NotImplementedError": ("RuntimeError",),
        "TypeError": ("Exception",),
        "ValueError": ("Exception",),
        "UnicodeError": ("ValueError",),
    },
    "php": {
        "Throwable": (),
        "Exception": ("Throwable",),
        "Error": ("Throwable",),
        "RuntimeException": ("Exception",),
        "LogicException": ("Exception",),
        "InvalidArgumentException": ("LogicException",),
        "OutOfBoundsException": ("RuntimeException",),
        "TypeError": ("Error",),
        "ValueError": ("Error",),
    },
    "javascript": {
        "Error": (),
        "AggregateError": ("Error",),
        "EvalError": ("Error",),
        "RangeError": ("Error",),
        "ReferenceError": ("Error",),
        "SyntaxError": ("Error",),
        "TypeError": ("Error",),
        "URIError": ("Error",),
    },
    "typescript": {
        "Error": (),
        "AggregateError": ("Error",),
        "EvalError": ("Error",),
        "RangeError": ("Error",),
        "ReferenceError": ("Error",),
        "SyntaxError": ("Error",),
        "TypeError": ("Error",),
        "URIError": ("Error",),
    },
}


def _exception_type_name(value: str) -> str:
    return value.rsplit(".", 1)[-1].rsplit("\\", 1)[-1]


class ExceptionMatch(str, Enum):
    MATCH = "match"
    NO_MATCH = "no_match"
    UNRESOLVED = "unresolved"


def _exception_type_matches(
    language: str, thrown: str | None, caught: str | None
) -> ExceptionMatch:
    """Match only exact or resolved built-in ancestry in the owner's language."""

    if caught is None:
        return ExceptionMatch.MATCH
    if thrown is None:
        return ExceptionMatch.UNRESOLVED
    thrown_name = _exception_type_name(thrown)
    caught_name = _exception_type_name(caught)
    if thrown == caught or thrown_name == caught_name:
        return ExceptionMatch.MATCH
    ancestry = _EXCEPTION_BASES_BY_LANGUAGE.get(language)
    if ancestry is None or thrown_name not in ancestry:
        return ExceptionMatch.UNRESOLVED
    if caught_name not in ancestry:
        return ExceptionMatch.NO_MATCH
    pending = list(ancestry[thrown_name])
    seen: set[str] = set()
    while pending:
        current = pending.pop()
        if current == caught_name:
            return ExceptionMatch.MATCH
        if current in seen:
            continue
        seen.add(current)
        pending.extend(ancestry.get(current, ()))
    return ExceptionMatch.NO_MATCH


def _all_locators(
    results: Sequence[AdapterResult],
) -> tuple[FileLocatorIR | AstLocatorIR | ConfigLocatorIR, ...]:
    values: list[FileLocatorIR | AstLocatorIR | ConfigLocatorIR] = []
    for result in results:
        for source_node in result.source_nodes:
            values.append(source_node.locator)
        for data_node in result.data_nodes:
            values.append(data_node.locator)
        for declaration in result.declarations:
            values.append(declaration.locator)
        for block in result.blocks:
            values.append(block.locator)
        for structure in result.structures:
            values.append(structure.evidence.locator)
        for site in result.call_sites:
            values.append(site.locator)
        for edge in result.edge_facts:
            values.extend((edge.locator, edge.evidence.locator))
            if type(edge.target) is BoundaryTarget:
                values.extend((
                    edge.target.descriptor.locator,
                    edge.target.descriptor.evidence.locator,
                ))
        for scope in result.exception_scopes:
            values.append(scope.locator)
        for terminal in result.terminals:
            values.append(terminal.locator)
        for effect in result.effects:
            values.append(effect.locator)
        for segment in result.framework_segments:
            values.append(segment.evidence.locator)
            if type(segment.target) is FrameworkBoundaryTarget:
                values.extend((
                    segment.target.descriptor.locator,
                    segment.target.descriptor.evidence.locator,
                ))
        for candidate in result.entrypoints:
            values.extend((candidate.registration_locator, candidate.evidence.locator))
        for fact in result.unresolved_facts:
            values.extend(fact.source_locators)
    return tuple(values)


class GraphBuilder:
    """Build one canonical graph-v2 artifact directly from validated adapter IR."""

    def __init__(self, *, generated_at: Callable[[], str] | None = None) -> None:
        self._generated_at = generated_at or _default_generated_at

    def build(
        self,
        context: ExtractionContext,
        results: Sequence[AdapterResult],
    ) -> GraphArtifactV2:
        collected = tuple(results)
        for result in collected:
            result.validate()
        from hermes_cli.hades_index.aggregate import aggregate_adapter_results

        collected = aggregate_adapter_results(collected)

        declarations = _deduplicate(
            (item for result in collected for item in result.declarations),
            key=lambda item: item.local_key,
        )
        source_nodes_ir = _deduplicate(
            (item for result in collected for item in result.source_nodes),
            key=lambda item: item.local_key,
        )
        source_node_by_key = {item.local_key: item for item in source_nodes_ir}
        data_nodes_ir = _deduplicate(
            (item for result in collected for item in result.data_nodes),
            key=lambda item: item.local_key,
        )
        blocks = _deduplicate(
            (item for result in collected for item in result.blocks),
            key=lambda item: item.local_key,
        )
        branch_arms = _deduplicate(
            (item for result in collected for item in result.branch_arms),
            key=lambda item: (item.branch_local_key, item.arm_ordinal),
        )
        structures_ir = _deduplicate(
            (item for result in collected for item in result.structures),
            key=lambda item: item.local_key,
        )
        call_sites = _deduplicate(
            (item for result in collected for item in result.call_sites),
            key=lambda item: item.local_key,
        )
        edge_facts = _deduplicate(
            (item for result in collected for item in result.edge_facts),
            key=lambda item: item.local_key,
        )
        terminals = _deduplicate(
            (item for result in collected for item in result.terminals),
            key=lambda item: item.local_key,
        )
        effects = _deduplicate(
            (item for result in collected for item in result.effects),
            key=lambda item: item.local_key,
        )
        framework_segments = _deduplicate(
            (item for result in collected for item in result.framework_segments),
            key=lambda item: item.local_key,
        )
        exception_scopes = _deduplicate(
            (item for result in collected for item in result.exception_scopes),
            key=lambda item: item.local_key,
        )
        entrypoint_candidates = _deduplicate(
            (item for result in collected for item in result.entrypoints),
            key=lambda item: (
                item.kind.value,
                item.framework or "",
                item.public_path or "",
                item.public_name or "",
                item.trigger_value,
                _locator_path(item.registration_locator),
                getattr(item.registration_locator, "structural_path", ""),
                getattr(item.registration_locator, "structural_pointer", ""),
                item.registration_locator.ordinal,
            ),
        )
        unresolved_facts = _deduplicate(
            (item for result in collected for item in result.unresolved_facts),
            key=lambda item: item.local_key,
        )
        coverage_events = _deduplicate(
            (item for result in collected for item in result.coverage_events),
            key=lambda item: (
                item.language,
                item.capability.value,
                item.outcome.value,
                item.reason_code or "",
                item.path or "",
            ),
        )

        declaration_by_key = {item.local_key: item for item in declarations}
        block_by_key = {item.local_key: item for item in blocks}
        structure_ir_by_key = {item.local_key: item for item in structures_ir}
        exception_scope_ir_by_key = {
            item.local_key: item for item in exception_scopes
        }
        call_site_by_key = {item.local_key: item for item in call_sites}
        edge_fact_by_key = {item.local_key: item for item in edge_facts}

        inventory_by_path = {item.path: item for item in context.inventory_files}
        digest_by_path = {
            item.path: item.file_sha256 for item in context.inventory_files
        }

        def candidate_language(candidate: EntrypointCandidate) -> str | None:
            declaration = declaration_by_key.get(candidate.handler_local_key or "")
            if declaration is not None:
                return declaration.language
            inventory = inventory_by_path.get(
                candidate.registration_locator.source_location.path
            )
            if inventory is not None and inventory.language is not None:
                return inventory.language
            if len(context.detected_languages) == 1:
                return context.detected_languages[0]
            return None

        locators = _all_locators(collected)
        for locator in locators:
            path = _locator_path(locator)
            digest = _locator_digest(locator)
            inventory = inventory_by_path.get(path)
            if inventory is None:
                raise IRValidationError(
                    "semantic_path_outside_inventory",
                    "semantic and evidence locators must belong to inventory_files",
                )
            if inventory.file_sha256 != digest:
                raise IRValidationError(
                    "conflicting_file_digest",
                    "one represented source path has conflicting file digests",
                )
        path_languages: dict[str, set[str]] = defaultdict(set)
        for item in context.inventory_files:
            if item.language is not None:
                path_languages[item.path].add(item.language)
        for declaration in declarations:
            path_languages[declaration.locator.source_location.path].add(
                declaration.language
            )
        for candidate in entrypoint_candidates:
            language = candidate_language(candidate) or "unknown"
            path_languages[candidate.registration_locator.source_location.path].add(
                language
            )
        effective_coverage_events = list(coverage_events)

        nodes: list[Node] = []
        local_node_ids: dict[str, str] = {}
        file_node_ids: dict[str, str] = {}
        coverage_by_path: dict[str, list[CoverageEvent]] = defaultdict(list)
        for event in coverage_events:
            if event.path is not None:
                coverage_by_path[event.path].append(event)
        for path in sorted(digest_by_path):
            inventory_file = inventory_by_path[path]
            languages = path_languages.get(path, set())
            language = next(iter(languages)) if len(languages) == 1 else None
            identity = FileIdentity(
                "file", context.workspace_binding_id, language, "file", path
            )
            public_id = node_id({
                "variant": "file",
                "workspace_binding_id": context.workspace_binding_id,
                "language": language,
                "kind": "file",
                "path": path,
            })
            file_node_ids[path] = public_id
            all_path_events = tuple(coverage_by_path.get(path, ()))
            inventory_path_events = tuple(
                event
                for event in all_path_events
                if event.capability is CoverageCapability.INVENTORY
            )
            unavailable_reason = next(
                (
                    _reason_code(event.reason_code)
                    for event in inventory_path_events
                    if event.reason_code
                    in {"symlink_unavailable", "submodule_unavailable"}
                ),
                None,
            )
            try:
                if unavailable_reason is not None:
                    raise OSError("inventory entry is unavailable")
                byte_size = (
                    inventory_file.byte_size
                    if inventory_file.byte_size is not None
                    else len(context.file_accessor(Path(path)))
                )
                analysis_status = AnalysisStatus.ANALYZED
                omission_reason = None
            except OSError:
                byte_size = 0
                analysis_status = AnalysisStatus.FAILED
                omission_reason = unavailable_reason or ReasonCode.FILE_READ_FAILED
                failure_language = language or (
                    context.detected_languages[0]
                    if len(context.detected_languages) == 1
                    else "unknown"
                )
                if unavailable_reason is None and not any(
                    event.path == path
                    and event.reason_code == ReasonCode.FILE_READ_FAILED.value
                    for event in effective_coverage_events
                ):
                    effective_coverage_events.append(
                        CoverageEvent(
                            failure_language,
                            CoverageCapability.INVENTORY,
                            CoverageOutcome.PARTIAL,
                            ReasonCode.FILE_READ_FAILED.value,
                            path,
                            0,
                            1,
                        )
                    )
            if analysis_status is AnalysisStatus.ANALYZED:
                if (
                    path in inventory_by_path
                    and not inventory_by_path[path].parser_candidate
                ):
                    analysis_status = AnalysisStatus.UNSUPPORTED
                failure_reason = next(
                    (
                        _reason_code(event.reason_code)
                        for event in all_path_events
                        if event.reason_code
                        in {
                            "parser_failed",
                            "file_read_failed",
                            "symlink_unavailable",
                            "submodule_unavailable",
                        }
                    ),
                    None,
                )
                if failure_reason is not None:
                    analysis_status = AnalysisStatus.FAILED
                    omission_reason = failure_reason
                elif any(
                    event.reason_code == "file_too_large"
                    for event in all_path_events
                ):
                    analysis_status = AnalysisStatus.TOO_LARGE
                    omission_reason = ReasonCode.FILE_TOO_LARGE
                elif any(
                    event.reason_code == "resource_budget_reached"
                    for event in inventory_path_events
                ):
                    analysis_status = AnalysisStatus.BUDGET_OMITTED
                    omission_reason = ReasonCode.RESOURCE_BUDGET_REACHED
                elif any(
                    event.outcome is CoverageOutcome.UNSUPPORTED
                    for event in inventory_path_events
                ):
                    analysis_status = AnalysisStatus.UNSUPPORTED
                    omission_reason = None
            nodes.append(
                Node(
                    public_id,
                    identity,
                    NodeKind.FILE,
                    language,
                    None,
                    PurePosixPath(path).name,
                    path,
                    None,
                    None,
                    None,
                    FileProperties(
                        digest_by_path[path],
                        byte_size,
                        analysis_status,
                        omission_reason,
                        inventory_file.is_test,
                        inventory_file.is_generated,
                    ),
                    EvidenceEnvelope(
                        EvidenceItem(
                            EvidenceOrigin.VERIFIED_FROM_CODE,
                            "hades.inventory.v2",
                            FileSourceLocator("file", path),
                            file_source_fingerprint(digest_by_path[path], path),
                            None,
                        ),
                        (),
                        0,
                    ),
                )
            )

        for source_node in source_nodes_ir:
            path = source_node.locator.source_location.path
            identity = SourceDeclarationIdentity(
                "source_declaration",
                context.workspace_binding_id,
                source_node.language,
                source_node.kind,
                source_node.namespace,
                source_node.qualified_name,
                path,
            )
            public_id = node_id({
                "variant": "source_declaration",
                "workspace_binding_id": context.workspace_binding_id,
                "language": source_node.language,
                "kind": source_node.kind.value,
                "namespace": source_node.namespace,
                "qualified_name": source_node.qualified_name,
                "path": path,
            })
            local_node_ids[source_node.local_key] = public_id
            properties = (
                TestProperties(case_count=1)
                if source_node.kind is NodeKind.TEST
                else TypeProperties()
            )
            nodes.append(
                Node(
                    public_id,
                    identity,
                    source_node.kind,
                    source_node.language,
                    None,
                    source_node.name,
                    source_node.qualified_name,
                    source_node.namespace,
                    None,
                    _source_location(source_node.locator),
                    properties,
                    _evidence(source_node.evidence),
                )
            )

        for data_node in data_nodes_ir:
            if data_node.kind is NodeKind.MODEL:
                path = data_node.locator.source_location.path
                qualified_name = data_node.qualified_name or data_node.name
                identity = SourceDeclarationIdentity(
                    "source_declaration",
                    context.workspace_binding_id,
                    data_node.language,
                    data_node.kind,
                    None,
                    qualified_name,
                    path,
                )
                public_id = node_id({
                    "variant": "source_declaration",
                    "workspace_binding_id": context.workspace_binding_id,
                    "language": data_node.language,
                    "kind": data_node.kind.value,
                    "namespace": None,
                    "qualified_name": qualified_name,
                    "path": path,
                })
            else:
                identity = SemanticResourceIdentity(
                    "semantic_resource",
                    context.workspace_binding_id,
                    data_node.language,
                    data_node.kind,
                    None,
                    None,
                    data_node.qualified_name,
                    data_node.public_resource_name,
                    None,
                    None,
                )
                public_id = node_id({
                    "variant": "semantic_resource",
                    "workspace_binding_id": context.workspace_binding_id,
                    "language": data_node.language,
                    "kind": data_node.kind.value,
                    "framework": None,
                    "namespace": None,
                    "qualified_name": data_node.qualified_name,
                    "public_resource_name": data_node.public_resource_name,
                    "protocol": None,
                    "operation": None,
                })
            local_node_ids[data_node.local_key] = public_id
            nodes.append(
                Node(
                    public_id,
                    identity,
                    data_node.kind,
                    data_node.language,
                    None,
                    data_node.name,
                    data_node.qualified_name,
                    None,
                    None,
                    _source_location(data_node.locator),
                    DataProperties(
                        public_resource_name=data_node.public_resource_name
                    ),
                    _evidence(data_node.evidence),
                )
            )

        pending = list(declarations)
        while pending:
            progressed = False
            for declaration in tuple(pending):
                if (
                    declaration.declaration_kind
                    not in EXECUTABLE_SOURCE_DECLARATION_KINDS
                ):
                    raise IRValidationError(
                        "invalid_declaration_kind",
                        "builder requires a schema-legal executable source declaration",
                    )
                if (
                    declaration.identity_kind is DeclarationIdentityKind.ANONYMOUS
                    and declaration.owner_declaration_key not in local_node_ids
                ):
                    continue
                path = declaration.locator.source_location.path
                if declaration.identity_kind is DeclarationIdentityKind.NAMED:
                    if declaration.qualified_name is None:
                        raise IRValidationError(
                            "invalid_declaration_identity",
                            "named declaration requires a qualified name",
                        )
                    identity = SourceDeclarationIdentity(
                        "source_declaration",
                        context.workspace_binding_id,
                        declaration.language,
                        declaration.declaration_kind,
                        declaration.namespace,
                        declaration.qualified_name,
                        path,
                    )
                    identity_payload = {
                        "variant": "source_declaration",
                        "workspace_binding_id": context.workspace_binding_id,
                        "language": declaration.language,
                        "kind": declaration.declaration_kind.value,
                        "namespace": declaration.namespace,
                        "qualified_name": declaration.qualified_name,
                        "path": path,
                    }
                    name = declaration.name
                    qualified_name = declaration.qualified_name
                    namespace = declaration.namespace
                else:
                    from hermes_cli.hades_graph_v2.model import (
                        AnonymousCallableIdentity,
                    )

                    owner_id = local_node_ids[declaration.owner_declaration_key or ""]
                    identity = AnonymousCallableIdentity(
                        "anonymous_callable",
                        context.workspace_binding_id,
                        declaration.language,
                        "function",
                        owner_id,
                        declaration.locator.structural_path,
                        declaration.locator.ordinal,
                    )
                    identity_payload = {
                        "variant": "anonymous_callable",
                        "workspace_binding_id": context.workspace_binding_id,
                        "language": declaration.language,
                        "kind": "function",
                        "owner_node_id": owner_id,
                        "structural_path": declaration.locator.structural_path,
                        "ordinal": declaration.locator.ordinal,
                    }
                    name = "<anonymous>"
                    qualified_name = None
                    namespace = declaration.namespace
                public_id = node_id(identity_payload)
                if declaration.identity_kind is DeclarationIdentityKind.ANONYMOUS:
                    owner = next(
                        node for node in nodes if node.id == identity.owner_node_id
                    )
                    qualified_name = (
                        f"{owner.qualified_name}::<anonymous:{public_id[-64:-52]}>"
                    )
                    namespace = owner.namespace
                local_node_ids[declaration.local_key] = public_id
                modifier_values = tuple(item.value for item in declaration.modifiers)
                if declaration.declaration_kind in {
                    NodeKind.EVENT,
                    NodeKind.LISTENER,
                    NodeKind.JOB,
                    NodeKind.QUEUE,
                }:
                    declaration_properties = AsyncProperties(
                        channel_kind=declaration.declaration_kind.value,
                        public_name=qualified_name or name,
                    )
                elif declaration.declaration_kind in {
                    NodeKind.MIDDLEWARE,
                    NodeKind.GUARD,
                    NodeKind.AUTHORIZATION,
                    NodeKind.VALIDATOR,
                    NodeKind.BINDING,
                }:
                    declaration_properties = FrameworkProperties(
                        framework_role=declaration.declaration_kind.value,
                        boundary_name=qualified_name or name,
                    )
                else:
                    declaration_properties = CallableProperties(
                        visibility=next(
                            (
                                item.value
                                for item in declaration.modifiers
                                if item
                                in {
                                    Modifier.PUBLIC,
                                    Modifier.PROTECTED,
                                    Modifier.PRIVATE,
                                }
                            ),
                            None,
                        ),
                        static=Modifier.STATIC in declaration.modifiers,
                        async_=Modifier.ASYNC in declaration.modifiers,
                        parameter_count=len(declaration.parameters),
                        return_type=declaration.return_type,
                        modifiers=modifier_values,
                    )
                nodes.append(
                    Node(
                        public_id,
                        identity,
                        declaration.declaration_kind,
                        declaration.language,
                        None,
                        name,
                        qualified_name,
                        namespace,
                        None,
                        _source_location(declaration.locator),
                        declaration_properties,
                        _synthetic_evidence(declaration.locator),
                    )
                )
                pending.remove(declaration)
                progressed = True
            if not progressed:
                raise IRValidationError(
                    "declaration_owner_cycle",
                    "anonymous declaration owners do not form a finite hierarchy",
                )

        for block in blocks:
            owner_id = local_node_ids[block.declaration_key]
            kind = _CONTROL_NODE_KIND[block.control_kind]
            identity = SourceOccurrenceIdentity(
                "source_occurrence",
                context.workspace_binding_id,
                declaration_by_key[block.declaration_key].language,
                kind,
                owner_id,
                block.locator.structural_path,
                block.ordinal,
                block.control_kind.value,
            )
            public_id = node_id({
                "variant": "source_occurrence",
                "workspace_binding_id": context.workspace_binding_id,
                "language": identity.language,
                "kind": kind.value,
                "owner_node_id": owner_id,
                "structural_path": block.locator.structural_path,
                "ordinal": block.ordinal,
                "semantic_role": block.control_kind.value,
            })
            local_node_ids[block.local_key] = public_id
            nodes.append(
                Node(
                    public_id,
                    identity,
                    kind,
                    identity.language,
                    None,
                    block.control_kind.value,
                    None,
                    None,
                    None,
                    _source_location(block.locator),
                    ControlProperties(block.control_kind.value, block.ordinal),
                    _synthetic_evidence(block.locator),
                )
            )

        for terminal in terminals:
            if terminal.source_block_key not in block_by_key:
                continue
            source_block = block_by_key[terminal.source_block_key]
            declaration = declaration_by_key[source_block.declaration_key]
            owner_id = local_node_ids[declaration.local_key]
            kind = _TERMINAL_NODE_KIND[terminal.kind]
            terminal_structural = (
                terminal.locator.structural_path
                if type(terminal.locator) is AstLocatorIR
                else terminal.locator.structural_pointer
            )
            identity = SourceOccurrenceIdentity(
                "source_occurrence",
                context.workspace_binding_id,
                declaration.language,
                kind,
                owner_id,
                terminal_structural,
                terminal.locator.ordinal,
                terminal.kind.value,
            )
            public_id = node_id({
                "variant": "source_occurrence",
                "workspace_binding_id": context.workspace_binding_id,
                "language": declaration.language,
                "kind": kind.value,
                "owner_node_id": owner_id,
                "structural_path": terminal_structural,
                "ordinal": terminal.locator.ordinal,
                "semantic_role": terminal.kind.value,
            })
            local_node_ids[terminal.local_key] = public_id
            nodes.append(
                Node(
                    public_id,
                    identity,
                    kind,
                    declaration.language,
                    None,
                    terminal.kind.value,
                    None,
                    None,
                    None,
                    _source_location(terminal.locator),
                    TerminalProperties(
                        terminal.public_status,
                        terminal.exception_type,
                        terminal.kind.value,
                    ),
                    _synthetic_evidence(terminal.locator),
                )
            )

        entrypoint_data: list[
            tuple[EntrypointCandidate, EntrypointIdentity, str, str]
        ] = []
        for candidate in entrypoint_candidates:
            language = candidate_language(candidate)
            if language is None:
                raise IRValidationError(
                    "entrypoint_language_unresolved",
                    "entrypoint language cannot be derived deterministically",
                )
            identity_value, node_identity, public_id = _entrypoint_identity(
                context, candidate, language
            )
            label = _entrypoint_label(candidate)
            nodes.append(
                Node(
                    public_id,
                    node_identity,
                    NodeKind.ENTRYPOINT,
                    language,
                    candidate.framework,
                    label,
                    candidate.public_name or identity_value.trigger.value,
                    None,
                    None,
                    _source_location(candidate.registration_locator),
                    BoundaryProperties(),
                    _evidence(candidate.evidence),
                )
            )
            entrypoint_data.append((candidate, identity_value, public_id, language))

        framework_segment_node_ids: dict[str, str] = {}
        framework_segment_owner_ids: dict[str, str] = {}
        framework_segment_language: dict[str, str] = {}
        entrypoint_data_by_segment = {
            segment_key: data
            for data in entrypoint_data
            for segment_key in data[0].framework_segment_keys
        }
        for segment in framework_segments:
            if segment.local_key not in entrypoint_data_by_segment:
                raise IRValidationError(
                    "orphan_framework_segment",
                    "framework segment is not owned by an entrypoint pipeline",
                )
            candidate, _identity, owner_id, language = entrypoint_data_by_segment[
                segment.local_key
            ]
            descriptor = (
                segment.target.descriptor
                if type(segment.target) is FrameworkBoundaryTarget
                else None
            )
            kind = _framework_node_kind(segment.framework_role)
            locator = (
                descriptor.locator
                if descriptor is not None
                else segment.evidence.locator
            )
            structural = (
                locator.structural_path
                if type(locator) is AstLocatorIR
                else locator.structural_pointer
            )
            structural_path = (
                f"{structural}/pipeline/{segment.pipeline_order}/"
                f"{segment.framework_role}"
            )
            identity = SourceOccurrenceIdentity(
                "source_occurrence",
                context.workspace_binding_id,
                language,
                kind,
                owner_id,
                structural_path,
                segment.pipeline_order,
                segment.framework_role,
            )
            public_id = node_id({
                "variant": "source_occurrence",
                "workspace_binding_id": context.workspace_binding_id,
                "language": language,
                "kind": kind.value,
                "owner_node_id": owner_id,
                "structural_path": structural_path,
                "ordinal": segment.pipeline_order,
                "semantic_role": segment.framework_role,
            })
            framework_segment_node_ids[segment.local_key] = public_id
            framework_segment_owner_ids[segment.local_key] = owner_id
            framework_segment_language[segment.local_key] = language
            nodes.append(
                Node(
                    public_id,
                    identity,
                    kind,
                    language,
                    descriptor.framework
                    if descriptor is not None
                    else candidate.framework,
                    (
                        descriptor.public_name
                        if descriptor is not None and descriptor.public_name is not None
                        else segment.framework_role
                    ),
                    None,
                    None,
                    None,
                    _source_location(locator),
                    FrameworkProperties(
                        segment.framework_role,
                        segment.pipeline_order,
                        None if descriptor is None else descriptor.public_name,
                    ),
                    _evidence(segment.evidence),
                )
            )

        for terminal in terminals:
            if terminal.source_block_key not in framework_segment_node_ids:
                continue
            owner_id = framework_segment_owner_ids[terminal.source_block_key]
            language = framework_segment_language[terminal.source_block_key]
            kind = _TERMINAL_NODE_KIND[terminal.kind]
            terminal_structural = (
                terminal.locator.structural_path
                if type(terminal.locator) is AstLocatorIR
                else terminal.locator.structural_pointer
            )
            identity = SourceOccurrenceIdentity(
                "source_occurrence",
                context.workspace_binding_id,
                language,
                kind,
                owner_id,
                terminal_structural,
                terminal.locator.ordinal,
                terminal.kind.value,
            )
            public_id = node_id({
                "variant": "source_occurrence",
                "workspace_binding_id": context.workspace_binding_id,
                "language": language,
                "kind": kind.value,
                "owner_node_id": owner_id,
                "structural_path": terminal_structural,
                "ordinal": terminal.locator.ordinal,
                "semantic_role": terminal.kind.value,
            })
            local_node_ids[terminal.local_key] = public_id
            nodes.append(
                Node(
                    public_id,
                    identity,
                    kind,
                    language,
                    None,
                    terminal.kind.value,
                    None,
                    None,
                    None,
                    _source_location(terminal.locator),
                    TerminalProperties(
                        terminal.public_status,
                        terminal.exception_type,
                        terminal.kind.value,
                    ),
                    _synthetic_evidence(terminal.locator),
                )
            )

        def framework_terminal_id(
            segment: FrameworkPipelineSegment,
            successor: ReturnSuccessor,
        ) -> str:
            del segment
            return local_node_ids[successor.terminal_local_key]

        structure_ids: dict[str, str] = {}
        for structure in structures_ir:
            owner_id = local_node_ids[structure.owner_declaration_key]
            identity = {
                "kind": structure.kind.value,
                "owner_node_id": owner_id,
                "structural_path": structure.structural_path,
                "ordinal": structure.ordinal,
                "subtype": structure.subtype.value,
            }
            helper = {
                StructureKind.CALL_SITE: call_site_id,
                StructureKind.BRANCH_GROUP: branch_group_id,
                StructureKind.EXCEPTION_SCOPE: exception_scope_id,
            }[structure.kind]
            structure_ids[structure.local_key] = helper(identity)

        call_site_structure_by_local: dict[str, str] = {}
        call_site_ir_by_structure: dict[str, CallSite] = {}
        for site in call_sites:
            match = next(
                structure
                for structure in structures_ir
                if structure.kind is StructureKind.CALL_SITE
                and structure.owner_declaration_key == site.caller_declaration_key
                and structure.structural_path == site.locator.structural_path
                and structure.ordinal == site.locator.ordinal
            )
            call_site_structure_by_local[site.local_key] = structure_ids[
                match.local_key
            ]
            call_site_ir_by_structure[match.local_key] = site

        structures: list[Structure] = []
        for structure in structures_ir:
            parent_key = structure.parent_structure_key
            if structure.kind is StructureKind.CALL_SITE:
                site = call_site_ir_by_structure.get(structure.local_key)
                if site is not None and site.exception_scope_key is not None:
                    parent_key = site.exception_scope_key
            structures.append(
                Structure(
                    structure_ids[structure.local_key],
                    structure.kind,
                    local_node_ids[structure.owner_declaration_key],
                    structure.structural_path,
                    structure.ordinal,
                    structure.subtype,
                    (
                        None
                        if structure.continuation_block_key is None
                        else local_node_ids[structure.continuation_block_key]
                    ),
                    None if parent_key is None else structure_ids[parent_key],
                    _evidence(structure.evidence),
                )
            )

        framework_exception_scope_ids: dict[str, str] = {}
        for segment in framework_segments:
            for successor in (
                segment.success_successor,
                *segment.short_circuit_successors,
            ):
                if type(successor) is not ExceptionSuccessor:
                    continue
                scope = exception_scope_ir_by_key.get(
                    successor.exception_scope_key
                )
                if scope is None or scope.structure_key not in structure_ids:
                    raise IRValidationError(
                        "unresolved_reference",
                        "framework exception successor lacks exact typed scope facts",
                    )
                framework_exception_scope_ids[successor.exception_scope_key] = (
                    structure_ids[scope.structure_key]
                )

        # Loop successors do not carry a structure key in the frozen IR, so
        # the canonical producer derives their branch group from the loop
        # header occurrence.  This keeps every lifecycle branch resolvable to
        # a real structure without asking language adapters to duplicate CFG
        # facts.
        loop_branch_ids: dict[str, str] = {}
        for block in blocks:
            loop_successors = tuple(
                successor
                for successor in block.successors
                if type(successor) is LoopSuccessor
            )
            if not loop_successors:
                continue
            owner_id = local_node_ids[block.declaration_key]
            identity = {
                "kind": StructureKind.BRANCH_GROUP.value,
                "owner_node_id": owner_id,
                "structural_path": block.locator.structural_path,
                "ordinal": block.ordinal,
                "subtype": StructureSubtype.LOOP.value,
            }
            public_id = branch_group_id(identity)
            loop_branch_ids[block.local_key] = public_id
            exit_successor = next(
                (
                    successor
                    for successor in loop_successors
                    if successor.loop_role is LoopRole.EXIT
                ),
                None,
            )
            structures.append(
                Structure(
                    public_id,
                    StructureKind.BRANCH_GROUP,
                    owner_id,
                    block.locator.structural_path,
                    block.ordinal,
                    StructureSubtype.LOOP,
                    (
                        None
                        if exit_successor is None
                        else local_node_ids[exit_successor.target_block_key]
                    ),
                    None,
                    _synthetic_evidence(block.locator),
                )
            )

        unresolved_by_key = {item.local_key: item for item in unresolved_facts}
        candidate_fact_by_edge_key = {
            edge_key: fact
            for fact in unresolved_facts
            if fact.candidate_set_knowledge is CandidateSetKnowledge.INCOMPLETE
            for edge_key in fact.candidate_edge_local_keys
        }
        entrypoint_by_unresolved = {
            candidate.unresolved_fact_local_key: (candidate, public_id)
            for candidate, _, public_id, _ in entrypoint_data
            if candidate.unresolved_fact_local_key is not None
        }
        edge_owner_local: dict[str, str] = {}
        for key, public_id in local_node_ids.items():
            if key in declaration_by_key:
                edge_owner_local[key] = public_id
            elif key in block_by_key:
                edge_owner_local[key] = local_node_ids[
                    block_by_key[key].declaration_key
                ]
            elif key in {item.local_key for item in data_nodes_ir}:
                edge_owner_local[key] = public_id
        for terminal in terminals:
            if terminal.source_block_key in block_by_key:
                block = block_by_key[terminal.source_block_key]
                edge_owner_local[terminal.local_key] = local_node_ids[
                    block.declaration_key
                ]
            else:
                edge_owner_local[terminal.local_key] = framework_segment_owner_ids[
                    terminal.source_block_key
                ]
        edge_owner_local.update(framework_segment_owner_ids)

        effect_target_ids: dict[str, str] = {}
        resource_nodes: dict[str, Node] = {}
        for effect in effects:
            if type(effect.source) is BlockEffectSource:
                source_block = block_by_key[effect.source.local_key]
            else:
                site = call_site_by_key[effect.source.local_key]
                source_block = block_by_key[site.source_block_key]
            declaration = declaration_by_key[source_block.declaration_key]
            kind, _relation, _flow = effect_kind_mapping(effect.kind)
            target_source = (
                source_node_by_key.get(effect.target_source_node_local_key)
                if effect.target_source_node_local_key is not None
                else None
            )
            target_language = (
                target_source.language if target_source is not None else declaration.language
            )
            if kind in {NodeKind.EVENT, NodeKind.JOB, NodeKind.QUEUE}:
                # The frozen schema reserves semantic-resource identities for
                # data/integration resources. Async effect targets therefore
                # use the source declaration form anchored to the verified
                # call occurrence, never an out-of-contract semantic kind.
                qualified_name = (
                    target_source.qualified_name
                    if target_source is not None
                    else f"{kind.value}:{effect.public_resource_name or effect.operation}"
                )
                target_path = (
                    target_source.locator.source_location.path
                    if target_source is not None
                    else effect.locator.source_location.path
                )
                target_namespace = (
                    target_source.namespace if target_source is not None else None
                )
                identity = SourceDeclarationIdentity(
                    "source_declaration",
                    context.workspace_binding_id,
                    target_language,
                    kind,
                    target_namespace,
                    qualified_name,
                    target_path,
                )
                identity_payload = {
                    "variant": "source_declaration",
                    "workspace_binding_id": context.workspace_binding_id,
                    "language": target_language,
                    "kind": kind.value,
                    "namespace": target_namespace,
                    "qualified_name": qualified_name,
                    "path": target_path,
                }
            else:
                identity = SemanticResourceIdentity(
                    "semantic_resource",
                    context.workspace_binding_id,
                    target_language if target_source is not None else declaration.language,
                    kind,
                    None,
                    None,
                    None,
                    effect.public_resource_name,
                    effect.protocol,
                    effect.operation,
                )
                identity_payload = {
                    "variant": "semantic_resource",
                    "workspace_binding_id": context.workspace_binding_id,
                    "language": declaration.language,
                    "kind": kind.value,
                    "framework": None,
                    "namespace": None,
                    "qualified_name": None,
                    "public_resource_name": effect.public_resource_name,
                    "protocol": effect.protocol,
                    "operation": effect.operation,
                }
            public_id = node_id(identity_payload)
            effect_target_ids[effect.local_key] = public_id
            if kind in {
                NodeKind.MODEL,
                NodeKind.TABLE,
                NodeKind.QUERY,
                NodeKind.CACHE,
                NodeKind.STORAGE,
            }:
                properties = DataProperties(
                    operation=effect.operation,
                    public_resource_name=effect.public_resource_name,
                )
            elif kind in {NodeKind.INTEGRATION, NodeKind.EXTERNAL_BOUNDARY}:
                properties = IntegrationProperties(
                    protocol=effect.protocol,
                    operation=effect.operation,
                    destination_kind=effect.kind.value,
                )
            else:
                properties = AsyncProperties(
                    channel_kind=effect.kind.value,
                    public_name=effect.public_resource_name,
                )
            is_async_effect_target = kind in {
                NodeKind.EVENT,
                NodeKind.JOB,
                NodeKind.QUEUE,
            }
            node_name = (
                identity.qualified_name
                if is_async_effect_target
                else effect.public_resource_name or effect.operation
            )
            _insert_public_record(
                resource_nodes,
                Node(
                    public_id,
                    identity,
                    kind,
                    target_language if is_async_effect_target else declaration.language,
                    None,
                    node_name,
                    identity.qualified_name if is_async_effect_target else None,
                    target_namespace if is_async_effect_target else None,
                    None,
                    _source_location(target_source.locator)
                    if is_async_effect_target and target_source is not None
                    else _source_location(effect.locator)
                    if is_async_effect_target
                    else None,
                    properties,
                    _evidence(target_source.evidence)
                    if is_async_effect_target and target_source is not None
                    else _synthetic_evidence(effect.locator),
                ),
                record_name="node",
            )
        nodes.extend(resource_nodes.values())

        boundary_target_ids: dict[str, str] = {}
        boundary_nodes: dict[str, Node] = {}
        unresolved_boundary_edge_keys = {
            fact.subject.local_key
            for fact in unresolved_facts
            if isinstance(fact.subject, EdgeSubjectIR)
            and fact.candidate_set_knowledge is not CandidateSetKnowledge.COMPLETE
        }
        framework_kind_by_role = {
            "middleware": NodeKind.MIDDLEWARE,
            "guard": NodeKind.GUARD,
            "authorization": NodeKind.AUTHORIZATION,
            "validator": NodeKind.VALIDATOR,
            "binding": NodeKind.BINDING,
        }
        for edge_ir in edge_facts:
            if type(edge_ir.target) is not BoundaryTarget:
                continue
            if edge_ir.local_key in unresolved_boundary_edge_keys:
                # The uncertainty materializer below owns the one canonical
                # UNKNOWN_BOUNDARY node and unresolved evidence envelope.
                continue
            descriptor = edge_ir.target.descriptor
            owner_id = edge_owner_local[edge_ir.source_node_local_key]
            owner = next(node for node in nodes if node.id == owner_id)
            kind = framework_kind_by_role.get(
                descriptor.role, NodeKind.FRAMEWORK_BOUNDARY
            )
            structural_path = (
                descriptor.locator.structural_path
                if type(descriptor.locator) is AstLocatorIR
                else descriptor.locator.structural_pointer
            )
            identity = SourceOccurrenceIdentity(
                "source_occurrence",
                context.workspace_binding_id,
                owner.language or context.detected_languages[0],
                kind,
                owner_id,
                structural_path,
                descriptor.locator.ordinal,
                descriptor.role,
            )
            identity_payload = {
                "variant": "source_occurrence",
                "workspace_binding_id": context.workspace_binding_id,
                "language": identity.language,
                "kind": kind.value,
                "owner_node_id": owner_id,
                "structural_path": structural_path,
                "ordinal": descriptor.locator.ordinal,
                "semantic_role": descriptor.role,
            }
            public_id = node_id(identity_payload)
            boundary_target_ids[edge_ir.local_key] = public_id
            _insert_public_record(
                boundary_nodes,
                Node(
                    public_id,
                    identity,
                    kind,
                    identity.language,
                    descriptor.framework,
                    descriptor.public_name or descriptor.role,
                    None,
                    None,
                    None,
                    _source_location(descriptor.locator),
                    FrameworkProperties(
                        framework_role=descriptor.role,
                        boundary_name=descriptor.public_name,
                    ),
                    _evidence(descriptor.evidence),
                ),
                record_name="node",
            )
        nodes.extend(boundary_nodes.values())

        complete_fact_by_edge_key = {
            edge_key: fact
            for fact in unresolved_facts
            if fact.candidate_set_knowledge is CandidateSetKnowledge.COMPLETE
            for edge_key in fact.candidate_edge_local_keys
        }
        complete_dynamic_group_by_fact: dict[str, str] = {}
        for fact in unresolved_facts:
            if (
                fact.candidate_set_knowledge is not CandidateSetKnowledge.COMPLETE
                or len(fact.candidate_edge_local_keys) <= 1
            ):
                continue
            first = edge_fact_by_key[fact.candidate_edge_local_keys[0]]
            entrypoint_owner = entrypoint_by_unresolved.get(fact.local_key)
            owner_id = (
                entrypoint_owner[1]
                if entrypoint_owner is not None
                else edge_owner_local[first.source_node_local_key]
            )
            structural = (
                first.locator.structural_path
                if type(first.locator) is AstLocatorIR
                else first.locator.structural_pointer
            )
            structural_path = f"{structural}/candidate_targets"
            identity = {
                "kind": StructureKind.BRANCH_GROUP.value,
                "owner_node_id": owner_id,
                "structural_path": structural_path,
                "ordinal": 0,
                "subtype": StructureSubtype.DYNAMIC_DISPATCH.value,
            }
            public_id = branch_group_id(identity)
            complete_dynamic_group_by_fact[fact.local_key] = public_id
            outer_parent = (
                structure_ids[first.branch_group_key]
                if first.branch_group_key is not None
                else (
                    structure_ids[first.exception_scope_key]
                    if first.exception_scope_key is not None
                    else None
                )
            )
            if outer_parent is None and first.call_site_key is not None:
                call_structure_id = structure_ids[first.call_site_key]
                outer_parent = next(
                    item.parent_structure_id
                    for item in structures
                    if item.id == call_structure_id
                )
            continuation = (
                None
                if first.call_site_key is None
                else next(
                    item.continuation_node_id
                    for item in structures
                    if item.id == structure_ids[first.call_site_key]
                )
            )
            structures.append(
                Structure(
                    public_id,
                    StructureKind.BRANCH_GROUP,
                    owner_id,
                    structural_path,
                    0,
                    StructureSubtype.DYNAMIC_DISPATCH,
                    continuation,
                    outer_parent,
                    _synthetic_evidence(first.locator),
                )
            )

        edges: list[Edge] = []
        edge_ids_by_local: dict[str, str] = {}
        uncertainties: list[Uncertainty] = []
        uncertainty_id_by_fact: dict[str, str] = {}
        unknown_nodes: list[Node] = []

        for fact in unresolved_facts:
            if fact.candidate_set_knowledge.value == "complete":
                continue
            if isinstance(fact.subject, EdgeSubjectIR):
                subject_ir = edge_fact_by_key[fact.subject.local_key]
            else:
                call_site_id_value = call_site_structure_by_local[
                    fact.subject.local_key
                ]
                subject_ir = next(
                    edge
                    for edge in edge_facts
                    if edge.call_site_key is not None
                    and structure_ids[edge.call_site_key] == call_site_id_value
                    and edge.evidence.origin is not EvidenceOrigin.INFERRED
                )
            entrypoint_owner = entrypoint_by_unresolved.get(fact.local_key)
            if entrypoint_owner is not None:
                _, source_id = entrypoint_owner
                owner_id = source_id
            else:
                source_id = (
                    framework_segment_node_ids[subject_ir.source_node_local_key]
                    if subject_ir.source_node_local_key in framework_segment_node_ids
                    else local_node_ids[subject_ir.source_node_local_key]
                )
                owner_id = edge_owner_local[subject_ir.source_node_local_key]
            locator = subject_ir.locator
            structural = (
                locator.structural_path
                if type(locator) is AstLocatorIR
                else locator.structural_pointer
            )
            owner_node = next(node for node in nodes if node.id == owner_id)
            language = owner_node.language or next(
                node.language for node in nodes if node.id == source_id
            )
            boundary_qualified_name: str | None = None
            boundary_public_resource_name: str | None = None
            boundary_kind = (
                NodeKind.EXTERNAL_BOUNDARY
                if fact.resolution_kind is ResolutionKind.EXTERNAL_TARGET
                else NodeKind.UNKNOWN_BOUNDARY
            )
            boundary_properties: BoundaryProperties | IntegrationProperties = (
                IntegrationProperties()
                if boundary_kind is NodeKind.EXTERNAL_BOUNDARY
                else BoundaryProperties(reason_code=fact.reason_code)
            )
            if owner_node.kind in _CALLABLE_OWNER_KINDS:
                semantic_role = boundary_kind.value
                boundary_identity = SourceOccurrenceIdentity(
                    "source_occurrence",
                    context.workspace_binding_id,
                    language,
                    boundary_kind,
                    owner_id,
                    f"{structural}/{semantic_role}",
                    locator.ordinal,
                    semantic_role,
                )
                boundary_identity_payload = {
                    "variant": "source_occurrence",
                    "workspace_binding_id": context.workspace_binding_id,
                    "language": language,
                    "kind": boundary_kind.value,
                    "owner_node_id": owner_id,
                    "structural_path": boundary_identity.structural_path,
                    "ordinal": locator.ordinal,
                    "semantic_role": semantic_role,
                }
            else:
                if boundary_kind is not NodeKind.EXTERNAL_BOUNDARY:
                    raise IRValidationError(
                        "invalid_unresolved_owner",
                        "non-callable unresolved targets must be external boundaries",
                    )
                boundary_qualified_name = (
                    f"{locator.source_location.path}::{structural}::external_boundary"
                )
                boundary_public_resource_name = (
                    subject_ir.target.descriptor.public_name
                    if type(subject_ir.target) is BoundaryTarget
                    else None
                )
                boundary_identity = SemanticResourceIdentity(
                    "semantic_resource",
                    context.workspace_binding_id,
                    language,
                    boundary_kind,
                    None,
                    None,
                    boundary_qualified_name,
                    boundary_public_resource_name,
                    None,
                    None,
                )
                boundary_identity_payload = {
                    "variant": "semantic_resource",
                    "workspace_binding_id": context.workspace_binding_id,
                    "language": language,
                    "kind": boundary_kind.value,
                    "framework": None,
                    "namespace": None,
                    "qualified_name": boundary_qualified_name,
                    "public_resource_name": boundary_public_resource_name,
                    "protocol": None,
                    "operation": None,
                }
            boundary_id = node_id(boundary_identity_payload)
            condition = (
                None
                if subject_ir.condition is None
                else Condition(
                    subject_ir.condition.kind,
                    subject_ir.condition.normalized,
                    subject_ir.condition.hash,
                    subject_ir.condition.polarity,
                )
            )
            branch_id = (
                None
                if subject_ir.branch_group_key is None
                else structure_ids[subject_ir.branch_group_key]
            )
            call_id = (
                None
                if subject_ir.call_site_key is None
                else structure_ids[subject_ir.call_site_key]
            )
            exception_id = (
                None
                if subject_ir.exception_scope_key is None
                else structure_ids[subject_ir.exception_scope_key]
            )
            occurrence = (
                EdgeAstOccurrence(
                    "ast", owner_id, locator.structural_path, locator.ordinal
                )
                if type(locator) is AstLocatorIR
                else EdgeConfigOccurrence(
                    "config",
                    owner_id,
                    locator.source_location.path,
                    locator.structural_pointer,
                    locator.ordinal,
                )
            )
            occurrence_payload = (
                {
                    "kind": "ast",
                    "owner_node_id": owner_id,
                    "ast_path": locator.structural_path,
                    "ordinal": locator.ordinal,
                }
                if type(locator) is AstLocatorIR
                else {
                    "kind": "config",
                    "owner_node_id": owner_id,
                    "path": locator.source_location.path,
                    "structural_pointer": locator.structural_pointer,
                    "ordinal": locator.ordinal,
                }
            )
            public_edge_id = edge_id({
                "source_id": source_id,
                "target_id": boundary_id,
                "relation": subject_ir.relation.value,
                "flow": None if subject_ir.flow is None else subject_ir.flow.value,
                "condition_hash": None if condition is None else condition.hash,
                "branch_group_id": branch_id,
                "call_site_id": call_id,
                "exception_scope_id": exception_id,
                "occurrence": occurrence_payload,
            })
            subject = (
                EdgeSubject(public_edge_id)
                if isinstance(fact.subject, EdgeSubjectIR)
                else __import__(
                    "hermes_cli.hades_graph_v2.model", fromlist=["CallSiteSubject"]
                ).CallSiteSubject(call_site_structure_by_local[fact.subject.local_key])
            )
            identity = {
                "domain": "graph",
                "project_id": context.project_id,
                "workspace_binding_id": context.workspace_binding_id,
                "subject": (
                    {"edge_id": subject.edge_id}
                    if isinstance(subject, EdgeSubject)
                    else {"call_site_id": subject.call_site_id}
                ),
                "resolution_kind": fact.resolution_kind.value,
                "reason_code": _reason_code(fact.reason_code).value,
                "question": fact.question,
            }
            fingerprint = uncertainty_fingerprint(identity)
            uncertainty_id_value = f"hades:uncertainty:v2:{fingerprint}"
            uncertainty_id_by_fact[fact.local_key] = uncertainty_id_value
            edge_ids_by_local[subject_ir.local_key] = public_edge_id
            edges.append(
                Edge(
                    public_edge_id,
                    source_id,
                    boundary_id,
                    subject_ir.relation,
                    subject_ir.flow,
                    condition,
                    branch_id,
                    call_id,
                    exception_id,
                    subject_ir.order,
                    uncertainty_id_value,
                    occurrence,
                    _evidence(
                        subject_ir.evidence,
                        origin=EvidenceOrigin.UNRESOLVED,
                        inference_rule=None,
                    ),
                    EdgeLocation(
                        locator.source_location.path,
                        locator.source_location.start_line,
                        locator.ordinal,
                    ),
                )
            )
            unknown_nodes.append(
                Node(
                    boundary_id,
                    boundary_identity,
                    boundary_kind,
                    language,
                    None,
                    (
                        "unknown boundary"
                        if boundary_kind is NodeKind.UNKNOWN_BOUNDARY
                        else boundary_public_resource_name or "external boundary"
                    ),
                    boundary_qualified_name,
                    None,
                    uncertainty_id_value,
                    _source_location(locator),
                    boundary_properties,
                    _evidence(
                        subject_ir.evidence,
                        origin=EvidenceOrigin.UNRESOLVED,
                        inference_rule=None,
                    ),
                )
            )
            uncertainties.append(
                Uncertainty(
                    uncertainty_id_value,
                    "graph",
                    subject,
                    fact.resolution_kind,
                    _reason_code(fact.reason_code),
                    fact.question,
                    fact.evidence_requirements,
                    tuple(
                        sorted(
                            {
                                SourceRef(
                                    locator.source_location.path,
                                    locator.source_location.start_line,
                                )
                                for locator in fact.source_locators
                            },
                            key=lambda item: (item.path, item.line),
                        )
                    ),
                    (),
                    (),
                    fact.candidate_set_knowledge,
                    fact.priority,
                    fact.impact,
                    fingerprint,
                )
            )

        def append_edge(
            *,
            source_id: str,
            target_id: str,
            relation: Relation,
            flow: EdgeFlow | None,
            locator: AstLocatorIR | ConfigLocatorIR,
            owner_id: str,
            evidence: EvidenceEnvelope,
            condition: Condition | None = None,
            branch_id: str | None = None,
            call_id: str | None = None,
            exception_id: str | None = None,
            order: int | None = None,
            occurrence_path: str | None = None,
            occurrence_ordinal: int | None = None,
            local_key: str | None = None,
            uncertainty_id_value: str | None = None,
        ) -> Edge:
            ordinal = (
                locator.ordinal if occurrence_ordinal is None else occurrence_ordinal
            )
            if type(locator) is AstLocatorIR:
                ast_path = occurrence_path or locator.structural_path
                occurrence = EdgeAstOccurrence("ast", owner_id, ast_path, ordinal)
                occurrence_payload = {
                    "kind": "ast",
                    "owner_node_id": owner_id,
                    "ast_path": ast_path,
                    "ordinal": ordinal,
                }
            else:
                pointer = occurrence_path or locator.structural_pointer
                occurrence = EdgeConfigOccurrence(
                    "config",
                    owner_id,
                    locator.source_location.path,
                    pointer,
                    ordinal,
                )
                occurrence_payload = {
                    "kind": "config",
                    "owner_node_id": owner_id,
                    "path": locator.source_location.path,
                    "structural_pointer": pointer,
                    "ordinal": ordinal,
                }
            public_edge_id = edge_id({
                "source_id": source_id,
                "target_id": target_id,
                "relation": relation.value,
                "flow": None if flow is None else flow.value,
                "condition_hash": None if condition is None else condition.hash,
                "branch_group_id": branch_id,
                "call_site_id": call_id,
                "exception_scope_id": exception_id,
                "occurrence": occurrence_payload,
            })
            value = Edge(
                public_edge_id,
                source_id,
                target_id,
                relation,
                flow,
                condition,
                branch_id,
                call_id,
                exception_id,
                order,
                uncertainty_id_value,
                occurrence,
                evidence,
                EdgeLocation(
                    locator.source_location.path,
                    locator.source_location.start_line,
                    ordinal,
                ),
            )
            edges.append(value)
            if local_key is not None:
                edge_ids_by_local[local_key] = public_edge_id
            return value

        unresolved_edge_keys = set(edge_ids_by_local)
        complete_template_only_keys = {
            fact.subject.local_key
            for fact in unresolved_facts
            if fact.candidate_set_knowledge is CandidateSetKnowledge.COMPLETE
            and isinstance(fact.subject, EdgeSubjectIR)
            and fact.subject.local_key not in fact.candidate_edge_local_keys
        }
        invocation_ir: list[tuple[Edge, EdgeFactIR]] = []
        for edge_ir in edge_facts:
            if edge_ir.local_key in unresolved_edge_keys | complete_template_only_keys:
                continue
            complete_fact = complete_fact_by_edge_key.get(edge_ir.local_key)
            complete_entrypoint = (
                None
                if complete_fact is None
                else entrypoint_by_unresolved.get(complete_fact.local_key)
            )
            owner_id = (
                complete_entrypoint[1]
                if complete_entrypoint is not None
                else (
                    local_node_ids[
                        structure_ir_by_key[edge_ir.call_site_key].owner_declaration_key
                    ]
                    if edge_ir.call_site_key is not None
                    else (
                        edge_owner_local[edge_ir.source_node_local_key]
                        if edge_ir.source_node_local_key in edge_owner_local
                        else local_node_ids[edge_ir.target.local_key]
                    )
                )
            )
            candidate_fact = candidate_fact_by_edge_key.get(edge_ir.local_key)
            effective_flow = edge_ir.flow
            effective_branch_id = (
                None
                if edge_ir.branch_group_key is None
                else structure_ids[edge_ir.branch_group_key]
            )
            if (
                complete_fact is not None
                and len(complete_fact.candidate_edge_local_keys) > 1
            ):
                effective_branch_id = complete_dynamic_group_by_fact[
                    complete_fact.local_key
                ]
                if edge_ir.flow not in {EdgeFlow.ASYNC, EdgeFlow.EXCEPTION}:
                    effective_flow = EdgeFlow.ALTERNATIVE
            value = append_edge(
                source_id=(
                    complete_entrypoint[1]
                    if complete_entrypoint is not None
                    else local_node_ids[edge_ir.source_node_local_key]
                ),
                target_id=(
                    local_node_ids[edge_ir.target.local_key]
                    if type(edge_ir.target) is LocalNodeTarget
                    else boundary_target_ids[edge_ir.local_key]
                ),
                relation=edge_ir.relation,
                flow=effective_flow,
                locator=edge_ir.locator,
                owner_id=owner_id,
                evidence=_evidence(edge_ir.evidence),
                condition=(
                    None
                    if edge_ir.condition is None
                    else Condition(
                        edge_ir.condition.kind,
                        edge_ir.condition.normalized,
                        edge_ir.condition.hash,
                        edge_ir.condition.polarity,
                    )
                ),
                branch_id=effective_branch_id,
                call_id=(
                    None
                    if edge_ir.call_site_key is None
                    else structure_ids[edge_ir.call_site_key]
                ),
                exception_id=(
                    None
                    if edge_ir.exception_scope_key is None
                    else structure_ids[edge_ir.exception_scope_key]
                ),
                order=edge_ir.order,
                local_key=edge_ir.local_key,
                uncertainty_id_value=(
                    None
                    if candidate_fact is None
                    else uncertainty_id_by_fact[candidate_fact.local_key]
                ),
            )
            if edge_ir.relation is Relation.INVOKES:
                invocation_ir.append((value, edge_ir))

        for fact in unresolved_facts:
            if fact.candidate_set_knowledge is not CandidateSetKnowledge.COMPLETE:
                continue
            candidate_pairs = tuple(
                sorted(
                    (
                        edge_ids_by_local[key],
                        edge_fact_by_key[key],
                    )
                    for key in fact.candidate_edge_local_keys
                )
            )
            candidate_ids = tuple(item[0] for item in candidate_pairs)
            target_ids = tuple(
                sorted({
                    local_node_ids[edge_ir.target.local_key]
                    for _, edge_ir in candidate_pairs
                    if type(edge_ir.target) is LocalNodeTarget
                })
            )
            if isinstance(fact.subject, EdgeSubjectIR):
                subject = EdgeSubject(candidate_ids[0])
            else:
                subject = __import__(
                    "hermes_cli.hades_graph_v2.model", fromlist=["CallSiteSubject"]
                ).CallSiteSubject(call_site_structure_by_local[fact.subject.local_key])
            identity = {
                "domain": "graph",
                "project_id": context.project_id,
                "workspace_binding_id": context.workspace_binding_id,
                "subject": (
                    {"edge_id": subject.edge_id}
                    if isinstance(subject, EdgeSubject)
                    else {"call_site_id": subject.call_site_id}
                ),
                "resolution_kind": fact.resolution_kind.value,
                "reason_code": _reason_code(fact.reason_code).value,
                "question": fact.question,
            }
            fingerprint = uncertainty_fingerprint(identity)
            uncertainty_id_value = f"hades:uncertainty:v2:{fingerprint}"
            uncertainty_id_by_fact[fact.local_key] = uncertainty_id_value
            ir_by_public_id = dict(candidate_pairs)
            for index, edge in enumerate(edges):
                if edge.id not in ir_by_public_id:
                    continue
                edge_ir = ir_by_public_id[edge.id]
                if (
                    isinstance(fact.subject, EdgeSubjectIR)
                    and edge.id == candidate_ids[0]
                ):
                    evidence = _evidence(
                        edge_ir.evidence,
                        origin=EvidenceOrigin.UNRESOLVED,
                        inference_rule=None,
                    )
                else:
                    evidence = _evidence(
                        edge_ir.evidence,
                        origin=EvidenceOrigin.INFERRED,
                        inference_rule=(
                            edge_ir.evidence.inference_rule or "complete_candidate"
                        ),
                    )
                edges[index] = replace(
                    edge,
                    uncertainty_id=uncertainty_id_value,
                    evidence=evidence,
                )
            uncertainties.append(
                Uncertainty(
                    uncertainty_id_value,
                    "graph",
                    subject,
                    fact.resolution_kind,
                    _reason_code(fact.reason_code),
                    fact.question,
                    fact.evidence_requirements,
                    tuple(
                        sorted(
                            {
                                SourceRef(
                                    locator.source_location.path,
                                    locator.source_location.start_line,
                                )
                                for locator in fact.source_locators
                            },
                            key=lambda item: (item.path, item.line),
                        )
                    ),
                    target_ids,
                    candidate_ids,
                    fact.candidate_set_knowledge,
                    fact.priority,
                    fact.impact,
                    fingerprint,
                )
            )

        for fact in unresolved_facts:
            if fact.candidate_set_knowledge is not CandidateSetKnowledge.INCOMPLETE:
                continue
            index = next(
                index
                for index, uncertainty in enumerate(uncertainties)
                if uncertainty.id == uncertainty_id_by_fact[fact.local_key]
            )
            uncertainties[index] = replace(
                uncertainties[index],
                candidate_target_node_ids=tuple(
                    sorted(
                        local_node_ids[key] for key in fact.candidate_target_local_keys
                    )
                ),
                candidate_edge_ids=tuple(
                    sorted(
                        edge_ids_by_local[key] for key in fact.candidate_edge_local_keys
                    )
                ),
            )

        for effect in effects:
            if type(effect.source) is BlockEffectSource:
                source_block = block_by_key[effect.source.local_key]
            else:
                site = call_site_by_key[effect.source.local_key]
                source_block = block_by_key[site.source_block_key]
            kind, relation, flow = effect_kind_mapping(effect.kind)
            del kind
            append_edge(
                source_id=local_node_ids[source_block.local_key],
                target_id=effect_target_ids[effect.local_key],
                relation=relation,
                flow=flow,
                locator=effect.locator,
                owner_id=local_node_ids[source_block.declaration_key],
                evidence=_synthetic_evidence(effect.locator),
                occurrence_path=(
                    getattr(effect.locator, "structural_path", None)
                    or getattr(effect.locator, "structural_pointer")
                ),
                local_key=effect.local_key,
            )

        framework_branch_ids: dict[tuple[str, str], str] = {}
        framework_missing_exit_boundary_ids: dict[tuple[str, str], str] = {}
        framework_missing_exit_boundary_edges: set[tuple[str, str]] = set()

        def framework_target_id(key: str) -> str:
            if key in local_node_ids:
                return local_node_ids[key]
            return framework_segment_node_ids[key]

        def materialize_missing_framework_exit_boundary(
            segment: FrameworkPipelineSegment,
            declaration: ExecutableDeclaration,
            exit_kind: str,
        ) -> None:
            """Stop a verified target at an explicit partial frontier.

            A framework successor is executable only from the target callable's
            exact typed exits.  If the adapter/control-flow union cannot provide
            the required exit family, retain the executed entry block and stop
            there instead of fabricating a declaration- or entry-sourced
            continuation.
            """

            key = (segment.local_key, exit_kind)
            role = f"unresolved_{exit_kind}_exit"
            declaration_id = local_node_ids[declaration.local_key]
            locator = segment.evidence.locator
            base_path = (
                locator.structural_path
                if type(locator) is AstLocatorIR
                else locator.structural_pointer
            )
            structural_path = (
                f"{base_path}/pipeline/{segment.pipeline_order}/"
                f"{segment.framework_role}/{role}"
            )
            identity = SourceOccurrenceIdentity(
                "source_occurrence",
                context.workspace_binding_id,
                declaration.language,
                NodeKind.FRAMEWORK_BOUNDARY,
                declaration_id,
                structural_path,
                segment.pipeline_order,
                role,
            )
            public_id = node_id({
                "variant": "source_occurrence",
                "workspace_binding_id": context.workspace_binding_id,
                "language": declaration.language,
                "kind": NodeKind.FRAMEWORK_BOUNDARY.value,
                "owner_node_id": declaration_id,
                "structural_path": structural_path,
                "ordinal": segment.pipeline_order,
                "semantic_role": role,
            })
            previous_id = framework_missing_exit_boundary_ids.setdefault(key, public_id)
            if previous_id != public_id:
                raise IRValidationError(
                    "semantic_collision",
                    "framework missing-exit boundary identity is inconsistent",
                )
            nodes.append(
                Node(
                    public_id,
                    identity,
                    NodeKind.FRAMEWORK_BOUNDARY,
                    declaration.language,
                    entrypoint_data_by_segment[segment.local_key][0].framework,
                    role.replace("_", " "),
                    None,
                    None,
                    None,
                    _source_location(locator),
                    FrameworkProperties(
                        segment.framework_role,
                        segment.pipeline_order,
                        role,
                    ),
                    _evidence(segment.evidence),
                )
            )
            if key not in framework_missing_exit_boundary_edges:
                append_edge(
                    source_id=local_node_ids[declaration.entry_block_key],
                    target_id=public_id,
                    relation=Relation.PASSES_THROUGH,
                    flow=EdgeFlow.ALWAYS,
                    locator=locator,
                    owner_id=declaration_id,
                    evidence=_evidence(segment.evidence),
                    order=0,
                    occurrence_path=structural_path,
                )
                framework_missing_exit_boundary_edges.add(key)
                effective_coverage_events.append(
                    CoverageEvent(
                        declaration.language,
                        CoverageCapability.FRAMEWORK_LIFECYCLE,
                        CoverageOutcome.PARTIAL,
                        ReasonCode.VERIFIED_TARGET_NOT_MATERIALIZED.value,
                        _locator_path(locator),
                        0,
                        1,
                    )
                )

        for segment in framework_segments:
            source_id = framework_segment_node_ids[segment.local_key]
            owner_id = framework_segment_owner_ids[segment.local_key]
            normal_successor_source_ids = (source_id,)
            exception_successor_source_ids = (source_id,)
            if type(segment.target) is FrameworkLocalTarget:
                target_declaration = declaration_by_key[segment.target.local_key]
                target_declaration_id = local_node_ids[segment.target.local_key]
                append_edge(
                    source_id=source_id,
                    target_id=target_declaration_id,
                    relation=Relation.ROUTES_TO,
                    flow=EdgeFlow.ALWAYS,
                    locator=segment.evidence.locator,
                    owner_id=owner_id,
                    evidence=_evidence(segment.evidence),
                    order=0,
                    occurrence_path=(f"pipeline/{segment.pipeline_order}/target"),
                )
                normal_successor_source_ids = tuple(
                    local_node_ids[key]
                    for key in target_declaration.normal_exit_block_keys
                )
                exception_successor_source_ids = tuple(
                    local_node_ids[key]
                    for key in target_declaration.exception_exit_block_keys
                )
            for successor in (
                segment.success_successor,
                *segment.short_circuit_successors,
            ):
                relation = Relation.PASSES_THROUGH
                flow = EdgeFlow.ALWAYS
                condition: Condition | None = None
                branch_id: str | None = None
                exception_id_value: str | None = None
                successor_owner_id = owner_id
                if type(successor) is ReturnSuccessor:
                    target_id = framework_terminal_id(segment, successor)
                    target_kind = next(
                        node.kind for node in nodes if node.id == target_id
                    )
                    relation = {
                        NodeKind.RESPONSE: Relation.RESPONDS_WITH,
                        NodeKind.REDIRECT: Relation.REDIRECTS_TO,
                        NodeKind.ABORT: Relation.ABORTS_WITH,
                        NodeKind.EXCEPTION: Relation.THROWS_TO,
                        NodeKind.EXIT: Relation.EXITS_AT,
                    }[target_kind]
                    if target_kind is NodeKind.EXCEPTION:
                        flow = EdgeFlow.EXCEPTION
                        condition = Condition(
                            "predicate",
                            "exception",
                            condition_hash("exception"),
                            ConditionPolarity.EXCEPTION,
                        )
                elif type(successor) is AsyncSuccessor:
                    target_id = local_node_ids[successor.target_local_key]
                    relation = Relation.DISPATCHES
                    flow = EdgeFlow.ASYNC
                else:
                    target_id = framework_target_id(successor.target_block_key)
                    if type(successor) is BranchSuccessor:
                        relation = Relation.BRANCHES_TO
                        flow = EdgeFlow.CONDITIONAL
                        matching_arm = next(
                            arm
                            for arm in branch_arms
                            if arm.branch_local_key == successor.branch_arm_key
                            and arm.arm_ordinal == successor.arm_ordinal
                            and arm.target_block_key == successor.target_block_key
                        )
                        condition = Condition(
                            matching_arm.condition.kind,
                            matching_arm.condition.normalized,
                            matching_arm.condition.hash,
                            matching_arm.condition.polarity,
                        )
                        branch_structure = structure_ir_by_key[successor.branch_arm_key]
                        branch_id = structure_ids[successor.branch_arm_key]
                        successor_owner_id = local_node_ids[
                            branch_structure.owner_declaration_key
                        ]
                    elif type(successor) is ExceptionSuccessor:
                        relation = Relation.THROWS_TO
                        flow = EdgeFlow.EXCEPTION
                        normalized = successor.caught_type_name or "exception"
                        condition = Condition(
                            "predicate",
                            normalized,
                            condition_hash(normalized),
                            ConditionPolarity.EXCEPTION,
                        )
                        exception_id_value = framework_exception_scope_ids[
                            successor.exception_scope_key
                        ]
                        exception_structure = structure_ir_by_key[
                            exception_scope_ir_by_key[
                                successor.exception_scope_key
                            ].structure_key
                        ]
                        successor_owner_id = local_node_ids[
                            exception_structure.owner_declaration_key
                        ]
                    elif type(successor) is LoopSuccessor:
                        relation = Relation.BRANCHES_TO
                        flow = {
                            LoopRole.BACK: EdgeFlow.LOOP,
                            LoopRole.BODY: EdgeFlow.CONDITIONAL,
                            LoopRole.EXIT: EdgeFlow.CONDITIONAL,
                        }[successor.loop_role]
                        if successor.loop_role is not LoopRole.BACK:
                            normalized = (
                                "loop_body"
                                if successor.loop_role is LoopRole.BODY
                                else "loop_exit"
                            )
                            condition = Condition(
                                "predicate",
                                normalized,
                                condition_hash(normalized),
                                (
                                    ConditionPolarity.LOOP_BODY
                                    if successor.loop_role is LoopRole.BODY
                                    else ConditionPolarity.LOOP_EXIT
                                ),
                            )
                    if type(successor) is LoopSuccessor:
                        branch_key = (
                            segment.local_key,
                            successor.kind,
                        )
                        structural_path = (
                            f"pipeline/{segment.pipeline_order}/{successor.kind}"
                        )
                        identity = {
                            "kind": StructureKind.BRANCH_GROUP.value,
                            "owner_node_id": owner_id,
                            "structural_path": structural_path,
                            "ordinal": 0,
                            "subtype": StructureSubtype.FRAMEWORK_SHORT_CIRCUIT.value,
                        }
                        derived_branch_id = branch_group_id(identity)
                        previous_branch_id = framework_branch_ids.setdefault(
                            branch_key, derived_branch_id
                        )
                        if previous_branch_id != derived_branch_id:
                            raise IRValidationError(
                                "semantic_collision",
                                "framework loop branch identity is inconsistent",
                            )
                        branch_id = derived_branch_id
                        structures.append(
                            Structure(
                                branch_id,
                                StructureKind.BRANCH_GROUP,
                                owner_id,
                                structural_path,
                                0,
                                StructureSubtype.FRAMEWORK_SHORT_CIRCUIT,
                                None,
                                None,
                                _evidence(segment.evidence),
                            )
                        )
                successor_source_ids = (
                    exception_successor_source_ids
                    if flow is EdgeFlow.EXCEPTION
                    else normal_successor_source_ids
                )
                if (
                    not successor_source_ids
                    and type(segment.target) is FrameworkLocalTarget
                ):
                    materialize_missing_framework_exit_boundary(
                        segment,
                        target_declaration,
                        "exception" if flow is EdgeFlow.EXCEPTION else "normal",
                    )
                    continue
                for successor_source_id in successor_source_ids:
                    append_edge(
                        source_id=successor_source_id,
                        target_id=target_id,
                        relation=relation,
                        flow=flow,
                        locator=segment.evidence.locator,
                        owner_id=successor_owner_id,
                        evidence=_evidence(segment.evidence),
                        condition=condition,
                        branch_id=branch_id,
                        exception_id=exception_id_value,
                        order=successor.order,
                        occurrence_path=(
                            f"pipeline/{segment.pipeline_order}/"
                            f"{successor.kind}/{successor.order}"
                        ),
                        occurrence_ordinal=successor.order,
                    )

        # One explicit synchronous root edge per resolved entrypoint.
        for candidate, _, public_id, _ in entrypoint_data:
            if candidate.handler_local_key is None:
                continue
            if candidate.framework_segment_keys:
                first_segment_key = candidate.framework_segment_keys[0]
                append_edge(
                    source_id=public_id,
                    target_id=framework_segment_node_ids[first_segment_key],
                    relation=Relation.ENTERS,
                    flow=EdgeFlow.ALWAYS,
                    locator=candidate.registration_locator,
                    owner_id=public_id,
                    evidence=_evidence(candidate.evidence),
                    order=0,
                    occurrence_path=(
                        f"{candidate.registration_locator.structural_path}/pipeline"
                        if type(candidate.registration_locator) is AstLocatorIR
                        else (
                            f"{candidate.registration_locator.structural_pointer}"
                            "/pipeline"
                        )
                    ),
                )
                continue
            append_edge(
                source_id=public_id,
                target_id=local_node_ids[candidate.handler_local_key],
                relation=Relation.ROUTES_TO,
                flow=EdgeFlow.ALWAYS,
                locator=candidate.registration_locator,
                owner_id=public_id,
                evidence=_evidence(candidate.evidence),
                order=0,
                occurrence_path=(
                    f"{candidate.registration_locator.structural_path}/handler"
                    if type(candidate.registration_locator) is AstLocatorIR
                    else f"{candidate.registration_locator.structural_pointer}/handler"
                ),
            )

        # Every callable declaration connects to its finite intraprocedural CFG.
        for declaration in declarations:
            append_edge(
                source_id=local_node_ids[declaration.local_key],
                target_id=local_node_ids[declaration.entry_block_key],
                relation=Relation.PASSES_THROUGH,
                flow=EdgeFlow.ALWAYS,
                locator=declaration.locator,
                owner_id=local_node_ids[declaration.local_key],
                evidence=_synthetic_evidence(declaration.locator),
                order=0,
                occurrence_path=f"{declaration.locator.structural_path}/entry",
            )

        arm_by_key = {
            (item.branch_local_key, item.source_block_key, item.target_block_key): item
            for item in branch_arms
        }
        merge_group_by_edge = {
            (arm.target_block_key, structure.continuation_block_key): structure_ids[
                structure.local_key
            ]
            for structure in structures_ir
            if structure.kind is StructureKind.BRANCH_GROUP
            and structure.continuation_block_key is not None
            for arm in branch_arms
            if arm.branch_local_key == structure.local_key
        }
        for block in blocks:
            source_id = local_node_ids[block.local_key]
            owner_id = local_node_ids[block.declaration_key]
            for successor in block.successors:
                target_id: str
                relation: Relation
                flow: EdgeFlow
                condition: Condition | None = None
                branch_id: str | None = None
                exception_id: str | None = None
                if type(successor) is AlwaysSuccessor:
                    target_id = local_node_ids[successor.target_block_key]
                    branch_id = merge_group_by_edge.get((
                        block.local_key,
                        successor.target_block_key,
                    ))
                    relation = (
                        Relation.MERGES_AT
                        if branch_id is not None
                        else Relation.PASSES_THROUGH
                    )
                    flow = EdgeFlow.ALWAYS
                elif type(successor) is BranchSuccessor:
                    arm = arm_by_key[
                        (
                            successor.branch_arm_key,
                            block.local_key,
                            successor.target_block_key,
                        )
                    ]
                    target_id = local_node_ids[successor.target_block_key]
                    relation = Relation.BRANCHES_TO
                    flow = EdgeFlow.CONDITIONAL
                    condition = Condition(
                        arm.condition.kind,
                        arm.condition.normalized,
                        arm.condition.hash,
                        arm.condition.polarity,
                    )
                    branch_id = structure_ids[arm.branch_local_key]
                elif type(successor) is ExceptionSuccessor:
                    target_id = local_node_ids[successor.target_block_key]
                    relation = Relation.THROWS_TO
                    flow = EdgeFlow.EXCEPTION
                    normalized = successor.caught_type_name or "exception"
                    condition = Condition(
                        "predicate",
                        normalized,
                        condition_hash(normalized),
                        __import__(
                            "hermes_cli.hades_graph_v2.model",
                            fromlist=["ConditionPolarity"],
                        ).ConditionPolarity.EXCEPTION,
                    )
                    exception_id = structure_ids[
                        exception_scope_ir_by_key[
                            successor.exception_scope_key
                        ].structure_key
                    ]
                elif type(successor) is LoopSuccessor:
                    target_id = local_node_ids[successor.target_block_key]
                    relation = Relation.BRANCHES_TO
                    flow = {
                        LoopRole.BACK: EdgeFlow.LOOP,
                        LoopRole.BODY: EdgeFlow.CONDITIONAL,
                        LoopRole.EXIT: EdgeFlow.CONDITIONAL,
                    }[successor.loop_role]
                    branch_id = loop_branch_ids[block.local_key]
                    if successor.loop_role is not LoopRole.BACK:
                        normalized = (
                            "loop_body"
                            if successor.loop_role is LoopRole.BODY
                            else "loop_exit"
                        )
                        condition = Condition(
                            "predicate",
                            normalized,
                            condition_hash(normalized),
                            (
                                ConditionPolarity.LOOP_BODY
                                if successor.loop_role is LoopRole.BODY
                                else ConditionPolarity.LOOP_EXIT
                            ),
                        )
                elif type(successor) is AsyncSuccessor:
                    target_id = local_node_ids[successor.target_local_key]
                    relation = Relation.DISPATCHES
                    flow = EdgeFlow.ASYNC
                elif type(successor) is ReturnSuccessor:
                    target_id = local_node_ids[successor.terminal_local_key]
                    terminal = next(
                        item
                        for item in terminals
                        if item.local_key == successor.terminal_local_key
                    )
                    relation = {
                        TerminalKind.RESPONSE: Relation.RESPONDS_WITH,
                        TerminalKind.REDIRECT: Relation.REDIRECTS_TO,
                        TerminalKind.ABORT: Relation.ABORTS_WITH,
                        TerminalKind.EXCEPTION: Relation.THROWS_TO,
                        TerminalKind.EXIT: Relation.EXITS_AT,
                    }[terminal.kind]
                    flow = (
                        EdgeFlow.EXCEPTION
                        if terminal.kind is TerminalKind.EXCEPTION
                        else EdgeFlow.ALWAYS
                    )
                    if flow is EdgeFlow.EXCEPTION:
                        normalized = terminal.exception_type or "exception"
                        condition = Condition(
                            "predicate",
                            normalized,
                            condition_hash(normalized),
                            __import__(
                                "hermes_cli.hades_graph_v2.model",
                                fromlist=["ConditionPolarity"],
                            ).ConditionPolarity.EXCEPTION,
                        )
                else:  # pragma: no cover - AdapterResult.validate closes the union.
                    raise AssertionError("unknown successor")
                append_edge(
                    source_id=source_id,
                    target_id=target_id,
                    relation=relation,
                    flow=flow,
                    locator=block.locator,
                    owner_id=owner_id,
                    evidence=_synthetic_evidence(block.locator),
                    condition=condition,
                    branch_id=branch_id,
                    exception_id=exception_id,
                    order=successor.order,
                    occurrence_path=(
                        f"{block.locator.structural_path}/successor/{successor.kind}"
                        f"/{successor.order}"
                    ),
                    occurrence_ordinal=successor.order,
                )

        def materialize_unresolved_exception_target(
            *,
            invocation: Edge,
            edge_ir: EdgeFactIR,
            site: CallSite,
            scope_ir: ExceptionScope,
            source_id: str,
            exit_key: str,
            ordinal: int,
        ) -> None:
            """Create one conservative frontier for an unknown catch relation."""

            if invocation.call_site_id is None:
                raise IRValidationError(
                    "invalid_call_site",
                    "exception uncertainty requires its exact invocation call site",
                )
            caller = declaration_by_key[site.caller_declaration_key]
            caller_id = local_node_ids[site.caller_declaration_key]
            exception_id_value = structure_ids[scope_ir.structure_key]
            role = "exception_target_unresolved"
            structural_path = (
                f"{site.locator.structural_path}/exception_return/{ordinal}/"
                f"unknown_boundary/{exit_key[:16]}"
            )
            boundary_identity = SourceOccurrenceIdentity(
                "source_occurrence",
                context.workspace_binding_id,
                caller.language,
                NodeKind.UNKNOWN_BOUNDARY,
                caller_id,
                structural_path,
                ordinal,
                role,
            )
            boundary_id = node_id({
                "variant": "source_occurrence",
                "workspace_binding_id": context.workspace_binding_id,
                "language": caller.language,
                "kind": NodeKind.UNKNOWN_BOUNDARY.value,
                "owner_node_id": caller_id,
                "structural_path": structural_path,
                "ordinal": ordinal,
                "semantic_role": role,
            })
            normalized = "exception_type_unresolved"
            condition = Condition(
                "predicate",
                normalized,
                condition_hash(normalized),
                ConditionPolarity.EXCEPTION,
            )
            occurrence_path = (
                f"{site.locator.structural_path}/exception_return/{ordinal}/unresolved"
            )
            occurrence_payload = {
                "kind": "ast",
                "owner_node_id": caller_id,
                "ast_path": occurrence_path,
                "ordinal": ordinal,
            }
            public_edge_id = edge_id({
                "source_id": source_id,
                "target_id": boundary_id,
                "relation": Relation.THROWS_TO.value,
                "flow": EdgeFlow.EXCEPTION.value,
                "condition_hash": condition.hash,
                "branch_group_id": None,
                "call_site_id": invocation.call_site_id,
                "exception_scope_id": exception_id_value,
                "occurrence": occurrence_payload,
            })
            subject = EdgeSubject(public_edge_id)
            question = "Which lexical catch arm handles the unresolved exception type?"
            fingerprint_identity = {
                "domain": "graph",
                "project_id": context.project_id,
                "workspace_binding_id": context.workspace_binding_id,
                "subject": {"edge_id": public_edge_id},
                "resolution_kind": ResolutionKind.EXCEPTION_TARGET.value,
                "reason_code": ReasonCode.EXCEPTION_TARGET_UNRESOLVED.value,
                "question": question,
            }
            fingerprint = uncertainty_fingerprint(fingerprint_identity)
            uncertainty_id_value = f"hades:uncertainty:v2:{fingerprint}"
            unresolved_evidence = _evidence(
                edge_ir.evidence,
                origin=EvidenceOrigin.UNRESOLVED,
                inference_rule=None,
            )
            unknown_nodes.append(
                Node(
                    boundary_id,
                    boundary_identity,
                    NodeKind.UNKNOWN_BOUNDARY,
                    caller.language,
                    None,
                    "unresolved exception target",
                    None,
                    None,
                    uncertainty_id_value,
                    _source_location(site.locator),
                    BoundaryProperties(
                        reason_code=ReasonCode.EXCEPTION_TARGET_UNRESOLVED.value
                    ),
                    unresolved_evidence,
                )
            )
            propagated = append_edge(
                source_id=source_id,
                target_id=boundary_id,
                relation=Relation.THROWS_TO,
                flow=EdgeFlow.EXCEPTION,
                locator=site.locator,
                owner_id=caller_id,
                evidence=unresolved_evidence,
                condition=condition,
                call_id=invocation.call_site_id,
                exception_id=exception_id_value,
                occurrence_path=occurrence_path,
                occurrence_ordinal=ordinal,
                uncertainty_id_value=uncertainty_id_value,
            )
            if propagated.id != public_edge_id:
                raise IRValidationError(
                    "semantic_collision",
                    "exception uncertainty edge identity is inconsistent",
                )
            uncertainties.append(
                Uncertainty(
                    uncertainty_id_value,
                    "graph",
                    subject,
                    ResolutionKind.EXCEPTION_TARGET,
                    ReasonCode.EXCEPTION_TARGET_UNRESOLVED,
                    question,
                    ("resolve_exception_type",),
                    (
                        SourceRef(
                            site.locator.source_location.path,
                            site.locator.source_location.start_line,
                        ),
                    ),
                    (),
                    (),
                    CandidateSetKnowledge.NOT_APPLICABLE,
                    Priority.HIGH,
                    "Exception flow stops until the thrown type is resolved.",
                    fingerprint,
                )
            )
            effective_coverage_events.append(
                CoverageEvent(
                    caller.language,
                    CoverageCapability.EXCEPTIONS,
                    CoverageOutcome.PARTIAL,
                    ReasonCode.EXCEPTION_TARGET_UNRESOLVED.value,
                    site.locator.source_location.path,
                    0,
                    1,
                )
            )

        # Normal returns are generated only for the invocation's exact call
        # site and continuation.  They are never shared across callers.
        for invocation, edge_ir in invocation_ir:
            if type(edge_ir.target) is not LocalNodeTarget:
                continue
            callee = declaration_by_key[edge_ir.target.local_key]
            site = next(
                item
                for item in call_sites
                if call_site_structure_by_local[item.local_key]
                == invocation.call_site_id
            )
            owner_id = local_node_ids[site.caller_declaration_key]
            continuation_key = structure_ir_by_key[
                edge_ir.call_site_key
            ].continuation_block_key
            if continuation_key is None:
                raise IRValidationError(
                    "call_site_continuation_missing",
                    "invocation call-site structure must have a continuation block",
                )
            for ordinal, exit_key in enumerate(callee.normal_exit_block_keys):
                source_id = local_node_ids[exit_key]
                target_id = local_node_ids[continuation_key]
                if source_id == target_id:
                    continue
                append_edge(
                    source_id=source_id,
                    target_id=target_id,
                    relation=Relation.RETURNS_TO,
                    flow=EdgeFlow.ALWAYS,
                    locator=site.locator,
                    owner_id=owner_id,
                    evidence=_synthetic_evidence(site.locator),
                    call_id=invocation.call_site_id,
                    occurrence_path=f"{site.locator.structural_path}/return/{ordinal}",
                    occurrence_ordinal=ordinal,
                )

            exception_terminal_by_block = {
                terminal.source_block_key: terminal
                for terminal in terminals
                if terminal.kind is TerminalKind.EXCEPTION
            }
            scope_by_structure_key = {
                scope_ir.structure_key: scope_ir for scope_ir in exception_scopes
            }

            for ordinal, exit_key in enumerate(callee.exception_exit_block_keys):
                source_id = local_node_ids[exit_key]
                thrown_type = (
                    exception_terminal_by_block[exit_key].exception_type
                    if exit_key in exception_terminal_by_block
                    else None
                )
                exception_structure_key = site.exception_scope_key
                exception_id_value: str | None = None
                target_id: str | None = None
                unresolved_scope: ExceptionScope | None = None
                if exception_structure_key is not None:
                    scope_ir = scope_by_structure_key.get(exception_structure_key)
                    while scope_ir is not None:
                        owner_language = declaration_by_key[
                            scope_ir.declaration_key
                        ].language
                        matching_catch_arm = None
                        for arm in scope_ir.catch_arms:
                            match = _exception_type_matches(
                                owner_language,
                                thrown_type,
                                arm.caught_type_name,
                            )
                            if match is ExceptionMatch.MATCH:
                                matching_catch_arm = arm
                                break
                            if match is ExceptionMatch.UNRESOLVED:
                                unresolved_scope = scope_ir
                                break
                        if matching_catch_arm is not None:
                            target_id = local_node_ids[
                                matching_catch_arm.target_block_key
                            ]
                            exception_id_value = structure_ids[scope_ir.structure_key]
                            break
                        if unresolved_scope is not None:
                            break
                        scope_ir = next(
                            (
                                item
                                for item in exception_scopes
                                if item.local_key == scope_ir.parent_scope_key
                            ),
                            None,
                        )
                if unresolved_scope is not None:
                    materialize_unresolved_exception_target(
                        invocation=invocation,
                        edge_ir=edge_ir,
                        site=site,
                        scope_ir=unresolved_scope,
                        source_id=source_id,
                        exit_key=exit_key,
                        ordinal=ordinal,
                    )
                    continue
                if target_id is None:
                    caller_id = local_node_ids[site.caller_declaration_key]
                    caller = declaration_by_key[site.caller_declaration_key]
                    structural_path = (
                        f"{site.locator.structural_path}/unhandled_exception"
                    )
                    identity = SourceOccurrenceIdentity(
                        "source_occurrence",
                        context.workspace_binding_id,
                        caller.language,
                        NodeKind.EXCEPTION,
                        caller_id,
                        structural_path,
                        ordinal,
                        "unhandled_exception",
                    )
                    target_id = node_id({
                        "variant": "source_occurrence",
                        "workspace_binding_id": context.workspace_binding_id,
                        "language": caller.language,
                        "kind": NodeKind.EXCEPTION.value,
                        "owner_node_id": caller_id,
                        "structural_path": structural_path,
                        "ordinal": ordinal,
                        "semantic_role": "unhandled_exception",
                    })
                    nodes.append(
                        Node(
                            target_id,
                            identity,
                            NodeKind.EXCEPTION,
                            caller.language,
                            None,
                            "unhandled exception",
                            None,
                            None,
                            None,
                            _source_location(site.locator),
                            TerminalProperties(
                                None,
                                thrown_type or "exception",
                                "exception",
                            ),
                            _synthetic_evidence(site.locator),
                        )
                    )
                normalized = thrown_type or "exception"
                propagated = append_edge(
                    source_id=source_id,
                    target_id=target_id,
                    relation=Relation.THROWS_TO,
                    flow=EdgeFlow.EXCEPTION,
                    locator=site.locator,
                    owner_id=local_node_ids[site.caller_declaration_key],
                    evidence=_synthetic_evidence(site.locator),
                    condition=Condition(
                        "predicate",
                        normalized,
                        condition_hash(normalized),
                        ConditionPolarity.EXCEPTION,
                    ),
                    call_id=invocation.call_site_id,
                    exception_id=exception_id_value,
                    occurrence_path=(
                        f"{site.locator.structural_path}/exception_return/{ordinal}"
                    ),
                    occurrence_ordinal=ordinal,
                )
                if propagated.call_site_id is None:
                    raise IRValidationError(
                        "invalid_call_site",
                        "interprocedural throw requires its exact invocation call site",
                    )

        edges = list(_deduplicate_edges(edges))

        nodes.extend(unknown_nodes)
        nodes = list(_deduplicate_public_records(nodes, record_name="node"))
        structures = list(
            _deduplicate_public_records(structures, record_name="structure")
        )
        entrypoints: list[Entrypoint] = []
        for candidate, identity, public_id, _ in entrypoint_data:
            uncertainty_id_value = (
                None
                if candidate.unresolved_fact_local_key is None
                else uncertainty_id_by_fact[candidate.unresolved_fact_local_key]
            )
            entrypoints.append(
                Entrypoint(
                    public_id,
                    candidate.kind,
                    _entrypoint_label(candidate),
                    candidate.framework,
                    candidate.method_semantics,
                    candidate.methods,
                    candidate.public_path,
                    candidate.public_name,
                    (
                        None
                        if candidate.handler_local_key is None
                        else local_node_ids[candidate.handler_local_key]
                    ),
                    uncertainty_id_value,
                    identity.trigger,
                    identity.match_constraints,
                    identity.registration_occurrence,
                    _evidence(
                        candidate.evidence,
                        origin=(
                            EvidenceOrigin.UNRESOLVED
                            if uncertainty_id_value is not None
                            else candidate.evidence.origin
                        ),
                        inference_rule=None,
                    ),
                )
            )

        language_names = tuple(
            sorted(
                set(context.detected_languages)
                | {event.language for event in effective_coverage_events}
                | {
                    node.identity.language
                    for node in nodes
                    if isinstance(node.identity, FileIdentity)
                    and node.identity.language is not None
                }
            )
        )
        if not language_names and context.inventory_files:
            language_names = ("unknown",)
        capabilities, language_completeness = self._completeness(
            language_names,
            effective_coverage_events,
            unresolved_facts,
            entrypoint_candidates,
        )
        topology = CanonicalTopology(
            tuple(nodes),
            tuple(structures),
            tuple(edges),
            tuple(uncertainties),
            capabilities,
            tuple(
                sorted(
                    (
                        local_node_ids[item.local_key],
                        tuple(
                            sorted(
                                local_node_ids[key]
                                for key in item.normal_exit_block_keys
                            )
                        ),
                    )
                    for item in declarations
                )
            ),
            tuple(
                sorted(
                    (
                        local_node_ids[item.local_key],
                        tuple(
                            sorted(
                                local_node_ids[key]
                                for key in item.exception_exit_block_keys
                            )
                        ),
                    )
                    for item in declarations
                )
            ),
        )
        # Summaries are computed before entrypoint traversal so recursive
        # callable components converge independently of public-flow order.
        summaries = build_callable_summaries(topology)
        flows, flow_steps = build_lifecycle_flows(
            topology,
            tuple(sorted(entrypoints, key=lambda item: item.id)),
            summaries,
        )

        completeness = Completeness(
            self._status(capabilities), capabilities, language_completeness
        )
        kind_counts = Counter(item.entrypoint_kind.value for item in entrypoints)
        by_kind_kwargs = {
            field: kind_counts.get(field) or None
            for field in (
                "http_route",
                "process_main",
                "cli_command",
                "scheduled_job",
                "queue_consumer",
                "event_listener",
                "rpc_method",
                "public_api",
            )
        }
        file_status_counts = Counter(
            node.properties.analysis_status
            for node in nodes
            if isinstance(node.identity, FileIdentity)
        )
        coverage = Coverage(
            CoverageScope(
                (".",),
                sha256_jcs(list(context.graph_config.excluded_paths)),
                context.excluded_path_count,
            ),
            FileCoverage(
                len(file_node_ids),
                len(file_node_ids),
                sum(inventory_by_path[path].parser_candidate for path in file_node_ids),
                file_status_counts[AnalysisStatus.ANALYZED],
                file_status_counts[AnalysisStatus.UNSUPPORTED],
                file_status_counts[AnalysisStatus.FAILED],
                file_status_counts[AnalysisStatus.TOO_LARGE],
                file_status_counts[AnalysisStatus.BUDGET_OMITTED],
            ),
            EntrypointCoverage(
                len(entrypoints),
                len(entrypoints),
                sum(
                    flow.completeness.status is CompletenessStatus.PARTIAL
                    for flow in flows
                    if flow.kind is not FlowKind.ASYNC_FLOW
                ),
                EntrypointKindCounts(**by_kind_kwargs),
            ),
            RecordCoverage(
                len(nodes),
                len(structures),
                len(edges),
                len(flows),
                len(flow_steps),
                len(uncertainties),
                0,
            ),
        )
        file_counts = Counter(
            node.identity.language
            for node in nodes
            if isinstance(node.identity, FileIdentity)
            and node.identity.language is not None
        )
        analyzed_file_counts = Counter(
            node.identity.language
            for node in nodes
            if isinstance(node.identity, FileIdentity)
            and node.identity.language is not None
            and node.properties.analysis_status is AnalysisStatus.ANALYZED
        )
        languages = tuple(
            LanguageRecord(
                name,
                "hades.lifecycle.v2",
                "2",
                file_counts[name],
                analyzed_file_counts[name],
            )
            for name in language_names
        )
        frameworks = tuple(
            sorted(
                context.detected_frameworks, key=lambda item: (item.language, item.name)
            )
        )
        artifact = GraphArtifactV2(
            "hades.code_graph.v2",
            self._generated_at(),
            ProjectIdentity(context.project_id, context.workspace_binding_id),
            context.source_identity,
            GraphContractMetadata(
                "hades.graph_artifact.v2",
                _ZERO_DIGEST,
                "queued",
                completeness,
                coverage,
            ),
            frameworks,
            languages,
            tuple(sorted(entrypoints, key=lambda item: item.id)),
            tuple(sorted(nodes, key=lambda item: item.id)),
            tuple(sorted(structures, key=lambda item: item.id)),
            tuple(sorted(edges, key=lambda item: item.id)),
            tuple(sorted(flows, key=lambda item: item.id)),
            tuple(sorted(flow_steps, key=lambda item: item.id)),
            tuple(sorted(uncertainties, key=lambda item: item.id)),
        )

        # Bootstrap the digest only so the closed validator can perform the
        # mandated pre-digest structural pass.  The normative final sequence is
        # then validate -> calculate -> replace only digest -> validate.
        boot_digest = artifact_graph_version(
            __import__(
                "hermes_cli.hades_graph_v2", fromlist=["artifact_to_payload"]
            ).artifact_to_payload(artifact)
        )
        artifact = replace(
            artifact,
            graph_contract=replace(
                artifact.graph_contract, artifact_graph_version=boot_digest
            ),
        )
        validate_artifact(artifact)
        final_digest = artifact_graph_version(
            __import__(
                "hermes_cli.hades_graph_v2", fromlist=["artifact_to_payload"]
            ).artifact_to_payload(artifact)
        )
        artifact = replace(
            artifact,
            graph_contract=replace(
                artifact.graph_contract, artifact_graph_version=final_digest
            ),
        )
        validate_artifact(artifact)
        return artifact

    @staticmethod
    def _status(capabilities: Capabilities) -> CompletenessStatus:
        return (
            CompletenessStatus.PARTIAL
            if any(
                getattr(capabilities, name).status
                in {CapabilityStatus.PARTIAL, CapabilityStatus.UNSUPPORTED}
                for name in _CAPABILITY_FIELDS
            )
            else CompletenessStatus.FULL
        )

    @staticmethod
    def _count(represented: int, capabilities: Sequence[Capability]) -> CountKnowledge:
        reasons = tuple(
            reason.code for capability in capabilities for reason in capability.reasons
        )
        status = (
            CapabilityStatus.PARTIAL
            if any(
                item.status in {CapabilityStatus.PARTIAL, CapabilityStatus.UNSUPPORTED}
                for item in capabilities
            )
            else CapabilityStatus.FULL
        )
        return count_knowledge(represented, 0, status, reasons)

    def _completeness(
        self,
        languages: Sequence[str],
        events: Sequence[CoverageEvent],
        unresolved: Sequence[UnresolvedFact],
        entrypoints: Sequence[EntrypointCandidate],
    ) -> tuple[Capabilities, tuple[LanguageCompleteness, ...]]:
        reason_counts: dict[tuple[str, str, ReasonCode], int] = Counter()
        reason_paths: dict[tuple[str, str, ReasonCode], set[str]] = defaultdict(set)
        statuses: dict[tuple[str, str], CapabilityStatus] = {}
        outcome_status = {
            CoverageOutcome.FULL: CapabilityStatus.FULL,
            CoverageOutcome.PARTIAL: CapabilityStatus.PARTIAL,
            CoverageOutcome.UNSUPPORTED: CapabilityStatus.UNSUPPORTED,
            CoverageOutcome.NOT_APPLICABLE: CapabilityStatus.NOT_APPLICABLE,
        }
        rank = {
            CapabilityStatus.NOT_APPLICABLE: 0,
            CapabilityStatus.FULL: 1,
            CapabilityStatus.PARTIAL: 2,
            CapabilityStatus.UNSUPPORTED: 3,
        }
        for event in events:
            name = _CAPABILITY_BY_IR[event.capability]
            candidate_status = outcome_status[event.outcome]
            key = (event.language, name)
            if key not in statuses or rank[candidate_status] > rank[statuses[key]]:
                statuses[key] = candidate_status
            if event.reason_code is not None:
                reason = _reason_code(event.reason_code)
                reason_counts[(event.language, name, reason)] += max(
                    1, event.omitted_count
                )
                if event.path is not None:
                    reason_paths[(event.language, name, reason)].add(event.path)

        entrypoint_language_by_unresolved = {
            candidate.unresolved_fact_local_key: (
                next(iter(languages)) if len(languages) == 1 else "unknown"
            )
            for candidate in entrypoints
            if candidate.unresolved_fact_local_key is not None
        }
        for fact in unresolved:
            language = entrypoint_language_by_unresolved.get(
                fact.local_key,
                next(iter(languages)) if len(languages) == 1 else "unknown",
            )
            name = _CAPABILITY_BY_RESOLUTION[fact.resolution_kind.value]
            statuses[(language, name)] = CapabilityStatus.PARTIAL
            reason = _reason_code(fact.reason_code)
            reason_counts[(language, name, reason)] += 1
            reason_paths[(language, name, reason)].update(
                locator.source_location.path for locator in fact.source_locators
            )

        def build_for(language: str | None) -> Capabilities:
            values: dict[str, Capability] = {}
            for name in _CAPABILITY_FIELDS:
                scoped_languages = tuple(languages) if language is None else (language,)
                framework_not_applicable = name == "framework_lifecycle" and not any(
                    item.framework for item in entrypoints
                )
                missing_languages = (
                    ()
                    if framework_not_applicable
                    else tuple(
                        item
                        for item in scoped_languages
                        if (item, name) not in statuses
                    )
                )
                selected_statuses = [
                    statuses[(item, name)]
                    for item in scoped_languages
                    if (item, name) in statuses
                ]
                if missing_languages:
                    selected_statuses.append(CapabilityStatus.PARTIAL)
                if selected_statuses:
                    status = max(selected_statuses, key=lambda item: rank[item])
                elif framework_not_applicable:
                    status = CapabilityStatus.NOT_APPLICABLE
                else:
                    status = CapabilityStatus.PARTIAL
                reasons: list[CapabilityReason] = []
                for reason in ReasonCode:
                    count = sum(
                        reason_counts[(item, name, reason)] for item in scoped_languages
                    )
                    if reason is ReasonCode.INVALID_SOURCE_FACT and missing_languages:
                        count += len(missing_languages)
                    if not count:
                        continue
                    paths = sorted(
                        path
                        for item in scoped_languages
                        for path in reason_paths[(item, name, reason)]
                    )[:10]
                    reasons.append(
                        CapabilityReason(
                            reason,
                            count,
                            language,
                            tuple(paths),
                        )
                    )
                values[name] = Capability(
                    status,
                    tuple(
                        sorted(
                            reasons,
                            key=lambda item: (
                                item.code.value,
                                item.language or "",
                                item.paths_sample[0] if item.paths_sample else "",
                            ),
                        )
                    ),
                )
            return Capabilities(**values)

        language_rows = tuple(
            LanguageCompleteness(
                language,
                self._status(capabilities := build_for(language)),
                capabilities,
            )
            for language in sorted(languages)
        )
        return build_for(None), language_rows


__all__ = ["GraphBuilder"]
