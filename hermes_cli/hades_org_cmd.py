"""Local-only commands for validating and materializing Hades OrgRuns."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

from hermes_cli import kanban_db as kb
from hermes_cli.hierarchical_execution import (
    parse_execution_portfolio,
    validate_execution_portfolio,
)
from hermes_cli.kanban_portfolio import OrgRunCreated, RemoteTaskTopology, create_org_run
from hermes_cli.hades_kanban_sync import SYNC_MODES, sync_remote_kanban
from hermes_cli.kanban_swarm import latest_blackboard
from hermes_cli.hades_coordination import snapshot_org_run


def _read_json(path: str) -> dict[str, Any]:
    raw = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("portfolio JSON must be an object")
    return raw


def _error(code: str, exc: Exception) -> dict[str, Any]:
    return {"status": "error", "code": code, "message": str(exc)[:300]}


def validate_portfolio_file(path: str) -> tuple[dict[str, Any], int]:
    try:
        plan = parse_execution_portfolio(_read_json(path))
        validation = validate_execution_portfolio(plan)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        return _error("invalid_portfolio", exc), 2
    return {
        "status": "valid",
        "schema": plan.schema,
        "org_run_id": plan.org_run_id,
        "task_count": len(plan.tasks),
        "conflict_count": len(validation.conflicts),
    }, 0


def materialize_portfolio_file(
    path: str,
    *,
    board: str | None,
) -> tuple[dict[str, Any], int]:
    try:
        plan = parse_execution_portfolio(_read_json(path))
        validation = validate_execution_portfolio(plan)
        with kb.connect(board=board) as conn:
            created = create_org_run(conn, plan, validation, board=board)
            topology = {
                "anchor_id": created.anchor_id,
                "remote_tasks": {
                    key: {
                        "anchor_id": value.anchor_id,
                        "execution_id": value.execution_id,
                        "review_id": value.review_id,
                        "integration_ready_id": value.integration_ready_id,
                        "completion_id": value.completion_id,
                        "work_item_id": value.work_item_id,
                    }
                    for key, value in created.remote_tasks.items()
                },
                "integration_id": created.integration_id,
                "review_id": created.review_id,
                "synthesis_id": created.synthesis_id,
            }
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        return _error("invalid_portfolio", exc), 2
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        return _error("org_run_materialization_failed", exc), 1
    return {
        "status": "materialized",
        "org_run_id": plan.org_run_id,
        "topology": topology,
    }, 0


def show_org_run(
    org_run_id: str,
    *,
    board: str | None,
) -> tuple[dict[str, Any], int]:
    try:
        with kb.connect(board=board) as conn:
            row = conn.execute(
                "SELECT id FROM tasks WHERE idempotency_key = ? "
                "AND status != 'archived' LIMIT 1",
                (f"org-run:{org_run_id}:anchor",),
            ).fetchone()
            if row is None:
                return _error("org_run_not_found", ValueError(org_run_id)), 1
            topology = latest_blackboard(conn, row["id"]).get("topology")
            if not isinstance(topology, dict):
                return _error("org_run_topology_missing", ValueError(org_run_id)), 1
            # Keep the CLI output useful without creating a second scheduler:
            # phase is derived from durable Kanban task state only.
            created = OrgRunCreated(
                anchor_id=str(topology["anchor_id"]),
                remote_tasks={
                    key: RemoteTaskTopology(**value)
                    for key, value in topology["remote_tasks"].items()
                },
                integration_id=str(topology["integration_id"]),
                review_id=str(topology["review_id"]),
                synthesis_id=str(topology["synthesis_id"]),
            )
            snapshot = snapshot_org_run(conn, org_run_id, created)
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        return _error("org_run_show_failed", exc), 1
    return {
        "status": "ok",
        "org_run_id": org_run_id,
        "topology": topology,
        "phase": snapshot.phase,
        "complete": snapshot.complete,
        "blocked": snapshot.blocked,
        "dispatchable": list(snapshot.dispatchable),
    }, 0


def sync_kanban(*, board: str | None, mode: str, project_id: str | None = None) -> tuple[dict[str, Any], int]:
    """Synchronize remote work items into the selected local board."""
    if mode not in SYNC_MODES:
        return _error("invalid_sync_mode", ValueError(mode)), 2
    if mode == "off":
        return {"status": "ok", "mode": mode, "pulled": 0}, 0
    try:
        from hermes_cli import hades_backend_runtime as runtime

        agent = runtime.current_agent()
        if agent is None:
            return _error("not_configured", ValueError("Hades backend is not configured")), 1
        selected_project = str(project_id or agent.project_id).strip()
        client = runtime.plugin_work_items_client_from_config()
        try:
            with kb.connect(board=board) as conn:
                result = sync_remote_kanban(
                    conn,
                    client,
                    project_id=selected_project,
                    mode=mode,
                )
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()
    except Exception as exc:  # pragma: no cover - CLI boundary
        return _error("kanban_sync_failed", exc), 1
    return {
        "status": "ok",
        "mode": result.mode,
        "project_id": selected_project,
        "pulled": result.pulled,
        "created": result.created,
        "existing": result.existing,
        "skipped": result.skipped,
    }, 0


def build_parser(subparsers, *, cmd_org: Callable[[argparse.Namespace], int]) -> None:
    parser = subparsers.add_parser(
        "org",
        help="Validate and materialize local Hades OrgRuns",
    )
    sub = parser.add_subparsers(dest="org_action")
    validate = sub.add_parser("validate", help="Validate a portfolio JSON file")
    validate.add_argument("portfolio")
    validate.add_argument("--json", action="store_true")
    validate.set_defaults(func=cmd_org)

    materialize = sub.add_parser("materialize", help="Materialize a portfolio in local Kanban")
    materialize.add_argument("portfolio")
    materialize.add_argument("--board", default=None)
    materialize.add_argument("--json", action="store_true")
    materialize.set_defaults(func=cmd_org)

    show = sub.add_parser("show", help="Show a materialized OrgRun")
    show.add_argument("org_run_id")
    show.add_argument("--board", default=None)
    show.add_argument("--json", action="store_true")
    show.set_defaults(func=cmd_org)

    sync = sub.add_parser("sync", help="Optionally pull backend work items into local Kanban")
    sync.add_argument("--mode", choices=sorted(SYNC_MODES), default="off")
    sync.add_argument("--project-id", default=None)
    sync.add_argument("--board", default=None)
    sync.add_argument("--json", action="store_true")
    sync.set_defaults(func=cmd_org)


def org_command(args: argparse.Namespace) -> int:
    action = getattr(args, "org_action", None)
    if action == "validate":
        result, code = validate_portfolio_file(args.portfolio)
    elif action == "materialize":
        result, code = materialize_portfolio_file(args.portfolio, board=args.board)
    elif action == "show":
        result, code = show_org_run(args.org_run_id, board=args.board)
    elif action == "sync":
        result, code = sync_kanban(
            board=args.board,
            mode=args.mode,
            project_id=args.project_id,
        )
    else:
        print("usage: hermes org <validate|materialize|show>")
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return code
