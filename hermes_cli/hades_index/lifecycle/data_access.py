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
            targets = table_keys.get(target_table, ())
            if len(targets) != 1:
                partial_by_path[(spec.language, spec.path)] += 1
                continue
            structural_path = f"data/{spec.orm}/foreign_key/{spec_ordinal}/{fk_ordinal}"
            locator = AstLocatorIR(
                SourceLocationIR(spec.path, line, line, row.file_sha256),
                structural_path,
                fk_ordinal,
            )
            evidence = IREvidence(
                EvidenceOrigin.VERIFIED_FROM_CODE,
                f"{spec.orm}.data-v2",
                locator,
                None,
            )
            edges.append(
                EdgeFactIR(
                    local_record_key(
                        spec.language,
                        spec.path,
                        "data_foreign_key",
                        "ast",
                        structural_path,
                        fk_ordinal,
                    ),
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


__all__ = ["TableSpec", "table_adapter_result"]
