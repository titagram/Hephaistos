from __future__ import annotations

import copy
from io import BytesIO
import hashlib
import inspect
import json
from pathlib import Path

import httpx
import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
OPENAPI_FIXTURE = REPO_ROOT / "docs" / "hades" / "openapi-hades-v1.json"
VALID_WIKI_PAGE_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
CANONICAL_BUNDLE_SCHEMA = (
    REPO_ROOT / "hermes_cli" / "hades_graph_v2" / "contracts" / "bundle.schema.json"
)
CANONICAL_ARTIFACT_SCHEMA = (
    REPO_ROOT / "hermes_cli" / "hades_graph_v2" / "contracts" / "artifact.schema.json"
)


def _canonical_graph_bundle_manifest() -> dict:
    from hermes_cli.hades_graph_v2.bundle import BundleLimits, build_bundle_plan
    from tests.hermes_cli.test_hades_graph_contract import _valid_semantic_artifact

    return copy.deepcopy(
        build_bundle_plan(
            _valid_semantic_artifact(),
            BundleLimits(
                max_chunk_uncompressed_bytes=8 * 1024 * 1024,
                max_bundle_uncompressed_bytes=512 * 1024 * 1024,
            ),
        ).manifest
    )


VALID_GRAPH_BUNDLE_MANIFEST = _canonical_graph_bundle_manifest()


def _expected_self_contained_bundle_schema() -> dict:
    bundle_schema = json.loads(CANONICAL_BUNDLE_SCHEMA.read_text(encoding="utf-8"))
    artifact_schema = json.loads(CANONICAL_ARTIFACT_SCHEMA.read_text(encoding="utf-8"))
    bridge_names = {
        "digest",
        "utcTimestamp",
        "safeInteger",
        "source",
        "project",
        "graphContract",
        "framework",
        "language",
    }
    bundle_defs = bundle_schema["$defs"]
    assert bridge_names == set(bundle_defs) & set(artifact_schema["$defs"])
    for name in bridge_names:
        assert bundle_defs.pop(name) == {"$ref": f"artifact.schema.json#/$defs/{name}"}

    required_artifact_defs = set(bridge_names)
    pending = list(bridge_names)
    while pending:
        value_stack = [artifact_schema["$defs"][pending.pop()]]
        while value_stack:
            value = value_stack.pop()
            if isinstance(value, dict):
                ref = value.get("$ref")
                prefix = "#/$defs/"
                if isinstance(ref, str) and ref.startswith(prefix):
                    name = ref.removeprefix(prefix)
                    if name not in required_artifact_defs:
                        required_artifact_defs.add(name)
                        pending.append(name)
                value_stack.extend(value.values())
            elif isinstance(value, list):
                value_stack.extend(value)

    artifact_defs = {
        name: copy.deepcopy(value)
        for name, value in artifact_schema["$defs"].items()
        if name in required_artifact_defs
    }
    assert not set(artifact_defs) & set(bundle_defs)
    bundle_schema["$defs"] = {
        **artifact_defs,
        **bundle_defs,
    }
    return bundle_schema


