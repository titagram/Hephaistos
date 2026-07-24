"""Telos storage, pointer management, authorization, and rollback."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .organism_home import ensure_organism_directories, secure_file_permissions
from .telos_contract import TelosRevision, telos_revision_from_dict, validate_telos_revision

if TYPE_CHECKING:
    from .ledger import EvolutionLedger


class TelosStoreError(Exception):
    """Raised when Telos storage, activation, approval, or rollback operations fail."""


class TelosStore:
    def __init__(self, organism_root: Path | None = None) -> None:
        self.organism_root = ensure_organism_directories(organism_root)
        self.telos_dir = self.organism_root / "telos"
        self.revisions_dir = self.telos_dir / "revisions"
        self.active_pointer = self.telos_dir / "active.json"
        self.lkg_pointer = self.telos_dir / "last-known-good.json"

    def save_revision(self, revision: TelosRevision) -> Path:
        validate_telos_revision(revision)
        digest = revision.canonical_digest
        path = self.revisions_dir / f"{digest}.json"
        if not path.exists():
            path.write_text(revision.to_canonical_json(), encoding="utf-8")
            secure_file_permissions(path)
        return path

    def get_revision(self, digest: str) -> TelosRevision:
        path = self.revisions_dir / f"{digest}.json"
        if not path.exists():
            raise TelosStoreError(f"Telos revision not found for digest: {digest}")
        data = json.loads(path.read_text(encoding="utf-8"))
        return telos_revision_from_dict(data)

    def activate_revision(
        self,
        digest: str,
        receipt_id: str,
        ledger: "EvolutionLedger | None" = None,
    ) -> None:
        raise TelosStoreError("host_approval_not_implemented")

    def get_active_digest(self) -> str | None:
        if not self.active_pointer.exists():
            return None
        try:
            data = json.loads(self.active_pointer.read_text(encoding="utf-8"))
            return str(data.get("digest", ""))
        except Exception:
            return None

    def get_active_revision(self) -> TelosRevision | None:
        digest = self.get_active_digest()
        if not digest:
            return None
        return self.get_revision(digest)

    def rollback(
        self,
        target_digest: str,
        receipt_id: str,
        ledger: "EvolutionLedger | None" = None,
    ) -> None:
        raise TelosStoreError("host_approval_not_implemented")
