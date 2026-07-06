"""Hades backend shared-memory provider."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List

from agent.memory_provider import MemoryProvider
from hermes_cli import hades_backend_db as db
from hermes_cli import hades_backend_runtime as runtime
from hermes_cli.hades_backend_client import redact_secret
from hermes_cli.hades_backend_sync import run_backend_sync
from tools.registry import tool_error, tool_result


PIGGYBACK_SYNC_INTERVAL_SECONDS = 60
LIVE_SEARCH_TIMEOUT_SECONDS = 2.0
CREATE_ACTIONS = {"add", "create"}
UPDATE_ACTIONS = {"replace", "update"}
DELETE_ACTIONS = {"remove", "delete"}
AUTO_PREFETCH_LIMIT = 8
TOOL_RESULT_LIMIT = 20
SEARCH_TOOL_NAME = "hades_backend_project_memory_search"
BUG_EVIDENCE_SEARCH_TOOL_NAME = "hades_backend_bug_evidence_search"
PROJECT_AWARENESS_STATUS_TOOL_NAME = "hades_backend_project_awareness_status"
RAW_CHUNK_DOMAINS = {
    "backend_wiki_chunks",
    "chunk",
    "chunks",
    "file_chunk",
    "file_chunks",
    "raw_chunk",
    "raw_chunks",
    "source_chunk",
    "source_chunks",
}
RAW_CHUNK_MARKERS = (
    "file_chunk",
    "source_chunk",
    "backend_wiki.file_chunk",
    "---begin_content---",
)
DOMAIN_ALIASES = {
    "agent_note": "agent_notes",
    "agent-notes": "agent_notes",
    "agent_notes": "agent_notes",
    "artifact": "artifacts",
    "artifacts": "artifacts",
    "log": "logbook",
    "logbook": "logbook",
    "memory": "project_memory",
    "note": "agent_notes",
    "notes": "agent_notes",
    "project": "project_memory",
    "project-memory": "project_memory",
    "project_memory": "project_memory",
    "source": "source_chunks",
    "source-chunks": "source_chunks",
    "source_chunks": "source_chunks",
    "wiki": "wiki",
    "wiki_revision": "wiki",
}
SEARCH_DOMAINS = ("all", "project_memory", "logbook", "wiki", "agent_notes", "source_chunks", "artifacts")
BUG_EVIDENCE_KINDS = (
    "all",
    "stack_trace",
    "log_excerpt",
    "failing_test",
    "http_request",
    "http_response",
    "browser_console",
    "deploy_version",
    "config_snapshot",
    "user_steps",
    "screenshot_ref",
    "other",
)
TOKEN_RE = re.compile(r"[A-Za-z0-9_./:-]{2,}")

SEARCH_TOOL_SCHEMA: Dict[str, Any] = {
    "name": SEARCH_TOOL_NAME,
    "description": (
        "Search the linked Hades backend project memory snapshot cached locally. "
        "Use this for project history, wiki/logbook facts, agent notes, or to "
        "diagnose missing shared-memory context. Raw source/wiki chunks are "
        "excluded unless include_raw_chunks is true."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search text for the project fact, decision, route, file, symbol, or task.",
            },
            "domain": {
                "type": "string",
                "enum": list(SEARCH_DOMAINS),
                "default": "all",
                "description": "Restrict search to a project-brain domain.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": TOOL_RESULT_LIMIT,
                "default": 8,
                "description": "Maximum number of bounded results to return.",
            },
            "include_raw_chunks": {
                "type": "boolean",
                "default": False,
                "description": "Include raw source/wiki chunk entries. Leave false for ordinary recall.",
            },
        },
        "required": ["query"],
    },
}

BUG_EVIDENCE_SEARCH_TOOL_SCHEMA: Dict[str, Any] = {
    "name": BUG_EVIDENCE_SEARCH_TOOL_NAME,
    "description": (
        "Search linked Hades backend bug evidence such as stack traces, log "
        "excerpts, failing tests, HTTP traces, browser console output, deploy "
        "versions, and user reproduction steps. Use this before making precise "
        "root-cause claims about a project bug. Results are live backend data; "
        "there is no local cache fallback for bug evidence."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search text from the symptom, exception, frame, route, test, log, or deploy evidence.",
            },
            "kind": {
                "type": "string",
                "enum": list(BUG_EVIDENCE_KINDS),
                "default": "all",
                "description": "Restrict search to one bug evidence kind.",
            },
            "bug_report_id": {
                "type": "string",
                "description": "Optional Hades bug report id to scope the search.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": TOOL_RESULT_LIMIT,
                "default": 8,
                "description": "Maximum number of bounded evidence results to return.",
            },
        },
        "required": ["query"],
    },
}

PROJECT_AWARENESS_STATUS_TOOL_SCHEMA: Dict[str, Any] = {
    "name": PROJECT_AWARENESS_STATUS_TOOL_NAME,
    "description": (
        "Read the linked Hades backend project-awareness gate for this workspace. "
        "Use this before precise root-cause claims or source-free diagnosis to "
        "check whether memory, indexed artifacts, code graph, source slices, and "
        "bug evidence are current, stale, partial, or missing. Results are live "
        "backend data; there is no local cache fallback."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
}


class HadesBackendMemoryProvider(MemoryProvider):
    def __init__(self) -> None:
        self._binding: db.WorkspaceBinding | None = None
        self._last_sync_at: float | None = None

    @property
    def name(self) -> str:
        return "hades_backend"

    def is_available(self) -> bool:
        agent = runtime.current_agent()
        return bool(agent and agent.capabilities.get("memory", True))

    def initialize(self, session_id: str, **kwargs) -> None:
        self._binding = self._resolve_binding(Path(os.getcwd()))

    def system_prompt_block(self) -> str:
        if self._binding is None:
            return (
                "Hades backend memory is configured, but this working directory "
                "is not linked to a backend project. Shared project memory is "
                "unavailable until the workspace is linked with `hades backend "
                "bootstrap ...` or `hades project link <project>`, followed by "
                "`hades backend sync`."
            )
        return (
            "Shared Hades backend memory is enabled for this linked project. "
            "Use recalled project memory as background context; the Hades "
            "backend remains the authoritative source of shared memory. "
            "Automatic recall is compact and excludes raw source/wiki chunks; "
            "search project memory, bug evidence, or project awareness status "
            "explicitly when exact evidence or diagnosis readiness is needed."
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if self._binding is None:
            return ""
        backend_result, _backend_error = self._backend_memory_search(
            query=query,
            domain="all",
            limit=AUTO_PREFETCH_LIMIT,
            include_raw_chunks=False,
        )
        if backend_result is not None:
            return _format_backend_prefetch(backend_result)
        cache = self._load_memory_cache()
        if cache is None or not cache.items:
            return (
                "Shared Hades project memory: linked, but no synced memory "
                "snapshot is available yet. Run `hades backend sync` to refresh."
            )
        matches, raw_omitted, _total = _search_memory_items(
            cache.items,
            query,
            domain="all",
            limit=AUTO_PREFETCH_LIMIT,
            include_raw_chunks=False,
        )
        if not matches:
            if raw_omitted:
                return (
                    "Shared Hades project memory: no compact auto-recall entries "
                    f"matched this turn; {raw_omitted} raw chunk item(s) were "
                    "excluded from automatic context."
                )
            return ""
        lines = [f"Shared Hades project memory (snapshot {cache.version}):"]
        for score, _idx, _raw, item in matches:
            summary = _compact_text(_item_summary(item), max_chars=520)
            if not summary:
                continue
            domain = _item_domain(item)
            label = f"[{domain}] " if domain else ""
            lines.append(f"- {label}{summary}")
        if raw_omitted:
            lines.append(f"- {raw_omitted} raw chunk item(s) excluded from automatic context.")
        return "\n".join(lines) if len(lines) > 1 else ""

    def _load_memory_cache(self) -> db.MemoryCache | None:
        with db.connect_closing() as conn:
            cache = db.get_memory_cache(conn, self._binding.backend_workspace_binding_id)
        return cache

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: List[Dict[str, Any]] | None = None,
    ) -> None:
        if self._binding is None:
            return None
        now = time.time()
        if (
            self._last_sync_at is not None
            and now - self._last_sync_at < PIGGYBACK_SYNC_INTERVAL_SECONDS
        ):
            return None
        self._last_sync_at = now
        run_backend_sync(quiet=True)
        return None

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [
            SEARCH_TOOL_SCHEMA,
            BUG_EVIDENCE_SEARCH_TOOL_SCHEMA,
            PROJECT_AWARENESS_STATUS_TOOL_SCHEMA,
        ]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        if tool_name == PROJECT_AWARENESS_STATUS_TOOL_NAME:
            return self._handle_project_awareness_status()
        if tool_name == BUG_EVIDENCE_SEARCH_TOOL_NAME:
            return self._handle_bug_evidence_search(args)
        if tool_name != SEARCH_TOOL_NAME:
            return tool_error(f"Unknown Hades backend memory tool: {tool_name}")
        query = str(args.get("query") or "").strip()
        if not query:
            return tool_error("Missing required parameter: query")
        domain = _normalize_domain(args.get("domain") or "all")
        if domain not in SEARCH_DOMAINS:
            return tool_error(f"Unsupported domain: {domain}", allowed_domains=list(SEARCH_DOMAINS))
        limit = _bounded_int(args.get("limit"), default=8, minimum=1, maximum=TOOL_RESULT_LIMIT)
        include_raw_chunks = bool(args.get("include_raw_chunks"))

        if self._binding is None:
            return tool_result(
                {
                    "status": "unmapped_project",
                    "message": (
                        "This working directory is not linked to a Hades backend "
                        "project, so shared project memory is unavailable."
                    ),
                    "actions": [
                        "Run `hades backend bootstrap ...` for a new backend project binding.",
                        "Run `hades project link <project>` from an existing local project.",
                        "Run `hades backend sync` after linking.",
                    ],
                    "items": [],
                }
            )

        backend_result, backend_error = self._backend_memory_search(
            query=query,
            domain=domain,
            limit=limit,
            include_raw_chunks=include_raw_chunks,
        )
        if backend_result is not None:
            return tool_result(_tool_result_from_backend_search(backend_result))

        cache = self._load_memory_cache()
        if cache is None or not cache.items:
            result = {
                "status": "empty_cache",
                "project_id": self._binding.project_id,
                "workspace_binding_id": self._binding.backend_workspace_binding_id,
                "message": "No synced Hades backend memory snapshot is available locally.",
                "actions": ["Run `hades backend sync` to refresh project memory."],
                "items": [],
            }
            if backend_error:
                result["backend_live_error"] = backend_error
            return tool_result(result)

        matches, raw_omitted, total = _search_memory_items(
            cache.items,
            query,
            domain=domain,
            limit=limit,
            include_raw_chunks=include_raw_chunks,
        )
        items = [
            _tool_result_item(item, score=score, raw_chunk=raw)
            for score, _idx, raw, item in matches
        ]
        result = {
            "status": "ok",
            "project_id": self._binding.project_id,
            "workspace_binding_id": self._binding.backend_workspace_binding_id,
            "cache_version": cache.version,
            "cache_updated_at": cache.updated_at,
            "query": query,
            "domain": domain,
            "include_raw_chunks": include_raw_chunks,
            "searched_cache_only": True,
            "count": len(items),
            "candidate_count": total,
            "truncated": total > len(items),
            "raw_chunks_omitted": raw_omitted,
            "items": items,
        }
        if backend_error:
            result["backend_live_error"] = backend_error
        return tool_result(result)

    def _handle_project_awareness_status(self) -> str:
        if self._binding is None:
            return tool_result(
                {
                    "status": "unmapped_project",
                    "message": (
                        "This working directory is not linked to a Hades backend "
                        "project, so project awareness status is unavailable."
                    ),
                    "actions": [
                        "Run `hades backend bootstrap ...` for a new backend project binding.",
                        "Run `hades project link <project>` from an existing local project.",
                        "Run `hades backend sync` after linking.",
                    ],
                }
            )

        backend_result, backend_error = self._backend_project_awareness_status()
        if backend_result is not None:
            return tool_result(_tool_result_from_backend_project_awareness_status(backend_result))

        result = {
            "status": "backend_unavailable",
            "project_id": self._binding.project_id,
            "workspace_binding_id": self._binding.backend_workspace_binding_id,
            "message": "Hades backend project awareness status is unavailable.",
            "actions": ["Run `hades backend status` and `hades backend sync` to diagnose backend connectivity."],
        }
        if backend_error:
            result["backend_live_error"] = backend_error
        return tool_result(result)

    def _handle_bug_evidence_search(self, args: Dict[str, Any]) -> str:
        query = str(args.get("query") or "").strip()
        if not query:
            return tool_error("Missing required parameter: query")
        kind = str(args.get("kind") or "all").strip()
        if kind not in BUG_EVIDENCE_KINDS:
            return tool_error(f"Unsupported bug evidence kind: {kind}", allowed_kinds=list(BUG_EVIDENCE_KINDS))
        limit = _bounded_int(args.get("limit"), default=8, minimum=1, maximum=TOOL_RESULT_LIMIT)
        bug_report_id = str(args.get("bug_report_id") or "").strip()

        if self._binding is None:
            return tool_result(
                {
                    "status": "unmapped_project",
                    "message": (
                        "This working directory is not linked to a Hades backend "
                        "project, so shared bug evidence is unavailable."
                    ),
                    "actions": [
                        "Run `hades backend bootstrap ...` for a new backend project binding.",
                        "Run `hades project link <project>` from an existing local project.",
                        "Run `hades backend sync` after linking.",
                    ],
                    "items": [],
                }
            )

        backend_result, backend_error = self._backend_bug_evidence_search(
            query=query,
            kind="" if kind == "all" else kind,
            bug_report_id=bug_report_id,
            limit=limit,
        )
        if backend_result is not None:
            return tool_result(_tool_result_from_backend_bug_evidence_search(backend_result))

        result = {
            "status": "backend_unavailable",
            "project_id": self._binding.project_id,
            "workspace_binding_id": self._binding.backend_workspace_binding_id,
            "message": "Hades backend bug evidence live search is unavailable.",
            "actions": ["Run `hades backend status` and `hades backend sync` to diagnose backend connectivity."],
            "items": [],
        }
        if backend_error:
            result["backend_live_error"] = backend_error
        return tool_result(result)

    def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
        metadata: Dict[str, Any] | None = None,
    ) -> None:
        if self._binding is None:
            return
        proposal_action = _proposal_action(action)
        if proposal_action is None:
            return
        metadata = dict(metadata or {})
        previous_summary = str(metadata.get("old_text") or "").strip()
        summary = str(content or "").strip() or previous_summary
        if not summary:
            return
        provenance = _proposal_provenance(
            provider=self.name,
            target=target,
            metadata=metadata,
            action=action,
            proposal_action=proposal_action,
            previous_summary=previous_summary,
        )
        with db.connect_closing() as conn:
            db.create_memory_proposal(
                conn,
                project_id=self._binding.project_id,
                workspace_binding_id=self._binding.backend_workspace_binding_id,
                action=proposal_action,
                intent="memory_write",
                summary=summary,
                provenance=provenance,
            )

    def _resolve_binding(self, cwd: Path) -> db.WorkspaceBinding | None:
        try:
            resolved = cwd.resolve()
        except OSError:
            return None
        best: db.WorkspaceBinding | None = None
        with db.connect_closing() as conn:
            for binding in db.list_workspace_bindings(conn, status="linked"):
                root = Path(binding.repo_root)
                try:
                    resolved.relative_to(root)
                except ValueError:
                    continue
                if best is None or len(str(root)) > len(best.repo_root):
                    best = binding
        return best

    def _backend_memory_search(
        self,
        *,
        query: str,
        domain: str,
        limit: int,
        include_raw_chunks: bool,
    ) -> tuple[dict[str, Any] | None, str | None]:
        if self._binding is None:
            return None, None
        try:
            client = runtime.client_from_config(timeout=LIVE_SEARCH_TIMEOUT_SECONDS)
            try:
                response = client.memory_search(
                    project_id=self._binding.project_id,
                    workspace_binding_id=self._binding.backend_workspace_binding_id,
                    query=query,
                    domain=domain,
                    limit=limit,
                    include_raw_chunks=include_raw_chunks,
                )
            finally:
                close = getattr(client, "close", None)
                if callable(close):
                    close()
        except Exception as exc:
            return None, redact_secret(str(exc))
        return response, None

    def _backend_bug_evidence_search(
        self,
        *,
        query: str,
        kind: str,
        bug_report_id: str,
        limit: int,
    ) -> tuple[dict[str, Any] | None, str | None]:
        if self._binding is None:
            return None, None
        try:
            client = runtime.client_from_config(timeout=LIVE_SEARCH_TIMEOUT_SECONDS)
            try:
                response = client.bug_evidence_search(
                    project_id=self._binding.project_id,
                    workspace_binding_id=self._binding.backend_workspace_binding_id,
                    query=query,
                    kind=kind or None,
                    bug_report_id=bug_report_id or None,
                    limit=limit,
                )
            finally:
                close = getattr(client, "close", None)
                if callable(close):
                    close()
        except Exception as exc:
            return None, redact_secret(str(exc))
        return response, None

    def _backend_project_awareness_status(self) -> tuple[dict[str, Any] | None, str | None]:
        if self._binding is None:
            return None, None
        try:
            client = runtime.client_from_config(timeout=LIVE_SEARCH_TIMEOUT_SECONDS)
            try:
                response = client.project_awareness_status(
                    project_id=self._binding.project_id,
                    workspace_binding_id=self._binding.backend_workspace_binding_id,
                )
            finally:
                close = getattr(client, "close", None)
                if callable(close):
                    close()
        except Exception as exc:
            return None, redact_secret(str(exc))
        return response, None


def _proposal_action(action: str) -> str | None:
    normalized = str(action or "").strip().lower()
    if normalized in CREATE_ACTIONS:
        return "create"
    if normalized in UPDATE_ACTIONS:
        return "update"
    if normalized in DELETE_ACTIONS:
        return "delete"
    return None


def _first_metadata_value(metadata: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = metadata.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _proposal_provenance(
    *,
    provider: str,
    target: str,
    metadata: dict[str, Any],
    action: str,
    proposal_action: str,
    previous_summary: str,
) -> dict[str, Any]:
    provenance: dict[str, Any] = {
        "target": target,
        "metadata": metadata,
        "provider": provider,
        "local_action": action,
        "proposal_action": proposal_action,
    }
    memory_id = _first_metadata_value(metadata, ("memory_id", "local_memory_id", "id"))
    base_version = _first_metadata_value(metadata, ("base_version", "etag", "memory_etag", "version"))
    if memory_id:
        provenance["memory_id"] = memory_id
    if base_version:
        provenance["base_version"] = base_version
    if previous_summary:
        provenance["previous_summary"] = previous_summary
    return provenance


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _compact_text(text: Any, *, max_chars: int) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 3].rstrip() + "..."


def _nested_dict(item: dict[str, Any], key: str) -> dict[str, Any]:
    value = item.get(key)
    return value if isinstance(value, dict) else {}


def _first_item_value(item: dict[str, Any], keys: tuple[str, ...]) -> str:
    containers = (
        item,
        _nested_dict(item, "metadata"),
        _nested_dict(item, "payload"),
        _nested_dict(item, "provenance"),
    )
    for container in containers:
        for key in keys:
            value = container.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
    return ""


def _normalize_domain(value: Any) -> str:
    raw = str(value or "").strip().lower().replace(" ", "_")
    if not raw:
        return ""
    return DOMAIN_ALIASES.get(raw, raw)


def _item_domain(item: dict[str, Any]) -> str:
    value = _first_item_value(item, ("domain", "memory_domain", "type", "kind"))
    domain = _normalize_domain(value)
    if domain in RAW_CHUNK_DOMAINS:
        return "source_chunks"
    return domain or "project_memory"


def _item_schema(item: dict[str, Any]) -> str:
    return _first_item_value(item, ("schema", "content_schema", "artifact_schema"))


def _item_summary(item: dict[str, Any]) -> str:
    return _first_item_value(
        item,
        (
            "summary",
            "title",
            "text",
            "content",
            "body",
            "description",
        ),
    )


def _item_source(item: dict[str, Any]) -> str:
    return _first_item_value(item, ("path", "source", "file", "uri", "route", "symbol"))


def _item_id(item: dict[str, Any]) -> str:
    return _first_item_value(item, ("id", "memory_id", "entry_id", "source_hash"))


def _is_raw_chunk_item(item: dict[str, Any]) -> bool:
    domain = _item_domain(item)
    if domain in RAW_CHUNK_DOMAINS or domain == "source_chunks":
        return True
    schema = _item_schema(item).lower()
    if any(marker in schema for marker in RAW_CHUNK_MARKERS):
        return True
    if "chunk_index" in item and ("chunk_count" in item or "path" in item):
        return True
    searchable = _item_search_text(item).lower()
    return any(marker in searchable for marker in RAW_CHUNK_MARKERS)


def _domain_matches(item_domain: str, requested: str) -> bool:
    if requested == "all":
        return True
    return item_domain == requested


def _tokenize(text: str) -> set[str]:
    return {token.lower() for token in TOKEN_RE.findall(text or "")}


def _item_search_text(item: dict[str, Any]) -> str:
    parts = [
        _item_summary(item),
        _item_source(item),
        _item_domain(item),
        _item_schema(item),
    ]
    metadata = item.get("metadata")
    provenance = item.get("provenance")
    payload = item.get("payload")
    for value in (metadata, provenance, payload):
        if isinstance(value, dict):
            parts.append(" ".join(str(v) for v in value.values() if v is not None))
    return "\n".join(part for part in parts if part)


def _score_item(item: dict[str, Any], query: str, query_tokens: set[str]) -> int:
    text = _item_search_text(item).lower()
    summary = _item_summary(item).lower()
    query_lower = query.lower().strip()
    score = 0
    if query_lower and query_lower in text:
        score += 20
    for token in query_tokens:
        if token in summary:
            score += 4
        elif token in text:
            score += 2
    return score


def _search_memory_items(
    items: list[dict[str, Any]],
    query: str,
    *,
    domain: str,
    limit: int,
    include_raw_chunks: bool,
) -> tuple[list[tuple[int, int, bool, dict[str, Any]]], int, int]:
    requested_domain = _normalize_domain(domain or "all")
    query_tokens = _tokenize(query)
    raw_omitted = 0
    candidates: list[tuple[int, int, bool, dict[str, Any]]] = []
    for idx, item in enumerate(items):
        item_domain = _item_domain(item)
        raw = _is_raw_chunk_item(item)
        if not _domain_matches(item_domain, requested_domain):
            continue
        if raw and not include_raw_chunks:
            raw_omitted += 1
            continue
        score = _score_item(item, query, query_tokens)
        candidates.append((score, idx, raw, item))

    candidates.sort(key=lambda match: (-match[0], match[1]))
    if query.strip() and any(score > 0 for score, _idx, _raw, _item in candidates):
        ranked = [match for match in candidates if match[0] > 0]
    else:
        ranked = candidates
    return ranked[:limit], raw_omitted, len(ranked)


def _tool_result_item(item: dict[str, Any], *, score: int, raw_chunk: bool) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": _item_id(item),
        "domain": _item_domain(item),
        "schema": _item_schema(item),
        "source": _item_source(item),
        "summary": _compact_text(_item_summary(item), max_chars=1200 if raw_chunk else 800),
        "score": score,
        "raw_chunk": raw_chunk,
    }
    etag = _first_item_value(item, ("etag", "version", "base_version"))
    if etag:
        result["etag"] = etag
    return {key: value for key, value in result.items() if value not in ("", None)}


def _backend_items(response: dict[str, Any]) -> list[dict[str, Any]]:
    value = response.get("items")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _format_backend_prefetch(response: dict[str, Any]) -> str:
    items = _backend_items(response)
    raw_omitted = _bounded_int(response.get("raw_chunks_omitted"), default=0, minimum=0, maximum=1_000_000)
    if not items:
        if raw_omitted:
            return (
                "Shared Hades project memory: no compact live-recall entries "
                f"matched this turn; {raw_omitted} raw chunk item(s) were "
                "excluded from automatic context."
            )
        return ""
    version = str(response.get("version") or response.get("etag") or "live")
    lines = [f"Shared Hades project memory (live search {version}):"]
    for item in items:
        summary = _compact_text(_item_summary(item), max_chars=520)
        if not summary:
            continue
        domain = _item_domain(item)
        label = f"[{domain}] " if domain else ""
        lines.append(f"- {label}{summary}")
    if raw_omitted:
        lines.append(f"- {raw_omitted} raw chunk item(s) excluded from automatic context.")
    return "\n".join(lines) if len(lines) > 1 else ""


def _tool_result_item_from_backend(item: dict[str, Any]) -> dict[str, Any]:
    score = _bounded_int(item.get("score"), default=0, minimum=0, maximum=1_000_000)
    raw_chunk = bool(item.get("raw_chunk") or _is_raw_chunk_item(item))
    result = _tool_result_item(item, score=score, raw_chunk=raw_chunk)
    for key in (
        "payload_excerpt",
        "page_id",
        "page_slug",
        "page_type",
        "source_type",
        "source_status",
        "evidence_count",
        "occurred_at",
        "updated_at",
        "version",
    ):
        value = item.get(key)
        if value not in ("", None):
            result[key] = value
    return result


def _tool_result_from_backend_search(response: dict[str, Any]) -> dict[str, Any]:
    items = [_tool_result_item_from_backend(item) for item in _backend_items(response)]
    count = _bounded_int(response.get("count"), default=len(items), minimum=0, maximum=1_000_000)
    candidate_count = _bounded_int(
        response.get("candidate_count"),
        default=count,
        minimum=0,
        maximum=1_000_000,
    )
    raw_omitted = _bounded_int(response.get("raw_chunks_omitted"), default=0, minimum=0, maximum=1_000_000)
    result: dict[str, Any] = {
        "status": "ok",
        "project_id": response.get("project_id"),
        "workspace_binding_id": response.get("workspace_binding_id"),
        "backend_version": response.get("version"),
        "backend_etag": response.get("etag"),
        "query": response.get("query", ""),
        "domain": _normalize_domain(response.get("domain") or "all"),
        "include_raw_chunks": bool(response.get("include_raw_chunks")),
        "searched_cache_only": False,
        "count": count,
        "candidate_count": candidate_count,
        "truncated": bool(response.get("truncated")),
        "raw_chunks_omitted": raw_omitted,
        "server_time": response.get("server_time"),
        "items": items,
    }
    freshness = response.get("freshness")
    if isinstance(freshness, dict):
        result["freshness"] = freshness
    return {key: value for key, value in result.items() if value not in ("", None)}


def _bounded_payload(value: Any) -> Any:
    if not isinstance(value, (dict, list)):
        return None
    encoded = json.dumps(value, sort_keys=True, default=str)
    if len(encoded) <= 4000:
        return value
    return {
        "truncated": True,
        "excerpt": _compact_text(encoded, max_chars=4000),
    }


def _bug_evidence_item_from_backend(item: dict[str, Any]) -> dict[str, Any]:
    payload = _bounded_payload(item.get("payload"))
    result: dict[str, Any] = {
        "id": _item_id(item),
        "bug_report_id": item.get("bug_report_id"),
        "kind": item.get("kind"),
        "summary": _compact_text(item.get("summary"), max_chars=1000),
        "source": item.get("source"),
        "payload": payload,
        "sha256": item.get("sha256"),
        "redactions": item.get("redactions"),
        "retention_class": item.get("retention_class"),
        "occurred_at": item.get("occurred_at"),
        "updated_at": item.get("updated_at"),
        "version": item.get("version"),
        "score": _bounded_int(item.get("score"), default=0, minimum=0, maximum=1_000_000),
    }
    return {key: value for key, value in result.items() if value not in ("", None)}


def _tool_result_from_backend_bug_evidence_search(response: dict[str, Any]) -> dict[str, Any]:
    items = [_bug_evidence_item_from_backend(item) for item in _backend_items(response)]
    count = _bounded_int(response.get("count"), default=len(items), minimum=0, maximum=1_000_000)
    candidate_count = _bounded_int(
        response.get("candidate_count"),
        default=count,
        minimum=0,
        maximum=1_000_000,
    )
    result: dict[str, Any] = {
        "status": "ok",
        "project_id": response.get("project_id"),
        "workspace_binding_id": response.get("workspace_binding_id"),
        "backend_version": response.get("version"),
        "backend_etag": response.get("etag"),
        "query": response.get("query", ""),
        "kind": response.get("kind") or "all",
        "bug_report_id": response.get("bug_report_id"),
        "searched_cache_only": False,
        "count": count,
        "candidate_count": candidate_count,
        "truncated": bool(response.get("truncated")),
        "server_time": response.get("server_time"),
        "items": items,
    }
    freshness = response.get("freshness")
    if isinstance(freshness, dict):
        result["freshness"] = freshness
    return {key: value for key, value in result.items() if value not in ("", None)}


def _tool_result_from_backend_project_awareness_status(response: dict[str, Any]) -> dict[str, Any]:
    freshness = response.get("freshness")
    coverage = response.get("coverage")
    actions = response.get("actions")
    result: dict[str, Any] = {
        "status": "ok",
        "project_id": response.get("project_id"),
        "workspace_binding_id": response.get("workspace_binding_id"),
        "workspace_head_commit": response.get("workspace_head_commit"),
        "overall_status": response.get("overall_status"),
        "diagnosable_without_source": bool(response.get("diagnosable_without_source")),
        "server_time": response.get("server_time"),
    }
    if isinstance(freshness, dict):
        result["freshness"] = freshness
    if isinstance(coverage, dict):
        result["coverage"] = coverage
    if isinstance(actions, list):
        result["actions"] = [str(action) for action in actions if str(action).strip()]
    return {key: value for key, value in result.items() if value not in ("", None)}


def register(ctx) -> None:
    ctx.register_memory_provider(HadesBackendMemoryProvider())
