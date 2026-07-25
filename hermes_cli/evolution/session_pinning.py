"""Session pinning for autopoiesis — uses public SessionDB pin methods only.

Never accesses SessionDB._conn directly. Uses SessionDB._execute_write rail
through public methods.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path
    from hermes_state import SessionDB


def get_session_autopoiesis_pin(db: SessionDB, session_id: str) -> dict | None:
    """Read the _autopoiesis_pin from model_config via public method."""
    return db.get_autopoiesis_pin(session_id)


def set_session_autopoiesis_pin_if_absent(
    db: SessionDB, session_id: str, pin: dict
) -> bool:
    """Set the pin only if not already present via public method."""
    return db.set_autopoiesis_pin_if_absent(session_id, pin)


def inherit_session_autopoiesis_pin(
    db: SessionDB, parent_session_id: str, child_session_id: str,
) -> bool:
    """Copy the pin from parent to child session via public method."""
    return db.inherit_autopoiesis_pin(parent_session_id, child_session_id)


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
    return OrganismSessionPin(organism_id=ident.organism_id)
