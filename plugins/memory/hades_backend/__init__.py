"""Hades backend shared-memory provider."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict, List

from agent.memory_provider import MemoryProvider
from hermes_cli import hades_backend_db as db
from hermes_cli.hades_backend_runtime import current_agent
from hermes_cli.hades_backend_sync import run_backend_sync


PIGGYBACK_SYNC_INTERVAL_SECONDS = 60
CREATE_ACTIONS = {"add", "create"}
UPDATE_ACTIONS = {"replace", "update"}
DELETE_ACTIONS = {"remove", "delete"}


class HadesBackendMemoryProvider(MemoryProvider):
    def __init__(self) -> None:
        self._binding: db.WorkspaceBinding | None = None
        self._last_sync_at: float | None = None

    @property
    def name(self) -> str:
        return "hades_backend"

    def is_available(self) -> bool:
        agent = current_agent()
        return bool(agent and agent.capabilities.get("memory", True))

    def initialize(self, session_id: str, **kwargs) -> None:
        self._binding = self._resolve_binding(Path(os.getcwd()))

    def system_prompt_block(self) -> str:
        if self._binding is None:
            return ""
        return (
            "Shared Hades backend memory is enabled for this linked project. "
            "Use recalled project memory as background context; Laravel remains "
            "the authoritative source of shared memory."
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if self._binding is None:
            return ""
        with db.connect_closing() as conn:
            cache = db.get_memory_cache(conn, self._binding.backend_workspace_binding_id)
        if cache is None or not cache.items:
            return ""
        lines = ["Shared Hades project memory:"]
        for item in cache.items[:12]:
            summary = str(item.get("summary") or "").strip()
            if summary:
                lines.append(f"- {summary}")
        return "\n".join(lines) if len(lines) > 1 else ""

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: List[Dict[str, Any]] | None = None,
    ) -> None:
        if self._binding is None:
            return None
        now = time.time()
        if (
            self._last_sync_at is not None
            and now - self._last_sync_at < PIGGYBACK_SYNC_INTERVAL_SECONDS
        ):
            return None
        self._last_sync_at = now
        run_backend_sync(quiet=True)
        return None

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return []

    def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
        metadata: Dict[str, Any] | None = None,
    ) -> None:
        if self._binding is None:
            return
        proposal_action = _proposal_action(action)
        if proposal_action is None:
            return
        metadata = dict(metadata or {})
        previous_summary = str(metadata.get("old_text") or "").strip()
        summary = str(content or "").strip() or previous_summary
        if not summary:
            return
        provenance = _proposal_provenance(
            provider=self.name,
            target=target,
            metadata=metadata,
            action=action,
            proposal_action=proposal_action,
            previous_summary=previous_summary,
        )
        with db.connect_closing() as conn:
            db.create_memory_proposal(
                conn,
                project_id=self._binding.project_id,
                workspace_binding_id=self._binding.backend_workspace_binding_id,
                action=proposal_action,
                intent="memory_write",
                summary=summary,
                provenance=provenance,
            )

    def _resolve_binding(self, cwd: Path) -> db.WorkspaceBinding | None:
        try:
            resolved = cwd.resolve()
        except OSError:
            return None
        best: db.WorkspaceBinding | None = None
        with db.connect_closing() as conn:
            for binding in db.list_workspace_bindings(conn, status="linked"):
                root = Path(binding.repo_root)
                try:
                    resolved.relative_to(root)
                except ValueError:
                    continue
                if best is None or len(str(root)) > len(best.repo_root):
                    best = binding
        return best


def _proposal_action(action: str) -> str | None:
    normalized = str(action or "").strip().lower()
    if normalized in CREATE_ACTIONS:
        return "create"
    if normalized in UPDATE_ACTIONS:
        return "update"
    if normalized in DELETE_ACTIONS:
        return "delete"
    return None


def _first_metadata_value(metadata: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = metadata.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _proposal_provenance(
    *,
    provider: str,
    target: str,
    metadata: dict[str, Any],
    action: str,
    proposal_action: str,
    previous_summary: str,
) -> dict[str, Any]:
    provenance: dict[str, Any] = {
        "target": target,
        "metadata": metadata,
        "provider": provider,
        "local_action": action,
        "proposal_action": proposal_action,
    }
    memory_id = _first_metadata_value(metadata, ("memory_id", "local_memory_id", "id"))
    base_version = _first_metadata_value(metadata, ("base_version", "etag", "memory_etag", "version"))
    if memory_id:
        provenance["memory_id"] = memory_id
    if base_version:
        provenance["base_version"] = base_version
    if previous_summary:
        provenance["previous_summary"] = previous_summary
    return provenance


def register(ctx) -> None:
    ctx.register_memory_provider(HadesBackendMemoryProvider())
