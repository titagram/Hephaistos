"""Bounded CLI actions for reviewing and verifying Hades backend wiki pages."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from hermes_cli import hades_backend_runtime as runtime
from hermes_cli.hades_backend_client import redact_secret, validate_wiki_page_id


WIKI_JSON_MAX_BYTES = 256_000
WIKI_CONTENT_MAX_CHARS = 24_000
WIKI_EVIDENCE_MAX_REFS = 80
WIKI_EVIDENCE_MAX_CLAIMS_PER_REF = 8
WIKI_EVIDENCE_MAX_TOTAL_CLAIMS = 80
WIKI_EVIDENCE_CLAIM_MAX_CHARS = 500
WIKI_NOTE_MAX_CHARS = 2_000
WIKI_SLUG_MAX_CHARS = 255
WIKI_TITLE_MAX_CHARS = 255
WIKI_PAGE_TYPES = frozenset({"business", "technical", "runbook", "audit"})
_DRAFT_FIELDS = (
    "slug",
    "title",
    "page_type",
    "content_markdown",
)
_DRAFT_EVIDENCE_FIELDS = frozenset(
    {"kind", "schema", "sha256", "hash", "path", "bytes", "raw_source_included"}
)
_VERIFICATION_EVIDENCE_FIELDS = frozenset(
    {"kind", "schema", "sha256", "hash", "path", "claims"}
)
_HEX_SHA256 = re.compile(r"\A[0-9a-fA-F]{64}\Z")
_SLUG = re.compile(r"\A[a-z0-9][a-z0-9/-]*\Z")
_WINDOWS_DRIVE = re.compile(r"\A[A-Za-z]:")
_CONTROL_CHARACTER = re.compile(r"[\x00-\x1F\x7F]")


def _load_bounded_json(path_value: Any) -> Any:
    if not str(path_value or "").strip():
        raise ValueError("wiki JSON file is required")
    path = Path(str(path_value)).expanduser()
    try:
        with path.open("rb") as handle:
            raw = handle.read(WIKI_JSON_MAX_BYTES + 1)
    except OSError:
        raise ValueError("wiki JSON file could not be read") from None
    if len(raw) > WIKI_JSON_MAX_BYTES:
        raise ValueError(f"wiki JSON file exceeds {WIKI_JSON_MAX_BYTES:,} bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("wiki JSON file must be valid UTF-8") from None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"wiki JSON file is invalid at line {exc.lineno}, column {exc.colno}") from None


def _safe_relative_path(value: Any) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 2048
        or value.startswith("/")
        or "\\" in value
        or _WINDOWS_DRIVE.match(value)
        or _CONTROL_CHARACTER.search(value)
    ):
        return False
    return all(segment not in {"", ".", ".."} for segment in value.split("/"))


def _bounded_draft_evidence_refs(value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError("wiki evidence must be a JSON list")
    if len(value) > WIKI_EVIDENCE_MAX_REFS:
        raise ValueError(f"wiki evidence exceeds {WIKI_EVIDENCE_MAX_REFS} refs")
    for index, ref in enumerate(value):
        if not isinstance(ref, dict):
            raise ValueError(f"wiki evidence ref {index} must be a JSON object")
        unsupported = sorted(set(ref) - _DRAFT_EVIDENCE_FIELDS)
        if unsupported:
            raise ValueError(f"wiki evidence ref {index} has unsupported field: {unsupported[0]}")
        kind = ref.get("kind")
        if not isinstance(kind, str) or not kind.strip() or len(kind) > 64:
            raise ValueError(f"wiki evidence ref {index} kind must be a non-empty string of at most 64 characters")
        schema = ref.get("schema")
        if "schema" in ref and (not isinstance(schema, str) or len(schema) > 191):
            raise ValueError(f"wiki evidence ref {index} schema must be a string of at most 191 characters")
        for field in ("sha256", "hash"):
            digest = ref.get(field)
            if field in ref and (not isinstance(digest, str) or _HEX_SHA256.fullmatch(digest) is None):
                raise ValueError(f"wiki evidence ref {index} {field} must be an exact 64-character hexadecimal hash")
        path = ref.get("path")
        if "path" in ref and not _safe_relative_path(path):
            raise ValueError(f"wiki evidence ref {index} path must be a safe relative path")
        byte_count = ref.get("bytes")
        if "bytes" in ref and (
            isinstance(byte_count, bool) or not isinstance(byte_count, int) or byte_count < 0
        ):
            raise ValueError(f"wiki evidence ref {index} bytes must be a non-negative integer")
        raw_source_included = ref.get("raw_source_included")
        if "raw_source_included" in ref and not isinstance(raw_source_included, bool):
            raise ValueError(f"wiki evidence ref {index} raw_source_included must be a boolean")
    return value


def _bounded_verification_evidence_refs(value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError("wiki evidence must be a JSON list")
    if not value:
        raise ValueError("wiki verification requires at least one evidence ref")
    if len(value) > WIKI_EVIDENCE_MAX_REFS:
        raise ValueError(f"wiki evidence exceeds {WIKI_EVIDENCE_MAX_REFS} refs")
    normalized_refs = []
    total_claims = 0
    for index, ref in enumerate(value):
        if not isinstance(ref, dict):
            raise ValueError(f"wiki evidence ref {index} must be a JSON object")
        unsupported = sorted(set(ref) - _VERIFICATION_EVIDENCE_FIELDS)
        if unsupported:
            raise ValueError(f"wiki evidence ref {index} has unsupported field: {unsupported[0]}")
        kind = ref.get("kind")
        if kind not in {"artifact_ref", "file_ref"}:
            raise ValueError(f"wiki evidence ref {index} kind must be artifact_ref or file_ref")
        schema = ref.get("schema")
        if "schema" in ref and (not isinstance(schema, str) or len(schema) > 191):
            raise ValueError(f"wiki evidence ref {index} schema must be a string of at most 191 characters")
        for field in ("sha256", "hash"):
            digest = ref.get(field)
            if field in ref and (not isinstance(digest, str) or _HEX_SHA256.fullmatch(digest) is None):
                raise ValueError(f"wiki evidence ref {index} {field} must be an exact 64-character hexadecimal hash")
        path = ref.get("path")
        if "path" in ref and not _safe_relative_path(path):
            raise ValueError(f"wiki evidence ref {index} path must be a safe relative path")
        if kind == "artifact_ref" and "sha256" not in ref:
            raise ValueError(f"wiki evidence ref {index} artifact_ref requires sha256")
        if kind == "file_ref":
            if "path" not in ref:
                raise ValueError(f"wiki evidence ref {index} file_ref requires path")
            if "hash" not in ref and "sha256" not in ref:
                raise ValueError(f"wiki evidence ref {index} file_ref requires hash or sha256")
        claims = ref.get("claims")
        if (
            not isinstance(claims, list)
            or not 1 <= len(claims) <= WIKI_EVIDENCE_MAX_CLAIMS_PER_REF
        ):
            raise ValueError(
                f"wiki evidence ref {index} claims must contain between 1 and "
                f"{WIKI_EVIDENCE_MAX_CLAIMS_PER_REF} claims"
            )
        normalized_claims = []
        for claim_index, mapping in enumerate(claims):
            if not isinstance(mapping, dict) or set(mapping) != {"claim", "proof"}:
                raise ValueError(
                    f"wiki evidence ref {index} claim {claim_index} must contain "
                    "exactly claim and proof"
                )
            claim = mapping["claim"]
            proof = mapping["proof"]
            if not isinstance(claim, str) or not isinstance(proof, str):
                raise ValueError(
                    f"wiki evidence ref {index} claim {claim_index} claim and proof "
                    "must be strings"
                )
            claim = claim.strip()
            proof = proof.strip()
            if not claim or not proof:
                raise ValueError(
                    f"wiki evidence ref {index} claim {claim_index} claim and proof "
                    "must be non-blank"
                )
            if (
                len(claim) > WIKI_EVIDENCE_CLAIM_MAX_CHARS
                or len(proof) > WIKI_EVIDENCE_CLAIM_MAX_CHARS
            ):
                raise ValueError(
                    f"wiki evidence ref {index} claim {claim_index} claim and proof "
                    f"must be at most {WIKI_EVIDENCE_CLAIM_MAX_CHARS} characters"
                )
            normalized_claims.append({"claim": claim, "proof": proof})
        total_claims += len(normalized_claims)
        if total_claims > WIKI_EVIDENCE_MAX_TOTAL_CLAIMS:
            raise ValueError(
                f"wiki evidence exceeds {WIKI_EVIDENCE_MAX_TOTAL_CLAIMS} total claims"
            )
        normalized_refs.append({**ref, "claims": normalized_claims})
    return normalized_refs


def _draft_payload(path_value: Any) -> dict[str, Any]:
    value = _load_bounded_json(path_value)
    if not isinstance(value, dict):
        raise ValueError("wiki draft must be a JSON object")
    missing = [field for field in _DRAFT_FIELDS if field not in value]
    if missing:
        raise ValueError(f"wiki draft is missing required field: {missing[0]}")
    if "source_status" in value:
        raise ValueError("wiki draft field source_status is prohibited")
    slug = value["slug"]
    if (
        not isinstance(slug, str)
        or not slug
        or len(slug) > WIKI_SLUG_MAX_CHARS
        or _SLUG.fullmatch(slug) is None
    ):
        raise ValueError("wiki draft slug must match the backend slug contract")
    title = value["title"]
    if not isinstance(title, str) or not title.strip() or len(title) > WIKI_TITLE_MAX_CHARS:
        raise ValueError("wiki draft title must be a non-empty string of at most 255 characters")
    page_type = value["page_type"]
    if not isinstance(page_type, str) or page_type not in WIKI_PAGE_TYPES:
        raise ValueError("wiki draft page_type must be business, technical, runbook, or audit")
    content = value["content_markdown"]
    if not isinstance(content, str) or not content.strip():
        raise ValueError("wiki draft content_markdown must be a non-empty string")
    if len(content) > WIKI_CONTENT_MAX_CHARS:
        raise ValueError(f"wiki draft content exceeds {WIKI_CONTENT_MAX_CHARS:,} characters")
    evidence_refs = _bounded_draft_evidence_refs(value.get("evidence_refs", []))
    return {
        "slug": value["slug"],
        "title": value["title"],
        "page_type": value["page_type"],
        "content_markdown": value["content_markdown"],
        "evidence_refs": evidence_refs,
    }


def _verification_evidence(path_value: Any) -> list[Any]:
    return _bounded_verification_evidence_refs(_load_bounded_json(path_value))


def _verification_note(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("wiki verification note must be a string")
    if len(value) > WIKI_NOTE_MAX_CHARS:
        raise ValueError(f"wiki verification note exceeds {WIKI_NOTE_MAX_CHARS:,} characters")
    return value


def _list_limit(value: Any) -> int:
    if value is None:
        return 20
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 50:
        raise ValueError("wiki list limit must be between 1 and 50")
    return value


def _current_agent_binding():
    from hermes_cli.hades_backend_cmd import _current_workspace_scoped_agent_binding

    return _current_workspace_scoped_agent_binding()


def _redacted_error(exc: Exception, binding: Any = None) -> str:
    code = str(getattr(exc, "code", "") or "")
    if code == "wiki_verification_capability_not_allowed":
        return (
            "verification requires verify_project_wiki; ask a project administrator "
            "to grant it and issue a new project-scoped bootstrap token, then re-register "
            "this Hades agent with `hades backend setup`. Existing tokens are not upgraded "
            "automatically"
        )
    if code == "wiki_capability_not_allowed":
        return (
            "wiki drafting requires populate_project_wiki; ask a project administrator "
            "to grant it and issue a new project-scoped bootstrap token, then re-register "
            "this Hades agent with `hades backend setup`. Existing tokens are not upgraded "
            "automatically"
        )
    message = redact_secret(str(exc))
    candidates = [Path.cwd()]
    repo_root = getattr(binding, "repo_root", None)
    if repo_root:
        candidates.append(Path(str(repo_root)))
    for candidate in candidates:
        try:
            absolute = str(candidate.resolve())
        except OSError:
            continue
        if absolute:
            message = message.replace(absolute, "<workspace>")
    return message


def _page_summary(value: Any) -> dict[str, Any]:
    response = value if isinstance(value, dict) else {}
    page = response.get("wiki_page") if isinstance(response.get("wiki_page"), dict) else response
    evidence = page.get("evidence_refs")
    evidence_count = page.get("evidence_count")
    if not isinstance(evidence_count, int):
        evidence_count = len(evidence) if isinstance(evidence, list) else 0
    return {
        "id": page.get("id") or response.get("wiki_page_id") or "unknown",
        "revision_id": page.get("current_revision_id")
        or page.get("revision_id")
        or response.get("wiki_revision_id")
        or "unknown",
        "title": page.get("title") or response.get("title") or "untitled",
        "status": page.get("source_status")
        or response.get("source_status")
        or "unknown",
        "evidence_count": evidence_count,
    }


def _print_page(value: Any, *, heading: str) -> None:
    summary = _page_summary(value)
    print(heading)
    print(f"  Page:     {summary['id']}")
    print(f"  Revision: {summary['revision_id']}")
    print(f"  Title:    {summary['title']}")
    print(f"  Status:   {summary['status']}")
    print(f"  Evidence: {summary['evidence_count']}")


def _print_list(response: Any) -> None:
    payload = response if isinstance(response, dict) else {}
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    print("Hades backend wiki pages")
    if not items:
        print("  No pages")
    for item in items:
        summary = _page_summary(item)
        print(f"  Page: {summary['id']}")
        print(f"    Revision: {summary['revision_id']}")
        print(f"    Title: {summary['title']}")
        print(f"    Status: {summary['status']}")
        print(f"    Evidence: {summary['evidence_count']}")
    next_cursor = payload.get("next_cursor")
    print(f"  Next cursor: {next_cursor if next_cursor else 'none'}")


def run_wiki_action(args: argparse.Namespace) -> int:
    """Run one workspace-scoped backend wiki action."""
    action = str(getattr(args, "wiki_action", "") or "").strip()
    client = None
    binding = None
    try:
        limit = _list_limit(getattr(args, "limit", None)) if action == "list" else None
        draft = _draft_payload(getattr(args, "from_file", None)) if action == "draft" else None
        evidence = (
            _verification_evidence(getattr(args, "evidence_file", None))
            if action == "verify"
            else None
        )
        note = _verification_note(getattr(args, "note", None)) if action == "verify" else None
        page_id = (
            validate_wiki_page_id(getattr(args, "wiki_page_id", ""))
            if action in {"show", "verify"}
            else ""
        )
        expected_revision = str(getattr(args, "expected_revision", "") or "").strip()
        if action == "verify" and not expected_revision:
            raise ValueError("expected wiki revision id is required")
        agent, binding = _current_agent_binding()
        scope = {
            "project_id": binding.project_id,
            "workspace_binding_id": binding.backend_workspace_binding_id,
        }
        client = runtime.client_for_agent(agent)
        if action == "list":
            request = {
                **scope,
                "source_status": str(getattr(args, "status", "") or "").strip() or None,
                "limit": limit,
                "cursor": str(getattr(args, "cursor", "") or "").strip() or None,
            }
            response = client.wiki_pages(**{key: value for key, value in request.items() if value is not None})
        elif action == "show":
            response = client.wiki_page(page_id, **scope)
        elif action == "draft":
            response = client.create_wiki_draft(**scope, **(draft or {}))
        elif action == "verify":
            response = client.verify_wiki_page(
                page_id,
                **scope,
                expected_current_revision_id=expected_revision,
                evidence_refs=evidence or [],
                verification_note=note,
            )
        else:
            raise ValueError("wiki action is required: list, show, draft, or verify")
    except Exception as exc:
        print(
            f"Hades backend wiki {action or 'action'}: {_redacted_error(exc, binding)}",
            file=sys.stderr,
        )
        return 1
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()

    if getattr(args, "json", False):
        print(json.dumps(response, sort_keys=True))
    elif action == "list":
        _print_list(response)
    else:
        _print_page(response, heading=f"Hades backend wiki {action}")
    return 0
