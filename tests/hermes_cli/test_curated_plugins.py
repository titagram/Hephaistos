from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from hermes_cli.curated_plugins import (
    CuratedPluginSpec,
    CuratedPluginSyncResult,
    main as curated_plugins_main,
    sync_curated_plugin,
    sync_default_plugins,
)


def _run_git(repo: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Hades Tests",
        "GIT_AUTHOR_EMAIL": "hades-tests@example.invalid",
        "GIT_COMMITTER_NAME": "Hades Tests",
        "GIT_COMMITTER_EMAIL": "hades-tests@example.invalid",
    }
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return result.stdout.strip()


def _plugin_repo(tmp_path: Path, *, manifest_name: str = "hermes-lcm") -> tuple[Path, str]:
    repo = tmp_path / "source"
    repo.mkdir()
    _run_git(repo, "init", "--initial-branch=main")
    (repo / "plugin.yaml").write_text(
        f"name: {manifest_name}\nversion: 0.20.0\n",
        encoding="utf-8",
    )
    (repo / "__init__.py").write_text(
        "def register(ctx):\n    return None\n",
        encoding="utf-8",
    )
    _run_git(repo, "add", "plugin.yaml", "__init__.py")
    _run_git(repo, "commit", "-m", "test plugin")
    _run_git(repo, "tag", "v0.20.0")
    return repo, _run_git(repo, "rev-parse", "HEAD")


def _spec(repo: Path, commit: str) -> CuratedPluginSpec:
    return CuratedPluginSpec(
        name="hermes-lcm",
        repository=str(repo),
        ref="v0.20.0",
        commit=commit,
        engine="lcm",
    )


def test_sync_installs_verified_pinned_plugin(tmp_path: Path) -> None:
    repo, commit = _plugin_repo(tmp_path)
    home = tmp_path / "home"

    result = sync_curated_plugin(_spec(repo, commit), hermes_home=home)

    target = home / "plugins" / "hermes-lcm"
    assert result.status == "installed"
    assert result.first_install is True
    assert (target / "plugin.yaml").is_file()
    assert not (target / ".git").exists()
    marker = json.loads((target / ".hades-managed.json").read_text(encoding="utf-8"))
    assert marker == {
        "activation_applied": False,
        "commit": commit,
        "name": "hermes-lcm",
        "ref": "v0.20.0",
        "repository": str(repo),
        "schema_version": 1,
    }
    assert (target / ".hades-managed.json").stat().st_mode & 0o777 == 0o600


def test_sync_rejects_commit_that_differs_from_lock(tmp_path: Path) -> None:
    repo, _commit = _plugin_repo(tmp_path)
    home = tmp_path / "home"
    spec = _spec(repo, "0" * 40)

    result = sync_curated_plugin(spec, hermes_home=home)

    assert result.status == "failed"
    assert "commit" in result.detail.lower()
    assert not (home / "plugins" / "hermes-lcm").exists()


def test_sync_rejects_wrong_manifest_name(tmp_path: Path) -> None:
    repo, commit = _plugin_repo(tmp_path, manifest_name="other-plugin")
    home = tmp_path / "home"

    result = sync_curated_plugin(_spec(repo, commit), hermes_home=home)

    assert result.status == "failed"
    assert "manifest" in result.detail.lower()
    assert not (home / "plugins" / "hermes-lcm").exists()


def test_sync_preserves_unmanaged_existing_plugin(tmp_path: Path) -> None:
    repo, commit = _plugin_repo(tmp_path)
    home = tmp_path / "home"
    target = home / "plugins" / "hermes-lcm"
    target.mkdir(parents=True)
    sentinel = target / "user-owned.txt"
    sentinel.write_text("keep me", encoding="utf-8")

    result = sync_curated_plugin(_spec(repo, commit), hermes_home=home)

    assert result.status == "preserved"
    assert sentinel.read_text(encoding="utf-8") == "keep me"
    assert not (target / ".hades-managed.json").exists()


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
def test_sync_rejects_symlink_plugin_root(tmp_path: Path) -> None:
    repo, commit = _plugin_repo(tmp_path)
    home = tmp_path / "home"
    outside = tmp_path / "outside"
    outside.mkdir()
    home.mkdir()
    (home / "plugins").symlink_to(outside, target_is_directory=True)

    result = sync_curated_plugin(_spec(repo, commit), hermes_home=home)

    assert result.status == "failed"
    assert "symlink" in result.detail.lower()
    assert list(outside.iterdir()) == []


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
def test_sync_rejects_symlink_target(tmp_path: Path) -> None:
    repo, commit = _plugin_repo(tmp_path)
    home = tmp_path / "home"
    plugins = home / "plugins"
    outside = tmp_path / "outside"
    plugins.mkdir(parents=True)
    outside.mkdir()
    (plugins / "hermes-lcm").symlink_to(outside, target_is_directory=True)

    result = sync_curated_plugin(_spec(repo, commit), hermes_home=home)

    assert result.status == "failed"
    assert "symlink" in result.detail.lower()
    assert list(outside.iterdir()) == []


