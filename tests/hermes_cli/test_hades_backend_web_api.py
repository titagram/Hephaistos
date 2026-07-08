from __future__ import annotations

from fastapi.testclient import TestClient


def test_hades_backend_status_web_route_reports_canonical_payload(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    from hermes_cli import hades_backend_db as hdb
    from hermes_cli import web_server

    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev-box",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"jobs": True, "memory": True},
        )
        hdb.upsert_workspace_binding(
            conn,
            project_id="proj_1",
            agent_id="agent_1",
            local_project_id="local_1",
            workspace_fingerprint="wf_1",
            display_path="~/repo",
            repo_root=str(tmp_path / "repo"),
            git_remote_display="origin",
            git_remote_hash="hash",
            head_commit="abc123",
            backend_workspace_binding_id="wb_1",
        )
        hdb.upsert_job(
            conn,
            job_id="job_1",
            project_id="proj_1",
            workspace_binding_id="wb_1",
            capability="sync_git_tree",
            payload={"paths": ["."]},
            status="waiting_confirmation",
        )
        proposal = hdb.create_memory_proposal(
            conn,
            project_id="proj_1",
            workspace_binding_id="wb_1",
            action="create",
            intent="remember",
            summary="Rejected proposal",
            provenance={"source": "test"},
        )
        hdb.mark_memory_proposal_status(conn, proposal.id, "refused", "duplicate")
        hdb.save_inbox_event(
            conn,
            event_id="evt_1",
            project_id="proj_1",
            event_type="message",
            payload={"message": "hello"},
        )
        hdb.record_sync_state(conn, "last_sync_summary", {"completed": 1, "waiting": 1})
        hdb.record_sync_state(
            conn,
            "last_quality_report",
            {
                "schema": "hades.quality_report.v1",
                "status": "failed",
                "summary": {"blockers": 1, "warnings": 0, "actions": 1},
                "metrics": {"no_codebase": {"accuracy": 0.5}},
                "action_queue": [
                    {
                        "id": "fix_no_codebase_eval_failures",
                        "severity": "blocker",
                        "message": "Fix failing no-codebase diagnosis fixtures before release.",
                    }
                ],
            },
        )

    previous_auth_required = getattr(web_server.app.state, "auth_required", None)
    web_server.app.state.auth_required = False
    client = TestClient(web_server.app)
    client.headers[web_server._SESSION_HEADER_NAME] = web_server._SESSION_TOKEN
    try:
        response = client.get("/api/hades/backend/status")
    finally:
        if previous_auth_required is None:
            try:
                delattr(web_server.app.state, "auth_required")
            except AttributeError:
                pass
        else:
            web_server.app.state.auth_required = previous_auth_required

    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is True
    assert body["degraded"] is True
    assert body["agent"]["project_id"] == "proj_1"
    assert body["bindings"][0]["workspace_binding_id"] == "wb_1"
    assert body["job_counts"]["waiting_confirmation"] == 1
    assert body["proposal_counts"]["refused"] == 1
    assert body["inbox_counts"] == {"total": 1, "unread": 1}
    assert body["sync"]["last_summary"] == {"completed": 1, "waiting": 1}
    assert isinstance(body["sync"]["last_summary_updated_at"], int)
    assert body["quality"]["last_report"]["status"] == "failed"
    assert body["quality"]["last_report"]["summary"]["blockers"] == 1
    assert isinstance(body["quality"]["last_report_updated_at"], int)
    assert any("backend job" in action for action in body["actions"])
    assert any("memory proposal" in action for action in body["actions"])
    assert any("quality report" in action for action in body["actions"])


