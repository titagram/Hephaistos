"""Finite, deterministic lifecycle traversal over canonical graph-v2 topology."""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, replace

from hermes_cli.hades_graph_v2 import flow_id, flow_step_id
from hermes_cli.hades_graph_v2.coverage import count_knowledge
from hermes_cli.hades_graph_v2.model import (
    AsyncContext,
    BackboneRole,
    Capabilities,
    Capability,
    CapabilityStatus,
    CompletenessStatus,
    CountKnowledge,
    Edge,
    EdgeFlow,
    Entrypoint,
    EntrypointKind,
    EvidenceOrigin,
    Flow,
    FlowCompleteness,
    FlowKind,
    FlowStep,
    Knowledge,
    Node,
    NodeKind,
    ReasonCode,
    Relation,
    Stage,
    StageCounts,
    Structure,
    Uncertainty,
)


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
_TERMINAL_OUTCOME_KINDS = frozenset({
    NodeKind.RESPONSE,
    NodeKind.REDIRECT,
    NodeKind.ABORT,
    NodeKind.EXCEPTION,
    NodeKind.EXIT,
})
_STOP_KINDS = frozenset({
    *_TERMINAL_OUTCOME_KINDS,
    NodeKind.EXTERNAL_BOUNDARY,
    NodeKind.FRAMEWORK_BOUNDARY,
    NodeKind.UNKNOWN_BOUNDARY,
})
_HANDLER_KINDS = frozenset({
    NodeKind.FUNCTION,
    NodeKind.METHOD,
    NodeKind.CONTROLLER,
    NodeKind.SERVICE,
    NodeKind.LISTENER,
    NodeKind.JOB,
})
_DOMAIN_KINDS = frozenset({
    NodeKind.FUNCTION,
    NodeKind.METHOD,
    NodeKind.CONTROLLER,
    NodeKind.SERVICE,
    NodeKind.DOMAIN,
})
_DATA_KINDS = frozenset({
    NodeKind.MODEL,
    NodeKind.REPOSITORY,
    NodeKind.TABLE,
    NodeKind.QUERY,
    NodeKind.CACHE,
    NodeKind.STORAGE,
})
_ASYNC_KINDS = frozenset({
    NodeKind.EVENT,
    NodeKind.LISTENER,
    NodeKind.JOB,
    NodeKind.QUEUE,
    NodeKind.ASYNC_BOUNDARY,
})
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
_FRONTIER_REASON_CODES = frozenset({
    ReasonCode.ENTRYPOINT_UNRESOLVED,
    ReasonCode.CALL_TARGET_UNRESOLVED,
    ReasonCode.DYNAMIC_DISPATCH,
    ReasonCode.REFLECTION_OR_GENERATED_CODE,
    ReasonCode.FRAMEWORK_CONFIG_UNRESOLVED,
    ReasonCode.EXCEPTION_TARGET_UNRESOLVED,
    ReasonCode.ASYNC_TARGET_UNRESOLVED,
    ReasonCode.EXTERNAL_BOUNDARY_UNRESOLVED,
    ReasonCode.GRAPHIFY_CANDIDATE,
})


@dataclass(frozen=True, slots=True, order=True)
class EdgeStageState:
    edge_id: str
    stage_from: Stage
    stage_to: Stage
    async_context: AsyncContext


@dataclass(frozen=True, slots=True)
class CallableSummary:
    reachable_edge_stage_states: frozenset[EdgeStageState]
    normal_exit_node_ids: frozenset[str]
    exception_exit_node_ids: frozenset[str]
    terminal_node_ids: frozenset[str]
    effect_edge_ids: frozenset[str]
    async_dispatch_edge_ids: frozenset[str]
    uncertainty_ids: frozenset[str]


@dataclass(frozen=True, slots=True)
class CanonicalTopology:
    nodes: tuple[Node, ...]
    structures: tuple[Structure, ...]
    edges: tuple[Edge, ...]
    uncertainties: tuple[Uncertainty, ...]
    capabilities: Capabilities
    normal_exits_by_callable: tuple[tuple[str, tuple[str, ...]], ...] = ()
    exception_exits_by_callable: tuple[tuple[str, tuple[str, ...]], ...] = ()


