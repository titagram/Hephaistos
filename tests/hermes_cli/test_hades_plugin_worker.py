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


def test_plugin_worker_fails_claimed_item_with_redacted_message(monkeypatch, tmp_path):
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
