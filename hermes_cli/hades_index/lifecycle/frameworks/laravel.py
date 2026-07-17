"""Static, bounded Laravel entrypoint and lifecycle extraction.

This adapter is deliberately a *configuration reader*, not a Laravel runner.  It
only consumes files through :class:`ExtractionContext.file_accessor`; computed
PHP expressions become typed partial coverage rather than a plausible route or
middleware order.  The resulting pipeline is framework IR, never a graph
artifact, so the lifecycle builder remains the sole canonical-ID authority.
"""

from __future__ import annotations

import hashlib
import json
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


_COMPOSER_FILES = ("composer.lock", "composer.json")
_ROUTE_FILES = (
    "routes/api.php",
    "routes/channels.php",
    "routes/console.php",
    "routes/web.php",
)
_KERNEL_FILES = ("bootstrap/app.php", "app/Http/Kernel.php")
_PROVIDER_FILES = (
    "app/Providers/RouteServiceProvider.php",
    "app/Providers/AppServiceProvider.php",
)
_COMPUTED_RE = re.compile(r"(?:\$|\b(?:env|config|app|resolve)\s*\(|\?\?|\{\{)")
_ROUTE_VERB_RE = re.compile(
    r"(?:\bRoute\s*::|->)\s*(?P<verb>get|post|put|patch|delete|options|any|match|resource|apiResource)\s*\(",
    re.IGNORECASE,
)
_ROUTE_ANY_RE = re.compile(r"\bRoute\s*::", re.IGNORECASE)
_NAMESPACE_RE = re.compile(
    r"\bnamespace\s+(?P<name>[A-Za-z_][A-Za-z0-9_\\]*)\s*;",
    re.MULTILINE,
)
_GROUP_RE = re.compile(
    r"->\s*group\s*\(\s*function\s*\([^)]*\)\s*(?::[^\{]+)?\{", re.DOTALL
)
_CHAIN_START = r"(?:->|\bRoute\s*::)\s*"
_MIDDLEWARE_CALL_RE = re.compile(_CHAIN_START + r"middleware\s*\(", re.DOTALL)
_PREFIX_CALL_RE = re.compile(_CHAIN_START + r"prefix\s*\(", re.DOTALL)
_NAME_CALL_RE = re.compile(_CHAIN_START + r"name\s*\(", re.DOTALL)
_DOMAIN_CALL_RE = re.compile(_CHAIN_START + r"domain\s*\(", re.DOTALL)
_CONTROLLER_MIDDLEWARE_RE = re.compile(r"\$this\s*->\s*middleware\s*\(", re.DOTALL)
_FORM_REQUEST_RE = re.compile(
    r"\b(?P<name>[A-Za-z_][A-Za-z0-9_\\]*Request)\s+\$[A-Za-z_][A-Za-z0-9_]*"
)
_MODEL_PARAMETER_RE = re.compile(
    r"\b(?P<name>[A-Za-z_][A-Za-z0-9_\\]*)\s+\$(?P<variable>[A-Za-z_][A-Za-z0-9_]*)"
)
_AUTHORIZE_RE = re.compile(
    r"(?:\$this\s*->\s*authorize|Gate\s*::\s*authorize|->\s*can)\s*\("
)
_GATE_DECISION_RE = re.compile(r"\bGate\s*::\s*(?P<decision>allows|denies)\s*\(")
_ABORT_RE = re.compile(r"\babort(?:_if|_unless)?\s*\(")
_REDIRECT_RE = re.compile(r"\b(?:redirect|to_route|back)\s*\(")
_THROW_RE = re.compile(r"\bthrow\s+(?:new\s+)?[A-Za-z_\\][A-Za-z0-9_\\]*")
_RESPONSE_RE = re.compile(r"\breturn\s+(?:response\s*\(|new\s+[A-Za-z_\\]*Response\b)")
_JOB_DISPATCH_RE = re.compile(
    r"\b(?P<class>[A-Za-z_][A-Za-z0-9_\\]*)\s*::\s*(?:dispatch|dispatchAfterResponse)\s*\("
)
_EVENT_DISPATCH_RE = re.compile(
    r"\b(?:event|dispatch)\s*\(\s*new\s+(?P<class>[A-Za-z_][A-Za-z0-9_\\]*)\s*\("
)
_QUEUE_DISPATCH_RE = re.compile(
    r"\bQueue\s*::\s*(?:push|later)\s*\(\s*(?P<class>[A-Za-z_][A-Za-z0-9_\\]*)\s*::\s*class"
)

_RESOURCE_IRREGULAR_SINGULARS = {
    "children": "child",
    "feet": "foot",
    "geese": "goose",
    "men": "man",
    "mice": "mouse",
    "people": "person",
    "teeth": "tooth",
    "women": "woman",
}


@dataclass(frozen=True, slots=True)
class _RouteContext:
    prefix: str = ""
    name_prefix: str = ""
    domain: str | None = None
    middleware: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _RouteSpec:
    path: str
    name: str | None
    methods: tuple[str, ...] | None
    controller: str | None
    middleware: tuple[str, ...]
    domain: str | None
    source_path: str
    source_line: int
    pointer: str
    source_order: int
    unresolved: bool = False


@dataclass(frozen=True, slots=True)
class _MiddlewareFacts:
    global_middleware: tuple[str, ...]
    groups: Mapping[str, tuple[str, ...]]
    aliases: Mapping[str, str]
    priority: tuple[str, ...]
    complete: bool


@dataclass(frozen=True, slots=True)
class _HandlerOutcome:
    controller_middleware: tuple[str, ...]
    binding: bool
    validation: bool
    authorization: bool
    aborts: bool
    redirects: bool
    throws: bool
    response: bool
    async_dispatches: tuple[tuple[AsyncDispatchKind, str], ...]
    controller_middleware_complete: bool = True
    handler_complete: bool = True
    gate_decisions: tuple[str, ...] = ()
    gate_complete: bool = True


@dataclass(frozen=True, slots=True)
class _Snapshot:
    middleware: _MiddlewareFacts
    routes: Mapping[tuple[str, str, int], _RouteSpec]
    outcomes: Mapping[str, _HandlerOutcome]
    async_targets: Mapping[str, str]
    event_listeners: Mapping[str, tuple[str, ...]]
    exception_renderer: bool
    coverage_events: tuple[CoverageEvent, ...]


@dataclass(slots=True)
class _Diagnostics:
    records: set[tuple[str, CoverageCapability, str]]

    def mark(
        self,
        path: str,
        capability: CoverageCapability = CoverageCapability.ENTRYPOINT_DISCOVERY,
        reason_code: str = "framework_config_unresolved",
    ) -> None:
        safe = _workspace_path(path)
        if safe is not None:
            self.records.add((safe, capability, reason_code))

    def events(self) -> tuple[CoverageEvent, ...]:
        return tuple(
            CoverageEvent(
                "php",
                capability,
                CoverageOutcome.PARTIAL,
                reason_code,
                path,
                0,
                1,
            )
            for path, capability, reason_code in sorted(
                self.records, key=lambda item: (item[0], item[1].value, item[2])
            )
        )


def _workspace_path(path: str) -> str | None:
    if not isinstance(path, str) or not path:
        return None
    try:
        return normalize_source_path(path)
    except GraphContractError:
        return None


def _text(context: ExtractionContext, path: str) -> str | None:
    safe = _workspace_path(path)
    if safe is None:
        return None
    try:
        return context.file_accessor(Path(safe)).decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _line(content: str, offset: int) -> int:
    return content.count("\n", 0, max(offset, 0)) + 1


