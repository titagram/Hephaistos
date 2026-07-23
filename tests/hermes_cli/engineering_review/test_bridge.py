from __future__ import annotations

import ctypes
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_cli.engineering_review import bridge
from hermes_cli.engineering_review.bridge import (
    EngineCancelledError,
    EngineOutputLimitError,
    EngineProcessError,
    EngineProtocolError,
    EngineTimeoutError,
    EngineeringReviewBridge,
    bundle_path,
)
from hermes_cli.engineering_review.protocol import (
    EngineDiagnostic,
    EngineRequest,
    EngineResponse,
)


PASSED_RESPONSE = {
    "protocolVersion": 1,
    "requestId": "r1",
    "status": "passed",
    "output": {},
    "diagnostics": [],
}


def request(tmp_path: Path, **changes: object) -> EngineRequest:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    reviews = tmp_path / "hermes" / "reviews"
    reviews.mkdir(parents=True, exist_ok=True)
    values: dict[str, object] = {
        "request_id": "r1",
        "command": "capture-target",
        "workspace": workspace.resolve(),
        "artifact_root": (reviews / "r1").resolve(),
        "input": {},
    }
    values.update(changes)
    return EngineRequest(**values)  # type: ignore[arg-type]


def make_fake_node(
    tmp_path: Path,
    *,
    stdout: str = "",
    stderr: str = "",
    exit_code: int = 0,
    delay: float = 0,
    env_file: Path | None = None,
    argv_file: Path | None = None,
) -> Path:
    path = tmp_path / f"fake-node-{time.monotonic_ns()}"
    source = f"""#!{sys.executable}
import json
import os
import sys
import time

sys.stdin.buffer.read()
if {str(env_file) if env_file else None!r}:
    with open({str(env_file) if env_file else None!r}, "w", encoding="utf-8") as handle:
        json.dump(dict(os.environ), handle)
if {str(argv_file) if argv_file else None!r}:
    with open({str(argv_file) if argv_file else None!r}, "w", encoding="utf-8") as handle:
        json.dump({{"argv": sys.argv, "cwd": os.getcwd()}}, handle)
sys.stdout.write({stdout!r})
sys.stdout.flush()
sys.stderr.write({stderr!r})
sys.stderr.flush()
time.sleep({delay!r})
raise SystemExit({exit_code})
"""
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.fixture(autouse=True)
def isolated_hermes_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.delenv("HADES_HOME", raising=False)


def install_fake_node(monkeypatch: pytest.MonkeyPatch, node: Path) -> list[str]:
    calls: list[str] = []

    def resolve(name: str) -> str:
        calls.append(name)
        return str(node)

    monkeypatch.setattr(bridge, "find_node_executable", resolve)
    return calls


def test_request_to_wire_uses_camel_case_and_canonical_paths(tmp_path: Path) -> None:
    req = request(tmp_path, input={"z": 1})

    assert req.to_wire() == {
        "protocolVersion": 1,
        "requestId": "r1",
        "command": "capture-target",
        "workspace": str(req.workspace),
        "artifactRoot": str(req.artifact_root),
        "input": {"z": 1},
    }


def test_request_round_trips_through_exact_proxy_wire_shape(tmp_path: Path) -> None:
    req = request(tmp_path, input={"kind": "local"})

    assert EngineRequest.from_wire(req.to_wire()) == req
    with pytest.raises(EngineProtocolError, match="invalid request field"):
        EngineRequest.from_wire({**req.to_wire(), "authenticatedReviewerRecords": []})


@pytest.mark.parametrize("field", ["workspace", "artifact_root"])
def test_request_rejects_non_absolute_paths(tmp_path: Path, field: str) -> None:
    req = request(tmp_path, **{field: Path("relative")})

    with pytest.raises(EngineProtocolError, match="absolute"):
        req.to_wire()


def test_request_rejects_artifacts_outside_reviews_root(tmp_path: Path) -> None:
    req = request(tmp_path, artifact_root=(tmp_path / "outside").resolve())

    with pytest.raises(EngineProtocolError, match="reviews"):
        req.to_wire()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("protocolVersion", True),
        ("requestId", 12),
        ("status", 1),
        ("output", []),
        ("diagnostics", {}),
    ],
)
def test_response_rejects_wrong_field_types(field: str, value: object) -> None:
    wire = dict(PASSED_RESPONSE)
    wire[field] = value

    with pytest.raises(EngineProtocolError):
        EngineResponse.from_wire(wire, expected_request_id="r1")


