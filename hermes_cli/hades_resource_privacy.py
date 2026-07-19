"""Dependency-free privacy predicates shared by graph producers and validators."""

from __future__ import annotations

import re
from urllib.parse import urlsplit


_WINDOWS_ABSOLUTE_PATH_RE = re.compile(r"^[A-Za-z]:[/\\]")
_FILE_ABSOLUTE_URI_RE = re.compile(r"^file:/", re.IGNORECASE)
_HTTP_ABSOLUTE_URI_RE = re.compile(r"^https?://", re.IGNORECASE)


def is_sensitive_semantic_resource_component(component: str) -> bool:
    """Return whether one public-resource path component is sensitive.

    The source-index policy intentionally keeps only the exact lowercase
    ``.env.example`` template in scope. All other environment-file spellings
    are case-insensitive secrets; the same contract applies to semantic graph
    resource names so an effect cannot republish a path the inventory excludes.
    """

    if component == ".env.example":
        return False
    folded = component.casefold()
    return (
        folded in {".ssh", ".git", ".aws", ".envrc"}
        or folded == ".env"
        or folded.startswith(".env.")
    )


def is_platform_absolute_semantic_resource_path(value: str) -> bool:
    """Return whether a resource spelling denotes a filesystem-private path.

    Semantic resources can describe data on any host, so the graph contract
    rejects POSIX, home-relative, Windows drive, UNC, and file-URI spellings.
    Network HTTP(S) endpoints are intentionally not filesystem paths and are
    validated by the dedicated HTTP boundary policy instead.
    """

    return (
        # A single leading backslash is a legitimate PHP fully-qualified class
        # reference (for example ``\\App\\Mail\\Receipt``); UNC starts with two.
        value.startswith(("/", "~", "\\\\"))
        or bool(_WINDOWS_ABSOLUTE_PATH_RE.match(value))
        or bool(_FILE_ABSOLUTE_URI_RE.match(value))
    )


def has_unsafe_semantic_resource_location(value: str) -> bool:
    """Return whether a public resource leaks a private path or URL detail.

    Resource names are platform-independent: both slash spellings delimit
    components, while a single leading backslash remains legal for PHP fully
    qualified names. HTTP schemes are case-insensitive and public endpoints
    may not retain userinfo, query strings, or fragments.
    """

    if is_platform_absolute_semantic_resource_path(value) or any(
        component in {".", ".."} or is_sensitive_semantic_resource_component(component)
        for component in re.split(r"[/:\\]", value)
    ):
        return True
    if not _HTTP_ABSOLUTE_URI_RE.match(value):
        return False
    parsed = urlsplit(value)
    return (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or bool(parsed.query)
        or bool(parsed.fragment)
    )


__all__ = [
    "has_unsafe_semantic_resource_location",
    "is_platform_absolute_semantic_resource_path",
    "is_sensitive_semantic_resource_component",
]
