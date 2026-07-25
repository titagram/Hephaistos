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
        receipt_id: str = "",
        *,
        grant_id: str | None = None,
        capability: "HostApprovalCapability | None" = None,
        ledger: "EvolutionLedger | None" = None,
    ) -> None:
        """Activate a Telos revision after live host-approved grant consumption.

        Requires a valid consumed grant_id AND a live HostApprovalCapability
        verified by the host CapabilityRegistry AND bound to the exact
        request context (organism_id, digest, action). The capability proves
        that a live host process (CLI, gateway, TUI) made the decision —
        SQLite rows alone are never sufficient authorization.

        The capability is consumed (single-use) and revoked from the host
        registry after successful activation.
        """
        if grant_id is None:
            raise TelosStoreError("host_approval_not_implemented")
        if capability is None:
            raise TelosStoreError("host_approval_not_implemented")

        from .telos_approval import (
            SqliteTelosApprovalBroker, CapabilityRegistry,
            verify_host_capability, clear_host_capability,
        )
        from datetime import datetime, timezone

        caller_owned_ledger = ledger is not None
        if ledger is None:
            from .ledger import EvolutionLedger
            ledger = EvolutionLedger(self.organism_root / "evolution" / "evolution.db")

        try:
            # Live host capability verification — SQLite rows alone are not authority
            # Uses module-level HOST_REGISTRY populated only by host surfaces
            if not verify_host_capability(capability):
                raise TelosStoreError("telos_live_host_capability_required")

            # Verify grant exists and matches digest + action
            grant_row = ledger.connection.execute(
                "SELECT organism_id, telos_digest, action FROM telos_approval_grants WHERE grant_id = ?",
                (grant_id,),
            ).fetchone()
            if grant_row is None:
                raise TelosStoreError("telos_grant_not_found")
            if grant_row["telos_digest"] != digest:
                raise TelosStoreError("telos_grant_digest_mismatch")
            if grant_row["action"] != "activate":
                raise TelosStoreError("telos_grant_wrong_action")

            # Exact context binding — capability must match the requested context
            organism_id = grant_row["organism_id"]
            if not capability.matches_request(organism_id, digest, "activate"):
                raise TelosStoreError(
                    "telos_capability_context_mismatch"
                )

            # Replay detection — a grant must be single-use for activation
            if self.active_pointer.exists():
                existing = json.loads(self.active_pointer.read_text(encoding="utf-8"))
                if existing.get("grant_id") == grant_id:
                    raise TelosStoreError("telos_grant_already_used")

            # Verify consumption exists
            consumption = ledger.connection.execute(
                "SELECT organism_id, telos_digest, action FROM telos_approval_consumptions WHERE grant_id = ?",
                (grant_id,),
            ).fetchone()
            if consumption is None:
                raise TelosStoreError("telos_grant_not_consumed")

            # Verify revision exists
            revision_path = self.revisions_dir / f"{digest}.json"
            if not revision_path.exists():
                raise TelosStoreError("telos_content_not_found")

            # Stage active pointer atomically
            active_data = {
                "digest": digest,
                "activated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                "grant_id": grant_id,
            }
            tmp = self.active_pointer.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(active_data, sort_keys=True), encoding="utf-8")
            tmp.chmod(0o600)
            tmp.rename(self.active_pointer)

            # Update LKG if this is first activation
            if not self.lkg_pointer.exists():
                lkg_data = {"digest": digest}
                self.lkg_pointer.write_text(json.dumps(lkg_data, sort_keys=True), encoding="utf-8")

            # Single-use: mark consumed and revoke from host registry
            capability.mark_consumed()

        finally:
            # Revoke capability from host registry (single-use enforcement)
            if capability is not None:
                clear_host_capability(capability)

            # Only close ledgers we created, NOT caller-owned ones
            if not caller_owned_ledger and ledger is not None:
                import sqlite3
                try:
                    ledger.connection.close()
                except (sqlite3.ProgrammingError, sqlite3.OperationalError):
                    pass  # already closed — not an error

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
        receipt_id: str = "",
        *,
        grant_id: str | None = None,
        capability: "HostApprovalCapability | None" = None,
        ledger: "EvolutionLedger | None" = None,
    ) -> None:
        """Rollback to a previously verified Telos revision.

        Requires a valid consumed grant_id AND live host capability
        bound to the exact context.
        """
        if grant_id is None:
            raise TelosStoreError("host_approval_not_implemented")
        if capability is None:
            raise TelosStoreError("host_approval_not_implemented")

        caller_owned_ledger = ledger is not None
        if ledger is None:
            from .ledger import EvolutionLedger
            ledger = EvolutionLedger(self.organism_root / "evolution" / "evolution.db")

        from .telos_approval import verify_host_capability as _vhc
        from .telos_approval import clear_host_capability as _chc

        try:
            if not _vhc(capability):
                raise TelosStoreError("telos_live_host_capability_required")

            grant_row = ledger.connection.execute(
                "SELECT organism_id, telos_digest, action FROM telos_approval_grants WHERE grant_id = ?",
                (grant_id,),
            ).fetchone()
            if grant_row is None:
                raise TelosStoreError("telos_grant_not_found")
            if grant_row["telos_digest"] != target_digest:
                raise TelosStoreError("telos_grant_digest_mismatch")
            if grant_row["action"] != "rollback":
                raise TelosStoreError("telos_grant_wrong_action")

            # Exact context binding
            organism_id = grant_row["organism_id"]
            if not capability.matches_request(organism_id, target_digest, "rollback"):
                raise TelosStoreError("telos_capability_context_mismatch")

            consumption = ledger.connection.execute(
                "SELECT organism_id, telos_digest, action FROM telos_approval_consumptions WHERE grant_id = ?",
                (grant_id,),
            ).fetchone()
            if consumption is None:
                raise TelosStoreError("telos_grant_not_consumed")

            from datetime import datetime, timezone

            active_data = {
                "digest": target_digest,
                "activated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                "grant_id": grant_id,
                "rollback": True,
            }
            tmp = self.active_pointer.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(active_data, sort_keys=True), encoding="utf-8")
            tmp.chmod(0o600)
            tmp.rename(self.active_pointer)

            # Single-use
            capability.mark_consumed()

        finally:
            if capability is not None:
                _chc(capability)

            if not caller_owned_ledger and ledger is not None:
                import sqlite3
                try:
                    ledger.connection.close()
                except (sqlite3.ProgrammingError, sqlite3.OperationalError):
                    pass  # already closed — not an error
