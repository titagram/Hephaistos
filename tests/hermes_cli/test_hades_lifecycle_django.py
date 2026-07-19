"""Golden behaviour for static, bounded Django lifecycle extraction.

The fixtures deliberately model configuration and source text only.  They do
not import Django or execute a project: every positive fact has to be visible
through the scoped ``ExtractionContext.file_accessor``.
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
from hermes_cli.hades_index.lifecycle.frameworks.django import DjangoLifecycleAdapter
from hermes_cli.hades_index.lifecycle.model import (
    ConfigLocatorIR,
    CoverageOutcome,
    ExtractionContext,
    InventoryFile,
    FrameworkLocalTarget,
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
    settings = _location(root, "project/settings.py")
    return ExtractionContext(
        workspace_root=root,
        project_id="project",
        workspace_binding_id="binding",
        source_identity=SourceIdentity(None, "a" * 64, False, None),
        graph_config=load_hades_graph_index_config({}),
        detected_languages=("python",),
        detected_frameworks=(
            FrameworkRecord(
                language="python",
                name="django",
                version="5.1.0",
                detector="pyproject",
                configuration_paths=("project/settings.py",),
                knowledge=FrameworkKnowledge.VERIFIED,
            ),
        ),
        composer_metadata=(),
        python_metadata=(ConfigLocatorIR(settings, "settings", 0),),
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


def _function(name: str, line: int) -> StructuralSymbol:
    """Build the exact Python function shape emitted by Tree-sitter."""

    return StructuralSymbol(name, "function", line, line)


def _class(name: str, line: int) -> StructuralSymbol:
    return StructuralSymbol(name, "class", line, line)


def _method(class_name: str, name: str, line: int) -> StructuralSymbol:
    # Python's tree-sitter adapter emits the bare method name plus container,
    # unlike PHP/TypeScript's qualified method names.
    return StructuralSymbol(name, "function", line, line, container=class_name)


def _syntax(root: Path, path: str, *symbols: StructuralSymbol) -> SyntaxIR:
    return SyntaxIR(
        ParsedFile(
            path=path,
            language="python",
            symbols=symbols,
            imports=(),
            calls=(),
        ),
        (),
    )


def _prepare_project(root: Path, *, middleware: str = "[]") -> None:
    _write(
        root,
        "pyproject.toml",
        '[project]\ndependencies = ["Django>=5.1"]\n',
    )
    _write(
        root,
        "project/settings.py",
        f'ROOT_URLCONF = "project.urls"\nMIDDLEWARE = {middleware}\n',
    )


def test_recurses_static_includes_preserves_order_namespace_prefix_and_converters(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "project/urls.py",
        """from django.urls import include, path, re_path
from . import views
urlpatterns = [
    path("api/<int:tenant_id>/", include(("project.api.urls", "api"), namespace="v1")),
    re_path(r"^legacy/(?P<slug>[-\\w]+)/$", views.legacy, name="legacy"),
]
""",
    )
    _write(
        tmp_path,
        "project/api/urls.py",
        'from project import views\nurlpatterns = [path("items/", views.items, name="items")]\n',
    )
    adapter = DjangoLifecycleAdapter()
    routes = adapter.entrypoints(
        _context(tmp_path),
        (
            _syntax(
                tmp_path,
                "project/views.py",
                _function("items", 1),
                _function("legacy", 2),
            ),
            _syntax(tmp_path, "project/api/urls.py"),
            _syntax(tmp_path, "project/urls.py"),
        ),
    )

    assert adapter.detect(_context(tmp_path)).detected is True
    assert [(item.public_path, item.public_name) for item in routes] == [
        ("/api/<int:tenant_id>/items/", "v1:items"),
        ("/^legacy/(?P<slug>[-\\w]+)/$", "legacy"),
    ]
    assert all(item.kind is EntrypointKind.HTTP_ROUTE for item in routes)
    assert all(item.method_semantics is MethodSemantics.UNRESTRICTED for item in routes)
    assert all(item.handler_local_key is not None for item in routes)


def test_pipeline_models_decorator_denial_middleware_order_and_entered_unwind(
    tmp_path: Path,
) -> None:
    _prepare_project(
        tmp_path,
        middleware='["project.middleware.First", "project.middleware.Second"]',
    )
    _write(
        tmp_path,
        "project/urls.py",
        'from . import views\nurlpatterns = [path("secure/", views.secure, name="secure")]\n',
    )
    _write(
        tmp_path,
        "project/views.py",
        """from django.contrib.auth.decorators import login_required
