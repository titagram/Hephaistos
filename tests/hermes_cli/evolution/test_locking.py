"""Real-process contracts for the profile-scoped evolution lifecycle lock."""

from __future__ import annotations

import json
import multiprocessing
import os
import stat
from pathlib import Path

import pytest

from hermes_cli.evolution import locking as locking_module
from hermes_cli.evolution.locking import (
    LifecycleLockError,
    LifecycleLockTimeout,
    lifecycle_lock,
)


def _use_home(home: Path) -> None:
    os.environ["HERMES_HOME"] = str(home)
    os.environ["HADES_HOME"] = str(home)


def _hold_lock(home: Path, ready, release, result) -> None:
    _use_home(home)
    try:
        with lifecycle_lock(timeout_seconds=2):
            ready.set()
            release.wait(timeout=10)
        result.put(("released",))
    except BaseException as exc:
        result.put(("error", type(exc).__name__, str(exc)))


def _crash_with_lock(home: Path, ready) -> None:
    _use_home(home)
    with lifecycle_lock(timeout_seconds=2):
        ready.set()
        os._exit(37)


def _join(process: multiprocessing.Process) -> None:
    process.join(timeout=15)
    if process.is_alive():
        process.terminate()
        process.join(timeout=5)
        pytest.fail("lifecycle-lock child did not terminate")


@pytest.fixture
def evolution_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "hermes-home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HADES_HOME", str(home))
    return home


def test_two_processes_cannot_hold_the_lifecycle_lease_concurrently(
    evolution_home: Path,
) -> None:
    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    result = context.Queue()
    holder = context.Process(
        target=_hold_lock,
        args=(evolution_home, ready, release, result),
    )
    holder.start()
    assert ready.wait(timeout=10)

    with pytest.raises(LifecycleLockTimeout, match="lifecycle_lock_timeout"):
        with lifecycle_lock(timeout_seconds=0.15):
            pytest.fail("a second process acquired the lifecycle lock")

    release.set()
    _join(holder)
    assert result.get(timeout=5) == ("released",)


def test_private_directory_revalidates_benign_concurrent_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "concurrent-home"
    original_mkdir = Path.mkdir

    def racing_mkdir(self: Path, *args, **kwargs) -> None:
        if self == path:
            original_mkdir(self, *args, **kwargs)
            raise FileExistsError
        return original_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", racing_mkdir)
    info = locking_module._private_directory(path)

    assert stat.S_ISDIR(info.st_mode)
    assert stat.S_IMODE(info.st_mode) == 0o700


def test_exception_releases_the_kernel_lease(evolution_home: Path) -> None:
    with pytest.raises(RuntimeError, match="injected"):
        with lifecycle_lock():
            raise RuntimeError("injected")

    with lifecycle_lock(timeout_seconds=0.2) as lease:
        assert lease.profile_id


def test_os_exit_releases_authority_without_pid_liveness(
    evolution_home: Path,
) -> None:
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    holder = context.Process(
        target=_crash_with_lock,
        args=(evolution_home, ready),
    )
    holder.start()
    assert ready.wait(timeout=10)
    _join(holder)
    assert holder.exitcode == 37

    with lifecycle_lock(timeout_seconds=0.5):
        pass


@pytest.mark.skipif(os.name != "posix", reason="POSIX unlink semantics")
def test_deleting_and_replacing_diagnostic_file_cannot_bypass_held_lease(
    evolution_home: Path,
) -> None:
    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    result = context.Queue()
    holder = context.Process(
        target=_hold_lock,
        args=(evolution_home, ready, release, result),
    )
    holder.start()
    assert ready.wait(timeout=10)
    path = evolution_home / "evolution" / ".lifecycle.lock"
    path.unlink()
    path.write_text("foreign replacement", encoding="utf-8")
    path.chmod(0o600)

    with pytest.raises(LifecycleLockTimeout):
        with lifecycle_lock(timeout_seconds=0.15):
            pytest.fail("replacement lockfile bypassed the durable anchor")

    release.set()
    _join(holder)
    assert result.get(timeout=5)[0] == "error"
    assert path.read_text(encoding="utf-8") == "foreign replacement"


def test_stale_diagnostics_are_ignored_and_replaced_with_bounded_schema(
    evolution_home: Path,
) -> None:
    root = evolution_home / "evolution"
    root.mkdir(mode=0o700)
    path = root / ".lifecycle.lock"
    path.write_text("{stale and not json", encoding="utf-8")
    path.chmod(0o600)

    with lifecycle_lock() as lease:
        payload = json.loads(path.read_bytes())
        assert payload == {
            "acquired_at": lease.acquired_at,
            "pid": lease.pid,
            "profile_id": lease.profile_id,
            "schema_version": 1,
        }
        assert path == lease.lock_path
        assert len(path.read_bytes()) < 1024


