"""Optional synchronization between the local Kanban and Hades work items.

The default is deliberately ``off``.  ``pull_only`` imports remote work items
as local triage cards; ``mirror`` currently has the same safe import behavior
and is the extension point for claim/result publication once a remote lease is
available.  No remote lifecycle mutation is performed by the pull path.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
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


@dataclass(frozen=True)
class RemoteLease:
    work_item_id: str
    lease_token: str


LEASE_AUTHOR = "hades-backend-sync"
LEASE_PREFIX = "HADES_REMOTE_LEASE "


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


def claim_remote_for_local_task(
    conn,
    client: object,
    task,
    *,
    local_workspace_id: str,
) -> tuple[bool, str]:
    """Claim the remote counterpart immediately before a local spawn.

    The lease is persisted as a task comment so a later worker completion can
    publish exactly once without changing the Kanban schema.
    """
    key = str(getattr(task, "idempotency_key", "") or "")
    if not key.startswith("remote-kanban:"):
        return True, "local-only task"
    existing = _latest_lease(conn, task.id)
    if existing is not None and existing.lease_token != "consumed":
        return True, "remote lease already acquired"
    work_item_id = key.rsplit(":", 1)[-1].strip()
    if not work_item_id:
        return False, "remote work item id missing"
    return claim_remote_work_item(
        conn,
        client,
        task_id=task.id,
        work_item_id=work_item_id,
        local_workspace_id=local_workspace_id,
    )


def claim_remote_work_item(
    conn,
    client: object,
    *,
    task_id: str,
    work_item_id: str,
    local_workspace_id: str,
) -> tuple[bool, str]:
    """Acquire and persist a lease for an explicitly mapped local task."""
    existing = _latest_lease(conn, task_id)
    if existing is not None and existing.lease_token != "consumed":
        return True, "remote lease already acquired"
    try:
        response = client.claim_agent_work_item(
            work_item_id,
            local_workspace_id=local_workspace_id,
        )
        lease_token = str(response.get("lease_token") or "").strip()
        if not lease_token:
            return False, "remote claim returned no lease token"
    except Exception as exc:
        return False, f"remote claim deferred: {exc}"
    kb.add_comment(
        conn,
        task_id,
        author=LEASE_AUTHOR,
        body=LEASE_PREFIX + json.dumps(
            {"work_item_id": work_item_id, "lease_token": lease_token},
            sort_keys=True,
        ),
    )
    return True, "remote lease acquired"


def make_remote_admission(
    conn,
    client: object,
    *,
    local_workspace_id: str,
):
    """Build a dispatcher admission callback for ``dispatch_once``."""
    def admission(task):
        allowed, reason = claim_remote_for_local_task(
            conn,
            client,
            task,
            local_workspace_id=local_workspace_id,
        )
        return kb.DispatchAdmission(
            action="allow" if allowed else "defer",
            reason=reason,
        )

    return admission


def _latest_lease(conn, task_id: str) -> RemoteLease | None:
    rows = kb.list_comments(conn, task_id)
    for row in reversed(rows):
        body = str(row["body"] if isinstance(row, dict) else row.body)
        if not body.startswith(LEASE_PREFIX):
            continue
        try:
            raw = json.loads(body[len(LEASE_PREFIX):])
            lease = RemoteLease(str(raw["work_item_id"]), str(raw["lease_token"]))
            if lease.lease_token == "consumed":
                return None
            if lease.work_item_id and lease.lease_token:
                return lease
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return None


def heartbeat_remote_for_local_task(conn, client: object, task_id: str) -> bool:
    lease = _latest_lease(conn, task_id)
    if lease is None:
        return False
    client.heartbeat_agent_work_item(lease.work_item_id, lease_token=lease.lease_token)
    return True


def publish_remote_result(
    conn,
    client: object,
    task_id: str,
    *,
    success: bool,
    message: str,
) -> bool:
    """Publish a local terminal result and mark the lease as consumed."""
    lease = _latest_lease(conn, task_id)
    if lease is None:
        return False
    if success:
        client.complete_agent_work_item(
            lease.work_item_id,
            lease_token=lease.lease_token,
            chat_message=message,
        )
    else:
        client.fail_agent_work_item(
            lease.work_item_id,
            lease_token=lease.lease_token,
            message=message,
        )
    kb.add_comment(
        conn,
        task_id,
        author=LEASE_AUTHOR,
        body=LEASE_PREFIX + json.dumps(
            {"work_item_id": lease.work_item_id, "lease_token": "consumed"},
            sort_keys=True,
        ),
    )
    return True