def _location(path: str, content: str, offset: int) -> SourceLocationIR:
    number = _line(content, offset)
    return SourceLocationIR(path, number, number, _digest(content))


def _locator(
    path: str, content: str, offset: int, pointer: str, ordinal: int
) -> ConfigLocatorIR:
    return ConfigLocatorIR(_location(path, content, offset), pointer, ordinal)


def _computed(value: object) -> bool:
    return not isinstance(value, str) or bool(_COMPUTED_RE.search(value))


def _matching(text: str, opening: int, left: str, right: str) -> int | None:
    """Return a balanced delimiter close while respecting quoted PHP literals."""

    if opening >= len(text) or text[opening] != left:
        return None
    depth = 0
    quote: str | None = None
    escaped = False
    for index in range(opening, len(text)):
        char = text[index]
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
        elif char == left:
            depth += 1
        elif char == right:
            depth -= 1
            if depth == 0:
                return index
    return None


def _argument_parts(text: str) -> tuple[str, ...] | None:
    """Split a complete PHP argument list, never evaluating an expression."""

    values: list[str] = []
    start = 0
    stack: list[str] = []
    quote: str | None = None
    escaped = False
    for index, char in enumerate(text):
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
        elif char in "([{":
            stack.append(char)
        elif char in ")]}":
            if not stack:
                return None
            opening = stack.pop()
            if (opening, char) not in {("(", ")"), ("[", "]"), ("{", "}")}:
                return None
        elif char == "," and not stack:
            values.append(text[start:index].strip())
            start = index + 1
    if quote is not None or stack:
        return None
    values.append(text[start:].strip())
    return tuple(values)


def _literal(value: str) -> str | None:
    value = value.strip()
    match = re.fullmatch(r"(['\"])(?P<value>(?:\\.|(?!\1).)*)\1", value, re.DOTALL)
    if match is None:
        return None
    result = match.group("value")
    if _computed(result):
        return None
    return result


def _literal_list(value: str) -> tuple[str, ...] | None:
    value = value.strip()
    if not (value.startswith("[") and value.endswith("]")):
        item = _literal(value)
        return (item,) if item is not None else None
    inner = _argument_parts(value[1:-1])
    if inner is None:
        return None
    values: list[str] = []
    for item in inner:
        literal = _literal(item)
        if literal is not None:
            values.append(literal)
            continue
        class_name = _class_literal(item)
        if class_name is None:
            return None
        values.append(class_name)
    return tuple(values)


def _class_literal(value: str) -> str | None:
    match = re.fullmatch(
        r"\\?(?P<name>[A-Za-z_][A-Za-z0-9_\\]*)\s*::\s*class", value.strip()
    )
    return match.group("name").lstrip("\\") if match else None


def _join(prefix: str, path: str) -> str:
    left, right = prefix.strip(), path.strip()
    value = (
        f"{left.rstrip('/')}/{right.lstrip('/')}"
        if left and right
        else left or right or "/"
    )
    if not value.startswith("/"):
        value = "/" + value
    value = re.sub(r"/{2,}", "/", value)
    return value if value == "/" else value.rstrip("/")


def _call_arguments(text: str, match: re.Match[str]) -> tuple[str, int] | None:
    opening = match.end() - 1
    closing = _matching(text, opening, "(", ")")
    if closing is None:
        return None
    return text[opening + 1 : closing], closing + 1


def _chain_values(chain: str, regex: re.Pattern[str]) -> tuple[tuple[str, ...], bool]:
    values: list[str] = []
    for match in regex.finditer(chain):
        parsed = _call_arguments(chain, match)
        if parsed is None:
            return (), False
        args, _end = parsed
        parts = _argument_parts(args)
        if parts is None or len(parts) != 1:
            return (), False
        literal = _literal_list(parts[0])
        if literal is None:
            return (), False
        values.extend(literal)
    return tuple(values), True


def _single_chain_value(chain: str, regex: re.Pattern[str]) -> tuple[str | None, bool]:
    found = list(regex.finditer(chain))
    if not found:
        return None, True
    match = found[-1]
    parsed = _call_arguments(chain, match)
    if parsed is None:
        return None, False
    args, _end = parsed
    parts = _argument_parts(args)
    if parts is None or len(parts) != 1:
        return None, False
    return _literal(parts[0]), _literal(parts[0]) is not None


def _apply_group(context: _RouteContext, chain: str) -> _RouteContext | None:
    prefixes, prefix_ok = _chain_values(chain, _PREFIX_CALL_RE)
    names, name_ok = _chain_values(chain, _NAME_CALL_RE)
    domains, domain_ok = _chain_values(chain, _DOMAIN_CALL_RE)
    middleware, middleware_ok = _chain_values(chain, _MIDDLEWARE_CALL_RE)
    if not all((prefix_ok, name_ok, domain_ok, middleware_ok)) or len(domains) > 1:
        return None
    return _RouteContext(
        prefix=_join(context.prefix, prefixes[-1]) if prefixes else context.prefix,
        name_prefix=context.name_prefix + "".join(names),
        domain=domains[-1] if domains else context.domain,
        middleware=context.middleware + middleware,
    )


def _find_statement_end(text: str, start: int, end: int) -> int | None:
    """Find the first top-level semicolon for an ordinary route expression."""

    quote: str | None = None
    escaped = False
    parens = brackets = 0
    for index in range(start, end):
        char = text[index]
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
        elif char == "(":
            parens += 1
        elif char == ")":
            parens -= 1
        elif char == "[":
            brackets += 1
        elif char == "]":
            brackets -= 1
        elif char == ";" and parens == 0 and brackets == 0:
            return index
    return None


def _route_handler(value: str) -> str | None:
    value = value.strip()
    pieces = (
        _argument_parts(value[1:-1])
        if value.startswith("[") and value.endswith("]")
        else None
    )
    if pieces is not None and len(pieces) == 2:
        class_name = _class_literal(pieces[0])
        method = _literal(pieces[1])
        if class_name and method and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", method):
            return f"{class_name}::{method}"
    class_name = _class_literal(value)
    if class_name:
        return f"{class_name}::__invoke"
    literal = _literal(value)
    if literal and "@" in literal:
        class_name, method = literal.rsplit("@", 1)
        if class_name and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", method):
            normalized_class = class_name.lstrip("\\")
            return f"{normalized_class}::{method}"
    return None


def _route_methods(verb: str, arguments: tuple[str, ...]) -> tuple[str, ...] | None:
    verb = verb.lower()
    mapping = {
        "get": ("GET",),
        "post": ("POST",),
        "put": ("PUT",),
        "patch": ("PATCH",),
        "delete": ("DELETE",),
        "options": ("OPTIONS",),
    }
    if verb in mapping:
        return mapping[verb]
    if verb == "any":
        return None
    if verb == "match" and arguments:
        values = _literal_list(arguments[0])
        if values is None or any(
            not re.fullmatch(r"[A-Za-z]+", item) for item in values
        ):
            return None
        return tuple(sorted({item.upper() for item in values}))
    return None


def _route_name(chain: str) -> tuple[str | None, bool]:
    return _single_chain_value(chain, _NAME_CALL_RE)


def _route_middleware(chain: str) -> tuple[tuple[str, ...], bool]:
    return _chain_values(chain, _MIDDLEWARE_CALL_RE)


