"""Required, bounded Tree-sitter adapter for Hades graph enrichment.

The adapter deliberately returns only symbol metadata. Source bytes and parser
trees stay local to one call and are never retained in the returned value.
"""

from __future__ import annotations

import importlib
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, TypeAlias

from hermes_cli.hades_index.lifecycle.model import (
    CoverageCapability,
    CoverageEvent,
    CoverageOutcome,
    local_record_key,
)
from hermes_cli.hades_resource_privacy import (
    is_platform_absolute_semantic_resource_path,
    is_sensitive_semantic_resource_component,
)


_SAFE_NAME_RE = re.compile(r"^[A-Za-z_.$\\][A-Za-z0-9_.$\\/:@>~\-]*$")
_SAFE_CALL_LITERAL_RE = re.compile(r"^[A-Za-z0-9._:/\-]{1,256}$")
_SAFE_REFERENCE_RE = re.compile(r"^\\?[A-Za-z_][A-Za-z0-9_\\]*$")
_PRIVATE_LITERAL_RE = re.compile(
    r"(?i)(?:^sk[_-]|^eyJ[A-Za-z0-9_-]{8,}|(?:api[_-]?key|access[_-]?token|"
    r"auth(?:orization)?|secret|password|bearer)(?:[_:-]|$))"
)
_LARAVEL_FACADE_PREFIX = "Illuminate\\Support\\Facades\\"
_LARAVEL_FACADES = frozenset({
    "DB",
    "Cache",
    "Storage",
    "Http",
    "Mail",
    "Notification",
    "Event",
    "Bus",
    "Queue",
})
_SYMBOL_TYPES = {
    "typescript": {
        "function_declaration": "function",
        "generator_function_declaration": "function",
        "class_declaration": "class",
        "interface_declaration": "interface",
        "type_alias_declaration": "type",
        "enum_declaration": "enum",
        "method_definition": "method",
    },
    "python": {
        "function_definition": "function",
        "async_function_definition": "function",
        "class_definition": "class",
    },
    "javascript": {
        "function_declaration": "function",
        "generator_function_declaration": "function",
        "class_declaration": "class",
        "method_definition": "method",
    },
    "php": {
        "function_definition": "function",
        "class_declaration": "class",
        "interface_declaration": "interface",
        "trait_declaration": "trait",
        "enum_declaration": "enum",
        "method_declaration": "method",
    },
}
_TYPE_SYMBOL_KINDS = frozenset({"class", "interface", "trait", "enum"})
_CALL_TYPES = {
    "call_expression",
    "function_call_expression",
    "member_call_expression",
    "nullsafe_member_call_expression",
    "scoped_call_expression",
    "call",
}
_IMPORT_TYPES = {
    "import_statement",
    "namespace_use_declaration",
}
_CALLABLE_TYPES = {
    "typescript": frozenset({
        "function_declaration",
        "generator_function_declaration",
        "function_expression",
        "generator_function",
        "arrow_function",
        "method_definition",
    }),
    "javascript": frozenset({
        "function_declaration",
        "generator_function_declaration",
        "function_expression",
        "generator_function",
        "arrow_function",
        "method_definition",
    }),
    "python": frozenset({"function_definition", "lambda"}),
    "php": frozenset({
        "function_definition",
        "method_declaration",
        "anonymous_function",
        "arrow_function",
    }),
}
_GRAMMAR_FACTORIES = {
    "typescript": ("tree_sitter_typescript", "language_typescript"),
    "tsx": ("tree_sitter_typescript", "language_tsx"),
    "javascript": ("tree_sitter_javascript", "language"),
    "php": ("tree_sitter_php", "language_php"),
    "python": ("tree_sitter_python", "language"),
}
_LANGUAGE_CANARIES = {
    "javascript": b"function hadesCanary() { return 1; }\n",
    "typescript": b"function hadesCanary(): number { return 1; }\n",
    "tsx": b"function HadesCanary() { return <main>ok</main>; }\n",
    "php": b"<?php function hades_canary(): int { return 1; }\n",
    "python": b"def hades_canary() -> int:\n    return 1\n",
}
_LANGUAGE_VARIANTS = {
    "javascript": ("javascript",),
    "typescript": ("typescript", "tsx"),
    "php": ("php",),
    "python": ("python",),
}

