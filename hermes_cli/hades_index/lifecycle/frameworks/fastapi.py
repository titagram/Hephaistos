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


def _response_model(call: ast.Call, return_annotation: ast.AST | None) -> bool | None:
    """Return whether a declared response model is statically meaningful.

    An explicit ``response_model=None`` opts out even when the endpoint has a
    return annotation.  Computed model factories and computed annotations are
    retained as typed lifecycle uncertainty rather than silently omitted.
    """

    value = _keyword(call, "response_model")
    if value is None:
        return _static_type_annotation(return_annotation)
    if isinstance(value, ast.Constant):
        return False if value.value is None else _static_type_annotation(value)
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


def _raised_types(node: ast.AST) -> tuple[tuple[str | None, ast.Raise], ...]:
    """Return only explicit public exception class references.

    A bare raise or computed exception expression is retained as ``None`` so
    an exception-handler arm cannot be guessed from an unrelated registration.
    """

    values: list[tuple[str | None, ast.Raise]] = []
    for item in ast.walk(node):
        if not isinstance(item, ast.Raise):
            continue
        expression = item.exc
        if isinstance(expression, ast.Call):
            expression = expression.func
        values.append((_dotted(expression), item))
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


def _import_from_base(module: str, node: ast.ImportFrom) -> str | None:
    parent = module.split(".")[:-1]
    base = node.module or ""
    if node.level:
        levels = max(0, node.level - 1)
        if levels > len(parent):
            return None
        prefix = parent[: len(parent) - levels]
        base = ".".join(prefix + ([base] if base else []))
    return base if base and _DOTTED_RE.fullmatch(base) else None


def _imports_for(path: str, module: str, tree: ast.Module) -> Mapping[str, str]:
    bindings: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".", 1)[0]
                bindings[local] = alias.name
        elif isinstance(node, ast.ImportFrom):
            base = _import_from_base(module, node)
            if base is None:
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


def _exception_identity(
    raw: str | None,
    module: str,
    imports: Mapping[str, Mapping[str, str]],
    aliases: Mapping[
        tuple[str, str],
        tuple[tuple[tuple[str, int, int], str | None], ...],
    ],
    *,
    occurrence: tuple[str, int, int],
) -> str | None:
    if raw is None:
        return None
    head, separator, tail = raw.partition(".")
    bindings = aliases.get((module, head), ())
    visible = tuple(item for item in bindings if item[0] <= occurrence)
    if visible:
        identity = visible[-1][1]
        if identity is None:
            return None
        resolved = f"{identity}.{tail}" if separator else identity
        return resolved if _DOTTED_RE.fullmatch(resolved) else None
    if raw in {"BaseException", "Exception"}:
        return f"builtins.{raw}"
    resolved = _resolved_reference(raw, module, imports)
    return ".".join(resolved) if resolved is not None else None