CLIENT_ROUTE_CASES = [
    {
        "method_name": "health",
        "http_method": "GET",
        "openapi_path": "/api/hades/v1/health",
        "wire_path": "/api/hades/v1/health",
    },
    {
        "method_name": "capabilities",
        "http_method": "GET",
        "openapi_path": "/api/hades/v1/capabilities",
        "wire_path": "/api/hades/v1/capabilities",
    },
    {
        "method_name": "verify_token",
        "http_method": "POST",
        "openapi_path": "/api/hades/v1/token/verify",
        "wire_path": "/api/hades/v1/token/verify",
        "kwargs": {"project_id": "proj_1"},
        "json_body": {"project_id": "proj_1"},
    },
    {
        "method_name": "register_agent",
        "http_method": "POST",
        "openapi_path": "/api/hades/v1/agents/register",
        "wire_path": "/api/hades/v1/agents/register",
        "kwargs": {
            "project_id": "proj_1",
            "agent_id": "agent_1",
            "label": "dev-machine",
            "platform": "darwin",
            "version": "0.17.0",
            "capabilities": ["read_files"],
        },
        "json_body": {
            "project_id": "proj_1",
            "agent_id": "agent_1",
            "label": "dev-machine",
            "platform": "darwin",
            "version": "0.17.0",
            "capabilities": ["read_files"],
        },
    },
    {
        "method_name": "bind_workspace",
        "http_method": "POST",
        "openapi_path": "/api/hades/v1/workspaces/bind",
        "wire_path": "/api/hades/v1/workspaces/bind",
        "kwargs": {"project_id": "proj_1", "workspace_fingerprint": "wf_1"},
        "json_body": {"project_id": "proj_1", "workspace_fingerprint": "wf_1"},
    },
    {
        "method_name": "unlink_workspace",
        "http_method": "POST",
        "openapi_path": "/api/hades/v1/workspaces/{workspaceBinding}/unlink",
        "wire_path": "/api/hades/v1/workspaces/wb_1/unlink",
        "args": ["wb_1"],
        "kwargs": {"project_id": "proj_1", "agent_id": "agent_1"},
        "json_body": {"project_id": "proj_1", "agent_id": "agent_1"},
    },
    {
        "method_name": "wiki_pages",
        "http_method": "GET",
        "openapi_path": "/api/hades/v1/wiki/pages",
        "wire_path": "/api/hades/v1/wiki/pages",
        "kwargs": {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "source_status": "needs_verification",
            "limit": 20,
        },
        "query": {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "source_status": "needs_verification",
            "limit": "20",
        },
    },
    {
        "method_name": "wiki_page",
        "http_method": "GET",
        "openapi_path": "/api/hades/v1/wiki/pages/{page}",
        "wire_path": f"/api/hades/v1/wiki/pages/{VALID_WIKI_PAGE_ID}",
        "args": [VALID_WIKI_PAGE_ID],
        "kwargs": {"project_id": "proj_1", "workspace_binding_id": "wb_1"},
        "query": {"project_id": "proj_1", "workspace_binding_id": "wb_1"},
    },
    {
        "method_name": "create_wiki_draft",
        "http_method": "POST",
        "openapi_path": "/api/hades/v1/wiki/pages",
        "wire_path": "/api/hades/v1/wiki/pages",
        "kwargs": {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "slug": "technical/overview",
            "title": "Overview",
            "page_type": "technical",
            "content_markdown": "# Overview",
            "evidence_refs": [],
        },
        "json_body": {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "slug": "technical/overview",
            "title": "Overview",
            "page_type": "technical",
            "content_markdown": "# Overview",
            "evidence_refs": [],
        },
    },
    {
        "method_name": "verify_wiki_page",
        "http_method": "POST",
        "openapi_path": "/api/hades/v1/wiki/pages/{page}/verify",
        "wire_path": f"/api/hades/v1/wiki/pages/{VALID_WIKI_PAGE_ID}/verify",
        "args": [VALID_WIKI_PAGE_ID],
        "kwargs": {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "expected_current_revision_id": "rev_1",
            "evidence_refs": [
                {
                    "kind": "file_ref",
                    "path": "src/app.py",
                    "hash": "a" * 64,
                    "claims": [{"claim": "claim", "proof": "proof"}],
                }
            ],
            "verification_note": "Checked against current tree",
        },
        "json_body": {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "expected_current_revision_id": "rev_1",
            "evidence_refs": [
                {
                    "kind": "file_ref",
                    "path": "src/app.py",
                    "hash": "a" * 64,
                    "claims": [{"claim": "claim", "proof": "proof"}],
                }
            ],
            "verification_note": "Checked against current tree",
        },
    },
    {
        "method_name": "list_logbook_entries",
        "http_method": "GET",
        "openapi_path": "/api/hades/v1/logbook/entries",
        "wire_path": "/api/hades/v1/logbook/entries",
        "args": ["proj_1"],
        "kwargs": {"workspace_binding_id": "wb_1"},
        "query": {"project_id": "proj_1", "workspace_binding_id": "wb_1"},
    },
    {
        "method_name": "get_logbook_entry",
        "http_method": "GET",
        "openapi_path": "/api/hades/v1/logbook/entries/{entry}",
        "wire_path": "/api/hades/v1/logbook/entries/entry_1",
        "args": ["proj_1", "entry_1"],
        "kwargs": {"workspace_binding_id": "wb_1"},
        "query": {"project_id": "proj_1", "workspace_binding_id": "wb_1"},
    },
    {
        "method_name": "create_logbook_entry",
        "http_method": "POST",
        "openapi_path": "/api/hades/v1/logbook/entries",
        "wire_path": "/api/hades/v1/logbook/entries",
        "args": ["proj_1"],
        "kwargs": {
            "workspace_binding_id": "wb_1",
            "event_type": "change",
            "summary": "Done",
            "severity": "info",
            "idempotency_key": "client-idempotency-0001",
            "references": [],
        },
        "json_body": {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "event_type": "change",
            "summary": "Done",
            "severity": "info",
            "idempotency_key": "client-idempotency-0001",
            "references": [],
        },
    },
    {
        "method_name": "memory_snapshot",
        "http_method": "GET",
        "openapi_path": "/api/hades/v1/memory/snapshot",
        "wire_path": "/api/hades/v1/memory/snapshot",
        "kwargs": {"project_id": "proj_1", "workspace_binding_id": "wb_1"},
        "query": {"project_id": "proj_1", "workspace_binding_id": "wb_1"},
    },
    {
        "method_name": "memory_search",
        "http_method": "GET",
        "openapi_path": "/api/hades/v1/memory/search",
        "wire_path": "/api/hades/v1/memory/search",
        "kwargs": {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "query": "hades routes",
            "domain": "wiki",
            "limit": 5,
            "include_raw_chunks": False,
        },
        "query": {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "query": "hades routes",
            "domain": "wiki",
            "limit": "5",
            "include_raw_chunks": "0",
        },
    },
    {
        "method_name": "create_memory_proposal",
        "http_method": "POST",
        "openapi_path": "/api/hades/v1/memory/proposals",
        "wire_path": "/api/hades/v1/memory/proposals",
        "kwargs": {
            "project_id": "proj_1",
            "action": "create",
            "summary": "Remember this",
        },
        "json_body": {
            "project_id": "proj_1",
            "action": "create",
            "summary": "Remember this",
        },
    },
    {
        "method_name": "import_memory_bundle",
        "http_method": "POST",
        "openapi_path": "/api/hades/v1/memory/import-bundles",
        "wire_path": "/api/hades/v1/memory/import-bundles",
        "kwargs": {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "source": {"kind": "hades_local_memory"},
            "entries": [
                {
                    "source_hash": "sha256:abc",
                    "summary": "Use backend memory as source of truth.",
                }
            ],
        },
        "json_body": {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "source": {"kind": "hades_local_memory"},
            "entries": [
                {
                    "source_hash": "sha256:abc",
                    "summary": "Use backend memory as source of truth.",
                }
            ],
        },
    },
    {
        "method_name": "create_bug_report",
        "http_method": "POST",
        "openapi_path": "/api/hades/v1/bug-reports",
        "wire_path": "/api/hades/v1/bug-reports",
        "kwargs": {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "title": "Route returns 500",
            "symptom": "Opening the page returns HTTP 500.",
        },
        "json_body": {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "title": "Route returns 500",
            "symptom": "Opening the page returns HTTP 500.",
        },
    },
    {
        "method_name": "get_bug_report",
        "http_method": "GET",
        "openapi_path": "/api/hades/v1/bug-reports/{bugReport}",
        "wire_path": "/api/hades/v1/bug-reports/bug_1",
        "args": ["bug_1"],
        "kwargs": {"project_id": "proj_1", "workspace_binding_id": "wb_1"},
        "query": {"project_id": "proj_1", "workspace_binding_id": "wb_1"},
    },
    {
        "method_name": "create_bug_evidence",
        "http_method": "POST",
        "openapi_path": "/api/hades/v1/bug-evidence",
        "wire_path": "/api/hades/v1/bug-evidence",
        "kwargs": {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "bug_report_id": "bug_1",
            "kind": "stack_trace",
            "summary": "Call to member function active() on null.",
            "payload": {"frames": [{"file": "Controller.php", "line": 42}]},
        },
        "json_body": {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "bug_report_id": "bug_1",
            "kind": "stack_trace",
            "summary": "Call to member function active() on null.",
            "payload": {"frames": [{"file": "Controller.php", "line": 42}]},
        },
    },
    {
        "method_name": "bug_evidence_search",
        "http_method": "GET",
        "openapi_path": "/api/hades/v1/bug-evidence/search",
        "wire_path": "/api/hades/v1/bug-evidence/search",
        "kwargs": {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "query": "active null",
            "kind": "stack_trace",
            "bug_report_id": "bug_1",
            "limit": 5,
        },
        "query": {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "query": "active null",
            "kind": "stack_trace",
            "bug_report_id": "bug_1",
            "limit": "5",
        },
    },
    {
        "method_name": "graph_traverse",
        "http_method": "GET",
        "openapi_path": "/api/hades/v1/graph/traverse",
        "wire_path": "/api/hades/v1/graph/traverse",
        "kwargs": {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "start": "orders.show",
            "direction": "any",
            "max_depth": 2,
            "limit": 10,
        },
        "query": {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "start": "orders.show",
            "direction": "any",
            "max_depth": "2",
            "limit": "10",
        },
    },
    {
        "method_name": "create_diagnosis_report",
        "http_method": "POST",
        "openapi_path": "/api/hades/v1/diagnosis-reports",
        "wire_path": "/api/hades/v1/diagnosis-reports",
        "kwargs": {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "bug_report_id": "bug_1",
            "status": "final",
            "confidence": "high",
            "root_cause": "OrderController dereferences a missing customer relation.",
            "mechanism": "The show action assumes customer is loaded and calls active().",
            "evidence_refs": [{"type": "source_slice", "id": "slice_1"}],
            "freshness": {"status": "current"},
            "payload": {"next_verification": "Run the focused failing test."},
            "redactions": 1,
        },
        "json_body": {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "bug_report_id": "bug_1",
            "status": "final",
            "confidence": "high",
            "root_cause": "OrderController dereferences a missing customer relation.",
            "mechanism": "The show action assumes customer is loaded and calls active().",
            "evidence_refs": [{"type": "source_slice", "id": "slice_1"}],
            "freshness": {"status": "current"},
            "payload": {"next_verification": "Run the focused failing test."},
            "redactions": 1,
        },
    },
    {
        "method_name": "promote_diagnosis_report",
        "http_method": "POST",
        "openapi_path": "/api/hades/v1/diagnosis-reports/{diagnosisReport}/promote",
        "wire_path": "/api/hades/v1/diagnosis-reports/diag_1/promote",
        "args": ["diag_1"],
        "kwargs": {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "verification_status": "test_passed",
            "fix_commit": "abc123",
            "affected_symbols": ["OrderController@show"],
            "regression_tests": ["OrderControllerTest::test_show_missing_customer"],
        },
        "json_body": {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "verification_status": "test_passed",
            "fix_commit": "abc123",
            "affected_symbols": ["OrderController@show"],
            "regression_tests": ["OrderControllerTest::test_show_missing_customer"],
        },
    },
    {
        "method_name": "project_awareness_status",
        "http_method": "GET",
        "openapi_path": "/api/hades/v1/project-awareness/status",
        "wire_path": "/api/hades/v1/project-awareness/status",
        "kwargs": {"project_id": "proj_1", "workspace_binding_id": "wb_1"},
        "query": {"project_id": "proj_1", "workspace_binding_id": "wb_1"},
    },
    {
        "method_name": "bootstrap_project_awareness",
        "http_method": "POST",
        "openapi_path": "/api/hades/v1/project-awareness/bootstrap",
        "wire_path": "/api/hades/v1/project-awareness/bootstrap",
        "kwargs": {"project_id": "proj_1", "workspace_binding_id": "wb_1"},
        "json_body": {"project_id": "proj_1", "workspace_binding_id": "wb_1"},
    },
    {
        "method_name": "pull_jobs",
        "http_method": "GET",
        "openapi_path": "/api/hades/v1/agent/jobs",
        "wire_path": "/api/hades/v1/agent/jobs",
        "kwargs": {"project_id": "proj_1", "workspace_binding_id": "wb_1"},
        "query": {"project_id": "proj_1", "workspace_binding_id": "wb_1"},
    },
    {
        "method_name": "update_job_status",
        "http_method": "POST",
        "openapi_path": "/api/hades/v1/agent/jobs/{job}/status",
        "wire_path": "/api/hades/v1/agent/jobs/job_1/status",
        "args": ["job_1"],
        "kwargs": {"status": "completed", "summary": "done"},
        "json_body": {"status": "completed", "summary": "done"},
    },
    {
        "method_name": "submit_job_result",
        "http_method": "POST",
        "openapi_path": "/api/hades/v1/agent/jobs/{job}/result",
        "wire_path": "/api/hades/v1/agent/jobs/job_1/result",
        "args": ["job_1"],
        "kwargs": {"status": "completed", "result": {"summary": "done"}},
        "json_body": {"status": "completed", "result": {"summary": "done"}},
    },
    {
        "method_name": "artifact_lookup",
        "http_method": "GET",
        "openapi_path": "/api/hades/v1/artifacts/lookup",
        "wire_path": "/api/hades/v1/artifacts/lookup",
        "kwargs": {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "schema": "hades.git_tree.v1",
            "sha256": "a" * 64,
        },
        "query": {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "schema": "hades.git_tree.v1",
            "sha256": "a" * 64,
        },
    },
    {
        "method_name": "upload_artifact",
        "http_method": "POST",
        "openapi_path": "/api/hades/v1/artifacts",
        "wire_path": "/api/hades/v1/artifacts",
        "kwargs": {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "schema": "hades.git_tree.v1",
            "artifact": {"files": []},
            "truncated": False,
            "redactions": 0,
        },
        "json_body": {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "schema": "hades.git_tree.v1",
            "artifact": {"files": []},
            "truncated": False,
            "redactions": 0,
        },
    },
    {
        "method_name": "create_source_slice",
        "http_method": "POST",
        "openapi_path": "/api/hades/v1/source-slices",
        "wire_path": "/api/hades/v1/source-slices",
        "kwargs": {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "path": "app/Http/Controllers/OrderController.php",
            "start_line": 41,
            "end_line": 43,
            "content_redacted": "return ***;",
        },
        "json_body": {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "path": "app/Http/Controllers/OrderController.php",
            "start_line": 41,
            "end_line": 43,
            "content_redacted": "return ***;",
        },
    },
    {
        "method_name": "source_slices",
        "http_method": "GET",
        "openapi_path": "/api/hades/v1/source-slices",
        "wire_path": "/api/hades/v1/source-slices",
        "kwargs": {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "path": "app/Http/Controllers/OrderController.php",
            "line": 42,
        },
        "query": {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "path": "app/Http/Controllers/OrderController.php",
            "line": "42",
        },
    },
    {
        "method_name": "create_evidence_pack",
        "http_method": "POST",
        "openapi_path": "/api/hades/v1/evidence-packs",
        "wire_path": "/api/hades/v1/evidence-packs",
        "kwargs": {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "bug_report_id": "bug_1",
            "title": "Order route 500 evidence pack",
            "summary": "Stack trace, graph edge, and source slice point to OrderController.",
            "evidence_refs": [{"type": "bug_evidence", "id": "evidence_1"}],
            "graph_refs": [
                {
                    "type": "route_handler",
                    "from": "route:orders.show",
                    "to": "OrderController@show",
                }
            ],
            "source_slice_ids": ["slice_1"],
            "payload": {
                "next_verification": "Run OrderControllerTest::test_archived_order_show"
            },
            "redactions": 1,
        },
        "json_body": {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "bug_report_id": "bug_1",
            "title": "Order route 500 evidence pack",
            "summary": "Stack trace, graph edge, and source slice point to OrderController.",
            "evidence_refs": [{"type": "bug_evidence", "id": "evidence_1"}],
            "graph_refs": [
                {
                    "type": "route_handler",
                    "from": "route:orders.show",
                    "to": "OrderController@show",
                }
            ],
            "source_slice_ids": ["slice_1"],
            "payload": {
                "next_verification": "Run OrderControllerTest::test_archived_order_show"
            },
            "redactions": 1,
        },
    },
    {
        "method_name": "evidence_packs",
        "http_method": "GET",
        "openapi_path": "/api/hades/v1/evidence-packs",
        "wire_path": "/api/hades/v1/evidence-packs",
        "kwargs": {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "query": "archived order",
            "bug_report_id": "bug_1",
            "limit": 5,
        },
        "query": {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "query": "archived order",
            "bug_report_id": "bug_1",
            "limit": "5",
        },
    },
    {
        "method_name": "create_causal_pack",
        "http_method": "POST",
        "openapi_path": "/api/hades/v1/causal-packs",
        "wire_path": "/api/hades/v1/causal-packs",
        "kwargs": {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "bug_report_id": "bug_1",
            "bug_id": "bug_booking_overlap",
            "root_cause_id": "booking-overlap-validation-gap",
            "bug_class": "validation",
            "failure_classification": "confirmed",
            "affected_refs": ["symbol:BookingController@store"],
            "freshness": {"status": "current"},
            "awareness": {"diagnosable_without_source": True},
            "evidence_refs": [{"type": "bug_evidence", "id": "evidence_1"}],
            "graph_refs": [{"type": "artifact", "id": "artifact_1"}],
            "source_slice_refs": [{"type": "source_slice", "id": "slice_1"}],
        },
        "json_body": {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "bug_report_id": "bug_1",
            "bug_id": "bug_booking_overlap",
            "root_cause_id": "booking-overlap-validation-gap",
            "bug_class": "validation",
            "failure_classification": "confirmed",
            "affected_refs": ["symbol:BookingController@store"],
            "freshness": {"status": "current"},
            "awareness": {"diagnosable_without_source": True},
            "evidence_refs": [{"type": "bug_evidence", "id": "evidence_1"}],
            "graph_refs": [{"type": "artifact", "id": "artifact_1"}],
            "source_slice_refs": [{"type": "source_slice", "id": "slice_1"}],
        },
    },
    {
        "method_name": "causal_packs",
        "http_method": "GET",
        "openapi_path": "/api/hades/v1/causal-packs",
        "wire_path": "/api/hades/v1/causal-packs",
        "kwargs": {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "query": "booking overlap",
            "limit": 5,
        },
        "query": {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "query": "booking overlap",
            "limit": "5",
        },
    },
    {
        "method_name": "causal_pack",
        "http_method": "GET",
        "openapi_path": "/api/hades/v1/causal-packs/{causalPack}",
        "wire_path": "/api/hades/v1/causal-packs/pack_1",
        "args": ["pack_1"],
        "kwargs": {"project_id": "proj_1", "workspace_binding_id": "wb_1"},
        "query": {"project_id": "proj_1", "workspace_binding_id": "wb_1"},
    },
    {
        "method_name": "replay_causal_pack",
        "http_method": "POST",
        "openapi_path": "/api/hades/v1/causal-packs/{causalPack}/replay",
        "wire_path": "/api/hades/v1/causal-packs/pack_1/replay",
        "args": ["pack_1"],
        "kwargs": {"project_id": "proj_1", "workspace_binding_id": "wb_1"},
        "json_body": {"project_id": "proj_1", "workspace_binding_id": "wb_1"},
    },
    {
        "method_name": "privacy_export",
        "http_method": "GET",
        "openapi_path": "/api/hades/v1/privacy/export",
        "wire_path": "/api/hades/v1/privacy/export",
        "kwargs": {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "include_content": False,
        },
        "query": {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "include_content": "0",
        },
    },
    {
        "method_name": "privacy_delete",
        "http_method": "POST",
        "openapi_path": "/api/hades/v1/privacy/delete",
        "wire_path": "/api/hades/v1/privacy/delete",
        "kwargs": {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "dry_run": False,
            "confirm": True,
        },
        "json_body": {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "dry_run": False,
            "confirm": True,
        },
    },
    {
        "method_name": "privacy_retention_cleanup",
        "http_method": "POST",
        "openapi_path": "/api/hades/v1/privacy/retention-cleanup",
        "wire_path": "/api/hades/v1/privacy/retention-cleanup",
        "kwargs": {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "retention_days": 30,
            "dry_run": True,
            "confirm": False,
        },
        "json_body": {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "retention_days": 30,
            "dry_run": True,
            "confirm": False,
        },
    },
    {
        "method_name": "submit_doctor_report",
        "http_method": "POST",
        "openapi_path": "/api/hades/v1/doctor/reports",
        "wire_path": "/api/hades/v1/doctor/reports",
        "kwargs": {
            "project_id": "proj_1",
            "status": "warning",
            "payload": {"checks": []},
        },
        "json_body": {
            "project_id": "proj_1",
            "status": "warning",
            "payload": {"checks": []},
        },
    },
    {
        "method_name": "list_inbox",
        "http_method": "GET",
        "openapi_path": "/api/hades/v1/persephone/inbox",
        "wire_path": "/api/hades/v1/persephone/inbox",
        "kwargs": {"project_id": "proj_1", "limit": 25},
        "query": {"project_id": "proj_1", "limit": "25"},
    },
    {
        "method_name": "create_inbox_message",
        "http_method": "POST",
        "openapi_path": "/api/hades/v1/persephone/messages",
        "wire_path": "/api/hades/v1/persephone/messages",
        "kwargs": {
            "project_id": "proj_1",
            "event_type": "proposal.reviewed",
            "payload": {"message": "done"},
        },
        "json_body": {
            "project_id": "proj_1",
            "event_type": "proposal.reviewed",
            "payload": {"message": "done"},
        },
    },
    {
        "method_name": "iter_persephone_events",
        "http_method": "GET",
        "openapi_path": "/api/hades/v1/persephone/events",
        "wire_path": "/api/hades/v1/persephone/events",
        "kwargs": {
            "project_id": "proj_1",
            "target_agent_id": "agent_1",
            "cursor": "42",
            "limit": 25,
        },
        "query": {
            "project_id": "proj_1",
            "target_agent_id": "agent_1",
            "cursor": "42",
            "limit": "25",
        },
        "stream": True,
    },
    {
        "method_name": "create_graph_import",
        "http_method": "POST",
        "openapi_path": "/api/hades/v1/graph-imports",
        "wire_path": "/api/hades/v1/graph-imports",
        "args": [VALID_GRAPH_BUNDLE_MANIFEST],
        "json_body": VALID_GRAPH_BUNDLE_MANIFEST,
        "response_json": {
            "import_id": "import-1",
            "attempt_generation": 1,
            "validation_status": "staging",
            "publication_status": "not_requested",
            "missing_chunk_indexes": [0],
            "expires_at": "2026-07-19T12:00:00Z",
        },
        "response_fields": {
            "import_id": "import-1",
            "missing_chunk_indexes": (0,),
        },
    },
    {
        "method_name": "upload_graph_chunk",
        "http_method": "PUT",
        "openapi_path": "/api/hades/v1/graph-imports/{graphImport}/chunks/{index}",
        "wire_path": "/api/hades/v1/graph-imports/import-1/chunks/0",
        "args": [
            "import-1",
            0,
            BytesIO(b"x"),
        ],
        "kwargs_factory": lambda: {
            "headers": __import__(
                "hermes_cli.hades_backend_client", fromlist=["ChunkHeaders"]
            ).ChunkHeaders(
                sha256="a" * 64,
                uncompressed_bytes=1,
                compressed_sha256=hashlib.sha256(b"x").hexdigest(),
                compressed_bytes=1,
            )
        },
        "raw_body": b"x",
        "response_json": {"index": 0, "status": "accepted"},
        "response_fields": {"index": 0, "status": "accepted"},
    },
    {
        "method_name": "complete_graph_import",
        "http_method": "POST",
        "openapi_path": "/api/hades/v1/graph-imports/{graphImport}/complete",
        "wire_path": "/api/hades/v1/graph-imports/import-1/complete",
        "args": ["import-1", "a" * 64],
        "json_body": {"artifact_graph_version": "a" * 64},
        "response_json": {
            "import_id": "import-1",
            "validation_status": "validated",
            "publication_status": "ready",
            "projection_version": "b" * 64,
        },
        "response_fields": {
            "import_id": "import-1",
            "validation_status": "validated",
        },
    },
    {
        "method_name": "graph_import",
        "http_method": "GET",
        "openapi_path": "/api/hades/v1/graph-imports/{graphImport}",
        "wire_path": "/api/hades/v1/graph-imports/import-1",
        "args": ["import-1"],
        "response_json": {
            "import_id": "import-1",
            "validation_status": "validated",
            "publication_status": "ready",
            "received_chunks": 1,
            "expected_chunks": 1,
            "missing_chunk_indexes": [],
            "failure": None,
            "projection_version": "b" * 64,
            "expires_at": None,
        },
        "response_fields": {
            "import_id": "import-1",
            "publication_status": "ready",
        },
    },
    {
        "method_name": "graph_verification_summary",
        "http_method": "GET",
        "openapi_path": "/api/hades/v1/graph/verification-summary",
        "wire_path": "/api/hades/v1/graph/verification-summary",
        "kwargs": {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "projection_version": "b" * 64,
        },
        "query": {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "projection_version": "b" * 64,
        },
    },
]

