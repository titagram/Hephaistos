"""Framework adapter boundary and framework-neutral entrypoint contracts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from hermes_cli.hades_graph_config import load_hades_graph_index_config
from hermes_cli.hades_graph_v2.model import (
    ConditionPolarity,
    EntrypointKind,
    MethodSemantics,
    SourceIdentity,
    StructureKind,
    StructureSubtype,
    TriggerKind,
)
from hermes_cli.hades_index.lifecycle.entrypoints import (
    EntrypointExtraction,
    extract_generic_entrypoints,
    normalized_entrypoint_identity,
)
from hermes_cli.hades_index.lifecycle.assembler import default_framework_registry
from hermes_cli.hades_index.lifecycle.frameworks import (
    FrameworkAdapterError,
    FrameworkAdapterRegistry,
    FrameworkDetection,
    FrameworkPipelineFacts,
    _validate_pipeline_fact_references,
    framework_pipeline_facts,
    run_framework_adapters,
)
from hermes_cli.hades_index.lifecycle.model import (
    AdapterResult,
    AsyncSuccessor,
    BasicBlock,
    BranchArm,
    BranchSuccessor,
    ConditionIR,
    ControlKind,
    CoverageEvent,
    EntrypointCandidate,
    ExceptionScope,
    ExceptionCatchArm,
    ExceptionSuccessor,
    ExtractionContext,
    InventoryFile,
    FrameworkPipelineSegment,
    FrameworkLocalTarget,
    IRValidationError,
    StructureIR,
    local_record_key,
)
from tests.hermes_cli.test_hades_lifecycle_ir import _valid_result
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
        inventory_files=(
            InventoryFile(
                "src/app.py",
                hashlib.sha256(source.read_bytes()).hexdigest(),
                languages[0],
                True,
            ),
        ),
        excluded_path_count=0,
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
        trigger_value=generic.trigger_value,
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

    def pipeline_facts(
        self, context: ExtractionContext, candidate: EntrypointCandidate
    ) -> FrameworkPipelineFacts:
        self.calls.append("pipeline_facts")
        return FrameworkPipelineFacts()


def _assert_framework_pipeline_closes_adapter_result(
    candidates: tuple[EntrypointCandidate, ...],
    segments: tuple[FrameworkPipelineSegment, ...],
    facts: FrameworkPipelineFacts,
) -> None:
    """Prove concrete adapter output closes at the frozen AdapterResult seam."""

    base = _valid_result()
    declaration_template = base.declarations[0]
    block_template = base.blocks[0]
    required_declaration_keys = {
        key
        for key in (
            *(candidate.handler_local_key for candidate in candidates),
            *(structure.owner_declaration_key for structure in facts.structures),
            *(scope.declaration_key for scope in facts.exception_scopes),
            *(
                segment.target.local_key
                for segment in segments
                if type(segment.target) is FrameworkLocalTarget
            ),
            *(
                successor.target_local_key
                for segment in segments
                for successor in (
                    segment.success_successor,
                    *segment.short_circuit_successors,
                )
                if type(successor) is AsyncSuccessor
            ),
        )
        if key is not None
    }
    declarations = []
    blocks = []
    for ordinal, local_key in enumerate(sorted(required_declaration_keys)):
        block_key = local_record_key(
            "framework",
            declaration_template.locator.source_location.path,
            "adapter_result_stub_block",
            "ast",
            f"framework_adapter_stub/{ordinal}",
            ordinal,
        )
        locator = replace(
            declaration_template.locator,
            structural_path=f"framework_adapter_stub/{ordinal}",
            ordinal=ordinal,
        )
        declaration = replace(
            declaration_template,
            local_key=local_key,
            owner_declaration_key=None,
            name=f"framework_stub_{ordinal}",
            qualified_name=f"framework.stub_{ordinal}",
            locator=locator,
            entry_block_key=block_key,
            normal_exit_block_keys=(block_key,),
            exception_exit_block_keys=(),
        )
        declarations.append(declaration)
        blocks.append(
            BasicBlock(
                block_key,
                local_key,
                ControlKind.RETURN,
                0,
                replace(locator, structural_path=f"{locator.structural_path}/entry"),
                (),
            )
        )
    result = AdapterResult(
        tuple(sorted(declarations, key=lambda item: item.local_key)),
        tuple(sorted(blocks, key=lambda item: item.local_key)),
        facts.branch_arms,
        facts.structures,
        (),
        (),
        facts.exception_scopes,
        facts.terminals,
        (),
        tuple(sorted(segments, key=lambda item: item.local_key)),
        candidates,
        (),
        (),
        (),
    )
    result.validate()


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


def test_all_required_framework_golden_suites_are_registered(tmp_path: Path) -> None:
    """The production registry owns every framework backed by a golden suite."""

    assert tuple(
        (adapter.language, adapter.framework)
        for adapter in default_framework_registry().adapters
    ) == (
        ("python", "django"),
        ("python", "fastapi"),
        ("php", "laravel"),
        ("php", "symfony"),
        ("javascript", "express"),
        ("typescript", "express"),
        ("javascript", "nextjs"),
        ("typescript", "nextjs"),
    )

    from tests.hermes_cli.test_hades_lifecycle_django import (
        test_recurses_static_includes_preserves_order_namespace_prefix_and_converters,
    )
    from tests.hermes_cli.test_hades_lifecycle_express import (
        test_nested_router_mount_composes_literal_path_prefix_exactly,
        test_typescript_adapter_preserves_language_in_every_generated_identity,
    )
    from tests.hermes_cli.test_hades_lifecycle_fastapi import (
        test_nested_routers_methods_dependencies_cache_cleanup_and_background_child,
    )
    from tests.hermes_cli.test_hades_lifecycle_laravel import (
        test_detects_laravel_and_preserves_nested_group_registration_order,
    )
    from tests.hermes_cli.test_hades_lifecycle_nextjs import (
        test_app_route_static_get_and_post_exports_are_method_entrypoints,
    )
    from tests.hermes_cli.test_hades_lifecycle_symfony import (
        test_routes_keep_yaml_and_attribute_collision_and_apply_import_context,
    )

    cases = (
        (
            test_detects_laravel_and_preserves_nested_group_registration_order,
            "laravel",
            (),
        ),
        (
            test_routes_keep_yaml_and_attribute_collision_and_apply_import_context,
            "symfony",
            (),
        ),
        (
            test_recurses_static_includes_preserves_order_namespace_prefix_and_converters,
            "django",
            (),
        ),
        (
            test_nested_routers_methods_dependencies_cache_cleanup_and_background_child,
            "fastapi",
            (),
        ),
        (
            test_nested_router_mount_composes_literal_path_prefix_exactly,
            "express-js",
            (),
        ),
        (
            test_typescript_adapter_preserves_language_in_every_generated_identity,
            "express-ts",
            (),
        ),
        (
            test_app_route_static_get_and_post_exports_are_method_entrypoints,
            "next-js",
            ("javascript",),
        ),
        (
            test_app_route_static_get_and_post_exports_are_method_entrypoints,
            "next-ts",
            ("typescript",),
        ),
    )
    for test_case, directory, arguments in cases:
        case_root = tmp_path / directory
        case_root.mkdir()
        test_case(case_root, *arguments)

    generic_root = tmp_path / "generic"
    generic_root.mkdir()
    test_generic_entrypoints_cover_non_http_runtime_roots(generic_root)


def test_framework_pipeline_protocol_rejects_orphan_duplicate_exception_scope(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    candidate = _candidate(context, framework="fastapi", public_name="main")
    assert candidate.handler_local_key is not None
    target_key = local_record_key(
        "python", "src/app.py", "basic_block", "ast", "body/catch", 0
    )
    structure_key = local_record_key(
        "python", "src/app.py", "structure", "ast", "body/try", 0
    )
    segment = FrameworkPipelineSegment(
        local_record_key(
            "python", "src/app.py", "framework_segment", "ast", "pipeline/0", 0
        ),
        "exception_handler",
        0,
        FrameworkLocalTarget(candidate.handler_local_key),
        ExceptionSuccessor(target_key, structure_key, "RuntimeError", 0),
        (),
        candidate.evidence,
    )
    locator = replace(candidate.registration_locator, structural_path="body/try")
    structure = StructureIR(
        structure_key,
        StructureKind.EXCEPTION_SCOPE,
        candidate.handler_local_key,
        "body/try",
        0,
        StructureSubtype.FRAMEWORK_EXCEPTION_HANDLER,
        None,
        None,
        replace(candidate.evidence, locator=locator),
    )
    scope = ExceptionScope(
        local_record_key(
            "python", "src/app.py", "exception_scope", "ast", "body/try", 0
        ),
        structure_key,
        candidate.handler_local_key,
        locator,
        (ExceptionCatchArm("RuntimeError", target_key, 0),),
        None,
        None,
    )
    orphan = replace(
        scope,
        local_key=local_record_key(
            "python",
            "src/app.py",
            "exception_scope",
            "ast",
            "body/try/orphan",
            0,
        ),
    )

    with pytest.raises(
        FrameworkAdapterError, match="duplicate exception scope occurrence"
    ):
        _validate_pipeline_fact_references(
            candidate,
            (segment,),
            FrameworkPipelineFacts(
                structures=(structure,), exception_scopes=(scope, orphan)
            ),
        )


def test_framework_pipeline_facts_preserve_catch_type_target_pairs(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    candidate = _candidate(context, framework="fastapi", public_name="main")
    assert candidate.handler_local_key is not None
    scope_key = local_record_key(
        "python", "src/app.py", "structure", "ast", "pipeline/scope", 0
    )
    a_target = local_record_key(
        "python", "src/app.py", "basic_block", "ast", "pipeline/a", 0
    )
    z_target = local_record_key(
        "python", "src/app.py", "basic_block", "ast", "pipeline/z", 0
    )
    segment = FrameworkPipelineSegment(
        local_record_key(
            "python", "src/app.py", "framework_segment", "ast", "pipeline/0", 0
        ),
        "exception_handler",
        0,
        FrameworkLocalTarget(candidate.handler_local_key),
        ExceptionSuccessor(a_target, scope_key, "ZError", 0),
        (ExceptionSuccessor(z_target, scope_key, "AError", 1),),
        candidate.evidence,
    )

    facts = framework_pipeline_facts(
        candidate,
        (segment,),
        lambda _segment, _successor: pytest.fail("unexpected return successor"),
    )
    scope = facts.exception_scopes[0]

    assert [
        (item.caught_type_name, item.target_block_key, item.arm_ordinal)
        for item in scope.catch_arms
    ] == [("ZError", a_target, 0), ("AError", z_target, 1)]


def test_framework_pipeline_protocol_rejects_orphan_and_duplicate_branch_arms(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    candidate = _candidate(context, framework="fastapi", public_name="main")
    assert candidate.handler_local_key is not None
    structure_key = local_record_key(
        "python", "src/app.py", "structure", "ast", "pipeline/branch", 0
    )
    source_key = local_record_key(
        "python", "src/app.py", "basic_block", "ast", "pipeline/source", 0
    )
    target_key = local_record_key(
        "python", "src/app.py", "basic_block", "ast", "pipeline/target", 0
    )
    orphan_target_key = local_record_key(
        "python", "src/app.py", "basic_block", "ast", "pipeline/orphan", 0
    )
    segment = FrameworkPipelineSegment(
        local_record_key(
            "python", "src/app.py", "framework_segment", "ast", "pipeline/0", 0
        ),
        "guard",
        0,
        FrameworkLocalTarget(candidate.handler_local_key),
        BranchSuccessor(target_key, structure_key, 0, 0),
        (),
        candidate.evidence,
    )
    locator = replace(candidate.registration_locator, structural_path="pipeline/branch")
    structure = StructureIR(
        structure_key,
        StructureKind.BRANCH_GROUP,
        candidate.handler_local_key,
        "pipeline/branch",
        0,
        StructureSubtype.FRAMEWORK_SHORT_CIRCUIT,
        None,
        None,
        replace(candidate.evidence, locator=locator),
    )
    condition = ConditionIR("predicate", "allowed", _DIGEST, ConditionPolarity.TRUE)
    arm = BranchArm(
        structure_key,
        source_key,
        target_key,
        ConditionPolarity.TRUE,
        condition,
        0,
    )
    orphan = BranchArm(
        structure_key,
        source_key,
        orphan_target_key,
        ConditionPolarity.FALSE,
        ConditionIR("predicate", "not_allowed", "b" * 64, ConditionPolarity.FALSE),
        1,
    )

    with pytest.raises(FrameworkAdapterError, match="orphan branch arms"):
        _validate_pipeline_fact_references(
            candidate,
            (segment,),
            FrameworkPipelineFacts(structures=(structure,), branch_arms=(arm, orphan)),
        )
    with pytest.raises(FrameworkAdapterError, match="duplicate branch arm identity"):
        FrameworkPipelineFacts(branch_arms=(arm, arm))


def test_framework_pipeline_protocol_rejects_ambiguous_same_target_branch_arms(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    candidate = _candidate(context, framework="fastapi", public_name="main")
    assert candidate.handler_local_key is not None
    structure_key = local_record_key(
        "python", "src/app.py", "structure", "ast", "pipeline/ambiguous", 0
    )
    first_source = local_record_key(
        "python", "src/app.py", "basic_block", "ast", "pipeline/source_a", 0
    )
    second_source = local_record_key(
        "python", "src/app.py", "basic_block", "ast", "pipeline/source_b", 0
    )
    target_key = local_record_key(
        "python", "src/app.py", "basic_block", "ast", "pipeline/target", 0
    )
    segment = FrameworkPipelineSegment(
        local_record_key(
            "python", "src/app.py", "framework_segment", "ast", "pipeline/0", 0
        ),
        "guard",
        0,
        FrameworkLocalTarget(candidate.handler_local_key),
        BranchSuccessor(target_key, structure_key, 0, 0),
        (),
        candidate.evidence,
    )
    locator = replace(
        candidate.registration_locator, structural_path="pipeline/ambiguous"
    )
    structure = StructureIR(
        structure_key,
        StructureKind.BRANCH_GROUP,
        candidate.handler_local_key,
        "pipeline/ambiguous",
        0,
        StructureSubtype.FRAMEWORK_SHORT_CIRCUIT,
        None,
        None,
        replace(candidate.evidence, locator=locator),
    )
    arms = (
        BranchArm(
            structure_key,
            first_source,
            target_key,
            ConditionPolarity.TRUE,
            ConditionIR("predicate", "allowed", _DIGEST, ConditionPolarity.TRUE),
            0,
        ),
        BranchArm(
            structure_key,
            second_source,
            target_key,
            ConditionPolarity.FALSE,
            ConditionIR("predicate", "not_allowed", "b" * 64, ConditionPolarity.FALSE),
            1,
        ),
    )

    with pytest.raises(FrameworkAdapterError, match="orphan branch arms"):
        _validate_pipeline_fact_references(
            candidate,
            (segment,),
            FrameworkPipelineFacts(structures=(structure,), branch_arms=arms),
        )


def test_framework_pipeline_protocol_rejects_duplicate_structure_occurrence(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    candidate = _candidate(context, framework="fastapi", public_name="main")
    assert candidate.handler_local_key is not None
    locator = replace(candidate.registration_locator, structural_path="pipeline/branch")
    structure = StructureIR(
        local_record_key(
            "python", "src/app.py", "structure", "ast", "pipeline/branch", 0
        ),
        StructureKind.BRANCH_GROUP,
        candidate.handler_local_key,
        "pipeline/branch",
        0,
        StructureSubtype.FRAMEWORK_SHORT_CIRCUIT,
        None,
        None,
        replace(candidate.evidence, locator=locator),
    )
    duplicate = replace(
        structure,
        local_key=local_record_key(
            "python",
            "src/app.py",
            "structure_duplicate",
            "ast",
            "pipeline/branch",
            0,
        ),
    )

    with pytest.raises(FrameworkAdapterError, match="duplicate structure occurrence"):
        FrameworkPipelineFacts(structures=(structure, duplicate))


def test_exception_scope_rejects_conflicting_catch_pairs(tmp_path: Path) -> None:
    context = _context(tmp_path)
    candidate = _candidate(context, framework="fastapi", public_name="main")
    assert candidate.handler_local_key is not None
    structure_key = local_record_key(
        "python", "src/app.py", "structure", "ast", "pipeline/scope", 0
    )
    first_target = local_record_key(
        "python", "src/app.py", "basic_block", "ast", "pipeline/catch_a", 0
    )
    second_target = local_record_key(
        "python", "src/app.py", "basic_block", "ast", "pipeline/catch_b", 0
    )

    with pytest.raises(IRValidationError, match="conflicting_catch_arm"):
        ExceptionScope(
            local_record_key(
                "python",
                "src/app.py",
                "exception_scope",
                "ast",
                "pipeline/scope",
                0,
            ),
            structure_key,
            candidate.handler_local_key,
            replace(candidate.registration_locator, structural_path="pipeline/scope"),
            (
                ExceptionCatchArm("RuntimeError", first_target, 0),
                ExceptionCatchArm("RuntimeError", second_target, 1),
            ),
            None,
            None,
        )


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
    assert first.calls == ["detect", "entrypoints", "pipeline", "pipeline_facts"]
    assert second.calls == ["detect", "entrypoints", "pipeline", "pipeline_facts"]


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
    assert adapter.calls == ["detect", "entrypoints", "pipeline", "pipeline_facts"]


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
    assert python_adapter.calls == [
        "detect",
        "entrypoints",
        "pipeline",
        "pipeline_facts",
    ]
    assert php_adapter.calls == []

    php_entrypoints(
        context, (_syntax(language="php", names=("main",)),), registry=registry
    )
    assert python_adapter.calls == [
        "detect",
        "entrypoints",
        "pipeline",
        "pipeline_facts",
    ]
    assert php_adapter.calls == [
        "detect",
        "entrypoints",
        "pipeline",
        "pipeline_facts",
    ]

    unknown_context = _context(tmp_path, languages=("rust",))
    unknown = python_entrypoints(
        unknown_context,
        (_syntax(language="rust", path="src/main.rs", names=("main",)),),
        registry=registry,
    )
    assert unknown.candidates == ()
    assert python_adapter.calls == [
        "detect",
        "entrypoints",
        "pipeline",
        "pipeline_facts",
    ]
    assert php_adapter.calls == [
        "detect",
        "entrypoints",
        "pipeline",
        "pipeline_facts",
    ]


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
    assert typescript_adapter.calls == [
        "detect",
        "entrypoints",
        "pipeline",
        "pipeline_facts",
    ]
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
        trigger_value=framework.trigger_value,
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
