"""Local Hades note-quality classification and backfill preview helpers."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


BEGIN_MARKER = "---BEGIN_CONTENT---"
END_MARKER = "---END_CONTENT---"
MAX_NOTE_BYTES = 1_000_000
MAX_ROUTES_PER_FACT = 50
ROUTE_HANDLER_RE = re.compile(r"`route:(?P<route>[^`]+)`\s+--handled_by-->\s+`file:(?P<handler>[^`]+)`")
RAW_SCHEMA_MARKERS = ("file_chunk", "source_chunk", "backend_wiki.file_chunk")


def read_note_preview(path: str | Path, *, max_bytes: int = MAX_NOTE_BYTES) -> tuple[str, bool]:
    candidate = Path(path).expanduser()
    with candidate.open("rb") as handle:
        raw = handle.read(max_bytes + 1)
    truncated = len(raw) > max_bytes
    return raw[:max_bytes].decode("utf-8", errors="replace"), truncated


def analyze_note_quality(text: str, *, source: str | None = None, truncated: bool = False) -> dict[str, Any]:
    header, content = _split_note(text)
    schema = str(header.get("schema") or "")
    raw_chunk = _is_raw_chunk(schema=schema, text=text)
    route_facts = _route_handler_facts(content, header)
    classification = "raw_chunk" if raw_chunk else ("candidate_fact_note" if route_facts else "unclassified_note")
    quality = _quality_assessment(
        classification=classification,
        raw_chunk=raw_chunk,
        candidate_facts=route_facts,
        provenance=_provenance(header),
        truncated=truncated,
    )
    return {
        "schema": "hades.note_quality.preview.v1",
        "source": source,
        "classification": classification,
        "raw_chunk": raw_chunk,
        "automatic_recall_allowed": not raw_chunk,
        "automatic_recall_reason": quality["automatic_recall_reason"],
        "memory_proposal_ready": False,
        "promotion_state": quality["promotion_state"],
        "quality_score": quality["score"],
        "quality_grade": quality["grade"],
        "quality_issues": quality["issues"],
        "candidate_fact_count": len(route_facts),
        "candidate_facts": route_facts,
        "provenance": _provenance(header),
        "truncated": truncated,
        "actions": _actions(classification, route_facts),
    }


def _split_note(text: str) -> tuple[dict[str, Any], str]:
    before, marker, after = text.partition(BEGIN_MARKER)
    if not marker:
        return ({}, text)
    header = _parse_header(before)
    content = after.partition(END_MARKER)[0]
    return header, content


def _parse_header(value: str) -> dict[str, Any]:
    stripped = value.strip()
    if not stripped:
        return {}
    try:
        parsed = json.loads(stripped)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _is_raw_chunk(*, schema: str, text: str) -> bool:
    lowered = f"{schema}\n{text[:4000]}".lower()
    return any(marker in lowered for marker in RAW_SCHEMA_MARKERS)


def _route_handler_facts(content: str, header: dict[str, Any]) -> list[dict[str, Any]]:
    by_handler: dict[str, list[str]] = {}
    for match in ROUTE_HANDLER_RE.finditer(content):
        route = " ".join(match.group("route").split())
        handler = " ".join(match.group("handler").split())
        if not route or not handler:
            continue
        routes = by_handler.setdefault(handler, [])
        if route not in routes:
            routes.append(route)

    facts: list[dict[str, Any]] = []
    for handler, routes in sorted(by_handler.items()):
        shown_routes = routes[:MAX_ROUTES_PER_FACT]
        truncated = len(routes) > len(shown_routes)
        evidence_ref = _provenance(header)
        facts.append(
            {
                "kind": "route_handler_group",
                "summary": _route_summary(handler, routes),
                "subject": handler,
                "predicate": "handles_routes",
                "objects": shown_routes,
                "object_count": len(routes),
                "truncated": truncated,
                "evidence_ref": evidence_ref,
                "fingerprint": _candidate_fact_fingerprint(
                    kind="route_handler_group",
                    subject=handler,
                    predicate="handles_routes",
                    objects=shown_routes,
                    evidence_ref=evidence_ref,
                ),
                "review_status": "candidate",
            }
        )
    return facts


def _route_summary(handler: str, routes: list[str]) -> str:
    if len(routes) == 1:
        return f"{handler} handles route {routes[0]}."
    prefix = _common_route_prefix(routes)
    prefix_text = f" in the {prefix} family" if prefix else ""
    return f"{handler} handles {len(routes)} routes{prefix_text}."


def _candidate_fact_fingerprint(
    *,
    kind: str,
    subject: str,
    predicate: str,
    objects: list[str],
    evidence_ref: dict[str, Any],
) -> str:
    payload = {
        "kind": kind,
        "subject": subject,
        "predicate": predicate,
        "objects": objects,
        "evidence_ref": evidence_ref,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _common_route_prefix(routes: list[str]) -> str:
    if not routes:
        return ""
    split_routes = [route.split("_") for route in routes]
    first = split_routes[0]
    parts: list[str] = []
    for index, part in enumerate(first):
        if all(index < len(route) and route[index] == part for route in split_routes):
            parts.append(part)
            continue
        break
    return "_".join(parts[:6])


def _provenance(header: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "batch_id",
        "schema",
        "path",
        "chunk_index",
        "chunk_count",
        "file_index",
        "file_count",
        "sha256",
    )
    return {key: header[key] for key in keys if key in header}


def _actions(classification: str, facts: list[dict[str, Any]]) -> list[str]:
    if classification == "raw_chunk" and facts:
        return [
            "Keep the raw chunk out of automatic recall.",
            "Review candidate facts and promote only verified summaries with evidence refs.",
        ]
    if classification == "raw_chunk":
        return [
            "Keep the raw chunk quarantined.",
            "Create structured artifacts or reviewed facts before promotion.",
        ]
    if facts:
        return ["Review candidate facts before creating project memory."]
    return ["No structured facts detected; leave as an unresolved note or add evidence manually."]


def _quality_assessment(
    *,
    classification: str,
    raw_chunk: bool,
    candidate_facts: list[dict[str, Any]],
    provenance: dict[str, Any],
    truncated: bool,
) -> dict[str, Any]:
    score = 35
    issues: list[str] = []
    if raw_chunk:
        score -= 20
        issues.append("raw_chunk_quarantined")
    if candidate_facts:
        score += 35
        issues.append("review_required")
    else:
        score -= 15
        issues.append("no_structured_facts")
    if provenance.get("schema") and provenance.get("sha256"):
        score += 15
    else:
        issues.append("missing_evidence_fingerprint")
    if truncated:
        score -= 20
        issues.append("preview_truncated")

    bounded_score = max(0, min(100, score))
    if raw_chunk and not candidate_facts:
        promotion_state = "quarantine"
    elif candidate_facts:
        promotion_state = "review_candidate"
    else:
        promotion_state = "needs_manual_structuring"

    if bounded_score >= 80:
        grade = "strong"
    elif bounded_score >= 55:
        grade = "reviewable"
    elif bounded_score >= 30:
        grade = "weak"
    else:
        grade = "quarantine"

    recall_reason = "raw chunks are excluded from automatic recall"
    if not raw_chunk:
        recall_reason = (
            "candidate facts require review before promotion"
            if classification == "candidate_fact_note"
            else "freeform notes require manual evidence before promotion"
        )

    return {
        "score": bounded_score,
        "grade": grade,
        "issues": issues,
        "promotion_state": promotion_state,
        "automatic_recall_reason": recall_reason,
    }
