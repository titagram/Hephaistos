"""Kernel-held, profile-scoped serialization for evolution lifecycle changes."""

from __future__ import annotations

import errno
import json
import math
import os
import stat
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

from hermes_constants import get_hermes_home

from .contract import canonical_json_bytes
from .pointers import _active_profile

try:  # pragma: no branch - one API exists on supported platforms
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

try:
    import msvcrt
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None  # type: ignore[assignment]


_LOCK_NAME = ".lifecycle.lock"
_POLL_SECONDS = 0.025
_MAX_TIMEOUT_SECONDS = 3600.0


class LifecycleLockError(RuntimeError):
    """A bounded lifecycle-lock validation or platform failure."""


class LifecycleLockTimeout(LifecycleLockError, TimeoutError):
    """The profile lifecycle lease was not acquired before its deadline."""


@dataclass(frozen=True)
class LifecycleLease:
    """Bounded public facts about one held kernel lease."""

    schema_version: int
    pid: int
    profile_id: str
    acquired_at: str
    lock_path: Path


def _fail(code: str = "unsafe_lifecycle_lock_path") -> None:
    raise LifecycleLockError(code)


def _same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _validate_directory(info: os.stat_result) -> None:
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
        or (
            hasattr(os, "geteuid")
            and info.st_uid != os.geteuid()
        )
        or (
            os.name == "posix"
            and stat.S_IMODE(info.st_mode) != 0o700
        )
    ):
        _fail()


def _validate_lock_file(info: os.stat_result) -> None:
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or (
            hasattr(os, "geteuid")
            and info.st_uid != os.geteuid()
        )
        or (
            os.name == "posix"
            and stat.S_IMODE(info.st_mode) != 0o600
        )
    ):
        _fail()


def _private_directory(path: Path) -> os.stat_result:
    try:
        info = path.lstat()
    except FileNotFoundError:
        try:
            path.mkdir(mode=0o700, parents=True, exist_ok=False)
            path.chmod(0o700)
            info = path.lstat()
        except (OSError, TypeError, NotImplementedError) as exc:
            raise LifecycleLockError(
                "unsafe_lifecycle_lock_path"
            ) from exc
    except (OSError, TypeError, NotImplementedError) as exc:
        raise LifecycleLockError("unsafe_lifecycle_lock_path") from exc
    _validate_directory(info)
    return info


def _open_directory(path: Path, expected: os.stat_result) -> int | None:
    if os.name != "posix":
        return None
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        _validate_directory(opened)
        if not _same_inode(expected, opened):
            _fail()
        return descriptor
    except BaseException:
        if "descriptor" in locals():
            os.close(descriptor)
        raise


def _open_lock_file(
    path: Path,
    root_descriptor: int | None,
) -> int:
    flags = os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY

    def open_target(open_flags: int, mode: int = 0o777) -> int:
        if root_descriptor is not None:
            return os.open(
                path.name,
                open_flags,
                mode,
                dir_fd=root_descriptor,
            )
        return os.open(path, open_flags, mode)

    try:
        try:
            linked = path.lstat()
        except FileNotFoundError:
            linked = None
        if linked is None:
            try:
                descriptor = open_target(
                    flags | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
                if hasattr(os, "fchmod"):
                    os.fchmod(descriptor, 0o600)
            except FileExistsError:
                linked = path.lstat()
                _validate_lock_file(linked)
                descriptor = open_target(flags)
        else:
            _validate_lock_file(linked)
            descriptor = open_target(flags)
        opened = os.fstat(descriptor)
        _validate_lock_file(opened)
        relinked = path.lstat()
        _validate_lock_file(relinked)
        if not _same_inode(opened, relinked):
            _fail()
        return descriptor
    except BaseException:
        if "descriptor" in locals():
            os.close(descriptor)
        raise


def _validate_timeout(value: object) -> float:
    if (
        isinstance(value, bool)
        or type(value) not in {int, float}
        or not math.isfinite(value)
        or value < 0
        or value > _MAX_TIMEOUT_SECONDS
    ):
        raise LifecycleLockError("invalid_lifecycle_lock_timeout")
    return float(value)


def _would_block(error: OSError) -> bool:
    return isinstance(error, BlockingIOError) or error.errno in {
        errno.EACCES,
        errno.EAGAIN,
    }


def _try_lock(descriptor: int) -> None:
    if fcntl is not None:
        fcntl.flock(
            descriptor,
            fcntl.LOCK_EX | fcntl.LOCK_NB,
        )
        return
    if msvcrt is not None:
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b" ")
            os.fsync(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        return
    raise LifecycleLockError("native_lifecycle_lock_unavailable")


def _unlock(descriptor: int) -> None:
    if fcntl is not None:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    elif msvcrt is not None:
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)


