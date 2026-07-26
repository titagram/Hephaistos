"""Pinned external plugins curated by the Hades distribution.

Curated plugins remain standalone projects. Hades installs only an immutable,
reviewed revision and records ownership locally so updates never overwrite a
user-managed plugin directory.
"""

from __future__ import annotations

import hmac
import json
import os
import shutil
import stat
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml


_MARKER_NAME = ".hades-managed.json"
_MARKER_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class CuratedPluginSpec:
    """Immutable source lock for a Hades-curated plugin."""

    name: str
    repository: str
    ref: str
    commit: str
    engine: str | None = None


@dataclass(frozen=True)
class CuratedPluginSyncResult:
    """Outcome of synchronizing one curated plugin."""

    name: str
    status: Literal["installed", "updated", "current", "preserved", "failed"]
    detail: str
    first_install: bool = False


HERMES_LCM_SPEC = CuratedPluginSpec(
    name="hermes-lcm",
    repository="https://github.com/stephenschoettler/hermes-lcm.git",
    ref="v0.20.0",
    commit="49e99a272d2d461e5c90732e7ef2bc20e96f0826",
    engine="lcm",
)

DEFAULT_CURATED_PLUGINS = (HERMES_LCM_SPEC,)
_CURATED_CONTEXT_ENGINES = {
    spec.name: spec.engine
    for spec in DEFAULT_CURATED_PLUGINS
    if spec.engine
}


def _result(
    spec: CuratedPluginSpec,
    status: Literal["installed", "updated", "current", "preserved", "failed"],
    detail: str,
    *,
    first_install: bool = False,
) -> CuratedPluginSyncResult:
    return CuratedPluginSyncResult(
        name=spec.name,
        status=status,
        detail=detail,
        first_install=first_install,
    )


def _is_symlink(path: Path) -> bool:
    try:
        return stat.S_ISLNK(path.lstat().st_mode)
    except FileNotFoundError:
        return False


def _read_marker(target: Path) -> dict | None:
    marker_path = target / _MARKER_NAME
    try:
        data = json.loads(marker_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _marker_owns_target(marker: dict, spec: CuratedPluginSpec) -> bool:
    return (
        marker.get("schema_version") == _MARKER_SCHEMA_VERSION
        and marker.get("name") == spec.name
        and marker.get("repository") == spec.repository
    )


def _marker_matches_lock(marker: dict, spec: CuratedPluginSpec) -> bool:
    return (
        _marker_owns_target(marker, spec)
        and marker.get("ref") == spec.ref
        and marker.get("commit") == spec.commit
    )


def _reject_symlinks(root: Path) -> None:
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(directory)
        for name in [*dirnames, *filenames]:
            candidate = base / name
            if _is_symlink(candidate):
                raise ValueError(f"plugin archive contains symlink: {candidate.name}")


def _write_marker(target: Path, spec: CuratedPluginSpec) -> None:
    marker = {
        "activation_applied": False,
        "commit": spec.commit,
        "name": spec.name,
        "ref": spec.ref,
        "repository": spec.repository,
        "schema_version": _MARKER_SCHEMA_VERSION,
    }
    marker_path = target / _MARKER_NAME
    payload = (json.dumps(marker, sort_keys=True, indent=2) + "\n").encode("utf-8")
    fd = os.open(marker_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)


def _set_activation_applied(target: Path) -> None:
    marker_path = target / _MARKER_NAME
    marker = _read_marker(target)
    if marker is None:
        raise ValueError("managed marker disappeared before activation")
    marker["activation_applied"] = True
    payload = (json.dumps(marker, sort_keys=True, indent=2) + "\n").encode("utf-8")
    fd, temp_name = tempfile.mkstemp(
        dir=target,
        prefix=".hades-managed-",
        suffix=".tmp",
    )
    temp_path = Path(temp_name)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, payload)
        os.fsync(fd)
        os.close(fd)
        fd = -1
        os.replace(temp_path, marker_path)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def _verify_staged_plugin(stage: Path, spec: CuratedPluginSpec) -> None:
    resolved = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=stage,
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    ).stdout.strip()
    if not hmac.compare_digest(resolved, spec.commit):
        raise ValueError(
            f"resolved commit {resolved or '(empty)'} does not match curated commit"
        )

    manifest_path = stage / "plugin.yaml"
    if not manifest_path.is_file() or _is_symlink(manifest_path):
        raise ValueError("plugin manifest is missing or unsafe")
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("name") != spec.name:
        raise ValueError("plugin manifest name does not match curated plugin")

    _reject_symlinks(stage)


