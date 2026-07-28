from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import hashlib
import json

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli.agentic_org_run import (
    adopt_legacy_org_run,
    is_org_run_task,
    load_org_run_topology,
    materialize_org_run,
)
from hermes_cli.hierarchical_execution import (
    parse_execution_portfolio,
    validate_execution_portfolio,
)
from hermes_cli.implementation_plan import (
    IMPLEMENTATION_PLAN_SCHEMA,
    ImplementationPlan,
    ImplementationTask,
    PlanValidation,
    canonical_plan_json,
)
from hermes_cli.kanban_portfolio import create_org_run
from hermes_cli.org_run_store import get_org_run, list_org_nodes
from hermes_cli.org_run_store import (
    insert_report,
    refresh_org_run_state,
    set_org_run_state,
)
from tools.delegation_routing import DelegationProfile


def _plan(
    *,
    run_id: str = "local-run-001",
    risk: str = "high",
    task_review: bool = True,
    global_review: bool = False,
) -> ImplementationPlan:
    return ImplementationPlan(
        schema=IMPLEMENTATION_PLAN_SCHEMA,
        run_id=run_id,
        objective="Ship a local OrgRun",
        base_commit="a" * 40,
        acceptance_criteria=("Focused tests pass",),
        tasks=(
            ImplementationTask(
                id="runtime",
                title="Disconnect runtime sync",
                role="leaf",
                risk=risk,
                write_scope=("hermes_cli/kanban.py",),
                depends_on=(),
                acceptance_criteria=("No backend client is constructed",),
                verification=("pytest tests/hermes_cli/test_kanban.py",),
                independent_review=task_review,
            ),
        ),
        independent_review=global_review,
    )


def _validation(
    plan: ImplementationPlan,
    *,
    plan_hash: str | None = None,
) -> PlanValidation:
    return PlanValidation(
        plan_hash=plan_hash or hashlib.sha256(
            canonical_plan_json(plan).encode("utf-8")
        ).hexdigest(),
        ordered_dependencies={task.id: task.depends_on for task in plan.tasks},
        conflicts=(),
        resolved_profiles={
            "orchestrator": "orchestrator",
            "leaf": "leaf",
            "reviewer": "reviewer",
        },
        routed_roles=("orchestrator", "leaf", "reviewer"),
    )


def _counts(conn) -> tuple[int, int, int, int]:
    return tuple(
        conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in (
            "tasks",
            "kanban_org_runs",
            "kanban_org_plan_versions",
            "kanban_org_nodes",
        )
    )


def test_materialize_builds_simplified_local_topology_with_logical_roles(tmp_path):
    plan = _plan()
    with kb.connect(tmp_path / "kanban.db") as conn:
        topology = materialize_org_run(
            conn, plan, _validation(plan), board="default"
        )

        assert kb.get_task(conn, topology.anchor_id).status == "done"
        assert kb.get_task(conn, topology.tasks["runtime"].execution_id).assignee == "leaf"
        assert kb.get_task(conn, topology.tasks["runtime"].review_id).assignee == "reviewer"
        assert kb.parent_ids(conn, topology.integration_id) == [
            topology.tasks["runtime"].review_id
        ]
        assert kb.get_task(conn, topology.integration_id).assignee == "orchestrator"
        assert kb.get_task(conn, topology.finalization_id).assignee == "orchestrator"
        assert topology.review_id is not None
        assert kb.parent_ids(conn, topology.review_id) == [topology.integration_id]
        assert kb.parent_ids(conn, topology.finalization_id) == [topology.review_id]
        assert _counts(conn) == (6, 1, 1, 6)
        assert conn.execute("SELECT COUNT(*) FROM kanban_remote_links").fetchone()[0] == 0
        assert load_org_run_topology(conn, plan.run_id) == topology
        assert all(
            is_org_run_task(conn, task_id)
            for task_id in (
                topology.anchor_id,
                topology.tasks["runtime"].execution_id,
                topology.tasks["runtime"].review_id,
                topology.integration_id,
                topology.review_id,
                topology.finalization_id,
            )
        )


@pytest.mark.parametrize(
    ("risk", "task_review", "global_review", "want_task_review", "want_global_review"),
    [
        ("low", False, False, False, False),
        ("low", False, True, False, True),
        ("high", False, False, True, True),
    ],
)
def test_materialize_creates_only_required_review_nodes(
    tmp_path,
    risk,
    task_review,
    global_review,
    want_task_review,
    want_global_review,
):
    plan = _plan(
        risk=risk,
        task_review=task_review,
        global_review=global_review,
    )
    with kb.connect(tmp_path / "kanban.db") as conn:
        topology = materialize_org_run(
            conn, plan, _validation(plan), board="default"
        )

        assert (topology.tasks["runtime"].review_id is not None) is want_task_review
        assert (topology.review_id is not None) is want_global_review
        expected_count = 1 + 1 + int(want_task_review) + 1 + int(want_global_review) + 1
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == expected_count


def test_materialize_links_dependencies_to_their_terminal_gates(tmp_path):
    first = _plan().tasks[0]
    second = replace(
        first,
        id="consumer",
        title="Consume the runtime",
        risk="low",
        independent_review=False,
        write_scope=("hermes_cli/consumer.py",),
        depends_on=("runtime",),
    )
    plan = replace(_plan(), tasks=(first, second))
    validation = replace(
        _validation(plan),
        ordered_dependencies={"consumer": ("runtime",), "runtime": ()},
    )

    with kb.connect(tmp_path / "kanban.db") as conn:
        topology = materialize_org_run(
            conn, plan, validation, board="default"
        )

        assert topology.tasks["runtime"].review_id in kb.parent_ids(
            conn, topology.tasks["consumer"].execution_id
        )
        assert kb.parent_ids(conn, topology.integration_id) == sorted(
            [
                topology.tasks["consumer"].execution_id,
                topology.tasks["runtime"].review_id,
            ]
        )


def test_materialize_exact_replay_is_idempotent_and_changed_plan_is_rejected(tmp_path):
    plan = _plan()
    validation = _validation(plan)
    with kb.connect(tmp_path / "kanban.db") as conn:
        first = materialize_org_run(
            conn, plan, validation, board="default"
        )
        first_counts = _counts(conn)

        assert materialize_org_run(
            conn, plan, validation, board="default"
        ) == first
        assert _counts(conn) == first_counts

        changed = replace(plan, objective="A different objective")
        with pytest.raises(ValueError, match="different plan hash"):
            materialize_org_run(
                conn,
                changed,
                _validation(changed),
                board="default",
            )
        assert _counts(conn) == first_counts


