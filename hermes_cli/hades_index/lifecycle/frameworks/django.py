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
from hermes_cli.hades_index.lifecycle.frameworks import FrameworkDetection
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
    local_record_key,
)
from hermes_cli.hades_index.tree_sitter_adapter import SyntaxIR


_PYPROJECT_FILES = ("pyproject.toml", "requirements.txt", "requirements/base.txt")
_URL_CALLS = frozenset({"path", "re_path"})
_DYNAMIC_REASON = "framework_config_unresolved"
_DOTTED_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")


@dataclass(frozen=True, slots=True)
class _RouteContext:
    prefix: str = ""
    namespaces: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _RouteSpec:
    path: str
    name: str | None
    handler: str | None
    cbv: str | None
    source_path: str
    source_line: int
    pointer: str
    source_order: int


@dataclass(frozen=True, slots=True)
class _ViewOutcome:
    is_async: bool
    decorator_denial: bool
    raises: bool
    handled_exception: bool
    cbv_methods: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class _MiddlewareFact:
    dotted_name: str
    has_response: bool
    may_short_circuit: bool


@dataclass(frozen=True, slots=True)
class _Snapshot:
    routes: Mapping[tuple[str, str, int], _RouteSpec]
    outcomes: Mapping[str, _ViewOutcome]
    handler_keys: Mapping[str, str]
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
                may_short_circuit = request_method is not None and any(
                    isinstance(node, ast.Return)
                    and not (
                        isinstance(node.value, ast.Constant)
                        and node.value.value is None
                    )
                    for node in ast.walk(request_method)
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


def _urlpatterns(tree: ast.Module) -> tuple[ast.AST, ...] | None:
    values: list[ast.AST] = []
    found = False
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "urlpatterns"
            for target in statement.targets
        ):
            if not isinstance(statement.value, (ast.List, ast.Tuple)):
                return None
            values.extend(statement.value.elts)
            found = True
        elif (
            isinstance(statement, ast.AugAssign)
            and isinstance(statement.target, ast.Name)
            and statement.target.id == "urlpatterns"
        ):
            if not isinstance(statement.value, (ast.List, ast.Tuple)):
                return None
            values.extend(statement.value.elts)
            found = True
    return tuple(values) if found else ()


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


def _route_target(node: ast.AST) -> tuple[str | None, str | None] | None:
    """Return (function, CBV class) only for an unambiguous static expression."""

    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "as_view"
    ):
        cbv = _dotted(node.func.value)
        return (None, cbv) if cbv else None
    handler = _dotted(node)
    return (handler, None) if handler else None


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
        if (
            not isinstance(item, ast.Call)
            or _dotted(item.func) not in _URL_CALLS
            or len(item.args) < 2
        ):
            diagnostics.mark(path, reason_code="url_pattern_unresolved")
            continue
        pattern = _literal_str(item.args[0])
        if pattern is None:
            diagnostics.mark(path, reason_code="url_pattern_unresolved")
            continue
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
        handler, cbv = target_parts
        if handler is None and cbv is None:
            diagnostics.mark(path, reason_code="route_target_unresolved")
            continue
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


def _handler_key(syntax: Sequence[SyntaxIR], dotted: str) -> str | None:
    return _handler_keys(syntax).get(dotted)


def _handler_keys(syntax: Sequence[SyntaxIR]) -> Mapping[str, str]:
    """Index each unique syntax declaration at the frozen local-key formula."""

    candidates: dict[str, list[str]] = {}
    for item in syntax:
        for ordinal, symbol in enumerate(item.symbols):
            candidates.setdefault(symbol.name, []).append(
                local_record_key(
                    "python",
                    item.path,
                    "executable_declaration",
                    "ast",
                    f"symbol/{symbol.name}",
                    ordinal,
                )
            )
    return MappingProxyType({
        name: keys[0] for name, keys in candidates.items() if len(keys) == 1
    })


