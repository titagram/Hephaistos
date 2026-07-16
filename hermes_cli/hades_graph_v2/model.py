"""Closed immutable Python model for the graph v2 producer artifact."""

from __future__ import annotations

import keyword
import types
from dataclasses import MISSING, dataclass, field, fields, is_dataclass
from enum import Enum
from functools import lru_cache
from typing import (
    Literal,
    Mapping,
    TypeAlias,
    Union,
    cast,
    get_args,
    get_origin,
    get_type_hints,
)

from .schema import JsonScalar, JsonValue, validate_schema


class _ContractEnum(str, Enum):
    """String enum whose wire representation is its exact value."""


class Knowledge(_ContractEnum):
    EXACT = "exact"
    ABSENCE_VERIFIED = "absence_verified"
    UNKNOWN = "unknown"


class ReasonCode(_ContractEnum):
    PARSER_UNAVAILABLE = "parser_unavailable"
    PARSER_FAILED = "parser_failed"
    UNSUPPORTED_LANGUAGE = "unsupported_language"
    FILE_READ_FAILED = "file_read_failed"
    FILE_TOO_LARGE = "file_too_large"
    RECORD_TOO_LARGE = "record_too_large"
    RESOURCE_BUDGET_REACHED = "resource_budget_reached"
    ENTRYPOINT_UNRESOLVED = "entrypoint_unresolved"
    CALL_TARGET_UNRESOLVED = "call_target_unresolved"
    DYNAMIC_DISPATCH = "dynamic_dispatch"
    REFLECTION_OR_GENERATED_CODE = "reflection_or_generated_code"
    FRAMEWORK_CONFIG_UNRESOLVED = "framework_config_unresolved"
    EXCEPTION_TARGET_UNRESOLVED = "exception_target_unresolved"
    ASYNC_TARGET_UNRESOLVED = "async_target_unresolved"
    EXTERNAL_BOUNDARY_UNRESOLVED = "external_boundary_unresolved"
    SYMLINK_UNAVAILABLE = "symlink_unavailable"
    SUBMODULE_UNAVAILABLE = "submodule_unavailable"
    GRAPHIFY_CANDIDATE = "graphify_candidate"
    INVALID_SOURCE_FACT = "invalid_source_fact"
    VERIFIED_TARGET_NOT_MATERIALIZED = "verified_target_not_materialized"


class CapabilityStatus(_ContractEnum):
    FULL = "full"
    PARTIAL = "partial"
    UNSUPPORTED = "unsupported"
    NOT_APPLICABLE = "not_applicable"


class CompletenessStatus(_ContractEnum):
    FULL = "full"
    PARTIAL = "partial"


class FrameworkKnowledge(_ContractEnum):
    VERIFIED = "verified"
    UNRESOLVED = "unresolved"


class EntrypointKind(_ContractEnum):
    HTTP_ROUTE = "http_route"
    PROCESS_MAIN = "process_main"
    CLI_COMMAND = "cli_command"
    SCHEDULED_JOB = "scheduled_job"
    QUEUE_CONSUMER = "queue_consumer"
    EVENT_LISTENER = "event_listener"
    RPC_METHOD = "rpc_method"
    PUBLIC_API = "public_api"


class MethodSemantics(_ContractEnum):
    EXPLICIT = "explicit"
    UNRESTRICTED = "unrestricted"
    NOT_APPLICABLE = "not_applicable"


class TriggerKind(_ContractEnum):
    HTTP = "http"
    PROCESS = "process"
    CLI = "cli"
    SCHEDULE = "schedule"
    QUEUE = "queue"
    EVENT = "event"
    RPC = "rpc"
    LIBRARY = "library"


