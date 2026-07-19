"""Dependency-free privacy predicates shared by graph producers and validators."""

from __future__ import annotations


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


__all__ = ["is_sensitive_semantic_resource_component"]