def test_hades_backend_web_routes_review_jobs_and_proposals(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    from hermes_cli import hades_backend_actions
    from hermes_cli import hades_backend_db as hdb
    from hermes_cli import hades_backend_runtime
    from hermes_cli import web_server

    class FakeBackendClient:
        def __init__(self):
            self.status_updates = []
            self.results = []

        def update_job_status(self, job_id, **payload):
            self.status_updates.append((job_id, payload))
            return {"ok": True}

        def submit_job_result(self, job_id, **payload):
            self.results.append((job_id, payload))
            return {"ok": True}

    fake_client = FakeBackendClient()
    monkeypatch.setattr(hades_backend_runtime, "client_from_config", lambda: fake_client)
    monkeypatch.setattr(
        hades_backend_actions,
        "execute_job",
        lambda job, workspace_root: {"status": "completed", "summary": "tree synced"},
    )

    repo = tmp_path / "repo"
    repo.mkdir()
    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev-box",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"jobs": True, "memory": True},
        )
        hdb.upsert_workspace_binding(
            conn,
            project_id="proj_1",
            agent_id="agent_1",
            local_project_id="local_1",
            workspace_fingerprint="wf_1",
            display_path="~/repo",
            repo_root=str(repo),
            git_remote_display="origin",
            git_remote_hash="hash",
            head_commit="abc123",
            backend_workspace_binding_id="wb_1",
        )
        hdb.upsert_job(
            conn,
            job_id="job_refuse",
            project_id="proj_1",
            workspace_binding_id="wb_1",
            capability="sync_git_tree",
            payload={"paths": ["."]},
            status="waiting_confirmation",
        )
        hdb.upsert_job(
            conn,
            job_id="job_approve",
            project_id="proj_1",
            workspace_binding_id="wb_1",
            capability="sync_git_tree",
            payload={"paths": ["."]},
            status="waiting_confirmation",
        )
        proposal = hdb.create_memory_proposal(
            conn,
            project_id="proj_1",
            workspace_binding_id="wb_1",
            action="create",
            intent="remember",
            summary="Rejected proposal",
            provenance={"source": "test"},
        )
        hdb.mark_memory_proposal_status(conn, proposal.id, "refused", "duplicate")

    previous_auth_required = getattr(web_server.app.state, "auth_required", None)
    web_server.app.state.auth_required = False
    client = TestClient(web_server.app)
    client.headers[web_server._SESSION_HEADER_NAME] = web_server._SESSION_TOKEN
    try:
        jobs_response = client.get("/api/hades/backend/jobs")
        proposals_response = client.get("/api/hades/backend/proposals")
        ack_response = client.post(f"/api/hades/backend/proposals/{proposal.id}/ack")
        refuse_response = client.post(
            "/api/hades/backend/jobs/job_refuse/refuse",
            json={"reason": "too broad"},
        )
        approve_response = client.post("/api/hades/backend/jobs/job_approve/approve")
    finally:
        if previous_auth_required is None:
            try:
                delattr(web_server.app.state, "auth_required")
            except AttributeError:
                pass
        else:
            web_server.app.state.auth_required = previous_auth_required

    assert jobs_response.status_code == 200
    assert {job["job_id"] for job in jobs_response.json()["jobs"]} == {"job_refuse", "job_approve"}
    assert proposals_response.status_code == 200
    assert proposals_response.json()["proposals"][0]["proposal_id"] == proposal.id

    assert ack_response.status_code == 200
    assert ack_response.json()["proposal"]["status"] == "acknowledged"
    assert refuse_response.status_code == 200
    assert refuse_response.json()["job"]["status"] == "cancelled"
    assert approve_response.status_code == 200
    assert approve_response.json()["job"]["status"] == "completed"
    assert any(payload["status"] == "cancelled" for _, payload in fake_client.status_updates)
    assert any(payload["status"] == "started" for _, payload in fake_client.status_updates)
    assert fake_client.results[0][0] == "job_approve"


def test_hades_backend_web_route_runs_sync(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    from hermes_cli import hades_backend_sync
    from hermes_cli import web_server

    calls = []

    def fake_sync(*, quiet=False):
        calls.append({"quiet": quiet})
        return hades_backend_sync.SyncResult({"pulled": 1, "completed": 1}, 0)

    monkeypatch.setattr(hades_backend_sync, "run_backend_sync", fake_sync)

    previous_auth_required = getattr(web_server.app.state, "auth_required", None)
    web_server.app.state.auth_required = False
    client = TestClient(web_server.app)
    client.headers[web_server._SESSION_HEADER_NAME] = web_server._SESSION_TOKEN
    try:
        response = client.post("/api/hades/backend/sync")
    finally:
        if previous_auth_required is None:
            try:
                delattr(web_server.app.state, "auth_required")
            except AttributeError:
                pass
        else:
            web_server.app.state.auth_required = previous_auth_required

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "status": "completed",
        "summary": "Backend sync completed",
        "sync": {"pulled": 1, "completed": 1},
    }
    assert calls == [{"quiet": True}]


