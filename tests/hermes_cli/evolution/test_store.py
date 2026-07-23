"""Real-filesystem contracts for immutable generation publication."""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import stat
from pathlib import Path

import pytest

from hermes_cli.evolution import store as store_module
from hermes_cli.evolution.manifest import generation_id_for
from hermes_cli.evolution.store import GenerationStore, StableBaseIdentity


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _manifest() -> dict[str, object]:
    artifact = b"echo safe\n"
    lockfile = b"hello==1\n"
    return {
        "schema_version": 1,
        "parent_generation_id": "a" * 64,
        "source_suggestion_id": "suggestion-1",
        "blueprint_digest": "b" * 64,
        "stable_base": {"release": "0.17.0", "repository_commit": None, "compatibility_version": "1", "configuration_fingerprint": "d" * 64},
        "compatibility_range": ">=1,<2",
        "components": [{"class": "script", "logical_id": "hello", "path": "bin/hello.sh", "digest": _digest(artifact), "source": "https://example.test/hello", "author": "Hermes Maintainer", "license": "MIT", "provenance": "upstream-release", "capabilities": ["hello"], "lockfiles": [{"path": "locks/hello.lock", "digest": _digest(lockfile)}]}],
        "dependency_constraints": ["hello>=1,<2"], "resolved_versions": {"hello": "1.0"}, "credential_references": ["hello_service_token"], "service_prerequisites": ["hello-service"], "capabilities": ["hello"], "invariants": ["hello-is-safe"], "verification_commands": ["hello --verify"], "canary_policy": {"side_effects": "none"}, "resource_ceilings": {"cpu_seconds": 10}, "expected_organism_diff": "adds hello command", "build_environment": {"builder": "hermes", "version": "1"}, "builder_version": "1", "rollback_plan": "remove hello component", "incompatibility_reasons": [], "created_at": "2026-07-23T12:34:56.123456Z", "attestations": {"mutable": "later"},
    }


def _stage(root: Path, *, artifact: bytes = b"echo safe\n") -> None:
    (root / "bin").mkdir(parents=True)
    (root / "bin/hello.sh").write_bytes(artifact)
    (root / "locks").mkdir()
    (root / "locks/hello.lock").write_bytes(b"hello==1\n")


def _publish_after_barrier(
    root: Path,
    stage: Path,
    manifest: dict[str, object],
    barrier,
    results,
) -> None:
    try:
        barrier.wait(timeout=10)
        published = GenerationStore(root).publish_staged(stage, manifest)
        results.put(("ok", published.generation_id))
    except BaseException as exc:
        results.put(("error", type(exc).__name__, str(exc)))


def _lock_then_crash(root: Path, ready) -> None:
    store = GenerationStore(root)
    store._secure_root()
    with store._publication_lock():
        ready.set()
        os._exit(23)


def _join_process(process: multiprocessing.Process) -> None:
    process.join(timeout=15)
    if process.is_alive():
        process.terminate()
        process.join(timeout=5)
        pytest.fail("child process did not finish")


def test_equivalent_staged_bytes_converge_and_final_tree_is_read_only(tmp_path: Path) -> None:
    store = GenerationStore(tmp_path / "generations")
    manifest = _manifest()
    first_stage, second_stage = tmp_path / "stage-one", tmp_path / "stage-two"
    _stage(first_stage)
    _stage(second_stage)

    first = store.publish_staged(first_stage, manifest)
    second = store.publish_staged(second_stage, manifest)

    assert first == second
    assert first.generation_id == generation_id_for(manifest)
    assert not list((tmp_path / "generations").glob(".*.tmp"))
    assert stat.S_IMODE((first.root / "bin/hello.sh").stat().st_mode) & 0o222 == 0
    assert stat.S_IMODE(first.root.stat().st_mode) & 0o222 == 0
    with pytest.raises(PermissionError):
        os.open(first.root / "bin/hello.sh", os.O_WRONLY)


