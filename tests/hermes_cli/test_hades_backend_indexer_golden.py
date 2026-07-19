"""Golden assertions for the canonical graph-v2 indexer boundary."""

from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from hermes_cli.hades_graph_v2 import artifact_to_payload, validate_artifact
from hermes_cli.hades_graph_v2.model import NodeKind, StructureKind
from hermes_cli.hades_index import build_canonical_graph
from hermes_cli.hades_index.aggregate import aggregate_adapter_results
from hermes_cli.hades_index.lifecycle.entrypoints import (
    EntrypointExtraction,
    aggregate_entrypoint_extraction,
)
from hermes_cli.hades_index.lifecycle.frameworks import FrameworkAdapterRegistry
from hermes_cli.hades_index.lifecycle.model import (
    AdapterResult,
    AlwaysSuccessor,
    AstLocatorIR,
    BasicBlock,
    ControlKind,
    CoverageCapability,
    CoverageEvent,
    CoverageOutcome,
    IRValidationError,
    InventoryFile,
    FrameworkLocalTarget,
    AsyncSuccessor,
    DeclarationIdentityKind,
    ExecutableDeclaration,
    ExceptionSuccessor,
    Modifier,
    local_record_key,
)
from tests.hermes_cli.test_hades_lifecycle_ir import _valid_result
from tests.hermes_cli.test_hades_lifecycle_traversal import _complex_result, _context


def test_canonical_v2_builder_is_exposed_at_the_indexer_boundary(tmp_path):
    artifact = build_canonical_graph(
        _context(tmp_path),
        (_valid_result(),),
        generated_at=lambda: "2026-07-19T12:00:00Z",
    )

    assert artifact.schema == "hades.code_graph.v2"
    assert artifact.graph_contract.version == "hades.graph_artifact.v2"
    assert artifact.graph_contract.artifact_graph_version != "0" * 64
    assert tuple(item.id for item in artifact.nodes) == tuple(
        sorted(item.id for item in artifact.nodes)
    )
    assert tuple(item.id for item in artifact.edges) == tuple(
        sorted(item.id for item in artifact.edges)
    )
    validate_artifact(artifact)


def test_canonical_indexer_output_is_permutation_invariant(tmp_path):
    result = _complex_result()

    first = build_canonical_graph(
        _context(tmp_path),
        (result, result),
        generated_at=lambda: "2026-07-19T12:00:00Z",
    )
    second = build_canonical_graph(
        _context(tmp_path),
        tuple(reversed((result, result))),
        generated_at=lambda: "2026-07-19T12:00:00Z",
    )

    assert artifact_to_payload(first) == artifact_to_payload(second)


def test_v2_aggregation_rejects_cross_adapter_semantic_collisions():
    first = _valid_result()
    declaration = first.declarations[0]
    conflicting = replace(
        first,
        declarations=(replace(declaration, name="different_name"),),
    )

    with pytest.raises(IRValidationError, match="semantic_collision"):
        aggregate_adapter_results((first, conflicting))


def _empty_result(*events: CoverageEvent) -> AdapterResult:
    return AdapterResult((), (), (), (), (), (), (), (), (), (), (), (), events, ())


def test_inventory_ledger_materializes_failure_only_file_and_counts_it(tmp_path):
    event = CoverageEvent(
        "python",
        CoverageCapability.INVENTORY,
        CoverageOutcome.PARTIAL,
        "file_read_failed",
        "src/unreadable.py",
        0,
        1,
    )
    context = replace(
        _context(tmp_path),
        inventory_files=(InventoryFile("src/unreadable.py", "b" * 64, "python", True),),
        file_accessor=lambda _path: (_ for _ in ()).throw(OSError("denied")),
    )

    artifact = build_canonical_graph(
        context,
        (_empty_result(event),),
        generated_at=lambda: "2026-07-19T12:00:00Z",
    )
    file_node = next(node for node in artifact.nodes if node.kind.value == "file")

    assert file_node.qualified_name == "src/unreadable.py"
    assert file_node.properties.analysis_status.value == "failed"
    assert artifact.graph_contract.coverage.files.discovered == 1
    assert artifact.graph_contract.coverage.files.failed == 1