def test_hades_backend_web_routes_run_privacy_and_retention_actions(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    from hermes_cli import hades_backend_db as hdb
    from hermes_cli import hades_backend_runtime
    from hermes_cli import web_server

    class FakeBackendClient:
        def __init__(self):
            self.calls = []
            self.closed = 0

        def privacy_export(self, **payload):
            self.calls.append(("privacy_export", payload))
            return {
                "include_content": payload["include_content"],
                "counts": {"bug_reports": 1, "source_slices": 2},
            }

        def privacy_delete(self, **payload):
            self.calls.append(("privacy_delete", payload))
            key = "deleted" if payload["confirm"] else "would_delete"
            return {"dry_run": payload["dry_run"], key: {"hades_bug_reports": 1}}

        def privacy_retention_cleanup(self, **payload):
            self.calls.append(("privacy_retention_cleanup", payload))
            key = "deleted" if payload["confirm"] else "would_delete"
            return {
                "retention_days": payload["retention_days"],
                "dry_run": payload["dry_run"],
                key: {"hades_bug_evidence": 2},
            }

        def close(self):
            self.closed += 1

    fake_client = FakeBackendClient()
    monkeypatch.setattr(hades_backend_runtime, "client_from_config", lambda: fake_client)

    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev-box",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"jobs": True, "memory": True},
        )
        hdb.upsert_workspace_binding(
            conn,
            project_id="proj_1",
            agent_id="agent_1",
            local_project_id="local_1",
            workspace_fingerprint="wf_1",
            display_path="~/repo",
            repo_root=str(repo),
            git_remote_display="origin",
            git_remote_hash="hash",
            head_commit="abc123",
            backend_workspace_binding_id="wb_1",
        )

    previous_auth_required = getattr(web_server.app.state, "auth_required", None)
    web_server.app.state.auth_required = False
    client = TestClient(web_server.app)
    client.headers[web_server._SESSION_HEADER_NAME] = web_server._SESSION_TOKEN
    try:
        export_response = client.post("/api/hades/backend/privacy-export", json={})
        delete_response = client.post("/api/hades/backend/privacy-delete", json={})
        cleanup_response = client.post(
            "/api/hades/backend/retention-cleanup",
            json={"retention_days": 14, "confirm": True},
        )
    finally:
        if previous_auth_required is None:
            try:
                delattr(web_server.app.state, "auth_required")
            except AttributeError:
                pass
        else:
            web_server.app.state.auth_required = previous_auth_required

    assert export_response.status_code == 200
    assert export_response.json()["include_content"] is False
    assert export_response.json()["counts"] == {"bug_reports": 1, "source_slices": 2}
    assert delete_response.status_code == 200
    assert delete_response.json()["dry_run"] is True
    assert delete_response.json()["would_delete"] == {"hades_bug_reports": 1}
    assert cleanup_response.status_code == 200
    assert cleanup_response.json()["dry_run"] is False
    assert cleanup_response.json()["retention_days"] == 14
    assert cleanup_response.json()["deleted"] == {"hades_bug_evidence": 2}
    assert fake_client.closed == 3
    assert fake_client.calls == [
        (
            "privacy_export",
            {
                "project_id": "proj_1",
                "workspace_binding_id": "wb_1",
                "include_content": False,
            },
        ),
        (
            "privacy_delete",
            {
                "project_id": "proj_1",
                "workspace_binding_id": "wb_1",
                "dry_run": True,
                "confirm": False,
            },
        ),
        (
            "privacy_retention_cleanup",
            {
                "project_id": "proj_1",
                "workspace_binding_id": "wb_1",
                "retention_days": 14,
                "dry_run": False,
                "confirm": True,
            },
        ),
    ]


