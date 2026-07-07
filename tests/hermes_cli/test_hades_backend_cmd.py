from __future__ import annotations

import json
from types import SimpleNamespace


def _seed_current_backend_workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "repo"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    from hermes_cli import hades_backend_db as hdb

    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev-box",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"memory": True},
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
    return workspace


def test_backend_setup_registers_agent_and_persists_derived_token(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    import hermes_cli.hades_backend_cmd as cmd
    from hermes_cli.config import load_config

    class FakeClient:
        def __init__(self, base_url, token, **kwargs):
            self.base_url = base_url
            self.token = token

        def verify_token(self, *, project_id):
            assert self.token == "bootstrap-token"
            return {"project_id": project_id, "capabilities": {"memory": True}}

        def register_agent(self, **payload):
            assert payload["project_id"] == "proj_1"
            assert payload["label"] == "dev-box"
            return {
                "agent_id": payload["agent_id"],
                "agent_token": "derived-token",
                "capabilities": {"memory": True, "jobs": True},
            }

    monkeypatch.setattr(cmd, "HadesBackendClient", FakeClient)
    monkeypatch.setattr(cmd, "_detect_default_capabilities", lambda: ["read_files"])

    rc = cmd.hades_backend_command(
        SimpleNamespace(
            backend_action="setup",
            url="https://backend.example",
            project_token="bootstrap-token",
            project_id="proj_1",
            label="dev-box",
            non_interactive=True,
        )
    )

    output = capsys.readouterr().out
    config = load_config()
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")

    assert rc == 0
    assert "Backend setup complete" in output
    assert config["backend"]["base_url"] == "https://backend.example"
    assert config["backend"]["default_project_id"] == "proj_1"
    assert config["memory"]["provider"] == "hades_backend"
    assert "derived-token" in env_text
    assert "bootstrap-token" not in env_text


def test_project_link_uses_backend_binding_and_stores_redacted_path(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    from hermes_cli import projects_db as pdb
    import hermes_cli.projects_cmd as projects_cmd

    with pdb.connect_closing() as conn:
        pdb.create_project(conn, name="Demo", folders=[str(tmp_path)])

    class FakeClient:
        def bind_workspace(self, **payload):
            assert payload["project_id"] == "backend_proj"
            assert payload["local_project_id"].startswith("p_")
            assert payload["display_path"].startswith("~") or payload["display_path"] == str(tmp_path)
            return {"workspace_binding_id": "wb_backend_1"}

    monkeypatch.setattr(projects_cmd, "_backend_client_from_config", lambda: FakeClient())
    monkeypatch.setattr(projects_cmd, "_default_backend_agent", lambda: SimpleNamespace(agent_id="agent_1", project_id="backend_proj"))

    rc = projects_cmd.projects_command(
        SimpleNamespace(
            project_action="link",
            project="demo",
            path=str(tmp_path),
            backend_project_id=None,
            yes=False,
        )
    )

    output = capsys.readouterr().out

    assert rc == 0
    assert "Linked demo" in output


def test_project_unlink_notifies_backend_before_marking_local_binding(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    from hermes_cli import hades_backend_db as hdb
    from hermes_cli import projects_db as pdb
    import hermes_cli.projects_cmd as projects_cmd
    from hermes_cli.hades_backend_runtime import workspace_fingerprint

    workspace = tmp_path / "repo"
    workspace.mkdir()

    with pdb.connect_closing() as conn:
        pdb.create_project(conn, name="Demo", folders=[str(workspace)], primary_path=str(workspace))
    fp = workspace_fingerprint(workspace, "backend_proj")
    with hdb.connect_closing() as conn:
        hdb.upsert_workspace_binding(
            conn,
            project_id="backend_proj",
            agent_id="agent_1",
            local_project_id="p_1",
            workspace_fingerprint=fp,
            display_path="~/repo",
            repo_root=str(workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id="wb_backend_1",
        )

    class FakeClient:
        def __init__(self):
            self.unlinked = []

        def unlink_workspace(self, workspace_binding_id, **payload):
            self.unlinked.append((workspace_binding_id, payload))
            return {"ok": True}

    fake = FakeClient()
    monkeypatch.setattr(projects_cmd, "_backend_client_from_config", lambda: fake)
    monkeypatch.setattr(projects_cmd, "_default_backend_agent", lambda: SimpleNamespace(agent_id="agent_1", project_id="backend_proj"))

    rc = projects_cmd.projects_command(
        SimpleNamespace(
            project_action="unlink",
            project="demo",
            path=str(workspace),
            yes=True,
        )
    )

    output = capsys.readouterr().out
    with hdb.connect_closing() as conn:
        binding = hdb.get_binding_for_fingerprint(conn, fp)

    assert rc == 0
    assert "Unlinked demo" in output
    assert fake.unlinked == [("wb_backend_1", {"project_id": "backend_proj", "agent_id": "agent_1"})]
    assert binding is not None
    assert binding.status == "unlinked"


def test_backend_sync_executes_read_only_job(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "README.md").write_text("hello\napi_key=secret-token-123\n", encoding="utf-8")

    import hermes_cli.hades_backend_cmd as cmd
    import hermes_cli.hades_backend_runtime as runtime
    from hermes_cli import hades_backend_db as hdb

    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev-box",
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

    class FakeClient:
        def __init__(self):
            self.statuses = []
            self.results = []

        def pull_jobs(self, **payload):
            assert payload["workspace_binding_id"] == "wb_1"
            return {
                "jobs": [
                    {
                        "job_id": "job_1",
                        "capability": "read_files",
                        "payload": {"paths": ["README.md"], "max_bytes": 200},
                    }
                ]
            }

        def update_job_status(self, job_id, **payload):
            self.statuses.append((job_id, payload["status"], payload))
            return {}

        def submit_job_result(self, job_id, **payload):
            self.results.append((job_id, payload))
            return {}

    fake = FakeClient()
    monkeypatch.setattr(runtime, "client_from_config", lambda: fake)

    rc = cmd.hades_backend_command(SimpleNamespace(backend_action="sync"))

    output = capsys.readouterr().out
    with hdb.connect_closing() as conn:
        job = hdb.get_job(conn, "job_1")

    assert rc == 0
    assert "completed 1" in output
    assert job is not None
    assert job.status == "completed"
    assert job.result is not None
    assert "secret-token-123" not in str(job.result)
    assert [status for _, status, _ in fake.statuses] == ["received", "started"]
    assert fake.results[0][1]["status"] == "completed"


def test_backend_worker_command_dispatches_plugin_worker(monkeypatch, capsys):
    import hermes_cli.hades_backend_cmd as cmd
    import hermes_cli.hades_plugin_worker as worker

    calls = []

    def fake_run_plugin_worker_once(**kwargs):
        calls.append(kwargs)
        return worker.PluginWorkerResult({"listed": 1, "claimed": 1, "completed": 1, "failed": 0, "skipped": 0}, 0)

    monkeypatch.setattr(worker, "run_plugin_worker_once", fake_run_plugin_worker_once)

    rc = cmd.hades_backend_command(
        SimpleNamespace(
            backend_action="worker",
            once=True,
            project_id="proj_1",
            local_workspace_id="lw_1",
            agent_key="local_agent",
            limit=2,
            json=True,
        )
    )

    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["completed"] == 1
    assert calls == [
        {
            "project_id": "proj_1",
            "local_workspace_id": "lw_1",
            "agent_key": "local_agent",
            "limit": 2,
            "quiet": True,
        }
    ]


def test_backend_worker_json_error_output_is_machine_readable(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    import hermes_cli.hades_backend_cmd as cmd
    from hermes_cli import hades_backend_db as hdb

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

    rc = cmd.hades_backend_command(
        SimpleNamespace(
            backend_action="worker",
            once=True,
            project_id=None,
            local_workspace_id=None,
            agent_key="local_agent",
            limit=1,
            json=True,
        )
    )

    output = capsys.readouterr().out.strip()

    assert rc == 1
    assert json.loads(output) == {"error": 1}
    assert output.startswith("{")


def test_backend_sync_updates_memory_cache_and_pending_proposals(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    workspace = tmp_path / "repo"
    workspace.mkdir()

    import hermes_cli.hades_backend_cmd as cmd
    import hermes_cli.hades_backend_runtime as runtime
    from hermes_cli import hades_backend_db as hdb

    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev-box",
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
        proposal = hdb.create_memory_proposal(
            conn,
            project_id="proj_1",
            workspace_binding_id="wb_1",
            action="create",
            intent="memory_write",
            summary="Use /api/hades/v1 for backend calls",
            provenance={"source": "test"},
        )
        hdb.record_sync_state(conn, "last_sync_error", {"message": "previous backend failure"})

    class FakeClient:
        def __init__(self):
            self.proposals = []

        def memory_snapshot(self, **payload):
            assert payload["workspace_binding_id"] == "wb_1"
            return {
                "version": "mem_v2",
                "items": [{"summary": "Shared project context", "memory_id": "m_1"}],
            }

        def create_memory_proposal(self, **payload):
            self.proposals.append(payload)
            assert payload["local_proposal_id"] == proposal.id
            return {"status": "accepted", "reason": "auto_accepted"}

        def pull_jobs(self, **payload):
            return {"jobs": []}

    fake = FakeClient()
    monkeypatch.setattr(runtime, "client_from_config", lambda: fake)

    rc = cmd.hades_backend_command(SimpleNamespace(backend_action="sync"))

    output = capsys.readouterr().out
    with hdb.connect_closing() as conn:
        cache = hdb.get_memory_cache(conn, "wb_1")
        proposals = hdb.list_memory_proposals(conn)
        summary = hdb.get_sync_state(conn, "last_sync_summary")
        last_error = hdb.get_sync_state(conn, "last_sync_error")

    assert rc == 0
    assert "memory 1" in output
    assert cache is not None
    assert cache.version == "mem_v2"
    assert cache.items[0]["summary"] == "Shared project context"
    assert proposals[0].id == proposal.id
    assert proposals[0].status == "accepted"
    assert proposals[0].reason == "auto_accepted"
    assert fake.proposals[0]["summary"] == "Use /api/hades/v1 for backend calls"
    assert summary["memory_snapshots"] == 1
    assert summary["proposals_synced"] == 1
    assert last_error is None


def test_backend_sync_marks_backend_pending_proposals_as_submitted(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    workspace = tmp_path / "repo"
    workspace.mkdir()

    import hermes_cli.hades_backend_cmd as cmd
    import hermes_cli.hades_backend_runtime as runtime
    from hermes_cli import hades_backend_db as hdb

    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev-box",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"memory": True},
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
        proposal = hdb.create_memory_proposal(
            conn,
            project_id="proj_1",
            workspace_binding_id="wb_1",
            action="create",
            intent="note_backfill_candidate",
            summary="Controller.php handles 3 taxonomy routes.",
            provenance={"source": "hades_note_quality"},
        )

    class FakeClient:
        def __init__(self):
            self.proposals = []

        def memory_snapshot(self, **payload):
            return {"version": "mem_v1", "items": []}

        def create_memory_proposal(self, **payload):
            self.proposals.append(payload)
            assert payload["intent"] == "note_backfill_candidate"
            return {"proposal": {"status": "pending", "reason_code": "manual_review_required"}}

        def pull_jobs(self, **payload):
            return {"jobs": []}

    fake = FakeClient()
    monkeypatch.setattr(runtime, "client_from_config", lambda: fake)

    first_rc = cmd.hades_backend_command(SimpleNamespace(backend_action="sync"))
    capsys.readouterr()
    second_rc = cmd.hades_backend_command(SimpleNamespace(backend_action="sync"))
    capsys.readouterr()

    with hdb.connect_closing() as conn:
        proposals = hdb.list_memory_proposals(conn)
        summary = hdb.get_sync_state(conn, "last_sync_summary")

    assert first_rc == 0
    assert second_rc == 0
    assert proposals[0].id == proposal.id
    assert proposals[0].status == "submitted"
    assert proposals[0].reason == "manual_review_required"
    assert len(fake.proposals) == 1
    assert summary["proposals_synced"] == 0


def test_backend_bootstrap_sets_up_project_links_workspace_and_syncs(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    workspace = tmp_path / "repo"
    workspace.mkdir()

    import hermes_cli.hades_backend_cmd as cmd
    import hermes_cli.hades_backend_runtime as runtime
    from hermes_cli import hades_backend_db as hdb
    from hermes_cli import projects_db as pdb
    from hermes_cli.config import load_config

    class BootstrapClient:
        def __init__(self, base_url, token, **kwargs):
            assert base_url == "https://backend.example"
            assert token == "bootstrap-token"

        def verify_token(self, *, project_id):
            assert project_id == "backend_proj"
            return {"ok": True}

        def register_agent(self, **payload):
            assert payload["project_id"] == "backend_proj"
            return {
                "agent_id": payload["agent_id"],
                "agent_token": "derived-token",
                "capabilities": {"memory": True, "jobs": True},
            }

    class OperationalClient:
        def __init__(self):
            self.bound = []
            self.pulled = []

        def bind_workspace(self, **payload):
            self.bound.append(payload)
            assert payload["project_id"] == "backend_proj"
            assert payload["display_path"]
            return {"workspace_binding_id": "wb_bootstrap_1"}

        def memory_snapshot(self, **payload):
            return {"version": "v1", "items": []}

        def pull_jobs(self, **payload):
            self.pulled.append(payload)
            return {"jobs": []}

    operational = OperationalClient()
    monkeypatch.setattr(cmd, "HadesBackendClient", BootstrapClient)
    monkeypatch.setattr(runtime, "client_from_config", lambda: operational)
    monkeypatch.setattr(cmd, "_detect_default_capabilities", lambda: ["read_files"])

    rc = cmd.hades_backend_command(
        SimpleNamespace(
            backend_action="bootstrap",
            url="https://backend.example",
            project_id="backend_proj",
            project_token="bootstrap-token",
            workspace=str(workspace),
            project_name="Demo Project",
            non_interactive=True,
        )
    )

    output = capsys.readouterr().out
    config = load_config()
    env_text = (tmp_path / "home" / ".env").read_text(encoding="utf-8")

    with pdb.connect_closing() as conn:
        project = pdb.project_for_path(conn, str(workspace))
    with hdb.connect_closing() as conn:
        bindings = hdb.list_workspace_bindings(conn, status="linked")
        summary = hdb.get_sync_state(conn, "last_sync_summary")

    assert rc == 0
    assert "Hades backend bootstrap complete" in output
    assert project is not None
    assert project.name == "Demo Project"
    assert config["backend"]["default_project_id"] == "backend_proj"
    assert "derived-token" in env_text
    assert "bootstrap-token" not in env_text
    assert bindings[0].backend_workspace_binding_id == "wb_bootstrap_1"
    assert operational.bound
    assert operational.pulled
    assert summary["pulled"] == 0


def test_backend_status_json_exposes_actionable_degraded_state(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import hades_backend_db as hdb
    import hermes_cli.hades_backend_cmd as cmd

    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev-box",
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
            repo_root=str(tmp_path / "repo"),
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id="wb_1",
        )
        hdb.upsert_job(
            conn,
            job_id="job_wait",
            project_id="proj_1",
            workspace_binding_id="wb_1",
            capability="read_files",
            payload={},
            status="waiting_confirmation",
        )
        refused = hdb.create_memory_proposal(
            conn,
            project_id="proj_1",
            workspace_binding_id="wb_1",
            action="update",
            intent="memory_write",
            summary="Update backend docs",
            provenance={},
        )
        hdb.mark_memory_proposal_status(conn, refused.id, "refused", "policy_denied")
        hdb.record_sync_state(conn, "last_sync_error", {"message": "backend unavailable"})

    rc = cmd.hades_backend_command(SimpleNamespace(backend_action="status", json=True))

    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["configured"] is True
    assert payload["job_counts"] == {"waiting_confirmation": 1}
    assert payload["proposal_counts"] == {"refused": 1}
    assert payload["degraded"] is True
    assert payload["actions"] == [
        "Review 1 backend job(s) waiting for confirmation.",
        "Review 1 refused/conflicted memory proposal(s).",
        "Inspect last backend sync error and rerun `hades backend sync`.",
        "Run `hades backend quality-report --record` to establish a governance baseline.",
    ]


def test_backend_status_text_prints_identity_next_step(monkeypatch, tmp_path, capsys):
    _seed_current_backend_workspace(monkeypatch, tmp_path)

    import hermes_cli.hades_backend_cmd as cmd

    rc = cmd.hades_backend_command(SimpleNamespace(backend_action="status", json=False))
    output = capsys.readouterr().out

    assert rc == 0
    assert "Next identity step:" in output
    assert "Run `hades backend sync`" in output
    assert "before source-free diagnosis" in output


def test_backend_support_report_json_redacts_paths_and_secrets(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import hades_backend_db as hdb
    import hermes_cli.hades_backend_cmd as cmd

    workspace = tmp_path / "repo"
    workspace.mkdir()
    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev-box",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"memory": True, "jobs": True},
        )
        hdb.upsert_workspace_binding(
            conn,
            project_id="proj_1",
            agent_id="agent_1",
            local_project_id="p_local",
            workspace_fingerprint="wf_1",
            display_path=str(workspace),
            repo_root=str(workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit="a" * 40,
            backend_workspace_binding_id="wb_1",
        )
        hdb.record_sync_state(
            conn,
            "last_sync_error",
            {"message": f"token=super-secret-token failed while reading {workspace / '.env'}"},
        )

    rc = cmd.hades_backend_command(SimpleNamespace(backend_action="support-report", json=True))
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert rc == 0
    assert payload["schema"] == "hades.backend_support_report.v1"
    assert payload["configured"] is True
    assert payload["agent"]["base_url"] == "https://backend.example"
    assert payload["bindings"][0]["display_path"] == {"present": True, "kind": "absolute_redacted"}
    assert payload["bindings"][0]["head_commit_short"] == "a" * 12
    assert payload["sync"]["last_error"]["message"] == "token=*** failed while reading [path]"
    assert "super-secret-token" not in output
    assert str(workspace) not in output
    assert ".env" not in output


def test_backend_privacy_export_defaults_to_metadata_only(monkeypatch, tmp_path, capsys):
    _seed_current_backend_workspace(monkeypatch, tmp_path)

    import hermes_cli.hades_backend_cmd as cmd
    import hermes_cli.hades_backend_runtime as runtime

    class FakeClient:
        def __init__(self):
            self.calls = []
            self.closed = 0

        def privacy_export(self, **payload):
            self.calls.append(payload)
            return {
                "include_content": payload["include_content"],
                "counts": {"bug_reports": 1, "source_slices": 1},
                "collections": {"source_slices": [{"id": "slice_1"}]},
            }

        def close(self):
            self.closed += 1

    fake = FakeClient()
    monkeypatch.setattr(runtime, "client_from_config", lambda: fake)

    rc = cmd.hades_backend_command(
        SimpleNamespace(backend_action="privacy-export", include_content=False, json=True)
    )

    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["include_content"] is False
    assert fake.calls == [
        {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "include_content": False,
        }
    ]
    assert fake.closed == 1


def test_backend_privacy_delete_is_dry_run_unless_confirmed(monkeypatch, tmp_path, capsys):
    _seed_current_backend_workspace(monkeypatch, tmp_path)

    import hermes_cli.hades_backend_cmd as cmd
    import hermes_cli.hades_backend_runtime as runtime

    class FakeClient:
        def __init__(self):
            self.calls = []

        def privacy_delete(self, **payload):
            self.calls.append(payload)
            return {"dry_run": payload["dry_run"], "would_delete": {"hades_bug_reports": 2}}

    fake = FakeClient()
    monkeypatch.setattr(runtime, "client_from_config", lambda: fake)

    rc = cmd.hades_backend_command(
        SimpleNamespace(backend_action="privacy-delete", yes=False, json=True)
    )

    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["dry_run"] is True
    assert fake.calls == [
        {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "dry_run": True,
            "confirm": False,
        }
    ]


def test_backend_retention_cleanup_requires_yes_for_confirmed_delete(monkeypatch, tmp_path, capsys):
    _seed_current_backend_workspace(monkeypatch, tmp_path)

    import hermes_cli.hades_backend_cmd as cmd
    import hermes_cli.hades_backend_runtime as runtime

    class FakeClient:
        def __init__(self):
            self.calls = []

        def privacy_retention_cleanup(self, **payload):
            self.calls.append(payload)
            return {"dry_run": payload["dry_run"], "deleted": {"hades_source_slices": 1}}

    fake = FakeClient()
    monkeypatch.setattr(runtime, "client_from_config", lambda: fake)

    rc = cmd.hades_backend_command(
        SimpleNamespace(backend_action="retention-cleanup", retention_days=30, yes=True, json=True)
    )

    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["dry_run"] is False
    assert fake.calls == [
        {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "retention_days": 30,
            "dry_run": False,
            "confirm": True,
        }
    ]


def test_backend_jobs_json_lists_waiting_jobs(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import hades_backend_db as hdb
    import hermes_cli.hades_backend_cmd as cmd

    with hdb.connect_closing() as conn:
        hdb.upsert_job(
            conn,
            job_id="job_wait",
            project_id="proj_1",
            workspace_binding_id="wb_1",
            capability="read_files",
            payload={"paths": ["README.md"]},
            status="waiting_confirmation",
        )
        hdb.upsert_job(
            conn,
            job_id="job_done",
            project_id="proj_1",
            workspace_binding_id="wb_1",
            capability="read_files",
            payload={},
            status="completed",
        )

    rc = cmd.hades_backend_command(SimpleNamespace(backend_action="jobs", status=None, all=False, json=True))

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert [job["job_id"] for job in payload["jobs"]] == ["job_wait"]
    assert payload["jobs"][0]["payload_keys"] == ["paths"]


def test_backend_approve_job_executes_waiting_job(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "README.md").write_text("hello\n", encoding="utf-8")

    from hermes_cli import hades_backend_db as hdb
    import hermes_cli.hades_backend_cmd as cmd
    import hermes_cli.hades_backend_runtime as runtime

    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev-box",
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
            job_id="job_wait",
            project_id="proj_1",
            workspace_binding_id="wb_1",
            capability="read_files",
            payload={"paths": ["README.md"]},
            status="waiting_confirmation",
        )

    class FakeClient:
        def __init__(self):
            self.statuses = []
            self.results = []

        def update_job_status(self, job_id, **payload):
            self.statuses.append((job_id, payload["status"], payload))
            return {}

        def submit_job_result(self, job_id, **payload):
            self.results.append((job_id, payload))
            return {}

    fake = FakeClient()
    monkeypatch.setattr(runtime, "client_from_config", lambda: fake)

    rc = cmd.hades_backend_command(SimpleNamespace(backend_action="approve-job", job_id="job_wait"))

    output = capsys.readouterr().out
    with hdb.connect_closing() as conn:
        job = hdb.get_job(conn, "job_wait")

    assert rc == 0
    assert "completed" in output
    assert job is not None
    assert job.status == "completed"
    assert fake.statuses == [("job_wait", "started", fake.statuses[0][2])]
    assert fake.results[0][0] == "job_wait"
    assert fake.results[0][1]["status"] == "completed"


def test_backend_refuse_job_cancels_waiting_job(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import hades_backend_db as hdb
    import hermes_cli.hades_backend_cmd as cmd
    import hermes_cli.hades_backend_runtime as runtime

    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev-box",
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
            repo_root=str(tmp_path / "repo"),
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id="wb_1",
        )
        hdb.upsert_job(
            conn,
            job_id="job_wait",
            project_id="proj_1",
            workspace_binding_id="wb_1",
            capability="read_files",
            payload={},
            status="waiting_confirmation",
        )

    class FakeClient:
        def __init__(self):
            self.statuses = []

        def update_job_status(self, job_id, **payload):
            self.statuses.append((job_id, payload["status"], payload))
            return {}

    fake = FakeClient()
    monkeypatch.setattr(runtime, "client_from_config", lambda: fake)

    rc = cmd.hades_backend_command(
        SimpleNamespace(backend_action="refuse-job", job_id="job_wait", reason="too broad")
    )

    output = capsys.readouterr().out
    with hdb.connect_closing() as conn:
        job = hdb.get_job(conn, "job_wait")

    assert rc == 0
    assert "cancelled" in output
    assert job is not None
    assert job.status == "cancelled"
    assert job.result == {"summary": "too broad"}
    assert fake.statuses[0][0] == "job_wait"
    assert fake.statuses[0][1] == "cancelled"
    assert fake.statuses[0][2]["reason"] == "too broad"


def test_backend_ack_proposal_marks_refused_proposal_reviewed(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import hades_backend_db as hdb
    import hermes_cli.hades_backend_cmd as cmd

    with hdb.connect_closing() as conn:
        proposal = hdb.create_memory_proposal(
            conn,
            project_id="proj_1",
            workspace_binding_id="wb_1",
            action="create",
            intent="memory_write",
            summary="Remember backend contract",
            provenance={},
        )
        hdb.mark_memory_proposal_status(conn, proposal.id, "refused", "policy_denied")

    rc = cmd.hades_backend_command(SimpleNamespace(backend_action="ack-proposal", proposal_id=proposal.id))

    output = capsys.readouterr().out
    with hdb.connect_closing() as conn:
        reviewed = hdb.list_memory_proposals(conn, ids=[proposal.id])[0]

    assert rc == 0
    assert "acknowledged" in output
    assert reviewed.status == "acknowledged"
    assert reviewed.reason == "policy_denied"


def test_backend_ingest_test_uploads_redacted_failing_test_evidence(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "repo"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    output_file = workspace / "phpunit.log"
    output_file.write_text(
        "FAILED Tests\\\\Feature\\\\OrderTest > it shows order\n"
        "Error: Call to member function active() on null at app/Http/Controllers/OrderController.php:42\n"
        "OPENAI_API_KEY=sk-live-secretvalue12345\n",
        encoding="utf-8",
    )

    from hermes_cli import hades_backend_db as hdb
    import hermes_cli.hades_backend_cmd as cmd
    import hermes_cli.hades_backend_runtime as runtime

    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev-box",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"memory": True},
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
        def __init__(self):
            self.evidence = []
            self.closed = 0

        def create_bug_evidence(self, **payload):
            self.evidence.append(payload)
            return {"evidence": {"id": "ev_test_1"}}

        def close(self):
            self.closed += 1

    fake = FakeClient()
    monkeypatch.setattr(runtime, "client_from_config", lambda: fake)

    rc = cmd.hades_backend_command(
        SimpleNamespace(
            backend_action="ingest-test",
            file=str(output_file),
            bug_report_id="bug_1",
            source=None,
            json=True,
        )
    )

    result = json.loads(capsys.readouterr().out)
    payload = fake.evidence[0]

    assert rc == 0
    assert result["evidence_id"] == "ev_test_1"
    assert payload["project_id"] == "proj_1"
    assert payload["workspace_binding_id"] == "wb_1"
    assert payload["bug_report_id"] == "bug_1"
    assert payload["kind"] == "failing_test"
    assert payload["retention_class"] == "test_failure"
    assert payload["payload"]["schema"] == "hades.test_output.v1"
    assert payload["payload"]["framework"] == "phpunit"
    assert payload["payload"]["frames"] == [
        {"path": "app/Http/Controllers/OrderController.php", "line": 42}
    ]
    assert payload["redactions"] == 1
    assert "sk-live-secretvalue12345" not in json.dumps(payload)
    assert fake.closed == 1


def test_backend_ingest_log_uploads_redacted_runtime_log_evidence(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "repo"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    log_file = workspace / "laravel.log"
    log_file.write_text(
        "[2026-07-07] production.ERROR: SQLSTATE[42S22]: Column not found\n"
        "#0 app/Models/Order.php:88\n"
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz\n",
        encoding="utf-8",
    )

    from hermes_cli import hades_backend_db as hdb
    import hermes_cli.hades_backend_cmd as cmd
    import hermes_cli.hades_backend_runtime as runtime

    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev-box",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"memory": True},
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
        def __init__(self):
            self.evidence = []

        def create_bug_evidence(self, **payload):
            self.evidence.append(payload)
            return {"evidence": {"id": "ev_log_1"}}

    fake = FakeClient()
    monkeypatch.setattr(runtime, "client_from_config", lambda: fake)

    rc = cmd.hades_backend_command(
        SimpleNamespace(
            backend_action="ingest-log",
            file=str(log_file),
            bug_report_id=None,
            source="laravel.log",
            json=True,
        )
    )

    result = json.loads(capsys.readouterr().out)
    payload = fake.evidence[0]

    assert rc == 0
    assert result["evidence_id"] == "ev_log_1"
    assert payload["kind"] == "log_excerpt"
    assert payload["retention_class"] == "log_excerpt"
    assert payload["payload"]["schema"] == "hades.runtime_log_excerpt.v1"
    assert payload["payload"]["frames"] == [{"path": "app/Models/Order.php", "line": 88}]
    assert payload["redactions"] == 1
    assert "abcdefghijklmnopqrstuvwxyz" not in json.dumps(payload)


def test_backend_bug_intake_creates_report_and_evidence(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "repo"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    test_output = workspace / "phpunit.log"
    test_output.write_text(
        "FAILED Feature OrderTest\n"
        "Error: Call to member function active() on null at app/Http/Controllers/OrderController.php:42\n"
        "OPENAI_API_KEY=sk-live-secretvalue12345\n",
        encoding="utf-8",
    )
    runtime_log = workspace / "runtime.log"
    runtime_log.write_text("production.ERROR Null active() token=secretvalue12345\n", encoding="utf-8")

    from hermes_cli import hades_backend_db as hdb
    import hermes_cli.hades_backend_cmd as cmd
    import hermes_cli.hades_backend_runtime as runtime

    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev-box",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"memory": True},
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
        def __init__(self):
            self.reports = []
            self.evidence = []
            self.closed = 0

        def create_bug_report(self, **payload):
            self.reports.append(payload)
            return {"bug_report": {"id": "bug_1"}}

        def create_bug_evidence(self, **payload):
            self.evidence.append(payload)
            return {"evidence": {"id": f"ev_{len(self.evidence)}"}}

        def close(self):
            self.closed += 1

    fake = FakeClient()
    monkeypatch.setattr(runtime, "client_from_config", lambda: fake)

    rc = cmd.hades_backend_command(
        SimpleNamespace(
            actual="HTTP 500",
            backend_action="bug-intake",
            environment="staging",
            expected="HTTP 200",
            json=True,
            log=[str(runtime_log)],
            severity="high",
            steps="Open /orders/1",
            symptom="Opening order detail returns HTTP 500",
            test_output=[str(test_output)],
            title="Order detail 500",
        )
    )
    result = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert result["bug_report_id"] == "bug_1"
    assert result["evidence_ids"] == ["ev_1", "ev_2"]
    assert fake.closed == 1
    assert fake.reports[0]["title"] == "Order detail 500"
    assert fake.reports[0]["payload"]["schema"] == "hades.bug_intake.v1"
    assert fake.reports[0]["payload"]["steps"] == "Open /orders/1"
    assert [item["kind"] for item in fake.evidence] == ["failing_test", "log_excerpt"]
    assert all(item["bug_report_id"] == "bug_1" for item in fake.evidence)
    assert "sk-live-secretvalue12345" not in json.dumps(fake.evidence)
    assert "secretvalue12345" not in json.dumps(fake.evidence)


def test_backend_sync_records_last_error_when_backend_pull_fails(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    workspace = tmp_path / "repo"
    workspace.mkdir()

    import hermes_cli.hades_backend_cmd as cmd
    import hermes_cli.hades_backend_runtime as runtime
    from hermes_cli import hades_backend_db as hdb

    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev-box",
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

    class FakeClient:
        def memory_snapshot(self, **payload):
            return {"items": []}

        def pull_jobs(self, **payload):
            raise RuntimeError("backend token=super-secret-token is unavailable")

    monkeypatch.setattr(runtime, "client_from_config", lambda: FakeClient())

    rc = cmd.hades_backend_command(SimpleNamespace(backend_action="sync"))

    stderr = capsys.readouterr().err
    with hdb.connect_closing() as conn:
        last_error = hdb.get_sync_state(conn, "last_sync_error")

    assert rc == 1
    assert "super-secret-token" not in stderr
    assert last_error is not None
    assert last_error["workspace_binding_id"] == "wb_1"
    assert "super-secret-token" not in str(last_error)
