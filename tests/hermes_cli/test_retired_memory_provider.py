"""Contracts for safely handling retired external memory selections."""

from __future__ import annotations

import builtins
import sys
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import hermes_cli.memory_setup as memory_setup
from hermes_cli.config import load_config
from hermes_cli.retired_memory_providers import (
    RETIRED_MEMORY_PROVIDERS,
    resolve_effective_memory_provider,
)


def test_retired_backend_resolution_is_immutable_and_preserves_config_bytes(tmp_path, monkeypatch):
    """Changing resolution must not mutate a legacy config or select another provider."""
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    config_path = hermes_home / "config.yaml"
    original = (
        "# retain this comment\n"
        "memory:\n"
        "  provider: hades_backend  # choose later\n"
        "  custom_future_key: keep\n"
        "unknown_future_section:\n"
        "  preserved: true\n"
    )
    config_path.write_text(original, encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    resolution = resolve_effective_memory_provider(load_config())

    assert RETIRED_MEMORY_PROVIDERS == frozenset({"hades_backend"})
    assert resolution.configured == "hades_backend"
    assert resolution.effective == ""
    assert resolution.retired is True
    assert "hades memory setup" in resolution.message
    assert config_path.read_text(encoding="utf-8") == original
    with pytest.raises(FrozenInstanceError):
        resolution.effective = "honcho"


def test_valid_memory_provider_resolution_passes_through_unchanged():
    """Resolver must not alter supported provider selections."""
    resolution = resolve_effective_memory_provider({"memory": {"provider": "openviking"}})

    assert resolution.configured == "openviking"
    assert resolution.effective == "openviking"
    assert resolution.retired is False
    assert resolution.message == ""


def test_retired_selection_is_inert_through_agent_and_status_without_rewriting_config(
    tmp_path, monkeypatch, capsys
):
    """The shared AIAgent path and status must retain the raw selection but never load it."""
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    config_path = hermes_home / "config.yaml"
    original = (
        "memory:\n"
        "  provider: hades_backend  # retain\n"
        "  unknown: keep\n"
        "future:\n"
        "  key: value\n"
    )
    config_path.write_text(original, encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    for module_name in list(sys.modules):
        if "hades_backend" in module_name:
            sys.modules.pop(module_name)

    real_import = builtins.__import__

    def reject_backend_import(name, *args, **kwargs):
        if "hades_backend" in name:
            raise AssertionError(f"retired Backend import attempted: {name}")
        return real_import(name, *args, **kwargs)

    with (
        patch.object(builtins, "__import__", reject_backend_import),
        patch("plugins.memory.load_memory_provider") as load_memory_provider,
        patch("agent.model_metadata.get_model_context_length", return_value=204_800),
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        from run_agent import AIAgent

        agent = AIAgent(
            api_key="test-key-1234567890",
            base_url="https://openrouter.ai/api/v1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=False,
        )
        memory_setup.cmd_status(SimpleNamespace())

    output = capsys.readouterr().out

    assert agent._memory_manager is None
    assert all(
        call.args[0] != "hades_backend"
        for call in load_memory_provider.call_args_list
    )
    assert "Configured: hades_backend" in output
    assert "Effective:  (none — built-in only)" in output
    assert "Retired:    yes" in output
    assert "hades memory setup" in output
    assert not any("hades_backend" in module_name for module_name in sys.modules)
    assert config_path.read_text(encoding="utf-8") == original
