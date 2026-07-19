"""Static, source-scoped Express request lifecycle extraction."""

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
from hermes_cli.hades_index.lifecycle.frameworks import (
    FrameworkDetection,
    FrameworkPipelineFacts,
    FrameworkTerminalSpec,
    framework_pipeline_facts,
)
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
    TerminalKind,
    local_record_key,
)
from hermes_cli.hades_index.tree_sitter_adapter import SyntaxIR


_METHODS = frozenset({"get", "post", "put", "patch", "delete", "options", "head"})
_CALL = re.compile(
    r"(?P<owner>[A-Za-z_$][\w$]*)\s*\.\s*(?P<method>use|all|get|post|put|patch|delete|options|head)\s*\("
)
_COMPUTED = re.compile(r"(?P<owner>[A-Za-z_$][\w$]*)\s*\[.+?\]\s*\(")
_OBJECT = re.compile(
    r"\b(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*express(?:\.Router)?\s*\("
)
_FUNCTION = re.compile(
    r"\b(?:async\s+)?function\s+(?P<name>[A-Za-z_$][\w$]*)\s*\((?P<params>[^)]*)\)\s*\{"
)
_IDENT = re.compile(r"^[A-Za-z_$][\w$]*$")


@dataclass(frozen=True, slots=True)
class _Function:
    key: str
    name: str
    arity: int
    code: str
    local_key: str | None


@dataclass(frozen=True, slots=True)
class _Registration:
    owner: str
    method: str
    path: str | None
    handlers: tuple[str, ...]
    source_path: str
    source: str
    offset: int
    ordinal: int


@dataclass(frozen=True, slots=True)
class _Route:
    registration: _Registration
    path: str
    owners: tuple[tuple[str, str], ...]
    mounts: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class _Stage:
    role: str
    handler: str | None
    error_identity: str | None = None


@dataclass(frozen=True, slots=True)
class _Snapshot:
    registrations: tuple[_Registration, ...]
    functions: dict[str, _Function]
    apps: frozenset[str]
    objects: frozenset[str]
    routes: tuple[_Route, ...]
    coverage: tuple[CoverageEvent, ...]


def _mask(text: str) -> str:
    """Retain executable punctuation, blank comments and literal contents."""
    out = list(text)
    index = 0
    while index < len(text):
        if text.startswith("//", index):
            end = text.find("\n", index)
            end = len(text) if end < 0 else end
            for pos in range(index, end):
                out[pos] = " "
            index = end
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            end = len(text) if end < 0 else end + 2
            for pos in range(index, end):
                if out[pos] != "\n":
                    out[pos] = " "
            index = end
            continue
        if text[index] in {"'", '"', "`"}:
            quote = text[index]
            pos = index + 1
            out[index] = " "
            while pos < len(text):
                if text[pos] == "\\":
                    out[pos] = " "
                    pos += 1
                    if pos < len(text):
                        out[pos] = " "
                        pos += 1
                    continue
                if text[pos] == quote:
                    out[pos] = " "
                    pos += 1
                    break
                if out[pos] != "\n":
                    out[pos] = " "
                pos += 1
            index = pos
            continue
        index += 1
    return "".join(out)


def _balanced(text: str, start: int) -> int | None:
    pairs = {"(": ")", "{": "}", "[": "]"}
    left = text[start] if start < len(text) else ""
    right = pairs.get(left)
    if right is None:
        return None
    masked = _mask(text)
    depth = 0
    for pos in range(start, len(masked)):
        if masked[pos] == left:
            depth += 1
        elif masked[pos] == right:
            depth -= 1
            if depth == 0:
                return pos
    return None


