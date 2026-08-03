from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

def test_hades_coordination_profiles_are_curated_and_local_only():
    from hermes_cli.hades_coordination import hades_coordination_profiles

    profiles = hades_coordination_profiles()
    ids = {profile["id"] for profile in profiles}

    assert {"planner", "implementer", "reviewer", "sync-curator", "memory-steward"}.issubset(ids)
    for profile in profiles:
        routing = profile["model_routing"]
        assert profile["backend_visible"] is False
        assert routing["provider_source"] == "config.yaml"
        assert "local_model_profile" in routing
        assert "provider" not in routing
        assert "model" not in routing


def test_hades_coordination_reviewer_does_not_require_live_review_authority():
    from hermes_cli.hades_coordination import hades_coordination_profile

    reviewer = hades_coordination_profile("reviewer")

    assert reviewer is not None
    assert reviewer["skill"] == "software-development/hierarchical-development"


def test_hades_coordination_profiles_are_copy_safe():
    from hermes_cli.hades_coordination import hades_coordination_profiles

    profiles = hades_coordination_profiles()
    profiles[0]["toolsets"].append("mutated")

    fresh = hades_coordination_profiles()

    assert "mutated" not in fresh[0]["toolsets"]


from hermes_cli import kanban_db as kb
from hermes_cli.hades_coordination import (
    claim_org_run_remote_task_outcome,
    claim_org_run_remote_task,
    publish_org_run_completion,
    post_coordination_event,
    snapshot_org_run,
)
from hermes_cli.hierarchical_execution import parse_execution_portfolio, validate_execution_portfolio
from hermes_cli.kanban_portfolio import create_org_run
from hermes_cli.kanban_swarm import latest_blackboard
from hermes_cli.kanban_backend import KanbanBackendContext
from hermes_cli.hades_backend_client import HadesBackendError


def _wrapped_org_connect_error() -> Exception:
    request = httpx.Request("POST", "https://hades.invalid/agent/work-items")
    try:
        raise httpx.ConnectError(
            "token=org-secret connection refused",
            request=request,
        )
    except httpx.TransportError as exc:
        try:
            raise HadesBackendError(str(exc)) from exc
        except HadesBackendError as wrapped:
            return wrapped

def _org_plan():
    return parse_execution_portfolio({"schema": "hades.execution-portfolio.v1", "org_run_id": "org_coord_1", "project_id": "p", "repository_id": "r", "workspace_binding_id": "binding-1", "base_commit": "a" * 40, "tasks": [{"remote_task_id": "HD-1", "work_item_id": "awi-1", "title": "Task", "body": "Body", "assignee": "default", "priority": 1, "risk": "low", "depends_on": [], "write_scope": ["src/a.py"]}]})

def test_snapshot_reports_execution_and_only_execution_is_dispatchable(tmp_path):
    conn = kb.connect(tmp_path / "kanban.db")
    try:
        plan = _org_plan(); created = create_org_run(conn, plan, validate_execution_portfolio(plan)); snapshot = snapshot_org_run(conn, plan.org_run_id, created)
        assert snapshot.phase == "execution"
        assert snapshot.complete is False
        assert snapshot.dispatchable == (created.remote_tasks["HD-1"].execution_id,)
    finally: conn.close()
def test_typed_coordination_event_is_bounded_and_structured(tmp_path):
    conn = kb.connect(tmp_path / "kanban.db")
    try:
        plan = _org_plan(); created = create_org_run(conn, plan, validate_execution_portfolio(plan))
        post_coordination_event(conn, anchor_id=created.anchor_id, event_type="review_request", summary="Review the bounded evidence.", related_task_ids=[created.remote_tasks["HD-1"].review_id], required_action="verify tests", evidence_refs=["run:1"])
        blackboard = latest_blackboard(conn, created.anchor_id)
        assert blackboard["coordination:review_request"]["type"] == "review_request"
        assert blackboard["coordination:review_request"]["required_action"] == "verify tests"
    finally: conn.close()


