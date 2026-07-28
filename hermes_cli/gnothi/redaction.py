from __future__ import annotations

import re
from pathlib import Path
from typing import Any

MAX_STRING_LENGTH = 1_000
REDACTED = "[REDACTED]"
ABSOLUTE_PATH = "[ABSOLUTE_PATH]"

SECRET_KEY_PATTERN = re.compile(
    r"(?:token|secret|password|passwd|api[_-]?key|authorization|cookie|"
    r"private[_-]?key|credential|bearer)",
    re.IGNORECASE,
)
_WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_WINDOWS_UNC_PATH = re.compile(r"^\\\\[^\\/]+[\\/]+[^\\/]+")
_PATH_TRAILING_PUNCTUATION = ".,;:!?)]}"
_LINE_LOCATION_SUFFIX = re.compile(r"(?::\d+){1,2}$")
_URL_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")
_EMBEDDED_PATH_PREFIX_DISALLOWED = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_:/.\\"
)
_EMBEDDED_PATH_TERMINATORS = frozenset(" \t\r\n\"'`<>|?*;,!")
_URL_TOKEN_TERMINATORS = frozenset(" \t\r\n\"'`<>")


def _safe_path(value: str, workspace_root: Path | None) -> tuple[str, int] | None:
    if (
        value.lower().startswith("file://")
        or _WINDOWS_ABSOLUTE_PATH.match(value)
        or _WINDOWS_UNC_PATH.match(value)
    ):
        return ABSOLUTE_PATH, 1

    path = Path(value)
    if not path.is_absolute():
        return None

    resolved = path.resolve(strict=False)
    if workspace_root is not None:
        root = workspace_root.resolve(strict=False)
        try:
            return resolved.relative_to(root).as_posix(), 1
        except ValueError:
            pass
    return ABSOLUTE_PATH, 1


def _is_embedded_path_boundary(value: str, start: int) -> bool:
    """Reject URLs, stable IDs, and relative paths before scanning a candidate."""
    return start == 0 or value[start - 1] not in _EMBEDDED_PATH_PREFIX_DISALLOWED


def _is_url_path_context(value: str, start: int) -> bool:
    """Whether a candidate appears in a URL token's path, query, or fragment."""
    token_start = start
    while (
        token_start > 0
        and value[token_start - 1] not in _URL_TOKEN_TERMINATORS
    ):
        token_start -= 1
    return _URL_SCHEME.search(value[token_start:start]) is not None


def _embedded_path_kind(value: str, start: int) -> str | None:
    if not _is_embedded_path_boundary(value, start):
        return None
    if _is_url_path_context(value, start):
        return None
    if value[start : start + 7].lower() == "file://":
        return "file"
    if value[start] == "/":
        return "posix"
    if value[start : start + 2] == "\\\\":
        return "unc"
    if (
        start + 2 < len(value)
        and value[start].isalpha()
        and value[start + 1] == ":"
        and value[start + 2] in "\\/"
    ):
        return "windows"
    return None


def _embedded_path_end(value: str, start: int, kind: str) -> int:
    """Return the first context delimiter after one syntactically absolute path."""
    index = start
    while index < len(value):
        character = value[index]
        if character in _EMBEDDED_PATH_TERMINATORS:
            break
        if kind == "posix" and character == "\\":
            break
        index += 1
    return index


def _split_embedded_path_suffix(candidate: str) -> tuple[str, str]:
    """Keep prose punctuation and editor line/column markers outside the path."""
    path = candidate.rstrip(_PATH_TRAILING_PUNCTUATION)
    suffix = candidate[len(path) :]
    match = _LINE_LOCATION_SUFFIX.search(path)
    if match:
        suffix = f"{path[match.start() :]}{suffix}"
        path = path[: match.start()]
    return path, suffix


def _redact_embedded_absolute_paths(value: str) -> tuple[str, int]:
    """Replace bounded absolute-path tokens embedded in untrusted prose."""
    parts: list[str] = []
    redactions = 0
    last = 0
    start = 0

    while start < len(value):
        kind = _embedded_path_kind(value, start)
        if kind is None:
            start += 1
            continue

        end = _embedded_path_end(value, start, kind)
        candidate = value[start:end]
        path, suffix = _split_embedded_path_suffix(candidate)
        safe_path = _safe_path(path, workspace_root=None)
        if safe_path is None:
            start += 1
            continue

        parts.append(value[last:start])
        parts.append(f"{safe_path[0]}{suffix}")
        redactions += safe_path[1]
        last = end
        start = end

    if not parts:
        return value, redactions
    parts.append(value[last:])
    return "".join(parts), redactions


def redact_value(
    value: Any,
    workspace_root: Path | None = None,
) -> tuple[Any, int]:
    """Recursively remove secrets and unsafe filesystem locations."""

    if isinstance(value, dict):
        redactions = 0
        result: dict[Any, Any] = {}
        for key, nested in value.items():
            if SECRET_KEY_PATTERN.search(str(key)):
                result[key] = REDACTED
                redactions += 1
                continue
            result[key], nested_count = redact_value(nested, workspace_root)
            redactions += nested_count
        return result, redactions

    if isinstance(value, list):
        redactions = 0
        result = []
        for nested in value:
            safe_nested, nested_count = redact_value(nested, workspace_root)
            result.append(safe_nested)
            redactions += nested_count
        return result, redactions

    if isinstance(value, tuple):
        redactions = 0
        result = []
        for nested in value:
            safe_nested, nested_count = redact_value(nested, workspace_root)
            result.append(safe_nested)
            redactions += nested_count
        return tuple(result), redactions

    if isinstance(value, Path):
        safe_path = _safe_path(str(value), workspace_root)
        if safe_path is not None:
            return safe_path
        return value.as_posix(), 1

    if isinstance(value, str):
        redactions = 0
        if len(value) > MAX_STRING_LENGTH:
            value = value[:MAX_STRING_LENGTH]
            redactions += 1
        if value.lower().startswith("file://"):
            value, embedded_path_redactions = _redact_embedded_absolute_paths(value)
            return value, redactions + embedded_path_redactions
        safe_path = _safe_path(value, workspace_root)
        if safe_path is not None:
            safe_value, path_count = safe_path
            return safe_value, redactions + path_count
        value, embedded_path_redactions = _redact_embedded_absolute_paths(value)
        return value, redactions + embedded_path_redactions

    return value, 0


def safe_exception_class(exc: BaseException) -> str:
    """Expose only the exception type, never its potentially sensitive text."""

    name = type(exc).__name__
    return name if name.isidentifier() else "Exception"