@login_required
def secure(request):
    return HttpResponse("ok")
""",
    )
    _write(
        tmp_path,
        "project/middleware.py",
        """class First:
    def process_request(self, request): return HttpResponse()
    def process_response(self, request, response): return response
class Second:
    def process_request(self, request): return HttpResponse()
    def process_response(self, request, response): return response
""",
    )
    adapter = DjangoLifecycleAdapter()
    context = _context(tmp_path)
    candidate = adapter.entrypoints(
        context,
        (_syntax(tmp_path, "project/views.py", _function("secure", 2)),),
    )[0]
    pipeline = adapter.pipeline(context, candidate)
    _assert_framework_pipeline_closes_adapter_result(
        (candidate,), pipeline, adapter.pipeline_facts(context, candidate)
    )

    assert [segment.framework_role for segment in pipeline] == [
        "url_resolver",
        "middleware_request",
        "middleware_request",
        "decorator_access_control",
        "sync_view",
        "middleware_response",
        "middleware_response",
        "response",
    ]
    assert [
        segment.target.descriptor.public_name
        for segment in pipeline
        if segment.framework_role == "middleware_request"
    ] == ["project.middleware.First", "project.middleware.Second"]
    first_request, second_request = pipeline[1:3]
    assert first_request.short_circuit_successors
    assert second_request.short_circuit_successors
    assert (
        first_request.short_circuit_successors[0].target_block_key
        == pipeline[6].local_key
    )
    assert (
        second_request.short_circuit_successors[0].target_block_key
        == pipeline[5].local_key
    )
    assert pipeline[3].short_circuit_successors  # decorator denial
    # Second response runs before First on the normal response arm.
    assert pipeline[5].target.descriptor.public_name == "project.middleware.Second"
    assert pipeline[6].target.descriptor.public_name == "project.middleware.First"


def test_cbv_dispatch_sync_async_and_exception_arms_are_explicit(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "project/urls.py",
        """from . import views
urlpatterns = [
    path("sync/", views.SyncView.as_view(), name="sync"),
    path("async/", views.async_view, name="async"),
    path("handled/", views.handled, name="handled"),
    path("broken/", views.broken, name="broken"),
]
""",
    )
    _write(
        tmp_path,
        "project/views.py",
        """class SyncView:
    def dispatch(self, request): pass
    def get(self, request): return HttpResponse()
    def post(self, request): return HttpResponse()
async def async_view(request): return HttpResponse()
def handled(request):
    try: raise ValueError()
    except ValueError: return HttpResponse()
