"""Narrow internal host-transition service for Telos approval/denial/rollback.

Owned by ``hermes_cli/evolution``.  Not a model command or core tool.
Called by the Classic CLI host flow and the gateway-owned
``TelosCoordinator``.  No assert as authorization.  No env flag,
global registry, caller-supplied authority token, receipt, clarify,
or raw SQL as authority.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .ledger import EvolutionLedger
    from .telos_approval import HostApprovalContext
    from .telos_store import TelosStore

logger = logging.getLogger("evolution.host_transition")

# Held only by this host-side service.  It is not serialisable, persisted, or
# available to browser/CLI command inputs; telos_store verifies identity before
# any pointer write.
_TELOS_POINTER_CAPABILITY = object()


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass(frozen=True)
class TelosTransitionResult:
    """Result of a Telos transition — narrowly typed, no pointer/grant IDs leaked."""

    status: str       # "approved" | "denied" | "rejected"
    request_id: str
    message: str


def perform_telos_transition(
    ledger: EvolutionLedger,
    store: TelosStore | None,
    context: HostApprovalContext,
    decision: str,
) -> TelosTransitionResult:
    """Complete a Telos host transition (approval or denial).

    Re-validates the request against the ledger, verifies context digest,
    validates revision (for approval), records the decision, and for
    approval: issues grant, consumes, verifies the valid approval chain,
    and publishes the active/LKG pointer.

    The caller must have already derived the ``HostApprovalContext`` from a
    real host event (MessageEvent or interactive CLI prompt).  This function
    does NOT accept a caller-supplied pre-approved grant or arbitrary
    pointer path.

    Returns a bounded ``TelosTransitionResult`` with no sensitive IDs.
    """
    from .telos_approval import (
        HostApprovalContext,
        SqliteTelosApprovalBroker,
        TelosApprovalError,
        compute_context_digest,
    )
    from .telos_store import TelosStoreError, _publish_host_approved_transition

    broker = SqliteTelosApprovalBroker()

    if decision not in ("approved", "denied"):
        return TelosTransitionResult(
            status="rejected",
            request_id=context.request_id,
            message="invalid decision",
        )

    if decision == "approved" and store is None:
        return TelosTransitionResult(
            status="rejected",
            request_id=context.request_id,
            message="store required for approval",
        )

    if decision == "approved":
        if context.telos_digest == "" or context.action == "":
            return TelosTransitionResult(
                status="rejected",
                request_id=context.request_id,
                message="incomplete caller context — digest and action required for approval",
            )

    # ── Re-validate the request exists and is pending / unexpired ──
    req = ledger.connection.execute(
        """SELECT r.request_id, r.organism_id, r.telos_digest, r.action,
                  r.expected_host_context_digest, r.display_nonce,
                  r.expires_at
           FROM telos_approval_requests r
           WHERE r.request_id = ?
             AND r.expires_at > datetime('now')
             AND NOT EXISTS (
                 SELECT 1 FROM telos_approval_decisions d
                 WHERE d.request_id = r.request_id
             )""",
        (context.request_id,),
    ).fetchone()

    if req is None:
        return TelosTransitionResult(
            status="rejected",
            request_id=context.request_id,
            message="request not found, expired, or already decided",
        )

    expected_digest = req["expected_host_context_digest"]
    live_digest = compute_context_digest(
        context.surface, context.actor_ref, context.session_ref,
        context.request_id, req["display_nonce"],
    )
    if live_digest != expected_digest:
        return TelosTransitionResult(
            status="rejected",
            request_id=context.request_id,
            message="context mismatch — request from a different session or actor",
        )

    request_id = req["request_id"]
    organism_id = req["organism_id"]
    telos_digest = req["telos_digest"]
    action = req["action"]

    # ── For approval: verify revision exists, digest matches, organism matches ──
    if decision == "approved":
        try:
            revision = store.get_revision(telos_digest)
        except TelosStoreError:
            return TelosTransitionResult(
                status="rejected",
                request_id=request_id,
                message="revision not found for requested digest",
            )

        if revision.canonical_digest != telos_digest:
            return TelosTransitionResult(
                status="rejected",
                request_id=request_id,
                message="revision digest mismatch",
            )

        if revision.organism_id != organism_id:
            return TelosTransitionResult(
                status="rejected",
                request_id=request_id,
                message="revision organism mismatch",
            )

        if context.action != action:
            return TelosTransitionResult(
                status="rejected",
                request_id=request_id,
                message="caller action does not match persisted request action",
            )

        if context.telos_digest != telos_digest:
            return TelosTransitionResult(
                status="rejected",
                request_id=request_id,
                message="caller digest does not match persisted request digest",
            )

    # ── Build a context with the correct request_id for recording decisions ──
    decision_ctx = HostApprovalContext(
        surface=context.surface,
        actor_ref=context.actor_ref,
        session_ref=context.session_ref,
        request_id=request_id,
        telos_digest=telos_digest if decision == "approved" else context.telos_digest,
        action=action if decision == "approved" else context.action,
        nonce=req["display_nonce"],
        context_digest=live_digest,
    )

    try:
        dec_id = broker.record_host_decision(ledger, decision_ctx, decision)
    except TelosApprovalError as exc:
        logger.warning("Telos decision recording failed: %s", exc)
        return TelosTransitionResult(
            status="rejected",
            request_id=request_id,
            message="decision recording failed",
        )

    if decision == "denied":
        return TelosTransitionResult(
            status="denied",
            request_id=request_id,
            message="denied",
        )

    # ── Approval path: issue grant, consume, verify chain, publish pointer ──
    try:
        grant_id = broker.issue_grant(ledger, request_id, dec_id)
    except TelosApprovalError as exc:
        logger.warning("Telos grant issue failed: %s", exc)
        return TelosTransitionResult(
            status="rejected",
            request_id=request_id,
            message="grant issue failed",
        )

    try:
        consumption_id = broker.consume_grant(
            ledger, grant_id, organism_id, telos_digest, action,
        )
    except TelosApprovalError as exc:
        logger.warning("Telos grant consumption failed: %s", exc)
        return TelosTransitionResult(
            status="rejected",
            request_id=request_id,
            message="grant consumption failed",
        )

    # ── Verify valid approval chain ──
    chain_row = ledger.connection.execute(
        """SELECT request_id, decision_id, grant_id, consumption_id,
                  organism_id, telos_digest, action,
                  host_surface, host_actor_ref, display_nonce,
                  expected_host_context_digest AS context_digest,
                  request_expires_at AS expiry
           FROM telos_valid_approval_chains
           WHERE grant_id = ?""",
        (grant_id,),
    ).fetchone()

    if chain_row is None:
        logger.warning("Telos: no valid chain for grant %s", grant_id)
        return TelosTransitionResult(
            status="rejected",
            request_id=request_id,
            message="chain verification failed",
        )

    if not (
        chain_row["request_id"] == request_id
        and chain_row["decision_id"] == dec_id
        and chain_row["grant_id"] == grant_id
        and chain_row["consumption_id"] == consumption_id
        and chain_row["organism_id"] == organism_id
        and chain_row["telos_digest"] == telos_digest
        and chain_row["action"] == action
        and chain_row["host_surface"] == context.surface
        and chain_row["host_actor_ref"] == context.actor_ref
        and chain_row["display_nonce"] == req["display_nonce"]
        and chain_row["context_digest"] == live_digest
        and chain_row["expiry"] == req["expires_at"]
    ):
        logger.warning("Telos: chain verification failed for grant %s", grant_id)
        return TelosTransitionResult(
            status="rejected",
            request_id=request_id,
            message="chain verification fields mismatch",
        )

    # ── Atomically publish active/lkg pointer through the host-only bridge ──
    # The request was validated above without mutating authority records.  The
    # bridge repeats revision proof immediately before the first Telos write,
    # and accepts only this in-memory host-transition capability.
    try:
        _publish_host_approved_transition(
            store,
            capability=_TELOS_POINTER_CAPABILITY,
            organism_id=organism_id,
            digest=telos_digest,
            grant_id=grant_id,
            action=action,
            now=_utcnow(),
        )
    except TelosStoreError as exc:
        if str(exc) == "telos_revision_changed":
            return TelosTransitionResult(
                status="rejected",
                request_id=request_id,
                message="revision changed before publication",
            )
        logger.warning("Telos pointer publication failed safely: %s", exc)
        return TelosTransitionResult(
            status="rejected",
            request_id=request_id,
            message="pointer publication failed",
        )

    return TelosTransitionResult(
        status="approved",
        request_id=request_id,
        message="completed",
    )


def prepare_telos_pending_request(
    digest: str,
    action: str,
    surface: str,
    actor_ref: str,
    session_ref: str,
    ttl_seconds: int = 3600,
    organism_root: str | os.PathLike[str] | None = None,
) -> dict[str, object]:
    """Prepare a pending Telos approval request for host interaction.

    Resolves the global organism root, validates the revision, creates
    a pending broker request, and returns the persisted row fields as a
    bounded dict (no pointer or grant mutation).

    Returns ``{"status": "ok", "request_id": ..., "prompt_fields": {...}}``
    on success, or ``{"status": "rejected"|"invalid", "request_id": None,
    "message": ...}`` on failure.

    Used by both the Classic CLI host flow and the TUI/Desktop JSON-RPC
    host flow to avoid duplicating organism-resolution and validation logic.
    """
    import uuid as _uuid_mod
    from .organism_home import get_organism_home
    from .organism_identity import load_organism_identity
    from .telos_store import TelosStore, TelosStoreError
    from .telos_approval import (
        HostApprovalContext,
        SqliteTelosApprovalBroker,
        TelosApprovalError,
    )
    from .ledger import EvolutionLedger

    if action not in ("activate", "rollback"):
        return {
            "status": "invalid",
            "request_id": None,
            "message": "unsupported action",
        }

    if not surface or not actor_ref or not session_ref:
        return {
            "status": "invalid",
            "request_id": None,
            "message": "incomplete host context — surface, actor, and session required",
        }

    if not digest or len(digest) != 64 or not all(c in "0123456789abcdef" for c in digest):
        return {
            "status": "invalid",
            "request_id": None,
            "message": "invalid digest",
        }

    root = Path(organism_root) if organism_root else get_organism_home()

    try:
        ident = load_organism_identity(root)
    except Exception:
        return {
            "status": "rejected",
            "request_id": None,
            "message": "organism identity not found",
        }
    organism_id = ident.organism_id

    store = TelosStore(root)
    try:
        revision = store.get_revision(digest)
    except TelosStoreError:
        return {
            "status": "rejected",
            "request_id": None,
            "message": "revision not found for requested digest",
        }

    if revision.canonical_digest != digest:
        return {
            "status": "rejected",
            "request_id": None,
            "message": "revision digest mismatch",
        }

    if revision.organism_id != organism_id:
        return {
            "status": "rejected",
            "request_id": None,
            "message": "revision organism mismatch",
        }

    ledger_path = root / "evolution" / "evolution.db"
    if not ledger_path.exists():
        return {
            "status": "rejected",
            "request_id": None,
            "message": "no organism ledger found",
        }

    broker = SqliteTelosApprovalBroker()
    ledger = EvolutionLedger(ledger_path)

    nonce = str(_uuid_mod.uuid4())[:8]

    ctx = HostApprovalContext(
        surface=surface,
        actor_ref=actor_ref,
        session_ref=session_ref,
        request_id=None,
        telos_digest=digest,
        action=action,
        nonce=nonce,
        context_digest="",
    )

    request_id = None
    try:
        request_id = broker.create_request(ledger, organism_id, digest, action, ctx, ttl_seconds)

        row = ledger.connection.execute(
            """SELECT request_id, organism_id, telos_digest, action,
                      display_nonce, bounded_summary,
                      expected_host_context_digest, expires_at
               FROM telos_approval_requests
               WHERE request_id = ?""",
            (request_id,),
        ).fetchone()

        if row is None:
            return {
                "status": "rejected",
                "request_id": request_id,
                "message": "persisted request row not found",
            }

        return {
            "status": "ok",
            "request_id": request_id,
            "prompt_fields": {
                "request_id": row["request_id"],
                "organism_id": row["organism_id"],
                "telos_digest": row["telos_digest"],
                "action": row["action"],
                "display_nonce": row["display_nonce"],
                "bounded_summary": row["bounded_summary"],
                "expected_host_context_digest": row["expected_host_context_digest"],
                "expires_at": row["expires_at"],
            },
        }
    except TelosApprovalError as exc:
        return {
            "status": "rejected",
            "request_id": request_id if request_id else None,
            "message": str(exc),
        }
    finally:
        ledger.connection.close()