def _resource_parameter_name(resource: str) -> str | None:
    """Return the closed English resource-name subset Laravel can prove here."""

    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", resource):
        return None
    folded = resource.casefold()
    irregular = _RESOURCE_IRREGULAR_SINGULARS.get(folded)
    if irregular is not None:
        return irregular if resource.islower() else irregular.capitalize()
    if resource.endswith("ies") and len(resource) > 3:
        return resource[:-3] + "y"
    if resource.endswith(("ches", "shes", "sses", "xes", "zes")):
        return resource[:-2]
    if resource.endswith("s") and not resource.endswith(("ss", "us", "is")):
        return resource[:-1]
    return None


def _resource_specs(
    context: _RouteContext,
    path: str,
    controller: str | None,
    middleware: tuple[str, ...],
    source_path: str,
    source_line: int,
    pointer: str,
    order: int,
    *,
    api_resource: bool,
) -> tuple[_RouteSpec, ...] | None:
    resource = path.strip("/")
    parameter = _resource_parameter_name(resource)
    if parameter is None:
        return None
    base = _join(context.prefix, resource)
    rows = [
        ("index", ("GET",), base),
        ("store", ("POST",), base),
        ("show", ("GET",), _join(base, "{" + parameter + "}")),
        ("update", ("PATCH", "PUT"), _join(base, "{" + parameter + "}")),
        ("destroy", ("DELETE",), _join(base, "{" + parameter + "}")),
    ]
    if not api_resource:
        rows.insert(1, ("create", ("GET",), _join(base, "create")))
        rows.insert(4, ("edit", ("GET",), _join(base, "{" + parameter + "}/edit")))
    return tuple(
        _RouteSpec(
            route_path,
            f"{context.name_prefix}{resource}.{method}",
            methods,
            f"{controller}::{method}" if controller else None,
            context.middleware + middleware,
            context.domain,
            source_path,
            source_line,
            f"{pointer}/resource/{ordinal}",
            order + ordinal,
            controller is None,
        )
        for ordinal, (method, methods, route_path) in enumerate(rows)
    )


def _route_from_statement(
    statement: str,
    context: _RouteContext,
    source_path: str,
    content: str,
    offset: int,
    pointer: str,
    order: int,
    diagnostics: _Diagnostics,
) -> tuple[_RouteSpec, ...]:
    match = _ROUTE_VERB_RE.search(statement)
    if match is None:
        diagnostics.mark(source_path)
        return ()
    effective_context = _apply_group(context, statement[: match.start()])
    if effective_context is None:
        diagnostics.mark(source_path, CoverageCapability.FRAMEWORK_LIFECYCLE)
        return ()
    parsed = _call_arguments(statement, match)
    if parsed is None:
        diagnostics.mark(source_path)
        return ()
    argument_text, after_call = parsed
    arguments = _argument_parts(argument_text)
    if arguments is None:
        diagnostics.mark(source_path)
        return ()
    verb = match.group("verb")
    chain = statement[after_call:]
    middleware, middleware_ok = _route_middleware(chain)
    name, name_ok = _route_name(chain)
    if not middleware_ok or not name_ok:
        diagnostics.mark(source_path, CoverageCapability.FRAMEWORK_LIFECYCLE)
        return ()
    if verb.lower() in {"resource", "apiresource"}:
        if len(arguments) < 2:
            diagnostics.mark(source_path)
            return ()
        resource = _literal(arguments[0])
        controller = _class_literal(arguments[1])
        if resource is None or controller is None:
            diagnostics.mark(source_path)
            return ()
        rows = _resource_specs(
            effective_context,
            resource,
            controller,
            middleware,
            source_path,
            _line(content, offset),
            pointer,
            order,
            api_resource=verb.lower() == "apiresource",
        )
        if rows is None:
            diagnostics.mark(
                source_path,
                CoverageCapability.ENTRYPOINT_DISCOVERY,
                "resource_parameter_unresolved",
            )
            return ()
        return rows
    if len(arguments) < 2:
        diagnostics.mark(source_path)
        return ()
    path = _literal(arguments[0])
    handler = _route_handler(arguments[1])
    methods = _route_methods(verb, arguments)
    if path is None:
        diagnostics.mark(source_path)
        return ()
    # ``any`` is an explicit Laravel semantic, not a collection of guessed
    # verbs; the IR expresses it as unrestricted methods.
    return (
        _RouteSpec(
            _join(effective_context.prefix, path),
            effective_context.name_prefix + name
            if name is not None
            else effective_context.name_prefix or None,
            methods,
            handler,
            effective_context.middleware + middleware,
            effective_context.domain,
            source_path,
            _line(content, offset),
            pointer,
            order,
            handler is None,
        ),
    )


def _routes_in_scope(
    content: str,
    source_path: str,
    start: int,
    end: int,
    context: _RouteContext,
    order: list[int],
    diagnostics: _Diagnostics,
) -> tuple[_RouteSpec, ...]:
    rows: list[_RouteSpec] = []
    cursor = start
    while cursor < end:
        # Group declarations start with ``Route::prefix``/``middleware`` rather
        # than a verb, so scanning only route verbs would skip their static
        # context and incorrectly treat nested routes as top-level.
        match = _ROUTE_ANY_RE.search(content, cursor, end)
        if match is None:
            break
        group_match = _GROUP_RE.search(content, match.start(), end)
        statement_end = _find_statement_end(content, match.start(), end)
        if group_match is not None and (
            statement_end is None or group_match.start() < statement_end
        ):
            opening = group_match.end() - 1
            closing = _matching(content, opening, "{", "}")
            if closing is None or closing >= end:
                diagnostics.mark(source_path)
                break
            group_context = _apply_group(
                context, content[match.start() : group_match.start()]
            )
            if group_context is None:
                diagnostics.mark(source_path, CoverageCapability.FRAMEWORK_LIFECYCLE)
            else:
                rows.extend(
                    _routes_in_scope(
                        content,
                        source_path,
                        opening + 1,
                        closing,
                        group_context,
                        order,
                        diagnostics,
                    )
                )
            cursor = closing + 1
            continue
        if statement_end is None:
            diagnostics.mark(source_path)
            break
        current_order = order[0]
        order[0] += 1
        rows.extend(
            _route_from_statement(
                content[match.start() : statement_end],
                context,
                source_path,
                content,
                match.start(),
                f"laravel/routes/{current_order}",
                current_order,
                diagnostics,
            )
        )
        cursor = statement_end + 1
    return tuple(rows)


def _array_after(content: str, start: int) -> tuple[str, int] | None:
    opening = content.find("[", start)
    if opening < 0:
        return None
    closing = _matching(content, opening, "[", "]")
    return (
        (content[opening : closing + 1], closing + 1) if closing is not None else None
    )


def _php_array_values(value: str) -> tuple[str, ...] | None:
    return _literal_list(value)


