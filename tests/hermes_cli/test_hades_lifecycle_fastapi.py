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
    starlette_version: str | None = "0.37.2",
) -> ExtractionContext:
    metadata = _location(root, "pyproject.toml")
    frameworks = [
        FrameworkRecord(
            language="python",
            name="fastapi",
            version="0.115.0",
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
    assert {"startup", "shutdown", "app_lifespan"} <= event_names


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
