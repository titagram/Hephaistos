"""Immutable global organism identity dataclass, persistence, and validation."""

from __future__ import annotations

import json
import re
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
        raise OrganismIdentityError(f"Unsupported schema version: {identity.schema_version}")
    if not _UUID.fullmatch(identity.organism_id):
        raise OrganismIdentityError("Invalid organism_id format")
    if not _DIGEST.fullmatch(identity.lineage_root_digest):
        raise OrganismIdentityError("Invalid lineage_root_digest format")
    if not _TIMESTAMP.fullmatch(identity.created_at):
        raise OrganismIdentityError("Invalid created_at timestamp format")


def create_organism_identity(
    organism_root: Path | None = None,
    lineage_root_digest: str = "0000000000000000000000000000000000000000000000000000000000000000",
) -> OrganismIdentity:
    root = ensure_organism_directories(organism_root)
    identity_path = root / "identity.json"

    if identity_path.is_symlink():
        raise OrganismIdentityError("identity.json must not be a symlink")
    if identity_path.exists():
        return load_organism_identity(root)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    identity = OrganismIdentity(
        schema_version=1,
        organism_id=str(uuid.uuid4()),
        created_at=now,
        lineage_root_digest=lineage_root_digest,
    )
    validate_organism_identity(identity)

    identity_path.write_text(identity.to_canonical_json(), encoding="utf-8")
    secure_file_permissions(identity_path)
    return identity


def load_organism_identity(organism_root: Path | None = None) -> OrganismIdentity:
    root = ensure_organism_directories(organism_root)
    identity_path = root / "identity.json"

    if identity_path.is_symlink():
        raise OrganismIdentityError("identity.json must not be a symlink")
    if not identity_path.exists():
        raise OrganismIdentityError("organism identity.json missing")

    try:
        data = json.loads(identity_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise OrganismIdentityError(f"Failed to parse identity.json: {e}") from e

    identity = OrganismIdentity(
        schema_version=int(data.get("schema_version", 0)),
        organism_id=str(data.get("organism_id", "")),
        created_at=str(data.get("created_at", "")),
        lineage_root_digest=str(data.get("lineage_root_digest", "")),
    )
    validate_organism_identity(identity)
    return identity
