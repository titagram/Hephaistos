"""Real-filesystem contracts for immutable generation publication."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

import pytest

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
