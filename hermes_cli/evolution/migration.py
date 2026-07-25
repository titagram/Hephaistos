"""Migration scanner and safe provenance archiver for legacy profile-scoped Project A state."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hermes_constants import get_default_hermes_root, get_organism_home
from .ledger import EvolutionLedger, EvolutionLedgerError
from .organism_home import ensure_organism_directories, secure_file_permissions


class MigrationError(Exception):
    """Raised when migration fails due to corrupted or unreadable legacy state."""


@dataclass(frozen=True)
class LegacyProfileState:
    profile_ref: str
    home_path: Path
    db_path: Path
    active_path: Path | None
    lkg_path: Path | None
    has_non_baseline_attempts: bool
    chain_root_digest: str | None
    active_digest: str | None


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def inspect_legacy_ledger(db_path: Path) -> tuple[bool, str | None]:
    """Inspect a legacy Project A ledger using EvolutionLedger. Return (has_non_baseline, chain_root_digest)."""
    ledger = EvolutionLedger(db_path)
    try:
        if ledger.verify_chain():
            raise MigrationError("Corrupt event chain in legacy ledger")

        events = ledger.history(limit=1000, after=0)
        chain_root = events[0].event_digest if events else None

        has_non_baseline = any(e.event_type != "baseline_designated" for e in events)
        return has_non_baseline, chain_root
    finally:
        ledger.connection.close()


def scan_legacy_profile_states(default_root: Path | None = None) -> list[LegacyProfileState]:
    root = (default_root or get_default_hermes_root()).resolve()
    candidates: list[Path] = [root]

    profiles_dir = root / "profiles"
    if profiles_dir.is_dir():
        for p in profiles_dir.iterdir():
            if p.is_dir():
                candidates.append(p)

    results: list[LegacyProfileState] = []
    for cand in candidates:
        evo_dir = cand / "evolution"
        db_path = evo_dir / "evolution.db"
        if not db_path.is_file():
            continue

        active_path = evo_dir / "active.json" if (evo_dir / "active.json").is_file() else None
        lkg_path = evo_dir / "last-known-good.json" if (evo_dir / "last-known-good.json").is_file() else None

        has_non_baseline, chain_root = inspect_legacy_ledger(db_path)

        rel_name = cand.name if cand != root else "default"
        profile_ref = hashlib.sha256(rel_name.encode("utf-8")).hexdigest()[:16]
        active_digest = _hash_file(active_path) if active_path else None

        results.append(
            LegacyProfileState(
                profile_ref=profile_ref,
                home_path=cand,
                db_path=db_path,
                active_path=active_path,
                lkg_path=lkg_path,
                has_non_baseline_attempts=has_non_baseline,
                chain_root_digest=chain_root,
                active_digest=active_digest,
            )
        )

    return results


def archive_legacy_provenance(states: list[LegacyProfileState], organism_root: Path | None = None) -> None:
    root = ensure_organism_directories(organism_root)
    archive_dir = root / "archives" / "legacy-profile-state"

    for st in states:
        manifest = {
            "profile_ref": st.profile_ref,
            "chain_root_digest": st.chain_root_digest,
            "active_digest": st.active_digest,
            "has_non_baseline_attempts": st.has_non_baseline_attempts,
        }
        dest = archive_dir / f"{st.profile_ref}.json"
        dest.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        secure_file_permissions(dest)
