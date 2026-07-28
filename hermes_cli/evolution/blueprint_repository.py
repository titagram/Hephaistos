"""Verified blueprint repository with tamper-proof reads over an existing ledger."""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass

from hermes_cli.evolution.blueprint_contract import (
    BlueprintContractError,
    BlueprintDocument,
    blueprint_document_from_json,
    validate_blueprint_document,
)
from hermes_cli.evolution.contract import content_digest
from hermes_cli.evolution.ledger import (
    BlueprintDraft,
    EvolutionLedger,
    EvolutionLedgerError,
)

_BLUEPRINT_ID_PATTERN = re.compile(r"bp_[A-Za-z0-9_-]+\Z")
_ATTEMPT_ID_PATTERN = re.compile(
    r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\Z",
    re.ASCII,
)
_EVENT_DIGEST_DOMAIN = "hermes-evolution-lifecycle-event-v1"
_MAX_PROPOSAL_EVENTS = 1


class BlueprintRepositoryError(RuntimeError):
    """A non-sensitive, stable repository read or write failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class StoredBlueprint:
    blueprint_id: str
    attempt_id: str
    canonical_digest: str
    state: str
    created_at: str
    document: BlueprintDocument


class BlueprintRepository:
    """Verified reads and delegated writes over an existing EvolutionLedger.

    The constructor accepts an already-initialized ledger and never creates
    or migrates a database itself.
    """

    def __init__(self, ledger: EvolutionLedger) -> None:
        self.ledger = ledger

    def create_or_get(self, document: BlueprintDocument) -> BlueprintDraft:
        try:
            validate_blueprint_document(document)
        except BlueprintContractError as exc:
            raise BlueprintRepositoryError(exc.code) from None
        suggestion_id = document.suggestion_id
        canonical_digest = document.canonical_digest
        canonical_document_json = document.canonical_json_bytes().decode("utf-8")
        input_digests = (canonical_digest, document.active_telos_digest)
        try:
            return self.ledger.create_or_get_blueprint_draft(
                suggestion_id=suggestion_id,
                canonical_document_json=canonical_document_json,
                canonical_digest=canonical_digest,
                input_digests=input_digests,
            )
        except EvolutionLedgerError as exc:
            raise BlueprintRepositoryError(str(exc)) from None
        except (
            sqlite3.Error,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            RecursionError,
        ) as exc:
            raise BlueprintRepositoryError("blueprint_document_incoherent") from exc

    def get(self, blueprint_id: str) -> StoredBlueprint | None:
        if not isinstance(blueprint_id, str) or _BLUEPRINT_ID_PATTERN.fullmatch(
            blueprint_id
        ) is None:
            raise BlueprintRepositoryError("invalid_blueprint_id")
        try:
            source = self.ledger.connection.execute(
                "SELECT blueprint_id FROM blueprint_documents WHERE blueprint_id = ?",
                (blueprint_id,),
            ).fetchone()
        except (sqlite3.DatabaseError, TypeError, ValueError):
            raise BlueprintRepositoryError("blueprint_document_incoherent") from None
        if source is None:
            return None
        try:
            row = self.ledger.connection.execute(
                """
                SELECT
                    d.blueprint_id,
                    d.attempt_id,
                    d.suggestion_id,
                    d.canonical_digest AS document_digest,
                    d.canonical_document_json,
                    d.created_at AS document_created_at,
                    b.attempt_id AS blueprint_attempt_id,
                    b.canonical_digest AS blueprint_digest,
                    b.state AS blueprint_state,
                    b.created_at AS blueprint_created_at,
                    a.attempt_id AS attempt_attempt_id,
                    a.source_kind,
                    a.source_ref,
                    a.state AS attempt_state,
                    a.created_at AS attempt_created_at
                FROM blueprint_documents d
                JOIN blueprints b ON b.blueprint_id = d.blueprint_id AND b.attempt_id = d.attempt_id
                JOIN attempts a ON a.attempt_id = d.attempt_id
                WHERE d.blueprint_id = ?
                """,
                (blueprint_id,),
            ).fetchone()
        except (sqlite3.DatabaseError, TypeError, ValueError):
            raise BlueprintRepositoryError("blueprint_document_incoherent") from None
        if row is None:
            raise BlueprintRepositoryError("blueprint_document_incoherent")
        try:
            document = blueprint_document_from_json(row["canonical_document_json"])
        except BlueprintContractError:
            raise BlueprintRepositoryError("blueprint_document_incoherent") from None
        recalculated_digest = document.canonical_digest
        if (
            recalculated_digest != row["document_digest"]
            or recalculated_digest != row["blueprint_digest"]
        ):
            raise BlueprintRepositoryError("blueprint_document_incoherent")
        if document.suggestion_id != row["suggestion_id"]:
            raise BlueprintRepositoryError("blueprint_document_incoherent")
        if (
            row["attempt_id"] != row["blueprint_attempt_id"]
            or row["attempt_id"] != row["attempt_attempt_id"]
            or not isinstance(row["attempt_id"], str)
            or _ATTEMPT_ID_PATTERN.fullmatch(row["attempt_id"]) is None
            or row["blueprint_state"] != "draft"
            or row["attempt_state"] != "draft"
            or row["source_kind"] != "observer-proposal-v1"
            or row["source_ref"] != recalculated_digest
            or row["document_created_at"] != row["blueprint_created_at"]
            or row["blueprint_created_at"] != row["attempt_created_at"]
        ):
            raise BlueprintRepositoryError("blueprint_document_incoherent")
        self._verify_proposal_event(
            attempt_id=row["attempt_id"],
            canonical_digest=recalculated_digest,
            active_telos_digest=document.active_telos_digest,
            created_at=row["document_created_at"],
        )
        return StoredBlueprint(
            blueprint_id=row["blueprint_id"],
            attempt_id=row["attempt_id"],
            canonical_digest=recalculated_digest,
            state="draft",
            created_at=row["document_created_at"],
            document=document,
        )

    def list(self, *, limit: int) -> list[StoredBlueprint]:
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or limit < 1
            or limit > 100
        ):
            raise BlueprintRepositoryError("invalid_list_limit")
        try:
            rows = self.ledger.connection.execute(
                """
                SELECT blueprint_id
                FROM blueprint_documents
                ORDER BY created_at DESC, blueprint_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        except (sqlite3.DatabaseError, TypeError, ValueError):
            raise BlueprintRepositoryError("blueprint_document_incoherent") from None
        results: list[StoredBlueprint] = []
        for row in rows:
            bp = self.get(row["blueprint_id"])
            if bp is None:
                raise BlueprintRepositoryError("blueprint_document_incoherent")
            results.append(bp)
        return results

    def _verify_proposal_event(
        self,
        *,
        attempt_id: str,
        canonical_digest: str,
        active_telos_digest: str,
        created_at: str,
    ) -> None:
        try:
            rows = self.ledger.connection.execute(
                """
                SELECT * FROM lifecycle_events
                WHERE attempt_id = ?
                LIMIT ?
                """,
                (attempt_id, _MAX_PROPOSAL_EVENTS + 1),
            ).fetchall()
            if len(rows) != _MAX_PROPOSAL_EVENTS:
                raise BlueprintRepositoryError(
                    "blueprint_document_incoherent"
                )
            event = self.ledger._stored(rows[0])
            previous_row = self.ledger.connection.execute(
                """
                SELECT event_digest
                FROM lifecycle_events
                WHERE event_sequence < ?
                ORDER BY event_sequence DESC
                LIMIT 1
                """,
                (event.event_sequence,),
            ).fetchone()
            actual_previous = (
                None
                if previous_row is None
                else str(previous_row["event_digest"])
            )
            expected_event_digest = content_digest(
                self.ledger._payload(event, actual_previous),
                domain=_EVENT_DIGEST_DOMAIN,
            )
        except BlueprintRepositoryError:
            raise
        except (
            EvolutionLedgerError,
            sqlite3.DatabaseError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            RecursionError,
        ):
            raise BlueprintRepositoryError(
                "blueprint_document_incoherent"
            ) from None

        if (
            event.previous_event_digest != actual_previous
            or event.event_digest != expected_event_digest
            or event.attempt_id != attempt_id
            or event.event_type != "blueprint_proposed"
            or event.prior_state is not None
            or event.next_state != "draft"
            or event.actor != "operator"
            or event.input_digests
            != (canonical_digest, active_telos_digest)
            or event.generation_id is not None
            or event.authorization_id is not None
            or event.reason_code != "blueprint_proposed"
            or event.reason_summary != "observer proposal created"
            or event.created_at != created_at
        ):
            raise BlueprintRepositoryError("blueprint_document_incoherent")