def sync_curated_plugin(
    spec: CuratedPluginSpec,
    *,
    hermes_home: Path,
) -> CuratedPluginSyncResult:
    """Install or update one verified curated plugin.

    Failures are returned as data so setup/update can remain operational and
    use the built-in context compressor.
    """

    home = Path(hermes_home)
    plugins_dir = home / "plugins"
    target = plugins_dir / spec.name

    if _is_symlink(plugins_dir):
        return _result(spec, "failed", "plugins directory is a symlink")
    try:
        plugins_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _result(spec, "failed", f"cannot create plugins directory: {exc}")

    if _is_symlink(target):
        return _result(spec, "failed", "plugin target is a symlink")

    first_install = not target.exists()
    existing_marker: dict | None = None
    if not first_install:
        if not target.is_dir():
            return _result(spec, "preserved", "existing plugin target is not a directory")
        existing_marker = _read_marker(target)
        if existing_marker is None:
            return _result(spec, "preserved", "existing plugin is not Hades-managed")
        if not _marker_owns_target(existing_marker, spec):
            return _result(
                spec,
                "preserved",
                "existing plugin is managed by a different source",
            )
        if _marker_matches_lock(existing_marker, spec):
            return _result(spec, "current", "curated revision already installed")

    backup: Path | None = None
    try:
        with tempfile.TemporaryDirectory(
            prefix=f".{spec.name}-stage-",
            dir=plugins_dir,
        ) as temp_dir:
            stage = Path(temp_dir) / "plugin"
            clone = subprocess.run(
                [
                    "git",
                    "clone",
                    "--depth",
                    "1",
                    "--branch",
                    spec.ref,
                    spec.repository,
                    str(stage),
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if clone.returncode != 0:
                detail = (clone.stderr or clone.stdout or "git clone failed").strip()
                return _result(spec, "failed", detail.splitlines()[0])

            _verify_staged_plugin(stage, spec)
            shutil.rmtree(stage / ".git")
            _write_marker(stage, spec)

            if target.exists():
                backup = plugins_dir / f".{spec.name}-backup-{uuid.uuid4().hex}"
                os.replace(target, backup)
            try:
                os.replace(stage, target)
            except BaseException:
                if backup is not None and backup.exists() and not target.exists():
                    os.replace(backup, target)
                raise

        if backup is not None and backup.exists():
            shutil.rmtree(backup)
        return _result(
            spec,
            "installed" if first_install else "updated",
            "curated plugin installed",
            first_install=first_install,
        )
    except (
        OSError,
        subprocess.SubprocessError,
        UnicodeError,
        ValueError,
        yaml.YAMLError,
    ) as exc:
        if backup is not None and backup.exists() and not target.exists():
            try:
                os.replace(backup, target)
            except OSError:
                pass
        return _result(spec, "failed", str(exc))


def activate_curated_context_engine(
    spec: CuratedPluginSpec,
    *,
    hermes_home: Path,
) -> bool:
    """Enable a curated context engine without replacing another custom engine."""

    if not spec.engine:
        return True

    config_path = Path(hermes_home) / "config.yaml"
    if config_path.exists():
        try:
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except (OSError, UnicodeError, yaml.YAMLError):
            return False
        if not isinstance(raw, dict):
            return False
    else:
        raw = {}

    plugins = raw.get("plugins")
    if not isinstance(plugins, dict):
        plugins = {}
    enabled = plugins.get("enabled", [])
    disabled = plugins.get("disabled", [])
    enabled_set = set(enabled) if isinstance(enabled, list) else set()
    disabled_set = set(disabled) if isinstance(disabled, list) else set()

    context = raw.get("context")
    if not isinstance(context, dict):
        context = {}
    current_engine = str(context.get("engine") or "").strip()

    from utils import atomic_roundtrip_yaml_update

    try:
        atomic_roundtrip_yaml_update(
            config_path,
            "plugins.enabled",
            sorted(enabled_set | {spec.name}),
        )
        atomic_roundtrip_yaml_update(
            config_path,
            "plugins.disabled",
            sorted(disabled_set - {spec.name}),
        )
        if current_engine in {"", "compressor"}:
            atomic_roundtrip_yaml_update(
                config_path,
                "context.engine",
                spec.engine,
            )
    except (OSError, TypeError, ValueError, yaml.YAMLError):
        return False
    return True


def curated_plugin_is_selected(
    plugin_name: str,
    *,
    config: dict | None = None,
) -> bool:
    """Return whether a curated context-engine plugin may execute.

    General plugins can register hooks at import time. Gating the curated LCM
    plugin on the selected engine makes ``context.engine=compressor`` a real
    rollback rather than merely hiding LCM's tool schemas.
    """

    expected_engine = _CURATED_CONTEXT_ENGINES.get(plugin_name)
    if expected_engine is None:
        return True
    if config is None:
        try:
            from hermes_cli.config import load_config

            config = load_config()
        except Exception:
            return False
    context = config.get("context") if isinstance(config, dict) else None
    if not isinstance(context, dict):
        return False
    return str(context.get("engine") or "").strip() == expected_engine


def sync_default_plugins(
    *,
    hermes_home: Path | None = None,
) -> list[CuratedPluginSyncResult]:
    """Synchronize every Hades-curated default plugin."""

    if hermes_home is None:
        from hermes_constants import get_hermes_home

        hermes_home = get_hermes_home()
    home = Path(hermes_home)
    results: list[CuratedPluginSyncResult] = []
    for spec in DEFAULT_CURATED_PLUGINS:
        result = sync_curated_plugin(spec, hermes_home=home)
        target = home / "plugins" / spec.name
        marker = _read_marker(target) if target.is_dir() else None
        activation_pending = (
            result.status in {"installed", "updated", "current"}
            and marker is not None
            and marker.get("activation_applied") is not True
        )
        if activation_pending:
            if activate_curated_context_engine(spec, hermes_home=home):
                try:
                    _set_activation_applied(target)
                except (OSError, ValueError) as exc:
                    result = _result(
                        spec,
                        "failed",
                        f"plugin installed but activation marker failed: {exc}",
                    )
            else:
                result = _result(
                    spec,
                    "failed",
                    "plugin installed but context-engine activation failed",
                )
        results.append(result)
    return results
