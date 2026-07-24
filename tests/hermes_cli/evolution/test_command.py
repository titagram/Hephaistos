"""Public, read-only contracts for the Project A evolution command surface."""

from __future__ import annotations

import json
import multiprocessing
from pathlib import Path

import pytest

from hermes_cli.config import DEFAULT_CONFIG, _normalize_evolution_config
from hermes_cli.evolution.bootstrap import ensure_evolution_initialized
from hermes_cli.evolution.command import evolution_command


def _args(**values):
    return type("Args", (), {"json": True, "action": "status", **values})()


def _bootstrap_child(queue) -> None:
    try:
        queue.put(("ok", ensure_evolution_initialized().generation_id))
    except BaseException as exc:
        queue.put(("error", type(exc).__name__, str(exc)))


def test_evolution_defaults_and_normalization_are_strict_and_local() -> None:
    assert DEFAULT_CONFIG["evolution"]["observer"]["recurrence_threshold"] == 3
    result = _normalize_evolution_config({
        "other": {"kept": True},
        "evolution": {
            "enabled": 1,
            "observer": {"enabled": "yes", "recurrence_threshold": True,
                         "scan_interval_seconds": -1, "notice_min_score": float("nan")},
            "authorization": {"research_ttl_seconds": 0},
            "retention": {"workspaces": -1, "evidence_days": 3651},
        },
    })
    assert result["other"] == {"kept": True}
    assert result["evolution"] == DEFAULT_CONFIG["evolution"]


def test_status_is_canonical_uninitialized_and_does_not_create_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    assert evolution_command(_args()) == 0
    value = json.loads(capsys.readouterr().out)
    assert value == {
        "schema_version": 1, "status": "uninitialized", "initialized": False,
        "overlay_enabled": False, "active_generation_id": None,
        "last_known_good_generation_id": None, "diagnostics": [],
    }
    assert not (tmp_path / "home" / "evolution").exists()


def test_init_is_idempotent_and_status_is_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    first = ensure_evolution_initialized()
    second = ensure_evolution_initialized()
    assert first.generation_id == second.generation_id
    root = tmp_path / "home" / "evolution"
    before = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert evolution_command(_args()) == 0
    status = json.loads(capsys.readouterr().out)
    after = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert status["status"] == "coherent"
    assert before == after


def test_bootstrap_concurrent_processes_converge_on_one_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    processes = [context.Process(target=_bootstrap_child, args=(queue,)) for _ in range(2)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0
    results = [queue.get(timeout=3) for _ in processes]
    assert {result[0] for result in results} == {"ok"}, results
    assert len({result[1] for result in results}) == 1


def test_history_is_bounded_and_show_missing_is_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    ensure_evolution_initialized()
    assert evolution_command(_args(action="history", limit=1, after=0)) == 0
    history = json.loads(capsys.readouterr().out)
    assert set(history) == {"schema_version", "status", "items", "next_after"}
    assert len(history["items"]) == 1
    assert evolution_command(_args(action="show", kind="suggestion", record_id="not-a-real-suggestion")) == 1
    assert json.loads(capsys.readouterr().out) == {
        "schema_version": 1, "status": "missing", "kind": "suggestion", "record": None,
    }


def test_lock_only_root_is_uninitialized_and_malformed_arguments_are_parser_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    root = home / "evolution"
    root.mkdir(parents=True, mode=0o700)
    (root / ".lifecycle.lock").write_bytes(b"")
    (root / ".lifecycle.lock").chmod(0o600)
    monkeypatch.setenv("HERMES_HOME", str(home))
    assert evolution_command(_args()) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "uninitialized"


def test_foreign_root_keeps_history_and_show_failure_envelopes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    root = home / "evolution"
    root.mkdir(parents=True, mode=0o700)
    (root / "foreign.marker").write_text("foreign")
    monkeypatch.setenv("HERMES_HOME", str(home))
    assert evolution_command(_args(action="history", limit=1, after=0)) == 1
    assert set(json.loads(capsys.readouterr().out)) == {"schema_version", "status", "items", "next_after"}
    assert evolution_command(_args(action="show", kind="generation", record_id="a" * 64)) == 1
    assert set(json.loads(capsys.readouterr().out)) == {"schema_version", "status", "kind", "record"}
