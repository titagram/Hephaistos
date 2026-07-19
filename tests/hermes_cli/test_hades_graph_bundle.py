from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from hermes_cli.hades_graph_v2 import (
    canonical_json_bytes,
    load_json_bytes,
    validate_artifact,
    validate_schema,
)
from tests.hermes_cli.test_hades_graph_contract import (
    _valid_flow_artifact,
    _valid_semantic_artifact,
)


def _limits(**overrides):
    from hermes_cli.hades_graph_v2.bundle import BundleLimits

    values = {
        "max_chunk_uncompressed_bytes": 2_048,
        "max_bundle_uncompressed_bytes": 8 * 1024 * 1024,
        "max_chunks": 512,
    }
    values.update(overrides)
    return BundleLimits(**values)


def _logical_bytes(bundle) -> int:
    return len(canonical_json_bytes(bundle.manifest)) + sum(
        descriptor["uncompressed_bytes"] for descriptor in bundle.manifest["chunks"]
    )


def _rewrite_resume_manifest_digest(spool: Path) -> None:
    resume_path = spool / "resume.json"
    resume = json.loads(resume_path.read_bytes())
    resume["manifest_sha256"] = hashlib.sha256(
        (spool / "manifest.json").read_bytes()
    ).hexdigest()
    resume_path.write_bytes(canonical_json_bytes(resume))
    os.chmod(resume_path, 0o600)


def test_writer_emits_descriptor_order_exact_jcs_and_single_member_gzip(tmp_path):
    from hermes_cli.hades_graph_v2.bundle import CHUNK_KINDS, GraphBundleWriter

    artifact = _valid_flow_artifact()
    first = GraphBundleWriter().write(artifact, tmp_path / "first", _limits())
    second = GraphBundleWriter().write(artifact, tmp_path / "second", _limits())

    assert first.manifest == second.manifest
    assert [path.read_bytes() for path in first.chunk_paths] == [
        path.read_bytes() for path in second.chunk_paths
    ]
    validate_schema("bundle.schema.json", first.manifest)
    descriptors = first.manifest["chunks"]
    assert [row["index"] for row in descriptors] == list(range(len(descriptors)))
    assert [CHUNK_KINDS.index(row["kind"]) for row in descriptors] == sorted(
        CHUNK_KINDS.index(row["kind"]) for row in descriptors
    )

    last_id_by_kind: dict[str, str] = {}
    for descriptor, path in zip(descriptors, first.chunk_paths, strict=True):
        compressed = path.read_bytes()
        assert compressed[:4] == b"\x1f\x8b\x08\x00"
        assert compressed[4:8] == b"\x00\x00\x00\x00"
        assert compressed[9] == 255
        assert hashlib.sha256(compressed).hexdigest() == descriptor["compressed_sha256"]
        raw = gzip.decompress(compressed)
        assert hashlib.sha256(raw).hexdigest() == descriptor["sha256"]
        chunk = load_json_bytes(raw)
        assert raw == canonical_json_bytes(chunk)
        assert list(chunk) == ["schema", "index", "kind", "records"] or set(chunk) == {
            "schema",
            "index",
            "kind",
            "records",
        }
        assert chunk["schema"] == "hades.graph_chunk.v2"
        assert chunk["index"] == descriptor["index"]
        assert chunk["kind"] == descriptor["kind"]
        assert len(chunk["records"]) == descriptor["record_count"]
        ids = [record["id"] for record in chunk["records"]]
        assert ids == sorted(ids)
        if ids and descriptor["kind"] in last_id_by_kind:
            assert last_id_by_kind[descriptor["kind"]] < ids[0]
        if ids:
            last_id_by_kind[descriptor["kind"]] = ids[-1]


def test_chunk_reassembly_is_referentially_complete_and_count_identical(tmp_path):
    from hermes_cli.hades_graph_v2.bundle import CHUNK_KINDS, GraphBundleWriter

    artifact = _valid_flow_artifact()
    bundle = GraphBundleWriter().write(artifact, tmp_path / "spool", _limits())
    reassembled = {kind: [] for kind in CHUNK_KINDS}
    for path in bundle.chunk_paths:
        chunk = json.loads(gzip.decompress(path.read_bytes()))
        reassembled[chunk["kind"]].extend(chunk["records"])

    rebuilt = dict(artifact)
    rebuilt.update(reassembled)
    validate_artifact(rebuilt)
    assert {kind: len(reassembled[kind]) for kind in CHUNK_KINDS} == {
        kind: bundle.manifest["counts"][kind] for kind in CHUNK_KINDS
    }


def test_chunk_bytes_are_permutation_invariant_at_the_canonical_boundary(tmp_path):
    from hermes_cli.hades_graph_v2.bundle import GraphBundleWriter

    artifact = _valid_flow_artifact()
    permuted = dict(artifact)
    for kind in (
        "entrypoints",
        "nodes",
        "structures",
        "edges",
        "flows",
        "flow_steps",
        "uncertainties",
    ):
        permuted[kind] = list(reversed(artifact[kind]))

    first = GraphBundleWriter().write(artifact, tmp_path / "first", _limits())
    second = GraphBundleWriter().write(permuted, tmp_path / "second", _limits())

    assert first.manifest == second.manifest
    assert [path.read_bytes() for path in first.chunk_paths] == [
        path.read_bytes() for path in second.chunk_paths
    ]


