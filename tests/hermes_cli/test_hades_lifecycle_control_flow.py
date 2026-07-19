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
    ExceptionCatchArm,
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
    worker_entry_keys = tuple(
        _key("basic_block", f"declaration/worker/{index}/entry")
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
                entry_block_key=worker_entry_keys[index],
                normal_exit_block_keys=(worker_entry_keys[index],),
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
                BranchSuccessor(true_block, branch_group, 0, 0),
                BranchSuccessor(false_block, branch_group, 1, 1),
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
            (ExceptionSuccessor(catch, scope, "Exception", 0),),
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
        *(
            BasicBlock(
                worker_entry_keys[index],
                key,
                ControlKind.ENTRY,
                0,
                _ast(f"declaration/worker/{index}/entry"),
                (),
            )
            for index, key in enumerate(worker_keys)
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
                exception_structure,
                handler,
                _ast("body/try"),
                (ExceptionCatchArm("Exception", catch, 0),),
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
def test_cfg_matrix(language, nodes):
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


def test_missing_parser_partial():
    from hermes_cli.hades_index.tree_sitter_adapter import TreeSitterAdapter

    result = TreeSitterAdapter(parser_loader=lambda _language: None).parse_bytes(
        b"def app(): pass", path="src/app.py", language="python"
    )

    assert result.status == "failed"
    assert result.syntax is None
    assert result.failure is not None and result.failure.code == "parser_unavailable"
    assert result.coverage_event.capability is CoverageCapability.CONTROL_FLOW
    assert result.coverage_event.outcome is CoverageOutcome.PARTIAL


def test_return_controls_retain_nearest_callable_owner_and_byte_span():
    from hermes_cli.hades_index.tree_sitter_adapter import TreeSitterAdapter

    source = b"""export async function middleware() {
  function unused({ value }) { return NextResponse.redirect('/ghost') }
  for await (const item of items) { if (item) return NextResponse.redirect('/loop') }
  return NextResponse.next()
}
"""

    result = TreeSitterAdapter().parse_bytes(
        source, path="middleware.ts", language="typescript"
    )

    assert result.status == "parsed"
    assert result.syntax is not None
    returns = [
        control for control in result.syntax.controls if control.kind == "return"
    ]
    assert len(returns) == 3
    assert returns[0].owner_structural_path != returns[1].owner_structural_path
    assert returns[1].owner_structural_path == returns[2].owner_structural_path
    assert all(
        source[item.start_byte : item.end_byte].lstrip().startswith(b"return")
        for item in returns
    )


def test_typescript_canary_covers_tsx_and_tsx_files_use_the_tsx_grammar():
    from hermes_cli.hades_index.tree_sitter_adapter import (
        RequiredParserUnavailable,
        TreeSitterAdapter,
    )

    loaded: list[str] = []

    class CanaryParser:
        def parse(self, _source: bytes):
            root = type("Root", (), {"has_error": False})()
            return type("Tree", (), {"root_node": root})()

    adapter = TreeSitterAdapter(
        parser_loader=lambda language: loaded.append(language) or CanaryParser()
    )
    adapter.require_languages(("typescript",))

    assert loaded == ["typescript", "tsx"]

    broken_tsx = TreeSitterAdapter(
        parser_loader=lambda language: (
            CanaryParser() if language == "typescript" else None
        )
    )
    with pytest.raises(RequiredParserUnavailable) as raised:
        broken_tsx.require_languages(("typescript",))

    assert raised.value.languages == ("typescript",)

    source = b"export default function Page() { return <main>Hello</main> }"
    result = TreeSitterAdapter().parse_bytes(
        source, path="app/page.tsx", language="typescript"
    )

    assert result.status == "parsed"
    assert result.syntax is not None
    assert result.syntax.language == "typescript"


def test_tree_sitter_traversal_stays_stable_with_large_route_inventory(tmp_path):
    from hermes_cli.hades_index import typescript
    from hermes_cli.hades_index.tree_sitter_adapter import TreeSitterAdapter

    source_dir = tmp_path / "src"
    source_dir.mkdir()
    path = source_dir / "routes.ts"
    path.write_text(
        "import express from 'express';\n"
        "const router = express.Router();\n"
        + "".join(
            f"router.get('/route/{index}', handler{index});\n" for index in range(501)
        ),
        encoding="utf-8",
    )
    legacy_graph = typescript.build_graph(
        tmp_path,
        [path],
        [],
        truncated=False,
        max_symbols=5_000,
        max_edges=10_000,
        max_file_bytes=512_000,
    )

    result = TreeSitterAdapter().parse_file(
        path,
        relative_path="src/routes.ts",
        language="typescript",
        max_bytes=512_000,
    )

    assert len(legacy_graph["routes"]) == 500
    assert result.status == "parsed"
    assert result.syntax is not None
    assert sum(control.kind == "call" for control in result.syntax.controls) == 502


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


@pytest.mark.parametrize(
    "call_kind",
    (
        TargetExpressionKind.DYNAMIC_MEMBER,
        TargetExpressionKind.REFLECTION,
        TargetExpressionKind.EVAL,
    ),
)
def test_dynamic_reflection_and_eval_never_become_exact_from_one_visible_target(
    call_kind: TargetExpressionKind,
):
    from hermes_cli.hades_index.lifecycle.interprocedural import (
        ResolutionDisposition,
        resolve_call_sites,
    )

    unproven = resolve_call_sites((_fixture(call_kind=call_kind, workers=1),))
    assert unproven.calls[0].disposition is ResolutionDisposition.UNRESOLVED_FRONTIER

    proven = resolve_call_sites((
        _fixture(
            call_kind=call_kind,
            workers=1,
            symbol_resolution_full=True,
        ),
    ))
    expected = (
        ResolutionDisposition.EXHAUSTIVE_CANDIDATES
        if call_kind is TargetExpressionKind.DYNAMIC_MEMBER
        else ResolutionDisposition.UNRESOLVED_FRONTIER
    )
    assert proven.calls[0].disposition is expected


def test_unscoped_cross_file_lexical_name_is_not_an_exact_call_target():
    from hermes_cli.hades_index.lifecycle.interprocedural import (
        ResolutionDisposition,
        resolve_call_sites,
    )

    result = _fixture(target="worker")
    worker = next(item for item in result.declarations if item.name == "worker")
    other_location = SourceLocationIR("src/other.py", 1, 2, _DIGEST)
    other_locator = AstLocatorIR(other_location, "declaration/worker/0", 0)
    site = replace(
        result.call_sites[0], lexical_target="worker", fully_qualified_target=None
    )
    separated = replace(
        result,
        declarations=_sort(
            replace(item, locator=other_locator)
            if item.local_key == worker.local_key
            else item
            for item in result.declarations
        ),
        call_sites=(site,),
    )
    separated.validate()

    decision = resolve_call_sites((separated,)).calls[0]
    assert decision.disposition is ResolutionDisposition.UNRESOLVED_FRONTIER


def _typed_receiver_result(
    *,
    receiver_method_count: int,
    same_file_receiver_methods: bool,
    symbol_resolution_full: bool,
) -> tuple[AdapterResult, str, tuple[str, ...]]:
    """Build a free-function homonym plus methods owned by ``app.Service``."""

    result = _fixture(
        workers=receiver_method_count + 1,
        symbol_resolution_full=symbol_resolution_full,
    )
    free_function = next(
        item for item in result.declarations if item.qualified_name == "app.worker"
    )
    receiver_methods = tuple(
        item
        for item in result.declarations
        if item.name == "worker" and item.local_key != free_function.local_key
    )
    service_key = _key("executable_declaration", "declaration/service")
    service_entry = _key("basic_block", "declaration/service/entry")
    service = ExecutableDeclaration(
        local_key=service_key,
        language="python",
        declaration_kind=NodeKind.SERVICE,
        identity_kind=DeclarationIdentityKind.NAMED,
        owner_declaration_key=None,
        name="Service",
        qualified_name="app.Service",
        namespace="app",
        modifiers=(),
        parameters=(),
        return_type=None,
        locator=_ast("declaration/service"),
        entry_block_key=service_entry,
        normal_exit_block_keys=(service_entry,),
        exception_exit_block_keys=(),
    )
    service_block = BasicBlock(
        service_entry,
        service_key,
        ControlKind.ENTRY,
        0,
        _ast("declaration/service/entry"),
        (),
    )
    other_location = SourceLocationIR("src/service.py", 1, 99, _DIGEST)
    updated_methods = tuple(
        replace(
            method,
            owner_declaration_key=service_key,
            qualified_name=f"app.Service.worker{index}",
            locator=(
                method.locator
                if same_file_receiver_methods
                else AstLocatorIR(
                    other_location,
                    f"declaration/service/worker/{index}",
                    0,
                )
            ),
        )
        for index, method in enumerate(receiver_methods)
    )
    method_by_key = {method.local_key: method for method in updated_methods}
    call_site = replace(
        result.call_sites[0],
        target_expression_kind=TargetExpressionKind.DIRECT_INSTANCE_METHOD,
        lexical_target="worker",
        fully_qualified_target=None,
        receiver_type="app.Service",
    )
    typed = replace(
        result,
        declarations=_sort((
            service,
            *(method_by_key.get(item.local_key, item) for item in result.declarations),
        )),
        blocks=_sort((*result.blocks, service_block)),
        call_sites=(call_site,),
    )
    typed.validate()
    return (
        typed,
        free_function.local_key,
        tuple(method.local_key for method in updated_methods),
    )


def test_ambiguous_same_file_name_continues_to_unique_receiver_proof():
    from hermes_cli.hades_index.lifecycle.interprocedural import (
        ResolutionDisposition,
        resolve_call_sites,
    )

    result, _free_function, receiver_methods = _typed_receiver_result(
        receiver_method_count=1,
        same_file_receiver_methods=True,
        symbol_resolution_full=True,
    )

    decision = resolve_call_sites((result,)).calls[0]
    assert decision.disposition is ResolutionDisposition.EXACT
    assert decision.target_declaration_keys == receiver_methods


def test_unique_same_file_name_still_precedes_receiver_proof():
    from hermes_cli.hades_index.lifecycle.interprocedural import (
        ResolutionDisposition,
        resolve_call_sites,
    )

    result, free_function, _receiver_methods = _typed_receiver_result(
        receiver_method_count=1,
        same_file_receiver_methods=False,
        symbol_resolution_full=True,
    )

    decision = resolve_call_sites((result,)).calls[0]
    assert decision.disposition is ResolutionDisposition.EXACT
    assert decision.target_declaration_keys == (free_function,)


@pytest.mark.parametrize(
    ("symbol_resolution_full", "expected"),
    (
        (False, "frontier"),
        (True, "candidates"),
    ),
)
def test_ambiguous_receiver_keeps_candidates_or_frontier(
    symbol_resolution_full: bool,
    expected: str,
):
    from hermes_cli.hades_index.lifecycle.interprocedural import (
        ResolutionDisposition,
        resolve_call_sites,
    )

    result, _free_function, receiver_methods = _typed_receiver_result(
        receiver_method_count=2,
        same_file_receiver_methods=True,
        symbol_resolution_full=symbol_resolution_full,
    )

    decision = resolve_call_sites((result,)).calls[0]
    if expected == "frontier":
        assert decision.disposition is ResolutionDisposition.UNRESOLVED_FRONTIER
        assert decision.target_declaration_keys == ()
    else:
        assert decision.disposition is ResolutionDisposition.EXHAUSTIVE_CANDIDATES
        assert decision.target_declaration_keys == tuple(sorted(receiver_methods))


def test_cfg_cycle_detection_is_iterative_for_a_deep_acyclic_graph():
    from hermes_cli.hades_index.lifecycle.control_flow import build_control_flow

    result = _fixture()
    handler = next(item for item in result.declarations if item.name == "handler")
    entry = next(
        item for item in result.blocks if item.local_key == handler.entry_block_key
    )
    branch = next(
        item for item in result.blocks if item.control_kind is ControlKind.BRANCH
    )
    deep_keys = tuple(_key("basic_block", f"deep/{index:04d}") for index in range(1200))
    deep_blocks = tuple(
        BasicBlock(
            key,
            handler.local_key,
            ControlKind.STRAIGHT_LINE,
            100 + index,
            _ast(f"deep/{index:04d}"),
            (
                AlwaysSuccessor(
                    deep_keys[index + 1]
                    if index + 1 < len(deep_keys)
                    else branch.local_key,
                    0,
                ),
            ),
        )
        for index, key in enumerate(deep_keys)
    )
    loop = next(
        item for item in result.blocks if item.control_kind is ControlKind.LOOP_HEADER
    )
    acyclic = replace(
        result,
        blocks=_sort((
            replace(entry, successors=(AlwaysSuccessor(deep_keys[0], 0),)),
            replace(
                loop,
                successors=(
                    LoopSuccessor(handler.normal_exit_block_keys[0], LoopRole.EXIT, 0),
                ),
            ),
            *(
                item
                for item in result.blocks
                if item.local_key not in {entry.local_key, loop.local_key}
            ),
            *deep_blocks,
        )),
    )
    acyclic.validate()

    control = build_control_flow(acyclic)
    assert len(control.blocks) >= 1200
    assert control.has_cycles is False
    assert control.path_count is None


def test_cross_declaration_synchronous_successor_is_rejected():
    result = _fixture(workers=1)
    handler = next(item for item in result.declarations if item.name == "handler")
    worker = next(item for item in result.declarations if item.name == "worker")
    entry = next(
        item for item in result.blocks if item.local_key == handler.entry_block_key
    )
    cross = replace(entry, successors=(AlwaysSuccessor(worker.entry_block_key, 0),))
    invalid = replace(
        result,
        blocks=_sort(
            cross if item.local_key == entry.local_key else item
            for item in result.blocks
        ),
    )

    with pytest.raises(ValueError, match="declaration"):
        invalid.validate()


def test_cross_declaration_non_call_edge_is_rejected():
    result = _fixture(workers=1)
    handler = next(item for item in result.declarations if item.name == "handler")
    worker = next(item for item in result.declarations if item.name == "worker")
    invalid = replace(
        result,
        edge_facts=(
            replace(
                result.edge_facts[0],
                relation=Relation.ROUTES_TO,
                source_node_local_key=handler.local_key,
                target=LocalNodeTarget(worker.local_key),
            ),
        ),
    )

    with pytest.raises(ValueError, match="cross declarations"):
        invalid.validate()


def _complete_native_candidate_result() -> AdapterResult:
    result = _fixture(unresolved=True, workers=2)
    fact = result.unresolved_facts[0]
    subject = result.edge_facts[0]
    workers = tuple(item for item in result.declarations if item.name == "worker")
    candidates = tuple(
        replace(
            subject,
            local_key=_key("complete_candidate", "body/call", index),
            target=LocalNodeTarget(worker.local_key),
            evidence=_evidence(_ast("body/call"), origin=EvidenceOrigin.INFERRED),
        )
        for index, worker in enumerate(workers)
    )
    complete = replace(
        fact,
        candidate_set_knowledge=CandidateSetKnowledge.COMPLETE,
        candidate_target_local_keys=tuple(
            sorted(worker.local_key for worker in workers)
        ),
        candidate_edge_local_keys=tuple(sorted(edge.local_key for edge in candidates)),
    )
    complete_result = replace(
        result,
        edge_facts=_sort(candidates),
        unresolved_facts=(complete,),
    )
    complete_result.validate()
    return complete_result


def test_graphify_preserves_native_complete_candidate_sets_as_a_noop():
    from hermes_cli.hades_index.graphify_candidates import attach_graphify_hints

    result = _complete_native_candidate_result()
    fact = result.unresolved_facts[0]
    target = next(
        item.local_key for item in result.declarations if item.name == "worker"
    )
    assert (
        attach_graphify_hints(result, {fact.local_key: (target,)}, enabled=True)
        is result
    )


def test_graphify_adds_incremental_hints_without_ordinal_collision():
    from hermes_cli.hades_index.graphify_candidates import attach_graphify_hints

    result = _fixture(unresolved=True, workers=3)
    fact = result.unresolved_facts[0]
    workers = tuple(
        item.local_key for item in result.declarations if item.name == "worker"
    )
    first = attach_graphify_hints(result, {fact.local_key: (workers[0],)}, enabled=True)
    second = attach_graphify_hints(first, {fact.local_key: (workers[1],)}, enabled=True)
    updated = second.unresolved_facts[0]

    assert updated.candidate_set_knowledge is CandidateSetKnowledge.INCOMPLETE
    assert updated.candidate_target_local_keys == tuple(sorted(workers[:2]))
    assert len(updated.candidate_edge_local_keys) == 2
    second.validate()


def test_graphify_preserves_native_incomplete_candidates_when_adding_hints():
    from hermes_cli.hades_index.graphify_candidates import attach_graphify_hints

    result = _fixture(unresolved=True, workers=2)
    fact = result.unresolved_facts[0]
    subject = result.edge_facts[0]
    workers = tuple(
        item.local_key for item in result.declarations if item.name == "worker"
    )
    native_candidate = replace(
        subject,
        local_key=_key("native_candidate", "body/call"),
        target=LocalNodeTarget(workers[0]),
        evidence=IREvidence(
            origin=EvidenceOrigin.INFERRED,
            extractor="resolver.static-candidates",
            locator=_ast("body/call"),
            inference_rule="static_candidate",
        ),
    )
    incomplete = replace(
        fact,
        candidate_set_knowledge=CandidateSetKnowledge.INCOMPLETE,
        candidate_target_local_keys=(workers[0],),
        candidate_edge_local_keys=(native_candidate.local_key,),
    )
    native = replace(
        result,
        edge_facts=_sort((*result.edge_facts, native_candidate)),
        unresolved_facts=(incomplete,),
    )
    native.validate()

    enriched = attach_graphify_hints(
        native, {incomplete.local_key: (workers[1],)}, enabled=True
    )
    updated = enriched.unresolved_facts[0]
    assert updated.candidate_target_local_keys == tuple(sorted(workers))
    assert native_candidate.local_key in updated.candidate_edge_local_keys
    assert (
        next(
            edge
            for edge in enriched.edge_facts
            if edge.local_key == native_candidate.local_key
        )
        == native_candidate
    )
    enriched.validate()


def test_parser_closed_discriminators_reject_unknown_variants():
    from hermes_cli.hades_index.tree_sitter_adapter import (
        ParseFailure,
        ParseResult,
        SyntaxControl,
    )

    with pytest.raises(ValueError, match="kind"):
        SyntaxControl("other", "root/node/0", 1, 1)
    with pytest.raises(ValueError, match="code"):
        ParseFailure("other", "src/app.py", "python")
    failure = ParseFailure("parser_failed", "src/app.py", "python")
    coverage = CoverageEvent(
        "python",
        CoverageCapability.CONTROL_FLOW,
        CoverageOutcome.PARTIAL,
        "parser_failed",
        "src/app.py",
        0,
        1,
    )
    with pytest.raises(ValueError, match="status"):
        ParseResult("other", None, failure, coverage)
