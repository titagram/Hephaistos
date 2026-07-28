from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from hermes_cli import agentic_org_run
from hermes_cli import kanban_db as kb
from hermes_cli.agentic_org_run import (
    apply_org_run_amendment,
    load_org_run_topology,
    materialize_org_run,
)
from hermes_cli.implementation_plan import (
    IMPLEMENTATION_PLAN_SCHEMA,
    ImplementationPlan,
    ImplementationTask,
    PlanValidation,
    parse_implementation_amendment,
)
from hermes_cli.org_run_store import get_org_run, list_org_nodes


def _task_payload(
    task_id: str,
    *,
    depends_on: list[str] | None = None,
    write_scope: list[str] | None = None,
    risk: str = "low",
) -> dict:
    return {
        "id": task_id,
        "title": f"Implement {task_id}",
        "role": "leaf",
        "risk": risk,
        "write_scope": write_scope or [f"hermes_cli/{task_id}.py"],
        "depends_on": depends_on or [],
        "acceptance_criteria": [f"{task_id} is covered"],
        "verification": [f"pytest tests/{task_id}"],
        "independent_review": False,
    }


def _amendment_payload(
    *,
    base_plan_version: int = 1,
    add_tasks: list[dict] | None = None,
    replace_tasks: list[dict] | None = None,
    cancel_task_ids: list[str] | None = None,
) -> dict:
    return {
        "schema": "hades.implementation-amendment.v1",
        "run_id": "local-run-001",
        "base_plan_version": base_plan_version,
        "reason": "Integration exposed a missing regression",
        "add_tasks": add_tasks or [],
        "replace_tasks": replace_tasks or [],
        "cancel_task_ids": cancel_task_ids or [],
    }


def _plan(repository: Path, *, two_tasks: bool = False) -> ImplementationPlan:
    tasks = [
        ImplementationTask(
            id="runtime",
            title="Disconnect runtime sync",
            role="leaf",
            risk="low",
            write_scope=("hermes_cli/runtime.py",),
            depends_on=(),
            acceptance_criteria=("No backend client is constructed",),
            verification=("pytest tests/hermes_cli/test_runtime.py",),
            independent_review=False,
        )
    ]
    if two_tasks:
        tasks.append(
            ImplementationTask(
                id="docs",
                title="Document the local flow",
                role="leaf",
                risk="low",
                write_scope=("website/docs/local.md",),
                depends_on=(),
                acceptance_criteria=("Local behavior is documented",),
                verification=("pytest tests/hermes_cli/test_docs.py",),
                independent_review=False,
            )
        )
    return ImplementationPlan(
        schema=IMPLEMENTATION_PLAN_SCHEMA,
        run_id="local-run-001",
        objective="Ship a local OrgRun",
        base_commit=_head(repository),
        acceptance_criteria=("Focused tests pass",),
        tasks=tuple(tasks),
    )