def _parts(text: str) -> tuple[str, ...] | None:
    masked = _mask(text)
    values: list[str] = []
    start = 0
    stack: list[str] = []
    pairs = {"(": ")", "[": "]", "{": "}"}
    for pos, char in enumerate(masked):
        if char in pairs:
            stack.append(char)
        elif char in ")]}":
            if not stack or pairs[stack.pop()] != char:
                return None
        elif char == "," and not stack:
            values.append(text[start:pos].strip())
            start = pos + 1
    return None if stack else tuple(values + [text[start:].strip()])


def _literal(value: str) -> str | None:
    match = re.fullmatch(r"\s*(['\"])(?P<v>(?:\\.|(?!\1).)*)\1\s*", value, re.DOTALL)
    return match.group("v") if match else None


def _join(prefix: str, path: str) -> str:
    return (prefix.rstrip("/") + "/" + path.lstrip("/")).replace("//", "/")


def _matches(prefix: str, path: str) -> bool:
    return (
        not prefix
        or prefix == "/"
        or path == prefix
        or path.startswith(prefix.rstrip("/") + "/")
    )


def _event(reason: str, path: str | None = None) -> CoverageEvent:
    return CoverageEvent(
        "javascript",
        CoverageCapability.FRAMEWORK_LIFECYCLE,
        CoverageOutcome.PARTIAL,
        reason,
        path,
        0,
        1,
    )


def _location(path: str, source: str, offset: int) -> SourceLocationIR:
    line = source.count("\n", 0, offset) + 1
    return SourceLocationIR(
        path, line, line, hashlib.sha256(source.encode()).hexdigest()
    )


def _locator(row: _Registration) -> AstLocatorIR:
    return AstLocatorIR(
        _location(row.source_path, row.source, row.offset),
        f"express/registration/{row.method}",
        row.ordinal,
    )


def _object_key(path: str, name: str) -> str:
    return f"{path}:{name}"


def _function_key(path: str, name: str) -> str:
    return f"{path}:{name}"


def _outcome(function: _Function | None) -> tuple[str | None, str | None]:
    if function is None:
        return None, None
    code = _mask(function.code)
    binding = {
        m.group("name"): m.group("kind")
        for m in re.finditer(
            r"\b(?:const|let)\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*new\s+(?P<kind>[A-Za-z_$][\w$]*)",
            code,
        )
    }
    next_match = re.search(r"\bnext\s*\(", code)
    if next_match:
        end = _balanced(function.code, next_match.end() - 1)
        arg = function.code[next_match.end() : end].strip() if end is not None else ""
        if not arg:
            return "next", None
        if re.fullmatch(r"(['\"])route\1", arg):
            return "next_route", None
        direct = re.match(r"new\s+([A-Za-z_$][\w$]*)", arg)
        if direct:
            return "next_error", direct.group(1)
        if arg in binding:
            return "next_error", binding[arg]
        return "computed_next", None
    thrown = re.search(r"\bthrow\s+new\s+([A-Za-z_$][\w$]*)", code)
    rejected = re.search(
        r"\breturn\s+Promise\.reject\s*\(\s*new\s+([A-Za-z_$][\w$]*)", code
    )
    if thrown:
        return "error", thrown.group(1)
    if rejected:
        return "error", rejected.group(1)
    if re.search(r"(?<!return\s)Promise\.reject\s*\(", code):
        return "detached_rejection", None
    if re.search(r"\bres\s*\[", code):
        return "computed_terminal", None
    for name in ("send", "json", "end", "redirect"):
        if re.search(rf"\bres\s*\.\s*{name}\s*\(", code):
            return name, None
    return None, None


