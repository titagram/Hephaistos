"""Global Gnothi Seauton structural organism store."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .organism_home import ensure_organism_directories, secure_file_permissions


@dataclass(frozen=True)
class OrganismCapability:
    capability_key: str
    description: str
    verified: bool
    source: str


class GlobalGnothiStore:
    def __init__(self, organism_root: Path | None = None) -> None:
        self.organism_root = ensure_organism_directories(organism_root)
        self.gnothi_dir = self.organism_root / "gnothi_seauton"
        self.current_file = self.gnothi_dir / "current.json"
        self.revisions_dir = self.gnothi_dir / "revisions"

    def _load_current(self) -> dict[str, Any]:
        if not self.current_file.exists():
            return {
                "schema_version": 1,
                "organism_capabilities": [],
                "profile_facets_count": 0,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        try:
            return json.loads(self.current_file.read_text(encoding="utf-8"))
        except Exception:
            return {"schema_version": 1, "organism_capabilities": [], "profile_facets_count": 0}

    def _save_current(self, data: dict[str, Any]) -> str:
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        content = json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False)
        self.current_file.write_text(content, encoding="utf-8")
        secure_file_permissions(self.current_file)

        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        rev_file = self.revisions_dir / f"{digest}.json"
        rev_file.write_text(content, encoding="utf-8")
        secure_file_permissions(rev_file)
        return digest

    def get_capabilities(self) -> list[OrganismCapability]:
        data = self._load_current()
        caps = []
        for d in data.get("organism_capabilities", []):
            caps.append(
                OrganismCapability(
                    capability_key=str(d.get("capability_key")),
                    description=str(d.get("description", "")),
                    verified=bool(d.get("verified", False)),
                    source=str(d.get("source", "local")),
                )
            )
        return caps

    def is_capability_verified(self, capability_key: str) -> bool:
        caps = self.get_capabilities()
        for c in caps:
            if c.capability_key == capability_key and c.verified:
                return True
        return False

    def register_capability(self, capability_key: str, description: str, verified: bool = True) -> str:
        data = self._load_current()
        caps = data.get("organism_capabilities", [])
        updated = False
        for c in caps:
            if c.get("capability_key") == capability_key:
                c["description"] = description
                c["verified"] = verified
                updated = True
                break
        if not updated:
            caps.append({
                "capability_key": capability_key,
                "description": description,
                "verified": verified,
                "source": "verified_local",
            })
        data["organism_capabilities"] = caps
        return self._save_current(data)