def _head(repository: Path) -> str:
    import subprocess

    return subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _validation(plan: ImplementationPlan) -> PlanValidation:
    plan_json = json.dumps(
        {
            "schema": plan.schema,
            "run_id": plan.run_id,
            "objective": plan.objective,
            "base_commit": plan.base_commit,
            "acceptance_criteria": list(plan.acceptance_criteria),
            "tasks": [
                {
                    **{
                        key: list(value) if isinstance(value, tuple) else value
                        for key, value in task.__dict__.items()
                    }
                }
                for task in plan.tasks
            ],
            "independent_review": plan.independent_review,
            "origin": plan.origin,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return PlanValidation(
        plan_hash=hashlib.sha256(plan_json.encode()).hexdigest(),
        ordered_dependencies={task.id: task.depends_on for task in plan.tasks},
        conflicts=(),
        resolved_profiles={
            "orchestrator": "orchestrator",
            "leaf": "leaf",
            "reviewer": "reviewer",
        },
        routed_roles=("orchestrator", "leaf", "reviewer"),
    )


def _materialized(conn, repository: Path, *, two_tasks: bool = False):
    plan = _plan(repository, two_tasks=two_tasks)
    topology = materialize_org_run(
        conn,
        plan,
        _validation(plan),
        board="default",
        activate=False,
    )
    return plan, topology


def _snapshot(conn) -> dict:
    tables = (
        "tasks",
        "task_links",
        "task_events",
        "kanban_org_runs",
        "kanban_org_plan_versions",
        "kanban_org_nodes",
    )
    return {
        table: [
            tuple(row)
            for row in conn.execute(
                f"SELECT * FROM {table} ORDER BY rowid"
            ).fetchall()
        ]
        for table in tables
    }


def test_parse_amendment_builds_immutable_operations():
    amendment = parse_implementation_amendment(
        _amendment_payload(
            add_tasks=[_task_payload("regression")],
            replace_tasks=[
                {
                    "replaces": "runtime",
                    "task": _task_payload("runtime-v2"),
                }
            ],
            cancel_task_ids=["docs"],
        )
    )

    assert amendment.schema == "hades.implementation-amendment.v1"
    assert amendment.run_id == "local-run-001"
    assert amendment.base_plan_version == 1
    assert amendment.add_tasks[0].id == "regression"
    assert amendment.replace_tasks[0].replaces == "runtime"
    assert amendment.replace_tasks[0].task.id == "runtime-v2"
    assert amendment.cancel_task_ids == ("docs",)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (_amendment_payload(), "empty"),
        (
            _amendment_payload(cancel_task_ids=["runtime", "runtime"]),
            "repeated target",
        ),
        (
            _amendment_payload(
                replace_tasks=[
                    {"replaces": "runtime", "task": _task_payload("runtime-v2")}
                ],
                cancel_task_ids=["runtime"],
            ),
            "repeated target",
        ),
    ],
)
def test_parse_amendment_rejects_empty_or_repeated_targets(payload, message):
    with pytest.raises(ValueError, match=message):
        parse_implementation_amendment(payload)


def test_additive_amendment_versions_full_plan_and_replays_idempotently(tmp_path):
    repository = Path(__file__).resolve().parents[2]
    amendment = parse_implementation_amendment(
        _amendment_payload(add_tasks=[_task_payload("regression")])
    )
    with kb.connect(tmp_path / "kanban.db") as conn:
        _, original = _materialized(conn, repository)

        amended = apply_org_run_amendment(
            conn,
            amendment,
            board="default",
            repository=repository,
            profile_exists=lambda _role: True,
            role_route_exists=lambda _role: True,
        )
        first_snapshot = _snapshot(conn)
        replay = apply_org_run_amendment(
            conn,
            amendment,
            board="default",
            repository=repository,
            profile_exists=lambda _role: True,
            role_route_exists=lambda _role: True,
        )

        run = get_org_run(conn, amendment.run_id)
        assert run is not None
        assert run.plan_version == 2
        assert amended == replay == load_org_run_topology(conn, amendment.run_id)
        assert set(amended.tasks) == {"runtime", "regression"}
        assert amended.tasks["runtime"] == original.tasks["runtime"]
        assert kb.parent_ids(conn, amended.integration_id) == sorted(
            [
                amended.tasks["runtime"].execution_id,
                amended.tasks["regression"].execution_id,
            ]
        )
        version = conn.execute(
            "SELECT plan_json, reason FROM kanban_org_plan_versions "
            "WHERE run_id=? AND plan_version=2",
            (amendment.run_id,),
        ).fetchone()
        assert [task["id"] for task in json.loads(version["plan_json"])["tasks"]] == [
            "runtime",
            "regression",
        ]
        assert version["reason"] == amendment.reason
        new_node = next(
            node for node in list_org_nodes(conn, amendment.run_id)
            if node.node_id.endswith(":task:regression")
        )
        assert new_node.plan_version == 2
        assert new_node.state == "active"
        assert conn.execute(
            "SELECT COUNT(*) FROM kanban_remote_links"
        ).fetchone()[0] == 0
        assert _snapshot(conn) == first_snapshot


