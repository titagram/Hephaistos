"""Adapter-author contract for the frozen graph lifecycle extraction IR."""

from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from hermes_cli.hades_graph_contract import canonical_json_bytes
from hermes_cli.hades_index.lifecycle.model import (
    AdapterDiagnostic,
    AdapterResult,
    AlwaysSuccessor,
    AstLocatorIR,
    AsyncSuccessor,
    BasicBlock,
    BlockEffectSource,
    BoundaryTarget,
    BranchArm,
    BranchSuccessor,
    CallSite,
    CallSiteEffectSource,
    CallSiteSubjectIR,
    CandidateSetKnowledge,
    ConditionIR,
    ConditionPolarity,
    ConfigLocatorIR,
    ControlKind,
    CoverageCapability,
    CoverageEvent,
    CoverageOutcome,
    DeclarationIdentityKind,
    EdgeFactIR,
    EdgeFlow,
    EdgeSubjectIR,
    EvidenceOrigin,
    Effect,
    EffectKind,
    EntrypointCandidate,
    EntrypointKind,
    ExecutableDeclaration,
    ExceptionScope,
    ExceptionSuccessor,
    FileLocatorIR,
    FrameworkBoundaryDescriptor,
    FrameworkLocalTarget,
    FrameworkPipelineSegment,
    IREvidence,
    IRValidationError,
    LocalNodeTarget,
    LoopSuccessor,
    MethodSemantics,
    MatchConstraints,
    Modifier,
    NodeKind,
    ParameterIR,
    Priority,
    Relation,
    ResolutionKind,
    ReturnSuccessor,
    SourceLocationIR,
    StructureIR,
    StructureKind,
    StructureSubtype,
    TargetExpressionKind,
    Terminal,
    TerminalKind,
    TriggerKind,
    UnresolvedFact,
    local_record_key,
    successor_from_json,
    successor_to_json,
)


_DIGEST = "a" * 64


def _key(
    family: str,
    locator_kind: str = "ast",
    structural: str = "body/0",
    ordinal: int = 0,
) -> str:
    return local_record_key(
        "python", "src/app.py", family, locator_kind, structural, ordinal
    )


def _location() -> SourceLocationIR:
    return SourceLocationIR(
        path="src/app.py", start_line=1, end_line=2, file_sha256=_DIGEST
    )


def _ast(path: str = "body/0", ordinal: int = 0) -> AstLocatorIR:
    return AstLocatorIR(
        source_location=_location(), structural_path=path, ordinal=ordinal
    )


def _config(path: str = "routes/0", ordinal: int = 0) -> ConfigLocatorIR:
    return ConfigLocatorIR(
        source_location=_location(), structural_pointer=path, ordinal=ordinal
    )


def _evidence(
    locator: FileLocatorIR | AstLocatorIR | ConfigLocatorIR | None = None,
) -> IREvidence:
    return IREvidence(
        origin=EvidenceOrigin.VERIFIED_FROM_CODE,
        extractor="tree_sitter.python",
        locator=locator or _ast(),
        inference_rule=None,
    )