SyntaxControlKind: TypeAlias = Literal[
    "branch",
    "branch_arm",
    "merge",
    "loop",
    "loop_body",
    "return",
    "throw",
    "try",
    "catch",
    "finally",
    "async_dispatch",
    "call",
]
ParseFailureCode: TypeAlias = Literal[
    "parser_unavailable",
    "parser_failed",
    "file_too_large",
    "file_read_failed",
]
_SYNTAX_CONTROL_KINDS = frozenset({
    "branch",
    "branch_arm",
    "merge",
    "loop",
    "loop_body",
    "return",
    "throw",
    "try",
    "catch",
    "finally",
    "async_dispatch",
    "call",
})
_PARSE_FAILURE_CODES = frozenset({
    "parser_unavailable",
    "parser_failed",
    "file_too_large",
    "file_read_failed",
})


@dataclass(frozen=True, slots=True)
class StructuralSymbol:
    name: str
    kind: str
    line: int
    end_line: int
    container: str = ""
    structural_path: str = "root"
    namespace: str | None = None


@dataclass(frozen=True, slots=True)
class StructuralImport:
    target: str
    line: int
    alias: str | None = None
    namespace: str | None = None


CallArgumentKind: TypeAlias = Literal["literal", "class_reference", "unknown"]


@dataclass(frozen=True, slots=True)
class StructuralCallArgument:
    """A bounded argument fact which cannot retain raw source or secrets."""

    kind: CallArgumentKind
    value: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"literal", "class_reference", "unknown"}:
            raise ValueError("structural call argument kind is not supported")
        if self.kind == "unknown" and self.value is not None:
            raise ValueError("unknown structural argument cannot retain a value")
        if self.kind != "unknown" and (
            not isinstance(self.value, str) or not self.value or len(self.value) > 256
        ):
            raise ValueError("structural argument fact must be bounded and non-empty")


@dataclass(frozen=True, slots=True)
class StructuralCall:
    caller: str
    target: str
    line: int
    argument_count: int = 0
    structural_path: str = "root"
    call_form: Literal["function", "scoped", "member", "nullsafe_member"] = "function"
    receiver: str | None = None
    member: str | None = None
    arguments: tuple[StructuralCallArgument, ...] = ()
    receiver_chain_key: str = "root"


@dataclass(frozen=True, slots=True)
class ParsedFile:
    path: str
    language: str
    symbols: tuple[StructuralSymbol, ...]
    imports: tuple[StructuralImport, ...]
    calls: tuple[StructuralCall, ...]
    namespace: str | None = None


@dataclass(frozen=True, slots=True)
class SyntaxControl:
    """A source-free, finite control fact retained from one parsed syntax tree."""

    kind: SyntaxControlKind
    structural_path: str
    line: int
    end_line: int
    owner_structural_path: str = "root"
    start_byte: int = 0
    end_byte: int = 0
    parent_control_path: str | None = None
    arm_polarity: Literal["true", "false"] | None = None

    def __post_init__(self) -> None:
        if self.kind not in _SYNTAX_CONTROL_KINDS:
            raise ValueError("syntax control kind must be a closed variant")
        if not self.structural_path or self.structural_path.startswith("/"):
            raise ValueError("syntax control must use a relative structural path")
        if not self.owner_structural_path or self.owner_structural_path.startswith("/"):
            raise ValueError("syntax control owner must use a relative structural path")
        if self.line < 1 or self.end_line < self.line:
            raise ValueError("syntax control must have positive ordered lines")
        if self.start_byte < 0 or self.end_byte < self.start_byte:
            raise ValueError("syntax control must have non-negative ordered bytes")
        if self.kind == "branch_arm":
            if self.parent_control_path is None or self.arm_polarity is None:
                raise ValueError("branch arm requires its exact parent and polarity")
        elif self.kind == "loop_body":
            if self.parent_control_path is None or self.arm_polarity is not None:
                raise ValueError("loop body requires only its exact parent")
        elif self.parent_control_path is not None or self.arm_polarity is not None:
            raise ValueError("only branch arms may carry parent and polarity")