def test_amended_reused_finalization_projection_failure_keeps_run_reviewing(
    tmp_path, monkeypatch,
):
    """A current amended run can retain its original finalization node."""
    repository = Path(__file__).resolve().parents[2]
    amendment = parse_implementation_amendment(
        _amendment_payload(add_tasks=[_task_payload("regression")])
    )
    with kb.connect(tmp_path / "kanban.db") as conn:
        plan = _plan(repository)
        materialize_org_run(
            conn, plan, _validation(plan), board="default", activate=True,
        )
        topology = apply_org_run_amendment(
            conn,
            amendment,
            board="default",
            repository=repository,
            profile_exists=lambda _role: True,
            role_route_exists=lambda _role: True,
        )
        final_node = next(
            node for node in list_org_nodes(conn, amendment.run_id)
            if node.task_id == topology.finalization_id
        )
        assert final_node.state == "active"
        assert final_node.plan_version == 1
        assert get_org_run(conn, amendment.run_id).plan_version == 2

        for task_id in (
            topology.tasks["runtime"].execution_id,
            topology.tasks["regression"].execution_id,
            topology.integration_id,
        ):
            assert kb.claim_task(conn, task_id, claimer="amended-projection-failure")
            assert kb.complete_task(conn, task_id, summary="complete")
        monkeypatch.setattr(
            "hermes_cli.kanban_reports.project_after_task_completion",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("projection failed")),
        )
        assert kb.claim_task(
            conn, topology.finalization_id, claimer="amended-projection-failure"
        )
        assert kb.complete_task(conn, topology.finalization_id, summary="final complete")

        assert get_org_run(conn, amendment.run_id).state == "reviewing"


@pytest.mark.parametrize(
    ("node_name", "status"),
    [
        ("integration_id", "running"),
        ("integration_id", "done"),
        ("review_id", "running"),
        ("review_id", "done"),
        ("finalization_id", "running"),
        ("finalization_id", "done"),
    ],
)
def test_amendment_rejects_started_downstream_phase_without_writes(
    tmp_path,
    node_name,
    status,
):
    repository = Path(__file__).resolve().parents[2]
    plan = _plan(repository)
    plan = replace(plan, tasks=(replace(plan.tasks[0], risk="high"),))
    amendment = parse_implementation_amendment(
        _amendment_payload(add_tasks=[_task_payload("regression")])
    )
    with kb.connect(tmp_path / "kanban.db") as conn:
        topology = materialize_org_run(
            conn,
            plan,
            _validation(plan),
            board="default",
            activate=False,
        )
        downstream_id = getattr(topology, node_name)
        assert downstream_id is not None
        conn.execute(
            "UPDATE tasks SET status=? WHERE id=?",
            (status, downstream_id),
        )
        conn.commit()
        before = _snapshot(conn)

        with pytest.raises(ValueError, match="downstream.*started"):
            apply_org_run_amendment(
                conn,
                amendment,
                board="default",
                repository=repository,
                profile_exists=lambda _role: True,
                role_route_exists=lambda _role: True,
            )

        assert _snapshot(conn) == before
        assert get_org_run(conn, amendment.run_id).plan_version == 1


@pytest.mark.parametrize("mismatch", ["reason", "operation"])
def test_stale_replay_requires_exact_amendment_provenance(tmp_path, mismatch):
    repository = Path(__file__).resolve().parents[2]
    original = parse_implementation_amendment(
        _amendment_payload(
            replace_tasks=[
                {
                    "replaces": "runtime",
                    "task": _task_payload("runtime-v2"),
                }
            ]
        )
    )
    if mismatch == "reason":
        replay = replace(original, reason="A different reason")
    else:
        replay = parse_implementation_amendment(
            _amendment_payload(
                add_tasks=[_task_payload("runtime-v2")],
                cancel_task_ids=["runtime"],
            )
        )

    with kb.connect(tmp_path / "kanban.db") as conn:
        _materialized(conn, repository)
        apply_org_run_amendment(
            conn,
            original,
            board="default",
            repository=repository,
            profile_exists=lambda _role: True,
            role_route_exists=lambda _role: True,
        )
        before = _snapshot(conn)

        with pytest.raises(ValueError, match="stale base_plan_version"):
            apply_org_run_amendment(
                conn,
                replay,
                board="default",
                repository=repository,
                profile_exists=lambda _role: True,
                role_route_exists=lambda _role: True,
            )

        assert _snapshot(conn) == before
        version = conn.execute(
            "SELECT reason FROM kanban_org_plan_versions "
            "WHERE run_id=? AND plan_version=2",
            (original.run_id,),
        ).fetchone()
        assert version["reason"] == original.reason