def _config_middleware(
    context: ExtractionContext, diagnostics: _Diagnostics
) -> _MiddlewareFacts:
    global_rows: list[str] = []
    groups: dict[str, tuple[str, ...]] = {}
    aliases: dict[str, str] = {}
    priority: tuple[str, ...] = ()
    for path in _KERNEL_FILES:
        content = _text(context, path)
        if content is None:
            continue
        for name, destination in (("middleware", global_rows),):
            match = re.search(rf"\${name}\s*=", content)
            if match:
                parsed = _array_after(content, match.end())
                values = _php_array_values(parsed[0]) if parsed else None
                if values is None:
                    diagnostics.mark(path, CoverageCapability.FRAMEWORK_LIFECYCLE)
                else:
                    destination.extend(values)
        groups_match = re.search(r"\$middlewareGroups\s*=", content)
        if groups_match:
            parsed = _array_after(content, groups_match.end())
            body = parsed[0][1:-1] if parsed else None
            if body is None:
                diagnostics.mark(path, CoverageCapability.FRAMEWORK_LIFECYCLE)
            else:
                for match in re.finditer(
                    r"['\"](?P<name>[A-Za-z0-9_.:-]+)['\"]\s*=>\s*\[", body
                ):
                    close = _matching(body, match.end() - 1, "[", "]")
                    values = (
                        _php_array_values(body[match.end() - 1 : close + 1])
                        if close is not None
                        else None
                    )
                    if values is None:
                        diagnostics.mark(path, CoverageCapability.FRAMEWORK_LIFECYCLE)
                    else:
                        groups[match.group("name")] = values
        for property_name in ("middlewareAliases", "routeMiddleware"):
            aliases_match = re.search(rf"\${property_name}\s*=", content)
            if aliases_match:
                parsed = _array_after(content, aliases_match.end())
                body = parsed[0][1:-1] if parsed else None
                if body is None:
                    diagnostics.mark(path, CoverageCapability.FRAMEWORK_LIFECYCLE)
                else:
                    for entry in _argument_parts(body) or ():
                        assignment = re.fullmatch(
                            r"\s*['\"](?P<alias>[A-Za-z0-9_.:-]+)['\"]\s*=>\s*(?P<target>[^,]+)\s*",
                            entry,
                            re.DOTALL,
                        )
                        if assignment is None:
                            diagnostics.mark(
                                path, CoverageCapability.FRAMEWORK_LIFECYCLE
                            )
                            continue
                        target = _class_literal(assignment.group("target"))
                        if target is None:
                            diagnostics.mark(
                                path, CoverageCapability.FRAMEWORK_LIFECYCLE
                            )
                            continue
                        aliases[assignment.group("alias")] = target
        priority_match = re.search(r"\$middlewarePriority\s*=", content)
        if priority_match:
            parsed = _array_after(content, priority_match.end())
            values = _php_array_values(parsed[0]) if parsed else None
            if values is None:
                diagnostics.mark(path, CoverageCapability.FRAMEWORK_LIFECYCLE)
            else:
                priority = values
    # Laravel 11 bootstrap configuration uses methods rather than properties.
    bootstrap = _text(context, "bootstrap/app.php")
    if bootstrap is not None:
        for method, target in (("append", global_rows), ("prepend", global_rows)):
            for match in re.finditer(rf"->\s*{method}\s*\(", bootstrap):
                parsed = _call_arguments(bootstrap, match)
                values = _php_array_values(parsed[0]) if parsed else None
                if values is None:
                    diagnostics.mark(
                        "bootstrap/app.php", CoverageCapability.FRAMEWORK_LIFECYCLE
                    )
                else:
                    target.extend(values)
        for match in re.finditer(r"->\s*alias\s*\(", bootstrap):
            parsed = _call_arguments(bootstrap, match)
            if parsed is None or not parsed[0].strip().startswith("["):
                diagnostics.mark(
                    "bootstrap/app.php", CoverageCapability.FRAMEWORK_LIFECYCLE
                )
                continue
            entries = _argument_parts(parsed[0][1:-1])
            if entries is None:
                diagnostics.mark(
                    "bootstrap/app.php", CoverageCapability.FRAMEWORK_LIFECYCLE
                )
                continue
            for entry in entries:
                assignment = re.fullmatch(
                    r"\s*['\"](?P<alias>[A-Za-z0-9_.:-]+)['\"]\s*=>\s*(?P<target>[^,]+)\s*",
                    entry,
                    re.DOTALL,
                )
                target = (
                    _class_literal(assignment.group("target")) if assignment else None
                )
                if assignment is None or target is None:
                    diagnostics.mark(
                        "bootstrap/app.php", CoverageCapability.FRAMEWORK_LIFECYCLE
                    )
                else:
                    aliases[assignment.group("alias")] = target
        for match in re.finditer(r"->\s*group\s*\(", bootstrap):
            parsed = _call_arguments(bootstrap, match)
            parts = _argument_parts(parsed[0]) if parsed else None
            values = _php_array_values(parts[1]) if parts and len(parts) == 2 else None
            group_name = _literal(parts[0]) if parts else None
            if group_name is None or values is None:
                diagnostics.mark(
                    "bootstrap/app.php", CoverageCapability.FRAMEWORK_LIFECYCLE
                )
            else:
                groups[group_name] = values
        for match in re.finditer(r"->\s*priority\s*\(", bootstrap):
            parsed = _call_arguments(bootstrap, match)
            values = _php_array_values(parsed[0]) if parsed else None
            if values is None:
                diagnostics.mark(
                    "bootstrap/app.php", CoverageCapability.FRAMEWORK_LIFECYCLE
                )
            else:
                priority = values
    return _MiddlewareFacts(
        tuple(global_rows),
        MappingProxyType(dict(groups)),
        MappingProxyType(dict(aliases)),
        priority,
        not diagnostics.records,
    )


def _provider_route_contexts(
    context: ExtractionContext, diagnostics: _Diagnostics
) -> Mapping[str, tuple[_RouteContext, ...]]:
    """Read static RouteServiceProvider registrations without evaluating PHP.

    A provider may register one route file more than once under different static
    contexts.  Each occurrence is retained in source order; a computed
    ``base_path``/chain is a partial boundary and never silently falls back to
    an unprefixed registration.
    """

    result: dict[str, list[_RouteContext]] = {}
    registration = re.compile(
        r"\bRoute\s*::(?P<chain>.*?)\s*->\s*group\s*\(\s*"
        r"base_path\s*\(\s*(['\"])(?P<path>routes/[A-Za-z0-9_./-]+\.php)\2\s*\)\s*\)",
        re.DOTALL,
    )
    for path in _PROVIDER_FILES:
        content = _text(context, path)
        if content is None:
            continue
        for match in registration.finditer(content):
            route_path = _workspace_path(match.group("path"))
            route_context = _apply_group(
                _RouteContext(), "Route::" + match.group("chain")
            )
            if route_path is None or route_context is None:
                diagnostics.mark(path, CoverageCapability.FRAMEWORK_LIFECYCLE)
                continue
            result.setdefault(route_path, []).append(route_context)
        if "base_path(" in content and not registration.search(content):
            diagnostics.mark(path, CoverageCapability.FRAMEWORK_LIFECYCLE)
    return MappingProxyType({
        path: tuple(contexts) for path, contexts in sorted(result.items())
    })


def _source_declares_class(content: str, class_name: str) -> bool:
    """Require a unique class declaration and, for FQCNs, its namespace."""

    normalized = class_name.lstrip("\\")
    namespace, separator, short_class = normalized.rpartition("\\")
    class_matches = list(
        re.finditer(rf"\bclass\s+{re.escape(short_class or normalized)}\b", content)
    )
    if len(class_matches) != 1:
        return False
    if not separator:
        return True
    namespace_match = _NAMESPACE_RE.search(content)
    return namespace_match is not None and namespace_match.group("name") == namespace


def _fqcn_controller_path(class_name: str) -> str | None:
    normalized = class_name.lstrip("\\")
    if not normalized.startswith("App\\"):
        return None
    return "app/" + normalized[len("App\\") :].replace("\\", "/") + ".php"