def test_hades_backend_web_route_creates_bug_intake_with_evidence(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    from hermes_cli import hades_backend_db as hdb
    from hermes_cli import hades_backend_runtime
    from hermes_cli import web_server

    class FakeBackendClient:
        def __init__(self):
            self.reports = []
            self.evidence = []
            self.closed = False

        def create_bug_report(self, **payload):
            self.reports.append(payload)
            return {"bug_report": {"id": "bug_1"}}

        def create_bug_evidence(self, **payload):
            self.evidence.append(payload)
            return {"evidence": {"id": f"ev_{len(self.evidence)}"}}

        def close(self):
            self.closed = True

    fake_client = FakeBackendClient()
    monkeypatch.setattr(hades_backend_runtime, "client_from_config", lambda: fake_client)

    repo = tmp_path / "repo"
    repo.mkdir()
    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev-box",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"jobs": True, "memory": True},
        )
        hdb.upsert_workspace_binding(
            conn,
            project_id="proj_1",
            agent_id="agent_1",
            local_project_id="local_1",
            workspace_fingerprint="wf_1",
            display_path="~/repo",
            repo_root=str(repo),
            git_remote_display="origin",
            git_remote_hash="hash",
            head_commit="a" * 40,
            backend_workspace_binding_id="wb_1",
        )

    previous_auth_required = getattr(web_server.app.state, "auth_required", None)
    web_server.app.state.auth_required = False
    client = TestClient(web_server.app)
    client.headers[web_server._SESSION_HEADER_NAME] = web_server._SESSION_TOKEN
    try:
        response = client.post(
            "/api/hades/backend/bug-intake",
            json={
                "workspace_binding_id": "wb_1",
                "title": "Checkout fails",
                "symptom": "The checkout endpoint returns 500.",
                "steps": "Open cart and submit payment.",
                "expected": "Order is created.",
                "actual": "HTTP 500.",
                "severity": "high",
                "environment": "production",
                "failing_test": "FAILED tests/test_checkout.py::test_submit OPENAI_API_KEY=sk-live-secretvalue12345",
                "runtime_log": "ERROR checkout Authorization: Bearer abcdefghijklmnopqrstuvwxyz",
                "deploy_commit": "b" * 40,
                "workspace_head": "a" * 40,
                "request_url": "https://app.example/orders/1?token=supersecret12345",
                "request_method": "post",
                "response_status": 500,
            },
        )
    finally:
        if previous_auth_required is None:
            try:
                delattr(web_server.app.state, "auth_required")
            except AttributeError:
                pass
        else:
            web_server.app.state.auth_required = previous_auth_required

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["bug_report_id"] == "bug_1"
    assert body["project_id"] == "proj_1"
    assert body["workspace_binding_id"] == "wb_1"
    assert body["evidence_ids"] == ["ev_1", "ev_2", "ev_3", "ev_4", "ev_5"]
    assert fake_client.closed is True

    assert fake_client.reports == [
        {
            "project_id": "proj_1",
            "workspace_binding_id": "wb_1",
            "title": "Checkout fails",
            "symptom": "The checkout endpoint returns 500.",
            "payload": {
                "schema": "hades.bug_intake.v1",
                "source": "dashboard",
                "steps": "Open cart and submit payment.",
                "expected": "Order is created.",
                "actual": "HTTP 500.",
                "severity": "high",
                "environment": "production",
                "agent_id": "agent_1",
            },
        }
    ]

    assert [item["kind"] for item in fake_client.evidence] == [
        "failing_test",
        "log_excerpt",
        "deploy_version",
        "http_request",
        "http_response",
    ]
    evidence_json = str(fake_client.evidence)
    assert "sk-live-secretvalue12345" not in evidence_json
    assert "abcdefghijklmnopqrstuvwxyz" not in evidence_json
    assert "supersecret12345" not in evidence_json
    assert fake_client.evidence[2]["payload"]["mismatch"] is True
    assert fake_client.evidence[3]["payload"]["method"] == "POST"
    assert fake_client.evidence[4]["payload"]["status"] == 500