def test_materialize_recomputes_supplied_validation_before_opening_transaction(
    tmp_path,
    monkeypatch,
):
    """Breaks if a forged plan hash/dependency projection reaches SQLite."""
    plan = _plan()
    forged = replace(
        _validation(plan),
        plan_hash="0" * 64,
        ordered_dependencies={"runtime": ("not-in-plan",)},
    )
    entered = False

    @contextmanager
    def forbidden_write_txn(_conn):
        nonlocal entered
        entered = True
        yield

    with kb.connect(tmp_path / "kanban.db") as conn:
        monkeypatch.setattr(kb, "write_txn", forbidden_write_txn)
        with pytest.raises(ValueError, match="supplied plan validation"):
            materialize_org_run(conn, plan, forged, board="default")

    assert entered is False


def test_generic_contract_and_dag_mutations_reject_managed_cards(tmp_path):
    """Breaks if ordinary Kanban verbs can rewrite a materialized plan."""
    plan = _plan(risk="low", task_review=False, global_review=False)
    with kb.connect(tmp_path / "kanban.db") as conn:
        topology = materialize_org_run(
            conn, plan, _validation(plan), board="default"
        )
        execution_id = topology.tasks["runtime"].execution_id
        outsider_id = kb.create_task(conn, title="ordinary task")

        forbidden = (
            lambda: kb.assign_task(conn, execution_id, "reviewer"),
            lambda: kb.link_tasks(conn, outsider_id, execution_id),
            lambda: kb.unlink_tasks(conn, topology.anchor_id, execution_id),
            lambda: kb.archive_task(conn, execution_id),
            lambda: kb.delete_task(conn, execution_id),
        )
        for mutation in forbidden:
            with pytest.raises(ValueError, match="managed OrgRun"):
                mutation()

        assert kb.get_task(conn, execution_id).assignee == "leaf"
        assert kb.get_task(conn, execution_id).status != "archived"
        assert kb.parent_ids(conn, execution_id) == [topology.anchor_id]


@pytest.mark.parametrize("mutation", ["create_child", "dependency_block"])
def test_indirect_generic_link_writers_reject_managed_cards(tmp_path, mutation):
    """Breaks if a less-obvious writer can attach a card to a managed DAG."""
    plan = _plan(risk="low", task_review=False, global_review=False)
    with kb.connect(tmp_path / "kanban.db") as conn:
        topology = materialize_org_run(
            conn, plan, _validation(plan), board="default"
        )
        execution_id = topology.tasks["runtime"].execution_id
        outsider_id = kb.create_task(conn, title="ordinary dependency")

        with pytest.raises(kb.ManagedPlanMutationError):
            if mutation == "create_child":
                kb.create_task(
                    conn,
                    title="generic child",
                    parents=[execution_id],
                )
            else:
                kb.block_task(
                    conn,
                    execution_id,
                    kind="dependency",
                    dependency_task_id=outsider_id,
                )

        assert kb.parent_ids(conn, execution_id) == [topology.anchor_id]
        assert kb.child_ids(conn, execution_id) == [topology.integration_id]


@pytest.mark.parametrize(
    "tamper_sql",
    [
        "UPDATE tasks SET assignee='reviewer' WHERE id=?",
        "UPDATE tasks SET title='rewritten contract' WHERE id=?",
        "UPDATE tasks SET skills='[\"other-skill\"]' WHERE id=?",
    ],
)
def test_dispatch_blocks_live_managed_contract_drift(
    tmp_path,
    monkeypatch,
    tamper_sql,
):
    """Breaks if dispatch trusts allowed-looking live fields over provenance."""
    plan = _plan(risk="low", task_review=False, global_review=False)
    spawned: list[str] = []
    with kb.connect(tmp_path / "kanban.db") as conn:
        topology = materialize_org_run(
            conn, plan, _validation(plan), board="default"
        )
        execution_id = topology.tasks["runtime"].execution_id
        conn.execute(tamper_sql, (execution_id,))
        conn.commit()
        monkeypatch.setattr(
            "hermes_cli.profiles.profile_exists",
            lambda _profile: True,
        )
        monkeypatch.setattr(
            kb,
            "_resolve_worker_role_route",
            lambda role: {"role": role},
        )

        kb.dispatch_once(
            conn,
            spawn_fn=lambda task, *_args, **_kwargs: spawned.append(task.id),
            board="default",
        )

        assert spawned == []
        assert kb.get_task(conn, execution_id).status == "blocked"
        blocked = [
            event for event in kb.list_events(conn, execution_id)
            if event.kind == "blocked"
        ]
        assert len(blocked) == 1
        assert "managed_plan_drift" in blocked[0].payload["reason"]


def test_dispatch_blocks_managed_parent_link_drift(tmp_path, monkeypatch):
    """Breaks if a ready card can launch after its stored DAG was rewired."""
    plan = _plan(risk="low", task_review=False, global_review=False)
    with kb.connect(tmp_path / "kanban.db") as conn:
        topology = materialize_org_run(
            conn, plan, _validation(plan), board="default"
        )
        execution_id = topology.tasks["runtime"].execution_id
        conn.execute(
            "DELETE FROM task_links WHERE parent_id=? AND child_id=?",
            (topology.anchor_id, execution_id),
        )
        conn.commit()
        monkeypatch.setattr(
            "hermes_cli.profiles.profile_exists",
            lambda _profile: True,
        )
        monkeypatch.setattr(
            kb,
            "_resolve_worker_role_route",
            lambda role: {"role": role},
        )

        result = kb.dispatch_once(
            conn,
            spawn_fn=lambda *_args, **_kwargs: pytest.fail(
                "drifted managed task spawned"
            ),
            board="default",
        )

        assert result.auto_blocked == [execution_id]
        assert kb.get_task(conn, execution_id).status == "blocked"


def test_review_column_dispatch_blocks_live_managed_contract_drift(
    tmp_path,
    monkeypatch,
):
    """Breaks if the review dispatcher skips the managed-plan preflight."""
    plan = _plan(risk="low", task_review=False, global_review=False)
    with kb.connect(tmp_path / "kanban.db") as conn:
        topology = materialize_org_run(
            conn, plan, _validation(plan), board="default"
        )
        execution_id = topology.tasks["runtime"].execution_id
        conn.execute(
            "UPDATE tasks SET status='review', assignee='reviewer' WHERE id=?",
            (execution_id,),
        )
        conn.commit()
        monkeypatch.setattr(
            "hermes_cli.profiles.profile_exists",
            lambda _profile: True,
        )
        monkeypatch.setattr(
            kb,
            "_resolve_worker_role_route",
            lambda role: {"role": role},
        )

        result = kb.dispatch_once(
            conn,
            spawn_fn=lambda *_args, **_kwargs: pytest.fail(
                "drifted managed review task spawned"
            ),
            board="default",
        )

        assert result.auto_blocked == [execution_id]
        assert kb.get_task(conn, execution_id).status == "blocked"


