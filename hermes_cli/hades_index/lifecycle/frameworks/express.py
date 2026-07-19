"""Static, bounded Express request-lifecycle extraction.

Only registrations and handler bodies visible through ``file_accessor`` are
interpreted.  This is intentionally a source reader, never an Express runner:
computed registrations become explicit partial coverage rather than guessed
routes, methods, mounts, or continuations.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

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
    AstLocatorIR,
    CoverageCapability,
    CoverageEvent,
    CoverageOutcome,
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
from hermes_cli.hades_index.tree_sitter_adapter import SyntaxIR


_METHODS = frozenset({"get", "post", "put", "patch", "delete", "options", "head"})
_CALL_RE = re.compile(
    r"(?P<owner>[A-Za-z_$][A-Za-z0-9_$]*)\s*\.\s*(?P<method>use|all|get|post|put|patch|delete|options|head)\s*\(",
    re.MULTILINE,
)
_COMPUTED_CALL_RE = re.compile(r"(?P<owner>[A-Za-z_$][A-Za-z0-9_$]*)\s*\[.+?\]\s*\(")
_OBJECT_RE = re.compile(
    r"\b(?:const|let|var)\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*express(?:\.Router)?\s*\(",
    re.MULTILINE,
)
_FUNCTION_RE = re.compile(
    r"\b(?:async\s+)?function\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*\((?P<params>[^)]*)\)\s*\{",
    re.MULTILINE,
)
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


@dataclass(frozen=True, slots=True)
class _Function:
    name: str
    arity: int
    body: str
    local_key: str | None


@dataclass(frozen=True, slots=True)
class _Registration:
    owner: str
    method: str
    path: str | None
    handlers: tuple[str, ...]
    source_path: str
    content: str
    offset: int
    ordinal: int


@dataclass(frozen=True, slots=True)
class _Snapshot:
    registrations: tuple[_Registration, ...]
    functions: dict[str, _Function]
    apps: frozenset[str]
    objects: frozenset[str]
    coverage: tuple[CoverageEvent, ...]


def _safe_path(path: str) -> str | None:
    try:
        return normalize_source_path(path)
    except GraphContractError:
        return None


def _text(context: ExtractionContext, path: str) -> str | None:
    safe = _safe_path(path)
    if safe is None:
        return None
    try:
        return context.file_accessor(Path(safe)).decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _balanced(text: str, opening: int) -> int | None:
    """Find one balanced delimiter without evaluating JavaScript."""

    depth = 0
    left = text[opening] if opening < len(text) else ""
    right = {"(": ")", "{": "}", "[": "]"}.get(left)
    if right is None:
        return None
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
        if char in {"'", '"', "`"}:
            quote = char
        elif char == left:
            depth += 1
        elif char == right:
            depth -= 1
            if depth == 0:
                return index
    return None


def _split_arguments(text: str) -> tuple[str, ...] | None:
    values: list[str] = []
    start = 0
    stack: list[str] = []
    quote: str | None = None
    escaped = False
    pairs = {"(": ")", "[": "]", "{": "}"}
    for index, char in enumerate(text):
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {"'", '"', "`"}:
            quote = char
        elif char in pairs:
            stack.append(char)
        elif char in ")]}":
            if not stack or pairs[stack.pop()] != char:
                return None
        elif char == "," and not stack:
            values.append(text[start:index].strip())
            start = index + 1
    if quote is not None or stack:
        return None
    values.append(text[start:].strip())
    return tuple(values)


def _literal(value: str) -> str | None:
    match = re.fullmatch(
        r"\s*(['\"])(?P<value>(?:\\.|(?!\1).)*)\1\s*", value, re.DOTALL
    )
    if match is None:
        return None
    return match.group("value")


def _join(prefix: str, path: str) -> str:
    left = prefix.rstrip("/")
    right = path if path.startswith("/") else f"/{path}"
    result = f"{left}{right}" if left else right
    return result if result.startswith("/") else f"/{result}"


def _location(path: str, content: str, offset: int) -> SourceLocationIR:
    line = content.count("\n", 0, offset) + 1
    return SourceLocationIR(
        path, line, line, hashlib.sha256(content.encode()).hexdigest()
    )


def _locator(registration: _Registration) -> AstLocatorIR:
    return AstLocatorIR(
        _location(registration.source_path, registration.content, registration.offset),
        f"express/registration/{registration.method}",
        registration.ordinal,
    )


def _reason_event(reason: str, path: str | None = None) -> CoverageEvent:
    return CoverageEvent(
        "javascript",
        CoverageCapability.FRAMEWORK_LIFECYCLE,
        CoverageOutcome.PARTIAL,
        reason,
        path,
        0,
        1,
    )


def _function_facts(
    syntax: Sequence[SyntaxIR], context: ExtractionContext
) -> dict[str, _Function]:
    facts: dict[str, _Function] = {}
    for item in syntax:
        if item.language not in {"javascript", "typescript"}:
            continue
        source = _text(context, item.path)
        if source is None:
            continue
        symbol_ordinals = {
            symbol.name: ordinal
            for ordinal, symbol in enumerate(item.symbols)
            if symbol.kind == "function"
        }
        for match in _FUNCTION_RE.finditer(source):
            closing = _balanced(source, match.end() - 1)
            if closing is None:
                continue
            name = match.group("name")
            params = tuple(
                part.strip()
                for part in match.group("params").split(",")
                if part.strip()
            )
            ordinal = symbol_ordinals.get(name)
            key = (
                local_record_key(
                    "javascript",
                    item.path,
                    "executable_declaration",
                    "ast",
                    f"symbol/{name}",
                    ordinal,
                )
                if ordinal is not None
                else None
            )
            facts[name] = _Function(
                name, len(params), source[match.end() : closing], key
            )
    return facts


def _outcome(function: _Function | None) -> str | None:
    if function is None:
        return None
    body = function.body
    if re.search(r"\bnext\s*\(\s*\)", body):
        return "next"
    if re.search(r"\bnext\s*\(\s*(['\"])route\1\s*\)", body):
        return "next_route"
    if re.search(r"\bnext\s*\(\s*[^)]", body):
        return (
            "next_error"
            if not re.search(r"\bnext\s*\(\s*[A-Za-z_$][A-Za-z0-9_$]*\s*\)", body)
            else "computed_next"
        )
    if re.search(r"\bthrow\b", body) or re.search(
        r"\breturn\s+Promise\.reject\s*\(", body
    ):
        return "error"
    if re.search(r"(?<!\.)Promise\.reject\s*\(", body):
        return "detached_rejection"
    if re.search(r"\bres\s*\[.+?\]\s*\(", body):
        return "computed_terminal"
    for terminal in ("send", "json", "end", "redirect"):
        if re.search(rf"\bres\s*\.\s*{terminal}\s*\(", body):
            return terminal
    return None


def _segment_key(candidate: EntrypointCandidate, role: str, ordinal: int) -> str:
    locator = candidate.registration_locator
    return local_record_key(
        "javascript",
        locator.source_location.path,
        "framework_pipeline",
        "ast",
        f"{locator.structural_path}/pipeline/{role}",
        ordinal,
    )


def _terminal_key(candidate: EntrypointCandidate, role: str, ordinal: int) -> str:
    locator = candidate.registration_locator
    return local_record_key(
        "javascript",
        locator.source_location.path,
        "framework_terminal",
        "ast",
        f"{locator.structural_path}/terminal/{role}",
        ordinal,
    )


class ExpressLifecycleAdapter:
    """Extract only source-proven Express registration and continuation facts."""

    language = "javascript"
    framework = "express"

    def __init__(self) -> None:
        self._snapshots: dict[tuple[str, str, str, str], _Snapshot] = {}

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
            self._snapshot_key(context), _Snapshot((), {}, frozenset(), frozenset(), ())
        )

    def _remember(self, context: ExtractionContext, snapshot: _Snapshot) -> None:
        self._snapshots[self._snapshot_key(context)] = snapshot

    def detect(self, context: ExtractionContext) -> FrameworkDetection:
        detected = any(
            record.language == "javascript" and record.name == "express"
            for record in context.detected_frameworks
        )
        return FrameworkDetection(self.language, self.framework, detected)

    def coverage_events(self, context: ExtractionContext) -> tuple[CoverageEvent, ...]:
        return self._snapshot(context).coverage

    def entrypoints(
        self, context: ExtractionContext, syntax: Sequence[SyntaxIR]
    ) -> tuple[EntrypointCandidate, ...]:
        functions = _function_facts(syntax, context)
        registrations: list[_Registration] = []
        coverage: list[CoverageEvent] = []
        objects: set[str] = set()
        apps: set[str] = set()
        ordinal = 0
        for item in syntax:
            if item.language not in {"javascript", "typescript"}:
                continue
            content = _text(context, item.path)
            if content is None:
                continue
            for object_match in _OBJECT_RE.finditer(content):
                objects.add(object_match.group("name"))
                if ".Router" not in object_match.group(0):
                    apps.add(object_match.group("name"))
            for computed in _COMPUTED_CALL_RE.finditer(content):
                if computed.group("owner") == "res":
                    continue
                if computed.group("owner") in objects:
                    coverage.append(_reason_event("route_method_unresolved", item.path))
                else:
                    coverage.extend(
                        _reason_event(reason, item.path)
                        for reason in (
                            "registration_target_unresolved",
                            "route_method_unresolved",
                            "route_path_unresolved",
                            "handler_target_unresolved",
                        )
                    )
            for match in _CALL_RE.finditer(content):
                owner, method = match.group("owner"), match.group("method")
                close = _balanced(content, match.end() - 1)
                if close is None:
                    coverage.append(
                        _reason_event("handler_target_unresolved", item.path)
                    )
                    continue
                parts = _split_arguments(content[match.end() : close])
                if parts is None:
                    coverage.append(
                        _reason_event("handler_target_unresolved", item.path)
                    )
                    continue
                if owner not in objects:
                    coverage.extend(
                        _reason_event(reason, item.path)
                        for reason in (
                            "registration_target_unresolved",
                            "route_method_unresolved",
                            "route_path_unresolved",
                            "handler_target_unresolved",
                        )
                    )
                    continue
                path: str | None
                handlers: tuple[str, ...]
                if method == "use":
                    if parts and _literal(parts[0]) is not None:
                        path, handlers = _literal(parts[0]), tuple(parts[1:])
                    else:
                        path, handlers = "", tuple(parts)
                        if len(parts) >= 2:
                            coverage.extend(
                                _reason_event(reason, item.path)
                                for reason in (
                                    "mount_prefix_unresolved",
                                    "router_target_unresolved",
                                )
                            )
                else:
                    path = _literal(parts[0]) if parts else None
                    handlers = tuple(parts[1:])
                    if path is None:
                        coverage.append(
                            _reason_event("route_path_unresolved", item.path)
                        )
                        continue
                if any(
                    part.startswith("...") or not _IDENTIFIER_RE.fullmatch(part)
                    for part in handlers
                ):
                    coverage.append(
                        _reason_event("handler_target_unresolved", item.path)
                    )
                    coverage.append(
                        _reason_event("middleware_order_unresolved", item.path)
                    )
                registrations.append(
                    _Registration(
                        owner,
                        method,
                        path,
                        handlers,
                        item.path,
                        content,
                        match.start(),
                        ordinal,
                    )
                )
                ordinal += 1
        for function in functions.values():
            outcome = _outcome(function)
            if outcome == "computed_next":
                coverage.extend(
                    _reason_event(reason)
                    for reason in (
                        "continuation_kind_unresolved",
                        "error_flow_unresolved",
                    )
                )
            elif outcome == "computed_terminal":
                coverage.append(_reason_event("response_outcome_unresolved"))
            elif outcome == "detached_rejection":
                coverage.append(_reason_event("async_error_flow_unresolved"))
        snapshot = _Snapshot(
            tuple(registrations),
            functions,
            frozenset(apps),
            frozenset(objects),
            tuple(
                sorted(
                    set(coverage),
                    key=lambda event: (event.reason_code or "", event.path or ""),
                )
            ),
        )
        self._remember(context, snapshot)

        routes = self._resolved_routes(snapshot, coverage)
        candidates: list[EntrypointCandidate] = []
        for registration, path in routes:
            handler = next(
                (value for value in registration.handlers if value in functions), None
            )
            locator = _locator(registration)
            unresolved = None
            if handler is None or functions[handler].local_key is None:
                unresolved = local_record_key(
                    "javascript",
                    registration.source_path,
                    "unresolved_fact",
                    "ast",
                    f"{locator.structural_path}/handler",
                    registration.ordinal,
                )
            evidence = IREvidence(
                EvidenceOrigin.VERIFIED_FROM_CODE
                if unresolved is None
                else EvidenceOrigin.UNRESOLVED,
                "express.lifecycle",
                locator,
                None,
            )
            methods = (
                () if registration.method == "all" else (registration.method.upper(),)
            )
            candidates.append(
                EntrypointCandidate(
                    EntrypointKind.HTTP_ROUTE,
                    "express",
                    MethodSemantics.UNRESTRICTED
                    if registration.method == "all"
                    else MethodSemantics.EXPLICIT,
                    methods,
                    path,
                    handler,
                    TriggerKind.HTTP,
                    f"{'ALL' if not methods else methods[0]} {path}",
                    MatchConstraints(None, (), None),
                    locator,
                    functions[handler].local_key if unresolved is None else None,
                    unresolved,
                    (),
                    evidence,
                )
            )
        snapshot = _Snapshot(
            snapshot.registrations,
            snapshot.functions,
            snapshot.apps,
            snapshot.objects,
            tuple(
                sorted(
                    set(coverage),
                    key=lambda event: (event.reason_code or "", event.path or ""),
                )
            ),
        )
        self._remember(context, snapshot)
        return tuple(
            replace(
                candidate,
                framework_segment_keys=tuple(
                    segment.local_key for segment in self.pipeline(context, candidate)
                ),
            )
            for candidate in candidates
        )

    def _resolved_routes(
        self, snapshot: _Snapshot, coverage: list[CoverageEvent]
    ) -> list[tuple[_Registration, str]]:
        by_owner: dict[str, list[_Registration]] = {}
        for registration in snapshot.registrations:
            by_owner.setdefault(registration.owner, []).append(registration)
        routes: list[tuple[_Registration, str]] = []

        def visit(owner: str, prefix: str, seen: frozenset[str]) -> None:
            if owner in seen:
                coverage.append(_reason_event("router_target_unresolved"))
                return
            for registration in by_owner.get(owner, ()):
                if registration.method in _METHODS or registration.method == "all":
                    routes.append((
                        registration,
                        _join(prefix, registration.path or ""),
                    ))
                elif registration.method == "use":
                    if registration.path is None:
                        coverage.append(
                            _reason_event(
                                "mount_prefix_unresolved", registration.source_path
                            )
                        )
                        continue
                    children = [
                        handler
                        for handler in registration.handlers
                        if handler in snapshot.objects
                    ]
                    if children:
                        for child in children:
                            visit(
                                child, _join(prefix, registration.path), seen | {owner}
                            )
                    elif registration.handlers and (
                        "(" in registration.handlers[0]
                        or registration.handlers[0].startswith("...")
                    ):
                        coverage.append(
                            _reason_event(
                                "router_target_unresolved", registration.source_path
                            )
                        )
                    elif (
                        registration.handlers
                        and registration.handlers[0] not in snapshot.functions
                    ):
                        coverage.extend((
                            _reason_event(
                                "error_middleware_arity_unresolved",
                                registration.source_path,
                            ),
                            _reason_event(
                                "handler_target_unresolved", registration.source_path
                            ),
                        ))

        for app in sorted(snapshot.apps):
            visit(app, "", frozenset())
        return routes

    def pipeline(
        self, context: ExtractionContext, candidate: EntrypointCandidate
    ) -> tuple[FrameworkPipelineSegment, ...]:
        snapshot = self._snapshot(context)
        registration = next(
            (
                item
                for item in snapshot.registrations
                if _locator(item) == candidate.registration_locator
            ),
            None,
        )
        if registration is None:
            return ()
        path = candidate.public_path or ""
        items: list[tuple[str, str | None]] = []
        route_error = False
        for middleware in snapshot.registrations:
            if middleware.method != "use" or middleware.ordinal >= registration.ordinal:
                continue
            if middleware.owner not in snapshot.apps or (
                middleware.path and not path.startswith(middleware.path)
            ):
                continue
            for handler in middleware.handlers:
                function = snapshot.functions.get(handler)
                if function is not None and function.arity == 4:
                    continue
                items.append(("middleware", handler))
        active_routes = [registration]
        for active in active_routes:
            for handler in active.handlers:
                items.append(("route_handler", handler))
                outcome = _outcome(snapshot.functions.get(handler))
                route_error = route_error or outcome in {"error", "next_error"}
                if outcome in {"next", "next_route", "next_error"}:
                    items.append((f"continuation_{outcome}", handler))
                elif outcome in {"send", "json", "end", "redirect"}:
                    items.append((f"terminal_{outcome}", handler))
                if outcome == "next_route":
                    next_route = next(
                        (
                            item
                            for item in snapshot.registrations
                            if item.owner == active.owner
                            and item.path == active.path
                            and (item.method == active.method or item.method == "all")
                            and item.ordinal > active.ordinal
                        ),
                        None,
                    )
                    if next_route is not None:
                        active_routes.append(next_route)
                    break
        if route_error:
            items = [item for item in items if item[0] != "middleware"]
            for middleware in snapshot.registrations:
                if (
                    middleware.method != "use"
                    or middleware.ordinal <= registration.ordinal
                ):
                    continue
                for handler in middleware.handlers:
                    function = snapshot.functions.get(handler)
                    if function is not None and function.arity == 4:
                        items.append(("error_middleware", handler))
                        terminal = _outcome(function)
                        if terminal in {"send", "json", "end", "redirect"}:
                            items.append((f"terminal_{terminal}", handler))
        keys = [
            _segment_key(candidate, role, ordinal)
            for ordinal, (role, _handler) in enumerate(items)
        ]
        segments: list[FrameworkPipelineSegment] = []
        for ordinal, ((role, handler), key) in enumerate(zip(items, keys, strict=True)):
            function = snapshot.functions.get(handler or "")
            target = (
                FrameworkLocalTarget(function.local_key)
                if function is not None and function.local_key is not None
                else FrameworkBoundaryTarget(
                    FrameworkBoundaryDescriptor(
                        "express",
                        role,
                        handler,
                        candidate.registration_locator,
                        candidate.evidence,
                    )
                )
            )
            successor = (
                AlwaysSuccessor(keys[ordinal + 1], ordinal)
                if ordinal + 1 < len(keys)
                else ReturnSuccessor(
                    _terminal_key(candidate, "response", ordinal), ordinal
                )
            )
            segments.append(
                FrameworkPipelineSegment(
                    key, role, ordinal, target, successor, (), candidate.evidence
                )
            )
        return tuple(segments)


__all__ = ["ExpressLifecycleAdapter"]