INTENTIONALLY_UNMAPPED_OPENAPI_ROUTES = {}

INTENTIONALLY_UNMAPPED_CLIENT_METHODS = {
    "presence_heartbeat",
    "presence_list",
    "code_claim_create",
    "code_claim_release",
    "code_claim_detect_conflicts",
}


def test_logbook_client_uses_entries_routes_preserves_project_id_and_accepts_201():
    from hermes_cli.hades_backend_client import HadesBackendClient

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(201, json={"entry": {"id": "entry_1"}})
        return httpx.Response(200, json={"items": []})

    client = HadesBackendClient(
        "https://backend.example", "agent-token", transport=httpx.MockTransport(handler)
    )
    assert client.list_logbook_entries("project_1", workspace_binding_id="binding_1") == {"items": []}
    assert client.get_logbook_entry("project_1", "entry_1", workspace_binding_id="binding_1") == {"items": []}
    assert client.create_logbook_entry(
        "project_1", workspace_binding_id="binding_1", event_type="change",
        summary="Done", severity="info", idempotency_key="client-idempotency-0001", references=[],
    ) == {"entry": {"id": "entry_1"}}
    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/api/hades/v1/logbook/entries"),
        ("GET", "/api/hades/v1/logbook/entries/entry_1"),
        ("POST", "/api/hades/v1/logbook/entries"),
    ]
    assert _query_dict(requests[0]) == {
        "project_id": "project_1", "workspace_binding_id": "binding_1",
    }
    assert _query_dict(requests[1]) == {
        "project_id": "project_1", "workspace_binding_id": "binding_1",
    }
    assert _json_request_body(requests[2]) == {
        "project_id": "project_1",
        "workspace_binding_id": "binding_1",
        "event_type": "change",
        "summary": "Done",
        "severity": "info",
        "idempotency_key": "client-idempotency-0001",
        "references": [],
    }


def test_graph_import_create_is_idempotent_for_200_and_201():
    from hermes_cli.hades_backend_client import HadesBackendClient, GraphImportState

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            201 if len(requests) == 1 else 200,
            json={
                "import_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
                "attempt_generation": 1,
                "validation_status": "staging",
                "publication_status": "not_requested",
                "missing_chunk_indexes": [0],
                "expires_at": "2026-07-19T12:00:00Z",
            },
        )

    client = HadesBackendClient(
        "https://backend.example",
        "agent-token",
        transport=httpx.MockTransport(handler),
    )
    manifest = _canonical_graph_bundle_manifest()

    first = client.create_graph_import(manifest)
    replay = client.create_graph_import(manifest)

    assert (
        first
        == replay
        == GraphImportState(
            import_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
            attempt_generation=1,
            validation_status="staging",
            publication_status="not_requested",
            missing_chunk_indexes=(0,),
            expires_at="2026-07-19T12:00:00Z",
            received_chunks=None,
            expected_chunks=None,
            failure=None,
            projection_version=None,
        )
    )
    assert all(
        request.url.path == "/api/hades/v1/graph-imports" for request in requests
    )
    assert all(_json_request_body(request) == manifest for request in requests)


