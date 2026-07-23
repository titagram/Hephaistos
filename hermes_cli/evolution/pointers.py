"""Verified baseline pointer documents for immutable evolution generations."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import stat
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Mapping

import hermes_constants
from hermes_cli.profiles import get_active_profile_name

from .contract import canonical_json_bytes, content_digest, require_digest
from .ledger import (
    EvolutionLedger,
    EvolutionLedgerError,
    LifecycleEvent,
    StoredEvent,
)
from .store import (
    GenerationStore,
    PublishedGeneration,
    VerifiedManifestDescriptor,
)


_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z\Z",
    re.ASCII,
)
_PROFILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z", re.ASCII)
_FIELDS = frozenset(
    {
        "schema_version",
        "profile_id",
        "generation_id",
        "manifest_digest",
        "lifecycle_sequence",
        "designated_at",
        "ledger_event_digest",
        "integrity_digest",
    }
)
_PAYLOAD_FIELDS = _FIELDS - {"integrity_digest"}
_MAX_POINTER_BYTES = 64 * 1024


class PointerError(ValueError):
    """A pointer is malformed, unsafe, or does not bind to its evidence."""


def _fail() -> None:
    raise PointerError("invalid_evolution_pointer")


def _timestamp(value: object) -> str:
    if not isinstance(value, str) or _TIMESTAMP.fullmatch(value) is None:
        _fail()
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    except (ValueError, OverflowError):
        _fail()
    if parsed.strftime("%Y-%m-%dT%H:%M:%S.%fZ") != value:
        _fail()
    return value


def _profile(value: object) -> str:
    if not isinstance(value, str) or _PROFILE.fullmatch(value) is None:
        _fail()
    return value


def _active_profile() -> str:
    """Return the canonical profile name without exposing a custom home path."""

    active = get_active_profile_name()
    resolved_home = hermes_constants.get_hermes_home().resolve()
    platform_default = (
        hermes_constants._get_platform_default_hermes_home().resolve()
    )
    if active not in {"default", "custom"}:
        return _profile(active)
    if active == "default" and resolved_home == platform_default:
        return "default"
    digest = content_digest(
        {"resolved_hermes_home": str(resolved_home)},
        domain="hades-evolution-custom-profile-v1",
    )
    return _profile(f"custom-{digest[:56]}")


def _positive_integer(value: object) -> int:
    if type(value) is not int or value < 1:
        _fail()
    return value


def _profile_digest(profile_id: str) -> str:
    return content_digest(
        {"profile_id": profile_id},
        domain="hades-evolution-profile-v1",
    )


def _event_input_digests(
    generation_id: str,
    manifest_digest: str,
    profile_id: str,
) -> tuple[str, ...]:
    return (
        generation_id,
        manifest_digest,
        _profile_digest(profile_id),
    )


@dataclass(frozen=True)
class PointerDocument:
    schema_version: int
    profile_id: str
    generation_id: str
    manifest_digest: str
    lifecycle_sequence: int
    designated_at: str
    ledger_event_digest: str
    integrity_digest: str

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "profile_id": self.profile_id,
            "generation_id": self.generation_id,
            "manifest_digest": self.manifest_digest,
            "lifecycle_sequence": self.lifecycle_sequence,
            "designated_at": self.designated_at,
            "ledger_event_digest": self.ledger_event_digest,
            "integrity_digest": self.integrity_digest,
        }


def _payload(document: Mapping[str, object]) -> dict[str, object]:
    if set(document) != _PAYLOAD_FIELDS:
        _fail()
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        _fail()
    return {
        "schema_version": 1,
        "profile_id": _profile(document["profile_id"]),
        "generation_id": require_digest(document["generation_id"]),
        "manifest_digest": require_digest(document["manifest_digest"]),
        "lifecycle_sequence": _positive_integer(
            document["lifecycle_sequence"]
        ),
        "designated_at": _timestamp(document["designated_at"]),
        "ledger_event_digest": require_digest(
            document["ledger_event_digest"]
        ),
    }


def pointer_integrity_digest(
    payload: Mapping[str, object],
    event_digest: str,
) -> str:
    """Return the schema-v1 integrity digest for a closed pointer payload."""

    normalized = _payload(payload)
    digest = require_digest(event_digest)
    if normalized["ledger_event_digest"] != digest:
        _fail()
    return hashlib.sha256(
        b"hades-evolution-pointer-v1\0"
        + canonical_json_bytes(normalized)
        + digest.encode("ascii")
    ).hexdigest()


def _document(value: Mapping[str, object]) -> PointerDocument:
    try:
        if set(value) != _FIELDS:
            _fail()
        payload = _payload(
            {key: value[key] for key in _PAYLOAD_FIELDS}
        )
        integrity = require_digest(value["integrity_digest"])
        expected = pointer_integrity_digest(
            payload,
            str(payload["ledger_event_digest"]),
        )
    except (KeyError, TypeError, ValueError):
        _fail()
    if integrity != expected:
        _fail()
    return PointerDocument(**payload, integrity_digest=integrity)


def _event_at(
    ledger: EvolutionLedger,
    sequence: int,
) -> StoredEvent | None:
    events = ledger.history(limit=1, after=sequence - 1)
    return (
        events[0]
        if events and events[0].event_sequence == sequence
        else None
    )


def _event_matches(
    event: StoredEvent,
    *,
    generation_id: str,
    manifest_digest: str,
    profile_id: str,
) -> bool:
    return (
        event.event_type == "baseline_designated"
        and event.attempt_id is None
        and event.generation_id is None
        and event.prior_state is None
        and event.next_state is None
        and event.actor == "system"
        and event.input_digests
        == _event_input_digests(
            generation_id,
            manifest_digest,
            profile_id,
        )
        and event.authorization_id is None
        and event.reason_code == "baseline"
        and event.reason_summary == "baseline designation"
    )


def validate_pointer(
    document: Mapping[str, object],
    ledger: EvolutionLedger,
    store: GenerationStore,
) -> PointerDocument:
    """Validate a pointer against the complete chain and immutable generation."""

    try:
        if ledger.connection.in_transaction:
            _fail()
        pointer = _document(document)
        if pointer.profile_id != _active_profile():
            _fail()
        if ledger.verify_chain():
            _fail()
        event = _event_at(ledger, pointer.lifecycle_sequence)
        if (
            event is None
            or event.event_digest != pointer.ledger_event_digest
            or event.created_at != pointer.designated_at
            or not _event_matches(
                event,
                generation_id=pointer.generation_id,
                manifest_digest=pointer.manifest_digest,
                profile_id=pointer.profile_id,
            )
        ):
            _fail()
        if ledger.prove_committed_event(event) != event:
            _fail()
        descriptor = store.verified_manifest_descriptor(
            pointer.generation_id
        )
        if descriptor.manifest_digest != pointer.manifest_digest:
            _fail()
    except (
        EvolutionLedgerError,
        PointerError,
        sqlite3.Error,
        ValueError,
        OSError,
    ):
        raise PointerError("invalid_evolution_pointer") from None
    return pointer


def _same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _validate_parent_info(info: os.stat_result) -> None:
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
        or (
            hasattr(os, "geteuid")
            and info.st_uid != os.geteuid()
        )
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        _fail()


def _validate_target_info(info: os.stat_result) -> None:
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or (
            hasattr(os, "geteuid")
            and info.st_uid != os.geteuid()
        )
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        _fail()


def _open_parent(path: Path) -> int:
    if not path.name or path.name in {".", ".."}:
        _fail()
    try:
        linked = path.parent.lstat()
        _validate_parent_info(linked)
        descriptor = os.open(
            path.parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        opened = os.fstat(descriptor)
        if not _same_inode(linked, opened):
            os.close(descriptor)
            _fail()
        _validate_parent_info(opened)
        return descriptor
    except PointerError:
        raise
    except (OSError, TypeError, NotImplementedError, AttributeError):
        _fail()
    raise AssertionError("unreachable")


def _require_linked_parent(path: Path, descriptor: int) -> None:
    try:
        linked = path.parent.lstat()
        opened = os.fstat(descriptor)
        if not _same_inode(linked, opened):
            _fail()
        _validate_parent_info(linked)
        _validate_parent_info(opened)
    except PointerError:
        raise
    except (OSError, TypeError, NotImplementedError):
        _fail()


def _open_existing_target(
    parent_descriptor: int,
    name: str,
) -> int | None:
    try:
        try:
            linked = os.stat(
                name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return None
        _validate_target_info(linked)
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=parent_descriptor,
        )
        opened = os.fstat(descriptor)
        if not _same_inode(linked, opened):
            os.close(descriptor)
            _fail()
        _validate_target_info(opened)
        relinked = os.stat(
            name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if not _same_inode(opened, relinked):
            os.close(descriptor)
            _fail()
        return descriptor
    except PointerError:
        raise
    except (OSError, TypeError, NotImplementedError):
        _fail()
    raise AssertionError("unreachable")


def _write_all(descriptor: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            _fail()
        view = view[written:]


def atomic_write_pointer(path: Path, document: PointerDocument) -> None:
    """Durably replace a pointer without exposing a partial document."""

    path = Path(path)
    try:
        data = canonical_json_bytes(
            _document(document.to_mapping()).to_mapping()
        )
        parent_descriptor = _open_parent(path)
    except (PointerError, ValueError, OSError):
        raise PointerError("pointer_write_failed") from None

    temporary_name: str | None = None
    temporary_descriptor: int | None = None
    try:
        existing = _open_existing_target(parent_descriptor, path.name)
        if existing is not None:
            os.close(existing)
        for _ in range(16):
            candidate = f".{path.name}.{secrets.token_hex(16)}"
            try:
                temporary_descriptor = os.open(
                    candidate,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=parent_descriptor,
                )
            except FileExistsError:
                continue
            temporary_name = candidate
            break
        if temporary_descriptor is None or temporary_name is None:
            _fail()
        os.fchmod(temporary_descriptor, 0o600)
        temporary_info = os.fstat(temporary_descriptor)
        _validate_target_info(temporary_info)
        _write_all(temporary_descriptor, data)
        os.fsync(temporary_descriptor)
        os.close(temporary_descriptor)
        temporary_descriptor = None
        _require_linked_parent(path, parent_descriptor)
        os.replace(
            temporary_name,
            path.name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
        )
        temporary_name = None
        os.fsync(parent_descriptor)
    except (OSError, PointerError, TypeError, NotImplementedError):
        raise PointerError("pointer_write_failed") from None
    finally:
        if temporary_descriptor is not None:
            try:
                os.close(temporary_descriptor)
            except OSError:
                pass
        if temporary_name is not None:
            try:
                os.unlink(
                    temporary_name,
                    dir_fd=parent_descriptor,
                )
            except FileNotFoundError:
                pass
            except OSError:
                pass
        os.close(parent_descriptor)


def _read_pointer(path: Path) -> Mapping[str, object] | None:
    path = Path(path)
    parent_descriptor: int | None = None
    descriptor: int | None = None
    try:
        parent_descriptor = _open_parent(path)
        descriptor = _open_existing_target(
            parent_descriptor,
            path.name,
        )
        if descriptor is None:
            return None
        chunks: list[bytes] = []
        size = 0
        while chunk := os.read(descriptor, 16 * 1024):
            size += len(chunk)
            if size > _MAX_POINTER_BYTES:
                _fail()
            chunks.append(chunk)
        data = b"".join(chunks)
        value = json.loads(data)
        if not isinstance(value, dict):
            _fail()
        if canonical_json_bytes(value) != data:
            _fail()
        _require_linked_parent(path, parent_descriptor)
        return value
    except (
        PointerError,
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ):
        _fail()
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if parent_descriptor is not None:
            os.close(parent_descriptor)
    raise AssertionError("unreachable")


def _new_document(
    event: StoredEvent,
    descriptor: VerifiedManifestDescriptor,
    profile_id: str,
) -> PointerDocument:
    payload: dict[str, object] = {
        "schema_version": 1,
        "profile_id": profile_id,
        "generation_id": descriptor.generation.generation_id,
        "manifest_digest": descriptor.manifest_digest,
        "lifecycle_sequence": event.event_sequence,
        "designated_at": event.created_at,
        "ledger_event_digest": event.event_digest,
    }
    return PointerDocument(
        **payload,
        integrity_digest=pointer_integrity_digest(
            payload,
            event.event_digest,
        ),
    )


def _history(ledger: EvolutionLedger) -> list[StoredEvent]:
    result: list[StoredEvent] = []
    after = 0
    while True:
        page = ledger.history(limit=1000, after=after)
        if not page:
            return result
        result.extend(page)
        after = page[-1].event_sequence


def _baseline_event(
    ledger: EvolutionLedger,
    descriptor: VerifiedManifestDescriptor,
    profile_id: str,
) -> StoredEvent | None:
    matches = [
        event
        for event in _history(ledger)
        if event.event_type == "baseline_designated"
    ]
    if not matches:
        return None
    if len(matches) != 1:
        _fail()
    event = matches[0]
    if not _event_matches(
        event,
        generation_id=descriptor.generation.generation_id,
        manifest_digest=descriptor.manifest_digest,
        profile_id=profile_id,
    ):
        _fail()
    return event


def _verified_baseline(
    store: GenerationStore,
    baseline: PublishedGeneration,
) -> VerifiedManifestDescriptor:
    descriptor = store.verified_manifest_descriptor(
        baseline.generation_id
    )
    if descriptor.generation != baseline:
        _fail()
    return descriptor


def initialize_baseline_pointers(
    ledger: EvolutionLedger,
    store: GenerationStore,
    baseline: PublishedGeneration,
) -> tuple[PointerDocument, PointerDocument]:
    """Create exactly one baseline designation and its verified pointer views."""

    try:
        if ledger.connection.in_transaction:
            _fail()
        descriptor = _verified_baseline(store, baseline)
        profile_id = _active_profile()
        root = store.root.parent
        active_path = root / "active.json"
        lkg_path = root / "last-known-good.json"

        existing_active = _read_pointer(active_path)
        existing_lkg = _read_pointer(lkg_path)
        active = (
            None
            if existing_active is None
            else validate_pointer(existing_active, ledger, store)
        )
        lkg = (
            None
            if existing_lkg is None
            else validate_pointer(existing_lkg, ledger, store)
        )

        if active is not None or lkg is not None:
            coherent = active or lkg
            if coherent is None:
                raise AssertionError("unreachable")
            if active is not None and lkg is not None and active != lkg:
                _fail()
            if (
                coherent.profile_id != profile_id
                or coherent.generation_id
                != descriptor.generation.generation_id
                or coherent.manifest_digest != descriptor.manifest_digest
            ):
                _fail()
            event = _baseline_event(ledger, descriptor, profile_id)
            if (
                event is None
                or coherent.lifecycle_sequence != event.event_sequence
                or coherent.ledger_event_digest != event.event_digest
                or coherent.designated_at != event.created_at
            ):
                _fail()
            if ledger.prove_committed_event(event) != event:
                _fail()
            if active is None:
                atomic_write_pointer(active_path, coherent)
                active = coherent
            if lkg is None:
                atomic_write_pointer(lkg_path, coherent)
                lkg = coherent
            return active, lkg

        if ledger.verify_chain():
            _fail()
        event = _baseline_event(ledger, descriptor, profile_id)
        if event is None:
            timestamp = datetime.now(UTC).strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ"
            )
            event = ledger.append_event(
                LifecycleEvent(
                    event_id=str(uuid.uuid4()),
                    attempt_id=None,
                    generation_id=None,
                    event_type="baseline_designated",
                    prior_state=None,
                    next_state=None,
                    actor="system",
                    input_digests=_event_input_digests(
                        descriptor.generation.generation_id,
                        descriptor.manifest_digest,
                        profile_id,
                    ),
                    authorization_id=None,
                    reason_code="baseline",
                    reason_summary="baseline designation",
                    created_at=timestamp,
                )
            )
        if ledger.prove_committed_event(event) != event:
            _fail()
        pointer = _new_document(event, descriptor, profile_id)
        atomic_write_pointer(active_path, pointer)
        atomic_write_pointer(lkg_path, pointer)
        return pointer, pointer
    except (
        EvolutionLedgerError,
        PointerError,
        sqlite3.Error,
        ValueError,
        OSError,
    ):
        raise PointerError(
            "baseline_pointer_initialization_failed"
        ) from None
