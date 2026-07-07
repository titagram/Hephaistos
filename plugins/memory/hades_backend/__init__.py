"""Hades backend shared-memory provider."""

from __future__ import annotations

from collections import deque
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
LIVE_SEARCH_TIMEOUT_SECONDS = 1.0
SOURCE_SLICE_FETCH_TIMEOUT_SECONDS = 1.5
LIVE_WRITE_TIMEOUT_SECONDS = 2.0
CREATE_ACTIONS = {"add", "create"}
UPDATE_ACTIONS = {"replace", "update"}
DELETE_ACTIONS = {"remove", "delete"}
AUTO_PREFETCH_LIMIT = 8
TOOL_RESULT_LIMIT = 20
SEARCH_TOOL_NAME = "hades_backend_project_memory_search"
BUG_EVIDENCE_SEARCH_TOOL_NAME = "hades_backend_bug_evidence_search"
GRAPH_SEARCH_TOOL_NAME = "hades_backend_graph_search"
GRAPH_TRAVERSE_TOOL_NAME = "hades_backend_graph_traverse"
SOURCE_SLICE_FETCH_TOOL_NAME = "hades_backend_source_slice_fetch"
EVIDENCE_PACK_SEARCH_TOOL_NAME = "hades_backend_evidence_pack_search"
EVIDENCE_PACK_CREATE_TOOL_NAME = "hades_backend_evidence_pack_create"
PROJECT_AWARENESS_STATUS_TOOL_NAME = "hades_backend_project_awareness_status"
DIAGNOSIS_REPORT_CREATE_TOOL_NAME = "hades_backend_diagnosis_report_create"
RESOLVED_BUG_PROMOTE_TOOL_NAME = "hades_backend_resolved_bug_promote"
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
SEARCH_FILTER_KEYS = ("kind", "schema", "source", "symbol", "path")
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
    "resolved-bug": "project_memory",
    "resolved_bug": "project_memory",
    "source": "source_chunks",
    "source-chunks": "source_chunks",
    "source_chunks": "source_chunks",
    "wiki": "wiki",
    "wiki_revision": "wiki",
}
SEARCH_DOMAINS = ("all", "project_memory", "logbook", "wiki", "agent_notes", "source_chunks", "artifacts")
GRAPH_ARTIFACT_SCHEMAS = {"hades.php_graph.v1", "hades.code_graph.v1"}
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
DIAGNOSIS_REPORT_STATUSES = ("draft", "final")
DIAGNOSIS_CONFIDENCE_LEVELS = ("high", "medium", "low", "insufficient")
RESOLVED_BUG_VERIFICATION_STATUSES = ("user_confirmed", "test_passed", "manual_review")
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
            "kind": {
                "type": "string",
                "description": "Optional memory item kind filter, for example resolved_bug, agent_note, or wiki_entry.",
            },
            "schema": {
                "type": "string",
                "description": "Optional structured schema filter, for example hades.resolved_bug.v1 or hades.php_graph.v1.",
            },
            "source": {
                "type": "string",
                "description": "Optional source/provenance filter, for example hades_diagnosis_report or wiki_revision.",
            },
            "symbol": {
                "type": "string",
                "description": "Optional code symbol filter, for example OrderController@show or App\\Service\\Class::method.",
            },
            "path": {
                "type": "string",
                "description": "Optional path filter for source-backed memory, graph nodes, wiki chunks, or artifacts.",
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

GRAPH_SEARCH_TOOL_SCHEMA: Dict[str, Any] = {
    "name": GRAPH_SEARCH_TOOL_NAME,
    "description": (
        "Search current Hades backend project graph/artifact context such as "
        "Laravel routes, controller methods, class symbols, and graph edges. "
        "Use this after bug evidence search and before source slice fetch when "
        "diagnosing call paths or owner methods without local source access. "
        "Results are live backend artifact search; there is no local cache "
        "fallback."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Route, symbol, file, class, method, edge, or framework term to search.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": TOOL_RESULT_LIMIT,
                "default": 8,
                "description": "Maximum number of graph/artifact results to return.",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
}

GRAPH_TRAVERSE_TOOL_SCHEMA: Dict[str, Any] = {
    "name": GRAPH_TRAVERSE_TOOL_NAME,
    "description": (
        "Traverse the current linked Hades backend code graph from a route, "
        "symbol, file, class, or method. Use this after bug evidence points to "
        "an entrypoint and before source slice fetch when you need bounded "
        "route -> controller -> service -> model context without local source "
        "access. Results prefer live backend data and fall back to the local "
        "synced graph cache when the backend is temporarily unavailable."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "start": {
                "type": "string",
                "description": "Route name/id, URI, file, class, method, or symbol to start from.",
            },
            "direction": {
                "type": "string",
                "enum": ["any", "out", "in"],
                "default": "any",
                "description": "Traversal direction relative to the start node.",
            },
            "max_depth": {
                "type": "integer",
                "minimum": 1,
                "maximum": 3,
                "default": 2,
                "description": "Maximum graph edge depth to traverse.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "default": 20,
                "description": "Maximum nodes/edges to return.",
            },
        },
        "required": ["start"],
        "additionalProperties": False,
    },
}

SOURCE_SLICE_FETCH_TOOL_SCHEMA: Dict[str, Any] = {
    "name": SOURCE_SLICE_FETCH_TOOL_NAME,
    "description": (
        "Fetch bounded, redacted source slices already stored in the linked "
        "Hades backend for this workspace. Use this after bug evidence or graph "
        "search points to a file/symbol/line and before claiming line-level root "
        "causes without local source access. Results are live backend data; "
        "there is no local cache fallback."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "id": {
                "type": "string",
                "description": "Optional exact source slice id.",
            },
            "query": {
                "type": "string",
                "description": "Optional text to match path, symbol, language, or redacted content.",
            },
            "path": {
                "type": "string",
                "description": "Optional exact project-relative source path.",
            },
            "symbol": {
                "type": "string",
                "description": "Optional symbol such as Controller@method or fully qualified class.",
            },
            "line": {
                "type": "integer",
                "minimum": 1,
                "description": "Optional source line that must fall inside the stored slice.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "default": 3,
                "description": "Maximum number of source slices to return.",
            },
        },
        "additionalProperties": False,
    },
}

EVIDENCE_PACK_SEARCH_TOOL_SCHEMA: Dict[str, Any] = {
    "name": EVIDENCE_PACK_SEARCH_TOOL_NAME,
    "description": (
        "Search persisted Hades evidence packs for this linked workspace. "
        "Evidence packs aggregate bug evidence refs, graph refs, and source "
        "slice ids into a bounded source-free diagnosis bundle. Use this after "
        "project awareness and before making root-cause claims when a prior "
        "pack may already contain the necessary evidence. Results are live "
        "backend data; there is no local cache fallback."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "id": {
                "type": "string",
                "description": "Optional exact evidence pack id.",
            },
            "query": {
                "type": "string",
                "description": "Optional text to match pack title, summary, refs, or bounded payload.",
            },
            "bug_report_id": {
                "type": "string",
                "description": "Optional Hades bug report id to scope pack search.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": TOOL_RESULT_LIMIT,
                "default": 5,
                "description": "Maximum number of evidence packs to return.",
            },
        },
        "additionalProperties": False,
    },
}

