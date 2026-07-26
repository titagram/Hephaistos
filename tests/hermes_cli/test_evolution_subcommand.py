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


@pytest.mark.parametrize("kind", ["suggestion", "blueprint"])
@pytest.mark.parametrize(
    "record_id",
    [
        "/Users/example/private",
        r"C:\\Users\\example\\private",
        "../private",
        "file:///private/token",
        "Bearer secret-token",
        "first line\nsecond-secret-line",
    ],
)
def test_identifier_parse_errors_do_not_reflect_input(
    kind: str,
    record_id: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser = __import__("argparse").ArgumentParser()
    build_evolution_parser(
        parser.add_subparsers(dest="command", required=True),
        cmd_evolution=lambda args: 0,
    )

    with pytest.raises(SystemExit) as error:
        parser.parse_args(["evolution", "show", kind, record_id, "--json"])

    assert error.value.code == 2
    stderr = capsys.readouterr().err
    assert record_id not in stderr
    assert stderr.endswith("error: argument record_id: invalid evolution identifier\n")


@pytest.mark.parametrize(
    ("argv", "value", "message"),
    [
        (["evolution", "history", "--limit"], "/private/token", "must be an integer between 1 and 1000"),
        (["evolution", "history", "--limit"], "1001", "must be an integer between 1 and 1000"),
        (["evolution", "history", "--after"], "file:///private/token", "must be a non-negative integer"),
        (["evolution", "history", "--after"], "-1", "must be a non-negative integer"),
    ],
)
def test_history_parse_errors_do_not_reflect_input(
    argv: list[str],
    value: str,
    message: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser = __import__("argparse").ArgumentParser()
    build_evolution_parser(
        parser.add_subparsers(dest="command", required=True),
        cmd_evolution=lambda args: 0,
    )

    with pytest.raises(SystemExit) as error:
        parser.parse_args([*argv, value])

    assert error.value.code == 2
    stderr = capsys.readouterr().err
    assert value not in stderr
    assert stderr.endswith(f"error: argument {argv[-1]}: {message}\n")


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


def test_evolution_parser_exposes_proposal_blueprint_actions_lazily() -> None:
    sys.modules.pop("hermes_cli.evolution.command", None)
    parser = __import__("argparse").ArgumentParser()
    build_evolution_parser(
        parser.add_subparsers(dest="command", required=True),
        cmd_evolution=lambda args: 0,
    )

    proposed = parser.parse_args(
        ["evolution", "propose", "sug_alpha", "--json"]
    )
    assert (
        proposed.evolution_action,
        proposed.action,
        proposed.suggestion_id,
        proposed.json,
    ) == ("propose", "propose", "sug_alpha", True)

    shown = parser.parse_args(
        ["evolution", "blueprint", "show", "bp_123", "--json"]
    )
    assert (
        shown.evolution_action,
        shown.blueprint_action,
        shown.action,
        shown.blueprint_id,
        shown.json,
    ) == ("blueprint_show", "show", "blueprint_show", "bp_123", True)

    listed = parser.parse_args(
        ["evolution", "blueprint", "list", "--json"]
    )
    assert (
        listed.evolution_action,
        listed.blueprint_action,
        listed.action,
        listed.limit,
        listed.json,
    ) == ("blueprint_list", "list", "blueprint_list", 20, True)
    assert "hermes_cli.evolution.command" not in sys.modules


@pytest.mark.parametrize(
    ("argv", "private_value", "argument_name", "message"),
    [
        (
            ["evolution", "propose", "/private/suggestion"],
            "/private/suggestion",
            "suggestion_id",
            "invalid evolution identifier",
        ),
        (
            ["evolution", "blueprint", "show", "bp_"],
            "bp_",
            "blueprint_id",
            "invalid blueprint identifier",
        ),
        (
            ["evolution", "blueprint", "show", "not-a-blueprint"],
            "not-a-blueprint",
            "blueprint_id",
            "invalid blueprint identifier",
        ),
        (
            ["evolution", "blueprint", "show", "../bp_secret"],
            "../bp_secret",
            "blueprint_id",
            "invalid blueprint identifier",
        ),
        (
            [
                "evolution",
                "blueprint",
                "list",
                "--limit",
                "file:///private/token",
            ],
            "file:///private/token",
            "--limit",
            "must be an integer between 1 and 100",
        ),
    ],
)
def test_proposal_blueprint_parse_errors_are_bounded_and_redacted(
    argv: list[str],
    private_value: str,
    argument_name: str,
    message: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser = __import__("argparse").ArgumentParser()
    build_evolution_parser(
        parser.add_subparsers(dest="command", required=True),
        cmd_evolution=lambda args: 0,
    )

    with pytest.raises(SystemExit) as error:
        parser.parse_args(argv)

    assert error.value.code == 2
    stderr = capsys.readouterr().err
    assert private_value not in stderr
    assert stderr.endswith(
        f"error: argument {argument_name}: {message}\n"
    )


@pytest.mark.parametrize("limit", ["0", "101"])
def test_blueprint_list_rejects_out_of_range_limits(limit: str) -> None:
    parser = __import__("argparse").ArgumentParser()
    build_evolution_parser(
        parser.add_subparsers(dest="command", required=True),
        cmd_evolution=lambda args: 0,
    )

    with pytest.raises(SystemExit) as error:
        parser.parse_args(
            ["evolution", "blueprint", "list", "--limit", limit]
        )

    assert error.value.code == 2


@pytest.mark.parametrize("limit", ["1", "100"])
def test_blueprint_list_accepts_boundary_limits(limit: str) -> None:
    parser = __import__("argparse").ArgumentParser()
    build_evolution_parser(
        parser.add_subparsers(dest="command", required=True),
        cmd_evolution=lambda args: 0,
    )

    parsed = parser.parse_args(
        ["evolution", "blueprint", "list", "--limit", limit]
    )

    assert parsed.limit == int(limit)
    assert parsed.action == "blueprint_list"