@dataclass(frozen=True, slots=True)
class SyntaxIR:
    """Closed parse output: metadata plus language-neutral control facts.

    It intentionally has no source bytes, native tree, exception object, or
    open metadata payload.  A later language adapter translates these facts
    into frozen lifecycle IR records.
    """

    parsed_file: ParsedFile
    controls: tuple[SyntaxControl, ...]

    @property
    def path(self) -> str:
        return self.parsed_file.path

    @property
    def language(self) -> str:
        return self.parsed_file.language

    @property
    def symbols(self) -> tuple[StructuralSymbol, ...]:
        return self.parsed_file.symbols

    @property
    def imports(self) -> tuple[StructuralImport, ...]:
        return self.parsed_file.imports

    @property
    def calls(self) -> tuple[StructuralCall, ...]:
        return self.parsed_file.calls

    @property
    def namespace(self) -> str | None:
        return self.parsed_file.namespace


def declaration_local_key(
    language: str,
    path: str,
    symbol: StructuralSymbol,
    ordinal: int,
) -> str:
    """Return the single local identity shared by every v2 language adapter."""

    structural_path = (
        f"symbol/{symbol.name}"
        if symbol.structural_path == "root"
        else symbol.structural_path
    )
    return local_record_key(
        language,
        path,
        "executable_declaration",
        "ast",
        structural_path,
        ordinal,
    )


@dataclass(frozen=True, slots=True)
class ParseFailure:
    """Privacy-safe parse failure; raw exceptions and source never leave parsing."""

    code: ParseFailureCode
    path: str
    language: str

    def __post_init__(self) -> None:
        if self.code not in _PARSE_FAILURE_CODES:
            raise ValueError("parse failure code must be a closed variant")


@dataclass(frozen=True, slots=True)
class ParseResult:
    """A parser outcome is never represented by a successful ``None`` value."""

    status: Literal["parsed", "failed"]
    syntax: SyntaxIR | None
    failure: ParseFailure | None
    coverage_event: CoverageEvent | None

    def __post_init__(self) -> None:
        if self.status not in {"parsed", "failed"}:
            raise ValueError("parse result status must be a closed variant")
        parsed = self.status == "parsed"
        if parsed != (self.syntax is not None) or parsed == (self.failure is not None):
            raise ValueError("parse result must contain exactly syntax or failure")
        if parsed != (self.coverage_event is None):
            raise ValueError("only failed parsing may carry a coverage event")

    @classmethod
    def parsed(cls, syntax: SyntaxIR) -> "ParseResult":
        return cls("parsed", syntax, None, None)

    @classmethod
    def failed(cls, code: ParseFailureCode, path: str, language: str) -> "ParseResult":
        failure = ParseFailure(code, path, language)
        return cls(
            "failed",
            None,
            failure,
            CoverageEvent(
                language=language,
                capability=CoverageCapability.CONTROL_FLOW,
                outcome=CoverageOutcome.PARTIAL,
                reason_code=code,
                path=path,
                represented_count=0,
                omitted_count=1,
            ),
        )


ParserLoader = Callable[[str], Any | None]


class RequiredParserUnavailable(RuntimeError):
    """Required parser installation failed its bounded language canary."""

    def __init__(self, languages: Iterable[str]) -> None:
        self.languages = tuple(sorted(set(languages)))
        super().__init__(
            "required structural parser unavailable for: " + ", ".join(self.languages)
        )


def _point_row(point: Any) -> int:
    try:
        return int(point[0])
    except (IndexError, KeyError, TypeError):
        return int(point.row)


def _bounded_node_text(source: bytes, node: Any | None, *, limit: int = 512) -> str:
    if node is None:
        return ""
    start = max(0, int(getattr(node, "start_byte", 0)))
    end = min(len(source), int(getattr(node, "end_byte", start)), start + limit)
    if end <= start:
        return ""
    value = source[start:end].decode("utf-8", errors="replace").strip().strip("'\"")
    if (
        not value
        or "\n" in value
        or "\r" in value
        or not _SAFE_NAME_RE.fullmatch(value)
    ):
        return ""
    return value


