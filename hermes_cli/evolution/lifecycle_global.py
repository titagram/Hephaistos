"""Global organism lifecycle initialization — identity, ledger, migration."""

from __future__ import annotations

import os
from pathlib import Path

from .organism_home import resolve_organism_root, OrganismHomeError, ensure_organism_directories, get_organism_home
from .organism_identity import (
    OrganismIdentityError,
    create_organism_identity,
    load_organism_identity,
)
from .ledger import EvolutionLedger, EvolutionLedgerError
from .store import GenerationStore, PublishedGeneration
from .bootstrap import _stable_base


class LifecycleInitError(RuntimeError):
    """Bounded error during global lifecycle initialization."""


def ensure_global_lifecycle_initialized(
    organism_root: Path | None = None,
) -> PublishedGeneration:
    """Ensure the global organism lifecycle is initialized.

    Creates identity and baseline ledger if they don't exist. Returns the
    baseline PublishedGeneration. Idempotent — repeated calls return the
    same generation.
    """
    root = organism_root or get_organism_home()
    root = resolve_organism_root(root)
    ensure_organism_directories(root)

    identity_path = root / "identity.json"
    ledger_path = root / "evolution" / "evolution.db"
    generations_dir = root / "evolution" / "generations"

    if not identity_path.exists():
        # Fresh initialization: create ledger first, then identity
        ledger = EvolutionLedger(ledger_path)
        store = GenerationStore(generations_dir)
        baseline = store.initialize_baseline(_stable_base())

        # Identity records the REAL baseline generation_id
        create_organism_identity(
            organism_root=root,
            lineage_root_digest=baseline.generation_id,
        )
        ledger.connection.close()
        return baseline

    # Identity exists — verify coherence with ledger
    ident = load_organism_identity(root)

    if not ledger_path.exists():
        raise LifecycleInitError("organism_identity_without_ledger")

    ledger = EvolutionLedger(ledger_path)
    store = GenerationStore(generations_dir)

    try:
        # Verify lineage root digest matches stored baseline
        try:
            baseline = store.verify(ident.lineage_root_digest)
        except Exception:
            raise LifecycleInitError("organism_identity_lineage_mismatch") from None

        ledger.connection.close()
        return baseline
    except Exception:
        ledger.connection.close()
        raise
