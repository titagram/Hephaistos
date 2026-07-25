"""Host approval capability, registry, and broker for Telos authorization.

The model-controlled shell cannot forge a HostApprovalCapability because:
- No public constructor exists — only the internal host adapter factory creates them
- The CapabilityRegistry stores live objects and verifies by identity (``is``)
- Capabilities are in-memory, non-serializable, single-use, and bound to context
"""

from __future__ import annotations

import secrets
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .ledger import EvolutionLedger


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
    Verified by identity (Python ``is``) against the active registry.
    Never written to disk, DB, or transmitted to the model.
    """

    __slots__ = ("_token", "_surface", "_actor_ref")

    def __init__(self, surface: str, actor_ref: str) -> None:
        self._token = secrets.token_urlsafe(32)
        self._surface = surface
        self._actor_ref = actor_ref

    @classmethod
    def _test_create(cls, surface: str, actor_ref: str) -> HostApprovalCapability:
        """Create a capability for testing only.

        NOT available in production — host adapters use their own internal factory.
        """
        return cls(surface, actor_ref)

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