def test_polyglot(tmp_path, monkeypatch):
    python = _complex_result()
    typescript = _empty_result(
        CoverageEvent(
            "typescript",
            CoverageCapability.CONTROL_FLOW,
            CoverageOutcome.UNSUPPORTED,
            "parser_unavailable",
            None,
            0,
            1,
        )
    )
    context = replace(_context(tmp_path), detected_languages=("python", "typescript"))

    first = build_canonical_graph(
        context,
        (python, typescript),
        generated_at=lambda: "2026-07-19T12:00:00Z",
    )
    second = build_canonical_graph(
        context,
        (typescript, python),
        generated_at=lambda: "2026-07-19T12:00:00Z",
    )

    assert artifact_to_payload(first) == artifact_to_payload(second)
    assert tuple(item.name for item in first.languages) == ("python", "typescript")
    assert tuple(
        item.language for item in first.graph_contract.completeness.languages
    ) == ("python", "typescript")

    from hermes_cli.hades_backend_jobs import execute_job
    from tests.hermes_cli.test_hades_backend_jobs import _materialize_graph_v2

    workspace = tmp_path / "all-adapters"
    workspace.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hades-home"))
    (workspace / "composer.json").write_text(
        '{"require":{"laravel/framework":"11.0","symfony/framework-bundle":"7.0"}}',
        encoding="utf-8",
    )
    (workspace / "requirements.txt").write_text(
        "django==5.1\nfastapi==0.115\n", encoding="utf-8"
    )
    (workspace / "package.json").write_text(
        '{"dependencies":{"express":"5.2.1","next":"15.0.0"}}',
        encoding="utf-8",
    )
    (workspace / "app.js").write_text(
        "const express = require('express');\n"
        "const app = express();\n"
        "function health(req, res) { res.json({ok:true}); }\n"
        "app.get('/express-health', health);\n",
        encoding="utf-8",
    )
    next_route = workspace / "app" / "api" / "items" / "route.ts"
    next_route.parent.mkdir(parents=True)
    next_route.write_text(
        "export async function GET() { return Response.json([]); }\n",
        encoding="utf-8",
    )
    (workspace / "app.py").write_text(
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n"
        "@app.get('/fastapi-health')\n"
        "def fastapi_health(): return {'ok': True}\n"
        "def main(): return 0\n",
        encoding="utf-8",
    )
    project = workspace / "project"
    project.mkdir()
    (project / "settings.py").write_text(
        'ROOT_URLCONF = "project.urls"\nMIDDLEWARE = []\n', encoding="utf-8"
    )
    (project / "urls.py").write_text(
        "from django.urls import path\n"
        "from . import views\n"
        "urlpatterns = [path('django-health/', views.health, name='health')]\n",
        encoding="utf-8",
    )
    (project / "views.py").write_text(
        "def health(request): return None\n", encoding="utf-8"
    )
    laravel = workspace / "routes" / "web.php"
    laravel.parent.mkdir()
    laravel.write_text(
        "<?php\nRoute::get('/laravel-health', [HealthController::class, 'show']);\n",
        encoding="utf-8",
    )
    controller = workspace / "app" / "Http" / "Controllers" / "HealthController.php"
    controller.parent.mkdir(parents=True)
    controller.write_text(
        "<?php\nclass HealthController { public function show() { return 'ok'; } }\n",
        encoding="utf-8",
    )
    symfony = workspace / "src" / "Controller" / "StatusController.php"
    symfony.parent.mkdir(parents=True)
    symfony.write_text(
        "<?php\nuse Symfony\\Component\\Routing\\Attribute\\Route;\n"
        "class StatusController { #[Route('/symfony-health', name: 'status')] "
        "public function status() { return 'ok'; } }\n",
        encoding="utf-8",
    )

    result = execute_job(
        {
            "job_id": "job_all_adapters",
            "capability": "populate_backend_ast",
            "payload": {
                "project_id": "01KXJD0SV73EBGWKNE2EK3M4KD",
                "workspace_binding_id": "01KXJD1BDMQ2TFABMVJV6EFE8Q",
                "max_files": 100,
                "max_symbols": 500,
                "max_edges": 1_000,
            },
        },
        workspace_root=workspace,
    )
    all_adapters = _materialize_graph_v2(result)
    assert {
        (item["language"], item["name"]) for item in all_adapters["frameworks"]
    } == {
        ("python", "django"),
        ("python", "fastapi"),
        ("php", "laravel"),
        ("php", "symfony"),
        ("javascript", "express"),
        ("typescript", "express"),
        ("javascript", "nextjs"),
        ("typescript", "nextjs"),
    }
    assert {item["language"] for item in all_adapters["nodes"]} >= {
        "javascript",
        "php",
        "python",
        "typescript",
    }
    assert {item.get("framework") for item in all_adapters["entrypoints"]} >= {
        "django",
        "express",
        "fastapi",
        "laravel",
        "nextjs",
        "symfony",
    }
    test_v2_aggregation_rejects_cross_adapter_semantic_collisions()


