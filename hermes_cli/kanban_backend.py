"""Optional Hades backend context for Kanban workspaces."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from hermes_cli import hades_backend_db as hdb
from hermes_cli import hades_backend_runtime
from hermes_cli import kanban_db as kb
from hermes_cli.hades_backend_sync import matching_workspace_binding_ids


@dataclass(frozen=True)
class KanbanBackendContext:
    """The backend identity, if any, associated with a Kanban workspace."""

    mode: Literal["local_only", "linked", "misconfigured"]
    workspace_root: Path
    project_id: str | None = None
    workspace_binding_id: str | None = None
    local_workspace_id: str | None = None
    agent_id: str | None = None
    error: str | None = None


def resolve_kanban_backend_context(
    *, board: str | None = None, cwd: str | Path | None = None,
) -> KanbanBackendContext:
    """Resolve a workspace's optional backend binding without creating backend state."""
    metadata = kb.read_board_metadata(board)
    root = Path(metadata.get("default_workdir") or cwd or Path.cwd()).resolve()
    if not hdb.hades_backend_db_path().exists():
        return KanbanBackendContext("local_only", root)

    binding_ids = matching_workspace_binding_ids(cwd=root)
    if not binding_ids:
        return KanbanBackendContext("local_only", root)

    with hdb.connect_closing() as conn:
        binding = hdb.get_binding_for_backend_id(conn, binding_ids[0])
    if binding is None:
        return KanbanBackendContext(
            "misconfigured", root, error="selected backend binding is missing",
        )
    return KanbanBackendContext(
        "linked",
        root,
        project_id=binding.project_id,
        workspace_binding_id=binding.backend_workspace_binding_id,
        local_workspace_id=binding.local_project_id,
        agent_id=binding.agent_id,
    )


def make_kanban_client(
    context: KanbanBackendContext,
    *,
    client_factory: Callable[[hdb.BackendAgent], object] | None = None,
) -> object:
    """Create a backend client for the exact agent selected by ``context``."""
    if context.mode != "linked" or not context.agent_id:
        raise RuntimeError("Kanban workspace is not linked to a Hades backend agent")

    with hdb.connect_closing() as conn:
        agent = hdb.get_agent(conn, context.agent_id)
    if agent is None or agent.project_id != context.project_id:
        raise RuntimeError("selected backend agent is missing or does not match the workspace")

    factory = client_factory or hades_backend_runtime.client_for_agent
    return factory(agent)
