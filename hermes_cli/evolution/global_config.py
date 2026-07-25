"""Global autopoiesis configuration loader — reads from default-root config.yaml."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hermes_cli.config import DEFAULT_CONFIG, load_config, save_config
from hermes_constants import get_default_hermes_root


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep-merge override into base, returning a new dict."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_global_config() -> dict[str, Any]:
    """Load autopoiesis config from default-root config.yaml, merged with defaults."""
    root = get_default_hermes_root()
    config_path = root / "config.yaml"

    user_config: dict[str, Any] = {}
    if config_path.exists():
        try:
            user_config = load_config()
        except Exception:
            pass

    # Only extract autopoiesis section, merge with defaults
    defaults = {"autopoiesis": DEFAULT_CONFIG.get("autopoiesis", {})}
    return _deep_merge(defaults, {"autopoiesis": user_config.get("autopoiesis", {})})


def save_global_config(autopoiesis_config: dict[str, Any]) -> None:
    """Save autopoiesis config to default-root config.yaml, preserving other sections."""
    root = get_default_hermes_root()
    config_path = root / "config.yaml"

    full_config: dict[str, Any] = {}
    if config_path.exists():
        try:
            full_config = load_config()
        except Exception:
            pass

    full_config["autopoiesis"] = autopoiesis_config
    save_config(full_config)


def autopoiesis_enabled() -> bool:
    """Check if autopoiesis is globally enabled."""
    cfg = load_global_config()
    return bool(cfg.get("autopoiesis", {}).get("enabled", False))