def test_empty_file_and_no_entrypoint_coverage_remain_distinct(tmp_path):
    artifact = build_canonical_graph(
        _context(tmp_path),
        (_valid_result(),),
        generated_at=lambda: "2026-07-19T12:00:00Z",
    )
    file_node = next(node for node in artifact.nodes if node.kind.value == "file")

    assert file_node.properties.byte_size == 0
    assert file_node.properties.analysis_status.value == "analyzed"

    no_entrypoint = build_canonical_graph(
        _context(tmp_path),
        (_empty_result(),),
        generated_at=lambda: "2026-07-19T12:00:00Z",
    )
    assert no_entrypoint.entrypoints == ()
    assert no_entrypoint.flows == ()
    assert no_entrypoint.graph_contract.coverage.entrypoints.detected == 0
    assert no_entrypoint.graph_contract.completeness.status.value == "partial"


def test_real_fastapi_pipeline_builds_ordered_framework_lifecycle(tmp_path):
    from hermes_cli.hades_index.lifecycle.frameworks.fastapi import (
        FastAPILifecycleAdapter,
    )
    from tests.hermes_cli.test_hades_lifecycle_fastapi import (
        _candidate,
        _context as fastapi_context,
        _prepare,
        _write,
    )

    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from fastapi import FastAPI
app = FastAPI()
@app.get('/items')
async def items():
    return {'ok': True}
