from __future__ import annotations

from pathlib import Path


FIXTURE_PATH = Path("tests/fixtures/hades/no_codebase_bug_cases.json")


def _kanban_bug_payload(run):
    evidence_refs = [{"kind": ref.split(":", 1)[0], "id": ref.split(":", 1)[1]} for ref in run.evidence_refs]
    return {
        "schema": "hades.kanban_task_work.v1",
        "task_id": "task_no_source_1",
        "project_id": "proj_1",
        "repository_id": "repo_1",
        "workspace_binding_id": "wb_1",
        "title": "Diagnose source-free fixture bug",
        "description": "A backend task asks the local agent to explain the exact root cause from Hades evidence.",
        "acceptance_criteria": [
            "Use shared Hades memory and project awareness evidence",
            "Persist a structured no-codebase diagnosis",
            "Do not use local source filesystem tools",
        ],
        "priority": "high",
        "risk": "high",
        "normalized_problem": "Explain the precise root cause using only Hades evidence refs and causal packs.",
        "task_type": "bug",
        "clarification_status": "ready",
        "ready_for_agent_work": True,
        "required_context": [
            "shared_project_memory",
            "project_awareness_status",
            "bug_evidence",
            "source_slices",
            "causal_packs",
        ],
        "source_access_policy": {
            "mode": "source_free_only",
            "local_source_filesystem_allowed": False,
            "source_slice_jobs_allowed": True,
        },
        "project_awareness_required": True,
        "memory_required": True,
        "created_from": {
            "type": "kanban_task",
            "source": "dashboard",
            "assigned_by": "fixture",
            "normalized_at": "2026-07-08T10:00:00Z",
        },
        "bug_report_id": "bug_fixture_1",
        "evidence_refs": evidence_refs,
        "bug_intake": {"status": "existing"},
        "quality_eval": {"no_codebase_fixture_id": run.fixture_id},
    }


def _structured_no_codebase_response(run, *, tool_calls=None):
    return {
        "final_response": (
            f"Root cause {run.root_cause_id} is supported by "
            f"{', '.join(run.evidence_refs[:2])} and causal pack {run.causal_pack_refs[0]}."
        ),
        "memory_refs": [{"type": "project_memory", "id": "mem_fixture_1"}],
        "diagnosis_report_id": "diag_fixture_1",
        "no_codebase_diagnosis": {
            "root_cause_id": run.root_cause_id,
            "confidence": run.confidence,
            "freshness_status": run.freshness_status,
            "diagnosable_without_source": run.diagnosable_without_source,
            "evidence_refs": list(run.evidence_refs),
            "tool_calls": list(tool_calls if tool_calls is not None else run.tool_calls),
            "causal_pack_refs": list(run.causal_pack_refs),
            "causal_chain": list(run.causal_chain),
            "persisted_report": True,
        },
        "memory_entry": {
            "kind": "resolved_bug",
            "summary": f"Resolved no-codebase fixture {run.fixture_id}",
            "payload": {
                "diagnosis_report_id": "diag_fixture_1",
                "evidence_refs": list(run.evidence_refs),
                "root_cause_id": run.root_cause_id,
            },
        },
    }


