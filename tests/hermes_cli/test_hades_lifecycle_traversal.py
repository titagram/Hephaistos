from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import replace
from pathlib import Path

import pytest

from hermes_cli.hades_graph_config import load_hades_graph_index_config
from hermes_cli.hades_graph_v2 import artifact_to_payload, validate_artifact
from hermes_cli.hades_graph_v2.model import (
    AnalysisStatus,
    AsyncContext,
    BackboneRole,
    EdgeFlow,
    EntrypointKind,
    FrameworkProperties,
    Knowledge,
    MethodSemantics,
    NodeKind,
    Relation,
    SourceIdentity,
    Stage,
    StructureKind,
    StructureSubtype,
    TriggerKind,
)
from hermes_cli.hades_index.lifecycle.model import (
    AdapterResult,
    AlwaysSuccessor,
    AsyncSuccessor,
    BranchSuccessor,
    ControlKind,
    CoverageCapability,
    CoverageEvent,
    CoverageOutcome,
    EntrypointCandidate,
    ExceptionCatchArm,
    ExtractionContext,
    FrameworkBoundaryTarget,
    FrameworkBoundaryDescriptor,
    FrameworkLocalTarget,
    FrameworkPipelineSegment,
    IRValidationError,
    InventoryFile,
    LoopSuccessor,
    MatchConstraints,
    ReturnSuccessor,
    ExceptionSuccessor,
    Terminal,
    TerminalKind,
)
from tests.hermes_cli.test_hades_lifecycle_control_flow import (
    _ast as _complex_ast,
    _evidence as _complex_evidence,
    _fixture as _control_flow_fixture,
    _key as _complex_key,
    _sort,
)
from tests.hermes_cli.test_hades_lifecycle_ir import _valid_result


def _context(tmp_path: Path) -> ExtractionContext:
    return ExtractionContext(
        workspace_root=tmp_path,
        project_id="01KXJD0SV73EBGWKNE2EK3M4KD",
        workspace_binding_id="01KXJD1BDMQ2TFABMVJV6EFE8Q",
        source_identity=SourceIdentity(None, "a" * 64, False, None),
        graph_config=load_hades_graph_index_config({}),
        detected_languages=("python",),
        detected_frameworks=(),
        composer_metadata=(),
        python_metadata=(),
        package_metadata=(),
        tsconfig_metadata=(),
        file_accessor=lambda _path: b"",
        inventory_files=(InventoryFile("src/app.py", "a" * 64, "python", True),),
        excluded_path_count=0,
    )


def _resolved_result():
    result = _valid_result()
    candidate = result.entrypoints[0]
    return replace(
        result,
        edge_facts=(),
        framework_segments=(),
        unresolved_facts=(),
        entrypoints=(
            replace(
                candidate,
                handler_local_key=result.declarations[0].local_key,
                unresolved_fact_local_key=None,
                framework_segment_keys=(),
            ),
        ),
    )


def _public_api_entrypoint(result):
    handler = next(item for item in result.declarations if item.name == "handler")
    return EntrypointCandidate(
        kind=EntrypointKind.PUBLIC_API,
        framework=None,
        method_semantics=MethodSemantics.NOT_APPLICABLE,
        methods=(),
        public_path=None,
        public_name=handler.qualified_name,
        trigger=TriggerKind.LIBRARY,
        trigger_value=handler.qualified_name or handler.name,
        match_constraints=MatchConstraints(None, (), None),
        registration_locator=handler.locator,
        handler_local_key=handler.local_key,
        unresolved_fact_local_key=None,
        framework_segment_keys=(),
        evidence=_complex_evidence(handler.locator),
    )


def _complex_result(*, unresolved: bool = False):
    result = _control_flow_fixture(
        workers=1,
        unresolved=unresolved,
        symbol_resolution_full=True,
    )
    coverage = tuple(
        sorted(
            (
                CoverageEvent(
                    "python",
                    capability,
                    (
                        CoverageOutcome.NOT_APPLICABLE
                        if capability is CoverageCapability.FRAMEWORK_LIFECYCLE
                        else CoverageOutcome.FULL
                    ),
                    None,
                    None,
                    1,
                    0,
                )
                for capability in CoverageCapability
            ),
            key=lambda item: (
                item.language,
                item.capability.value,
                item.outcome.value,
                item.reason_code or "",
                item.path or "",
            ),
        )
    )
    return replace(
        result,
        entrypoints=(_public_api_entrypoint(result),),
        coverage_events=coverage,
    )


def _segment_rich_result():
    """A validated pipeline containing local/boundary targets and short circuits."""

    result = _valid_result()
    declaration = result.declarations[0]
    terminal = result.terminals[0]
    base = result.framework_segments[0]
    authorization_key = _complex_key("framework_segment", "authorization")
    authorization = FrameworkPipelineSegment(
        local_key=authorization_key,
        framework_role="authorization",
        pipeline_order=1,
        target=FrameworkBoundaryTarget(
            FrameworkBoundaryDescriptor(
                framework="fastapi",
                role="authorization",
                public_name="requires_admin",
                locator=base.evidence.locator,
                evidence=base.evidence,
            )
        ),
        success_successor=ReturnSuccessor(terminal.local_key, 0),
        short_circuit_successors=(),
        evidence=base.evidence,
    )
    middleware = replace(
        base,
        success_successor=AlwaysSuccessor(authorization.local_key, 0),
    )
    entrypoint = replace(
        result.entrypoints[0],
        handler_local_key=declaration.local_key,
        unresolved_fact_local_key=None,
        framework_segment_keys=(middleware.local_key, authorization.local_key),
    )
    segment_rich = replace(
        result,
        edge_facts=(),
        framework_segments=(middleware, authorization),
        entrypoints=(entrypoint,),
        unresolved_facts=(),
    )
    segment_rich.validate()
    return segment_rich


def _empty_result(*coverage_events: CoverageEvent) -> AdapterResult:
    return AdapterResult(
        (), (), (), (), (), (), (), (), (), (), (), (), coverage_events, ()
    )


def _recursive_result(*, mutual: bool):
    from hermes_cli.hades_index.lifecycle.model import (
        CallSite,
        EdgeFactIR,
        LocalNodeTarget,
        StructureIR,
        TargetExpressionKind,
    )

    result = _complex_result()
    handler = next(item for item in result.declarations if item.name == "handler")
    worker = next(item for item in result.declarations if item.name == "worker")
    merge = next(
        item
        for item in result.blocks
        if item.declaration_key == handler.local_key
        and item.control_kind.value == "merge"
    )
    call_structure = next(
        item for item in result.structures if item.kind is StructureKind.CALL_SITE
    )
    first_site = replace(result.call_sites[0], continuation_block_key=merge.local_key)
    first_structure = replace(call_structure, continuation_block_key=merge.local_key)
    first_edge = replace(
        result.edge_facts[0],
        source_node_local_key=first_site.source_block_key,
        target=LocalNodeTarget(worker.local_key if mutual else handler.local_key),
    )
    if not mutual:
        recursive = replace(
            result,
            call_sites=(first_site,),
            structures=_sort(
                first_structure if item.local_key == first_structure.local_key else item
                for item in result.structures
            ),
            edge_facts=(first_edge,),
        )
        recursive.validate()
        return recursive

    worker_entry = next(
        item for item in result.blocks if item.declaration_key == worker.local_key
    )
    second_path = "declaration/worker/0/call"
    second_structure_key = _complex_key("structure", second_path)
    second_site_key = _complex_key("call_site", second_path)
    second_structure = StructureIR(
        second_structure_key,
        StructureKind.CALL_SITE,
        worker.local_key,
        second_path,
        0,
        StructureSubtype.CALL,
        worker_entry.local_key,
        None,
        _complex_evidence(_complex_ast(second_path)),
    )
    second_site = CallSite(
        second_site_key,
        worker.local_key,
        worker_entry.local_key,
        _complex_ast(second_path),
        TargetExpressionKind.DIRECT_FUNCTION,
        handler.qualified_name,
        handler.qualified_name,
        None,
        0,
        worker_entry.local_key,
        None,
    )
    second_edge = EdgeFactIR(
        _complex_key("edge_fact", second_path),
        worker_entry.local_key,
        LocalNodeTarget(handler.local_key),
        Relation.INVOKES,
        EdgeFlow.ALWAYS,
        None,
        None,
        second_structure_key,
        None,
        0,
        _complex_ast(second_path),
        _complex_evidence(_complex_ast(second_path)),
    )
    recursive = replace(
        result,
        call_sites=tuple(
            sorted((first_site, second_site), key=lambda item: item.local_key)
        ),
        structures=_sort(
            [
                first_structure if item.local_key == first_structure.local_key else item
                for item in result.structures
            ]
            + [second_structure]
        ),
        edge_facts=_sort((first_edge, second_edge)),
    )
    recursive.validate()
    return recursive


def _build(tmp_path: Path, result):
    from hermes_cli.hades_index.lifecycle.builder import GraphBuilder

    return GraphBuilder(generated_at=lambda: "2026-07-19T12:00:00Z").build(
        _context(tmp_path), (result,)
    )


def test_graph_builder_maps_valid_ir_to_a_closed_valid_v2_artifact(tmp_path):
    artifact = _build(tmp_path, _valid_result())

    assert artifact.schema == "hades.code_graph.v2"
    assert artifact.graph_contract.version == "hades.graph_artifact.v2"
    assert artifact.graph_contract.artifact_graph_version != "0" * 64
    assert len(artifact.entrypoints) == 1
    assert (
        len([flow for flow in artifact.flows if flow.kind.value != "async_flow"]) == 1
    )
    assert artifact.flows[0].represented_step_count == len(artifact.flow_steps)
    assert any(edge.relation is Relation.READS for edge in artifact.edges)
    validate_artifact(artifact)


