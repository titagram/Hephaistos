from __future__ import annotations

import copy
import gzip
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from hermes_cli.hades_graph_v2 import (
    artifact_graph_version,
    artifact_to_payload,
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


@pytest.fixture(scope="module")
def exact_manifest_ceiling_artifact():
    from hermes_cli.hades_graph_v2.bundle import (
        MAX_MANIFEST_BYTES,
        build_bundle_plan,
    )

    payload = copy.deepcopy(_valid_semantic_artifact())
    payload["nodes"] = []
    payload["graph_contract"]["coverage"]["files"].update(
        discovered=0,
        hashed=0,
        parser_candidates=0,
        analyzed=0,
    )
    payload["graph_contract"]["coverage"]["records"]["nodes"] = 0
    payload["languages"][0].update(
        detected_file_count=0,
        analyzed_file_count=0,
    )
    completeness = payload["graph_contract"]["completeness"]
    completeness["languages"][0]["capabilities"] = copy.deepcopy(
        completeness["languages"][0]["capabilities"]
    )
    completeness["status"] = "partial"
    reasons = []
    for reason_index in range(103):
        reasons.append({
            "code": "invalid_source_fact",
            "count": 1,
            "language": None,
            "paths_sample": [
                f"manifest/{reason_index:03d}/{path_index:02d}-x"
                for path_index in range(10)
            ],
        })
    completeness["capabilities"]["inventory"] = {
        "status": "partial",
        "reasons": reasons,
    }
    payload["graph_contract"]["artifact_graph_version"] = artifact_graph_version(
        payload
    )
    limits = _limits(max_bundle_uncompressed_bytes=16 * 1024 * 1024)
    initial = build_bundle_plan(payload, limits)
    remaining = MAX_MANIFEST_BYTES - len(initial.manifest_bytes)
    assert remaining > 0

    for reason in reasons:
        for index, path in enumerate(reason["paths_sample"]):
            growth = min(remaining, 4_096 - len(path))
            reason["paths_sample"][index] = path + ("x" * growth)
            remaining -= growth
            if remaining == 0:
                break
        if remaining == 0:
            break
    assert remaining == 0
    payload["graph_contract"]["artifact_graph_version"] = artifact_graph_version(
        payload
    )
    exact = build_bundle_plan(payload, limits)
    assert len(exact.manifest_bytes) == MAX_MANIFEST_BYTES
    return payload


def _shift_manifest_size(payload: dict, delta: int) -> dict:
    shifted = copy.deepcopy(payload)
    reasons = shifted["graph_contract"]["completeness"]["capabilities"]["inventory"][
        "reasons"
    ]
    if delta < 0:
        path = reasons[0]["paths_sample"][0]
        assert len(path) > len("manifest/000/00-x")
        reasons[0]["paths_sample"][0] = path[:delta]
    elif delta > 0:
        for reason in reasons:
            for index, path in enumerate(reason["paths_sample"]):
                if len(path) < 4_096:
                    reason["paths_sample"][index] = path + ("x" * delta)
                    break
            else:
                continue
            break
        else:  # pragma: no cover - fixture capacity invariant
            raise AssertionError("manifest fixture has no remaining safe-path capacity")
    shifted["graph_contract"]["artifact_graph_version"] = artifact_graph_version(
        shifted
    )
    return shifted


@pytest.mark.parametrize("delta", [-1, 0], ids=["below", "exact"])
def test_manifest_envelope_below_or_at_4mib_is_valid_for_writer_and_pruner(
    tmp_path,
    exact_manifest_ceiling_artifact,
    delta,
):
    from hermes_cli.hades_graph_v2.bundle import (
        MAX_MANIFEST_BYTES,
        GraphBundleWriter,
    )
    from hermes_cli.hades_graph_v2.pruning import GraphBudgetPruner

    artifact = _shift_manifest_size(exact_manifest_ceiling_artifact, delta)
    limits = _limits(max_bundle_uncompressed_bytes=16 * 1024 * 1024)
    bundle = GraphBundleWriter().write(
        artifact,
        tmp_path / f"writer-{delta}",
        limits,
    )
    assert len(canonical_json_bytes(bundle.manifest)) == MAX_MANIFEST_BYTES + delta

    selected = GraphBudgetPruner().select(artifact, limits)
    assert artifact_to_payload(selected) == artifact


def test_manifest_envelope_above_4mib_is_graph_record_too_large_for_both_entrypoints(
    tmp_path,
    exact_manifest_ceiling_artifact,
):
    from hermes_cli.hades_graph_v2.bundle import (
        CHUNK_KINDS,
        GraphBundleError,
        GraphBundleWriter,
        build_bundle_plan,
    )
    from hermes_cli.hades_graph_v2.pruning import GraphBudgetError, GraphBudgetPruner

    artifact = _shift_manifest_size(exact_manifest_ceiling_artifact, 1)
    limits = _limits(max_bundle_uncompressed_bytes=16 * 1024 * 1024)
    assert all(not artifact[kind] for kind in CHUNK_KINDS)
    with pytest.raises(GraphBundleError):
        build_bundle_plan(artifact, limits, enforce_total=False)
    with pytest.raises(GraphBundleError) as writer_error:
        GraphBundleWriter().write(artifact, tmp_path / "writer-above", limits)
    with pytest.raises(GraphBudgetError) as pruner_error:
        GraphBudgetPruner().select(artifact, limits)

    assert writer_error.value.code == "graph_record_too_large"
    assert type(writer_error.value).__name__ == "GraphEnvelopeTooLargeError"
    assert pruner_error.value.code == "graph_record_too_large"


def test_record_derived_manifest_overflow_is_recoverable_by_atomic_pruning(
    tmp_path,
    monkeypatch,
):
    from hermes_cli.hades_graph_v2 import bundle as bundle_module
    from hermes_cli.hades_graph_v2.pruning import GraphBudgetPruner
    from tests.hermes_cli.test_hades_graph_budget_pruner import _route_artifact

    artifact = _route_artifact(3)
    limits = _limits(max_bundle_uncompressed_bytes=32 * 1024 * 1024)
    normal = bundle_module.build_bundle_plan(artifact, limits)
    manifest_ceiling = 8_000
    assert len(normal.manifest_bytes) > manifest_ceiling
    monkeypatch.setattr(bundle_module, "MAX_MANIFEST_BYTES", manifest_ceiling)

    with pytest.raises(bundle_module.GraphBundleError) as writer_error:
        bundle_module.GraphBundleWriter().write(
            artifact,
            tmp_path / "writer-record-derived",
            limits,
        )
    selected = GraphBudgetPruner().select(artifact, limits)
    final = bundle_module.build_bundle_plan(selected, limits)

    assert writer_error.value.code == "resource_budget_reached"
    assert type(writer_error.value).__name__ == "GraphManifestCapacityError"
    assert 0 < len(selected.entrypoints) < len(artifact["entrypoints"])
    assert len(final.manifest_bytes) <= manifest_ceiling


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


def test_large_bundle(tmp_path):
    from hermes_cli.hades_backend_benchmark import run_hades_backend_benchmark
    from hermes_cli.hades_graph_v2.bundle import CHUNK_KINDS

    del tmp_path
    report = run_hades_backend_benchmark(
        cases=[
            {
                "name": "large_code_graph",
                "nodes": 5_501,
                "entrypoints": 501,
                "edges": 10_501,
            }
        ]
    )
    assert report["case_count"] == 1
    graph = report["cases"][0]
    assert graph["requested_counts"] == {
        "nodes": 5_501,
        "entrypoints": 501,
        "edges": 10_501,
    }
    assert all(
        graph["source_counts"][kind] >= graph["requested_counts"][kind]
        for kind in graph["requested_counts"]
    )
    assert graph["chunk_count"] > len(CHUNK_KINDS)
    assert graph["delivered_counts"] == graph["manifest_counts"]
    assert graph["descriptor_counts"] == graph["manifest_counts"]
    assert graph["reassembled_counts"] == graph["manifest_counts"]
    assert {
        kind: graph["delivered_counts"][kind] + graph["omitted_counts"][kind]
        for kind in CHUNK_KINDS
    } == graph["source_counts"]
    assert graph["coverage_omission_ledger"] == sum(graph["omitted_counts"].values())
    assert graph["deterministic"] is True
    assert graph["reassembly_valid"] is True
    assert (
        graph["reassembled_artifact_graph_version"] == graph["artifact_graph_version"]
    )
    assert len(graph["artifact_graph_version"]) == 64
    assert len(graph["manifest_sha256"]) == 64


def test_gate_report_generator_rejects_nonpassing_pytest_summaries() -> None:
    script = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "generate_graph_v2_agent_gates.py"
    )
    spec = importlib.util.spec_from_file_location("graph_v2_gate_report", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    expected_python = Path(__file__).resolve().parents[2] / ".venv" / "bin" / "python"
    assert module._resolved_python(Path(".venv/bin/python")) == expected_python
    assert module._resolved_python(expected_python) == expected_python
    assert module._passed_count(". 1 passed in 0.10s") == 1
    for output in (
        "1 skipped in 0.10s",
        "1 xfailed in 0.10s",
        "1 passed, 1 deselected in 0.10s",
        "no tests ran in 0.10s",
    ):
        with pytest.raises(ValueError, match="gate subprocess"):
            module._passed_count(output)


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

    with pytest.raises(GraphBundleError, match="record_too_large") as exc_info:
        GraphBundleWriter().write(
            _valid_semantic_artifact(),
            tmp_path / "spool",
            _limits(max_chunk_uncompressed_bytes=512),
        )
    assert exc_info.value.code == "record_too_large"
    assert type(exc_info.value).__name__ == "GraphUnitRecordTooLargeError"


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
