"""Materialize execution portfolios on the existing Kanban board."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import sqlite3
import hashlib
import json

from hermes_cli import kanban_db as kb
from hermes_cli.hierarchical_execution import ExecutionPortfolio, PortfolioValidation
from hermes_cli.kanban_swarm import latest_blackboard, post_blackboard_update


@dataclass(frozen=True)
class RemoteTaskTopology:
    anchor_id: str
    execution_id: str
    review_id: str
    integration_ready_id: str
    completion_id: str
    work_item_id: str = ""


@dataclass(frozen=True)
class OrgRunCreated:
    anchor_id: str
    remote_tasks: dict[str, RemoteTaskTopology]
    integration_id: str
    review_id: str
    synthesis_id: str
    project_id: str = ""


@dataclass(frozen=True)
class RemoteMandateReconciliation:
    """Outcome of comparing one local projection with its remote mandate."""

    remote_id: str
    previous_version: str | None
    observed_version: str
    status: str
    affected_remote_ids: tuple[str, ...] = ()
    affected_nodes: tuple[str, ...] = ()
    evidence_valid: bool = True


@dataclass(frozen=True)
class MandateAcceptance:
    status: str
    remote_id: str
    version: str
    resumed_nodes: tuple[str, ...] = ()


def _ensure_projection_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS hades_org_evidence (
            evidence_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
            anchor_id TEXT NOT NULL, remote_id TEXT NOT NULL, node_id TEXT NOT NULL,
            packet TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('current','stale')),
            mandate_version TEXT NOT NULL, updated_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_hades_org_evidence_scope
            ON hades_org_evidence(anchor_id, remote_id, state);
        CREATE TABLE IF NOT EXISTS hades_org_contracts (
            project_id TEXT NOT NULL, anchor_id TEXT NOT NULL, remote_id TEXT NOT NULL,
            node_id TEXT NOT NULL, mandate_version TEXT NOT NULL,
            contract_version INTEGER NOT NULL, contract_hash TEXT NOT NULL,
            contract TEXT NOT NULL, PRIMARY KEY(project_id, anchor_id, node_id)
        );
    """)


