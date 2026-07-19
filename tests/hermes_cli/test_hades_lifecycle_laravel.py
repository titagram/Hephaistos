"""Golden behaviour for the closed Laravel lifecycle adapter.

These fixtures intentionally use only files visible through ``ExtractionContext``.
They exercise static Laravel registration facts, not a booted application.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable

from tests.hermes_cli.test_hades_lifecycle_framework_adapter import (
    _assert_framework_pipeline_closes_adapter_result,
)

from hermes_cli.hades_graph_config import load_hades_graph_index_config
from hermes_cli.hades_graph_v2.model import (
    EntrypointKind,
    FrameworkKnowledge,
    FrameworkRecord,
    MethodSemantics,
    SourceIdentity,
)
from hermes_cli.hades_index.lifecycle.entrypoints import normalized_entrypoint_identity
from hermes_cli.hades_index.lifecycle.frameworks.laravel import LaravelLifecycleAdapter
from hermes_cli.hades_index.lifecycle.model import (
    AlwaysSuccessor,
    AsyncSuccessor,
    ConfigLocatorIR,
    CoverageOutcome,
    ExceptionSuccessor,
    ExtractionContext,
    InventoryFile,
    ReturnSuccessor,
    SourceLocationIR,
)
from hermes_cli.hades_index.tree_sitter_adapter import (
    ParsedFile,
    StructuralSymbol,
    SyntaxIR,
)


def _write(root: Path, path: str, content: str) -> None:
    destination = root / path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8")


def _location(root: Path, path: str) -> SourceLocationIR:
    content = (root / path).read_bytes()
    return SourceLocationIR(
        path, 1, max(1, content.count(b"\n") + 1), hashlib.sha256(content).hexdigest()
    )


def _prepare_project(root: Path) -> None:
    _write(
        root,
        "composer.json",
        '{"require":{"laravel/framework":"^11.0"}}',
    )
    _write(
        root,
        "composer.lock",
        '{"packages":[{"name":"laravel/framework","version":"v11.22.0"}]}',
    )


def _context(
    root: Path, *, file_accessor: Callable[[Path], bytes] | None = None
) -> ExtractionContext:
    composer = _location(root, "composer.json")
    return ExtractionContext(
        workspace_root=root,
        project_id="project",
        workspace_binding_id="binding",
        source_identity=SourceIdentity(None, "a" * 64, False, None),
        graph_config=load_hades_graph_index_config({}),
        detected_languages=("php",),
        detected_frameworks=(
            FrameworkRecord(
                language="php",
                name="laravel",
                version="11.22.0",
                detector="composer_lock",
                configuration_paths=("composer.json",),
                knowledge=FrameworkKnowledge.VERIFIED,
            ),
        ),
        composer_metadata=(ConfigLocatorIR(composer, "composer", 0),),
        python_metadata=(),
        package_metadata=(),
        tsconfig_metadata=(),
        file_accessor=file_accessor or (lambda path: (root / path).read_bytes()),
        inventory_files=tuple(
            InventoryFile(
                path.relative_to(root).as_posix(),
                hashlib.sha256(path.read_bytes()).hexdigest(),
                None,
                True,
            )
            for path in sorted(root.rglob("*"))
            if path.is_file()
        ),
        excluded_path_count=0,
    )


def _syntax(root: Path, path: str, *symbols: str) -> SyntaxIR:
    return SyntaxIR(
        ParsedFile(
            path=path,
            language="php",
            symbols=tuple(
                StructuralSymbol(name, "method", index + 1, index + 1)
                for index, name in enumerate(symbols)
            ),
            imports=(),
            calls=(),
        ),
        (),
    )


def test_detects_laravel_and_preserves_nested_group_registration_order(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "routes/web.php",
        """<?php
