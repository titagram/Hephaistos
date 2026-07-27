"""Optional synchronization between the local Kanban and Hades work items.

The default is deliberately ``off``.  ``pull_only`` imports remote work items
as local triage cards; ``mirror`` currently has the same safe import behavior
and is the extension point for claim/result publication once a remote lease is
available.  No remote lifecycle mutation is performed by the pull path.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3
from typing import Any

from hermes_cli import kanban_db as kb
from hermes_cli.kanban_backend import KanbanBackendContext

SYNC_MODES = {"off", "pull_only", "mirror"}


@dataclass(frozen=True)
class KanbanSyncResult:
    mode: str
    status: str = "ok"
    pulled: int = 0
    created: int = 0
    existing: int = 0
    skipped: int = 0
    failed: int = 0
    error: str | None = None


@dataclass(frozen=True)
class RemoteMandateSyncResult:
    """Operator-visible status for optional remote mandate observation."""

    mode: str
    project_id: str
    cursor: str | None = None
    observed: int = 0
    status: str = "disabled"
    error: str | None = None


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


def _find_remote_link_task_id(
    conn: sqlite3.Connection,
    *,
    context: KanbanBackendContext,
    remote_work_item_id: str,
) -> str | None:
    row = conn.execute(
        "SELECT task_id FROM kanban_remote_links WHERE project_id = ? "
        "AND workspace_binding_id = ? AND remote_work_item_id = ?",
        (context.project_id, context.workspace_binding_id, remote_work_item_id),
    ).fetchone()
    return str(row["task_id"]) if row is not None else None


def sync_remote_kanban(
    conn,
    client: object,
    *,
    context: KanbanBackendContext,
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
    if context.mode != "linked" or not context.project_id or not context.workspace_binding_id:
        raise ValueError("remote Kanban sync requires a linked workspace context")
    if mode == "off":
        return KanbanSyncResult(mode=mode)
    response = client.list_agent_work_items(
        project_id=context.project_id,
        agent_key=agent_key,
        status="queued",
        limit=max(1, int(limit)),
    )
    items = _items(response)
    for item in items:
        item_project = str(
            item.get("project_id") or _payload(item).get("project_id")
            or context.project_id
        ).strip()
        if item_project != context.project_id:
            return KanbanSyncResult(
                mode=mode,
                status="rejected_page",
                failed=len(items),
                error="backend page contains a missing or cross-project item",
            )

    created = existing = skipped = 0
    for item in items:
        remote_id = _remote_id(item)
        if not remote_id:
            skipped += 1
            continue
        payload = _payload(item)
        task_id = _find_remote_link_task_id(
            conn, context=context, remote_work_item_id=remote_id,
        )
        if task_id is not None:
            existing += 1
            continue
        key = f"remote-kanban:{context.project_id}:{remote_id}"
        legacy = conn.execute(
            "SELECT id FROM tasks WHERE idempotency_key = ? "
            "AND status != 'archived' LIMIT 1",
            (key,),
        ).fetchone()
        if legacy is not None:
            task_id = str(legacy["id"])
            existing += 1
        else:
            title = str(payload.get("title") or payload.get("name") or f"Remote work item {remote_id}").strip()
            body = str(payload.get("body") or payload.get("description") or "").strip() or None
            priority = payload.get("priority", 0)
            try:
                priority = int(priority)
            except (TypeError, ValueError):
                priority = 0
            task_id = kb.create_task(
                conn,
                title=title,
                body=body,
                assignee=payload.get("assignee") or "default",
                created_by="hades-backend-sync",
                priority=priority,
                triage=True,
                # Retain the legacy key for exactly one compatibility release.
                idempotency_key=key,
                project_id=context.project_id,
            )
            created += 1
        kb.upsert_remote_link(
            conn,
            task_id=task_id,
            project_id=context.project_id,
            workspace_binding_id=context.workspace_binding_id,
            remote_work_item_id=remote_id,
        )
    return KanbanSyncResult(
        mode=mode,
        pulled=created + existing + skipped,
        created=created,
        existing=existing,
        skipped=skipped,
    )


def _latest_legacy_lease(
    conn: sqlite3.Connection,
    task_id: str,
    remote_work_item_id: str,
) -> tuple[RemoteLease | None, str | None]:
    """Return the newest usable legacy lease, flagging conflicting history."""
    conflict = False
    newest: RemoteLease | None = None
    saw_matching_lease = False
    for row in reversed(kb.list_comments(conn, task_id)):
        if row.author != LEASE_AUTHOR or not row.body.startswith(LEASE_PREFIX):
            continue
        try:
            raw = json.loads(row.body[len(LEASE_PREFIX):])
            work_item_id = str(raw["work_item_id"]).strip()
            lease_token = str(raw["lease_token"]).strip()
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if not work_item_id or not lease_token:
            continue
        if work_item_id != remote_work_item_id:
            conflict = True
            continue
        if not saw_matching_lease:
            saw_matching_lease = True
            if lease_token != "consumed":
                newest = RemoteLease(work_item_id, lease_token)
    return newest, "legacy remote lease history is ambiguous" if conflict else None


def migrate_legacy_remote_links(
    conn: sqlite3.Connection,
    context: KanbanBackendContext,
) -> int:
    """Import legacy key/comment state into binding-scoped remote links.

    This migration is deliberately local-only: it takes a database connection,
    never receives a client, and makes no backend calls.
    """
    if context.mode != "linked" or not context.project_id or not context.workspace_binding_id:
        return 0
    migrated = 0
    for task in kb.list_tasks(conn, include_archived=True):
        key = str(task.idempotency_key or "")
        if not key.startswith("remote-kanban:") or kb.get_remote_link(conn, task.id):
            continue
        parts = key.split(":", 2)
        if len(parts) != 3:
            continue
        prefix, project_id, remote_id = parts
        if prefix != "remote-kanban" or project_id != context.project_id or not remote_id:
            continue
        lease, last_error = _latest_legacy_lease(conn, task.id, remote_id)
        kb.upsert_remote_link(
            conn,
            task_id=task.id,
            project_id=project_id,
            workspace_binding_id=context.workspace_binding_id,
            remote_work_item_id=remote_id,
            last_error=(last_error[:500] if last_error else None),
        )
        if lease is not None:
            kb.set_remote_lease(
                conn, task.id, lease_token=lease.lease_token, lease_status="acquired",
            )
        migrated += 1
    return migrated


def run_kanban_sync(**kwargs):
    """Compatibility façade for the binding-aware sync runner."""
    from hermes_cli.kanban_backend import run_kanban_sync as _run_kanban_sync
    return _run_kanban_sync(**kwargs)


def sync_remote_mandates(
    conn,
    client: object,
    *,
    topology,
    mode: str = "off",
    cursor: str | None = None,
    expected_project_id: str | None = None,
    limit: int = 100,
) -> RemoteMandateSyncResult:
    """Observe project-scoped remote mandates without mutating remote cards.

    The caller performs semantic reconciliation because it owns the OrgRun
    topology.  This bounded primitive supplies an explicit cursor and offline
    status; ``off`` is a true network-off switch.
    """
    if mode not in SYNC_MODES:
        raise ValueError(f"mode must be one of {sorted(SYNC_MODES)}")
    project_id = str(getattr(topology, "project_id", "") or "").strip()
    if not project_id:
        raise ValueError("OrgRun topology has no authoritative project_id")
    if expected_project_id is not None and str(expected_project_id).strip() != project_id:
        raise ValueError("sync project does not match authoritative OrgRun project")
    projection_anchor_id = str(getattr(topology, "anchor_id", "") or "").strip()
    if mode == "off":
        return RemoteMandateSyncResult(mode, project_id)
    if cursor is None and projection_anchor_id:
        from hermes_cli.kanban_swarm import latest_blackboard
        stored = latest_blackboard(conn, projection_anchor_id).get("remote_projection_sync")
        if isinstance(stored, dict) and stored.get("project_id") == project_id:
            cursor = str(stored.get("cursor") or "").strip() or None
    try:
        response = client.list_agent_work_items(
            project_id=project_id,
            status="queued",
            limit=max(1, min(int(limit), 100)),
            **({"cursor": cursor} if cursor else {}),
        )
    except Exception as exc:
        result = RemoteMandateSyncResult(
            mode, project_id, cursor, status="offline", error=str(exc)[:500]
        )
        if projection_anchor_id:
            _persist_projection_sync(conn, projection_anchor_id, result)
        return result
    next_cursor = None
    if isinstance(response, dict):
        next_cursor = str(response.get("next_cursor") or "").strip() or None
    items = _items(response)
    for item in items:
        item_project = str(item.get("project_id") or _payload(item).get("project_id") or "").strip()
        if item_project != project_id:
            result = RemoteMandateSyncResult(
                mode, project_id, cursor, observed=0, status="rejected_page",
                error="backend page contains a missing or cross-project item",
            )
            if projection_anchor_id:
                _persist_projection_sync(conn, projection_anchor_id, result)
            return result
    result = RemoteMandateSyncResult(
        mode, project_id, next_cursor, observed=len(items), status="observed"
    )
    if projection_anchor_id:
        _persist_projection_sync(conn, projection_anchor_id, result)
    return result


def _persist_projection_sync(conn, anchor_id: str, result: RemoteMandateSyncResult) -> None:
    from hermes_cli.kanban_swarm import post_blackboard_update
    post_blackboard_update(
        conn, anchor_id, author=LEASE_AUTHOR, key="remote_projection_sync",
        value={"schema": "hades.remote-projection-sync.v1", "mode": result.mode,
               "project_id": result.project_id, "cursor": result.cursor,
               "observed": result.observed, "status": result.status,
               "error": result.error},
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
