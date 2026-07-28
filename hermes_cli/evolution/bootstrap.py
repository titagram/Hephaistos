"""Idempotent first-use initialization for the local evolution lifecycle."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from hermes_constants import get_hermes_home
from hermes_cli import __version__
from hermes_cli.config import _normalize_evolution_config, load_config

from .contract import content_digest
from .ledger import EvolutionLedger
from .locking import (
    LifecycleLockError,
    _validate_directory,
    _validate_lock_file,
    lifecycle_lock,
)
from .pointers import initialize_baseline_pointers
from .reconcile import reconcile_evolution_state
from .store import GenerationStore, PublishedGeneration, StableBaseIdentity

_COMMIT = re.compile(r"[0-9a-f]{40}\Z", re.ASCII)


class EvolutionBootstrapError(RuntimeError):
    """A pre-existing partial lifecycle must be reconciled, never replaced."""


def _repository_commit() -> str | None:
    project_root = Path(__file__).resolve().parents[2]
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], text=True, capture_output=True,
            cwd=project_root, timeout=2, check=False,
        )
        value = result.stdout.strip()
        if result.returncode == 0 and _COMMIT.fullmatch(value):
            return value
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        from hermes_cli.build_info import get_build_sha
        value = get_build_sha(short=40)
        return value if isinstance(value, str) and _COMMIT.fullmatch(value) else None
    except (OSError, ValueError):
        return None


def _stable_base() -> StableBaseIdentity:
    config = _normalize_evolution_config(load_config())
    evolution = config["evolution"]
    return StableBaseIdentity(
        release=__version__, repository_commit=_repository_commit(),
        compatibility_version=__version__,
        configuration_fingerprint=content_digest(
            evolution, domain="hades-evolution-config-v1"
        ),
    )


def evolution_state_kind(root: Path, *, max_members: int | None = None) -> str:
    """Classify state without creating paths: empty/lock-only is uninitialized."""
    if max_members is not None and (
        isinstance(max_members, bool)
        or not isinstance(max_members, int)
        or max_members < 1
    ):
        return "blocked"
    try:
        root_info = root.lstat()
    except FileNotFoundError:
        return "uninitialized"
    except (OSError, TypeError, NotImplementedError):
        return "blocked"
    try:
        _validate_directory(root_info)
        iterator = iter(root.iterdir())
        if max_members is None:
            try:
                first_member = next(iterator)
            except StopIteration:
                return "uninitialized"
            try:
                next(iterator)
            except StopIteration:
                members = [first_member]
            else:
                return "existing"
        else:
            members = []
            for _ in range(max_members + 1):
                try:
                    members.append(next(iterator))
                except StopIteration:
                    break
            if len(members) > max_members:
                return "blocked"
    except (
        LifecycleLockError,
        OSError,
        TypeError,
        NotImplementedError,
    ):
        return "blocked"
    if not members:
        return "uninitialized"
    if len(members) != 1 or members[0].name != ".lifecycle.lock":
        return "existing"
    try:
        _validate_lock_file(members[0].lstat())
    except (
        LifecycleLockError,
        OSError,
        TypeError,
        NotImplementedError,
    ):
        return "blocked"
    return "uninitialized"


def ensure_evolution_initialized() -> PublishedGeneration:
    """Initialize the global organism lifecycle.

    Delegates to ensure_global_lifecycle_initialized. Blocks if legacy
    profile state exists at HERMES_HOME/evolution.
    """
    legacy_root = Path(get_hermes_home()) / "evolution"
    if evolution_state_kind(legacy_root) == "existing":
        raise EvolutionBootstrapError("legacy_state_detected")
    from .lifecycle_global import ensure_global_lifecycle_initialized
    return ensure_global_lifecycle_initialized()
