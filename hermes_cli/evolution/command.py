"""Bounded, sanitized operator reads for local evolution lifecycle state."""

from __future__ import annotations

import json
import re
from typing import Any

from hermes_constants import get_hermes_home
from agent.redact import redact_sensitive_text

from .authorization import AuthorizationError, _privacy_safe_symbolic
from .bootstrap import EvolutionBootstrapError, ensure_evolution_initialized, evolution_state_kind
from .ledger import EvolutionLedger, EvolutionLedgerError, StoredEvent, _require_timestamp
from .locking import LifecycleLockError
from .reconcile import reconcile_evolution_state, read_evolution_snapshot

_SYMBOL = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z", re.ASCII)
_DIGEST = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z\Z", re.ASCII)
_UUID = re.compile(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\Z", re.ASCII)
_EVENT_TYPES = frozenset({"baseline_designated", "state_transition", "supervisor_recovery", "authorization_requested", "authorization_granted", "authorization_denied", "authorization_consumed"})
_ACTORS = frozenset({"system", "supervisor", "operator", "host"})
_REASON_CODES = frozenset({"baseline", "transition", "active_restored_from_lkg", "stable_base_only", "authorization_requested", "authorization_granted", "authorization_denied", "authorization_consumed"})
_PUBLIC_SUMMARIES = frozenset({"baseline designation", "restored active pointer from proven last known good", "evolution overlays disabled because no pointer was proven"})
_STATES = frozenset({"draft", "research_authorized", "blueprint_ready", "build_approved", "building", "quarantined", "canary_running", "promotion_ready", "active", "stable", "rejected", "research_expired", "build_failed", "canary_failed", "rolled_back", "retired"})


def _is_public_identity(value: object) -> bool:
    if not isinstance(value, str):
        return False
    if _UUID.fullmatch(value) is not None:
        return True
    try:
        _privacy_safe_symbolic(
            value,
            code="invalid_public_identity",
            limit=64,
        )
    except AuthorizationError:
        return False
    return redact_sensitive_text(value, force=True) == value


def _emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False))


def _status() -> dict[str, Any]:
    root = get_hermes_home() / "evolution"
    state_kind = evolution_state_kind(root)
    if state_kind == "uninitialized":
        return {"schema_version": 1, "status": "uninitialized", "initialized": False,
                "overlay_enabled": False, "active_generation_id": None,
                "last_known_good_generation_id": None, "diagnostics": []}
    if state_kind == "blocked":
        return {"schema_version": 1, "status": "blocked", "initialized": False,
                "overlay_enabled": False, "active_generation_id": None,
                "last_known_good_generation_id": None,
                "diagnostics": ["evolution_unavailable"]}
    result = reconcile_evolution_state(repair=False)
    return {"schema_version": 1, "status": result.status, "initialized": True,
            "overlay_enabled": result.overlay_enabled,
            "active_generation_id": None if result.active is None else result.active.generation_id,
            "last_known_good_generation_id": None if result.last_known_good is None else result.last_known_good.generation_id,
            "diagnostics": list(result.diagnostics)}


def _event(event: StoredEvent) -> dict[str, Any]:
    def identity(value: str | None) -> str | None:
        return value if _is_public_identity(value) else None
    def timestamp(value: object) -> str | None:
        try:
            return _require_timestamp(value)
        except EvolutionLedgerError:
            return None
    state = lambda value: value if value in _STATES else None
    return {"sequence": event.event_sequence, "event_id": identity(event.event_id),
            "attempt_id": identity(event.attempt_id),
            "generation_id": event.generation_id if event.generation_id and _DIGEST.fullmatch(event.generation_id) else None,
            "event_type": event.event_type if event.event_type in _EVENT_TYPES else None,
            "prior_state": state(event.prior_state), "next_state": state(event.next_state),
            "actor": event.actor if event.actor in _ACTORS else identity(event.actor),
            "input_digests": [digest for digest in event.input_digests if _DIGEST.fullmatch(digest)],
            "authorization_id": identity(event.authorization_id),
            "reason_code": event.reason_code if event.reason_code in _REASON_CODES else None,
            "reason_summary": event.reason_summary if event.reason_summary in _PUBLIC_SUMMARIES else "redacted",
            "timestamp": timestamp(event.created_at),
            "event_digest": event.event_digest if _DIGEST.fullmatch(event.event_digest) else None}


