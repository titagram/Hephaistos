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
import stat
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
MAX_PENDING_DIRS = 128
_LOCAL_PATH_RE = re.compile(
    r"(?<![\w])/(?:Users|home|private|tmp|var/folders)/[^\s,'\"}]+"
)
_XML_ELEMENT_RE = re.compile(
    r"<(?P<tag>(?:[A-Za-z_][A-Za-z0-9_.-]*:)?"
    r"(?P<key>[A-Za-z_][A-Za-z0-9_.-]*))(?P<attrs>\s[^>]*)?>"
    r"(?P<value>.*?)</(?P=tag)>",
    re.IGNORECASE | re.DOTALL,
)
_XML_ATTRIBUTE_RE = re.compile(
    r"(?P<name>(?:[A-Za-z_][A-Za-z0-9_.-]*:)?"
    r"(?P<key>[A-Za-z_][A-Za-z0-9_.-]*))\s*=\s*"
    r"(?P<quote>[\"'])(?P<value>.*?)(?P=quote)",
    re.IGNORECASE | re.DOTALL,
)
_XML_TAG_RE = re.compile(
    r"<(?P<tag>(?:[A-Za-z_][A-Za-z0-9_.-]*:)?[A-Za-z_][A-Za-z0-9_.-]*)"
    r"(?P<attrs>\s[^<>]*?)(?P<close>/?)>",
    re.IGNORECASE | re.DOTALL,
)
_PEM_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*(?:PRIVATE KEY|CERTIFICATE)-----.*?"
    r"(?:-----END [A-Z0-9 ]+-----|\Z)",
    re.IGNORECASE | re.DOTALL,
)
_AWS_KEY_RE = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")
_KNOWN_TOKEN_RE = re.compile(
    r"(?i)\b(?:ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"sk-[A-Za-z0-9_-]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|"
    r"AIza[A-Za-z0-9_-]{30,}|ya29\.[A-Za-z0-9._-]{20,}|"
    r"Bearer\s+[A-Za-z0-9._~+/-]{10,})\b"
)
_EXCLUDED_DIRS = frozenset(
    {".git", ".hermes", ".hades", ".codex", ".ssh", ".docker", ".aws", ".azure", ".gcp", "secrets", "credentials", ".venv", "venv", "node_modules", "dist", "build"}
)
_SENSITIVE_EXTENSIONS = frozenset({".pem", ".key", ".p12", ".pfx", ".crt", ".cer"})
_SOURCE_EXTENSIONS = frozenset(
    {
        ".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
        ".go", ".rs", ".java", ".kt", ".scala", ".rb", ".php", ".cs",
        ".c", ".cc", ".cpp", ".h", ".hpp", ".swift", ".sh", ".bash",
        ".zsh", ".fish", ".sql",
    }
)


