from __future__ import annotations

from hermes_cli import kanban_db as kb
from hermes_cli.hades_coordination import publish_org_run_proposal
from hermes_cli.hierarchical_execution import (
    parse_execution_portfolio,
    validate_execution_portfolio,
)
from hermes_cli.kanban_portfolio import (
    create_org_run,
    import_remote_mandate,
    reconcile_remote_mandate,
    register_org_run_evidence,
    require_current_org_run_evidence,
    accept_remote_mandate_reconciliation,
    persist_org_run_contract,
)


def _plan():
    return parse_execution_portfolio({
        "schema": "hades.execution-portfolio.v1",
        "org_run_id": "org-projection-1",
        "project_id": "project-uuid",
        "repository_id": "repo",
        "workspace_binding_id": "binding-1",
        "base_commit": "a" * 40,
        "tasks": [
            {"remote_task_id": "r1", "work_item_id": "w1", "title": "A", "body": "A", "assignee": "default", "priority": 2, "risk": "low", "depends_on": [], "write_scope": ["src/a.py"]},
            {"remote_task_id": "r2", "work_item_id": "w2", "title": "B", "body": "B", "assignee": "default", "priority": 1, "risk": "low", "depends_on": ["r1"], "write_scope": ["src/b.py"]},
            {"remote_task_id": "r3", "work_item_id": "w3", "title": "C", "body": "C", "assignee": "default", "priority": 1, "risk": "low", "depends_on": [], "write_scope": ["src/c.py"]},
        ],
    })


def _contract(version=1):
    return {"objective": "Implement task", "deliverable": "Verified result", "in_scope": ["src"],
            "out_of_scope": ["backend mutation"], "workspace": ".", "write_scope": ["src/**"],
            "input_evidence": ["mandate"], "dependencies": [], "acceptance_criteria": ["tests pass"],
            "required_verification": ["pytest"], "return_schema": ["evidence"],
            "task_version": version, "contract_version": version}


def _approved_row(conn, org, *, remote_id="r1", version="2", message_id="approval-1"):
    from hermes_cli.hades_persephone_messages import AGENT_MESSAGE_SCHEMA, parse_envelope
    from hermes_cli.hades_persephone_store import approve_request, record_inbox, transition_message
    envelope = parse_envelope({"schema": AGENT_MESSAGE_SCHEMA, "message_id": message_id,
        "correlation_id": message_id, "causation_id": None, "project_id": org.project_id,
        "sender_agent_id": "agent", "target_agent_id": "human-gate",
        "target_workspace_binding_id": None, "message_type": "local_decision", "effect": "mutating",
        "capability": "org_run_reconciliation", "remote_task_id": remote_id,
        "remote_task_version": version, "expires_at": 9999999999,
        "payload": {"action": "accept_org_run_mandate", "org_run_id": org.anchor_id,
                    "remote_id": remote_id, "mandate_version": version}}, now=100)
    record_inbox(conn, envelope, now=100)
    transition_message(conn, message_id, "waiting_human_approval", now=101)
    approve_request(conn, message_id, approved=True, decided_by="human:gabriele", now=102)
    return message_id


def test_remote_version_change_blocks_only_derived_subtree(tmp_path):
    conn = kb.connect(tmp_path / "kanban.db")
    try:
        plan = _plan()
        validation = validate_execution_portfolio(plan)
        org = create_org_run(conn, plan, validation)
        import_remote_mandate(conn, topology=org, remote_id="r1", version="1")

        result = reconcile_remote_mandate(
            conn, topology=org, dependencies=validation.ordered_dependencies,
            remote_id="r1", version="2",
        )

        assert result.status == "stale"
        assert result.previous_version == "1"
        assert set(result.affected_remote_ids) == {"r1", "r2"}
        assert all(kb.get_task(conn, node).status == "blocked" for node in result.affected_nodes)
        assert kb.get_task(conn, org.remote_tasks["r3"].execution_id).status != "blocked"
        assert result.evidence_valid is False
    finally:
        conn.close()