def broken(request): raise RuntimeError()
""",
    )
    adapter = DjangoLifecycleAdapter()
    context = _context(tmp_path)
    routes = adapter.entrypoints(
        context,
        (
            _syntax(
                tmp_path,
                "project/views.py",
                _class("SyncView", 1),
                _method("SyncView", "dispatch", 2),
                _method("SyncView", "get", 3),
                _method("SyncView", "post", 4),
                _function("async_view", 5),
                _function("handled", 6),
                _function("broken", 7),
            ),
        ),
    )
    pipelines = {
        route.public_name: adapter.pipeline(context, route) for route in routes
    }

    assert [
        segment.framework_role
        for segment in pipelines["sync"]
        if segment.framework_role.startswith("cbv_")
    ] == [
        "cbv_dispatch",
        "cbv_get",
        "cbv_post",
    ]
    assert all(
        isinstance(segment.target, FrameworkLocalTarget)
        for segment in pipelines["sync"]
        if segment.framework_role in {"cbv_get", "cbv_post"}
    )
    assert "async_view" in {segment.framework_role for segment in pipelines["async"]}
    assert "handled_exception" in {
        segment.framework_role for segment in pipelines["handled"]
    }
    assert "unhandled_exception" in {
        segment.framework_role for segment in pipelines["broken"]
    }


def test_management_command_and_asgi_wsgi_declarations_are_static_entrypoints(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(tmp_path, "project/urls.py", "urlpatterns = []\n")
    _write(
        tmp_path,
        "project/management/commands/rebuild.py",
        "class Command:\n    def handle(self, *args): pass\n",
    )
    _write(
        tmp_path,
        "project/asgi.py",
        "application = get_asgi_application()\n",
    )
    _write(
        tmp_path,
        "project/wsgi.py",
        "application = get_wsgi_application()\n",
    )
    adapter = DjangoLifecycleAdapter()
    context = _context(tmp_path)
    candidates = adapter.entrypoints(
        context,
        (
            _syntax(
                tmp_path,
                "project/management/commands/rebuild.py",
                _class("Command", 1),
                _method("Command", "handle", 2),
            ),
            _syntax(tmp_path, "project/asgi.py"),
            _syntax(tmp_path, "project/wsgi.py"),
        ),
    )

    assert [(item.kind, item.public_name) for item in candidates] == [
        (EntrypointKind.CLI_COMMAND, "rebuild"),
        (EntrypointKind.PROCESS_MAIN, "asgi"),
        (EntrypointKind.PROCESS_MAIN, "wsgi"),
    ]
    asgi = next(item for item in candidates if item.public_name == "asgi")
    assert asgi.handler_local_key is None
    assert asgi.unresolved_fact_local_key is not None
    assert adapter.pipeline(context, asgi)[0].framework_role == "asgi_application"


def test_dynamic_root_or_url_facts_are_partial_not_defaulted_or_guessed(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "project/settings.py",
        'ROOT_URLCONF = os.environ["URLCONF"]\nMIDDLEWARE = build_middleware()\n',
    )
    _write(
        tmp_path,
        "project/urls.py",
        'urlpatterns = [path(prefix + "users/", views.users, name="users")]\n',
    )
    adapter = DjangoLifecycleAdapter()
    context = _context(tmp_path)

    assert (
        adapter.entrypoints(
            context, (_syntax(tmp_path, "project/views.py", _function("users", 1)),)
        )
        == ()
    )
    assert {
        (event.reason_code, event.outcome) for event in adapter.coverage_events(context)
    } >= {("root_urlconf_unresolved", CoverageOutcome.PARTIAL)}


def test_dynamic_route_expression_does_not_create_a_plausible_route(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "project/urls.py",
        'urlpatterns = [path(prefix + "users/", views.users, name="users")]\n',
    )
    adapter = DjangoLifecycleAdapter()
    context = _context(tmp_path)

    assert (
        adapter.entrypoints(
            context, (_syntax(tmp_path, "project/views.py", _function("users", 1)),)
        )
        == ()
    )
    assert ("url_pattern_unresolved", CoverageOutcome.PARTIAL) in {
        (event.reason_code, event.outcome) for event in adapter.coverage_events(context)
    }


def test_resolves_real_tree_sitter_function_and_cbv_symbols_via_url_imports(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "project/urls.py",
        """from . import views
urlpatterns = [
    path("function/", views.function_view, name="function"),
    path("cbv/", views.RealView.as_view(), name="cbv"),
]
""",
    )
    _write(
        tmp_path,
        "project/views.py",
        """def function_view(request): return HttpResponse()
class RealView:
    def dispatch(self, request): pass
    def get(self, request): return HttpResponse()
""",
    )
    context = _context(tmp_path)
    adapter = DjangoLifecycleAdapter()
    routes = adapter.entrypoints(
        context,
        (
            _syntax(
                tmp_path,
                "project/views.py",
                _function("function_view", 1),
                _class("RealView", 2),
                _method("RealView", "dispatch", 3),
                _method("RealView", "get", 4),
            ),
        ),
    )

    assert all(route.handler_local_key is not None for route in routes)
    cbv = next(route for route in routes if route.public_name == "cbv")
    assert all(
        isinstance(segment.target, FrameworkLocalTarget)
        for segment in adapter.pipeline(context, cbv)
        if segment.framework_role in {"cbv_dispatch", "cbv_get"}
    )


def test_cbv_does_not_merge_same_named_class_from_a_different_module(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "project/urls.py",
        'from . import views\nurlpatterns = [path("dup/", views.Duplicate.as_view(), name="dup")]\n',
    )
    _write(
        tmp_path,
        "project/views.py",
        """class Duplicate:
    def post(self, request): return HttpResponse()