def test_sync_preserves_target_with_corrupt_marker(tmp_path: Path) -> None:
    repo, commit = _plugin_repo(tmp_path)
    home = tmp_path / "home"
    target = home / "plugins" / "hermes-lcm"
    target.mkdir(parents=True)
    (target / ".hades-managed.json").write_text("{broken", encoding="utf-8")
    sentinel = target / "sentinel"
    sentinel.write_text("keep", encoding="utf-8")

    result = sync_curated_plugin(_spec(repo, commit), hermes_home=home)

    assert result.status == "preserved"
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_sync_preserves_target_managed_by_other_repository(tmp_path: Path) -> None:
    repo, commit = _plugin_repo(tmp_path)
    home = tmp_path / "home"
    target = home / "plugins" / "hermes-lcm"
    target.mkdir(parents=True)
    marker = {
        "schema_version": 1,
        "name": "hermes-lcm",
        "repository": "https://example.invalid/other.git",
        "ref": "v0.20.0",
        "commit": commit,
    }
    (target / ".hades-managed.json").write_text(
        json.dumps(marker),
        encoding="utf-8",
    )
    sentinel = target / "sentinel"
    sentinel.write_text("keep", encoding="utf-8")

    result = sync_curated_plugin(_spec(repo, commit), hermes_home=home)

    assert result.status == "preserved"
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_sync_is_idempotent_for_current_managed_plugin(tmp_path: Path) -> None:
    repo, commit = _plugin_repo(tmp_path)
    home = tmp_path / "home"
    spec = _spec(repo, commit)
    first = sync_curated_plugin(spec, hermes_home=home)
    marker = home / "plugins" / "hermes-lcm" / ".hades-managed.json"
    before = marker.stat().st_mtime_ns

    second = sync_curated_plugin(spec, hermes_home=home)

    assert first.status == "installed"
    assert second.status == "current"
    assert second.first_install is False
    assert marker.stat().st_mtime_ns == before


def test_default_context_engine_is_lcm() -> None:
    from hermes_cli.config import DEFAULT_CONFIG

    assert DEFAULT_CONFIG["context"]["engine"] == "lcm"


def test_default_sync_activates_lcm_on_first_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, commit = _plugin_repo(tmp_path)
    home = tmp_path / "home"
    spec = _spec(repo, commit)
    monkeypatch.setattr(
        "hermes_cli.curated_plugins.DEFAULT_CURATED_PLUGINS",
        (spec,),
    )

    results = sync_default_plugins(hermes_home=home)

    raw = pytest.importorskip("yaml").safe_load(
        (home / "config.yaml").read_text(encoding="utf-8")
    )
    marker = json.loads(
        (home / "plugins" / "hermes-lcm" / ".hades-managed.json").read_text(
            encoding="utf-8"
        )
    )
    assert results[0].status == "installed"
    assert raw["context"]["engine"] == "lcm"
    assert "hermes-lcm" in raw["plugins"]["enabled"]
    assert "hermes-lcm" not in raw["plugins"].get("disabled", [])
    assert marker["activation_applied"] is True


def test_default_sync_does_not_undo_compressor_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, commit = _plugin_repo(tmp_path)
    home = tmp_path / "home"
    spec = _spec(repo, commit)
    monkeypatch.setattr(
        "hermes_cli.curated_plugins.DEFAULT_CURATED_PLUGINS",
        (spec,),
    )
    sync_default_plugins(hermes_home=home)
    from utils import atomic_roundtrip_yaml_update

    atomic_roundtrip_yaml_update(home / "config.yaml", "context.engine", "compressor")

    results = sync_default_plugins(hermes_home=home)

    raw = pytest.importorskip("yaml").safe_load(
        (home / "config.yaml").read_text(encoding="utf-8")
    )
    assert results[0].status == "current"
    assert raw["context"]["engine"] == "compressor"


def test_default_sync_preserves_other_selected_context_engine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, commit = _plugin_repo(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text(
        "context:\n  engine: another-engine\n",
        encoding="utf-8",
    )
    spec = _spec(repo, commit)
    monkeypatch.setattr(
        "hermes_cli.curated_plugins.DEFAULT_CURATED_PLUGINS",
        (spec,),
    )

    sync_default_plugins(hermes_home=home)

    raw = pytest.importorskip("yaml").safe_load(
        (home / "config.yaml").read_text(encoding="utf-8")
    )
    assert raw["context"]["engine"] == "another-engine"


def test_curated_plugins_module_command_reports_success(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "hermes_cli.curated_plugins.sync_default_plugins",
        lambda: [
            CuratedPluginSyncResult(
                name="hermes-lcm",
                status="current",
                detail="curated revision already installed",
            )
        ],
    )

    exit_code = curated_plugins_main(["sync-defaults"])

    assert exit_code == 0
    assert "hermes-lcm: current" in capsys.readouterr().out


def test_curated_plugins_module_command_is_nonzero_on_failed_sync(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        "hermes_cli.curated_plugins.sync_default_plugins",
        lambda: [
            CuratedPluginSyncResult(
                name="hermes-lcm",
                status="failed",
                detail="commit mismatch",
            )
        ],
    )

    exit_code = curated_plugins_main(["sync-defaults"])

    assert exit_code == 1
    assert "commit mismatch" in capsys.readouterr().err
