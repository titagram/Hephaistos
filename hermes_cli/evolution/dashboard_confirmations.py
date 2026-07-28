"""Host-owned, one-time Telos confirmations for the local dashboard."""

from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .dashboard_service import EvolutionDashboardConflict, EvolutionDashboardError, EvolutionDashboardService
from .host_transition import perform_telos_transition, prepare_telos_pending_request
from .ledger import EvolutionLedger
from .telos_approval import HostApprovalContext
from .telos_store import TelosStore


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _expiry(value: object) -> datetime:
    if not isinstance(value, str):
        raise EvolutionDashboardConflict("confirmation_expired")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise EvolutionDashboardConflict("confirmation_expired") from None
    if parsed.tzinfo is None:
        raise EvolutionDashboardConflict("confirmation_expired")
    return parsed.astimezone(UTC)


@dataclass(frozen=True)
class _PendingConfirmation:
    context: HostApprovalContext
    organism_id: str
    expected_snapshot_digest: str
    current_digest: str
    target_digest: str
    action: str
    expires_at: str


class DashboardConfirmationStore:
    """Keep dashboard approval authority in process memory, never in a response.

    The class-level map intentionally disappears on process restart.  SQLite
    retains a harmless pending audit request, but it cannot move a Telos pointer
    without this in-memory host context.
    """

    _contexts: dict[tuple[str, str], _PendingConfirmation] = {}
    _contexts_lock = threading.RLock()

    def __init__(self, root: Path | None = None) -> None:
        self.service = EvolutionDashboardService(root)
        self.root = self.service.root

    def _key(self, confirmation_id: str) -> tuple[str, str]:
        return (str(self.root.absolute()), confirmation_id)

    def prepare(
        self,
        *,
        organism_id: str,
        expected_snapshot_digest: str,
        current_digest: str,
        target_digest: str,
        action: str,
    ) -> dict[str, str]:
        """Create a pending host request and reveal only its exact public prompt."""
        if action not in {"activate", "rollback"}:
            raise EvolutionDashboardConflict("telos_target_changed")
        with self.service._validated_mutation(
            organism_id=organism_id,
            expected_snapshot_digest=expected_snapshot_digest,
        ) as identity:
            try:
                store = TelosStore(self.root)
                if store.get_active_digest() != current_digest:
                    raise EvolutionDashboardConflict("telos_current_changed")
                session_ref = secrets.token_urlsafe(32)
                prepared = prepare_telos_pending_request(
                    digest=target_digest,
                    action=action,
                    surface="dashboard",
                    actor_ref="authenticated-local-operator",
                    session_ref=session_ref,
                    ttl_seconds=300,
                    organism_root=self.root,
                )
            except EvolutionDashboardConflict:
                raise
            except Exception:
                raise EvolutionDashboardError("telos_unavailable") from None

            if prepared.get("status") != "ok":
                raise EvolutionDashboardError("telos_unavailable")
            request_id = prepared.get("request_id")
            prompt = prepared.get("prompt_fields")
            if not isinstance(request_id, str) or not isinstance(prompt, dict):
                raise EvolutionDashboardError("telos_unavailable")
            nonce = prompt.get("display_nonce")
            expires_at = prompt.get("expires_at")
            context_digest = prompt.get("expected_host_context_digest")
            if not all(
                isinstance(value, str)
                for value in (nonce, expires_at, context_digest)
            ):
                raise EvolutionDashboardError("telos_unavailable")
            # The host transition recomputes this secret binding from the live
            # in-memory context.  It is never returned to the browser.
            context = HostApprovalContext(
                surface="dashboard",
                actor_ref="authenticated-local-operator",
                session_ref=session_ref,
                request_id=request_id,
                telos_digest=target_digest,
                action=action,
                nonce=nonce,
                context_digest=context_digest,
                expires_at=expires_at,
            )
            pending = _PendingConfirmation(
                context=context,
                organism_id=identity.organism_id,
                expected_snapshot_digest=expected_snapshot_digest,
                current_digest=current_digest,
                target_digest=target_digest,
                action=action,
                expires_at=expires_at,
            )
            with self._contexts_lock:
                self._contexts[self._key(request_id)] = pending

        return {
            "confirmation_id": request_id,
            "display_nonce": nonce,
            "organism_id": identity.organism_id,
            "current_digest": current_digest,
            "target_digest": target_digest,
            "action": action,
            "expires_at": expires_at,
            "required_phrase": (
                f"{action.upper()} {identity.organism_id[:8]} "
                f"{target_digest[:12]} {nonce}"
            ),
        }

    def confirm(
        self,
        *,
        confirmation_id: str,
        organism_id: str,
        expected_snapshot_digest: str,
        current_digest: str,
        target_digest: str,
        action: str,
        phrase: str,
    ) -> dict[str, str]:
        """Consume one pending context and complete its exact Telos transition."""
        if not isinstance(confirmation_id, str):
            raise EvolutionDashboardConflict("confirmation_not_found")
        with self._contexts_lock:
            pending = self._contexts.pop(self._key(confirmation_id), None)
        if pending is None:
            raise EvolutionDashboardConflict("confirmation_not_found")

        # Popping before validation makes every terminal attempt one-time,
        # including stale, malformed, expired, and rejected confirmations.
        with self.service._validated_mutation(
            organism_id=organism_id,
            expected_snapshot_digest=expected_snapshot_digest,
        ) as identity:
            if organism_id != pending.organism_id or identity.organism_id != pending.organism_id:
                raise EvolutionDashboardConflict("organism_changed")
            if expected_snapshot_digest != pending.expected_snapshot_digest:
                raise EvolutionDashboardConflict("snapshot_changed")
            if action != pending.action:
                raise EvolutionDashboardConflict("telos_target_changed")
            if current_digest != pending.current_digest:
                raise EvolutionDashboardConflict("telos_current_changed")
            if target_digest != pending.target_digest:
                raise EvolutionDashboardConflict("telos_target_changed")
            required_phrase = (
                f"{pending.action.upper()} {pending.organism_id[:8]} "
                f"{pending.target_digest[:12]} {pending.context.nonce}"
            )
            if not isinstance(phrase, str) or not secrets.compare_digest(
                phrase, required_phrase
            ):
                raise EvolutionDashboardConflict("confirmation_phrase_mismatch")
            if _utcnow() >= _expiry(pending.expires_at):
                raise EvolutionDashboardConflict("confirmation_expired")

            try:
                store = TelosStore(self.root)
                if store.get_active_digest() != pending.current_digest:
                    raise EvolutionDashboardConflict("telos_current_changed")
                ledger = EvolutionLedger(self.root / "evolution" / "evolution.db")
                try:
                    result = perform_telos_transition(
                        ledger, store, pending.context, "approved"
                    )
                finally:
                    ledger.connection.close()
            except EvolutionDashboardConflict:
                raise
            except Exception:
                raise EvolutionDashboardError("telos_unavailable") from None
            if result.status != "approved":
                raise EvolutionDashboardConflict("telos_transition_rejected")
        return {"status": "approved"}
