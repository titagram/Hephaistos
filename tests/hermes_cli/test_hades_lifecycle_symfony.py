"""Golden behaviour for the closed Symfony lifecycle adapter."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable

from hermes_cli.hades_graph_config import load_hades_graph_index_config
from hermes_cli.hades_graph_v2.model import (
    FrameworkKnowledge,
    FrameworkRecord,
    MethodSemantics,
    SourceIdentity,
)
from hermes_cli.hades_index.lifecycle.frameworks import FrameworkAdapterRegistry
from hermes_cli.hades_index.lifecycle.frameworks.symfony import SymfonyLifecycleAdapter
from hermes_cli.hades_index.lifecycle.model import (
    ConfigLocatorIR,
    CoverageOutcome,
    ExceptionSuccessor,
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
        path,
        1,
        max(1, content.count(b"\n") + 1),
        hashlib.sha256(content).hexdigest(),
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
                name="symfony",
                version="6.4.12",
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


def _prepare_project(root: Path) -> None:
    _write(
        root,
        "composer.json",
        '{"require":{"symfony/framework-bundle":"^6.4"}}',
    )
    _write(
        root,
        "composer.lock",
        '{"packages":[{"name":"symfony/framework-bundle","version":"v6.4.12"}]}',
    )


def test_detects_symfony_from_composer_lock_version(tmp_path: Path) -> None:
    _prepare_project(tmp_path)

    detection = SymfonyLifecycleAdapter().detect(_context(tmp_path))

    assert detection.detected is True
    assert (detection.language, detection.framework) == ("php", "symfony")
    assert SymfonyLifecycleAdapter().detected_version(_context(tmp_path)) == "6.4.12"


def test_routes_keep_yaml_and_attribute_collision_and_apply_import_context(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "config/routes.yaml",
        """api_routes:
  resource: routes/api.yaml
  prefix: /v1
  name_prefix: api_
  methods: [GET]
  host: api.example.test
  condition: request.isXmlHttpRequest()
""",
    )
    _write(
        tmp_path,
        "config/routes/api.yaml",
        """users:
  path: /users
  controller: App\\Controller\\UserController::list
  priority: 10
""",
    )
    _write(
        tmp_path,
        "src/Controller/UserController.php",
        """<?php
namespace App\\Controller;
use Symfony\\Component\\Routing\\Attribute\\Route;
final class UserController {
    #[Route('/v1/users', name: 'api_users_attribute', methods: ['GET'], host: 'api.example.test', condition: 'request.isXmlHttpRequest()', priority: 20)]
    public function list(): Response { return new Response(); }
}
""",
    )
    context = _context(tmp_path)
    adapter = SymfonyLifecycleAdapter()

    routes = adapter.entrypoints(
        context,
        (
            _syntax(
                tmp_path, "src/Controller/UserController.php", "UserController.list"
            ),
        ),
    )

    assert [(item.public_name, item.public_path) for item in routes] == [
        ("api_users_attribute", "/v1/users"),
        ("api_users", "/v1/users"),
    ]
    assert all(item.method_semantics is MethodSemantics.EXPLICIT for item in routes)
    assert all(item.methods == ("GET",) for item in routes)
    assert all(item.match_constraints.host == "api.example.test" for item in routes)
    assert all(item.match_constraints.condition_hash is not None for item in routes)
    assert all(item.handler_local_key is not None for item in routes)


def test_pipeline_uses_explicit_listener_and_security_order_with_short_circuits(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "config/routes.yaml",
        """admin:
  path: /admin
  controller: App\\Controller\\AdminController::dashboard
  methods: [GET]
""",
    )
    _write(
        tmp_path,
        "config/packages/security.yaml",
        """security:
  firewalls:
    main: { lazy: true }
  access_control:
    - { path: ^/admin, allow_if: 'false' }
""",
    )
    _write(
        tmp_path,
        "config/services.yaml",
        """services:
  App\\Listener\\SlowListener:
    tags:
      - { name: kernel.event_listener, event: kernel.request, priority: 1, method: onRequest }
  App\\Listener\\EarlyListener:
    tags:
      - { name: kernel.event_listener, event: kernel.request, priority: 100, method: onRequest }
  App\\Listener\\ExceptionListener:
    tags:
      - { name: kernel.event_listener, event: kernel.exception, priority: 5, method: onException }
  App\\Security\\AdminVoter:
    tags: [security.voter]
