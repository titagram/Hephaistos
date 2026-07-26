"""Global autopoiesis configuration loader — reads from default-root config.yaml.

Never uses the profile-aware load_config()/save_config(). Always reads/writes
the default Hermes root config, not the active profile config.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import copy

from hermes_cli.config import DEFAULT_CONFIG
import hermes_constants


def _read_yaml(path: Path) -> dict[str, Any]:
    """Read a YAML file, returning empty dict on any error."""
    try:
        import yaml
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    """Write a YAML file atomically via temp+rename."""
    import yaml
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, default_flow_style=False, allow_unicode=True)
    tmp.chmod(0o600)
    tmp.rename(path)


def load_global_config() -> dict[str, Any]:
    """Load autopoiesis config from default-root config.yaml, merged with defaults.

    Returns an independent deep copy — mutating the result never alters
    ``hermes_cli.config.DEFAULT_CONFIG`` or any earlier/later return value.
    """
    root = hermes_constants.get_default_hermes_root()
    config_path = root / "config.yaml"
    user_config = _read_yaml(config_path)

    defaults = copy.deepcopy(DEFAULT_CONFIG.get("autopoiesis", {}))
    result: dict[str, Any] = {"autopoiesis": defaults}
    if "autopoiesis" in user_config and isinstance(user_config["autopoiesis"], dict):
        result["autopoiesis"].update(user_config["autopoiesis"])
    return result


def save_global_config(autopoiesis_config: dict[str, Any]) -> None:
    """Save autopoiesis config to default-root config.yaml, preserving other sections."""
    root = hermes_constants.get_default_hermes_root()
    config_path = root / "config.yaml"

    full_config = _read_yaml(config_path)
    full_config["autopoiesis"] = autopoiesis_config
    _write_yaml(config_path, full_config)


def autopoiesis_enabled() -> bool:
    """Check if autopoiesis is globally enabled."""
    cfg = load_global_config()
    return bool(cfg.get("autopoiesis", {}).get("enabled", False))


def observer_enabled() -> bool:
    """Check if the observer is enabled (autopoiesis + observer both on)."""
    cfg = load_global_config()
    aut_cfg = cfg.get("autopoiesis", {})
    if not aut_cfg.get("enabled", False):
        return False
    obs_cfg = aut_cfg.get("observer", {})
    return bool(obs_cfg.get("enabled", True))