def _handler_key(
    context: ExtractionContext, syntax: Sequence[SyntaxIR], handler: str | None
) -> str | None:
    if handler is None or "::" not in handler:
        return None
    class_name, method = handler.rsplit("::", 1)
    short_class = class_name.rsplit("\\", 1)[-1]
    expected_path = _fqcn_controller_path(class_name) if "\\" in class_name else None
    if "\\" in class_name and expected_path is None:
        return None
    exact: list[str] = []
    for item in syntax:
        if expected_path is not None:
            if item.parsed_file.path != expected_path:
                continue
            content = _text(context, item.parsed_file.path)
            if content is None or not _source_declares_class(content, class_name):
                continue
        for ordinal, symbol in enumerate(item.parsed_file.symbols):
            name = symbol.name
            short = name.replace("::", ".").split(".")
            if len(short) >= 2 and short[-1] == method and short[-2] == short_class:
                exact.append(
                    local_record_key(
                        "php",
                        item.parsed_file.path,
                        "executable_declaration",
                        "ast",
                        f"symbol/{name}",
                        ordinal,
                    )
                )
    return exact[0] if len(exact) == 1 else None


def _async_target_keys(syntax: Sequence[SyntaxIR]) -> Mapping[str, str]:
    """Index only unique static class ``handle`` symbols for async dispatch."""

    candidates: dict[str, list[str]] = {}
    for item in syntax:
        for ordinal, symbol in enumerate(item.parsed_file.symbols):
            parts = symbol.name.replace("::", ".").split(".")
            if len(parts) < 2 or parts[-1] not in {"handle", "__invoke"}:
                continue
            key = local_record_key(
                "php",
                item.parsed_file.path,
                "executable_declaration",
                "ast",
                f"symbol/{symbol.name}",
                ordinal,
            )
            candidates.setdefault(parts[-2], []).append(key)
    return MappingProxyType({
        name: values[0] for name, values in candidates.items() if len(values) == 1
    })


def _handler_source(context: ExtractionContext, handler: str | None) -> str | None:
    if handler is None or "::" not in handler:
        return None
    class_name, _method = handler.rsplit("::", 1)
    short = class_name.rsplit("\\", 1)[-1]
    fqcn_path = _fqcn_controller_path(class_name) if "\\" in class_name else None
    if "\\" in class_name:
        content = _text(context, fqcn_path) if fqcn_path is not None else None
        return (
            content
            if content is not None and _source_declares_class(content, class_name)
            else None
        )
    candidates = (
        f"app/Http/Controllers/{short}.php",
        f"app/Jobs/{short}.php",
        f"app/Listeners/{short}.php",
        f"app/Console/Commands/{short}.php",
    )
    for path in candidates:
        content = _text(context, path)
        if content is not None and _source_declares_class(content, short):
            return content
    return None


def _is_terminable_middleware(context: ExtractionContext, middleware: str) -> bool:
    """Prove ``terminate`` on the configured middleware class itself."""

    class_name = middleware.rsplit("\\", 1)[-1]
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", class_name):
        return False
    content = _text(context, f"app/Http/Middleware/{class_name}.php")
    return (
        content is not None
        and _method_source(content, f"{class_name}::terminate") is not None
    )


def _method_source(content: str, handler: str | None) -> str | None:
    """Return one statically identified handler declaration, never a class-wide guess."""

    if handler is None or "::" not in handler:
        return None
    class_name, method = handler.rsplit("::", 1)
    short_class = class_name.rsplit("\\", 1)[-1]
    if not _source_declares_class(content, class_name):
        return None
    class_matches = list(re.finditer(rf"\bclass\s+{re.escape(short_class)}\b", content))
    if len(class_matches) != 1:
        return None
    class_opening = content.find("{", class_matches[0].end())
    class_closing = (
        _matching(content, class_opening, "{", "}") if class_opening >= 0 else None
    )
    if class_closing is None:
        return None
    class_body = content[class_opening + 1 : class_closing]
    match = re.search(
        rf"\bfunction\s+{re.escape(method)}\s*\([^)]*\)\s*(?::[^{{]+)?\{{",
        class_body,
        re.DOTALL,
    )
    if match is None:
        return None
    opening = match.end() - 1
    closing = _matching(class_body, opening, "{", "}")
    return class_body[match.start() : closing + 1] if closing is not None else None


def _controller_middleware(
    content: str | None, handler: str | None
) -> tuple[tuple[str, ...], bool]:
    """Select only literal controller middleware applicable to one action."""

    if content is None or handler is None or "::" not in handler:
        return (), False
    class_name, selected_method = handler.rsplit("::", 1)
    short_class = class_name.rsplit("\\", 1)[-1]
    class_matches = list(re.finditer(rf"\bclass\s+{re.escape(short_class)}\b", content))
    if len(class_matches) != 1:
        return (), False
    class_opening = content.find("{", class_matches[0].end())
    class_closing = (
        _matching(content, class_opening, "{", "}") if class_opening >= 0 else None
    )
    if class_closing is None:
        return (), False
    class_body = content[class_opening + 1 : class_closing]
    if re.search(r"\bfunction\s+__construct\b", class_body) is None:
        return (), True
    constructor = _method_source(content, f"{class_name}::__construct")
    if constructor is None:
        return (), False
    output: list[str] = []
    for match in _CONTROLLER_MIDDLEWARE_RE.finditer(constructor):
        parsed = _call_arguments(constructor, match)
        parts = _argument_parts(parsed[0]) if parsed else None
        values = _literal_list(parts[0]) if parts and len(parts) >= 1 else None
        if values is None or parsed is None:
            return (), False
        statement_end = _find_statement_end(constructor, parsed[1], len(constructor))
        if statement_end is None:
            return (), False
        tail = constructor[parsed[1] : statement_end]
        selectors: list[tuple[str, tuple[str, ...]]] = []
        for selector_name in ("only", "except"):
            selector_match = re.search(rf"->\s*{selector_name}\s*\(", tail)
            if selector_match is None:
                continue
            selector_call = _call_arguments(tail, selector_match)
            selector_parts = (
                _argument_parts(selector_call[0]) if selector_call else None
            )
            selector_values = (
                _literal_list(selector_parts[0])
                if selector_parts and len(selector_parts) == 1
                else None
            )
            if selector_values is None:
                return (), False
            selectors.append((selector_name, selector_values))
        if len(selectors) > 1:
            return (), False
        if selectors:
            selector_name, selector_values = selectors[0]
            if selector_name == "only" and selected_method not in selector_values:
                continue
            if selector_name == "except" and selected_method in selector_values:
                continue
        output.extend(values)
    return tuple(output), True


def _handler_outcome(
    context: ExtractionContext, handler: str | None
) -> _HandlerOutcome:
    content = _handler_source(context, handler)
    if content is None:
        return _HandlerOutcome(
            (), False, False, False, False, False, False, False, (), False, False
        )
    controller_middleware, controller_complete = _controller_middleware(
        content, handler
    )
    method_source = _method_source(content, handler)
    if method_source is None:
        return _HandlerOutcome(
            controller_middleware,
            False,
            False,
            False,
            False,
            False,
            False,
            False,
            (),
            controller_complete,
            False,
        )
    opening = method_source.find("{")
    body = method_source[opening + 1 : -1] if opening >= 0 else method_source
    form_request = bool(_FORM_REQUEST_RE.search(method_source))
    model_parameter = bool(_MODEL_PARAMETER_RE.search(method_source))
    dispatches: list[tuple[AsyncDispatchKind, str]] = []
    dispatches.extend(
        (AsyncDispatchKind.JOB, item.group("class").lstrip("\\"))
        for item in _JOB_DISPATCH_RE.finditer(body)
    )
    dispatches.extend(
        (AsyncDispatchKind.EVENT, item.group("class").lstrip("\\"))
        for item in _EVENT_DISPATCH_RE.finditer(body)
    )
    dispatches.extend(
        (AsyncDispatchKind.QUEUE, item.group("class").lstrip("\\"))
        for item in _QUEUE_DISPATCH_RE.finditer(body)
    )
    unique: list[tuple[AsyncDispatchKind, str]] = []
    for item in dispatches:
        if item not in unique:
            unique.append(item)
    gate_decisions, gate_complete = _gate_decisions(body)
    return _HandlerOutcome(
        controller_middleware,
        model_parameter,
        form_request,
        bool(_AUTHORIZE_RE.search(body)),
        bool(_ABORT_RE.search(body)),
        bool(_REDIRECT_RE.search(body)),
        bool(_THROW_RE.search(body)),
        bool(_RESPONSE_RE.search(body)),
        tuple(unique),
        controller_complete,
        True,
        gate_decisions,
        gate_complete,
    )