def _records() -> dict[str, object]:
    decl = _key("executable_declaration")
    entry = _key("basic_block", structural="body/entry")
    body = _key("basic_block", structural="body/body")
    catch = _key("basic_block", structural="body/catch")
    terminal = _key("terminal", structural="body/return")
    branch = _key("structure", structural="body/if")
    call_structure = _key("structure", structural="body/call")
    exception_structure = _key("structure", structural="body/try")
    call_site = _key("call_site", structural="body/call")
    edge = _key("edge_fact", structural="body/call")
    exception_scope = _key("exception_scope", structural="body/try")
    effect = _key("effect", structural="body/effect")
    segment = _key(
        "framework_pipeline_segment", structural="routes/0", locator_kind="config"
    )
    unresolved = _key("unresolved_fact", structural="routes/0", locator_kind="config")

    declaration = ExecutableDeclaration(
        local_key=decl,
        language="python",
        declaration_kind=NodeKind.FUNCTION,
        identity_kind=DeclarationIdentityKind.NAMED,
        owner_declaration_key=None,
        name="handler",
        qualified_name="app.handler",
        namespace="app",
        modifiers=(Modifier.PUBLIC,),
        parameters=(ParameterIR(0, "request", "Request", False, False, False),),
        return_type="Response",
        locator=_ast("body/handler"),
        entry_block_key=entry,
        normal_exit_block_keys=(body,),
        exception_exit_block_keys=(catch,),
    )
    blocks = tuple(
        sorted(
            (
                BasicBlock(
                    local_key=body,
                    declaration_key=decl,
                    control_kind=ControlKind.STRAIGHT_LINE,
                    ordinal=1,
                    locator=_ast("body/body"),
                    successors=(ReturnSuccessor(terminal_local_key=terminal, order=0),),
                ),
                BasicBlock(
                    local_key=catch,
                    declaration_key=decl,
                    control_kind=ControlKind.CATCH,
                    ordinal=2,
                    locator=_ast("body/catch"),
                    successors=(AlwaysSuccessor(target_block_key=body, order=0),),
                ),
                BasicBlock(
                    local_key=entry,
                    declaration_key=decl,
                    control_kind=ControlKind.ENTRY,
                    ordinal=0,
                    locator=_ast("body/entry"),
                    successors=(AlwaysSuccessor(target_block_key=body, order=0),),
                ),
            ),
            key=lambda item: item.local_key,
        )
    )
    structures = tuple(
        sorted(
            (
                StructureIR(
                    local_key=branch,
                    kind=StructureKind.BRANCH_GROUP,
                    owner_declaration_key=decl,
                    structural_path="body/if",
                    ordinal=0,
                    subtype=StructureSubtype.IF,
                    continuation_block_key=body,
                    parent_structure_key=None,
                    evidence=_evidence(),
                ),
                StructureIR(
                    local_key=call_structure,
                    kind=StructureKind.CALL_SITE,
                    owner_declaration_key=decl,
                    structural_path="body/call",
                    ordinal=0,
                    subtype=StructureSubtype.CALL,
                    continuation_block_key=body,
                    parent_structure_key=None,
                    evidence=_evidence(),
                ),
                StructureIR(
                    local_key=exception_structure,
                    kind=StructureKind.EXCEPTION_SCOPE,
                    owner_declaration_key=decl,
                    structural_path="body/try",
                    ordinal=0,
                    subtype=StructureSubtype.TRY_CATCH,
                    continuation_block_key=None,
                    parent_structure_key=None,
                    evidence=_evidence(),
                ),
            ),
            key=lambda item: item.local_key,
        )
    )
    call = CallSite(
        local_key=call_site,
        caller_declaration_key=decl,
        source_block_key=entry,
        locator=_ast("body/call"),
        target_expression_kind=TargetExpressionKind.DIRECT_FUNCTION,
        lexical_target="service.load",
        fully_qualified_target="app.service.load",
        receiver_type=None,
        argument_count=1,
        continuation_block_key=body,
        exception_scope_key=exception_structure,
    )
    edge_fact = EdgeFactIR(
        local_key=edge,
        source_node_local_key=decl,
        target=LocalNodeTarget(local_key=decl),
        relation=Relation.ROUTES_TO,
        flow=EdgeFlow.ALWAYS,
        condition=None,
        branch_group_key=None,
        call_site_key=None,
        exception_scope_key=None,
        order=0,
        locator=_config(),
        evidence=_evidence(_config()),
    )
    exception = ExceptionScope(
        local_key=exception_scope,
        declaration_key=decl,
        locator=_ast("body/try"),
        caught_type_names=("RuntimeError",),
        catch_block_keys=(catch,),
        finally_block_key=None,
        parent_scope_key=None,
    )
    terminal_record = Terminal(
        local_key=terminal,
        source_block_key=body,
        kind=TerminalKind.RESPONSE,
        public_status=200,
        exception_type=None,
        locator=_ast("body/return"),
    )
    effect_record = Effect(
        local_key=effect,
        source=BlockEffectSource(local_key=body),
        kind=EffectKind.DATA_READ,
        operation="select",
        public_resource_name="users",
        protocol=None,
        locator=_ast("body/effect"),
    )
    boundary = FrameworkBoundaryDescriptor(
        framework="fastapi",
        role="middleware",
        public_name="cors",
        locator=_config(),
        evidence=_evidence(_config()),
    )
    framework_segment = FrameworkPipelineSegment(
        local_key=segment,
        framework_role="middleware",
        pipeline_order=0,
        target=FrameworkLocalTarget(local_key=decl),
        success_successor=AlwaysSuccessor(target_block_key=entry, order=0),
        short_circuit_successors=(),
        evidence=_evidence(_config()),
    )
    unresolved_fact = UnresolvedFact(
        local_key=unresolved,
        subject=EdgeSubjectIR(local_key=edge),
        resolution_kind=ResolutionKind.ENTRYPOINT_HANDLER,
        candidate_set_knowledge=CandidateSetKnowledge.NOT_APPLICABLE,
        reason_code="entrypoint_unresolved",
        question="Which handler receives this route?",
        evidence_requirements=("inspect_route_configuration",),
        source_locators=(_config(),),
        candidate_target_local_keys=(),
        candidate_edge_local_keys=(),
        priority=Priority.HIGH,
        impact="The request lifecycle cannot reach a verified handler.",
    )
    entrypoint = EntrypointCandidate(
        kind=EntrypointKind.HTTP_ROUTE,
        framework="fastapi",
        method_semantics=MethodSemantics.EXPLICIT,
        methods=("GET",),
        public_path="/health",
        public_name=None,
        trigger=TriggerKind.HTTP,
        match_constraints=MatchConstraints(
            host=None, schemes=("https",), condition_hash=None
        ),
        registration_locator=_config(),
        handler_local_key=None,
        unresolved_fact_local_key=unresolved,
        framework_segment_keys=(segment,),
        evidence=_evidence(_config()),
    )
    return {
        "declarations": (declaration,),
        "blocks": blocks,
        "branch_arms": (
            BranchArm(
                branch,
                entry,
                body,
                ConditionPolarity.TRUE,
                ConditionIR("predicate", "is_admin", _DIGEST, ConditionPolarity.TRUE),
                0,
            ),
        ),
        "structures": structures,
        "call_sites": (call,),
        "edge_facts": (edge_fact,),
        "exception_scopes": (exception,),
        "terminals": (terminal_record,),
        "effects": (effect_record,),
        "framework_segments": (framework_segment,),
        "entrypoints": (entrypoint,),
        "unresolved_facts": (unresolved_fact,),
        "coverage_events": (
            CoverageEvent(
                "python",
                CoverageCapability.CONTROL_FLOW,
                CoverageOutcome.FULL,
                None,
                None,
                1,
                0,
            ),
        ),
        "diagnostics": (AdapterDiagnostic("info", "adapter_complete", _location()),),
        "boundary": boundary,
    }


