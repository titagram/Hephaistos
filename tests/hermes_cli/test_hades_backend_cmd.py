from __future__ import annotations

import json
from pathlib import Path
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


def test_backend_default_capabilities_include_project_wiki_refresh():
    import hermes_cli.hades_backend_cmd as cmd

    assert "populate_project_wiki" in cmd._detect_default_capabilities()
    assert "populate_project_wiki" in cmd.AUTO_JOB_CAPABILITIES


def test_backend_bootstrap_awareness_orchestrates_current_workspace(monkeypatch, tmp_path, capsys):
    _seed_current_backend_workspace(monkeypatch, tmp_path)

    import hermes_cli.hades_backend_cmd as cmd
    import hermes_cli.hades_backend_runtime as runtime
    import hermes_cli.hades_backend_sync as sync
    from hermes_cli.hades_backend_actions import BackendActionResult
    from hermes_cli.hades_backend_sync import SyncResult

    class FakeClient:
        def __init__(self):
            self.closed = False
            self.bootstrap_requests = []

        def bootstrap_project_awareness(self, **payload):
            self.bootstrap_requests.append(payload)
            return {"job": {"id": "job_wiki", "status": "queued"}}

        def project_awareness_status(self, **payload):
            return {"overall_status": "ready", "project_id": payload["project_id"]}

        def close(self):
            self.closed = True

    fake_client = FakeClient()
    sync_calls = []

    monkeypatch.setattr(runtime, "client_for_agent", lambda agent, timeout=15.0: fake_client)
    monkeypatch.setattr(sync, "_sync_baseline_artifacts", lambda *args, **kwargs: (2, 0, 1, 25))
    monkeypatch.setattr(
        sync,
        "run_backend_sync",
        lambda **kwargs: (sync_calls.append(kwargs) or SyncResult({"pulled": 0, "completed": 0}, 0)),
    )
    monkeypatch.setattr(
        cmd,
        "approve_backend_jobs",
        lambda **kwargs: BackendActionResult(
            ok=True,
            status="completed",
            summary="approved 3/3 job(s); failed 0",
            payload={"approved": 3, "failed": 0, "dry_run": False},
        ),
    )

    rc = cmd.hades_backend_command(
        SimpleNamespace(
            backend_action="bootstrap-awareness",
            yes=True,
            skip_wiki=False,
            record_quality_report=False,
            json=False,
        )
    )

    output = capsys.readouterr().out

    assert rc == 0
    assert "Hades backend bootstrap-awareness complete" in output
    assert "25 source-slice candidate(s)" in output
    assert "approved 3/3 job(s); failed 0" in output
    assert "ready" in output
    assert fake_client.bootstrap_requests == [
        {
            "project_id": "proj_1",
            "agent_id": "agent_1",
            "workspace_binding_id": "wb_1",
            "reason": "CLI bootstrap-awareness",
        }
    ]
    assert sync_calls == [
        {"quiet": True, "workspace_binding_ids": ["wb_1"]},
        {"quiet": True, "workspace_binding_ids": ["wb_1"]},
        {"quiet": True, "workspace_binding_ids": ["wb_1"]},
        {"quiet": True, "workspace_binding_ids": ["wb_1"]},
    ]
    assert fake_client.closed is True


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


def test_hades_backend_benchmark_reports_compressed_large_artifact():
    from hermes_cli.hades_backend_benchmark import run_hades_backend_benchmark

    report = run_hades_backend_benchmark(
        cases=[
            {"name": "medium_code_graph", "symbols": 600, "routes": 60, "edges": 900},
            {"name": "large_code_graph", "symbols": 2600, "routes": 260, "edges": 5200},
        ]
    )

    assert report["schema"] == "hades.backend_benchmark.v1"
    assert report["status"] == "passed"
    assert report["case_count"] == 2
    large = report["cases"][1]
    assert large["schema"] == "hades.code_graph.v1"
    assert large["raw_source_included"] is False
    assert large["upload_mode"] == "compressed"
    assert large["compressed_bytes"] < large["original_bytes"]
    assert large["compression_ratio"] < 0.75
    assert len(large["payload_sha256"]) == 64


def test_hades_backend_benchmark_reports_real_workspace_artifacts(tmp_path):
    from hermes_cli.hades_backend_benchmark import run_hades_backend_benchmark

    routes = tmp_path / "routes"
    controller_dir = tmp_path / "app" / "Http" / "Controllers"
    routes.mkdir()
    controller_dir.mkdir(parents=True)
    (routes / "api.php").write_text(
        "<?php\n"
        "use App\\Http\\Controllers\\OrderController;\n"
        "Route::get('/orders/{order}', [OrderController::class, 'show'])->name('orders.show');\n"
    )
    (controller_dir / "OrderController.php").write_text(
        "<?php\n"
        "namespace App\\Http\\Controllers;\n"
        "class OrderController { public function show(int $order): array { return ['id' => $order]; } }\n"
    )

    report = run_hades_backend_benchmark(cases=[], workspace=tmp_path)

    assert report["schema"] == "hades.backend_benchmark.v1"
    assert report["has_workspace_dataset"] is True
    assert report["case_count"] == 2
    assert [case["name"] for case in report["cases"]] == ["workspace_git_tree", "workspace_code_graph"]
    tree, graph = report["cases"]
    assert tree["source"] == "workspace"
    assert tree["schema"] == "hades.git_tree.v1"
    assert tree["file_count"] >= 2
    assert graph["source"] == "workspace"
    assert graph["schema"] == "hades.php_graph.v1"
    assert graph["route_count"] == 1
    assert graph["raw_source_included"] is False
    assert isinstance(graph["index_duration_ms"], int)
    assert len(graph["payload_sha256"]) == 64