def _gate_decisions(body: str) -> tuple[tuple[str, ...], bool]:
    """Accept only literal ability plus simple subject-variable Gate decisions."""

    decisions: list[str] = []
    for match in _GATE_DECISION_RE.finditer(body):
        parsed = _call_arguments(body, match)
        parts = _argument_parts(parsed[0]) if parsed else None
        ability = _literal(parts[0]) if parts and len(parts) == 2 else None
        subject = parts[1].strip() if parts and len(parts) == 2 else ""
        if (
            ability is None
            or re.fullmatch(r"\$[A-Za-z_][A-Za-z0-9_]*", subject) is None
        ):
            return (), False
        decision = "allow" if match.group("decision") == "allows" else "deny"
        if decision not in decisions:
            decisions.append(decision)
    return tuple(decisions), True


def _expand_middleware(
    values: tuple[str, ...], facts: _MiddlewareFacts
) -> tuple[str, ...] | None:
    output: list[str] = []
    expanding: set[str] = set()

    def append(value: str) -> bool:
        name, _separator, _argument = value.partition(":")
        if name in facts.groups:
            if name in expanding:
                return False
            expanding.add(name)
            for child in facts.groups[name]:
                if not append(child):
                    return False
            expanding.remove(name)
            return True
        output.append(facts.aliases.get(name, value))
        return True

    for value in values:
        if not append(value):
            return None
    # Laravel priority is a stable reordering only for entries covered by the
    # configured priority list.  Preserve all unprioritized declaration order.
    priority_index = {value: index for index, value in enumerate(facts.priority)}
    indexed = list(enumerate(output))
    indexed.sort(
        key=lambda item: (
            (0, priority_index[item[1]]) if item[1] in priority_index else (1, item[0])
        )
    )
    deduped: list[str] = []
    for _index, item in indexed:
        if item not in deduped:
            deduped.append(item)
    return tuple(deduped)


def _candidate_key(candidate: EntrypointCandidate) -> tuple[str, str, int]:
    locator = candidate.registration_locator
    return (locator.source_location.path, locator.structural_pointer, locator.ordinal)


def _event_listener_map(
    context: ExtractionContext, diagnostics: _Diagnostics
) -> Mapping[str, tuple[str, ...]]:
    """Read only static event→listener registrations from the conventional provider."""

    path = "app/Providers/EventServiceProvider.php"
    content = _text(context, path)
    if content is None:
        return MappingProxyType({})
    result: dict[str, list[str]] = {}
    for match in re.finditer(
        r"(?P<event>[A-Za-z_][A-Za-z0-9_\\]*)\s*::\s*class\s*=>\s*\[",
        content,
    ):
        opening = match.end() - 1
        closing = _matching(content, opening, "[", "]")
        values = _php_array_values(content[opening : closing + 1]) if closing else None
        if values is None:
            diagnostics.mark(path, CoverageCapability.ASYNC)
            continue
        event = match.group("event").rsplit("\\", 1)[-1]
        result.setdefault(event, []).extend(values)
    for match in re.finditer(r"Event\s*::\s*listen\s*\(", content):
        parsed = _call_arguments(content, match)
        parts = _argument_parts(parsed[0]) if parsed else None
        if parts is None or len(parts) < 2:
            diagnostics.mark(path, CoverageCapability.ASYNC)
            continue
        event, listener = _class_literal(parts[0]), _class_literal(parts[1])
        if event is None or listener is None:
            diagnostics.mark(path, CoverageCapability.ASYNC)
            continue
        result.setdefault(event.rsplit("\\", 1)[-1], []).append(listener)
    return MappingProxyType({
        event: tuple(dict.fromkeys(listeners))
        for event, listeners in sorted(result.items())
    })


def _pipeline_key(candidate: EntrypointCandidate, role: str, ordinal: int) -> str:
    locator = candidate.registration_locator
    return local_record_key(
        "php",
        locator.source_location.path,
        "framework_pipeline",
        "config",
        f"{locator.structural_pointer}/pipeline/{role}",
        ordinal,
    )


def _terminal_key(candidate: EntrypointCandidate, role: str, ordinal: int) -> str:
    locator = candidate.registration_locator
    return local_record_key(
        "php",
        locator.source_location.path,
        "framework_terminal",
        "config",
        f"{locator.structural_pointer}/terminal/{role}",
        ordinal,
    )


def _exception_scope_key(candidate: EntrypointCandidate) -> str:
    locator = candidate.registration_locator
    return local_record_key(
        "php",
        locator.source_location.path,
        "framework_exception_scope",
        "config",
        f"{locator.structural_pointer}/exceptions",
        locator.ordinal,
    )


def _candidate(
    context: ExtractionContext, spec: _RouteSpec, syntax: Sequence[SyntaxIR]
) -> EntrypointCandidate:
    content = _text(context, spec.source_path) or ""
    locator = _locator(
        spec.source_path, content, spec.source_line - 1, spec.pointer, spec.source_order
    )
    handler = (
        None if spec.unresolved else _handler_key(context, syntax, spec.controller)
    )
    unresolved = None
    if handler is None:
        unresolved = local_record_key(
            "php",
            spec.source_path,
            "unresolved_fact",
            "config",
            f"{spec.pointer}/handler",
            spec.source_order,
        )
    evidence = IREvidence(
        EvidenceOrigin.UNRESOLVED if unresolved else EvidenceOrigin.VERIFIED_FROM_CODE,
        "laravel.routes",
        locator,
        None,
    )
    return EntrypointCandidate(
        EntrypointKind.HTTP_ROUTE,
        "laravel",
        MethodSemantics.EXPLICIT if spec.methods else MethodSemantics.UNRESTRICTED,
        spec.methods or (),
        spec.path,
        spec.name,
        TriggerKind.HTTP,
        f"{'|'.join(spec.methods) if spec.methods else 'ALL'} {spec.path}",
        MatchConstraints(spec.domain, (), None),
        locator,
        handler,
        unresolved,
        (),
        evidence,
    )


