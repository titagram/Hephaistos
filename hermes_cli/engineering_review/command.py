"""Public autonomous engineering-review orchestration.

This module only prepares the normal interactive Hermes chat. The trusted
``ReviewAuthority`` is created by a session-ready callback after ``AIAgent``
has synchronized its session with the process context and remains owned by
this public process until the chat lifecycle returns.
"""

from __future__ import annotations

import json
import logging
import os
from argparse import Namespace
from pathlib import Path
from typing import Callable, Literal, cast

from .authority import ReviewAuthority
from .execution_policy import decide_execution, target_kind_for
from .runs import Effort


ReviewEffort = Literal["low", "medium", "high"]
_LOGGER = logging.getLogger(__name__)


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


def _prune_completed_review_runs() -> None:
    """Apply the configured retention bound after an authority is closed."""
    from hermes_cli.config import load_config
    from hermes_constants import get_hermes_home

    from .runs import normalize_retention_runs, prune_completed_runs

    config = load_config()
    review = config.get("review")
    raw_keep = review.get("retention_runs") if isinstance(review, dict) else None
    prune_completed_runs(
        get_hermes_home(),
        keep=normalize_retention_runs(raw_keep),
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
        target_kind = target_kind_for(target)
        backend = os.environ.get("TERMINAL_ENV", "local")
        allow_local = False
        if target_kind == "pr" and backend.strip().lower() == "local":
            # Reuse the live Hermes approval surface.  Absence, timeout, and
            # every unrecognized result fail closed while static review remains
            # available.
            from tools.terminal_tool import _get_approval_callback

            approval = _get_approval_callback()
            if approval is not None:
                verdict = approval(
                    "hermes review: execute untrusted pull-request build/tests locally",
                    (
                        "The pull request can execute repository-controlled code. "
                        "Allow this review to run its recorded build and test commands "
                        "outside a sandbox?"
                    ),
                    allow_permanent=False,
                )
                allow_local = verdict in {"once", "session"}
        execution = decide_execution(
            target_kind=target_kind,
            sandbox=backend,
            allow_local=allow_local,
        )
        candidate = ReviewAuthority(
            workspace=workspace.resolve(),
            target=target,
            effort=cast(Effort, effort),
            session_id=session_id,
            execution_decision=execution,
        )
        try:
            candidate.start_serving()
        except BaseException:
            # start_serving transactionally revokes its capability, but close
            # remains idempotent and protects future implementations.
            candidate.close()
            raise
        authority = candidate

    # A remote PR reviewed through the local backend may need explicit user
    # consent before repository-controlled build/test commands can execute.
    # Seed the review through the normal interactive prompt lifecycle instead
    # of ``-q`` single-query mode: the prompt_toolkit app is then live when
    # ``session_ready`` asks the existing terminal approval callback.  The
    # private one-shot flag returns after the seeded turn, preserving the
    # public command's non-REPL behavior.
    args.query = None
    args.initial_query = query
    args.exit_after_initial_query = True
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
                try:
                    _prune_completed_review_runs()
                except Exception:
                    _LOGGER.warning(
                        "Could not prune completed engineering review runs",
                        exc_info=True,
                    )
        finally:
            if inherited_yolo is None:
                os.environ.pop("HERMES_YOLO_MODE", None)
            else:
                os.environ["HERMES_YOLO_MODE"] = inherited_yolo


def review_command(args: Namespace) -> int:
    """Entry point for ``hermes review``."""
    target = str(getattr(args, "target", "local") or "local")
    recovery_run = getattr(args, "run", None)
    if target == "cleanup":
        if not isinstance(recovery_run, str) or not recovery_run:
            raise ValueError("review cleanup requires --run RUN_ID")
        from .recovery import recover_review_run

        print(
            json.dumps(
                recover_review_run(recovery_run),
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return 0
    if recovery_run is not None:
        raise ValueError("--run is accepted only by `hermes review cleanup`")
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
