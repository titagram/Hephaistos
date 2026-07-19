"""Direct canonical IR emission for statically verified ORM table facts."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from hermes_cli.hades_graph_v2.model import EvidenceOrigin, NodeKind

from .model import (
    AdapterResult,
    AstLocatorIR,
    CoverageCapability,
    CoverageEvent,
    CoverageOutcome,
    DataNodeIR,
    EdgeFactIR,
    ExtractionContext,
    IREvidence,
    LocalNodeTarget,
    Relation,
    SourceLocationIR,
    local_record_key,
)


@dataclass(frozen=True, slots=True)
class TableSpec:
    language: str
    orm: str
    path: str
    line: int
    table: str
    model: str | None = None
    foreign_keys: tuple[tuple[str, str, int], ...] = ()


def data_budget_omission(language: str, path: str) -> CoverageEvent:
    """Create the exact per-file ledger entry for a stopped data adapter."""

    return CoverageEvent(
        language,
        CoverageCapability.DATA_ACCESS,
        CoverageOutcome.PARTIAL,
        "resource_budget_reached",
        path,
        0,
        1,
    )


def append_coverage_events(
    result: AdapterResult, events: tuple[CoverageEvent, ...]
) -> AdapterResult:
    """Merge deterministic adapter coverage and revalidate the closed IR."""

    if not events:
        return result
    merged = AdapterResult(
        declarations=result.declarations,
        blocks=result.blocks,
        branch_arms=result.branch_arms,
        structures=result.structures,
        call_sites=result.call_sites,
        edge_facts=result.edge_facts,
        exception_scopes=result.exception_scopes,
        terminals=result.terminals,
        effects=result.effects,
        framework_segments=result.framework_segments,
        entrypoints=result.entrypoints,
        unresolved_facts=result.unresolved_facts,
        coverage_events=tuple(
            sorted(
                (*result.coverage_events, *events),
                key=lambda row: (
                    row.language,
                    row.capability.value,
                    row.outcome.value,
                    row.reason_code or "",
                    row.path or "",
                ),
            )
        ),
        diagnostics=result.diagnostics,
        source_nodes=result.source_nodes,
        data_nodes=result.data_nodes,
    )
    merged.validate()
    return merged


def table_adapter_result(
    context: ExtractionContext,
    specs: tuple[TableSpec, ...],
) -> AdapterResult:
    """Materialize parser-owned table specs without passing through a v1 graph."""

    inventory = {item.path: item for item in context.inventory_files}
    ordered = tuple(
        sorted(
            specs,
            key=lambda row: (row.language, row.path, row.line, row.table, row.orm),
        )
    )
    nodes: list[DataNodeIR] = []
    edges: list[EdgeFactIR] = []
    coverage: list[CoverageEvent] = []
    table_keys: dict[str, list[str]] = defaultdict(list)
    table_key_by_spec: dict[TableSpec, str] = {}
    represented_by_path: Counter[tuple[str, str]] = Counter()

    for ordinal, spec in enumerate(ordered):
        row = inventory[spec.path]
        structural_path = f"data/{spec.orm}/table/{ordinal}"
        locator = AstLocatorIR(
            SourceLocationIR(spec.path, spec.line, spec.line, row.file_sha256),
            structural_path,
            ordinal,
        )
        evidence = IREvidence(
            EvidenceOrigin.VERIFIED_FROM_CODE,
            f"{spec.orm}.data-v2",
            locator,
            None,
        )
        table_key = local_record_key(
            spec.language,
            spec.path,
            "data_table",
            "ast",
            structural_path,
            ordinal,
        )
        nodes.append(
            DataNodeIR(
                table_key,
                spec.language,
                NodeKind.TABLE,
                spec.table,
                f"{spec.orm}:{spec.table}",
                spec.table,
                locator,
                evidence,
            )
        )
        table_keys[spec.table].append(table_key)
        table_key_by_spec[spec] = table_key
        represented_by_path[(spec.language, spec.path)] += 1

    partial_by_path: Counter[tuple[str, str]] = Counter()
    for spec_ordinal, spec in enumerate(ordered):
        source_key = table_key_by_spec[spec]
        row = inventory[spec.path]
        for fk_ordinal, (_column, target_table, line) in enumerate(spec.foreign_keys):
            targets = tuple(sorted(table_keys.get(target_table, ())))
            structural_path = f"data/{spec.orm}/foreign_key/{spec_ordinal}/{fk_ordinal}"
            locator = AstLocatorIR(
                SourceLocationIR(spec.path, line, line, row.file_sha256),
                structural_path,
                fk_ordinal,
            )
            edge_key = local_record_key(
                spec.language,
                spec.path,
                "data_foreign_key",
                "ast",
                structural_path,
                fk_ordinal,
            )
            if len(targets) != 1:
                partial_by_path[(spec.language, spec.path)] += 1
                continue
            evidence = IREvidence(
                EvidenceOrigin.VERIFIED_FROM_CODE,
                f"{spec.orm}.data-v2",
                locator,
                None,
            )
            edges.append(
                EdgeFactIR(
                    edge_key,
                    source_key,
                    LocalNodeTarget(targets[0]),
                    Relation.REFERENCES,
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

    for language, path in sorted(represented_by_path):
        omitted = partial_by_path[(language, path)]
        coverage.append(
            CoverageEvent(
                language,
                CoverageCapability.DATA_ACCESS,
                CoverageOutcome.PARTIAL if omitted else CoverageOutcome.FULL,
                "external_boundary_unresolved" if omitted else None,
                path,
                represented_by_path[(language, path)],
                omitted,
            )
        )

    result = AdapterResult(
        declarations=(),
        blocks=(),
        branch_arms=(),
        structures=(),
        call_sites=(),
        edge_facts=tuple(sorted(edges, key=lambda row: row.local_key)),
        exception_scopes=(),
        terminals=(),
        effects=(),
        framework_segments=(),
        entrypoints=(),
        unresolved_facts=(),
        coverage_events=tuple(
            sorted(
                coverage,
                key=lambda row: (
                    row.language,
                    row.capability.value,
                    row.outcome.value,
                    row.reason_code or "",
                    row.path or "",
                ),
            )
        ),
        diagnostics=(),
        data_nodes=tuple(sorted(nodes, key=lambda row: row.local_key)),
    )
    result.validate()
    return result


__all__ = [
    "TableSpec",
    "append_coverage_events",
    "data_budget_omission",
    "table_adapter_result",
]