def _valid_result() -> AdapterResult:
    records = _records()
    return AdapterResult(**{
        key: value for key, value in records.items() if key != "boundary"
    })


@pytest.mark.parametrize(
    "successor",
    [
        AlwaysSuccessor(target_block_key="b2", order=0),
        BranchSuccessor(target_block_key="b2", branch_arm_key="arm", order=0),
        ExceptionSuccessor(
            target_block_key="catch",
            exception_scope_key="scope",
            caught_type_name="RuntimeError",
            order=0,
        ),
        LoopSuccessor(target_block_key="loop", loop_role="back", order=0),
        AsyncSuccessor(target_local_key="job", dispatch_kind="job", order=0),
        ReturnSuccessor(terminal_local_key="return", order=0),
    ],
)
def test_successor_union_round_trips(successor: object) -> None:
    assert successor_from_json(successor_to_json(successor)) == successor


def test_all_ir_record_families_construct_and_validate_without_mutation() -> None:
    result = _valid_result()
    before = result
    assert result.validate() is None
    assert result == before


def test_file_ast_and_config_locators_round_trip_every_allowed_evidence_variant() -> (
    None
):
    file = FileLocatorIR(path="inventory.py", file_sha256=_DIGEST)
    ast = _ast()
    config = _config()
    assert file.kind == "file"
    assert ast.kind == "ast"
    assert config.kind == "config"
    assert _evidence(file).locator == file
    assert _evidence(ast).locator == ast
    assert _evidence(config).locator == config


def test_discriminated_targets_and_effect_sources_construct_every_variant() -> None:
    records = _records()
    boundary = records["boundary"]
    assert isinstance(boundary, FrameworkBoundaryDescriptor)
    assert BoundaryTarget(boundary).kind == "boundary"
    assert FrameworkLocalTarget(_DIGEST).kind == "local_node"
    assert CallSiteEffectSource(_DIGEST).kind == "call_site"


