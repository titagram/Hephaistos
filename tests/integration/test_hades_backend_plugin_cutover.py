"""Hermetic host cutover checks for the optional Backend plugin."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_backend_plugin_matrix_is_hermetic_across_classic_cli_and_tui(
    tmp_path: Path,
) -> None:
    """Only an enabled ordinary plugin exposes Backend on either host surface."""
    for state, visible in (
        ("absent", False),
        ("disabled", False),
        ("enabled-unconfigured", True),
        ("enabled-unlinked", True),
        ("enabled-linked-a", True),
        ("enabled-linked-a-and-b", True),
    ):
        home = _matrix_home(tmp_path / state, state=state)

        assert _backend_visible_in_classic_cli(home) is visible
        assert _backend_visible_in_tui_catalog(home) is visible


def _matrix_home(home: Path, *, state: str) -> Path:
    """Create a complete profile row without relying on an external checkout."""
    home.mkdir(parents=True)
    enabled = state.startswith("enabled")
    (home / "config.yaml").write_text(
        "plugins:\n  enabled: [hades-backend]\n"
        if enabled
        else "plugins:\n  enabled: []\n",
        encoding="utf-8",
    )
    if enabled:
        _write_generic_backend_plugin(home)
    if state.endswith("linked-a") or state.endswith("linked-a-and-b"):
        (home / "workspace-a").mkdir()
    if state.endswith("linked-a-and-b"):
        (home / "workspace-b").mkdir()
    return home


def _write_generic_backend_plugin(home: Path) -> None:
    """Install the smallest repository-owned plugin that exercises the host ABI."""
    plugin = home / "plugins" / "hades-backend"
    plugin.mkdir(parents=True)
    (plugin / "plugin.yaml").write_text(
        "name: hades-backend\nkind: standalone\nversion: 0.0.0-test\n",
        encoding="utf-8",
    )
    (plugin / "__init__.py").write_text(
        """
def _setup(parser):
    actions = parser.add_subparsers(dest=\"backend_action\", required=True)
    for name in (\"set-token\", \"status\", \"sync\"):
        actions.add_parser(name)


def _run(_args):
    return 0


def _slash(_raw_args, _context=None):
    return \"plugin command completed without Backend access\"


def register(ctx):
    ctx.register_cli_command(\"backend\", \"Test Backend plugin\", _setup, _run)
    ctx.register_command(
        \"backend\", _slash, \"Test Backend plugin\", \"set-token|status|sync\"
    )
    ctx.register_skill(\"project-knowledge\", __file__, \"Test project knowledge skill\")
""".lstrip(),
        encoding="utf-8",
    )


def _plugin_env(home: Path) -> dict[str, str]:
    return {
        **os.environ,
        "HERMES_HOME": str(home),
        "PYTHONPATH": str(PROJECT_ROOT),
        "NO_COLOR": "1",
    }


def _backend_visible_in_classic_cli(home: Path) -> bool:
    result = subprocess.run(
        [sys.executable, "-m", "hermes_cli.main", "backend", "--help"],
        cwd=home,
        env=_plugin_env(home),
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    output = result.stdout + result.stderr
    assert "hades_backend" not in output
    return result.returncode == 0 and "set-token" in output and "sync" in output


def _backend_visible_in_tui_catalog(home: Path) -> bool:
    catalog_path = home / "catalog.json"
    script = (
        "import json; from pathlib import Path; from tui_gateway import server; "
        "response = server.handle_request({'id': 'catalog', 'method': 'commands.catalog', 'params': {}}); "
        f"Path({str(catalog_path)!r}).write_text(json.dumps(response), encoding='utf-8')"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=home,
        env=_plugin_env(home),
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    pairs = dict(
        json.loads(catalog_path.read_text(encoding="utf-8"))["result"]["pairs"]
    )
    return "/backend" in pairs and "set-token|status|sync" in pairs["/backend"]
