"""Local-only commands for versioned Hades implementation-plan OrgRuns."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
from typing import Any, Callable

from hermes_cli import kanban_db as kb
from hermes_cli.agentic_org_run import (
    adopt_legacy_org_run,
    apply_org_run_amendment,
    load_org_run_topology,
    materialize_org_run,
)
from hermes_cli.config import read_raw_config
from hermes_cli.implementation_plan import (
    parse_implementation_amendment,
    parse_implementation_plan,
    validate_implementation_plan,
)
from hermes_cli.org_run_store import (
    get_org_run,
    list_org_nodes,
    refresh_org_run_state,
)
from tools.delegation_routing import load_delegation_routing, resolve_role_profile


_SYNC_DISABLED = {
    "state": "unsupported",
    "code": "agentic_kanban_has_no_remote_sync",
    "retryable": False,
}


class _BoardWorkspaceMissing(ValueError):
    """Selected board does not identify a usable local Git repository."""


def _read_json(path: str) -> dict[str, Any]:
    raw = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("JSON input must be an object")
    return raw


def _error(code: str, exc: Exception) -> dict[str, Any]:
    return {"status": "error", "code": code, "message": str(exc)[:300]}


def _repository_for_board(board: str | None) -> Path:
    slug = board or kb.get_current_board()
    raw = str(kb.read_board_metadata(slug).get("default_workdir") or "").strip()
    path = Path(raw).expanduser().resolve()
    if not raw or not (path / ".git").exists():
        raise _BoardWorkspaceMissing("selected board has no Git default_workdir")
    return path


def _role_route_exists(role: str) -> bool:
    routing = load_delegation_routing(read_raw_config())
    return resolve_role_profile(routing, role) is not None


def _validated_plan(path: str, *, board: str | None):
    plan = parse_implementation_plan(_read_json(path))
    validation = validate_implementation_plan(
        plan,
        repository=_repository_for_board(board),
        profile_exists=_role_route_exists,
        role_route_exists=_role_route_exists,
    )
    return plan, validation


def _topology_payload(topology) -> dict[str, Any]:
    return asdict(topology)


def _report_ids(conn, run_id: str) -> list[int]:
    """Read durable report identities without loading report projection code."""
    rows = conn.execute(
        "SELECT id FROM kanban_reports WHERE subject_id = ? ORDER BY "
        "source_version ASC, generated_at ASC, id ASC",
        (run_id,),
    ).fetchall()
    return [int(row["id"]) for row in rows]


def validate_plan_file(path: str, *, board: str | None) -> tuple[dict[str, Any], int]:
    try:
        plan, validation = _validated_plan(path, board=board)
    except _BoardWorkspaceMissing as exc:
        return _error("board_workspace_missing", exc), 2
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        return _error("invalid_plan", exc), 2
    return {
        "status": "valid",
        "schema": plan.schema,
        "run_id": plan.run_id,
        "task_count": len(plan.tasks),
        "conflict_count": len(validation.conflicts),
        "plan_hash": validation.plan_hash,
        "resolved_profiles": validation.resolved_profiles,
        "routed_roles": sorted(validation.routed_roles),
    }, 0


def materialize_plan_file(
    path: str,
    *,
    board: str | None,
) -> tuple[dict[str, Any], int]:
    try:
        plan, validation = _validated_plan(path, board=board)
        with kb.connect(board=board) as conn:
            topology = materialize_org_run(conn, plan, validation, board=board)
    except _BoardWorkspaceMissing as exc:
        return _error("board_workspace_missing", exc), 2
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        return _error("invalid_plan", exc), 2
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        return _error("org_run_materialization_failed", exc), 1
    return {
        "status": "materialized",
        "run_id": plan.run_id,
        "plan_hash": validation.plan_hash,
        "topology": _topology_payload(topology),
    }, 0


def _show_payload(conn, run_id: str) -> dict[str, Any]:
    run = get_org_run(conn, run_id)
    if run is None:
        raise KeyError(run_id)
    state = refresh_org_run_state(conn, run_id)
    nodes = list_org_nodes(conn, run_id)
    stored_topology = load_org_run_topology(conn, run_id)
    if stored_topology is None:
        raise ValueError(f"OrgRun {run_id} has incomplete stored topology")
    blocked_nodes: list[str] = []
    dispatchable_nodes: list[str] = []
    for node in nodes:
        task = kb.get_task(conn, node.task_id)
        if task is not None:
            if task.status == "blocked":
                blocked_nodes.append(node.node_id)
            elif task.status == "ready":
                dispatchable_nodes.append(node.node_id)
    return {
        "status": "ok",
        "run_id": run_id,
        "state": state,
        "plan_version": run.plan_version,
        "plan_hash": run.plan_hash,
        "topology": _topology_payload(stored_topology),
        "blocked_nodes": sorted(blocked_nodes),
        "dispatchable_nodes": sorted(dispatchable_nodes),
        "report_ids": _report_ids(conn, run_id),
    }


def show_org_run(
    run_id: str,
    *,
    board: str | None,
) -> tuple[dict[str, Any], int]:
    try:
        with kb.connect(board=board) as conn:
            return _show_payload(conn, run_id), 0
    except KeyError:
        return _error("org_run_not_found", ValueError(run_id)), 1
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        return _error("org_run_show_failed", exc), 1


def amend_org_run_file(path: str, *, board: str | None) -> tuple[dict[str, Any], int]:
    try:
        amendment = parse_implementation_amendment(_read_json(path))
        repository = _repository_for_board(board)
        with kb.connect(board=board) as conn:
            topology = apply_org_run_amendment(
                conn,
                amendment,
                board=board,
                repository=repository,
                profile_exists=_role_route_exists,
            )
            run = get_org_run(conn, amendment.run_id)
            assert run is not None
    except _BoardWorkspaceMissing as exc:
        return _error("board_workspace_missing", exc), 2
    except (KeyError, ValueError, OSError, json.JSONDecodeError) as exc:
        return _error("invalid_amendment", exc), 2
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        return _error("org_run_amendment_failed", exc), 1
    return {
        "status": "amended",
        "run_id": amendment.run_id,
        "plan_version": run.plan_version,
        "plan_hash": run.plan_hash,
        "topology": _topology_payload(topology),
    }, 0


def _legacy_run_ids(conn) -> list[str]:
    rows = conn.execute(
        "SELECT t.idempotency_key FROM tasks AS t "
        "WHERE t.idempotency_key LIKE 'org-run:%:anchor' "
        "AND NOT EXISTS ("
        "SELECT 1 FROM task_links AS link WHERE link.child_id = t.id"
        ")"
    ).fetchall()
    prefix = "org-run:"
    suffix = ":anchor"
    return sorted({
        str(row["idempotency_key"])[len(prefix):-len(suffix)]
        for row in rows
        if row["idempotency_key"]
    })


def list_org_runs(*, board: str | None) -> tuple[dict[str, Any], int]:
    try:
        board_slug = board or kb.get_current_board()
        with kb.connect(board=board) as conn:
            rows = conn.execute(
                "SELECT * FROM kanban_org_runs WHERE board_slug = ? ORDER BY run_id",
                (board_slug,),
            ).fetchall()
            runs = [{
                "run_id": str(row["run_id"]),
                "state": str(row["state"]),
                "origin": str(row["origin"]),
                "plan_version": int(row["plan_version"]),
                "plan_hash": str(row["plan_hash"]),
            } for row in rows]
            known = {run["run_id"] for run in runs}
            runs.extend({
                "run_id": run_id,
                "state": "legacy_unadopted",
                "origin": "legacy",
                "plan_version": None,
                "plan_hash": None,
            } for run_id in _legacy_run_ids(conn) if run_id not in known)
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        return _error("org_run_list_failed", exc), 1
    return {"status": "ok", "runs": sorted(runs, key=lambda run: run["run_id"])}, 0


def adopt_legacy_run(run_id: str, *, board: str | None) -> tuple[dict[str, Any], int]:
    try:
        with kb.connect(board=board) as conn:
            topology = adopt_legacy_org_run(conn, run_id, board=board)
            run = get_org_run(conn, run_id)
            assert run is not None
    except (KeyError, ValueError) as exc:
        return _error("invalid_legacy_org_run", exc), 2
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        return _error("org_run_adoption_failed", exc), 1
    return {
        "status": "adopted",
        "run_id": run_id,
        "plan_version": run.plan_version,
        "plan_hash": run.plan_hash,
        "topology": _topology_payload(topology),
    }, 0


def project_org_run_reports(
    run_id: str,
    *,
    board: str | None,
) -> tuple[dict[str, Any], int]:
    """Backfill canonical reports for an already-terminal local OrgRun."""
    board_slug = board or kb.get_current_board()
    try:
        with kb.connect(board=board) as conn:
            run = get_org_run(conn, run_id)
            if run is None or run.board_slug != board_slug:
                raise KeyError(run_id)
            # Keep report projection out of this module's cold-import graph:
            # kanban_reports owns evidence redaction and is loaded only when
            # an operator explicitly requests this local recovery path.
            from hermes_cli.kanban_reports import (
                list_reports,
                project_org_run_completion,
            )

            final = project_org_run_completion(
                conn,
                run_id,
                board=board_slug,
            )
            if final is None:
                return _error(
                    "org_run_reports_not_ready",
                    ValueError(
                        f"OrgRun {run_id} is not terminal; "
                        "complete its active gates before projecting reports"
                    ),
                ), 2
            active_task_ids = sorted(
                node.task_id
                for node in list_org_nodes(conn, run_id)
                if node.state == "active"
            )
            task_report_ids = sorted(
                report.id
                for task_id in active_task_ids
                for report in list_reports(
                    conn,
                    report_type="task",
                    subject_id=task_id,
                )
            )
            final_report_ids = [
                report.id
                for report in list_reports(
                    conn,
                    report_type="org_run_final",
                    subject_id=run_id,
                )
            ]
            state = refresh_org_run_state(conn, run_id)
    except KeyError:
        return _error("org_run_not_found", ValueError(run_id)), 1
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        return _error("org_run_report_projection_failed", exc), 1
    return {
        "status": "projected",
        "run_id": run_id,
        "state": state,
        "task_report_ids": task_report_ids,
        "final_report_ids": final_report_ids,
        "report_ids": sorted(task_report_ids + final_report_ids),
    }, 0


def sync_kanban(*, board: str | None, mode: str, project_id: str | None = None) -> tuple[dict[str, Any], int]:
    """Keep the legacy parser entry as a typed, local-only rejection."""
    del board, mode, project_id
    return dict(_SYNC_DISABLED), 2


def build_parser(subparsers, *, cmd_org: Callable[[argparse.Namespace], int]) -> None:
    parser = subparsers.add_parser(
        "org",
        help="Validate and materialize local Hades OrgRuns",
    )
    sub = parser.add_subparsers(dest="org_action")
    validate = sub.add_parser("validate", help="Validate an implementation plan JSON file")
    validate.add_argument("plan")
    validate.add_argument("--board", default=None)
    validate.add_argument("--json", action="store_true")
    validate.set_defaults(func=cmd_org)

    materialize = sub.add_parser("materialize", help="Materialize an implementation plan in local Kanban")
    materialize.add_argument("plan")
    materialize.add_argument("--board", default=None)
    materialize.add_argument("--json", action="store_true")
    materialize.set_defaults(func=cmd_org)

    show = sub.add_parser("show", help="Show a materialized local OrgRun")
    show.add_argument("run_id")
    show.add_argument("--board", default=None)
    show.add_argument("--json", action="store_true")
    show.set_defaults(func=cmd_org)

    amend = sub.add_parser("amend", help="Apply an implementation-plan amendment")
    amend.add_argument("amendment")
    amend.add_argument("--board", default=None)
    amend.add_argument("--json", action="store_true")
    amend.set_defaults(func=cmd_org)

    list_runs = sub.add_parser("list", help="List local and unadopted legacy OrgRuns")
    list_runs.add_argument("--board", default=None)
    list_runs.add_argument("--json", action="store_true")
    list_runs.set_defaults(func=cmd_org)

    adopt = sub.add_parser("adopt-legacy", help="Adopt legacy OrgRun cards in place")
    adopt.add_argument("run_id")
    adopt.add_argument("--board", default=None)
    adopt.add_argument("--json", action="store_true")
    adopt.set_defaults(func=cmd_org)

    project_reports = sub.add_parser(
        "project-reports",
        help="Backfill canonical reports for a terminal local OrgRun",
    )
    project_reports.add_argument("run_id")
    project_reports.add_argument("--board", default=None)
    project_reports.add_argument("--json", action="store_true")
    project_reports.set_defaults(func=cmd_org)

    sync = sub.add_parser("sync", help="Report the local-only sync boundary")
    sync.add_argument("--mode", default="off")
    sync.add_argument("--project-id", default=None)
    sync.add_argument("--board", default=None)
    sync.add_argument("--json", action="store_true")
    sync.set_defaults(func=cmd_org)


def org_command(args: argparse.Namespace) -> int:
    action = getattr(args, "org_action", None)
    if action == "validate":
        result, code = validate_plan_file(args.plan, board=args.board)
    elif action == "materialize":
        result, code = materialize_plan_file(args.plan, board=args.board)
    elif action == "show":
        result, code = show_org_run(args.run_id, board=args.board)
    elif action == "amend":
        result, code = amend_org_run_file(args.amendment, board=args.board)
    elif action == "list":
        result, code = list_org_runs(board=args.board)
    elif action == "adopt-legacy":
        result, code = adopt_legacy_run(args.run_id, board=args.board)
    elif action == "project-reports":
        result, code = project_org_run_reports(args.run_id, board=args.board)
    elif action == "sync":
        result, code = sync_kanban(
            board=args.board,
            mode=args.mode,
            project_id=args.project_id,
        )
    else:
        print(
            "usage: hermes org "
            "<validate|materialize|show|amend|list|adopt-legacy|project-reports|sync>"
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return code
