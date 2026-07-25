"""Host approval capability, registry, and broker for Telos authorization.

The model-controlled shell cannot forge a HostApprovalCapability because:
- No public constructor exists — only the internal host adapter factory creates them
- The CapabilityRegistry stores live objects and verifies by identity (``is``)
- Capabilities are in-memory, non-serializable, single-use, and bound to context

Host surfaces (CLI, gateway, TUI) register capabilities in the module-level
_HOST_REGISTRY. activate_revision/rollback check against this registry.
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


# -- Module-level host registry --
# Only host surfaces (CLI, gateway, TUI) populate this. The model-controlled
# process does not have access to a register() here -- it can construct its
# own CapabilityRegistry but it will not be THIS one.

_HOST_REGISTRY = None  # set after CapabilityRegistry class def below


def set_host_capability(capability):
    """Register a capability in the process-global host registry.

    Only authorized host surfaces (CLI, Gateway, TUI) and test helpers
    may call this function.  Stack inspection verifies the caller's file
    is within the authorized set.
    """
    _verify_authorized_caller()
    global _HOST_REGISTRY
    if _HOST_REGISTRY is None:
        _HOST_REGISTRY = CapabilityRegistry()
    _HOST_REGISTRY.register(capability)


def _verify_authorized_caller() -> None:
    """Raise PermissionError if the immediate caller is not authorized."""
    import inspect
    stack = inspect.stack()
    # Walk the stack to find the first frame outside this module
    this_file = __file__
    for frame_info in stack:
        caller_file = frame_info.filename
        if caller_file == this_file:
            continue  # skip frames inside telos_approval.py itself
        # Check against authorized suffixes
        for suffix in _AUTHORIZED_HOST_PATH_SUFFIXES:
            if suffix in caller_file:
                return  # authorized
        raise PermissionError(
            f"set_host_capability rejected: caller {caller_file!r} "
            f"is not an authorized host surface"
        )
    raise PermissionError(
        "set_host_capability rejected: could not determine caller"
    )


def clear_host_capability(capability):
    """Revoke and clear a capability from the host registry."""
    global _HOST_REGISTRY
    if _HOST_REGISTRY is not None:
        _HOST_REGISTRY.revoke(capability)


def verify_host_capability(capability):
    """Check whether a capability is registered in the host registry."""
    if _HOST_REGISTRY is None:
        return False
    return _HOST_REGISTRY.verify(capability)


import secrets
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .ledger import EvolutionLedger


# -- Per-process mint token --
# Only host adapter factories (CLI, Gateway, TUI) and test helpers may create
# HostApprovalCapability instances.  The token is generated at import time and
# never exposed through the public API surface.
_MINT_TOKEN = secrets.token_urlsafe(32)


# -- Authorized caller paths for set_host_capability --
# set_host_capability() verifies the caller's file via stack inspection.
_AUTHORIZED_HOST_PATH_SUFFIXES = frozenset({
    # Real host surfaces
    "gateway/slash_commands.py",
    "hermes_cli/cli/telos_prompt.py",
    # Test files — test helpers call set_host_capability from within tests/
    "tests/",
})


class TelosApprovalError(RuntimeError):
    """Bounded error during Telos approval workflow."""


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


class HostApprovalCapability:
    """An in-process, non-serializable token proving host origin.

    Created only by the host adapter factory (no public constructor).
    Verified by identity (Python ``is``) against the active registry AND
    by exact context binding — the capability must match the requested
    organism_id, digest, action, surface, and actor_ref.

    Never written to disk, DB, or transmitted to the model.

    Single-use: consumed (revoked from registry) after one successful
    Telos transition.
    """

    __slots__ = (
        "_token", "_surface", "_actor_ref",
        "_organism_id", "_digest", "_action",
        "_consumed",
    )

    def __init__(self, surface: str, actor_ref: str, *, _mint_token: str = "") -> None:
        if _mint_token != _MINT_TOKEN:
            raise PermissionError(
                "HostApprovalCapability must be created through an authorized "
                "host adapter factory. Direct instantiation is not permitted."
            )
        self._token = secrets.token_urlsafe(32)
        self._surface = surface
        self._actor_ref = actor_ref
        self._organism_id: str | None = None
        self._digest: str | None = None
        self._action: str | None = None
        self._consumed: bool = False

    @classmethod
    def _test_create(cls, surface: str, actor_ref: str) -> "HostApprovalCapability":
        """Create a capability for testing only.

        NOT available in production — host adapters use their own internal factory.
        """
        return cls(surface, actor_ref, _mint_token=_MINT_TOKEN)

    def bind_to_request(
        self,
        organism_id: str,
        digest: str,
        action: str,
    ) -> None:
        """Bind this capability to a specific Telos request context.

        Must be called by the host surface before activation/rollback.
        Once bound, the capability can only authorize the exact context.
        """
        self._organism_id = organism_id
        self._digest = digest
        self._action = action

    def matches_request(
        self,
        organism_id: str,
        digest: str,
        action: str,
    ) -> bool:
        """Check if this capability is bound to and matches the given context."""
        if self._consumed:
            return False
        if self._organism_id is None:
            # Legacy capability without binding — reject for safety
            return False
        return (
            self._organism_id == organism_id
            and self._digest == digest
            and self._action == action
        )

    def mark_consumed(self) -> None:
        """Mark this capability as consumed (single-use enforcement)."""
        self._consumed = True

    @property
    def consumed(self) -> bool:
        return self._consumed

    @property
    def surface(self) -> str:
        return self._surface

    @property
    def actor_ref(self) -> str:
        return self._actor_ref

    def __eq__(self, other: object) -> bool:
        """Identity-based equality — value equality is not supported."""
        return self is other

    def __hash__(self) -> int:
        return id(self)


class CapabilityRegistry:
    """Per-process registry of active capabilities.

    Stores live capability objects. Verifies by identity (not value).
    After restart, no capabilities survive.
    """

    def __init__(self) -> None:
        self._caps: list[HostApprovalCapability] = []

    def register(self, cap: HostApprovalCapability) -> None:
        """Register a capability minted by a host adapter."""
        self._caps.append(cap)

    def verify(self, cap: HostApprovalCapability) -> bool:
        """Check if *cap* is a registered live capability object."""
        return any(stored is cap for stored in self._caps)

    def revoke(self, cap: HostApprovalCapability) -> None:
        """Revoke a capability — removes by identity."""
        self._caps = [stored for stored in self._caps if stored is not cap]

    @property
    def active_count(self) -> int:
        return len(self._caps)


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
    - Verify capabilities via a CapabilityRegistry before recording decisions
    - Insert into append-only Telos tables with triggers
    - Enforce single-use grants and consumptions
    """

    def __init__(self, registry: CapabilityRegistry) -> None:
        self._registry = registry

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
        capability: HostApprovalCapability,
        context: HostApprovalContext,
        decision: str,
    ) -> str:
        """Record a host approval decision. Requires verified capability. Returns decision_id."""
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

    Implements all broker methods using the Telos append-only tables
    in the EvolutionLedger (created by v3→v4 migration).
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
        import uuid
        from datetime import datetime, timezone, timedelta

        request_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=ttl_seconds)

        with ledger.transaction() as conn:
            conn.execute(
                """INSERT INTO telos_approval_requests
                   (request_id, organism_id, telos_digest, action,
                    expected_host_context_digest, display_nonce,
                    bounded_summary, created_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    request_id, organism_id, telos_digest, action,
                    context.context_digest, context.nonce,
                    f"Telos {action} for {organism_id[:8]}...",
                    now.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                    expires.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                ),
            )
        return request_id

    def record_host_decision(
        self,
        ledger: EvolutionLedger,
        capability: HostApprovalCapability,
        context: HostApprovalContext,
        decision: str,
    ) -> str:
        from datetime import datetime, timezone

        if not self._registry.verify(capability):
            raise TelosApprovalError("telos_capability_not_verified")

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
        from datetime import datetime, timezone, timedelta
        import uuid

        grant_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        expires = now + timedelta(hours=1)

        with ledger.transaction() as conn:
            # Verify request exists
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
        from datetime import datetime, timezone
        import uuid

        consumption_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        with ledger.transaction() as conn:
            # Verify grant exists and hasn't expired
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

    Returns a HostApprovalDecision. Timeout, Ctrl-C, EOF, and invalid input
    all result in 'denied'. Only explicit 'y' or 'yes' is 'approved'.
    """
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

    try:
        from prompt_toolkit.shortcuts import PromptSession
        session = PromptSession()
        answer = session.prompt(
            "\n".join(lines) + "\n> ",
            timeout=timeout,
        )
    except (TimeoutError, EOFError, KeyboardInterrupt):
        answer = "n"

    decision = "approved" if answer.strip().lower() in ("y", "yes") else "denied"
    return HostApprovalDecision(
        request_id=prompt.request_id,
        decision=decision,
        host_surface="classic_cli",
        host_actor_ref="interactive",
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
    )
