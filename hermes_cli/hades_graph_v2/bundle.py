"""Deterministic graph-v2 chunk planning, private spooling, and resume checks."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
import copy
from dataclasses import dataclass
import gzip
import hashlib
import os
from pathlib import Path
import shutil
from typing import Any, cast
import zlib

try:  # pragma: no branch - exactly one native lock API exists on supported OSes
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

try:
    import msvcrt
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None  # type: ignore[assignment]

from .canonicalize import canonicalize_records
from .identity import canonical_json_bytes
from .model import GraphArtifactV2, artifact_from_payload, artifact_to_payload
from .schema import JsonValue, load_json_bytes, validate_schema
from .validation import validate_artifact


CHUNK_KINDS = (
    "entrypoints",
    "nodes",
    "structures",
    "edges",
    "flows",
    "flow_steps",
    "uncertainties",
)
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_WIRE_CHUNK_BYTES = 8 * 1024 * 1024
MAX_CHUNKS = 512
MAX_DECOMPRESSION_RATIO = 100
_RESUME_FIELDS = frozenset({
    "schema",
    "artifact_graph_version",
    "manifest_sha256",
    "uploaded_chunk_indexes",
})


class GraphBundleError(RuntimeError):
    """A deterministic local bundle failure that prevents publication."""

    code = "graph_bundle_invalid"

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(f"{self.code}: {message}")


class GraphRecoverableCapacityError(GraphBundleError):
    """A unit-local capacity failure for which whole-unit pruning is legal."""

    code = "resource_budget_reached"


class GraphUnitRecordTooLargeError(GraphRecoverableCapacityError):
    """One public chunk record cannot fit and its semantic unit must be rejected."""

    code = "record_too_large"


class GraphManifestCapacityError(GraphRecoverableCapacityError):
    """Record-derived descriptors make a prunable manifest exceed its ceiling."""


class GraphChunkCapacityError(GraphRecoverableCapacityError):
    """Chunk count, wire body, ratio, or total bytes require unit pruning."""


class GraphEnvelopeTooLargeError(GraphBundleError):
    """Required manifest metadata alone exceeds the hard envelope ceiling."""

    code = "graph_record_too_large"


@dataclass(frozen=True, slots=True)
class BundleLimits:
    max_chunk_uncompressed_bytes: int
    max_bundle_uncompressed_bytes: int
    max_chunks: int = MAX_CHUNKS
    backend_max_artifact_bytes: int | None = None
    backend_max_body_bytes: int | None = None

    def __post_init__(self) -> None:
        for name in (
            "max_chunk_uncompressed_bytes",
            "max_bundle_uncompressed_bytes",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if type(self.max_chunks) is not int or not 0 <= self.max_chunks <= MAX_CHUNKS:
            raise ValueError("max_chunks must be between 0 and 512")
        for name in ("backend_max_artifact_bytes", "backend_max_body_bytes"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 1):
                raise ValueError(f"{name} must be null or a positive integer")

    @property
    def chunk_ceiling(self) -> int:
        return min(self.max_chunk_uncompressed_bytes, MAX_WIRE_CHUNK_BYTES)

    @property
    def bundle_ceiling(self) -> int:
        if self.backend_max_artifact_bytes is None:
            return self.max_bundle_uncompressed_bytes
        return min(self.max_bundle_uncompressed_bytes, self.backend_max_artifact_bytes)

    @property
    def body_ceiling(self) -> int:
        if self.backend_max_body_bytes is None:
            return MAX_WIRE_CHUNK_BYTES
        return min(MAX_WIRE_CHUNK_BYTES, self.backend_max_body_bytes)


@dataclass(frozen=True, slots=True)
class BundleResumeState:
    spool: Path
    artifact_graph_version: str
    manifest: dict[str, Any]
    chunk_paths: tuple[Path, ...]
    uploaded_chunk_indexes: tuple[int, ...]
    missing_chunk_indexes: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class BundleManifest:
    spool: Path
    artifact_graph_version: str
    manifest: dict[str, Any]
    chunk_paths: tuple[Path, ...]

    def record_uploaded(self, index: int) -> None:
        GraphBundleWriter()._record_uploaded(self.spool, index)


@dataclass(frozen=True, slots=True)
class BundlePlan:
    manifest: dict[str, JsonValue]
    manifest_bytes: bytes
    chunks: tuple[bytes, ...]
    uncompressed_chunks: tuple[bytes, ...]
    logical_uncompressed_bytes: int


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = -1
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _deterministic_gzip(payload: bytes) -> bytes:
    compressed = bytearray(gzip.compress(payload, compresslevel=6, mtime=0))
    if len(compressed) < 18 or compressed[:4] != b"\x1f\x8b\x08\x00":
        raise GraphBundleError("deterministic gzip encoder produced an invalid member")
    compressed[9] = 255
    return bytes(compressed)


def _validate_gzip_header(payload: bytes) -> None:
    if (
        len(payload) < 18
        or payload[:4] != b"\x1f\x8b\x08\x00"
        or payload[4:8] != b"\x00\x00\x00\x00"
        or payload[9] != 255
    ):
        raise GraphBundleError(
            "graph chunk gzip header has optional fields or nondeterministic metadata"
        )


def _decode_single_gzip_member(payload: bytes, *, ceiling: int) -> bytes:
    _validate_gzip_header(payload)
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    output = bytearray()
    view = memoryview(payload)
    cursor = 0
    try:
        while cursor < len(view):
            block = view[cursor : cursor + 64 * 1024]
            cursor += len(block)
            remaining = ceiling + 1 - len(output)
            output.extend(decoder.decompress(block, remaining))
            if len(output) > ceiling or decoder.unconsumed_tail:
                raise GraphBundleError("graph chunk exceeds its uncompressed ceiling")
            if decoder.eof:
                break
        output.extend(decoder.flush())
    except zlib.error as exc:
        raise GraphBundleError("graph chunk gzip member is invalid") from exc
    if len(output) > ceiling:
        raise GraphBundleError("graph chunk exceeds its uncompressed ceiling")
    if not decoder.eof:
        raise GraphBundleError("graph chunk gzip member ended early")
    trailing = bytes(decoder.unused_data) + bytes(view[cursor:])
    if trailing:
        raise GraphBundleError(
            "graph chunk has trailing bytes or multiple gzip members"
        )
    if len(output) > len(payload) * MAX_DECOMPRESSION_RATIO:
        raise GraphBundleError("graph chunk exceeds the decompression ratio limit")
    return bytes(output)


def _canonical_artifact_payload(
    artifact: GraphArtifactV2 | Mapping[str, Any],
    *,
    validated: bool = False,
) -> dict[str, JsonValue]:
    if isinstance(artifact, GraphArtifactV2):
        if not validated:
            validate_artifact(artifact)
        return artifact_to_payload(artifact)
    elif isinstance(artifact, Mapping):
        payload = cast(dict[str, JsonValue], copy.deepcopy(dict(artifact)))
        for kind in ("frameworks", "languages", *CHUNK_KINDS):
            records = payload.get(kind)
            if isinstance(records, list):
                payload[kind] = cast(JsonValue, canonicalize_records(records))
        graph_contract = payload.get("graph_contract")
        if isinstance(graph_contract, dict):
            completeness = graph_contract.get("completeness")
            if isinstance(completeness, dict) and isinstance(
                completeness.get("languages"), list
            ):
                completeness["languages"] = canonicalize_records(
                    cast(list[dict[str, Any]], completeness["languages"])
                )
    else:
        raise TypeError("graph bundle artifact must be GraphArtifactV2 or a mapping")
    model = artifact_from_payload(payload)
    validate_artifact(model)
    return artifact_to_payload(model)


def _chunk_payload(index: int, kind: str, records: list[dict[str, JsonValue]]) -> bytes:
    return canonical_json_bytes({
        "schema": "hades.graph_chunk.v2",
        "index": index,
        "kind": kind,
        "records": records,
    })


def _chunk_payload_from_canonical_records(
    index: int, kind: str, records: list[bytes]
) -> bytes:
    """Assemble the exact JCS wrapper without re-encoding prior records."""

    empty = _chunk_payload(index, kind, [])
    marker = b'"records":[]'
    marker_at = empty.find(marker)
    if marker_at < 0:  # pragma: no cover - guards the local wrapper invariant
        raise AssertionError("canonical graph chunk wrapper has an unexpected shape")
    records_at = marker_at + len(b'"records":')
    return (
        empty[:records_at] + b"[" + b",".join(records) + b"]" + empty[records_at + 2 :]
    )


def record_fits_chunk(
    kind: str, record: Mapping[str, Any], limits: BundleLimits
) -> bool:
    """Return whether one public record plus its exact wrapper fits a chunk."""

    if kind not in CHUNK_KINDS:
        raise ValueError("unknown graph chunk kind")
    return (
        len(_chunk_payload(0, kind, [cast(dict[str, JsonValue], dict(record))]))
        <= limits.chunk_ceiling
    )


def _partition_records(
    artifact: Mapping[str, JsonValue], limits: BundleLimits
) -> list[tuple[str, list[dict[str, JsonValue]], bytes]]:
    partitions: list[tuple[str, list[dict[str, JsonValue]], bytes]] = []
    next_index = 0
    for kind in CHUNK_KINDS:
        raw_records = artifact[kind]
        if not isinstance(raw_records, list):
            raise GraphBundleError(f"artifact {kind} must be an array")
        records = sorted(raw_records, key=lambda record: cast(dict, record)["id"])
        current: list[dict[str, JsonValue]] = []
        current_bytes: list[bytes] = []
        current_size = len(_chunk_payload(next_index, kind, []))
        for raw_record in records:
            if not isinstance(raw_record, dict):
                raise GraphBundleError(f"artifact {kind} contains a non-object")
            record = cast(dict[str, JsonValue], raw_record)
            record_bytes = canonical_json_bytes(record)
            separator_size = 1 if current else 0
            candidate_size = current_size + separator_size + len(record_bytes)
            if candidate_size <= limits.chunk_ceiling:
                current.append(record)
                current_bytes.append(record_bytes)
                current_size = candidate_size
                continue
            if not current:
                raise GraphUnitRecordTooLargeError(
                    f"{kind} record does not fit one chunk"
                )
            partitions.append((
                kind,
                current,
                _chunk_payload_from_canonical_records(next_index, kind, current_bytes),
            ))
            next_index += 1
            current = [record]
            current_bytes = [record_bytes]
            current_size = len(_chunk_payload(next_index, kind, [])) + len(record_bytes)
            if current_size > limits.chunk_ceiling:
                raise GraphUnitRecordTooLargeError(
                    f"{kind} record does not fit one chunk"
                )
        if current:
            partitions.append((
                kind,
                current,
                _chunk_payload_from_canonical_records(next_index, kind, current_bytes),
            ))
            next_index += 1
    if len(partitions) > limits.max_chunks:
        raise GraphChunkCapacityError(
            "graph bundle exceeds the configured chunk-count limit"
        )
    return partitions


def build_bundle_plan(
    artifact: GraphArtifactV2 | Mapping[str, Any],
    limits: BundleLimits,
    *,
    enforce_total: bool = True,
    _validated: bool = False,
) -> BundlePlan:
    """Serialize the exact manifest/chunk candidate without filesystem effects."""

    payload = _canonical_artifact_payload(artifact, validated=_validated)
    partitions = _partition_records(payload, limits)
    descriptors: list[dict[str, JsonValue]] = []
    compressed_chunks: list[bytes] = []
    raw_chunks: list[bytes] = []
    for index, (kind, records, raw) in enumerate(partitions):
        compressed = _deterministic_gzip(raw)
        if len(compressed) > limits.body_ceiling:
            raise GraphChunkCapacityError(
                "compressed graph chunk exceeds the wire body limit"
            )
        if len(raw) > len(compressed) * MAX_DECOMPRESSION_RATIO:
            raise GraphChunkCapacityError(
                "graph chunk exceeds the decompression ratio limit"
            )
        descriptors.append({
            "index": index,
            "kind": kind,
            "record_count": len(records),
            "sha256": _sha256(raw),
            "uncompressed_bytes": len(raw),
            "compression": "gzip",
            "compressed_sha256": _sha256(compressed),
            "compressed_bytes": len(compressed),
        })
        compressed_chunks.append(compressed)
        raw_chunks.append(raw)

    graph_contract = payload["graph_contract"]
    if not isinstance(graph_contract, dict):
        raise GraphBundleError("graph artifact contract metadata is invalid")
    manifest: dict[str, JsonValue] = {
        "schema": "hades.graph_bundle.v2",
        "artifact_schema": payload["schema"],
        "artifact_graph_version": graph_contract["artifact_graph_version"],
        "generated_at": payload["generated_at"],
        "source": payload["source"],
        "project": payload["project"],
        "graph_contract": graph_contract,
        "frameworks": payload["frameworks"],
        "languages": payload["languages"],
        "counts": {
            "frameworks": len(cast(list, payload["frameworks"])),
            "languages": len(cast(list, payload["languages"])),
            **{kind: len(cast(list, payload[kind])) for kind in CHUNK_KINDS},
        },
        "chunks": descriptors,
    }
    validate_schema("bundle.schema.json", manifest)
    manifest_bytes = canonical_json_bytes(manifest)
    if len(manifest_bytes) > MAX_MANIFEST_BYTES:
        envelope = copy.deepcopy(manifest)
        envelope["counts"] = {
            "frameworks": len(cast(list, payload["frameworks"])),
            "languages": len(cast(list, payload["languages"])),
            **{kind: 0 for kind in CHUNK_KINDS},
        }
        envelope["chunks"] = []
        if len(canonical_json_bytes(envelope)) > MAX_MANIFEST_BYTES:
            raise GraphEnvelopeTooLargeError(
                "required graph bundle manifest metadata exceeds 4 MiB"
            )
        raise GraphManifestCapacityError(
            "record-derived graph bundle manifest exceeds 4 MiB"
        )
    total = len(manifest_bytes) + sum(len(raw) for raw in raw_chunks)
    if enforce_total and total > limits.bundle_ceiling:
        raise GraphChunkCapacityError(
            "graph bundle exceeds the configured total-byte limit"
        )
    return BundlePlan(
        manifest,
        manifest_bytes,
        tuple(compressed_chunks),
        tuple(raw_chunks),
        total,
    )


@contextmanager
def _exclusive_lock(spool: Path, *, blocking: bool = True) -> Iterator[None]:
    spool.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(spool, 0o700)
    path = spool / ".lock"
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    os.chmod(path, 0o600)
    locked = False
    try:
        if fcntl is not None:
            operation = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
            fcntl.flock(descriptor, operation)
        elif msvcrt is not None:
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b" ")
                os.fsync(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
            operation = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
            msvcrt.locking(descriptor, operation, 1)
        else:  # pragma: no cover - unsupported Python platform
            raise GraphBundleError("no native exclusive file-lock API is available")
        locked = True
        yield
    finally:
        try:
            if locked and fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            elif locked and msvcrt is not None:
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        finally:
            os.close(descriptor)


def _resume_payload(
    artifact_graph_version: str,
    manifest_sha256: str,
    uploaded: list[int],
) -> dict[str, JsonValue]:
    return {
        "schema": "hades.graph_bundle_resume.v1",
        "artifact_graph_version": artifact_graph_version,
        "manifest_sha256": manifest_sha256,
        "uploaded_chunk_indexes": uploaded,
    }


def _load_canonical_object(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        value = load_json_bytes(raw)
    except (OSError, ValueError) as exc:
        raise GraphBundleError(f"graph bundle {label} is invalid") from exc
    if not isinstance(value, dict) or canonical_json_bytes(value) != raw:
        raise GraphBundleError(f"graph bundle {label} is not canonical JCS")
    return cast(dict[str, Any], value), raw


class GraphBundleWriter:
    """Freeze validated bytes and verify that retries never reserialize them."""

    def lock(self, spool: Path):
        return _exclusive_lock(Path(spool))

    def write(
        self,
        artifact: GraphArtifactV2 | Mapping[str, Any],
        spool: Path,
        limits: BundleLimits,
    ) -> BundleManifest:
        spool = Path(spool)
        with _exclusive_lock(spool):
            plan = build_bundle_plan(artifact, limits)
            paths: list[Path] = []
            for index, compressed in enumerate(plan.chunks):
                path = spool / f"chunk-{index:05d}.gz"
                _atomic_write(path, compressed)
                paths.append(path)
            _atomic_write(spool / "manifest.json", plan.manifest_bytes)
            version = cast(str, plan.manifest["artifact_graph_version"])
            _atomic_write(
                spool / "resume.json",
                canonical_json_bytes(
                    _resume_payload(version, _sha256(plan.manifest_bytes), [])
                ),
            )
        return BundleManifest(spool, version, dict(plan.manifest), tuple(paths))

    def _resume_state_unlocked(self, spool: Path) -> BundleResumeState:
        resume, _ = _load_canonical_object(spool / "resume.json", "resume metadata")
        if frozenset(resume) != _RESUME_FIELDS or resume.get("schema") != (
            "hades.graph_bundle_resume.v1"
        ):
            raise GraphBundleError("graph bundle resume metadata is invalid")
        manifest, manifest_raw = _load_canonical_object(
            spool / "manifest.json", "manifest"
        )
        if resume.get("manifest_sha256") != _sha256(manifest_raw):
            raise GraphBundleError("graph bundle manifest changed after digest")
        try:
            validate_schema("bundle.schema.json", manifest)
        except ValueError as exc:
            raise GraphBundleError("graph bundle manifest is invalid") from exc
        descriptors = manifest["chunks"]
        if not isinstance(descriptors, list):
            raise GraphBundleError("graph bundle descriptors are invalid")
        if [row.get("index") for row in descriptors] != list(range(len(descriptors))):
            raise GraphBundleError("graph bundle descriptor indexes are not contiguous")
        kind_ordinals = [
            CHUNK_KINDS.index(cast(str, row["kind"])) for row in descriptors
        ]
        if kind_ordinals != sorted(kind_ordinals):
            raise GraphBundleError("graph bundle descriptor kinds are out of order")

        chunk_paths: list[Path] = []
        count_by_kind = {kind: 0 for kind in CHUNK_KINDS}
        last_id_by_kind: dict[str, str] = {}
        for descriptor in descriptors:
            index = cast(int, descriptor["index"])
            path = spool / f"chunk-{index:05d}.gz"
            try:
                compressed = path.read_bytes()
            except OSError as exc:
                raise GraphBundleError("graph bundle chunk is missing") from exc
            if (
                len(compressed) != descriptor["compressed_bytes"]
                or _sha256(compressed) != descriptor["compressed_sha256"]
            ):
                raise GraphBundleError("graph bundle compressed digest mismatch")
            raw = _decode_single_gzip_member(
                compressed,
                ceiling=min(
                    cast(int, descriptor["uncompressed_bytes"]),
                    MAX_WIRE_CHUNK_BYTES,
                ),
            )
            if (
                len(raw) != descriptor["uncompressed_bytes"]
                or _sha256(raw) != (descriptor["sha256"])
            ):
                raise GraphBundleError("graph bundle uncompressed digest mismatch")
            try:
                chunk = load_json_bytes(raw)
                validate_schema("chunk.schema.json", chunk)
            except ValueError as exc:
                raise GraphBundleError(
                    "graph bundle chunk contract is invalid"
                ) from exc
            if not isinstance(chunk, dict) or canonical_json_bytes(chunk) != raw:
                raise GraphBundleError("graph bundle chunk is not exact JCS")
            if chunk["index"] != index or chunk["kind"] != descriptor["kind"]:
                raise GraphBundleError("graph bundle descriptor does not match chunk")
            records = cast(list[dict[str, Any]], chunk["records"])
            ids = [cast(str, record["id"]) for record in records]
            kind = cast(str, descriptor["kind"])
            if (
                len(records) != descriptor["record_count"]
                or ids != sorted(set(ids))
                or (ids and kind in last_id_by_kind and last_id_by_kind[kind] >= ids[0])
            ):
                raise GraphBundleError("graph bundle chunk record order is invalid")
            if ids:
                last_id_by_kind[kind] = ids[-1]
            count_by_kind[kind] += len(records)
            chunk_paths.append(path)
        if any(
            manifest["counts"][kind] != count for kind, count in count_by_kind.items()
        ):
            raise GraphBundleError("graph bundle manifest record counts do not close")

        version = manifest["artifact_graph_version"]
        if resume.get("artifact_graph_version") != version:
            raise GraphBundleError(
                "graph bundle resume identity does not match manifest"
            )
        uploaded = resume.get("uploaded_chunk_indexes")
        if (
            not isinstance(uploaded, list)
            or any(type(index) is not int for index in uploaded)
            or uploaded != sorted(set(uploaded))
            or any(not 0 <= index < len(chunk_paths) for index in uploaded)
        ):
            raise GraphBundleError("graph bundle uploaded-chunk state is invalid")
        return BundleResumeState(
            spool,
            cast(str, version),
            manifest,
            tuple(chunk_paths),
            tuple(uploaded),
            tuple(index for index in range(len(chunk_paths)) if index not in uploaded),
        )

    def resume_state(self, spool: Path) -> BundleResumeState:
        spool = Path(spool)
        with _exclusive_lock(spool):
            return self._resume_state_unlocked(spool)

    def _record_uploaded(self, spool: Path, index: int) -> None:
        spool = Path(spool)
        with _exclusive_lock(spool):
            state = self._resume_state_unlocked(spool)
            if type(index) is not int or not 0 <= index < len(state.chunk_paths):
                raise ValueError("uploaded graph chunk index is outside the manifest")
            uploaded = sorted({*state.uploaded_chunk_indexes, index})
            manifest_raw = (spool / "manifest.json").read_bytes()
            _atomic_write(
                spool / "resume.json",
                canonical_json_bytes(
                    _resume_payload(
                        state.artifact_graph_version,
                        _sha256(manifest_raw),
                        uploaded,
                    )
                ),
            )

    def delete(self, spool: Path, *, outcome: str) -> None:
        if outcome not in {"published", "canceled"}:
            raise ValueError("spool deletion requires published or canceled outcome")
        spool = Path(spool)
        if not spool.exists():
            return
        with _exclusive_lock(spool):
            shutil.rmtree(spool)

    def cleanup_stale(
        self,
        root: Path,
        *,
        ttl_seconds: int,
        now: float,
    ) -> tuple[Path, ...]:
        if type(ttl_seconds) is not int or ttl_seconds < 1:
            raise ValueError("spool cleanup TTL must be a positive integer")
        root = Path(root)
        if not root.exists():
            return ()
        removed: list[Path] = []
        candidates = sorted(
            {
                path.parent
                for pattern in (
                    ".lock",
                    "manifest.json",
                    "resume.json",
                    "chunk-*.gz",
                    ".chunk-*.tmp",
                )
                for path in root.rglob(pattern)
            },
            key=lambda path: path.as_posix(),
        )
        for spool in candidates:
            try:
                age = now - spool.stat().st_mtime
            except OSError:
                continue
            if age <= ttl_seconds:
                continue
            try:
                with _exclusive_lock(spool, blocking=False):
                    shutil.rmtree(spool)
            except (BlockingIOError, FileNotFoundError):
                continue
            removed.append(spool)
        return tuple(removed)


__all__ = [
    "BundleLimits",
    "BundleManifest",
    "BundlePlan",
    "BundleResumeState",
    "CHUNK_KINDS",
    "GraphBundleError",
    "GraphBundleWriter",
    "GraphChunkCapacityError",
    "GraphEnvelopeTooLargeError",
    "GraphManifestCapacityError",
    "GraphRecoverableCapacityError",
    "GraphUnitRecordTooLargeError",
    "build_bundle_plan",
    "record_fits_chunk",
]