def test_response_rejects_unknown_status_and_request_id() -> None:
    wire = dict(PASSED_RESPONSE, status="unknown")
    with pytest.raises(EngineProtocolError, match="status"):
        EngineResponse.from_wire(wire, expected_request_id="r1")

    with pytest.raises(EngineProtocolError, match="requestId"):
        EngineResponse.from_wire(PASSED_RESPONSE, expected_request_id="other")


def test_response_rejects_more_than_200_diagnostics() -> None:
    wire = dict(
        PASSED_RESPONSE,
        diagnostics=[{"code": "c", "message": "m"}] * 201,
    )

    with pytest.raises(EngineProtocolError, match="200"):
        EngineResponse.from_wire(wire, expected_request_id="r1")


def test_response_is_immutable_and_typed() -> None:
    response = EngineResponse.from_wire(
        {
            **PASSED_RESPONSE,
            "diagnostics": [{"code": "ok", "message": "done"}],
        },
        expected_request_id="r1",
    )

    assert response.diagnostics == (EngineDiagnostic(code="ok", message="done"),)
    with pytest.raises((AttributeError, TypeError)):
        response.status = "failed"  # type: ignore[misc]
    assert (
        EngineResponse.from_wire(response.to_wire(), expected_request_id="r1")
        == response
    )


def test_bridge_uses_managed_node_and_exactly_one_json_document(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    argv_file = tmp_path / "argv.json"
    node = make_fake_node(
        tmp_path,
        stdout=json.dumps(PASSED_RESPONSE) + "\n",
        argv_file=argv_file,
    )
    calls = install_fake_node(monkeypatch, node)
    bundle = tmp_path / "engine.mjs"

    response = EngineeringReviewBridge(bundle=bundle).invoke(
        request(tmp_path), timeout=2
    )

    assert response.status == "passed"
    assert calls == ["node"]
    invocation = json.loads(argv_file.read_text(encoding="utf-8"))
    assert invocation == {
        "argv": [str(node), str(bundle)],
        "cwd": str((tmp_path / "workspace").resolve()),
    }


def test_bridge_rejects_extra_stdout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    node = make_fake_node(tmp_path, stdout="noise\n{}\n")
    install_fake_node(monkeypatch, node)

    with pytest.raises(EngineProtocolError, match="exactly one JSON document"):
        EngineeringReviewBridge(bundle=tmp_path / "engine.mjs").invoke(
            request(tmp_path), timeout=2
        )


def test_bridge_rejects_malformed_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    node = make_fake_node(tmp_path, stdout="{not-json}\n")
    install_fake_node(monkeypatch, node)

    with pytest.raises(EngineProtocolError, match="exactly one JSON document"):
        EngineeringReviewBridge(bundle=tmp_path / "engine.mjs").invoke(
            request(tmp_path), timeout=2
        )


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"requestId": "wrong"}, "requestId"),
        ({"protocolVersion": 2}, "protocolVersion"),
    ],
)
def test_bridge_rejects_mismatched_response_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    change: dict[str, object],
    message: str,
) -> None:
    node = make_fake_node(
        tmp_path, stdout=json.dumps({**PASSED_RESPONSE, **change}) + "\n"
    )
    install_fake_node(monkeypatch, node)

    with pytest.raises(EngineProtocolError, match=message):
        EngineeringReviewBridge(bundle=tmp_path / "engine.mjs").invoke(
            request(tmp_path), timeout=2
        )


