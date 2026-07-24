"""Tests for persistent pause/resume configuration and persistent circuit breaker state."""

import pytest
from pathlib import Path
from argparse import Namespace

from hermes_cli.config import load_config, save_config
from hermes_cli.evolution.command import evolution_command
from hermes_cli.evolution.observer_service import ObserverService, CircuitBreakerOpen


def test_persistent_pause_and_resume(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    # Run pause
    args_pause = Namespace(action="pause", json=True)
    res_pause = evolution_command(args_pause)
    assert res_pause == 0

    cfg_pause = load_config()
    assert cfg_pause.get("autopoiesis", {}).get("enabled") is False

    # Run resume
    args_resume = Namespace(action="resume", json=True)
    res_resume = evolution_command(args_resume)
    assert res_resume == 0

    cfg_resume = load_config()
    assert cfg_resume.get("autopoiesis", {}).get("enabled") is True


def test_persistent_circuit_breaker(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    org_root = tmp_path / "organism"

    service1 = ObserverService(org_root, max_consecutive_errors=2)
    assert service1.circuit_open is False

    # Simulate errors to trip breaker
    try:
        service1.record_error()
    except Exception:
        pass
    service1.record_error()
    assert service1.circuit_open is True

    # Re-instantiate service (simulating CLI restart)
    service2 = ObserverService(org_root, max_consecutive_errors=2)
    assert service2.circuit_open is True

    # Reset circuit breaker
    service2.reset_circuit_breaker()
    assert service2.circuit_open is False

    # Re-instantiate service again
    service3 = ObserverService(org_root, max_consecutive_errors=2)
    assert service3.circuit_open is False