class NodeKind(_ContractEnum):
    ENTRYPOINT = "entrypoint"
    FILE = "file"
    MODULE = "module"
    NAMESPACE = "namespace"
    CLASS = "class"
    INTERFACE = "interface"
    TRAIT = "trait"
    ENUM = "enum"
    FUNCTION = "function"
    METHOD = "method"
    BASIC_BLOCK = "basic_block"
    BRANCH = "branch"
    MERGE = "merge"
    LOOP = "loop"
    MIDDLEWARE = "middleware"
    GUARD = "guard"
    AUTHORIZATION = "authorization"
    VALIDATOR = "validator"
    BINDING = "binding"
    CONTROLLER = "controller"
    SERVICE = "service"
    DOMAIN = "domain"
    MODEL = "model"
    REPOSITORY = "repository"
    TABLE = "table"
    QUERY = "query"
    CACHE = "cache"
    STORAGE = "storage"
    INTEGRATION = "integration"
    EXTERNAL_BOUNDARY = "external_boundary"
    RESPONSE = "response"
    REDIRECT = "redirect"
    ABORT = "abort"
    EXCEPTION = "exception"
    EXIT = "exit"
    EVENT = "event"
    LISTENER = "listener"
    JOB = "job"
    QUEUE = "queue"
    ASYNC_BOUNDARY = "async_boundary"
    FRAMEWORK_BOUNDARY = "framework_boundary"
    TEST = "test"
    UNKNOWN_BOUNDARY = "unknown_boundary"


class AnalysisStatus(_ContractEnum):
    ANALYZED = "analyzed"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"
    TOO_LARGE = "too_large"
    BUDGET_OMITTED = "budget_omitted"


class EvidenceOrigin(_ContractEnum):
    VERIFIED_FROM_CODE = "verified_from_code"
    INFERRED = "inferred"
    UNRESOLVED = "unresolved"


class StructureKind(_ContractEnum):
    CALL_SITE = "call_site"
    BRANCH_GROUP = "branch_group"
    EXCEPTION_SCOPE = "exception_scope"


class StructureSubtype(_ContractEnum):
    CALL = "call"
    IF = "if"
    SWITCH = "switch"
    MATCH = "match"
    TERNARY = "ternary"
    LOOP = "loop"
    EXCEPTION_DISPATCH = "exception_dispatch"
    DYNAMIC_DISPATCH = "dynamic_dispatch"
    FRAMEWORK_SHORT_CIRCUIT = "framework_short_circuit"
    TRY_CATCH = "try_catch"
    TRY_FINALLY = "try_finally"
    TRY_CATCH_FINALLY = "try_catch_finally"
    FRAMEWORK_EXCEPTION_HANDLER = "framework_exception_handler"


class Relation(_ContractEnum):
    ENTERS = "enters"
    ROUTES_TO = "routes_to"
    PASSES_THROUGH = "passes_through"
    BINDS = "binds"
    VALIDATES = "validates"
    AUTHORIZES = "authorizes"
    INVOKES = "invokes"
    RETURNS_TO = "returns_to"
    BRANCHES_TO = "branches_to"
    MERGES_AT = "merges_at"
    THROWS_TO = "throws_to"
    READS = "reads"
    WRITES = "writes"
    QUERIES = "queries"
    CALLS_EXTERNAL = "calls_external"
    EMITS = "emits"
    DISPATCHES = "dispatches"
    HANDLES = "handles"
    SCHEDULES = "schedules"
    RESPONDS_WITH = "responds_with"
    REDIRECTS_TO = "redirects_to"
    ABORTS_WITH = "aborts_with"
    EXITS_AT = "exits_at"
    DECLARES = "declares"
    CONTAINS = "contains"
    IMPORTS = "imports"
    INHERITS = "inherits"
    IMPLEMENTS = "implements"
    REFERENCES = "references"
    TESTS = "tests"
    DOCUMENTS = "documents"
    MAPS_TO = "maps_to"


class EdgeFlow(_ContractEnum):
    ALWAYS = "always"
    CONDITIONAL = "conditional"
    ALTERNATIVE = "alternative"
    EXCEPTION = "exception"
    ASYNC = "async"
    LOOP = "loop"


class ConditionPolarity(_ContractEnum):
    TRUE = "true"
    FALSE = "false"
    CASE = "case"
    DEFAULT = "default"
    LOOP_BODY = "loop_body"
    LOOP_EXIT = "loop_exit"
    EXCEPTION = "exception"
    FINALLY = "finally"


class FlowKind(_ContractEnum):
    REQUEST_LIFECYCLE = "request_lifecycle"
    EXECUTION_FLOW = "execution_flow"
    ASYNC_FLOW = "async_flow"


