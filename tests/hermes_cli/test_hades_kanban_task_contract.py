from __future__ import annotations


def _valid_payload():
    return {
        "schema": "hades.kanban_task_work.v1",
        "task_id": "task_1",
        "project_id": "proj_1",
        "repository_id": "repo_1",
        "workspace_binding_id": "wb_1",
        "title": "Fix checkout regression",
        "description": "Checkout fails after selecting an existing customer.",
        "acceptance_criteria": ["Explain root cause", "Identify evidence refs"],
        "priority": "high",
        "risk": "medium",
        "normalized_problem": "Diagnose checkout failure for existing customers.",
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


def test_kanban_task_work_contract_accepts_ready_bug_payload():
    from hermes_cli.hades_kanban_task_contract import kanban_task_contract_status, kanban_task_prompt

    payload = _valid_payload()
    status = kanban_task_contract_status(payload)
    prompt = kanban_task_prompt(payload)

    assert status == {
        "schema": "hades.kanban_task_work.v1",
        "valid": True,
        "errors": [],
    }
    assert "Fix checkout regression" in prompt
    assert "Evidence refs:" in prompt
    assert "bug_evidence: ev_1" in prompt
    assert "shared Hades memory" in prompt


def test_kanban_task_work_contract_rejects_non_ready_missing_context_payload():
    from hermes_cli.hades_kanban_task_contract import kanban_task_contract_status

    payload = _valid_payload()
    payload.pop("repository_id")
    payload["ready_for_agent_work"] = False
    payload["clarification_status"] = "unclear"
    payload["memory_required"] = False
    payload["evidence_refs"] = []

    status = kanban_task_contract_status(payload)
    errors = {(error["field"], error["code"]) for error in status["errors"]}

    assert status["valid"] is False
    assert ("repository_id", "missing_required_field") in errors
    assert ("ready_for_agent_work", "not_ready") in errors
    assert ("clarification_status", "invalid_value") in errors
    assert ("memory_required", "invalid_value") in errors
    assert ("evidence_refs", "missing_required_field") in errors
