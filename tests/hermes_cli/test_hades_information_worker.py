from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from hermes_cli import hades_backend_db as db
from hermes_cli.hades_persephone_messages import (
    AGENT_MESSAGE_SCHEMA,
    EffectClass,
    MessageType,
    parse_envelope,
)


NOW = 2_000_000_000


def _request(
    workspace: Path,
    *,
    capability: str = "source_search",
    payload: dict | None = None,
):
    return parse_envelope(
        {
            "schema": AGENT_MESSAGE_SCHEMA,
            "message_id": "request_1",
            "correlation_id": "correlation_1",
            "causation_id": None,
            "project_id": "project_1",
            "sender_agent_id": "sender_1",
            "target_agent_id": "agent_1",
            "target_workspace_binding_id": "binding_1",
            "message_type": MessageType.INFORMATION_REQUEST.value,
            "effect": EffectClass.INFORMATION_READ.value,
            "capability": capability,
            "remote_task_id": None,
            "remote_task_version": None,
            "expires_at": NOW + 100,
            "payload": payload or {"query": "needle"},
        },
        now=NOW,
    )


def _binding(workspace: Path) -> db.WorkspaceBinding:
    return db.WorkspaceBinding(
        workspace_fingerprint="fingerprint_1",
        project_id="project_1",
        agent_id="agent_1",
        local_project_id="local_1",
        backend_workspace_binding_id="binding_1",
        display_path=str(workspace),
        repo_root=str(workspace),
        git_remote_display="",
        git_remote_hash="",
        head_commit="",
        status="linked",
    )


@pytest.mark.parametrize(
    "capability",
    ["terminal", "run_tests", "build", "browser", "git_commit", "database_query"],
)
def test_mutating_or_uncertain_capabilities_are_not_auto_accepted(capability):
    from hermes_cli.hades_information_worker import (
        PolicyDenied,
        validate_information_capability,
    )

    with pytest.raises(PolicyDenied):
        validate_information_capability(capability)


def test_information_worker_never_receives_terminal_toolset(tmp_path):
    from hermes_cli.hades_information_worker import run_information_request

    (tmp_path / "module.py").write_text("needle = 'safe'\n", encoding="utf-8")
    calls = []

    def forbidden_agent_factory(**kwargs):
        calls.append(kwargs)
        raise AssertionError("direct source search must not instantiate a model")

    response = run_information_request(
        _request(tmp_path),
        binding=_binding(tmp_path),
        agent_factory=forbidden_agent_factory,
        now=NOW,
    )

    assert calls == []
    assert response.answer_summary == "Found 1 matching source line."
    assert response.evidence_refs[0]["path"] == "module.py"


def test_request_must_match_exact_linked_project_agent_and_workspace(tmp_path):
    from hermes_cli.hades_information_worker import PolicyDenied, run_information_request

    wrong = _binding(tmp_path)
    object.__setattr__(wrong, "project_id", "other_project")

    with pytest.raises(PolicyDenied, match="project"):
        run_information_request(_request(tmp_path), binding=wrong, now=NOW)


def test_expired_request_is_denied_at_worker_boundary(tmp_path):
    from hermes_cli.hades_information_worker import PolicyDenied, run_information_request

    with pytest.raises(PolicyDenied, match="expired"):
        run_information_request(
            _request(tmp_path),
            binding=_binding(tmp_path),
            now=NOW + 100,
        )


def test_source_slice_is_bounded_and_cannot_escape_workspace(tmp_path):
    from hermes_cli.hades_information_worker import PolicyDenied, run_information_request

    (tmp_path / "safe.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    response = run_information_request(
        _request(
            tmp_path,
            capability="source_slice",
            payload={"path": "safe.py", "start_line": 2, "end_line": 3},
        ),
        binding=_binding(tmp_path),
        now=NOW,
    )
    assert response.evidence_refs == (
        {"path": "safe.py", "start_line": 2, "end_line": 3, "content": "two\nthree"},
    )
    assert response.truncated is False

    with pytest.raises(PolicyDenied, match="workspace"):
        run_information_request(
            _request(tmp_path, capability="source_slice", payload={"path": "../secret"}),
            binding=_binding(tmp_path),
            now=NOW,
        )


def test_source_slice_rejects_symlink_escape(tmp_path):
    from hermes_cli.hades_information_worker import PolicyDenied, run_information_request

    outside = tmp_path.parent / "outside-secret.txt"
    outside.write_text("password=do-not-return", encoding="utf-8")
    (tmp_path / "link.txt").symlink_to(outside)

    with pytest.raises(PolicyDenied, match="workspace"):
        run_information_request(
            _request(tmp_path, capability="source_slice", payload={"path": "link.txt"}),
            binding=_binding(tmp_path),
            now=NOW,
        )


