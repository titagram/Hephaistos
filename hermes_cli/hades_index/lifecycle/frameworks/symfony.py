"""Static, privacy-safe Symfony entrypoint and request-pipeline extraction.

The adapter deliberately reads only configuration and PHP source available through
``ExtractionContext.file_accessor``.  It never executes PHP, boots a Symfony
kernel, or turns a computed route/service into a guessed target.
"""

from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as element_tree
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

try:  # PyYAML is already a project dependency; retain a safe failure mode.
    import yaml
except ImportError:  # pragma: no cover - exercised by integration environments.
    yaml = None  # type: ignore[assignment]

from hermes_cli.hades_graph_v2.identity import condition_hash, normalize_source_path
from hermes_cli.hades_graph_v2.schema import GraphContractError
from hermes_cli.hades_graph_v2.model import (
    EntrypointKind,
    EvidenceOrigin,
    MethodSemantics,
    TriggerKind,
)
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
    Successor,
    SourceLocationIR,
    local_record_key,
)
from hermes_cli.hades_index.tree_sitter_adapter import StructuralSymbol, SyntaxIR


_COMPOSER_FILES = ("composer.lock", "composer.json")
_ROUTE_FILES = (
    "config/routes.yaml",
    "config/routes.yml",
    "config/routes.xml",
    "config/routes.php",
)
_SECURITY_FILES = (
    "config/packages/security.yaml",
    "config/packages/security.yml",
    "config/packages/security.xml",
    "config/packages/security.php",
)
_SERVICE_FILES = (
    "config/services.yaml",
    "config/services.yml",
    "config/services.xml",
    "config/services.php",
)
_ROUTE_ATTRIBUTE_RE = re.compile(
    r"#\[\s*(?:[A-Za-z0-9_\\]+\\)?Route\s*\((?P<args>.*?)\)\s*\]",
    re.DOTALL,
)
_ROUTE_ANNOTATION_RE = re.compile(
    r"@(?:[A-Za-z0-9_\\]+\\)?Route\s*\((?P<args>.*?)\)", re.DOTALL
)
_DOCBLOCK_RE = re.compile(r"/\*\*(?P<body>.*?)\*/", re.DOTALL)
_CLASS_RE = re.compile(
    r"\bclass\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?:\s+extends\s+(?P<parent>[A-Za-z_\\][A-Za-z0-9_\\]*))?",
    re.MULTILINE,
)
_NAMESPACE_RE = re.compile(
    r"\bnamespace\s+(?P<name>[A-Za-z_][A-Za-z0-9_\\]*)\s*;",
    re.MULTILINE,
)
_METHOD_RE = re.compile(
    r"\b(?:public|protected|private)?\s*(?:static\s+)?function\s+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\(",
    re.MULTILINE,
)
_DECLARATION_RE = re.compile(
    r"\b(?:final\s+|abstract\s+)?class\s+(?P<class>[A-Za-z_][A-Za-z0-9_]*)"
    r"|\b(?:public|protected|private)?\s*(?:static\s+)?function\s+"
    r"(?P<method>[A-Za-z_][A-Za-z0-9_]*)\s*\(",
    re.MULTILINE,
)
_PHP_ROUTE_RE = re.compile(
    r"->add\s*\(\s*['\"](?P<name>[^'\"]+)['\"]\s*,\s*"
    r"['\"](?P<path>[^'\"]+)['\"]\s*\)"
    r"(?P<chain>[^;]{0,1200})",
    re.DOTALL,
)
_PHP_IMPORT_RE = re.compile(
    r"->import\s*\(\s*['\"](?P<resource>[^'\"]+)['\"]"
    r"(?:\s*,\s*['\"][^'\"]+['\"])?\s*\)"
    r"(?P<chain>[^;]{0,1200})",
    re.DOTALL,
)
_PHP_ROUTE_OPERATION_START_RE = re.compile(r"->(?:add|import)\s*\(")
_PHP_CONTROLLER_RE = re.compile(
    r"->controller\s*\(\s*['\"](?P<controller>[^'\"]+)['\"]\s*\)"
)
_PHP_METHODS_RE = re.compile(r"->methods\s*\(\s*\[(?P<items>[^]]*)\]\s*\)")
_PHP_SERVICE_RE = re.compile(
    r"->set\s*\(\s*['\"](?P<id>[^'\"]+)['\"]\s*,\s*"
    r"(?P<class>[A-Za-z_\\][A-Za-z0-9_\\]*)::class\s*\)"
)
_PHP_SET_CLASS_RE = re.compile(
    r"->set\s*\(\s*(?P<class>[A-Za-z_\\][A-Za-z0-9_\\]*)::class\s*\)"
    r"(?P<chain>[^;]{0,1200})",
    re.DOTALL,
)
_PHP_TAG_RE = re.compile(
    r"->tag\s*\(\s*['\"](?P<name>[^'\"]+)['\"]"
    r"(?:\s*,\s*\[(?P<attrs>[^]]*)\])?\s*\)"
)
_SUBSCRIBED_EVENT_RE = re.compile(
    r"KernelEvents::(?P<event>REQUEST|RESPONSE|EXCEPTION)\s*=>\s*"
    r"\[?\s*['\"](?P<method>[A-Za-z_][A-Za-z0-9_]*)['\"]"
    r"(?:\s*,\s*(?P<priority>-?\d+))?",
    re.MULTILINE,
)
_RESPONSE_RE = re.compile(
    r"\breturn\s+(?:new\s+)?(?:[A-Za-z_\\]*Response|\$response)\b",
    re.MULTILINE,
)
_THROW_RE = re.compile(r"\bthrow\s+new\s+[A-Za-z_\\][A-Za-z0-9_\\]*", re.MULTILINE)
_COMPUTED_RE = re.compile(r"(?:%[^%]+%|\$|\b(?:env|parameter|service)\s*\()")


@dataclass(frozen=True, slots=True)
class _RouteContext:
    prefix: str = ""
    name_prefix: str = ""
    methods: tuple[str, ...] | None = None
    host: str | None = None
    condition: str | None = None
    priority: int = 0
    unresolved: bool = False


@dataclass(frozen=True, slots=True)
class _RouteSpec:
    path: str
    name: str | None
    methods: tuple[str, ...] | None
    host: str | None
    condition: str | None
    controller: str | None
    priority: int
    source_path: str
    source_line: int
    structural_pointer: str
    source_order: int
    unresolved: bool = False


@dataclass(frozen=True, slots=True)
class _Listener:
    event: str
    service: str
    priority: int
    source_path: str
    source_order: int
    method_name: str
    returns_response: bool


@dataclass(frozen=True, slots=True)
class _Voter:
    service: str
    attributes: frozenset[str]
    subject_types: frozenset[str]


@dataclass(frozen=True, slots=True)
class _ServiceFacts:
    bindings: Mapping[str, str]
    listeners: tuple[_Listener, ...]
    voters: tuple[_Voter, ...]
    exception_handlers: tuple[_Listener, ...]


@dataclass(frozen=True, slots=True)
class _ControllerOutcome:
    returns_response: bool
    throws: bool
    authorization_needs: frozenset[tuple[str, str]]


@dataclass(frozen=True, slots=True)
class _ExtractionSnapshot:
    services: _ServiceFacts
    controller_outcomes: Mapping[str, _ControllerOutcome]
    coverage_events: tuple[CoverageEvent, ...]


@dataclass(slots=True)
class _Diagnostics:
    """Collect safe, typed omissions while preserving successful facts."""

    paths: dict[str, set[CoverageCapability]]

    def mark(
        self,
        path: str,
        capability: CoverageCapability = CoverageCapability.ENTRYPOINT_DISCOVERY,
    ) -> None:
        safe = _workspace_path(path)
        if safe is not None:
            self.paths.setdefault(safe, set()).add(capability)

    def events(self) -> tuple[CoverageEvent, ...]:
        return tuple(
            CoverageEvent(
                "php",
                capability,
                CoverageOutcome.PARTIAL,
                "framework_config_unresolved",
                path,
                0,
                1,
            )
            for path, capabilities in sorted(self.paths.items())
            for capability in sorted(capabilities, key=lambda value: value.value)
        )


def _workspace_path(path: str) -> str | None:
    """Return one canonical workspace-relative path or reject it before I/O."""

    if not isinstance(path, str) or not path:
        return None
    try:
        return normalize_source_path(path)
    except GraphContractError:
        return None


def _text(context: ExtractionContext, path: str) -> str | None:
    """Read only a canonical source-relative path through the scoped accessor."""

    safe_path = _workspace_path(path)
    if safe_path is None:
        return None
    try:
        return context.file_accessor(Path(safe_path)).decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _line(content: str, offset: int) -> int:
    return content.count("\n", 0, max(0, offset)) + 1


