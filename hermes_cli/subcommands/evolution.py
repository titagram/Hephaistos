"""Lazy parser for Project A + Project B evolution lifecycle and Autopoiesis operations."""

from __future__ import annotations

import argparse
import re
from typing import Callable

_DIGEST = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_SYMBOL = re.compile(r"[A-Za-z0-9_-]{1,64}\Z", re.ASCII)


def _bounded_positive(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be an integer between 1 and 1000") from None
    if not 1 <= parsed <= 1000:
        raise argparse.ArgumentTypeError("must be an integer between 1 and 1000")
    return parsed


def _after(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a non-negative integer") from None
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be a non-negative integer")
    return parsed


def _blueprint_id(value: str) -> str:
    if (
        not value.startswith("bp_")
        or len(value) == 3
        or _SYMBOL.fullmatch(value) is None
    ):
        raise argparse.ArgumentTypeError("invalid blueprint identifier")
    return value


def _blueprint_limit(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            "must be an integer between 1 and 100"
        ) from None
    if not 1 <= parsed <= 100:
        raise argparse.ArgumentTypeError(
            "must be an integer between 1 and 100"
        )
    return parsed


def _identifier(kind: str):
    pattern = _SYMBOL if kind == "suggestion" else _DIGEST
    def validate(value: str) -> str:
        if pattern.fullmatch(value) is None:
            raise argparse.ArgumentTypeError("invalid evolution identifier")
        return value
    return validate


def build_evolution_parser(subparsers, *, cmd_evolution: Callable) -> None:
    parser = subparsers.add_parser("evolution", help="Inspect local autopoiesis evolution & observer state")
    actions = parser.add_subparsers(dest="evolution_action", required=True)

    # Legacy & baseline commands
    init = actions.add_parser("init", help="Initialize the immutable baseline")

    status = actions.add_parser("status", help="Show lifecycle status")
    history = actions.add_parser("history", help="Show bounded lifecycle history")
    history.add_argument("--limit", type=_bounded_positive, default=100)
    history.add_argument("--after", type=_after, default=0)

    show = actions.add_parser("show", help="Show one public lifecycle record")
    kinds = show.add_subparsers(dest="kind", required=True)
    for kind in ("suggestion", "blueprint", "generation", "report"):
        child = kinds.add_parser(kind)
        child.add_argument("record_id", type=_identifier(kind))
        child.add_argument("--json", action="store_true")

    # Project B operations
    pause = actions.add_parser("pause", help="Pause autopoiesis observer scans")
    resume = actions.add_parser("resume", help="Resume autopoiesis observer scans")
    doctor = actions.add_parser("doctor", help="Diagnose autopoiesis global organism state")

    observer = actions.add_parser("observer", help="Observer status and scan controls")
    obs_actions = observer.add_subparsers(dest="observer_action", required=True)
    obs_status = obs_actions.add_parser("status", help="Show observer status")
    obs_scan = obs_actions.add_parser("scan", help="Run immediate observer scan")

    suggestions = actions.add_parser("suggestions", help="List active autopoiesis suggestions")

    propose = actions.add_parser(
        "propose",
        help="Create an inert draft proposal from an eligible suggestion",
    )
    propose.add_argument(
        "suggestion_id",
        type=_identifier("suggestion"),
    )

    blueprint = actions.add_parser(
        "blueprint",
        help="Inspect immutable local proposal blueprints",
    )
    blueprint_actions = blueprint.add_subparsers(
        dest="blueprint_action",
        required=True,
    )
    blueprint_show = blueprint_actions.add_parser(
        "show",
        help="Show one verified blueprint",
    )
    blueprint_show.add_argument("blueprint_id", type=_blueprint_id)
    blueprint_list = blueprint_actions.add_parser(
        "list",
        help="List verified blueprint summaries",
    )
    blueprint_list.add_argument(
        "--limit",
        type=_blueprint_limit,
        default=20,
    )

    telos = actions.add_parser("telos", help="Global Telos commands")
    telos_actions = telos.add_subparsers(dest="telos_action", required=True)
    telos_status = telos_actions.add_parser("status", help="Show active Telos status")
    telos_draft = telos_actions.add_parser("draft", help="Draft a new Telos revision from a JSON file")
    telos_draft.add_argument("--file", type=str, required=True, help="Path to JSON Telos revision document")
    telos_history = telos_actions.add_parser("history", help="Show Telos revision history")

    telos_approve = telos_actions.add_parser("approve", help="Approve and activate a Telos revision digest")
    telos_approve.add_argument("digest", type=_identifier("digest"))

    telos_rollback = telos_actions.add_parser("rollback", help="Roll back to a verified Telos revision digest")
    telos_rollback.add_argument("digest", type=_identifier("digest"))


    all_commands = [
        init, status, history, show, pause, resume, doctor,
        observer, obs_status, obs_scan, suggestions,
        propose, blueprint_show, blueprint_list,
        telos, telos_status, telos_draft, telos_history, telos_approve, telos_rollback,
    ]

    for cmd in all_commands:
        cmd.add_argument("--json", action="store_true")
        cmd.set_defaults(func=cmd_evolution)

    init.set_defaults(action="init")
    status.set_defaults(action="status")
    history.set_defaults(action="history")
    show.set_defaults(action="show")
    pause.set_defaults(action="pause")
    resume.set_defaults(action="resume")
    doctor.set_defaults(action="doctor")
    obs_status.set_defaults(action="observer_status")
    obs_scan.set_defaults(action="observer_scan")
    suggestions.set_defaults(action="suggestions")
    propose.set_defaults(action="propose")
    blueprint_show.set_defaults(
        action="blueprint_show",
        evolution_action="blueprint_show",
    )
    blueprint_list.set_defaults(
        action="blueprint_list",
        evolution_action="blueprint_list",
    )
    telos_status.set_defaults(action="telos_status")
    telos_draft.set_defaults(action="telos_draft")
    telos_history.set_defaults(action="telos_history")
    telos_approve.set_defaults(action="telos_approve")
    telos_rollback.set_defaults(action="telos_rollback")