def _node_owner(node: Node) -> str:
    owner = getattr(node.identity, "owner_node_id", None)
    return owner if isinstance(owner, str) else node.id


def _stage_to(
    edge: Edge,
    stage_from: Stage,
    nodes: Mapping[str, Node],
    invocation_stages: Mapping[str, Stage],
) -> Stage:
    target_kind = nodes[edge.target_id].kind
    if edge.relation is Relation.RETURNS_TO:
        return invocation_stages[edge.call_site_id or ""]
    if edge.flow is EdgeFlow.EXCEPTION or target_kind is NodeKind.EXCEPTION:
        return Stage.ERROR
    if edge.flow is EdgeFlow.ASYNC:
        return Stage.ASYNC
    if edge.relation is Relation.ENTERS:
        return Stage.ROUTING
    if target_kind is NodeKind.MIDDLEWARE:
        return Stage.MIDDLEWARE
    if target_kind in {NodeKind.GUARD, NodeKind.AUTHORIZATION}:
        return Stage.SECURITY
    if target_kind in {NodeKind.BINDING, NodeKind.VALIDATOR}:
        return Stage.INPUT
    if edge.relation is Relation.ROUTES_TO and target_kind in _HANDLER_KINDS:
        return Stage.HANDLER
    if target_kind in _DATA_KINDS:
        return Stage.DATA
    if target_kind in {NodeKind.INTEGRATION, NodeKind.EXTERNAL_BOUNDARY}:
        return Stage.INTEGRATION
    if target_kind in _ASYNC_KINDS:
        return Stage.ASYNC
    if target_kind in {
        NodeKind.RESPONSE,
        NodeKind.REDIRECT,
        NodeKind.ABORT,
        NodeKind.EXIT,
    }:
        return Stage.RESPONSE
    if target_kind in _DOMAIN_KINDS:
        if _STAGE_ORDER.index(stage_from) < _STAGE_ORDER.index(Stage.HANDLER):
            return Stage.HANDLER
        return Stage.DOMAIN
    return stage_from


def _strong_components(
    adjacency: Mapping[str, set[str]],
) -> tuple[tuple[str, ...], ...]:
    """Kosaraju SCCs with iterative stacks and canonical component ordering."""

    nodes = sorted(
        set(adjacency) | {target for values in adjacency.values() for target in values}
    )
    reverse: dict[str, set[str]] = defaultdict(set)
    for source, targets in adjacency.items():
        for target in targets:
            reverse[target].add(source)
    finish: list[str] = []
    seen: set[str] = set()
    for root in nodes:
        if root in seen:
            continue
        seen.add(root)
        stack: list[tuple[str, int, tuple[str, ...]]] = [
            (root, 0, tuple(sorted(adjacency.get(root, set()))))
        ]
        while stack:
            node, index, targets = stack[-1]
            if index >= len(targets):
                finish.append(node)
                stack.pop()
                continue
            target = targets[index]
            stack[-1] = (node, index + 1, targets)
            if target not in seen:
                seen.add(target)
                stack.append((target, 0, tuple(sorted(adjacency.get(target, set())))))
    assigned: set[str] = set()
    components: list[tuple[str, ...]] = []
    for root in reversed(finish):
        if root in assigned:
            continue
        component: set[str] = {root}
        assigned.add(root)
        pending = [root]
        while pending:
            node = pending.pop()
            for source in sorted(reverse.get(node, set())):
                if source not in assigned:
                    assigned.add(source)
                    component.add(source)
                    pending.append(source)
        components.append(tuple(sorted(component)))
    return tuple(components)


