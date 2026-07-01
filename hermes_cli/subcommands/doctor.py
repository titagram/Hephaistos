"""``hermes doctor`` subcommand parser.

Extracted verbatim from ``hermes_cli/main.py:main()`` (god-file Phase 2).
Handler injected to avoid importing ``main``.
"""

from __future__ import annotations

from typing import Callable


def build_doctor_parser(subparsers, *, cmd_doctor: Callable) -> None:
    """Attach the ``doctor`` subcommand to ``subparsers``."""
    # =========================================================================
    # doctor command
    # =========================================================================
    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Check configuration and dependencies",
        description="Diagnose issues with Hermes Agent setup",
    )
    doctor_parser.add_argument(
        "--fix", action="store_true", help="Attempt to fix issues automatically"
    )
    doctor_parser.add_argument(
        "--ack",
        metavar="ADVISORY_ID",
        default=None,
        help=(
            "Acknowledge a security advisory by ID and exit. After ack, the "
            "advisory will no longer trigger startup banners. Run `hermes "
            "doctor` first to see active advisories and their IDs."
        ),
    )
    doctor_parser.add_argument(
        "--report-backend",
        action="store_true",
        help="Explicitly submit a compact Hades doctor report to the configured backend",
    )
    doctor_sub = doctor_parser.add_subparsers(dest="doctor_action")
    cleanup = doctor_sub.add_parser("cleanup", help="Run local Hades maintenance cleanup")
    cleanup.add_argument("--orphaned-cache", action="store_true", help="Remove orphaned Hades shared-memory cache")
    cleanup.add_argument("--all", action="store_true", help="Include non-expired orphaned cache")
    cleanup.add_argument("--yes", action="store_true", help="Proceed without an interactive confirmation")
    doctor_parser.set_defaults(func=cmd_doctor)
