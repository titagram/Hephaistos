"""Public autonomous engineering-review orchestration.

This module only prepares the normal interactive Hermes chat. The trusted
``ReviewAuthority`` is created by a session-ready callback after ``AIAgent``
has assigned a session ID and remains owned by this public process until the
chat lifecycle returns.
"""

from __future__ import annotations

import json
import os
from argparse import Namespace
from pathlib import Path
from typing import Callable, Literal, cast

from .authority import ReviewAuthority
from .runs import Effort


ReviewEffort = Literal["low", "medium", "high"]


def _load_chat_command() -> Callable[[Namespace], object]:
    # Lazy import avoids a main.py cycle while parsers are being registered.
    from hermes_cli.main import cmd_chat

    return cmd_chat


def _review_query(target: str, effort: ReviewEffort) -> str:
    # JSON string quoting keeps the two user-controlled values unambiguous.
    return (
        "Execute the preloaded requesting-code-review skill exactly as written "
        f"for target {json.dumps(target)} with effort {json.dumps(effort)}."
    )


def launch_review_chat(
    *,
    args: Namespace,
    workspace: Path,
    target: str,
    effort: ReviewEffort,
    skills: list[str],
    auto_approve: bool,
    pass_session_id: bool,
    query: str,
) -> None:
    """Launch classic chat while owning exactly one session-bound authority."""
    authority: ReviewAuthority | None = None

    def session_ready(session_id: str) -> None:
        nonlocal authority
        if authority is not None:
            raise RuntimeError("review authority was already created")
        candidate = ReviewAuthority(
            workspace=workspace.resolve(),
            target=target,
            effort=cast(Effort, effort),
            session_id=session_id,
        )
        try:
            candidate.start_serving()
        except BaseException:
            # start_serving transactionally revokes its capability, but close
            # remains idempotent and protects future implementations.
            candidate.close()
            raise
        authority = candidate

    args.query = query
    args.skills = list(skills)
    args.pass_session_id = pass_session_id
    args.yolo = auto_approve
    args.cli = True
    args.tui = False
    args.session_ready_callback = session_ready

    inherited_yolo = os.environ.pop("HERMES_YOLO_MODE", None)
    try:
        _load_chat_command()(args)
    finally:
        try:
            if authority is not None:
                authority.close()
        finally:
            if inherited_yolo is None:
                os.environ.pop("HERMES_YOLO_MODE", None)
            else:
                os.environ["HERMES_YOLO_MODE"] = inherited_yolo


def review_command(args: Namespace) -> int:
    """Entry point for ``hermes review``."""
    target = str(getattr(args, "target", "local") or "local")
    effort_value = str(getattr(args, "effort", "medium"))
    if effort_value not in {"low", "medium", "high"}:
        raise ValueError("effort must be low, medium, or high")
    effort = cast(ReviewEffort, effort_value)
    launch_review_chat(
        args=args,
        workspace=Path.cwd(),
        target=target,
        effort=effort,
        skills=["requesting-code-review"],
        auto_approve=False,
        pass_session_id=True,
        query=_review_query(target, effort),
    )
    return 0
