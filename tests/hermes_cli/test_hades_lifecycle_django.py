"""Golden behaviour for static, bounded Django lifecycle extraction.

The fixtures deliberately model configuration and source text only.  They do
not import Django or execute a project: every positive fact has to be visible
through the scoped ``ExtractionContext.file_accessor``.
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
from hermes_cli.hades_index.lifecycle.frameworks.django import DjangoLifecycleAdapter
from hermes_cli.hades_index.lifecycle.model import (
    ConfigLocatorIR,
    CoverageOutcome,
    ExtractionContext,
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
    )


def _syntax(root: Path, path: str, *symbols: str) -> SyntaxIR:
    return SyntaxIR(
        ParsedFile(
            path=path,
            language="python",
            symbols=tuple(
                StructuralSymbol(name, "function", index + 1, index + 1)
                for index, name in enumerate(symbols)
            ),
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
urlpatterns = [
    path("api/<int:tenant_id>/", include(("project.api.urls", "api"), namespace="v1")),
    re_path(r"^legacy/(?P<slug>[-\\w]+)/$", views.legacy, name="legacy"),
]
""",
    )
    _write(
        tmp_path,
        "project/api/urls.py",
        'urlpatterns = [path("items/", views.items, name="items")]\n',
    )
    adapter = DjangoLifecycleAdapter()
    routes = adapter.entrypoints(
        _context(tmp_path),
        (
            _syntax(tmp_path, "project/views.py", "views.items", "views.legacy"),
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
        'urlpatterns = [path("secure/", views.secure, name="secure")]\n',
    )
    _write(
        tmp_path,
        "project/views.py",
        """@login_required
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
        (_syntax(tmp_path, "project/views.py", "views.secure"),),
    )[0]
    pipeline = adapter.pipeline(context, candidate)

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
        """urlpatterns = [
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
                "views.SyncView.dispatch",
                "views.SyncView.get",
                "views.SyncView.post",
                "views.async_view",
                "views.handled",
                "views.broken",
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
                "Command.handle",
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
            context, (_syntax(tmp_path, "project/views.py", "views.users"),)
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
            context, (_syntax(tmp_path, "project/views.py", "views.users"),)
        )
        == ()
    )
    assert ("url_pattern_unresolved", CoverageOutcome.PARTIAL) in {
        (event.reason_code, event.outcome) for event in adapter.coverage_events(context)
    }
