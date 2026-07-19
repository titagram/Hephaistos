"""Static, privacy-safe Django entrypoint and request-lifecycle extraction.

The adapter is intentionally a parser of files already exposed by
``ExtractionContext``.  It never imports a Django project, evaluates settings,
or follows a dynamic URL expression.  A fact is emitted only when the relevant
setting, URL declaration, and target declaration are all statically visible;
otherwise the omission remains explicit through partial coverage or a typed
pipeline boundary.
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
from hermes_cli.hades_index.lifecycle.frameworks import (
    FrameworkDetection,
    FrameworkPipelineFacts,
    FrameworkTerminalSpec,
    framework_pipeline_facts,
)
from hermes_cli.hades_index.lifecycle.model import (
    AlwaysSuccessor,
    ConfigLocatorIR,
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
    TerminalKind,
    local_record_key,
)
from hermes_cli.hades_index.tree_sitter_adapter import SyntaxIR, declaration_local_key


_PYPROJECT_FILES = ("pyproject.toml", "requirements.txt", "requirements/base.txt")
_URL_CALLS = frozenset({"path", "re_path"})
_DYNAMIC_REASON = "framework_config_unresolved"
_DOTTED_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
_DJANGO_ACCESS_DECORATORS = frozenset({
    "django.contrib.auth.decorators.login_required",
    "django.contrib.auth.decorators.permission_required",
    "django.contrib.auth.decorators.user_passes_test",
})
_BUILTIN_EXCEPTION_BASES: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "builtins.BaseException": (),
    "builtins.BaseExceptionGroup": ("builtins.BaseException",),
    "builtins.Exception": ("builtins.BaseException",),
    "builtins.ExceptionGroup": (
        "builtins.BaseExceptionGroup",
        "builtins.Exception",
    ),
    "builtins.ArithmeticError": ("builtins.Exception",),
    "builtins.AssertionError": ("builtins.Exception",),
    "builtins.AttributeError": ("builtins.Exception",),
    "builtins.BufferError": ("builtins.Exception",),
    "builtins.EOFError": ("builtins.Exception",),
    "builtins.ImportError": ("builtins.Exception",),
    "builtins.LookupError": ("builtins.Exception",),
    "builtins.MemoryError": ("builtins.Exception",),
    "builtins.NameError": ("builtins.Exception",),
    "builtins.OSError": ("builtins.Exception",),
    "builtins.ReferenceError": ("builtins.Exception",),
    "builtins.RuntimeError": ("builtins.Exception",),
    "builtins.StopAsyncIteration": ("builtins.Exception",),
    "builtins.StopIteration": ("builtins.Exception",),
    "builtins.SyntaxError": ("builtins.Exception",),
    "builtins.SystemError": ("builtins.Exception",),
    "builtins.TypeError": ("builtins.Exception",),
    "builtins.ValueError": ("builtins.Exception",),
    "builtins.Warning": ("builtins.Exception",),
    "builtins.UnicodeError": ("builtins.ValueError",),
    "builtins.UnboundLocalError": ("builtins.NameError",),
})
_MAX_INHERITANCE_DEPTH = 32


@dataclass(frozen=True, slots=True)
class _RouteContext:
    prefix: str = ""
    namespaces: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _RouteSpec:
    path: str
    name: str | None
    handler: "_FunctionTarget | None"
    cbv: "_ClassTarget | None"
    source_path: str
    source_line: int
    pointer: str
    source_order: int


@dataclass(frozen=True, slots=True)
class _ViewOutcome:
    is_async: bool
    decorator_denial: bool
    exception_arms: tuple[str, ...]
    cbv_methods: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class _MiddlewareFact:
    dotted_name: str
    has_response: bool
    may_short_circuit: bool


@dataclass(frozen=True, slots=True)
class _FunctionTarget:
    module: str
    name: str


@dataclass(frozen=True, slots=True)
class _ClassTarget:
    module: str
    name: str


@dataclass(frozen=True, slots=True)
class _ImportBinding:
    target: str


@dataclass(frozen=True, slots=True)
class _ImportBindings:
    values: Mapping[str, _ImportBinding]
    invalidated: frozenset[str]


@dataclass(frozen=True, slots=True)
class _SyntaxIndex:
    functions: Mapping[tuple[str, str], str]
    classes: Mapping[tuple[str, str], str]
    methods: Mapping[tuple[str, str, str], str]


@dataclass(frozen=True, slots=True)
class _InheritanceIndex:
    bases: Mapping[str, tuple[str, ...]]
    incomplete: frozenset[str]


@dataclass(frozen=True, slots=True)
class _Snapshot:
    routes: Mapping[tuple[str, str, int], _RouteSpec]
    outcomes: Mapping[str, _ViewOutcome]
    middleware: tuple[_MiddlewareFact, ...] | None
    coverage_events: tuple[CoverageEvent, ...]


@dataclass(slots=True)
class _Diagnostics:
    records: set[tuple[str | None, CoverageCapability, str]]

    def mark(
        self,
        path: str | None,
        capability: CoverageCapability = CoverageCapability.ENTRYPOINT_DISCOVERY,
        reason_code: str = _DYNAMIC_REASON,
    ) -> None:
        if path is None:
            self.records.add((None, capability, reason_code))
            return
        safe = _safe_path(path)
        if safe is not None:
            self.records.add((safe, capability, reason_code))

    def events(self) -> tuple[CoverageEvent, ...]:
        return tuple(
            CoverageEvent(
                "python",
                capability,
                CoverageOutcome.PARTIAL,
                reason_code,
                path,
                0,
                1,
            )
            for path, capability, reason_code in sorted(
                self.records,
                key=lambda item: (item[0] or "", item[1].value, item[2]),
            )
        )


def _safe_path(path: str) -> str | None:
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
    content = _text(context, path)
    if content is None:
        return None
    try:
        return content, ast.parse(content, filename=path)
    except SyntaxError:
        return None


def _digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _location(path: str, content: str, line: int) -> SourceLocationIR:
    safe_line = max(1, line)
    return SourceLocationIR(path, safe_line, safe_line, _digest(content))


def _locator(
    path: str, content: str, line: int, pointer: str, ordinal: int
) -> ConfigLocatorIR:
    return ConfigLocatorIR(_location(path, content, line), pointer, ordinal)


def _dotted(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted(node.value)
        return f"{parent}.{node.attr}" if parent else None
    return None


def _literal_str(node: ast.AST | None) -> str | None:
    return (
        node.value
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        else None
    )


def _literal_strings(node: ast.AST | None) -> tuple[str, ...] | None:
    if not isinstance(node, (ast.List, ast.Tuple)):
        return None
    values = tuple(_literal_str(item) for item in node.elts)
    return values if all(value is not None for value in values) else None


def _keyword(call: ast.Call, name: str) -> ast.AST | None:
    return next((item.value for item in call.keywords if item.arg == name), None)


def _module_path(module: str) -> str | None:
    if not _DOTTED_RE.fullmatch(module):
        return None
    return f"{module.replace('.', '/')}.py"


def _module_name(path: str) -> str | None:
    """Map a safe Python source path to its static import module name."""

    safe = _safe_path(path)
    if safe is None or not safe.endswith(".py"):
        return None
    parts = safe[:-3].split("/")
    if parts[-1] == "__init__":
        parts.pop()
    module = ".".join(parts)
    return module if module and _DOTTED_RE.fullmatch(module) else None


def _relative_import_module(path: str, module: str | None, level: int) -> str | None:
    if level == 0:
        return module if module is not None and _DOTTED_RE.fullmatch(module) else None
    current = _module_name(path)
    if current is None:
        return None
    parent = current.split(".")[:-1]
    if level > len(parent) + 1:
        return None
    prefix = parent[: len(parent) - max(0, level - 1)]
    parts = prefix + (module.split(".") if module else [])
    candidate = ".".join(parts)
    return candidate if candidate and _DOTTED_RE.fullmatch(candidate) else None


def _source_start(node: ast.AST) -> tuple[int, int]:
    return getattr(node, "lineno", 0), getattr(node, "col_offset", 0)


def _source_end(node: ast.AST) -> tuple[int, int]:
    return (
        getattr(node, "end_lineno", getattr(node, "lineno", 0)),
        getattr(node, "end_col_offset", getattr(node, "col_offset", 0)),
    )


def _assignment_roots(target: ast.AST) -> frozenset[str]:
    """Return aliases affected by direct, destructuring, or attribute mutation."""

    if isinstance(target, ast.Name):
        return frozenset({target.id})
    if isinstance(target, ast.Starred):
        return _assignment_roots(target.value)
    if isinstance(target, (ast.Tuple, ast.List)):
        return frozenset().union(*(_assignment_roots(item) for item in target.elts))
    if isinstance(target, (ast.Attribute, ast.Subscript)):
        return _assignment_roots(target.value)
    return frozenset()


def _direct_rebound_names(statement: ast.stmt) -> frozenset[str]:
    """Collect names rebound by one statement without entering child bodies."""

    targets: tuple[ast.AST, ...] = ()
    if isinstance(statement, ast.Import):
        return frozenset(
            alias.asname or alias.name.split(".", 1)[0]
            for alias in statement.names
            if _DOTTED_RE.fullmatch(alias.name)
        )
    if isinstance(statement, ast.ImportFrom):
        return frozenset(
            alias.asname or alias.name
            for alias in statement.names
            if alias.name != "*" and _DOTTED_RE.fullmatch(alias.name)
        )
    if isinstance(statement, ast.Assign):
        targets = tuple(statement.targets)
    elif isinstance(statement, ast.AnnAssign):
        targets = (statement.target,)
    elif isinstance(statement, ast.AugAssign):
        targets = (statement.target,)
    elif isinstance(statement, ast.Delete):
        targets = tuple(statement.targets)
    elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return frozenset({statement.name})
    elif isinstance(statement, (ast.For, ast.AsyncFor)):
        targets = (statement.target,)
    elif isinstance(statement, (ast.With, ast.AsyncWith)):
        targets = tuple(
            item.optional_vars
            for item in statement.items
            if item.optional_vars is not None
        )
    elif (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Call)
        and _dotted(statement.value.func) in {"setattr", "delattr"}
        and statement.value.args
    ):
        targets = (statement.value.args[0],)
    return frozenset().union(*(_assignment_roots(target) for target in targets))


def _rebound_names(statement: ast.stmt) -> frozenset[str]:
    """Collect names possibly rebound by a visible executable statement."""

    names = set(_direct_rebound_names(statement))

    def visit(statements: Sequence[ast.stmt]) -> None:
        for child in statements:
            names.update(_rebound_names(child))

    if isinstance(statement, ast.If):
        visit(statement.body)
        visit(statement.orelse)
    elif isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
        visit(statement.body)
        visit(statement.orelse)
    elif isinstance(statement, (ast.With, ast.AsyncWith)):
        visit(statement.body)
    elif isinstance(statement, ast.Try):
        visit(statement.body)
        visit(statement.orelse)
        visit(statement.finalbody)
        for handler in statement.handlers:
            if handler.name is not None:
                names.add(handler.name)
            visit(handler.body)
    elif isinstance(statement, ast.Match):
        for case in statement.cases:
            visit(case.body)
    return frozenset(names)


def _import_bindings(
    path: str, tree: ast.Module, before: ast.AST | None = None
) -> _ImportBindings:
    """Resolve imports that remain valid at one visible source-order use site."""

    values: dict[str, _ImportBinding] = {}
    invalidated: set[str] = set()
    for statement in tree.body:
        if before is not None and _source_end(statement) > _source_start(before):
            continue
        if isinstance(statement, ast.Import):
            for alias in statement.names:
                if not _DOTTED_RE.fullmatch(alias.name):
                    continue
                local = alias.asname or alias.name.split(".", 1)[0]
                target = alias.name if alias.asname else local
                values[local] = _ImportBinding(target)
                invalidated.discard(local)
        elif isinstance(statement, ast.ImportFrom):
            base = _relative_import_module(path, statement.module, statement.level)
            if base is None:
                continue
            for alias in statement.names:
                if alias.name == "*" or not _DOTTED_RE.fullmatch(alias.name):
                    continue
                local = alias.asname or alias.name
                values[local] = _ImportBinding(f"{base}.{alias.name}")
                invalidated.discard(local)
        else:
            for name in _rebound_names(statement):
                if name in values:
                    values.pop(name)
                    invalidated.add(name)
    return _ImportBindings(MappingProxyType(values), frozenset(invalidated))


def _resolve_reference(raw: str, path: str, bindings: _ImportBindings) -> str | None:
    """Resolve a URL expression through a concrete import, never a suffix."""

    if not _DOTTED_RE.fullmatch(raw):
        return None
    head, *tail = raw.split(".")
    binding = bindings.values.get(head)
    if binding is not None:
        return ".".join((binding.target, *tail))
    if head in bindings.invalidated:
        return None
    # A locally declared view is valid only if later exact SyntaxIR binding can
    # prove it.  This is not a fallback to another module.
    current = _module_name(path)
    return f"{current}.{raw}" if current is not None else None


def _join(prefix: str, value: str) -> str:
    left, right = prefix.strip(), value.strip()
    if not left and not right:
        return "/"
    joined = (
        f"{left.rstrip('/')}/{right.lstrip('/')}" if left and right else left or right
    )
    joined = re.sub(r"/{2,}", "/", joined)
    return joined if joined.startswith("/") else f"/{joined}"


def _settings_paths(
    context: ExtractionContext, syntax: Sequence[SyntaxIR]
) -> tuple[str, ...]:
    paths = {locator.source_location.path for locator in context.python_metadata}
    paths.update(item.path for item in syntax if item.path.endswith("settings.py"))
    paths.add("settings.py")
    return tuple(sorted(path for path in paths if _safe_path(path) is not None))


def _visible_settings_path(
    context: ExtractionContext, syntax: Sequence[SyntaxIR]
) -> str | None:
    return next(
        (
            path
            for path in _settings_paths(context, syntax)
            if _text(context, path) is not None
        ),
        None,
    )


def _setting(
    context: ExtractionContext,
    syntax: Sequence[SyntaxIR],
    name: str,
) -> tuple[str, ast.AST, str, ast.Module] | None:
    """Find one statically assigned setting, rejecting duplicate/dynamic values."""

    found: list[tuple[str, ast.AST, str, ast.Module]] = []
    for path in _settings_paths(context, syntax):
        parsed = _tree(context, path)
        if parsed is None:
            continue
        content, tree = parsed
        for statement in tree.body:
            if isinstance(statement, ast.Assign):
                targets = statement.targets
            elif isinstance(statement, ast.AnnAssign):
                targets = (statement.target,)
            else:
                continue
            if any(
                isinstance(target, ast.Name) and target.id == name for target in targets
            ):
                found.append((path, statement.value, content, tree))
    return found[0] if len(found) == 1 else None


def _root_urlconf(
    context: ExtractionContext,
    syntax: Sequence[SyntaxIR],
    diagnostics: _Diagnostics,
) -> str | None:
    setting = _setting(context, syntax, "ROOT_URLCONF")
    if setting is None:
        # Attribute omissions are scoped to a visible settings file when possible.
        settings_path = _visible_settings_path(context, syntax)
        diagnostics.mark(settings_path, reason_code="root_urlconf_unresolved")
        return None
    path, value, _content, _tree_value = setting
    root = _literal_str(value)
    module_path = _module_path(root) if root is not None else None
    if module_path is None:
        diagnostics.mark(path, reason_code="root_urlconf_unresolved")
    return module_path


def _has_direct_non_none_return(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    """Find returns in this callable's lexical body, never a nested callable."""

    def visit(node: ast.AST) -> bool:
        if isinstance(node, ast.Return):
            return not (
                isinstance(node.value, ast.Constant) and node.value.value is None
            )
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
        ):
            return False
        return any(visit(child) for child in ast.iter_child_nodes(node))

    return any(visit(statement) for statement in function.body)