class ExpressLifecycleAdapter:
    language = "javascript"
    framework = "express"

    def __init__(self) -> None:
        self._snapshots: dict[tuple[str, str, str, str], _Snapshot] = {}

    def _key(self, context: ExtractionContext) -> tuple[str, str, str, str]:
        return (
            str(context.workspace_root),
            context.project_id,
            context.workspace_binding_id,
            context.source_identity.tree_sha256,
        )

    def _snapshot(self, context: ExtractionContext) -> _Snapshot:
        return self._snapshots.get(
            self._key(context), _Snapshot((), {}, frozenset(), frozenset(), (), ())
        )

    def detect(self, context: ExtractionContext) -> FrameworkDetection:
        return FrameworkDetection(
            self.language,
            self.framework,
            any(
                row.language == "javascript" and row.name == "express"
                for row in context.detected_frameworks
            ),
        )

    def coverage_events(self, context: ExtractionContext) -> tuple[CoverageEvent, ...]:
        return self._snapshot(context).coverage

    def entrypoints(
        self, context: ExtractionContext, syntax: Sequence[SyntaxIR]
    ) -> tuple[EntrypointCandidate, ...]:
        coverage: list[CoverageEvent] = []
        objects: set[str] = set()
        apps: set[str] = set()
        functions: dict[str, _Function] = {}
        registrations: list[_Registration] = []
        ordinal = 0
        sources: list[tuple[SyntaxIR, str, str]] = []
        for item in syntax:
            if item.language not in {"javascript", "typescript"}:
                continue
            try:
                source = context.file_accessor(
                    Path(normalize_source_path(item.path))
                ).decode()
            except (OSError, UnicodeDecodeError, GraphContractError):
                continue
            masked = _mask(source)
            sources.append((item, source, masked))
            for match in _OBJECT.finditer(masked):
                key = _object_key(item.path, match.group("name"))
                objects.add(key)
                if ".Router" not in masked[match.start() : match.end()]:
                    apps.add(key)
            ordinals = {
                symbol.name: index
                for index, symbol in enumerate(item.symbols)
                if symbol.kind == "function"
            }
            for match in _FUNCTION.finditer(masked):
                end = _balanced(source, match.end() - 1)
                if end is None:
                    continue
                name = match.group("name")
                params = [
                    part for part in match.group("params").split(",") if part.strip()
                ]
                local = (
                    local_record_key(
                        "javascript",
                        item.path,
                        "executable_declaration",
                        "ast",
                        f"symbol/{name}",
                        ordinals[name],
                    )
                    if name in ordinals
                    else None
                )
                functions[_function_key(item.path, name)] = _Function(
                    _function_key(item.path, name),
                    name,
                    len(params),
                    source[match.end() : end],
                    local,
                )
        for item, source, masked in sources:
            for match in _COMPUTED.finditer(masked):
                owner = _object_key(item.path, match.group("owner"))
                if owner in objects:
                    coverage.append(_event("route_method_unresolved", item.path))
                elif match.group("owner") != "res":
                    coverage.extend(
                        _event(reason, item.path)
                        for reason in (
                            "registration_target_unresolved",
                            "route_method_unresolved",
                            "route_path_unresolved",
                            "handler_target_unresolved",
                        )
                    )
            for match in _CALL.finditer(masked):
                owner = _object_key(item.path, match.group("owner"))
                method = match.group("method")
                end = _balanced(source, match.end() - 1)
                args = _parts(source[match.end() : end]) if end is not None else None
                if owner not in objects:
                    coverage.extend(
                        _event(reason, item.path)
                        for reason in (
                            "registration_target_unresolved",
                            "route_method_unresolved",
                            "route_path_unresolved",
                            "handler_target_unresolved",
                        )
                    )
                    continue
                if args is None:
                    coverage.append(_event("handler_target_unresolved", item.path))
                    continue
                if method == "use":
                    if args and _literal(args[0]) is not None:
                        path, handlers = _literal(args[0]), args[1:]
                    elif len(args) == 1:
                        path, handlers = "", args
                    else:
                        path, handlers = None, args[1:]
                        coverage.extend(
                            _event(reason, item.path)
                            for reason in (
                                "mount_prefix_unresolved",
                                "router_target_unresolved",
                            )
                        )
                else:
                    path, handlers = (_literal(args[0]) if args else None), args[1:]
                    if path is None:
                        coverage.append(_event("route_path_unresolved", item.path))
                        continue
                resolved = tuple(
                    _function_key(item.path, value)
                    if _IDENT.fullmatch(value)
                    else value
                    for value in handlers
                )
                if any(not _IDENT.fullmatch(value) for value in handlers):
                    coverage.extend((
                        _event("middleware_order_unresolved", item.path),
                        _event("handler_target_unresolved", item.path),
                    ))
                if method == "use" and any(
                    _function_key(item.path, value) not in functions
                    and _object_key(item.path, value) not in objects
                    for value in handlers
                    if _IDENT.fullmatch(value)
                ):
                    coverage.extend((
                        _event("error_middleware_arity_unresolved", item.path),
                        _event("handler_target_unresolved", item.path),
                    ))
                registrations.append(
                    _Registration(
                        owner,
                        method,
                        path,
                        resolved,
                        item.path,
                        source,
                        match.start(),
                        ordinal,
                    )
                )
                ordinal += 1
        for function in functions.values():
            kind, _identity = _outcome(function)
            if kind == "computed_next":
                coverage.extend(
                    _event(reason)
                    for reason in (
                        "continuation_kind_unresolved",
                        "error_flow_unresolved",
                    )
                )
            elif kind == "computed_terminal":
                coverage.append(_event("response_outcome_unresolved"))
            elif kind == "detached_rejection":
                coverage.append(_event("async_error_flow_unresolved"))
        by_owner: dict[str, list[_Registration]] = {}
        for row in registrations:
            by_owner.setdefault(row.owner, []).append(row)
        routes: list[_Route] = []

        def visit(
            owner: str,
            prefix: str,
            owners: tuple[tuple[str, str], ...],
            mounts: tuple[tuple[str, str], ...],
            seen: frozenset[str],
        ) -> None:
            if owner in seen:
                coverage.append(_event("router_target_unresolved"))
                return
            for row in by_owner.get(owner, ()):
                if row.method in _METHODS or row.method == "all":
                    routes.append(
                        _Route(row, _join(prefix, row.path or ""), owners, mounts)
                    )
                elif row.method == "use" and row.path is not None:
                    for target in row.handlers:
                        if target in objects:
                            visit(
                                target,
                                _join(prefix, row.path),
                                owners + ((target, _join(prefix, row.path)),),
                                mounts + ((target, _join(prefix, row.path)),),
                                seen | {owner},
                            )

        for app in sorted(apps):
            visit(app, "", ((app, ""),), (), frozenset())
        snapshot = _Snapshot(
            tuple(registrations),
            functions,
            frozenset(apps),
            frozenset(objects),
            tuple(routes),
            tuple(
                sorted(
                    set(coverage),
                    key=lambda event: (event.reason_code or "", event.path or ""),
                )
            ),
        )
        self._snapshots[self._key(context)] = snapshot
        candidates: list[EntrypointCandidate] = []
        for route in routes:
            row = route.registration
            handler = next(
                (
                    value
                    for value in row.handlers
                    if value in functions and functions[value].local_key
                ),
                None,
            )
            locator = _locator(row)
            unresolved = (
                None
                if handler
                else local_record_key(
                    "javascript",
                    row.source_path,
                    "unresolved_fact",
                    "ast",
                    f"{locator.structural_path}/handler",
                    row.ordinal,
                )
            )
            evidence = IREvidence(
                EvidenceOrigin.VERIFIED_FROM_CODE
                if handler
                else EvidenceOrigin.UNRESOLVED,
                "express.lifecycle",
                locator,
                None,
            )
            methods = () if row.method == "all" else (row.method.upper(),)
            candidates.append(
                EntrypointCandidate(
                    EntrypointKind.HTTP_ROUTE,
                    "express",
                    MethodSemantics.UNRESTRICTED
                    if row.method == "all"
                    else MethodSemantics.EXPLICIT,
                    methods,
                    route.path,
                    functions[handler].name if handler else None,
                    TriggerKind.HTTP,
                    f"{'ALL' if not methods else methods[0]} {route.path}",
                    MatchConstraints(None, (), None),
                    locator,
                    functions[handler].local_key if handler else None,
                    unresolved,
                    (),
                    evidence,
                )
            )
        return tuple(
            replace(
                candidate,
                framework_segment_keys=tuple(
                    segment.local_key for segment in self.pipeline(context, candidate)
                ),
            )
            for candidate in candidates
        )

    def pipeline(
        self, context: ExtractionContext, candidate: EntrypointCandidate
    ) -> tuple[FrameworkPipelineSegment, ...]:
        snap = self._snapshot(context)
        route = next(
            (
                item
                for item in snap.routes
                if _locator(item.registration) == candidate.registration_locator
            ),
            None,
        )
        if route is None:
            return ()
        stages: list[_Stage] = [
            _Stage("router_mount", None, f"{identity}@{prefix}")
            for identity, prefix in route.mounts
        ]

        def applicable(row: _Registration) -> bool:
            owner_prefix = next(
                (prefix for owner, prefix in route.owners if owner == row.owner), None
            )
            return (
                owner_prefix is not None
                and row.path is not None
                and _matches(_join(owner_prefix, row.path), route.path)
            )

        error_at: int | None = None
        for row in sorted(snap.registrations, key=lambda item: item.ordinal):
            if (
                row.method != "use"
                or row.ordinal >= route.registration.ordinal
                or not applicable(row)
            ):
                continue
            for handler in row.handlers:
                function = snap.functions.get(handler)
                if function is None:
                    continue
                if function and function.arity == 4:
                    continue
                stages.append(_Stage("middleware", handler))
                kind, identity = _outcome(function)
                if kind == "next":
                    stages.append(_Stage("continuation_next", handler))
                    continue
                if kind in {"error", "next_error"}:
                    error_at = row.ordinal
                    stages.append(
                        _Stage(
                            "error_transition"
                            if kind == "error"
                            else "continuation_next_error",
                            None,
                            identity,
                        )
                    )
                    break
                if kind in {"send", "json", "end", "redirect"}:
                    stages.append(_Stage(f"terminal_{kind}", handler))
                    return self._segments(candidate, snap, stages)
                return self._segments(candidate, snap, stages)
        active = route.registration
        while error_at is None:
            for handler in active.handlers:
                function = snap.functions.get(handler)
                if function is None:
                    continue
                stages.append(_Stage("route_handler", handler))
                kind, identity = _outcome(function)
                if kind == "next":
                    stages.append(_Stage("continuation_next", handler))
                    continue
                if kind == "next_route":
                    stages.append(_Stage("continuation_next_route", handler))
                    active = next(
                        (
                            row
                            for row in snap.registrations
                            if row.owner == active.owner
                            and row.path == active.path
                            and row.ordinal > active.ordinal
                            and (row.method == active.method or row.method == "all")
                        ),
                        active,
                    )
                    break
                if kind in {"error", "next_error"}:
                    error_at = active.ordinal
                    stages.append(
                        _Stage(
                            "error_transition"
                            if kind == "error"
                            else "continuation_next_error",
                            None,
                            identity,
                        )
                    )
                    break
                if kind in {"send", "json", "end", "redirect"}:
                    stages.append(_Stage(f"terminal_{kind}", handler))
                    return self._segments(candidate, snap, stages)
                return self._segments(candidate, snap, stages)
            else:
                return self._segments(candidate, snap, stages)
            if active is route.registration:
                break
            route = replace(route, registration=active)
        if error_at is not None:
            for row in sorted(snap.registrations, key=lambda item: item.ordinal):
                if (
                    row.method != "use"
                    or row.ordinal <= error_at
                    or not applicable(row)
                ):
                    continue
                for handler in row.handlers:
                    function = snap.functions.get(handler)
                    if function and function.arity == 4:
                        stages.append(_Stage("error_middleware", handler))
                        kind, _ = _outcome(function)
                        if kind in {"send", "json", "end", "redirect"}:
                            stages.append(_Stage(f"terminal_{kind}", handler))
                            return self._segments(candidate, snap, stages)
                        if kind == "next":
                            stages.append(_Stage("continuation_next", handler))
                            continue
                        return self._segments(candidate, snap, stages)
        return self._segments(candidate, snap, stages)

    def pipeline_facts(
        self, context: ExtractionContext, candidate: EntrypointCandidate
    ) -> FrameworkPipelineFacts:
        def terminal_spec(
            segment: FrameworkPipelineSegment, _successor: ReturnSuccessor
        ) -> FrameworkTerminalSpec:
            role = segment.framework_role
            if role in {"error_transition", "continuation_next_error"}:
                exception_type = (
                    segment.target.descriptor.public_name
                    if type(segment.target) is FrameworkBoundaryTarget
                    else None
                )
                return FrameworkTerminalSpec(
                    TerminalKind.EXCEPTION,
                    exception_type=exception_type or "Error",
                )
            if role == "terminal_redirect":
                return FrameworkTerminalSpec(TerminalKind.REDIRECT)
            return FrameworkTerminalSpec(TerminalKind.RESPONSE)

        pipeline = self.pipeline(context, candidate)
        return framework_pipeline_facts(candidate, pipeline, terminal_spec)

    def _segments(
        self, candidate: EntrypointCandidate, snap: _Snapshot, stages: list[_Stage]
    ) -> tuple[FrameworkPipelineSegment, ...]:
        keys = [
            local_record_key(
                "javascript",
                candidate.registration_locator.source_location.path,
                "framework_pipeline",
                "ast",
                f"{candidate.registration_locator.structural_path}/pipeline/{stage.role}",
                index,
            )
            for index, stage in enumerate(stages)
        ]
        rows: list[FrameworkPipelineSegment] = []
        for index, stage in enumerate(stages):
            function = snap.functions.get(stage.handler or "")
            target = (
                FrameworkBoundaryTarget(
                    FrameworkBoundaryDescriptor(
                        "express",
                        stage.role,
                        function.name,
                        candidate.registration_locator,
                        candidate.evidence,
                    )
                )
                if stage.role == "error_middleware" and function
                else (
                    FrameworkLocalTarget(function.local_key)
                    if function and function.local_key
                    else FrameworkBoundaryTarget(
                        FrameworkBoundaryDescriptor(
                            "express",
                            stage.role,
                            stage.error_identity
                            or (function.name if function else stage.handler),
                            candidate.registration_locator,
                            candidate.evidence,
                        )
                    )
                )
            )
            successor = (
                ReturnSuccessor(
                    local_record_key(
                        "javascript",
                        candidate.registration_locator.source_location.path,
                        "framework_terminal",
                        "ast",
                        f"{candidate.registration_locator.structural_path}/terminal/{stage.role}",
                        index,
                    ),
                    index,
                )
                if stage.role.startswith("terminal_")
                else (
                    AlwaysSuccessor(keys[index + 1], index)
                    if index + 1 < len(keys)
                    else ReturnSuccessor(
                        local_record_key(
                            "javascript",
                            candidate.registration_locator.source_location.path,
                            "framework_terminal",
                            "ast",
                            f"{candidate.registration_locator.structural_path}/terminal/response",
                            index,
                        ),
                        index,
                    )
                )
            )
            rows.append(
                FrameworkPipelineSegment(
                    keys[index],
                    stage.role,
                    index,
                    target,
                    successor,
                    (),
                    candidate.evidence,
                )
            )
        return tuple(rows)


__all__ = ["ExpressLifecycleAdapter"]