def _field(node: Any, *names: str) -> Any | None:
    child_by_field_name = getattr(node, "child_by_field_name", None)
    if not callable(child_by_field_name):
        return None
    for name in names:
        child = child_by_field_name(name)
        if child is not None:
            return child
    return None


def _safe_reference(source: bytes, node: Any | None) -> str | None:
    value = _bounded_node_text(source, node)
    return value if value and _SAFE_REFERENCE_RE.fullmatch(value) else None


def _safe_literal(source: bytes, node: Any | None) -> str | None:
    """Return only a privacy-safe static literal retained in parse facts."""

    if node is None or str(getattr(node, "type", "")) != "string":
        return None
    start = max(0, int(getattr(node, "start_byte", 0)))
    end = min(len(source), int(getattr(node, "end_byte", start)))
    if end <= start:
        return None
    raw = source[start:end].decode("utf-8", errors="replace").strip()
    if len(raw) < 2 or raw[0] not in {"'", '"'} or raw[-1] != raw[0]:
        return None
    value = raw[1:-1]
    if not _SAFE_CALL_LITERAL_RE.fullmatch(value):
        return None
    if _PRIVATE_LITERAL_RE.search(value):
        return None
    if is_platform_absolute_semantic_resource_path(value) or any(
        segment in {".", ".."}
        or is_sensitive_semantic_resource_component(segment)
        for segment in re.split(r"[/:]", value)
    ):
        return None
    if value.startswith(("http://", "https://")) and any(
        marker in value for marker in ("?", "#", "@")
    ):
        return None
    return value


def _argument_fact(
    source: bytes, node: Any, *, retain_literal: bool
) -> StructuralCallArgument:
    value = next(iter(getattr(node, "named_children", ())), None)
    if value is None:
        return StructuralCallArgument("unknown")
    literal = _safe_literal(source, value) if retain_literal else None
    if literal is not None:
        return StructuralCallArgument("literal", literal)
    if str(getattr(value, "type", "")) == "object_creation_expression":
        reference = _safe_reference(
            source,
            _field(value, "name", "class")
            or next(iter(getattr(value, "named_children", ())), None),
        )
        if reference is not None:
            return StructuralCallArgument("class_reference", reference)
    if str(getattr(value, "type", "")) == "class_constant_access":
        name = _bounded_node_text(source, _field(value, "name"))
        reference = _safe_reference(source, _field(value, "scope", "class"))
        if name == "class" and reference is not None:
            return StructuralCallArgument("class_reference", reference)
    return StructuralCallArgument("unknown")


def _call_root_receiver(source: bytes, node: Any | None) -> str | None:
    """Return the statically named root of a chained PHP call, if any."""

    if node is None:
        return None
    node_type = str(getattr(node, "type", ""))
    if node_type == "scoped_call_expression":
        return _safe_reference(source, _field(node, "scope"))
    if node_type in {"member_call_expression", "nullsafe_member_call_expression"}:
        return _call_root_receiver(source, _field(node, "object"))
    return _safe_reference(source, node)


def _structural_child_path(parent: Any, parent_path: str, target: Any) -> str:
    counts: dict[str, int] = {}
    for child in getattr(parent, "children", ()):
        child_type = str(getattr(child, "type", "node"))
        ordinal = counts.get(child_type, 0)
        counts[child_type] = ordinal + 1
        if child == target:
            return f"{parent_path}/{child_type}/{ordinal}"
    return parent_path


def _receiver_chain_key(node: Any, structural_path: str) -> str:
    """Return the root call occurrence of one fluent receiver chain."""

    object_node = _field(node, "object")
    if str(getattr(object_node, "type", "")) not in _CALL_TYPES:
        return structural_path
    return _receiver_chain_key(
        object_node,
        _structural_child_path(node, structural_path, object_node),
    )