def _middleware(
    context: ExtractionContext,
    syntax: Sequence[SyntaxIR],
    diagnostics: _Diagnostics,
) -> tuple[_MiddlewareFact, ...] | None:
    setting = _setting(context, syntax, "MIDDLEWARE")
    if setting is None:
        settings_path = _visible_settings_path(context, syntax)
        diagnostics.mark(
            settings_path,
            CoverageCapability.FRAMEWORK_LIFECYCLE,
            "middleware_unresolved",
        )
        return None
    path, value, _content, _tree_value = setting
    names = _literal_strings(value)
    if names is None or any(not _DOTTED_RE.fullmatch(item) for item in names):
        diagnostics.mark(
            path, CoverageCapability.FRAMEWORK_LIFECYCLE, "middleware_unresolved"
        )
        return None
    facts: list[_MiddlewareFact] = []
    for dotted in names:
        class_name = dotted.rsplit(".", 1)[-1]
        module_path = _module_path(dotted.rsplit(".", 1)[0])
        parsed = _tree(context, module_path) if module_path else None
        has_response = False
        may_short_circuit = False
        if parsed is not None:
            _source, tree = parsed
            classes = [
                node
                for node in tree.body
                if isinstance(node, ast.ClassDef) and node.name == class_name
            ]
            if len(classes) == 1:
                methods = {
                    method.name
                    for method in classes[0].body
                    if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
                }
                has_response = "process_response" in methods or "__call__" in methods
                request_method = next(
                    (
                        method
                        for method in classes[0].body
                        if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and method.name == "process_request"
                    ),
                    None,
                )
                may_short_circuit = (
                    request_method is not None
                    and _has_direct_non_none_return(request_method)
                )
            else:
                diagnostics.mark(
                    module_path,
                    CoverageCapability.FRAMEWORK_LIFECYCLE,
                    "middleware_target_unresolved",
                )
        else:
            diagnostics.mark(
                module_path or path,
                CoverageCapability.FRAMEWORK_LIFECYCLE,
                "middleware_target_unresolved",
            )
        # A registered Django middleware is entered even when only a modern
        # ``__call__`` wrapper is proven.  The individual hooks remain optional.
        facts.append(_MiddlewareFact(dotted, has_response, may_short_circuit))
    return tuple(facts)