""",
    )
    _write(
        tmp_path,
        "src/Listener/EarlyListener.php",
        "<?php namespace App\\Listener; final class EarlyListener { public function onRequest() { return new Response(); } }",
    )
    _write(
        tmp_path,
        "src/Listener/ExceptionListener.php",
        "<?php namespace App\\Listener; final class ExceptionListener { public function onException() { return new Response(); } }",
    )
    _write(
        tmp_path,
        "src/Controller/AdminController.php",
        "<?php namespace App\\Controller; final class AdminController { public function dashboard() { if ($failed) { throw new RuntimeException(); } return new Response(); } }",
    )
    context = _context(tmp_path)
    adapter = SymfonyLifecycleAdapter()
    candidate = adapter.entrypoints(
        context,
        (
            _syntax(
                tmp_path,
                "src/Controller/AdminController.php",
                "AdminController.dashboard",
            ),
        ),
    )[0]

    pipeline = adapter.pipeline(context, candidate)

    assert [segment.framework_role for segment in pipeline] == [
        "router",
        "kernel_request_listener",
        "kernel_request_listener",
        "firewall",
        "access_control_deny",
        "authorization_unresolved_boundary",
        "argument_resolver",
        "controller",
        "response_listener",
        "exception_listener",
    ]
    request_listeners = pipeline[1:3]
    assert [segment.target.descriptor.public_name for segment in request_listeners] == [
        "App\\Listener\\EarlyListener",
        "App\\Listener\\SlowListener",
    ]
    assert request_listeners[0].short_circuit_successors
    assert pipeline[4].short_circuit_successors  # access denial
    assert pipeline[-1].short_circuit_successors  # handled exception response


def test_computed_route_or_service_is_an_unresolved_boundary_not_a_guessed_handler(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "config/routes.yaml",
        """computed:
  path: /safe
  controller: '%env(resolve:APP_CONTROLLER)%'
  methods: [GET]
""",
    )
    context = _context(tmp_path)
    adapter = SymfonyLifecycleAdapter()

    route = adapter.entrypoints(context, ())[0]
    pipeline = adapter.pipeline(context, route)

    assert route.handler_local_key is None
    assert route.unresolved_fact_local_key is not None
    assert any(segment.framework_role == "unresolved_boundary" for segment in pipeline)


def test_inherited_legacy_annotation_and_xml_route_keep_exact_handler_and_error_arm(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "config/routes.xml",
        """<routes>
  <route id="status" path="/status" controller="App\\Controller\\StatusController::show" methods="GET" />
</routes>
""",
    )
    _write(
        tmp_path,
        "src/Controller/ParentController.php",
        """<?php
class ParentController {
    /** @Route('/audit', name="audit_", methods={"GET"}) */
    public function audit() { throw new RuntimeException(); }
}
""",
    )
    _write(
        tmp_path,
        "src/Controller/ChildController.php",
        """<?php