def _source_location(path: str, content: str, line: int) -> SourceLocationIR:
    return SourceLocationIR(path, max(1, line), max(1, line), _digest(content))


def _config_locator(
    path: str, content: str, line: int, pointer: str, ordinal: int
) -> ConfigLocatorIR:
    return ConfigLocatorIR(_source_location(path, content, line), pointer, ordinal)


def _normalize_methods(value: object) -> tuple[str, ...] | None:
    if value is None:
        return None
    if isinstance(value, str):
        if _is_computed(value):
            return None
        if not re.fullmatch(r"\s*[A-Za-z]+(?:[\s,|]+[A-Za-z]+)*\s*", value):
            return None
        values = re.findall(r"[A-Za-z]+", value)
    elif isinstance(value, (list, tuple)):
        if not value or any(
            not isinstance(item, str)
            or _is_computed(item)
            or re.fullmatch(r"[A-Za-z]+", item) is None
            for item in value
        ):
            return None
        values = list(value)
    else:
        return None
    methods = tuple(sorted({item.upper() for item in values if item}))
    return methods or None


def _join_path(prefix: str, path: str) -> str:
    left = prefix.strip()
    right = path.strip()
    if not left:
        combined = right or "/"
    elif not right:
        combined = left
    else:
        combined = f"{left.rstrip('/')}/{right.lstrip('/')}"
    if not combined.startswith("/"):
        combined = f"/{combined}"
    combined = re.sub(r"/{2,}", "/", combined)
    if combined != "/":
        combined = combined.rstrip("/")
    return combined


def _as_mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _safe_yaml(
    content: str, path: str, diagnostics: _Diagnostics
) -> Mapping[str, object] | None:
    if yaml is None:
        diagnostics.mark(path, CoverageCapability.FRAMEWORK_LIFECYCLE)
        return None
    try:
        loaded = yaml.safe_load(content)
    except yaml.YAMLError:
        diagnostics.mark(path)
        return None
    return _as_mapping(loaded)


def _is_computed(value: object) -> bool:
    return not isinstance(value, str) or bool(_COMPUTED_RE.search(value))


def _php_quoted_literal(value: str, offset: int = 0) -> tuple[str, int] | None:
    """Read one PHP single/double quoted literal without evaluating it."""

    index = offset
    while index < len(value) and value[index].isspace():
        index += 1
    if index >= len(value) or value[index] not in {"'", '"'}:
        return None
    quote = value[index]
    start = index + 1
    index = start
    while index < len(value):
        if value[index] == "\\":
            index += 2
            continue
        if value[index] == quote:
            return value[start:index], index + 1
        index += 1
    return None


def _php_literal_argument(args: str, name: str) -> tuple[bool, str | None]:
    """Return whether a named argument exists and its closed string literal."""

    pattern = re.compile(rf"\b{re.escape(name)}\s*[:=]\s*", re.DOTALL)
    match = pattern.search(args)
    if match is None:
        return False, None
    literal = _php_quoted_literal(args, match.end())
    if literal is None:
        return True, None
    _value, end = literal
    tail = args[end:].lstrip()
    # A literal followed by an expression operator is not a literal argument:
    # recording its prefix would fabricate a concrete route constraint.
    if tail and not tail.startswith(","):
        return True, None
    return True, literal[0]


def _php_positional_path(args: str) -> tuple[bool, str | None]:
    match = re.match(r"\s*(?P<value>[^,)]*)", args, re.DOTALL)
    if match is None:
        return False, None
    value = match.group("value").strip()
    literal = re.fullmatch(r"(['\"])(?P<value>.*?)\1", value, re.DOTALL)
    return True, literal.group("value") if literal is not None else None


def _php_methods_argument(args: str) -> tuple[bool, tuple[str, ...] | None]:
    """Parse only a complete literal method list, never a partial regex match."""

    match = re.search(
        r"\bmethods\s*[:=]\s*(?P<value>\[[^]]*\]|[^,)]*)", args, re.DOTALL
    )
    if match is None:
        return False, None
    value = match.group("value").strip()
    if (value.startswith("[") and value.endswith("]")) or (
        value.startswith("{") and value.endswith("}")
    ):
        items = value[1:-1].strip()
        if not items:
            return True, None
        if (
            re.fullmatch(
                r"\s*(['\"])[A-Za-z]+\1(?:\s*,\s*(['\"])[A-Za-z]+\2)*\s*",
                items,
            )
            is None
        ):
            return True, None
        return True, _normalize_methods(re.findall(r"['\"]([A-Za-z]+)['\"]", items))
    literal = re.fullmatch(r"(['\"])(?P<method>[A-Za-z]+)\1", value)
    return True, _normalize_methods(literal.group("method") if literal else None)


def _php_chain_string(chain: str, method: str) -> tuple[bool, str | None]:
    """Distinguish an absent fluent option from a dynamic/non-literal one."""

    match = re.search(rf"->{re.escape(method)}\s*\(\s*", chain, re.DOTALL)
    if match is None:
        return False, None
    literal = _php_quoted_literal(chain, match.end())
    if literal is None:
        return True, None
    _value, end = literal
    return True, literal[0] if chain[end:].lstrip().startswith(")") else None


def _php_chain_methods(chain: str) -> tuple[bool, tuple[str, ...] | None]:
    match = re.search(r"->methods\s*\((?P<value>[^)]*)\)", chain, re.DOTALL)
    if match is None:
        return False, None
    value = match.group("value").strip()
    if value.startswith("[") and value.endswith("]"):
        items = value[1:-1].strip()
        if (
            re.fullmatch(
                r"\s*(['\"])[A-Za-z]+\1(?:\s*,\s*(['\"])[A-Za-z]+\2)*\s*",
                items,
            )
            is None
        ):
            return True, None
        return True, _normalize_methods(re.findall(r"['\"]([A-Za-z]+)['\"]", items))
    literal = re.fullmatch(r"(['\"])(?P<method>[A-Za-z]+)\1", value)
    return True, _normalize_methods(literal.group("method") if literal else None)


def _php_chain_priority(chain: str) -> tuple[bool, int | None]:
    match = re.search(r"->priority\s*\((?P<value>[^)]*)\)", chain)
    if match is None:
        return False, None
    value = match.group("value").strip()
    return True, int(value) if re.fullmatch(r"-?\d+", value) else None


def _php_priority_argument(args: str) -> tuple[bool, int | None]:
    match = re.search(r"\bpriority\s*[:=]\s*(?P<value>[^,)]*)", args)
    if match is None:
        return False, None
    value = match.group("value").strip()
    return True, int(value) if re.fullmatch(r"-?\d+", value) else None


def _route_from_mapping(
    value: Mapping[str, object],
    *,
    key: str,
    context: _RouteContext,
    source_path: str,
    content: str,
    source_order: int,
    diagnostics: _Diagnostics,
) -> _RouteSpec | None:
    raw_path = value.get("path")
    own_name = value.get("name") if "name" in value else key
    raw_host = value.get("host") if "host" in value else context.host
    raw_condition = (
        value.get("condition") if "condition" in value else context.condition
    )
    own_methods = (
        _normalize_methods(value.get("methods")) if "methods" in value else None
    )
    methods = own_methods if "methods" in value else context.methods
    raw_priority = value.get("priority") if "priority" in value else None
    if (
        not isinstance(raw_path, str)
        or not raw_path.startswith("/")
        or context.unresolved
        or _is_computed(raw_path)
        or (own_name is not None and _is_computed(own_name))
        or (raw_host is not None and _is_computed(raw_host))
        or (raw_condition is not None and _is_computed(raw_condition))
        or ("methods" in value and own_methods is None)
        or ("priority" in value and type(raw_priority) is not int)
    ):
        diagnostics.mark(source_path)
        return None
    controller_value = value.get("controller", value.get("_controller"))
    controller = controller_value if isinstance(controller_value, str) else None
    host = raw_host if isinstance(raw_host, str) else None
    condition = raw_condition if isinstance(raw_condition, str) else None
    name = own_name if isinstance(own_name, str) else None
    name = f"{context.name_prefix}{own_name}" if own_name else None
    line = _line(content, content.find(key))
    return _RouteSpec(
        path=_join_path(context.prefix, raw_path),
        name=name,
        methods=methods,
        host=host,
        condition=condition,
        controller=controller,
        priority=context.priority + (raw_priority if raw_priority is not None else 0),
        source_path=source_path,
        source_line=line,
        structural_pointer=f"routes/{source_order}",
        source_order=source_order,
        unresolved=_is_computed(controller_value),
    )


