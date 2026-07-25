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
    confirm_migration: bool = False,
) -> PublishedGeneration:
    """Ensure the global organism lifecycle is initialized.

    If no global organism exists and legacy profile state is detected,
    a migration proposal is returned via LifecycleInitError with diagnostic
    details. The caller must confirm migration explicitly.

    Creates identity and baseline ledger if they don't exist and no legacy
    state is found. Returns the baseline PublishedGeneration. Idempotent.
    """
    root = organism_root or get_organism_home()
    root = resolve_organism_root(root)
    ensure_organism_directories(root)

    identity_path = root / "identity.json"
    ledger_path = root / "evolution" / "evolution.db"
    generations_dir = root / "evolution" / "generations"

    if identity_path.exists():
        # Identity exists — verify coherence with ledger
        ident = load_organism_identity(root)
        if not ledger_path.exists():
            raise LifecycleInitError("organism_identity_without_ledger")

        ledger = EvolutionLedger(ledger_path)
        store = GenerationStore(generations_dir)
        try:
            baseline = store.verify(ident.lineage_root_digest)
            ledger.connection.close()
            return baseline
        except Exception:
            ledger.connection.close()
            raise LifecycleInitError("organism_identity_lineage_mismatch") from None

    # No identity — scan legacy profiles before fresh init
    from .migration_discovery import (
        discover_legacy_state,
        LegacyProjectAState,
        LegacyStatus,
    )

    candidates = discover_legacy_state()
    coherent = [c for c in candidates if c.status == LegacyStatus.COHERENT_BASELINE_ONLY]

    if coherent and not confirm_migration:
        # Block automatic init — caller must confirm
        raise LifecycleInitError(
            f"legacy_state_detected:{len(coherent)}_coherent_baselines"
        )

    # Fresh or confirmed migration: create ledger, baseline, identity
    ledger = EvolutionLedger(ledger_path)
    store = GenerationStore(generations_dir)
    baseline = store.initialize_baseline(_stable_base())

    create_organism_identity(
        organism_root=root,
        lineage_root_digest=baseline.generation_id,
    )

    if coherent and confirm_migration:
        # Record migration provenance event
        try:
            from datetime import datetime, timezone
            import json
            manifest = {
                "source_roots": [str(c.db_path.parent.parent) for c in coherent],
                "generation_ids": [c.active_generation_id for c in coherent if c.active_generation_id],
                "migrated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            }
            ledger.connection.execute(
                "INSERT INTO lifecycle_events (event_id, generation_id, event_type, summary, event_data, recorded_at) VALUES (?,?,?,?,?,?)",
                (f"migration-{baseline.generation_id[:8]}", baseline.generation_id,
                 "state_transition", f"Migrated {len(coherent)} legacy profile(s)",
                 json.dumps(manifest, sort_keys=True),
                 datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")),
            )
            ledger.connection.commit()
        except Exception:
            pass  # Migration provenance is best-effort

    ledger.connection.close()
    return baseline