def test_one_finite_flow_step_per_edge_stage_state_with_shortest_depth(tmp_path):
    artifact = _build(tmp_path, _complex_result())
    sync_flow = next(flow for flow in artifact.flows if flow.kind.value != "async_flow")
    steps = tuple(step for step in artifact.flow_steps if step.flow_id == sync_flow.id)
    keys = {
        (step.edge_id, step.stage_from, step.stage_to, step.async_context)
        for step in steps
    }

    assert len(keys) == len(steps)
    assert len(steps) <= len(artifact.edges) * len(Stage)
    assert min(step.min_depth for step in steps) == 0
    assert all(step.min_depth >= 0 for step in steps)
    assert any(step.backbone_role is BackboneRole.LOOP for step in steps)
    edges = {edge.id: edge for edge in artifact.edges}
    nodes = {node.id: node for node in artifact.nodes}
    outgoing = defaultdict(list)
    for step in steps:
        edge = edges[step.edge_id]
        outgoing[(edge.source_id, step.stage_from)].append(step)
    root = (sync_flow.root_node_id, Stage.ENTRY)
    depths = {root: 0}
    expected = {}
    pending = deque([root])
    stop_kinds = {
        NodeKind.RESPONSE,
        NodeKind.REDIRECT,
        NodeKind.ABORT,
        NodeKind.EXCEPTION,
        NodeKind.EXIT,
        NodeKind.EXTERNAL_BOUNDARY,
        NodeKind.FRAMEWORK_BOUNDARY,
        NodeKind.UNKNOWN_BOUNDARY,
    }
    while pending:
        state = pending.popleft()
        for step in outgoing[state]:
            expected[step.id] = min(expected.get(step.id, 10**9), depths[state])
            edge = edges[step.edge_id]
            if (
                edge.flow is EdgeFlow.ASYNC
                or edge.uncertainty_id is not None
                or nodes[edge.target_id].kind in stop_kinds
            ):
                continue
            target = (edge.target_id, step.stage_to)
            target_depth = depths[state] + 1
            if target_depth < depths.get(target, 10**9):
                depths[target] = target_depth
                pending.append(target)
    assert {step.id: step.min_depth for step in steps} == expected
    validate_artifact(artifact)


def test_branches_exceptions_and_matching_call_site_returns_are_explicit(tmp_path):
    artifact = _build(tmp_path, _complex_result())
    edges = {edge.id: edge for edge in artifact.edges}
    sync = next(flow for flow in artifact.flows if flow.kind.value != "async_flow")
    steps = [step for step in artifact.flow_steps if step.flow_id == sync.id]

    branch_edges = [
        edges[step.edge_id]
        for step in steps
        if edges[step.edge_id].relation is Relation.BRANCHES_TO
        and edges[step.edge_id].flow is EdgeFlow.CONDITIONAL
        and edges[step.edge_id].condition.polarity.value in {"true", "false"}
    ]
    assert {edge.condition.polarity.value for edge in branch_edges} == {"true", "false"}
    invocation = next(
        edge for edge in edges.values() if edge.relation is Relation.INVOKES
    )
    returns = [edge for edge in edges.values() if edge.relation is Relation.RETURNS_TO]
    assert returns
    assert {edge.call_site_id for edge in returns} == {invocation.call_site_id}
    exception = next(
        edges[step.edge_id]
        for step in steps
        if edges[step.edge_id].flow is EdgeFlow.EXCEPTION
    )
    assert exception.exception_scope_id is not None
    assert (
        next(
            structure.id
            for structure in artifact.structures
            if structure.kind is StructureKind.EXCEPTION_SCOPE
        )
        == exception.exception_scope_id
    )


def test_uncertainty_is_a_hard_frontier_and_omits_hint_returns(tmp_path):
    artifact = _build(tmp_path, _complex_result(unresolved=True))
    edges = {edge.id: edge for edge in artifact.edges}
    frontier_steps = [
        step
        for step in artifact.flow_steps
        if edges[step.edge_id].uncertainty_id is not None
    ]

    assert len(frontier_steps) == 1
    frontier_target = edges[frontier_steps[0].edge_id].target_id
    assert (
        next(node for node in artifact.nodes if node.id == frontier_target).kind
        is NodeKind.UNKNOWN_BOUNDARY
    )
    assert all(
        edges[step.edge_id].source_id != frontier_target for step in artifact.flow_steps
    )
    assert not any(
        edges[step.edge_id].relation is Relation.RETURNS_TO
        and edges[step.edge_id].call_site_id
        == edges[frontier_steps[0].edge_id].call_site_id
        for step in artifact.flow_steps
    )
    validate_artifact(artifact)


def test_complete_call_candidates_are_closed_dynamic_frontiers(tmp_path):
    from hermes_cli.hades_graph_v2.model import EvidenceOrigin
    from hermes_cli.hades_index.lifecycle.model import (
        CallSiteSubjectIR,
        CandidateSetKnowledge,
        LocalNodeTarget,
        Priority,
        ResolutionKind,
        UnresolvedFact,
    )

    result = _control_flow_fixture(
        workers=2,
        unresolved=False,
        symbol_resolution_full=True,
    )
    result = replace(result, entrypoints=(_public_api_entrypoint(result),))
    base = result.edge_facts[0]
    workers = tuple(item for item in result.declarations if item.name == "worker")
    evidence = replace(
        base.evidence,
        origin=EvidenceOrigin.INFERRED,
        inference_rule="closed_world_dispatch",
    )
    candidates = tuple(
        replace(
            base,
            local_key=_complex_key("edge_fact", f"candidate/{index}"),
            target=LocalNodeTarget(worker.local_key),
            evidence=evidence,
        )
        for index, worker in enumerate(workers)
    )
    fact = UnresolvedFact(
        _complex_key("unresolved", "candidate/set"),
        CallSiteSubjectIR(result.call_sites[0].local_key),
        ResolutionKind.CALL_TARGET,
        CandidateSetKnowledge.COMPLETE,
        "dynamic_dispatch",
        "Which implementation is exhaustive?",
        ("inspect_receiver_assignments",),
        (base.locator,),
        tuple(sorted(item.local_key for item in workers)),
        tuple(sorted(item.local_key for item in candidates)),
        Priority.HIGH,
        "Changes the call lifecycle.",
    )
    artifact = _build(
        tmp_path,
        replace(
            result,
            edge_facts=_sort(candidates),
            unresolved_facts=(fact,),
        ),
    )
    uncertainty = artifact.uncertainties[0]
    candidate_edges = tuple(
        edge for edge in artifact.edges if edge.id in uncertainty.candidate_edge_ids
    )

    assert uncertainty.candidate_set_knowledge is CandidateSetKnowledge.COMPLETE
    assert len(candidate_edges) == 2
    assert len({edge.target_id for edge in candidate_edges}) == 2
    assert all(edge.flow is EdgeFlow.ALTERNATIVE for edge in candidate_edges)
    assert len({edge.branch_group_id for edge in candidate_edges}) == 1
    assert not any(
        step.edge_id in uncertainty.candidate_edge_ids
        and any(
            other.flow_id == step.flow_id
            and next(
                edge for edge in artifact.edges if edge.id == other.edge_id
            ).source_id
            == next(
                edge for edge in artifact.edges if edge.id == step.edge_id
            ).target_id
            for other in artifact.flow_steps
        )
        for step in artifact.flow_steps
    )
    validate_artifact(artifact)


@pytest.mark.parametrize(
    "declaration_kind",
    (NodeKind.EVENT, NodeKind.LISTENER, NodeKind.JOB, NodeKind.QUEUE),
)
def test_verified_async_dispatch_links_executable_child_owned_cfg(
    tmp_path, declaration_kind
):
    result = _complex_result()
    worker = next(item for item in result.declarations if item.name == "worker")
    result = replace(
        result,
        declarations=_sort(
            replace(item, declaration_kind=declaration_kind)
            if item.local_key == worker.local_key
            else item
            for item in result.declarations
        ),
    )
    result.validate()
    artifact = _build(tmp_path, replace(result, edge_facts=()))
    edges = {edge.id: edge for edge in artifact.edges}
    parent = next(flow for flow in artifact.flows if flow.kind.value != "async_flow")
    child = next(flow for flow in artifact.flows if flow.kind.value == "async_flow")
    dispatch = next(
        step
        for step in artifact.flow_steps
        if step.flow_id == parent.id and edges[step.edge_id].flow is EdgeFlow.ASYNC
    )

    assert dispatch.async_child_flow_id == child.id
    assert dispatch.async_context is AsyncContext.SYNCHRONOUS
    assert child.root_node_id == edges[dispatch.edge_id].target_id
    child_steps = tuple(
        step for step in artifact.flow_steps if step.flow_id == child.id
    )
    assert child_steps
    assert all(step.async_context is AsyncContext.LINKED_ASYNC for step in child_steps)
    assert any(
        edges[step.edge_id].source_id == child.root_node_id for step in child_steps
    )
    assert all(
        not (
            step.flow_id == parent.id
            and edges[step.edge_id].source_id == child.root_node_id
        )
        for step in artifact.flow_steps
    )


@pytest.mark.parametrize(
    "declaration_kind",
    (
        NodeKind.MIDDLEWARE,
        NodeKind.GUARD,
        NodeKind.AUTHORIZATION,
        NodeKind.VALIDATOR,
        NodeKind.BINDING,
    ),
)
def test_schema_legal_framework_callable_declaration_owns_cfg(
    tmp_path, declaration_kind
):
    result = _complex_result()
    worker = next(item for item in result.declarations if item.name == "worker")
    result = replace(
        result,
        declarations=_sort(
            replace(item, declaration_kind=declaration_kind)
            if item.local_key == worker.local_key
            else item
            for item in result.declarations
        ),
        blocks=_sort(
            replace(
                block,
                successors=tuple(
                    successor
                    for successor in block.successors
                    if not isinstance(successor, AsyncSuccessor)
                ),
            )
            for block in result.blocks
        ),
    )
    result.validate()

    artifact = _build(tmp_path, result)
    declaration_node = next(
        node
        for node in artifact.nodes
        if node.kind is declaration_kind
        and node.qualified_name == worker.qualified_name
    )
    edges = {edge.id: edge for edge in artifact.edges}

    assert any(
        edges[step.edge_id].source_id == declaration_node.id
        for step in artifact.flow_steps
    )


def test_self_and_mutual_recursion_reach_finite_fixed_points(tmp_path):
    for mutual in (False, True):
        artifact = _build(tmp_path, _recursive_result(mutual=mutual))
        edges = {edge.id: edge for edge in artifact.edges}
        sync = next(flow for flow in artifact.flows if flow.kind.value != "async_flow")
        recursion = [
            step
            for step in artifact.flow_steps
            if step.flow_id == sync.id
            and edges[step.edge_id].relation is Relation.INVOKES
            and step.backbone_role is BackboneRole.LOOP
        ]
        assert len(recursion) == (2 if mutual else 1)
        assert len(artifact.flow_steps) <= len(artifact.edges) * len(Stage) * len(
            artifact.flows
        )
        validate_artifact(artifact)


