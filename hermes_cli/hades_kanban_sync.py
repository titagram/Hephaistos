"""Optional synchronization between the local Kanban and Hades work items.

The default is deliberately ``off``.  ``pull_only`` imports remote work items
as local triage cards; ``mirror`` currently has the same safe import behavior
and is the extension point for claim/result publication once a remote lease is
available.  No remote lifecycle mutation is performed by the pull path.
"""

from __future__ import annotations

from dataclasses import dataclass
import inspect
import json
import secrets
import sqlite3
import time
from typing import Any

from hermes_cli import kanban_db as kb
from hermes_cli.hades_backend_client import HadesBackendError, redact_secret
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
        workspace_binding_id=context.workspace_binding_id,
        agent_key=agent_key,
        status="queued",
        limit=max(1, int(limit)),
    )
    items = _items(response)
    for item in items:
        payload = _payload(item)
        item_projects = [
            str(value).strip()
            for value in (item.get("project_id"), payload.get("project_id"))
            if value is not None
        ]
        item_bindings = [
            str(value).strip()
            for value in (
                item.get("workspace_binding_id"),
                payload.get("workspace_binding_id"),
            )
            if value is not None
        ]
        if (
            any(project != context.project_id for project in item_projects)
            or not item_bindings
            or any(binding != context.workspace_binding_id for binding in item_bindings)
        ):
            return KanbanSyncResult(
                mode=mode,
                status="rejected_page",
                failed=len(items),
                error="backend page contains a missing or cross-project/cross-binding item",
            )

    created = existing = skipped = 0
    for item in items:
        remote_id = _remote_id(item)
        if not remote_id:
            skipped += 1
            continue
        payload = _payload(item)
        key = f"remote-kanban:{context.project_id}:{remote_id}"
        was_created = False
        # The identity lookup, task creation/reuse, and link insert are one
        # SQLite write transaction.  Competing processes serialize at BEGIN
        # IMMEDIATE and the loser rechecks the complete remote identity, so it
        # can never leave a duplicate unlinked task behind.
        with kb.write_txn(conn):
            task_id = _find_remote_link_task_id(
                conn, context=context, remote_work_item_id=remote_id,
            )
            if task_id is None:
                legacy = conn.execute(
                    "SELECT id FROM tasks WHERE idempotency_key = ? "
                    "AND status != 'archived' LIMIT 1",
                    (key,),
                ).fetchone()
                if legacy is not None:
                    task_id = str(legacy["id"])
                else:
                    title = str(
                        payload.get("title")
                        or payload.get("name")
                        or f"Remote work item {remote_id}"
                    ).strip()
                    body = str(
                        payload.get("body") or payload.get("description") or ""
                    ).strip() or None
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
                        idempotency_key=key,
                        project_id=context.project_id,
                    )
                    was_created = True
                kb.upsert_remote_link(
                    conn,
                    task_id=task_id,
                    project_id=context.project_id,
                    workspace_binding_id=context.workspace_binding_id,
                    remote_work_item_id=remote_id,
                )
        if was_created:
            created += 1
        else:
            existing += 1
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
        if len(parts) != 3 or not parts[1] or not parts[2]:
            existing = conn.execute(
                "SELECT 1 FROM task_events WHERE task_id=? "
                "AND kind='remote_link_migration_diagnostic' LIMIT 1",
                (task.id,),
            ).fetchone()
            if existing is None:
                with kb.write_txn(conn):
                    kb._append_event(
                        conn,
                        task.id,
                        "remote_link_migration_diagnostic",
                        {"reason": "malformed remote identity marker"},
                    )
            continue
        prefix, project_id, remote_id = parts
        if prefix != "remote-kanban" or project_id != context.project_id:
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
    """Compatibility claim helper backed by a persisted remote link."""
    link = kb.get_remote_link(conn, task.id)
    if link is None:
        return True, "local-only task"
    if link.lease_status == "acquired" and link.lease_token:
        return True, "remote lease already acquired"
    return claim_remote_work_item(
        conn,
        client,
        task_id=task.id,
        work_item_id=link.remote_work_item_id,
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
    link = kb.get_remote_link(conn, task_id)
    if link is None or link.remote_work_item_id != work_item_id:
        return False, "remote work item mapping is missing"
    if link.lease_status == "acquired" and link.lease_token:
        return True, "remote lease already acquired"
    if not local_workspace_id:
        return False, "remote local workspace identity is unavailable"
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
    kb.set_remote_lease(
        conn, task_id, lease_token=lease_token, lease_status="acquired",
    )
    return True, "remote lease acquired"


def make_remote_admission(
    conn,
    *,
    context: KanbanBackendContext,
    client_factory=None,
):
    """Build a dispatcher admission callback for ``dispatch_once``."""
    def admission(task, *, dry_run: bool = False):
        # This lookup intentionally comes before any context or client work:
        # local cards must remain dispatchable while the backend is absent.
        link = kb.get_remote_link(conn, task.id)
        if link is None:
            return kb.DispatchAdmission("allow", "local-only task")
        if context.mode == "linked":
            if (
                context.project_id != link.project_id
                or context.workspace_binding_id != link.workspace_binding_id
            ):
                kb.record_remote_link_state(
                    conn,
                    task.id,
                    sync_status="identity_mismatch",
                    error="active backend binding does not match stored remote identity",
                )
                return kb.DispatchAdmission("supersede", "remote_binding_mismatch")
        if link.lease_status == "acquired" and link.lease_token:
            return kb.DispatchAdmission("allow", "remote lease already acquired")
        if context.mode == "local_only":
            return kb.DispatchAdmission("defer", "remote_backend_unavailable")
        if context.mode != "linked" or not context.local_workspace_id:
            return kb.DispatchAdmission("supersede", "remote_binding_unavailable")
        if not context.backend_available:
            return kb.DispatchAdmission("defer", "remote_backend_unavailable")
        if dry_run:
            # A dry-run is observational: it must never claim a remote lease
            # merely to decide whether the card would currently be runnable.
            return kb.DispatchAdmission("defer", "remote_backend_unavailable")
        client = None
        try:
            client = _make_remote_client(context, client_factory)
            response = client.claim_agent_work_item(
                link.remote_work_item_id,
                local_workspace_id=context.local_workspace_id,
            )
            lease_token = str(response.get("lease_token") or "").strip()
            if not lease_token:
                kb.record_remote_link_state(
                    conn,
                    task.id,
                    sync_status="claim_malformed",
                    error="remote claim returned no lease token",
                )
                return kb.DispatchAdmission("supersede", "remote_claim_malformed")
            kb.set_remote_lease(
                conn,
                task.id,
                lease_token=lease_token,
                lease_status="acquired",
            )
            kb.record_remote_link_state(
                conn, task.id, sync_status="leased", error=None,
            )
        except Exception as exc:
            failure = _remote_failure_class(exc)
            kb.record_remote_link_state(
                conn,
                task.id,
                sync_status=failure,
                error=redact_secret(str(exc)),
            )
            if failure == "transport_unavailable":
                return kb.DispatchAdmission("defer", "remote_backend_unavailable")
            if failure == "authorization_rejected":
                return kb.DispatchAdmission(
                    "supersede", "remote_authorization_rejected",
                )
            if failure == "validation_rejected":
                return kb.DispatchAdmission(
                    "supersede", "remote_validation_rejected",
                )
            return kb.DispatchAdmission("supersede", "remote_identity_rejected")
        finally:
            _close_client(client)
        return kb.DispatchAdmission("allow", "remote lease acquired")

    return admission


def _latest_lease(conn, task_id: str) -> RemoteLease | None:
    link = kb.get_remote_link(conn, task_id)
    if link is None or link.lease_status != "acquired" or not link.lease_token:
        return None
    return RemoteLease(link.remote_work_item_id, link.lease_token)


def heartbeat_remote_for_local_task(conn, client: object, task_id: str) -> bool:
    lease = _latest_lease(conn, task_id)
    if lease is None:
        return False
    client.heartbeat_agent_work_item(lease.work_item_id, lease_token=lease.lease_token)
    return True


def _remote_failure_class(exc: BaseException) -> str:
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return "transport_unavailable"
    if isinstance(exc, HadesBackendError):
        status = exc.status_code
        if status is not None and (status == 408 or status == 429 or status >= 500):
            return "transport_unavailable"
        if status in {401, 403}:
            return "authorization_rejected"
        if status in {400, 422}:
            return "validation_rejected"
        return "identity_rejected"
    if isinstance(exc, (PermissionError, ValueError, TypeError, KeyError)):
        return "validation_rejected"
    return "identity_rejected"


def heartbeat_remote_for_local_task_context(
    conn,
    task_id: str,
    *,
    board: str | None = None,
) -> str:
    """Renew one running task's exact remote lease without weakening local liveness."""
    link = kb.get_remote_link(conn, task_id)
    if link is None:
        return "local_only"
    if link.lease_status != "acquired" or not link.lease_token:
        return "no_lease"
    from hermes_cli import kanban_backend

    context = kanban_backend.resolve_kanban_backend_context(board=board)
    if (
        context.mode != "linked"
        or context.project_id != link.project_id
        or context.workspace_binding_id != link.workspace_binding_id
    ):
        context = kanban_backend.resolve_kanban_backend_context_for_link(link)
    if context.mode != "linked" or not context.backend_available:
        kb.record_remote_link_state(
            conn,
            task_id,
            sync_status="transport_unavailable",
            error=context.error or "backend unavailable for remote heartbeat",
        )
        return "transport_unavailable"

    client = None
    try:
        client = kanban_backend.make_kanban_client(context)
        client.heartbeat_agent_work_item(
            link.remote_work_item_id,
            lease_token=link.lease_token,
        )
    except Exception as exc:
        failure = _remote_failure_class(exc)
        safe_error = redact_secret(str(exc))
        if failure == "transport_unavailable":
            kb.record_remote_link_state(
                conn, task_id, sync_status=failure, error=safe_error,
            )
            return failure
        kb.set_remote_lease(
            conn, task_id, lease_token=None, lease_status="expired",
        )
        kb.record_remote_link_state(
            conn, task_id, sync_status=failure, error=safe_error,
        )
        return "expired"
    finally:
        _close_client(client)
    kb.record_remote_link_state(
        conn, task_id, sync_status="leased", error=None,
    )
    return "renewed"


def publish_remote_result(
    conn,
    *,
    context: KanbanBackendContext,
    task_id: str,
    success: bool,
    message: str,
    client_factory=None,
) -> bool:
    """Durably queue a terminal result, then make one best-effort delivery."""
    link = kb.get_remote_link(conn, task_id)
    if link is None:
        return False
    operation = "complete" if success else "fail"
    entry = kb.enqueue_remote_result(
        conn,
        task_id=task_id,
        operation=operation,
        payload={"message": str(message)[:10_000]},
        idempotency_key=(
            f"{operation}:{link.project_id}:{link.workspace_binding_id}:"
            f"{link.remote_work_item_id}"
        ),
    )
    if entry.status != "pending":
        return False
    owner = secrets.token_urlsafe(18)
    claimed = kb.claim_remote_result(
        conn,
        entry.id,
        workspace_binding_id=link.workspace_binding_id,
        owner_token=owner,
        now=int(time.time()),
    )
    if claimed is None:
        return False
    return _deliver_remote_outbox_entry(
        conn,
        context=context,
        entry=claimed,
        client_factory=client_factory,
        now=int(time.time()),
    )


def drain_remote_outbox(
    conn,
    *,
    context: KanbanBackendContext,
    client_factory=None,
    borrowed_client=None,
    now: int | None = None,
    limit: int = 20,
) -> tuple[int, int]:
    """Deliver a bounded batch of locally durable terminal results."""
    current = int(time.time() if now is None else now)
    delivered = failed = 0
    if context.mode != "linked" or not context.workspace_binding_id:
        return (0, 0)
    owner = secrets.token_urlsafe(18)
    entries = kb.claim_due_remote_results(
        conn,
        workspace_binding_id=context.workspace_binding_id,
        owner_token=owner,
        now=current,
        limit=limit,
    )
    for entry in entries:
        if _deliver_remote_outbox_entry(
            conn,
            context=context,
            entry=entry,
            client_factory=client_factory,
            borrowed_client=borrowed_client,
            now=current,
        ):
            delivered += 1
        else:
            failed += 1
    return delivered, failed


def _make_remote_client(context: KanbanBackendContext, client_factory):
    if client_factory is not None:
        try:
            parameters = inspect.signature(client_factory).parameters.values()
            accepts_context = any(
                parameter.kind
                in {
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    inspect.Parameter.VAR_POSITIONAL,
                }
                for parameter in parameters
            )
        except (TypeError, ValueError):
            accepts_context = True
        return client_factory(context) if accepts_context else client_factory()
    from hermes_cli.kanban_backend import make_kanban_client
    return make_kanban_client(context)


def _close_client(client: object | None) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        close()


def _delivery_rejection_is_terminal(exc: Exception) -> bool:
    if isinstance(exc, (PermissionError, ValueError)):
        return True
    return isinstance(exc, HadesBackendError) and exc.status_code in {400, 401, 403, 422}


def _delivery_retry_at(entry, now: int) -> int:
    return now + min(3_600, 60 * (2 ** min(entry.attempts, 5)))


def _deliver_remote_outbox_entry(
    conn,
    *,
    context: KanbanBackendContext,
    entry,
    client_factory,
    borrowed_client=None,
    now: int,
) -> bool:
    """Attempt one outbox entry without allowing network failure to alter local state."""
    link = kb.get_remote_link(conn, entry.task_id)
    if (
        link is None
        or context.mode != "linked"
        or context.project_id != getattr(link, "project_id", None)
        or context.workspace_binding_id != getattr(link, "workspace_binding_id", None)
        or link.lease_status != "acquired"
        or not link.lease_token
    ):
        kb.mark_remote_result_retry(
            conn,
            entry.id,
            error="remote backend context or lease is unavailable",
            next_attempt_at=_delivery_retry_at(entry, now),
            dead_letter=True,
            claim_token=entry.claim_token,
        )
        return False
    client = None
    try:
        client = borrowed_client or _make_remote_client(context, client_factory)
        message = str(entry.payload.get("message") or "")[:10_000]
        if entry.operation == "complete":
            client.complete_agent_work_item(
                link.remote_work_item_id,
                lease_token=link.lease_token,
                chat_message=message,
            )
        else:
            client.fail_agent_work_item(
                link.remote_work_item_id,
                lease_token=link.lease_token,
                message=message,
            )
    except Exception as exc:
        kb.mark_remote_result_retry(
            conn,
            entry.id,
            error=redact_secret(str(exc)),
            next_attempt_at=_delivery_retry_at(entry, now),
            dead_letter=_delivery_rejection_is_terminal(exc),
            claim_token=entry.claim_token,
        )
        return False
    finally:
        if borrowed_client is None:
            _close_client(client)
    kb.mark_remote_result_sent(
        conn, entry.id, claim_token=entry.claim_token,
    )
    kb.set_remote_lease(
        conn, entry.task_id, lease_token=None, lease_status="consumed")
    return True


def deliver_remote_terminal_for_task(conn, task_id: str) -> bool:
    """Best-effort delivery for a result already queued by a core transition."""
    link = kb.get_remote_link(conn, task_id)
    if link is None:
        return False
    from hermes_cli import kanban_backend

    context = kanban_backend.resolve_kanban_backend_context_for_link(link)
    if context.mode != "linked" or not context.backend_available:
        return False
    row = conn.execute(
        f"SELECT {kb._REMOTE_OUTBOX_COLUMNS} FROM kanban_sync_outbox "
        "WHERE task_id=? AND status='pending' "
        "ORDER BY id DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    if row is None:
        return False
    entry = kb._remote_outbox_from_row(row)
    owner = secrets.token_urlsafe(18)
    claimed = kb.claim_remote_result(
        conn,
        entry.id,
        workspace_binding_id=link.workspace_binding_id,
        owner_token=owner,
        now=int(time.time()),
    )
    if claimed is None:
        return False
    return _deliver_remote_outbox_entry(
        conn,
        context=context,
        entry=claimed,
        client_factory=None,
        now=int(time.time()),
    )