@pytest.mark.parametrize(
    "relative",
    [
        ".env",
        ".env.local",
        ".envrc",
        "credentials.json",
        "secrets.yaml",
        "id_rsa",
        "server.pem",
        ".git/config",
        ".hermes/auth.json",
        ".hades/tokens.json",
        "provider_config.yaml",
        "auth.config.json",
    ],
)
def test_source_slice_denies_sensitive_paths_without_echoing_them(tmp_path, relative):
    from hermes_cli.hades_information_worker import PolicyDenied, run_information_request

    target = tmp_path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("password=private-value", encoding="utf-8")

    with pytest.raises(PolicyDenied) as exc_info:
        run_information_request(
            _request(tmp_path, capability="source_slice", payload={"path": relative}),
            binding=_binding(tmp_path),
        )
    assert relative not in str(exc_info.value)
    assert "private-value" not in str(exc_info.value)


def test_source_search_prunes_sensitive_directories_and_files(tmp_path):
    from hermes_cli.hades_information_worker import run_information_request

    for relative in (".env", "credentials.json", ".git/config", ".hermes/auth.json"):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("unique_sensitive_needle", encoding="utf-8")
    (tmp_path / "safe.py").write_text("unique_sensitive_needle = True", encoding="utf-8")

    response = run_information_request(
        _request(tmp_path, payload={"query": "unique_sensitive_needle"}),
        binding=_binding(tmp_path),
    )

    assert [item["path"] for item in response.evidence_refs] == ["safe.py"]


def test_source_content_semantically_redacts_common_secret_formats(tmp_path):
    from hermes_cli.hades_information_worker import run_information_request

    secrets = [
        "PASSWORD=env-private",
        '"api_key": "json-private"',
        "token: 'yaml-private'",
        "AWS_SECRET_ACCESS_KEY=aws-private",
        "authorization: Bearer bearer-private",
        "cookie=session-private",
        "-----BEGIN PRIVATE KEY-----\npem-private\n-----END PRIVATE KEY-----",
        "AKIAABCDEFGHIJKLMNOP",
        "const header = 'Bearer abcdefghijklmnopqrstuvwxyz'",
        "ghp_abcdefghijklmnopqrstuvwxyz123456",
    ]
    (tmp_path / "example.py").write_text("\n".join(secrets), encoding="utf-8")

    response = run_information_request(
        _request(
            tmp_path,
            capability="source_slice",
            payload={"path": "example.py", "start_line": 1, "end_line": 20},
        ),
        binding=_binding(tmp_path),
    )

    rendered = str(response.to_payload())
    for secret in (
        "env-private",
        "json-private",
        "yaml-private",
        "aws-private",
        "bearer-private",
        "session-private",
        "pem-private",
        "AKIAABCDEFGHIJKLMNOP",
        "abcdefghijklmnopqrstuvwxyz",
        "ghp_abcdefghijklmnopqrstuvwxyz123456",
    ):
        assert secret not in rendered


def test_unterminated_private_key_block_is_redacted(tmp_path):
    from hermes_cli.hades_information_worker import run_information_request

    (tmp_path / "example.py").write_text(
        "-----BEGIN PRIVATE KEY-----\n" + ("private-material\n" * 1_000),
        encoding="utf-8",
    )
    response = run_information_request(
        _request(
            tmp_path,
            capability="source_slice",
            payload={"path": "example.py", "start_line": 1, "end_line": 200},
        ),
        binding=_binding(tmp_path),
    )
    assert "private-material" not in str(response.to_payload())


def test_search_uses_top_down_bounded_walk_not_whole_tree_rglob(tmp_path, monkeypatch):
    from hermes_cli import hades_information_worker as worker

    (tmp_path / "module.py").write_text("needle = True", encoding="utf-8")
    monkeypatch.setattr(
        Path,
        "rglob",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unbounded rglob")),
    )

    response = worker.run_information_request(
        _request(tmp_path), binding=_binding(tmp_path)
    )
    assert len(response.evidence_refs) == 1


def test_search_stops_at_file_and_aggregate_read_budget(tmp_path, monkeypatch):
    from hermes_cli import hades_information_worker as worker

    for index in range(10):
        (tmp_path / f"module_{index}.py").write_text("needle = True", encoding="utf-8")
    monkeypatch.setattr(worker, "MAX_FILES_SCANNED", 2)
    monkeypatch.setattr(worker, "MAX_AGGREGATE_BYTES", 100)

    response = worker.run_information_request(
        _request(tmp_path), binding=_binding(tmp_path)
    )

    assert len(response.evidence_refs) <= 2
    assert response.truncated is True


