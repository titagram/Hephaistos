"""Parser registration remains lazy and exposes only Project A operations."""

from __future__ import annotations

import sys

import pytest

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


@pytest.mark.parametrize("argv", [
    ["evolution", "history", "--limit", "0"],
    ["evolution", "history", "--after", "-1"],
    ["evolution", "show", "generation", "nope"],
    ["evolution", "show", "suggestion", "/etc/passwd"],
])
def test_evolution_parser_rejects_malformed_contract_arguments(argv: list[str]) -> None:
    parser = __import__("argparse").ArgumentParser()
    build_evolution_parser(parser.add_subparsers(dest="command", required=True), cmd_evolution=lambda args: 0)
    with pytest.raises(SystemExit) as error:
        parser.parse_args(argv)
    assert error.value.code == 2


def test_symbolic_non_uuid_suggestion_id_is_a_valid_parser_contract() -> None:
    parser = __import__("argparse").ArgumentParser()
    build_evolution_parser(
        parser.add_subparsers(dest="command", required=True),
        cmd_evolution=lambda args: 0,
    )

    parsed = parser.parse_args(
        ["evolution", "show", "suggestion", "suggestion-alpha", "--json"]
    )

    assert parsed.kind == "suggestion"
    assert parsed.record_id == "suggestion-alpha"
    assert parsed.json is True


@pytest.mark.parametrize("kind", ["blueprint", "generation", "report"])
@pytest.mark.parametrize(
    "record_id",
    [
        "a" * 63,
        "a" * 65,
        "A" * 64,
        "g" * 64,
        "0x" + "a" * 62,
    ],
)
def test_digest_show_kinds_require_exact_lowercase_hex(
    kind: str,
    record_id: str,
) -> None:
    parser = __import__("argparse").ArgumentParser()
    build_evolution_parser(
        parser.add_subparsers(dest="command", required=True),
        cmd_evolution=lambda args: 0,
    )

    with pytest.raises(SystemExit) as error:
        parser.parse_args(["evolution", "show", kind, record_id, "--json"])

    assert error.value.code == 2