def test_hades_backend_web_route_reads_bug_report_detail(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    from hermes_cli import hades_backend_db as hdb
    from hermes_cli import hades_backend_runtime
    from hermes_cli import web_server

    class FakeBackendClient:
        def __init__(self):
            self.calls = []
            self.closed = False

        def get_bug_report(self, bug_report_id, **payload):
            self.calls.append((bug_report_id, payload))
            return {
                "bug_report": {
                    "id": bug_report_id,
                    "title": "Checkout fails",
                    "symptom": "HTTP 500 on submit",
                    "status": "open",
                    "severity": "high",
                },
                "evidence": [
                    {
                        "id": "ev_1",
                        "kind": "failing_test",
                        "summary": "CheckoutTest failed",
                        "source": "pytest",
                        "retention_class": "test_failure",
                    }
                ],
                "diagnosis_reports": [
                    {
                        "id": "diag_1",
                        "confidence": "medium",
                        "root_cause": "Order persistence fails after payment capture",
                    }
                ],
            }

        def evidence_packs(self, **payload):
            self.calls.append(("evidence_packs", payload))
            return {
                "count": 1,
                "items": [
                    {
                        "id": "pack_1",
                        "title": "Checkout evidence pack",
                        "evidence_refs": [{"type": "bug_evidence", "id": "ev_1"}],
                        "graph_refs": [{"type": "route", "id": "checkout.submit"}],
                        "source_slice_ids": ["slice_1"],
                    }
                ],
                "freshness": {"status": "current"},
            }

        def close(self):
            self.closed = True

    fake_client = FakeBackendClient()
    monkeypatch.setattr(hades_backend_runtime, "client_from_config", lambda: fake_client)

    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev-box",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"jobs": True, "memory": True},
        )
        hdb.upsert_workspace_binding(
            conn,
            project_id="proj_1",
            agent_id="agent_1",
            local_project_id="local_1",
            workspace_fingerprint="wf_1",
            display_path="~/repo",
            repo_root=str(repo),
            git_remote_display="origin",
            git_remote_hash="hash",
            head_commit="a" * 40,
            backend_workspace_binding_id="wb_1",
        )

    previous_auth_required = getattr(web_server.app.state, "auth_required", None)
    web_server.app.state.auth_required = False
    client = TestClient(web_server.app)
    client.headers[web_server._SESSION_HEADER_NAME] = web_server._SESSION_TOKEN
    try:
        response = client.get("/api/hades/backend/bug-reports/bug_1")
    finally:
        if previous_auth_required is None:
            try:
                delattr(web_server.app.state, "auth_required")
            except AttributeError:
                pass
        else:
            web_server.app.state.auth_required = previous_auth_required

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["status"] == "loaded"
    assert body["project_id"] == "proj_1"
    assert body["workspace_binding_id"] == "wb_1"
    assert body["bug_report"]["title"] == "Checkout fails"
    assert body["evidence"][0]["id"] == "ev_1"
    assert body["evidence_packs"][0]["id"] == "pack_1"
    assert body["evidence_pack_count"] == 1
    assert body["evidence_pack_freshness"] == {"status": "current"}
    assert body["diagnosis_reports"][0]["id"] == "diag_1"
    assert fake_client.closed is True
    assert fake_client.calls == [
        (
            "bug_1",
            {
                "project_id": "proj_1",
                "workspace_binding_id": "wb_1",
            },
        ),
        (
            "evidence_packs",
            {
                "project_id": "proj_1",
                "workspace_binding_id": "wb_1",
                "bug_report_id": "bug_1",
                "limit": 5,
            },
        ),
    ]


def test_hades_backend_web_route_promotes_diagnosis(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    from hermes_cli import hades_backend_db as hdb
    from hermes_cli import hades_backend_runtime
    from hermes_cli import web_server

    class FakeBackendClient:
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

    fake_client = FakeBackendClient()
    monkeypatch.setattr(hades_backend_runtime, "client_from_config", lambda: fake_client)

    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev-box",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"jobs": True, "memory": True},
        )
        hdb.upsert_workspace_binding(
            conn,
            project_id="proj_1",
            agent_id="agent_1",
            local_project_id="local_1",
            workspace_fingerprint="wf_1",
            display_path="~/repo",
            repo_root=str(repo),
            git_remote_display="origin",
            git_remote_hash="hash",
            head_commit="abc123",
            backend_workspace_binding_id="wb_1",
        )

    previous_auth_required = getattr(web_server.app.state, "auth_required", None)
    web_server.app.state.auth_required = False
    client = TestClient(web_server.app)
    client.headers[web_server._SESSION_HEADER_NAME] = web_server._SESSION_TOKEN
    try:
        response = client.post(
            "/api/hades/backend/promote-diagnosis",
            json={
                "diagnosis_report_id": "diag_1",
                "verification_status": "test_passed",
                "fix_commit": "abc123",
                "fix_pr_url": "https://example.test/pr/1",
                "affected_symbols": ["OrderController@show"],
                "regression_tests": ["OrderControllerTest::test_missing_customer"],
                "notes": "Regression passed with OPENAI_API_KEY=sk-live-secretvalue12345",
            },
        )
    finally:
        if previous_auth_required is None:
            try:
                delattr(web_server.app.state, "auth_required")
            except AttributeError:
                pass
        else:
            web_server.app.state.auth_required = previous_auth_required

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["status"] == "promoted"
    assert body["resolved_bug_memory_id"] == "mem_bug_1"
    assert fake_client.closed is True
    assert fake_client.calls == [
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
