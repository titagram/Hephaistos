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



def _doctor(org_root: Any = None) -> dict[str, Any]:
    """Read-only bounded diagnostic of autopoiesis state."""
    from pathlib import Path
    from .organism_home import get_organism_home
    from .global_config import load_global_config, autopoiesis_enabled

    root = Path(org_root) if org_root else get_organism_home()
    cfg = load_global_config()

    diagnostic: dict[str, Any] = {
        "schema_version": 1,
        # Project B persistence is intentionally local. Backend sync is an
        # optional collaboration edge, not an Autopoiesis runtime dependency.
        "storage_mode": "local",
        "backend_required": False,
        "organism_root_exists": root.exists() if org_root else None,
        "autopoiesis_enabled": autopoiesis_enabled(),
        "config_observer_enabled": cfg.get("autopoiesis", {}).get("observer", {}).get("enabled", True),
    }

    try:
        from .organism_identity import load_organism_identity
        ident = load_organism_identity(root)
        diagnostic["organism_id"] = ident.organism_id[:8] + "..."
    except Exception:
        diagnostic["organism_id"] = None

    ledger_path = root / "evolution" / "evolution.db"
    diagnostic["ledger_exists"] = ledger_path.exists()
    if ledger_path.exists():
        try:
            from .ledger import EvolutionLedger
            ledger = EvolutionLedger(ledger_path)
            diagnostic["ledger_schema_version"] = ledger.schema_version
            ledger.connection.close()
        except Exception:
            diagnostic["ledger_schema_version"] = "error"

    try:
        from .observer_service import ObserverService
        svc = ObserverService(root)
        diagnostic["observer_circuit_open"] = svc.circuit_open
        diagnostic["observer_degraded_reason"] = svc.degraded_reason
    except Exception:
        diagnostic["observer_circuit_open"] = "error"

    try:
        from .telos_store import TelosStore
        store = TelosStore(root)
        digest = store.get_active_digest()
        diagnostic["telos_active_digest"] = (digest[:16] + "...") if digest else None
    except Exception:
        diagnostic["telos_active_digest"] = "error"

    return diagnostic


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
        if action == "pause":
            from .global_config import load_global_config, save_global_config
            cfg = load_global_config()
            cfg["autopoiesis"]["enabled"] = False
            save_global_config(cfg["autopoiesis"])
            _emit({"schema_version": 1, "action": "pause", "autopoiesis_enabled": False})
            return 0
        if action == "resume":
            from .global_config import load_global_config, save_global_config
            cfg = load_global_config()
            cfg["autopoiesis"]["enabled"] = True
            save_global_config(cfg["autopoiesis"])
            _emit({"schema_version": 1, "action": "resume", "autopoiesis_enabled": True})
            return 0
        if action == "doctor":
            _emit(_doctor(getattr(args, "org_root", None)))
            return 0
        if action == "telos_status":
            _emit(_telos_command("status", getattr(args, "org_root", None)))
            return 0
        if action == "telos_history":
            _emit(_telos_command("history", getattr(args, "org_root", None)))
            return 0
        if action == "telos_draft":
            _emit(_telos_command("draft", getattr(args, "org_root", None)))
            return 0
        if action == "telos_approve":
            result = _handle_telos_cli_transition(getattr(args, "digest", ""), "activate", getattr(args, "org_root", None))
            _emit(result)
            return 0 if result["status"] == "approved" else 1
        if action == "telos_rollback":
            result = _handle_telos_cli_transition(getattr(args, "digest", ""), "rollback", getattr(args, "org_root", None))
            _emit(result)
            return 0 if result["status"] == "approved" else 1
        if action == "observer_status":
            _emit(_observer_status(getattr(args, "org_root", None)))
            return 0
        if action == "observer_scan":
            _emit(_observer_scan(getattr(args, "org_root", None)))
            return 0
        if action == "suggestions":
            _emit(_suggestions_list(getattr(args, "org_root", None)))
            return 0
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