def test_exact_logical_bundle_ceiling_passes_and_one_byte_less_fails(tmp_path):
    from hermes_cli.hades_graph_v2.bundle import GraphBundleError, GraphBundleWriter

    writer = GraphBundleWriter()
    measured = writer.write(_valid_semantic_artifact(), tmp_path / "measure", _limits())
    exact = _logical_bytes(measured)

    writer.write(
        _valid_semantic_artifact(),
        tmp_path / "exact",
        _limits(max_bundle_uncompressed_bytes=exact),
    )
    with pytest.raises(GraphBundleError, match="total-byte limit"):
        writer.write(
            _valid_semantic_artifact(),
            tmp_path / "short",
            _limits(max_bundle_uncompressed_bytes=exact - 1),
        )


def test_resume_after_chunk_two_tracks_only_persisted_acknowledgements(tmp_path):
    from hermes_cli.hades_graph_v2.bundle import GraphBundleWriter

    writer = GraphBundleWriter()
    bundle = writer.write(_valid_flow_artifact(), tmp_path / "spool", _limits())
    assert len(bundle.chunk_paths) >= 3
    bundle.record_uploaded(0)
    bundle.record_uploaded(1)
    bundle.record_uploaded(2)

    resumed = writer.resume_state(tmp_path / "spool")

    assert resumed.artifact_graph_version == bundle.artifact_graph_version
    assert resumed.uploaded_chunk_indexes == (0, 1, 2)
    assert resumed.missing_chunk_indexes == tuple(
        range(3, len(bundle.manifest["chunks"]))
    )


def test_resume_detects_manifest_mutation_after_chunk_two(tmp_path):
    from hermes_cli.hades_graph_v2.bundle import GraphBundleError, GraphBundleWriter

    writer = GraphBundleWriter()
    bundle = writer.write(_valid_flow_artifact(), tmp_path / "spool", _limits())
    for index in range(3):
        bundle.record_uploaded(index)
    manifest = json.loads((bundle.spool / "manifest.json").read_bytes())
    manifest["generated_at"] = "2026-07-16T12:00:01Z"
    (bundle.spool / "manifest.json").write_bytes(canonical_json_bytes(manifest))

    with pytest.raises(GraphBundleError, match="manifest changed after digest"):
        writer.resume_state(bundle.spool)


def test_resume_rejects_compressed_digest_mismatch(tmp_path):
    from hermes_cli.hades_graph_v2.bundle import GraphBundleError, GraphBundleWriter

    writer = GraphBundleWriter()
    bundle = writer.write(_valid_semantic_artifact(), tmp_path / "spool", _limits())
    path = bundle.chunk_paths[0]
    path.write_bytes(path.read_bytes()[:-1] + b"x")

    with pytest.raises(GraphBundleError, match="compressed digest"):
        writer.resume_state(bundle.spool)


def test_resume_rejects_uncompressed_digest_mismatch_even_if_wire_digest_matches(
    tmp_path,
):
    from hermes_cli.hades_graph_v2.bundle import GraphBundleError, GraphBundleWriter

    writer = GraphBundleWriter()
    bundle = writer.write(_valid_semantic_artifact(), tmp_path / "spool", _limits())
    manifest_path = bundle.spool / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    path = bundle.chunk_paths[0]
    original = gzip.decompress(path.read_bytes())
    changed = original.replace(b'"records":[', b'"records": [', 1)
    assert changed != original
    compressed = gzip.compress(changed, compresslevel=6, mtime=0)
    compressed = compressed[:9] + b"\xff" + compressed[10:]
    path.write_bytes(compressed)
    descriptor = manifest["chunks"][0]
    descriptor["compressed_bytes"] = len(compressed)
    descriptor["compressed_sha256"] = hashlib.sha256(compressed).hexdigest()
    descriptor["uncompressed_bytes"] = len(changed)
    manifest_path.write_bytes(canonical_json_bytes(manifest))
    _rewrite_resume_manifest_digest(bundle.spool)

    with pytest.raises(GraphBundleError, match="uncompressed digest"):
        writer.resume_state(bundle.spool)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda body: body + b"trailing",
        lambda body: body + body,
    ],
    ids=["trailing-bytes", "second-member"],
)
def test_resume_rejects_trailing_bytes_and_concatenated_members(tmp_path, mutation):
    from hermes_cli.hades_graph_v2.bundle import GraphBundleError, GraphBundleWriter

    writer = GraphBundleWriter()
    bundle = writer.write(_valid_semantic_artifact(), tmp_path / "spool", _limits())
    manifest_path = bundle.spool / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    path = bundle.chunk_paths[0]
    changed = mutation(path.read_bytes())
    path.write_bytes(changed)
    descriptor = manifest["chunks"][0]
    descriptor["compressed_bytes"] = len(changed)
    descriptor["compressed_sha256"] = hashlib.sha256(changed).hexdigest()
    manifest_path.write_bytes(canonical_json_bytes(manifest))
    _rewrite_resume_manifest_digest(bundle.spool)

    with pytest.raises(GraphBundleError, match="trailing bytes|multiple gzip"):
        writer.resume_state(bundle.spool)