def _literal_urlpatterns(value: ast.AST | None) -> tuple[ast.AST, ...] | None:
    """Accept a sequence only when its membership is statically visible."""

    if not isinstance(value, (ast.List, ast.Tuple)):
        return None
    if any(isinstance(item, ast.Starred) for item in value.elts):
        return None
    return tuple(value.elts)


def _targets_urlpatterns(targets: Sequence[ast.AST]) -> bool:
    return any("urlpatterns" in _assignment_roots(target) for target in targets)


def _is_urlpattern_declaration(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and _dotted(node.func) in _URL_CALLS


def _urlpatterns(tree: ast.Module) -> tuple[ast.AST, ...] | None:
    """Evaluate a bounded, source-ordered subset of Python assignment semantics."""

    values: tuple[ast.AST, ...] = ()
    found = False
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            if not _targets_urlpatterns(statement.targets):
                continue
            if not any(
                isinstance(target, ast.Name) and target.id == "urlpatterns"
                for target in statement.targets
            ):
                return None
            replacement = _literal_urlpatterns(statement.value)
            if replacement is None:
                return None
            values = replacement
            found = True
            continue
        if isinstance(statement, ast.AnnAssign):
            if "urlpatterns" not in _assignment_roots(statement.target):
                continue
            if not (
                isinstance(statement.target, ast.Name)
                and statement.target.id == "urlpatterns"
            ):
                return None
            replacement = _literal_urlpatterns(statement.value)
            if replacement is None:
                return None
            values = replacement
            found = True
            continue
        if isinstance(statement, ast.AugAssign):
            if "urlpatterns" not in _assignment_roots(statement.target):
                continue
            if not (
                isinstance(statement.target, ast.Name)
                and statement.target.id == "urlpatterns"
                and isinstance(statement.op, ast.Add)
                and found
            ):
                return None
            extension = _literal_urlpatterns(statement.value)
            if extension is None:
                return None
            values += extension
            continue
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
            call = statement.value
            if not (
                isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "urlpatterns"
            ):
                continue
            if not found or call.keywords:
                return None
            if call.func.attr == "extend" and len(call.args) == 1:
                extension = _literal_urlpatterns(call.args[0])
                if extension is not None and all(
                    _is_urlpattern_declaration(item) for item in extension
                ):
                    values += extension
                    continue
            if (
                call.func.attr == "append"
                and len(call.args) == 1
                and _is_urlpattern_declaration(call.args[0])
            ):
                values += (call.args[0],)
                continue
            return None
        if "urlpatterns" in _rebound_names(statement):
            return None
    return values if found else ()


def _include_target(call: ast.Call) -> tuple[str, str | None] | None:
    if not call.args:
        return None
    target = call.args[0]
    module: str | None = None
    app_name: str | None = None
    if isinstance(target, ast.Tuple) and len(target.elts) == 2:
        module, app_name = (_literal_str(target.elts[0]), _literal_str(target.elts[1]))
    else:
        module = _literal_str(target)
    namespace = _literal_str(_keyword(call, "namespace"))
    if module is None or _module_path(module) is None:
        return None
    return module, namespace or app_name


def _route_target(node: ast.AST) -> tuple[str, str] | None:
    """Return (kind, raw reference) only for an unambiguous static expression."""

    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "as_view"
    ):
        cbv = _dotted(node.func.value)
        return ("cbv", cbv) if cbv else None
    handler = _dotted(node)
    return ("function", handler) if handler else None