def test_replacement_archives_unfinished_task_and_rewires_integration(tmp_path):
    repository = Path(__file__).resolve().parents[2]
    amendment = parse_implementation_amendment(
        _amendment_payload(
            replace_tasks=[
                {
                    "replaces": "runtime",
                    "task": _task_payload("runtime-v2"),
                }
            ]
        )
    )
    with kb.connect(tmp_path / "kanban.db") as conn:
        _, original = _materialized(conn, repository)

        amended = apply_org_run_amendment(
            conn,
            amendment,
            board="default",
            repository=repository,
            profile_exists=lambda _role: True,
            role_route_exists=lambda _role: True,
        )

        assert set(amended.tasks) == {"runtime-v2"}
        assert kb.get_task(
            conn, original.tasks["runtime"].execution_id
        ).status == "archived"
        old_node = next(
            node for node in list_org_nodes(conn, amendment.run_id)
            if node.node_id.endswith(":task:runtime")
        )
        assert old_node.state == "cancelled"
        assert kb.parent_ids(conn, amended.integration_id) == [
            amended.tasks["runtime-v2"].execution_id
        ]
        assert original.tasks["runtime"].execution_id not in kb.parent_ids(
            conn, amended.integration_id
        )


def test_amendment_versions_every_retained_node_whose_effective_dag_changes(
    tmp_path,
):
    """Breaks if rewired retained cards keep a stale contract hash/version."""
    repository = Path(__file__).resolve().parents[2]
    original_plan = _plan(repository, two_tasks=True)
    docs = replace(original_plan.tasks[1], depends_on=("runtime",))
    original_plan = replace(original_plan, tasks=(original_plan.tasks[0], docs))
    amendment = parse_implementation_amendment(
        _amendment_payload(cancel_task_ids=["runtime"])
    )
    with kb.connect(tmp_path / "kanban.db") as conn:
        original = materialize_org_run(
            conn,
            original_plan,
            _validation(original_plan),
            board="default",
            activate=False,
        )

        amended = apply_org_run_amendment(
            conn,
            amendment,
            board="default",
            repository=repository,
            profile_exists=lambda _role: True,
            role_route_exists=lambda _role: True,
        )

        nodes = {node.node_id: node for node in list_org_nodes(conn, amendment.run_id)}
        docs_node = nodes[f"org-run:{amendment.run_id}:task:docs"]
        integration_node = nodes[f"org-run:{amendment.run_id}:integration"]
        cancelled_runtime = nodes[f"org-run:{amendment.run_id}:task:runtime"]
        assert docs_node.plan_version == 2
        assert integration_node.plan_version == 2
        assert cancelled_runtime.plan_version == 1
        assert cancelled_runtime.state == "cancelled"
        assert kb.parent_ids(conn, amended.tasks["docs"].execution_id) == [
            amended.anchor_id
        ]
        assert conn.execute(
            "SELECT COUNT(*) FROM kanban_org_plan_versions WHERE run_id=?",
            (amendment.run_id,),
        ).fetchone()[0] == 2
        assert load_org_run_topology(conn, amendment.run_id) == amended
        assert original.tasks["runtime"].execution_id != amended.tasks[
            "docs"
        ].execution_id


def test_cancellation_archives_unfinished_task_and_keeps_remaining_gate(tmp_path):
    repository = Path(__file__).resolve().parents[2]
    amendment = parse_implementation_amendment(
        _amendment_payload(cancel_task_ids=["docs"])
    )
    with kb.connect(tmp_path / "kanban.db") as conn:
        _, original = _materialized(conn, repository, two_tasks=True)

        amended = apply_org_run_amendment(
            conn,
            amendment,
            board="default",
            repository=repository,
            profile_exists=lambda _role: True,
            role_route_exists=lambda _role: True,
        )

        assert set(amended.tasks) == {"runtime"}
        assert kb.get_task(conn, original.tasks["docs"].execution_id).status == "archived"
        assert kb.parent_ids(conn, amended.integration_id) == [
            amended.tasks["runtime"].execution_id
        ]
        cancelled = [
            node for node in list_org_nodes(conn, amendment.run_id)
            if node.node_id.endswith(":task:docs")
        ]
        assert [node.state for node in cancelled] == ["cancelled"]