EVIDENCE_PACK_CREATE_TOOL_SCHEMA: Dict[str, Any] = {
    "name": EVIDENCE_PACK_CREATE_TOOL_NAME,
    "description": (
        "Persist a bounded Hades evidence pack for this linked workspace after "
        "collecting bug evidence, graph refs, and source slice ids. Use this to "
        "preserve the evidence bundle that supports a source-free diagnosis; do "
        "not include raw project dumps or unredacted secrets."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "bug_report_id": {
                "type": "string",
                "description": "Optional Hades bug report id this evidence pack belongs to.",
            },
            "title": {
                "type": "string",
                "description": "Short evidence pack title.",
            },
            "summary": {
                "type": "string",
                "description": "Bounded summary of what this evidence pack proves or still lacks.",
            },
            "evidence_refs": {
                "type": "array",
                "description": "Refs to bug evidence items, source slices, graph artifacts, tests, or logs.",
                "items": {"type": "object", "additionalProperties": True},
            },
            "graph_refs": {
                "type": "array",
                "description": "Route, symbol, graph node, graph edge, table, config, or artifact refs.",
                "items": {"type": "object", "additionalProperties": True},
            },
            "source_slice_ids": {
                "type": "array",
                "description": "Stored source slice ids included by reference.",
                "items": {"type": "string"},
            },
            "payload": {
                "type": "object",
                "description": "Optional bounded structured bundle detail.",
                "additionalProperties": True,
            },
            "head_commit": {
                "type": "string",
                "description": "Optional workspace HEAD commit represented by this evidence pack.",
            },
            "redactions": {
                "type": "integer",
                "minimum": 0,
                "maximum": 100000,
                "default": 0,
                "description": "Number of redactions applied to pack payload/evidence.",
            },
        },
        "required": ["title", "summary"],
        "additionalProperties": False,
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

DIAGNOSIS_REPORT_CREATE_TOOL_SCHEMA: Dict[str, Any] = {
    "name": DIAGNOSIS_REPORT_CREATE_TOOL_NAME,
    "description": (
        "Persist a structured Hades backend diagnosis report for the linked "
        "workspace after the bug-diagnosis workflow has compared awareness, bug "
        "evidence, graph results, and source slices. Use this to preserve final "
        "or insufficient-evidence outcomes with evidence refs. Results are live "
        "backend data; there is no local cache fallback."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "bug_report_id": {
                "type": "string",
                "description": "Optional Hades bug report id this diagnosis belongs to.",
            },
            "status": {
                "type": "string",
                "enum": list(DIAGNOSIS_REPORT_STATUSES),
                "default": "final",
                "description": "Whether this is a draft or final diagnosis report.",
            },
            "confidence": {
                "type": "string",
                "enum": list(DIAGNOSIS_CONFIDENCE_LEVELS),
                "description": "Confidence level supported by the cited evidence.",
            },
            "root_cause": {
                "type": "string",
                "description": "Precise root cause, or 'not determined' for insufficient evidence.",
            },
            "mechanism": {
                "type": "string",
                "description": "How the bug happens at runtime.",
            },
            "evidence_refs": {
                "type": "array",
                "description": "Evidence references such as bug evidence, graph artifact, and source slice ids.",
                "items": {
                    "type": "object",
                    "additionalProperties": True,
                },
            },
            "freshness": {
                "type": "object",
                "description": "Freshness and commit comparison used by the diagnosis.",
                "additionalProperties": True,
            },
            "awareness": {
                "type": "object",
                "description": (
                    "Project awareness status used by the diagnosis. High/medium "
                    "confidence requires diagnosable_without_source=true."
                ),
                "additionalProperties": True,
            },
            "payload": {
                "type": "object",
                "description": "Optional bounded structured diagnosis detail.",
                "additionalProperties": True,
            },
            "redactions": {
                "type": "integer",
                "minimum": 0,
                "maximum": 100000,
                "default": 0,
                "description": "Number of redactions applied to report payload/evidence.",
            },
        },
        "required": ["confidence", "root_cause"],
        "additionalProperties": False,
    },
}