def _decorator_name(node: ast.AST) -> str | None:
    return _dotted(node.func) if isinstance(node, ast.Call) else _dotted(node)


def _collect_routes(
    context: ExtractionContext,
    path: str,
    route_context: _RouteContext,
    diagnostics: _Diagnostics,
    seen_modules: set[str],
    order: list[int],
) -> tuple[_RouteSpec, ...]:
    """Resolve one static URLconf recursively, preserving list declaration order."""

    if path in seen_modules:
        diagnostics.mark(path, reason_code="urlconf_cycle")
        return ()
    parsed = _tree(context, path)
    if parsed is None:
        diagnostics.mark(path, reason_code="urlconf_unresolved")
        return ()
    content, tree = parsed
    entries = _urlpatterns(tree)
    if entries is None:
        diagnostics.mark(path, reason_code="urlpatterns_unresolved")
        return ()
    result: list[_RouteSpec] = []
    current_seen = seen_modules | {path}
    for item in entries:
        constructor = _dotted(item.func) if isinstance(item, ast.Call) else None
        if (
            not isinstance(item, ast.Call)
            or constructor not in _URL_CALLS
            or len(item.args) < 2
        ):
            diagnostics.mark(path, reason_code="url_pattern_unresolved")
            continue
        pattern = _literal_str(item.args[0])
        if pattern is None:
            diagnostics.mark(path, reason_code="url_pattern_unresolved")
            continue
        if constructor == "path":
            pattern = re.sub(r"<(?:[^:>]+:)?([^>]+)>", r"{\1}", pattern)
        target = item.args[1]
        target_name = _dotted(target.func) if isinstance(target, ast.Call) else None
        if target_name == "include":
            include = _include_target(target)
            if include is None:
                diagnostics.mark(path, reason_code="include_unresolved")
                continue
            module, namespace = include
            child_path = _module_path(module)
            if child_path is None:
                diagnostics.mark(path, reason_code="include_unresolved")
                continue
            child_context = _RouteContext(
                _join(route_context.prefix, pattern),
                route_context.namespaces + ((namespace,) if namespace else ()),
            )
            result.extend(
                _collect_routes(
                    context, child_path, child_context, diagnostics, current_seen, order
                )
            )
            continue
        target_parts = _route_target(target)
        name = _literal_str(_keyword(item, "name"))
        if target_parts is None or (
            name is None and _keyword(item, "name") is not None
        ):
            diagnostics.mark(path, reason_code="route_target_unresolved")
            continue
        kind, raw_target = target_parts
        target_reference = _resolve_reference(
            raw_target, path, _import_bindings(path, tree, item)
        )
        if target_reference is None:
            diagnostics.mark(path, reason_code="route_target_unresolved")
            continue
        target_parts = target_reference.rsplit(".", 1)
        if len(target_parts) != 2:
            diagnostics.mark(path, reason_code="route_target_unresolved")
            continue
        module, declaration = target_parts
        handler = _FunctionTarget(module, declaration) if kind == "function" else None
        cbv = _ClassTarget(module, declaration) if kind == "cbv" else None
        public_name = ":".join(route_context.namespaces + (name,)) if name else None
        ordinal = order[0]
        order[0] += 1
        result.append(
            _RouteSpec(
                _join(route_context.prefix, pattern),
                public_name,
                handler,
                cbv,
                path,
                getattr(item, "lineno", 1),
                f"django/urls/{ordinal}",
                ordinal,
            )
        )
    return tuple(result)


def _unique_index(
    candidates: Mapping[tuple[str, ...], list[str]],
) -> Mapping[tuple[str, ...], str]:
    return MappingProxyType({
        key: values[0] for key, values in candidates.items() if len(values) == 1
    })