def _console_entries(
    context: ExtractionContext, syntax: Sequence[SyntaxIR], diagnostics: _Diagnostics
) -> tuple[EntrypointCandidate, ...]:
    rows: list[EntrypointCandidate] = []
    ordinal = 0
    artisan_pattern = re.compile(
        r"\bArtisan\s*::\s*command\s*\(\s*(['\"])(?P<name>[^'\"]+)\1\s*,\s*(?P<handler>[^)]+)\)"
    )
    schedule_pattern = re.compile(
        r"(?:\bSchedule\s*::|\$schedule\s*->)\s*command\s*\(\s*(?P<handler>[^)]+)\)\s*->\s*(?P<frequency>[A-Za-z_][A-Za-z0-9_]*)\s*\("
    )
    for path in ("routes/console.php", "app/Console/Kernel.php"):
        content = _text(context, path)
        if content is None:
            continue
        registrations: list[
            tuple[int, EntrypointKind, TriggerKind, str, str, str | None]
        ] = []
        registrations.extend(
            (
                match.start(),
                EntrypointKind.CLI_COMMAND,
                TriggerKind.CLI,
                match.group("name"),
                match.group("handler"),
                None,
            )
            for match in artisan_pattern.finditer(content)
        )
        for match in schedule_pattern.finditer(content):
            handler_expression = match.group("handler")
            handler_class = _class_literal(handler_expression)
            executable_name = (
                handler_class.rsplit("\\", 1)[-1]
                if handler_class is not None
                else _literal(handler_expression)
            )
            if executable_name is None:
                diagnostics.mark(
                    path,
                    CoverageCapability.ENTRYPOINT_DISCOVERY,
                    "framework_config_unresolved",
                )
                continue
            registrations.append((
                match.start(),
                EntrypointKind.SCHEDULED_JOB,
                TriggerKind.SCHEDULE,
                executable_name,
                handler_expression,
                match.group("frequency"),
            ))
        for offset, kind, trigger, public_name, handler_expression, frequency in sorted(
            registrations, key=lambda item: item[0]
        ):
            handler_class = _class_literal(handler_expression)
            handler = f"{handler_class}::handle" if handler_class else None
            handler_key = _handler_key(context, syntax, handler)
            pointer = f"laravel/console/{ordinal}"
            if frequency is not None:
                pointer += f"/schedule/{frequency}"
            locator = _locator(path, content, offset, pointer, ordinal)
            unresolved = (
                None
                if handler_key is not None
                else local_record_key(
                    "php",
                    path,
                    "unresolved_fact",
                    "config",
                    f"{pointer}/handler",
                    ordinal,
                )
            )
            evidence = IREvidence(
                EvidenceOrigin.VERIFIED_FROM_CODE
                if handler_key
                else EvidenceOrigin.UNRESOLVED,
                "laravel.console",
                locator,
                None,
            )
            rows.append(
                EntrypointCandidate(
                    kind,
                    "laravel",
                    MethodSemantics.NOT_APPLICABLE,
                    (),
                    None,
                    public_name,
                    trigger,
                    frequency or public_name,
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


class LaravelLifecycleAdapter:
    """Framework adapter for statically provable Laravel lifecycle facts."""

    language = "php"
    framework = "laravel"

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
                _MiddlewareFacts(
                    (),
                    MappingProxyType({}),
                    MappingProxyType({}),
                    (),
                    True,
                ),
                MappingProxyType({}),
                MappingProxyType({}),
                MappingProxyType({}),
                MappingProxyType({}),
                False,
                (),
            ),
        )

    def _remember(self, context: ExtractionContext, snapshot: _Snapshot) -> None:
        rows = dict(self._snapshots)
        rows[self._snapshot_key(context)] = snapshot
        self._snapshots = MappingProxyType(rows)

    def coverage_events(self, context: ExtractionContext) -> tuple[CoverageEvent, ...]:
        return self._snapshot(context).coverage_events

    def detected_version(self, context: ExtractionContext) -> str | None:
        for record in context.detected_frameworks:
            if record.language == "php" and record.name == "laravel" and record.version:
                return record.version
        for path in _COMPOSER_FILES:
            content = _text(context, path)
            if content is None:
                continue
            try:
                document = json.loads(content)
            except json.JSONDecodeError:
                continue
            for package in document.get("packages", ()):
                if (
                    isinstance(package, Mapping)
                    and package.get("name") == "laravel/framework"
                    and isinstance(package.get("version"), str)
                ):
                    return package["version"].lstrip("v")
            for section in ("require", "require-dev"):
                requirement = document.get(section, {})
                if isinstance(requirement, Mapping) and isinstance(
                    requirement.get("laravel/framework"), str
                ):
                    match = re.search(
                        r"(\d+\.\d+(?:\.\d+)?)", requirement["laravel/framework"]
                    )
                    if match:
                        return match.group(1)
        return None

    def detect(self, context: ExtractionContext) -> FrameworkDetection:
        detected = (
            any(
                record.language == "php" and record.name == "laravel"
                for record in context.detected_frameworks
            )
            or self.detected_version(context) is not None
        )
        return FrameworkDetection("php", "laravel", detected)

    def entrypoints(
        self, context: ExtractionContext, syntax: Sequence[SyntaxIR]
    ) -> tuple[EntrypointCandidate, ...]:
        diagnostics = _Diagnostics(set())
        middleware = _config_middleware(context, diagnostics)
        provider_contexts = _provider_route_contexts(context, diagnostics)
        order = [0]
        specs: list[_RouteSpec] = []
        for path in _ROUTE_FILES:
            if path == "routes/console.php":
                continue
            content = _text(context, path)
            if content is not None:
                contexts = provider_contexts.get(path, (_RouteContext(),))
                for route_context in contexts:
                    specs.extend(
                        _routes_in_scope(
                            content,
                            path,
                            0,
                            len(content),
                            route_context,
                            order,
                            diagnostics,
                        )
                    )
        candidates = [_candidate(context, spec, syntax) for spec in specs]
        outcomes: dict[str, _HandlerOutcome] = {}
        async_targets = _async_target_keys(syntax)
        event_listeners = _event_listener_map(context, diagnostics)
        for candidate, spec in zip(candidates, specs, strict=True):
            if candidate.handler_local_key is not None:
                outcome = _handler_outcome(context, spec.controller)
                # Parameter model binding is observed from the registered route as
                # well as the handler declaration.  It remains a short-circuit
                # boundary, never an asserted successful model lookup.
                if "{" in spec.path:
                    outcome = replace(outcome, binding=True)
                outcomes[candidate.handler_local_key] = outcome
                if not outcome.controller_middleware_complete:
                    diagnostics.mark(
                        spec.source_path,
                        CoverageCapability.FRAMEWORK_LIFECYCLE,
                        "controller_middleware_unresolved",
                    )
                if not outcome.handler_complete:
                    diagnostics.mark(
                        spec.source_path,
                        CoverageCapability.FRAMEWORK_LIFECYCLE,
                        "handler_outcome_unresolved",
                    )
                if not outcome.gate_complete:
                    diagnostics.mark(
                        spec.source_path,
                        CoverageCapability.FRAMEWORK_LIFECYCLE,
                        "gate_unresolved",
                    )
                base_effective = (
                    _expand_middleware(
                        middleware.global_middleware + spec.middleware,
                        middleware,
                    )
                    if middleware.complete
                    else None
                )
                effective = (
                    _expand_middleware(
                        middleware.global_middleware
                        + spec.middleware
                        + outcome.controller_middleware,
                        middleware,
                    )
                    if base_effective is not None
                    and outcome.controller_middleware_complete
                    else base_effective
                )
                if middleware.complete and effective is None:
                    diagnostics.mark(
                        spec.source_path,
                        CoverageCapability.FRAMEWORK_LIFECYCLE,
                        "middleware_cycle",
                    )
                for dispatch_kind, target_name in outcome.async_dispatches:
                    target_names = (
                        event_listeners.get(target_name.rsplit("\\", 1)[-1], ())
                        if dispatch_kind is AsyncDispatchKind.EVENT
                        else (target_name,)
                    )
                    if not target_names or not any(
                        async_targets.get(name.rsplit("\\", 1)[-1])
                        for name in target_names
                    ):
                        diagnostics.mark(
                            spec.source_path,
                            CoverageCapability.ASYNC,
                            "async_target_unresolved",
                        )
        console = _console_entries(context, syntax, diagnostics)
        renderer = any(
            _text(context, path) is not None
            for path in ("app/Exceptions/Handler.php", "bootstrap/app.php")
        )
        snapshot = _Snapshot(
            middleware,
            MappingProxyType({
                _candidate_key(candidate): spec
                for candidate, spec in zip(candidates, specs, strict=True)
            }),
            MappingProxyType(outcomes),
            async_targets,
            event_listeners,
            renderer,
            diagnostics.events(),
        )
        self._remember(context, snapshot)
        all_candidates = candidates + list(console)
        completed: list[EntrypointCandidate] = []
        for candidate in all_candidates:
            segments = self.pipeline(context, candidate)
            completed.append(
                replace(
                    candidate,
                    framework_segment_keys=tuple(
                        segment.local_key for segment in segments
                    ),
                )
            )
        return tuple(completed)

    def pipeline(
        self, context: ExtractionContext, candidate: EntrypointCandidate
    ) -> tuple[FrameworkPipelineSegment, ...]:
        snapshot = self._snapshot(context)
        if candidate.kind is not EntrypointKind.HTTP_ROUTE:
            role = (
                "scheduler"
                if candidate.kind is EntrypointKind.SCHEDULED_JOB
                else "console_command"
            )
            target = (
                FrameworkLocalTarget(candidate.handler_local_key)
                if candidate.handler_local_key
                else FrameworkBoundaryTarget(
                    FrameworkBoundaryDescriptor(
                        "laravel",
                        "unresolved_boundary",
                        None,
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
        spec = snapshot.routes.get(_candidate_key(candidate))
        outcome = snapshot.outcomes.get(
            candidate.handler_local_key or "",
            _HandlerOutcome(
                (), False, False, False, False, False, False, False, (), False, False
            ),
        )
        base_effective = (
            _expand_middleware(
                snapshot.middleware.global_middleware
                + (spec.middleware if spec else ()),
                snapshot.middleware,
            )
            if snapshot.middleware.complete
            else None
        )
        effective = (
            _expand_middleware(
                snapshot.middleware.global_middleware
                + (spec.middleware if spec else ())
                + outcome.controller_middleware,
                snapshot.middleware,
            )
            if base_effective is not None and outcome.controller_middleware_complete
            else base_effective
        )
        roles: list[tuple[str, str | None, bool]] = [("router", None, False)]
        if effective is None:
            roles.append(("middleware_unresolved_boundary", None, False))
        else:
            for middleware in effective:
                roles.append(("middleware", middleware, False))
            if not outcome.controller_middleware_complete:
                roles.append(("controller_middleware_unresolved_boundary", None, False))
        if outcome.binding:
            roles.append(("route_binding", None, False))
        if any(
            item.endswith("Authenticate") or item == "auth" for item in effective or ()
        ):
            roles.append(("authentication", None, False))
        if outcome.authorization or any(
            item.endswith("Authorize") or item.startswith("can:")
            for item in (effective or ())
        ):
            roles.append(("authorization", None, False))
        for decision in outcome.gate_decisions:
            roles.append((f"gate_{decision}", None, False))
        if not outcome.gate_complete:
            roles.append(("gate_unresolved_boundary", None, False))
        if outcome.validation:
            roles.append(("validation", None, False))
        roles.append(("handler", None, False))
        for kind, target in outcome.async_dispatches:
            roles.append((f"{kind.value}_dispatch", target, False))
        if candidate.unresolved_fact_local_key is not None:
            roles.append(("unresolved_boundary", None, False))
        roles.append(("response", None, False))
        for middleware in effective or ():
            if _is_terminable_middleware(context, middleware):
                roles.append(("terminating_middleware", middleware, False))
        if outcome.throws and snapshot.exception_renderer:
            # This is an error arm, not the normal successor of ``response``.
            roles.append(("exception_renderer", None, False))
        keys = [
            _pipeline_key(candidate, role, ordinal)
            for ordinal, (role, _name, _term) in enumerate(roles)
        ]
        segments: list[FrameworkPipelineSegment] = []
        for ordinal, (role, name, _terminating) in enumerate(roles):
            target = (
                FrameworkLocalTarget(candidate.handler_local_key)
                if role == "handler" and candidate.handler_local_key
                else FrameworkBoundaryTarget(
                    FrameworkBoundaryDescriptor(
                        "laravel",
                        role,
                        name,
                        candidate.registration_locator,
                        candidate.evidence,
                    )
                )
            )
            shortcuts: list[Successor] = []
            if role in {
                "route_binding",
                "authentication",
                "authorization",
                "validation",
            }:
                shortcuts.append(
                    ReturnSuccessor(_terminal_key(candidate, role, ordinal), 0)
                )
            if role == "handler":
                if outcome.throws:
                    renderer_index = next(
                        (
                            index
                            for index, item in enumerate(roles)
                            if item[0] == "exception_renderer"
                        ),
                        None,
                    )
                    if renderer_index is not None:
                        shortcuts.append(
                            ExceptionSuccessor(
                                keys[renderer_index],
                                _exception_scope_key(candidate),
                                None,
                                len(shortcuts),
                            )
                        )
            if role.endswith("_dispatch") and name:
                lookup = name.rsplit("\\", 1)[-1]
                targets = (
                    snapshot.event_listeners.get(lookup, ())
                    if role == "event_dispatch"
                    else (lookup,)
                )
                kind = {
                    "job_dispatch": AsyncDispatchKind.JOB,
                    "event_dispatch": AsyncDispatchKind.EVENT,
                    "queue_dispatch": AsyncDispatchKind.QUEUE,
                }[role]
                for target_name in targets:
                    async_handler = snapshot.async_targets.get(
                        target_name.rsplit("\\", 1)[-1]
                    )
                    if async_handler is not None:
                        shortcuts.append(
                            AsyncSuccessor(async_handler, kind, len(shortcuts) + 1)
                        )
            if role == "exception_renderer":
                response_index = next(
                    index for index, item in enumerate(roles) if item[0] == "response"
                )
                success = AlwaysSuccessor(keys[response_index], ordinal)
            elif role == "response" and any(
                item[0] == "terminating_middleware" for item in roles[ordinal + 1 :]
            ):
                next_key = keys[ordinal + 1]
                success = AlwaysSuccessor(next_key, ordinal)
            elif role == "terminating_middleware":
                next_index = ordinal + 1
                if (
                    next_index < len(roles)
                    and roles[next_index][0] == "terminating_middleware"
                ):
                    success = AlwaysSuccessor(keys[next_index], ordinal)
                else:
                    success = ReturnSuccessor(
                        _terminal_key(candidate, "response", ordinal), ordinal
                    )
            elif role == "response":
                success = ReturnSuccessor(
                    _terminal_key(candidate, "response", ordinal), ordinal
                )
            else:
                # Error segments are intentionally not connected by the normal
                # backbone.  They are reached only by the handler exception arm.
                next_index = ordinal + 1
                if (
                    next_index < len(roles)
                    and roles[next_index][0] == "exception_renderer"
                ):
                    success = ReturnSuccessor(
                        _terminal_key(candidate, "response", ordinal), ordinal
                    )
                else:
                    next_key = (
                        keys[next_index]
                        if next_index < len(keys)
                        else _terminal_key(candidate, "response", ordinal)
                    )
                    success = AlwaysSuccessor(next_key, ordinal)
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


__all__ = ["LaravelLifecycleAdapter"]
