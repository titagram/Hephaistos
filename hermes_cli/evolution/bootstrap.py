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
from .locking import lifecycle_lock
from .pointers import initialize_baseline_pointers
from .reconcile import reconcile_evolution_state
from .store import GenerationStore, PublishedGeneration, StableBaseIdentity

_COMMIT = re.compile(r"[0-9a-f]{40}\Z", re.ASCII)


class EvolutionBootstrapError(RuntimeError):
    """A pre-existing partial lifecycle must be reconciled, never replaced."""


def _repository_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], text=True, capture_output=True,
            timeout=2, check=False,
        )
        value = result.stdout.strip()
        return value if _COMMIT.fullmatch(value) else None
    except (OSError, subprocess.SubprocessError):
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


def _state_members(root: Path) -> set[str]:
    if not root.exists():
        return set()
    if root.is_symlink() or not root.is_dir():
        return {"unsafe"}
    return {member.name for member in root.iterdir() if member.name != ".lifecycle.lock"}


def ensure_evolution_initialized() -> PublishedGeneration:
    """Initialize exactly once, refusing all non-empty/non-coherent state."""
    root = Path(get_hermes_home()) / "evolution"
    with lifecycle_lock():
        members = _state_members(root)
        if members:
            result = reconcile_evolution_state(repair=False)
            if result.status == "coherent" and result.active is not None:
                return GenerationStore(root / "generations").verify(result.active.generation_id)
            raise EvolutionBootstrapError("existing_state_requires_reconciliation")
        ledger: EvolutionLedger | None = None
        try:
            ledger = EvolutionLedger(root / "evolution.db")
            store = GenerationStore(root / "generations")
            baseline = store.initialize_baseline(_stable_base())
            initialize_baseline_pointers(ledger, store, baseline)
            return baseline
        finally:
            if ledger is not None:
                ledger.connection.close()
