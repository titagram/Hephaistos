"""Required, bounded Tree-sitter adapter for Hades graph enrichment.

The adapter deliberately returns only symbol metadata. Source bytes and parser
trees stay local to one call and are never retained in the returned value.
"""

from __future__ import annotations

import importlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, TypeAlias

from hermes_cli.hades_index.lifecycle.model import (
    CoverageCapability,
    CoverageEvent,
    CoverageOutcome,
)


_SAFE_NAME_RE = re.compile(r"^[A-Za-z_.$\\][A-Za-z0-9_.$\\/:@>~\-]*$")
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
    "javascript": ("tree_sitter_javascript", "language"),
    "php": ("tree_sitter_php", "language_php"),
    "python": ("tree_sitter_python", "language"),
}
_LANGUAGE_CANARIES = {
    "javascript": b"function hadesCanary() { return 1; }\n",
    "typescript": b"function hadesCanary(): number { return 1; }\n",
    "php": b"<?php function hades_canary(): int { return 1; }\n",
    "python": b"def hades_canary() -> int:\n    return 1\n",
}

SyntaxControlKind: TypeAlias = Literal[
    "branch",
    "merge",
    "loop",
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
    "merge",
    "loop",
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


@dataclass(frozen=True, slots=True)
class StructuralImport:
    target: str
    line: int


@dataclass(frozen=True, slots=True)
class StructuralCall:
    caller: str
    target: str
    line: int


@dataclass(frozen=True, slots=True)
class ParsedFile:
    path: str
    language: str
    symbols: tuple[StructuralSymbol, ...]
    imports: tuple[StructuralImport, ...]
    calls: tuple[StructuralCall, ...]


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
            "required structural parser unavailable for: "
            + ", ".join(self.languages)
        )


def _point_row(point: Any) -> int:
    if hasattr(point, "row"):
        return int(point.row)
    return int(point[0])


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
        return self._parser(language) is not None

    def require_languages(self, languages: Iterable[str]) -> None:
        """Fail atomically when a detected supported grammar cannot really parse."""

        failed: list[str] = []
        required = tuple(sorted({item for item in languages if item in _LANGUAGE_CANARIES}))
        for language in required:
            parser = self._parser(language)
            if parser is None:
                failed.append(language)
                continue
            try:
                tree = parser.parse(_LANGUAGE_CANARIES[language])
                root = tree.root_node
                if bool(getattr(root, "has_error", True)):
                    failed.append(language)
            except Exception:
                failed.append(language)
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
        parser = self._parser(language)
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

            def add_control(
                kind: SyntaxControlKind,
                structural_path: str,
                owner_structural_path: str,
                node: Any,
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
                    )
                )

            def visit(
                node: Any,
                context: str = "",
                container: str = "",
                structural_path: str = "root",
                owner_structural_path: str = "root",
            ) -> None:
                node_type = str(getattr(node, "type", ""))
                next_context = context
                next_container = container
                next_owner = (
                    structural_path
                    if node_type in _CALLABLE_TYPES.get(language, ())
                    else owner_structural_path
                )
                symbol_kind = symbol_types.get(node_type)
                if symbol_kind:
                    name = _bounded_node_text(source, _field(node, "name"))
                    if name:
                        qualified = (
                            f"{container}.{name}"
                            if symbol_kind == "method" and container
                            else name
                        )
                        symbols.append(
                            StructuralSymbol(
                                name=qualified,
                                kind=symbol_kind,
                                line=_point_row(node.start_point) + 1,
                                end_line=_point_row(node.end_point) + 1,
                                container=container,
                                structural_path=structural_path,
                            )
                        )
                        next_context = qualified
                        if symbol_kind in {"class", "interface", "trait", "enum"}:
                            next_container = name
                if node_type in _IMPORT_TYPES:
                    target = _bounded_node_text(source, _field(node, "source", "name"))
                    if target:
                        imports.append(
                            StructuralImport(
                                target=target, line=_point_row(node.start_point) + 1
                            )
                        )
                if node_type in _CALL_TYPES and context:
                    target = _bounded_node_text(
                        source, _field(node, "function", "name", "member")
                    )
                    if target:
                        calls.append(
                            StructuralCall(
                                caller=context,
                                target=target,
                                line=_point_row(node.start_point) + 1,
                            )
                        )
                node_is_named = bool(getattr(node, "is_named", True))
                control_kind = _control_kind(node_type) if node_is_named else None
                if control_kind is not None:
                    add_control(control_kind, structural_path, next_owner, node)
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
                        next_container,
                        f"{structural_path}/{child_type}/{child_ordinal}",
                        next_owner,
                    )

            visit(root)
            parsed = ParsedFile(
                path=path,
                language=language,
                symbols=tuple(symbols),
                imports=tuple(imports),
                calls=tuple(calls),
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
