from __future__ import annotations

from hermes_cli import kanban_db as kb
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