""",
    )
    _write(
        tmp_path,
        "other/views.py",
        """class Duplicate:
    def dispatch(self, request): pass
    def get(self, request): return HttpResponse()
""",
    )
    context = _context(tmp_path)
    adapter = DjangoLifecycleAdapter()
    candidate = adapter.entrypoints(
        context,
        (
            _syntax(
                tmp_path,
                "project/views.py",
                _class("Duplicate", 1),
                _method("Duplicate", "post", 2),
            ),
            _syntax(
                tmp_path,
                "other/views.py",
                _class("Duplicate", 1),
                _method("Duplicate", "dispatch", 2),
                _method("Duplicate", "get", 3),
            ),
        ),
    )[0]
    pipeline = adapter.pipeline(context, candidate)

    assert candidate.handler_local_key is None
    assert candidate.unresolved_fact_local_key is not None
    assert [
        segment.framework_role
        for segment in pipeline
        if segment.framework_role.startswith("cbv_")
    ] == ["cbv_dispatch_boundary"]
    assert not any(
        isinstance(segment.target, FrameworkLocalTarget) for segment in pipeline
    )


def test_command_helper_without_exact_command_handle_is_partial_not_a_cli_root(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(tmp_path, "project/urls.py", "urlpatterns = []\n")
    _write(
        tmp_path,
        "project/management/commands/helpers.py",
        "def utility(): pass\n",
    )
    context = _context(tmp_path)
    adapter = DjangoLifecycleAdapter()

    assert (
        adapter.entrypoints(
            context,
            (
                _syntax(
                    tmp_path,
                    "project/management/commands/helpers.py",
                    _function("utility", 1),
                ),
            ),
        )
        == ()
    )
    assert ("management_command_unresolved", CoverageOutcome.PARTIAL) in {
        (event.reason_code, event.outcome) for event in adapter.coverage_events(context)
    }


def test_short_circuit_never_runs_a_later_unentered_response_middleware(
    tmp_path: Path,
) -> None:
    _prepare_project(
        tmp_path,
        middleware='["project.middleware.First", "project.middleware.Second"]',
    )
    _write(
        tmp_path,
        "project/urls.py",
        'from . import views\nurlpatterns = [path("stop/", views.stop, name="stop")]\n',
    )
    _write(tmp_path, "project/views.py", "def stop(request): return HttpResponse()\n")
    _write(
        tmp_path,
        "project/middleware.py",
        """class First:
    def process_request(self, request): return HttpResponse()
class Second:
    def process_request(self, request): return None
    def process_response(self, request, response): return response
""",
    )
    context = _context(tmp_path)
    adapter = DjangoLifecycleAdapter()
    candidate = adapter.entrypoints(
        context, (_syntax(tmp_path, "project/views.py", _function("stop", 1)),)
    )[0]
    pipeline = adapter.pipeline(context, candidate)
    first = next(
        segment
        for segment in pipeline
        if segment.framework_role == "middleware_request"
    )
    response = next(
        segment for segment in pipeline if segment.framework_role == "response"
    )

    assert first.short_circuit_successors[0].target_block_key == response.local_key


def test_only_lexically_enclosing_compatible_catches_create_handled_exception_arm(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "project/urls.py",
        """from . import views
urlpatterns = [
    path("outside/", views.outside, name="outside"),
    path("mismatch/", views.mismatch, name="mismatch"),
]
""",
    )
    _write(
        tmp_path,
        "project/views.py",
        """def outside(request):
    try:
        value = 1
    except ValueError:
        return HttpResponse()
    raise RuntimeError()