def test_missing_root_is_created_private_under_hostile_umask(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "new-home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HADES_HOME", str(home))
    old_umask = os.umask(0o777)
    try:
        with lifecycle_lock():
            pass
    finally:
        os.umask(old_umask)

    if os.name == "posix":
        assert stat.S_IMODE(home.stat().st_mode) == 0o700
        assert stat.S_IMODE((home / "evolution").stat().st_mode) == 0o700
        assert (
            stat.S_IMODE(
                (home / "evolution" / ".lifecycle.lock").stat().st_mode
            )
            == 0o600
        )


@pytest.mark.parametrize("kind", ["mode", "file", "symlink"])
def test_hostile_existing_evolution_root_fails_without_repair(
    evolution_home: Path,
    tmp_path: Path,
    kind: str,
) -> None:
    if kind == "mode" and os.name != "posix":
        pytest.skip("private mode validation is POSIX-only")
    root = evolution_home / "evolution"
    if kind == "mode":
        root.mkdir(mode=0o700)
        root.chmod(0o755)
    elif kind == "file":
        root.write_text("not a directory", encoding="utf-8")
    else:
        target = tmp_path / "target"
        target.mkdir(mode=0o700)
        root.symlink_to(target, target_is_directory=True)

    with pytest.raises(LifecycleLockError, match="unsafe_lifecycle_lock_path"):
        with lifecycle_lock():
            pass
    if kind == "mode":
        assert stat.S_IMODE(root.stat().st_mode) == 0o755


@pytest.mark.parametrize("kind", ["mode", "hardlink", "symlink", "directory"])
def test_hostile_existing_lock_endpoint_fails_closed(
    evolution_home: Path,
    tmp_path: Path,
    kind: str,
) -> None:
    if kind == "mode" and os.name != "posix":
        pytest.skip("private mode validation is POSIX-only")
    root = evolution_home / "evolution"
    root.mkdir(mode=0o700)
    path = root / ".lifecycle.lock"
    if kind == "mode":
        path.write_text("stale", encoding="utf-8")
        path.chmod(0o640)
    elif kind == "hardlink":
        target = tmp_path / "target"
        target.write_text("stale", encoding="utf-8")
        target.chmod(0o600)
        os.link(target, path)
    elif kind == "symlink":
        target = tmp_path / "target"
        target.write_text("stale", encoding="utf-8")
        path.symlink_to(target)
    else:
        path.mkdir(mode=0o700)

    with pytest.raises(LifecycleLockError, match="unsafe_lifecycle_lock_path"):
        with lifecycle_lock():
            pass
    if kind == "mode":
        assert stat.S_IMODE(path.stat().st_mode) == 0o640


def test_owner_mismatch_is_rejected(
    evolution_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not hasattr(os, "geteuid"):
        pytest.skip("owner validation is unavailable")
    root = evolution_home / "evolution"
    root.mkdir(mode=0o700)
    actual_uid = os.geteuid()
    monkeypatch.setattr(locking_module.os, "geteuid", lambda: actual_uid + 1)

    with pytest.raises(LifecycleLockError, match="unsafe_lifecycle_lock_path"):
        with lifecycle_lock():
            pass


@pytest.mark.parametrize(
    "timeout",
    [True, False, None, "1", float("nan"), float("inf"), -0.1, 3600.1],
)
def test_timeout_validation_is_exact_and_bounded(
    evolution_home: Path,
    timeout: object,
) -> None:
    with pytest.raises(
        LifecycleLockError,
        match="invalid_lifecycle_lock_timeout",
    ):
        with lifecycle_lock(timeout_seconds=timeout):  # type: ignore[arg-type]
            pass


def test_zero_timeout_is_a_supported_nonblocking_attempt(
    evolution_home: Path,
) -> None:
    with lifecycle_lock(timeout_seconds=0):
        pass


def test_unsupported_native_lock_platform_fails_closed(
    evolution_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(locking_module, "fcntl", None)
    monkeypatch.setattr(locking_module, "msvcrt", None)

    with pytest.raises(
        LifecycleLockError,
        match="native_lifecycle_lock_unavailable",
    ):
        with lifecycle_lock():
            pass


def test_msvcrt_branch_locks_and_unlocks_one_byte(
    evolution_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, int, int]] = []

    class FakeMsvcrt:
        LK_NBLCK = 11
        LK_UNLCK = 12

        @staticmethod
        def locking(descriptor: int, operation: int, length: int) -> None:
            calls.append((descriptor, operation, length))

    monkeypatch.setattr(locking_module, "fcntl", None)
    monkeypatch.setattr(locking_module, "msvcrt", FakeMsvcrt())

    with lifecycle_lock() as lease:
        assert lease.lock_path.exists()

    assert [operation for _, operation, _ in calls] == [11, 12]
    assert all(length == 1 for _, _, length in calls)
    assert calls[0][0] == calls[1][0]


@pytest.mark.skipif(os.name != "posix", reason="POSIX rename semantics")
def test_root_replacement_is_detected_before_cleanup(
    evolution_home: Path,
) -> None:
    root = evolution_home / "evolution"
    with pytest.raises(LifecycleLockError, match="unsafe_lifecycle_lock_path"):
        with lifecycle_lock():
            root.rename(evolution_home / "retained-evolution")
            root.mkdir(mode=0o700)