def test_graph_import_chunk_uses_exact_raw_body_and_digest_headers():
    from hermes_cli.hades_backend_client import (
        ChunkHeaders,
        GraphChunkState,
        HadesBackendClient,
    )

    body = b"\x1f\x8bdeterministic-gzip"
    compressed_sha256 = hashlib.sha256(body).hexdigest()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        assert request.url.path.endswith("/graph-imports/import-1/chunks/2")
        assert request.content == body
        assert (
            request.headers["content-type"] == "application/vnd.hades.graph-chunk+gzip"
        )
        assert request.headers["x-hades-chunk-sha256"] == "a" * 64
        assert request.headers["x-hades-chunk-uncompressed-bytes"] == "42"
        assert request.headers["x-hades-chunk-compressed-sha256"] == compressed_sha256
        assert request.headers["x-hades-chunk-compressed-bytes"] == str(len(body))
        return httpx.Response(201, json={"index": 2, "status": "accepted"})

    client = HadesBackendClient(
        "https://backend.example",
        "agent-token",
        transport=httpx.MockTransport(handler),
    )

    class TrackingBody(BytesIO):
        read_sizes: list[int]

        def __init__(self, value: bytes) -> None:
            super().__init__(value)
            self.read_sizes = []

        def read(self, size: int = -1) -> bytes:
            self.read_sizes.append(size)
            return super().read(size)

    stream = TrackingBody(body)
    state = client.upload_graph_chunk(
        "import-1",
        2,
        stream,
        ChunkHeaders(
            sha256="a" * 64,
            uncompressed_bytes=42,
            compressed_sha256=compressed_sha256,
            compressed_bytes=len(body),
        ),
    )

    assert state == GraphChunkState(index=2, status="accepted")
    assert stream.read_sizes == [len(body) + 1]


def test_graph_import_chunk_conflict_preserves_stable_backend_error():
    from hermes_cli.hades_backend_client import (
        ChunkHeaders,
        HadesBackendClient,
        HadesBackendError,
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json={
                "error": {
                    "code": "chunk_digest_conflict",
                    "message": "Chunk digest does not match the existing upload.",
                }
            },
        )

    client = HadesBackendClient(
        "https://backend.example",
        "agent-token",
        transport=httpx.MockTransport(handler),
    )
    headers = ChunkHeaders("a" * 64, 1, hashlib.sha256(b"x").hexdigest(), 1)

    with pytest.raises(HadesBackendError) as raised:
        client.upload_graph_chunk("import-1", 0, BytesIO(b"x"), headers)

    assert raised.value.status_code == 409
    assert raised.value.code == "chunk_digest_conflict"


def test_graph_import_chunk_replay_accepts_the_typed_200_response():
    from hermes_cli.hades_backend_client import (
        ChunkHeaders,
        GraphChunkState,
        HadesBackendClient,
    )

    client = HadesBackendClient(
        "https://backend.example",
        "agent-token",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={"index": 0, "status": "accepted"},
            )
        ),
    )

    state = client.upload_graph_chunk(
        "import-1",
        0,
        BytesIO(b"x"),
        ChunkHeaders("a" * 64, 1, hashlib.sha256(b"x").hexdigest(), 1),
    )

    assert state == GraphChunkState(index=0, status="accepted")


@pytest.mark.parametrize(
    "import_id",
    [
        "",
        "../other",
        "import/other",
        "import%2Fother",
        "import%252Fother",
        "import?admin=1",
        "import#fragment",
        "import\\other",
        "import\x00other",
        ".",
        "..",
        "a" * 129,
    ],
)
@pytest.mark.parametrize(
    "method_name",
    ["upload_graph_chunk", "complete_graph_import", "graph_import"],
)
def test_graph_import_methods_reject_unsafe_import_ids_before_network(
    method_name,
    import_id,
):
    from hermes_cli.hades_backend_client import ChunkHeaders, HadesBackendClient

    requests: list[httpx.Request] = []
    client = HadesBackendClient(
        "https://backend.example",
        "agent-token",
        transport=httpx.MockTransport(
            lambda request: (
                requests.append(request)
                or httpx.Response(200, json={"unexpected": True})
            )
        ),
    )
    if method_name == "upload_graph_chunk":
        args = (
            import_id,
            0,
            BytesIO(b"x"),
            ChunkHeaders("a" * 64, 1, hashlib.sha256(b"x").hexdigest(), 1),
        )
    elif method_name == "complete_graph_import":
        args = (import_id, "a" * 64)
    else:
        args = (import_id,)

    with pytest.raises(ValueError, match="safe route segment"):
        getattr(client, method_name)(*args)

    assert requests == []


@pytest.mark.parametrize("index", [-1, 512, True, "0"])
def test_graph_import_chunk_rejects_invalid_indexes_before_network(index):
    from hermes_cli.hades_backend_client import ChunkHeaders, HadesBackendClient

    requests: list[httpx.Request] = []
    client = HadesBackendClient(
        "https://backend.example",
        "agent-token",
        transport=httpx.MockTransport(
            lambda request: (
                requests.append(request)
                or httpx.Response(200, json={"unexpected": True})
            )
        ),
    )

    with pytest.raises(ValueError, match="between 0 and 511"):
        client.upload_graph_chunk(
            "import-1",
            index,
            BytesIO(b"x"),
            ChunkHeaders("a" * 64, 1, hashlib.sha256(b"x").hexdigest(), 1),
        )

    assert requests == []


def test_graph_import_create_rejects_v1_manifest_before_network():
    from hermes_cli.hades_backend_client import HadesBackendClient

    requests: list[httpx.Request] = []
    client = HadesBackendClient(
        "https://backend.example",
        "agent-token",
        transport=httpx.MockTransport(
            lambda request: (
                requests.append(request)
                or httpx.Response(200, json={"unexpected": True})
            )
        ),
    )

    manifest = _canonical_graph_bundle_manifest()
    manifest["schema"] = "hades.graph_bundle.v1"
    manifest["artifact_schema"] = "hades.code_graph.v1"

    with pytest.raises(ValueError, match="graph bundle v2"):
        client.create_graph_import(manifest)

    assert requests == []


def test_graph_import_create_rejects_incomplete_manifest_before_network():
    from hermes_cli.hades_backend_client import HadesBackendClient

    manifest = _canonical_graph_bundle_manifest()
    del manifest["graph_contract"]
    requests: list[httpx.Request] = []
    client = HadesBackendClient(
        "https://backend.example",
        "agent-token",
        transport=httpx.MockTransport(
            lambda request: (
                requests.append(request)
                or httpx.Response(201, json={"unexpected": True})
            )
        ),
    )

    with pytest.raises(ValueError, match="canonical graph bundle v2 manifest"):
        client.create_graph_import(manifest)

    assert requests == []


def test_graph_import_create_rejects_manifest_over_4mib_before_network(monkeypatch):
    import hermes_cli.hades_backend_client as client_module
    from hermes_cli.hades_backend_client import HadesBackendClient
    from hermes_cli.hades_graph_v2 import canonical_json_bytes

    manifest = _canonical_graph_bundle_manifest()
    manifest_size = len(canonical_json_bytes(manifest))
    monkeypatch.setattr(
        client_module,
        "_MAX_GRAPH_MANIFEST_BYTES",
        manifest_size - 1,
        raising=False,
    )
    requests: list[httpx.Request] = []
    client = HadesBackendClient(
        "https://backend.example",
        "agent-token",
        transport=httpx.MockTransport(
            lambda request: (
                requests.append(request)
                or httpx.Response(201, json={"unexpected": True})
            )
        ),
    )

    with pytest.raises(ValueError, match="4 MiB"):
        client.create_graph_import(manifest)

    assert client_module._MAX_GRAPH_MANIFEST_BYTES < 4 * 1024 * 1024
    assert requests == []


def test_graph_import_create_rejects_unsafe_response_import_id():
    from hermes_cli.hades_backend_client import HadesBackendClient, HadesBackendError

    client = HadesBackendClient(
        "https://backend.example",
        "agent-token",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                201,
                json={
                    "import_id": "../other",
                    "attempt_generation": 1,
                    "validation_status": "staging",
                    "publication_status": "not_requested",
                    "missing_chunk_indexes": [],
                    "expires_at": "2026-07-19T12:00:00Z",
                },
            )
        ),
    )

    with pytest.raises(HadesBackendError, match="invalid import_id"):
        client.create_graph_import(_canonical_graph_bundle_manifest())


@pytest.mark.parametrize(
    ("method_name", "response", "expected_missing"),
    [
        (
            "create_graph_import",
            {
                "import_id": "import-1",
                "validation_status": "staging",
                "publication_status": "not_requested",
                "missing_chunk_indexes": [],
                "expires_at": None,
            },
            "attempt_generation",
        ),
        (
            "complete_graph_import",
            {
                "import_id": "import-1",
                "validation_status": "validating",
                "publication_status": "not_requested",
            },
            "projection_version",
        ),
        (
            "graph_import",
            {
                "import_id": "import-1",
                "validation_status": "validated",
                "publication_status": "ready",
                "received_chunks": 1,
                "expected_chunks": 1,
                "missing_chunk_indexes": [],
                "failure": None,
                "projection_version": "a" * 64,
            },
            "expires_at",
        ),
    ],
)
def test_graph_import_methods_reject_responses_missing_operation_fields(
    method_name,
    response,
    expected_missing,
):
    from hermes_cli.hades_backend_client import HadesBackendClient, HadesBackendError

    client = HadesBackendClient(
        "https://backend.example",
        "agent-token",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json=response)
        ),
    )
    if method_name == "create_graph_import":
        args = (_canonical_graph_bundle_manifest(),)
    elif method_name == "complete_graph_import":
        args = ("import-1", "a" * 64)
    else:
        args = ("import-1",)

    with pytest.raises(HadesBackendError, match=expected_missing):
        getattr(client, method_name)(*args)


@pytest.mark.parametrize(
    ("method_name", "status", "response", "message"),
    [
        (
            "create_graph_import",
            201,
            {
                "import_id": "import-1",
                "attempt_generation": 1,
                "validation_status": "validated",
                "publication_status": "ready",
                "missing_chunk_indexes": [],
                "expires_at": None,
            },
            "201",
        ),
        (
            "create_graph_import",
            200,
            {
                "import_id": "import-1",
                "attempt_generation": 1,
                "validation_status": "failed",
                "publication_status": "not_requested",
                "missing_chunk_indexes": [],
                "expires_at": None,
            },
            "200",
        ),
        (
            "complete_graph_import",
            200,
            {
                "import_id": "import-1",
                "validation_status": "validating",
                "publication_status": "not_requested",
                "projection_version": None,
            },
            "200",
        ),
        (
            "complete_graph_import",
            202,
            {
                "import_id": "import-1",
                "validation_status": "validated",
                "publication_status": "ready",
                "projection_version": "b" * 64,
            },
            "202",
        ),
    ],
)
def test_graph_import_methods_reject_http_state_correlations(
    method_name,
    status,
    response,
    message,
):
    from hermes_cli.hades_backend_client import HadesBackendClient, HadesBackendError

    client = HadesBackendClient(
        "https://backend.example",
        "agent-token",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(status, json=response)
        ),
    )
    args = (
        (_canonical_graph_bundle_manifest(),)
        if method_name == "create_graph_import"
        else ("import-1", "a" * 64)
    )

    with pytest.raises(HadesBackendError, match=message):
        getattr(client, method_name)(*args)