def test_same_remote_version_is_idempotent_and_does_not_block(tmp_path):
    conn = kb.connect(tmp_path / "kanban.db")
    try:
        plan = _plan(); validation = validate_execution_portfolio(plan)
        org = create_org_run(conn, plan, validation)
        import_remote_mandate(conn, topology=org, remote_id="r1", version="1")
        result = reconcile_remote_mandate(conn, topology=org, dependencies=validation.ordered_dependencies, remote_id="r1", version="1")
        assert result.status == "current"
        assert result.affected_nodes == ()
        assert result.evidence_valid is True
    finally:
        conn.close()


def test_version_change_invalidates_real_d4_packet_and_validator_rejects_it(tmp_path):
    import pytest
    from tools.delegation_evidence import build_evidence_packet
    conn = kb.connect(tmp_path / "kanban.db")
    try:
        plan = _plan(); validation = validate_execution_portfolio(plan)
        org = create_org_run(conn, plan, validation)
        import_remote_mandate(conn, topology=org, remote_id="r1", version="1")
        contract_hash = persist_org_run_contract(
            conn, topology=org, remote_id="r1", node_id=org.remote_tasks["r1"].execution_id,
            mandate_version="1", contract=_contract(), expected_contract_version=None,
        )
        packet = build_evidence_packet(
            contract_hash=contract_hash, base_commit="a" * 40, diff_hash="diff",
            result_ref="b" * 40, covered_files=["src/a.py"],
            verification=[{"command": "pytest", "passed": True}],
        ).to_dict()
        ref = register_org_run_evidence(
            conn, topology=org, remote_id="r1",
            node_id=org.remote_tasks["r1"].execution_id,
            mandate_version="1", packet=packet,
        )
        require_current_org_run_evidence(conn, topology=org, evidence_refs=[ref])
        reconcile_remote_mandate(conn, topology=org, dependencies=validation.ordered_dependencies, remote_id="r1", version="2")
        with pytest.raises(ValueError, match="stale OrgRun evidence rejected"):
            require_current_org_run_evidence(conn, topology=org, evidence_refs=[ref])
    finally:
        conn.close()


def test_reconciliation_requires_human_evidence_and_is_single_accept(tmp_path):
    import pytest
    conn = kb.connect(tmp_path / "kanban.db")
    try:
        plan = _plan(); validation = validate_execution_portfolio(plan)
        org = create_org_run(conn, plan, validation)
        import_remote_mandate(conn, topology=org, remote_id="r1", version="1")
        stale = reconcile_remote_mandate(conn, topology=org, dependencies=validation.ordered_dependencies, remote_id="r1", version="2")
        with pytest.raises(ValueError, match="guarded reconciliation"):
            import_remote_mandate(conn, topology=org, remote_id="r1", version="2")
        from hermes_cli import hades_backend_db
        approval_conn = hades_backend_db.connect(tmp_path / "approvals.db")
        for node_id in stale.affected_nodes:
            owner = next((candidate for candidate in stale.affected_remote_ids if node_id in {
                org.remote_tasks[candidate].execution_id, org.remote_tasks[candidate].review_id,
                org.remote_tasks[candidate].integration_ready_id, org.remote_tasks[candidate].completion_id,
            }), "r1")
            persist_org_run_contract(conn, topology=org, remote_id=owner, node_id=node_id,
                                     mandate_version="1", contract=_contract(1))
        replacements = {
                node_id: {"expected_contract_version": 1, "contract": _contract(2)}
                for node_id in stale.affected_nodes
        }
        with pytest.raises(ValueError, match="durable approval"):
            accept_remote_mandate_reconciliation(conn, topology=org, remote_id="r1", observed_version="2",
                                                 approval_conn=approval_conn, approval_message_id="forged",
                                                 replacement_contracts=replacements)
        approval_id = _approved_row(approval_conn, org)
        old_replacements = {
            node_id: {"expected_contract_version": 1, "contract": _contract(1)}
            for node_id in stale.affected_nodes
        }
        with pytest.raises(ValueError, match="increase monotonically"):
            accept_remote_mandate_reconciliation(
                conn, topology=org, remote_id="r1", observed_version="2",
                approval_conn=approval_conn, approval_message_id=approval_id,
                replacement_contracts=old_replacements,
            )
        assert conn.execute("SELECT COUNT(*) FROM hades_org_approval_consumptions").fetchone()[0] == 0
        assert all(kb.get_task(conn, node_id).status == "blocked" for node_id in stale.affected_nodes)
        accepted = accept_remote_mandate_reconciliation(
            conn, topology=org, remote_id="r1", observed_version="2",
            approval_conn=approval_conn, approval_message_id=approval_id,
            replacement_contracts=replacements,
        )
        assert accepted.status == "accepted"
        assert set(accepted.resumed_nodes) == set(stale.affected_nodes)
        assert {org.integration_id, org.review_id, org.synthesis_id}.issubset(accepted.resumed_nodes)
        assert all(kb.get_task(conn, node_id).status in {"ready", "todo"} for node_id in stale.affected_nodes)
        with pytest.raises(ValueError, match="not awaiting"):
            accept_remote_mandate_reconciliation(
                conn, topology=org, remote_id="r1", observed_version="2",
                approval_conn=approval_conn, approval_message_id=approval_id,
                replacement_contracts=replacements,
            )
        approval_conn.close()
    finally:
        conn.close()


