"""Lazy parser for safe Project A evolution lifecycle operations."""

from __future__ import annotations

from typing import Callable


def build_evolution_parser(subparsers, *, cmd_evolution: Callable) -> None:
    parser = subparsers.add_parser("evolution", help="Inspect local evolution lifecycle state")
    actions = parser.add_subparsers(dest="evolution_action", required=True)
    init = actions.add_parser("init", help="Initialize the immutable baseline")
    status = actions.add_parser("status", help="Show lifecycle status")
    history = actions.add_parser("history", help="Show bounded lifecycle history")
    history.add_argument("--limit", type=int, default=100)
    history.add_argument("--after", type=int, default=0)
    show = actions.add_parser("show", help="Show one public lifecycle record")
    kinds = show.add_subparsers(dest="kind", required=True)
    for kind in ("suggestion", "blueprint", "generation", "report"):
        child = kinds.add_parser(kind)
        child.add_argument("record_id")
        child.add_argument("--json", action="store_true")
    for child in (init, status, history, show):
        child.add_argument("--json", action="store_true")
        child.set_defaults(func=cmd_evolution)
    init.set_defaults(action="init")
    status.set_defaults(action="status")
    history.set_defaults(action="history")
    show.set_defaults(action="show")
