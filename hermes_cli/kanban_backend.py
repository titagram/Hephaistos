"""Optional Hades backend context for Kanban workspaces."""

from __future__ import annotations

from dataclasses import dataclass
import inspect
from pathlib import Path
import secrets
import sqlite3
import time
from typing import Callable, Literal

from hermes_cli import hades_backend_db as hdb
from hermes_cli import hades_backend_runtime
from hermes_cli import kanban_db as kb
from hermes_cli.hades_backend_client import HadesBackendError, redact_secret
from hermes_cli.hades_backend_sync import matching_workspace_binding_ids

try:
    import httpx
except ImportError:  # pragma: no cover - the backend client requires httpx.
    httpx = None  # type: ignore[assignment]


def _sync_error_text(exc: BaseException) -> str:
    """Return one bounded, secret-safe sync error for local state and CLI output."""
    return redact_secret(str(exc))[:500]


def _is_transport_failure(exc: BaseException) -> bool:
    """Recognize the network failures wrapped by the backend HTTP client."""
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (ConnectionError, TimeoutError, OSError)):
            return True
        if httpx is not None and isinstance(current, httpx.TransportError):
            return True
        current = current.__cause__ or current.__context__
    return False


def _http_status_from_chain(exc: BaseException) -> int | None:
    """Read an explicit httpx status without confusing it for transport."""
    if httpx is None:
        return None
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, httpx.HTTPStatusError):
            status = current.response.status_code
            return status if isinstance(status, int) else None
        current = current.__cause__ or current.__context__
    return None


def _state_for_http_status(status: int) -> str:
    """Only an unavailable backend service is an offline sync condition."""
    return "backend_offline" if status == 408 or status >= 500 else "sync_error"


def _sync_failure_state(exc: BaseException) -> str:
    """Classify backend errors without hiding identity/configuration failures.

    The HTTP client wraps both transport errors and HTTP responses in
    ``HadesBackendError``. Only an unreachable transport or an unavailable
    service is an offline condition. Auth, identity, validation, and malformed
    backend responses must remain a nonzero ``sync_error`` for operators.
    """
    if isinstance(exc, HadesBackendError):
        status = exc.status_code
        if status is not None:
            return _state_for_http_status(status)
    status = _http_status_from_chain(exc)
    if status is not None:
        return _state_for_http_status(status)
    return "backend_offline" if _is_transport_failure(exc) else "sync_error"


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
    backend_available: bool = True


@dataclass(frozen=True)
class KanbanSyncReport:
    """Binding-scoped outcome for one safe local Kanban sync attempt."""

    state: str
    workspace_binding_id: str | None = None
    pulled: int = 0
    created: int = 0
    existing: int = 0
    delivered: int = 0
    deferred: int = 0
    failed: int = 0
    outbox_pending: int = 0
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
    if client_factory is not None:
        try:
            parameters = inspect.signature(factory).parameters.values()
            accepts_agent = any(
                parameter.kind
                in {
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    inspect.Parameter.VAR_POSITIONAL,
                }
                for parameter in parameters
            )
        except (TypeError, ValueError):
            accepts_agent = True
        if not accepts_agent:
            return factory()
    return factory(agent)


def resolve_kanban_backend_context_for_link(
    link: kb.RemoteLink,
    *,
    workspace_root: str | Path | None = None,
) -> KanbanBackendContext:
    """Resolve the exact stored binding without reclassifying it as local work."""
    root = Path(workspace_root or Path.cwd()).resolve()
    if not hdb.hades_backend_db_path().exists():
        return KanbanBackendContext(
            "linked",
            root,
            project_id=link.project_id,
            workspace_binding_id=link.workspace_binding_id,
            error="backend configuration is unavailable",
            backend_available=False,
        )
    with hdb.connect_closing() as conn:
        binding = hdb.get_binding_for_backend_id(conn, link.workspace_binding_id)
    if (
        binding is None
        or binding.project_id != link.project_id
        or binding.status != "linked"
    ):
        return KanbanBackendContext(
            "misconfigured",
            root,
            project_id=link.project_id,
            workspace_binding_id=link.workspace_binding_id,
            error="stored remote binding is missing or does not match",
            backend_available=False,
        )
    return KanbanBackendContext(
        "linked",
        root,
        project_id=binding.project_id,
        workspace_binding_id=binding.backend_workspace_binding_id,
        local_workspace_id=binding.local_project_id,
        agent_id=binding.agent_id,
    )


def _pending_outbox_count(conn, workspace_binding_id: str) -> int:
    return kb.count_remote_outbox(
        conn, workspace_binding_id=workspace_binding_id,
    )


