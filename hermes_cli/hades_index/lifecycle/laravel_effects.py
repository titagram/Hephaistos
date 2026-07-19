"""Native Tree-sitter facts to conservative Laravel Graph-v2 effects.

Only bounded, source-free parser facts enter this producer.  In particular,
receiver ownership is resolved through exact imports/namespaces and an effect
is published only when its structural call occurrence has an emitted block.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Sequence
from urllib.parse import urlsplit
import re

from hermes_cli.hades_graph_v2.model import (
    CandidateSetKnowledge,
    EdgeFlow,
    EvidenceOrigin,
    NodeKind,
    Priority,
    Relation,
    ResolutionKind,
)
from hermes_cli.hades_index.tree_sitter_adapter import StructuralCall, SyntaxIR
from hermes_cli.hades_resource_privacy import is_sensitive_semantic_resource_component

from .model import (
    AdapterResult,
    AstLocatorIR,
    BlockEffectSource,
    BoundaryTarget,
    CoverageCapability,
    CoverageEvent,
    CoverageOutcome,
    Effect,
    EffectKind,
    EdgeFactIR,
    EdgeSubjectIR,
    ExtractionContext,
    FrameworkBoundaryDescriptor,
    IREvidence,
    SourceLocationIR,
    SourceNodeIR,
    UnresolvedFact,
    local_record_key,
)


_FACADE_PREFIX = "Illuminate\\Support\\Facades\\"
_FACADE_NAMES = frozenset({
    "DB",
    "Cache",
    "Storage",
    "Http",
    "Mail",
    "Notification",
    "Event",
    "Bus",
    "Queue",
})
_READ_METHODS = frozenset({
    "select", "selectOne", "scalar", "get", "first", "find", "value", "pluck", "count", "exists", "all"
})
_WRITE_METHODS = frozenset({
    "insert", "update", "delete", "upsert", "create", "save", "increment", "decrement", "statement"
})
_CACHE_READ = frozenset({"get", "remember", "has"})
_CACHE_WRITE = frozenset({"put", "add", "forever", "forget", "flush", "increment", "decrement"})
_STORAGE_READ = frozenset({"get", "readStream", "exists", "download", "url"})
_STORAGE_WRITE = frozenset({"put", "writeStream", "delete", "copy", "move"})
_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "send", "request"})
_MAIL_METHODS = frozenset({"send", "queue", "sendNow"})
_QUEUE_METHODS = frozenset({"push", "later", "bulk"})
_TABLE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
_PRIVATE_RE = re.compile(
    r"(?i)(?:^sk[_-]|^eyJ[A-Za-z0-9_-]{8,}|(?:api[_-]?key|access[_-]?token|"
    r"auth(?:orization)?|secret|password|bearer)(?:[_:-]|$))"
)
_CONTROL_CHARACTER_RE = re.compile(r"[\x00-\x1f\x7f]")


def _unsafe_public_components(value: str, *, separators: str) -> bool:
    return any(
        component in {".", ".."}
        or is_sensitive_semantic_resource_component(component)
        for component in re.split(separators, value)
    )


@dataclass(frozen=True, slots=True)
class _EffectCandidate:
    kind: EffectKind
    operation: str
    resource: str | None
    protocol: str | None
    target_source_node_local_key: str | None = None


def _canonical_facade(value: str) -> str | None:
    normalized = value.lstrip("\\")
    if not normalized.startswith(_FACADE_PREFIX):
        return None
    name = normalized[len(_FACADE_PREFIX) :]
    return name if name in _FACADE_NAMES else None


def _safe_cache_key(value: str | None) -> str | None:
    if value is None or _PRIVATE_RE.search(value) or _CONTROL_CHARACTER_RE.search(value):
        return None
    if value.startswith(("/", "~")) or "/" in value:
        return None
    if _unsafe_public_components(value, separators=r":"):
        return None
    return value


def _safe_storage_path(value: str | None) -> str | None:
    if (
        value is None
        or _PRIVATE_RE.search(value)
        or _CONTROL_CHARACTER_RE.search(value)
        or value.startswith(("/", "~"))
    ):
        return None
    parts = value.split("/")
    if any(part == "" for part in parts) or _unsafe_public_components(value, separators=r"/"):
        return None
    return value


def _safe_http_endpoint(value: str | None) -> str | None:
    if value is None or _PRIVATE_RE.search(value) or _CONTROL_CHARACTER_RE.search(value):
        return None
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return None
    path = parsed.path or "/"
    if _unsafe_public_components(path, separators=r"/"):
        return None
    authority = parsed.hostname.lower()
    if parsed.port is not None:
        authority = f"{authority}:{parsed.port}"
    return f"{parsed.scheme}://{authority}{path}"


def _literal(call: StructuralCall, index: int = 0) -> str | None:
    if index >= len(call.arguments):
        return None
    argument = call.arguments[index]
    return argument.value if argument.kind == "literal" else None


def _class_reference(call: StructuralCall) -> str | None:
    return next(
        (argument.value for argument in call.arguments if argument.kind == "class_reference"),
        None,
    )


def _visible_imports(item: SyntaxIR) -> dict[str, str]:
    namespace = item.namespace
    return {
        imported.alias or imported.target.rsplit("\\", 1)[-1]: imported.target
        for imported in item.imports
        if imported.namespace == namespace
    }


def _local_type_names(item: SyntaxIR) -> frozenset[str]:
    return frozenset(
        symbol.name.rsplit(".", 1)[-1]
        for symbol in item.symbols
        if symbol.kind in {"class", "interface", "trait", "enum"}
    )


def _facade_for(call: StructuralCall, item: SyntaxIR) -> str | None:
    receiver = call.receiver
    if receiver is None:
        return None
    imports = _visible_imports(item)
    local_names = _local_type_names(item)
    if receiver in _FACADE_NAMES and receiver in local_names:
        return None
    imported = imports.get(receiver)
    if imported is not None:
        return _canonical_facade(imported)
    canonical = _canonical_facade(receiver)
    if canonical is not None:
        return canonical
    if receiver in _FACADE_NAMES and receiver not in imports and receiver not in local_names:
        return receiver
    return None


def _resolve_class_reference(
    value: str | None,
    item: SyntaxIR,
    by_fqn: dict[str, SourceNodeIR],
) -> SourceNodeIR | None:
    if value is None:
        return None
    normalized = value.lstrip("\\")
    imports = _visible_imports(item)
    if "\\" in normalized:
        first, remainder = normalized.split("\\", 1)
        normalized = f"{imports[first]}\\{remainder}" if first in imports else normalized
    elif normalized in imports:
        normalized = imports[normalized]
    elif item.namespace:
        normalized = f"{item.namespace}\\{normalized}"
    return by_fqn.get(normalized)


def _known_targets(result: AdapterResult, prefix: str) -> dict[str, SourceNodeIR]:
    return {
        node.qualified_name: node
        for node in result.source_nodes
        if node.kind is NodeKind.CLASS
        and node.locator.source_location.path.startswith(prefix)
    }


def _known_models(result: AdapterResult) -> frozenset[str]:
    return frozenset(
        node.qualified_name or node.name
        for node in result.data_nodes
        if node.kind is NodeKind.MODEL and node.qualified_name is not None
    )


def _resolve_model_receiver(call: StructuralCall, item: SyntaxIR, models: frozenset[str]) -> str | None:
    receiver = call.receiver
    if call.call_form != "scoped" or receiver is None:
        return None
    imports = _visible_imports(item)
    if receiver.startswith("\\"):
        candidate = receiver.lstrip("\\")
    elif receiver in imports:
        candidate = imports[receiver]
    elif item.namespace:
        candidate = f"{item.namespace}\\{receiver}"
    else:
        return None
    return candidate if candidate in models else None


def _locator(context: ExtractionContext, item: SyntaxIR, call: StructuralCall, ordinal: int) -> AstLocatorIR:
    digest = next(row.file_sha256 for row in context.inventory_files if row.path == item.path)
    return AstLocatorIR(
        SourceLocationIR(item.path, call.line, call.line, digest), call.structural_path, ordinal
    )


def _effect_relation(kind: EffectKind) -> tuple[Relation, EdgeFlow]:
    if kind in {EffectKind.DATA_READ, EffectKind.CACHE_READ, EffectKind.STORAGE_READ}:
        return Relation.READS, EdgeFlow.ALWAYS
    if kind in {EffectKind.DATA_WRITE, EffectKind.CACHE_WRITE, EffectKind.STORAGE_WRITE}:
        return Relation.WRITES, EdgeFlow.ALWAYS
    if kind is EffectKind.EXTERNAL_CALL:
        return Relation.CALLS_EXTERNAL, EdgeFlow.ALWAYS
    if kind is EffectKind.EVENT_EMIT:
        return Relation.EMITS, EdgeFlow.ASYNC
    return Relation.DISPATCHES, EdgeFlow.ASYNC


def _candidate_for_call(
    call: StructuralCall,
    item: SyntaxIR,
    *,
    tables: dict[tuple[str, str], str],
    models: frozenset[str],
    events: dict[str, SourceNodeIR],
    jobs: dict[str, SourceNodeIR],
) -> _EffectCandidate | None:
    member = call.member
    facade = _facade_for(call, item)
    if member is None:
        return None
    if facade == "DB" and member in _READ_METHODS | _WRITE_METHODS:
        table = tables.get((call.caller, call.receiver_chain_key))
        if table is None:
            return None
        return _EffectCandidate(
            EffectKind.DATA_READ if member in _READ_METHODS else EffectKind.DATA_WRITE,
            member,
            table,
            None,
        )
    model = _resolve_model_receiver(call, item, models)
    if model is not None and member in _READ_METHODS | _WRITE_METHODS:
        return _EffectCandidate(
            EffectKind.DATA_READ if member in _READ_METHODS else EffectKind.DATA_WRITE,
            member,
            model,
            None,
        )
    if facade == "Cache" and member in _CACHE_READ | _CACHE_WRITE:
        resource = _safe_cache_key(_literal(call))
        if resource is not None:
            return _EffectCandidate(
                EffectKind.CACHE_READ if member in _CACHE_READ else EffectKind.CACHE_WRITE,
                member,
                resource,
                None,
            )
    if facade == "Storage" and member in _STORAGE_READ | _STORAGE_WRITE:
        resource = _safe_storage_path(_literal(call))
        if resource is not None:
            return _EffectCandidate(
                EffectKind.STORAGE_READ if member in _STORAGE_READ else EffectKind.STORAGE_WRITE,
                member,
                resource,
                None,
            )
    if facade == "Http" and member in _HTTP_METHODS:
        endpoint = _safe_http_endpoint(_literal(call, 1 if member == "request" else 0))
        return _EffectCandidate(EffectKind.EXTERNAL_CALL, member, endpoint or "http", "http")
    if facade in {"Mail", "Notification"} and member in _MAIL_METHODS:
        target = _class_reference(call)
        if target is not None:
            return _EffectCandidate(EffectKind.EXTERNAL_CALL, member, target, "mail")
    event_call = (facade == "Event" and member == "dispatch") or (facade is None and member == "event")
    if event_call:
        target = _resolve_class_reference(_class_reference(call), item, events)
        if target is not None:
            return _EffectCandidate(EffectKind.EVENT_EMIT, member, target.qualified_name, None, target.local_key)
    job_call = (facade == "Bus" and member == "dispatch") or (
        facade is None and call.call_form != "scoped" and member == "dispatch"
    )
    if job_call or (call.call_form == "scoped" and member == "dispatch"):
        target = _resolve_class_reference(
            _class_reference(call) if job_call else call.receiver,
            item,
            jobs,
        )
        if target is not None:
            return _EffectCandidate(EffectKind.JOB_DISPATCH, member, target.qualified_name, None, target.local_key)
    if facade == "Queue" and member in _QUEUE_METHODS:
        target = _resolve_class_reference(_class_reference(call), item, jobs)
        if target is not None:
            return _EffectCandidate(EffectKind.QUEUE_DISPATCH, member, target.qualified_name, None, target.local_key)
    return None


def _potential_kind(
    call: StructuralCall,
    item: SyntaxIR,
    models: frozenset[str],
    jobs: dict[str, SourceNodeIR],
) -> EffectKind | None:
    member = call.member
    facade = _facade_for(call, item)
    if member is None:
        return None
    if facade == "DB" or _resolve_model_receiver(call, item, models) is not None:
        if member in _READ_METHODS:
            return EffectKind.DATA_READ
        if member in _WRITE_METHODS:
            return EffectKind.DATA_WRITE
    if facade == "Cache":
        return EffectKind.CACHE_READ if member in _CACHE_READ else EffectKind.CACHE_WRITE if member in _CACHE_WRITE else None
    if facade == "Storage":
        return EffectKind.STORAGE_READ if member in _STORAGE_READ else EffectKind.STORAGE_WRITE if member in _STORAGE_WRITE else None
    if facade == "Http" and member in _HTTP_METHODS:
        return EffectKind.EXTERNAL_CALL
    if facade in {"Mail", "Notification"} and member in _MAIL_METHODS:
        return EffectKind.EXTERNAL_CALL
    if (facade == "Event" and member == "dispatch") or (facade is None and member == "event"):
        return EffectKind.EVENT_EMIT
    if (facade == "Bus" and member == "dispatch") or (
        facade is None and call.call_form != "scoped" and member == "dispatch"
    ):
        return EffectKind.JOB_DISPATCH
    if (
        call.call_form == "scoped"
        and member == "dispatch"
        and _resolve_class_reference(call.receiver, item, jobs) is not None
    ):
        return EffectKind.JOB_DISPATCH
    if facade == "Queue" and member in _QUEUE_METHODS:
        return EffectKind.QUEUE_DISPATCH
    return None


def apply_laravel_effects(context: ExtractionContext, syntax: Sequence[SyntaxIR], result: AdapterResult) -> AdapterResult:
    """Append exact Laravel effects and typed uncertainty without source fallback."""

    if not any(item.name == "laravel" and item.language == "php" for item in context.detected_frameworks):
        return result
    declarations = {
        (row.language, row.locator.source_location.path, row.qualified_name or row.name): row
        for row in result.declarations
    }
    block_by_occurrence = {
        (block.locator.source_location.path, block.locator.structural_path): block.local_key
        for block in result.blocks
    }
    effects = list(result.effects)
    edges = list(result.edge_facts)
    unresolved = list(result.unresolved_facts)
    coverage = list(result.coverage_events)
    models = _known_models(result)
    events = _known_targets(result, "app/Events/")
    jobs = _known_targets(result, "app/Jobs/")

    for item in sorted((row for row in syntax if row.language == "php"), key=lambda row: row.path):
        tables: dict[tuple[str, str], str] = {}
        for call in item.calls:
            if _facade_for(call, item) == "DB" and call.member == "table":
                table = _literal(call)
                if table is not None and _TABLE_RE.fullmatch(table):
                    tables[(call.caller, call.receiver_chain_key)] = table
        represented_data = omitted_data = represented_async = omitted_async = 0
        for ordinal, call in enumerate(item.calls):
            declaration = declarations.get(("php", item.path, call.caller))
            source_block_key = block_by_occurrence.get((item.path, call.structural_path))
            candidate = _candidate_for_call(
                call, item, tables=tables, models=models, events=events, jobs=jobs
            )
            potential = _potential_kind(call, item, models, jobs) if candidate is None else None
            kind = candidate.kind if candidate is not None else potential
            if declaration is None or source_block_key is None or kind is None:
                if potential is not None:
                    if potential in {EffectKind.EVENT_EMIT, EffectKind.JOB_DISPATCH, EffectKind.QUEUE_DISPATCH}:
                        omitted_async += 1
                    else:
                        omitted_data += 1
                continue
            locator = _locator(context, item, call, ordinal)
            if candidate is not None:
                effects.append(
                    Effect(
                        local_record_key("php", item.path, "laravel_effect", "ast", call.structural_path, ordinal),
                        BlockEffectSource(source_block_key),
                        candidate.kind,
                        candidate.operation,
                        candidate.resource,
                        candidate.protocol,
                        locator,
                        target_source_node_local_key=candidate.target_source_node_local_key,
                    )
                )
                if candidate.kind in {EffectKind.EVENT_EMIT, EffectKind.JOB_DISPATCH, EffectKind.QUEUE_DISPATCH}:
                    represented_async += 1
                else:
                    represented_data += 1
                continue
            relation, flow = _effect_relation(kind)
            edge_key = local_record_key("php", item.path, "laravel_effect_unresolved_edge", "ast", call.structural_path, ordinal)
            evidence = IREvidence(EvidenceOrigin.UNRESOLVED, "laravel.effects-v2", locator, None)
            edges.append(
                EdgeFactIR(
                    edge_key,
                    source_block_key,
                    BoundaryTarget(FrameworkBoundaryDescriptor("laravel", "async_target" if flow is EdgeFlow.ASYNC else "effect_target", None, locator, evidence)),
                    relation,
                    flow,
                    None,
                    None,
                    None,
                    None,
                    ordinal,
                    locator,
                    evidence,
                )
            )
            async_target = flow is EdgeFlow.ASYNC
            unresolved.append(
                UnresolvedFact(
                    local_record_key("php", item.path, "laravel_effect_unresolved", "ast", call.structural_path, ordinal),
                    EdgeSubjectIR(edge_key),
                    ResolutionKind.ASYNC_TARGET if async_target else ResolutionKind.EXTERNAL_TARGET,
                    CandidateSetKnowledge.NOT_APPLICABLE,
                    "dynamic_dispatch",
                    "Which verified target is reached by this recognised Laravel API call?",
                    ("inspect_static_call_arguments",),
                    (locator,),
                    (),
                    (),
                    Priority.NORMAL,
                    "The API operation is known but its target is not statically verified.",
                )
            )
            if async_target:
                omitted_async += 1
            else:
                omitted_data += 1
        if represented_data or omitted_data:
            coverage.append(CoverageEvent("php", CoverageCapability.DATA_ACCESS, CoverageOutcome.FULL if not omitted_data else CoverageOutcome.PARTIAL, None, item.path, represented_data, omitted_data))
        if represented_async or omitted_async:
            coverage.append(CoverageEvent("php", CoverageCapability.ASYNC, CoverageOutcome.FULL if not omitted_async else CoverageOutcome.PARTIAL, None, item.path, represented_async, omitted_async))
    merged = replace(
        result,
        effects=tuple(sorted(effects, key=lambda row: row.local_key)),
        edge_facts=tuple(sorted(edges, key=lambda row: row.local_key)),
        unresolved_facts=tuple(sorted(unresolved, key=lambda row: row.local_key)),
        coverage_events=tuple(sorted(coverage, key=lambda row: (row.language, row.capability.value, row.outcome.value, row.reason_code or "", row.path or ""))),
    )
    merged.validate()
    return merged


__all__ = ["apply_laravel_effects"]