def _import_context(
    value: Mapping[str, object],
    parent: _RouteContext,
    diagnostics: _Diagnostics,
    path: str,
) -> _RouteContext:
    prefix = value.get("prefix") if isinstance(value.get("prefix"), str) else ""
    name_prefix = value.get("name_prefix", value.get("name-prefix", ""))
    host = value.get("host") if isinstance(value.get("host"), str) else parent.host
    condition = (
        value.get("condition")
        if isinstance(value.get("condition"), str)
        else parent.condition
    )
    unresolved = parent.unresolved or any(
        not isinstance(value.get(field), str) or _is_computed(value.get(field))
        for field in ("prefix", "name_prefix", "name-prefix", "host", "condition")
        if field in value
    )
    if "methods" in value and _normalize_methods(value.get("methods")) is None:
        unresolved = True
    raw_priority = value.get("priority") if "priority" in value else 0
    if type(raw_priority) is not int:
        unresolved = True
    if unresolved:
        diagnostics.mark(path)
    return _RouteContext(
        prefix=_join_path(parent.prefix, prefix),
        name_prefix=parent.name_prefix
        + (name_prefix if isinstance(name_prefix, str) else ""),
        methods=_normalize_methods(value.get("methods")) or parent.methods,
        host=host,
        condition=condition,
        priority=parent.priority + raw_priority,
        unresolved=unresolved,
    )


def _resource_path(
    base_path: str, resource: str, diagnostics: _Diagnostics
) -> str | None:
    if resource.startswith("@") or _is_computed(resource):
        diagnostics.mark(base_path)
        return None
    parent = Path(base_path).parent
    candidate = (parent / resource).as_posix()
    safe_candidate = _workspace_path(candidate)
    if safe_candidate is None:
        diagnostics.mark(base_path)
        return None
    return safe_candidate


def _yaml_routes(
    context,
    path: str,
    route_context: _RouteContext,
    order: list[int],
    active: frozenset[str],
    diagnostics: _Diagnostics,
) -> list[_RouteSpec]:
    if path in active:
        diagnostics.mark(path)
        return []
    content = _text(context, path)
    if content is None:
        return []
    document = _safe_yaml(content, path, diagnostics)
    if document is None:
        return []
    routes: list[_RouteSpec] = []
    for key, raw_value in document.items():
        if not isinstance(key, str) or not isinstance(raw_value, Mapping):
            continue
        value = _as_mapping(raw_value)
        resource = value.get("resource")
        if isinstance(resource, str):
            child = _resource_path(path, resource, diagnostics)
            if child is not None:
                routes.extend(
                    _routes_from_path(
                        context,
                        child,
                        _import_context(value, route_context, diagnostics, path),
                        order,
                        active | {path},
                        diagnostics,
                    )
                )
            continue
        spec = _route_from_mapping(
            value,
            key=key,
            context=route_context,
            source_path=path,
            content=content,
            source_order=order[0],
            diagnostics=diagnostics,
        )
        if spec is not None:
            order[0] += 1
            routes.append(spec)
    return routes


def _xml_routes(
    context,
    path: str,
    route_context: _RouteContext,
    order: list[int],
    active: frozenset[str],
    diagnostics: _Diagnostics,
) -> list[_RouteSpec]:
    if path in active:
        diagnostics.mark(path)
        return []
    content = _text(context, path)
    if content is None:
        return []
    try:
        root = element_tree.fromstring(content)
    except element_tree.ParseError:
        diagnostics.mark(path)
        return []
    routes: list[_RouteSpec] = []
    for node in root:
        name = node.tag.rsplit("}", 1)[-1]
        if name == "import":
            resource = node.attrib.get("resource")
            if resource:
                child = _resource_path(path, resource, diagnostics)
                if child:
                    data: dict[str, object] = dict(node.attrib)
                    if "priority" in data:
                        try:
                            data["priority"] = int(str(data["priority"]))
                        except ValueError:
                            diagnostics.mark(path)
                            continue
                    routes.extend(
                        _routes_from_path(
                            context,
                            child,
                            _import_context(data, route_context, diagnostics, path),
                            order,
                            active | {path},
                            diagnostics,
                        )
                    )
            continue
        if name != "route":
            continue
        defaults = {
            child.attrib.get("key", ""): (child.text or "").strip()
            for child in node
            if child.tag.rsplit("}", 1)[-1] == "default"
        }
        requirements = {
            child.attrib.get("key", ""): (child.text or "").strip()
            for child in node
            if child.tag.rsplit("}", 1)[-1] == "requirement"
        }
        priority_text = node.attrib.get("priority", "0")
        try:
            priority = int(priority_text)
        except ValueError:
            diagnostics.mark(path)
            continue
        value: dict[str, object] = {
            "path": node.attrib.get("path"),
            "controller": node.attrib.get("controller", defaults.get("_controller")),
            "methods": node.attrib.get("methods", requirements.get("_method")),
            "host": node.attrib.get("host"),
            "condition": node.attrib.get("condition"),
            "priority": priority,
        }
        spec = _route_from_mapping(
            value,
            key=node.attrib.get("id", ""),
            context=route_context,
            source_path=path,
            content=content,
            source_order=order[0],
            diagnostics=diagnostics,
        )
        if spec:
            order[0] += 1
            routes.append(spec)
    return routes


def _php_routes(
    context,
    path: str,
    route_context: _RouteContext,
    order: list[int],
    active: frozenset[str],
    diagnostics: _Diagnostics,
) -> list[_RouteSpec]:
    if path in active:
        diagnostics.mark(path)
        return []
    content = _text(context, path)
    if content is None:
        return []
    routes: list[_RouteSpec] = []
    route_matches = tuple(_PHP_ROUTE_RE.finditer(content))
    import_matches = tuple(_PHP_IMPORT_RE.finditer(content))
    literal_operation_starts = {
        match.start() for match in (*route_matches, *import_matches)
    }
    # Retain the closed literal grammar for exact facts, but do not silently
    # skip a recognizable route-builder call whose primary argument is dynamic.
    # One path-level diagnostic is deduplicated by ``_Diagnostics``.
    for match in _PHP_ROUTE_OPERATION_START_RE.finditer(content):
        if match.start() not in literal_operation_starts:
            diagnostics.mark(path)
    operations: list[tuple[int, str, re.Match[str]]] = [
        (match.start(), "route", match) for match in route_matches
    ]
    operations.extend((match.start(), "import", match) for match in import_matches)
    for _offset, operation, match in sorted(operations, key=lambda item: item[0]):
        chain = match.group("chain")
        if operation == "import":
            resource = _resource_path(path, match.group("resource"), diagnostics)
            if resource is None:
                continue
            prefix_present, prefix_value = _php_chain_string(chain, "prefix")
            name_present, name_value = _php_chain_string(chain, "namePrefix")
            host_present, host_value = _php_chain_string(chain, "host")
            condition_present, condition_value = _php_chain_string(chain, "condition")
            methods_present, methods_value = _php_chain_methods(chain)
            priority_present, priority_value = _php_chain_priority(chain)
            prefix = prefix_value if prefix_present else ""
            name_prefix = name_value if name_present else ""
            host = host_value if host_present else route_context.host
            condition = (
                condition_value if condition_present else route_context.condition
            )
            methods = methods_value if methods_present else route_context.methods
            priority = priority_value if priority_present else 0
            unresolved = route_context.unresolved or any(
                present and (value is None or _is_computed(value))
                for present, value in (
                    (prefix_present, prefix_value),
                    (name_present, name_value),
                    (host_present, host_value),
                    (condition_present, condition_value),
                )
            )
            if methods_present and methods_value is None:
                unresolved = True
            if priority_present and priority_value is None:
                unresolved = True
            if unresolved:
                diagnostics.mark(path)
                continue
            routes.extend(
                _routes_from_path(
                    context,
                    resource,
                    _RouteContext(
                        prefix=_join_path(route_context.prefix, prefix),
                        name_prefix=route_context.name_prefix + name_prefix,
                        methods=methods,
                        host=host,
                        condition=condition,
                        priority=route_context.priority + priority,
                        unresolved=unresolved,
                    ),
                    order,
                    active | {path},
                    diagnostics,
                )
            )
            continue
        controller_match = _PHP_CONTROLLER_RE.search(chain)
        controller = controller_match.group("controller") if controller_match else None
        methods_present, methods_value = _php_chain_methods(chain)
        priority_present, priority_value = _php_chain_priority(chain)
        methods = methods_value if methods_present else route_context.methods
        route_name = match.group("name")
        route_path = match.group("path")
        if (
            route_context.unresolved
            or _is_computed(route_name)
            or _is_computed(route_path)
            or (route_context.host is not None and _is_computed(route_context.host))
            or (
                route_context.condition is not None
                and _is_computed(route_context.condition)
            )
            or (methods_present and methods_value is None)
            or (priority_present and priority_value is None)
        ):
            diagnostics.mark(path)
            continue
        routes.append(
            _RouteSpec(
                path=_join_path(route_context.prefix, route_path),
                name=route_context.name_prefix + route_name,
                methods=methods,
                host=route_context.host,
                condition=route_context.condition,
                controller=controller,
                priority=route_context.priority
                + (priority_value if priority_value is not None else 0),
                source_path=path,
                source_line=_line(content, match.start()),
                structural_pointer=f"routes/{order[0]}",
                source_order=order[0],
                unresolved=_is_computed(controller),
            )
        )
        order[0] += 1
    return routes