use Symfony\\Component\\Routing\\Attribute\\Route;
#[Route('/v2', name: 'v2_')]
class ChildController extends ParentController {}
""",
    )
    _write(
        tmp_path,
        "src/Controller/StatusController.php",
        "<?php namespace App\\Controller; class StatusController { public function show() {} }",
    )
    adapter = SymfonyLifecycleAdapter()
    context = _context(tmp_path)

    routes = adapter.entrypoints(
        context,
        (
            _syntax(
                tmp_path,
                "src/Controller/ParentController.php",
                "ParentController.audit",
            ),
            _syntax(tmp_path, "src/Controller/ChildController.php"),
            _syntax(
                tmp_path, "src/Controller/StatusController.php", "StatusController.show"
            ),
        ),
    )

    inherited = next(item for item in routes if item.public_path == "/v2/audit")
    xml = next(item for item in routes if item.public_path == "/status")
    assert inherited.public_name == "v2_audit_"
    assert inherited.handler_local_key is not None
    assert xml.handler_local_key is not None
    assert any(
        segment.framework_role == "unhandled_exception"
        for segment in adapter.pipeline(context, inherited)
    )


def test_registry_runs_symfony_adapter_without_legacy_fallback(tmp_path: Path) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "config/routes.php",
        "<?php return static function ($routes) { $routes->add('health', '/health')->controller('App\\Controller\\HealthController::check')->methods(['GET']); };",
    )
    _write(
        tmp_path,
        "src/Controller/HealthController.php",
        "<?php final class HealthController { public function check() {} }",
    )
    context = _context(tmp_path)
    registry = FrameworkAdapterRegistry()
    registry.register(SymfonyLifecycleAdapter())

    from hermes_cli.hades_index.lifecycle.frameworks import run_framework_adapters

    result = run_framework_adapters(
        registry,
        context,
        (
            _syntax(
                tmp_path,
                "src/Controller/HealthController.php",
                "HealthController.check",
            ),
        ),
    )

    assert len(result.candidates) == 1
    assert result.candidates[0].public_path == "/health"
    assert result.framework_segments
    assert result.coverage_events == ()


def test_registry_propagates_partial_symfony_configuration_coverage(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(tmp_path, "config/routes.yaml", "routes: [unterminated")
    registry = FrameworkAdapterRegistry()
    registry.register(SymfonyLifecycleAdapter())

    from hermes_cli.hades_index.lifecycle.frameworks import run_framework_adapters

    result = run_framework_adapters(
        registry,
        _context(tmp_path),
        (_syntax(tmp_path, "src/Controller/HealthController.php"),),
    )

    assert result.coverage_events
    assert all(
        event.outcome is CoverageOutcome.PARTIAL for event in result.coverage_events
    )


def test_php_route_import_applies_static_context_without_executing_configuration(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "config/routes.php",
        """<?php
return static function ($routes): void {
    $routes->import('routes/health.yaml')->prefix('/api')->namePrefix('api_')->methods(['GET'])->host('api.example.test')->condition('request.isXmlHttpRequest()');
};
""",
    )
    _write(
        tmp_path,
        "config/routes/health.yaml",
        """health:
  path: /health
  controller: App\\Controller\\HealthController::check
""",
    )
    _write(
        tmp_path,
        "src/Controller/HealthController.php",
        "<?php final class HealthController { public function check() {} }",
    )
    context = _context(tmp_path)

    adapter = SymfonyLifecycleAdapter()
    route = adapter.entrypoints(
        context,
        (
            _syntax(
                tmp_path,
                "src/Controller/HealthController.php",
                "HealthController.check",
            ),
        ),
    )[0]

    assert (route.public_name, route.public_path, route.methods) == (
        "api_health",
        "/api/health",
        ("GET",),
    )
    assert route.match_constraints.host == "api.example.test"
    assert route.match_constraints.condition_hash is not None
    assert adapter.coverage_events(context) == ()


def test_php_service_configuration_registers_ordered_kernel_listener(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "config/routes.yaml",
        """status:
  path: /status
  controller: App\\Controller\\StatusController::show
""",
    )
    _write(
        tmp_path,
        "config/services.php",
        """<?php
