from __future__ import annotations


def test_doctor_backend_check_reports_configured_agent(monkeypatch, tmp_path, capsys):
    from hermes_cli import hades_backend_db as db
    import hermes_cli.doctor as doctor

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HADES_BACKEND_AGENT_TOKEN_TEST", "derived-token")
    class FakeClient:
        def health(self):
            return {"status": "ok"}

        def capabilities(self):
            return {"capabilities": {"memory": True, "jobs": True}}

    monkeypatch.setattr(doctor, "_hades_backend_client_from_config", lambda: FakeClient())

    with db.connect_closing() as conn:
        db.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"memory": True, "jobs": True},
        )
        db.upsert_job(
            conn,
            job_id="job_1",
            project_id="proj_1",
            workspace_binding_id="wb_1",
            capability="read_files",
            payload={},
            status="waiting_confirmation",
        )
        db.create_memory_proposal(
            conn,
            project_id="proj_1",
            workspace_binding_id="wb_1",
            action="create",
            intent="memory_write",
            summary="Remember backend contract",
            provenance={},
        )
        db.record_sync_state(conn, "last_sync_summary", {"completed": 1, "waiting": 1})

    issues: list[str] = []
    doctor._check_hades_backend(issues)
    output = capsys.readouterr().out

    assert "Hades backend configured" in output
    assert "proj_1" in output
    assert "Hades backend health reachable" in output
    assert "Hades backend capabilities" in output
    assert "Backend jobs" in output
    assert "Memory proposals" in output
    assert "Last backend sync" in output
    assert issues == []


def test_doctor_backend_check_warns_when_missing(capsys):
    import hermes_cli.doctor as doctor

    issues: list[str] = []
    doctor._check_hades_backend(issues)
    output = capsys.readouterr().out

    assert "Hades backend not configured" in output
    assert issues


def test_doctor_cleanup_orphaned_cache_reports_removed(monkeypatch, tmp_path, capsys):
    from types import SimpleNamespace

    from hermes_cli import hades_backend_db as db
    import hermes_cli.doctor as doctor

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    with db.connect_closing() as conn:
        db.upsert_workspace_binding(
            conn,
            project_id="proj_1",
            agent_id="agent_1",
            local_project_id="p_1",
            workspace_fingerprint="wf_1",
            display_path="~/repo",
            repo_root=str(tmp_path / "repo"),
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id="wb_orphan",
        )
        db.mark_binding_unlinked(conn, "wf_1")
        db.replace_memory_cache(
            conn,
            project_id="proj_1",
            workspace_binding_id="wb_orphan",
            version="v1",
            items=[{"summary": "stale"}],
        )

    doctor.run_doctor(
        SimpleNamespace(
            fix=False,
            ack=None,
            doctor_action="cleanup",
            orphaned_cache=True,
            all=True,
            yes=True,
        )
    )

    output = capsys.readouterr().out
    with db.connect_closing() as conn:
        cache = db.get_memory_cache(conn, "wb_orphan")

    assert "Removed 1 orphaned Hades memory cache" in output
    assert cache is None


def test_doctor_cleanup_stale_jobs_dry_run_keeps_rows(monkeypatch, tmp_path, capsys):
    from types import SimpleNamespace

    from hermes_cli import hades_backend_db as db
    import hermes_cli.doctor as doctor

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    now = 2_000_000
    old = now - 31 * 86400
    with db.connect_closing() as conn:
        db.upsert_job(
            conn,
            job_id="job_done_old",
            project_id="proj_1",
            workspace_binding_id="wb_1",
            capability="read_files",
            payload={},
            status="completed",
        )
        conn.execute("UPDATE backend_jobs SET updated_at = ? WHERE job_id = ?", (old, "job_done_old"))
        conn.commit()

    monkeypatch.setattr(db, "_now", lambda: now)
    doctor.run_doctor(
        SimpleNamespace(
            fix=False,
            ack=None,
            doctor_action="cleanup",
            orphaned_cache=False,
            stale_jobs=True,
            stale_proposals=False,
            stale_inbox=False,
            retention_days=30,
            all=False,
            yes=False,
        )
    )

    output = capsys.readouterr().out
    with db.connect_closing() as conn:
        job = db.get_job(conn, "job_done_old")

    assert "Would remove 1 stale terminal Hades backend job" in output
    assert "dry run only" in output
    assert job is not None


def test_doctor_backend_report_is_explicit_and_structured(monkeypatch, tmp_path, capsys, caplog):
    import logging

    from hermes_cli import hades_backend_db as db
    import hermes_cli.doctor as doctor

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    class FakeClient:
        def __init__(self):
            self.reports = []

        def submit_doctor_report(self, **payload):
            self.reports.append(payload)
            return {"report": {"id": "report_1"}}

    fake = FakeClient()
    monkeypatch.setattr(doctor, "_hades_backend_client_from_config", lambda: fake)

    with db.connect_closing() as conn:
        db.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"jobs": True, "persephone": True},
        )
        db.upsert_workspace_binding(
            conn,
            project_id="proj_1",
            agent_id="agent_1",
            local_project_id="p_1",
            workspace_fingerprint="wf_1",
            display_path="~/repo",
            repo_root=str(tmp_path / "repo"),
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id="wb_1",
        )
        db.upsert_job(
            conn,
            job_id="job_1",
            project_id="proj_1",
            workspace_binding_id="wb_1",
            capability="read_files",
            payload={},
            status="waiting_confirmation",
        )
        db.save_inbox_event(
            conn,
            event_id="evt_1",
            project_id="proj_1",
            event_type="proposal.reviewed",
            payload={"message": "done"},
        )

    with caplog.at_level(logging.INFO, logger="hermes_cli.hades_backend"):
        doctor._submit_hades_doctor_report(["manual issue"])

    output = capsys.readouterr().out
    assert "Hades backend doctor report submitted" in output
    assert fake.reports[0]["project_id"] == "proj_1"
    assert fake.reports[0]["workspace_binding_id"] == "wb_1"
    assert fake.reports[0]["status"] == "warning"
    assert fake.reports[0]["payload"]["job_counts"] == {"waiting_confirmation": 1}
    assert fake.reports[0]["payload"]["inbox_counts"] == {"total": 1, "unread": 1}
    records = [
        record
        for record in caplog.records
        if getattr(record, "hades_event", None) == "doctor_report.submitted"
    ]
    assert records
    assert records[0].hades_report_id == "report_1"
    assert records[0].hades_issue_count == 1