def test_dispatch_routes_managed_task_by_validated_persisted_role(
    tmp_path,
    monkeypatch,
):
    """Breaks if managed dispatch routes from a mutable assignee field."""
    plan = _plan(risk="low", task_review=False, global_review=False)
    with kb.connect(tmp_path / "kanban.db") as conn:
        topology = materialize_org_run(
            conn, plan, _validation(plan), board="default"
        )
        execution_id = topology.tasks["runtime"].execution_id
        routed_roles: list[str] = []
        monkeypatch.setattr(
            "hermes_cli.agentic_org_run.validate_live_org_run_task",
            lambda _conn, _task_id: "reviewer",
        )
        monkeypatch.setattr(
            "hermes_cli.profiles.profile_exists",
            lambda _profile: True,
        )
        monkeypatch.setattr(
            kb,
            "_resolve_worker_role_route",
            lambda role: routed_roles.append(role) or {"role": role},
        )

        result = kb.dispatch_once(
            conn,
            spawn_fn=lambda *_args, **_kwargs: None,
            board="default",
            dry_run=True,
        )

        assert result.spawned == [(execution_id, "leaf", "")]
        assert routed_roles == ["reviewer"]


def test_exact_replay_rejects_live_managed_contract_drift(tmp_path):
    """Breaks if idempotent replay validates rows but not their live contract."""
    plan = _plan(risk="low", task_review=False, global_review=False)
    validation = _validation(plan)
    with kb.connect(tmp_path / "kanban.db") as conn:
        topology = materialize_org_run(
            conn, plan, validation, board="default"
        )
        conn.execute(
            "UPDATE tasks SET body='tampered' WHERE id=?",
            (topology.tasks["runtime"].execution_id,),
        )
        conn.commit()

        with pytest.raises(ValueError, match="managed plan drift"):
            materialize_org_run(conn, plan, validation, board="default")


def test_materialize_rejects_an_unowned_idempotency_key_collision(tmp_path):
    plan = _plan()
    anchor_key = f"org-run:{plan.run_id}:anchor"
    with kb.connect(tmp_path / "kanban.db") as conn:
        unrelated_id = kb.create_task(
            conn,
            title="Unrelated pre-existing card",
            assignee="default",
            idempotency_key=anchor_key,
        )
        before = _counts(conn)

        with pytest.raises(ValueError, match="pre-existing OrgRun task key"):
            materialize_org_run(
                conn, plan, _validation(plan), board="default"
            )

        assert _counts(conn) == before
        unrelated = kb.get_task(conn, unrelated_id)
        assert unrelated.status == "ready"
        assert unrelated.assignee == "default"


def test_materialize_requires_explicit_adoption_for_legacy_key_collisions(
    tmp_path,
):
    legacy_plan = parse_execution_portfolio(
        _legacy_payload(run_id="local-run-001")
    )
    plan = _plan()
    with kb.connect(tmp_path / "kanban.db") as conn:
        legacy = create_org_run(
            conn,
            legacy_plan,
            validate_execution_portfolio(legacy_plan),
        )
        task_count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        integration = kb.get_task(conn, legacy.integration_id)
        integration_parents = kb.parent_ids(conn, legacy.integration_id)

        with pytest.raises(ValueError, match="explicit adoption"):
            materialize_org_run(
                conn, plan, _validation(plan), board="default"
            )

        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == task_count
        assert get_org_run(conn, plan.run_id) is None
        assert kb.get_task(conn, legacy.integration_id).assignee == integration.assignee
        assert kb.parent_ids(conn, legacy.integration_id) == integration_parents


def test_load_rejects_missing_anchor_provenance(tmp_path):
    plan = _plan()
    with kb.connect(tmp_path / "kanban.db") as conn:
        materialize_org_run(
            conn, plan, _validation(plan), board="default"
        )
        conn.execute(
            "DELETE FROM kanban_org_nodes WHERE run_id=? AND node_kind='anchor'",
            (plan.run_id,),
        )
        conn.commit()

        with pytest.raises(ValueError, match="incomplete stored topology"):
            load_org_run_topology(conn, plan.run_id)


@pytest.mark.parametrize("missing_kind", ["task_review", "global_review"])
def test_exact_replay_rejects_missing_required_review_provenance(
    tmp_path,
    missing_kind,
):
    plan = _plan()
    validation = _validation(plan)
    with kb.connect(tmp_path / "kanban.db") as conn:
        materialize_org_run(conn, plan, validation, board="default")
        conn.execute(
            "DELETE FROM kanban_org_nodes WHERE run_id=? AND node_kind=?",
            (plan.run_id, missing_kind),
        )
        conn.commit()

        with pytest.raises(ValueError, match="incomplete stored topology"):
            materialize_org_run(conn, plan, validation, board="default")


@pytest.mark.parametrize(
    ("field", "corrupt_value"),
    [
        ("node_kind", "execution"),
        ("logical_role", "leaf"),
        ("task_id", None),
    ],
)
def test_exact_replay_rejects_mutated_review_provenance_fields(
    tmp_path,
    field,
    corrupt_value,
):
    plan = _plan()
    validation = _validation(plan)
    with kb.connect(tmp_path / "kanban.db") as conn:
        materialize_org_run(conn, plan, validation, board="default")
        if field == "task_id":
            corrupt_value = kb.create_task(
                conn,
                title="Unrelated card",
                assignee="default",
                idempotency_key="unrelated-review-provenance",
            )
        conn.execute(
            f"UPDATE kanban_org_nodes SET {field}=? "
            "WHERE run_id=? AND node_kind='task_review'",
            (corrupt_value, plan.run_id),
        )
        conn.commit()

        with pytest.raises(ValueError, match="incomplete stored topology"):
            materialize_org_run(conn, plan, validation, board="default")


