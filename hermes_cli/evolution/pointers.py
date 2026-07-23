"""Verified baseline pointer documents for immutable evolution generations."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Mapping

from .contract import canonical_json_bytes, content_digest, require_digest, sha256_digest
from .ledger import EvolutionLedger, LifecycleEvent, StoredEvent
from .store import GenerationStore, PublishedGeneration


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


class PointerError(ValueError):
    """A pointer is malformed, unsafe, or does not bind to its evidence."""


def _fail() -> None:
    raise PointerError("invalid_evolution_pointer")


def _timestamp(value: object) -> str:
    if not isinstance(value, str) or _TIMESTAMP.fullmatch(value) is None:
        _fail()
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    except (ValueError, OverflowError):
        _fail()
    return value


def _profile(value: object) -> str:
    if not isinstance(value, str) or _PROFILE.fullmatch(value) is None:
        _fail()
    return value


def _active_profile() -> str:
    return _profile(os.environ.get("HERMES_PROFILE", "default"))


def _positive_integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        _fail()
    return value


def _profile_digest(profile_id: str) -> str:
    return content_digest({"profile_id": profile_id}, domain="hades-evolution-profile-v1")


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
    if set(document) != _FIELDS:
        _fail()
    if document["schema_version"] != 1 or isinstance(document["schema_version"], bool):
        _fail()
    return {
        "schema_version": 1,
        "profile_id": _profile(document["profile_id"]),
        "generation_id": require_digest(document["generation_id"]),
        "manifest_digest": require_digest(document["manifest_digest"]),
        "lifecycle_sequence": _positive_integer(document["lifecycle_sequence"]),
        "designated_at": _timestamp(document["designated_at"]),
        "ledger_event_digest": require_digest(document["ledger_event_digest"]),
    }


def pointer_integrity_digest(payload: Mapping[str, object], event_digest: str) -> str:
    """Return the schema-v1 integrity digest for a closed pointer payload."""

    normalized = _payload({**dict(payload), "integrity_digest": "0" * 64})
    digest = require_digest(event_digest)
    return hashlib.sha256(
        b"hades-evolution-pointer-v1\0"
        + canonical_json_bytes(normalized)
        + digest.encode("ascii")
    ).hexdigest()


def _document(value: Mapping[str, object]) -> PointerDocument:
    try:
        payload = _payload(value)
        integrity = require_digest(value["integrity_digest"])
    except (KeyError, TypeError, ValueError):
        _fail()
    expected = pointer_integrity_digest(payload, payload["ledger_event_digest"])
    if integrity != expected:
        _fail()
    return PointerDocument(**payload, integrity_digest=integrity)


def _manifest_digest(generation: PublishedGeneration) -> str:
    return sha256_digest(canonical_json_bytes(dict(generation.manifest)))


def _event_at(ledger: EvolutionLedger, sequence: int) -> StoredEvent | None:
    events = ledger.history(limit=1, after=sequence - 1)
    return events[0] if events and events[0].event_sequence == sequence else None


def validate_pointer(
    document: Mapping[str, object], ledger: EvolutionLedger, store: GenerationStore
) -> PointerDocument:
    """Validate a pointer against the complete chain and immutable generation."""

    try:
        pointer = _document(document)
        if pointer.profile_id != _active_profile():
            _fail()
        if ledger.verify_chain():
            _fail()
        event = _event_at(ledger, pointer.lifecycle_sequence)
        if event is None:
            _fail()
        if (
            event.event_digest != pointer.ledger_event_digest
            or event.event_type != "baseline_designated"
            or event.generation_id != pointer.generation_id
            or event.created_at != pointer.designated_at
            or pointer.manifest_digest not in event.input_digests
            or _profile_digest(pointer.profile_id) not in event.input_digests
        ):
            _fail()
        generation = store.verify(pointer.generation_id)
        if _manifest_digest(generation) != pointer.manifest_digest:
            _fail()
    except (PointerError, ValueError, OSError):
        raise PointerError("invalid_evolution_pointer") from None
    return pointer


def _validate_parent(path: Path) -> None:
    try:
        info = path.parent.lstat()
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISDIR(info.st_mode)
            or (hasattr(os, "geteuid") and info.st_uid != os.geteuid())
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            _fail()
    except OSError:
        _fail()


def atomic_write_pointer(path: Path, document: PointerDocument) -> None:
    """Durably replace a pointer without exposing a partial document."""

    path = Path(path)
    _validate_parent(path)
    try:
        if path.exists() or path.is_symlink():
            target = path.lstat()
            if (
                stat.S_ISLNK(target.st_mode)
                or not stat.S_ISREG(target.st_mode)
                or target.st_uid != os.geteuid()
                or stat.S_IMODE(target.st_mode) != 0o600
            ):
                _fail()
    except OSError:
        _fail()
    data = canonical_json_bytes(_document(document.to_mapping()).to_mapping())
    temporary: Path | None = None
    descriptor: int | None = None
    try:
        for _ in range(16):
            temporary = path.parent / f".{path.name}.{secrets.token_hex(16)}"
            try:
                descriptor = os.open(
                    temporary,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                )
                break
            except FileExistsError:
                temporary = None
        if descriptor is None:
            _fail()
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                _fail()
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary, path)
        temporary = None
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except (OSError, PointerError):
        raise PointerError("pointer_write_failed") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def _read_pointer(path: Path) -> Mapping[str, object] | None:
    try:
        if not path.exists():
            return None
        if path.is_symlink():
            _fail()
        data = path.read_bytes()
        value = json.loads(data)
        if not isinstance(value, dict):
            _fail()
        return value
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _fail()


def _new_document(
    event: StoredEvent, baseline: PublishedGeneration, profile_id: str
) -> PointerDocument:
    payload: dict[str, object] = {
        "schema_version": 1,
        "profile_id": profile_id,
        "generation_id": baseline.generation_id,
        "manifest_digest": _manifest_digest(baseline),
        "lifecycle_sequence": event.event_sequence,
        "designated_at": event.created_at,
        "ledger_event_digest": event.event_digest,
    }
    return PointerDocument(
        **payload,
        integrity_digest=pointer_integrity_digest(payload, event.event_digest),
    )


def _baseline_event(
    ledger: EvolutionLedger, baseline: PublishedGeneration, profile_id: str
) -> StoredEvent | None:
    manifest_digest = _manifest_digest(baseline)
    profile_digest = _profile_digest(profile_id)
    for event in ledger.history(limit=1000):
        if event.event_type == "baseline_designated" and event.generation_id == baseline.generation_id:
            if manifest_digest in event.input_digests and profile_digest in event.input_digests:
                return event
            _fail()
    return None


def _ensure_baseline_generation(
    ledger: EvolutionLedger, baseline: PublishedGeneration
) -> None:
    """Register the immutable baseline before its FK-bound lifecycle event."""

    manifest_digest = _manifest_digest(baseline)
    row = ledger.connection.execute(
        "SELECT canonical_digest, state FROM generations WHERE generation_id = ?",
        (baseline.generation_id,),
    ).fetchone()
    if row is not None:
        if row["canonical_digest"] != manifest_digest or row["state"] != "stable":
            _fail()
        return
    attempt_id = ledger.create_attempt("baseline", "initial")
    with ledger.transaction() as connection:
        connection.execute(
            """
            INSERT INTO generations(
                generation_id, attempt_id, canonical_digest, state, created_at
            ) VALUES (?, ?, ?, 'stable', ?)
            """,
            (
                baseline.generation_id,
                attempt_id,
                manifest_digest,
                datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            ),
        )


def initialize_baseline_pointers(
    ledger: EvolutionLedger, store: GenerationStore, baseline: PublishedGeneration
) -> tuple[PointerDocument, PointerDocument]:
    """Create exactly one baseline designation and its verified pointer views."""

    try:
        verified = store.verify(baseline.generation_id)
        if verified.manifest != baseline.manifest:
            _fail()
        _ensure_baseline_generation(ledger, verified)
        profile_id = _active_profile()
        event = _baseline_event(ledger, verified, profile_id)
        if event is None:
            timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            event = ledger.append_event(
                LifecycleEvent(
                    event_id=str(uuid.uuid4()),
                    attempt_id=None,
                    generation_id=verified.generation_id,
                    event_type="baseline_designated",
                    prior_state=None,
                    next_state=None,
                    actor="system",
                    input_digests=(_manifest_digest(verified), _profile_digest(profile_id)),
                    authorization_id=None,
                    reason_code="baseline",
                    reason_summary="baseline designation",
                    created_at=timestamp,
                )
            )
        pointer = _new_document(event, verified, profile_id)
        root = store.root.parent
        active_path = root / "active.json"
        lkg_path = root / "last-known-good.json"
        existing_active = _read_pointer(active_path)
        existing_lkg = _read_pointer(lkg_path)
        active = pointer if existing_active is None else validate_pointer(existing_active, ledger, store)
        lkg = pointer if existing_lkg is None else validate_pointer(existing_lkg, ledger, store)
        if active.generation_id != verified.generation_id or lkg.generation_id != verified.generation_id:
            _fail()
        if existing_active is None:
            atomic_write_pointer(active_path, pointer)
        if existing_lkg is None:
            atomic_write_pointer(lkg_path, pointer)
        return active, lkg
    except (PointerError, ValueError, OSError):
        raise PointerError("baseline_pointer_initialization_failed") from None
