"""Gateway-owned Telos transition coordinator.

One in-memory instance lives on GatewayRunner.  Created during Gateway
initialisation, never exposed to model commands or serialised.  Restart
destroys coordinator state; pending requests need a new real host event.

The volatile ``TelosHostDecision`` is created inside the coordinator from
the actual ``MessageEvent`` and is immutable, single-use.

TelosStore contains **zero** pointer-mutating transition methods.
All host-authorised pointer publication lives in the shared
``hermes_cli.evolution.host_transition`` module, called by both
this coordinator and the Classic CLI host flow.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("gateway.telos_coordinator")


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
    Context derivation remains here; transition logic is delegated to
    ``hermes_cli.evolution.host_transition.perform_telos_transition``.
    """

    def __init__(self) -> None:
        self._active_decision: TelosHostDecision | None = None

    def _reset_decision(self) -> None:
        self._active_decision = None

    def _derive_context(self, event: Any, session_key: str) -> dict[str, str]:
        """Derive host context from a real MessageEvent.  Returns surface, actor, session."""
        source = event.source
        host_surface = "gateway"
        platform_val = getattr(source, "platform", None)
        pv = platform_val.value if hasattr(platform_val, "value") else str(platform_val or "?")
        user_id = getattr(source, "user_id", "") or ""
        actor = f"{pv}:{user_id}"
        return {
            "surface": host_surface,
            "actor": actor,
            "session": session_key,
        }

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
        from hermes_cli.evolution.host_transition import perform_telos_transition
        from hermes_cli.evolution.telos_approval import (
            HostApprovalContext,
            compute_context_digest,
        )

        ctx_info = self._derive_context(event, session_key)

        # ── Quick existence check for TelosHostDecision construction ──
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

        live_digest = compute_context_digest(
            ctx_info["surface"], ctx_info["actor"], ctx_info["session"],
            req["request_id"], req["display_nonce"],
        )

        if live_digest != req["expected_host_context_digest"]:
            return "Telos: context mismatch — request from a different session or actor."

        # ── Volatile interlock ──
        decision = TelosHostDecision(
            request_id=req["request_id"],
            organism_id=req["organism_id"],
            telos_digest=req["telos_digest"],
            action=req["action"],
            host_surface=ctx_info["surface"],
            actor=ctx_info["actor"],
            session_channel=ctx_info["session"],
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

            context = HostApprovalContext(
                surface=ctx_info["surface"],
                actor_ref=ctx_info["actor"],
                session_ref=ctx_info["session"],
                request_id=request_id,
                telos_digest=req["telos_digest"],
                action=req["action"],
                nonce=req["display_nonce"],
                context_digest=live_digest,
            )

            result = perform_telos_transition(ledger, store, context, "approved")

            if result.status == "approved":
                return f"Telos {req['action']} completed for request {request_id[:8]}."
            if result.message == "context mismatch — request from a different session or actor":
                return "Telos: context mismatch — request from a different session or actor."
            if result.message == "revision not found for requested digest":
                return "Telos: revision not found for requested digest."
            if result.message == "revision digest mismatch":
                return "Telos: revision digest mismatch."
            if result.message == "revision organism mismatch":
                return "Telos: revision organism mismatch."
            logger.warning("Telos transition rejected: %s", result.message)
            return "Telos: approval failed."
        except Exception as exc:
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
        from hermes_cli.evolution.host_transition import perform_telos_transition
        from hermes_cli.evolution.telos_approval import HostApprovalContext

        ctx_info = self._derive_context(event, session_key)

        context = HostApprovalContext(
            surface=ctx_info["surface"],
            actor_ref=ctx_info["actor"],
            session_ref=ctx_info["session"],
            request_id=request_id,
            telos_digest="",
            action="",
            nonce="",
            context_digest="",
        )

        try:
            result = perform_telos_transition(ledger, None, context, "denied")
        except Exception as exc:
            logger.warning("Telos deny failed: %s", exc)
            return "Telos: denial failed."

        if result.status == "denied":
            return f"Telos request {request_id[:8]} denied."
        if result.message == "context mismatch — request from a different session or actor":
            return "Telos: context mismatch — request from a different session or actor."
        return "Telos: denial failed."