def _telos_command(sub: str, org_root: Any = None) -> dict[str, Any]:
    """Telos status, history, and draft commands.

    Model-facing operations may save drafts, inspect status, and inspect
    history.  They may NOT record host decisions, issue/consume grants,
    activate, or rollback.  The interactive approve/rollback CLI flow
    is handled by ``_handle_telos_approve_cli`` / ``_handle_telos_rollback_cli``.
    """
    from pathlib import Path
    from .organism_home import get_organism_home
    from .telos_store import TelosStore

    root = Path(org_root) if org_root else get_organism_home()
    store = TelosStore(root)

    if sub == "status":
        active = store.get_active_digest()
        return {
            "schema_version": 1,
            "action": "telos_status",
            "active_digest": active,
            "has_active": active is not None,
        }
    elif sub == "history":
        revisions_dir = root / "telos" / "revisions"
        digests = []
        if revisions_dir.is_dir():
            for f in sorted(revisions_dir.iterdir()):
                if f.suffix == ".json":
                    digests.append(f.stem)
        return {
            "schema_version": 1,
            "action": "telos_history",
            "revision_count": len(digests),
            "revisions": digests[:50],
        }
    elif sub == "draft":
        return {
            "schema_version": 1,
            "action": "telos_draft",
            "status": "unsupported",
            "reason": "draft requires a defined input contract; use telos workshop",
        }
    return {"schema_version": 1, "action": "telos", "error": "unknown_subcommand"}


def _handle_telos_cli_transition(digest: str, action: str, org_root: Any = None) -> dict[str, Any]:
    """Interactive Classic CLI host flow for ``hermes evolution telos <action> <digest>``.

    Resolves the global organism root, verifies the revision exists and
    matches the organism, creates a pending request via the broker, prompts
    the user via ``telos_approval_prompt``, then delegates to the shared
    ``perform_telos_transition`` service for the actual transition.

    ``action`` must be exactly ``"activate"`` or ``"rollback"``; output
    ``action`` maps ``activate`` → ``telos_approve`` and ``rollback`` →
    ``telos_rollback``.  Never emits ``telos_activate``.
    """
    import uuid as _uuid_mod
    from pathlib import Path
    from .organism_home import get_organism_home
    from .telos_store import TelosStore
    from .telos_approval import (
        HostApprovalContext,
        TelosApprovalPrompt,
        TelosApprovalError,
        telos_approval_prompt,
    )
    from .ledger import EvolutionLedger
    from .host_transition import (
        perform_telos_transition,
        prepare_telos_pending_request,
    )

    if action not in ("activate", "rollback"):
        return {
            "schema_version": 1,
            "action": f"telos_{action}",
            "status": "invalid",
            "request_id": None,
            "message": "unsupported action",
        }

    action_name = "telos_approve" if action == "activate" else "telos_rollback"

    if not digest or _DIGEST.fullmatch(digest) is None:
        return {
            "schema_version": 1,
            "action": action_name,
            "status": "invalid",
            "request_id": None,
            "message": "invalid digest",
        }

    surface = "classic_cli"
    actor_ref = "interactive-local-user"
    session_ref = str(_uuid_mod.uuid4())

    prepared = prepare_telos_pending_request(
        digest=digest,
        action=action,
        surface=surface,
        actor_ref=actor_ref,
        session_ref=session_ref,
        ttl_seconds=3600,
        organism_root=org_root,
    )

    if prepared["status"] != "ok":
        return {
            "schema_version": 1,
            "action": action_name,
            "status": prepared["status"],
            "request_id": prepared.get("request_id"),
            "message": prepared.get("message", ""),
        }

    pf = prepared["prompt_fields"]
    request_id = prepared["request_id"]

    prompt = TelosApprovalPrompt(
        request_id=pf["request_id"],
        organism_id=pf["organism_id"],
        telos_digest=pf["telos_digest"],
        action=pf["action"],
        display_nonce=pf["display_nonce"],
        bounded_summary=pf["bounded_summary"],
        host_context_digest=pf["expected_host_context_digest"],
        expires_at=pf.get("expires_at"),
    )

    decision = telos_approval_prompt(prompt, timeout=120)

    decision_ctx = HostApprovalContext(
        surface=surface,
        actor_ref=actor_ref,
        session_ref=session_ref,
        request_id=prompt.request_id,
        telos_digest=pf["telos_digest"],
        action=pf["action"],
        nonce=pf["display_nonce"],
        context_digest=prompt.host_context_digest,
    )

    root = Path(org_root) if org_root else get_organism_home()
    ledger_path = root / "evolution" / "evolution.db"
    ledger = EvolutionLedger(ledger_path)
    store = TelosStore(root)
    try:
        result = perform_telos_transition(ledger, store, decision_ctx, decision.decision)

        if result.status == "approved":
            return {
                "schema_version": 1,
                "action": action_name,
                "status": "approved",
                "request_id": request_id[:8],
            }
        else:
            return {
                "schema_version": 1,
                "action": action_name,
                "status": result.status,
                "request_id": request_id[:8],
                "message": result.message,
            }
    except TelosApprovalError as exc:
        return {
            "schema_version": 1,
            "action": action_name,
            "status": "rejected",
            "request_id": request_id[:8] if request_id else None,
            "message": str(exc),
        }
    finally:
        ledger.connection.close()


