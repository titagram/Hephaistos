"""Operator-facing proposal service with global safety gates.

Creates or retrieves a closed BlueprintDocument from an eligible suggestion,
enforcing the full gate order before any write-capable object is constructed.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from hermes_cli.evolution.blueprint_contract import (
    BlueprintContractError,
    BlueprintDocument,
    blueprint_document_from_suggestion,
)
from hermes_cli.evolution.blueprint_repository import (
    BlueprintRepository,
    BlueprintRepositoryError,
)
from hermes_cli.evolution.contract import require_digest
from hermes_cli.evolution.ledger import (
    BlueprintDraft,
    EvolutionLedger,
    EvolutionLedgerError,
)
from hermes_cli.evolution.locking import (
    LifecycleLockError,
    LifecycleLockTimeout,
    lifecycle_lock,
)
from hermes_cli.evolution.organism_home import OrganismHomeError, resolve_organism_root
from hermes_cli.evolution.organism_identity import (
    OrganismIdentityError,
    load_organism_identity,
)
from hermes_cli.evolution.suggestions import SuggestionRepository, SuggestionRepositoryError
from hermes_cli.evolution.telos_contract import TelosContractError
from hermes_cli.evolution.telos_store import TelosStore, TelosStoreError


_SUGGESTION_ID_PATTERN = re.compile(
    r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z",
    re.ASCII,
)


@dataclass(frozen=True)
class ProposalResult:
    status: Literal["created", "existing"]
    blueprint: BlueprintDraft


class ProposalError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def propose_suggestion(
    *,
    organism_root: Path,
    suggestion_id: str,
) -> ProposalResult:
    if (
        not isinstance(suggestion_id, str)
        or _SUGGESTION_ID_PATTERN.fullmatch(suggestion_id) is None
    ):
        raise ProposalError("invalid_suggestion_id")

    try:
        root = resolve_organism_root(organism_root)
    except (OrganismHomeError, OSError, TypeError):
        raise ProposalError("organism_root_unsafe") from None
    try:
        root.lstat()
    except FileNotFoundError:
        raise ProposalError("organism_identity_missing") from None
    except OSError:
        raise ProposalError("organism_root_unsafe") from None

    try:
        with lifecycle_lock(home=root, timeout_seconds=120):
            return _propose_under_lock(
                root=root,
                suggestion_id=suggestion_id,
            )

    except LifecycleLockTimeout:
        raise ProposalError("lifecycle_lock_timeout") from None
    except LifecycleLockError:
        raise ProposalError("lifecycle_lock_denied") from None
    except ProposalError:
        raise
    except Exception:
        raise ProposalError("proposal_internal_error") from None


def _propose_under_lock(
    *,
    root: Path,
    suggestion_id: str,
) -> ProposalResult:
    try:
        identity = load_organism_identity(root)
    except OrganismIdentityError:
        raise ProposalError("organism_identity_missing") from None
    except OrganismHomeError:
        raise ProposalError("organism_root_unsafe") from None

    db_path = root / "evolution" / "evolution.db"

    # SuggestionRepository is deliberately opened first: it validates an
    # existing current-schema database without creating or migrating it.
    try:
        suggestion_repository = SuggestionRepository(db_path)
    except SuggestionRepositoryError as exc:
        raise ProposalError(_ledger_preflight_code(exc)) from None

    try:
        ledger = EvolutionLedger(db_path)
    except (EvolutionLedgerError, sqlite3.DatabaseError):
        raise ProposalError("ledger_incoherent") from None

    try:
        try:
            chain_errors = ledger.verify_chain()
        except (EvolutionLedgerError, sqlite3.DatabaseError):
            raise ProposalError("lifecycle_chain_invalid") from None
        if chain_errors:
            raise ProposalError("lifecycle_chain_invalid")

        active_telos_digest = _active_telos_digest(
            root,
            organism_id=identity.organism_id,
        )

        try:
            suggestion = suggestion_repository.get_suggestion_by_id(
                suggestion_id
            )
        except SuggestionRepositoryError:
            raise ProposalError("suggestion_unavailable") from None
        if suggestion is None:
            raise ProposalError("suggestion_missing")
        if suggestion.state != "eligible":
            raise ProposalError("suggestion_not_eligible")
        if suggestion.active_telos_digest != active_telos_digest:
            raise ProposalError("suggestion_telos_mismatch")

        try:
            document: BlueprintDocument = blueprint_document_from_suggestion(
                suggestion,
                active_telos_digest=active_telos_digest,
            )
        except BlueprintContractError:
            raise ProposalError("blueprint_contract_invalid") from None

        try:
            draft = BlueprintRepository(ledger).create_or_get(document)
        except BlueprintRepositoryError:
            raise ProposalError("blueprint_write_failed") from None

        status: Literal["created", "existing"] = (
            "created" if draft.created else "existing"
        )
        return ProposalResult(status=status, blueprint=draft)
    finally:
        try:
            ledger.connection.close()
        except sqlite3.Error:
            pass


def _ledger_preflight_code(error: SuggestionRepositoryError) -> str:
    code = error.args[0] if len(error.args) == 1 else None
    if code == "observer_database_missing":
        return "ledger_not_found"
    if code == "observer_schema_unsupported":
        return "ledger_unsupported_schema"
    if code == "observer_schema_unavailable":
        return "ledger_empty"
    if code == "observer_database_unsafe":
        return "ledger_unsafe"
    return "ledger_incoherent"


def _active_telos_digest(root: Path, *, organism_id: str) -> str:
    try:
        store = TelosStore(root)
        digest = store.get_active_digest()
        if digest is None:
            raise ProposalError("no_active_telos")
        require_digest(digest)
        revision = store.get_revision(digest)
        if revision.canonical_digest != digest:
            raise ProposalError("no_active_telos")
        if revision.organism_id != organism_id:
            raise ProposalError("telos_organism_mismatch")
        return digest
    except ProposalError:
        raise
    except (
        EvolutionLedgerError,
        TelosContractError,
        TelosStoreError,
        json.JSONDecodeError,
        OSError,
        TypeError,
        ValueError,
    ):
        raise ProposalError("no_active_telos") from None
