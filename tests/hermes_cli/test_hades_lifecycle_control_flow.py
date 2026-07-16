"""Bounded CFG, call-target and Graphify contracts for the lifecycle IR.

These tests deliberately use tiny language-neutral fixtures.  The parser is
responsible for recognizing syntax; the CFG and resolver must never enumerate
runtime paths or silently select one dynamic target.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from hermes_cli.hades_graph_v2.model import (
    CandidateSetKnowledge,
    ConditionPolarity,
    EdgeFlow,
    EvidenceOrigin,
    NodeKind,
    Priority,
    Relation,
    ResolutionKind,
    StructureKind,
    StructureSubtype,
)
from hermes_cli.hades_index.lifecycle.model import (
    AdapterResult,
    AlwaysSuccessor,
    AstLocatorIR,
    AsyncSuccessor,
    BasicBlock,
    BranchArm,
    BranchSuccessor,
    CallSite,
    CallSiteSubjectIR,
    ConditionIR,
    ControlKind,
    CoverageCapability,
    CoverageEvent,
    CoverageOutcome,
    DeclarationIdentityKind,
    EdgeFactIR,
    ExecutableDeclaration,
    ExceptionScope,
    ExceptionSuccessor,
    IREvidence,
    LocalNodeTarget,
    LoopRole,
    LoopSuccessor,
    Modifier,
    ParameterIR,
    ReturnSuccessor,
    SourceLocationIR,
    StructureIR,
    TargetExpressionKind,
    Terminal,
    TerminalKind,
    UnresolvedFact,
    local_record_key,
)


_DIGEST = "a" * 64


def _key(family: str, structural: str, ordinal: int = 0) -> str:
    return local_record_key("python", "src/app.py", family, "ast", structural, ordinal)


def _location() -> SourceLocationIR:
    return SourceLocationIR("src/app.py", 1, 99, _DIGEST)


def _ast(structural: str, ordinal: int = 0) -> AstLocatorIR:
    return AstLocatorIR(_location(), structural, ordinal)


def _evidence(
    locator: AstLocatorIR | None = None,
    *,
    origin: EvidenceOrigin = EvidenceOrigin.VERIFIED_FROM_CODE,
) -> IREvidence:
    return IREvidence(
        origin=origin,
        extractor="graphify.candidates"
        if origin is EvidenceOrigin.INFERRED
        else "tree-sitter.python",
        locator=locator or _ast("body/0"),
        inference_rule="graphify_candidate"
        if origin is EvidenceOrigin.INFERRED
        else None,
    )


def _sort(records):
    return tuple(sorted(records, key=lambda item: item.local_key))


def _fixture(
    *,
    call_kind: TargetExpressionKind = TargetExpressionKind.DIRECT_FUNCTION,
    target: str | None = "app.worker",
    workers: int = 1,
    unresolved: bool = False,
    symbol_resolution_full: bool = False,
) -> AdapterResult:
    """A valid declaration with branch, loop, exception, return and async facts."""

    handler = _key("executable_declaration", "declaration/handler")
    worker_keys = tuple(
        _key("executable_declaration", f"declaration/worker/{index}")
        for index in range(workers)
    )
    entry = _key("basic_block", "body/entry")
    branch = _key("basic_block", "body/if")
    true_block = _key("basic_block", "body/true")
    false_block = _key("basic_block", "body/false")
    merge = _key("basic_block", "body/merge")
    loop = _key("basic_block", "body/loop")
    catch = _key("basic_block", "body/catch")
    finally_block = _key("basic_block", "body/finally")
    terminal_key = _key("terminal", "body/return")
    branch_group = _key("structure", "body/if")
    call_structure = _key("structure", "body/call")
    exception_structure = _key("structure", "body/try")
    scope = _key("exception_scope", "body/try")
    call_site = _key("call_site", "body/call")
    subject_edge = _key("edge_fact", "body/call")
    unresolved_key = _key("unresolved_fact", "body/call")

    declarations = [
        ExecutableDeclaration(
            local_key=handler,
            language="python",
            declaration_kind=NodeKind.FUNCTION,
            identity_kind=DeclarationIdentityKind.NAMED,
            owner_declaration_key=None,
            name="handler",
            qualified_name="app.handler",
            namespace="app",
            modifiers=(Modifier.PUBLIC,),
            parameters=(ParameterIR(0, "request", None, False, False, False),),
            return_type=None,
            locator=_ast("declaration/handler"),
            entry_block_key=entry,
            normal_exit_block_keys=tuple(sorted((finally_block,))),
            exception_exit_block_keys=(),
        )
    ]
    for index, key in enumerate(worker_keys):
        declarations.append(
            ExecutableDeclaration(
                local_key=key,
                language="python",
                declaration_kind=NodeKind.FUNCTION,
                identity_kind=DeclarationIdentityKind.NAMED,
                owner_declaration_key=None,
                name="worker",
                qualified_name=(
                    "app.worker" if index == 0 else f"app.alt.worker{index}"
                ),
                namespace="app",
                modifiers=(),
                parameters=(),
                return_type=None,
                locator=_ast(f"declaration/worker/{index}"),
                entry_block_key=entry,
                normal_exit_block_keys=tuple(sorted((finally_block,))),
                exception_exit_block_keys=(),
            )
        )
    true_condition = ConditionIR(
        "predicate", "is_admin", _DIGEST, ConditionPolarity.TRUE
    )
    false_condition = ConditionIR(
        "predicate", "not_is_admin", _DIGEST, ConditionPolarity.FALSE
    )
    blocks = [
        BasicBlock(
            entry,
            handler,
            ControlKind.ENTRY,
            0,
            _ast("body/entry"),
            (AlwaysSuccessor(branch, 0),),
        ),
        BasicBlock(
            branch,
            handler,
            ControlKind.BRANCH,
            1,
            _ast("body/if"),
            (
                BranchSuccessor(true_block, branch_group, 0),
                BranchSuccessor(false_block, branch_group, 1),
            ),
        ),
        BasicBlock(
            true_block,
            handler,
            ControlKind.STRAIGHT_LINE,
            2,
            _ast("body/true"),
            (AlwaysSuccessor(merge, 0),),
        ),
        BasicBlock(
            false_block,
            handler,
            ControlKind.STRAIGHT_LINE,
            3,
            _ast("body/false"),
            (ExceptionSuccessor(catch, exception_structure, "Exception", 0),),
        ),
        BasicBlock(
            merge,
            handler,
            ControlKind.MERGE,
            4,
            _ast("body/merge"),
            (
                AlwaysSuccessor(loop, 0),
                AsyncSuccessor(worker_keys[0], "task", 1),
            ),
        ),
        BasicBlock(
            loop,
            handler,
            ControlKind.LOOP_HEADER,
            5,
            _ast("body/loop"),
            (
                LoopSuccessor(loop, LoopRole.BACK, 0),
                LoopSuccessor(finally_block, LoopRole.EXIT, 1),
            ),
        ),
        BasicBlock(
            catch,
            handler,
            ControlKind.CATCH,
            6,
            _ast("body/catch"),
            (AlwaysSuccessor(finally_block, 0),),
        ),
        BasicBlock(
            finally_block,
            handler,
            ControlKind.FINALLY,
            7,
            _ast("body/finally"),
            (ReturnSuccessor(terminal_key, 0),),
        ),
    ]
    structures = [
        StructureIR(
            branch_group,
            StructureKind.BRANCH_GROUP,
            handler,
            "body/if",
            0,
            StructureSubtype.IF,
            merge,
            None,
            _evidence(_ast("body/if")),
        ),
        StructureIR(
            call_structure,
            StructureKind.CALL_SITE,
            handler,
            "body/call",
            0,
            StructureSubtype.CALL,
            merge,
            None,
            _evidence(_ast("body/call")),
        ),
        StructureIR(
            exception_structure,
            StructureKind.EXCEPTION_SCOPE,
            handler,
            "body/try",
            0,
            StructureSubtype.TRY_CATCH_FINALLY,
            finally_block,
            None,
            _evidence(_ast("body/try")),
        ),
    ]
    call = CallSite(
        call_site,
        handler,
        merge,
        _ast("body/call"),
        call_kind,
        target,
        target,
        None,
        0,
        finally_block,
        exception_structure,
    )
    edges = [
        EdgeFactIR(
            subject_edge,
            handler,
            LocalNodeTarget(worker_keys[0]),
            Relation.INVOKES,
            EdgeFlow.ALWAYS,
            None,
            None,
            call_structure,
            None,
            0,
            _ast("body/call"),
            _evidence(_ast("body/call")),
        )
    ]
    unresolved_facts = []
    if unresolved:
        unresolved_facts.append(
            UnresolvedFact(
                unresolved_key,
                CallSiteSubjectIR(call_site),
                ResolutionKind.CALL_TARGET,
                CandidateSetKnowledge.NOT_APPLICABLE,
                "dynamic_dispatch",
                "Which implementation can this call invoke?",
                ("inspect_receiver_assignments",),
                (_ast("body/call"),),
                (),
                (),
                Priority.HIGH,
                "May change execution after this call.",
            )
        )
    result = AdapterResult(
        declarations=_sort(declarations),
        blocks=_sort(blocks),
        branch_arms=tuple(
            sorted(
                (
                    BranchArm(
                        branch_group,
                        branch,
                        true_block,
                        ConditionPolarity.TRUE,
                        true_condition,
                        0,
                    ),
                    BranchArm(
                        branch_group,
                        branch,
                        false_block,
                        ConditionPolarity.FALSE,
                        false_condition,
                        1,
                    ),
                ),
                key=lambda arm: (arm.branch_local_key, arm.arm_ordinal),
            )
        ),
        structures=_sort(structures),
        call_sites=(call,),
        edge_facts=_sort(edges),
        exception_scopes=(
            ExceptionScope(
                scope,
                handler,
                _ast("body/try"),
                ("Exception",),
                (catch,),
                finally_block,
                None,
            ),
        ),
        terminals=(
            Terminal(
                terminal_key,
                finally_block,
                TerminalKind.RESPONSE,
                200,
                None,
                _ast("body/return"),
            ),
        ),
        effects=(),
        framework_segments=(),
        entrypoints=(),
        unresolved_facts=_sort(unresolved_facts),
        coverage_events=(
            CoverageEvent(
                "python",
                CoverageCapability.SYMBOL_RESOLUTION,
                CoverageOutcome.FULL,
                None,
                None,
                workers + 1,
                0,
            ),
        )
        if symbol_resolution_full
        else (),
        diagnostics=(),
    )
    result.validate()
    return result


class _Node:
    def __init__(self, node_type: str, *, children=(), fields=None, row: int = 0):
        self.type = node_type
        self.children = tuple(children)
        self._fields = fields or {}
        self.start_byte = 0
        self.end_byte = 0
        self.start_point = (row, 0)
        self.end_point = (row, 0)

    def child_by_field_name(self, name: str):
        return self._fields.get(name)


class _Parser:
    def __init__(self, root: _Node):
        self.root = root

    def parse(self, _source: bytes):
        return type("Tree", (), {"root_node": self.root})()


@pytest.mark.parametrize(
    ("language", "nodes"),
    [
        (
            "php",
            (
                "if_statement",
                "switch_statement",
                "return_statement",
                "try_statement",
                "catch_clause",
                "finally_clause",
                "foreach_statement",
                "await_expression",
                "function_call_expression",
            ),
        ),
        (
            "python",
            (
                "if_statement",
                "match_statement",
                "return_statement",
                "try_statement",
                "except_clause",
                "finally_clause",
                "for_statement",
                "await",
                "call",
            ),
        ),
        (
            "typescript",
            (
                "if_statement",
                "switch_statement",
                "return_statement",
                "try_statement",
                "catch_clause",
                "finally_clause",
                "for_statement",
                "await_expression",
                "call_expression",
            ),
        ),
    ],
)
def test_parser_returns_finite_typed_control_syntax_for_supported_languages(
    language, nodes
):
    from hermes_cli.hades_index.tree_sitter_adapter import TreeSitterAdapter

    root = _Node(
        "program",
        children=tuple(_Node(node, row=index) for index, node in enumerate(nodes)),
    )
    result = TreeSitterAdapter(
        parser_loader=lambda _language: _Parser(root)
    ).parse_bytes(b"safe source", path=f"src/sample.{language}", language=language)

    assert result.status == "parsed"
    assert result.syntax is not None
    kinds = {item.kind for item in result.syntax.controls}
    assert {
        "branch",
        "merge",
        "return",
        "catch",
        "finally",
        "loop",
        "async_dispatch",
        "call",
    } <= kinds
    assert len(result.syntax.controls) <= len(nodes) * 2


def test_parser_failure_is_a_typed_partial_coverage_event():
    from hermes_cli.hades_index.tree_sitter_adapter import TreeSitterAdapter

    result = TreeSitterAdapter(parser_loader=lambda _language: None).parse_bytes(
        b"def app(): pass", path="src/app.py", language="python"
    )

    assert result.status == "failed"
    assert result.syntax is None
    assert result.failure is not None and result.failure.code == "parser_unavailable"
    assert result.coverage_event.capability is CoverageCapability.CONTROL_FLOW
    assert result.coverage_event.outcome is CoverageOutcome.PARTIAL


def test_control_flow_is_finite_and_keeps_branch_return_exception_loop_and_async_boundaries():
    from hermes_cli.hades_index.lifecycle.control_flow import build_control_flow

    result = _fixture()
    control = build_control_flow(result)

    assert len(control.blocks) == len(result.blocks)
    assert {edge.kind for edge in control.edges} >= {
        "always",
        "async",
        "branch",
        "exception",
        "loop",
        "return",
    }
    assert control.terminal_keys == tuple(item.local_key for item in result.terminals)
    assert control.has_cycles is True
    assert control.path_count is None


def test_interprocedural_resolution_never_guesses_a_dynamic_target_or_unbounded_recursion():
    from hermes_cli.hades_index.lifecycle.interprocedural import (
        ResolutionDisposition,
        resolve_call_sites,
    )

    exact = resolve_call_sites((_fixture(),))
    assert exact.calls[0].disposition is ResolutionDisposition.EXACT
    assert len(exact.calls[0].target_declaration_keys) == 1

    dynamic = resolve_call_sites((
        _fixture(
            call_kind=TargetExpressionKind.DYNAMIC_MEMBER,
            target="worker",
            workers=2,
            symbol_resolution_full=True,
        ),
    ))
    assert dynamic.calls[0].disposition is ResolutionDisposition.EXHAUSTIVE_CANDIDATES
    assert dynamic.calls[0].candidate_set_knowledge is CandidateSetKnowledge.COMPLETE
    assert len(dynamic.calls[0].target_declaration_keys) == 2

    insufficient_coverage = resolve_call_sites((
        _fixture(
            call_kind=TargetExpressionKind.DYNAMIC_MEMBER, target="worker", workers=2
        ),
    ))
    assert (
        insufficient_coverage.calls[0].disposition
        is ResolutionDisposition.UNRESOLVED_FRONTIER
    )

    recursion = resolve_call_sites((_fixture(target="app.handler"),))
    assert recursion.calls[0].disposition is ResolutionDisposition.EXACT
    assert recursion.calls[0].target_declaration_keys

    unresolved = resolve_call_sites((
        _fixture(call_kind=TargetExpressionKind.REFLECTION, target="worker", workers=2),
    ))
    assert unresolved.calls[0].disposition is ResolutionDisposition.UNRESOLVED_FRONTIER
    assert unresolved.calls[0].target_declaration_keys == ()
    assert unresolved.calls[0].required_uncertainty is True


def test_graphify_is_off_by_default_and_can_only_add_bounded_incomplete_native_hints():
    from hermes_cli.hades_index.graphify_candidates import attach_graphify_hints

    result = _fixture(unresolved=True, workers=2)
    fact = result.unresolved_facts[0]
    worker_keys = tuple(
        item.local_key for item in result.declarations if item.name == "worker"
    )

    assert (
        attach_graphify_hints(result, {fact.local_key: worker_keys}, enabled=False)
        is result
    )
    enriched = attach_graphify_hints(
        result, {fact.local_key: worker_keys + ("missing",) * 30}, enabled=True
    )

    updated = next(
        item for item in enriched.unresolved_facts if item.local_key == fact.local_key
    )
    assert updated.candidate_set_knowledge is CandidateSetKnowledge.INCOMPLETE
    assert updated.candidate_target_local_keys == tuple(sorted(worker_keys))
    assert len(updated.candidate_edge_local_keys) == len(worker_keys) <= 20
    hints = [
        edge
        for edge in enriched.edge_facts
        if edge.local_key in updated.candidate_edge_local_keys
    ]
    assert all(
        edge.evidence.origin is EvidenceOrigin.INFERRED
        and edge.evidence.extractor.startswith("graphify.")
        for edge in hints
    )
    enriched.validate()


def test_graphify_never_creates_a_subject_without_native_uncertainty():
    from hermes_cli.hades_index.graphify_candidates import attach_graphify_hints

    result = _fixture(unresolved=False)
    assert (
        attach_graphify_hints(result, {"anything": ("anything",)}, enabled=True)
        is result
    )
