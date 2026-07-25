"""Immutable global organism identity dataclass, persistence, and validation."""

from __future__ import annotations

import json
import os
import re
import stat as stat_module
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .organism_home import ensure_organism_directories, secure_file_permissions

_UUID = re.compile(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\Z", re.ASCII)
_DIGEST = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z\Z", re.ASCII)


class OrganismIdentityError(Exception):
    """Raised when organism identity loading, creation, or validation fails."""


@dataclass(frozen=True)
class OrganismIdentity:
    schema_version: int
    organism_id: str
    created_at: str
    lineage_root_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "organism_id": self.organism_id,
            "created_at": self.created_at,
            "lineage_root_digest": self.lineage_root_digest,
        }

    def to_canonical_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def validate_organism_identity(identity: OrganismIdentity) -> None:
    if identity.schema_version != 1:
        raise OrganismIdentityError("Unsupported schema version")
    if not _UUID.fullmatch(identity.organism_id):
        raise OrganismIdentityError("Invalid organism_id format")
    if not _DIGEST.fullmatch(identity.lineage_root_digest):
        raise OrganismIdentityError("Invalid lineage_root_digest format")
    if not _TIMESTAMP.fullmatch(identity.created_at):
        raise OrganismIdentityError("Invalid created_at timestamp format")


def _identity_path_stat(root: Path) -> os.stat_result | None:
    """lstat identity.json; return None if missing, raise on symlink/unsafe."""
    identity_path = root / "identity.json"
    try:
        st = identity_path.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        raise OrganismIdentityError("organism_identity_unsafe") from None
    if stat_module.S_ISLNK(st.st_mode):
        raise OrganismIdentityError("organism_identity_unsafe") from None
    return st


def create_organism_identity(
    organism_root: Path | None = None,
    lineage_root_digest: str = "0000000000000000000000000000000000000000000000000000000000000000",
) -> OrganismIdentity:
    root = ensure_organism_directories(organism_root)
    identity_path = root / "identity.json"

    st = _identity_path_stat(root)
    if st is not None:
        # Identity file exists (already validated by _identity_path_stat).
        # Load it if valid; if malformed, let load_organism_identity raise.
        return load_organism_identity(root)

    # Atomic creation: write to temp, fsync, then rename
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    identity = OrganismIdentity(
        schema_version=1,
        organism_id=str(uuid.uuid4()),
        created_at=now,
        lineage_root_digest=lineage_root_digest,
    )
    validate_organism_identity(identity)

    tmp_name = f".identity.json.tmp.{os.getpid()}"
    tmp_path = root / tmp_name
    try:
        # O_EXCL | O_CREAT ensures exclusive creation
        fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # Another process is also creating — wait and retry
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)

    try:
        payload = identity.to_canonical_json().encode("utf-8")
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)

    # fsync parent directory before rename
    dir_fd = os.open(str(root), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)

    try:
        os.rename(str(tmp_path), str(identity_path))
    except OSError:
        # Another process won the race — clean up and load theirs
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return load_organism_identity(root)

    secure_file_permissions(identity_path)
    return identity


def load_organism_identity(organism_root: Path | None = None) -> OrganismIdentity:
    root = ensure_organism_directories(organism_root)
    identity_path = root / "identity.json"

    st = _identity_path_stat(root)
    if st is None:
        raise OrganismIdentityError("organism_identity_missing")
    if not stat_module.S_ISREG(st.st_mode):
        raise OrganismIdentityError("organism_identity_unsafe") from None

    try:
        data = json.loads(identity_path.read_text(encoding="utf-8"))
    except Exception:
        raise OrganismIdentityError("organism_identity_corrupted") from None

    identity = OrganismIdentity(
        schema_version=int(data.get("schema_version", 0)),
        organism_id=str(data.get("organism_id", "")),
        created_at=str(data.get("created_at", "")),
        lineage_root_digest=str(data.get("lineage_root_digest", "")),
    )
    validate_organism_identity(identity)
    return identity
