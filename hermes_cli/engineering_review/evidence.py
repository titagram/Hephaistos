"""Stable reviewer-evidence handoff shared by the harness and review skill.

Task 11 must launch the consolidating verifier as a ``reviewer`` subagent with
exactly one ``Hermes-Review-Run`` line followed by exactly one
``Hermes-Review-Plan`` line.  Its final response must contain only
``VERIFIED_FINDINGS_EVIDENCE_MARKER`` followed by a JSON array accepted by
``parse_verified_findings``.  The verifier must also complete at least one
successful tool call; prose before or after the envelope is invalid.

The envelope is structured evidence, not log prose.  Its allowlisted string
fields are serialized without regex redaction so code such as ``api_key`` or
``Authorization`` keeps its meaning.  Unknown fields are rejected instead of
being persisted, which prevents arbitrary credential objects from hitching a
ride in the evidence format.

Task 11's lifecycle owner is the long-lived public Hermes process. It creates
and serves ``ReviewAuthority`` only after the session exists; the internal CLI
is a short-lived proxy and must not create/load runs or invoke the bridge
directly. The reviewer callback commits this envelope in the authority process.
Every run-scoped engine operation executes there against the registered
workspace and its descriptor-backed bundle snapshot. Authority loss or a
platform without safe local-peer and descriptor execution fails closed.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any


REVIEW_RUN_MARKER = "Hermes-Review-Run:"
REVIEW_PLAN_MARKER = "Hermes-Review-Plan:"
VERIFIED_FINDINGS_EVIDENCE_MARKER = "Hermes-Verified-Findings-v1\n"
VERIFIER_HANDOFF_INSTRUCTION = f"""Return exactly one final response in this form:
{VERIFIED_FINDINGS_EVIDENCE_MARKER}<JSON array>
Do not add a Markdown fence or prose before or after the JSON array. Every array
entry must contain exactly: id, severity, title, body, path, quotedCode,
sourceReviewerIds, verification. Complete at least one successful tool call
before returning the envelope."""

_FINDING_KEY_ORDER = (
    "id",
    "severity",
    "title",
    "body",
    "path",
    "quotedCode",
    "sourceReviewerIds",
    "verification",
)
_FINDING_KEYS = frozenset(_FINDING_KEY_ORDER)
_STRING_KEYS = ("id", "title", "body", "path", "quotedCode")
_SEVERITIES = frozenset({"blocker", "high", "medium", "low"})
_VERIFICATIONS = frozenset({"confirmed", "rejected", "uncertain"})


def parse_verified_findings(value: object) -> list[dict[str, Any]]:
    """Validate the stable envelope shape without changing string semantics."""
    if not isinstance(value, list):
        raise ValueError("verified findings must be a JSON array")
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping) or not all(isinstance(key, str) for key in raw):
            raise ValueError(f"verified finding {index} must be an object")
        keys = frozenset(raw)
        if keys != _FINDING_KEYS:
            raise ValueError(f"verified finding {index} has invalid fields")
        for key in _STRING_KEYS:
            if not isinstance(raw[key], str):
                raise ValueError(f"verified finding {index}.{key} must be a string")
        if raw["severity"] not in _SEVERITIES:
            raise ValueError(f"verified finding {index}.severity is invalid")
        if raw["verification"] not in _VERIFICATIONS:
            raise ValueError(f"verified finding {index}.verification is invalid")
        source_ids = raw["sourceReviewerIds"]
        if not isinstance(source_ids, list) or not all(
            isinstance(agent_id, str) for agent_id in source_ids
        ):
            raise ValueError(
                f"verified finding {index}.sourceReviewerIds must be a string array"
            )
        normalized.append({key: raw[key] for key in _FINDING_KEY_ORDER})
    return normalized


def encode_verified_findings(findings: Sequence[Mapping[str, object]]) -> str:
    """Return the canonical, envelope-safe final verifier response."""
    validated = parse_verified_findings(list(findings))
    payload = json.dumps(
        validated,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return VERIFIED_FINDINGS_EVIDENCE_MARKER + payload


def canonical_verified_findings_response(
    value: str, *, sensitive_values: Sequence[str] = ()
) -> str | None:
    """Canonicalize a verifier response, or return ``None`` for ordinary prose."""
    if not value.startswith(VERIFIED_FINDINGS_EVIDENCE_MARKER):
        return None
    raw = value[len(VERIFIED_FINDINGS_EVIDENCE_MARKER) :]
    try:
        parsed = json.loads(
            raw,
            parse_constant=lambda constant: (_ for _ in ()).throw(
                ValueError(f"invalid JSON constant: {constant}")
            ),
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError("verified finding envelope is invalid JSON") from exc
    findings = parse_verified_findings(parsed)
    secrets = sorted(
        {
            secret
            for secret in sensitive_values
            if isinstance(secret, str) and len(secret) >= 4
        },
        key=len,
        reverse=True,
    )
    if secrets:
        for finding in findings:
            for key in _STRING_KEYS:
                text = finding[key]
                for secret in secrets:
                    text = text.replace(secret, "[REDACTED]")
                finding[key] = text
    return encode_verified_findings(findings)
