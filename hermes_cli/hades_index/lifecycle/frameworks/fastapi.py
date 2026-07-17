"""Static, privacy-safe FastAPI request-lifecycle extraction.

The adapter only interprets Python source already exposed by
``ExtractionContext.file_accessor``.  It deliberately never imports an ASGI
application, evaluates a factory, resolves package metadata over the network,
or guesses a computed router/dependency/middleware order.

FastAPI copies a router's routes when ``include_router`` executes.  The
extractor mirrors that finite, static registration model for local source and
for statically imported routers, retaining every order that is provable.  A
dynamic expression or an unsupported Starlette-version ordering is represented
as partial coverage and a bounded framework boundary, never as an invented
request path.
"""

from __future__ import annotations

import ast
import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType

from hermes_cli.hades_graph_v2.identity import normalize_source_path
from hermes_cli.hades_graph_v2.model import (
    EntrypointKind,
    EvidenceOrigin,
    MethodSemantics,
    TriggerKind,
)
from hermes_cli.hades_graph_v2.schema import GraphContractError
from hermes_cli.hades_index.lifecycle.frameworks import FrameworkDetection
from hermes_cli.hades_index.lifecycle.model import (
    AlwaysSuccessor,
    AsyncDispatchKind,
    AsyncSuccessor,
    AstLocatorIR,
    CoverageCapability,
    CoverageEvent,
    CoverageOutcome,
    EntrypointCandidate,
    ExceptionSuccessor,
    ExtractionContext,
    FrameworkBoundaryDescriptor,
    FrameworkBoundaryTarget,
    FrameworkLocalTarget,
    FrameworkPipelineSegment,
    IREvidence,
    MatchConstraints,
    ReturnSuccessor,
    SourceLocationIR,
    Successor,
    local_record_key,
)
from hermes_cli.hades_index.tree_sitter_adapter import StructuralSymbol, SyntaxIR


_PYPROJECT_FILES = ("pyproject.toml", "requirements.txt", "requirements/base.txt")
_DOTTED_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
_HTTP_DECORATORS = frozenset({
    "get",
    "post",
    "put",
    "patch",
    "delete",
    "options",
    "head",
    "trace",
})
_ROUTE_DECORATORS = _HTTP_DECORATORS | {"api_route", "route"}

# These are the Starlette series for which this adapter has an explicit,
# source-reviewed registration rule: user middleware are appended and the
# application is wrapped in reverse list order, so request entry is reverse
# registration and response exit unwinds registration order.  A new series is
# intentionally a boundary until that contract is reviewed and added here.
_STARLETTE_REVERSE_REGISTRATION_SERIES = frozenset({
    (0, 37),
    (0, 38),
    (0, 39),
    (0, 40),
    (0, 41),
    (0, 42),
    (0, 43),
    (0, 44),
    (0, 45),
})


@dataclass(frozen=True, slots=True)
class _Dependency:
    raw_target: str
    module: str
    cache: bool
    source_path: str
    line: int

    @property
    def identity(self) -> tuple[str, str]:
        return (self.module, self.raw_target)


@dataclass(frozen=True, slots=True)
class _Object:
    key: str
    module: str
    name: str
    kind: str
    prefix: str | None
    dependencies: tuple[_Dependency, ...]
    lifespan: str | None
    source_path: str
    line: int
    order: tuple[str, int, int]
    unresolved: bool


@dataclass(frozen=True, slots=True)
class _Include:
    parent: str
    child: str | None
    prefix: str | None
    dependencies: tuple[_Dependency, ...]
    source_path: str
    line: int
    order: tuple[str, int, int]
    unresolved: bool


@dataclass(frozen=True, slots=True)
class _Route:
    owner: str
    path: str | None
    methods: tuple[str, ...] | None
    dependencies: tuple[_Dependency, ...]
    handler_module: str
    handler_name: str
    response_model: bool
    source_path: str
    line: int
    order: tuple[str, int, int]
    ordinal: int
    unresolved: bool


@dataclass(frozen=True, slots=True)
class _Middleware:
    owner: str
    public_name: str
    local_key: str | None
    may_short_circuit: bool
    source_path: str
    line: int
    order: tuple[str, int, int]


@dataclass(frozen=True, slots=True)
class _ExceptionHandler:
    owner: str
    exception_name: str | None
    local_key: str | None
    public_name: str
    source_path: str
    line: int
    order: tuple[str, int, int]


@dataclass(frozen=True, slots=True)
class _Event:
    owner: str
    event: str
    handler_module: str
    handler_name: str
    source_path: str
    line: int
    ordinal: int


@dataclass(frozen=True, slots=True)
class _FunctionOutcome:
    is_async: bool
    has_yield: bool
    raised_types: tuple[str | None, ...]
    has_request_validation: bool
    background_targets: tuple[str, ...]

    @property
    def raises(self) -> bool:
        return bool(self.raised_types)


@dataclass(frozen=True, slots=True)
class _ResolvedRoute:
    route: _Route
    public_path: str
    dependencies: tuple[tuple[str, _Dependency], ...]
    app_key: str


@dataclass(frozen=True, slots=True)
class _Snapshot:
    routes: Mapping[tuple[str, str, int], _ResolvedRoute]
    outcomes: Mapping[tuple[str, str], _FunctionOutcome]
    function_keys: Mapping[tuple[str, str], str]
    imports: Mapping[str, Mapping[str, str]]
    middleware: Mapping[str, tuple[_Middleware, ...]]
    exception_handlers: Mapping[str, tuple[_ExceptionHandler, ...]]
    event_candidates: tuple[EntrypointCandidate, ...]
    middleware_order_proven: bool
    cleanup_before_background_proven: bool
    coverage_events: tuple[CoverageEvent, ...]


@dataclass(slots=True)
class _Diagnostics:
    records: set[tuple[str | None, CoverageCapability, str]]

    def mark(
        self,
        path: str | None,
        capability: CoverageCapability,
        reason_code: str,
    ) -> None:
        safe = _safe_path(path) if path is not None else None
        self.records.add((safe, capability, reason_code))

    def events(self) -> tuple[CoverageEvent, ...]:
        return tuple(
            CoverageEvent(
                "python",
                capability,
                CoverageOutcome.PARTIAL,
                reason,
                path,
                0,
                1,
            )
            for path, capability, reason in sorted(
                self.records,
                key=lambda item: (
                    item[0] or "",
                    item[1].value,
                    item[2],
                ),
            )
        )


def _safe_path(path: str | None) -> str | None:
    if not isinstance(path, str) or not path:
        return None
    try:
        return normalize_source_path(path)
    except (GraphContractError, TypeError):
        return None


def _text(context: ExtractionContext, path: str) -> str | None:
    safe = _safe_path(path)
    if safe is None:
        return None
    try:
        return context.file_accessor(Path(safe)).decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _tree(context: ExtractionContext, path: str) -> tuple[str, ast.Module] | None:
    source = _text(context, path)
    if source is None:
        return None
    try:
        return source, ast.parse(source, filename=path)
    except SyntaxError:
        return None


