"""Optional synchronization between the local Kanban and Hades work items.

The default is deliberately ``off``.  ``pull_only`` imports remote work items
as local triage cards; ``mirror`` currently has the same safe import behavior
and is the extension point for claim/result publication once a remote lease is
available.  No remote lifecycle mutation is performed by the pull path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hermes_cli import kanban_db as kb

SYNC_MODES = {"off", "pull_only", "mirror"}


@dataclass(frozen=True)
class KanbanSyncResult:
    mode: str
    pulled: int = 0
    created: int = 0
    existing: int = 0
    skipped: int = 0


def _items(response: Any) -> list[dict[str, Any]]:
    raw = response.get("items", []) if isinstance(response, dict) else response
    return [item for item in (raw or []) if isinstance(item, dict)]


def _remote_id(item: dict[str, Any]) -> str:
    return str(item.get("id") or item.get("work_item_id") or item.get("remote_task_id") or "").strip()


def _payload(item: dict[str, Any]) -> dict[str, Any]:
    value = item.get("payload")
    return value if isinstance(value, dict) else item


def sync_remote_kanban(
    conn,
    client: object,
    *,
    project_id: str,
    agent_key: str = "local_agent",
    mode: str = "off",
    limit: int = 100,
) -> KanbanSyncResult:
    """Pull remote work items into local Kanban without duplicating cards.

    This function is intentionally dependency-free and accepts an injected
    client, making it safe to call from a scheduler, CLI, or tests.  Remote
    cards are always imported as ``triage`` so a local operator can review
    them before dispatch.  ``off`` performs no network call.
    """
    if mode not in SYNC_MODES:
        raise ValueError(f"mode must be one of {sorted(SYNC_MODES)}")
    if mode == "off":
        return KanbanSyncResult(mode=mode)
    response = client.list_agent_work_items(
        project_id=project_id,
        agent_key=agent_key,
        status="queued",
        limit=max(1, int(limit)),
    )
    created = existing = skipped = 0
    for item in _items(response):
        remote_id = _remote_id(item)
        if not remote_id:
            skipped += 1
            continue
        payload = _payload(item)
        key = f"remote-kanban:{project_id}:{remote_id}"
        row = conn.execute(
            "SELECT id FROM tasks WHERE idempotency_key = ? AND status != 'archived' LIMIT 1",
            (key,),
        ).fetchone()
        if row is not None:
            existing += 1
            continue
        title = str(payload.get("title") or payload.get("name") or f"Remote work item {remote_id}").strip()
        body = str(payload.get("body") or payload.get("description") or "").strip() or None
        priority = payload.get("priority", 0)
        try:
            priority = int(priority)
        except (TypeError, ValueError):
            priority = 0
        kb.create_task(
            conn,
            title=title,
            body=body,
            assignee=payload.get("assignee") or "default",
            created_by="hades-backend-sync",
            priority=priority,
            triage=True,
            idempotency_key=key,
            project_id=project_id,
        )
        created += 1
    return KanbanSyncResult(
        mode=mode,
        pulled=created + existing + skipped,
        created=created,
        existing=existing,
        skipped=skipped,
    )
