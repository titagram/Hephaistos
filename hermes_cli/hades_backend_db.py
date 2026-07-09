"""Profile-scoped SQLite state for Hades backend integration."""

from __future__ import annotations

import contextlib
import json
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from hermes_cli.sqlite_util import write_txn
from hermes_constants import get_hermes_home

TERMINAL_BACKEND_JOB_STATUSES = (
    "cancelled",
    "completed",
    "expired",
    "failed",
    "skipped",
    "unlinked",
)
TERMINAL_PLUGIN_WORK_ITEM_STATUSES = (
    "cancelled",
    "completed",
    "completed_with_incomplete_memory",
    "expired",
    "failed",
    "skipped",
)
REVIEWED_MEMORY_PROPOSAL_STATUSES = ("accepted", "acknowledged")


def hades_backend_db_path() -> Path:
    return get_hermes_home() / "hades_backend.db"


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS backend_agents (
    agent_id       TEXT PRIMARY KEY,
    project_id     TEXT NOT NULL,
    base_url       TEXT NOT NULL,
    label          TEXT NOT NULL,
    token_env_key  TEXT NOT NULL,
    capabilities   TEXT NOT NULL DEFAULT '{}',
    created_at     INTEGER NOT NULL,
    updated_at     INTEGER NOT NULL,
    last_seen_at   INTEGER
);

