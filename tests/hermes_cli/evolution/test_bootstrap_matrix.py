"""Real bootstrap identity, convergence, refusal, and cleanup contracts."""

from __future__ import annotations

import json
import multiprocessing
import sqlite3
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_cli import __version__
from hermes_cli.evolution import bootstrap as bootstrap_module
from hermes_cli.evolution.bootstrap import (
    EvolutionBootstrapError,
    _repository_commit,
    ensure_evolution_initialized,
)
from hermes_cli.evolution.contract import content_digest
from hermes_cli.evolution.ledger import EvolutionLedger


def _bootstrap_child(queue) -> None:
    try:
        baseline = ensure_evolution_initialized()
        queue.put(("ok", baseline.generation_id))
    except BaseException as exc:
        queue.put(("error", type(exc).__name__, str(exc)))


def _non_lock_inventory(root: Path) -> tuple[tuple[object, ...], ...]:
    records: list[tuple[object, ...]] = []
    if not root.exists():
        return ()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if relative == ".lifecycle.lock":
            continue
        info = path.lstat()
        payload = path.read_bytes() if stat.S_ISREG(info.st_mode) else None
        records.append((
            relative,
            stat.S_IFMT(info.st_mode),
            stat.S_IMODE(info.st_mode),
            info.st_size,
            info.st_mtime_ns,
            payload,
        ))
    return tuple(records)


def test_two_actual_processes_create_one_event_equal_pointers_and_one_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    processes = [
        context.Process(target=_bootstrap_child, args=(queue,)) for _ in range(2)
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0
    results = [queue.get(timeout=5) for _ in processes]

    assert {result[0] for result in results} == {"ok"}, results
    generation_ids = {result[1] for result in results}
    assert len(generation_ids) == 1
    generation_id = generation_ids.pop()
    root = home / "evolution"
    active_bytes = (root / "active.json").read_bytes()
    lkg_bytes = (root / "last-known-good.json").read_bytes()
    assert active_bytes == lkg_bytes
    pointer = json.loads(active_bytes)
    assert pointer["generation_id"] == generation_id

    ledger = EvolutionLedger(root / "evolution.db")
    try:
        history = ledger.history()
        assert ledger.verify_chain() == []
    finally:
        ledger.connection.close()
    assert len(history) == 1
    assert history[0].event_sequence == 1
    assert history[0].event_type == "baseline_designated"
    assert history[0].generation_id is None
    generation_directories = {
        path.name for path in (root / "generations").iterdir() if path.is_dir()
    }
    assert generation_directories == {generation_id}


@pytest.mark.parametrize(
    "repository_commit",
    ["1" * 40, None],
)
def test_baseline_binds_release_compatibility_builder_commit_and_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    repository_commit: str | None,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    config = {
        "unrelated": {"preserved": True},
        "evolution": {
            "enabled": False,
            "observer": {
                "enabled": True,
                "recurrence_threshold": 11,
                "scan_interval_seconds": 42,
                "notice_min_score": 0.75,
            },
            "authorization": {
                "research_ttl_seconds": 61,
                "build_ttl_seconds": 62,
                "promotion_ttl_seconds": 63,
            },
            "retention": {
                "workspaces": 4,
                "evidence_days": 5,
            },
        },
    }
    monkeypatch.setattr(bootstrap_module, "load_config", lambda: config)
    monkeypatch.setattr(
        bootstrap_module,
        "_repository_commit",
        lambda: repository_commit,
    )

    baseline = ensure_evolution_initialized()

    manifest = baseline.manifest
    stable_base = manifest["stable_base"]
    assert manifest["schema_version"] == 1
    assert manifest["builder_version"] == __version__
    assert manifest["build_environment"] == {
        "builder": "hermes",
        "version": __version__,
    }
    assert stable_base["release"] == __version__
    assert stable_base["compatibility_version"] == __version__
    assert manifest["compatibility_range"] == __version__
    assert stable_base["repository_commit"] == repository_commit
    assert stable_base["configuration_fingerprint"] == content_digest(
        config["evolution"],
        domain="hades-evolution-config-v1",
    )


def test_repository_commit_is_installation_bound_and_validated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    commit = "a" * 40

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured.update(kwargs)
        return SimpleNamespace(returncode=0, stdout=f"{commit}\n")

    monkeypatch.setattr(bootstrap_module.subprocess, "run", fake_run)

    assert _repository_commit() == commit
    assert captured["argv"] == ["git", "rev-parse", "HEAD"]
    assert captured["cwd"] == Path(bootstrap_module.__file__).resolve().parents[2]
    assert captured["timeout"] == 2
    assert captured["check"] is False
    assert "shell" not in captured


@pytest.mark.parametrize(
    ("baked", "expected"),
    [
        ("b" * 40, "b" * 40),
        ("B" * 40, None),
        ("short", None),
        (None, None),
    ],
)
def test_repository_commit_uses_only_a_valid_baked_fallback(
    monkeypatch: pytest.MonkeyPatch,
    baked: str | None,
    expected: str | None,
) -> None:
    import hermes_cli.build_info as build_info

    monkeypatch.setattr(
        bootstrap_module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout=""),
    )
    monkeypatch.setattr(
        build_info,
        "get_build_sha",
        lambda *, short: baked,
    )

    assert _repository_commit() == expected


@pytest.mark.parametrize("state", ["foreign", "partial-pointer"])
def test_partial_or_foreign_state_refuses_without_mutating_retained_members(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
) -> None:
    home = tmp_path / "home"
    root = home / "evolution"
    home.mkdir(mode=0o700)
    root.mkdir(mode=0o700)
    home.chmod(0o700)
    root.chmod(0o700)
    (root / ".lifecycle.lock").write_bytes(b"")
    (root / ".lifecycle.lock").chmod(0o600)
    if state == "foreign":
        (root / "foreign.marker").write_bytes(b"retained-foreign")
    else:
        (root / "active.json").write_bytes(b'{"partial":true}')
    monkeypatch.setenv("HERMES_HOME", str(home))
    before = _non_lock_inventory(root)

    with pytest.raises(
        EvolutionBootstrapError,
        match="existing_state_requires_reconciliation",
    ):
        ensure_evolution_initialized()

    assert _non_lock_inventory(root) == before
    assert not (root / "evolution.db").exists()
    assert not (root / "generations").exists()
    assert not (root / "last-known-good.json").exists()


def test_injected_pointer_failure_closes_the_bootstrap_ledger_connection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    captured: list[sqlite3.Connection] = []

    def fail_pointer_initialization(ledger, store, baseline):
        captured.append(ledger.connection)
        raise RuntimeError("injected-pointer-failure")

    monkeypatch.setattr(
        bootstrap_module,
        "initialize_baseline_pointers",
        fail_pointer_initialization,
    )

    with pytest.raises(RuntimeError, match="injected-pointer-failure"):
        ensure_evolution_initialized()

    assert len(captured) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        captured[0].execute("SELECT 1")
