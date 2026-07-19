"""Direct canonical IR emission for statically verified ORM table facts."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from hermes_cli.hades_graph_v2.model import (
    CandidateSetKnowledge,
    EvidenceOrigin,
    NodeKind,
    Priority,
    ResolutionKind,
)

from .model import (
    AdapterResult,
    AstLocatorIR,
    BoundaryTarget,
    CoverageCapability,
    CoverageEvent,
    CoverageOutcome,
    DataNodeIR,
    EdgeFactIR,
    EdgeSubjectIR,
    ExtractionContext,
    FrameworkBoundaryDescriptor,
    IREvidence,
    LocalNodeTarget,
    Relation,
    SourceLocationIR,
    UnresolvedFact,
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
    unresolved: list[UnresolvedFact] = []
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
                evidence = IREvidence(
                    EvidenceOrigin.UNRESOLVED,
                    f"{spec.orm}.data-v2",
                    locator,
                    None,
                )
                edges.append(
                    EdgeFactIR(
                        edge_key,
                        source_key,
                        BoundaryTarget(
                            FrameworkBoundaryDescriptor(
                                spec.orm,
                                "table_reference",
                                target_table,
                                locator,
                                evidence,
                            )
                        ),
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
                unresolved.append(
                    UnresolvedFact(
                        local_record_key(
                            spec.language,
                            spec.path,
                            "data_foreign_key_uncertainty",
                            "ast",
                            structural_path,
                            fk_ordinal,
                        ),
                        EdgeSubjectIR(edge_key),
                        ResolutionKind.EXTERNAL_TARGET,
                        (
                            CandidateSetKnowledge.INCOMPLETE
                            if targets
                            else CandidateSetKnowledge.NOT_APPLICABLE
                        ),
                        "external_boundary_unresolved",
                        "Which unique table is referenced by this foreign key?",
                        ("inspect_schema_declarations",),
                        (locator,),
                        targets,
                        (),
                        Priority.HIGH,
                        "The foreign-key target cannot be selected authoritatively.",
                    )
                )
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
                None,
                path,
                represented_by_path[(language, path)],
                0,
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
        unresolved_facts=tuple(sorted(unresolved, key=lambda row: row.local_key)),
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
