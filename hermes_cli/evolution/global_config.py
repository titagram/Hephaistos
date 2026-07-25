"""Global autopoiesis configuration loader — reads from default-root config.yaml.

Never uses the profile-aware load_config()/save_config(). Always reads/writes
the default Hermes root config, not the active profile config.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

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
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, default_flow_style=False, allow_unicode=True)
    tmp.chmod(0o600)
    tmp.rename(path)


def load_global_config() -> dict[str, Any]:
    """Load autopoiesis config from default-root config.yaml, merged with defaults."""
    root = hermes_constants.get_default_hermes_root()
    config_path = root / "config.yaml"
    user_config = _read_yaml(config_path)

    defaults = {"autopoiesis": DEFAULT_CONFIG.get("autopoiesis", {})}
    result = dict(defaults)
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