def test_backend_benchmark_command_emits_json(capsys):
    import hermes_cli.hades_backend_cmd as cmd

    rc = cmd.hades_backend_command(
        SimpleNamespace(
            backend_action="benchmark",
            medium_symbols=400,
            large_symbols=2200,
            json=True,
        )
    )

    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["schema"] == "hades.backend_benchmark.v1"
    assert payload["status"] == "passed"
    assert [case["name"] for case in payload["cases"]] == ["medium_code_graph", "large_code_graph"]


def test_backend_schedule_quality_creates_and_updates_cron_job(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    import cron.jobs as cron_jobs
    import hermes_cli.hades_backend_cmd as cmd

    monkeypatch.setattr(cron_jobs, "CRON_DIR", tmp_path / "cron")
    monkeypatch.setattr(cron_jobs, "JOBS_FILE", tmp_path / "cron" / "jobs.json")
    monkeypatch.setattr(cron_jobs, "OUTPUT_DIR", tmp_path / "cron" / "output")

    fixture = tmp_path / "no_codebase.json"
    fixture.write_text('{"fixtures": [], "runs": []}', encoding="utf-8")

    rc = cmd.hades_backend_command(
        SimpleNamespace(
            backend_action="schedule-quality",
            schedule="0 7 * * *",
            name="Hades backend quality report",
            deliver="local",
            no_codebase_eval=str(fixture),
            json=True,
        )
    )
    created = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert created["status"] == "created"
    assert created["no_agent"] is True
    script_path = Path(created["script"])
    assert script_path.exists()
    script = script_path.read_text(encoding="utf-8")
    assert "backend_action=\"quality-report\"" in script
    assert str(fixture.resolve()) in script

    rc = cmd.hades_backend_command(
        SimpleNamespace(
            backend_action="schedule-quality",
            schedule="every 2h",
            name="Hades backend quality report",
            deliver="local",
            no_codebase_eval=str(fixture),
            json=True,
        )
    )
    updated = json.loads(capsys.readouterr().out)
    jobs = cron_jobs.list_jobs(include_disabled=True)

    assert rc == 0
    assert updated["status"] == "updated"
    assert updated["job_id"] == created["job_id"]
    assert len(jobs) == 1
    assert jobs[0]["no_agent"] is True
    assert jobs[0]["script"] == "hades_backend_quality_report.py"
    assert jobs[0]["schedule"]["kind"] == "interval"
    assert jobs[0]["schedule"]["minutes"] == 120


def test_backend_promote_diagnosis_command_calls_backend(monkeypatch, tmp_path, capsys):
    _seed_current_backend_workspace(monkeypatch, tmp_path)

    import hermes_cli.hades_backend_cmd as cmd
    import hermes_cli.hades_backend_runtime as runtime

    class FakeClient:
        def __init__(self):
            self.calls = []
            self.closed = False

        def promote_diagnosis_report(self, diagnosis_report_id, **payload):
            self.calls.append((diagnosis_report_id, payload))
            return {
                "diagnosis_report_id": diagnosis_report_id,
                "already_promoted": False,
                "resolved_bug_memory": {"id": "mem_bug_1", "kind": "resolved_bug"},
            }

        def close(self):
            self.closed = True

    fake = FakeClient()
    monkeypatch.setattr(runtime, "client_from_config", lambda: fake)

    rc = cmd.hades_backend_command(
        SimpleNamespace(
            backend_action="promote-diagnosis",
            diagnosis_report_id="diag_1",
            verification_status="test_passed",
            fix_commit="abc123",
            fix_pr_url="https://example.test/pr/1",
            affected_symbol=["OrderController@show"],
            regression_test=["OrderControllerTest::test_missing_customer"],
            notes="Regression passed with OPENAI_API_KEY=sk-live-secretvalue12345",
            json=True,
        )
    )
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["status"] == "promoted"
    assert payload["resolved_bug_memory_id"] == "mem_bug_1"
    assert fake.closed is True
    assert fake.calls == [
        (
            "diag_1",
            {
                "project_id": "proj_1",
                "workspace_binding_id": "wb_1",
                "verification_status": "test_passed",
                "fix_commit": "abc123",
                "fix_pr_url": "https://example.test/pr/1",
                "affected_symbols": ["OrderController@show"],
                "regression_tests": ["OrderControllerTest::test_missing_customer"],
                "payload": {"notes": "Regression passed with OPENAI_API_KEY=***"},
                "redactions": 1,
            },
        )
    ]


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


def test_backend_worker_loop_polls_until_idle_limit(monkeypatch, capsys):
    import hermes_cli.hades_backend_cmd as cmd
    import hermes_cli.hades_plugin_worker as worker

    summaries = iter(
        [
            {"listed": 1, "claimed": 1, "completed": 1, "failed": 0, "skipped": 0},
            {"listed": 0, "claimed": 0, "completed": 0, "failed": 0, "skipped": 0},
            {"listed": 0, "claimed": 0, "completed": 0, "failed": 0, "skipped": 0},
        ]
    )
    calls = []
    sleeps = []

    def fake_run_plugin_worker_once(**kwargs):
        calls.append(kwargs)
        return worker.PluginWorkerResult(next(summaries), 0)

    monkeypatch.setattr(worker, "run_plugin_worker_once", fake_run_plugin_worker_once)
    monkeypatch.setattr(cmd.time, "sleep", lambda seconds: sleeps.append(seconds))

    rc = cmd.hades_backend_command(
        SimpleNamespace(
            backend_action="worker",
            once=False,
            loop=True,
            interval=0.25,
            max_cycles=5,
            idle_exit_after=2,
            project_id="proj_1",
            local_workspace_id="lw_1",
            agent_key="local_agent",
            limit=1,
            json=True,
        )
    )

    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["mode"] == "loop"
    assert payload["cycles"] == 3
    assert payload["error_cycles"] == 0
    assert payload["completed"] == 1
    assert payload["idle_cycles"] == 2
    assert len(calls) == 3
    assert sleeps == [0.25]


def test_backend_worker_loop_stops_after_consecutive_error_limit(monkeypatch, capsys):
    import hermes_cli.hades_backend_cmd as cmd
    import hermes_cli.hades_plugin_worker as worker

    calls = []
    sleeps = []

    def fake_run_plugin_worker_once(**kwargs):
        calls.append(kwargs)
        return worker.PluginWorkerResult(
            {"listed": 1, "claimed": 1, "completed": 0, "failed": 1, "skipped": 0},
            1,
        )

    monkeypatch.setattr(worker, "run_plugin_worker_once", fake_run_plugin_worker_once)
    monkeypatch.setattr(cmd.time, "sleep", lambda seconds: sleeps.append(seconds))

    rc = cmd.hades_backend_command(
        SimpleNamespace(
            backend_action="worker",
            once=False,
            loop=True,
            interval=0.25,
            max_cycles=0,
            idle_exit_after=0,
            max_errors=2,
            project_id="proj_1",
            local_workspace_id="lw_1",
            agent_key="local_agent",
            limit=1,
            json=True,
        )
    )

    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert payload["mode"] == "loop"
    assert payload["cycles"] == 2
    assert payload["failed"] == 2
    assert payload["error_cycles"] == 2
    assert len(calls) == 2
    assert sleeps == [0.25]


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
    payload = json.loads(output)
    assert payload["error"]["code"] == "missing_local_workspace_id"
    assert "worker-setup" in payload["error"]["next_step"]
    assert output.startswith("{")


def test_backend_worker_setup_registers_device_workspace_and_persists_ids(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "repo"
    workspace.mkdir()

    import hermes_cli.hades_backend_cmd as cmd
    import hermes_cli.hades_plugin_tasks as tasks
    from hermes_cli import hades_backend_db as hdb
    from hermes_cli.config import load_config

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

    class FakeClient:
        def __init__(self):
            self.closed = False

        def register_device(self, **payload):
            assert payload["name"]
            assert payload["fingerprint_hash"].startswith("sha256:")
            return {"device_id": "dev_1", "status": "active"}

        def list_repositories(self, project_id):
            assert project_id == "proj_1"
            return {"repositories": [{"repository_id": "repo_1", "name": "repo", "slug": "repo"}]}

        def register_local_workspace(self, repository_id, **payload):
            assert repository_id == "repo_1"
            assert payload["device_id"] == "dev_1"
            assert payload["local_root_hash"].startswith("sha256:")
            assert payload["display_path"]
            return {"local_workspace_id": "lw_1", "status": "linked"}

        def close(self):
            self.closed = True

    fake = FakeClient()
    monkeypatch.setattr(tasks.runtime, "plugin_work_items_client_from_config", lambda: fake)

    rc = cmd.hades_backend_command(
        SimpleNamespace(
            backend_action="worker-setup",
            workspace=str(workspace),
            repository_id=None,
            json=True,
        )
    )

    payload = json.loads(capsys.readouterr().out)
    config = load_config()

    assert rc == 0
    assert payload["local_workspace_id"] == "lw_1"
    assert payload["repository_id"] == "repo_1"
    assert config["backend"]["plugin_device_id"] == "dev_1"
    assert config["backend"]["plugin_repository_id"] == "repo_1"
    assert config["backend"]["plugin_local_workspace_id"] == "lw_1"
    assert fake.closed is True


def test_backend_tasks_list_outputs_available_local_agent_work(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    import hermes_cli.hades_backend_cmd as cmd
    import hermes_cli.hades_plugin_tasks as tasks
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

    class FakeClient:
        def list_agent_work_items(self, **payload):
            assert payload["project_id"] == "proj_1"
            assert payload["agent_key"] == "local_agent"
            return {
                "items": [
                    {
                        "id": "awi_1",
                        "project_id": "proj_1",
                        "task_id": "task_1",
                        "status": "queued",
                        "priority": "high",
                        "title": "Fix failing checkout",
                        "payload": {
                            "schema": "hades.kanban_task_work.v1",
                            "task_id": "task_1",
                            "project_id": "proj_1",
                            "repository_id": "repo_1",
                            "workspace_binding_id": "wb_1",
                            "title": "Fix failing checkout",
                            "description": "Checkout fails after customer selection.",
                            "acceptance_criteria": ["Explain root cause"],
                            "priority": "high",
                            "risk": "medium",
                            "normalized_problem": "Diagnose checkout failure after customer selection.",
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
                        },
                    }
                ]
            }

        def close(self):
            pass

    monkeypatch.setattr(tasks.runtime, "plugin_work_items_client_from_config", lambda: FakeClient())

    rc = cmd.hades_backend_command(
        SimpleNamespace(
            backend_action="tasks",
            tasks_action="list",
            project_id=None,
            repository_id=None,
            agent_key="local_agent",
            status="queued",
            limit=20,
            json=True,
        )
    )

    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["count"] == 1
    assert payload["items"][0]["work_item_id"] == "awi_1"
    assert payload["items"][0]["task_id"] == "task_1"
    assert payload["items"][0]["title"] == "Fix failing checkout"
    assert payload["items"][0]["contract"]["valid"] is True

    with hdb.connect_closing() as conn:
        cached = hdb.get_plugin_work_item(conn, "awi_1")
    assert cached is not None
    assert cached.kind == "hades.kanban_task_work.v1"


def test_backend_tasks_list_preserves_plugin_error_code(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    from hermes_cli.hades_backend_client import HadesBackendError
    import hermes_cli.hades_backend_cmd as cmd
    import hermes_cli.hades_plugin_tasks as tasks
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

    class FakeClient:
        def list_agent_work_items(self, **payload):
            raise HadesBackendError(
                "403: workspace mismatch",
                status_code=403,
                code="workspace_mismatch",
                next_step="Run `hades backend worker-setup` in this checkout.",
            )

        def close(self):
            pass

    monkeypatch.setattr(tasks.runtime, "plugin_work_items_client_from_config", lambda: FakeClient())

    rc = cmd.hades_backend_command(
        SimpleNamespace(
            backend_action="tasks",
            tasks_action="list",
            project_id=None,
            repository_id=None,
            agent_key="local_agent",
            status="queued",
            limit=20,
            json=True,
        )
    )

    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert payload["error"]["code"] == "workspace_mismatch"
    assert payload["error"]["next_step"] == "Run `hades backend worker-setup` in this checkout."


def test_backend_tasks_list_returns_json_error_when_plugin_token_missing(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    import hermes_cli.hades_backend_cmd as cmd
    import hermes_cli.hades_plugin_tasks as tasks
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

    def raise_missing_token():
        raise RuntimeError(
            "Hades plugin API token is missing; set backend.plugin_token_env_key "
            "or HADES_BACKEND_PLUGIN_TOKEN. Do not use the Hades agent token for plugin work."
        )

    monkeypatch.setattr(tasks.runtime, "plugin_work_items_client_from_config", raise_missing_token)

    rc = cmd.hades_backend_command(
        SimpleNamespace(
            backend_action="tasks",
            tasks_action="list",
            project_id=None,
            repository_id=None,
            agent_key="local_agent",
            status="queued",
            limit=20,
            json=True,
        )
    )

    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert payload["error"]["code"] == "list_work_items_failed"
    assert "plugin API token is missing" in payload["error"]["message"]
    assert "plugin token" in payload["error"]["next_step"]


def test_backend_worker_setup_returns_json_error_when_plugin_token_missing(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "repo"
    workspace.mkdir()

    import hermes_cli.hades_backend_cmd as cmd
    import hermes_cli.hades_plugin_tasks as tasks
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

    def raise_missing_token():
        raise RuntimeError(
            "Hades plugin API token is missing; set backend.plugin_token_env_key "
            "or HADES_BACKEND_PLUGIN_TOKEN. Do not use the Hades agent token for plugin work."
        )

    monkeypatch.setattr(tasks.runtime, "plugin_work_items_client_from_config", raise_missing_token)

    rc = cmd.hades_backend_command(
        SimpleNamespace(
            backend_action="worker-setup",
            workspace=str(workspace),
            repository_id=None,
            json=True,
        )
    )

    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert payload["error"]["code"] == "worker_setup_failed"
    assert "plugin API token is missing" in payload["error"]["message"]
    assert "plugin token" in payload["error"]["next_step"]


def test_backend_tasks_work_reuses_plugin_worker(monkeypatch, capsys):
    import hermes_cli.hades_backend_cmd as cmd
    import hermes_cli.hades_plugin_worker as worker

    calls = []

    def fake_run_plugin_worker_once(**kwargs):
        calls.append(kwargs)
        return worker.PluginWorkerResult({"listed": 1, "claimed": 1, "completed": 1, "failed": 0, "skipped": 0}, 0)

    monkeypatch.setattr(worker, "run_plugin_worker_once", fake_run_plugin_worker_once)

    rc = cmd.hades_backend_command(
        SimpleNamespace(
            backend_action="tasks",
            tasks_action="work",
            once=True,
            project_id="proj_1",
            local_workspace_id="lw_1",
            agent_key="local_agent",
            limit=1,
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
            "limit": 1,
            "quiet": True,
        }
    ]


def test_backend_tasks_status_summarizes_cached_work_items(monkeypatch, tmp_path, capsys):
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
        hdb.upsert_plugin_work_item(
            conn,
            work_item_id="awi_1",
            project_id="proj_1",
            agent_key="local_agent",
            kind="hades.kanban_task_work.v1",
            status="queued",
            payload={
                "schema": "hades.kanban_task_work.v1",
                "memory_required": True,
                "memory_search_status": {"status": "empty", "refs": []},
            },
        )

    rc = cmd.hades_backend_command(
        SimpleNamespace(
            backend_action="tasks",
            tasks_action="status",
            project_id=None,
            json=True,
        )
    )
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["project_id"] == "proj_1"
    assert payload["total"] == 1
    assert payload["by_status"] == {"queued": 1}
    assert payload["quality"]["missing_shared_memory_context_count"] == 0
    assert payload["next_step"] == "Run `hades backend tasks work --once` to process queued work."


def test_backend_tasks_explain_shows_cached_quality(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    import hermes_cli.hades_backend_cmd as cmd
    from hermes_cli import hades_backend_db as hdb

    with hdb.connect_closing() as conn:
        hdb.upsert_plugin_work_item(
            conn,
            work_item_id="awi_missing",
            project_id="proj_1",
            repository_id="repo_1",
            local_workspace_id="lw_1",
            agent_key="local_agent",
            kind="hades.kanban_task_work.v1",
            status="completed",
            payload={
                "schema": "hades.kanban_task_work.v1",
                "memory_required": True,
                "title": "Diagnose checkout bug",
            },
            result={"final_response": "Done."},
        )

    rc = cmd.hades_backend_command(
        SimpleNamespace(
            backend_action="tasks",
            tasks_action="explain",
            work_item_id="awi_missing",
            json=True,
        )
    )
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["item"]["work_item_id"] == "awi_missing"
    assert payload["item"]["repository_id"] == "repo_1"
    assert payload["item"]["payload"]["title"] == "Diagnose checkout bug"
    assert payload["quality"]["missing_shared_memory_context_count"] == 1
    assert payload["quality"]["completed_missing_shared_memory_context_count"] == 1


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
            assert payload["plugin_device"]["fingerprint_hash"].startswith("sha256:")
            return {
                "agent_id": payload["agent_id"],
                "agent_token": "derived-token",
                "plugin_credentials": {
                    "project_id": "backend_proj",
                    "token": "derived-plugin-token",
                    "device_id": "dev_1",
                    "device_secret": "derived-device-secret",
                },
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
    assert config["backend"]["plugin_device_id"] == "dev_1"
    assert config["backend"]["plugin_token_env_key"]
    assert config["backend"]["plugin_device_secret_env_key"]
    assert "derived-token" in env_text
    assert "derived-plugin-token" in env_text
    assert "derived-device-secret" in env_text
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
        "Run `hades backend worker-setup` in this checkout before claiming backend task work.",
    ]


def test_backend_status_waiting_jobs_are_actionable_but_not_degraded(monkeypatch, tmp_path, capsys):
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

    rc = cmd.hades_backend_command(SimpleNamespace(backend_action="status", json=True))

    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["job_counts"] == {"waiting_confirmation": 1}
    assert payload["degraded"] is False
    assert "Review 1 backend job(s) waiting for confirmation." in payload["actions"]


def test_backend_status_text_prints_identity_next_step(monkeypatch, tmp_path, capsys):
    _seed_current_backend_workspace(monkeypatch, tmp_path)

    import hermes_cli.hades_backend_cmd as cmd

    rc = cmd.hades_backend_command(SimpleNamespace(backend_action="status", json=False))
    output = capsys.readouterr().out

    assert rc == 0
    assert "Next identity step:" in output
    assert "Run `hades backend sync`" in output
    assert "before source-free diagnosis" in output


def test_backend_status_text_prints_task_work_summary(monkeypatch, tmp_path, capsys):
    _seed_current_backend_workspace(monkeypatch, tmp_path)

    from hermes_cli import hades_backend_db as hdb
    import hermes_cli.hades_backend_cmd as cmd

    with hdb.connect_closing() as conn:
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
                "memory_search_status": {"status": "ready", "refs": ["memory:bug:1"]},
            },
        )
        hdb.upsert_plugin_work_item(
            conn,
            work_item_id="awi_failed",
            project_id="proj_1",
            agent_key="local_agent",
            kind="hades.kanban_task_work.v1",
            status="failed",
            payload={"schema": "hades.kanban_task_work.v1", "memory_required": True},
        )

    rc = cmd.hades_backend_command(SimpleNamespace(backend_action="status", json=False))
    output = capsys.readouterr().out

    assert rc == 0
    assert "Task work: 2 cached (queued 1, failed 1, memory 1/2)" in output
    assert "Inspect failed backend task work with `hades backend tasks status`." in output
    assert "Repair backend task work missing shared memory context before relying on agent output." in output


def test_backend_status_json_surfaces_causal_pack_awareness_action(monkeypatch, tmp_path, capsys):
    _seed_current_backend_workspace(monkeypatch, tmp_path)

    from hermes_cli import hades_backend_db as hdb
    import hermes_cli.hades_backend_cmd as cmd

    with hdb.connect_closing() as conn:
        hdb.record_sync_state(
            conn,
            "last_sync_summary",
            {
                "project_id": "proj_1",
                "workspace_binding_id": "wb_1",
                "artifacts_uploaded": 1,
                "source_slices_uploaded": 1,
                "bug_evidence_items": 1,
                "causal_packs_valid": 1,
                "causal_packs_missing_for_open_bugs": 1,
            },
        )

    rc = cmd.hades_backend_command(SimpleNamespace(backend_action="status", json=True))
    payload = json.loads(capsys.readouterr().out)
    awareness = payload["bindings"][0]["awareness"]

    assert rc == 0
    assert awareness["coverage"]["causal_packs"] == {
        "status": "partial",
        "valid": 1,
        "invalid": 0,
        "missing_for_open_bugs": 1,
    }
    assert "create_causal_pack" in awareness["quality"]["actions"]


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


def test_backend_approve_job_uses_binding_scoped_agent(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    workspace_one = tmp_path / "repo-one"
    workspace_two = tmp_path / "repo-two"
    workspace_one.mkdir()
    workspace_two.mkdir()
    (workspace_one / "README.md").write_text("one\n", encoding="utf-8")

    from hermes_cli import hades_backend_db as hdb
    import hermes_cli.hades_backend_cmd as cmd
    import hermes_cli.hades_backend_runtime as runtime

    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_one",
            project_id="project_one",
            base_url="https://backend.example",
            label="dev-box",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_ONE",
            capabilities={"jobs": True},
        )
        hdb.save_agent(
            conn,
            agent_id="agent_two",
            project_id="project_two",
            base_url="https://backend.example",
            label="dev-box",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TWO",
            capabilities={"jobs": True},
        )
        hdb.upsert_workspace_binding(
            conn,
            project_id="project_one",
            agent_id="agent_one",
            local_project_id="p_local_one",
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
            agent_id="agent_two",
            local_project_id="p_local_two",
            workspace_fingerprint="wf_two",
            display_path="~/repo-two",
            repo_root=str(workspace_two),
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id="wb_two",
        )
        hdb.upsert_job(
            conn,
            job_id="job_one",
            project_id="project_one",
            workspace_binding_id="wb_one",
            capability="read_files",
            payload={"paths": ["README.md"]},
            status="waiting_confirmation",
        )
        conn.execute("UPDATE backend_agents SET updated_at = 10 WHERE agent_id = 'agent_one'")
        conn.execute("UPDATE backend_agents SET updated_at = 20 WHERE agent_id = 'agent_two'")
        conn.commit()

    class FakeClient:
        def __init__(self, expected_agent_id):
            self.expected_agent_id = expected_agent_id
            self.statuses = []
            self.results = []

        def update_job_status(self, job_id, **payload):
            assert payload["agent_id"] == self.expected_agent_id
            self.statuses.append((job_id, payload["status"], payload))
            return {}

        def submit_job_result(self, job_id, **payload):
            assert payload["agent_id"] == self.expected_agent_id
            self.results.append((job_id, payload))
            return {}

    default_client = FakeClient("agent_two")
    binding_client = FakeClient("agent_one")
    monkeypatch.setattr(runtime, "client_from_config", lambda: default_client)
    monkeypatch.setattr(runtime, "client_for_agent", lambda agent: binding_client)

    rc = cmd.hades_backend_command(SimpleNamespace(backend_action="approve-job", job_id="job_one"))

    output = capsys.readouterr().out
    with hdb.connect_closing() as conn:
        job = hdb.get_job(conn, "job_one")

    assert rc == 0
    assert "completed" in output
    assert job is not None
    assert job.status == "completed"
    assert default_client.statuses == []
    assert binding_client.statuses[0][2]["project_id"] == "project_one"
    assert binding_client.results[0][1]["agent_id"] == "agent_one"


def test_backend_approve_jobs_batches_current_project_waiting_jobs(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    workspace_one = tmp_path / "repo-one"
    workspace_two = tmp_path / "repo-two"
    workspace_one.mkdir()
    workspace_two.mkdir()
    (workspace_two / "README.md").write_text("two\n", encoding="utf-8")

    from hermes_cli import hades_backend_db as hdb
    import hermes_cli.hades_backend_cmd as cmd
    import hermes_cli.hades_backend_runtime as runtime

    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_one",
            project_id="project_one",
            base_url="https://backend.example",
            label="dev-box",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_ONE",
            capabilities={"jobs": True},
        )
        hdb.save_agent(
            conn,
            agent_id="agent_two",
            project_id="project_two",
            base_url="https://backend.example",
            label="dev-box",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TWO",
            capabilities={"jobs": True},
        )
        hdb.upsert_workspace_binding(
            conn,
            project_id="project_one",
            agent_id="agent_one",
            local_project_id="p_local_one",
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
            agent_id="agent_two",
            local_project_id="p_local_two",
            workspace_fingerprint="wf_two",
            display_path="~/repo-two",
            repo_root=str(workspace_two),
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id="wb_two",
        )
        hdb.upsert_job(
            conn,
            job_id="job_other_project",
            project_id="project_one",
            workspace_binding_id="wb_one",
            capability="read_files",
            payload={"paths": ["README.md"]},
            status="waiting_confirmation",
        )
        hdb.upsert_job(
            conn,
            job_id="job_current_project",
            project_id="project_two",
            workspace_binding_id="wb_two",
            capability="read_files",
            payload={"paths": ["README.md"]},
            status="waiting_confirmation",
        )
        conn.execute("UPDATE backend_agents SET updated_at = 10 WHERE agent_id = 'agent_one'")
        conn.execute("UPDATE backend_agents SET updated_at = 20 WHERE agent_id = 'agent_two'")
        conn.commit()

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

    rc = cmd.hades_backend_command(
        SimpleNamespace(
            backend_action="approve-jobs",
            capability=["read_files"],
            project_id=None,
            workspace_binding_id=None,
            all_projects=False,
            limit=0,
            dry_run=False,
            json=False,
        )
    )

    output = capsys.readouterr().out
    with hdb.connect_closing() as conn:
        other = hdb.get_job(conn, "job_other_project")
        current = hdb.get_job(conn, "job_current_project")

    assert rc == 0
    assert "approved 1/1" in output
    assert other is not None
    assert other.status == "waiting_confirmation"
    assert current is not None
    assert current.status == "completed"
    assert fake.results[0][0] == "job_current_project"
    assert fake.results[0][1]["agent_id"] == "agent_two"


def test_backend_approve_job_expires_stale_remote_job(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "README.md").write_text("hello\n", encoding="utf-8")

    from hermes_cli import hades_backend_db as hdb
    import hermes_cli.hades_backend_cmd as cmd
    from hermes_cli.hades_backend_client import HadesBackendError
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
        def update_job_status(self, job_id, **payload):
            raise HadesBackendError("404: job not found", status_code=404, code="job_not_found")

    monkeypatch.setattr(runtime, "client_from_config", lambda: FakeClient())

    rc = cmd.hades_backend_command(SimpleNamespace(backend_action="approve-job", job_id="job_wait"))

    output = capsys.readouterr()
    with hdb.connect_closing() as conn:
        job = hdb.get_job(conn, "job_wait")

    assert rc == 1
    assert "expired" in output.out
    assert "Remote Hades job no longer exists" in output.err
    assert job is not None
    assert job.status == "expired"
    assert job.result == {
        "status": "expired",
        "summary": "Remote Hades job no longer exists; local cached job expired.",
    }


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
    assert len(payload["payload"]["excerpt_sha256"]) == 64
    assert payload["payload"]["frame_refs"] == [
        {
            "type": "source_frame",
            "path": "app/Models/Order.php",
            "line": 88,
            "graph_query": "app/Models/Order.php",
            "source_slice_hint": {"path": "app/Models/Order.php", "line": 88},
        }
    ]
    assert payload["payload"]["log_refs"] == [
        {"type": "runtime_log_frame", "path": "app/Models/Order.php", "line": 88}
    ]
    assert payload["redactions"] == 1
    assert "abcdefghijklmnopqrstuvwxyz" not in json.dumps(payload)


def test_backend_ingest_deploy_uploads_mismatch_evidence(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "repo"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    from hermes_cli import hades_backend_db as hdb
    import hermes_cli.hades_backend_cmd as cmd
    import hermes_cli.hades_backend_runtime as runtime

    workspace_head = "a" * 40
    deployed = "b" * 40

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
            head_commit=workspace_head,
            backend_workspace_binding_id="wb_1",
        )

    class FakeClient:
        def __init__(self):
            self.evidence = []
            self.closed = 0

        def create_bug_evidence(self, **payload):
            self.evidence.append(payload)
            return {"evidence": {"id": "ev_deploy_1"}}

        def close(self):
            self.closed += 1

    fake = FakeClient()
    monkeypatch.setattr(runtime, "client_from_config", lambda: fake)

    rc = cmd.hades_backend_command(
        SimpleNamespace(
            backend_action="ingest-deploy",
            bug_report_id="bug_1",
            deploy_commit=deployed,
            environment="production",
            json=True,
            source=None,
            workspace_head=None,
        )
    )

    result = json.loads(capsys.readouterr().out)
    payload = fake.evidence[0]

    assert rc == 0
    assert result["evidence_id"] == "ev_deploy_1"
    assert result["mismatch"] is True
    assert payload["kind"] == "deploy_version"
    assert payload["retention_class"] == "runtime_evidence"
    assert payload["summary"].startswith("Deploy commit mismatch")
    assert payload["payload"] == {
        "schema": "hades.deploy_version.v1",
        "source": "deploy",
        "environment": "production",
        "deploy_commit": deployed,
        "deploy_commit_short": "b" * 12,
        "workspace_head_commit": workspace_head,
        "workspace_head_commit_short": "a" * 12,
        "mismatch": True,
    }
    assert fake.closed == 1


def test_backend_ingest_http_uploads_redacted_request_response_evidence(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "repo"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    request_file = workspace / "request.txt"
    request_file.write_text("Authorization: Bearer abcdefghijklmnopqrstuvwxyz\n", encoding="utf-8")
    response_file = workspace / "response.txt"
    response_file.write_text("HTTP/1.1 500\napi_key=secretvalue12345\n", encoding="utf-8")

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
            return {"evidence": {"id": f"ev_{len(self.evidence)}"}}

        def close(self):
            self.closed += 1

    fake = FakeClient()
    monkeypatch.setattr(runtime, "client_from_config", lambda: fake)

    rc = cmd.hades_backend_command(
        SimpleNamespace(
            backend_action="ingest-http",
            bug_report_id="bug_1",
            environment="production",
            json=True,
            method="post",
            request_file=str(request_file),
            response_file=str(response_file),
            source=None,
            status=500,
            url="https://app.example/orders/1?token=supersecret12345",
        )
    )

    result = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert result["evidence_ids"] == ["ev_1", "ev_2"]
    assert result["kinds"] == ["http_request", "http_response"]
    assert [item["kind"] for item in fake.evidence] == ["http_request", "http_response"]
    assert fake.evidence[0]["retention_class"] == "http_trace"
    assert fake.evidence[0]["payload"]["schema"] == "hades.http_request.v1"
    assert fake.evidence[0]["payload"]["method"] == "POST"
    assert fake.evidence[0]["payload"]["url"] == "https://app.example/orders/1?token=***"
    assert fake.evidence[0]["redactions"] == 2
    assert fake.evidence[1]["payload"]["schema"] == "hades.http_response.v1"
    assert fake.evidence[1]["payload"]["status"] == 500
    assert fake.evidence[1]["redactions"] == 2
    assert "supersecret12345" not in json.dumps(fake.evidence)
    assert "abcdefghijklmnopqrstuvwxyz" not in json.dumps(fake.evidence)
    assert "secretvalue12345" not in json.dumps(fake.evidence)
    assert fake.closed == 1


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
    response_file = workspace / "response.txt"
    response_file.write_text("HTTP/1.1 500\n", encoding="utf-8")

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
            head_commit="a" * 40,
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
            deploy_commit="b" * 40,
            deploy_source=None,
            environment="staging",
            expected="HTTP 200",
            json=True,
            log=[str(runtime_log)],
            request_file=None,
            request_method="GET",
            request_url="https://app.example/orders/1",
            response_file=str(response_file),
            response_status=500,
            severity="high",
            steps="Open /orders/1",
            symptom="Opening order detail returns HTTP 500",
            test_output=[str(test_output)],
            title="Order detail 500",
            workspace_head=None,
        )
    )
    result = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert result["bug_report_id"] == "bug_1"
    assert result["evidence_ids"] == ["ev_1", "ev_2", "ev_3", "ev_4", "ev_5"]
    assert fake.closed == 1
    assert fake.reports[0]["title"] == "Order detail 500"
    assert fake.reports[0]["payload"]["schema"] == "hades.bug_intake.v1"
    assert fake.reports[0]["payload"]["steps"] == "Open /orders/1"
    assert [item["kind"] for item in fake.evidence] == [
        "failing_test",
        "log_excerpt",
        "deploy_version",
        "http_request",
        "http_response",
    ]
    assert all(item["bug_report_id"] == "bug_1" for item in fake.evidence)
    assert fake.evidence[2]["payload"]["mismatch"] is True
    assert fake.evidence[2]["payload"]["environment"] == "staging"
    assert fake.evidence[4]["payload"]["status"] == 500
    assert "sk-live-secretvalue12345" not in json.dumps(fake.evidence)
    assert "secretvalue12345" not in json.dumps(fake.evidence)


def test_backend_causal_pack_create_and_replay(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    workspace = tmp_path / "repo"
    workspace.mkdir()
    payload_path = tmp_path / "causal-pack.json"
    payload_path.write_text(
        json.dumps(
            {
                "bug_id": "bug_booking_overlap",
                "root_cause_id": "booking-overlap-validation-gap",
                "bug_class": "validation",
                "failure_classification": "confirmed",
                "affected_refs": ["symbol:BookingController@store"],
                "freshness": {"status": "current", "head_commit": "a" * 40},
                "awareness": {"diagnosable_without_source": True},
                "evidence_refs": [{"type": "bug_evidence", "id": "ev_1"}],
                "graph_refs": [{"type": "artifact", "id": "artifact_1"}],
                "source_slice_refs": [{"type": "source_slice", "id": "slice_1"}],
            }
        ),
        encoding="utf-8",
    )

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
            capabilities={"causal_packs": True},
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
            head_commit="a" * 40,
            backend_workspace_binding_id="wb_1",
        )

    class FakeClient:
        def __init__(self):
            self.created = []
            self.replayed = []
            self.closed = 0

        def create_causal_pack(self, **payload):
            self.created.append(payload)
            return {
                "causal_pack": {
                    "id": "pack_1",
                    "status": "valid",
                    "root_cause_id": payload["root_cause_id"],
                }
            }

        def replay_causal_pack(self, causal_pack_id, **payload):
            self.replayed.append((causal_pack_id, payload))
            return {"replay": {"replayable": True, "missing_refs": []}}

        def close(self):
            self.closed += 1

    fake = FakeClient()
    monkeypatch.setattr(runtime, "client_from_config", lambda: fake)
    monkeypatch.chdir(workspace)

    create_rc = cmd.hades_backend_command(
        SimpleNamespace(
            backend_action="causal-pack",
            causal_pack_action="create",
            from_file=str(payload_path),
            from_diagnosis=None,
            json=True,
        )
    )
    create_result = json.loads(capsys.readouterr().out)

    replay_rc = cmd.hades_backend_command(
        SimpleNamespace(
            backend_action="causal-pack",
            causal_pack_action="replay",
            causal_pack_id="pack_1",
            json=True,
        )
    )
    replay_result = json.loads(capsys.readouterr().out)

    assert create_rc == 0
    assert replay_rc == 0
    assert create_result["schema"] == "hades.causal_pack_cli_result.v1"
    assert create_result["causal_pack"]["id"] == "pack_1"
    assert replay_result["replay"]["replayable"] is True
    assert fake.created[0]["project_id"] == "proj_1"
    assert fake.created[0]["workspace_binding_id"] == "wb_1"
    assert fake.created[0]["root_cause_id"] == "booking-overlap-validation-gap"
    assert fake.created[0]["source_slice_refs"] == [{"type": "source_slice", "id": "slice_1"}]
    assert fake.replayed == [("pack_1", {"project_id": "proj_1", "workspace_binding_id": "wb_1"})]
    assert fake.closed == 2


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