Route::middleware(['web', 'auth'])->prefix('api')->name('api.')->domain('api.test')->group(function () {
    Route::prefix('v1')->group(function () {
        Route::get('/users/{user}', [UserController::class, 'show'])->name('users.show');
        Route::post('/users', [UserController::class, 'store'])->middleware('throttle:api');
    });
});
""",
    )
    adapter = LaravelLifecycleAdapter()
    context = _context(tmp_path)
    assert adapter.detect(context).detected is True
    assert adapter.detected_version(context) == "11.22.0"

    routes = adapter.entrypoints(
        context,
        (
            _syntax(
                tmp_path,
                "app/Http/Controllers/UserController.php",
                "UserController.show",
                "UserController.store",
            ),
        ),
    )

    assert [(route.public_path, route.public_name) for route in routes] == [
        ("/api/v1/users/{user}", "api.users.show"),
        ("/api/v1/users", "api."),
    ]
    assert routes[0].methods == ("GET",)
    assert routes[0].match_constraints.host == "api.test"
    assert all(route.handler_local_key is not None for route in routes)
    pipeline = adapter.pipeline(context, routes[0])
    _assert_framework_pipeline_closes_adapter_result(
        (routes[0],), pipeline, adapter.pipeline_facts(context, routes[0])
    )


def test_resource_routes_expand_to_explicit_methods_in_declaration_order(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "routes/api.php",
        "<?php Route::resource('users', UserController::class)->middleware('api');",
    )
    routes = LaravelLifecycleAdapter().entrypoints(
        _context(tmp_path),
        (
            _syntax(
                tmp_path,
                "app/Http/Controllers/UserController.php",
                *(
                    "UserController.index",
                    "UserController.create",
                    "UserController.store",
                    "UserController.show",
                    "UserController.edit",
                    "UserController.update",
                    "UserController.destroy",
                ),
            ),
        ),
    )

    assert [route.public_name for route in routes] == [
        "users.index",
        "users.create",
        "users.store",
        "users.show",
        "users.edit",
        "users.update",
        "users.destroy",
    ]
    assert [route.methods for route in routes] == [
        ("GET",),
        ("GET",),
        ("POST",),
        ("GET",),
        ("GET",),
        ("PATCH", "PUT"),
        ("DELETE",),
    ]


def test_resource_paths_use_laravel_static_singular_parameters(tmp_path: Path) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "routes/web.php",
        "<?php Route::resource('categories', CategoryController::class);",
    )

    routes = LaravelLifecycleAdapter().entrypoints(
        _context(tmp_path),
        (
            _syntax(
                tmp_path,
                "app/Http/Controllers/CategoryController.php",
                "CategoryController.index",
                "CategoryController.create",
                "CategoryController.store",
                "CategoryController.show",
                "CategoryController.edit",
                "CategoryController.update",
                "CategoryController.destroy",
            ),
        ),
    )
    paths = {route.public_name: route.public_path for route in routes}

    assert paths["categories.show"] == "/categories/{category}"
    assert paths["categories.edit"] == "/categories/{category}/edit"
    assert paths["categories.update"] == "/categories/{category}"
    assert paths["categories.destroy"] == "/categories/{category}"


def test_resource_buses_uses_a_proven_static_parameter(tmp_path: Path) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "routes/web.php",
        "<?php Route::resource('buses', BusController::class);",
    )

    routes = LaravelLifecycleAdapter().entrypoints(
        _context(tmp_path),
        (
            _syntax(
                tmp_path,
                "app/Http/Controllers/BusController.php",
                "BusController.index",
                "BusController.create",
                "BusController.store",
                "BusController.show",
                "BusController.edit",
                "BusController.update",
                "BusController.destroy",
            ),
        ),
    )
    paths = {route.public_name: route.public_path for route in routes}

    assert paths["buses.show"] == "/buses/{bus}"
    assert paths["buses.edit"] == "/buses/{bus}/edit"


def test_resource_with_unproven_parameter_inflection_is_partial(tmp_path: Path) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "routes/web.php",
        "<?php Route::resource('status', StatusController::class);",
    )
    adapter = LaravelLifecycleAdapter()
    context = _context(tmp_path)

    assert adapter.entrypoints(context, ()) == ()
    assert "resource_parameter_unresolved" in {
        event.reason_code for event in adapter.coverage_events(context)
    }


def test_resource_with_unsupported_es_inflection_is_partial(tmp_path: Path) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "routes/web.php",
        "<?php Route::resource('quizzes', QuizController::class);",
    )
    adapter = LaravelLifecycleAdapter()
    context = _context(tmp_path)

    assert adapter.entrypoints(context, ()) == ()
    assert "resource_parameter_unresolved" in {
        event.reason_code for event in adapter.coverage_events(context)
    }


def test_route_service_provider_context_is_applied_without_a_default_fallback(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "app/Providers/RouteServiceProvider.php",
        "<?php class RouteServiceProvider { public function boot() { Route::middleware('api')->prefix('v1')->name('v1.')->group(base_path('routes/api.php')); } }",
    )
    _write(
        tmp_path,
        "routes/api.php",
        "<?php Route::get('/users', [UserController::class, 'index'])->name('users');",
    )

    routes = LaravelLifecycleAdapter().entrypoints(
        _context(tmp_path),
        (
            _syntax(
                tmp_path,
                "app/Http/Controllers/UserController.php",
                "UserController.index",
            ),
        ),
    )

    assert [(route.public_path, route.public_name) for route in routes] == [
        ("/v1/users", "v1.users"),
    ]


def test_pipeline_expands_global_group_alias_route_and_controller_middleware(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "app/Http/Kernel.php",
        """<?php
