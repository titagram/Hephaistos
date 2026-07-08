"""Causal evidence-pack helpers for Hades source-free diagnosis."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import time
from typing import Any


def build_causal_pack(payload: Mapping[str, Any]) -> dict[str, Any]:
    freshness = _mapping(payload.get("freshness"))
    awareness = _mapping(payload.get("awareness"))
    diagnosis = _mapping(payload.get("diagnosis"))
    evidence_refs = _string_list(payload.get("evidence_refs"))
    graph_refs = _string_list(payload.get("graph_refs"))
    source_slice_refs = _string_list(payload.get("source_slice_refs"))
    pack = {
        "schema": "hades.causal_pack.v1",
        "project_id": str(payload.get("project_id") or ""),
        "binding_id": str(payload.get("binding_id") or ""),
        "bug_id": str(payload.get("bug_id") or ""),
        "root_cause_id": str(diagnosis.get("root_cause_id") or ""),
        "bug_class": str(diagnosis.get("bug_class") or ""),
        "failure_classification": str(diagnosis.get("failure_classification") or ""),
        "affected_refs": _string_list(diagnosis.get("affected_refs")),
        "freshness": dict(freshness),
        "awareness": {"diagnosable_without_source": bool(awareness.get("diagnosable_without_source"))},
        "evidence_refs": evidence_refs,
        "graph_refs": graph_refs,
        "source_slice_refs": source_slice_refs,
        "replay": {"required_refs": evidence_refs + graph_refs + source_slice_refs},
        "created_at": int(payload.get("created_at") or time.time()),
    }
    validation = validate_causal_pack(pack)
    pack["status"] = validation["status"]
    pack["blockers"] = validation["blockers"]
    pack["pack_key"] = causal_pack_key(pack)
    return pack


def validate_causal_pack(pack: Mapping[str, Any]) -> dict[str, Any]:
    blockers: list[str] = []
    if _mapping(pack.get("freshness")).get("status") != "current":
        blockers.append("freshness_not_current")
    if not _mapping(pack.get("awareness")).get("diagnosable_without_source"):
        blockers.append("awareness_not_diagnosable")
    if not _string_list(pack.get("evidence_refs")):
        blockers.append("evidence_refs_required")
    if not _string_list(pack.get("graph_refs")):
        blockers.append("graph_refs_required")
    if not _string_list(pack.get("source_slice_refs")):
        blockers.append("source_slice_refs_required")
    for field in ("root_cause_id", "bug_class", "failure_classification"):
        if not str(pack.get(field) or "").strip():
            blockers.append(f"{field}_required")
    return {"status": "valid" if not blockers else "invalid", "blockers": blockers}


def causal_pack_key(pack: Mapping[str, Any]) -> str:
    normalized = {
        "project_id": str(pack.get("project_id") or ""),
        "binding_id": str(pack.get("binding_id") or ""),
        "bug_id": str(pack.get("bug_id") or ""),
        "root_cause_id": str(pack.get("root_cause_id") or ""),
        "required_refs": sorted(_string_list(_mapping(pack.get("replay")).get("required_refs"))),
        "head_commit": str(_mapping(pack.get("freshness")).get("head_commit") or ""),
    }
    raw = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list | tuple):
        return []
    return [str(item) for item in value if str(item or "").strip()]