def _syntax_index(syntax: Sequence[SyntaxIR]) -> _SyntaxIndex:
    """Index the actual Python Tree-sitter naming contract without suffixes."""

    functions: dict[tuple[str, ...], list[str]] = {}
    classes: dict[tuple[str, ...], list[str]] = {}
    methods: dict[tuple[str, ...], list[str]] = {}
    for item in syntax:
        if item.language != "python":
            continue
        module = _module_name(item.path)
        if module is None:
            continue
        for ordinal, symbol in enumerate(item.symbols):
            key = declaration_local_key("python", item.path, symbol, ordinal)
            if symbol.kind == "class" and not symbol.container:
                classes.setdefault((module, symbol.name), []).append(key)
            elif symbol.kind == "function" and symbol.container:
                methods.setdefault((module, symbol.container, symbol.name), []).append(
                    key
                )
            elif symbol.kind == "function":
                functions.setdefault((module, symbol.name), []).append(key)
    return _SyntaxIndex(
        _unique_index(functions), _unique_index(classes), _unique_index(methods)
    )


def _exception_reference(
    node: ast.AST | None,
    path: str,
    bindings: _ImportBindings,
    index: _SyntaxIndex,
) -> str | None:
    """Resolve an exception class only through visible declarations/imports."""

    target = node.func if isinstance(node, ast.Call) else node
    raw = _dotted(target)
    if raw is None:
        return None
    name = raw.rsplit(".", 1)[-1]
    # A lower-case identifier is a runtime exception value, not a static class.
    if not name or not name[0].isupper():
        return None
    head, *tail = raw.split(".")
    binding = bindings.values.get(head)
    if binding is not None:
        return ".".join((binding.target, *tail))
    if tail:
        return None
    module = _module_name(path)
    if module is not None and (module, raw) in index.classes:
        return f"{module}.{raw}"
    built_in = f"builtins.{raw}"
    return built_in if built_in in _BUILTIN_EXCEPTION_BASES else None


def _inheritance_index(
    context: ExtractionContext,
    syntax: Sequence[SyntaxIR],
    index: _SyntaxIndex,
) -> _InheritanceIndex:
    """Index only class ancestry exposed by the current bounded source set."""

    bases: dict[str, tuple[str, ...]] = {}
    incomplete: set[str] = set()
    for item in syntax:
        if item.language != "python":
            continue
        module = _module_name(item.path)
        parsed = _tree(context, item.path)
        if module is None or parsed is None:
            continue
        _content, tree = parsed
        for node in tree.body:
            if (
                not isinstance(node, ast.ClassDef)
                or (module, node.name) not in index.classes
            ):
                continue
            name = f"{module}.{node.name}"
            bindings = _import_bindings(item.path, tree, node)
            visible_bases = tuple(
                value
                for base in node.bases
                if (value := _exception_reference(base, item.path, bindings, index))
                is not None
            )
            if len(visible_bases) != len(node.bases):
                incomplete.add(name)
            bases[name] = visible_bases
    return _InheritanceIndex(MappingProxyType(bases), frozenset(incomplete))


def _is_subclass(
    child: str,
    parent: str,
    inheritance: _InheritanceIndex,
    seen: frozenset[str] = frozenset(),
    depth: int = 0,
) -> bool | None:
    """Return true/false only for bounded ancestry; unknown paths remain unknown."""

    if child == parent:
        return True
    if child in seen or depth >= _MAX_INHERITANCE_DEPTH:
        return None
    bases = _BUILTIN_EXCEPTION_BASES.get(child)
    incomplete = False
    if bases is None:
        bases = inheritance.bases.get(child)
        if bases is None:
            return None
        incomplete = child in inheritance.incomplete
    outcomes = tuple(
        _is_subclass(base, parent, inheritance, seen | {child}, depth + 1)
        for base in bases
    )
    if any(outcome is True for outcome in outcomes):
        return True
    if incomplete or any(outcome is None for outcome in outcomes):
        return None
    return False


def _handler_catches(
    handler: ast.ExceptHandler,
    raised_type: str,
    path: str,
    bindings: _ImportBindings,
    index: _SyntaxIndex,
    inheritance: _InheritanceIndex,
) -> bool | None:
    if handler.type is None:
        return True
    types = (
        handler.type.elts if isinstance(handler.type, ast.Tuple) else (handler.type,)
    )
    unresolved = False
    for caught in types:
        caught_type = _exception_reference(caught, path, bindings, index)
        if caught_type is None:
            unresolved = True
            continue
        outcome = _is_subclass(raised_type, caught_type, inheritance)
        if outcome is True:
            return True
        unresolved = unresolved or outcome is None
    return None if unresolved else False


def _exception_arms(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    path: str,
    bindings: _ImportBindings,
    index: _SyntaxIndex,
    inheritance: _InheritanceIndex,
) -> tuple[str, ...]:
    """Classify each raise by the handlers that lexically enclose it."""

    found: set[str] = set()

    def visit(node: ast.AST, handlers: tuple[ast.ExceptHandler, ...]) -> None:
        if isinstance(node, ast.Raise):
            raised_type = _exception_reference(node.exc, path, bindings, index)
            if raised_type is None:
                found.add("unresolved_exception_boundary")
                return
            outcomes = tuple(
                _handler_catches(
                    handler, raised_type, path, bindings, index, inheritance
                )
                for handler in handlers
            )
            if any(outcome is True for outcome in outcomes):
                found.add("handled_exception")
            elif any(outcome is None for outcome in outcomes):
                found.add("unresolved_exception_boundary")
            else:
                found.add("unhandled_exception")
            return
        if isinstance(node, ast.Try):
            for child in node.body:
                visit(child, handlers + tuple(node.handlers))
            for child in node.orelse:
                visit(child, handlers)
            for handler in node.handlers:
                for child in handler.body:
                    visit(child, handlers)
            for child in node.finalbody:
                visit(child, handlers)
            return
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
        ):
            if node is not function:
                return
        for child in ast.iter_child_nodes(node):
            visit(child, handlers)

    for statement in function.body:
        visit(statement, ())
    return tuple(
        role
        for role in (
            "handled_exception",
            "unhandled_exception",
            "unresolved_exception_boundary",
        )
        if role in found
    )


