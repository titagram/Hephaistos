from __future__ import annotations

import gc
import hashlib
import json
import subprocess
import threading
import weakref
from pathlib import Path
from typing import Any

import pytest

import hermes_cli.engineering_review.terminal_execution as terminal_execution
from hermes_cli.engineering_review import recovery
from hermes_cli.engineering_review.execution_policy import decide_execution
from hermes_cli.engineering_review.protocol import EngineRequest
from hermes_cli.engineering_review.runs import ReviewRun
from hermes_cli.engineering_review.terminal_execution import (
    SandboxSource,
    SandboxTerminalExecutor,
    _default_snapshot_builder,
    sandbox_source_from_capture,
)


def _request(run: ReviewRun, *, plan_path: Path | None = None) -> EngineRequest:
    return EngineRequest(
        request_id="sandbox-request",
        command="build-test",
        workspace=run.workspace,
        artifact_root=run.root,
        input={
            "planPath": str(plan_path or (run.root / "plan.json")),
            "timeoutMs": 30_000,
            "execution": {
                "mode": "local",
                "allowed": True,
                "sanitizedEnv": {"OPENAI_API_KEY": "forged"},
                "network": True,
                "reason": "caller controlled",
                "backend": None,
            },
        },
    )


def _run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[ReviewRun, dict[str, Any]]:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir(mode=0o700)
    workspace.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    run = ReviewRun.create(
        workspace,
        target="https://github.com/o/r/pull/1",
        effort="low",
        session_id="session-1",
    )
    (run.root / "plan.json").write_text(
        '{"files":[],"buildTest":{"commands":[]}}',
        encoding="utf-8",
    )
    seen: dict[str, Any] = {}
    return run, seen


def _source(run: ReviewRun) -> SandboxSource:
    return SandboxSource(
        worktree=run.workspace,
        base_ref="1" * 40,
        head_ref="2" * 40,
    )


def _write_fake_bundle(
    _source: SandboxSource, destination: Path, _environment: object
) -> None:
    destination.write_bytes(b"fake git bundle")


