from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path

from hermes_constants import get_hermes_home
from hermes_cli.gnothi.redaction import redact_value

_EVENT_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _bounded(value: object, limit: int = 256) -> str:
    safe, _ = redact_value(str(value or ""))
    return str(safe)[:limit]


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def emit_experience_event(
    *,
    event_type: str,
    generation_id: str,
    component_id: str,
    capability_id: str | None,
    operation: str,
    failure_class: str | None,
    severity: str,
    retry_count: int = 0,
    task_impact: str = "unknown",
    recovered: bool = False,
    evidence_refs: list[str] | None = None,
    occurred_at: str | None = None,
) -> None:
    signature_fields = {
        "generation_id": _bounded(generation_id),
        "component_id": _bounded(component_id),
        "capability_id": _bounded(capability_id) if capability_id else None,
        "operation": _bounded(operation),
        "failure_class": _bounded(failure_class) if failure_class else None,
    }
    bounded_signature = f"sha256:{_digest(signature_fields)}"
    row = {
        "event_type": _bounded(event_type),
        **signature_fields,
        "bounded_signature": bounded_signature,
        "severity": _bounded(severity, 32),
        "retry_count": max(0, min(int(retry_count), 1_000_000)),
        "task_impact": _bounded(task_impact, 64),
        "recovered": bool(recovered),
        "evidence_refs": sorted({_bounded(ref) for ref in (evidence_refs or [])})[:20],
        "occurred_at": _bounded(occurred_at or _now(), 64),
    }
    row["event_id"] = f"event:{_digest(row)[:32]}"
    encoded = json.dumps(
        row,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    path = get_hermes_home() / "logs" / "organism-events.jsonl"
    with _EVENT_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.write(descriptor, (encoded + "\n").encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