@pytest.mark.parametrize(
    ("method_name", "status", "response"),
    [
        (
            "create_graph_import",
            201,
            {
                "import_id": "import-1",
                "attempt_generation": 1,
                "validation_status": "staging",
                "publication_status": "not_requested",
                "missing_chunk_indexes": [0],
                "expires_at": "2026-07-19T12:00:00Z",
                "unexpected": True,
            },
        ),
        (
            "upload_graph_chunk",
            201,
            {"index": 0, "status": "accepted", "unexpected": True},
        ),
        (
            "complete_graph_import",
            202,
            {
                "import_id": "import-1",
                "validation_status": "validating",
                "publication_status": "not_requested",
                "projection_version": None,
                "unexpected": True,
            },
        ),
        (
            "graph_import",
            200,
            {
                "import_id": "import-1",
                "validation_status": "validated",
                "publication_status": "ready",
                "received_chunks": 1,
                "expected_chunks": 1,
                "missing_chunk_indexes": [],
                "failure": None,
                "projection_version": "b" * 64,
                "expires_at": None,
                "unexpected": True,
            },
        ),
    ],
)
def test_graph_import_methods_reject_extra_response_fields(
    method_name,
    status,
    response,
):
    from hermes_cli.hades_backend_client import (
        ChunkHeaders,
        HadesBackendClient,
        HadesBackendError,
    )

    client = HadesBackendClient(
        "https://backend.example",
        "agent-token",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(status, json=response)
        ),
    )
    if method_name == "create_graph_import":
        args = (_canonical_graph_bundle_manifest(),)
    elif method_name == "upload_graph_chunk":
        args = (
            "import-1",
            0,
            BytesIO(b"x"),
            ChunkHeaders("a" * 64, 1, hashlib.sha256(b"x").hexdigest(), 1),
        )
    elif method_name == "complete_graph_import":
        args = ("import-1", "a" * 64)
    else:
        args = ("import-1",)

    with pytest.raises(HadesBackendError, match="unexpected fields"):
        getattr(client, method_name)(*args)


@pytest.mark.parametrize("method_name", ["complete_graph_import", "graph_import"])
def test_graph_import_methods_reject_response_for_a_different_import(method_name):
    from hermes_cli.hades_backend_client import HadesBackendClient, HadesBackendError

    response = {
        "import_id": "import-2",
        "validation_status": "validated",
        "publication_status": "ready",
        "projection_version": "a" * 64,
    }
    if method_name == "graph_import":
        response.update({
            "received_chunks": 1,
            "expected_chunks": 1,
            "missing_chunk_indexes": [],
            "failure": None,
            "expires_at": None,
        })
    client = HadesBackendClient(
        "https://backend.example",
        "agent-token",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json=response)
        ),
    )
    args = (
        ("import-1", "a" * 64)
        if method_name == "complete_graph_import"
        else ("import-1",)
    )

    with pytest.raises(HadesBackendError, match="does not match request"):
        getattr(client, method_name)(*args)


@pytest.mark.parametrize(
    ("received", "expected", "missing"),
    [
        (513, 513, []),
        (2, 1, []),
        (1, 3, [2]),
    ],
)
def test_graph_import_get_rejects_impossible_chunk_accounting(
    received,
    expected,
    missing,
):
    from hermes_cli.hades_backend_client import HadesBackendClient, HadesBackendError

    client = HadesBackendClient(
        "https://backend.example",
        "agent-token",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={
                    "import_id": "import-1",
                    "validation_status": "staging",
                    "publication_status": "not_requested",
                    "received_chunks": received,
                    "expected_chunks": expected,
                    "missing_chunk_indexes": missing,
                    "failure": None,
                    "projection_version": None,
                    "expires_at": "2026-07-19T12:00:00Z",
                },
            )
        ),
    )

    with pytest.raises(HadesBackendError, match="chunk accounting"):
        client.graph_import("import-1")


def test_graph_import_create_rejects_missing_index_outside_manifest():
    from hermes_cli.hades_backend_client import HadesBackendClient, HadesBackendError

    manifest = _canonical_graph_bundle_manifest()
    assert len(manifest["chunks"]) == 1
    client = HadesBackendClient(
        "https://backend.example",
        "agent-token",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                201,
                json={
                    "import_id": "import-1",
                    "attempt_generation": 1,
                    "validation_status": "staging",
                    "publication_status": "not_requested",
                    "missing_chunk_indexes": [1],
                    "expires_at": "2026-07-19T12:00:00Z",
                },
            )
        ),
    )

    with pytest.raises(HadesBackendError, match="missing chunk indexes"):
        client.create_graph_import(manifest)


def test_graph_import_create_rejects_non_rfc3339_expiry():
    from hermes_cli.hades_backend_client import HadesBackendClient, HadesBackendError

    client = HadesBackendClient(
        "https://backend.example",
        "agent-token",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                201,
                json={
                    "import_id": "import-1",
                    "attempt_generation": 1,
                    "validation_status": "staging",
                    "publication_status": "not_requested",
                    "missing_chunk_indexes": [0],
                    "expires_at": "tomorrow",
                },
            )
        ),
    )

    with pytest.raises(HadesBackendError, match="RFC3339"):
        client.create_graph_import(_canonical_graph_bundle_manifest())


@pytest.mark.parametrize(
    "response",
    [
        {
            "import_id": "import-1",
            "validation_status": "staging",
            "publication_status": "not_requested",
            "received_chunks": 0,
            "expected_chunks": 1,
            "missing_chunk_indexes": [0],
            "failure": None,
            "projection_version": None,
            "expires_at": "2026-07-19T12:00:00Z",
        },
        {
            "import_id": "import-1",
            "validation_status": "validating",
            "publication_status": "not_requested",
            "received_chunks": 1,
            "expected_chunks": 1,
            "missing_chunk_indexes": [],
            "failure": {
                "code": "graph_validation_transient",
                "details": {"retry": 2},
            },
            "projection_version": None,
            "expires_at": None,
        },
        {
            "import_id": "import-1",
            "validation_status": "validated",
            "publication_status": "failed",
            "received_chunks": 1,
            "expected_chunks": 1,
            "missing_chunk_indexes": [],
            "failure": {
                "code": "graph_projection_failed",
                "details": {"retryable": True},
            },
            "projection_version": None,
            "expires_at": None,
        },
        {
            "import_id": "import-1",
            "validation_status": "failed",
            "publication_status": "not_requested",
            "received_chunks": 1,
            "expected_chunks": 1,
            "missing_chunk_indexes": [],
            "failure": {
                "code": "graph_validation_failed",
                "details": {},
            },
            "projection_version": None,
            "expires_at": None,
        },
        {
            "import_id": "import-1",
            "validation_status": "stale",
            "publication_status": "not_requested",
            "received_chunks": 0,
            "expected_chunks": 1,
            "missing_chunk_indexes": [0],
            "failure": None,
            "projection_version": None,
            "expires_at": None,
        },
    ],
)
def test_graph_import_get_accepts_documented_transient_and_terminal_states(response):
    from hermes_cli.hades_backend_client import HadesBackendClient

    client = HadesBackendClient(
        "https://backend.example",
        "agent-token",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json=response)
        ),
    )

    state = client.graph_import("import-1")

    assert state.validation_status == response["validation_status"]
    assert state.publication_status == response["publication_status"]


@pytest.mark.parametrize(
    "response",
    [
        {
            "validation_status": "staging",
            "publication_status": "not_requested",
            "failure": None,
            "projection_version": None,
            "expires_at": None,
        },
        {
            "validation_status": "validating",
            "publication_status": "queued",
            "failure": None,
            "projection_version": None,
            "expires_at": None,
        },
        {
            "validation_status": "validated",
            "publication_status": "ready",
            "failure": None,
            "projection_version": None,
            "expires_at": None,
        },
        {
            "validation_status": "failed",
            "publication_status": "not_requested",
            "failure": None,
            "projection_version": None,
            "expires_at": None,
        },
        {
            "validation_status": "validated",
            "publication_status": "queued",
            "failure": {"code": "graph_projection_failed", "details": {}},
            "projection_version": None,
            "expires_at": None,
        },
    ],
)
def test_graph_import_get_rejects_illegal_state_combinations(response):
    from hermes_cli.hades_backend_client import HadesBackendClient, HadesBackendError

    payload = {
        "import_id": "import-1",
        "received_chunks": 1,
        "expected_chunks": 1,
        "missing_chunk_indexes": [],
        **response,
    }
    client = HadesBackendClient(
        "https://backend.example",
        "agent-token",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json=payload)
        ),
    )

    with pytest.raises(HadesBackendError, match="illegal state"):
        client.graph_import("import-1")


@pytest.mark.parametrize(
    ("method_name", "unexpected_status", "response"),
    [
        (
            "create_graph_import",
            202,
            {
                "import_id": "import-1",
                "attempt_generation": 1,
                "validation_status": "staging",
                "publication_status": "not_requested",
                "missing_chunk_indexes": [],
                "expires_at": "2026-07-19T12:00:00Z",
            },
        ),
        (
            "upload_graph_chunk",
            202,
            {"index": 0, "status": "accepted"},
        ),
        (
            "complete_graph_import",
            201,
            {
                "import_id": "import-1",
                "validation_status": "validating",
                "publication_status": "not_requested",
                "projection_version": None,
            },
        ),
        (
            "graph_import",
            201,
            {
                "import_id": "import-1",
                "validation_status": "validated",
                "publication_status": "ready",
                "received_chunks": 0,
                "expected_chunks": 0,
                "missing_chunk_indexes": [],
                "failure": None,
                "projection_version": "a" * 64,
                "expires_at": None,
            },
        ),
    ],
)
def test_graph_import_methods_reject_uncontracted_success_statuses(
    method_name,
    unexpected_status,
    response,
):
    from hermes_cli.hades_backend_client import (
        ChunkHeaders,
        HadesBackendClient,
        HadesBackendError,
    )

    client = HadesBackendClient(
        "https://backend.example",
        "agent-token",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(unexpected_status, json=response)
        ),
    )
    if method_name == "create_graph_import":
        args = (_canonical_graph_bundle_manifest(),)
    elif method_name == "upload_graph_chunk":
        args = (
            "import-1",
            0,
            BytesIO(b"x"),
            ChunkHeaders("a" * 64, 1, hashlib.sha256(b"x").hexdigest(), 1),
        )
    elif method_name == "complete_graph_import":
        args = ("import-1", "a" * 64)
    else:
        args = ("import-1",)

    with pytest.raises(HadesBackendError, match="unexpected success status"):
        getattr(client, method_name)(*args)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"uncompressed_bytes": 8 * 1024 * 1024 + 1},
        {"compressed_bytes": 8 * 1024 * 1024 + 1},
    ],
)
def test_graph_chunk_headers_reject_values_above_the_contract_ceiling(kwargs):
    from hermes_cli.hades_backend_client import ChunkHeaders

    values = {
        "sha256": "a" * 64,
        "uncompressed_bytes": 1,
        "compressed_sha256": "b" * 64,
        "compressed_bytes": 1,
    }
    values.update(kwargs)

    with pytest.raises(ValueError, match="at most 8 MiB"):
        ChunkHeaders(**values)


