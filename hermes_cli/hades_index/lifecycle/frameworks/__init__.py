"""Registry and closed protocol for framework-specific lifecycle adapters."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Protocol

from hermes_cli.hades_index.lifecycle.model import (
    AstLocatorIR,
    BranchArm,
    ConfigLocatorIR,
    CoverageEvent,
    EntrypointCandidate,
    ExceptionCatchArm,
    ExceptionScope,
    ExtractionContext,
    FrameworkPipelineSegment,
    BranchSuccessor,
    ExceptionSuccessor,
    ReturnSuccessor,
    StructureIR,
    Terminal,
    TerminalKind,
    local_record_key,
)
from hermes_cli.hades_graph_v2.model import StructureKind, StructureSubtype
from hermes_cli.hades_index.tree_sitter_adapter import SyntaxIR


_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")


class FrameworkAdapter(Protocol):
    """A framework adapter owns only its detection, roots, and pipeline facts."""

    language: str
    framework: str

    def detect(self, context: ExtractionContext) -> "FrameworkDetection": ...

    def entrypoints(
        self,
        context: ExtractionContext,
        syntax: Sequence[SyntaxIR],
    ) -> tuple[EntrypointCandidate, ...]: ...

    def pipeline(
        self,
        context: ExtractionContext,
        candidate: EntrypointCandidate,
    ) -> tuple[FrameworkPipelineSegment, ...]: ...

    def pipeline_facts(
        self,
        context: ExtractionContext,
        candidate: EntrypointCandidate,
    ) -> "FrameworkPipelineFacts": ...

    def coverage_events(
        self, context: ExtractionContext
    ) -> tuple[CoverageEvent, ...]: ...


class FrameworkAdapterError(ValueError):
    """A deterministic failure at the closed framework-adapter boundary."""


@dataclass(frozen=True, slots=True)
class FrameworkDetection:
    language: str
    framework: str
    detected: bool

    def __post_init__(self) -> None:
        if not _IDENTIFIER_RE.fullmatch(self.language):
            raise FrameworkAdapterError("framework detection language is invalid")
        if not _IDENTIFIER_RE.fullmatch(self.framework):
            raise FrameworkAdapterError("framework detection name is invalid")
        if type(self.detected) is not bool:
            raise FrameworkAdapterError("framework detection flag must be boolean")


@dataclass(frozen=True, slots=True)
class FrameworkTerminalSpec:
    kind: TerminalKind
    public_status: int | None = None
    exception_type: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, TerminalKind):
            raise FrameworkAdapterError("framework terminal kind is invalid")
        if self.kind is TerminalKind.EXCEPTION and self.exception_type is None:
            raise FrameworkAdapterError("framework exception terminal requires a type")
        if self.kind is not TerminalKind.EXCEPTION and self.exception_type is not None:
            raise FrameworkAdapterError(
                "non-exception framework terminal carries a type"
            )


@dataclass(frozen=True, slots=True)
class FrameworkPipelineFacts:
    """Closed typed facts referenced by one framework pipeline."""

    terminals: tuple[Terminal, ...] = ()
    structures: tuple[StructureIR, ...] = ()
    branch_arms: tuple[BranchArm, ...] = ()
    exception_scopes: tuple[ExceptionScope, ...] = ()

    def __post_init__(self) -> None:
        for values, expected, label in (
            (self.terminals, Terminal, "terminals"),
            (self.structures, StructureIR, "structures"),
            (self.branch_arms, BranchArm, "branch_arms"),
            (self.exception_scopes, ExceptionScope, "exception_scopes"),
        ):
            if type(values) is not tuple or any(
                type(item) is not expected for item in values
            ):
                raise FrameworkAdapterError(f"framework pipeline {label} are invalid")
        identities = (
            ("terminal", tuple(item.local_key for item in self.terminals)),
            ("structure", tuple(item.local_key for item in self.structures)),
            (
                "branch arm",
                tuple(
                    (item.branch_local_key, item.arm_ordinal)
                    for item in self.branch_arms
                ),
            ),
            (
                "exception scope",
                tuple(item.local_key for item in self.exception_scopes),
            ),
        )
        for label, values in identities:
            if len(values) != len(set(values)):
                raise FrameworkAdapterError(
                    f"framework pipeline emitted duplicate {label} identity"
                )
        structure_occurrences = tuple(
            (
                item.kind,
                item.owner_declaration_key,
                item.structural_path,
                item.ordinal,
                item.subtype,
            )
            for item in self.structures
        )
        if len(structure_occurrences) != len(set(structure_occurrences)):
            raise FrameworkAdapterError(
                "framework pipeline emitted duplicate structure occurrence"
            )
        scope_structures = tuple(item.structure_key for item in self.exception_scopes)
        if len(scope_structures) != len(set(scope_structures)):
            raise FrameworkAdapterError(
                "framework pipeline emitted duplicate exception scope occurrence"
            )


def _framework_fact_locator(
    candidate: EntrypointCandidate,
    suffix: str,
) -> AstLocatorIR | ConfigLocatorIR:
    locator = candidate.registration_locator
    if type(locator) is AstLocatorIR:
        return replace(locator, structural_path=f"{locator.structural_path}/{suffix}")
    return replace(
        locator,
        structural_pointer=f"{locator.structural_pointer}/{suffix}",
    )


def framework_pipeline_facts(
    candidate: EntrypointCandidate,
    pipeline: Sequence[FrameworkPipelineSegment],
    terminal_spec: Callable[
        [FrameworkPipelineSegment, ReturnSuccessor], FrameworkTerminalSpec
    ],
) -> FrameworkPipelineFacts:
    """Materialize exact typed facts at the adapter boundary, never in the builder."""

    terminals: dict[str, Terminal] = {}
    exception_successors: dict[
        str, list[tuple[FrameworkPipelineSegment, ExceptionSuccessor]]
    ] = {}
    for segment in pipeline:
        for successor in (segment.success_successor, *segment.short_circuit_successors):
            if type(successor) is ReturnSuccessor:
                spec = terminal_spec(segment, successor)
                locator = _framework_fact_locator(
                    candidate,
                    (
                        f"framework_terminal/{segment.pipeline_order}/"
                        f"{successor.order}/{spec.kind.value}"
                    ),
                )
                value = Terminal(
                    successor.terminal_local_key,
                    segment.local_key,
                    spec.kind,
                    spec.public_status,
                    spec.exception_type,
                    locator,
                )
                previous = terminals.setdefault(value.local_key, value)
                if previous != value:
                    raise FrameworkAdapterError(
                        "framework terminal key has conflicting typed facts"
                    )
            elif type(successor) is ExceptionSuccessor:
                exception_successors.setdefault(
                    successor.exception_scope_key, []
                ).append((segment, successor))

    structures: list[StructureIR] = []
    scopes: list[ExceptionScope] = []
    if exception_successors and candidate.handler_local_key is None:
        raise FrameworkAdapterError(
            "framework exception facts require a resolved handler declaration"
        )
    for ordinal, (structure_key, values) in enumerate(
        sorted(exception_successors.items())
    ):
        locator = _framework_fact_locator(candidate, f"framework_exception/{ordinal}")
        catch_arms = tuple(
            sorted(
                (
                    ExceptionCatchArm(
                        successor.caught_type_name,
                        successor.target_block_key,
                        successor.order,
                    )
                    for _segment, successor in values
                ),
                key=lambda item: item.arm_ordinal,
            )
        )
        locator = replace(locator, ordinal=ordinal)
        evidence = replace(candidate.evidence, locator=locator)
        structural = (
            locator.structural_path
            if type(locator) is AstLocatorIR
            else locator.structural_pointer
        )
        structures.append(
            StructureIR(
                structure_key,
                StructureKind.EXCEPTION_SCOPE,
                candidate.handler_local_key or "",
                structural,
                ordinal,
                StructureSubtype.FRAMEWORK_EXCEPTION_HANDLER,
                None,
                None,
                evidence,
            )
        )
        scope_key = local_record_key(
            "framework",
            locator.source_location.path,
            "exception_scope_fact",
            "ast" if type(locator) is AstLocatorIR else "config",
            structural,
            ordinal,
        )
        scopes.append(
            ExceptionScope(
                scope_key,
                structure_key,
                candidate.handler_local_key or "",
                locator,
                catch_arms,
                None,
                None,
            )
        )
    return FrameworkPipelineFacts(
        tuple(terminals[key] for key in sorted(terminals)),
        tuple(sorted(structures, key=lambda item: item.local_key)),
        (),
        tuple(sorted(scopes, key=lambda item: item.local_key)),
    )


def _validate_pipeline_fact_references(
    candidate: EntrypointCandidate,
    pipeline: Sequence[FrameworkPipelineSegment],
    facts: FrameworkPipelineFacts,
) -> None:
    terminals = {item.local_key: item for item in facts.terminals}
    structures = {item.local_key: item for item in facts.structures}
    arms = {
        (item.branch_local_key, item.arm_ordinal): item for item in facts.branch_arms
    }
    referenced_terminals: set[str] = set()
    referenced_structures: set[str] = set()
    referenced_arms: set[tuple[str, int]] = set()
    referenced_exception_arms: dict[str, set[tuple[str | None, str, int]]] = {}
    for segment in pipeline:
        for successor in (segment.success_successor, *segment.short_circuit_successors):
            if type(successor) is ReturnSuccessor:
                terminal = terminals.get(successor.terminal_local_key)
                if terminal is None or terminal.source_block_key != segment.local_key:
                    raise FrameworkAdapterError(
                        "framework return successor lacks its exact typed terminal"
                    )
                referenced_terminals.add(terminal.local_key)
            elif type(successor) is ExceptionSuccessor:
                structure = structures.get(successor.exception_scope_key)
                if (
                    structure is None
                    or structure.kind is not StructureKind.EXCEPTION_SCOPE
                    or structure.owner_declaration_key != candidate.handler_local_key
                ):
                    raise FrameworkAdapterError(
                        "framework exception successor lacks its exact typed scope"
                    )
                referenced_structures.add(structure.local_key)
                referenced_exception_arms.setdefault(structure.local_key, set()).add((
                    successor.caught_type_name,
                    successor.target_block_key,
                    successor.order,
                ))
            elif type(successor) is BranchSuccessor:
                structure = structures.get(successor.branch_arm_key)
                arm = arms.get((successor.branch_arm_key, successor.arm_ordinal))
                if (
                    structure is None
                    or structure.kind is not StructureKind.BRANCH_GROUP
                    or arm is None
                    or arm.target_block_key != successor.target_block_key
                ):
                    raise FrameworkAdapterError(
                        "framework branch successor lacks its exact typed arm"
                    )
                referenced_structures.add(structure.local_key)
                referenced_arms.add((
                    successor.branch_arm_key,
                    successor.arm_ordinal,
                ))
    if referenced_terminals != set(terminals):
        raise FrameworkAdapterError("framework pipeline emitted orphan terminals")
    if referenced_structures != set(structures):
        raise FrameworkAdapterError("framework pipeline emitted orphan structures")
    if referenced_arms != set(arms):
        raise FrameworkAdapterError("framework pipeline emitted orphan branch arms")
    scopes_by_structure = {item.structure_key: item for item in facts.exception_scopes}
    if set(scopes_by_structure) != set(referenced_exception_arms):
        raise FrameworkAdapterError("framework pipeline emitted orphan exception scope")
    for structure_key, expected_arms in referenced_exception_arms.items():
        scope = scopes_by_structure[structure_key]
        structure = structures[structure_key]
        structural = (
            scope.locator.structural_path
            if type(scope.locator) is AstLocatorIR
            else scope.locator.structural_pointer
        )
        if (
            structure.owner_declaration_key != scope.declaration_key
            or structure.structural_path != structural
            or structure.ordinal != scope.locator.ordinal
        ):
            raise FrameworkAdapterError(
                "framework exception scope lacks its exact StructureIR"
            )
        actual_arms = {
            (item.caught_type_name, item.target_block_key, item.arm_ordinal)
            for item in scope.catch_arms
        }
        if actual_arms != expected_arms:
            raise FrameworkAdapterError(
                "framework exception scope has conflicting catch arms"
            )
    if any(
        item.kind is StructureKind.EXCEPTION_SCOPE
        and item.local_key not in scopes_by_structure
        for item in facts.structures
    ):
        raise FrameworkAdapterError(
            "framework exception StructureIR lacks its exact exception scope"
        )


@dataclass(frozen=True, slots=True)
class FrameworkAdapterRun:
    detections: tuple[FrameworkDetection, ...]
    candidates: tuple[EntrypointCandidate, ...]
    framework_segments: tuple[FrameworkPipelineSegment, ...]
    pipeline_facts: FrameworkPipelineFacts = FrameworkPipelineFacts()
    coverage_events: tuple[CoverageEvent, ...] = ()


class FrameworkAdapterRegistry:
    """Ordered, duplicate-free registry used by explicit index commands only."""

    def __init__(self) -> None:
        self._adapters: list[FrameworkAdapter] = []

    @property
    def adapters(self) -> tuple[FrameworkAdapter, ...]:
        return tuple(self._adapters)

    def register(self, adapter: FrameworkAdapter) -> None:
        language = getattr(adapter, "language", None)
        framework = getattr(adapter, "framework", None)
        if (
            not isinstance(language, str)
            or not _IDENTIFIER_RE.fullmatch(language)
            or not isinstance(framework, str)
            or not _IDENTIFIER_RE.fullmatch(framework)
        ):
            raise FrameworkAdapterError(
                "framework adapter requires lower language and framework names"
            )
        if not callable(getattr(adapter, "coverage_events", None)):
            raise FrameworkAdapterError("framework adapter requires coverage_events")
        if not callable(getattr(adapter, "pipeline_facts", None)):
            raise FrameworkAdapterError("framework adapter requires pipeline_facts")
        key = (language, framework)
        if any((item.language, item.framework) == key for item in self._adapters):
            raise FrameworkAdapterError(
                f"duplicate framework adapter: {language}/{framework}"
            )
        self._adapters.append(adapter)


def run_framework_adapters(
    registry: FrameworkAdapterRegistry,
    context: ExtractionContext,
    syntax: Sequence[SyntaxIR],
    *,
    languages: frozenset[str] | None = None,
) -> FrameworkAdapterRun:
    """Run selected detected adapters once in explicit registration order."""

    detections: list[FrameworkDetection] = []
    candidates: list[EntrypointCandidate] = []
    segments: list[FrameworkPipelineSegment] = []
    coverage_events: list[CoverageEvent] = []
    seen_segments: set[str] = set()
    fact_records: dict[tuple[str, object], object] = {}

    def insert_fact(family: str, key: object, value: object) -> None:
        identity = (family, key)
        previous = fact_records.setdefault(identity, value)
        if previous != value:
            raise FrameworkAdapterError(
                "framework pipeline fact has conflicting deterministic identity"
            )

    for adapter in registry.adapters:
        if languages is not None and adapter.language not in languages:
            continue
        relevant_syntax = tuple(
            item for item in syntax if item.language == adapter.language
        )
        if not relevant_syntax:
            continue
        detection = adapter.detect(context)
        if type(detection) is not FrameworkDetection:
            raise FrameworkAdapterError(
                "framework adapter must return FrameworkDetection"
            )
        if (detection.language, detection.framework) != (
            adapter.language,
            adapter.framework,
        ):
            raise FrameworkAdapterError(
                "framework adapter detection does not match registration"
            )
        if not detection.detected:
            continue
        detections.append(detection)
        adapter_candidates = adapter.entrypoints(context, relevant_syntax)
        adapter_coverage = adapter.coverage_events(context)
        if type(adapter_coverage) is not tuple or any(
            type(event) is not CoverageEvent for event in adapter_coverage
        ):
            raise FrameworkAdapterError(
                "framework adapter emitted invalid coverage events"
            )
        coverage_events.extend(adapter_coverage)
        for candidate in adapter_candidates:
            if type(candidate) is not EntrypointCandidate:
                raise FrameworkAdapterError(
                    "framework adapter emitted a non-entrypoint candidate"
                )
            if candidate.framework != adapter.framework:
                raise FrameworkAdapterError(
                    "framework entrypoint does not match adapter"
                )
            pipeline = adapter.pipeline(context, candidate)
            if any(
                type(segment) is not FrameworkPipelineSegment for segment in pipeline
            ):
                raise FrameworkAdapterError(
                    "framework pipeline emitted an invalid segment"
                )
            if candidate.framework_segment_keys != tuple(
                segment.local_key for segment in pipeline
            ):
                raise FrameworkAdapterError(
                    "framework entrypoint pipeline keys do not match emitted segments"
                )
            for segment in pipeline:
                if segment.local_key in seen_segments:
                    raise FrameworkAdapterError("duplicate framework pipeline segment")
                seen_segments.add(segment.local_key)
                segments.append(segment)
            facts = adapter.pipeline_facts(context, candidate)
            if type(facts) is not FrameworkPipelineFacts:
                raise FrameworkAdapterError(
                    "framework adapter must return FrameworkPipelineFacts"
                )
            _validate_pipeline_fact_references(candidate, pipeline, facts)
            for value in facts.terminals:
                insert_fact("terminal", value.local_key, value)
            for value in facts.structures:
                insert_fact("structure", value.local_key, value)
            for value in facts.branch_arms:
                insert_fact(
                    "branch_arm", (value.branch_local_key, value.arm_ordinal), value
                )
            for value in facts.exception_scopes:
                insert_fact("exception_scope", value.local_key, value)
            candidates.append(candidate)

    return FrameworkAdapterRun(
        tuple(detections),
        tuple(candidates),
        tuple(segments),
        FrameworkPipelineFacts(
            tuple(
                value
                for (family, _key), value in sorted(
                    fact_records.items(), key=lambda item: repr(item[0])
                )
                if family == "terminal"
            ),
            tuple(
                value
                for (family, _key), value in sorted(
                    fact_records.items(), key=lambda item: repr(item[0])
                )
                if family == "structure"
            ),
            tuple(
                value
                for (family, _key), value in sorted(
                    fact_records.items(), key=lambda item: repr(item[0])
                )
                if family == "branch_arm"
            ),
            tuple(
                value
                for (family, _key), value in sorted(
                    fact_records.items(), key=lambda item: repr(item[0])
                )
                if family == "exception_scope"
            ),
        ),
        tuple(coverage_events),
    )


__all__ = [
    "FrameworkAdapter",
    "FrameworkAdapterError",
    "FrameworkAdapterRegistry",
    "FrameworkAdapterRun",
    "FrameworkDetection",
    "FrameworkPipelineFacts",
    "FrameworkTerminalSpec",
    "framework_pipeline_facts",
    "run_framework_adapters",
]