def test_configured_docker_executes_captured_engine_through_environment_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, seen = _run(tmp_path, monkeypatch)
    decision = decide_execution(
        target_kind="pr",
        sandbox="docker",
        allow_local=False,
        environ={
            "PATH": "/usr/bin",
            "LANG": "C.UTF-8",
            "OPENAI_API_KEY": "secret",
        },
    )

    class FakeEnvironment:
        def execute(self, command: str, **kwargs: object) -> dict[str, object]:
            seen.setdefault("commands", []).append(command)
            if command == "node --version":
                return {"returncode": 0, "output": "v22.23.1\n"}
            if command.startswith("git clone "):
                return {"returncode": 0, "output": ""}
            seen["command"] = command
            seen["execute"] = kwargs
            request = json.loads(str(kwargs["stdin_data"]))
            seen["request"] = request
            return {
                "returncode": 0,
                "output": json.dumps({
                    "protocolVersion": 1,
                    "requestId": request["requestId"],
                    "status": "passed",
                    "output": {"packageManager": None, "commands": []},
                    "diagnostics": [],
                }),
            }

        def cleanup(self, *, force_remove: bool = False) -> None:
            seen["cleaned"] = True
            seen["force_remove"] = force_remove

        def wait_for_cleanup(self, timeout: float = 30.0) -> bool:
            seen["cleanup_timeout"] = timeout
            return True

        @property
        def cleanup_error(self) -> None:
            return None

        def recovery_identity(self) -> dict[str, str]:
            return {
                "containerId": "a" * 64,
                "containerName": "hermes-review",
                "taskId": f"review-{run.run_id}",
            }

    def factory(**kwargs: object) -> FakeEnvironment:
        seen["factory"] = kwargs
        return FakeEnvironment()

    response = SandboxTerminalExecutor(
        run=run,
        decision=decision,
        config_loader=lambda: {
            "env_type": "docker",
            "docker_image": "trusted-image",
            "container_cpu": 1,
            "container_memory": 512,
            "container_disk": 1024,
            "docker_volumes": ["/host:/host"],
            "docker_forward_env": ["OPENAI_API_KEY"],
            "docker_env": {"GITHUB_TOKEN": "secret"},
            "docker_extra_args": ["--network=host"],
        },
        environment_factory=factory,
        snapshot_builder=_write_fake_bundle,
    ).invoke(_request(run), timeout=40, source=_source(run))

    assert response.status == "passed"
    assert seen["commands"] == [
        "node --version",
        (
            "git clone --quiet /hermes-runtime/review.bundle "
            "/workspace/repository && git -C /workspace/repository checkout "
            "--quiet 2222222222222222222222222222222222222222"
        ),
        (
            "node /hermes-runtime/hermes_cli/engineering_review/"
            "hermes-engineering.mjs"
        ),
    ]
    execute = seen["execute"]
    assert execute["cwd"] == "/workspace/repository"
    assert execute["timeout"] == 40
    factory_args = seen["factory"]
    assert factory_args["env_type"] == "docker"
    assert factory_args["network"] is False
    assert factory_args["mount_hermes_resources"] is False
    assert factory_args["allow_implicit_env_passthrough"] is False
    assert factory_args["host_cwd"] is None
    container = factory_args["container_config"]
    assert container["docker_forward_env"] == []
    assert container["docker_extra_args"] == []
    assert container["docker_env"] == {
        "HOME": "/root",
        "LANG": "C.UTF-8",
        "PATH": ("/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"),
        "TMPDIR": "/tmp",
    }
    assert container["container_persistent"] is False
    assert container["docker_persist_across_processes"] is False
    assert all("secret" not in value for value in container["docker_volumes"])
    request = seen["request"]
    assert request["workspace"] == "/workspace/repository"
    assert request["artifactRoot"] == "/hermes-artifacts"
    assert request["input"]["planPath"] == "/hermes-artifacts/plan.json"
    assert request["input"]["execution"] == {
        "mode": "local",
        "allowed": True,
        "sanitizedEnv": {
            "HOME": "/root",
            "LANG": "C.UTF-8",
            "PATH": ("/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"),
            "TMPDIR": "/tmp",
        },
        "network": False,
        "reason": "authority_sandbox_execution",
        "backend": None,
    }
    assert seen["cleaned"] is True
    assert seen["force_remove"] is True
    assert seen["cleanup_timeout"] == 60


def test_executor_rejects_plan_outside_registered_run_without_creating_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, seen = _run(tmp_path, monkeypatch)
    decision = decide_execution(
        target_kind="pr",
        sandbox="docker",
        allow_local=False,
        environ={"PATH": "/usr/bin"},
    )

    response = SandboxTerminalExecutor(
        run=run,
        decision=decision,
        config_loader=lambda: {"env_type": "docker"},
        environment_factory=lambda **kwargs: seen.update(kwargs),
        snapshot_builder=_write_fake_bundle,
    ).invoke(
        _request(run, plan_path=tmp_path / "outside.json"),
        timeout=30,
        source=_source(run),
    )

    assert response.status == "inconclusive"
    assert response.diagnostics[0].code == "sandbox_workspace_invalid"
    assert seen == {}


@pytest.mark.parametrize("configured", ["local", "modal", "daytona", "singularity"])
def test_executor_fails_closed_for_mismatch_or_backend_without_safe_network_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    configured: str,
) -> None:
    run, seen = _run(tmp_path, monkeypatch)
    decision = decide_execution(
        target_kind="pr",
        sandbox=configured if configured != "local" else "docker",
        allow_local=False,
        environ={"PATH": "/usr/bin"},
    )

    response = SandboxTerminalExecutor(
        run=run,
        decision=decision,
        config_loader=lambda: {"env_type": configured},
        environment_factory=lambda **kwargs: seen.update(kwargs),
        snapshot_builder=_write_fake_bundle,
    ).invoke(_request(run), timeout=30, source=_source(run))

    assert response.status == "inconclusive"
    assert response.diagnostics[0].code in {
        "sandbox_backend_mismatch",
        "sandbox_backend_unsupported",
    }
    assert seen == {}