def test_source_search_does_not_follow_symlinks_outside_workspace(tmp_path):
    from hermes_cli.hades_information_worker import run_information_request

    outside = tmp_path.parent / "outside-search-secret.txt"
    outside.write_text("unique_remote_needle", encoding="utf-8")
    (tmp_path / "linked.txt").symlink_to(outside)

    response = run_information_request(
        _request(tmp_path, payload={"query": "unique_remote_needle"}),
        binding=_binding(tmp_path),
    )

    assert response.evidence_refs == ()


def test_symbol_artifact_git_and_project_memory_handlers_are_direct_and_scoped(tmp_path):
    from hermes_cli.hades_information_worker import run_information_request

    (tmp_path / "module.py").write_text("class NeedleService:\n    pass\n", encoding="utf-8")
    (tmp_path / ".git" / "refs" / "heads").mkdir(parents=True)
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (tmp_path / ".git" / "refs" / "heads" / "main").write_text("a" * 40 + "\n", encoding="utf-8")
    conn = db.connect(tmp_path / "memory.db")
    db.replace_memory_cache(
        conn,
        project_id="project_1",
        workspace_binding_id="binding_1",
        version="1",
        items=[{"summary": "Needle architecture"}, {"summary": "unrelated"}],
    )

    symbol = run_information_request(
        _request(tmp_path, capability="symbol_lookup", payload={"symbol": "NeedleService"}),
        binding=_binding(tmp_path),
    )
    artifact = run_information_request(
        _request(tmp_path, capability="artifact_metadata", payload={"path": "module.py"}),
        binding=_binding(tmp_path),
    )
    git = run_information_request(
        _request(tmp_path, capability="git_metadata", payload={}),
        binding=_binding(tmp_path),
    )
    memory = run_information_request(
        _request(tmp_path, capability="project_memory_search", payload={"query": "needle"}),
        binding=_binding(tmp_path),
        connection=conn,
    )

    assert symbol.evidence_refs[0]["path"] == "module.py"
    assert artifact.evidence_refs[0]["size_bytes"] > 0
    assert {item["kind"] for item in git.evidence_refs} == {"git_head", "git_commit"}
    assert len(memory.evidence_refs) == 1


def test_git_metadata_does_not_follow_git_directory_outside_workspace(tmp_path):
    from hermes_cli.hades_information_worker import run_information_request

    outside = tmp_path.parent / "outside-git"
    outside.mkdir()
    (outside / "HEAD").write_text("authorization=Bearer private-token", encoding="utf-8")
    (tmp_path / ".git").symlink_to(outside, target_is_directory=True)

    response = run_information_request(
        _request(tmp_path, capability="git_metadata", payload={}),
        binding=_binding(tmp_path),
    )

    assert response.evidence_refs == ()
    assert "private-token" not in str(response.to_payload())


def test_memory_evidence_redacts_secret_keys_and_absolute_paths(tmp_path):
    from hermes_cli.hades_information_worker import run_information_request

    conn = db.connect(tmp_path / "secret-memory.db")
    db.replace_memory_cache(
        conn,
        project_id="project_1",
        workspace_binding_id="binding_1",
        version="1",
        items=[{"password=private-value": "needle at /Users/alice/private/file.py"}],
    )

    response = run_information_request(
        _request(tmp_path, capability="project_memory_search", payload={"query": "needle"}),
        binding=_binding(tmp_path),
        connection=conn,
    )

    rendered = str(response.to_payload())
    assert "private-value" not in rendered
    assert "/Users/alice" not in rendered


def test_memory_redaction_handles_nested_sensitive_keys_and_cycles(tmp_path):
    from hermes_cli import hades_information_worker as worker

    cyclic = {"password": "private-password", "nested": {"Authorization": "Bearer private"}}
    cyclic["cycle"] = cyclic
    response = worker.InformationResponse("ok", (cyclic,), False, ())

    cleaned = worker._redacted(response, tmp_path)
    rendered = str(cleaned.to_payload())
    assert "private-password" not in rendered
    assert "Bearer private" not in rendered
    assert len(rendered) < 16_000