def test_load_rejects_cancelled_node_without_historical_provenance(tmp_path):
    repository = Path(__file__).resolve().parents[2]
    amendment = parse_implementation_amendment(
        _amendment_payload(cancel_task_ids=["docs"])
    )
    with kb.connect(tmp_path / "kanban.db") as conn:
        _materialized(conn, repository, two_tasks=True)
        apply_org_run_amendment(
            conn,
            amendment,
            board="default",
            repository=repository,
            profile_exists=lambda _role: True,
            role_route_exists=lambda _role: True,
        )
        conn.execute(
            "UPDATE kanban_org_nodes SET plan_version=99 "
            "WHERE run_id=? AND node_id LIKE '%:task:docs'",
            (amendment.run_id,),
        )
        conn.commit()

        with pytest.raises(ValueError, match="incomplete stored topology"):
            load_org_run_topology(conn, amendment.run_id)


@pytest.mark.parametrize(
    "tamper",
    ["plan_hash", "plan_json", "active_contract_hash"],
)
def test_load_rejects_tampered_current_plan_or_contract(tmp_path, tamper):
    repository = Path(__file__).resolve().parents[2]
    with kb.connect(tmp_path / "kanban.db") as conn:
        plan, topology = _materialized(conn, repository)
        if tamper == "plan_hash":
            conn.execute(
                "UPDATE kanban_org_plan_versions SET plan_hash='tampered' "
                "WHERE run_id=? AND plan_version=1",
                (plan.run_id,),
            )
            conn.execute(
                "UPDATE kanban_org_runs SET plan_hash='tampered' WHERE run_id=?",
                (plan.run_id,),
            )
        elif tamper == "plan_json":
            row = conn.execute(
                "SELECT plan_json FROM kanban_org_plan_versions "
                "WHERE run_id=? AND plan_version=1",
                (plan.run_id,),
            ).fetchone()
            payload = json.loads(row["plan_json"])
            payload["objective"] = "Tampered objective"
            plan_json = json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
            plan_hash = hashlib.sha256(plan_json.encode()).hexdigest()
            conn.execute(
                "UPDATE kanban_org_plan_versions "
                "SET plan_json=?, plan_hash=? "
                "WHERE run_id=? AND plan_version=1",
                (plan_json, plan_hash, plan.run_id),
            )
            conn.execute(
                "UPDATE kanban_org_runs SET plan_hash=? WHERE run_id=?",
                (plan_hash, plan.run_id),
            )
        else:
            conn.execute(
                "UPDATE kanban_org_nodes SET contract_hash='tampered' "
                "WHERE run_id=? AND task_id=?",
                (plan.run_id, topology.tasks["runtime"].execution_id),
            )
        conn.commit()

        with pytest.raises(ValueError, match="incomplete stored topology"):
            load_org_run_topology(conn, plan.run_id)


def test_load_rejects_tampered_historical_cancelled_contract(tmp_path):
    repository = Path(__file__).resolve().parents[2]
    amendment = parse_implementation_amendment(
        _amendment_payload(cancel_task_ids=["docs"])
    )
    with kb.connect(tmp_path / "kanban.db") as conn:
        _materialized(conn, repository, two_tasks=True)
        apply_org_run_amendment(
            conn,
            amendment,
            board="default",
            repository=repository,
            profile_exists=lambda _role: True,
            role_route_exists=lambda _role: True,
        )
        conn.execute(
            "UPDATE kanban_org_nodes SET contract_hash='tampered' "
            "WHERE run_id=? AND node_id LIKE '%:task:docs'",
            (amendment.run_id,),
        )
        conn.commit()

        with pytest.raises(ValueError, match="incomplete stored topology"):
            load_org_run_topology(conn, amendment.run_id)


@pytest.mark.parametrize("status", ["done", "running"])
def test_amendment_refuses_to_rewrite_started_task_without_version_bump(
    tmp_path,
    status,
):
    repository = Path(__file__).resolve().parents[2]
    amendment = parse_implementation_amendment(
        _amendment_payload(cancel_task_ids=["runtime"])
    )
    with kb.connect(tmp_path / "kanban.db") as conn:
        _, original = _materialized(conn, repository)
        conn.execute(
            "UPDATE tasks SET status=? WHERE id=?",
            (status, original.tasks["runtime"].execution_id),
        )
        conn.commit()
        before = _snapshot(conn)

        with pytest.raises(ValueError, match="cannot amend.*runtime"):
            apply_org_run_amendment(
                conn,
                amendment,
                board="default",
                repository=repository,
                profile_exists=lambda _role: True,
                role_route_exists=lambda _role: True,
            )

        assert _snapshot(conn) == before
        assert get_org_run(conn, amendment.run_id).plan_version == 1


