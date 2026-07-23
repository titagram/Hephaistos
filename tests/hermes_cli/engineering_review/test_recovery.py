from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from hermes_cli.engineering_review import recovery
from hermes_cli.engineering_review.runs import ReviewRun, ReviewRunError


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(
        ["git", "config", "user.email", "review@example.invalid"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Review Test"],
        cwd=repository,
        check=True,
    )
    (repository / "source.py").write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "source.py"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repository, check=True)
    return repository


def _failed_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    target: str = "HEAD",
) -> ReviewRun:
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HERMES_HOME", str(home))
    run = ReviewRun.create(
        _repository(tmp_path),
        target=target,
        effort="low",
        session_id="session-1",
    )
    return run.mark_cleanup_failed()


def test_public_recovery_removes_only_deterministic_registered_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _failed_run(tmp_path, monkeypatch)
    suffix = hashlib.sha256(
        f"{run.workspace}\0{run.run_id}".encode()
    ).hexdigest()[:16]
    worktree = run.workspace.parent / f".hermes-review-{suffix}"
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(worktree), "HEAD"],
        cwd=run.workspace,
        check=True,
        capture_output=True,
    )

    result = recovery.recover_review_run(run.run_id)

    assert result["status"] == "complete"
    assert str(worktree) in result["removed"]
    assert not worktree.exists()
    assert ReviewRun.load(run.run_id, run.session_id).status == "complete"


def test_public_recovery_refuses_symlink_at_deterministic_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _failed_run(tmp_path, monkeypatch)
    suffix = hashlib.sha256(
        f"{run.workspace}\0{run.run_id}".encode()
    ).hexdigest()[:16]
    worktree = run.workspace.parent / f".hermes-review-{suffix}"
    external = tmp_path / "external"
    external.mkdir()
    marker = external / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    worktree.symlink_to(external, target_is_directory=True)

    with pytest.raises(ReviewRunError, match="unsafe"):
        recovery.recover_review_run(run.run_id)

    assert marker.read_text("utf-8") == "keep"
    assert worktree.is_symlink()
    assert ReviewRun.load(run.run_id, run.session_id).status == "cleanup_failed"


def test_public_recovery_verifies_recorded_container_labels_before_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _failed_run(tmp_path, monkeypatch, target="local")
    container_id = "c" * 64
    run.atomic_artifact(
        "sandbox-recovery.json",
        json.dumps({
            "schemaVersion": 1,
            "runId": run.run_id,
            "backend": "docker",
            "containerId": container_id,
            "containerName": "hermes-review",
            "taskId": f"review-{run.run_id}",
        }).encode(),
    )
    calls: list[list[str]] = []

    def fake_run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess:
        calls.append(args)
        if args[1] == "inspect" and "--format" in args:
            return subprocess.CompletedProcess(
                args,
                0,
                stdout=(
                    f"{container_id}\n/hermes-review\n"
                    + json.dumps({
                        "hermes-agent": "1",
                        "hermes-task-id": f"review-{run.run_id}",
                    })
                    + "\n"
                ),
                stderr="",
            )
        if args[1:3] == ["rm", "-f"]:
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="")

    monkeypatch.setattr(recovery, "_repository_root", lambda _workspace: run.workspace)
    monkeypatch.setattr(
        recovery, "_remove_registered_worktree", lambda _repo, _tree: False
    )
    monkeypatch.setattr(recovery.shutil, "which", lambda *_args, **_kwargs: "/docker")
    monkeypatch.setattr(recovery.subprocess, "run", fake_run)

    result = recovery.recover_review_run(run.run_id)

    assert result["removed"] == [f"docker:{container_id}"]
    assert calls[0][-1] == container_id
    assert calls[1] == ["/docker", "rm", "-f", container_id]
    assert calls[2] == ["/docker", "inspect", container_id]


def test_public_recovery_accepts_finalized_clean_sandbox_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _failed_run(tmp_path, monkeypatch, target="local")
    run.atomic_artifact(
        "sandbox-recovery.json",
        json.dumps({
            "schemaVersion": 1,
            "runId": run.run_id,
            "backend": "docker",
            "taskId": f"review-{run.run_id}",
            "state": "clean",
        }).encode(),
    )
    monkeypatch.setattr(recovery, "_repository_root", lambda _workspace: run.workspace)
    monkeypatch.setattr(
        recovery, "_remove_registered_worktree", lambda _repo, _tree: False
    )
    monkeypatch.setattr(
        recovery,
        "_recover_container",
        lambda _identity: (_ for _ in ()).throw(
            AssertionError("clean sandbox has no container to recover")
        ),
    )

    result = recovery.recover_review_run(run.run_id)

    assert result == {"runId": run.run_id, "status": "complete", "removed": []}


def test_public_recovery_rejects_non_failed_or_unknown_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HERMES_HOME", str(home))
    run = ReviewRun.create(
        _repository(tmp_path),
        target="local",
        effort="low",
        session_id="session-1",
    ).mark_complete()

    with pytest.raises(ReviewRunError, match="cleanup_failed"):
        recovery.recover_review_run(run.run_id)
    with pytest.raises(ReviewRunError, match="not found uniquely"):
        recovery.recover_review_run("A" * 16)