def test_plugin_worker_completes_kanban_bug_task_with_no_codebase_diagnosis(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    from hermes_cli import hades_backend_db as db
    from hermes_cli.hades_agent_work_eval import build_agent_work_no_codebase_report
    from hermes_cli.hades_kanban_task_contract import kanban_task_contract_status
    from hermes_cli.hades_no_codebase_eval import evaluate_no_codebase_diagnoses, load_no_codebase_eval_fixture
    from hermes_cli.hades_plugin_worker import run_plugin_worker_once
    from hermes_cli.hades_quality_report import build_hades_quality_report

    fixtures, runs = load_no_codebase_eval_fixture(FIXTURE_PATH)
    fixture = fixtures[0]
    run = runs[0]
    payload = _kanban_bug_payload(run)
    assert fixture.fixture_id == run.fixture_id
    assert kanban_task_contract_status(payload)["valid"] is True

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

    class FakeClient:
        def __init__(self):
            self.completes = []
            self.failures = []

        def list_agent_work_items(self, **request):
            assert request["project_id"] == "proj_1"
            assert request["agent_key"] == "local_agent"
            assert request["status"] == "queued"
            return {"items": [{"id": "awi_no_source_1", "project_id": "proj_1", "payload": payload}]}

        def claim_agent_work_item(self, work_item_id, *, local_workspace_id):
            assert work_item_id == "awi_no_source_1"
            assert local_workspace_id == "lw_1"
            return {
                "lease_token": "lease_1",
                "item": {"id": work_item_id, "project_id": "proj_1", "payload": payload},
            }

        def heartbeat_agent_work_item(self, work_item_id, *, lease_token):
            assert (work_item_id, lease_token) == ("awi_no_source_1", "lease_1")
            return {"ok": True}

        def complete_agent_work_item(self, work_item_id, **request):
            self.completes.append((work_item_id, request))
            return {"ok": True}

        def fail_agent_work_item(self, work_item_id, **request):
            self.failures.append((work_item_id, request))
            return {"ok": True}

    fake = FakeClient()
    prompts = []

    def runner(prompt, item):
        prompts.append(prompt)
        assert item["id"] == "awi_no_source_1"
        assert payload["source_access_policy"]["local_source_filesystem_allowed"] is False
        assert "/Users/gabriele/Dev" not in prompt
        assert "Hades backend kanban task" in prompt
        assert "shared Hades memory" in prompt
        assert run.evidence_refs[0].split(":", 1)[1] in prompt
        return _structured_no_codebase_response(run)

    worker_result = run_plugin_worker_once(
        client_factory=lambda: fake,
        agent_runner=runner,
        local_workspace_id="lw_1",
        quiet=True,
    )

    with db.connect_closing() as conn:
        item = db.get_plugin_work_item(conn, "awi_no_source_1")

    assert worker_result.exit_code == 0
    assert worker_result.summary["completed"] == 1
    assert fake.failures == []
    assert len(prompts) == 1
    assert fake.completes == [
        (
            "awi_no_source_1",
            {
                "lease_token": "lease_1",
                "chat_message": _structured_no_codebase_response(run)["final_response"],
                "memory_entry": _structured_no_codebase_response(run)["memory_entry"],
            },
        )
    ]
    assert item is not None
    assert item.status == "completed"
    assert item.result is not None
    assert item.result["no_codebase_diagnosis"]["evidence_refs"] == list(run.evidence_refs)

    agent_work_report = build_agent_work_no_codebase_report(fixtures, [item])
    no_codebase_report = evaluate_no_codebase_diagnoses([fixture], [run]).to_dict()
    quality_report = build_hades_quality_report(
        no_codebase_report=no_codebase_report,
        agent_work_no_codebase_report=agent_work_report,
    )

    assert agent_work_report["status"] == "passed"
    assert agent_work_report["eligible_work_items"] == 1
    assert agent_work_report["evaluated_work_items"] == 1
    assert agent_work_report["skipped_work_items"] == []
    assert quality_report["status"] == "passed"
    assert {action["id"] for action in quality_report["action_queue"]} == set()


def test_agent_work_no_codebase_gate_rejects_local_source_tool_use(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    from hermes_cli.hades_agent_work_eval import build_agent_work_no_codebase_report
    from hermes_cli.hades_no_codebase_eval import load_no_codebase_eval_fixture
    from hermes_cli.hades_quality_report import build_hades_quality_report

    fixtures, runs = load_no_codebase_eval_fixture(FIXTURE_PATH)
    run = runs[0]
    payload = _kanban_bug_payload(run)
    result = _structured_no_codebase_response(run, tool_calls=[*run.tool_calls, "read_file"])

    agent_work_report = build_agent_work_no_codebase_report(
        fixtures,
        [
            {
                "work_item_id": "awi_no_source_1",
                "status": "completed",
                "payload": payload,
                "result": result,
            }
        ],
    )
    quality_report = build_hades_quality_report(
        no_codebase_report=agent_work_report,
        agent_work_no_codebase_report=agent_work_report,
    )
    action_ids = {action["id"] for action in quality_report["action_queue"]}

    assert agent_work_report["status"] == "failed"
    assert agent_work_report["no_codebase_violations"] == [
        {"fixture_id": run.fixture_id, "tool": "read_file"}
    ]
    assert "agent_work_remove_forbidden_source_access" in action_ids