def _history(limit: int, after: int) -> dict[str, Any]:
    if type(limit) is not int or not 1 <= limit <= 1000 or type(after) is not int or after < 0:
        raise EvolutionLedgerError("invalid_history_limit")
    root = get_hermes_home() / "evolution"
    kind = evolution_state_kind(root)
    if kind == "uninitialized":
        return {"schema_version": 1, "status": "uninitialized", "items": [], "next_after": None}
    if kind == "blocked":
        raise EvolutionLedgerError("evolution_unavailable")
    if not (root / "evolution.db").exists():
        raise EvolutionLedgerError("evolution_unavailable")
    def query(ledger: EvolutionLedger):
        if ledger.verify_chain():
            raise EvolutionLedgerError("invalid_event_chain")
        return ledger.history(limit=limit, after=after)
    items = read_evolution_snapshot(query)
    return {"schema_version": 1, "status": "ok", "items": [_event(item) for item in items],
            "next_after": items[-1].event_sequence if len(items) == limit else None}


def _show(kind: str, record_id: str) -> dict[str, Any]:
    valid = bool(_SYMBOL.fullmatch(record_id)) if kind == "suggestion" else bool(_DIGEST.fullmatch(record_id))
    if not valid:
        return {"schema_version": 1, "status": "missing", "kind": kind, "record": None}
    root = get_hermes_home() / "evolution"
    state_kind = evolution_state_kind(root)
    if state_kind == "uninitialized":
        return {"schema_version": 1, "status": "missing", "kind": kind, "record": None}
    if state_kind == "blocked":
        raise EvolutionLedgerError("evolution_unavailable")
    if not (root / "evolution.db").exists():
        raise EvolutionLedgerError("evolution_unavailable")
    queries = {
        "suggestion": ("SELECT suggestion_id, canonical_digest, state, created_at FROM suggestions WHERE suggestion_id = ?", (record_id,), ("suggestion_id", "canonical_digest", "state", "created_at")),
        "blueprint": ("SELECT blueprint_id, canonical_digest, state, created_at FROM blueprints WHERE canonical_digest = ?", (record_id,), ("blueprint_id", "canonical_digest", "state", "created_at")),
        "generation": ("SELECT generation_id, canonical_digest, state, created_at FROM generations WHERE generation_id = ?", (record_id,), ("generation_id", "canonical_digest", "state", "created_at")),
        "report": ("SELECT promotion_report_id, generation_id, report_digest, state, created_at FROM promotion_reports WHERE report_digest = ?", (record_id,), ("promotion_report_id", "generation_id", "report_digest", "state", "created_at")),
    }
    sql, parameters, fields = queries[kind]
    def query(ledger: EvolutionLedger):
        if ledger.verify_chain():
            raise EvolutionLedgerError("invalid_event_chain")
        return ledger.connection.execute(sql, parameters).fetchone()
    row = read_evolution_snapshot(query)
    if row is not None:
        record = {field: row[field] for field in fields}
        for field, value in record.items():
            if value is None:
                continue
            if field.endswith("digest") or field == "generation_id":
                if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
                    return {"schema_version": 1, "status": "missing", "kind": kind, "record": None}
            elif field == "created_at":
                try:
                    _require_timestamp(value)
                except EvolutionLedgerError:
                    return {"schema_version": 1, "status": "missing", "kind": kind, "record": None}
            elif field == "state":
                if value not in _STATES:
                    return {"schema_version": 1, "status": "missing", "kind": kind, "record": None}
            elif field == "suggestion_id":
                if not isinstance(value, str) or _SYMBOL.fullmatch(value) is None or not _is_public_identity(value):
                    return {"schema_version": 1, "status": "missing", "kind": kind, "record": None}
            elif field in {"blueprint_id", "promotion_report_id"}:
                if not _is_public_identity(value):
                    return {"schema_version": 1, "status": "missing", "kind": kind, "record": None}
            elif not _is_public_identity(value):
                return {"schema_version": 1, "status": "missing", "kind": kind, "record": None}
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
    except (
        EvolutionBootstrapError,
        EvolutionLedgerError,
        LifecycleLockError,
        OSError,
        ValueError,
    ):
        if getattr(args, "action", None) == "history":
            _emit({"schema_version": 1, "status": "blocked", "items": [], "next_after": None})
        elif getattr(args, "action", None) == "show":
            _emit({"schema_version": 1, "status": "missing", "kind": getattr(args, "kind", None), "record": None})
        else:
            _emit({"schema_version": 1, "status": "blocked", "initialized": False,
                   "overlay_enabled": False, "active_generation_id": None,
                   "last_known_good_generation_id": None, "diagnostics": ["evolution_unavailable"]})
        return 1
    return 2