def mismatch(request):
    try:
        raise RuntimeError()
    except ValueError:
        return HttpResponse()
""",
    )
    context = _context(tmp_path)
    adapter = DjangoLifecycleAdapter()
    routes = adapter.entrypoints(
        context,
        (
            _syntax(
                tmp_path,
                "project/views.py",
                _function("outside", 1),
                _function("mismatch", 7),
            ),
        ),
    )

    for route in routes:
        roles = {segment.framework_role for segment in adapter.pipeline(context, route)}
        assert "unhandled_exception" in roles
        assert "handled_exception" not in roles


def test_dynamic_raised_value_uses_an_explicit_uncertainty_boundary(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "project/urls.py",
        'from . import views\nurlpatterns = [path("unknown/", views.unknown, name="unknown")]\n',
    )
    _write(tmp_path, "project/views.py", "def unknown(request): raise error\n")
    context = _context(tmp_path)
    adapter = DjangoLifecycleAdapter()
    route = adapter.entrypoints(
        context, (_syntax(tmp_path, "project/views.py", _function("unknown", 1)),)
    )[0]

    assert "unresolved_exception_boundary" in {
        segment.framework_role for segment in adapter.pipeline(context, route)
    }
    assert ("exception_resolution_unresolved", CoverageOutcome.PARTIAL) in {
        (event.reason_code, event.outcome) for event in adapter.coverage_events(context)
    }


def test_nested_helper_return_does_not_create_middleware_short_circuit(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path, middleware='["project.middleware.First"]')
    _write(
        tmp_path,
        "project/urls.py",
        'from . import views\nurlpatterns = [path("safe/", views.safe, name="safe")]\n',
    )
    _write(tmp_path, "project/views.py", "def safe(request): return HttpResponse()\n")
    _write(
        tmp_path,
        "project/middleware.py",
        """class First:
    def process_request(self, request):
        def helper():
            return HttpResponse()
        return None
""",
    )
    context = _context(tmp_path)
    adapter = DjangoLifecycleAdapter()
    route = adapter.entrypoints(
        context, (_syntax(tmp_path, "project/views.py", _function("safe", 1)),)
    )[0]
    request_stage = next(
        segment
        for segment in adapter.pipeline(context, route)
        if segment.framework_role == "middleware_request"
    )

    assert request_stage.short_circuit_successors == ()


def test_non_django_access_decorators_never_create_denial_arm(tmp_path: Path) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "project/urls.py",
        """from . import views
urlpatterns = [
    path("local/", views.local, name="local"),
    path("custom/", views.custom, name="custom"),
    path("external/", views.external, name="external"),
]
""",
    )
    _write(
        tmp_path,
        "project/views.py",
        """def login_required(function): return function
from .decorators import login_required as custom_login_required
from third_party.decorators import login_required as external_login_required
@login_required
def local(request): return HttpResponse()
@custom_login_required
def custom(request): return HttpResponse()
@external_login_required
def external(request): return HttpResponse()
""",
    )
    context = _context(tmp_path)
    adapter = DjangoLifecycleAdapter()
    routes = adapter.entrypoints(
        context,
        (
            _syntax(
                tmp_path,
                "project/views.py",
                _function("login_required", 1),
                _function("local", 5),
                _function("custom", 7),
                _function("external", 9),
            ),
        ),
    )

    for route in routes:
        assert "decorator_access_control" not in {
            segment.framework_role for segment in adapter.pipeline(context, route)
        }


def test_visible_and_external_subclass_exception_arms_are_not_confused(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "project/urls.py",
        """from . import views
urlpatterns = [
    path("local/", views.local_error, name="local"),
    path("external/", views.external_error, name="external"),
]
""",
    )
    _write(
        tmp_path,
        "project/views.py",
        """class CustomError(ValueError): pass
from remote.errors import ExternalError
def local_error(request):
    try:
        raise CustomError()
    except ValueError:
        return HttpResponse()
def external_error(request):
    try:
        raise ExternalError()
    except ValueError:
        return HttpResponse()
