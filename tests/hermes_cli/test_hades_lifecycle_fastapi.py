"""Golden behaviour for static, bounded FastAPI lifecycle extraction.

The fixtures deliberately contain source text only.  The adapter must never
import an application or evaluate application configuration: every lifecycle
fact asserted here is visible through ``ExtractionContext.file_accessor``.
"""

from __future__ import annotations

import ast
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
from hermes_cli.hades_index.lifecycle.frameworks import (
    FrameworkAdapterRegistry,
    run_framework_adapters,
)
from hermes_cli.hades_index.lifecycle.frameworks.fastapi import FastAPILifecycleAdapter
from hermes_cli.hades_index.lifecycle.model import (
    AsyncSuccessor,
    ConfigLocatorIR,
    CoverageOutcome,
    ExceptionSuccessor,
    ExtractionContext,
    FrameworkLocalTarget,
    ReturnSuccessor,
    SourceLocationIR,
    local_record_key,
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
    root: Path,
    *,
    file_accessor: Callable[[Path], bytes] | None = None,
    fastapi_version: str = "0.115.0",
    starlette_version: str | None = "0.37.2",
) -> ExtractionContext:
    metadata = _location(root, "pyproject.toml")
    frameworks = [
        FrameworkRecord(
            language="python",
            name="fastapi",
            version=fastapi_version,
            detector="pyproject",
            configuration_paths=("pyproject.toml",),
            knowledge=FrameworkKnowledge.VERIFIED,
        )
    ]
    if starlette_version is not None:
        frameworks.append(
            FrameworkRecord(
                language="python",
                name="starlette",
                version=starlette_version,
                detector="pyproject",
                configuration_paths=("pyproject.toml",),
                knowledge=FrameworkKnowledge.VERIFIED,
            )
        )
    return ExtractionContext(
        workspace_root=root,
        project_id="project",
        workspace_binding_id="binding",
        source_identity=SourceIdentity(None, "a" * 64, False, None),
        graph_config=load_hades_graph_index_config({}),
        detected_languages=("python",),
        detected_frameworks=tuple(frameworks),
        composer_metadata=(),
        python_metadata=(ConfigLocatorIR(metadata, "pyproject", 0),),
        package_metadata=(),
        tsconfig_metadata=(),
        file_accessor=file_accessor or (lambda path: (root / path).read_bytes()),
    )


def _syntax(root: Path, path: str) -> SyntaxIR:
    source = (root / path).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=path)
    symbols: list[StructuralSymbol] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(
                StructuralSymbol(
                    node.name,
                    "function",
                    node.lineno,
                    getattr(node, "end_lineno", node.lineno),
                )
            )
        elif isinstance(node, ast.ClassDef):
            symbols.append(
                StructuralSymbol(
                    node.name,
                    "class",
                    node.lineno,
                    getattr(node, "end_lineno", node.lineno),
                )
            )
    return SyntaxIR(ParsedFile(path, "python", tuple(symbols), (), ()), ())


def _prepare(root: Path) -> None:
    _write(
        root,
        "pyproject.toml",
        """[project]
dependencies = ["fastapi==0.115.0", "starlette==0.37.2"]
""",
    )


def _candidate(adapter: FastAPILifecycleAdapter, context: ExtractionContext, name: str):
    return next(
        item
        for item in adapter.entrypoints(
            context, (_syntax(context.workspace_root, "app.py"),)
        )
        if item.public_name == name and item.kind is EntrypointKind.HTTP_ROUTE
    )


def _function_key(root: Path, name: str) -> str:
    return _function_key_at(root, "app.py", name)


def _function_key_at(root: Path, path: str, name: str) -> str:
    syntax = _syntax(root, path)
    return next(
        local_record_key(
            "python",
            syntax.path,
            "executable_declaration",
            "ast",
            f"symbol/{symbol.name}",
            ordinal,
        )
        for ordinal, symbol in enumerate(syntax.symbols)
        if symbol.kind == "function" and symbol.name == name
    )


