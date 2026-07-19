"""Translate source-free Tree-sitter facts into the closed graph-v2 IR."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import replace
import hashlib

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
from hermes_cli.hades_index.tree_sitter_adapter import SyntaxIR, declaration_local_key

from .entrypoints import (
    EntrypointExtraction,
    aggregate_entrypoint_extraction,
    merge_entrypoint_extractions,
)
from .frameworks import FrameworkAdapterRegistry
from .model import (
    AdapterResult,
    AlwaysSuccessor,
    AstLocatorIR,
    BasicBlock,
    BoundaryTarget,
    BranchArm,
    BranchSuccessor,
    CallSite,
    ConditionIR,
    ControlKind,
    CoverageCapability,
    CoverageEvent,
    CoverageOutcome,
    DataNodeIR,
    DeclarationIdentityKind,
    EdgeFactIR,
    EdgeSubjectIR,
    ExceptionCatchArm,
    ExceptionScope,
    ExceptionSuccessor,
    ExecutableDeclaration,
    ExtractionContext,
    FrameworkBoundaryTarget,
    FrameworkBoundaryDescriptor,
    FrameworkLocalTarget,
    IREvidence,
    LocalNodeTarget,
    LoopRole,
    LoopSuccessor,
    Modifier,
    ReturnSuccessor,
    SourceNodeIR,
    SourceLocationIR,
    StructureIR,
    TargetExpressionKind,
    UnresolvedFact,
    local_record_key,
)


_EXECUTABLE_SYMBOL_KINDS = frozenset({"function", "method"})

_CONTROL_KIND = {
    "branch": ControlKind.BRANCH,
    "branch_arm": ControlKind.STRAIGHT_LINE,
    "merge": ControlKind.MERGE,
    "loop": ControlKind.LOOP_HEADER,
    "loop_body": ControlKind.LOOP_BODY,
    "return": ControlKind.RETURN,
    "throw": ControlKind.THROW,
    "try": ControlKind.STRAIGHT_LINE,
    "catch": ControlKind.CATCH,
    "finally": ControlKind.FINALLY,
    "async_dispatch": ControlKind.ASYNC_DISPATCH,
    "call": ControlKind.STRAIGHT_LINE,
}


def _source_node_kind(symbol_kind: str) -> NodeKind | None:
    return {
        "class": NodeKind.CLASS,
        "interface": NodeKind.INTERFACE,
        "trait": NodeKind.TRAIT,
        "enum": NodeKind.ENUM,
    }.get(symbol_kind)


def _data_node_kind(path: str, language: str, symbol_kind: str) -> NodeKind | None:
    """Return source-proven ORM resources without retyping declarations."""

    if language == "php" and symbol_kind == "class" and path.startswith("app/Models/"):
        return NodeKind.MODEL
    return None


def default_framework_registry() -> FrameworkAdapterRegistry:
    """Return the explicit built-in framework registry used by graph jobs."""

    from .frameworks.django import DjangoLifecycleAdapter
    from .frameworks.fastapi import FastAPILifecycleAdapter
    from .frameworks.laravel import LaravelLifecycleAdapter
    from .frameworks.nextjs import NextJSLifecycleAdapter
    from .frameworks.symfony import SymfonyLifecycleAdapter

    registry = FrameworkAdapterRegistry()
    for adapter in (
        DjangoLifecycleAdapter(),
        FastAPILifecycleAdapter(),
        LaravelLifecycleAdapter(),
        SymfonyLifecycleAdapter(),
        NextJSLifecycleAdapter("javascript"),
        NextJSLifecycleAdapter("typescript"),
    ):
        registry.register(adapter)
    return registry


def _empty_result(*, coverage_events: tuple[CoverageEvent, ...] = ()) -> AdapterResult:
    return AdapterResult(
        declarations=(),
        blocks=(),
        branch_arms=(),
        structures=(),
        call_sites=(),
        edge_facts=(),
        exception_scopes=(),
        terminals=(),
        effects=(),
        framework_segments=(),
        entrypoints=(),
        unresolved_facts=(),
        coverage_events=coverage_events,
        diagnostics=(),
    )


def _coverage_key(event: CoverageEvent) -> tuple[object, ...]:
    return (
        event.language,
        event.capability.value,
        event.outcome.value,
        event.reason_code or "",
        event.path or "",
    )


def _declaration_and_blocks(
    *,
    local_key: str,
    language: str,
    name: str,
    locator: AstLocatorIR,
    kind: NodeKind,
) -> tuple[ExecutableDeclaration, tuple[BasicBlock, BasicBlock]]:
    entry_locator = replace(locator, structural_path=f"{locator.structural_path}/entry")
    exit_locator = replace(locator, structural_path=f"{locator.structural_path}/exit")
    entry_key = local_record_key(
        language,
        locator.source_location.path,
        "basic_block",
        "ast",
        f"{entry_locator.structural_path}/{local_key}",
        0,
    )
    exit_key = local_record_key(
        language,
        locator.source_location.path,
        "basic_block",
        "ast",
        f"{exit_locator.structural_path}/{local_key}",
        0,
    )
    declaration = ExecutableDeclaration(
        local_key=local_key,
        language=language,
        declaration_kind=kind,
        identity_kind=DeclarationIdentityKind.NAMED,
        owner_declaration_key=None,
        name=name.rsplit(".", 1)[-1],
        qualified_name=name,
        namespace=name.rsplit(".", 1)[0] if "." in name else None,
        modifiers=(Modifier.PUBLIC,),
        parameters=(),
        return_type=None,
        locator=locator,
        entry_block_key=entry_key,
        normal_exit_block_keys=(exit_key,),
        exception_exit_block_keys=(),
    )
    return declaration, (
        BasicBlock(
            entry_key,
            local_key,
            ControlKind.ENTRY,
            0,
            entry_locator,
            (AlwaysSuccessor(exit_key, 0),),
        ),
        BasicBlock(exit_key, local_key, ControlKind.RETURN, 1, exit_locator, ()),
    )


def _condition(label: str, polarity: ConditionPolarity) -> ConditionIR:
    return ConditionIR(
        "predicate",
        label,
        hashlib.sha256(label.encode("ascii")).hexdigest(),
        polarity,
    )


def _syntax_control_flow(
    declaration: ExecutableDeclaration,
    syntax: SyntaxIR,
    symbol_path: str,
) -> tuple[
    ExecutableDeclaration,
    tuple[BasicBlock, ...],
    tuple[BranchArm, ...],
    tuple[StructureIR, ...],
    tuple[ExceptionScope, ...],
    dict[int, tuple[str, ...]],
    int,
]:
    """Build a finite CFG from exact controls without enumerating runtime paths."""

    controls = tuple(
        sorted(
            (
                control
                for control in syntax.controls
                if control.owner_structural_path == symbol_path
            ),
            key=lambda row: (
                (
                    row.end_byte
                    if row.kind in {"merge", "return", "throw"}
                    else row.start_byte
                ),
                {
                    "branch": 0,
                    "loop": 0,
                    "try": 0,
                    "branch_arm": 1,
                    "loop_body": 1,
                    "merge": 3,
                    "return": 4,
                    "throw": 4,
                }.get(row.kind, 2),
                row.end_byte,
                row.structural_path,
                row.kind,
            ),
        )
    )
    if not controls:
        _, defaults = _declaration_and_blocks(
            local_key=declaration.local_key,
            language=declaration.language,
            name=declaration.qualified_name or declaration.name,
            locator=declaration.locator,
            kind=declaration.declaration_kind,
        )
        return declaration, defaults, (), (), (), {}, 0

    blocks: dict[str, BasicBlock] = {}
    control_keys: dict[int, str] = {}
    control_rows: dict[int, object] = {}
    call_blocks: dict[int, list[str]] = defaultdict(list)
    file_digest = declaration.locator.source_location.file_sha256
    for ordinal, control in enumerate(controls):
        locator = AstLocatorIR(
            SourceLocationIR(
                syntax.path,
                control.line,
                control.end_line,
                file_digest,
            ),
            control.structural_path,
            ordinal,
        )
        key = local_record_key(
            declaration.language,
            syntax.path,
            "basic_block",
            "ast",
            control.structural_path,
            ordinal,
        )
        control_keys[ordinal] = key
        control_rows[ordinal] = control
        blocks[key] = BasicBlock(
            key,
            declaration.local_key,
            _CONTROL_KIND[control.kind],
            ordinal + 1,
            locator,
            (),
        )
        if control.kind == "call":
            call_blocks[control.line].append(key)

    entry_key = declaration.entry_block_key
    exit_key = declaration.normal_exit_block_keys[0]
    entry_locator = replace(
        declaration.locator,
        structural_path=f"{declaration.locator.structural_path}/entry",
    )
    exit_locator = replace(
        declaration.locator,
        structural_path=f"{declaration.locator.structural_path}/exit",
    )
    first_key = control_keys[0]
    blocks[entry_key] = BasicBlock(
        entry_key,
        declaration.local_key,
        ControlKind.ENTRY,
        0,
        entry_locator,
        (AlwaysSuccessor(first_key, 0),),
    )
    blocks[exit_key] = BasicBlock(
        exit_key,
        declaration.local_key,
        ControlKind.RETURN,
        len(controls) + 2,
        exit_locator,
        (),
    )

    structures: list[StructureIR] = []
    branch_arms: list[BranchArm] = []
    scopes: list[ExceptionScope] = []
    return_keys: set[str] = {exit_key}
    throw_keys: set[str] = set()
    uncertain_frontiers = 0

    def matching_merge(parent) -> str:
        return next(
            (
                candidate_key
                for candidate_index, candidate_key in control_keys.items()
                if control_rows[candidate_index].kind == "merge"
                and control_rows[candidate_index].start_byte == parent.start_byte
                and control_rows[candidate_index].end_byte == parent.end_byte
            ),
            exit_key,
        )

    def exact_child(parent, kind: str, polarity: str | None = None) -> str | None:
        return next(
            (
                control_keys[candidate_index]
                for candidate_index, candidate in control_rows.items()
                if candidate.kind == kind
                and candidate.parent_control_path == parent.structural_path
                and (polarity is None or candidate.arm_polarity == polarity)
            ),
            None,
        )

    def following(index: int) -> str:
        control = control_rows[index]
        containers = [
            (candidate_index, candidate)
            for candidate_index, candidate in control_rows.items()
            if candidate.kind in {"branch_arm", "loop_body"}
            and (
                candidate_index == index
                or control.structural_path.startswith(f"{candidate.structural_path}/")
            )
        ]
        if containers:
            _container_index, container = max(
                containers, key=lambda item: len(item[1].structural_path)
            )
            nested = next(
                (
                    control_keys[candidate_index]
                    for candidate_index in range(index + 1, len(controls))
                    if control_rows[candidate_index].structural_path.startswith(
                        f"{container.structural_path}/"
                    )
                ),
                None,
            )
            if nested is not None:
                return nested
            parent = next(
                (
                    candidate
                    for candidate in control_rows.values()
                    if candidate.structural_path == container.parent_control_path
                ),
                None,
            )
            if parent is not None and container.kind == "loop_body":
                return next(
                    key
                    for candidate_index, key in control_keys.items()
                    if control_rows[candidate_index] is parent
                )
            if parent is not None:
                return matching_merge(parent)
        return control_keys.get(index + 1, exit_key)

    for index, key in control_keys.items():
        control = control_rows[index]
        block = blocks[key]
        next_key = following(index)
        if control.kind in {"return", "throw"}:
            if control.kind == "return":
                return_keys.add(key)
            else:
                throw_keys.add(key)
            continue
        if control.kind == "loop_body":
            parent_key = next(
                (
                    control_keys[candidate_index]
                    for candidate_index, candidate in control_rows.items()
                    if candidate.structural_path == control.parent_control_path
                    and candidate.kind == "loop"
                ),
                None,
            )
            if parent_key is None:
                uncertain_frontiers += 1
                blocks[key] = replace(block, successors=(AlwaysSuccessor(next_key, 0),))
            else:
                nested_key = next_key
                blocks[key] = replace(
                    block,
                    successors=(
                        LoopSuccessor(parent_key, LoopRole.BACK, 0)
                        if nested_key == parent_key
                        else AlwaysSuccessor(nested_key, 0)
                    ,),
                )
            continue
        if control.kind == "branch":
            merge_key = matching_merge(control)
            true_key = exact_child(control, "branch_arm", "true")
            false_key = exact_child(control, "branch_arm", "false") or merge_key
            if true_key is None:
                true_key = merge_key
                uncertain_frontiers += 1
            if true_key == false_key:
                uncertain_frontiers += 1
            structure_key = local_record_key(
                declaration.language,
                syntax.path,
                "branch_structure",
                "ast",
                control.structural_path,
                index,
            )
            evidence = IREvidence(
                EvidenceOrigin.VERIFIED_FROM_CODE,
                "tree-sitter.lifecycle-v2",
                block.locator,
                None,
            )
            structures.append(
                StructureIR(
                    structure_key,
                    StructureKind.BRANCH_GROUP,
                    declaration.local_key,
                    control.structural_path,
                    index,
                    StructureSubtype.IF,
                    merge_key,
                    None,
                    evidence,
                )
            )
            true_condition = _condition("syntax_branch_true", ConditionPolarity.TRUE)
            false_condition = _condition("syntax_branch_false", ConditionPolarity.FALSE)
            branch_arms.extend((
                BranchArm(structure_key, key, true_key, ConditionPolarity.TRUE, true_condition, 0),
                BranchArm(structure_key, key, false_key, ConditionPolarity.FALSE, false_condition, 1),
            ))
            blocks[key] = replace(
                block,
                successors=(
                    BranchSuccessor(true_key, structure_key, 0, 0),
                    BranchSuccessor(false_key, structure_key, 1, 1),
                ),
            )
            continue
        if control.kind == "loop":
            merge_key = matching_merge(control)
            body_key = exact_child(control, "loop_body")
            if body_key is None:
                body_key = merge_key
                uncertain_frontiers += 1
            structure_key = local_record_key(
                declaration.language,
                syntax.path,
                "loop_structure",
                "ast",
                control.structural_path,
                index,
            )
            evidence = IREvidence(
                EvidenceOrigin.VERIFIED_FROM_CODE,
                "tree-sitter.lifecycle-v2",
                block.locator,
                None,
            )
            structures.append(
                StructureIR(
                    structure_key,
                    StructureKind.BRANCH_GROUP,
                    declaration.local_key,
                    control.structural_path,
                    index,
                    StructureSubtype.LOOP,
                    merge_key,
                    None,
                    evidence,
                )
            )
            body_condition = _condition("syntax_loop_body", ConditionPolarity.LOOP_BODY)
            exit_condition = _condition("syntax_loop_exit", ConditionPolarity.LOOP_EXIT)
            branch_arms.extend((
                BranchArm(structure_key, key, body_key, ConditionPolarity.LOOP_BODY, body_condition, 0),
                BranchArm(structure_key, key, merge_key, ConditionPolarity.LOOP_EXIT, exit_condition, 1),
            ))
            blocks[key] = replace(
                block,
                successors=(
                    BranchSuccessor(body_key, structure_key, 0, 0),
                    BranchSuccessor(merge_key, structure_key, 1, 1),
                ),
            )
            continue
        if control.kind == "try":
            catch_indexes = [
                candidate_index
                for candidate_index in control_keys
                if candidate_index > index
                and control_rows[candidate_index].kind == "catch"
                and control_rows[candidate_index].structural_path.startswith(
                    control.structural_path
                )
            ]
            finally_index = next(
                (
                    candidate_index
                    for candidate_index in control_keys
                    if candidate_index > index
                    and control_rows[candidate_index].kind == "finally"
                    and control_rows[candidate_index].structural_path.startswith(
                        control.structural_path
                    )
                ),
                None,
            )
            if catch_indexes or finally_index is not None:
                structure_key = local_record_key(
                    declaration.language,
                    syntax.path,
                    "exception_structure",
                    "ast",
                    control.structural_path,
                    index,
                )
                evidence = IREvidence(
                    EvidenceOrigin.VERIFIED_FROM_CODE,
                    "tree-sitter.lifecycle-v2",
                    block.locator,
                    None,
                )
                subtype = (
                    StructureSubtype.TRY_CATCH_FINALLY
                    if catch_indexes and finally_index is not None
                    else StructureSubtype.TRY_CATCH
                    if catch_indexes
                    else StructureSubtype.TRY_FINALLY
                )
                structures.append(
                    StructureIR(
                        structure_key,
                        StructureKind.EXCEPTION_SCOPE,
                        declaration.local_key,
                        control.structural_path,
                        index,
                        subtype,
                        control_keys[finally_index] if finally_index is not None else next_key,
                        None,
                        evidence,
                    )
                )
                catch_arms = tuple(
                    ExceptionCatchArm(None, control_keys[catch_index], arm_index)
                    for arm_index, catch_index in enumerate(catch_indexes[:1])
                )
                uncertain_frontiers += max(0, len(catch_indexes) - 1)
                scope_key = local_record_key(
                    declaration.language,
                    syntax.path,
                    "exception_scope",
                    "ast",
                    control.structural_path,
                    index,
                )
                scopes.append(
                    ExceptionScope(
                        scope_key,
                        structure_key,
                        declaration.local_key,
                        block.locator,
                        catch_arms,
                        control_keys[finally_index] if finally_index is not None else None,
                        None,
                    )
                )
                successors = [AlwaysSuccessor(next_key, 0)]
                successors.extend(
                    ExceptionSuccessor(control_keys[catch_index], scope_key, None, arm_index + 1)
                    for arm_index, catch_index in enumerate(catch_indexes[:1])
                )
                blocks[key] = replace(block, successors=tuple(successors))
                continue
        blocks[key] = replace(block, successors=(AlwaysSuccessor(next_key, 0),))

    declaration = replace(
        declaration,
        normal_exit_block_keys=tuple(sorted(return_keys)),
        exception_exit_block_keys=tuple(sorted(throw_keys)),
    )
    return (
        declaration,
        tuple(sorted(blocks.values(), key=lambda row: row.local_key)),
        tuple(sorted(branch_arms, key=lambda row: (row.branch_local_key, row.arm_ordinal))),
        tuple(sorted(structures, key=lambda row: row.local_key)),
        tuple(sorted(scopes, key=lambda row: row.local_key)),
        {line: tuple(values) for line, values in call_blocks.items()},
        uncertain_frontiers,
    )


def _structural_result(
    context: ExtractionContext,
    syntax: Sequence[SyntaxIR],
    parse_coverage: Sequence[CoverageEvent],
) -> AdapterResult:
    declarations: list[ExecutableDeclaration] = []
    source_nodes: list[SourceNodeIR] = []
    data_nodes: list[DataNodeIR] = []
    blocks: list[BasicBlock] = []
    branch_arms: list[BranchArm] = []
    structures: list[StructureIR] = []
    call_sites: list[CallSite] = []
    edges: list[EdgeFactIR] = []
    exception_scopes: list[ExceptionScope] = []
    coverage = list(parse_coverage)
    names: dict[tuple[str, str], list[ExecutableDeclaration]] = defaultdict(list)
    exits: dict[str, str] = {}
    entries: dict[str, str] = {}
    call_blocks_by_declaration: dict[str, dict[int, tuple[str, ...]]] = {}
    block_by_key: dict[str, BasicBlock] = {}
    uncertain_by_file: dict[tuple[str, str], int] = defaultdict(int)
    source_node_by_name: dict[tuple[str, str, str], SourceNodeIR] = {}

    for item in sorted(syntax, key=lambda value: (value.language, value.path)):
        inventory_file = next(
            row for row in context.inventory_files if row.path == item.path
        )
        file_sha256 = inventory_file.file_sha256
        represented = 0
        for ordinal, symbol in enumerate(item.symbols):
            locator = AstLocatorIR(
                SourceLocationIR(
                    item.path,
                    symbol.line,
                    symbol.end_line,
                    file_sha256,
                ),
                symbol.structural_path,
                0,
            )
            source_kind = _source_node_kind(symbol.kind)
            evidence = IREvidence(
                EvidenceOrigin.VERIFIED_FROM_CODE,
                "tree-sitter.lifecycle-v2",
                locator,
                None,
            )
            if source_kind is not None:
                source_node = SourceNodeIR(
                    local_record_key(
                        item.language,
                        item.path,
                        "source_node",
                        "ast",
                        symbol.structural_path,
                        0,
                    ),
                    item.language,
                    source_kind,
                    symbol.name.rsplit(".", 1)[-1],
                    symbol.name,
                    symbol.container or None,
                    locator,
                    evidence,
                )
                source_nodes.append(source_node)
                source_node_by_name[(item.language, item.path, symbol.name)] = source_node
                source_node_by_name[
                    (item.language, item.path, symbol.name.rsplit(".", 1)[-1])
                ] = source_node
                represented += 1
            data_kind = _data_node_kind(item.path, item.language, symbol.kind)
            if data_kind is not None:
                data_nodes.append(
                    DataNodeIR(
                        local_record_key(
                            item.language,
                            item.path,
                            "data_node",
                            "ast",
                            symbol.structural_path,
                            0,
                        ),
                        item.language,
                        data_kind,
                        symbol.name.rsplit(".", 1)[-1],
                        symbol.name,
                        symbol.name,
                        locator,
                        evidence,
                    )
                )
                represented += 1
            if inventory_file.is_test and symbol.kind in _EXECUTABLE_SYMBOL_KINDS:
                source_nodes.append(
                    SourceNodeIR(
                        local_record_key(
                            item.language,
                            item.path,
                            "test_node",
                            "ast",
                            symbol.structural_path,
                            0,
                        ),
                        item.language,
                        NodeKind.TEST,
                        symbol.name.rsplit(".", 1)[-1],
                        symbol.name,
                        symbol.container or None,
                        locator,
                        evidence,
                    )
                )
                represented += 1
            if symbol.kind not in _EXECUTABLE_SYMBOL_KINDS:
                continue
            kind = NodeKind.METHOD if symbol.kind == "method" or symbol.container else NodeKind.FUNCTION
            local_key = declaration_local_key(item.language, item.path, symbol, ordinal)
            declaration, _default_blocks = _declaration_and_blocks(
                local_key=local_key,
                language=item.language,
                name=symbol.name,
                locator=locator,
                kind=kind,
            )
            (
                declaration,
                emitted_blocks,
                emitted_arms,
                emitted_structures,
                emitted_scopes,
                call_blocks,
                uncertain_frontiers,
            ) = _syntax_control_flow(declaration, item, symbol.structural_path)
            declarations.append(declaration)
            blocks.extend(emitted_blocks)
            branch_arms.extend(emitted_arms)
            structures.extend(emitted_structures)
            exception_scopes.extend(emitted_scopes)
            names[(item.language, symbol.name)].append(declaration)
            names[(item.language, symbol.name.rsplit(".", 1)[-1])].append(declaration)
            entries[declaration.local_key] = declaration.entry_block_key
            exits[declaration.local_key] = declaration.normal_exit_block_keys[0]
            call_blocks_by_declaration[declaration.local_key] = call_blocks
            block_by_key.update({row.local_key: row for row in emitted_blocks})
            uncertain_by_file[(item.language, item.path)] += uncertain_frontiers
            represented += 1
            if symbol.container:
                owner = source_node_by_name.get(
                    (item.language, item.path, symbol.container)
                )
                if owner is not None:
                    edges.append(
                        EdgeFactIR(
                            local_record_key(
                                item.language,
                                item.path,
                                "contains_edge",
                                "ast",
                                symbol.structural_path,
                                0,
                            ),
                            owner.local_key,
                            LocalNodeTarget(declaration.local_key),
                            Relation.CONTAINS,
                            None,
                            None,
                            None,
                            None,
                            None,
                            None,
                            locator,
                            evidence,
                        )
                    )

        omitted_control = uncertain_by_file[(item.language, item.path)]
        coverage.extend((
            CoverageEvent(
                item.language,
                CoverageCapability.SYMBOL_RESOLUTION,
                CoverageOutcome.FULL,
                None,
                item.path,
                represented,
                0,
            ),
            CoverageEvent(
                item.language,
                CoverageCapability.CONTROL_FLOW,
                CoverageOutcome.PARTIAL if omitted_control else CoverageOutcome.FULL,
                "control_frontier_unresolved" if omitted_control else None,
                item.path,
                len(item.controls),
                omitted_control,
            ),
        ))

    def resolve_declaration(
        language: str, path: str, name: str
    ) -> ExecutableDeclaration | None:
        matches = names.get((language, name), ())
        if not matches:
            matches = names.get((language, name.rsplit(".", 1)[-1]), ())
        unique = {row.local_key: row for row in matches}
        local = {
            key: row
            for key, row in unique.items()
            if row.locator.source_location.path == path
        }
        if len(local) == 1:
            return next(iter(local.values()))
        return next(iter(unique.values())) if len(unique) == 1 else None

    unresolved_calls: dict[tuple[str, str], int] = defaultdict(int)
    represented_calls: dict[tuple[str, str], int] = defaultdict(int)
    for item in sorted(syntax, key=lambda value: (value.language, value.path)):
        file_sha256 = next(
            row.file_sha256 for row in context.inventory_files if row.path == item.path
        )
        controls_by_line: dict[int, list[object]] = defaultdict(list)
        for control in item.controls:
            if control.kind == "call":
                controls_by_line[control.line].append(control)
        call_line_ordinals: dict[int, int] = defaultdict(int)
        for call in item.calls:
            caller = resolve_declaration(item.language, item.path, call.caller)
            target = resolve_declaration(item.language, item.path, call.target)
            if caller is None or target is None:
                unresolved_calls[(item.language, item.path)] += 1
                continue
            ordinal = call_line_ordinals[call.line]
            call_line_ordinals[call.line] += 1
            line_controls = controls_by_line.get(call.line, ())
            structural_path = (
                line_controls[min(ordinal, len(line_controls) - 1)].structural_path
                if line_controls
                else f"calls/{call.line}/{ordinal}"
            )
            locator = AstLocatorIR(
                SourceLocationIR(item.path, call.line, call.line, file_sha256),
                structural_path,
                ordinal,
            )
            declaration_call_blocks = call_blocks_by_declaration.get(
                caller.local_key, {}
            )
            exact_blocks = declaration_call_blocks.get(call.line, ())
            source_block_key = (
                exact_blocks[min(ordinal, len(exact_blocks) - 1)]
                if exact_blocks
                else entries[caller.local_key]
            )
            source_block = block_by_key[source_block_key]
            continuation_key = next(
                (
                    successor.target_block_key
                    for successor in source_block.successors
                    if type(successor) is AlwaysSuccessor
                ),
                exits[caller.local_key],
            )
            structure_key = local_record_key(
                item.language, item.path, "call_site_structure", "ast", structural_path, ordinal
            )
            site_key = local_record_key(
                item.language, item.path, "call_site", "ast", structural_path, ordinal
            )
            evidence = IREvidence(
                EvidenceOrigin.VERIFIED_FROM_CODE,
                "tree-sitter.lifecycle-v2",
                locator,
                None,
            )
            structures.append(
                StructureIR(
                    structure_key,
                    StructureKind.CALL_SITE,
                    caller.local_key,
                    structural_path,
                    ordinal,
                    StructureSubtype.CALL,
                    continuation_key,
                    None,
                    evidence,
                )
            )
            call_sites.append(
                CallSite(
                    site_key,
                    caller.local_key,
                    source_block_key,
                    locator,
                    (
                        TargetExpressionKind.DIRECT_INSTANCE_METHOD
                        if "." in call.target
                        else TargetExpressionKind.DIRECT_FUNCTION
                    ),
                    call.target,
                    target.qualified_name,
                    None,
                    call.argument_count,
                    continuation_key,
                    None,
                )
            )
            edges.append(
                EdgeFactIR(
                    local_record_key(
                        item.language, item.path, "invocation_edge", "ast", structural_path, ordinal
                    ),
                    source_block_key,
                    LocalNodeTarget(target.local_key),
                    Relation.INVOKES,
                    EdgeFlow.ALWAYS,
                    None,
                    None,
                    structure_key,
                    None,
                    ordinal,
                    locator,
                    evidence,
                )
            )
            represented_calls[(item.language, item.path)] += 1

    for language, path in sorted(set(unresolved_calls) | set(represented_calls)):
        omitted = unresolved_calls[(language, path)]
        coverage.append(
            CoverageEvent(
                language,
                CoverageCapability.CALL_GRAPH,
                CoverageOutcome.PARTIAL if omitted else CoverageOutcome.FULL,
                "call_target_unresolved" if omitted else None,
                path,
                represented_calls[(language, path)],
                omitted,
            )
        )

    result = AdapterResult(
        declarations=tuple(sorted(declarations, key=lambda row: row.local_key)),
        blocks=tuple(sorted(blocks, key=lambda row: row.local_key)),
        branch_arms=tuple(
            sorted(branch_arms, key=lambda row: (row.branch_local_key, row.arm_ordinal))
        ),
        structures=tuple(sorted(structures, key=lambda row: row.local_key)),
        call_sites=tuple(sorted(call_sites, key=lambda row: row.local_key)),
        edge_facts=tuple(sorted(edges, key=lambda row: row.local_key)),
        exception_scopes=tuple(
            sorted(exception_scopes, key=lambda row: row.local_key)
        ),
        terminals=(),
        effects=(),
        framework_segments=(),
        entrypoints=(),
        unresolved_facts=(),
        coverage_events=tuple(sorted(coverage, key=_coverage_key)),
        diagnostics=(),
        source_nodes=tuple(sorted(source_nodes, key=lambda row: row.local_key)),
        data_nodes=tuple(sorted(data_nodes, key=lambda row: row.local_key)),
    )
    result.validate()
    return result


def _language_entrypoints(
    context: ExtractionContext,
    syntax: Sequence[SyntaxIR],
    registry: FrameworkAdapterRegistry,
) -> EntrypointExtraction:
    values: list[EntrypointExtraction] = []
    if any(item.language == "php" for item in syntax):
        from hermes_cli.hades_index.php import extract_lifecycle_entrypoints

        values.append(
            extract_lifecycle_entrypoints(
                context,
                tuple(item for item in syntax if item.language == "php"),
                registry=registry,
            )
        )
    if any(item.language == "python" for item in syntax):
        from hermes_cli.hades_index.python import extract_lifecycle_entrypoints

        values.append(
            extract_lifecycle_entrypoints(
                context,
                tuple(item for item in syntax if item.language == "python"),
                registry=registry,
            )
        )
    if any(item.language in {"javascript", "typescript"} for item in syntax):
        from hermes_cli.hades_index.typescript import extract_lifecycle_entrypoints

        values.append(
            extract_lifecycle_entrypoints(
                context,
                tuple(
                    item
                    for item in syntax
                    if item.language in {"javascript", "typescript"}
                ),
                registry=registry,
            )
        )
    return merge_entrypoint_extractions(*values) if values else EntrypointExtraction((), (), ())


def _resolve_framework_references(
    context: ExtractionContext,
    base: AdapterResult,
    extraction: EntrypointExtraction,
) -> tuple[AdapterResult, EntrypointExtraction]:
    """Resolve adapter keys to real syntax nodes or preserve a typed boundary."""

    edges = list(base.edge_facts)
    unresolved = list(base.unresolved_facts)
    declaration_keys = {row.local_key for row in base.declarations}
    declarations_by_key = {row.local_key: row for row in base.declarations}
    block_keys = {row.local_key for row in base.blocks}
    language_by_path = {row.path: row.language for row in context.inventory_files}
    segments = list(extraction.framework_segments)
    segment_keys = {row.local_key for row in segments}
    degraded_paths: set[tuple[str, str]] = set()

    candidates = []
    for candidate in extraction.candidates:
        if candidate.evidence.origin is EvidenceOrigin.UNRESOLVED:
            candidate = replace(
                candidate,
                evidence=IREvidence(
                    EvidenceOrigin.VERIFIED_FROM_CODE,
                    "framework.registration-v2",
                    candidate.registration_locator,
                    None,
                ),
            )
        if (
            candidate.handler_local_key is not None
            and candidate.handler_local_key not in declaration_keys
        ):
            locator = candidate.registration_locator
            language = language_by_path.get(locator.source_location.path) or "unknown"
            unresolved_key = local_record_key(
                language,
                locator.source_location.path,
                "unresolved_fact",
                locator.kind,
                (
                    locator.structural_path
                    if type(locator) is AstLocatorIR
                    else locator.structural_pointer
                ),
                locator.ordinal,
            )
            candidate = replace(
                candidate,
                handler_local_key=None,
                unresolved_fact_local_key=unresolved_key,
                evidence=IREvidence(
                    EvidenceOrigin.VERIFIED_FROM_CODE,
                    "framework.registration-v2",
                    locator,
                    None,
                ),
            )
            degraded_paths.add((language, locator.source_location.path))
        candidates.append(candidate)

    framework_by_segment = {
        key: candidate.framework or "generic"
        for candidate in candidates
        for key in candidate.framework_segment_keys
    }
    normalized_segments = []
    for segment in segments:
        if segment.evidence.origin is EvidenceOrigin.UNRESOLVED:
            segment = replace(
                segment,
                evidence=IREvidence(
                    EvidenceOrigin.VERIFIED_FROM_CODE,
                    "framework.pipeline-registration-v2",
                    segment.evidence.locator,
                    None,
                ),
            )
        target = segment.target
        if (
            type(target) is FrameworkLocalTarget
            and target.local_key not in declaration_keys
        ):
            descriptor = FrameworkBoundaryDescriptor(
                framework_by_segment.get(segment.local_key, "generic"),
                segment.framework_role,
                None,
                segment.evidence.locator,
                segment.evidence,
            )
            target = FrameworkBoundaryTarget(descriptor)
            locator = segment.evidence.locator
            path = locator.source_location.path
            degraded_paths.add((language_by_path.get(path) or "unknown", path))
        normalized_segments.append(replace(segment, target=target))
    segments = normalized_segments

    invalid_structure_keys = {
        structure.local_key
        for structure in extraction.structures
        if structure.owner_declaration_key not in declaration_keys
    }
    invalid_structure_keys.update(
        arm.branch_local_key
        for arm in extraction.branch_arms
        if arm.source_block_key not in block_keys or arm.target_block_key not in block_keys
    )
    invalid_structure_keys.update(
        scope.structure_key
        for scope in extraction.exception_scopes
        if scope.declaration_key not in declaration_keys
        or any(arm.target_block_key not in block_keys for arm in scope.catch_arms)
        or (
            scope.finally_block_key is not None
            and scope.finally_block_key not in block_keys
        )
    )
    structures = tuple(
        row for row in extraction.structures if row.local_key not in invalid_structure_keys
    )
    branch_arms = tuple(
        row for row in extraction.branch_arms if row.branch_local_key not in invalid_structure_keys
    )
    exception_scopes = tuple(
        row for row in extraction.exception_scopes if row.structure_key not in invalid_structure_keys
    )
    valid_scope_keys = {row.local_key for row in exception_scopes}

    def normalize_successor(successor, segment, ordinal: int):
        target_key = getattr(successor, "target_block_key", None)
        if target_key in declaration_keys:
            return AlwaysSuccessor(
                declarations_by_key[target_key].entry_block_key,
                successor.order,
            )
        if target_key is None or target_key in block_keys | segment_keys:
            invalid_typed_reference = (
                type(successor) is BranchSuccessor
                and successor.branch_arm_key in invalid_structure_keys
            ) or (
                type(successor) is ExceptionSuccessor
                and successor.exception_scope_key not in valid_scope_keys
            )
            if invalid_typed_reference:
                return AlwaysSuccessor(target_key, successor.order)
            return successor
        boundary_key = target_key
        descriptor = FrameworkBoundaryDescriptor(
            framework_by_segment.get(segment.local_key, "generic"),
            "unresolved_pipeline",
            None,
            segment.evidence.locator,
            IREvidence(
                EvidenceOrigin.UNRESOLVED,
                "framework.lifecycle-v2",
                segment.evidence.locator,
                None,
            ),
        )
        handler = next(
            (
                candidate.handler_local_key
                for candidate in candidates
                if segment.local_key in candidate.framework_segment_keys
                and candidate.handler_local_key in declaration_keys
            ),
            None,
        )
        if handler is None:
            raise ValueError("unresolved framework pipeline lacks a real continuation")
        if boundary_key not in segment_keys:
            segments.append(
                type(segment)(
                    boundary_key,
                    "unresolved_pipeline",
                    segment.pipeline_order + ordinal + 1,
                    FrameworkBoundaryTarget(descriptor),
                    AlwaysSuccessor(
                        declarations_by_key[handler].entry_block_key,
                        0,
                    ),
                    (),
                    segment.evidence,
                )
            )
            segment_keys.add(boundary_key)
        path = segment.evidence.locator.source_location.path
        degraded_paths.add((language_by_path.get(path) or "unknown", path))
        return AlwaysSuccessor(boundary_key, successor.order)

    normalized_segments = []
    for segment in tuple(segments):
        success = normalize_successor(segment.success_successor, segment, 0)
        shorts = tuple(
            sorted(
                (
                    normalize_successor(successor, segment, ordinal + 1)
                    for ordinal, successor in enumerate(segment.short_circuit_successors)
                ),
                key=lambda row: (row.order, row.kind, getattr(row, "target_block_key", "")),
            )
        )
        normalized_segments.append(
            replace(segment, success_successor=success, short_circuit_successors=shorts)
        )
    extra_segments = [
        row for row in segments if row.local_key not in {item.local_key for item in normalized_segments}
    ]
    segments = sorted((*normalized_segments, *extra_segments), key=lambda row: row.local_key)

    unmaterialized_candidates: set[str] = set()
    for ordinal, candidate in enumerate(candidates):
        if candidate.unresolved_fact_local_key is None:
            continue
        locator = candidate.registration_locator
        language = language_by_path.get(locator.source_location.path) or "unknown"
        registration_key = next(
            (
                row.local_key
                for row in base.declarations
                if type(locator) is AstLocatorIR
                and row.locator.source_location.path
                == locator.source_location.path
                and (
                    locator.structural_path == row.locator.structural_path
                    or locator.structural_path.startswith(
                        f"{row.locator.structural_path}/"
                    )
                )
            ),
            next(
                (
                    key
                    for key in candidate.framework_segment_keys
                    if key in segment_keys
                ),
                None,
            ),
        )
        if registration_key is None:
            unmaterialized_candidates.add(candidate.unresolved_fact_local_key)
            degraded_paths.add((language, locator.source_location.path))
            continue
        edge_key = local_record_key(
            language,
            locator.source_location.path,
            "entrypoint_unresolved_edge",
            locator.kind,
            locator.structural_path if type(locator) is AstLocatorIR else locator.structural_pointer,
            ordinal,
        )
        evidence = IREvidence(EvidenceOrigin.UNRESOLVED, "framework.lifecycle-v2", locator, None)
        boundary = FrameworkBoundaryDescriptor(
            candidate.framework or "generic",
            "handler",
            candidate.public_name,
            locator,
            evidence,
        )
        edges.append(
            EdgeFactIR(
                edge_key,
                registration_key,
                BoundaryTarget(boundary),
                Relation.ROUTES_TO,
                EdgeFlow.ALWAYS,
                None,
                None,
                None,
                None,
                ordinal,
                locator,
                evidence,
            )
        )
        if not any(
            row.local_key == candidate.unresolved_fact_local_key for row in unresolved
        ):
            unresolved.append(
            UnresolvedFact(
                candidate.unresolved_fact_local_key,
                EdgeSubjectIR(edge_key),
                ResolutionKind.ENTRYPOINT_HANDLER,
                CandidateSetKnowledge.NOT_APPLICABLE,
                "entrypoint_unresolved",
                "Which source handler receives this entrypoint?",
                ("inspect_route_configuration",),
                (locator,),
                (),
                (),
                Priority.HIGH,
                "The lifecycle cannot continue to a verified source handler.",
            )
            )

    coverage = list(base.coverage_events)
    coverage.extend(
        CoverageEvent(
            language,
            CoverageCapability.FRAMEWORK_LIFECYCLE,
            CoverageOutcome.PARTIAL,
            "framework_target_unresolved",
            path,
            0,
            1,
        )
        for language, path in sorted(degraded_paths)
    )
    resolved_base = replace(
        base,
        edge_facts=tuple(sorted(edges, key=lambda row: row.local_key)),
        unresolved_facts=tuple(sorted(unresolved, key=lambda row: row.local_key)),
        coverage_events=tuple(sorted(coverage, key=_coverage_key)),
    )
    resolved_extraction = replace(
        extraction,
        candidates=tuple(
            candidate
            for candidate in candidates
            if candidate.unresolved_fact_local_key not in unmaterialized_candidates
        ),
        framework_segments=tuple(segments),
        structures=structures,
        branch_arms=branch_arms,
        exception_scopes=exception_scopes,
    )
    return resolved_base, resolved_extraction


def assemble_graph_v2_adapter_result(
    context: ExtractionContext,
    syntax: Sequence[SyntaxIR],
    *,
    parse_coverage: Sequence[CoverageEvent] = (),
    registry: FrameworkAdapterRegistry | None = None,
) -> AdapterResult:
    """Assemble parsed structural facts and framework facts into one valid IR."""

    collected = tuple(sorted(syntax, key=lambda row: (row.language, row.path)))
    active_registry = registry or default_framework_registry()
    base = _structural_result(context, collected, parse_coverage)
    extraction = _language_entrypoints(context, collected, active_registry)
    resolved_base, resolved_extraction = _resolve_framework_references(
        context, base, extraction
    )
    result = aggregate_entrypoint_extraction(resolved_base, resolved_extraction)
    result.validate()
    return result


__all__ = ["assemble_graph_v2_adapter_result", "default_framework_registry"]
