"""Telos storage and pointer-management — no host-authorised transition methods."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from .organism_home import ensure_organism_directories, secure_file_permissions
from .telos_contract import TelosRevision, telos_revision_from_dict, validate_telos_revision


class TelosStoreError(Exception):
    """Raised when Telos storage, activation, or rollback operations fail."""


_JsonReader = Callable[[Path], dict[str, Any] | None]


class TelosStore:
    def __init__(self, organism_root: Path | None = None) -> None:
        self.organism_root = ensure_organism_directories(organism_root)
        self.telos_dir = self.organism_root / "telos"
        self.revisions_dir = self.telos_dir / "revisions"
        self.active_pointer = self.telos_dir / "active.json"
        self.lkg_pointer = self.telos_dir / "last-known-good.json"

    @classmethod
    def from_verified_read_root(cls, organism_root: Path) -> "TelosStore":
        """Bind a store to a caller-verified existing root without mutation.

        Dashboard readers must preflight the root and provide a safe ``read_json``
        callback to the read methods below.  Unlike ``__init__``, this factory
        deliberately does not create directories or adjust permissions.
        """
        store = cls.__new__(cls)
        store.organism_root = Path(organism_root)
        store.telos_dir = store.organism_root / "telos"
        store.revisions_dir = store.telos_dir / "revisions"
        store.active_pointer = store.telos_dir / "active.json"
        store.lkg_pointer = store.telos_dir / "last-known-good.json"
        return store

    def save_revision(self, revision: TelosRevision) -> Path:
        validate_telos_revision(revision)
        digest = revision.canonical_digest
        path = self.revisions_dir / f"{digest}.json"
        if not path.exists():
            path.write_text(revision.to_canonical_json(), encoding="utf-8")
            secure_file_permissions(path)
        return path

    def get_revision(
        self, digest: str, *, read_json: _JsonReader | None = None
    ) -> TelosRevision:
        path = self.revisions_dir / f"{digest}.json"
        if read_json is not None:
            data = read_json(path)
        else:
            if not path.exists():
                raise TelosStoreError(f"Telos revision not found for digest: {digest}")
            data = json.loads(path.read_text(encoding="utf-8"))
        if data is None:
            raise TelosStoreError(f"Telos revision not found for digest: {digest}")
        return telos_revision_from_dict(data)

    def activate_revision(
        self,
        digest: str,
        receipt_id: str = "",
        *,
        grant_id: str | None = None,
    ) -> None:
        """Public activation — always fails closed.

        Model callers cannot invoke pointer mutation through any
        TelosStore method, public or private.  Host-authorised
        pointer publication lives in the gateway-owned
        ``TelosCoordinator``.
        """
        raise TelosStoreError("host_approval_not_implemented")

    def get_active_digest(self, *, read_json: _JsonReader | None = None) -> str | None:
        if read_json is not None:
            data = read_json(self.active_pointer)
            return None if data is None else str(data.get("digest", ""))
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
        receipt_id: str = "",
        *,
        grant_id: str | None = None,
    ) -> None:
        """Public rollback — always fails closed for model callers."""
        raise TelosStoreError("host_approval_not_implemented")
