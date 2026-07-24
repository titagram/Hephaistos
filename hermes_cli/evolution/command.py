"""Bounded, sanitized operator reads for local evolution lifecycle state."""

from __future__ import annotations

import json
import re
from typing import Any

from hermes_constants import get_hermes_home

from .bootstrap import EvolutionBootstrapError, ensure_evolution_initialized
from .ledger import EvolutionLedger, EvolutionLedgerError, StoredEvent
from .reconcile import reconcile_evolution_state, read_evolution_snapshot

_SYMBOL = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z", re.ASCII)
_DIGEST = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_PUBLIC = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}\Z", re.ASCII)


def _emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False))


def _status() -> dict[str, Any]:
    root = get_hermes_home() / "evolution"
    if not root.exists():
        return {"schema_version": 1, "status": "uninitialized", "initialized": False,
                "overlay_enabled": False, "active_generation_id": None,
                "last_known_good_generation_id": None, "diagnostics": []}
    result = reconcile_evolution_state(repair=False)
    return {"schema_version": 1, "status": result.status, "initialized": True,
            "overlay_enabled": result.overlay_enabled,
            "active_generation_id": None if result.active is None else result.active.generation_id,
            "last_known_good_generation_id": None if result.last_known_good is None else result.last_known_good.generation_id,
            "diagnostics": list(result.diagnostics)}


def _event(event: StoredEvent) -> dict[str, Any]:
    def public(value: str | None) -> str | None:
        return value if value is None or _PUBLIC.fullmatch(value) else None

    summary = " ".join(event.reason_summary.split())[:512]
    if "/" in summary or "\\" in summary or re.search(r"(?:sk|pk|ghp|xox)[_-]", summary, re.I):
        summary = "redacted"
    return {"sequence": event.event_sequence, "event_id": public(event.event_id),
            "attempt_id": public(event.attempt_id), "generation_id": public(event.generation_id),
            "event_type": public(event.event_type), "prior_state": public(event.prior_state),
            "next_state": public(event.next_state), "actor": public(event.actor),
            "input_digests": list(event.input_digests), "authorization_id": event.authorization_id,
            "reason_code": public(event.reason_code),
            "reason_summary": summary,
            "timestamp": event.created_at, "event_digest": event.event_digest}


def _history(limit: int, after: int) -> dict[str, Any]:
    if type(limit) is not int or not 1 <= limit <= 1000 or type(after) is not int or after < 0:
        raise EvolutionLedgerError("invalid_history_limit")
    root = get_hermes_home() / "evolution"
    if not root.exists() or not (root / "evolution.db").exists():
        return {"schema_version": 1, "status": "uninitialized", "items": [], "next_after": None}
    items = read_evolution_snapshot(lambda ledger: ledger.history(limit=limit, after=after))
    return {"schema_version": 1, "status": "ok", "items": [_event(item) for item in items],
            "next_after": items[-1].event_sequence if len(items) == limit else None}


def _show(kind: str, record_id: str) -> dict[str, Any]:
    valid = bool(_SYMBOL.fullmatch(record_id)) if kind == "suggestion" else bool(_DIGEST.fullmatch(record_id))
    if not valid:
        return {"schema_version": 1, "status": "missing", "kind": kind, "record": None}
    root = get_hermes_home() / "evolution"
    if not root.exists() or not (root / "evolution.db").exists():
        return {"schema_version": 1, "status": "missing", "kind": kind, "record": None}
    queries = {
        "suggestion": ("SELECT suggestion_id, canonical_digest, state, created_at FROM suggestions WHERE suggestion_id = ?", (record_id,), ("suggestion_id", "canonical_digest", "state", "created_at")),
        "blueprint": ("SELECT blueprint_id, canonical_digest, state, created_at FROM blueprints WHERE canonical_digest = ?", (record_id,), ("blueprint_id", "canonical_digest", "state", "created_at")),
        "generation": ("SELECT generation_id, canonical_digest, state, created_at FROM generations WHERE generation_id = ?", (record_id,), ("generation_id", "canonical_digest", "state", "created_at")),
        "report": ("SELECT promotion_report_id, generation_id, report_digest, state, created_at FROM promotion_reports WHERE report_digest = ?", (record_id,), ("promotion_report_id", "generation_id", "report_digest", "state", "created_at")),
    }
    sql, parameters, fields = queries[kind]
    row = read_evolution_snapshot(lambda ledger: ledger.connection.execute(sql, parameters).fetchone())
    if row is not None:
        record = {field: row[field] for field in fields}
        return {"schema_version": 1, "status": "found", "kind": kind, "record": record}
    return {"schema_version": 1, "status": "missing", "kind": kind, "record": None}


def evolution_command(args: Any) -> int:
    try:
        action = args.action
        if action == "init":
            ensure_evolution_initialized()
            _emit(_status())
            return 0
        if action == "status":
            _emit(_status())
            return 0
        if action == "history":
            _emit(_history(args.limit, args.after))
            return 0
        if action == "show":
            value = _show(args.kind, args.record_id)
            _emit(value)
            return 0 if value["status"] == "found" else 1
    except (EvolutionBootstrapError, EvolutionLedgerError, OSError, ValueError):
        _emit({"schema_version": 1, "status": "blocked", "diagnostics": ["evolution_unavailable"]})
        return 1
    return 2
