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


def test_status_reports_sanitized_queue_health() -> None:
    payload = _status(
        persephone={
            "state": "connected",
            "projects": 2,
            "unread": 3,
            "pending_approval": 4,
            "retry": 5,
            "dead_letters": 6,
            "last_error": "must not be copied",
            "payload": {"secret": "must not be copied"},
        }
    )

    assert payload["persephone"] == {
        "scope": "profile_linked_routes",
        "state": "connected",
        "active": True,
        "routes": 0,
        "agents": 0,
        "projects": 2,
        "unread": 3,
        "pending_approval": 4,
        "retry": 5,
        "dead_letters": 6,
        "failure_count": 0,
        "restart_streak": 0,
        "next_retry_at": None,
        "stable_since": None,
    }


def test_failed_or_backoff_queue_marks_status_degraded() -> None:
    for state in ("backoff", "failed"):
        payload = _status(persephone={"state": state, "projects": 1})
        assert payload["degraded"] is True


def test_unknown_queue_state_fails_closed() -> None:
    payload = _status(persephone={"state": "surprise", "projects": -4})

    assert payload["persephone"]["state"] == "failed"
    assert payload["persephone"]["projects"] == 0
    assert payload["degraded"] is True


def test_local_queue_health_counts_only_states_not_payloads(tmp_path) -> None:
    from hermes_cli import hades_backend_db as db
    from hermes_cli.hades_backend_status import _load_persephone_status

    with db.connect_closing(tmp_path / "status.db") as conn:
        db.record_sync_state(
            conn,
            "persephone_receiver_status",
            {"state": "backoff", "active": True, "failure_count": 2, "next_retry_at": 123},
        )
        agent = db.save_agent(
            conn,
            agent_id="agent_1",
            project_id="project_1",
            base_url="https://example.invalid",
            label="test",
            token_env_key="TOKEN",
            capabilities={},
        )
        binding = db.upsert_workspace_binding(
            conn,
            project_id="project_1",
            agent_id="agent_1",
            local_project_id="local_1",
            workspace_fingerprint="fingerprint_1",
            display_path="~/repo",
            repo_root=str(tmp_path),
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id="wb_1",
        )
        envelope = '{"secret":"not returned"}'
        conn.execute(
            "INSERT INTO persephone_inbox "
            "(message_id, project_id, target_agent_id, envelope, message_type, effect, capability, state, attempts, next_attempt_at, received_at, updated_at) "
            "VALUES ('m1', 'project_1', 'agent_1', ?, 'information_request', 'mutating', 'unknown', 'waiting_human_approval', 0, 0, 1, 1)",
            (envelope,),
        )
        health = _load_persephone_status(conn, agent=agent, bindings=[binding])

    assert health == {
        "scope": "profile_linked_routes",
        "state": "backoff",
        "active": True,
        "routes": 1,
        "agents": 1,
        "projects": 1,
        "unread": 0,
        "pending_approval": 1,
        "retry": 0,
        "dead_letters": 0,
        "failure_count": 2,
        "restart_streak": 0,
        "next_retry_at": 123,
        "stable_since": None,
    }


def test_support_report_strips_arbitrary_persephone_fields() -> None:
    from hermes_cli.hades_backend_status import support_report_payload

    report = support_report_payload(
        {
            "persephone": {
                "state": "connected",
                "projects": 1,
                "payload": {"token": "super-secret"},
                "last_error": "Bearer super-secret",
                "unexpected": "leak",
            }
        }
    )

    rendered = str(report)
    assert "super-secret" not in rendered
    assert "payload" not in report["persephone"]
    assert "last_error" not in report["persephone"]
    assert set(report["persephone"]) == {
        "scope", "state", "active", "routes", "agents", "projects",
        "unread", "pending_approval", "retry", "dead_letters",
        "failure_count", "restart_streak", "next_retry_at", "stable_since",
    }


def test_queue_counts_are_scoped_to_current_project_and_agent(tmp_path) -> None:
    from hermes_cli import hades_backend_db as db
    from hermes_cli.hades_backend_status import _load_persephone_status

    with db.connect_closing(tmp_path / "scope.db") as conn:
        agent = db.save_agent(
            conn, agent_id="current", project_id="project_current",
            base_url="https://example.invalid", label="current",
            token_env_key="TOKEN", capabilities={},
        )
        binding = db.upsert_workspace_binding(
            conn, project_id="project_current", agent_id="current",
            local_project_id="local", workspace_fingerprint="fp",
            display_path="~/repo", repo_root=str(tmp_path),
            git_remote_display="", git_remote_hash="", head_commit="",
            backend_workspace_binding_id="wb",
        )
        for message_id, project, target in (
            ("owned", "project_current", "current"),
            ("foreign_project", "other", "current"),
            ("foreign_agent", "project_current", "other"),
        ):
            conn.execute(
                "INSERT INTO persephone_inbox "
                "(message_id, project_id, target_agent_id, envelope, message_type, effect, capability, state, received_at, updated_at) "
                "VALUES (?, ?, ?, '{}', 'information_request', 'mutating', 'unknown', 'waiting_human_approval', 1, 1)",
                (message_id, project, target),
            )
        health = _load_persephone_status(conn, agent=agent, bindings=[binding])

    assert health["pending_approval"] == 1


