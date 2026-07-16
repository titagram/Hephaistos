"""Finite intraprocedural control-flow views over frozen adapter IR.

This module does not enumerate runtime paths.  It makes each already-emitted
successor explicit once, including back edges and terminals, so traversal can
later operate on a bounded directed multigraph.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .model import (
    AdapterResult,
    AlwaysSuccessor,
    AsyncSuccessor,
    BasicBlock,
    BranchArm,
    BranchSuccessor,
    CoverageEvent,
    ExceptionSuccessor,
    LoopSuccessor,
    ReturnSuccessor,
)


@dataclass(frozen=True, slots=True)
class ControlFlowEdge:
    """One explicit CFG successor; ``target_key`` is never a generated path."""

    source_block_key: str
    target_key: str
    kind: Literal["always", "branch", "exception", "loop", "async", "return"]
    order: int


@dataclass(frozen=True, slots=True)
class ControlFlowResult:
    """The finite control-flow projection used by later interprocedural work."""

    blocks: tuple[BasicBlock, ...]
    edges: tuple[ControlFlowEdge, ...]
    branch_arms: tuple[BranchArm, ...]
    terminal_keys: tuple[str, ...]
    exception_scope_keys: tuple[str, ...]
    coverage_events: tuple[CoverageEvent, ...]
    has_cycles: bool
    # Counting whole paths is intentionally unsupported: branch products and
    # loops are exactly why lifecycle v2 traverses edge-stage states instead.
    path_count: None = None


def _edge_from_successor(source: str, successor: object) -> ControlFlowEdge:
    if type(successor) is AlwaysSuccessor:
        return ControlFlowEdge(
            source, successor.target_block_key, "always", successor.order
        )
    if type(successor) is BranchSuccessor:
        return ControlFlowEdge(
            source, successor.target_block_key, "branch", successor.order
        )
    if type(successor) is ExceptionSuccessor:
        return ControlFlowEdge(
            source, successor.target_block_key, "exception", successor.order
        )
    if type(successor) is LoopSuccessor:
        return ControlFlowEdge(
            source, successor.target_block_key, "loop", successor.order
        )
    if type(successor) is AsyncSuccessor:
        return ControlFlowEdge(
            source, successor.target_local_key, "async", successor.order
        )
    if type(successor) is ReturnSuccessor:
        return ControlFlowEdge(
            source, successor.terminal_local_key, "return", successor.order
        )
    raise TypeError("adapter result contains an unknown successor variant")


def _has_cycle(edges: tuple[ControlFlowEdge, ...], block_keys: frozenset[str]) -> bool:
    """Detect cycles without walking paths or imposing an arbitrary depth bound."""

    adjacency: dict[str, tuple[str, ...]] = {
        key: tuple(
            sorted(
                edge.target_key
                for edge in edges
                if edge.source_block_key == key and edge.target_key in block_keys
            )
        )
        for key in block_keys
    }
    # 0 = unvisited, 1 = on the explicit DFS stack, 2 = completed.  Keeping
    # the traversal iterative avoids turning Python's recursion limit into an
    # accidental CFG-depth limit for an otherwise finite, valid program.
    color = {key: 0 for key in block_keys}
    for root in sorted(block_keys):
        if color[root] != 0:
            continue
        color[root] = 1
        stack: list[tuple[str, int]] = [(root, 0)]
        while stack:
            node, index = stack[-1]
            targets = adjacency[node]
            if index >= len(targets):
                color[node] = 2
                stack.pop()
                continue
            target = targets[index]
            stack[-1] = (node, index + 1)
            if color[target] == 1:
                return True
            if color[target] == 0:
                color[target] = 1
                stack.append((target, 0))
    return False


def build_control_flow(result: AdapterResult) -> ControlFlowResult:
    """Return each CFG fact exactly once after validating the adapter boundary.

    ``AdapterResult`` validation establishes all cross-record references before
    this function runs.  The output therefore contains no guessed edge, no
    expansion of a loop, and no path-count/BFS cap.
    """

    result.validate()
    edges = tuple(
        sorted(
            (
                _edge_from_successor(block.local_key, successor)
                for block in result.blocks
                for successor in block.successors
            ),
            key=lambda edge: (
                edge.source_block_key,
                edge.order,
                edge.kind,
                edge.target_key,
            ),
        )
    )
    block_keys = frozenset(block.local_key for block in result.blocks)
    return ControlFlowResult(
        blocks=result.blocks,
        edges=edges,
        branch_arms=result.branch_arms,
        terminal_keys=tuple(terminal.local_key for terminal in result.terminals),
        exception_scope_keys=tuple(
            scope.local_key for scope in result.exception_scopes
        ),
        coverage_events=result.coverage_events,
        has_cycles=_has_cycle(edges, block_keys),
    )


__all__ = ["ControlFlowEdge", "ControlFlowResult", "build_control_flow"]
