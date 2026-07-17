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
_FRAMEWORK_REGISTRATION_METHODS = frozenset({
    "add_api_route",
    "add_event_handler",
    "add_exception_handler",
    "add_middleware",
    "add_route",
    "exception_handler",
    "include_router",
    "middleware",
    "on_event",
})

# These exact detected versions have source-reviewed route registration
# signatures/defaults.  Patch siblings remain partial until reviewed.
_FASTAPI_ROUTE_SIGNATURE_VERSIONS = frozenset({"0.115.0"})
_STARLETTE_ROUTE_SIGNATURE_VERSIONS = frozenset({"0.37.2"})

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

_Order = tuple[str, int, int]
_BindingTimeline = Mapping[
    tuple[str, str],
    tuple[tuple[_Order, str | None], ...],
]
_ObjectBindingTimeline = Mapping[
    tuple[str, str],
    tuple[tuple[_Order, str | None], ...],
]
_ResponseClassTimeline = Mapping[
    tuple[str, str],
    tuple[tuple[_Order, bool | None], ...],
]
_DependencyBindingTimeline = Mapping[
    tuple[str, str],
    tuple[tuple[_Order, tuple["_Dependency", ...] | None], ...],
]
_WildcardExports = Mapping[str, tuple[str, ...] | None]
_NON_FRAMEWORK_BINDING = "<hades-non-framework>"
_BACKGROUND_TASKS_BINDING = "<hades-background-tasks>"
_WILDCARD_BINDING = "<hades-wildcard>"


@dataclass(frozen=True, slots=True)
class _Reference:
    raw_target: str
    target: tuple[str, str] | None


@dataclass(frozen=True, slots=True)
class _Dependency:
    raw_target: str
    module: str
    target: tuple[str, str] | None
    cache: bool
    scopes: tuple[str, ...]
    source_path: str
    line: int

    @property
    def identity(self) -> tuple[str, str]:
        return self.target or (self.module, self.raw_target)


@dataclass(frozen=True, slots=True)
class _Object:
    key: str
    module: str
    name: str
    kind: str
    prefix: str | None
    dependencies: tuple[_Dependency, ...]
    lifespan: _Reference | None
    lifespan_declared: bool
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
    flavor: str
    path: str | None
    methods: tuple[str, ...] | None
    dependencies: tuple[_Dependency, ...]
    handler_module: str
    handler_name: str
    response_model: bool | None
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
class _ContinuationContainer:
    values: tuple[tuple[object, _ContinuationRelation], ...]


_ContinuationRelation = bool | None | _ContinuationContainer
_INVALID_STATIC_KEY = object()


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
    order: tuple[str, int, int]
    ordinal: int
    instance_ordinal: int = 0


@dataclass(frozen=True, slots=True)
class _EventUncertainty:
    owner: str
    source_path: str
    order: tuple[str, int, int]


@dataclass(frozen=True, slots=True)
class _LifespanInstance:
    owner: _Object
    instance_ordinal: int


@dataclass(frozen=True, slots=True)
class _FunctionOutcome:
    is_async: bool
    has_yield: bool
    raised_types: tuple[str | None, ...]
    has_request_validation: bool
    background_targets: tuple[_Reference, ...]

    @property
    def raises(self) -> bool:
        return bool(self.raised_types)


@dataclass(frozen=True, slots=True)
class _ResolvedRoute:
    route: _Route
    public_path: str
    dependencies: tuple[tuple[str, _Dependency], ...]
    app_key: str
    instance_ordinal: int = 0


@dataclass(frozen=True, slots=True)
class _Snapshot:
    routes: Mapping[tuple[str, str, int], _ResolvedRoute]
    outcomes: Mapping[tuple[str, str], _FunctionOutcome]
    function_keys: Mapping[tuple[str, str], str]
    imports: Mapping[str, Mapping[str, str]]
    middleware: Mapping[str, tuple[_Middleware, ...]]
    exception_handlers: Mapping[str, tuple[_ExceptionHandler, ...]]
    exception_mros: Mapping[str, tuple[str, ...] | None]
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


