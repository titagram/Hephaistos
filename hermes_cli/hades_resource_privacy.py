"""Dependency-free privacy predicates shared by graph producers and validators."""

from __future__ import annotations

import re


_WINDOWS_ABSOLUTE_PATH_RE = re.compile(r"^[A-Za-z]:[/\\]")
_FILE_ABSOLUTE_URI_RE = re.compile(r"^file:/", re.IGNORECASE)


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


__all__ = [
    "is_platform_absolute_semantic_resource_path",
    "is_sensitive_semantic_resource_component",
]
