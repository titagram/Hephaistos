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
        "state": "connected",
        "active": True,
        "projects": 2,
        "unread": 3,
        "pending_approval": 4,
        "retry": 5,
        "dead_letters": 6,
        "failure_count": 0,
        "next_retry_at": None,
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
        "state": "backoff",
        "active": True,
        "projects": 1,
        "unread": 0,
        "pending_approval": 1,
        "retry": 0,
        "dead_letters": 0,
        "failure_count": 2,
        "next_retry_at": 123,
    }