def _function_outcomes(
    context: ExtractionContext, syntax: Sequence[SyntaxIR]
) -> Mapping[str, _ViewOutcome]:
    """Index functions and class methods only when a unique syntax symbol exists."""

    values: dict[str, _ViewOutcome] = {}
    for item in syntax:
        if item.language != "python":
            continue
        parsed = _tree(context, item.path)
        if parsed is None:
            continue
        _content, tree = parsed
        entries: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                entries.append((node.name, node))
            elif isinstance(node, ast.ClassDef):
                entries.extend(
                    (f"{node.name}.{method.name}", method)
                    for method in node.body
                    if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
                )
        for name, function in entries:
            matches = [
                symbol
                for symbol in item.symbols
                if symbol.name == name or symbol.name.endswith(f".{name}")
            ]
            if len(matches) != 1:
                continue
            symbolic_name = matches[0].name
            decorator_denial = any(
                decorator_name.endswith((
                    "login_required",
                    "permission_required",
                    "user_passes_test",
                ))
                for decorator in function.decorator_list
                if (decorator_name := _decorator_name(decorator)) is not None
            )
            raises = any(isinstance(node, ast.Raise) for node in ast.walk(function))
            handled = any(
                isinstance(node, ast.Try) and node.handlers
                for node in ast.walk(function)
            )
            values[symbolic_name] = _ViewOutcome(
                isinstance(function, ast.AsyncFunctionDef),
                decorator_denial,
                raises,
                handled,
            )
    return MappingProxyType(values)


def _cbv_outcome(
    outcomes: Mapping[str, _ViewOutcome],
    cbv: str,
) -> tuple[str | None, _ViewOutcome]:
    """Resolve a CBV only through its explicit dispatch/method declarations."""

    short = cbv.rsplit(".", 1)[-1]
    dispatch_name = next(
        (name for name in outcomes if name.endswith(f"{short}.dispatch")), None
    )
    methods = tuple(
        sorted(
            (
                (name.rsplit(".", 1)[-1].upper(), name)
                for name in outcomes
                if name.endswith(f"{short}.get") or name.endswith(f"{short}.post")
            ),
            key=lambda item: item[0],
        )
    )
    if dispatch_name is None:
        return None, _ViewOutcome(False, False, False, False, methods)
    base = outcomes[dispatch_name]
    return dispatch_name, replace(base, cbv_methods=methods)


def _candidate_key(candidate: EntrypointCandidate) -> tuple[str, str, int]:
    locator = candidate.registration_locator
    return (locator.source_location.path, locator.structural_pointer, locator.ordinal)


