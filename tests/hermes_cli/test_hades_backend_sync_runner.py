from __future__ import annotations

from hermes_cli.hades_persephone_messages import AGENT_MESSAGE_SCHEMA


def test_sync_path_executes_information_request_once(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import hades_backend_db as hdb
    from hermes_cli.hades_backend_sync import run_backend_sync
    from hermes_cli.hades_persephone_store import get_message

    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "module.py").write_text("needle = True\n", encoding="utf-8")
    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_1",
            project_id="project_1",
            base_url="https://backend.invalid",
            label="test",
            token_env_key="TOKEN_TEST",
            capabilities={},
        )
        hdb.upsert_workspace_binding(
            conn,
            project_id="project_1",
            agent_id="agent_1",
            local_project_id="local_1",
            workspace_fingerprint="fingerprint_1",
            display_path=str(workspace),
            repo_root=str(workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id="binding_1",
        )

    envelope = {
        "schema": AGENT_MESSAGE_SCHEMA,
        "message_id": "sync_information_1",
        "correlation_id": "sync_correlation_1",
        "causation_id": None,
        "project_id": "project_1",
        "sender_agent_id": "sender_1",
        "target_agent_id": "agent_1",
        "target_workspace_binding_id": "binding_1",
        "message_type": "information_request",
        "effect": "information_read",
        "capability": "source_search",
        "remote_task_id": None,
        "remote_task_version": None,
        "expires_at": 2_000_000_100,
        "payload": {"query": "needle"},
    }

    class Client:
        def capabilities(self):
            return {"persephone_agent_queue_v1": True}

        def memory_snapshot(self, **payload):
            return {"items": []}

        def pull_jobs(self, **payload):
            return {"jobs": []}

        def list_inbox(self, **payload):
            return {"events": [{"id": "cursor_sync_1", "payload": envelope}]}

    run_backend_sync(client_factory=Client, now=2_000_000_000, quiet=True)

    with hdb.connect_closing() as conn:
        request = get_message(conn, "sync_information_1")
        outbox_count = conn.execute("SELECT COUNT(*) FROM persephone_outbox").fetchone()[0]
    assert request is not None and request.state == "responded"
    assert outbox_count == 1


def test_sync_inbox_routes_agent_messages_and_preserves_legacy_events(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import hades_backend_db as hdb
    from hermes_cli.hades_backend_sync import _sync_inbox

    agent_message = {
        "schema": "hades.persephone.agent-message.v1",
        "message_id": "msg_1",
    }
    routed = []

    class FakeReceiver:
        @staticmethod
        def is_agent_event(event):
            return event.get("payload", {}).get("schema") == agent_message["schema"]

        def ingest_event(self, event, **context):
            routed.append(event)
            return "accepted"

    saved = _sync_inbox(
        {
            "events": [
                {"id": "cursor_1", "payload": agent_message},
                {
                    "id": "legacy_1",
                    "event_type": "proposal.reviewed",
                    "payload": {"message": "Proposal accepted."},
                },
            ]
        },
        "project_1",
        receiver=FakeReceiver(),
        target_agent_id="agent_1",
    )

    with hdb.connect_closing() as conn:
        legacy = hdb.list_inbox_events(conn, project_id="project_1")
    assert saved == 2
    assert [event["id"] for event in routed] == ["cursor_1"]
    assert [event.event_id for event in legacy] == ["legacy_1"]


def test_sync_inbox_passes_authenticated_subscription_context_to_receiver():
    from hermes_cli.hades_backend_sync import _sync_inbox

    calls = []

    class FakeReceiver:
        @staticmethod
        def is_agent_event(event):
            return True

        def ingest_event(self, event, **context):
            calls.append(context)
            return "subscription_route_mismatch"

    assert _sync_inbox(
        {"events": [{"id": "cursor_a", "payload": {"schema": "any"}}]},
        "project_a",
        receiver=FakeReceiver(),
        target_agent_id="agent_a",
        workspace_binding_id="wb_a",
    ) == 1
    assert calls == [
        {
            "expected_project_id": "project_a",
            "expected_target_agent_id": "agent_a",
            "expected_workspace_binding_id": "wb_a",
        }
    ]


def _decode_compressed_artifact_payload(payload):
    import base64
    import gzip
    import json

    raw = gzip.decompress(base64.b64decode(payload["artifact_compressed"].encode("ascii")))
    return json.loads(raw.decode("utf-8"))


def test_sync_runner_logs_redacted_backend_errors(monkeypatch, tmp_path, caplog):
    import logging

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import hades_backend_db as hdb
    from hermes_cli.hades_backend_sync import run_backend_sync

    workspace = tmp_path / "repo"
    workspace.mkdir()

    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"memory": True, "jobs": True},
        )
        hdb.upsert_workspace_binding(
            conn,
            project_id="proj_1",
            agent_id="agent_1",
            local_project_id="p_local",
            workspace_fingerprint="wf_1",
            display_path="~/repo",
            repo_root=str(workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id="wb_1",
        )
    class FakeClient:
        def memory_snapshot(self, **payload):
            raise RuntimeError("token=super-secret-token failed")

        def list_inbox(self, **payload):
            return {"events": []}

        def pull_jobs(self, **payload):
            return {"jobs": []}

    with caplog.at_level(logging.WARNING, logger="hermes_cli.hades_backend"):
        result = run_backend_sync(client_factory=lambda: FakeClient(), quiet=True)

    records = [
        record
        for record in caplog.records
        if getattr(record, "hades_event", None) == "sync.error"
    ]

    assert result.exit_code == 1
    assert records
    assert records[0].hades_workspace_binding_id == "wb_1"
    assert "super-secret-token" not in records[0].hades_error
    assert "super-secret-token" not in records[0].getMessage()


def test_sync_runner_expires_waiting_jobs_after_deadline(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import hades_backend_db as hdb
    from hermes_cli.hades_backend_sync import run_backend_sync

    workspace = tmp_path / "repo"
    workspace.mkdir()

    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"jobs": True},
        )
        hdb.upsert_workspace_binding(
            conn,
            project_id="proj_1",
            agent_id="agent_1",
            local_project_id="p_local",
            workspace_fingerprint="wf_1",
            display_path="~/repo",
            repo_root=str(workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id="wb_1",
        )
        hdb.upsert_job(
            conn,
            job_id="job_expired",
            project_id="proj_1",
            workspace_binding_id="wb_1",
            capability="read_files",
            payload={"deadline_at": 10},
            status="waiting_confirmation",
        )

    class FakeClient:
        def __init__(self):
            self.statuses = []

        def memory_snapshot(self, **payload):
            return {"items": []}

        def pull_jobs(self, **payload):
            return {"jobs": []}

        def update_job_status(self, job_id, **payload):
            self.statuses.append((job_id, payload["status"], payload))
            return {}

    fake = FakeClient()
    result = run_backend_sync(client_factory=lambda: fake, now=20)

    with hdb.connect_closing() as conn:
        job = hdb.get_job(conn, "job_expired")

    assert result.exit_code == 0
    assert result.summary["expired"] == 1
    assert job is not None
    assert job.status == "expired"
    assert fake.statuses == [("job_expired", "expired", fake.statuses[0][2])]


def test_sync_runner_counts_pending_source_slice_jobs(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import hades_backend_db as hdb
    from hermes_cli.hades_backend_sync import run_backend_sync

    workspace = tmp_path / "repo"
    workspace.mkdir()

    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"jobs": True},
        )
        hdb.upsert_workspace_binding(
            conn,
            project_id="proj_1",
            agent_id="agent_1",
            local_project_id="p_local",
            workspace_fingerprint="wf_1",
            display_path="~/repo",
            repo_root=str(workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit="abc123",
            backend_workspace_binding_id="wb_1",
        )
        hdb.upsert_workspace_binding(
            conn,
            project_id="retired_proj",
            agent_id="retired_agent",
            local_project_id="p_retired",
            workspace_fingerprint="wf_retired",
            display_path="~/retired",
            repo_root=str(tmp_path / "retired"),
            git_remote_display="",
            git_remote_hash="",
            head_commit="old",
            backend_workspace_binding_id="wb_retired",
        )
        hdb.mark_binding_unlinked(conn, "wf_retired")
        hdb.upsert_job(
            conn,
            job_id="job_slice_retired",
            project_id="retired_proj",
            workspace_binding_id="wb_retired",
            capability="read_source_slice",
            payload={"path": "old.py", "start_line": 1, "end_line": 2},
            status="waiting_confirmation",
        )

    class FakeClient:
        def __init__(self):
            self.statuses = []

        def memory_snapshot(self, **payload):
            return {"items": []}

        def pull_jobs(self, **payload):
            return {
                "jobs": [
                    {
                        "job_id": "job_slice",
                        "capability": "read_source_slice",
                        "policy": "confirm",
                        "requires_confirmation": True,
                        "payload": {
                            "path": "app/Http/Controllers/BookingController.php",
                            "start_line": 1,
                            "end_line": 25,
                            "candidate_key": "a" * 64,
                        },
                    }
                ]
            }

        def update_job_status(self, job_id, **payload):
            self.statuses.append((job_id, payload["status"], payload))
            return {}

    fake = FakeClient()
    result = run_backend_sync(client_factory=lambda: fake)

    with hdb.connect_closing() as conn:
        job = hdb.get_job(conn, "job_slice")
        summary = hdb.get_sync_state(conn, "last_sync_summary")

    assert result.exit_code == 0
    assert result.summary["waiting"] == 1
    assert result.summary["source_slice_jobs_waiting"] == 1
    assert job is not None
    assert job.status == "waiting_confirmation"
    assert summary["source_slice_jobs_waiting"] == 1


def test_sync_runner_uploads_baseline_artifacts_without_remote_jobs(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import hades_backend_db as hdb
    from hermes_cli.hades_backend_sync import run_backend_sync

    workspace = tmp_path / "repo"
    controller = workspace / "app" / "Http" / "Controllers" / "BookingController.php"
    controller.parent.mkdir(parents=True)
    controller.write_text(
        "<?php\n"
        "namespace App\\Http\\Controllers;\n"
        "class BookingController {\n"
        "    public function show() { return response()->json(['ok' => true]); }\n"
        "}\n",
        encoding="utf-8",
    )

    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"memory": True, "jobs": True, "sync_git_tree": True, "populate_backend_ast": True},
        )
        hdb.upsert_workspace_binding(
            conn,
            project_id="proj_1",
            agent_id="agent_1",
            local_project_id="p_local",
            workspace_fingerprint="wf_1",
            display_path="~/repo",
            repo_root=str(workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit="abc123",
            backend_workspace_binding_id="wb_1",
        )

    class FakeClient:
        def __init__(self):
            self.uploads = []
            self.pull_upload_counts = []
            self.pull_payloads = []

        def memory_snapshot(self, **payload):
            return {"items": []}

        def list_inbox(self, **payload):
            return {"events": []}

        def artifact_lookup(self, **payload):
            return {"exists": False}

        def upload_artifact(self, **payload):
            self.uploads.append(payload)
            return {"artifact": {"id": f"artifact_{len(self.uploads)}"}}

        def pull_jobs(self, **payload):
            self.pull_upload_counts.append(len(self.uploads))
            self.pull_payloads.append(payload)
            return {"jobs": []}

    fake = FakeClient()
    result = run_backend_sync(client_factory=lambda: fake)

    schemas = {upload["schema"] for upload in fake.uploads}
    assert result.exit_code == 0
    assert result.summary["artifacts_uploaded"] == 2
    assert result.summary["artifact_errors"] == 0
    assert result.summary["source_slice_candidates"] >= 1
    assert schemas == {"hades.git_tree.v1", "hades.php_graph.v1"}
    assert {upload["job_id"] for upload in fake.uploads} == {None}
    assert all(upload["workspace_binding_id"] == "wb_1" for upload in fake.uploads)
    assert fake.pull_upload_counts == [0]
    assert "read_source_slice" in fake.pull_payloads[0]["capabilities"]
    assert "populate_project_wiki" in fake.pull_payloads[0]["capabilities"]
    graph_upload = next(upload for upload in fake.uploads if upload["schema"] == "hades.php_graph.v1")
    graph = graph_upload["artifact"]
    node_ids = {node["id"] for node in graph["nodes"]}
    assert graph["graph_contract"]["version"] == "hades.graph_artifact.v1"
    assert graph["symbols"] and "id" not in graph["symbols"][0]
    assert node_ids and all(node_id.startswith("hades:node:v1:") for node_id in node_ids)
    assert all(
        relationship["source_id"] in node_ids and relationship["target_id"] in node_ids
        for relationship in graph["relationships"]
    )


def test_sync_runner_uploads_current_organism_with_dedupe_and_safe_manifest(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import hades_backend_db as hdb
    from hermes_cli.gnothi.contract import add_node, new_artifact
    from hermes_cli.gnothi.store import OrganismRevisionStore
    from hermes_cli.hades_backend_sync import (
        _artifact_upload_cache_key,
        run_backend_sync,
    )

    workspace = tmp_path / "repo"
    workspace.mkdir()
    head = "a" * 40
    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={
                "artifacts": True,
                "sync_git_tree": False,
                "populate_backend_ast": False,
            },
        )
        hdb.upsert_workspace_binding(
            conn,
            project_id="proj_1",
            agent_id="agent_1",
            local_project_id="p_local",
            workspace_fingerprint="wf_1",
            display_path="~/repo",
            repo_root=str(workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit=head,
            backend_workspace_binding_id="wb_1",
        )

    def artifact(revision: str, semantic: str):
        value = new_artifact(
            revision_id=revision,
            generation_id=f"git:{head}",
            generation_scope="stable",
            head_commit=head,
            collected_at="2026-07-14T12:00:00Z",
        )
        value["organism_contract"].update(
            status="current",
            coverage={"source": {"status": "current", "fingerprint": semantic}},
            semantic_fingerprint=semantic,
        )
        value["evidence"] = []
        add_node(
            value,
            node_id="workspace:fixture",
            kind="workspace",
            label=workspace.name,
            owner_class="workspace",
            owner_id="workspace:fixture",
            state={"verified": False},
            properties={"collector": "source"},
        )
        add_node(
            value,
            node_id="source:relative",
            kind="source_file",
            label="src/demo.py",
            owner_class="workspace",
            owner_id="workspace:fixture",
            state={"verified": False},
            properties={"collector": "source", "path": "src/demo.py"},
        )
        add_node(
            value,
            node_id="source:absolute",
            kind="source_file",
            label="opaque",
            owner_class="workspace",
            owner_id="workspace:fixture",
            state={"verified": False},
            properties={"collector": "source", "path": "/private/secret.py"},
        )
        return value

    store = OrganismRevisionStore()
    store.publish(artifact("rev-org-1", "sha256:first"))

    class FakeClient:
        def __init__(self):
            self.lookups = []
            self.uploads = []

        def capabilities(self):
            return {
                "organism_graph_schema": "hades.organism_graph.v1",
                "graph_scopes": ["project", "organism"],
            }

        def memory_snapshot(self, **payload):
            return {"items": []}

        def list_inbox(self, **payload):
            return {"events": []}

        def pull_jobs(self, **payload):
            return {"jobs": []}

        def artifact_lookup(self, **payload):
            self.lookups.append(payload)
            return {"exists": False}

        def upload_artifact(self, **payload):
            self.uploads.append(payload)
            return {"artifact": {"id": f"artifact_{len(self.uploads)}"}}

    fake = FakeClient()
    first = run_backend_sync(client_factory=lambda: fake, quiet=True)
    unchanged = run_backend_sync(client_factory=lambda: fake, quiet=True)
    store.publish(artifact("rev-org-2", "sha256:changed"))
    changed = run_backend_sync(client_factory=lambda: fake, quiet=True)

    assert first.summary["artifacts_uploaded"] == 1
    assert unchanged.summary["artifacts_skipped"] == 1
    assert changed.summary["artifacts_uploaded"] == 1
    assert [upload["schema"] for upload in fake.uploads] == [
        "hades.organism_graph.v1",
        "hades.organism_graph.v1",
    ]
    assert len(fake.lookups) == 2
    assert all(len(lookup["sha256"]) == 64 for lookup in fake.lookups)

    with hdb.connect_closing() as conn:
        binding = hdb.get_binding_for_backend_id(conn, "wb_1")
        cache = hdb.get_sync_state(
            conn,
            _artifact_upload_cache_key(binding, "hades.organism_graph.v1"),
        )
    assert cache["file_manifest"]["paths"].keys() == {"src/demo.py"}
    assert "/private/secret.py" not in str(cache)


def test_sync_runner_uses_binding_scoped_agent_for_each_project(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import hades_backend_db as hdb
    from hermes_cli import hades_backend_runtime as runtime
    from hermes_cli.hades_backend_sync import run_backend_sync

    workspace_one = tmp_path / "repo-one"
    workspace_two = tmp_path / "repo-two"
    workspace_one.mkdir()
    workspace_two.mkdir()

    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_project_one",
            project_id="project_one",
            base_url="https://backend.example",
            label="dev",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_ONE",
            capabilities={"memory": True, "jobs": True, "sync_git_tree": False, "populate_backend_ast": False},
        )
        hdb.save_agent(
            conn,
            agent_id="agent_project_two",
            project_id="project_two",
            base_url="https://backend.example",
            label="dev",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TWO",
            capabilities={"memory": True, "jobs": True, "sync_git_tree": False, "populate_backend_ast": False},
        )
        hdb.upsert_workspace_binding(
            conn,
            project_id="project_one",
            agent_id="agent_project_one",
            local_project_id="local_one",
            workspace_fingerprint="wf_one",
            display_path="~/repo-one",
            repo_root=str(workspace_one),
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id="wb_one",
        )
        hdb.upsert_workspace_binding(
            conn,
            project_id="project_two",
            agent_id="agent_project_two",
            local_project_id="local_two",
            workspace_fingerprint="wf_two",
            display_path="~/repo-two",
            repo_root=str(workspace_two),
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id="wb_two",
        )
        conn.execute("UPDATE backend_agents SET updated_at = 10 WHERE agent_id = 'agent_project_one'")
        conn.execute("UPDATE backend_agents SET updated_at = 20 WHERE agent_id = 'agent_project_two'")
        conn.commit()

    calls = []

    class FakeClient:
        def __init__(self, agent):
            self.agent = agent

        def _assert_scoped(self, payload):
            assert payload["project_id"] == self.agent.project_id
            if "agent_id" in payload:
                assert payload["agent_id"] == self.agent.agent_id
            calls.append((self.agent.agent_id, payload["project_id"], payload.get("workspace_binding_id")))

        def memory_snapshot(self, **payload):
            self._assert_scoped(payload)
            return {"items": []}

        def list_inbox(self, **payload):
            self._assert_scoped(payload)
            return {"events": []}

        def pull_jobs(self, **payload):
            self._assert_scoped(payload)
            return {"jobs": []}

        def close(self):
            return None

    clients = {}
    default_agent = None
    with hdb.connect_closing() as conn:
        default_agent = hdb.get_agent(conn, "agent_project_two")
    assert default_agent is not None

    def fake_client_for_agent(agent):
        clients.setdefault(agent.agent_id, FakeClient(agent))
        return clients[agent.agent_id]

    monkeypatch.setattr(runtime, "client_from_config", lambda: fake_client_for_agent(default_agent))
    monkeypatch.setattr(runtime, "client_for_agent", fake_client_for_agent)

    result = run_backend_sync(quiet=True)

    assert result.exit_code == 0
    assert ("agent_project_one", "project_one", "wb_one") in calls
    assert ("agent_project_two", "project_two", "wb_two") in calls
    assert all(agent_id.endswith(project_id.rsplit("_", 1)[-1]) for agent_id, project_id, _ in calls)


def test_cleanup_orphaned_memory_cache_removes_unlinked_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import hades_backend_db as hdb

    with hdb.connect_closing() as conn:
        hdb.upsert_workspace_binding(
            conn,
            project_id="proj_1",
            agent_id="agent_1",
            local_project_id="p_local",
            workspace_fingerprint="wf_1",
            display_path="~/repo",
            repo_root=str(tmp_path / "repo"),
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id="wb_orphan",
        )
        hdb.mark_binding_unlinked(conn, "wf_1")
        hdb.replace_memory_cache(
            conn,
            project_id="proj_1",
            workspace_binding_id="wb_orphan",
            version="v1",
            items=[{"summary": "stale"}],
        )

        report = hdb.cleanup_orphaned_memory_cache(conn, include_all=True)
        cache = hdb.get_memory_cache(conn, "wb_orphan")

    assert report["removed"] == 1
    assert report["candidates"] == 1
    assert cache is None


def test_cleanup_terminal_backend_jobs_keeps_active_and_fresh_jobs(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import hades_backend_db as hdb

    now = 2_000_000
    old = now - 31 * 86400
    fresh = now - 2 * 86400
    with hdb.connect_closing() as conn:
        hdb.upsert_job(
            conn,
            job_id="job_done_old",
            project_id="proj_1",
            workspace_binding_id="wb_1",
            capability="read_files",
            payload={},
            status="completed",
        )
        hdb.upsert_job(
            conn,
            job_id="job_failed_fresh",
            project_id="proj_1",
            workspace_binding_id="wb_1",
            capability="read_files",
            payload={},
            status="failed",
        )
        hdb.upsert_job(
            conn,
            job_id="job_waiting_old",
            project_id="proj_1",
            workspace_binding_id="wb_1",
            capability="read_files",
            payload={},
            status="waiting_confirmation",
        )
        conn.execute("UPDATE backend_jobs SET updated_at = ? WHERE job_id = ?", (old, "job_done_old"))
        conn.execute("UPDATE backend_jobs SET updated_at = ? WHERE job_id = ?", (fresh, "job_failed_fresh"))
        conn.execute("UPDATE backend_jobs SET updated_at = ? WHERE job_id = ?", (old, "job_waiting_old"))
        conn.commit()

        dry_run = hdb.cleanup_terminal_backend_jobs(conn, retention_days=30, now=now, dry_run=True)
        assert hdb.get_job(conn, "job_done_old") is not None

        report = hdb.cleanup_terminal_backend_jobs(conn, retention_days=30, now=now)

        assert dry_run["would_remove"] == 1
        assert dry_run["removed"] == 0
        assert report["removed"] == 1
        assert report["status_completed"] == 1
        assert hdb.get_job(conn, "job_done_old") is None
        assert hdb.get_job(conn, "job_failed_fresh") is not None
        assert hdb.get_job(conn, "job_waiting_old") is not None


def test_cleanup_reviewed_memory_proposals_keeps_unacknowledged_refusals(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import hades_backend_db as hdb

    now = 2_000_000
    old = now - 91 * 86400
    with hdb.connect_closing() as conn:
        accepted = hdb.create_memory_proposal(
            conn,
            project_id="proj_1",
            workspace_binding_id="wb_1",
            action="create",
            intent="memory_write",
            summary="accepted",
            provenance={},
        )
        refused = hdb.create_memory_proposal(
            conn,
            project_id="proj_1",
            workspace_binding_id="wb_1",
            action="create",
            intent="memory_write",
            summary="refused",
            provenance={},
        )
        hdb.mark_memory_proposal_status(conn, accepted.id, "accepted")
        hdb.mark_memory_proposal_status(conn, refused.id, "refused", "backend policy")
        conn.execute("UPDATE memory_proposals SET updated_at = ? WHERE id IN (?, ?)", (old, accepted.id, refused.id))
        conn.commit()

        report = hdb.cleanup_reviewed_memory_proposals(conn, retention_days=90, now=now)
        remaining = {proposal.id: proposal.status for proposal in hdb.list_memory_proposals(conn)}

    assert report["removed"] == 1
    assert report["status_accepted"] == 1
    assert accepted.id not in remaining
    assert remaining[refused.id] == "refused"


def test_cleanup_inbox_events_removes_stale_events(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import hades_backend_db as hdb

    now = 2_000_000
    old = now - 31 * 86400
    fresh = now - 2 * 86400
    with hdb.connect_closing() as conn:
        hdb.save_inbox_event(conn, event_id="evt_old", project_id="proj_1", event_type="notice", payload={"x": 1})
        hdb.save_inbox_event(conn, event_id="evt_fresh", project_id="proj_1", event_type="notice", payload={"x": 2})
        conn.execute("UPDATE inbox_events SET received_at = ? WHERE event_id = ?", (old, "evt_old"))
        conn.execute("UPDATE inbox_events SET received_at = ? WHERE event_id = ?", (fresh, "evt_fresh"))
        conn.commit()

        report = hdb.cleanup_inbox_events(conn, retention_days=30, now=now)
        remaining = [event.event_id for event in hdb.list_inbox_events(conn)]

    assert report["removed"] == 1
    assert report["unread"] == 1
    assert remaining == ["evt_fresh"]


def test_sync_runner_uploads_artifacts_and_polls_persephone_inbox(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import hades_backend_db as hdb
    from hermes_cli.hades_backend_sync import run_backend_sync

    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "README.md").write_text("hello\n", encoding="utf-8")
    head_commit = "f" * 40

    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"jobs": True, "artifacts": True, "persephone": True},
        )
        hdb.upsert_workspace_binding(
            conn,
            project_id="proj_1",
            agent_id="agent_1",
            local_project_id="p_local",
            workspace_fingerprint="wf_1",
            display_path="~/repo",
            repo_root=str(workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit=head_commit,
            backend_workspace_binding_id="wb_1",
        )

    class FakeClient:
        def __init__(self):
            self.artifacts = []
            self.results = []
            self.inbox_queries = []

        def capabilities(self):
            return {"persephone_agent_queue_v1": True}

        def memory_snapshot(self, **payload):
            return {"items": []}

        def pull_jobs(self, **payload):
            return {
                "jobs": [
                    {
                        "job_id": "job_tree",
                        "capability": "sync_git_tree",
                        "payload": {"max_files": 10, "max_bytes": 20_000},
                    }
                ]
            }

        def update_job_status(self, job_id, **payload):
            return {}

        def submit_job_result(self, job_id, **payload):
            self.results.append((job_id, payload))
            return {}

        def upload_artifact(self, **payload):
            self.artifacts.append(payload)
            return {"artifact": {"id": "artifact_1"}}

        def list_inbox(self, **payload):
            self.inbox_queries.append(payload)
            return {
                "events": [
                    {
                        "id": "evt_1",
                        "event_type": "proposal.reviewed",
                        "payload": {"message": "Memory proposal refused."},
                    }
                ]
            }

    fake = FakeClient()
    result = run_backend_sync(client_factory=lambda: fake)

    with hdb.connect_closing() as conn:
        events = hdb.list_inbox_events(conn, project_id="proj_1")

    assert result.exit_code == 0
    assert result.summary["completed"] == 1
    assert result.summary["artifacts_uploaded"] == 1
    assert result.summary["inbox_events"] == 1
    assert fake.artifacts[0]["schema"] == "hades.git_tree.v1"
    assert fake.artifacts[0]["job_id"] == "job_tree"
    assert fake.artifacts[0]["workspace_binding_id"] == "wb_1"
    assert fake.artifacts[0]["artifact"]["head_commit"] == head_commit
    assert fake.artifacts[0]["artifact"]["indexed_head_commit"] == head_commit
    assert fake.artifacts[0]["artifact"]["workspace_head_commit"] == head_commit
    assert fake.results[0][0] == "job_tree"
    assert fake.inbox_queries == [
        {"project_id": "proj_1", "target_agent_id": "agent_1", "limit": 50}
    ]
    assert events[0].event_id == "evt_1"
    assert events[0].event_type == "proposal.reviewed"


def test_sync_runner_uploads_php_graph_artifacts(monkeypatch, tmp_path):
    from hermes_cli.hades_backend_db import BackendAgent, WorkspaceBinding
    from hermes_cli.hades_backend_sync import _upload_job_artifact

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    class FakeClient:
        def __init__(self):
            self.uploads = []

        def upload_artifact(self, **payload):
            self.uploads.append(payload)
            return {"artifact": {"id": "artifact_php_graph"}}

    client = FakeClient()
    agent = BackendAgent(
        agent_id="agent_1",
        project_id="proj_1",
        base_url="https://backend.example",
        label="dev",
        token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
        capabilities={"artifacts": True},
    )
    binding = WorkspaceBinding(
        workspace_fingerprint="wf_1",
        project_id="proj_1",
        agent_id="agent_1",
        local_project_id="local_1",
        backend_workspace_binding_id="wb_1",
        display_path="~/repo",
        repo_root="/tmp/repo",
        git_remote_display="",
        git_remote_hash="",
        head_commit="a" * 40,
        status="active",
    )

    uploaded, errors, skipped = _upload_job_artifact(
        client,
        agent,
        binding,
        "job_php_graph",
        {
            "artifact": {
                "schema": "hades.php_graph.v1",
                "routes": [],
                "symbols": [],
                "edges": [],
                "truncated": False,
                "redactions": 0,
                "raw_source_included": False,
            }
        },
    )

    assert uploaded == 1
    assert errors == 0
    assert skipped == 0
    assert client.uploads[0]["schema"] == "hades.php_graph.v1"
    assert client.uploads[0]["artifact"]["indexed_head_commit"] == "a" * 40


def test_sync_runner_uploads_code_graph_artifacts(monkeypatch, tmp_path):
    from hermes_cli.hades_backend_db import BackendAgent, WorkspaceBinding
    from hermes_cli.hades_backend_sync import _upload_job_artifact

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    class FakeClient:
        def __init__(self):
            self.uploads = []

        def upload_artifact(self, **payload):
            self.uploads.append(payload)
            return {"artifact": {"id": "artifact_code_graph"}}

    client = FakeClient()
    agent = BackendAgent(
        agent_id="agent_1",
        project_id="proj_1",
        base_url="https://backend.example",
        label="dev",
        token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
        capabilities={"artifacts": True},
    )
    binding = WorkspaceBinding(
        workspace_fingerprint="wf_1",
        project_id="proj_1",
        agent_id="agent_1",
        local_project_id="local_1",
        backend_workspace_binding_id="wb_1",
        display_path="~/repo",
        repo_root="/tmp/repo",
        git_remote_display="",
        git_remote_hash="",
        head_commit="a" * 40,
        status="active",
    )

    uploaded, errors, skipped = _upload_job_artifact(
        client,
        agent,
        binding,
        "job_code_graph",
        {
            "artifact": {
                "schema": "hades.code_graph.v1",
                "routes": [],
                "symbols": [],
                "edges": [],
                "truncated": False,
                "redactions": 0,
                "raw_source_included": False,
            }
        },
    )

    assert uploaded == 1
    assert errors == 0
    assert skipped == 0
    assert client.uploads[0]["schema"] == "hades.code_graph.v1"
    assert client.uploads[0]["artifact"]["indexed_head_commit"] == "a" * 40


def test_sync_runner_compresses_large_artifact_uploads(monkeypatch, tmp_path):
    from hermes_cli.hades_backend_db import BackendAgent, WorkspaceBinding
    from hermes_cli.hades_backend_sync import _upload_job_artifact

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    class FakeClient:
        def __init__(self):
            self.uploads = []

        def upload_artifact(self, **payload):
            self.uploads.append(payload)
            return {"artifact": {"id": "artifact_code_graph"}}

    client = FakeClient()
    agent = BackendAgent(
        agent_id="agent_1",
        project_id="proj_1",
        base_url="https://backend.example",
        label="dev",
        token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
        capabilities={"artifacts": True},
    )
    binding = WorkspaceBinding(
        workspace_fingerprint="wf_1",
        project_id="proj_1",
        agent_id="agent_1",
        local_project_id="local_1",
        backend_workspace_binding_id="wb_1",
        display_path="~/repo",
        repo_root="/tmp/repo",
        git_remote_display="",
        git_remote_hash="",
        head_commit="d" * 40,
        status="active",
    )
    symbols = [
        {"kind": "component", "name": f"OrderComponent{i}", "path": "app/orders/page.tsx", "line": i}
        for i in range(2500)
    ]

    uploaded, errors, skipped = _upload_job_artifact(
        client,
        agent,
        binding,
        "job_large_code_graph",
        {
            "artifact": {
                "schema": "hades.code_graph.v1",
                "framework": "nextjs",
                "routes": [],
                "symbols": symbols,
                "edges": [],
                "truncated": False,
                "redactions": 0,
                "raw_source_included": False,
            }
        },
    )

    assert uploaded == 1
    assert errors == 0
    assert skipped == 0
    assert len(client.uploads) == 1
    payload = client.uploads[0]
    assert "artifact" not in payload
    assert payload["artifact_encoding"] == "gzip+base64"
    assert payload["artifact_compressed_bytes"] < payload["artifact_uncompressed_bytes"]
    decoded = _decode_compressed_artifact_payload(payload)
    assert decoded["schema"] == "hades.code_graph.v1"
    assert decoded["indexed_head_commit"] == "d" * 40
    assert len(decoded["symbols"]) == 2500


def test_sync_runner_retries_raw_when_compressed_artifact_upload_is_rejected(monkeypatch, tmp_path):
    from hermes_cli.hades_backend_db import BackendAgent, WorkspaceBinding
    from hermes_cli.hades_backend_sync import _upload_job_artifact

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    class FakeClient:
        def __init__(self):
            self.uploads = []

        def upload_artifact(self, **payload):
            self.uploads.append(payload)
            if payload.get("artifact_encoding") == "gzip+base64":
                raise RuntimeError("validation failed")
            return {"artifact": {"id": "artifact_code_graph"}}

    client = FakeClient()
    agent = BackendAgent(
        agent_id="agent_1",
        project_id="proj_1",
        base_url="https://backend.example",
        label="dev",
        token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
        capabilities={"artifacts": True},
    )
    binding = WorkspaceBinding(
        workspace_fingerprint="wf_1",
        project_id="proj_1",
        agent_id="agent_1",
        local_project_id="local_1",
        backend_workspace_binding_id="wb_1",
        display_path="~/repo",
        repo_root="/tmp/repo",
        git_remote_display="",
        git_remote_hash="",
        head_commit="e" * 40,
        status="active",
    )
    result = {
        "artifact": {
            "schema": "hades.code_graph.v1",
            "framework": "nextjs",
            "routes": [],
            "symbols": [
                {"kind": "component", "name": f"OrderComponent{i}", "path": "app/orders/page.tsx", "line": i}
                for i in range(2500)
            ],
            "edges": [],
            "truncated": False,
            "redactions": 0,
            "raw_source_included": False,
        }
    }

    assert _upload_job_artifact(client, agent, binding, "job_large_code_graph", result) == (1, 0, 0)
    assert len(client.uploads) == 2
    assert client.uploads[0]["artifact_encoding"] == "gzip+base64"
    assert client.uploads[1]["artifact"]["indexed_head_commit"] == "e" * 40


def test_sync_runner_skips_unchanged_artifact_uploads(monkeypatch, tmp_path):
    from hermes_cli.hades_backend_db import BackendAgent, WorkspaceBinding
    from hermes_cli.hades_backend_sync import _upload_job_artifact

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    class FakeClient:
        def __init__(self):
            self.uploads = []

        def upload_artifact(self, **payload):
            self.uploads.append(payload)
            return {"artifact": {"id": f"artifact_{len(self.uploads)}"}}

    client = FakeClient()
    agent = BackendAgent(
        agent_id="agent_1",
        project_id="proj_1",
        base_url="https://backend.example",
        label="dev",
        token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
        capabilities={"artifacts": True},
    )
    binding = WorkspaceBinding(
        workspace_fingerprint="wf_1",
        project_id="proj_1",
        agent_id="agent_1",
        local_project_id="local_1",
        backend_workspace_binding_id="wb_1",
        display_path="~/repo",
        repo_root="/tmp/repo",
        git_remote_display="",
        git_remote_hash="",
        head_commit="b" * 40,
        status="active",
    )
    result = {
        "artifact": {
            "schema": "hades.code_graph.v1",
            "framework": "nextjs",
            "routes": [{"method": "GET", "path": "/api/orders"}],
            "symbols": [{"kind": "component", "name": "OrdersPage"}],
            "edges": [],
            "truncated": False,
            "redactions": 0,
            "raw_source_included": False,
        }
    }

    first = _upload_job_artifact(client, agent, binding, "job_code_graph_1", result)
    second = _upload_job_artifact(client, agent, binding, "job_code_graph_2", result)

    assert first == (1, 0, 0)
    assert second == (0, 0, 1)
    assert len(client.uploads) == 1


def test_sync_runner_skips_artifacts_already_present_on_backend(monkeypatch, tmp_path):
    from hermes_cli import hades_backend_db as hdb
    from hermes_cli.hades_backend_db import BackendAgent, WorkspaceBinding
    from hermes_cli.hades_backend_sync import _artifact_upload_cache_key, _upload_job_artifact

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    class FakeClient:
        def __init__(self):
            self.lookups = []
            self.uploads = []

        def artifact_lookup(self, **payload):
            self.lookups.append(payload)
            return {
                "exists": True,
                "artifact": {
                    "id": "artifact_backend_1",
                    "schema": payload["schema"],
                    "sha256": payload["sha256"],
                },
            }

        def upload_artifact(self, **payload):
            self.uploads.append(payload)
            return {"artifact": {"id": f"artifact_{len(self.uploads)}"}}

    client = FakeClient()
    agent = BackendAgent(
        agent_id="agent_1",
        project_id="proj_1",
        base_url="https://backend.example",
        label="dev",
        token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
        capabilities={"artifacts": True},
    )
    binding = WorkspaceBinding(
        workspace_fingerprint="wf_1",
        project_id="proj_1",
        agent_id="agent_1",
        local_project_id="local_1",
        backend_workspace_binding_id="wb_1",
        display_path="~/repo",
        repo_root="/tmp/repo",
        git_remote_display="",
        git_remote_hash="",
        head_commit="b" * 40,
        status="active",
    )
    result = {
        "artifact": {
            "schema": "hades.code_graph.v1",
            "framework": "nextjs",
            "routes": [{"method": "GET", "path": "/api/orders"}],
            "symbols": [{"kind": "component", "name": "OrdersPage"}],
            "edges": [],
            "truncated": False,
            "redactions": 0,
            "raw_source_included": False,
        }
    }

    assert _upload_job_artifact(client, agent, binding, "job_code_graph_1", result) == (0, 0, 1)
    assert client.lookups[0]["project_id"] == "proj_1"
    assert client.lookups[0]["workspace_binding_id"] == "wb_1"
    assert client.lookups[0]["schema"] == "hades.code_graph.v1"
    assert len(client.lookups[0]["sha256"]) == 64
    assert client.uploads == []

    with hdb.connect_closing() as conn:
        cache = hdb.get_sync_state(conn, _artifact_upload_cache_key(binding, "hades.code_graph.v1"))
    assert cache["backend_artifact_id"] == "artifact_backend_1"
    assert cache["backend_skip_reason"] == "unchanged_on_backend"
    assert cache["file_manifest"]["count"] == 1


def test_sync_runner_records_file_level_artifact_delta(monkeypatch, tmp_path):
    from hermes_cli import hades_backend_db as hdb
    from hermes_cli.hades_backend_db import BackendAgent, WorkspaceBinding
    from hermes_cli.hades_backend_sync import _artifact_upload_cache_key, _upload_job_artifact

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    class FakeClient:
        def __init__(self):
            self.uploads = []

        def upload_artifact(self, **payload):
            self.uploads.append(payload)
            return {"artifact": {"id": f"artifact_{len(self.uploads)}"}}

    client = FakeClient()
    agent = BackendAgent(
        agent_id="agent_1",
        project_id="proj_1",
        base_url="https://backend.example",
        label="dev",
        token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
        capabilities={"artifacts": True},
    )
    binding = WorkspaceBinding(
        workspace_fingerprint="wf_1",
        project_id="proj_1",
        agent_id="agent_1",
        local_project_id="local_1",
        backend_workspace_binding_id="wb_1",
        display_path="~/repo",
        repo_root="/tmp/repo",
        git_remote_display="",
        git_remote_hash="",
        head_commit="c" * 40,
        status="active",
    )
    first_result = {
        "artifact": {
            "schema": "hades.git_tree.v1",
            "files": [
                {"path": "app/a.py", "sha256": "aaa", "bytes": 10},
                {"path": "app/b.py", "sha256": "bbb", "bytes": 20},
            ],
            "truncated": False,
            "redactions": 0,
            "raw_source_included": False,
        }
    }
    second_result = {
        "artifact": {
            "schema": "hades.git_tree.v1",
            "files": [
                {"path": "app/a.py", "sha256": "aaa", "bytes": 10},
                {"path": "app/b.py", "sha256": "changed", "bytes": 21},
                {"path": "app/c.py", "sha256": "ccc", "bytes": 30},
            ],
            "truncated": False,
            "redactions": 0,
            "raw_source_included": False,
        }
    }

    assert _upload_job_artifact(client, agent, binding, "job_tree_1", first_result) == (1, 0, 0)
    with hdb.connect_closing() as conn:
        cache = hdb.get_sync_state(conn, _artifact_upload_cache_key(binding, "hades.git_tree.v1"))
    assert cache["file_manifest"]["count"] == 2
    assert cache["file_delta"] == {
        "added": 2,
        "changed": 0,
        "removed": 0,
        "unchanged": 0,
        "added_paths": ["app/a.py", "app/b.py"],
        "changed_paths": [],
        "removed_paths": [],
    }

    assert _upload_job_artifact(client, agent, binding, "job_tree_2", second_result) == (1, 0, 0)
    with hdb.connect_closing() as conn:
        cache = hdb.get_sync_state(conn, _artifact_upload_cache_key(binding, "hades.git_tree.v1"))
    assert cache["file_manifest"]["count"] == 3
    assert cache["file_delta"] == {
        "added": 1,
        "changed": 1,
        "removed": 0,
        "unchanged": 1,
        "added_paths": ["app/c.py"],
        "changed_paths": ["app/b.py"],
        "removed_paths": [],
    }
    assert len(client.uploads) == 2


def test_sync_runner_uploads_source_slices():
    from hermes_cli.hades_backend_db import BackendAgent, WorkspaceBinding
    from hermes_cli.hades_backend_sync import _upload_job_source_slice

    class FakeClient:
        def __init__(self):
            self.uploads = []

        def create_source_slice(self, **payload):
            self.uploads.append(payload)
            return {"source_slice": {"id": "slice_1"}}

    client = FakeClient()
    agent = BackendAgent(
        agent_id="agent_1",
        project_id="proj_1",
        base_url="https://backend.example",
        label="dev",
        token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
        capabilities={"jobs": True},
    )
    binding = WorkspaceBinding(
        workspace_fingerprint="wf_1",
        project_id="proj_1",
        agent_id="agent_1",
        local_project_id="local_1",
        backend_workspace_binding_id="wb_1",
        display_path="~/repo",
        repo_root="/tmp/repo",
        git_remote_display="",
        git_remote_hash="",
        head_commit="b" * 40,
        status="active",
    )

    uploaded, failed = _upload_job_source_slice(
        client,
        agent,
        binding,
        "job_slice",
        {
            "source_slice": {
                "path": "app/Http/Controllers/OrderController.php",
                "start_line": 41,
                "end_line": 43,
                "content_redacted": "return ***;",
                "sha256": "c" * 64,
                "redactions": 1,
                "truncated": False,
            }
        },
    )

    assert uploaded == 1
    assert failed == 0
    assert client.uploads[0]["project_id"] == "proj_1"
    assert client.uploads[0]["workspace_binding_id"] == "wb_1"
    assert client.uploads[0]["job_id"] == "job_slice"
    assert client.uploads[0]["head_commit"] == "b" * 40
    assert client.uploads[0]["content_redacted"] == "return ***;"


def test_git_tree_artifact_omits_sensitive_ignored_binary_and_large_files(tmp_path):
    from hermes_cli.hades_backend_jobs import execute_job

    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (workspace / ".env").write_text("HADES_TOKEN=super-secret-token\n", encoding="utf-8")
    (workspace / "README.md").write_text("hello\n", encoding="utf-8")
    (workspace / "ignored.txt").write_text("ignored\n", encoding="utf-8")
    (workspace / "large.txt").write_text("x" * 64, encoding="utf-8")
    (workspace / "logo.png").write_bytes(b"\x89PNG\r\n")

    result = execute_job(
        {
            "job_id": "job_tree",
            "capability": "sync_git_tree",
            "payload": {"max_files": 20, "max_bytes": 10_000, "max_file_bytes": 16},
        },
        workspace_root=workspace,
    )

    artifact = result["artifact"]
    paths = {item["path"] for item in artifact["files"]}
    omitted = {item["path"]: item["reason"] for item in artifact["omitted"]}

    assert result["status"] == "completed"
    assert "README.md" in paths
    assert ".env" not in paths
    assert "ignored.txt" not in paths
    assert "large.txt" not in paths
    assert "logo.png" not in paths
    assert omitted[".env"] == "sensitive_name"
    assert omitted["ignored.txt"] == "gitignored"
    assert omitted["large.txt"] == "file_too_large"
    assert omitted["logo.png"] == "binary_or_archive"
    assert artifact["raw_source_included"] is False
    assert artifact["retention_class"] == "source_metadata"
    assert artifact["redactions"] == len(artifact["omitted"])
    assert "super-secret-token" not in str(artifact)


def test_git_tree_artifact_includes_structured_project_index(tmp_path):
    from hermes_cli.hades_backend_jobs import execute_job

    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "routes").mkdir()
    (workspace / "database" / "migrations").mkdir(parents=True)
    (workspace / "composer.json").write_text(
        '{"require":{"laravel/framework":"^11.0","guzzlehttp/guzzle":"^7.0"}}',
        encoding="utf-8",
    )
    (workspace / "package.json").write_text(
        '{"dependencies":{"@vitejs/plugin-react":"latest"},"devDependencies":{"vitest":"latest"}}',
        encoding="utf-8",
    )
    (workspace / "routes" / "api.php").write_text(
        "<?php\nRoute::get('/hades/memory', [MemoryController::class, 'index'])->name('hades.memory');\n",
        encoding="utf-8",
    )
    (workspace / "database" / "migrations" / "2026_07_06_000000_create_hades_memory.php").write_text(
        "<?php\nreturn new class {};\n",
        encoding="utf-8",
    )

    result = execute_job(
        {
            "job_id": "job_tree",
            "capability": "sync_git_tree",
            "payload": {"max_files": 50, "max_bytes": 100_000, "max_file_bytes": 10_000},
        },
        workspace_root=workspace,
    )

    artifact = result["artifact"]
    index = artifact["project_index"]

    assert index["schema"] == "hades.project_index.v1"
    assert index["source_schema"] == "hades.git_tree.v1"
    assert index["language_counts"]["php"]["files"] >= 2
    assert any(
        route.items()
        >= {
            "method": "GET",
            "uri": "/hades/memory",
            "handler": "MemoryController@index",
            "name": "hades.memory",
            "path": "routes/api.php",
        }.items()
        for route in index["routes"]
    )
    assert {
        "manager": "composer",
        "path": "composer.json",
        "packages": ["guzzlehttp/guzzle", "laravel/framework"],
    } in index["dependency_manifests"]
    assert {
        "manager": "npm",
        "path": "package.json",
        "packages": ["@vitejs/plugin-react", "vitest"],
    } in index["dependency_manifests"]
    assert index["database"]["migrations"] == [
        "database/migrations/2026_07_06_000000_create_hades_memory.php"
    ]
    assert artifact["raw_source_included"] is False
    assert "laravel/framework" in artifact["summary"]
    assert "Route::get" not in str(artifact)


def test_read_files_omitted_reasons_do_not_leak_absolute_paths(monkeypatch, tmp_path):
    import errno
    from pathlib import Path

    import hermes_cli.hades_backend_jobs as jobs

    workspace = tmp_path / "repo"
    workspace.mkdir()
    target = workspace / "allowed.txt"
    target.write_text("hello\n", encoding="utf-8")

    def fail_read(path: Path, max_bytes: int):
        raise OSError(errno.EACCES, "Permission denied", str(path))

    monkeypatch.setattr(jobs, "_read_text_bounded", fail_read)

    result = jobs.execute_job(
        {
            "job_id": "job_read",
            "capability": "read_files",
            "payload": {"paths": ["allowed.txt"]},
        },
        workspace_root=workspace,
    )

    assert result["status"] == "completed"
    assert result["attachments"] == []
    assert result["omitted"] == [{"path": "allowed.txt", "reason": "read_error:13"}]
    assert str(workspace) not in str(result)


def test_background_sync_runs_once_and_records_success(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import hades_backend_db as hdb
    import hermes_cli.hades_backend_sync as sync

    workspace = tmp_path / "repo"
    workspace.mkdir()

    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"jobs": True},
        )
        hdb.upsert_workspace_binding(
            conn,
            project_id="proj_1",
            agent_id="agent_1",
            local_project_id="p_local",
            workspace_fingerprint="wf_1",
            display_path="~/repo",
            repo_root=str(workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id="wb_1",
        )

    calls = []

    def fake_sync_runner(**kwargs):
        calls.append(kwargs)
        return sync.SyncResult({"pulled": 0, "completed": 0}, 0)

    first = sync.maybe_run_backend_sync(now=1000, run_inline=True, sync_runner=fake_sync_runner)
    second = sync.maybe_run_backend_sync(now=1001, run_inline=True, sync_runner=fake_sync_runner)

    with hdb.connect_closing() as conn:
        state = hdb.get_sync_state(conn, sync.BACKGROUND_SYNC_STATE_KEY)

    assert first.status == "ran"
    assert first.reason == "ok"
    assert second.status == "skipped"
    assert second.reason == "backoff"
    assert calls == [{"quiet": True}]
    assert state is not None
    assert state["status"] == "ok"
    assert state["failure_count"] == 0
    assert state["next_attempt_at"] == 1300


def test_sync_runner_scopes_to_workspace_binding_id(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import hades_backend_db as hdb
    from hermes_cli.hades_backend_sync import run_backend_sync

    workspace_1 = tmp_path / "repo-1"
    workspace_2 = tmp_path / "repo-2"
    workspace_1.mkdir()
    workspace_2.mkdir()

    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev-1",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST_1",
            capabilities={"memory": True, "jobs": True, "sync_git_tree": False, "populate_backend_ast": False},
        )
        hdb.save_agent(
            conn,
            agent_id="agent_2",
            project_id="proj_2",
            base_url="https://backend.example",
            label="dev-2",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST_2",
            capabilities={"memory": True, "jobs": True, "sync_git_tree": False, "populate_backend_ast": False},
        )
        hdb.upsert_workspace_binding(
            conn,
            project_id="proj_1",
            agent_id="agent_1",
            local_project_id="p_local_1",
            workspace_fingerprint="wf_1",
            display_path="~/repo-1",
            repo_root=str(workspace_1),
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id="wb_1",
        )
        hdb.upsert_workspace_binding(
            conn,
            project_id="proj_2",
            agent_id="agent_2",
            local_project_id="p_local_2",
            workspace_fingerprint="wf_2",
            display_path="~/repo-2",
            repo_root=str(workspace_2),
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id="wb_2",
        )
        hdb.upsert_job(
            conn,
            job_id="job_expired_other_workspace",
            project_id="proj_2",
            workspace_binding_id="wb_2",
            capability="read_files",
            payload={"deadline_at": 10},
            status="waiting_confirmation",
        )

    class FakeClient:
        def __init__(self):
            self.pulls = []

        def memory_snapshot(self, **payload):
            return {"items": []}

        def pull_jobs(self, **payload):
            self.pulls.append(payload)
            return {"jobs": []}

    fake = FakeClient()

    result = run_backend_sync(
        client_factory=lambda: fake,
        now=100,
        workspace_binding_ids=["wb_1"],
    )

    assert result.exit_code == 0
    assert [call["workspace_binding_id"] for call in fake.pulls] == ["wb_1"]
    with hdb.connect_closing() as conn:
        assert hdb.get_job(conn, "job_expired_other_workspace").status == "waiting_confirmation"


def test_workspace_sync_selects_latest_identity_and_binding_when_timestamps_match(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import hades_backend_db as hdb
    from hermes_cli import hades_backend_runtime as runtime
    import hermes_cli.hades_backend_sync as sync

    workspace = tmp_path / "repo"
    workspace.mkdir()
    monkeypatch.setattr(hdb, "_now", lambda: 100)

    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_historical_1",
            project_id="project_historical_1",
            base_url="https://backend.example",
            label="old-1",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_OLD_1",
            capabilities={"memory": True, "jobs": True, "sync_git_tree": False, "populate_backend_ast": False},
        )
        hdb.upsert_workspace_binding(
            conn,
            project_id="project_historical_1",
            agent_id="agent_historical_1",
            local_project_id="local_old_1",
            workspace_fingerprint="fingerprint_old_1",
            display_path=str(workspace),
            repo_root=str(workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit="old1",
            backend_workspace_binding_id="binding_historical_1",
        )
        hdb.save_agent(
            conn,
            agent_id="agent_historical_2",
            project_id="project_historical_2",
            base_url="https://backend.example",
            label="old-2",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_OLD_2",
            capabilities={"memory": True, "jobs": True, "sync_git_tree": False, "populate_backend_ast": False},
        )
        hdb.upsert_workspace_binding(
            conn,
            project_id="project_historical_2",
            agent_id="agent_historical_2",
            local_project_id="local_old_2",
            workspace_fingerprint="fingerprint_old_2",
            display_path=str(workspace),
            repo_root=str(workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit="old2",
            backend_workspace_binding_id="binding_historical_2",
        )
        hdb.save_agent(
            conn,
            agent_id="agent_current",
            project_id="project_current",
            base_url="https://backend.example",
            label="current",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_CURRENT",
            capabilities={"memory": True, "jobs": True, "sync_git_tree": False, "populate_backend_ast": False},
        )
        hdb.upsert_workspace_binding(
            conn,
            project_id="project_current",
            agent_id="agent_current",
            local_project_id="local_current_old",
            workspace_fingerprint="fingerprint_current_old",
            display_path=str(workspace),
            repo_root=str(workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit="current-old",
            backend_workspace_binding_id="binding_current_old",
        )
        hdb.upsert_workspace_binding(
            conn,
            project_id="project_current",
            agent_id="agent_current",
            local_project_id="local_current_new",
            workspace_fingerprint="fingerprint_current_new",
            display_path=str(workspace),
            repo_root=str(workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit="current-new",
            backend_workspace_binding_id="binding_current_new",
        )
        assert hdb.get_default_agent(conn).agent_id == "agent_current"
        assert [
            binding.backend_workspace_binding_id
            for binding in hdb.list_workspace_bindings(conn, status="linked")[:2]
        ] == ["binding_current_new", "binding_current_old"]

    calls = []
    constructed_agents = []

    class FakeClient:
        def memory_snapshot(self, **payload):
            return {"items": []}

        def list_inbox(self, **payload):
            return {"events": []}

        def pull_jobs(self, **payload):
            return {"jobs": []}

        def close(self):
            return None

    def current_client():
        constructed_agents.append("agent_current")
        return FakeClient()

    def historical_client(agent):
        constructed_agents.append(agent.agent_id)
        return FakeClient()

    monkeypatch.setattr(runtime, "client_from_config", current_client)
    monkeypatch.setattr(runtime, "client_for_agent", historical_client)

    def fake_sync_runner(**kwargs):
        calls.append(kwargs)
        return sync.run_backend_sync(**kwargs)

    decision = sync.maybe_run_backend_sync_for_workspace(
        cwd=workspace,
        force=True,
        run_inline=True,
        sync_runner=fake_sync_runner,
    )

    assert decision.status == "ran"
    assert calls == [{"quiet": True, "workspace_binding_ids": ["binding_current_new"]}]
    assert constructed_agents == ["agent_current"]


def test_background_sync_records_failure_backoff_and_degraded_status(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import hades_backend_db as hdb
    from hermes_cli.hades_backend_status import load_backend_status_payload
    import hermes_cli.hades_backend_sync as sync

    workspace = tmp_path / "repo"
    workspace.mkdir()

    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"jobs": True},
        )
        hdb.upsert_workspace_binding(
            conn,
            project_id="proj_1",
            agent_id="agent_1",
            local_project_id="p_local",
            workspace_fingerprint="wf_1",
            display_path="~/repo",
            repo_root=str(workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id="wb_1",
        )

    def failing_sync_runner(**kwargs):
        return sync.SyncResult({"error": 1}, 1)

    decision = sync.maybe_run_backend_sync(
        now=2000,
        run_inline=True,
        failure_base_delay_seconds=30,
        sync_runner=failing_sync_runner,
    )
    skipped = sync.maybe_run_backend_sync(now=2020, run_inline=True, sync_runner=failing_sync_runner)
    payload = load_backend_status_payload()

    assert decision.status == "ran"
    assert decision.reason == "failed"
    assert skipped.status == "skipped"
    assert skipped.reason == "backoff"
    assert payload["degraded"] is True
    assert payload["sync"]["background"]["status"] == "failed"
    assert payload["sync"]["background"]["failure_count"] == 1
    assert payload["sync"]["background"]["next_attempt_at"] == 2030
    assert payload["actions"] == [
        "Background backend sync is backing off; run `hades backend sync` to retry now.",
        "Run `hades backend quality-report --record` to establish a governance baseline.",
        "Run `hades backend worker-setup` in this checkout before claiming backend task work.",
    ]


def test_backend_status_reports_partial_project_awareness(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import hades_backend_db as hdb
    from hermes_cli.hades_backend_status import load_backend_status_payload

    workspace = tmp_path / "repo"
    workspace.mkdir()

    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"jobs": True, "memory": True},
        )
        hdb.upsert_workspace_binding(
            conn,
            project_id="proj_1",
            agent_id="agent_1",
            local_project_id="p_local",
            workspace_fingerprint="wf_1",
            display_path="~/repo",
            repo_root=str(workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit="abc123",
            backend_workspace_binding_id="wb_1",
        )

    monkeypatch.chdir(workspace)
    payload = load_backend_status_payload()
    binding_awareness = payload["bindings"][0]["awareness"]
    identity = payload["identity"]

    assert payload["awareness"] == {
        "status": "partial",
        "bindings": 1,
        "ready_bindings": 0,
        "partial_bindings": 1,
        "degraded_bindings": 0,
        "diagnosable_without_source_bindings": 0,
    }
    assert payload["bindings"][0]["head_commit"] == "abc123"
    assert binding_awareness["status"] == "partial"
    assert binding_awareness["diagnosable_without_source"] is False
    assert binding_awareness["coverage"]["memory_cache"]["status"] == "missing"
    assert binding_awareness["coverage"]["project_artifacts"]["status"] == "missing"
    assert binding_awareness["coverage"]["source_slices"]["status"] == "missing"
    assert binding_awareness["coverage"]["bug_evidence"]["status"] == "unknown"
    assert binding_awareness["quality"]["confidence"] == "incomplete"
    assert binding_awareness["quality"]["missing"] == [
        "shared_memory_cache",
        "project_artifact_index",
        "source_slice_index",
        "bug_evidence",
    ]
    assert payload["actions"] == [
        "Project awareness is incomplete; inspect `awareness` before source-free diagnosis.",
        "Run `hades backend quality-report --record` to establish a governance baseline.",
        "Run `hades backend worker-setup` in this checkout before claiming backend task work.",
    ]
    assert identity["personal_memory"]["scope"] == "local_profile"
    assert identity["personal_memory"]["portable_between_devices"] is False
    assert identity["project_memory"]["scope"] == "backend_project"
    assert identity["project_memory"]["project_id"] == "proj_1"
    assert identity["project_memory"]["cached_items"] == 0
    assert identity["project_memory"]["portable_between_devices"] is True
    assert identity["workspace_binding"]["scope"] == "local_workspace"
    assert identity["workspace_binding"]["current_workspace_binding_id"] == "wb_1"
    assert identity["workspace_binding"]["current_display_path"] == "~/repo"
    assert identity["workspace_binding"]["current_status"] == "partial"
    assert identity["workspace_binding"]["current_source_free_ready"] is False
    assert identity["workspace_binding"]["linked_bindings"] == 1
    assert identity["login_recovery"] == {
        "can_use_project_memory_without_old_device": True,
        "current_workspace_mapped": True,
        "source_free_diagnosis_ready": False,
        "requires_workspace_binding_for_indexing": True,
        "recommended_next_action": (
            "Run `hades backend sync`, then capture current bug evidence and source slices "
            "before source-free diagnosis."
        ),
    }


def test_backend_status_reports_pending_source_slice_candidates(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import hades_backend_db as hdb
    from hermes_cli.hades_backend_status import load_backend_status_payload

    workspace = tmp_path / "repo"
    workspace.mkdir()

    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"jobs": True, "memory": True},
        )
        hdb.upsert_workspace_binding(
            conn,
            project_id="proj_1",
            agent_id="agent_1",
            local_project_id="p_local",
            workspace_fingerprint="wf_1",
            display_path="~/repo",
            repo_root=str(workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit="abc123",
            backend_workspace_binding_id="wb_1",
        )
        hdb.replace_memory_cache(
            conn,
            project_id="proj_1",
            workspace_binding_id="wb_1",
            version="mem_v1",
            items=[{"kind": "resolved_bug", "summary": "Known login regression"}],
        )
        hdb.record_sync_state(
            conn,
            "last_sync_summary",
            {
                "memory_snapshots": 1,
                "artifacts_uploaded": 1,
                "artifacts_skipped": 0,
                "artifact_errors": 0,
                "source_slices_uploaded": 0,
                "source_slice_errors": 0,
                "source_slice_candidates": 1,
                "source_slice_jobs_waiting": 1,
                "bug_evidence_items": 1,
                "proposal_errors": 0,
            },
        )

    monkeypatch.chdir(workspace)
    payload = load_backend_status_payload()
    binding_awareness = payload["bindings"][0]["awareness"]

    assert binding_awareness["diagnosable_without_source"] is False
    assert binding_awareness["coverage"]["source_slices"]["status"] == "missing"
    assert binding_awareness["coverage"]["source_slice_candidates"] == {
        "status": "pending",
        "count": 1,
        "waiting_jobs": 1,
    }
    assert binding_awareness["quality"]["missing"] == ["source_slice_index"]
    assert binding_awareness["quality"]["actions"] == ["approve_source_slice_jobs"]


def test_backend_status_reports_cached_task_work_readiness(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import hades_backend_db as hdb
    from hermes_cli.hades_backend_status import load_backend_status_payload

    workspace = tmp_path / "repo"
    workspace.mkdir()

    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"jobs": True, "memory": True},
        )
        hdb.upsert_workspace_binding(
            conn,
            project_id="proj_1",
            agent_id="agent_1",
            local_project_id="p_local",
            workspace_fingerprint="wf_1",
            display_path="~/repo",
            repo_root=str(workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit="abc123",
            backend_workspace_binding_id="wb_1",
        )
        hdb.upsert_plugin_work_item(
            conn,
            work_item_id="awi_queued",
            project_id="proj_1",
            agent_key="local_agent",
            kind="hades.kanban_task_work.v1",
            status="queued",
            payload={
                "schema": "hades.kanban_task_work.v1",
                "memory_required": True,
                "title": "Fix checkout regression",
            },
        )
        hdb.upsert_plugin_work_item(
            conn,
            work_item_id="awi_failed",
            project_id="proj_1",
            agent_key="local_agent",
            kind="hades.kanban_task_work.v1",
            status="failed",
            payload={
                "schema": "hades.kanban_task_work.v1",
                "memory_required": True,
                "memory_search_status": {"status": "ready", "refs": ["memory:bug:1"]},
            },
        )
        hdb.upsert_plugin_work_item(
            conn,
            work_item_id="awi_other_project",
            project_id="proj_other",
            agent_key="local_agent",
            kind="hades.kanban_task_work.v1",
            status="queued",
            payload={"schema": "hades.kanban_task_work.v1", "memory_required": True},
        )

    monkeypatch.chdir(workspace)
    payload = load_backend_status_payload()
    task_work = payload["task_work"]

    assert task_work["project_id"] == "proj_1"
    assert task_work["total"] == 2
    assert task_work["queued"] == 1
    assert task_work["failed"] == 1
    assert task_work["shared_memory_required"] == 2
    assert task_work["shared_memory_context"] == 1
    assert task_work["missing_shared_memory_context"] == 1
    assert task_work["missing_work_item_ids"] == ["awi_queued"]
    assert task_work["worker_setup"]["status"] == "missing"
    assert task_work["worker_setup"]["local_workspace_id_present"] is False
    assert any("worker-setup" in action for action in payload["actions"])
    assert any("Inspect failed backend task work" in action for action in payload["actions"])
    assert any("Repair backend task work missing shared memory context" in action for action in payload["actions"])


def test_backend_status_prefers_remote_awareness_over_last_sync_summary(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import hades_backend_db as hdb
    import hermes_cli.hades_backend_runtime as runtime
    from hermes_cli.hades_backend_status import load_backend_status_payload

    workspace = tmp_path / "repo"
    workspace.mkdir()

    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"jobs": True, "memory": True},
        )
        hdb.upsert_workspace_binding(
            conn,
            project_id="proj_1",
            agent_id="agent_1",
            local_project_id="p_local",
            workspace_fingerprint="wf_1",
            display_path="~/repo",
            repo_root=str(workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit="abc123",
            backend_workspace_binding_id="wb_1",
        )
        hdb.replace_memory_cache(
            conn,
            project_id="proj_1",
            workspace_binding_id="wb_1",
            version="mem_v1",
            items=[{"kind": "resolved_bug", "summary": "Known login regression"}],
        )
        hdb.record_sync_state(
            conn,
            "last_sync_summary",
            {
                "memory_snapshots": 1,
                "artifacts_uploaded": 0,
                "artifacts_skipped": 0,
                "artifact_errors": 0,
                "source_slices_uploaded": 0,
                "source_slice_errors": 0,
                "source_slice_candidates": 0,
                "source_slice_jobs_waiting": 15,
                "bug_evidence_items": 0,
                "proposal_errors": 0,
            },
        )

    class FakeClient:
        def __init__(self):
            self.calls = []
            self.closed = False

        def project_awareness_status(self, **payload):
            self.calls.append(payload)
            return {
                "overall_status": "partial",
                "diagnosable_without_source": False,
                "coverage": {
                    "memory": {"status": "current", "count": 1743},
                    "artifacts": {
                        "status": "current",
                        "count": 1,
                        "schemas": {"hades.php_graph.v1": 1},
                        "latest_schema": "hades.php_graph.v1",
                    },
                    "code_graph": {
                        "status": "current",
                        "count": 1,
                        "schema": "hades.php_graph.v1",
                        "coverage_type": "code_graph",
                    },
                    "source_slices": {"status": "current", "count": 10},
                    "source_slice_candidates": {
                        "status": "pending",
                        "count": 200,
                        "pending_candidates": 10,
                        "waiting_jobs": 190,
                    },
                    "bug_evidence": {"status": "missing", "count": 0},
                    "causal_packs": {
                        "status": "none",
                        "valid": 0,
                        "invalid": 0,
                        "missing_for_open_bugs": 0,
                    },
                },
                "actions": ["Capture stack traces, failing tests, logs, or reproduction steps as typed bug evidence."],
            }

        def close(self):
            self.closed = True

    fake_client = FakeClient()
    monkeypatch.setattr(runtime, "client_from_config", lambda **kwargs: fake_client)
    monkeypatch.chdir(workspace)
    payload = load_backend_status_payload()
    binding_awareness = payload["bindings"][0]["awareness"]

    assert fake_client.calls == [{"project_id": "proj_1", "workspace_binding_id": "wb_1"}]
    assert fake_client.closed is True
    assert binding_awareness["coverage"]["memory_cache"]["items"] == 1743
    assert binding_awareness["coverage"]["project_artifacts"]["status"] == "current"
    assert binding_awareness["coverage"]["project_artifacts"]["count"] == 1
    assert binding_awareness["coverage"]["code_graph"]["status"] == "current"
    assert binding_awareness["coverage"]["source_slices"]["status"] == "current"
    assert binding_awareness["coverage"]["source_slices"]["count"] == 10
    assert binding_awareness["coverage"]["source_slice_candidates"]["pending_candidates"] == 10
    assert binding_awareness["coverage"]["source_slice_candidates"]["waiting_jobs"] == 190
    assert binding_awareness["quality"]["missing"] == ["bug_evidence"]
    assert binding_awareness["quality"]["summary_scope"] == "backend"
    assert binding_awareness["quality"]["actions"] == [
        "poll_source_slice_candidates",
        "approve_source_slice_jobs",
    ]


def test_remote_awareness_keeps_pending_candidates_distinct_from_waiting_jobs():
    from hermes_cli.hades_backend_status import _remote_binding_awareness_payload

    payload = _remote_binding_awareness_payload(
        {
            "overall_status": "partial",
            "coverage": {
                "source_slice_candidates": {
                    "status": "pending",
                    "count": 12,
                    "pending_candidates": 12,
                    "waiting_jobs": 0,
                }
            },
        },
        memory_cache=None,
        last_error=None,
        last_summary_updated_at=None,
    )

    assert payload["coverage"]["source_slice_candidates"] == {
        "status": "pending",
        "count": 12,
        "pending_candidates": 12,
        "waiting_jobs": 0,
    }
    assert payload["quality"]["actions"] == ["poll_source_slice_candidates"]


def test_remote_awareness_derives_pending_candidates_for_legacy_payload():
    from hermes_cli.hades_backend_status import _remote_binding_awareness_payload

    payload = _remote_binding_awareness_payload(
        {
            "overall_status": "partial",
            "coverage": {
                "source_slice_candidates": {
                    "status": "pending",
                    "count": 12,
                    "waiting_jobs": 7,
                }
            },
        },
        memory_cache=None,
        last_error=None,
        last_summary_updated_at=None,
    )

    assert payload["coverage"]["source_slice_candidates"]["pending_candidates"] == 5
    assert payload["quality"]["actions"] == [
        "poll_source_slice_candidates",
        "approve_source_slice_jobs",
    ]


def test_backend_status_explains_new_device_without_current_workspace_binding(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import hades_backend_db as hdb
    from hermes_cli.hades_backend_status import load_backend_status_payload

    workspace = tmp_path / "unmapped"
    workspace.mkdir()

    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="new-device",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"jobs": True, "memory": True},
        )

    monkeypatch.chdir(workspace)
    payload = load_backend_status_payload()
    identity = payload["identity"]

    assert payload["awareness"]["status"] == "unmapped"
    assert identity["project_memory"]["available"] is True
    assert identity["project_memory"]["portable_between_devices"] is True
    assert identity["workspace_binding"]["current_workspace_binding_id"] is None
    assert identity["workspace_binding"]["current_status"] == "unmapped"
    assert identity["login_recovery"] == {
        "can_use_project_memory_without_old_device": True,
        "current_workspace_mapped": False,
        "source_free_diagnosis_ready": False,
        "requires_workspace_binding_for_indexing": True,
        "recommended_next_action": (
            "Link this workspace with `hades backend bootstrap ...` or "
            "`hades project link <project>`, then run `hades backend sync`."
        ),
    }


def test_backend_status_reports_source_free_diagnosis_readiness(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import hades_backend_db as hdb
    from hermes_cli.hades_backend_status import load_backend_status_payload

    workspace = tmp_path / "repo"
    workspace.mkdir()

    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"jobs": True, "memory": True},
        )
        hdb.upsert_workspace_binding(
            conn,
            project_id="proj_1",
            agent_id="agent_1",
            local_project_id="p_local",
            workspace_fingerprint="wf_1",
            display_path="~/repo",
            repo_root=str(workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit="abc123",
            backend_workspace_binding_id="wb_1",
        )
        hdb.replace_memory_cache(
            conn,
            project_id="proj_1",
            workspace_binding_id="wb_1",
            version="mem_v1",
            items=[{"kind": "resolved_bug", "summary": "Known login regression"}],
        )
        hdb.record_sync_state(
            conn,
            "last_sync_summary",
            {
                "memory_snapshots": 1,
                "artifacts_uploaded": 1,
                "artifacts_skipped": 0,
                "artifact_errors": 0,
                "source_slices_uploaded": 2,
                "source_slice_errors": 0,
                "bug_evidence_items": 1,
                "proposal_errors": 0,
            },
        )

    monkeypatch.chdir(workspace)
    payload = load_backend_status_payload()
    binding_awareness = payload["bindings"][0]["awareness"]

    assert payload["awareness"] == {
        "status": "ready",
        "bindings": 1,
        "ready_bindings": 1,
        "partial_bindings": 0,
        "degraded_bindings": 0,
        "diagnosable_without_source_bindings": 1,
    }
    assert payload["actions"] == [
        "Run `hades backend quality-report --record` to establish a governance baseline.",
        "Run `hades backend worker-setup` in this checkout before claiming backend task work.",
    ]
    assert binding_awareness["status"] == "ready"
    assert binding_awareness["diagnosable_without_source"] is True
    assert binding_awareness["coverage"]["memory_cache"]["items"] == 1
    assert binding_awareness["coverage"]["memory_cache"]["version"] == "mem_v1"
    assert binding_awareness["coverage"]["project_artifacts"]["uploaded_last_sync"] == 1
    assert binding_awareness["coverage"]["project_artifacts"]["skipped_unchanged_last_sync"] == 0
    assert binding_awareness["coverage"]["source_slices"]["uploaded_last_sync"] == 2
    assert binding_awareness["coverage"]["bug_evidence"]["items_last_sync"] == 1
    assert binding_awareness["quality"]["confidence"] == "ready"
    assert binding_awareness["quality"]["missing"] == []
    assert payload["identity"]["project_memory"]["cached_items"] == 1
    assert payload["identity"]["workspace_binding"]["source_free_ready"] == 1
    assert payload["identity"]["workspace_binding"]["current_status"] == "ready"
    assert payload["identity"]["workspace_binding"]["current_source_free_ready"] is True
    assert payload["identity"]["login_recovery"] == {
        "can_use_project_memory_without_old_device": True,
        "current_workspace_mapped": True,
        "source_free_diagnosis_ready": True,
        "requires_workspace_binding_for_indexing": False,
        "recommended_next_action": "Project memory and source-free diagnosis are ready on this device.",
    }


def test_backend_status_treats_unchanged_artifact_skips_as_project_artifact_coverage(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import hades_backend_db as hdb
    from hermes_cli.hades_backend_status import load_backend_status_payload

    workspace = tmp_path / "repo"
    workspace.mkdir()
    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"memory": True, "jobs": True},
        )
        hdb.upsert_workspace_binding(
            conn,
            project_id="proj_1",
            agent_id="agent_1",
            local_project_id="p_local",
            workspace_fingerprint="wf_1",
            display_path="~/repo",
            repo_root=str(workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit="abc123",
            backend_workspace_binding_id="wb_1",
        )
        hdb.replace_memory_cache(
            conn,
            project_id="proj_1",
            workspace_binding_id="wb_1",
            version="mem_v1",
            items=[{"kind": "resolved_bug", "summary": "Known login regression"}],
        )
        hdb.record_sync_state(
            conn,
            "last_sync_summary",
            {
                "memory_snapshots": 1,
                "artifacts_uploaded": 0,
                "artifacts_skipped": 1,
                "artifact_errors": 0,
                "source_slices_uploaded": 1,
                "source_slice_errors": 0,
                "bug_evidence_items": 1,
                "proposal_errors": 0,
            },
        )

    payload = load_backend_status_payload()
    coverage = payload["bindings"][0]["awareness"]["coverage"]["project_artifacts"]

    assert coverage["status"] == "present"
    assert coverage["uploaded_last_sync"] == 0
    assert coverage["skipped_unchanged_last_sync"] == 1
    assert payload["bindings"][0]["awareness"]["diagnosable_without_source"] is True


def test_backend_status_keeps_multi_binding_aggregate_summary_partial(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import hades_backend_db as hdb
    from hermes_cli.hades_backend_status import load_backend_status_payload

    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"jobs": True, "memory": True},
        )
        for index in range(2):
            workspace = tmp_path / f"repo_{index}"
            workspace.mkdir()
            workspace_binding_id = f"wb_{index}"
            hdb.upsert_workspace_binding(
                conn,
                project_id="proj_1",
                agent_id="agent_1",
                local_project_id=f"p_local_{index}",
                workspace_fingerprint=f"wf_{index}",
                display_path=f"~/repo_{index}",
                repo_root=str(workspace),
                git_remote_display="",
                git_remote_hash="",
                head_commit="abc123",
                backend_workspace_binding_id=workspace_binding_id,
            )
            hdb.replace_memory_cache(
                conn,
                project_id="proj_1",
                workspace_binding_id=workspace_binding_id,
                version="mem_v1",
                items=[{"summary": f"memory {index}"}],
            )
        hdb.record_sync_state(
            conn,
            "last_sync_summary",
            {
                "artifacts_uploaded": 2,
                "artifact_errors": 0,
                "source_slices_uploaded": 2,
                "source_slice_errors": 0,
                "bug_evidence_items": 2,
                "proposal_errors": 0,
            },
        )

    payload = load_backend_status_payload()

    assert payload["awareness"]["status"] == "partial"
    assert payload["awareness"]["diagnosable_without_source_bindings"] == 0
    assert {
        binding["awareness"]["quality"]["summary_scope"]
        for binding in payload["bindings"]
    } == {"aggregate"}
    assert all(
        not binding["awareness"]["diagnosable_without_source"]
        for binding in payload["bindings"]
    )
    assert all(
        binding["awareness"]["coverage"]["project_artifacts"]["status"]
        == "aggregate"
        for binding in payload["bindings"]
    )
    assert all(
        "project_artifact_index" in binding["awareness"]["quality"]["missing"]
        for binding in payload["bindings"]
    )


def test_sync_auth_quarantine_counts_one_401_per_route_cycle(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import hades_backend_db as hdb
    from hermes_cli.hades_backend_client import HadesBackendError
    from hermes_cli.hades_backend_sync import run_backend_sync

    workspace = tmp_path / "repo"
    workspace.mkdir()
    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_auth",
            project_id="project_auth",
            base_url="https://backend.invalid",
            label="auth",
            token_env_key="TOKEN_AUTH",
            capabilities={"sync_git_tree": False, "populate_backend_ast": False},
        )
        hdb.upsert_workspace_binding(
            conn,
            project_id="project_auth",
            agent_id="agent_auth",
            local_project_id="local_auth",
            workspace_fingerprint="wf_auth",
            display_path="~/repo",
            repo_root=str(workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id="binding_auth",
        )

    class UnauthorizedClient:
        @staticmethod
        def _fail():
            raise HadesBackendError("unauthorized", status_code=401)

        def capabilities(self):
            return self._fail()

        def memory_snapshot(self, **payload):
            return self._fail()

        def list_inbox(self, **payload):
            return self._fail()

        def pull_jobs(self, **payload):
            return self._fail()

    summaries = []
    for now in (100, 200, 300):
        summaries.append(
            run_backend_sync(
                client_factory=UnauthorizedClient,
                quiet=True,
                now=now,
            ).summary
        )

    with hdb.connect_closing() as conn:
        health = hdb.get_route_auth_health(
            conn, project_id="project_auth", agent_id="agent_auth"
        )
        binding = hdb.get_binding_for_fingerprint(conn, "wf_auth")

    assert [summary["auth_failed_routes"] for summary in summaries] == [1, 1, 1]
    assert [summary["auth_quarantined_routes"] for summary in summaries] == [0, 0, 1]
    assert health is not None and health["consecutive_failures"] == 3
    assert binding is not None and binding.status == "auth_failed"


def test_sync_does_not_quarantine_binding_after_persisted_token_rotation(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import hades_backend_db as hdb
    from hermes_cli import hades_backend_runtime as runtime
    from hermes_cli import hades_backend_sync
    from hermes_cli.hades_backend_client import HadesBackendError

    workspace = tmp_path / "repo"
    workspace.mkdir()
    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_auth",
            project_id="project_auth",
            base_url="https://backend.invalid",
            label="auth",
            token_env_key="TOKEN_AUTH",
            capabilities={"sync_git_tree": False, "populate_backend_ast": False},
        )
        hdb.upsert_workspace_binding(
            conn,
            project_id="project_auth",
            agent_id="agent_auth",
            local_project_id="local_auth",
            workspace_fingerprint="wf_auth",
            display_path="~/repo",
            repo_root=str(workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id="binding_auth",
        )

    class UnauthorizedClient:
        @staticmethod
        def _fail():
            raise HadesBackendError("unauthorized", status_code=401)

        def capabilities(self):
            return self._fail()

        def memory_snapshot(self, **payload):
            return self._fail()

        def list_inbox(self, **payload):
            return self._fail()

        def pull_jobs(self, **payload):
            return self._fail()

    monkeypatch.setattr(runtime, "agent_token", lambda selected: "old-token")
    monkeypatch.setattr(
        hades_backend_sync,
        "_persisted_credential_fingerprint",
        lambda selected: hades_backend_sync._credential_fingerprint("new-token"),
        raising=False,
    )

    result = hades_backend_sync.run_backend_sync(
        client_factory=UnauthorizedClient, quiet=True
    )

    with hdb.connect_closing() as conn:
        health = hdb.get_route_auth_health(
            conn, project_id="project_auth", agent_id="agent_auth"
        )
        binding = hdb.get_binding_for_fingerprint(conn, "wf_auth")
        last_error = hdb.get_sync_state(conn, "last_sync_error")

    assert result.exit_code == 0
    assert result.summary["stale_auth_routes"] == 1
    assert result.summary["auth_failed_routes"] == 0
    assert binding is not None and binding.status == "linked"
    assert health is None
    assert last_error is None


def test_sync_auth_quarantine_resets_on_authenticated_success(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import hades_backend_db as hdb
    from hermes_cli.hades_backend_client import HadesBackendError
    from hermes_cli.hades_backend_sync import run_backend_sync

    workspace = tmp_path / "repo"
    workspace.mkdir()
    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_auth",
            project_id="project_auth",
            base_url="https://backend.invalid",
            label="auth",
            token_env_key="TOKEN_AUTH",
            capabilities={"sync_git_tree": False, "populate_backend_ast": False},
        )
        hdb.upsert_workspace_binding(
            conn,
            project_id="project_auth",
            agent_id="agent_auth",
            local_project_id="local_auth",
            workspace_fingerprint="wf_auth",
            display_path="~/repo",
            repo_root=str(workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id="binding_auth",
        )

    class UnauthorizedClient:
        def capabilities(self):
            raise HadesBackendError("unauthorized", status_code=401)

        memory_snapshot = capabilities
        list_inbox = capabilities
        pull_jobs = capabilities

    class HealthyClient:
        def capabilities(self):
            return {"persephone_agent_queue_v1": True}

        def memory_snapshot(self, **payload):
            return {"items": []}

        def list_inbox(self, **payload):
            return {"events": [], "cursor": payload.get("cursor")}

        def pull_jobs(self, **payload):
            return {"jobs": []}

    run_backend_sync(client_factory=UnauthorizedClient, quiet=True, now=100)
    healthy = run_backend_sync(client_factory=HealthyClient, quiet=True, now=200)

    with hdb.connect_closing() as conn:
        health = hdb.get_route_auth_health(
            conn, project_id="project_auth", agent_id="agent_auth"
        )
        binding = hdb.get_binding_for_fingerprint(conn, "wf_auth")

    assert healthy.summary["auth_failed_routes"] == 0
    assert healthy.summary["auth_quarantined_routes"] == 0
    assert health is None
    assert binding is not None and binding.status == "linked"


def test_sync_auth_quarantine_ignores_non_401_failures(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import hades_backend_db as hdb
    from hermes_cli.hades_backend_client import HadesBackendError
    from hermes_cli.hades_backend_sync import run_backend_sync

    workspace = tmp_path / "repo"
    workspace.mkdir()
    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_auth",
            project_id="project_auth",
            base_url="https://backend.invalid",
            label="auth",
            token_env_key="TOKEN_AUTH",
            capabilities={"sync_git_tree": False, "populate_backend_ast": False},
        )
        hdb.upsert_workspace_binding(
            conn,
            project_id="project_auth",
            agent_id="agent_auth",
            local_project_id="local_auth",
            workspace_fingerprint="wf_auth",
            display_path="~/repo",
            repo_root=str(workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id="binding_auth",
        )

    class UnauthorizedClient:
        def capabilities(self):
            raise HadesBackendError("unauthorized", status_code=401)

        memory_snapshot = capabilities
        list_inbox = capabilities
        pull_jobs = capabilities

    run_backend_sync(client_factory=UnauthorizedClient, quiet=True)

    for status_code in (403, 404, 429, 500):
        class OtherFailureClient:
            def capabilities(self):
                raise HadesBackendError("other failure", status_code=status_code)

            memory_snapshot = capabilities
            list_inbox = capabilities
            pull_jobs = capabilities

        run_backend_sync(client_factory=OtherFailureClient, quiet=True)

    with hdb.connect_closing() as conn:
        health = hdb.get_route_auth_health(
            conn, project_id="project_auth", agent_id="agent_auth"
        )
        binding = hdb.get_binding_for_fingerprint(conn, "wf_auth")

    assert health is not None and health["consecutive_failures"] == 1
    assert binding is not None and binding.status == "linked"


def test_manual_sync_success_clears_background_backoff_state(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import hades_backend_db as hdb
    from hermes_cli.hades_backend_sync import BACKGROUND_SYNC_STATE_KEY, run_backend_sync

    workspace = tmp_path / "repo"
    workspace.mkdir()

    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"jobs": True},
        )
        hdb.upsert_workspace_binding(
            conn,
            project_id="proj_1",
            agent_id="agent_1",
            local_project_id="p_local",
            workspace_fingerprint="wf_1",
            display_path="~/repo",
            repo_root=str(workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id="wb_1",
        )
        hdb.record_sync_state(
            conn,
            BACKGROUND_SYNC_STATE_KEY,
            {"status": "failed", "failure_count": 3, "next_attempt_at": 9999},
        )

    class FakeClient:
        def memory_snapshot(self, **payload):
            return {"items": []}

        def pull_jobs(self, **payload):
            return {"jobs": []}

    result = run_backend_sync(client_factory=lambda: FakeClient())

    with hdb.connect_closing() as conn:
        state = hdb.get_sync_state(conn, BACKGROUND_SYNC_STATE_KEY)

    assert result.exit_code == 0
    assert state is None


def test_background_sync_skips_when_already_running(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import hades_backend_db as hdb
    import hermes_cli.hades_backend_sync as sync

    workspace = tmp_path / "repo"
    workspace.mkdir()

    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"jobs": True},
        )
        hdb.upsert_workspace_binding(
            conn,
            project_id="proj_1",
            agent_id="agent_1",
            local_project_id="p_local",
            workspace_fingerprint="wf_1",
            display_path="~/repo",
            repo_root=str(workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id="wb_1",
        )

    monkeypatch.setattr(sync, "_BACKGROUND_SYNC_RUNNING", True)

    decision = sync.maybe_run_backend_sync(now=3000, run_inline=True)

    assert decision.status == "skipped"
    assert decision.reason == "already_running"