def _acquire(descriptor: int, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            _try_lock(descriptor)
            return
        except LifecycleLockError:
            raise
        except OSError as exc:
            if not _would_block(exc):
                raise LifecycleLockError(
                    "native_lifecycle_lock_failure"
                ) from exc
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LifecycleLockTimeout(
                    "lifecycle_lock_timeout"
                ) from None
            time.sleep(min(_POLL_SECONDS, remaining))


def _write_diagnostics(
    descriptor: int,
    lease: LifecycleLease,
) -> None:
    data = canonical_json_bytes(
        {
            "schema_version": lease.schema_version,
            "pid": lease.pid,
            "profile_id": lease.profile_id,
            "acquired_at": lease.acquired_at,
        }
    )
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        os.ftruncate(descriptor, 0)
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                _fail()
            view = view[written:]
        os.fsync(descriptor)
    except LifecycleLockError:
        raise
    except (OSError, TypeError, NotImplementedError) as exc:
        raise LifecycleLockError(
            "lifecycle_lock_diagnostics_failure"
        ) from exc


def _verify_paths(
    *,
    home: Path,
    home_info: os.stat_result,
    home_descriptor: int | None,
    root: Path,
    root_info: os.stat_result,
    root_descriptor: int | None,
    lock_path: Path,
    lock_descriptor: int,
) -> None:
    try:
        linked_home = home.lstat()
        linked_root = root.lstat()
        linked_lock = lock_path.lstat()
        retained_home = (
            os.fstat(home_descriptor)
            if home_descriptor is not None
            else home_info
        )
        retained_root = (
            os.fstat(root_descriptor)
            if root_descriptor is not None
            else root_info
        )
        retained_lock = os.fstat(lock_descriptor)
        _validate_directory(linked_home)
        _validate_directory(retained_home)
        _validate_directory(linked_root)
        _validate_directory(retained_root)
        _validate_lock_file(linked_lock)
        _validate_lock_file(retained_lock)
        if (
            not _same_inode(linked_home, retained_home)
            or not _same_inode(linked_root, retained_root)
            or not _same_inode(linked_lock, retained_lock)
        ):
            _fail()
    except LifecycleLockError:
        raise
    except (OSError, TypeError, NotImplementedError) as exc:
        raise LifecycleLockError("unsafe_lifecycle_lock_path") from exc


@contextmanager
def lifecycle_lock(
    *,
    timeout_seconds: float = 30.0,
) -> Iterator[LifecycleLease]:
    """Hold the profile lifecycle authority through a bounded kernel lock."""

    timeout = _validate_timeout(timeout_seconds)
    if fcntl is None and msvcrt is None:
        raise LifecycleLockError("native_lifecycle_lock_unavailable")

    home = Path(get_hermes_home())
    home_info = _private_directory(home)
    home_descriptor = _open_directory(home, home_info)
    root = home / "evolution"
    root_descriptor: int | None = None
    lock_descriptor: int | None = None
    acquired_descriptor: int | None = None
    body_failed = False
    cleanup_error: BaseException | None = None
    try:
        if fcntl is not None:
            if home_descriptor is None:
                raise LifecycleLockError(
                    "native_lifecycle_lock_unavailable"
                )
            acquired_descriptor = home_descriptor
            _acquire(acquired_descriptor, timeout)

        root_info = _private_directory(root)
        root_descriptor = _open_directory(root, root_info)
        lock_path = root / _LOCK_NAME
        lock_descriptor = _open_lock_file(
            lock_path,
            root_descriptor,
        )
        if fcntl is None:
            acquired_descriptor = lock_descriptor
            _acquire(acquired_descriptor, timeout)

        lease = LifecycleLease(
            schema_version=1,
            pid=os.getpid(),
            profile_id=_active_profile(),
            acquired_at=datetime.now(UTC).strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ"
            ),
            lock_path=lock_path,
        )
        _write_diagnostics(lock_descriptor, lease)
        _verify_paths(
            home=home,
            home_info=home_info,
            home_descriptor=home_descriptor,
            root=root,
            root_info=root_info,
            root_descriptor=root_descriptor,
            lock_path=lock_path,
            lock_descriptor=lock_descriptor,
        )
        try:
            yield lease
        except BaseException:
            body_failed = True
            raise
        finally:
            try:
                _verify_paths(
                    home=home,
                    home_info=home_info,
                    home_descriptor=home_descriptor,
                    root=root,
                    root_info=root_info,
                    root_descriptor=root_descriptor,
                    lock_path=lock_path,
                    lock_descriptor=lock_descriptor,
                )
            except BaseException as exc:
                cleanup_error = exc
    finally:
        if acquired_descriptor is not None:
            try:
                _unlock(acquired_descriptor)
            except OSError:
                if cleanup_error is None:
                    cleanup_error = LifecycleLockError(
                        "native_lifecycle_unlock_failure"
                    )
        if lock_descriptor is not None:
            os.close(lock_descriptor)
        if root_descriptor is not None:
            os.close(root_descriptor)
        if home_descriptor is not None:
            os.close(home_descriptor)
        if cleanup_error is not None and not body_failed:
            raise cleanup_error