def test_materialize_rolls_back_all_rows_when_task_creation_fails(
    tmp_path, monkeypatch
):
    plan = _plan()
    real_create = kb.create_task
    calls = 0

    def fail_midway(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("injected create failure")
        return real_create(*args, **kwargs)

    fired_hooks: list[tuple[str, str]] = []
    monkeypatch.setattr(kb, "create_task", fail_midway)
    monkeypatch.setattr(
        kb,
        "_fire_kanban_lifecycle_hook",
        lambda event, task_id, **_fields: fired_hooks.append((event, task_id)),
    )
    with kb.connect(tmp_path / "kanban.db") as conn:
        with pytest.raises(RuntimeError, match="injected create failure"):
            materialize_org_run(
                conn, plan, _validation(plan), board="default"
            )

        assert _counts(conn) == (0, 0, 0, 0)
        assert conn.execute("SELECT COUNT(*) FROM task_events").fetchone()[0] == 0
        assert fired_hooks == []


def test_caller_owned_materialization_is_rejected_without_rows_or_hooks(
    tmp_path,
    monkeypatch,
):
    plan = _plan()
    fired_hooks: list[tuple[str, str]] = []
    monkeypatch.setattr(
        kb,
        "_fire_kanban_lifecycle_hook",
        lambda event, task_id, **_fields: fired_hooks.append((event, task_id)),
    )
    with kb.connect(tmp_path / "kanban.db") as conn:
        conn.execute("BEGIN IMMEDIATE")
        with pytest.raises(ValueError, match="existing transaction"):
            materialize_org_run(
                conn, plan, _validation(plan), board="default"
            )

        assert _counts(conn) == (0, 0, 0, 0)
        assert conn.execute("SELECT COUNT(*) FROM task_events").fetchone()[0] == 0
        assert fired_hooks == []
        assert conn.in_transaction is True
        conn.rollback()


def test_owned_materialization_commits_before_emitting_exactly_one_hook(
    tmp_path,
    monkeypatch,
):
    plan = _plan()
    observed_hooks: list[dict] = []

    with kb.connect(tmp_path / "kanban.db") as conn:
        def observe_hook(event, task_id, **fields):
            task = kb.get_task(conn, task_id)
            observed_hooks.append({
                "event": event,
                "task_id": task_id,
                "fields": fields,
                "in_transaction": conn.in_transaction,
                "task_status": task.status if task is not None else None,
                "counts": _counts(conn),
            })

        monkeypatch.setattr(kb, "_fire_kanban_lifecycle_hook", observe_hook)
        topology = materialize_org_run(
            conn, plan, _validation(plan), board="default"
        )

        assert conn.in_transaction is False
        assert len(observed_hooks) == 1
        hook = observed_hooks[0]
        assert hook["event"] == "kanban_task_completed"
        assert hook["task_id"] == topology.anchor_id
        assert hook["fields"]["board"] == "default"
        assert hook["fields"]["assignee"] == "orchestrator"
        assert isinstance(hook["fields"]["run_id"], int)
        assert hook["fields"]["summary"] == (
            "Local OrgRun plan accepted for materialization."
        )
        assert hook["in_transaction"] is False
        assert hook["task_status"] == "done"
        assert hook["counts"] == (6, 1, 1, 6)


def test_refresh_org_run_state_uses_durable_statuses_with_exact_precedence(
    tmp_path,
):
    plan = _plan()
    with kb.connect(tmp_path / "kanban.db") as conn:
        topology = materialize_org_run(
            conn,
            plan,
            _validation(plan),
            board="default",
            activate=False,
        )
        execution_id = topology.tasks["runtime"].execution_id

        assert refresh_org_run_state(conn, plan.run_id) == "materialized"

        conn.execute(
            "UPDATE tasks SET status='running' WHERE id=?",
            (execution_id,),
        )
        conn.commit()
        assert refresh_org_run_state(conn, plan.run_id) == "running"

        conn.execute("UPDATE tasks SET status='done' WHERE id=?", (execution_id,))
        conn.execute(
            "UPDATE tasks SET status='running' WHERE id=?",
            (topology.integration_id,),
        )
        conn.commit()
        assert refresh_org_run_state(conn, plan.run_id) == "integrating"

        conn.execute(
            "UPDATE tasks SET status='done' WHERE id=?",
            (topology.integration_id,),
        )
        conn.execute(
            "UPDATE tasks SET status='running' WHERE id=?",
            (topology.review_id,),
        )
        conn.commit()
        assert refresh_org_run_state(conn, plan.run_id) == "reviewing"

        conn.execute(
            "UPDATE tasks SET status='done' WHERE id=?",
            (topology.review_id,),
        )
        conn.execute(
            "UPDATE tasks SET status='done' WHERE id=?",
            (topology.finalization_id,),
        )
        conn.commit()
        assert refresh_org_run_state(conn, plan.run_id) == "reviewing"

        insert_report(
            conn,
            board_slug="default",
            report_type="org_run",
            subject_id=plan.run_id,
            terminal_run_id=None,
            source_version=1,
            report_json="{}",
            report_markdown="# Generic",
            generated_at=122,
            idempotency_key=f"org-run:{plan.run_id}:generic-report:v1",
        )
        assert refresh_org_run_state(conn, plan.run_id) == "reviewing"

        insert_report(
            conn,
            board_slug="default",
            report_type="org_run_final",
            subject_id=plan.run_id,
            terminal_run_id=None,
            source_version=1,
            report_json="{}",
            report_markdown="# Final",
            generated_at=123,
            idempotency_key=f"org-run:{plan.run_id}:final-report:v1",
        )
        assert refresh_org_run_state(conn, plan.run_id) == "completed"

        conn.execute(
            "UPDATE kanban_org_runs SET plan_version=2 WHERE run_id=?",
            (plan.run_id,),
        )
        conn.commit()
        assert refresh_org_run_state(conn, plan.run_id) == "reviewing"

        conn.execute(
            "UPDATE tasks SET status='blocked' WHERE id=?",
            (execution_id,),
        )
        conn.commit()
        assert refresh_org_run_state(conn, plan.run_id) == "blocked"

        conn.execute(
            "UPDATE kanban_org_runs SET state='cancelled' WHERE run_id=?",
            (plan.run_id,),
        )
        conn.commit()
        assert refresh_org_run_state(conn, plan.run_id) == "cancelled"


def test_materialize_persists_materialized_until_execution_really_starts(tmp_path):
    """Breaks if activation intent is persisted as running before any claim."""
    plan = _plan(risk="low", task_review=False, global_review=False)
    with kb.connect(tmp_path / "kanban.db") as conn:
        topology = materialize_org_run(
            conn, plan, _validation(plan), board="default"
        )
        execution_id = topology.tasks["runtime"].execution_id

        assert get_org_run(conn, plan.run_id).state == "materialized"
        assert refresh_org_run_state(conn, plan.run_id) == "materialized"
        assert kb.claim_task(conn, execution_id, claimer="state-test")
        assert get_org_run(conn, plan.run_id).state == "running"
        assert kb.block_task(
            conn,
            execution_id,
            reason="operator input",
            kind="needs_input",
        )
        assert get_org_run(conn, plan.run_id).state == "blocked"


def test_recurrence_routed_triage_is_a_blocked_org_run_state(tmp_path):
    """Breaks if human-intervention triage is misclassified as materialized."""
    plan = _plan(risk="low", task_review=False, global_review=False)
    with kb.connect(tmp_path / "kanban.db") as conn:
        topology = materialize_org_run(
            conn,
            plan,
            _validation(plan),
            board="default",
            activate=False,
        )
        execution_id = topology.tasks["runtime"].execution_id
        conn.execute("UPDATE tasks SET status='ready' WHERE id=?", (execution_id,))
        conn.commit()
        assert kb.block_task(
            conn, execution_id, reason="same capability", kind="capability"
        )
        assert kb.unblock_task(conn, execution_id)
        assert kb.block_task(
            conn, execution_id, reason="same capability", kind="capability"
        )

        assert kb.get_task(conn, execution_id).status == "triage"
        assert refresh_org_run_state(conn, plan.run_id) == "blocked"


def test_complete_task_projects_local_reports_after_org_run_finalization(tmp_path):
    """The completion path derives reports after, never instead of, local state."""
    plan = _plan(risk="low", task_review=False, global_review=False)
    with kb.connect(tmp_path / "kanban.db") as conn:
        topology = materialize_org_run(
            conn, plan, _validation(plan), board="default"
        )
        for task_id, summary in (
            (topology.tasks["runtime"].execution_id, "runtime complete"),
            (topology.integration_id, "integration complete"),
            (topology.finalization_id, "finalization complete"),
        ):
            assert kb.claim_task(conn, task_id, claimer="report-projection")
            assert kb.complete_task(conn, task_id, summary=summary)

        reports = conn.execute(
            "SELECT report_type, subject_id FROM kanban_reports ORDER BY id"
        ).fetchall()
        assert ("task", topology.tasks["runtime"].execution_id) in [
            (row["report_type"], row["subject_id"]) for row in reports
        ]
        assert ("org_run_final", plan.run_id) in [
            (row["report_type"], row["subject_id"]) for row in reports
        ]
        assert get_org_run(conn, plan.run_id).state == "completed"


def test_final_report_projection_failure_keeps_completed_org_run_in_review(tmp_path, monkeypatch):
    """A projection retry must not turn durable finalization into completion."""
    plan = _plan(risk="low", task_review=False, global_review=False)
    with kb.connect(tmp_path / "kanban.db") as conn:
        topology = materialize_org_run(
            conn, plan, _validation(plan), board="default"
        )
        for task_id in (
            topology.tasks["runtime"].execution_id,
            topology.integration_id,
        ):
            assert kb.claim_task(conn, task_id, claimer="report-projection-failure")
            assert kb.complete_task(conn, task_id, summary="complete")

        monkeypatch.setattr(
            "hermes_cli.kanban_reports.project_after_task_completion",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("projection failed")),
        )
        assert kb.claim_task(conn, topology.finalization_id, claimer="report-projection-failure")
        assert kb.complete_task(conn, topology.finalization_id, summary="final complete")

        assert kb.get_task(conn, topology.finalization_id).status == "done"
        assert get_org_run(conn, plan.run_id).state == "reviewing"
        assert any(
            event.kind == "report_projection_failed"
            for event in kb.list_events(conn, topology.finalization_id)
        )


