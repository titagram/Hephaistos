"""Telos storage, pointer management, authorization, and rollback."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .organism_home import ensure_organism_directories, secure_file_permissions
from .ledger import EvolutionLedger
from .authorization import (
    consume_grant,
    create_authorization_request,
    issue_grant,
    AuthorizationError,
)
from .contract import content_digest
from .telos_contract import TelosRevision, telos_revision_from_dict, validate_telos_revision

_REQUEST_DIGEST_DOMAIN = "hades-evolution-authorization-request-v1"


@dataclass(frozen=True)
class ApprovalReceipt:
    """Returned by issue_approval_receipt — holds the grant_id usable as receipt_id."""
    receipt_id: str
    grant_id: str


class TelosStoreError(Exception):
    """Raised when Telos storage, activation, approval, or rollback operations fail."""


class TelosStore:
    def __init__(self, organism_root: Path | None = None) -> None:
        self.organism_root = ensure_organism_directories(organism_root)
        self.telos_dir = self.organism_root / "telos"
        self.revisions_dir = self.telos_dir / "revisions"
        self.active_pointer = self.telos_dir / "active.json"
        self.lkg_pointer = self.telos_dir / "last-known-good.json"

    def _get_ledger(self) -> EvolutionLedger:
        """Open the organism's EvolutionLedger (evolution.db)."""
        db_path = self.organism_root / "evolution" / "evolution.db"
        return EvolutionLedger(db_path)

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

    def issue_approval_receipt(
        self,
        digest: str,
        *,
        action: str = "activate",
        approved_by: str = "host_user",
    ) -> ApprovalReceipt:
        """Issue a real authorization grant (host-bound, ledger-persisted).

        Creates a fresh attempt + authorization request + grant in the organism's
        EvolutionLedger. The returned ApprovalReceipt.receipt_id must be passed
        to activate_revision or rollback — it is single-use.
        """
        ledger = self._get_ledger()
        try:
            attempt_id = ledger.get_active_attempt_id()
        except Exception:
            attempt_id = ledger.create_attempt("operator", f"telos-{action}-request")
        req = create_authorization_request(
            ledger,
            attempt_id=attempt_id,
            kind="telos_activation",
            subject_digest=digest,
            scope={"action": action},
            ttl_seconds=3600,
        )
        grant = issue_grant(
            ledger,
            request_id=req.request_id,
            approved_by=approved_by,
            confirmation_digest=content_digest(
                req.canonical_payload(), domain=_REQUEST_DIGEST_DOMAIN
            ),
        )
        return ApprovalReceipt(receipt_id=grant.grant_id, grant_id=grant.grant_id)

    def activate_revision(
        self,
        digest: str,
        receipt_id: str,
        ledger: "EvolutionLedger | None" = None,
    ) -> None:
        if ledger is None:
            ledger = self._get_ledger()
        consume_grant(
            ledger,
            grant_id=receipt_id,
            expected_kind="telos_activation",
            expected_subject_digest=digest,
            required_scope={"action": "activate"},
        )
        rev = self.get_revision(digest)

        # Move current active to LKG if active exists
        if self.active_pointer.exists():
            current_active = json.loads(self.active_pointer.read_text(encoding="utf-8"))
            self.lkg_pointer.write_text(
                json.dumps(current_active, indent=2, sort_keys=True), encoding="utf-8"
            )
            secure_file_permissions(self.lkg_pointer)

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
        active_data = {
            "schema_version": 1,
            "organism_id": rev.organism_id,
            "digest": digest,
            "activated_at": now,
            "approval_receipt_id": receipt_id,
        }
        self.active_pointer.write_text(
            json.dumps(active_data, indent=2, sort_keys=True), encoding="utf-8"
        )
        secure_file_permissions(self.active_pointer)

        if not self.lkg_pointer.exists():
            self.lkg_pointer.write_text(
                json.dumps(active_data, indent=2, sort_keys=True), encoding="utf-8"
            )
            secure_file_permissions(self.lkg_pointer)

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
        if ledger is None:
            ledger = self._get_ledger()
        consume_grant(
            ledger,
            grant_id=receipt_id,
            expected_kind="telos_activation",
            expected_subject_digest=target_digest,
            required_scope={"action": "rollback"},
        )
        rev = self.get_revision(target_digest)

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
        active_data = {
            "schema_version": 1,
            "organism_id": rev.organism_id,
            "digest": target_digest,
            "activated_at": now,
            "approval_receipt_id": receipt_id,
            "rollback": True,
        }
        self.active_pointer.write_text(
            json.dumps(active_data, indent=2, sort_keys=True), encoding="utf-8"
        )
        secure_file_permissions(self.active_pointer)
