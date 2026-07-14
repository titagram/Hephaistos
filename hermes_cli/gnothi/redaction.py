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


def _safe_path(value: str, workspace_root: Path | None) -> tuple[str, int] | None:
    if value.lower().startswith("file://") or _WINDOWS_ABSOLUTE_PATH.match(value):
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
        safe_path = _safe_path(value, workspace_root)
        if safe_path is not None:
            safe_value, path_count = safe_path
            return safe_value, redactions + path_count
        return value, redactions

    return value, 0


def safe_exception_class(exc: BaseException) -> str:
    """Expose only the exception type, never its potentially sensitive text."""

    name = type(exc).__name__
    return name if name.isidentifier() else "Exception"