def test_same_identity_with_different_declared_bytes_fails(tmp_path: Path) -> None:
    store = GenerationStore(tmp_path / "generations")
    manifest = _manifest()
    good, bad = tmp_path / "good", tmp_path / "bad"
    _stage(good)
    _stage(bad, artifact=b"tampered\n")
    store.publish_staged(good, manifest)

    with pytest.raises(ValueError, match="integrity"):
        store.publish_staged(bad, manifest)


def test_failed_publish_leaves_no_generation_or_partial_directory(tmp_path: Path) -> None:
    store = GenerationStore(tmp_path / "generations")
    manifest = _manifest()
    stage = tmp_path / "stage"
    _stage(stage, artifact=b"tampered\n")

    with pytest.raises(ValueError):
        store.publish_staged(stage, manifest)

    assert not (tmp_path / "generations" / generation_id_for(manifest)).exists()
    assert not list((tmp_path / "generations").glob(".*.tmp"))


def test_publication_retries_short_writes_before_fsync(tmp_path: Path, monkeypatch) -> None:
    store = GenerationStore(tmp_path / "generations")
    stage = tmp_path / "stage"
    _stage(stage)
    original_write = os.write

    def short_write(descriptor: int, data: bytes) -> int:
        return original_write(descriptor, data[:1])

    monkeypatch.setattr(os, "write", short_write)

    published = store.publish_staged(stage, _manifest())

    assert (published.root / "bin/hello.sh").read_bytes() == b"echo safe\n"


def test_rename_failure_removes_only_the_operation_temporary_directory(
    tmp_path: Path, monkeypatch
) -> None:
    store = GenerationStore(tmp_path / "generations")
    stage = tmp_path / "stage"
    _stage(stage)

    def fail_rename(source: Path, destination: Path) -> None:
        raise OSError("injected rename failure")

    monkeypatch.setattr(os, "rename", fail_rename)

    with pytest.raises(OSError, match="injected rename failure"):
        store.publish_staged(stage, _manifest())

    assert not (tmp_path / "generations" / generation_id_for(_manifest())).exists()
    assert not list((tmp_path / "generations").glob(".generation-*"))


def test_metadata_failure_before_rename_cannot_publish_writable_destination(
    tmp_path: Path, monkeypatch
) -> None:
    import hermes_cli.evolution.store as store_module

    store = GenerationStore(tmp_path / "generations")
    stage = tmp_path / "stage"
    _stage(stage)
    monkeypatch.setattr(store_module, "_readonly_tree", lambda _: (_ for _ in ()).throw(OSError("chmod failed")))

    with pytest.raises(OSError, match="chmod failed"):
        store.publish_staged(stage, _manifest())

    assert not (tmp_path / "generations" / generation_id_for(_manifest())).exists()


def test_empty_overlay_baseline_uses_normal_publication(tmp_path: Path) -> None:
    store = GenerationStore(tmp_path / "generations")
    baseline = store.initialize_baseline(
        StableBaseIdentity("0.17.0", "c" * 40, "1", "d" * 64)
    )

    assert baseline.manifest["components"] == []
    assert store.verify(baseline.generation_id) == baseline


def test_new_store_root_fsyncs_its_inode_then_parent_directory(
    tmp_path: Path, monkeypatch
) -> None:
    parent = tmp_path / "evolution"
    parent.mkdir(mode=0o700)
    root = parent / "generations"
    parent_inode = parent.stat().st_ino
    directory_fsyncs: list[int] = []
    original_fsync = os.fsync

    def recording_fsync(descriptor: int) -> None:
        info = os.fstat(descriptor)
        if stat.S_ISDIR(info.st_mode):
            directory_fsyncs.append(info.st_ino)
        original_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", recording_fsync)
    GenerationStore(root)._secure_root()

    root_inode = root.stat().st_ino
    assert root_inode in directory_fsyncs
    assert parent_inode in directory_fsyncs
    assert directory_fsyncs.index(root_inode) < directory_fsyncs.index(parent_inode)


