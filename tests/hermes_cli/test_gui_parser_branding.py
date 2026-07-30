"""Branding and artifact-resolution tests for the desktop launcher."""

from __future__ import annotations

import argparse

import pytest

from hermes_cli import main as cli_main
from hermes_cli.subcommands.gui import build_gui_parser


def test_desktop_help_uses_hades_branding(capsys):
    """Catch a parser regression that presents the Electron app as Hermes."""
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    build_gui_parser(subparsers, cmd_gui=lambda _args: None)

    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["desktop", "--help"])

    assert exc.value.code == 0
    help_text = capsys.readouterr().out
    assert "Hades Electron desktop app" in help_text
    assert "Hermes Electron desktop app" not in help_text


def test_packaged_desktop_resolves_hades_artifact(tmp_path, monkeypatch):
    """Catch packaged resolution that does not recognize the Hades app."""
    monkeypatch.setattr(cli_main.sys, "platform", "darwin")
    desktop_dir = tmp_path / "apps" / "desktop"
    hades = desktop_dir / "release" / "mac-arm64" / "Hades.app" / "Contents" / "MacOS" / "Hades"
    hades.parent.mkdir(parents=True)
    hades.write_text("", encoding="utf-8")

    assert cli_main._desktop_packaged_executable(desktop_dir) == hades


def test_packaged_desktop_accepts_legacy_hermes_artifact(tmp_path, monkeypatch):
    """Keep already-built Hermes app packages launchable after rebranding."""
    monkeypatch.setattr(cli_main.sys, "platform", "darwin")
    desktop_dir = tmp_path / "apps" / "desktop"
    legacy = desktop_dir / "release" / "mac-arm64" / "Hermes.app" / "Contents" / "MacOS" / "Hermes"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("", encoding="utf-8")

    assert cli_main._desktop_packaged_executable(desktop_dir) == legacy