def _routes_from_path(
    context,
    path: str,
    route_context: _RouteContext,
    order: list[int],
    active: frozenset[str],
    diagnostics: _Diagnostics,
) -> list[_RouteSpec]:
    if path.endswith((".yaml", ".yml")):
        return _yaml_routes(context, path, route_context, order, active, diagnostics)
    if path.endswith(".xml"):
        return _xml_routes(context, path, route_context, order, active, diagnostics)
    if path.endswith(".php"):
        return _php_routes(context, path, route_context, order, active, diagnostics)
    diagnostics.mark(path)
    return []


def _route_arguments(
    args: str,
) -> tuple[
    str | None,
    str | None,
    tuple[str, ...] | None,
    str | None,
    str | None,
    int,
    bool,
]:
    path_present, named_path = _php_literal_argument(args, "path")
    positional_present, positional_path = _php_positional_path(args)
    name_present, name = _php_literal_argument(args, "name")
    methods_present, methods = _php_methods_argument(args)
    host_present, host = _php_literal_argument(args, "host")
    condition_present, condition = _php_literal_argument(args, "condition")
    priority_present, priority = _php_priority_argument(args)
    path = named_path if path_present else positional_path
    unresolved = any(
        present and (value is None or _is_computed(value))
        for present, value in (
            (path_present or positional_present, path),
            (name_present, name),
            (host_present, host),
            (condition_present, condition),
        )
    )
    unresolved = unresolved or (methods_present and methods is None)
    unresolved = unresolved or (priority_present and priority is None)
    return (
        path,
        name,
        methods,
        host,
        condition,
        priority if priority is not None else 0,
        unresolved,
    )


def _class_for_offset(
    classes: Sequence[tuple[str, str | None, int, int]], offset: int
) -> tuple[str, str | None] | None:
    for name, parent, start, end in classes:
        if start <= offset < end:
            return name, parent
    return None


def _attribute_routes(
    context,
    syntax: Sequence[SyntaxIR],
    order: list[int],
    diagnostics: _Diagnostics,
) -> list[_RouteSpec]:
    """Read supported Route attributes/annotations from already parsed PHP files."""

    class_routes: dict[str, list[_RouteSpec]] = {}
    method_routes: dict[str, list[tuple[str, _RouteSpec]]] = {}
    classes: dict[str, str | None] = {}
    all_routes: list[_RouteSpec] = []
    for item in syntax:
        content = _text(context, item.path)
        if content is None:
            continue
        matches = list(_CLASS_RE.finditer(content))
        class_ranges = [
            (
                match.group("name"),
                match.group("parent"),
                match.start(),
                matches[index + 1].start()
                if index + 1 < len(matches)
                else len(content),
            )
            for index, match in enumerate(matches)
        ]
        classes.update({name: parent for name, parent, _start, _end in class_ranges})
        annotations: list[tuple[int, str]] = [
            (match.start(), match.group("args"))
            for match in _ROUTE_ATTRIBUTE_RE.finditer(content)
        ]
        for docblock in _DOCBLOCK_RE.finditer(content):
            annotations.extend(
                (docblock.start() + nested.start(), nested.group("args"))
                for nested in _ROUTE_ANNOTATION_RE.finditer(docblock.group("body"))
            )
        for offset, args in sorted(annotations):
            declaration = _DECLARATION_RE.search(content, offset)
            if declaration is None or declaration.start() - offset > 2_000:
                continue
            path, name, methods, host, condition, priority, unresolved = (
                _route_arguments(args)
            )
            if (
                path is None
                or not path.startswith("/")
                or unresolved
                or _is_computed(path)
                or (name is not None and _is_computed(name))
                or (host is not None and _is_computed(host))
                or (condition is not None and _is_computed(condition))
            ):
                diagnostics.mark(item.path)
                continue
            locator_path = item.path
            spec = _RouteSpec(
                path=path,
                name=name,
                methods=methods,
                host=host,
                condition=condition,
                controller=None,
                priority=priority,
                source_path=locator_path,
                source_line=_line(content, offset),
                structural_pointer=f"attributes/{order[0]}",
                source_order=order[0],
            )
            order[0] += 1
            class_context = _class_for_offset(class_ranges, declaration.start())
            if declaration.group("class"):
                class_routes.setdefault(declaration.group("class"), []).append(spec)
            elif declaration.group("method") and class_context is not None:
                owner, _parent = class_context
                method_routes.setdefault(owner, []).append((
                    declaration.group("method"),
                    spec,
                ))
            else:
                all_routes.append(spec)
    for owner, routes in method_routes.items():
        for method, method_route in routes:
            own_class_routes = class_routes.get(owner, ())
            if not own_class_routes:
                all_routes.append(
                    replace(method_route, controller=f"{owner}::{method}")
                )
                continue
            for class_route in own_class_routes:
                all_routes.append(
                    replace(
                        method_route,
                        path=_join_path(class_route.path, method_route.path),
                        name=(class_route.name or "") + (method_route.name or ""),
                        methods=method_route.methods or class_route.methods,
                        host=method_route.host or class_route.host,
                        condition=method_route.condition or class_route.condition,
                        controller=f"{owner}::{method}",
                        priority=max(class_route.priority, method_route.priority),
                    )
                )
    for child, parent in classes.items():
        if not parent or not class_routes.get(child):
            continue
        parent_name = parent.rsplit("\\", 1)[-1]
        for method, inherited in method_routes.get(parent_name, ()):
            for class_route in class_routes[child]:
                all_routes.append(
                    replace(
                        inherited,
                        path=_join_path(class_route.path, inherited.path),
                        name=(class_route.name or "") + (inherited.name or ""),
                        methods=inherited.methods or class_route.methods,
                        host=inherited.host or class_route.host,
                        condition=inherited.condition or class_route.condition,
                        controller=f"{parent_name}::{method}",
                        priority=max(class_route.priority, inherited.priority),
                        # The effective registration is the child class route;
                        # using the inherited method locator for every child
                        # would collapse distinct public entries and pipeline
                        # keys into one identity.
                        source_path=class_route.source_path,
                        source_line=class_route.source_line,
                        structural_pointer=(
                            f"{class_route.structural_pointer}/inherited/"
                            f"{inherited.source_order}"
                        ),
                        source_order=class_route.source_order,
                    )
                )
    return all_routes


def _default_listener_method(event: str) -> str:
    """Symfony's omitted tag method is the invokable listener, never a guess."""

    _ = event
    return "__invoke"


def _class_body(content: str, class_name: str) -> str | None:
    """Return the one balanced body for an exact PHP class declaration."""

    pattern = re.compile(rf"\bclass\s+{re.escape(class_name)}\b")
    match = pattern.search(content)
    if match is None:
        return None
    opening = content.find("{", match.end())
    if opening < 0:
        return None
    depth = 0
    for index in range(opening, len(content)):
        character = content[index]
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return content[opening + 1 : index]
    return None


def _method_body(content: str, class_name: str, method_name: str) -> str | None:
    """Prove behaviour from the registered method in the owning class only."""

    class_body = _class_body(content, class_name)
    if class_body is None:
        return None
    method = re.compile(
        rf"\bfunction\s+{re.escape(method_name)}\s*\([^)]*\)"
        r"\s*(?::\s*[A-Za-z_\\][A-Za-z0-9_\\|? ]*)?\s*\{",
        re.MULTILINE,
    ).search(class_body)
    if method is None:
        return None
    opening = method.end() - 1
    depth = 0
    for index in range(opening, len(class_body)):
        character = class_body[index]
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return class_body[opening + 1 : index]
    return None


