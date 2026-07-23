"""Safe subprocess facade for the packaged engineering review Node bundle."""

from __future__ import annotations

import ctypes
import json
import math
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from ctypes import wintypes
from pathlib import Path
from typing import BinaryIO, Mapping, Protocol

from hermes_constants import find_node_executable, with_hermes_node_path

from .protocol import (
    MAX_TRANSPORT_BYTES,
    EngineProtocolError,
    EngineRequest,
    EngineResponse,
)


DEFAULT_STDOUT_LIMIT = 4 * 1024 * 1024
DEFAULT_STDERR_LIMIT = 1024 * 1024
_DESCRIPTOR_BOOTSTRAP = "await import('file://'+process.argv[1]);"


class EngineExecutionError(RuntimeError):
    """Base class for failures to execute the engine safely."""


class EngineProcessError(EngineExecutionError):
    """The engine could not start or exited unsuccessfully."""


class EngineTimeoutError(EngineExecutionError, TimeoutError):
    """The engine exceeded its invocation deadline."""


class EngineCancelledError(EngineExecutionError):
    """The caller cancelled the engine invocation."""


class EngineOutputLimitError(EngineExecutionError):
    """The engine exceeded a bounded output channel."""


class EngineEvidenceError(EngineExecutionError):
    """Authoritative reviewer evidence is absent, forged, or unauthenticated."""


_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9


class _JobObjectBasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
        ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", wintypes.ULARGE_INTEGER),
        ("WriteOperationCount", wintypes.ULARGE_INTEGER),
        ("OtherOperationCount", wintypes.ULARGE_INTEGER),
        ("ReadTransferCount", wintypes.ULARGE_INTEGER),
        ("WriteTransferCount", wintypes.ULARGE_INTEGER),
        ("OtherTransferCount", wintypes.ULARGE_INTEGER),
    ]


class _JobObjectExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JobObjectBasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _WindowsJobApiProtocol(Protocol):
    def create_kill_on_close_job(self) -> object: ...

    def assign_process(self, job_handle: object, process_handle: object) -> None: ...

    def close_handle(self, job_handle: object) -> None: ...


class _CtypesWindowsJobApi:
    """Small ctypes wrapper around the Win32 Job Object calls we require."""

    def __init__(self) -> None:
        win_dll = getattr(ctypes, "WinDLL", None)
        if os.name != "nt" or win_dll is None:
            raise OSError("Windows Job Objects are unavailable on this platform")
        self._kernel32 = win_dll("kernel32", use_last_error=True)
        self._kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        self._kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        self._kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        self._kernel32.SetInformationJobObject.restype = wintypes.BOOL
        self._kernel32.AssignProcessToJobObject.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
        ]
        self._kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = wintypes.BOOL

    @staticmethod
    def _error(operation: str) -> OSError:
        get_last_error = getattr(ctypes, "get_last_error", lambda: 0)
        error_code = int(get_last_error())
        return OSError(error_code, f"{operation} failed with Win32 error {error_code}")

    def create_kill_on_close_job(self) -> object:
        handle = self._kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise self._error("CreateJobObjectW")
        information = _JobObjectExtendedLimitInformation()
        information.BasicLimitInformation.LimitFlags = (
            _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        if not self._kernel32.SetInformationJobObject(
            handle,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
            ctypes.byref(information),
            ctypes.sizeof(information),
        ):
            error = self._error("SetInformationJobObject")
            self._kernel32.CloseHandle(handle)
            raise error
        return handle

    def assign_process(self, job_handle: object, process_handle: object) -> None:
        if not self._kernel32.AssignProcessToJobObject(job_handle, process_handle):
            raise self._error("AssignProcessToJobObject")

    def close_handle(self, job_handle: object) -> None:
        if not self._kernel32.CloseHandle(job_handle):
            raise self._error("CloseHandle")


class _WindowsJobObject:
    """Own a kill-on-close Job Object assigned to one engine process tree."""

    def __init__(self, api: _WindowsJobApiProtocol, handle: object) -> None:
        self._api = api
        self._handle: object | None = handle

    @classmethod
    def create(
        cls,
        process: subprocess.Popen[bytes] | object,
        *,
        api: _WindowsJobApiProtocol | None = None,
    ) -> _WindowsJobObject:
        try:
            job_api = api if api is not None else _CtypesWindowsJobApi()
            handle = job_api.create_kill_on_close_job()
        except OSError as exc:
            raise EngineProcessError(
                f"could not establish Windows Job Object containment: {exc}"
            ) from exc
        job = cls(job_api, handle)
        process_handle = getattr(process, "_handle", None)
        if not process_handle:
            try:
                job.close()
            finally:
                raise EngineProcessError(
                    "could not establish Windows Job Object containment: "
                    "child process handle is unavailable"
                )
        try:
            job_api.assign_process(handle, process_handle)
        except OSError as exc:
            try:
                job.close()
            except EngineProcessError:
                pass
            raise EngineProcessError(
                f"could not establish Windows Job Object containment: {exc}"
            ) from exc
        return job

    def close(self) -> None:
        handle = self._handle
        if handle is None:
            return
        try:
            self._api.close_handle(handle)
        except OSError as exc:
            raise EngineProcessError(
                f"could not close Windows Job Object containment: {exc}"
            ) from exc
        self._handle = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


def _create_windows_job(
    process: subprocess.Popen[bytes],
) -> _WindowsJobObject | None:
    if os.name != "nt":
        return None
    return _WindowsJobObject.create(process)


def bundle_path() -> Path:
    """Return the absolute path of the engine bundled inside ``hermes_cli``."""
    return (
        Path(__file__).resolve().parent.parent
        / "engineering_dist"
        / "hermes-engineering.mjs"
    )


def windows_process_group_flags() -> int:
    """Return the creation flag used to isolate a Windows child process tree."""
    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))