def test_local_key_uses_exact_nfc_jcs_preimage_without_absolute_paths() -> None:
    expected = hashlib.sha256(
        canonical_json_bytes({
            "language": "python",
            "path": "src/Caf\u00e9.py",
            "record_family": "basic_block",
            "locator_kind": "ast",
            "structural_path_or_pointer": "body/0",
            "ordinal": 0,
        })
    ).hexdigest()
    assert (
        local_record_key(
            "python", "src/Cafe\u0301.py", "basic_block", "ast", "body/0", 0
        )
        == expected
    )
    assert local_record_key(
        "python", "src/Caf\u00e9.py", "basic_block", "ast", "body/Cafe\u0301", 0
    ) == local_record_key(
        "python", "src/Caf\u00e9.py", "basic_block", "ast", "body/Caf\u00e9", 0
    )
    with pytest.raises(IRValidationError, match="safe source-relative"):
        local_record_key("python", "/private/app.py", "basic_block", "ast", "body/0", 0)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda result: replace(result, blocks=tuple(reversed(result.blocks))),
        lambda result: replace(
            result,
            blocks=(
                replace(
                    result.blocks[0],
                    successors=(ReturnSuccessor("z", 1), ReturnSuccessor("a", 0)),
                ),
            )
            + result.blocks[1:],
        ),
        lambda result: replace(
            result,
            declarations=(
                replace(
                    result.declarations[0],
                    parameters=(
                        ParameterIR(1, "b", None, False, False, False),
                        ParameterIR(0, "a", None, False, False, False),
                    ),
                ),
            ),
        ),
        lambda result: replace(
            result,
            declarations=(
                replace(
                    result.declarations[0], modifiers=(Modifier.STATIC, Modifier.PUBLIC)
                ),
            ),
        ),
    ],
)
def test_result_rejects_unsorted_tuple_contracts(mutation) -> None:
    with pytest.raises(IRValidationError):
        mutation(_valid_result()).validate()


def test_entrypoint_handler_and_unresolved_fact_are_exclusive_and_linked() -> None:
    result = _valid_result()
    entrypoint = result.entrypoints[0]
    with pytest.raises(IRValidationError, match="exactly one"):
        replace(
            result,
            entrypoints=(
                replace(entrypoint, handler_local_key=result.declarations[0].local_key),
            ),
        ).validate()
    with pytest.raises(IRValidationError, match="entrypoint_handler"):
        replace(
            result,
            unresolved_facts=(
                replace(
                    result.unresolved_facts[0],
                    resolution_kind=ResolutionKind.CALL_TARGET,
                ),
            ),
        ).validate()


def test_unresolved_candidate_knowledge_enforces_its_closed_empty_or_nonempty_contract() -> (
    None
):
    result = _valid_result()
    with pytest.raises(IRValidationError, match="complete"):
        replace(
            result.unresolved_facts[0],
            candidate_set_knowledge=CandidateSetKnowledge.COMPLETE,
        )


def test_result_rejects_unresolved_or_structure_references_that_do_not_resolve() -> (
    None
):
    result = _valid_result()
    with pytest.raises(IRValidationError, match="unresolved"):
        replace(
            result,
            entrypoints=(
                replace(result.entrypoints[0], unresolved_fact_local_key="b" * 64),
            ),
        ).validate()
    with pytest.raises(IRValidationError, match="structure"):
        replace(
            result, edge_facts=(replace(result.edge_facts[0], call_site_key="b" * 64),)
        ).validate()


def test_file_locator_is_limited_to_inventory_file_facts() -> None:
    result = _valid_result()
    with pytest.raises(IRValidationError, match="locator"):
        replace(
            result,
            declarations=(
                replace(
                    result.declarations[0], locator=FileLocatorIR("src/app.py", _DIGEST)
                ),
            ),
        ).validate()


def test_closed_enums_and_nullable_fields_reject_invalid_values() -> None:
    with pytest.raises(IRValidationError):
        BasicBlock("x", "d", "not-a-control-kind", 0, _ast(), ())  # type: ignore[arg-type]
    with pytest.raises(IRValidationError):
        Terminal("x", "block", TerminalKind.RESPONSE, None, "Exception", _ast())
    with pytest.raises(IRValidationError):
        CallSite(
            "x",
            "d",
            "b",
            _ast(),
            TargetExpressionKind.DIRECT_FUNCTION,
            None,
            None,
            None,
            0,
            "next",
            None,
        )
