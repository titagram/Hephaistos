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
