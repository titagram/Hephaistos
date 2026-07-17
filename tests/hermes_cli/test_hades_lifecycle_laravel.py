"""Golden behaviour for the closed Laravel lifecycle adapter.

These fixtures intentionally use only files visible through ``ExtractionContext``.
They exercise static Laravel registration facts, not a booted application.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable

from hermes_cli.hades_graph_config import load_hades_graph_index_config
from hermes_cli.hades_graph_v2.model import (
    EntrypointKind,
    FrameworkKnowledge,
    FrameworkRecord,
    MethodSemantics,
    SourceIdentity,
)
from hermes_cli.hades_index.lifecycle.frameworks.laravel import LaravelLifecycleAdapter
from hermes_cli.hades_index.lifecycle.model import (
    AsyncSuccessor,
    ConfigLocatorIR,
    CoverageOutcome,
    ExtractionContext,
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
    assert roles[-1] == "response"
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
        (EntrypointKind.SCHEDULED_JOB, "daily"),
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
        (EntrypointKind.SCHEDULED_JOB, "daily"),
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
