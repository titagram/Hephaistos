from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from hermes_cli.hades_graph_config import load_hades_graph_index_config
from hermes_cli.hades_graph_v2 import artifact_to_payload, validate_artifact
from hermes_cli.hades_graph_v2.model import (
    AsyncContext,
    BackboneRole,
    EdgeFlow,
    EntrypointKind,
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
    EntrypointCandidate,
    ExtractionContext,
    MatchConstraints,
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
    )


def _resolved_result():
    result = _valid_result()
    candidate = result.entrypoints[0]
    return replace(
        result,
        edge_facts=(),
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
    return replace(result, entrypoints=(_public_api_entrypoint(result),))


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
    first_structure = replace(
        call_structure, continuation_block_key=merge.local_key
    )
    first_edge = replace(
        result.edge_facts[0],
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
        worker.local_key,
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
        call_sites=tuple(sorted((first_site, second_site), key=lambda item: item.local_key)),
        structures=_sort(
            [
                first_structure
                if item.local_key == first_structure.local_key
                else item
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
    assert len([flow for flow in artifact.flows if flow.kind.value != "async_flow"]) == 1
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
    invocation = next(edge for edge in edges.values() if edge.relation is Relation.INVOKES)
    returns = [
        edge
        for edge in edges.values()
        if edge.relation is Relation.RETURNS_TO
    ]
    assert returns
    assert {edge.call_site_id for edge in returns} == {invocation.call_site_id}
    exception = next(
        edges[step.edge_id]
        for step in steps
        if edges[step.edge_id].flow is EdgeFlow.EXCEPTION
    )
    assert exception.exception_scope_id is not None
    assert next(
        structure.id
        for structure in artifact.structures
        if structure.kind is StructureKind.EXCEPTION_SCOPE
    ) == exception.exception_scope_id


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
    assert next(node for node in artifact.nodes if node.id == frontier_target).kind is NodeKind.UNKNOWN_BOUNDARY
    assert all(edges[step.edge_id].source_id != frontier_target for step in artifact.flow_steps)
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
            and next(edge for edge in artifact.edges if edge.id == other.edge_id).source_id
            == next(edge for edge in artifact.edges if edge.id == step.edge_id).target_id
            for other in artifact.flow_steps
        )
        for step in artifact.flow_steps
    )
    validate_artifact(artifact)


def test_verified_async_dispatch_links_child_without_inline_child_steps(tmp_path):
    result = _complex_result()
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
    assert all(
        step.async_context is AsyncContext.LINKED_ASYNC
        for step in artifact.flow_steps
        if step.flow_id == child.id
    )
    assert all(
        not (
            step.flow_id == parent.id
            and edges[step.edge_id].source_id == child.root_node_id
        )
        for step in artifact.flow_steps
    )


def test_self_and_mutual_recursion_reach_finite_fixed_points(tmp_path):
    for mutual in (False, True):
        artifact = _build(tmp_path, _recursive_result(mutual=mutual))
        edges = {edge.id: edge for edge in artifact.edges}
        sync = next(
            flow for flow in artifact.flows if flow.kind.value != "async_flow"
        )
        recursion = [
            step
            for step in artifact.flow_steps
            if step.flow_id == sync.id
            and edges[step.edge_id].relation is Relation.INVOKES
            and step.backbone_role is BackboneRole.LOOP
        ]
        assert len(recursion) == (2 if mutual else 1)
        assert len(artifact.flow_steps) <= len(artifact.edges) * len(Stage) * len(artifact.flows)
        validate_artifact(artifact)


def test_terminal_and_async_count_knowledge_closes_over_represented_steps(tmp_path):
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
