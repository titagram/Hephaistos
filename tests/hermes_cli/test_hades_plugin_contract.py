from __future__ import annotations

import json
from pathlib import Path


def _ready_kanban_payload() -> dict:
    return {
        "schema": "hades.kanban_task_work.v1",
        "task_id": "task_1",
        "project_id": "proj_1",
        "repository_id": "repo_1",
        "workspace_binding_id": "wb_1",
        "title": "Diagnose checkout failure",
        "description": "Checkout fails for existing customers after address selection.",
        "acceptance_criteria": [
            "Explain root cause with evidence refs",
            "Avoid source-free claims without evidence",
        ],
        "priority": "high",
        "risk": "medium",
        "normalized_problem": "Find the root cause of the checkout failure using shared Hades evidence first.",
        "task_type": "bug",
        "clarification_status": "ready",
        "ready_for_agent_work": True,
        "required_context": ["shared_project_memory", "project_awareness_status", "bug_evidence"],
        "source_access_policy": {"mode": "source_free_first", "source_slice_jobs_allowed": True},
        "project_awareness_required": True,
        "memory_required": True,
        "created_from": {
            "type": "kanban_task",
            "source": "dashboard",
            "assigned_by": "user_1",
            "normalized_at": "2026-07-08T10:00:00Z",
        },
        "bug_report_id": "bug_1",
        "evidence_refs": [{"kind": "bug_evidence", "id": "ev_1"}],
        "bug_intake": {"status": "created"},
    }


def test_hades_plugin_openapi_contract_pins_task_work_payload_shape():
    from hermes_cli.hades_kanban_task_contract import REQUIRED_FIELDS

    spec = json.loads(Path("docs/hades/openapi-hades-v1.json").read_text())
    paths = spec["paths"]
    schemas = spec["components"]["schemas"]

    assert "/api/plugin/v1/agent-work-items" in paths
    assert "/api/plugin/v1/agent-work-items/{workItem}/claim" in paths
    assert "/api/plugin/v1/agent-work-items/{workItem}/complete" in paths
    assert "/api/plugin/v1/devices/register" in paths
    assert "/api/plugin/v1/repositories/{repository}/local-workspaces" in paths

    payload_schema = schemas["KanbanTaskWorkPayload"]
    assert payload_schema["properties"]["schema"]["const"] == "hades.kanban_task_work.v1"
    assert set(REQUIRED_FIELDS).issubset(set(payload_schema["required"]))
    assert payload_schema["properties"]["ready_for_agent_work"]["const"] is True
    assert payload_schema["properties"]["memory_required"]["const"] is True

    item_schema = schemas["PluginAgentWorkItem"]
    assert item_schema["properties"]["payload"]["$ref"] == "#/components/schemas/KanbanTaskWorkPayload"
    assert item_schema["properties"]["assigned_agent_key"]["const"] == "local_agent"

    complete_schema = schemas["PluginWorkItemCompleteRequest"]
    assert "lease_token" in complete_schema["required"]
    assert "memory_entry" in complete_schema["properties"]


def test_plugin_task_list_accepts_backend_contract_item_and_caches_it(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    from hermes_cli import hades_backend_db as db
    from hermes_cli.hades_plugin_tasks import list_plugin_tasks

    with db.connect_closing() as conn:
        db.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"jobs": True},
        )

    payload = _ready_kanban_payload()

    class FakeClient:
        def list_agent_work_items(self, **params):
            assert params == {
                "project_id": "proj_1",
                "repository_id": None,
                "agent_key": "local_agent",
                "status": "queued",
                "limit": 20,
            }
            return {
                "items": [
                    {
                        "id": "awi_1",
                        "task_id": "task_1",
                        "project_id": "proj_1",
                        "repository_id": "repo_1",
                        "assigned_agent_key": "local_agent",
                        "status": "queued",
                        "payload": payload,
                    }
                ]
            }

    result = list_plugin_tasks(client=FakeClient())

    assert result["status"] == "ok"
    assert result["count"] == 1
    task = result["items"][0]
    assert task["work_item_id"] == "awi_1"
    assert task["agent_key"] == "local_agent"
    assert task["contract"] == {
        "schema": "hades.kanban_task_work.v1",
        "valid": True,
        "errors": [],
    }
    assert "Diagnose checkout failure" in task["prompt_preview"]
    assert "shared Hades evidence first" in task["prompt_preview"]

    with db.connect_closing() as conn:
        cached = db.get_plugin_work_item(conn, "awi_1")

    assert cached is not None
    assert cached.kind == "hades.kanban_task_work.v1"
    assert cached.payload["task_id"] == "task_1"
