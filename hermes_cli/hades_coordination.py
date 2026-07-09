"""Curated local-only Hades coordination profiles and heartbeat coordination."""

from __future__ import annotations

import logging
import threading
import time
from copy import deepcopy
from typing import Any

from hermes_cli.hades_backend_client import HadesBackendClient, HadesBackendError

logger = logging.getLogger(__name__)


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
