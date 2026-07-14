"""Behavior contracts for the read-only ``/gnothi_seauton`` prompt."""

from agent.gnothi_prompt import build_gnothi_prompt


def test_bare_request_builds_read_only_evidence_summary_prompt():
    prompt = build_gnothi_prompt("")

    assert "scope=organism" in prompt
    assert "read-only" in prompt.lower()
    assert "evidence" in prompt.lower()
    assert "status" in prompt.lower()
    assert "self-summary" in prompt.lower()
    for state in ("stale", "partial", "unknown"):
        assert state in prompt.lower()


def test_prompt_preserves_supported_request_and_exact_cli_semantics():
    request = "diff rev-old rev-new"
    prompt = build_gnothi_prompt(request)

    assert request in prompt
    for command in ("status", "inspect", "explain", "diff", "wiki"):
        assert f"gnothi-seauton {command}" in prompt


def test_prompt_does_not_authorize_evolution_or_external_changes():
    prompt = build_gnothi_prompt("inspect capability:browser")

    assert "does not authorize" in prompt.lower()
    for action in ("mutation", "research", "download", "install", "autopoiesis"):
        assert action in prompt.lower()


def test_whitespace_only_request_is_bare_request():
    assert build_gnothi_prompt(" \n ") == build_gnothi_prompt("")


def test_gnothi_registry_is_available_on_cli_and_gateway():
    from hermes_cli.commands import GATEWAY_KNOWN_COMMANDS, resolve_command

    command = resolve_command("gnothi_seauton")
    assert command is not None
    assert command.name == "gnothi_seauton"
    assert command.category == "Info"
    assert resolve_command("know-thyself").name == "gnothi_seauton"
    assert "gnothi_seauton" in GATEWAY_KNOWN_COMMANDS
    assert not command.cli_only
