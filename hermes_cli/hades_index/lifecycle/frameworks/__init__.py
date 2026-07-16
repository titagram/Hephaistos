"""Registry and closed protocol for framework-specific lifecycle adapters."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from hermes_cli.hades_index.lifecycle.model import (
    EntrypointCandidate,
    ExtractionContext,
    FrameworkPipelineSegment,
)
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
class FrameworkAdapterRun:
    detections: tuple[FrameworkDetection, ...]
    candidates: tuple[EntrypointCandidate, ...]
    framework_segments: tuple[FrameworkPipelineSegment, ...]


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
    seen_segments: set[str] = set()

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
        for candidate in adapter.entrypoints(context, relevant_syntax):
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
            candidates.append(candidate)

    return FrameworkAdapterRun(tuple(detections), tuple(candidates), tuple(segments))


__all__ = [
    "FrameworkAdapter",
    "FrameworkAdapterError",
    "FrameworkAdapterRegistry",
    "FrameworkAdapterRun",
    "FrameworkDetection",
    "run_framework_adapters",
]