def test_amendment_rejects_stale_base_version_without_writes(tmp_path):
    repository = Path(__file__).resolve().parents[2]
    amendment = parse_implementation_amendment(
        _amendment_payload(
            base_plan_version=7,
            add_tasks=[_task_payload("regression")],
        )
    )
    with kb.connect(tmp_path / "kanban.db") as conn:
        _materialized(conn, repository)
        before = _snapshot(conn)

        with pytest.raises(ValueError, match="stale base_plan_version"):
            apply_org_run_amendment(
                conn,
                amendment,
                board="default",
                repository=repository,
                profile_exists=lambda _role: True,
                role_route_exists=lambda _role: True,
            )

        assert _snapshot(conn) == before


@pytest.mark.parametrize("failure", ["scope_cycle", "profile"])
def test_validation_failure_rolls_back_without_version_bump(tmp_path, failure):
    repository = Path(__file__).resolve().parents[2]
    task = (
        _task_payload(
            "aaa",
            depends_on=["runtime"],
            write_scope=["hermes_cli/runtime.py"],
        )
        if failure == "scope_cycle"
        else _task_payload("regression")
    )
    amendment = parse_implementation_amendment(
        _amendment_payload(add_tasks=[task])
    )
    with kb.connect(tmp_path / "kanban.db") as conn:
        _materialized(conn, repository)
        before = _snapshot(conn)

        with pytest.raises(
            ValueError,
            match="dependency cycle" if failure == "scope_cycle" else "missing profile",
        ):
            apply_org_run_amendment(
                conn,
                amendment,
                board="default",
                repository=repository,
                profile_exists=(
                    (lambda role: role != "leaf")
                    if failure == "profile"
                    else (lambda _role: True)
                ),
                role_route_exists=lambda _role: True,
            )

        assert _snapshot(conn) == before
        assert get_org_run(conn, amendment.run_id).plan_version == 1


def test_amendment_requires_profile_and_route_as_separate_prerequisites(tmp_path):
    """Breaks if amendment validation silently trusts either prerequisite."""
    repository = Path(__file__).resolve().parents[2]
    amendment = parse_implementation_amendment(
        _amendment_payload(add_tasks=[_task_payload("regression")])
    )
    with kb.connect(tmp_path / "kanban.db") as conn:
        _materialized(conn, repository)
        before = _snapshot(conn)

        with pytest.raises(
            ValueError,
            match="missing delegation role route: reviewer",
        ):
            apply_org_run_amendment(
                conn,
                amendment,
                board="default",
                repository=repository,
                profile_exists=lambda _role: True,
                role_route_exists=lambda role: role != "reviewer",
            )

        assert _snapshot(conn) == before


def test_mid_apply_failure_rolls_back_cards_links_nodes_and_version(
    tmp_path,
    monkeypatch,
):
    repository = Path(__file__).resolve().parents[2]
    amendment = parse_implementation_amendment(
        _amendment_payload(
            replace_tasks=[
                {
                    "replaces": "runtime",
                    "task": _task_payload("runtime-v2"),
                }
            ]
        )
    )
    with kb.connect(tmp_path / "kanban.db") as conn:
        _materialized(conn, repository)
        before = _snapshot(conn)

        def fail_node_insert(*_args, **_kwargs):
            raise RuntimeError("injected node persistence failure")

        monkeypatch.setattr(agentic_org_run, "insert_org_node", fail_node_insert)
        with pytest.raises(RuntimeError, match="injected node"):
            apply_org_run_amendment(
                conn,
                amendment,
                board="default",
                repository=repository,
                profile_exists=lambda _role: True,
                role_route_exists=lambda _role: True,
            )

        assert _snapshot(conn) == before
        assert get_org_run(conn, amendment.run_id).plan_version == 1
