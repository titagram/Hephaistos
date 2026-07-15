"""Bounded CLI actions for reviewing and verifying Hades backend wiki pages."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from hermes_cli.hades_backend_client import redact_secret
from hermes_cli.hades_backend_runtime import client_from_config


WIKI_JSON_MAX_BYTES = 256_000
WIKI_CONTENT_MAX_CHARS = 24_000
WIKI_EVIDENCE_MAX_REFS = 80
_DRAFT_FIELDS = (
    "slug",
    "title",
    "page_type",
    "content_markdown",
    "evidence_refs",
)


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


def _bounded_evidence_refs(value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError("wiki evidence must be a JSON list")
    if len(value) > WIKI_EVIDENCE_MAX_REFS:
        raise ValueError(f"wiki evidence exceeds {WIKI_EVIDENCE_MAX_REFS} refs")
    return value


def _draft_payload(path_value: Any) -> dict[str, Any]:
    value = _load_bounded_json(path_value)
    if not isinstance(value, dict):
        raise ValueError("wiki draft must be a JSON object")
    missing = [field for field in _DRAFT_FIELDS if field not in value]
    if missing:
        raise ValueError(f"wiki draft is missing required field: {missing[0]}")
    for field in ("slug", "title", "page_type", "content_markdown"):
        if not isinstance(value[field], str):
            raise ValueError(f"wiki draft field {field} must be a string")
    if not value["slug"].strip() or not value["title"].strip() or not value["page_type"].strip():
        raise ValueError("wiki draft slug, title, and page_type are required")
    if len(value["content_markdown"]) > WIKI_CONTENT_MAX_CHARS:
        raise ValueError(f"wiki draft content exceeds {WIKI_CONTENT_MAX_CHARS:,} characters")
    evidence_refs = _bounded_evidence_refs(value["evidence_refs"])
    return {
        "slug": value["slug"],
        "title": value["title"],
        "page_type": value["page_type"],
        "content_markdown": value["content_markdown"],
        "evidence_refs": evidence_refs,
    }


def _verification_evidence(path_value: Any) -> list[Any]:
    return _bounded_evidence_refs(_load_bounded_json(path_value))


def _current_binding():
    from hermes_cli.hades_backend_cmd import _current_workspace_binding

    _agent, binding = _current_workspace_binding()
    return binding


def _redacted_error(exc: Exception, binding: Any = None) -> str:
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
    page = response.get("page") if isinstance(response.get("page"), dict) else response
    revision = response.get("revision") if isinstance(response.get("revision"), dict) else {}
    if not revision and isinstance(page.get("current_revision"), dict):
        revision = page["current_revision"]
    evidence = revision.get("evidence_refs")
    if not isinstance(evidence, list):
        evidence = page.get("evidence_refs")
    evidence_count = page.get("evidence_count")
    if not isinstance(evidence_count, int):
        evidence_count = len(evidence) if isinstance(evidence, list) else 0
    return {
        "id": page.get("id") or response.get("id") or "unknown",
        "revision_id": page.get("current_revision_id")
        or revision.get("id")
        or response.get("current_revision_id")
        or "unknown",
        "title": page.get("title") or response.get("title") or "untitled",
        "status": page.get("source_status")
        or revision.get("source_status")
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
        draft = _draft_payload(getattr(args, "from_file", None)) if action == "draft" else None
        evidence = (
            _verification_evidence(getattr(args, "evidence_file", None))
            if action == "verify"
            else None
        )
        binding = _current_binding()
        scope = {
            "project_id": binding.project_id,
            "workspace_binding_id": binding.backend_workspace_binding_id,
        }
        client = client_from_config()
        if action == "list":
            limit = int(getattr(args, "limit", 20) or 20)
            if not 1 <= limit <= 50:
                raise ValueError("wiki list limit must be between 1 and 50")
            request = {
                **scope,
                "source_status": str(getattr(args, "status", "") or "").strip() or None,
                "limit": limit,
                "cursor": str(getattr(args, "cursor", "") or "").strip() or None,
            }
            response = client.wiki_pages(**{key: value for key, value in request.items() if value is not None})
        elif action == "show":
            page_id = str(getattr(args, "wiki_page_id", "") or "").strip()
            response = client.wiki_page(page_id, **scope)
        elif action == "draft":
            response = client.create_wiki_draft(**scope, **(draft or {}))
        elif action == "verify":
            page_id = str(getattr(args, "wiki_page_id", "") or "").strip()
            expected_revision = str(getattr(args, "expected_revision", "") or "").strip()
            if not page_id:
                raise ValueError("wiki page id is required")
            if not expected_revision:
                raise ValueError("expected wiki revision id is required")
            response = client.verify_wiki_page(
                page_id,
                **scope,
                expected_current_revision_id=expected_revision,
                evidence_refs=evidence or [],
                verification_note=getattr(args, "note", None),
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