def _exception_aliases(
    parsed: Mapping[str, tuple[str, ast.Module, str]],
) -> Mapping[
    tuple[str, str],
    tuple[tuple[tuple[str, int, int], str | None], ...],
]:
    """Resolve source-visible exception bindings at each use occurrence."""

    aliases: dict[
        tuple[str, str],
        list[tuple[tuple[str, int, int], str | None]],
    ] = {}

    def resolve(
        raw: str | None,
        module: str,
        occurrence: tuple[str, int, int],
    ) -> str | None:
        if raw is None:
            return None
        head, separator, tail = raw.partition(".")
        visible = tuple(
            item for item in aliases.get((module, head), ()) if item[0] <= occurrence
        )
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
        order: tuple[str, int, int],
        identity: str | None,
    ) -> None:
        aliases.setdefault((module, name), []).append((order, identity))

    class _DynamicBindingVisitor(ast.NodeVisitor):
        def __init__(self, path: str) -> None:
            self.path = path
            self.found: list[tuple[str, tuple[str, int, int]]] = []

        def _record(self, name: str | None, node: ast.AST) -> None:
            if name:
                self.found.append((name, _line_order(self.path, node)))

        def visit_Name(self, node: ast.Name) -> None:
            if isinstance(node.ctx, (ast.Store, ast.Del)):
                self._record(node.id, node)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._record(node.name, node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._record(node.name, node)

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self._record(node.name, node)

        def visit_Lambda(self, node: ast.Lambda) -> None:
            return None

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

        def _visit_comprehensions(
            self, generators: Sequence[ast.comprehension]
        ) -> None:
            for generator in generators:
                self.visit(generator.iter)
                for condition in generator.ifs:
                    self.visit(condition)

        def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
            self._record(node.name, node)
            for statement in node.body:
                self.visit(statement)

        def visit_Import(self, node: ast.Import) -> None:
            for alias in node.names:
                self._record(alias.asname or alias.name.split(".", 1)[0], node)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            for alias in node.names:
                if alias.name != "*":
                    self._record(alias.asname or alias.name, node)

    for path, (_source, tree, module) in parsed.items():
        for node in tree.body:
            order = _line_order(path, node)
            if isinstance(node, ast.ClassDef):
                remember(module, node.name, order, f"{module}.{node.name}")
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
                base = _import_from_base(module, node)
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    name = alias.asname or alias.name
                    identity = f"{base}.{alias.name}" if base is not None else None
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
            visitor = _DynamicBindingVisitor(path)
            visitor.visit(node)
            for name, binding_order in visitor.found:
                remember(module, name, binding_order, None)
    return MappingProxyType({
        key: tuple(sorted(values, key=lambda item: item[0]))
        for key, values in aliases.items()
    })


def _exception_mros(
    parsed: Mapping[str, tuple[str, ast.Module, str]],
    imports: Mapping[str, Mapping[str, str]],
    aliases: Mapping[
        tuple[str, str],
        tuple[tuple[tuple[str, int, int], str | None], ...],
    ],
) -> Mapping[str, tuple[str, ...] | None]:
    """Build only fully proven Python MROs for source-visible classes."""

    bases_by_class: dict[str, tuple[str, ...] | None] = {}
    for path, (_source, tree, module) in parsed.items():
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            identity = f"{module}.{node.name}"
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


def _is_registration_call(
    node: ast.AST,
    module: str,
    imports: Mapping[str, Mapping[str, str]],
    objects: Mapping[str, _Object],
) -> bool:
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return False
    owner = _resolved_reference(_dotted(node.func.value) or "", module, imports)
    return (
        owner is not None
        and f"{owner[0]}:{owner[1]}" in objects
        and (
            node.func.attr in _FRAMEWORK_REGISTRATION_METHODS
            or node.func.attr in _ROUTE_DECORATORS
        )
    )


def _has_unmodeled_registration(
    path: str,
    tree: ast.Module,
    module: str,
    imports: Mapping[str, Mapping[str, str]],
    objects: Mapping[str, _Object],
    rebound_names: Mapping[tuple[str, str], tuple[tuple[str, int, int], ...]],
    duplicate_objects: frozenset[str],
) -> bool:
    def is_modeled(node: ast.AST) -> bool:
        if not _is_registration_call(node, module, imports, objects):
            return False
        assert isinstance(node, ast.Call)
        assert isinstance(node.func, ast.Attribute)
        return (
            _object_reference(
                _dotted(node.func.value),
                module,
                imports,
                objects,
                occurrence=_line_order(path, node),
                rebound_names=rebound_names,
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
        and _is_registration_call(node, module, imports, objects)
        for node in ast.walk(tree)
    )


def _module_scope_rebound_orders(
    path: str,
    tree: ast.Module,
    object_orders: Mapping[str, tuple[str, int, int]],
) -> tuple[tuple[str, tuple[str, int, int]], ...]:
    found: list[tuple[str, tuple[str, int, int]]] = []

    class _Visitor(ast.NodeVisitor):
        def _record(self, name: str | None, node: ast.AST) -> None:
            order = _line_order(path, node)
            if name in object_orders and order > object_orders[name]:
                found.append((name, order))

        def visit_Name(self, node: ast.Name) -> None:
            if isinstance(node.ctx, (ast.Store, ast.Del)):
                self._record(node.id, node)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._record(node.name, node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._record(node.name, node)

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self._record(node.name, node)

        def visit_Lambda(self, node: ast.Lambda) -> None:
            return None

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

        def _visit_comprehensions(
            self, generators: Sequence[ast.comprehension]
        ) -> None:
            for generator in generators:
                self.visit(generator.iter)
                for condition in generator.ifs:
                    self.visit(condition)

        def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
            self._record(node.name, node)
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
                self._record(alias.asname or alias.name, node)

    visitor = _Visitor()
    for statement in tree.body:
        if (
            isinstance(statement, ast.Assign)
            and isinstance(statement.value, ast.Call)
            and _call_name(statement.value)
            and _call_name(statement.value).rsplit(".", 1)[-1]
            in {"APIRouter", "FastAPI"}
        ):
            continue
        visitor.visit(statement)
    return tuple(found)


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

        fastapi_version = self.detected_version(context)
        starlette_version = self.detected_starlette_version(context)
        fastapi_route_contract_proven = (
            fastapi_version in _FASTAPI_ROUTE_SIGNATURE_VERSIONS
        )
        starlette_route_contract_proven = (
            starlette_version in _STARLETTE_ROUTE_SIGNATURE_VERSIONS
        )
        exception_aliases = _exception_aliases(parsed)
        exception_mros = _exception_mros(parsed, imports, exception_aliases)

        objects: dict[str, _Object] = {}
        duplicate_objects: set[str] = set()
        rebound_names: dict[tuple[str, str], list[tuple[str, int, int]]] = {}
        outcomes: dict[tuple[str, str], _FunctionOutcome] = {}
        function_returns: dict[tuple[str, str], ast.AST | None] = {}
        function_dependencies: dict[
            tuple[str, str], tuple[_Dependency, ...] | None
        ] = {}
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
                    prefix = (
                        ""
                        if kind == "app" or prefix_node is None
                        else _literal_string(prefix_node)
                    )
                    dependencies = _dependencies(
                        _keyword(node.value, "dependencies"), module, path
                    )
                    lifespan_node = (
                        _keyword(node.value, "lifespan") if kind == "app" else None
                    )
                    no_custom_lifespan = (
                        isinstance(lifespan_node, ast.Constant)
                        and lifespan_node.value is None
                    )
                    lifespan = (
                        _dotted(lifespan_node)
                        if lifespan_node is not None and not no_custom_lifespan
                        else None
                    )
                    lifespan_declared = (
                        kind == "app"
                        and lifespan_node is not None
                        and not no_custom_lifespan
                    )
                    unresolved = prefix is None or dependencies is None
                    if kind == "app" and lifespan_declared and lifespan is None:
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
                    continue
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
                        tuple(
                            _exception_identity(
                                raw,
                                module,
                                imports,
                                exception_aliases,
                                occurrence=_line_order(path, raise_node),
                            )
                            for raw, raise_node in _raised_types(node)
                        ),
                        _has_request_validation(node),
                        background_targets or (),
                    )
                    outcomes[(module, node.name)] = outcome
                    function_returns[(module, node.name)] = node.returns
                    function_dependencies[(module, node.name)] = (
                        _parameter_dependencies(node, module, path)
                    )
                    function_nodes.append((path, source, module, node))

        object_orders_by_module: dict[str, dict[str, tuple[str, int, int]]] = {}
        for item in objects.values():
            object_orders_by_module.setdefault(item.module, {})[item.name] = item.order
        for path, (_source, tree, module) in parsed.items():
            orders = object_orders_by_module.setdefault(module, {})
            for node in tree.body:
                if isinstance(node, ast.ImportFrom):
                    local_names = tuple(
                        alias.asname or alias.name
                        for alias in node.names
                        if alias.name != "*"
                    )
                else:
                    local_names = ()
                for name in local_names:
                    target = _resolved_reference(name, module, imports)
                    if target is not None and f"{target[0]}:{target[1]}" in objects:
                        orders.setdefault(name, _line_order(path, node))
        for path, (_source, tree, module) in parsed.items():
            for name, order in _module_scope_rebound_orders(
                path,
                tree,
                object_orders_by_module.get(module, {}),
            ):
                rebound_names.setdefault((module, name), []).append(order)
        rebound_view = MappingProxyType({
            key: tuple(sorted(value)) for key, value in rebound_names.items()
        })
        duplicate_view = frozenset(duplicate_objects)
        rebound_object_keys: set[str] = set()
        for (module, name), orders in rebound_view.items():
            target = _resolved_reference(name, module, imports)
            object_key = f"{target[0]}:{target[1]}" if target is not None else None
            binding_order = object_orders_by_module.get(module, {}).get(name)
            if (
                object_key in objects
                and binding_order is not None
                and any(order > binding_order for order in orders)
            ):
                assert object_key is not None
                rebound_object_keys.add(object_key)
        for path, (_source, tree, module) in parsed.items():
            if _has_unmodeled_registration(
                path,
                tree,
                module,
                imports,
                objects,
                rebound_view,
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
        events: list[_Event] = []
        route_ordinal = 0
        event_ordinal = 0

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
                    objects,
                    occurrence=_line_order(path, decorator),
                    rebound_names=rebound_view,
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
                        _dependencies(_keyword(decorator, "dependencies"), module, path)
                        if flavor == "fastapi"
                        else ()
                    )
                    parameter_dependencies = (
                        function_dependencies.get((module, function.name))
                        if flavor == "fastapi"
                        else ()
                    )
                    response_model = (
                        _response_model(decorator, function.returns)
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
                        _dependencies(_keyword(call, "dependencies"), module, path)
                        if flavor == "fastapi"
                        else ()
                    )
                    target = _resolved_reference(endpoint or "", module, imports)
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

        shadowed_event_owners = {
            item.key
            for item in objects.values()
            if item.kind == "app" and item.lifespan_declared
        }
        pending_shadowed = list(shadowed_event_owners)
        includes_by_parent: dict[str, list[_Include]] = {}
        for include in includes:
            if not include.unresolved and include.child is not None:
                includes_by_parent.setdefault(include.parent, []).append(include)
        while pending_shadowed:
            parent = pending_shadowed.pop()
            for include in includes_by_parent.get(parent, ()):
                child = include.child
                if child is not None and child not in shadowed_event_owners:
                    shadowed_event_owners.add(child)
                    pending_shadowed.append(child)

        event_candidates: list[EntrypointCandidate] = []
        for item in events:
            if item.owner in invalid_root_apps or item.owner in shadowed_event_owners:
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
