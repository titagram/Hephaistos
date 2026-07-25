"""Session pinning for autopoiesis — stores organism state in model_config._autopoiesis_pin.

Uses SessionDB._execute_write rail. Never uses db.connection directly.
Never calls .changes() (sqlite3.Connection has no such method).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hermes_state import SessionDB


def get_session_autopoiesis_pin(db: SessionDB, session_id: str) -> dict | None:
    """Read the _autopoiesis_pin from model_config without parsing all keys."""
    row = db._conn.execute(
        "SELECT json_extract(COALESCE(model_config, '{}'), '$._autopoiesis_pin') "
        "FROM sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    if row and row[0]:
        return json.loads(row[0])
    return None


def set_session_autopoiesis_pin_if_absent(
    db: SessionDB, session_id: str, pin: dict
) -> bool:
    """Set the pin only if not already present. Returns True if set, False if existed.

    Uses _execute_write for transaction safety, WAL, lock, and retry.
    """
    pin_json = json.dumps(pin, sort_keys=True)

    def _set(conn):
        cur = conn.execute(
            "UPDATE sessions SET model_config = json_set("
            "  COALESCE(model_config, '{}'), '$._autopoiesis_pin', json(?)) "
            "WHERE id = ? "
            "AND json_extract(COALESCE(model_config, '{}'), '$._autopoiesis_pin') IS NULL",
            (pin_json, session_id),
        )
        return cur.rowcount > 0

    return db._execute_write(_set)


def inherit_session_autopoiesis_pin(
    db: SessionDB, parent_session_id: str, child_session_id: str,
) -> bool:
    """Copy the pin from parent to child session. Returns True if inherited."""

    def _inherit(conn):
        row = conn.execute(
            "SELECT json_extract(COALESCE(model_config, '{}'), '$._autopoiesis_pin') "
            "FROM sessions WHERE id = ?",
            (parent_session_id,),
        ).fetchone()
        if not row or not row[0]:
            return False
        cur = conn.execute(
            "UPDATE sessions SET model_config = json_set("
            "  COALESCE(model_config, '{}'), '$._autopoiesis_pin', json(?)) "
            "WHERE id = ?",
            (row[0], child_session_id),
        )
        return cur.rowcount > 0

    return db._execute_write(_inherit)


@dataclass(frozen=True)
class OrganismSessionPin:
    """Immutable snapshot of organism state pinned at session creation."""

    organism_id: str
    active_telos_digest: str | None = None
    active_generation_id: str | None = None
    gnothi_seauton_revision_digest: str | None = None
    profile_revision: str | None = None
    workspace_route: str | None = None

    def to_dict(self) -> dict:
        d = {"organism_id": self.organism_id}
        if self.active_telos_digest:
            d["active_telos_digest"] = self.active_telos_digest
        if self.active_generation_id:
            d["active_generation_id"] = self.active_generation_id
        if self.gnothi_seauton_revision_digest:
            d["gnothi_seauton_revision_digest"] = self.gnothi_seauton_revision_digest
        if self.profile_revision:
            d["profile_revision"] = self.profile_revision
        if self.workspace_route:
            d["workspace_route"] = self.workspace_route
        return d


def load_session_pin(organism_root: "Path | None" = None) -> OrganismSessionPin:
    """Load the current organism state into a session pin dataclass."""
    from pathlib import Path

    from .organism_identity import load_organism_identity
    from .organism_home import get_organism_home

    org_root = organism_root or get_organism_home()
    ident = load_organism_identity(org_root)

    pin = OrganismSessionPin(organism_id=ident.organism_id)

    # Try to load active Telos
    try:
        from .telos_store import TelosStore
        store = TelosStore(org_root)
        digest = store.get_active_digest()
        if digest:
            pin = OrganismSessionPin(
                organism_id=ident.organism_id,
                active_telos_digest=digest,
            )
    except Exception:
        pass

    return pin