def test_publish_requires_completed_integration_and_org_review(tmp_path):
    conn = kb.connect(tmp_path / "kanban.db")
    try:
        plan = _org_plan()
        created = create_org_run(conn, plan, validate_execution_portfolio(plan))
        published, reason = publish_org_run_completion(
            conn,
            board="target",
            org_run_id=plan.org_run_id,
            topology=created,
            remote_task_id="HD-1",
            message="bounded result",
        )
        assert published is False
        assert reason == "integration gate is not complete"
    finally:
        conn.close()


def test_org_run_creation_links_gated_policy_in_same_transaction(tmp_path):
    conn = kb.connect(tmp_path / "kanban.db")
    try:
        plan = _org_plan()
        created = create_org_run(
            conn, plan, validate_execution_portfolio(plan),
        )
        link = kb.get_remote_link(
            conn, created.remote_tasks["HD-1"].execution_id,
        )
        assert link is not None
        assert link.publication_policy == "org_run_gated"
        assert link.sync_status == "linked"
    finally:
        conn.close()


def test_org_run_creation_rollback_cannot_leave_ordinary_link(
    tmp_path, monkeypatch,
):
    """A crash after link insertion rolls back the execution node and policy."""
    conn = kb.connect(tmp_path / "kanban.db")
    real_upsert = kb.upsert_remote_link

    def interrupt_after_link(*args, **kwargs):
        real_upsert(*args, **kwargs)
        raise RuntimeError("simulated interruption")

    monkeypatch.setattr(kb, "upsert_remote_link", interrupt_after_link)
    try:
        plan = _org_plan()
        with pytest.raises(RuntimeError, match="simulated interruption"):
            create_org_run(conn, plan, validate_execution_portfolio(plan))
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM kanban_remote_links"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_gated_policy_survives_claim_heartbeat_and_generic_completion(
    tmp_path, monkeypatch,
):
    """Mutable lease/sync lifecycle can never erase the publication gate."""
    import hermes_cli.hades_kanban_sync as remote_sync
    import hermes_cli.kanban_backend as backend

    class Client:
        def claim_agent_work_item(self, *_args, **_kwargs):
            return {"lease_token": "lease-1"}

        def heartbeat_agent_work_item(self, *_args, **_kwargs):
            return {}

    conn = kb.connect(tmp_path / "kanban.db")
    try:
        plan = _org_plan()
        created = create_org_run(
            conn, plan, validate_execution_portfolio(plan),
        )
        execution_id = created.remote_tasks["HD-1"].execution_id
        client = Client()
        outcome = claim_org_run_remote_task_outcome(
            conn,
            client=client,
            topology=created,
            remote_task_id="HD-1",
            local_workspace_id="lw-1",
        )
        assert outcome.action == "allow"
        monkeypatch.setattr(
            backend,
            "resolve_kanban_backend_context",
            lambda **_kwargs: KanbanBackendContext(
                "linked",
                Path.cwd(),
                project_id="p",
                workspace_binding_id="binding-1",
                local_workspace_id="lw-1",
                agent_id="agent-1",
            ),
        )
        monkeypatch.setattr(
            backend,
            "make_kanban_client",
            lambda *_args, **_kwargs: client,
        )
        assert remote_sync.heartbeat_remote_for_local_task_context(
            conn, execution_id,
        ) == "renewed"
        assert kb.complete_task(conn, execution_id, summary="implementation done")
        link = kb.get_remote_link(conn, execution_id)
        assert link.publication_policy == "org_run_gated"
        assert link.sync_status == "leased"
        assert conn.execute(
            "SELECT COUNT(*) FROM kanban_sync_outbox WHERE task_id=?",
            (execution_id,),
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_gated_policy_survives_generic_admission_and_skips_generic_outbox(
    tmp_path,
):
    import hermes_cli.hades_kanban_sync as remote_sync

    class Client:
        def claim_agent_work_item(self, *_args, **_kwargs):
            return {"lease_token": "lease-1"}

    conn = kb.connect(tmp_path / "kanban.db")
    try:
        plan = _org_plan()
        created = create_org_run(
            conn, plan, validate_execution_portfolio(plan),
        )
        execution_id = created.remote_tasks["HD-1"].execution_id
        decision = remote_sync.make_remote_admission(
            conn,
            context=KanbanBackendContext(
                "linked",
                Path.cwd(),
                project_id="p",
                workspace_binding_id="binding-1",
                local_workspace_id="lw-1",
                agent_id="agent-1",
            ),
            client_factory=lambda _context: Client(),
        )(kb.get_task(conn, execution_id))
        assert decision.action == "allow"
        assert kb.complete_task(conn, execution_id, summary="done")
        link = kb.get_remote_link(conn, execution_id)
        assert link.publication_policy == "org_run_gated"
        assert link.sync_status == "leased"
        assert conn.execute(
            "SELECT COUNT(*) FROM kanban_sync_outbox WHERE task_id=?",
            (execution_id,),
        ).fetchone()[0] == 0
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("failure", "action", "reason"),
    [
        (
            _wrapped_org_connect_error(),
            "defer",
            "remote_backend_unavailable",
        ),
        (
            HadesBackendError(
                "token=org-secret forbidden",
                status_code=403,
            ),
            "supersede",
            "remote_authorization_rejected",
        ),
        (
            HadesBackendError(
                "token=org-secret invalid",
                status_code=422,
            ),
            "supersede",
            "remote_validation_rejected",
        ),
        (
            HadesBackendError(
                "token=org-secret wrong identity",
                status_code=409,
            ),
            "supersede",
            "remote_identity_rejected",
        ),
    ],
)
def test_org_run_claim_uses_structured_secret_safe_outcome(
    tmp_path, failure, action, reason,
):
    class Client:
        def claim_agent_work_item(self, *_args, **_kwargs):
            raise failure

    conn = kb.connect(tmp_path / "kanban.db")
    try:
        plan = _org_plan()
        created = create_org_run(
            conn, plan, validate_execution_portfolio(plan),
        )
        execution_id = created.remote_tasks["HD-1"].execution_id
        outcome = claim_org_run_remote_task_outcome(
            conn,
            client=Client(),
            topology=created,
            remote_task_id="HD-1",
            local_workspace_id="lw-1",
        )
        link = kb.get_remote_link(conn, execution_id)
        assert outcome.action == action
        assert outcome.reason == reason
        assert "org-secret" not in outcome.reason
        assert "org-secret" not in (link.last_error or "")
        assert link.publication_policy == "org_run_gated"
    finally:
        conn.close()