def test_graph_import_errors_redact_all_structured_secret_fields():
    from hermes_cli.hades_backend_client import (
        ChunkHeaders,
        HadesBackendClient,
        HadesBackendError,
    )

    secret = "hades_agent_01ARZ3NDEKTSV4RRFFQ69G5FAV|" + "S" * 64
    client = HadesBackendClient(
        "https://backend.example",
        "agent-token",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                409,
                json={
                    "error": {
                        "code": "chunk_digest_conflict",
                        "message": f"conflict for {secret}",
                        "next_step": f"do not reuse token={secret}",
                        "details": {
                            "received": f"Bearer {secret}",
                            f"token={secret}": "redact dictionary keys too",
                        },
                    }
                },
            )
        ),
    )

    with pytest.raises(HadesBackendError) as raised:
        client.upload_graph_chunk(
            "import-1",
            0,
            BytesIO(b"x"),
            ChunkHeaders("a" * 64, 1, hashlib.sha256(b"x").hexdigest(), 1),
        )

    error = raised.value
    assert secret not in str(error)
    assert secret not in (error.next_step or "")
    assert secret not in json.dumps(error.details)
    assert error.status_code == 409
    assert error.code == "chunk_digest_conflict"


def test_graph_import_rejects_unsafe_http_error_code_without_leaking_it():
    from hermes_cli.hades_backend_client import (
        ChunkHeaders,
        HadesBackendClient,
        HadesBackendError,
    )

    secret = "hades_agent_01ARZ3NDEKTSV4RRFFQ69G5FAV|" + "S" * 64
    client = HadesBackendClient(
        "https://backend.example",
        "agent-token",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                409,
                json={
                    "error": {
                        "code": f"token={secret}",
                        "message": "bad error contract",
                    }
                },
            )
        ),
    )

    with pytest.raises(HadesBackendError) as raised:
        client.upload_graph_chunk(
            "import-1",
            0,
            BytesIO(b"x"),
            ChunkHeaders("a" * 64, 1, hashlib.sha256(b"x").hexdigest(), 1),
        )

    assert secret not in str(raised.value)
    assert secret not in (raised.value.code or "")
    assert raised.value.code is None


def test_graph_import_success_redacts_failure_details_and_preserves_safe_code():
    from hermes_cli.hades_backend_client import HadesBackendClient

    secret = "hades_agent_01ARZ3NDEKTSV4RRFFQ69G5FAV|" + "S" * 64
    client = HadesBackendClient(
        "https://backend.example",
        "agent-token",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={
                    "import_id": "import-1",
                    "validation_status": "failed",
                    "publication_status": "not_requested",
                    "received_chunks": 1,
                    "expected_chunks": 1,
                    "missing_chunk_indexes": [],
                    "failure": {
                        "code": "graph_validation_failed",
                        "details": {"token": f"Bearer {secret}"},
                    },
                    "projection_version": None,
                    "expires_at": None,
                },
            )
        ),
    )

    state = client.graph_import("import-1")

    assert state.failure is not None
    assert state.failure["code"] == "graph_validation_failed"
    assert secret not in json.dumps(state.failure)


def test_graph_import_success_rejects_unsafe_failure_code_without_leaking_it():
    from hermes_cli.hades_backend_client import HadesBackendClient, HadesBackendError

    secret = "hades_agent_01ARZ3NDEKTSV4RRFFQ69G5FAV|" + "S" * 64
    client = HadesBackendClient(
        "https://backend.example",
        "agent-token",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={
                    "import_id": "import-1",
                    "validation_status": "failed",
                    "publication_status": "not_requested",
                    "received_chunks": 1,
                    "expected_chunks": 1,
                    "missing_chunk_indexes": [],
                    "failure": {
                        "code": f"token={secret}",
                        "details": {},
                    },
                    "projection_version": None,
                    "expires_at": None,
                },
            )
        ),
    )

    with pytest.raises(HadesBackendError) as raised:
        client.graph_import("import-1")

    assert "invalid failure code" in str(raised.value)
    assert secret not in str(raised.value)


def test_graph_import_complete_and_poll_keep_validation_and_publication_separate():
    from hermes_cli.hades_backend_client import HadesBackendClient

    responses = iter([
        {
            "import_id": "import-1",
            "validation_status": "validating",
            "publication_status": "not_requested",
            "projection_version": None,
        },
        {
            "import_id": "import-1",
            "validation_status": "validated",
            "publication_status": "queued",
            "received_chunks": 3,
            "expected_chunks": 3,
            "missing_chunk_indexes": [],
            "failure": None,
            "projection_version": None,
            "expires_at": None,
        },
        {
            "import_id": "import-1",
            "validation_status": "validated",
            "publication_status": "ready",
            "received_chunks": 3,
            "expected_chunks": 3,
            "missing_chunk_indexes": [],
            "failure": None,
            "projection_version": "c" * 64,
            "expires_at": None,
        },
    ])

    def handler(request: httpx.Request) -> httpx.Response:
        payload = next(responses)
        if request.method == "POST":
            assert _json_request_body(request) == {"artifact_graph_version": "a" * 64}
            return httpx.Response(202, json=payload)
        return httpx.Response(200, json=payload)

    client = HadesBackendClient(
        "https://backend.example",
        "agent-token",
        transport=httpx.MockTransport(handler),
    )

    validating = client.complete_graph_import("import-1", "a" * 64)
    validated = client.graph_import("import-1")
    ready = client.graph_import("import-1")

    assert (validating.validation_status, validating.publication_status) == (
        "validating",
        "not_requested",
    )
    assert (validated.validation_status, validated.publication_status) == (
        "validated",
        "queued",
    )
    assert ready.is_ready
    assert ready.projection_version == "c" * 64


def test_openapi_graph_import_wire_contract_is_exact():
    spec = _openapi_spec()
    paths = spec["paths"]
    schemas = spec["components"]["schemas"]
    create = paths["/api/hades/v1/graph-imports"]["post"]
    chunk = paths["/api/hades/v1/graph-imports/{graphImport}/chunks/{index}"]["put"]
    complete = paths["/api/hades/v1/graph-imports/{graphImport}/complete"]["post"]
    get = paths["/api/hades/v1/graph-imports/{graphImport}"]["get"]

    assert create["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/GraphImportCreateResponse"
    }
    assert create["responses"]["201"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/GraphImportCreateResponse"
    }
    for status in ("200", "201"):
        assert chunk["responses"][status]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/GraphChunkAcceptedResponse"
        }
    for status in ("200", "202"):
        assert complete["responses"][status]["content"]["application/json"][
            "schema"
        ] == {"$ref": "#/components/schemas/GraphImportCompletionResponse"}
    assert get["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/GraphImportStatusResponse"
    }

    graph_import_parameters = [
        next(
            parameter
            for parameter in operation["parameters"]
            if parameter["name"] == "graphImport"
        )
        for operation in (chunk, complete, get)
    ]
    assert all(
        parameter["schema"] == {"$ref": "#/components/schemas/GraphImportId"}
        for parameter in graph_import_parameters
    )
    index_parameter = next(
        parameter for parameter in chunk["parameters"] if parameter["name"] == "index"
    )
    assert index_parameter["schema"] == {
        "type": "integer",
        "minimum": 0,
        "maximum": 511,
    }
    assert chunk["requestBody"]["content"]["application/vnd.hades.graph-chunk+gzip"][
        "schema"
    ] == {"type": "string", "format": "binary"}

    assert schemas["GraphChunkAcceptedResponse"] == {
        "type": "object",
        "required": ["index", "status"],
        "additionalProperties": False,
        "properties": {
            "index": {"type": "integer", "minimum": 0, "maximum": 511},
            "status": {"const": "accepted"},
        },
    }
    assert schemas["GraphImportCreateResponse"]["required"] == [
        "import_id",
        "attempt_generation",
        "validation_status",
        "publication_status",
        "missing_chunk_indexes",
        "expires_at",
    ]
    assert schemas["GraphImportCompletionResponse"]["required"] == [
        "import_id",
        "validation_status",
        "publication_status",
        "projection_version",
    ]
    assert schemas["GraphImportStatusResponse"]["required"] == [
        "import_id",
        "validation_status",
        "publication_status",
        "received_chunks",
        "expected_chunks",
        "missing_chunk_indexes",
        "failure",
        "projection_version",
        "expires_at",
    ]
    assert all(
        schemas[name]["additionalProperties"] is False
        for name in (
            "GraphImportCreateResponse",
            "GraphImportCompletionResponse",
            "GraphImportStatusResponse",
        )
    )

    assert schemas["GraphBundleManifest"] == _expected_self_contained_bundle_schema()
    assert schemas["GraphImportFailure"] == {
        "type": "object",
        "required": ["code", "details"],
        "additionalProperties": False,
        "properties": {
            "code": {
                "type": "string",
                "pattern": "^[a-z][a-z0-9_]{0,127}$",
            },
            "details": {"type": ["object", "null"]},
        },
    }

    for header_name in (
        "X-Hades-Chunk-Uncompressed-Bytes",
        "X-Hades-Chunk-Compressed-Bytes",
    ):
        parameter = next(
            candidate
            for candidate in chunk["parameters"]
            if candidate["name"] == header_name
        )
        assert parameter["schema"]["maximum"] == 8 * 1024 * 1024


def test_openapi_graph_bundle_manifest_resolves_and_validates_offline():
    from jsonschema import Draft202012Validator

    schema = _openapi_spec()["components"]["schemas"]["GraphBundleManifest"]
    Draft202012Validator.check_schema(schema)

    external_refs = []

    def collect_external_refs(value):
        if isinstance(value, dict):
            ref = value.get("$ref")
            if isinstance(ref, str) and not ref.startswith("#/"):
                external_refs.append(ref)
            for child in value.values():
                collect_external_refs(child)
        elif isinstance(value, list):
            for child in value:
                collect_external_refs(child)

    collect_external_refs(schema)

    assert external_refs == []
    Draft202012Validator(schema).validate(_canonical_graph_bundle_manifest())