def test_absent_default_evolution_hierarchy_is_private_and_ledger_compatible(
    tmp_path: Path, monkeypatch
) -> None:
    import hermes_cli.evolution.store as store_module
    from hermes_cli.evolution.ledger import EvolutionLedger

    hermes_home = tmp_path / "hermes-home"
    hermes_home.mkdir(mode=0o700)
    monkeypatch.setattr(store_module, "get_hermes_home", lambda: hermes_home)

    store = store_module.GenerationStore()
    store._secure_root()

    assert stat.S_IMODE((hermes_home / "evolution").stat().st_mode) == 0o700
    assert stat.S_IMODE(store.root.stat().st_mode) == 0o700
    ledger = EvolutionLedger(hermes_home / "evolution" / "evolution.db")
    ledger.connection.close()


def test_first_use_fsyncs_each_new_hierarchy_dirent_in_durable_order(
    tmp_path: Path, monkeypatch
) -> None:
    hermes_home = tmp_path / "hermes-home"
    hermes_home.mkdir(mode=0o700)
    root = hermes_home / "evolution" / "generations"
    directory_fsyncs: list[int] = []
    original_fsync = os.fsync

    def recording_fsync(descriptor: int) -> None:
        info = os.fstat(descriptor)
        if stat.S_ISDIR(info.st_mode):
            directory_fsyncs.append(info.st_ino)
        original_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", recording_fsync)
    GenerationStore(root)._secure_root()

    evolution_inode = (hermes_home / "evolution").stat().st_ino
    generations_inode = root.stat().st_ino
    assert directory_fsyncs == [
        evolution_inode,
        hermes_home.stat().st_ino,
        generations_inode,
        evolution_inode,
    ]


@pytest.mark.parametrize("hostile_kind", ["mode", "symlink", "file"])
def test_hostile_existing_intermediate_hierarchy_fails_closed(
    tmp_path: Path, hostile_kind: str
) -> None:
    hermes_home = tmp_path / "hermes-home"
    hermes_home.mkdir(mode=0o700)
    evolution = hermes_home / "evolution"
    if hostile_kind == "mode":
        evolution.mkdir(mode=0o700)
        evolution.chmod(0o755)
    elif hostile_kind == "symlink":
        target = tmp_path / "target"
        target.mkdir(mode=0o700)
        evolution.symlink_to(target, target_is_directory=True)
    else:
        evolution.write_text("not a directory")

    with pytest.raises(ValueError, match="integrity"):
        GenerationStore(evolution / "generations")._secure_root()
    if hostile_kind == "mode":
        assert stat.S_IMODE(evolution.stat().st_mode) == 0o755


def test_managed_hierarchy_owner_mismatch_fails_closed(
    tmp_path: Path, monkeypatch
) -> None:
    hermes_home = tmp_path / "hermes-home"
    hermes_home.mkdir(mode=0o700)
    actual_uid = os.geteuid()
    monkeypatch.setattr(os, "geteuid", lambda: actual_uid + 1)

    with pytest.raises(ValueError, match="ownership"):
        GenerationStore(hermes_home / "evolution" / "generations")._secure_root()