def test_async_terminal_semantics(tmp_path):
    artifact = _build(tmp_path, _complex_result())
    parent = next(flow for flow in artifact.flows if flow.kind.value != "async_flow")

    assert parent.terminal_count.knowledge is Knowledge.EXACT
    assert parent.terminal_count.value == parent.terminal_count.represented
    assert parent.linked_async_flow_count.value == 1
    assert parent.uncertainty_count.knowledge is Knowledge.ABSENCE_VERIFIED
    assert parent.uncertainty_count.value == 0


def test_graph_builder_is_permutation_invariant_and_deduplicates_identical_ir(tmp_path):
    from hermes_cli.hades_index.lifecycle.builder import GraphBuilder

    builder = GraphBuilder(generated_at=lambda: "2026-07-19T12:00:00Z")
    result = _complex_result()
    first = builder.build(_context(tmp_path), (result, result))
    second = builder.build(_context(tmp_path), tuple(reversed((result, result))))

    assert artifact_to_payload(first) == artifact_to_payload(second)


def test_framework_pipeline_segments_are_ordered_and_short_circuits_are_reachable(
    tmp_path,
):
    artifact = _build(tmp_path, _segment_rich_result())
    edges = {edge.id: edge for edge in artifact.edges}
    framework_nodes = {
        node.id: node
        for node in artifact.nodes
        if isinstance(node.properties, FrameworkProperties)
    }
    sync = next(flow for flow in artifact.flows if flow.kind.value != "async_flow")
    steps = [step for step in artifact.flow_steps if step.flow_id == sync.id]
    reached = {edges[step.edge_id].target_id: step.min_depth for step in steps}

    assert {node.properties.pipeline_order for node in framework_nodes.values()} == {
        0,
        1,
    }
    middleware = next(
        node for node in framework_nodes.values() if node.kind is NodeKind.MIDDLEWARE
    )
    authorization = next(
        node for node in framework_nodes.values() if node.kind is NodeKind.AUTHORIZATION
    )
    assert reached[middleware.id] < reached[authorization.id]
    assert any(
        edges[step.edge_id].source_id in framework_nodes
        and artifact.nodes[
            next(
                index
                for index, node in enumerate(artifact.nodes)
                if node.id == edges[step.edge_id].target_id
            )
        ].kind
        in {NodeKind.RESPONSE, NodeKind.EXCEPTION}
        for step in steps
    )
    validate_artifact(artifact)


def test_framework_pipeline_rejects_dangling_segment_successor():
    result = _valid_result()
    dangling = replace(
        result.framework_segments[0],
        success_successor=AlwaysSuccessor(_complex_key("missing", "segment"), 0),
    )

    with pytest.raises(IRValidationError, match="unresolved_reference"):
        replace(result, framework_segments=(dangling,)).validate()


def test_framework_pipeline_materializes_every_successor_union(tmp_path):
    from hermes_cli.hades_graph_v2.model import ConditionPolarity
    from hermes_cli.hades_index.lifecycle.model import (
        BasicBlock,
        BranchArm,
        ConditionIR,
        ControlKind,
        StructureIR,
    )

    result = _valid_result()
    base = result.framework_segments[0]
    declaration = result.declarations[0]
    terminal = result.terminals[0]
    entry = next(
        item for item in result.blocks if item.local_key == declaration.entry_block_key
    )
    body = next(
        item
        for item in result.blocks
        if item.local_key in declaration.normal_exit_block_keys
    )
    catch = next(item for item in result.blocks if item.control_kind.value == "catch")
    exception_structure = next(
        item for item in result.structures if item.kind is StructureKind.EXCEPTION_SCOPE
    )
    exception_scope = next(
        item
        for item in result.exception_scopes
        if item.structure_key == exception_structure.local_key
    )
    branch_key = _complex_key("structure", "framework/union")
    branch = StructureIR(
        branch_key,
        StructureKind.BRANCH_GROUP,
        declaration.local_key,
        "framework/union",
        0,
        StructureSubtype.FRAMEWORK_SHORT_CIRCUIT,
        body.local_key,
        None,
        base.evidence,
    )
    arm = BranchArm(
        branch_key,
        body.local_key,
        entry.local_key,
        ConditionPolarity.TRUE,
        ConditionIR("predicate", "framework_union", "b" * 64, ConditionPolarity.TRUE),
        0,
    )
    job_block_key = _complex_key("basic_block", "framework/union/job")
    job = replace(
        declaration,
        local_key=_complex_key("declaration", "framework/union/job"),
        declaration_kind=NodeKind.JOB,
        name="framework_union_job",
        qualified_name="app.framework_union_job",
        locator=_complex_ast("framework/union/job"),
        entry_block_key=job_block_key,
        normal_exit_block_keys=(job_block_key,),
        exception_exit_block_keys=(),
    )
    job_block = BasicBlock(
        job_block_key,
        job.local_key,
        ControlKind.RETURN,
        0,
        _complex_ast("framework/union/job/entry"),
        (),
    )
    keys = tuple(
        _complex_key("framework_segment", f"union/{index}") for index in range(4)
    )

    def boundary(index: int, role: str, successor):
        return FrameworkPipelineSegment(
            keys[index],
            role,
            index,
            FrameworkBoundaryTarget(
                FrameworkBoundaryDescriptor(
                    "fastapi",
                    role,
                    role,
                    base.evidence.locator,
                    base.evidence,
                )
            ),
            successor,
            (),
            base.evidence,
        )

    first = FrameworkPipelineSegment(
        keys[0],
        "middleware",
        0,
        FrameworkLocalTarget(declaration.local_key),
        AlwaysSuccessor(keys[1], 0),
        (
            BranchSuccessor(entry.local_key, branch_key, 0, 0),
            ExceptionSuccessor(
                entry.local_key,
                exception_scope.local_key,
                "RuntimeError",
                1,
            ),
            LoopSuccessor(entry.local_key, "back", 2),
            AsyncSuccessor(job.local_key, "task", 3),
            ReturnSuccessor(terminal.local_key, 4),
        ),
        base.evidence,
    )
    segments = (
        first,
        boundary(1, "authorization", AlwaysSuccessor(keys[2], 0)),
        boundary(2, "request_validation", AlwaysSuccessor(keys[3], 0)),
        boundary(3, "response", ReturnSuccessor(terminal.local_key, 0)),
    )
    candidate = replace(
        result.entrypoints[0],
        handler_local_key=declaration.local_key,
        unresolved_fact_local_key=None,
        framework_segment_keys=keys,
    )
    all_successors = replace(
        result,
        branch_arms=tuple(
            sorted(
                (*result.branch_arms, arm),
                key=lambda item: (
                    item.branch_local_key,
                    item.arm_ordinal,
                ),
            )
        ),
        blocks=_sort((*result.blocks, job_block)),
        declarations=_sort((*result.declarations, job)),
        edge_facts=(),
        framework_segments=_sort(segments),
        entrypoints=(candidate,),
        structures=_sort((*result.structures, branch)),
        unresolved_facts=(),
    )
    all_successors.validate()

    artifact = _build(tmp_path, all_successors)
    reached = {(edge.relation, edge.flow) for edge in artifact.edges}

    assert (Relation.BRANCHES_TO, EdgeFlow.CONDITIONAL) in reached
    assert (Relation.THROWS_TO, EdgeFlow.EXCEPTION) in reached
    assert any(flow is EdgeFlow.LOOP for _relation, flow in reached)
    assert (Relation.DISPATCHES, EdgeFlow.ASYNC) in reached
    assert (Relation.RESPONDS_WITH, EdgeFlow.ALWAYS) in reached


def test_callable_summary_contains_declared_normal_exit(tmp_path):
    from hermes_cli.hades_index.lifecycle.traversal import (
        CanonicalTopology,
        build_callable_summaries,
    )

    result = _complex_result()
    artifact = _build(tmp_path, result)
    worker = next(node for node in artifact.nodes if node.name == "worker")
    worker_ir = next(item for item in result.declarations if item.name == "worker")
    exit_ir = next(
        block
        for block in result.blocks
        if block.local_key in worker_ir.normal_exit_block_keys
    )
    exit_node = next(
        node
        for node in artifact.nodes
        if getattr(node.identity, "owner_node_id", None) == worker.id
        and getattr(node.identity, "structural_path", None)
        == exit_ir.locator.structural_path
    )
    topology = CanonicalTopology(
        artifact.nodes,
        artifact.structures,
        artifact.edges,
        artifact.uncertainties,
        artifact.graph_contract.completeness.capabilities,
    )

    summary = build_callable_summaries(topology)[(worker.id, Stage.DOMAIN)]

    assert exit_node.id in summary.normal_exit_node_ids


def test_root_callable_summary_is_the_flow_membership_source(tmp_path):
    from hermes_cli.hades_index.lifecycle.traversal import (
        CanonicalTopology,
        build_callable_summaries,
        build_lifecycle_flows,
    )

    artifact = _build(tmp_path, _complex_result())
    topology = CanonicalTopology(
        artifact.nodes,
        artifact.structures,
        artifact.edges,
        artifact.uncertainties,
        artifact.graph_contract.completeness.capabilities,
    )
    summaries = dict(build_callable_summaries(topology))
    entrypoint = artifact.entrypoints[0]
    root_key = (entrypoint.id, Stage.ENTRY)
    admitted = next(
        item
        for item in summaries[root_key].reachable_edge_stage_states
        if next(edge for edge in artifact.edges if edge.id == item.edge_id).source_id
        == entrypoint.id
        and item.stage_from is Stage.ENTRY
    )
    summaries[root_key] = replace(
        summaries[root_key],
        reachable_edge_stage_states=frozenset({admitted}),
    )

    flows, steps = build_lifecycle_flows(topology, artifact.entrypoints, summaries)
    root_flow = next(item for item in flows if item.root_node_id == entrypoint.id)

    assert {item.edge_id for item in steps if item.flow_id == root_flow.id} == {
        admitted.edge_id
    }