class Stage(_ContractEnum):
    ENTRY = "entry"
    ROUTING = "routing"
    MIDDLEWARE = "middleware"
    SECURITY = "security"
    INPUT = "input"
    HANDLER = "handler"
    DOMAIN = "domain"
    DATA = "data"
    INTEGRATION = "integration"
    ASYNC = "async"
    RESPONSE = "response"
    ERROR = "error"


class AsyncContext(_ContractEnum):
    SYNCHRONOUS = "synchronous"
    LINKED_ASYNC = "linked_async"


class BackboneRole(_ContractEnum):
    MANDATORY = "mandatory"
    BRANCH = "branch"
    EXCEPTION = "exception"
    LOOP = "loop"
    ASYNC = "async"


class ResolutionKind(_ContractEnum):
    CALL_TARGET = "call_target"
    ENTRYPOINT_HANDLER = "entrypoint_handler"
    ASYNC_TARGET = "async_target"
    EXCEPTION_TARGET = "exception_target"
    FRAMEWORK_TARGET = "framework_target"
    EXTERNAL_TARGET = "external_target"


class CandidateSetKnowledge(_ContractEnum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    NOT_APPLICABLE = "not_applicable"


class Priority(_ContractEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class ProjectIdentity:
    project_id: str
    workspace_binding_id: str


@dataclass(frozen=True, slots=True)
class SourceIdentity:
    head_commit: str | None
    tree_sha256: str
    dirty: bool
    branch: str | None


@dataclass(frozen=True, slots=True)
class LanguageRecord:
    name: str
    extractor: str
    extractor_version: str
    detected_file_count: int
    analyzed_file_count: int


@dataclass(frozen=True, slots=True)
class FrameworkRecord:
    language: str
    name: str
    version: str | None
    detector: str
    configuration_paths: tuple[str, ...]
    knowledge: FrameworkKnowledge


@dataclass(frozen=True, slots=True)
class CapabilityReason:
    code: ReasonCode
    count: int
    language: str | None
    paths_sample: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Capability:
    status: CapabilityStatus
    reasons: tuple[CapabilityReason, ...]


@dataclass(frozen=True, slots=True)
class Capabilities:
    inventory: Capability
    entrypoint_discovery: Capability
    symbol_resolution: Capability
    call_graph: Capability
    control_flow: Capability
    framework_lifecycle: Capability
    exceptions: Capability
    async_: Capability = field(metadata={"wire_name": "async"})
    data_access: Capability


@dataclass(frozen=True, slots=True)
class LanguageCompleteness:
    language: str
    status: CompletenessStatus
    capabilities: Capabilities


@dataclass(frozen=True, slots=True)
class Completeness:
    status: CompletenessStatus
    capabilities: Capabilities
    languages: tuple[LanguageCompleteness, ...]


@dataclass(frozen=True, slots=True)
class FlowCompleteness:
    status: CompletenessStatus
    capabilities: Capabilities


@dataclass(frozen=True, slots=True)
class CoverageScope:
    included_roots: tuple[str, ...]
    excluded_config_sha256: str
    excluded_path_count: int


@dataclass(frozen=True, slots=True)
class FileCoverage:
    discovered: int
    hashed: int
    parser_candidates: int
    analyzed: int
    unsupported: int
    failed: int
    too_large: int
    budget_omitted: int


def _omitted_none() -> object:
    return field(default=None, metadata={"omit_none": True})


class _OmittedProperty(Enum):
    VALUE = "__hades_internal_omitted_property__"


_OMITTED_PROPERTY = _OmittedProperty.VALUE


@dataclass(frozen=True, slots=True)
class EntrypointKindCounts:
    http_route: int | None = _omitted_none()
    process_main: int | None = _omitted_none()
    cli_command: int | None = _omitted_none()
    scheduled_job: int | None = _omitted_none()
    queue_consumer: int | None = _omitted_none()
    event_listener: int | None = _omitted_none()
    rpc_method: int | None = _omitted_none()
    public_api: int | None = _omitted_none()


@dataclass(frozen=True, slots=True)
class EntrypointCoverage:
    detected: int
    analyzed: int
    partial: int
    by_kind: EntrypointKindCounts


@dataclass(frozen=True, slots=True)
class RecordCoverage:
    nodes: int
    structures: int
    edges: int
    flows: int
    flow_steps: int
    uncertainties: int
    omitted_by_bundle_budget: int


@dataclass(frozen=True, slots=True)
class Coverage:
    scope: CoverageScope
    files: FileCoverage
    entrypoints: EntrypointCoverage
    records: RecordCoverage


@dataclass(frozen=True, slots=True)
class GraphContractMetadata:
    version: Literal["hades.graph_artifact.v2"]
    artifact_graph_version: str
    projection_state: Literal["queued"]
    completeness: Completeness
    coverage: Coverage


@dataclass(frozen=True, slots=True)
class CountKnowledge:
    represented: int
    value: int | None
    knowledge: Knowledge
    reason: ReasonCode | None


@dataclass(frozen=True, slots=True)
class RegistrationAst:
    kind: Literal["ast"]
    path: str
    structural_path: str
    ordinal: int


@dataclass(frozen=True, slots=True)
class RegistrationConfig:
    kind: Literal["config"]
    path: str
    structural_pointer: str
    ordinal: int


RegistrationOccurrence: TypeAlias = RegistrationAst | RegistrationConfig


@dataclass(frozen=True, slots=True)
class Trigger:
    kind: TriggerKind
    value: str | None


@dataclass(frozen=True, slots=True)
class MatchConstraints:
    host: str | None
    schemes: tuple[str, ...]
    condition_hash: str | None


@dataclass(frozen=True, slots=True)
class EntrypointIdentity:
    entrypoint_kind: EntrypointKind
    framework: str | None
    method_semantics: MethodSemantics
    methods: tuple[str, ...]
    public_path: str | None
    public_name: str | None
    trigger: Trigger
    match_constraints: MatchConstraints
    registration_occurrence: RegistrationOccurrence


@dataclass(frozen=True, slots=True)
class SourceDeclarationIdentity:
    variant: Literal["source_declaration"]
    workspace_binding_id: str
    language: str
    kind: NodeKind
    namespace: str | None
    qualified_name: str
    path: str


@dataclass(frozen=True, slots=True)
class FileIdentity:
    variant: Literal["file"]
    workspace_binding_id: str
    language: str | None
    kind: Literal["file"]
    path: str


@dataclass(frozen=True, slots=True)
class SourceOccurrenceIdentity:
    variant: Literal["source_occurrence"]
    workspace_binding_id: str
    language: str
    kind: NodeKind
    owner_node_id: str
    structural_path: str
    ordinal: int
    semantic_role: str


@dataclass(frozen=True, slots=True)
class AnonymousCallableIdentity:
    variant: Literal["anonymous_callable"]
    workspace_binding_id: str
    language: str
    kind: Literal["function"]
    owner_node_id: str
    structural_path: str
    ordinal: int


@dataclass(frozen=True, slots=True)
class EntrypointNodeIdentity:
    variant: Literal["entrypoint"]
    workspace_binding_id: str
    language: str
    kind: Literal["entrypoint"]
    path: str
    entrypoint_identity: EntrypointIdentity


@dataclass(frozen=True, slots=True)
class SemanticResourceIdentity:
    variant: Literal["semantic_resource"]
    workspace_binding_id: str
    language: str | None
    kind: NodeKind
    framework: str | None
    namespace: str | None
    qualified_name: str | None
    public_resource_name: str | None
    protocol: str | None
    operation: str | None


NodeIdentity: TypeAlias = (
    SourceDeclarationIdentity
    | FileIdentity
    | SourceOccurrenceIdentity
    | AnonymousCallableIdentity
    | EntrypointNodeIdentity
    | SemanticResourceIdentity
)


@dataclass(frozen=True, slots=True)
class FileSourceLocator:
    kind: Literal["file"]
    path: str


@dataclass(frozen=True, slots=True)
class AstSourceLocator:
    kind: Literal["ast"]
    path: str
    structural_path: str


@dataclass(frozen=True, slots=True)
class ConfigSourceLocator:
    kind: Literal["config"]
    path: str
    structural_pointer: str


SourceLocator: TypeAlias = FileSourceLocator | AstSourceLocator | ConfigSourceLocator


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    origin: EvidenceOrigin
    extractor: str
    source_locator: SourceLocator
    source_fingerprint: str
    inference_rule: str | None


@dataclass(frozen=True, slots=True)
class EvidenceEnvelope:
    primary: EvidenceItem
    supporting: tuple[EvidenceItem, ...]
    supporting_omitted_count: int


@dataclass(frozen=True, slots=True)
class SourceLocation:
    path: str
    start_line: int
    end_line: int


ScalarProperty: TypeAlias = JsonScalar


def _omitted_property() -> object:
    return field(default=_OMITTED_PROPERTY, metadata={"omit_omitted": True})


@dataclass(frozen=True, slots=True)
class FileProperties:
    file_sha256: str
    byte_size: int
    analysis_status: AnalysisStatus
    omission_reason: ReasonCode | None
    is_test: bool
    is_generated: bool


@dataclass(frozen=True, slots=True)
class ModuleProperties:
    package: ScalarProperty | _OmittedProperty = _omitted_property()
    module_system: ScalarProperty | _OmittedProperty = _omitted_property()
    is_test: bool | None = _omitted_none()
    is_generated: bool | None = _omitted_none()


@dataclass(frozen=True, slots=True)
class TypeProperties:
    visibility: ScalarProperty | _OmittedProperty = _omitted_property()
    abstract: bool | None = _omitted_none()
    final: bool | None = _omitted_none()
    modifiers: tuple[ScalarProperty, ...] | None = _omitted_none()


@dataclass(frozen=True, slots=True)
class CallableProperties:
    visibility: ScalarProperty | _OmittedProperty = _omitted_property()
    static: bool | None = _omitted_none()
    async_: bool | None = field(
        default=None,
        metadata={"omit_none": True, "wire_name": "async"},
    )
    parameter_count: int | None = _omitted_none()
    return_type: ScalarProperty | _OmittedProperty = _omitted_property()
    modifiers: tuple[ScalarProperty, ...] | None = _omitted_none()


@dataclass(frozen=True, slots=True)
class ControlProperties:
    control_kind: ScalarProperty | _OmittedProperty = _omitted_property()
    ordinal: int | None = _omitted_none()


@dataclass(frozen=True, slots=True)
class FrameworkProperties:
    framework_role: ScalarProperty | _OmittedProperty = _omitted_property()
    pipeline_order: int | None = _omitted_none()
    boundary_name: ScalarProperty | _OmittedProperty = _omitted_property()


@dataclass(frozen=True, slots=True)
class DataProperties:
    operation: ScalarProperty | _OmittedProperty = _omitted_property()
    public_resource_name: ScalarProperty | _OmittedProperty = _omitted_property()
    query_kind: ScalarProperty | _OmittedProperty = _omitted_property()


@dataclass(frozen=True, slots=True)
class IntegrationProperties:
    protocol: ScalarProperty | _OmittedProperty = _omitted_property()
    operation: ScalarProperty | _OmittedProperty = _omitted_property()
    destination_kind: ScalarProperty | _OmittedProperty = _omitted_property()


@dataclass(frozen=True, slots=True)
class TerminalProperties:
    status_code: int | None = _omitted_none()
    exception_type: ScalarProperty | _OmittedProperty = _omitted_property()
    terminal_kind: ScalarProperty | _OmittedProperty = _omitted_property()


@dataclass(frozen=True, slots=True)
class AsyncProperties:
    channel_kind: ScalarProperty | _OmittedProperty = _omitted_property()
    public_name: ScalarProperty | _OmittedProperty = _omitted_property()
    schedule: ScalarProperty | _OmittedProperty = _omitted_property()


@dataclass(frozen=True, slots=True)
class TestProperties:
    test_framework: ScalarProperty | _OmittedProperty = _omitted_property()
    case_count: int | None = _omitted_none()


@dataclass(frozen=True, slots=True)
class BoundaryProperties:
    reason_code: ScalarProperty | _OmittedProperty = _omitted_property()


NodeProperties: TypeAlias = (
    FileProperties
    | ModuleProperties
    | TypeProperties
    | CallableProperties
    | ControlProperties
    | FrameworkProperties
    | DataProperties
    | IntegrationProperties
    | TerminalProperties
    | AsyncProperties
    | TestProperties
    | BoundaryProperties
)


@dataclass(frozen=True, slots=True)
class Node:
    id: str
    identity: NodeIdentity
    kind: NodeKind
    language: str | None
    framework: str | None
    name: str
    qualified_name: str | None
    namespace: str | None
    uncertainty_id: str | None
    location: SourceLocation | None
    properties: NodeProperties
    evidence: EvidenceEnvelope


@dataclass(frozen=True, slots=True)
class Entrypoint:
    id: str
    entrypoint_kind: EntrypointKind
    label: str
    framework: str | None
    method_semantics: MethodSemantics
    methods: tuple[str, ...]
    public_path: str | None
    public_name: str | None
    handler_node_id: str | None
    uncertainty_id: str | None
    trigger: Trigger
    match_constraints: MatchConstraints
    registration_occurrence: RegistrationOccurrence
    evidence: EvidenceEnvelope


@dataclass(frozen=True, slots=True)
class Structure:
    id: str
    kind: StructureKind
    owner_node_id: str
    structural_path: str
    ordinal: int
    subtype: StructureSubtype
    continuation_node_id: str | None
    parent_structure_id: str | None
    evidence: EvidenceEnvelope


@dataclass(frozen=True, slots=True)
class Condition:
    kind: Literal["predicate"]
    normalized: str
    hash: str
    polarity: ConditionPolarity


@dataclass(frozen=True, slots=True)
class EdgeAstOccurrence:
    kind: Literal["ast"]
    owner_node_id: str
    ast_path: str
    ordinal: int


@dataclass(frozen=True, slots=True)
class EdgeConfigOccurrence:
    kind: Literal["config"]
    owner_node_id: str
    path: str
    structural_pointer: str
    ordinal: int


EdgeOccurrence: TypeAlias = EdgeAstOccurrence | EdgeConfigOccurrence


@dataclass(frozen=True, slots=True)
class EdgeLocation:
    path: str
    line: int
    ordinal: int


@dataclass(frozen=True, slots=True)
class Edge:
    id: str
    source_id: str
    target_id: str
    relation: Relation
    flow: EdgeFlow | None
    condition: Condition | None
    branch_group_id: str | None
    call_site_id: str | None
    exception_scope_id: str | None
    order: int | None
    uncertainty_id: str | None
    occurrence: EdgeOccurrence
    evidence: EvidenceEnvelope
    location: EdgeLocation | None


@dataclass(frozen=True, slots=True)
class StageCounts:
    entry: CountKnowledge | None = _omitted_none()
    routing: CountKnowledge | None = _omitted_none()
    middleware: CountKnowledge | None = _omitted_none()
    security: CountKnowledge | None = _omitted_none()
    input: CountKnowledge | None = _omitted_none()
    handler: CountKnowledge | None = _omitted_none()
    domain: CountKnowledge | None = _omitted_none()
    data: CountKnowledge | None = _omitted_none()
    integration: CountKnowledge | None = _omitted_none()
    async_: CountKnowledge | None = field(
        default=None,
        metadata={"omit_none": True, "wire_name": "async"},
    )
    response: CountKnowledge | None = _omitted_none()
    error: CountKnowledge | None = _omitted_none()


@dataclass(frozen=True, slots=True)
class Flow:
    id: str
    entrypoint_id: str
    root_node_id: str
    kind: FlowKind
    represented_step_count: int
    terminal_count: CountKnowledge
    linked_async_flow_count: CountKnowledge
    stage_counts: StageCounts
    completeness: FlowCompleteness
    uncertainty_count: CountKnowledge


@dataclass(frozen=True, slots=True)
class FlowStep:
    id: str
    flow_id: str
    edge_id: str
    stage_from: Stage
    stage_to: Stage
    min_depth: int
    branch_group_id: str | None
    async_context: AsyncContext
    async_child_flow_id: str | None
    async_cycle: bool
    backbone_role: BackboneRole
    order_key: str


@dataclass(frozen=True, slots=True)
class CallSiteSubject:
    call_site_id: str


@dataclass(frozen=True, slots=True)
class EdgeSubject:
    edge_id: str


UncertaintySubject: TypeAlias = CallSiteSubject | EdgeSubject


@dataclass(frozen=True, slots=True)
class SourceRef:
    path: str
    line: int


@dataclass(frozen=True, slots=True)
class Uncertainty:
    id: str
    domain: Literal["graph"]
    subject: UncertaintySubject
    resolution_kind: ResolutionKind
    reason_code: ReasonCode
    question: str
    evidence_requirements: tuple[str, ...]
    source_refs: tuple[SourceRef, ...]
    candidate_target_node_ids: tuple[str, ...]
    candidate_edge_ids: tuple[str, ...]
    candidate_set_knowledge: CandidateSetKnowledge
    priority: Priority
    impact: str
    fingerprint: str


@dataclass(frozen=True, slots=True)
class GraphArtifactV2:
    schema: Literal["hades.code_graph.v2"]
    generated_at: str
    project: ProjectIdentity
    source: SourceIdentity
    graph_contract: GraphContractMetadata
    frameworks: tuple[FrameworkRecord, ...]
    languages: tuple[LanguageRecord, ...]
    entrypoints: tuple[Entrypoint, ...]
    nodes: tuple[Node, ...]
    structures: tuple[Structure, ...]
    edges: tuple[Edge, ...]
    flows: tuple[Flow, ...]
    flow_steps: tuple[FlowStep, ...]
    uncertainties: tuple[Uncertainty, ...]


_PROPERTY_MODELS: dict[str, type[NodeProperties]] = {
    "file": FileProperties,
    "module": ModuleProperties,
    "namespace": ModuleProperties,
    "class": TypeProperties,
    "interface": TypeProperties,
    "trait": TypeProperties,
    "enum": TypeProperties,
    "function": CallableProperties,
    "method": CallableProperties,
    "controller": CallableProperties,
    "service": CallableProperties,
    "domain": CallableProperties,
    "repository": CallableProperties,
    "basic_block": ControlProperties,
    "branch": ControlProperties,
    "merge": ControlProperties,
    "loop": ControlProperties,
    "middleware": FrameworkProperties,
    "guard": FrameworkProperties,
    "authorization": FrameworkProperties,
    "validator": FrameworkProperties,
    "binding": FrameworkProperties,
    "framework_boundary": FrameworkProperties,
    "model": DataProperties,
    "table": DataProperties,
    "query": DataProperties,
    "cache": DataProperties,
    "storage": DataProperties,
    "integration": IntegrationProperties,
    "external_boundary": IntegrationProperties,
    "response": TerminalProperties,
    "redirect": TerminalProperties,
    "abort": TerminalProperties,
    "exception": TerminalProperties,
    "exit": TerminalProperties,
    "event": AsyncProperties,
    "listener": AsyncProperties,
    "job": AsyncProperties,
    "queue": AsyncProperties,
    "async_boundary": AsyncProperties,
    "test": TestProperties,
    "entrypoint": BoundaryProperties,
    "unknown_boundary": BoundaryProperties,
}


@lru_cache(maxsize=None)
def _hints(model_type: type[object]) -> dict[str, object]:
    return get_type_hints(model_type)


def _wire_name(model_field: object) -> str:
    metadata = getattr(model_field, "metadata")
    return cast(str, metadata.get("wire_name", getattr(model_field, "name")))


def _literal_matches(model_type: type[object], value: Mapping[str, JsonValue]) -> bool:
    hints = _hints(model_type)
    for discriminator in ("variant", "kind"):
        annotation = hints.get(discriminator)
        if get_origin(annotation) is Literal:
            return value.get(discriminator) in get_args(annotation)
    return False


def _decode_union(annotation: object, value: JsonValue) -> object:
    members = get_args(annotation)
    if value is None and type(None) in members:
        return None
    non_null = tuple(member for member in members if member is not type(None))
    if isinstance(value, dict):
        dataclass_members = tuple(
            member
            for member in non_null
            if isinstance(member, type) and is_dataclass(member)
        )
        for member in dataclass_members:
            if _literal_matches(member, value):
                return _decode_dataclass(member, value)
        if set(value) == {"call_site_id"} and CallSiteSubject in dataclass_members:
            return _decode_dataclass(CallSiteSubject, value)
        if set(value) == {"edge_id"} and EdgeSubject in dataclass_members:
            return _decode_dataclass(EdgeSubject, value)
    last_error: (TypeError | ValueError) | None = None
    for member in non_null:
        try:
            return _decode_value(member, value)
        except (TypeError, ValueError) as exc:
            last_error = exc
    raise TypeError("contract value does not match its closed union") from last_error


def _decode_value(annotation: object, value: JsonValue) -> object:
    origin = get_origin(annotation)
    if origin in {Union, types.UnionType}:
        return _decode_union(annotation, value)
    if origin is Literal:
        if value not in get_args(annotation):
            raise ValueError("contract literal is not recognized")
        return value
    if origin is tuple:
        if not isinstance(value, list):
            raise TypeError("contract array is required")
        item_type = get_args(annotation)[0]
        return tuple(_decode_value(item_type, item) for item in value)
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        if not isinstance(value, str):
            raise TypeError("contract enum must be a string")
        return annotation(value)
    if isinstance(annotation, type) and is_dataclass(annotation):
        if not isinstance(value, dict):
            raise TypeError("contract object is required")
        return _decode_dataclass(annotation, value)
    if annotation in {str, int, bool}:
        if type(value) is not annotation:
            raise TypeError("contract scalar has the wrong type")
        return value
    if annotation is JsonScalar:
        if value is not None and type(value) not in {str, int, bool}:
            raise TypeError("node property must be a contract scalar")
        return value
    if annotation is object:
        return value
    raise TypeError("unsupported closed model annotation")


def _decode_dataclass(
    model_type: type[object],
    value: Mapping[str, JsonValue],
) -> object:
    hints = _hints(model_type)
    kwargs: dict[str, object] = {}
    for model_field in fields(model_type):
        wire_name = _wire_name(model_field)
        if wire_name not in value:
            if (
                model_field.default is not MISSING
                or model_field.default_factory is not MISSING
            ):
                continue
            raise TypeError("contract object is missing a required field")
        raw = value[wire_name]
        if model_type is Node and model_field.name == "properties":
            kind = value["kind"]
            if not isinstance(kind, str):
                raise TypeError("node kind must be a string")
            kwargs[model_field.name] = _decode_dataclass(
                _PROPERTY_MODELS[kind],
                cast(Mapping[str, JsonValue], raw),
            )
        else:
            kwargs[model_field.name] = _decode_value(hints[model_field.name], raw)
    return model_type(**kwargs)


def _to_payload(value: object) -> JsonValue:
    if isinstance(value, Enum):
        return cast(str, value.value)
    if value is None or type(value) in {str, int, bool}:
        return cast(JsonScalar, value)
    if isinstance(value, tuple):
        return [_to_payload(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        payload: dict[str, JsonValue] = {}
        for model_field in fields(value):
            field_value = getattr(value, model_field.name)
            if model_field.metadata.get("omit_none") and field_value is None:
                continue
            if (
                model_field.metadata.get("omit_omitted")
                and field_value is _OMITTED_PROPERTY
            ):
                continue
            payload[_wire_name(model_field)] = _to_payload(field_value)
        return payload
    raise TypeError("value is not part of the closed graph artifact model")


def artifact_from_payload(payload: Mapping[str, JsonValue]) -> GraphArtifactV2:
    """Schema-gate and decode a real JSON artifact without coercion."""

    raw = dict(payload)
    validate_schema("artifact.schema.json", raw)
    return cast(GraphArtifactV2, _decode_dataclass(GraphArtifactV2, raw))


def artifact_to_payload(artifact: GraphArtifactV2) -> dict[str, JsonValue]:
    """Encode the immutable model to its exact closed JSON payload."""

    payload = _to_payload(artifact)
    if not isinstance(payload, dict):
        raise AssertionError("artifact model did not encode to an object")
    return payload


def dataclass_wire_fields(model_type: type[object]) -> frozenset[str]:
    """Return the public wire-field inventory for schema parity tests."""

    if not is_dataclass(model_type):
        raise TypeError("wire fields are defined only for dataclass models")
    return frozenset(_wire_name(model_field) for model_field in fields(model_type))


__all__ = [
    name
    for name, value in globals().items()
    if (
        not name.startswith("_")
        and (
            isinstance(value, type)
            or name
            in {"artifact_from_payload", "artifact_to_payload", "dataclass_wire_fields"}
        )
        and not keyword.iskeyword(name)
    )
]