def _call_fields(
    source: bytes, node: Any, structural_path: str
) -> tuple[
    Literal["function", "scoped", "member", "nullsafe_member"],
    str | None,
    str | None,
    tuple[StructuralCallArgument, ...],
    str,
]:
    """Translate a native call node into closed, source-free facts."""

    node_type = str(getattr(node, "type", ""))
    if node_type == "function_call_expression":
        form: Literal["function", "scoped", "member", "nullsafe_member"] = "function"
        receiver = None
        member = _safe_reference(source, _field(node, "function", "name"))
    elif node_type == "scoped_call_expression":
        form = "scoped"
        receiver = _safe_reference(source, _field(node, "scope"))
        member = _safe_reference(source, _field(node, "name", "member"))
    elif node_type == "nullsafe_member_call_expression":
        form = "nullsafe_member"
        receiver = _call_root_receiver(source, _field(node, "object"))
        member = _safe_reference(source, _field(node, "name", "member"))
    else:
        form = "member"
        receiver = _call_root_receiver(source, _field(node, "object"))
        member = _safe_reference(source, _field(node, "name", "member"))
    arguments = tuple(
        _argument_fact(
            source,
            child,
            retain_literal=index == 0 or (member in {"send", "request"} and index == 1),
        )
        for index, child in enumerate(
            getattr(_field(node, "arguments"), "named_children", ())
        )
    )
    return form, receiver, member, arguments, _receiver_chain_key(node, structural_path)


def _canonical_laravel_facade(target: str) -> str | None:
    normalized = target.lstrip("\\")
    if not normalized.startswith(_LARAVEL_FACADE_PREFIX):
        return None
    facade = normalized[len(_LARAVEL_FACADE_PREFIX) :]
    return facade if facade in _LARAVEL_FACADES else None


def _sanitize_php_call_literals(
    calls: tuple[StructuralCall, ...],
    imports: tuple[StructuralImport, ...],
    symbols: tuple[StructuralSymbol, ...],
) -> tuple[StructuralCall, ...]:
    """Retain literals only for unshadowed candidate Laravel resource APIs."""

    aliases: dict[str, str] = {}
    occupied: set[str] = set()
    for imported in imports:
        visible = imported.alias or imported.target.rsplit("\\", 1)[-1]
        occupied.add(visible)
        facade = _canonical_laravel_facade(imported.target)
        if facade is not None:
            aliases[visible] = facade
            aliases[imported.target] = facade
            aliases[f"\\{imported.target}"] = facade
    local_names = {
        symbol.name.rsplit(".", 1)[-1]
        for symbol in symbols
        if symbol.kind in {"class", "interface", "trait", "enum"}
    }

    def facade_for(receiver: str | None) -> str | None:
        if receiver is None:
            return None
        if receiver in aliases:
            return aliases[receiver]
        facade = _canonical_laravel_facade(receiver)
        if facade is not None:
            return facade
        if receiver in _LARAVEL_FACADES and receiver not in occupied | local_names:
            return receiver
        return None

    sanitized: list[StructuralCall] = []
    for call in calls:
        facade = facade_for(call.receiver)
        retained_indexes: frozenset[int] = frozenset()
        if facade == "DB" and call.member == "table":
            retained_indexes = frozenset({0})
        elif facade in {"Cache", "Storage"}:
            retained_indexes = frozenset({0})
        elif facade == "Http" and call.member in {
            "get", "post", "put", "patch", "delete", "request"
        }:
            retained_indexes = frozenset({1 if call.member == "request" else 0})
        arguments = tuple(
            argument
            if argument.kind == "class_reference" or index in retained_indexes
            else StructuralCallArgument("unknown")
            for index, argument in enumerate(call.arguments)
        )
        sanitized.append(replace(call, arguments=arguments))
    return tuple(sanitized)


def _load_parser(language: str) -> Any | None:
    module_spec = _GRAMMAR_FACTORIES.get(language)
    if module_spec is None:
        return None
    try:
        tree_sitter = importlib.import_module("tree_sitter")
        grammar = importlib.import_module(module_spec[0])
        language_factory = getattr(grammar, module_spec[1])
        language_object = tree_sitter.Language(language_factory())
        return tree_sitter.Parser(language_object)
    except (ImportError, AttributeError, TypeError, ValueError):
        return None


