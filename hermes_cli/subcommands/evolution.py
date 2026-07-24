"""Lazy parser for safe Project A evolution lifecycle operations."""

from __future__ import annotations

from typing import Callable
import re


_DIGEST = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_SYMBOL = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z", re.ASCII)


def _bounded_positive(value: str) -> int:
    parsed = int(value)
    if not 1 <= parsed <= 1000:
        raise ValueError("must be between 1 and 1000")
    return parsed


def _after(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise ValueError("must be non-negative")
    return parsed


def _identifier(kind: str):
    pattern = _SYMBOL if kind == "suggestion" else _DIGEST
    def validate(value: str) -> str:
        if pattern.fullmatch(value) is None:
            raise ValueError("invalid identifier")
        return value
    return validate


def build_evolution_parser(subparsers, *, cmd_evolution: Callable) -> None:
    parser = subparsers.add_parser("evolution", help="Inspect local evolution lifecycle state")
    actions = parser.add_subparsers(dest="evolution_action", required=True)
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
    for child in (init, status, history, show):
        child.add_argument("--json", action="store_true")
        child.set_defaults(func=cmd_evolution)
    init.set_defaults(action="init")
    status.set_defaults(action="status")
    history.set_defaults(action="history")
    show.set_defaults(action="show")