def test_source_block_self_recursion_is_classified_as_loop(tmp_path):
    result = _recursive_result(mutual=False)
    site = result.call_sites[0]
    invocation = replace(
        result.edge_facts[0], source_node_local_key=site.source_block_key
    )
    source_block_result = replace(result, edge_facts=(invocation,))
    source_block_result.validate()

    artifact = _build(tmp_path, source_block_result)
    edges = {edge.id: edge for edge in artifact.edges}

    assert any(
        edges[step.edge_id].relation is Relation.INVOKES
        and step.backbone_role is BackboneRole.LOOP
        for step in artifact.flow_steps
    )


def test_uncertainty_boundary_is_not_counted_as_terminal_outcome(tmp_path):
    artifact = _build(tmp_path, _valid_result())
    flow = next(flow for flow in artifact.flows if flow.kind.value != "async_flow")

    assert flow.terminal_count.represented == 0
    assert flow.terminal_count.value is None
    assert flow.terminal_count.knowledge is Knowledge.UNKNOWN
    assert flow.uncertainty_count.represented == 1


def test_detected_unsupported_language_cannot_report_full_completeness(tmp_path):
    event = CoverageEvent(
        "python",
        CoverageCapability.CONTROL_FLOW,
        CoverageOutcome.UNSUPPORTED,
        "parser_unavailable",
        "app.py",
        0,
        1,
    )
    artifact = _build(tmp_path, _empty_result(event))
    completeness = artifact.graph_contract.completeness

    assert tuple(item.language for item in completeness.languages) == ("python",)
    assert completeness.capabilities.control_flow.status.value == "unsupported"
    assert completeness.status.value == "partial"


def test_unreadable_represented_file_is_failed_not_zero_byte_analyzed(tmp_path):
    def unreadable(_path: Path) -> bytes:
        raise OSError("permission denied")

    context = replace(_context(tmp_path), file_accessor=unreadable)
    from hermes_cli.hades_index.lifecycle.builder import GraphBuilder

    artifact = GraphBuilder(generated_at=lambda: "2026-07-19T12:00:00Z").build(
        context, (_valid_result(),)
    )
    file_node = next(node for node in artifact.nodes if node.kind is NodeKind.FILE)
    files = artifact.graph_contract.coverage.files

    assert file_node.properties.analysis_status is AnalysisStatus.FAILED
    assert file_node.properties.omission_reason.value == "file_read_failed"
    assert files.analyzed == 0
    assert files.failed == 1


def test_same_canonical_edge_id_with_different_value_is_fatal(tmp_path):
    from hermes_cli.hades_index.lifecycle.model import EdgeFactIR, LocalNodeTarget

    result = _resolved_result()
    declaration = result.declarations[0]
    duplicate = EdgeFactIR(
        local_key=_complex_key("edge_fact", "generated-entry-collision"),
        source_node_local_key=declaration.local_key,
        target=LocalNodeTarget(declaration.entry_block_key),
        relation=Relation.PASSES_THROUGH,
        flow=EdgeFlow.ALWAYS,
        condition=None,
        branch_group_key=None,
        call_site_key=None,
        exception_scope_key=None,
        order=99,
        locator=replace(
            declaration.locator,
            structural_path=f"{declaration.locator.structural_path}/entry",
        ),
        evidence=_complex_evidence(
            replace(
                declaration.locator,
                structural_path=f"{declaration.locator.structural_path}/entry",
            )
        ),
    )
    colliding = replace(result, edge_facts=(duplicate,))
    colliding.validate()

    with pytest.raises(IRValidationError, match="semantic_collision"):
        _build(tmp_path, colliding)


def test_same_semantic_edge_merges_compatible_evidence(tmp_path):
    from hermes_cli.hades_index.lifecycle.model import EdgeFactIR, LocalNodeTarget

    result = _resolved_result()
    declaration = result.declarations[0]
    locator = replace(
        declaration.locator,
        structural_path=f"{declaration.locator.structural_path}/entry",
    )
    first = EdgeFactIR(
        _complex_key("edge_fact", "evidence/first"),
        declaration.local_key,
        LocalNodeTarget(declaration.entry_block_key),
        Relation.PASSES_THROUGH,
        EdgeFlow.ALWAYS,
        None,
        None,
        None,
        None,
        0,
        locator,
        _complex_evidence(declaration.locator),
    )
    second = replace(
        first,
        local_key=_complex_key("edge_fact", "evidence/second"),
        evidence=replace(first.evidence, extractor="resolver.static"),
    )
    supported = replace(result, edge_facts=_sort((first, second)))
    supported.validate()

    artifact = _build(tmp_path, supported)
    matching = [
        edge
        for edge in artifact.edges
        if edge.relation is Relation.PASSES_THROUGH
        and edge.source_id
        == next(node.id for node in artifact.nodes if node.name == declaration.name)
    ]

    assert len(matching) == 1
    evidence = matching[0].evidence
    assert {item.extractor for item in (evidence.primary, *evidence.supporting)} == {
        "hades.builder.v2",
        "resolver.static",
        "tree-sitter.python",
    }


def test_callee_exception_exit_propagates_to_call_site_scope(tmp_path):
    result = _complex_result()
    worker = next(item for item in result.declarations if item.name == "worker")
    worker_entry = next(
        item for item in result.blocks if item.local_key == worker.entry_block_key
    )
    terminal = Terminal(
        _complex_key("terminal", "worker/runtime_error/scope"),
        worker_entry.local_key,
        TerminalKind.EXCEPTION,
        None,
        "RuntimeError",
        _complex_ast("worker/runtime_error/scope"),
    )
    exceptional_worker = replace(
        worker,
        normal_exit_block_keys=(),
        exception_exit_block_keys=(worker.entry_block_key,),
    )
    exceptional = replace(
        result,
        declarations=_sort(
            exceptional_worker if item.local_key == worker.local_key else item
            for item in result.declarations
        ),
        blocks=_sort(
            replace(
                item,
                control_kind=ControlKind.THROW,
                successors=(ReturnSuccessor(terminal.local_key, 0),),
            )
            if item.local_key == worker_entry.local_key
            else item
            for item in result.blocks
        ),
        terminals=_sort((*result.terminals, terminal)),
    )
    exceptional.validate()

    artifact = _build(tmp_path, exceptional)
    worker_node = next(node for node in artifact.nodes if node.name == "worker")
    worker_exit = next(
        node
        for node in artifact.nodes
        if getattr(node.identity, "owner_node_id", None) == worker_node.id
    )
    expected_scope = next(
        structure.id
        for structure in artifact.structures
        if structure.kind is StructureKind.EXCEPTION_SCOPE
    )
    propagated = next(
        edge
        for edge in artifact.edges
        if edge.source_id == worker_exit.id and edge.relation is Relation.THROWS_TO
    )

    assert propagated.flow is EdgeFlow.EXCEPTION
    assert propagated.exception_scope_id == expected_scope
    assert next(
        node for node in artifact.nodes if node.id == propagated.target_id
    ).kind in {
        NodeKind.BASIC_BLOCK,
        NodeKind.MERGE,
    }


def test_callee_exception_uses_nearest_nested_lexical_scope(tmp_path):
    from hermes_cli.hades_index.lifecycle.model import (
        BasicBlock,
        ControlKind,
        ExceptionScope,
        StructureIR,
        Terminal,
        TerminalKind,
    )

    result = _complex_result()
    handler = next(item for item in result.declarations if item.name == "handler")
    worker = next(item for item in result.declarations if item.name == "worker")
    worker_entry = next(
        item for item in result.blocks if item.local_key == worker.entry_block_key
    )
    outer_structure = next(
        item for item in result.structures if item.kind is StructureKind.EXCEPTION_SCOPE
    )
    outer_scope = result.exception_scopes[0]
    final_block = outer_scope.finally_block_key
    assert final_block is not None
    inner_path = "body/try/inner"
    inner_structure = StructureIR(
        _complex_key("structure", inner_path),
        StructureKind.EXCEPTION_SCOPE,
        handler.local_key,
        inner_path,
        0,
        StructureSubtype.TRY_CATCH,
        final_block,
        outer_structure.local_key,
        _complex_evidence(_complex_ast(inner_path)),
    )
    inner_catch = BasicBlock(
        _complex_key("basic_block", f"{inner_path}/catch"),
        handler.local_key,
        ControlKind.CATCH,
        8,
        _complex_ast(f"{inner_path}/catch"),
        (AlwaysSuccessor(final_block, 0),),
    )
    inner_scope = ExceptionScope(
        _complex_key("exception_scope", inner_path),
        inner_structure.local_key,
        handler.local_key,
        _complex_ast(inner_path),
        (ExceptionCatchArm("RuntimeError", inner_catch.local_key, 0),),
        None,
        outer_scope.local_key,
    )
    terminal = Terminal(
        _complex_key("terminal", "worker/runtime_error/nested"),
        worker_entry.local_key,
        TerminalKind.EXCEPTION,
        None,
        "RuntimeError",
        _complex_ast("worker/runtime_error/nested"),
    )
    site = replace(result.call_sites[0], exception_scope_key=inner_structure.local_key)
    exceptional_worker = replace(
        worker,
        normal_exit_block_keys=(),
        exception_exit_block_keys=(worker.entry_block_key,),
    )
    nested = replace(
        result,
        declarations=_sort(
            exceptional_worker if item.local_key == worker.local_key else item
            for item in result.declarations
        ),
        blocks=_sort((
            inner_catch,
            *(
                replace(
                    item,
                    control_kind=ControlKind.THROW,
                    successors=(ReturnSuccessor(terminal.local_key, 0),),
                )
                if item.local_key == worker_entry.local_key
                else item
                for item in result.blocks
            ),
        )),
        structures=_sort((*result.structures, inner_structure)),
        call_sites=(site,),
        exception_scopes=_sort((*result.exception_scopes, inner_scope)),
        terminals=_sort((*result.terminals, terminal)),
    )
    nested.validate()

    artifact = _build(tmp_path, nested)
    expected_target = next(
        node
        for node in artifact.nodes
        if getattr(node.identity, "structural_path", None)
        == inner_catch.locator.structural_path
    )
    expected_scope = next(
        structure.id
        for structure in artifact.structures
        if structure.structural_path == inner_structure.structural_path
    )
    worker_node = next(node for node in artifact.nodes if node.name == "worker")
    propagated = next(
        edge
        for edge in artifact.edges
        if edge.relation is Relation.THROWS_TO
        and getattr(
            next(node for node in artifact.nodes if node.id == edge.source_id).identity,
            "owner_node_id",
            None,
        )
        == worker_node.id
    )

    assert propagated.target_id == expected_target.id
    assert propagated.exception_scope_id == expected_scope


