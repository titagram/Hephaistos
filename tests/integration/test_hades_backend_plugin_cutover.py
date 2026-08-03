"""Hermetic host cutover checks for the optional Backend plugin."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_backend_plugin_matrix_is_hermetic_across_classic_cli_and_tui(
    tmp_path: Path,
) -> None:
    """Only an enabled ordinary plugin exposes Backend on either host surface."""
    for state, visible, expected in (
        ("absent", False, (False, False, False, ())),
        ("disabled", False, (True, False, False, ())),
        ("enabled-unconfigured", True, (True, True, False, ())),
        ("enabled-unlinked", True, (True, True, True, ())),
        ("enabled-linked-a", True, (True, True, True, ("workspace-a",))),
        (
            "enabled-linked-a-and-b",
            True,
            (True, True, True, ("workspace-a", "workspace-b")),
        ),
    ):
        home = _matrix_home(tmp_path / state, state=state)

        assert _matrix_state(home) == expected
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
    if state != "absent":
        _write_generic_backend_plugin(home)
    configured = state in {
        "enabled-unlinked",
        "enabled-linked-a",
        "enabled-linked-a-and-b",
    }
    if configured:
        with (home / "config.yaml").open("a", encoding="utf-8") as stream:
            stream.write("mcp_servers:\n  hades_backend:\n    command: python\n")
    bindings: list[str] = []
    if state.endswith("linked-a") or state.endswith("linked-a-and-b"):
        (home / "workspace-a").mkdir()
        bindings.append("workspace-a")
    if state.endswith("linked-a-and-b"):
        (home / "workspace-b").mkdir()
        bindings.append("workspace-b")
    if bindings:
        _write_canonical_backend_state(home, bindings)
    return home


def _matrix_state(home: Path) -> tuple[bool, bool, bool, tuple[str, ...]]:
    """Read the profile state represented by each matrix row, not its label."""
    config = (home / "config.yaml").read_text(encoding="utf-8")
    installed = (home / "plugins" / "hades-backend" / "plugin.yaml").is_file()
    enabled = "enabled: [hades-backend]" in config
    configured = "mcp_servers:\n  hades_backend:" in config
    db_path = home / "hades_backend.db"
    if not db_path.exists():
        return installed, enabled, configured, ()
    with sqlite3.connect(db_path) as connection:
        bindings = tuple(
            row[0]
            for row in connection.execute(
                "SELECT repo_root FROM workspace_bindings "
                "WHERE status = 'linked' ORDER BY repo_root"
            )
        )
        tokens = tuple(
            row[0]
            for row in connection.execute(
                "SELECT backend_agents.token_env_key FROM workspace_bindings "
                "JOIN backend_agents USING (agent_id) "
                "WHERE workspace_bindings.status = 'linked' "
                "ORDER BY workspace_bindings.repo_root"
            )
        )
    assert tokens == tuple(_derived_token_key(name) for name in bindings)
    return installed, enabled, configured, bindings


def _write_canonical_backend_state(home: Path, bindings: list[str]) -> None:
    """Create the plugin-compatible profile tables and derived token references."""
    db_path = home / "hades_backend.db"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            "CREATE TABLE backend_agents ("
            "agent_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, base_url TEXT NOT NULL, "
            "label TEXT NOT NULL, token_env_key TEXT NOT NULL, capabilities TEXT NOT NULL);"
            "CREATE TABLE workspace_bindings ("
            "workspace_fingerprint TEXT PRIMARY KEY, project_id TEXT NOT NULL, agent_id TEXT NOT NULL, "
            "local_project_id TEXT NOT NULL, backend_workspace_binding_id TEXT NOT NULL, "
            "display_path TEXT NOT NULL, repo_root TEXT NOT NULL, git_remote_display TEXT, "
            "git_remote_hash TEXT, head_commit TEXT, status TEXT NOT NULL);"
        )
        for name in bindings:
            token_key = _derived_token_key(name)
            connection.execute(
                "INSERT INTO backend_agents VALUES (?, ?, ?, ?, ?, ?)",
                (
                    f"agent-{name}",
                    f"project-{name}",
                    "https://fake.invalid",
                    name,
                    token_key,
                    "{}",
                ),
            )
            connection.execute(
                "INSERT INTO workspace_bindings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    f"fingerprint-{name}",
                    f"project-{name}",
                    f"agent-{name}",
                    f"local-{name}",
                    f"binding-{name}",
                    name,
                    name,
                    "",
                    "",
                    "",
                    "linked",
                ),
            )
    (home / ".env").write_text(
        "".join(f"{_derived_token_key(name)}=DERIVED_{name}\n" for name in bindings),
        encoding="utf-8",
    )


def _derived_token_key(name: str) -> str:
    material = f"https://fake.invalid|project-{name}|agent-{name}"
    return (
        "HADES_BACKEND_AGENT_TOKEN_"
        + hashlib.sha256(material.encode()).hexdigest()[:16].upper()
    )


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