def test_existing_hostile_store_root_is_not_chmod_repaired(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "generations"
    root.mkdir()
    root.chmod(0o755)
    root_inode = root.stat().st_ino
    fchmod_inodes: list[int] = []
    original_fchmod = os.fchmod

    def recording_fchmod(descriptor: int, mode: int) -> None:
        fchmod_inodes.append(os.fstat(descriptor).st_ino)
        original_fchmod(descriptor, mode)

    monkeypatch.setattr(os, "fchmod", recording_fchmod)

    with pytest.raises(ValueError, match="private"):
        GenerationStore(root).verify("a" * 64)
    assert root_inode not in fchmod_inodes
    assert stat.S_IMODE(root.stat().st_mode) == 0o755


def test_concurrent_first_publishers_converge_when_store_root_is_absent(
    tmp_path: Path,
) -> None:
    context = multiprocessing.get_context("spawn")
    root = tmp_path / "generations"
    stages = [tmp_path / "stage-one", tmp_path / "stage-two"]
    for stage in stages:
        _stage(stage)
    barrier = context.Barrier(2)
    results = context.Queue()
    processes = [
        context.Process(
            target=_publish_after_barrier,
            args=(root, stage, _manifest(), barrier, results),
        )
        for stage in stages
    ]

    for process in processes:
        process.start()
    for process in processes:
        _join_process(process)

    outcomes = [results.get(timeout=5) for _ in processes]
    assert outcomes == [
        ("ok", generation_id_for(_manifest())),
        ("ok", generation_id_for(_manifest())),
    ]
    assert all(process.exitcode == 0 for process in processes)
    assert GenerationStore(root).verify(generation_id_for(_manifest())).generation_id == generation_id_for(_manifest())


def test_publication_lock_is_reacquired_after_process_crash(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    root = tmp_path / "generations"
    ready = context.Event()
    process = context.Process(target=_lock_then_crash, args=(root, ready))
    process.start()
    assert ready.wait(timeout=10)
    _join_process(process)
    assert process.exitcode == 23

    stage = tmp_path / "stage"
    _stage(stage)
    assert (
        GenerationStore(root).publish_staged(stage, _manifest()).generation_id
        == generation_id_for(_manifest())
    )


def test_verify_rejects_generation_path_swapped_between_digest_phases(
    tmp_path: Path, monkeypatch
) -> None:
    store = GenerationStore(tmp_path / "generations")
    stage = tmp_path / "stage"
    _stage(stage)
    published = store.publish_staged(stage, _manifest())
    manifest_bytes = (published.root / "manifest.json").read_bytes()
    artifact_inode = (published.root / "bin/hello.sh").stat().st_ino
    original_read = os.read
    swapped = False

    def swap_generation() -> None:
        nonlocal swapped
        original = published.root.with_name(f".original-{published.generation_id}")
        published.root.rename(original)
        _stage(published.root, artifact=b"tampered\n")
        (published.root / "manifest.json").write_bytes(manifest_bytes)
        for path in (
            published.root / "bin/hello.sh",
            published.root / "locks/hello.lock",
            published.root / "manifest.json",
        ):
            path.chmod(0o444)
        (published.root / "bin").chmod(0o555)
        (published.root / "locks").chmod(0o555)
        published.root.chmod(0o555)
        swapped = True

    def racing_read(descriptor: int, count: int) -> bytes:
        data = original_read(descriptor, count)
        if (
            not swapped
            and not data
            and stat.S_ISREG(os.fstat(descriptor).st_mode)
            and os.fstat(descriptor).st_ino == artifact_inode
        ):
            swap_generation()
        return data

    monkeypatch.setattr(os, "read", racing_read)

    with pytest.raises(ValueError, match="integrity"):
        store.verify(published.generation_id)
    assert swapped


def test_publication_fsyncs_regular_files_after_they_become_read_only(
    tmp_path: Path, monkeypatch
) -> None:
    store = GenerationStore(tmp_path / "generations")
    stage = tmp_path / "stage"
    _stage(stage)
    events: list[tuple[str, int, int]] = []
    expected_inodes: set[int] = set()
    original_fsync = os.fsync
    original_rename = os.rename

    def recording_fsync(fd: int) -> None:
        info = os.fstat(fd)
        if stat.S_ISREG(info.st_mode):
            events.append(("fsync", info.st_ino, stat.S_IMODE(info.st_mode)))
        original_fsync(fd)

    def recording_rename(source: Path, destination: Path) -> None:
        expected_inodes.update(
            path.stat().st_ino for path in Path(source).rglob("*") if path.is_file()
        )
        events.append(("rename", 0, 0))
        original_rename(source, destination)

    monkeypatch.setattr(os, "fsync", recording_fsync)
    monkeypatch.setattr(os, "rename", recording_rename)
    store.publish_staged(stage, _manifest())

    rename_index = events.index(("rename", 0, 0))
    assert len(expected_inodes) == 3
    for inode in expected_inodes:
        assert any(
            event == ("fsync", inode, 0o444)
            for event in events[:rename_index]
        )


def test_posix_capability_gate_is_bounded(monkeypatch, tmp_path: Path) -> None:
    import hermes_cli.evolution.store as module

    monkeypatch.delattr(module.os, "O_NOFOLLOW", raising=False)
    with pytest.raises(ValueError, match="POSIX"):
        GenerationStore(tmp_path / "generations").verify("a" * 64)


def test_posix_capability_gate_checks_descriptor_relative_primitives(
    monkeypatch, tmp_path: Path
) -> None:
    import hermes_cli.evolution.store as module

    monkeypatch.setattr(module.os, "supports_dir_fd", frozenset())
    with pytest.raises(ValueError, match="POSIX"):
        GenerationStore(tmp_path / "generations").verify("a" * 64)


def test_verify_existing_never_creates_a_missing_store_root(
    tmp_path: Path,
) -> None:
    root = tmp_path / "evolution" / "generations"
    (tmp_path / "evolution").mkdir(mode=0o700)

    with pytest.raises(ValueError, match="integrity"):
        GenerationStore(root).verified_manifest_descriptor_existing("a" * 64)

    assert not root.exists()


@pytest.mark.parametrize(
    ("corruption", "expected_code"),
    [
        ("generation_missing", "generation_missing"),
        ("manifest_missing", "manifest_missing"),
        ("manifest_noncanonical", "manifest_noncanonical"),
        ("manifest_wrong", "manifest_identity_mismatch"),
        ("content_wrong", "published_content_mismatch"),
    ],
)
def test_existing_generation_proof_returns_typed_bounded_evidence(
    tmp_path: Path,
    corruption: str,
    expected_code: str,
) -> None:
    store = GenerationStore(tmp_path / "generations")
    stage = tmp_path / "stage"
    _stage(stage)
    published = store.publish_staged(stage, _manifest())
    root = published.root
    manifest_path = root / "manifest.json"
    if corruption == "generation_missing":
        root.rename(root.with_name(f".missing-{root.name}"))
    else:
        root.chmod(0o755)
        manifest_path.chmod(0o600)
        if corruption == "manifest_missing":
            manifest_path.unlink()
        elif corruption == "manifest_noncanonical":
            manifest_path.write_bytes(manifest_path.read_bytes() + b"\n")
        elif corruption == "manifest_wrong":
            value = json.loads(manifest_path.read_bytes())
            value["builder_version"] = "wrong"
            manifest_path.write_bytes(
                store_module.canonical_json_bytes(value)
            )
        else:
            artifact = root / "bin" / "hello.sh"
            artifact.chmod(0o600)
            artifact.write_bytes(b"wrong content\n")
            artifact.chmod(0o444)
        if manifest_path.exists():
            manifest_path.chmod(0o444)
        root.chmod(0o555)

    observation = store.observe_existing_generation(
        published.generation_id
    )

    assert observation.descriptor is None
    assert observation.failure_code == expected_code
    assert len(observation.evidence_digest) == 64
    assert all(
        character in "0123456789abcdef"
        for character in observation.evidence_digest
    )
    assert not hasattr(observation, "raw")
    assert not hasattr(observation, "path")


def test_existing_generation_proof_enforces_manifest_and_entry_bounds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = GenerationStore(tmp_path / "generations")
    stage = tmp_path / "stage"
    _stage(stage)
    published = store.publish_staged(stage, _manifest())

    monkeypatch.setattr(store_module, "_MAX_PROOF_MANIFEST_BYTES", 8)
    manifest_limited = store.observe_existing_generation(
        published.generation_id
    )
    monkeypatch.setattr(store_module, "_MAX_PROOF_MANIFEST_BYTES", 256 * 1024)
    monkeypatch.setattr(store_module, "_MAX_PROOF_ENTRIES", 1)
    entry_limited = store.observe_existing_generation(
        published.generation_id
    )

    assert manifest_limited.failure_code == "proof_limit_exceeded"
    assert entry_limited.failure_code == "proof_limit_exceeded"
    assert (
        manifest_limited.evidence_digest
        != entry_limited.evidence_digest
    )