def test_nested_routers_methods_dependencies_cache_cleanup_and_background_child(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from contextlib import asynccontextmanager
from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI

class AuthError(Exception): pass
class Out: pass

def app_dep(): pass
def router_dep(): pass
def route_dep():
    raise AuthError()
def shared(): pass
def yield_dep():
    yield "resource"
def notify(): pass

@asynccontextmanager
async def lifespan(app):
    yield

app = FastAPI(dependencies=[Depends(app_dep)], lifespan=lifespan)
parent = APIRouter(prefix="/api", dependencies=[Depends(router_dep), Depends(shared)])
child = APIRouter(prefix="/v1", dependencies=[Depends(shared)])

@app.middleware("http")
async def first(request, call_next):
    return await call_next(request)

@app.middleware("http")
async def second(request, call_next):
    return await call_next(request)

@app.exception_handler(AuthError)
async def auth_error(request, exc):
    return None

@app.on_event("startup")
async def startup(): pass

@app.on_event("shutdown")
async def shutdown(): pass

@child.api_route(
    "/items/{item_id}", methods=["POST", "GET"],
    dependencies=[Depends(shared), Depends(yield_dep)], response_model=Out,
)
async def items(item_id: int, background_tasks: BackgroundTasks):
    background_tasks.add_task(notify)
    return Out()

parent.include_router(child, prefix="/nested", dependencies=[Depends(route_dep)])
app.include_router(parent)
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    route = _candidate(adapter, context, "items")
    pipeline = adapter.pipeline(context, route)
    roles = [segment.framework_role for segment in pipeline]

    assert adapter.detect(context).detected is True
    assert route.public_path == "/api/nested/v1/items/{item_id}"
    assert route.methods == ("GET", "POST")
    assert route.method_semantics is MethodSemantics.EXPLICIT
    assert roles.index("app_dependency") < roles.index("router_dependency")
    assert roles.index("router_dependency") < roles.index("route_dependency")
    assert roles.index("route_dependency") < roles.index("decorator_dependency")
    assert "dependency_cache_reuse" in roles
    assert "request_validation" in roles
    assert "async_handler" in roles
    assert "response_model_serialization" in roles
    assert "yield_dependency_cleanup" in roles
    assert "background_task_dispatch" in roles
    assert [
        segment.target.local_key
        for segment in pipeline
        if segment.framework_role == "middleware_request"
        and isinstance(segment.target, FrameworkLocalTarget)
    ] == [_function_key(tmp_path, "second"), _function_key(tmp_path, "first")]

    validation = next(
        item for item in pipeline if item.framework_role == "request_validation"
    )
    assert any(
        isinstance(successor, ReturnSuccessor)
        for successor in validation.short_circuit_successors
    )
    dependency = next(
        item for item in pipeline if item.framework_role == "route_dependency"
    )
    assert any(
        isinstance(successor, ExceptionSuccessor)
        for successor in dependency.short_circuit_successors
    )
    background = next(
        item for item in pipeline if item.framework_role == "background_task_dispatch"
    )
    assert any(
        isinstance(successor, AsyncSuccessor)
        for successor in background.short_circuit_successors
    )
    event_names = {
        item.public_name
        for item in adapter.entrypoints(context, (_syntax(tmp_path, "app.py"),))
        if item.kind in {EntrypointKind.EVENT_LISTENER, EntrypointKind.PROCESS_MAIN}
    }
    assert event_names == {"app_lifespan"}


def test_sync_handler_and_exception_arm_are_explicit(tmp_path: Path) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from fastapi import FastAPI

class Problem(Exception): pass
app = FastAPI()

@app.exception_handler(Problem)
def problem_handler(request, exc): return None

@app.get("/sync")
def sync_endpoint():
    raise Problem()
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    pipeline = adapter.pipeline(context, _candidate(adapter, context, "sync_endpoint"))
    roles = [segment.framework_role for segment in pipeline]

    assert "sync_handler" in roles
    assert "exception_handler" in roles
    handler = next(item for item in pipeline if item.framework_role == "sync_handler")
    assert any(
        isinstance(successor, ExceptionSuccessor)
        for successor in handler.short_circuit_successors
    )


def test_unknown_starlette_order_is_a_partial_boundary_not_a_guess(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path, "pyproject.toml", '[project]\ndependencies = ["fastapi==0.115.0"]\n'
    )
    _write(
        tmp_path,
        "app.py",
        """from fastapi import FastAPI
app = FastAPI()
@app.middleware("http")
async def middleware(request, call_next): return await call_next(request)
@app.get("/health")
async def health(): return {"ok": True}
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path, starlette_version=None)
    route = _candidate(adapter, context, "health")
    pipeline = adapter.pipeline(context, route)

    assert "middleware_order_boundary" in {
        segment.framework_role for segment in pipeline
    }
    assert "middleware" not in {segment.framework_role for segment in pipeline}
    assert any(
        event.outcome is CoverageOutcome.PARTIAL
        and event.reason_code == "middleware_order_unresolved"
        for event in adapter.coverage_events(context)
    )


def test_computed_router_configuration_is_reported_partial_and_never_guessed(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from fastapi import APIRouter, FastAPI
app = FastAPI()
router = APIRouter(prefix=prefix_from_environment())
@router.get(dynamic_path())
async def hidden(): return None
app.include_router(router)
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)

    assert adapter.entrypoints(context, (_syntax(tmp_path, "app.py"),)) == ()
    assert any(
        event.outcome is CoverageOutcome.PARTIAL
        and event.reason_code == "framework_config_unresolved"
        for event in adapter.coverage_events(context)
    )


def test_unmatched_exception_and_input_free_handler_do_not_gain_guessed_arms(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from fastapi import FastAPI

class Other(Exception): pass
class Problem(Exception): pass
app = FastAPI()

@app.exception_handler(Other)
async def other_handler(request, exc): return None

@app.get("/ready")
async def ready():
    raise Problem()
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    pipeline = adapter.pipeline(context, _candidate(adapter, context, "ready"))
    roles = [segment.framework_role for segment in pipeline]

    assert "request_validation" not in roles
    assert "exception_handler" not in roles
    assert "unhandled_exception" in roles


def test_imported_router_and_dependency_cache_use_resolved_callable_identity(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "common.py",
        """def shared(): pass
""",
    )
    _write(
        tmp_path,
        "routers.py",
        """from fastapi import APIRouter, Depends
from common import shared

router = APIRouter(prefix="/router", dependencies=[Depends(shared)])

@router.get("/ping")
async def ping(): return {"ok": True}
""",
    )
    _write(
        tmp_path,
        "app.py",
        """from fastapi import Depends, FastAPI
from common import shared
from routers import router

app = FastAPI(dependencies=[Depends(shared)])
app.include_router(router, prefix="/api")
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    candidates = adapter.entrypoints(
        context,
        (
            _syntax(tmp_path, "app.py"),
            _syntax(tmp_path, "common.py"),
            _syntax(tmp_path, "routers.py"),
        ),
    )
    route = next(item for item in candidates if item.public_name == "ping")
    pipeline = adapter.pipeline(context, route)

    assert route.public_path == "/api/router/ping"
    app_dependency = next(
        item for item in pipeline if item.framework_role == "app_dependency"
    )
    assert isinstance(app_dependency.target, FrameworkLocalTarget)
    assert app_dependency.target.local_key == _function_key_at(
        tmp_path, "common.py", "shared"
    )
    assert "dependency_cache_reuse" in {item.framework_role for item in pipeline}


def test_rebound_application_object_is_partial_not_an_old_registration(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from fastapi import FastAPI

app = FastAPI()
@app.get("/before-rebind")
async def stale(): return None
app = build_app()
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)

    assert adapter.entrypoints(context, (_syntax(tmp_path, "app.py"),)) == ()
    assert any(
        event.outcome is CoverageOutcome.PARTIAL
        and event.reason_code == "framework_object_rebound"
        for event in adapter.coverage_events(context)
    )


def test_repeated_router_inclusion_has_distinct_registry_safe_pipeline_identity(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from fastapi import APIRouter, FastAPI

app = FastAPI()
router = APIRouter()

@router.get("/health")
async def health(): return {"ok": True}

app.include_router(router, prefix="/v1")
app.include_router(router, prefix="/v2")
""",
    )
    context = _context(tmp_path)
    syntax = (_syntax(tmp_path, "app.py"),)
    registry = FrameworkAdapterRegistry()
    registry.register(FastAPILifecycleAdapter())

    result = run_framework_adapters(registry, context, syntax)
    routes = tuple(
        item for item in result.candidates if item.kind is EntrypointKind.HTTP_ROUTE
    )

    assert {item.public_path for item in routes} == {"/v1/health", "/v2/health"}
    assert len({item.registration_locator.structural_path for item in routes}) == 2
    assert len(result.framework_segments) == len({
        item.local_key for item in result.framework_segments
    })


def test_apirouter_absent_prefix_is_the_static_empty_prefix(tmp_path: Path) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from fastapi import APIRouter, FastAPI

app = FastAPI()
router = APIRouter()

@router.get("/health")
async def health(): return {"ok": True}

app.include_router(router, prefix="/v1")
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)

    assert _candidate(adapter, context, "health").public_path == "/v1/health"
    assert not any(
        event.reason_code == "framework_config_unresolved"
        for event in adapter.coverage_events(context)
    )


def test_reviewed_route_method_defaults_and_signatures_are_exact(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from fastapi import APIRouter, FastAPI

app = FastAPI()
router = APIRouter()

@router.api_route("/api-default")
async def api_default(): return None

@router.route("/starlette-post", methods=["POST"])
async def starlette_post(request): return None

@router.route("/starlette-default")
async def starlette_default(request): return None

async def direct(): return None
router.add_api_route("/direct", direct)
app.include_router(router)
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    routes = {
        item.public_path: item
        for item in adapter.entrypoints(context, (_syntax(tmp_path, "app.py"),))
        if item.kind is EntrypointKind.HTTP_ROUTE
    }

    assert routes["/api-default"].methods == ("GET",)
    assert routes["/direct"].methods == ("GET",)
    assert routes["/starlette-post"].methods == ("POST",)
    assert routes["/starlette-default"].methods == ("GET", "HEAD")
    assert all(
        item.method_semantics is MethodSemantics.EXPLICIT for item in routes.values()
    )


def test_unreviewed_route_method_contracts_are_partial_and_not_invented(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from fastapi import APIRouter, FastAPI

app = FastAPI()
router = APIRouter()

@router.api_route("/fastapi-unknown")
async def fastapi_unknown(): return None

@router.route("/starlette-unknown")
async def starlette_unknown(request): return None

app.include_router(router)
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(
        tmp_path,
        fastapi_version="9.99.0",
        starlette_version="9.99.0",
    )

    assert adapter.entrypoints(context, (_syntax(tmp_path, "app.py"),)) == ()
    assert any(
        event.outcome is CoverageOutcome.PARTIAL
        and event.reason_code == "route_method_contract_unresolved"
        for event in adapter.coverage_events(context)
    )


def test_exception_handlers_use_proven_exact_type_and_mro_specificity(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from fastapi import FastAPI

class ParentProblem(Exception): pass
class ExactProblem(ParentProblem): pass
class ChildProblem(ParentProblem): pass

app = FastAPI()

@app.exception_handler(Exception)
async def generic(request, exc): return None

@app.exception_handler(ParentProblem)
async def parent(request, exc): return None

@app.exception_handler(ExactProblem)
async def specific(request, exc): return None

@app.get("/exact")
async def exact_endpoint(): raise ExactProblem()

@app.get("/inherited")
async def inherited_endpoint(): raise ChildProblem()
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)

    exact_pipeline = adapter.pipeline(
        context, _candidate(adapter, context, "exact_endpoint")
    )
    inherited_pipeline = adapter.pipeline(
        context, _candidate(adapter, context, "inherited_endpoint")
    )
    exact_handler = next(
        item for item in exact_pipeline if item.framework_role == "exception_handler"
    )
    inherited_handler = next(
        item
        for item in inherited_pipeline
        if item.framework_role == "exception_handler"
    )

    assert isinstance(exact_handler.target, FrameworkLocalTarget)
    assert exact_handler.target.local_key == _function_key(tmp_path, "specific")
    assert isinstance(inherited_handler.target, FrameworkLocalTarget)
    assert inherited_handler.target.local_key == _function_key(tmp_path, "parent")


def test_exception_handler_identity_never_matches_only_a_short_suffix(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(tmp_path, "alpha.py", "class Problem(Exception): pass\n")
    _write(tmp_path, "beta.py", "class Problem(Exception): pass\n")
    _write(
        tmp_path,
        "app.py",
        """import alpha
import beta
from fastapi import FastAPI

app = FastAPI()

@app.exception_handler(alpha.Problem)
async def alpha_handler(request, exc): return None

@app.exception_handler(Exception)
async def generic(request, exc): return None

@app.get("/beta")
async def beta_endpoint(): raise beta.Problem()
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    candidates = adapter.entrypoints(
        context,
        (
            _syntax(tmp_path, "alpha.py"),
            _syntax(tmp_path, "app.py"),
            _syntax(tmp_path, "beta.py"),
        ),
    )
    route = next(item for item in candidates if item.public_name == "beta_endpoint")
    handler = next(
        item
        for item in adapter.pipeline(context, route)
        if item.framework_role == "exception_handler"
    )

    assert isinstance(handler.target, FrameworkLocalTarget)
    assert handler.target.local_key == _function_key(tmp_path, "generic")


def test_unproven_exception_ancestry_is_a_boundary_not_a_guessed_handler(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """import opaque
from fastapi import FastAPI

app = FastAPI()

@app.exception_handler(Exception)
async def generic(request, exc): return None

@app.get("/opaque")
async def opaque_endpoint(): raise opaque.Problem()
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    pipeline = adapter.pipeline(
        context, _candidate(adapter, context, "opaque_endpoint")
    )
    roles = {item.framework_role for item in pipeline}

    assert "exception_handler" not in roles
    assert "exception_handler_resolution_boundary" in roles


def test_return_annotation_drives_serialization_unless_explicitly_opted_out(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from fastapi import FastAPI

class Out: pass
def model_factory(): return Out
app = FastAPI()

@app.get("/annotated")
async def annotated() -> Out: return Out()

@app.get("/opted-out", response_model=None)
async def opted_out() -> Out: return Out()

@app.get("/computed")
async def computed() -> model_factory(): return Out()
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)

    annotated = {
        item.framework_role
        for item in adapter.pipeline(context, _candidate(adapter, context, "annotated"))
    }
    opted_out = {
        item.framework_role
        for item in adapter.pipeline(context, _candidate(adapter, context, "opted_out"))
    }
    computed = {
        item.framework_role
        for item in adapter.pipeline(context, _candidate(adapter, context, "computed"))
    }

    assert "response_model_serialization" in annotated
    assert "response_model_serialization" not in opted_out
    assert "response_model_resolution_boundary" in computed


def test_dynamic_control_flow_registration_is_partial_without_invented_route(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from fastapi import APIRouter, FastAPI

app = FastAPI()
router = APIRouter()

@router.get("/hidden")
async def hidden(): return None

if enabled:
    app.include_router(router)
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)

    assert adapter.entrypoints(context, (_syntax(tmp_path, "app.py"),)) == ()
    assert any(
        event.outcome is CoverageOutcome.PARTIAL
        and event.reason_code == "framework_config_unresolved"
        for event in adapter.coverage_events(context)
    )


def test_dynamic_non_framework_call_does_not_create_registration_uncertainty(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from fastapi import FastAPI

app = FastAPI()
settings = {}

if enabled:
    settings.get("feature")

@app.get("/health")
async def health(): return {"ok": True}
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)

    assert _candidate(adapter, context, "health").public_path == "/health"
    assert not any(
        event.reason_code == "framework_config_unresolved"
        for event in adapter.coverage_events(context)
    )


def test_literal_none_lifespan_preserves_routes_and_legacy_events(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from fastapi import FastAPI

def build_lifespan(): return None

none_app = FastAPI(lifespan=None)

@none_app.on_event("startup")
async def startup(): pass

@none_app.get("/exact")
async def exact(): return None

dynamic_app = FastAPI(lifespan=build_lifespan())

@dynamic_app.get("/dynamic")
async def dynamic(): return None
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    entrypoints = adapter.entrypoints(context, (_syntax(tmp_path, "app.py"),))

    assert {(item.kind, item.public_name) for item in entrypoints} == {
        (EntrypointKind.HTTP_ROUTE, "exact"),
        (EntrypointKind.EVENT_LISTENER, "startup"),
    }
    assert any(
        event.reason_code == "framework_config_unresolved"
        and event.outcome is CoverageOutcome.PARTIAL
        for event in adapter.coverage_events(context)
    )


def test_plain_starlette_routes_exclude_fastapi_only_pipeline_stages(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from fastapi import APIRouter, Depends, FastAPI

class Out: pass
def dep(): pass

app = FastAPI()
router = APIRouter()

@router.route("/decorated")
async def decorated(request: str, ignored=Depends(dep)) -> Out: return Out()

async def direct(request: str, ignored=Depends(dep)) -> Out: return Out()
router.add_route("/direct", direct, methods=["POST"])
app.include_router(router)
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    forbidden = {
        "app_dependency",
        "decorator_dependency",
        "dependency_cache_reuse",
        "request_validation",
        "response_model_resolution_boundary",
        "response_model_serialization",
        "route_dependency",
        "router_dependency",
    }

    for name in ("decorated", "direct"):
        roles = {
            item.framework_role
            for item in adapter.pipeline(context, _candidate(adapter, context, name))
        }
        assert roles.isdisjoint(forbidden)


def test_exception_aliases_require_proven_local_class_identity(tmp_path: Path) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from fastapi import FastAPI

class Problem(Exception): pass
Alias = Problem
ComputedAlias = choose_type()

exact_app = FastAPI()

@exact_app.exception_handler(Alias)
async def alias_handler(request, exc): return None

@exact_app.get("/exact-alias")
async def exact_alias_endpoint(): raise Problem()

uncertain_app = FastAPI()

@uncertain_app.exception_handler(Exception)
async def uncertain_generic(request, exc): return None

@uncertain_app.exception_handler(ComputedAlias)
async def uncertain_handler(request, exc): return None

@uncertain_app.get("/uncertain-alias")
async def uncertain_alias_endpoint(): raise Problem()
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    exact = adapter.pipeline(
        context, _candidate(adapter, context, "exact_alias_endpoint")
    )
    uncertain = adapter.pipeline(
        context, _candidate(adapter, context, "uncertain_alias_endpoint")
    )

    handler = next(item for item in exact if item.framework_role == "exception_handler")
    assert isinstance(handler.target, FrameworkLocalTarget)
    assert handler.target.local_key == _function_key(tmp_path, "alias_handler")
    assert "exception_handler_resolution_boundary" in {
        item.framework_role for item in uncertain
    }


def test_add_api_route_merges_registration_and_endpoint_dependencies(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from fastapi import APIRouter, Depends, FastAPI, Security

def registration_dep(): pass
def parameter_dep(): pass
def security_dep(): pass

async def direct(
    first=Depends(parameter_dep),
    second=Security(security_dep),
): return None

app = FastAPI()
router = APIRouter()
router.add_api_route(
    "/direct", direct, dependencies=[Depends(registration_dep)],
)
app.include_router(router)
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    pipeline = adapter.pipeline(context, _candidate(adapter, context, "direct"))

    assert [
        item.target.local_key
        for item in pipeline
        if item.framework_role == "decorator_dependency"
        and isinstance(item.target, FrameworkLocalTarget)
    ] == [
        _function_key(tmp_path, "registration_dep"),
        _function_key(tmp_path, "parameter_dep"),
        _function_key(tmp_path, "security_dep"),
    ]


def test_quoted_return_annotations_are_parsed_before_serialization_claims(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from fastapi import FastAPI

class Out: pass
def model_factory(): return Out
app = FastAPI()

@app.get("/safe")
async def safe() -> "list[Out]": return [Out()]

@app.get("/computed")
async def computed() -> "model_factory()": return Out()

@app.get("/unparseable")
async def unparseable() -> "Out[": return Out()
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)

    safe_roles = {
        item.framework_role
        for item in adapter.pipeline(context, _candidate(adapter, context, "safe"))
    }
    assert "response_model_serialization" in safe_roles
    for name in ("computed", "unparseable"):
        roles = {
            item.framework_role
            for item in adapter.pipeline(context, _candidate(adapter, context, name))
        }
        assert "response_model_serialization" not in roles
        assert "response_model_resolution_boundary" in roles


def test_dynamic_expression_registrations_are_partial_without_invented_routes(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from fastapi import APIRouter, FastAPI

app = FastAPI()
router = APIRouter()

@router.get("/hidden")
async def hidden(): return None

enabled and app.include_router(router)
app.include_router(router) if alternate else None
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)

    assert adapter.entrypoints(context, (_syntax(tmp_path, "app.py"),)) == ()
    assert any(
        event.reason_code == "framework_config_unresolved"
        and event.outcome is CoverageOutcome.PARTIAL
        for event in adapter.coverage_events(context)
    )


def test_dynamic_expression_rebinding_invalidates_stale_framework_identity(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from fastapi import FastAPI

app = FastAPI()

@app.get("/stale")
async def stale(): return None

enabled and (app := build_app())
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)

    assert adapter.entrypoints(context, (_syntax(tmp_path, "app.py"),)) == ()
    assert any(
        event.reason_code == "framework_object_rebound"
        and event.outcome is CoverageOutcome.PARTIAL
        for event in adapter.coverage_events(context)
    )


def test_route_contract_gates_are_exact_reviewed_patch_versions(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from fastapi import APIRouter, FastAPI

app = FastAPI()
router = APIRouter()

@router.api_route("/fastapi-sibling")
async def fastapi_sibling(): return None

@router.route("/starlette-sibling")
async def starlette_sibling(request): return None

app.include_router(router)
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(
        tmp_path,
        fastapi_version="0.115.1",
        starlette_version="0.37.3",
    )

    assert adapter.entrypoints(context, (_syntax(tmp_path, "app.py"),)) == ()
    assert any(
        event.reason_code == "route_method_contract_unresolved"
        and event.outcome is CoverageOutcome.PARTIAL
        for event in adapter.coverage_events(context)
    )


def test_exception_alias_occurrence_preserves_registration_and_raise_identity(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from fastapi import FastAPI

class A(Exception): pass
class B(Exception): pass

Alias = A
app = FastAPI()

@app.exception_handler(Alias)
async def a_handler(request, exc): return None

Alias = B

@app.get("/exact-a")
async def exact_a(): raise A()

@app.get("/alias-b")
async def alias_b(): raise Alias()
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    exact_a = adapter.pipeline(context, _candidate(adapter, context, "exact_a"))
    alias_b = adapter.pipeline(context, _candidate(adapter, context, "alias_b"))

    handler = next(
        item for item in exact_a if item.framework_role == "exception_handler"
    )
    assert isinstance(handler.target, FrameworkLocalTarget)
    assert handler.target.local_key == _function_key(tmp_path, "a_handler")
    assert "exception_handler" not in {item.framework_role for item in alias_b}
    assert "unhandled_exception" in {item.framework_role for item in alias_b}


def test_exception_alias_occurrence_dynamic_state_is_a_boundary(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from fastapi import FastAPI

class A(Exception): pass
class B(Exception): pass

Alias = A
if enabled:
    Alias = B

app = FastAPI()

@app.exception_handler(Alias)
async def uncertain_handler(request, exc): return None

@app.get("/uncertain")
async def uncertain(): raise A()
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    roles = {
        item.framework_role
        for item in adapter.pipeline(context, _candidate(adapter, context, "uncertain"))
    }

    assert "exception_handler" not in roles
    assert "exception_handler_resolution_boundary" in roles


def test_comprehension_namedexpr_rebinds_but_iteration_target_stays_local(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from fastapi import FastAPI

local_app = FastAPI()

@local_app.get("/local")
async def local(): return None

[local_app for local_app in values]

rebound_app = FastAPI()

@rebound_app.get("/stale")
async def stale(): return None

[(rebound_app := build_app()) for item in values]
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    entrypoints = adapter.entrypoints(context, (_syntax(tmp_path, "app.py"),))

    assert {
        item.public_name
        for item in entrypoints
        if item.kind is EntrypointKind.HTTP_ROUTE
    } == {"local"}
    assert any(
        event.reason_code == "framework_object_rebound"
        and event.outcome is CoverageOutcome.PARTIAL
        for event in adapter.coverage_events(context)
    )


def test_imported_app_alias_rebind_invalidates_reference_and_lost_registrations(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "shared.py",
        """from fastapi import FastAPI

app = FastAPI()
""",
    )
    _write(
        tmp_path,
        "app.py",
        """from shared import app

@app.get("/before-rebind")
async def before_rebind(): return None

app = build_app()

@app.get("/after-rebind")
async def after_rebind(): return None
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    entrypoints = adapter.entrypoints(
        context,
        (_syntax(tmp_path, "app.py"), _syntax(tmp_path, "shared.py")),
    )

    assert entrypoints == ()
    assert any(
        event.reason_code == "framework_object_rebound"
        and event.outcome is CoverageOutcome.PARTIAL
        for event in adapter.coverage_events(context)
    )


def test_runtime_exception_alias_lookup_distinguishes_global_and_local_state(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from fastapi import FastAPI

class A(Exception): pass
class B(Exception): pass

Alias = A
app = FastAPI()

@app.exception_handler(A)
async def a_handler(request, exc): return None

@app.exception_handler(B)
async def b_handler(request, exc): return None

@app.get("/global-final")
async def global_final(): raise Alias()

@app.get("/local-exact")
async def local_exact():
    Alias = A
    raise Alias()

@app.get("/local-dynamic")
async def local_dynamic():
    if enabled:
        Alias = A
    raise Alias()

Alias = B
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)

    global_pipeline = adapter.pipeline(
        context, _candidate(adapter, context, "global_final")
    )
    local_pipeline = adapter.pipeline(
        context, _candidate(adapter, context, "local_exact")
    )
    dynamic_roles = {
        item.framework_role
        for item in adapter.pipeline(
            context, _candidate(adapter, context, "local_dynamic")
        )
    }

    global_handler = next(
        item for item in global_pipeline if item.framework_role == "exception_handler"
    )
    local_handler = next(
        item for item in local_pipeline if item.framework_role == "exception_handler"
    )
    assert isinstance(global_handler.target, FrameworkLocalTarget)
    assert global_handler.target.local_key == _function_key(tmp_path, "b_handler")
    assert isinstance(local_handler.target, FrameworkLocalTarget)
    assert local_handler.target.local_key == _function_key(tmp_path, "a_handler")
    assert "exception_handler" not in dynamic_roles
    assert "exception_handler_resolution_boundary" in dynamic_roles


def test_redeclared_exception_classes_have_distinct_runtime_identity(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from fastapi import FastAPI

class Problem(Exception): pass
OldProblem = Problem

app = FastAPI()

@app.exception_handler(OldProblem)
async def old_handler(request, exc): return None

class Problem(Exception): pass

@app.get("/old-problem")
async def old_problem(): raise OldProblem()

@app.get("/new-problem")
async def new_problem(): raise Problem()
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    old_pipeline = adapter.pipeline(
        context, _candidate(adapter, context, "old_problem")
    )
    new_roles = {
        item.framework_role
        for item in adapter.pipeline(
            context, _candidate(adapter, context, "new_problem")
        )
    }

    old_handler = next(
        item for item in old_pipeline if item.framework_role == "exception_handler"
    )
    assert isinstance(old_handler.target, FrameworkLocalTarget)
    assert old_handler.target.local_key == _function_key(tmp_path, "old_handler")
    assert "exception_handler" not in new_roles
    assert "unhandled_exception" in new_roles


def test_framework_import_references_resolve_at_each_occurrence(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "first.py",
        """from fastapi import APIRouter, FastAPI

app = FastAPI()
router = APIRouter()

@router.get("/first")
async def first(): return None
""",
    )
    _write(
        tmp_path,
        "second.py",
        """from fastapi import APIRouter, FastAPI

app = FastAPI()
router = APIRouter()

@router.get("/second")
async def second(): return None
""",
    )
    _write(
        tmp_path,
        "app.py",
        """from first import app as selected_app, router as selected_router

selected_app.include_router(selected_router, prefix="/one")

from second import app as selected_app, router as selected_router

selected_app.include_router(selected_router, prefix="/two")
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    entrypoints = adapter.entrypoints(
        context,
        (
            _syntax(tmp_path, "app.py"),
            _syntax(tmp_path, "first.py"),
            _syntax(tmp_path, "second.py"),
        ),
    )

    assert {
        (item.public_name, item.public_path)
        for item in entrypoints
        if item.kind is EntrypointKind.HTTP_ROUTE
    } == {("first", "/one/first"), ("second", "/two/second")}
    assert not any(
        event.source_path == "app.py"
        and event.reason_code
        in {"framework_config_unresolved", "framework_object_rebound"}
        for event in adapter.coverage_events(context)
    )


def test_module_header_expressions_rebind_framework_objects_but_bodies_do_not(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from fastapi import FastAPI

decorator_app = FastAPI()
@decorator_app.get("/decorator-stale")
async def decorator_stale(): return None
@configure(decorator_app := build_app())
def decorated(): pass

default_app = FastAPI()
@default_app.get("/default-stale")
async def default_stale(): return None
def defaulted(value=(default_app := build_app())): pass

annotation_app = FastAPI()
@annotation_app.get("/annotation-stale")
async def annotation_stale(): return None
def annotated(value: marker(annotation_app := build_app())): pass

base_app = FastAPI()
@base_app.get("/base-stale")
async def base_stale(): return None
class HeaderBase((base_app := make_base())): pass

keyword_app = FastAPI()
@keyword_app.get("/keyword-stale")
async def keyword_stale(): return None
class HeaderKeyword(object, metaclass=select_meta(keyword_app := build_app())): pass

body_app = FastAPI()
@body_app.get("/body-stable")
async def body_stable(): return None
def body_scope():
    body_app = build_app()

class_app = FastAPI()
@class_app.get("/class-stable")
async def class_stable(): return None
class ClassScope:
    class_app = build_app()
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    entrypoints = adapter.entrypoints(context, (_syntax(tmp_path, "app.py"),))

    assert {
        item.public_name
        for item in entrypoints
        if item.kind is EntrypointKind.HTTP_ROUTE
    } == {"body_stable", "class_stable"}
    assert any(
        event.reason_code == "framework_object_rebound"
        and event.outcome is CoverageOutcome.PARTIAL
        for event in adapter.coverage_events(context)
    )


def test_cross_file_include_keeps_only_the_proven_registration_snapshot(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "routers.py",
        """from fastapi import APIRouter

outer = APIRouter()
nested = APIRouter()

@outer.get("/before")
async def before(): return None

@nested.get("/nested")
async def nested_route(): return None
""",
    )
    _write(
        tmp_path,
        "app.py",
        """from fastapi import FastAPI
from routers import nested, outer

app = FastAPI()
app.include_router(outer, prefix="/api")

@outer.get("/late")
async def late(): return None

outer.include_router(nested, prefix="/late-nested")
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    entrypoints = adapter.entrypoints(
        context,
        (_syntax(tmp_path, "app.py"), _syntax(tmp_path, "routers.py")),
    )

    assert {
        (item.public_name, item.public_path)
        for item in entrypoints
        if item.kind is EntrypointKind.HTTP_ROUTE
    } == {("before", "/api/before")}


def test_class_body_global_rebinds_app_but_class_local_store_does_not(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from fastapi import FastAPI

app = FastAPI()

@app.get("/stale")
async def stale(): return None

class GlobalMutation:
    global app
    app = build_app()

local_app = FastAPI()

@local_app.get("/class-local-valid")
async def class_local_valid(): return None

class LocalStore:
    local_app = build_app()
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    entrypoints = adapter.entrypoints(context, (_syntax(tmp_path, "app.py"),))

    assert {
        item.public_name
        for item in entrypoints
        if item.kind is EntrypointKind.HTTP_ROUTE
    } == {"class_local_valid"}
    assert any(
        event.reason_code == "framework_object_rebound"
        and event.outcome is CoverageOutcome.PARTIAL
        for event in adapter.coverage_events(context)
    )


def test_exception_class_headers_update_bindings_and_decorators_boundary_identity(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from fastapi import FastAPI

class A(Exception): pass
class B(Exception): pass

Alias = A
class HeaderEffect((Alias := B)): pass

header_app = FastAPI()

@header_app.exception_handler(Alias)
async def b_handler(request, exc): return None

@header_app.get("/header-binding")
async def header_binding(): raise B()

@replace_class
class Decorated(Exception): pass

decorated_app = FastAPI()

@decorated_app.exception_handler(Decorated)
async def decorated_handler(request, exc): return None

@decorated_app.get("/decorated-boundary")
async def decorated_boundary(): raise Decorated()
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    header_pipeline = adapter.pipeline(
        context, _candidate(adapter, context, "header_binding")
    )
    decorated_roles = {
        item.framework_role
        for item in adapter.pipeline(
            context, _candidate(adapter, context, "decorated_boundary")
        )
    }

    header_handler = next(
        item for item in header_pipeline if item.framework_role == "exception_handler"
    )
    assert isinstance(header_handler.target, FrameworkLocalTarget)
    assert header_handler.target.local_key == _function_key(tmp_path, "b_handler")
    assert "exception_handler" not in decorated_roles
    assert "exception_handler_resolution_boundary" in decorated_roles


def test_all_registration_references_use_their_occurrence_import_binding(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    for path in ("first.py", "second.py"):
        _write(
            tmp_path,
            path,
            """def dependency(): return None
def security_dependency(): return None
def task(): return None
async def endpoint(): return None
async def exception_handler(request, exc): return None
""",
        )
    _write(
        tmp_path,
        "app.py",
        """from fastapi import BackgroundTasks, Depends, FastAPI, Security
from first import dependency as dep_alias
from first import endpoint as endpoint_alias
from first import exception_handler as handler_alias
from first import security_dependency as security_alias
from first import task as task_alias

dep_ref = dep_alias
endpoint_ref = endpoint_alias
handler_ref = handler_alias
security_ref = security_alias
task_ref = task_alias

app = FastAPI()
app.add_exception_handler(Exception, handler_ref)
app.add_api_route(
    "/direct",
    endpoint_ref,
    dependencies=[Depends(dep_ref), Security(security_ref)],
)

@app.get("/problem")
async def problem(): raise Exception()

@app.get("/background")
async def background(background_tasks: BackgroundTasks):
    background_tasks.add_task(task_ref)

from second import dependency as dep_alias
from second import endpoint as endpoint_alias
from second import exception_handler as handler_alias
from second import security_dependency as security_alias
from second import task as task_alias

dep_ref = dep_alias
endpoint_ref = endpoint_alias
handler_ref = handler_alias
security_ref = security_alias
task_ref = task_alias
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    syntax = (
        _syntax(tmp_path, "app.py"),
        _syntax(tmp_path, "first.py"),
        _syntax(tmp_path, "second.py"),
    )
    entrypoints = adapter.entrypoints(context, syntax)
    direct = next(item for item in entrypoints if item.public_path == "/direct")
    problem = next(item for item in entrypoints if item.public_path == "/problem")
    background = next(item for item in entrypoints if item.public_path == "/background")

    assert direct.handler_local_key == _function_key_at(
        tmp_path, "first.py", "endpoint"
    )
    dependency_targets = {
        item.target.local_key
        for item in adapter.pipeline(context, direct)
        if item.framework_role == "decorator_dependency"
        and isinstance(item.target, FrameworkLocalTarget)
    }
    assert dependency_targets == {
        _function_key_at(tmp_path, "first.py", "dependency"),
        _function_key_at(tmp_path, "first.py", "security_dependency"),
    }
    exception_handler = next(
        item
        for item in adapter.pipeline(context, problem)
        if item.framework_role == "exception_handler"
    )
    assert isinstance(exception_handler.target, FrameworkLocalTarget)
    assert exception_handler.target.local_key == _function_key_at(
        tmp_path, "first.py", "exception_handler"
    )
    task_dispatch = next(
        item
        for item in adapter.pipeline(context, background)
        if item.framework_role == "background_task_dispatch"
    )
    assert isinstance(task_dispatch.target, FrameworkLocalTarget)
    assert task_dispatch.target.local_key == _function_key_at(
        tmp_path, "second.py", "task"
    )


def test_local_framework_object_provenance_is_source_ordered_and_constructor_bound(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from fastapi import FastAPI as FrameworkFastAPI
import fastapi as fastapi_module

@before_app.get("/before-construction")
async def before_construction(): return None

before_app = FrameworkFastAPI()

real_app = FrameworkFastAPI()
alias_app = real_app

@alias_app.get("/alias-valid")
async def alias_valid(): return None

qualified_app = fastapi_module.FastAPI()

@qualified_app.get("/qualified-valid")
async def qualified_valid(): return None
""",
    )
    _write(
        tmp_path,
        "dynamic.py",
        """def FastAPI(): return build_fake()
fake_app = FastAPI()

@fake_app.get("/fake")
async def fake(): return None
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    entrypoints = adapter.entrypoints(
        context,
        (_syntax(tmp_path, "app.py"), _syntax(tmp_path, "dynamic.py")),
    )

    assert {
        item.public_name
        for item in entrypoints
        if item.kind is EntrypointKind.HTTP_ROUTE
    } == {"alias_valid", "qualified_valid"}
    assert any(
        event.path == "dynamic.py"
        and event.reason_code == "framework_config_unresolved"
        and event.outcome is CoverageOutcome.PARTIAL
        for event in adapter.coverage_events(context)
    )


def test_yield_and_background_discovery_excludes_nested_lexical_scopes(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from fastapi import BackgroundTasks, Depends, FastAPI

def task(): return None

def dependency():
    def nested_generator():
        yield None
    return None

app = FastAPI()

@app.get("/lexical", dependencies=[Depends(dependency)])
async def lexical(background_tasks: BackgroundTasks):
    def nested_function():
        background_tasks.add_task(task)
    class NestedClass:
        background_tasks.add_task(task)
    hidden = lambda: background_tasks.add_task(task)
    return None
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    roles = {
        item.framework_role
        for item in adapter.pipeline(context, _candidate(adapter, context, "lexical"))
    }

    assert "yield_dependency_cleanup" not in roles
    assert "background_task_dispatch" not in roles


def test_background_tasks_prove_receiver_and_use_runtime_and_lexical_bindings(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    for path in ("first.py", "second.py"):
        _write(tmp_path, path, "def task(): return None\n")
    _write(
        tmp_path,
        "app.py",
        """from fastapi import BackgroundTasks as Tasks, FastAPI
from first import task as global_task

app = FastAPI()

@app.get("/runtime")
async def runtime(queue: Tasks):
    queue.add_task(global_task)

@app.get("/local")
async def local(queue: Tasks):
    from first import task as local_task
    queue.add_task(local_task)
    from second import task as local_task

@app.get("/unproved")
async def unproved(worker):
    worker.add_task(global_task)

from second import task as global_task
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    syntax = tuple(
        _syntax(tmp_path, path) for path in ("app.py", "first.py", "second.py")
    )
    entrypoints = adapter.entrypoints(context, syntax)

    def dispatched_target(name: str) -> str | None:
        candidate = next(item for item in entrypoints if item.public_name == name)
        dispatch = next(
            (
                item
                for item in adapter.pipeline(context, candidate)
                if item.framework_role == "background_task_dispatch"
            ),
            None,
        )
        return (
            dispatch.target.local_key
            if dispatch is not None
            and isinstance(dispatch.target, FrameworkLocalTarget)
            else None
        )

    assert dispatched_target("runtime") == _function_key_at(
        tmp_path, "second.py", "task"
    )
    assert dispatched_target("local") == _function_key_at(tmp_path, "first.py", "task")
    assert dispatched_target("unproved") is None
    assert any(
        event.path == "app.py"
        and event.reason_code == "background_task_unresolved"
        and event.outcome is CoverageOutcome.PARTIAL
        for event in adapter.coverage_events(context)
    )


def test_lifespan_target_uses_constructor_occurrence_binding(tmp_path: Path) -> None:
    _prepare(tmp_path)
    for path in ("first.py", "second.py"):
        _write(
            tmp_path,
            path,
            """async def lifespan(app):
    yield
""",
        )
    _write(
        tmp_path,
        "app.py",
        """from fastapi import FastAPI
from first import lifespan as selected_lifespan

app = FastAPI(lifespan=selected_lifespan)

from second import lifespan as selected_lifespan

def choose_lifespan(): return None
dynamic_app = FastAPI(lifespan=choose_lifespan())
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    entrypoints = adapter.entrypoints(
        context,
        tuple(_syntax(tmp_path, path) for path in ("app.py", "first.py", "second.py")),
    )

    lifespan = next(item for item in entrypoints if item.public_name == "app_lifespan")
    assert lifespan.handler_local_key == _function_key_at(
        tmp_path, "first.py", "lifespan"
    )
    assert not any(item.public_name == "dynamic_app_lifespan" for item in entrypoints)
    assert any(
        event.path == "app.py"
        and event.reason_code == "framework_config_unresolved"
        and event.outcome is CoverageOutcome.PARTIAL
        for event in adapter.coverage_events(context)
    )


def test_exception_class_execution_updates_globals_and_bounds_conditional_headers(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from fastapi import FastAPI

class A(Exception): pass
class B(Exception): pass

Alias = A
class GlobalMutation:
    global Alias
    Alias = B

global_app = FastAPI()

@global_app.exception_handler(Alias)
async def global_handler(request, exc): return None

@global_app.get("/global")
async def global_endpoint(): raise B()

ConditionalAlias = A
class ConditionalHeader(
    (ConditionalAlias := B) if enabled else Exception
): pass

conditional_app = FastAPI()

@conditional_app.exception_handler(ConditionalAlias)
async def conditional_handler(request, exc): return None

@conditional_app.get("/conditional")
async def conditional_endpoint(): raise B()
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    entrypoints = adapter.entrypoints(context, (_syntax(tmp_path, "app.py"),))
    global_route = next(
        item for item in entrypoints if item.public_name == "global_endpoint"
    )
    conditional_route = next(
        item for item in entrypoints if item.public_name == "conditional_endpoint"
    )

    global_handler = next(
        item
        for item in adapter.pipeline(context, global_route)
        if item.framework_role == "exception_handler"
    )
    assert isinstance(global_handler.target, FrameworkLocalTarget)
    assert global_handler.target.local_key == _function_key(tmp_path, "global_handler")
    conditional_roles = {
        item.framework_role for item in adapter.pipeline(context, conditional_route)
    }
    assert "exception_handler" not in conditional_roles
    assert "exception_handler_resolution_boundary" in conditional_roles


def test_class_body_global_def_and_class_bindings_invalidate_framework_objects(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from fastapi import FastAPI

def_app = FastAPI()

@def_app.get("/stale-def")
async def stale_def(): return None

class DefMutation:
    global def_app
    def def_app(): return None

class_app = FastAPI()

@class_app.get("/stale-class")
async def stale_class(): return None

class ClassMutation:
    global class_app
    class class_app: pass

safe_app = FastAPI()

@safe_app.get("/safe")
async def safe(): return None

class NestedBody:
    global safe_app
    def nested():
        safe_app = build_app()
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    entrypoints = adapter.entrypoints(context, (_syntax(tmp_path, "app.py"),))

    assert {
        item.public_name
        for item in entrypoints
        if item.kind is EntrypointKind.HTTP_ROUTE
    } == {"safe"}
    assert any(
        event.reason_code == "framework_object_rebound"
        and event.outcome is CoverageOutcome.PARTIAL
        for event in adapter.coverage_events(context)
    )


def test_annotated_assignments_discover_proven_framework_objects_and_bound_dynamic(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from fastapi import APIRouter, FastAPI

app: FastAPI = FastAPI()
app_alias: FastAPI = app
router: APIRouter = APIRouter(prefix="/api")
router_alias: APIRouter = router

@router_alias.get("/annotated")
async def annotated(): return None

app_alias.include_router(router_alias)
""",
    )
    _write(
        tmp_path,
        "dynamic.py",
        """from fastapi import FastAPI

def factory(): return None
dynamic: FastAPI = factory()

@dynamic.get("/invented")
async def invented(): return None
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    entrypoints = adapter.entrypoints(
        context,
        (_syntax(tmp_path, "app.py"), _syntax(tmp_path, "dynamic.py")),
    )

    assert {
        (item.public_name, item.public_path)
        for item in entrypoints
        if item.kind is EntrypointKind.HTTP_ROUTE
    } == {("annotated", "/api/annotated")}
    assert any(
        event.path == "dynamic.py"
        and event.reason_code == "framework_config_unresolved"
        and event.outcome is CoverageOutcome.PARTIAL
        for event in adapter.coverage_events(context)
    )


def test_registration_detection_uses_receiver_binding_at_call_occurrence(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "proven.py",
        """from fastapi import FastAPI

receiver = {}
receiver.get("ordinary")
receiver = FastAPI()

@receiver.get("/valid")
async def valid(): return None
""",
    )
    _write(
        tmp_path,
        "unknown.py",
        """from fastapi import FastAPI

if enabled:
    receiver = build_app()
receiver.get("/maybe")
receiver = FastAPI()
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    entrypoints = adapter.entrypoints(
        context,
        (_syntax(tmp_path, "proven.py"), _syntax(tmp_path, "unknown.py")),
    )

    assert {item.public_name for item in entrypoints} == {"valid"}
    assert not any(
        event.path == "proven.py" and event.reason_code == "framework_config_unresolved"
        for event in adapter.coverage_events(context)
    )
    assert any(
        event.path == "unknown.py"
        and event.reason_code == "framework_config_unresolved"
        and event.outcome is CoverageOutcome.PARTIAL
        for event in adapter.coverage_events(context)
    )


def test_router_snapshot_and_legacy_events_are_include_instance_bounded(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "routers.py",
        """from fastapi import APIRouter

router = APIRouter()

@router.on_event("startup")
async def before_event(): return None
""",
    )
    _write(
        tmp_path,
        "registrar.py",
        """from routers import router

@router.get("/unknown-order")
async def unknown_order(): return None
""",
    )
    _write(
        tmp_path,
        "app.py",
        """from fastapi import FastAPI
from routers import router

async def lifespan(app):
    yield

lifespan_app = FastAPI(lifespan=lifespan)
lifespan_app.include_router(router)

@router.on_event("startup")
async def after_event(): return None

legacy_app = FastAPI()
legacy_app.include_router(router)
""",
    )
    _write(
        tmp_path,
        "cycle_app.py",
        """from fastapi import FastAPI

app = FastAPI()
from cycle_router import router
app.include_router(router)
""",
    )
    _write(
        tmp_path,
        "cycle_router.py",
        """from fastapi import APIRouter

router = APIRouter()
import cycle_app

@router.get("/cyclic")
async def cyclic(): return None
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    entrypoints = adapter.entrypoints(
        context,
        tuple(
            _syntax(tmp_path, path)
            for path in (
                "app.py",
                "routers.py",
                "registrar.py",
                "cycle_app.py",
                "cycle_router.py",
            )
        ),
    )

    assert not any(item.public_name == "unknown_order" for item in entrypoints)
    assert not any(item.public_name == "cyclic" for item in entrypoints)
    assert {
        item.public_name
        for item in entrypoints
        if item.kind is EntrypointKind.EVENT_LISTENER
    } == {"before_event", "after_event"}
    assert any(
        event.path == "registrar.py"
        and event.reason_code == "router_snapshot_order_unresolved"
        and event.outcome is CoverageOutcome.PARTIAL
        for event in adapter.coverage_events(context)
    )
    assert any(
        event.path == "cycle_router.py"
        and event.reason_code == "router_snapshot_order_unresolved"
        and event.outcome is CoverageOutcome.PARTIAL
        for event in adapter.coverage_events(context)
    )


def test_middleware_call_next_discovery_excludes_nested_lexical_scopes(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from fastapi import FastAPI

app = FastAPI()

@app.middleware("http")
async def middleware(request, call_next):
    def nested_function():
        return call_next(request)
    class NestedClass:
        result = call_next(request)
    hidden = lambda: call_next(request)
    return None

@app.get("/endpoint")
async def endpoint(): return None
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    middleware = next(
        item
        for item in adapter.pipeline(context, _candidate(adapter, context, "endpoint"))
        if item.framework_role == "middleware_request"
    )

    assert middleware.short_circuit_successors


def test_dependency_proof_supports_annotated_and_bounds_fake_and_dynamic_cache(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from typing import Annotated
from fastapi import Depends as FastDepends, FastAPI, Security as FastSecurity

def shared(): return None
def secured(): return None

app = FastAPI()

@app.get("/annotated")
async def annotated(
    first: Annotated[str, FastDepends(shared)],
    second: Annotated[str, FastSecurity(secured)],
    third: Annotated[str, FastDepends(shared)],
): return None
""",
    )
    _write(
        tmp_path,
        "fake.py",
        """from fastapi import FastAPI

def Depends(target): return target
def dependency(): return None

app = FastAPI()

@app.get("/fake", dependencies=[Depends(dependency)])
async def fake(): return None
""",
    )
    _write(
        tmp_path,
        "dynamic_cache.py",
        """from fastapi import Depends, FastAPI

def dependency(): return None
cache_setting = build_cache_setting()
app = FastAPI()

@app.get(
    "/dynamic-cache",
    dependencies=[Depends(dependency, use_cache=cache_setting)],
)
async def dynamic_cache(): return None
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    syntax = tuple(
        _syntax(tmp_path, path) for path in ("app.py", "fake.py", "dynamic_cache.py")
    )
    entrypoints = adapter.entrypoints(context, syntax)

    assert {item.public_name for item in entrypoints} == {"annotated"}
    annotated = next(item for item in entrypoints if item.public_name == "annotated")
    dependency_segments = [
        item
        for item in adapter.pipeline(context, annotated)
        if item.framework_role in {"decorator_dependency", "dependency_cache_reuse"}
    ]
    assert [item.framework_role for item in dependency_segments] == [
        "decorator_dependency",
        "decorator_dependency",
        "dependency_cache_reuse",
    ]
    assert [
        item.target.local_key
        for item in dependency_segments[:2]
        if isinstance(item.target, FrameworkLocalTarget)
    ] == [
        _function_key(tmp_path, "shared"),
        _function_key(tmp_path, "secured"),
    ]
    assert {
        event.path
        for event in adapter.coverage_events(context)
        if event.reason_code == "framework_config_unresolved"
        and event.outcome is CoverageOutcome.PARTIAL
    } >= {"fake.py", "dynamic_cache.py"}


def test_background_receiver_binding_at_call_controls_dispatch(tmp_path: Path) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from fastapi import BackgroundTasks, FastAPI

def task(): return None
app = FastAPI()

@app.get("/exact")
async def exact(tasks: BackgroundTasks):
    tasks.add_task(task)

@app.get("/rebound")
async def rebound(tasks: BackgroundTasks):
    tasks = {}
    tasks.add_task(task)

@app.get("/uncertain")
async def uncertain(tasks: BackgroundTasks):
    if enabled:
        tasks = build_tasks()
    tasks.add_task(task)
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    entrypoints = adapter.entrypoints(context, (_syntax(tmp_path, "app.py"),))

    def dispatches(name: str) -> bool:
        candidate = next(item for item in entrypoints if item.public_name == name)
        return any(
            item.framework_role == "background_task_dispatch"
            for item in adapter.pipeline(context, candidate)
        )

    assert dispatches("exact") is True
    assert dispatches("rebound") is False
    assert dispatches("uncertain") is False
    assert any(
        event.path == "app.py"
        and event.reason_code == "background_task_unresolved"
        and event.outcome is CoverageOutcome.PARTIAL
        for event in adapter.coverage_events(context)
    )


def test_middleware_binds_continuation_and_requires_must_call_proof(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from fastapi import FastAPI

app = FastAPI()

@app.middleware("http")
async def renamed(request, proceed):
    return await proceed(request)

@app.middleware("http")
async def aliased(request, proceed):
    next_step = proceed
    return await next_step(request)

@app.middleware("http")
async def fake_name(request, proceed):
    def call_next(value): return value
    return call_next(request)

@app.middleware("http")
async def conditional(request, proceed):
    if enabled:
        return await proceed(request)
    return None

@app.middleware("http")
async def chained_comparison(request, proceed):
    return 0 < threshold < await proceed(request)

@app.middleware("http")
async def uncertain(request, proceed):
    proceed = wrap(proceed)
    return await proceed(request)

@app.get("/endpoint")
async def endpoint(): return None
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    pipeline = adapter.pipeline(context, _candidate(adapter, context, "endpoint"))

    def shortcut(name: str) -> bool:
        local_key = _function_key(tmp_path, name)
        middleware = next(
            item
            for item in pipeline
            if item.framework_role == "middleware_request"
            and isinstance(item.target, FrameworkLocalTarget)
            and item.target.local_key == local_key
        )
        return bool(middleware.short_circuit_successors)

    assert shortcut("renamed") is False
    assert shortcut("aliased") is False
    assert shortcut("fake_name") is True
    assert shortcut("conditional") is True
    assert shortcut("chained_comparison") is True
    assert shortcut("uncertain") is True
    assert any(
        event.path == "app.py"
        and event.reason_code == "middleware_behavior_unresolved"
        and event.outcome is CoverageOutcome.PARTIAL
        for event in adapter.coverage_events(context)
    )


def test_annotated_metadata_requires_proven_dependency_or_nondependency(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from typing import Annotated
from fastapi import Depends as Dependency, FastAPI

def shared(): return None
def marker_factory(): return Dependency(shared)

marker = Dependency(shared)
app = FastAPI()

@app.get("/exact")
async def exact(value: Annotated[str, Dependency(shared)]): return None

@app.get("/nondependency")
async def nondependency(value: Annotated[str, "description", 42]): return None

@app.get("/aliased")
async def aliased(value: Annotated[str, marker]): return None

@app.get("/computed")
async def computed(value: Annotated[str, marker_factory()]): return None

@app.get("/quoted")
async def quoted(value: "Annotated[str, Dependency(shared)]"): return None
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    entrypoints = adapter.entrypoints(context, (_syntax(tmp_path, "app.py"),))

    assert {item.public_name for item in entrypoints} == {"exact", "nondependency"}
    exact = next(item for item in entrypoints if item.public_name == "exact")
    assert any(
        item.framework_role == "decorator_dependency"
        and isinstance(item.target, FrameworkLocalTarget)
        and item.target.local_key == _function_key(tmp_path, "shared")
        for item in adapter.pipeline(context, exact)
    )
    assert any(
        event.path == "app.py"
        and event.reason_code == "framework_config_unresolved"
        and event.outcome is CoverageOutcome.PARTIAL
        for event in adapter.coverage_events(context)
    )


def test_security_scopes_participate_in_dependency_cache_identity(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from fastapi import FastAPI, Security

def shared(): return None
app = FastAPI()

@app.get("/scoped")
async def scoped(
    first=Security(shared, scopes=["write", "read", "write"]),
    second=Security(shared, scopes=("read", "write")),
    third=Security(shared, scopes=["admin"]),
): return None

dynamic_scopes = build_scopes()

@app.get("/dynamic")
async def dynamic(value=Security(shared, scopes=dynamic_scopes)): return None
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    entrypoints = adapter.entrypoints(context, (_syntax(tmp_path, "app.py"),))

    assert {item.public_name for item in entrypoints} == {"scoped"}
    scoped = next(item for item in entrypoints if item.public_name == "scoped")
    assert [
        item.framework_role
        for item in adapter.pipeline(context, scoped)
        if item.framework_role in {"decorator_dependency", "dependency_cache_reuse"}
    ] == [
        "decorator_dependency",
        "dependency_cache_reuse",
        "decorator_dependency",
    ]
    assert any(
        event.path == "app.py"
        and event.reason_code == "framework_config_unresolved"
        and event.outcome is CoverageOutcome.PARTIAL
        for event in adapter.coverage_events(context)
    )


def test_response_annotations_prove_response_identity_and_subclass_boundaries(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from fastapi import FastAPI, Response as FastResponse
from fastapi.responses import ORJSONResponse
from starlette.responses import Response

class CustomResponse(Response): pass
class Out: pass
class MaybeResponse(select_base()): pass

app = FastAPI()

@app.get("/direct")
async def direct() -> FastResponse: return FastResponse()

@app.get("/subclass")
async def subclass() -> CustomResponse: return CustomResponse()

@app.get("/builtin-subclass")
async def builtin_subclass() -> ORJSONResponse: return ORJSONResponse({})

@app.get("/model")
async def model() -> Out: return Out()

@app.get("/unknown")
async def unknown() -> MaybeResponse: return MaybeResponse()
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)

    def roles(name: str) -> set[str]:
        return {
            item.framework_role
            for item in adapter.pipeline(context, _candidate(adapter, context, name))
        }

    for name in ("direct", "subclass", "builtin_subclass"):
        assert "response_model_serialization" not in roles(name)
        assert "response_model_resolution_boundary" not in roles(name)
    assert "response_model_serialization" in roles("model")
    assert "response_model_serialization" not in roles("unknown")
    assert "response_model_resolution_boundary" in roles("unknown")


def test_explicit_exception_metaclass_is_an_identity_boundary(tmp_path: Path) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from fastapi import FastAPI

class MetaError(Exception, metaclass=select_metaclass()): pass

app = FastAPI()

@app.exception_handler(MetaError)
async def meta_handler(request, exc): return None

@app.get("/meta")
async def meta_endpoint(): raise MetaError()
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    roles = {
        item.framework_role
        for item in adapter.pipeline(
            context,
            _candidate(adapter, context, "meta_endpoint"),
        )
    }

    assert "exception_handler" not in roles
    assert "exception_handler_resolution_boundary" in roles


def test_imperative_and_constructor_events_are_occurrence_and_app_bounded(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    for path in ("first.py", "second.py"):
        _write(
            tmp_path,
            path,
            """async def startup(): return None
async def shutdown(): return None
""",
        )
    _write(
        tmp_path,
        "app.py",
        """from contextlib import asynccontextmanager
from fastapi import FastAPI
from first import shutdown as selected_shutdown
from first import startup as selected_startup

async def local_startup(): return None
async def local_shutdown(): return None

@asynccontextmanager
async def lifespan(app):
    yield

legacy = FastAPI(
    on_startup=[selected_startup],
    on_shutdown=(selected_shutdown,),
)
lifespan_app = FastAPI(
    lifespan=lifespan,
    on_startup=[local_startup],
    on_shutdown=[local_shutdown],
)

from second import startup as selected_startup

legacy.add_event_handler("startup", selected_startup)
legacy.add_event_handler("shutdown", local_shutdown)

dynamic_handlers = build_handlers()
dynamic_app = FastAPI(on_startup=dynamic_handlers)
legacy.add_event_handler("startup", build_handler())
""",
    )
    _write(
        tmp_path,
        "none.py",
        """from fastapi import FastAPI

app = FastAPI(on_startup=None, on_shutdown=None)
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    entrypoints = adapter.entrypoints(
        context,
        tuple(
            _syntax(tmp_path, path)
            for path in ("app.py", "first.py", "second.py", "none.py")
        ),
    )

    event_keys = {
        item.handler_local_key
        for item in entrypoints
        if item.kind is EntrypointKind.EVENT_LISTENER
    }
    assert event_keys == {
        _function_key_at(tmp_path, "first.py", "startup"),
        _function_key_at(tmp_path, "first.py", "shutdown"),
        _function_key_at(tmp_path, "second.py", "startup"),
        _function_key(tmp_path, "local_shutdown"),
    }
    assert _function_key(tmp_path, "local_startup") not in event_keys
    assert any(
        event.path == "app.py"
        and event.reason_code == "framework_config_unresolved"
        and event.outcome is CoverageOutcome.PARTIAL
        for event in adapter.coverage_events(context)
    )
    assert not any(
        event.path == "none.py" and event.reason_code == "framework_config_unresolved"
        for event in adapter.coverage_events(context)
    )


def test_annotated_background_receiver_literal_rebind_is_proven_nonreceiver(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from fastapi import BackgroundTasks, FastAPI

def task(): return None
app = FastAPI()

@app.get("/rebound")
async def rebound(tasks: BackgroundTasks):
    tasks: object = {}
    tasks.add_task(task)
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    route = _candidate(adapter, context, "rebound")

    assert not any(
        item.framework_role == "background_task_dispatch"
        for item in adapter.pipeline(context, route)
    )
    assert not any(
        event.path == "app.py" and event.reason_code == "background_task_unresolved"
        for event in adapter.coverage_events(context)
    )


def test_middleware_destructuring_aliases_and_storage_escape_are_bounded(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from fastapi import FastAPI

exact_app = FastAPI()

@exact_app.middleware("http")
async def destructured(request, proceed):
    (next_step,) = (proceed,)
    return await next_step(request)

@exact_app.get("/exact")
async def exact_endpoint(): return None

escaped_app = FastAPI()

@escaped_app.middleware("http")
async def attribute_escape(request, proceed):
    holder.callback = proceed
    return await holder.callback(request)

@escaped_app.middleware("http")
async def container_escape(request, proceed):
    callbacks[0] = proceed
    return await callbacks[0](request)

@escaped_app.get("/escaped")
async def escaped_endpoint(): return None
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    entrypoints = adapter.entrypoints(context, (_syntax(tmp_path, "app.py"),))

    def middleware_for(route_name: str, middleware_name: str):
        route = next(item for item in entrypoints if item.public_name == route_name)
        local_key = _function_key(tmp_path, middleware_name)
        return next(
            item
            for item in adapter.pipeline(context, route)
            if item.framework_role == "middleware_request"
            and isinstance(item.target, FrameworkLocalTarget)
            and item.target.local_key == local_key
        )

    assert not middleware_for(
        "exact_endpoint",
        "destructured",
    ).short_circuit_successors
    for name in ("attribute_escape", "container_escape"):
        assert middleware_for("escaped_endpoint", name).short_circuit_successors
    assert any(
        event.path == "app.py"
        and event.reason_code == "middleware_behavior_unresolved"
        and event.outcome is CoverageOutcome.PARTIAL
        for event in adapter.coverage_events(context)
    )


def test_dependency_marker_and_annotated_alias_bindings_are_occurrence_bounded(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from typing import Annotated
from fastapi import Depends, FastAPI

def shared(): return None

marker = Depends(shared)
DependencyValue = Annotated[str, Depends(shared)]
app = FastAPI()

@app.get("/marker")
async def marker_route(value=marker): return None

@app.get("/alias")
async def alias_route(value: DependencyValue): return None

marker = build_marker()
DependencyValue = build_annotation()

@app.get("/dynamic-marker")
async def dynamic_marker(value=marker): return None

@app.get("/dynamic-alias")
async def dynamic_alias(value: DependencyValue): return None
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    entrypoints = adapter.entrypoints(context, (_syntax(tmp_path, "app.py"),))

    assert {item.public_name for item in entrypoints} == {
        "marker_route",
        "alias_route",
    }
    for route in entrypoints:
        dependencies = [
            item
            for item in adapter.pipeline(context, route)
            if item.framework_role == "decorator_dependency"
        ]
        assert len(dependencies) == 1
        assert isinstance(dependencies[0].target, FrameworkLocalTarget)
        assert dependencies[0].target.local_key == _function_key(tmp_path, "shared")
    assert any(
        event.path == "app.py"
        and event.reason_code == "framework_config_unresolved"
        and event.outcome is CoverageOutcome.PARTIAL
        for event in adapter.coverage_events(context)
    )


def test_uncached_dependency_execution_populates_later_cache_and_single_cleanup(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from fastapi import Depends, FastAPI

def resource():
    yield object()

app = FastAPI()

@app.get("/cache")
async def endpoint(
    first=Depends(resource, use_cache=False),
    second=Depends(resource),
): return None
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    pipeline = adapter.pipeline(context, _candidate(adapter, context, "endpoint"))

    assert [
        item.framework_role
        for item in pipeline
        if item.framework_role in {"decorator_dependency", "dependency_cache_reuse"}
    ] == ["decorator_dependency", "dependency_cache_reuse"]
    assert (
        sum(item.framework_role == "yield_dependency_cleanup" for item in pipeline) == 1
    )


def test_response_subclass_requires_every_base_identity_to_be_proven(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from fastapi import FastAPI, Response

class Plain: pass
class SafeResponse(Response, Plain): pass
class UnknownBase(metaclass=select_metaclass()): pass
class MaybeResponse(Response, UnknownBase): pass
class ReplacedResponse(Response, metaclass=select_metaclass()): pass
class InheritedResponse(ReplacedResponse): pass

app = FastAPI()

@app.get("/safe")
async def safe() -> SafeResponse: return SafeResponse()

@app.get("/maybe")
async def maybe() -> MaybeResponse: return MaybeResponse()

@app.get("/inherited")
async def inherited() -> InheritedResponse: return InheritedResponse()
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)

    def roles(name: str) -> set[str]:
        return {
            item.framework_role
            for item in adapter.pipeline(context, _candidate(adapter, context, name))
        }

    assert "response_model_serialization" not in roles("safe")
    assert "response_model_resolution_boundary" not in roles("safe")
    for name in ("maybe", "inherited"):
        assert "response_model_serialization" not in roles(name)
        assert "response_model_resolution_boundary" in roles(name)


def test_exception_identity_requires_transitively_proven_metaclass(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from fastapi import FastAPI

class DynamicBase(Exception, metaclass=select_metaclass()): pass
class ChildError(DynamicBase): pass

app = FastAPI()

@app.exception_handler(ChildError)
async def child_handler(request, exc): return None

@app.get("/child")
async def child_endpoint(): raise ChildError()
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    roles = {
        item.framework_role
        for item in adapter.pipeline(
            context,
            _candidate(adapter, context, "child_endpoint"),
        )
    }

    assert "exception_handler" not in roles
    assert "exception_handler_resolution_boundary" in roles


def test_router_constructor_events_are_snapshot_and_lifespan_bounded(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from contextlib import asynccontextmanager
from fastapi import APIRouter, FastAPI

async def router_startup(): return None
async def late_startup(): return None

router = APIRouter(on_startup=[router_startup])

legacy_one = FastAPI()
legacy_one.include_router(router)

router.add_event_handler("startup", late_startup)

legacy_two = FastAPI()
legacy_two.include_router(router)

dynamic_router = APIRouter(on_startup=build_handlers())

@asynccontextmanager
async def lifespan(app):
    yield

lifespan_app = FastAPI(lifespan=lifespan, on_startup=build_handlers())
lifespan_app.include_router(dynamic_router)
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    entrypoints = adapter.entrypoints(context, (_syntax(tmp_path, "app.py"),))

    event_keys = [
        item.handler_local_key
        for item in entrypoints
        if item.kind is EntrypointKind.EVENT_LISTENER
    ]
    assert event_keys.count(_function_key(tmp_path, "router_startup")) == 4
    assert event_keys.count(_function_key(tmp_path, "late_startup")) == 3
    assert any(
        event.path == "app.py"
        and event.reason_code == "framework_config_unresolved"
        and event.outcome is CoverageOutcome.PARTIAL
        for event in adapter.coverage_events(context)
    )


def test_background_receiver_destructuring_is_occurrence_and_shape_bounded(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "exact.py",
        """from fastapi import BackgroundTasks, FastAPI

def before(): return None
def after(): return None
app = FastAPI()

@app.get("/exact")
async def exact(tasks: BackgroundTasks):
    tasks.add_task(before)
    (tasks, (other,)) = ({}, (1,))
    tasks.add_task(after)
""",
    )
    _write(
        tmp_path,
        "mismatch.py",
        """from fastapi import BackgroundTasks, FastAPI

def task(): return None
app = FastAPI()

@app.get("/mismatch")
async def mismatch(tasks: BackgroundTasks):
    (tasks, other) = ({},)
    tasks.add_task(task)
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    entrypoints = adapter.entrypoints(
        context,
        (_syntax(tmp_path, "exact.py"), _syntax(tmp_path, "mismatch.py")),
    )

    exact_route = next(item for item in entrypoints if item.public_name == "exact")
    mismatch_route = next(
        item for item in entrypoints if item.public_name == "mismatch"
    )
    exact_dispatches = [
        item
        for item in adapter.pipeline(context, exact_route)
        if item.framework_role == "background_task_dispatch"
    ]
    assert len(exact_dispatches) == 1
    assert isinstance(exact_dispatches[0].target, FrameworkLocalTarget)
    assert exact_dispatches[0].target.local_key == _function_key_at(
        tmp_path, "exact.py", "before"
    )
    assert not any(
        item.framework_role == "background_task_dispatch"
        for item in adapter.pipeline(context, mismatch_route)
    )
    coverage = adapter.coverage_events(context)
    assert not any(
        event.path == "exact.py" and event.reason_code == "background_task_unresolved"
        for event in coverage
    )
    assert any(
        event.path == "mismatch.py"
        and event.reason_code == "background_task_unresolved"
        for event in coverage
    )


def test_middleware_static_containers_preserve_only_traced_continuations(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "exact.py",
        """from fastapi import FastAPI

app = FastAPI()

@app.middleware("http")
async def static_container(request, proceed):
    callbacks = {"request": [(proceed,)]}
    next_step = callbacks["request"][0][0]
    return await next_step(request)

@app.get("/exact")
async def exact_endpoint(): return None
""",
    )
    _write(
        tmp_path,
        "escaped.py",
        """from fastapi import FastAPI

app = FastAPI()

@app.middleware("http")
async def escaped_container(request, proceed):
    holder.callbacks = [proceed]
    return await holder.callbacks[0](request)

@app.get("/escaped")
async def escaped_endpoint(): return None
""",
    )
    _write(
        tmp_path,
        "overwritten.py",
        """from fastapi import FastAPI

app = FastAPI()

@app.middleware("http")
async def overwritten_container(request, proceed):
    callbacks = [proceed]
    callbacks[0] = replacement
    return await callbacks[0](request)

@app.get("/overwritten")
async def overwritten_endpoint(): return None
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    entrypoints = adapter.entrypoints(
        context,
        (
            _syntax(tmp_path, "exact.py"),
            _syntax(tmp_path, "escaped.py"),
            _syntax(tmp_path, "overwritten.py"),
        ),
    )

    def middleware(route_name: str):
        route = next(item for item in entrypoints if item.public_name == route_name)
        return next(
            item
            for item in adapter.pipeline(context, route)
            if item.framework_role == "middleware_request"
        )

    assert not middleware("exact_endpoint").short_circuit_successors
    assert middleware("escaped_endpoint").short_circuit_successors
    assert middleware("overwritten_endpoint").short_circuit_successors
    coverage = adapter.coverage_events(context)
    assert not any(
        event.path == "exact.py"
        and event.reason_code == "middleware_behavior_unresolved"
        for event in coverage
    )
    assert any(
        event.path == "escaped.py"
        and event.reason_code == "middleware_behavior_unresolved"
        for event in coverage
    )
    assert any(
        event.path == "overwritten.py"
        and event.reason_code == "middleware_behavior_unresolved"
        for event in coverage
    )


def test_dependency_alias_graph_handles_nested_stores_modules_and_cycles(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "providers.py",
        """from typing import Annotated
from fastapi import Depends

def shared(): return None
marker = Depends(shared)
DependencyAlias = Annotated[str, Depends(shared)]
""",
    )
    _write(
        tmp_path,
        "cycle_a.py",
        """from cycle_b import other_marker
cycle_marker = other_marker
""",
    )
    _write(
        tmp_path,
        "cycle_b.py",
        """from cycle_a import cycle_marker as other_marker
""",
    )
    _write(
        tmp_path,
        "app.py",
        """from fastapi import Depends, FastAPI
from providers import DependencyAlias, marker
from cycle_a import cycle_marker

def local_shared(): return None
app = FastAPI()

@app.get("/marker")
async def imported_marker(value=marker): return None

@app.get("/alias")
async def imported_alias(value: DependencyAlias): return None

conditional_marker = Depends(local_shared)
if enabled:
    conditional_marker = build_marker()

@app.get("/conditional")
async def conditional(value=conditional_marker): return None

destructured_marker = Depends(local_shared)
(destructured_marker, other) = (build_marker(), object())

@app.get("/destructured")
async def destructured(value=destructured_marker): return None

@app.get("/cycle")
async def cyclic(value=cycle_marker): return None
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    entrypoints = adapter.entrypoints(
        context,
        tuple(
            _syntax(tmp_path, path)
            for path in (
                "app.py",
                "cycle_a.py",
                "cycle_b.py",
                "providers.py",
            )
        ),
    )

    assert {item.public_name for item in entrypoints} == {
        "imported_marker",
        "imported_alias",
    }
    for route in entrypoints:
        dependencies = [
            item
            for item in adapter.pipeline(context, route)
            if item.framework_role == "decorator_dependency"
        ]
        assert len(dependencies) == 1
        assert isinstance(dependencies[0].target, FrameworkLocalTarget)
        assert dependencies[0].target.local_key == _function_key_at(
            tmp_path, "providers.py", "shared"
        )
    assert any(
        event.path == "app.py" and event.reason_code == "framework_config_unresolved"
        for event in adapter.coverage_events(context)
    )


def test_exception_identity_fixed_point_is_file_order_independent_and_bounded(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "z_base.py",
        """from fastapi import FastAPI
class BaseError(Exception): pass
safe_app = FastAPI()
cycle_app = FastAPI()
""",
    )
    _write(
        tmp_path,
        "middle.py",
        """from z_base import BaseError as ReexportedBase
""",
    )
    _write(
        tmp_path,
        "a_child.py",
        """from middle import ReexportedBase
from z_base import safe_app
class ChildError(ReexportedBase): pass

@safe_app.exception_handler(ChildError)
async def child_handler(request, exc): return None

@safe_app.get("/child")
async def child_endpoint(): raise ChildError()
""",
    )
    _write(
        tmp_path,
        "cycle_a.py",
        """from cycle_b import BError
from z_base import cycle_app
class AError(BError): pass

@cycle_app.exception_handler(AError)
async def cycle_handler(request, exc): return None

@cycle_app.get("/cycle")
async def cycle_endpoint(): raise AError()
""",
    )
    _write(
        tmp_path,
        "cycle_b.py",
        """from cycle_a import AError
class BError(AError): pass
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    entrypoints = adapter.entrypoints(
        context,
        tuple(
            _syntax(tmp_path, path)
            for path in (
                "a_child.py",
                "cycle_a.py",
                "cycle_b.py",
                "middle.py",
                "z_base.py",
            )
        ),
    )

    def roles(name: str) -> set[str]:
        route = next(item for item in entrypoints if item.public_name == name)
        return {item.framework_role for item in adapter.pipeline(context, route)}

    assert "exception_handler" in roles("child_endpoint")
    assert "exception_handler_resolution_boundary" not in roles("child_endpoint")
    assert "exception_handler" not in roles("cycle_endpoint")
    assert "exception_handler_resolution_boundary" in roles("cycle_endpoint")


def test_router_lifespans_expand_per_include_snapshot_with_custom_app_lifespan(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from contextlib import asynccontextmanager
from fastapi import APIRouter, FastAPI

@asynccontextmanager
async def child_lifespan(app):
    yield

@asynccontextmanager
async def app_lifespan(app):
    yield

child = APIRouter(lifespan=child_lifespan)
parent = APIRouter()
parent.include_router(child)

legacy = FastAPI()
legacy.include_router(parent)

parent.include_router(child)

custom = FastAPI(lifespan=app_lifespan)
custom.include_router(parent)

dynamic = APIRouter(lifespan=build_lifespan())
custom.include_router(dynamic)
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    entrypoints = adapter.entrypoints(context, (_syntax(tmp_path, "app.py"),))

    lifespan_keys = [
        item.handler_local_key
        for item in entrypoints
        if item.kind is EntrypointKind.PROCESS_MAIN
    ]
    assert lifespan_keys.count(_function_key(tmp_path, "child_lifespan")) == 3
    assert lifespan_keys.count(_function_key(tmp_path, "app_lifespan")) == 1
    assert any(
        event.path == "app.py"
        and event.reason_code == "framework_config_unresolved"
        and event.outcome is CoverageOutcome.PARTIAL
        for event in adapter.coverage_events(context)
    )


def test_qualified_dependency_aliases_follow_module_binding_occurrences(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "providers.py",
        """from typing import Annotated
from fastapi import Depends

def shared(): return None
marker = Depends(shared)
DependencyAlias = Annotated[str, Depends(shared)]
""",
    )
    _write(
        tmp_path,
        "app.py",
        """import providers as deps
from fastapi import FastAPI

app = FastAPI()

@app.get("/marker")
async def marker_route(value=deps.marker): return None

@app.get("/alias")
async def alias_route(value: deps.DependencyAlias): return None

deps = build_namespace()

@app.get("/dynamic-marker")
async def dynamic_marker_route(value=deps.marker): return None

@app.get("/dynamic-alias")
async def dynamic_alias_route(value: deps.DependencyAlias): return None
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    entrypoints = adapter.entrypoints(
        context,
        (_syntax(tmp_path, "app.py"), _syntax(tmp_path, "providers.py")),
    )

    assert {item.public_name for item in entrypoints} == {
        "marker_route",
        "alias_route",
    }
    for route in entrypoints:
        dependencies = [
            item
            for item in adapter.pipeline(context, route)
            if item.framework_role == "decorator_dependency"
        ]
        assert len(dependencies) == 1
        assert isinstance(dependencies[0].target, FrameworkLocalTarget)
        assert dependencies[0].target.local_key == _function_key_at(
            tmp_path,
            "providers.py",
            "shared",
        )
    assert any(
        event.path == "app.py"
        and event.reason_code == "framework_config_unresolved"
        and event.outcome is CoverageOutcome.PARTIAL
        for event in adapter.coverage_events(context)
    )


def test_dependency_destructuring_pairs_only_exact_finite_shapes(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from typing import Annotated
from fastapi import Depends, FastAPI

def shared(): return None

(marker, (DependencyAlias, plain)) = (
    Depends(shared),
    (Annotated[str, Depends(shared)], 1),
)

mismatched = Depends(shared)
(mismatched, other) = (Depends(shared),)

starred = Depends(shared)
(starred, *rest) = (Depends(shared), object())

dynamic = Depends(shared)
(dynamic, other_dynamic) = build_bindings()

app = FastAPI()

@app.get("/marker")
async def marker_route(value=marker): return None

@app.get("/alias")
async def alias_route(value: DependencyAlias): return None

@app.get("/mismatched")
async def mismatched_route(value=mismatched): return None

@app.get("/starred")
async def starred_route(value=starred): return None

@app.get("/dynamic")
async def dynamic_route(value=dynamic): return None
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    entrypoints = adapter.entrypoints(context, (_syntax(tmp_path, "app.py"),))

    assert {item.public_name for item in entrypoints} == {
        "marker_route",
        "alias_route",
        "starred_route",
    }
    for route in entrypoints:
        dependencies = [
            item
            for item in adapter.pipeline(context, route)
            if item.framework_role == "decorator_dependency"
        ]
        assert len(dependencies) == 1
        assert isinstance(dependencies[0].target, FrameworkLocalTarget)
        assert dependencies[0].target.local_key == _function_key(tmp_path, "shared")
    assert any(
        event.path == "app.py"
        and event.reason_code == "framework_config_unresolved"
        and event.outcome is CoverageOutcome.PARTIAL
        for event in adapter.coverage_events(context)
    )


def test_middleware_dict_aliases_use_python_key_and_overwrite_semantics(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "exact.py",
        """from fastapi import FastAPI

exact_app = FastAPI()

@exact_app.middleware("http")
async def duplicate_key(request, proceed):
    callbacks = {"next": replacement, "next": proceed}
    return await callbacks["next"](request)

@exact_app.middleware("http")
async def equal_keys(request, proceed):
    callbacks = {1: replacement, True: proceed}
    return await callbacks[1.0](request)

@exact_app.middleware("http")
async def none_key(request, proceed):
    callbacks = {None: proceed}
    return await callbacks[None](request)

@exact_app.get("/exact")
async def exact_endpoint(): return None
""",
    )
    _write(
        tmp_path,
        "escaped.py",
        """from fastapi import FastAPI

escaped_app = FastAPI()

@escaped_app.middleware("http")
async def mutated_dict(request, proceed):
    callbacks = {"next": proceed}
    callbacks["next"] = replacement
    return await callbacks["next"](request)

@escaped_app.get("/escaped")
async def escaped_endpoint(): return None
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    entrypoints = adapter.entrypoints(
        context,
        (_syntax(tmp_path, "exact.py"), _syntax(tmp_path, "escaped.py")),
    )

    def middleware(route_name: str, middleware_name: str):
        route = next(item for item in entrypoints if item.public_name == route_name)
        local_key = _function_key_at(
            tmp_path,
            "exact.py" if route_name == "exact_endpoint" else "escaped.py",
            middleware_name,
        )
        return next(
            item
            for item in adapter.pipeline(context, route)
            if item.framework_role == "middleware_request"
            and isinstance(item.target, FrameworkLocalTarget)
            and item.target.local_key == local_key
        )

    for name in ("duplicate_key", "equal_keys", "none_key"):
        assert not middleware("exact_endpoint", name).short_circuit_successors
    assert middleware(
        "escaped_endpoint",
        "mutated_dict",
    ).short_circuit_successors
    coverage = adapter.coverage_events(context)
    assert not any(
        event.path == "exact.py"
        and event.reason_code == "middleware_behavior_unresolved"
        for event in coverage
    )
    assert any(
        event.path == "escaped.py"
        and event.reason_code == "middleware_behavior_unresolved"
        and event.outcome is CoverageOutcome.PARTIAL
        for event in coverage
    )


def test_relative_imports_preserve_package_reexports_and_bound_cycles(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "pkg/providers.py",
        """from typing import Annotated
from fastapi import Depends

def shared(): return None
marker = Depends(shared)
DependencyAlias = Annotated[str, Depends(shared)]
class BaseError(Exception): pass
""",
    )
    _write(
        tmp_path,
        "pkg/__init__.py",
        """from .providers import BaseError as ExportedError
from .providers import DependencyAlias as ExportedAlias
from .providers import marker as exported_marker
""",
    )
    _write(
        tmp_path,
        "pkg/sub/__init__.py",
        """from .. import ExportedAlias, ExportedError, exported_marker
""",
    )
    _write(
        tmp_path,
        "pkg/cycle_a.py",
        """from .cycle_b import BError
class AError(BError): pass
""",
    )
    _write(
        tmp_path,
        "pkg/cycle_b.py",
        """from .cycle_a import AError
class BError(AError): pass
""",
    )
    _write(
        tmp_path,
        "pkg/sub/app.py",
        """from fastapi import FastAPI
from . import ExportedAlias, ExportedError, exported_marker
from ..cycle_a import AError

exact_app = FastAPI()
cycle_app = FastAPI()

@exact_app.exception_handler(ExportedError)
async def exported_handler(request, exc): return None

@cycle_app.exception_handler(AError)
async def cycle_handler(request, exc): return None

@exact_app.get("/marker")
async def marker_route(value=exported_marker): return None

@exact_app.get("/alias")
async def alias_route(value: ExportedAlias): return None

@exact_app.get("/error")
async def error_route(): raise ExportedError()

@cycle_app.get("/cycle")
async def cycle_route(): raise AError()
""",
    )
    paths = (
        "pkg/__init__.py",
        "pkg/cycle_a.py",
        "pkg/cycle_b.py",
        "pkg/providers.py",
        "pkg/sub/__init__.py",
        "pkg/sub/app.py",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    entrypoints = adapter.entrypoints(
        context,
        tuple(_syntax(tmp_path, path) for path in paths),
    )

    by_name = {item.public_name: item for item in entrypoints}
    assert {"marker_route", "alias_route", "error_route", "cycle_route"} <= set(by_name)
    for name in ("marker_route", "alias_route"):
        dependencies = [
            item
            for item in adapter.pipeline(context, by_name[name])
            if item.framework_role == "decorator_dependency"
        ]
        assert len(dependencies) == 1
        assert isinstance(dependencies[0].target, FrameworkLocalTarget)
        assert dependencies[0].target.local_key == _function_key_at(
            tmp_path,
            "pkg/providers.py",
            "shared",
        )

    exact_roles = {
        item.framework_role
        for item in adapter.pipeline(context, by_name["error_route"])
    }
    cycle_roles = {
        item.framework_role
        for item in adapter.pipeline(context, by_name["cycle_route"])
    }
    assert "exception_handler" in exact_roles
    assert "exception_handler_resolution_boundary" not in exact_roles
    assert "exception_handler" not in cycle_roles
    assert "exception_handler_resolution_boundary" in cycle_roles


def test_custom_app_lifespan_retains_router_events_per_include_snapshot(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from contextlib import asynccontextmanager
from fastapi import APIRouter, FastAPI

async def early_startup(): return None
async def late_startup(): return None
async def router_shutdown(): return None
async def lifespan_router_startup(): return None
async def root_startup(): return None
async def root_shutdown(): return None

@asynccontextmanager
async def app_lifespan(app):
    yield

@asynccontextmanager
async def router_lifespan(app):
    yield

default_router = APIRouter(
    on_startup=[early_startup],
    on_shutdown=[router_shutdown],
)
lifespan_router = APIRouter(
    lifespan=router_lifespan,
    on_startup=[lifespan_router_startup],
)
app = FastAPI(
    lifespan=app_lifespan,
    on_startup=[root_startup],
    on_shutdown=[root_shutdown],
)

app.include_router(default_router)
default_router.add_event_handler("startup", late_startup)
app.include_router(default_router)
app.include_router(lifespan_router)
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    entrypoints = adapter.entrypoints(context, (_syntax(tmp_path, "app.py"),))

    event_keys = [
        item.handler_local_key
        for item in entrypoints
        if item.kind is EntrypointKind.EVENT_LISTENER
    ]
    assert event_keys.count(_function_key(tmp_path, "early_startup")) == 2
    assert event_keys.count(_function_key(tmp_path, "late_startup")) == 2
    assert event_keys.count(_function_key(tmp_path, "router_shutdown")) == 2
    assert event_keys.count(_function_key(tmp_path, "lifespan_router_startup")) == 0
    assert _function_key(tmp_path, "root_startup") not in event_keys
    assert _function_key(tmp_path, "root_shutdown") not in event_keys

    lifespan_keys = [
        item.handler_local_key
        for item in entrypoints
        if item.kind is EntrypointKind.PROCESS_MAIN
    ]
    assert lifespan_keys.count(_function_key(tmp_path, "app_lifespan")) == 1
    assert lifespan_keys.count(_function_key(tmp_path, "router_lifespan")) == 1


def test_qualified_dependencies_require_reachable_parsed_module_namespaces(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "providers.py",
        """from typing import Annotated
from fastapi import Depends

def shared(): return None
marker = Depends(shared)
DependencyAlias = Annotated[str, Depends(shared)]
namespace = build_namespace()
""",
    )
    _write(
        tmp_path,
        "app.py",
        """import external_dependencies as external
import providers as parsed
from fastapi import FastAPI
from providers import namespace as opaque

app = FastAPI()

@app.get("/parsed-marker")
async def parsed_marker(value=parsed.marker): return None

@app.get("/parsed-alias")
async def parsed_alias(value: parsed.DependencyAlias): return None

@app.get("/external")
async def external_marker(value=external.marker): return None

@app.get("/opaque")
async def opaque_alias(value: opaque.DependencyAlias): return None
""",
    )
    _write(
        tmp_path,
        "cycle_a.py",
        """from fastapi import Depends
import cycle_b

def shared(): return None
marker = Depends(shared)
""",
    )
    _write(
        tmp_path,
        "cycle_b.py",
        """import cycle_a as partial
from fastapi import FastAPI

app = FastAPI()

@app.get("/cycle")
async def cycle_marker(value=partial.marker): return None
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    entrypoints = adapter.entrypoints(
        context,
        tuple(
            _syntax(tmp_path, path)
            for path in ("app.py", "cycle_a.py", "cycle_b.py", "providers.py")
        ),
    )

    assert {item.public_name for item in entrypoints} == {
        "parsed_marker",
        "parsed_alias",
    }
    for route in entrypoints:
        dependencies = [
            item
            for item in adapter.pipeline(context, route)
            if item.framework_role == "decorator_dependency"
        ]
        assert len(dependencies) == 1
        assert isinstance(dependencies[0].target, FrameworkLocalTarget)
        assert dependencies[0].target.local_key == _function_key_at(
            tmp_path,
            "providers.py",
            "shared",
        )
    coverage = adapter.coverage_events(context)
    for path in ("app.py", "cycle_b.py"):
        assert any(
            event.path == path
            and event.reason_code == "framework_config_unresolved"
            and event.outcome is CoverageOutcome.PARTIAL
            for event in coverage
        )


def test_dependency_destructuring_pairs_one_finite_starred_capture(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "app.py",
        """from typing import Annotated
from fastapi import Depends, FastAPI

def shared(): return None

(marker, *captured, DependencyAlias) = (
    Depends(shared),
    1,
    2,
    Annotated[str, Depends(shared)],
)

incompatible = Depends(shared)
(incompatible, *short_middle, short_last) = (Depends(shared),)

dynamic = Depends(shared)
(dynamic, *dynamic_middle) = build_bindings()

multiple = Depends(shared)
(multiple, *outer, (nested, *inner)) = (
    Depends(shared),
    (Depends(shared), 1),
)

app = FastAPI()

@app.get("/marker")
async def marker_route(value=marker): return None

@app.get("/alias")
async def alias_route(value: DependencyAlias): return None

@app.get("/captured")
async def captured_route(value=captured): return None

@app.get("/incompatible")
async def incompatible_route(value=incompatible): return None

@app.get("/dynamic")
async def dynamic_route(value=dynamic): return None

@app.get("/multiple")
async def multiple_route(value=multiple): return None
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    entrypoints = adapter.entrypoints(context, (_syntax(tmp_path, "app.py"),))

    by_name = {item.public_name: item for item in entrypoints}
    assert set(by_name) == {"marker_route", "alias_route", "captured_route"}
    for name in ("marker_route", "alias_route"):
        dependencies = [
            item
            for item in adapter.pipeline(context, by_name[name])
            if item.framework_role == "decorator_dependency"
        ]
        assert len(dependencies) == 1
        assert isinstance(dependencies[0].target, FrameworkLocalTarget)
        assert dependencies[0].target.local_key == _function_key(tmp_path, "shared")
    assert not any(
        item.framework_role == "decorator_dependency"
        for item in adapter.pipeline(context, by_name["captured_route"])
    )
    assert any(
        event.path == "app.py"
        and event.reason_code == "framework_config_unresolved"
        and event.outcome is CoverageOutcome.PARTIAL
        for event in adapter.coverage_events(context)
    )


def test_middleware_nested_container_mutations_are_behavior_boundaries(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "exact.py",
        """from fastapi import FastAPI

app = FastAPI()

@app.middleware("http")
async def stable(request, proceed):
    callbacks = [proceed]
    return await callbacks[0](request)

@app.get("/exact")
async def exact_endpoint(): return None
""",
    )
    _write(
        tmp_path,
        "mutated.py",
        """from fastapi import FastAPI

app = FastAPI()

@app.middleware("http")
async def conditional_subscript(request, proceed):
    callbacks = [proceed]
    if request:
        callbacks[0] = replacement
    return await callbacks[0](request)

@app.middleware("http")
async def conditional_attribute(request, proceed):
    next_step = proceed
    try:
        next_step.callback = replacement
    except AttributeError:
        pass
    return await next_step(request)

@app.middleware("http")
async def loop_delete(request, proceed):
    callbacks = [proceed]
    for _item in request:
        del callbacks[0]
    return await callbacks[0](request)

@app.get("/mutated")
async def mutated_endpoint(): return None
""",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    entrypoints = adapter.entrypoints(
        context,
        (_syntax(tmp_path, "exact.py"), _syntax(tmp_path, "mutated.py")),
    )

    def middleware(route_name: str, path: str, middleware_name: str):
        route = next(item for item in entrypoints if item.public_name == route_name)
        local_key = _function_key_at(tmp_path, path, middleware_name)
        return next(
            item
            for item in adapter.pipeline(context, route)
            if item.framework_role == "middleware_request"
            and isinstance(item.target, FrameworkLocalTarget)
            and item.target.local_key == local_key
        )

    assert not middleware(
        "exact_endpoint",
        "exact.py",
        "stable",
    ).short_circuit_successors
    for name in ("conditional_subscript", "conditional_attribute", "loop_delete"):
        assert middleware(
            "mutated_endpoint",
            "mutated.py",
            name,
        ).short_circuit_successors
    coverage = adapter.coverage_events(context)
    assert not any(
        event.path == "exact.py"
        and event.reason_code == "middleware_behavior_unresolved"
        for event in coverage
    )
    assert any(
        event.path == "mutated.py"
        and event.reason_code == "middleware_behavior_unresolved"
        and event.outcome is CoverageOutcome.PARTIAL
        for event in coverage
    )


def test_wildcard_imports_resolve_finite_exports_and_bound_unknowns(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "pkg/providers.py",
        """from typing import Annotated
from fastapi import Depends

__all__ = ["marker", "DependencyAlias", "ExportedError"]

def shared(): return None
marker = Depends(shared)
DependencyAlias = Annotated[str, Depends(shared)]
class ExportedError(Exception): pass
""",
    )
    _write(
        tmp_path,
        "pkg/__init__.py",
        """from .providers import *
""",
    )
    _write(
        tmp_path,
        "public_provider.py",
        """from fastapi import Depends

def public_shared(): return None
def private_shared(): return None
public_marker = Depends(public_shared)
_private_marker = Depends(private_shared)
""",
    )
    _write(
        tmp_path,
        "dynamic_provider.py",
        """from fastapi import Depends

__all__ = build_exports()
def dynamic_shared(): return None
dynamic_marker = Depends(dynamic_shared)
""",
    )
    _write(
        tmp_path,
        "cycle_a.py",
        """from fastapi import Depends
from cycle_b import *

__all__ = ["cycle_marker"]
def cycle_shared(): return None
cycle_marker = Depends(cycle_shared)
""",
    )
    _write(
        tmp_path,
        "cycle_b.py",
        """from cycle_a import *
from fastapi import FastAPI

app = FastAPI()

@app.get("/cycle")
async def cycle_route(value=cycle_marker): return None
""",
    )
    _write(
        tmp_path,
        "app.py",
        """from fastapi import FastAPI
from pkg import *
from public_provider import *

app = FastAPI()

@app.exception_handler(ExportedError)
async def exported_handler(request, exc): return None

@app.get("/marker")
async def marker_route(value=marker): return None

@app.get("/alias")
async def alias_route(value: DependencyAlias): return None

@app.get("/public")
async def public_route(value=public_marker): return None

@app.get("/error")
async def error_route(): raise ExportedError()

marker = build_marker()

@app.get("/rebound")
async def rebound_route(value=marker): return None
""",
    )
    _write(
        tmp_path,
        "dynamic_app.py",
        """from dynamic_provider import *
import pkg as stable_namespace
from fastapi import FastAPI

app = FastAPI()

@app.get("/dynamic")
async def dynamic_route(value=dynamic_marker): return None

from dynamic_provider import *

@app.get("/dynamic-qualified")
async def dynamic_qualified_route(value=stable_namespace.marker): return None
""",
    )
    paths = (
        "app.py",
        "cycle_a.py",
        "cycle_b.py",
        "dynamic_app.py",
        "dynamic_provider.py",
        "pkg/__init__.py",
        "pkg/providers.py",
        "public_provider.py",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    entrypoints = adapter.entrypoints(
        context,
        tuple(_syntax(tmp_path, path) for path in paths),
    )

    by_name = {item.public_name: item for item in entrypoints}
    assert set(by_name) == {
        "marker_route",
        "alias_route",
        "public_route",
        "error_route",
    }
    expected_dependencies = {
        "marker_route": ("pkg/providers.py", "shared"),
        "alias_route": ("pkg/providers.py", "shared"),
        "public_route": ("public_provider.py", "public_shared"),
    }
    for name, (path, target_name) in expected_dependencies.items():
        dependencies = [
            item
            for item in adapter.pipeline(context, by_name[name])
            if item.framework_role == "decorator_dependency"
        ]
        assert len(dependencies) == 1
        assert isinstance(dependencies[0].target, FrameworkLocalTarget)
        assert dependencies[0].target.local_key == _function_key_at(
            tmp_path,
            path,
            target_name,
        )
    error_roles = {
        item.framework_role
        for item in adapter.pipeline(context, by_name["error_route"])
    }
    assert "exception_handler" in error_roles
    assert "exception_handler_resolution_boundary" not in error_roles
    coverage = adapter.coverage_events(context)
    for path in ("app.py", "cycle_b.py", "dynamic_app.py"):
        assert any(
            event.path == path
            and event.reason_code == "framework_config_unresolved"
            and event.outcome is CoverageOutcome.PARTIAL
            for event in coverage
        )


def test_included_router_events_separate_copies_from_lifespan_contexts(
    tmp_path: Path,
) -> None:
    _prepare(tmp_path)
    _write(
        tmp_path,
        "matrix.py",
        """from contextlib import asynccontextmanager
from fastapi import APIRouter, FastAPI

async def default_default_handler(): return None
async def default_custom_handler(): return None
async def custom_default_handler(): return None
async def custom_custom_handler(): return None

@asynccontextmanager
async def root_lifespan(app):
    yield

@asynccontextmanager
async def router_lifespan(app):
    yield

default_default = APIRouter(on_startup=[default_default_handler])
default_custom = APIRouter(
    lifespan=router_lifespan,
    on_startup=[default_custom_handler],
)
custom_default = APIRouter(on_startup=[custom_default_handler])
custom_custom = APIRouter(
    lifespan=router_lifespan,
    on_startup=[custom_custom_handler],
)

default_root = FastAPI()
default_root.include_router(default_default)
default_root.include_router(default_custom)

custom_root = FastAPI(lifespan=root_lifespan)
custom_root.include_router(custom_default)
custom_root.include_router(custom_custom)
""",
    )
    for path, root_lifespan, router_lifespan in (
        ("dynamic_default_default.py", None, None),
        ("dynamic_default_custom.py", None, "router_lifespan"),
        ("dynamic_custom_default.py", "root_lifespan", None),
        ("dynamic_custom_custom.py", "root_lifespan", "router_lifespan"),
    ):
        _write(
            tmp_path,
            path,
            """from contextlib import asynccontextmanager
from fastapi import APIRouter, FastAPI

@asynccontextmanager
async def root_lifespan(app):
    yield

@asynccontextmanager
async def router_lifespan(app):
    yield

router = APIRouter(
    on_startup=build_handlers(),
    lifespan=ROUTER_LIFESPAN,
)
app = FastAPI(lifespan=ROOT_LIFESPAN)
app.include_router(router)
""".replace(
                "ROUTER_LIFESPAN",
                router_lifespan or "None",
            ).replace(
                "ROOT_LIFESPAN",
                root_lifespan or "None",
            ),
        )
    paths = (
        "matrix.py",
        "dynamic_default_default.py",
        "dynamic_default_custom.py",
        "dynamic_custom_default.py",
        "dynamic_custom_custom.py",
    )
    adapter = FastAPILifecycleAdapter()
    context = _context(tmp_path)
    entrypoints = adapter.entrypoints(
        context,
        tuple(_syntax(tmp_path, path) for path in paths),
    )

    event_keys = [
        item.handler_local_key
        for item in entrypoints
        if item.kind is EntrypointKind.EVENT_LISTENER
    ]
    assert (
        event_keys.count(
            _function_key_at(tmp_path, "matrix.py", "default_default_handler")
        )
        == 2
    )
    assert (
        event_keys.count(
            _function_key_at(tmp_path, "matrix.py", "default_custom_handler")
        )
        == 1
    )
    assert (
        event_keys.count(
            _function_key_at(tmp_path, "matrix.py", "custom_default_handler")
        )
        == 1
    )
    assert (
        event_keys.count(
            _function_key_at(tmp_path, "matrix.py", "custom_custom_handler")
        )
        == 0
    )

    coverage = adapter.coverage_events(context)
    for path in (
        "dynamic_default_default.py",
        "dynamic_default_custom.py",
        "dynamic_custom_default.py",
    ):
        assert any(
            event.path == path
            and event.reason_code == "framework_config_unresolved"
            and event.outcome is CoverageOutcome.PARTIAL
            for event in coverage
        )
    assert not any(
        event.path == "dynamic_custom_custom.py"
        and event.reason_code == "framework_config_unresolved"
        for event in coverage
    )
