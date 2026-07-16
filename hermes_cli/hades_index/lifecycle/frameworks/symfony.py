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
from typing import Any

try:  # PyYAML is already a project dependency; retain a safe failure mode.
    import yaml
except ImportError:  # pragma: no cover - exercised by integration environments.
    yaml = None  # type: ignore[assignment]

from hermes_cli.hades_graph_v2.identity import condition_hash
from hermes_cli.hades_graph_v2.model import (
    EdgeFlow,
    EntrypointKind,
    EvidenceOrigin,
    MethodSemantics,
    TriggerKind,
)
from hermes_cli.hades_index.lifecycle.frameworks import FrameworkDetection
from hermes_cli.hades_index.lifecycle.model import (
    AlwaysSuccessor,
    ConfigLocatorIR,
    EntrypointCandidate,
    ExtractionContext,
    FrameworkBoundaryDescriptor,
    FrameworkBoundaryTarget,
    FrameworkLocalTarget,
    FrameworkPipelineSegment,
    IREvidence,
    MatchConstraints,
    ReturnSuccessor,
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
    returns_response: bool


@dataclass(frozen=True, slots=True)
class _ServiceFacts:
    bindings: Mapping[str, str]
    listeners: tuple[_Listener, ...]
    voters: tuple[str, ...]
    exception_handlers: tuple[_Listener, ...]


def _text(context, path: str) -> str | None:
    """Read a known source-relative path with no filesystem fallback."""

    try:
        return context.file_accessor(Path(path)).decode("utf-8")
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
        values = re.findall(r"[A-Za-z]+", value)
    elif isinstance(value, (list, tuple)):
        values = [item for item in value if isinstance(item, str)]
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


def _safe_yaml(content: str) -> Mapping[str, object]:
    if yaml is None:
        return {}
    try:
        loaded = yaml.safe_load(content)
    except yaml.YAMLError:
        return {}
    return _as_mapping(loaded)


def _is_computed(value: object) -> bool:
    return not isinstance(value, str) or bool(_COMPUTED_RE.search(value))


def _php_string_argument(args: str, name: str) -> str | None:
    pattern = re.compile(
        rf"\b{re.escape(name)}\s*[:=]\s*(['\"])(?P<value>.*?)\1", re.DOTALL
    )
    match = pattern.search(args)
    return match.group("value") if match else None


def _php_positional_path(args: str) -> str | None:
    match = re.match(r"\s*(['\"])(?P<value>.*?)\1", args, re.DOTALL)
    return match.group("value") if match else None


def _php_methods_argument(args: str) -> tuple[str, ...] | None:
    match = re.search(r"\bmethods\s*[:=]\s*\[(?P<items>[^]]*)\]", args, re.DOTALL)
    if match:
        return _normalize_methods(
            re.findall(r"['\"]([A-Za-z]+)['\"]", match.group("items"))
        )
    single = _php_string_argument(args, "methods")
    return _normalize_methods(single)


def _php_chain_string(chain: str, method: str) -> str | None:
    match = re.search(
        rf"->{re.escape(method)}\s*\(\s*(['\"])(?P<value>.*?)\1\s*\)",
        chain,
        re.DOTALL,
    )
    return match.group("value") if match else None


def _php_priority_argument(args: str) -> int:
    match = re.search(r"\bpriority\s*[:=]\s*(?P<value>-?\d+)", args)
    return int(match.group("value")) if match else 0


def _route_from_mapping(
    value: Mapping[str, object],
    *,
    key: str,
    context: _RouteContext,
    source_path: str,
    content: str,
    source_order: int,
) -> _RouteSpec | None:
    raw_path = value.get("path")
    if not isinstance(raw_path, str) or not raw_path.startswith("/"):
        return None
    controller_value = value.get("controller", value.get("_controller"))
    controller = controller_value if isinstance(controller_value, str) else None
    own_methods = _normalize_methods(value.get("methods"))
    own_name = value.get("name") if isinstance(value.get("name"), str) else key
    host = value.get("host") if isinstance(value.get("host"), str) else context.host
    condition = (
        value.get("condition")
        if isinstance(value.get("condition"), str)
        else context.condition
    )
    name = f"{context.name_prefix}{own_name}" if own_name else None
    line = _line(content, content.find(key))
    return _RouteSpec(
        path=_join_path(context.prefix, raw_path),
        name=name,
        methods=own_methods if own_methods is not None else context.methods,
        host=host,
        condition=condition,
        controller=controller,
        priority=int(value.get("priority", 0))
        if isinstance(value.get("priority", 0), int)
        else 0,
        source_path=source_path,
        source_line=line,
        structural_pointer=f"routes/{source_order}",
        source_order=source_order,
        unresolved=_is_computed(controller_value),
    )


def _import_context(
    value: Mapping[str, object], parent: _RouteContext
) -> _RouteContext:
    prefix = value.get("prefix") if isinstance(value.get("prefix"), str) else ""
    name_prefix = value.get("name_prefix", value.get("name-prefix", ""))
    host = value.get("host") if isinstance(value.get("host"), str) else parent.host
    condition = (
        value.get("condition")
        if isinstance(value.get("condition"), str)
        else parent.condition
    )
    return _RouteContext(
        prefix=_join_path(parent.prefix, prefix),
        name_prefix=parent.name_prefix
        + (name_prefix if isinstance(name_prefix, str) else ""),
        methods=_normalize_methods(value.get("methods")) or parent.methods,
        host=host,
        condition=condition,
    )


def _resource_path(base_path: str, resource: str) -> str | None:
    if resource.startswith(("@", "%")) or _is_computed(resource):
        return None
    parent = Path(base_path).parent
    candidate = (parent / resource).as_posix()
    if candidate.startswith("../") or "/../" in candidate:
        return None
    return candidate


def _yaml_routes(
    context,
    path: str,
    route_context: _RouteContext,
    order: list[int],
    seen: set[str],
) -> list[_RouteSpec]:
    if path in seen:
        return []
    seen.add(path)
    content = _text(context, path)
    if content is None:
        return []
    document = _safe_yaml(content)
    routes: list[_RouteSpec] = []
    for key, raw_value in document.items():
        if not isinstance(key, str) or not isinstance(raw_value, Mapping):
            continue
        value = _as_mapping(raw_value)
        resource = value.get("resource")
        if isinstance(resource, str):
            child = _resource_path(path, resource)
            if child is not None:
                routes.extend(
                    _routes_from_path(
                        context,
                        child,
                        _import_context(value, route_context),
                        order,
                        seen,
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
    seen: set[str],
) -> list[_RouteSpec]:
    if path in seen:
        return []
    seen.add(path)
    content = _text(context, path)
    if content is None:
        return []
    try:
        root = element_tree.fromstring(content)
    except element_tree.ParseError:
        return []
    routes: list[_RouteSpec] = []
    for node in root:
        name = node.tag.rsplit("}", 1)[-1]
        if name == "import":
            resource = node.attrib.get("resource")
            if resource:
                child = _resource_path(path, resource)
                if child:
                    data: dict[str, object] = dict(node.attrib)
                    routes.extend(
                        _routes_from_path(
                            context,
                            child,
                            _import_context(data, route_context),
                            order,
                            seen,
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
        value: dict[str, object] = {
            "path": node.attrib.get("path"),
            "controller": node.attrib.get("controller", defaults.get("_controller")),
            "methods": node.attrib.get("methods", requirements.get("_method")),
            "host": node.attrib.get("host"),
            "condition": node.attrib.get("condition"),
            "priority": int(node.attrib.get("priority", "0")),
        }
        spec = _route_from_mapping(
            value,
            key=node.attrib.get("id", ""),
            context=route_context,
            source_path=path,
            content=content,
            source_order=order[0],
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
    seen: set[str],
) -> list[_RouteSpec]:
    if path in seen:
        return []
    seen.add(path)
    content = _text(context, path)
    if content is None:
        return []
    routes: list[_RouteSpec] = []
    operations: list[tuple[int, str, re.Match[str]]] = [
        (match.start(), "route", match) for match in _PHP_ROUTE_RE.finditer(content)
    ]
    operations.extend(
        (match.start(), "import", match) for match in _PHP_IMPORT_RE.finditer(content)
    )
    for _offset, operation, match in sorted(operations, key=lambda item: item[0]):
        chain = match.group("chain")
        if operation == "import":
            resource = _resource_path(path, match.group("resource"))
            if resource is None:
                continue
            prefix = _php_chain_string(chain, "prefix") or ""
            name_prefix = _php_chain_string(chain, "namePrefix") or ""
            host = _php_chain_string(chain, "host") or route_context.host
            condition = _php_chain_string(chain, "condition") or route_context.condition
            methods_match = _PHP_METHODS_RE.search(chain)
            methods = (
                _normalize_methods(
                    re.findall(r"['\"]([A-Za-z]+)['\"]", methods_match.group("items"))
                )
                if methods_match
                else route_context.methods
            )
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
                    ),
                    order,
                    seen,
                )
            )
            continue
        controller_match = _PHP_CONTROLLER_RE.search(chain)
        methods_match = _PHP_METHODS_RE.search(chain)
        controller = controller_match.group("controller") if controller_match else None
        methods = (
            _normalize_methods(
                re.findall(r"['\"]([A-Za-z]+)['\"]", methods_match.group("items"))
            )
            if methods_match
            else route_context.methods
        )
        routes.append(
            _RouteSpec(
                path=_join_path(route_context.prefix, match.group("path")),
                name=route_context.name_prefix + match.group("name"),
                methods=methods,
                host=route_context.host,
                condition=route_context.condition,
                controller=controller,
                priority=0,
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
    seen: set[str],
) -> list[_RouteSpec]:
    if path.endswith((".yaml", ".yml")):
        return _yaml_routes(context, path, route_context, order, seen)
    if path.endswith(".xml"):
        return _xml_routes(context, path, route_context, order, seen)
    if path.endswith(".php"):
        return _php_routes(context, path, route_context, order, seen)
    return []


def _route_arguments(
    args: str,
) -> tuple[str | None, str | None, tuple[str, ...] | None, str | None, str | None, int]:
    return (
        _php_string_argument(args, "path") or _php_positional_path(args),
        _php_string_argument(args, "name"),
        _php_methods_argument(args),
        _php_string_argument(args, "host"),
        _php_string_argument(args, "condition"),
        _php_priority_argument(args),
    )


def _class_for_offset(
    classes: Sequence[tuple[str, str | None, int, int]], offset: int
) -> tuple[str, str | None] | None:
    for name, parent, start, end in classes:
        if start <= offset < end:
            return name, parent
    return None


def _attribute_routes(
    context, syntax: Sequence[SyntaxIR], order: list[int]
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
            path, name, methods, host, condition, priority = _route_arguments(args)
            if path is None or not path.startswith("/"):
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


def _service_facts(context, syntax: Sequence[SyntaxIR]) -> _ServiceFacts:
    bindings: dict[str, str] = {}
    listeners: list[_Listener] = []
    voters: list[str] = []
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
            services = _as_mapping(_safe_yaml(content).get("services"))
            for service, raw in services.items():
                if not isinstance(service, str) or not isinstance(raw, Mapping):
                    continue
                data = _as_mapping(raw)
                class_name = data.get("class")
                if isinstance(class_name, str) and not _is_computed(class_name):
                    bindings[service] = class_name
                tags = data.get("tags", ())
                if isinstance(tags, str):
                    tags = (tags,)
                if not isinstance(tags, (list, tuple)):
                    continue
                for tag in tags:
                    if isinstance(tag, str):
                        tag_data: Mapping[str, object] = {"name": tag}
                    else:
                        tag_data = _as_mapping(tag)
                    tag_name = tag_data.get("name")
                    if tag_name == "security.voter":
                        voters.append(service)
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
                    listener = _Listener(
                        event=str(event),
                        service=service,
                        priority=priority if isinstance(priority, int) else 0,
                        source_path=path,
                        source_order=order,
                        returns_response=_service_returns_response(
                            service, bindings, source_texts, context
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
                for tag_node in service_node:
                    if tag_node.tag.rsplit("}", 1)[-1] != "tag":
                        continue
                    tag_name = tag_node.attrib.get("name")
                    if tag_name == "security.voter":
                        voters.append(service)
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
                        priority = 0
                    listener = _Listener(
                        event=event,
                        service=service,
                        priority=priority,
                        source_path=path,
                        source_order=order,
                        returns_response=_service_returns_response(
                            service, bindings, source_texts, context
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
                for tag_match in _PHP_TAG_RE.finditer(match.group("chain")):
                    tag_name = tag_match.group("name")
                    if tag_name == "security.voter":
                        voters.append(service)
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
                    listener = _Listener(
                        event=event,
                        service=service,
                        priority=priority,
                        source_path=path,
                        source_order=order,
                        returns_response=_service_returns_response(
                            service, bindings, source_texts, context
                        ),
                    )
                    order += 1
                    (
                        exception_handlers if event == "kernel.exception" else listeners
                    ).append(listener)
    for path, content in source_texts.items():
        class_match = _CLASS_RE.search(content)
        if class_match is None or "getSubscribedEvents" not in content:
            continue
        service = class_match.group("name")
        for event_match in _SUBSCRIBED_EVENT_RE.finditer(content):
            event_name = f"kernel.{event_match.group('event').lower()}"
            priority = int(event_match.group("priority") or 0)
            listener = _Listener(
                event=event_name,
                service=service,
                priority=priority,
                source_path=path,
                source_order=order,
                returns_response=bool(_RESPONSE_RE.search(content)),
            )
            order += 1
            (
                exception_handlers if event_name == "kernel.exception" else listeners
            ).append(listener)
    ordered = lambda rows: tuple(
        sorted(
            rows,
            key=lambda item: (
                -item.priority,
                item.service,
                item.source_path,
                item.source_order,
            ),
        )
    )
    return _ServiceFacts(
        bindings,
        ordered(listeners),
        tuple(sorted(set(voters))),
        ordered(exception_handlers),
    )


def _service_returns_response(
    service: str,
    bindings: Mapping[str, str],
    source_texts: Mapping[str, str],
    context,
) -> bool:
    class_tail = bindings.get(service, service).rsplit("\\", 1)[-1]
    texts = list(source_texts.values())
    class_name = bindings.get(service, service).lstrip("\\")
    if class_name.startswith("App\\"):
        relative_class = class_name[len("App\\") :].replace("\\", "/")
        inferred_path = f"src/{relative_class}.php"
        inferred = _text(context, inferred_path)
        if inferred is not None:
            texts.append(inferred)
    return any(
        class_tail in content and bool(_RESPONSE_RE.search(content))
        for content in texts
    )


def _security_for_route(context, path: str) -> tuple[bool, bool]:
    """Return only statically applicable firewall/access-control facts."""

    firewall = False
    access = False
    for config_path in _SECURITY_FILES:
        content = _text(context, config_path)
        if content is None or not config_path.endswith((".yaml", ".yml")):
            continue
        security = _as_mapping(_safe_yaml(content).get("security"))
        firewall = firewall or bool(_as_mapping(security.get("firewalls")))
        controls = security.get("access_control", ())
        if not isinstance(controls, (tuple, list)):
            continue
        for rule in controls:
            pattern = _as_mapping(rule).get("path")
            if not isinstance(pattern, str) or _is_computed(pattern):
                continue
            if pattern.startswith("^") and path.startswith(pattern[1:]):
                access = True
            elif pattern == path:
                access = True
    return firewall, access


def _handler_keys(syntax: Sequence[SyntaxIR]) -> dict[str, str]:
    keys: dict[str, str] = {}
    for item in syntax:
        for index, symbol in enumerate(item.symbols):
            key = local_record_key(
                "php", item.path, "executable_declaration", "ast", f"symbols/{index}", 0
            )
            names = {symbol.name, symbol.name.replace("::", ".")}
            if symbol.container:
                names.add(f"{symbol.container}.{symbol.name.rsplit('.', 1)[-1]}")
            for name in names:
                keys[name.casefold()] = key
                keys[name.rsplit(".", 1)[-1].casefold()] = key
    return keys


def _resolve_handler(
    controller: str | None, bindings: Mapping[str, str], keys: Mapping[str, str]
) -> str | None:
    if controller is None or _is_computed(controller):
        return None
    value = bindings.get(controller, controller).strip().lstrip("\\")
    if "::" not in value:
        return None
    class_name, method = value.rsplit("::", 1)
    if _is_computed(class_name) or _is_computed(method):
        return None
    class_tail = class_name.rsplit("\\", 1)[-1]
    candidates = (
        f"{class_tail}.{method}",
        f"{class_name}.{method}",
        method,
    )
    matched = {keys[item.casefold()] for item in candidates if item.casefold() in keys}
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
    class_name, _method = target.rsplit("::", 1)
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
    return any(
        source is not None and bool(pattern.search(source))
        for source in (_text(context, path) for path in paths)
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
    context, spec: _RouteSpec, bindings: Mapping[str, str], keys: Mapping[str, str]
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


class SymfonyLifecycleAdapter:
    """FrameworkAdapter implementation for statically provable Symfony facts."""

    language = "php"
    framework = "symfony"

    def __init__(self) -> None:
        # ``pipeline`` receives only a normalized candidate, so retain the two
        # facts already proven while extracting this context.  The cache is
        # reset per extraction and never contains source text or guesses.
        self._response_handler_keys: set[str] = set()
        self._exception_handler_keys: set[str] = set()

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
        services = _service_facts(context, syntax)
        order = [0]
        routes: list[_RouteSpec] = []
        seen: set[str] = set()
        for path in _ROUTE_FILES:
            routes.extend(
                _routes_from_path(context, path, _RouteContext(), order, seen)
            )
        routes.extend(_attribute_routes(context, syntax, order))
        keys = _handler_keys(syntax)
        self._response_handler_keys.clear()
        self._exception_handler_keys.clear()
        candidates: list[EntrypointCandidate] = []
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
                if _controller_has(
                    context, spec.controller, services.bindings, _RESPONSE_RE
                ):
                    self._response_handler_keys.add(candidate.handler_local_key)
                if _controller_has(
                    context, spec.controller, services.bindings, _THROW_RE
                ):
                    self._exception_handler_keys.add(candidate.handler_local_key)
            candidates.append(candidate)
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
        services = _service_facts(context, ())
        firewall, access_control = _security_for_route(
            context, candidate.public_path or "/"
        )
        roles: list[tuple[str, str | None, bool]] = [("router", None, False)]
        roles.extend(
            ("kernel_request_listener", listener.service, listener.returns_response)
            for listener in services.listeners
            if listener.event == "kernel.request"
        )
        if firewall:
            roles.append(("firewall", None, False))
        if access_control:
            roles.append(("access_control", None, True))
        roles.extend(("voter", voter, False) for voter in services.voters)
        roles.append(("argument_resolver", None, False))
        handler_has_response = (
            candidate.handler_local_key is not None
            and candidate.handler_local_key in self._response_handler_keys
        )
        handler_has_exception = (
            candidate.handler_local_key is not None
            and candidate.handler_local_key in self._exception_handler_keys
        )
        roles.append(("controller", None, handler_has_response))
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
        roles.extend(
            ("exception_listener", listener.service, listener.returns_response)
            for listener in services.exception_handlers
        )
        if handler_has_exception and not services.exception_handlers:
            roles.append(("unhandled_exception", None, True))
        if candidate.unresolved_fact_local_key is not None:
            roles.append(("unresolved_boundary", None, False))
        keys = [
            _pipeline_key(candidate, role, ordinal)
            for ordinal, (role, _name, _short) in enumerate(roles)
        ]
        segments: list[FrameworkPipelineSegment] = []
        for ordinal, (role, name, short_circuit) in enumerate(roles):
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
            next_key = (
                keys[ordinal + 1]
                if ordinal + 1 < len(keys)
                else _terminal_key(candidate, "response", ordinal)
            )
            shortcuts: tuple[ReturnSuccessor, ...] = ()
            if short_circuit or role in {"access_control", "exception_listener"}:
                shortcuts = (
                    ReturnSuccessor(_terminal_key(candidate, role, ordinal), 0),
                )
            segments.append(
                FrameworkPipelineSegment(
                    local_key=keys[ordinal],
                    framework_role=role,
                    pipeline_order=ordinal,
                    target=target,
                    success_successor=AlwaysSuccessor(next_key, ordinal),
                    short_circuit_successors=shortcuts,
                    evidence=candidate.evidence,
                )
            )
        return tuple(segments)


__all__ = ["SymfonyLifecycleAdapter"]