def build_callable_summaries(
    graph: CanonicalTopology,
) -> Mapping[tuple[str, Stage], CallableSummary]:
    """Compute monotone summaries over the reverse-topological SCC DAG.

    The summary domain contains sets only, so recursive iteration can only grow
    and is bounded by canonical edges/nodes.  No call-stack or runtime path is
    enumerated.
    """

    nodes = {node.id: node for node in graph.nodes}
    edges_by_id = {edge.id: edge for edge in graph.edges}
    declared_normal_exits = dict(graph.normal_exits_by_callable)
    declared_exception_exits = dict(graph.exception_exits_by_callable)
    callables = sorted({
        _node_owner(node)
        for node in graph.nodes
        if _node_owner(node) in nodes
        and nodes[_node_owner(node)].kind
        in {
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
        }
    })
    direct_edges: dict[str, tuple[Edge, ...]] = {
        callable_id: tuple(
            edge
            for edge in graph.edges
            if edge.occurrence.owner_node_id == callable_id
            and edge.relation not in _STRUCTURAL_RELATIONS
        )
        for callable_id in callables
    }
    summary_outgoing: dict[str, list[Edge]] = defaultdict(list)
    summary_returns_by_call_site: dict[str, list[Edge]] = defaultdict(list)
    summary_exceptions_by_call_site: dict[str, list[Edge]] = defaultdict(list)
    for edge in graph.edges:
        if edge.relation in _STRUCTURAL_RELATIONS:
            continue
        if edge.relation is Relation.RETURNS_TO and edge.call_site_id is not None:
            summary_returns_by_call_site[edge.call_site_id].append(edge)
        elif edge.relation is Relation.THROWS_TO and edge.call_site_id is not None:
            summary_exceptions_by_call_site[edge.call_site_id].append(edge)
        else:
            summary_outgoing[edge.source_id].append(edge)
    for values in summary_outgoing.values():
        values.sort(key=lambda item: item.id)
    call_graph: dict[str, set[str]] = {item: set() for item in callables}
    for callable_id, edges in direct_edges.items():
        call_graph[callable_id].update(
            edge.target_id
            for edge in edges
            if edge.target_id in call_graph
            and edge.target_id != callable_id
            and edge.relation not in {Relation.RETURNS_TO, Relation.THROWS_TO}
            and edge.uncertainty_id is None
        )
    components = _strong_components(call_graph)
    component_by_node = {
        node: index for index, component in enumerate(components) for node in component
    }
    condensation: dict[int, set[int]] = defaultdict(set)
    for source, targets in call_graph.items():
        for target in targets:
            source_component = component_by_node[source]
            target_component = component_by_node[target]
            if source_component != target_component:
                condensation[source_component].add(target_component)

    # DFS postorder gives callees before callers (reverse topological order).
    component_order: list[int] = []
    seen_components: set[int] = set()
    for root in range(len(components)):
        if root in seen_components:
            continue
        stack = [(root, False)]
        while stack:
            component, expanded = stack.pop()
            if expanded:
                component_order.append(component)
                continue
            if component in seen_components:
                continue
            seen_components.add(component)
            stack.append((component, True))
            stack.extend(
                (target, False)
                for target in sorted(condensation.get(component, set()), reverse=True)
                if target not in seen_components
            )

    empty = CallableSummary(
        frozenset(),
        frozenset(),
        frozenset(),
        frozenset(),
        frozenset(),
        frozenset(),
        frozenset(),
    )
    summaries: dict[tuple[str, Stage], CallableSummary] = {
        (callable_id, stage): empty for callable_id in callables for stage in Stage
    }

    def union(left: CallableSummary, right: CallableSummary) -> CallableSummary:
        return CallableSummary(
            *(
                getattr(left, item.name) | getattr(right, item.name)
                for item in fields(CallableSummary)
            )
        )

    def direct_summary(callable_id: str, stage: Stage) -> CallableSummary:
        invocation_stages: dict[str, Stage] = {}
        states: set[EdgeStageState] = set()
        normal: set[str] = {
            edge.source_id
            for edge in graph.edges
            if edge.relation is Relation.RETURNS_TO
            and _node_owner(nodes[edge.source_id]) == callable_id
        }
        normal.update(declared_normal_exits.get(callable_id, ()))
        exceptional: set[str] = {
            edge.source_id
            for edge in graph.edges
            if edge.relation is Relation.THROWS_TO
            and _node_owner(nodes[edge.source_id]) == callable_id
        }
        exceptional.update(declared_exception_exits.get(callable_id, ()))
        terminals: set[str] = set()
        effects: set[str] = set()
        async_edges: set[str] = set()
        uncertainty_ids: set[str] = set()
        aggregate = empty
        pending = deque([(callable_id, stage)])
        visited: set[tuple[str, Stage]] = set()

        def remember(edge: Edge, stage_from: Stage, stage_to: Stage) -> None:
            states.add(
                EdgeStageState(
                    edge.id,
                    stage_from,
                    stage_to,
                    AsyncContext.SYNCHRONOUS,
                )
            )
            if edge.flow is EdgeFlow.EXCEPTION:
                exceptional.add(edge.target_id)
            if nodes[edge.target_id].kind in _TERMINAL_OUTCOME_KINDS:
                terminals.add(edge.target_id)
            if edge.relation in {
                Relation.READS,
                Relation.WRITES,
                Relation.QUERIES,
                Relation.CALLS_EXTERNAL,
            }:
                effects.add(edge.id)
            if edge.flow is EdgeFlow.ASYNC:
                async_edges.add(edge.id)
            if edge.uncertainty_id is not None:
                uncertainty_ids.add(edge.uncertainty_id)

        def exit_stages(
            summary: CallableSummary,
            exit_node_id: str,
            fallback: Stage,
        ) -> tuple[Stage, ...]:
            values = {
                state.stage_to
                for state in summary.reachable_edge_stage_states
                if edges_by_id[state.edge_id].target_id == exit_node_id
            }
            values.update(
                state.stage_from
                for state in summary.reachable_edge_stage_states
                if edges_by_id[state.edge_id].source_id == exit_node_id
            )
            return tuple(sorted(values or {fallback}, key=_STAGE_ORDER.index))

        while pending:
            node_id_value, stage_from = pending.popleft()
            if (node_id_value, stage_from) in visited:
                continue
            visited.add((node_id_value, stage_from))
            source_owner_id = _node_owner(nodes[node_id_value])
            for edge in summary_outgoing.get(node_id_value, ()):
                if edge.occurrence.owner_node_id not in {
                    callable_id,
                    source_owner_id,
                }:
                    continue
                stage_to = _stage_to(edge, stage_from, nodes, invocation_stages)
                remember(edge, stage_from, stage_to)
                if edge.relation is Relation.INVOKES and edge.call_site_id is not None:
                    invocation_stages.setdefault(edge.call_site_id, stage_from)
                    if edge.uncertainty_id is None and edge.target_id in call_graph:
                        callee_summary = summaries[(edge.target_id, stage_to)]
                        aggregate = union(
                            aggregate,
                            callee_summary,
                        )
                        if callee_summary.normal_exit_node_ids:
                            for return_edge in sorted(
                                summary_returns_by_call_site.get(edge.call_site_id, ()),
                                key=lambda item: item.id,
                            ):
                                for exit_stage in exit_stages(
                                    callee_summary,
                                    return_edge.source_id,
                                    stage_to,
                                ):
                                    remember(return_edge, exit_stage, stage_from)
                                normal.add(return_edge.target_id)
                                pending.append((return_edge.target_id, stage_from))
                        if callee_summary.exception_exit_node_ids:
                            for exception_edge in sorted(
                                summary_exceptions_by_call_site.get(
                                    edge.call_site_id, ()
                                ),
                                key=lambda item: item.id,
                            ):
                                for exit_stage in exit_stages(
                                    callee_summary,
                                    exception_edge.source_id,
                                    Stage.ERROR,
                                ):
                                    remember(exception_edge, exit_stage, Stage.ERROR)
                                exceptional.add(exception_edge.source_id)
                                pending.append((exception_edge.target_id, Stage.ERROR))
                    continue
                if (
                    edge.uncertainty_id is None
                    and edge.target_id in call_graph
                    and edge.target_id != callable_id
                ):
                    aggregate = union(aggregate, summaries[(edge.target_id, stage_to)])
                if (
                    edge.flow is EdgeFlow.ASYNC
                    or edge.uncertainty_id is not None
                    or nodes[edge.target_id].kind in _STOP_KINDS
                ):
                    continue
                pending.append((edge.target_id, stage_to))
        direct = CallableSummary(
            frozenset(states),
            frozenset(normal),
            frozenset(exceptional),
            frozenset(terminals),
            frozenset(effects),
            frozenset(async_edges),
            frozenset(uncertainty_ids),
        )
        return union(direct, aggregate)

    for component_index in component_order:
        component = components[component_index]
        changed = True
        while changed:
            changed = False
            for callable_id in component:
                for stage in Stage:
                    updated = direct_summary(callable_id, stage)
                    key = (callable_id, stage)
                    if updated != summaries[key]:
                        summaries[key] = updated
                        changed = True
    return summaries