def test_worker_persists_bounded_response_atomically(tmp_path):
    from hermes_cli.hades_information_worker import execute_stored_information_request
    from hermes_cli.hades_persephone_store import get_message, record_inbox, transition_message

    (tmp_path / "module.py").write_text("needle = 'safe'\n", encoding="utf-8")
    conn = db.connect(tmp_path / "queue.db")
    request = _request(tmp_path)
    record_inbox(conn, request, now=NOW)
    transition_message(conn, request.message_id, "processing", now=NOW)

    stored = execute_stored_information_request(
        conn,
        request.message_id,
        binding=_binding(tmp_path),
        now=NOW,
        response_message_id="response_1",
    )

    linked = get_message(conn, request.message_id)
    assert stored.state == "outbox_pending"
    assert linked is not None and linked.state == "responded"
    payload = stored.envelope.to_dict()["payload"]
    assert payload["answer_summary"] == "Found 1 matching source line."
    assert payload["truncated"] is False
    assert payload["residual_uncertainty"] == []
    assert len(str(payload)) < 16_000


def test_operational_failure_is_redacted_and_requeued(tmp_path, monkeypatch):
    from hermes_cli import hades_information_worker as worker
    from hermes_cli.hades_persephone_store import record_inbox, transition_message

    conn = db.connect(tmp_path / "failure.db")
    request = _request(tmp_path)
    record_inbox(conn, request, now=NOW)
    transition_message(conn, request.message_id, "processing", now=NOW)

    def fail(*args, **kwargs):
        raise OSError("authorization=Bearer very-secret-token")

    monkeypatch.setattr(worker, "_search", fail)
    result = worker.execute_stored_information_request(
        conn,
        request.message_id,
        binding=_binding(tmp_path),
        now=NOW,
        response_message_id="response_failure",
    )

    assert result is None
    stored = worker.get_message(conn, request.message_id)
    assert stored is not None and stored.state == "received"
    assert stored.last_error == "information_handler_failed"
    assert "very-secret-token" not in str(stored)


def test_large_memory_matches_still_fit_wire_payload(tmp_path):
    from hermes_cli.hades_information_worker import run_information_request

    conn = db.connect(tmp_path / "large-memory.db")
    db.replace_memory_cache(
        conn,
        project_id="project_1",
        workspace_binding_id="binding_1",
        version="1",
        items=[{"summary": "needle " + ("x" * 20_000)} for _ in range(20)],
    )

    response = run_information_request(
        _request(tmp_path, capability="project_memory_search", payload={"query": "needle"}),
        binding=_binding(tmp_path),
        connection=conn,
    )

    assert len(str(response.to_payload()).encode()) < 16_000
    assert response.truncated is True


def test_oversized_memory_blob_is_rejected_before_json_materialization(
    tmp_path, monkeypatch
):
    from hermes_cli import hades_information_worker as worker

    conn = db.connect(tmp_path / "oversized-memory.db")
    db.replace_memory_cache(
        conn,
        project_id="project_1",
        workspace_binding_id="binding_1",
        version="1",
        items=[{"summary": "needle " + ("x" * 200)}],
    )
    monkeypatch.setattr(worker, "MAX_AGGREGATE_BYTES", 100)
    monkeypatch.setattr(
        worker.db,
        "get_memory_cache",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("oversized JSON was materialized")
        ),
    )

    response = worker.run_information_request(
        _request(tmp_path, capability="project_memory_search", payload={"query": "needle"}),
        binding=_binding(tmp_path),
        connection=conn,
    )

    assert response.evidence_refs == ()
    assert response.truncated is True


def test_response_construction_failure_leaves_request_retryable(tmp_path, monkeypatch):
    from hermes_cli import hades_information_worker as worker
    from hermes_cli.hades_persephone_store import get_message, record_inbox, transition_message

    (tmp_path / "module.py").write_text("needle = True\n", encoding="utf-8")
    conn = db.connect(tmp_path / "construction-failure.db")
    request = _request(tmp_path)
    record_inbox(conn, request, now=NOW)
    transition_message(conn, request.message_id, "processing", now=NOW)
    monkeypatch.setattr(
        worker,
        "make_response",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("cannot encode")),
    )

    result = worker.execute_stored_information_request(
        conn,
        request.message_id,
        binding=_binding(tmp_path),
        now=NOW,
        response_message_id="response_invalid",
    )

    stored = get_message(conn, request.message_id)
    assert result is None
    assert stored is not None and stored.state == "received"


def test_worker_requires_processing_state_before_read(tmp_path, monkeypatch):
    from hermes_cli import hades_information_worker as worker
    from hermes_cli.hades_persephone_store import record_inbox

    conn = db.connect(tmp_path / "wrong-state.db")
    request = _request(tmp_path)
    record_inbox(conn, request, now=NOW)
    monkeypatch.setattr(
        worker,
        "run_information_request",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("read happened")),
    )

    with pytest.raises(worker.PolicyDenied, match="processing"):
        worker.execute_stored_information_request(
            conn,
            request.message_id,
            binding=_binding(tmp_path),
            now=NOW,
            response_message_id="response_wrong_state",
        )