def test_backend_failure_is_inconclusive_and_environment_is_cleaned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, seen = _run(tmp_path, monkeypatch)
    decision = decide_execution(
        target_kind="pr",
        sandbox="docker",
        allow_local=False,
        environ={"PATH": "/usr/bin"},
    )

    class FailingEnvironment:
        def execute(self, *_args: object, **_kwargs: object) -> dict[str, object]:
            raise RuntimeError("backend unavailable")

        def cleanup(self, *, force_remove: bool = False) -> None:
            seen["cleaned"] = True

        def wait_for_cleanup(self, timeout: float = 30.0) -> bool:
            return True

        @property
        def cleanup_error(self) -> None:
            return None

        def recovery_identity(self) -> dict[str, str]:
            return {}

    response = SandboxTerminalExecutor(
        run=run,
        decision=decision,
        config_loader=lambda: {
            "env_type": "docker",
            "docker_image": "trusted-image",
        },
        environment_factory=lambda **_kwargs: FailingEnvironment(),
        snapshot_builder=_write_fake_bundle,
    ).invoke(_request(run), timeout=30, source=_source(run))

    assert response.status == "inconclusive"
    assert response.diagnostics[0].code == "sandbox_execution_failed"
    assert "backend unavailable" not in response.diagnostics[0].message
    assert seen["cleaned"] is True


def test_sandbox_requires_verified_node_22_before_repository_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, seen = _run(tmp_path, monkeypatch)
    decision = decide_execution(
        target_kind="pr",
        sandbox="docker",
        allow_local=False,
        environ={"PATH": "/usr/bin"},
    )

    class OldNodeEnvironment:
        cleanup_error = None

        def execute(self, command: str, **_kwargs: object) -> dict[str, object]:
            seen.setdefault("commands", []).append(command)
            return {"returncode": 0, "output": "v20.19.0\n"}

        def cleanup(self, *, force_remove: bool = False) -> None:
            seen["cleaned"] = force_remove

        def wait_for_cleanup(self, timeout: float = 30.0) -> bool:
            return True

        def recovery_identity(self) -> dict[str, str]:
            return {}

    response = SandboxTerminalExecutor(
        run=run,
        decision=decision,
        config_loader=lambda: {
            "env_type": "docker",
            "docker_image": "trusted-image",
        },
        environment_factory=lambda **_kwargs: OldNodeEnvironment(),
        snapshot_builder=_write_fake_bundle,
    ).invoke(_request(run), timeout=30, source=_source(run))

    assert response.status == "inconclusive"
    assert response.diagnostics[0].code == "sandbox_runtime_unavailable"
    assert seen["commands"] == ["node --version"]
    assert seen["cleaned"] is True


def test_sandbox_missing_project_dependencies_is_not_a_project_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, seen = _run(tmp_path, monkeypatch)
    (run.root / "plan.json").write_text(
        json.dumps({
            "files": [],
            "hermes": {
                "buildTest": {
                    "packageManager": "npm",
                    "commands": [
                        {
                            "phase": "test",
                            "executable": "npm",
                            "args": ["run", "test"],
                            "cwd": ".",
                        }
                    ],
                }
            },
        }),
        encoding="utf-8",
    )
    decision = decide_execution(
        target_kind="pr",
        sandbox="docker",
        allow_local=False,
        environ={"PATH": "/usr/bin"},
    )

    class MissingDependenciesEnvironment:
        cleanup_error = None

        def execute(self, command: str, **_kwargs: object) -> dict[str, object]:
            seen.setdefault("commands", []).append(command)
            if command == "node --version":
                return {"returncode": 0, "output": "v22.23.1\n"}
            if command.startswith("git clone "):
                return {"returncode": 0, "output": ""}
            return {"returncode": 1, "output": ""}

        def cleanup(self, *, force_remove: bool = False) -> None:
            pass

        def wait_for_cleanup(self, timeout: float = 30.0) -> bool:
            return True

        def recovery_identity(self) -> dict[str, str]:
            return {}

    response = SandboxTerminalExecutor(
        run=run,
        decision=decision,
        config_loader=lambda: {
            "env_type": "docker",
            "docker_image": "trusted-image",
        },
        environment_factory=lambda **_kwargs: MissingDependenciesEnvironment(),
        snapshot_builder=_write_fake_bundle,
    ).invoke(_request(run), timeout=30, source=_source(run))

    assert response.status == "inconclusive"
    assert response.diagnostics[0].code == "sandbox_dependency_unavailable"
    assert not any(
        command.startswith("node /hermes-runtime")
        for command in seen["commands"]
    )


