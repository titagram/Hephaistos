"""Lifecycle and filesystem-boundary tests for engineering review runs."""

from __future__ import annotations

import json
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from hermes_cli.engineering_review.runs import (
    ReviewRun,
    ReviewRunError,
    prune_completed_runs,
)


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "profile-home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


def test_run_root_is_profile_local_private_and_atomic(fake_home: Path, tmp_path: Path) -> None:
    run = ReviewRun.create(tmp_path, target="local", effort="medium", session_id="s1")

    assert run.root.parent == fake_home / "reviews" / "s1"
    assert stat.S_IMODE(run.root.stat().st_mode) == 0o700

    run.atomic_artifact("plan.json", b"{}")

    artifact = run.root / "plan.json"
    assert artifact.read_bytes() == b"{}"
    assert stat.S_IMODE(artifact.stat().st_mode) == 0o600
    assert not list(run.root.glob(".plan.json.*.tmp"))


def test_load_rejects_wrong_session_and_symlinked_run_root(fake_home: Path, tmp_path: Path) -> None:
    run = ReviewRun.create(tmp_path, target="local", effort="low", session_id="s1")

    with pytest.raises(ReviewRunError):
        ReviewRun.load(run.run_id, session_id="other")

    run.root.rename(run.root.with_name("real-run"))
    run.root.symlink_to(run.root.with_name("real-run"), target_is_directory=True)

    with pytest.raises(ReviewRunError, match="symlink"):
        ReviewRun.load(run.run_id, session_id="s1")


def test_load_rejects_unknown_state_and_root_escape(fake_home: Path, tmp_path: Path) -> None:
    run = ReviewRun.create(tmp_path, target="local", effort="high", session_id="s1")
    metadata_path = run.root / "run.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["status"] = "unexpected"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    with pytest.raises(ReviewRunError, match="status"):
        ReviewRun.load(run.run_id, session_id="s1")

    with pytest.raises(ReviewRunError, match="run id"):
        ReviewRun.load("../outside", session_id="s1")


def test_mark_complete_persists_lifecycle_state(fake_home: Path, tmp_path: Path) -> None:
    run = ReviewRun.create(tmp_path, target="local", effort="medium", session_id="s1")

    complete = run.mark_complete()
    metadata = json.loads((run.root / "run.json").read_text(encoding="utf-8"))

    assert complete.status == "complete"
    assert metadata["status"] == "complete"
    assert metadata["completed_at"] is not None
    assert ReviewRun.load(run.run_id, session_id="s1").status == "complete"


def test_prune_keeps_30_completed_and_every_active(fake_home: Path, tmp_path: Path) -> None:
    completed = [
        ReviewRun.create(tmp_path, target="local", effort="medium", session_id="s1").mark_complete()
        for _ in range(32)
    ]
    active = [
        ReviewRun.create(tmp_path, target="local", effort="medium", session_id="s1")
        for _ in range(2)
    ]

    removed = prune_completed_runs(fake_home, keep=30)

    assert len(removed) == 2
    assert all(not run.root.exists() for run in completed[:2])
    assert all(run.root.exists() for run in active)


def test_zero_retention_never_deletes_active_runs(fake_home: Path, tmp_path: Path) -> None:
    completed = ReviewRun.create(tmp_path, target="local", effort="medium", session_id="s1").mark_complete()
    active = ReviewRun.create(tmp_path, target="local", effort="medium", session_id="s1")

    removed = prune_completed_runs(fake_home, keep=0)

    assert removed == [completed.root]
    assert not completed.root.exists()
    assert active.root.exists()


def test_concurrent_pruners_do_not_delete_active_runs(fake_home: Path, tmp_path: Path) -> None:
    completed = [
        ReviewRun.create(tmp_path, target="local", effort="medium", session_id="s1").mark_complete()
        for _ in range(2)
    ]
    active = ReviewRun.create(tmp_path, target="local", effort="medium", session_id="s1")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: prune_completed_runs(fake_home, keep=0), range(2)))

    assert sum(len(result) for result in results) == 2
    assert all(not run.root.exists() for run in completed)
    assert active.root.exists()


@pytest.mark.parametrize("value", [-1, "bad", None])
def test_invalid_review_retention_uses_default(fake_home: Path, value: object) -> None:
    from hermes_cli.config import load_config

    (fake_home / "config.yaml").write_text(
        "review:\n  retention_runs: " + json.dumps(value) + "\n",
        encoding="utf-8",
    )

    assert load_config()["review"]["retention_runs"] == 30
