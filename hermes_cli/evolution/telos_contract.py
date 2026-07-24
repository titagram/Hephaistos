"""Telos schema, canonicalization, validation, digest, and constitution safety checks."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Any

_ID_PATTERN = re.compile(r"[a-z][a-z0-9_.-]{0,63}\Z", re.ASCII)
_TAG_PATTERN = re.compile(r"[a-z][a-z0-9_.-]{0,127}\Z", re.ASCII)
_DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)


class TelosContractError(Exception):
    """Raised when Telos schema validation or Constitution safety check fails."""


@dataclass(frozen=True)
class TelosItem:
    id: str
    statement: str
    tags: tuple[str, ...]
    priority: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "statement": self.statement,
            "tags": list(self.tags),
            "priority": self.priority,
        }


@dataclass(frozen=True)
class DesiredTrait(TelosItem):
    pass


@dataclass(frozen=True)
class CapabilityDirection(TelosItem):
    pass


@dataclass(frozen=True)
class Priority(TelosItem):
    pass


@dataclass(frozen=True)
class Tradeoff(TelosItem):
    pass


@dataclass(frozen=True)
class Prohibition(TelosItem):
    pass


@dataclass(frozen=True)
class ProactivityPolicy(TelosItem):
    pass


@dataclass(frozen=True)
class SuccessIndicator(TelosItem):
    pass


@dataclass(frozen=True)
class TelosRevision:
    schema_version: int
    organism_id: str
    parent_digest: str | None
    purpose: str
    desired_traits: tuple[DesiredTrait, ...]
    capability_directions: tuple[CapabilityDirection, ...]
    priorities: tuple[Priority, ...]
    tradeoffs: tuple[Tradeoff, ...]
    prohibitions: tuple[Prohibition, ...]
    proactivity_policy: ProactivityPolicy
    success_indicators: tuple[SuccessIndicator, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "organism_id": self.organism_id,
            "parent_digest": self.parent_digest,
            "purpose": self.purpose,
            "desired_traits": [item.to_dict() for item in self.desired_traits],
            "capability_directions": [item.to_dict() for item in self.capability_directions],
            "priorities": [item.to_dict() for item in self.priorities],
            "tradeoffs": [item.to_dict() for item in self.tradeoffs],
            "prohibitions": [item.to_dict() for item in self.prohibitions],
            "proactivity_policy": self.proactivity_policy.to_dict(),
            "success_indicators": [item.to_dict() for item in self.success_indicators],
        }

    def to_canonical_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @property
    def canonical_digest(self) -> str:
        return hashlib.sha256(self.to_canonical_json().encode("utf-8")).hexdigest()


def _validate_item(item: TelosItem, seen_ids: set[str]) -> None:
    if not _ID_PATTERN.fullmatch(item.id):
        raise TelosContractError(f"Invalid item ID format: {item.id!r}")
    if item.id in seen_ids:
        raise TelosContractError(f"Duplicate item ID: {item.id!r}")
    seen_ids.add(item.id)

    if not (1 <= len(item.statement) <= 500):
        raise TelosContractError(f"Statement length for {item.id!r} must be 1..500 chars")

    if len(item.tags) > 16:
        raise TelosContractError(f"Item {item.id!r} has more than 16 tags")

    for tag in item.tags:
        if not _TAG_PATTERN.fullmatch(tag):
            raise TelosContractError(f"Invalid tag format {tag!r} in item {item.id!r}")

    if not (1 <= item.priority <= 5):
        raise TelosContractError(f"Priority for {item.id!r} must be integer 1..5")


def validate_telos_revision(telos: TelosRevision) -> None:
    if telos.schema_version != 1:
        raise TelosContractError(f"Unsupported schema version: {telos.schema_version}")

    if not (1 <= len(telos.purpose) <= 1000):
        raise TelosContractError("Telos purpose length must be 1..1000 chars")

    if telos.parent_digest is not None and not _DIGEST_PATTERN.fullmatch(telos.parent_digest):
        raise TelosContractError("Invalid parent_digest format")

    collections_check = [
        ("desired_traits", telos.desired_traits, 1, 32),
        ("capability_directions", telos.capability_directions, 1, 32),
        ("priorities", telos.priorities, 1, 32),
        ("tradeoffs", telos.tradeoffs, 0, 32),
        ("prohibitions", telos.prohibitions, 1, 32),
        ("success_indicators", telos.success_indicators, 1, 32),
    ]

    seen_ids: set[str] = set()

    for name, coll, min_c, max_c in collections_check:
        if not (min_c <= len(coll) <= max_c):
            raise TelosContractError(f"Collection {name} length must be {min_c}..{max_c}")
        for item in coll:
            _validate_item(item, seen_ids)

    _validate_item(telos.proactivity_policy, seen_ids)

    check_constitution_safety(telos)


FORBIDDEN_TERMS = frozenset({
    "bypass_auth",
    "unapproved_network",
    "leak_secrets",
    "mutate_core",
    "auto_promote",
    "prompt_injection",
})


def check_constitution_safety(telos: TelosRevision) -> None:
    """Ensure Telos does not authorize constitutionally forbidden operations."""
    text_corpus = (
        telos.purpose
        + " "
        + " ".join(item.statement for coll in [
            telos.desired_traits,
            telos.capability_directions,
            telos.priorities,
            telos.tradeoffs,
            telos.prohibitions,
            telos.success_indicators,
        ] for item in coll)
        + " "
        + telos.proactivity_policy.statement
    ).lower()

    for term in FORBIDDEN_TERMS:
        if term in text_corpus:
            raise TelosContractError(f"Constitution conflict: forbidden phrase {term!r} detected in Telos")


def item_from_dict(cls: type[TelosItem], data: dict[str, Any]) -> TelosItem:
    return cls(
        id=str(data["id"]),
        statement=str(data["statement"]),
        tags=tuple(str(t) for t in data.get("tags", [])),
        priority=int(data["priority"]),
    )


def telos_revision_from_dict(data: dict[str, Any]) -> TelosRevision:
    rev = TelosRevision(
        schema_version=int(data["schema_version"]),
        organism_id=str(data["organism_id"]),
        parent_digest=data.get("parent_digest"),
        purpose=str(data["purpose"]),
        desired_traits=tuple(item_from_dict(DesiredTrait, d) for d in data["desired_traits"]),
        capability_directions=tuple(item_from_dict(CapabilityDirection, d) for d in data["capability_directions"]),
        priorities=tuple(item_from_dict(Priority, d) for d in data["priorities"]),
        tradeoffs=tuple(item_from_dict(Tradeoff, d) for d in data["tradeoffs"]),
        prohibitions=tuple(item_from_dict(Prohibition, d) for d in data["prohibitions"]),
        proactivity_policy=item_from_dict(ProactivityPolicy, data["proactivity_policy"]),
        success_indicators=tuple(item_from_dict(SuccessIndicator, d) for d in data["success_indicators"]),
    )
    validate_telos_revision(rev)
    return rev
