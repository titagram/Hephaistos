"""Pass F — Global config isolation tests. Prove default-root and profile config are separate."""

import json
import pytest
from pathlib import Path


def test_global_config_isolated_from_profile(tmp_path, monkeypatch):
    """Default-root config says false, profile config says true. Global must return false."""
    import hermes_constants as _hc

    default_root = tmp_path / ".hermes"
    default_root.mkdir()
    profile_root = tmp_path / "profile"
    profile_root.mkdir()

    # Write default-root config
    (default_root / "config.yaml").write_text("autopoiesis:\n  enabled: false\n")

    # Write profile config
    (profile_root / "config.yaml").write_text("autopoiesis:\n  enabled: true\n")

    monkeypatch.setattr(_hc, "get_default_hermes_root", lambda: default_root)
    monkeypatch.setenv("HERMES_HOME", str(profile_root))
    monkeypatch.delenv("HERMES_PROFILE", raising=False)

    from hermes_cli.evolution.global_config import load_global_config, autopoiesis_enabled

    cfg = load_global_config()
    assert cfg["autopoiesis"]["enabled"] is False
    assert autopoiesis_enabled() is False


def test_save_global_config_preserves_other_keys(tmp_path, monkeypatch):
    """save_global_config must not destroy unrelated default-root keys."""
    import hermes_constants as _hc

    root = tmp_path / ".hermes"
    root.mkdir()
    (root / "config.yaml").write_text("model:\n  provider: openrouter\nautopoiesis:\n  enabled: false\n")

    monkeypatch.setattr(_hc, "get_default_hermes_root", lambda: root)

    from hermes_cli.evolution.global_config import save_global_config, load_global_config

    save_global_config({"enabled": True, "observer": {"enabled": False}})
    cfg = load_global_config()
    assert cfg["autopoiesis"]["enabled"] is True
    assert cfg["autopoiesis"]["observer"]["enabled"] is False

    # Unrelated key preserved
    import yaml
    raw = yaml.safe_load((root / "config.yaml").read_text())
    assert raw["model"]["provider"] == "openrouter"


# ── Pass I: Breaker durability ──

def test_breaker_state_atomic_write(tmp_path, monkeypatch):
    """Breaker state file must be written atomically via temp+rename,fail-closed on malformed."""
    import hermes_constants as _hc
    from hermes_cli.evolution import organism_home as _oh

    org = tmp_path / "organism"
    org.mkdir(parents=True)
    monkeypatch.setattr(_hc, "get_organism_home", lambda: org)
    monkeypatch.setattr(_oh, "get_organism_home", lambda: org)

    from hermes_cli.evolution.observer_service import ObserverService

    svc = ObserverService(org, max_consecutive_errors=2)
    assert svc.circuit_open is False

    # Trip the breaker via record_error to trigger save
    svc.record_error()
    svc.record_error()

    # Re-open service — must read persisted state
    svc2 = ObserverService(org, max_consecutive_errors=2)
    assert svc2.circuit_open is True

    # State file exists with correct content
    state = json.loads(svc2.state_file.read_text())
    assert state["circuit_open"] is True


def test_breaker_malformed_state_fail_closed(tmp_path, monkeypatch):
    """Malformed breaker state file opens circuit, preserves file."""
    import hermes_constants as _hc
    from hermes_cli.evolution import organism_home as _oh

    org = tmp_path / "organism"
    org.mkdir(parents=True)
    monkeypatch.setattr(_hc, "get_organism_home", lambda: org)
    monkeypatch.setattr(_oh, "get_organism_home", lambda: org)

    state_path = org / "observer_state.json"
    state_path.write_text("not valid json {{{")

    from hermes_cli.evolution.observer_service import ObserverService
    svc = ObserverService(org)
    assert svc.circuit_open is True  # fail-closed
    assert "corrupted" in (svc.degraded_reason or "")
    # File preserved for diagnosis
    assert state_path.exists()
    assert "not valid json" in state_path.read_text()