""",
    )
    context = _context(tmp_path)
    adapter = DjangoLifecycleAdapter()
    routes = adapter.entrypoints(
        context,
        (
            _syntax(
                tmp_path,
                "project/views.py",
                _class("CustomError", 1),
                _function("local_error", 3),
                _function("external_error", 8),
            ),
        ),
    )
    pipelines = {
        route.public_name: {
            segment.framework_role for segment in adapter.pipeline(context, route)
        }
        for route in routes
    }

    assert "handled_exception" in pipelines["local"]
    assert "unhandled_exception" not in pipelines["local"]
    assert "unresolved_exception_boundary" in pipelines["external"]
    assert "unhandled_exception" not in pipelines["external"]


def test_django_decorator_import_expires_after_visible_rebinding(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "project/urls.py",
        """from . import views
urlpatterns = [
    path("before/", views.before, name="before"),
    path("after/", views.after, name="after"),
]
""",
    )
    _write(
        tmp_path,
        "project/views.py",
        """from django.contrib.auth.decorators import login_required
@login_required
def before(request): return HttpResponse()
login_required = lambda function: function
@login_required
def after(request): return HttpResponse()
""",
    )
    context = _context(tmp_path)
    adapter = DjangoLifecycleAdapter()
    routes = adapter.entrypoints(
        context,
        (
            _syntax(
                tmp_path,
                "project/views.py",
                _function("before", 3),
                _function("after", 6),
            ),
        ),
    )
    pipelines = {
        route.public_name: {
            segment.framework_role for segment in adapter.pipeline(context, route)
        }
        for route in routes
    }

    assert "decorator_access_control" in pipelines["before"]
    assert "decorator_access_control" not in pipelines["after"]


def test_url_import_alias_expires_after_visible_rebinding(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "project/urls.py",
        """from . import views
urlpatterns = [path("before/", views.before, name="before")]
views = replacement
urlpatterns += [path("after/", views.after, name="after")]
""",
    )
    _write(
        tmp_path,
        "project/views.py",
        """def before(request): return HttpResponse()
def after(request): return HttpResponse()
""",
    )
    context = _context(tmp_path)
    adapter = DjangoLifecycleAdapter()
    routes = adapter.entrypoints(
        context,
        (
            _syntax(
                tmp_path,
                "project/views.py",
                _function("before", 1),
                _function("after", 2),
            ),
        ),
    )

    assert [route.public_name for route in routes] == ["before"]
    assert ("route_target_unresolved", CoverageOutcome.PARTIAL) in {
        (event.reason_code, event.outcome) for event in adapter.coverage_events(context)
    }


def test_control_flow_rebindings_invalidate_later_decorator_and_url_uses(
    tmp_path: Path,
) -> None:
    cases = (
        (
            "conditional_assignment",
            "if runtime_flag:\n    login_required = lambda function: function",
            "if runtime_flag:\n    views = replacement",
        ),
        (
            "try_function",
            "try:\n    def login_required(function):\n        return function\nexcept Exception:\n    pass",
            "try:\n    def views():\n        pass\nexcept Exception:\n    pass",
        ),
        (
            "loop_class",
            "for candidate in candidates:\n    class login_required:\n        pass",
            "for candidate in candidates:\n    class views:\n        pass",
        ),
        (
            "match_assignment",
            "match selector:\n    case _:\n        login_required = lambda function: function",
            "match selector:\n    case _:\n        views = replacement",
        ),
    )
    for name, decorator_rebind, url_rebind in cases:
        decorator_root = tmp_path / name / "decorator"
        _prepare_project(decorator_root)
        _write(
            decorator_root,
            "project/urls.py",
            """from . import views
urlpatterns = [
    path("before/", views.before, name="before"),
    path("after/", views.after, name="after"),
]
""",
        )
        _write(
            decorator_root,
            "project/views.py",
            f"""from django.contrib.auth.decorators import login_required