class Kernel {
 protected $middleware = [TrustProxies::class, TrimStrings::class];
 protected $middlewareGroups = ['web' => [EncryptCookies::class, 'auth']];
 protected $middlewareAliases = ['auth' => Authenticate::class, 'can' => Authorize::class];
 protected $middlewarePriority = [Authenticate::class, Authorize::class];
}
""",
    )
    _write(
        tmp_path,
        "routes/web.php",
        "<?php Route::get('/admin/{user}', [AdminController::class, 'show'])->middleware(['web', 'can:view,user']);",
    )
    _write(
        tmp_path,
        "app/Http/Controllers/AdminController.php",
        "<?php class AdminController { public function __construct() { $this->middleware('auth'); } public function show(ShowUserRequest $request, User $user) { return response()->json([]); } }",
    )
    adapter = LaravelLifecycleAdapter()
    route = adapter.entrypoints(
        _context(tmp_path),
        (
            _syntax(
                tmp_path,
                "app/Http/Controllers/AdminController.php",
                "AdminController.show",
            ),
        ),
    )[0]
    pipeline = adapter.pipeline(_context(tmp_path), route)
    roles = [segment.framework_role for segment in pipeline]

    assert roles[:4] == ["router", "middleware", "middleware", "middleware"]
    assert "route_binding" in roles
    assert "authentication" in roles
    assert "authorization" in roles
    assert "validation" in roles
    assert "handler" in roles
    assert "response" in roles
    assert "response_outcome" in roles
    assert roles.index("response") < roles.index("response_outcome")
    # Two global entries plus the expanded group/aliases remain visible; the
    # duplicate ``auth`` registration is collapsed after alias expansion.
    assert len([role for role in roles if role == "middleware"]) == 5


def test_binding_validation_policy_redirect_abort_and_exception_are_explicit_arms(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "routes/web.php",
        "<?php Route::post('/orders/{order}', [OrderController::class, 'store'])->middleware('auth');",
    )
    _write(
        tmp_path,
        "app/Http/Controllers/OrderController.php",
        """<?php class OrderController {
 public function store(StoreOrderRequest $request, Order $order) {
   $this->authorize('update', $order); if ($bad) { abort(403); }
   if ($other) { return redirect('/orders'); } throw new RuntimeException();
 }
}""",
    )
    _write(
        tmp_path,
        "app/Exceptions/Handler.php",
        "<?php class Handler { public function render($request, Throwable $exception) {} }",
    )
    adapter = LaravelLifecycleAdapter()
    route = adapter.entrypoints(
        _context(tmp_path),
        (
            _syntax(
                tmp_path,
                "app/Http/Controllers/OrderController.php",
                "OrderController.store",
            ),
        ),
    )[0]
    pipeline = adapter.pipeline(_context(tmp_path), route)
    by_role = {segment.framework_role: segment for segment in pipeline}

    assert by_role["route_binding"].short_circuit_successors
    assert by_role["validation"].short_circuit_successors
    assert by_role["authorization"].short_circuit_successors
    assert by_role["handler"].short_circuit_successors
    assert "exception_renderer" in by_role


def test_handler_outcomes_join_response_and_terminating_middleware(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "app/Http/Kernel.php",
        "<?php class Kernel { protected $middleware = [Terminable::class]; }",
    )
    _write(
        tmp_path,
        "app/Http/Middleware/Terminable.php",
        "<?php class Terminable { public function terminate($request, $response) {} }",
    )
    _write(
        tmp_path,
        "routes/web.php",
        """<?php
