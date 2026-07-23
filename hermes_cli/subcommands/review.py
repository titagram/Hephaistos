"""Parser for the public ``hermes review`` workflow."""

from __future__ import annotations

from typing import Callable


def build_review_parser(subparsers, *, cmd_review: Callable) -> None:
    """Attach the intentionally small review surface to ``subparsers``."""
    parser = subparsers.add_parser(
        "review",
        help="Run an autonomous, evidence-backed engineering review",
        description=(
            "Review local changes, a git range, a diff file, or a GitHub pull "
            "request without automatically modifying or publishing anything."
        ),
    )
    parser.add_argument(
        "target",
        nargs="?",
        default="local",
        help=(
            "local (default), a git range, a diff file, or a GitHub pull-request URL"
        ),
    )
    parser.add_argument(
        "--effort",
        choices=("low", "medium", "high"),
        default="medium",
        help="Review depth and reviewer roster (default: medium)",
    )
    # Hidden chat defaults. The public command deliberately exposes no model,
    # provider, runner, sandbox, publication, or mutation knobs.
    parser.set_defaults(
        func=cmd_review,
        runner="auto",
        query=None,
        image=None,
        model=None,
        provider=None,
        toolsets=None,
        skills=None,
        verbose=None,
        quiet=False,
        resume=None,
        continue_last=None,
        worktree=False,
        checkpoints=False,
        pass_session_id=True,
        max_turns=None,
        accept_hooks=False,
        yolo=False,
        safe_mode=False,
        ignore_user_config=False,
        ignore_rules=False,
        compact=False,
        source=None,
        tui=False,
        tui_dev=False,
        cli=True,
    )
