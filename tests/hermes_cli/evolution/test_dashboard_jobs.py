"""Behavior contracts for bounded, persistent Evolution dashboard jobs."""

from __future__ import annotations

import json
import os
import stat
import threading
import uuid
from pathlib import Path

import pytest

import hermes_cli.evolution.dashboard_jobs as jobs_module
from hermes_cli.evolution.dashboard_jobs import (
    MAX_JOB_RESULT_BYTES,
    EvolutionJobConflict,
    EvolutionJobManager,
    EvolutionJobStorageError,
    EvolutionJobValidationError,
)


def _workspace(tmp_path: Path) -> Path:
    """Create the minimal server-selected repository root fixture."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".git").mkdir()
    return workspace


def _manager(tmp_path: Path) -> EvolutionJobManager:
    return EvolutionJobManager(
        tmp_path / "organism",
        workspace_root=_workspace(tmp_path),
    )


def _wait(manager: EvolutionJobManager, job_id: str):
    result = manager.wait(job_id, timeout=3)
    assert result is not None, "job did not reach a terminal state"
    return result


def test_missing_job_reads_do_not_create_organism_or_job_directory(tmp_path):
    """Removing read-only probes must fail this no-initialization contract."""
    root = tmp_path / "organism"
    manager = EvolutionJobManager(root, workspace_root=_workspace(tmp_path))
    try:
        assert manager.get_job(str(uuid.uuid4())) is None
        assert manager.list_jobs() == []
        assert not root.exists()
    finally:
        manager.shutdown()


def test_non_submission_transitions_do_not_create_the_job_directory(tmp_path):
    """Creating storage from a failed progress/cancel request must fail this boundary."""
    root = tmp_path / "organism"
    manager = EvolutionJobManager(root, workspace_root=_workspace(tmp_path))
    try:
        with pytest.raises(EvolutionJobConflict, match="job_not_active"):
            manager.update_progress(str(uuid.uuid4()), 1)
        with pytest.raises(EvolutionJobConflict, match="job_not_cancellable"):
            manager.cancel_job(str(uuid.uuid4()))
        assert not root.exists()
    finally:
        manager.shutdown()


def test_submit_persists_uuid_record_and_private_permissions_atomically(tmp_path, monkeypatch):
    """Replacing durable submission with an in-memory job must fail this test."""
    manager = _manager(tmp_path)
    started = threading.Event()
    release = threading.Event()

    def blocked_build(*args, **kwargs):
        started.set()
        assert release.wait(3)
        return {
            "organism_contract": {
                "revision_id": "rev:job:123",
                "semantic_fingerprint": "a" * 64,
            },
            "build_result": "published",
        }

    monkeypatch.setattr(jobs_module, "build_organism_revision", blocked_build)
    try:
        job = manager.submit_rebuild()
        assert uuid.UUID(job.job_id).version == 4
        assert started.wait(3)

        record_path = tmp_path / "organism" / "evolution" / "dashboard-jobs" / f"{job.job_id}.json"
        assert record_path.is_file()
        assert stat.S_IMODE(record_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(record_path.parent.stat().st_mode) == 0o700
        persisted = json.loads(record_path.read_text(encoding="utf-8"))
        assert persisted["job_id"] == job.job_id
        assert persisted["kind"] == "organism_rebuild"

        before = record_path.stat()
        manager.update_progress(job.job_id, 44)
        after = record_path.stat()
        assert after.st_ino != before.st_ino
        assert manager.get_job(job.job_id).progress == 44  # type: ignore[union-attr]
    finally:
        release.set()
        manager.shutdown()


def test_fixed_jobs_allow_one_rebuild_and_one_scan_while_both_run(tmp_path, monkeypatch):
    """Allowing a second active job of either exclusive kind must fail here."""
    manager = _manager(tmp_path)
    build_started = threading.Event()
    scan_started = threading.Event()
    release = threading.Event()

    def blocked_build(*args, **kwargs):
        build_started.set()
        assert release.wait(3)
        return {"organism_contract": {}, "build_result": "unchanged"}

    class BlockingObserver:
        def __init__(self, root):
            assert root == tmp_path / "organism"

        def scan_and_update_suggestions(self, *, max_events):
            assert max_events == 1000
            scan_started.set()
            assert release.wait(3)
            return []

    monkeypatch.setattr(jobs_module, "build_organism_revision", blocked_build)
    monkeypatch.setattr(jobs_module, "ObserverService", BlockingObserver)
    try:
        rebuild = manager.submit_rebuild()
        scan = manager.submit_observer_scan()
        assert build_started.wait(3)
        assert scan_started.wait(3)
        assert manager.executor._max_workers == 2

        with pytest.raises(EvolutionJobConflict, match="job_already_active"):
            manager.submit_rebuild()
        with pytest.raises(EvolutionJobConflict, match="job_already_active"):
            manager.submit_observer_scan()
        assert manager.get_job(rebuild.job_id).state == "running"  # type: ignore[union-attr]
        assert manager.get_job(scan.job_id).state == "running"  # type: ignore[union-attr]
    finally:
        release.set()
        manager.shutdown()


@pytest.mark.parametrize(("entered", "expected"), [(-9, 0), (101, 100), (47, 47)])
def test_job_progress_is_clamped_to_public_percentage_range(tmp_path, monkeypatch, entered, expected):
    """Removing progress bounds must fail this externally visible status check."""
    manager = _manager(tmp_path)
    started = threading.Event()
    release = threading.Event()

    def blocked_build(*args, **kwargs):
        started.set()
        assert release.wait(3)
        return {"organism_contract": {}, "build_result": "unchanged"}

    monkeypatch.setattr(jobs_module, "build_organism_revision", blocked_build)
    try:
        job = manager.submit_rebuild()
        assert started.wait(3)
        assert manager.update_progress(job.job_id, entered).progress == expected
    finally:
        release.set()
        manager.shutdown()


def test_job_failure_exposes_only_a_stable_redacted_capped_code(tmp_path, monkeypatch):
    """Returning exception text to dashboard callers must fail this privacy check."""
    manager = _manager(tmp_path)
    leaked_secret = "api_key=sk-top-secret"

    def failed_build(*args, **kwargs):
        raise RuntimeError(f"{leaked_secret}; " + "x" * 10_000)

    monkeypatch.setattr(jobs_module, "build_organism_revision", failed_build)
    try:
        job = _wait(manager, manager.submit_rebuild().job_id)
        assert job.state == "failed"
        assert job.error_code == "job_failed"
        assert leaked_secret not in (job.error_code or "")
        assert len(job.error_code or "") <= 64
    finally:
        manager.shutdown()


def test_foreign_running_record_reads_as_interrupted_unknown_without_rewriting(tmp_path):
    """Treating a previous process as still running must fail after restart."""
    manager = _manager(tmp_path)
    job_id = str(uuid.uuid4())
    record_path = tmp_path / "organism" / "evolution" / "dashboard-jobs" / f"{job_id}.json"
    record_path.parent.mkdir(parents=True, mode=0o700)
    record_path.parents[2].chmod(0o700)
    record_path.parents[1].chmod(0o700)
    record_path.parent.chmod(0o700)
    record_path.write_text(
        json.dumps(
            {
                "job_id": job_id,
                "kind": "organism_rebuild",
                "state": "running",
                "progress": 55,
                "created_at": "2026-07-28T12:00:00Z",
                "started_at": "2026-07-28T12:00:01Z",
                "finished_at": None,
                "process_nonce": str(uuid.uuid4()),
                "result": None,
                "error_code": None,
            }
        ),
        encoding="utf-8",
    )
    record_path.chmod(0o600)
    before = record_path.read_bytes()
    try:
        job = manager.get_job(job_id)
        assert job is not None
        assert job.state == "unknown"
        assert job.error_code == "process_interrupted"
        assert record_path.read_bytes() == before
    finally:
        manager.shutdown()


def test_revision_diff_result_uses_known_kind_and_fixed_payload_budget(tmp_path, monkeypatch):
    """Passing an unbounded worker result through must fail this response cap."""
    manager = _manager(tmp_path)

    class FakeQuery:
        def __init__(self, store):
            assert store.root == tmp_path / "organism" / "gnothi_seauton"

        def diff(self, left, right):
            assert (left, right) == ("rev:left", "rev:right")
            return {
                "added_capabilities": [{"id": "capability:" + "x" * 50_000}],
                "truncated": False,
            }

    monkeypatch.setattr(jobs_module, "OrganismQuery", FakeQuery)
    try:
        job = _wait(manager, manager.submit_revision_diff("rev:left", "rev:right").job_id)
        assert job.state == "completed"
        assert job.result is not None
        assert job.result["kind"] == "revision_diff"
        assert len(json.dumps(job.result, sort_keys=True).encode("utf-8")) <= MAX_JOB_RESULT_BYTES
    finally:
        manager.shutdown()


def test_job_read_rejects_an_unknown_result_payload_shape(tmp_path):
    """Passing an unregistered result field through durable storage must fail closed."""
    manager = _manager(tmp_path)
    job_id = str(uuid.uuid4())
    record_path = tmp_path / "organism" / "evolution" / "dashboard-jobs" / f"{job_id}.json"
    record_path.parent.mkdir(parents=True, mode=0o700)
    record_path.parents[2].chmod(0o700)
    record_path.parents[1].chmod(0o700)
    record_path.parent.chmod(0o700)
    record_path.write_text(
        json.dumps(
            {
                "job_id": job_id,
                "kind": "revision_diff",
                "state": "completed",
                "progress": 100,
                "created_at": "2026-07-28T12:00:00Z",
                "started_at": "2026-07-28T12:00:01Z",
                "finished_at": "2026-07-28T12:00:02Z",
                "process_nonce": "dashboard-instance-42",
                "result": {"kind": "revision_diff", "unexpected": "private"},
                "error_code": None,
            }
        ),
        encoding="utf-8",
    )
    record_path.chmod(0o600)
    try:
        with pytest.raises(EvolutionJobStorageError, match="job_record_invalid"):
            manager.get_job(job_id)
    finally:
        manager.shutdown()


def test_cancel_only_changes_a_queued_job(tmp_path, monkeypatch):
    """Allowing v1 cancellation to alter running work must fail this test."""
    manager = _manager(tmp_path)
    build_started = threading.Event()
    scan_started = threading.Event()
    release = threading.Event()

    def blocked_build(*args, **kwargs):
        build_started.set()
        assert release.wait(3)
        return {"organism_contract": {}, "build_result": "unchanged"}

    class BlockingObserver:
        def __init__(self, root):
            pass

        def scan_and_update_suggestions(self, *, max_events):
            scan_started.set()
            assert release.wait(3)
            return []

    monkeypatch.setattr(jobs_module, "build_organism_revision", blocked_build)
    monkeypatch.setattr(jobs_module, "ObserverService", BlockingObserver)
    try:
        running = manager.submit_rebuild()
        manager.submit_observer_scan()
        assert build_started.wait(3)
        assert scan_started.wait(3)
        queued = manager.submit_revision_diff("rev:left", "rev:right")
        assert manager.get_job(queued.job_id).state == "queued"  # type: ignore[union-attr]

        assert manager.cancel_job(queued.job_id).state == "cancelled"
        with pytest.raises(EvolutionJobConflict, match="job_not_cancellable"):
            manager.cancel_job(running.job_id)
    finally:
        release.set()
        manager.shutdown()


def test_job_reads_reject_symlink_or_nonregular_job_paths(tmp_path):
    """Following attacker-controlled job paths must fail closed rather than read them."""
    root = tmp_path / "organism"
    root.mkdir(mode=0o700)
    evolution = root / "evolution"
    target = tmp_path / "outside"
    target.mkdir()
    evolution.symlink_to(target, target_is_directory=True)
    manager = EvolutionJobManager(root, workspace_root=_workspace(tmp_path))
    try:
        with pytest.raises(EvolutionJobStorageError, match="job_storage_unsafe"):
            manager.list_jobs()
    finally:
        manager.shutdown()


def test_manager_accepts_only_a_server_selected_repository_root(tmp_path):
    """Allowing arbitrary browser workspace paths must fail this constructor boundary."""
    with pytest.raises(EvolutionJobValidationError, match="invalid_workspace_root"):
        EvolutionJobManager(tmp_path / "organism", workspace_root=tmp_path / "browser-path")


def test_process_nonce_is_an_opaque_server_instance_identifier(tmp_path):
    """Requiring a UUID for an otherwise opaque server nonce must fail this contract."""
    manager = EvolutionJobManager(
        tmp_path / "organism",
        workspace_root=_workspace(tmp_path),
        process_nonce="dashboard-instance-42",
    )
    try:
        assert manager.process_nonce == "dashboard-instance-42"
    finally:
        manager.shutdown()


def test_default_process_nonce_is_shared_by_managers_in_one_server_process(tmp_path):
    """Giving each manager a fresh nonce must fail this process-restart contract."""
    workspace = _workspace(tmp_path)
    first = EvolutionJobManager(tmp_path / "organism", workspace_root=workspace)
    second = EvolutionJobManager(tmp_path / "organism", workspace_root=workspace)
    try:
        assert first.process_nonce == second.process_nonce
    finally:
        first.shutdown()
        second.shutdown()