def test_openapi_graph_import_errors_are_closed_per_operation_and_status():
    spec = _openapi_spec()
    paths = spec["paths"]
    schemas = spec["components"]["schemas"]
    operations = {
        "create": paths["/api/hades/v1/graph-imports"]["post"],
        "chunk": paths["/api/hades/v1/graph-imports/{graphImport}/chunks/{index}"][
            "put"
        ],
        "complete": paths["/api/hades/v1/graph-imports/{graphImport}/complete"]["post"],
        "get": paths["/api/hades/v1/graph-imports/{graphImport}"]["get"],
    }
    expected = {
        ("create", "404"): (
            "GraphImportNotFoundErrorResponse",
            {"graph_import_not_found"},
        ),
        ("create", "409"): (
            "GraphManifestConflictErrorResponse",
            {"graph_import_manifest_conflict"},
        ),
        ("create", "422"): ("GraphManifestErrorResponse", {"graph_manifest_invalid"}),
        ("chunk", "404"): (
            "GraphImportNotFoundErrorResponse",
            {"graph_import_not_found"},
        ),
        ("chunk", "409"): (
            "GraphChunkConflictErrorResponse",
            {"chunk_digest_conflict"},
        ),
        ("chunk", "422"): (
            "GraphChunkValidationErrorResponse",
            {
                "graph_chunk_invalid",
                "graph_chunk_too_large",
                "graph_import_not_staging",
            },
        ),
        ("complete", "404"): (
            "GraphImportNotFoundErrorResponse",
            {"graph_import_not_found"},
        ),
        ("complete", "409"): (
            "GraphImportFailedErrorResponse",
            {"graph_import_failed"},
        ),
        ("complete", "410"): ("GraphImportStaleErrorResponse", {"graph_import_stale"}),
        ("complete", "422"): (
            "GraphCompleteValidationErrorResponse",
            {"graph_manifest_invalid", "graph_import_incomplete"},
        ),
        ("get", "404"): (
            "GraphImportNotFoundErrorResponse",
            {"graph_import_not_found"},
        ),
    }

    for (operation_name, status), (schema_name, codes) in expected.items():
        response_schema = operations[operation_name]["responses"][status]["content"][
            "application/json"
        ]["schema"]
        assert response_schema == {"$ref": f"#/components/schemas/{schema_name}"}
        schema = schemas[schema_name]
        assert schema["additionalProperties"] is False
        error = schema["properties"]["error"]
        assert error["additionalProperties"] is False
        code_schema = error["properties"]["code"]
        actual_codes = (
            {code_schema["const"]}
            if "const" in code_schema
            else set(code_schema["enum"])
        )
        assert actual_codes == codes


def _openapi_routes() -> dict[tuple[str, str], dict]:
    spec = json.loads(OPENAPI_FIXTURE.read_text(encoding="utf-8"))
    return {
        (method.upper(), path): operation
        for path, methods in spec["paths"].items()
        for method, operation in methods.items()
    }


def _openapi_spec() -> dict:
    return json.loads(OPENAPI_FIXTURE.read_text(encoding="utf-8"))


def _json_request_body(request: httpx.Request) -> dict:
    if not request.content:
        return {}
    return json.loads(request.content.decode("utf-8"))


def _query_dict(request: httpx.Request) -> dict[str, str]:
    return {key: request.url.params[key] for key in request.url.params}


@pytest.mark.parametrize(
    "case",
    CLIENT_ROUTE_CASES,
    ids=[case["method_name"] for case in CLIENT_ROUTE_CASES],
)
def test_client_methods_are_backed_by_openapi_fixture(case):
    from hermes_cli.hades_backend_client import HadesBackendClient

    routes = _openapi_routes()
    assert (case["http_method"], case["openapi_path"]) in routes

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.method == case["http_method"]
        assert request.url.path == case["wire_path"]
        assert request.headers["authorization"] == "Bearer agent-token"
        if "json_body" in case:
            assert _json_request_body(request) == case["json_body"]
        elif "raw_body" in case:
            assert request.content == case["raw_body"]
        else:
            assert request.content == b""
        if "query" in case:
            assert _query_dict(request) == case["query"]
        if case.get("stream"):
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text='data: {"ok":true}\n\n',
            )
        return httpx.Response(200, json=case.get("response_json", {"ok": True}))

    client = HadesBackendClient(
        "https://backend.example",
        "agent-token",
        transport=httpx.MockTransport(handler),
    )

    kwargs = dict(case.get("kwargs") or {})
    if "kwargs_factory" in case:
        kwargs.update(case["kwargs_factory"]())
    response = getattr(client, case["method_name"])(*(case.get("args") or ()), **kwargs)
    if case.get("stream"):
        response = list(response)

    if "response_fields" in case:
        assert {
            name: getattr(response, name) for name in case["response_fields"]
        } == case["response_fields"]
    else:
        assert response == ([{"ok": True}] if case.get("stream") else {"ok": True})
    assert seen


def test_client_route_coverage_is_explicit_against_openapi_fixture():
    from hermes_cli.hades_backend_client import HadesBackendClient

    public_client_methods = {
        name
        for name, member in inspect.getmembers(
            HadesBackendClient, predicate=inspect.isfunction
        )
        if not name.startswith("_") and name != "close"
    }
    covered_client_methods = {case["method_name"] for case in CLIENT_ROUTE_CASES}
    covered_routes = {
        (case["http_method"], case["openapi_path"]) for case in CLIENT_ROUTE_CASES
    }
    fixture_routes = {
        route for route in _openapi_routes() if route[1].startswith("/api/hades/v1/")
    }

    assert (
        public_client_methods
        == covered_client_methods | INTENTIONALLY_UNMAPPED_CLIENT_METHODS
    )
    assert fixture_routes == covered_routes | set(INTENTIONALLY_UNMAPPED_OPENAPI_ROUTES)


def test_openapi_capability_contract_matches_authenticated_backend_discovery():
    spec = _openapi_spec()
    operation = spec["paths"]["/api/hades/v1/capabilities"]["get"]
    schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    capability_schema = spec["components"]["schemas"]["CapabilitiesResponse"]

    assert schema == {"$ref": "#/components/schemas/CapabilitiesResponse"}
    assert operation["responses"]["401"] == {
        "$ref": "#/components/responses/Unauthorized"
    }
    assert "capability_names" in capability_schema["required"]
    assert capability_schema["properties"]["persephone_agent_queue_v1"]["const"] is True
    assert (
        "verify_project_wiki"
        in capability_schema["properties"]["capability_names"]["description"]
    )
    assert spec["paths"]["/api/hades/v1/token/verify"]["post"]["responses"]["401"] == {
        "$ref": "#/components/responses/Unauthorized"
    }


def test_openapi_wiki_verification_contract_requires_bounded_claim_mappings():
    spec = _openapi_spec()
    operation = spec["paths"]["/api/hades/v1/wiki/pages/{page}/verify"]["post"]
    schema = operation["requestBody"]["content"]["application/json"]["schema"]
    evidence = schema["properties"]["evidence_refs"]
    ref = evidence["items"]
    claims = ref["properties"]["claims"]
    mapping = claims["items"]

    assert evidence["maxItems"] == 80
    assert set(ref["properties"]) == {
        "kind",
        "schema",
        "sha256",
        "hash",
        "path",
        "claims",
    }
    assert ref["additionalProperties"] is False
    assert claims["minItems"] == 1
    assert claims["maxItems"] == 8
    assert mapping["required"] == ["claim", "proof"]
    assert mapping["additionalProperties"] is False
    assert mapping["properties"]["claim"]["maxLength"] == 500
    assert mapping["properties"]["proof"]["maxLength"] == 500
    forbidden = spec["paths"]["/api/hades/v1/wiki/pages/{page}/verify"]["post"][
        "responses"
    ]["403"]
    assert forbidden["content"]["application/json"]["example"]["error"]["code"] == (
        "wiki_verification_capability_not_allowed"
    )
    assert operation["responses"]["422"] == {"$ref": "#/components/responses/Error"}


def test_openapi_artifact_hash_contract_matches_backend_canonical_rules():
    spec = _openapi_spec()
    schemas = spec["components"]["schemas"]
    description = schemas["ArtifactUploadRequest"]["properties"]["sha256"][
        "description"
    ]
    error_codes = schemas["ArtifactUploadErrorResponse"]["properties"]["error"][
        "properties"
    ]["code"]["enum"]

    assert "recursively key-sorted compact JSON" in description
    assert "preserving list order, Unicode, slashes, and zero fractions" in description
    assert "artifact_hash_mismatch" in error_codes


def test_wiki_client_methods_use_exact_bounded_backend_contract():
    from hermes_cli.hades_backend_client import HadesBackendClient

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    client = HadesBackendClient(
        "https://backend.example",
        "agent-token",
        transport=httpx.MockTransport(handler),
    )

    assert client.wiki_pages(
        project_id="p",
        workspace_binding_id="w",
        source_status="needs_verification",
        limit=20,
    ) == {"ok": True}
    assert client.wiki_page(
        f" {VALID_WIKI_PAGE_ID} ",
        project_id="p",
        workspace_binding_id="w",
    ) == {"ok": True}
    assert client.create_wiki_draft(
        project_id="p",
        workspace_binding_id="w",
        slug="technical/overview",
        title="Overview",
        page_type="technical",
        content_markdown="# Overview",
        evidence_refs=[],
    ) == {"ok": True}
    assert client.verify_wiki_page(
        f" {VALID_WIKI_PAGE_ID} ",
        project_id="p",
        workspace_binding_id="w",
        expected_current_revision_id="rev",
        evidence_refs=[
            {
                "kind": "file_ref",
                "path": "src/app.py",
                "hash": "a" * 64,
                "claims": [{"claim": "claim", "proof": "proof"}],
            }
        ],
        verification_note="Checked against current tree",
    ) == {"ok": True}

    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/api/hades/v1/wiki/pages"),
        ("GET", f"/api/hades/v1/wiki/pages/{VALID_WIKI_PAGE_ID}"),
        ("POST", "/api/hades/v1/wiki/pages"),
        ("POST", f"/api/hades/v1/wiki/pages/{VALID_WIKI_PAGE_ID}/verify"),
    ]
    assert _query_dict(requests[0]) == {
        "project_id": "p",
        "workspace_binding_id": "w",
        "source_status": "needs_verification",
        "limit": "20",
    }
    assert _query_dict(requests[1]) == {
        "project_id": "p",
        "workspace_binding_id": "w",
    }
    assert _json_request_body(requests[2]) == {
        "project_id": "p",
        "workspace_binding_id": "w",
        "slug": "technical/overview",
        "title": "Overview",
        "page_type": "technical",
        "content_markdown": "# Overview",
        "evidence_refs": [],
    }
    assert _json_request_body(requests[3]) == {
        "project_id": "p",
        "workspace_binding_id": "w",
        "expected_current_revision_id": "rev",
        "evidence_refs": [
            {
                "kind": "file_ref",
                "path": "src/app.py",
                "hash": "a" * 64,
                "claims": [{"claim": "claim", "proof": "proof"}],
            }
        ],
        "verification_note": "Checked against current tree",
    }


@pytest.mark.parametrize("method_name", ["wiki_page", "verify_wiki_page"])
def test_wiki_client_page_methods_require_a_non_empty_page_id(method_name):
    from hermes_cli.hades_backend_client import HadesBackendClient

    client = HadesBackendClient(
        "https://backend.example",
        "agent-token",
        transport=httpx.MockTransport(
            lambda _request: pytest.fail("request must not be sent")
        ),
    )

    with pytest.raises(ValueError, match="canonical ULID"):
        getattr(client, method_name)(
            "  ",
            project_id="p",
            workspace_binding_id="w",
        )


@pytest.mark.parametrize("method_name", ["wiki_page", "verify_wiki_page"])
@pytest.mark.parametrize(
    "page_id",
    ["../../privacy/export", "page?admin=1", "page#fragment"],
)
def test_wiki_client_rejects_non_ulid_page_ids_without_sending_request(
    method_name, page_id
):
    from hermes_cli.hades_backend_client import HadesBackendClient

    requests: list[httpx.Request] = []
    client = HadesBackendClient(
        "https://backend.example",
        "agent-token",
        transport=httpx.MockTransport(
            lambda request: (
                requests.append(request) or httpx.Response(200, json={"ok": True})
            )
        ),
    )

    with pytest.raises(ValueError, match="canonical ULID"):
        getattr(client, method_name)(
            page_id,
            project_id="p",
            workspace_binding_id="w",
        )

    assert requests == []