def _is_django_access_decorator(
    decorator: ast.AST,
    path: str,
    bindings: _ImportBindings,
) -> bool:
    """Recognize only an explicitly imported Django auth decorator."""

    raw = _decorator_name(decorator)
    if raw is None or not _DOTTED_RE.fullmatch(raw):
        return False
    # Do not use _resolve_reference's local fallback: access control is a
    # framework fact only when an import binding proves its Django origin.
    if raw.split(".", 1)[0] not in bindings.values:
        return False
    resolved = _resolve_reference(raw, path, bindings)
    return resolved in _DJANGO_ACCESS_DECORATORS


def _view_outcome(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    path: str,
    bindings: _ImportBindings,
    index: _SyntaxIndex,
    inheritance: _InheritanceIndex,
) -> _ViewOutcome:
    decorator_denial = any(
        _is_django_access_decorator(decorator, path, bindings)
        for decorator in function.decorator_list
    )
    return _ViewOutcome(
        isinstance(function, ast.AsyncFunctionDef),
        decorator_denial,
        _exception_arms(function, path, bindings, index, inheritance),
    )


def _function_outcomes(
    context: ExtractionContext,
    syntax: Sequence[SyntaxIR],
    index: _SyntaxIndex,
) -> Mapping[str, _ViewOutcome]:
    """Map parsed function bodies only to their exact module/container symbol."""

    values: dict[str, _ViewOutcome] = {}
    inheritance = _inheritance_index(context, syntax, index)
    for item in syntax:
        if item.language != "python":
            continue
        module = _module_name(item.path)
        parsed = _tree(context, item.path)
        if module is None or parsed is None:
            continue
        _content, tree = parsed
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                key = index.functions.get((module, node.name))
                if key is not None:
                    values[key] = _view_outcome(
                        node,
                        item.path,
                        _import_bindings(item.path, tree, node),
                        index,
                        inheritance,
                    )
            elif isinstance(node, ast.ClassDef):
                if (module, node.name) not in index.classes:
                    continue
                for method in node.body:
                    if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    key = index.methods.get((module, node.name, method.name))
                    if key is not None:
                        values[key] = _view_outcome(
                            method,
                            item.path,
                            _import_bindings(item.path, tree, method),
                            index,
                            inheritance,
                        )
    return MappingProxyType(values)


def _cbv_outcome(
    outcomes: Mapping[str, _ViewOutcome],
    index: _SyntaxIndex,
    cbv: _ClassTarget,
) -> tuple[str | None, _ViewOutcome]:
    """Require one exact module/class/dispatch proof before CBV expansion."""

    if (cbv.module, cbv.name) not in index.classes:
        return None, _ViewOutcome(False, False, ())
    dispatch_key = index.methods.get((cbv.module, cbv.name, "dispatch"))
    base = outcomes.get(dispatch_key or "")
    if dispatch_key is None or base is None:
        return None, _ViewOutcome(False, False, ())
    methods = tuple(
        (verb.upper(), key)
        for verb in ("get", "post")
        if (key := index.methods.get((cbv.module, cbv.name, verb))) is not None
        and key in outcomes
    )
    return dispatch_key, replace(base, cbv_methods=methods)


def _candidate_key(candidate: EntrypointCandidate) -> tuple[str, str, int]:
    locator = candidate.registration_locator
    return (locator.source_location.path, locator.structural_pointer, locator.ordinal)


def _candidate(
    context: ExtractionContext,
    spec: _RouteSpec,
    outcomes: Mapping[str, _ViewOutcome],
    index: _SyntaxIndex,
) -> tuple[EntrypointCandidate, _ViewOutcome]:
    content = _text(context, spec.source_path) or ""
    locator = _locator(
        spec.source_path, content, spec.source_line, spec.pointer, spec.source_order
    )
    view_outcome = _ViewOutcome(False, False, ())
    handler_key: str | None = None
    if spec.cbv is not None:
        handler_key, view_outcome = _cbv_outcome(outcomes, index, spec.cbv)
    elif spec.handler is not None:
        handler_key = index.functions.get((spec.handler.module, spec.handler.name))
        view_outcome = outcomes.get(handler_key or "", view_outcome)
    unresolved = None
    if handler_key is None:
        unresolved = local_record_key(
            "python",
            spec.source_path,
            "unresolved_fact",
            "config",
            f"{spec.pointer}/handler",
            spec.source_order,
        )
    evidence = IREvidence(
        EvidenceOrigin.VERIFIED_FROM_CODE if handler_key else EvidenceOrigin.UNRESOLVED,
        "django.urls",
        locator,
        None,
    )
    public_path = spec.path
    return (
        EntrypointCandidate(
            EntrypointKind.HTTP_ROUTE,
            "django",
            MethodSemantics.UNRESTRICTED,
            (),
            public_path,
            spec.name,
            TriggerKind.HTTP,
            f"ALL {public_path}",
            MatchConstraints(None, (), None),
            locator,
            handler_key,
            unresolved,
            (),
            evidence,
        ),
        view_outcome,
    )


def _command_candidates(
    context: ExtractionContext,
    syntax: Sequence[SyntaxIR],
    index: _SyntaxIndex,
    diagnostics: _Diagnostics,
) -> tuple[EntrypointCandidate, ...]:
    rows: list[EntrypointCandidate] = []
    ordinal = 0
    for item in syntax:
        marker = "/management/commands/"
        if marker not in f"/{item.path}":
            continue
        module = _module_name(item.path)
        parsed = _tree(context, item.path)
        if module is None or parsed is None:
            diagnostics.mark(
                item.path,
                CoverageCapability.ENTRYPOINT_DISCOVERY,
                "management_command_unresolved",
            )
            continue
        _source, tree = parsed
        command_classes = [
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "Command"
        ]
        handler_key = index.methods.get((module, "Command", "handle"))
        if (
            len(command_classes) != 1
            or not any(
                isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
                and method.name == "handle"
                for method in command_classes[0].body
            )
            or handler_key is None
            or (module, "Command") not in index.classes
        ):
            diagnostics.mark(
                item.path,
                CoverageCapability.ENTRYPOINT_DISCOVERY,
                "management_command_unresolved",
            )
            continue
        command = Path(item.path).stem
        content = _text(context, item.path) or ""
        locator = _locator(item.path, content, 1, f"django/command/{ordinal}", ordinal)
        evidence = IREvidence(
            EvidenceOrigin.VERIFIED_FROM_CODE,
            "django.command",
            locator,
            None,
        )
        rows.append(
            EntrypointCandidate(
                EntrypointKind.CLI_COMMAND,
                "django",
                MethodSemantics.NOT_APPLICABLE,
                (),
                None,
                command,
                TriggerKind.CLI,
                command,
                MatchConstraints(None, (), None),
                locator,
                handler_key,
                None,
                (),
                evidence,
            )
        )
        ordinal += 1
    return tuple(rows)