return static function ($services): void {
    $services->set(App\\Listener\\PhpRequestListener::class)
        ->tag('kernel.event_listener', ['event' => 'kernel.request', 'priority' => 50, 'method' => 'onRequest']);
};
""",
    )
    _write(
        tmp_path,
        "src/Listener/PhpRequestListener.php",
        "<?php namespace App\\Listener; class PhpRequestListener { public function onRequest() { return new Response(); } }",
    )
    _write(
        tmp_path,
        "src/Controller/StatusController.php",
        "<?php class StatusController { public function show() {} }",
    )
    adapter = SymfonyLifecycleAdapter()
    context = _context(tmp_path)
    route = adapter.entrypoints(
        context,
        (
            _syntax(
                tmp_path,
                "src/Controller/StatusController.php",
                "StatusController.show",
            ),
        ),
    )[0]

    listener = adapter.pipeline(context, route)[1]

    assert listener.framework_role == "kernel_request_listener"
    assert listener.target.descriptor.public_name == "App\\Listener\\PhpRequestListener"
    assert listener.short_circuit_successors


def test_imports_never_send_absolute_or_traversal_paths_to_the_accessor(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "config/routes.yaml",
        "outside: { resource: /private/secret-routes.yaml }\n",
    )
    _write(
        tmp_path,
        "config/routes.xml",
        '<routes><import resource="../../secret-routes.xml" /></routes>',
    )
    _write(
        tmp_path,
        "config/routes.php",
        "$routes->import('/private/secret-routes.php');",
    )
    paths: list[Path] = []

    def accessor(path: Path) -> bytes:
        paths.append(path)
        assert not path.is_absolute()
        assert ".." not in path.parts
        return (tmp_path / path).read_bytes()

    SymfonyLifecycleAdapter().entrypoints(
        _context(tmp_path, file_accessor=accessor), ()
    )

    assert paths
    assert all(not path.is_absolute() and ".." not in path.parts for path in paths)


def test_handler_needs_one_class_qualified_proof_not_a_bare_method_match(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "config/routes.yaml",
        "missing:\n  path: /missing\n  controller: App\\Controller\\MissingController::index\n",
    )
    route = SymfonyLifecycleAdapter().entrypoints(
        _context(tmp_path),
        (
            _syntax(tmp_path, "src/A.php", "A.index"),
            _syntax(tmp_path, "src/B.php", "B.index"),
        ),
    )[0]

    assert route.handler_local_key is None
    assert route.unresolved_fact_local_key is not None


def test_computed_route_fields_are_partial_coverage_not_exact_public_endpoints(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "config/routes.yaml",
        "computed:\n  path: /%kernel.route_path%\n  name: '%kernel.route_name%'\n  host: '%kernel.route_host%'\n  condition: '%kernel.route_condition%'\n  controller: App\\Controller\\HealthController::check\nimported: { resource: '%kernel.route_resource%' }\n",
    )
    adapter = SymfonyLifecycleAdapter()
    context = _context(tmp_path)

    routes = adapter.entrypoints(
        context,
        (
            _syntax(
                tmp_path,
                "src/Controller/HealthController.php",
                "HealthController.check",
            ),
        ),
    )

    assert routes == ()
    assert any(
        event.outcome is CoverageOutcome.PARTIAL
        and event.reason_code == "framework_config_unresolved"
        for event in adapter.coverage_events(context)
    )


def test_same_imported_resource_is_retained_for_each_declared_context(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "config/routes.yaml",
        "one: { resource: routes/shared.yaml, prefix: /one }\n"
        "two: { resource: routes/shared.yaml, prefix: /two }\n",
    )
    _write(
        tmp_path,
        "config/routes/shared.yaml",
        "health:\n  path: /health\n  controller: App\\Controller\\HealthController::check\n",
    )

    routes = SymfonyLifecycleAdapter().entrypoints(
        _context(tmp_path),
        (
            _syntax(
                tmp_path,
                "src/Controller/HealthController.php",
                "HealthController.check",
            ),
        ),
    )

    assert [route.public_path for route in routes] == ["/one/health", "/two/health"]


def test_equal_priority_listener_source_order_and_subscriber_survive_pipeline(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "config/routes.yaml",
        "health:\n  path: /health\n  controller: App\\Controller\\HealthController::check\n",
    )
    _write(
        tmp_path,
        "config/services.yaml",
        "services:\n"
        "  App\\Listener\\ZFirst:\n"
        "    tags: [{ name: kernel.event_listener, event: kernel.request, priority: 10, method: onRequest }]\n"
        "  App\\Listener\\ASecond:\n"
        "    tags: [{ name: kernel.event_listener, event: kernel.request, priority: 10, method: onRequest }]\n"
        "  App\\Listener\\Subscriber:\n"
        "    autoconfigure: true\n",
    )
    _write(
        tmp_path,
        "src/Listener/Subscriber.php",
        "<?php namespace App\\Listener; class Subscriber implements EventSubscriberInterface { public static function getSubscribedEvents() { return [KernelEvents::REQUEST => ['onRequest', 5]]; } public function onRequest() {} }",
    )
    adapter = SymfonyLifecycleAdapter()
    context = _context(tmp_path)
    route = adapter.entrypoints(
        context,
        (
            _syntax(
                tmp_path,
                "src/Controller/HealthController.php",
                "HealthController.check",
            ),
            _syntax(tmp_path, "src/Listener/Subscriber.php", "Subscriber.onRequest"),
        ),
    )[0]

    names = [
        segment.target.descriptor.public_name
        for segment in adapter.pipeline(context, route)
        if segment.framework_role == "kernel_request_listener"
    ]

    assert names == [
        "App\\Listener\\ZFirst",
        "App\\Listener\\ASecond",
        "App\\Listener\\Subscriber",
    ]


def test_response_shortcuts_require_the_exact_listener_and_controller_method(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "config/routes.yaml",
        "go:\n  path: /go\n  controller: App\\Controller\\NoResponseController::go\n",
    )
    _write(
        tmp_path,
        "config/services.yaml",
        "services:\n  App\\Listener\\NoResponse:\n    tags: [{ name: kernel.event_listener, event: kernel.request, method: onRequest }]\n",
    )
    _write(
        tmp_path,
        "src/Listener/NoResponse.php",
        "<?php class NoResponse { public function onRequest() {} public function unrelated() { return new Response(); } }",
    )
    _write(
        tmp_path,
        "src/Controller/NoResponseController.php",
        "<?php class NoResponseController { public function go() {} public function unrelated() { return new Response(); } }",
    )
    adapter = SymfonyLifecycleAdapter()
    context = _context(tmp_path)
    route = adapter.entrypoints(
        context,
        (
            _syntax(
                tmp_path,
                "src/Controller/NoResponseController.php",
                "NoResponseController.go",
            ),
        ),
    )[0]

    pipeline = adapter.pipeline(context, route)
    listener = next(
        item for item in pipeline if item.framework_role == "kernel_request_listener"
    )
    controller = next(item for item in pipeline if item.framework_role == "controller")

    assert listener.short_circuit_successors == ()
    assert controller.short_circuit_successors == ()


def test_security_first_match_distinguishes_public_allow_from_runtime_boundary(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "config/routes.yaml",
        "public:\n  path: /public/info\n  controller: App\\Controller\\HealthController::check\nadmin:\n  path: /admin/users\n  controller: App\\Controller\\HealthController::check\n",
    )
    _write(
        tmp_path,
        "config/packages/security.yaml",
        "security:\n  access_control:\n    - { path: ^/public, roles: PUBLIC_ACCESS }\n    - { path: ^/admin/.*, roles: ROLE_ADMIN }\n",
    )
    adapter = SymfonyLifecycleAdapter()
    context = _context(tmp_path)
    routes = adapter.entrypoints(
        context,
        (
            _syntax(
                tmp_path,
                "src/Controller/HealthController.php",
                "HealthController.check",
            ),
        ),
    )

    public_pipeline = adapter.pipeline(context, routes[0])
    admin_pipeline = adapter.pipeline(context, routes[1])
    public_access = next(
        item
        for item in public_pipeline
        if item.framework_role == "access_control_allow"
    )

    assert public_access.short_circuit_successors == ()
    assert any(
        item.framework_role == "security_unresolved_boundary" for item in admin_pipeline
    )


def test_exception_listener_is_reached_only_by_a_proven_throw_arm(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "config/routes.yaml",
        "ok:\n  path: /ok\n  controller: App\\Controller\\OkController::show\nboom:\n  path: /boom\n  controller: App\\Controller\\BoomController::show\n",
    )
    _write(
        tmp_path,
        "config/services.yaml",
        "services:\n  App\\Listener\\ExceptionListener:\n    tags: [{ name: kernel.event_listener, event: kernel.exception, method: onException }]\n",
    )
    _write(
        tmp_path,
        "src/Controller/OkController.php",
        "<?php namespace App\\Controller; class OkController { public function show() {} }",
    )
    _write(
        tmp_path,
        "src/Controller/BoomController.php",
        "<?php namespace App\\Controller; class BoomController { public function show() { throw new RuntimeException(); } }",
    )
    _write(
        tmp_path,
        "src/Listener/ExceptionListener.php",
        "<?php class ExceptionListener { public function onException() { return new Response(); } }",
    )
    adapter = SymfonyLifecycleAdapter()
    context = _context(tmp_path)
    routes = adapter.entrypoints(
        context,
        (
            _syntax(tmp_path, "src/Controller/OkController.php", "OkController.show"),
            _syntax(
                tmp_path, "src/Controller/BoomController.php", "BoomController.show"
            ),
        ),
    )

    ok_pipeline = adapter.pipeline(context, routes[0])
    boom_pipeline = adapter.pipeline(context, routes[1])
    boom_controller = next(
        item for item in boom_pipeline if item.framework_role == "controller"
    )

    assert not any(item.framework_role == "exception_listener" for item in ok_pipeline)
    assert any(item.framework_role == "exception_listener" for item in boom_pipeline)
    assert any(
        isinstance(item, ExceptionSuccessor)
        for item in boom_controller.short_circuit_successors
    )


def test_malformed_or_invalid_configuration_becomes_partial_coverage_not_crash(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(tmp_path, "config/routes.yaml", "routes: [unterminated")
    _write(
        tmp_path,
        "config/routes.xml",
        '<routes><route id="bad" path="/bad" priority="not-an-int" /></routes>',
    )
    adapter = SymfonyLifecycleAdapter()
    context = _context(tmp_path)

    assert adapter.entrypoints(context, ()) == ()
    events = adapter.coverage_events(context)
    assert len(events) >= 2
    assert all(event.outcome is CoverageOutcome.PARTIAL for event in events)


def test_dynamic_yaml_methods_list_is_partial_coverage_not_an_invalid_route(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "config/routes.yaml",
        "x:\n  path: /x\n  methods: ['%kernel.method%']\n"
        "  controller: App\\Controller\\C::go\n",
    )
    adapter = SymfonyLifecycleAdapter()
    context = _context(tmp_path)

    assert adapter.entrypoints(context, ()) == ()
    assert any(
        event.outcome is CoverageOutcome.PARTIAL
        for event in adapter.coverage_events(context)
    )


def test_dynamic_yaml_priority_is_partial_coverage_not_guessed_order(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "config/routes.yaml",
        "x:\n  path: /x\n  priority: '%kernel.priority%'\n"
        "  controller: App\\Controller\\C::go\n",
    )
    adapter = SymfonyLifecycleAdapter()
    context = _context(tmp_path)

    assert adapter.entrypoints(context, ()) == ()
    assert any(
        event.outcome is CoverageOutcome.PARTIAL
        for event in adapter.coverage_events(context)
    )


def test_dynamic_php_import_prefix_is_partial_coverage_not_an_empty_prefix(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "config/routes.php",
        "<?php $routes->import('routes/x.yaml')->prefix($dynamic);",
    )
    _write(
        tmp_path,
        "config/routes/x.yaml",
        "x:\n  path: /x\n  controller: App\\Controller\\C::go\n",
    )
    adapter = SymfonyLifecycleAdapter()
    context = _context(tmp_path)

    assert adapter.entrypoints(context, ()) == ()
    assert any(
        event.outcome is CoverageOutcome.PARTIAL
        for event in adapter.coverage_events(context)
    )


def test_dynamic_php_import_resource_is_partial_coverage_not_silently_skipped(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(tmp_path, "config/routes.php", "<?php $routes->import($dynamic);")
    adapter = SymfonyLifecycleAdapter()
    context = _context(tmp_path)

    assert adapter.entrypoints(context, ()) == ()
    events = adapter.coverage_events(context)
    assert len(events) == 1
    assert events[0].outcome is CoverageOutcome.PARTIAL
    assert events[0].path == "config/routes.php"


def test_dynamic_php_route_name_is_partial_coverage_not_silently_skipped(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "config/routes.php",
        "<?php $routes->add($name, '/x')->controller('App\\Controller\\C::go');",
    )
    adapter = SymfonyLifecycleAdapter()
    context = _context(tmp_path)

    assert adapter.entrypoints(context, ()) == ()
    events = adapter.coverage_events(context)
    assert len(events) == 1
    assert events[0].outcome is CoverageOutcome.PARTIAL
    assert events[0].path == "config/routes.php"


def test_dynamic_php_route_path_is_partial_coverage_not_silently_skipped(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "config/routes.php",
        "<?php $routes->add('x', $path)->controller('App\\Controller\\C::go');",
    )
    adapter = SymfonyLifecycleAdapter()
    context = _context(tmp_path)

    assert adapter.entrypoints(context, ()) == ()
    events = adapter.coverage_events(context)
    assert len(events) == 1
    assert events[0].outcome is CoverageOutcome.PARTIAL
    assert events[0].path == "config/routes.php"


def test_dynamic_attribute_methods_is_partial_coverage_not_unrestricted(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "src/Controller/C.php",
        "<?php class C { #[Route('/x', methods: $methods)] public function go() {} }",
    )
    adapter = SymfonyLifecycleAdapter()
    context = _context(tmp_path)

    assert (
        adapter.entrypoints(
            context, (_syntax(tmp_path, "src/Controller/C.php", "C.go"),)
        )
        == ()
    )
    assert any(
        event.outcome is CoverageOutcome.PARTIAL
        for event in adapter.coverage_events(context)
    )


def test_dynamic_named_attribute_path_is_partial_coverage_not_a_literal_prefix(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "src/Controller/C.php",
        "<?php class C { #[Route(path: '/x' . $suffix)] public function go() {} }",
    )
    adapter = SymfonyLifecycleAdapter()
    context = _context(tmp_path)

    assert (
        adapter.entrypoints(
            context, (_syntax(tmp_path, "src/Controller/C.php", "C.go"),)
        )
        == ()
    )
    assert any(
        event.outcome is CoverageOutcome.PARTIAL
        for event in adapter.coverage_events(context)
    )


def test_fqcn_handler_requires_a_matching_php_namespace_proof(tmp_path: Path) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "config/routes.yaml",
        "x:\n  path: /x\n  controller: App\\Controller\\AdminController::index\n",
    )
    _write(
        tmp_path,
        "vendor/X/AdminController.php",
        "<?php namespace Vendor\\X; class AdminController { public function index() {} }",
    )

    route = SymfonyLifecycleAdapter().entrypoints(
        _context(tmp_path),
        (_syntax(tmp_path, "vendor/X/AdminController.php", "AdminController.index"),),
    )[0]

    assert route.handler_local_key is None
    assert route.unresolved_fact_local_key is not None


def test_registered_fqcn_subscriber_requires_matching_namespace_proof(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "config/routes.yaml",
        "x:\n  path: /x\n  controller: App\\Controller\\C::go\n",
    )
    _write(
        tmp_path,
        "config/services.yaml",
        "services:\n  App\\Listener\\Subscriber:\n    autoconfigure: true\n",
    )
    _write(
        tmp_path,
        "vendor/X/Subscriber.php",
        "<?php namespace Vendor\\X; class Subscriber implements EventSubscriberInterface { public static function getSubscribedEvents() { return [KernelEvents::REQUEST => 'onRequest']; } public function onRequest() {} }",
    )
    adapter = SymfonyLifecycleAdapter()
    context = _context(tmp_path)
    route = adapter.entrypoints(
        context,
        (_syntax(tmp_path, "vendor/X/Subscriber.php", "Subscriber.onRequest"),),
    )[0]

    assert not any(
        segment.framework_role == "kernel_request_listener"
        for segment in adapter.pipeline(context, route)
    )


def test_unregistered_event_subscriber_is_not_a_request_listener(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "config/routes.yaml",
        "x:\n  path: /x\n  controller: App\\Controller\\C::go\n",
    )
    _write(
        tmp_path,
        "src/Utility/Helper.php",
        "<?php class Helper implements EventSubscriberInterface { public static function getSubscribedEvents() { return [KernelEvents::REQUEST => 'onRequest']; } public function onRequest() {} }",
    )
    adapter = SymfonyLifecycleAdapter()
    context = _context(tmp_path)
    route = adapter.entrypoints(
        context,
        (
            _syntax(tmp_path, "src/Controller/C.php", "C.go"),
            _syntax(tmp_path, "src/Utility/Helper.php", "Helper.onRequest"),
        ),
    )[0]

    assert not any(
        segment.framework_role == "kernel_request_listener"
        for segment in adapter.pipeline(context, route)
    )


def test_unused_voter_is_a_boundary_not_a_success_pipeline_stage(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "config/routes.yaml",
        "x:\n  path: /x\n  controller: App\\Controller\\C::go\n",
    )
    _write(
        tmp_path,
        "config/services.yaml",
        "services:\n  App\\Security\\UnusedVoter:\n    tags: [security.voter]\n",
    )
    adapter = SymfonyLifecycleAdapter()
    context = _context(tmp_path)
    route = adapter.entrypoints(
        context,
        (_syntax(tmp_path, "src/Controller/C.php", "C.go"),),
    )[0]

    roles = [segment.framework_role for segment in adapter.pipeline(context, route)]

    assert "voter" not in roles
    assert "authorization_unresolved_boundary" in roles