@pytest.mark.parametrize(
    "path",
    ["wiki/../privacy/export", "health?admin=1", "health#fragment"],
)
def test_client_rejects_unsafe_internal_route_paths(path):
    from hermes_cli.hades_backend_client import HadesBackendClient

    requests: list[httpx.Request] = []
    client = HadesBackendClient(
        "https://backend.example",
        "agent-token",
        transport=httpx.MockTransport(
            lambda request: (
                requests.append(request) or httpx.Response(200, json={"ok": True})
            )
        ),
    )

    with pytest.raises(ValueError, match="backend route path"):
        client._request("GET", path)

    assert requests == []


def test_client_uses_hades_v1_routes_and_bearer_auth():
    from hermes_cli.hades_backend_client import HadesBackendClient

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.url.path == "/api/hades/v1/agents/register"
        assert request.headers["authorization"] == "Bearer bootstrap-token"
        payload = json.loads(request.content.decode("utf-8"))
        assert payload["project_id"] == "proj_1"
        assert payload["agent_id"] == "agent_1"
        return httpx.Response(
            200,
            json={
                "agent_id": "agent_1",
                "agent_token": "derived-token",
                "capabilities": {"memory": True, "jobs": True},
            },
        )

    client = HadesBackendClient(
        "https://backend.example",
        "bootstrap-token",
        transport=httpx.MockTransport(handler),
    )

    response = client.register_agent(
        project_id="proj_1",
        agent_id="agent_1",
        label="dev-machine",
        platform="darwin",
        version="0.17.0",
        capabilities=["read_files"],
    )

    assert response["agent_token"] == "derived-token"
    assert seen


def test_token_env_key_is_stable_and_redaction_hides_tokens():
    from hermes_cli.hades_backend_client import redact_secret, token_env_key

    first = token_env_key("https://backend.example", "proj_1", "agent_1")
    second = token_env_key("https://backend.example/", "proj_1", "agent_1")

    assert first == second
    assert first.startswith("HADES_BACKEND_AGENT_TOKEN_")
    assert first.isupper()
    assert "sk-live-secret" not in redact_secret("token=sk-live-secret")
    assert "derived-token" not in redact_secret("Bearer derived-token")


@pytest.mark.parametrize("token_kind", ["agent", "bootstrap"])
def test_redaction_hides_complete_pipe_delimited_hades_tokens(token_kind):
    from hermes_cli.hades_backend_client import redact_secret

    secret = "S" * 64
    token = f"hades_{token_kind}_01ARZ3NDEKTSV4RRFFQ69G5FAV|{secret}"

    redacted = redact_secret(f"request failed: token={token}")

    assert redacted == "request failed: token=***"


def test_client_raises_backend_error_with_redacted_body():
    from hermes_cli.hades_backend_client import HadesBackendClient, HadesBackendError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={
                "error": {
                    "code": "job_not_found",
                    "message": "bad token sk-live-secret",
                    "next_step": "Run hades backend sync.",
                }
            },
        )

    client = HadesBackendClient(
        "https://backend.example",
        "sk-live-secret",
        transport=httpx.MockTransport(handler),
    )

    try:
        client.health()
    except HadesBackendError as exc:
        text = str(exc)
        error = exc
    else:  # pragma: no cover - guard
        raise AssertionError("expected HadesBackendError")

    assert "404" in text
    assert "sk-live-secret" not in text
    assert error.status_code == 404
    assert error.code == "job_not_found"
    assert error.next_step == "Run hades backend sync."


def test_get_payloads_use_query_params_for_laravel_routes():
    from hermes_cli.hades_backend_client import HadesBackendClient

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.method == "GET"
        assert request.url.path == "/api/hades/v1/agent/jobs"
        assert request.content == b""
        assert request.url.params["project_id"] == "proj_1"
        assert request.url.params["workspace_binding_id"] == "wb_1"
        assert request.url.query.decode("utf-8").count("capabilities%5B%5D=") == 2
        return httpx.Response(200, json={"jobs": []})

    client = HadesBackendClient(
        "https://backend.example",
        "agent-token",
        transport=httpx.MockTransport(handler),
    )

    response = client.pull_jobs(
        project_id="proj_1",
        workspace_binding_id="wb_1",
        capabilities=["read_files", "sync_git_tree"],
    )

    assert response == {"jobs": []}
    assert seen


def test_client_unlinks_workspace_with_route_parameter():
    from hermes_cli.hades_backend_client import HadesBackendClient

    seen: list[tuple[str, str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        seen.append((request.method, request.url.path, payload))
        return httpx.Response(200, json={"ok": True})

    client = HadesBackendClient(
        "https://backend.example",
        "agent-token",
        transport=httpx.MockTransport(handler),
    )

    assert client.unlink_workspace("wb_1", project_id="proj_1", agent_id="agent_1") == {
        "ok": True
    }
    assert seen == [
        (
            "POST",
            "/api/hades/v1/workspaces/wb_1/unlink",
            {"project_id": "proj_1", "agent_id": "agent_1"},
        )
    ]


def test_client_posts_doctor_reports_and_persephone_messages():
    from hermes_cli.hades_backend_client import HadesBackendClient

    seen: list[tuple[str, str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        seen.append((request.method, request.url.path, payload))
        return httpx.Response(201, json={"ok": True})

    client = HadesBackendClient(
        "https://backend.example",
        "agent-token",
        transport=httpx.MockTransport(handler),
    )

    assert client.submit_doctor_report(
        project_id="proj_1", status="warning", payload={"checks": []}
    ) == {"ok": True}
    assert client.create_inbox_message(
        project_id="proj_1", event_type="proposal.reviewed", payload={"message": "done"}
    ) == {"ok": True}
    assert seen == [
        (
            "POST",
            "/api/hades/v1/doctor/reports",
            {"project_id": "proj_1", "status": "warning", "payload": {"checks": []}},
        ),
        (
            "POST",
            "/api/hades/v1/persephone/messages",
            {
                "project_id": "proj_1",
                "event_type": "proposal.reviewed",
                "payload": {"message": "done"},
            },
        ),
    ]


def test_client_presence_heartbeat():
    from hermes_cli.hades_backend_client import HadesBackendClient

    seen: list[tuple[str, str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        seen.append((request.method, request.url.path, payload))
        return httpx.Response(
            200,
            json={
                "id": "pres_1",
                "agent_id": "agent_1",
                "observed_at": "2026-07-09T00:00:00Z",
            },
        )

    client = HadesBackendClient(
        "https://backend.example",
        "agent-token",
        transport=httpx.MockTransport(handler),
    )

    response = client.presence_heartbeat(
        project_id="proj_1",
        workspace_binding_id="wb_1",
        agent_id="agent_1",
        current_branch="main",
        last_head_sha="abc123",
        dirty_status=False,
        ttl_seconds=300,
    )

    assert response["id"] == "pres_1"
    assert response["agent_id"] == "agent_1"
    assert seen == [
        (
            "POST",
            "/api/hades/v1/presence/heartbeat",
            {
                "project_id": "proj_1",
                "workspace_binding_id": "wb_1",
                "agent_id": "agent_1",
                "current_branch": "main",
                "last_head_sha": "abc123",
                "dirty_status": False,
                "ttl_seconds": 300,
            },
        )
    ]


def test_client_presence_list():
    from hermes_cli.hades_backend_client import HadesBackendClient

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.method == "GET"
        assert request.url.path == "/api/hades/v1/presence"
        assert request.url.params["project_id"] == "proj_1"
        assert request.url.params["workspace_binding_id"] == "wb_1"
        return httpx.Response(
            200,
            json=[
                {
                    "id": "pres_1",
                    "agent_id": "agent_1",
                    "current_branch": "main",
                    "dirty_status": False,
                    "observed_at": "2026-07-09T00:00:00Z",
                    "ttl_seconds": 300,
                }
            ],
        )

    client = HadesBackendClient(
        "https://backend.example",
        "agent-token",
        transport=httpx.MockTransport(handler),
    )

    response = client.presence_list(
        project_id="proj_1",
        workspace_binding_id="wb_1",
    )

    assert len(response) == 1
    assert response[0]["agent_id"] == "agent_1"
    assert seen


def test_client_code_claim_create():
    from hermes_cli.hades_backend_client import HadesBackendClient

    seen: list[tuple[str, str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        seen.append((request.method, request.url.path, payload))
        return httpx.Response(201, json={"id": "claim_1", "agent_id": "agent_1"})

    client = HadesBackendClient(
        "https://backend.example",
        "agent-token",
        transport=httpx.MockTransport(handler),
    )

    response = client.code_claim_create(
        project_id="proj_1",
        workspace_binding_id="wb_1",
        agent_id="agent_1",
        refs=[{"type": "path", "value": "app/Foo.php"}],
        scope="edit",
        branch="main",
        head_sha="abc123",
        ttl_seconds=600,
    )

    assert response["id"] == "claim_1"
    assert seen == [
        (
            "POST",
            "/api/hades/v1/code-claims",
            {
                "project_id": "proj_1",
                "workspace_binding_id": "wb_1",
                "agent_id": "agent_1",
                "refs": [{"type": "path", "value": "app/Foo.php"}],
                "scope": "edit",
                "branch": "main",
                "head_sha": "abc123",
                "ttl_seconds": 600,
            },
        )
    ]


def test_client_code_claim_release():
    from hermes_cli.hades_backend_client import HadesBackendClient

    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        return httpx.Response(200, json={"ok": True})

    client = HadesBackendClient(
        "https://backend.example",
        "agent-token",
        transport=httpx.MockTransport(handler),
    )

    response = client.code_claim_release(claim_id="claim_1")

    assert response == {"ok": True}
    assert seen == [("POST", "/api/hades/v1/code-claims/claim_1/release")]


def test_client_code_claim_detect_conflicts():
    from hermes_cli.hades_backend_client import HadesBackendClient

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.method == "GET"
        assert request.url.path == "/api/hades/v1/code-claims/conflicts"
        assert request.url.params["project_id"] == "proj_1"
        assert request.url.params["scope"] == "edit"
        return httpx.Response(
            200,
            json=[
                {
                    "claim_id": "claim_1",
                    "agent_id": "agent_1",
                    "ref": "app/Foo.php",
                    "reason": "Overlap on app/Foo.php (scope edit)",
                }
            ],
        )

    client = HadesBackendClient(
        "https://backend.example",
        "agent-token",
        transport=httpx.MockTransport(handler),
    )

    response = client.code_claim_detect_conflicts(
        project_id="proj_1",
        refs=[{"type": "path", "value": "app/Foo.php"}],
        scope="edit",
    )

    assert len(response) == 1
    assert response[0]["agent_id"] == "agent_1"
    assert seen
