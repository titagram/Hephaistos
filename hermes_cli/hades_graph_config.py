"""Typed, immutable configuration and source snapshots for graph lifecycle v2.

The graph index must never silently accept a misspelled budget or an unsafe
source scope.  This module is deliberately the single reader for the narrow
``hades.graph_index`` configuration boundary; normal config loading/merging
continues to live in :mod:`hermes_cli.config`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final
import unicodedata

from hermes_cli.hades_graph_v2.model import SourceIdentity
from hermes_cli.hades_index.inventory import (
    SourceIdentityError,
    build_source_snapshot,
)


class GraphIndexConfigError(ValueError):
    """A closed ``hades.graph_index`` configuration is invalid."""


DEFAULT_MAX_FILE_BYTES: Final = 8_388_608
DEFAULT_MAX_TOTAL_SOURCE_BYTES: Final = 2_147_483_648
DEFAULT_MAX_WALL_SECONDS: Final = 3_600
DEFAULT_MAX_CHUNK_UNCOMPRESSED_BYTES: Final = 8_388_608
DEFAULT_MAX_BUNDLE_UNCOMPRESSED_BYTES: Final = 536_870_912
DEFAULT_SPOOL_TTL_SECONDS: Final = 86_400

_RANGES: Final[dict[str, tuple[int, int, int]]] = {
    "max_file_bytes": (1_024, 1_073_741_824, DEFAULT_MAX_FILE_BYTES),
    "max_total_source_bytes": (
        1_048_576,
        17_592_186_044_416,
        DEFAULT_MAX_TOTAL_SOURCE_BYTES,
    ),
    "max_wall_seconds": (30, 86_400, DEFAULT_MAX_WALL_SECONDS),
    "max_chunk_uncompressed_bytes": (
        65_536,
        8_388_608,
        DEFAULT_MAX_CHUNK_UNCOMPRESSED_BYTES,
    ),
    "max_bundle_uncompressed_bytes": (
        8_388_608,
        4_294_967_296,
        DEFAULT_MAX_BUNDLE_UNCOMPRESSED_BYTES,
    ),
    "spool_ttl_seconds": (3_600, 604_800, DEFAULT_SPOOL_TTL_SECONDS),
}
_KNOWN_KEYS: Final = frozenset((*_RANGES, "graphify_candidates", "excluded_paths"))


@dataclass(frozen=True, slots=True)
class HadesGraphIndexConfig:
    """The complete, validated graph-index budget configuration."""

    excluded_paths: tuple[str, ...]
    max_file_bytes: int
    max_total_source_bytes: int
    max_wall_seconds: int
    max_bundle_uncompressed_bytes: int
    max_chunk_uncompressed_bytes: int
    spool_ttl_seconds: int
    graphify_candidates: bool


def _graph_index_mapping(config: Mapping[str, object]) -> Mapping[str, object]:
    hades = config.get("hades")
    if hades is None:
        return {}
    if not isinstance(hades, Mapping):
        raise GraphIndexConfigError("hades must be a mapping")
    graph_index = hades.get("graph_index")
    if graph_index is None:
        return {}
    if not isinstance(graph_index, Mapping):
        raise GraphIndexConfigError("hades.graph_index must be a mapping")
    return graph_index


def _safe_user_exclusion(value: object, *, index: int) -> str:
    if not isinstance(value, str):
        raise GraphIndexConfigError(
            f"hades.graph_index.excluded_paths[{index}] must be a string"
        )
    normalized = unicodedata.normalize("NFC", value)
    if (
        not normalized
        or normalized.startswith("/")
        or "\\" in normalized
        or "\x00" in normalized
        or any(part in {"", ".", ".."} for part in normalized.split("/"))
    ):
        raise GraphIndexConfigError(
            f"hades.graph_index.excluded_paths[{index}] must be a safe source-relative path"
        )
    return normalized


def load_hades_graph_index_config(
    config: Mapping[str, object],
) -> HadesGraphIndexConfig:
    """Read the closed graph-index subsection from an already merged config.

    The function intentionally does not consult environment variables.  It is
    called at the explicit graph-index command/job boundary, which is where a
    typo must be an actionable error rather than a deferred warning.
    """

    graph_index = _graph_index_mapping(config)
    for key in sorted(graph_index, key=lambda item: str(item)):
        if not isinstance(key, str) or key not in _KNOWN_KEYS:
            raise GraphIndexConfigError(
                f"unknown configuration key hades.graph_index.{key}"
            )

    raw_chunk = graph_index.get(
        "max_chunk_uncompressed_bytes", DEFAULT_MAX_CHUNK_UNCOMPRESSED_BYTES
    )
    raw_bundle = graph_index.get(
        "max_bundle_uncompressed_bytes", DEFAULT_MAX_BUNDLE_UNCOMPRESSED_BYTES
    )
    if type(raw_chunk) is int and type(raw_bundle) is int and raw_chunk > raw_bundle:
        raise GraphIndexConfigError(
            "hades.graph_index.max_chunk_uncompressed_bytes cannot exceed "
            "hades.graph_index.max_bundle_uncompressed_bytes"
        )

    values: dict[str, int] = {}
    for key, (minimum, maximum, default) in _RANGES.items():
        value = graph_index.get(key, default)
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not minimum <= value <= maximum
        ):
            raise GraphIndexConfigError(
                f"hades.graph_index.{key} must be an integer from {minimum} to {maximum}"
            )
        values[key] = value

    graphify_candidates = graph_index.get("graphify_candidates", False)
    if type(graphify_candidates) is not bool:
        raise GraphIndexConfigError(
            "hades.graph_index.graphify_candidates must be a boolean"
        )

    raw_exclusions = graph_index.get("excluded_paths", [])
    if not isinstance(raw_exclusions, list):
        raise GraphIndexConfigError(
            "hades.graph_index.excluded_paths must be a list of paths"
        )
    excluded_paths = tuple(
        sorted({
            _safe_user_exclusion(value, index=index)
            for index, value in enumerate(raw_exclusions)
        })
    )

    return HadesGraphIndexConfig(
        excluded_paths=excluded_paths,
        max_file_bytes=values["max_file_bytes"],
        max_total_source_bytes=values["max_total_source_bytes"],
        max_wall_seconds=values["max_wall_seconds"],
        max_bundle_uncompressed_bytes=values["max_bundle_uncompressed_bytes"],
        max_chunk_uncompressed_bytes=values["max_chunk_uncompressed_bytes"],
        spool_ttl_seconds=values["spool_ttl_seconds"],
        graphify_candidates=graphify_candidates,
    )


def build_source_identity(root: Path, config: HadesGraphIndexConfig) -> SourceIdentity:
    """Hash every in-scope source entry before extraction starts."""

    snapshot = build_source_snapshot(
        Path(root), user_excluded_paths=config.excluded_paths
    )
    return SourceIdentity(
        head_commit=snapshot.head_commit,
        tree_sha256=snapshot.tree_sha256,
        dirty=snapshot.dirty,
        branch=snapshot.branch,
    )


def verify_source_unchanged(
    root: Path,
    config: HadesGraphIndexConfig,
    before: SourceIdentity,
) -> SourceIdentity:
    """Rehash after extraction and reject a graph built from a moving source."""

    after = build_source_identity(root, config)
    if after.tree_sha256 != before.tree_sha256:
        raise SourceIdentityError("source_changed_during_index")
    return after


__all__ = [
    "GraphIndexConfigError",
    "HadesGraphIndexConfig",
    "SourceIdentityError",
    "build_source_identity",
    "load_hades_graph_index_config",
    "verify_source_unchanged",
]