RESOLVED_BUG_PROMOTE_TOOL_SCHEMA: Dict[str, Any] = {
    "name": RESOLVED_BUG_PROMOTE_TOOL_NAME,
    "description": (
        "Promote a final high/medium confidence Hades diagnosis report to "
        "verified resolved-bug project memory. Use only after a user confirms "
        "the diagnosis or a regression/fix verification has passed, so similar "
        "future bugs can be recalled without reading the source code."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "diagnosis_report_id": {
                "type": "string",
                "description": "Hades diagnosis report id to promote.",
            },
            "verification_status": {
                "type": "string",
                "enum": list(RESOLVED_BUG_VERIFICATION_STATUSES),
                "description": "How the resolved bug was verified.",
            },
            "fix_commit": {
                "type": "string",
                "description": "Optional commit hash containing the fix.",
            },
            "fix_pr_url": {
                "type": "string",
                "description": "Optional PR or review URL for the fix.",
            },
            "affected_symbols": {
                "type": "array",
                "description": "Optional affected symbols, routes, classes, or methods.",
                "items": {"type": "string"},
            },
            "regression_tests": {
                "type": "array",
                "description": "Optional regression tests that prove the fix.",
                "items": {"type": "string"},
            },
            "payload": {
                "type": "object",
                "description": "Optional bounded promotion metadata.",
                "additionalProperties": True,
            },
            "redactions": {
                "type": "integer",
                "minimum": 0,
                "maximum": 100000,
                "default": 0,
                "description": "Number of redactions applied to promotion metadata.",
            },
        },
        "required": ["diagnosis_report_id", "verification_status"],
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
            "search project memory, bug evidence, evidence packs, or project "
            "awareness status explicitly when exact evidence or diagnosis "
            "readiness is needed."
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if self._binding is None:
            return ""
        backend_result, _backend_error = self._backend_memory_search(
            query=query,
            domain="all",
            filters={},
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
            filters={},
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
            GRAPH_SEARCH_TOOL_SCHEMA,
            GRAPH_TRAVERSE_TOOL_SCHEMA,
            SOURCE_SLICE_FETCH_TOOL_SCHEMA,
            EVIDENCE_PACK_SEARCH_TOOL_SCHEMA,
            EVIDENCE_PACK_CREATE_TOOL_SCHEMA,
            PROJECT_AWARENESS_STATUS_TOOL_SCHEMA,
            DIAGNOSIS_REPORT_CREATE_TOOL_SCHEMA,
            RESOLVED_BUG_PROMOTE_TOOL_SCHEMA,
        ]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        if tool_name == EVIDENCE_PACK_CREATE_TOOL_NAME:
            return self._handle_evidence_pack_create(args)
        if tool_name == EVIDENCE_PACK_SEARCH_TOOL_NAME:
            return self._handle_evidence_pack_search(args)
        if tool_name == RESOLVED_BUG_PROMOTE_TOOL_NAME:
            return self._handle_resolved_bug_promote(args)
        if tool_name == DIAGNOSIS_REPORT_CREATE_TOOL_NAME:
            return self._handle_diagnosis_report_create(args)
        if tool_name == PROJECT_AWARENESS_STATUS_TOOL_NAME:
            return self._handle_project_awareness_status()
        if tool_name == SOURCE_SLICE_FETCH_TOOL_NAME:
            return self._handle_source_slice_fetch(args)
        if tool_name == GRAPH_SEARCH_TOOL_NAME:
            return self._handle_graph_search(args)
        if tool_name == GRAPH_TRAVERSE_TOOL_NAME:
            return self._handle_graph_traverse(args)
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
        filters = _search_filters_from_args(args)
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
            filters=filters,
            limit=limit,
            include_raw_chunks=include_raw_chunks,
        )
        if backend_result is not None:
            backend_result = _filter_backend_search_response(backend_result, filters)
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
            filters=filters,
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
            "kind": filters.get("kind") or "all",
            "filters": filters,
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

    def _handle_diagnosis_report_create(self, args: Dict[str, Any]) -> str:
        root_cause = str(args.get("root_cause") or "").strip()
        if not root_cause:
            return tool_error("Missing required parameter: root_cause")
        confidence = str(args.get("confidence") or "").strip()
        if confidence not in DIAGNOSIS_CONFIDENCE_LEVELS:
            return tool_error(
                f"Unsupported diagnosis confidence: {confidence}",
                allowed_confidence=list(DIAGNOSIS_CONFIDENCE_LEVELS),
            )
        status = str(args.get("status") or "final").strip()
        if status not in DIAGNOSIS_REPORT_STATUSES:
            return tool_error(
                f"Unsupported diagnosis report status: {status}",
                allowed_statuses=list(DIAGNOSIS_REPORT_STATUSES),
            )

        evidence_refs = args.get("evidence_refs")
        if evidence_refs is None:
            evidence_refs = []
        if not isinstance(evidence_refs, list):
            return tool_error("Parameter evidence_refs must be an array when provided.")
        freshness = args.get("freshness")
        if freshness is not None and not isinstance(freshness, dict):
            return tool_error("Parameter freshness must be an object when provided.")
        awareness = args.get("awareness")
        if awareness is not None and not isinstance(awareness, dict):
            return tool_error("Parameter awareness must be an object when provided.")
        payload = args.get("payload")
        if payload is not None and not isinstance(payload, dict):
            return tool_error("Parameter payload must be an object when provided.")
        if confidence in {"high", "medium"}:
            if not evidence_refs:
                return tool_error(
                    "High/medium confidence diagnosis reports require evidence_refs.",
                    required_for_confidence=confidence,
                )
            freshness_status = str((freshness or {}).get("status") or "").strip()
            if freshness_status != "current":
                return tool_error(
                    "High/medium confidence diagnosis reports require freshness.status=current.",
                    freshness_status=freshness_status or "missing",
                )
            if not bool((awareness or {}).get("diagnosable_without_source")):
                return tool_error(
                    "High/medium confidence diagnosis reports require awareness.diagnosable_without_source=true.",
                    diagnosable_without_source=bool((awareness or {}).get("diagnosable_without_source")),
                )

        if self._binding is None:
            return tool_result(
                {
                    "status": "unmapped_project",
                    "message": (
                        "This working directory is not linked to a Hades backend "
                        "project, so diagnosis reports cannot be saved."
                    ),
                    "actions": [
                        "Run `hades backend bootstrap ...` for a new backend project binding.",
                        "Run `hades project link <project>` from an existing local project.",
                        "Run `hades backend sync` after linking.",
                    ],
                }
            )

        backend_result, backend_error = self._backend_diagnosis_report_create(
            bug_report_id=str(args.get("bug_report_id") or "").strip(),
            status=status,
            confidence=confidence,
            root_cause=root_cause,
            mechanism=str(args.get("mechanism") or "").strip(),
            evidence_refs=evidence_refs,
            freshness=freshness,
            payload=payload,
            redactions=_bounded_int(args.get("redactions"), default=0, minimum=0, maximum=100_000),
        )
        if backend_result is not None:
            return tool_result(_tool_result_from_backend_diagnosis_report(backend_result))

        result = {
            "status": "backend_unavailable",
            "project_id": self._binding.project_id,
            "workspace_binding_id": self._binding.backend_workspace_binding_id,
            "message": "Hades backend diagnosis report create is unavailable.",
            "actions": ["Run `hades backend status` and retry after backend connectivity is healthy."],
        }
        if backend_error:
            result["backend_live_error"] = backend_error
        return tool_result(result)

    def _handle_resolved_bug_promote(self, args: Dict[str, Any]) -> str:
        diagnosis_report_id = str(args.get("diagnosis_report_id") or "").strip()
        if not diagnosis_report_id:
            return tool_error("Missing required parameter: diagnosis_report_id")
        verification_status = str(args.get("verification_status") or "").strip()
        if verification_status not in RESOLVED_BUG_VERIFICATION_STATUSES:
            return tool_error(
                f"Unsupported resolved bug verification status: {verification_status}",
                allowed_statuses=list(RESOLVED_BUG_VERIFICATION_STATUSES),
            )
        affected_symbols = _string_list(args.get("affected_symbols"))
        regression_tests = _string_list(args.get("regression_tests"))
        payload = args.get("payload")
        if payload is not None and not isinstance(payload, dict):
            return tool_error("Parameter payload must be an object when provided.")

        if self._binding is None:
            return tool_result(
                {
                    "status": "unmapped_project",
                    "message": (
                        "This working directory is not linked to a Hades backend "
                        "project, so resolved bug memory cannot be promoted."
                    ),
                    "actions": [
                        "Run `hades backend bootstrap ...` for a new backend project binding.",
                        "Run `hades project link <project>` from an existing local project.",
                        "Run `hades backend sync` after linking.",
                    ],
                }
            )

        backend_result, backend_error = self._backend_resolved_bug_promote(
            diagnosis_report_id=diagnosis_report_id,
            verification_status=verification_status,
            fix_commit=str(args.get("fix_commit") or "").strip(),
            fix_pr_url=str(args.get("fix_pr_url") or "").strip(),
            affected_symbols=affected_symbols,
            regression_tests=regression_tests,
            payload=payload,
            redactions=_bounded_int(args.get("redactions"), default=0, minimum=0, maximum=100_000),
        )
        if backend_result is not None:
            return tool_result(_tool_result_from_backend_resolved_bug_promote(backend_result))

        result = {
            "status": "backend_unavailable",
            "project_id": self._binding.project_id,
            "workspace_binding_id": self._binding.backend_workspace_binding_id,
            "message": "Hades backend resolved bug promotion is unavailable.",
            "actions": ["Run `hades backend status` and retry after backend connectivity is healthy."],
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

    def _handle_source_slice_fetch(self, args: Dict[str, Any]) -> str:
        slice_id = str(args.get("id") or "").strip()
        query = str(args.get("query") or "").strip()
        path = str(args.get("path") or "").strip()
        symbol = str(args.get("symbol") or "").strip()
        line_value = args.get("line")
        line = _bounded_int(line_value, default=0, minimum=0, maximum=10_000_000) if line_value is not None else 0
        limit = _bounded_int(args.get("limit"), default=3, minimum=1, maximum=10)
        if not any((slice_id, query, path, symbol, line)):
            return tool_error("Provide at least one of id, query, path, symbol, or line.")

        if self._binding is None:
            return tool_result(
                {
                    "status": "unmapped_project",
                    "message": (
                        "This working directory is not linked to a Hades backend "
                        "project, so source slices are unavailable."
                    ),
                    "actions": [
                        "Run `hades backend bootstrap ...` for a new backend project binding.",
                        "Run `hades project link <project>` from an existing local project.",
                        "Run `hades backend sync` after linking.",
                    ],
                    "items": [],
                }
            )

        backend_result, backend_error = self._backend_source_slice_fetch(
            slice_id=slice_id,
            query=query,
            path=path,
            symbol=symbol,
            line=line,
            limit=limit,
        )
        if backend_result is not None:
            return tool_result(_tool_result_from_backend_source_slices(backend_result))

        result = {
            "status": "backend_unavailable",
            "project_id": self._binding.project_id,
            "workspace_binding_id": self._binding.backend_workspace_binding_id,
            "message": "Hades backend source slice live fetch is unavailable.",
            "actions": ["Run `hades backend status` and `hades backend sync` to diagnose backend connectivity."],
            "items": [],
        }
        if backend_error:
            result["backend_live_error"] = backend_error
        return tool_result(result)

    def _handle_evidence_pack_search(self, args: Dict[str, Any]) -> str:
        pack_id = str(args.get("id") or "").strip()
        query = str(args.get("query") or "").strip()
        bug_report_id = str(args.get("bug_report_id") or "").strip()
        limit = _bounded_int(args.get("limit"), default=5, minimum=1, maximum=TOOL_RESULT_LIMIT)
        if not any((pack_id, query, bug_report_id)):
            return tool_error("Provide at least one of id, query, or bug_report_id.")

        if self._binding is None:
            return tool_result(
                {
                    "status": "unmapped_project",
                    "message": (
                        "This working directory is not linked to a Hades backend "
                        "project, so evidence packs are unavailable."
                    ),
                    "actions": [
                        "Run `hades backend bootstrap ...` for a new backend project binding.",
                        "Run `hades project link <project>` from an existing local project.",
                        "Run `hades backend sync` after linking.",
                    ],
                    "items": [],
                }
            )

        backend_result, backend_error = self._backend_evidence_pack_search(
            pack_id=pack_id,
            query=query,
            bug_report_id=bug_report_id,
            limit=limit,
        )
        if backend_result is not None:
            return tool_result(_tool_result_from_backend_evidence_packs(backend_result))

        result = {
            "status": "backend_unavailable",
            "project_id": self._binding.project_id,
            "workspace_binding_id": self._binding.backend_workspace_binding_id,
            "message": "Hades backend evidence pack live search is unavailable.",
            "actions": ["Run `hades backend status` and `hades backend sync` to diagnose backend connectivity."],
            "items": [],
        }
        if backend_error:
            result["backend_live_error"] = backend_error
        return tool_result(result)

    def _handle_evidence_pack_create(self, args: Dict[str, Any]) -> str:
        title = str(args.get("title") or "").strip()
        if not title:
            return tool_error("Missing required parameter: title")
        summary = str(args.get("summary") or "").strip()
        if not summary:
            return tool_error("Missing required parameter: summary")

        evidence_refs = args.get("evidence_refs")
        if evidence_refs is None:
            evidence_refs = []
        if not isinstance(evidence_refs, list):
            return tool_error("Parameter evidence_refs must be an array when provided.")
        graph_refs = args.get("graph_refs")
        if graph_refs is None:
            graph_refs = []
        if not isinstance(graph_refs, list):
            return tool_error("Parameter graph_refs must be an array when provided.")
        source_slice_ids = _string_list(args.get("source_slice_ids"))
        payload = args.get("payload")
        if payload is not None and not isinstance(payload, dict):
            return tool_error("Parameter payload must be an object when provided.")

        if self._binding is None:
            return tool_result(
                {
                    "status": "unmapped_project",
                    "message": (
                        "This working directory is not linked to a Hades backend "
                        "project, so evidence packs cannot be saved."
                    ),
                    "actions": [
                        "Run `hades backend bootstrap ...` for a new backend project binding.",
                        "Run `hades project link <project>` from an existing local project.",
                        "Run `hades backend sync` after linking.",
                    ],
                }
            )

        backend_result, backend_error = self._backend_evidence_pack_create(
            bug_report_id=str(args.get("bug_report_id") or "").strip(),
            title=title,
            summary=summary,
            evidence_refs=evidence_refs,
            graph_refs=graph_refs,
            source_slice_ids=source_slice_ids,
            payload=payload,
            head_commit=str(args.get("head_commit") or "").strip(),
            redactions=_bounded_int(args.get("redactions"), default=0, minimum=0, maximum=100_000),
        )
        if backend_result is not None:
            return tool_result(_tool_result_from_backend_evidence_pack_create(backend_result))

        result = {
            "status": "backend_unavailable",
            "project_id": self._binding.project_id,
            "workspace_binding_id": self._binding.backend_workspace_binding_id,
            "message": "Hades backend evidence pack create is unavailable.",
            "actions": ["Run `hades backend status` and retry after backend connectivity is healthy."],
        }
        if backend_error:
            result["backend_live_error"] = backend_error
        return tool_result(result)

    def _handle_graph_search(self, args: Dict[str, Any]) -> str:
        query = str(args.get("query") or "").strip()
        if not query:
            return tool_error("Missing required parameter: query")
        limit = _bounded_int(args.get("limit"), default=8, minimum=1, maximum=TOOL_RESULT_LIMIT)

        if self._binding is None:
            return tool_result(
                {
                    "status": "unmapped_project",
                    "message": (
                        "This working directory is not linked to a Hades backend "
                        "project, so graph search is unavailable."
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
            domain="artifacts",
            filters={},
            limit=limit,
            include_raw_chunks=False,
        )
        if backend_result is not None:
            result = _tool_result_from_backend_search(backend_result)
            result["tool_domain"] = "graph"
            return tool_result(result)

        result = {
            "status": "backend_unavailable",
            "project_id": self._binding.project_id,
            "workspace_binding_id": self._binding.backend_workspace_binding_id,
            "message": "Hades backend graph live search is unavailable.",
            "actions": ["Run `hades backend status` and `hades backend sync` to diagnose backend connectivity."],
            "items": [],
        }
        if backend_error:
            result["backend_live_error"] = backend_error
        return tool_result(result)

    def _handle_graph_traverse(self, args: Dict[str, Any]) -> str:
        start = str(args.get("start") or "").strip()
        if not start:
            return tool_error("Missing required parameter: start")
        direction = str(args.get("direction") or "any").strip()
        if direction not in ("any", "out", "in"):
            return tool_error("Unsupported graph traversal direction", allowed_directions=["any", "out", "in"])
        max_depth = _bounded_int(args.get("max_depth"), default=2, minimum=1, maximum=3)
        limit = _bounded_int(args.get("limit"), default=20, minimum=1, maximum=50)

        if self._binding is None:
            return tool_result(
                {
                    "status": "unmapped_project",
                    "message": (
                        "This working directory is not linked to a Hades backend "
                        "project, so graph traversal is unavailable."
                    ),
                    "actions": [
                        "Run `hades backend bootstrap ...` for a new backend project binding.",
                        "Run `hades project link <project>` from an existing local project.",
                        "Run `hades backend sync` after linking.",
                    ],
                    "nodes": [],
                    "edges": [],
                }
            )

        backend_result, backend_error = self._backend_graph_traverse(
            start=start,
            direction=direction,
            max_depth=max_depth,
            limit=limit,
        )
        if backend_result is not None:
            return tool_result(_tool_result_from_backend_graph_traverse(backend_result))

        local_result = self._local_graph_traverse(
            start=start,
            direction=direction,
            max_depth=max_depth,
            limit=limit,
        )
        if local_result is not None:
            local_result["project_id"] = self._binding.project_id
            local_result["workspace_binding_id"] = self._binding.backend_workspace_binding_id
            if backend_error:
                local_result["backend_live_error"] = backend_error
            return tool_result(local_result)

        result = {
            "status": "backend_unavailable",
            "project_id": self._binding.project_id,
            "workspace_binding_id": self._binding.backend_workspace_binding_id,
            "message": "Hades backend graph traversal is unavailable.",
            "actions": ["Run `hades backend status` and `hades backend sync` to diagnose backend connectivity."],
            "nodes": [],
            "edges": [],
        }
        if backend_error:
            result["backend_live_error"] = backend_error
        return tool_result(result)

    def _local_graph_traverse(
        self,
        *,
        start: str,
        direction: str,
        max_depth: int,
        limit: int,
    ) -> dict[str, Any] | None:
        if self._binding is None:
            return None

        sources: list[dict[str, Any]] = []
        cache = self._load_memory_cache()
        if cache is not None:
            for item in cache.items:
                sources.append(
                    {
                        "origin": "memory_cache",
                        "cache_version": cache.version,
                        "cache_updated_at": cache.updated_at,
                        "item": item,
                    }
                )

        with db.connect_closing() as conn:
            jobs = db.list_jobs(conn, statuses=["completed"])
        for job in reversed(jobs):
            if job.workspace_binding_id != self._binding.backend_workspace_binding_id or not job.result:
                continue
            sources.append(
                {
                    "origin": "backend_job",
                    "job_id": job.job_id,
                    "item": job.result,
                }
            )

        return _local_graph_traverse_response(
            sources,
            start=start,
            direction=direction,
            max_depth=max_depth,
            limit=limit,
        )

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
        filters: dict[str, str],
        limit: int,
        include_raw_chunks: bool,
    ) -> tuple[dict[str, Any] | None, str | None]:
        if self._binding is None:
            return None, None
        try:
            client = runtime.client_from_config(timeout=LIVE_SEARCH_TIMEOUT_SECONDS)
            try:
                payload = {
                    "project_id": self._binding.project_id,
                    "workspace_binding_id": self._binding.backend_workspace_binding_id,
                    "query": query,
                    "domain": domain,
                    "limit": limit,
                    "include_raw_chunks": include_raw_chunks,
                }
                payload.update(filters)
                response = client.memory_search(**payload)
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

    def _backend_graph_traverse(
        self,
        *,
        start: str,
        direction: str,
        max_depth: int,
        limit: int,
    ) -> tuple[dict[str, Any] | None, str | None]:
        if self._binding is None:
            return None, None
        try:
            client = runtime.client_from_config(timeout=LIVE_SEARCH_TIMEOUT_SECONDS)
            try:
                response = client.graph_traverse(
                    project_id=self._binding.project_id,
                    workspace_binding_id=self._binding.backend_workspace_binding_id,
                    start=start,
                    direction=direction,
                    max_depth=max_depth,
                    limit=limit,
                )
            finally:
                close = getattr(client, "close", None)
                if callable(close):
                    close()
        except Exception as exc:
            return None, redact_secret(str(exc))
        return response, None

    def _backend_source_slice_fetch(
        self,
        *,
        slice_id: str,
        query: str,
        path: str,
        symbol: str,
        line: int,
        limit: int,
    ) -> tuple[dict[str, Any] | None, str | None]:
        if self._binding is None:
            return None, None
        try:
            client = runtime.client_from_config(timeout=SOURCE_SLICE_FETCH_TIMEOUT_SECONDS)
            try:
                response = client.source_slices(
                    project_id=self._binding.project_id,
                    workspace_binding_id=self._binding.backend_workspace_binding_id,
                    id=slice_id or None,
                    query=query or None,
                    path=path or None,
                    symbol=symbol or None,
                    line=line or None,
                    limit=limit,
                )
            finally:
                close = getattr(client, "close", None)
                if callable(close):
                    close()
        except Exception as exc:
            return None, redact_secret(str(exc))
        return response, None

    def _backend_evidence_pack_search(
        self,
        *,
        pack_id: str,
        query: str,
        bug_report_id: str,
        limit: int,
    ) -> tuple[dict[str, Any] | None, str | None]:
        if self._binding is None:
            return None, None
        try:
            client = runtime.client_from_config(timeout=LIVE_SEARCH_TIMEOUT_SECONDS)
            try:
                response = client.evidence_packs(
                    project_id=self._binding.project_id,
                    workspace_binding_id=self._binding.backend_workspace_binding_id,
                    id=pack_id or None,
                    query=query or None,
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

    def _backend_evidence_pack_create(
        self,
        *,
        bug_report_id: str,
        title: str,
        summary: str,
        evidence_refs: list[Any],
        graph_refs: list[Any],
        source_slice_ids: list[str],
        payload: dict[str, Any] | None,
        head_commit: str,
        redactions: int,
    ) -> tuple[dict[str, Any] | None, str | None]:
        if self._binding is None:
            return None, None
        try:
            client = runtime.client_from_config(timeout=LIVE_WRITE_TIMEOUT_SECONDS)
            try:
                response = client.create_evidence_pack(
                    project_id=self._binding.project_id,
                    workspace_binding_id=self._binding.backend_workspace_binding_id,
                    bug_report_id=bug_report_id or None,
                    title=title,
                    summary=summary,
                    evidence_refs=evidence_refs,
                    graph_refs=graph_refs,
                    source_slice_ids=source_slice_ids,
                    payload=payload,
                    head_commit=head_commit or None,
                    redactions=redactions,
                )
            finally:
                close = getattr(client, "close", None)
                if callable(close):
                    close()
        except Exception as exc:
            return None, redact_secret(str(exc))
        return response, None

    def _backend_diagnosis_report_create(
        self,
        *,
        bug_report_id: str,
        status: str,
        confidence: str,
        root_cause: str,
        mechanism: str,
        evidence_refs: list[Any],
        freshness: dict[str, Any] | None,
        payload: dict[str, Any] | None,
        redactions: int,
    ) -> tuple[dict[str, Any] | None, str | None]:
        if self._binding is None:
            return None, None
        try:
            client = runtime.client_from_config(timeout=LIVE_WRITE_TIMEOUT_SECONDS)
            try:
                response = client.create_diagnosis_report(
                    project_id=self._binding.project_id,
                    workspace_binding_id=self._binding.backend_workspace_binding_id,
                    bug_report_id=bug_report_id or None,
                    status=status,
                    confidence=confidence,
                    root_cause=root_cause,
                    mechanism=mechanism or None,
                    evidence_refs=evidence_refs,
                    freshness=freshness,
                    payload=payload,
                    redactions=redactions,
                )
            finally:
                close = getattr(client, "close", None)
                if callable(close):
                    close()
        except Exception as exc:
            return None, redact_secret(str(exc))
        return response, None

    def _backend_resolved_bug_promote(
        self,
        *,
        diagnosis_report_id: str,
        verification_status: str,
        fix_commit: str,
        fix_pr_url: str,
        affected_symbols: list[str],
        regression_tests: list[str],
        payload: dict[str, Any] | None,
        redactions: int,
    ) -> tuple[dict[str, Any] | None, str | None]:
        if self._binding is None:
            return None, None
        try:
            client = runtime.client_from_config(timeout=LIVE_WRITE_TIMEOUT_SECONDS)
            try:
                response = client.promote_diagnosis_report(
                    diagnosis_report_id,
                    project_id=self._binding.project_id,
                    workspace_binding_id=self._binding.backend_workspace_binding_id,
                    verification_status=verification_status,
                    fix_commit=fix_commit or None,
                    fix_pr_url=fix_pr_url or None,
                    affected_symbols=affected_symbols,
                    regression_tests=regression_tests,
                    payload=payload,
                    redactions=redactions,
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


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if not isinstance(item, (str, int, float, bool)):
            continue
        text = _compact_text(item, max_chars=500)
        if text:
            result.append(text)
        if len(result) >= 50:
            break
    return list(dict.fromkeys(result))


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


def _kind_matches(item: dict[str, Any], requested: str) -> bool:
    expected = str(requested or "").strip().lower()
    if not expected:
        return True
    return _first_item_value(item, ("kind",)).strip().lower() == expected


def _search_filters_from_args(args: dict[str, Any]) -> dict[str, str]:
    filters: dict[str, str] = {}
    for key in SEARCH_FILTER_KEYS:
        value = str(args.get(key) or "").strip()
        if value:
            filters[key] = value
    return filters


def _stable_filter_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, default=str)
    return str(value)


def _item_filter_text(item: dict[str, Any], key: str) -> str:
    if key == "kind":
        return _first_item_value(item, ("kind",))
    if key == "schema":
        return _item_schema(item)
    if key == "source":
        return _item_source(item)
    fields = {
        "path": ("path", "source_path", "file", "uri"),
        "symbol": ("symbol", "symbols", "affected_symbols", "name", "class", "handler"),
    }.get(key, (key,))
    values: list[str] = []
    containers = (
        item,
        _nested_dict(item, "metadata"),
        _nested_dict(item, "payload"),
        _nested_dict(item, "provenance"),
    )
    for container in containers:
        for field in fields:
            value = container.get(field)
            rendered = _stable_filter_value(value).strip()
            if rendered:
                values.append(rendered)
    return "\n".join(values)


def _item_matches_filters(item: dict[str, Any], filters: dict[str, str]) -> bool:
    for key, requested in filters.items():
        expected = str(requested or "").strip().lower()
        if not expected:
            continue
        actual = _item_filter_text(item, key).lower()
        if key == "kind":
            if actual != expected:
                return False
        elif expected not in actual:
            return False
    return True


def _filter_backend_search_response(response: dict[str, Any], filters: dict[str, str]) -> dict[str, Any]:
    clean_filters = {key: str(value).strip() for key, value in filters.items() if str(value).strip()}
    if not clean_filters:
        return response
    response_items = _backend_items(response)
    items = [item for item in response_items if _item_matches_filters(item, clean_filters)]
    filtered = dict(response)
    filtered["items"] = items
    filtered["filters"] = clean_filters
    for key, value in clean_filters.items():
        filtered[key] = value
    filtered["count"] = len(items)
    filtered["truncated"] = bool(response.get("truncated")) and len(items) >= len(response_items)
    return filtered


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
    if _first_item_value(item, ("kind",)).lower() == "resolved_bug":
        score += 12
    return score


def _search_memory_items(
    items: list[dict[str, Any]],
    query: str,
    *,
    domain: str,
    filters: dict[str, str],
    limit: int,
    include_raw_chunks: bool,
) -> tuple[list[tuple[int, int, bool, dict[str, Any]]], int, int]:
    requested_domain = _normalize_domain(domain or "all")
    clean_filters = {key: str(value).strip() for key, value in filters.items() if str(value).strip()}
    query_tokens = _tokenize(query)
    raw_omitted = 0
    candidates: list[tuple[int, int, bool, dict[str, Any]]] = []
    for idx, item in enumerate(items):
        item_domain = _item_domain(item)
        raw = _is_raw_chunk_item(item)
        if not _domain_matches(item_domain, requested_domain):
            continue
        if clean_filters and not _item_matches_filters(item, clean_filters):
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
        "kind": _first_item_value(item, ("kind",)),
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
        "stale",
        "stale_reason",
        "updated_at",
        "version",
        "match_fields",
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
        "kind": response.get("kind") or "all",
        "filters": response.get("filters") if isinstance(response.get("filters"), dict) else {},
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


def _graph_node_from_backend(node: dict[str, Any]) -> dict[str, Any]:
    attributes = node.get("attributes") if isinstance(node.get("attributes"), dict) else {}
    result: dict[str, Any] = {
        "id": _compact_text(node.get("id"), max_chars=500),
        "kind": _compact_text(node.get("kind"), max_chars=100),
        "label": _compact_text(node.get("label"), max_chars=500),
        "path": _compact_text(node.get("path"), max_chars=500),
        "attributes": _bounded_payload(attributes),
    }
    return {key: value for key, value in result.items() if value not in ("", None, {})}


def _graph_edge_from_backend(edge: dict[str, Any]) -> dict[str, Any]:
    provenance = edge.get("provenance") if isinstance(edge.get("provenance"), dict) else {}
    result: dict[str, Any] = {
        "id": _compact_text(edge.get("id"), max_chars=500),
        "kind": _compact_text(edge.get("kind"), max_chars=100),
        "from": _compact_text(edge.get("from"), max_chars=500),
        "to": _compact_text(edge.get("to"), max_chars=500),
        "provenance": _bounded_payload(provenance),
    }
    return {key: value for key, value in result.items() if value not in ("", None, {})}


def _tool_result_from_backend_graph_traverse(response: dict[str, Any]) -> dict[str, Any]:
    nodes = [
        _graph_node_from_backend(node)
        for node in response.get("nodes", [])
        if isinstance(node, dict)
    ]
    edges = [
        _graph_edge_from_backend(edge)
        for edge in response.get("edges", [])
        if isinstance(edge, dict)
    ]
    result: dict[str, Any] = {
        "status": "ok",
        "project_id": response.get("project_id"),
        "workspace_binding_id": response.get("workspace_binding_id"),
        "backend_version": response.get("version"),
        "backend_etag": response.get("etag"),
        "artifact_id": response.get("artifact_id"),
        "schema": response.get("schema"),
        "head_commit": response.get("head_commit"),
        "start": response.get("start"),
        "direction": response.get("direction"),
        "max_depth": response.get("max_depth"),
        "limit": response.get("limit"),
        "count": _bounded_int(response.get("count"), default=len(nodes), minimum=0, maximum=1_000_000),
        "edge_count": _bounded_int(response.get("edge_count"), default=len(edges), minimum=0, maximum=1_000_000),
        "truncated": bool(response.get("truncated")),
        "match_fields": response.get("match_fields") if isinstance(response.get("match_fields"), list) else [],
        "provenance": response.get("provenance") if isinstance(response.get("provenance"), dict) else {},
        "server_time": response.get("server_time"),
        "nodes": nodes,
        "edges": edges,
    }
    freshness = response.get("freshness")
    if isinstance(freshness, dict):
        result["freshness"] = freshness
    return {key: value for key, value in result.items() if value not in ("", None)}


def _iter_graph_candidates(value: Any, *, depth: int = 0) -> list[dict[str, Any]]:
    if not isinstance(value, dict) or depth > 2:
        return []
    candidates = [value]
    for key in ("artifact", "payload", "result"):
        child = value.get(key)
        if isinstance(child, dict):
            candidates.extend(_iter_graph_candidates(child, depth=depth + 1))
    return candidates


def _graph_artifact_from_source(source: dict[str, Any]) -> dict[str, Any] | None:
    item = source.get("item")
    if not isinstance(item, dict):
        return None
    item_schema = _item_schema(item)
    for candidate in _iter_graph_candidates(item):
        graph = dict(candidate)
        schema = str(graph.get("schema") or item_schema or "").strip()
        if schema not in GRAPH_ARTIFACT_SCHEMAS:
            continue
        if not any(isinstance(graph.get(key), list) for key in ("routes", "symbols", "edges")):
            continue
        graph["schema"] = schema
        return graph
    return None


def _local_graph_artifacts(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    seen_schemas: set[str] = set()
    for source in sources:
        graph = _graph_artifact_from_source(source)
        if graph is None:
            continue
        schema = str(graph.get("schema") or "")
        if schema in seen_schemas:
            continue
        seen_schemas.add(schema)
        artifact_id = _item_id(source.get("item") if isinstance(source.get("item"), dict) else {})
        if not artifact_id:
            artifact_id = str(source.get("job_id") or schema)
        artifacts.append(
            {
                "artifact": graph,
                "artifact_id": artifact_id,
                "origin": source.get("origin"),
                "cache_version": source.get("cache_version"),
                "cache_updated_at": source.get("cache_updated_at"),
                "job_id": source.get("job_id"),
            }
        )
    return artifacts


def _local_graph_node_kind(node_id: str) -> str:
    if node_id.startswith("route:"):
        return "route"
    if node_id.startswith("table:"):
        return "database_table"
    if node_id.startswith("view:"):
        return "blade_view"
    if node_id.startswith("component:"):
        return "blade_component"
    if node_id.startswith("livewire:"):
        return "livewire_component"
    if node_id.startswith("middleware:"):
        return "middleware"
    if node_id.startswith("config:"):
        return "config"
    if node_id.startswith("env:"):
        return "env"
    return "symbol"


def _local_graph_add_node(
    nodes: dict[str, dict[str, Any]],
    node_id: Any,
    *,
    kind: str,
    label: Any = "",
    path: Any = "",
    attributes: dict[str, Any] | None = None,
) -> None:
    clean_id = _compact_text(node_id, max_chars=500)
    if not clean_id or clean_id in nodes:
        return
    attrs = {key: value for key, value in (attributes or {}).items() if value not in ("", None, [], {})}
    node = {
        "id": clean_id,
        "kind": _compact_text(kind, max_chars=100),
        "label": _compact_text(label or clean_id, max_chars=500),
        "path": _compact_text(path, max_chars=500),
        "attributes": _bounded_payload(attrs),
    }
    nodes[clean_id] = {key: value for key, value in node.items() if value not in ("", None, {})}


def _local_graph_route_id(route: dict[str, Any]) -> str:
    name = str(route.get("name") or "").strip()
    if name:
        return f"route:{name}"
    method_uri = f"{route.get('method', '')} {route.get('uri', '')}".strip()
    return f"route:{method_uri}" if method_uri else ""


def _local_graph_build(artifacts: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str, str, str, str]] = set()

    for artifact_source in artifacts:
        graph = artifact_source["artifact"]
        schema = str(graph.get("schema") or "")
        artifact_id = str(artifact_source.get("artifact_id") or schema)

        for route in graph.get("routes") or []:
            if not isinstance(route, dict):
                continue
            node_id = _local_graph_route_id(route)
            _local_graph_add_node(
                nodes,
                node_id,
                kind="route",
                label=str(route.get("name") or f"{route.get('method', '')} {route.get('uri', '')}").strip(),
                path=route.get("path"),
                attributes={
                    "method": route.get("method"),
                    "uri": route.get("uri"),
                    "handler": route.get("handler"),
                    "name": route.get("name"),
                    "middleware": route.get("middleware"),
                    "line": route.get("line"),
                    "schema": schema,
                    "artifact_id": artifact_id,
                },
            )

        for symbol in graph.get("symbols") or []:
            if not isinstance(symbol, dict):
                continue
            node_id = str(symbol.get("name") or "").strip()
            if not node_id:
                continue
            _local_graph_add_node(
                nodes,
                node_id,
                kind=str(symbol.get("kind") or symbol.get("role") or "symbol"),
                label=symbol.get("short_name") or symbol.get("name"),
                path=symbol.get("path"),
                attributes={
                    key: value
                    for key, value in symbol.items()
                    if key not in {"name", "kind", "short_name", "path"}
                }
                | {"schema": schema, "artifact_id": artifact_id},
            )

        database = graph.get("database") if isinstance(graph.get("database"), dict) else {}
        for table in database.get("tables") or []:
            if not isinstance(table, dict):
                continue
            table_name = str(table.get("table") or "").strip()
            if not table_name:
                continue
            _local_graph_add_node(
                nodes,
                f"table:{table_name}",
                kind="database_table",
                label=f"table:{table_name}",
                path=table.get("path"),
                attributes={
                    "columns": table.get("columns"),
                    "foreign_keys": table.get("foreign_keys"),
                    "indexes": table.get("indexes"),
                    "line": table.get("line"),
                    "schema": schema,
                    "artifact_id": artifact_id,
                },
            )

        for idx, edge in enumerate(graph.get("edges") or []):
            if not isinstance(edge, dict):
                continue
            edge_kind = _compact_text(edge.get("kind"), max_chars=100)
            edge_from = _compact_text(edge.get("from"), max_chars=500)
            edge_to = _compact_text(edge.get("to"), max_chars=500)
            if not edge_kind or not edge_from or not edge_to:
                continue
            provenance = {
                key: value
                for key, value in edge.items()
                if key not in {"id", "kind", "from", "to"} and value not in ("", None, [], {})
            }
            provenance.update({"schema": schema, "artifact_id": artifact_id})
            key = (
                edge_kind,
                edge_from,
                edge_to,
                str(provenance.get("path") or ""),
                str(provenance.get("line") or ""),
            )
            if key in seen_edges:
                continue
            seen_edges.add(key)
            _local_graph_add_node(
                nodes,
                edge_from,
                kind=_local_graph_node_kind(edge_from),
                label=edge_from,
                attributes={"schema": schema, "artifact_id": artifact_id},
            )
            _local_graph_add_node(
                nodes,
                edge_to,
                kind=_local_graph_node_kind(edge_to),
                label=edge_to,
                attributes={"schema": schema, "artifact_id": artifact_id},
            )
            edges.append(
                {
                    "id": _compact_text(edge.get("id") or f"{artifact_id}:edge:{idx}", max_chars=500),
                    "kind": edge_kind,
                    "from": edge_from,
                    "to": edge_to,
                    "provenance": _bounded_payload(provenance),
                }
            )

    return nodes, edges


def _local_graph_match_score(node: dict[str, Any], start: str, tokens: set[str]) -> tuple[int, list[str]]:
    query = start.strip().lower()
    if not query:
        return 0, []
    node_id = str(node.get("id") or "")
    label = str(node.get("label") or "")
    path = str(node.get("path") or "")
    attributes = node.get("attributes") if isinstance(node.get("attributes"), dict) else {}
    rendered_attributes = json.dumps(attributes, sort_keys=True, default=str)
    fields = {
        "id": node_id,
        "label": label,
        "path": path,
        "attributes": rendered_attributes,
    }
    lowered = {key: value.lower() for key, value in fields.items() if value}
    if lowered.get("id") == query:
        return 100, ["id"]
    if lowered.get("id") == f"route:{query}":
        return 95, ["id"]
    if lowered.get("label") == query:
        return 90, ["label"]
    if lowered.get("path") == query or lowered.get("path", "").endswith(query):
        return 80, ["path"]
    for key, value in lowered.items():
        if query in value:
            return 50, [key]
    text = "\n".join(lowered.values())
    if tokens and all(token in text for token in tokens):
        return 30, ["tokens"]
    overlap = sum(1 for token in tokens if token in text)
    if overlap:
        return 10 + overlap, ["tokens"]
    return 0, []


def _local_graph_start_matches(
    nodes: dict[str, dict[str, Any]],
    *,
    start: str,
    limit: int,
) -> tuple[list[dict[str, Any]], list[str], bool]:
    tokens = _tokenize(start)
    scored: list[tuple[int, str, list[str], dict[str, Any]]] = []
    for node_id, node in nodes.items():
        score, fields = _local_graph_match_score(node, start, tokens)
        if score > 0:
            scored.append((score, node_id, fields, node))
    scored.sort(key=lambda item: (-item[0], item[1]))
    selected = scored[:limit]
    match_fields = list(dict.fromkeys(field for _score, _node_id, fields, _node in selected for field in fields))
    return [node for _score, _node_id, _fields, node in selected], match_fields, len(scored) > len(selected)


def _local_graph_traverse(
    nodes: dict[str, dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    start_nodes: list[dict[str, Any]],
    direction: str,
    max_depth: int,
    limit: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    selected_nodes: dict[str, dict[str, Any]] = {}
    selected_edges: list[dict[str, Any]] = []
    seen_edges: set[str] = set()
    queue: deque[tuple[str, int]] = deque()

    for node in start_nodes:
        node_id = str(node.get("id") or "")
        if not node_id:
            continue
        selected_nodes[node_id] = node
        queue.append((node_id, 0))

    truncated = len(selected_nodes) > limit
    while queue:
        current, depth = queue.popleft()
        if depth >= max_depth:
            continue
        for edge in edges:
            edge_id = str(edge.get("id") or f"{edge.get('kind')}:{edge.get('from')}->{edge.get('to')}")
            edge_from = str(edge.get("from") or "")
            edge_to = str(edge.get("to") or "")
            next_node = ""
            if direction in {"out", "any"} and edge_from == current:
                next_node = edge_to
            elif direction in {"in", "any"} and edge_to == current:
                next_node = edge_from
            if not next_node:
                continue
            if edge_id not in seen_edges:
                if len(selected_edges) >= limit:
                    truncated = True
                    continue
                selected_edges.append(edge)
                seen_edges.add(edge_id)
            if next_node not in selected_nodes and next_node in nodes:
                if len(selected_nodes) >= limit:
                    truncated = True
                    continue
                selected_nodes[next_node] = nodes[next_node]
                queue.append((next_node, depth + 1))

    return list(selected_nodes.values())[:limit], selected_edges[:limit], truncated


def _local_graph_traverse_response(
    sources: list[dict[str, Any]],
    *,
    start: str,
    direction: str,
    max_depth: int,
    limit: int,
) -> dict[str, Any] | None:
    artifacts = _local_graph_artifacts(sources)
    if not artifacts:
        return None
    nodes, edges = _local_graph_build(artifacts)
    start_nodes, match_fields, match_truncated = _local_graph_start_matches(nodes, start=start, limit=limit)
    selected_nodes, selected_edges, traverse_truncated = _local_graph_traverse(
        nodes,
        edges,
        start_nodes=start_nodes,
        direction=direction,
        max_depth=max_depth,
        limit=limit,
    )
    primary = artifacts[0]
    graph = primary["artifact"]
    provenance = {
        "source": "local_graph_cache",
        "artifacts": [
            {
                "artifact_id": artifact.get("artifact_id"),
                "schema": artifact["artifact"].get("schema"),
                "origin": artifact.get("origin"),
                "cache_version": artifact.get("cache_version"),
                "cache_updated_at": artifact.get("cache_updated_at"),
                "job_id": artifact.get("job_id"),
            }
            for artifact in artifacts
        ],
    }
    result: dict[str, Any] = {
        "status": "ok",
        "searched_cache_only": True,
        "backend_version": None,
        "artifact_id": primary.get("artifact_id"),
        "schema": graph.get("schema"),
        "head_commit": graph.get("head_commit") or graph.get("workspace_head_commit") or graph.get("indexed_head_commit"),
        "start": start,
        "direction": direction,
        "max_depth": max_depth,
        "limit": limit,
        "count": len(selected_nodes),
        "edge_count": len(selected_edges),
        "candidate_count": len(start_nodes),
        "graph_node_count": len(nodes),
        "graph_edge_count": len(edges),
        "truncated": match_truncated or traverse_truncated,
        "match_fields": match_fields,
        "provenance": _bounded_payload(provenance),
        "freshness": {
            "status": "cached",
            "index_status": "local_graph_cache",
            "workspace_head_commit": graph.get("workspace_head_commit") or graph.get("head_commit"),
        },
        "nodes": selected_nodes,
        "edges": selected_edges,
    }
    return {key: value for key, value in result.items() if value not in ("", None, {})}


def _source_slice_item_from_backend(item: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": _item_id(item),
        "path": item.get("path"),
        "start_line": item.get("start_line"),
        "end_line": item.get("end_line"),
        "language": item.get("language"),
        "symbol": item.get("symbol"),
        "head_commit": item.get("head_commit"),
        "sha256": item.get("sha256"),
        "content_redacted": item.get("content_redacted"),
        "redactions": item.get("redactions"),
        "truncated": bool(item.get("truncated")),
        "retention_class": item.get("retention_class"),
        "policy": item.get("policy"),
        "updated_at": item.get("updated_at"),
        "version": item.get("version"),
        "score": _bounded_int(item.get("score"), default=0, minimum=0, maximum=1_000_000),
    }
    return {key: value for key, value in result.items() if value not in ("", None)}


def _tool_result_from_backend_source_slices(response: dict[str, Any]) -> dict[str, Any]:
    items = [_source_slice_item_from_backend(item) for item in _backend_items(response)]
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
        "path": response.get("path"),
        "symbol": response.get("symbol"),
        "line": response.get("line"),
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


def _evidence_pack_item_from_backend(item: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": _item_id(item),
        "bug_report_id": item.get("bug_report_id"),
        "title": _compact_text(item.get("title"), max_chars=500),
        "summary": _compact_text(item.get("summary"), max_chars=1200),
        "evidence_refs": item.get("evidence_refs") if isinstance(item.get("evidence_refs"), list) else [],
        "graph_refs": item.get("graph_refs") if isinstance(item.get("graph_refs"), list) else [],
        "source_slice_ids": item.get("source_slice_ids") if isinstance(item.get("source_slice_ids"), list) else [],
        "payload": _bounded_payload(item.get("payload")),
        "sha256": item.get("sha256"),
        "redactions": item.get("redactions"),
        "retention_class": item.get("retention_class"),
        "head_commit": item.get("head_commit"),
        "updated_at": item.get("updated_at"),
        "version": item.get("version"),
        "score": _bounded_int(item.get("score"), default=0, minimum=0, maximum=1_000_000),
    }
    return {key: value for key, value in result.items() if value not in ("", None)}


def _tool_result_from_backend_evidence_packs(response: dict[str, Any]) -> dict[str, Any]:
    items = [_evidence_pack_item_from_backend(item) for item in _backend_items(response)]
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


def _tool_result_from_backend_evidence_pack_create(response: dict[str, Any]) -> dict[str, Any]:
    pack = response.get("evidence_pack")
    result: dict[str, Any] = {
        "status": "ok",
        "project_id": response.get("project_id"),
        "workspace_binding_id": response.get("workspace_binding_id"),
        "server_time": response.get("server_time"),
    }
    if isinstance(pack, dict):
        result["evidence_pack"] = _evidence_pack_item_from_backend(pack)
    else:
        result["evidence_pack"] = {}
    return {key: value for key, value in result.items() if value not in ("", None)}


def _tool_result_from_backend_diagnosis_report(response: dict[str, Any]) -> dict[str, Any]:
    report = response.get("diagnosis_report")
    if not isinstance(report, dict):
        report = {}
    report_payload: dict[str, Any] = {
        "id": report.get("id"),
        "bug_report_id": report.get("bug_report_id"),
        "status": report.get("status"),
        "confidence": report.get("confidence"),
        "root_cause": _compact_text(report.get("root_cause"), max_chars=1200),
        "mechanism": _compact_text(report.get("mechanism"), max_chars=1600),
        "evidence_refs": report.get("evidence_refs") if isinstance(report.get("evidence_refs"), list) else [],
        "freshness": report.get("freshness") if isinstance(report.get("freshness"), dict) else {},
        "payload": _bounded_payload(report.get("payload")),
        "redactions": report.get("redactions"),
        "created_at": report.get("created_at"),
        "updated_at": report.get("updated_at"),
        "version": report.get("version"),
    }
    result: dict[str, Any] = {
        "status": "ok",
        "project_id": response.get("project_id"),
        "workspace_binding_id": response.get("workspace_binding_id"),
        "server_time": response.get("server_time"),
        "diagnosis_report": {
            key: value for key, value in report_payload.items() if value not in ("", None)
        },
    }
    return {key: value for key, value in result.items() if value not in ("", None)}


def _tool_result_from_backend_resolved_bug_promote(response: dict[str, Any]) -> dict[str, Any]:
    memory = response.get("resolved_bug_memory")
    if not isinstance(memory, dict):
        memory = {}
    payload = memory.get("payload") if isinstance(memory.get("payload"), dict) else {}
    memory_payload: dict[str, Any] = {
        "id": memory.get("id"),
        "kind": memory.get("kind"),
        "summary": _compact_text(memory.get("summary"), max_chars=1200),
        "payload": _bounded_payload(payload),
        "occurred_at": memory.get("occurred_at"),
        "updated_at": memory.get("updated_at"),
        "version": memory.get("version"),
    }
    result: dict[str, Any] = {
        "status": "ok",
        "project_id": response.get("project_id"),
        "workspace_binding_id": response.get("workspace_binding_id"),
        "diagnosis_report_id": response.get("diagnosis_report_id"),
        "already_promoted": bool(response.get("already_promoted")),
        "server_time": response.get("server_time"),
        "resolved_bug_memory": {
            key: value for key, value in memory_payload.items() if value not in ("", None)
        },
    }
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
