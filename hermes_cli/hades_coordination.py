"""Curated local-only Hades coordination profiles and heartbeat coordination."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from copy import deepcopy
from typing import Any, Callable, TypeVar

from hermes_cli.hades_backend_client import HadesBackendClient, HadesBackendError
from hermes_cli.hades_agent_coordination import DelegationAuthority, LeafManifest

logger = logging.getLogger(__name__)

T = TypeVar("T")  # Generic return type for claim_and_run runner


_PROFILE_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "id": "planner",
        "title": "Planning and decomposition",
        "description": "Break shared backend work into bounded local tasks before delegation.",
        "skill": "autonomous-ai-agents/hades-coordination",
        "model_routing": {
            "local_model_profile": "hades.planner",
            "selector": "strongest_allowed",
            "provider_source": "config.yaml",
        },
        "budget": {"max_turns": 8, "max_runtime_seconds": 900},
        "toolsets": ["filesystem", "terminal", "git"],
        "policies": ["no_backend_model_disclosure", "sync_before_handoff"],
        "backend_visible": False,
    },
    {
        "id": "implementer",
        "title": "Bounded implementation",
        "description": "Execute a narrow task in the linked workspace and keep changes reviewable.",
        "skill": "autonomous-ai-agents/hades-coordination",
        "model_routing": {
            "local_model_profile": "hades.implementer",
            "selector": "cheapest_capable",
            "provider_source": "config.yaml",
        },
        "budget": {"max_turns": 12, "max_runtime_seconds": 1200},
        "toolsets": ["filesystem", "terminal", "git"],
        "policies": ["bounded_scope", "focused_tests_required", "no_backend_model_disclosure"],
        "backend_visible": False,
    },
    {
        "id": "reviewer",
        "title": "Review and verification",
        "description": "Review diffs, validate contracts, and decide whether the MVP gate is met.",
        "skill": "software-development/requesting-code-review",
        "model_routing": {
            "local_model_profile": "hades.reviewer",
            "selector": "strongest_allowed",
            "provider_source": "config.yaml",
        },
        "budget": {"max_turns": 6, "max_runtime_seconds": 900},
        "toolsets": ["filesystem", "terminal", "git"],
        "policies": ["findings_first", "verification_required", "no_backend_model_disclosure"],
        "backend_visible": False,
    },
    {
        "id": "sync-curator",
        "title": "Artifact sync curator",
        "description": "Prepare read-only git tree and symbol artifacts for backend ingestion.",
        "skill": "autonomous-ai-agents/hades-coordination",
        "model_routing": {
            "local_model_profile": "hades.sync",
            "selector": "fast_local_preferred",
            "provider_source": "config.yaml",
        },
        "budget": {"max_turns": 4, "max_runtime_seconds": 600},
        "toolsets": ["filesystem", "terminal"],
        "capabilities": ["sync_git_tree", "populate_backend_ast"],
        "policies": ["read_only_artifacts", "redact_secrets", "no_backend_model_disclosure"],
        "backend_visible": False,
    },
    {
        "id": "memory-steward",
        "title": "Shared memory steward",
        "description": "Draft and review project-scoped memory proposals without publishing personal memory.",
        "skill": "autonomous-ai-agents/hades-coordination",
        "model_routing": {
            "local_model_profile": "hades.memory",
            "selector": "balanced_allowed",
            "provider_source": "config.yaml",
        },
        "budget": {"max_turns": 5, "max_runtime_seconds": 600},
        "toolsets": ["filesystem"],
        "policies": ["project_memory_only", "proposal_review_required", "no_backend_model_disclosure"],
        "backend_visible": False,
    },
)


def hades_coordination_profiles() -> list[dict[str, Any]]:
    """Return copy-safe local Hades coordination profile definitions."""

    return deepcopy(list(_PROFILE_DEFINITIONS))


def hades_coordination_profile(profile_id: str) -> dict[str, Any] | None:
    """Return a copy of one curated profile by id."""

    for profile in _PROFILE_DEFINITIONS:
        if profile["id"] == profile_id:
            return deepcopy(profile)
    return None


class HadesCoordination:
    """Non-blocking heartbeat loop for Hades presence coordination on Python worker.

    Maintains presence on backend via periodic heartbeat. Failures are logged but
    don't block the runner. Thread-safe git state updates with lock protection.
    Graceful shutdown with configurable timeout.
    """

    def __init__(
        self,
        project_id: str,
        workspace_binding_id: str,
        agent_id: str,
        backend_client: HadesBackendClient,
        heartbeat_interval: float = 30.0,
        ttl_seconds: int = 300,
    ) -> None:
        """Initialize HadesCoordination instance.

        Args:
            project_id: Project identifier
            workspace_binding_id: Workspace binding identifier
            agent_id: Agent identifier
            backend_client: HadesBackendClient instance
            heartbeat_interval: Seconds between heartbeats (default 30)
            ttl_seconds: TTL for presence record on backend (default 300)
        """
        self.project_id = project_id
        self.workspace_binding_id = workspace_binding_id
        self.agent_id = agent_id
        self.backend_client = backend_client
        self.heartbeat_interval = heartbeat_interval
        self.ttl_seconds = ttl_seconds

        # Git state protected by lock
        self._git_state: dict[str, Any] = {}
        self._git_state_lock = threading.RLock()

        # Heartbeat thread management
        self._heartbeat_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self.delegation_authority = DelegationAuthority(
            root_id=agent_id, project_id=project_id
        )

    def register_delegated_agent(self, manifest: LeafManifest) -> None:
        """Register a child manifest for root observation and parent authority."""

        self.delegation_authority.register(actor_id=manifest.parent_id, manifest=manifest)

    def inspect_delegated_agent(self, actor_id: str, agent_id: str) -> LeafManifest:
        """Inspect role/scope without granting command authority."""

        return self.delegation_authority.inspect(actor_id, agent_id)

    def update_delegated_contract(
        self,
        actor_id: str,
        agent_id: str,
        *,
        expected_task_version: int,
        expected_contract_version: int,
        patch: dict[str, Any],
    ) -> LeafManifest:
        """Apply a contract patch only when ``actor_id`` is the direct parent."""

        return self.delegation_authority.update_contract(
            actor=actor_id,
            target=agent_id,
            expected_task_version=expected_task_version,
            expected_contract_version=expected_contract_version,
            patch=patch,
        )

    def set_git_state(
        self,
        current_branch: str | None = None,
        last_head_sha: str | None = None,
        dirty_status: bool | None = None,
    ) -> None:
        """Update git state (thread-safe).

        Args:
            current_branch: Current git branch name
            last_head_sha: Current HEAD SHA
            dirty_status: Whether working directory is dirty
        """
        with self._git_state_lock:
            if current_branch is not None:
                self._git_state["current_branch"] = current_branch
            if last_head_sha is not None:
                self._git_state["last_head_sha"] = last_head_sha
            if dirty_status is not None:
                self._git_state["dirty_status"] = dirty_status

    def start_heartbeat_loop(self) -> None:
        """Start non-blocking background heartbeat loop.

        Idempotent: calling multiple times reuses existing thread.
        """
        if self._heartbeat_thread is not None and self._heartbeat_thread.is_alive():
            return

        self._stop_event.clear()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop_worker,
            daemon=False,
        )
        self._heartbeat_thread.start()

    def stop_heartbeat_loop(self, timeout: float = 5.0) -> None:
        """Stop heartbeat loop gracefully.

        Args:
            timeout: Maximum seconds to wait for thread termination
        """
        if self._heartbeat_thread is None:
            return

        self._stop_event.set()
        self._heartbeat_thread.join(timeout=timeout)

        if self._heartbeat_thread.is_alive():
            logger.warning(
                "Heartbeat thread did not terminate within %.1f seconds",
                timeout
            )
        else:
            self._heartbeat_thread = None

    def _heartbeat_loop_worker(self) -> None:
        """Background worker that sends periodic presence heartbeats.

        Errors are caught and logged. The loop continues despite failures,
        ensuring the runner is never blocked.
        """
        while not self._stop_event.is_set():
            try:
                with self._git_state_lock:
                    git_state = self._git_state.copy()

                # Ensure minimal state
                if not git_state:
                    self._stop_event.wait(timeout=self.heartbeat_interval)
                    continue

                # Send heartbeat to backend
                payload = {
                    "project_id": self.project_id,
                    "workspace_binding_id": self.workspace_binding_id,
                    "agent_id": self.agent_id,
                    "current_branch": git_state.get("current_branch", "unknown"),
                    "last_head_sha": git_state.get("last_head_sha", "unknown"),
                    "dirty_status": git_state.get("dirty_status", False),
                    "ttl_seconds": self.ttl_seconds,
                }

                self.backend_client.presence_heartbeat(**payload)

            except HadesBackendError as exc:
                logger.error(
                    "Heartbeat failed (backend error): %s",
                    str(exc),
                )
            except Exception as exc:  # pylint: disable=broad-except
                logger.error(
                    "Heartbeat failed (unexpected): %s",
                    str(exc),
                )

            # Wait for next heartbeat or stop signal
            self._stop_event.wait(timeout=self.heartbeat_interval)

    def claim_and_run(
        self,
        runner: Callable[[], T],
        refs: list[dict[str, str]],
        scope: str = "edit",
    ) -> dict[str, Any]:
        """Claim code, run a task, and release the claim.

        Automatically creates a code claim before running the task and releases it
        in a finally block to ensure release even if the runner raises an exception.

        This is the core coordination primitive for multi-agent conflict avoidance.
        The backend enforces exclusivity rules based on scope and path/symbol overlap.

        Args:
            runner: Callable that performs the work. Return value is captured.
            refs: List of refs to claim, e.g. [{"type": "path", "value": "app/Foo.php"}]
            scope: Claim scope ('read'|'edit'|'refactor'|'verify', default 'edit').
                  'read' scope never conflicts. 'edit'/'refactor' conflict with each other
                  and higher scopes on same path/symbol. 'verify' is for validation.

        Returns:
            dict with keys:
            - "success": bool, True if runner completed, False if exception
            - "runner_result": the return value from runner (if success=True)
            - "claim": full claim response dict from backend
            - "conflicts": list of conflict dicts from claim response, if any

        Raises:
            HadesBackendError: if claim creation fails (release not attempted)
            Any exception raised by runner is re-raised after release occurs.

        Example:
            >>> result = coordination.claim_and_run(
            ...     runner=lambda: some_work(),
            ...     refs=[{"type": "path", "value": "app/Models/User.php"}],
            ...     scope="edit"
            ... )
            >>> if result["conflicts"]:
            ...     logger.warning(f"Soft-lock conflict: {result['conflicts']}")
            >>> if result["success"]:
            ...     print(f"Work completed: {result['runner_result']}")
        """
        claim_response: dict[str, Any] | None = None
        claim_id: str | None = None

        try:
            # Create claim on backend
            claim_response = self.backend_client.code_claim_create(
                project_id=self.project_id,
                workspace_binding_id=self.workspace_binding_id,
                agent_id=self.agent_id,
                refs=refs,
                scope=scope,
            )
            claim_id = claim_response.get("id")

            # Run the task
            runner_result = runner()

            return {
                "success": True,
                "runner_result": runner_result,
                "claim": claim_response,
                "conflicts": claim_response.get("conflicts", []),
            }
        finally:
            # CRITICAL: Release claim in finally to ensure it always happens,
            # even if runner raised an exception. This is essential for correctness
            # so we don't leave stale soft-locks on the backend.
            if claim_id:
                try:
                    self.backend_client.code_claim_release(claim_id)
                except HadesBackendError as exc:
                    # Log release failure but don't suppress original exception
                    logger.error(
                        "Failed to release claim %s: %s",
                        claim_id,
                        str(exc),
                    )


# Deterministic OrgRun snapshots and typed notices.
from dataclasses import dataclass as _dataclass
from typing import Iterable as _Iterable
from hermes_cli import kanban_db as _kb
from hermes_cli.kanban_portfolio import OrgRunCreated as _OrgRunCreated
from hermes_cli.kanban_swarm import post_blackboard_update as _post_blackboard_update

COORDINATION_EVENT_TYPES = {"fyi", "handoff", "blocker", "decision_proposal", "decision_resolution", "interface_change", "review_request", "integration_notice"}

@_dataclass(frozen=True)
class OrgRunSnapshot:
    org_run_id: str
    phase: str
    complete: bool
    blocked: bool
    execution: dict[str, str]
    reviews: dict[str, str]
    integration_ready: dict[str, str]
    completion: dict[str, str]
    integration: str
    org_review: str
    synthesis: str
    dispatchable: tuple[str, ...]

def _org_status(conn, task_id: str) -> str:
    task = _kb.get_task(conn, task_id)
    return task.status if task is not None else "missing"

def snapshot_org_run(conn, org_run_id: str, topology: _OrgRunCreated) -> OrgRunSnapshot:
    execution = {key: _org_status(conn, item.execution_id) for key, item in topology.remote_tasks.items()}
    reviews = {key: _org_status(conn, item.review_id) for key, item in topology.remote_tasks.items()}
    integration_ready = {key: _org_status(conn, item.integration_ready_id) for key, item in topology.remote_tasks.items()}
    completion = {key: _org_status(conn, item.completion_id) for key, item in topology.remote_tasks.items()}
    integration = _org_status(conn, topology.integration_id)
    org_review = _org_status(conn, topology.review_id)
    synthesis = _org_status(conn, topology.synthesis_id)
    statuses = [*execution.values(), *reviews.values(), *integration_ready.values(), *completion.values(), integration, org_review, synthesis]
    blocked = any(value == "blocked" for value in statuses)
    complete = synthesis == "done"
    if complete: phase = "complete"
    elif blocked: phase = "blocked"
    elif any(value != "done" for value in execution.values()): phase = "execution"
    elif any(value != "done" for value in [*reviews.values(), *integration_ready.values()]): phase = "review"
    elif integration != "done": phase = "integration"
    elif org_review != "done": phase = "org_review"
    elif any(value != "done" for value in completion.values()): phase = "publish"
    else: phase = "synthesis"
    candidates = [topology.integration_id, topology.review_id, topology.synthesis_id, *[item.execution_id for item in topology.remote_tasks.values()]]
    dispatchable = tuple(task_id for task_id in candidates if _org_status(conn, task_id) == "ready")
    return OrgRunSnapshot(org_run_id, phase, complete, blocked, execution, reviews, integration_ready, completion, integration, org_review, synthesis, dispatchable)

def post_coordination_event(conn, *, anchor_id: str, event_type: str, summary: str, related_task_ids: _Iterable[str] = (), required_action: str | None = None, evidence_refs: _Iterable[str] = (), author: str = "org-coordinator") -> None:
    if event_type not in COORDINATION_EVENT_TYPES:
        raise ValueError(f"unknown coordination event type: {event_type!r}")
    clean = str(summary or "").strip()
    if not clean: raise ValueError("summary is required")
    if len(clean) > 1000: raise ValueError("summary exceeds 1000 characters")
    _post_blackboard_update(conn, anchor_id, author=author, key=f"coordination:{event_type}", value={"schema": "hades.coordination-event.v1", "type": event_type, "summary": clean, "related_task_ids": [str(value).strip() for value in related_task_ids if str(value).strip()], "required_action": str(required_action or "").strip() or None, "evidence_refs": [str(value).strip() for value in evidence_refs if str(value).strip()]})


def publish_org_run_completion(
    conn,
    *,
    client: object,
    org_run_id: str,
    topology: _OrgRunCreated,
    remote_task_id: str,
    message: str,
) -> tuple[bool, str]:
    """Publish one remote result only after the global integration gate.

    The per-item completion card must also be done.  This keeps a manual or
    future automated caller from bypassing the DAG's integration and review
    nodes when it has access to a backend client.
    """
    if _org_status(conn, topology.integration_id) != "done" or _org_status(conn, topology.review_id) != "done":
        return False, "integration gate is not complete"
    remote = topology.remote_tasks.get(remote_task_id)
    if remote is None:
        return False, "unknown remote task"
    if _org_status(conn, remote.completion_id) != "done":
        return False, "local completion evidence is not complete"
    from hermes_cli.hades_kanban_sync import publish_remote_result

    published = publish_remote_result(
        conn,
        client,
        remote.execution_id,
        success=True,
        message=message,
    )
    if not published:
        return False, "remote lease is unavailable or already consumed"
    post_coordination_event(
        conn,
        anchor_id=topology.anchor_id,
        event_type="integration_notice",
        summary=f"Published verified result for {remote_task_id}.",
        related_task_ids=[remote.execution_id, remote.completion_id],
        evidence_refs=[f"org_run:{org_run_id}"],
    )
    return True, "published"


def claim_org_run_remote_task(
    conn,
    *,
    client: object,
    topology: _OrgRunCreated,
    remote_task_id: str,
    local_workspace_id: str,
) -> tuple[bool, str]:
    """Acquire the remote lease for an OrgRun execution node."""
    remote = topology.remote_tasks.get(remote_task_id)
    if remote is None or not remote.work_item_id:
        return False, "remote work item mapping is missing"
    from hermes_cli.hades_kanban_sync import claim_remote_work_item

    return claim_remote_work_item(
        conn,
        client,
        task_id=remote.execution_id,
        work_item_id=remote.work_item_id,
        local_workspace_id=local_workspace_id,
    )


_ORG_PROPOSAL_TYPES = frozenset(
    {"clarification", "decision_proposal", "progress_summary", "completion_proposal"}
)


def publish_org_run_proposal(
    *,
    outbox_conn,
    topology: _OrgRunCreated,
    sender_agent_id: str,
    target_agent_id: str,
    remote_task_id: str,
    remote_task_version: str,
    proposal_type: str,
    summary: str,
    evidence_refs: _Iterable[str] = (),
    idempotency_key: str,
    target_workspace_binding_id: str | None = None,
    now: int | None = None,
    expected_project_id: str | None = None,
) -> str:
    """Append a bounded proposal to Persephone; never rewrite a PM card.

    ``idempotency_key`` deterministically identifies the envelope.  It is
    durably enqueued before the capability-gated sender performs network I/O;
    the stable message ID deduplicates retries across restart/offline recovery.
    """
    if not topology.project_id:
        raise ValueError("OrgRun topology has no authoritative project_id")
    if expected_project_id is not None and str(expected_project_id).strip() != topology.project_id:
        raise ValueError("proposal project does not match authoritative OrgRun project")
    values = {
        "project_id": topology.project_id, "sender_agent_id": sender_agent_id,
        "target_agent_id": target_agent_id, "remote_task_id": remote_task_id,
        "remote_task_version": remote_task_version, "idempotency_key": idempotency_key,
    }
    clean = {key: str(value or "").strip() for key, value in values.items()}
    missing = [key for key, value in clean.items() if not value]
    if missing:
        raise ValueError(f"required proposal fields are blank: {', '.join(missing)}")
    if proposal_type not in _ORG_PROPOSAL_TYPES:
        raise ValueError(f"unsupported proposal_type: {proposal_type}")
    summary = str(summary or "").strip()
    if not summary or len(summary) > 1000:
        raise ValueError("summary must contain 1..1000 characters")
    refs = [str(value).strip() for value in evidence_refs if str(value).strip()]
    if len(refs) > 16:
        raise ValueError("evidence_refs exceeds 16 items")
    message_id = str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"hades:{clean['project_id']}:{clean['idempotency_key']}",
    ))
    from hermes_cli.hades_persephone_messages import (
        AGENT_MESSAGE_SCHEMA, EffectClass, MessageType, parse_envelope,
    )
    timestamp = int(time.time()) if now is None else int(now)
    envelope = parse_envelope({
        "schema": AGENT_MESSAGE_SCHEMA,
        "message_id": message_id,
        "correlation_id": message_id,
        "causation_id": None,
        "project_id": clean["project_id"],
        "sender_agent_id": clean["sender_agent_id"],
        "target_agent_id": clean["target_agent_id"],
        "target_workspace_binding_id": str(target_workspace_binding_id or "").strip() or None,
        "message_type": MessageType.LOCAL_DECISION.value,
        "effect": EffectClass.INFORMATION_READ.value,
        "capability": "org_run_projection",
        "remote_task_id": clean["remote_task_id"],
        "remote_task_version": clean["remote_task_version"],
        "expires_at": timestamp + 86_400,
        "payload": {
            "schema": "hades.org-run-proposal.v1", "proposal_type": proposal_type,
            "summary": summary, "evidence_refs": refs,
            "requires_human_approval_for_remote_change": True,
        },
    }, now=timestamp)
    from hermes_cli.hades_persephone_store import enqueue_outbox
    enqueue_outbox(outbox_conn, envelope, now=timestamp)
    return message_id
