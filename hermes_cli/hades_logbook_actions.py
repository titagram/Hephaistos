"""Bounded, durable project-logbook CLI actions.

The outbox is intentionally separate from the request transport: every append
is committed locally before an authenticated network request can happen.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import stat
import sys
import time
from typing import Any

from hermes_cli import hades_backend_db as db
from hermes_cli.hades_backend_client import HadesBackendError, redact_secret


LOGBOOK_EVENT_TYPES = frozenset({
    "change", "creation", "import", "projection", "verification", "wiki",
    "decision", "failure", "rollback", "note",
})
LOGBOOK_SEVERITIES = frozenset({"info", "warning", "error"})
MAX_NARRATIVE_CODE_POINTS = 8_000
MAX_NARRATIVE_BYTES = MAX_NARRATIVE_CODE_POINTS * 4
MAX_SUMMARY_CODE_POINTS = 240
MAX_REFERENCE_COUNT = 40
MAX_RETRY_ATTEMPTS = 5
RETRY_BASE_SECONDS = 30
RETRY_MAX_SECONDS = 3_600
_REFERENCE_KIND = re.compile(r"\A[a-z][a-z0-9_-]{0,63}\Z")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class LogbookActionResult:
    exit_code: int
    state: str
    message: str
    payload: dict[str, Any] | None = None


def _now(value: int | None = None) -> int:
    return int(time.time()) if value is None else int(value)


def read_narrative_file(path_value: str | Path) -> str:
    """Read one bounded real UTF-8 file; stdin, links, and devices are excluded."""

    clean = str(path_value or "").strip()
    if not clean or clean == "-":
        raise ValueError("narrative file must be a regular UTF-8 file, not standard input")
    path = Path(clean).expanduser()
    try:
        metadata = path.lstat()
    except OSError:
        raise ValueError("narrative file could not be read") from None
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("narrative file must be a regular file")
    if metadata.st_size > MAX_NARRATIVE_BYTES:
        raise ValueError("narrative file exceeds 8,000 code points")
    try:
        raw = path.read_bytes()
        narrative = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        raise ValueError("narrative file must be valid UTF-8") from None
    if len(narrative) > MAX_NARRATIVE_CODE_POINTS:
        raise ValueError("narrative file exceeds 8,000 code points")
    return narrative


def _clean_text(value: Any, *, field: str, maximum: int, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise ValueError(f"logbook {field} is required")
        return None
    if not isinstance(value, str):
        raise ValueError(f"logbook {field} must be text")
    clean = value.strip()
    if required and not clean:
        raise ValueError(f"logbook {field} is required")
    if len(clean) > maximum or _CONTROL.search(clean):
        raise ValueError(f"logbook {field} is invalid or too long")
    return clean or None


def _parse_references(value: Any) -> list[dict[str, str]]:
    raw_values = list(value or [])
    if len(raw_values) > MAX_REFERENCE_COUNT:
        raise ValueError(f"logbook references exceed {MAX_REFERENCE_COUNT}")
    references: list[dict[str, str]] = []
    for raw in raw_values:
        if isinstance(raw, dict) and set(raw) == {"kind", "id"}:
            kind = str(raw["kind"]).strip()
            reference_id = str(raw["id"]).strip()
        elif isinstance(raw, str) and ":" in raw:
            kind, reference_id = raw.split(":", 1)
            kind = kind.strip()
            reference_id = reference_id.strip()
        else:
            raise ValueError("logbook reference must use KIND:ID")
        if _REFERENCE_KIND.fullmatch(kind) is None or not reference_id or len(reference_id) > 255 or _CONTROL.search(reference_id):
            raise ValueError("logbook reference must use a safe KIND:ID")
        references.append({"kind": kind, "id": reference_id})
    return references


def canonical_logbook_request(command: dict[str, Any], binding: Any) -> dict[str, Any]:
    event_type = _clean_text(command.get("event_type") or command.get("type"), field="type", maximum=32, required=True)
    if event_type not in LOGBOOK_EVENT_TYPES:
        raise ValueError("logbook type is not supported")
    summary = _clean_text(command.get("summary"), field="summary", maximum=MAX_SUMMARY_CODE_POINTS, required=True)
    severity = _clean_text(command.get("severity") or "info", field="severity", maximum=16, required=True)
    if severity not in LOGBOOK_SEVERITIES:
        raise ValueError("logbook severity must be info, warning, or error")
    idempotency_key = _clean_text(command.get("idempotency_key"), field="idempotency key", maximum=255, required=True)
    correlation_id = _clean_text(command.get("correlation_id"), field="correlation id", maximum=255)
    narrative = command.get("narrative_markdown")
    if narrative is not None:
        if not isinstance(narrative, str) or len(narrative) > MAX_NARRATIVE_CODE_POINTS:
            raise ValueError("logbook narrative exceeds 8,000 code points")
    workspace_binding_id = _clean_text(
        getattr(binding, "backend_workspace_binding_id", None),
        field="workspace binding id", maximum=255, required=True,
    )
    request: dict[str, Any] = {
        "workspace_binding_id": workspace_binding_id,
        "event_type": event_type,
        "severity": severity,
        "summary": summary,
        "idempotency_key": idempotency_key,
        "references": _parse_references(command.get("references", [])),
    }
    if narrative:
        request["narrative_markdown"] = narrative
    if correlation_id:
        request["correlation_id"] = correlation_id
    return request


def enqueue_logbook_entry(
    conn: Any, *, command: dict[str, Any], binding: Any, now: int | None = None
) -> db.LogbookOutboxEntry:
    request = canonical_logbook_request(command, binding)
    project_id = _clean_text(getattr(binding, "project_id", None), field="project id", maximum=255, required=True)
    return db.enqueue_logbook_outbox_entry(
        conn,
        project_id=project_id or "",
        workspace_binding_id=request["workspace_binding_id"],
        idempotency_key=request["idempotency_key"],
        request=request,
        now=_now(now),
    )


def _response_identity(response: Any) -> tuple[str | None, str | None]:
    pending = [response]
    while pending:
        value = pending.pop()
        if not isinstance(value, dict):
            continue
        key = value.get("idempotency_key")
        if isinstance(key, str) and key:
            entry_id = value.get("id") or value.get("entry_id")
            return (str(entry_id) if entry_id else None, key)
        pending.extend(item for item in value.values() if isinstance(item, dict))
    return None, None


def _error_text(exc: BaseException) -> str:
    message = redact_secret(str(exc)).replace("\n", " ").strip()
    return message[:1_000] or "backend logbook request failed"


def _is_capability_denial(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None)
    code = str(getattr(exc, "code", "") or "")
    return status in {401, 403} or "capability" in code or "not_allowed" in code


def _retry_at(entry: db.LogbookOutboxEntry, now: int) -> int:
    delay = min(RETRY_MAX_SECONDS, RETRY_BASE_SECONDS * (2 ** max(0, entry.attempts - 1)))
    return int(now) + delay


def _dead_letter_message() -> str:
    return (
        "logbook write was persisted but rejected permanently; ask a project administrator "
        "to grant write_project_logbook, then re-register with `hades backend setup`"
    )


def flush_due_logbook_entries(
    conn: Any, client: Any, *, now: int | None = None, limit: int = 20,
    project_id: str | None = None, workspace_binding_id: str | None = None,
) -> dict[str, int]:
    """Flush at most twenty persisted entries and surface retry/dead-letter state."""

    timestamp = _now(now)
    summary = {"pending": 0, "sent": 0, "retry": 0, "dead_letter": 0}
    for entry in db.lease_due_logbook_outbox_entries(
        conn, now=timestamp, limit=limit, project_id=project_id,
        workspace_binding_id=workspace_binding_id,
    ):
        try:
            response = client.create_logbook_entry(entry.project_id, **entry.request)
        except Exception as exc:
            if isinstance(exc, HadesBackendError) and exc.status_code == 409:
                response_id, response_key = _response_identity(exc.details)
                if response_key == entry.idempotency_key:
                    db.resolve_logbook_outbox_entry(
                        conn, entry_id=entry.id, lease_token=entry.lease_token or "", state="sent",
                        now=timestamp, response_id=response_id,
                    )
                    summary["sent"] += 1
                    continue
                db.resolve_logbook_outbox_entry(
                    conn, entry_id=entry.id, lease_token=entry.lease_token or "", state="dead_letter",
                    now=timestamp, last_error="backend idempotency conflict does not match the persisted request",
                )
                summary["dead_letter"] += 1
                continue
            if _is_capability_denial(exc) or entry.attempts >= MAX_RETRY_ATTEMPTS:
                db.resolve_logbook_outbox_entry(
                    conn, entry_id=entry.id, lease_token=entry.lease_token or "", state="dead_letter",
                    now=timestamp, last_error=_error_text(exc),
                )
                summary["dead_letter"] += 1
            else:
                db.resolve_logbook_outbox_entry(
                    conn, entry_id=entry.id, lease_token=entry.lease_token or "", state="pending",
                    now=timestamp, next_attempt_at=_retry_at(entry, timestamp), last_error=_error_text(exc),
                )
                summary["retry"] += 1
            continue
        response_id, _response_key = _response_identity(response)
        db.resolve_logbook_outbox_entry(
            conn, entry_id=entry.id, lease_token=entry.lease_token or "", state="sent",
            now=timestamp, response_id=response_id,
        )
        summary["sent"] += 1
    pending = [
        entry for entry in db.list_logbook_outbox_entries(conn, states=("pending", "leased"))
        if (not project_id or entry.project_id == project_id)
        and (not workspace_binding_id or entry.workspace_binding_id == workspace_binding_id)
    ]
    summary["pending"] = len(pending)
    return summary


def run_logbook_write(
    conn: Any, *, command: dict[str, Any], binding: Any, client: Any, now: int | None = None
) -> LogbookActionResult:
    """Persist first, then make the best bounded attempt without false success."""

    entry = enqueue_logbook_entry(conn, command=command, binding=binding, now=now)
    flush_due_logbook_entries(
        conn,
        client,
        now=now,
        limit=20,
        project_id=entry.project_id,
        workspace_binding_id=entry.workspace_binding_id,
    )
    current = db.get_logbook_outbox_entry(conn, entry.id)
    assert current is not None
    if current.state == "sent":
        return LogbookActionResult(0, "sent", "logbook entry recorded", {"entry_id": current.response_id})
    if current.state == "dead_letter":
        return LogbookActionResult(1, "dead_letter", _dead_letter_message())
    return LogbookActionResult(
        1,
        current.state,
        "logbook entry is durably queued; backend delivery is degraded and will retry on sync",
    )


def _current_agent_binding():
    from hermes_cli.hades_backend_cmd import _current_workspace_scoped_agent_binding

    return _current_workspace_scoped_agent_binding()


def _list_limit(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 50:
        raise ValueError("logbook list limit must be between 1 and 50")
    return value


def run_logbook_list(
    *, event_type: str | None, actor: str | None, severity: str | None,
    cursor: str | None, limit: int, client: Any,
) -> LogbookActionResult:
    _agent, binding = _current_agent_binding()
    clean_type = _clean_text(event_type, field="type", maximum=32)
    if clean_type and clean_type not in LOGBOOK_EVENT_TYPES:
        raise ValueError("logbook type is not supported")
    clean_severity = _clean_text(severity, field="severity", maximum=16)
    if clean_severity and clean_severity not in LOGBOOK_SEVERITIES:
        raise ValueError("logbook severity must be info, warning, or error")
    request = {
        "workspace_binding_id": binding.backend_workspace_binding_id,
        "types": clean_type,
        "actor": _clean_text(actor, field="actor", maximum=255),
        "severity": clean_severity,
        "cursor": _clean_text(cursor, field="cursor", maximum=1_024),
        "limit": _list_limit(limit),
    }
    response = client.list_logbook_entries(
        binding.project_id, **{key: value for key, value in request.items() if value is not None}
    )
    return LogbookActionResult(0, "read", "logbook entries loaded", response)


def run_logbook_show(*, entry_id: str, client: Any) -> LogbookActionResult:
    _agent, binding = _current_agent_binding()
    clean_id = _clean_text(entry_id, field="entry id", maximum=255, required=True)
    response = client.get_logbook_entry(
        binding.project_id, clean_id or "", workspace_binding_id=binding.backend_workspace_binding_id
    )
    return LogbookActionResult(0, "read", "logbook entry loaded", response)


def _print_result(result: LogbookActionResult, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps({"state": result.state, "message": result.message, "payload": result.payload}, sort_keys=True))
        return
    print(result.message)
    if result.payload is not None:
        print(json.dumps(result.payload, indent=2, sort_keys=True))


def run_logbook_action(args: argparse.Namespace) -> int:
    action = str(getattr(args, "logbook_action", "") or "").strip()
    client = None
    try:
        if action in {"list", "show"}:
            agent, _binding = _current_agent_binding()
            from hermes_cli import hades_backend_runtime as runtime

            client = runtime.client_for_agent(agent)
            if action == "list":
                result = run_logbook_list(
                    event_type=getattr(args, "event_type", None), actor=getattr(args, "actor", None),
                    severity=getattr(args, "severity", None), cursor=getattr(args, "cursor", None),
                    limit=getattr(args, "limit", 20), client=client,
                )
            else:
                result = run_logbook_show(entry_id=getattr(args, "entry_id", ""), client=client)
        elif action == "write":
            agent, binding = _current_agent_binding()
            narrative_path = getattr(args, "narrative_file", None)
            command = {
                "event_type": getattr(args, "event_type", None),
                "summary": getattr(args, "summary", None),
                "severity": getattr(args, "severity", None),
                "idempotency_key": getattr(args, "idempotency_key", None),
                "references": getattr(args, "reference", None),
                "correlation_id": getattr(args, "correlation_id", None),
                "narrative_markdown": read_narrative_file(narrative_path) if narrative_path else None,
            }
            from hermes_cli import hades_backend_runtime as runtime

            client = runtime.client_for_agent(agent)
            with db.connect_closing() as conn:
                result = run_logbook_write(conn, command=command, binding=binding, client=client)
        else:
            raise ValueError("logbook action is required: list, show, or write")
    except Exception as exc:
        result = LogbookActionResult(1, "error", redact_secret(str(exc)))
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()
    _print_result(result, as_json=bool(getattr(args, "json", False)))
    return result.exit_code