def sanitized_engine_env(source: Mapping[str, str]) -> dict[str, str]:
    """Keep runtime essentials while dropping credentials and injection hooks."""
    exact_names = {
        "PATH",
        "HOME",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMDATA",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "TMP",
        "TEMP",
        "TMPDIR",
        "LANG",
        "LANGUAGE",
    }
    return {
        name: value
        for name, value in source.items()
        if name in exact_names or name.startswith("LC_")
    }


def _engine_process_env() -> dict[str, str]:
    """Add authority-selected Python probe paths after scrubbing caller env."""
    environment = sanitized_engine_env(with_hermes_node_path())
    environment["HERMES_ENGINE_PYTHON"] = sys.executable
    environment["HERMES_ENGINE_PYTHON_ROOT"] = str(Path(__file__).resolve().parents[2])
    return environment


def _canonical_request_bytes(
    request: EngineRequest,
    authenticated_reviewer_records: list[dict[str, object]] | None = None,
) -> bytes:
    wire = request.to_wire()
    if authenticated_reviewer_records is not None:
        wire["authenticatedReviewerRecords"] = authenticated_reviewer_records
    payload = json.dumps(
        wire,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(payload) > MAX_TRANSPORT_BYTES:
        raise EngineProtocolError("authenticated engine transport exceeds 4 MiB")
    return payload


def _file_size(stream: BinaryIO) -> int:
    return os.fstat(stream.fileno()).st_size


def _read_bounded(stream: BinaryIO, *, limit: int, channel: str) -> bytes:
    size = _file_size(stream)
    if size > limit:
        raise EngineOutputLimitError(
            f"engine {channel} exceeded the configured {limit}-byte limit"
        )
    stream.seek(0)
    return stream.read(limit + 1)


def _terminate_windows_process_tree(
    process: subprocess.Popen[bytes] | object,
    *,
    job: _WindowsJobObject | None,
    grace: float,
) -> None:
    """Stop a Windows engine tree, using Job close as the enforcement boundary."""
    cleanup_error: EngineProcessError | None = None
    if job is not None:
        if process.poll() is None:
            try:
                process.send_signal(getattr(signal, "CTRL_BREAK_EVENT", signal.SIGTERM))
            except OSError:
                pass
            try:
                process.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                pass
        try:
            job.close()
        except EngineProcessError as exc:
            cleanup_error = exc
    elif process.poll() is None:
        # This path is used only while failing an invocation because enforceable
        # Job containment could not be established. Kill the tree immediately;
        # never continue an engine run with direct-PID-only cleanup semantics.
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                capture_output=True,
                timeout=max(grace, 0.1),
                env=sanitized_engine_env(with_hermes_node_path()),
            )
        except (OSError, subprocess.SubprocessError):
            try:
                process.kill()
            except OSError:
                pass

    try:
        process.wait(timeout=max(grace, 0.1))
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=max(grace, 0.1))
        except subprocess.TimeoutExpired:
            pass
    if cleanup_error is not None:
        raise cleanup_error