def _record_sync_report(
    conn,
    report: KanbanSyncReport,
    *,
    now: int | None,
) -> None:
    if report.workspace_binding_id is None:
        return
    current = int(time.time() if now is None else now)
    previous = kb.get_kanban_sync_state(conn, report.workspace_binding_id)
    next_attempt_at = None
    if report.state in {"backend_offline", "sync_error"}:
        previous_failures = int((previous or {}).get("failure_count") or 0)
        next_attempt_at = current + min(3_600, 30 * (2 ** min(previous_failures, 7)))
    kb.record_kanban_sync_state(
        conn,
        workspace_binding_id=report.workspace_binding_id,
        state=report.state,
        summary={
            "pulled": report.pulled,
            "created": report.created,
            "existing": report.existing,
            "delivered": report.delivered,
            "deferred": report.deferred,
            "failed": report.failed,
            "outbox_pending": report.outbox_pending,
        },
        last_error=report.error,
        next_attempt_at=next_attempt_at,
        now=current,
    )


def read_kanban_sync_status(
    *,
    board: str | None = None,
    cwd: str | Path | None = None,
) -> KanbanSyncReport:
    """Read only local binding-scoped state and outbox depth."""
    context = resolve_kanban_backend_context(board=board, cwd=cwd)
    if context.mode == "local_only":
        return KanbanSyncReport(state="local_only")
    if context.mode != "linked" or not context.workspace_binding_id:
        return KanbanSyncReport(state="sync_error", error=context.error)
    with kb.connect(board=board) as conn:
        stored = kb.get_kanban_sync_state(conn, context.workspace_binding_id)
        summary = (stored or {}).get("summary") or {}
        return KanbanSyncReport(
            state=str((stored or {}).get("state") or "linked"),
            workspace_binding_id=context.workspace_binding_id,
            pulled=int(summary.get("pulled") or 0),
            created=int(summary.get("created") or 0),
            existing=int(summary.get("existing") or 0),
            delivered=int(summary.get("delivered") or 0),
            deferred=int(summary.get("deferred") or 0),
            failed=int(summary.get("failed") or 0),
            outbox_pending=_pending_outbox_count(
                conn, context.workspace_binding_id,
            ),
            error=(stored or {}).get("last_error"),
        )


def run_kanban_sync(
    *,
    board: str | None = None,
    cwd: str | Path | None = None,
    client_factory: Callable[[hdb.BackendAgent], object] | None = None,
    now: int | None = None,
) -> KanbanSyncReport:
    """Pull one bound workspace without turning local boards into backend clients."""
    context = resolve_kanban_backend_context(board=board, cwd=cwd)
    if context.mode == "local_only":
        return KanbanSyncReport(state="local_only")
    if context.mode != "linked":
        return KanbanSyncReport(state="sync_error", error=context.error)

    # Import legacy keys before constructing a network client: migration is
    # intentionally local and can safely run while the backend is unavailable.
    from hermes_cli.hades_kanban_sync import (
        drain_remote_outbox,
        migrate_legacy_remote_links,
        sync_remote_kanban,
    )

    with kb.connect(board=board) as conn:
        try:
            migrate_legacy_remote_links(conn, context)
            client = make_kanban_client(context, client_factory=client_factory)
            try:
                result = sync_remote_kanban(
                    conn, client, context=context, mode="pull_only",
                )
                delivered, deferred = drain_remote_outbox(
                    conn,
                    context=context,
                    borrowed_client=client,
                    now=now,
                )
            finally:
                close = getattr(client, "close", None)
                if callable(close):
                    close()
        except (ValueError, KeyError, sqlite3.IntegrityError) as exc:
            report = KanbanSyncReport(
                state="sync_error",
                workspace_binding_id=context.workspace_binding_id,
                outbox_pending=_pending_outbox_count(
                    conn, context.workspace_binding_id,
                ),
                error=_sync_error_text(exc),
            )
        except Exception as exc:
            report = KanbanSyncReport(
                state=_sync_failure_state(exc),
                workspace_binding_id=context.workspace_binding_id,
                outbox_pending=_pending_outbox_count(
                    conn, context.workspace_binding_id,
                ),
                error=_sync_error_text(exc),
            )
        else:
            report = KanbanSyncReport(
                state="synced" if result.status == "ok" else "sync_error",
                workspace_binding_id=context.workspace_binding_id,
                pulled=result.pulled,
                created=result.created,
                existing=result.existing,
                delivered=delivered,
                deferred=deferred,
                failed=result.failed,
                outbox_pending=_pending_outbox_count(
                    conn, context.workspace_binding_id,
                ),
                error=result.error,
            )
        _record_sync_report(conn, report, now=now)
        return report


