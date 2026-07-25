"""Host approval broker for Telos authorization.

The SQLiteTelosApprovalBroker is an internal implementation detail used by
host-owned adapters such as the gateway coordinator and interactive Classic
CLI. Coherent SQLite rows alone never invoke a pointer mutation.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid as _uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, fields
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .ledger import EvolutionLedger


class TelosApprovalError(RuntimeError):
    """Bounded error during Telos approval workflow."""


def compute_context_digest(
    surface: str, actor: str, session: str,
    request_id: str, nonce: str,
) -> str:
    """Canonical context digest binding host surface, actor, session, request_id, and nonce.

    Uses canonical JSON (sorted-key, compact) with a fixed domain/version marker
    to prevent structural delimiter collisions (e.g. ``::`` in any field value).

    ``create_request`` stores this as ``expected_host_context_digest``.
    The gateway-owned coordinator re-computes the same digest from the
    live event and request row during ``approve`` / ``deny``.
    """
    raw = json.dumps(
        ["telos-host-context-v1", surface, actor, session, request_id, nonce],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class HostApprovalContext:
    """Context bound to a Telos approval request."""

    surface: str
    actor_ref: str
    session_ref: str
    request_id: str | None
    telos_digest: str
    action: str  # 'activate' | 'rollback'
    nonce: str
    context_digest: str
    expires_at: str | None = None


@dataclass(frozen=True)
class TelosApprovalPrompt:
    """Data sent to a host surface for user approval."""

    request_id: str
    organism_id: str
    telos_digest: str
    action: str
    display_nonce: str
    bounded_summary: str
    host_context_digest: str
    expires_at: str | None = None


@dataclass(frozen=True)
class HostApprovalDecision:
    """Result of a host approval prompt."""

    request_id: str
    decision: str  # 'approved' | 'denied'
    host_surface: str
    host_actor_ref: str
    timestamp: str


class TelosApprovalBroker(ABC):
    """ABC for the Telos approval broker — connects host decisions to SQLite.

    The implementation must:
    - Insert into append-only Telos tables with triggers
    - Enforce single-use grants and consumptions
    """

    @abstractmethod
    def create_request(
        self,
        ledger: EvolutionLedger,
        organism_id: str,
        telos_digest: str,
        action: str,
        context: HostApprovalContext,
        ttl_seconds: int,
    ) -> str:
        """Create a pending approval request. Returns request_id."""
        ...

    @abstractmethod
    def record_host_decision(
        self,
        ledger: EvolutionLedger,
        context: HostApprovalContext,
        decision: str,
    ) -> str:
        """Record a host approval decision. Returns decision_id."""
        ...

    @abstractmethod
    def issue_grant(
        self,
        ledger: EvolutionLedger,
        request_id: str,
        decision_id: str,
    ) -> str:
        """Issue a grant from an approved decision. Returns grant_id."""
        ...

    @abstractmethod
    def consume_grant(
        self,
        ledger: EvolutionLedger,
        grant_id: str,
        organism_id: str,
        telos_digest: str,
        action: str,
    ) -> str:
        """Consume a grant, making Telos activation possible. Returns consumption_id."""
        ...

    @abstractmethod
    def get_pending_requests(
        self,
        ledger: EvolutionLedger,
        organism_id: str,
    ) -> list[dict]:
        """Return pending (no decision yet) requests for an organism."""
        ...


class SqliteTelosApprovalBroker(TelosApprovalBroker):
    """SQLite-backed Telos approval broker.

    Internal implementation detail — used by the gateway-owned TelosCoordinator.
    Not guarded by a host capability, because the coordinator IS the host
    authority.  Model callers can write SQLite rows through this broker,
    but coherent rows alone never invoke a pointer mutation.
    """

    def create_request(
        self,
        ledger: EvolutionLedger,
        organism_id: str,
        telos_digest: str,
        action: str,
        context: HostApprovalContext,
        ttl_seconds: int,
    ) -> str:
        if action not in ("activate", "rollback"):
            raise TelosApprovalError(
                f"telos_invalid_action: expected activate or rollback, got {action!r}"
            )
        if not organism_id:
            raise TelosApprovalError("telos_empty_organism_id")
        if len(telos_digest) != 64 or not all(
            c in "0123456789abcdefABCDEF" for c in telos_digest
        ):
            raise TelosApprovalError(
                "telos_invalid_digest: must be 64 hex characters"
            )
        if ttl_seconds <= 0 or ttl_seconds > 86400:
            raise TelosApprovalError(
                "telos_invalid_ttl: must be between 1 and 86400 seconds"
            )
        if context.action != action or context.telos_digest != telos_digest:
            raise TelosApprovalError("telos_request_context_mismatch")
        if not all(
            (
                context.surface,
                context.actor_ref,
                context.session_ref,
                context.nonce,
            )
        ):
            raise TelosApprovalError("telos_request_context_incomplete")

        request_id = str(_uuid.uuid4())
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=ttl_seconds)
        actual_digest = compute_context_digest(
            context.surface, context.actor_ref, context.session_ref,
            request_id, context.nonce,
        )

        with ledger.transaction() as conn:
            conn.execute(
                """INSERT INTO telos_approval_requests
                   (request_id, organism_id, telos_digest, action,
                    expected_host_context_digest, display_nonce,
                    bounded_summary, created_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    request_id, organism_id, telos_digest, action,
                    actual_digest, context.nonce,
                    f"Telos {action} for {organism_id[:8]}...",
                    now.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                    expires.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                ),
            )
        return request_id

    def record_host_decision(
        self,
        ledger: EvolutionLedger,
        context: HostApprovalContext,
        decision: str,
    ) -> str:
        from datetime import datetime, timezone

        if decision not in ("approved", "denied"):
            raise TelosApprovalError("telos_invalid_decision")

        import uuid
        decision_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        try:
            with ledger.transaction() as conn:
                conn.execute(
                    """INSERT INTO telos_approval_decisions
                       (decision_id, request_id, decision,
                        host_surface, host_actor_ref,
                        host_context_digest, decided_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        decision_id, context.request_id, decision,
                        context.surface, context.actor_ref,
                        context.context_digest,
                        now.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                    ),
                )
        except Exception:
            raise TelosApprovalError("telos_decision_failed") from None

        return decision_id

    def issue_grant(
        self,
        ledger: EvolutionLedger,
        request_id: str,
        decision_id: str,
    ) -> str:
        import uuid
        from datetime import datetime, timedelta, timezone

        grant_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        expires = now + timedelta(hours=1)

        with ledger.transaction() as conn:
            req = conn.execute(
                "SELECT organism_id, telos_digest, action FROM telos_approval_requests WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if req is None:
                raise TelosApprovalError("telos_request_not_found")

            try:
                conn.execute(
                    """INSERT INTO telos_approval_grants
                       (grant_id, request_id, decision_id,
                        organism_id, telos_digest, action,
                        issued_at, expires_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        grant_id, request_id, decision_id,
                        req["organism_id"], req["telos_digest"], req["action"],
                        now.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                        expires.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                    ),
                )
            except Exception:
                raise TelosApprovalError("telos_grant_failed") from None

        return grant_id

    def consume_grant(
        self,
        ledger: EvolutionLedger,
        grant_id: str,
        organism_id: str,
        telos_digest: str,
        action: str,
    ) -> str:
        import uuid
        from datetime import datetime, timezone

        consumption_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        with ledger.transaction() as conn:
            grant = conn.execute(
                "SELECT organism_id, telos_digest, action, expires_at FROM telos_approval_grants WHERE grant_id = ?",
                (grant_id,),
            ).fetchone()
            if grant is None:
                raise TelosApprovalError("telos_grant_not_found")
            if grant["organism_id"] != organism_id:
                raise TelosApprovalError("telos_grant_organism_mismatch")
            if grant["telos_digest"] != telos_digest:
                raise TelosApprovalError("telos_grant_digest_mismatch")
            if grant["action"] != action:
                raise TelosApprovalError("telos_grant_action_mismatch")

            try:
                conn.execute(
                    """INSERT INTO telos_approval_consumptions
                       (consumption_id, grant_id, organism_id,
                        telos_digest, action, consumed_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        consumption_id, grant_id, organism_id,
                        telos_digest, action,
                        now.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                    ),
                )
            except Exception:
                raise TelosApprovalError("telos_consumption_failed") from None

        return consumption_id

    def get_pending_requests(
        self,
        ledger: EvolutionLedger,
        organism_id: str,
    ) -> list[dict]:
        rows = ledger.connection.execute(
            """SELECT r.request_id, r.telos_digest, r.action, r.display_nonce,
                      r.bounded_summary, r.created_at, r.expires_at
               FROM telos_approval_requests r
               WHERE r.organism_id = ?
                 AND NOT EXISTS (
                     SELECT 1 FROM telos_approval_decisions d
                     WHERE d.request_id = r.request_id
                 )
               ORDER BY r.created_at""",
            (organism_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def telos_approval_prompt(prompt: TelosApprovalPrompt, timeout: int = 120) -> HostApprovalDecision:
    """Show a prompt_toolkit-based Telos approval prompt to the user.

    Uses ``PromptSession.prompt_async`` (the documented async API) with
    ``asyncio.wait_for`` for a bounded timeout.  The synchronous classic-CLI
    wrapper uses ``asyncio.run`` to bridge into the event loop.

    Timeout, Ctrl-C, EOF, and invalid input all result in 'denied'.
    Only explicit 'y' or 'yes' is 'approved'.

    ``PromptSession.prompt`` does **not** accept a ``timeout`` kwarg;
    this function uses the supported async path only.
    """
    import asyncio
    from datetime import datetime, timezone

    lines = [
        "══════════════════════════════════════════",
        f" Telos {prompt.action.upper()} Request",
        "══════════════════════════════════════════",
        f" Digest:  {prompt.telos_digest[:16]}...",
        f" Nonce:   {prompt.display_nonce}",
        f" Summary: {prompt.bounded_summary}",
        "──────────────────────────────────────────",
        "Type 'y' to approve or 'n' to deny.",
    ]

    async def _read_prompt() -> str:
        from prompt_toolkit.shortcuts import PromptSession
        session = PromptSession()
        try:
            return await asyncio.wait_for(
                session.prompt_async("\n".join(lines) + "\n> "),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            raise TimeoutError("Telos approval prompt timed out")

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        running_loop = False
    else:
        running_loop = True

    if running_loop:
        # ``asyncio.run`` cannot nest inside a live event loop. This synchronous
        # Classic CLI adapter therefore fails closed without creating a coroutine.
        answer = None
    else:
        try:
            answer = asyncio.run(_read_prompt())
        except (TimeoutError, EOFError, KeyboardInterrupt):
            answer = None

    if answer is None:
        answer = "n"

    decision = "approved" if answer.strip().lower() in ("y", "yes") else "denied"
    return HostApprovalDecision(
        request_id=prompt.request_id,
        decision=decision,
        host_surface="classic_cli",
        host_actor_ref="interactive",
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
    )
