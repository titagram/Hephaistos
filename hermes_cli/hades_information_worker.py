"""Bounded, information-only execution for Persephone peer requests.

Every handler is a direct local read.  This module deliberately exposes no
terminal, browser, delegation, file-write, Git-write, database-write, or
remote mutation primitive.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Callable, Mapping

from hermes_cli import hades_backend_db as db
from hermes_cli.hades_backend_client import redact_secret
from hermes_cli.hades_persephone_messages import (
    AgentMessageEnvelope,
    EffectClass,
    MessageType,
    make_response,
)
from hermes_cli.hades_persephone_store import (
    get_message,
    persist_response_for_request,
    transition_message,
)


INFORMATION_CAPABILITIES = frozenset(
    {
        "source_slice",
        "source_search",
        "symbol_lookup",
        "git_metadata",
        "artifact_metadata",
        "project_memory_search",
    }
)
MAX_RESULT_CHARS = 12_000
MAX_EVIDENCE_ITEMS = 20
MAX_FILES_SCANNED = 2_000
MAX_FILE_BYTES = 1_000_000
_LOCAL_PATH_RE = re.compile(
    r"(?<![\w])/(?:Users|home|private|tmp|var/folders)/[^\s,'\"}]+"
)
_SECRET_ASSIGN_RE = re.compile(
    r"(?i)\b(password|token|secret|authorization|api[_-]?key)=([^\s,'\"}]+)"
)


class PolicyDenied(ValueError):
    """The request is outside the explicit information-only authority."""


@dataclass(frozen=True)
class InformationRequest:
    envelope: AgentMessageEnvelope
    binding: db.WorkspaceBinding


@dataclass(frozen=True)
class InformationResponse:
    answer_summary: str
    evidence_refs: tuple[dict[str, Any], ...]
    truncated: bool
    residual_uncertainty: tuple[str, ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            "answer_summary": self.answer_summary,
            "evidence_refs": list(self.evidence_refs),
            "truncated": self.truncated,
            "residual_uncertainty": list(self.residual_uncertainty),
        }


def validate_information_capability(capability: str) -> str:
    clean = str(capability or "").strip()
    if clean not in INFORMATION_CAPABILITIES:
        raise PolicyDenied("capability is not in the information-only allowlist")
    return clean


def _validate_authority(
    envelope: AgentMessageEnvelope, binding: db.WorkspaceBinding
) -> Path:
    if envelope.message_type != MessageType.INFORMATION_REQUEST:
        raise PolicyDenied("only information requests may execute automatically")
    if envelope.effect != EffectClass.INFORMATION_READ:
        raise PolicyDenied("only information_read requests may execute automatically")
    validate_information_capability(envelope.capability)
    if binding.status != "linked":
        raise PolicyDenied("workspace binding is not linked")
    if binding.project_id != envelope.project_id:
        raise PolicyDenied("request project does not match workspace project")
    if binding.agent_id != envelope.target_agent_id:
        raise PolicyDenied("request target does not match workspace agent")
    if binding.backend_workspace_binding_id != envelope.target_workspace_binding_id:
        raise PolicyDenied("request workspace does not match linked workspace")
    root = Path(binding.repo_root).expanduser().resolve()
    if not root.is_dir():
        raise PolicyDenied("linked workspace is unavailable")
    return root


def _bounded_path(root: Path, raw: Any) -> tuple[Path, str]:
    if not isinstance(raw, str) or not raw.strip():
        raise PolicyDenied("a workspace-relative path is required")
    relative = Path(raw.strip())
    if relative.is_absolute():
        raise PolicyDenied("path must be relative to the workspace")
    target = (root / relative).resolve()
    try:
        clean = target.relative_to(root).as_posix()
    except ValueError:
        raise PolicyDenied("path escapes the linked workspace") from None
    return target, clean


def _safe_text(path: Path) -> str | None:
    try:
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            return None
        raw = path.read_bytes()
        if b"\x00" in raw[:8_192]:
            return None
        return raw.decode("utf-8", errors="replace")
    except OSError:
        return None


def _source_files(root: Path):
    count = 0
    for path in sorted(root.rglob("*")):
        if count >= MAX_FILES_SCANNED:
            break
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if path.is_symlink():
            continue
        if any(part in {".git", ".venv", "venv", "node_modules", "dist", "build"} for part in relative.parts):
            continue
        if path.is_file():
            count += 1
            yield path, relative.as_posix()


def _source_slice(root: Path, payload: Mapping[str, Any]) -> InformationResponse:
    path, relative = _bounded_path(root, payload.get("path"))
    text = _safe_text(path)
    if text is None:
        return InformationResponse("Source file is unavailable.", (), False, ("file could not be read",))
    lines = text.splitlines()
    start = payload.get("start_line", 1)
    end = payload.get("end_line", min(len(lines), 200))
    if isinstance(start, bool) or not isinstance(start, int) or start < 1:
        raise PolicyDenied("start_line must be a positive integer")
    if isinstance(end, bool) or not isinstance(end, int) or end < start:
        raise PolicyDenied("end_line must be at least start_line")
    bounded_end = min(end, start + 199, len(lines))
    content = "\n".join(lines[start - 1 : bounded_end])[:MAX_RESULT_CHARS]
    truncated = end > bounded_end or len(content) >= MAX_RESULT_CHARS
    evidence = ({"path": relative, "start_line": start, "end_line": bounded_end, "content": content},)
    return InformationResponse(
        f"Read source lines {start}-{bounded_end} from {relative}.", evidence, truncated, ()
    )


def _search(root: Path, payload: Mapping[str, Any], *, symbol: bool) -> InformationResponse:
    query = payload.get("symbol" if symbol else "query")
    if not isinstance(query, str) or not query.strip() or len(query) > 256:
        raise PolicyDenied("a bounded non-blank search query is required")
    needle = query.strip()
    pattern = (
        re.compile(rf"^\s*(?:async\s+def|def|class)\s+{re.escape(needle)}\b")
        if symbol
        else None
    )
    evidence: list[dict[str, Any]] = []
    truncated = False
    for path, relative in _source_files(root):
        text = _safe_text(path)
        if text is None:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            matched = bool(pattern.search(line)) if pattern else needle.casefold() in line.casefold()
            if not matched:
                continue
            if len(evidence) >= MAX_EVIDENCE_ITEMS:
                truncated = True
                break
            evidence.append({"path": relative, "line": number, "content": line[:500]})
        if truncated:
            break
    noun = "symbol definition" if symbol else "matching source line"
    suffix = "s" if len(evidence) != 1 else ""
    return InformationResponse(
        f"Found {len(evidence)} {noun}{suffix}.", tuple(evidence), truncated, ()
    )


def _git_metadata(root: Path) -> InformationResponse:
    git_dir = root / ".git"
    if git_dir.is_symlink():
        return InformationResponse(
            "Git metadata is unavailable.",
            (),
            False,
            ("Git directory resolves outside the trusted workspace",),
        )
    head = _safe_text(git_dir / "HEAD")
    evidence: list[dict[str, Any]] = []
    uncertainty: list[str] = []
    if head:
        clean_head = head.strip()
        evidence.append({"kind": "git_head", "value": clean_head})
        if clean_head.startswith("ref: "):
            ref = clean_head[5:]
            ref_path, _ = _bounded_path(root, f".git/{ref}")
            oid = _safe_text(ref_path)
            if oid:
                evidence.append({"kind": "git_commit", "value": oid.strip()})
            else:
                uncertainty.append("commit may be stored in packed refs")
    else:
        uncertainty.append("workspace has no readable Git HEAD")
    return InformationResponse("Read local Git metadata.", tuple(evidence), False, tuple(uncertainty))


def _artifact_metadata(root: Path, payload: Mapping[str, Any]) -> InformationResponse:
    path, relative = _bounded_path(root, payload.get("path"))
    try:
        stat = path.stat()
    except OSError:
        return InformationResponse("Artifact is unavailable.", (), False, ("path could not be inspected",))
    evidence = ({"path": relative, "kind": "directory" if path.is_dir() else "file", "size_bytes": stat.st_size},)
    return InformationResponse(f"Inspected metadata for {relative}.", evidence, False, ())


def _memory_search(
    conn: sqlite3.Connection | None,
    request: InformationRequest,
) -> InformationResponse:
    query = request.envelope.payload.get("query")
    if not isinstance(query, str) or not query.strip() or len(query) > 256:
        raise PolicyDenied("a bounded non-blank memory query is required")
    if conn is None:
        return InformationResponse("Project memory is unavailable.", (), False, ("no local memory store was supplied",))
    cache = db.get_memory_cache(conn, request.binding.backend_workspace_binding_id)
    if cache is None or cache.project_id != request.envelope.project_id:
        return InformationResponse("Project memory has no cached matches.", (), False, ("local cache may be incomplete",))
    needle = query.casefold()
    evidence: list[dict[str, Any]] = []
    for index, item in enumerate(cache.items):
        rendered = str(item)
        if needle in rendered.casefold():
            evidence.append({"kind": "project_memory", "index": index, "item": item})
            if len(evidence) == MAX_EVIDENCE_ITEMS:
                break
    return InformationResponse(f"Found {len(evidence)} cached project-memory item(s).", tuple(evidence), False, ("results use the local synchronized cache",))


def _redacted(response: InformationResponse, root: Path) -> InformationResponse:
    root_text = str(root)

    def clean(value: Any) -> Any:
        if isinstance(value, str):
            without_root = value.replace(root_text, "<workspace>")
            without_paths = _LOCAL_PATH_RE.sub("<redacted-path>", without_root)
            without_assignments = _SECRET_ASSIGN_RE.sub(r"\1=***", without_paths)
            return redact_secret(without_assignments)[:2_000]
        if isinstance(value, dict):
            return {clean(str(key)): clean(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [clean(item) for item in value]
        return value

    summary = clean(response.answer_summary)
    uncertainty = tuple(clean(item) for item in response.residual_uncertainty)
    evidence: list[dict[str, Any]] = []
    truncated = response.truncated or len(response.evidence_refs) > MAX_EVIDENCE_ITEMS
    for raw_item in response.evidence_refs[:MAX_EVIDENCE_ITEMS]:
        item = clean(raw_item)
        candidate = {
            "answer_summary": summary,
            "evidence_refs": [*evidence, item],
            "truncated": truncated,
            "residual_uncertainty": list(uncertainty),
        }
        if len(json.dumps(candidate, ensure_ascii=False).encode("utf-8")) > MAX_RESULT_CHARS:
            truncated = True
            break
        evidence.append(item)
    return InformationResponse(summary, tuple(evidence), truncated, uncertainty)


def run_information_request(
    envelope: AgentMessageEnvelope,
    *,
    binding: db.WorkspaceBinding,
    connection: sqlite3.Connection | None = None,
    agent_factory: Callable[..., object] | None = None,
    now: int | None = None,
) -> InformationResponse:
    """Execute one validated request using only direct information handlers.

    ``agent_factory`` is accepted for dependency compatibility but deliberately
    never invoked: all v1 capabilities have safer structured handlers.
    """
    del agent_factory, now
    root = _validate_authority(envelope, binding)
    request = InformationRequest(envelope=envelope, binding=binding)
    capability = envelope.capability
    if capability == "source_slice":
        response = _source_slice(root, envelope.payload)
    elif capability == "source_search":
        response = _search(root, envelope.payload, symbol=False)
    elif capability == "symbol_lookup":
        response = _search(root, envelope.payload, symbol=True)
    elif capability == "git_metadata":
        response = _git_metadata(root)
    elif capability == "artifact_metadata":
        response = _artifact_metadata(root, envelope.payload)
    else:
        response = _memory_search(connection, request)
    return _redacted(response, root)


def execute_stored_information_request(
    conn: sqlite3.Connection,
    request_message_id: str,
    *,
    binding: db.WorkspaceBinding,
    now: int,
    response_message_id: str,
    agent_factory: Callable[..., object] | None = None,
) -> Any:
    """Execute and atomically link a response to a durable processing request."""
    stored = get_message(conn, request_message_id)
    if stored is None:
        raise KeyError(request_message_id)
    root = _validate_authority(stored.envelope, binding)
    try:
        response = run_information_request(
            stored.envelope,
            binding=binding,
            connection=conn,
            agent_factory=agent_factory,
            now=now,
        )
    except PolicyDenied:
        raise
    except Exception:
        # Operational details can contain local paths or credentials.  The
        # peer only needs a stable failure statement; diagnostics stay local.
        response = _redacted(
            InformationResponse(
                "Information request could not be completed.",
                (),
                False,
                ("local information handler failed",),
            ),
            root,
        )
    transition_message(conn, request_message_id, "processed", now=now)
    envelope = make_response(
        stored.envelope,
        message_id=response_message_id,
        # The v1 wire contract requires a workspace route for workspace-scoped
        # capabilities.  O1 exposes no independent reply-binding field, so the
        # validated request binding is the only non-user-controlled route.
        target_workspace_binding_id=stored.envelope.target_workspace_binding_id,
        payload=response.to_payload(),
        expires_at=min(stored.envelope.expires_at, now + 300),
    )
    return persist_response_for_request(conn, request_message_id, envelope, now=now)


__all__ = [
    "INFORMATION_CAPABILITIES",
    "InformationRequest",
    "InformationResponse",
    "PolicyDenied",
    "execute_stored_information_request",
    "run_information_request",
    "validate_information_capability",
]
