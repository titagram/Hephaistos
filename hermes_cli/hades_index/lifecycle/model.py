"""Closed, immutable language-neutral facts emitted by graph index adapters.

This module deliberately stops at extraction facts.  It does not create graph
artifact IDs, infer missing facts, or canonicalize an adapter result: those are
the builder's responsibilities.  Keeping this boundary strict makes failures
from a language/framework adapter visible before aggregation.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal, Protocol, TypeAlias

from hermes_cli.hades_graph_config import HadesGraphIndexConfig
from hermes_cli.hades_graph_v2.identity import (
    normalize_source_path,
    normalize_structural_path,
    sha256_jcs,
)
from hermes_cli.hades_graph_v2.model import (
    CandidateSetKnowledge,
    ConditionPolarity,
    EdgeFlow,
    EntrypointKind,
    EvidenceOrigin,
    FrameworkRecord,
    MethodSemantics,
    NodeKind,
    Priority,
    Relation,
    ResolutionKind,
    StructureKind,
    StructureSubtype,
    SourceIdentity,
    TriggerKind,
)
from hermes_cli.hades_graph_v2.schema import GraphContractError, SAFE_INTEGER_MAX


_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_RULE_RE = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
_REASON_RE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_METHOD_RE = re.compile(r"^[A-Z][A-Z0-9_-]{0,31}$")


class IRValidationError(ValueError):
    """A deterministic, safe adapter-contract violation."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def _fail(code: str, message: str) -> None:
    raise IRValidationError(code, message)


def _nfc(
    value: str, *, field_name: str, allow_empty: bool = False, limit: int = 1024
) -> str:
    if not isinstance(value, str):
        _fail("invalid_scalar", f"{field_name} must be a string")
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        _fail("invalid_scalar", f"{field_name} contains an isolated surrogate")
    normalized = unicodedata.normalize("NFC", value)
    if normalized != value:
        _fail("non_nfc", f"{field_name} must already be Unicode NFC")
    if (not allow_empty and not value) or len(value.encode("utf-8")) > limit:
        _fail("invalid_scalar", f"{field_name} has an invalid UTF-8 length")
    if any(unicodedata.category(character) in {"Cc", "Cf"} for character in value):
        _fail("invalid_scalar", f"{field_name} contains control characters")
    return value


def _safe_path(value: str, *, field_name: str = "path") -> str:
    try:
        normalized = normalize_source_path(value)
    except GraphContractError as exc:
        _fail("unsafe_source_path", f"{field_name} must be a safe source-relative path")
    if normalized != value:
        _fail("non_nfc", f"{field_name} must already be normalized")
    return value


def _structural(value: str, *, field_name: str) -> str:
    try:
        normalized = normalize_structural_path(value)
    except GraphContractError:
        _fail("unsafe_structural_path", f"{field_name} must be normalized")
    if normalized != value:
        _fail("non_nfc", f"{field_name} must already be normalized")
    return value