def test_final_report_projection_failure_preserves_a_blocked_active_gate(tmp_path, monkeypatch):
    """A finalization retry cannot overwrite the canonical blocked state."""
    plan = _plan(risk="low", task_review=False, global_review=False)
    with kb.connect(tmp_path / "kanban.db") as conn:
        topology = materialize_org_run(
            conn, plan, _validation(plan), board="default"
        )
        execution_id = topology.tasks["runtime"].execution_id
        for task_id in (execution_id, topology.integration_id):
            assert kb.claim_task(conn, task_id, claimer="blocked-projection-failure")
            assert kb.complete_task(conn, task_id, summary="complete")

        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET status='blocked' WHERE id = ?", (execution_id,))
            kb._append_event(conn, execution_id, "blocked", {"reason": "manual gate"})
        assert refresh_org_run_state(conn, plan.run_id) == "blocked"
        monkeypatch.setattr(
            "hermes_cli.kanban_reports.project_after_task_completion",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("projection failed")),
        )
        assert kb.claim_task(conn, topology.finalization_id, claimer="blocked-projection-failure")
        assert kb.complete_task(conn, topology.finalization_id, summary="final complete")

        assert get_org_run(conn, plan.run_id).state == "blocked"


def _legacy_payload(*, run_id: str = "legacy-run-001") -> dict:
    return {
        "schema": "hades.execution-portfolio.v1",
        "org_run_id": run_id,
        "project_id": f"proj-{run_id}",
        "repository_id": "repo-1",
        "workspace_binding_id": f"binding-{run_id}-must-not-be-read",
        "base_commit": "b" * 40,
        "tasks": [
            {
                "remote_task_id": "runtime",
                "work_item_id": f"work-{run_id}",
                "title": "Legacy runtime",
                "body": "Implement the bounded change.",
                "assignee": "default",
                "priority": 10,
                "risk": "high",
                "depends_on": [],
                "write_scope": ["hermes_cli/legacy.py"],
            }
        ],
    }


def test_adopt_legacy_org_run_records_provenance_without_recreating_cards_or_reading_binding(
    tmp_path, monkeypatch
):
    with kb.connect(tmp_path / "kanban.db") as conn:
        legacy_plan = parse_execution_portfolio(_legacy_payload())
        legacy = create_org_run(
            conn, legacy_plan, validate_execution_portfolio(legacy_plan)
        )
        review_ids = [
            legacy.remote_tasks["runtime"].review_id,
            legacy.review_id,
        ]
        placeholders = ",".join("?" for _ in review_ids)
        conn.execute(
            f"UPDATE tasks SET assignee='default', "
            f"skills='[\"requesting-code-review\"]', status='blocked' "
            f"WHERE id IN ({placeholders})",
            review_ids,
        )
        conn.commit()
        original_task_ids = {
            row["id"] for row in conn.execute("SELECT id FROM tasks")
        }
        original_task_count = len(original_task_ids)
        original_completed_events = [
            (row["id"], row["task_id"])
            for row in conn.execute(
                "SELECT id, task_id FROM task_events WHERE kind='completed' ORDER BY id"
            )
        ]

        monkeypatch.setattr(
            kb,
            "get_remote_link",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("backend binding was read")
            ),
        )
        topology = adopt_legacy_org_run(
            conn, "legacy-run-001", board="default"
        )

        assert topology.anchor_id == legacy.anchor_id
        assert topology.tasks["runtime"].execution_id == legacy.remote_tasks["runtime"].execution_id
        assert topology.tasks["runtime"].review_id == legacy.remote_tasks["runtime"].review_id
        assert topology.integration_id == legacy.integration_id
        assert topology.review_id == legacy.review_id
        assert topology.finalization_id == legacy.synthesis_id
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == original_task_count
        assert {
            row["id"] for row in conn.execute("SELECT id FROM tasks")
        } == original_task_ids
        assert [
            (row["id"], row["task_id"])
            for row in conn.execute(
                "SELECT id, task_id FROM task_events WHERE kind='completed' ORDER BY id"
            )
        ] == original_completed_events
        assert {node.node_kind for node in list_org_nodes(conn, "legacy-run-001")} >= {
            "anchor",
            "execution",
            "task_review",
            "legacy_gate",
            "integration",
            "global_review",
            "finalization",
        }
        assert all(
            kb.get_task(conn, review_id).assignee == "reviewer"
            and kb.get_task(conn, review_id).skills == ["hierarchical-development"]
            for review_id in review_ids
        )
        assert get_org_run(conn, "legacy-run-001").state == "blocked"
        assert adopt_legacy_org_run(
            conn, "legacy-run-001", board="default"
        ) == topology