def test_cleanup_failure_is_observed_and_records_public_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, _ = _run(tmp_path, monkeypatch)
    decision = decide_execution(
        target_kind="pr",
        sandbox="docker",
        allow_local=False,
        environ={"PATH": "/usr/bin"},
    )

    class CleanupFailureEnvironment:
        cleanup_error = "docker rm -f exited 1"

        def execute(self, command: str, **kwargs: object) -> dict[str, object]:
            if command == "node --version":
                return {"returncode": 0, "output": "v22.23.1\n"}
            if command.startswith("git clone "):
                return {"returncode": 0, "output": ""}
            request = json.loads(str(kwargs["stdin_data"]))
            return {
                "returncode": 0,
                "output": json.dumps({
                    "protocolVersion": 1,
                    "requestId": request["requestId"],
                    "status": "passed",
                    "output": {"packageManager": None, "commands": []},
                    "diagnostics": [],
                }),
            }

        def cleanup(self, *, force_remove: bool = False) -> None:
            assert force_remove is True

        def wait_for_cleanup(self, timeout: float = 30.0) -> bool:
            return True

        def recovery_identity(self) -> dict[str, str]:
            return {
                "containerId": "b" * 64,
                "containerName": "hermes-review",
                "taskId": f"review-{run.run_id}",
            }

    response = SandboxTerminalExecutor(
        run=run,
        decision=decision,
        config_loader=lambda: {
            "env_type": "docker",
            "docker_image": "trusted-image",
        },
        environment_factory=lambda **_kwargs: CleanupFailureEnvironment(),
        snapshot_builder=_write_fake_bundle,
    ).invoke(_request(run), timeout=30, source=_source(run))

    assert response.status == "inconclusive"
    assert response.diagnostics[0].code == "cleanup_failed"
    assert (
        f"hermes review cleanup --run {run.run_id}"
        in response.diagnostics[0].message
    )
    recovery = json.loads((run.root / "sandbox-recovery.json").read_text("utf-8"))
    assert recovery["containerId"] == "b" * 64
    assert recovery["taskId"] == f"review-{run.run_id}"


