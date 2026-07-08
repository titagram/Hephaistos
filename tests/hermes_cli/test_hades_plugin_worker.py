from __future__ import annotations

from threading import Event


def test_plugin_worker_claims_heartbeats_and_completes_with_chat_message(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    from hermes_cli import hades_backend_db as db
    from hermes_cli.hades_plugin_worker import run_plugin_worker_once

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
            self.heartbeats = []
            self.completes = []

        def list_agent_work_items(self, **payload):
            assert payload["project_id"] == "proj_1"
            assert payload["agent_key"] == "local_agent"
            return {
                "items": [
                    {
                        "id": "awi_1",
                        "project_id": "proj_1",
                        "agent_key": "local_agent",
                        "payload": {
                            "kind": "devboard.agent_chat_turn.v1",
                            "user_message": {"content": "Che cosa hai capito?"},
                        },
                    }
                ]
            }

        def claim_agent_work_item(self, work_item_id, *, local_workspace_id):
            assert work_item_id == "awi_1"
            assert local_workspace_id == "lw_1"
            return {
                "lease_token": "lease_1",
                "item": {
                    "id": "awi_1",
                    "payload": {
                        "kind": "devboard.agent_chat_turn.v1",
                        "user_message": {"content": "Che cosa hai capito?"},
                    },
                },
            }

        def heartbeat_agent_work_item(self, work_item_id, *, lease_token):
            self.heartbeats.append((work_item_id, lease_token))
            return {"ok": True}

        def complete_agent_work_item(self, work_item_id, **payload):
            self.completes.append((work_item_id, payload))
            return {"ok": True}

    fake = FakeClient()
    prompts = []

    def runner(prompt, item):
        prompts.append(prompt)
        return {"final_response": "Risposta locale", "metadata": {"item": item["id"]}}

    result = run_plugin_worker_once(
        client_factory=lambda: fake,
        agent_runner=runner,
        local_workspace_id="lw_1",
        quiet=True,
    )

    with db.connect_closing() as conn:
        item = db.get_plugin_work_item(conn, "awi_1")

    assert result.exit_code == 0
    assert result.summary["completed"] == 1
    assert prompts == ["Che cosa hai capito?"]
    assert fake.heartbeats == [("awi_1", "lease_1")]
    assert fake.completes == [
        (
            "awi_1",
            {
                "lease_token": "lease_1",
                "chat_message": "Risposta locale",
                "memory_entry": None,
            },
        )
    ]
    assert item is not None
    assert item.status == "completed"
    assert item.lease_token == "lease_1"
    assert item.result == {"final_response": "Risposta locale", "metadata": {"item": "awi_1"}}


def test_plugin_worker_builds_prompt_from_kanban_task_contract(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    from hermes_cli import hades_backend_db as db
    from hermes_cli.hades_plugin_worker import run_plugin_worker_once

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

    kanban_payload = {
        "schema": "hades.kanban_task_work.v1",
        "task_id": "task_1",
        "project_id": "proj_1",
        "repository_id": "repo_1",
        "workspace_binding_id": "wb_1",
        "title": "Fix checkout regression",
        "description": "Checkout fails after selecting an existing customer.",
        "acceptance_criteria": ["Explain root cause"],
        "priority": "high",
        "risk": "medium",
        "normalized_problem": "Diagnose checkout failure for existing customers.",
        "task_type": "bug",
        "clarification_status": "ready",
        "ready_for_agent_work": True,
        "required_context": ["shared_project_memory", "bug_evidence"],
        "source_access_policy": {"mode": "source_free_first"},
        "project_awareness_required": True,
        "memory_required": True,
        "created_from": {"type": "kanban_task", "source": "dashboard"},
        "bug_report_id": "bug_1",
        "evidence_refs": [{"kind": "bug_evidence", "id": "ev_1"}],
        "bug_intake": {"status": "created"},
    }

    class FakeClient:
        def list_agent_work_items(self, **payload):
            return {"items": [{"id": "awi_1", "project_id": "proj_1", "payload": kanban_payload}]}

        def claim_agent_work_item(self, work_item_id, *, local_workspace_id):
            return {"lease_token": "lease_1", "item": {"id": work_item_id, "payload": kanban_payload}}

        def heartbeat_agent_work_item(self, work_item_id, *, lease_token):
            return {"ok": True}

        def complete_agent_work_item(self, work_item_id, **payload):
            return {"ok": True}

    prompts = []

    def runner(prompt, item):
        prompts.append(prompt)
        return "done"

    result = run_plugin_worker_once(
        client_factory=FakeClient,
        agent_runner=runner,
        local_workspace_id="lw_1",
        quiet=True,
    )

    assert result.exit_code == 0
    assert result.summary["completed"] == 1
    assert len(prompts) == 1
    assert "Fix checkout regression" in prompts[0]
    assert "bug_evidence: ev_1" in prompts[0]
    assert "shared Hades memory" in prompts[0]


def test_plugin_worker_keeps_heartbeating_while_runner_is_active(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    from hermes_cli import hades_backend_db as db
    from hermes_cli.hades_plugin_worker import run_plugin_worker_once

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

    second_heartbeat = Event()

    class FakeClient:
        def __init__(self):
            self.heartbeat_count = 0

        def list_agent_work_items(self, **payload):
            return {"items": [{"id": "awi_1", "payload": {"prompt": "Do it"}}]}

        def claim_agent_work_item(self, work_item_id, *, local_workspace_id):
            return {"lease_token": "lease_1", "item": {"id": work_item_id, "payload": {"prompt": "Do it"}}}

        def heartbeat_agent_work_item(self, work_item_id, *, lease_token):
            self.heartbeat_count += 1
            if self.heartbeat_count >= 2:
                second_heartbeat.set()
            return {"ok": True}

        def complete_agent_work_item(self, work_item_id, **payload):
            return {"ok": True}

    fake = FakeClient()

    def runner(prompt, item):
        assert second_heartbeat.wait(timeout=1.0)
        return "done"

    result = run_plugin_worker_once(
        client_factory=lambda: fake,
        agent_runner=runner,
        local_workspace_id="lw_1",
        heartbeat_interval_seconds=0.01,
        quiet=True,
    )

    assert result.exit_code == 0
    assert fake.heartbeat_count >= 2


def test_plugin_worker_fails_claimed_item_with_redacted_message(monkeypatch, tmp_path, caplog):
    import logging

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    from hermes_cli import hades_backend_db as db
    from hermes_cli.hades_plugin_worker import run_plugin_worker_once

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
            self.failures = []

        def list_agent_work_items(self, **payload):
            return {
                "items": [
                    {
                        "id": "awi_1",
                        "project_id": "proj_1",
                        "agent_key": "local_agent",
                        "payload": {"prompt": "Do it"},
                    }
                ]
            }

        def claim_agent_work_item(self, work_item_id, *, local_workspace_id):
            return {"lease_token": "lease_1", "item": {"id": work_item_id, "payload": {"prompt": "Do it"}}}

        def heartbeat_agent_work_item(self, work_item_id, *, lease_token):
            return {"ok": True}

        def fail_agent_work_item(self, work_item_id, **payload):
            self.failures.append((work_item_id, payload))
            return {"ok": True}

    fake = FakeClient()

    def runner(prompt, item):
        raise RuntimeError("token=super-secret-token exploded")

    with caplog.at_level(logging.WARNING, logger="hermes_cli.hades_backend"):
        result = run_plugin_worker_once(
            client_factory=lambda: fake,
            agent_runner=runner,
            local_workspace_id="lw_1",
            quiet=True,
        )

    with db.connect_closing() as conn:
        item = db.get_plugin_work_item(conn, "awi_1")

    assert result.exit_code == 1
    assert result.summary["failed"] == 1
    assert fake.failures[0][0] == "awi_1"
    assert fake.failures[0][1]["lease_token"] == "lease_1"
    assert "super-secret-token" not in fake.failures[0][1]["message"]
    assert item is not None
    assert item.status == "failed"

    records = [
        record
        for record in caplog.records
        if getattr(record, "hades_event", None) == "worker.failed"
    ]
    assert records
    assert records[0].hades_work_item_id == "awi_1"
    assert "super-secret-token" not in records[0].hades_error
    assert "lease_1" not in records[0].getMessage()
    assert "lease_1" not in str(records[0].__dict__)
