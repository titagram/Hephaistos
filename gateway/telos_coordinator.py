"""Gateway-owned Telos transition coordinator.

One in-memory instance lives on GatewayRunner.  Created during Gateway
initialisation, never exposed to model commands or serialised.  Restart
destroys coordinator state; pending requests need a new real host event.

The volatile ``TelosHostDecision`` is created inside the coordinator from
the actual ``MessageEvent`` and is immutable, single-use.

TelosStore contains **zero** pointer-mutating transition methods.
All host-authorised pointer publication lives in this module.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("gateway.telos_coordinator")


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass(frozen=True)
class TelosHostDecision:
    """Volatile, single-use host decision bound to a specific Telos request.

    Created inside the coordinator from a real MessageEvent.
    Immutable.  Revoked in ``finally`` after use.
    """

    request_id: str
    organism_id: str
    telos_digest: str
    action: str
    host_surface: str
    actor: str
    session_channel: str
    nonce: str
    context_digest: str
    expiry: str


class TelosCoordinator:
    """Gateway-owned Telos transition coordinator.

    Created during GatewayRunner initialisation.  The single instance
    per gateway process is never exposed to model commands or serialised.
    TelosStore contains zero pointer-mutating methods; all host-authorised
    pointer publication lives here.
    """

    def __init__(self) -> None:
        self._active_decision: TelosHostDecision | None = None

    def _reset_decision(self) -> None:
        self._active_decision = None

    async def approve(
        self,
        event: Any,
        request_id: str,
        session_key: str,
        ledger: Any,
        store: Any,
    ) -> str:
        """Process /approve telos <request-id> — complete host transition.

        Returns a bounded user-facing string with no sensitive details.
        """
        from hermes_cli.evolution.telos_approval import (
            HostApprovalContext,
            SqliteTelosApprovalBroker,
            TelosApprovalError,
            compute_context_digest,
        )
        from hermes_cli.evolution.telos_store import TelosStoreError

        broker = SqliteTelosApprovalBroker()

        source = event.source
        host_surface = "gateway"
        platform_val = getattr(source, "platform", None)
        pv = platform_val.value if hasattr(platform_val, "value") else str(platform_val or "?")
        user_id = getattr(source, "user_id", "") or ""
        actor = f"{pv}:{user_id}"
        session_channel = session_key

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
            (request_id,),
        ).fetchone()
        if req is None:
            return "Telos: request not found, expired, or already decided."

        expected_digest = req["expected_host_context_digest"]
        live_digest = compute_context_digest(
            host_surface, actor, session_channel,
            req["request_id"], req["display_nonce"],
        )
        if live_digest != expected_digest:
            return "Telos: context mismatch — request from a different session or actor."

        # ── Verify revision exists, digest matches, and organism matches ──
        try:
            revision = store.get_revision(req["telos_digest"])
        except TelosStoreError:
            return "Telos: revision not found for requested digest."

        if revision.canonical_digest != req["telos_digest"]:
            return "Telos: revision digest mismatch."

        if revision.organism_id != req["organism_id"]:
            return "Telos: revision organism mismatch."

        decision = TelosHostDecision(
            request_id=req["request_id"],
            organism_id=req["organism_id"],
            telos_digest=req["telos_digest"],
            action=req["action"],
            host_surface=host_surface,
            actor=actor,
            session_channel=session_channel,
            nonce=req["display_nonce"],
            context_digest=live_digest,
            expiry=req["expires_at"],
        )
        self._active_decision = decision

        try:
            if self._active_decision is not decision:
                logger.warning(
                    "Telos: volatile decision state changed before publication"
                )
                return "Telos: approval failed."

            ctx = HostApprovalContext(
                surface=host_surface,
                actor_ref=actor,
                session_ref=session_channel,
                request_id=request_id,
                telos_digest=req["telos_digest"],
                action=req["action"],
                nonce=req["display_nonce"],
                context_digest=live_digest,
            )

            dec_id = broker.record_host_decision(ledger, ctx, "approved")
            grant_id = broker.issue_grant(ledger, request_id, dec_id)
            consumption_id = broker.consume_grant(
                ledger, grant_id, req["organism_id"], req["telos_digest"], req["action"]
            )

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
                return "Telos: approval failed."

            if not (
                chain_row["request_id"] == decision.request_id
                and chain_row["decision_id"] == dec_id
                and chain_row["grant_id"] == grant_id
                and chain_row["consumption_id"] == consumption_id
                and chain_row["organism_id"] == decision.organism_id
                and chain_row["telos_digest"] == decision.telos_digest
                and chain_row["action"] == decision.action
                and chain_row["host_surface"] == decision.host_surface
                and chain_row["host_actor_ref"] == decision.actor
                and chain_row["display_nonce"] == decision.nonce
                and chain_row["context_digest"] == decision.context_digest
                and chain_row["expiry"] == decision.expiry
            ):
                logger.warning("Telos: chain verification failed for grant %s", grant_id)
                return "Telos: approval failed."

            now = _utcnow()

            if decision.action == "activate":
                if store.active_pointer.exists():
                    current_active = json.loads(
                        store.active_pointer.read_text(encoding="utf-8")
                    )
                    lkg_data = {"digest": current_active["digest"]}
                    tmp_lkg = store.lkg_pointer.with_suffix(".json.tmp")
                    tmp_lkg.write_text(
                        json.dumps(lkg_data, sort_keys=True), encoding="utf-8"
                    )
                    tmp_lkg.chmod(0o600)
                    tmp_lkg.rename(store.lkg_pointer)

                active_data = {
                    "digest": decision.telos_digest,
                    "activated_at": now,
                    "grant_id": grant_id,
                }
                tmp = store.active_pointer.with_suffix(".json.tmp")
                tmp.write_text(
                    json.dumps(active_data, sort_keys=True), encoding="utf-8"
                )
                tmp.chmod(0o600)
                tmp.rename(store.active_pointer)

            elif decision.action == "rollback":
                active_data = {
                    "digest": decision.telos_digest,
                    "activated_at": now,
                    "grant_id": grant_id,
                    "rollback": True,
                }
                tmp = store.active_pointer.with_suffix(".json.tmp")
                tmp.write_text(
                    json.dumps(active_data, sort_keys=True), encoding="utf-8"
                )
                tmp.chmod(0o600)
                tmp.rename(store.active_pointer)

            return f"Telos {decision.action} completed for request {request_id[:8]}."

        except (TelosApprovalError, TelosStoreError) as exc:
            logger.warning("Telos transition failed: %s", exc)
            return "Telos: approval failed."
        finally:
            self._reset_decision()

    async def deny(
        self,
        event: Any,
        request_id: str,
        session_key: str,
        ledger: Any,
    ) -> str:
        """Process /deny telos <request-id> — records denial, no transition."""
        from hermes_cli.evolution.telos_approval import (
            HostApprovalContext,
            SqliteTelosApprovalBroker,
            TelosApprovalError,
            compute_context_digest,
        )

        broker = SqliteTelosApprovalBroker()
        source = event.source
        host_surface = "gateway"
        platform_val = getattr(source, "platform", None)
        pv = platform_val.value if hasattr(platform_val, "value") else str(platform_val or "?")
        user_id = getattr(source, "user_id", "") or ""
        actor = f"{pv}:{user_id}"
        session_channel = session_key

        req = ledger.connection.execute(
            """SELECT r.request_id, r.expected_host_context_digest,
                      r.display_nonce, r.expires_at
               FROM telos_approval_requests r
               WHERE r.request_id = ?
                 AND r.expires_at > datetime('now')
                 AND NOT EXISTS (
                     SELECT 1 FROM telos_approval_decisions d
                     WHERE d.request_id = r.request_id
                 )""",
            (request_id,),
        ).fetchone()
        if req is None:
            return "Telos: request not found, expired, or already decided."

        expected_digest = req["expected_host_context_digest"]
        live_digest = compute_context_digest(
            host_surface, actor, session_channel,
            req["request_id"], req["display_nonce"],
        )
        if live_digest != expected_digest:
            return "Telos: context mismatch — request from a different session or actor."

        ctx = HostApprovalContext(
            surface=host_surface,
            actor_ref=actor,
            session_ref=session_channel,
            request_id=request_id,
            telos_digest="",
            action="",
            nonce=req["display_nonce"],
            context_digest=live_digest,
        )

        try:
            broker.record_host_decision(ledger, ctx, "denied")
            return f"Telos request {request_id[:8]} denied."
        except TelosApprovalError as exc:
            logger.warning("Telos deny failed: %s", exc)
            return "Telos: denial failed."