def test_unhandled_callee_exception_exit_reaches_exception_terminal(tmp_path):
    result = _complex_result()
    worker = next(item for item in result.declarations if item.name == "worker")
    site = replace(result.call_sites[0], exception_scope_key=None)
    exceptional_worker = replace(
        worker,
        normal_exit_block_keys=(),
        exception_exit_block_keys=(worker.entry_block_key,),
    )
    exceptional = replace(
        result,
        declarations=_sort(
            exceptional_worker if item.local_key == worker.local_key else item
            for item in result.declarations
        ),
        call_sites=(site,),
    )
    exceptional.validate()

    artifact = _build(tmp_path, exceptional)
    worker_node = next(node for node in artifact.nodes if node.name == "worker")
    worker_exit = next(
        node
        for node in artifact.nodes
        if getattr(node.identity, "owner_node_id", None) == worker_node.id
    )
    propagated = next(
        edge
        for edge in artifact.edges
        if edge.source_id == worker_exit.id and edge.relation is Relation.THROWS_TO
    )

    assert propagated.exception_scope_id is None
    assert (
        next(node for node in artifact.nodes if node.id == propagated.target_id).kind
        is NodeKind.EXCEPTION
    )


def test_complete_candidate_call_does_not_leak_shared_callee_return(tmp_path):
    from hermes_cli.hades_graph_v2.model import EvidenceOrigin
    from hermes_cli.hades_index.lifecycle.model import (
        CallSite,
        CallSiteSubjectIR,
        CandidateSetKnowledge,
        EdgeFactIR,
        LocalNodeTarget,
        Priority,
        ResolutionKind,
        StructureIR,
        TargetExpressionKind,
        UnresolvedFact,
    )

    result = _control_flow_fixture(workers=2, symbol_resolution_full=True)
    result = replace(result, entrypoints=(_public_api_entrypoint(result),))
    handler = next(item for item in result.declarations if item.name == "handler")
    workers = tuple(item for item in result.declarations if item.name == "worker")
    original_site = result.call_sites[0]
    original_structure = next(
        item for item in result.structures if item.kind is StructureKind.CALL_SITE
    )
    base = result.edge_facts[0]
    candidate_evidence = replace(
        base.evidence,
        origin=EvidenceOrigin.INFERRED,
        inference_rule="closed_world_dispatch",
    )
    candidates = tuple(
        replace(
            base,
            local_key=_complex_key("edge_fact", f"candidate/shared/{index}"),
            target=LocalNodeTarget(worker.local_key),
            evidence=candidate_evidence,
        )
        for index, worker in enumerate(workers)
    )
    fact = UnresolvedFact(
        _complex_key("unresolved", "candidate/shared"),
        CallSiteSubjectIR(original_site.local_key),
        ResolutionKind.CALL_TARGET,
        CandidateSetKnowledge.COMPLETE,
        "dynamic_dispatch",
        "Which shared implementation is exhaustive?",
        ("inspect_receiver_assignments",),
        (base.locator,),
        tuple(sorted(item.local_key for item in workers)),
        tuple(sorted(item.local_key for item in candidates)),
        Priority.HIGH,
        "Changes the call lifecycle.",
    )
    verified_path = "body/call_verified"
    verified_structure = StructureIR(
        _complex_key("structure", verified_path),
        StructureKind.CALL_SITE,
        handler.local_key,
        verified_path,
        0,
        StructureSubtype.CALL,
        original_structure.continuation_block_key,
        None,
        _complex_evidence(_complex_ast(verified_path)),
    )
    verified_site = CallSite(
        _complex_key("call_site", verified_path),
        handler.local_key,
        original_site.source_block_key,
        _complex_ast(verified_path),
        TargetExpressionKind.DIRECT_FUNCTION,
        workers[0].qualified_name,
        workers[0].qualified_name,
        None,
        0,
        original_site.continuation_block_key,
        original_site.exception_scope_key,
    )
    verified_edge = EdgeFactIR(
        _complex_key("edge_fact", verified_path),
        original_site.source_block_key,
        LocalNodeTarget(workers[0].local_key),
        Relation.INVOKES,
        EdgeFlow.ALWAYS,
        None,
        None,
        verified_structure.local_key,
        None,
        1,
        _complex_ast(verified_path),
        _complex_evidence(_complex_ast(verified_path)),
    )
    shared = replace(
        result,
        structures=_sort((*result.structures, verified_structure)),
        call_sites=_sort((*result.call_sites, verified_site)),
        edge_facts=_sort((*candidates, verified_edge)),
        unresolved_facts=(fact,),
    )
    shared.validate()

    artifact = _build(tmp_path, shared)
    uncertainty = artifact.uncertainties[0]
    uncertain_call_site_id = getattr(uncertainty.subject, "call_site_id")
    edges = {edge.id: edge for edge in artifact.edges}

    assert any(
        edge.relation is Relation.RETURNS_TO
        and edge.call_site_id != uncertain_call_site_id
        for edge in edges.values()
    )
    assert not any(
        edges[step.edge_id].relation is Relation.RETURNS_TO
        and edges[step.edge_id].call_site_id == uncertain_call_site_id
        for step in artifact.flow_steps
    )


def test_framework_terminal_paths_execute_local_target_before_continuation(tmp_path):
    artifact = _build(tmp_path, _segment_rich_result())
    flow = next(item for item in artifact.flows if item.kind.value != "async_flow")
    edges = {item.id: item for item in artifact.edges}
    nodes = {item.id: item for item in artifact.nodes}
    adjacency = defaultdict(list)
    for step in artifact.flow_steps:
        if step.flow_id == flow.id:
            edge = edges[step.edge_id]
            adjacency[edge.source_id].append(edge.target_id)
    terminal_kinds = {
        NodeKind.RESPONSE,
        NodeKind.REDIRECT,
        NodeKind.ABORT,
        NodeKind.EXCEPTION,
        NodeKind.EXIT,
    }
    terminal_paths: list[tuple[str, ...]] = []
    pending = [(flow.root_node_id, (flow.root_node_id,))]
    while pending:
        current, path = pending.pop()
        if nodes[current].kind in terminal_kinds:
            terminal_paths.append(path)
            continue
        for target in adjacency[current]:
            if target not in path:
                pending.append((target, (*path, target)))

    handler_id = artifact.entrypoints[0].handler_node_id
    assert handler_id is not None
    handler_cfg_ids = {
        node.id
        for node in artifact.nodes
        if node.kind
        in {NodeKind.BASIC_BLOCK, NodeKind.BRANCH, NodeKind.MERGE, NodeKind.LOOP}
        and getattr(node.identity, "owner_node_id", None) == handler_id
    }
    assert terminal_paths
    assert handler_cfg_ids
    assert all(handler_id in path for path in terminal_paths)
    assert all(handler_cfg_ids.intersection(path) for path in terminal_paths)


def test_framework_local_target_without_required_exit_stops_at_partial_boundary(
    tmp_path,
):
    result = _segment_rich_result()
    handler = result.declarations[0]
    missing_exit_result = replace(
        result,
        declarations=(replace(handler, normal_exit_block_keys=()),),
    )
    missing_exit_result.validate()

    artifact = _build(tmp_path, missing_exit_result)
    handler_id = next(node.id for node in artifact.nodes if node.name == handler.name)
    assert handler_id is not None
    handler_entry_id = next(
        node.id
        for node in artifact.nodes
        if getattr(node.identity, "owner_node_id", None) == handler_id
        and node.kind is NodeKind.BASIC_BLOCK
    )
    boundaries = [
        node
        for node in artifact.nodes
        if node.kind is NodeKind.FRAMEWORK_BOUNDARY
        and getattr(node.identity, "owner_node_id", None) == handler_id
        and getattr(node.identity, "semantic_role", None) == "unresolved_normal_exit"
    ]

    assert len(boundaries) == 1
    assert any(
        edge.source_id == handler_entry_id
        and edge.target_id == boundaries[0].id
        and edge.relation is Relation.PASSES_THROUGH
        and edge.flow is EdgeFlow.ALWAYS
        for edge in artifact.edges
    )
    assert artifact.graph_contract.completeness.status.value == "partial"
    assert any(
        reason.code.value == "verified_target_not_materialized"
        for reason in artifact.graph_contract.completeness.capabilities.framework_lifecycle.reasons
    )
    assert not any(
        edge.target_id != boundaries[0].id
        and (
            getattr(edge.occurrence, "ast_path", None)
            or getattr(edge.occurrence, "structural_pointer", "")
        ).startswith("pipeline/0/")
        and not (
            getattr(edge.occurrence, "ast_path", None)
            or getattr(edge.occurrence, "structural_pointer", "")
        ).endswith("/target")
        for edge in artifact.edges
    )


def test_framework_exception_continuation_requires_exact_exception_exit(tmp_path):
    result = _valid_result()
    handler = result.declarations[0]
    scope = result.exception_scopes[0]
    catch = scope.catch_arms[0]
    segment = replace(
        result.framework_segments[0],
        success_successor=ExceptionSuccessor(
            catch.target_block_key,
            scope.local_key,
            catch.caught_type_name,
            0,
        ),
    )
    missing_exit_result = replace(
        result,
        declarations=(replace(handler, exception_exit_block_keys=()),),
        framework_segments=(segment,),
    )
    missing_exit_result.validate()

    artifact = _build(tmp_path, missing_exit_result)
    handler_id = next(node.id for node in artifact.nodes if node.name == handler.name)
    boundary = next(
        node
        for node in artifact.nodes
        if node.kind is NodeKind.FRAMEWORK_BOUNDARY
        and getattr(node.identity, "owner_node_id", None) == handler_id
        and getattr(node.identity, "semantic_role", None) == "unresolved_exception_exit"
    )
    catch_id = next(
        node.id
        for node in artifact.nodes
        if getattr(node.identity, "structural_path", None) == "body/catch"
    )

    assert not any(
        edge.target_id == catch_id
        and edge.relation is Relation.THROWS_TO
        and (
            getattr(edge.occurrence, "ast_path", None)
            or getattr(edge.occurrence, "structural_pointer", "")
        ).startswith("pipeline/0/")
        for edge in artifact.edges
    )
    assert any(edge.target_id == boundary.id for edge in artifact.edges)
    assert artifact.graph_contract.completeness.status.value == "partial"


