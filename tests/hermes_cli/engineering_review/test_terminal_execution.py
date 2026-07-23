from __future__ import annotations

import json
import hashlib
import subprocess
from pathlib import Path
from typing import Any

import pytest

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

        def cleanup(self) -> None:
            seen["cleaned"] = True

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
    assert seen["command"] == (
        "git clone --quiet /hermes-runtime/review.bundle "
        "/workspace/repository && git -C /workspace/repository checkout "
        "--quiet 2222222222222222222222222222222222222222 && "
        "node /hermes-runtime/hermes_cli/engineering_review/"
        "hermes-engineering.mjs"
    )
    execute = seen["execute"]
    assert execute["cwd"] == "/workspace"
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

        def cleanup(self) -> None:
            seen["cleaned"] = True

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