def _class_source_items(
    context: ExtractionContext,
    class_name: str,
    source_texts: Mapping[str, str],
) -> tuple[tuple[str, str], ...]:
    """Find only source files that declare the requested class, never a tail match."""

    normalized = class_name.lstrip("\\")
    namespace, separator, tail = normalized.rpartition("\\")

    def declares_requested_class(content: str) -> bool:
        if _class_body(content, tail) is None:
            return False
        if not separator:
            return True
        namespace_match = _NAMESPACE_RE.search(content)
        return (
            namespace_match is not None and namespace_match.group("name") == namespace
        )

    candidates = {
        path: content
        for path, content in source_texts.items()
        if declares_requested_class(content)
    }
    if normalized.startswith("App\\"):
        relative = normalized[len("App\\") :].replace("\\", "/")
        inferred_path = f"src/{relative}.php"
        inferred = _text(context, inferred_path)
        if inferred is not None and declares_requested_class(inferred):
            candidates[inferred_path] = inferred
    # A duplicate declaration cannot prove which runtime class is registered.
    return tuple(candidates.items()) if len(candidates) == 1 else ()


def _class_sources(
    context: ExtractionContext,
    class_name: str,
    source_texts: Mapping[str, str],
) -> tuple[str, ...]:
    return tuple(
        content
        for _path, content in _class_source_items(context, class_name, source_texts)
    )


def _implements_event_subscriber(content: str, class_name: str) -> bool:
    declaration = re.search(
        rf"\bclass\s+{re.escape(class_name)}\b(?P<tail>[^{{]*)\{{",
        content,
        re.DOTALL,
    )
    return bool(
        declaration
        and re.search(
            r"\b(?:[A-Za-z_\\]+\\)?EventSubscriberInterface\b",
            declaration.group("tail"),
        )
    )


def _voter_supports(
    context: ExtractionContext,
    service: str,
    bindings: Mapping[str, str],
    source_texts: Mapping[str, str],
) -> _Voter:
    class_name = bindings.get(service, service).lstrip("\\")
    class_tail = class_name.rsplit("\\", 1)[-1]
    attributes: set[str] = set()
    subject_types: set[str] = set()
    for content in _class_sources(context, class_name, source_texts):
        body = _method_body(content, class_tail, "supports")
        if body is None:
            continue
        attributes.update(
            re.findall(r"\$attribute\s*={2,3}\s*['\"]([A-Za-z0-9_.:-]+)['\"]", body)
        )
        subject_types.update(
            re.findall(r"\$subject\s+instanceof\s+([A-Za-z_\\][A-Za-z0-9_\\]*)", body)
        )
    return _Voter(service, frozenset(attributes), frozenset(subject_types))


def _service_method_matches(
    context: ExtractionContext,
    service: str,
    bindings: Mapping[str, str],
    source_texts: Mapping[str, str],
    method_name: str,
    pattern: re.Pattern[str],
) -> bool:
    class_name = bindings.get(service, service).lstrip("\\")
    class_tail = class_name.rsplit("\\", 1)[-1]
    return any(
        body is not None and bool(pattern.search(body))
        for content in _class_sources(context, class_name, source_texts)
        for body in (_method_body(content, class_tail, method_name),)
    )


def _service_facts(
    context: ExtractionContext,
    syntax: Sequence[SyntaxIR],
    diagnostics: _Diagnostics,
) -> _ServiceFacts:
    bindings: dict[str, str] = {}
    listeners: list[_Listener] = []
    voter_services: list[str] = []
    subscriber_registrations: dict[str, str] = {}
    exception_handlers: list[_Listener] = []
    order = 0
    source_texts: dict[str, str] = {}
    for item in syntax:
        content = _text(context, item.path)
        if content is not None:
            source_texts[item.path] = content
    for path in _SERVICE_FILES:
        content = _text(context, path)
        if content is None:
            continue
        if path.endswith((".yaml", ".yml")):
            document = _safe_yaml(content, path, diagnostics)
            if document is None:
                continue
            services = _as_mapping(document.get("services"))
            defaults = _as_mapping(services.get("_defaults"))
            default_autoconfigure = defaults.get("autoconfigure") is True
            for service, raw in services.items():
                if (
                    service == "_defaults"
                    or not isinstance(service, str)
                    or not isinstance(raw, Mapping)
                ):
                    continue
                data = _as_mapping(raw)
                class_name = data.get("class")
                if isinstance(class_name, str) and not _is_computed(class_name):
                    bindings[service] = class_name
                tags = data.get("tags", ())
                if isinstance(tags, str):
                    tags = (tags,)
                if not isinstance(tags, (list, tuple)):
                    tags = ()
                tag_names = {
                    name
                    for tag in tags
                    for name in (
                        tag if isinstance(tag, str) else _as_mapping(tag).get("name"),
                    )
                    if isinstance(name, str)
                }
                registered_class = bindings.get(service, service)
                if (
                    "kernel.event_subscriber" in tag_names
                    or data.get("autoconfigure", default_autoconfigure) is True
                ) and (service in bindings or "\\" in registered_class):
                    subscriber_registrations[service] = registered_class
                for tag in tags:
                    if isinstance(tag, str):
                        tag_data: Mapping[str, object] = {"name": tag}
                    else:
                        tag_data = _as_mapping(tag)
                    tag_name = tag_data.get("name")
                    if tag_name == "security.voter":
                        voter_services.append(service)
                    if tag_name != "kernel.event_listener":
                        continue
                    event = tag_data.get("event")
                    if event not in {
                        "kernel.request",
                        "kernel.response",
                        "kernel.exception",
                    }:
                        continue
                    priority = tag_data.get("priority", 0)
                    method_name = tag_data.get("method")
                    if not isinstance(method_name, str) or _is_computed(method_name):
                        method_name = _default_listener_method(str(event))
                    listener = _Listener(
                        event=str(event),
                        service=service,
                        priority=priority if isinstance(priority, int) else 0,
                        source_path=path,
                        source_order=order,
                        method_name=method_name,
                        returns_response=_service_method_matches(
                            context,
                            service,
                            bindings,
                            source_texts,
                            method_name,
                            _RESPONSE_RE,
                        ),
                    )
                    order += 1
                    if event == "kernel.exception":
                        exception_handlers.append(listener)
                    else:
                        listeners.append(listener)
        elif path.endswith(".xml"):
            try:
                root = element_tree.fromstring(content)
            except element_tree.ParseError:
                diagnostics.mark(path)
                continue
            for service_node in root.iter():
                if service_node.tag.rsplit("}", 1)[-1] != "service":
                    continue
                service = service_node.attrib.get("id")
                if not service:
                    continue
                class_name = service_node.attrib.get("class")
                if class_name and not _is_computed(class_name):
                    bindings[service] = class_name
                registered_class = bindings.get(service, service)
                if service_node.attrib.get("autoconfigure") == "true" and (
                    service in bindings or "\\" in registered_class
                ):
                    subscriber_registrations[service] = registered_class
                for tag_node in service_node:
                    if tag_node.tag.rsplit("}", 1)[-1] != "tag":
                        continue
                    tag_name = tag_node.attrib.get("name")
                    if tag_name == "security.voter":
                        voter_services.append(service)
                    if tag_name == "kernel.event_subscriber":
                        subscriber_registrations[service] = registered_class
                    event = tag_node.attrib.get("event")
                    if tag_name != "kernel.event_listener" or event not in {
                        "kernel.request",
                        "kernel.response",
                        "kernel.exception",
                    }:
                        continue
                    try:
                        priority = int(tag_node.attrib.get("priority", "0"))
                    except ValueError:
                        diagnostics.mark(path)
                        priority = 0
                    method_name = tag_node.attrib.get(
                        "method"
                    ) or _default_listener_method(event)
                    if _is_computed(method_name):
                        diagnostics.mark(path)
                        continue
                    listener = _Listener(
                        event=event,
                        service=service,
                        priority=priority,
                        source_path=path,
                        source_order=order,
                        method_name=method_name,
                        returns_response=_service_method_matches(
                            context,
                            service,
                            bindings,
                            source_texts,
                            method_name,
                            _RESPONSE_RE,
                        ),
                    )
                    order += 1
                    (
                        exception_handlers if event == "kernel.exception" else listeners
                    ).append(listener)
        else:
            for match in _PHP_SERVICE_RE.finditer(content):
                bindings[match.group("id")] = match.group("class")
            for match in _PHP_SET_CLASS_RE.finditer(content):
                service = match.group("class")
                bindings[service] = service
                if re.search(r"->autoconfigure\s*\(\s*\)", match.group("chain")):
                    subscriber_registrations[service] = service
                for tag_match in _PHP_TAG_RE.finditer(match.group("chain")):
                    tag_name = tag_match.group("name")
                    if tag_name == "security.voter":
                        voter_services.append(service)
                    if tag_name == "kernel.event_subscriber":
                        subscriber_registrations[service] = service
                    if tag_name != "kernel.event_listener":
                        continue
                    attributes = tag_match.group("attrs") or ""
                    event_match = re.search(
                        r"['\"]event['\"]\s*=>\s*['\"](?P<value>[^'\"]+)['\"]",
                        attributes,
                    )
                    if event_match is None:
                        continue
                    event = event_match.group("value")
                    if event not in {
                        "kernel.request",
                        "kernel.response",
                        "kernel.exception",
                    }:
                        continue
                    priority_match = re.search(
                        r"['\"]priority['\"]\s*=>\s*(?P<value>-?\d+)",
                        attributes,
                    )
                    priority = (
                        int(priority_match.group("value")) if priority_match else 0
                    )
                    method_match = re.search(
                        r"['\"]method['\"]\s*=>\s*['\"](?P<value>[^'\"]+)['\"]",
                        attributes,
                    )
                    method_name = (
                        method_match.group("value")
                        if method_match is not None
                        else _default_listener_method(event)
                    )
                    if _is_computed(method_name):
                        diagnostics.mark(path)
                        continue
                    listener = _Listener(
                        event=event,
                        service=service,
                        priority=priority,
                        source_path=path,
                        source_order=order,
                        method_name=method_name,
                        returns_response=_service_method_matches(
                            context,
                            service,
                            bindings,
                            source_texts,
                            method_name,
                            _RESPONSE_RE,
                        ),
                    )
                    order += 1
                    (
                        exception_handlers if event == "kernel.exception" else listeners
                    ).append(listener)
    for service, class_name in subscriber_registrations.items():
        class_tail = class_name.rsplit("\\", 1)[-1]
        for source_path, content in _class_source_items(
            context, class_name, source_texts
        ):
            if not _implements_event_subscriber(content, class_tail):
                continue
            for event_match in _SUBSCRIBED_EVENT_RE.finditer(content):
                event_name = f"kernel.{event_match.group('event').lower()}"
                priority = int(event_match.group("priority") or 0)
                listener = _Listener(
                    event=event_name,
                    service=service,
                    priority=priority,
                    source_path=source_path,
                    source_order=order,
                    method_name=event_match.group("method"),
                    returns_response=_service_method_matches(
                        context,
                        service,
                        bindings,
                        source_texts,
                        event_match.group("method"),
                        _RESPONSE_RE,
                    ),
                )
                order += 1
                (
                    exception_handlers
                    if event_name == "kernel.exception"
                    else listeners
                ).append(listener)
    ordered = lambda rows: tuple(
        sorted(
            rows,
            key=lambda item: (
                -item.priority,
                item.source_order,
                item.source_path,
                item.service,
            ),
        )
    )
    return _ServiceFacts(
        MappingProxyType(dict(bindings)),
        ordered(listeners),
        tuple(
            _voter_supports(context, service, bindings, source_texts)
            for service in dict.fromkeys(voter_services)
        ),
        ordered(exception_handlers),
    )


