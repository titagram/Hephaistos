"""Parser registration remains lazy and exposes only Project A operations."""

from __future__ import annotations

import sys

from hermes_cli.subcommands.evolution import build_evolution_parser


def test_evolution_parser_has_typed_read_surface_without_importing_heavy_modules() -> None:
    sys.modules.pop("hermes_cli.evolution.command", None)
    parser = __import__("argparse").ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_evolution_parser(subparsers, cmd_evolution=lambda args: 0)
    parsed = parser.parse_args(["evolution", "show", "generation", "a" * 64, "--json"])
    assert (parsed.command, parsed.evolution_action, parsed.kind, parsed.record_id, parsed.json) == (
        "evolution", "show", "generation", "a" * 64, True,
    )
    assert "hermes_cli.evolution.command" not in sys.modules
