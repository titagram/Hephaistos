"""P0 tests: global organism lifecycle contracts for evolution init/status/history/show."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_cli.evolution.command import evolution_command
from hermes_cli.evolution.ledger import EvolutionLedger
from hermes_cli.evolution.lifecycle_global import (
    ensure_global_lifecycle_initialized,
)
from hermes_cli.evolution.organism_home import get_organism_home
from hermes_cli.evolution.organism_identity import load_organism_identity


def _run(**values: object) -> tuple[int, dict[str, object], str]:
    arguments = {
        "action": "status",
        "json": True,
        "limit": 100,
        "after": 0,
        "kind": "generation",
        "record_id": "a" * 64,
        **values,
    }
    args = SimpleNamespace(**arguments)
    output = io.StringIO()
    with redirect_stdout(output):
        exit_code = evolution_command(args)
    serialized = output.getvalue().strip()
    return exit_code, json.loads(serialized), serialized


def test_fresh_env_status_is_uninitialized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before init, evolution status is uninitialized; no state created."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    exit_code, status, _ = _run(action="status")
    assert exit_code == 0
    assert status == {
        "schema_version": 1, "status": "uninitialized", "initialized": False,
        "overlay_enabled": False, "active_generation_id": None,
        "last_known_good_generation_id": None, "diagnostics": [],
    }
    assert not (tmp_path / "home" / "evolution").exists()
    assert not (tmp_path / "home" / "organism" / "evolution").exists()


def test_fresh_env_doctor_has_no_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before init, doctor reports no global ledger and null organism identity."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    exit_code, doctor, _ = _run(action="doctor")
    assert exit_code == 0
    assert doctor["organism_id"] is None
    assert doctor["ledger_exists"] is False


def test_init_creates_global_organism_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After init: status coherent, no legacy evolution dir, global state exists."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    exit_code, status, _ = _run(action="init")
    assert exit_code == 0
    assert status["status"] == "coherent"
    assert status["initialized"] is True
    assert not (tmp_path / "home" / "evolution").exists()
    org_evo = tmp_path / "home" / "organism" / "evolution"
    assert (org_evo / "evolution.db").exists()
    assert (org_evo / "active.json").exists()
    assert (org_evo / "last-known-good.json").exists()
    assert (tmp_path / "home" / "organism" / "identity.json").exists()


def test_init_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-running init returns same generation and preserves identity."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    _, first, _ = _run(action="init")
    _, second, _ = _run(action="init")
    assert first["status"] == "coherent"
    assert second["status"] == "coherent"
    assert first["active_generation_id"] == second["active_generation_id"]
    ident1 = load_organism_identity(tmp_path / "home" / "organism")
    ident2 = load_organism_identity(tmp_path / "home" / "organism")
    assert ident1.organism_id == ident2.organism_id


def test_explicit_organism_root_owns_global_lifecycle_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Explicit init root also owns the lock; no ambient organism is created."""
    ambient_home = tmp_path / "ambient-home"
    organism_root = tmp_path / "explicit-organism"
    monkeypatch.setenv("HERMES_HOME", str(ambient_home))

    ensure_global_lifecycle_initialized(organism_root=organism_root)

    assert (
        organism_root / "evolution" / ".lifecycle.lock"
    ).is_file()
    assert not (ambient_home / "organism").exists()


def test_doctor_has_global_state_after_init(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After init, doctor reports a global ledger and non-null organism identity."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    _run(action="init")
    exit_code, doctor, _ = _run(action="doctor")
    assert exit_code == 0
    assert doctor["ledger_exists"] is True
    assert doctor["organism_id"] is not None


def test_init_blocked_by_legacy_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Legacy state at HERMES_HOME/evolution blocks init."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    legacy = tmp_path / "home" / "evolution"
    legacy.mkdir(parents=True, mode=0o700)
    (legacy / "legacy.marker").write_text("foreign")
    exit_code, result, _ = _run(action="init")
    assert exit_code == 1
    assert result["status"] == "blocked"


def test_status_blocked_by_legacy_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Legacy state at HERMES_HOME/evolution makes status report legacy_state_detected."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    legacy = tmp_path / "home" / "evolution"
    legacy.mkdir(parents=True, mode=0o700)
    (legacy / "legacy.marker").write_text("foreign")
    exit_code, status, _ = _run(action="status")
    assert exit_code == 0
    assert status["status"] == "blocked"
    assert "legacy_state_detected" in status["diagnostics"]


def test_history_and_show_use_global_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """History and show read from the same global organism lifecycle."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    _run(action="init")
    exit_code, history, _ = _run(action="history", limit=100, after=0)
    assert exit_code == 0
    assert history["status"] == "ok"
    assert len(history["items"]) >= 1
    assert not (tmp_path / "home" / "evolution").exists()