def _terminate_process_group(
    process: subprocess.Popen[bytes],
    *,
    grace: float,
    windows_job: _WindowsJobObject | None = None,
) -> None:
    """Terminate and then kill the complete isolated child process group."""
    if os.name == "nt":
        _terminate_windows_process_tree(process, job=windows_job, grace=grace)
        return

    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except OSError:
            pass
        if grace:
            time.sleep(grace)
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass

        try:
            process.wait(timeout=max(grace, 0.1))
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except OSError:
                pass
            try:
                process.wait(timeout=max(grace, 0.1))
            except subprocess.TimeoutExpired:
                pass


def _cleanup_started_process(
    process: subprocess.Popen[bytes],
    *,
    windows_job: _WindowsJobObject | None,
    grace: float,
) -> None:
    """Stop a live child and always release its optional Job Object handle."""
    try:
        if process.poll() is None:
            _terminate_process_group(
                process,
                grace=grace,
                windows_job=windows_job,
            )
    finally:
        if windows_job is not None:
            windows_job.close()


def _parse_single_response(raw: bytes, request_id: str) -> EngineResponse:
    try:
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"invalid JSON constant: {value}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise EngineProtocolError(
            "engine stdout must contain exactly one JSON document"
        ) from exc
    return EngineResponse.from_wire(value, expected_request_id=request_id)


