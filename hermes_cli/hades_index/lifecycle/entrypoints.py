"""Framework-neutral discovery and identity normalization for entrypoints.

This module deliberately emits only immutable extraction facts.  Framework
adapters supply HTTP routing and lifecycle semantics separately; the generic
recognizer covers the non-HTTP roots every executable application may expose.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from hermes_cli.hades_graph_v2.model import (
    EntrypointIdentity,
    EntrypointKind,
    EntrypointNodeIdentity,
    MatchConstraints as GraphMatchConstraints,
    MethodSemantics,
    NodeKind,
    RegistrationAst,
    RegistrationConfig,
    RegistrationOccurrence,
    Trigger,
    TriggerKind,
)
from hermes_cli.hades_index.lifecycle.model import (
    AstLocatorIR,
    ConfigLocatorIR,
    CoverageCapability,
    CoverageEvent,
    CoverageOutcome,
    EntrypointCandidate,
    EvidenceOrigin,
    ExtractionContext,
    FrameworkPipelineSegment,
    IREvidence,
    MatchConstraints,
    SourceLocationIR,
    local_record_key,
)
from hermes_cli.hades_index.tree_sitter_adapter import StructuralSymbol, SyntaxIR

if TYPE_CHECKING:
    from hermes_cli.hades_index.lifecycle.frameworks import FrameworkAdapterRegistry


_EXECUTABLE_LANGUAGES = frozenset({"javascript", "php", "python", "typescript"})


@dataclass(frozen=True, slots=True)
class EntrypointExtraction:
    """Closed entrypoint facts emitted before artifact construction."""

    candidates: tuple[EntrypointCandidate, ...]
    framework_segments: tuple[FrameworkPipelineSegment, ...]
    coverage_events: tuple[CoverageEvent, ...]


def _candidate_key(candidate: EntrypointCandidate) -> tuple[object, ...]:
    locator = candidate.registration_locator
    structural = (
        locator.structural_path
        if type(locator) is AstLocatorIR
        else locator.structural_pointer
    )
    return (
        candidate.kind.value,
        candidate.framework or "",
        candidate.public_path or "",
        candidate.public_name or "",
        locator.source_location.path,
        structural,
        locator.ordinal,
    )


def _coverage_key(event: CoverageEvent) -> tuple[object, ...]:
    return (
        event.language,
        event.capability.value,
        event.outcome.value,
        event.reason_code or "",
        event.path or "",
    )


def merge_entrypoint_extractions(
    *extractions: EntrypointExtraction,
) -> EntrypointExtraction:
    """Combine closed extraction outputs without silently dropping collisions."""

    candidates = tuple(
        sorted(
            (
                candidate
                for extraction in extractions
                for candidate in extraction.candidates
            ),
            key=_candidate_key,
        )
    )
    if len({_candidate_key(candidate) for candidate in candidates}) != len(candidates):
        raise ValueError("duplicate entrypoint candidate identity")

    segments = tuple(
        sorted(
            (
                segment
                for extraction in extractions
                for segment in extraction.framework_segments
            ),
            key=lambda segment: segment.local_key,
        )
    )
    if len({segment.local_key for segment in segments}) != len(segments):
        raise ValueError("duplicate framework pipeline segment")

    coverage = tuple(
        sorted(
            (
                event
                for extraction in extractions
                for event in extraction.coverage_events
            ),
            key=_coverage_key,
        )
    )
    if len({_coverage_key(event) for event in coverage}) != len(coverage):
        raise ValueError("duplicate entrypoint coverage event")
    return EntrypointExtraction(candidates, segments, coverage)


def _symbol_entrypoint_kind(symbol: StructuralSymbol) -> EntrypointKind | None:
    """Classify an explicit conventional runtime root without guessing a route."""

    name = symbol.name.rsplit(".", 1)[-1].casefold()
    if name in {"main", "__main__"}:
        return EntrypointKind.PROCESS_MAIN
    if "command" in name:
        return EntrypointKind.CLI_COMMAND
    if any(token in name for token in ("schedule", "scheduled", "cron")):
        return EntrypointKind.SCHEDULED_JOB
    if any(token in name for token in ("consumer", "consume", "worker")):
        return EntrypointKind.QUEUE_CONSUMER
    if any(token in name for token in ("listener", "listen", "subscriber")):
        return EntrypointKind.EVENT_LISTENER
    if name.startswith(("api_", "public_")):
        return EntrypointKind.PUBLIC_API
    return None


def _trigger_for(kind: EntrypointKind) -> TriggerKind:
    return {
        EntrypointKind.PROCESS_MAIN: TriggerKind.PROCESS,
        EntrypointKind.CLI_COMMAND: TriggerKind.CLI,
        EntrypointKind.SCHEDULED_JOB: TriggerKind.SCHEDULE,
        EntrypointKind.QUEUE_CONSUMER: TriggerKind.QUEUE,
        EntrypointKind.EVENT_LISTENER: TriggerKind.EVENT,
        EntrypointKind.PUBLIC_API: TriggerKind.LIBRARY,
    }[kind]


def _source_digest(context: ExtractionContext, path: str) -> str:
    """Read one scoped file only long enough to calculate a safe locator digest."""

    return hashlib.sha256(context.file_accessor(Path(path))).hexdigest()


def _generic_candidate(
    context: ExtractionContext,
    syntax: SyntaxIR,
    symbol: StructuralSymbol,
    *,
    ordinal: int,
    file_sha256: str,
) -> EntrypointCandidate | None:
    kind = _symbol_entrypoint_kind(symbol)
    if kind is None:
        return None
    structural_path = f"entrypoints/{kind.value}/{ordinal}"
    locator = AstLocatorIR(
        SourceLocationIR(syntax.path, symbol.line, symbol.end_line, file_sha256),
        structural_path,
        0,
    )
    handler_key = local_record_key(
        syntax.language,
        syntax.path,
        "executable_declaration",
        "ast",
        f"symbols/{ordinal}",
        0,
    )
    return EntrypointCandidate(
        kind=kind,
        framework=None,
        method_semantics=MethodSemantics.NOT_APPLICABLE,
        methods=(),
        public_path=None,
        public_name=symbol.name,
        trigger=_trigger_for(kind),
        match_constraints=MatchConstraints(None, (), None),
        registration_locator=locator,
        handler_local_key=handler_key,
        unresolved_fact_local_key=None,
        framework_segment_keys=(),
        evidence=IREvidence(
            origin=EvidenceOrigin.VERIFIED_FROM_CODE,
            extractor=f"generic.{syntax.language}",
            locator=locator,
            inference_rule=None,
        ),
    )


def unsupported_language_extraction(
    syntax: Sequence[SyntaxIR],
) -> EntrypointExtraction:
    """Report unsupported languages explicitly; never substitute another adapter."""

    events = tuple(
        sorted(
            (
                CoverageEvent(
                    item.language,
                    CoverageCapability.ENTRYPOINT_DISCOVERY,
                    CoverageOutcome.UNSUPPORTED,
                    "unsupported_language",
                    item.path,
                    0,
                    1,
                )
                for item in syntax
            ),
            key=_coverage_key,
        )
    )
    return EntrypointExtraction((), (), events)


def extract_generic_entrypoints(
    context: ExtractionContext,
    syntax: Sequence[SyntaxIR],
) -> EntrypointExtraction:
    """Extract generic process/CLI/schedule/consumer/listener/API roots.

    The recognizer works from immutable syntax facts only.  It intentionally
    never treats a source file as an HTTP route and never selects Python as a
    fallback parser for another language.
    """

    supported = tuple(item for item in syntax if item.language in _EXECUTABLE_LANGUAGES)
    unsupported = tuple(
        item for item in syntax if item.language not in _EXECUTABLE_LANGUAGES
    )
    candidates: list[EntrypointCandidate] = []
    coverage: list[CoverageEvent] = []

    for item in sorted(supported, key=lambda value: (value.language, value.path)):
        root_symbols = tuple(
            (ordinal, symbol)
            for ordinal, symbol in enumerate(item.symbols)
            if _symbol_entrypoint_kind(symbol) is not None
        )
        try:
            file_sha256 = _source_digest(context, item.path) if root_symbols else ""
        except OSError:
            coverage.append(
                CoverageEvent(
                    item.language,
                    CoverageCapability.ENTRYPOINT_DISCOVERY,
                    CoverageOutcome.PARTIAL,
                    "file_read_failed",
                    item.path,
                    0,
                    1,
                )
            )
            continue
        found = [
            candidate
            for ordinal, symbol in root_symbols
            if (
                candidate := _generic_candidate(
                    context,
                    item,
                    symbol,
                    ordinal=ordinal,
                    file_sha256=file_sha256,
                )
            )
            is not None
        ]
        candidates.extend(found)
        coverage.append(
            CoverageEvent(
                item.language,
                CoverageCapability.ENTRYPOINT_DISCOVERY,
                CoverageOutcome.PARTIAL,
                "generic_syntax_only",
                item.path,
                len(found),
                0,
            )
        )

    extraction = EntrypointExtraction(
        tuple(sorted(candidates, key=_candidate_key)),
        (),
        tuple(sorted(coverage, key=_coverage_key)),
    )
    return merge_entrypoint_extractions(
        extraction, unsupported_language_extraction(unsupported)
    )


def sql_entrypoint_extraction(syntax: Sequence[SyntaxIR]) -> EntrypointExtraction:
    """SQL contributes data topology only; it cannot invent executable roots."""

    sql_syntax = tuple(item for item in syntax if item.language == "sql")
    foreign = tuple(item for item in syntax if item.language != "sql")
    events: list[CoverageEvent] = []
    for item in sql_syntax:
        for capability in (
            CoverageCapability.ENTRYPOINT_DISCOVERY,
            CoverageCapability.CALL_GRAPH,
            CoverageCapability.CONTROL_FLOW,
            CoverageCapability.FRAMEWORK_LIFECYCLE,
            CoverageCapability.EXCEPTIONS,
            CoverageCapability.ASYNC,
        ):
            events.append(
                CoverageEvent(
                    "sql",
                    capability,
                    CoverageOutcome.NOT_APPLICABLE,
                    None,
                    item.path,
                    0,
                    0,
                )
            )
    return merge_entrypoint_extractions(
        EntrypointExtraction((), (), tuple(sorted(events, key=_coverage_key))),
        unsupported_language_extraction(foreign),
    )


def extract_languages_entrypoints(
    context: ExtractionContext,
    syntax: Sequence[SyntaxIR],
    *,
    languages: frozenset[str],
    registry: FrameworkAdapterRegistry | None = None,
) -> EntrypointExtraction:
    """Delegate selected language syntax to registered framework adapters.

    A module only receives syntax in its own language.  Foreign syntax is
    reported as unsupported at this boundary rather than silently handed to
    Python or another parser.
    """

    if not languages or not languages.issubset(_EXECUTABLE_LANGUAGES):
        raise ValueError("generic entrypoint extraction requires executable languages")
    own_syntax = tuple(item for item in syntax if item.language in languages)
    foreign_syntax = tuple(item for item in syntax if item.language not in languages)
    generic = extract_generic_entrypoints(context, own_syntax)

    from hermes_cli.hades_index.lifecycle.frameworks import (
        FrameworkAdapterRegistry,
        run_framework_adapters,
    )

    if registry is None:
        active_registry = FrameworkAdapterRegistry()
    elif type(registry) is FrameworkAdapterRegistry:
        active_registry = registry
    else:
        raise TypeError("registry must be a FrameworkAdapterRegistry")
    framework_run = run_framework_adapters(active_registry, context, own_syntax)
    framework = EntrypointExtraction(
        framework_run.candidates,
        framework_run.framework_segments,
        (),
    )
    return merge_entrypoint_extractions(
        generic,
        framework,
        unsupported_language_extraction(foreign_syntax),
    )


def extract_language_entrypoints(
    context: ExtractionContext,
    syntax: Sequence[SyntaxIR],
    *,
    language: str,
    registry: FrameworkAdapterRegistry | None = None,
) -> EntrypointExtraction:
    """Delegate one language without selecting a parser for foreign syntax."""

    return extract_languages_entrypoints(
        context,
        syntax,
        languages=frozenset({language}),
        registry=registry,
    )


def _candidate_language(
    context: ExtractionContext,
    candidate: EntrypointCandidate,
) -> str:
    """Resolve a candidate language without using an implicit Python fallback."""

    if candidate.framework is not None:
        matches = tuple(
            record.language
            for record in context.detected_frameworks
            if record.name == candidate.framework
        )
        if len(matches) == 1:
            return matches[0]
    extractor_language = candidate.evidence.extractor.rsplit(".", 1)[-1]
    if extractor_language in context.detected_languages:
        return extractor_language
    if len(context.detected_languages) == 1:
        return context.detected_languages[0]
    raise ValueError("entrypoint candidate language is not uniquely known")


def normalized_entrypoint_identity(
    context: ExtractionContext,
    candidate: EntrypointCandidate,
) -> EntrypointNodeIdentity:
    """Return the one handler-independent v2 identity for every entrypoint.

    Generic and framework candidates both enter the builder through this
    function.  Effective handler and framework pipeline are deliberately
    excluded so a verified binding change cannot rename the public entrypoint.
    """

    locator = candidate.registration_locator
    occurrence: RegistrationOccurrence
    if type(locator) is AstLocatorIR:
        occurrence = RegistrationAst(
            "ast",
            locator.source_location.path,
            locator.structural_path,
            locator.ordinal,
        )
    elif type(locator) is ConfigLocatorIR:
        occurrence = RegistrationConfig(
            "config",
            locator.source_location.path,
            locator.structural_pointer,
            locator.ordinal,
        )
    else:  # Defensive: EntrypointCandidate already closes this union.
        raise TypeError("entrypoint registration locator must be AST or config")

    return EntrypointNodeIdentity(
        variant="entrypoint",
        workspace_binding_id=context.workspace_binding_id,
        language=_candidate_language(context, candidate),
        kind=NodeKind.ENTRYPOINT,
        path=locator.source_location.path,
        entrypoint_identity=EntrypointIdentity(
            entrypoint_kind=candidate.kind,
            framework=candidate.framework,
            method_semantics=candidate.method_semantics,
            methods=candidate.methods,
            public_path=candidate.public_path,
            public_name=candidate.public_name,
            trigger=Trigger(
                candidate.trigger,
                candidate.public_path or candidate.public_name,
            ),
            match_constraints=GraphMatchConstraints(
                candidate.match_constraints.host,
                candidate.match_constraints.schemes,
                candidate.match_constraints.condition_hash,
            ),
            registration_occurrence=occurrence,
        ),
    )


__all__ = [
    "EntrypointExtraction",
    "extract_generic_entrypoints",
    "extract_language_entrypoints",
    "extract_languages_entrypoints",
    "merge_entrypoint_extractions",
    "normalized_entrypoint_identity",
    "sql_entrypoint_extraction",
    "unsupported_language_extraction",
]