@pytest.mark.parametrize(
    ("language", "thrown", "caught", "expected"),
    (
        ("python", "FileNotFoundError", "OSError", "match"),
        ("python", "RuntimeError", "Error", "no_match"),
        ("python", None, None, "match"),
        ("python", None, "Exception", "unresolved"),
        ("php", "RuntimeException", "Exception", "match"),
        ("php", "RuntimeException", "Error", "no_match"),
        ("javascript", "TypeError", "Error", "match"),
        ("typescript", "RangeError", "Error", "match"),
        ("typescript", "RuntimeError", "Error", "unresolved"),
    ),
)
def test_exception_type_matching_uses_only_the_owner_language_hierarchy(
    language, thrown, caught, expected
):
    from hermes_cli.hades_index.lifecycle.builder import _exception_type_matches

    result = _exception_type_matches(language, thrown, caught)

    assert getattr(result, "value", result) == expected


def _unknown_exception_result(*, catch_all: bool):
    result = _complex_result()
    worker = next(item for item in result.declarations if item.name == "worker")
    worker_entry = next(
        item for item in result.blocks if item.local_key == worker.entry_block_key
    )
    scope = result.exception_scopes[0]
    catch_arm = scope.catch_arms[0]
    if catch_all:
        scope = replace(
            scope,
            catch_arms=(ExceptionCatchArm(None, catch_arm.target_block_key, 0),),
        )
    exceptional = replace(
        result,
        declarations=_sort(
            replace(
                worker,
                normal_exit_block_keys=(),
                exception_exit_block_keys=(worker.entry_block_key,),
            )
            if item.local_key == worker.local_key
            else item
            for item in result.declarations
        ),
        blocks=_sort(
            replace(item, control_kind=ControlKind.THROW, successors=())
            if item.local_key == worker_entry.local_key
            else item
            for item in result.blocks
        ),
        exception_scopes=_sort(
            scope if item.local_key == scope.local_key else item
            for item in result.exception_scopes
        ),
    )
    exceptional.validate()
    return exceptional


def test_unknown_exception_matches_nearest_lexical_catch_all(tmp_path):
    result = _unknown_exception_result(catch_all=True)
    catch_key = result.exception_scopes[0].catch_arms[0].target_block_key

    artifact = _build(tmp_path, result)
    catch_id = next(
        node.id
        for node in artifact.nodes
        if getattr(node.identity, "structural_path", None)
        == next(
            block.locator.structural_path
            for block in result.blocks
            if block.local_key == catch_key
        )
    )
    propagated = next(
        edge
        for edge in artifact.edges
        if edge.relation is Relation.THROWS_TO and edge.call_site_id is not None
    )

    assert propagated.target_id == catch_id
    assert propagated.uncertainty_id is None


def test_unknown_exception_to_typed_catch_stops_at_partial_uncertainty(tmp_path):
    artifact = _build(tmp_path, _unknown_exception_result(catch_all=False))
    propagated = next(
        edge
        for edge in artifact.edges
        if edge.relation is Relation.THROWS_TO and edge.call_site_id is not None
    )
    target = next(node for node in artifact.nodes if node.id == propagated.target_id)

    assert target.kind is NodeKind.UNKNOWN_BOUNDARY
    assert propagated.uncertainty_id is not None
    assert propagated.uncertainty_id == target.uncertainty_id
    uncertainty = next(
        item for item in artifact.uncertainties if item.id == propagated.uncertainty_id
    )
    assert len(artifact.uncertainties) == 1
    assert uncertainty.reason_code.value == "exception_target_unresolved"
    assert artifact.graph_contract.completeness.status.value == "partial"
    assert any(
        reason.code.value == "exception_target_unresolved"
        for reason in artifact.graph_contract.completeness.capabilities.exceptions.reasons
    )
    assert artifact.graph_contract.coverage.records.uncertainties == 1
    containing_flow = next(
        flow
        for flow in artifact.flows
        if any(
            step.flow_id == flow.id and step.edge_id == propagated.id
            for step in artifact.flow_steps
        )
    )
    assert containing_flow.completeness.status.value == "partial"
    assert containing_flow.uncertainty_count.represented == 1


def test_unknown_exception_uncertainty_is_adapter_permutation_invariant(tmp_path):
    from hermes_cli.hades_index.lifecycle.builder import GraphBuilder

    result = _unknown_exception_result(catch_all=False)
    empty = _empty_result()
    builder = GraphBuilder(generated_at=lambda: "2026-07-19T12:00:00Z")

    first = builder.build(_context(tmp_path), (result, empty))
    second = builder.build(_context(tmp_path), (empty, result))

    assert artifact_to_payload(first) == artifact_to_payload(second)


@pytest.mark.parametrize("specific_first", (True, False))
def test_overlapping_exception_catches_follow_lexical_arm_order(
    tmp_path, specific_first
):
    result = _complex_result()
    handler = next(item for item in result.declarations if item.name == "handler")
    worker = next(item for item in result.declarations if item.name == "worker")
    worker_entry = next(
        item for item in result.blocks if item.local_key == worker.entry_block_key
    )
    scope = result.exception_scopes[0]
    original_catch = scope.catch_arms[0].target_block_key
    general_catch = replace(
        next(item for item in result.blocks if item.local_key == original_catch),
        local_key=_complex_key("basic_block", "body/general_catch"),
        locator=_complex_ast("body/general_catch"),
        ordinal=9,
    )
    arm_specs = (
        ("RuntimeError", original_catch),
        ("Exception", general_catch.local_key),
    )
    if not specific_first:
        arm_specs = tuple(reversed(arm_specs))
    arms = tuple(
        ExceptionCatchArm(caught, target, ordinal)
        for ordinal, (caught, target) in enumerate(arm_specs)
    )
    terminal = Terminal(
        _complex_key("terminal", "worker/runtime_error/overlap"),
        worker_entry.local_key,
        TerminalKind.EXCEPTION,
        None,
        "RuntimeError",
        _complex_ast("worker/runtime_error/overlap"),
    )
    exceptional = replace(
        result,
        declarations=_sort(
            replace(
                worker,
                normal_exit_block_keys=(),
                exception_exit_block_keys=(worker.entry_block_key,),
            )
            if item.local_key == worker.local_key
            else item
            for item in result.declarations
        ),
        blocks=_sort((
            general_catch,
            *(
                replace(
                    item,
                    control_kind=ControlKind.THROW,
                    successors=(ReturnSuccessor(terminal.local_key, 0),),
                )
                if item.local_key == worker_entry.local_key
                else item
                for item in result.blocks
            ),
        )),
        exception_scopes=(replace(scope, catch_arms=arms),),
        terminals=_sort((*result.terminals, terminal)),
    )
    exceptional.validate()

    artifact = _build(tmp_path, exceptional)
    expected_path = (
        next(
            item for item in result.blocks if item.local_key == original_catch
        ).locator.structural_path
        if specific_first
        else general_catch.locator.structural_path
    )
    propagated = next(
        edge
        for edge in artifact.edges
        if edge.relation is Relation.THROWS_TO and edge.call_site_id is not None
    )
    target = next(node for node in artifact.nodes if node.id == propagated.target_id)

    assert getattr(target.identity, "structural_path", None) == expected_path


def test_framework_return_and_exception_references_are_closed() -> None:
    result = _valid_result()
    segment = result.framework_segments[0]
    missing_terminal = _complex_key("terminal", "missing/framework")
    missing_scope = _complex_key("structure", "missing/framework")

    with pytest.raises(IRValidationError, match="unresolved_reference"):
        replace(
            result,
            framework_segments=(
                replace(
                    segment,
                    success_successor=ReturnSuccessor(missing_terminal, 0),
                ),
            ),
        ).validate()
    with pytest.raises(IRValidationError, match="unresolved_reference"):
        replace(
            result,
            framework_segments=(
                replace(
                    segment,
                    success_successor=ExceptionSuccessor(
                        result.blocks[0].local_key,
                        missing_scope,
                        "RuntimeError",
                        0,
                    ),
                ),
            ),
        ).validate()