def test_cleanup_failure_blocks_later_sandbox_without_overwriting_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, _ = _run(tmp_path, monkeypatch)
    decision = decide_execution(
        target_kind="pr",
        sandbox="docker",
        allow_local=False,
        environ={"PATH": "/usr/bin"},
    )
    created: list[str] = []

    class CleanupFailureEnvironment:
        cleanup_error = "docker rm -f exited 1"

        def __init__(self, name: str, container_id: str) -> None:
            self.name = name
            self.container_id = container_id

        def execute(self, command: str, **kwargs: object) -> dict[str, object]:
            if command == "node --version":
                return {"returncode": 0, "output": "v22.23.1\n"}
            if command.startswith("git clone "):
                return {"returncode": 0, "output": ""}
            request = json.loads(str(kwargs["stdin_data"]))
            return {
                "returncode": 0,
                "output": json.dumps({
                    "protocolVersion": 1,
                    "requestId": request["requestId"],
                    "status": "passed",
                    "output": {"packageManager": None, "commands": []},
                    "diagnostics": [],
                }),
            }

        def cleanup(self, *, force_remove: bool = False) -> None:
            assert force_remove is True

        def wait_for_cleanup(self, timeout: float = 30.0) -> bool:
            return True

        def recovery_identity(self) -> dict[str, str]:
            return {
                "containerId": self.container_id,
                "containerName": f"hermes-review-{self.name}",
                "taskId": f"review-{run.run_id}",
            }

    environments = [
        CleanupFailureEnvironment("first", "a" * 64),
        CleanupFailureEnvironment("second", "b" * 64),
    ]

    def factory(**_kwargs: object) -> CleanupFailureEnvironment:
        environment = environments[len(created)]
        created.append(environment.name)
        return environment

    executor = SandboxTerminalExecutor(
        run=run,
        decision=decision,
        config_loader=lambda: {
            "env_type": "docker",
            "docker_image": "trusted-image",
        },
        environment_factory=factory,
        snapshot_builder=_write_fake_bundle,
    )

    first = executor.invoke(_request(run), timeout=30, source=_source(run))
    second = executor.invoke(_request(run), timeout=30, source=_source(run))

    assert first.status == "inconclusive"
    assert first.diagnostics[0].code == "cleanup_failed"
    assert second.status == "inconclusive"
    assert second.diagnostics[0].code == "cleanup_failed"
    assert f"hermes review cleanup --run {run.run_id}" in second.diagnostics[0].message
    assert created == ["first"]
    recovery = json.loads((run.root / "sandbox-recovery.json").read_text("utf-8"))
    assert recovery["containerId"] == "a" * 64
    assert recovery["containerName"] == "hermes-review-first"


def test_distinct_environments_cannot_share_teardown_result_when_ids_are_reused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, _ = _run(tmp_path, monkeypatch)
    decision = decide_execution(
        target_kind="pr",
        sandbox="docker",
        allow_local=False,
        environ={"PATH": "/usr/bin"},
    )
    calls: list[tuple[str, str]] = []

    class Environment:
        def __init__(self, name: str, cleanup_error: str | None) -> None:
            self.name = name
            self._cleanup_error = cleanup_error

        def execute(self, command: str, **kwargs: object) -> dict[str, object]:
            if command == "node --version":
                return {"returncode": 0, "output": "v22.23.1\n"}
            if command.startswith("git clone "):
                return {"returncode": 0, "output": ""}
            request = json.loads(str(kwargs["stdin_data"]))
            return {
                "returncode": 0,
                "output": json.dumps({
                    "protocolVersion": 1,
                    "requestId": request["requestId"],
                    "status": "passed",
                    "output": {"packageManager": None, "commands": []},
                    "diagnostics": [],
                }),
            }

        def cleanup(self, *, force_remove: bool = False) -> None:
            assert force_remove is True
            calls.append((self.name, "cleanup"))

        def wait_for_cleanup(self, timeout: float = 30.0) -> bool:
            assert timeout == 60
            calls.append((self.name, "wait"))
            return True

        @property
        def cleanup_error(self) -> str | None:
            return self._cleanup_error

        def recovery_identity(self) -> dict[str, str]:
            container_id = "a" * 64 if self.name == "first" else "b" * 64
            return {
                "containerId": container_id,
                "containerName": f"hermes-review-{self.name}",
                "taskId": f"review-{run.run_id}",
            }

    first_environment = Environment("first", None)
    second_environment = Environment("second", "docker rm -f exited 1")
    environments = [first_environment, second_environment]
    # Deterministically reproduce the old cache collision. Before the fix,
    # terminal_execution.id shadowed builtins.id and both environments shared
    # one teardown result. The implementation must not depend on integer IDs.
    monkeypatch.setattr(terminal_execution, "id", lambda _value: 1, raising=False)
    executor = SandboxTerminalExecutor(
        run=run,
        decision=decision,
        config_loader=lambda: {
            "env_type": "docker",
            "docker_image": "trusted-image",
        },
        environment_factory=lambda **_kwargs: environments.pop(0),
        snapshot_builder=_write_fake_bundle,
    )

    first = executor.invoke(_request(run), timeout=30, source=_source(run))
    second = executor.invoke(_request(run), timeout=30, source=_source(run))

    assert first.status == "passed"
    assert second.status == "inconclusive"
    assert second.diagnostics[0].code == "cleanup_failed"
    assert calls == [
        ("first", "cleanup"),
        ("first", "wait"),
        ("second", "cleanup"),
        ("second", "wait"),
    ]
    assert executor._teardown_environment(first_environment, {}) is None
    assert calls == [
        ("first", "cleanup"),
        ("first", "wait"),
        ("second", "cleanup"),
        ("second", "wait"),
    ]
    recovery = json.loads((run.root / "sandbox-recovery.json").read_text("utf-8"))
    assert recovery["containerId"] == "b" * 64
    assert recovery["containerName"] == "hermes-review-second"