def _ready_org_run_for_publish(conn, client, *, board: str):
    from tools.delegation_evidence import build_evidence_packet
    from hermes_cli.kanban_portfolio import import_remote_mandate, persist_org_run_contract, register_org_run_evidence
    plan = _org_plan()
    created = create_org_run(conn, plan, validate_execution_portfolio(plan), board=board)
    remote = created.remote_tasks["HD-1"]
    assert claim_org_run_remote_task(
        conn, client=client, topology=created, remote_task_id="HD-1", local_workspace_id="lw-1"
    )[0]
    for task_id in [
        remote.execution_id,
        remote.review_id,
        remote.integration_ready_id,
        created.integration_id,
        created.review_id,
        remote.completion_id,
    ]:
        assert kb.complete_task(conn, task_id, summary="verified")
    import_remote_mandate(conn, topology=created, remote_id="HD-1", version="1")
    contract = {"objective":"Implement","deliverable":"Result","in_scope":["src/a.py"],"out_of_scope":["backend"],"workspace":".","write_scope":["src/a.py"],"input_evidence":["mandate"],"dependencies":[],"acceptance_criteria":["tests"],"required_verification":["pytest"],"return_schema":["evidence"]}
    contract_hash = persist_org_run_contract(conn, topology=created, remote_id="HD-1", node_id=remote.execution_id, mandate_version="1", contract=contract)
    evidence_ref = register_org_run_evidence(
        conn, topology=created, remote_id="HD-1", node_id=remote.execution_id,
        mandate_version="1",
        packet=build_evidence_packet(
            contract_hash=contract_hash, base_commit="a" * 40, diff_hash="diff",
            result_ref="b" * 40, covered_files=["src/a.py"],
            verification=[{"command": "pytest", "passed": True}],
        ).to_dict(),
    )
    return plan, created, remote, evidence_ref


