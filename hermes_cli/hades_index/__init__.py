"""Entry point for pluggable code graph indexing — dispatches to per-language indexer modules."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hermes_cli.hades_graph_v2.model import GraphArtifactV2
    from hermes_cli.hades_index.lifecycle.model import AdapterResult, ExtractionContext


def build_canonical_graph(
    context: ExtractionContext,
    results: Sequence[AdapterResult],
    *,
    generated_at: Callable[[], str] | None = None,
) -> GraphArtifactV2:
    """Build the canonical graph-v2 artifact from closed adapter IR only."""

    from hermes_cli.hades_index.lifecycle.builder import GraphBuilder

    return GraphBuilder(generated_at=generated_at).build(context, results)