Route::get('/response', [OutcomeController::class, 'response']);
Route::get('/redirect', [OutcomeController::class, 'redirect']);
Route::get('/abort', [OutcomeController::class, 'abort']);
Route::get('/throws', [OutcomeController::class, 'throws']);
""",
    )
    _write(
        tmp_path,
        "app/Http/Controllers/OutcomeController.php",
        """<?php class OutcomeController {
 public function response() { return response()->noContent(); }
 public function redirect() { return redirect('/next'); }
 public function abort() { abort(403); }
 public function throws() { throw new RuntimeException(); }
}""",
    )
    _write(
        tmp_path,
        "app/Exceptions/Handler.php",
        "<?php class Handler { public function render($request, Throwable $exception) {} }",
    )
    adapter = LaravelLifecycleAdapter()
    routes = adapter.entrypoints(
        _context(tmp_path),
        (
            _syntax(
                tmp_path,
                "app/Http/Controllers/OutcomeController.php",
                "OutcomeController.response",
                "OutcomeController.redirect",
                "OutcomeController.abort",
                "OutcomeController.throws",
            ),
        ),
    )

    expected_outcome_roles = {
        "/response": "response_outcome",
        "/redirect": "redirect_outcome",
        "/abort": "abort_outcome",
    }
    for route in routes:
        pipeline = adapter.pipeline(_context(tmp_path), route)
        by_role = {segment.framework_role: segment for segment in pipeline}
        handler = by_role["handler"]
        response = by_role["response"]
        terminating = by_role["terminating_middleware"]
        assert not any(
            isinstance(successor, ReturnSuccessor)
            for successor in handler.short_circuit_successors
        )
        assert isinstance(response.success_successor, AlwaysSuccessor)
        assert response.success_successor.target_block_key == terminating.local_key
        if route.public_path in expected_outcome_roles:
            outcome = by_role[expected_outcome_roles[route.public_path]]
            assert isinstance(outcome.success_successor, AlwaysSuccessor)
            assert outcome.success_successor.target_block_key == response.local_key
            assert any(
                isinstance(successor, AlwaysSuccessor)
                and successor.target_block_key == outcome.local_key
                for successor in handler.short_circuit_successors
            )
        if "exception_renderer" in by_role:
            renderer = by_role["exception_renderer"]
            assert isinstance(renderer.success_successor, AlwaysSuccessor)
            assert renderer.success_successor.target_block_key == response.local_key
            assert any(
                isinstance(successor, ExceptionSuccessor)
                for successor in (handler.success_successor,)
                + handler.short_circuit_successors
            )


def test_throw_without_renderer_is_an_explicit_partial_exception_boundary(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "routes/web.php",
        "<?php Route::get('/throws', [ThrowingController::class, 'show']);",
    )
    _write(
        tmp_path,
        "app/Http/Controllers/ThrowingController.php",
        "<?php class ThrowingController { public function show() { throw new RuntimeException(); } }",
    )
    adapter = LaravelLifecycleAdapter()
    context = _context(tmp_path)
    route = adapter.entrypoints(
        context,
        (
            _syntax(
                tmp_path,
                "app/Http/Controllers/ThrowingController.php",
                "ThrowingController.show",
            ),
        ),
    )[0]
    by_role = {
        segment.framework_role: segment for segment in adapter.pipeline(context, route)
    }
    handler = by_role["handler"]
    unresolved = by_role["unresolved_exception"]

    assert "exception_renderer" not in by_role
    assert isinstance(handler.success_successor, ExceptionSuccessor)
    assert handler.success_successor.target_block_key == unresolved.local_key
    assert not any(
        isinstance(successor, AlwaysSuccessor)
        and successor.target_block_key == by_role["response"].local_key
        for successor in (handler.success_successor,) + handler.short_circuit_successors
    )
    assert isinstance(unresolved.success_successor, ReturnSuccessor)
    assert any(
        event.outcome is CoverageOutcome.PARTIAL
        and event.reason_code == "exception_renderer_unresolved"
        for event in adapter.coverage_events(context)
    )


def test_conditional_throw_routes_normal_completion_through_partial_boundary(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "routes/web.php",
        "<?php Route::get('/maybe', [MaybeController::class, 'show']);",
    )
    _write(
        tmp_path,
        "app/Http/Controllers/MaybeController.php",
        "<?php class MaybeController { public function show() { if ($bad) { throw new RuntimeException(); } } }",
    )
    adapter = LaravelLifecycleAdapter()
    context = _context(tmp_path)
    route = adapter.entrypoints(
        context,
        (
            _syntax(
                tmp_path,
                "app/Http/Controllers/MaybeController.php",
                "MaybeController.show",
            ),
        ),
    )[0]
    by_role = {
        segment.framework_role: segment for segment in adapter.pipeline(context, route)
    }
    handler = by_role["handler"]
    normal = by_role["normal_completion_unresolved_boundary"]

    assert isinstance(handler.success_successor, AlwaysSuccessor)
    assert handler.success_successor.target_block_key == normal.local_key
    assert isinstance(normal.success_successor, AlwaysSuccessor)
    assert normal.success_successor.target_block_key == by_role["response"].local_key
    assert any(
        isinstance(successor, ExceptionSuccessor)
        for successor in handler.short_circuit_successors
    )
    assert any(
        event.outcome is CoverageOutcome.PARTIAL
        and event.reason_code == "normal_completion_unresolved"
        for event in adapter.coverage_events(context)
    )


def test_linked_async_job_and_event_never_become_inline_handler_continuations(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "routes/web.php",
        "<?php Route::post('/orders', [OrderController::class, 'store']);",
    )
    _write(
        tmp_path,
        "app/Http/Controllers/OrderController.php",
        "<?php class OrderController { public function store() { ShipOrder::dispatch(); event(new OrderPlaced()); return response()->noContent(); } }",
    )
    _write(
        tmp_path,
        "app/Providers/EventServiceProvider.php",
        "<?php class EventServiceProvider { protected $listen = [OrderPlaced::class => [SendReceipt::class, AuditOrder::class]]; }",
    )
    adapter = LaravelLifecycleAdapter()
    route = adapter.entrypoints(
        _context(tmp_path),
        (
            _syntax(
                tmp_path,
                "app/Http/Controllers/OrderController.php",
                "OrderController.store",
            ),
            _syntax(tmp_path, "app/Jobs/ShipOrder.php", "ShipOrder.handle"),
            _syntax(tmp_path, "app/Listeners/SendReceipt.php", "SendReceipt.handle"),
            _syntax(tmp_path, "app/Listeners/AuditOrder.php", "AuditOrder.handle"),
        ),
    )[0]
    pipeline = adapter.pipeline(_context(tmp_path), route)
    async_successors = [
        successor
        for segment in pipeline
        for successor in segment.short_circuit_successors
        if isinstance(successor, AsyncSuccessor)
    ]

    assert [
        segment.framework_role
        for segment in pipeline
        if "dispatch" in segment.framework_role
    ] == ["job_dispatch", "event_dispatch"]
    assert len(async_successors) == 3
    assert all(
        successor.target_local_key != route.handler_local_key
        for successor in async_successors
    )
    assert all(
        segment.success_successor.kind == "always"
        for segment in pipeline
        if "dispatch" in segment.framework_role
    )


def test_console_and_scheduler_entrypoints_are_discovered_without_http_assumption(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "routes/console.php",
        """<?php
