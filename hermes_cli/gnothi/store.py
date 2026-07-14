from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hermes_constants import get_hermes_home
from hermes_cli.gnothi.contract import validate_artifact
from utils import atomic_replace

POINTER_SCHEMA = "hades.gnothi_pointer.v1"
_SAFE_REVISION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def _encoded_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class OrganismRevisionStore:
    """Immutable local organism revisions with an atomic current pointer."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root if root is not None else get_hermes_home() / "gnothi_seauton"
        self.revisions_dir = self.root / "revisions"
        self.current_path = self.root / "current.json"

    @staticmethod
    def _validate_revision_id(revision_id: object) -> str:
        value = str(revision_id or "")
        if not _SAFE_REVISION_ID.fullmatch(value):
            raise ValueError(f"unsafe revision id: {value!r}")
        return value

    @staticmethod
    def _write_atomic(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary_path, 0o600)
            written_path = Path(atomic_replace(temporary_path, path))
            try:
                os.chmod(written_path, 0o600)
            except OSError:
                pass
        finally:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"expected JSON object in {path}")
        return value

    def _read_pointer(self) -> dict[str, Any] | None:
        if not self.current_path.is_file():
            return None
        pointer = self._load_json(self.current_path)
        if pointer.get("schema") != POINTER_SCHEMA:
            raise ValueError("invalid organism current pointer")
        return pointer

    def publish(
        self,
        artifact: dict[str, Any],
        *,
        published_at: str | None = None,
    ) -> dict[str, Any]:
        errors = validate_artifact(artifact)
        if errors:
            raise ValueError(f"invalid organism artifact: {', '.join(errors)}")

        contract = artifact.get("organism_contract", {})
        revision_id = self._validate_revision_id(contract.get("revision_id"))
        content = _encoded_json(artifact)
        digest = _sha256(content)
        revision_path = self.revisions_dir / f"{revision_id}.json"

        if revision_path.exists():
            if revision_path.read_bytes() != content:
                raise ValueError(f"conflicting revision: {revision_id}")
            current_pointer = self._read_pointer()
            if current_pointer and (
                current_pointer.get("revision_id") == revision_id
                and current_pointer.get("sha256") == digest
            ):
                return current_pointer
        else:
            self._write_atomic(revision_path, content)

        pointer = {
            "schema": POINTER_SCHEMA,
            "revision_id": revision_id,
            "sha256": digest,
            "published_at": published_at or _utc_now(),
        }
        self._write_atomic(self.current_path, _encoded_json(pointer))
        return pointer

    def get(self, revision_id: str) -> dict[str, Any] | None:
        safe_revision_id = self._validate_revision_id(revision_id)
        path = self.revisions_dir / f"{safe_revision_id}.json"
        if not path.is_file():
            return None
        return self._load_json(path)

    def current(self) -> dict[str, Any] | None:
        pointer = self._read_pointer()
        if pointer is None:
            return None
        revision_id = self._validate_revision_id(pointer.get("revision_id"))
        path = self.revisions_dir / f"{revision_id}.json"
        if not path.is_file():
            raise ValueError(f"missing current organism revision: {revision_id}")
        content = path.read_bytes()
        if _sha256(content) != pointer.get("sha256"):
            raise ValueError(f"current organism revision digest mismatch: {revision_id}")
        value = json.loads(content)
        if not isinstance(value, dict):
            raise ValueError(f"expected JSON object in {path}")
        return value

    def list_revisions(self) -> list[dict[str, Any]]:
        if not self.revisions_dir.is_dir():
            return []
        revisions = [
            self._load_json(path)
            for path in self.revisions_dir.glob("*.json")
            if path.is_file()
        ]

        def sort_key(artifact: dict[str, Any]) -> tuple[str, str]:
            contract = artifact.get("organism_contract", {})
            return (
                str(contract.get("collected_at") or ""),
                str(contract.get("revision_id") or ""),
            )

        return sorted(revisions, key=sort_key, reverse=True)

    def previous_healthy(self) -> dict[str, Any] | None:
        pointer = self._read_pointer()
        current_id = str(pointer.get("revision_id") or "") if pointer else ""
        for artifact in self.list_revisions():
            contract = artifact.get("organism_contract", {})
            if str(contract.get("revision_id") or "") == current_id:
                continue
            if contract.get("status") in {"current", "stale"}:
                return artifact
        return None