@login_required
def before(request): return HttpResponse()
{decorator_rebind}
@login_required
def after(request): return HttpResponse()
""",
        )
        context = _context(decorator_root)
        adapter = DjangoLifecycleAdapter()
        routes = adapter.entrypoints(
            context,
            (
                _syntax(
                    decorator_root,
                    "project/views.py",
                    _function("before", 3),
                    _function("after", 9),
                ),
            ),
        )
        pipelines = {
            route.public_name: {
                segment.framework_role for segment in adapter.pipeline(context, route)
            }
            for route in routes
        }

        assert "decorator_access_control" in pipelines["before"], name
        assert "decorator_access_control" not in pipelines["after"], name
        url_root = tmp_path / name / "url"
        _prepare_project(url_root)
        _write(
            url_root,
            "project/urls.py",
            f"""from . import views
urlpatterns = [path("before/", views.before, name="before")]
{url_rebind}
urlpatterns += [path("after/", views.after, name="after")]
""",
        )
        _write(
            url_root,
            "project/views.py",
            """def before(request): return HttpResponse()
def after(request): return HttpResponse()
""",
        )
        url_context = _context(url_root)
        url_adapter = DjangoLifecycleAdapter()
        url_routes = url_adapter.entrypoints(
            url_context,
            (
                _syntax(
                    url_root,
                    "project/views.py",
                    _function("before", 1),
                    _function("after", 2),
                ),
            ),
        )

        assert [route.public_name for route in url_routes] == ["before"], name
        assert ("route_target_unresolved", CoverageOutcome.PARTIAL) in {
            (event.reason_code, event.outcome)
            for event in url_adapter.coverage_events(url_context)
        }, name


def test_urlpatterns_assignment_replaces_previous_static_routes(tmp_path: Path) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "project/urls.py",
        """from . import views
urlpatterns = [path("obsolete/", views.obsolete, name="obsolete")]
urlpatterns = [path("live/", views.live, name="live")]
""",
    )
    _write(
        tmp_path,
        "project/views.py",
        """def obsolete(request): return HttpResponse()
def live(request): return HttpResponse()
""",
    )
    adapter = DjangoLifecycleAdapter()
    routes = adapter.entrypoints(
        _context(tmp_path),
        (
            _syntax(
                tmp_path,
                "project/views.py",
                _function("obsolete", 1),
                _function("live", 2),
            ),
        ),
    )

    assert [(route.public_path, route.public_name) for route in routes] == [
        ("/live/", "live")
    ]


def test_urlpatterns_augmented_assignment_extends_static_routes(tmp_path: Path) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "project/urls.py",
        """from . import views
urlpatterns = [path("first/", views.first, name="first")]
urlpatterns += [path("second/", views.second, name="second")]
""",
    )
    _write(
        tmp_path,
        "project/views.py",
        """def first(request): return HttpResponse()
def second(request): return HttpResponse()
""",
    )
    adapter = DjangoLifecycleAdapter()
    routes = adapter.entrypoints(
        _context(tmp_path),
        (
            _syntax(
                tmp_path,
                "project/views.py",
                _function("first", 1),
                _function("second", 2),
            ),
        ),
    )

    assert [(route.public_path, route.public_name) for route in routes] == [
        ("/first/", "first"),
        ("/second/", "second"),
    ]


def test_uncertain_urlpatterns_assignments_do_not_retain_static_routes(
    tmp_path: Path,
) -> None:
    assignments = (
        (
            "conditional",
            'if runtime_flag:\n    urlpatterns = [path("conditional/", views.conditional, name="conditional")]',
        ),
        ("computed", "urlpatterns = build_routes()"),
    )
    for name, assignment in assignments:
        root = tmp_path / name
        _prepare_project(root)
        _write(
            root,
            "project/urls.py",
            f"""from . import views
urlpatterns = [path("stale/", views.stale, name="stale")]
{assignment}
""",
        )
        _write(root, "project/views.py", "def stale(request): return HttpResponse()\n")
        adapter = DjangoLifecycleAdapter()
        routes = adapter.entrypoints(
            _context(root),
            (_syntax(root, "project/views.py", _function("stale", 1)),),
        )

        assert routes == (), name
        assert ("urlpatterns_unresolved", CoverageOutcome.PARTIAL) in {
            (event.reason_code, event.outcome)
            for event in adapter.coverage_events(_context(root))
        }, name


def test_urlpatterns_import_rebindings_do_not_retain_static_routes(
    tmp_path: Path,
) -> None:
    imports = (
        ("aliased", "import replacement as urlpatterns"),
        ("from_import", "from replacement import urlpatterns"),
        (
            "conditional",
            "if runtime_flag:\n    import replacement as urlpatterns",
        ),
    )
    for name, import_statement in imports:
        root = tmp_path / name
        _prepare_project(root)
        _write(
            root,
            "project/urls.py",
            f"""from . import views