CREATE TABLE IF NOT EXISTS workspace_bindings (
    workspace_fingerprint       TEXT PRIMARY KEY,
    project_id                  TEXT NOT NULL,
    agent_id                    TEXT NOT NULL,
    local_project_id            TEXT NOT NULL,
    backend_workspace_binding_id TEXT NOT NULL,
    display_path                TEXT NOT NULL,
    repo_root                   TEXT NOT NULL,
    git_remote_display          TEXT,
    git_remote_hash             TEXT,
    head_commit                 TEXT,
    status                      TEXT NOT NULL,
    created_at                  INTEGER NOT NULL,
    updated_at                  INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_workspace_bindings_project
    ON workspace_bindings(project_id, local_project_id);

CREATE TABLE IF NOT EXISTS backend_jobs (
    job_id               TEXT PRIMARY KEY,
    project_id           TEXT NOT NULL,
    workspace_binding_id TEXT NOT NULL,
    capability           TEXT NOT NULL,
    payload              TEXT NOT NULL,
    status               TEXT NOT NULL,
    result               TEXT,
    created_at           INTEGER NOT NULL,
    updated_at           INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS plugin_work_items (
    work_item_id        TEXT PRIMARY KEY,
    project_id          TEXT NOT NULL,
    repository_id       TEXT,
    local_workspace_id  TEXT,
    agent_key           TEXT NOT NULL,
    kind                TEXT NOT NULL,
    status              TEXT NOT NULL,
    lease_token         TEXT,
    payload             TEXT NOT NULL,
    result              TEXT,
    created_at          INTEGER NOT NULL,
    updated_at          INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_plugin_work_items_status
    ON plugin_work_items(project_id, status);

CREATE TABLE IF NOT EXISTS memory_proposals (
    id                   TEXT PRIMARY KEY,
    project_id           TEXT NOT NULL,
    workspace_binding_id TEXT NOT NULL,
    action               TEXT NOT NULL,
    intent               TEXT NOT NULL,
    summary              TEXT NOT NULL,
    provenance           TEXT NOT NULL,
    status               TEXT NOT NULL,
    reason               TEXT,
    created_at           INTEGER NOT NULL,
    updated_at           INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_cache (
    workspace_binding_id TEXT PRIMARY KEY,
    project_id           TEXT NOT NULL,
    version              TEXT NOT NULL,
    items                TEXT NOT NULL,
    updated_at           INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS inbox_events (
    event_id     TEXT PRIMARY KEY,
    project_id   TEXT NOT NULL,
    event_type   TEXT NOT NULL,
    payload      TEXT NOT NULL,
    received_at  INTEGER NOT NULL,
    read_at      INTEGER
);

CREATE TABLE IF NOT EXISTS sync_state (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);
"""


_INITIALIZED_PATHS: set[str] = set()


def _now() -> int:
    return int(time.time())


def _json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, sort_keys=True, separators=(",", ":"))


def _json_loads(value: str | None) -> Any:
    if not value:
        return {}
    try:
        return json.loads(value)
    except ValueError:
        return {}


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path if db_path is not None else hades_backend_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    resolved = str(path.resolve())
    conn = sqlite3.connect(str(path))
    try:
        conn.row_factory = sqlite3.Row
        from hermes_state import apply_wal_with_fallback

        apply_wal_with_fallback(conn, db_label="hades_backend.db")
        if resolved not in _INITIALIZED_PATHS:
            conn.executescript(SCHEMA_SQL)
            _INITIALIZED_PATHS.add(resolved)
    except Exception:
        conn.close()
        raise
    return conn


@contextlib.contextmanager
def connect_closing(db_path: Path | None = None):
    conn = connect(db_path=db_path)
    try:
        yield conn
    finally:
        conn.close()


@dataclass(frozen=True)
class BackendAgent:
    agent_id: str
    project_id: str
    base_url: str
    label: str
    token_env_key: str
    capabilities: dict[str, Any]


@dataclass(frozen=True)
class WorkspaceBinding:
    workspace_fingerprint: str
    project_id: str
    agent_id: str
    local_project_id: str
    backend_workspace_binding_id: str
    display_path: str
    repo_root: str
    git_remote_display: str
    git_remote_hash: str
    head_commit: str
    status: str


@dataclass(frozen=True)
class BackendJob:
    job_id: str
    project_id: str
    workspace_binding_id: str
    capability: str
    payload: dict[str, Any]
    status: str
    result: dict[str, Any] | None


@dataclass(frozen=True)
class PluginWorkItem:
    work_item_id: str
    project_id: str
    repository_id: str
    local_workspace_id: str
    agent_key: str
    kind: str
    status: str
    lease_token: str
    payload: dict[str, Any]
    result: dict[str, Any] | None


@dataclass(frozen=True)
class MemoryProposal:
    id: str
    project_id: str
    workspace_binding_id: str
    action: str
    intent: str
    summary: str
    provenance: dict[str, Any]
    status: str
    reason: str | None


@dataclass(frozen=True)
class InboxEvent:
    event_id: str
    project_id: str
    event_type: str
    payload: dict[str, Any]
    received_at: int


@dataclass(frozen=True)
class MemoryCache:
    project_id: str
    workspace_binding_id: str
    version: str
    items: list[dict[str, Any]]
    updated_at: int


class WorkspaceBindingConflict(ValueError):
    def __init__(self, existing_project_id: str, new_project_id: str) -> None:
        super().__init__(
            f"workspace is already linked to backend project {existing_project_id}; "
            f"refusing link to {new_project_id}"
        )
        self.existing_project_id = existing_project_id
        self.new_project_id = new_project_id


def _agent_from_row(row: sqlite3.Row | None) -> BackendAgent | None:
    if row is None:
        return None
    return BackendAgent(
        agent_id=row["agent_id"],
        project_id=row["project_id"],
        base_url=row["base_url"],
        label=row["label"],
        token_env_key=row["token_env_key"],
        capabilities=_json_loads(row["capabilities"]),
    )


def _binding_from_row(row: sqlite3.Row | None) -> WorkspaceBinding | None:
    if row is None:
        return None
    return WorkspaceBinding(
        workspace_fingerprint=row["workspace_fingerprint"],
        project_id=row["project_id"],
        agent_id=row["agent_id"],
        local_project_id=row["local_project_id"],
        backend_workspace_binding_id=row["backend_workspace_binding_id"],
        display_path=row["display_path"],
        repo_root=row["repo_root"],
        git_remote_display=row["git_remote_display"] or "",
        git_remote_hash=row["git_remote_hash"] or "",
        head_commit=row["head_commit"] or "",
        status=row["status"],
    )


def _job_from_row(row: sqlite3.Row) -> BackendJob:
    return BackendJob(
        job_id=row["job_id"],
        project_id=row["project_id"],
        workspace_binding_id=row["workspace_binding_id"],
        capability=row["capability"],
        payload=_json_loads(row["payload"]),
        status=row["status"],
        result=_json_loads(row["result"]) if row["result"] else None,
    )


def _plugin_work_item_from_row(row: sqlite3.Row) -> PluginWorkItem:
    return PluginWorkItem(
        work_item_id=row["work_item_id"],
        project_id=row["project_id"],
        repository_id=row["repository_id"] or "",
        local_workspace_id=row["local_workspace_id"] or "",
        agent_key=row["agent_key"],
        kind=row["kind"],
        status=row["status"],
        lease_token=row["lease_token"] or "",
        payload=_json_loads(row["payload"]),
        result=_json_loads(row["result"]) if row["result"] else None,
    )


def _proposal_from_row(row: sqlite3.Row) -> MemoryProposal:
    return MemoryProposal(
        id=row["id"],
        project_id=row["project_id"],
        workspace_binding_id=row["workspace_binding_id"],
        action=row["action"],
        intent=row["intent"],
        summary=row["summary"],
        provenance=_json_loads(row["provenance"]),
        status=row["status"],
        reason=row["reason"],
    )


def _event_from_row(row: sqlite3.Row) -> InboxEvent:
    return InboxEvent(
        event_id=row["event_id"],
        project_id=row["project_id"],
        event_type=row["event_type"],
        payload=_json_loads(row["payload"]),
        received_at=row["received_at"],
    )


def save_agent(
    conn: sqlite3.Connection,
    *,
    agent_id: str,
    project_id: str,
    base_url: str,
    label: str,
    token_env_key: str,
    capabilities: dict[str, Any] | None = None,
) -> BackendAgent:
    now = _now()
    with write_txn(conn):
        conn.execute(
            "INSERT INTO backend_agents "
            "(agent_id, project_id, base_url, label, token_env_key, capabilities, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(agent_id) DO UPDATE SET "
            "project_id = excluded.project_id, base_url = excluded.base_url, "
            "label = excluded.label, token_env_key = excluded.token_env_key, "
            "capabilities = excluded.capabilities, updated_at = excluded.updated_at",
            (
                agent_id,
                project_id,
                base_url,
                label,
                token_env_key,
                _json_dumps(capabilities or {}),
                now,
                now,
            ),
        )
    loaded = get_agent(conn, agent_id)
    assert loaded is not None
    return loaded


def get_agent(conn: sqlite3.Connection, agent_id: str) -> BackendAgent | None:
    return _agent_from_row(
        conn.execute("SELECT * FROM backend_agents WHERE agent_id = ?", (agent_id,)).fetchone()
    )


def get_default_agent(conn: sqlite3.Connection) -> BackendAgent | None:
    return _agent_from_row(
        conn.execute("SELECT * FROM backend_agents ORDER BY updated_at DESC LIMIT 1").fetchone()
    )


def upsert_workspace_binding(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    agent_id: str,
    local_project_id: str,
    workspace_fingerprint: str,
    display_path: str,
    repo_root: str,
    git_remote_display: str,
    git_remote_hash: str,
    head_commit: str,
    backend_workspace_binding_id: str,
) -> WorkspaceBinding:
    existing = get_binding_for_fingerprint(conn, workspace_fingerprint)
    if existing and existing.project_id != project_id and existing.status == "linked":
        raise WorkspaceBindingConflict(existing.project_id, project_id)
    now = _now()
    with write_txn(conn):
        conn.execute(
            "INSERT INTO workspace_bindings "
            "(workspace_fingerprint, project_id, agent_id, local_project_id, backend_workspace_binding_id, "
            " display_path, repo_root, git_remote_display, git_remote_hash, head_commit, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'linked', ?, ?) "
            "ON CONFLICT(workspace_fingerprint) DO UPDATE SET "
            "project_id = excluded.project_id, agent_id = excluded.agent_id, "
            "local_project_id = excluded.local_project_id, "
            "backend_workspace_binding_id = excluded.backend_workspace_binding_id, "
            "display_path = excluded.display_path, repo_root = excluded.repo_root, "
            "git_remote_display = excluded.git_remote_display, git_remote_hash = excluded.git_remote_hash, "
            "head_commit = excluded.head_commit, status = 'linked', updated_at = excluded.updated_at",
            (
                workspace_fingerprint,
                project_id,
                agent_id,
                local_project_id,
                backend_workspace_binding_id,
                display_path,
                repo_root,
                git_remote_display,
                git_remote_hash,
                head_commit,
                now,
                now,
            ),
        )
    loaded = get_binding_for_fingerprint(conn, workspace_fingerprint)
    assert loaded is not None
    return loaded


def get_binding_for_fingerprint(conn: sqlite3.Connection, workspace_fingerprint: str) -> WorkspaceBinding | None:
    return _binding_from_row(
        conn.execute(
            "SELECT * FROM workspace_bindings WHERE workspace_fingerprint = ?",
            (workspace_fingerprint,),
        ).fetchone()
    )


def get_binding_for_backend_id(conn: sqlite3.Connection, workspace_binding_id: str) -> WorkspaceBinding | None:
    return _binding_from_row(
        conn.execute(
            "SELECT * FROM workspace_bindings WHERE backend_workspace_binding_id = ?",
            (workspace_binding_id,),
        ).fetchone()
    )


def mark_binding_unlinked(conn: sqlite3.Connection, workspace_fingerprint: str) -> None:
    with write_txn(conn):
        conn.execute(
            "UPDATE workspace_bindings SET status = 'unlinked', updated_at = ? WHERE workspace_fingerprint = ?",
            (_now(), workspace_fingerprint),
        )


def list_workspace_bindings(conn: sqlite3.Connection, *, status: str | None = None) -> list[WorkspaceBinding]:
    if status:
        rows = conn.execute(
            "SELECT * FROM workspace_bindings WHERE status = ? ORDER BY updated_at DESC",
            (status,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM workspace_bindings ORDER BY updated_at DESC").fetchall()
    return [b for row in rows if (b := _binding_from_row(row)) is not None]


def upsert_job(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    project_id: str,
    workspace_binding_id: str,
    capability: str,
    payload: dict[str, Any],
    status: str,
) -> BackendJob:
    now = _now()
    with write_txn(conn):
        conn.execute(
            "INSERT INTO backend_jobs "
            "(job_id, project_id, workspace_binding_id, capability, payload, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(job_id) DO UPDATE SET updated_at = excluded.updated_at",
            (job_id, project_id, workspace_binding_id, capability, _json_dumps(payload), status, now, now),
        )
    row = conn.execute("SELECT * FROM backend_jobs WHERE job_id = ?", (job_id,)).fetchone()
    return _job_from_row(row)


def get_job(conn: sqlite3.Connection, job_id: str) -> BackendJob | None:
    row = conn.execute("SELECT * FROM backend_jobs WHERE job_id = ?", (job_id,)).fetchone()
    return _job_from_row(row) if row is not None else None


def update_job_status(
    conn: sqlite3.Connection,
    job_id: str,
    status: str,
    *,
    result: dict[str, Any] | None = None,
) -> BackendJob | None:
    with write_txn(conn):
        conn.execute(
            "UPDATE backend_jobs SET status = ?, result = COALESCE(?, result), updated_at = ? WHERE job_id = ?",
            (status, _json_dumps(result) if result is not None else None, _now(), job_id),
        )
    return get_job(conn, job_id)


def list_jobs(conn: sqlite3.Connection, *, statuses: Iterable[str] | None = None) -> list[BackendJob]:
    if statuses:
        values = list(statuses)
        placeholders = ",".join("?" for _ in values)
        rows = conn.execute(
            f"SELECT * FROM backend_jobs WHERE status IN ({placeholders}) ORDER BY created_at ASC",
            values,
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM backend_jobs ORDER BY created_at ASC").fetchall()
    return [_job_from_row(row) for row in rows]


def upsert_plugin_work_item(
    conn: sqlite3.Connection,
    *,
    work_item_id: str,
    project_id: str,
    repository_id: str | None = None,
    local_workspace_id: str | None = None,
    agent_key: str,
    kind: str,
    status: str,
    payload: dict[str, Any],
    lease_token: str | None = None,
    result: dict[str, Any] | None = None,
) -> PluginWorkItem:
    now = _now()
    with write_txn(conn):
        conn.execute(
            "INSERT INTO plugin_work_items "
            "(work_item_id, project_id, repository_id, local_workspace_id, agent_key, kind, status, "
            " lease_token, payload, result, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(work_item_id) DO UPDATE SET "
            "project_id = excluded.project_id, repository_id = excluded.repository_id, "
            "local_workspace_id = COALESCE(excluded.local_workspace_id, plugin_work_items.local_workspace_id), "
            "agent_key = excluded.agent_key, kind = excluded.kind, status = excluded.status, "
            "lease_token = COALESCE(excluded.lease_token, plugin_work_items.lease_token), "
            "payload = excluded.payload, result = COALESCE(excluded.result, plugin_work_items.result), "
            "updated_at = excluded.updated_at",
            (
                work_item_id,
                project_id,
                repository_id,
                local_workspace_id,
                agent_key,
                kind,
                status,
                lease_token,
                _json_dumps(payload),
                _json_dumps(result) if result is not None else None,
                now,
                now,
            ),
        )
    loaded = get_plugin_work_item(conn, work_item_id)
    assert loaded is not None
    return loaded


def get_plugin_work_item(conn: sqlite3.Connection, work_item_id: str) -> PluginWorkItem | None:
    row = conn.execute(
        "SELECT * FROM plugin_work_items WHERE work_item_id = ?",
        (work_item_id,),
    ).fetchone()
    return _plugin_work_item_from_row(row) if row is not None else None


def list_plugin_work_items(conn: sqlite3.Connection, *, statuses: Iterable[str] | None = None) -> list[PluginWorkItem]:
    if statuses:
        values = list(statuses)
        placeholders = ",".join("?" for _ in values)
        rows = conn.execute(
            f"SELECT * FROM plugin_work_items WHERE status IN ({placeholders}) ORDER BY updated_at DESC",
            values,
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM plugin_work_items ORDER BY updated_at DESC").fetchall()
    return [_plugin_work_item_from_row(row) for row in rows]


def update_plugin_work_item_status(
    conn: sqlite3.Connection,
    work_item_id: str,
    status: str,
    *,
    lease_token: str | None = None,
    result: dict[str, Any] | None = None,
) -> PluginWorkItem | None:
    with write_txn(conn):
        conn.execute(
            "UPDATE plugin_work_items SET status = ?, "
            "lease_token = COALESCE(?, lease_token), result = COALESCE(?, result), updated_at = ? "
            "WHERE work_item_id = ?",
            (
                status,
                lease_token,
                _json_dumps(result) if result is not None else None,
                _now(),
                work_item_id,
            ),
        )
    return get_plugin_work_item(conn, work_item_id)


def count_plugin_work_items_by_status(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT status, COUNT(*) AS count FROM plugin_work_items GROUP BY status ORDER BY status"
    ).fetchall()
    return {str(row["status"]): int(row["count"]) for row in rows}


def expire_waiting_jobs(
    conn: sqlite3.Connection,
    *,
    now: int | None = None,
    project_id: str | None = None,
    workspace_binding_ids: Iterable[str] | None = None,
) -> list[BackendJob]:
    current = int(now if now is not None else _now())
    clean_project_id = str(project_id or "").strip()
    clean_binding_ids = {
        str(binding_id).strip()
        for binding_id in (workspace_binding_ids or [])
        if str(binding_id or "").strip()
    }
    expired: list[str] = []
    for job in list_jobs(conn, statuses=["waiting_confirmation"]):
        if clean_project_id and job.project_id != clean_project_id:
            continue
        if clean_binding_ids and job.workspace_binding_id not in clean_binding_ids:
            continue
        deadline = job.payload.get("deadline_at") or job.payload.get("deadline")
        try:
            deadline_value = int(deadline)
        except (TypeError, ValueError):
            continue
        if deadline_value <= current:
            expired.append(job.job_id)
    if not expired:
        return []
    with write_txn(conn):
        for job_id in expired:
            conn.execute(
                "UPDATE backend_jobs SET status = 'expired', updated_at = ? WHERE job_id = ?",
                (current, job_id),
            )
    return [job for job_id in expired if (job := get_job(conn, job_id)) is not None]


def create_memory_proposal(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    workspace_binding_id: str,
    action: str,
    intent: str,
    summary: str,
    provenance: dict[str, Any],
) -> MemoryProposal:
    pid = "mp_" + secrets.token_hex(8)
    now = _now()
    with write_txn(conn):
        conn.execute(
            "INSERT INTO memory_proposals "
            "(id, project_id, workspace_binding_id, action, intent, summary, provenance, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
            (pid, project_id, workspace_binding_id, action, intent, summary, _json_dumps(provenance), now, now),
        )
    return list_memory_proposals(conn, ids=[pid])[0]


def mark_memory_proposal_status(
    conn: sqlite3.Connection,
    proposal_id: str,
    status: str,
    reason: str | None = None,
) -> None:
    with write_txn(conn):
        conn.execute(
            "UPDATE memory_proposals SET status = ?, reason = ?, updated_at = ? WHERE id = ?",
            (status, reason, _now(), proposal_id),
        )


def list_memory_proposals_by_status(conn: sqlite3.Connection, statuses: Iterable[str]) -> list[MemoryProposal]:
    values = list(statuses)
    if not values:
        return []
    placeholders = ",".join("?" for _ in values)
    rows = conn.execute(
        f"SELECT * FROM memory_proposals WHERE status IN ({placeholders}) ORDER BY created_at ASC",
        values,
    ).fetchall()
    return [_proposal_from_row(row) for row in rows]


def list_memory_proposals(
    conn: sqlite3.Connection,
    *,
    ids: Iterable[str] | None = None,
) -> list[MemoryProposal]:
    if ids:
        values = list(ids)
        placeholders = ",".join("?" for _ in values)
        rows = conn.execute(
            f"SELECT * FROM memory_proposals WHERE id IN ({placeholders}) ORDER BY created_at ASC",
            values,
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM memory_proposals ORDER BY created_at ASC").fetchall()
    return [_proposal_from_row(row) for row in rows]


def count_jobs_by_status(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT status, COUNT(*) AS count FROM backend_jobs GROUP BY status ORDER BY status ASC"
    ).fetchall()
    return {str(row["status"]): int(row["count"]) for row in rows}


def count_memory_proposals_by_status(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT status, COUNT(*) AS count FROM memory_proposals GROUP BY status ORDER BY status ASC"
    ).fetchall()
    return {str(row["status"]): int(row["count"]) for row in rows}


def record_sync_state(conn: sqlite3.Connection, key: str, value: dict[str, Any]) -> None:
    with write_txn(conn):
        conn.execute(
            "INSERT INTO sync_state (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, _json_dumps(value), _now()),
        )


def clear_sync_state(conn: sqlite3.Connection, key: str) -> None:
    with write_txn(conn):
        conn.execute("DELETE FROM sync_state WHERE key = ?", (key,))


def get_sync_state(conn: sqlite3.Connection, key: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
    if row is None:
        return None
    value = _json_loads(row["value"])
    return value if isinstance(value, dict) else {}


def get_sync_state_updated_at(conn: sqlite3.Connection, key: str) -> int | None:
    row = conn.execute("SELECT updated_at FROM sync_state WHERE key = ?", (key,)).fetchone()
    if row is None:
        return None
    return int(row["updated_at"])


def cleanup_orphaned_memory_cache(
    conn: sqlite3.Connection,
    *,
    include_all: bool = False,
    retention_days: int = 90,
    now: int | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    current = int(now if now is not None else _now())
    rows = conn.execute(
        """
        SELECT mc.workspace_binding_id, mc.items, mc.updated_at
        FROM memory_cache mc
        LEFT JOIN workspace_bindings wb
          ON wb.backend_workspace_binding_id = mc.workspace_binding_id
         AND wb.status = 'linked'
        WHERE wb.backend_workspace_binding_id IS NULL
        ORDER BY mc.updated_at ASC
        """
    ).fetchall()
    candidates = len(rows)
    cutoff = current - (max(0, int(retention_days)) * 86400)
    remove_ids: list[str] = []
    total_bytes = 0
    for row in rows:
        if include_all or retention_days == 0 or int(row["updated_at"]) <= cutoff:
            remove_ids.append(str(row["workspace_binding_id"]))
            total_bytes += len(str(row["items"]).encode("utf-8"))
    if remove_ids and not dry_run:
        with write_txn(conn):
            for workspace_binding_id in remove_ids:
                conn.execute(
                    "DELETE FROM memory_cache WHERE workspace_binding_id = ?",
                    (workspace_binding_id,),
                )
    removed = 0 if dry_run else len(remove_ids)
    return {
        "candidates": candidates,
        "would_remove": len(remove_ids),
        "removed": removed,
        "bytes": total_bytes,
    }


def cleanup_terminal_backend_jobs(
    conn: sqlite3.Connection,
    *,
    include_all: bool = False,
    retention_days: int = 30,
    now: int | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    current = int(now if now is not None else _now())
    cutoff = current - (max(0, int(retention_days)) * 86400)
    placeholders = ",".join("?" for _ in TERMINAL_BACKEND_JOB_STATUSES)
    rows = conn.execute(
        f"""
        SELECT job_id, status, payload, result, updated_at
        FROM backend_jobs
        WHERE status IN ({placeholders})
        ORDER BY updated_at ASC
        """,
        list(TERMINAL_BACKEND_JOB_STATUSES),
    ).fetchall()
    remove_ids: list[str] = []
    by_status: dict[str, int] = {}
    total_bytes = 0
    for row in rows:
        if not (include_all or retention_days == 0 or int(row["updated_at"]) <= cutoff):
            continue
        remove_ids.append(str(row["job_id"]))
        status = str(row["status"])
        by_status[status] = by_status.get(status, 0) + 1
        total_bytes += len(str(row["payload"]).encode("utf-8"))
        total_bytes += len(str(row["result"] or "").encode("utf-8"))
    if remove_ids and not dry_run:
        with write_txn(conn):
            for job_id in remove_ids:
                conn.execute("DELETE FROM backend_jobs WHERE job_id = ?", (job_id,))
    removed = 0 if dry_run else len(remove_ids)
    return {
        "candidates": len(rows),
        "would_remove": len(remove_ids),
        "removed": removed,
        "bytes": total_bytes,
        **{f"status_{status}": count for status, count in sorted(by_status.items())},
    }


def cleanup_terminal_plugin_work_items(
    conn: sqlite3.Connection,
    *,
    include_all: bool = False,
    retention_days: int = 30,
    now: int | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    current = int(now if now is not None else _now())
    cutoff = current - (max(0, int(retention_days)) * 86400)
    placeholders = ",".join("?" for _ in TERMINAL_PLUGIN_WORK_ITEM_STATUSES)
    rows = conn.execute(
        f"""
        SELECT work_item_id, status, payload, result, updated_at
        FROM plugin_work_items
        WHERE status IN ({placeholders})
        ORDER BY updated_at ASC
        """,
        list(TERMINAL_PLUGIN_WORK_ITEM_STATUSES),
    ).fetchall()
    remove_ids: list[str] = []
    by_status: dict[str, int] = {}
    total_bytes = 0
    for row in rows:
        if not (include_all or retention_days == 0 or int(row["updated_at"]) <= cutoff):
            continue
        remove_ids.append(str(row["work_item_id"]))
        status = str(row["status"])
        by_status[status] = by_status.get(status, 0) + 1
        total_bytes += len(str(row["payload"]).encode("utf-8"))
        total_bytes += len(str(row["result"] or "").encode("utf-8"))
    if remove_ids and not dry_run:
        with write_txn(conn):
            for work_item_id in remove_ids:
                conn.execute("DELETE FROM plugin_work_items WHERE work_item_id = ?", (work_item_id,))
    removed = 0 if dry_run else len(remove_ids)
    return {
        "candidates": len(rows),
        "would_remove": len(remove_ids),
        "removed": removed,
        "bytes": total_bytes,
        **{f"status_{status}": count for status, count in sorted(by_status.items())},
    }


def cleanup_reviewed_memory_proposals(
    conn: sqlite3.Connection,
    *,
    include_all: bool = False,
    retention_days: int = 90,
    now: int | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    current = int(now if now is not None else _now())
    cutoff = current - (max(0, int(retention_days)) * 86400)
    placeholders = ",".join("?" for _ in REVIEWED_MEMORY_PROPOSAL_STATUSES)
    rows = conn.execute(
        f"""
        SELECT id, status, summary, provenance, reason, updated_at
        FROM memory_proposals
        WHERE status IN ({placeholders})
        ORDER BY updated_at ASC
        """,
        list(REVIEWED_MEMORY_PROPOSAL_STATUSES),
    ).fetchall()
    remove_ids: list[str] = []
    by_status: dict[str, int] = {}
    total_bytes = 0
    for row in rows:
        if not (include_all or retention_days == 0 or int(row["updated_at"]) <= cutoff):
            continue
        remove_ids.append(str(row["id"]))
        status = str(row["status"])
        by_status[status] = by_status.get(status, 0) + 1
        total_bytes += len(str(row["summary"]).encode("utf-8"))
        total_bytes += len(str(row["provenance"]).encode("utf-8"))
        total_bytes += len(str(row["reason"] or "").encode("utf-8"))
    if remove_ids and not dry_run:
        with write_txn(conn):
            for proposal_id in remove_ids:
                conn.execute("DELETE FROM memory_proposals WHERE id = ?", (proposal_id,))
    removed = 0 if dry_run else len(remove_ids)
    return {
        "candidates": len(rows),
        "would_remove": len(remove_ids),
        "removed": removed,
        "bytes": total_bytes,
        **{f"status_{status}": count for status, count in sorted(by_status.items())},
    }


def cleanup_inbox_events(
    conn: sqlite3.Connection,
    *,
    include_all: bool = False,
    retention_days: int = 30,
    now: int | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    current = int(now if now is not None else _now())
    cutoff = current - (max(0, int(retention_days)) * 86400)
    rows = conn.execute(
        """
        SELECT event_id, payload, received_at, read_at
        FROM inbox_events
        ORDER BY received_at ASC
        """
    ).fetchall()
    remove_ids: list[str] = []
    unread = 0
    total_bytes = 0
    for row in rows:
        if not (include_all or retention_days == 0 or int(row["received_at"]) <= cutoff):
            continue
        remove_ids.append(str(row["event_id"]))
        if row["read_at"] is None:
            unread += 1
        total_bytes += len(str(row["payload"]).encode("utf-8"))
    if remove_ids and not dry_run:
        with write_txn(conn):
            for event_id in remove_ids:
                conn.execute("DELETE FROM inbox_events WHERE event_id = ?", (event_id,))
    removed = 0 if dry_run else len(remove_ids)
    return {
        "candidates": len(rows),
        "would_remove": len(remove_ids),
        "removed": removed,
        "bytes": total_bytes,
        "unread": unread,
    }


def replace_memory_cache(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    workspace_binding_id: str,
    version: str,
    items: list[dict[str, Any]],
) -> MemoryCache:
    now = _now()
    with write_txn(conn):
        conn.execute(
            "INSERT INTO memory_cache (workspace_binding_id, project_id, version, items, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(workspace_binding_id) DO UPDATE SET "
            "project_id = excluded.project_id, version = excluded.version, "
            "items = excluded.items, updated_at = excluded.updated_at",
            (workspace_binding_id, project_id, version, _json_dumps(items), now),
        )
    loaded = get_memory_cache(conn, workspace_binding_id)
    assert loaded is not None
    return loaded


def get_memory_cache(conn: sqlite3.Connection, workspace_binding_id: str) -> MemoryCache | None:
    row = conn.execute(
        "SELECT * FROM memory_cache WHERE workspace_binding_id = ?",
        (workspace_binding_id,),
    ).fetchone()
    if row is None:
        return None
    raw_items = _json_loads(row["items"])
    items = raw_items if isinstance(raw_items, list) else []
    return MemoryCache(
        project_id=row["project_id"],
        workspace_binding_id=row["workspace_binding_id"],
        version=row["version"],
        items=[item for item in items if isinstance(item, dict)],
        updated_at=row["updated_at"],
    )


def save_inbox_event(
    conn: sqlite3.Connection,
    *,
    event_id: str,
    project_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    with write_txn(conn):
        conn.execute(
            "INSERT OR IGNORE INTO inbox_events "
            "(event_id, project_id, event_type, payload, received_at) VALUES (?, ?, ?, ?, ?)",
            (event_id, project_id, event_type, _json_dumps(payload), _now()),
        )


def list_inbox_events(conn: sqlite3.Connection, *, project_id: str | None = None) -> list[InboxEvent]:
    if project_id:
        rows = conn.execute(
            "SELECT * FROM inbox_events WHERE project_id = ? ORDER BY received_at ASC",
            (project_id,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM inbox_events ORDER BY received_at ASC").fetchall()
    return [_event_from_row(row) for row in rows]


def count_inbox_events(conn: sqlite3.Connection) -> dict[str, int]:
    row = conn.execute(
        "SELECT COUNT(*) AS total, SUM(CASE WHEN read_at IS NULL THEN 1 ELSE 0 END) AS unread FROM inbox_events"
    ).fetchone()
    return {"total": int(row["total"] or 0), "unread": int(row["unread"] or 0)}