Artisan::command('reports:daily', ReportCommand::class);
Schedule::command(ReportCommand::class)->daily();
""",
    )
    entries = LaravelLifecycleAdapter().entrypoints(
        _context(tmp_path),
        (
            _syntax(
                tmp_path,
                "app/Console/Commands/ReportCommand.php",
                "ReportCommand.handle",
            ),
        ),
    )

    assert [(entry.kind, entry.public_name) for entry in entries] == [
        (EntrypointKind.CLI_COMMAND, "reports:daily"),
        (EntrypointKind.SCHEDULED_JOB, "ReportCommand"),
    ]
    assert all(
        entry.method_semantics is MethodSemantics.NOT_APPLICABLE for entry in entries
    )


def test_legacy_console_kernel_scheduler_is_a_distinct_non_http_entrypoint(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "app/Console/Kernel.php",
        "<?php class Kernel { protected function schedule($schedule) { $schedule->command(ReportCommand::class)->daily(); } }",
    )

    entries = LaravelLifecycleAdapter().entrypoints(
        _context(tmp_path),
        (
            _syntax(
                tmp_path,
                "app/Console/Commands/ReportCommand.php",
                "ReportCommand.handle",
            ),
        ),
    )

    assert [(entry.kind, entry.public_name) for entry in entries] == [
        (EntrypointKind.SCHEDULED_JOB, "ReportCommand"),
    ]


def test_handler_facts_do_not_leak_from_an_unrelated_method(tmp_path: Path) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "routes/web.php",
        "<?php Route::get('/safe', [SafeController::class, 'show']);",
    )
    _write(
        tmp_path,
        "app/Http/Controllers/SafeController.php",
        """<?php class SafeController {
 public function show() { return 'safe'; }
 public function dangerous(StoreRequest $request) { abort(403); ShipJob::dispatch(); throw new RuntimeException(); }
}""",
    )
    adapter = LaravelLifecycleAdapter()
    route = adapter.entrypoints(
        _context(tmp_path),
        (
            _syntax(
                tmp_path,
                "app/Http/Controllers/SafeController.php",
                "SafeController.show",
            ),
        ),
    )[0]
    roles = [
        segment.framework_role
        for segment in adapter.pipeline(_context(tmp_path), route)
    ]

    assert "validation" not in roles
    assert "job_dispatch" not in roles
    assert "exception_renderer" not in roles


def test_unproven_fqcn_route_handler_remains_unresolved(tmp_path: Path) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "routes/web.php",
        "<?php Route::get('/x', [Admin\\C::class, 'show']);",
    )
    _write(
        tmp_path,
        "app/Http/Controllers/C.php",
        "<?php class C { public function show() { Ship::dispatch(); } }",
    )
    adapter = LaravelLifecycleAdapter()
    route = adapter.entrypoints(
        _context(tmp_path),
        (
            _syntax(
                tmp_path,
                "app/Http/Controllers/C.php",
                "C.show",
            ),
        ),
    )[0]

    assert route.handler_local_key is None
    assert route.unresolved_fact_local_key is not None
    assert "job_dispatch" not in [
        segment.framework_role
        for segment in adapter.pipeline(_context(tmp_path), route)
    ]


def test_fqcn_route_handler_requires_matching_laravel_psr4_source(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "routes/web.php",
        "<?php Route::get('/x', [App\\Http\\Controllers\\Admin\\C::class, 'show']);",
    )
    _write(
        tmp_path,
        "app/Http/Controllers/Admin/C.php",
        """<?php