@dataclass(frozen=True, slots=True)
class _SecurityRule:
    pattern: str
    decision: str
    source_path: str
    source_order: int


@dataclass(frozen=True, slots=True)
class _FirewallRule:
    pattern: str | None
    enabled: bool
    uncertain: bool
    source_path: str
    source_order: int


def _security_decision(rule: Mapping[str, object]) -> str:
    """Classify only the outcome Symfony can establish without evaluating PHP."""

    allow_if = rule.get("allow_if")
    if allow_if in {"true", True}:
        return "allow"
    if allow_if in {"false", False}:
        return "deny"
    roles = rule.get("roles", rule.get("role"))
    if isinstance(roles, str):
        role_values = {roles}
    elif isinstance(roles, (list, tuple)) and all(
        isinstance(item, str) for item in roles
    ):
        role_values = set(roles)
    else:
        return "boundary"
    if role_values and role_values <= {
        "PUBLIC_ACCESS",
        "IS_AUTHENTICATED_ANONYMOUSLY",
    }:
        return "allow"
    return "boundary"


def _matching_security_rule(
    path: str,
    rules: Sequence[_SecurityRule],
    diagnostics: _Diagnostics,
) -> _SecurityRule | None:
    """Apply Symfony access controls in declared first-match order."""

    for rule in rules:
        if _is_computed(rule.pattern):
            continue
        try:
            if re.search(rule.pattern, path) is not None:
                return rule
        except re.error:
            diagnostics.mark(rule.source_path)
            continue
    return None


def _matching_firewall(
    path: str,
    rules: Sequence[_FirewallRule],
    diagnostics: _Diagnostics,
) -> tuple[bool, bool]:
    """Return (proven firewall, uncertain matcher) using first-match semantics."""

    for rule in rules:
        if rule.uncertain:
            diagnostics.mark(rule.source_path)
            return False, True
        if rule.pattern is None:
            return rule.enabled, False
        try:
            if re.search(rule.pattern, path) is not None:
                return rule.enabled, False
        except re.error:
            diagnostics.mark(rule.source_path)
            return False, True
    return False, False


def _security_for_route(
    context: ExtractionContext,
    path: str,
    diagnostics: _Diagnostics,
) -> tuple[bool, _SecurityRule | None]:
    """Read bounded YAML/XML/PHP security facts without changing their order."""

    firewall_rules: list[_FirewallRule] = []
    rules: list[_SecurityRule] = []
    access_order = 0
    firewall_order = 0
    for config_path in _SECURITY_FILES:
        content = _text(context, config_path)
        if content is None:
            continue
        if config_path.endswith((".yaml", ".yml")):
            document = _safe_yaml(content, config_path, diagnostics)
            if document is None:
                continue
            security = _as_mapping(document.get("security"))
            firewalls = _as_mapping(security.get("firewalls"))
            for _name, raw_firewall in firewalls.items():
                firewall_data = _as_mapping(raw_firewall)
                raw_pattern = firewall_data.get("pattern")
                has_custom_matcher = any(
                    field in firewall_data
                    for field in ("request_matcher", "matcher", "host", "methods")
                )
                if raw_pattern is None and not has_custom_matcher:
                    firewall_rules.append(
                        _FirewallRule(
                            None,
                            firewall_data.get("security") is not False,
                            False,
                            config_path,
                            firewall_order,
                        )
                    )
                elif isinstance(raw_pattern, str) and not _is_computed(raw_pattern):
                    firewall_rules.append(
                        _FirewallRule(
                            raw_pattern,
                            firewall_data.get("security") is not False,
                            False,
                            config_path,
                            firewall_order,
                        )
                    )
                else:
                    firewall_rules.append(
                        _FirewallRule(
                            None,
                            False,
                            True,
                            config_path,
                            firewall_order,
                        )
                    )
                firewall_order += 1
            controls = security.get("access_control", ())
            if not isinstance(controls, (tuple, list)):
                diagnostics.mark(config_path)
                continue
            for raw_rule in controls:
                rule = _as_mapping(raw_rule)
                pattern = rule.get("path")
                if not isinstance(pattern, str) or _is_computed(pattern):
                    diagnostics.mark(config_path)
                    continue
                rules.append(
                    _SecurityRule(
                        pattern,
                        _security_decision(rule),
                        config_path,
                        access_order,
                    )
                )
                access_order += 1
            continue
        if config_path.endswith(".xml"):
            try:
                root = element_tree.fromstring(content)
            except element_tree.ParseError:
                diagnostics.mark(config_path)
                continue
            for node in root.iter():
                tag = node.tag.rsplit("}", 1)[-1]
                if tag == "firewall":
                    pattern = node.attrib.get("pattern")
                    has_custom_matcher = any(
                        attribute in node.attrib
                        for attribute in ("request-matcher", "matcher", "host")
                    )
                    firewall_rules.append(
                        _FirewallRule(
                            pattern if pattern and not _is_computed(pattern) else None,
                            node.attrib.get("security") != "false",
                            has_custom_matcher
                            or (pattern is not None and _is_computed(pattern)),
                            config_path,
                            firewall_order,
                        )
                    )
                    firewall_order += 1
                if tag not in {"access-control", "access_control"}:
                    continue
                pattern = node.attrib.get("path")
                if not pattern or _is_computed(pattern):
                    diagnostics.mark(config_path)
                    continue
                roles = node.attrib.get("roles", node.attrib.get("role"))
                decision = _security_decision({
                    "roles": roles.split(",") if isinstance(roles, str) else roles,
                    "allow_if": node.attrib.get("allow-if"),
                })
                rules.append(
                    _SecurityRule(pattern, decision, config_path, access_order)
                )
                access_order += 1
            continue
        # PHP configuration is intentionally bounded to literal path + role calls.
        for match in re.finditer(
            r"->path\s*\(\s*['\"](?P<path>[^'\"]+)['\"]\s*\)"
            r"(?P<chain>[^;]{0,800})",
            content,
            re.DOTALL,
        ):
            roles_match = re.search(
                r"->roles\s*\(\s*\[(?P<roles>[^]]*)\]\s*\)",
                match.group("chain"),
            )
            roles = (
                re.findall(r"['\"]([^'\"]+)['\"]", roles_match.group("roles"))
                if roles_match is not None
                else ()
            )
            rules.append(
                _SecurityRule(
                    match.group("path"),
                    _security_decision({"roles": roles}),
                    config_path,
                    access_order,
                )
            )
            access_order += 1
        if "firewall" in content:
            # PHP builder calls outside the tiny literal grammar are not a
            # proven matcher, so they cannot create an exact firewall stage.
            firewall_rules.append(
                _FirewallRule(None, False, True, config_path, firewall_order)
            )
            firewall_order += 1
    firewall, firewall_uncertain = _matching_firewall(path, firewall_rules, diagnostics)
    rule = _matching_security_rule(path, rules, diagnostics)
    if firewall_uncertain and rule is None:
        rule = _SecurityRule("", "boundary", "config/packages/security", -1)
    return firewall, rule


