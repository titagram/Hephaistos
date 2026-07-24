"""Actual main-parser dispatch, help, fast-path, and laziness contracts."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest

from hermes_cli import main as main_module
from hermes_cli.main import (
    _BUILTIN_SUBCOMMANDS,
    _coalesce_session_name_args,
    _plugin_cli_discovery_needed,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _main_process(
    home: Path,
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["HERMES_HOME"] = str(home)
    return subprocess.run(
        [sys.executable, "-m", "hermes_cli.main", *arguments],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_actual_main_help_and_every_action_dispatch_contract(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    home.chmod(0o700)
    help_result = _main_process(home, "evolution", "--help")
    assert help_result.returncode == 0
    for action in ("init", "status", "history", "show"):
        assert action in help_result.stdout

    status_result = _main_process(home, "evolution", "status", "--json")
    assert status_result.returncode == 0
    assert json.loads(status_result.stdout) == {
        "schema_version": 1,
        "status": "uninitialized",
        "initialized": False,
        "overlay_enabled": False,
        "active_generation_id": None,
        "last_known_good_generation_id": None,
        "diagnostics": [],
    }

    history_result = _main_process(
        home,
        "evolution",
        "history",
        "--limit",
        "1",
        "--after",
        "0",
        "--json",
    )
    assert history_result.returncode == 0
    assert json.loads(history_result.stdout) == {
        "schema_version": 1,
        "status": "uninitialized",
        "items": [],
        "next_after": None,
    }

    show_result = _main_process(
        home,
        "evolution",
        "show",
        "suggestion",
        "suggestion-alpha",
        "--json",
    )
    assert show_result.returncode == 1
    assert json.loads(show_result.stdout) == {
        "schema_version": 1,
        "status": "missing",
        "kind": "suggestion",
        "record": None,
    }

    init_result = _main_process(home, "evolution", "init", "--json")
    assert init_result.returncode == 0
    initialized = json.loads(init_result.stdout)
    assert initialized["schema_version"] == 1
    assert initialized["status"] == "coherent"
    assert initialized["initialized"] is True
    assert initialized["active_generation_id"]
    assert (
        initialized["last_known_good_generation_id"]
        == initialized["active_generation_id"]
    )


@pytest.mark.parametrize("state", ["unsafe-root", "hostile-lock"])
def test_actual_main_init_lock_failures_are_bounded_without_path_disclosure(
    tmp_path: Path,
    state: str,
) -> None:
    home = tmp_path / "home"
    root = home / "evolution"
    root.mkdir(parents=True, mode=0o700)
    home.chmod(0o700)
    root.chmod(0o700)
    if state == "unsafe-root":
        root.chmod(0o755)
    else:
        (root / ".lifecycle.lock").symlink_to(root / "missing-lock")

    result = _main_process(home, "evolution", "init", "--json")

    assert result.returncode == 1
    assert json.loads(result.stdout) == {
        "schema_version": 1,
        "status": "blocked",
        "initialized": False,
        "overlay_enabled": False,
        "active_generation_id": None,
        "last_known_good_generation_id": None,
        "diagnostics": ["evolution_unavailable"],
    }
    assert "Traceback" not in result.stderr
    assert str(home) not in result.stderr
    assert str(REPOSITORY_ROOT) not in result.stderr


def test_actual_main_evolution_help_keeps_handler_import_lazy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sys.modules.pop("hermes_cli.evolution.command", None)
    monkeypatch.setattr(sys, "argv", ["hermes", "evolution", "--help"])
    stdout = io.StringIO()
    stderr = io.StringIO()

    with patch.object(
        main_module,
        "_plugin_cli_discovery_needed",
        return_value=False,
    ):
        with redirect_stdout(stdout), redirect_stderr(stderr):
            with pytest.raises(SystemExit) as error:
                main_module.main()

    assert error.value.code == 0
    assert "usage: hades evolution" in stdout.getvalue()
    for action in ("init", "status", "history", "show"):
        assert action in stdout.getvalue()
    assert "hermes_cli.evolution.command" not in sys.modules


def test_actual_main_evolution_parser_rejects_identifier_without_disclosure(
    tmp_path: Path,
) -> None:
    secret_path = "/Users/example/private/a8-secret"

    result = _main_process(
        tmp_path / "home",
        "evolution",
        "show",
        "suggestion",
        secret_path,
        "--json",
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert secret_path not in result.stderr
    assert result.stderr.endswith(
        "error: argument record_id: invalid evolution identifier\n"
    )


@pytest.mark.parametrize(
    "argv",
    [
        ["hermes", "evolution", "status"],
        ["hermes", "--tui", "evolution", "history"],
        ["hermes", "-m", "provider/model", "evolution", "--help"],
    ],
)
def test_evolution_uses_the_builtin_plugin_discovery_fast_path(
    argv: list[str],
) -> None:
    assert "evolution" in _BUILTIN_SUBCOMMANDS
    with patch.object(sys, "argv", argv):
        assert _plugin_cli_discovery_needed() is False


@pytest.mark.parametrize("flag", ["-c", "--continue", "-r", "--resume"])
def test_session_name_coalescing_stops_at_evolution(
    flag: str,
) -> None:
    assert _coalesce_session_name_args([
        flag,
        "my",
        "session",
        "evolution",
        "status",
    ]) == [flag, "my session", "evolution", "status"]
