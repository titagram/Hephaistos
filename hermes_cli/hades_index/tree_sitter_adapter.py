"""Optional, bounded Tree-sitter adapter for Hades graph enrichment.

The adapter deliberately returns only symbol metadata. Source bytes and parser
trees stay local to one call and are never retained in the returned value.
"""

from __future__ import annotations

import importlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


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
}
_IMPORT_TYPES = {
    "import_statement",
    "namespace_use_declaration",
}


@dataclass(frozen=True, slots=True)
class StructuralSymbol:
    name: str
    kind: str
    line: int
    end_line: int
    container: str = ""


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


ParserLoader = Callable[[str], Any | None]


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
    if not value or "\n" in value or "\r" in value or not _SAFE_NAME_RE.fullmatch(value):
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
    grammar_name = "typescript" if language == "typescript" else language
    for module_name in ("tree_sitter_language_pack", "tree_sitter_languages"):
        try:
            module = importlib.import_module(module_name)
            return module.get_parser(grammar_name)
        except Exception:
            continue

    grammar_modules = {
        "typescript": ("tree_sitter_typescript", "language_typescript"),
        "javascript": ("tree_sitter_javascript", "language"),
        "php": ("tree_sitter_php", "language_php"),
    }
    module_spec = grammar_modules.get(language)
    if module_spec is None:
        return None
    try:
        tree_sitter = importlib.import_module("tree_sitter")
        grammar = importlib.import_module(module_spec[0])
        language_factory = getattr(grammar, module_spec[1], None) or getattr(grammar, "language")
        language_object = language_factory()
        try:
            language_object = tree_sitter.Language(language_object)
        except TypeError:
            pass
        parser = tree_sitter.Parser()
        if hasattr(parser, "set_language"):
            parser.set_language(language_object)
        else:
            parser.language = language_object
        return parser
    except (ImportError, AttributeError, TypeError, ValueError):
        return None


class TreeSitterAdapter:
    """Load grammars lazily and extract privacy-safe facts one file at a time."""

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

    def parse_file(self, path: Path, *, relative_path: str, language: str, max_bytes: int) -> ParsedFile | None:
        try:
            if path.stat().st_size > max_bytes:
                return None
            with path.open("rb") as handle:
                source = handle.read(max_bytes + 1)
            if len(source) > max_bytes:
                return None
            return self.parse_bytes(source, path=relative_path, language=language)
        except OSError:
            return None

    def parse_bytes(self, source: bytes, *, path: str, language: str) -> ParsedFile | None:
        parser = self._parser(language)
        if parser is None:
            return None
        tree = None
        try:
            tree = parser.parse(source)
            root = tree.root_node
            symbols: list[StructuralSymbol] = []
            imports: list[StructuralImport] = []
            calls: list[StructuralCall] = []
            symbol_types = _SYMBOL_TYPES.get(language, {})

            def visit(node: Any, context: str = "", container: str = "") -> None:
                node_type = str(getattr(node, "type", ""))
                next_context = context
                next_container = container
                symbol_kind = symbol_types.get(node_type)
                if symbol_kind:
                    name = _bounded_node_text(source, _field(node, "name"))
                    if name:
                        qualified = f"{container}.{name}" if symbol_kind == "method" and container else name
                        symbols.append(
                            StructuralSymbol(
                                name=qualified,
                                kind=symbol_kind,
                                line=_point_row(node.start_point) + 1,
                                end_line=_point_row(node.end_point) + 1,
                                container=container,
                            )
                        )
                        next_context = qualified
                        if symbol_kind in {"class", "interface", "trait", "enum"}:
                            next_container = name
                if node_type in _IMPORT_TYPES:
                    target = _bounded_node_text(source, _field(node, "source", "name"))
                    if target:
                        imports.append(StructuralImport(target=target, line=_point_row(node.start_point) + 1))
                if node_type in _CALL_TYPES and context:
                    target = _bounded_node_text(source, _field(node, "function", "name", "member"))
                    if target:
                        calls.append(StructuralCall(caller=context, target=target, line=_point_row(node.start_point) + 1))
                for child in getattr(node, "children", ()):
                    visit(child, next_context, next_container)

            visit(root)
            return ParsedFile(
                path=path,
                language=language,
                symbols=tuple(symbols),
                imports=tuple(imports),
                calls=tuple(calls),
            )
        except Exception:
            return None
        finally:
            # Keep neither the source buffer nor native tree alive across files.
            del tree
