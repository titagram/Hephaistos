"""Canonical encoding and validation helpers for evolution records."""

from __future__ import annotations

import hashlib
import json
import math
import re

_DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_PATH_SCHEME_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:")


class EvolutionContractError(ValueError):
    """A contract violation identified by a stable, non-sensitive code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _validate_canonical_value(value: object, active_ids: set[int]) -> None:
    if value is None or isinstance(value, (bool, int, str)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise EvolutionContractError("invalid_canonical_value")
        return
    if isinstance(value, dict):
        identity = id(value)
        if identity in active_ids:
            raise EvolutionContractError("invalid_canonical_value")
        active_ids.add(identity)
        try:
            for key, child in value.items():
                if not isinstance(key, str):
                    raise EvolutionContractError("invalid_mapping_key")
                _validate_canonical_value(child, active_ids)
        finally:
            active_ids.remove(identity)
        return
    if isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in active_ids:
            raise EvolutionContractError("invalid_canonical_value")
        active_ids.add(identity)
        try:
            for child in value:
                _validate_canonical_value(child, active_ids)
        finally:
            active_ids.remove(identity)
        return
    raise EvolutionContractError("invalid_canonical_value")


def canonical_json_bytes(value: object) -> bytes:
    """Encode a JSON-compatible value into deterministic UTF-8 bytes."""

    _validate_canonical_value(value, set())
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def sha256_digest(data: bytes) -> str:
    """Return the lowercase SHA-256 digest of exact bytes."""

    if not isinstance(data, bytes):
        raise EvolutionContractError("invalid_digest_input")
    return hashlib.sha256(data).hexdigest()


def content_digest(value: object, *, domain: str) -> str:
    """Hash a canonical value with an ASCII domain-separation frame."""

    if (
        not isinstance(domain, str)
        or not domain
        or "\0" in domain
        or not domain.isascii()
    ):
        raise EvolutionContractError("invalid_digest_domain")
    framed = domain.encode("ascii") + b"\0" + canonical_json_bytes(value)
    return hashlib.sha256(framed).hexdigest()


def bounded_reason(value: object, *, limit: int = 512) -> str:
    """Return a single-line, whitespace-normalized reason within ``limit``."""

    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise EvolutionContractError("invalid_reason_limit")
    text = "" if value is None else str(value)
    return " ".join(text.split())[:limit]


def require_relative_posix_path(value: object) -> str:
    """Return a safe canonical path relative to a generation root."""

    if (
        not isinstance(value, str)
        or not value
        or "\0" in value
        or "\\" in value
        or value.startswith("/")
        or _PATH_SCHEME_PATTERN.match(value)
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise EvolutionContractError("invalid_relative_posix_path")
    return value


def require_digest(value: object) -> str:
    """Return a canonical lowercase hexadecimal SHA-256 digest."""

    if not isinstance(value, str) or _DIGEST_PATTERN.fullmatch(value) is None:
        raise EvolutionContractError("invalid_digest")
    return value