namespace App\\Http\\Controllers\\Admin;
class C { public function show() { Ship::dispatch(); } }
""",
    )
    route = LaravelLifecycleAdapter().entrypoints(
        _context(tmp_path),
        (
            _syntax(
                tmp_path,
                "app/Http/Controllers/Admin/C.php",
                "C.show",
            ),
        ),
    )[0]

    assert route.handler_local_key is not None
    assert route.unresolved_fact_local_key is None


def test_event_without_static_listener_mapping_remains_a_boundary_not_async_child(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "routes/web.php",
        "<?php Route::post('/orders', [OrderController::class, 'store']);",
    )
    _write(
        tmp_path,
        "app/Http/Controllers/OrderController.php",
        "<?php class OrderController { public function store() { event(new OrderPlaced()); } }",
    )
    adapter = LaravelLifecycleAdapter()
    route = adapter.entrypoints(
        _context(tmp_path),
        (
            _syntax(
                tmp_path,
                "app/Http/Controllers/OrderController.php",
                "OrderController.store",
            ),
            _syntax(tmp_path, "app/Listeners/SendReceipt.php", "SendReceipt.handle"),
        ),
    )[0]
    event_segment = next(
        segment
        for segment in adapter.pipeline(_context(tmp_path), route)
        if segment.framework_role == "event_dispatch"
    )

    assert not any(
        isinstance(successor, AsyncSuccessor)
        for successor in event_segment.short_circuit_successors
    )


def test_dynamic_configuration_is_partial_never_an_exact_route_or_default_pipeline(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "routes/web.php",
        "<?php Route::prefix($tenant)->group(function () { Route::get('/users', [UserController::class, 'show']); });",
    )
    adapter = LaravelLifecycleAdapter()
    assert adapter.entrypoints(_context(tmp_path), ()) == ()
    assert any(
        event.outcome is CoverageOutcome.PARTIAL
        and event.reason_code == "framework_config_unresolved"
        for event in adapter.coverage_events(_context(tmp_path))
    )


def test_dynamic_middleware_configuration_becomes_a_boundary_not_auth_default(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "bootstrap/app.php",
        "<?php ->withMiddleware(function ($middleware) { $middleware->alias($aliases); });",
    )
    _write(
        tmp_path,
        "routes/web.php",
        "<?php Route::get('/private', [PrivateController::class, 'show'])->middleware('auth');",
    )
    adapter = LaravelLifecycleAdapter()
    route = adapter.entrypoints(
        _context(tmp_path),
        (
            _syntax(
                tmp_path,
                "app/Http/Controllers/PrivateController.php",
                "PrivateController.show",
            ),
        ),
    )[0]
    roles = [
        segment.framework_role
        for segment in adapter.pipeline(_context(tmp_path), route)
    ]

    assert "middleware_unresolved_boundary" in roles
    assert "authentication" not in roles
    assert any(
        event.outcome is CoverageOutcome.PARTIAL
        and event.capability.value == "framework_lifecycle"
        for event in adapter.coverage_events(_context(tmp_path))
    )


def test_adapter_never_passes_absolute_or_traversal_paths_to_workspace_accessor(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(tmp_path, "routes/web.php", "<?php Route::get('/x', [C::class, 'x']);")
    observed: list[Path] = []

    def accessor(path: Path) -> bytes:
        observed.append(path)
        assert not path.is_absolute()
        assert ".." not in path.parts
        return (tmp_path / path).read_bytes()

    LaravelLifecycleAdapter().entrypoints(
        _context(tmp_path, file_accessor=accessor), ()
    )
    assert observed
    assert all(not path.is_absolute() and ".." not in path.parts for path in observed)


def test_direct_preverb_route_chain_is_expanded_without_losing_static_context(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "app/Http/Controllers/UserController.php",
        "<?php class UserController { public function index() {} }",
    )
    _write(
        tmp_path,
        "routes/web.php",
        "<?php Route::prefix('v1')->name('v1.')->domain('api.test')->middleware('auth')->get('/users', [UserController::class, 'index'])->name('users');",
    )
    adapter = LaravelLifecycleAdapter()
    route = adapter.entrypoints(
        _context(tmp_path),
        (
            _syntax(
                tmp_path,
                "app/Http/Controllers/UserController.php",
                "UserController.index",
            ),
        ),
    )[0]

    assert (route.public_path, route.public_name, route.methods) == (
        "/v1/users",
        "v1.users",
        ("GET",),
    )
    assert route.match_constraints.host == "api.test"
    assert "authentication" in [
        segment.framework_role
        for segment in adapter.pipeline(_context(tmp_path), route)
    ]


def test_missing_controller_source_is_partial_without_erasing_route_middleware(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "routes/web.php",
        "<?php Route::get('/private', [PrivateController::class, 'show'])->middleware('auth');",
    )
    adapter = LaravelLifecycleAdapter()
    context = _context(tmp_path)
    route = adapter.entrypoints(
        context,
        (
            _syntax(
                tmp_path,
                "app/Http/Controllers/PrivateController.php",
                "PrivateController.show",
            ),
        ),
    )[0]
    roles = [segment.framework_role for segment in adapter.pipeline(context, route)]

    assert "authentication" in roles
    assert "controller_middleware_unresolved_boundary" in roles
    assert {event.reason_code for event in adapter.coverage_events(context)} >= {
        "controller_middleware_unresolved",
        "handler_outcome_unresolved",
    }


def test_api_resource_omits_create_and_edit_actions(tmp_path: Path) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "routes/api.php",
        "<?php Route::apiResource('users', UserController::class);",
    )
    routes = LaravelLifecycleAdapter().entrypoints(
        _context(tmp_path),
        (
            _syntax(
                tmp_path,
                "app/Http/Controllers/UserController.php",
                "UserController.index",
                "UserController.store",
                "UserController.show",
                "UserController.update",
                "UserController.destroy",
            ),
        ),
    )

    assert [route.public_name for route in routes] == [
        "users.index",
        "users.store",
        "users.show",
        "users.update",
        "users.destroy",
    ]
    assert all("create" not in (route.public_name or "") for route in routes)
    assert all("edit" not in (route.public_name or "") for route in routes)


def test_terminable_middleware_is_resolved_in_its_own_class_after_response(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "app/Http/Kernel.php",
        "<?php class Kernel { protected $middleware = [Terminable::class]; }",
    )
    _write(
        tmp_path,
        "app/Http/Middleware/Terminable.php",
        "<?php class Terminable { public function terminate($request, $response) {} }",
    )
    _write(
        tmp_path,
        "routes/web.php",
        "<?php Route::get('/status', [StatusController::class, 'show']);",
    )
    _write(
        tmp_path,
        "app/Http/Controllers/StatusController.php",
        "<?php class StatusController { public function show() {} }",
    )
    adapter = LaravelLifecycleAdapter()
    route = adapter.entrypoints(
        _context(tmp_path),
        (
            _syntax(
                tmp_path,
                "app/Http/Controllers/StatusController.php",
                "StatusController.show",
            ),
        ),
    )[0]
    pipeline = adapter.pipeline(_context(tmp_path), route)
    roles = [segment.framework_role for segment in pipeline]

    assert "terminating_middleware" in roles
    assert roles.index("response") < roles.index("terminating_middleware")
    response = pipeline[roles.index("response")]
    terminating = pipeline[roles.index("terminating_middleware")]
    assert response.success_successor.kind == "always"
    assert terminating.success_successor.kind == "return"


def test_controller_middleware_only_and_except_apply_to_selected_handler_only(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "routes/web.php",
        "<?php Route::get('/show', [C::class, 'show']); Route::get('/edit', [C::class, 'edit']);",
    )
    _write(
        tmp_path,
        "app/Http/Controllers/C.php",
        "<?php class C { public function __construct() { $this->middleware('auth')->only('edit'); $this->middleware('throttle:api')->except(['edit']); } public function show() {} public function edit() {} }",
    )
    adapter = LaravelLifecycleAdapter()
    routes = adapter.entrypoints(
        _context(tmp_path),
        (_syntax(tmp_path, "app/Http/Controllers/C.php", "C.show", "C.edit"),),
    )
    by_path = {route.public_path: route for route in routes}
    show_roles = [
        segment.framework_role
        for segment in adapter.pipeline(_context(tmp_path), by_path["/show"])
    ]
    edit_roles = [
        segment.framework_role
        for segment in adapter.pipeline(_context(tmp_path), by_path["/edit"])
    ]

    assert "authentication" not in show_roles
    assert "authentication" in edit_roles
    assert len([role for role in show_roles if role == "middleware"]) == 1
    assert len([role for role in edit_roles if role == "middleware"]) == 1


def test_same_named_method_in_another_class_never_leaks_handler_outcomes(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "routes/web.php",
        "<?php Route::get('/safe', [C::class, 'show']);",
    )
    _write(
        tmp_path,
        "app/Http/Controllers/C.php",
        """<?php
