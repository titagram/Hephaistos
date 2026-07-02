"""Regression tests for the trimmed Hades local-agent surface."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_hades_slash_registry_exposes_backend_project_doctor_uninstall():
    from hermes_cli.commands import SUBCOMMANDS, resolve_command

    assert resolve_command("backend").name == "backend"
    assert resolve_command("project").name == "project"
    assert resolve_command("doctor").name == "doctor"
    assert resolve_command("uninstall").name == "uninstall"
    assert "worker" in SUBCOMMANDS["/backend"]
    assert "link" in SUBCOMMANDS["/project"]
    assert "cleanup" in SUBCOMMANDS["/doctor"]


def test_hades_auth_help_does_not_expose_spotify():
    result = subprocess.run(
        [sys.executable, "-m", "hermes_cli.main", "auth", "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    assert "spotify" not in result.stdout.lower()


def test_hades_slash_subcommand_dispatches_to_main(monkeypatch):
    from cli import HermesCLI

    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    cli = HermesCLI.__new__(HermesCLI)
    cli._console_print = lambda *args, **kwargs: None

    cli._handle_hades_subcommand_slash("/backend status")

    assert captured["cmd"][-3:] == ["hermes_cli.main", "backend", "status"]
    assert captured["cmd"][-4] == "-m"
    assert captured["kwargs"]["stdin"] is subprocess.DEVNULL
    assert captured["kwargs"]["capture_output"] is True


def test_hades_slash_uninstall_requires_noninteractive_confirmation(monkeypatch):
    from cli import HermesCLI

    def fail_run(*args, **kwargs):  # pragma: no cover - should not be called
        raise AssertionError("subprocess.run should not be called")

    monkeypatch.setattr(subprocess, "run", fail_run)
    output: list[str] = []
    cli = HermesCLI.__new__(HermesCLI)
    cli._console_print = lambda msg, *args, **kwargs: output.append(str(msg))

    cli._handle_hades_subcommand_slash("/uninstall")

    assert output
    assert "hades uninstall" in output[0]
    assert "--yes" in output[0]


def test_hades_excluded_toolsets_are_not_resolved_or_configurable():
    from hermes_cli.tools_config import (
        CONFIGURABLE_TOOLSETS,
        _get_platform_tools,
        valid_post_setup_keys,
    )
    from toolsets import get_all_toolsets, get_toolset_names, resolve_toolset

    excluded = {"image_gen", "video_gen", "spotify"}
    configurable = {key for key, _label, _desc in CONFIGURABLE_TOOLSETS}

    assert excluded.isdisjoint(configurable)
    assert excluded.isdisjoint(get_all_toolsets())
    assert excluded.isdisjoint(get_toolset_names())
    assert resolve_toolset("image_gen") == []
    assert "image_generate" not in resolve_toolset("hermes-cli")

    cfg = {"platform_toolsets": {"cli": ["hermes-cli", "image_gen", "video_gen", "spotify"]}}
    enabled = _get_platform_tools(cfg, "cli", include_default_mcp_servers=False)
    assert excluded.isdisjoint(enabled)
    assert "spotify" not in valid_post_setup_keys()


def test_hades_excluded_toolsets_are_not_reported_by_model_tools():
    import model_tools

    available, unavailable = model_tools.check_tool_availability()
    names = set(available) | {item["name"] for item in unavailable}

    assert {"image_gen", "video_gen", "spotify"}.isdisjoint(names)
    assert "image_generate" not in model_tools.get_all_tool_names()
    assert model_tools.get_toolset_for_tool("image_generate") is None


def test_hades_excluded_lazy_features_are_not_installable():
    from tools.lazy_deps import FeatureUnavailable, ensure, feature_install_command, is_available

    for feature in ("search.exa", "search.firecrawl", "image.fal"):
        assert feature_install_command(feature) is None
        assert is_available(feature) is False
        with pytest.raises(FeatureUnavailable):
            ensure(feature, prompt=False)


def test_hades_excluded_pyproject_extras_are_absent():
    import tomllib

    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extras = data["project"]["optional-dependencies"]

    assert "exa" not in extras
    assert "firecrawl" not in extras
    assert "fal" not in extras


def test_hades_excluded_bundled_plugins_do_not_load():
    from hermes_cli.plugins import PluginManager

    mgr = PluginManager()
    mgr.discover_and_load()

    excluded = {
        "browser/firecrawl",
        "google_meet",
        "image_gen/openai",
        "spotify",
        "teams_pipeline",
        "video_gen/fal",
        "web/exa",
        "web/firecrawl",
        "web/tavily",
    }
    assert excluded.isdisjoint(mgr._plugins)


def test_hades_mcp_catalog_filters_excluded_entries(monkeypatch):
    monkeypatch.delenv("HERMES_OPTIONAL_MCPS", raising=False)

    from hermes_cli.mcp_catalog import list_catalog

    names = {entry.name for entry in list_catalog()}
    assert {"linear", "n8n", "unreal-engine"}.isdisjoint(names)


def test_hades_bundled_skill_discovery_filters_excluded_skills():
    from tools.skills_sync import _discover_bundled_skills

    discovered = {
        path.relative_to(REPO_ROOT / "skills").as_posix()
        for _name, path in _discover_bundled_skills(REPO_ROOT / "skills")
    }

    assert not any(path.startswith("creative/") for path in discovered)
    assert "productivity/teams-meeting-pipeline" not in discovered
    assert "autonomous-ai-agents/hermes-agent" not in discovered


def test_uninstall_removes_hades_and_legacy_hermes_wrappers(tmp_path, monkeypatch):
    import hermes_cli.uninstall as uninstall

    home = tmp_path / "home"
    bin_dir = home / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    for name in ("hades", "hermes"):
        wrapper = bin_dir / name
        wrapper.write_text("#!/bin/sh\nexec python -m hermes_cli.main \"$@\"\n")

    monkeypatch.setattr(uninstall.Path, "home", staticmethod(lambda: home))

    removed = set(uninstall.remove_wrapper_script())

    assert bin_dir / "hades" in removed
    assert bin_dir / "hermes" in removed
    assert not (bin_dir / "hades").exists()
    assert not (bin_dir / "hermes").exists()


def test_uninstall_removes_hades_path_blocks(tmp_path, monkeypatch):
    import hermes_cli.uninstall as uninstall

    zshrc = tmp_path / ".zshrc"
    zshrc.write_text(
        "before\n"
        "# Hades Agent - ensure ~/.local/bin is on PATH\n"
        'export PATH="$HOME/.local/bin:$PATH"\n'
        "after\n"
    )

    monkeypatch.setattr(uninstall, "find_shell_configs", lambda: [zshrc])

    removed = uninstall.remove_path_from_shell_configs()
    text = zshrc.read_text()

    assert removed == [zshrc]
    assert "Hades Agent" not in text
    assert ".local/bin" not in text
    assert "before" in text
    assert "after" in text


def test_installers_advertise_uninstall_and_no_windows_skills_flag():
    sh = (REPO_ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    ps1 = (REPO_ROOT / "scripts" / "install.ps1").read_text(encoding="utf-8")

    assert "hades uninstall" in sh
    assert "hades uninstall" in ps1
    assert "[switch]$NoSkills" in ps1