def test_adoption_rejects_malformed_raw_skills(tmp_path):
    """Breaks if malformed JSON is silently sealed as an absent skill contract."""
    with kb.connect(tmp_path / "kanban.db") as conn:
        legacy_plan = parse_execution_portfolio(_legacy_payload())
        legacy = create_org_run(
            conn, legacy_plan, validate_execution_portfolio(legacy_plan)
        )
        conn.execute(
            "UPDATE tasks SET skills='{\"broken\"' WHERE id=?",
            (legacy.remote_tasks["runtime"].execution_id,),
        )
        conn.commit()

        with pytest.raises(ValueError, match="invalid skills"):
            adopt_legacy_org_run(conn, "legacy-run-001", board="default")

        assert get_org_run(conn, "legacy-run-001") is None


@pytest.mark.parametrize(
    "mutation",
    ("assignee", "title", "body", "skills", "malformed_skills", "parents"),
)
def test_adopted_replay_rejects_live_contract_and_parent_drift(
    tmp_path,
    mutation,
):
    """Breaks if adopted replay trusts mutable cards instead of its sealed contract."""
    with kb.connect(tmp_path / "kanban.db") as conn:
        legacy_plan = parse_execution_portfolio(_legacy_payload())
        create_org_run(
            conn, legacy_plan, validate_execution_portfolio(legacy_plan)
        )
        topology = adopt_legacy_org_run(
            conn, "legacy-run-001", board="default"
        )
        execution_id = topology.tasks["runtime"].execution_id
        if mutation == "parents":
            conn.execute(
                "INSERT INTO task_links (parent_id, child_id) VALUES (?, ?)",
                (topology.anchor_id, execution_id),
            )
        elif mutation == "malformed_skills":
            conn.execute(
                "UPDATE tasks SET skills='[\"unterminated\"' WHERE id=?",
                (execution_id,),
            )
        else:
            changed = {
                "assignee": "reviewer",
                "title": "tampered title",
                "body": "tampered body",
                "skills": json.dumps(["tampered-skill"]),
            }[mutation]
            conn.execute(
                f"UPDATE tasks SET {mutation}=? WHERE id=?",
                (changed, execution_id),
            )
        conn.commit()

        with pytest.raises(ValueError, match="managed plan drift"):
            adopt_legacy_org_run(conn, "legacy-run-001", board="default")


@pytest.mark.parametrize("tamper", ("title", "parents"))
def test_adopted_replay_rejects_rehashed_historical_contract_drift(
    tmp_path,
    tamper,
):
    """Breaks if rewriting both the snapshot and live row bypasses node hashes."""
    with kb.connect(tmp_path / "kanban.db") as conn:
        legacy_plan = parse_execution_portfolio(_legacy_payload())
        create_org_run(
            conn, legacy_plan, validate_execution_portfolio(legacy_plan)
        )
        topology = adopt_legacy_org_run(
            conn, "legacy-run-001", board="default"
        )
        execution_id = topology.tasks["runtime"].execution_id
        row = conn.execute(
            "SELECT plan_json FROM kanban_org_plan_versions "
            "WHERE run_id=? AND plan_version=1",
            ("legacy-run-001",),
        ).fetchone()
        payload = json.loads(row["plan_json"])
        execution = next(
            node for node in payload["nodes"]
            if node["task_id"] == execution_id
        )
        if tamper == "title":
            execution["task_contract"]["title"] = "rewritten together"
            conn.execute(
                "UPDATE tasks SET title='rewritten together' WHERE id=?",
                (execution_id,),
            )
        else:
            conn.execute(
                "INSERT INTO task_links (parent_id, child_id) VALUES (?, ?)",
                (topology.anchor_id, execution_id),
            )
            execution["parent_task_ids"] = kb.parent_ids(
                conn, execution_id
            )
        plan_json = json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        plan_hash = hashlib.sha256(plan_json.encode("utf-8")).hexdigest()
        conn.execute(
            "UPDATE kanban_org_plan_versions "
            "SET plan_json=?, plan_hash=? WHERE run_id=? AND plan_version=1",
            (plan_json, plan_hash, "legacy-run-001"),
        )
        conn.execute(
            "UPDATE kanban_org_runs SET plan_hash=? WHERE run_id=?",
            (plan_hash, "legacy-run-001"),
        )
        conn.commit()

        with pytest.raises(ValueError, match="incomplete stored topology"):
            adopt_legacy_org_run(conn, "legacy-run-001", board="default")


def test_dispatch_blocks_adopted_live_role_drift(tmp_path, monkeypatch):
    """Breaks if dispatch routes an adopted card after its live role changed."""
    with kb.connect(tmp_path / "kanban.db") as conn:
        legacy_plan = parse_execution_portfolio(_legacy_payload())
        create_org_run(
            conn, legacy_plan, validate_execution_portfolio(legacy_plan)
        )
        topology = adopt_legacy_org_run(
            conn, "legacy-run-001", board="default"
        )
        execution_id = topology.tasks["runtime"].execution_id
        conn.execute(
            "UPDATE tasks SET assignee='reviewer' WHERE id=?",
            (execution_id,),
        )
        conn.commit()
        monkeypatch.setattr(
            "hermes_cli.profiles.profile_exists",
            lambda _profile: True,
        )
        monkeypatch.setattr(
            kb,
            "_resolve_worker_role_route",
            lambda role: {"role": role},
        )

        result = kb.dispatch_once(
            conn,
            spawn_fn=lambda *_args, **_kwargs: pytest.fail(
                "drifted adopted card spawned"
            ),
            board="default",
        )

        assert result.auto_blocked == [execution_id]
        assert kb.get_task(conn, execution_id).status == "blocked"


def test_adoption_does_not_absorb_a_run_whose_id_extends_the_requested_prefix(
    tmp_path,
):
    with kb.connect(tmp_path / "kanban.db") as conn:
        for run_id in ("legacy", "legacy:nested"):
            legacy_plan = parse_execution_portfolio(
                _legacy_payload(run_id=run_id)
            )
            create_org_run(
                conn,
                legacy_plan,
                validate_execution_portfolio(legacy_plan),
            )

        topology = adopt_legacy_org_run(conn, "legacy", board="default")

        assert set(topology.tasks) == {"runtime"}


@pytest.mark.parametrize(
    ("missing_profile", "missing_route", "reason"),
    [
        (True, False, "profile_unavailable: leaf"),
        (False, True, "role_route_unavailable: leaf"),
    ],
)
def test_dispatch_blocks_managed_ready_task_once_when_role_is_unavailable(
    tmp_path, monkeypatch, missing_profile, missing_route, reason
):
    plan = _plan(risk="low", task_review=False)
    with kb.connect(tmp_path / "kanban.db") as conn:
        topology = materialize_org_run(
            conn, plan, _validation(plan), board="default"
        )
        task_id = topology.tasks["runtime"].execution_id
        monkeypatch.setattr(
            "hermes_cli.profiles.profile_exists",
            lambda profile: not (missing_profile and profile == "leaf"),
        )
        monkeypatch.setattr(
            kb,
            "_resolve_worker_role_route",
            lambda profile: None if missing_route and profile == "leaf" else object(),
        )
        spawned: list[str] = []

        kb.dispatch_once(
            conn,
            spawn_fn=lambda task, _workspace: spawned.append(task.id),
            board="default",
        )
        kb.dispatch_once(
            conn,
            spawn_fn=lambda task, _workspace: spawned.append(task.id),
            board="default",
        )

        task = kb.get_task(conn, task_id)
        blocked = [
            event for event in kb.list_events(conn, task_id)
            if event.kind == "blocked"
        ]
        assert task.status == "blocked"
        assert task.block_kind == "capability"
        assert spawned == []
        assert len(blocked) == 1
        assert blocked[0].payload["reason"] == reason
        assert blocked[0].payload["kind"] == "capability"


