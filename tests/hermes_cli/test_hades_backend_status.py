from __future__ import annotations

from hermes_cli.hades_backend_status import backend_status_payload


def _status(**overrides):
    values = {
        "agent": None,
        "bindings": [],
        "job_counts": {},
        "proposal_counts": {},
        "inbox_counts": {},
        "last_summary": None,
        "last_error": None,
        "now": 100,
    }
    values.update(overrides)
    return backend_status_payload(**values)


def test_loaded_status_keeps_default_identity_outside_linked_workspace(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import hades_backend_db as db
    from hermes_cli.hades_backend_status import load_backend_status_payload

    with db.connect_closing() as conn:
        db.save_agent(
            conn, agent_id="older", project_id="project_old",
            base_url="https://example.invalid", label="older",
            token_env_key="TOKEN_OLD", capabilities={},
        )
        db.upsert_workspace_binding(
            conn, project_id="project_old", agent_id="older",
            local_project_id="old", workspace_fingerprint="old",
            display_path="~/old", repo_root=str(tmp_path / "old"),
            git_remote_display="", git_remote_hash="", head_commit="",
            backend_workspace_binding_id="wb_old",
        )
        db.save_agent(
            conn, agent_id="default", project_id="project_default",
            base_url="https://example.invalid", label="default",
            token_env_key="TOKEN_DEFAULT", capabilities={},
        )
        db.upsert_workspace_binding(
            conn, project_id="project_default", agent_id="default",
            local_project_id="default", workspace_fingerprint="default",
            display_path="~/default", repo_root=str(tmp_path / "default"),
            git_remote_display="", git_remote_hash="", head_commit="",
            backend_workspace_binding_id="wb_default",
        )
    monkeypatch.setattr(
        "hermes_cli.hades_backend_status._load_remote_awarenesses",
        lambda agent, bindings: {},
    )
    payload = load_backend_status_payload()

    assert payload["agent"]["agent_id"] == "default"
    assert {item["project_id"] for item in payload["bindings"]} == {"project_default"}
    assert "persephone" not in payload


def test_loaded_status_uses_identity_bound_to_current_workspace(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    current_workspace = tmp_path / "current"
    default_workspace = tmp_path / "default"
    current_workspace.mkdir()
    default_workspace.mkdir()

    from hermes_cli import hades_backend_db as db
    from hermes_cli.hades_backend_status import load_backend_status_payload

    with db.connect_closing() as conn:
        db.save_agent(
            conn, agent_id="workspace_agent", project_id="workspace_project",
            base_url="https://example.invalid", label="workspace",
            token_env_key="TOKEN_WORKSPACE", capabilities={},
        )
        db.upsert_workspace_binding(
            conn, project_id="workspace_project", agent_id="workspace_agent",
            local_project_id="workspace", workspace_fingerprint="workspace",
            display_path=str(current_workspace), repo_root=str(current_workspace),
            git_remote_display="", git_remote_hash="", head_commit="",
            backend_workspace_binding_id="wb_workspace",
        )
        db.save_agent(
            conn, agent_id="newer_default", project_id="default_project",
            base_url="https://example.invalid", label="default",
            token_env_key="TOKEN_DEFAULT", capabilities={},
        )
        db.upsert_workspace_binding(
            conn, project_id="default_project", agent_id="newer_default",
            local_project_id="default", workspace_fingerprint="default",
            display_path=str(default_workspace), repo_root=str(default_workspace),
            git_remote_display="", git_remote_hash="", head_commit="",
            backend_workspace_binding_id="wb_default",
        )

    monkeypatch.chdir(current_workspace)
    monkeypatch.setattr(
        "hermes_cli.hades_backend_status._load_remote_awarenesses",
        lambda agent, bindings: {},
    )

    payload = load_backend_status_payload()

    assert payload["agent"]["agent_id"] == "workspace_agent"
    assert payload["agent"]["project_id"] == "workspace_project"
    assert [item["workspace_binding_id"] for item in payload["bindings"]] == [
        "wb_workspace"
    ]
    assert payload["identity"]["workspace_binding"]["current_status"] == "partial"


def test_loaded_status_prefers_nested_workspace_binding_over_default_agent(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    subproject = repo / "subproject"
    subproject.mkdir(parents=True)

    from hermes_cli import hades_backend_db as db
    from hermes_cli.hades_backend_status import (
        background_sync_state_key,
        load_backend_status_payload,
    )

    with db.connect_closing() as conn:
        db.save_agent(
            conn, agent_id="nested-agent", project_id="nested-project",
            base_url="https://example.invalid", label="nested",
            token_env_key="TOKEN_NESTED", capabilities={},
        )
        db.upsert_workspace_binding(
            conn, project_id="nested-project", agent_id="nested-agent",
            local_project_id="nested", workspace_fingerprint="nested",
            display_path=str(subproject), repo_root=str(subproject),
            git_remote_display="", git_remote_hash="", head_commit="",
            backend_workspace_binding_id="nested-binding",
        )
        db.save_agent(
            conn, agent_id="default-agent", project_id="default-project",
            base_url="https://example.invalid", label="default",
            token_env_key="TOKEN_DEFAULT", capabilities={},
        )
        db.upsert_workspace_binding(
            conn, project_id="default-project", agent_id="default-agent",
            local_project_id="default", workspace_fingerprint="default",
            display_path=str(repo), repo_root=str(repo),
            git_remote_display="", git_remote_hash="", head_commit="",
            backend_workspace_binding_id="default-binding",
        )
        db.record_sync_state(
            conn, background_sync_state_key("nested-binding"), {"status": "ok"},
        )
        db.record_sync_state(
            conn, background_sync_state_key("default-binding"), {"status": "failed"},
        )

    monkeypatch.setattr(
        "hermes_cli.hades_backend_status._load_remote_awarenesses",
        lambda agent, bindings: {},
    )
    payload = load_backend_status_payload(cwd=subproject)

    assert payload["agent"]["agent_id"] == "nested-agent"
    assert [item["workspace_binding_id"] for item in payload["bindings"]] == [
        "nested-binding"
    ]
    assert payload["sync"]["background"]["status"] == "ok"


def test_loaded_status_does_not_fallback_to_failed_aggregate_for_current_binding(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "repo"
    workspace.mkdir()

    from hermes_cli import hades_backend_db as db
    from hermes_cli.hades_backend_status import (
        BACKGROUND_SYNC_STATE_KEY,
        load_backend_status_payload,
    )

    with db.connect_closing() as conn:
        db.save_agent(
            conn, agent_id="agent", project_id="project",
            base_url="https://example.invalid", label="current",
            token_env_key="TOKEN_CURRENT", capabilities={},
        )
        db.upsert_workspace_binding(
            conn, project_id="project", agent_id="agent",
            local_project_id="current", workspace_fingerprint="current",
            display_path=str(workspace), repo_root=str(workspace),
            git_remote_display="", git_remote_hash="", head_commit="",
            backend_workspace_binding_id="current-binding",
        )
        db.record_sync_state(
            conn, BACKGROUND_SYNC_STATE_KEY,
            {"status": "failed", "failure_count": 1},
        )

    monkeypatch.setattr(
        "hermes_cli.hades_backend_status._load_remote_awarenesses",
        lambda agent, bindings: {},
    )
    payload = load_backend_status_payload(cwd=workspace)

    assert payload["sync"]["background"] is None
    assert payload["degraded"] is False


def test_loaded_status_reports_auth_quarantine_without_counting_receiver_routes(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import hades_backend_db as db
    from hermes_cli.hades_backend_status import (
        load_backend_status_payload,
        support_report_payload,
    )

    with db.connect_closing() as conn:
        db.save_agent(
            conn, agent_id="agent", project_id="project",
            base_url="https://example.invalid", label="agent",
            token_env_key="TOKEN", capabilities={},
        )
        for suffix in ("a", "b"):
            db.upsert_workspace_binding(
                conn, project_id="project", agent_id="agent",
                local_project_id=suffix, workspace_fingerprint=f"wf_{suffix}",
                display_path=f"~/repo-{suffix}", repo_root=str(tmp_path / suffix),
                git_remote_display="", git_remote_hash="", head_commit="",
                backend_workspace_binding_id=f"wb_{suffix}",
            )
        for now in (100, 200, 300):
            db.record_route_auth_cycle(
                conn, project_id="project", agent_id="agent",
                unauthorized=True, now=now,
            )

    monkeypatch.setattr(
        "hermes_cli.hades_backend_status._load_remote_awarenesses",
        lambda agent, bindings: {},
    )
    payload = load_backend_status_payload()
    support = support_report_payload(payload)

    assert payload["auth_quarantine"] == {"routes": 1, "bindings": 2}
    assert support["auth_quarantine"] == {"routes": 1, "bindings": 2}
    assert "persephone" not in payload
    recovery_actions = [
        action.lower()
        for action in payload["actions"]
        if "quarantined" in action.lower()
    ]
    assert recovery_actions == [
        "re-authenticate quarantined hades routes from each affected checkout."
    ]
