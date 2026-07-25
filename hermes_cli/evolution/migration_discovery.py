"""Read-only legacy Project A state discovery for profile-to-global migration."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from hermes_constants import get_default_hermes_root


class LegacyStatus(str, Enum):
    NO_STATE = "no_state"
    COHERENT_BASELINE_ONLY = "coherent_baseline_only"
    MULTIPLE_IDENTICAL_BASELINE_ONLY = "multiple_identical_baseline_only"
    NON_BASELINE_ATTEMPT = "non_baseline_attempt"
    DIVERGENT_GENERATION = "divergent_generation"
    MALFORMED_STATE = "malformed_state"
    UNKNOWN_SCHEMA = "unknown_schema"
    GLOBAL_STATE_ALREADY_EXISTS = "global_state_already_exists"


@dataclass(frozen=True)
class LegacyProjectAState:
    status: LegacyStatus
    profile_ref: str  # opaque domain-separated hash
    db_path: Path
    schema_version: int | None = None
    generation_count: int = 0
    active_generation_id: str | None = None
    error_code: str | None = None


def _opaque_profile_ref(profile_root: Path) -> str:
    """Derive an opaque, non-path ref from profile root."""
    return hashlib.sha256(
        f"autopoiesis:profile-ref:{profile_root.absolute()}".encode()
    ).hexdigest()[:16]


def _profile_roots(default_root: Path) -> list[Path]:
    """Return known profile evolution directories."""
    roots: list[Path] = []
    profiles_dir = default_root / "profiles"
    if profiles_dir.is_dir():
        for child in profiles_dir.iterdir():
            if child.is_dir() and not child.name.startswith("."):
                evolution_db = child / "evolution" / "evolution.db"
                if evolution_db.is_file():
                    roots.append(child)
    # Also check the default profile at root level
    default_evolution = default_root / "evolution" / "evolution.db"
    if default_evolution.is_file():
        roots.append(default_root)
    return roots


def _classify_database(db_path: Path) -> LegacyProjectAState | None:
    """Read-only classification of a single evolution database."""
    profile_root = db_path.parent.parent  # <root>/evolution/evolution.db -> <root>
    ref = _opaque_profile_ref(profile_root)

    try:
        conn = sqlite3.connect(f"file:{db_path.absolute()}?mode=ro", uri=True)
    except sqlite3.Error:
        return LegacyProjectAState(
            status=LegacyStatus.MALFORMED_STATE,
            profile_ref=ref,
            db_path=db_path,
            error_code="cannot_open",
        )

    try:
        conn.row_factory = sqlite3.Row
        # Read schema version
        sv = conn.execute(
            "SELECT version FROM schema_version WHERE singleton = 1"
        ).fetchone()
        if sv is None:
            conn.close()
            return LegacyProjectAState(
                status=LegacyStatus.MALFORMED_STATE,
                profile_ref=ref,
                db_path=db_path,
                error_code="no_schema_version",
            )
        version = sv["version"]

        # Count generations
        gen_count = conn.execute(
            "SELECT COUNT(*) FROM generations"
        ).fetchone()[0]

        # Look for active/latest generation
        active_gen = conn.execute(
            "SELECT generation_id, state FROM generations ORDER BY created_at DESC LIMIT 1"
        ).fetchone()

        if gen_count == 0:
            conn.close()
            return LegacyProjectAState(
                status=LegacyStatus.COHERENT_BASELINE_ONLY,
                profile_ref=ref,
                db_path=db_path,
                schema_version=version,
                generation_count=0,
            )

        # Check if all generations are baseline-only (state = 'active' with no parent)
        non_baseline_count = conn.execute(
            "SELECT COUNT(*) FROM generations WHERE state != 'active'"
        ).fetchone()[0]

        conn.close()

        if non_baseline_count == 0 and gen_count == 1:
            return LegacyProjectAState(
                status=LegacyStatus.COHERENT_BASELINE_ONLY,
                profile_ref=ref,
                db_path=db_path,
                schema_version=version,
                generation_count=gen_count,
                active_generation_id=active_gen["generation_id"] if active_gen else None,
            )

        if non_baseline_count > 0:
            return LegacyProjectAState(
                status=LegacyStatus.NON_BASELINE_ATTEMPT,
                profile_ref=ref,
                db_path=db_path,
                schema_version=version,
                generation_count=gen_count,
            )

        return LegacyProjectAState(
            status=LegacyStatus.DIVERGENT_GENERATION,
            profile_ref=ref,
            db_path=db_path,
            schema_version=version,
            generation_count=gen_count,
        )

    except (sqlite3.Error, sqlite3.DatabaseError):
        return LegacyProjectAState(
            status=LegacyStatus.MALFORMED_STATE,
            profile_ref=ref,
            db_path=db_path,
            error_code="classification_error",
        )


def discover_legacy_state(default_root: Path | None = None) -> list[LegacyProjectAState]:
    """Scan known profile roots for coherent legacy Project A state."""
    root = default_root or get_default_hermes_root()
    results: list[LegacyProjectAState] = []

    for profile_root in _profile_roots(root):
        db_path = profile_root / "evolution" / "evolution.db"
        if not db_path.is_file():
            continue
        result = _classify_database(db_path)
        if result is not None:
            results.append(result)

    return results


def build_migration_plan(
    states: list[LegacyProjectAState],
    organism_root: Path | None = None,
) -> dict:
    """Build a migration plan from discovered legacy states."""
    from hermes_constants import get_organism_home

    organism_path = organism_root or get_organism_home()
    has_global = (organism_path / "identity.json").exists()

    plan = {
        "global_state_exists": has_global,
        "profiles_found": len(states),
        "baseline_candidates": 0,
        "blocked_profiles": 0,
        "recommended_action": "none",
        "profiles": [],
    }

    for s in states:
        entry = {
            "profile_ref": s.profile_ref,
            "status": s.status.value,
        }
        if s.status == LegacyStatus.COHERENT_BASELINE_ONLY:
            plan["baseline_candidates"] += 1
        elif s.status in (
            LegacyStatus.NON_BASELINE_ATTEMPT,
            LegacyStatus.DIVERGENT_GENERATION,
            LegacyStatus.MALFORMED_STATE,
            LegacyStatus.UNKNOWN_SCHEMA,
        ):
            plan["blocked_profiles"] += 1
        plan["profiles"].append(entry)

    if has_global:
        plan["recommended_action"] = "already_global"
    elif plan["baseline_candidates"] == 1 and plan["blocked_profiles"] == 0:
        plan["recommended_action"] = "migrate_single_baseline"
    elif plan["baseline_candidates"] > 1 and plan["blocked_profiles"] == 0:
        plan["recommended_action"] = "confirm_multiple_identical"
    elif plan["baseline_candidates"] == 0:
        plan["recommended_action"] = "fresh_initialization"
    else:
        plan["recommended_action"] = "manual_review_required"

    return plan


def build_provenance_manifest(states: list[LegacyProjectAState]) -> dict:
    """Build a provenance manifest for archived legacy state."""
    manifests = []
    for s in states:
        manifests.append({
            "profile_ref": s.profile_ref,
            "status": s.status.value,
            "schema_version": s.schema_version,
            "generation_count": s.generation_count,
            "active_generation_id": s.active_generation_id,
        })
    return {
        "schema_version": 1,
        "profile_count": len(states),
        "profiles": manifests,
    }
