"""Global organism directory layout resolver and permission enforcer."""

from __future__ import annotations

import os
from pathlib import Path
from hermes_constants import get_organism_home, get_default_hermes_root


def ensure_organism_directories(root: Path | None = None) -> Path:
    """Ensure all global organism directory trees exist with 0700 permissions."""
    organism_root = (root or get_organism_home()).resolve()
    organism_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(organism_root, 0o700)
    except OSError:
        pass

    subdirs = [
        "evolution",
        "evolution/generations",
        "telos",
        "telos/revisions",
        "telos/drafts",
        "gnothi_seauton",
        "gnothi_seauton/revisions",
        "evidence-brokers",
        "archives",
        "archives/legacy-profile-state",
        "wiki",
    ]

    for sub in subdirs:
        d = organism_root / sub
        d.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass

    return organism_root


def secure_file_permissions(path: Path) -> None:
    """Chmod file 0600 if it exists."""
    if path.exists():
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