def _digest(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
        _fail("invalid_digest", f"{field_name} must be a lower-case SHA-256 digest")
    return value


def _key(value: str, *, field_name: str) -> str:
    return _digest(value, field_name=field_name)


def _nonnegative(value: int, *, field_name: str) -> int:
    if type(value) is not int or value < 0 or value > SAFE_INTEGER_MAX:
        _fail("invalid_integer", f"{field_name} must be a safe non-negative integer")
    return value


def _positive(value: int, *, field_name: str) -> int:
    if type(value) is not int or value < 1 or value > SAFE_INTEGER_MAX:
        _fail("invalid_integer", f"{field_name} must be a safe positive integer")
    return value


def _tuple(value: object, *, field_name: str) -> tuple[object, ...]:
    if not isinstance(value, tuple):
        _fail("tuple_required", f"{field_name} must be a tuple")
    if any(item is None for item in value):
        _fail("null_not_allowed", f"{field_name} cannot contain null")
    return value


def _sorted_unique(
    values: tuple[object, ...],
    *,
    field_name: str,
    key: Callable[[object], object] | None = None,
) -> None:
    comparer = key or (lambda item: item)
    try:
        ordered = tuple(sorted(values, key=comparer))
    except TypeError:
        _fail("invalid_order", f"{field_name} has values that cannot be ordered")
    if values != ordered or len(set(values)) != len(values):
        _fail("not_sorted_unique", f"{field_name} must be sorted and unique")


def _require_enum(value: object, enum_type: type[Enum], *, field_name: str) -> None:
    if not isinstance(value, enum_type):
        _fail("invalid_enum", f"{field_name} must be a {enum_type.__name__}")


class _IREnum(str, Enum):
    """Exact wire values used by the adapter IR only."""


class DeclarationIdentityKind(_IREnum):
    NAMED = "named"
    ANONYMOUS = "anonymous"


class TargetExpressionKind(_IREnum):
    DIRECT_FUNCTION = "direct_function"
    DIRECT_STATIC_METHOD = "direct_static_method"
    DIRECT_INSTANCE_METHOD = "direct_instance_method"
    CONSTRUCTOR = "constructor"
    CALLABLE_VALUE = "callable_value"
    DYNAMIC_MEMBER = "dynamic_member"
    REFLECTION = "reflection"
    EVAL = "eval"
    IMPORT_SYMBOL = "import_symbol"
    FRAMEWORK_SERVICE = "framework_service"


class ControlKind(_IREnum):
    ENTRY = "entry"
    STRAIGHT_LINE = "straight_line"
    BRANCH = "branch"
    MERGE = "merge"
    LOOP_HEADER = "loop_header"
    LOOP_BODY = "loop_body"
    CATCH = "catch"
    FINALLY = "finally"
    RETURN = "return"
    THROW = "throw"
    ASYNC_DISPATCH = "async_dispatch"


class TerminalKind(_IREnum):
    RESPONSE = "response"
    REDIRECT = "redirect"
    ABORT = "abort"
    EXCEPTION = "exception"
    EXIT = "exit"


class EffectKind(_IREnum):
    DATA_READ = "data_read"
    DATA_WRITE = "data_write"
    EXTERNAL_CALL = "external_call"
    CACHE_READ = "cache_read"
    CACHE_WRITE = "cache_write"
    STORAGE_READ = "storage_read"
    STORAGE_WRITE = "storage_write"
    EVENT_EMIT = "event_emit"
    JOB_DISPATCH = "job_dispatch"
    QUEUE_DISPATCH = "queue_dispatch"


class Modifier(_IREnum):
    PUBLIC = "public"
    PROTECTED = "protected"
    PRIVATE = "private"
    STATIC = "static"
    ABSTRACT = "abstract"
    FINAL = "final"
    ASYNC = "async"
    GENERATOR = "generator"
    READONLY = "readonly"
    SEALED = "sealed"
    VIRTUAL = "virtual"
    OVERRIDE = "override"


class LoopRole(_IREnum):
    BODY = "body"
    BACK = "back"
    EXIT = "exit"


class AsyncDispatchKind(_IREnum):
    EVENT = "event"
    JOB = "job"
    QUEUE = "queue"
    TASK = "task"
    PROMISE = "promise"
    CALLBACK = "callback"


class CoverageCapability(_IREnum):
    INVENTORY = "inventory"
    ENTRYPOINT_DISCOVERY = "entrypoint_discovery"
    SYMBOL_RESOLUTION = "symbol_resolution"
    CALL_GRAPH = "call_graph"
    CONTROL_FLOW = "control_flow"
    FRAMEWORK_LIFECYCLE = "framework_lifecycle"
    EXCEPTIONS = "exceptions"
    ASYNC = "async"
    DATA_ACCESS = "data_access"


class CoverageOutcome(_IREnum):
    FULL = "full"
    PARTIAL = "partial"
    UNSUPPORTED = "unsupported"
    NOT_APPLICABLE = "not_applicable"


class DiagnosticLevel(_IREnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class SourceLocationIR:
    path: str
    start_line: int
    end_line: int
    file_sha256: str

    def __post_init__(self) -> None:
        _safe_path(self.path)
        _positive(self.start_line, field_name="start_line")
        _positive(self.end_line, field_name="end_line")
        if self.end_line < self.start_line:
            _fail("invalid_location", "end_line must not precede start_line")
        _digest(self.file_sha256, field_name="file_sha256")


@dataclass(frozen=True, slots=True)
class FileLocatorIR:
    path: str
    file_sha256: str
    kind: Literal["file"] = "file"

    def __post_init__(self) -> None:
        if self.kind != "file":
            _fail("invalid_discriminator", "file locator kind must be file")
        _safe_path(self.path)
        _digest(self.file_sha256, field_name="file_sha256")


@dataclass(frozen=True, slots=True)
class AstLocatorIR:
    source_location: SourceLocationIR
    structural_path: str
    ordinal: int
    kind: Literal["ast"] = "ast"

    def __post_init__(self) -> None:
        if self.kind != "ast" or not isinstance(self.source_location, SourceLocationIR):
            _fail(
                "invalid_discriminator", "AST locator must carry an AST source location"
            )
        _structural(self.structural_path, field_name="structural_path")
        _nonnegative(self.ordinal, field_name="ordinal")


@dataclass(frozen=True, slots=True)
class ConfigLocatorIR:
    source_location: SourceLocationIR
    structural_pointer: str
    ordinal: int
    kind: Literal["config"] = "config"

    def __post_init__(self) -> None:
        if self.kind != "config" or not isinstance(
            self.source_location, SourceLocationIR
        ):
            _fail(
                "invalid_discriminator", "config locator must carry a source location"
            )
        _structural(self.structural_pointer, field_name="structural_pointer")
        _nonnegative(self.ordinal, field_name="ordinal")


EvidenceLocatorIR: TypeAlias = FileLocatorIR | AstLocatorIR | ConfigLocatorIR
OccurrenceLocatorIR: TypeAlias = AstLocatorIR | ConfigLocatorIR


@dataclass(frozen=True, slots=True)
class ParameterIR:
    position: int
    name: str | None
    type_name: str | None
    variadic: bool
    by_reference: bool
    has_default: bool

    def __post_init__(self) -> None:
        _nonnegative(self.position, field_name="parameter.position")
        for field_name, value in (
            ("parameter.name", self.name),
            ("parameter.type_name", self.type_name),
        ):
            if value is not None:
                _nfc(value, field_name=field_name, limit=256)
        for field_name, value in (
            ("variadic", self.variadic),
            ("by_reference", self.by_reference),
            ("has_default", self.has_default),
        ):
            if type(value) is not bool:
                _fail("invalid_scalar", f"parameter.{field_name} must be boolean")


@dataclass(frozen=True, slots=True)
class IREvidence:
    origin: EvidenceOrigin
    extractor: str
    locator: EvidenceLocatorIR
    inference_rule: str | None

    def __post_init__(self) -> None:
        _require_enum(self.origin, EvidenceOrigin, field_name="evidence.origin")
        extractor = _nfc(self.extractor, field_name="evidence.extractor", limit=128)
        if not _IDENTIFIER_RE.fullmatch(extractor.replace(".", "_")):
            _fail("invalid_identifier", "evidence.extractor must be a lower identifier")
        if not isinstance(self.locator, (FileLocatorIR, AstLocatorIR, ConfigLocatorIR)):
            _fail(
                "invalid_locator", "evidence.locator must be a closed evidence locator"
            )
        if self.inference_rule is not None:
            rule = _nfc(
                self.inference_rule, field_name="evidence.inference_rule", limit=80
            )
            if not _RULE_RE.fullmatch(rule):
                _fail(
                    "invalid_identifier",
                    "evidence.inference_rule must be lower snake case",
                )


@dataclass(frozen=True, slots=True)
class ConditionIR:
    kind: Literal["predicate"]
    normalized: str
    hash: str
    polarity: ConditionPolarity

    def __post_init__(self) -> None:
        if self.kind != "predicate":
            _fail("invalid_discriminator", "condition kind must be predicate")
        _nfc(self.normalized, field_name="condition.normalized", limit=256)
        _digest(self.hash, field_name="condition.hash")
        _require_enum(self.polarity, ConditionPolarity, field_name="condition.polarity")


@dataclass(frozen=True, slots=True)
class AlwaysSuccessor:
    target_block_key: str
    order: int
    kind: Literal["always"] = "always"

    def __post_init__(self) -> None:
        if self.kind != "always":
            _fail("invalid_discriminator", "always successor kind must be always")
        _nfc(self.target_block_key, field_name="target_block_key", limit=128)
        _nonnegative(self.order, field_name="successor.order")


@dataclass(frozen=True, slots=True)
class BranchSuccessor:
    target_block_key: str
    branch_arm_key: str
    order: int
    kind: Literal["branch"] = "branch"

    def __post_init__(self) -> None:
        if self.kind != "branch":
            _fail("invalid_discriminator", "branch successor kind must be branch")
        _nfc(self.target_block_key, field_name="target_block_key", limit=128)
        _nfc(self.branch_arm_key, field_name="branch_arm_key", limit=128)
        _nonnegative(self.order, field_name="successor.order")


@dataclass(frozen=True, slots=True)
class ExceptionSuccessor:
    target_block_key: str
    exception_scope_key: str
    caught_type_name: str | None
    order: int
    kind: Literal["exception"] = "exception"

    def __post_init__(self) -> None:
        if self.kind != "exception":
            _fail("invalid_discriminator", "exception successor kind must be exception")
        _nfc(self.target_block_key, field_name="target_block_key", limit=128)
        _nfc(self.exception_scope_key, field_name="exception_scope_key", limit=128)
        if self.caught_type_name is not None:
            _nfc(self.caught_type_name, field_name="caught_type_name", limit=256)
        _nonnegative(self.order, field_name="successor.order")


@dataclass(frozen=True, slots=True)
class LoopSuccessor:
    target_block_key: str
    loop_role: LoopRole | Literal["body", "back", "exit"]
    order: int
    kind: Literal["loop"] = "loop"

    def __post_init__(self) -> None:
        if self.kind != "loop":
            _fail("invalid_discriminator", "loop successor kind must be loop")
        _nfc(self.target_block_key, field_name="target_block_key", limit=128)
        try:
            role = (
                self.loop_role
                if isinstance(self.loop_role, LoopRole)
                else LoopRole(self.loop_role)
            )
        except (TypeError, ValueError):
            _fail("invalid_enum", "loop_role must be a LoopRole")
        object.__setattr__(self, "loop_role", role)
        _nonnegative(self.order, field_name="successor.order")


@dataclass(frozen=True, slots=True)
class AsyncSuccessor:
    target_local_key: str
    dispatch_kind: (
        AsyncDispatchKind
        | Literal["event", "job", "queue", "task", "promise", "callback"]
    )
    order: int
    kind: Literal["async"] = "async"

    def __post_init__(self) -> None:
        if self.kind != "async":
            _fail("invalid_discriminator", "async successor kind must be async")
        _nfc(self.target_local_key, field_name="target_local_key", limit=128)
        try:
            dispatch = (
                self.dispatch_kind
                if isinstance(self.dispatch_kind, AsyncDispatchKind)
                else AsyncDispatchKind(self.dispatch_kind)
            )
        except (TypeError, ValueError):
            _fail("invalid_enum", "dispatch_kind must be an AsyncDispatchKind")
        object.__setattr__(self, "dispatch_kind", dispatch)
        _nonnegative(self.order, field_name="successor.order")


@dataclass(frozen=True, slots=True)
class ReturnSuccessor:
    terminal_local_key: str
    order: int
    kind: Literal["return"] = "return"

    def __post_init__(self) -> None:
        if self.kind != "return":
            _fail("invalid_discriminator", "return successor kind must be return")
        _nfc(self.terminal_local_key, field_name="terminal_local_key", limit=128)
        _nonnegative(self.order, field_name="successor.order")


Successor: TypeAlias = (
    AlwaysSuccessor
    | BranchSuccessor
    | ExceptionSuccessor
    | LoopSuccessor
    | AsyncSuccessor
    | ReturnSuccessor
)


def _successor_sort_key(successor: Successor) -> tuple[int, str, str]:
    target = getattr(
        successor,
        "target_block_key",
        getattr(
            successor, "target_local_key", getattr(successor, "terminal_local_key", "")
        ),
    )
    return (successor.order, successor.kind, target)


def successor_to_json(successor: Successor) -> dict[str, object]:
    """Encode one closed successor without exposing an arbitrary payload API."""

    if isinstance(successor, AlwaysSuccessor):
        return {
            "kind": successor.kind,
            "target_block_key": successor.target_block_key,
            "order": successor.order,
        }
    if isinstance(successor, BranchSuccessor):
        return {
            "kind": successor.kind,
            "target_block_key": successor.target_block_key,
            "branch_arm_key": successor.branch_arm_key,
            "order": successor.order,
        }
    if isinstance(successor, ExceptionSuccessor):
        return {
            "kind": successor.kind,
            "target_block_key": successor.target_block_key,
            "exception_scope_key": successor.exception_scope_key,
            "caught_type_name": successor.caught_type_name,
            "order": successor.order,
        }
    if isinstance(successor, LoopSuccessor):
        return {
            "kind": successor.kind,
            "target_block_key": successor.target_block_key,
            "loop_role": successor.loop_role.value,
            "order": successor.order,
        }
    if isinstance(successor, AsyncSuccessor):
        return {
            "kind": successor.kind,
            "target_local_key": successor.target_local_key,
            "dispatch_kind": successor.dispatch_kind.value,
            "order": successor.order,
        }
    if isinstance(successor, ReturnSuccessor):
        return {
            "kind": successor.kind,
            "terminal_local_key": successor.terminal_local_key,
            "order": successor.order,
        }
    _fail("invalid_discriminator", "successor must be a closed successor variant")


def successor_from_json(value: object) -> Successor:
    """Decode only the explicit successor union used in adapter fixtures."""

    if not isinstance(value, dict) or not isinstance(value.get("kind"), str):
        _fail("invalid_discriminator", "successor must carry a kind")
    kind = value["kind"]
    expected: dict[str, frozenset[str]] = {
        "always": frozenset({"kind", "target_block_key", "order"}),
        "branch": frozenset({"kind", "target_block_key", "branch_arm_key", "order"}),
        "exception": frozenset({
            "kind",
            "target_block_key",
            "exception_scope_key",
            "caught_type_name",
            "order",
        }),
        "loop": frozenset({"kind", "target_block_key", "loop_role", "order"}),
        "async": frozenset({"kind", "target_local_key", "dispatch_kind", "order"}),
        "return": frozenset({"kind", "terminal_local_key", "order"}),
    }
    if kind not in expected or frozenset(value) != expected[kind]:
        _fail("invalid_discriminator", "successor does not match a closed variant")
    try:
        if kind == "always":
            return AlwaysSuccessor(value["target_block_key"], value["order"])
        if kind == "branch":
            return BranchSuccessor(
                value["target_block_key"], value["branch_arm_key"], value["order"]
            )
        if kind == "exception":
            return ExceptionSuccessor(
                value["target_block_key"],
                value["exception_scope_key"],
                value["caught_type_name"],
                value["order"],
            )
        if kind == "loop":
            return LoopSuccessor(
                value["target_block_key"], LoopRole(value["loop_role"]), value["order"]
            )
        if kind == "async":
            return AsyncSuccessor(
                value["target_local_key"],
                AsyncDispatchKind(value["dispatch_kind"]),
                value["order"],
            )
        return ReturnSuccessor(value["terminal_local_key"], value["order"])
    except (KeyError, TypeError, ValueError) as exc:
        _fail("invalid_successor", "successor fields have invalid values")
        raise AssertionError from exc


@dataclass(frozen=True, slots=True)
class ExecutableDeclaration:
    local_key: str
    language: str
    declaration_kind: NodeKind
    identity_kind: DeclarationIdentityKind
    owner_declaration_key: str | None
    name: str
    qualified_name: str | None
    namespace: str | None
    modifiers: tuple[Modifier, ...]
    parameters: tuple[ParameterIR, ...]
    return_type: str | None
    locator: AstLocatorIR
    entry_block_key: str
    normal_exit_block_keys: tuple[str, ...]
    exception_exit_block_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        _key(self.local_key, field_name="declaration.local_key")
        language = _nfc(self.language, field_name="declaration.language", limit=32)
        if not _IDENTIFIER_RE.fullmatch(language):
            _fail(
                "invalid_identifier", "declaration.language must be a lower identifier"
            )
        _require_enum(self.declaration_kind, NodeKind, field_name="declaration.kind")
        _require_enum(
            self.identity_kind,
            DeclarationIdentityKind,
            field_name="declaration.identity_kind",
        )
        if self.owner_declaration_key is not None:
            _key(
                self.owner_declaration_key,
                field_name="declaration.owner_declaration_key",
            )
        _nfc(self.name, field_name="declaration.name", limit=1024)
        for field_name, value in (
            ("qualified_name", self.qualified_name),
            ("namespace", self.namespace),
            ("return_type", self.return_type),
        ):
            if value is not None:
                _nfc(value, field_name=field_name, limit=1024)
        modifiers = _tuple(self.modifiers, field_name="declaration.modifiers")
        if any(not isinstance(item, Modifier) for item in modifiers):
            _fail("invalid_enum", "declaration.modifiers must contain Modifier values")
        modifier_order = {modifier: index for index, modifier in enumerate(Modifier)}
        if tuple(
            sorted(modifiers, key=lambda item: modifier_order[item])
        ) != modifiers or len(set(modifiers)) != len(modifiers):
            _fail(
                "not_sorted_unique",
                "declaration.modifiers must use enum order and be unique",
            )
        parameters = _tuple(self.parameters, field_name="declaration.parameters")
        if any(not isinstance(item, ParameterIR) for item in parameters):
            _fail("invalid_record", "declaration.parameters must contain ParameterIR")
        if tuple(
            sorted(parameters, key=lambda item: item.position)
        ) != parameters or len({item.position for item in parameters}) != len(
            parameters
        ):
            _fail(
                "not_sorted_unique",
                "declaration.parameters must be sorted by unique position",
            )
        if not isinstance(self.locator, AstLocatorIR):
            _fail("invalid_locator", "declaration locator must be AST")
        _key(self.entry_block_key, field_name="declaration.entry_block_key")
        for field_name, keys in (
            ("normal_exit_block_keys", self.normal_exit_block_keys),
            ("exception_exit_block_keys", self.exception_exit_block_keys),
        ):
            values = _tuple(keys, field_name=field_name)
            if any(not isinstance(item, str) for item in values):
                _fail("invalid_reference", f"{field_name} must contain local keys")
            _sorted_unique(values, field_name=field_name)


@dataclass(frozen=True, slots=True)
class BasicBlock:
    local_key: str
    declaration_key: str
    control_kind: ControlKind
    ordinal: int
    locator: AstLocatorIR
    successors: tuple[Successor, ...]

    def __post_init__(self) -> None:
        _key(self.local_key, field_name="block.local_key")
        _key(self.declaration_key, field_name="block.declaration_key")
        _require_enum(self.control_kind, ControlKind, field_name="block.control_kind")
        _nonnegative(self.ordinal, field_name="block.ordinal")
        if not isinstance(self.locator, AstLocatorIR):
            _fail("invalid_locator", "basic block locator must be AST")
        values = _tuple(self.successors, field_name="block.successors")
        if any(
            not isinstance(
                item,
                (
                    AlwaysSuccessor,
                    BranchSuccessor,
                    ExceptionSuccessor,
                    LoopSuccessor,
                    AsyncSuccessor,
                    ReturnSuccessor,
                ),
            )
            for item in values
        ):
            _fail(
                "invalid_discriminator",
                "block.successors must use the closed successor union",
            )
        if tuple(sorted(values, key=_successor_sort_key)) != values:
            _fail(
                "not_sorted",
                "block.successors must be sorted by order, kind, and target",
            )


@dataclass(frozen=True, slots=True)
class BranchArm:
    branch_local_key: str
    source_block_key: str
    target_block_key: str
    polarity: ConditionPolarity
    condition: ConditionIR
    arm_ordinal: int

    def __post_init__(self) -> None:
        for field_name, value in (
            ("branch_local_key", self.branch_local_key),
            ("source_block_key", self.source_block_key),
            ("target_block_key", self.target_block_key),
        ):
            _key(value, field_name=field_name)
        _require_enum(
            self.polarity, ConditionPolarity, field_name="branch_arm.polarity"
        )
        if (
            not isinstance(self.condition, ConditionIR)
            or self.condition.polarity is not self.polarity
        ):
            _fail("invalid_condition", "branch arm condition must match its polarity")
        _nonnegative(self.arm_ordinal, field_name="branch_arm.arm_ordinal")


@dataclass(frozen=True, slots=True)
class StructureIR:
    local_key: str
    kind: StructureKind
    owner_declaration_key: str
    structural_path: str
    ordinal: int
    subtype: StructureSubtype
    continuation_block_key: str | None
    parent_structure_key: str | None
    evidence: IREvidence

    def __post_init__(self) -> None:
        _key(self.local_key, field_name="structure.local_key")
        _require_enum(self.kind, StructureKind, field_name="structure.kind")
        _key(self.owner_declaration_key, field_name="structure.owner_declaration_key")
        _structural(self.structural_path, field_name="structure.structural_path")
        _nonnegative(self.ordinal, field_name="structure.ordinal")
        _require_enum(self.subtype, StructureSubtype, field_name="structure.subtype")
        allowed_subtypes = {
            StructureKind.CALL_SITE: frozenset({StructureSubtype.CALL}),
            StructureKind.BRANCH_GROUP: frozenset({
                StructureSubtype.IF,
                StructureSubtype.SWITCH,
                StructureSubtype.MATCH,
                StructureSubtype.TERNARY,
                StructureSubtype.LOOP,
                StructureSubtype.DYNAMIC_DISPATCH,
                StructureSubtype.FRAMEWORK_SHORT_CIRCUIT,
            }),
            StructureKind.EXCEPTION_SCOPE: frozenset({
                StructureSubtype.EXCEPTION_DISPATCH,
                StructureSubtype.TRY_CATCH,
                StructureSubtype.TRY_FINALLY,
                StructureSubtype.TRY_CATCH_FINALLY,
                StructureSubtype.FRAMEWORK_EXCEPTION_HANDLER,
            }),
        }
        if self.subtype not in allowed_subtypes[self.kind]:
            _fail("invalid_structure", "structure kind and subtype are incompatible")
        for field_name, value in (
            ("continuation_block_key", self.continuation_block_key),
            ("parent_structure_key", self.parent_structure_key),
        ):
            if value is not None:
                _key(value, field_name=field_name)
        if not isinstance(self.evidence, IREvidence):
            _fail("invalid_record", "structure.evidence must be IREvidence")


@dataclass(frozen=True, slots=True)
class CallSite:
    local_key: str
    caller_declaration_key: str
    source_block_key: str
    locator: AstLocatorIR
    target_expression_kind: TargetExpressionKind
    lexical_target: str | None
    fully_qualified_target: str | None
    receiver_type: str | None
    argument_count: int
    continuation_block_key: str
    exception_scope_key: str | None

    def __post_init__(self) -> None:
        for field_name, value in (
            ("call_site.local_key", self.local_key),
            ("caller_declaration_key", self.caller_declaration_key),
            ("source_block_key", self.source_block_key),
            ("continuation_block_key", self.continuation_block_key),
        ):
            _key(value, field_name=field_name)
        if self.exception_scope_key is not None:
            _key(self.exception_scope_key, field_name="exception_scope_key")
        if not isinstance(self.locator, AstLocatorIR):
            _fail("invalid_locator", "call-site locator must be AST")
        _require_enum(
            self.target_expression_kind,
            TargetExpressionKind,
            field_name="target_expression_kind",
        )
        for field_name, value in (
            ("lexical_target", self.lexical_target),
            ("fully_qualified_target", self.fully_qualified_target),
            ("receiver_type", self.receiver_type),
        ):
            if value is not None:
                _nfc(value, field_name=field_name, limit=1024)
        _nonnegative(self.argument_count, field_name="argument_count")


@dataclass(frozen=True, slots=True)
class LocalNodeTarget:
    local_key: str
    kind: Literal["local_node"] = "local_node"

    def __post_init__(self) -> None:
        if self.kind != "local_node":
            _fail("invalid_discriminator", "local node target kind must be local_node")
        _key(self.local_key, field_name="target.local_key")


@dataclass(frozen=True, slots=True)
class FrameworkBoundaryDescriptor:
    framework: str
    role: str
    public_name: str | None
    locator: OccurrenceLocatorIR
    evidence: IREvidence

    def __post_init__(self) -> None:
        for field_name, value in (("framework", self.framework), ("role", self.role)):
            text = _nfc(value, field_name=field_name, limit=128)
            if not _IDENTIFIER_RE.fullmatch(text):
                _fail("invalid_identifier", f"{field_name} must be a lower identifier")
        if self.public_name is not None:
            _nfc(self.public_name, field_name="public_name", limit=1024)
        if not isinstance(self.locator, (AstLocatorIR, ConfigLocatorIR)):
            _fail("invalid_locator", "framework boundary locator must be AST or config")
        if not isinstance(self.evidence, IREvidence):
            _fail("invalid_record", "framework boundary evidence must be IREvidence")


@dataclass(frozen=True, slots=True)
class BoundaryTarget:
    descriptor: FrameworkBoundaryDescriptor
    kind: Literal["boundary"] = "boundary"

    def __post_init__(self) -> None:
        if self.kind != "boundary" or not isinstance(
            self.descriptor, FrameworkBoundaryDescriptor
        ):
            _fail(
                "invalid_discriminator",
                "boundary target requires FrameworkBoundaryDescriptor",
            )


EdgeTargetIR: TypeAlias = LocalNodeTarget | BoundaryTarget


@dataclass(frozen=True, slots=True)
class EdgeFactIR:
    local_key: str
    source_node_local_key: str
    target: EdgeTargetIR
    relation: Relation
    flow: EdgeFlow
    condition: ConditionIR | None
    branch_group_key: str | None
    call_site_key: str | None
    exception_scope_key: str | None
    order: int | None
    locator: OccurrenceLocatorIR
    evidence: IREvidence

    def __post_init__(self) -> None:
        _key(self.local_key, field_name="edge.local_key")
        _key(self.source_node_local_key, field_name="edge.source_node_local_key")
        if not isinstance(self.target, (LocalNodeTarget, BoundaryTarget)):
            _fail("invalid_discriminator", "edge target must be a closed target union")
        _require_enum(self.relation, Relation, field_name="edge.relation")
        _require_enum(self.flow, EdgeFlow, field_name="edge.flow")
        if self.condition is not None and not isinstance(self.condition, ConditionIR):
            _fail("invalid_condition", "edge.condition must be ConditionIR or null")
        for field_name, value in (
            ("branch_group_key", self.branch_group_key),
            ("call_site_key", self.call_site_key),
            ("exception_scope_key", self.exception_scope_key),
        ):
            if value is not None:
                _key(value, field_name=field_name)
        if self.order is not None:
            _nonnegative(self.order, field_name="edge.order")
        if not isinstance(self.locator, (AstLocatorIR, ConfigLocatorIR)):
            _fail("invalid_locator", "edge locator must be AST or config")
        if not isinstance(self.evidence, IREvidence):
            _fail("invalid_record", "edge.evidence must be IREvidence")


@dataclass(frozen=True, slots=True)
class ExceptionScope:
    local_key: str
    declaration_key: str
    locator: AstLocatorIR
    caught_type_names: tuple[str, ...]
    catch_block_keys: tuple[str, ...]
    finally_block_key: str | None
    parent_scope_key: str | None

    def __post_init__(self) -> None:
        _key(self.local_key, field_name="exception_scope.local_key")
        _key(self.declaration_key, field_name="exception_scope.declaration_key")
        if not isinstance(self.locator, AstLocatorIR):
            _fail("invalid_locator", "exception scope locator must be AST")
        caught = _tuple(self.caught_type_names, field_name="caught_type_names")
        if any(not isinstance(item, str) for item in caught):
            _fail("invalid_scalar", "caught_type_names must contain strings")
        for item in caught:
            _nfc(item, field_name="caught_type_name", limit=256)
        blocks = _tuple(self.catch_block_keys, field_name="catch_block_keys")
        if any(not isinstance(item, str) for item in blocks):
            _fail("invalid_reference", "catch_block_keys must contain local keys")
        for field_name, value in (
            ("finally_block_key", self.finally_block_key),
            ("parent_scope_key", self.parent_scope_key),
        ):
            if value is not None:
                _key(value, field_name=field_name)


@dataclass(frozen=True, slots=True)
class Terminal:
    local_key: str
    source_block_key: str
    kind: TerminalKind
    public_status: int | None
    exception_type: str | None
    locator: AstLocatorIR

    def __post_init__(self) -> None:
        _key(self.local_key, field_name="terminal.local_key")
        _key(self.source_block_key, field_name="terminal.source_block_key")
        _require_enum(self.kind, TerminalKind, field_name="terminal.kind")
        if self.public_status is not None:
            _positive(self.public_status, field_name="terminal.public_status")
            if self.public_status > 999:
                _fail(
                    "invalid_status",
                    "terminal.public_status must be a public status code",
                )
        if self.exception_type is not None:
            _nfc(self.exception_type, field_name="terminal.exception_type", limit=256)
        if self.kind is TerminalKind.EXCEPTION and self.exception_type is None:
            _fail("invalid_nullability", "exception terminal requires exception_type")
        if self.kind is not TerminalKind.EXCEPTION and self.exception_type is not None:
            _fail(
                "invalid_nullability",
                "only exception terminal can carry exception_type",
            )
        if not isinstance(self.locator, AstLocatorIR):
            _fail("invalid_locator", "terminal locator must be AST")


@dataclass(frozen=True, slots=True)
class BlockEffectSource:
    local_key: str
    kind: Literal["block"] = "block"

    def __post_init__(self) -> None:
        if self.kind != "block":
            _fail("invalid_discriminator", "block effect source kind must be block")
        _key(self.local_key, field_name="effect.source.local_key")


@dataclass(frozen=True, slots=True)
class CallSiteEffectSource:
    local_key: str
    kind: Literal["call_site"] = "call_site"

    def __post_init__(self) -> None:
        if self.kind != "call_site":
            _fail(
                "invalid_discriminator",
                "call-site effect source kind must be call_site",
            )
        _key(self.local_key, field_name="effect.source.local_key")


EffectSourceIR: TypeAlias = BlockEffectSource | CallSiteEffectSource


@dataclass(frozen=True, slots=True)
class Effect:
    local_key: str
    source: EffectSourceIR
    kind: EffectKind
    operation: str
    public_resource_name: str | None
    protocol: str | None
    locator: OccurrenceLocatorIR

    def __post_init__(self) -> None:
        _key(self.local_key, field_name="effect.local_key")
        if not isinstance(self.source, (BlockEffectSource, CallSiteEffectSource)):
            _fail(
                "invalid_discriminator", "effect.source must be a closed source union"
            )
        _require_enum(self.kind, EffectKind, field_name="effect.kind")
        _nfc(self.operation, field_name="effect.operation", limit=128)
        for field_name, value in (
            ("public_resource_name", self.public_resource_name),
            ("protocol", self.protocol),
        ):
            if value is not None:
                _nfc(value, field_name=field_name, limit=1024)
        if not isinstance(self.locator, (AstLocatorIR, ConfigLocatorIR)):
            _fail("invalid_locator", "effect locator must be AST or config")


@dataclass(frozen=True, slots=True)
class FrameworkLocalTarget:
    local_key: str
    kind: Literal["local_node"] = "local_node"

    def __post_init__(self) -> None:
        if self.kind != "local_node":
            _fail(
                "invalid_discriminator",
                "framework local target kind must be local_node",
            )
        _key(self.local_key, field_name="framework target local_key")


@dataclass(frozen=True, slots=True)
class FrameworkBoundaryTarget:
    descriptor: FrameworkBoundaryDescriptor
    kind: Literal["boundary"] = "boundary"

    def __post_init__(self) -> None:
        if self.kind != "boundary" or not isinstance(
            self.descriptor, FrameworkBoundaryDescriptor
        ):
            _fail(
                "invalid_discriminator", "framework boundary target requires descriptor"
            )


FrameworkTargetIR: TypeAlias = FrameworkLocalTarget | FrameworkBoundaryTarget


@dataclass(frozen=True, slots=True)
class FrameworkPipelineSegment:
    local_key: str
    framework_role: str
    pipeline_order: int
    target: FrameworkTargetIR
    success_successor: Successor
    short_circuit_successors: tuple[Successor, ...]
    evidence: IREvidence

    def __post_init__(self) -> None:
        _key(self.local_key, field_name="framework_segment.local_key")
        role = _nfc(self.framework_role, field_name="framework_role", limit=128)
        if not _IDENTIFIER_RE.fullmatch(role):
            _fail("invalid_identifier", "framework_role must be a lower identifier")
        _nonnegative(self.pipeline_order, field_name="pipeline_order")
        if not isinstance(self.target, (FrameworkLocalTarget, FrameworkBoundaryTarget)):
            _fail("invalid_discriminator", "framework target must be a closed union")
        if not isinstance(
            self.success_successor,
            (
                AlwaysSuccessor,
                BranchSuccessor,
                ExceptionSuccessor,
                LoopSuccessor,
                AsyncSuccessor,
                ReturnSuccessor,
            ),
        ):
            _fail(
                "invalid_discriminator", "success successor must be a closed successor"
            )
        values = _tuple(
            self.short_circuit_successors, field_name="short_circuit_successors"
        )
        if any(
            not isinstance(
                item,
                (
                    AlwaysSuccessor,
                    BranchSuccessor,
                    ExceptionSuccessor,
                    LoopSuccessor,
                    AsyncSuccessor,
                    ReturnSuccessor,
                ),
            )
            for item in values
        ):
            _fail(
                "invalid_discriminator",
                "short circuit successors must be a closed union",
            )
        if tuple(sorted(values, key=_successor_sort_key)) != values:
            _fail("not_sorted", "short circuit successors must be sorted")
        if not isinstance(self.evidence, IREvidence):
            _fail("invalid_record", "framework segment evidence must be IREvidence")


@dataclass(frozen=True, slots=True)
class MatchConstraints:
    host: str | None
    schemes: tuple[str, ...]
    condition_hash: str | None

    def __post_init__(self) -> None:
        if self.host is not None:
            _nfc(self.host, field_name="match_constraints.host", limit=253)
        schemes = _tuple(self.schemes, field_name="match_constraints.schemes")
        if any(item not in {"http", "https"} for item in schemes):
            _fail("invalid_enum", "schemes must contain http or https")
        _sorted_unique(schemes, field_name="match_constraints.schemes")
        if self.condition_hash is not None:
            _digest(self.condition_hash, field_name="match_constraints.condition_hash")


@dataclass(frozen=True, slots=True)
class EntrypointCandidate:
    kind: EntrypointKind
    framework: str | None
    method_semantics: MethodSemantics
    methods: tuple[str, ...]
    public_path: str | None
    public_name: str | None
    trigger: TriggerKind
    match_constraints: MatchConstraints
    registration_locator: OccurrenceLocatorIR
    handler_local_key: str | None
    unresolved_fact_local_key: str | None
    framework_segment_keys: tuple[str, ...]
    evidence: IREvidence

    def __post_init__(self) -> None:
        _require_enum(self.kind, EntrypointKind, field_name="entrypoint.kind")
        if self.framework is not None:
            _nfc(self.framework, field_name="entrypoint.framework", limit=128)
        _require_enum(
            self.method_semantics,
            MethodSemantics,
            field_name="entrypoint.method_semantics",
        )
        methods = _tuple(self.methods, field_name="entrypoint.methods")
        if any(
            not isinstance(method, str) or not _METHOD_RE.fullmatch(method)
            for method in methods
        ):
            _fail(
                "invalid_method",
                "entrypoint.methods must contain public uppercase methods",
            )
        _sorted_unique(methods, field_name="entrypoint.methods")
        if self.method_semantics is MethodSemantics.EXPLICIT and not methods:
            _fail("invalid_nullability", "explicit method semantics requires methods")
        if self.method_semantics is not MethodSemantics.EXPLICIT and methods:
            _fail(
                "invalid_nullability",
                "non-explicit method semantics requires no methods",
            )
        for field_name, value in (
            ("public_path", self.public_path),
            ("public_name", self.public_name),
        ):
            if value is not None:
                _nfc(value, field_name=field_name, limit=1024)
        _require_enum(self.trigger, TriggerKind, field_name="entrypoint.trigger")
        if not isinstance(self.match_constraints, MatchConstraints):
            _fail(
                "invalid_record",
                "entrypoint.match_constraints must be MatchConstraints",
            )
        if not isinstance(self.registration_locator, (AstLocatorIR, ConfigLocatorIR)):
            _fail(
                "invalid_locator",
                "entrypoint registration locator must be AST or config",
            )
        if (self.handler_local_key is None) == (self.unresolved_fact_local_key is None):
            _fail(
                "invalid_xor",
                "entrypoint requires exactly one handler or unresolved fact",
            )
        for field_name, value in (
            ("handler_local_key", self.handler_local_key),
            ("unresolved_fact_local_key", self.unresolved_fact_local_key),
        ):
            if value is not None:
                _key(value, field_name=field_name)
        segments = _tuple(
            self.framework_segment_keys, field_name="framework_segment_keys"
        )
        if any(not isinstance(item, str) for item in segments):
            _fail("invalid_reference", "framework_segment_keys must contain local keys")
        if len(set(segments)) != len(segments):
            _fail("not_unique", "framework_segment_keys must be unique")
        if not isinstance(self.evidence, IREvidence):
            _fail("invalid_record", "entrypoint.evidence must be IREvidence")


@dataclass(frozen=True, slots=True)
class CallSiteSubjectIR:
    local_key: str
    kind: Literal["call_site"] = "call_site"

    def __post_init__(self) -> None:
        if self.kind != "call_site":
            _fail("invalid_discriminator", "call site subject kind must be call_site")
        _key(self.local_key, field_name="subject.local_key")


@dataclass(frozen=True, slots=True)
class EdgeSubjectIR:
    local_key: str
    kind: Literal["edge"] = "edge"

    def __post_init__(self) -> None:
        if self.kind != "edge":
            _fail("invalid_discriminator", "edge subject kind must be edge")
        _key(self.local_key, field_name="subject.local_key")


UnresolvedSubjectIR: TypeAlias = CallSiteSubjectIR | EdgeSubjectIR


@dataclass(frozen=True, slots=True)
class UnresolvedFact:
    local_key: str
    subject: UnresolvedSubjectIR
    resolution_kind: ResolutionKind
    candidate_set_knowledge: CandidateSetKnowledge
    reason_code: str
    question: str
    evidence_requirements: tuple[str, ...]
    source_locators: tuple[OccurrenceLocatorIR, ...]
    candidate_target_local_keys: tuple[str, ...]
    candidate_edge_local_keys: tuple[str, ...]
    priority: Priority
    impact: str

    def __post_init__(self) -> None:
        _key(self.local_key, field_name="unresolved.local_key")
        if not isinstance(self.subject, (CallSiteSubjectIR, EdgeSubjectIR)):
            _fail(
                "invalid_discriminator", "unresolved subject must be call_site or edge"
            )
        _require_enum(
            self.resolution_kind, ResolutionKind, field_name="resolution_kind"
        )
        _require_enum(
            self.candidate_set_knowledge,
            CandidateSetKnowledge,
            field_name="candidate_set_knowledge",
        )
        reason = _nfc(self.reason_code, field_name="reason_code", limit=128)
        if not _REASON_RE.fullmatch(reason):
            _fail("invalid_identifier", "reason_code must be lower snake case")
        _nfc(self.question, field_name="question", limit=500)
        _nfc(self.impact, field_name="impact", limit=1000)
        requirements = _tuple(
            self.evidence_requirements, field_name="evidence_requirements"
        )
        if not 1 <= len(requirements) <= 16 or any(
            not isinstance(item, str) or not _RULE_RE.fullmatch(item)
            for item in requirements
        ):
            _fail(
                "invalid_evidence_requirements",
                "evidence requirements must be 1-16 lower identifiers",
            )
        _sorted_unique(requirements, field_name="evidence_requirements")
        locators = _tuple(self.source_locators, field_name="source_locators")
        if (
            not locators
            or len(locators) > 20
            or any(
                not isinstance(item, (AstLocatorIR, ConfigLocatorIR))
                for item in locators
            )
        ):
            _fail(
                "invalid_locator",
                "unresolved source locators must be 1-20 AST/config locators",
            )
        for field_name, values in (
            ("candidate_target_local_keys", self.candidate_target_local_keys),
            ("candidate_edge_local_keys", self.candidate_edge_local_keys),
        ):
            keys = _tuple(values, field_name=field_name)
            if len(keys) > 20 or any(not isinstance(item, str) for item in keys):
                _fail(
                    "invalid_reference",
                    f"{field_name} must contain at most 20 local keys",
                )
            _sorted_unique(keys, field_name=field_name)
        if self.candidate_set_knowledge is CandidateSetKnowledge.NOT_APPLICABLE and (
            self.candidate_target_local_keys or self.candidate_edge_local_keys
        ):
            _fail(
                "invalid_candidate_set",
                "not_applicable candidate knowledge requires empty candidate arrays",
            )
        if self.candidate_set_knowledge is CandidateSetKnowledge.COMPLETE and (
            not self.candidate_target_local_keys or not self.candidate_edge_local_keys
        ):
            _fail(
                "invalid_candidate_set",
                "complete candidate knowledge requires target and edge candidates",
            )
        if self.candidate_set_knowledge is CandidateSetKnowledge.INCOMPLETE and not (
            self.candidate_target_local_keys or self.candidate_edge_local_keys
        ):
            _fail(
                "invalid_candidate_set",
                "incomplete candidate knowledge requires a hint",
            )
        _require_enum(self.priority, Priority, field_name="priority")


@dataclass(frozen=True, slots=True)
class CoverageEvent:
    language: str
    capability: CoverageCapability
    outcome: CoverageOutcome
    reason_code: str | None
    path: str | None
    represented_count: int
    omitted_count: int

    def __post_init__(self) -> None:
        language = _nfc(self.language, field_name="coverage.language", limit=32)
        if not _IDENTIFIER_RE.fullmatch(language):
            _fail("invalid_identifier", "coverage.language must be a lower identifier")
        _require_enum(
            self.capability, CoverageCapability, field_name="coverage.capability"
        )
        _require_enum(self.outcome, CoverageOutcome, field_name="coverage.outcome")
        if self.reason_code is not None:
            reason = _nfc(
                self.reason_code, field_name="coverage.reason_code", limit=128
            )
            if not _REASON_RE.fullmatch(reason):
                _fail(
                    "invalid_identifier",
                    "coverage.reason_code must be lower snake case",
                )
        if self.path is not None:
            _safe_path(self.path, field_name="coverage.path")
        _nonnegative(self.represented_count, field_name="coverage.represented_count")
        _nonnegative(self.omitted_count, field_name="coverage.omitted_count")


@dataclass(frozen=True, slots=True)
class AdapterDiagnostic:
    level: DiagnosticLevel | Literal["info", "warning", "error"]
    code: str
    location: SourceLocationIR

    def __post_init__(self) -> None:
        try:
            level = (
                self.level
                if isinstance(self.level, DiagnosticLevel)
                else DiagnosticLevel(self.level)
            )
        except (TypeError, ValueError):
            _fail("invalid_enum", "diagnostic level must be a DiagnosticLevel")
        object.__setattr__(self, "level", level)
        code = _nfc(self.code, field_name="diagnostic.code", limit=128)
        if not _REASON_RE.fullmatch(code):
            _fail("invalid_identifier", "diagnostic code must be lower snake case")
        if not isinstance(self.location, SourceLocationIR):
            _fail(
                "invalid_location", "diagnostic location must be safe SourceLocationIR"
            )


class ReadOnlyFileAccessor(Protocol):
    """Read-only adapter access to an already scoped inventory file."""

    def __call__(self, path: Path) -> bytes: ...


@dataclass(frozen=True, slots=True)
class ExtractionContext:
    workspace_root: Path
    project_id: str
    workspace_binding_id: str
    source_identity: SourceIdentity
    graph_config: HadesGraphIndexConfig
    detected_languages: tuple[str, ...]
    detected_frameworks: tuple[FrameworkRecord, ...]
    composer_metadata: tuple[ConfigLocatorIR, ...]
    python_metadata: tuple[ConfigLocatorIR, ...]
    package_metadata: tuple[ConfigLocatorIR, ...]
    tsconfig_metadata: tuple[ConfigLocatorIR, ...]
    file_accessor: ReadOnlyFileAccessor

    def __post_init__(self) -> None:
        if not isinstance(self.workspace_root, Path):
            _fail("invalid_context", "workspace_root must be a Path")
        for field_name, value in (
            ("project_id", self.project_id),
            ("workspace_binding_id", self.workspace_binding_id),
        ):
            _nfc(value, field_name=field_name, limit=128)
        languages = _tuple(self.detected_languages, field_name="detected_languages")
        if any(
            not isinstance(item, str) or not _IDENTIFIER_RE.fullmatch(item)
            for item in languages
        ):
            _fail(
                "invalid_context", "detected_languages must contain lower identifiers"
            )
        _sorted_unique(languages, field_name="detected_languages")
        for field_name, values in (
            ("composer_metadata", self.composer_metadata),
            ("python_metadata", self.python_metadata),
            ("package_metadata", self.package_metadata),
            ("tsconfig_metadata", self.tsconfig_metadata),
        ):
            rows = _tuple(values, field_name=field_name)
            if any(not isinstance(item, ConfigLocatorIR) for item in rows):
                _fail("invalid_context", f"{field_name} must contain ConfigLocatorIR")
        if not callable(self.file_accessor):
            _fail("invalid_context", "file_accessor must be read-only callable")


def local_record_key(
    language: str,
    path: str,
    record_family: str,
    locator_kind: Literal["file", "ast", "config"],
    structural_path_or_pointer: str,
    ordinal: int,
) -> str:
    """Return the exact local identity digest for one adapter record occurrence."""

    lang = _nfc(language, field_name="language", limit=32)
    if not _IDENTIFIER_RE.fullmatch(lang):
        _fail("invalid_identifier", "language must be a lower identifier")
    try:
        safe_path = normalize_source_path(path)
    except GraphContractError:
        _fail("unsafe_source_path", "path must be a safe source-relative path")
    family = _nfc(record_family, field_name="record_family", limit=128)
    if not _IDENTIFIER_RE.fullmatch(family):
        _fail("invalid_identifier", "record_family must be a lower identifier")
    if locator_kind not in {"file", "ast", "config"}:
        _fail("invalid_discriminator", "locator_kind must be file, ast, or config")
    if locator_kind == "file":
        if structural_path_or_pointer != "" or ordinal != 0:
            _fail(
                "invalid_local_key",
                "file locator local keys require empty structure and ordinal zero",
            )
        structural = ""
    else:
        try:
            structural = normalize_structural_path(structural_path_or_pointer)
        except GraphContractError:
            _fail(
                "unsafe_structural_path",
                "structural_path_or_pointer must be normalized",
            )
        _nonnegative(ordinal, field_name="ordinal")
    return sha256_jcs({
        "language": lang,
        "path": safe_path,
        "record_family": family,
        "locator_kind": locator_kind,
        "structural_path_or_pointer": structural,
        "ordinal": ordinal,
    })


local_key = local_record_key


def _result_sorted(
    records: tuple[object, ...], *, field_name: str, key: Callable[[object], object]
) -> None:
    _tuple(records, field_name=field_name)
    if tuple(sorted(records, key=key)) != records:
        _fail("not_sorted", f"{field_name} must be deterministically sorted")


@dataclass(frozen=True, slots=True)
class AdapterResult:
    declarations: tuple[ExecutableDeclaration, ...]
    blocks: tuple[BasicBlock, ...]
    branch_arms: tuple[BranchArm, ...]
    structures: tuple[StructureIR, ...]
    call_sites: tuple[CallSite, ...]
    edge_facts: tuple[EdgeFactIR, ...]
    exception_scopes: tuple[ExceptionScope, ...]
    terminals: tuple[Terminal, ...]
    effects: tuple[Effect, ...]
    framework_segments: tuple[FrameworkPipelineSegment, ...]
    entrypoints: tuple[EntrypointCandidate, ...]
    unresolved_facts: tuple[UnresolvedFact, ...]
    coverage_events: tuple[CoverageEvent, ...]
    diagnostics: tuple[AdapterDiagnostic, ...]

    def validate(self) -> None:
        """Validate a result as read-only facts; never repair or reorder adapter data."""

        families: tuple[tuple[str, tuple[object, ...]], ...] = (
            ("declarations", self.declarations),
            ("blocks", self.blocks),
            ("structures", self.structures),
            ("call_sites", self.call_sites),
            ("edge_facts", self.edge_facts),
            ("exception_scopes", self.exception_scopes),
            ("terminals", self.terminals),
            ("effects", self.effects),
            ("framework_segments", self.framework_segments),
            ("unresolved_facts", self.unresolved_facts),
        )
        for name, records in families:
            _result_sorted(
                records, field_name=name, key=lambda item: getattr(item, "local_key")
            )
            if any(
                not isinstance(getattr(item, "local_key", None), str)
                for item in records
            ):
                _fail("invalid_record", f"{name} must contain typed local-key records")
        _result_sorted(
            self.branch_arms,
            field_name="branch_arms",
            key=lambda item: (
                item.branch_local_key,
                item.arm_ordinal,
                item.target_block_key,
            ),
        )
        _result_sorted(
            self.entrypoints,
            field_name="entrypoints",
            key=lambda item: (
                item.kind.value,
                item.framework or "",
                item.public_path or "",
                item.public_name or "",
                item.registration_locator.source_location.path,
                item.registration_locator.ordinal,
            ),
        )
        _result_sorted(
            self.coverage_events,
            field_name="coverage_events",
            key=lambda item: (
                item.language,
                item.capability.value,
                item.outcome.value,
                item.reason_code or "",
                item.path or "",
            ),
        )
        _result_sorted(
            self.diagnostics,
            field_name="diagnostics",
            key=lambda item: (
                item.code,
                item.location.path,
                item.location.start_line,
                item.level.value,
            ),
        )

        indexes: dict[str, dict[str, object]] = {
            name: {getattr(row, "local_key"): row for row in rows}
            for name, rows in families
        }
        for name, rows in families:
            if len(indexes[name]) != len(rows):
                _fail("duplicate_local_key", f"{name} contains a duplicate local key")
        all_local_keys: set[str] = set()
        for name, index in indexes.items():
            overlap = all_local_keys.intersection(index)
            if overlap:
                _fail(
                    "duplicate_local_key",
                    f"local key is reused across record families: {min(overlap)}",
                )
            all_local_keys.update(index)

        declarations = indexes["declarations"]
        blocks = indexes["blocks"]
        structures = indexes["structures"]
        call_sites = indexes["call_sites"]
        edges = indexes["edge_facts"]
        scopes = indexes["exception_scopes"]
        terminals = indexes["terminals"]
        segments = indexes["framework_segments"]
        unresolved = indexes["unresolved_facts"]

        def need(index: dict[str, object], key: str, label: str) -> object:
            if key not in index:
                _fail(
                    "unresolved_reference",
                    f"{label} does not resolve in this AdapterResult",
                )
            return index[key]

        for declaration in self.declarations:
            if declaration.owner_declaration_key is not None:
                need(
                    declarations, declaration.owner_declaration_key, "owner declaration"
                )
            need(blocks, declaration.entry_block_key, "entry block")
            for block_key in (
                declaration.normal_exit_block_keys
                + declaration.exception_exit_block_keys
            ):
                need(blocks, block_key, "declaration exit block")
        for block in self.blocks:
            need(declarations, block.declaration_key, "block declaration")
            for successor in block.successors:
                if isinstance(
                    successor,
                    (
                        AlwaysSuccessor,
                        BranchSuccessor,
                        ExceptionSuccessor,
                        LoopSuccessor,
                    ),
                ):
                    need(blocks, successor.target_block_key, "successor block")
                elif isinstance(successor, AsyncSuccessor):
                    if successor.target_local_key not in all_local_keys:
                        _fail(
                            "unresolved_reference",
                            "async successor target does not resolve",
                        )
                else:
                    need(terminals, successor.terminal_local_key, "return terminal")
                if isinstance(successor, BranchSuccessor):
                    arm = next(
                        (
                            item
                            for item in self.branch_arms
                            if item.branch_local_key == successor.branch_arm_key
                        ),
                        None,
                    )
                    if arm is None:
                        _fail(
                            "unresolved_reference",
                            "branch successor arm does not resolve",
                        )
                if isinstance(successor, ExceptionSuccessor):
                    structure = need(
                        structures,
                        successor.exception_scope_key,
                        "exception successor structure",
                    )
                    if (
                        not isinstance(structure, StructureIR)
                        or structure.kind is not StructureKind.EXCEPTION_SCOPE
                    ):
                        _fail(
                            "invalid_structure",
                            "exception successor must reference exception_scope structure",
                        )
        for arm in self.branch_arms:
            structure = need(structures, arm.branch_local_key, "branch arm structure")
            if (
                not isinstance(structure, StructureIR)
                or structure.kind is not StructureKind.BRANCH_GROUP
            ):
                _fail(
                    "invalid_structure",
                    "branch arm must reference branch_group structure",
                )
            need(blocks, arm.source_block_key, "branch arm source block")
            need(blocks, arm.target_block_key, "branch arm target block")
        for structure in self.structures:
            need(declarations, structure.owner_declaration_key, "structure owner")
            if structure.continuation_block_key is not None:
                need(blocks, structure.continuation_block_key, "structure continuation")
            if structure.parent_structure_key is not None:
                need(structures, structure.parent_structure_key, "parent structure")
            if isinstance(structure.evidence.locator, FileLocatorIR):
                _fail(
                    "invalid_file_locator",
                    "file locator is only allowed for inventory-file facts",
                )
        for site in self.call_sites:
            need(
                declarations,
                site.caller_declaration_key,
                "call-site caller declaration",
            )
            need(blocks, site.source_block_key, "call-site source block")
            need(blocks, site.continuation_block_key, "call-site continuation block")
            matching = [
                row
                for row in self.structures
                if row.kind is StructureKind.CALL_SITE
                and row.owner_declaration_key == site.caller_declaration_key
                and row.structural_path == site.locator.structural_path
                and row.ordinal == site.locator.ordinal
            ]
            if not matching:
                _fail(
                    "invalid_structure",
                    "call-site requires an emitted call_site StructureIR",
                )
            if site.exception_scope_key is not None:
                structure = need(
                    structures,
                    site.exception_scope_key,
                    "call-site exception structure",
                )
                if (
                    not isinstance(structure, StructureIR)
                    or structure.kind is not StructureKind.EXCEPTION_SCOPE
                ):
                    _fail(
                        "invalid_structure",
                        "call-site exception reference must be exception_scope structure",
                    )
        for scope in self.exception_scopes:
            need(declarations, scope.declaration_key, "exception scope declaration")
            for block_key in scope.catch_block_keys:
                need(blocks, block_key, "exception catch block")
            if scope.finally_block_key is not None:
                need(blocks, scope.finally_block_key, "exception finally block")
            if scope.parent_scope_key is not None:
                need(scopes, scope.parent_scope_key, "exception parent scope")
            matching = [
                row
                for row in self.structures
                if row.kind is StructureKind.EXCEPTION_SCOPE
                and row.owner_declaration_key == scope.declaration_key
                and row.structural_path == scope.locator.structural_path
                and row.ordinal == scope.locator.ordinal
            ]
            if not matching:
                _fail(
                    "invalid_structure",
                    "exception scope requires emitted exception_scope StructureIR",
                )
        for edge in self.edge_facts:
            if edge.source_node_local_key not in all_local_keys:
                _fail("unresolved_reference", "edge source does not resolve")
            if (
                isinstance(edge.target, LocalNodeTarget)
                and edge.target.local_key not in all_local_keys
            ):
                _fail("unresolved_reference", "edge target does not resolve")
            for field_name, value, expected_kind in (
                ("branch", edge.branch_group_key, StructureKind.BRANCH_GROUP),
                ("call_site", edge.call_site_key, StructureKind.CALL_SITE),
                ("exception", edge.exception_scope_key, StructureKind.EXCEPTION_SCOPE),
            ):
                if value is not None:
                    structure = need(structures, value, f"edge {field_name} structure")
                    if (
                        not isinstance(structure, StructureIR)
                        or structure.kind is not expected_kind
                    ):
                        _fail(
                            "invalid_structure",
                            f"edge {field_name} reference requires matching StructureIR",
                        )
            if isinstance(edge.evidence.locator, FileLocatorIR):
                _fail(
                    "invalid_file_locator",
                    "file locator is only allowed for inventory-file facts",
                )
        for terminal in self.terminals:
            need(blocks, terminal.source_block_key, "terminal source block")
        for effect in self.effects:
            source_key = effect.source.local_key
            if isinstance(effect.source, BlockEffectSource):
                need(blocks, source_key, "effect source block")
            else:
                need(call_sites, source_key, "effect source call site")
        for segment in self.framework_segments:
            if (
                isinstance(segment.target, FrameworkLocalTarget)
                and segment.target.local_key not in all_local_keys
            ):
                _fail(
                    "unresolved_reference", "framework segment target does not resolve"
                )
            for successor in (
                segment.success_successor,
            ) + segment.short_circuit_successors:
                if isinstance(
                    successor,
                    (
                        AlwaysSuccessor,
                        BranchSuccessor,
                        ExceptionSuccessor,
                        LoopSuccessor,
                    ),
                ):
                    need(
                        blocks, successor.target_block_key, "framework successor block"
                    )
                elif (
                    isinstance(successor, AsyncSuccessor)
                    and successor.target_local_key not in all_local_keys
                ):
                    _fail(
                        "unresolved_reference",
                        "framework async target does not resolve",
                    )
                elif isinstance(successor, ReturnSuccessor):
                    need(
                        terminals,
                        successor.terminal_local_key,
                        "framework return terminal",
                    )
        for entrypoint in self.entrypoints:
            if entrypoint.handler_local_key is not None:
                need(declarations, entrypoint.handler_local_key, "entrypoint handler")
            else:
                fact = need(
                    unresolved,
                    entrypoint.unresolved_fact_local_key or "",
                    "entrypoint unresolved fact",
                )
                if (
                    not isinstance(fact, UnresolvedFact)
                    or fact.resolution_kind is not ResolutionKind.ENTRYPOINT_HANDLER
                    or not isinstance(fact.subject, EdgeSubjectIR)
                ):
                    _fail(
                        "invalid_entrypoint_unresolved",
                        "entrypoint unresolved fact must be an entrypoint_handler edge fact",
                    )
                need(
                    edges, fact.subject.local_key, "entrypoint unresolved edge subject"
                )
            pipeline = tuple(
                need(segments, segment_key, "entrypoint framework segment")
                for segment_key in entrypoint.framework_segment_keys
            )
            if (
                tuple(sorted(pipeline, key=lambda item: item.pipeline_order))
                != pipeline
            ):
                _fail(
                    "not_sorted",
                    "entrypoint framework segments must be ordered by pipeline_order",
                )
        for fact in self.unresolved_facts:
            if isinstance(fact.subject, CallSiteSubjectIR):
                need(call_sites, fact.subject.local_key, "unresolved call-site subject")
            else:
                need(edges, fact.subject.local_key, "unresolved edge subject")
            for key in fact.candidate_target_local_keys:
                if key not in all_local_keys:
                    _fail(
                        "unresolved_reference",
                        "unresolved candidate target does not resolve",
                    )
            for key in fact.candidate_edge_local_keys:
                need(edges, key, "unresolved candidate edge")


__all__ = [
    "AdapterDiagnostic",
    "AdapterResult",
    "AlwaysSuccessor",
    "AstLocatorIR",
    "AsyncDispatchKind",
    "AsyncSuccessor",
    "BasicBlock",
    "BlockEffectSource",
    "BoundaryTarget",
    "BranchArm",
    "BranchSuccessor",
    "CallSite",
    "CallSiteEffectSource",
    "CallSiteSubjectIR",
    "CandidateSetKnowledge",
    "ConditionIR",
    "ConditionPolarity",
    "ConfigLocatorIR",
    "ControlKind",
    "CoverageCapability",
    "CoverageEvent",
    "CoverageOutcome",
    "DeclarationIdentityKind",
    "DiagnosticLevel",
    "EdgeFactIR",
    "EdgeFlow",
    "EdgeSubjectIR",
    "EdgeTargetIR",
    "Effect",
    "EffectKind",
    "EffectSourceIR",
    "EntrypointCandidate",
    "EntrypointKind",
    "EvidenceLocatorIR",
    "EvidenceOrigin",
    "ExceptionScope",
    "ExceptionSuccessor",
    "FileLocatorIR",
    "FrameworkBoundaryDescriptor",
    "FrameworkBoundaryTarget",
    "FrameworkLocalTarget",
    "FrameworkPipelineSegment",
    "FrameworkTargetIR",
    "IREvidence",
    "IRValidationError",
    "LocalNodeTarget",
    "LoopRole",
    "LoopSuccessor",
    "MatchConstraints",
    "MethodSemantics",
    "Modifier",
    "NodeKind",
    "OccurrenceLocatorIR",
    "ParameterIR",
    "Priority",
    "ReadOnlyFileAccessor",
    "Relation",
    "ResolutionKind",
    "ReturnSuccessor",
    "SourceLocationIR",
    "StructureIR",
    "StructureKind",
    "StructureSubtype",
    "Successor",
    "TargetExpressionKind",
    "Terminal",
    "TerminalKind",
    "TriggerKind",
    "UnresolvedFact",
    "UnresolvedSubjectIR",
    "local_key",
    "local_record_key",
    "successor_from_json",
    "successor_to_json",
]