@dataclass
class _ReadBudget:
    deadline: float
    dirs: int = 0
    entries: int = 0
    files: int = 0
    bytes_read: int = 0
    clipped: bool = False

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
    stem_normalized = re.sub(r"[^a-z0-9]+", "_", stem).strip("_")
    suffix = Path(name).suffix.casefold()
    tokens = tuple(token for token in re.split(r"[^a-z0-9]+", stem) if token)
    cloud_config_path = any(
        parts[index : index + 2] == (".config", "gcloud")
        for index in range(max(0, len(parts) - 1))
    )
    sensitive_directory = any(part in _EXCLUDED_DIRS for part in parts[:-1])
    if suffix in _SOURCE_EXTENSIONS and not sensitive_directory and not cloud_config_path:
        return False
    return (
        cloud_config_path
        or sensitive_directory
        or name == ".env"
        or name.startswith(".env.")
        or name == ".envrc"
        or suffix == ".env"
        or name in {".netrc", ".npmrc", ".pypirc", "id_rsa", "id_ed25519"}
        or "service_account" in stem_normalized
        or ("service" in tokens and "account" in tokens)
        or stem_normalized == "application_default_credentials"
        or any(token in {"credential", "credentials", "secret", "secrets", "token", "tokens"} for token in tokens)
        or stem in {"credential", "credentials", "secret", "secrets", "token", "tokens", "auth", "oauth", "providers"}
        or (
            any(marker in stem_normalized.split("_") for marker in ("auth", "oauth", "provider", "providers"))
            and any(marker in stem_normalized.split("_") for marker in ("config", "store", "credential", "credentials", "token", "tokens"))
        )
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
        if path.is_symlink():
            return None
        active = budget or _ReadBudget.start()
        if not active.available():
            return None
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            file_stat = os.fstat(handle.fileno())
            if not stat.S_ISREG(file_stat.st_mode):
                return None
            size = int(file_stat.st_size)
            if size > MAX_FILE_BYTES or active.bytes_read + size > MAX_AGGREGATE_BYTES:
                active.clipped = True
                return None
            raw = handle.read(MAX_FILE_BYTES + 1)
        if time.monotonic() > active.deadline:
            return None
        if len(raw) > MAX_FILE_BYTES:
            active.clipped = True
            return None
        active.files += 1
        active.bytes_read += len(raw)
        if b"\x00" in raw[:8_192]:
            return None
        return raw.decode("utf-8", errors="replace")
    except OSError:
        return None


def _source_files(root: Path, budget: _ReadBudget):
    pending = [root]
    while pending and budget.available():
        current = pending.pop()
        budget.dirs += 1
        try:
            scanner = os.scandir(current)
            with scanner as entries:
                iterator = iter(entries)
                while budget.available():
                    try:
                        entry = next(iterator)
                    except StopIteration:
                        break
                    budget.entries += 1
                    relative = Path(entry.path).relative_to(root)
                    if entry.is_symlink() or _is_sensitive_name(relative):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if (
                            entry.name.casefold() not in _EXCLUDED_DIRS
                            and len(pending) < MAX_PENDING_DIRS
                        ):
                            pending.append(Path(entry.path))
                        elif entry.name.casefold() not in _EXCLUDED_DIRS:
                            budget.clipped = True
                        continue
                    if entry.is_file(follow_symlinks=False):
                        yield Path(entry.path), relative.as_posix(), budget
        except OSError:
            budget.clipped = True
            continue


def _source_slice(root: Path, payload: Mapping[str, Any]) -> InformationResponse:
    path, relative = _bounded_path(root, payload.get("path"))
    budget = _ReadBudget.start()
    text = _safe_text(path, budget)
    if text is None:
        return InformationResponse(
            "Source file is unavailable.",
            (),
            budget.clipped or not budget.available(),
            ("file could not be read",),
        )
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
    truncated = truncated or budget.clipped or not budget.available()
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
    work = {
        "nodes": 0,
        "deadline": time.monotonic() + MAX_SCAN_SECONDS,
        "clipped": False,
    }
    for index, item in enumerate(cache.items):
        if index >= MAX_MEMORY_NODES or time.monotonic() > work["deadline"]:
            work["clipped"] = True
            break
        if _bounded_contains(item, needle, work=work, depth=0, seen=set()):
            evidence.append({"kind": "project_memory", "index": index, "item": item})
            if len(evidence) == MAX_EVIDENCE_ITEMS:
                if index + 1 < len(cache.items):
                    work["clipped"] = True
                break
    return InformationResponse(
        f"Found {len(evidence)} cached project-memory item(s).",
        tuple(evidence),
        bool(work["clipped"]),
        ("results use the local synchronized cache",),
    )


def _bounded_contains(
    value: Any,
    needle: str,
    *,
    work: dict[str, Any],
    depth: int,
    seen: set[int],
) -> bool:
    if depth > MAX_MEMORY_DEPTH or work["nodes"] >= MAX_MEMORY_NODES:
        work["clipped"] = True
        return False
    if time.monotonic() > work["deadline"]:
        work["clipped"] = True
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
                work["clipped"] = True
                break
            if _bounded_contains(str(key)[:MAX_MEMORY_STRING], needle, work=work, depth=depth + 1, seen=seen):
                return True
            if _bounded_contains(item, needle, work=work, depth=depth + 1, seen=seen):
                return True
        return False
    if isinstance(value, (list, tuple)):
        seen.add(identity)
        if len(value) > MAX_MEMORY_NODES:
            work["clipped"] = True
        return any(
            _bounded_contains(item, needle, work=work, depth=depth + 1, seen=seen)
            for item in value[:MAX_MEMORY_NODES]
        )
    return False


def _is_sensitive_key(value: Any) -> bool:
    raw = str(value).strip(" \"'")
    separated = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", raw)
    separated = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", separated)
    normalized = separated.casefold()
    tokens = tuple(token for token in re.split(r"[^a-z0-9]+", normalized) if token)
    compact = "".join(tokens)
    token_metadata = {
        "count", "counts", "usage", "used", "budget", "budgets", "limit",
        "limits", "total", "remaining", "estimate", "estimated", "cost",
    }
    password_metadata = {
        "policy", "policies", "rule", "rules", "requirement", "requirements",
        "validation", "validator", "length", "minimum", "maximum", "min", "max",
        "count", "counts",
    }
    credential_qualifiers = {
        "api", "access", "refresh", "auth", "authorization", "provider", "client",
        "private", "secret", "bearer",
    }
    if compact.startswith("tokenizer"):
        return False
    if (
        any(token in {"token", "tokens"} for token in tokens)
        and any(token in token_metadata for token in tokens)
        and not any(token in credential_qualifiers for token in tokens)
    ):
        return False
    password_qualifiers = {
        "secret", "token", "key", "keys", "api", "access", "private",
        "credential", "credentials", "auth", "authorization",
    }
    if (
        "password" in tokens
        and any(token in password_metadata for token in tokens)
        and not any(token in password_qualifiers for token in tokens)
    ):
        return False
    session_cookie_qualifiers = {
        "token", "value", "secret", "key", "keys", "auth", "authorization",
        "credential", "credentials", "api", "access", "private",
    }
    if (
        any(token in {"session", "cookie"} for token in tokens)
        and any(token in token_metadata for token in tokens)
        and not any(token in session_cookie_qualifiers for token in tokens)
    ):
        return False
    key_qualifiers = {
        "credential", "credentials", "private", "api", "access", "signing",
        "encryption", "secret", "provider", "password", "token", "auth",
        "authorization",
    }
    if (
        any(token in {"key", "keys"} for token in tokens)
        and not any(token in key_qualifiers for token in tokens)
    ):
        return False
    sensitive_tokens = {
        "password", "passphrase", "secret", "token", "authorization",
        "credential", "credentials", "cookie", "session", "jwt", "bearer",
        "passwords", "passphrases", "secrets", "tokens", "keys",
    }
    sensitive_compounds = {
        "apikey", "accesskey", "privatekey", "clientsecret",
        "awssecretaccesskey", "applicationdefaultcredentials",
    }
    return (
        any(token in sensitive_tokens for token in tokens)
        or any(compound in compact for compound in sensitive_compounds)
    )


def _redact_key_value_pairs(value: str) -> tuple[str, bool]:
    """Lexically redact bounded object/assignment text without skipping containers."""
    replacements: list[tuple[int, int, str]] = []
    deadline = time.monotonic() + MAX_SCAN_SECONDS
    work = 0
    clipped = False

    def quoted_end(start: int, end: int) -> int:
        nonlocal work, clipped
        quote = value[start]
        cursor = start + 1
        escaped = False
        while cursor < end:
            work += 1
            if work > MAX_RESULT_CHARS * 4 or time.monotonic() > deadline:
                clipped = True
                return end
            char = value[cursor]
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                return cursor + 1
            cursor += 1
        return end

    def container_end(start: int, end: int) -> int:
        nonlocal work, clipped
        pairs = {"{": "}", "[": "]"}
        stack = [pairs[value[start]]]
        cursor = start + 1
        while cursor < end and stack:
            work += 1
            if work > MAX_RESULT_CHARS * 4 or time.monotonic() > deadline:
                clipped = True
                return end
            char = value[cursor]
            if char in "\"'":
                cursor = quoted_end(cursor, end)
                continue
            if char in pairs:
                stack.append(pairs[char])
            elif char == stack[-1]:
                stack.pop()
            cursor += 1
        if stack:
            clipped = True
            return end
        return cursor

    def rhs_end(start: int, end: int) -> int:
        """Find a true top-level RHS boundary, conservatively consuming ambiguity."""
        nonlocal work, clipped
        pairs = {"(": ")", "[": "]", "{": "}"}
        stack: list[str] = []
        cursor = start
        while cursor < end:
            work += 1
            if work > MAX_RESULT_CHARS * 4 or time.monotonic() > deadline:
                clipped = True
                return end
            char = value[cursor]
            if char in "\"'":
                cursor = quoted_end(cursor, end)
                continue
            if char in pairs:
                stack.append(pairs[char])
                cursor += 1
                continue
            if char in ")]}":
                if stack and char == stack[-1]:
                    stack.pop()
                    cursor += 1
                    continue
                if not stack:
                    return cursor
                clipped = True
                return end
            if not stack and char in ",;\r\n":
                return cursor
            cursor += 1
        if stack:
            clipped = True
        return end

    def scan(start: int, end: int, *, force: bool = False, depth: int = 0) -> None:
        nonlocal work, clipped
        if depth > MAX_MEMORY_DEPTH:
            replacements.append((start, end, "[TRUNCATED]"))
            clipped = True
            return
        cursor = start
        while cursor < end:
            work += 1
            if work > MAX_RESULT_CHARS * 4 or time.monotonic() > deadline:
                replacements.append((cursor, end, "[TRUNCATED]"))
                clipped = True
                return
            char = value[cursor]
            if char == "[":
                probe = cursor + 1
                while probe < end and value[probe].isspace():
                    probe += 1
                if probe >= end or value[probe] not in "\"'":
                    cursor += 1
                    continue
                quoted_key_end = quoted_end(probe, end)
                key = value[probe + 1 : max(probe + 1, quoted_key_end - 1)]
                after = quoted_key_end
                while after < end and value[after].isspace():
                    after += 1
                if after >= end or value[after] != "]":
                    cursor = quoted_key_end
                    continue
                key_end = after + 1
                after = key_end
            elif char in "\"'":
                key_end = quoted_end(cursor, end)
                key = value[cursor + 1 : max(cursor + 1, key_end - 1)]
                after = key_end
            elif char.isalpha() or char == "_":
                key_end = cursor + 1
                while key_end < end and (
                    value[key_end].isalnum() or value[key_end] in "_.-"
                ):
                    key_end += 1
                key = value[cursor:key_end]
                after = key_end
            else:
                cursor += 1
                continue
            while after < end and value[after].isspace():
                after += 1
            if after >= end or value[after] not in ":=":
                cursor = key_end
                continue
            scalar_start = after + 1
            while scalar_start < end and value[scalar_start].isspace():
                scalar_start += 1
            sensitive = force or _is_sensitive_key(key)
            if scalar_start >= end:
                return
            if value[scalar_start] in "[{":
                close = container_end(scalar_start, end)
                if sensitive:
                    replacements.append((scalar_start, close, '"***"'))
                else:
                    scan(
                        scalar_start + 1,
                        max(scalar_start + 1, close - 1),
                        force=False,
                        depth=depth + 1,
                    )
                cursor = close
                continue
            if not sensitive:
                cursor = scalar_start
                continue
            if value[scalar_start] in "\"'":
                scalar_end = quoted_end(scalar_start, end)
                replacements.append((scalar_start + 1, max(scalar_start + 1, scalar_end - 1), "***"))
                cursor = scalar_end
                continue
            scalar_end = rhs_end(scalar_start, end)
            trimmed_end = scalar_end
            while trimmed_end > scalar_start and value[trimmed_end - 1].isspace():
                trimmed_end -= 1
            replacements.append((scalar_start, trimmed_end, "***"))
            cursor = scalar_end

    scan(0, len(value))
    rendered = value
    for start, end, replacement in sorted(replacements, reverse=True):
        rendered = rendered[:start] + replacement + rendered[end:]
    return rendered, clipped


def _redact_xml_elements(value: str) -> str:
    def semantic_attribute(attrs: str) -> bool:
        for attribute in _XML_ATTRIBUTE_RE.finditer(attrs):
            local_name = attribute.group("key").casefold()
            if local_name in {"name", "key", "id", "type"} and _is_sensitive_key(
                attribute.group("value")
            ):
                return True
        return False

    def replace(match: re.Match[str]) -> str:
        attrs = match.group("attrs") or ""
        if not (
            _is_sensitive_key(match.group("key"))
            or semantic_attribute(attrs)
        ):
            return match.group(0)
        tag = match.group("tag")
        content = match.group("value")
        replacement = "<![CDATA[***]]>" if content.startswith("<![CDATA[") else "***"
        return f"<{tag}{attrs}>{replacement}</{tag}>"

    elements = _XML_ELEMENT_RE.sub(replace, value)

    def replace_semantic_tag(match: re.Match[str]) -> str:
        attrs = match.group("attrs") or ""
        if not semantic_attribute(attrs):
            return match.group(0)

        def redact_value(attribute: re.Match[str]) -> str:
            local_name = attribute.group("key").casefold()
            if local_name not in {
                "value", "defaultvalue", "default", "text", "content"
            }:
                return attribute.group(0)
            quote = attribute.group("quote")
            return f"{attribute.group('name')}={quote}***{quote}"

        cleaned_attrs = _XML_ATTRIBUTE_RE.sub(redact_value, attrs)
        return f"<{match.group('tag')}{cleaned_attrs}{match.group('close')}>"

    elements = _XML_TAG_RE.sub(replace_semantic_tag, elements)

    def replace_attribute(match: re.Match[str]) -> str:
        if not _is_sensitive_key(match.group("key")):
            return match.group(0)
        quote = match.group("quote")
        return f"{match.group('name')}={quote}***{quote}"

    return _XML_ATTRIBUTE_RE.sub(replace_attribute, elements)


def _redacted(response: InformationResponse, root: Path) -> InformationResponse:
    root_text = str(root)
    seen: set[int] = set()
    nodes = 0
    clipped = False
    redacted = False
    lexical_uncertain = False
    deadline = time.monotonic() + MAX_SCAN_SECONDS

    def redact_plain_text(value: str) -> str:
        nonlocal clipped, redacted, lexical_uncertain
        working = value
        if len(working) > MAX_RESULT_CHARS:
            clipped = True
            working = working[:MAX_RESULT_CHARS]
        without_root = working.replace(root_text, "<workspace>")
        without_paths = _LOCAL_PATH_RE.sub("<redacted-path>", without_root)
        without_pem = _PEM_RE.sub("[REDACTED PRIVATE MATERIAL]", without_paths)
        without_keys = _AWS_KEY_RE.sub("[REDACTED ACCESS KEY]", without_pem)
        without_jwt = _JWT_RE.sub("[REDACTED TOKEN]", without_keys)
        without_tokens = _KNOWN_TOKEN_RE.sub("[REDACTED TOKEN]", without_jwt)
        without_pairs, lexical_clip = _redact_key_value_pairs(without_tokens)
        if lexical_clip:
            clipped = True
            lexical_uncertain = True
        without_xml = _redact_xml_elements(without_pairs)
        safe = redact_secret(without_xml)
        if safe != working:
            redacted = True
        if len(safe) > 2_000:
            clipped = True
        return safe[:2_000]

    def clean_json(value: Any, *, depth: int = 0) -> Any:
        nonlocal clipped, redacted
        if depth > MAX_MEMORY_DEPTH or time.monotonic() > deadline:
            clipped = True
            return "[TRUNCATED]"
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for index, (key, item) in enumerate(value.items()):
                if index >= 128:
                    clipped = True
                    break
                if _is_sensitive_key(key):
                    redacted = True
                    result[str(key)] = "***"
                else:
                    result[str(key)] = clean_json(item, depth=depth + 1)
            return result
        if isinstance(value, list):
            if len(value) > 128:
                clipped = True
            return [clean_json(item, depth=depth + 1) for item in value[:128]]
        if isinstance(value, str):
            return redact_plain_text(value)
        return value

    def redact_text(value: str) -> str:
        if len(value) > MAX_RESULT_CHARS:
            return redact_plain_text(value)
        stripped = value.strip()
        if stripped.startswith(("{", "[")):
            try:
                parsed = json.loads(value)
            except (TypeError, ValueError):
                pass
            else:
                rendered = json.dumps(
                    clean_json(parsed), ensure_ascii=False, separators=(",", ":")
                )
                return redact_plain_text(rendered)
        return redact_plain_text(value)

    def clean(value: Any, *, depth: int = 0) -> Any:
        nonlocal nodes, clipped, redacted
        nodes += 1
        if (
            nodes > MAX_MEMORY_NODES
            or depth > MAX_MEMORY_DEPTH
            or time.monotonic() > deadline
        ):
            clipped = True
            return "[TRUNCATED]"
        if isinstance(value, str):
            return redact_text(value)
        if isinstance(value, Mapping):
            identity = id(value)
            if identity in seen:
                clipped = True
                return "[CYCLE]"
            seen.add(identity)
            result: dict[str, Any] = {}
            for index, (key, item) in enumerate(value.items()):
                if index >= 128:
                    clipped = True
                    result["[TRUNCATED]"] = True
                    break
                clean_key = redact_text(str(key))
                if _is_sensitive_key(key):
                    redacted = True
                    result[clean_key] = "***"
                else:
                    result[clean_key] = clean(item, depth=depth + 1)
            return result
        if isinstance(value, (list, tuple)):
            identity = id(value)
            if identity in seen:
                clipped = True
                return "[CYCLE]"
            seen.add(identity)
            if len(value) > 128:
                clipped = True
            return [clean(item, depth=depth + 1) for item in value[:128]]
        return value

    summary = clean(response.answer_summary)
    if len(response.residual_uncertainty) > 20:
        clipped = True
    uncertainty = [clean(item) for item in response.residual_uncertainty[:20]]
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
    if redacted and "sensitive values were redacted" not in uncertainty:
        if len(uncertainty) < 20:
            uncertainty.append("sensitive values were redacted")
        else:
            clipped = True
    if lexical_uncertain and "lexical evidence was conservatively truncated" not in uncertainty:
        if len(uncertainty) < 20:
            uncertainty.append("lexical evidence was conservatively truncated")
        else:
            clipped = True
    while evidence:
        final_payload = {
            "answer_summary": summary,
            "evidence_refs": evidence,
            "truncated": truncated or clipped,
            "residual_uncertainty": uncertainty,
        }
        if (
            len(json.dumps(final_payload, ensure_ascii=False).encode("utf-8"))
            <= MAX_RESULT_CHARS
        ):
            break
        evidence.pop()
        clipped = True
    return InformationResponse(
        summary, tuple(evidence), truncated or clipped, tuple(uncertainty)
    )


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
