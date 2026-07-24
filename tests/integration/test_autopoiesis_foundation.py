"""Real-process contract gate for the Project A local lifecycle foundation."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from hermes_cli.evolution.ledger import EvolutionLedger
from hermes_cli.evolution.pointers import validate_pointer
from hermes_cli.evolution.store import GenerationStore


ROOT = Path(__file__).resolve().parents[2]
PYTHON = Path(sys.executable)


def _environment(home: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update({
        "HERMES_HOME": str(home),
        "HADES_HOME": str(home),
        "LANG": "C",
        "LC_ALL": "C",
        "TZ": "UTC",
    })
    return environment


def _cli(home: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(PYTHON), "-m", "hermes_cli.main", "evolution", *arguments],
        cwd=ROOT,
        env=_environment(home),
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )


def _cli_with_environment(home: Path, environment: dict[str, str], *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(PYTHON), "-m", "hermes_cli.main", "evolution", *arguments],
        cwd=ROOT, env={**_environment(home), **environment}, text=True,
        capture_output=True, check=False, timeout=30,
    )


def _json(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    assert result.stderr == ""
    assert result.stdout.endswith("\n")
    assert result.stdout.count("\n") == 1
    value = json.loads(result.stdout)
    assert result.stdout == json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ) + "\n"
    return value


def _inventory(root: Path) -> tuple[tuple[object, ...], ...]:
    records: list[tuple[object, ...]] = []
    for path in [root, *sorted(root.rglob("*"))]:
        info = path.lstat()
        relative = "." if path == root else path.relative_to(root).as_posix()
        kind = stat.S_IFMT(info.st_mode)
        payload = path.read_bytes() if stat.S_ISREG(info.st_mode) else None
        records.append((relative, kind, stat.S_IMODE(info.st_mode), info.st_size,
                        info.st_mtime_ns, payload))
    return tuple(records)


def _assert_a4_mode_contract(root: Path, generation_id: str) -> None:
    expected = {
        ".": (stat.S_IFDIR, 0o700),
        ".lifecycle.lock": (stat.S_IFREG, 0o600),
        "evolution.db": (stat.S_IFREG, 0o600),
        "active.json": (stat.S_IFREG, 0o600),
        "last-known-good.json": (stat.S_IFREG, 0o600),
        "generations": (stat.S_IFDIR, 0o700),
        "generations/.publish.lock": (stat.S_IFREG, 0o600),
        f"generations/{generation_id}": (stat.S_IFDIR, 0o555),
        f"generations/{generation_id}/manifest.json": (stat.S_IFREG, 0o444),
    }
    actual = {
        relative: (kind, mode)
        for relative, kind, mode, _size, _mtime, _payload in _inventory(root)
    }
    assert actual == expected


def _assert_interrupted_inventory(root: Path, generation_id: str, pointers: set[str]) -> None:
    expected = {
        ".": (stat.S_IFDIR, 0o700),
        ".lifecycle.lock": (stat.S_IFREG, 0o600),
        "evolution.db": (stat.S_IFREG, 0o600),
        "generations": (stat.S_IFDIR, 0o700),
        "generations/.publish.lock": (stat.S_IFREG, 0o600),
        f"generations/{generation_id}": (stat.S_IFDIR, 0o555),
        f"generations/{generation_id}/manifest.json": (stat.S_IFREG, 0o444),
    }
    expected.update({name: (stat.S_IFREG, 0o600) for name in pointers})
    actual = {relative: (kind, mode) for relative, kind, mode, *_ in _inventory(root)}
    assert actual == expected


def _event_count(database: Path) -> int:
    connection = sqlite3.connect(database)
    try:
        return connection.execute("SELECT COUNT(*) FROM lifecycle_events").fetchone()[0]
    finally:
        connection.close()


def _assert_valid_event_chain(database: Path) -> None:
    ledger = EvolutionLedger(database)
    try:
        assert ledger.verify_chain() == []
    finally:
        ledger.connection.close()


def _assert_retained_baseline(root: Path, generation_id: str, pointers: set[str]) -> None:
    prior_home, prior_hades = os.environ.get("HERMES_HOME"), os.environ.get("HADES_HOME")
    os.environ["HERMES_HOME"] = str(root.parent)
    os.environ["HADES_HOME"] = str(root.parent)
    ledger = EvolutionLedger(root / "evolution.db")
    try:
        store = GenerationStore(root / "generations")
        assert store.verify(generation_id).generation_id == generation_id
        for name in pointers:
            document = json.loads((root / name).read_bytes())
            assert validate_pointer(document, ledger, store).generation_id == generation_id
    finally:
        ledger.connection.close()
        if prior_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = prior_home
        if prior_hades is None:
            os.environ.pop("HADES_HOME", None)
        else:
            os.environ["HADES_HOME"] = prior_hades


def _safe_show_records(home: Path, generation_id: str) -> dict[str, str]:
    database = home / "evolution" / "evolution.db"
    connection = sqlite3.connect(database)
    try:
        timestamp = "2026-07-24T00:00:00.000000Z"
        connection.execute(
            "INSERT INTO attempts VALUES (?,?,?,?,?)",
            ("attempt-alpha", "local", "operator", "draft", timestamp),
        )
        connection.execute(
            "INSERT INTO suggestions VALUES (?,?,?,?,?)",
            ("suggestion-alpha", "attempt-alpha", "a" * 64, "draft", timestamp),
        )
        connection.execute(
            "INSERT INTO blueprints VALUES (?,?,?,?,?)",
            ("blueprint-alpha", "attempt-alpha", "b" * 64, "draft", timestamp),
        )
        connection.execute(
            "INSERT INTO generations VALUES (?,?,?,?,?)",
            (generation_id, "attempt-alpha", generation_id, "draft", timestamp),
        )
        connection.execute(
            "INSERT INTO promotion_reports VALUES (?,?,?,?,?)",
            ("report-alpha", generation_id, "c" * 64, "draft", timestamp),
        )
        connection.commit()
    finally:
        connection.close()
    return {
        "suggestion": "suggestion-alpha",
        "blueprint": "b" * 64,
        "generation": generation_id,
        "report": "c" * 64,
    }


def test_real_cli_baseline_is_reopenable_private_and_read_only(tmp_path: Path) -> None:
    home = tmp_path / "home"
    help_result = _cli(home, "--help")
    assert help_result.returncode == 0
    assert "Initialize the immutable baseline" in help_result.stdout

    uninitialized = _cli(home, "status", "--json")
    assert uninitialized.returncode == 0
    assert _json(uninitialized)["status"] == "uninitialized"
    assert not (home / "evolution").exists()

    initialized = _cli(home, "init", "--json")
    assert initialized.returncode == 0
    status = _json(initialized)
    generation_id = status["active_generation_id"]
    assert isinstance(generation_id, str) and len(generation_id) == 64
    assert status["last_known_good_generation_id"] == generation_id

    reopened = _cli(home, "status", "--json")
    assert reopened.returncode == 0
    assert _json(reopened) == status
    root = home / "evolution"
    assert len(list((root / "generations").iterdir())) == 2  # lock + baseline
    assert _event_count(root / "evolution.db") == 1
    _assert_valid_event_chain(root / "evolution.db")
    assert (root / "active.json").read_bytes() == (root / "last-known-good.json").read_bytes()
    _assert_a4_mode_contract(root, generation_id)

    history = _cli(home, "history", "--limit", "1", "--after", "0", "--json")
    assert history.returncode == 0
    history_value = _json(history)
    assert len(history_value["items"]) == 1
    assert history_value["next_after"] == 1

    identifiers = _safe_show_records(home, generation_id)
    for kind, identifier in identifiers.items():
        found = _cli(home, "show", kind, identifier, "--json")
        assert found.returncode == 0
        assert _json(found)["status"] == "found"
        missing = _cli(home, "show", kind, "missing-alpha" if kind == "suggestion" else "f" * 64, "--json")
        assert missing.returncode == 1
        assert _json(missing)["status"] == "missing"

    before = _inventory(root)
    for arguments in (("status", "--json"), ("history", "--limit", "100", "--after", "0", "--json"),
                      *( ("show", kind, identifier, "--json") for kind, identifier in identifiers.items())):
        result = _cli(home, *arguments)
        assert result.returncode == 0
        _json(result)
    assert _inventory(root) == before


def test_real_cli_concurrent_initialization_converges_to_one_baseline(tmp_path: Path) -> None:
    home = tmp_path / "home"
    ready = [tmp_path / f"ready-{index}" for index in range(2)]
    start = tmp_path / "start"
    child = '''
import os, pathlib, sys, time
pathlib.Path(os.environ["A8_READY"]).touch()
deadline = time.monotonic() + 15
while not pathlib.Path(os.environ["A8_START"]).exists():
    if time.monotonic() > deadline:
        raise SystemExit(75)
    time.sleep(.01)
sys.argv = ["hermes", "evolution", "init", "--json"]
from hermes_cli.main import main
main()
'''
    commands = [
        subprocess.Popen(
            [str(PYTHON), "-c", child], cwd=ROOT,
            env={**_environment(home), "A8_READY": str(ready[index]), "A8_START": str(start)}, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        for index in range(2)
    ]
    try:
        deadline = time.monotonic() + 15
        while not all(path.exists() for path in ready):
            assert time.monotonic() < deadline
            time.sleep(.01)
        start.touch()
        results = [process.communicate(timeout=30) for process in commands]
        values = []
        for process, (stdout, stderr) in zip(commands, results, strict=True):
            result = subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
            assert process.returncode == 0, stderr
            values.append(_json(result))
    finally:
        for process in commands:
            if process.poll() is None:
                process.terminate()
        for process in commands:
            try:
                process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=3)
    generation_ids = {value["active_generation_id"] for value in values}
    assert len(generation_ids) == 1
    root = home / "evolution"
    assert _event_count(root / "evolution.db") == 1
    _assert_valid_event_chain(root / "evolution.db")
    assert (root / "active.json").read_bytes() == (root / "last-known-good.json").read_bytes()
    assert len([entry for entry in (root / "generations").iterdir() if entry.name != ".publish.lock"]) == 1


def test_durable_foundation_excludes_forbidden_fixture_material(tmp_path: Path) -> None:
    home = tmp_path / "home"
    initialized = _cli(home, "init", "--json")
    assert initialized.returncode == 0
    _safe_show_records(home, _json(initialized)["active_generation_id"])
    fixtures = (
        str(ROOT), "/Users/example/private", r"C:\\Users\\example\\private",
        "../private", "file:///private", "github_pat_" + "A" * 30,
        "Bearer eyJhbGciOiJIUzI1NiJ9.payload.signature",
        "prompt fixture that must never persist", "Traceback injected failure",
    )
    for fixture in fixtures:
        result = _cli_with_environment(
            home,
            {"AUTOP0IESIS_A8_AMBIENT": fixture},
            "show", "suggestion", fixture, "--json",
        )
        if fixture.startswith("github_pat_"):
            assert result.returncode == 1
            assert _json(result) == {
                "schema_version": 1,
                "status": "missing",
                "kind": "suggestion",
                "record": None,
            }
        else:
            assert result.returncode == 2
            assert "invalid evolution identifier" in result.stderr
        assert fixture not in result.stdout + result.stderr
    root = home / "evolution"
    payload = b"\n".join(
        path.read_bytes() for path in root.rglob("*") if path.is_file()
    ).decode("utf-8", errors="replace")
    connection = sqlite3.connect(root / "evolution.db")
    try:
        sqlite_text = "\n".join(connection.iterdump())
    finally:
        connection.close()
    for fixture in fixtures:
        assert fixture not in payload
        assert fixture not in sqlite_text


@pytest.mark.parametrize(
    ("boundary", "expected_exit", "pointers"),
    (("before-first", 1, set()), ("after-first", 1, {"active.json"}),
     ("before-second", 1, {"active.json"}),
     ("after-second", 0, {"active.json", "last-known-good.json"})),
)
def test_real_cli_pointer_interruption_recovers_one_baseline(
    tmp_path: Path, boundary: str, expected_exit: int, pointers: set[str]
) -> None:
    """Exercise the established pointer-write seam in a controlled CLI child."""
    home = tmp_path / "home"
    child = f'''\
import sys
from hermes_cli.evolution import pointers
original = pointers.atomic_write_pointer
calls = 0
boundary = {boundary!r}
def injected(path, document):
    global calls
    calls += 1
    if boundary == "before-first" and calls == 1:
        raise OSError("injected")
    if boundary == "before-second" and calls == 2:
        raise OSError("injected")
    original(path, document)
    if boundary == "after-first" and calls == 1:
        raise OSError("injected")
    if boundary == "after-second" and calls == 2:
        raise OSError("injected")
pointers.atomic_write_pointer = injected
sys.argv = ["hermes", "evolution", "init", "--json"]
from hermes_cli.main import main
main()
'''
    interrupted = subprocess.run(
        [str(PYTHON), "-c", child], cwd=ROOT, env=_environment(home), text=True,
        capture_output=True, check=False, timeout=30,
    )
    assert interrupted.returncode == 1
    interrupted_value = _json(interrupted)
    assert interrupted_value == {
        "schema_version": 1, "status": "blocked", "initialized": False,
        "overlay_enabled": False, "active_generation_id": None,
        "last_known_good_generation_id": None, "diagnostics": ["evolution_unavailable"],
    }
    assert not any(value in interrupted.stdout + interrupted.stderr for value in (str(home), str(ROOT), "Traceback", "injected"))
    recovered = _cli(home, "init", "--json")
    assert recovered.returncode == expected_exit
    recovered_value = _json(recovered)
    root = home / "evolution"
    assert _event_count(root / "evolution.db") == 1
    _assert_valid_event_chain(root / "evolution.db")
    generations = [entry for entry in (root / "generations").iterdir() if entry.name != ".publish.lock"]
    assert len(generations) == 1
    generation_id = generations[0].name
    _assert_interrupted_inventory(root, generation_id, pointers)
    _assert_retained_baseline(root, generation_id, pointers)
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE((root / "evolution.db").stat().st_mode) == 0o600
    assert stat.S_IMODE((root / "generations").stat().st_mode) == 0o700
    assert stat.S_IMODE(generations[0].stat().st_mode) == 0o555
    assert stat.S_IMODE((generations[0] / "manifest.json").stat().st_mode) == 0o444
    if recovered.returncode == 0:
        assert recovered_value["active_generation_id"] == generation_id
        assert (root / "active.json").read_bytes() == (root / "last-known-good.json").read_bytes()
        _assert_a4_mode_contract(root, generation_id)
    else:
        assert recovered_value == interrupted_value
