"""Canonical serialization and hashing for Hades artifact payloads."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_artifact_bytes(artifact_payload: Any) -> bytes:
    """Serialize an artifact exactly as the Hades backend hash contract requires."""
    return json.dumps(
        artifact_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def artifact_payload_hash(artifact_payload: Any) -> str:
    """Return the canonical SHA-256 digest for a Hades artifact payload."""
    return hashlib.sha256(canonical_artifact_bytes(artifact_payload)).hexdigest()
