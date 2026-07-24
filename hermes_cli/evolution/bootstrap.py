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


def evolution_state_kind(root: Path) -> str:
    """Classify state without creating paths: empty/lock-only is uninitialized."""
    if not root.exists():
        return "uninitialized"
    if root.is_symlink() or not root.is_dir():
        return "blocked"
    members = {member.name for member in root.iterdir() if member.name != ".lifecycle.lock"}
    return "uninitialized" if not members else "existing"


def ensure_evolution_initialized() -> PublishedGeneration:
    """Initialize exactly once, refusing all non-empty/non-coherent state."""
    root = Path(get_hermes_home()) / "evolution"
    with lifecycle_lock():
        if evolution_state_kind(root) != "uninitialized":
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