def _handler_keys(
    context: ExtractionContext,
    syntax: Sequence[SyntaxIR],
) -> Mapping[str, frozenset[str]]:
    """Index method keys only when their class namespace is source-proven."""

    keys: dict[str, set[str]] = {}
    for item in syntax:
        content = _text(context, item.path)
        namespace_match = _NAMESPACE_RE.search(content) if content is not None else None
        namespace = (
            namespace_match.group("name") if namespace_match is not None else None
        )
        for index, symbol in enumerate(item.symbols):
            key = local_record_key(
                "php", item.path, "executable_declaration", "ast", f"symbols/{index}", 0
            )
            normalized_name = symbol.name.replace("::", ".")
            names = {normalized_name}
            if symbol.container:
                names.add(f"{symbol.container}.{symbol.name.rsplit('.', 1)[-1]}")
            qualified_name = next(
                (name for name in names if "." in name), normalized_name
            )
            method_name = qualified_name.rsplit(".", 1)[-1]
            class_tail = qualified_name.rsplit(".", 1)[0].rsplit(".", 1)[-1]
            if namespace is not None:
                names.add(f"{namespace}\\{class_tail}.{method_name}")
            for name in names:
                if "." in name:
                    keys.setdefault(name.casefold(), set()).add(key)
    return MappingProxyType({name: frozenset(values) for name, values in keys.items()})


def _resolve_handler(
    controller: str | None,
    bindings: Mapping[str, str],
    keys: Mapping[str, frozenset[str]],
) -> str | None:
    if controller is None or _is_computed(controller):
        return None
    value = bindings.get(controller, controller).strip().lstrip("\\")
    if "::" not in value:
        return None
    class_name, method = value.rsplit("::", 1)
    if _is_computed(class_name) or _is_computed(method):
        return None
    candidates = {f"{class_name}.{method}"}
    matched = {
        key for item in candidates for key in keys.get(item.casefold(), frozenset())
    }
    return next(iter(matched)) if len(matched) == 1 else None


def _controller_has(
    context,
    controller: str | None,
    bindings: Mapping[str, str],
    pattern: re.Pattern[str],
) -> bool:
    """Prove a terminal/error arm from a conventional static controller class."""

    if controller is None or _is_computed(controller):
        return False
    target = bindings.get(controller, controller).lstrip("\\")
    if "::" not in target:
        return False
    class_name, method_name = target.rsplit("::", 1)
    paths: tuple[str, ...]
    if class_name.startswith("App\\"):
        relative_class = class_name[len("App\\") :].replace("\\", "/")
        paths = (f"src/{relative_class}.php",)
    elif "\\" not in class_name:
        # Attributes may use the short class name inside a namespace.  The
        # conventional controller path is a proof attempt, not a fallback to
        # another class: failure simply leaves the terminal behaviour unknown.
        paths = (f"src/Controller/{class_name}.php",)
    else:
        return False
    class_tail = class_name.rsplit("\\", 1)[-1]
    return any(
        body is not None and bool(pattern.search(body))
        for source in (_text(context, path) for path in paths)
        if source is not None
        for body in (_method_body(source, class_tail, method_name),)
    )


def _controller_authorization_needs(
    context: ExtractionContext,
    controller: str | None,
    bindings: Mapping[str, str],
) -> frozenset[tuple[str, str]]:
    """Prove a literal voter attribute and subject construction in this method."""

    if controller is None or _is_computed(controller):
        return frozenset()
    target = bindings.get(controller, controller).lstrip("\\")
    if "::" not in target:
        return frozenset()
    class_name, method_name = target.rsplit("::", 1)
    if not class_name.startswith("App\\"):
        return frozenset()
    relative_class = class_name[len("App\\") :].replace("\\", "/")
    content = _text(context, f"src/{relative_class}.php")
    if content is None:
        return frozenset()
    body = _method_body(content, class_name.rsplit("\\", 1)[-1], method_name)
    if body is None:
        return frozenset()
    return frozenset(
        re.findall(
            r"(?:denyAccessUnlessGranted|isGranted)\s*\(\s*"
            r"['\"](?P<attribute>[A-Za-z0-9_.:-]+)['\"]\s*,\s*"
            r"new\s+(?P<subject>[A-Za-z_\\][A-Za-z0-9_\\]*)\s*\(",
            body,
        )
    )


def _route_locator(context, spec: _RouteSpec) -> ConfigLocatorIR:
    content = _text(context, spec.source_path) or ""
    return _config_locator(
        spec.source_path,
        content,
        spec.source_line,
        spec.structural_pointer,
        spec.source_order,
    )


def _candidate_from_spec(
    context,
    spec: _RouteSpec,
    bindings: Mapping[str, str],
    keys: Mapping[str, frozenset[str]],
) -> EntrypointCandidate:
    locator = _route_locator(context, spec)
    handler = (
        None if spec.unresolved else _resolve_handler(spec.controller, bindings, keys)
    )
    unresolved = None
    if handler is None:
        unresolved = local_record_key(
            "php",
            spec.source_path,
            "unresolved_fact",
            "config",
            f"{spec.structural_pointer}/handler",
            spec.source_order,
        )
    methods = spec.methods or ()
    evidence = IREvidence(
        EvidenceOrigin.UNRESOLVED if unresolved else EvidenceOrigin.VERIFIED_FROM_CODE,
        "symfony.routes",
        locator,
        None,
    )
    return EntrypointCandidate(
        kind=EntrypointKind.HTTP_ROUTE,
        framework="symfony",
        method_semantics=(
            MethodSemantics.EXPLICIT if methods else MethodSemantics.UNRESTRICTED
        ),
        methods=methods,
        public_path=spec.path,
        public_name=spec.name,
        trigger=TriggerKind.HTTP,
        trigger_value=f"{'|'.join(methods) if methods else 'ALL'} {spec.path}",
        match_constraints=MatchConstraints(
            spec.host,
            (),
            condition_hash(spec.condition) if spec.condition else None,
        ),
        registration_locator=locator,
        handler_local_key=handler,
        unresolved_fact_local_key=unresolved,
        framework_segment_keys=(),
        evidence=evidence,
    )


def _candidate_key(candidate: EntrypointCandidate) -> tuple[object, ...]:
    locator = candidate.registration_locator
    return (
        candidate.public_path or "",
        candidate.public_name or "",
        locator.source_location.path,
        locator.structural_pointer,
        locator.ordinal,
    )


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


