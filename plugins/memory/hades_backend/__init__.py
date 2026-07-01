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
        if action not in {"add", "create", "update"}:
            return
        summary = str(content or "").strip()
        if not summary:
            return
        with db.connect_closing() as conn:
            db.create_memory_proposal(
                conn,
                project_id=self._binding.project_id,
                workspace_binding_id=self._binding.backend_workspace_binding_id,
                action="create" if action in {"add", "create"} else "update",
                intent="memory_write",
                summary=summary,
                provenance={
                    "target": target,
                    "metadata": metadata or {},
                    "provider": self.name,
                },
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


def register(ctx) -> None:
    ctx.register_memory_provider(HadesBackendMemoryProvider())
