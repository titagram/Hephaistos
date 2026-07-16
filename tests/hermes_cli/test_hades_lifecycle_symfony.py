"""Golden behaviour for the closed Symfony lifecycle adapter."""

from __future__ import annotations

import hashlib
from pathlib import Path

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


def _context(root: Path) -> ExtractionContext:
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
        file_accessor=lambda path: (root / path).read_bytes(),
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
    - { path: ^/admin, roles: ROLE_ADMIN }
""",
    )
    _write(
        tmp_path,
        "config/services.yaml",
        """services:
  App\\Listener\\SlowListener:
    tags:
      - { name: kernel.event_listener, event: kernel.request, priority: 1 }
  App\\Listener\\EarlyListener:
    tags:
      - { name: kernel.event_listener, event: kernel.request, priority: 100 }
  App\\Listener\\ExceptionListener:
    tags:
      - { name: kernel.event_listener, event: kernel.exception, priority: 5 }
  App\\Security\\AdminVoter:
    tags: [security.voter]
""",
    )
    _write(
        tmp_path,
        "src/Listener/EarlyListener.php",
        "<?php final class EarlyListener { public function onRequest() { return new Response(); } }",
    )
    _write(
        tmp_path,
        "src/Listener/ExceptionListener.php",
        "<?php final class ExceptionListener { public function onException() { return new Response(); } }",
    )
    _write(
        tmp_path,
        "src/Controller/AdminController.php",
        "<?php final class AdminController { public function dashboard() { return new Response(); } }",
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
        "access_control",
        "voter",
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
        "<?php class StatusController { public function show() {} }",
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

    route = SymfonyLifecycleAdapter().entrypoints(
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
        ->tag('kernel.event_listener', ['event' => 'kernel.request', 'priority' => 50]);
};
""",
    )
    _write(
        tmp_path,
        "src/Listener/PhpRequestListener.php",
        "<?php class PhpRequestListener { public function onRequest() { return new Response(); } }",
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