def test_contract_cas_is_monotonic_across_connections(tmp_path):
    import pytest
    path = tmp_path / "kanban.db"
    first = kb.connect(path)
    plan = _plan(); org = create_org_run(first, plan, validate_execution_portfolio(plan))
    node = org.remote_tasks["r1"].execution_id
    persist_org_run_contract(first, topology=org, remote_id="r1", node_id=node,
                             mandate_version="1", contract=_contract(1))
    second = kb.connect(path)
    persist_org_run_contract(first, topology=org, remote_id="r1", node_id=node,
                             mandate_version="2", contract=_contract(2), expected_contract_version=1)
    with pytest.raises(ValueError, match="CAS failed"):
        persist_org_run_contract(second, topology=org, remote_id="r1", node_id=node,
                                 mandate_version="2", contract=_contract(3), expected_contract_version=1)
    with pytest.raises(ValueError, match="monotonically"):
        persist_org_run_contract(second, topology=org, remote_id="r1", node_id=node,
                                 mandate_version="2", contract=_contract(2), expected_contract_version=2)
    first.close(); second.close()


def test_publish_is_durable_project_scoped_and_idempotent(tmp_path):
    from hermes_cli import hades_backend_db
    from hermes_cli.hades_persephone_store import get_message
    from hermes_cli.hades_persephone_transport import send_due_messages
    class Client:
        def __init__(self): self.messages = []
        def create_inbox_message(self, **payload): self.messages.append(payload); return {"ok": True}
        def update_project_manager_card(self, *args, **kwargs): raise AssertionError("remote card mutation forbidden")

    plan = _plan()
    kanban = kb.connect(tmp_path / "kanban.db")
    org = create_org_run(kanban, plan, validate_execution_portfolio(plan))
    outbox = hades_backend_db.connect(tmp_path / "backend.db")
    client = Client()
    first = publish_org_run_proposal(
        outbox_conn=outbox, org_conn=kanban, topology=org, sender_agent_id="agent-a",
        target_agent_id="agent-pm", remote_task_id="r1", remote_task_version="2",
        proposal_type="decision_proposal", summary="Mandate changed; reconcile local scope.",
        evidence_refs=[], idempotency_key="projection:r1:2",
        now=1_000,
    )
    second = publish_org_run_proposal(
        outbox_conn=outbox, org_conn=kanban, topology=org, sender_agent_id="agent-a",
        target_agent_id="agent-pm", remote_task_id="r1", remote_task_version="2",
        proposal_type="decision_proposal", summary="Mandate changed; reconcile local scope.",
        evidence_refs=[], idempotency_key="projection:r1:2",
        now=1_000,
    )
    assert first == second
    assert get_message(outbox, first, queue="outbox").state == "outbox_pending"
    assert client.messages == []
    assert send_due_messages(outbox, client, now=1_000, project_id=org.project_id, sender_agent_id="agent-a")["sent"] == 1
    assert len(client.messages) == 1
    envelope = client.messages[0]
    assert envelope["project_id"] == "project-uuid"
    assert envelope["effect"] == "information_read"
    assert envelope["message_type"] == "local_decision"
    assert envelope["payload"]["proposal_type"] == "decision_proposal"
    assert envelope["payload"]["evidence_refs"] == []
    kanban.close(); outbox.close()