urlpatterns = [path("stale/", views.stale, name="stale")]
{import_statement}
""",
        )
        _write(root, "project/views.py", "def stale(request): return HttpResponse()\n")
        context = _context(root)
        adapter = DjangoLifecycleAdapter()
        routes = adapter.entrypoints(
            context,
            (_syntax(root, "project/views.py", _function("stale", 1)),),
        )

        assert routes == (), name
        assert ("urlpatterns_unresolved", CoverageOutcome.PARTIAL) in {
            (event.reason_code, event.outcome)
            for event in adapter.coverage_events(context)
        }, name


def test_unrelated_import_does_not_invalidate_static_urlpatterns(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "project/urls.py",
        """from . import views
import replacement
urlpatterns = [path("live/", views.live, name="live")]
""",
    )
    _write(tmp_path, "project/views.py", "def live(request): return HttpResponse()\n")
    adapter = DjangoLifecycleAdapter()
    routes = adapter.entrypoints(
        _context(tmp_path),
        (_syntax(tmp_path, "project/views.py", _function("live", 1)),),
    )

    assert [(route.public_path, route.public_name) for route in routes] == [
        ("/live/", "live")
    ]


def test_urlpatterns_literal_extend_and_append_preserve_route_order(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "project/urls.py",
        """from . import views
urlpatterns = [path("first/", views.first, name="first")]
urlpatterns.extend([path("second/", views.second, name="second")])
urlpatterns.append(path("third/", views.third, name="third"))
""",
    )
    _write(
        tmp_path,
        "project/views.py",
        """def first(request): return HttpResponse()
def second(request): return HttpResponse()
def third(request): return HttpResponse()
""",
    )
    adapter = DjangoLifecycleAdapter()
    routes = adapter.entrypoints(
        _context(tmp_path),
        (
            _syntax(
                tmp_path,
                "project/views.py",
                _function("first", 1),
                _function("second", 2),
                _function("third", 3),
            ),
        ),
    )

    assert [(route.public_path, route.public_name) for route in routes] == [
        ("/first/", "first"),
        ("/second/", "second"),
        ("/third/", "third"),
    ]


def test_computed_urlpatterns_mutation_discards_static_routes(tmp_path: Path) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "project/urls.py",
        """from . import views
urlpatterns = [path("stale/", views.stale, name="stale")]
urlpatterns.extend(build_routes())
""",
    )
    _write(tmp_path, "project/views.py", "def stale(request): return HttpResponse()\n")
    context = _context(tmp_path)
    adapter = DjangoLifecycleAdapter()
    routes = adapter.entrypoints(
        context,
        (_syntax(tmp_path, "project/views.py", _function("stale", 1)),),
    )

    assert routes == ()
    assert ("urlpatterns_unresolved", CoverageOutcome.PARTIAL) in {
        (event.reason_code, event.outcome) for event in adapter.coverage_events(context)
    }


def test_unrelated_list_mutation_does_not_invalidate_urlpatterns(
    tmp_path: Path,
) -> None:
    _prepare_project(tmp_path)
    _write(
        tmp_path,
        "project/urls.py",
        """from . import views
urlpatterns = [path("live/", views.live, name="live")]
other.extend(build_routes())
""",
    )
    _write(tmp_path, "project/views.py", "def live(request): return HttpResponse()\n")
    adapter = DjangoLifecycleAdapter()
    routes = adapter.entrypoints(
        _context(tmp_path),
        (_syntax(tmp_path, "project/views.py", _function("live", 1)),),
    )

    assert [(route.public_path, route.public_name) for route in routes] == [
        ("/live/", "live")
    ]