class TreeSitterAdapter:
    """Load bundled grammars on demand and extract source-free finite facts."""

    def __init__(self, parser_loader: ParserLoader = _load_parser) -> None:
        self._parser_loader = parser_loader
        self._parsers: dict[str, Any | None] = {}

    def _parser(self, language: str) -> Any | None:
        if language not in self._parsers:
            try:
                self._parsers[language] = self._parser_loader(language)
            except Exception:
                self._parsers[language] = None
        return self._parsers[language]

    def is_available(self, language: str) -> bool:
        variants = _LANGUAGE_VARIANTS.get(language, (language,))
        return all(self._parser(variant) is not None for variant in variants)

    def require_languages(self, languages: Iterable[str]) -> None:
        """Fail atomically when a detected supported grammar cannot really parse."""

        failed: list[str] = []
        required = tuple(
            sorted({item for item in languages if item in _LANGUAGE_VARIANTS})
        )
        for language in required:
            for variant in _LANGUAGE_VARIANTS[language]:
                parser = self._parser(variant)
                if parser is None:
                    failed.append(language)
                    break
                try:
                    tree = parser.parse(_LANGUAGE_CANARIES[variant])
                    root = tree.root_node
                    if bool(getattr(root, "has_error", True)):
                        failed.append(language)
                        break
                except Exception:
                    failed.append(language)
                    break
        if failed:
            raise RequiredParserUnavailable(failed)

    def parse_file(
        self,
        path: Path,
        *,
        relative_path: str,
        language: str,
        max_bytes: int,
    ) -> ParseResult:
        try:
            if path.stat().st_size > max_bytes:
                return ParseResult.failed("file_too_large", relative_path, language)
            with path.open("rb") as handle:
                source = handle.read(max_bytes + 1)
            if len(source) > max_bytes:
                return ParseResult.failed("file_too_large", relative_path, language)
            return self.parse_bytes(source, path=relative_path, language=language)
        except OSError:
            return ParseResult.failed("file_read_failed", relative_path, language)

    def parse_bytes(self, source: bytes, *, path: str, language: str) -> ParseResult:
        parser_language = (
            "tsx"
            if language == "typescript" and Path(path).suffix.lower() == ".tsx"
            else language
        )
        parser = self._parser(parser_language)
        if parser is None:
            return ParseResult.failed("parser_unavailable", path, language)
        tree = None
        try:
            tree = parser.parse(source)
            root = tree.root_node
            if bool(getattr(root, "has_error", False)):
                return ParseResult.failed("parser_failed", path, language)
            symbols: list[StructuralSymbol] = []
            imports: list[StructuralImport] = []
            calls: list[StructuralCall] = []
            controls: list[SyntaxControl] = []
            symbol_types = _SYMBOL_TYPES.get(language, {})
            file_namespace = (
                next(
                    (
                        _safe_reference(source, _field(node, "name"))
                        for node in getattr(root, "named_children", ())
                        if str(getattr(node, "type", "")) == "namespace_definition"
                    ),
                    None,
                )
                if language == "php"
                else None
            )

            def add_control(
                kind: SyntaxControlKind,
                structural_path: str,
                owner_structural_path: str,
                node: Any,
                *,
                parent_control_path: str | None = None,
                arm_polarity: Literal["true", "false"] | None = None,
            ) -> None:
                controls.append(
                    SyntaxControl(
                        kind=kind,
                        structural_path=structural_path,
                        line=_point_row(node.start_point) + 1,
                        end_line=_point_row(node.end_point) + 1,
                        owner_structural_path=owner_structural_path,
                        start_byte=max(0, int(getattr(node, "start_byte", 0))),
                        end_byte=max(0, int(getattr(node, "end_byte", 0))),
                        parent_control_path=parent_control_path,
                        arm_polarity=arm_polarity,
                    )
                )

            def child_path(parent: Any, parent_path: str, target: Any) -> str:
                counts: dict[str, int] = {}
                for child in getattr(parent, "children", ()):
                    child_type = str(getattr(child, "type", "node"))
                    ordinal = counts.get(child_type, 0)
                    counts[child_type] = ordinal + 1
                    if child == target:
                        return f"{parent_path}/{child_type}/{ordinal}"
                raise ValueError("Tree-sitter field node is not a direct child")

            def visit(
                node: Any,
                context: str = "",
                lexical_owner: str = "",
                lexical_owner_kind: str = "",
                structural_path: str = "root",
                owner_structural_path: str = "root",
                namespace: str | None = None,
            ) -> None:
                node_type = str(getattr(node, "type", ""))
                next_context = context
                next_lexical_owner = lexical_owner
                next_lexical_owner_kind = lexical_owner_kind
                next_namespace = namespace
                if language == "php" and node_type == "namespace_definition":
                    next_namespace = _safe_reference(source, _field(node, "name"))
                next_owner = (
                    structural_path
                    if node_type in _CALLABLE_TYPES.get(language, ())
                    else owner_structural_path
                )
                symbol_kind = symbol_types.get(node_type)
                if symbol_kind:
                    name = _bounded_node_text(source, _field(node, "name"))
                    if name:
                        qualified = f"{lexical_owner}.{name}" if lexical_owner else name
                        container = (
                            lexical_owner
                            if lexical_owner_kind in _TYPE_SYMBOL_KINDS
                            else ""
                        )
                        symbols.append(
                            StructuralSymbol(
                                name=qualified,
                                kind=symbol_kind,
                                line=_point_row(node.start_point) + 1,
                                end_line=_point_row(node.end_point) + 1,
                                container=container,
                                structural_path=structural_path,
                                namespace=namespace,
                            )
                        )
                        next_context = qualified
                        next_lexical_owner = qualified
                        next_lexical_owner_kind = symbol_kind
                if (
                    language in {"javascript", "typescript"}
                    and node_type == "variable_declarator"
                ):
                    value = _field(node, "value")
                    value_type = str(getattr(value, "type", ""))
                    name = _bounded_node_text(source, _field(node, "name"))
                    if value_type in _CALLABLE_TYPES[language] and name:
                        value_path = child_path(node, structural_path, value)
                        qualified = f"{lexical_owner}.{name}" if lexical_owner else name
                        symbols.append(
                            StructuralSymbol(
                                name=qualified,
                                kind="function",
                                line=_point_row(value.start_point) + 1,
                                end_line=_point_row(value.end_point) + 1,
                                container=(
                                    lexical_owner
                                    if lexical_owner_kind in _TYPE_SYMBOL_KINDS
                                    else ""
                                ),
                                structural_path=value_path,
                            )
                        )
                        next_context = qualified
                        next_lexical_owner = qualified
                        next_lexical_owner_kind = "function"
                if node_type in _IMPORT_TYPES:
                    if language == "php" and node_type == "namespace_use_declaration":
                        for clause in getattr(node, "named_children", ()):
                            if (
                                str(getattr(clause, "type", ""))
                                != "namespace_use_clause"
                            ):
                                continue
                            parts = tuple(getattr(clause, "named_children", ()))
                            target = _safe_reference(
                                source, parts[0] if parts else None
                            )
                            alias = _safe_reference(
                                source, parts[-1] if len(parts) > 1 else None
                            )
                            if target:
                                imports.append(
                                    StructuralImport(
                                        target=target,
                                        line=_point_row(clause.start_point) + 1,
                                        alias=alias,
                                        namespace=namespace,
                                    )
                                )
                    else:
                        target = _bounded_node_text(
                            source, _field(node, "source", "name")
                        )
                        if target:
                            imports.append(
                                StructuralImport(
                                    target=target,
                                    line=_point_row(node.start_point) + 1,
                                    namespace=namespace,
                                )
                            )
                if node_type in _CALL_TYPES and context:
                    call_form, receiver, member, arguments, receiver_chain_key = _call_fields(
                        source, node, structural_path
                    )
                    target = member or _bounded_node_text(
                        source, _field(node, "function", "name", "member")
                    )
                    if target:
                        calls.append(
                            StructuralCall(
                                caller=context,
                                target=target,
                                line=_point_row(node.start_point) + 1,
                                argument_count=len(arguments),
                                structural_path=structural_path,
                                call_form=call_form,
                                receiver=receiver,
                                member=member,
                                arguments=arguments,
                                receiver_chain_key=receiver_chain_key,
                            )
                        )
                node_is_named = bool(getattr(node, "is_named", True))
                control_kind = _control_kind(node_type) if node_is_named else None
                if control_kind is not None:
                    add_control(control_kind, structural_path, next_owner, node)
                    if control_kind == "branch":
                        consequence = _field(node, "consequence", "body")
                        alternative = _field(node, "alternative")
                        if consequence is not None:
                            add_control(
                                "branch_arm",
                                child_path(node, structural_path, consequence),
                                next_owner,
                                consequence,
                                parent_control_path=structural_path,
                                arm_polarity="true",
                            )
                        if alternative is not None:
                            add_control(
                                "branch_arm",
                                child_path(node, structural_path, alternative),
                                next_owner,
                                alternative,
                                parent_control_path=structural_path,
                                arm_polarity="false",
                            )
                    if control_kind == "loop":
                        body = _field(node, "body")
                        if body is not None:
                            add_control(
                                "loop_body",
                                child_path(node, structural_path, body),
                                next_owner,
                                body,
                                parent_control_path=structural_path,
                            )
                    if control_kind in {"branch", "loop", "try"}:
                        # This is a structural convergence marker, not an
                        # inferred runtime path.  CFG construction later owns
                        # the concrete continuation block.
                        add_control(
                            "merge",
                            f"{structural_path}/merge",
                            next_owner,
                            node,
                        )
                if node_is_named and node_type in _CALL_TYPES:
                    add_control("call", structural_path, next_owner, node)
                child_type_counts: dict[str, int] = {}
                for child in getattr(node, "children", ()):
                    child_type = str(getattr(child, "type", "node"))
                    child_ordinal = child_type_counts.get(child_type, 0)
                    child_type_counts[child_type] = child_ordinal + 1
                    visit(
                        child,
                        next_context,
                        next_lexical_owner,
                        next_lexical_owner_kind,
                        f"{structural_path}/{child_type}/{child_ordinal}",
                        next_owner,
                        next_namespace,
                    )

            visit(root, namespace=file_namespace)

            parsed = ParsedFile(
                path=path,
                language=language,
                symbols=tuple(symbols),
                imports=tuple(imports),
                calls=(
                    _sanitize_php_call_literals(tuple(calls), tuple(imports), tuple(symbols))
                    if language == "php"
                    else tuple(
                        replace(
                            call,
                            arguments=tuple(
                                StructuralCallArgument("unknown")
                                if argument.kind == "literal"
                                else argument
                                for argument in call.arguments
                            ),
                        )
                        for call in calls
                    )
                ),
                namespace=file_namespace,
            )
            return ParseResult.parsed(SyntaxIR(parsed, tuple(controls)))
        except Exception:
            return ParseResult.failed("parser_failed", path, language)
        finally:
            # Keep neither the source buffer nor native tree alive across files.
            del tree


def _control_kind(node_type: str) -> SyntaxControlKind | None:
    """Map grammar node spellings to the finite language-neutral syntax set."""

    if node_type in {
        "if_statement",
        "switch_statement",
        "match_statement",
        "conditional_expression",
        "ternary_expression",
    }:
        return "branch"
    if node_type in {
        "for_statement",
        "while_statement",
        "foreach_statement",
        "for_in_statement",
        "do_statement",
    }:
        return "loop"
    if node_type in {"return_statement", "return"}:
        return "return"
    if node_type in {"throw_statement", "raise_statement"}:
        return "throw"
    if node_type in {"try_statement", "try"}:
        return "try"
    if node_type in {"catch_clause", "except_clause"}:
        return "catch"
    if node_type in {"finally_clause", "finally"}:
        return "finally"
    if node_type in {"await_expression", "await"}:
        return "async_dispatch"
    return None