def test_bridge_rejects_nonzero_exit_even_with_passed_response(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    node = make_fake_node(
        tmp_path,
        stdout=json.dumps(PASSED_RESPONSE) + "\n",
        stderr="engine failed",
        exit_code=3,
    )
    install_fake_node(monkeypatch, node)

    with pytest.raises(EngineProcessError, match="exit code 3"):
        EngineeringReviewBridge(bundle=tmp_path / "engine.mjs").invoke(
            request(tmp_path), timeout=2
        )


def test_bridge_times_out_and_terminates_process_group(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    node = make_fake_node(tmp_path, delay=10)
    install_fake_node(monkeypatch, node)

    started = time.monotonic()
    with pytest.raises(EngineTimeoutError):
        EngineeringReviewBridge(bundle=tmp_path / "engine.mjs").invoke(
            request(tmp_path), timeout=0.1
        )
    assert time.monotonic() - started < 3


def test_bridge_honors_already_set_cancellation_without_launching(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cancel_event = threading.Event()
    cancel_event.set()
    monkeypatch.setattr(
        bridge,
        "find_node_executable",
        lambda _name: pytest.fail("Node must not be resolved after cancellation"),
    )

    with pytest.raises(EngineCancelledError):
        EngineeringReviewBridge(bundle=tmp_path / "engine.mjs").invoke(
            request(tmp_path), timeout=2, cancel_event=cancel_event
        )


def test_bridge_honors_midflight_cancellation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    node = make_fake_node(tmp_path, delay=10)
    install_fake_node(monkeypatch, node)
    cancel_event = threading.Event()
    timer = threading.Timer(0.1, cancel_event.set)
    timer.start()
    try:
        with pytest.raises(EngineCancelledError):
            EngineeringReviewBridge(bundle=tmp_path / "engine.mjs").invoke(
                request(tmp_path), timeout=2, cancel_event=cancel_event
            )
    finally:
        timer.cancel()


@pytest.mark.parametrize(
    ("stream", "expected"),
    [("stdout", "stdout"), ("stderr", "stderr")],
)
def test_bridge_stops_process_when_output_exceeds_cap(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    stream: str,
    expected: str,
) -> None:
    kwargs = {stream: "x" * 8192, "delay": 10}
    node = make_fake_node(tmp_path, **kwargs)  # type: ignore[arg-type]
    install_fake_node(monkeypatch, node)

    with pytest.raises(EngineOutputLimitError, match=expected):
        EngineeringReviewBridge(
            bundle=tmp_path / "engine.mjs",
            stdout_limit=1024,
            stderr_limit=1024,
        ).invoke(request(tmp_path), timeout=2)


def test_bridge_scrubs_credentials_and_runtime_injection_variables(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_file = tmp_path / "env.json"
    node = make_fake_node(
        tmp_path,
        stdout=json.dumps(PASSED_RESPONSE) + "\n",
        env_file=env_file,
    )
    install_fake_node(monkeypatch, node)
    monkeypatch.setenv("OPENAI_API_KEY", "provider-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "token-secret")
    monkeypatch.setenv("NODE_OPTIONS", "--require=/tmp/evil.js")
    monkeypatch.setenv("NODE_PATH", "/tmp/modules")
    monkeypatch.setenv("PYTHONPATH", "/tmp/python")
    monkeypatch.setenv("GIT_ASKPASS", "/tmp/askpass")
    monkeypatch.setenv("HTTPS_PROXY", "http://user:pass@example.test")

    EngineeringReviewBridge(bundle=tmp_path / "engine.mjs").invoke(
        request(tmp_path), timeout=2
    )

    child_env = json.loads(env_file.read_text(encoding="utf-8"))
    assert "PATH" in child_env
    assert child_env["HERMES_ENGINE_PYTHON"] == sys.executable
    assert (
        Path(child_env["HERMES_ENGINE_PYTHON_ROOT"])
        == Path(__file__).resolve().parents[3]
    )
    for name in (
        "OPENAI_API_KEY",
        "GITHUB_TOKEN",
        "NODE_OPTIONS",
        "NODE_PATH",
        "PYTHONPATH",
        "GIT_ASKPASS",
        "HTTPS_PROXY",
    ):
        assert name not in child_env


def test_bundle_path_targets_packaged_engine() -> None:
    assert bundle_path().name == "hermes-engineering.mjs"
    assert bundle_path().parent.name == "engineering_dist"
    assert bundle_path().is_file()


def test_distribution_configuration_includes_engine_bundle() -> None:
    root = Path(__file__).resolve().parents[3]
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    manifest = (root / "MANIFEST.in").read_text(encoding="utf-8")

    assert '"engineering_dist/*.mjs"' in pyproject
    assert '"engineering_dist/NOTICE.qwen-code"' in pyproject
    assert '"engineering_dist/UPSTREAM.qwen-code.json"' in pyproject
    assert (
        "recursive-include hermes_cli/engineering_dist "
        "*.mjs NOTICE.qwen-code UPSTREAM.qwen-code.json" in manifest
    )


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group assertion")
@pytest.mark.live_system_guard_bypass
def test_timeout_kills_descendant_process_group(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    child_pid_file = tmp_path / "child.pid"
    node = tmp_path / "node-with-child"
    node.write_text(
        f"""#!{sys.executable}
import os
import subprocess
import sys
import time

child = subprocess.Popen([
    sys.executable,
    "-c",
    "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)",
])
with open({str(child_pid_file)!r}, "w", encoding="utf-8") as handle:
    handle.write(str(child.pid))
sys.stdin.buffer.read()
while True:
    time.sleep(1)
""",
        encoding="utf-8",
    )
    node.chmod(0o755)
    install_fake_node(monkeypatch, node)

    with pytest.raises(EngineTimeoutError):
        EngineeringReviewBridge(
            bundle=tmp_path / "engine.mjs", termination_grace=0.05
        ).invoke(request(tmp_path), timeout=0.5)

    child_pid = int(child_pid_file.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        stat = Path(f"/proc/{child_pid}/stat")
        if stat.exists() and stat.read_text(encoding="utf-8").split()[2] == "Z":
            break
        time.sleep(0.02)
    else:
        os.kill(child_pid, signal.SIGKILL)
        pytest.fail("descendant process survived bridge timeout")


class FakeWindowsJobApi:
    def __init__(self, *, assignment_error: OSError | None = None) -> None:
        self.assignment_error = assignment_error
        self.events: list[tuple[str, object]] = []

    def create_kill_on_close_job(self) -> object:
        handle = object()
        self.events.append(("create", handle))
        return handle

    def assign_process(self, job_handle: object, process_handle: object) -> None:
        self.events.append(("assign", (job_handle, process_handle)))
        if self.assignment_error is not None:
            raise self.assignment_error

    def close_handle(self, job_handle: object) -> None:
        self.events.append(("close", job_handle))


def test_ctypes_job_api_configures_kill_on_close_limit() -> None:
    observed: dict[str, object] = {}

    class FakeKernel32:
        @staticmethod
        def CreateJobObjectW(_security: object, _name: object) -> int:
            return 123

        @staticmethod
        def SetInformationJobObject(
            handle: object,
            information_class: int,
            information_pointer: object,
            size: int,
        ) -> int:
            information = ctypes.cast(
                information_pointer,
                ctypes.POINTER(bridge._JobObjectExtendedLimitInformation),
            ).contents
            observed.update(
                handle=handle,
                information_class=information_class,
                limit_flags=information.BasicLimitInformation.LimitFlags,
                size=size,
            )
            return 1

        @staticmethod
        def CloseHandle(_handle: object) -> int:
            return 1

    api = bridge._CtypesWindowsJobApi.__new__(bridge._CtypesWindowsJobApi)
    api._kernel32 = FakeKernel32()

    assert api.create_kill_on_close_job() == 123
    assert observed == {
        "handle": 123,
        "information_class": 9,
        "limit_flags": 0x00002000,
        "size": ctypes.sizeof(bridge._JobObjectExtendedLimitInformation),
    }


def test_windows_job_object_is_created_assigned_and_closed() -> None:
    api = FakeWindowsJobApi()
    process = SimpleNamespace(_handle=1234)

    job = bridge._WindowsJobObject.create(process, api=api)
    job.close()
    job.close()

    handle = api.events[0][1]
    assert api.events == [
        ("create", handle),
        ("assign", (handle, 1234)),
        ("close", handle),
    ]


def test_windows_job_assignment_failure_closes_handle_and_fails_closed() -> None:
    api = FakeWindowsJobApi(assignment_error=OSError("assignment denied"))
    process = SimpleNamespace(_handle=1234)

    with pytest.raises(EngineProcessError, match="Job Object containment"):
        bridge._WindowsJobObject.create(process, api=api)

    assert [event for event, _value in api.events] == ["create", "assign", "close"]


def test_windows_teardown_closes_job_after_direct_parent_exited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = FakeWindowsJobApi()
    process = SimpleNamespace(_handle=1234)
    job = bridge._WindowsJobObject.create(process, api=api)

    class ExitedProcess:
        pid = 99

        @staticmethod
        def poll() -> int:
            return 0

        @staticmethod
        def wait(timeout: float) -> int:
            return 0

        @staticmethod
        def send_signal(sig: int) -> None:
            pytest.fail(f"exited parent must not be signalled: {sig}")

        @staticmethod
        def kill() -> None:
            pytest.fail("direct-process fallback must not replace Job Object cleanup")

    taskkill_calls: list[list[str]] = []
    monkeypatch.setattr(
        bridge.subprocess,
        "run",
        lambda command, **_kwargs: taskkill_calls.append(command),
    )

    bridge._terminate_windows_process_tree(ExitedProcess(), job=job, grace=0)

    assert [event for event, _value in api.events] == ["create", "assign", "close"]
    assert taskkill_calls == []


def test_bridge_retains_windows_job_until_normal_teardown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    node = make_fake_node(tmp_path, stdout=json.dumps(PASSED_RESPONSE) + "\n")
    install_fake_node(monkeypatch, node)
    api = FakeWindowsJobApi()
    attached_jobs: list[object] = []

    def attach(process: object) -> object:
        job = bridge._WindowsJobObject.create(SimpleNamespace(_handle=5678), api=api)
        attached_jobs.append(job)
        return job

    monkeypatch.setattr(bridge, "_create_windows_job", attach)

    response = EngineeringReviewBridge(bundle=tmp_path / "engine.mjs").invoke(
        request(tmp_path), timeout=2
    )

    assert response.status == "passed"
    assert len(attached_jobs) == 1
    assert [event for event, _value in api.events] == ["create", "assign", "close"]


def test_bridge_closes_windows_job_when_writer_setup_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    api = FakeWindowsJobApi()
    job = bridge._WindowsJobObject.create(SimpleNamespace(_handle=5678), api=api)
    fake_process = SimpleNamespace(stdin=object(), pid=99, poll=lambda: None)
    monkeypatch.setattr(bridge, "find_node_executable", lambda _name: "node")
    monkeypatch.setattr(
        bridge.subprocess, "Popen", lambda *_args, **_kwargs: fake_process
    )
    monkeypatch.setattr(bridge, "_create_windows_job", lambda _process: job)
    monkeypatch.setattr(
        bridge.threading.Thread,
        "start",
        lambda _thread: (_ for _ in ()).throw(RuntimeError("thread unavailable")),
    )
    terminated: list[object] = []

    def terminate(process: object, **_kwargs: object) -> None:
        terminated.append(process)
        job.close()

    monkeypatch.setattr(bridge, "_terminate_process_group", terminate)

    with pytest.raises(RuntimeError, match="thread unavailable"):
        EngineeringReviewBridge(bundle=tmp_path / "engine.mjs").invoke(
            request(tmp_path), timeout=2
        )

    assert terminated == [fake_process]
    assert [event for event, _value in api.events] == ["create", "assign", "close"]


@pytest.mark.skipif(os.name != "nt", reason="native Windows Job Object test")
def test_native_windows_job_close_kills_descendant(tmp_path: Path) -> None:
    child_pid_file = tmp_path / "windows-child.pid"
    parent_script = """
import subprocess
import sys
import time

sys.stdin.readline()
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    handle.write(str(child.pid))
while True:
    time.sleep(1)
"""
    parent = subprocess.Popen(
        [sys.executable, "-c", parent_script, str(child_pid_file)],
        stdin=subprocess.PIPE,
        creationflags=bridge.windows_process_group_flags(),
        text=True,
    )
    job = bridge._WindowsJobObject.create(parent)
    try:
        assert parent.stdin is not None
        parent.stdin.write("spawn\n")
        parent.stdin.flush()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not child_pid_file.exists():
            time.sleep(0.02)
        assert child_pid_file.is_file()
        child_pid = int(child_pid_file.read_text(encoding="utf-8"))

        job.close()
        parent.wait(timeout=5)

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        synchronize = 0x00100000
        kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel32.WaitForSingleObject.restype = ctypes.c_ulong
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        handle = kernel32.OpenProcess(synchronize, False, child_pid)
        if handle:
            try:
                assert kernel32.WaitForSingleObject(handle, 5000) == 0
            finally:
                kernel32.CloseHandle(handle)
    finally:
        job.close()
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=5)
