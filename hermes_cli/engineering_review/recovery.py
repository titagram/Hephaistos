"""Public, post-session recovery for deterministic review resources."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Mapping

from .execution_policy import canonical_capture_input
from .runs import ReviewRun, ReviewRunError


_CONTAINER_ID = re.compile(r"^[0-9a-f]{12,64}$")
_CONTAINER_NAME = re.compile(r"^hermes-[A-Za-z0-9_-]{1,64}$")
_GIT_TIMEOUT = 60


def _command_environment() -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
    }


def _git(cwd: Path, args: list[str], *, check: bool = True) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=_command_environment(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT,
        check=False,
    )
    if check and completed.returncode != 0:
        raise ReviewRunError("registered Git worktree cleanup failed")
    return completed.stdout.strip()


def _repository_root(workspace: Path) -> Path:
    raw = _git(workspace, ["rev-parse", "--show-toplevel"])
    root = Path(raw).resolve(strict=True)
    info = root.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ReviewRunError("registered repository root is unsafe")
    return root


def _remove_registered_worktree(repo_root: Path, worktree: Path) -> bool:
    """Remove only a deterministic Git worktree, never an arbitrary tree."""
    if not worktree.exists() and not worktree.is_symlink():
        _git(repo_root, ["worktree", "prune"])
        return False
    info = worktree.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ReviewRunError("registered recovery worktree is unsafe")
    if worktree.resolve(strict=True) != worktree.resolve(strict=False):
        raise ReviewRunError("registered recovery worktree is not canonical")
    git_entry = worktree / ".git"
    git_info = git_entry.lstat()
    if stat.S_ISLNK(git_info.st_mode) or not stat.S_ISREG(git_info.st_mode):
        raise ReviewRunError("registered recovery path is not a Git worktree")
    common = Path(
        _git(worktree, ["rev-parse", "--git-common-dir"])
    )
    if not common.is_absolute():
        common = worktree / common
    common = common.resolve(strict=True)
    registered_common = Path(
        _git(repo_root, ["rev-parse", "--git-common-dir"])
    )
    if not registered_common.is_absolute():
        registered_common = repo_root / registered_common
    if common != registered_common.resolve(strict=True):
        raise ReviewRunError("recovery worktree belongs to another repository")
    _git(
        repo_root.parent,
        [
            f"--git-dir={common}",
            "worktree",
            "remove",
            "--force",
            str(worktree),
        ],
    )
    _git(repo_root, ["worktree", "prune"])
    if worktree.exists() or worktree.is_symlink():
        raise ReviewRunError("registered recovery worktree remains present")
    listing = _git(repo_root, ["worktree", "list", "--porcelain"])
    if f"worktree {worktree}\n" in f"{listing}\n":
        raise ReviewRunError("registered recovery worktree lease remains")
    return True


def _capture_worktree(repo_root: Path, run_id: str) -> Path:
    suffix = hashlib.sha256(f"{repo_root}\0{run_id}".encode()).hexdigest()[:16]
    return repo_root.parent / f".hermes-review-{suffix}"


def _probe_worktree(workspace: Path, run_id: str) -> Path:
    canonical = workspace.resolve(strict=False)
    suffix = hashlib.sha256(f"{canonical}\0{run_id}".encode()).hexdigest()[:16]
    return canonical / f".hermes-efficacy-{suffix}"


def _load_sandbox_identity(run: ReviewRun) -> dict[str, str] | None:
    path = run.root / "sandbox-recovery.json"
    if not path.exists() and not path.is_symlink():
        return None
    try:
        value = json.loads(run.read_private_artifact(path.name))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ReviewRunError("sandbox recovery record is invalid") from exc
    if not isinstance(value, Mapping):
        raise ReviewRunError("sandbox recovery record must be an object")
    expected_task = f"review-{run.run_id}"
    state = value.get("state")
    if state in {"creating", "clean"}:
        if (
            set(value)
            != {"schemaVersion", "runId", "backend", "taskId", "state"}
            or value.get("schemaVersion") != 1
            or value.get("runId") != run.run_id
            or value.get("backend") != "docker"
            or value.get("taskId") != expected_task
        ):
            raise ReviewRunError("sandbox recovery state is invalid")
        if state == "creating":
            raise ReviewRunError("sandbox creation is still in flight")
        return None
    container_id = value.get("containerId")
    container_name = value.get("containerName")
    if (
        value.get("schemaVersion") != 1
        or value.get("runId") != run.run_id
        or value.get("backend") != "docker"
        or value.get("taskId") != expected_task
        or not isinstance(container_id, str)
        or not _CONTAINER_ID.fullmatch(container_id)
        or not isinstance(container_name, str)
        or not _CONTAINER_NAME.fullmatch(container_name)
    ):
        raise ReviewRunError("sandbox recovery identity is invalid")
    return {
        "containerId": container_id,
        "containerName": container_name,
        "taskId": expected_task,
    }


def _recover_container(identity: Mapping[str, str]) -> bool:
    docker = shutil.which("docker", path=os.environ.get("PATH"))
    if docker is None:
        raise ReviewRunError("Docker is unavailable for sandbox recovery")
    container_id = identity["containerId"]
    inspected = subprocess.run(
        [
            docker,
            "inspect",
            "--format",
            "{{.Id}}\n{{.Name}}\n{{json .Config.Labels}}",
            container_id,
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if inspected.returncode != 0:
        return False
    lines = inspected.stdout.splitlines()
    if len(lines) != 3:
        raise ReviewRunError("sandbox recovery inspection was invalid")
    actual_id, actual_name, labels_json = lines
    try:
        labels = json.loads(labels_json)
    except json.JSONDecodeError as exc:
        raise ReviewRunError("sandbox recovery labels were invalid") from exc
    if (
        actual_id != container_id
        or actual_name.removeprefix("/") != identity["containerName"]
        or not isinstance(labels, Mapping)
        or labels.get("hermes-agent") != "1"
        or labels.get("hermes-task-id") != identity["taskId"]
    ):
        raise ReviewRunError("Docker resource does not match the recorded review")
    removed = subprocess.run(
        [docker, "rm", "-f", container_id],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if removed.returncode != 0:
        raise ReviewRunError("recorded review container could not be removed")
    verify = subprocess.run(
        [docker, "inspect", container_id],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if verify.returncode == 0:
        raise ReviewRunError("recorded review container remains live")
    return True


def recover_review_run(run_id: str) -> dict[str, object]:
    """Recover one registered cleanup_failed run by ID and mark it complete."""
    run = ReviewRun.load_cleanup_failed(run_id)
    removed: list[str] = []
    # Read this first.  A factory may still be creating a container while the
    # closing authority has already made the run publicly recoverable.  In
    # that state neither Git worktrees nor run metadata may be finalized.
    sandbox = _load_sandbox_identity(run)
    target = canonical_capture_input(run.target)
    repo_root = _repository_root(run.workspace)
    if target["kind"] in {"range", "pr"}:
        capture = _capture_worktree(repo_root, run.run_id)
        if _remove_registered_worktree(repo_root, capture):
            removed.append(str(capture))
    probe = _probe_worktree(run.workspace, run.run_id)
    if _remove_registered_worktree(repo_root, probe):
        removed.append(str(probe))
    if sandbox is not None and _recover_container(sandbox):
        removed.append(f"docker:{sandbox['containerId']}")
    run.mark_recovered()
    return {"runId": run.run_id, "status": "complete", "removed": removed}