class _CompletionClient:
    def claim_agent_work_item(self, work_item_id, *, local_workspace_id):
        assert (work_item_id, local_workspace_id) == ("awi-1", "lw-1")
        return {"lease_token": "lease-1"}

    def complete_agent_work_item(self, work_item_id, *, lease_token, chat_message=None, memory_entry=None):
        self.completed = (work_item_id, lease_token, chat_message)
        return {}


def test_publish_uses_execution_lease_only_after_gate(monkeypatch):
    import hermes_cli.hades_coordination as coordination

    kb.create_board("target")
    kb.create_board("active")
    kb.set_current_board("active")
    conn = kb.connect(board="target")
    try:
        client = _CompletionClient()
        plan, created, _remote, evidence_ref = _ready_org_run_for_publish(
            conn, client, board="target",
        )
        resolver_calls = []
        monkeypatch.setattr(
            coordination,
            "resolve_kanban_backend_context",
            lambda **kwargs: (
                resolver_calls.append(kwargs)
                or KanbanBackendContext(
                    "linked", Path.cwd(), project_id="p", workspace_binding_id="binding-1",
                    local_workspace_id="lw-1", agent_id="agent-1",
                )
            ),
            raising=False,
        )
        selected_agents = []
        monkeypatch.setattr(
            coordination,
            "make_kanban_client",
            lambda context: selected_agents.append(context.agent_id) or client,
            raising=False,
        )
        assert publish_org_run_completion(
            conn,
            board="target",
            org_run_id=plan.org_run_id,
            topology=created,
            remote_task_id="HD-1",
            message="bounded result",
            evidence_refs=[evidence_ref],
        ) == (True, "published")
        assert client.completed == ("awi-1", "lease-1", "bounded result")
        assert resolver_calls == [{"board": "target"}]
        assert selected_agents == ["agent-1"]
    finally:
        conn.close()


def test_publish_rejects_live_workspace_binding_mismatch(monkeypatch):
    """Removing the live-binding check would publish through a different workspace agent."""
    import hermes_cli.hades_coordination as coordination

    kb.create_board("target")
    conn = kb.connect(board="target")
    try:
        client = _CompletionClient()
        plan, created, _remote, evidence_ref = _ready_org_run_for_publish(
            conn, client, board="target",
        )
        monkeypatch.setattr(
            coordination,
            "resolve_kanban_backend_context",
            lambda **_kwargs: KanbanBackendContext(
                "linked", Path.cwd(), project_id="p", workspace_binding_id="other-binding",
                local_workspace_id="other-workspace", agent_id="other-agent",
            ),
            raising=False,
        )
        assert publish_org_run_completion(
            conn,
            board="target",
            org_run_id=plan.org_run_id,
            topology=created,
            remote_task_id="HD-1",
            message="bounded result",
            evidence_refs=[evidence_ref],
        ) == (False, "remote binding does not match this workspace")
        assert not hasattr(client, "completed")
    finally:
        conn.close()


def test_publish_rejects_precreated_client_factory_bypass(monkeypatch):
    """A caller cannot substitute a client that was created for another agent."""
    import hermes_cli.hades_coordination as coordination

    kb.create_board("target")
    conn = kb.connect(board="target")
    try:
        client = _CompletionClient()
        plan, created, _remote, evidence_ref = _ready_org_run_for_publish(
            conn, client, board="target",
        )
        monkeypatch.setattr(
            coordination,
            "resolve_kanban_backend_context",
            lambda **_kwargs: KanbanBackendContext(
                "linked", Path.cwd(), project_id="p", workspace_binding_id="binding-1",
                local_workspace_id="lw-1", agent_id="agent-1",
            ),
            raising=False,
        )
        with pytest.raises(TypeError):
            publish_org_run_completion(
                conn,
                client_factory=lambda: client,
                org_run_id=plan.org_run_id,
                topology=created,
                remote_task_id="HD-1",
                message="bounded result",
                evidence_refs=[evidence_ref],
            )
        assert not hasattr(client, "completed")
    finally:
        conn.close()