def test_dispatch_dry_run_reports_unavailable_managed_role_without_mutation(
    tmp_path, monkeypatch
):
    plan = _plan(risk="low", task_review=False)
    with kb.connect(tmp_path / "kanban.db") as conn:
        topology = materialize_org_run(
            conn, plan, _validation(plan), board="default"
        )
        task_id = topology.tasks["runtime"].execution_id
        monkeypatch.setattr(
            "hermes_cli.profiles.profile_exists",
            lambda profile: profile != "leaf",
        )

        result = kb.dispatch_once(conn, dry_run=True, board="default")

        assert kb.get_task(conn, task_id).status == "ready"
        assert not any(
            event.kind == "blocked" for event in kb.list_events(conn, task_id)
        )
        assert task_id in result.auto_blocked


@pytest.mark.parametrize(
    ("profile_raises", "reason"),
    [
        (True, "profile_unavailable: leaf"),
        (False, "role_route_unavailable: leaf"),
    ],
)
def test_dispatch_contains_managed_role_resolution_errors_per_card(
    tmp_path, monkeypatch, profile_raises, reason
):
    plan = _plan(risk="low", task_review=False)
    with kb.connect(tmp_path / "kanban.db") as conn:
        topology = materialize_org_run(
            conn, plan, _validation(plan), board="default"
        )
        task_id = topology.tasks["runtime"].execution_id

        def profile_exists(_profile):
            if profile_raises:
                raise ValueError("damaged profile state")
            return True

        def resolve_route(_profile):
            raise ValueError("damaged routing state")

        monkeypatch.setattr(
            "hermes_cli.profiles.profile_exists",
            profile_exists,
        )
        monkeypatch.setattr(kb, "_resolve_worker_role_route", resolve_route)

        kb.dispatch_once(conn, spawn_fn=lambda *_args: 1, board="default")

        task = kb.get_task(conn, task_id)
        blocked = [
            event for event in kb.list_events(conn, task_id)
            if event.kind == "blocked"
        ]
        assert task.status == "blocked"
        assert len(blocked) == 1
        assert blocked[0].payload["reason"] == reason


def test_dispatch_reads_managed_profile_availability_only_once(
    tmp_path, monkeypatch
):
    plan = _plan(risk="low", task_review=False)
    with kb.connect(tmp_path / "kanban.db") as conn:
        topology = materialize_org_run(
            conn, plan, _validation(plan), board="default"
        )
        task_id = topology.tasks["runtime"].execution_id
        calls = 0

        def profile_exists(_profile):
            nonlocal calls
            calls += 1
            if calls > 1:
                raise ValueError("profile state changed during one preflight")
            return True

        monkeypatch.setattr(
            "hermes_cli.profiles.profile_exists",
            profile_exists,
        )
        monkeypatch.setattr(
            kb,
            "_resolve_worker_role_route",
            lambda _profile: object(),
        )

        result = kb.dispatch_once(conn, dry_run=True, board="default")

        assert calls == 1
        assert [spawned[0] for spawned in result.spawned] == [task_id]


def test_dispatch_never_applies_default_assignee_to_unassigned_managed_task(
    tmp_path, monkeypatch
):
    plan = _plan(risk="low", task_review=False)
    route = DelegationProfile("test", "model", None, 10, 60)
    with kb.connect(tmp_path / "kanban.db") as conn:
        topology = materialize_org_run(
            conn, plan, _validation(plan), board="default"
        )
        task_id = topology.tasks["runtime"].execution_id
        conn.execute("UPDATE tasks SET assignee=NULL WHERE id=?", (task_id,))
        conn.commit()
        monkeypatch.setattr(
            "hermes_cli.profiles.profile_exists",
            lambda _profile: True,
        )
        monkeypatch.setattr(
            kb,
            "_resolve_worker_role_route",
            lambda _profile: route,
        )
        spawned: list[str] = []

        kb.dispatch_once(
            conn,
            spawn_fn=lambda task, _workspace: spawned.append(task.id),
            default_assignee="leaf",
            board="default",
        )

        task = kb.get_task(conn, task_id)
        assert task.status == "blocked"
        assert task.assignee is None
        assert task.block_kind == "capability"
        assert spawned == []
        assert not any(
            event.kind == "assigned" for event in kb.list_events(conn, task_id)
        )


def test_dispatch_capability_blocks_an_unassigned_managed_review(
    tmp_path,
    monkeypatch,
):
    plan = _plan()
    with kb.connect(tmp_path / "kanban.db") as conn:
        topology = materialize_org_run(
            conn, plan, _validation(plan), board="default"
        )
        review_id = topology.tasks["runtime"].review_id
        assert review_id is not None
        conn.execute(
            "UPDATE tasks SET status='blocked' WHERE id<>?",
            (review_id,),
        )
        conn.execute(
            "UPDATE tasks SET status='review', assignee=NULL WHERE id=?",
            (review_id,),
        )
        conn.commit()
        monkeypatch.setattr(
            "hermes_cli.profiles.profile_exists",
            lambda _profile: True,
        )
        spawned: list[str] = []

        result = kb.dispatch_once(
            conn,
            spawn_fn=lambda task, _workspace: spawned.append(task.id),
            default_assignee="reviewer",
            board="default",
        )

        review = kb.get_task(conn, review_id)
        assert review.status == "blocked"
        assert review.assignee is None
        assert review.block_kind == "capability"
        assert review_id not in result.skipped_unassigned
        assert spawned == []


def test_dispatch_pins_validated_role_route_through_spawn(
    tmp_path, monkeypatch
):
    plan = _plan(risk="low", task_review=False)
    route = DelegationProfile("provider-a", "model-a", "high", 20, 120)
    with kb.connect(tmp_path / "kanban.db") as conn:
        topology = materialize_org_run(
            conn, plan, _validation(plan), board="default"
        )
        task_id = topology.tasks["runtime"].execution_id
        route_reads = 0
        captured_routes: list[DelegationProfile | None] = []

        def resolve_route(_profile):
            nonlocal route_reads
            route_reads += 1
            return route if route_reads == 1 else None

        def spawn(
            _task,
            _workspace,
            *,
            board=None,
            routed_profile=None,
        ):
            captured_routes.append(routed_profile)
            return 123

        monkeypatch.setattr(
            "hermes_cli.profiles.profile_exists",
            lambda _profile: True,
        )
        monkeypatch.setattr(kb, "_resolve_worker_role_route", resolve_route)
        monkeypatch.setattr(kb, "_default_spawn", spawn)

        result = kb.dispatch_once(conn, board="default")

        assert [item[0] for item in result.spawned] == [task_id]
        assert route_reads == 1
        assert captured_routes == [route]