def _count(represented: int, capabilities: Sequence[Capability]) -> CountKnowledge:
    partial = tuple(
        capability
        for capability in capabilities
        if capability.status in {CapabilityStatus.PARTIAL, CapabilityStatus.UNSUPPORTED}
    )
    reasons = tuple(
        reason.code for capability in partial for reason in capability.reasons
    )
    return count_knowledge(
        represented,
        0,
        CapabilityStatus.PARTIAL if partial else CapabilityStatus.FULL,
        reasons,
    )


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


def _flow_capabilities(
    capabilities: Capabilities,
    frontier_reasons: Counter[ReasonCode],
) -> Capabilities:
    values: dict[str, Capability] = {}
    for name in _CAPABILITY_FIELDS:
        capability = getattr(capabilities, name)
        reasons = tuple(
            (
                replace(reason, count=frontier_reasons[reason.code])
                if reason.code in _FRONTIER_REASON_CODES
                else reason
            )
            for reason in capability.reasons
            if reason.code not in _FRONTIER_REASON_CODES
            or frontier_reasons[reason.code]
        )
        status = capability.status
        if not reasons and status in {
            CapabilityStatus.PARTIAL,
            CapabilityStatus.UNSUPPORTED,
        }:
            status = CapabilityStatus.FULL
        values[name] = Capability(status, reasons)
    return Capabilities(**values)