def _deployment_candidates(
    context: ExtractionContext,
    syntax: Sequence[SyntaxIR],
) -> tuple[EntrypointCandidate, ...]:
    rows: list[EntrypointCandidate] = []
    for ordinal, item in enumerate(
        value for value in syntax if Path(value.path).name in {"asgi.py", "wsgi.py"}
    ):
        parsed = _tree(context, item.path)
        if parsed is None:
            continue
        content, tree = parsed
        kind = Path(item.path).stem
        function = "get_asgi_application" if kind == "asgi" else "get_wsgi_application"
        static = any(
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "application"
                for target in node.targets
            )
            and isinstance(node.value, ast.Call)
            and _dotted(node.value.func) == function
            for node in tree.body
        )
        if not static:
            continue
        locator = _locator(item.path, content, 1, f"django/{kind}/{ordinal}", ordinal)
        unresolved = local_record_key(
            "python",
            item.path,
            "unresolved_fact",
            "config",
            f"django/{kind}/{ordinal}/application",
            ordinal,
        )
        evidence = IREvidence(
            EvidenceOrigin.UNRESOLVED, "django.deployment", locator, None
        )
        rows.append(
            EntrypointCandidate(
                EntrypointKind.PROCESS_MAIN,
                "django",
                MethodSemantics.NOT_APPLICABLE,
                (),
                None,
                kind,
                TriggerKind.PROCESS,
                kind,
                MatchConstraints(None, (), None),
                locator,
                None,
                unresolved,
                (),
                evidence,
            )
        )
    return tuple(rows)


def _pipeline_key(candidate: EntrypointCandidate, role: str, ordinal: int) -> str:
    locator = candidate.registration_locator
    return local_record_key(
        "python",
        locator.source_location.path,
        "framework_pipeline",
        "config",
        f"{locator.structural_pointer}/pipeline/{role}",
        ordinal,
    )


def _terminal_key(candidate: EntrypointCandidate, role: str, ordinal: int) -> str:
    locator = candidate.registration_locator
    return local_record_key(
        "python",
        locator.source_location.path,
        "framework_terminal",
        "config",
        f"{locator.structural_pointer}/terminal/{role}",
        ordinal,
    )


def _exception_scope_key(candidate: EntrypointCandidate) -> str:
    locator = candidate.registration_locator
    return local_record_key(
        "python",
        locator.source_location.path,
        "framework_exception_scope",
        "config",
        f"{locator.structural_pointer}/exception",
        locator.ordinal,
    )