def maybe_run_kanban_sync(
    *,
    board: str | None = None,
    cwd: str | Path | None = None,
    min_interval_seconds: int = 30,
    now: int | None = None,
    force: bool = False,
) -> KanbanSyncReport:
    """Run at most one best-effort sync per binding during the configured interval.

    Dispatcher surfaces call this helper before a tick.  Local-only boards do
    not open a backend client and are never throttled; remote work remains
    subject to its admission callback independently of this opportunistic
    pull/delivery attempt.
    """
    context = resolve_kanban_backend_context(board=board, cwd=cwd)
    if context.mode == "local_only":
        return KanbanSyncReport(state="local_only")
    if context.mode != "linked" or not context.workspace_binding_id:
        return KanbanSyncReport(state="sync_error", error=context.error)

    current = int(time.time() if now is None else now)
    interval = max(0, int(min_interval_seconds))
    owner = secrets.token_urlsafe(18)
    with kb.connect(board=board) as lock_conn:
        previous = kb.get_kanban_sync_state(
            lock_conn, context.workspace_binding_id,
        )
        if previous is not None and not force:
            next_attempt_at = previous.get("next_attempt_at")
            last_attempt_at = previous.get("last_attempt_at")
            if next_attempt_at is not None and current < int(next_attempt_at):
                summary = previous.get("summary") or {}
                return KanbanSyncReport(
                    state=str(previous.get("state") or "backend_offline"),
                    workspace_binding_id=context.workspace_binding_id,
                    pulled=int(summary.get("pulled") or 0),
                    created=int(summary.get("created") or 0),
                    existing=int(summary.get("existing") or 0),
                    delivered=int(summary.get("delivered") or 0),
                    deferred=int(summary.get("deferred") or 0),
                    failed=int(summary.get("failed") or 0),
                    outbox_pending=_pending_outbox_count(
                        lock_conn, context.workspace_binding_id,
                    ),
                    error=previous.get("last_error"),
                )
            if (
                last_attempt_at is not None
                and current - int(last_attempt_at) < interval
            ):
                return KanbanSyncReport(
                    state="sync_deferred",
                    workspace_binding_id=context.workspace_binding_id,
                    outbox_pending=_pending_outbox_count(
                        lock_conn, context.workspace_binding_id,
                    ),
                )
        if not kb.try_acquire_kanban_sync_lock(
            lock_conn,
            workspace_binding_id=context.workspace_binding_id,
            owner_token=owner,
            now=current,
        ):
            return KanbanSyncReport(
                state="sync_inflight",
                workspace_binding_id=context.workspace_binding_id,
                outbox_pending=_pending_outbox_count(
                    lock_conn, context.workspace_binding_id,
                ),
            )
    try:
        report = run_kanban_sync(board=board, cwd=cwd, now=current)
    except Exception as exc:
        report = KanbanSyncReport(
            state=_sync_failure_state(exc),
            workspace_binding_id=context.workspace_binding_id,
            error=_sync_error_text(exc),
        )
    finally:
        with kb.connect(board=board) as lock_conn:
            kb.release_kanban_sync_lock(
                lock_conn,
                workspace_binding_id=context.workspace_binding_id,
                owner_token=owner,
            )
    with kb.connect(board=board) as state_conn:
        stored = kb.get_kanban_sync_state(
            state_conn, context.workspace_binding_id,
        )
        if stored is None or int(stored.get("updated_at") or -1) != current:
            _record_sync_report(state_conn, report, now=current)
    return report


def dispatch_context_for_sync_report(
    report: KanbanSyncReport,
    *,
    board: str | None = None,
    cwd: str | Path | None = None,
) -> KanbanBackendContext:
    """Select an admission context without retrying a known failed sync.

    ``sync_deferred`` only follows a recently healthy bounded attempt, so it
    may resolve the linked context for lease admission.  A reported offline,
    validation, or unknown state is deliberately represented as local-only:
    local cards remain usable while the structured-link admission callback
    defers every remote card before constructing a client.
    """
    if report.state == "local_only":
        return KanbanBackendContext("local_only", Path(cwd or Path.cwd()).resolve())
    if report.workspace_binding_id or report.state in {
        "synced", "sync_deferred", "sync_inflight", "backend_offline", "sync_error",
    }:
        try:
            context = resolve_kanban_backend_context(board=board, cwd=cwd)
            if (
                context.mode == "linked"
                and (
                    report.workspace_binding_id is None
                    or report.workspace_binding_id == context.workspace_binding_id
                )
            ):
                if report.state in {"backend_offline", "sync_error", "sync_inflight"}:
                    return KanbanBackendContext(
                        context.mode,
                        context.workspace_root,
                        project_id=context.project_id,
                        workspace_binding_id=context.workspace_binding_id,
                        local_workspace_id=context.local_workspace_id,
                        agent_id=context.agent_id,
                        error=report.error,
                        backend_available=False,
                    )
                return context
        except Exception:
            pass
    return KanbanBackendContext("local_only", Path(cwd or Path.cwd()).resolve())
