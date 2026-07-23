"""Authority-owned execution of untrusted review checks in Hermes terminals.

The Node engine remains the deterministic classifier, but for an untrusted PR
the authority runs that captured engine *inside* the configured sandbox.  The
model/proxy cannot select the backend, command, mounts, workspace, environment,
or network policy.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import stat
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from .execution_policy import ExecutionDecision
from .protocol import (
    MAX_TRANSPORT_BYTES,
    EngineDiagnostic,
    EngineProtocolError,
    EngineRequest,
    EngineResponse,
)
from .runs import ReviewRun, ReviewRunError


_CONTAINER_WORKSPACE = Path("/workspace")
_CONTAINER_REPOSITORY = _CONTAINER_WORKSPACE / "repository"
_CONTAINER_ARTIFACTS = Path("/hermes-artifacts")
_CONTAINER_RUNTIME = Path("/hermes-runtime")
_ENGINE_RELATIVE = Path("hermes_cli/engineering_review/hermes-engineering.mjs")
_PYTEST_PROBE_RELATIVE = Path("hermes_cli/engineering_review/pytest_probe.py")
_MAX_TIMEOUT_SECONDS = 660


class _TerminalEnvironment(Protocol):
    def execute(self, command: str, **kwargs: object) -> Mapping[str, object]: ...

    def cleanup(self) -> None: ...


EnvironmentFactory = Callable[..., _TerminalEnvironment]
ConfigLoader = Callable[[], Mapping[str, Any]]
SnapshotBuilder = Callable[["SandboxSource", Path, Mapping[str, str]], None]
_OBJECT_ID = re.compile(r"^[0-9a-fA-F]{40,64}$")
_CONTAINER_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


@dataclass(frozen=True, slots=True)
class SandboxSource:
    """Authority-registered immutable identity of a captured PR worktree."""

    worktree: Path
    base_ref: str
    head_ref: str


def sandbox_source_from_capture(
    run: ReviewRun, output: Mapping[str, object]
) -> SandboxSource:
    """Validate capture output against the deterministic registered path."""
    worktree = output.get("worktreePath")
    base_ref = output.get("baseRef")
    head_ref = output.get("headRef")
    if (
        not isinstance(worktree, str)
        or not isinstance(base_ref, str)
        or not isinstance(head_ref, str)
        or not _OBJECT_ID.fullmatch(base_ref)
        or not _OBJECT_ID.fullmatch(head_ref)
    ):
        raise ReviewRunError("sandbox capture has no immutable source identity")

    repo_root: Path | None = None
    for candidate in (run.workspace, *run.workspace.parents):
        marker = candidate / ".git"
        try:
            info = marker.lstat()
        except OSError:
            continue
        if stat.S_ISLNK(info.st_mode) or not (
            stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)
        ):
            raise ReviewRunError("registered repository metadata is unsafe")
        repo_root = candidate
        break
    if repo_root is None:
        raise ReviewRunError("registered workspace is not a Git repository")

    suffix = sha256(f"{repo_root}\0{run.run_id}".encode()).hexdigest()[:16]
    expected = (repo_root.parent / f".hermes-review-{suffix}").resolve(strict=False)
    resolved = Path(worktree).resolve(strict=False)
    if resolved != expected:
        raise ReviewRunError("captured worktree is not registered for this run")
    try:
        info = Path(worktree).lstat()
    except OSError as exc:
        raise ReviewRunError("captured worktree is unavailable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ReviewRunError("captured worktree is unsafe")
    return SandboxSource(
        worktree=resolved,
        base_ref=base_ref.lower(),
        head_ref=head_ref.lower(),
    )


def _default_config_loader() -> Mapping[str, Any]:
    from tools.terminal_tool import _get_env_config

    return _get_env_config()


def _container_environment(source: Mapping[str, str]) -> dict[str, str]:
    """Use container-native paths while retaining only non-secret locale data."""
    result = {
        "PATH": _CONTAINER_PATH,
        "HOME": "/root",
        "TMPDIR": "/tmp",
    }
    for name in ("LANG", "LANGUAGE"):
        value = source.get(name)
        if value is not None:
            result[name] = value
    result.update(
        (name, value) for name, value in source.items() if name.startswith("LC_")
    )
    return result


def _default_environment_factory(**kwargs: object) -> _TerminalEnvironment:
    from tools.terminal_tool import _create_environment

    return _create_environment(**kwargs)


def _default_snapshot_builder(
    source: SandboxSource,
    destination: Path,
    environment: Mapping[str, str],
) -> None:
    """Create a no-network Git snapshot without executing repository code."""
    env = {
        **dict(environment),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
    }
    try:
        subprocess.run(
            [
                "git",
                "-C",
                str(source.worktree),
                "cat-file",
                "-e",
                f"{source.base_ref}^{{commit}}",
            ],
            check=True,
            capture_output=True,
            timeout=30,
            env=env,
            stdin=subprocess.DEVNULL,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(source.worktree),
                "cat-file",
                "-e",
                f"{source.head_ref}^{{commit}}",
            ],
            check=True,
            capture_output=True,
            timeout=30,
            env=env,
            stdin=subprocess.DEVNULL,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(source.worktree),
                "bundle",
                "create",
                str(destination),
                "HEAD",
            ],
            check=True,
            capture_output=True,
            timeout=120,
            env=env,
            stdin=subprocess.DEVNULL,
        )
        destination.chmod(0o600)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReviewRunError("could not snapshot registered PR worktree") from exc


def _empty_output(command: str) -> dict[str, object]:
    if command == "build-test":
        return {"packageManager": None, "commands": []}
    return {
        "runner": None,
        "tests": [],
        "unreachable": [],
        "gated": [],
        "inert": [],
        "inconclusive": [],
        "availableRunners": [],
        "probeWorktreePath": None,
        "cleanupFailure": None,
    }


def _inconclusive(request: EngineRequest, code: str, message: str) -> EngineResponse:
    return EngineResponse(
        request_id=request.request_id,
        status="inconclusive",
        output=_empty_output(request.command),
        diagnostics=(EngineDiagnostic(code=code, message=message),),
    )


def _safe_write(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError(f"could not fully write {path.name}")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_trusted_probe() -> bytes:
    path = Path(__file__).with_name("pytest_probe.py")
    before = path.lstat()
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise ReviewRunError("pytest probe is not a trusted regular file")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        after = os.fstat(descriptor)
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise ReviewRunError("pytest probe identity changed while opening")
        data = b""
        while len(data) < after.st_size:
            chunk = os.read(descriptor, after.st_size - len(data))
            if not chunk:
                raise ReviewRunError("pytest probe could not be fully read")
            data += chunk
        return data
    finally:
        os.close(descriptor)


def _mount_source(path: Path) -> str:
    value = str(path)
    if "\0" in value or "\n" in value or "\r" in value or ":" in value:
        raise ReviewRunError("sandbox mount path is not representable safely")
    return value


def _remove_runtime(path: Path | None, identity: os.stat_result | None) -> None:
    if path is None or identity is None:
        return
    try:
        current = path.lstat()
        if (
            not stat.S_ISLNK(current.st_mode)
            and stat.S_ISDIR(current.st_mode)
            and (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino)
        ):
            shutil.rmtree(path)
    except OSError:
        pass


class SandboxTerminalExecutor:
    """Run the captured review engine through a confined Hermes environment."""

    def __init__(
        self,
        *,
        run: ReviewRun,
        decision: ExecutionDecision,
        config_loader: ConfigLoader = _default_config_loader,
        environment_factory: EnvironmentFactory = _default_environment_factory,
        snapshot_builder: SnapshotBuilder = _default_snapshot_builder,
    ) -> None:
        self._run = run
        self._decision = decision
        self._config_loader = config_loader
        self._environment_factory = environment_factory
        self._snapshot_builder = snapshot_builder
        self._container_env = _container_environment(decision.sanitized_env)
        self._cancelled = threading.Event()
        self._environment_lock = threading.RLock()
        self._active_environment: _TerminalEnvironment | None = None

    def cancel(self) -> None:
        """Best-effort termination of the currently active sandbox."""
        self._cancelled.set()
        with self._environment_lock:
            environment = self._active_environment
        if environment is not None:
            try:
                environment.cleanup()
            except Exception:
                pass

    def _translated_request(self, request: EngineRequest) -> dict[str, object]:
        if request.workspace.resolve(strict=False) != self._run.workspace:
            raise ReviewRunError("sandbox workspace does not match registered run")
        if request.artifact_root.resolve(strict=False) != self._run.root:
            raise ReviewRunError("sandbox artifact root does not match registered run")
        plan = request.input.get("planPath")
        expected_plan = (self._run.root / "plan.json").resolve(strict=False)
        if (
            not isinstance(plan, str)
            or Path(plan).resolve(strict=False) != expected_plan
        ):
            raise ReviewRunError("sandbox plan is outside the registered run")

        trusted_input = dict(request.input)
        trusted_input["planPath"] = str(_CONTAINER_ARTIFACTS / "plan.json")
        trusted_input["execution"] = {
            "mode": "local",
            "allowed": True,
            "sanitizedEnv": dict(self._container_env),
            # The classifier's child processes are local relative to the
            # sandbox, while the actual container is network-isolated.
            "network": False,
            "reason": "authority_sandbox_execution",
            "backend": None,
        }
        return {
            "protocolVersion": 1,
            "requestId": request.request_id,
            "command": request.command,
            "workspace": str(_CONTAINER_REPOSITORY),
            "artifactRoot": str(_CONTAINER_ARTIFACTS),
            "input": trusted_input,
        }

    def invoke(
        self,
        request: EngineRequest,
        *,
        timeout: float,
        source: SandboxSource | None,
    ) -> EngineResponse:
        if request.command not in {"build-test", "test-efficacy"}:
            return _inconclusive(
                request,
                "sandbox_command_invalid",
                "only deterministic build and test checks may use the review sandbox",
            )
        if self._cancelled.is_set():
            return _inconclusive(
                request,
                "sandbox_execution_cancelled",
                "sandbox execution was cancelled by the review authority",
            )
        if (
            not isinstance(timeout, (int, float))
            or isinstance(timeout, bool)
            or timeout <= 0
            or not math.isfinite(timeout)
        ):
            return _inconclusive(
                request,
                "sandbox_timeout_invalid",
                "sandbox execution requires a positive finite timeout",
            )
        if source is None:
            return _inconclusive(
                request,
                "sandbox_source_unavailable",
                "the authority has no registered captured PR worktree",
            )
        if (
            self._decision.mode != "sandbox"
            or not self._decision.allowed
            or self._decision.network
            or not self._decision.backend
        ):
            return _inconclusive(
                request,
                "sandbox_policy_invalid",
                "the authority did not grant a network-isolated sandbox",
            )

        try:
            config = dict(self._config_loader())
        except Exception:
            return _inconclusive(
                request,
                "sandbox_backend_unavailable",
                "the configured Hermes terminal environment could not be loaded",
            )
        configured = str(config.get("env_type", "")).strip().lower()
        if configured != self._decision.backend:
            return _inconclusive(
                request,
                "sandbox_backend_mismatch",
                "the authorized sandbox no longer matches the configured terminal backend",
            )
        # DockerEnvironment is currently the only Hermes backend exposing both
        # a confined review-worktree mount and an enforceable network=False
        # constructor contract. Other backends fail closed until they provide
        # those same two guarantees.
        if configured != "docker":
            return _inconclusive(
                request,
                "sandbox_backend_unsupported",
                "the configured terminal backend cannot yet prove review workspace and network confinement",
            )

        runtime: Path | None = None
        runtime_identity: os.stat_result | None = None
        try:
            translated = self._translated_request(request)
            executable, _ = self._run.authorize_engine_invocation(
                engine_bundle=None,
                require_evidence=False,
                workspace=request.workspace,
            )
            runtime = Path(
                tempfile.mkdtemp(prefix=".sandbox-runtime-", dir=self._run.root)
            )
            runtime_identity = runtime.lstat()
            module_dir = runtime / _ENGINE_RELATIVE.parent
            module_dir.mkdir(mode=0o700, parents=True)
            _safe_write(runtime / _ENGINE_RELATIVE, executable)
            _safe_write(runtime / _PYTEST_PROBE_RELATIVE, _read_trusted_probe())
            self._snapshot_builder(
                source,
                runtime / "review.bundle",
                self._decision.sanitized_env,
            )
            payload = json.dumps(
                translated,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        except (OSError, ReviewRunError, TypeError, ValueError):
            _remove_runtime(runtime, runtime_identity)
            return _inconclusive(
                request,
                "sandbox_workspace_invalid",
                "the registered review workspace could not be prepared safely",
            )

        environment: _TerminalEnvironment | None = None
        try:
            container_config = dict(config)
            container_config.update({
                "container_persistent": False,
                "docker_persist_across_processes": False,
                "docker_volumes": [
                    f"{_mount_source(self._run.root)}:/hermes-artifacts:ro",
                    f"{_mount_source(runtime)}:/hermes-runtime:ro",
                ],
                "docker_forward_env": [],
                "docker_env": dict(self._container_env),
                # User extra args can override network/mount/security flags.
                "docker_extra_args": [],
            })
            environment = self._environment_factory(
                env_type="docker",
                image=str(config.get("docker_image", "")),
                cwd=str(_CONTAINER_WORKSPACE),
                timeout=min(math.ceil(timeout), _MAX_TIMEOUT_SECONDS),
                ssh_config=None,
                container_config=container_config,
                local_config=None,
                task_id=f"review-{self._run.run_id}",
                host_cwd=None,
                network=False,
                mount_hermes_resources=False,
                allow_implicit_env_passthrough=False,
            )
            with self._environment_lock:
                if self._cancelled.is_set():
                    try:
                        environment.cleanup()
                    finally:
                        environment = None
                    raise RuntimeError("sandbox execution was cancelled")
                self._active_environment = environment
            result = environment.execute(
                (
                    "git clone --quiet /hermes-runtime/review.bundle "
                    "/workspace/repository && "
                    f"git -C /workspace/repository checkout --quiet {source.head_ref} "
                    "&& node /hermes-runtime/hermes_cli/engineering_review/"
                    "hermes-engineering.mjs"
                ),
                cwd=str(_CONTAINER_WORKSPACE),
                timeout=min(math.ceil(timeout), _MAX_TIMEOUT_SECONDS),
                stdin_data=payload,
                rewrite_compound_background=False,
            )
            output = result.get("output")
            returncode = result.get("returncode")
            if returncode != 0 or not isinstance(output, str):
                raise RuntimeError("sandbox engine did not complete successfully")
            encoded = output.encode("utf-8")
            if len(encoded) > MAX_TRANSPORT_BYTES:
                raise RuntimeError("sandbox engine output exceeded its limit")
            value = json.loads(output)
            return EngineResponse.from_wire(
                value, expected_request_id=request.request_id
            )
        except (
            EngineProtocolError,
            json.JSONDecodeError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
        ):
            return _inconclusive(
                request,
                "sandbox_execution_failed",
                "the configured Hermes terminal environment could not complete the check",
            )
        finally:
            if environment is not None:
                try:
                    environment.cleanup()
                except Exception:
                    pass
            with self._environment_lock:
                if self._active_environment is environment:
                    self._active_environment = None
            try:
                _remove_runtime(runtime, runtime_identity)
            except Exception:
                pass