def _recursive_invocations(
    steps: Sequence[FlowStep], edges: Mapping[str, Edge]
) -> set[str]:
    invocation_edges = [
        edges[step.edge_id]
        for step in steps
        if edges[step.edge_id].relation is Relation.INVOKES
    ]
    adjacency: dict[str, set[str]] = defaultdict(set)
    for edge in invocation_edges:
        adjacency[edge.occurrence.owner_node_id].add(edge.target_id)
    recursive: set[str] = set()
    components = _strong_components(adjacency)
    component_by_node = {
        node: component for component in components for node in component
    }
    for edge in invocation_edges:
        source_callable = edge.occurrence.owner_node_id
        component = component_by_node.get(source_callable, ())
        if edge.target_id in component and (
            source_callable == edge.target_id or len(component) > 1
        ):
            recursive.add(edge.id)
    return recursive


def _assign_backbone_roles(
    steps: Sequence[FlowStep],
    edges: Mapping[str, Edge],
    nodes: Mapping[str, Node],
    root_node_id: str,
) -> tuple[FlowStep, ...]:
    non_async = [
        step for step in steps if edges[step.edge_id].flow is not EdgeFlow.ASYNC
    ]
    root_state = (root_node_id, Stage.ENTRY)
    reachable_states = {root_state}
    predecessors: dict[tuple[str, Stage], list[tuple[tuple[str, Stage], str]]] = (
        defaultdict(list)
    )
    terminal_states: set[tuple[str, Stage]] = set()
    for step in non_async:
        edge = edges[step.edge_id]
        source = (edge.source_id, step.stage_from)
        target = (edge.target_id, step.stage_to)
        reachable_states.add(target)
        predecessors[target].append((source, step.id))
        if edge.uncertainty_id is not None or nodes[edge.target_id].kind in _STOP_KINDS:
            terminal_states.add(target)
    step_bits = {
        step.id: 1 << ordinal
        for ordinal, step in enumerate(sorted(non_async, key=lambda item: item.id))
    }
    all_bits = (1 << len(step_bits)) - 1
    dominators = {
        state: (0 if state == root_state else all_bits) for state in reachable_states
    }
    changed = True
    while changed:
        changed = False
        for state in sorted(
            reachable_states, key=lambda item: (item[0], item[1].value)
        ):
            if state == root_state or not predecessors[state]:
                continue
            updated = all_bits
            for source, step_id_value in predecessors[state]:
                updated &= dominators[source] | step_bits[step_id_value]
            if updated != dominators[state]:
                dominators[state] = updated
                changed = True
    mandatory_bits = all_bits
    for state in terminal_states:
        mandatory_bits &= dominators[state]
    mandatory = {
        step_id_value
        for step_id_value, bit in step_bits.items()
        if terminal_states and mandatory_bits & bit
    }
    recursive = _recursive_invocations(steps, edges)
    result: list[FlowStep] = []
    for step in steps:
        edge = edges[step.edge_id]
        if edge.flow is EdgeFlow.ASYNC:
            role = BackboneRole.ASYNC
        elif edge.flow is EdgeFlow.EXCEPTION:
            role = BackboneRole.EXCEPTION
        elif edge.flow is EdgeFlow.LOOP or edge.id in recursive:
            role = BackboneRole.LOOP
        elif edge.flow is EdgeFlow.ALWAYS and step.id in mandatory:
            role = BackboneRole.MANDATORY
        else:
            role = BackboneRole.BRANCH
        result.append(replace(step, backbone_role=role))
    return tuple(result)