def test_dispatch_does_not_retry_spawn_without_the_pinned_route_on_type_error(
    tmp_path, monkeypatch
):
    plan = _plan(risk="low", task_review=False)
    route = DelegationProfile("provider-a", "model-a", None, 20, 120)
    with kb.connect(tmp_path / "kanban.db") as conn:
        materialize_org_run(
            conn, plan, _validation(plan), board="default"
        )
        captured_routes: list[DelegationProfile | None] = []

        def spawn(
            _task,
            _workspace,
            *,
            routed_profile=None,
        ):
            captured_routes.append(routed_profile)
            raise TypeError("spawn implementation failed")

        monkeypatch.setattr(
            "hermes_cli.profiles.profile_exists",
            lambda _profile: True,
        )
        monkeypatch.setattr(
            kb,
            "_resolve_worker_role_route",
            lambda _profile: route,
        )
        monkeypatch.setattr(kb, "_default_spawn", spawn)

        kb.dispatch_once(conn, board="default", failure_limit=5)

        assert captured_routes == [route]


def test_dispatch_blocks_adopted_review_instead_of_falling_back_to_default(
    tmp_path, monkeypatch
):
    with kb.connect(tmp_path / "kanban.db") as conn:
        legacy_plan = parse_execution_portfolio(_legacy_payload())
        legacy = create_org_run(
            conn, legacy_plan, validate_execution_portfolio(legacy_plan)
        )
        topology = adopt_legacy_org_run(conn, "legacy-run-001", board="default")
        review_id = topology.tasks["runtime"].review_id
        conn.execute(
            "UPDATE tasks SET assignee='default', status='review', skills=? WHERE id=?",
            (json.dumps(["custom-review"]), review_id),
        )
        conn.commit()
        monkeypatch.setattr(
            "hermes_cli.profiles.profile_exists",
            lambda profile: profile != "default",
        )
        monkeypatch.setattr(kb, "_resolve_worker_role_route", lambda _profile: None)
        spawned: list[str] = []

        kb.dispatch_once(
            conn,
            spawn_fn=lambda task, _workspace: spawned.append(task.id),
            board="default",
        )
        kb.dispatch_once(
            conn,
            spawn_fn=lambda task, _workspace: spawned.append(task.id),
            board="default",
        )

        review = kb.get_task(conn, review_id)
        events = kb.list_events(conn, review_id)
        assert review.status == "blocked"
        assert review.assignee == "default"
        assert spawned == []
        assert [event.kind for event in events].count("blocked") == 1
        assert not any(event.kind == "assigned" for event in events)
        assert get_org_run(conn, "legacy-run-001") is not None


def test_reclaim_refreshes_owning_org_run_state(tmp_path):
    """Breaks if reclaim leaves the cached run state stuck at running."""
    plan = _plan(risk="low", task_review=False, global_review=False)
    with kb.connect(tmp_path / "kanban.db") as conn:
        topology = materialize_org_run(
            conn, plan, _validation(plan), board="default"
        )
        execution_id = topology.tasks["runtime"].execution_id
        assert kb.claim_task(conn, execution_id, claimer="org-test") is not None
        assert get_org_run(conn, plan.run_id).state == "running"

        assert kb.reclaim_task(
            conn,
            execution_id,
            reason="operator retry",
            signal_fn=lambda *_args: None,
        )

        assert get_org_run(conn, plan.run_id).state == "materialized"


@pytest.mark.parametrize(
    ("failure_limit", "expected_state"),
    ((2, "materialized"), (1, "blocked")),
)
def test_record_task_failure_refreshes_owning_org_run_state(
    tmp_path,
    failure_limit,
    expected_state,
):
    """Breaks if a failed managed attempt leaves stale cached run state."""
    plan = _plan(
        run_id=f"failure-refresh-{failure_limit}",
        risk="low",
        task_review=False,
        global_review=False,
    )
    with kb.connect(tmp_path / "kanban.db") as conn:
        topology = materialize_org_run(
            conn, plan, _validation(plan), board="default"
        )
        execution_id = topology.tasks["runtime"].execution_id
        assert kb.claim_task(conn, execution_id, claimer="org-test") is not None

        kb._record_task_failure(
            conn,
            execution_id,
            "spawn failed",
            outcome="spawn_failed",
            failure_limit=failure_limit,
            release_claim=True,
            end_run=True,
        )

        assert get_org_run(conn, plan.run_id).state == expected_state


def test_dashboard_direct_status_transition_refreshes_owning_org_run_state(
    tmp_path,
):
    """Breaks if dashboard drag-drop leaves the cached run state stale."""
    from plugins.kanban.dashboard.plugin_api import _set_status_direct

    plan = _plan(risk="low", task_review=False, global_review=False)
    with kb.connect(tmp_path / "kanban.db") as conn:
        topology = materialize_org_run(
            conn, plan, _validation(plan), board="default"
        )
        execution_id = topology.tasks["runtime"].execution_id
        assert kb.claim_task(conn, execution_id, claimer="org-test") is not None
        assert get_org_run(conn, plan.run_id).state == "running"

        assert _set_status_direct(conn, execution_id, "ready")

        assert get_org_run(conn, plan.run_id).state == "materialized"


def test_cancelled_org_run_cannot_dispatch_ready_managed_cards(
    tmp_path,
    monkeypatch,
):
    """Breaks if cancellation is report-only and ready cards can still launch."""
    plan = _plan(risk="low", task_review=False, global_review=False)
    with kb.connect(tmp_path / "kanban.db") as conn:
        topology = materialize_org_run(
            conn, plan, _validation(plan), board="default"
        )
        execution_id = topology.tasks["runtime"].execution_id
        set_org_run_state(conn, plan.run_id, "cancelled", now=123)
        set_org_run_state(conn, plan.run_id, "cancelled", now=124)
        monkeypatch.setattr(
            "hermes_cli.profiles.profile_exists",
            lambda _profile: True,
        )
        monkeypatch.setattr(
            kb,
            "_resolve_worker_role_route",
            lambda role: {"role": role},
        )

        result = kb.dispatch_once(
            conn,
            spawn_fn=lambda *_args, **_kwargs: pytest.fail(
                "cancelled OrgRun card spawned"
            ),
            board="default",
        )

        assert result.auto_blocked == [execution_id]
        assert get_org_run(conn, plan.run_id).state == "cancelled"
        reports = conn.execute(
            "SELECT report_type FROM kanban_reports "
            "WHERE subject_id=? ORDER BY id",
            (plan.run_id,),
        ).fetchall()
        assert [row["report_type"] for row in reports] == [
            "org_run_cancelled"
        ]