def _candidate(
    context: ExtractionContext,
    syntax: Sequence[SyntaxIR],
    spec: _RouteSpec,
    outcomes: Mapping[str, _ViewOutcome],
) -> tuple[EntrypointCandidate, _ViewOutcome]:
    content = _text(context, spec.source_path) or ""
    locator = _locator(
        spec.source_path, content, spec.source_line, spec.pointer, spec.source_order
    )
    view_outcome = _ViewOutcome(False, False, False, False)
    handler_name = spec.handler
    if spec.cbv is not None:
        handler_name, view_outcome = _cbv_outcome(outcomes, spec.cbv)
    elif handler_name is not None:
        view_outcome = outcomes.get(handler_name, view_outcome)
    handler_key = _handler_key(syntax, handler_name) if handler_name else None
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
    return (
        EntrypointCandidate(
            EntrypointKind.HTTP_ROUTE,
            "django",
            MethodSemantics.UNRESTRICTED,
            (),
            spec.path,
            spec.name,
            TriggerKind.HTTP,
            f"ALL {spec.path}",
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
) -> tuple[EntrypointCandidate, ...]:
    rows: list[EntrypointCandidate] = []
    ordinal = 0
    for item in syntax:
        marker = "/management/commands/"
        if marker not in f"/{item.path}":
            continue
        command = Path(item.path).stem
        handler = next(
            (
                symbol.name
                for symbol in item.symbols
                if symbol.name.endswith("Command.handle")
            ),
            None,
        )
        content = _text(context, item.path) or ""
        locator = _locator(item.path, content, 1, f"django/command/{ordinal}", ordinal)
        handler_key = _handler_key((item,), handler) if handler else None
        unresolved = (
            None
            if handler_key
            else local_record_key(
                "python",
                item.path,
                "unresolved_fact",
                "config",
                f"django/command/{ordinal}/handler",
                ordinal,
            )
        )
        evidence = IREvidence(
            EvidenceOrigin.VERIFIED_FROM_CODE
            if handler_key
            else EvidenceOrigin.UNRESOLVED,
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
                unresolved,
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
        outcomes = _function_outcomes(context, syntax)
        handler_keys = _handler_keys(syntax)
        specs = (
            _collect_routes(context, root, _RouteContext(), diagnostics, set(), [0])
            if root
            else ()
        )
        candidates: list[EntrypointCandidate] = []
        route_map: dict[tuple[str, str, int], _RouteSpec] = {}
        candidate_outcomes: dict[str, _ViewOutcome] = {}
        for spec in specs:
            candidate, outcome = _candidate(context, syntax, spec, outcomes)
            candidates.append(candidate)
            route_map[_candidate_key(candidate)] = spec
            if candidate.handler_local_key is not None:
                candidate_outcomes[candidate.handler_local_key] = outcome
            if candidate.handler_local_key is None:
                diagnostics.mark(
                    spec.source_path, reason_code="route_handler_unresolved"
                )
        snapshot = _Snapshot(
            MappingProxyType(route_map),
            MappingProxyType(candidate_outcomes),
            handler_keys,
            middleware,
            diagnostics.events(),
        )
        self._remember(context, snapshot)
        all_candidates = (
            candidates
            + list(_command_candidates(context, syntax))
            + list(_deployment_candidates(context, syntax))
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
            candidate.handler_local_key or "", _ViewOutcome(False, False, False, False)
        )
        middleware = snapshot.middleware
        roles: list[tuple[str, str | None]] = [("url_resolver", None)]
        if middleware is None:
            roles.append(("middleware_unresolved_boundary", None))
            active_middleware: tuple[_MiddlewareFact, ...] = ()
        else:
            active_middleware = middleware
            roles.extend(
                ("middleware_request", fact.dotted_name) for fact in active_middleware
            )
        if outcome.decorator_denial:
            roles.append(("decorator_access_control", None))
        if spec is not None and spec.cbv is not None:
            if candidate.handler_local_key is None:
                roles.append(("cbv_dispatch_boundary", spec.cbv))
            else:
                roles.append(("cbv_dispatch", None))
                roles.extend(
                    (f"cbv_{method.lower()}", target)
                    for method, target in outcome.cbv_methods
                )
        else:
            roles.append(("async_view" if outcome.is_async else "sync_view", None))
        response_middleware = tuple(
            fact for fact in reversed(active_middleware) if fact.has_response
        )
        roles.extend(
            ("middleware_response", fact.dotted_name) for fact in response_middleware
        )
        roles.append(("response", None))
        exception_roles: list[tuple[str, str | None]] = []
        if outcome.raises:
            exception_roles.append((
                "handled_exception"
                if outcome.handled_exception
                else "unhandled_exception",
                None,
            ))
        roles.extend(exception_roles)
        keys = [
            _pipeline_key(candidate, role, ordinal)
            for ordinal, (role, _name) in enumerate(roles)
        ]
        response_start = next(
            (
                index
                for index, (role, _name) in enumerate(roles)
                if role == "middleware_response"
            ),
            next(
                index for index, (role, _name) in enumerate(roles) if role == "response"
            ),
        )
        request_positions = [
            index
            for index, (role, _name) in enumerate(roles)
            if role == "middleware_request"
        ]
        response_positions = [
            index
            for index, (role, _name) in enumerate(roles)
            if role == "middleware_response"
        ]
        response_for_request: dict[int, int] = {}
        for request_index in request_positions:
            name = roles[request_index][1]
            response_for_request[request_index] = next(
                (index for index in response_positions if roles[index][1] == name),
                response_start,
            )
        exception_index = next(
            (
                index
                for index, (role, _name) in enumerate(roles)
                if role in {"handled_exception", "unhandled_exception"}
            ),
            None,
        )
        segments: list[FrameworkPipelineSegment] = []
        for ordinal, (role, name) in enumerate(roles):
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
                method_key = snapshot.handler_keys.get(name)
                target = (
                    FrameworkBoundaryTarget(
                        FrameworkBoundaryDescriptor(
                            "django",
                            "cbv_method_boundary",
                            name,
                            candidate.registration_locator,
                            candidate.evidence,
                        )
                    )
                    if method_key is None
                    else FrameworkLocalTarget(method_key)
                )
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
                middleware_fact = next(
                    fact for fact in active_middleware if fact.dotted_name == name
                )
                if middleware_fact.may_short_circuit:
                    shortcuts.append(
                        AlwaysSuccessor(keys[response_for_request[ordinal]], 0)
                    )
            if role == "decorator_access_control":
                shortcuts.append(AlwaysSuccessor(keys[response_start], 0))
            if (
                role in {"sync_view", "async_view", "cbv_dispatch"}
                and exception_index is not None
            ):
                shortcuts.append(
                    ExceptionSuccessor(
                        keys[exception_index], _exception_scope_key(candidate), None, 0
                    )
                )
            if role == "unhandled_exception":
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


__all__ = ["DjangoLifecycleAdapter"]