def test_spool_directories_and_all_files_use_private_modes(tmp_path):
    from hermes_cli.hades_graph_v2.bundle import GraphBundleWriter

    bundle = GraphBundleWriter().write(
        _valid_semantic_artifact(), tmp_path / "spool", _limits()
    )

    assert stat.S_IMODE(bundle.spool.stat().st_mode) == 0o700
    for path in bundle.spool.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_locked_spool_is_never_cleanup_deleted_then_unlocked_stale_spool_is(tmp_path):
    from hermes_cli.hades_graph_v2.bundle import GraphBundleWriter

    writer = GraphBundleWriter()
    bundle = writer.write(
        _valid_semantic_artifact(), tmp_path / "root" / "spool", _limits()
    )
    old = 1_600_000_000
    for path in bundle.spool.iterdir():
        os.utime(path, (old, old))
    os.utime(bundle.spool, (old, old))

    with writer.lock(bundle.spool):
        assert (
            writer.cleanup_stale(tmp_path / "root", ttl_seconds=3_600, now=old + 7_200)
            == ()
        )
        assert bundle.spool.exists()

    assert writer.cleanup_stale(
        tmp_path / "root", ttl_seconds=3_600, now=old + 7_200
    ) == (bundle.spool,)
    assert not bundle.spool.exists()


def test_incomplete_unlocked_stale_spool_is_cleanup_deleted(tmp_path):
    from hermes_cli.hades_graph_v2.bundle import GraphBundleWriter

    writer = GraphBundleWriter()
    spool = tmp_path / "root" / "incomplete"
    spool.mkdir(parents=True)
    (spool / ".lock").write_bytes(b"")
    (spool / ".chunk-00000.gz.123.tmp").write_bytes(b"partial")
    old = 1_600_000_000
    for path in spool.iterdir():
        os.utime(path, (old, old))
    os.utime(spool, (old, old))

    assert writer.cleanup_stale(
        tmp_path / "root", ttl_seconds=3_600, now=old + 7_200
    ) == (spool,)
    assert not spool.exists()


def test_exclusive_lock_uses_native_windows_fallback(tmp_path, monkeypatch):
    from hermes_cli.hades_graph_v2 import bundle as bundle_module

    calls = []
    fake_msvcrt = SimpleNamespace(
        LK_LOCK=1,
        LK_NBLCK=2,
        LK_UNLCK=3,
        locking=lambda descriptor, operation, size: calls.append((
            descriptor,
            operation,
            size,
        )),
    )
    monkeypatch.setattr(bundle_module, "fcntl", None)
    monkeypatch.setattr(bundle_module, "msvcrt", fake_msvcrt, raising=False)

    with bundle_module.GraphBundleWriter().lock(tmp_path / "spool"):
        assert calls[-1][1:] == (fake_msvcrt.LK_LOCK, 1)

    assert calls[-1][1:] == (fake_msvcrt.LK_UNLCK, 1)


def test_successful_or_canceled_spool_is_deleted_explicitly(tmp_path):
    from hermes_cli.hades_graph_v2.bundle import GraphBundleWriter

    writer = GraphBundleWriter()
    published = writer.write(
        _valid_semantic_artifact(), tmp_path / "published", _limits()
    )
    canceled = writer.write(
        _valid_semantic_artifact(), tmp_path / "canceled", _limits()
    )

    writer.delete(published.spool, outcome="published")
    writer.delete(canceled.spool, outcome="canceled")

    assert not published.spool.exists()
    assert not canceled.spool.exists()


def test_record_larger_than_chunk_is_a_typed_hard_writer_failure(tmp_path):
    from hermes_cli.hades_graph_v2.bundle import GraphBundleError, GraphBundleWriter

    with pytest.raises(GraphBundleError, match="graph_record_too_large"):
        GraphBundleWriter().write(
            _valid_semantic_artifact(),
            tmp_path / "spool",
            _limits(max_chunk_uncompressed_bytes=512),
        )


def test_zero_record_artifact_emits_zero_chunks(tmp_path):
    from hermes_cli.hades_graph_v2.bundle import CHUNK_KINDS, GraphBundleWriter

    artifact = _valid_semantic_artifact()
    # The file record is still inventory and therefore one chunk; this probe
    # uses the pruner-produced empty envelope to prove 0 chunks is supported.
    from hermes_cli.hades_graph_v2.pruning import GraphBudgetPruner

    selected = GraphBudgetPruner().select(
        artifact,
        _limits(max_bundle_uncompressed_bytes=3_000),
    )
    bundle = GraphBundleWriter().write(selected, tmp_path / "spool", _limits())

    assert all(not getattr(selected, kind) for kind in CHUNK_KINDS)
    assert bundle.manifest["chunks"] == []
    assert bundle.chunk_paths == ()