""",
    )
    adapter = FastAPILifecycleAdapter()
    adapter_context = fastapi_context(tmp_path)
    candidate = _candidate(adapter, adapter_context, "items")
    actual_segments = adapter.pipeline(adapter_context, candidate)
    pipeline_facts = adapter.pipeline_facts(adapter_context, candidate)
    base = _valid_result()
    declaration = base.declarations[0]
    transformed = tuple(
        replace(
            segment,
            target=(
                FrameworkLocalTarget(declaration.local_key)
                if isinstance(segment.target, FrameworkLocalTarget)
                else segment.target
            ),
            short_circuit_successors=tuple(
                replace(successor, target_local_key=declaration.local_key)
                if isinstance(successor, AsyncSuccessor)
                else successor
                for successor in segment.short_circuit_successors
            ),
        )
        for segment in actual_segments
    )
    result = replace(
        base,
        branch_arms=tuple(
            sorted(
                (*base.branch_arms, *pipeline_facts.branch_arms),
                key=lambda item: (item.branch_local_key, item.arm_ordinal),
            )
        ),
        edge_facts=(),
        exception_scopes=tuple(
            sorted(
                (
                    *base.exception_scopes,
                    *(
                        replace(item, declaration_key=declaration.local_key)
                        for item in pipeline_facts.exception_scopes
                    ),
                ),
                key=lambda item: item.local_key,
            )
        ),
        framework_segments=tuple(sorted(transformed, key=lambda item: item.local_key)),
        entrypoints=(
            replace(
                candidate,
                handler_local_key=declaration.local_key,
                unresolved_fact_local_key=None,
            ),
        ),
        structures=tuple(
            sorted(
                (
                    *base.structures,
                    *(
                        replace(item, owner_declaration_key=declaration.local_key)
                        for item in pipeline_facts.structures
                    ),
                ),
                key=lambda item: item.local_key,
            )
        ),
        terminals=tuple(
            sorted(
                (*base.terminals, *pipeline_facts.terminals),
                key=lambda item: item.local_key,
            )
        ),
        unresolved_facts=(),
    )
    result.validate()
    context = replace(
        _context(tmp_path),
        detected_frameworks=adapter_context.detected_frameworks,
        python_metadata=adapter_context.python_metadata,
        inventory_files=(
            InventoryFile(
                "app.py",
                hashlib.sha256((tmp_path / "app.py").read_bytes()).hexdigest(),
                "python",
                True,
            ),
            InventoryFile(
                "pyproject.toml",
                hashlib.sha256((tmp_path / "pyproject.toml").read_bytes()).hexdigest(),
                None,
                False,
            ),
            InventoryFile("src/app.py", "a" * 64, "python", True),
        ),
        file_accessor=lambda path: (
            (tmp_path / path).read_bytes() if (tmp_path / path).exists() else b""
        ),
    )

    artifact = build_canonical_graph(
        context,
        (result,),
        generated_at=lambda: "2026-07-19T12:00:00Z",
    )
    framework_nodes = [
        node for node in artifact.nodes if hasattr(node.properties, "pipeline_order")
    ]

    assert len(actual_segments) >= 3
    assert sorted(node.properties.pipeline_order for node in framework_nodes) == list(
        range(len(actual_segments))
    )
    validate_artifact(artifact)


def _framework_e2e_case(tmp_path, framework: str):
    if framework == "fastapi":
        from hermes_cli.hades_index.lifecycle.frameworks.fastapi import (
            FastAPILifecycleAdapter,
        )
        from hermes_cli.hades_index.python import extract_lifecycle_entrypoints
        from tests.hermes_cli import test_hades_lifecycle_fastapi as fixture

        fixture._prepare(tmp_path)
        fixture._write(
            tmp_path,
            "app.py",
            "from fastapi import FastAPI\n"
            "app = FastAPI()\n"
            "@app.get('/items')\n"
            "async def items(): return {'ok': True}\n",
        )
        context = fixture._context(tmp_path)
        syntax = (fixture._syntax(tmp_path, "app.py"),)
        adapter = FastAPILifecycleAdapter()
    elif framework == "django":
        from hermes_cli.hades_index.lifecycle.frameworks.django import (
            DjangoLifecycleAdapter,
        )
        from hermes_cli.hades_index.python import extract_lifecycle_entrypoints
        from tests.hermes_cli import test_hades_lifecycle_django as fixture

        fixture._prepare_project(tmp_path)
        fixture._write(
            tmp_path,
            "project/urls.py",
            "from . import views\n"
            "urlpatterns = [path('items/', views.items, name='items')]\n",
        )
        fixture._write(
            tmp_path,
            "project/views.py",
            "def items(request): return HttpResponse('ok')\n",
        )
        context = fixture._context(tmp_path)
        syntax = (
            fixture._syntax(
                tmp_path, "project/views.py", fixture._function("items", 1)
            ),
        )
        adapter = DjangoLifecycleAdapter()
    elif framework == "laravel":
        from hermes_cli.hades_index.lifecycle.frameworks.laravel import (
            LaravelLifecycleAdapter,
        )
        from hermes_cli.hades_index.php import extract_lifecycle_entrypoints
        from tests.hermes_cli import test_hades_lifecycle_laravel as fixture

        fixture._prepare_project(tmp_path)
        fixture._write(
            tmp_path,
            "routes/web.php",
            "<?php Route::get('/items', [ItemController::class, 'index']);",
        )
        fixture._write(
            tmp_path,
            "app/Http/Controllers/ItemController.php",
            "<?php class ItemController { public function index() {} }",
        )
        context = fixture._context(tmp_path)
        syntax = (
            fixture._syntax(
                tmp_path,
                "app/Http/Controllers/ItemController.php",
                "ItemController.index",
            ),
        )
        adapter = LaravelLifecycleAdapter()
    elif framework == "symfony":
        from hermes_cli.hades_index.lifecycle.frameworks.symfony import (
            SymfonyLifecycleAdapter,
        )
        from hermes_cli.hades_index.php import extract_lifecycle_entrypoints
        from tests.hermes_cli import test_hades_lifecycle_symfony as fixture

        fixture._prepare_project(tmp_path)
        fixture._write(
            tmp_path,
            "config/routes.yaml",
            "items:\n"
            "  path: /items\n"
            "  controller: App\\Controller\\ItemController::index\n"
            "  methods: [GET]\n",
        )
        fixture._write(
            tmp_path,
            "src/Controller/ItemController.php",
            "<?php namespace App\\Controller; final class ItemController { "
            "public function index() {} }",
        )
        context = fixture._context(tmp_path)
        syntax = (
            fixture._syntax(
                tmp_path,
                "src/Controller/ItemController.php",
                "ItemController.index",
            ),
        )
        adapter = SymfonyLifecycleAdapter()
    else:
        from hermes_cli.hades_index.lifecycle.frameworks.nextjs import (
            NextJSLifecycleAdapter,
        )
        from hermes_cli.hades_index.typescript import extract_lifecycle_entrypoints
        from tests.hermes_cli import test_hades_lifecycle_nextjs as fixture

        fixture._write(
            tmp_path,
            "app/api/items/route.ts",
            "export async function GET() { return Response.json([]) }\n",
        )
        context = fixture._context(tmp_path, "typescript")
        syntax = (fixture._syntax(tmp_path, "app/api/items/route.ts", "typescript"),)
        adapter = NextJSLifecycleAdapter("typescript")

    registry = FrameworkAdapterRegistry()
    registry.register(adapter)
    extraction = extract_lifecycle_entrypoints(context, syntax, registry=registry)
    canonical_context = _context(tmp_path)
    context = replace(
        context,
        project_id=canonical_context.project_id,
        workspace_binding_id=canonical_context.workspace_binding_id,
        source_identity=canonical_context.source_identity,
    )
    return context, extraction


def _companion_control_flow(context, extraction: EntrypointExtraction) -> AdapterResult:
    segment_keys = {item.local_key for item in extraction.framework_segments}
    exception_declaration_keys = {
        segment.target.local_key
        for segment in extraction.framework_segments
        if isinstance(segment.target, FrameworkLocalTarget)
        and any(
            isinstance(successor, ExceptionSuccessor)
            for successor in (
                segment.success_successor,
                *segment.short_circuit_successors,
            )
        )
    }
    references = {}
    for candidate in extraction.candidates:
        if candidate.handler_local_key is not None:
            references.setdefault(
                candidate.handler_local_key, candidate.registration_locator
            )
    for segment in extraction.framework_segments:
        if isinstance(segment.target, FrameworkLocalTarget):
            references.setdefault(segment.target.local_key, segment.evidence.locator)
        for successor in (
            segment.success_successor,
            *segment.short_circuit_successors,
        ):
            if isinstance(successor, AsyncSuccessor):
                references.setdefault(
                    successor.target_local_key, segment.evidence.locator
                )
    for structure in extraction.structures:
        references.setdefault(
            structure.owner_declaration_key, structure.evidence.locator
        )
    for scope in extraction.exception_scopes:
        references.setdefault(scope.declaration_key, scope.locator)

    declarations = []
    blocks = []
    block_keys: set[str] = set()
    for ordinal, (declaration_key, reference) in enumerate(sorted(references.items())):
        locator = AstLocatorIR(
            reference.source_location,
            f"e2e/callable/{ordinal}",
            0,
        )
        entry_key = local_record_key(
            context.detected_languages[0],
            locator.source_location.path,
            "basic_block",
            "ast",
            f"{locator.structural_path}/entry",
            0,
        )
        exit_key = local_record_key(
            context.detected_languages[0],
            locator.source_location.path,
            "basic_block",
            "ast",
            f"{locator.structural_path}/exit",
            0,
        )
        exception_exit_key = local_record_key(
            context.detected_languages[0],
            locator.source_location.path,
            "basic_block",
            "ast",
            f"{locator.structural_path}/exception_exit",
            0,
        )
        declarations.append(
            ExecutableDeclaration(
                declaration_key,
                context.detected_languages[0],
                NodeKind.FUNCTION,
                DeclarationIdentityKind.NAMED,
                None,
                f"callable_{ordinal}",
                f"e2e.callable_{ordinal}",
                "e2e",
                (Modifier.PUBLIC,),
                (),
                None,
                locator,
                entry_key,
                (exit_key,),
                (
                    (exception_exit_key,)
                    if declaration_key in exception_declaration_keys
                    else ()
                ),
            )
        )
        blocks.extend((
            BasicBlock(
                entry_key,
                declaration_key,
                ControlKind.ENTRY,
                0,
                replace(locator, structural_path=f"{locator.structural_path}/entry"),
                (AlwaysSuccessor(exit_key, 0),),
            ),
            BasicBlock(
                exit_key,
                declaration_key,
                ControlKind.RETURN,
                1,
                replace(locator, structural_path=f"{locator.structural_path}/exit"),
                (),
            ),
        ))
        block_keys.update((entry_key, exit_key))
        if declaration_key in exception_declaration_keys:
            blocks.append(
                BasicBlock(
                    exception_exit_key,
                    declaration_key,
                    ControlKind.THROW,
                    2,
                    replace(
                        locator,
                        structural_path=(f"{locator.structural_path}/exception_exit"),
                    ),
                    (),
                )
            )
            block_keys.add(exception_exit_key)

    handler_by_segment = {
        key: candidate.handler_local_key
        for candidate in extraction.candidates
        for key in candidate.framework_segment_keys
        if candidate.handler_local_key is not None
    }
    required_blocks: dict[str, tuple[str, object]] = {}
    declaration_keys = set(references)
    for segment in extraction.framework_segments:
        owner_key = handler_by_segment.get(segment.local_key)
        if owner_key is None:
            continue
        for successor in (
            segment.success_successor,
            *segment.short_circuit_successors,
        ):
            target_key = getattr(successor, "target_block_key", None)
            if (
                target_key is not None
                and target_key not in segment_keys | declaration_keys | block_keys
            ):
                required_blocks.setdefault(
                    target_key, (owner_key, segment.evidence.locator)
                )
    for arm in extraction.branch_arms:
        structure = next(
            item
            for item in extraction.structures
            if item.local_key == arm.branch_local_key
        )
        for key in (arm.source_block_key, arm.target_block_key):
            if key not in segment_keys | declaration_keys | block_keys:
                required_blocks.setdefault(
                    key, (structure.owner_declaration_key, structure.evidence.locator)
                )
    for ordinal, (key, (owner_key, reference)) in enumerate(
        sorted(required_blocks.items())
    ):
        locator = AstLocatorIR(
            reference.source_location,
            f"e2e/framework_block/{ordinal}",
            0,
        )
        blocks.append(
            BasicBlock(
                key,
                owner_key,
                ControlKind.STRAIGHT_LINE,
                ordinal + 2,
                locator,
                (),
            )
        )

    return AdapterResult(
        declarations=tuple(sorted(declarations, key=lambda item: item.local_key)),
        blocks=tuple(sorted(blocks, key=lambda item: item.local_key)),
        branch_arms=(),
        structures=(),
        call_sites=(),
        edge_facts=(),
        exception_scopes=(),
        terminals=(),
        effects=(),
        framework_segments=(),
        entrypoints=(),
        unresolved_facts=(),
        coverage_events=(),
        diagnostics=(),
    )


@pytest.mark.parametrize(
    "framework", ("fastapi", "django", "laravel", "symfony", "nextjs")
)
def test_untouched_framework_extraction_aggregates_and_executes_callable_cfg(
    tmp_path, framework
):
    context, extraction = _framework_e2e_case(tmp_path, framework)
    frozen_output = repr(extraction)
    assembled = aggregate_entrypoint_extraction(
        _companion_control_flow(context, extraction), extraction
    )
    aggregate_adapter_results((assembled,))
    artifact = build_canonical_graph(
        context,
        (assembled,),
        generated_at=lambda: "2026-07-19T12:00:00Z",
    )

    assert repr(extraction) == frozen_output
    assert artifact.entrypoints
    nodes = {item.id: item for item in artifact.nodes}
    edges = artifact.edges
    for entrypoint in artifact.entrypoints:
        if entrypoint.handler_node_id is None:
            continue
        cfg_ids = {
            item.id
            for item in artifact.nodes
            if getattr(item.identity, "owner_node_id", None)
            == entrypoint.handler_node_id
            and item.kind in {NodeKind.BASIC_BLOCK, NodeKind.MERGE}
        }
        assert cfg_ids
        local_orders = {
            segment.pipeline_order
            for segment in extraction.framework_segments
            if isinstance(segment.target, FrameworkLocalTarget)
            and segment.target.local_key
            == next(
                candidate.handler_local_key
                for candidate in extraction.candidates
                if candidate.public_name == entrypoint.public_name
            )
        }
        for order in local_orders:
            continuations = [
                edge
                for edge in edges
                if (
                    getattr(edge.occurrence, "ast_path", None)
                    or getattr(edge.occurrence, "structural_pointer", "")
                ).startswith(f"pipeline/{order}/")
                and not (
                    getattr(edge.occurrence, "ast_path", None)
                    or getattr(edge.occurrence, "structural_pointer", "")
                ).endswith("/target")
            ]
            assert continuations
            assert all(edge.source_id in cfg_ids for edge in continuations)
    assert all(edge.source_id in nodes for edge in edges)
    validate_artifact(artifact)


def test_django_two_routes_sharing_exception_handler_reach_final_graph(tmp_path):
    from hermes_cli.hades_index.lifecycle.frameworks.django import (
        DjangoLifecycleAdapter,
    )
    from hermes_cli.hades_index.python import extract_lifecycle_entrypoints
    from tests.hermes_cli import test_hades_lifecycle_django as fixture

    fixture._prepare_project(tmp_path)
    fixture._write(
        tmp_path,
        "project/urls.py",
        "from . import views\n"
        "urlpatterns = [\n"
        "    path('a/', views.items, name='a'),\n"
        "    path('b/', views.items, name='b'),\n"
        "]\n",
    )
    fixture._write(
        tmp_path,
        "project/views.py",
        "def items(request):\n"
        "    try:\n"
        "        raise RuntimeError()\n"
        "    except RuntimeError:\n"
        "        return HttpResponse('error')\n",
    )
    context = fixture._context(tmp_path)
    syntax = (
        fixture._syntax(
            tmp_path,
            "project/views.py",
            fixture._function("items", 1),
        ),
    )
    registry = FrameworkAdapterRegistry()
    registry.register(DjangoLifecycleAdapter())

    extraction = extract_lifecycle_entrypoints(context, syntax, registry=registry)
    canonical_context = _context(tmp_path)
    context = replace(
        context,
        project_id=canonical_context.project_id,
        workspace_binding_id=canonical_context.workspace_binding_id,
        source_identity=canonical_context.source_identity,
    )
    assembled = aggregate_entrypoint_extraction(
        _companion_control_flow(context, extraction), extraction
    )
    artifact = build_canonical_graph(
        context,
        (assembled,),
        generated_at=lambda: "2026-07-19T12:00:00Z",
    )

    assert len(extraction.candidates) == 2
    assert len(artifact.entrypoints) == 2
    assert all(
        any(
            edge.relation.value == "throws_to" and edge.exception_scope_id is not None
            for edge in artifact.edges
            if (
                getattr(edge.occurrence, "ast_path", None)
                or getattr(edge.occurrence, "structural_pointer", "")
            ).startswith(f"pipeline/{segment.pipeline_order}/")
        )
        for segment in extraction.framework_segments
        if any(
            isinstance(successor, ExceptionSuccessor)
            for successor in (
                segment.success_successor,
                *segment.short_circuit_successors,
            )
        )
    )
    validate_artifact(artifact)