def test_shutdown_releases_torn_down_environment_instances(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, _ = _run(tmp_path, monkeypatch)
    decision = decide_execution(
        target_kind="pr",
        sandbox="docker",
        allow_local=False,
        environ={"PATH": "/usr/bin"},
    )
    references: list[weakref.ReferenceType[object]] = []

    class Environment:
        cleanup_error = None

        def execute(self, command: str, **kwargs: object) -> dict[str, object]:
            if command == "node --version":
                return {"returncode": 0, "output": "v22.23.1\n"}
            if command.startswith("git clone "):
                return {"returncode": 0, "output": ""}
            request = json.loads(str(kwargs["stdin_data"]))
            return {
                "returncode": 0,
                "output": json.dumps({
                    "protocolVersion": 1,
                    "requestId": request["requestId"],
                    "status": "passed",
                    "output": {"packageManager": None, "commands": []},
                    "diagnostics": [],
                }),
            }

        def cleanup(self, *, force_remove: bool = False) -> None:
            assert force_remove is True

        def wait_for_cleanup(self, timeout: float = 30.0) -> bool:
            return True

        def recovery_identity(self) -> dict[str, str]:
            return {}

    def factory(**_kwargs: object) -> Environment:
        environment = Environment()
        references.append(weakref.ref(environment))
        return environment

    executor = SandboxTerminalExecutor(
        run=run,
        decision=decision,
        config_loader=lambda: {
            "env_type": "docker",
            "docker_image": "trusted-image",
        },
        environment_factory=factory,
        snapshot_builder=_write_fake_bundle,
    )

    assert executor.invoke(
        _request(run), timeout=30, source=_source(run)
    ).status == "passed"
    gc.collect()
    assert references[0]() is not None

    assert executor.shutdown() is None
    gc.collect()
    assert references[0]() is None


def test_shutdown_waits_for_inflight_teardown_before_releasing_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, _ = _run(tmp_path, monkeypatch)
    decision = decide_execution(
        target_kind="pr",
        sandbox="docker",
        allow_local=False,
        environ={"PATH": "/usr/bin"},
    )
    executing = threading.Event()
    release_execute = threading.Event()
    cleanup_recorded = threading.Event()
    shutdown_returned = threading.Event()
    references: list[weakref.ReferenceType[object]] = []
    cleanup_calls: list[bool] = []

    class Environment:
        cleanup_error = "forced cleanup failure"

        def execute(self, command: str, **_kwargs: object) -> dict[str, object]:
            assert command == "node --version"
            executing.set()
            assert release_execute.wait(timeout=5)
            return {"returncode": 1, "output": ""}

        def cleanup(self, *, force_remove: bool = False) -> None:
            cleanup_calls.append(force_remove)
            release_execute.set()

        def wait_for_cleanup(self, timeout: float = 30.0) -> bool:
            cleanup_recorded.set()
            return True

        def recovery_identity(self) -> dict[str, str]:
            return {
                "containerId": "c" * 64,
                "containerName": "hermes-review-race",
                "taskId": f"review-{run.run_id}",
            }

    def factory(**_kwargs: object) -> Environment:
        environment = Environment()
        references.append(weakref.ref(environment))
        return environment

    executor = SandboxTerminalExecutor(
        run=run,
        decision=decision,
        config_loader=lambda: {
            "env_type": "docker",
            "docker_image": "trusted-image",
        },
        environment_factory=factory,
        snapshot_builder=_write_fake_bundle,
    )
    responses: list[object] = []
    invoke_thread = threading.Thread(
        target=lambda: responses.append(
            executor.invoke(_request(run), timeout=30, source=_source(run))
        )
    )
    invoke_thread.start()
    assert executing.wait(timeout=5)

    def shutdown() -> None:
        executor.shutdown()
        shutdown_returned.set()

    shutdown_thread = threading.Thread(target=shutdown)
    shutdown_thread.start()
    assert cleanup_recorded.wait(timeout=5)
    assert not shutdown_returned.is_set()

    invoke_thread.join(timeout=5)
    shutdown_thread.join(timeout=5)
    assert not invoke_thread.is_alive()
    assert not shutdown_thread.is_alive()
    assert shutdown_returned.is_set()
    assert cleanup_calls == [True]
    assert getattr(responses[0], "diagnostics")[0].code == "cleanup_failed"
    recovery = json.loads((run.root / "sandbox-recovery.json").read_text("utf-8"))
    assert recovery["containerId"] == "c" * 64
    gc.collect()
    assert references[0]() is None


def test_shutdown_during_factory_records_identity_for_public_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, _ = _run(tmp_path, monkeypatch)
    decision = decide_execution(
        target_kind="pr",
        sandbox="docker",
        allow_local=False,
        environ={"PATH": "/usr/bin"},
    )
    factory_entered = threading.Event()
    release_factory = threading.Event()
    shutdown_returned = threading.Event()
    container_id = "d" * 64

    class Environment:
        cleanup_error = "forced cleanup failure"

        def execute(self, _command: str, **_kwargs: object) -> dict[str, object]:
            raise AssertionError("cancelled sandbox must not execute commands")

        def cleanup(self, *, force_remove: bool = False) -> None:
            assert force_remove is True

        def wait_for_cleanup(self, timeout: float = 30.0) -> bool:
            assert timeout == 60
            return True

        def recovery_identity(self) -> dict[str, str]:
            return {
                "containerId": container_id,
                "containerName": "hermes-review-factory-race",
                "taskId": f"review-{run.run_id}",
            }

    def factory(**_kwargs: object) -> Environment:
        factory_entered.set()
        assert release_factory.wait(timeout=5)
        return Environment()

    executor = SandboxTerminalExecutor(
        run=run,
        decision=decision,
        config_loader=lambda: {
            "env_type": "docker",
            "docker_image": "trusted-image",
        },
        environment_factory=factory,
        snapshot_builder=_write_fake_bundle,
    )
    responses: list[object] = []
    invoke_thread = threading.Thread(
        target=lambda: responses.append(
            executor.invoke(_request(run), timeout=30, source=_source(run))
        )
    )
    invoke_thread.start()
    assert factory_entered.wait(timeout=5)

    def shutdown() -> None:
        executor.shutdown()
        shutdown_returned.set()

    shutdown_thread = threading.Thread(target=shutdown)
    shutdown_thread.start()
    assert executor._cancelled.wait(timeout=5)
    assert not shutdown_returned.is_set()
    release_factory.set()

    invoke_thread.join(timeout=5)
    shutdown_thread.join(timeout=5)
    assert not invoke_thread.is_alive()
    assert not shutdown_thread.is_alive()
    assert shutdown_returned.is_set()
    assert getattr(responses[0], "diagnostics")[0].code == "cleanup_failed"
    recorded = json.loads(
        (run.root / "sandbox-recovery.json").read_text("utf-8")
    )
    assert recorded == {
        "backend": "docker",
        "containerId": container_id,
        "containerName": "hermes-review-factory-race",
        "runId": run.run_id,
        "schemaVersion": 1,
        "taskId": f"review-{run.run_id}",
    }

    failed_run = run.mark_cleanup_failed()
    recovered: list[dict[str, str]] = []
    monkeypatch.setattr(
        recovery, "_repository_root", lambda _workspace: failed_run.workspace
    )
    monkeypatch.setattr(
        recovery, "_remove_registered_worktree", lambda _repo, _tree: False
    )

    def recover_container(identity: dict[str, str]) -> bool:
        recovered.append(identity)
        return True

    monkeypatch.setattr(recovery, "_recover_container", recover_container)

    result = recovery.recover_review_run(failed_run.run_id)

    assert recovered == [{
        "containerId": container_id,
        "containerName": "hermes-review-factory-race",
        "taskId": f"review-{run.run_id}",
    }]
    assert result["removed"] == [f"docker:{container_id}"]
    assert result["status"] == "complete"


def test_snapshot_failure_is_inconclusive_and_removes_runtime_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, seen = _run(tmp_path, monkeypatch)
    decision = decide_execution(
        target_kind="pr",
        sandbox="docker",
        allow_local=False,
        environ={"PATH": "/usr/bin"},
    )

    def fail_snapshot(
        _source: SandboxSource, _destination: Path, _environment: object
    ) -> None:
        raise OSError("snapshot failed")

    response = SandboxTerminalExecutor(
        run=run,
        decision=decision,
        config_loader=lambda: {
            "env_type": "docker",
            "docker_image": "trusted-image",
        },
        environment_factory=lambda **kwargs: seen.update(kwargs),
        snapshot_builder=fail_snapshot,
    ).invoke(_request(run), timeout=30, source=_source(run))

    assert response.status == "inconclusive"
    assert response.diagnostics[0].code == "sandbox_workspace_invalid"
    assert seen == {}
    assert not any(
        entry.name.startswith(".sandbox-runtime-") for entry in run.root.iterdir()
    )


def test_capture_source_accepts_only_the_deterministic_registered_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    repository = tmp_path / "repository"
    home.mkdir(mode=0o700)
    repository.mkdir()
    (repository / ".git").mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    run = ReviewRun.create(
        repository,
        target="https://github.com/o/r/pull/1",
        effort="low",
        session_id="session-1",
    )
    suffix = hashlib.sha256(f"{repository}\0{run.run_id}".encode()).hexdigest()[:16]
    worktree = tmp_path / f".hermes-review-{suffix}"
    worktree.mkdir()
    output = {
        "worktreePath": str(worktree),
        "baseRef": "1" * 40,
        "headRef": "2" * 40,
    }

    source = sandbox_source_from_capture(run, output)

    assert source.worktree == worktree
    with pytest.raises(ValueError, match="not registered"):
        sandbox_source_from_capture(
            run,
            {**output, "worktreePath": str(tmp_path / "attacker-worktree")},
        )


def test_snapshot_builder_creates_bundle_from_registered_head_without_hooks(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.name", "Test"],
        check=True,
    )
    (repository / "file.txt").write_text("review source\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "file.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "commit", "-q", "-m", "fixture"],
        check=True,
    )
    head = subprocess.check_output(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    destination = tmp_path / "review.bundle"

    _default_snapshot_builder(
        SandboxSource(worktree=repository, base_ref=head, head_ref=head),
        destination,
        {"PATH": "/usr/bin:/bin"},
    )

    subprocess.run(["git", "bundle", "verify", str(destination)], check=True)
    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", "-q", str(destination), str(clone)],
        check=True,
    )
    cloned_head = subprocess.check_output(
        ["git", "-C", str(clone), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    assert cloned_head == head
    assert destination.stat().st_mode & 0o777 == 0o600
