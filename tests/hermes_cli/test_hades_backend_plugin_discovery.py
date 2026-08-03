"""The optional Backend command is owned by normal directory-plugin discovery."""

from __future__ import annotations

import os
import json
from pathlib import Path
import shutil
import subprocess
import sys

import yaml


PLUGIN_REPOSITORY = (
    Path(__file__).resolve().parents[2].parent / "hades-backend-plugin-repo"
)


def _run_backend_help(home: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "hermes_cli.main", "backend", "--help"],
        cwd=home,
        env={
            **os.environ,
            "HERMES_HOME": str(home),
            "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
        },
        text=True,
        capture_output=True,
    )


def _run_tui_catalog(home: Path) -> dict:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from pathlib import Path; from tui_gateway import server; import json; "
                "Path('catalog.json').write_text(json.dumps(server.handle_request({'id': '1', 'method': 'commands.catalog', 'params': {}})))"
            ),
        ],
        cwd=home,
        env={
            **os.environ,
            "HERMES_HOME": str(home),
            "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
        },
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads((home / "catalog.json").read_text(encoding="utf-8"))


def _install_plugin(home: Path, *, enabled: bool) -> None:
    if not PLUGIN_REPOSITORY.is_dir():
        raise AssertionError(f"missing standalone plugin fixture: {PLUGIN_REPOSITORY}")
    shutil.copytree(PLUGIN_REPOSITORY, home / "plugins" / "hades-backend")
    (home / "config.yaml").write_text(
        yaml.safe_dump({"plugins": {"enabled": ["hades-backend"] if enabled else []}}),
        encoding="utf-8",
    )


def test_backend_cli_command_is_registered_only_by_enabled_directory_plugin(tmp_path: Path):
    """Removing the plugin must remove the parser without importing legacy core code."""
    enabled_home = tmp_path / "enabled"
    enabled_home.mkdir()
    _install_plugin(enabled_home, enabled=True)

    enabled = _run_backend_help(enabled_home)

    assert enabled.returncode == 0, enabled.stderr
    assert "set-token" in enabled.stdout
    assert "setup" not in enabled.stdout
    assert "--project-token" not in enabled.stdout

    disabled_home = tmp_path / "disabled"
    disabled_home.mkdir()
    _install_plugin(disabled_home, enabled=False)

    disabled = _run_backend_help(disabled_home)

    assert disabled.returncode != 0
    assert "hades_backend" not in (disabled.stdout + disabled.stderr)


def test_absent_backend_command_does_not_import_legacy_core_module(tmp_path: Path):
    """An absent command remains an argparse error even when legacy imports are blocked."""
    home = tmp_path / "absent"
    home.mkdir()
    (home / "config.yaml").write_text(yaml.safe_dump({"plugins": {"enabled": []}}), encoding="utf-8")
    script = """
import builtins
import sys

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name.startswith('hermes_cli.hades_backend'):
        raise AssertionError(f'legacy backend import: {name}')
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
from hermes_cli.main import main
sys.argv = ['hades', 'backend', '--help']
main()
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=home,
        env={
            **os.environ,
            "HERMES_HOME": str(home),
            "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "legacy backend import" not in (result.stdout + result.stderr)


def test_backend_plugin_slash_command_reaches_the_generic_tui_catalog(tmp_path: Path):
    """TUI and Desktop receive enabled plugin commands through the shared catalog."""
    enabled_home = tmp_path / "enabled"
    enabled_home.mkdir()
    _install_plugin(enabled_home, enabled=True)

    enabled_pairs = dict(_run_tui_catalog(enabled_home)["result"]["pairs"])

    assert "set-token|status|sync" in enabled_pairs["/backend"]

    disabled_home = tmp_path / "disabled"
    disabled_home.mkdir()
    _install_plugin(disabled_home, enabled=False)

    disabled_pairs = dict(_run_tui_catalog(disabled_home)["result"]["pairs"])

    assert "/backend" not in disabled_pairs
