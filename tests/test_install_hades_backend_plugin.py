from __future__ import annotations

import hashlib
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLATION_DOC = REPO_ROOT / "docs" / "hades" / "installation.md"
WEBSITE_BACKEND_DOC = (
    REPO_ROOT / "website" / "docs" / "getting-started" / "hades-backend.md"
)


def test_backend_installation_uses_only_the_explicit_plugin_and_pairing_flow():
    """The public guide must not revive the retired bootstrap-token installer."""
    source = INSTALLATION_DOC.read_text(encoding="utf-8")

    assert "hades plugins install titagram/hades-backend-plugin --enable" in source
    assert (
        "hades backend set-token --url https://backend.example.test --project-id project-test"
        in source
    )
    assert "not yet published" in source
    for forbidden in (
        "--backend-url",
        "--backend-project-id",
        "--backend-project-token",
        "-BackendProjectToken",
        "--project-token",
        "backend bootstrap",
    ):
        assert forbidden not in source


def test_website_backend_setup_does_not_document_retired_bootstrap_tokens():
    source = WEBSITE_BACKEND_DOC.read_text(encoding="utf-8")

    assert "hades plugins install titagram/hades-backend-plugin --enable" in source
    assert "not yet published" in source
    for forbidden in ("backend bootstrap", "--project-token", "bootstrap token"):
        assert forbidden not in source.lower()


def test_plugin_lifecycle_changes_only_plugin_discovery_state(tmp_path, monkeypatch):
    """Enable, disable, update, and removal preserve unrelated Hades state."""
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    config_path = hermes_home / "config.yaml"
    config_path.write_text(
        """memory:
  provider: holographic
  memory_enabled: false
  user_profile_enabled: false
  holographic:
    custom_unknown_key: keep
plugins:
  hermes-memory-store:
    hrr_weight: 0.25
backend:
  legacy_unknown: keep
mcp_servers:
  hades_backend:
    future_unknown: keep
custom_future_section:
  preserve: true
""",
        encoding="utf-8",
    )
    (hermes_home / ".env").write_text("HADES_BACKEND_TOKEN=canary\n", encoding="utf-8")
    (hermes_home / "hades_backend.db").write_bytes(b"sqlite-canary")
    (hermes_home / "MEMORY.md").write_text("memory canary\n", encoding="utf-8")
    (hermes_home / "USER.md").write_text("user canary\n", encoding="utf-8")
    (hermes_home / "holographic.db").write_bytes(b"holographic-canary")
    plugin_dir = hermes_home / "plugins" / "hades_backend"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / ".git").mkdir()
    (plugin_dir / "plugin.yaml").write_text("name: hades_backend\n", encoding="utf-8")
    (plugin_dir / "payload.txt").write_text("plugin canary\n", encoding="utf-8")

    external_paths = [
        hermes_home / ".env",
        hermes_home / "hades_backend.db",
        hermes_home / "MEMORY.md",
        hermes_home / "USER.md",
        hermes_home / "holographic.db",
    ]
    external_before = {
        path: hashlib.sha256(path.read_bytes()).hexdigest() for path in external_paths
    }

    from hermes_cli import plugins_cmd

    monkeypatch.setattr(
        plugins_cmd, "_resolve_plugin_key", lambda name: "hades_backend"
    )
    monkeypatch.setattr(
        plugins_cmd, "_git_pull_plugin_dir", lambda path: (True, "Already up to date")
    )

    plugins_cmd.cmd_enable("hades_backend")
    plugins_cmd.cmd_update("hades_backend")
    plugins_cmd.cmd_disable("hades_backend")
    plugins_cmd.cmd_remove("hades_backend")

    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert config["plugins"]["enabled"] == []
    assert config["plugins"]["disabled"] == ["hades_backend"]
    assert config["memory"] == {
        "provider": "holographic",
        "memory_enabled": False,
        "user_profile_enabled": False,
        "holographic": {"custom_unknown_key": "keep"},
    }
    assert config["plugins"]["hermes-memory-store"] == {"hrr_weight": 0.25}
    assert config["backend"] == {"legacy_unknown": "keep"}
    assert config["mcp_servers"]["hades_backend"] == {"future_unknown": "keep"}
    assert config["custom_future_section"] == {"preserve": True}
    assert not plugin_dir.exists()
    assert {
        path: hashlib.sha256(path.read_bytes()).hexdigest() for path in external_paths
    } == external_before