def test_proposal_rejects_cross_project_before_outbox_persistence(tmp_path):
    from hermes_cli import hades_backend_db
    plan = _plan(); conn = kb.connect(tmp_path / "kanban.db")
    org = create_org_run(conn, plan, validate_execution_portfolio(plan))
    outbox = hades_backend_db.connect(tmp_path / "backend.db")
    import pytest
    with pytest.raises(ValueError, match="authoritative OrgRun project"):
        publish_org_run_proposal(
            outbox_conn=outbox, org_conn=conn, topology=org, expected_project_id="other-project",
            sender_agent_id="agent-a", target_agent_id="agent-pm", remote_task_id="r1",
            remote_task_version="2", proposal_type="clarification", summary="Question",
            idempotency_key="cross-project", now=1_000,
        )
    assert outbox.execute("SELECT COUNT(*) FROM persephone_outbox").fetchone()[0] == 0
    conn.close(); outbox.close()


def test_proposal_bounds_and_offline_restart_recovery(tmp_path):
    import pytest
    from hermes_cli import hades_backend_db
    from hermes_cli.hades_backend_client import HadesBackendError
    from hermes_cli.hades_persephone_store import get_message
    from hermes_cli.hades_persephone_transport import RetryPolicy, send_due_messages
    plan = _plan(); kanban = kb.connect(tmp_path / "kanban.db")
    org = create_org_run(kanban, plan, validate_execution_portfolio(plan))
    path = tmp_path / "backend.db"; outbox = hades_backend_db.connect(path)
    common = dict(
        outbox_conn=outbox, org_conn=kanban, topology=org, sender_agent_id="agent-a",
        target_agent_id="agent-pm", remote_task_id="r1", remote_task_version="2",
        proposal_type="clarification", summary="Need clarification", now=1_000,
    )
    with pytest.raises(ValueError, match="16 items"):
        publish_org_run_proposal(**common, evidence_refs=[f"packet:{i}" for i in range(17)], idempotency_key="too-many")
    with pytest.raises(ValueError, match="payload exceeds"):
        publish_org_run_proposal(**common, evidence_refs=["x" * 66_000], idempotency_key="too-large")
    message_id = publish_org_run_proposal(**common, idempotency_key="offline-recover")
    class Offline:
        def create_inbox_message(self, **payload): raise HadesBackendError("offline", status_code=503)
    result = send_due_messages(outbox, Offline(), now=1_000, retry=RetryPolicy(base=1, maximum=1, jitter=0))
    assert result["retry"] == 1
    outbox.close()
    reopened = hades_backend_db.connect(path)
    assert get_message(reopened, message_id, queue="outbox").state == "retry"
    class Online:
        def __init__(self): self.sent = []
        def create_inbox_message(self, **payload): self.sent.append(payload)
    online = Online()
    assert send_due_messages(reopened, online, now=1_001)["sent"] == 1
    assert online.sent[0]["project_id"] == org.project_id
    reopened.close(); kanban.close()


