"""Tests for explicit-global model switch persistence."""

import types
from unittest.mock import patch

import pytest

from hermes_cli.model_switch import parse_model_flags, resolve_persist_behavior


# ---------------------------------------------------------------------------
# parse_model_flags
# ---------------------------------------------------------------------------


class TestParseModelFlagsSession:
    def test_no_flags(self):
        assert parse_model_flags("sonnet") == ("sonnet", "", False, False, False)

    def test_global_flag(self):
        assert parse_model_flags("sonnet --global") == ("sonnet", "", True, False, False)

    def test_session_flag(self):
        assert parse_model_flags("sonnet --session") == (
            "sonnet",
            "",
            False,
            False,
            True,
        )

    def test_session_with_provider(self):
        assert parse_model_flags("sonnet --provider anthropic --session") == (
            "sonnet",
            "anthropic",
            False,
            False,
            True,
        )

    def test_refresh_flag_still_parsed(self):
        assert parse_model_flags("--refresh") == ("", "", False, True, False)

    def test_unicode_dash_session_normalized(self):
        # Telegram/iOS auto-converts -- to en/em dashes.
        assert parse_model_flags("sonnet \u2013session") == (
            "sonnet",
            "",
            False,
            False,
            True,
        )


# ---------------------------------------------------------------------------
# resolve_persist_behavior
# ---------------------------------------------------------------------------


class TestResolvePersistBehavior:
    @pytest.mark.parametrize(
        ("is_global", "is_session", "expected"),
        [
            (False, False, False),
            (True, False, True),
            (False, True, False),
            (True, True, False),
        ],
    )
    def test_persistence_requires_unambiguous_global_scope(
        self, is_global, is_session, expected
    ):
        assert resolve_persist_behavior(is_global, is_session) is expected

    def test_legacy_persist_by_default_config_does_not_change_unscoped_scope(
        self, tmp_path, monkeypatch
    ):
        config_path = tmp_path / "config.yaml"
        original = "model:\n  persist_switch_by_default: true\n"
        config_path.write_text(original, encoding="utf-8")
        monkeypatch.setattr(
            "hermes_cli.config.load_config",
            lambda: {"model": {"persist_switch_by_default": True}},
        )

        assert resolve_persist_behavior(False, False) is False
        assert config_path.read_text(encoding="utf-8") == original


def test_model_fallback_help_describes_session_default():
    """Unavailable picker help must not promise persistence for plain /model."""
    import cli

    printed = []
    shell = types.SimpleNamespace(model="", provider="", base_url="")
    with (
        patch("hermes_cli.inventory.load_picker_context", side_effect=RuntimeError),
        patch.object(cli, "_cprint", printed.append),
    ):
        cli.HermesCLI._handle_model_switch(shell, "/model")

    assert "  /model <name>                        switch model for this session only" in printed
    assert "  /model <name> --global               switch and persist" in printed