def test_queue_health_aggregates_exact_profile_linked_routes(tmp_path) -> None:
    from hermes_cli import hades_backend_db as db
    from hermes_cli.hades_backend_status import _load_persephone_status

    with db.connect_closing(tmp_path / "profile-scope.db") as conn:
        default = db.save_agent(
            conn, agent_id="agent_default", project_id="project_a",
            base_url="https://example.invalid", label="default",
            token_env_key="TOKEN_A", capabilities={},
        )
        second = db.save_agent(
            conn, agent_id="agent_second", project_id="project_b",
            base_url="https://example.invalid", label="second",
            token_env_key="TOKEN_B", capabilities={},
        )
        db.upsert_workspace_binding(
            conn, project_id="project_a", agent_id="agent_default",
            local_project_id="local_a", workspace_fingerprint="fp_a",
            display_path="~/a", repo_root=str(tmp_path / "a"),
            git_remote_display="", git_remote_hash="", head_commit="",
            backend_workspace_binding_id="wb_a",
        )
        # A second binding on the same route must not duplicate its queue rows.
        db.upsert_workspace_binding(
            conn, project_id="project_a", agent_id="agent_default",
            local_project_id="local_a2", workspace_fingerprint="fp_a2",
            display_path="~/a2", repo_root=str(tmp_path / "a2"),
            git_remote_display="", git_remote_hash="", head_commit="",
            backend_workspace_binding_id="wb_a2",
        )
        db.upsert_workspace_binding(
            conn, project_id="project_b", agent_id="agent_second",
            local_project_id="local_b", workspace_fingerprint="fp_b",
            display_path="~/b", repo_root=str(tmp_path / "b"),
            git_remote_display="", git_remote_hash="", head_commit="",
            backend_workspace_binding_id="wb_b",
        )
        db.upsert_workspace_binding(
            conn, project_id="project_c", agent_id="agent_stale",
            local_project_id="local_c", workspace_fingerprint="fp_c",
            display_path="~/c", repo_root=str(tmp_path / "c"),
            git_remote_display="", git_remote_hash="", head_commit="",
            backend_workspace_binding_id="wb_c",
        )
        db.mark_binding_unlinked(conn, "fp_c")
        for message_id, project, target, state in (
            ("a_received", "project_a", "agent_default", "received"),
            ("b_approval", "project_b", "agent_second", "waiting_human_approval"),
            ("b_retry", "project_b", "agent_second", "retry"),
            ("stale_agent", "project_a", "agent_old", "dead_letter"),
            ("unlinked_route", "project_c", "agent_stale", "dead_letter"),
        ):
            conn.execute(
                "INSERT INTO persephone_inbox "
                "(message_id, project_id, target_agent_id, envelope, message_type, effect, capability, state, received_at, updated_at) "
                "VALUES (?, ?, ?, '{}', 'information_request', 'information_read', 'source_search', ?, 1, 1)",
                (message_id, project, target, state),
            )
        for message_id, project, sender, state in (
            ("a_out_retry", "project_a", "agent_default", "retry"),
            ("b_out_dead", "project_b", "agent_second", "dead_letter"),
            ("stale_out", "project_b", "agent_old", "dead_letter"),
        ):
            conn.execute(
                "INSERT INTO persephone_outbox "
                "(message_id, project_id, sender_agent_id, target_agent_id, envelope, state, attempts, next_attempt_at, created_at, updated_at) "
                "VALUES (?, ?, ?, 'target', '{}', ?, 0, 0, 1, 1)",
                (message_id, project, sender, state),
            )

        health = _load_persephone_status(
            conn,
            agent=default,
            bindings=db.list_workspace_bindings(conn),
        )

    assert health == {
        "scope": "profile_linked_routes",
        "state": "disabled_capability",
        "active": False,
        "routes": 2,
        "agents": 2,
        "projects": 2,
        "unread": 1,
        "pending_approval": 1,
        "retry": 2,
        "dead_letters": 1,
        "failure_count": 0,
        "restart_streak": 0,
        "next_retry_at": None,
        "stable_since": None,
    }
    assert second.agent_id == "agent_second"  # both profile agents are intentional


def test_loaded_status_uses_all_linked_routes_but_keeps_default_identity(
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
        conn.execute(
            "INSERT INTO persephone_inbox "
            "(message_id, project_id, target_agent_id, envelope, message_type, effect, capability, state, received_at, updated_at) "
            "VALUES ('old_message', 'project_old', 'older', '{}', 'information_request', 'information_read', 'source_search', 'received', 1, 1)"
        )
        conn.commit()

    monkeypatch.setattr(
        "hermes_cli.hades_backend_status._load_remote_awarenesses",
        lambda agent, bindings: {},
    )
    payload = load_backend_status_payload()

    assert payload["agent"]["agent_id"] == "default"
    assert {item["project_id"] for item in payload["bindings"]} == {"project_default"}
    assert payload["persephone"]["scope"] == "profile_linked_routes"
    assert payload["persephone"]["routes"] == 2
    assert payload["persephone"]["agents"] == 2
    assert payload["persephone"]["projects"] == 2
    assert payload["persephone"]["unread"] == 1