def test_projection_sync_off_does_no_remote_work(tmp_path):
    from hermes_cli.hades_kanban_sync import sync_remote_mandates
    class Client:
        def list_agent_work_items(self, **kwargs): raise AssertionError("network forbidden")
    conn = kb.connect(tmp_path / "kanban.db")
    try:
        plan = _plan(); org = create_org_run(conn, plan, validate_execution_portfolio(plan))
        result = sync_remote_mandates(conn, Client(), topology=org, mode="off")
        assert result.mode == "off"
        assert result.cursor is None
    finally:
        conn.close()


def test_projection_cursor_and_offline_status_are_durable(tmp_path):
    from hermes_cli.hades_kanban_sync import sync_remote_mandates
    from hermes_cli.kanban_swarm import latest_blackboard
    class Client:
        def __init__(self): self.cursors = []
        def list_agent_work_items(self, **kwargs):
            self.cursors.append(kwargs.get("cursor"))
            if len(self.cursors) == 1:
                return {"items": [], "next_cursor": "cursor-1"}
            raise OSError("offline")
    conn = kb.connect(tmp_path / "kanban.db")
    try:
        plan = _plan(); org = create_org_run(conn, plan, validate_execution_portfolio(plan))
        client = Client()
        first = sync_remote_mandates(conn, client, topology=org, mode="pull_only")
        second = sync_remote_mandates(conn, client, topology=org, mode="pull_only")
        assert first.cursor == "cursor-1"
        assert second.status == "offline" and second.cursor == "cursor-1"
        assert client.cursors == [None, "cursor-1"]
        assert latest_blackboard(conn, org.anchor_id)["remote_projection_sync"]["status"] == "offline"
    finally:
        conn.close()


def test_projection_rejects_cross_project_page_without_cursor_advance(tmp_path):
    from hermes_cli.hades_kanban_sync import sync_remote_mandates
    from hermes_cli.kanban_swarm import latest_blackboard
    class Client:
        def list_agent_work_items(self, **kwargs):
            return {"items": [{"id": "evil", "project_id": "other-project"}], "next_cursor": "evil-cursor"}
    conn = kb.connect(tmp_path / "kanban.db")
    try:
        plan = _plan(); org = create_org_run(conn, plan, validate_execution_portfolio(plan))
        result = sync_remote_mandates(conn, Client(), topology=org, mode="pull_only", cursor="safe-cursor")
        assert result.status == "rejected_page" and result.observed == 0
        assert result.cursor == "safe-cursor"
        stored = latest_blackboard(conn, org.anchor_id)["remote_projection_sync"]
        assert stored["cursor"] == "safe-cursor" and stored["status"] == "rejected_page"
    finally:
        conn.close()


def test_evidence_rejects_wrong_version_hash_and_cross_node(tmp_path):
    import pytest
    from tools.delegation_evidence import build_evidence_packet
    conn = kb.connect(tmp_path / "kanban.db")
    try:
        plan = _plan(); org = create_org_run(conn, plan, validate_execution_portfolio(plan))
        import_remote_mandate(conn, topology=org, remote_id="r1", version="1")
        node = org.remote_tasks["r1"].execution_id
        digest = persist_org_run_contract(conn, topology=org, remote_id="r1", node_id=node,
                                          mandate_version="1", contract=_contract())
        packet = build_evidence_packet(contract_hash=digest, base_commit="a"*40, diff_hash="d",
                                       covered_files=["src/a.py"], verification=[]).to_dict()
        with pytest.raises(ValueError, match="currently accepted"):
            register_org_run_evidence(conn, topology=org, remote_id="r1", node_id=node,
                                      mandate_version="2", packet=packet)
        bad = {**packet, "contract_hash": "forged"}
        with pytest.raises(ValueError, match="contract_hash"):
            register_org_run_evidence(conn, topology=org, remote_id="r1", node_id=node,
                                      mandate_version="1", packet=bad)
        with pytest.raises(ValueError, match="matching persisted node contract"):
            register_org_run_evidence(conn, topology=org, remote_id="r1",
                                      node_id=org.remote_tasks["r1"].review_id,
                                      mandate_version="1", packet=packet)
    finally:
        conn.close()
