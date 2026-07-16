"""Typed, immutable configuration and source snapshots for graph lifecycle v2.

The graph index must never silently accept a misspelled budget or an unsafe
source scope.  This module is deliberately the single reader for the narrow
``hades.graph_index`` configuration boundary; normal config loading/merging
continues to live in :mod:`hermes_cli.config`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import fnmatch
from pathlib import Path, PurePosixPath
from typing import Final
import re
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

# Compiled source-scope policy.  This is deliberately defined here, alongside
# the typed reader, so no config value can weaken it.  The Hades information
# worker's conservative sensitive-path policy is represented here too: graph
# inventory is a source producer and must not become a side door around it.
COMPILED_EXCLUDED_DIRECTORY_NAMES: Final = frozenset({
    ".aws",
    ".azure",
    ".cache",
    ".codex",
    ".docker",
    ".gcp",
    ".git",
    ".gradle",
    ".hades",
    ".hermes",
    ".hg",
    ".m2",
    ".mypy_cache",
    ".next",
    ".nuxt",
    ".parcel-cache",
    ".pnpm-store",
    ".pytest_cache",
    ".ruff_cache",
    ".ssh",
    ".svn",
    ".tox",
    ".turbo",
    ".venv",
    ".yarn",
    "__pycache__",
    "bower_components",
    "build",
    "coverage",
    "credentials",
    "dist",
    "node_modules",
    "out",
    "secrets",
    "target",
    "tmp",
    "vendor",
    "venv",
})
COMPILED_EXCLUDED_DIRECTORY_SEQUENCES: Final = (
    ("var", "cache"),
    ("storage", "framework", "cache"),
)
_HADES_SENSITIVE_EXTENSIONS: Final = frozenset({
    ".cer",
    ".cert",
    ".crt",
    ".der",
    ".key",
    ".p12",
    ".pem",
    ".pfx",
})
_HADES_SOURCE_EXTENSIONS: Final = frozenset({
    ".bash",
    ".c",
    ".cc",
    ".cjs",
    ".cpp",
    ".cs",
    ".fish",
    ".go",
    ".h",
    ".hpp",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".mjs",
    ".php",
    ".py",
    ".pyi",
    ".rb",
    ".rs",
    ".scala",
    ".sh",
    ".sql",
    ".swift",
    ".ts",
    ".tsx",
    ".zsh",
})


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
        or len(normalized.encode("utf-8")) > 4_096
        or any(part in {"", ".", ".."} for part in normalized.split("/"))
        or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
        or (
            len(normalized.split("/", 1)[0]) >= 2
            and normalized.split("/", 1)[0][1] == ":"
        )
    ):
        raise GraphIndexConfigError(
            f"hades.graph_index.excluded_paths[{index}] must be a safe source-relative path"
        )
    return normalized


def _is_hades_sensitive_source_path(path: str) -> bool:
    """Apply the existing Hades sensitive-name policy without I/O imports."""

    parts = tuple(part.casefold() for part in PurePosixPath(path).parts)
    if not parts:
        return True
    if any(part in COMPILED_EXCLUDED_DIRECTORY_NAMES for part in parts):
        return True
    name = parts[-1]
    # The v2 specification explicitly retains example env files in scope.
    if name == ".env.example":
        return False
    stem = PurePosixPath(name).stem.casefold()
    stem_normalized = re.sub(r"[^a-z0-9]+", "_", stem).strip("_")
    suffix = PurePosixPath(name).suffix.casefold()
    tokens = tuple(token for token in re.split(r"[^a-z0-9]+", stem) if token)
    cloud_config_path = any(
        parts[index : index + 2] == (".config", "gcloud")
        for index in range(max(0, len(parts) - 1))
    )
    if suffix in _HADES_SOURCE_EXTENSIONS and not cloud_config_path:
        return False
    return (
        cloud_config_path
        or name == ".env"
        or name.startswith(".env.")
        or name == ".envrc"
        or suffix == ".env"
        or name
        in {".netrc", ".npmrc", ".pypirc", "id_dsa", "id_ecdsa", "id_ed25519", "id_rsa"}
        or "service_account" in stem_normalized
        or ("service" in tokens and "account" in tokens)
        or stem_normalized == "application_default_credentials"
        or any(
            token
            in {"credential", "credentials", "secret", "secrets", "token", "tokens"}
            for token in tokens
        )
        or stem
        in {
            "credential",
            "credentials",
            "secret",
            "secrets",
            "token",
            "tokens",
            "auth",
            "oauth",
            "providers",
        }
        or (
            any(
                marker in stem_normalized.split("_")
                for marker in ("auth", "oauth", "provider", "providers")
            )
            and any(
                marker in stem_normalized.split("_")
                for marker in (
                    "config",
                    "store",
                    "credential",
                    "credentials",
                    "token",
                    "tokens",
                )
            )
        )
        or suffix in _HADES_SENSITIVE_EXTENSIONS
    )


def is_compiled_source_excluded(path: str) -> bool:
    """Return whether a normalized source-relative path is permanently out of scope."""

    parts = tuple(part.casefold() for part in PurePosixPath(path).parts)
    if any(
        parts[index : index + len(sequence)] == sequence
        for sequence in COMPILED_EXCLUDED_DIRECTORY_SEQUENCES
        for index in range(0, len(parts) - len(sequence) + 1)
    ):
        return True
    return _is_hades_sensitive_source_path(path)


def is_graph_source_excluded(
    path: str, user_excluded_paths: tuple[str, ...] = ()
) -> bool:
    """Apply compiled scope policy and additive, validated user exclusions."""

    if is_compiled_source_excluded(path):
        return True
    return any(
        path == pattern
        or path.startswith(pattern + "/")
        or (
            any(token in pattern for token in "*?[")
            and fnmatch.fnmatchcase(path, pattern)
        )
        for pattern in user_excluded_paths
    )


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