class EngineeringReviewBridge:
    """Invoke one request against the packaged engine with bounded resources."""

    def __init__(
        self,
        *,
        bundle: Path | None = None,
        require_authority: bool = False,
        stdout_limit: int = DEFAULT_STDOUT_LIMIT,
        stderr_limit: int = DEFAULT_STDERR_LIMIT,
        poll_interval: float = 0.02,
        termination_grace: float = 0.25,
    ) -> None:
        if stdout_limit < 1 or stderr_limit < 1:
            raise ValueError("engine output limits must be positive")
        if poll_interval <= 0 or termination_grace < 0:
            raise ValueError("engine timing settings must be non-negative")
        self.bundle = Path(bundle) if bundle is not None else bundle_path()
        self._explicit_bundle = bundle is not None
        self.require_authority = require_authority
        self.stdout_limit = stdout_limit
        self.stderr_limit = stderr_limit
        self.poll_interval = poll_interval
        self.termination_grace = termination_grace

    def invoke(
        self,
        request: EngineRequest,
        timeout: float,
        cancel_event: threading.Event | None = None,
    ) -> EngineResponse:
        """Run the engine once or raise a typed execution/protocol failure."""
        if cancel_event is not None and cancel_event.is_set():
            raise EngineCancelledError("engineering review invocation cancelled")
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool):
            raise ValueError("timeout must be a positive finite number")
        if timeout <= 0 or not math.isfinite(timeout):
            raise ValueError("timeout must be a positive finite number")

        evidence_commands = {"check-coverage", "resolve-anchors", "compose-review"}
        authenticated_records: list[dict[str, object]] | None = None
        executable_bytes: bytes | None = None
        try:
            # Lazy import avoids the run module's bundle-provenance import
            # forming a module initialization cycle.
            from .runs import ReviewRun, ReviewRunError

            root = Path(request.artifact_root)
            run = ReviewRun.load(root.name, session_id=root.parent.name)
            executable_bytes, authenticated_records = run.authorize_engine_invocation(
                engine_bundle=self.bundle if self._explicit_bundle else None,
                require_evidence=request.command in evidence_commands,
                workspace=request.workspace,
            )
        except (OSError, ReviewRunError, ValueError) as exc:
            if self.require_authority or request.command in evidence_commands:
                raise EngineEvidenceError(
                    f"authoritative review engine is unavailable: {exc}"
                ) from exc
        payload = _canonical_request_bytes(request, authenticated_records)
        node = find_node_executable("node")
        if not node:
            raise EngineProcessError("a usable managed Node executable was not found")

        executable_file: BinaryIO | None = None
        argv = [node, str(self.bundle)]
        popen_extra: dict[str, object] = {}
        if executable_bytes is not None:
            if os.name == "nt":
                raise EngineProcessError(
                    "authoritative descriptor execution is unavailable on Windows"
                )
            executable_file = tempfile.TemporaryFile("w+b")
            executable_file.write(executable_bytes)
            executable_file.flush()
            executable_file.seek(0)
            descriptor = executable_file.fileno()
            descriptor_root = (
                "/proc/self/fd" if Path("/proc/self/fd").is_dir() else "/dev/fd"
            )
            descriptor_path = f"{descriptor_root}/{descriptor}"
            argv = [
                node,
                "--input-type=module",
                "--eval",
                _DESCRIPTOR_BOOTSTRAP,
                descriptor_path,
            ]
            popen_extra["pass_fds"] = (descriptor,)

        try:
            stdout_file = tempfile.TemporaryFile("w+b")
            stderr_file = tempfile.TemporaryFile("w+b")
            try:
                process = subprocess.Popen(
                    argv,
                    stdin=subprocess.PIPE,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    cwd=request.workspace,
                    env=_engine_process_env(),
                    start_new_session=(os.name != "nt"),
                    creationflags=windows_process_group_flags(),
                    shell=False,
                    **popen_extra,
                )
            except OSError as exc:
                raise EngineProcessError(
                    f"could not start engineering engine: {exc}"
                ) from exc

            windows_job: _WindowsJobObject | None = None
            try:
                windows_job = _create_windows_job(process)
            except EngineProcessError:
                _terminate_process_group(
                    process,
                    grace=self.termination_grace,
                    windows_job=None,
                )
                raise

            write_errors: list[BaseException] = []

            def write_request() -> None:
                assert process.stdin is not None
                try:
                    process.stdin.write(payload)
                    process.stdin.flush()
                except (BrokenPipeError, OSError) as exc:
                    write_errors.append(exc)
                finally:
                    try:
                        process.stdin.close()
                    except OSError:
                        pass

            try:
                writer = threading.Thread(
                    target=write_request,
                    name="engineering-review-stdin",
                    daemon=True,
                )
                writer.start()
            except BaseException:
                _cleanup_started_process(
                    process,
                    windows_job=windows_job,
                    grace=self.termination_grace,
                )
                raise
            deadline = time.monotonic() + float(timeout)
            failure: EngineExecutionError | None = None
            try:
                while process.poll() is None:
                    if _file_size(stdout_file) > self.stdout_limit:
                        failure = EngineOutputLimitError(
                            "engine stdout exceeded the configured "
                            f"{self.stdout_limit}-byte limit"
                        )
                        break
                    if _file_size(stderr_file) > self.stderr_limit:
                        failure = EngineOutputLimitError(
                            "engine stderr exceeded the configured "
                            f"{self.stderr_limit}-byte limit"
                        )
                        break
                    if cancel_event is not None and cancel_event.is_set():
                        failure = EngineCancelledError(
                            "engineering review invocation cancelled"
                        )
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        failure = EngineTimeoutError(
                            f"engineering review invocation exceeded {timeout:g} seconds"
                        )
                        break
                    time.sleep(min(self.poll_interval, remaining))

                if failure is not None:
                    _terminate_process_group(
                        process,
                        grace=self.termination_grace,
                        windows_job=windows_job,
                    )
                    raise failure

                writer.join(timeout=max(self.termination_grace, 0.1))
                stdout = _read_bounded(
                    stdout_file, limit=self.stdout_limit, channel="stdout"
                )
                stderr = _read_bounded(
                    stderr_file, limit=self.stderr_limit, channel="stderr"
                )
                if process.returncode != 0:
                    detail = stderr.decode("utf-8", errors="replace").strip()
                    suffix = f": {detail}" if detail else ""
                    raise EngineProcessError(
                        f"engineering engine exited with exit code {process.returncode}{suffix}"
                    )
                if write_errors:
                    raise EngineProcessError(
                        f"could not write engineering request: {write_errors[0]}"
                    )
                return _parse_single_response(stdout, request.request_id)
            finally:
                _cleanup_started_process(
                    process,
                    windows_job=windows_job,
                    grace=self.termination_grace,
                )
        finally:
            try:
                stdout_file.close()
            except UnboundLocalError:
                pass
            try:
                stderr_file.close()
            except UnboundLocalError:
                pass
            if executable_file is not None:
                executable_file.close()