class DjangoLifecycleAdapter:
    """Framework adapter for only statically proven Django lifecycle facts."""

    language = "python"
    framework = "django"

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
                None,
                (),
            ),
        )

    def _remember(self, context: ExtractionContext, snapshot: _Snapshot) -> None:
        values = dict(self._snapshots)
        values[self._snapshot_key(context)] = snapshot
        self._snapshots = MappingProxyType(values)

    def detected_version(self, context: ExtractionContext) -> str | None:
        for record in context.detected_frameworks:
            if (
                record.language == "python"
                and record.name == "django"
                and record.version
            ):
                return record.version
        for path in _PYPROJECT_FILES:
            content = _text(context, path)
            if content is None:
                continue
            match = re.search(
                r"(?i)django\s*(?:[<>=!~^ ]+)\s*(\d+(?:\.\d+){0,2})", content
            )
            if match:
                return match.group(1)
        return None

    def detect(self, context: ExtractionContext) -> FrameworkDetection:
        detected = (
            any(
                record.language == "python" and record.name == "django"
                for record in context.detected_frameworks
            )
            or self.detected_version(context) is not None
        )
        return FrameworkDetection("python", "django", detected)

    def coverage_events(self, context: ExtractionContext) -> tuple[CoverageEvent, ...]:
        return self._snapshot(context).coverage_events

    def entrypoints(
        self, context: ExtractionContext, syntax: Sequence[SyntaxIR]
    ) -> tuple[EntrypointCandidate, ...]:
        diagnostics = _Diagnostics(set())
        root = _root_urlconf(context, syntax, diagnostics)
        middleware = _middleware(context, syntax, diagnostics)
        index = _syntax_index(syntax)
        outcomes = _function_outcomes(context, syntax, index)
        specs = (
            _collect_routes(context, root, _RouteContext(), diagnostics, set(), [0])
            if root
            else ()
        )
        candidates: list[EntrypointCandidate] = []
        route_map: dict[tuple[str, str, int], _RouteSpec] = {}
        candidate_outcomes: dict[str, _ViewOutcome] = {}
        for spec in specs:
            candidate, outcome = _candidate(context, spec, outcomes, index)
            candidates.append(candidate)
            route_map[_candidate_key(candidate)] = spec
            if candidate.handler_local_key is not None:
                candidate_outcomes[candidate.handler_local_key] = outcome
            if candidate.handler_local_key is None:
                diagnostics.mark(
                    spec.source_path, reason_code="route_handler_unresolved"
                )
            if "unresolved_exception_boundary" in outcome.exception_arms:
                diagnostics.mark(
                    spec.source_path,
                    CoverageCapability.FRAMEWORK_LIFECYCLE,
                    "exception_resolution_unresolved",
                )
        commands = _command_candidates(context, syntax, index, diagnostics)
        snapshot = _Snapshot(
            MappingProxyType(route_map),
            MappingProxyType(candidate_outcomes),
            middleware,
            diagnostics.events(),
        )
        self._remember(context, snapshot)
        all_candidates = (
            candidates + list(commands) + list(_deployment_candidates(context, syntax))
        )
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
                "console_command"
                if candidate.kind is EntrypointKind.CLI_COMMAND
                else f"{candidate.public_name}_application"
            )
            target = (
                FrameworkLocalTarget(candidate.handler_local_key)
                if candidate.handler_local_key
                else FrameworkBoundaryTarget(
                    FrameworkBoundaryDescriptor(
                        "django",
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
        spec = snapshot.routes.get(_candidate_key(candidate))
        outcome = snapshot.outcomes.get(
            candidate.handler_local_key or "", _ViewOutcome(False, False, ())
        )
        middleware = snapshot.middleware
        # The final integer is the registered middleware position.  Keeping it
        # alongside the stage prevents a request short-circuit from jumping to
        # a later layer that was never entered.
        roles: list[tuple[str, str | None, int | None]] = [("url_resolver", None, None)]
        if middleware is None:
            roles.append(("middleware_unresolved_boundary", None, None))
            active_middleware: tuple[_MiddlewareFact, ...] = ()
        else:
            active_middleware = middleware
            roles.extend(
                ("middleware_request", fact.dotted_name, index)
                for index, fact in enumerate(active_middleware)
            )
        if outcome.decorator_denial:
            roles.append(("decorator_access_control", None, None))
        if spec is not None and spec.cbv is not None:
            if candidate.handler_local_key is None:
                roles.append(("cbv_dispatch_boundary", spec.cbv.name, None))
            else:
                roles.append(("cbv_dispatch", None, None))
                roles.extend(
                    (f"cbv_{method.lower()}", target, None)
                    for method, target in outcome.cbv_methods
                )
        else:
            roles.append((
                "async_view" if outcome.is_async else "sync_view",
                None,
                None,
            ))
        response_middleware = tuple(
            (index, fact)
            for index, fact in reversed(tuple(enumerate(active_middleware)))
            if fact.has_response
        )
        roles.extend(
            ("middleware_response", fact.dotted_name, index)
            for index, fact in response_middleware
        )
        roles.append(("response", None, None))
        roles.extend((role, None, None) for role in outcome.exception_arms)
        keys = [
            _pipeline_key(candidate, role, ordinal)
            for ordinal, (role, _name, _middleware_index) in enumerate(roles)
        ]
        response_index = next(
            index
            for index, (role, _name, _middleware_index) in enumerate(roles)
            if role == "response"
        )
        response_start = next(
            (
                index
                for index, (role, _name, _middleware_index) in enumerate(roles)
                if role == "middleware_response"
            ),
            response_index,
        )
        response_positions = [
            index
            for index, (role, _name, _middleware_index) in enumerate(roles)
            if role == "middleware_response"
        ]
        response_for_request: dict[int, int] = {}
        for request_index, (_role, _name, middleware_index) in enumerate(roles):
            if _role != "middleware_request" or middleware_index is None:
                continue
            eligible = [
                index
                for index in response_positions
                if (response_middleware_index := roles[index][2]) is not None
                and response_middleware_index <= middleware_index
            ]
            response_for_request[request_index] = (
                min(eligible, key=lambda index: -int(roles[index][2]))
                if eligible
                else response_index
            )
        exception_indices = {
            role: index
            for index, (role, _name, _middleware_index) in enumerate(roles)
            if role
            in {
                "handled_exception",
                "unhandled_exception",
                "unresolved_exception_boundary",
            }
        }
        segments: list[FrameworkPipelineSegment] = []
        for ordinal, (role, name, middleware_index) in enumerate(roles):
            if (
                role in {"sync_view", "async_view", "cbv_dispatch"}
                and candidate.handler_local_key
            ):
                target = FrameworkLocalTarget(candidate.handler_local_key)
            elif (
                role.startswith("cbv_")
                and role not in {"cbv_dispatch", "cbv_dispatch_boundary"}
                and name
            ):
                target = FrameworkLocalTarget(name)
            else:
                target = FrameworkBoundaryTarget(
                    FrameworkBoundaryDescriptor(
                        "django",
                        role,
                        name,
                        candidate.registration_locator,
                        candidate.evidence,
                    )
                )
            shortcuts: list[Successor] = []
            if role == "middleware_request":
                if middleware_index is None:
                    raise AssertionError("middleware request stage requires position")
                middleware_fact = active_middleware[middleware_index]
                if middleware_fact.may_short_circuit:
                    shortcuts.append(
                        AlwaysSuccessor(keys[response_for_request[ordinal]], 0)
                    )
            if role == "decorator_access_control":
                shortcuts.append(AlwaysSuccessor(keys[response_start], 0))
            if (
                role in {"sync_view", "async_view", "cbv_dispatch"}
                and exception_indices
            ):
                shortcuts.extend(
                    ExceptionSuccessor(
                        keys[index], _exception_scope_key(candidate), None, order
                    )
                    for order, index in enumerate(exception_indices.values())
                )
            if role in {"unhandled_exception", "unresolved_exception_boundary"}:
                success = ReturnSuccessor(
                    _terminal_key(candidate, "exception", ordinal), ordinal
                )
            elif role == "handled_exception":
                success = AlwaysSuccessor(keys[response_start], ordinal)
            elif role == "response":
                success = ReturnSuccessor(
                    _terminal_key(candidate, "response", ordinal), ordinal
                )
            elif role == "middleware_response":
                next_index = ordinal + 1
                success = AlwaysSuccessor(keys[next_index], ordinal)
            elif role == "cbv_dispatch" and outcome.cbv_methods:
                success = AlwaysSuccessor(keys[ordinal + 1], ordinal)
                for method_offset in range(1, len(outcome.cbv_methods)):
                    shortcuts.append(
                        AlwaysSuccessor(
                            keys[ordinal + 1 + method_offset], method_offset
                        )
                    )
            elif role.startswith("cbv_") and role not in {
                "cbv_dispatch",
                "cbv_dispatch_boundary",
            }:
                success = AlwaysSuccessor(keys[response_start], ordinal)
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

    def pipeline_facts(
        self, context: ExtractionContext, candidate: EntrypointCandidate
    ) -> FrameworkPipelineFacts:
        def terminal_spec(
            segment: FrameworkPipelineSegment, _successor: ReturnSuccessor
        ) -> FrameworkTerminalSpec:
            if candidate.kind is not EntrypointKind.HTTP_ROUTE:
                return FrameworkTerminalSpec(TerminalKind.EXIT)
            status = next(
                (
                    value
                    for value in (403, 404, 500)
                    if str(value) in segment.framework_role
                ),
                None,
            )
            return FrameworkTerminalSpec(TerminalKind.RESPONSE, public_status=status)

        pipeline = self.pipeline(context, candidate)
        return framework_pipeline_facts(candidate, pipeline, terminal_spec)


__all__ = ["DjangoLifecycleAdapter"]
