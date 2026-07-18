"""Static, source-only Next.js request lifecycle extraction.

The adapter intentionally recognizes a small closed set of file conventions and
literal configuration forms.  It never imports a Next.js configuration module
or executes application source: anything computed stays explicit coverage
uncertainty rather than becoming guessed request topology.
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


_LANGUAGES = frozenset({"javascript", "typescript"})
_APP_ROUTE_RE = re.compile(r"(?:^|/)app/(.*)/route\.(?:js|ts)$")
_PAGES_API_RE = re.compile(r"(?:^|/)pages/api/(.*)\.(?:js|ts)$")
_MIDDLEWARE_RE = re.compile(r"^(?:src/)?middleware\.(?:js|ts)$")
_CONFIG_RE = re.compile(r"(?:^|/)next\.config\.(?:js|ts|mjs)$")
_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"})
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
_LITERAL_STRING_RE = re.compile(r"^\s*([\"'])(?P<value>[^\n\r\"']*)\1\s*$")


@dataclass(frozen=True, slots=True)
class _FileRole:
    path: str
    language: str
    role: str
    public_path: str | None
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _HttpExport:
    path: str
    name: str
    line: int
    method: str | None
    resolved: bool


@dataclass(frozen=True, slots=True)
class _ConfigRule:
    path: str
    kind: str
    source: str | None
    destination: str | None
    permanent: bool | None
    phase: str | None
    order: int
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Snapshot:
    candidates: tuple[EntrypointCandidate, ...]
    pipelines: Mapping[tuple[object, ...], tuple[FrameworkPipelineSegment, ...]]
    coverage_events: tuple[CoverageEvent, ...]


def _safe_path(path: str) -> str | None:
    if not isinstance(path, str) or not path:
        return None
    try:
        return normalize_source_path(path)
    except GraphContractError:
        return None


def _text(context: ExtractionContext, path: str) -> str | None:
    """Read one already-inventoried, safe relative path as strict UTF-8."""

    safe_path = _safe_path(path)
    if safe_path is None:
        return None
    try:
        return context.file_accessor(Path(safe_path)).decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _detected_version(
    context: ExtractionContext, language: str
) -> tuple[str | None, str]:
    for record in context.detected_frameworks:
        if record.language == language and record.name == "nextjs" and record.version:
            return record.version, "framework_record"
    for metadata in context.package_metadata:
        if metadata.source_location.path != "package.json":
            continue
        source = _text(context, metadata.source_location.path)
        if source is None:
            continue
        try:
            document = json.loads(source)
        except json.JSONDecodeError:
            continue
        for section in ("dependencies", "devDependencies"):
            values = document.get(section)
            version = values.get("next") if isinstance(values, Mapping) else None
            if isinstance(version, str) and _VERSION_RE.fullmatch(version):
                return version, "package_json"
    return None, "unresolved"


def _segments_pattern(segments: Sequence[str]) -> tuple[str | None, tuple[str, ...]]:
    normalized: list[str] = []
    parameters: list[str] = []
    for segment in segments:
        if not segment:
            continue
        if re.fullmatch(r"\([A-Za-z0-9_-]+\)", segment):
            continue
        if match := re.fullmatch(r"\[\[\.\.\.([A-Za-z_][A-Za-z0-9_]*)\]\]", segment):
            parameters.append(match.group(1))
            normalized.append(f":{match.group(1)}*?")
            continue
        if match := re.fullmatch(r"\[\.\.\.([A-Za-z_][A-Za-z0-9_]*)\]", segment):
            parameters.append(match.group(1))
            normalized.append(f":{match.group(1)}*")
            continue
        if match := re.fullmatch(r"\[([A-Za-z_][A-Za-z0-9_]*)\]", segment):
            parameters.append(match.group(1))
            normalized.append(f":{match.group(1)}")
            continue
        if "[" in segment or "]" in segment or segment in {".", ".."}:
            return None, ("route_pattern_unresolved",)
        normalized.append(segment)
    return "/" + "/".join(normalized), tuple(parameters)


def _public_pattern(path: str) -> tuple[str | None, tuple[str, ...]]:
    match = _APP_ROUTE_RE.search(path)
    if match is None:
        return None, ("route_pattern_unresolved",)
    return _segments_pattern(
        tuple(segment for segment in match.group(1).split("/") if segment)
    )


def _file_role(path: str, language: str) -> _FileRole:
    safe_path = _safe_path(path)
    if safe_path is None:
        return _FileRole(
            path, language, "unsupported", None, ("route_pattern_unresolved",)
        )
    if _APP_ROUTE_RE.search(safe_path):
        public_path, reasons = _public_pattern(safe_path)
        return _FileRole(safe_path, language, "app_route", public_path, reasons)
    pages_match = _PAGES_API_RE.search(safe_path)
    if pages_match:
        public_path, reasons = _segments_pattern((
            "api",
            *pages_match.group(1).split("/"),
        ))
        return _FileRole(safe_path, language, "pages_api", public_path, reasons)
    if _MIDDLEWARE_RE.search(safe_path):
        return _FileRole(safe_path, language, "middleware", "/", ())
    if _CONFIG_RE.search(safe_path):
        return _FileRole(safe_path, language, "next_config", None, ())
    if safe_path.startswith("app/") and re.search(
        r"/(?:page|layout)\.(?:js|ts|jsx|tsx)$", safe_path
    ):
        return _FileRole(safe_path, language, "render", None, ())
    if safe_path.startswith("app/") and "/api/" in f"/{safe_path}":
        return _FileRole(
            safe_path,
            language,
            "ambiguous",
            None,
            ("http_entrypoint_file_role_unresolved",),
        )
    return _FileRole(safe_path, language, "unsupported", None, ())


def _line(source: str, offset: int) -> int:
    return source.count("\n", 0, max(0, offset)) + 1


def _http_exports(source: str, path: str) -> tuple[_HttpExport, ...]:
    exports: list[_HttpExport] = []
    declaration = re.compile(
        r"\bexport\s+(?:async\s+)?function\s+(?P<name>[A-Z]+)\s*\(|"
        r"\bexport\s+const\s+(?P<const>[A-Z]+)\s*=",
        re.MULTILINE,
    )
    for match in declaration.finditer(source):
        name = match.group("name") or match.group("const")
        if name in _HTTP_METHODS:
            exports.append(
                _HttpExport(path, name, _line(source, match.start()), name, True)
            )
    reexports = re.compile(
        r"\bexport\s*{(?P<names>[^}]+)}\s*from\s*[\"'][^\"']+[\"']",
        re.MULTILINE,
    )
    for match in reexports.finditer(source):
        for name in re.findall(r"\b[A-Z]+\b", match.group("names")):
            if name in _HTTP_METHODS:
                exports.append(
                    _HttpExport(path, name, _line(source, match.start()), name, False)
                )
    return tuple(sorted(exports, key=lambda item: (item.line, item.name)))


def _pages_api_methods(source: str) -> tuple[tuple[str, ...] | None, bool]:
    switch = re.search(r"\bswitch\s*\(\s*req\.method\s*\)\s*{", source)
    if switch is None:
        return None, False
    body = _balanced(source, switch.end() - 1)
    if body is None:
        return None, False
    methods = tuple(sorted(set(re.findall(r"\bcase\s*[\"']([A-Z]+)[\"']\s*:", body))))
    exhaustive = bool(methods) and "default:" in body
    return (methods if exhaustive else None), exhaustive


def _pages_api_dispatch_unresolved(source: str) -> bool:
    """Separate computed dispatch from a literal but non-exhaustive switch."""

    switch = re.search(r"\bswitch\s*\(\s*req\.method\s*\)\s*{", source)
    if switch is None:
        return "req.method" in source
    body = _balanced(source, switch.end() - 1)
    if body is None:
        return True
    return bool(re.search(r"\bcase\s+(?![\"'])", body))


def _balanced(source: str, start: int) -> str | None:
    if start >= len(source) or source[start] not in "[{":
        return None
    opening = source[start]
    closing = "]" if opening == "[" else "}"
    depth = 0
    quote: str | None = None
    escaped = False
    for index in range(start, len(source)):
        character = source[index]
        if quote:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in {"'", '"'}:
            quote = character
        elif character == opening:
            depth += 1
        elif character == closing:
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    return None


def _literal_string(value: str) -> str | None:
    match = _LITERAL_STRING_RE.fullmatch(value)
    return match.group("value") if match else None


def _literal_matchers(value: str) -> tuple[str, ...] | None:
    value = value.strip()
    if literal := _literal_string(value):
        return (literal,)
    if value.startswith("{"):
        source = re.fullmatch(
            r"\{\s*source\s*:\s*([\"'][^\"']*[\"'])\s*\}", value, re.DOTALL
        )
        return (_literal_string(source.group(1)),) if source else None
    if not value.startswith("["):
        return None
    array = _balanced(value, 0)
    if array != value:
        return None
    inner = array[1:-1]
    values: list[str] = []
    position = 0
    item = re.compile(
        r"\s*(?:([\"'][^\"']*[\"'])|\{\s*source\s*:\s*([\"'][^\"']*[\"'])\s*\})\s*(?:,|$)",
        re.DOTALL,
    )
    while position < len(inner):
        match = item.match(inner, position)
        if match is None:
            return None
        literal = _literal_string(match.group(1) or match.group(2))
        if literal is None:
            return None
        values.append(literal)
        position = match.end()
    return tuple(values)


def _middleware_rules(source: str, path: str) -> tuple[_ConfigRule, ...]:
    rules: list[_ConfigRule] = []
    matcher = re.search(r"\bmatcher\s*:\s*", source)
    if matcher:
        start = matcher.end()
        while start < len(source) and source[start].isspace():
            start += 1
        if start < len(source) and source[start] in "[{":
            value = _balanced(source, start)
        else:
            value_match = re.match(r"[^,}\n]+", source[start:])
            value = value_match.group(0) if value_match else None
        matchers = _literal_matchers(value) if value else None
        if matchers is None:
            rules.append(
                _ConfigRule(
                    path,
                    "unresolved",
                    None,
                    None,
                    None,
                    None,
                    0,
                    ("middleware_matcher_unresolved",),
                )
            )
        else:
            rules.extend(
                _ConfigRule(path, "matcher", item, None, None, None, index, ())
                for index, item in enumerate(matchers)
            )
    elif re.search(r"\bconfig\s*=\s*\{\s*matcher\s*}\s*", source):
        rules.append(
            _ConfigRule(
                path,
                "unresolved",
                None,
                None,
                None,
                None,
                0,
                ("middleware_matcher_unresolved",),
            )
        )
    order = len(rules)
    for match in re.finditer(
        r"\bNextResponse\.redirect\s*\(\s*([\"'][^\"']*[\"'])", source
    ):
        destination = _literal_string(match.group(1))
        if destination is not None:
            rules.append(
                _ConfigRule(path, "redirect", None, destination, None, None, order, ())
            )
            order += 1
    if "NextResponse.redirect" in source and not any(
        rule.kind == "redirect" for rule in rules
    ):
        rules.append(
            _ConfigRule(
                path,
                "unresolved",
                None,
                None,
                None,
                None,
                order,
                ("middleware_outcome_unresolved",),
            )
        )
        order += 1
    for _match in re.finditer(r"\bnew\s+NextResponse\s*\(", source):
        rules.append(_ConfigRule(path, "response", None, None, None, None, order, ()))
        order += 1
    for _match in re.finditer(r"\bNextResponse\.next\s*\(", source):
        rules.append(_ConfigRule(path, "next", None, None, None, None, order, ()))
        order += 1
    if re.search(
        r"\breturn\s+(?!NextResponse\.(?:redirect|next)|new\s+NextResponse)", source
    ):
        rules.append(
            _ConfigRule(
                path,
                "unresolved",
                None,
                None,
                None,
                None,
                order,
                ("middleware_outcome_unresolved",),
            )
        )
    return tuple(rules)


def _return_expression(source: str, name: str) -> str | None:
    function = re.search(rf"\b{name}\s*\([^)]*\)\s*{{", source)
    if function is None:
        return None
    returned = re.search(r"\breturn\s+", source[function.end() :])
    if returned is None:
        return None
    start = function.end() + returned.end()
    while start < len(source) and source[start].isspace():
        start += 1
    return _balanced(source, start)


def _objects(array: str) -> tuple[str, ...] | None:
    if not array.startswith("[") or not array.endswith("]"):
        return None
    values: list[str] = []
    index = 1
    while index < len(array) - 1:
        while index < len(array) - 1 and array[index] in " \t\r\n,":
            index += 1
        if index == len(array) - 1:
            break
        item = _balanced(array, index)
        if item is None:
            return None
        values.append(item)
        index += len(item)
    return tuple(values)


def _property(object_source: str, name: str) -> tuple[object, bool]:
    match = re.search(rf"\b{name}\s*:\s*", object_source)
    if match is None:
        return None, False
    start = match.end()
    while start < len(object_source) and object_source[start].isspace():
        start += 1
    if start < len(object_source) and object_source[start] in "'\"":
        quote = object_source[start]
        end = object_source.find(quote, start + 1)
        return (
            (object_source[start + 1 : end], end != -1) if end != -1 else (None, False)
        )
    if object_source.startswith("true", start):
        return True, True
    if object_source.startswith("false", start):
        return False, True
    return None, False


def _config_entries(
    path: str, kind: str, expression: str | None, order: int
) -> tuple[tuple[_ConfigRule, ...], int]:
    if expression is None:
        return (
            _ConfigRule(
                path,
                "unresolved",
                None,
                None,
                None,
                None,
                order,
                ("framework_config_unresolved",),
            ),
        ), order + 1
    arrays: list[tuple[str | None, str]] = []
    if expression.startswith("["):
        arrays.append((None, expression))
    elif expression.startswith("{") and kind == "rewrite":
        for phase in ("beforeFiles", "afterFiles", "fallback"):
            match = re.search(rf"\b{phase}\s*:\s*", expression)
            if match is None:
                continue
            start = match.end()
            while start < len(expression) and expression[start].isspace():
                start += 1
            array = _balanced(expression, start)
            if array is None:
                return (
                    _ConfigRule(
                        path,
                        "unresolved",
                        None,
                        None,
                        None,
                        None,
                        order,
                        ("framework_config_unresolved",),
                    ),
                ), order + 1
            arrays.append((phase, array))
        if not arrays:
            return (
                _ConfigRule(
                    path,
                    "unresolved",
                    None,
                    None,
                    None,
                    None,
                    order,
                    ("framework_config_unresolved",),
                ),
            ), order + 1
    else:
        return (
            _ConfigRule(
                path,
                "unresolved",
                None,
                None,
                None,
                None,
                order,
                ("framework_config_unresolved",),
            ),
        ), order + 1
    rules: list[_ConfigRule] = []
    for phase, array in arrays:
        entries = _objects(array)
        if entries is None:
            return (
                _ConfigRule(
                    path,
                    "unresolved",
                    None,
                    None,
                    None,
                    None,
                    order,
                    ("framework_config_unresolved",),
                ),
            ), order + 1
        for entry in entries:
            source, source_ok = _property(entry, "source")
            destination, destination_ok = _property(entry, "destination")
            permanent, permanent_ok = _property(entry, "permanent")
            is_valid = (
                source_ok
                and destination_ok
                and isinstance(source, str)
                and isinstance(destination, str)
            )
            if kind == "redirect":
                is_valid = is_valid and permanent_ok and type(permanent) is bool
            if not is_valid:
                return (
                    _ConfigRule(
                        path,
                        "unresolved",
                        None,
                        None,
                        None,
                        None,
                        order,
                        ("framework_config_unresolved",),
                    ),
                ), order + 1
            rules.append(
                _ConfigRule(
                    path,
                    kind,
                    source,
                    destination,
                    permanent if kind == "redirect" else None,
                    phase,
                    order,
                    (),
                )
            )
            order += 1
    return tuple(rules), order


def _next_config_rules(source: str, path: str) -> tuple[_ConfigRule, ...]:
    rules: list[_ConfigRule] = []
    order = 0
    for kind, name in (("rewrite", "rewrites"), ("redirect", "redirects")):
        if re.search(rf"\b{name}\s*\(", source):
            entries, order = _config_entries(
                path, kind, _return_expression(source, name), order
            )
            rules.extend(entries)
    return tuple(rules)


def _candidate_key(candidate: EntrypointCandidate) -> tuple[object, ...]:
    locator = candidate.registration_locator
    return (
        locator.source_location.path,
        locator.structural_path,
        locator.ordinal,
        candidate.public_path or "",
        candidate.public_name or "",
        candidate.method_semantics.value,
        candidate.methods,
    )


def _locator(
    path: str, source: str, line: int, pointer: str, ordinal: int
) -> AstLocatorIR:
    return AstLocatorIR(
        SourceLocationIR(
            path,
            max(1, line),
            max(1, line),
            hashlib.sha256(source.encode()).hexdigest(),
        ),
        pointer,
        ordinal,
    )


def _candidate(
    language: str,
    path: str,
    source: str,
    line: int,
    pointer: str,
    ordinal: int,
    public_path: str | None,
    public_name: str | None,
    methods: tuple[str, ...],
    resolved: bool,
) -> EntrypointCandidate:
    locator = _locator(path, source, line, pointer, ordinal)
    handler = (
        local_record_key(language, path, "framework_handler", "ast", pointer, ordinal)
        if resolved
        else None
    )
    unresolved = (
        None
        if resolved
        else local_record_key(
            language, path, "unresolved_fact", "ast", pointer, ordinal
        )
    )
    evidence = IREvidence(
        EvidenceOrigin.VERIFIED_FROM_CODE if resolved else EvidenceOrigin.UNRESOLVED,
        "nextjs.lifecycle",
        locator,
        None,
    )
    return EntrypointCandidate(
        EntrypointKind.HTTP_ROUTE,
        "nextjs",
        MethodSemantics.EXPLICIT if methods else MethodSemantics.UNRESTRICTED,
        methods,
        public_path,
        public_name,
        TriggerKind.HTTP,
        f"{'|'.join(methods) if methods else 'ALL'} {public_path or public_name or 'nextjs'}",
        MatchConstraints(None, (), None),
        locator,
        handler,
        unresolved,
        (),
        evidence,
    )


def _pipeline(
    language: str, candidate: EntrypointCandidate, rules: Sequence[_ConfigRule]
) -> tuple[FrameworkPipelineSegment, ...]:
    locator = candidate.registration_locator
    segments: list[FrameworkPipelineSegment] = []
    for index, rule in enumerate(rule for rule in rules if not rule.reasons):
        role = {
            "matcher": "middleware_matcher",
            "redirect": "middleware_redirect",
            "response": "middleware_response",
            "next": "middleware_next",
            "rewrite": f"rewrite_{_snake_phase(rule.phase)}",
        }.get(rule.kind)
        if rule.kind == "redirect" and rule.permanent is not None:
            role = f"redirect_{308 if rule.permanent else 307}"
        if role is None:
            continue
        key = local_record_key(
            language,
            locator.source_location.path,
            "framework_pipeline",
            "ast",
            f"{locator.structural_path}/pipeline/{role}",
            index,
        )
        terminal = local_record_key(
            language,
            locator.source_location.path,
            "framework_terminal",
            "ast",
            f"{locator.structural_path}/terminal/{role}",
            index,
        )
        public_name = rule.source if rule.kind == "matcher" else rule.destination
        target = FrameworkBoundaryTarget(
            FrameworkBoundaryDescriptor(
                "nextjs", role, public_name, locator, candidate.evidence
            )
        )
        segments.append(
            FrameworkPipelineSegment(
                key,
                role,
                len(segments),
                target,
                ReturnSuccessor(terminal, 0),
                (),
                candidate.evidence,
            )
        )
    if not segments and candidate.handler_local_key is not None:
        key = local_record_key(
            language,
            locator.source_location.path,
            "framework_pipeline",
            "ast",
            f"{locator.structural_path}/pipeline/handler",
            0,
        )
        terminal = local_record_key(
            language,
            locator.source_location.path,
            "framework_terminal",
            "ast",
            f"{locator.structural_path}/terminal/handler",
            0,
        )
        segments.append(
            FrameworkPipelineSegment(
                key,
                "handler",
                0,
                FrameworkLocalTarget(candidate.handler_local_key),
                ReturnSuccessor(terminal, 0),
                (),
                candidate.evidence,
            )
        )
    return tuple(segments)


def _snake_phase(phase: str | None) -> str:
    if phase is None:
        return "default"
    return re.sub(r"(?<!^)([A-Z])", r"_\1", phase).lower()


def _coverage(
    language: str, path: str, reason: str, outcome: CoverageOutcome
) -> CoverageEvent:
    return CoverageEvent(
        language,
        CoverageCapability.FRAMEWORK_LIFECYCLE,
        outcome,
        reason,
        path,
        0,
        1,
    )


def _build_snapshot(
    context: ExtractionContext, syntax: Sequence[SyntaxIR], language: str
) -> _Snapshot:
    candidates: list[EntrypointCandidate] = []
    candidate_rules: dict[tuple[object, ...], tuple[_ConfigRule, ...]] = {}
    coverage: list[CoverageEvent] = []
    version, _provenance = _detected_version(context, language)
    if version is None:
        coverage.append(
            _coverage(
                language,
                "package.json",
                "framework_version_unresolved",
                CoverageOutcome.UNSUPPORTED,
            )
        )
    for item in syntax:
        role = _file_role(item.path, language)
        if role.reasons:
            coverage.extend(
                _coverage(language, role.path, reason, CoverageOutcome.UNSUPPORTED)
                for reason in role.reasons
            )
        if (
            role.role in {"unsupported", "render", "ambiguous"}
            or role.public_path is None
            and role.role in {"app_route", "pages_api"}
        ):
            continue
        source = _text(context, role.path)
        if source is None:
            continue
        if role.role == "app_route":
            for ordinal, export in enumerate(_http_exports(source, role.path)):
                candidate = _candidate(
                    language,
                    role.path,
                    source,
                    export.line,
                    f"exports/{export.name}",
                    ordinal,
                    role.public_path,
                    export.name,
                    (export.method,) if export.method else (),
                    export.resolved,
                )
                candidates.append(candidate)
                candidate_rules[_candidate_key(candidate)] = ()
                if not export.resolved:
                    coverage.append(
                        _coverage(
                            language,
                            role.path,
                            "route_handler_export_target_unresolved",
                            CoverageOutcome.PARTIAL,
                        )
                    )
        elif role.role == "pages_api" and re.search(r"\bexport\s+default\b", source):
            methods, exhaustive = _pages_api_methods(source)
            candidate = _candidate(
                language,
                role.path,
                source,
                _line(source, source.find("export default")),
                "exports/default",
                0,
                role.public_path,
                None,
                methods or (),
                True,
            )
            candidates.append(candidate)
            candidate_rules[_candidate_key(candidate)] = ()
            if not exhaustive and _pages_api_dispatch_unresolved(source):
                coverage.append(
                    _coverage(
                        language,
                        role.path,
                        "pages_api_method_dispatch_unresolved",
                        CoverageOutcome.PARTIAL,
                    )
                )
        elif role.role == "middleware":
            rules = _middleware_rules(source, role.path)
            candidate = _candidate(
                language,
                role.path,
                source,
                1,
                "middleware",
                0,
                role.public_path,
                "middleware",
                (),
                bool(re.search(r"\b(?:export\s+)?function\s+middleware\b", source)),
            )
            candidates.append(candidate)
            candidate_rules[_candidate_key(candidate)] = rules
            for rule in rules:
                for reason in rule.reasons:
                    coverage.append(
                        _coverage(language, role.path, reason, CoverageOutcome.PARTIAL)
                    )
        elif role.role == "next_config":
            for rule in _next_config_rules(source, role.path):
                if rule.reasons:
                    coverage.extend(
                        _coverage(
                            language, role.path, reason, CoverageOutcome.UNSUPPORTED
                        )
                        for reason in rule.reasons
                    )
                    continue
                candidate = _candidate(
                    language,
                    role.path,
                    source,
                    _line(source, source.find(rule.source or "")),
                    f"{rule.kind}s/{rule.order}",
                    rule.order,
                    rule.source,
                    rule.destination,
                    (),
                    True,
                )
                candidates.append(candidate)
                candidate_rules[_candidate_key(candidate)] = (rule,)
    ordered = tuple(
        sorted(
            candidates,
            key=lambda item: (
                item.registration_locator.source_location.path,
                item.registration_locator.source_location.start_line,
                item.registration_locator.structural_path,
                item.registration_locator.ordinal,
            ),
        )
    )
    pipelines: dict[tuple[object, ...], tuple[FrameworkPipelineSegment, ...]] = {}
    completed: list[EntrypointCandidate] = []
    for candidate in ordered:
        segments = _pipeline(
            language, candidate, candidate_rules[_candidate_key(candidate)]
        )
        pipelines[_candidate_key(candidate)] = segments
        completed.append(
            replace(
                candidate,
                framework_segment_keys=tuple(item.local_key for item in segments),
            )
        )
    event_key = lambda event: (
        event.path or "",
        event.capability.value,
        event.reason_code or "",
        event.outcome.value,
    )
    events = tuple(
        sorted({event_key(event): event for event in coverage}.values(), key=event_key)
    )
    return _Snapshot(tuple(completed), MappingProxyType(pipelines), events)


class NextJSLifecycleAdapter:
    framework = "nextjs"

    def __init__(self, language: str = "typescript") -> None:
        if language not in _LANGUAGES:
            raise ValueError("Next.js language must be javascript or typescript")
        self.language = language
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
            self._snapshot_key(context), _Snapshot((), MappingProxyType({}), ())
        )

    def _remember(self, context: ExtractionContext, snapshot: _Snapshot) -> None:
        values = dict(self._snapshots)
        values[self._snapshot_key(context)] = snapshot
        self._snapshots = MappingProxyType(values)

    def detect(self, context: ExtractionContext) -> FrameworkDetection:
        version, _provenance = _detected_version(context, self.language)
        detected = version is not None or any(
            row.language == self.language and row.name == self.framework
            for row in context.detected_frameworks
        )
        return FrameworkDetection(self.language, self.framework, detected)

    def entrypoints(
        self, context: ExtractionContext, syntax: Sequence[SyntaxIR]
    ) -> tuple[EntrypointCandidate, ...]:
        relevant = tuple(
            sorted(
                (item for item in syntax if item.language == self.language),
                key=lambda item: item.path,
            )
        )
        snapshot = _build_snapshot(context, relevant, self.language)
        self._remember(context, snapshot)
        return snapshot.candidates

    def pipeline(
        self, context: ExtractionContext, candidate: EntrypointCandidate
    ) -> tuple[FrameworkPipelineSegment, ...]:
        return self._snapshot(context).pipelines.get(_candidate_key(candidate), ())

    def coverage_events(self, context: ExtractionContext) -> tuple[CoverageEvent, ...]:
        return self._snapshot(context).coverage_events


__all__ = ["NextJSLifecycleAdapter"]