def build_lifecycle_flows(
    graph: CanonicalTopology,
    entrypoints: Sequence[Entrypoint],
    summaries: Mapping[tuple[str, Stage], CallableSummary] | None = None,
) -> tuple[tuple[Flow, ...], tuple[FlowStep, ...]]:
    """Traverse every verified edge-stage state once with no arbitrary limit."""

    nodes = {node.id: node for node in graph.nodes}
    edges = {edge.id: edge for edge in graph.edges}
    uncertainties = {item.id: item for item in graph.uncertainties}
    callable_summaries = summaries or build_callable_summaries(graph)
    uncertain_call_sites = {
        edge.call_site_id
        for edge in graph.edges
        if edge.relation is Relation.INVOKES
        and edge.call_site_id is not None
        and edge.uncertainty_id is not None
    }
    mutable_outgoing: dict[str, list[Edge]] = defaultdict(list)
    for edge in graph.edges:
        if edge.relation not in _STRUCTURAL_RELATIONS:
            mutable_outgoing[edge.source_id].append(edge)
    outgoing = {
        source: tuple(sorted(values, key=lambda item: item.id))
        for source, values in mutable_outgoing.items()
    }
    materialized: dict[str, tuple[Flow, tuple[FlowStep, ...]]] = {}
    building: set[str] = set()

    def build_one(
        entrypoint: Entrypoint,
        root_node_id: str,
        kind: FlowKind,
        ancestors: tuple[str, ...],
    ) -> str:
        public_flow_id = flow_id(entrypoint.id, root_node_id, kind.value)
        if public_flow_id in materialized or public_flow_id in building:
            return public_flow_id
        building.add(public_flow_id)
        context = (
            AsyncContext.LINKED_ASYNC
            if kind is FlowKind.ASYNC_FLOW
            else AsyncContext.SYNCHRONOUS
        )
        root_state = (root_node_id, Stage.ENTRY, context)
        root_summary = callable_summaries.get((root_node_id, Stage.ENTRY))
        admitted_states = (
            {
                (item.edge_id, item.stage_from, item.stage_to)
                for item in root_summary.reachable_edge_stage_states
            }
            if root_summary is not None
            else set()
        )
        depths: dict[tuple[str, Stage, AsyncContext], int] = {root_state: 0}
        invocation_stages: dict[str, Stage] = {}
        returnable_call_sites: set[str] = set()
        throwable_call_sites: set[str] = set()
        expanded_generation: dict[tuple[str, Stage, AsyncContext], int] = {}
        queue = deque([root_state])
        step_values: dict[
            tuple[str, Stage, Stage, AsyncContext], dict[str, object]
        ] = {}
        while queue:
            state = queue.popleft()
            generation = len(invocation_stages)
            if expanded_generation.get(state, -1) >= generation:
                continue
            expanded_generation[state] = generation
            source_id, stage_from, async_context = state
            source_depth = depths[state]
            for edge in outgoing.get(source_id, ()):
                if edge.call_site_id is not None:
                    if edge.relation is Relation.RETURNS_TO and (
                        edge.call_site_id not in invocation_stages
                        or edge.call_site_id in uncertain_call_sites
                        or edge.call_site_id not in returnable_call_sites
                    ):
                        continue
                    if edge.relation is Relation.THROWS_TO and (
                        edge.call_site_id not in invocation_stages
                        or edge.call_site_id in uncertain_call_sites
                        or edge.call_site_id not in throwable_call_sites
                    ):
                        continue
                if edge.uncertainty_id is not None:
                    uncertainty = uncertainties[edge.uncertainty_id]
                    if uncertainty.candidate_set_knowledge.value != "complete":
                        allowed = getattr(
                            uncertainty.subject, "edge_id", None
                        ) == edge.id or (
                            getattr(uncertainty.subject, "call_site_id", None)
                            == edge.call_site_id
                            and edge.evidence.primary.origin
                            is EvidenceOrigin.UNRESOLVED
                        )
                        if not allowed:
                            continue
                stage_to = _stage_to(edge, stage_from, nodes, invocation_stages)
                if (edge.id, stage_from, stage_to) not in admitted_states:
                    continue
                if edge.relation is Relation.INVOKES and edge.call_site_id is not None:
                    previous = invocation_stages.get(edge.call_site_id)
                    if previous is not None and previous is not stage_from:
                        # A call-site occurrence is serialized once per flow.
                        # Recursive fixed points can revisit its source node at
                        # a later stage, but that is not a second invocation.
                        continue
                    if previous is None:
                        invocation_stages[edge.call_site_id] = stage_from
                        # Demand the callee summary for this exact input stage.
                        # Return edges remain call-site scoped below; this lookup
                        # deliberately never enables a companion uncertain return.
                        callee_summary = callable_summaries.get((
                            edge.target_id,
                            stage_to,
                        ))
                        if (
                            edge.uncertainty_id is None
                            and callee_summary is not None
                            and callee_summary.normal_exit_node_ids
                        ):
                            returnable_call_sites.add(edge.call_site_id)
                        if (
                            edge.uncertainty_id is None
                            and callee_summary is not None
                            and callee_summary.exception_exit_node_ids
                        ):
                            throwable_call_sites.add(edge.call_site_id)
                        queue.extend(
                            sorted(
                                depths,
                                key=lambda item: (
                                    item[0],
                                    item[1].value,
                                    item[2].value,
                                ),
                            )
                        )
                key = (edge.id, stage_from, stage_to, async_context)
                child_id: str | None = None
                async_cycle = False
                if edge.flow is EdgeFlow.ASYNC and edge.uncertainty_id is None:
                    child_id = flow_id(
                        entrypoint.id, edge.target_id, FlowKind.ASYNC_FLOW.value
                    )
                    async_cycle = child_id in (*ancestors, public_flow_id)
                    if not async_cycle:
                        build_one(
                            entrypoint,
                            edge.target_id,
                            FlowKind.ASYNC_FLOW,
                            (*ancestors, public_flow_id),
                        )
                current = step_values.get(key)
                if current is None or source_depth < current["min_depth"]:
                    step_values[key] = {
                        "edge": edge,
                        "stage_from": stage_from,
                        "stage_to": stage_to,
                        "context": async_context,
                        "min_depth": source_depth,
                        "child_id": child_id,
                        "async_cycle": async_cycle,
                    }
                stop = (
                    edge.flow is EdgeFlow.ASYNC
                    or edge.uncertainty_id is not None
                    or nodes[edge.target_id].kind in _STOP_KINDS
                )
                if stop:
                    continue
                target_state = (edge.target_id, stage_to, async_context)
                target_depth = source_depth + 1
                if target_depth < depths.get(target_state, target_depth + 1):
                    depths[target_state] = target_depth
                    queue.append(target_state)

        provisional: list[FlowStep] = []
        for value in step_values.values():
            edge = value["edge"]
            stage_from = value["stage_from"]
            stage_to = value["stage_to"]
            async_context = value["context"]
            min_depth = value["min_depth"]
            step_id_value = flow_step_id(
                public_flow_id,
                edge.id,
                stage_from.value,
                stage_to.value,
                async_context.value,
            )
            provisional.append(
                FlowStep(
                    step_id_value,
                    public_flow_id,
                    edge.id,
                    stage_from,
                    stage_to,
                    min_depth,
                    edge.branch_group_id,
                    async_context,
                    value["child_id"],
                    value["async_cycle"],
                    BackboneRole.BRANCH,
                    f"{_STAGE_ORDER.index(stage_from):02d}:{min_depth:06d}:"
                    f"{edge.source_id}:{edge.target_id}:{edge.id}",
                )
            )
        steps = _assign_backbone_roles(provisional, edges, nodes, root_node_id)
        uncertainty_ids = {
            edges[step.edge_id].uncertainty_id
            for step in steps
            if edges[step.edge_id].uncertainty_id is not None
        }
        frontier_reasons = Counter(
            uncertainties[item].reason_code
            for item in uncertainty_ids
            if item is not None
        )
        capabilities = _flow_capabilities(graph.capabilities, frontier_reasons)
        terminal_ids = {
            edges[step.edge_id].target_id
            for step in steps
            if nodes[edges[step.edge_id].target_id].kind in _TERMINAL_OUTCOME_KINDS
        }
        linked_ids = {
            step.async_child_flow_id for step in steps if step.async_child_flow_id
        }
        stage_members: dict[Stage, set[str]] = defaultdict(set)
        stage_members[Stage.ENTRY].add(root_node_id)
        for step in steps:
            edge = edges[step.edge_id]
            stage_members[step.stage_from].add(edge.source_id)
            stage_members[step.stage_to].add(edge.target_id)
        stage_counts: dict[str, CountKnowledge | None] = {}
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
            Stage.DOMAIN: (capabilities.call_graph, capabilities.control_flow),
            Stage.DATA: (capabilities.data_access,),
            Stage.INTEGRATION: (capabilities.data_access,),
            Stage.ASYNC: (capabilities.async_,),
            Stage.RESPONSE: (capabilities.control_flow,),
            Stage.ERROR: (capabilities.exceptions,),
        }
        for stage in Stage:
            field_name = "async_" if stage is Stage.ASYNC else stage.value
            stage_counts[field_name] = (
                _count(len(stage_members[stage]), stage_capabilities[stage])
                if stage_members[stage]
                else None
            )
        flow = Flow(
            public_flow_id,
            entrypoint.id,
            root_node_id,
            kind,
            len(steps),
            _count(
                len(terminal_ids),
                (
                    capabilities.inventory,
                    capabilities.call_graph,
                    capabilities.control_flow,
                    capabilities.exceptions,
                ),
            ),
            _count(
                len(linked_ids),
                (capabilities.inventory, capabilities.call_graph, capabilities.async_),
            ),
            StageCounts(**stage_counts),
            FlowCompleteness(_status(capabilities), capabilities),
            _count(
                len(uncertainty_ids),
                tuple(getattr(capabilities, name) for name in _CAPABILITY_FIELDS),
            ),
        )
        building.remove(public_flow_id)
        materialized[public_flow_id] = (flow, tuple(steps))
        return public_flow_id

    for entrypoint in sorted(entrypoints, key=lambda item: item.id):
        build_one(
            entrypoint,
            entrypoint.id,
            (
                FlowKind.REQUEST_LIFECYCLE
                if entrypoint.entrypoint_kind is EntrypointKind.HTTP_ROUTE
                else FlowKind.EXECUTION_FLOW
            ),
            (),
        )
    flows = tuple(
        sorted((item[0] for item in materialized.values()), key=lambda item: item.id)
    )
    steps = tuple(
        sorted(
            (step for item in materialized.values() for step in item[1]),
            key=lambda item: item.id,
        )
    )
    return flows, steps


__all__ = [
    "CallableSummary",
    "CanonicalTopology",
    "EdgeStageState",
    "build_callable_summaries",
    "build_lifecycle_flows",
]
