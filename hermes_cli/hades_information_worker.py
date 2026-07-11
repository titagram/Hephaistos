"""Bounded, information-only execution for Persephone peer requests.

Every handler is a direct local read.  This module deliberately exposes no
terminal, browser, delegation, file-write, Git-write, database-write, or
remote mutation primitive.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import sqlite3
import time
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
    record_information_failure,
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
MAX_DIRS_SCANNED = 256
MAX_ENTRIES_SCANNED = 5_000
MAX_AGGREGATE_BYTES = 4_000_000
MAX_SCAN_SECONDS = 2.0
MAX_MEMORY_NODES = 2_000
MAX_MEMORY_DEPTH = 8
MAX_MEMORY_STRING = 4_096
_LOCAL_PATH_RE = re.compile(
    r"(?<![\w])/(?:Users|home|private|tmp|var/folders)/[^\s,'\"}]+"
)
_SECRET_ASSIGN_RE = re.compile(
    r"(?i)([\"']?\b(?:password|passphrase|secret|token|api[_-]?key|access[_-]?key|"
    r"private[_-]?key|authorization|credential|cookie|session|aws_secret_access_key)"
    r"\b[\"']?\s*[=:]\s*)(?:\"[^\"]*\"|'[^']*'|[^\r\n,}\]]+)"
)
_PEM_RE = re.compile(
    r"-----BEGIN [^-]*(?:PRIVATE KEY|CERTIFICATE)-----.*?-----END [^-]+-----",
    re.IGNORECASE | re.DOTALL,
)
_AWS_KEY_RE = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")
_KNOWN_TOKEN_RE = re.compile(
    r"(?i)\b(?:ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"sk-[A-Za-z0-9_-]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|"
    r"Bearer\s+[A-Za-z0-9._~+/-]{10,})\b"
)
_SENSITIVE_KEYS = frozenset(
    {
        "password", "passphrase", "secret", "token", "api_key", "apikey",
        "access_key", "private_key", "authorization", "credential", "credentials",
        "cookie", "session", "client_secret", "aws_secret_access_key",
    }
)
_EXCLUDED_DIRS = frozenset(
    {".git", ".hermes", ".hades", ".codex", ".ssh", "secrets", "credentials", ".venv", "venv", "node_modules", "dist", "build"}
)
_SENSITIVE_EXTENSIONS = frozenset({".pem", ".key", ".p12", ".pfx", ".crt", ".cer"})


@dataclass
class _ReadBudget:
    deadline: float
    dirs: int = 0
    entries: int = 0
    files: int = 0
    bytes_read: int = 0

    @classmethod
    def start(cls) -> "_ReadBudget":
        return cls(deadline=time.monotonic() + MAX_SCAN_SECONDS)

    def available(self) -> bool:
        return (
            time.monotonic() <= self.deadline
            and self.dirs < MAX_DIRS_SCANNED
            and self.entries < MAX_ENTRIES_SCANNED
            and self.files < MAX_FILES_SCANNED
            and self.bytes_read < MAX_AGGREGATE_BYTES
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
    _deny_sensitive_path(Path(clean))
    return target, clean


def _is_sensitive_name(path: Path) -> bool:
    parts = tuple(part.casefold() for part in path.parts)
    if any(part in _EXCLUDED_DIRS for part in parts[:-1]):
        return True
    name = parts[-1] if parts else ""
    stem = Path(name).stem.casefold()
    suffix = Path(name).suffix.casefold()
    return (
        name == ".env"
        or name.startswith(".env.")
        or name in {".netrc", ".npmrc", ".pypirc", "id_rsa", "id_ed25519"}
        or stem in {"credential", "credentials", "secret", "secrets", "token", "tokens", "auth", "oauth", "providers"}
        or suffix in _SENSITIVE_EXTENSIONS
    )


def _deny_sensitive_path(path: Path) -> None:
    if _is_sensitive_name(path):
        raise PolicyDenied("sensitive paths are not available to peer information requests")


def _validate_optional_glob(payload: Mapping[str, Any]) -> None:
    value = payload.get("glob")
    if value is None:
        return
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise PolicyDenied("search glob is not allowed")
    lowered = value.casefold()
    if any(marker in lowered for marker in (".env", "credential", "secret", "token", ".git", ".hermes", ".hades", ".pem", ".key")):
        raise PolicyDenied("sensitive search globs are not allowed")


def _safe_text(path: Path, budget: _ReadBudget | None = None) -> str | None:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            return None
        active = budget or _ReadBudget.start()
        if not active.available() or active.bytes_read + size > MAX_AGGREGATE_BYTES:
            return None
        with path.open("rb") as handle:
            raw = handle.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            return None
        active.files += 1
        active.bytes_read += len(raw)
        if b"\x00" in raw[:8_192]:
            return None
        return raw.decode("utf-8", errors="replace")
    except OSError:
        return None


def _source_files(root: Path, budget: _ReadBudget):
    for current, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        if not budget.available():
            break
        budget.dirs += 1
        safe_dirs: list[str] = []
        for name in dirnames:
            budget.entries += 1
            candidate = Path(current) / name
            if budget.entries >= MAX_ENTRIES_SCANNED:
                break
            if name.casefold() not in _EXCLUDED_DIRS and not candidate.is_symlink():
                safe_dirs.append(name)
        dirnames[:] = safe_dirs
        for name in filenames:
            budget.entries += 1
            if not budget.available():
                return
            path = Path(current) / name
            relative = path.relative_to(root)
            if _is_sensitive_name(relative) or path.is_symlink():
                continue
            yield path, relative.as_posix(), budget


def _source_slice(root: Path, payload: Mapping[str, Any]) -> InformationResponse:
    path, relative = _bounded_path(root, payload.get("path"))
    text = _safe_text(path, _ReadBudget.start())
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
    _validate_optional_glob(payload)
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
    budget = _ReadBudget.start()
    for path, relative, budget in _source_files(root, budget):
        text = _safe_text(path, budget)
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
    truncated = truncated or not budget.available()
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
            if not re.fullmatch(r"refs/[A-Za-z0-9._/-]+", ref):
                uncertainty.append("Git HEAD ref is not safely readable")
                ref_path = None
            else:
                ref_path = (git_dir / ref).resolve()
                try:
                    ref_path.relative_to(git_dir.resolve())
                except ValueError:
                    ref_path = None
            oid = _safe_text(ref_path) if ref_path is not None else None
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
    size_row = conn.execute(
        "SELECT project_id, length(CAST(items AS BLOB)) AS byte_length "
        "FROM memory_cache WHERE workspace_binding_id = ?",
        (request.binding.backend_workspace_binding_id,),
    ).fetchone()
    if size_row is not None and int(size_row["byte_length"] or 0) > MAX_AGGREGATE_BYTES:
        return InformationResponse(
            "Project memory is too large for a bounded peer query.",
            (),
            True,
            ("local cache exceeds the information-read budget",),
        )
    cache = db.get_memory_cache(conn, request.binding.backend_workspace_binding_id)
    if cache is None or cache.project_id != request.envelope.project_id:
        return InformationResponse("Project memory has no cached matches.", (), False, ("local cache may be incomplete",))
    needle = query.casefold()
    evidence: list[dict[str, Any]] = []
    work = {"nodes": 0, "deadline": time.monotonic() + MAX_SCAN_SECONDS}
    for index, item in enumerate(cache.items):
        if index >= MAX_MEMORY_NODES or time.monotonic() > work["deadline"]:
            break
        if _bounded_contains(item, needle, work=work, depth=0, seen=set()):
            evidence.append({"kind": "project_memory", "index": index, "item": item})
            if len(evidence) == MAX_EVIDENCE_ITEMS:
                break
    return InformationResponse(f"Found {len(evidence)} cached project-memory item(s).", tuple(evidence), False, ("results use the local synchronized cache",))


def _bounded_contains(
    value: Any,
    needle: str,
    *,
    work: dict[str, Any],
    depth: int,
    seen: set[int],
) -> bool:
    if depth > MAX_MEMORY_DEPTH or work["nodes"] >= MAX_MEMORY_NODES:
        return False
    if time.monotonic() > work["deadline"]:
        return False
    work["nodes"] += 1
    if isinstance(value, str):
        return needle in value[:MAX_MEMORY_STRING].casefold()
    if value is None or isinstance(value, (bool, int, float)):
        return needle in str(value).casefold()
    identity = id(value)
    if identity in seen:
        return False
    if isinstance(value, Mapping):
        seen.add(identity)
        for index, (key, item) in enumerate(value.items()):
            if index >= MAX_MEMORY_NODES:
                break
            if _bounded_contains(str(key)[:MAX_MEMORY_STRING], needle, work=work, depth=depth + 1, seen=seen):
                return True
            if _bounded_contains(item, needle, work=work, depth=depth + 1, seen=seen):
                return True
        return False
    if isinstance(value, (list, tuple)):
        seen.add(identity)
        return any(
            _bounded_contains(item, needle, work=work, depth=depth + 1, seen=seen)
            for item in value[:MAX_MEMORY_NODES]
        )
    return False


def _redacted(response: InformationResponse, root: Path) -> InformationResponse:
    root_text = str(root)
    seen: set[int] = set()
    nodes = 0

    def redact_text(value: str) -> str:
        without_root = value.replace(root_text, "<workspace>")
        without_paths = _LOCAL_PATH_RE.sub("<redacted-path>", without_root)
        without_pem = _PEM_RE.sub("[REDACTED PRIVATE MATERIAL]", without_paths)
        without_keys = _AWS_KEY_RE.sub("[REDACTED ACCESS KEY]", without_pem)
        without_jwt = _JWT_RE.sub("[REDACTED TOKEN]", without_keys)
        without_tokens = _KNOWN_TOKEN_RE.sub("[REDACTED TOKEN]", without_jwt)
        without_assignments = _SECRET_ASSIGN_RE.sub(r"\1***", without_tokens)
        return redact_secret(without_assignments)[:2_000]

    def sensitive_key(value: Any) -> bool:
        normalized = str(value).strip(" \"'").casefold().replace("-", "_")
        compact = re.sub(r"[^a-z0-9]", "", normalized)
        sensitive_compact = {re.sub(r"[^a-z0-9]", "", key) for key in _SENSITIVE_KEYS}
        return compact in sensitive_compact or normalized in _SENSITIVE_KEYS or any(
            normalized.endswith(f"_{key}") for key in _SENSITIVE_KEYS
        )

    def clean(value: Any, *, depth: int = 0) -> Any:
        nonlocal nodes
        nodes += 1
        if nodes > MAX_MEMORY_NODES or depth > MAX_MEMORY_DEPTH:
            return "[TRUNCATED]"
        if isinstance(value, str):
            return redact_text(value)
        if isinstance(value, Mapping):
            identity = id(value)
            if identity in seen:
                return "[CYCLE]"
            seen.add(identity)
            result: dict[str, Any] = {}
            for index, (key, item) in enumerate(value.items()):
                if index >= 128:
                    result["[TRUNCATED]"] = True
                    break
                clean_key = redact_text(str(key))
                result[clean_key] = "***" if sensitive_key(key) else clean(item, depth=depth + 1)
            return result
        if isinstance(value, (list, tuple)):
            identity = id(value)
            if identity in seen:
                return "[CYCLE]"
            seen.add(identity)
            return [clean(item, depth=depth + 1) for item in value[:128]]
        return value

    summary = clean(response.answer_summary)
    uncertainty = tuple(clean(item) for item in response.residual_uncertainty[:20])
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
    del agent_factory
    timestamp = int(time.time()) if now is None else int(now)
    if envelope.expires_at <= timestamp:
        raise PolicyDenied("information request has expired")
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
    if stored.state != "processing":
        raise PolicyDenied("information request must be durably processing before any read")
    if stored.envelope.expires_at <= int(now):
        transition_message(conn, request_message_id, "expired", now=now)
        return None
    _validate_authority(stored.envelope, binding)
    try:
        response = run_information_request(
            stored.envelope,
            binding=binding,
            connection=conn,
            agent_factory=agent_factory,
            now=now,
        )
        envelope = make_response(
            stored.envelope,
            message_id=response_message_id,
            # O1 exposes no independent reply-binding field; the validated
            # request route is the only non-user-controlled workspace route.
            target_workspace_binding_id=stored.envelope.target_workspace_binding_id,
            payload=response.to_payload(),
            expires_at=min(stored.envelope.expires_at, now + 300),
        )
        return persist_response_for_request(conn, request_message_id, envelope, now=now)
    except PolicyDenied:
        raise
    except Exception:
        record_information_failure(conn, request_message_id, now=now)
        return None


__all__ = [
    "INFORMATION_CAPABILITIES",
    "InformationRequest",
    "InformationResponse",
    "PolicyDenied",
    "execute_stored_information_request",
    "run_information_request",
    "validate_information_capability",
]