def test_framework_branch_and_loop_successors_preserve_exact_semantics(tmp_path):
    from hermes_cli.hades_graph_v2.model import ConditionPolarity
    from hermes_cli.hades_index.lifecycle.model import (
        BranchArm,
        ConditionIR,
        StructureIR,
    )

    result = _valid_result()
    segment = result.framework_segments[0]
    declaration = result.declarations[0]
    entry = next(
        item for item in result.blocks if item.local_key == declaration.entry_block_key
    )
    body = next(
        item
        for item in result.blocks
        if item.local_key in declaration.normal_exit_block_keys
    )
    catch = next(item for item in result.blocks if item.control_kind.value == "catch")
    branch_key = _complex_key("structure", "framework/branch")
    branch = StructureIR(
        branch_key,
        StructureKind.BRANCH_GROUP,
        declaration.local_key,
        "framework/branch",
        0,
        StructureSubtype.FRAMEWORK_SHORT_CIRCUIT,
        body.local_key,
        None,
        segment.evidence,
    )
    arms = (
        BranchArm(
            branch_key,
            body.local_key,
            entry.local_key,
            ConditionPolarity.TRUE,
            ConditionIR("predicate", "authorized", "b" * 64, ConditionPolarity.TRUE),
            0,
        ),
        BranchArm(
            branch_key,
            entry.local_key,
            catch.local_key,
            ConditionPolarity.FALSE,
            ConditionIR("predicate", "authorized", "b" * 64, ConditionPolarity.FALSE),
            1,
        ),
    )
    exact = replace(
        result,
        branch_arms=tuple(
            sorted(
                (*result.branch_arms, *arms),
                key=lambda item: (item.branch_local_key, item.arm_ordinal),
            )
        ),
        structures=_sort((*result.structures, branch)),
        framework_segments=(
            replace(
                segment,
                short_circuit_successors=(
                    BranchSuccessor(entry.local_key, branch_key, 0, 0),
                    BranchSuccessor(catch.local_key, branch_key, 1, 1),
                    LoopSuccessor(entry.local_key, "body", 2),
                    LoopSuccessor(body.local_key, "back", 3),
                    LoopSuccessor(catch.local_key, "exit", 4),
                ),
            ),
        ),
        edge_facts=(),
        entrypoints=(
            replace(
                result.entrypoints[0],
                handler_local_key=declaration.local_key,
                unresolved_fact_local_key=None,
            ),
        ),
        unresolved_facts=(),
    )
    exact.validate()

    artifact = _build(tmp_path, exact)
    edges = {item.id: item for item in artifact.edges}
    reached = [edges[item.edge_id] for item in artifact.flow_steps]
    branches = [
        item
        for item in reached
        if item.relation is Relation.BRANCHES_TO
        and item.condition is not None
        and item.condition.polarity in {ConditionPolarity.TRUE, ConditionPolarity.FALSE}
    ]
    loop_body = next(
        item
        for item in reached
        if item.condition is not None
        and item.condition.polarity is ConditionPolarity.LOOP_BODY
    )
    loop_back = next(
        item
        for item in reached
        if item.relation is Relation.BRANCHES_TO
        and item.flow is EdgeFlow.LOOP
        and item.condition is None
    )
    loop_exit = next(
        item
        for item in reached
        if item.condition is not None
        and item.condition.polarity is ConditionPolarity.LOOP_EXIT
    )

    assert len({item.branch_group_id for item in branches}) == 1
    assert {item.condition.polarity for item in branches} == {
        ConditionPolarity.TRUE,
        ConditionPolarity.FALSE,
    }
    assert loop_body.flow is EdgeFlow.CONDITIONAL
    assert loop_back.flow is EdgeFlow.LOOP
    assert loop_exit.flow is EdgeFlow.CONDITIONAL


def _shared_exception_callee_result():
    from hermes_cli.hades_index.lifecycle.model import (
        BasicBlock,
        CallSite,
        ControlKind,
        EdgeFactIR,
        ExceptionScope,
        LocalNodeTarget,
        StructureIR,
        TargetExpressionKind,
    )

    result = _complex_result()
    worker = next(item for item in result.declarations if item.name == "worker")
    handler = next(item for item in result.declarations if item.name == "handler")
    worker = replace(
        worker,
        normal_exit_block_keys=(),
        exception_exit_block_keys=(worker.entry_block_key,),
    )
    declaration_key = _complex_key("declaration", "disconnected")
    entry_key = _complex_key("basic_block", "disconnected/entry")
    continuation_key = _complex_key("basic_block", "disconnected/continuation")
    catch_key = _complex_key("basic_block", "disconnected/catch")
    disconnected = replace(
        handler,
        local_key=declaration_key,
        name="disconnected",
        qualified_name="app.disconnected",
        locator=_complex_ast("disconnected"),
        entry_block_key=entry_key,
        normal_exit_block_keys=(continuation_key,),
        exception_exit_block_keys=(),
    )
    disconnected_blocks = (
        BasicBlock(
            entry_key,
            declaration_key,
            ControlKind.ENTRY,
            0,
            _complex_ast("disconnected/entry"),
            (),
        ),
        BasicBlock(
            continuation_key,
            declaration_key,
            ControlKind.RETURN,
            1,
            _complex_ast("disconnected/continuation"),
            (),
        ),
        BasicBlock(
            catch_key,
            declaration_key,
            ControlKind.CATCH,
            2,
            _complex_ast("disconnected/catch"),
            (),
        ),
    )
    scope_structure_key = _complex_key("structure", "disconnected/try")
    scope_structure = StructureIR(
        scope_structure_key,
        StructureKind.EXCEPTION_SCOPE,
        declaration_key,
        "disconnected/try",
        0,
        StructureSubtype.TRY_CATCH,
        continuation_key,
        None,
        _complex_evidence(_complex_ast("disconnected/try")),
    )
    scope = ExceptionScope(
        _complex_key("exception_scope", "disconnected/try"),
        scope_structure_key,
        declaration_key,
        _complex_ast("disconnected/try"),
        (ExceptionCatchArm(None, catch_key, 0),),
        None,
        None,
    )
    call_path = "disconnected/call"
    call_structure_key = _complex_key("structure", call_path)
    call_site_key = _complex_key("call_site", call_path)
    call_structure = StructureIR(
        call_structure_key,
        StructureKind.CALL_SITE,
        declaration_key,
        call_path,
        0,
        StructureSubtype.CALL,
        continuation_key,
        scope_structure_key,
        _complex_evidence(_complex_ast(call_path)),
    )
    call_site = CallSite(
        call_site_key,
        declaration_key,
        entry_key,
        _complex_ast(call_path),
        TargetExpressionKind.DIRECT_FUNCTION,
        worker.qualified_name,
        worker.qualified_name,
        None,
        0,
        continuation_key,
        scope_structure_key,
    )
    invocation = EdgeFactIR(
        _complex_key("edge_fact", call_path),
        entry_key,
        LocalNodeTarget(worker.local_key),
        Relation.INVOKES,
        EdgeFlow.ALWAYS,
        None,
        None,
        call_structure_key,
        None,
        0,
        _complex_ast(call_path),
        _complex_evidence(_complex_ast(call_path)),
    )
    # The shared worker intentionally has no extracted exception type. Catch-all
    # scopes keep this fixture focused on verified propagation and flow closure.
    shared = replace(
        result,
        declarations=_sort((
            disconnected,
            *(
                worker if item.local_key == worker.local_key else item
                for item in result.declarations
            ),
        )),
        blocks=_sort((*result.blocks, *disconnected_blocks)),
        structures=_sort((*result.structures, scope_structure, call_structure)),
        call_sites=_sort((*result.call_sites, call_site)),
        edge_facts=_sort((*result.edge_facts, invocation)),
        exception_scopes=_sort(
            replace(
                item,
                catch_arms=tuple(
                    replace(arm, caught_type_name=None) for arm in item.catch_arms
                ),
            )
            for item in (*result.exception_scopes, scope)
        ),
    )
    shared.validate()
    return shared, catch_key


def test_shared_exception_callee_does_not_reach_disconnected_caller_catch(tmp_path):
    result, disconnected_catch_key = _shared_exception_callee_result()
    artifact = _build(tmp_path, result)
    catch_path = next(
        item.locator.structural_path
        for item in result.blocks
        if item.local_key == disconnected_catch_key
    )
    disconnected_catch_id = next(
        item.id
        for item in artifact.nodes
        if getattr(item.identity, "structural_path", None) == catch_path
    )
    edges = {item.id: item for item in artifact.edges}

    assert not any(
        edges[item.edge_id].target_id == disconnected_catch_id
        for item in artifact.flow_steps
    )


def test_inactive_same_caller_call_site_does_not_require_exception_continuation(
    tmp_path,
):
    result, disconnected_catch_key = _shared_exception_callee_result()
    disconnected = next(
        item for item in result.declarations if item.name == "disconnected"
    )
    handler = next(item for item in result.declarations if item.name == "handler")
    same_caller = replace(
        result,
        declarations=tuple(
            item
            for item in result.declarations
            if item.local_key != disconnected.local_key
        ),
        blocks=_sort(
            replace(item, declaration_key=handler.local_key)
            if item.declaration_key == disconnected.local_key
            else item
            for item in result.blocks
        ),
        structures=_sort(
            replace(item, owner_declaration_key=handler.local_key)
            if item.owner_declaration_key == disconnected.local_key
            else item
            for item in result.structures
        ),
        call_sites=tuple(
            replace(item, caller_declaration_key=handler.local_key)
            if item.caller_declaration_key == disconnected.local_key
            else item
            for item in result.call_sites
        ),
        exception_scopes=_sort(
            replace(item, declaration_key=handler.local_key)
            if item.declaration_key == disconnected.local_key
            else item
            for item in result.exception_scopes
        ),
    )
    same_caller.validate()

    artifact = _build(tmp_path, same_caller)
    catch_path = next(
        item.locator.structural_path
        for item in same_caller.blocks
        if item.local_key == disconnected_catch_key
    )
    disconnected_catch_id = next(
        item.id
        for item in artifact.nodes
        if getattr(item.identity, "structural_path", None) == catch_path
    )
    edges = {item.id: item for item in artifact.edges}

    assert not any(
        edges[item.edge_id].target_id == disconnected_catch_id
        for item in artifact.flow_steps
    )


def test_artifact_validation_rejects_omitted_active_interprocedural_throw(
    tmp_path,
):
    from hermes_cli.hades_graph_contract import artifact_graph_version
    from hermes_cli.hades_graph_v2.validation import GraphValidationError

    result, _disconnected_catch_key = _shared_exception_callee_result()
    artifact = _build(tmp_path, result)
    payload = artifact_to_payload(artifact)
    sync_flow = next(item for item in payload["flows"] if item["kind"] != "async_flow")
    steps = [
        item for item in payload["flow_steps"] if item["flow_id"] == sync_flow["id"]
    ]
    edges = {item["id"]: item for item in payload["edges"]}
    active_call_sites = {
        edges[item["edge_id"]]["call_site_id"]
        for item in steps
        if edges[item["edge_id"]]["relation"] == "invokes"
    }
    active_callee_ids = {
        edges[item["edge_id"]]["target_id"]
        for item in steps
        if edges[item["edge_id"]]["relation"] == "invokes"
    }
    throw_step = next(
        item
        for item in steps
        if edges[item["edge_id"]]["relation"] == "throws_to"
        and edges[item["edge_id"]]["call_site_id"] in active_call_sites
    )
    payload["flow_steps"].remove(throw_step)
    sync_flow["represented_step_count"] -= 1
    sync_flow["terminal_count"]["represented"] -= 1
    sync_flow["terminal_count"]["value"] -= 1
    if sync_flow["terminal_count"]["represented"] == 0:
        sync_flow["terminal_count"]["knowledge"] = "absence_verified"
    sync_flow["stage_counts"].pop("error")
    for step in steps:
        edge = edges[step["edge_id"]]
        if (
            edge["relation"] == "passes_through"
            and step["min_depth"] in {1, 2}
            and edge["call_site_id"] is None
            and edge["source_id"] not in active_callee_ids
        ):
            step["backbone_role"] = "mandatory"
        if step is not throw_step and step["stage_from"] == "error":
            step["min_depth"] += 1
            order_prefix, _depth, order_suffix = step["order_key"].split(":", 2)
            step["order_key"] = f"{order_prefix}:{step['min_depth']:06d}:{order_suffix}"
    payload["graph_contract"]["coverage"]["records"]["flow_steps"] -= 1
    payload["graph_contract"]["artifact_graph_version"] = artifact_graph_version(
        payload
    )

    with pytest.raises(GraphValidationError) as exc_info:
        validate_artifact(payload)

    assert exc_info.value.code == "flow_edge_omission"