def _visit_function_header(
    visitor: ast.NodeVisitor,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> None:
    for decorator in node.decorator_list:
        visitor.visit(decorator)
    for default in node.args.defaults:
        visitor.visit(default)
    for default in node.args.kw_defaults:
        if default is not None:
            visitor.visit(default)
    arguments = (
        *node.args.posonlyargs,
        *node.args.args,
        *node.args.kwonlyargs,
    )
    for argument in arguments:
        if argument.annotation is not None:
            visitor.visit(argument.annotation)
    for argument in (node.args.vararg, node.args.kwarg):
        if argument is not None and argument.annotation is not None:
            visitor.visit(argument.annotation)
    if node.returns is not None:
        visitor.visit(node.returns)
    for type_parameter in getattr(node, "type_params", ()):
        visitor.visit(type_parameter)


def _visit_class_header(visitor: ast.NodeVisitor, node: ast.ClassDef) -> None:
    for decorator in node.decorator_list:
        visitor.visit(decorator)
    for base in node.bases:
        visitor.visit(base)
    for keyword in node.keywords:
        visitor.visit(keyword.value)
    for type_parameter in getattr(node, "type_params", ()):
        visitor.visit(type_parameter)


class _ScopeBindingVisitor(ast.NodeVisitor):
    """Collect stores in one scope while excluding nested bodies/local targets."""

    def __init__(self) -> None:
        self.records: list[tuple[str, ast.AST]] = []
        self.global_names: set[str] = set()
        self.nonlocal_names: set[str] = set()

    def _record(self, name: str | None, node: ast.AST) -> None:
        if name:
            self.records.append((name, node))

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self._record(node.id, node)

    def visit_Global(self, node: ast.Global) -> None:
        self.global_names.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.nonlocal_names.update(node.names)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._record(node.name, node)
        _visit_function_header(self, node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._record(node.name, node)
        _visit_function_header(self, node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._record(node.name, node)
        _visit_class_header(self, node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self.visit(node.elt)
        self._visit_comprehensions(node.generators)

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self.visit(node.elt)
        self._visit_comprehensions(node.generators)

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self.visit(node.key)
        self.visit(node.value)
        self._visit_comprehensions(node.generators)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self.visit(node.elt)
        self._visit_comprehensions(node.generators)

    def _visit_comprehensions(self, generators: Sequence[ast.comprehension]) -> None:
        for generator in generators:
            self.visit(generator.iter)
            for condition in generator.ifs:
                self.visit(condition)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        self._record(node.name, node)
        if node.type is not None:
            self.visit(node.type)
        for statement in node.body:
            self.visit(statement)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        self._record(node.name, node)
        if node.pattern is not None:
            self.visit(node.pattern)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        self._record(node.name, node)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        self._record(node.rest, node)
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._record(alias.asname or alias.name.split(".", 1)[0], node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name != "*":
                self._record(alias.asname or alias.name, node)


def _join_path(*parts: str) -> str:
    values = [part.strip("/") for part in parts if part and part != "/"]
    return "/" + "/".join(values) if values else "/"


def _literal_http_methods(node: ast.AST | None) -> tuple[str, ...] | None:
    if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return None
    methods = tuple(
        item.value.upper()
        for item in node.elts
        if isinstance(item, ast.Constant)
        and isinstance(item.value, str)
        and re.fullmatch(r"[A-Za-z]+", item.value)
    )
    if len(methods) != len(node.elts) or not methods:
        return None
    return tuple(sorted(set(methods)))


def _http_methods(
    call: ast.Call,
    method: str,
    *,
    fastapi_contract_proven: bool,
    starlette_contract_proven: bool,
) -> tuple[str, ...] | None:
    if method in _HTTP_DECORATORS:
        return (method.upper(),)
    if method in {"api_route", "add_api_route"}:
        if not fastapi_contract_proven:
            return None
        values = _keyword(call, "methods")
        return ("GET",) if values is None else _literal_http_methods(values)
    if method not in {"route", "add_route"} or not starlette_contract_proven:
        return None
    values = _keyword(call, "methods")
    positional_index = 1 if method == "route" else 2
    if values is None and len(call.args) > positional_index:
        values = call.args[positional_index]
    if values is None:
        return ("GET", "HEAD")
    methods = _literal_http_methods(values)
    if methods is None:
        return None
    return tuple(sorted(set(methods) | ({"HEAD"} if "GET" in methods else set())))


def _static_type_annotation(
    node: ast.AST | None,
    *,
    quoted_depth: int = 0,
) -> bool | None:
    if node is None:
        return False
    if isinstance(node, ast.Constant):
        if node.value is None:
            return False
        if not isinstance(node.value, str) or not node.value.strip() or quoted_depth:
            return None
        try:
            expression = ast.parse(node.value, mode="eval").body
        except SyntaxError:
            return None
        return _static_type_annotation(expression, quoted_depth=quoted_depth + 1)
    if isinstance(node, ast.Name):
        return True
    if isinstance(node, ast.Attribute):
        return True if _dotted(node) is not None else None
    if isinstance(node, ast.Subscript):
        return (
            True
            if _static_type_annotation(node.value, quoted_depth=quoted_depth) is True
            and _static_type_annotation(node.slice, quoted_depth=quoted_depth) is True
            else None
        )
    if isinstance(node, (ast.Tuple, ast.List)):
        return (
            True
            if node.elts
            and all(
                _static_type_annotation(item, quoted_depth=quoted_depth) is True
                for item in node.elts
            )
            else None
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        sides = (
            _static_type_annotation(node.left, quoted_depth=quoted_depth),
            _static_type_annotation(node.right, quoted_depth=quoted_depth),
        )
        return True if all(item in {True, False} for item in sides) else None
    return None


def _response_model(
    call: ast.Call,
    return_annotation: ast.AST | None,
    module: str,
    path: str,
    imports: Mapping[str, Mapping[str, str]],
    import_bindings: _BindingTimeline,
    response_classes: _ResponseClassTimeline,
) -> bool | None:
    """Return whether a declared response model is statically meaningful.

    An explicit ``response_model=None`` opts out even when the endpoint has a
    return annotation.  Computed model factories and computed annotations are
    retained as typed lifecycle uncertainty rather than silently omitted.
    """

    value = _keyword(call, "response_model")
    if value is None:
        relation = _response_annotation_relation(
            return_annotation,
            module,
            path,
            imports,
            import_bindings,
            response_classes,
        )
        if relation is True:
            return False
        if relation is None and isinstance(
            return_annotation,
            (ast.Name, ast.Attribute, ast.Constant),
        ):
            return None
        return _static_type_annotation(return_annotation)
    if isinstance(value, ast.Constant):
        return False if value.value is None else _static_type_annotation(value)
    if isinstance(value, (ast.Name, ast.Attribute, ast.Subscript, ast.Tuple, ast.List)):
        return True
    return None


def _dependency_from(
    node: ast.AST,
    module: str,
    path: str,
    imports: Mapping[str, Mapping[str, str]],
    import_bindings: _BindingTimeline,
) -> _Dependency | None:
    if not isinstance(node, ast.Call):
        return None
    name = _call_name(node)
    if name is None:
        return None
    constructor = _resolved_reference_at(
        name,
        module,
        imports,
        import_bindings,
        _line_order(path, node.func),
    )
    if constructor not in {
        ("fastapi", "Depends"),
        ("fastapi", "Security"),
        ("fastapi.params", "Depends"),
        ("fastapi.params", "Security"),
    }:
        return None
    if not node.args:
        return None
    target = _dotted(node.args[0])
    if target is None:
        return None
    use_cache = _keyword(node, "use_cache")
    if use_cache is None:
        cache = True
    elif isinstance(use_cache, ast.Constant) and type(use_cache.value) is bool:
        cache = use_cache.value
    else:
        return None
    scopes_node = _keyword(node, "scopes")
    if constructor in {
        ("fastapi", "Security"),
        ("fastapi.params", "Security"),
    }:
        if scopes_node is None or (
            isinstance(scopes_node, ast.Constant) and scopes_node.value is None
        ):
            scopes: tuple[str, ...] = ()
        elif isinstance(scopes_node, (ast.List, ast.Tuple)) and all(
            isinstance(item, ast.Constant) and isinstance(item.value, str)
            for item in scopes_node.elts
        ):
            scopes = tuple(sorted({item.value for item in scopes_node.elts}))
        else:
            return None
    elif scopes_node is not None:
        return None
    else:
        scopes = ()
    return _Dependency(
        target,
        module,
        _resolved_reference_at(
            target,
            module,
            imports,
            import_bindings,
            _line_order(path, node.args[0]),
        ),
        cache,
        scopes,
        path,
        int(getattr(node, "lineno", 1)),
    )


def _dependencies(
    node: ast.AST | None,
    module: str,
    path: str,
    imports: Mapping[str, Mapping[str, str]],
    import_bindings: _BindingTimeline,
) -> tuple[_Dependency, ...] | None:
    if node is None:
        return ()
    if not isinstance(node, (ast.List, ast.Tuple)):
        return None
    values = tuple(
        _dependency_from(item, module, path, imports, import_bindings)
        for item in node.elts
    )
    return values if all(value is not None for value in values) else None


def _annotated_dependencies(
    annotation: ast.AST,
    occurrence_node: ast.AST,
    *,
    quoted: bool,
    module: str,
    path: str,
    imports: Mapping[str, Mapping[str, str]],
    import_bindings: _BindingTimeline,
) -> tuple[bool, tuple[_Dependency, ...] | None]:
    if not isinstance(annotation, ast.Subscript):
        return (False, ())
    raw_wrapper = _dotted(annotation.value)
    if raw_wrapper is None or raw_wrapper.rsplit(".", 1)[-1] != "Annotated":
        return (False, ())
    wrapper = _resolved_reference_at(
        raw_wrapper,
        module,
        imports,
        import_bindings,
        _line_order(path, occurrence_node),
    )
    if wrapper not in {
        ("typing", "Annotated"),
        ("typing_extensions", "Annotated"),
    }:
        return (True, None)
    elements = (
        annotation.slice.elts
        if isinstance(annotation.slice, ast.Tuple)
        else (annotation.slice,)
    )

    def proven_nondependency(metadata: ast.AST) -> bool:
        if isinstance(metadata, ast.Constant):
            return True
        if isinstance(metadata, (ast.List, ast.Set, ast.Tuple)):
            return all(proven_nondependency(item) for item in metadata.elts)
        if isinstance(metadata, ast.Dict):
            return all(
                key is not None
                and proven_nondependency(key)
                and proven_nondependency(value)
                for key, value in zip(metadata.keys, metadata.values, strict=True)
            )
        return False

    found: list[_Dependency] = []
    for metadata in elements[1:]:
        if proven_nondependency(metadata):
            continue
        if quoted or not isinstance(metadata, ast.Call):
            return (True, None)
        dependency = _dependency_from(
            metadata,
            module,
            path,
            imports,
            import_bindings,
        )
        if dependency is None:
            return (True, None)
        found.append(dependency)
    return (True, tuple(found))


def _dependency_bindings(
    parsed: Mapping[str, tuple[str, ast.Module, str]],
    imports: Mapping[str, Mapping[str, str]],
    import_bindings: _BindingTimeline,
    wildcard_exports: _WildcardExports,
    reachability: frozenset[tuple[str, str]],
) -> _DependencyBindingTimeline:
    known_modules = {module for _source, _tree, module in parsed.values()}
    previous_finals: dict[tuple[str, str], tuple[_Dependency, ...] | None] = {}
    final_bindings: dict[
        tuple[str, str],
        list[tuple[_Order, tuple[_Dependency, ...] | None]],
    ] = {}

    for _iteration in range(len(known_modules) + 2):
        bindings: dict[
            tuple[str, str],
            list[tuple[_Order, tuple[_Dependency, ...] | None]],
        ] = {}

        def remember(
            module: str,
            name: str,
            order: _Order,
            value: tuple[_Dependency, ...] | None,
        ) -> None:
            bindings.setdefault((module, name), []).append((order, value))

        def visible(
            module: str,
            name: str,
            occurrence: _Order,
        ) -> tuple[_Dependency, ...] | None:
            item = next(
                (
                    candidate
                    for candidate in reversed(bindings.get((module, name), ()))
                    if candidate[0] <= occurrence
                ),
                None,
            )
            return item[1] if item is not None else None

        def classify(
            value: ast.AST | None,
            module: str,
            path: str,
            occurrence: _Order,
        ) -> tuple[_Dependency, ...] | None:
            if value is None:
                return None
            dependency = _dependency_from(
                value,
                module,
                path,
                imports,
                import_bindings,
            )
            if dependency is not None:
                return (dependency,)
            is_annotated, annotated = _annotated_dependencies(
                value,
                value,
                quoted=False,
                module=module,
                path=path,
                imports=imports,
                import_bindings=import_bindings,
            )
            if is_annotated:
                return annotated
            if isinstance(value, ast.Name):
                return visible(module, value.id, occurrence)
            if isinstance(
                value,
                (
                    ast.Constant,
                    ast.Dict,
                    ast.List,
                    ast.Set,
                    ast.Tuple,
                ),
            ):
                return ()
            return None

        def paired_destructuring(
            target: ast.AST,
            value: ast.AST,
        ) -> tuple[tuple[ast.Name, ast.AST], ...] | None:
            if sum(isinstance(item, ast.Starred) for item in ast.walk(target)) > 1:
                return None
            if any(isinstance(item, ast.Starred) for item in ast.walk(value)):
                return None
            if isinstance(target, ast.Name):
                return ((target, value),)
            if not isinstance(target, (ast.List, ast.Tuple)) or not isinstance(
                value,
                (ast.List, ast.Tuple),
            ):
                return None
            star_positions = tuple(
                index
                for index, item in enumerate(target.elts)
                if isinstance(item, ast.Starred)
            )
            if not star_positions and len(target.elts) != len(value.elts):
                return None
            if star_positions and len(value.elts) < len(target.elts) - 1:
                return None
            pairs: list[tuple[ast.Name, ast.AST]] = []
            if star_positions:
                star_index = star_positions[0]
                suffix_count = len(target.elts) - star_index - 1
                captured_end = len(value.elts) - suffix_count
                starred = target.elts[star_index]
                assert isinstance(starred, ast.Starred)
                if not isinstance(starred.value, ast.Name):
                    return None
                items = (
                    *zip(
                        target.elts[:star_index],
                        value.elts[:star_index],
                        strict=True,
                    ),
                    (
                        starred.value,
                        ast.copy_location(
                            ast.List(
                                elts=value.elts[star_index:captured_end],
                                ctx=ast.Load(),
                            ),
                            value,
                        ),
                    ),
                    *zip(
                        target.elts[star_index + 1 :],
                        value.elts[captured_end:],
                        strict=True,
                    ),
                )
            else:
                items = tuple(zip(target.elts, value.elts, strict=True))
            for nested_target, nested_value in items:
                nested = paired_destructuring(nested_target, nested_value)
                if nested is None:
                    return None
                pairs.extend(nested)
            return tuple(pairs)

        def remember_assignment(
            module: str,
            path: str,
            target: ast.AST,
            value: ast.AST,
            order: _Order,
        ) -> None:
            pairs = paired_destructuring(target, value)
            if pairs is not None:
                for name, paired_value in pairs:
                    remember(
                        module,
                        name.id,
                        order,
                        classify(paired_value, module, path, order),
                    )
                return
            visitor = _ScopeBindingVisitor()
            visitor.visit(target)
            for name, binding in visitor.records:
                remember(module, name, _line_order(path, binding), None)

        for path, (_source, tree, module) in parsed.items():
            for node in tree.body:
                order = _line_order(path, node)
                if isinstance(node, ast.ImportFrom):
                    base = _import_from_base(path, module, node)
                    cyclic = (
                        base is not None
                        and base != module
                        and (base, module) in reachability
                    )
                    for alias in node.names:
                        if alias.name == "*":
                            names = (
                                wildcard_exports.get(base)
                                if base is not None and not cyclic
                                else None
                            )
                            if names is None:
                                remember(
                                    module,
                                    _WILDCARD_BINDING,
                                    order,
                                    None,
                                )
                                continue
                            for name in names:
                                remember(
                                    module,
                                    name,
                                    order,
                                    previous_finals.get((base, name)),
                                )
                            continue
                        name = alias.asname or alias.name
                        value = (
                            previous_finals.get((base, alias.name))
                            if base in known_modules and not cyclic
                            else None
                            if base in known_modules
                            else ()
                        )
                        remember(module, name, order, value)
                    continue
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        remember(
                            module,
                            alias.asname or alias.name.split(".", 1)[0],
                            order,
                            (),
                        )
                    continue
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        remember_assignment(
                            module,
                            path,
                            target,
                            node.value,
                            order,
                        )
                    continue
                if isinstance(node, ast.AnnAssign) and isinstance(
                    node.target,
                    ast.Name,
                ):
                    remember(
                        module,
                        node.target.id,
                        order,
                        classify(node.value, module, path, order),
                    )
                    continue
                if isinstance(
                    node,
                    (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
                ):
                    remember(module, node.name, order, ())
                    continue
                visitor = _ScopeBindingVisitor()
                visitor.visit(node)
                for name, binding in visitor.records:
                    remember(module, name, _line_order(path, binding), None)

        current_finals = {
            key: values[-1][1] for key, values in bindings.items() if values
        }
        final_bindings = bindings
        if current_finals == previous_finals:
            break
        previous_finals = current_finals

    return MappingProxyType({
        key: tuple(values) for key, values in final_bindings.items()
    })


def _parameter_dependencies(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    module: str,
    path: str,
    imports: Mapping[str, Mapping[str, str]],
    import_bindings: _BindingTimeline,
    dependency_bindings: _DependencyBindingTimeline,
    import_reachability: frozenset[tuple[str, str]],
) -> tuple[_Dependency, ...] | None:
    found: list[_Dependency] = []

    def dependency_binding(
        value: ast.AST,
        occurrence_node: ast.AST,
    ) -> tuple[bool, tuple[_Dependency, ...] | None]:
        occurrence = _line_order(path, occurrence_node)
        if isinstance(value, ast.Name):
            timeline = dependency_bindings.get((module, value.id))
            named = next(
                (item for item in reversed(timeline or ()) if item[0] <= occurrence),
                None,
            )
            wildcard = next(
                (
                    item
                    for item in reversed(
                        dependency_bindings.get(
                            (module, _WILDCARD_BINDING),
                            (),
                        )
                    )
                    if item[0] <= occurrence
                ),
                None,
            )
            if wildcard is not None and (named is None or wildcard[0] > named[0]):
                return (True, None)
            if named is None:
                return (True, None) if timeline is not None else (False, ())
            return (True, named[1])
        if not isinstance(value, ast.Attribute):
            return (False, ())
        raw = _dotted(value)
        if raw is None:
            return (True, None)
        head = raw.partition(".")[0]
        reference = _resolved_reference_at(
            raw,
            module,
            imports,
            import_bindings,
            occurrence,
        )
        if reference is None:
            return ((module, head) in import_bindings, None)
        if reference[0] not in imports or (
            reference[0] != module and (reference[0], module) in import_reachability
        ):
            return (True, None)
        timeline = dependency_bindings.get(reference)
        if not timeline:
            return (True, None)
        binding = (
            next(
                (item for order, item in reversed(timeline) if order <= occurrence),
                None,
            )
            if reference[0] == module
            else timeline[-1][1]
        )
        return (True, binding)

    positional = (*function.args.posonlyargs, *function.args.args)
    default_arguments = (
        positional[-len(function.args.defaults) :] if function.args.defaults else ()
    )
    positional_defaults: dict[str, ast.AST] = {
        argument.arg: default
        for argument, default in zip(
            default_arguments,
            function.args.defaults,
            strict=True,
        )
    }
    keyword_defaults = {
        argument.arg: default
        for argument, default in zip(
            function.args.kwonlyargs,
            function.args.kw_defaults,
            strict=True,
        )
        if default is not None
    }

    def append_call(call: ast.Call) -> bool:
        name = _call_name(call)
        if name is None:
            return True
        dependency = _dependency_from(
            call,
            module,
            path,
            imports,
            import_bindings,
        )
        if dependency is None:
            constructor = _resolved_reference_at(
                name,
                module,
                imports,
                import_bindings,
                _line_order(path, call.func),
            )
            return not (
                name.rsplit(".", 1)[-1] in {"Depends", "Security"}
                or constructor
                in {
                    ("fastapi", "Depends"),
                    ("fastapi", "Security"),
                    ("fastapi.params", "Depends"),
                    ("fastapi.params", "Security"),
                }
            )
        found.append(dependency)
        return True

    def append_annotation(annotation: ast.AST | None) -> bool:
        quoted = False
        occurrence_node = annotation
        if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
            try:
                annotation = ast.parse(annotation.value, mode="eval").body
            except SyntaxError:
                return True
            quoted = True
        handled, binding = dependency_binding(
            annotation,
            occurrence_node or annotation,
        )
        if handled:
            if binding is None:
                return False
            found.extend(binding)
            return True
        is_annotated, dependencies = _annotated_dependencies(
            annotation,
            occurrence_node or annotation,
            quoted=quoted,
            module=module,
            path=path,
            imports=imports,
            import_bindings=import_bindings,
        )
        if not is_annotated:
            return True
        if dependencies is None:
            return False
        found.extend(dependencies)
        return True

    for argument in (*positional, *function.args.kwonlyargs):
        if not append_annotation(argument.annotation):
            return None
        default = positional_defaults.get(argument.arg) or keyword_defaults.get(
            argument.arg
        )
        if isinstance(default, ast.Call):
            if not append_call(default):
                return None
        elif isinstance(default, (ast.Name, ast.Attribute)):
            handled, binding = dependency_binding(default, default)
            if handled:
                if binding is None:
                    return None
                found.extend(binding)
    return tuple(found)


def _has_yield(node: ast.AST) -> bool:
    found = False

    class _Visitor(ast.NodeVisitor):
        def visit_Yield(self, node: ast.Yield) -> None:
            nonlocal found
            found = True

        def visit_YieldFrom(self, node: ast.YieldFrom) -> None:
            nonlocal found
            found = True

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            return None

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            return None

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            return None

        def visit_Lambda(self, node: ast.Lambda) -> None:
            return None

    visitor = _Visitor()
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for statement in node.body:
            visitor.visit(statement)
    else:
        visitor.visit(node)
    return found


def _middleware_may_short_circuit(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool | None:
    """Prove whether every normal middleware path invokes its continuation."""

    positional = (*function.args.posonlyargs, *function.args.args)
    if len(positional) < 2:
        return None
    continuation = positional[1].arg

    bindings = _ScopeBindingVisitor()
    for statement in function.body:
        bindings.visit(statement)
    if any(name == continuation for name, _binding in bindings.records):
        return None

    def position(node: ast.AST) -> tuple[int, int]:
        return (
            int(getattr(node, "lineno", function.lineno)),
            int(getattr(node, "col_offset", 0)),
        )

    alias_bindings: dict[
        str,
        list[tuple[tuple[int, int], _ContinuationRelation]],
    ] = {continuation: [(position(function), True)]}

    def alias_at(name: str, node: ast.AST) -> _ContinuationRelation:
        occurrence = position(node)
        visible = next(
            (
                value
                for order, value in reversed(alias_bindings.get(name, ()))
                if order <= occurrence
            ),
            False,
        )
        return visible

    def remember_alias(
        name: str,
        node: ast.AST,
        value: _ContinuationRelation,
    ) -> None:
        alias_bindings.setdefault(name, []).append((position(node), value))

    def literal_key(value: ast.AST) -> object:
        try:
            key = ast.literal_eval(value)
            hash(key)
        except (TypeError, ValueError):
            return _INVALID_STATIC_KEY
        return key

    def alias_value(
        value: ast.AST | None,
        occurrence: ast.AST,
    ) -> _ContinuationRelation:
        if isinstance(value, ast.Name):
            return alias_at(value.id, occurrence)
        if isinstance(value, ast.Constant):
            return False
        if isinstance(value, (ast.List, ast.Tuple)):
            return _ContinuationContainer(
                tuple(
                    (index, alias_value(item, occurrence))
                    for index, item in enumerate(value.elts)
                )
            )
        if isinstance(value, ast.Dict):
            items: dict[object, _ContinuationRelation] = {}
            for key_node, item in zip(value.keys, value.values, strict=True):
                if key_node is None:
                    return None
                key = literal_key(key_node)
                if key is _INVALID_STATIC_KEY:
                    return None
                items[key] = alias_value(item, occurrence)
            return _ContinuationContainer(tuple(items.items()))
        if isinstance(value, ast.Attribute):
            relation = contains_continuation(alias_value(value.value, occurrence))
            return None if relation in {True, None} else False
        if isinstance(value, ast.Subscript):
            container = alias_value(value.value, occurrence)
            key = literal_key(value.slice)
            if container is False:
                return False
            if (
                not isinstance(container, _ContinuationContainer)
                or key is _INVALID_STATIC_KEY
            ):
                return None
            return next(
                (
                    relation
                    for item_key, relation in container.values
                    if item_key == key
                ),
                None,
            )
        if isinstance(value, ast.Set):
            relations = tuple(alias_value(item, occurrence) for item in value.elts)
            return False if all(item is False for item in relations) else None
        return None

    def contains_continuation(
        relation: _ContinuationRelation,
    ) -> bool | None:
        if not isinstance(relation, _ContinuationContainer):
            return relation
        values = tuple(contains_continuation(item) for _key, item in relation.values)
        if True in values:
            return True
        return None if None in values else False

    continuation_escaped = False

    def remember_target(
        target: ast.AST,
        value: ast.AST | None,
        statement: ast.AST,
    ) -> None:
        nonlocal continuation_escaped
        if isinstance(target, ast.Name):
            remember_alias(target.id, statement, alias_value(value, statement))
            return
        if isinstance(target, (ast.List, ast.Tuple)):
            if isinstance(value, (ast.List, ast.Tuple)) and len(target.elts) == len(
                value.elts
            ):
                for nested_target, nested_value in zip(
                    target.elts,
                    value.elts,
                    strict=True,
                ):
                    remember_target(nested_target, nested_value, statement)
                return
            for nested_target in target.elts:
                remember_target(nested_target, None, statement)
            return
        if isinstance(target, (ast.Attribute, ast.Subscript)):
            stored = contains_continuation(alias_value(value, statement))
            container = contains_continuation(alias_value(target.value, statement))
            continuation_escaped |= stored in {True, None} or container in {
                True,
                None,
            }

    for statement in function.body:
        if isinstance(statement, ast.Assign):
            for target in statement.targets:
                remember_target(target, statement.value, statement)
            continue
        if isinstance(statement, ast.AnnAssign):
            remember_target(statement.target, statement.value, statement)
            continue
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            remember_alias(
                statement.name,
                statement,
                False if not statement.decorator_list else None,
            )
            continue
        if isinstance(statement, (ast.Import, ast.ImportFrom)):
            visitor = _ScopeBindingVisitor()
            visitor.visit(statement)
            for name, binding in visitor.records:
                remember_alias(name, binding, False)
            continue
        visitor = _ScopeBindingVisitor()
        visitor.visit(statement)
        for name, binding in visitor.records:
            remember_alias(name, binding, None)

    def mutation_target(
        target: ast.AST,
        value: ast.AST | None,
        statement: ast.AST,
    ) -> None:
        nonlocal continuation_escaped
        if isinstance(target, (ast.List, ast.Tuple)):
            for nested in target.elts:
                mutation_target(nested, value, statement)
            return
        if isinstance(target, ast.Starred):
            mutation_target(target.value, value, statement)
            return
        if not isinstance(target, (ast.Attribute, ast.Subscript)):
            return
        stored = contains_continuation(alias_value(value, statement))
        container = contains_continuation(alias_value(target.value, statement))
        continuation_escaped |= stored in {True, None} or container in {True, None}

    class _MutationVisitor(ast.NodeVisitor):
        def visit_Assign(self, node: ast.Assign) -> None:
            for target in node.targets:
                mutation_target(target, node.value, node)
            self.visit(node.value)

        def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
            mutation_target(node.target, node.value, node)
            if node.value is not None:
                self.visit(node.value)

        def visit_AugAssign(self, node: ast.AugAssign) -> None:
            mutation_target(node.target, node.value, node)
            self.visit(node.value)

        def visit_Delete(self, node: ast.Delete) -> None:
            for target in node.targets:
                mutation_target(target, ast.Constant(value=None), node)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            return None

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            return None

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            return None

        def visit_Lambda(self, node: ast.Lambda) -> None:
            return None

    mutation_visitor = _MutationVisitor()
    for statement in function.body:
        mutation_visitor.visit(statement)

    uncertain_call = False

    def call_presence(node: ast.AST | None) -> tuple[bool, bool]:
        """Return (must call, may call) under Python expression evaluation."""

        if node is None or isinstance(node, (ast.Constant, ast.Name)):
            return (False, False)
        if isinstance(node, ast.Call):
            nonlocal uncertain_call
            relation = alias_value(node.func, node.func)
            if relation is True:
                return (True, True)
            if relation is None:
                uncertain_call = True
            if any(
                contains_continuation(alias_value(value, value)) in {True, None}
                for value in (*node.args, *(item.value for item in node.keywords))
            ):
                uncertain_call = True
            values = (node.func, *node.args, *(item.value for item in node.keywords))
            results = tuple(call_presence(item) for item in values)
            return (any(item[0] for item in results), any(item[1] for item in results))
        if isinstance(node, ast.BoolOp):
            results = tuple(call_presence(item) for item in node.values)
            must = bool(results) and results[0][0]
            may = any(item[1] for item in results)
            return (must, may)
        if isinstance(node, ast.Compare):
            guaranteed = (node.left, *node.comparators[:1])
            all_values = (node.left, *node.comparators)
            return (
                any(call_presence(item)[0] for item in guaranteed),
                any(call_presence(item)[1] for item in all_values),
            )
        if isinstance(node, ast.IfExp):
            test = call_presence(node.test)
            body = call_presence(node.body)
            otherwise = call_presence(node.orelse)
            return (
                test[0] or (body[0] and otherwise[0]),
                test[1] or body[1] or otherwise[1],
            )
        if isinstance(
            node,
            (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp, ast.Lambda),
        ):
            return (False, False)
        results = tuple(call_presence(child) for child in ast.iter_child_nodes(node))
        return (any(item[0] for item in results), any(item[1] for item in results))

    def apply_expression(states: set[bool], node: ast.AST | None) -> set[bool]:
        must_call, may_call = call_presence(node)
        if must_call:
            return {True} if states else set()
        if may_call and False in states:
            return states | {True}
        return set(states)

    def walk_block(
        statements: Sequence[ast.stmt],
        incoming: set[bool],
    ) -> tuple[set[bool], list[bool], bool]:
        active = set(incoming)
        returned: list[bool] = []
        for statement in statements:
            if not active:
                break
            if isinstance(statement, ast.Return):
                returned.extend(apply_expression(active, statement.value))
                active.clear()
                continue
            if isinstance(statement, ast.Raise):
                returned.extend(apply_expression(active, statement.exc))
                active.clear()
                continue
            if isinstance(statement, ast.If):
                branch_input = apply_expression(active, statement.test)
                body_active, body_returned, body_unknown = walk_block(
                    statement.body,
                    branch_input,
                )
                else_active, else_returned, else_unknown = (
                    walk_block(statement.orelse, branch_input)
                    if statement.orelse
                    else (set(branch_input), [], False)
                )
                active = body_active | else_active
                returned.extend((*body_returned, *else_returned))
                if body_unknown or else_unknown:
                    return (active, returned, True)
                continue
            if isinstance(statement, ast.Expr):
                active = apply_expression(active, statement.value)
                continue
            if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                active = apply_expression(active, statement.value)
                continue
            if isinstance(
                statement,
                (
                    ast.AsyncFunctionDef,
                    ast.ClassDef,
                    ast.FunctionDef,
                    ast.Global,
                    ast.Import,
                    ast.ImportFrom,
                    ast.Nonlocal,
                    ast.Pass,
                ),
            ):
                continue
            return (active, returned, True)
        return (active, returned, False)

    active, returned, unknown = walk_block(function.body, {False})
    if unknown or uncertain_call or continuation_escaped:
        return None
    terminal_states = (*returned, *active)
    if not terminal_states:
        return None
    return any(not called for called in terminal_states)


def _raised_types(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[tuple[str | None, ast.Raise], ...]:
    """Return only explicit public exception class references.

    A bare raise or computed exception expression is retained as ``None`` so
    an exception-handler arm cannot be guessed from an unrelated registration.
    """

    values: list[tuple[str | None, ast.Raise]] = []

    class _Visitor(ast.NodeVisitor):
        def visit_Raise(self, node: ast.Raise) -> None:
            expression = node.exc
            if isinstance(expression, ast.Call):
                expression = expression.func
            values.append((_dotted(expression), node))

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            return None

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            return None

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            return None

        def visit_Lambda(self, node: ast.Lambda) -> None:
            return None

    visitor = _Visitor()
    for statement in function.body:
        visitor.visit(statement)
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


def _background_targets(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    module: str,
    path: str,
    imports: Mapping[str, Mapping[str, str]],
    import_bindings: _BindingTimeline,
) -> tuple[_Reference, ...] | None:
    targets: list[_Reference] = []
    unresolved = False

    collector = _ScopeBindingVisitor()
    for statement in node.body:
        collector.visit(statement)
    arguments = {
        argument.arg: argument
        for argument in (
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
        )
    }
    arguments.update({
        argument.arg: argument
        for argument in (node.args.vararg, node.args.kwarg)
        if argument is not None
    })

    def typed_background_receiver(raw: str) -> bool:
        argument = arguments.get(raw)
        if argument is None or argument.annotation is None:
            return False
        annotation = argument.annotation
        if isinstance(annotation, ast.Subscript):
            wrapper = _dotted(annotation.value)
            if wrapper is not None and wrapper.rsplit(".", 1)[-1] == "Annotated":
                wrapper_target = _resolved_reference_at(
                    wrapper,
                    module,
                    imports,
                    import_bindings,
                    _line_order(path, annotation.value),
                )
                if wrapper_target not in {
                    ("typing", "Annotated"),
                    ("typing_extensions", "Annotated"),
                }:
                    return False
                annotation = (
                    annotation.slice.elts[0]
                    if isinstance(annotation.slice, ast.Tuple) and annotation.slice.elts
                    else annotation.slice
                )
        annotation_raw = _dotted(annotation)
        if annotation_raw is None:
            return False
        target = _resolved_reference_at(
            annotation_raw,
            module,
            imports,
            import_bindings,
            _line_order(path, annotation),
        )
        return target in {
            ("fastapi", "BackgroundTasks"),
            ("starlette.background", "BackgroundTasks"),
        }

    bound_names = set(arguments) | {name for name, _binding in collector.records}
    local_names = bound_names - collector.global_names - collector.nonlocal_names
    runtime_global_writes = bound_names & collector.global_names
    local_aliases: dict[str, list[tuple[_Order, str | None]]] = {}

    def remember(name: str, binding: ast.AST, identity: str | None) -> None:
        if name in local_names:
            local_aliases.setdefault(name, []).append((
                _line_order(path, binding),
                identity,
            ))

    for name in arguments:
        remember(
            name,
            node,
            _BACKGROUND_TASKS_BINDING if typed_background_receiver(name) else None,
        )

    def final_global_identity(raw: str) -> str | None:
        head, separator, tail = raw.partition(".")
        if head in runtime_global_writes or head in collector.nonlocal_names:
            return None
        binding = _visible_binding(
            import_bindings,
            module,
            head,
            (path, 10**9, 10**9),
        )
        if binding is not None:
            if binding[1] is None:
                return None
            return f"{binding[1]}.{tail}" if separator else binding[1]
        resolved = _resolved_reference(raw, module, imports)
        return ".".join(resolved) if resolved is not None else None

    def identity_at(raw: str, occurrence: _Order) -> str | None:
        head, separator, tail = raw.partition(".")
        if head not in local_names:
            return final_global_identity(raw)
        visible = next(
            (
                item
                for item in reversed(local_aliases.get(head, ()))
                if item[0] <= occurrence
            ),
            None,
        )
        if visible is None or visible[1] is None:
            return None
        return f"{visible[1]}.{tail}" if separator else visible[1]

    def assigned_identity(value: ast.AST | None, order: _Order) -> str | None:
        identity = identity_at(_dotted(value) or "", order)
        if identity is None and isinstance(
            value,
            (
                ast.Constant,
                ast.Dict,
                ast.DictComp,
                ast.GeneratorExp,
                ast.Lambda,
                ast.List,
                ast.ListComp,
                ast.Set,
                ast.SetComp,
                ast.Tuple,
            ),
        ):
            return _NON_FRAMEWORK_BINDING
        return identity

    def remember_assignment(
        target: ast.AST,
        value: ast.AST | None,
        statement: ast.AST,
    ) -> None:
        if isinstance(target, ast.Name):
            remember(
                target.id,
                statement,
                assigned_identity(value, _line_order(path, value or statement)),
            )
            return
        if isinstance(target, (ast.List, ast.Tuple)):
            if isinstance(value, (ast.List, ast.Tuple)) and len(target.elts) == len(
                value.elts
            ):
                for nested_target, nested_value in zip(
                    target.elts,
                    value.elts,
                    strict=True,
                ):
                    remember_assignment(nested_target, nested_value, statement)
                return
            visitor = _ScopeBindingVisitor()
            visitor.visit(target)
            for name, binding in visitor.records:
                remember(name, binding, None)
            return
        if isinstance(target, ast.Starred):
            remember_assignment(target.value, None, statement)

    for statement in node.body:
        if isinstance(statement, ast.Assign):
            for target in statement.targets:
                remember_assignment(target, statement.value, statement)
            continue
        if isinstance(statement, ast.AnnAssign):
            remember_assignment(statement.target, statement.value, statement)
            continue
        if isinstance(statement, ast.Import):
            for alias in statement.names:
                remember(
                    alias.asname or alias.name.split(".", 1)[0],
                    statement,
                    alias.name if alias.asname else alias.name.split(".", 1)[0],
                )
            continue
        if isinstance(statement, ast.ImportFrom):
            base = _import_from_base(path, module, statement)
            for alias in statement.names:
                if alias.name != "*":
                    remember(
                        alias.asname or alias.name,
                        statement,
                        f"{base}.{alias.name}" if base is not None else None,
                    )
            continue
        visitor = _ScopeBindingVisitor()
        visitor.visit(statement)
        for name, binding in visitor.records:
            remember(name, binding, None)

    class _Visitor(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:
            nonlocal unresolved
            if isinstance(node.func, ast.Attribute) and node.func.attr == "add_task":
                receiver = _dotted(node.func.value)
                receiver_binding = (
                    identity_at(receiver, _line_order(path, node.func.value))
                    if receiver is not None
                    else None
                )
                if receiver_binding == _NON_FRAMEWORK_BINDING:
                    return
                if receiver_binding != _BACKGROUND_TASKS_BINDING:
                    unresolved = True
                    return
                if not node.args:
                    unresolved = True
                    return
                raw = _dotted(node.args[0])
                if raw is None:
                    unresolved = True
                    return
                targets.append(
                    _Reference(
                        raw,
                        (
                            tuple(identity.rsplit(".", 1))
                            if (
                                identity := identity_at(
                                    raw,
                                    _line_order(path, node.args[0]),
                                )
                            )
                            and "." in identity
                            else None
                        ),
                    )
                )
            self.generic_visit(node)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            return None

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            return None

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            return None

        def visit_Lambda(self, node: ast.Lambda) -> None:
            return None

    visitor = _Visitor()
    for statement in node.body:
        visitor.visit(statement)
    return None if unresolved else tuple(targets)


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


def _import_from_base(
    path: str,
    module: str,
    node: ast.ImportFrom,
) -> str | None:
    base = node.module or ""
    if node.level:
        safe = _safe_path(path)
        if safe is None:
            return None
        package = (
            module
            if safe.rsplit("/", 1)[-1] == "__init__.py"
            else module.rpartition(".")[0]
        )
        if not package:
            return None
        parent = package.split(".")
        levels = node.level - 1
        if levels >= len(parent):
            return None
        prefix = parent[: len(parent) - levels]
        base = ".".join(prefix + ([base] if base else []))
    return base if base and _DOTTED_RE.fullmatch(base) else None


def _wildcard_exports(
    parsed: Mapping[str, tuple[str, ast.Module, str]],
    reachability: frozenset[tuple[str, str]],
) -> _WildcardExports:
    absent = object()
    descriptions: dict[
        str,
        tuple[object | tuple[str, ...] | None, set[str], tuple[str | None, ...], bool],
    ] = {}

    def literal_all(value: ast.AST | None) -> tuple[str, ...] | None:
        if not isinstance(value, (ast.List, ast.Tuple)):
            return None
        names = tuple(
            item.value
            for item in value.elts
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
        )
        if len(names) != len(value.elts):
            return None
        return tuple(dict.fromkeys(names))

    for path, (_source, tree, module) in parsed.items():
        explicit_all: object | tuple[str, ...] | None = absent
        public: set[str] = set()
        wildcard_bases: list[str | None] = []
        uncertain_public = False
        for node in tree.body:
            if isinstance(node, ast.Import):
                public.update(
                    alias.asname or alias.name.split(".", 1)[0] for alias in node.names
                )
                continue
            if isinstance(node, ast.ImportFrom):
                base = _import_from_base(path, module, node)
                for alias in node.names:
                    if alias.name == "*":
                        wildcard_bases.append(base)
                    else:
                        public.add(alias.asname or alias.name)
                continue
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                public.add(node.name)
                continue
            if isinstance(node, ast.Assign):
                direct_names = tuple(
                    target.id for target in node.targets if isinstance(target, ast.Name)
                )
                public.update(direct_names)
                if "__all__" in direct_names:
                    explicit_all = literal_all(node.value)
                if len(direct_names) != len(node.targets):
                    visitor = _ScopeBindingVisitor()
                    for target in node.targets:
                        visitor.visit(target)
                    public.update(name for name, _binding in visitor.records)
                    uncertain_public = True
                continue
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                public.add(node.target.id)
                if node.target.id == "__all__":
                    explicit_all = literal_all(node.value)
                continue
            visitor = _ScopeBindingVisitor()
            visitor.visit(node)
            names = {name for name, _binding in visitor.records}
            if names:
                public.update(names)
                uncertain_public = True
                if "__all__" in names:
                    explicit_all = None
        descriptions[module] = (
            explicit_all,
            {name for name in public if not name.startswith("_")},
            tuple(wildcard_bases),
            uncertain_public,
        )

    previous: dict[str, tuple[str, ...] | None] = {}
    for module, (
        explicit_all,
        public,
        wildcard_bases,
        uncertain,
    ) in descriptions.items():
        if explicit_all is not absent:
            previous[module] = explicit_all  # type: ignore[assignment]
        elif uncertain or wildcard_bases:
            previous[module] = None
        else:
            previous[module] = tuple(sorted(public))

    for _iteration in range(len(descriptions) + 1):
        current: dict[str, tuple[str, ...] | None] = {}
        for module, (
            explicit_all,
            public,
            wildcard_bases,
            uncertain,
        ) in descriptions.items():
            if explicit_all is not absent:
                current[module] = explicit_all  # type: ignore[assignment]
                continue
            names = set(public)
            unresolved = uncertain
            for base in wildcard_bases:
                if (
                    base not in descriptions
                    or base is None
                    or (base != module and (base, module) in reachability)
                    or previous.get(base) is None
                ):
                    unresolved = True
                    continue
                names.update(previous[base] or ())
            current[module] = None if unresolved else tuple(sorted(names))
        if current == previous:
            break
        previous = current
    return MappingProxyType(previous)


def _imports_for(
    path: str,
    module: str,
    tree: ast.Module,
    wildcard_exports: _WildcardExports,
    reachability: frozenset[tuple[str, str]],
) -> Mapping[str, str]:
    bindings: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".", 1)[0]
                bindings[local] = alias.name
        elif isinstance(node, ast.ImportFrom):
            base = _import_from_base(path, module, node)
            if base is None:
                continue
            for alias in node.names:
                if alias.name == "*":
                    if base != module and (base, module) in reachability:
                        continue
                    for name in wildcard_exports.get(base) or ():
                        bindings[name] = f"{base}.{name}"
                    continue
                bindings[alias.asname or alias.name] = f"{base}.{alias.name}"
    return MappingProxyType(bindings)


def _import_bindings(
    parsed: Mapping[str, tuple[str, ast.Module, str]],
    wildcard_exports: _WildcardExports,
    reachability: frozenset[tuple[str, str]],
) -> _BindingTimeline:
    bindings: dict[tuple[str, str], list[tuple[_Order, str | None]]] = {}

    def remember(
        module: str,
        name: str,
        order: _Order,
        identity: str | None,
    ) -> None:
        bindings.setdefault((module, name), []).append((order, identity))

    def resolve(raw: str | None, module: str, occurrence: _Order) -> str | None:
        if raw is None:
            return None
        head, separator, tail = raw.partition(".")
        visible = next(
            (
                item
                for item in reversed(bindings.get((module, head), ()))
                if item[0] <= occurrence
            ),
            None,
        )
        if visible is not None:
            if visible[1] is None:
                return None
            return f"{visible[1]}.{tail}" if separator else visible[1]
        return raw if separator else f"{module}.{raw}"

    for path, (_source, tree, module) in parsed.items():
        for node in tree.body:
            order = _line_order(path, node)
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.asname or alias.name.split(".", 1)[0]
                    identity = alias.name if alias.asname else name
                    remember(module, name, order, identity)
            elif isinstance(node, ast.ImportFrom):
                base = _import_from_base(path, module, node)
                for alias in node.names:
                    if alias.name == "*":
                        names = (
                            wildcard_exports.get(base)
                            if base is not None
                            and not (base != module and (base, module) in reachability)
                            else None
                        )
                        if names is None:
                            remember(
                                module,
                                _WILDCARD_BINDING,
                                order,
                                None,
                            )
                            continue
                        for name in names:
                            remember(
                                module,
                                name,
                                order,
                                f"{base}.{name}",
                            )
                        continue
                    name = alias.asname or alias.name
                    identity = f"{base}.{alias.name}" if base is not None else None
                    remember(module, name, order, identity)
            elif isinstance(node, ast.Assign):
                identity = resolve(_dotted(node.value), module, order)
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        remember(module, target.id, order, identity)
                    else:
                        visitor = _ScopeBindingVisitor()
                        visitor.visit(target)
                        for name, binding_node in visitor.records:
                            remember(
                                module,
                                name,
                                _line_order(path, binding_node),
                                None,
                            )
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                remember(
                    module,
                    node.target.id,
                    order,
                    resolve(_dotted(node.value), module, order),
                )
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                remember(
                    module,
                    node.name,
                    order,
                    f"{module}.{node.name}" if not node.decorator_list else None,
                )
                header_visitor = _ScopeBindingVisitor()
                _visit_function_header(header_visitor, node)
                for name, binding_node in header_visitor.records:
                    remember(module, name, _line_order(path, binding_node), None)
            elif isinstance(node, ast.ClassDef):
                remember(module, node.name, order, None)
                header_visitor = _ScopeBindingVisitor()
                _visit_class_header(header_visitor, node)
                for name, binding_node in header_visitor.records:
                    remember(module, name, _line_order(path, binding_node), None)
                for name, binding_node in _class_global_binding_records(node):
                    remember(module, name, _line_order(path, binding_node), None)
            else:
                visitor = _ScopeBindingVisitor()
                visitor.visit(node)
                for name, binding_node in visitor.records:
                    remember(module, name, _line_order(path, binding_node), None)
    return MappingProxyType({
        key: tuple(sorted(values, key=lambda item: item[0]))
        for key, values in bindings.items()
    })


def _module_import_reachability(
    parsed: Mapping[str, tuple[str, ast.Module, str]],
) -> frozenset[tuple[str, str]]:
    known_modules = {module for _source, _tree, module in parsed.values()}
    graph: dict[str, set[str]] = {module: set() for module in known_modules}
    for path, (_source, tree, module) in parsed.items():
        for node in tree.body:
            if isinstance(node, ast.Import):
                graph[module].update(
                    alias.name for alias in node.names if alias.name in known_modules
                )
            elif isinstance(node, ast.ImportFrom):
                base = _import_from_base(path, module, node)
                if base in known_modules:
                    graph[module].add(base)

    reachable: set[tuple[str, str]] = set()
    for source in known_modules:
        pending = list(graph[source])
        visited: set[str] = set()
        while pending:
            target = pending.pop()
            if target in visited:
                continue
            visited.add(target)
            reachable.add((source, target))
            pending.extend(graph.get(target, ()))
    return frozenset(reachable)


_RESPONSE_TARGETS = frozenset(
    (module, name)
    for module in ("fastapi", "fastapi.responses", "starlette.responses")
    for name in (
        "FileResponse",
        "HTMLResponse",
        "JSONResponse",
        "PlainTextResponse",
        "RedirectResponse",
        "Response",
        "StreamingResponse",
    )
) | frozenset({
    ("fastapi.responses", "ORJSONResponse"),
    ("fastapi.responses", "UJSONResponse"),
})
_KNOWN_NON_RESPONSE_NAMES = frozenset({
    "Any",
    "None",
    "bool",
    "bytes",
    "complex",
    "dict",
    "float",
    "frozenset",
    "int",
    "list",
    "object",
    "set",
    "str",
    "tuple",
    "type",
})


def _response_class_bindings(
    parsed: Mapping[str, tuple[str, ast.Module, str]],
    imports: Mapping[str, Mapping[str, str]],
    import_bindings: _BindingTimeline,
) -> _ResponseClassTimeline:
    declarations: dict[tuple[str, str, _Order], ast.ClassDef] = {}
    paths: dict[int, str] = {}
    modules: dict[int, str] = {}
    for path, (_source, tree, module) in parsed.items():
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                declarations[(module, node.name, _line_order(path, node))] = node
                paths[id(node)] = path
                modules[id(node)] = module

    known: dict[int, bool | None] = {}
    active: set[int] = set()

    def target_status(
        raw: str | None,
        module: str,
        path: str,
        occurrence: _Order,
    ) -> bool | None:
        if raw is None:
            return None
        if raw in _KNOWN_NON_RESPONSE_NAMES:
            return False
        target = _resolved_reference_at(
            raw,
            module,
            imports,
            import_bindings,
            occurrence,
        )
        if target in _RESPONSE_TARGETS:
            return True
        if target is not None and target[0] in {
            "builtins",
            "collections.abc",
            "typing",
            "typing_extensions",
        }:
            return False
        head, separator, _tail = raw.partition(".")
        if separator:
            return None
        visible = _visible_binding(import_bindings, module, head, occurrence)
        if visible is None:
            return None
        declaration = declarations.get((module, head, visible[0]))
        return status(declaration) if declaration is not None else None

    def status(node: ast.ClassDef) -> bool | None:
        if id(node) in known:
            return known[id(node)]
        if id(node) in active:
            return None
        if node.decorator_list or any(
            keyword.arg == "metaclass" for keyword in node.keywords
        ):
            known[id(node)] = None
            return None
        if not node.bases:
            known[id(node)] = False
            return False
        active.add(id(node))
        path = paths[id(node)]
        module = modules[id(node)]
        bases = tuple(
            target_status(
                _dotted(base),
                module,
                path,
                _line_order(path, base),
            )
            for base in node.bases
        )
        active.remove(id(node))
        result = (
            None
            if any(item is None for item in bases)
            else True
            if True in bases
            else False
        )
        known[id(node)] = result
        return result

    values: dict[tuple[str, str], list[tuple[_Order, bool | None]]] = {}
    for (module, name, order), declaration in declarations.items():
        values.setdefault((module, name), []).append((order, status(declaration)))
    return MappingProxyType({
        key: tuple(sorted(items, key=lambda item: item[0]))
        for key, items in values.items()
    })


def _response_annotation_relation(
    annotation: ast.AST | None,
    module: str,
    path: str,
    imports: Mapping[str, Mapping[str, str]],
    import_bindings: _BindingTimeline,
    response_classes: _ResponseClassTimeline,
    *,
    occurrence: _Order | None = None,
) -> bool | None:
    """Return whether a return annotation is exactly a Response subclass."""

    if annotation is None:
        return False
    occurrence = occurrence or _line_order(path, annotation)
    if isinstance(annotation, ast.Constant):
        if annotation.value is None:
            return False
        if not isinstance(annotation.value, str) or not annotation.value.strip():
            return False
        try:
            parsed = ast.parse(annotation.value, mode="eval").body
        except SyntaxError:
            return None
        return _response_annotation_relation(
            parsed,
            module,
            path,
            imports,
            import_bindings,
            response_classes,
            occurrence=occurrence,
        )
    if not isinstance(annotation, (ast.Name, ast.Attribute)):
        return False
    raw = _dotted(annotation)
    if raw is None:
        return None
    if raw in _KNOWN_NON_RESPONSE_NAMES:
        return False
    target = _resolved_reference_at(
        raw,
        module,
        imports,
        import_bindings,
        occurrence,
    )
    if target in _RESPONSE_TARGETS:
        return True
    if target is not None and target[0] in {
        "builtins",
        "collections.abc",
        "typing",
        "typing_extensions",
    }:
        return False
    head, separator, _tail = raw.partition(".")
    if separator:
        return None
    visible = _visible_binding(import_bindings, module, head, occurrence)
    if visible is None:
        return None
    status = next(
        (
            value
            for order, value in reversed(response_classes.get((module, head), ()))
            if order == visible[0]
        ),
        None,
    )
    return status


def _visible_binding(
    bindings: _BindingTimeline,
    module: str,
    name: str,
    occurrence: _Order,
) -> tuple[_Order, str | None] | None:
    return next(
        (
            item
            for item in reversed(bindings.get((module, name), ()))
            if item[0] <= occurrence
        ),
        None,
    )


def _resolved_reference_at(
    raw: str,
    module: str,
    imports: Mapping[str, Mapping[str, str]],
    import_bindings: _BindingTimeline,
    occurrence: _Order,
) -> tuple[str, str] | None:
    if not raw:
        return None
    head, separator, tail = raw.partition(".")
    binding = _visible_binding(import_bindings, module, head, occurrence)
    wildcard = _visible_binding(
        import_bindings,
        module,
        _WILDCARD_BINDING,
        occurrence,
    )
    if wildcard is not None and (binding is None or wildcard[0] > binding[0]):
        return None
    if binding is not None:
        if binding[1] is None:
            return None
        dotted = f"{binding[1]}.{tail}" if separator else binding[1]
    elif (module, head) in import_bindings:
        return None
    else:
        return _resolved_reference(raw, module, imports)
    if not _DOTTED_RE.fullmatch(dotted) or "." not in dotted:
        return None
    target_module, target_name = dotted.rsplit(".", 1)
    return (target_module, target_name)


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


def _exception_identity(
    raw: str | None,
    module: str,
    imports: Mapping[str, Mapping[str, str]],
    aliases: _BindingTimeline,
    *,
    occurrence: _Order | None,
) -> str | None:
    def final_alias(identity: str, seen: frozenset[str]) -> str | None:
        if identity in seen or "." not in identity:
            return None if identity in seen else identity
        target_module, target_name = identity.rsplit(".", 1)
        timeline = aliases.get((target_module, target_name))
        if not timeline:
            return identity
        target = timeline[-1][1]
        if target is None:
            return None
        if target == identity:
            return identity
        return final_alias(target, seen | {identity})

    if raw is None:
        return None
    head, separator, tail = raw.partition(".")
    bindings = aliases.get((module, head), ())
    visible = (
        bindings
        if occurrence is None
        else tuple(item for item in bindings if item[0] <= occurrence)
    )
    wildcard_bindings = aliases.get((module, _WILDCARD_BINDING), ())
    visible_wildcards = (
        wildcard_bindings
        if occurrence is None
        else tuple(item for item in wildcard_bindings if item[0] <= occurrence)
    )
    if visible_wildcards and (not visible or visible_wildcards[-1][0] > visible[-1][0]):
        return None
    if visible:
        identity = visible[-1][1]
        if identity is None:
            return None
        resolved = f"{identity}.{tail}" if separator else identity
        return (
            final_alias(resolved, frozenset())
            if _DOTTED_RE.fullmatch(resolved)
            else None
        )
    if raw in {"BaseException", "Exception"}:
        return f"builtins.{raw}"
    resolved = _resolved_reference(raw, module, imports)
    return (
        final_alias(".".join(resolved), frozenset()) if resolved is not None else None
    )


def _class_identity_preserved(node: ast.ClassDef) -> bool:
    return not node.decorator_list and not any(
        keyword.arg == "metaclass" for keyword in node.keywords
    )


def _exception_aliases(
    parsed: Mapping[str, tuple[str, ast.Module, str]],
    class_identities: Mapping[int, str],
    wildcard_exports: _WildcardExports,
    reachability: frozenset[tuple[str, str]],
    proven_mros: Mapping[str, tuple[str, ...] | None] | None = None,
) -> _BindingTimeline:
    """Resolve source-visible exception bindings at each use occurrence."""

    aliases: dict[
        tuple[str, str],
        list[tuple[tuple[str, int, int], str | None]],
    ] = {}

    def resolve(
        raw: str | None,
        module: str,
        occurrence: _Order,
    ) -> str | None:
        if raw is None:
            return None
        head, separator, tail = raw.partition(".")
        visible = tuple(
            item for item in aliases.get((module, head), ()) if item[0] <= occurrence
        )
        wildcard = tuple(
            item
            for item in aliases.get((module, _WILDCARD_BINDING), ())
            if item[0] <= occurrence
        )
        if wildcard and (not visible or wildcard[-1][0] > visible[-1][0]):
            return None
        if visible:
            identity = visible[-1][1]
            if identity is None:
                return None
            resolved = f"{identity}.{tail}" if separator else identity
            return resolved if _DOTTED_RE.fullmatch(resolved) else None
        if raw in {"BaseException", "Exception"}:
            return f"builtins.{raw}"
        return None

    def remember(
        module: str,
        name: str,
        order: _Order,
        identity: str | None,
    ) -> None:
        aliases.setdefault((module, name), []).append((order, identity))

    def class_header_bindings(
        node: ast.ClassDef,
    ) -> tuple[tuple[ast.NamedExpr, bool], ...]:
        values: list[tuple[ast.NamedExpr, bool]] = []

        class _Visitor(ast.NodeVisitor):
            def __init__(self) -> None:
                self.conditional_depth = 0

            def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
                values.append((node, bool(self.conditional_depth)))
                self.visit(node.value)

            def visit_IfExp(self, node: ast.IfExp) -> None:
                self.visit(node.test)
                self.conditional_depth += 1
                self.visit(node.body)
                self.visit(node.orelse)
                self.conditional_depth -= 1

            def visit_BoolOp(self, node: ast.BoolOp) -> None:
                if not node.values:
                    return
                self.visit(node.values[0])
                self.conditional_depth += 1
                for value in node.values[1:]:
                    self.visit(value)
                self.conditional_depth -= 1

            def _visit_conditional(self, node: ast.AST) -> None:
                self.conditional_depth += 1
                self.generic_visit(node)
                self.conditional_depth -= 1

            def visit_ListComp(self, node: ast.ListComp) -> None:
                self._visit_conditional(node)

            def visit_SetComp(self, node: ast.SetComp) -> None:
                self._visit_conditional(node)

            def visit_DictComp(self, node: ast.DictComp) -> None:
                self._visit_conditional(node)

            def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
                self._visit_conditional(node)

        visitor = _Visitor()
        _visit_class_header(visitor, node)
        return tuple(sorted(values, key=lambda item: _line_order(path, item[0])))

    def class_global_values(node: ast.ClassDef) -> Mapping[int, str | None]:
        values: dict[int, str | None] = {}

        class _GlobalCollector(ast.NodeVisitor):
            def __init__(self) -> None:
                self.names: set[str] = set()

            def visit_Global(self, node: ast.Global) -> None:
                self.names.update(node.names)

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                return None

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
                return None

            def visit_ClassDef(self, node: ast.ClassDef) -> None:
                return None

            def visit_Lambda(self, node: ast.Lambda) -> None:
                return None

        globals_visitor = _GlobalCollector()
        for statement in node.body:
            globals_visitor.visit(statement)
        for statement in node.body:
            if isinstance(statement, ast.Assign):
                for target in statement.targets:
                    if (
                        isinstance(target, ast.Name)
                        and target.id in globals_visitor.names
                    ):
                        values[id(target)] = _dotted(statement.value)
            elif isinstance(statement, ast.AnnAssign) and isinstance(
                statement.target, ast.Name
            ):
                if statement.target.id in globals_visitor.names:
                    values[id(statement.target)] = _dotted(statement.value)
            if isinstance(statement, ast.ClassDef):
                values.update(class_global_values(statement))
        return MappingProxyType(values)

    for path, (_source, tree, module) in parsed.items():
        for node in tree.body:
            order = _line_order(path, node)
            if isinstance(node, ast.ClassDef):
                for binding, conditional in class_header_bindings(node):
                    if not isinstance(binding.target, ast.Name):
                        continue
                    binding_order = _line_order(path, binding)
                    remember(
                        module,
                        binding.target.id,
                        binding_order,
                        None
                        if conditional
                        else resolve(_dotted(binding.value), module, binding_order),
                    )
                direct_values = class_global_values(node)
                for name, binding_node in _class_global_binding_records(node):
                    binding_order = _line_order(path, binding_node)
                    remember(
                        module,
                        name,
                        binding_order,
                        resolve(
                            direct_values[id(binding_node)],
                            module,
                            binding_order,
                        )
                        if id(binding_node) in direct_values
                        else None,
                    )
                binding_order = (
                    path,
                    int(getattr(node, "end_lineno", node.lineno)),
                    int(getattr(node, "end_col_offset", node.col_offset)) + 1,
                )
                identity = class_identities[id(node)]
                remember(
                    module,
                    node.name,
                    binding_order,
                    identity
                    if proven_mros is None or proven_mros.get(identity) is not None
                    else None,
                )
                continue
            if isinstance(node, ast.Import):
                for alias in node.names:
                    remember(
                        module,
                        alias.asname or alias.name.split(".", 1)[0],
                        order,
                        alias.name if alias.asname else alias.name.split(".", 1)[0],
                    )
                continue
            if isinstance(node, ast.ImportFrom):
                base = _import_from_base(path, module, node)
                for alias in node.names:
                    if alias.name == "*":
                        names = (
                            wildcard_exports.get(base)
                            if base is not None
                            and not (base != module and (base, module) in reachability)
                            else None
                        )
                        if names is None:
                            remember(
                                module,
                                _WILDCARD_BINDING,
                                order,
                                None,
                            )
                            continue
                        for name in names:
                            identity = f"{base}.{name}"
                            if (
                                proven_mros is not None
                                and identity in proven_mros
                                and proven_mros[identity] is None
                            ):
                                identity = None
                            remember(module, name, order, identity)
                        continue
                    name = alias.asname or alias.name
                    identity = f"{base}.{alias.name}" if base is not None else None
                    if (
                        identity is not None
                        and proven_mros is not None
                        and identity in proven_mros
                        and proven_mros[identity] is None
                    ):
                        identity = None
                    remember(module, name, order, identity)
                continue
            targets: tuple[ast.Name, ...] = ()
            value: ast.AST | None = None
            if isinstance(node, ast.Assign):
                targets = tuple(
                    target for target in node.targets if isinstance(target, ast.Name)
                )
                value = node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                targets = (node.target,)
                value = node.value
            if targets:
                identity = resolve(_dotted(value), module, order)
                for target in targets:
                    remember(module, target.id, order, identity)
                continue
            visitor = _ScopeBindingVisitor()
            visitor.visit(node)
            for name, binding_node in visitor.records:
                remember(module, name, _line_order(path, binding_node), None)
    return MappingProxyType({
        key: tuple(sorted(values, key=lambda item: item[0]))
        for key, values in aliases.items()
    })


def _exception_class_identities(
    parsed: Mapping[str, tuple[str, ast.Module, str]],
) -> Mapping[int, str]:
    declarations: dict[tuple[str, str], list[tuple[str, ast.ClassDef]]] = {}
    for path, (_source, tree, module) in parsed.items():
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                declarations.setdefault((module, node.name), []).append((path, node))

    identities: dict[int, str] = {}
    for (module, name), values in declarations.items():
        repeated = len(values) > 1
        for path, node in values:
            identity = f"{module}.{name}"
            if repeated:
                order = _line_order(path, node)
                identity = f"{identity}.__hades_occurrence_{order[1]}_{order[2]}"
            identities[id(node)] = identity
    return MappingProxyType(identities)


def _raised_exception_identities(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    path: str,
    module: str,
    imports: Mapping[str, Mapping[str, str]],
    aliases: _BindingTimeline,
) -> tuple[str | None, ...]:
    """Resolve endpoint raises using Python's lexical/runtime name lookup."""

    collector = _ScopeBindingVisitor()
    for statement in function.body:
        collector.visit(statement)

    argument_names = {
        argument.arg
        for argument in (
            *function.args.posonlyargs,
            *function.args.args,
            *function.args.kwonlyargs,
        )
    }
    argument_names.update(
        argument.arg
        for argument in (function.args.vararg, function.args.kwarg)
        if argument is not None
    )
    bound_names = argument_names | {name for name, _node in collector.records}
    local_names = bound_names - collector.global_names - collector.nonlocal_names
    runtime_global_writes = bound_names & collector.global_names
    local_aliases: dict[str, list[tuple[_Order, str | None]]] = {}

    def remember(name: str, node: ast.AST, identity: str | None) -> None:
        if name in local_names:
            local_aliases.setdefault(name, []).append((
                _line_order(path, node),
                identity,
            ))

    for name in argument_names:
        if name in local_names:
            local_aliases.setdefault(name, []).append((
                _line_order(path, function),
                None,
            ))

    def resolve(raw: str | None, occurrence: _Order) -> str | None:
        if raw is None:
            return None
        head, separator, tail = raw.partition(".")
        if head in runtime_global_writes or head in collector.nonlocal_names:
            return None
        if head in local_names:
            visible = tuple(
                item for item in local_aliases.get(head, ()) if item[0] <= occurrence
            )
            if not visible or visible[-1][1] is None:
                return None
            identity = visible[-1][1]
            assert identity is not None
            resolved = f"{identity}.{tail}" if separator else identity
            return resolved if _DOTTED_RE.fullmatch(resolved) else None
        return _exception_identity(
            raw,
            module,
            imports,
            aliases,
            occurrence=None,
        )

    for statement in function.body:
        order = _line_order(path, statement)
        targets: tuple[ast.Name, ...] = ()
        value: ast.AST | None = None
        if isinstance(statement, ast.Assign):
            targets = tuple(
                target for target in statement.targets if isinstance(target, ast.Name)
            )
            value = statement.value
        elif isinstance(statement, ast.AnnAssign) and isinstance(
            statement.target, ast.Name
        ):
            targets = (statement.target,)
            value = statement.value
        if targets:
            identity = resolve(_dotted(value), order)
            for target in targets:
                remember(target.id, statement, identity)
            continue
        if isinstance(statement, ast.Import):
            for alias in statement.names:
                name = alias.asname or alias.name.split(".", 1)[0]
                identity = alias.name if alias.asname else name
                remember(name, statement, identity)
            continue
        if isinstance(statement, ast.ImportFrom):
            base = _import_from_base(path, module, statement)
            for alias in statement.names:
                if alias.name != "*":
                    name = alias.asname or alias.name
                    identity = f"{base}.{alias.name}" if base is not None else None
                    remember(name, statement, identity)
            continue
        visitor = _ScopeBindingVisitor()
        visitor.visit(statement)
        for name, node in visitor.records:
            remember(name, node, None)

    return tuple(
        resolve(raw, _line_order(path, raise_node))
        for raw, raise_node in _raised_types(function)
    )


def _exception_mros(
    parsed: Mapping[str, tuple[str, ast.Module, str]],
    imports: Mapping[str, Mapping[str, str]],
    aliases: _BindingTimeline,
    class_identities: Mapping[int, str],
) -> Mapping[str, tuple[str, ...] | None]:
    """Build only fully proven Python MROs for source-visible classes."""

    bases_by_class: dict[str, tuple[str, ...] | None] = {}
    for path, (_source, tree, module) in parsed.items():
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            identity = class_identities[id(node)]
            if not _class_identity_preserved(node):
                bases_by_class[identity] = None
                continue
            if not node.bases:
                bases_by_class[identity] = ("builtins.object",)
                continue
            bases = tuple(
                _exception_identity(
                    _dotted(base),
                    module,
                    imports,
                    aliases,
                    occurrence=_line_order(path, base),
                )
                for base in node.bases
            )
            bases_by_class[identity] = (
                tuple(item for item in bases if item is not None)
                if all(item is not None for item in bases)
                else None
            )

    known: dict[str, tuple[str, ...] | None] = {
        "builtins.object": ("builtins.object",),
        "builtins.BaseException": (
            "builtins.BaseException",
            "builtins.object",
        ),
        "builtins.Exception": (
            "builtins.Exception",
            "builtins.BaseException",
            "builtins.object",
        ),
    }
    active: set[str] = set()

    def linearize(identity: str) -> tuple[str, ...] | None:
        if identity in known:
            return known[identity]
        bases = bases_by_class.get(identity)
        if bases is None or identity in active:
            known[identity] = None
            return None
        active.add(identity)
        parent_mros = tuple(linearize(base) for base in bases)
        if any(item is None for item in parent_mros):
            active.remove(identity)
            known[identity] = None
            return None
        sequences = [list(item or ()) for item in parent_mros]
        sequences.append(list(bases))
        merged: list[str] = []
        while any(sequences):
            sequences = [sequence for sequence in sequences if sequence]
            candidate = next(
                (
                    sequence[0]
                    for sequence in sequences
                    if not any(sequence[0] in other[1:] for other in sequences)
                ),
                None,
            )
            if candidate is None:
                active.remove(identity)
                known[identity] = None
                return None
            merged.append(candidate)
            for sequence in sequences:
                if sequence and sequence[0] == candidate:
                    sequence.pop(0)
        active.remove(identity)
        result = (identity, *merged)
        known[identity] = result
        return result

    for identity in bases_by_class:
        linearize(identity)
    return MappingProxyType(known)


def _class_global_binding_records(
    node: ast.ClassDef,
) -> tuple[tuple[str, ast.AST], ...]:
    """Return stores that a class body explicitly directs to module globals."""

    class _GlobalCollector(ast.NodeVisitor):
        def __init__(self) -> None:
            self.names: set[str] = set()

        def visit_Global(self, node: ast.Global) -> None:
            self.names.update(node.names)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            return None

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            return None

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            return None

        def visit_Lambda(self, node: ast.Lambda) -> None:
            return None

    globals_visitor = _GlobalCollector()
    for statement in node.body:
        globals_visitor.visit(statement)

    class _StoreCollector(_ScopeBindingVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._record(node.name, node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._record(node.name, node)

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self._record(node.name, node)

        def visit_Lambda(self, node: ast.Lambda) -> None:
            return None

    stores = _StoreCollector()
    for statement in node.body:
        stores.visit(statement)
    records = [
        (name, binding_node)
        for name, binding_node in stores.records
        if name in globals_visitor.names
    ]

    nested_classes: list[ast.ClassDef] = []

    class _NestedClassCollector(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            return None

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            return None

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            nested_classes.append(node)

        def visit_Lambda(self, node: ast.Lambda) -> None:
            return None

    nested_visitor = _NestedClassCollector()
    for statement in node.body:
        nested_visitor.visit(statement)
    for nested in nested_classes:
        records.extend(_class_global_binding_records(nested))
    return tuple(records)


def _module_binding_orders(
    parsed: Mapping[str, tuple[str, ast.Module, str]],
) -> Mapping[tuple[str, str], tuple[_Order, ...]]:
    values: dict[tuple[str, str], list[_Order]] = {}
    for path, (_source, tree, module) in parsed.items():
        visitor = _ScopeBindingVisitor()
        for statement in tree.body:
            visitor.visit(statement)
            if isinstance(statement, ast.ClassDef):
                for name, binding_node in _class_global_binding_records(statement):
                    values.setdefault((module, name), []).append(
                        _line_order(path, binding_node)
                    )
        for name, binding_node in visitor.records:
            values.setdefault((module, name), []).append(
                _line_order(path, binding_node)
            )
    return MappingProxyType({
        key: tuple(sorted(set(orders))) for key, orders in values.items()
    })


def _framework_constructor_kind(
    call: ast.Call,
    module: str,
    imports: Mapping[str, Mapping[str, str]],
    import_bindings: _BindingTimeline,
    module_bindings: Mapping[tuple[str, str], tuple[_Order, ...]],
    occurrence: _Order,
) -> str | None:
    raw = _call_name(call)
    if raw is None:
        return None
    head = raw.split(".", 1)[0]
    import_binding = _visible_binding(import_bindings, module, head, occurrence)
    if import_binding is None or import_binding[1] is None:
        return None
    latest_store = next(
        (
            order
            for order in reversed(module_bindings.get((module, head), ()))
            if order < occurrence
        ),
        None,
    )
    if latest_store is not None and latest_store > import_binding[0]:
        return None
    target = _resolved_reference_at(
        raw,
        module,
        imports,
        import_bindings,
        occurrence,
    )
    if target in {
        ("fastapi", "FastAPI"),
        ("fastapi.applications", "FastAPI"),
    }:
        return "app"
    if target in {
        ("fastapi", "APIRouter"),
        ("fastapi.routing", "APIRouter"),
    }:
        return "router"
    return None


def _framework_annotation_kind(
    annotation: ast.AST,
    path: str,
    module: str,
    imports: Mapping[str, Mapping[str, str]],
    import_bindings: _BindingTimeline,
) -> str | None:
    raw = _dotted(annotation)
    if raw is None:
        return None
    target = _resolved_reference_at(
        raw,
        module,
        imports,
        import_bindings,
        _line_order(path, annotation),
    )
    if target in {
        ("fastapi", "FastAPI"),
        ("fastapi.applications", "FastAPI"),
    }:
        return "app"
    if target in {
        ("fastapi", "APIRouter"),
        ("fastapi.routing", "APIRouter"),
    }:
        return "router"
    return None


def _object_reference(
    raw: str | None,
    module: str,
    imports: Mapping[str, Mapping[str, str]],
    import_bindings: _BindingTimeline,
    object_bindings: _ObjectBindingTimeline,
    objects: Mapping[str, _Object],
    *,
    occurrence: tuple[str, int, int],
    duplicate_objects: frozenset[str],
) -> str | None:
    if raw is None:
        return None
    head, separator, _tail = raw.partition(".")
    local_binding = next(
        (
            item
            for item in reversed(object_bindings.get((module, head), ()))
            if item[0] <= occurrence
        ),
        None,
    )
    if local_binding is not None:
        key = local_binding[1] if not separator else None
        return (
            key
            if key is not None and key in objects and key not in duplicate_objects
            else None
        )
    if (module, head) in object_bindings:
        return None
    target = _resolved_reference_at(
        raw,
        module,
        imports,
        import_bindings,
        occurrence,
    )
    if target is None:
        return None
    key = f"{target[0]}:{target[1]}"
    return key if key in objects and key not in duplicate_objects else None


def _object_binding_timeline(
    parsed: Mapping[str, tuple[str, ast.Module, str]],
    imports: Mapping[str, Mapping[str, str]],
    import_bindings: _BindingTimeline,
    objects: Mapping[str, _Object],
    duplicate_objects: frozenset[str],
) -> _ObjectBindingTimeline:
    bindings: dict[tuple[str, str], list[tuple[_Order, str | None]]] = {}

    def remember(
        module: str,
        name: str,
        order: _Order,
        key: str | None,
    ) -> None:
        bindings.setdefault((module, name), []).append((order, key))

    def resolve(raw: str | None, module: str, occurrence: _Order) -> str | None:
        if raw is None:
            return None
        head, separator, _tail = raw.partition(".")
        local = next(
            (
                item
                for item in reversed(bindings.get((module, head), ()))
                if item[0] <= occurrence
            ),
            None,
        )
        if local is not None:
            return local[1] if not separator else None
        target = _resolved_reference_at(
            raw,
            module,
            imports,
            import_bindings,
            occurrence,
        )
        if target is None:
            return None
        key = f"{target[0]}:{target[1]}"
        return key if key in objects and key not in duplicate_objects else None

    def proven_non_framework(node: ast.AST | None) -> bool:
        return isinstance(
            node,
            (
                ast.Constant,
                ast.Dict,
                ast.DictComp,
                ast.GeneratorExp,
                ast.Lambda,
                ast.List,
                ast.ListComp,
                ast.Set,
                ast.SetComp,
                ast.Tuple,
            ),
        )

    object_at_order = {(item.module, item.order): item.key for item in objects.values()}
    for path, (_source, tree, module) in parsed.items():
        for node in tree.body:
            order = _line_order(path, node)
            object_key = object_at_order.get((module, order))
            if isinstance(node, ast.Assign):
                value = object_key or resolve(_dotted(node.value), module, order)
                if value is None and proven_non_framework(node.value):
                    value = _NON_FRAMEWORK_BINDING
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        remember(module, target.id, order, value)
                    else:
                        visitor = _ScopeBindingVisitor()
                        visitor.visit(target)
                        for name, binding_node in visitor.records:
                            remember(
                                module,
                                name,
                                _line_order(path, binding_node),
                                None,
                            )
                continue
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                value = object_key or resolve(_dotted(node.value), module, order)
                if value is None and proven_non_framework(node.value):
                    value = _NON_FRAMEWORK_BINDING
                remember(
                    module,
                    node.target.id,
                    order,
                    value,
                )
                continue
            if isinstance(node, ast.ImportFrom):
                base = _import_from_base(path, module, node)
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    name = alias.asname or alias.name
                    target = f"{base}:{alias.name}" if base is not None else None
                    remember(
                        module,
                        name,
                        order,
                        target if target in objects else None,
                    )
                continue
            if isinstance(node, ast.Import):
                continue
            visitor = _ScopeBindingVisitor()
            visitor.visit(node)
            for name, binding_node in visitor.records:
                exact_non_framework = (
                    binding_node is node
                    and isinstance(
                        node,
                        (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
                    )
                    and not node.decorator_list
                )
                remember(
                    module,
                    name,
                    _line_order(path, binding_node),
                    _NON_FRAMEWORK_BINDING if exact_non_framework else None,
                )
            if isinstance(node, ast.ClassDef):
                for name, binding_node in _class_global_binding_records(node):
                    remember(module, name, _line_order(path, binding_node), None)

    return MappingProxyType({
        key: tuple(sorted(values, key=lambda item: item[0]))
        for key, values in bindings.items()
    })


def _is_registration_call(
    node: ast.AST,
    path: str,
    module: str,
    imports: Mapping[str, Mapping[str, str]],
    import_bindings: _BindingTimeline,
    object_bindings: _ObjectBindingTimeline,
    objects: Mapping[str, _Object],
) -> bool:
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return False
    raw_owner = _dotted(node.func.value)
    if raw_owner is None:
        return False
    head = raw_owner.split(".", 1)[0]
    owner = _resolved_reference_at(
        raw_owner,
        module,
        imports,
        import_bindings,
        _line_order(path, node),
    )
    timeline = object_bindings.get((module, head), ())
    visible = next(
        (item for item in reversed(timeline) if item[0] <= _line_order(path, node)),
        None,
    )
    if visible is not None and visible[1] == _NON_FRAMEWORK_BINDING:
        return False
    return (
        visible is not None
        or bool(timeline)
        or (owner is not None and f"{owner[0]}:{owner[1]}" in objects)
    ) and (
        node.func.attr in _FRAMEWORK_REGISTRATION_METHODS
        or node.func.attr in _ROUTE_DECORATORS
    )


def _has_unmodeled_registration(
    path: str,
    tree: ast.Module,
    module: str,
    imports: Mapping[str, Mapping[str, str]],
    import_bindings: _BindingTimeline,
    object_bindings: _ObjectBindingTimeline,
    objects: Mapping[str, _Object],
    duplicate_objects: frozenset[str],
) -> bool:
    def is_modeled(node: ast.AST) -> bool:
        if not _is_registration_call(
            node,
            path,
            module,
            imports,
            import_bindings,
            object_bindings,
            objects,
        ):
            return False
        assert isinstance(node, ast.Call)
        assert isinstance(node.func, ast.Attribute)
        return (
            _object_reference(
                _dotted(node.func.value),
                module,
                imports,
                import_bindings,
                object_bindings,
                objects,
                occurrence=_line_order(path, node),
                duplicate_objects=duplicate_objects,
            )
            is not None
        )

    modeled: set[int] = set()
    for statement in tree.body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            modeled.update(
                id(decorator)
                for decorator in statement.decorator_list
                if is_modeled(decorator)
            )
        elif isinstance(statement, ast.Expr) and is_modeled(statement.value):
            modeled.add(id(statement.value))
    return any(
        id(node) not in modeled
        and _is_registration_call(
            node,
            path,
            module,
            imports,
            import_bindings,
            object_bindings,
            objects,
        )
        for node in ast.walk(tree)
    )


def _dependency_key(
    dependency: _Dependency,
    imports: Mapping[str, Mapping[str, str]],
    function_keys: Mapping[tuple[str, str], str],
) -> str | None:
    del imports
    return (
        function_keys.get(dependency.target) if dependency.target is not None else None
    )


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
        (
            f"fastapi/events/{event.event}/{event.ordinal}/"
            f"instances/{event.instance_ordinal}"
        ),
        event.instance_ordinal,
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
            (
                f"fastapi/events/{event.event}/{event.ordinal}/"
                f"instances/{event.instance_ordinal}/handler"
            ),
            event.instance_ordinal,
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
    instance_ordinal: int = 0,
) -> EntrypointCandidate | None:
    del imports
    if app.lifespan is None:
        return None
    locator = _locator(
        app.source_path,
        source,
        app.line,
        (
            f"fastapi/lifespan/{app.name}"
            if app.kind == "app"
            else f"fastapi/lifespan/{app.name}/instances/{instance_ordinal}"
        ),
        instance_ordinal,
    )
    handler = (
        function_keys.get(app.lifespan.target)
        if app.lifespan.target is not None
        else None
    )
    unresolved = (
        None
        if handler is not None
        else local_record_key(
            "python",
            app.source_path,
            "unresolved_fact",
            "ast",
            f"fastapi/lifespan/{app.name}/handler",
            instance_ordinal,
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
        f"fastapi/routes/{spec.ordinal}/instances/{route.instance_ordinal}",
        route.instance_ordinal,
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
            (
                f"fastapi/routes/{spec.ordinal}/instances/"
                f"{route.instance_ordinal}/handler"
            ),
            route.instance_ordinal,
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
    modules_by_path: Mapping[str, str],
    import_reachability: frozenset[tuple[str, str]],
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
            if cutoff is not None:
                if route.source_path == cutoff[0]:
                    if route.order > cutoff:
                        continue
                elif route.source_path != current.source_path or (
                    (cutoff_module := modules_by_path.get(cutoff[0])) is not None
                    and (current.module, cutoff_module) in import_reachability
                ):
                    diagnostics.mark(
                        route.source_path,
                        CoverageCapability.ENTRYPOINT_DISCOVERY,
                        "router_snapshot_order_unresolved",
                    )
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
                    (
                        dependencies
                        + tuple(
                            ("decorator_dependency", item)
                            for item in route.dependencies
                        )
                        if route.flavor == "fastapi"
                        else ()
                    ),
                    app_key,
                )
            )
        for include in includes_by_parent.get(object_key, ()):
            if cutoff is not None:
                if include.source_path == cutoff[0]:
                    if include.order > cutoff:
                        continue
                elif include.source_path != current.source_path or (
                    (cutoff_module := modules_by_path.get(cutoff[0])) is not None
                    and (current.module, cutoff_module) in import_reachability
                ):
                    diagnostics.mark(
                        include.source_path,
                        CoverageCapability.ENTRYPOINT_DISCOVERY,
                        "router_snapshot_order_unresolved",
                    )
                    continue
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
            walk(
                child.key,
                _join_path(current_prefix, include.prefix),
                dependencies
                + tuple(("route_dependency", item) for item in include.dependencies),
                include.order,
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
    ordered = sorted(
        resolved,
        key=lambda item: (
            item.route.order,
            item.public_path,
            item.route.handler_name,
        ),
    )
    return tuple(
        replace(item, instance_ordinal=ordinal) for ordinal, item in enumerate(ordered)
    )


def _expand_events(
    objects: Mapping[str, _Object],
    includes: Sequence[_Include],
    events: Sequence[_Event],
    uncertainties: Sequence[_EventUncertainty],
    diagnostics: _Diagnostics,
    *,
    invalid_root_apps: frozenset[str],
    modules_by_path: Mapping[str, str],
    import_reachability: frozenset[tuple[str, str]],
) -> tuple[_Event, ...]:
    includes_by_parent: dict[str, list[_Include]] = {}
    events_by_owner: dict[str, list[_Event]] = {}
    uncertainties_by_owner: dict[str, list[_EventUncertainty]] = {}
    for include in includes:
        includes_by_parent.setdefault(include.parent, []).append(include)
    for event in events:
        events_by_owner.setdefault(event.owner, []).append(event)
    for uncertainty in uncertainties:
        uncertainties_by_owner.setdefault(uncertainty.owner, []).append(uncertainty)
    for values in includes_by_parent.values():
        values.sort(key=lambda item: item.order)
    for values in events_by_owner.values():
        values.sort(key=lambda item: item.order)
    for values in uncertainties_by_owner.values():
        values.sort(key=lambda item: item.order)

    resolved: list[tuple[str, _Event]] = []

    def visible(
        source_path: str,
        order: _Order,
        current: _Object,
        cutoff: _Order | None,
    ) -> bool:
        if cutoff is None:
            return True
        if source_path == cutoff[0]:
            return order <= cutoff
        if source_path == current.source_path:
            cutoff_module = modules_by_path.get(cutoff[0])
            if (
                cutoff_module is None
                or (
                    current.module,
                    cutoff_module,
                )
                not in import_reachability
            ):
                return True
        diagnostics.mark(
            source_path,
            CoverageCapability.ENTRYPOINT_DISCOVERY,
            "router_snapshot_order_unresolved",
        )
        return False

    def emit_copied_handlers(
        object_key: str,
        cutoff: _Order | None,
        app_key: str,
        ancestry: frozenset[str],
    ) -> None:
        current = objects.get(object_key)
        if current is None or current.unresolved:
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
        for uncertainty in uncertainties_by_owner.get(object_key, ()):
            if visible(
                uncertainty.source_path,
                uncertainty.order,
                current,
                cutoff,
            ):
                diagnostics.mark(
                    uncertainty.source_path,
                    CoverageCapability.FRAMEWORK_LIFECYCLE,
                    "framework_config_unresolved",
                )
        for event in events_by_owner.get(object_key, ()):
            if visible(event.source_path, event.order, current, cutoff):
                resolved.append((app_key, event))
        for include in includes_by_parent.get(object_key, ()):
            if not visible(include.source_path, include.order, current, cutoff):
                continue
            child = objects.get(include.child or "")
            if include.unresolved or child is None or child.kind != "router":
                diagnostics.mark(
                    include.source_path,
                    CoverageCapability.ENTRYPOINT_DISCOVERY,
                    "framework_config_unresolved",
                )
                continue
            emit_copied_handlers(
                child.key,
                include.order,
                app_key,
                ancestry | {object_key},
            )

    def walk_lifespan_context(
        object_key: str,
        cutoff: _Order | None,
        app_key: str,
        ancestry: frozenset[str],
    ) -> None:
        current = objects.get(object_key)
        if current is None or current.unresolved:
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

        # FastAPI composes two independent mechanisms when a router is
        # included.  Its event handlers are copied into the parent's lists at
        # registration time, while its lifespan context is merged separately.
        # A default lifespan reads the router's mutable, final handler lists;
        # a custom lifespan suppresses those lists without suppressing nested
        # router contexts already merged into it.
        if not current.lifespan_declared:
            emit_copied_handlers(object_key, None, app_key, frozenset())

        for include in includes_by_parent.get(object_key, ()):
            if not visible(include.source_path, include.order, current, cutoff):
                continue
            child = objects.get(include.child or "")
            if include.unresolved or child is None or child.kind != "router":
                diagnostics.mark(
                    include.source_path,
                    CoverageCapability.ENTRYPOINT_DISCOVERY,
                    "framework_config_unresolved",
                )
                continue
            walk_lifespan_context(
                child.key,
                include.order,
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
        walk_lifespan_context(
            app.key,
            None,
            app.key,
            frozenset(),
        )

    ordered = sorted(
        resolved,
        key=lambda item: (item[1].order, item[0], item[1].handler_name),
    )
    return tuple(
        replace(event, instance_ordinal=ordinal)
        for ordinal, (_app_key, event) in enumerate(ordered)
    )


def _expand_lifespans(
    objects: Mapping[str, _Object],
    includes: Sequence[_Include],
    diagnostics: _Diagnostics,
    *,
    invalid_root_apps: frozenset[str],
    modules_by_path: Mapping[str, str],
    import_reachability: frozenset[tuple[str, str]],
) -> tuple[_LifespanInstance, ...]:
    includes_by_parent: dict[str, list[_Include]] = {}
    for include in includes:
        includes_by_parent.setdefault(include.parent, []).append(include)
    for values in includes_by_parent.values():
        values.sort(key=lambda item: item.order)

    resolved: list[_LifespanInstance] = []
    instance_counts: dict[str, int] = {}

    def visible(
        source_path: str,
        order: _Order,
        current: _Object,
        cutoff: _Order | None,
    ) -> bool:
        if cutoff is None:
            return True
        if source_path == cutoff[0]:
            return order <= cutoff
        if source_path == current.source_path:
            cutoff_module = modules_by_path.get(cutoff[0])
            if (
                cutoff_module is None
                or (
                    current.module,
                    cutoff_module,
                )
                not in import_reachability
            ):
                return True
        diagnostics.mark(
            source_path,
            CoverageCapability.ENTRYPOINT_DISCOVERY,
            "router_snapshot_order_unresolved",
        )
        return False

    def walk(
        object_key: str,
        cutoff: _Order | None,
        ancestry: frozenset[str],
    ) -> None:
        current = objects.get(object_key)
        if current is None or current.unresolved:
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
        if current.lifespan_declared:
            if current.lifespan is None or current.lifespan.target is None:
                diagnostics.mark(
                    current.source_path,
                    CoverageCapability.FRAMEWORK_LIFECYCLE,
                    "framework_config_unresolved",
                )
            else:
                ordinal = instance_counts.get(object_key, 0)
                instance_counts[object_key] = ordinal + 1
                resolved.append(_LifespanInstance(current, ordinal))
        for include in includes_by_parent.get(object_key, ()):
            if not visible(include.source_path, include.order, current, cutoff):
                continue
            child = objects.get(include.child or "")
            if include.unresolved or child is None or child.kind != "router":
                diagnostics.mark(
                    include.source_path,
                    CoverageCapability.ENTRYPOINT_DISCOVERY,
                    "framework_config_unresolved",
                )
                continue
            walk(child.key, include.order, ancestry | {object_key})

    for app in sorted(
        (
            item
            for item in objects.values()
            if item.kind == "app" and item.key not in invalid_root_apps
        ),
        key=lambda item: item.order,
    ):
        walk(app.key, None, frozenset())
    return tuple(resolved)


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

        import_reachability = _module_import_reachability(parsed)
        wildcard_exports = _wildcard_exports(parsed, import_reachability)
        imports = {
            module: _imports_for(
                path,
                module,
                tree,
                wildcard_exports,
                import_reachability,
            )
            for path, (_source, tree, module) in parsed.items()
        }

        fastapi_version = self.detected_version(context)
        starlette_version = self.detected_starlette_version(context)
        import_binding_view = _import_bindings(
            parsed,
            wildcard_exports,
            import_reachability,
        )
        dependency_binding_view = _dependency_bindings(
            parsed,
            imports,
            import_binding_view,
            wildcard_exports,
            import_reachability,
        )
        response_class_view = _response_class_bindings(
            parsed,
            imports,
            import_binding_view,
        )
        modules_by_path = MappingProxyType({
            path: module for path, (_source, _tree, module) in parsed.items()
        })
        module_binding_view = _module_binding_orders(parsed)
        fastapi_route_contract_proven = (
            fastapi_version in _FASTAPI_ROUTE_SIGNATURE_VERSIONS
        )
        starlette_route_contract_proven = (
            starlette_version in _STARLETTE_ROUTE_SIGNATURE_VERSIONS
        )
        exception_classes = _exception_class_identities(parsed)
        exception_declarations = _exception_aliases(
            parsed,
            exception_classes,
            wildcard_exports,
            import_reachability,
        )
        exception_mros = _exception_mros(
            parsed,
            imports,
            exception_declarations,
            exception_classes,
        )
        exception_aliases = _exception_aliases(
            parsed,
            exception_classes,
            wildcard_exports,
            import_reachability,
            exception_mros,
        )

        objects: dict[str, _Object] = {}
        duplicate_objects: set[str] = set()
        outcomes: dict[tuple[str, str], _FunctionOutcome] = {}
        function_returns: dict[tuple[str, str], ast.AST | None] = {}
        function_return_paths: dict[tuple[str, str], str] = {}
        function_dependencies: dict[
            tuple[str, str], tuple[_Dependency, ...] | None
        ] = {}
        function_nodes: list[
            tuple[str, str, str, ast.FunctionDef | ast.AsyncFunctionDef]
        ] = []
        events: list[_Event] = []
        event_uncertainties: list[_EventUncertainty] = []
        event_ordinal = 0
        for path, (source, tree, module) in parsed.items():
            for node in tree.body:
                constructor_call: ast.Call | None = None
                targets: list[str] = []
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                    constructor_call = node.value
                    targets = [
                        target.id
                        for target in node.targets
                        if isinstance(target, ast.Name)
                    ]
                elif (
                    isinstance(node, ast.AnnAssign)
                    and isinstance(node.target, ast.Name)
                    and isinstance(node.value, ast.Call)
                ):
                    constructor_call = node.value
                    targets = [node.target.id]
                if constructor_call is not None:
                    kind = _framework_constructor_kind(
                        constructor_call,
                        module,
                        imports,
                        import_binding_view,
                        module_binding_view,
                        _line_order(path, node),
                    )
                    if kind is None:
                        call_name = _call_name(constructor_call)
                        annotated_kind = (
                            _framework_annotation_kind(
                                node.annotation,
                                path,
                                module,
                                imports,
                                import_binding_view,
                            )
                            if isinstance(node, ast.AnnAssign)
                            else None
                        )
                        if annotated_kind is not None or (
                            call_name is not None
                            and call_name.rsplit(".", 1)[-1] in {"APIRouter", "FastAPI"}
                        ):
                            diagnostics.mark(
                                path,
                                CoverageCapability.ENTRYPOINT_DISCOVERY,
                                "framework_config_unresolved",
                            )
                        continue
                    if len(targets) != 1:
                        diagnostics.mark(
                            path,
                            CoverageCapability.ENTRYPOINT_DISCOVERY,
                            "framework_config_unresolved",
                        )
                        continue
                    prefix_node = _keyword(constructor_call, "prefix")
                    prefix = (
                        ""
                        if kind == "app" or prefix_node is None
                        else _literal_string(prefix_node)
                    )
                    dependencies = _dependencies(
                        _keyword(constructor_call, "dependencies"),
                        module,
                        path,
                        imports,
                        import_binding_view,
                    )
                    lifespan_node = (
                        _keyword(constructor_call, "lifespan")
                        if kind in {"app", "router"}
                        else None
                    )
                    no_custom_lifespan = (
                        isinstance(lifespan_node, ast.Constant)
                        and lifespan_node.value is None
                    )
                    lifespan_raw = (
                        _dotted(lifespan_node)
                        if lifespan_node is not None and not no_custom_lifespan
                        else None
                    )
                    lifespan_target = (
                        _resolved_reference_at(
                            lifespan_raw,
                            module,
                            imports,
                            import_binding_view,
                            _line_order(path, lifespan_node),
                        )
                        if lifespan_raw is not None and lifespan_node is not None
                        else None
                    )
                    if (
                        lifespan_target is None
                        and lifespan_raw is not None
                        and "." not in lifespan_raw
                    ):
                        declaration = next(
                            (
                                candidate
                                for candidate in tree.body
                                if isinstance(
                                    candidate,
                                    (ast.FunctionDef, ast.AsyncFunctionDef),
                                )
                                and candidate.name == lifespan_raw
                                and _line_order(path, candidate)
                                < _line_order(path, node)
                            ),
                            None,
                        )
                        visible_lifespan = _visible_binding(
                            import_binding_view,
                            module,
                            lifespan_raw,
                            _line_order(path, lifespan_node),
                        )
                        if (
                            declaration is not None
                            and visible_lifespan is not None
                            and visible_lifespan[0] == _line_order(path, declaration)
                            and len(declaration.decorator_list) == 1
                            and _resolved_reference_at(
                                _dotted(declaration.decorator_list[0]) or "",
                                module,
                                imports,
                                import_binding_view,
                                _line_order(path, declaration.decorator_list[0]),
                            )
                            == ("contextlib", "asynccontextmanager")
                        ):
                            lifespan_target = (module, lifespan_raw)
                    lifespan = (
                        _Reference(
                            lifespan_raw,
                            lifespan_target,
                        )
                        if lifespan_raw is not None and lifespan_node is not None
                        else None
                    )
                    lifespan_declared = (
                        kind in {"app", "router"}
                        and lifespan_node is not None
                        and not no_custom_lifespan
                    )
                    unresolved = prefix is None or dependencies is None
                    if (
                        kind == "app"
                        and lifespan_declared
                        and (lifespan is None or lifespan.target is None)
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
                        lifespan_declared,
                        path,
                        node.lineno,
                        _line_order(path, node),
                        unresolved,
                    )
                    if kind in {"app", "router"}:
                        for event_name, keyword_name in (
                            ("startup", "on_startup"),
                            ("shutdown", "on_shutdown"),
                        ):
                            handlers_node = _keyword(constructor_call, keyword_name)
                            if handlers_node is None:
                                continue
                            if (
                                isinstance(handlers_node, ast.Constant)
                                and handlers_node.value is None
                            ):
                                continue
                            if not isinstance(handlers_node, (ast.List, ast.Tuple)):
                                event_uncertainties.append(
                                    _EventUncertainty(
                                        key,
                                        path,
                                        _line_order(path, node),
                                    )
                                )
                                continue
                            for handler_node in handlers_node.elts:
                                handler_raw = _dotted(handler_node)
                                target = _resolved_reference_at(
                                    handler_raw or "",
                                    module,
                                    imports,
                                    import_binding_view,
                                    _line_order(path, handler_node),
                                )
                                if handler_raw is None or target is None:
                                    event_uncertainties.append(
                                        _EventUncertainty(
                                            key,
                                            path,
                                            _line_order(path, node),
                                        )
                                    )
                                    continue
                                events.append(
                                    _Event(
                                        key,
                                        event_name,
                                        target[0],
                                        target[1],
                                        path,
                                        int(
                                            getattr(handler_node, "lineno", node.lineno)
                                        ),
                                        _line_order(path, node),
                                        event_ordinal,
                                    )
                                )
                                event_ordinal += 1
                    continue
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    background_targets = _background_targets(
                        node,
                        module,
                        path,
                        imports,
                        import_binding_view,
                    )
                    if background_targets is None:
                        diagnostics.mark(
                            path,
                            CoverageCapability.ASYNC,
                            "background_task_unresolved",
                        )
                    outcome = _FunctionOutcome(
                        isinstance(node, ast.AsyncFunctionDef),
                        _has_yield(node),
                        _raised_exception_identities(
                            node,
                            path,
                            module,
                            imports,
                            exception_aliases,
                        ),
                        _has_request_validation(node),
                        background_targets or (),
                    )
                    outcomes[(module, node.name)] = outcome
                    function_returns[(module, node.name)] = node.returns
                    function_return_paths[(module, node.name)] = path
                    function_dependencies[(module, node.name)] = (
                        _parameter_dependencies(
                            node,
                            module,
                            path,
                            imports,
                            import_binding_view,
                            dependency_binding_view,
                            import_reachability,
                        )
                    )
                    function_nodes.append((path, source, module, node))

        duplicate_view = frozenset(duplicate_objects)
        object_binding_view = _object_binding_timeline(
            parsed,
            imports,
            import_binding_view,
            objects,
            duplicate_view,
        )
        rebound_object_keys = {
            item.key
            for item in objects.values()
            if object_binding_view.get((item.module, item.name), ())[-1][1] != item.key
        }
        for (module, name), timeline in object_binding_view.items():
            imported = _visible_binding(
                import_binding_view,
                module,
                name,
                timeline[0][0],
            )
            if imported is None or imported[1] is None:
                continue
            direct = imported[1].rsplit(".", 1)
            imported_key = f"{direct[0]}:{direct[1]}" if len(direct) == 2 else None
            if (
                imported_key in objects
                and any(key == imported_key for _order, key in timeline)
                and timeline[-1][1] is None
            ):
                rebound_object_keys.add(imported_key)
        for path, (_source, tree, module) in parsed.items():
            if _has_unmodeled_registration(
                path,
                tree,
                module,
                imports,
                import_binding_view,
                object_binding_view,
                objects,
                duplicate_view,
            ):
                diagnostics.mark(
                    path,
                    CoverageCapability.ENTRYPOINT_DISCOVERY,
                    "framework_config_unresolved",
                )
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
        route_ordinal = 0

        for path, source, module, function in function_nodes:
            for decorator in function.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(
                    decorator.func, ast.Attribute
                ):
                    continue
                owner = _object_reference(
                    _dotted(decorator.func.value),
                    module,
                    imports,
                    import_binding_view,
                    object_binding_view,
                    objects,
                    occurrence=_line_order(path, decorator),
                    duplicate_objects=duplicate_view,
                )
                if owner is None:
                    continue
                method = decorator.func.attr
                if method in _ROUTE_DECORATORS:
                    flavor = "starlette" if method == "route" else "fastapi"
                    path_node = (
                        decorator.args[0]
                        if decorator.args
                        else _keyword(decorator, "path")
                    )
                    public_path = _literal_string(path_node)
                    methods = _http_methods(
                        decorator,
                        method,
                        fastapi_contract_proven=fastapi_route_contract_proven,
                        starlette_contract_proven=starlette_route_contract_proven,
                    )
                    dependencies = (
                        _dependencies(
                            _keyword(decorator, "dependencies"),
                            module,
                            path,
                            imports,
                            import_binding_view,
                        )
                        if flavor == "fastapi"
                        else ()
                    )
                    parameter_dependencies = (
                        function_dependencies.get((module, function.name))
                        if flavor == "fastapi"
                        else ()
                    )
                    response_model = (
                        _response_model(
                            decorator,
                            function.returns,
                            module,
                            path,
                            imports,
                            import_binding_view,
                            response_class_view,
                        )
                        if flavor == "fastapi"
                        else False
                    )
                    unresolved = public_path is None or methods is None
                    if flavor == "fastapi":
                        unresolved = (
                            unresolved
                            or dependencies is None
                            or parameter_dependencies is None
                        )
                    if methods is None:
                        diagnostics.mark(
                            path,
                            CoverageCapability.ENTRYPOINT_DISCOVERY,
                            "route_method_contract_unresolved",
                        )
                    if flavor == "fastapi" and response_model is None:
                        diagnostics.mark(
                            path,
                            CoverageCapability.FRAMEWORK_LIFECYCLE,
                            "response_model_unresolved",
                        )
                    routes.append(
                        _Route(
                            owner,
                            flavor,
                            public_path,
                            methods,
                            (dependencies or ()) + (parameter_dependencies or ()),
                            module,
                            function.name,
                            response_model,
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
                    may_short_circuit = _middleware_may_short_circuit(function)
                    if may_short_circuit is None:
                        diagnostics.mark(
                            path,
                            CoverageCapability.FRAMEWORK_LIFECYCLE,
                            "middleware_behavior_unresolved",
                        )
                    middleware.setdefault(owner, []).append(
                        _Middleware(
                            owner,
                            function.name,
                            local,
                            True if may_short_circuit is None else may_short_circuit,
                            path,
                            decorator.lineno,
                            _line_order(path, decorator),
                        )
                    )
                elif method == "exception_handler":
                    exception_name = _exception_identity(
                        _dotted(decorator.args[0] if decorator.args else None),
                        module,
                        imports,
                        exception_aliases,
                        occurrence=_line_order(path, decorator),
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
                        event_uncertainties.append(
                            _EventUncertainty(
                                owner,
                                path,
                                _line_order(path, decorator),
                            )
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
                            _line_order(path, decorator),
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
                    import_binding_view,
                    object_binding_view,
                    objects,
                    occurrence=_line_order(path, node),
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
                        import_binding_view,
                        object_binding_view,
                        objects,
                        occurrence=_line_order(path, node),
                        duplicate_objects=duplicate_view,
                    )
                    prefix_node = _keyword(call, "prefix")
                    prefix = "" if prefix_node is None else _literal_string(prefix_node)
                    dependencies = _dependencies(
                        _keyword(call, "dependencies"),
                        module,
                        path,
                        imports,
                        import_binding_view,
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
                elif method == "add_event_handler":
                    event_node = (
                        call.args[0] if call.args else _keyword(call, "event_type")
                    )
                    event = _literal_string(event_node)
                    handler_node = (
                        call.args[1]
                        if len(call.args) > 1
                        else _keyword(call, "func") or _keyword(call, "handler")
                    )
                    handler_raw = _dotted(handler_node)
                    target = _resolved_reference_at(
                        handler_raw or "",
                        module,
                        imports,
                        import_binding_view,
                        _line_order(path, handler_node or node),
                    )
                    if event not in {"startup", "shutdown"} or target is None:
                        event_uncertainties.append(
                            _EventUncertainty(
                                owner,
                                path,
                                _line_order(path, node),
                            )
                        )
                        continue
                    events.append(
                        _Event(
                            owner,
                            event,
                            target[0],
                            target[1],
                            path,
                            node.lineno,
                            _line_order(path, node),
                            event_ordinal,
                        )
                    )
                    event_ordinal += 1
                elif method in {"add_api_route", "add_route"}:
                    flavor = "fastapi" if method == "add_api_route" else "starlette"
                    path_node = call.args[0] if call.args else _keyword(call, "path")
                    endpoint = _dotted(
                        call.args[1]
                        if len(call.args) > 1
                        else _keyword(call, "endpoint")
                    )
                    public_path = _literal_string(path_node)
                    methods = _http_methods(
                        call,
                        method,
                        fastapi_contract_proven=fastapi_route_contract_proven,
                        starlette_contract_proven=starlette_route_contract_proven,
                    )
                    registration_dependencies = (
                        _dependencies(
                            _keyword(call, "dependencies"),
                            module,
                            path,
                            imports,
                            import_binding_view,
                        )
                        if flavor == "fastapi"
                        else ()
                    )
                    target = _resolved_reference_at(
                        endpoint or "",
                        module,
                        imports,
                        import_binding_view,
                        _line_order(path, node),
                    )
                    endpoint_dependencies = (
                        function_dependencies.get(target)
                        if flavor == "fastapi"
                        and target is not None
                        and target in function_dependencies
                        else ()
                    )
                    dependencies = (
                        None
                        if registration_dependencies is None
                        or endpoint_dependencies is None
                        else registration_dependencies + endpoint_dependencies
                    )
                    response_model = (
                        _response_model(
                            call,
                            function_returns.get(target)
                            if target is not None
                            else None,
                            target[0] if target is not None else module,
                            function_return_paths.get(target, path)
                            if target is not None
                            else path,
                            imports,
                            import_binding_view,
                            response_class_view,
                        )
                        if flavor == "fastapi"
                        else False
                    )
                    unresolved = (
                        public_path is None
                        or methods is None
                        or dependencies is None
                        or target is None
                    )
                    if methods is None:
                        diagnostics.mark(
                            path,
                            CoverageCapability.ENTRYPOINT_DISCOVERY,
                            "route_method_contract_unresolved",
                        )
                    if flavor == "fastapi" and response_model is None:
                        diagnostics.mark(
                            path,
                            CoverageCapability.FRAMEWORK_LIFECYCLE,
                            "response_model_unresolved",
                        )
                    routes.append(
                        _Route(
                            owner,
                            flavor,
                            public_path,
                            methods,
                            dependencies or (),
                            target[0] if target else module,
                            target[1] if target else "unresolved_handler",
                            response_model,
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
                    exception_name = _exception_identity(
                        _dotted(call.args[0] if call.args else None),
                        module,
                        imports,
                        exception_aliases,
                        occurrence=_line_order(path, node),
                    )
                    handler_raw = _dotted(
                        call.args[1]
                        if len(call.args) > 1
                        else _keyword(call, "handler")
                    )
                    target = _resolved_reference_at(
                        handler_raw or "",
                        module,
                        imports,
                        import_binding_view,
                        _line_order(path, node),
                    )
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
            and (object_key in duplicate_view or object_key in rebound_object_keys)
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
            modules_by_path=modules_by_path,
            import_reachability=import_reachability,
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
                target = task.target
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
        expanded_events = _expand_events(
            objects,
            includes,
            events,
            event_uncertainties,
            diagnostics,
            invalid_root_apps=invalid_root_apps,
            modules_by_path=modules_by_path,
            import_reachability=import_reachability,
        )
        for item in expanded_events:
            source = parsed.get(item.source_path, ("", None, ""))[0]
            if source:
                event_candidates.append(
                    _event_candidate(context, item, function_keys, source)
                )
        expanded_lifespans = _expand_lifespans(
            objects,
            includes,
            diagnostics,
            invalid_root_apps=invalid_root_apps,
            modules_by_path=modules_by_path,
            import_reachability=import_reachability,
        )
        for instance in expanded_lifespans:
            owner = instance.owner
            source = parsed.get(owner.source_path, ("", None, ""))[0]
            if source:
                candidate = _lifespan_candidate(
                    owner,
                    function_keys,
                    imports,
                    source,
                    instance.instance_ordinal,
                )
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
            exception_mros,
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
        if resolved.route.flavor == "fastapi" and outcome.has_request_validation:
            roles.append(("request_validation", None, None, True, False))

        seen_cached: set[tuple[tuple[str, str], tuple[str, ...]]] = set()
        yielded: list[_Dependency] = []
        dependency_raises = False
        for dependency_role, dependency in resolved.dependencies:
            key = _dependency_key(dependency, snapshot.imports, snapshot.function_keys)
            target_identity = ("local", key) if key is not None else dependency.identity
            identity = (target_identity, dependency.scopes)
            if dependency.cache and identity in seen_cached:
                roles.append((
                    "dependency_cache_reuse",
                    None,
                    _dependency_name(dependency),
                    False,
                    False,
                ))
                continue
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
        if resolved.route.response_model is True:
            roles.append(("response_model_serialization", None, None, False, False))
        elif resolved.route.response_model is None:
            roles.append((
                "response_model_resolution_boundary",
                None,
                None,
                False,
                False,
            ))
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
            target = (
                snapshot.function_keys.get(task.target)
                if task.target is not None
                else None
            )
            roles.append((
                "background_task_dispatch",
                target,
                task.raw_target,
                False,
                False,
            ))
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
        exception_match, handler = self._matching_exception_handler(
            handlers,
            raised_types,
            snapshot.exception_mros,
        )
        exception_role = {
            "handler": "exception_handler",
            "unhandled": "unhandled_exception",
            "boundary": "exception_handler_resolution_boundary",
        }[exception_match]
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
                if item[0]
                in {
                    "exception_handler",
                    "exception_handler_resolution_boundary",
                    "unhandled_exception",
                }
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

            if role in {
                "exception_handler_resolution_boundary",
                "unhandled_exception",
            }:
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
        handlers: Sequence[_ExceptionHandler],
        raised_types: tuple[str | None, ...],
        exception_mros: Mapping[str, tuple[str, ...] | None],
    ) -> tuple[str, _ExceptionHandler | None]:
        """Resolve Starlette's type/MRO dispatch without suffix/order guesses."""

        if not raised_types or any(item is None for item in raised_types):
            return ("boundary", None)
        if any(handler.exception_name is None for handler in handlers):
            return ("boundary", None)
        matches: list[_ExceptionHandler | None] = []
        for raised in raised_types:
            assert raised is not None
            exact = [
                handler for handler in handlers if handler.exception_name == raised
            ]
            if exact:
                matches.append(exact[-1])
                continue
            mro = exception_mros.get(raised)
            if mro is None:
                return ("boundary", None)
            selected = next(
                (
                    candidates[-1]
                    for identity in mro[1:]
                    for candidates in (
                        [
                            handler
                            for handler in handlers
                            if handler.exception_name == identity
                        ],
                    )
                    if candidates
                ),
                None,
            )
            matches.append(selected)
        first = matches[0]
        if not all(item == first for item in matches):
            return ("boundary", None)
        return ("handler", first) if first is not None else ("unhandled", None)

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