def _module_name(path: str) -> str | None:
    safe = _safe_path(path)
    if safe is None or not safe.endswith(".py"):
        return None
    parts = safe[:-3].split("/")
    if parts[-1] == "__init__":
        parts.pop()
    module = ".".join(parts)
    return module if module and _DOTTED_RE.fullmatch(module) else None


def _digest(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _locator(
    path: str, source: str, line: int, structural_path: str, ordinal: int
) -> AstLocatorIR:
    safe_line = max(1, line)
    return AstLocatorIR(
        SourceLocationIR(path, safe_line, safe_line, _digest(source)),
        structural_path,
        ordinal,
    )


def _dotted(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted(node.value)
        return f"{parent}.{node.attr}" if parent else None
    return None


def _literal_string(node: ast.AST | None) -> str | None:
    return (
        node.value
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        else None
    )


def _keyword(call: ast.Call, name: str) -> ast.AST | None:
    return next((item.value for item in call.keywords if item.arg == name), None)


def _call_name(call: ast.Call) -> str | None:
    return _dotted(call.func)


def _line_order(path: str, node: ast.AST) -> tuple[str, int, int]:
    return (path, int(getattr(node, "lineno", 0)), int(getattr(node, "col_offset", 0)))


def _join_path(*parts: str) -> str:
    values = [part.strip("/") for part in parts if part and part != "/"]
    return "/" + "/".join(values) if values else "/"


def _http_methods(call: ast.Call, method: str) -> tuple[str, ...] | None:
    if method in _HTTP_DECORATORS:
        return (method.upper(),)
    if method == "route":
        return ()
    values = _keyword(call, "methods")
    if values is None and len(call.args) > 1:
        values = call.args[1]
    if not isinstance(values, (ast.List, ast.Tuple, ast.Set)):
        return None
    methods = tuple(
        item.value.upper()
        for item in values.elts
        if isinstance(item, ast.Constant)
        and isinstance(item.value, str)
        and re.fullmatch(r"[A-Za-z]+", item.value)
    )
    if len(methods) != len(values.elts) or not methods:
        return None
    return tuple(sorted(set(methods)))


def _response_model(call: ast.Call) -> bool | None:
    """Return whether a declared response model is statically meaningful.

    ``None`` is a deliberate FastAPI opt-out.  Names, attributes, subscripts,
    and string forward references are source-visible model declarations;
    computed factories are not silently treated as a serializer.
    """

    value = _keyword(call, "response_model")
    if value is None:
        return False
    if isinstance(value, ast.Constant):
        return False if value.value is None else isinstance(value.value, str)
    if isinstance(value, (ast.Name, ast.Attribute, ast.Subscript, ast.Tuple, ast.List)):
        return True
    return None


def _dependency_from(node: ast.AST, module: str, path: str) -> _Dependency | None:
    if not isinstance(node, ast.Call):
        return None
    name = _call_name(node)
    if name is None or name.rsplit(".", 1)[-1] not in {"Depends", "Security"}:
        return None
    if not node.args:
        return None
    target = _dotted(node.args[0])
    if target is None:
        return None
    use_cache = _keyword(node, "use_cache")
    cache = not (
        isinstance(use_cache, ast.Constant)
        and type(use_cache.value) is bool
        and use_cache.value is False
    )
    return _Dependency(target, module, cache, path, int(getattr(node, "lineno", 1)))


def _dependencies(
    node: ast.AST | None,
    module: str,
    path: str,
) -> tuple[_Dependency, ...] | None:
    if node is None:
        return ()
    if not isinstance(node, (ast.List, ast.Tuple)):
        return None
    values = tuple(_dependency_from(item, module, path) for item in node.elts)
    return values if all(value is not None for value in values) else None


def _parameter_dependencies(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    module: str,
    path: str,
) -> tuple[_Dependency, ...] | None:
    defaults = tuple(function.args.defaults) + tuple(
        item for item in function.args.kw_defaults if item is not None
    )
    found: list[_Dependency] = []
    for default in defaults:
        if not isinstance(default, ast.Call):
            continue
        name = _call_name(default)
        if name is None or name.rsplit(".", 1)[-1] not in {"Depends", "Security"}:
            continue
        dependency = _dependency_from(default, module, path)
        if dependency is None:
            return None
        found.append(dependency)
    return tuple(found)


def _has_yield(node: ast.AST) -> bool:
    return any(isinstance(item, (ast.Yield, ast.YieldFrom)) for item in ast.walk(node))


def _raised_types(node: ast.AST) -> tuple[str | None, ...]:
    """Return only explicit public exception class references.

    A bare raise or computed exception expression is retained as ``None`` so
    an exception-handler arm cannot be guessed from an unrelated registration.
    """

    values: list[str | None] = []
    for item in ast.walk(node):
        if not isinstance(item, ast.Raise):
            continue
        expression = item.exc
        if isinstance(expression, ast.Call):
            expression = expression.func
        values.append(_dotted(expression))
    return tuple(values)


def _has_request_validation(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Recognize request-bound annotations/defaults without treating framework objects as input."""

    framework_parameters = {"Request", "WebSocket", "BackgroundTasks", "Response"}
    for argument in (
        *function.args.posonlyargs,
        *function.args.args,
        *function.args.kwonlyargs,
    ):
        annotation = _dotted(argument.annotation)
        if (
            annotation is not None
            and annotation.rsplit(".", 1)[-1] not in framework_parameters
        ):
            return True
    for default in (
        *function.args.defaults,
        *(item for item in function.args.kw_defaults if item is not None),
    ):
        if not isinstance(default, ast.Call):
            continue
        name = _call_name(default)
        if name is not None and name.rsplit(".", 1)[-1] in {
            "Body",
            "Cookie",
            "Form",
            "Header",
            "Path",
            "Query",
        }:
            return True
    return False


def _background_targets(node: ast.AST) -> tuple[str, ...] | None:
    targets: list[str] = []
    for item in ast.walk(node):
        if not isinstance(item, ast.Call) or not isinstance(item.func, ast.Attribute):
            continue
        if item.func.attr != "add_task":
            continue
        if not item.args:
            return None
        target = _dotted(item.args[0])
        if target is None:
            return None
        targets.append(target)
    return tuple(targets)


def _function_key_index(syntax: Sequence[SyntaxIR]) -> Mapping[tuple[str, str], str]:
    values: dict[tuple[str, str], list[str]] = {}
    for item in syntax:
        if item.language != "python":
            continue
        module = _module_name(item.path)
        if module is None:
            continue
        for ordinal, symbol in enumerate(item.symbols):
            if symbol.kind != "function" or symbol.container:
                continue
            key = local_record_key(
                "python",
                item.path,
                "executable_declaration",
                "ast",
                f"symbol/{symbol.name}",
                ordinal,
            )
            values.setdefault((module, symbol.name), []).append(key)
    return MappingProxyType({
        item: keys[0] for item, keys in values.items() if len(keys) == 1
    })


def _imports_for(path: str, module: str, tree: ast.Module) -> Mapping[str, str]:
    bindings: dict[str, str] = {}
    parent = module.split(".")[:-1]
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".", 1)[0]
                bindings[local] = alias.name
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                levels = max(0, node.level - 1)
                if levels > len(parent):
                    continue
                prefix = parent[: len(parent) - levels]
                base = ".".join(prefix + ([base] if base else []))
            if not base or not _DOTTED_RE.fullmatch(base):
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                bindings[alias.asname or alias.name] = f"{base}.{alias.name}"
    return MappingProxyType(bindings)


def _resolved_reference(
    raw: str,
    module: str,
    imports: Mapping[str, Mapping[str, str]],
) -> tuple[str, str] | None:
    if not raw:
        return None
    head, separator, tail = raw.partition(".")
    binding = imports.get(module, {}).get(head)
    if binding:
        dotted = f"{binding}{separator}{tail}" if separator else binding
    elif separator:
        dotted = raw
    else:
        return (module, raw)
    if not _DOTTED_RE.fullmatch(dotted):
        return None
    if "." not in dotted:
        return None
    target_module, target_name = dotted.rsplit(".", 1)
    return (target_module, target_name)


def _object_reference(
    raw: str | None,
    module: str,
    imports: Mapping[str, Mapping[str, str]],
    objects: Mapping[str, _Object],
    *,
    occurrence: tuple[str, int, int],
    rebound_names: Mapping[tuple[str, str], tuple[tuple[str, int, int], ...]],
    duplicate_objects: frozenset[str],
) -> str | None:
    if raw is None:
        return None
    head = raw.split(".", 1)[0]
    if any(order <= occurrence for order in rebound_names.get((module, head), ())):
        return None
    target = _resolved_reference(raw, module, imports)
    if target is None:
        return None
    key = f"{target[0]}:{target[1]}"
    return key if key in objects and key not in duplicate_objects else None


def _assigned_names(node: ast.stmt) -> tuple[str, ...]:
    """Return direct top-level names rebound by one statement.

    This intentionally does not descend into control flow: a conditional,
    computed rebinding makes framework registration order unprovable and is
    handled by the caller as partial rather than as a synthetic branch.
    """

    targets: tuple[ast.AST, ...]
    if isinstance(node, ast.Assign):
        targets = tuple(node.targets)
    elif isinstance(node, ast.AnnAssign):
        targets = (node.target,)
    elif isinstance(node, ast.AugAssign):
        targets = (node.target,)
    elif isinstance(node, ast.Delete):
        targets = tuple(node.targets)
    else:
        return ()
    return tuple(target.id for target in targets if isinstance(target, ast.Name))


def _dependency_key(
    dependency: _Dependency,
    imports: Mapping[str, Mapping[str, str]],
    function_keys: Mapping[tuple[str, str], str],
) -> str | None:
    resolved = _resolved_reference(dependency.raw_target, dependency.module, imports)
    return function_keys.get(resolved) if resolved is not None else None


def _dependency_name(dependency: _Dependency) -> str:
    return dependency.raw_target.rsplit(".", 1)[-1]


def _candidate_key(candidate: EntrypointCandidate) -> tuple[str, str, int]:
    locator = candidate.registration_locator
    return (locator.source_location.path, locator.structural_path, locator.ordinal)


def _pipeline_key(candidate: EntrypointCandidate, role: str, ordinal: int) -> str:
    locator = candidate.registration_locator
    return local_record_key(
        "python",
        locator.source_location.path,
        "framework_pipeline",
        "ast",
        f"{locator.structural_path}/pipeline/{role}",
        ordinal,
    )


def _terminal_key(candidate: EntrypointCandidate, role: str, ordinal: int) -> str:
    locator = candidate.registration_locator
    return local_record_key(
        "python",
        locator.source_location.path,
        "framework_terminal",
        "ast",
        f"{locator.structural_path}/terminal/{role}",
        ordinal,
    )


def _exception_scope_key(candidate: EntrypointCandidate) -> str:
    locator = candidate.registration_locator
    return local_record_key(
        "python",
        locator.source_location.path,
        "framework_exception_scope",
        "ast",
        f"{locator.structural_path}/exception",
        locator.ordinal,
    )


def _version_tuple(value: str | None) -> tuple[int, int] | None:
    if value is None:
        return None
    match = re.fullmatch(r"(\d+)\.(\d+)(?:\.\d+)?", value)
    return (int(match.group(1)), int(match.group(2))) if match else None


def _detected_version(context: ExtractionContext, name: str) -> str | None:
    for record in context.detected_frameworks:
        if record.language == "python" and record.name == name and record.version:
            return record.version
    for path in _PYPROJECT_FILES:
        source = _text(context, path)
        if source is None:
            continue
        match = re.search(
            rf"(?i){re.escape(name)}\s*(?:[<>=!~^ ]+)\s*(\d+(?:\.\d+){{1,2}})",
            source,
        )
        if match:
            return match.group(1)
    return None


def _event_candidate(
    context: ExtractionContext,
    event: _Event,
    function_keys: Mapping[tuple[str, str], str],
    source: str,
) -> EntrypointCandidate:
    locator = _locator(
        event.source_path,
        source,
        event.line,
        f"fastapi/events/{event.event}/{event.ordinal}",
        event.ordinal,
    )
    handler = function_keys.get((event.handler_module, event.handler_name))
    unresolved = (
        None
        if handler is not None
        else local_record_key(
            "python",
            event.source_path,
            "unresolved_fact",
            "ast",
            f"fastapi/events/{event.event}/{event.ordinal}/handler",
            event.ordinal,
        )
    )
    evidence = IREvidence(
        EvidenceOrigin.VERIFIED_FROM_CODE if handler else EvidenceOrigin.UNRESOLVED,
        "fastapi.events",
        locator,
        None,
    )
    return EntrypointCandidate(
        EntrypointKind.EVENT_LISTENER,
        "fastapi",
        MethodSemantics.NOT_APPLICABLE,
        (),
        None,
        event.handler_name,
        TriggerKind.EVENT,
        event.event,
        MatchConstraints(None, (), None),
        locator,
        handler,
        unresolved,
        (),
        evidence,
    )


def _lifespan_candidate(
    app: _Object,
    function_keys: Mapping[tuple[str, str], str],
    imports: Mapping[str, Mapping[str, str]],
    source: str,
) -> EntrypointCandidate | None:
    if app.lifespan is None:
        return None
    locator = _locator(
        app.source_path,
        source,
        app.line,
        f"fastapi/lifespan/{app.name}",
        0,
    )
    lifespan = _resolved_reference(app.lifespan, app.module, imports)
    handler = function_keys.get(lifespan) if lifespan is not None else None
    unresolved = (
        None
        if handler is not None
        else local_record_key(
            "python",
            app.source_path,
            "unresolved_fact",
            "ast",
            f"fastapi/lifespan/{app.name}/handler",
            0,
        )
    )
    evidence = IREvidence(
        EvidenceOrigin.VERIFIED_FROM_CODE if handler else EvidenceOrigin.UNRESOLVED,
        "fastapi.lifespan",
        locator,
        None,
    )
    return EntrypointCandidate(
        EntrypointKind.PROCESS_MAIN,
        "fastapi",
        MethodSemantics.NOT_APPLICABLE,
        (),
        None,
        f"{app.name}_lifespan",
        TriggerKind.PROCESS,
        app.name,
        MatchConstraints(None, (), None),
        locator,
        handler,
        unresolved,
        (),
        evidence,
    )


def _route_candidate(
    route: _ResolvedRoute,
    function_keys: Mapping[tuple[str, str], str],
    source: str,
) -> EntrypointCandidate:
    spec = route.route
    locator = _locator(
        spec.source_path,
        source,
        spec.line,
        f"fastapi/routes/{spec.ordinal}",
        spec.ordinal,
    )
    handler = function_keys.get((spec.handler_module, spec.handler_name))
    unresolved = (
        None
        if handler is not None
        else local_record_key(
            "python",
            spec.source_path,
            "unresolved_fact",
            "ast",
            f"fastapi/routes/{spec.ordinal}/handler",
            spec.ordinal,
        )
    )
    evidence = IREvidence(
        EvidenceOrigin.VERIFIED_FROM_CODE if handler else EvidenceOrigin.UNRESOLVED,
        "fastapi.routes",
        locator,
        None,
    )
    methods = spec.methods or ()
    return EntrypointCandidate(
        EntrypointKind.HTTP_ROUTE,
        "fastapi",
        MethodSemantics.EXPLICIT if methods else MethodSemantics.UNRESTRICTED,
        methods,
        route.public_path,
        spec.handler_name,
        TriggerKind.HTTP,
        f"{','.join(methods) if methods else 'ALL'} {route.public_path}",
        MatchConstraints(None, (), None),
        locator,
        handler,
        unresolved,
        (),
        evidence,
    )


def _expand_routes(
    objects: Mapping[str, _Object],
    includes: Sequence[_Include],
    routes: Sequence[_Route],
    diagnostics: _Diagnostics,
    *,
    invalid_root_apps: frozenset[str],
) -> tuple[_ResolvedRoute, ...]:
    includes_by_parent: dict[str, list[_Include]] = {}
    routes_by_owner: dict[str, list[_Route]] = {}
    for item in includes:
        includes_by_parent.setdefault(item.parent, []).append(item)
    for item in routes:
        routes_by_owner.setdefault(item.owner, []).append(item)
    for rows in includes_by_parent.values():
        rows.sort(key=lambda item: item.order)
    for rows in routes_by_owner.values():
        rows.sort(key=lambda item: item.order)

    resolved: list[_ResolvedRoute] = []

    def walk(
        object_key: str,
        parent_prefix: str,
        inherited_dependencies: tuple[tuple[str, _Dependency], ...],
        cutoff: tuple[str, int, int] | None,
        app_key: str,
        ancestry: frozenset[str],
    ) -> None:
        current = objects.get(object_key)
        if current is None or current.unresolved or current.prefix is None:
            diagnostics.mark(
                current.source_path if current else None,
                CoverageCapability.ENTRYPOINT_DISCOVERY,
                "framework_config_unresolved",
            )
            return
        if object_key in ancestry:
            diagnostics.mark(
                current.source_path,
                CoverageCapability.ENTRYPOINT_DISCOVERY,
                "router_cycle_unresolved",
            )
            return
        current_prefix = _join_path(parent_prefix, current.prefix)
        role = "app_dependency" if current.kind == "app" else "router_dependency"
        dependencies = inherited_dependencies + tuple(
            (role, item) for item in current.dependencies
        )
        for route in routes_by_owner.get(object_key, ()):
            if (
                cutoff is not None
                and route.source_path == current.source_path
                and route.order > cutoff
            ):
                continue
            if route.unresolved or route.path is None or route.methods is None:
                diagnostics.mark(
                    route.source_path,
                    CoverageCapability.ENTRYPOINT_DISCOVERY,
                    "framework_config_unresolved",
                )
                continue
            resolved.append(
                _ResolvedRoute(
                    route,
                    _join_path(current_prefix, route.path),
                    dependencies
                    + tuple(
                        ("decorator_dependency", item) for item in route.dependencies
                    ),
                    app_key,
                )
            )
        for include in includes_by_parent.get(object_key, ()):
            child = objects.get(include.child or "")
            if (
                include.unresolved
                or include.prefix is None
                or child is None
                or child.kind != "router"
            ):
                diagnostics.mark(
                    include.source_path,
                    CoverageCapability.ENTRYPOINT_DISCOVERY,
                    "framework_config_unresolved",
                )
                continue
            next_cutoff = include.order if child.module == current.module else None
            walk(
                child.key,
                _join_path(current_prefix, include.prefix),
                dependencies
                + tuple(("route_dependency", item) for item in include.dependencies),
                next_cutoff,
                app_key,
                ancestry | {object_key},
            )

    for app in sorted(
        (
            item
            for item in objects.values()
            if item.kind == "app" and item.key not in invalid_root_apps
        ),
        key=lambda item: item.order,
    ):
        walk(app.key, "", (), None, app.key, frozenset())
    return tuple(
        sorted(
            resolved,
            key=lambda item: (
                item.route.order,
                item.public_path,
                item.route.handler_name,
            ),
        )
    )


class FastAPILifecycleAdapter:
    """Extract FastAPI's static routes and finite request lifecycle facts."""

    language = "python"
    framework = "fastapi"

    def __init__(self) -> None:
        self._snapshots: Mapping[tuple[str, str, str, str], _Snapshot] = (
            MappingProxyType({})
        )

    @staticmethod
    def _snapshot_key(context: ExtractionContext) -> tuple[str, str, str, str]:
        return (
            str(context.workspace_root),
            context.project_id,
            context.workspace_binding_id,
            context.source_identity.tree_sha256,
        )

    def _snapshot(self, context: ExtractionContext) -> _Snapshot:
        return self._snapshots.get(
            self._snapshot_key(context),
            _Snapshot(
                MappingProxyType({}),
                MappingProxyType({}),
                MappingProxyType({}),
                MappingProxyType({}),
                MappingProxyType({}),
                MappingProxyType({}),
                (),
                False,
                False,
                (),
            ),
        )

    def _remember(self, context: ExtractionContext, snapshot: _Snapshot) -> None:
        values = dict(self._snapshots)
        values[self._snapshot_key(context)] = snapshot
        self._snapshots = MappingProxyType(values)

    def detected_version(self, context: ExtractionContext) -> str | None:
        return _detected_version(context, "fastapi")

    def detected_starlette_version(self, context: ExtractionContext) -> str | None:
        return _detected_version(context, "starlette")

    def detect(self, context: ExtractionContext) -> FrameworkDetection:
        detected = (
            any(
                record.language == "python" and record.name == "fastapi"
                for record in context.detected_frameworks
            )
            or self.detected_version(context) is not None
        )
        return FrameworkDetection("python", "fastapi", detected)

    def coverage_events(self, context: ExtractionContext) -> tuple[CoverageEvent, ...]:
        return self._snapshot(context).coverage_events

    def entrypoints(
        self, context: ExtractionContext, syntax: Sequence[SyntaxIR]
    ) -> tuple[EntrypointCandidate, ...]:
        diagnostics = _Diagnostics(set())
        syntax = tuple(item for item in syntax if item.language == "python")
        function_keys = _function_key_index(syntax)
        parsed: dict[str, tuple[str, ast.Module, str]] = {}
        imports: dict[str, Mapping[str, str]] = {}
        for item in sorted(syntax, key=lambda value: value.path):
            module = _module_name(item.path)
            tree = _tree(context, item.path)
            if module is None or tree is None:
                diagnostics.mark(
                    item.path,
                    CoverageCapability.ENTRYPOINT_DISCOVERY,
                    "framework_config_unresolved",
                )
                continue
            source, parsed_tree = tree
            parsed[item.path] = (source, parsed_tree, module)
            imports[module] = _imports_for(item.path, module, parsed_tree)

        objects: dict[str, _Object] = {}
        duplicate_objects: set[str] = set()
        rebound_names: dict[tuple[str, str], list[tuple[str, int, int]]] = {}
        outcomes: dict[tuple[str, str], _FunctionOutcome] = {}
        function_nodes: list[
            tuple[str, str, str, ast.FunctionDef | ast.AsyncFunctionDef]
        ] = []
        for path, (source, tree, module) in parsed.items():
            for node in tree.body:
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                    call_name = _call_name(node.value)
                    kind = (
                        "app"
                        if call_name and call_name.rsplit(".", 1)[-1] == "FastAPI"
                        else "router"
                        if call_name and call_name.rsplit(".", 1)[-1] == "APIRouter"
                        else None
                    )
                    if kind is None:
                        for name in _assigned_names(node):
                            rebound_names.setdefault((module, name), []).append(
                                _line_order(path, node)
                            )
                        continue
                    targets = [
                        target.id
                        for target in node.targets
                        if isinstance(target, ast.Name)
                    ]
                    if len(targets) != 1:
                        diagnostics.mark(
                            path,
                            CoverageCapability.ENTRYPOINT_DISCOVERY,
                            "framework_config_unresolved",
                        )
                        continue
                    prefix_node = _keyword(node.value, "prefix")
                    prefix = "" if kind == "app" else _literal_string(prefix_node)
                    dependencies = _dependencies(
                        _keyword(node.value, "dependencies"), module, path
                    )
                    lifespan = (
                        _dotted(_keyword(node.value, "lifespan"))
                        if kind == "app"
                        else None
                    )
                    unresolved = prefix is None or dependencies is None
                    if (
                        kind == "app"
                        and _keyword(node.value, "lifespan") is not None
                        and lifespan is None
                    ):
                        unresolved = True
                    name = targets[0]
                    key = f"{module}:{name}"
                    if key in objects:
                        duplicate_objects.add(key)
                    objects[key] = _Object(
                        key,
                        module,
                        name,
                        kind,
                        prefix,
                        dependencies or (),
                        lifespan,
                        path,
                        node.lineno,
                        _line_order(path, node),
                        unresolved,
                    )
                    continue
                for name in _assigned_names(node):
                    rebound_names.setdefault((module, name), []).append(
                        _line_order(path, node)
                    )
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    background_targets = _background_targets(node)
                    if background_targets is None:
                        diagnostics.mark(
                            path,
                            CoverageCapability.ASYNC,
                            "background_task_unresolved",
                        )
                    outcome = _FunctionOutcome(
                        isinstance(node, ast.AsyncFunctionDef),
                        _has_yield(node),
                        _raised_types(node),
                        _has_request_validation(node),
                        background_targets or (),
                    )
                    outcomes[(module, node.name)] = outcome
                    function_nodes.append((path, source, module, node))

        rebound_view = MappingProxyType({
            key: tuple(sorted(value)) for key, value in rebound_names.items()
        })
        duplicate_view = frozenset(duplicate_objects)
        for object_key in duplicate_view:
            diagnostics.mark(
                objects[object_key].source_path,
                CoverageCapability.ENTRYPOINT_DISCOVERY,
                "framework_object_rebound",
            )

        includes: list[_Include] = []
        routes: list[_Route] = []
        middleware: dict[str, list[_Middleware]] = {}
        exception_handlers: dict[str, list[_ExceptionHandler]] = {}
        events: list[_Event] = []
        route_ordinal = 0
        event_ordinal = 0

        for path, source, module, function in function_nodes:
            parameter_dependencies = _parameter_dependencies(function, module, path)
            for decorator in function.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(
                    decorator.func, ast.Attribute
                ):
                    continue
                owner = _object_reference(
                    _dotted(decorator.func.value),
                    module,
                    imports,
                    objects,
                    occurrence=_line_order(path, decorator),
                    rebound_names=rebound_view,
                    duplicate_objects=duplicate_view,
                )
                if owner is None:
                    continue
                method = decorator.func.attr
                if method in _ROUTE_DECORATORS:
                    path_node = (
                        decorator.args[0]
                        if decorator.args
                        else _keyword(decorator, "path")
                    )
                    public_path = _literal_string(path_node)
                    methods = _http_methods(decorator, method)
                    dependencies = _dependencies(
                        _keyword(decorator, "dependencies"), module, path
                    )
                    response_model = _response_model(decorator)
                    unresolved = (
                        public_path is None
                        or methods is None
                        or dependencies is None
                        or parameter_dependencies is None
                        or response_model is None
                    )
                    routes.append(
                        _Route(
                            owner,
                            public_path,
                            methods,
                            (dependencies or ()) + (parameter_dependencies or ()),
                            module,
                            function.name,
                            response_model is True,
                            path,
                            decorator.lineno,
                            _line_order(path, decorator),
                            route_ordinal,
                            unresolved,
                        )
                    )
                    route_ordinal += 1
                elif method == "middleware":
                    transport = _literal_string(
                        decorator.args[0] if decorator.args else None
                    )
                    if transport != "http":
                        diagnostics.mark(
                            path,
                            CoverageCapability.FRAMEWORK_LIFECYCLE,
                            "framework_config_unresolved",
                        )
                        continue
                    local = function_keys.get((module, function.name))
                    calls_next = any(
                        isinstance(item, ast.Call)
                        and _dotted(item.func)
                        and _dotted(item.func).rsplit(".", 1)[-1] == "call_next"
                        for item in ast.walk(function)
                    )
                    middleware.setdefault(owner, []).append(
                        _Middleware(
                            owner,
                            function.name,
                            local,
                            not calls_next,
                            path,
                            decorator.lineno,
                            _line_order(path, decorator),
                        )
                    )
                elif method == "exception_handler":
                    exception_name = _dotted(
                        decorator.args[0] if decorator.args else None
                    )
                    exception_handlers.setdefault(owner, []).append(
                        _ExceptionHandler(
                            owner,
                            exception_name,
                            function_keys.get((module, function.name)),
                            function.name,
                            path,
                            decorator.lineno,
                            _line_order(path, decorator),
                        )
                    )
                elif method == "on_event":
                    event = _literal_string(
                        decorator.args[0] if decorator.args else None
                    )
                    if event not in {"startup", "shutdown"}:
                        diagnostics.mark(
                            path,
                            CoverageCapability.FRAMEWORK_LIFECYCLE,
                            "framework_config_unresolved",
                        )
                        continue
                    events.append(
                        _Event(
                            owner,
                            event,
                            module,
                            function.name,
                            path,
                            decorator.lineno,
                            event_ordinal,
                        )
                    )
                    event_ordinal += 1

        for path, (source, tree, module) in parsed.items():
            for node in tree.body:
                if not isinstance(node, ast.Expr) or not isinstance(
                    node.value, ast.Call
                ):
                    continue
                call = node.value
                if not isinstance(call.func, ast.Attribute):
                    continue
                owner = _object_reference(
                    _dotted(call.func.value),
                    module,
                    imports,
                    objects,
                    occurrence=_line_order(path, node),
                    rebound_names=rebound_view,
                    duplicate_objects=duplicate_view,
                )
                if owner is None:
                    continue
                method = call.func.attr
                if method == "include_router":
                    child = _object_reference(
                        _dotted(call.args[0] if call.args else None),
                        module,
                        imports,
                        objects,
                        occurrence=_line_order(path, node),
                        rebound_names=rebound_view,
                        duplicate_objects=duplicate_view,
                    )
                    prefix_node = _keyword(call, "prefix")
                    prefix = "" if prefix_node is None else _literal_string(prefix_node)
                    dependencies = _dependencies(
                        _keyword(call, "dependencies"), module, path
                    )
                    includes.append(
                        _Include(
                            owner,
                            child,
                            prefix,
                            dependencies or (),
                            path,
                            node.lineno,
                            _line_order(path, node),
                            child is None or prefix is None or dependencies is None,
                        )
                    )
                elif method in {"add_api_route", "add_route"}:
                    path_node = call.args[0] if call.args else _keyword(call, "path")
                    endpoint = _dotted(
                        call.args[1]
                        if len(call.args) > 1
                        else _keyword(call, "endpoint")
                    )
                    public_path = _literal_string(path_node)
                    methods = _http_methods(
                        call, "api_route" if method == "add_api_route" else "route"
                    )
                    dependencies = _dependencies(
                        _keyword(call, "dependencies"), module, path
                    )
                    target = _resolved_reference(endpoint or "", module, imports)
                    unresolved = (
                        public_path is None
                        or methods is None
                        or dependencies is None
                        or target is None
                        or _response_model(call) is None
                    )
                    routes.append(
                        _Route(
                            owner,
                            public_path,
                            methods,
                            dependencies or (),
                            target[0] if target else module,
                            target[1] if target else "unresolved_handler",
                            _response_model(call) is True,
                            path,
                            node.lineno,
                            _line_order(path, node),
                            route_ordinal,
                            unresolved,
                        )
                    )
                    route_ordinal += 1
                elif method == "add_middleware":
                    public_name = _dotted(call.args[0] if call.args else None)
                    if public_name is None:
                        diagnostics.mark(
                            path,
                            CoverageCapability.FRAMEWORK_LIFECYCLE,
                            "framework_config_unresolved",
                        )
                        continue
                    middleware.setdefault(owner, []).append(
                        _Middleware(
                            owner,
                            public_name.rsplit(".", 1)[-1],
                            None,
                            True,
                            path,
                            node.lineno,
                            _line_order(path, node),
                        )
                    )
                    diagnostics.mark(
                        path,
                        CoverageCapability.FRAMEWORK_LIFECYCLE,
                        "middleware_behavior_unresolved",
                    )
                elif method == "add_exception_handler":
                    exception_name = _dotted(call.args[0] if call.args else None)
                    handler_raw = _dotted(
                        call.args[1]
                        if len(call.args) > 1
                        else _keyword(call, "handler")
                    )
                    target = _resolved_reference(handler_raw or "", module, imports)
                    exception_handlers.setdefault(owner, []).append(
                        _ExceptionHandler(
                            owner,
                            exception_name,
                            function_keys.get(target) if target else None,
                            handler_raw.rsplit(".", 1)[-1]
                            if handler_raw
                            else "exception_handler",
                            path,
                            node.lineno,
                            _line_order(path, node),
                        )
                    )

        invalid_root_apps = frozenset(
            object_key
            for object_key, item in objects.items()
            if item.kind == "app"
            and (
                object_key in duplicate_view
                or any(
                    order > item.order
                    for order in rebound_view.get((item.module, item.name), ())
                )
            )
        )
        for object_key in invalid_root_apps:
            diagnostics.mark(
                objects[object_key].source_path,
                CoverageCapability.ENTRYPOINT_DISCOVERY,
                "framework_object_rebound",
            )
        expanded = _expand_routes(
            objects,
            includes,
            routes,
            diagnostics,
            invalid_root_apps=invalid_root_apps,
        )
        starlette_series = _version_tuple(self.detected_starlette_version(context))
        middleware_order_proven = (
            starlette_series in _STARLETTE_REVERSE_REGISTRATION_SERIES
        )
        if any(middleware.values()) and not middleware_order_proven:
            for values in middleware.values():
                for item in values:
                    diagnostics.mark(
                        item.source_path,
                        CoverageCapability.FRAMEWORK_LIFECYCLE,
                        "middleware_order_unresolved",
                    )
        fastapi_series = _version_tuple(self.detected_version(context))
        cleanup_before_background_proven = (
            fastapi_series is not None and fastapi_series >= (0, 106)
        )
        if (
            any(
                outcome.has_yield and outcome.background_targets
                for outcome in outcomes.values()
            )
            and not cleanup_before_background_proven
        ):
            for (module, _name), outcome in outcomes.items():
                if outcome.has_yield and outcome.background_targets:
                    diagnostics.mark(
                        None,
                        CoverageCapability.FRAMEWORK_LIFECYCLE,
                        "cleanup_background_order_unresolved",
                    )

        function_paths = {
            (module, function.name): path
            for path, _source, module, function in function_nodes
        }
        for function_ref, outcome in outcomes.items():
            for task in outcome.background_targets:
                target = _resolved_reference(task, function_ref[0], imports)
                if target is None or target not in function_keys:
                    diagnostics.mark(
                        function_paths.get(function_ref),
                        CoverageCapability.ASYNC,
                        "background_task_unresolved",
                    )

        route_candidates: list[EntrypointCandidate] = []
        route_map: dict[tuple[str, str, int], _ResolvedRoute] = {}
        for item in expanded:
            source = parsed.get(item.route.source_path, ("", None, ""))[0]
            if not source:
                diagnostics.mark(
                    item.route.source_path,
                    CoverageCapability.ENTRYPOINT_DISCOVERY,
                    "framework_config_unresolved",
                )
                continue
            candidate = _route_candidate(item, function_keys, source)
            if candidate.handler_local_key is None:
                diagnostics.mark(
                    item.route.source_path,
                    CoverageCapability.ENTRYPOINT_DISCOVERY,
                    "route_handler_unresolved",
                )
            for _role, dependency in item.dependencies:
                if _dependency_key(dependency, imports, function_keys) is None:
                    diagnostics.mark(
                        dependency.source_path,
                        CoverageCapability.SYMBOL_RESOLUTION,
                        "dependency_target_unresolved",
                    )
            route_candidates.append(candidate)
            route_map[_candidate_key(candidate)] = item

        event_candidates: list[EntrypointCandidate] = []
        for item in events:
            if item.owner in invalid_root_apps:
                continue
            source = parsed.get(item.source_path, ("", None, ""))[0]
            if source:
                event_candidates.append(
                    _event_candidate(context, item, function_keys, source)
                )
        for app in objects.values():
            if app.kind != "app" or app.key in invalid_root_apps:
                continue
            source = parsed.get(app.source_path, ("", None, ""))[0]
            if source:
                candidate = _lifespan_candidate(app, function_keys, imports, source)
                if candidate is not None:
                    event_candidates.append(candidate)

        snapshot = _Snapshot(
            MappingProxyType(route_map),
            MappingProxyType(outcomes),
            function_keys,
            MappingProxyType(imports),
            MappingProxyType({
                key: tuple(sorted(value, key=lambda item: item.order))
                for key, value in middleware.items()
            }),
            MappingProxyType({
                key: tuple(sorted(value, key=lambda item: item.order))
                for key, value in exception_handlers.items()
            }),
            tuple(event_candidates),
            middleware_order_proven,
            cleanup_before_background_proven,
            diagnostics.events(),
        )
        self._remember(context, snapshot)
        all_candidates = route_candidates + event_candidates
        return tuple(
            replace(
                candidate,
                framework_segment_keys=tuple(
                    segment.local_key for segment in self.pipeline(context, candidate)
                ),
            )
            for candidate in all_candidates
        )

    def pipeline(
        self, context: ExtractionContext, candidate: EntrypointCandidate
    ) -> tuple[FrameworkPipelineSegment, ...]:
        if candidate.kind is not EntrypointKind.HTTP_ROUTE:
            role = (
                "lifespan"
                if candidate.kind is EntrypointKind.PROCESS_MAIN
                else f"{candidate.trigger_value}_event"
            )
            target = (
                FrameworkLocalTarget(candidate.handler_local_key)
                if candidate.handler_local_key is not None
                else FrameworkBoundaryTarget(
                    FrameworkBoundaryDescriptor(
                        "fastapi",
                        role,
                        candidate.public_name,
                        candidate.registration_locator,
                        candidate.evidence,
                    )
                )
            )
            key = _pipeline_key(candidate, role, 0)
            return (
                FrameworkPipelineSegment(
                    key,
                    role,
                    0,
                    target,
                    ReturnSuccessor(_terminal_key(candidate, "exit", 0), 0),
                    (),
                    candidate.evidence,
                ),
            )

        snapshot = self._snapshot(context)
        resolved = snapshot.routes.get(_candidate_key(candidate))
        if resolved is None:
            return self._boundary_pipeline(candidate, "route_unresolved_boundary")
        outcome = snapshot.outcomes.get(
            (resolved.route.handler_module, resolved.route.handler_name),
            _FunctionOutcome(False, False, (), False, ()),
        )
        middlewares = snapshot.middleware.get(resolved.app_key, ())
        handlers = snapshot.exception_handlers.get(resolved.app_key, ())

        # role, local target, safe public label, may short-circuit, may raise
        roles: list[tuple[str, str | None, str | None, bool, bool]] = [
            ("router", None, None, False, False)
        ]
        request_middlewares: tuple[_Middleware, ...] = ()
        if middlewares:
            if snapshot.middleware_order_proven:
                request_middlewares = tuple(reversed(middlewares))
                roles.extend(
                    (
                        "middleware_request",
                        item.local_key,
                        item.public_name,
                        item.may_short_circuit,
                        False,
                    )
                    for item in request_middlewares
                )
            else:
                roles.append(("middleware_order_boundary", None, None, False, False))
        if outcome.has_request_validation:
            roles.append(("request_validation", None, None, True, False))

        seen_cached: set[tuple[str, str]] = set()
        yielded: list[_Dependency] = []
        dependency_raises = False
        for dependency_role, dependency in resolved.dependencies:
            key = _dependency_key(dependency, snapshot.imports, snapshot.function_keys)
            identity = ("local", key) if key is not None else dependency.identity
            if dependency.cache and identity in seen_cached:
                roles.append((
                    "dependency_cache_reuse",
                    None,
                    _dependency_name(dependency),
                    False,
                    False,
                ))
                continue
            if dependency.cache:
                seen_cached.add(identity)
            # Imports were resolved during discovery for route shape.  A
            # dependency target outside the parsed syntax remains a framework
            # boundary; it is not a guessed local executable declaration.
            roles.append((
                dependency_role,
                key,
                _dependency_name(dependency),
                False,
                False,
            ))
            resolved_key = next(
                (
                    item
                    for item, value in snapshot.function_keys.items()
                    if value == key
                ),
                None,
            )
            dependency_outcome = (
                snapshot.outcomes.get(resolved_key) if resolved_key else None
            )
            if dependency_outcome is not None:
                if dependency_outcome.has_yield:
                    yielded.append(dependency)
                dependency_raises = dependency_raises or dependency_outcome.raises

        roles.append((
            "async_handler" if outcome.is_async else "sync_handler",
            candidate.handler_local_key,
            candidate.public_name,
            False,
            outcome.raises,
        ))
        if resolved.route.response_model:
            roles.append(("response_model_serialization", None, None, False, False))
        if yielded:
            if (
                outcome.background_targets
                and not snapshot.cleanup_before_background_proven
            ):
                roles.append((
                    "cleanup_background_order_boundary",
                    None,
                    None,
                    False,
                    False,
                ))
            else:
                for dependency in reversed(yielded):
                    roles.append((
                        "yield_dependency_cleanup",
                        None,
                        _dependency_name(dependency),
                        False,
                        False,
                    ))
        for task in outcome.background_targets:
            task_reference = _resolved_reference(
                task, resolved.route.handler_module, snapshot.imports
            )
            target = (
                snapshot.function_keys.get(task_reference)
                if task_reference is not None
                else None
            )
            roles.append(("background_task_dispatch", target, task, False, False))
        roles.extend(
            ("middleware_response", item.local_key, item.public_name, False, False)
            for item in middlewares
            if snapshot.middleware_order_proven
        )
        roles.append(("response", None, None, False, False))
        raised_types = (
            tuple(
                item
                for dependency_role, dependency in resolved.dependencies
                if dependency_role != "dependency_cache_reuse"
                for key in (
                    _dependency_key(
                        dependency, snapshot.imports, snapshot.function_keys
                    ),
                )
                for function_ref in (
                    next(
                        (
                            item
                            for item, value in snapshot.function_keys.items()
                            if value == key
                        ),
                        None,
                    ),
                )
                for item in (
                    snapshot.outcomes.get(function_ref).raised_types
                    if function_ref in snapshot.outcomes
                    else ()
                )
            )
            + outcome.raised_types
        )
        handler = self._matching_exception_handler(handlers, raised_types)
        exception_role = (
            "exception_handler" if handler is not None else "unhandled_exception"
        )
        exception_local = handler.local_key if handler is not None else None
        exception_name = handler.public_name if handler is not None else None
        if dependency_raises or outcome.raises:
            roles.append((
                exception_role,
                exception_local,
                exception_name,
                False,
                False,
            ))

        keys = [
            _pipeline_key(candidate, role, ordinal)
            for ordinal, (role, *_rest) in enumerate(roles)
        ]
        response_index = next(
            index for index, item in enumerate(roles) if item[0] == "response"
        )
        response_middleware_indices = [
            index
            for index, item in enumerate(roles)
            if item[0] == "middleware_response"
        ]
        response_start = (
            response_middleware_indices[0]
            if response_middleware_indices
            else response_index
        )
        exception_index = next(
            (
                index
                for index, item in enumerate(roles)
                if item[0] in {"exception_handler", "unhandled_exception"}
            ),
            None,
        )
        response_for_request: dict[int, int] = {}
        for request_index, item in enumerate(roles):
            if item[0] != "middleware_request":
                continue
            request_name = item[2]
            response_for_request[request_index] = next(
                (
                    index
                    for index in response_middleware_indices
                    if roles[index][2] == request_name
                ),
                response_start,
            )

        segments: list[FrameworkPipelineSegment] = []
        for ordinal, (
            role,
            local_key,
            public_name,
            may_short_circuit,
            may_raise,
        ) in enumerate(roles):
            target = (
                FrameworkLocalTarget(local_key)
                if local_key is not None
                else FrameworkBoundaryTarget(
                    FrameworkBoundaryDescriptor(
                        "fastapi",
                        role,
                        public_name,
                        candidate.registration_locator,
                        candidate.evidence,
                    )
                )
            )
            shortcuts: list[Successor] = []
            if role == "request_validation":
                shortcuts.append(
                    ReturnSuccessor(
                        _terminal_key(candidate, "validation_422", ordinal), 0
                    )
                )
            if role == "middleware_request" and may_short_circuit:
                shortcuts.append(
                    AlwaysSuccessor(keys[response_for_request[ordinal]], len(shortcuts))
                )
            if role in {
                "app_dependency",
                "router_dependency",
                "route_dependency",
                "decorator_dependency",
            }:
                resolved_key = local_key
                function_ref = next(
                    (
                        item
                        for item, value in snapshot.function_keys.items()
                        if value == resolved_key
                    ),
                    None,
                )
                if (
                    function_ref is not None
                    and snapshot.outcomes.get(
                        function_ref, _FunctionOutcome(False, False, (), False, ())
                    ).raises
                    and exception_index is not None
                ):
                    shortcuts.append(
                        ExceptionSuccessor(
                            keys[exception_index],
                            _exception_scope_key(candidate),
                            None,
                            len(shortcuts),
                        )
                    )
            if (
                role in {"sync_handler", "async_handler"}
                and may_raise
                and exception_index is not None
            ):
                shortcuts.append(
                    ExceptionSuccessor(
                        keys[exception_index],
                        _exception_scope_key(candidate),
                        None,
                        len(shortcuts),
                    )
                )
            if role == "background_task_dispatch" and local_key is not None:
                shortcuts.append(
                    AsyncSuccessor(local_key, AsyncDispatchKind.TASK, len(shortcuts))
                )

            if role == "unhandled_exception":
                success: Successor = ReturnSuccessor(
                    _terminal_key(candidate, "exception", ordinal), ordinal
                )
            elif role == "exception_handler":
                success = AlwaysSuccessor(keys[response_start], ordinal)
            elif role == "response":
                success = ReturnSuccessor(
                    _terminal_key(candidate, "response", ordinal), ordinal
                )
            else:
                next_index = ordinal + 1
                success = AlwaysSuccessor(
                    keys[next_index]
                    if next_index < len(keys)
                    else _terminal_key(candidate, "response", ordinal),
                    ordinal,
                )
            segments.append(
                FrameworkPipelineSegment(
                    keys[ordinal],
                    role,
                    ordinal,
                    target,
                    success,
                    tuple(sorted(shortcuts, key=lambda item: (item.order, item.kind))),
                    candidate.evidence,
                )
            )
        return tuple(segments)

    @staticmethod
    def _matching_exception_handler(
        handlers: Sequence[_ExceptionHandler], raised_types: tuple[str | None, ...]
    ) -> _ExceptionHandler | None:
        """Choose a handler only when every proven throw type has the same target."""

        if not raised_types or any(item is None for item in raised_types):
            return None
        matches: list[_ExceptionHandler] = []
        for raised in raised_types:
            candidates = [
                handler
                for handler in handlers
                if handler.exception_name is not None
                and handler.exception_name.rsplit(".", 1)[-1]
                in {raised.rsplit(".", 1)[-1], "Exception", "BaseException"}
            ]
            if not candidates:
                return None
            matches.append(candidates[0])
        first = matches[0]
        return first if all(item == first for item in matches) else None

    @staticmethod
    def _boundary_pipeline(
        candidate: EntrypointCandidate, role: str
    ) -> tuple[FrameworkPipelineSegment, ...]:
        key = _pipeline_key(candidate, role, 0)
        target = FrameworkBoundaryTarget(
            FrameworkBoundaryDescriptor(
                "fastapi",
                role,
                candidate.public_name,
                candidate.registration_locator,
                candidate.evidence,
            )
        )
        return (
            FrameworkPipelineSegment(
                key,
                role,
                0,
                target,
                ReturnSuccessor(_terminal_key(candidate, "response", 0), 0),
                (),
                candidate.evidence,
            ),
        )


__all__ = ["FastAPILifecycleAdapter"]
