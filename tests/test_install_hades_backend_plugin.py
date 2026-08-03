from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
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


def _write_canary_profile(
    tmp_path: Path, monkeypatch
) -> tuple[Path, Path, dict[Path, str]]:
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
    (hermes_home / "memory_store.db").write_bytes(b"holographic-canary")

    external_paths = [
        hermes_home / ".env",
        hermes_home / "hades_backend.db",
        hermes_home / "MEMORY.md",
        hermes_home / "USER.md",
        hermes_home / "memory_store.db",
    ]
    return (
        hermes_home,
        config_path,
        {
            path: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in external_paths
        },
    )


def _assert_canary_unchanged(
    config_path: Path,
    external_before: dict[Path, str],
) -> None:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
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
    assert {
        path: hashlib.sha256(path.read_bytes()).hexdigest() for path in external_before
    } == external_before


def _commit_plugin_version(repo: Path, payload: str) -> None:
    (repo / "plugin.yaml").write_text(
        "name: hades-backend\nmanifest_version: 1\n", encoding="utf-8"
    )
    (repo / "payload.txt").write_text(payload, encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=task12@example.test",
            "-c",
            "user.name=Task 12",
            "commit",
            "-m",
            f"plugin {payload.strip()}",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def _local_plugin_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "plugin-source"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    _commit_plugin_version(repo, "version one\n")
    return repo


def _write_manifest_plugin(path: Path, name: str, payload: str = "payload\n") -> Path:
    path.mkdir(parents=True)
    (path / "plugin.yaml").write_text(
        f"name: {name}\nmanifest_version: 1\n", encoding="utf-8"
    )
    (path / "payload.txt").write_text(payload, encoding="utf-8")
    return path


def test_real_plugin_install_and_update_preserve_canary_state(tmp_path, monkeypatch):
    """A local Git install/update never performs Backend work or rewrites profile state."""
    hermes_home, config_path, external_before = _write_canary_profile(
        tmp_path, monkeypatch
    )
    source = _local_plugin_repo(tmp_path)

    from hermes_cli import plugins_cmd

    plugin_ref = f"file://{source}"
    plugins_cmd.cmd_install(plugin_ref, enable=True)
    target = hermes_home / "plugins" / "hades-backend"
    assert (target / "payload.txt").read_text(encoding="utf-8") == "version one\n"
    assert yaml.safe_load(config_path.read_text(encoding="utf-8"))["plugins"][
        "enabled"
    ] == ["hades-backend"]
    _assert_canary_unchanged(config_path, external_before)

    _commit_plugin_version(source, "version two\n")
    plugins_cmd.cmd_update("hades-backend")
    plugins_cmd.cmd_update("hades-backend")
    assert (target / "payload.txt").read_text(encoding="utf-8") == "version two\n"
    _assert_canary_unchanged(config_path, external_before)

    _commit_plugin_version(source, "version three\n")
    plugins_cmd.cmd_install(plugin_ref, force=True, enable=False)
    assert (target / "payload.txt").read_text(encoding="utf-8") == "version three\n"
    assert yaml.safe_load(config_path.read_text(encoding="utf-8"))["plugins"][
        "enabled"
    ] == ["hades-backend"]
    _assert_canary_unchanged(config_path, external_before)


def test_direct_remove_disarms_reinstall_without_erasing_legacy_state(
    tmp_path, monkeypatch
):
    hermes_home, config_path, external_before = _write_canary_profile(
        tmp_path, monkeypatch
    )
    source = _local_plugin_repo(tmp_path)

    from hermes_cli import plugins_cmd

    plugin_ref = f"file://{source}"
    plugins_cmd.cmd_install(plugin_ref, enable=True)
    plugins_cmd.cmd_remove("hades-backend")

    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert "hades-backend" not in config["plugins"].get("enabled", [])
    assert "hades_backend" not in config["plugins"].get("enabled", [])
    assert config["plugins"]["disabled"] == ["hades-backend"]
    assert not (hermes_home / "plugins" / "hades-backend").exists()

    plugins_cmd.cmd_install(plugin_ref, enable=False)
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert "hades-backend" not in config["plugins"].get("enabled", [])
    assert config["plugins"]["disabled"] == ["hades-backend"]
    _assert_canary_unchanged(config_path, external_before)


def test_force_reinstall_clone_move_failure_retains_existing_plugin(
    tmp_path, monkeypatch
):
    hermes_home, config_path, external_before = _write_canary_profile(
        tmp_path, monkeypatch
    )
    source = _local_plugin_repo(tmp_path)

    from hermes_cli import plugins_cmd

    plugin_ref = f"file://{source}"
    plugins_cmd.cmd_install(plugin_ref, enable=True)
    target = hermes_home / "plugins" / "hades-backend"
    before = (target / "payload.txt").read_bytes()
    config_before = config_path.read_bytes()

    monkeypatch.setattr(
        plugins_cmd.shutil,
        "move",
        lambda *_args: (_ for _ in ()).throw(OSError("injected move failure")),
    )

    with pytest.raises(OSError, match="injected move failure"):
        plugins_cmd.cmd_install(plugin_ref, force=True, enable=False)

    assert target.exists()
    assert (target / "payload.txt").read_bytes() == before
    assert config_path.read_bytes() == config_before
    _assert_canary_unchanged(config_path, external_before)


def test_force_reinstall_replace_failure_restores_existing_plugin(
    tmp_path, monkeypatch
):
    hermes_home, config_path, external_before = _write_canary_profile(
        tmp_path, monkeypatch
    )
    source = _local_plugin_repo(tmp_path)

    from hermes_cli import plugins_cmd

    plugin_ref = f"file://{source}"
    plugins_cmd.cmd_install(plugin_ref, enable=True)
    target = hermes_home / "plugins" / "hades-backend"
    before = (target / "payload.txt").read_bytes()
    real_replace = plugins_cmd.os.replace

    def fail_staged_swap(source_path, destination_path):
        if Path(source_path).name.startswith(".hades-backend.staging-"):
            raise OSError("injected swap failure")
        return real_replace(source_path, destination_path)

    monkeypatch.setattr(plugins_cmd.os, "replace", fail_staged_swap)

    with pytest.raises(OSError, match="injected swap failure"):
        plugins_cmd.cmd_install(plugin_ref, force=True, enable=False)

    assert (target / "payload.txt").read_bytes() == before
    _assert_canary_unchanged(config_path, external_before)


def test_plugin_update_failure_is_retry_safe(tmp_path, monkeypatch):
    hermes_home, config_path, external_before = _write_canary_profile(
        tmp_path, monkeypatch
    )
    source = _local_plugin_repo(tmp_path)

    from hermes_cli import plugins_cmd

    plugin_ref = f"file://{source}"
    plugins_cmd.cmd_install(plugin_ref, enable=True)
    target = hermes_home / "plugins" / "hades-backend"
    _commit_plugin_version(source, "version two\n")
    (target / "payload.txt").write_text("local interruption\n", encoding="utf-8")

    with pytest.raises(SystemExit):
        plugins_cmd.cmd_update("hades-backend")

    assert (target / "payload.txt").read_text(
        encoding="utf-8"
    ) == "local interruption\n"
    _assert_canary_unchanged(config_path, external_before)

    subprocess.run(["git", "checkout", "--", "payload.txt"], cwd=target, check=True)
    plugins_cmd.cmd_update("hades-backend")
    plugins_cmd.cmd_update("hades-backend")
    assert (target / "payload.txt").read_text(encoding="utf-8") == "version two\n"
    _assert_canary_unchanged(config_path, external_before)


def test_core_update_noop_preserves_plugin_and_profile_canaries(tmp_path, monkeypatch):
    """The real core-update orchestration never invokes plugin lifecycle work."""
    hermes_home, config_path, external_before = _write_canary_profile(
        tmp_path, monkeypatch
    )
    source = _local_plugin_repo(tmp_path)

    from hermes_cli import main as hm
    from hermes_cli import plugins_cmd

    def unexpected_backend_operation(*_args, **_kwargs):
        raise AssertionError("core update must not invoke plugin lifecycle work")

    plugins_cmd.cmd_install(f"file://{source}", enable=True)
    target = hermes_home / "plugins" / "hades-backend"
    plugin_before = hashlib.sha256((target / "payload.txt").read_bytes()).hexdigest()
    core_root = tmp_path / "core"
    (core_root / ".git").mkdir(parents=True)

    def fake_run(command, **_kwargs):
        joined = " ".join(str(part) for part in command)
        if "rev-parse --abbrev-ref HEAD" in joined:
            return subprocess.CompletedProcess(command, 0, stdout="main\n", stderr="")
        if "rev-list" in joined:
            return subprocess.CompletedProcess(command, 0, stdout="0\n", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(hm, "PROJECT_ROOT", core_root)
    monkeypatch.setattr(hm.subprocess, "run", fake_run)
    monkeypatch.setattr(hm, "_run_pre_update_backup", lambda _args: None)
    monkeypatch.setattr(hm, "_install_hangup_protection", lambda **_kwargs: None)
    monkeypatch.setattr(hm, "_finalize_update_output", lambda _state: None)
    monkeypatch.setattr(hm, "_get_origin_url", lambda *_args: None)
    monkeypatch.setattr(hm, "_discard_lockfile_churn", lambda *_args: None)
    monkeypatch.setattr(plugins_cmd, "cmd_install", unexpected_backend_operation)
    monkeypatch.setattr(plugins_cmd, "cmd_update", unexpected_backend_operation)

    hm.cmd_update(SimpleNamespace(check=False, gateway=True, branch=None, force=False))

    assert (
        hashlib.sha256((target / "payload.txt").read_bytes()).hexdigest()
        == plugin_before
    )
    _assert_canary_unchanged(config_path, external_before)


def test_remove_keeps_distinct_plugin_identities_and_dashboard_disarms_target(
    tmp_path, monkeypatch
):
    hermes_home, config_path, external_before = _write_canary_profile(
        tmp_path, monkeypatch
    )
    from hermes_cli import plugins_cmd

    plugins_dir = hermes_home / "plugins"
    for name in ("foo-bar", "foo_bar", "hades-backend"):
        plugin_dir = plugins_dir / name
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.yaml").write_text(
            f"name: {name}\nmanifest_version: 1\n", encoding="utf-8"
        )
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["plugins"]["enabled"] = ["foo-bar", "foo_bar", "hades-backend"]
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    plugins_cmd.cmd_remove("foo-bar")
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert config["plugins"]["enabled"] == ["foo_bar", "hades-backend"]
    assert config["plugins"]["disabled"] == ["foo-bar"]

    result = plugins_cmd.dashboard_remove_user_plugin("hades-backend")
    assert result == {"ok": True, "name": "hades-backend"}
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert config["plugins"]["enabled"] == ["foo_bar"]
    assert config["plugins"]["disabled"] == ["foo-bar", "hades-backend"]
    _assert_canary_unchanged(config_path, external_before)


def test_transaction_residue_is_ignored_and_reconciled_target_locally(
    tmp_path, monkeypatch
):
    hermes_home, _config_path, _external_before = _write_canary_profile(
        tmp_path, monkeypatch
    )
    plugins_dir = hermes_home / "plugins"
    backup = plugins_dir / ".hades-backend.backup-dead"
    staging = plugins_dir / ".hades-backend.staging-dead"
    for path, payload in ((backup, "backup"), (staging, "staging")):
        path.mkdir(parents=True)
        (path / "plugin.yaml").write_text(
            "name: hades-backend\nmanifest_version: 1\n", encoding="utf-8"
        )
        (path / "payload.txt").write_text(payload, encoding="utf-8")

    from hermes_cli import plugins_cmd
    from hermes_cli.plugin_transactions import reconcile_plugin_transaction
    from hermes_cli.plugins import PluginManager

    assert "hades-backend" not in [
        entry[0] for entry in plugins_cmd._discover_all_plugins()
    ]
    assert PluginManager()._scan_directory(plugins_dir, "user") == []
    target = reconcile_plugin_transaction(plugins_dir, "hades-backend")
    assert target == plugins_dir / "hades-backend"
    assert (target / "payload.txt").read_text(encoding="utf-8") == "backup"
    assert not staging.exists()
    assert not backup.exists()


@pytest.mark.parametrize("surface", ["cli", "dashboard"])
@pytest.mark.parametrize("nested_manifest", ["nested-observer", "nemo"])
def test_remove_uses_exact_nested_registry_identity_and_preserves_flat_plugin(
    tmp_path, monkeypatch, surface, nested_manifest
):
    hermes_home, config_path, external_before = _write_canary_profile(
        tmp_path, monkeypatch
    )
    plugins_dir = hermes_home / "plugins"
    nested = _write_manifest_plugin(
        plugins_dir / "observability" / "nemo", nested_manifest
    )
    flat = _write_manifest_plugin(plugins_dir / "nemo", "nemo")
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["plugins"]["enabled"] = ["observability/nemo", "nemo"]
    config["plugins"]["disabled"] = []
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    from hermes_cli import plugins_cmd

    if surface == "cli":
        plugins_cmd.cmd_remove("observability/nemo")
    else:
        assert plugins_cmd.dashboard_remove_user_plugin("observability/nemo") == {
            "ok": True,
            "name": "observability/nemo",
        }

    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert config["plugins"]["enabled"] == ["nemo"]
    assert config["plugins"]["disabled"] == ["observability/nemo"]
    assert not nested.exists()
    assert flat.is_dir()
    _assert_canary_unchanged(config_path, external_before)


def test_remove_preserves_ambiguous_manifest_name_activation(tmp_path, monkeypatch):
    hermes_home, config_path, external_before = _write_canary_profile(
        tmp_path, monkeypatch
    )
    plugins_dir = hermes_home / "plugins"
    first = _write_manifest_plugin(plugins_dir / "alpha" / "one", "shared-name")
    second = _write_manifest_plugin(plugins_dir / "beta" / "two", "shared-name")
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["plugins"]["enabled"] = ["alpha/one", "beta/two", "shared-name"]
    config["plugins"]["disabled"] = []
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    from hermes_cli import plugins_cmd

    plugins_cmd.cmd_remove("alpha/one")

    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert config["plugins"]["enabled"] == ["beta/two", "shared-name"]
    assert config["plugins"]["disabled"] == ["alpha/one"]
    assert not first.exists()
    assert second.is_dir()
    _assert_canary_unchanged(config_path, external_before)


def test_remove_preserves_identity_shared_by_manifest_name_and_other_leaf(
    tmp_path, monkeypatch
):
    hermes_home, config_path, external_before = _write_canary_profile(
        tmp_path, monkeypatch
    )
    plugins_dir = hermes_home / "plugins"
    target = _write_manifest_plugin(plugins_dir / "alpha" / "one", "shared-alias")
    sibling = _write_manifest_plugin(
        plugins_dir / "beta" / "shared-alias", "different-name"
    )
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["plugins"]["enabled"] = ["alpha/one", "beta/shared-alias", "shared-alias"]
    config["plugins"]["disabled"] = []
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    from hermes_cli import plugins_cmd

    plugins_cmd.cmd_remove("alpha/one")

    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert config["plugins"]["enabled"] == ["beta/shared-alias", "shared-alias"]
    assert config["plugins"]["disabled"] == ["alpha/one"]
    assert not target.exists()
    assert sibling.is_dir()
    _assert_canary_unchanged(config_path, external_before)


@pytest.mark.parametrize("surface", ["cli", "dashboard"])
def test_remove_cleans_valid_transaction_residue_before_reinstall(
    tmp_path, monkeypatch, surface
):
    hermes_home, config_path, external_before = _write_canary_profile(
        tmp_path, monkeypatch
    )
    source = _local_plugin_repo(tmp_path)
    plugins_dir = hermes_home / "plugins"
    target = _write_manifest_plugin(
        plugins_dir / "hades-backend", "hades-backend", "published\n"
    )
    backup = _write_manifest_plugin(
        plugins_dir / ".hades-backend.backup-dead", "hades-backend", "removed-old\n"
    )
    staging = _write_manifest_plugin(
        plugins_dir / ".hades-backend.staging-beef",
        "hades-backend",
        "abandoned-candidate\n",
    )
    foreign = _write_manifest_plugin(
        plugins_dir / ".other-plugin.backup-dead", "other-plugin", "foreign\n"
    )
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["plugins"]["enabled"] = ["hades-backend"]
    config["plugins"]["disabled"] = []
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    from hermes_cli import plugins_cmd

    if surface == "cli":
        plugins_cmd.cmd_remove("hades-backend")
    else:
        assert plugins_cmd.dashboard_remove_user_plugin("hades-backend") == {
            "ok": True,
            "name": "hades-backend",
        }

    assert not target.exists()
    assert not backup.exists()
    assert not staging.exists()
    assert foreign.is_dir()
    plugins_cmd.cmd_install(f"file://{source}", enable=False)
    assert (target / "payload.txt").read_text(encoding="utf-8") == "version one\n"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert config["plugins"]["enabled"] == []
    assert config["plugins"]["disabled"] == ["hades-backend"]
    _assert_canary_unchanged(config_path, external_before)


@pytest.mark.parametrize("surface", ["cli", "dashboard"])
@pytest.mark.parametrize("residue_state", ["incomplete", "ambiguous", "invalid"])
def test_remove_fails_closed_on_unproven_transaction_residue(
    tmp_path, monkeypatch, surface, residue_state
):
    hermes_home, config_path, external_before = _write_canary_profile(
        tmp_path, monkeypatch
    )
    plugins_dir = hermes_home / "plugins"
    target_name = "wrong-plugin" if residue_state == "invalid" else "hades-backend"
    target = _write_manifest_plugin(plugins_dir / "hades-backend", target_name)
    residue = plugins_dir / ".hades-backend.backup-dead"
    if residue_state == "incomplete":
        residue.mkdir()
    else:
        _write_manifest_plugin(residue, "hades-backend")
    extra_residue = None
    if residue_state == "ambiguous":
        extra_residue = _write_manifest_plugin(
            plugins_dir / ".hades-backend.backup-beef", "hades-backend"
        )
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["plugins"]["enabled"] = ["hades-backend"]
    config["plugins"]["disabled"] = []
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    config_before = config_path.read_bytes()

    from hermes_cli import plugins_cmd

    if surface == "cli":
        with pytest.raises(SystemExit):
            plugins_cmd.cmd_remove("hades-backend")
    else:
        result = plugins_cmd.dashboard_remove_user_plugin("hades-backend")
        assert result["ok"] is False
        assert "transaction" in result["error"].lower()

    assert target.is_dir()
    assert residue.is_dir()
    if extra_residue is not None:
        assert extra_residue.is_dir()
    assert config_path.read_bytes() == config_before
    _assert_canary_unchanged(config_path, external_before)