class Other { public function show(StoreRequest $request) { abort(403); Ship::dispatch(); } }
class C { public function show() { return response()->noContent(); } }
""",
    )
    adapter = LaravelLifecycleAdapter()
    route = adapter.entrypoints(
        _context(tmp_path),
        (_syntax(tmp_path, "app/Http/Controllers/C.php", "C.show"),),
    )[0]
    roles = [
        segment.framework_role
        for segment in adapter.pipeline(_context(tmp_path), route)
    ]

    assert "validation" not in roles
    assert "job_dispatch" not in roles


def test_dispatch_sync_does_not_create_linked_async_child_flow(tmp_path: Path) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "routes/web.php",
        "<?php Route::post('/orders', [OrderController::class, 'store']);",
    )
    _write(
        tmp_path,
        "app/Http/Controllers/OrderController.php",
        "<?php class OrderController { public function store() { ShipJob::dispatchSync(); } }",
    )
    adapter = LaravelLifecycleAdapter()
    route = adapter.entrypoints(
        _context(tmp_path),
        (
            _syntax(
                tmp_path,
                "app/Http/Controllers/OrderController.php",
                "OrderController.store",
            ),
            _syntax(tmp_path, "app/Jobs/ShipJob.php", "ShipJob.handle"),
        ),
    )[0]
    pipeline = adapter.pipeline(_context(tmp_path), route)

    assert "job_dispatch" not in [segment.framework_role for segment in pipeline]
    assert not any(
        isinstance(successor, AsyncSuccessor)
        for segment in pipeline
        for successor in segment.short_circuit_successors
    )


def test_unresolved_event_and_cyclic_middleware_are_partial_without_pipeline_mutation(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "app/Http/Kernel.php",
        "<?php class Kernel { protected $middlewareGroups = ['a' => ['b'], 'b' => ['a']]; }",
    )
    _write(
        tmp_path,
        "routes/web.php",
        "<?php Route::post('/orders', [OrderController::class, 'store'])->middleware('a');",
    )
    _write(
        tmp_path,
        "app/Http/Controllers/OrderController.php",
        "<?php class OrderController { public function store() { event(new OrderPlaced()); } }",
    )
    adapter = LaravelLifecycleAdapter()
    context = _context(tmp_path)
    route = adapter.entrypoints(
        context,
        (
            _syntax(
                tmp_path,
                "app/Http/Controllers/OrderController.php",
                "OrderController.store",
            ),
        ),
    )[0]
    first = adapter.pipeline(context, route)
    second = adapter.pipeline(context, route)
    events = adapter.coverage_events(context)

    assert [segment.framework_role for segment in first] == [
        segment.framework_role for segment in second
    ]
    assert "middleware_unresolved_boundary" in [
        segment.framework_role for segment in first
    ]
    assert {event.reason_code for event in events} >= {
        "async_target_unresolved",
        "middleware_cycle",
    }


def test_closed_gate_allows_and_denies_have_distinct_security_arms(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "routes/web.php",
        "<?php Route::get('/orders', [OrderController::class, 'show']);",
    )
    _write(
        tmp_path,
        "app/Http/Controllers/OrderController.php",
        "<?php class OrderController { public function show() { if (Gate::allows('view', $order)) {} if (Gate::denies('delete', $order)) {} } }",
    )
    adapter = LaravelLifecycleAdapter()
    route = adapter.entrypoints(
        _context(tmp_path),
        (
            _syntax(
                tmp_path,
                "app/Http/Controllers/OrderController.php",
                "OrderController.show",
            ),
        ),
    )[0]
    pipeline = adapter.pipeline(_context(tmp_path), route)
    by_role = {segment.framework_role: segment for segment in pipeline}

    assert "gate_allow" in by_role
    assert "gate_deny" in by_role
    assert not by_role["gate_allow"].short_circuit_successors
    assert not by_role["gate_deny"].short_circuit_successors
    assert "authorization" not in by_role


def test_authorize_and_auth_middleware_keep_distinct_denial_boundaries(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "routes/web.php",
        "<?php Route::get('/orders', [OrderController::class, 'show'])->middleware('auth');",
    )
    _write(
        tmp_path,
        "app/Http/Controllers/OrderController.php",
        "<?php class OrderController { public function show() { Gate::authorize('view', $order); } }",
    )
    adapter = LaravelLifecycleAdapter()
    route = adapter.entrypoints(
        _context(tmp_path),
        (
            _syntax(
                tmp_path,
                "app/Http/Controllers/OrderController.php",
                "OrderController.show",
            ),
        ),
    )[0]
    by_role = {
        segment.framework_role: segment
        for segment in adapter.pipeline(_context(tmp_path), route)
    }

    assert by_role["authentication"].short_circuit_successors
    assert by_role["authorization"].short_circuit_successors


def test_dynamic_gate_is_an_explicit_security_boundary(tmp_path: Path) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "routes/web.php",
        "<?php Route::get('/orders', [OrderController::class, 'show']);",
    )
    _write(
        tmp_path,
        "app/Http/Controllers/OrderController.php",
        "<?php class OrderController { public function show() { Gate::allows($ability, $order); } }",
    )
    adapter = LaravelLifecycleAdapter()
    context = _context(tmp_path)
    route = adapter.entrypoints(
        context,
        (
            _syntax(
                tmp_path,
                "app/Http/Controllers/OrderController.php",
                "OrderController.show",
            ),
        ),
    )[0]

    assert "gate_unresolved_boundary" in [
        segment.framework_role for segment in adapter.pipeline(context, route)
    ]
    assert "gate_unresolved" in {
        event.reason_code for event in adapter.coverage_events(context)
    }


def test_console_and_scheduler_keep_source_order_and_executable_identity(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "routes/console.php",
        """<?php
Schedule::command(ReportCommand::class)->daily();
Artisan::command('reports:daily', ReportCommand::class);
""",
    )
    entries = LaravelLifecycleAdapter().entrypoints(
        _context(tmp_path),
        (
            _syntax(
                tmp_path,
                "app/Console/Commands/ReportCommand.php",
                "ReportCommand.handle",
            ),
        ),
    )

    assert [(entry.kind, entry.public_name) for entry in entries] == [
        (EntrypointKind.SCHEDULED_JOB, "ReportCommand"),
        (EntrypointKind.CLI_COMMAND, "reports:daily"),
    ]
    assert entries[0].registration_locator.structural_pointer.endswith(
        "/schedule/daily"
    )
    identity = normalized_entrypoint_identity(_context(tmp_path), entries[0])
    assert identity.entrypoint_identity.public_name == "ReportCommand"
    assert identity.entrypoint_identity.trigger.value == "daily"
