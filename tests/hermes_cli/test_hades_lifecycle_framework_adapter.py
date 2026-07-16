"""Framework adapter boundary and framework-neutral entrypoint contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from hermes_cli.hades_graph_config import load_hades_graph_index_config
from hermes_cli.hades_graph_v2.model import (
    EntrypointKind,
    MethodSemantics,
    SourceIdentity,
    TriggerKind,
)
from hermes_cli.hades_index.lifecycle.entrypoints import (
    EntrypointExtraction,
    extract_generic_entrypoints,
    normalized_entrypoint_identity,
)
from hermes_cli.hades_index.lifecycle.frameworks import (
    FrameworkAdapterError,
    FrameworkAdapterRegistry,
    FrameworkDetection,
    run_framework_adapters,
)
from hermes_cli.hades_index.lifecycle.model import (
    CoverageEvent,
    EntrypointCandidate,
    ExtractionContext,
    FrameworkPipelineSegment,
    local_record_key,
)
from hermes_cli.hades_index.php import extract_lifecycle_entrypoints as php_entrypoints
from hermes_cli.hades_index.python import (
    extract_lifecycle_entrypoints as python_entrypoints,
)
from hermes_cli.hades_index.sql import extract_lifecycle_entrypoints as sql_entrypoints
from hermes_cli.hades_index.typescript import (
    extract_lifecycle_entrypoints as typescript_entrypoints,
)
from hermes_cli.hades_index.tree_sitter_adapter import (
    ParsedFile,
    StructuralSymbol,
    SyntaxIR,
)


_DIGEST = "a" * 64


def _context(
    root: Path, *, languages: tuple[str, ...] = ("python",)
) -> ExtractionContext:
    source = root / "src" / "app.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("def main(): pass\n", encoding="utf-8")
    return ExtractionContext(
        workspace_root=root,
        project_id="project",
        workspace_binding_id="binding",
        source_identity=SourceIdentity(None, _DIGEST, False, None),
        graph_config=load_hades_graph_index_config({}),
        detected_languages=languages,
        detected_frameworks=(),
        composer_metadata=(),
        python_metadata=(),
        package_metadata=(),
        tsconfig_metadata=(),
        file_accessor=lambda path: (root / path).read_bytes(),
    )


def _syntax(
    *,
    language: str = "python",
    path: str = "src/app.py",
    names: tuple[str, ...] = (),
) -> SyntaxIR:
    return SyntaxIR(
        ParsedFile(
            path=path,
            language=language,
            symbols=tuple(
                StructuralSymbol(name, "function", index + 1, index + 1)
                for index, name in enumerate(names)
            ),
            imports=(),
            calls=(),
        ),
        (),
    )


def _candidate(
    context: ExtractionContext,
    *,
    framework: str,
    public_name: str,
) -> EntrypointCandidate:
    generic = extract_generic_entrypoints(
        context, (_syntax(names=("main",)),)
    ).candidates[0]
    return EntrypointCandidate(
        kind=generic.kind,
        framework=framework,
        method_semantics=generic.method_semantics,
        methods=generic.methods,
        public_path=generic.public_path,
        public_name=public_name,
        trigger=generic.trigger,
        match_constraints=generic.match_constraints,
        registration_locator=generic.registration_locator,
        handler_local_key=generic.handler_local_key,
        unresolved_fact_local_key=None,
        framework_segment_keys=(),
        evidence=generic.evidence,
    )


@dataclass
class _Adapter:
    language: str
    framework: str
    entrypoint_name: str
    calls: list[str] = field(default_factory=list)

    def detect(self, context: ExtractionContext) -> FrameworkDetection:
        self.calls.append("detect")
        return FrameworkDetection(self.language, self.framework, True)

    def entrypoints(
        self, context: ExtractionContext, syntax: tuple[SyntaxIR, ...]
    ) -> tuple[EntrypointCandidate, ...]:
        self.calls.append("entrypoints")
        assert all(item.language == self.language for item in syntax)
        return (
            _candidate(
                context, framework=self.framework, public_name=self.entrypoint_name
            ),
        )

    def pipeline(
        self, context: ExtractionContext, candidate: EntrypointCandidate
    ) -> tuple[FrameworkPipelineSegment, ...]:
        self.calls.append("pipeline")
        return ()

    def coverage_events(self, context: ExtractionContext) -> tuple[CoverageEvent, ...]:
        return ()


@dataclass
class _LegacyAdapterWithoutCoverage:
    language: str = "python"
    framework: str = "legacy"


def test_registry_preserves_registration_order_and_rejects_duplicate_framework() -> (
    None
):
    registry = FrameworkAdapterRegistry()
    first = _Adapter("python", "fastapi", "first")
    second = _Adapter("php", "symfony", "second")

    registry.register(first)
    registry.register(second)

    assert registry.adapters == (first, second)
    with pytest.raises(FrameworkAdapterError, match="duplicate framework adapter"):
        registry.register(_Adapter("python", "fastapi", "duplicate"))


def test_registry_requires_the_formal_coverage_events_adapter_method() -> None:
    with pytest.raises(FrameworkAdapterError, match="coverage_events"):
        FrameworkAdapterRegistry().register(_LegacyAdapterWithoutCoverage())


def test_all_detected_framework_adapters_run_in_registration_order(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path, languages=("php", "python"))
    registry = FrameworkAdapterRegistry()
    first = _Adapter("python", "fastapi", "fastapi-main")
    second = _Adapter("php", "symfony", "symfony-main")
    registry.register(first)
    registry.register(second)

    result = run_framework_adapters(
        registry,
        context,
        (
            _syntax(language="php", names=("main",)),
            _syntax(language="python", names=("main",)),
        ),
    )

    assert tuple(detection.framework for detection in result.detections) == (
        "fastapi",
        "symfony",
    )
    assert tuple(candidate.public_name for candidate in result.candidates) == (
        "fastapi-main",
        "symfony-main",
    )
    assert first.calls == ["detect", "entrypoints", "pipeline"]
    assert second.calls == ["detect", "entrypoints", "pipeline"]


def test_language_module_delegates_its_syntax_to_registered_frameworks(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    adapter = _Adapter("python", "fastapi", "framework-main")
    registry = FrameworkAdapterRegistry()
    registry.register(adapter)

    result = python_entrypoints(
        context,
        (_syntax(names=("main",)),),
        registry=registry,
    )

    assert any(candidate.framework == "fastapi" for candidate in result.candidates)
    assert adapter.calls == ["detect", "entrypoints", "pipeline"]


def test_language_delegates_never_run_foreign_or_unknown_registry_adapters(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path, languages=("php", "python"))
    registry = FrameworkAdapterRegistry()
    python_adapter = _Adapter("python", "fastapi", "python-main")
    php_adapter = _Adapter("php", "symfony", "php-main")
    registry.register(python_adapter)
    registry.register(php_adapter)

    python_entrypoints(
        context, (_syntax(language="python", names=("main",)),), registry=registry
    )
    assert python_adapter.calls == ["detect", "entrypoints", "pipeline"]
    assert php_adapter.calls == []

    php_entrypoints(
        context, (_syntax(language="php", names=("main",)),), registry=registry
    )
    assert python_adapter.calls == ["detect", "entrypoints", "pipeline"]
    assert php_adapter.calls == ["detect", "entrypoints", "pipeline"]

    unknown_context = _context(tmp_path, languages=("rust",))
    unknown = python_entrypoints(
        unknown_context,
        (_syntax(language="rust", path="src/main.rs", names=("main",)),),
        registry=registry,
    )
    assert unknown.candidates == ()
    assert python_adapter.calls == ["detect", "entrypoints", "pipeline"]
    assert php_adapter.calls == ["detect", "entrypoints", "pipeline"]


def test_composite_language_delegate_skips_registered_adapter_without_syntax(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path, languages=("javascript", "typescript"))
    registry = FrameworkAdapterRegistry()
    typescript_adapter = _Adapter("typescript", "nextjs", "typescript-main")
    javascript_adapter = _Adapter("javascript", "express", "javascript-main")
    registry.register(typescript_adapter)
    registry.register(javascript_adapter)

    result = typescript_entrypoints(
        context,
        (_syntax(language="typescript", names=("main",)),),
        registry=registry,
    )

    assert any(candidate.framework == "nextjs" for candidate in result.candidates)
    assert typescript_adapter.calls == ["detect", "entrypoints", "pipeline"]
    assert javascript_adapter.calls == []


def test_generic_entrypoints_cover_non_http_runtime_roots(tmp_path: Path) -> None:
    context = _context(tmp_path)
    result = extract_generic_entrypoints(
        context,
        (
            _syntax(
                names=(
                    "main",
                    "daily_command",
                    "nightly_schedule",
                    "orders_consumer",
                    "audit_listener",
                    "public_health",
                )
            ),
        ),
    )

    assert isinstance(result, EntrypointExtraction)
    assert tuple(candidate.kind for candidate in result.candidates) == (
        EntrypointKind.CLI_COMMAND,
        EntrypointKind.EVENT_LISTENER,
        EntrypointKind.PROCESS_MAIN,
        EntrypointKind.PUBLIC_API,
        EntrypointKind.QUEUE_CONSUMER,
        EntrypointKind.SCHEDULED_JOB,
    )
    assert all(
        candidate.method_semantics is MethodSemantics.NOT_APPLICABLE
        for candidate in result.candidates
    )
    assert all(candidate.methods == () for candidate in result.candidates)
    assert {candidate.trigger for candidate in result.candidates} == {
        TriggerKind.CLI,
        TriggerKind.EVENT,
        TriggerKind.PROCESS,
        TriggerKind.LIBRARY,
        TriggerKind.QUEUE,
        TriggerKind.SCHEDULE,
    }


def test_generic_and_framework_entrypoints_share_handler_independent_identity(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    generic = extract_generic_entrypoints(
        context, (_syntax(names=("main",)),)
    ).candidates[0]
    framework = _candidate(context, framework="fastapi", public_name="main")
    changed_handler = EntrypointCandidate(
        kind=framework.kind,
        framework=framework.framework,
        method_semantics=framework.method_semantics,
        methods=framework.methods,
        public_path=framework.public_path,
        public_name=framework.public_name,
        trigger=framework.trigger,
        match_constraints=framework.match_constraints,
        registration_locator=framework.registration_locator,
        handler_local_key=local_record_key(
            "python", "src/app.py", "executable_declaration", "ast", "symbol/other", 0
        ),
        unresolved_fact_local_key=None,
        framework_segment_keys=(),
        evidence=framework.evidence,
    )

    assert normalized_entrypoint_identity(
        context, framework
    ) == normalized_entrypoint_identity(context, changed_handler)
    assert (
        normalized_entrypoint_identity(context, generic).entrypoint_identity.framework
        is None
    )


def test_sql_declares_control_flow_not_applicable_and_never_invents_entrypoints(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path, languages=("sql",))
    result = sql_entrypoints(
        context, (_syntax(language="sql", path="schema.sql", names=("main",)),)
    )

    assert result.candidates == ()
    outcomes = {
        (event.capability.value, event.outcome.value)
        for event in result.coverage_events
    }
    assert ("control_flow", "not_applicable") in outcomes
    assert ("entrypoint_discovery", "not_applicable") in outcomes


def test_unknown_language_is_reported_unsupported_without_python_fallback(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path, languages=("rust",))
    result = extract_generic_entrypoints(
        context, (_syntax(language="rust", path="src/main.rs", names=("main",)),)
    )

    assert result.candidates == ()
    assert {
        (event.language, event.outcome.value) for event in result.coverage_events
    } == {("rust", "unsupported")}
    assert (
        python_entrypoints(
            context, (_syntax(language="rust", path="src/main.rs", names=("main",)),)
        ).candidates
        == ()
    )


def test_generic_entrypoint_file_failure_is_a_typed_partial_coverage_event(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)

    result = extract_generic_entrypoints(
        context, (_syntax(path="src/missing.py", names=("main",)),)
    )

    assert result.candidates == ()
    assert {
        (
            event.outcome.value,
            event.reason_code,
            event.represented_count,
            event.omitted_count,
        )
        for event in result.coverage_events
    } == {("partial", "file_read_failed", 0, 1)}
