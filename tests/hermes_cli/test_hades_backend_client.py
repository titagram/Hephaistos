from __future__ import annotations

import inspect
import json
from pathlib import Path

import httpx
import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
OPENAPI_FIXTURE = REPO_ROOT / "docs" / "hades" / "openapi-hades-v1.json"

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
            "include_raw_chunks": "false",
        },
    },
    {
        "method_name": "create_memory_proposal",
        "http_method": "POST",
        "openapi_path": "/api/hades/v1/memory/proposals",
        "wire_path": "/api/hades/v1/memory/proposals",
        "kwargs": {"project_id": "proj_1", "action": "create", "summary": "Remember this"},
        "json_body": {"project_id": "proj_1", "action": "create", "summary": "Remember this"},
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
            "graph_refs": [{"type": "route_handler", "from": "route:orders.show", "to": "OrderController@show"}],
            "source_slice_ids": ["slice_1"],
            "payload": {"next_verification": "Run OrderControllerTest::test_archived_order_show"},
            "redactions": 1,
        },
        "json_body": {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "bug_report_id": "bug_1",
            "title": "Order route 500 evidence pack",
            "summary": "Stack trace, graph edge, and source slice point to OrderController.",
            "evidence_refs": [{"type": "bug_evidence", "id": "evidence_1"}],
            "graph_refs": [{"type": "route_handler", "from": "route:orders.show", "to": "OrderController@show"}],
            "source_slice_ids": ["slice_1"],
            "payload": {"next_verification": "Run OrderControllerTest::test_archived_order_show"},
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
            "include_content": "false",
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
        "kwargs": {"project_id": "proj_1", "status": "warning", "payload": {"checks": []}},
        "json_body": {"project_id": "proj_1", "status": "warning", "payload": {"checks": []}},
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
        "kwargs": {"project_id": "proj_1", "event_type": "proposal.reviewed", "payload": {"message": "done"}},
        "json_body": {"project_id": "proj_1", "event_type": "proposal.reviewed", "payload": {"message": "done"}},
    },
]

INTENTIONALLY_UNMAPPED_OPENAPI_ROUTES = {
    (
        "GET",
        "/api/hades/v1/persephone/events",
    ): "SSE fallback for realtime inbox reads; the local sync client uses the polling inbox route.",
}

INTENTIONALLY_UNMAPPED_CLIENT_METHODS = {
    "presence_heartbeat",
    "presence_list",
    "code_claim_create",
    "code_claim_release",
    "code_claim_detect_conflicts",
}


def _openapi_routes() -> dict[tuple[str, str], dict]:
    spec = json.loads(OPENAPI_FIXTURE.read_text(encoding="utf-8"))
    return {
        (method.upper(), path): operation
        for path, methods in spec["paths"].items()
        for method, operation in methods.items()
    }


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
        else:
            assert request.content == b""
        if "query" in case:
            assert _query_dict(request) == case["query"]
        return httpx.Response(200, json={"ok": True})

    client = HadesBackendClient(
        "https://backend.example",
        "agent-token",
        transport=httpx.MockTransport(handler),
    )

    response = getattr(client, case["method_name"])(*(case.get("args") or ()), **(case.get("kwargs") or {}))

    assert response == {"ok": True}
    assert seen


def test_client_route_coverage_is_explicit_against_openapi_fixture():
    from hermes_cli.hades_backend_client import HadesBackendClient

    public_client_methods = {
        name
        for name, member in inspect.getmembers(HadesBackendClient, predicate=inspect.isfunction)
        if not name.startswith("_") and name != "close"
    }
    covered_client_methods = {case["method_name"] for case in CLIENT_ROUTE_CASES}
    covered_routes = {(case["http_method"], case["openapi_path"]) for case in CLIENT_ROUTE_CASES}
    fixture_routes = {
        route
        for route in _openapi_routes()
        if route[1].startswith("/api/hades/v1/")
    }

    assert public_client_methods == covered_client_methods | INTENTIONALLY_UNMAPPED_CLIENT_METHODS
    assert fixture_routes == covered_routes | set(INTENTIONALLY_UNMAPPED_OPENAPI_ROUTES)


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

    assert client.unlink_workspace("wb_1", project_id="proj_1", agent_id="agent_1") == {"ok": True}
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

    assert client.submit_doctor_report(project_id="proj_1", status="warning", payload={"checks": []}) == {"ok": True}
    assert client.create_inbox_message(project_id="proj_1", event_type="proposal.reviewed", payload={"message": "done"}) == {"ok": True}
    assert seen == [
        (
            "POST",
            "/api/hades/v1/doctor/reports",
            {"project_id": "proj_1", "status": "warning", "payload": {"checks": []}},
        ),
        (
            "POST",
            "/api/hades/v1/persephone/messages",
            {"project_id": "proj_1", "event_type": "proposal.reviewed", "payload": {"message": "done"}},
        ),
    ]


def test_client_presence_heartbeat():
    from hermes_cli.hades_backend_client import HadesBackendClient

    seen: list[tuple[str, str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        seen.append((request.method, request.url.path, payload))
        return httpx.Response(200, json={"id": "pres_1", "agent_id": "agent_1", "observed_at": "2026-07-09T00:00:00Z"})

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
        return httpx.Response(200, json=[
            {
                "id": "pres_1",
                "agent_id": "agent_1",
                "current_branch": "main",
                "dirty_status": False,
                "observed_at": "2026-07-09T00:00:00Z",
                "ttl_seconds": 300,
            }
        ])

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
        return httpx.Response(200, json=[
            {
                "claim_id": "claim_1",
                "agent_id": "agent_1",
                "ref": "app/Foo.php",
                "reason": "Overlap on app/Foo.php (scope edit)",
            }
        ])

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