def persist_org_run_contract(
    conn: sqlite3.Connection, *, topology: OrgRunCreated, remote_id: str,
    node_id: str, mandate_version: str, contract: dict,
    expected_contract_version: int | None = None,
) -> str:
    """Install one canonical parsed TaskContract with version-CAS semantics."""
    from dataclasses import asdict
    from tools.delegation_contract import parse_orchestrator_contract
    if remote_id not in topology.remote_tasks or not topology.project_id:
        raise ValueError("contract is outside authoritative OrgRun topology")
    parsed = parse_orchestrator_contract(contract)
    canonical = json.dumps(asdict(parsed), sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    _ensure_projection_tables(conn)
    current = conn.execute(
        "SELECT contract_version FROM hades_org_contracts WHERE project_id=? AND anchor_id=? AND node_id=?",
        (topology.project_id, topology.anchor_id, node_id),
    ).fetchone()
    current_version = int(current["contract_version"]) if current else None
    if expected_contract_version != current_version:
        raise ValueError("contract version CAS failed")
    conn.execute(
        "INSERT INTO hades_org_contracts VALUES(?,?,?,?,?,?,?,?) "
        "ON CONFLICT(project_id,anchor_id,node_id) DO UPDATE SET remote_id=excluded.remote_id, "
        "mandate_version=excluded.mandate_version, contract_version=excluded.contract_version, "
        "contract_hash=excluded.contract_hash, contract=excluded.contract",
        (topology.project_id, topology.anchor_id, remote_id, node_id, str(mandate_version),
         parsed.contract_version, digest, canonical),
    )
    conn.commit()
    return digest


def register_org_run_evidence(
    conn: sqlite3.Connection, *, topology: OrgRunCreated, remote_id: str,
    node_id: str, mandate_version: str, packet: dict,
) -> str:
    """Persist a validated D4 packet with immutable OrgRun provenance."""
    if not topology.project_id or remote_id not in topology.remote_tasks:
        raise ValueError("evidence is outside the authoritative OrgRun project/topology")
    remote = topology.remote_tasks[remote_id]
    allowed = {remote.execution_id, remote.review_id, remote.integration_ready_id,
               remote.completion_id, topology.integration_id, topology.review_id,
               topology.synthesis_id}
    if node_id not in allowed:
        raise ValueError("evidence node is outside the mandate-derived subtree")
    from tools.delegation_evidence import validate_evidence_packet
    validate_evidence_packet(packet)
    _ensure_projection_tables(conn)
    mandate = _mandates(conn, topology).get(remote_id, {})
    if str(mandate.get("version") or "") != str(mandate_version) or mandate.get("status") not in {"current", "accepted"}:
        raise ValueError("evidence mandate version is not currently accepted")
    contract = conn.execute(
        "SELECT remote_id, mandate_version, contract_hash FROM hades_org_contracts "
        "WHERE project_id=? AND anchor_id=? AND node_id=?",
        (topology.project_id, topology.anchor_id, node_id),
    ).fetchone()
    if contract is None or contract["remote_id"] != remote_id or contract["mandate_version"] != str(mandate_version):
        raise ValueError("evidence has no matching persisted node contract")
    if packet.get("contract_hash") != contract["contract_hash"]:
        raise ValueError("evidence contract_hash does not match persisted contract")
    encoded = json.dumps(packet, sort_keys=True, separators=(",", ":"), allow_nan=False)
    evidence_id = "packet:" + hashlib.sha256(
        f"{topology.project_id}:{topology.anchor_id}:{remote_id}:{node_id}:{mandate_version}:{encoded}".encode()
    ).hexdigest()
    conn.execute(
        "INSERT OR IGNORE INTO hades_org_evidence VALUES (?, ?, ?, ?, ?, ?, 'current', ?, strftime('%s','now'))",
        (evidence_id, topology.project_id, topology.anchor_id, remote_id, node_id,
         encoded, str(mandate_version)),
    )
    conn.commit()
    return evidence_id


def require_current_org_run_evidence(
    conn: sqlite3.Connection, *, topology: OrgRunCreated, evidence_refs: tuple[str, ...] | list[str],
    remote_id: str | None = None, mandate_version: str | None = None,
) -> None:
    _ensure_projection_tables(conn)
    for evidence_id in evidence_refs:
        row = conn.execute(
            "SELECT e.project_id,e.anchor_id,e.remote_id,e.node_id,e.state,e.mandate_version,"
            "json_extract(e.packet,'$.contract_hash') AS packet_contract,c.contract_hash,c.mandate_version AS contract_mandate "
            "FROM hades_org_evidence e LEFT JOIN hades_org_contracts c ON c.project_id=e.project_id "
            "AND c.anchor_id=e.anchor_id AND c.node_id=e.node_id WHERE e.evidence_id = ?",
            (str(evidence_id),),
        ).fetchone()
        if row is None or row["project_id"] != topology.project_id or row["anchor_id"] != topology.anchor_id:
            raise ValueError(f"unknown or cross-project OrgRun evidence: {evidence_id}")
        if row["state"] != "current":
            raise ValueError(f"stale OrgRun evidence rejected: {evidence_id}")
        mandate = _mandates(conn, topology).get(row["remote_id"], {})
        if (str(mandate.get("version") or "") != row["mandate_version"]
                or row["contract_mandate"] != row["mandate_version"]
                or row["packet_contract"] != row["contract_hash"]):
            raise ValueError(f"obsolete OrgRun evidence rejected: {evidence_id}")
        if remote_id is not None and row["remote_id"] != remote_id:
            raise ValueError(f"cross-mandate OrgRun evidence rejected: {evidence_id}")
        if mandate_version is not None and row["mandate_version"] != str(mandate_version):
            raise ValueError(f"wrong-version OrgRun evidence rejected: {evidence_id}")


def _mandates(conn: sqlite3.Connection, topology: OrgRunCreated) -> dict[str, dict]:
    raw = latest_blackboard(conn, topology.anchor_id).get("remote_mandates", {})
    if not isinstance(raw, dict):
        return {}
    return {
        str(key): dict(value)
        for key, value in raw.items()
        if isinstance(key, str) and isinstance(value, dict)
    }


def import_remote_mandate(
    conn: sqlite3.Connection,
    *,
    topology: OrgRunCreated,
    remote_id: str,
    version: str,
    author: str = "hades-backend-sync",
) -> None:
    """Record an explicitly accepted remote ID/version as projection metadata."""
    remote_id = str(remote_id).strip()
    version = str(version).strip()
    if remote_id not in topology.remote_tasks:
        raise ValueError(f"unknown remote mandate: {remote_id}")
    if not version:
        raise ValueError("remote mandate version is required")
    mandates = _mandates(conn, topology)
    mandates[remote_id] = {"version": version, "status": "current", "evidence_valid": True}
    post_blackboard_update(
        conn, topology.anchor_id, author=author, key="remote_mandates", value=mandates
    )


def reconcile_remote_mandate(
    conn: sqlite3.Connection,
    *,
    topology: OrgRunCreated,
    dependencies: dict[str, tuple[str, ...]],
    remote_id: str,
    version: str,
    author: str = "hades-backend-sync",
) -> RemoteMandateReconciliation:
    """Pause only the local subtree derived from a changed remote mandate.

    This never accepts the new version.  A human/local reconciliation must call
    :func:`import_remote_mandate` after reviewing the changed natural-language
    mandate and rebuilding any affected task contracts.
    """
    remote_id = str(remote_id).strip()
    version = str(version).strip()
    if remote_id not in topology.remote_tasks:
        raise ValueError(f"unknown remote mandate: {remote_id}")
    if not version:
        raise ValueError("remote mandate version is required")
    mandates = _mandates(conn, topology)
    previous = mandates.get(remote_id, {}).get("version")
    if previous is None:
        import_remote_mandate(
            conn, topology=topology, remote_id=remote_id, version=version, author=author
        )
        return RemoteMandateReconciliation(remote_id, None, version, "imported")
    if str(previous) == version:
        return RemoteMandateReconciliation(remote_id, str(previous), version, "current")

    affected = {remote_id}
    changed = True
    while changed:
        changed = False
        for candidate, parents in dependencies.items():
            if candidate not in affected and any(parent in affected for parent in parents):
                affected.add(candidate)
                changed = True
    node_ids: list[str] = []
    for candidate in sorted(affected):
        remote = topology.remote_tasks[candidate]
        node_ids.extend((remote.execution_id, remote.review_id, remote.integration_ready_id, remote.completion_id))
    # Integration products depend on every projected remote task.
    node_ids.extend((topology.integration_id, topology.review_id, topology.synthesis_id))
    _ensure_projection_tables(conn)
    placeholders = ",".join("?" for _ in affected)
    conn.execute(
        f"UPDATE hades_org_evidence SET state = 'stale', updated_at = strftime('%s','now') "
        f"WHERE project_id = ? AND anchor_id = ? AND remote_id IN ({placeholders})",
        (topology.project_id, topology.anchor_id, *sorted(affected)),
    )
    projection_blocked: list[str] = []
    for task_id in node_ids:
        task = kb.get_task(conn, task_id)
        if task is not None and task.status not in {"done", "archived", "blocked"}:
            conn.execute(
                "UPDATE tasks SET status = 'blocked', block_kind = 'needs_input' WHERE id = ?",
                (task_id,),
            )
            projection_blocked.append(task_id)
    conn.commit()
    mandates[remote_id] = {
        "version": str(previous), "observed_version": version,
        "status": "awaiting_human", "evidence_valid": False,
    }
    post_blackboard_update(conn, topology.anchor_id, author=author, key="remote_mandates", value=mandates)
    post_blackboard_update(
        conn, topology.anchor_id, author=author, key=f"stale_projection:{remote_id}",
        value={"schema": "hades.remote-projection-stale.v1", "remote_id": remote_id,
               "accepted_version": str(previous), "observed_version": version,
               "affected_remote_ids": sorted(affected), "evidence_valid": False,
               "affected_nodes": node_ids, "projection_blocked_nodes": projection_blocked,
               "requires_human_reconciliation": True},
    )
    return RemoteMandateReconciliation(
        remote_id, str(previous), version, "stale", tuple(sorted(affected)),
        tuple(node_ids), False,
    )


def accept_remote_mandate_reconciliation(
    conn: sqlite3.Connection, *, topology: OrgRunCreated, remote_id: str,
    observed_version: str, approval: dict,
) -> MandateAcceptance:
    """Atomically accept a stale mandate only with explicit human evidence."""
    if not isinstance(approval, dict) or approval.get("decision") != "accepted":
        raise ValueError("accepted human approval is required")
    approver = str(approval.get("approved_by") or "").strip()
    evidence_ref = str(approval.get("evidence_ref") or "").strip()
    if not approver or not evidence_ref:
        raise ValueError("human approval requires approved_by and evidence_ref")
    remote_id = str(remote_id).strip(); observed_version = str(observed_version).strip()
    from hermes_cli.kanban_swarm import BLACKBOARD_PREFIX
    _ensure_projection_tables(conn)
    with kb.write_txn(conn):
        mandates = _mandates(conn, topology)
        state = mandates.get(remote_id, {})
        if state.get("status") != "awaiting_human" or state.get("observed_version") != observed_version:
            raise ValueError("mandate is not awaiting this human reconciliation")
        stale = latest_blackboard(conn, topology.anchor_id).get(f"stale_projection:{remote_id}")
        affected_remote = tuple(stale.get("affected_remote_ids", ())) if isinstance(stale, dict) else (remote_id,)
        affected_nodes = tuple(stale.get("affected_nodes", ())) if isinstance(stale, dict) else ()
        projection_blocked = set(stale.get("projection_blocked_nodes", ())) if isinstance(stale, dict) else set()
        contracts = approval.get("replacement_contracts")
        if not isinstance(contracts, dict) or set(contracts) != set(affected_nodes):
            raise ValueError("replacement_contracts must cover every affected node")
        from dataclasses import asdict
        from tools.delegation_contract import parse_orchestrator_contract
        normalized_contracts: dict[str, dict[str, str | int]] = {}
        for node_id in affected_nodes:
            replacement = contracts[node_id]
            if not isinstance(replacement, dict) or not isinstance(replacement.get("contract"), dict):
                raise ValueError("each replacement must carry a canonical TaskContract")
            expected = replacement.get("expected_contract_version")
            current = conn.execute(
                "SELECT contract_version FROM hades_org_contracts WHERE project_id=? AND anchor_id=? AND node_id=?",
                (topology.project_id, topology.anchor_id, node_id),
            ).fetchone()
            current_version = int(current["contract_version"]) if current else None
            if expected != current_version:
                raise ValueError("replacement contract version CAS failed")
            parsed = parse_orchestrator_contract(replacement["contract"])
            canonical = json.dumps(asdict(parsed), sort_keys=True, separators=(",", ":"))
            digest = hashlib.sha256(canonical.encode()).hexdigest()
            owner = next((candidate for candidate in affected_remote if node_id in {
                topology.remote_tasks[candidate].execution_id, topology.remote_tasks[candidate].review_id,
                topology.remote_tasks[candidate].integration_ready_id, topology.remote_tasks[candidate].completion_id,
            }), remote_id)
            conn.execute(
                "INSERT INTO hades_org_contracts VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(project_id,anchor_id,node_id) "
                "DO UPDATE SET remote_id=excluded.remote_id,mandate_version=excluded.mandate_version,"
                "contract_version=excluded.contract_version,contract_hash=excluded.contract_hash,contract=excluded.contract",
                (topology.project_id, topology.anchor_id, owner, node_id, observed_version,
                 parsed.contract_version, digest, canonical),
            )
            normalized_contracts[node_id] = {"contract_hash": digest, "mandate_version": observed_version,
                                              "contract_version": parsed.contract_version,
                                              "evidence_required": "regenerate"}
        resumed: list[str] = []
        for node_id in affected_nodes:
            if node_id in projection_blocked:
                row = conn.execute("SELECT status FROM tasks WHERE id = ?", (node_id,)).fetchone()
                if row is not None and row["status"] == "blocked":
                    parents = conn.execute(
                        "SELECT p.status FROM task_links l JOIN tasks p ON p.id=l.parent_id WHERE l.child_id=?",
                        (node_id,),
                    ).fetchall()
                    status = "ready" if all(p["status"] in {"done", "archived"} for p in parents) else "todo"
                    conn.execute("UPDATE tasks SET status=?, block_kind=NULL WHERE id=?", (status, node_id))
                    resumed.append(node_id)
        mandates[remote_id] = {
            "version": observed_version, "status": "accepted", "evidence_valid": False,
            "approval": {"approved_by": approver, "evidence_ref": evidence_ref},
        }
        now = int(__import__("time").time())
        for key, value in (
            ("remote_mandates", mandates),
            (f"stale_projection:{remote_id}", {
                "schema": "hades.remote-projection-stale.v1", "remote_id": remote_id,
                "status": "accepted", "accepted_version": observed_version,
                "approval_evidence_ref": evidence_ref, "evidence_valid": False,
                "requires_evidence_regeneration": True,
            }),
            (f"reconciled_contracts:{remote_id}", normalized_contracts),
        ):
            body = BLACKBOARD_PREFIX + json.dumps({"key": key, "value": value}, sort_keys=True)
            conn.execute(
                "INSERT INTO task_comments(task_id,author,body,created_at) VALUES(?,?,?,?)",
                (topology.anchor_id, approver, body, now),
            )
    return MandateAcceptance("accepted", remote_id, observed_version, tuple(resumed))


def _protocol(org_run_id: str, remote_task_id: str, write_scope: tuple[str, ...]) -> str:
    scope = ", ".join(write_scope) or "(read-only)"
    return (
        "\n\n## OrgRun protocol\n"
        f"- OrgRun: `{org_run_id}`.\n"
        f"- Remote task: `{remote_task_id}`.\n"
        f"- Declared write scope: `{scope}`.\n"
        "- Stay inside declared write scope.\n"
        "- Complete with structured evidence or block with a typed reason.\n"
        "- Do not publish memory or contact the backend directly.\n"
    )


def _topology_from_blackboard(
    conn: sqlite3.Connection, anchor_id: str
) -> OrgRunCreated | None:
    blackboard = latest_blackboard(conn, anchor_id)
    value = blackboard.get("topology")
    if not isinstance(value, dict):
        return None
    try:
        remote_tasks = {
            remote_id: RemoteTaskTopology(**raw)
            for remote_id, raw in value["remote_tasks"].items()
        }
        return OrgRunCreated(
            anchor_id=str(value["anchor_id"]),
            remote_tasks=remote_tasks,
            integration_id=str(value["integration_id"]),
            review_id=str(value["review_id"]),
            synthesis_id=str(value["synthesis_id"]),
            project_id=str(value.get("project_id") or (blackboard.get("portfolio") or {}).get("project_id") or ""),
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def _create_or_complete_anchor(
    conn: sqlite3.Connection,
    *,
    title: str,
    body: str,
    idempotency_key: str,
    created_by: str,
    parents: list[str] | None = None,
) -> str:
    task_id = kb.create_task(
        conn,
        title=title,
        body=body,
        assignee=created_by,
        created_by=created_by,
        parents=parents or [],
        idempotency_key=idempotency_key,
    )
    task = kb.get_task(conn, task_id)
    if task is not None and task.status != "done":
        kb.complete_task(
            conn,
            task_id,
            summary="OrgRun topology anchor created.",
            metadata={"kind": "hades_org_anchor_v1"},
        )
    return task_id


def create_org_run(
    conn: sqlite3.Connection,
    plan: ExecutionPortfolio,
    validation: PortfolioValidation,
    *,
    board: str | None = None,
    activate: bool = True,
) -> OrgRunCreated:
    """Create or recover an idempotent OrgRun graph."""
    if plan.org_run_id == "":
        raise ValueError("org_run_id is required")
    created_by = "org-orchestrator"
    anchor_id = kb.create_task(
        conn,
        title=f"OrgRun: {plan.org_run_id}",
        body=(
            "Durable Hades OrgRun anchor and bounded blackboard.\n\n"
            f"Project: {plan.project_id}\nRepository: {plan.repository_id}\n"
            f"Base commit: {plan.base_commit}"
        ),
        assignee=created_by,
        created_by=created_by,
        idempotency_key=f"org-run:{plan.org_run_id}:anchor",
        board=board,
    )
    existing = _topology_from_blackboard(conn, anchor_id)
    if existing is not None:
        return existing
    anchor = kb.get_task(conn, anchor_id)
    if anchor is not None and anchor.status != "done":
        kb.complete_task(
            conn,
            anchor_id,
            summary="OrgRun portfolio accepted for local materialization.",
            metadata={
                "kind": "hades_org_run_v1",
                "org_run_id": plan.org_run_id,
                "base_commit": plan.base_commit,
            },
        )

    remote_anchors: dict[str, str] = {}
    for task in plan.tasks:
        remote_anchor = _create_or_complete_anchor(
            conn,
            title=f"Remote anchor: {task.remote_task_id}",
            body=task.body,
            idempotency_key=f"org-run:{plan.org_run_id}:{task.remote_task_id}:anchor",
            created_by=created_by,
            parents=[anchor_id],
        )
        remote_anchors[task.remote_task_id] = remote_anchor

    execution_ids: dict[str, str] = {}
    review_ids: dict[str, str] = {}
    integration_ready_ids: dict[str, str] = {}
    for task in plan.tasks:
        execution_ids[task.remote_task_id] = kb.create_task(
            conn,
            title=f"Execute: {task.title}",
            body=task.body + _protocol(plan.org_run_id, task.remote_task_id, task.write_scope),
            assignee=task.assignee,
            created_by=created_by,
            parents=[remote_anchors[task.remote_task_id]],
            priority=task.priority,
            triage=not activate,
            idempotency_key=f"org-run:{plan.org_run_id}:{task.remote_task_id}:execute",
            board=board,
        )
        review_ids[task.remote_task_id] = kb.create_task(
            conn,
            title=f"Review: {task.title}",
            body=(
                "Review the implementation evidence, changed files, scope and focused tests.\n"
                + _protocol(plan.org_run_id, task.remote_task_id, task.write_scope)
            ),
            assignee="default",
            created_by=created_by,
            parents=[execution_ids[task.remote_task_id]],
            priority=task.priority,
            skills=["requesting-code-review"],
            idempotency_key=f"org-run:{plan.org_run_id}:{task.remote_task_id}:review",
            board=board,
        )
        integration_ready_ids[task.remote_task_id] = kb.create_task(
            conn,
            title=f"Ready for integration: {task.title}",
            body="Validate that this task has supplied complete integration evidence.",
            assignee="default",
            created_by=created_by,
            parents=[review_ids[task.remote_task_id]],
            priority=task.priority,
            idempotency_key=f"org-run:{plan.org_run_id}:{task.remote_task_id}:ready",
            board=board,
        )

    for task_id, parents in validation.ordered_dependencies.items():
        for parent_id in parents:
            kb.link_tasks(
                conn,
                integration_ready_ids[parent_id],
                execution_ids[task_id],
            )

    integration_id = kb.create_task(
        conn,
        title=f"Integrate OrgRun {plan.org_run_id}",
        body="Apply accepted patches in dependency order and run focused plus global tests.",
        assignee="default",
        created_by=created_by,
        parents=list(integration_ready_ids.values()),
        idempotency_key=f"org-run:{plan.org_run_id}:integration",
        board=board,
    )
    review_id = kb.create_task(
        conn,
        title=f"Review integrated OrgRun {plan.org_run_id}",
        body="Independently verify the integrated worktree, acceptance criteria and regression suite.",
        assignee="default",
        created_by=created_by,
        parents=[integration_id],
        skills=["requesting-code-review"],
        idempotency_key=f"org-run:{plan.org_run_id}:org-review",
        board=board,
    )

    completion_ids: dict[str, str] = {}
    for task in plan.tasks:
        completion_ids[task.remote_task_id] = kb.create_task(
            conn,
            title=f"Publish result: {task.remote_task_id}",
            body="Publish bounded completion evidence only after global integration review passes.",
            assignee="default",
            created_by=created_by,
            parents=[review_id],
            priority=task.priority,
            idempotency_key=f"org-run:{plan.org_run_id}:{task.remote_task_id}:complete",
            board=board,
        )
    synthesis_id = kb.create_task(
        conn,
        title=f"Synthesize OrgRun {plan.org_run_id}",
        body="Summarize verified outcomes, residual risks and backend-facing bounded evidence.",
        assignee="default",
        created_by=created_by,
        parents=list(completion_ids.values()),
        idempotency_key=f"org-run:{plan.org_run_id}:synthesis",
        board=board,
    )

    created = OrgRunCreated(
        anchor_id=anchor_id,
        remote_tasks={
            task.remote_task_id: RemoteTaskTopology(
                anchor_id=remote_anchors[task.remote_task_id],
                execution_id=execution_ids[task.remote_task_id],
                review_id=review_ids[task.remote_task_id],
                integration_ready_id=integration_ready_ids[task.remote_task_id],
                completion_id=completion_ids[task.remote_task_id],
                work_item_id=task.work_item_id,
            )
            for task in plan.tasks
        },
        integration_id=integration_id,
        review_id=review_id,
        synthesis_id=synthesis_id,
        project_id=plan.project_id,
    )
    post_blackboard_update(
        conn,
        anchor_id,
        author=created_by,
        key="portfolio",
        value={
            "schema": plan.schema,
            "org_run_id": plan.org_run_id,
            "project_id": plan.project_id,
            "repository_id": plan.repository_id,
            "base_commit": plan.base_commit,
        },
    )
    post_blackboard_update(
        conn,
        anchor_id,
        author=created_by,
        key="topology",
        value={
            "anchor_id": created.anchor_id,
            "remote_tasks": {
                key: asdict(value) for key, value in created.remote_tasks.items()
            },
            "integration_id": created.integration_id,
            "review_id": created.review_id,
            "synthesis_id": created.synthesis_id,
            "project_id": created.project_id,
        },
    )
    post_blackboard_update(
        conn,
        anchor_id,
        author=created_by,
        key="conflicts",
        value=list(validation.conflicts),
    )
    return created
