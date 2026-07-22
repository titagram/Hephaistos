"""Safe subprocess facade for the packaged engineering review Node bundle."""

from __future__ import annotations

import json
import math
import os
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import BinaryIO, Mapping

from hermes_constants import find_node_executable, with_hermes_node_path

from .protocol import EngineProtocolError, EngineRequest, EngineResponse


DEFAULT_STDOUT_LIMIT = 4 * 1024 * 1024
DEFAULT_STDERR_LIMIT = 1024 * 1024


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


def _canonical_request_bytes(request: EngineRequest) -> bytes:
    return json.dumps(
        request.to_wire(),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


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


def _terminate_process_group(process: subprocess.Popen[bytes], *, grace: float) -> None:
    """Terminate and then kill the complete isolated child process group."""
    if os.name == "nt":
        try:
            process.send_signal(getattr(signal, "CTRL_BREAK_EVENT", signal.SIGTERM))
        except OSError:
            pass
        try:
            process.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            pass
        if process.poll() is None:
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

        payload = _canonical_request_bytes(request)
        node = find_node_executable("node")
        if not node:
            raise EngineProcessError("a usable managed Node executable was not found")

        with (
            tempfile.TemporaryFile("w+b") as stdout_file,
            tempfile.TemporaryFile("w+b") as stderr_file,
        ):
            try:
                process = subprocess.Popen(
                    [node, str(self.bundle)],
                    stdin=subprocess.PIPE,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    cwd=request.workspace,
                    env=sanitized_engine_env(with_hermes_node_path()),
                    start_new_session=(os.name != "nt"),
                    creationflags=windows_process_group_flags(),
                    shell=False,
                )
            except OSError as exc:
                raise EngineProcessError(
                    f"could not start engineering engine: {exc}"
                ) from exc

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

            writer = threading.Thread(
                target=write_request,
                name="engineering-review-stdin",
                daemon=True,
            )
            writer.start()
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
                    _terminate_process_group(process, grace=self.termination_grace)
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
                if process.poll() is None:
                    _terminate_process_group(process, grace=self.termination_grace)