class SymfonyLifecycleAdapter:
    """FrameworkAdapter implementation for statically provable Symfony facts."""

    language = "php"
    framework = "symfony"

    def __init__(self) -> None:
        # ``pipeline`` only receives normalized candidates.  Preserve immutable,
        # source-derived facts per extraction context, never source text or
        # mutable cross-project state.
        self._snapshots: Mapping[tuple[str, str, str, str], _ExtractionSnapshot] = (
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

    def _snapshot(self, context: ExtractionContext) -> _ExtractionSnapshot:
        snapshot = self._snapshots.get(self._snapshot_key(context))
        if snapshot is not None:
            return snapshot
        return _ExtractionSnapshot(
            _ServiceFacts(MappingProxyType({}), (), (), ()),
            MappingProxyType({}),
            (),
        )

    def _remember(
        self, context: ExtractionContext, snapshot: _ExtractionSnapshot
    ) -> None:
        snapshots = dict(self._snapshots)
        snapshots[self._snapshot_key(context)] = snapshot
        self._snapshots = MappingProxyType(snapshots)

    def coverage_events(self, context: ExtractionContext) -> tuple[CoverageEvent, ...]:
        return self._snapshot(context).coverage_events

    def detected_version(self, context: ExtractionContext) -> str | None:
        for record in context.detected_frameworks:
            if record.language == "php" and record.name == "symfony" and record.version:
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
                    and package.get("name") == "symfony/framework-bundle"
                ):
                    version = package.get("version")
                    if isinstance(version, str):
                        return version.lstrip("v")
            for section in ("require", "require-dev"):
                requirement = _as_mapping(document.get(section)).get(
                    "symfony/framework-bundle"
                )
                if isinstance(requirement, str):
                    match = re.search(r"(\d+\.\d+(?:\.\d+)?)", requirement)
                    if match:
                        return match.group(1)
        return None

    def detect(self, context: ExtractionContext) -> FrameworkDetection:
        detected = (
            any(
                record.language == "php" and record.name == "symfony"
                for record in context.detected_frameworks
            )
            or self.detected_version(context) is not None
        )
        return FrameworkDetection("php", "symfony", detected)

    def entrypoints(
        self, context: ExtractionContext, syntax: Sequence[SyntaxIR]
    ) -> tuple[EntrypointCandidate, ...]:
        diagnostics = _Diagnostics({})
        services = _service_facts(context, syntax, diagnostics)
        order = [0]
        routes: list[_RouteSpec] = []
        for path in _ROUTE_FILES:
            routes.extend(
                _routes_from_path(
                    context,
                    path,
                    _RouteContext(),
                    order,
                    frozenset(),
                    diagnostics,
                )
            )
        routes.extend(_attribute_routes(context, syntax, order, diagnostics))
        keys = _handler_keys(context, syntax)
        candidates: list[EntrypointCandidate] = []
        outcomes: dict[str, _ControllerOutcome] = {}
        for spec in sorted(
            routes,
            key=lambda item: (
                -item.priority,
                item.source_order,
                item.source_path,
                item.structural_pointer,
            ),
        ):
            candidate = _candidate_from_spec(context, spec, services.bindings, keys)
            if candidate.handler_local_key is not None:
                previous = outcomes.get(
                    candidate.handler_local_key,
                    _ControllerOutcome(False, False, frozenset()),
                )
                outcomes[candidate.handler_local_key] = _ControllerOutcome(
                    previous.returns_response
                    or _controller_has(
                        context, spec.controller, services.bindings, _RESPONSE_RE
                    ),
                    previous.throws
                    or _controller_has(
                        context, spec.controller, services.bindings, _THROW_RE
                    ),
                    previous.authorization_needs
                    | _controller_authorization_needs(
                        context, spec.controller, services.bindings
                    ),
                )
            # Parse security now so typed diagnostics are part of this immutable
            # extraction snapshot, rather than a side effect of display order.
            _security_for_route(context, candidate.public_path or "/", diagnostics)
            candidates.append(candidate)
        self._remember(
            context,
            _ExtractionSnapshot(
                services,
                MappingProxyType(dict(outcomes)),
                diagnostics.events(),
            ),
        )
        completed: list[EntrypointCandidate] = []
        for candidate in candidates:
            segments = self.pipeline(context, candidate)
            completed.append(
                replace(
                    candidate,
                    framework_segment_keys=tuple(item.local_key for item in segments),
                )
            )
        # ``routes`` is already ordered by Symfony route priority and declared
        # resource order.  Do not re-sort it by display fields: that would turn
        # a verified registration ordering fact into a different, guessed one.
        return tuple(completed)

    def pipeline(
        self, context: ExtractionContext, candidate: EntrypointCandidate
    ) -> tuple[FrameworkPipelineSegment, ...]:
        snapshot = self._snapshot(context)
        services = snapshot.services
        firewall, access_rule = _security_for_route(
            context, candidate.public_path or "/", _Diagnostics({})
        )
        roles: list[tuple[str, str | None, bool]] = [("router", None, False)]
        roles.extend(
            ("kernel_request_listener", listener.service, listener.returns_response)
            for listener in services.listeners
            if listener.event == "kernel.request"
        )
        if firewall:
            roles.append(("firewall", None, False))
        if access_rule is not None:
            if access_rule.decision == "allow":
                roles.append(("access_control_allow", None, False))
            elif access_rule.decision == "deny":
                roles.append(("access_control_deny", None, True))
            else:
                roles.append(("security_unresolved_boundary", None, False))
        outcome = (
            snapshot.controller_outcomes.get(candidate.handler_local_key)
            if candidate.handler_local_key is not None
            else None
        )
        handler_has_response = (
            outcome.returns_response if outcome is not None else False
        )
        handler_has_exception = outcome.throws if outcome is not None else False
        authorization_needs = (
            outcome.authorization_needs if outcome is not None else frozenset()
        )
        matching_voters = tuple(
            voter
            for voter in services.voters
            if any(
                attribute in voter.attributes and subject in voter.subject_types
                for attribute, subject in authorization_needs
            )
        )
        roles.extend(("voter", voter.service, False) for voter in matching_voters)
        if services.voters and not matching_voters:
            roles.append(("authorization_unresolved_boundary", None, False))
        roles.append(("argument_resolver", None, False))
        roles.append(("controller", None, False))
        response_listeners = tuple(
            listener
            for listener in services.listeners
            if listener.event == "kernel.response"
        )
        if response_listeners:
            roles.extend(
                ("response_listener", listener.service, listener.returns_response)
                for listener in response_listeners
            )
        else:
            # The response event dispatch itself is framework behaviour, not an
            # assertion that a user listener is registered.  Keeping this
            # boundary makes the response phase visible without inventing one.
            roles.append(("response_listener", None, False))
        if candidate.unresolved_fact_local_key is not None:
            roles.append(("unresolved_boundary", None, False))
        exception_roles: list[tuple[str, str | None, bool]] = []
        if handler_has_exception:
            exception_roles.extend(
                ("exception_listener", listener.service, listener.returns_response)
                for listener in services.exception_handlers
            )
            if not services.exception_handlers:
                exception_roles.append(("unhandled_exception", None, True))
        all_roles = roles + exception_roles
        keys = [
            _pipeline_key(candidate, role, ordinal)
            for ordinal, (role, _name, _short) in enumerate(all_roles)
        ]
        segments: list[FrameworkPipelineSegment] = []
        main_count = len(roles)
        for ordinal, (role, name, short_circuit) in enumerate(all_roles):
            locator = candidate.registration_locator
            target = (
                FrameworkLocalTarget(candidate.handler_local_key)
                if role == "controller" and candidate.handler_local_key is not None
                else FrameworkBoundaryTarget(
                    FrameworkBoundaryDescriptor(
                        "symfony", role, name, locator, candidate.evidence
                    )
                )
            )
            if ordinal < main_count:
                next_key = (
                    keys[ordinal + 1]
                    if ordinal + 1 < main_count
                    else _terminal_key(candidate, "response", ordinal)
                )
            else:
                next_key = (
                    keys[ordinal + 1]
                    if ordinal + 1 < len(keys)
                    else _terminal_key(candidate, "exception", ordinal)
                )
            shortcuts: list[Successor] = []
            if short_circuit:
                shortcuts.append(
                    ReturnSuccessor(_terminal_key(candidate, role, ordinal), 0)
                )
            if role == "controller":
                if handler_has_response:
                    shortcuts.append(
                        ReturnSuccessor(_terminal_key(candidate, role, ordinal), 0)
                    )
                if handler_has_exception and exception_roles:
                    shortcuts.append(
                        ExceptionSuccessor(
                            keys[main_count],
                            _exception_scope_key(candidate),
                            None,
                            1,
                        )
                    )
            segments.append(
                FrameworkPipelineSegment(
                    local_key=keys[ordinal],
                    framework_role=role,
                    pipeline_order=ordinal,
                    target=target,
                    success_successor=AlwaysSuccessor(next_key, ordinal),
                    short_circuit_successors=tuple(
                        sorted(
                            shortcuts,
                            key=lambda successor: (
                                successor.order,
                                successor.kind,
                            ),
                        )
                    ),
                    evidence=candidate.evidence,
                )
            )
        return tuple(segments)


__all__ = ["SymfonyLifecycleAdapter"]