def _handle_telos_approve_cli(digest: str, org_root: Any = None) -> dict[str, Any]:
    """Public wrapper for ``hermes evolution telos approve <digest>``."""
    return _handle_telos_cli_transition(digest, "activate", org_root)


def _handle_telos_rollback_cli(digest: str, org_root: Any = None) -> dict[str, Any]:
    """Public wrapper for ``hermes evolution telos rollback <digest>``."""
    return _handle_telos_cli_transition(digest, "rollback", org_root)


def _observer_status(org_root: Any = None) -> dict[str, Any]:
    """Bounded observer status read."""
    from pathlib import Path
    from .organism_home import get_organism_home

    root = Path(org_root) if org_root else get_organism_home()
    try:
        from .observer_service import ObserverService
        svc = ObserverService(root)
        return {
            "schema_version": 1,
            "action": "observer_status",
            "circuit_open": svc.circuit_open,
            "degraded_reason": svc.degraded_reason,
        }
    except Exception:
        return {
            "schema_version": 1,
            "action": "observer_status",
            "status": "error",
            "message": "observer not available",
        }


def _observer_scan(org_root: Any = None) -> dict[str, Any]:
    """Bounded observer scan — calls ``scan_and_update_suggestions``."""
    from pathlib import Path
    from .organism_home import get_organism_home

    root = Path(org_root) if org_root else get_organism_home()
    try:
        from .observer_service import ObserverService
        svc = ObserverService(root)
        if svc.circuit_open:
            return {
                "schema_version": 1,
                "action": "observer_scan",
                "status": "degraded",
                "reason": str(svc.degraded_reason) if svc.degraded_reason else "circuit_open",
                "count": 0,
            }
        suggestions = svc.scan_and_update_suggestions(max_events=1000)
        return {
            "schema_version": 1,
            "action": "observer_scan",
            "status": "completed",
            "count": len(suggestions),
        }
    except Exception:
        return {
            "schema_version": 1,
            "action": "observer_scan",
            "status": "unsupported",
            "message": "observer scan not available",
        }


def _suggestions_list(org_root: Any = None) -> dict[str, Any]:
    """List current suggestions from the observer."""
    from pathlib import Path
    from .organism_home import get_organism_home

    root = Path(org_root) if org_root else get_organism_home()
    db_path = root / "evolution" / "evolution.db"

    if not db_path.exists():
        return {"schema_version": 1, "action": "suggestions", "error": "no_ledger", "items": []}

    try:
        from .ledger import EvolutionLedger
        ledger = EvolutionLedger(db_path)
        rows = ledger.connection.execute(
            "SELECT suggestion_id, opportunity_key, state, score, summary_reason FROM opportunity_suggestions ORDER BY score DESC LIMIT 20"
        ).fetchall()
        items = [{"id": r["suggestion_id"], "key": r["opportunity_key"], "state": r["state"], "score": r["score"], "reason": r["summary_reason"]} for r in rows]
        ledger.connection.close()
        return {"schema_version": 1, "action": "suggestions", "count": len(items), "items": items}
    except Exception:
        return {"schema_version": 1, "action": "suggestions", "error": "ledger_error", "items": []}