def test_exception_propagation_skips_inner_nonmatching_catch_and_preserves_type(
    tmp_path,
):
    from hermes_cli.hades_index.lifecycle.model import (
        BasicBlock,
        ControlKind,
        ExceptionScope,
        StructureIR,
        Terminal,
        TerminalKind,
    )

    result = _complex_result()
    handler = next(item for item in result.declarations if item.name == "handler")
    worker = next(item for item in result.declarations if item.name == "worker")
    worker_entry = next(
        item for item in result.blocks if item.local_key == worker.entry_block_key
    )
    outer_structure = next(
        item for item in result.structures if item.kind is StructureKind.EXCEPTION_SCOPE
    )
    outer_scope = result.exception_scopes[0]
    outer_scope = replace(
        outer_scope,
        catch_arms=(
            ExceptionCatchArm(
                "RuntimeError", outer_scope.catch_arms[0].target_block_key, 0
            ),
        ),
    )
    final_block = outer_scope.finally_block_key
    assert final_block is not None
    inner_path = "body/try/nonmatching"
    inner_structure = StructureIR(
        _complex_key("structure", inner_path),
        StructureKind.EXCEPTION_SCOPE,
        handler.local_key,
        inner_path,
        0,
        StructureSubtype.TRY_CATCH,
        final_block,
        outer_structure.local_key,
        _complex_evidence(_complex_ast(inner_path)),
    )
    inner_catch = BasicBlock(
        _complex_key("basic_block", f"{inner_path}/catch"),
        handler.local_key,
        ControlKind.CATCH,
        9,
        _complex_ast(f"{inner_path}/catch"),
        (AlwaysSuccessor(final_block, 0),),
    )
    inner_scope = ExceptionScope(
        _complex_key("exception_scope", inner_path),
        inner_structure.local_key,
        handler.local_key,
        _complex_ast(inner_path),
        (ExceptionCatchArm("ValueError", inner_catch.local_key, 0),),
        None,
        outer_scope.local_key,
    )
    terminal = Terminal(
        _complex_key("terminal", "worker/runtime_error"),
        worker_entry.local_key,
        TerminalKind.EXCEPTION,
        None,
        "RuntimeError",
        _complex_ast("worker/runtime_error"),
    )
    exceptional_worker = replace(
        worker,
        normal_exit_block_keys=(),
        exception_exit_block_keys=(worker_entry.local_key,),
    )
    exceptional_entry = replace(
        worker_entry,
        control_kind=ControlKind.THROW,
        successors=(ReturnSuccessor(terminal.local_key, 0),),
    )
    site = replace(result.call_sites[0], exception_scope_key=inner_structure.local_key)
    typed = replace(
        result,
        declarations=_sort(
            exceptional_worker if item.local_key == worker.local_key else item
            for item in result.declarations
        ),
        blocks=_sort((
            inner_catch,
            *(
                exceptional_entry if item.local_key == worker_entry.local_key else item
                for item in result.blocks
            ),
        )),
        structures=_sort((*result.structures, inner_structure)),
        call_sites=(site,),
        exception_scopes=_sort((outer_scope, inner_scope)),
        terminals=_sort((*result.terminals, terminal)),
    )
    typed.validate()

    artifact = _build(tmp_path, typed)
    outer_target = next(
        item.id
        for item in artifact.nodes
        if getattr(item.identity, "structural_path", None)
        == next(
            block.locator.structural_path
            for block in result.blocks
            if block.local_key == outer_scope.catch_arms[0].target_block_key
        )
    )
    propagated = next(
        item
        for item in artifact.edges
        if item.relation is Relation.THROWS_TO
        and item.exception_scope_id is not None
        and item.condition is not None
        and getattr(
            next(node for node in artifact.nodes if node.id == item.source_id).identity,
            "owner_node_id",
            None,
        )
        == next(node.id for node in artifact.nodes if node.name == "worker")
    )

    assert propagated.target_id == outer_target
    assert propagated.condition.normalized == "RuntimeError"


def test_semantic_locator_outside_authoritative_inventory_is_rejected(tmp_path):
    from hermes_cli.hades_index.lifecycle.model import InventoryFile

    context = replace(
        _context(tmp_path),
        inventory_files=(InventoryFile("only/inventory.py", "c" * 64, "python", True),),
    )

    with pytest.raises(IRValidationError, match="outside_inventory"):
        from hermes_cli.hades_index.lifecycle.builder import GraphBuilder

        GraphBuilder(generated_at=lambda: "2026-07-19T12:00:00Z").build(
            context, (_valid_result(),)
        )


def test_excluded_path_count_is_carried_from_typed_inventory_context(tmp_path):
    from hermes_cli.hades_index.lifecycle.model import InventoryFile

    result = _empty_result()
    context = replace(
        _context(tmp_path),
        inventory_files=(InventoryFile("kept.py", "d" * 64, "python", True),),
        excluded_path_count=3,
    )
    from hermes_cli.hades_index.lifecycle.builder import GraphBuilder

    artifact = GraphBuilder(generated_at=lambda: "2026-07-19T12:00:00Z").build(
        context, (result,)
    )

    assert artifact.graph_contract.coverage.scope.excluded_path_count == 3


def test_semantic_resource_merges_all_occurrence_evidence_under_permutation(tmp_path):
    result = _resolved_result()
    first = result.effects[0]
    second = replace(
        first,
        local_key=_complex_key("effect", "second/resource/occurrence"),
        locator=replace(first.locator, structural_path="body/effect/second"),
    )
    variants = (
        replace(result, effects=_sort((first, second))),
        replace(result, effects=_sort((second, first))),
    )
    artifacts = [_build(tmp_path, item) for item in variants]
    resources = [
        next(node for node in artifact.nodes if node.kind is NodeKind.QUERY)
        for artifact in artifacts
    ]

    assert artifact_to_payload(artifacts[0]) == artifact_to_payload(artifacts[1])
    assert len(resources[0].evidence.supporting) == 1


def test_conflicting_boundary_node_is_fatal_under_every_input_permutation(tmp_path):
    from hermes_cli.hades_index.lifecycle.model import BoundaryTarget, EdgeFactIR

    result = _resolved_result()
    declaration = result.declarations[0]
    locator = declaration.locator
    descriptor = FrameworkBoundaryDescriptor(
        "fastapi", "middleware", "first", locator, _complex_evidence(locator)
    )
    first = EdgeFactIR(
        _complex_key("edge_fact", "boundary/first"),
        declaration.local_key,
        BoundaryTarget(descriptor),
        Relation.ROUTES_TO,
        EdgeFlow.ALWAYS,
        None,
        None,
        None,
        None,
        0,
        locator,
        _complex_evidence(locator),
    )
    second = replace(
        first,
        local_key=_complex_key("edge_fact", "boundary/second"),
        target=BoundaryTarget(replace(descriptor, public_name="second")),
    )
    for values in ((first, second), (second, first)):
        conflicting = replace(result, edge_facts=_sort(values))
        conflicting.validate()
        with pytest.raises(IRValidationError, match="semantic_collision"):
            _build(tmp_path, conflicting)


@pytest.mark.parametrize(
    "exception_types",
    (("TypeA", "TypeB"), ("TypeB", "TypeA")),
)
def test_unhandled_exception_terminal_collision_is_fatal_in_both_orders(
    tmp_path, exception_types
):
    from hermes_cli.hades_index.lifecycle.model import (
        ControlKind,
        LocalNodeTarget,
        Terminal,
        TerminalKind,
    )

    result = _control_flow_fixture(workers=2, symbol_resolution_full=True)
    result = replace(result, entrypoints=(_public_api_entrypoint(result),))
    workers = tuple(
        sorted(
            (item for item in result.declarations if item.name == "worker"),
            key=lambda item: item.local_key,
        )
    )
    base_invocation = result.edge_facts[0]
    invocations = (
        replace(base_invocation, target=LocalNodeTarget(workers[0].local_key)),
        replace(
            base_invocation,
            local_key=_complex_key("edge_fact", "body/call/alternate"),
            target=LocalNodeTarget(workers[1].local_key),
        ),
    )
    terminals = []
    exceptional_workers = {}
    exceptional_blocks = {}
    for worker, exception_type in zip(workers, exception_types, strict=True):
        terminal = Terminal(
            _complex_key(
                "terminal", f"{worker.locator.structural_path}/{exception_type}"
            ),
            worker.entry_block_key,
            TerminalKind.EXCEPTION,
            None,
            exception_type,
            replace(
                worker.locator,
                structural_path=(
                    f"{worker.locator.structural_path}/throw/{exception_type}"
                ),
            ),
        )
        terminals.append(terminal)
        exceptional_workers[worker.local_key] = replace(
            worker,
            normal_exit_block_keys=(),
            exception_exit_block_keys=(worker.entry_block_key,),
        )
        entry = next(
            item for item in result.blocks if item.local_key == worker.entry_block_key
        )
        exceptional_blocks[entry.local_key] = replace(
            entry,
            control_kind=ControlKind.THROW,
            successors=(ReturnSuccessor(terminal.local_key, 0),),
        )

    conflicting = replace(
        result,
        declarations=_sort(
            exceptional_workers.get(item.local_key, item)
            for item in result.declarations
        ),
        blocks=_sort(
            exceptional_blocks.get(item.local_key, item) for item in result.blocks
        ),
        call_sites=(replace(result.call_sites[0], exception_scope_key=None),),
        edge_facts=_sort(invocations),
        terminals=_sort((*result.terminals, *terminals)),
    )
    conflicting.validate()

    with pytest.raises(IRValidationError, match="semantic_collision"):
        _build(tmp_path, conflicting)
